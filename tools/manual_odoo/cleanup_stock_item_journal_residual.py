#!/usr/bin/env python3
"""Guarded manual fix: clean rounding residual after multiple stock.item.journal rollbacks."""

from __future__ import annotations

import argparse
import asyncio
from datetime import date
import json
from typing import Any

from common import add_manual_common_args, chunks, open_manual_connection, read_company_lock_blockers, write_manual_artifact
from reverse_stock_item_journal import (
    _default_output_dir,
    _fallback_analytic_distribution,
    _fill_blank_line_analytic_distribution,
    _journal_date_counts,
    _journal_prefix_counts,
    _read_item_journal,
    _read_item_journal_lines,
    _read_journal_rows,
    _safe_int_list,
    _submit_and_approve,
)
from smartscc_tools.features.item_journal.utils import normalize_text, utc_string_from_local_date
from smartscc_tools.services.odoo.gateway import build_company_context


def _parse_csv_ints(raw: str | None) -> list[int]:
    out: list[int] = []
    for chunk in str(raw or "").split(","):
        text = chunk.strip()
        if not text:
            continue
        try:
            value = int(text)
        except ValueError as exc:
            raise SystemExit(f"Invalid integer id in list: {text!r}") from exc
        if value > 0 and value not in out:
            out.append(value)
    return out


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create one small cleanup stock.item.journal to close residual SVL rounding gaps."
    )
    parser.add_argument("--source-item-journal-id", type=int, required=True, help="Primary source stock.item.journal id.")
    parser.add_argument(
        "--scope-item-journal-ids",
        required=True,
        help="Comma-separated stock.item.journal ids to net together, e.g. 5946,6141,6142.",
    )
    parser.add_argument("--company-id", type=int, help="Expected company id.")
    parser.add_argument("--target-local-date", required=True, help="Target local date YYYY-MM-DD.")
    parser.add_argument(
        "--fallback-analytic-account-id",
        type=int,
        help="Fill blank cleanup line analytic_distribution with this analytic account at 100%% before submit.",
    )
    parser.add_argument(
        "--line-name-prefix",
        default="Residual cleanup SIJ",
        help="Prefix for cleanup line descriptions.",
    )
    add_manual_common_args(parser)
    parser.set_defaults(output_dir=_default_output_dir())
    return parser.parse_args()


async def _read_scope_journals(
    client: Any,
    scope_ids: list[int],
    context: dict[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    fields = [
        "id",
        "name",
        "state",
        "company_id",
        "warehouse_id",
        "location_id",
        "inventory_date",
        "accounting_date",
        "memo",
        "move_ids",
    ]
    for batch in chunks(scope_ids, 200):
        rows.extend(
            await client.read(
                "stock.item.journal",
                batch,
                fields=fields,
                context=context,
                stage="MANUAL_READ_SCOPE_STOCK_ITEM_JOURNAL",
            )
        )
    return sorted(rows, key=lambda row: int(row.get("id") or 0))


async def _read_scope_moves(
    client: Any,
    scope_ids: list[int],
    context: dict[str, Any],
) -> list[dict[str, Any]]:
    rows = await client.search_read(
        "stock.move",
        [["item_journal_id", "in", scope_ids]],
        fields=["id", "item_journal_id", "product_id", "product_uom_qty", "account_move_ids"],
        limit=10000,
        order="id asc",
        context=context,
        stage="MANUAL_READ_SCOPE_STOCK_MOVES",
    )
    return sorted(rows, key=lambda row: int(row.get("id") or 0))


async def _read_scope_svls(
    client: Any,
    move_ids: list[int],
    context: dict[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for batch in chunks(move_ids, 200):
        rows.extend(
            await client.search_read(
                "stock.valuation.layer",
                [["stock_move_id", "in", batch]],
                fields=["id", "stock_move_id", "product_id", "quantity", "value", "account_move_id"],
                limit=10000,
                order="id asc",
                context=context,
                stage="MANUAL_READ_SCOPE_SVLS",
            )
        )
    return sorted(rows, key=lambda row: int(row.get("id") or 0))


def _round_qty(value: Any) -> float:
    return round(float(value or 0.0), 6)


def _round_value(value: Any) -> float:
    return round(float(value or 0.0), 2)


def _build_cleanup_lines(
    source_lines: list[dict[str, Any]],
    scope_svls: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, float], list[str]]:
    source_by_product: dict[int, dict[str, Any]] = {}
    for row in source_lines:
        product_id = int((row.get("product_id") or [0])[0] or 0)
        if product_id > 0 and product_id not in source_by_product:
            source_by_product[product_id] = row

    residual_by_product: dict[int, dict[str, float | list[Any]]] = {}
    for row in scope_svls:
        product = row.get("product_id") or [0, ""]
        product_id = int(product[0] or 0) if isinstance(product, (list, tuple)) and product else 0
        if product_id <= 0:
            continue
        bucket = residual_by_product.setdefault(
            product_id,
            {"qty": 0.0, "value": 0.0, "product_id": product},
        )
        bucket["qty"] = float(bucket["qty"]) + float(row.get("quantity") or 0.0)
        bucket["value"] = float(bucket["value"]) + float(row.get("value") or 0.0)

    cleanup_lines: list[dict[str, Any]] = []
    errors: list[str] = []
    net_qty_sum = 0.0
    net_value_sum = 0.0
    for product_id in sorted(residual_by_product):
        bucket = residual_by_product[product_id]
        qty = _round_qty(bucket["qty"])
        value = _round_value(bucket["value"])
        if abs(qty) < 0.005 and abs(value) < 1.0:
            continue
        source_line = source_by_product.get(product_id)
        if not source_line:
            errors.append(f"Source line untuk product_id={product_id} tidak ditemukan.")
            continue
        cleanup_type = "increase" if qty < 0 else "decrease"
        cleanup_qty = _round_qty(abs(qty))
        if cleanup_qty <= 0.0:
            errors.append(f"Residual qty product_id={product_id} tidak valid: qty={qty}, value={value}.")
            continue
        cost_value = source_line.get("cost")
        if cost_value in (None, False, ""):
            cost_value = abs(value / cleanup_qty)
        cleanup_lines.append(
            {
                "product_id": source_line.get("product_id"),
                "purpose_id": source_line.get("purpose_id"),
                "uom_id": source_line.get("uom_id"),
                "name": source_line.get("name"),
                "analytic_distribution": source_line.get("analytic_distribution"),
                "qty": cleanup_qty,
                "type": cleanup_type,
                "cost": float(cost_value or 0.0),
                "net_qty_before": qty,
                "net_value_before": value,
            }
        )
        net_qty_sum += qty
        net_value_sum += value
    summary = {
        "net_qty_sum": _round_qty(net_qty_sum),
        "net_value_sum": _round_value(net_value_sum),
    }
    return cleanup_lines, summary, errors


def _build_create_values(
    *,
    source: dict[str, Any],
    cleanup_lines: list[dict[str, Any]],
    target_local_date: str,
    line_name_prefix: str,
) -> dict[str, Any]:
    inventory_date_utc = utc_string_from_local_date(date.fromisoformat(target_local_date), 7, False)
    commands: list[list[Any]] = []
    clean_prefix = normalize_text(line_name_prefix) or "Residual cleanup SIJ"
    for line in cleanup_lines:
        original_name = normalize_text(line.get("name"))
        line_label = f"{clean_prefix} {int(source['id'])}"
        if original_name:
            line_label = f"{line_label} - {original_name}"
        values: dict[str, Any] = {
            "purpose_id": int((line.get("purpose_id") or [0])[0] or 0),
            "product_id": int((line.get("product_id") or [0])[0] or 0),
            "name": line_label,
            "qty": float(line.get("qty") or 0.0),
            "type": normalize_text(line.get("type")),
            "cost": float(line.get("cost") or 0.0),
        }
        analytic_distribution = line.get("analytic_distribution")
        if analytic_distribution not in (None, False, "", {}):
            values["analytic_distribution"] = analytic_distribution
        commands.append([0, 0, values])

    scope_hint = normalize_text(source.get("name")) or f"id={int(source['id'])}"
    memo = f"Residual cleanup after rollback of stock.item.journal {int(source['id'])} | Source {scope_hint}"
    return {
        "company_id": int((source.get("company_id") or [0])[0] or 0),
        "warehouse_id": int((source.get("warehouse_id") or [0])[0] or 0),
        "location_id": int((source.get("location_id") or [0])[0] or 0),
        "inventory_date": inventory_date_utc,
        "accounting_date": target_local_date,
        "memo": memo,
        "line_ids": commands,
    }


async def _build_precheck(client: Any, args: argparse.Namespace) -> dict[str, Any]:
    scope_ids = _parse_csv_ints(args.scope_item_journal_ids)
    if int(args.source_item_journal_id) not in scope_ids:
        scope_ids.append(int(args.source_item_journal_id))
    scope_ids = sorted(set(scope_ids))
    context = build_company_context(int(args.company_id or 0)) if int(args.company_id or 0) > 0 else {}
    source = await _read_item_journal(client, int(args.source_item_journal_id), context)
    company_id = int((source.get("company_id") or [0])[0] or 0)
    if company_id <= 0:
        raise RuntimeError("Company source stock.item.journal tidak valid.")
    if int(args.company_id or 0) > 0 and int(args.company_id) != company_id:
        raise RuntimeError(
            f"Company mismatch: source company_id={company_id}, expected={int(args.company_id)}."
        )
    context = build_company_context(company_id)
    source_lines = await _read_item_journal_lines(client, _safe_int_list(source.get("line_ids")), context)
    scope_journals = await _read_scope_journals(client, scope_ids, context)
    missing_scope_ids = [item_id for item_id in scope_ids if item_id not in {int(row.get("id") or 0) for row in scope_journals}]
    if missing_scope_ids:
        raise RuntimeError(f"Scope stock.item.journal tidak ditemukan: {missing_scope_ids}")
    scope_moves = await _read_scope_moves(client, scope_ids, context)
    scope_svls = await _read_scope_svls(client, [int(row.get("id") or 0) for row in scope_moves], context)
    cleanup_lines, residual_summary, residual_errors = _build_cleanup_lines(source_lines, scope_svls)
    blockers = await read_company_lock_blockers(client, company_id, args.target_local_date)
    errors: list[str] = []
    source_state = normalize_text(source.get("state")).lower()
    if source_state != "done":
        errors.append(f"Source stock.item.journal harus done. Actual={source.get('state')!r}")
    bad_scope = [
        {"id": int(row.get("id") or 0), "state": row.get("state")}
        for row in scope_journals
        if normalize_text(row.get("state")).lower() != "done"
    ]
    if bad_scope:
        errors.append(f"Semua scope stock.item.journal harus done. Invalid={bad_scope}")
    errors.extend(residual_errors)
    if not cleanup_lines:
        errors.append("Tidak ada residual cleanup line yang perlu dibuat.")
    if blockers:
        errors.append(f"Target date {args.target_local_date} kena company lock date.")
    status = "ready" if not errors else "blocked"
    return {
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
        },
        "scope_item_journal_ids": scope_ids,
        "scope_item_journal_summary": [
            {
                "id": int(row.get("id") or 0),
                "name": normalize_text(row.get("name")),
                "state": normalize_text(row.get("state")),
                "accounting_date": row.get("accounting_date"),
                "inventory_date": row.get("inventory_date"),
                "move_count": len(_safe_int_list(row.get("move_ids"))),
                "memo": normalize_text(row.get("memo")),
            }
            for row in scope_journals
        ],
        "residual_plan": {
            "target_local_date": args.target_local_date,
            "target_inventory_datetime_utc": utc_string_from_local_date(date.fromisoformat(args.target_local_date), 7, False),
            "planned_line_count": len(cleanup_lines),
            "residual_net_qty_sum_before": residual_summary["net_qty_sum"],
            "residual_net_value_sum_before": residual_summary["net_value_sum"],
        },
        "fallback_analytic_distribution": _fallback_analytic_distribution(args),
        "cleanup_line_sample_first_20": cleanup_lines[:20],
        "status": status,
        "errors": errors,
        "lock_blockers": blockers,
    }


async def _compute_scope_net_after(
    client: Any,
    scope_ids: list[int],
    context: dict[str, Any],
) -> dict[str, float]:
    scope_moves = await _read_scope_moves(client, scope_ids, context)
    scope_svls = await _read_scope_svls(client, [int(row.get("id") or 0) for row in scope_moves], context)
    return {
        "net_qty_sum": _round_qty(sum(float(row.get("quantity") or 0.0) for row in scope_svls)),
        "net_value_sum": _round_value(sum(float(row.get("value") or 0.0) for row in scope_svls)),
        "move_count": len(scope_moves),
        "svl_count": len(scope_svls),
    }


async def amain() -> int:
    args = parse_args()
    try:
        date.fromisoformat(args.target_local_date)
    except ValueError as exc:
        raise SystemExit("--target-local-date must be YYYY-MM-DD") from exc

    conn = await open_manual_connection(args)
    try:
        precheck = await _build_precheck(conn.client, args)
        precheck["database"] = conn.config.database
        if not args.apply:
            print(json.dumps(precheck, ensure_ascii=False, indent=2, default=str))
            path = write_manual_artifact(
                precheck,
                args,
                f"cleanup_stock_item_journal_residual_{args.source_item_journal_id}_dry_run",
            )
            if path:
                print(f"Artefact: {path}")
            return 0

        if normalize_text(precheck.get("status")) != "ready":
            raise RuntimeError(f"Precheck blocked: {precheck.get('errors')}")

        company_id = int((precheck["source_item_journal"]["company_id"] or [0])[0] or 0)
        context = build_company_context(company_id)
        source = await _read_item_journal(conn.client, int(args.source_item_journal_id), context)
        source_lines = await _read_item_journal_lines(conn.client, _safe_int_list(source.get("line_ids")), context)
        scope_moves = await _read_scope_moves(conn.client, precheck["scope_item_journal_ids"], context)
        scope_svls = await _read_scope_svls(conn.client, [int(row.get("id") or 0) for row in scope_moves], context)
        cleanup_lines, residual_summary, residual_errors = _build_cleanup_lines(source_lines, scope_svls)
        if residual_errors:
            raise RuntimeError(f"Residual cleanup plan error: {residual_errors}")
        values = _build_create_values(
            source=source,
            cleanup_lines=cleanup_lines,
            target_local_date=args.target_local_date,
            line_name_prefix=args.line_name_prefix,
        )
        cleanup_id = await conn.client.create(
            "stock.item.journal",
            values,
            context=context,
            stage="MANUAL_CREATE_STOCK_ITEM_JOURNAL_RESIDUAL_CLEANUP",
        )
        if cleanup_id <= 0:
            raise RuntimeError("Create residual cleanup stock.item.journal gagal.")

        analytic_fill = await _fill_blank_line_analytic_distribution(
            conn.client,
            cleanup_id,
            context,
            _fallback_analytic_distribution(args),
        )
        before_submit = await _read_item_journal(conn.client, cleanup_id, context)
        await _submit_and_approve(conn.client, cleanup_id, context)
        after_done = await _read_item_journal(conn.client, cleanup_id, context)
        cleanup_moves = await _read_scope_moves(conn.client, [cleanup_id], context)
        cleanup_journal_ids = sorted({jid for row in cleanup_moves for jid in _safe_int_list(row.get("account_move_ids"))})
        cleanup_journals = await _read_journal_rows(conn.client, cleanup_journal_ids, context)
        net_after = await _compute_scope_net_after(conn.client, precheck["scope_item_journal_ids"] + [cleanup_id], context)

        payload = {
            "mode": "apply",
            "database": conn.config.database,
            "source_item_journal": precheck["source_item_journal"],
            "scope_item_journal_ids": precheck["scope_item_journal_ids"],
            "residual_before": residual_summary,
            "cleanup_prepare": analytic_fill,
            "cleanup_before_submit": before_submit,
            "cleanup_after_approve": after_done,
            "cleanup_stj_count": len(cleanup_journals),
            "cleanup_stj_date_counts": _journal_date_counts(cleanup_journals),
            "cleanup_stj_prefix_counts": _journal_prefix_counts(cleanup_journals),
            "cleanup_stj_sample_first_10": cleanup_journals[:10],
            "scope_net_after_cleanup": net_after,
            "status": "done" if normalize_text(after_done.get("state")).lower() == "done" else "unexpected_state",
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        path = write_manual_artifact(
            payload,
            args,
            f"cleanup_stock_item_journal_residual_{args.source_item_journal_id}_apply",
        )
        if path:
            print(f"Artefact: {path}")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(amain()))
