#!/usr/bin/env python3
"""Guarded manual fix: reverse one done stock.item.journal into a new approved journal."""

from __future__ import annotations

import argparse
import asyncio
from datetime import date
import json
from pathlib import Path
from typing import Any

from common import (
    add_manual_common_args,
    batch_write,
    chunks,
    open_manual_connection,
    read_company_lock_blockers,
    write_manual_artifact,
)
from smartscc_tools.features.item_journal.utils import normalize_text, utc_string_from_local_date
from smartscc_tools.services.odoo.gateway import build_company_context


def _default_output_dir() -> str:
    home = Path.home()
    candidates = [
        home / "OneDrive" / "Desktop" / "Perbaikan Odoo",
        home / "Desktop" / "Perbaikan Odoo",
    ]
    for path in candidates:
        if path.parent.exists():
            return str(path)
    return str(candidates[0])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a reversal stock.item.journal from one done source journal."
    )
    parser.add_argument("--item-journal-id", type=int, required=True, help="Source stock.item.journal id.")
    parser.add_argument("--company-id", type=int, help="Expected company id.")
    parser.add_argument("--target-local-date", required=True, help="Target local date YYYY-MM-DD.")
    parser.add_argument(
        "--qty-factor",
        type=float,
        default=1.0,
        help="Multiply source line qty for reversal. Use 2 to rollback two duplicate executions. Default: 1.",
    )
    parser.add_argument(
        "--resume-draft-id",
        type=int,
        help="Existing draft reversal stock.item.journal id to continue instead of creating a new one.",
    )
    parser.add_argument(
        "--fallback-analytic-account-id",
        type=int,
        help="Fill blank reversal line analytic_distribution with this analytic account at 100%% before submit.",
    )
    parser.add_argument(
        "--line-name-prefix",
        default="Reverse SIJ",
        help="Prefix for reversal line descriptions. Default: 'Reverse SIJ'.",
    )
    add_manual_common_args(parser)
    parser.set_defaults(output_dir=_default_output_dir())
    return parser.parse_args()


async def _read_item_journal(client: Any, item_journal_id: int, context: dict[str, Any]) -> dict[str, Any]:
    rows = await client.read(
        "stock.item.journal",
        [int(item_journal_id)],
        fields=[
            "id",
            "name",
            "state",
            "company_id",
            "warehouse_id",
            "location_id",
            "inventory_date",
            "accounting_date",
            "memo",
            "total_amount",
            "line_ids",
            "move_ids",
            "approved_by",
            "approved_date",
        ],
        context=context,
        stage="MANUAL_READ_STOCK_ITEM_JOURNAL",
    )
    if not rows:
        raise RuntimeError(f"stock.item.journal id={item_journal_id} tidak ditemukan.")
    return rows[0]


async def _read_item_journal_lines(client: Any, line_ids: list[int], context: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    fields = [
        "id",
        "item_journal_id",
        "product_id",
        "purpose_id",
        "qty",
        "type",
        "name",
        "cost",
        "total",
        "uom_id",
        "cost_method",
        "analytic_distribution",
    ]
    for batch in chunks(line_ids, 200):
        rows.extend(
            await client.read(
                "stock.item.journal.line",
                batch,
                fields=fields,
                context=context,
                stage="MANUAL_READ_STOCK_ITEM_JOURNAL_LINES",
            )
        )
    return sorted(rows, key=lambda row: int(row.get("id") or 0))


async def _read_move_rows(client: Any, move_ids: list[int], context: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    fields = [
        "id",
        "item_journal_id",
        "product_id",
        "product_uom_qty",
        "state",
        "date",
        "location_id",
        "location_dest_id",
        "account_move_ids",
        "reference",
    ]
    for batch in chunks(move_ids, 200):
        rows.extend(
            await client.read(
                "stock.move",
                batch,
                fields=fields,
                context=context,
                stage="MANUAL_READ_ITEM_JOURNAL_MOVES",
            )
        )
    return sorted(rows, key=lambda row: int(row.get("id") or 0))


async def _read_journal_rows(client: Any, journal_ids: list[int], context: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for batch in chunks(journal_ids, 200):
        rows.extend(
            await client.read(
                "account.move",
                batch,
                fields=["id", "name", "state", "date", "ref", "company_id"],
                context=context,
                stage="MANUAL_READ_ITEM_JOURNAL_STJ",
            )
        )
    return sorted(rows, key=lambda row: int(row.get("id") or 0))


def _safe_int_list(value: Any) -> list[int]:
    out: list[int] = []
    for item in value or []:
        try:
            num = int(item)
        except (TypeError, ValueError):
            continue
        if num > 0 and num not in out:
            out.append(num)
    return out


def _swap_type(value: str) -> str:
    clean = normalize_text(value).lower()
    if clean == "increase":
        return "decrease"
    if clean == "decrease":
        return "increase"
    raise RuntimeError(f"Tipe line tidak dikenal: {value}")


def _build_reversal_line_name(prefix: str, source_journal_id: int, original_name: str) -> str:
    clean_prefix = normalize_text(prefix) or "Reverse SIJ"
    clean_name = normalize_text(original_name)
    if clean_name:
        return f"{clean_prefix} {source_journal_id} - {clean_name}"
    return f"{clean_prefix} {source_journal_id}"


def _journal_date_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        key = normalize_text(row.get("date")) or "(blank)"
        counts[key] = counts.get(key, 0) + 1
    return counts


def _journal_prefix_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        name = normalize_text(row.get("name"))
        key = name.split("/", 1)[0] if "/" in name else (name or "(blank)")
        counts[key] = counts.get(key, 0) + 1
    return counts


def _fallback_analytic_distribution(args: argparse.Namespace) -> dict[str, float] | bool:
    analytic_account_id = int(args.fallback_analytic_account_id or 0)
    if analytic_account_id <= 0:
        return False
    return {str(analytic_account_id): 100.0}


async def _read_recent_company_analytic_account_counts(
    client: Any,
    company_id: int,
    context: dict[str, Any],
) -> dict[str, int]:
    rows = await client.search_read(
        "account.move.line",
        [["company_id", "=", int(company_id)], ["analytic_distribution", "!=", False]],
        fields=["analytic_distribution"],
        limit=200,
        order="id desc",
        context=context,
        stage="MANUAL_READ_COMPANY_ANALYTIC_USAGE",
    )
    counts: dict[str, int] = {}
    for row in rows:
        analytic_distribution = row.get("analytic_distribution") or {}
        if not isinstance(analytic_distribution, dict):
            continue
        for analytic_account_id in analytic_distribution:
            key = str(analytic_account_id).strip()
            if key:
                counts[key] = counts.get(key, 0) + 1
    return counts


async def _read_resume_draft(
    client: Any,
    draft_id: int,
    context: dict[str, Any],
) -> dict[str, Any]:
    draft = await _read_item_journal(client, int(draft_id), context)
    if normalize_text(draft.get("state")).lower() != "draft":
        raise RuntimeError(f"Resume draft id={draft_id} harus state draft. Actual={draft.get('state')!r}")
    return draft


async def _build_precheck(client: Any, *, args: argparse.Namespace) -> dict[str, Any]:
    base_context = build_company_context(int(args.company_id or 0)) if int(args.company_id or 0) > 0 else {}
    source = await _read_item_journal(client, int(args.item_journal_id), base_context)
    company_id = int((source.get("company_id") or [0])[0] or 0)
    context = build_company_context(company_id)
    source = await _read_item_journal(client, int(args.item_journal_id), context)
    resume_draft = (
        await _read_resume_draft(client, int(args.resume_draft_id), context)
        if int(args.resume_draft_id or 0) > 0
        else None
    )

    line_ids = _safe_int_list(source.get("line_ids"))
    lines = await _read_item_journal_lines(client, line_ids, context)
    move_ids = _safe_int_list(source.get("move_ids"))
    moves = await _read_move_rows(client, move_ids, context)
    journal_ids = sorted({jid for row in moves for jid in _safe_int_list(row.get("account_move_ids"))})
    journals = await _read_journal_rows(client, journal_ids, context)
    analytic_account_usage = await _read_recent_company_analytic_account_counts(client, company_id, context)

    target_local_date = date.fromisoformat(args.target_local_date).strftime("%Y-%m-%d")
    blockers = await read_company_lock_blockers(client, company_id, target_local_date)

    qty_factor = float(args.qty_factor or 1.0)
    reverse_total = round(sum(abs(float(row.get("total") or 0.0)) * qty_factor for row in lines), 2)
    source_total = round(float(source.get("total_amount") or 0.0), 2)
    negative_count = sum(1 for row in lines if normalize_text(row.get("type")).lower() == "decrease")
    positive_count = sum(1 for row in lines if normalize_text(row.get("type")).lower() == "increase")

    status = "ready"
    errors: list[str] = []
    if int(args.company_id or 0) > 0 and company_id != int(args.company_id):
        status = "blocked"
        errors.append(f"Company mismatch. Expected {int(args.company_id)}, actual {company_id}.")
    if normalize_text(source.get("state")).lower() != "done":
        status = "blocked"
        errors.append(f"Source stock.item.journal harus done. Actual={normalize_text(source.get('state')) or '-'}")
    if blockers:
        status = "blocked"
        errors.append(f"Target date diblokir lock date: {blockers}")
    if not lines:
        status = "blocked"
        errors.append("Source tidak punya line_ids.")
    if resume_draft is not None:
        resume_company_id = int((resume_draft.get("company_id") or [0])[0] or 0)
        if resume_company_id != company_id:
            status = "blocked"
            errors.append(
                f"Resume draft company mismatch. Resume draft company={resume_company_id}, source company={company_id}."
            )

    return {
        "mode": "dry_run",
        "database": "",
        "source_item_journal": {
            "id": int(source["id"]),
            "name": normalize_text(source.get("name")),
            "state": normalize_text(source.get("state")),
            "company_id": source.get("company_id"),
            "warehouse_id": source.get("warehouse_id"),
            "location_id": source.get("location_id"),
            "inventory_date": source.get("inventory_date"),
            "accounting_date": source.get("accounting_date"),
            "approved_by": source.get("approved_by"),
            "approved_date": source.get("approved_date"),
            "total_amount": source.get("total_amount"),
            "memo": source.get("memo"),
            "line_count": len(lines),
            "move_count": len(moves),
            "stj_count": len(journals),
            "stj_date_counts": _journal_date_counts(journals),
            "stj_prefix_counts": _journal_prefix_counts(journals),
        },
        "reverse_plan": {
            "target_local_date": target_local_date,
            "target_inventory_datetime_utc": utc_string_from_local_date(
                date.fromisoformat(target_local_date),
                7,
                False,
            ),
            "qty_factor": qty_factor,
            "planned_line_count": len(lines),
            "planned_decrease_count": positive_count,
            "planned_increase_count": negative_count,
            "expected_reverse_total_abs": reverse_total,
            "source_total_amount": source_total,
        },
        "resume_draft": resume_draft,
        "fallback_analytic_distribution": _fallback_analytic_distribution(args),
        "recent_company_analytic_account_usage": analytic_account_usage,
        "status": status,
        "errors": errors,
        "lock_blockers": blockers,
        "line_sample_first_10": lines[:10],
        "stj_sample_first_10": journals[:10],
    }


def _build_create_values(precheck: dict[str, Any], source: dict[str, Any], lines: list[dict[str, Any]], args: argparse.Namespace) -> dict[str, Any]:
    target_local_date = precheck["reverse_plan"]["target_local_date"]
    inventory_date_utc = precheck["reverse_plan"]["target_inventory_datetime_utc"]
    qty_factor = float(precheck["reverse_plan"].get("qty_factor") or 1.0)
    fallback_analytic_distribution = _fallback_analytic_distribution(args)
    commands: list[list[Any]] = []
    for line in lines:
        values: dict[str, Any] = {
            "purpose_id": int((line.get("purpose_id") or [0])[0] or 0),
            "product_id": int((line.get("product_id") or [0])[0] or 0),
            "name": _build_reversal_line_name(
                normalize_text(args.line_name_prefix),
                int(source["id"]),
                normalize_text(line.get("name")),
            ),
            "qty": float(line.get("qty") or 0.0) * qty_factor,
            "type": _swap_type(normalize_text(line.get("type"))),
        }
        analytic_distribution = line.get("analytic_distribution")
        if analytic_distribution in (None, False, "", {}) and fallback_analytic_distribution:
            analytic_distribution = fallback_analytic_distribution
        if analytic_distribution not in (None, False, "", {}):
            values["analytic_distribution"] = analytic_distribution
        cost_value = line.get("cost")
        if cost_value not in (None, False, ""):
            values["cost"] = float(cost_value or 0.0)
        commands.append([0, 0, values])

    memo = normalize_text(source.get("memo"))
    memo_prefix = f"Safe reversal of stock.item.journal {int(source['id'])}"
    if abs(qty_factor - 1.0) > 1e-9:
        memo_prefix = f"{memo_prefix} (qty_factor={qty_factor:g})"
    if memo:
        memo = f"{memo_prefix} | Source memo: {memo}"
    else:
        memo = memo_prefix

    return {
        "company_id": int((source.get("company_id") or [0])[0] or 0),
        "warehouse_id": int((source.get("warehouse_id") or [0])[0] or 0),
        "location_id": int((source.get("location_id") or [0])[0] or 0),
        "inventory_date": inventory_date_utc,
        "accounting_date": target_local_date,
        "memo": memo,
        "line_ids": commands,
    }


async def _fill_blank_line_analytic_distribution(
    client: Any,
    reversal_id: int,
    context: dict[str, Any],
    fallback_analytic_distribution: dict[str, float] | bool,
) -> dict[str, Any]:
    line_ids = _safe_int_list((await _read_item_journal(client, int(reversal_id), context)).get("line_ids"))
    if not line_ids or not fallback_analytic_distribution:
        return {
            "fallback_analytic_distribution": fallback_analytic_distribution or False,
            "blank_line_count_before": 0,
            "written_line_count": 0,
            "sample_after_fill": [],
        }
    lines = await _read_item_journal_lines(client, line_ids, context)
    blank_line_ids = [
        int(row.get("id") or 0)
        for row in lines
        if int(row.get("id") or 0) > 0 and row.get("analytic_distribution") in (None, False, "", {})
    ]
    if blank_line_ids:
        await batch_write(
            client,
            "stock.item.journal.line",
            blank_line_ids,
            {"analytic_distribution": fallback_analytic_distribution},
            context,
        )
    sample_after_fill = await client.read(
        "stock.item.journal.line",
        line_ids[:5],
        fields=["id", "analytic_distribution"],
        context=context,
        stage="MANUAL_READ_REVERSAL_LINE_ANALYTIC_SAMPLE",
    )
    return {
        "fallback_analytic_distribution": fallback_analytic_distribution,
        "blank_line_count_before": len(blank_line_ids),
        "written_line_count": len(blank_line_ids),
        "sample_after_fill": sample_after_fill,
    }


async def _submit_and_approve(client: Any, reversal_id: int, context: dict[str, Any]) -> None:
    await client.execute_kw(
        "stock.item.journal",
        "action_submit",
        args=[[int(reversal_id)]],
        kwargs={"context": context},
        stage="MANUAL_STOCK_ITEM_JOURNAL_SUBMIT",
        mutating=True,
    )
    await client.execute_kw(
        "stock.item.journal",
        "action_approve",
        args=[[int(reversal_id)]],
        kwargs={"context": context},
        stage="MANUAL_STOCK_ITEM_JOURNAL_APPROVE",
        mutating=True,
    )


async def amain() -> int:
    args = parse_args()
    try:
        date.fromisoformat(args.target_local_date)
    except ValueError as exc:
        raise SystemExit("--target-local-date must be YYYY-MM-DD") from exc
    if float(args.qty_factor or 0.0) <= 0.0:
        raise SystemExit("--qty-factor must be > 0")

    conn = await open_manual_connection(args)
    try:
        precheck = await _build_precheck(conn.client, args=args)
        precheck["database"] = conn.config.database
        if not args.apply:
            print(json.dumps(precheck, ensure_ascii=False, indent=2, default=str))
            path = write_manual_artifact(precheck, args, f"reverse_stock_item_journal_{args.item_journal_id}_dry_run")
            if path:
                print(f"Artefact: {path}")
            return 0

        if normalize_text(precheck.get("status")) != "ready":
            raise RuntimeError(f"Precheck blocked: {precheck.get('errors')}")

        company_id = int((precheck["source_item_journal"]["company_id"] or [0])[0] or 0)
        context = build_company_context(company_id)
        source = await _read_item_journal(conn.client, int(args.item_journal_id), context)
        lines = await _read_item_journal_lines(conn.client, _safe_int_list(source.get("line_ids")), context)
        if int(args.resume_draft_id or 0) > 0:
            reversal_id = int(args.resume_draft_id)
        else:
            values = _build_create_values(precheck, source, lines, args)
            reversal_id = await conn.client.create(
                "stock.item.journal",
                values,
                context=context,
                stage="MANUAL_CREATE_STOCK_ITEM_JOURNAL_REVERSAL",
            )
            if reversal_id <= 0:
                raise RuntimeError("Create reversal stock.item.journal gagal.")

        analytic_fill = await _fill_blank_line_analytic_distribution(
            conn.client,
            reversal_id,
            context,
            _fallback_analytic_distribution(args),
        )
        before_submit = await _read_item_journal(conn.client, reversal_id, context)
        await _submit_and_approve(conn.client, reversal_id, context)
        after_done = await _read_item_journal(conn.client, reversal_id, context)
        reverse_moves = await _read_move_rows(conn.client, _safe_int_list(after_done.get("move_ids")), context)
        reverse_journal_ids = sorted({jid for row in reverse_moves for jid in _safe_int_list(row.get("account_move_ids"))})
        reverse_journals = await _read_journal_rows(conn.client, reverse_journal_ids, context)

        payload = {
            "mode": "apply",
            "database": conn.config.database,
            "source_item_journal": {
                "id": int(source["id"]),
                "name": normalize_text(source.get("name")),
                "state": normalize_text(source.get("state")),
                "company_id": source.get("company_id"),
                "inventory_date": source.get("inventory_date"),
                "accounting_date": source.get("accounting_date"),
                "total_amount": source.get("total_amount"),
                "line_count": len(lines),
                "move_count": len(_safe_int_list(source.get("move_ids"))),
            },
            "reversal_prepare": analytic_fill,
            "reversal_before_submit": before_submit,
            "reversal_after_approve": after_done,
            "reversal_stj_count": len(reverse_journals),
            "reversal_stj_date_counts": _journal_date_counts(reverse_journals),
            "reversal_stj_prefix_counts": _journal_prefix_counts(reverse_journals),
            "reversal_stj_sample_first_10": reverse_journals[:10],
            "status": "done" if normalize_text(after_done.get("state")).lower() == "done" else "unexpected_state",
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        path = write_manual_artifact(payload, args, f"reverse_stock_item_journal_{args.item_journal_id}_apply")
        if path:
            print(f"Artefact: {path}")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(amain()))
