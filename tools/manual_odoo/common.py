#!/usr/bin/env python3
"""Shared helpers for guarded manual Odoo repair scripts."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
import sys
from typing import Any, Iterable, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartscc_tools.services.odoo.gateway import build_company_context
from tools.odoo_inspector.common import add_common_connection_args, open_tool_connection, write_json_artifact


DEFAULT_MANUAL_OUTPUT_DIR = PROJECT_ROOT / "logs" / "manual_odoo"


def add_manual_common_args(parser: argparse.ArgumentParser) -> None:
    add_common_connection_args(parser)
    parser.set_defaults(output_dir=str(DEFAULT_MANUAL_OUTPUT_DIR))
    parser.add_argument("--apply", action="store_true", help="Apply writes. Default is dry-run.")


def m2o_id(value: Any) -> int:
    if isinstance(value, (list, tuple)) and value:
        try:
            return int(value[0])
        except (TypeError, ValueError):
            return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def chunks(items: Sequence[int], size: int) -> Iterable[list[int]]:
    for start in range(0, len(items), size):
        yield [int(item) for item in items[start : start + size]]


def normalize_qty(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        text = format(Decimal(str(value)).normalize(), "f")
    except (InvalidOperation, ValueError):
        return str(value).strip()
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def parse_odoo_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


async def fetch_picking(client: Any, picking_name: str) -> dict[str, Any]:
    rows = await client.search_read(
        "stock.picking",
        [["name", "=", picking_name]],
        fields=["id", "name", "state", "company_id"],
        limit=2,
        stage="MANUAL_FETCH_PICKING",
    )
    if len(rows) != 1:
        raise RuntimeError(f"Picking {picking_name} tidak unik / tidak ditemukan: {len(rows)} row.")
    return rows[0]


async def fetch_move_ids_for_picking(client: Any, picking_id: int, context: dict[str, Any]) -> list[int]:
    result = await client.execute_kw(
        "stock.move",
        "search",
        args=[[["picking_id", "=", int(picking_id)]]],
        kwargs={"context": context, "limit": 10000, "order": "id asc"},
        stage="MANUAL_FETCH_PICKING_MOVES",
    )
    return [int(item) for item in result or []]


async def read_journal_rows(client: Any, journal_ids: Sequence[int], context: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for batch in chunks(list(journal_ids), 200):
        rows.extend(
            await client.read(
                "account.move",
                batch,
                fields=["id", "name", "state", "date", "ref", "company_id"],
                context=context,
                stage="MANUAL_READ_JOURNALS",
            )
        )
    return rows


async def fetch_related_journals_for_picking(client: Any, picking_name: str) -> dict[str, Any]:
    picking = await fetch_picking(client, picking_name)
    company_id = m2o_id(picking.get("company_id"))
    context = build_company_context(company_id)
    move_ids = await fetch_move_ids_for_picking(client, int(picking["id"]), context)

    journal_ids: set[int] = set()
    aml_ids: set[int] = set()
    svl_count = 0
    for batch in chunks(move_ids, 200):
        svl_rows = await client.search_read(
            "stock.valuation.layer",
            [["stock_move_id", "in", batch]],
            fields=["id", "stock_move_id", "account_move_line_id", "account_move_id"],
            limit=10000,
            context=context,
            stage="MANUAL_FETCH_SVL_BY_MOVE",
        )
        svl_count += len(svl_rows)
        for row in svl_rows:
            journal_id = m2o_id(row.get("account_move_id"))
            aml_id = m2o_id(row.get("account_move_line_id"))
            if journal_id:
                journal_ids.add(journal_id)
            if aml_id:
                aml_ids.add(aml_id)

    for batch in chunks(sorted(aml_ids), 200):
        aml_rows = await client.read(
            "account.move.line",
            batch,
            fields=["id", "move_id"],
            context=context,
            stage="MANUAL_FETCH_AML_MOVE",
        )
        for row in aml_rows:
            journal_id = m2o_id(row.get("move_id"))
            if journal_id:
                journal_ids.add(journal_id)

    return {
        "picking": picking,
        "company_id": company_id,
        "context": context,
        "move_ids": move_ids,
        "svl_count": svl_count,
        "aml_ids": sorted(aml_ids),
        "journal_ids": sorted(journal_ids),
        "journal_rows": await read_journal_rows(client, sorted(journal_ids), context),
    }


async def read_company_lock_blockers(client: Any, company_id: int, target_date: str) -> list[dict[str, Any]]:
    target = parse_odoo_date(target_date)
    if company_id <= 0 or target is None:
        return []
    fields_meta = await client.fields_get("res.company", attributes=["type"], stage="MANUAL_COMPANY_FIELDS")
    candidate_fields = [
        "fiscalyear_lock_date",
        "hard_lock_date",
        "user_fiscalyear_lock_date",
        "user_hard_lock_date",
    ]
    fields = ["id", "name"] + [name for name in candidate_fields if name in fields_meta]
    rows = await client.read("res.company", [company_id], fields=fields, stage="MANUAL_COMPANY_LOCK_READ")
    row = rows[0] if rows else {}
    blockers: list[dict[str, Any]] = []
    for field in candidate_fields:
        value = row.get(field)
        parsed = parse_odoo_date(value)
        if parsed is not None and target <= parsed:
            blockers.append({"model": "res.company", "field": field, "lock_date": str(value)})
    return blockers


async def batch_method(client: Any, model: str, method: str, ids: Sequence[int], context: dict[str, Any]) -> None:
    for batch in chunks(list(ids), 100):
        await client.execute_kw(model, method, args=[batch], kwargs={"context": context}, stage=f"MANUAL_{method}", mutating=True)


async def batch_write(client: Any, model: str, ids: Sequence[int], values: dict[str, Any], context: dict[str, Any]) -> None:
    for batch in chunks(list(ids), 100):
        await client.write(model, batch, dict(values), context=context, stage=f"MANUAL_WRITE_{model}")


async def read_journal_names(client: Any, journal_ids: Sequence[int], context: dict[str, Any]) -> dict[int, str]:
    rows = await read_journal_rows(client, journal_ids, context)
    return {int(row["id"]): str(row.get("name") or "") for row in rows if row.get("id")}


async def build_move_to_journal_map(client: Any, move_ids: Sequence[int], context: dict[str, Any]) -> dict[int, int]:
    result: dict[int, int] = {}
    for batch in chunks(list(move_ids), 200):
        svl_rows = await client.search_read(
            "stock.valuation.layer",
            [["stock_move_id", "in", batch]],
            fields=["id", "stock_move_id", "account_move_id", "account_move_line_id"],
            limit=10000,
            context=context,
            stage="MANUAL_MAP_MOVE_TO_JOURNAL",
        )
        aml_ids: set[int] = set()
        for row in svl_rows:
            move_id = m2o_id(row.get("stock_move_id"))
            journal_id = m2o_id(row.get("account_move_id"))
            aml_id = m2o_id(row.get("account_move_line_id"))
            if move_id and journal_id:
                result[move_id] = journal_id
            if aml_id:
                aml_ids.add(aml_id)
        if aml_ids:
            aml_rows = await client.read(
                "account.move.line",
                sorted(aml_ids),
                fields=["id", "move_id"],
                context=context,
                stage="MANUAL_MAP_AML_TO_JOURNAL",
            )
            aml_to_journal = {int(row["id"]): m2o_id(row.get("move_id")) for row in aml_rows if row.get("id")}
            for row in svl_rows:
                move_id = m2o_id(row.get("stock_move_id"))
                aml_id = m2o_id(row.get("account_move_line_id"))
                journal_id = aml_to_journal.get(aml_id, 0)
                if move_id and journal_id and move_id not in result:
                    result[move_id] = journal_id
    return result


async def fetch_move_rows_with_product_keys(client: Any, move_ids: Sequence[int], context: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for batch in chunks(list(move_ids), 200):
        rows.extend(
            await client.read(
                "stock.move",
                batch,
                fields=["id", "product_id", "product_uom_qty", "product_qty", "product_uom"],
                context=context,
                stage="MANUAL_FETCH_MOVE_PRODUCT_KEYS",
            )
        )
    out: list[dict[str, Any]] = []
    for row in sorted(rows, key=lambda item: int(item.get("id") or 0)):
        product = row.get("product_id") or []
        uom = row.get("product_uom") or []
        out.append(
            {
                "move_id": int(row.get("id") or 0),
                "prod_key": str(product[1] if isinstance(product, (list, tuple)) and len(product) > 1 else product).strip(),
                "qty": normalize_qty(row.get("product_uom_qty", row.get("product_qty"))),
                "uom": str(uom[1] if isinstance(uom, (list, tuple)) and len(uom) > 1 else uom).strip(),
            }
        )
    return out


async def open_manual_connection(args: argparse.Namespace):
    return await open_tool_connection(
        database_profile_id=args.database_profile,
        max_concurrency=args.max_concurrency,
        verbose=args.verbose,
    )


def write_manual_artifact(payload: dict[str, Any], args: argparse.Namespace, prefix: str) -> Path | None:
    if args.no_artifact:
        return None
    return write_json_artifact(payload, output_dir=args.output_dir, prefix=prefix)

