"""Upload service mixins extracted from ItemJournalServiceAsync."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
import hashlib
import json
import time
from typing import Any, Dict, List

from smartscc_tools.features.item_journal.messaging import build_slow_feedback, stage_to_business_message
from smartscc_tools.features.item_journal.observability.audit import AuditEvent
from smartscc_tools.services.odoo.gateway import build_company_context, extract_many2one_id
from smartscc_tools.features.item_journal.services.date_ops import collect_active_company_ids, read_picking_name
from smartscc_tools.features.item_journal.workbook import ItemJournalRow
from smartscc_tools.features.item_journal.utils import (
    append_error,
    chunked,
    compute_hybrid_delay_ms,
    extract_http_status,
    is_retryable_error,
    normalize_text,
    parse_datetime_text,
)

from .upload import BatchEntry, GlobalPrecheckError, PendingStjRecovery, PickingAccumulator, ProductInfo, _safe_float_or_none, _split_semicolon_refs


class UploadExecutionMixin:
    def _build_group_key(self, company_id: int, op_type: str, src_loc: str, dest_loc: str, date_done_utc: str) -> str:
        base = f"{company_id}|{normalize_text(op_type).lower()}|{normalize_text(src_loc).lower()}|{normalize_text(dest_loc).lower()}"
        if date_done_utc:
            return f"{base}|{date_done_utc}"
        return base

    def _build_internal_batch_signature(
        self,
        product_id: int,
        company_id: int,
        date_done_utc: str,
        src_loc: str,
        dest_loc: str,
    ) -> tuple[int, int, str, str, str]:
        return (
            int(product_id or 0),
            int(company_id or 0),
            normalize_text(date_done_utc),
            normalize_text(src_loc).lower(),
            normalize_text(dest_loc).lower(),
        )

    def _format_date_to_odoo_utc(self, row: ItemJournalRow) -> str:
        row_date = row.parsed_date()
        if row_date is None:
            return ""
        local_dt = datetime(row_date.year, row_date.month, row_date.day, 0, 0, 0)
        if not self.settings.use_local_time_as_utc:
            local_dt = local_dt - timedelta(hours=self.settings.local_tz_offset)
        return local_dt.strftime("%Y-%m-%d %H:%M:%S")

    async def _get_picking_type_id_cached(self, op_type_name: str, company_id: int) -> int:
        key = f"{normalize_text(op_type_name).lower()}|{company_id}"
        if key in self.cache_pick_type:
            return self.cache_pick_type[key]
        if not normalize_text(op_type_name):
            self.cache_pick_type[key] = 0
            return 0
        context = build_company_context(company_id)
        rows = await self.rpc.search_read(
            model="stock.picking.type",
            domain=[["name", "=", op_type_name], ["company_id", "=", company_id]],
            fields=["id"],
            limit=1,
            context=context,
            stage="ROW_LOOP",
        )
        pick_type_id = int(rows[0]["id"]) if rows else 0
        self.cache_pick_type[key] = pick_type_id
        return pick_type_id

    async def _resolve_location_id_cached(self, location_name: str, company_id: int) -> int:
        clean_name = normalize_text(location_name)
        if not clean_name:
            return 0
        if clean_name.isdigit():
            return int(clean_name)

        key = f"{clean_name.lower()}|{company_id}"
        if key in self.cache_location:
            return self.cache_location[key]
        shared_location_id = self._master_cache_get_location_id(company_id=company_id, location_name=clean_name)
        if shared_location_id is not None:
            self.cache_location[key] = int(shared_location_id or 0)
            return self.cache_location[key]

        context = build_company_context(company_id)
        domains = [
            [["name", "=", clean_name], ["company_id", "=", company_id]],
            [["complete_name", "=", clean_name], ["company_id", "=", company_id]],
            [["name", "=", clean_name], ["company_id", "=", False]],
            [["complete_name", "=", clean_name], ["company_id", "=", False]],
        ]
        location_id = 0
        for domain in domains:
            rows = await self.rpc.search_read(
                model="stock.location",
                domain=domain,
                fields=["id"],
                limit=1,
                context=context,
                stage="ROW_LOOP",
            )
            if rows:
                location_id = int(rows[0]["id"])
                break

        if location_id == 0:
            ilike_domain = [["complete_name", "ilike", clean_name], ["company_id", "in", [False, company_id]]]
            rows = await self.rpc.search_read(
                model="stock.location",
                domain=ilike_domain,
                fields=["id", "complete_name", "name", "company_id"],
                limit=40,
                context=context,
                stage="GLOBAL_PRECHECK",
            )
            best_score = -10**9
            for rec in rows:
                candidate_name = normalize_text(rec.get("complete_name")) or normalize_text(rec.get("name"))
                candidate_company = extract_many2one_id(rec.get("company_id"))
                score = self._score_location_match(clean_name, candidate_name, company_id, candidate_company)
                if score > best_score:
                    best_score = score
                    location_id = int(rec.get("id") or 0)

        self.cache_location[key] = location_id
        if location_id > 0:
            self._master_cache_set_location_id(
                company_id=company_id,
                location_name=clean_name,
                location_id=location_id,
            )
        return location_id

    def _score_location_match(self, requested_name: str, candidate_name: str, company_id: int, candidate_company_id: int) -> int:
        req = normalize_text(requested_name).lower()
        cand = normalize_text(candidate_name).lower()
        score = 0
        if candidate_company_id == company_id:
            score += 1000000
        elif candidate_company_id == 0:
            score += 500000
        else:
            score -= 200000
        if req == cand:
            score += 600000
        if cand.startswith(req):
            score += 250000
        if req in cand:
            score += 120000
        score += len(set(req.split()) & set(cand.split())) * 1000
        score -= abs(len(cand) - len(req)) * 10
        return score

    async def _validate_location_company(self, location_id: int, company_id: int) -> tuple[bool, str]:
        if location_id <= 0 or company_id <= 0:
            return True, ""
        rows = await self.rpc.read(
            model="stock.location",
            ids=[location_id],
            fields=["company_id", "display_name"],
            context=build_company_context(company_id),
            stage="GLOBAL_PRECHECK",
        )
        if not rows:
            return False, f"Location ID {location_id} tidak ditemukan."
        loc_company_id = extract_many2one_id(rows[0].get("company_id"))
        if loc_company_id == 0 or loc_company_id == company_id:
            return True, ""
        loc_name = normalize_text(rows[0].get("display_name")) or f"ID {location_id}"
        return False, f"{loc_name} (company_id={loc_company_id})"

    def _normalize_create_origin_mode(self) -> str:
        mode = normalize_text(getattr(self.settings, "create_origin_mode", "technical")).lower()
        if mode == "human_suffix":
            human_ref = normalize_text(getattr(self.settings, "create_origin_human_ref", ""))
            if human_ref:
                return "human_suffix"
        return "technical"

    def _build_create_origin_value(self, idempotency_key: str) -> tuple[str, str]:
        mode = self._normalize_create_origin_mode()
        if mode == "human_suffix":
            human_ref = normalize_text(getattr(self.settings, "create_origin_human_ref", ""))
            return f"{human_ref} [ID:{idempotency_key}]", mode
        return idempotency_key, "technical"

    def _build_batch_hash16(
        self,
        batch_entries: List[BatchEntry],
        company_id: int,
        pick_type_id: int,
        src_loc_id: int,
        dest_loc_id: int,
        date_done_utc: str,
    ) -> str:
        stable_moves: List[Dict[str, Any]] = []
        for entry in batch_entries:
            move_vals: Dict[str, Any] = {}
            if len(entry.move_command) >= 3 and isinstance(entry.move_command[2], dict):
                move_vals = entry.move_command[2]
            row_meta = dict(entry.row_meta or {})
            stable_moves.append(
                {
                    "row_number": int(row_meta.get("row_number") or entry.row.row_number or 0),
                    "product_id": int(row_meta.get("product_id") or move_vals.get("product_id") or 0),
                    "qty": float(row_meta.get("qty") or move_vals.get("product_uom_qty") or 0.0),
                    "uom": int(row_meta.get("uom_id") or move_vals.get("product_uom") or 0),
                    "src": int(row_meta.get("src_loc_id") or move_vals.get("location_id") or 0),
                    "dest": int(row_meta.get("dest_loc_id") or move_vals.get("location_dest_id") or 0),
                }
            )
        stable_moves = sorted(
            stable_moves,
            key=lambda item: (
                int(item.get("product_id") or 0),
                float(item.get("qty") or 0.0),
                int(item.get("uom") or 0),
                int(item.get("src") or 0),
                int(item.get("dest") or 0),
                int(item.get("row_number") or 0),
            ),
        )
        payload = {
            "company_id": int(company_id or 0),
            "pick_type_id": int(pick_type_id or 0),
            "src_loc_id": int(src_loc_id or 0),
            "dest_loc_id": int(dest_loc_id or 0),
            "date_done_utc": normalize_text(date_done_utc),
            "moves": stable_moves,
        }
        signature = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.md5(signature.encode("utf-8")).hexdigest()[:16]

    def _build_idempotency_key(
        self,
        batch_entries: List[BatchEntry],
        company_id: int,
        pick_type_id: int,
        src_loc_id: int,
        dest_loc_id: int,
        date_done_utc: str,
        batch_seq: int,
        split_depth: int,
        split_branch: str,
    ) -> str:
        hash16 = self._build_batch_hash16(
            batch_entries=batch_entries,
            company_id=company_id,
            pick_type_id=pick_type_id,
            src_loc_id=src_loc_id,
            dest_loc_id=dest_loc_id,
            date_done_utc=date_done_utc,
        )
        branch = normalize_text(split_branch) or "root"
        return f"IJ:{self.run_id}:{batch_seq}:{split_depth}:{branch}:{hash16}"

    def _mark_batch_success(
        self,
        batch_entries: List[BatchEntry],
        pick_acc: PickingAccumulator,
        group_key: str,
        batch_seq: int,
        on_batch_success: Callable[[int, str, List[BatchEntry], str], None] | None,
        origin_scope_key: str,
    ) -> tuple[bool, str]:
        result_name = pick_acc.picking_name or str(pick_acc.picking_id)
        successful_entries: List[BatchEntry] = []
        for entry in batch_entries:
            if entry.row.result != "[Error]":
                entry.row.result = result_name
                entry.row.stj = ""
                successful_entries.append(entry)
                self._emit_audit(
                    stage="ROW_DONE",
                    row_number=entry.row.row_number,
                    group_key=group_key,
                    batch_seq=batch_seq,
                    result="OK",
                    message=f"picking={result_name}",
                    picking_id=pick_acc.picking_id,
                )
        if on_batch_success is not None:
            on_batch_success(
                pick_acc.picking_id,
                result_name,
                successful_entries,
                normalize_text(origin_scope_key),
            )
        return True, ""

    def _stj_recovery_delay_ms(self, next_attempt: int) -> int:
        base_delay_ms = max(0, int(getattr(self.settings, "stj_recovery_poll_delay_ms", 0) or 0))
        fast_attempts = max(1, int(getattr(self.settings, "stj_recovery_fast_attempts", 1) or 1))
        max_delay_ms = max(
            base_delay_ms,
            int(getattr(self.settings, "stj_recovery_poll_max_delay_ms", base_delay_ms) or base_delay_ms),
        )
        return compute_hybrid_delay_ms(
            next_attempt=next_attempt,
            base_delay_ms=base_delay_ms,
            fast_attempts=fast_attempts,
            max_delay_ms=max_delay_ms,
        )

    def _select_reconcile_candidate(self, candidates: List[Dict[str, Any]]) -> Dict[str, Any] | None:
        if not candidates:
            return None
        state_rank = {
            "done": 6,
            "posted": 5,
            "assigned": 4,
            "confirmed": 3,
            "waiting": 2,
            "draft": 1,
        }
        clean_candidates = [rec for rec in candidates if normalize_text(rec.get("state")).lower() != "cancel"]
        if not clean_candidates:
            return None

        def _score(record: Dict[str, Any]) -> tuple[int, float, int]:
            state = normalize_text(record.get("state")).lower()
            rank = state_rank.get(state, 0)
            created_at = normalize_text(record.get("create_date"))
            parsed = parse_datetime_text(created_at)
            ts = parsed.timestamp() if parsed is not None else 0.0
            rec_id = int(record.get("id") or 0)
            return (rank, ts, rec_id)

        clean_candidates.sort(key=_score, reverse=True)
        return clean_candidates[0]

    async def _reconcile_create_timeout(
        self,
        company_id: int,
        idempotency_key: str,
        origin_mode: str,
    ) -> tuple[str, Dict[str, Any] | None]:
        clean_mode = normalize_text(origin_mode).lower() or "technical"
        token = f"[ID:{idempotency_key}]"
        if clean_mode == "human_suffix":
            domain = [["company_id", "=", company_id], ["state", "!=", "cancel"], ["origin", "ilike", token]]
        else:
            domain = [["company_id", "=", company_id], ["state", "!=", "cancel"], ["origin", "=", idempotency_key]]
        rows = await self.rpc.search_read(
            model="stock.picking",
            domain=domain,
            fields=["id", "name", "state", "create_date", "origin"],
            order="create_date desc,id desc",
            limit=20,
            context=build_company_context(company_id),
            stage="CREATE_RECONCILE",
        )
        candidates: List[Dict[str, Any]] = []
        for rec in rows:
            origin_value = normalize_text(rec.get("origin"))
            if clean_mode == "human_suffix":
                if token.lower() not in origin_value.lower():
                    continue
            elif origin_value != idempotency_key:
                continue
            if normalize_text(rec.get("state")).lower() == "cancel":
                continue
            candidates.append(rec)
        if not candidates:
            return "MISS", None
        if len(candidates) == 1:
            return "HIT", candidates[0]
        selected = self._select_reconcile_candidate(candidates)
        if selected is None:
            return "MISS", None
        return "AMBIGUOUS", selected

    def _collect_missing_stj_rows(self, rows: List[ItemJournalRow]) -> List[ItemJournalRow]:
        return [row for row in rows if row.result != "[Error]" and not normalize_text(row.stj)]

    async def _filter_required_stj_rows(self, rows: List[ItemJournalRow]) -> List[ItemJournalRow]:
        required_rows: List[ItemJournalRow] = []
        for row in rows:
            row_number = int(row.row_number or 0)
            expected, reason = await self._should_expect_journal_for_row(row_number)
            self._row_journal_expected[row_number] = expected
            self._row_journal_reason[row_number] = reason
            if expected:
                required_rows.append(row)
        return required_rows

    def _emit_stj_done_rows(
        self,
        rows: List[ItemJournalRow],
        group_key: str,
        batch_seq: int,
        pick_id: int,
    ) -> None:
        for row in rows:
            stj_value = normalize_text(row.stj)
            if row.result == "[Error]" or not stj_value:
                continue
            if row.row_number in self._stj_done_emitted_rows:
                continue
            self._stj_done_emitted_rows.add(row.row_number)
            self._emit_audit(
                stage="STJ_DONE",
                row_number=row.row_number,
                group_key=group_key,
                batch_seq=batch_seq,
                result="OK",
                message=f"stj={stj_value}",
                picking_id=pick_id,
            )

    def _emit_stj_mapping_events(
        self,
        pick_id: int,
        group_key: str,
        batch_seq: int,
    ) -> None:
        meta = getattr(self.stj_service, "last_meta", {})
        if not isinstance(meta, dict):
            return
        scope_pick_ids = meta.get("scope_pick_ids", [])
        scope_phase = normalize_text(meta.get("scope_phase"))
        scope_phases = meta.get("scope_phases", [])
        if isinstance(scope_pick_ids, list) and scope_pick_ids:
            scope_message = (
                f"scope_pickings={len(scope_pick_ids)} "
                f"root_picking={pick_id} source={normalize_text(meta.get('scope_source')) or '-'} "
                f"scope_phase={scope_phase or '-'}"
            )
            self._emit_audit(
                stage="STJ_BACKORDER_SCOPE_RESOLVED",
                group_key=group_key,
                batch_seq=batch_seq,
                result="OK",
                message=scope_message,
                picking_id=pick_id,
            )
            self._emit_perf_marker(
                event_type="stj_backorder_scope_resolved",
                stage="STJ_BACKORDER_SCOPE_RESOLVED",
                severity="info",
                cause_code="unknown",
                message=scope_message,
                batch_seq=batch_seq,
                details={
                    "picking_id": pick_id,
                    "scope_pick_ids": scope_pick_ids,
                    "scope_source": normalize_text(meta.get("scope_source")),
                    "scope_phase": scope_phase,
                    "scope_phases": scope_phases if isinstance(scope_phases, list) else [],
                },
            )
        journal_source = normalize_text(meta.get("journal_source"))
        if journal_source:
            source_message = f"journal_source={journal_source} picking={pick_id}"
            self._emit_audit(
                stage="STJ_JOURNAL_SOURCE",
                group_key=group_key,
                batch_seq=batch_seq,
                result="OK",
                message=source_message,
                picking_id=pick_id,
            )

        remap_message = normalize_text(meta.get("remap_message"))
        if meta.get("remap_applied"):
            message = remap_message or f"remap_applied picking={pick_id}"
            self._emit_audit(
                stage="STJ_REMAP_APPLIED",
                group_key=group_key,
                batch_seq=batch_seq,
                result="OK",
                message=message,
                picking_id=pick_id,
            )
            self._emit_perf_marker(
                event_type="stj_remap_applied",
                stage="STJ_REMAP_APPLIED",
                severity="info",
                cause_code="unknown",
                message=message,
                batch_seq=batch_seq,
                details={
                    "picking_id": pick_id,
                    "row_count": int(meta.get("row_count") or 0),
                    "move_count": int(meta.get("move_count") or 0),
                    "assigned_rows": int(meta.get("assigned_rows") or 0),
                },
            )
        elif meta.get("remap_failed"):
            message = remap_message or f"remap_failed picking={pick_id}"
            self._emit_audit(
                stage="STJ_REMAP_FAILED",
                group_key=group_key,
                batch_seq=batch_seq,
                result="ERROR",
                message=message,
                picking_id=pick_id,
            )
            self._emit_perf_marker(
                event_type="stj_remap_failed",
                stage="STJ_REMAP_FAILED",
                severity="error",
                cause_code="stj_missing",
                message=message,
                batch_seq=batch_seq,
                details={
                    "picking_id": pick_id,
                    "row_count": int(meta.get("row_count") or 0),
                    "move_count": int(meta.get("move_count") or 0),
                    "assigned_rows": int(meta.get("assigned_rows") or 0),
                    "missing_rows": int(meta.get("missing_rows") or 0),
                },
            )

    def _emit_perf_marker(
        self,
        event_type: str,
        stage: str,
        severity: str,
        cause_code: str,
        message: str,
        batch_seq: int,
        details: Dict[str, Any] | None = None,
    ) -> None:
        if self.perf_reporter is None:
            return
        self.perf_reporter.emit(
            event_type=event_type,
            stage=stage,
            severity=severity,
            cause_code=cause_code,
            rpc_model="stock.picking",
            rpc_method="stj_recovery",
            duration_ms=int(getattr(self.rpc, "last_duration_ms", 0) or 0),
            batch_seq=batch_seq,
            row_number=0,
            message=message,
            http_status=int(getattr(self.rpc, "last_http_status", 0) or 0),
            details=details or {},
        )

    def _queue_stj_recovery(
        self,
        pick_id: int,
        company_id: int,
        group_key: str,
        batch_seq: int,
        target_entries: List[BatchEntry],
        origin_scope_key: str,
        missing_rows: List[ItemJournalRow],
        initial_warning: str,
    ) -> None:
        target_rows = [entry.row for entry in target_entries]
        row_specs = {
            int(entry.row.row_number): dict(entry.row_meta)
            for entry in target_entries
            if entry.row.row_number > 0
        }
        now = self._stj_recovery_now()
        timeout_sec = max(0, int(getattr(self.settings, "stj_recovery_timeout_ms", 0) or 0)) / 1000.0
        first_delay_ms = self._stj_recovery_delay_ms(next_attempt=1)
        poll_delay_sec = first_delay_ms / 1000.0
        self.pending_stj_recoveries[pick_id] = PendingStjRecovery(
            pick_id=pick_id,
            company_id=company_id,
            group_key=group_key,
            batch_seq=batch_seq,
            target_rows=target_rows[:],
            row_specs=row_specs,
            origin_scope_key=normalize_text(origin_scope_key),
            queued_at_monotonic=now,
            deadline_at_monotonic=now + timeout_sec,
            next_retry_at_monotonic=now + poll_delay_sec,
            last_warning=initial_warning,
        )
        message = append_error(
            initial_warning,
            (
                f"STJ recovery queued (picking={pick_id}, missing_rows={len(missing_rows)}, "
                f"timeout_ms={int(timeout_sec * 1000)}, next_delay_ms={first_delay_ms})."
            ),
        )
        self._emit_audit(
            stage="STJ_RECOVERY_QUEUED",
            group_key=group_key,
            batch_seq=batch_seq,
            result="WARN",
            message=message,
            picking_id=pick_id,
        )
        self._publish_progress(
            stage="STJ_RECOVERY_QUEUED",
            current=batch_seq,
            total=max(1, batch_seq),
            message=f"STJ pending untuk picking {pick_id}: {len(missing_rows)} baris",
        )
        self._emit_perf_marker(
            event_type="stj_recovery_queued",
            stage="STJ_RECOVERY_QUEUED",
            severity="warn",
            cause_code="stj_missing",
            message=message,
            batch_seq=batch_seq,
            details={
                "picking_id": pick_id,
                "missing_rows": len(missing_rows),
                "timeout_ms": int(timeout_sec * 1000),
                "next_delay_ms": first_delay_ms,
            },
        )

    async def _process_pending_stj_recoveries(
        self,
        repo: ItemJournalWorkbookRepo,
        wait_until_empty: bool,
    ) -> None:
        if not self.pending_stj_recoveries:
            return

        while self.pending_stj_recoveries:
            now = self._stj_recovery_now()
            due_pick_ids: List[int] = []
            next_wakeup_at: float | None = None
            for pick_id, pending in self.pending_stj_recoveries.items():
                wakeup_at = min(pending.next_retry_at_monotonic, pending.deadline_at_monotonic)
                if wakeup_at <= now:
                    due_pick_ids.append(pick_id)
                    continue
                if next_wakeup_at is None or wakeup_at < next_wakeup_at:
                    next_wakeup_at = wakeup_at

            if not due_pick_ids:
                if not wait_until_empty:
                    return
                sleep_sec = max(0.0, (next_wakeup_at or now) - now)
                repo.write_status(
                    f"Menunggu STJ recovery: {len(self.pending_stj_recoveries)} picking pending"
                )
                self._publish_progress(
                    stage="STJ_RECOVERY_RETRY",
                    current=len(self.pending_stj_recoveries),
                    total=len(self.pending_stj_recoveries),
                    message=f"Menunggu STJ recovery ({len(self.pending_stj_recoveries)} picking pending)",
                )
                if sleep_sec > 0:
                    await asyncio.sleep(sleep_sec)
                continue

            for pick_id in due_pick_ids:
                pending = self.pending_stj_recoveries.get(pick_id)
                if pending is None:
                    continue
                await self._process_single_stj_recovery(pending)

            self._flush_audit_to_repo(repo)
            if not wait_until_empty:
                return

    async def _process_single_stj_recovery(self, pending: PendingStjRecovery) -> None:
        missing_rows = self._collect_missing_stj_rows(pending.target_rows)
        if not missing_rows:
            self._emit_stj_done_rows(
                rows=pending.target_rows,
                group_key=pending.group_key,
                batch_seq=pending.batch_seq,
                pick_id=pending.pick_id,
            )
            self.pending_stj_recoveries.pop(pending.pick_id, None)
            return

        now = self._stj_recovery_now()
        if now >= pending.deadline_at_monotonic:
            self._finalize_stj_recovery_failure(pending, missing_rows)
            return

        pending.attempts += 1
        self._publish_progress(
            stage="STJ_RECOVERY_RETRY",
            current=pending.batch_seq,
            total=max(1, pending.batch_seq),
            message=(
                f"Retry STJ picking {pending.pick_id} attempt {pending.attempts}: "
                f"{len(missing_rows)} baris masih kosong"
            ),
        )
        warning = await self.stj_service.fill_stj_for_group(
            pick_id=pending.pick_id,
            company_id=pending.company_id,
            target_rows=pending.target_rows,
            row_specs=pending.row_specs,
            origin_scope_key=pending.origin_scope_key,
        )
        self._emit_stj_mapping_events(
            pick_id=pending.pick_id,
            group_key=pending.group_key,
            batch_seq=pending.batch_seq,
        )
        if warning:
            pending.last_warning = warning

        missing_rows = self._collect_missing_stj_rows(pending.target_rows)
        elapsed_ms = int(max(self._stj_recovery_now() - pending.queued_at_monotonic, 0.0) * 1000.0)
        next_delay_ms = 0
        if missing_rows:
            next_delay_ms = self._stj_recovery_delay_ms(next_attempt=pending.attempts + 1)
        message = append_error(
            warning,
            (
                f"STJ recovery retry (picking={pending.pick_id}, attempt={pending.attempts}, "
                f"missing_rows={len(missing_rows)}, next_delay_ms={next_delay_ms}, elapsed_ms={elapsed_ms})."
            ),
        )
        self._emit_audit(
            stage="STJ_RECOVERY_RETRY",
            group_key=pending.group_key,
            batch_seq=pending.batch_seq,
            result="RETRY" if missing_rows else "OK",
            message=message,
            picking_id=pending.pick_id,
        )
        self._emit_perf_marker(
            event_type="stj_recovery_retry",
            stage="STJ_RECOVERY_RETRY",
            severity="warn" if missing_rows else "info",
            cause_code="stj_missing" if missing_rows else "unknown",
            message=message,
            batch_seq=pending.batch_seq,
            details={
                "picking_id": pending.pick_id,
                "attempt": pending.attempts,
                "missing_rows": len(missing_rows),
                "next_delay_ms": next_delay_ms,
                "elapsed_ms": elapsed_ms,
            },
        )
        self._emit_stj_done_rows(
            rows=pending.target_rows,
            group_key=pending.group_key,
            batch_seq=pending.batch_seq,
            pick_id=pending.pick_id,
        )
        if not missing_rows:
            done_message = append_error(
                warning,
                (
                    f"STJ recovery complete (picking={pending.pick_id}, attempts={pending.attempts}, "
                    f"elapsed_ms={elapsed_ms})."
                ),
            )
            self._emit_audit(
                stage="STJ_RECOVERY_DONE",
                group_key=pending.group_key,
                batch_seq=pending.batch_seq,
                result="OK",
                message=done_message,
                picking_id=pending.pick_id,
            )
            self._emit_perf_marker(
                event_type="stj_recovery_done",
                stage="STJ_RECOVERY_DONE",
                severity="info",
                cause_code="unknown",
                message=done_message,
                batch_seq=pending.batch_seq,
                details={
                    "picking_id": pending.pick_id,
                    "attempts": pending.attempts,
                    "elapsed_ms": elapsed_ms,
                },
            )
            self.pending_stj_recoveries.pop(pending.pick_id, None)
            return

        pending.next_retry_at_monotonic = self._stj_recovery_now() + (next_delay_ms / 1000.0)
        if self._stj_recovery_now() >= pending.deadline_at_monotonic:
            self._finalize_stj_recovery_failure(pending, missing_rows)

    def _finalize_stj_recovery_failure(
        self,
        pending: PendingStjRecovery,
        missing_rows: List[ItemJournalRow],
    ) -> None:
        elapsed_ms = int(max(self._stj_recovery_now() - pending.queued_at_monotonic, 0.0) * 1000.0)
        warning_tag = (
            f"STJ_TIMEOUT_WARNING[picking={pending.pick_id};missing={len(missing_rows)};elapsed_ms={elapsed_ms}]"
        )
        strict_message = append_error(
            pending.last_warning,
            (
                f"{warning_tag} | STJ wajib terisi namun kosong pada {len(missing_rows)} baris "
                f"(picking={pending.pick_id}, elapsed_ms={elapsed_ms})."
            ),
        )
        self._emit_audit(
            stage="STJ_REQUIRED_WARN",
            group_key=pending.group_key,
            batch_seq=pending.batch_seq,
            result="WARN",
            message=strict_message,
            picking_id=pending.pick_id,
        )
        self._publish_progress(
            stage="STJ_REQUIRED_WARN",
            current=pending.batch_seq,
            total=max(1, pending.batch_seq),
            message=f"STJ recovery timeout untuk picking {pending.pick_id}",
        )
        self._emit_perf_marker(
            event_type="stj_required_warn",
            stage="STJ_REQUIRED_WARN",
            severity="warn",
            cause_code="stj_missing",
            message=strict_message,
            batch_seq=pending.batch_seq,
            details={
                "picking_id": pending.pick_id,
                "missing_rows": len(missing_rows),
                "elapsed_ms": elapsed_ms,
            },
        )
        for row in missing_rows:
            row.append_error(warning_tag)
            self._emit_audit(
                stage="ROW_WARN",
                row_number=row.row_number,
                group_key=pending.group_key,
                batch_seq=pending.batch_seq,
                result="WARN",
                message=warning_tag,
                picking_id=pending.pick_id,
            )
        self.pending_stj_recoveries.pop(pending.pick_id, None)

    def _is_stock_picking_create_timeout(self, error_text: str) -> bool:
        clean = normalize_text(error_text).lower()
        if not clean:
            return False
        if "stock.picking.create timeout" in clean:
            return True
        model_ctx = normalize_text(getattr(self.rpc.call_context, "model", "")).lower()
        method_ctx = normalize_text(getattr(self.rpc.call_context, "method", "")).lower()
        has_create_context = (
            "stock.picking.create" in clean
            or ("stock.picking" in clean and "create" in clean)
            or (model_ctx == "stock.picking" and method_ctx == "create")
        )
        if not has_create_context:
            return False
        timeout_tokens = [
            "timeout",
            "timed out",
            "read timeout",
            "connect timeout",
            "gateway timeout",
        ]
        if any(token in clean for token in timeout_tokens):
            return True
        status = extract_http_status(clean)
        return status in {502, 503, 504}

    async def _process_batch_with_retry(
        self,
        batch_entries: List[BatchEntry],
        company_id: int,
        pick_type_id: int,
        src_loc_id: int,
        dest_loc_id: int,
        date_done_utc: str,
        precheck_error: str,
        dry_run: bool,
        group_key: str,
        batch_seq: int,
        on_batch_success: Callable[[int, str, List[BatchEntry], str], None] | None = None,
        split_depth: int = 0,
        split_branch: str = "root",
    ) -> tuple[bool, str, int]:
        if not batch_entries:
            return True, "", 0
        if precheck_error:
            for entry in batch_entries:
                entry.row.mark_error(precheck_error)
                self._emit_audit(stage="ROW_ERROR", row_number=entry.row.row_number, group_key=group_key, batch_seq=batch_seq, result="ERROR", message=precheck_error, picking_id=0)
            return False, precheck_error, 0

        move_commands = [entry.move_command for entry in batch_entries]
        idempotency_key = self._build_idempotency_key(
            batch_entries=batch_entries,
            company_id=company_id,
            pick_type_id=pick_type_id,
            src_loc_id=src_loc_id,
            dest_loc_id=dest_loc_id,
            date_done_utc=date_done_utc,
            batch_seq=batch_seq,
            split_depth=split_depth,
            split_branch=split_branch,
        )
        create_origin, origin_mode = self._build_create_origin_value(idempotency_key)
        adaptive_split_count = 0
        last_error = ""
        last_error_is_create_timeout = False

        general_attempt = 0
        create_timeout_retry_used = 0
        create_timeout_retry_max = 3
        create_timeout_retry_delay_ms = 2000
        while True:
            general_attempt += 1
            pick_acc = PickingAccumulator()
            ok, err = await self._submit_picking_batch(
                pick_acc=pick_acc,
                company_id=company_id,
                pick_type_id=pick_type_id,
                src_loc_id=src_loc_id,
                dest_loc_id=dest_loc_id,
                move_commands=move_commands,
                date_done_utc=date_done_utc,
                dry_run=dry_run,
                create_origin=create_origin,
            )
            if ok:
                ok_result, ok_err = self._mark_batch_success(
                    batch_entries=batch_entries,
                    pick_acc=pick_acc,
                    group_key=group_key,
                    batch_seq=batch_seq,
                    on_batch_success=on_batch_success,
                    origin_scope_key=idempotency_key,
                )
                return ok_result, ok_err, adaptive_split_count

            last_error = err
            last_error_is_create_timeout = self._is_stock_picking_create_timeout(err)
            if last_error_is_create_timeout:
                reconcile_status, reconcile_rec = await self._reconcile_create_timeout(
                    company_id=company_id,
                    idempotency_key=idempotency_key,
                    origin_mode=origin_mode,
                )
                stage = f"CREATE_RECONCILE_{reconcile_status}"
                rec_pick_id = int((reconcile_rec or {}).get("id") or 0)
                rec_name = normalize_text((reconcile_rec or {}).get("name"))
                rec_state = normalize_text((reconcile_rec or {}).get("state"))
                reconcile_message = (
                    f"create_timeout_reconcile status={reconcile_status} "
                    f"origin_mode={origin_mode} key={idempotency_key} "
                    f"pick_id={rec_pick_id or '-'} state={rec_state or '-'}"
                )
                self._emit_audit(
                    stage=stage,
                    group_key=group_key,
                    batch_seq=batch_seq,
                    result="OK" if reconcile_status == "HIT" else "WARN",
                    message=reconcile_message,
                    picking_id=rec_pick_id,
                )
                if self.perf_reporter is not None:
                    self.perf_reporter.emit(
                        event_type=stage.lower(),
                        stage=stage,
                        severity="info" if reconcile_status == "HIT" else "warn",
                        cause_code="unknown" if reconcile_status in {"HIT", "AMBIGUOUS"} else "transport_timeout",
                        rpc_model="stock.picking",
                        rpc_method="create",
                        duration_ms=int(getattr(self.rpc, "last_duration_ms", 0) or 0),
                        batch_seq=batch_seq,
                        row_number=0,
                        message=reconcile_message,
                        http_status=int(getattr(self.rpc, "last_http_status", 0) or 0),
                        details={
                            "idempotency_key": idempotency_key,
                            "origin_mode": origin_mode,
                            "picking_id": rec_pick_id,
                            "state": rec_state,
                        },
                    )
                if reconcile_status in {"HIT", "AMBIGUOUS"} and rec_pick_id > 0:
                    pick_acc.picking_id = rec_pick_id
                    pick_acc.picking_name = rec_name or str(rec_pick_id)
                    ok_result, ok_err = self._mark_batch_success(
                        batch_entries=batch_entries,
                        pick_acc=pick_acc,
                        group_key=group_key,
                        batch_seq=batch_seq,
                        on_batch_success=on_batch_success,
                        origin_scope_key=idempotency_key,
                    )
                    return ok_result, ok_err, adaptive_split_count

            if last_error_is_create_timeout and create_timeout_retry_used < create_timeout_retry_max:
                create_timeout_retry_used += 1
                retry_kind = "create_timeout_retry_only"
                retry_delay_ms = create_timeout_retry_delay_ms
                retry_attempt = create_timeout_retry_used
                retry_message = (
                    f"{err} | retry_kind=create_timeout_retry_only "
                    f"({create_timeout_retry_used}/{create_timeout_retry_max})"
                )
                self._emit_audit(
                    stage="RETRY",
                    group_key=group_key,
                    batch_seq=batch_seq,
                    attempt=retry_attempt,
                    result="RETRY",
                    message=retry_message,
                    picking_id=pick_acc.picking_id,
                )
                self._publish_progress(stage="RETRY", current=batch_seq, total=max(batch_seq, 1), message=f"Retry batch {batch_seq}: {retry_message}")
                if self.perf_reporter is not None:
                    self.perf_reporter.emit(
                        event_type="retry",
                        stage="RETRY",
                        severity="warn",
                        cause_code="retry_storm",
                        rpc_model=normalize_text(getattr(self.rpc.call_context, "model", "")),
                        rpc_method=normalize_text(getattr(self.rpc.call_context, "method", "")),
                        duration_ms=int(getattr(self.rpc, "last_duration_ms", 0) or 0),
                        batch_seq=batch_seq,
                        row_number=0,
                        message=retry_message,
                        http_status=int(getattr(self.rpc, "last_http_status", 0) or 0),
                        attempt=retry_attempt,
                        details={"retry_kind": retry_kind},
                    )
                if retry_delay_ms > 0:
                    await asyncio.sleep(retry_delay_ms / 1000.0)
                continue

            use_general_retry = (
                not last_error_is_create_timeout
                and general_attempt <= self.settings.retry_max
                and is_retryable_error(err)
            )
            if use_general_retry:
                retry_kind = "general"
                retry_message = err
                retry_delay_ms = int(getattr(self.settings, "retry_delay_ms", 0) or 0)
                retry_attempt = general_attempt
                self._emit_audit(
                    stage="RETRY",
                    group_key=group_key,
                    batch_seq=batch_seq,
                    attempt=retry_attempt,
                    result="RETRY",
                    message=retry_message,
                    picking_id=pick_acc.picking_id,
                )
                self._publish_progress(stage="RETRY", current=batch_seq, total=max(batch_seq, 1), message=f"Retry batch {batch_seq}: {retry_message}")
                if self.perf_reporter is not None:
                    self.perf_reporter.emit(
                        event_type="retry",
                        stage="RETRY",
                        severity="warn",
                        cause_code="retry_storm",
                        rpc_model=normalize_text(getattr(self.rpc.call_context, "model", "")),
                        rpc_method=normalize_text(getattr(self.rpc.call_context, "method", "")),
                        duration_ms=int(getattr(self.rpc, "last_duration_ms", 0) or 0),
                        batch_seq=batch_seq,
                        row_number=0,
                        message=retry_message,
                        http_status=int(getattr(self.rpc, "last_http_status", 0) or 0),
                        attempt=retry_attempt,
                        details={"retry_kind": retry_kind},
                    )
                if retry_delay_ms > 0:
                    await asyncio.sleep(retry_delay_ms / 1000.0)
                continue
            break

        should_adaptive_split = (
            not last_error_is_create_timeout
            and
            len(batch_entries) >= max(self.settings.adaptive_batch_min * 2, 2)
            and ("error" in last_error.lower() or is_retryable_error(last_error))
        )
        if split_depth < 8 and should_adaptive_split:
            middle = len(batch_entries) // 2
            left = batch_entries[:middle]
            right = batch_entries[middle:]
            if left and right:
                adaptive_split_count += 1
                self._emit_audit(stage="SPLIT", group_key=group_key, batch_seq=batch_seq, result="SPLIT", message=f"split_depth={split_depth} left={len(left)} right={len(right)}", picking_id=0)
                self._publish_progress(stage="SPLIT", current=batch_seq, total=max(batch_seq, 1), message=f"Split batch {batch_seq}: {len(left)} + {len(right)}")
                if self.perf_reporter is not None:
                    self.perf_reporter.emit(
                        event_type="split",
                        stage="SPLIT",
                        severity="warn",
                        cause_code="retry_storm",
                        rpc_model=normalize_text(getattr(self.rpc.call_context, "model", "")),
                        rpc_method=normalize_text(getattr(self.rpc.call_context, "method", "")),
                        duration_ms=int(getattr(self.rpc, "last_duration_ms", 0) or 0),
                        batch_seq=batch_seq,
                        row_number=0,
                        message=f"split_depth={split_depth} left={len(left)} right={len(right)}",
                        http_status=int(getattr(self.rpc, "last_http_status", 0) or 0),
                        attempt=split_depth + 1,
                    )
                ok_left, err_left, split_left = await self._process_batch_with_retry(
                    left,
                    company_id,
                    pick_type_id,
                    src_loc_id,
                    dest_loc_id,
                    date_done_utc,
                    precheck_error,
                    dry_run,
                    group_key,
                    batch_seq,
                    on_batch_success=on_batch_success,
                    split_depth=split_depth + 1,
                    split_branch=f"{split_branch}L",
                )
                ok_right, err_right, split_right = await self._process_batch_with_retry(
                    right,
                    company_id,
                    pick_type_id,
                    src_loc_id,
                    dest_loc_id,
                    date_done_utc,
                    precheck_error,
                    dry_run,
                    group_key,
                    batch_seq,
                    on_batch_success=on_batch_success,
                    split_depth=split_depth + 1,
                    split_branch=f"{split_branch}R",
                )
                return ok_left and ok_right, append_error(err_left, err_right), adaptive_split_count + split_left + split_right

        for entry in batch_entries:
            entry.row.mark_error(last_error or "Gagal tanpa pesan.")
            self._emit_audit(stage="ROW_ERROR", row_number=entry.row.row_number, group_key=group_key, batch_seq=batch_seq, result="ERROR", message=last_error or "Gagal tanpa pesan.", picking_id=0)
        return False, last_error, adaptive_split_count

    async def _submit_picking_batch(
        self,
        pick_acc: PickingAccumulator,
        company_id: int,
        pick_type_id: int,
        src_loc_id: int,
        dest_loc_id: int,
        move_commands: List[List[Any]],
        date_done_utc: str,
        dry_run: bool,
        create_origin: str = "",
    ) -> tuple[bool, str]:
        _ = date_done_utc
        if pick_type_id <= 0:
            return False, "Operation Type/picking_type_id belum valid."
        if src_loc_id <= 0:
            return False, "Source Location/location_id belum valid."
        if dest_loc_id <= 0:
            return False, "Destination Location/location_dest_id belum valid."

        if dry_run:
            pick_acc.picking_id = self.dry_run_pick_seq
            self.dry_run_pick_seq += 1
            pick_acc.picking_name = f"DRYRUN/PICK/{pick_acc.picking_id}"
            return True, ""

        context = build_company_context(company_id)
        values = {
            "picking_type_id": pick_type_id,
            "location_id": src_loc_id,
            "location_dest_id": dest_loc_id,
            "company_id": company_id,
            "move_ids": move_commands,
        }
        if normalize_text(create_origin):
            values["origin"] = normalize_text(create_origin)
        self._emit_audit(
            stage="PICKING_CREATE_START",
            batch_seq=self.batch_seq,
            result="START",
            message=f"moves={len(move_commands)} origin={normalize_text(values.get('origin')) or '-'}",
            picking_id=0,
        )
        self._publish_progress(
            stage="PICKING_CREATE",
            current=self.batch_seq,
            total=max(self.batch_seq, 1),
            message=f"Create picking untuk {len(move_commands)} move",
        )
        try:
            pick_id = await self.rpc.create(model="stock.picking", values=values, context=context, stage="BATCH_CREATE")
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)
        if pick_id <= 0:
            return False, "Create picking gagal: result=0"
        pick_acc.picking_id = pick_id
        pick_acc.picking_name = await self._read_picking_name(pick_id, context) or str(pick_id)
        self._emit_audit(
            stage="PICKING_CREATE_DONE",
            batch_seq=self.batch_seq,
            result="OK",
            message=f"picking={pick_acc.picking_name}",
            picking_id=pick_acc.picking_id,
        )
        return True, ""

    async def _cleanup_failed_picking(
        self,
        pick_id: int,
        company_id: int,
        group_key: str,
        batch_seq: int,
    ) -> str:
        policy = normalize_text(getattr(self.settings, "validate_cleanup_policy", "cancel_then_keep")).lower() or "cancel_then_keep"
        if pick_id <= 0 or policy != "cancel_then_keep":
            return ""

        context = build_company_context(company_id)
        rows = await self.rpc.read(
            model="stock.picking",
            ids=[pick_id],
            fields=["id", "name", "state"],
            context=context,
            stage="PICKING_CLEANUP",
        )
        if not rows:
            return "Cleanup dilewati: picking tidak ditemukan."

        rec = rows[0]
        pick_name = normalize_text(rec.get("name")) or str(pick_id)
        state = normalize_text(rec.get("state")).lower()
        if state in {"done", "cancel"}:
            message = f"Cleanup dilewati: picking {pick_name} state={state}."
            self._emit_audit(
                stage="PICKING_CLEANUP_SKIP",
                group_key=group_key,
                batch_seq=batch_seq,
                result="WARN",
                message=message,
                picking_id=pick_id,
            )
            return message

        self._emit_audit(
            stage="PICKING_CLEANUP_START",
            group_key=group_key,
            batch_seq=batch_seq,
            result="START",
            message=f"policy={policy} picking={pick_name}",
            picking_id=pick_id,
        )
        try:
            await self.rpc.execute_kw(
                model="stock.picking",
                method="action_cancel",
                args=[[pick_id]],
                kwargs={"context": context},
                stage="PICKING_CLEANUP",
            )
        except Exception as exc:  # noqa: BLE001
            message = f"Cleanup gagal action_cancel picking {pick_name}: {exc}"
            self._emit_audit(
                stage="PICKING_CLEANUP_FAIL",
                group_key=group_key,
                batch_seq=batch_seq,
                result="ERROR",
                message=message,
                picking_id=pick_id,
            )
            return message

        verify_rows = await self.rpc.read(
            model="stock.picking",
            ids=[pick_id],
            fields=["state"],
            context=context,
            stage="PICKING_CLEANUP",
        )
        final_state = normalize_text((verify_rows[0] if verify_rows else {}).get("state")).lower() or "unknown"
        message = f"Cleanup selesai: picking {pick_name} state={final_state}."
        self._emit_audit(
            stage="PICKING_CLEANUP_DONE",
            group_key=group_key,
            batch_seq=batch_seq,
            result="OK",
            message=message,
            picking_id=pick_id,
        )
        return message

    async def _read_picking_name(self, pick_id: int, context: Dict[str, Any]) -> str:
        return await read_picking_name(self.rpc, pick_id, context)

    async def _enforce_stj_cost_gate(self, rows: List[ItemJournalRow]) -> None:
        for row in rows:
            row_result = normalize_text(row.result)
            if not row_result or row_result in {"[Error]", "[Stopped]"}:
                continue
            if normalize_text(row.stj):
                continue

            row_number = int(row.row_number or 0)
            journal_expected, journal_reason = await self._should_expect_journal_for_row(row_number)
            self._row_journal_expected[row_number] = journal_expected
            self._row_journal_reason[row_number] = journal_reason

            if not journal_expected:
                reason = f"STJ tidak diwajibkan: {journal_reason}"
                row.append_error(reason)
                self._stj_not_expected_rows.add(row_number)
                self._emit_audit(
                    stage="STJ_NOT_EXPECTED",
                    row_number=row_number,
                    result="WARN",
                    message=reason,
                    picking_id=0,
                )
                continue

            standard_price = self._row_cost_by_number.get(row_number)
            if standard_price is not None and abs(float(standard_price)) <= 1e-9:
                reason = "STJ kosong diizinkan: cost item 0"
                row.append_error(reason)
                self._stj_cost_zero_reason_rows.add(row_number)
                self._emit_audit(
                    stage="STJ_COST_ZERO_EXCEPTION",
                    row_number=row_number,
                    result="WARN",
                    message=reason,
                    picking_id=0,
                )
                continue

            if standard_price is None:
                fail_message = append_error(
                    "STJ tidak ditemukan dan standard_price produk tidak tersedia.",
                    journal_reason,
                )
            else:
                fail_message = (
                    f"STJ tidak ditemukan dan cost item > 0 "
                    f"(standard_price={float(standard_price):.6f}). "
                    f"Alasan ekspektasi jurnal: {journal_reason}"
                )
            row.mark_error(fail_message)
            self._emit_audit(
                stage="STJ_REQUIRED_FAIL",
                row_number=row_number,
                result="ERROR",
                message=fail_message,
                picking_id=0,
            )

    async def _should_expect_journal_for_row(self, row_number: int) -> tuple[bool, str]:
        mode = normalize_text(getattr(self.settings, "journal_expectation_mode", "hybrid")).lower() or "hybrid"
        if mode != "hybrid":
            return True, f"journal_expectation_mode={mode}"

        spec = self._row_specs_by_number.get(int(row_number))
        if not isinstance(spec, dict):
            return True, "metadata baris tidak tersedia"

        company_id = int(spec.get("company_id") or 0)
        product_id = int(spec.get("product_id") or 0)
        src_loc_id = int(spec.get("src_loc_id") or 0)
        dest_loc_id = int(spec.get("dest_loc_id") or 0)
        if company_id <= 0 or product_id <= 0 or src_loc_id <= 0 or dest_loc_id <= 0:
            return True, "metadata product/location tidak lengkap"

        src_usage = await self._get_location_usage(company_id, src_loc_id)
        dest_usage = await self._get_location_usage(company_id, dest_loc_id)
        valuation = await self._get_product_valuation(company_id, product_id)

        reasons: List[str] = []
        if src_usage == "internal" and dest_usage == "internal":
            reasons.append("source/destination usage sama-sama internal")
        if valuation and valuation != "real_time":
            reasons.append(f"property_valuation={valuation}")
        if reasons:
            return False, "; ".join(reasons)
        if not valuation:
            return True, "property_valuation tidak terbaca"
        return True, f"property_valuation={valuation}, usage={src_usage}->{dest_usage}"

    async def _get_location_usage(self, company_id: int, location_id: int) -> str:
        key = f"{int(company_id)}|{int(location_id)}"
        if key in self._location_usage_cache:
            return self._location_usage_cache[key]

        usage = ""
        try:
            rows = await self.rpc.read(
                model="stock.location",
                ids=[int(location_id)],
                fields=["id", "usage"],
                context=build_company_context(company_id),
                stage="STJ_EXPECTATION",
            )
            if rows:
                usage = normalize_text(rows[0].get("usage")).lower()
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("Gagal membaca stock.location.usage (id=%s): %s", location_id, exc)
        self._location_usage_cache[key] = usage
        return usage

    async def _get_product_valuation(self, company_id: int, product_id: int) -> str:
        key = f"{int(company_id)}|{int(product_id)}"
        if key in self._product_valuation_cache:
            return self._product_valuation_cache[key]

        valuation = ""
        try:
            prod_rows = await self.rpc.read(
                model="product.product",
                ids=[int(product_id)],
                fields=["id", "categ_id"],
                context=build_company_context(company_id),
                stage="STJ_EXPECTATION",
            )
            if prod_rows:
                categ_id = extract_many2one_id(prod_rows[0].get("categ_id"))
                if categ_id > 0:
                    categ_rows = await self.rpc.read(
                        model="product.category",
                        ids=[int(categ_id)],
                        fields=["id", "property_valuation"],
                        context=build_company_context(company_id),
                        stage="STJ_EXPECTATION",
                    )
                    if categ_rows:
                        valuation = normalize_text(categ_rows[0].get("property_valuation")).lower()
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("Gagal membaca valuation product_id=%s: %s", product_id, exc)

        self._product_valuation_cache[key] = valuation
        return valuation

    def _build_transfer_results(self, rows: List[ItemJournalRow]) -> tuple[List[Dict[str, Any]], List[str], List[str]]:
        transfer_results: List[Dict[str, Any]] = []
        transfer_refs: set[str] = set()
        journal_refs: set[str] = set()

        sorted_rows = sorted(rows, key=lambda item: int(item.row_number or 0))
        for row in sorted_rows:
            row_result = normalize_text(row.result)
            transfer_ref = row_result if row_result and not row_result.startswith("[") else ""
            row_journal_refs = _split_semicolon_refs(row.stj)
            for ref in row_journal_refs:
                journal_refs.add(ref)
            if transfer_ref:
                transfer_refs.add(transfer_ref)

            if row_result == "[Error]":
                journal_status = "error"
            elif row_result == "[Stopped]":
                journal_status = "stopped"
            elif not row_result:
                journal_status = "pending"
            elif row_journal_refs:
                journal_status = "ok"
            elif int(row.row_number or 0) in self._stj_not_expected_rows:
                journal_status = "not_expected"
            elif int(row.row_number or 0) in self._stj_cost_zero_reason_rows:
                journal_status = "cost_zero_exception"
            else:
                journal_status = "missing"

            row_number = int(row.row_number or 0)

            transfer_results.append(
                {
                    "row_number": row_number,
                    "transfer_ref": transfer_ref,
                    "journal_refs": row_journal_refs,
                    "journal_status": journal_status,
                    "journal_expected": bool(self._row_journal_expected.get(row_number, True)),
                    "journal_reason": normalize_text(self._row_journal_reason.get(row_number, "")),
                    "error": normalize_text(row.error),
                }
            )

        return transfer_results, sorted(transfer_refs), sorted(journal_refs)

    def _is_journal_failure(self, result: Dict[str, Any]) -> bool:
        status = normalize_text(result.get("journal_status")).lower()
        if status in {"missing", "error"}:
            return True
        error_text = normalize_text(result.get("error")).lower()
        return "stj_timeout_warning[" in error_text

    def _aggregate_journal_summary(
        self,
        transfer_results: List[Dict[str, Any]],
    ) -> tuple[Dict[str, int], Dict[str, Any]]:
        summary = {
            "row_ok": 0,
            "row_failed": 0,
            "row_not_required": 0,
            "row_other": 0,
            "transfer_ok": 0,
            "transfer_failed": 0,
            "transfer_not_required": 0,
            "transfer_other": 0,
        }
        transfer_state: Dict[str, Dict[str, bool]] = {}
        failed_rows: List[int] = []
        failed_transfer_refs: List[str] = []

        for index, result in enumerate(transfer_results, start=1):
            row_number = int(result.get("row_number") or 0)
            status = normalize_text(result.get("journal_status")).lower()
            is_failed = self._is_journal_failure(result)
            is_not_required = status in {"not_expected", "cost_zero_exception"}
            is_ok = status == "ok"

            if is_failed:
                summary["row_failed"] += 1
                if row_number > 0:
                    failed_rows.append(row_number)
            elif is_ok:
                summary["row_ok"] += 1
            elif is_not_required:
                summary["row_not_required"] += 1
            else:
                summary["row_other"] += 1

            transfer_ref = normalize_text(result.get("transfer_ref"))
            transfer_key = transfer_ref or (f"ROW-{row_number}" if row_number > 0 else f"UNKNOWN-{index}")
            bucket = transfer_state.setdefault(
                transfer_key,
                {"ok": False, "failed": False, "not_required": False, "other": False},
            )
            if is_failed:
                bucket["failed"] = True
                if transfer_ref and transfer_ref not in failed_transfer_refs:
                    failed_transfer_refs.append(transfer_ref)
            elif is_ok:
                bucket["ok"] = True
            elif is_not_required:
                bucket["not_required"] = True
            else:
                bucket["other"] = True

        for bucket in transfer_state.values():
            if bucket["failed"]:
                summary["transfer_failed"] += 1
            elif bucket["ok"]:
                summary["transfer_ok"] += 1
            elif bucket["not_required"]:
                summary["transfer_not_required"] += 1
            else:
                summary["transfer_other"] += 1

        details = {
            "row_total": len(transfer_results),
            "transfer_total": len(transfer_state),
            "failed_rows_count": len(failed_rows),
            "failed_rows_sample": failed_rows[:20],
            "failed_transfer_refs_sample": failed_transfer_refs[:20],
        }
        return summary, details

    @staticmethod
    def _format_preview_qty(value: Any) -> str:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return normalize_text(value)
        if number.is_integer():
            return str(int(number))
        return f"{number:.2f}".rstrip("0").rstrip(".")

    def _set_active_transfer_context(
        self,
        *,
        company_name: str | None = None,
        src: str | None = None,
        dest: str | None = None,
        item_count: int | None = None,
        reset_items: bool = False,
    ) -> None:
        if reset_items:
            self._active_transfer_items = []
        if not self._active_transfer_context:
            self._active_transfer_context = {
                "company_name": "",
                "src": "",
                "dest": "",
                "item_count": 0,
            }
        if company_name is not None:
            self._active_transfer_context["company_name"] = normalize_text(company_name)
        if src is not None:
            self._active_transfer_context["src"] = normalize_text(src)
        if dest is not None:
            self._active_transfer_context["dest"] = normalize_text(dest)
        if item_count is not None:
            self._active_transfer_context["item_count"] = max(0, int(item_count or 0))
        elif self._active_transfer_items:
            self._active_transfer_context["item_count"] = len(self._active_transfer_items)

    def _append_active_transfer_item(self, *, product_name: str, qty: Any) -> None:
        name = normalize_text(product_name) or "item"
        self._active_transfer_items.append({"name": name, "qty": qty})
        self._set_active_transfer_context(item_count=len(self._active_transfer_items))

    def _build_sync_wait_preview(self) -> tuple[List[str], List[str], List[str], int]:
        rendered: List[str] = []
        for item in self._active_transfer_items:
            name = normalize_text(item.get("name")) or "item"
            qty_text = self._format_preview_qty(item.get("qty"))
            rendered.append(f"{name} x{qty_text}" if qty_text else name)
        total_items = len(rendered)
        if total_items > 10:
            return rendered, rendered[:5], rendered[-5:], total_items
        return rendered, rendered, [], total_items

    def _build_sync_wait_payload(self, *, message: str, slow_level: str, elapsed_sec: int, batch_seq: int) -> Dict[str, Any]:
        context = dict(self._active_transfer_context)
        preview_items, preview_top, preview_bottom, preview_count = self._build_sync_wait_preview()
        item_count = max(0, int(context.get("item_count") or 0))
        if item_count <= 0:
            item_count = preview_count
        return {
            "stage": "SYNC_WAIT",
            "current": batch_seq,
            "total": max(1, batch_seq),
            "message": message,
            "slow_level": slow_level,
            "elapsed_sec": elapsed_sec,
            "company_name": normalize_text(context.get("company_name")) or "-",
            "src": normalize_text(context.get("src")) or "-",
            "dest": normalize_text(context.get("dest")) or "-",
            "item_count": item_count,
            "preview_items": preview_items,
            "preview_top": preview_top,
            "preview_bottom": preview_bottom,
        }

    def _sync_wait_context_fingerprint(self, payload: Dict[str, Any]) -> str:
        fingerprint_payload = {
            "company_name": normalize_text(payload.get("company_name")),
            "src": normalize_text(payload.get("src")),
            "dest": normalize_text(payload.get("dest")),
            "item_count": int(payload.get("item_count") or 0),
            "preview_items": list(payload.get("preview_items") or []),
            "preview_top": list(payload.get("preview_top") or []),
            "preview_bottom": list(payload.get("preview_bottom") or []),
        }
        digest = hashlib.md5(
            json.dumps(fingerprint_payload, ensure_ascii=True, sort_keys=True).encode("utf-8")
        )
        return digest.hexdigest()

    def _should_emit_sync_wait(self, *, slow_level: str, context_fingerprint: str, now_monotonic: float) -> bool:
        level = normalize_text(slow_level).lower()
        if level not in {"info", "warn"}:
            return False
        context_changed = context_fingerprint != self._last_sync_wait_context_fingerprint
        level_changed = level != self._last_ui_slow_level

        if level == "warn":
            should_emit = (
                level_changed
                or self._last_sync_wait_warn_at <= 0.0
                or (now_monotonic - self._last_sync_wait_warn_at) >= 60.0
                or context_changed
            )
            if not should_emit:
                return False
            self._last_sync_wait_warn_at = now_monotonic
        else:
            should_emit = (
                level_changed
                or self._last_sync_wait_info_at <= 0.0
                or (now_monotonic - self._last_sync_wait_info_at) >= 30.0
                or context_changed
            )
            if not should_emit:
                return False
            self._last_sync_wait_info_at = now_monotonic

        self._last_ui_slow_level = level
        self._last_sync_wait_context_fingerprint = context_fingerprint
        return True

    def _narrate_transfer_results(self, transfer_results: List[Dict[str, Any]]) -> None:
        summary, details = self._aggregate_journal_summary(transfer_results)
        if summary["row_failed"] > 0 or summary["transfer_failed"] > 0:
            self.narrator.warning("JOURNAL_SUMMARY", **summary)
        else:
            self.narrator.info("JOURNAL_SUMMARY", **summary)
        self.narrator.technical("JOURNAL_SUMMARY_DETAIL", **details)

    def _start_perf_monitor(self) -> None:
        if self.perf_reporter is None:
            return
        if self._perf_heartbeat_task is not None and not self._perf_heartbeat_task.done():
            return
        self._perf_shutdown_event = asyncio.Event()
        self._perf_heartbeat_task = asyncio.create_task(self._perf_heartbeat_loop())

    async def _stop_perf_monitor(self) -> None:
        task = self._perf_heartbeat_task
        if task is None:
            return
        self._perf_shutdown_event.set()
        try:
            await task
        except Exception:
            return
        finally:
            self._perf_heartbeat_task = None

    async def _perf_heartbeat_loop(self) -> None:
        while not self._perf_shutdown_event.is_set():
            interval_sec = max(1, int(self.settings.perf_heartbeat_sec or 1))
            try:
                await asyncio.wait_for(self._perf_shutdown_event.wait(), timeout=interval_sec)
                if self._perf_shutdown_event.is_set():
                    break
            except TimeoutError:
                pass
            if self.perf_reporter is None:
                continue
            alert = self.perf_reporter.check_stall()
            elapsed_sec = int(self.perf_reporter.get_stall_elapsed_sec() or 0)
            feedback = build_slow_feedback(elapsed_sec)
            if feedback is None:
                self._last_ui_slow_level = ""
                self._last_sync_wait_info_at = 0.0
                self._last_sync_wait_warn_at = 0.0
                self._last_sync_wait_context_fingerprint = ""
                continue
            batch_seq = int((alert or {}).get("batch_seq", self.batch_seq) or self.batch_seq or 0)
            payload = self._build_sync_wait_payload(
                message=feedback.message,
                slow_level=feedback.level,
                elapsed_sec=elapsed_sec,
                batch_seq=batch_seq,
            )
            context_fingerprint = self._sync_wait_context_fingerprint(payload)
            if not self._should_emit_sync_wait(
                slow_level=feedback.level,
                context_fingerprint=context_fingerprint,
                now_monotonic=time.monotonic(),
            ):
                continue

            self.run_control.publish_progress(payload)
            ui_message = stage_to_business_message("SYNC_WAIT", payload)
            if feedback.level == "warn":
                self.logger.warning(ui_message)
            else:
                self.logger.info(ui_message)

    def _record_stage_span(self, stage: str, started_at: float, message: str) -> None:
        if self.perf_reporter is None:
            return
        self.perf_reporter.record_stage_span(
            stage=stage,
            started_perf=started_at,
            ended_perf=time.perf_counter(),
            message=message,
            batch_seq=self.batch_seq,
            row_number=self._perf_row_number,
        )

    def _publish_progress(
        self,
        stage: str,
        current: int,
        total: int,
        message: str,
        stats: Dict[str, Any] | None = None,
        started_at: float | None = None,
        extra: Dict[str, Any] | None = None,
    ) -> None:
        self._perf_stage = stage
        if current > 0:
            self._perf_row_number = current
        if self.perf_reporter is not None:
            self.perf_reporter.notify_progress(stage=stage, batch_seq=self.batch_seq, row_number=self._perf_row_number)
        payload: Dict[str, Any] = {"stage": stage, "current": current, "total": total, "message": message}
        if stats is not None:
            payload["stats"] = dict(stats)
        if extra is not None:
            payload.update(dict(extra))
        if started_at is not None and current > 0 and total > 0:
            elapsed = max(time.perf_counter() - started_at, 0.001)
            rate_per_sec = current / elapsed
            eta_sec = int((total - current) / rate_per_sec) if rate_per_sec > 0 else 0
            payload["rows_per_min"] = round(rate_per_sec * 60.0, 2)
            payload["eta_sec"] = max(0, eta_sec)
        self.run_control.publish_progress(payload)

    def _emit_audit(
        self,
        stage: str,
        row_number: int = 0,
        group_key: str = "",
        batch_seq: int = 0,
        model: str = "",
        method: str = "",
        attempt: int = 0,
        result: str = "",
        message: str = "",
        picking_id: int = 0,
    ) -> None:
        if normalize_text(stage).upper() == "ROW_ERROR":
            row_no = int(row_number or 0)
            row_obj = self._rows_by_number.get(row_no)
            product_name = "-"
            company = "-"
            if row_obj is not None:
                product_name = (
                    normalize_text(getattr(row_obj, "prod_key", ""))
                    or normalize_text(getattr(row_obj, "prod_id_text", ""))
                    or "-"
                )
                company = normalize_text(getattr(row_obj, "company_name", "")) or "-"
            transfer_ctx = {
                "row_number": row_no,
                "product_name": product_name,
                "company_name": company,
                "error_summary": normalize_text(message),
            }
            self._publish_progress(
                stage="TRANSFER_ERROR",
                current=max(0, int(self._perf_row_number or 0)),
                total=max(1, int(self._active_rows_total or 1)),
                message=stage_to_business_message("TRANSFER_ERROR", transfer_ctx),
                extra=transfer_ctx,
            )

        if self.audit_sink is None:
            return
        event = AuditEvent.now(
            run_id=self.run_id,
            row_number=row_number,
            group_key=group_key,
            batch_seq=batch_seq,
            stage=stage,
            model=model or normalize_text(getattr(self.rpc.call_context, "model", "")),
            method=method or normalize_text(getattr(self.rpc.call_context, "method", "")),
            attempt=attempt,
            http_status=int(getattr(self.rpc, "last_http_status", 0) or 0),
            duration_ms=int(getattr(self.rpc, "last_duration_ms", 0) or 0),
            result=result,
            message=message,
            picking_id=picking_id,
        )
        self.audit_sink.emit(event)

    def _flush_audit_to_repo(self, repo: ItemJournalWorkbookRepo, force: bool = False) -> None:
        if self.audit_sink is None:
            return
        if not force and not self.audit_sink.should_flush():
            return
        events = self.audit_sink.pop_buffered_events()
        if not events:
            return
        repo.append_audit_events(event.to_dict() for event in events)
