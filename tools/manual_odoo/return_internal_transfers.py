#!/usr/bin/env python3
"""Guarded manual fix: create or reuse returns for done Internal Transfers and sync dates."""

from __future__ import annotations

import argparse
import asyncio
from datetime import date
import json
from pathlib import Path
import time
from typing import Any

from common import (
    add_manual_common_args,
    batch_method,
    batch_write,
    fetch_move_ids_for_picking,
    fetch_picking,
    fetch_related_journals_for_picking,
    m2o_id,
    open_manual_connection,
    read_company_lock_blockers,
    read_journal_rows,
    write_manual_artifact,
)
from smartscc_tools.features.item_journal.services.date_ops import DateSyncServiceAsync
from smartscc_tools.features.item_journal.utils import local_date_from_utc_text, normalize_text, utc_string_from_local_date
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
        description="Create safe returns for done Internal Transfers and backdate return stock/STJ dates."
    )
    parser.add_argument("--company-id", type=int, help="Expected company id for all requested pickings.")
    parser.add_argument(
        "--picking",
        dest="pickings",
        action="append",
        required=True,
        help="Stock picking number, for example HNMG/INT/00072. Repeat for multiple pickings.",
    )
    parser.add_argument(
        "--target-local-date",
        help="Optional YYYY-MM-DD override for every picking. Default: infer from original picking date_done.",
    )
    add_manual_common_args(parser)
    parser.set_defaults(output_dir=_default_output_dir())
    return parser.parse_args()


async def _read_picking_row(client: Any, picking_id: int, context: dict[str, Any]) -> dict[str, Any]:
    rows = await client.read(
        "stock.picking",
        [int(picking_id)],
        fields=[
            "id",
            "name",
            "state",
            "company_id",
            "return_id",
            "return_ids",
            "return_count",
            "origin",
            "date_done",
            "scheduled_date",
        ],
        context=context,
        stage="MANUAL_READ_PICKING_ROW",
    )
    return rows[0] if rows else {}


async def _stock_move_qty_field_candidates(client: Any) -> list[str]:
    meta = await client.fields_get("stock.move", attributes=["type"], stage="MANUAL_STOCK_MOVE_FIELDS")
    out: list[str] = []
    for field_name in ("product_uom_qty", "product_qty", "quantity"):
        field_meta = meta.get(field_name, {})
        field_type = normalize_text(field_meta.get("type")).lower()
        if field_type in {"float", "integer", "monetary"}:
            out.append(field_name)
    return out or ["product_uom_qty"]


def _pick_move_qty_value(move_row: dict[str, Any], qty_fields: list[str]) -> float:
    for field_name in qty_fields:
        try:
            return float(move_row.get(field_name) or 0.0)
        except (TypeError, ValueError):
            continue
    return 0.0


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


async def _search_return_ids(client: Any, original_picking_id: int, context: dict[str, Any]) -> list[int]:
    result = await client.execute_kw(
        "stock.picking",
        "search",
        args=[[["return_id", "=", int(original_picking_id)]]],
        kwargs={"context": context, "order": "id asc", "limit": 20},
        stage="MANUAL_SEARCH_RETURN_IDS",
    )
    return _safe_int_list(result)


async def _resolve_existing_return_rows(
    client: Any,
    *,
    original_row: dict[str, Any],
    context: dict[str, Any],
) -> list[dict[str, Any]]:
    return_ids = _safe_int_list(original_row.get("return_ids"))
    if not return_ids and int(original_row.get("return_count") or 0) > 0:
        return_ids = await _search_return_ids(client, int(original_row["id"]), context)
    if not return_ids:
        return []
    rows = await client.read(
        "stock.picking",
        return_ids,
        fields=[
            "id",
            "name",
            "state",
            "company_id",
            "return_id",
            "return_ids",
            "return_count",
            "origin",
            "date_done",
            "scheduled_date",
        ],
        context=context,
        stage="MANUAL_READ_EXISTING_RETURN_ROWS",
    )
    return sorted(rows, key=lambda row: int(row.get("id") or 0))


def _choose_target_local_date(
    *,
    row: dict[str, Any],
    settings: Any,
    explicit_target_date: str,
) -> tuple[str, str]:
    if explicit_target_date:
        return explicit_target_date, "arg.target_local_date"
    date_done_text = normalize_text(row.get("date_done"))
    if date_done_text:
        picked = local_date_from_utc_text(
            date_done_text,
            local_tz_offset_hours=settings.local_tz_offset,
            use_local_as_utc=settings.use_local_time_as_utc,
        )
        if picked is not None:
            return picked.strftime("%Y-%m-%d"), "original.date_done"
    scheduled_text = normalize_text(row.get("scheduled_date"))
    if scheduled_text:
        picked = local_date_from_utc_text(
            scheduled_text,
            local_tz_offset_hours=settings.local_tz_offset,
            use_local_as_utc=settings.use_local_time_as_utc,
        )
        if picked is not None:
            return picked.strftime("%Y-%m-%d"), "original.scheduled_date"
    raise RuntimeError(
        f"Picking {normalize_text(row.get('name')) or row.get('id')} tidak punya tanggal asal yang bisa dipakai."
    )


async def _build_precheck_entry(
    client: Any,
    *,
    picking_name: str,
    company_id_expected: int,
    explicit_target_date: str,
    settings: Any,
    qty_fields: list[str],
) -> dict[str, Any]:
    base_row = await fetch_picking(client, picking_name)
    company_id = m2o_id(base_row.get("company_id"))
    context = build_company_context(company_id)
    row = await _read_picking_row(client, int(base_row["id"]), context)

    entry: dict[str, Any] = {
        "name": normalize_text(row.get("name")) or picking_name,
        "picking_id": int(row.get("id") or 0),
        "state": normalize_text(row.get("state")),
        "company": row.get("company_id"),
        "status": "ready",
        "errors": [],
    }
    if company_id_expected > 0 and company_id != company_id_expected:
        entry["status"] = "blocked"
        entry["errors"].append(
            f"Company mismatch. Expected {company_id_expected}, actual {company_id} ({row.get('company_id')})."
        )

    if normalize_text(row.get("state")).lower() != "done":
        entry["status"] = "blocked"
        entry["errors"].append(f"Picking state harus done. Actual={normalize_text(row.get('state')) or '-'}")

    target_local_date, target_source = _choose_target_local_date(
        row=row,
        settings=settings,
        explicit_target_date=explicit_target_date,
    )
    entry["target_local_date"] = target_local_date
    entry["target_date_source"] = target_source
    entry["target_stock_datetime_utc"] = utc_string_from_local_date(
        date.fromisoformat(target_local_date),
        settings.local_tz_offset,
        settings.use_local_time_as_utc,
    )

    blockers = await read_company_lock_blockers(client, company_id, target_local_date)
    entry["lock_blockers"] = blockers
    if blockers:
        entry["status"] = "blocked"
        entry["errors"].append(f"Target date diblokir lock date: {blockers}")

    existing_rows = await _resolve_existing_return_rows(client, original_row=row, context=context)
    entry["return_count"] = len(existing_rows)
    entry["existing_return_ids"] = [int(item.get("id") or 0) for item in existing_rows if int(item.get("id") or 0) > 0]
    entry["existing_returns"] = [
        {
            "id": int(item.get("id") or 0),
            "name": normalize_text(item.get("name")),
            "state": normalize_text(item.get("state")),
            "date_done": item.get("date_done"),
            "scheduled_date": item.get("scheduled_date"),
        }
        for item in existing_rows
    ]
    if len(existing_rows) == 1:
        entry["status"] = "reuse_existing_return"
    elif len(existing_rows) > 1:
        entry["status"] = "blocked"
        entry["errors"].append(f"Lebih dari satu return sudah ada: {entry['existing_return_ids']}")

    move_rows = await client.search_read(
        "stock.move",
        [["picking_id", "=", int(row["id"])], ["state", "!=", "cancel"], ["scrapped", "=", False]],
        fields=["id", "state", "scrapped"] + qty_fields,
        limit=100000,
        context=context,
        stage="MANUAL_PRECHECK_MOVES",
    )
    move_state_counts: dict[str, int] = {}
    nonzero_count = 0
    abs_qty_sum = 0.0
    for move_row in move_rows:
        state_text = normalize_text(move_row.get("state")) or "(blank)"
        move_state_counts[state_text] = move_state_counts.get(state_text, 0) + 1
        qty = _pick_move_qty_value(move_row, qty_fields)
        abs_qty_sum += abs(qty)
        if abs(qty) > 1e-9:
            nonzero_count += 1
    entry["move_count_non_cancel_non_scrap"] = len(move_rows)
    entry["move_state_counts"] = move_state_counts
    entry["nonzero_move_quantity_count"] = nonzero_count
    entry["abs_quantity_sum_diagnostic"] = abs_qty_sum
    return entry


async def _create_return_wizard(
    client: Any,
    *,
    original_picking_id: int,
    company_id: int,
) -> tuple[int, dict[str, Any]]:
    wizard_context = build_company_context(company_id)
    wizard_context.update(
        {
            "active_model": "stock.picking",
            "active_id": int(original_picking_id),
            "active_ids": [int(original_picking_id)],
        }
    )
    wizard_id = await client.create(
        "stock.return.picking",
        {"picking_id": int(original_picking_id)},
        context=wizard_context,
        stage="MANUAL_RETURN_WIZARD_CREATE",
    )
    if wizard_id <= 0:
        raise RuntimeError("Wizard stock.return.picking gagal dibuat.")
    rows = await client.read(
        "stock.return.picking",
        [int(wizard_id)],
        fields=["id", "picking_id", "product_return_moves"],
        context=wizard_context,
        stage="MANUAL_RETURN_WIZARD_READ",
    )
    if not rows:
        raise RuntimeError(f"Wizard stock.return.picking id={wizard_id} tidak dapat dibaca.")
    return wizard_id, {"context": wizard_context, "row": rows[0]}


async def _fill_zero_return_line_qty(
    client: Any,
    *,
    wizard_id: int,
    wizard_context: dict[str, Any],
    qty_fields: list[str],
) -> dict[str, Any]:
    wizard_rows = await client.read(
        "stock.return.picking",
        [int(wizard_id)],
        fields=["product_return_moves"],
        context=wizard_context,
        stage="MANUAL_RETURN_WIZARD_LINES",
    )
    line_ids = _safe_int_list((wizard_rows[0] if wizard_rows else {}).get("product_return_moves"))
    if not line_ids:
        return {"line_count": 0, "zero_line_ids": [], "filled_line_ids": []}

    line_rows = await client.read(
        "stock.return.picking.line",
        line_ids,
        fields=["id", "move_id", "quantity"],
        context=wizard_context,
        stage="MANUAL_RETURN_WIZARD_LINE_READ",
    )
    move_ids = sorted({m2o_id(line.get("move_id")) for line in line_rows if m2o_id(line.get("move_id")) > 0})
    move_rows = await client.read(
        "stock.move",
        move_ids,
        fields=["id"] + qty_fields,
        context=wizard_context,
        stage="MANUAL_RETURN_WIZARD_MOVE_QTY",
    )
    qty_by_move = {
        int(move_row.get("id") or 0): _pick_move_qty_value(move_row, qty_fields)
        for move_row in move_rows
        if int(move_row.get("id") or 0) > 0
    }
    zero_line_ids: list[int] = []
    filled_line_ids: list[int] = []
    for line_row in line_rows:
        line_id = int(line_row.get("id") or 0)
        move_id = m2o_id(line_row.get("move_id"))
        try:
            qty = float(line_row.get("quantity") or 0.0)
        except (TypeError, ValueError):
            qty = 0.0
        if line_id <= 0 or abs(qty) > 1e-9:
            continue
        zero_line_ids.append(line_id)
        fill_qty = float(qty_by_move.get(move_id, 0.0))
        if abs(fill_qty) <= 1e-9:
            continue
        ok = await client.write(
            "stock.return.picking.line",
            [line_id],
            {"quantity": fill_qty},
            context=wizard_context,
            stage="MANUAL_RETURN_WIZARD_FILL_QTY",
        )
        if ok:
            filled_line_ids.append(line_id)
    return {
        "line_count": len(line_ids),
        "zero_line_ids": zero_line_ids,
        "filled_line_ids": filled_line_ids,
    }


async def _run_return_wizard_method(
    client: Any,
    *,
    wizard_id: int,
    wizard_context: dict[str, Any],
) -> tuple[str, Any, list[str]]:
    attempts: list[str] = []
    for method_name in ("action_create_returns_all", "action_create_returns", "create_returns"):
        try:
            result = await client.execute_kw(
                "stock.return.picking",
                method_name,
                args=[[int(wizard_id)]],
                kwargs={"context": wizard_context},
                stage="MANUAL_RETURN_CREATE",
                mutating=True,
            )
            return method_name, result, attempts
        except Exception as exc:  # noqa: BLE001
            attempts.append(f"{method_name}: {exc}")
    detail = " | ".join(attempts) if attempts else "Tidak ada method create return yang tersedia."
    raise RuntimeError(f"Gagal membuat return via wizard: {detail}")


async def _resolve_return_picking_id_after_wizard(
    client: Any,
    *,
    original_picking_id: int,
    existing_return_ids: list[int],
    method_result: Any,
    context: dict[str, Any],
) -> int:
    if isinstance(method_result, dict):
        try:
            res_id = int(method_result.get("res_id") or 0)
        except (TypeError, ValueError):
            res_id = 0
        if res_id > 0:
            return res_id
    current_ids = await _search_return_ids(client, original_picking_id, context)
    new_ids = [item for item in current_ids if item not in existing_return_ids]
    if len(new_ids) == 1:
        return new_ids[0]
    if len(current_ids) == 1:
        return current_ids[0]
    raise RuntimeError(
        f"Tidak bisa menentukan return picking baru untuk original id={original_picking_id}. current_ids={current_ids}"
    )


async def _sync_stock_dates_for_picking(
    client: Any,
    *,
    date_service: DateSyncServiceAsync,
    picking_id: int,
    company_id: int,
    target_stock_datetime_utc: str,
    allow_preset_scheduled_date: bool,
) -> dict[str, Any]:
    capabilities = await date_service.initialize_capabilities()
    company_context = build_company_context(company_id)
    sync_context = build_company_context(company_id)
    sync_context["check_move_validity"] = False

    messages: list[str] = []
    warnings: list[str] = []
    row = await _read_picking_row(client, picking_id, company_context)
    current_state = normalize_text(row.get("state")).lower()

    if allow_preset_scheduled_date and current_state != "done":
        ok, err = await date_service._write_date_for_ids(  # noqa: SLF001
            model="stock.picking",
            ids=[int(picking_id)],
            field=capabilities.stock_picking_scheduled,
            company_id=company_id,
            date_done_utc=target_stock_datetime_utc,
            verify_only=False,
            context_override=company_context,
        )
        if ok:
            messages.append(f"stock.picking scheduled_date preset -> {target_stock_datetime_utc}")
        else:
            lower = normalize_text(err).lower()
            if "done" in lower or "cancel" in lower:
                warnings.append(f"Preset scheduled_date dilewati: {err}")
            else:
                raise RuntimeError(f"Gagal preset scheduled_date: {err}")

    if current_state != "done":
        ok, err = await date_service._run_action_assign(int(picking_id), company_context)  # noqa: SLF001
        if not ok:
            raise RuntimeError(err)
        ok, err = await date_service._enforce_done_qty_from_demand(int(picking_id), company_id, sync_context)  # noqa: SLF001
        if not ok:
            raise RuntimeError(err)
        ok, err, warn = await date_service._safe_validate_picking(int(picking_id), company_id, sync_context)  # noqa: SLF001
        if not ok:
            raise RuntimeError(err)
        if normalize_text(warn):
            warnings.append(warn)
        done, done_err = await date_service._ensure_picking_done(int(picking_id), company_context)  # noqa: SLF001
        if not done:
            raise RuntimeError(done_err)

    ok, err = await date_service._write_date_for_ids(  # noqa: SLF001
        model="stock.picking",
        ids=[int(picking_id)],
        field=capabilities.stock_picking_done,
        company_id=company_id,
        date_done_utc=target_stock_datetime_utc,
        verify_only=False,
    )
    if not ok:
        raise RuntimeError(f"Gagal sinkronisasi stock.picking.{capabilities.stock_picking_done.field_name}: {err}")
    messages.append(f"stock.picking done date (1 records) -> {target_stock_datetime_utc}")

    move_ids = await fetch_move_ids_for_picking(client, int(picking_id), company_context)
    if move_ids:
        ok, err = await date_service._write_date_for_ids(  # noqa: SLF001
            model="stock.move",
            ids=move_ids,
            field=capabilities.stock_move,
            company_id=company_id,
            date_done_utc=target_stock_datetime_utc,
            verify_only=False,
            context_override=sync_context,
        )
        if not ok:
            raise RuntimeError(f"Gagal sinkronisasi stock.move.{capabilities.stock_move.field_name}: {err}")
        messages.append(f"stock.move date ({len(move_ids)} records) -> {target_stock_datetime_utc}")

        line_ids = await client.search(
            "stock.move.line",
            [["move_id", "in", move_ids]],
            context=company_context,
            stage="MANUAL_SYNC_STOCK_MOVE_LINE_IDS",
        )
        if line_ids:
            ok, err = await date_service._write_date_for_ids(  # noqa: SLF001
                model="stock.move.line",
                ids=line_ids,
                field=capabilities.stock_move_line,
                company_id=company_id,
                date_done_utc=target_stock_datetime_utc,
                verify_only=False,
                context_override=sync_context,
            )
            if not ok:
                raise RuntimeError(f"Gagal sinkronisasi stock.move.line.{capabilities.stock_move_line.field_name}: {err}")
            messages.append(f"stock.move.line date ({len(line_ids)} records) -> {target_stock_datetime_utc}")

        if capabilities.stock_valuation_layer is not None:
            svl_ids = await client.search(
                "stock.valuation.layer",
                [["stock_move_id", "in", move_ids]],
                context=company_context,
                stage="MANUAL_SYNC_SVL_IDS",
            )
            if svl_ids:
                ok, err = await date_service._write_date_for_ids(  # noqa: SLF001
                    model="stock.valuation.layer",
                    ids=svl_ids,
                    field=capabilities.stock_valuation_layer,
                    company_id=company_id,
                    date_done_utc=target_stock_datetime_utc,
                    verify_only=False,
                    context_override=sync_context,
                )
                if not ok:
                    raise RuntimeError(
                        f"Gagal sinkronisasi stock.valuation.layer.{capabilities.stock_valuation_layer.field_name}: {err}"
                    )
                messages.append(
                    f"stock.valuation.layer {capabilities.stock_valuation_layer.field_name} ({len(svl_ids)} records) -> "
                    f"{target_stock_datetime_utc}"
                )

    return {
        "messages": messages,
        "warnings": warnings,
        "move_count": len(move_ids),
    }


async def _move_posted_journals_to_draft(client: Any, journal_ids: list[int], context: dict[str, Any]) -> str:
    attempts: list[str] = []
    for method_name in ("button_draft", "action_draft"):
        try:
            await batch_method(client, "account.move", method_name, journal_ids, context)
        except Exception as exc:  # noqa: BLE001
            attempts.append(f"{method_name}: {exc}")
            continue
        verify_rows = await read_journal_rows(client, journal_ids, context)
        if all(normalize_text(row.get("state")).lower() != "posted" for row in verify_rows):
            return method_name
        attempts.append(f"{method_name}: posted journals masih tersisa")
    detail = " | ".join(attempts) if attempts else "Metode draft tidak tersedia."
    raise RuntimeError(f"Gagal memindahkan STJ posted ke draft: {detail}")


async def _sync_journal_dates_for_picking(
    client: Any,
    *,
    picking_name: str,
    target_local_date: str,
) -> dict[str, Any]:
    related = await fetch_related_journals_for_picking(client, picking_name)
    journal_rows = related["journal_rows"]
    journal_ids = [int(row["id"]) for row in journal_rows if int(row.get("id") or 0) > 0]
    result: dict[str, Any] = {
        "picking": picking_name,
        "journal_count": len(journal_ids),
        "posted_before_count": 0,
        "draft_before_count": 0,
        "before_date_counts": {},
        "before_name_sample_first_5": [],
        "after_date_counts": {},
        "after_state_counts": {},
        "after_name_sample_first_5": [],
        "renamed_count": 0,
        "renamed_sample_first_5": [],
        "status": "done",
    }
    if not journal_ids:
        result["status"] = "no_journal"
        return result

    before_name_map = {int(row["id"]): normalize_text(row.get("name")) for row in journal_rows if row.get("id")}
    result["posted_before_count"] = sum(1 for row in journal_rows if normalize_text(row.get("state")).lower() == "posted")
    result["draft_before_count"] = len(journal_rows) - result["posted_before_count"]
    for row in journal_rows:
        date_text = normalize_text(row.get("date")) or "(blank)"
        result["before_date_counts"][date_text] = result["before_date_counts"].get(date_text, 0) + 1
    result["before_name_sample_first_5"] = [normalize_text(row.get("name")) for row in journal_rows[:5]]

    context = related["context"]
    posted_ids = [int(row["id"]) for row in journal_rows if normalize_text(row.get("state")).lower() == "posted"]
    draft_ids = [int(row["id"]) for row in journal_rows if normalize_text(row.get("state")).lower() != "posted"]

    if posted_ids:
        draft_method = await _move_posted_journals_to_draft(client, posted_ids, context)
        await batch_write(client, "account.move", posted_ids, {"date": target_local_date, "name": "/"}, context)
        await batch_method(client, "account.move", "action_post", posted_ids, context)
        result["draft_method"] = draft_method
    if draft_ids:
        await batch_write(client, "account.move", draft_ids, {"date": target_local_date}, context)

    after_rows = await read_journal_rows(client, journal_ids, context)
    after_name_map = {int(row["id"]): normalize_text(row.get("name")) for row in after_rows if row.get("id")}
    for row in after_rows:
        date_text = normalize_text(row.get("date")) or "(blank)"
        state_text = normalize_text(row.get("state")) or "(blank)"
        result["after_date_counts"][date_text] = result["after_date_counts"].get(date_text, 0) + 1
        result["after_state_counts"][state_text] = result["after_state_counts"].get(state_text, 0) + 1
    result["after_name_sample_first_5"] = [normalize_text(row.get("name")) for row in after_rows[:5]]

    renamed_sample: list[dict[str, Any]] = []
    renamed_count = 0
    for journal_id in journal_ids:
        before_name = before_name_map.get(journal_id, "")
        after_name = after_name_map.get(journal_id, "")
        if before_name != after_name:
            renamed_count += 1
            if len(renamed_sample) < 5:
                renamed_sample.append({"id": journal_id, "before": before_name, "after": after_name})
    result["renamed_count"] = renamed_count
    result["renamed_sample_first_5"] = renamed_sample
    result["mismatch_count"] = sum(
        1 for row in after_rows if normalize_text(row.get("date")) != target_local_date
    )
    result["mismatch_sample_first_5"] = [
        {
            "id": int(row.get("id") or 0),
            "date": normalize_text(row.get("date")),
            "state": normalize_text(row.get("state")),
            "name": normalize_text(row.get("name")),
        }
        for row in after_rows
        if normalize_text(row.get("date")) != target_local_date
    ][:5]
    return result


async def _run_apply_for_entry(
    client: Any,
    *,
    entry: dict[str, Any],
    date_service: DateSyncServiceAsync,
    qty_fields: list[str],
) -> dict[str, Any]:
    company_id = m2o_id(entry.get("company"))
    company_context = build_company_context(company_id)
    result: dict[str, Any] = {
        "name": entry["name"],
        "original_picking_id": int(entry["picking_id"]),
        "target_local_date": entry["target_local_date"],
        "target_stock_datetime_utc": entry["target_stock_datetime_utc"],
        "started_at_epoch": time.time(),
    }

    existing_return_ids = _safe_int_list(entry.get("existing_return_ids"))
    if len(existing_return_ids) == 1:
        return_picking_id = existing_return_ids[0]
        result["reused_existing_return"] = True
        result["return_picking_id"] = return_picking_id
    else:
        wizard_id, wizard_info = await _create_return_wizard(
            client,
            original_picking_id=int(entry["picking_id"]),
            company_id=company_id,
        )
        result["wizard_id"] = wizard_id
        fill_info = await _fill_zero_return_line_qty(
            client,
            wizard_id=wizard_id,
            wizard_context=wizard_info["context"],
            qty_fields=qty_fields,
        )
        result["wizard_return_line_count"] = int(fill_info["line_count"])
        result["wizard_zero_line_count"] = len(fill_info["zero_line_ids"])
        result["wizard_filled_zero_line_count"] = len(fill_info["filled_line_ids"])
        method_name, method_result, method_attempts = await _run_return_wizard_method(
            client,
            wizard_id=wizard_id,
            wizard_context=wizard_info["context"],
        )
        result[method_name] = method_result
        if method_attempts:
            result["method_attempt_failures"] = method_attempts
        return_picking_id = await _resolve_return_picking_id_after_wizard(
            client,
            original_picking_id=int(entry["picking_id"]),
            existing_return_ids=existing_return_ids,
            method_result=method_result,
            context=company_context,
        )
        result["return_picking_id"] = return_picking_id

    before_row = await _read_picking_row(client, int(return_picking_id), company_context)
    result["return_before_validate"] = before_row

    stock_sync = await _sync_stock_dates_for_picking(
        client,
        date_service=date_service,
        picking_id=int(return_picking_id),
        company_id=company_id,
        target_stock_datetime_utc=entry["target_stock_datetime_utc"],
        allow_preset_scheduled_date=True,
    )
    result["stock_sync"] = stock_sync

    after_row = await _read_picking_row(client, int(return_picking_id), company_context)
    result["return_after_sync"] = after_row
    original_after = await _read_picking_row(client, int(entry["picking_id"]), company_context)
    result["original_after_return"] = original_after

    journal_sync = await _sync_journal_dates_for_picking(
        client,
        picking_name=normalize_text(after_row.get("name")) or entry["name"],
        target_local_date=entry["target_local_date"],
    )
    result["journal_sync"] = journal_sync
    result["status"] = "done"
    result["finished_at_epoch"] = time.time()
    return result


async def amain() -> int:
    args = parse_args()
    if normalize_text(args.target_local_date):
        try:
            date.fromisoformat(args.target_local_date)
        except ValueError as exc:
            raise SystemExit("--target-local-date must be YYYY-MM-DD") from exc

    requested_pickings: list[str] = []
    for picking_name in args.pickings:
        clean = normalize_text(picking_name)
        if clean and clean not in requested_pickings:
            requested_pickings.append(clean)
    if not requested_pickings:
        raise SystemExit("At least one --picking is required.")

    conn = await open_manual_connection(args)
    try:
        qty_fields = await _stock_move_qty_field_candidates(conn.client)
        precheck_entries: list[dict[str, Any]] = []
        for picking_name in requested_pickings:
            precheck_entries.append(
                await _build_precheck_entry(
                    conn.client,
                    picking_name=picking_name,
                    company_id_expected=int(args.company_id or 0),
                    explicit_target_date=normalize_text(args.target_local_date),
                    settings=conn.settings,
                    qty_fields=qty_fields,
                )
            )

        dry_run_payload = {
            "mode": "dry_run",
            "database": conn.config.database,
            "company_id": int(args.company_id or 0),
            "pickings": precheck_entries,
        }
        if not args.apply:
            print(json.dumps(dry_run_payload, ensure_ascii=False, indent=2, default=str))
            path = write_manual_artifact(dry_run_payload, args, "return_internal_transfers_dry_run")
            if path:
                print(f"Artefact: {path}")
            return 0

        blocked = [entry for entry in precheck_entries if normalize_text(entry.get("status")) == "blocked"]
        if blocked:
            raise RuntimeError(f"Precheck blocked. Review pickings: {[item['name'] for item in blocked]}")

        date_service = DateSyncServiceAsync(conn.client, conn.settings, conn.logger)
        apply_payload: dict[str, Any] = {
            "mode": "apply",
            "database": conn.config.database,
            "company_id": int(args.company_id or 0),
            "requested_pickings": requested_pickings,
            "results": [],
        }
        for entry in precheck_entries:
            apply_payload["results"].append(
                await _run_apply_for_entry(
                    conn.client,
                    entry=entry,
                    date_service=date_service,
                    qty_fields=qty_fields,
                )
            )

        print(json.dumps(apply_payload, ensure_ascii=False, indent=2, default=str))
        path = write_manual_artifact(apply_payload, args, "return_internal_transfers_apply")
        if path:
            print(f"Artefact: {path}")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(amain()))
