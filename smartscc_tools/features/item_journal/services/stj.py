"""Async STJ mapping service for Item Journal."""

from __future__ import annotations

import asyncio
import inspect
import logging
import re
import time
from typing import Any, Dict, Iterable, List, Set, Tuple

from smartscc_tools.features.item_journal.config import RuntimeSettings
from smartscc_tools.services.odoo.gateway import AsyncOdooJsonRpcClient, build_company_context, extract_many2one_id
from smartscc_tools.features.item_journal.workbook import ItemJournalRow
from smartscc_tools.features.item_journal.utils import append_error, compute_hybrid_delay_ms, normalize_text, to_float, to_int


ID_TOKEN_RE = re.compile(r"\[ID:([^\]]+)\]", re.IGNORECASE)


def _append_stj_value(existing: str, candidate: str) -> str:
    value = normalize_text(candidate)
    if not value or value == "/":
        return existing
    parts = [item.strip() for item in existing.split(";") if item.strip()]
    for item in parts:
        if item.lower() == value.lower():
            return existing
    if not parts:
        return value
    return existing + ";" + value


def _sorted_unique_ints(values: Iterable[int]) -> List[int]:
    return sorted({int(v) for v in values if int(v) > 0})


def _extract_many2one_name(value: Any) -> str:
    if isinstance(value, list) and len(value) >= 2:
        return normalize_text(value[1])
    if isinstance(value, tuple) and len(value) >= 2:
        return normalize_text(value[1])
    return ""


def _normalize_origin_mode(settings: RuntimeSettings) -> str:
    mode = normalize_text(getattr(settings, "create_origin_mode", "technical")).lower()
    if mode == "human_suffix":
        human_ref = normalize_text(getattr(settings, "create_origin_human_ref", ""))
        if human_ref:
            return "human_suffix"
    return "technical"


class StjServiceAsync:
    def __init__(
        self,
        rpc: AsyncOdooJsonRpcClient,
        logger: logging.Logger,
        settings: RuntimeSettings | None = None,
    ) -> None:
        self.rpc = rpc
        self.logger = logger
        self.settings = settings or RuntimeSettings()
        self.last_meta: Dict[str, Any] = {}
        self._picking_has_account_move_ids: bool | None = None
        self._move_has_account_move_ids: bool | None = None

    async def fill_stj_for_group(
        self,
        pick_id: int,
        company_id: int,
        target_rows: List[ItemJournalRow],
        row_specs: Dict[int, Dict[str, Any]] | None = None,
        origin_scope_key: str = "",
    ) -> str:
        attempts = max(1, int(getattr(self.settings, "stj_poll_retry_count", 1) or 1))
        base_delay_ms = max(0, int(getattr(self.settings, "stj_poll_retry_delay_ms", 0) or 0))
        fast_attempts = max(1, int(getattr(self.settings, "stj_poll_fast_attempts", 1) or 1))
        max_delay_ms = max(base_delay_ms, int(getattr(self.settings, "stj_poll_max_delay_ms", base_delay_ms) or base_delay_ms))
        last_warning = ""
        last_missing = 0
        started_at = time.monotonic()

        self.last_meta = {
            "row_count": len(target_rows),
            "move_count": 0,
            "assigned_rows": 0,
            "missing_rows": len(target_rows),
            "scope_pick_ids": [int(pick_id or 0)] if int(pick_id or 0) > 0 else [],
            "scope_source": "none",
            "remap_applied": False,
            "remap_failed": False,
            "remap_message": "",
        }

        for attempt in range(1, attempts + 1):
            warning, missing_count, _assign_count, meta = await self._dispatch_fill_once(
                pick_id=pick_id,
                company_id=company_id,
                target_rows=target_rows,
                row_specs=row_specs or {},
                origin_scope_key=origin_scope_key,
            )
            if isinstance(meta, dict):
                self.last_meta = dict(meta)
            if missing_count <= 0:
                if attempt > 1 and warning:
                    return append_error(warning, f"STJ ditemukan setelah retry attempt={attempt}/{attempts}.")
                return warning

            last_warning = warning
            last_missing = missing_count
            next_delay_ms = compute_hybrid_delay_ms(
                next_attempt=attempt + 1,
                base_delay_ms=base_delay_ms,
                fast_attempts=fast_attempts,
                max_delay_ms=max_delay_ms,
            )
            if attempt < attempts and next_delay_ms > 0:
                elapsed_ms = int(max(time.monotonic() - started_at, 0.0) * 1000.0)
                self.logger.warning(
                    "STJ polling retry (picking=%s) attempt=%s/%s rows_stj_empty=%s next_delay_ms=%s elapsed_ms=%s",
                    pick_id,
                    attempt + 1,
                    attempts,
                    missing_count,
                    next_delay_ms,
                    elapsed_ms,
                )
                await asyncio.sleep(next_delay_ms / 1000.0)

        return append_error(
            last_warning,
            f"STJ polling habis (attempt={attempts}, rows_stj_empty={last_missing}).",
        )

    async def _dispatch_fill_once(
        self,
        pick_id: int,
        company_id: int,
        target_rows: List[ItemJournalRow],
        row_specs: Dict[int, Dict[str, Any]],
        origin_scope_key: str,
    ) -> tuple[str, int, int, Dict[str, Any]]:
        fn = self._fill_stj_for_group_once
        signature = inspect.signature(fn)
        supports_kwargs = "row_specs" in signature.parameters and "origin_scope_key" in signature.parameters
        if supports_kwargs:
            result = await fn(
                pick_id=pick_id,
                company_id=company_id,
                target_rows=target_rows,
                row_specs=row_specs,
                origin_scope_key=origin_scope_key,
            )
        else:
            legacy = await fn(pick_id, company_id, target_rows)  # type: ignore[misc]
            if isinstance(legacy, tuple) and len(legacy) == 3:
                warning, missing_count, assign_count = legacy
                return (
                    str(warning),
                    int(missing_count),
                    int(assign_count),
                    {
                        "row_count": len(target_rows),
                        "move_count": 0,
                        "assigned_rows": int(assign_count),
                        "missing_rows": int(missing_count),
                        "scope_pick_ids": [int(pick_id or 0)] if int(pick_id or 0) > 0 else [],
                        "scope_source": "legacy",
                        "remap_applied": False,
                        "remap_failed": False,
                        "remap_message": "",
                    },
                )
            raise RuntimeError("Unexpected legacy STJ fill return shape.")

        if isinstance(result, tuple) and len(result) == 4:
            warning, missing_count, assign_count, meta = result
            return str(warning), int(missing_count), int(assign_count), dict(meta or {})
        if isinstance(result, tuple) and len(result) == 3:
            warning, missing_count, assign_count = result
            return (
                str(warning),
                int(missing_count),
                int(assign_count),
                {
                    "row_count": len(target_rows),
                    "move_count": 0,
                    "assigned_rows": int(assign_count),
                    "missing_rows": int(missing_count),
                    "scope_pick_ids": [int(pick_id or 0)] if int(pick_id or 0) > 0 else [],
                    "scope_source": "legacy",
                    "remap_applied": False,
                    "remap_failed": False,
                    "remap_message": "",
                },
            )
        raise RuntimeError("Unexpected STJ fill return shape.")

    async def _fill_stj_for_group_once(
        self,
        pick_id: int,
        company_id: int,
        target_rows: List[ItemJournalRow],
        row_specs: Dict[int, Dict[str, Any]] | None = None,
        origin_scope_key: str = "",
    ) -> tuple[str, int, int, Dict[str, Any]]:
        active_rows = [row for row in target_rows if row.result != "[Error]"]
        meta: Dict[str, Any] = {
            "row_count": len(active_rows),
            "move_count": 0,
            "assigned_rows": 0,
            "missing_rows": 0,
            "scope_pick_ids": [int(pick_id or 0)] if int(pick_id or 0) > 0 else [],
            "scope_source": "root_only",
            "scope_phase": "root_done",
            "scope_phases": [],
            "remap_applied": False,
            "remap_failed": False,
            "remap_message": "",
        }
        if pick_id <= 0 or not active_rows:
            return "", 0, 0, meta

        for row in active_rows:
            row.stj = ""

        normalized_specs = self._build_row_specs(active_rows, row_specs or {})
        remap_mode = normalize_text(getattr(self.settings, "stj_remap_mode", "conservative")).lower() or "conservative"
        phase_definitions = [
            ("root_done", False),
            ("origin_done", True),
            ("root_fallback_done", False),
        ]
        phase_meta: List[Dict[str, Any]] = []
        last_missing_move_warning = ""
        last_remap_failure = ""

        for phase_name, include_origin_scope in phase_definitions:
            scope_pick_ids, picking_names, scope_source = await self._resolve_scope_pickings(
                root_pick_id=pick_id,
                company_id=company_id,
                origin_scope_key=origin_scope_key,
                include_origin_scope=include_origin_scope,
            )
            phase_scope_ids = _sorted_unique_ints(scope_pick_ids)
            if not phase_scope_ids:
                phase_scope_ids = [int(pick_id)]
            phase_scope_source = f"{scope_source}|phase={phase_name}"
            meta["scope_pick_ids"] = phase_scope_ids[:]
            meta["scope_source"] = phase_scope_source
            meta["scope_phase"] = phase_name
            phase_entry: Dict[str, Any] = {
                "scope_phase": phase_name,
                "scope_pick_ids": phase_scope_ids[:],
                "scope_source": phase_scope_source,
                "move_count": 0,
            }
            phase_meta.append(phase_entry)
            meta["scope_phases"] = [dict(item) for item in phase_meta]

            move_rows = await self._fetch_scope_moves(
                scope_pick_ids=phase_scope_ids,
                company_id=company_id,
                move_state="done",
            )
            move_ids = [int(item.get("id") or 0) for item in move_rows if int(item.get("id") or 0) > 0]
            phase_entry["move_count"] = len(move_ids)
            meta["move_count"] = len(move_ids)
            if not move_ids:
                last_missing_move_warning = append_error(
                    last_missing_move_warning,
                    f"STJ phase {phase_name}: tidak ada stock.move done (picking={pick_id}).",
                )
                continue

            stj_by_picking = await self._collect_stj_by_picking(
                pick_ids=phase_scope_ids,
                company_id=company_id,
            )
            stj_by_move_relation = await self._collect_stj_by_move_account_move_ids(
                move_ids=move_ids,
                company_id=company_id,
            )
            stj_by_move = await self._collect_stj_by_move(move_ids=move_ids, company_id=company_id)
            assignments: Dict[int, Dict[str, int]] = {}
            warning = ""
            if remap_mode == "conservative":
                remap_ok, remap_message, assignments = self._conservative_remap(
                    rows=active_rows,
                    row_specs=normalized_specs,
                    move_rows=move_rows,
                )
                scoped_remap_message = f"scope_phase={phase_name} {remap_message}"
                if not remap_ok:
                    last_remap_failure = (
                        f"STJ remap gagal (picking={pick_id}, scope_phase={phase_name}): {remap_message}"
                    )
                    continue
                meta["remap_applied"] = True
                meta["remap_message"] = scoped_remap_message
            else:
                warning, assignments = self._legacy_assign(
                    root_pick_id=pick_id,
                    rows=active_rows,
                    move_rows=move_rows,
                )

            missing_count = 0
            assign_count = 0
            source_pick_count = 0
            source_move_relation_count = 0
            source_move_count = 0
            missing_sample: List[str] = []
            non_empty_count = 0
            result_mode = normalize_text(getattr(self.settings, "stj_result_picking_mode", "actual")).lower() or "actual"
            sorted_rows = sorted(active_rows, key=lambda item: int(item.row_number or 0))

            for row in sorted_rows:
                assignment = assignments.get(int(row.row_number or 0))
                if not assignment:
                    row.stj = ""
                    missing_count += 1
                    if len(missing_sample) < 10:
                        missing_sample.append(f"row={row.row_number}")
                    continue

                assign_count += 1
                move_id = int(assignment.get("move_id") or 0)
                assigned_pick_id = int(assignment.get("picking_id") or 0)
                stj_from_pick = normalize_text(stj_by_picking.get(assigned_pick_id, ""))
                stj_from_move_relation = normalize_text(stj_by_move_relation.get(move_id, ""))
                stj_from_move = normalize_text(stj_by_move.get(move_id, ""))
                if stj_from_pick:
                    source_pick_count += 1
                if stj_from_move_relation:
                    source_move_relation_count += 1
                if stj_from_move:
                    source_move_count += 1
                if stj_from_pick:
                    stj_value = _append_stj_value(stj_from_pick, stj_from_move_relation)
                    stj_value = _append_stj_value(stj_value, stj_from_move)
                else:
                    stj_value = _append_stj_value(stj_from_move_relation, stj_from_move)
                row.stj = stj_value
                if stj_value:
                    non_empty_count += 1
                else:
                    missing_count += 1
                    if len(missing_sample) < 10:
                        missing_sample.append(f"move_id={move_id}")
                if result_mode == "actual":
                    if assigned_pick_id > 0:
                        pick_name = normalize_text(picking_names.get(assigned_pick_id))
                        row.result = pick_name or str(assigned_pick_id)

            if assign_count != len(active_rows):
                warning = append_error(
                    warning,
                    (
                        f"STJ mapping mismatch (picking={pick_id}, scope_phase={phase_name}): "
                        f"row_sukses={len(active_rows)}, move_count={len(move_rows)}, assigned={assign_count}"
                    ),
                )

            if missing_count > 0:
                warning = append_error(
                    warning,
                    (
                        f"STJ summary (picking={pick_id}, scope_phase={phase_name}): moves_total={len(move_rows)}, "
                        f"rows_assigned={assign_count}, rows_stj_empty={missing_count}, "
                        f"sample={','.join(missing_sample) if missing_sample else '-'}"
                    ),
                )
            if assign_count > 0 and non_empty_count == 0:
                warning = append_error(
                    warning,
                    f"STJ tidak ditemukan untuk semua baris assign (picking={pick_id}, scope_phase={phase_name}).",
                )

            meta["assigned_rows"] = assign_count
            meta["missing_rows"] = missing_count
            source_tokens: List[str] = []
            if source_pick_count > 0:
                source_tokens.append("account_move_ids")
            if source_move_relation_count > 0:
                source_tokens.append("move_account_move_ids")
            if source_move_count > 0:
                source_tokens.append("move_mapping_fallback")
            meta["journal_source"] = "+".join(source_tokens) if source_tokens else "none"
            return warning, missing_count, assign_count, meta

        if last_remap_failure:
            for row in active_rows:
                row.mark_error(last_remap_failure)
            meta["remap_failed"] = True
            meta["remap_message"] = last_remap_failure
            meta["assigned_rows"] = 0
            meta["missing_rows"] = 0
            return last_remap_failure, 0, 0, meta

        warning = append_error(
            last_missing_move_warning,
            f"Tidak ada stock.move done untuk STJ (picking={pick_id}).",
        )
        meta["assigned_rows"] = 0
        meta["missing_rows"] = len(active_rows)
        meta["journal_source"] = "none"
        return warning, len(active_rows), 0, meta

    async def _collect_stj_by_picking(
        self,
        pick_ids: List[int],
        company_id: int,
    ) -> Dict[int, str]:
        clean_pick_ids = _sorted_unique_ints(pick_ids)
        if not clean_pick_ids:
            return {}
        if not await self._supports_picking_account_move_ids(company_id):
            return {}

        context = build_company_context(company_id)
        try:
            pick_rows = await self.rpc.read(
                model="stock.picking",
                ids=clean_pick_ids,
                fields=["id", "account_move_ids"],
                context=context,
                stage="STJ_PICKING_ACCOUNT_MOVE",
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("Gagal membaca stock.picking.account_move_ids: %s", exc)
            return {}

        pick_to_move_ids: Dict[int, List[int]] = {}
        all_move_ids: Set[int] = set()
        for rec in pick_rows:
            pick_id = int(rec.get("id") or 0)
            if pick_id <= 0:
                continue
            move_ids_raw = rec.get("account_move_ids")
            if not isinstance(move_ids_raw, list):
                continue
            move_ids = _sorted_unique_ints([to_int(item) for item in move_ids_raw])
            if not move_ids:
                continue
            pick_to_move_ids[pick_id] = move_ids
            for move_id in move_ids:
                all_move_ids.add(move_id)

        if not all_move_ids:
            return {}

        move_rows = await self.rpc.read(
            model="account.move",
            ids=_sorted_unique_ints(all_move_ids),
            fields=["id", "name"],
            context=context,
            stage="STJ_PICKING_ACCOUNT_MOVE_READ",
        )
        name_by_move_id: Dict[int, str] = {}
        for rec in move_rows:
            move_id = int(rec.get("id") or 0)
            if move_id <= 0:
                continue
            name_by_move_id[move_id] = normalize_text(rec.get("name"))

        stj_by_picking: Dict[int, str] = {}
        for pick_id, move_ids in pick_to_move_ids.items():
            stj_value = ""
            for move_id in move_ids:
                stj_value = _append_stj_value(stj_value, name_by_move_id.get(move_id, ""))
            if stj_value:
                stj_by_picking[pick_id] = stj_value
        return stj_by_picking

    async def _supports_picking_account_move_ids(self, company_id: int) -> bool:
        if self._picking_has_account_move_ids is not None:
            return self._picking_has_account_move_ids
        context = build_company_context(company_id)
        try:
            meta = await self.rpc.fields_get(
                model="stock.picking",
                attributes=["type", "relation"],
                context=context,
                stage="STJ_PICKING_ACCOUNT_MOVE_PREFLIGHT",
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("Gagal fields_get stock.picking: %s", exc)
            self._picking_has_account_move_ids = False
            return False

        field_meta = meta.get("account_move_ids", {})
        relation = normalize_text(field_meta.get("relation")).lower()
        field_type = normalize_text(field_meta.get("type")).lower()
        self._picking_has_account_move_ids = field_type == "many2many" and relation == "account.move"
        return self._picking_has_account_move_ids

    def _build_row_specs(
        self,
        rows: List[ItemJournalRow],
        row_specs: Dict[int, Dict[str, Any]],
    ) -> Dict[int, Dict[str, Any]]:
        normalized: Dict[int, Dict[str, Any]] = {}
        for row in rows:
            row_number = int(row.row_number or 0)
            if row_number <= 0:
                continue
            spec = dict(row_specs.get(row_number, {}))
            product_id = to_int(spec.get("product_id"))
            if product_id <= 0:
                product_id = to_int(row.prod_id_text)
            normalized[row_number] = {
                "row_number": row_number,
                "product_id": product_id,
                "uom_id": to_int(spec.get("uom_id")),
                "src_loc_id": to_int(spec.get("src_loc_id")),
                "dest_loc_id": to_int(spec.get("dest_loc_id")),
                "qty": to_float(spec.get("qty") if "qty" in spec else row.qty),
            }
        return normalized

    async def _resolve_scope_pickings(
        self,
        root_pick_id: int,
        company_id: int,
        origin_scope_key: str,
        include_origin_scope: bool | None = None,
    ) -> tuple[List[int], Dict[int, str], str]:
        context = build_company_context(company_id)
        picking_names: Dict[int, str] = {}
        root_rows = await self.rpc.read(
            model="stock.picking",
            ids=[root_pick_id],
            fields=["id", "name", "origin", "state", "company_id"],
            context=context,
            stage="STJ_SCOPE_ROOT",
        )
        root_origin = ""
        if root_rows:
            root = root_rows[0]
            rid = int(root.get("id") or root_pick_id)
            picking_names[rid] = normalize_text(root.get("name")) or str(rid)
            root_origin = normalize_text(root.get("origin"))
        else:
            picking_names[root_pick_id] = str(root_pick_id)

        include_scope = (
            bool(getattr(self.settings, "stj_include_origin_scope", True))
            if include_origin_scope is None
            else bool(include_origin_scope)
        )
        if not include_scope:
            return [root_pick_id], picking_names, "root_only"

        origin_mode = _normalize_origin_mode(self.settings)
        lookup_mode = origin_mode
        lookup_key = ""
        lookup_token = ""
        clean_scope_key = normalize_text(origin_scope_key)
        if origin_mode == "human_suffix":
            if clean_scope_key:
                lookup_token = f"[ID:{clean_scope_key}]"
                lookup_key = clean_scope_key
            else:
                match = ID_TOKEN_RE.search(root_origin)
                if match:
                    lookup_key = normalize_text(match.group(1))
                    if lookup_key:
                        lookup_token = f"[ID:{lookup_key}]"
        else:
            lookup_key = clean_scope_key or root_origin

        if origin_mode == "human_suffix" and not lookup_token:
            return [root_pick_id], picking_names, "root_fallback_missing_origin"
        if origin_mode != "human_suffix" and not lookup_key:
            return [root_pick_id], picking_names, "root_fallback_missing_origin"

        if origin_mode == "human_suffix":
            domain = [
                ["company_id", "=", company_id],
                ["state", "!=", "cancel"],
                ["origin", "ilike", lookup_token],
            ]
        else:
            domain = [
                ["company_id", "=", company_id],
                ["state", "!=", "cancel"],
                ["origin", "=", lookup_key],
            ]

        candidates = await self.rpc.search_read(
            model="stock.picking",
            domain=domain,
            fields=["id", "name", "state", "origin", "company_id"],
            order="id asc",
            limit=200,
            context=context,
            stage="STJ_SCOPE_PICKINGS",
        )
        pick_ids: Set[int] = {int(root_pick_id)}
        for rec in candidates:
            rec_id = int(rec.get("id") or 0)
            if rec_id <= 0:
                continue
            company_match = extract_many2one_id(rec.get("company_id"))
            if company_match > 0 and company_match != company_id:
                continue
            state = normalize_text(rec.get("state")).lower()
            if state == "cancel":
                continue
            origin_value = normalize_text(rec.get("origin"))
            if origin_mode == "human_suffix":
                if lookup_token.lower() not in origin_value.lower():
                    continue
            elif origin_value != lookup_key:
                continue
            pick_ids.add(rec_id)
            picking_names[rec_id] = normalize_text(rec.get("name")) or str(rec_id)

        if root_pick_id not in pick_ids:
            pick_ids.add(root_pick_id)
        if root_pick_id not in picking_names:
            picking_names[root_pick_id] = str(root_pick_id)

        sorted_pick_ids = sorted(pick_ids)
        if len(sorted_pick_ids) <= 1:
            return sorted_pick_ids, picking_names, "root_only"
        return sorted_pick_ids, picking_names, f"origin_scope:{lookup_mode}"

    async def _fetch_scope_moves(
        self,
        scope_pick_ids: List[int],
        company_id: int,
        move_state: str = "non_cancel",
    ) -> List[Dict[str, Any]]:
        if not scope_pick_ids:
            return []
        context = build_company_context(company_id)
        state_mode = normalize_text(move_state).lower() or "non_cancel"
        if state_mode == "done":
            state_clause: List[Any] = ["state", "=", "done"]
        else:
            state_clause = ["state", "!=", "cancel"]
        move_rows = await self.rpc.search_read(
            model="stock.move",
            domain=[["picking_id", "in", scope_pick_ids], state_clause],
            fields=["id", "picking_id", "product_id", "product_uom", "product_uom_qty", "location_id", "location_dest_id"],
            order="picking_id asc,id asc",
            context=context,
            stage="STJ_FETCH_SCOPE_MOVES",
        )
        normalized: List[Dict[str, Any]] = []
        for rec in move_rows:
            move_id = int(rec.get("id") or 0)
            if move_id <= 0:
                continue
            picking_id = extract_many2one_id(rec.get("picking_id"))
            if picking_id <= 0:
                continue
            normalized.append(
                {
                    "id": move_id,
                    "picking_id": picking_id,
                    "product_id": extract_many2one_id(rec.get("product_id")),
                    "uom_id": extract_many2one_id(rec.get("product_uom")),
                    "src_loc_id": extract_many2one_id(rec.get("location_id")),
                    "dest_loc_id": extract_many2one_id(rec.get("location_dest_id")),
                    "qty": to_float(rec.get("product_uom_qty")),
                }
            )
        normalized.sort(key=lambda item: (int(item.get("picking_id") or 0), int(item.get("id") or 0)))
        return normalized

    async def _collect_stj_by_move_account_move_ids(
        self,
        move_ids: List[int],
        company_id: int,
    ) -> Dict[int, str]:
        clean_move_ids = _sorted_unique_ints(move_ids)
        if not clean_move_ids:
            return {}
        if not await self._supports_move_account_move_ids(company_id):
            return {}

        context = build_company_context(company_id)
        try:
            move_rows = await self.rpc.read(
                model="stock.move",
                ids=clean_move_ids,
                fields=["id", "account_move_ids"],
                context=context,
                stage="STJ_MOVE_ACCOUNT_MOVE",
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("Gagal membaca stock.move.account_move_ids: %s", exc)
            return {}

        move_to_account_ids: Dict[int, List[int]] = {}
        all_account_ids: Set[int] = set()
        for rec in move_rows:
            move_id = int(rec.get("id") or 0)
            if move_id <= 0:
                continue
            raw = rec.get("account_move_ids")
            if not isinstance(raw, list):
                continue
            account_ids = _sorted_unique_ints([to_int(item) for item in raw])
            if not account_ids:
                continue
            move_to_account_ids[move_id] = account_ids
            all_account_ids.update(account_ids)

        if not all_account_ids:
            return {}

        acc_rows = await self.rpc.read(
            model="account.move",
            ids=_sorted_unique_ints(all_account_ids),
            fields=["id", "name"],
            context=context,
            stage="STJ_MOVE_ACCOUNT_MOVE_READ",
        )
        name_by_id: Dict[int, str] = {}
        for rec in acc_rows:
            account_id = int(rec.get("id") or 0)
            if account_id <= 0:
                continue
            name_by_id[account_id] = normalize_text(rec.get("name"))

        stj_by_move: Dict[int, str] = {}
        for move_id, account_ids in move_to_account_ids.items():
            stj_value = ""
            for account_id in account_ids:
                stj_value = _append_stj_value(stj_value, name_by_id.get(account_id, ""))
            if stj_value:
                stj_by_move[move_id] = stj_value
        return stj_by_move

    async def _supports_move_account_move_ids(self, company_id: int) -> bool:
        if self._move_has_account_move_ids is not None:
            return self._move_has_account_move_ids

        context = build_company_context(company_id)
        try:
            meta = await self.rpc.fields_get(
                model="stock.move",
                attributes=["type", "relation"],
                context=context,
                stage="STJ_MOVE_ACCOUNT_MOVE_PREFLIGHT",
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("Gagal fields_get stock.move: %s", exc)
            self._move_has_account_move_ids = False
            return False

        field_meta = meta.get("account_move_ids", {})
        relation = normalize_text(field_meta.get("relation")).lower()
        field_type = normalize_text(field_meta.get("type")).lower()
        self._move_has_account_move_ids = field_type == "many2many" and relation == "account.move"
        return self._move_has_account_move_ids

    async def _collect_stj_by_move(
        self,
        move_ids: List[int],
        company_id: int,
    ) -> Dict[int, str]:
        context = build_company_context(company_id)
        relation_model = await self._resolve_aml_move_relation(company_id)

        direct_stj_by_move: Dict[int, str] = {}
        all_aml_ids: Set[int] = set()
        move_to_aml_ids: Dict[int, Set[int]] = {}
        move_to_account_move_ids: Dict[int, Set[int]] = {}
        all_account_move_ids: Set[int] = set()
        account_move_meta: Dict[int, Dict[str, Any]] = {}

        if relation_model == "stock.move":
            aml_rows = await self.rpc.search_read(
                model="account.move.line",
                domain=[["move_id", "in", move_ids]],
                fields=["id", "move_name", "move_id"],
                context=context,
                stage="STJ_AML_DIRECT",
            )
        else:
            account_move_rows = await self.rpc.search_read(
                model="account.move",
                domain=[["stock_move_id", "in", move_ids]],
                fields=["id", "name", "stock_move_id"],
                context=context,
                stage="STJ_ACC_MOVE_BY_STOCK_MOVE",
            )
            account_move_ids: List[int] = []
            for rec in account_move_rows:
                move_id = extract_many2one_id(rec.get("stock_move_id"))
                acc_id = int(rec.get("id") or 0)
                if acc_id > 0:
                    account_move_ids.append(acc_id)
                    account_move_meta[acc_id] = {
                        "name": normalize_text(rec.get("name")),
                        "stock_move_id": move_id,
                    }
            account_move_ids = _sorted_unique_ints(account_move_ids)
            if account_move_ids:
                aml_rows = await self.rpc.search_read(
                    model="account.move.line",
                    domain=[["move_id", "in", account_move_ids]],
                    fields=["id", "move_name", "move_id"],
                    context=context,
                    stage="STJ_AML_BY_ACCOUNT_MOVE",
                )
            else:
                aml_rows = []

        for aml in aml_rows:
            aml_id = int(aml.get("id") or 0)
            if aml_id > 0:
                all_aml_ids.add(aml_id)

            relation_move_id = extract_many2one_id(aml.get("move_id"))
            if relation_move_id <= 0:
                continue

            stock_move_id = relation_move_id
            if relation_model != "stock.move":
                meta = account_move_meta.get(relation_move_id, {})
                stock_move_id = int(meta.get("stock_move_id") or 0)
                if stock_move_id <= 0:
                    continue

            if stock_move_id not in move_ids:
                continue

            stj_candidate = normalize_text(aml.get("move_name"))
            if relation_model != "stock.move" and not stj_candidate:
                stj_candidate = normalize_text(account_move_meta.get(relation_move_id, {}).get("name"))
            current = direct_stj_by_move.get(stock_move_id, "")
            direct_stj_by_move[stock_move_id] = _append_stj_value(current, stj_candidate)

        svl_rows = await self.rpc.search_read(
            model="stock.valuation.layer",
            domain=[["stock_move_id", "in", move_ids]],
            fields=["id", "stock_move_id", "account_move_line_id", "account_move_id"],
            context=context,
            stage="STJ_SVL_FETCH",
        )
        for svl in svl_rows:
            move_id = extract_many2one_id(svl.get("stock_move_id"))
            if move_id <= 0:
                continue
            aml_id = extract_many2one_id(svl.get("account_move_line_id"))
            if aml_id > 0:
                move_to_aml_ids.setdefault(move_id, set()).add(aml_id)
                all_aml_ids.add(aml_id)
            acc_move_id = extract_many2one_id(svl.get("account_move_id"))
            if acc_move_id > 0:
                move_to_account_move_ids.setdefault(move_id, set()).add(acc_move_id)
                all_account_move_ids.add(acc_move_id)

        aml_info: Dict[int, Dict[str, Any]] = {}
        if all_aml_ids:
            aml_rows_all = await self.rpc.read(
                model="account.move.line",
                ids=_sorted_unique_ints(all_aml_ids),
                fields=["id", "move_name", "move_id"],
                context=context,
                stage="STJ_AML_READ",
            )
            for rec in aml_rows_all:
                aml_id = int(rec.get("id") or 0)
                if aml_id <= 0:
                    continue
                aml_info[aml_id] = {
                    "move_name": normalize_text(rec.get("move_name")),
                    "move_id": extract_many2one_id(rec.get("move_id")),
                }
                if relation_model != "stock.move":
                    move_ref = extract_many2one_id(rec.get("move_id"))
                    if move_ref > 0:
                        all_account_move_ids.add(move_ref)

        account_move_names: Dict[int, str] = {}
        if all_account_move_ids:
            acc_rows = await self.rpc.read(
                model="account.move",
                ids=_sorted_unique_ints(all_account_move_ids),
                fields=["id", "name", "stock_move_id"],
                context=context,
                stage="STJ_ACCOUNT_MOVE_READ",
            )
            for rec in acc_rows:
                acc_id = int(rec.get("id") or 0)
                if acc_id <= 0:
                    continue
                account_move_names[acc_id] = normalize_text(rec.get("name"))
                if acc_id not in account_move_meta:
                    account_move_meta[acc_id] = {
                        "name": normalize_text(rec.get("name")),
                        "stock_move_id": extract_many2one_id(rec.get("stock_move_id")),
                    }

        stj_by_move: Dict[int, str] = {}
        for move_id in move_ids:
            stj_value = direct_stj_by_move.get(move_id, "")
            for aml_id in sorted(move_to_aml_ids.get(move_id, set())):
                info = aml_info.get(aml_id, {})
                stj_candidate = normalize_text(info.get("move_name"))
                account_move_id = int(info.get("move_id") or 0)
                if not stj_candidate and account_move_id > 0:
                    stj_candidate = account_move_names.get(account_move_id, "")
                stj_value = _append_stj_value(stj_value, stj_candidate)
            for account_move_id in sorted(move_to_account_move_ids.get(move_id, set())):
                stj_value = _append_stj_value(stj_value, account_move_names.get(account_move_id, ""))
            stj_by_move[move_id] = stj_value
        return stj_by_move

    def _legacy_assign(
        self,
        root_pick_id: int,
        rows: List[ItemJournalRow],
        move_rows: List[Dict[str, Any]],
    ) -> tuple[str, Dict[int, Dict[str, int]]]:
        warning = ""
        assignments: Dict[int, Dict[str, int]] = {}
        sorted_rows = sorted(rows, key=lambda item: int(item.row_number or 0))
        sorted_moves = sorted(
            move_rows,
            key=lambda item: (
                int(item.get("picking_id") or 0),
                int(item.get("id") or 0),
            ),
        )
        assign_count = min(len(sorted_rows), len(sorted_moves))
        for idx in range(assign_count):
            row = sorted_rows[idx]
            move = sorted_moves[idx]
            assignments[int(row.row_number or 0)] = {
                "move_id": int(move.get("id") or 0),
                "picking_id": int(move.get("picking_id") or root_pick_id),
            }
        if len(sorted_rows) != len(sorted_moves):
            warning = append_error(
                warning,
                (
                    f"STJ mapping mismatch (picking={root_pick_id}): "
                    f"row_sukses={len(sorted_rows)}, move_count={len(sorted_moves)}, assigned={assign_count}"
                ),
            )
        return warning, assignments

    def _conservative_remap(
        self,
        rows: List[ItemJournalRow],
        row_specs: Dict[int, Dict[str, Any]],
        move_rows: List[Dict[str, Any]],
    ) -> tuple[bool, str, Dict[int, Dict[str, int]]]:
        tolerance = to_float(getattr(self.settings, "stj_remap_qty_tolerance", 1e-6))
        if tolerance <= 0:
            tolerance = 1e-6
        split_policy = normalize_text(getattr(self.settings, "stj_row_split_policy", "fail")).lower() or "fail"

        row_groups: Dict[Tuple[int, int, int, int], List[Dict[str, Any]]] = {}
        for row in rows:
            row_number = int(row.row_number or 0)
            spec = row_specs.get(row_number)
            if not spec:
                return False, f"row {row_number} tidak memiliki metadata remap.", {}
            signature = (
                int(spec.get("product_id") or 0),
                int(spec.get("uom_id") or 0),
                int(spec.get("src_loc_id") or 0),
                int(spec.get("dest_loc_id") or 0),
            )
            if signature[0] <= 0 or signature[1] <= 0 or signature[2] <= 0 or signature[3] <= 0:
                return False, f"row {row_number} memiliki signature tidak valid: {signature}.", {}
            row_groups.setdefault(signature, []).append(
                {
                    "row_number": row_number,
                    "qty": to_float(spec.get("qty")),
                }
            )

        move_groups: Dict[Tuple[int, int, int, int], List[Dict[str, Any]]] = {}
        for move in move_rows:
            signature = (
                int(move.get("product_id") or 0),
                int(move.get("uom_id") or 0),
                int(move.get("src_loc_id") or 0),
                int(move.get("dest_loc_id") or 0),
            )
            if signature[0] <= 0 or signature[1] <= 0 or signature[2] <= 0 or signature[3] <= 0:
                continue
            move_groups.setdefault(signature, []).append(
                {
                    "id": int(move.get("id") or 0),
                    "picking_id": int(move.get("picking_id") or 0),
                    "qty": to_float(move.get("qty")),
                }
            )

        all_signatures = sorted(set(row_groups.keys()) | set(move_groups.keys()))
        assignments: Dict[int, Dict[str, int]] = {}

        for signature in all_signatures:
            sig_rows = row_groups.get(signature, [])
            sig_moves = move_groups.get(signature, [])
            if not sig_rows or not sig_moves:
                return False, f"signature mismatch untuk {signature}: rows={len(sig_rows)} moves={len(sig_moves)}.", {}

            total_row_qty = sum(to_float(item.get("qty")) for item in sig_rows)
            total_move_qty = sum(to_float(item.get("qty")) for item in sig_moves)
            if abs(total_row_qty - total_move_qty) > tolerance:
                return (
                    False,
                    (
                        f"qty mismatch untuk {signature}: "
                        f"rows={total_row_qty:.6f}, moves={total_move_qty:.6f}."
                    ),
                    {},
                )

            sig_rows.sort(key=lambda item: int(item.get("row_number") or 0))
            sig_moves.sort(key=lambda item: (int(item.get("picking_id") or 0), int(item.get("id") or 0)))
            remaining = [to_float(item.get("qty")) for item in sig_moves]

            for row_item in sig_rows:
                row_number = int(row_item.get("row_number") or 0)
                qty_needed = to_float(row_item.get("qty"))
                if qty_needed < 0:
                    return False, f"row {row_number} qty negatif.", {}
                if qty_needed <= tolerance:
                    first = sig_moves[0]
                    assignments[row_number] = {
                        "move_id": int(first.get("id") or 0),
                        "picking_id": int(first.get("picking_id") or 0),
                    }
                    continue

                selected_index = -1
                for idx, remain_qty in enumerate(remaining):
                    if remain_qty + tolerance >= qty_needed:
                        selected_index = idx
                        break
                if selected_index < 0:
                    total_remaining = sum(max(item, 0.0) for item in remaining)
                    if total_remaining + tolerance >= qty_needed and split_policy == "fail":
                        return (
                            False,
                            (
                                f"row split dibutuhkan untuk row {row_number} signature={signature} "
                                f"(qty={qty_needed:.6f}, remaining_total={total_remaining:.6f})."
                            ),
                            {},
                        )
                    return (
                        False,
                        (
                            f"alokasi gagal untuk row {row_number} signature={signature} "
                            f"(qty={qty_needed:.6f}, remaining_total={total_remaining:.6f})."
                        ),
                        {},
                    )

                move_rec = sig_moves[selected_index]
                assignments[row_number] = {
                    "move_id": int(move_rec.get("id") or 0),
                    "picking_id": int(move_rec.get("picking_id") or 0),
                }
                remaining[selected_index] = max(0.0, remaining[selected_index] - qty_needed)

        return True, f"rows={len(rows)} moves={len(move_rows)} signatures={len(all_signatures)}", assignments

    async def _resolve_aml_move_relation(self, company_id: int) -> str:
        context = build_company_context(company_id)
        try:
            field_meta = await self.rpc.fields_get(
                model="account.move.line",
                attributes=["type", "relation"],
                context=context,
                stage="STJ_RELATION_PREFLIGHT",
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("Gagal membaca fields_get account.move.line: %s. fallback account.move", exc)
            return "account.move"

        move_meta = field_meta.get("move_id", {})
        if not isinstance(move_meta, dict):
            return "account.move"
        relation = normalize_text(move_meta.get("relation")).lower()
        field_type = normalize_text(move_meta.get("type")).lower()
        if field_type != "many2one":
            return "account.move"
        if relation in {"stock.move", "account.move"}:
            return relation
        return "account.move"
