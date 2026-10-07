#!/usr/bin/env python3
"""Guarded manual fix: move named MISC account.move rows to a target date."""

from __future__ import annotations

import argparse
import asyncio
from datetime import date
import json
from typing import Any

from common import (
    add_manual_common_args,
    batch_method,
    batch_write,
    chunks,
    m2o_id,
    open_manual_connection,
    parse_odoo_date,
    write_manual_artifact,
)
from smartscc_tools.services.odoo.gateway import build_company_context


DEFAULT_TARGET_DATE = "2026-04-01"
DEFAULT_SOURCE_MONTH = "2026-03"

ACCOUNT_MOVE_BASE_FIELDS = [
    "id",
    "name",
    "state",
    "date",
    "ref",
    "company_id",
    "journal_id",
    "move_type",
    "posted_before",
]

COMPANY_LOCK_FIELDS = [
    "fiscalyear_lock_date",
    "hard_lock_date",
    "sale_lock_date",
    "purchase_lock_date",
    "tax_lock_date",
    "user_fiscalyear_lock_date",
    "user_hard_lock_date",
    "user_sale_lock_date",
    "user_purchase_lock_date",
    "user_tax_lock_date",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Move named MISC/account.move rows from one source month to a target date."
    )
    parser.add_argument("--name", action="append", default=[], help="Journal number. Repeat as needed.")
    parser.add_argument(
        "--names",
        default="",
        help="Comma/newline separated journal numbers, for example MISC/2026/03/0020,MISC/2026/03/0021.",
    )
    parser.add_argument("--company-id", type=int, required=True, help="Expected Odoo res.company id.")
    parser.add_argument("--source-month", default=DEFAULT_SOURCE_MONTH, help="Only change dates in YYYY-MM.")
    parser.add_argument("--target-date", default=DEFAULT_TARGET_DATE, help="Target journal date, YYYY-MM-DD.")
    parser.add_argument(
        "--preserve-name",
        action="store_true",
        help="Do not reset account.move.name to '/'. Default resets names when changing sequence month.",
    )
    add_manual_common_args(parser)
    return parser.parse_args()


def parse_names(args: argparse.Namespace) -> list[str]:
    raw_parts: list[str] = []
    raw_parts.extend(str(item or "") for item in args.name)
    raw_parts.extend(str(args.names or "").replace("\n", ",").split(","))

    names: list[str] = []
    seen: set[str] = set()
    for part in raw_parts:
        clean = part.strip()
        if clean and clean not in seen:
            names.append(clean)
            seen.add(clean)
    if not names:
        raise SystemExit("Isi --name atau --names.")
    return names


def safe_m2o_name(value: Any) -> str:
    if isinstance(value, (list, tuple)) and len(value) > 1:
        return str(value[1] or "")
    return ""


def is_source_month(value: Any, source_month: str) -> bool:
    parsed = parse_odoo_date(value)
    return parsed is not None and parsed.strftime("%Y-%m") == source_month


def target_period_differs(source_name: str, source_date: Any, target_date: str) -> bool:
    source = parse_odoo_date(source_date)
    target = parse_odoo_date(target_date)
    if source is None or target is None:
        return False
    source_period = source.strftime("%Y/%m")
    target_period = target.strftime("%Y/%m")
    return source_period != target_period and f"/{source_period}/" in str(source_name or "")


def read_lock_value(row: dict[str, Any], user_field: str, raw_field: str) -> str:
    value = row.get(user_field)
    if value in (None, False, ""):
        value = row.get(raw_field)
    return str(value or "")


def build_effective_locks(
    company_row: dict[str, Any],
    *,
    journal_type: str,
    has_tax: bool,
) -> list[dict[str, Any]]:
    checks = [
        ("fiscalyear_lock_date", "user_fiscalyear_lock_date", "fiscalyear"),
        ("hard_lock_date", "user_hard_lock_date", "hard"),
    ]
    if journal_type == "sale":
        checks.append(("sale_lock_date", "user_sale_lock_date", "sale"))
    elif journal_type == "purchase":
        checks.append(("purchase_lock_date", "user_purchase_lock_date", "purchase"))
    if has_tax:
        checks.append(("tax_lock_date", "user_tax_lock_date", "tax"))

    locks: list[dict[str, Any]] = []
    for raw_field, user_field, scope in checks:
        lock_text = read_lock_value(company_row, user_field, raw_field)
        lock_date = parse_odoo_date(lock_text)
        if lock_date is None:
            continue
        locks.append({"scope": scope, "field": user_field if company_row.get(user_field) else raw_field, "lock_date": lock_text})
    return locks


def lock_blockers(
    company_row: dict[str, Any],
    *,
    journal_type: str,
    has_tax: bool,
    checked_date: Any,
) -> list[dict[str, Any]]:
    checked = parse_odoo_date(checked_date)
    if checked is None:
        return []
    blockers: list[dict[str, Any]] = []
    for item in build_effective_locks(company_row, journal_type=journal_type, has_tax=has_tax):
        lock_date = parse_odoo_date(item.get("lock_date"))
        if lock_date is not None and checked <= lock_date:
            blocked = dict(item)
            blocked["checked_date"] = checked.isoformat()
            blockers.append(blocked)
    return blockers


async def safe_fields(client: Any, model: str, candidates: list[str], context: dict[str, Any]) -> list[str]:
    meta = await client.fields_get(model, attributes=["type"], context=context, stage=f"MANUAL_FIELDS_{model}")
    return [name for name in candidates if name in meta]


async def read_company_lock_snapshot(client: Any, company_id: int, context: dict[str, Any]) -> dict[str, Any]:
    company_fields = await safe_fields(client, "res.company", ["id", "name", *COMPANY_LOCK_FIELDS], context)
    company_rows = await client.read(
        "res.company",
        [int(company_id)],
        fields=company_fields,
        context=context,
        stage="MANUAL_READ_COMPANY_LOCKS",
    )
    company_row = company_rows[0] if company_rows else {}

    account_lock_rows: list[dict[str, Any]] = []
    account_lock_error = ""
    try:
        account_lock_fields = await safe_fields(
            client,
            "account.change.lock.date",
            ["id", "fiscalyear_lock_date", "hard_lock_date", "sale_lock_date", "purchase_lock_date", "tax_lock_date"],
            context,
        )
        if account_lock_fields:
            account_lock_rows = await client.search_read(
                "account.change.lock.date",
                [],
                fields=account_lock_fields,
                limit=1,
                order="id desc",
                context=context,
                stage="MANUAL_READ_ACCOUNT_LOCK_WIZARD",
            )
    except Exception as exc:  # noqa: BLE001
        account_lock_error = str(exc)

    return {
        "company_row": company_row,
        "account_change_lock_date_latest": account_lock_rows[0] if account_lock_rows else {},
        "account_change_lock_date_error": account_lock_error,
    }


async def read_move_line_tax_flags(client: Any, move_ids: list[int], context: dict[str, Any]) -> dict[int, bool]:
    result = {int(move_id): False for move_id in move_ids}
    if not move_ids:
        return result

    line_fields = await safe_fields(client, "account.move.line", ["id", "move_id", "tax_ids", "tax_line_id"], context)
    if "move_id" not in line_fields:
        return result

    tax_fields = [field for field in ("tax_ids", "tax_line_id") if field in line_fields]
    if not tax_fields:
        return result

    for batch in chunks(move_ids, 200):
        rows = await client.search_read(
            "account.move.line",
            [["move_id", "in", batch]],
            fields=["move_id", *tax_fields],
            limit=10000,
            context=context,
            stage="MANUAL_READ_MOVE_LINE_TAX_FLAGS",
        )
        for row in rows:
            move_id = m2o_id(row.get("move_id"))
            if not move_id:
                continue
            if isinstance(row.get("tax_ids"), list) and row.get("tax_ids"):
                result[move_id] = True
            if m2o_id(row.get("tax_line_id")):
                result[move_id] = True
    return result


async def read_journal_types(client: Any, journal_ids: list[int], context: dict[str, Any]) -> dict[int, str]:
    result: dict[int, str] = {}
    clean_ids = sorted({int(item) for item in journal_ids if int(item) > 0})
    if not clean_ids:
        return result
    fields = await safe_fields(client, "account.journal", ["id", "name", "type"], context)
    if "type" not in fields:
        return result
    for batch in chunks(clean_ids, 200):
        rows = await client.read(
            "account.journal",
            batch,
            fields=fields,
            context=context,
            stage="MANUAL_READ_JOURNAL_TYPES",
        )
        for row in rows:
            result[int(row.get("id") or 0)] = str(row.get("type") or "")
    return result


async def read_named_moves(client: Any, names: list[str], context: dict[str, Any]) -> list[dict[str, Any]]:
    fields = await safe_fields(client, "account.move", ACCOUNT_MOVE_BASE_FIELDS, context)
    rows: list[dict[str, Any]] = []
    for batch_names in [names[start : start + 200] for start in range(0, len(names), 200)]:
        rows.extend(
            await client.search_read(
                "account.move",
                [["name", "in", batch_names]],
                fields=fields,
                limit=10000,
                order="name asc, id asc",
                context=context,
                stage="MANUAL_READ_NAMED_MISC_MOVES",
            )
        )
    return rows


async def draft_posted_moves(client: Any, posted_ids: list[int], context: dict[str, Any]) -> None:
    if not posted_ids:
        return
    try:
        await batch_method(client, "account.move", "button_draft", posted_ids, context)
    except Exception as exc:  # noqa: BLE001
        text = str(exc)
        try:
            await batch_method(client, "account.move", "action_draft", posted_ids, context)
        except Exception:  # noqa: BLE001
            raise RuntimeError(f"button_draft gagal: {text}") from exc


async def build_plan(args: argparse.Namespace, names: list[str]) -> tuple[Any, dict[str, Any], dict[str, Any]]:
    target = date.fromisoformat(args.target_date)
    source_month = str(args.source_month or "").strip()
    if len(source_month) != 7 or source_month[4] != "-":
        raise SystemExit("--source-month harus format YYYY-MM.")

    conn = await open_manual_connection(args)
    context = build_company_context(int(args.company_id))
    try:
        lock_snapshot = await read_company_lock_snapshot(conn.client, int(args.company_id), context)
        rows = await read_named_moves(conn.client, names, context)
        rows_by_name: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            rows_by_name.setdefault(str(row.get("name") or ""), []).append(row)

        target_rows: list[dict[str, Any]] = []
        for group in rows_by_name.values():
            for row in group:
                if m2o_id(row.get("company_id")) == int(args.company_id):
                    target_rows.append(row)

        tax_flags = await read_move_line_tax_flags(
            conn.client,
            [int(row.get("id") or 0) for row in target_rows if int(row.get("id") or 0) > 0],
            context,
        )
        journal_types = await read_journal_types(
            conn.client,
            [m2o_id(row.get("journal_id")) for row in target_rows],
            context,
        )
        company_row = lock_snapshot.get("company_row") or {}

        entries: list[dict[str, Any]] = []
        for name in names:
            matching = rows_by_name.get(name, [])
            matching_company = [row for row in matching if m2o_id(row.get("company_id")) == int(args.company_id)]
            other_company_rows = [row for row in matching if m2o_id(row.get("company_id")) != int(args.company_id)]

            if not matching:
                entries.append({"requested_name": name, "status": "missing", "fatal": True})
                continue
            if not matching_company:
                entries.append(
                    {
                        "requested_name": name,
                        "status": "wrong_company",
                        "fatal": True,
                        "other_company_rows": other_company_rows,
                    }
                )
                continue
            if len(matching_company) > 1:
                entries.append(
                    {
                        "requested_name": name,
                        "status": "ambiguous_company_record",
                        "fatal": True,
                        "matching_company_rows": matching_company,
                    }
                )
                continue

            row = dict(matching_company[0])
            move_id = int(row.get("id") or 0)
            row["company_id_value"] = m2o_id(row.get("company_id"))
            row["company_name"] = safe_m2o_name(row.get("company_id"))
            row["journal_id_value"] = m2o_id(row.get("journal_id"))
            row["journal_name"] = safe_m2o_name(row.get("journal_id"))
            row["journal_type"] = journal_types.get(row["journal_id_value"], "")
            row["has_tax"] = bool(tax_flags.get(move_id))

            current_date = parse_odoo_date(row.get("date"))
            if current_date is None:
                entries.append({"requested_name": name, "status": "invalid_date", "fatal": True, "row": row})
                continue
            if current_date == target:
                entries.append({"requested_name": name, "status": "already_target_date", "fatal": False, "row": row})
                continue
            if not is_source_month(row.get("date"), source_month):
                entries.append({"requested_name": name, "status": "not_source_month", "fatal": False, "row": row})
                continue

            target_blockers = lock_blockers(
                company_row,
                journal_type=row["journal_type"],
                has_tax=row["has_tax"],
                checked_date=args.target_date,
            )
            current_blockers: list[dict[str, Any]] = []
            if str(row.get("state") or "") == "posted":
                current_blockers = lock_blockers(
                    company_row,
                    journal_type=row["journal_type"],
                    has_tax=row["has_tax"],
                    checked_date=row.get("date"),
                )

            if target_blockers or current_blockers:
                entries.append(
                    {
                        "requested_name": name,
                        "status": "lock_blocked",
                        "fatal": True,
                        "row": row,
                        "target_date_blockers": target_blockers,
                        "current_date_unpost_blockers": current_blockers,
                    }
                )
                continue

            state = str(row.get("state") or "")
            if state not in {"posted", "draft"}:
                entries.append({"requested_name": name, "status": f"unsupported_state_{state or 'empty'}", "fatal": True, "row": row})
                continue

            reset_name = (not args.preserve_name) and target_period_differs(str(row.get("name") or ""), row.get("date"), args.target_date)
            entries.append(
                {
                    "requested_name": name,
                    "status": "will_update",
                    "fatal": False,
                    "row": row,
                    "target_date": args.target_date,
                    "reset_name": reset_name,
                }
            )

        plan = {
            "action": "fix_misc_journal_dates",
            "mode": "apply" if args.apply else "dry_run",
            "database": conn.config.database,
            "company_id": int(args.company_id),
            "source_month": source_month,
            "target_date": args.target_date,
            "requested_count": len(names),
            "found_row_count": len(rows),
            "lock_snapshot": lock_snapshot,
            "entries": entries,
            "summary": {
                "will_update": sum(1 for item in entries if item.get("status") == "will_update"),
                "fatal": sum(1 for item in entries if item.get("fatal")),
                "skipped": sum(1 for item in entries if item.get("status") not in {"will_update"} and not item.get("fatal")),
            },
        }
        return conn, context, plan
    except Exception:
        await conn.close()
        raise


async def amain() -> int:
    args = parse_args()
    names = parse_names(args)
    try:
        date.fromisoformat(args.target_date)
    except ValueError as exc:
        raise SystemExit("--target-date harus YYYY-MM-DD.") from exc

    conn, context, plan = await build_plan(args, names)
    try:
        if args.apply:
            fatal_entries = [entry for entry in plan["entries"] if entry.get("fatal")]
            if fatal_entries:
                plan["apply_error"] = "Apply dibatalkan karena ada entry fatal/blocker."
                raise RuntimeError(plan["apply_error"])

            targets = [entry for entry in plan["entries"] if entry.get("status") == "will_update"]
            posted_ids = [int(entry["row"]["id"]) for entry in targets if str(entry["row"].get("state") or "") == "posted"]
            draft_ids = [int(entry["row"]["id"]) for entry in targets if str(entry["row"].get("state") or "") == "draft"]
            reset_ids = [int(entry["row"]["id"]) for entry in targets if entry.get("reset_name")]
            non_reset_ids = [int(entry["row"]["id"]) for entry in targets if not entry.get("reset_name")]

            if posted_ids:
                await draft_posted_moves(conn.client, posted_ids, context)
                reset_posted_ids = [item for item in posted_ids if item in reset_ids]
                keep_posted_ids = [item for item in posted_ids if item in non_reset_ids]
                if reset_posted_ids:
                    await batch_write(conn.client, "account.move", reset_posted_ids, {"date": args.target_date, "name": "/"}, context)
                if keep_posted_ids:
                    await batch_write(conn.client, "account.move", keep_posted_ids, {"date": args.target_date}, context)
                await batch_method(conn.client, "account.move", "action_post", posted_ids, context)

            if draft_ids:
                reset_draft_ids = [item for item in draft_ids if item in reset_ids]
                keep_draft_ids = [item for item in draft_ids if item in non_reset_ids]
                if reset_draft_ids:
                    await batch_write(conn.client, "account.move", reset_draft_ids, {"date": args.target_date, "name": "/"}, context)
                if keep_draft_ids:
                    await batch_write(conn.client, "account.move", keep_draft_ids, {"date": args.target_date}, context)

            after_rows = []
            changed_ids = posted_ids + draft_ids
            if changed_ids:
                fields = await safe_fields(conn.client, "account.move", ACCOUNT_MOVE_BASE_FIELDS, context)
                for batch in chunks(changed_ids, 200):
                    after_rows.extend(
                        await conn.client.read(
                            "account.move",
                            batch,
                            fields=fields,
                            context=context,
                            stage="MANUAL_VERIFY_MISC_MOVES",
                        )
                    )
            plan["applied_posted_count"] = len(posted_ids)
            plan["applied_draft_count"] = len(draft_ids)
            plan["after_rows"] = after_rows
            after_by_id = {int(row.get("id") or 0): row for row in after_rows}
            plan["name_changes"] = [
                {
                    "id": int(entry["row"]["id"]),
                    "old_name": entry["row"].get("name"),
                    "new_name": (after_by_id.get(int(entry["row"]["id"])) or {}).get("name"),
                    "new_date": (after_by_id.get(int(entry["row"]["id"])) or {}).get("date"),
                    "new_state": (after_by_id.get(int(entry["row"]["id"])) or {}).get("state"),
                }
                for entry in targets
            ]

        print(json.dumps(plan, ensure_ascii=False, indent=2, default=str))
        path = write_manual_artifact(plan, args, f"fix_misc_journal_dates_c{args.company_id}_{args.source_month}_to_{args.target_date}")
        if path:
            print(f"Artefact: {path}")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(json.dumps(plan, ensure_ascii=False, indent=2, default=str))
        path = write_manual_artifact(plan, args, f"fix_misc_journal_dates_c{args.company_id}_{args.source_month}_blocked")
        if path:
            print(f"Artefact: {path}")
        raise SystemExit(str(exc)) from exc
    finally:
        await conn.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(amain()))
