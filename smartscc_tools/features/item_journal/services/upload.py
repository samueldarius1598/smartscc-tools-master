"""Async upload service for Item Journal to Odoo Internal Transfer."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
import hashlib
import json
import logging
import time
from typing import Any, Callable, Dict, List

from smartscc_tools.features.item_journal.config import RuntimeSettings
from smartscc_tools.features.item_journal.messaging import BusinessNarrator, build_slow_feedback, stage_to_business_message
from smartscc_tools.features.item_journal.observability.audit import AuditEvent, AuditSink
from smartscc_tools.features.item_journal.observability.perf import PerfReporter
from smartscc_tools.services.odoo.gateway import AsyncOdooJsonRpcClient, build_company_context, extract_many2one_id
from smartscc_tools.services.odoo.master_cache import SessionMasterCache, rpc_cache_scope
from smartscc_tools.features.item_journal.runtime import RunControl
from smartscc_tools.features.item_journal.services.date_ops import (
    DateSyncServiceAsync,
    LockDateServiceAsync,
    collect_active_company_ids,
    read_picking_name,
)
from smartscc_tools.features.item_journal.services.stj import StjServiceAsync
from smartscc_tools.features.item_journal.workbook import ItemJournalRow, ItemJournalWorkbookRepo
from smartscc_tools.features.item_journal.utils import (
    append_error,
    chunked,
    compute_hybrid_delay_ms,
    extract_http_status,
    is_retryable_error,
    normalize_text,
    parse_datetime_text,
)


@dataclass
class ProductInfo:
    product_id: int
    name: str
    uom_id: int
    standard_price: float | None = None
    is_storable: bool | None = None
    detailed_type: str = ""


@dataclass
class BatchEntry:
    row: ItemJournalRow
    move_command: List[Any]
    row_meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PickingAccumulator:
    picking_id: int = 0
    picking_name: str = ""


@dataclass
class PendingStjRecovery:
    pick_id: int
    company_id: int
    group_key: str
    batch_seq: int
    target_rows: List[ItemJournalRow]
    row_specs: Dict[int, Dict[str, Any]]
    origin_scope_key: str
    queued_at_monotonic: float
    deadline_at_monotonic: float
    next_retry_at_monotonic: float
    last_warning: str = ""
    attempts: int = 0


class GlobalPrecheckError(RuntimeError):
    def __init__(self, message: str, row_errors: Dict[int, List[str]] | None = None) -> None:
        super().__init__(message)
        self.row_errors: Dict[int, List[str]] = row_errors or {}


def _safe_float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        if isinstance(value, bool):
            return float(value)
        if isinstance(value, (int, float)):
            return float(value)
        text = str(value).strip()
        if not text:
            return None
        return float(text)
    except (TypeError, ValueError):
        return None


def _split_semicolon_refs(value: str) -> List[str]:
    refs: List[str] = []
    seen: set[str] = set()
    for item in normalize_text(value).split(";"):
        clean = normalize_text(item)
        if not clean:
            continue
        key = clean.lower()
        if key in seen:
            continue
        seen.add(key)
        refs.append(clean)
    return refs

from .upload_execution import UploadExecutionMixin
from .upload_precheck import UploadPrecheckMixin


class ItemJournalServiceAsync(UploadPrecheckMixin, UploadExecutionMixin):
    def __init__(
        self,
        rpc: AsyncOdooJsonRpcClient,
        settings: RuntimeSettings,
        logger: logging.Logger,
        narrator: BusinessNarrator | None = None,
        run_control: RunControl | None = None,
        audit_sink: AuditSink | None = None,
        run_id: str | None = None,
        perf_reporter: PerfReporter | None = None,
        master_cache: SessionMasterCache | None = None,
    ) -> None:
        self.rpc = rpc
        self.settings = settings
        self.logger = logger
        self.narrator = narrator or BusinessNarrator(user_logger=logger, technical_logger=logger)
        self.run_control = run_control or RunControl()
        self.audit_sink = audit_sink
        self.run_id = run_id or datetime.now().strftime("%Y%m%d_%H%M%S")
        self.perf_reporter = perf_reporter
        self.master_cache = master_cache

        self.lock_date_service = LockDateServiceAsync(rpc=rpc, logger=logger)
        self.date_sync_service = DateSyncServiceAsync(rpc=rpc, settings=settings, logger=logger)
        self.stj_service = StjServiceAsync(rpc=rpc, logger=logger, settings=settings)

        self.cache_pick_type: Dict[str, int] = {}
        self.cache_location: Dict[str, int] = {}
        self.cache_uom: Dict[str, int] = {}
        self.cache_product_by_id: Dict[str, ProductInfo] = {}
        self.cache_product_by_key: Dict[str, ProductInfo] = {}
        self.dry_run_pick_seq = 1
        self.batch_seq = 0
        self._perf_stage = "INIT"
        self._perf_row_number = 0
        self._perf_shutdown_event = asyncio.Event()
        self._perf_heartbeat_task: asyncio.Task | None = None
        self.pending_stj_recoveries: Dict[int, PendingStjRecovery] = {}
        self._stj_done_emitted_rows: set[int] = set()
        self._stj_recovery_now: Callable[[], float] = time.monotonic
        self._row_cost_by_number: Dict[int, float | None] = {}
        self._stj_cost_zero_reason_rows: set[int] = set()
        self._stj_not_expected_rows: set[int] = set()
        self._row_journal_expected: Dict[int, bool] = {}
        self._row_journal_reason: Dict[int, str] = {}
        self._row_specs_by_number: Dict[int, Dict[str, Any]] = {}
        self._location_usage_cache: Dict[str, str] = {}
        self._product_valuation_cache: Dict[str, str] = {}
        self._product_storeable_mode: str = ""
        self._product_query_fields: List[str] | None = None
        self._rows_by_number: Dict[int, ItemJournalRow] = {}
        self._last_ui_slow_level = ""
        self._active_rows_total = 0
        self._active_transfer_context: Dict[str, Any] = {}
        self._active_transfer_items: List[Dict[str, Any]] = []
        self._last_sync_wait_info_at = 0.0
        self._last_sync_wait_warn_at = 0.0
        self._last_sync_wait_context_fingerprint = ""

    def _master_cache_scope(self) -> tuple[str, str]:
        return rpc_cache_scope(self.rpc)

    def _master_cache_get_product_record(
        self,
        *,
        model: str,
        company_id: int,
        code: str,
        required_fields: List[str] | None = None,
    ) -> Dict[str, Any] | None:
        if self.master_cache is None:
            return None
        base_url, database = self._master_cache_scope()
        return self.master_cache.get_product_record(
            base_url=base_url,
            database=database,
            model=model,
            company_id=company_id,
            code=code,
            required_fields=required_fields,
        )

    def _master_cache_set_product_record(
        self,
        *,
        model: str,
        company_id: int,
        row: Dict[str, Any],
        fields: List[str] | None = None,
        code: str | None = None,
    ) -> None:
        if self.master_cache is None:
            return
        base_url, database = self._master_cache_scope()
        self.master_cache.set_product_record(
            base_url=base_url,
            database=database,
            model=model,
            company_id=company_id,
            row=row,
            fields=fields,
            code=code,
        )

    def _master_cache_get_location_id(self, *, company_id: int, location_name: str) -> int | None:
        if self.master_cache is None:
            return None
        base_url, database = self._master_cache_scope()
        return self.master_cache.get_location_id(
            base_url=base_url,
            database=database,
            company_id=company_id,
            location_name=location_name,
        )

    def _master_cache_set_location_id(self, *, company_id: int, location_name: str, location_id: int) -> None:
        if self.master_cache is None:
            return
        base_url, database = self._master_cache_scope()
        self.master_cache.set_location_id(
            base_url=base_url,
            database=database,
            company_id=company_id,
            location_name=location_name,
            location_id=location_id,
        )

    async def run_upload(self, repo: ItemJournalWorkbookRepo, dry_run: bool = False) -> Dict[str, Any]:
        start_time = time.perf_counter()
        self.pending_stj_recoveries.clear()
        self._stj_done_emitted_rows.clear()
        self._row_cost_by_number.clear()
        self._stj_cost_zero_reason_rows.clear()
        self._stj_not_expected_rows.clear()
        self._row_journal_expected.clear()
        self._row_journal_reason.clear()
        self._row_specs_by_number.clear()
        self._location_usage_cache.clear()
        self._product_valuation_cache.clear()
        self._product_storeable_mode = ""
        self._product_query_fields = None
        self._rows_by_number.clear()
        self._last_ui_slow_level = ""
        self._active_transfer_context = {}
        self._active_transfer_items = []
        self._last_sync_wait_info_at = 0.0
        self._last_sync_wait_warn_at = 0.0
        self._last_sync_wait_context_fingerprint = ""
        rows = repo.read_rows()
        if not rows:
            raise RuntimeError("Tidak ada data di sheet 'Item Journal'.")
        self._rows_by_number = {
            int(row.row_number or 0): row
            for row in rows
            if int(row.row_number or 0) > 0
        }

        active_rows = [row for row in rows if row.is_active()]
        if not active_rows:
            raise RuntimeError("Tidak ada data aktif untuk di-upload.")
        self._active_rows_total = len(active_rows)
        if not self.settings.auto_validate and not dry_run:
            raise RuntimeError(
                "Konfigurasi tidak kompatibel: AUTO_VALIDATE=true wajib untuk strict journal return "
                "(STJ_REQUIRED=true membutuhkan AUTO_VALIDATE=true)."
            )
        remap_mode = normalize_text(getattr(self.settings, "stj_remap_mode", "")).lower()
        if not dry_run and remap_mode != "conservative":
            old_mode = remap_mode or "-"
            self.settings.stj_remap_mode = "conservative"
            guard_message = (
                f"old_mode={old_mode} new_mode=conservative dry_run={dry_run} reason=production_guard"
            )
            self.logger.warning("CONFIG_GUARD_REMAP_FORCED | %s", guard_message)
            self._emit_audit(
                stage="CONFIG_GUARD_REMAP_FORCED",
                result="WARN",
                message=guard_message,
            )

        company_name = next(
            (
                normalize_text(item.company_name)
                for item in active_rows
                if normalize_text(item.company_name)
            ),
            "",
        )

        stats = {
            "company_name": company_name,
            "active_rows": len(active_rows),
            "groups": 0,
            "batches": 0,
            "rows_success": 0,
            "rows_error": 0,
            "rows_stopped": 0,
            "adaptive_split": 0,
            "dry_run": dry_run,
            "stopped": False,
        }
        self._emit_audit(stage="RUN_START", result="START", message=f"active_rows={len(active_rows)} dry_run={dry_run}")
        if self.perf_reporter is not None:
            self.perf_reporter.notify_progress(stage="RUN_START", batch_seq=0, row_number=0)
            self.perf_reporter.emit(
                event_type="run_start",
                stage="RUN_START",
                severity="info",
                cause_code="unknown",
                duration_ms=0,
                batch_seq=0,
                row_number=0,
                message=f"active_rows={len(active_rows)} dry_run={dry_run}",
            )
            self._start_perf_monitor()

        try:
            repo.write_status("STEP AUTH_CHECK: verifikasi login Odoo")
            self._publish_progress(stage="AUTH_CHECK", current=0, total=len(active_rows), message="Verifikasi login Odoo")
            auth_start = time.perf_counter()
            await self.rpc.ensure_login()
            self.narrator.info("CONNECTED_TO_ODOO")
            self._record_stage_span("AUTH_CHECK", auth_start, "Login Odoo berhasil")

            repo.write_status("STEP DATE_SYNC_PREFLIGHT: cek capability tanggal")
            self._publish_progress(stage="DATE_SYNC_PREFLIGHT", current=0, total=len(active_rows), message="Preflight sinkronisasi tanggal")
            preflight_start = time.perf_counter()
            await self.date_sync_service.initialize_capabilities()
            self._record_stage_span("DATE_SYNC_PREFLIGHT", preflight_start, "Capability tanggal siap")

            repo.write_status("STEP GLOBAL_PRECHECK: validasi tanggal, lock period, dan lokasi")
            self._publish_progress(stage="GLOBAL_PRECHECK", current=0, total=len(active_rows), message="Validasi global upload")
            precheck_start = time.perf_counter()
            try:
                precheck = await self._run_global_precheck(active_rows)
            except GlobalPrecheckError as exc:
                self._apply_precheck_failure_rows(
                    active_rows=active_rows,
                    row_errors=exc.row_errors,
                    stop_reason="Precheck upload gagal. Proses dihentikan sebelum create Internal Transfer.",
                )
                self._record_stage_span("GLOBAL_PRECHECK", precheck_start, "FAILED")
                self._emit_audit(stage="PRECHECK_FAIL", result="ERROR", message=str(exc))
                repo.write_status("FAILED GLOBAL_PRECHECK")

                stats["rows_success"] = 0
                stats["rows_error"] = 0
                stats["rows_stopped"] = 0
                for row in rows:
                    if row.result == "[Error]":
                        stats["rows_error"] += 1
                    elif row.result == "[Stopped]":
                        stats["rows_stopped"] += 1
                    elif normalize_text(row.result):
                        stats["rows_success"] += 1

                writeback_start = time.perf_counter()
                repo.write_rows(rows)
                self._record_stage_span("WRITEBACK", writeback_start, "Writeback hasil precheck gagal")
                self._flush_audit_to_repo(repo, force=True)

                elapsed = time.perf_counter() - start_time
                self._emit_audit(stage="RUN_DONE", result="DONE", message=f"t_total={elapsed:.2f}s")
                self._flush_audit_to_repo(repo, force=True)
                if self.perf_reporter is not None:
                    self.perf_reporter.emit(
                        event_type="run_done",
                        stage="RUN_DONE",
                        severity="info",
                        cause_code="unknown",
                        duration_ms=int(elapsed * 1000),
                        batch_seq=self.batch_seq,
                        row_number=0,
                        message=f"t_total={elapsed:.2f}s",
                        details={"stats": dict(stats)},
                    )
                    stats["perf_summary"] = self.perf_reporter.close()
                    stats["perf_summary_path"] = (
                        str(self.perf_reporter.summary_path)
                        if self.perf_reporter.summary_path is not None
                        else "(disabled)"
                    )
                    stats["perf_events_path"] = (
                        str(self.perf_reporter.events_path)
                        if self.perf_reporter.events_path is not None
                        else "(disabled)"
                    )
                transfer_results, transfer_refs, journal_refs = self._build_transfer_results(active_rows)
                stats["transfer_results"] = transfer_results
                stats["transfer_refs"] = transfer_refs
                stats["journal_refs"] = journal_refs
                self._narrate_transfer_results(transfer_results)
                return stats
            company_id: int = precheck["company_id"]
            self._record_stage_span("GLOBAL_PRECHECK", precheck_start, f"company_id={company_id}")
            self._emit_audit(stage="PRECHECK", result="OK", message=f"company_id={company_id}")

            repo.write_status("STEP PREFETCH_PRODUCT/UOM: prefetch master")
            self._publish_progress(stage="PREFETCH", current=0, total=len(active_rows), message="Prefetch product dan UoM")
            prefetch_start = time.perf_counter()
            await asyncio.gather(
                self._prefetch_products(active_rows, company_id),
                self._prefetch_uoms(active_rows),
            )
            self._record_stage_span("PREFETCH", prefetch_start, "Prefetch product/uom selesai")

            repo.write_status(f"STEP ROW_LOOP: proses baris upload ({len(active_rows)} rows aktif)")
            active_sorted = sorted(active_rows, key=lambda item: item.row_number)

            group_key = ""
            group_rows: List[ItemJournalRow] = []
            group_batch: List[BatchEntry] = []
            group_pick_ids: List[int] = []
            group_entries_by_picking_id: Dict[int, List[BatchEntry]] = {}
            group_origin_key_by_picking_id: Dict[int, str] = {}
            group_date_done_utc = ""
            group_company_id = 0
            group_pick_type_id = 0
            group_src_loc_id = 0
            group_dest_loc_id = 0
            group_error = ""
            group_batch_tail_signature: tuple[int, int, str, str, str] | None = None
            group_batch_deferred_at_limit = False
            stopped = False
            progress_current = 0

            async def flush_batch() -> bool:
                nonlocal group_batch_tail_signature, group_batch_deferred_at_limit, progress_current
                if not group_batch:
                    return True
                if self.run_control.is_stop_requested():
                    return False

                self.batch_seq += 1
                stats["batches"] += 1

                def register_batch_success(
                    picking_id: int,
                    picking_name: str,
                    entries_for_pick: List[BatchEntry],
                    origin_scope_key: str,
                ) -> None:
                    if picking_id <= 0 or not entries_for_pick:
                        return
                    if picking_id not in group_entries_by_picking_id:
                        group_entries_by_picking_id[picking_id] = []
                    group_entries_by_picking_id[picking_id].extend(entries_for_pick)
                    if normalize_text(origin_scope_key):
                        group_origin_key_by_picking_id[picking_id] = normalize_text(origin_scope_key)
                    if picking_id not in group_pick_ids:
                        group_pick_ids.append(picking_id)
                    transfer_ctx = {
                        "company_name": normalize_text(entries_for_pick[0].row.company_name),
                        "item_count": len(entries_for_pick),
                        "transfer_ref": normalize_text(picking_name),
                    }
                    self._publish_progress(
                        stage="TRANSFER_DONE",
                        current=max(0, int(progress_current)),
                        total=len(active_sorted),
                        message=stage_to_business_message("TRANSFER_DONE", transfer_ctx),
                        extra=transfer_ctx,
                    )

                transfer_ctx = {
                    "company_name": normalize_text(group_rows[0].company_name) if group_rows else (company_name or "-"),
                    "item_count": max(len(group_batch), len(self._active_transfer_items)),
                    "src": normalize_text(group_rows[0].src_loc) if group_rows else "",
                    "dest": normalize_text(group_rows[0].dest_loc) if group_rows else "",
                }
                self._set_active_transfer_context(
                    company_name=transfer_ctx.get("company_name"),
                    src=transfer_ctx.get("src"),
                    dest=transfer_ctx.get("dest"),
                    item_count=int(transfer_ctx.get("item_count") or 0),
                )
                self._publish_progress(
                    stage="TRANSFER_PROGRESS",
                    current=max(0, int(progress_current)),
                    total=len(active_sorted),
                    message=stage_to_business_message("TRANSFER_PROGRESS", transfer_ctx),
                    extra=transfer_ctx,
                )

                self._emit_audit(
                    stage="BATCH_SUBMIT",
                    group_key=group_key,
                    batch_seq=self.batch_seq,
                    result="START",
                    message=f"rows={len(group_batch)}",
                    picking_id=0,
                )
                success, error_text, adaptive_split = await self._process_batch_with_retry(
                    batch_entries=group_batch[:],
                    company_id=group_company_id,
                    pick_type_id=group_pick_type_id,
                    src_loc_id=group_src_loc_id,
                    dest_loc_id=group_dest_loc_id,
                    date_done_utc=group_date_done_utc,
                    precheck_error=group_error,
                    dry_run=dry_run,
                    group_key=group_key,
                    batch_seq=self.batch_seq,
                    on_batch_success=register_batch_success,
                )
                if adaptive_split:
                    stats["adaptive_split"] += adaptive_split
                if not success and error_text:
                    self.logger.error("Batch gagal final: %s", error_text)
                    self._emit_audit(
                        stage="BATCH_ERROR",
                        group_key=group_key,
                        batch_seq=self.batch_seq,
                        result="ERROR",
                        message=error_text,
                        picking_id=0,
                    )
                group_batch.clear()
                group_batch_tail_signature = None
                group_batch_deferred_at_limit = False
                self._flush_audit_to_repo(repo)
                return success

            async def finalize_group(allow_submit: bool) -> None:
                nonlocal group_key, group_rows, group_batch, group_pick_ids, group_entries_by_picking_id, group_origin_key_by_picking_id, group_date_done_utc
                nonlocal group_company_id, group_pick_type_id, group_src_loc_id, group_dest_loc_id, group_error
                nonlocal group_batch_tail_signature, group_batch_deferred_at_limit
                if not group_key:
                    return

                if allow_submit and group_batch:
                    await flush_batch()

                if self.settings.auto_validate and group_pick_ids:
                    for pick_id in group_pick_ids:
                        target_entries = [item for item in group_entries_by_picking_id.get(pick_id, []) if item.row.result != "[Error]"]
                        target_rows = [item.row for item in target_entries]
                        if not target_rows:
                            continue

                        if dry_run:
                            for row in target_rows:
                                row.append_error("Dry-run: validate/date sync/STJ dilewati.")
                            continue

                        self._emit_audit(
                            stage="PICKING_VALIDATE_START",
                            group_key=group_key,
                            batch_seq=self.batch_seq,
                            result="START",
                            message=f"picking_id={pick_id}",
                            picking_id=pick_id,
                        )
                        ok, err, warn = await self.date_sync_service.force_date_and_validate(
                            pick_id=pick_id,
                            company_id=group_company_id,
                            date_done_utc=group_date_done_utc,
                        )
                        if not ok:
                            cleanup_note = await self._cleanup_failed_picking(
                                pick_id=pick_id,
                                company_id=group_company_id,
                                group_key=group_key,
                                batch_seq=self.batch_seq,
                            )
                            for row in target_rows:
                                row.mark_error(f"Validate gagal: {err}")
                                if cleanup_note:
                                    row.append_error(cleanup_note)
                                self._emit_audit(
                                    stage="ROW_ERROR",
                                    row_number=row.row_number,
                                    group_key=group_key,
                                    batch_seq=self.batch_seq,
                                    result="ERROR",
                                    message=append_error(f"Validate gagal: {err}", cleanup_note),
                                    picking_id=pick_id,
                                )
                            continue

                        self._emit_audit(
                            stage="PICKING_VALIDATE_DONE",
                            group_key=group_key,
                            batch_seq=self.batch_seq,
                            result="OK",
                            message=f"picking_id={pick_id}",
                            picking_id=pick_id,
                        )
                        if warn:
                            for row in target_rows:
                                row.append_error(f"Validate warning: {warn}")

                        self._emit_audit(
                            stage="STJ_START",
                            group_key=group_key,
                            batch_seq=self.batch_seq,
                            result="START",
                            message=f"picking_id={pick_id}",
                            picking_id=pick_id,
                        )
                        stj_target_entries = [item for item in target_entries if item.row.result != "[Error]"]
                        stj_target_rows = [item.row for item in stj_target_entries]
                        row_specs = {
                            int(item.row.row_number): dict(item.row_meta)
                            for item in stj_target_entries
                            if item.row.row_number > 0
                        }
                        origin_scope_key = group_origin_key_by_picking_id.get(pick_id, "")
                        stj_warn = await self.stj_service.fill_stj_for_group(
                            pick_id=pick_id,
                            company_id=group_company_id,
                            target_rows=stj_target_rows,
                            row_specs=row_specs,
                            origin_scope_key=origin_scope_key,
                        )
                        self._emit_stj_mapping_events(
                            pick_id=pick_id,
                            group_key=group_key,
                            batch_seq=self.batch_seq,
                        )
                        missing_stj_rows = self._collect_missing_stj_rows(stj_target_rows)
                        required_missing_rows = await self._filter_required_stj_rows(missing_stj_rows)
                        if stj_warn and (not self.settings.stj_required or not required_missing_rows):
                            for row in stj_target_rows:
                                if row.result == "[Error]":
                                    continue
                                row.append_error(f"STJ warning: {stj_warn}")
                        self._emit_stj_done_rows(
                            rows=stj_target_rows,
                            group_key=group_key,
                            batch_seq=self.batch_seq,
                            pick_id=pick_id,
                        )
                        if self.settings.stj_required and required_missing_rows:
                            self._queue_stj_recovery(
                                pick_id=pick_id,
                                company_id=group_company_id,
                                group_key=group_key,
                                batch_seq=self.batch_seq,
                                target_entries=stj_target_entries,
                                origin_scope_key=origin_scope_key,
                                missing_rows=required_missing_rows,
                                initial_warning=stj_warn,
                            )

                    if self.pending_stj_recoveries:
                        await self._process_pending_stj_recoveries(repo=repo, wait_until_empty=False)

                group_key = ""
                group_rows = []
                group_batch = []
                group_pick_ids = []
                group_entries_by_picking_id = {}
                group_origin_key_by_picking_id = {}
                group_date_done_utc = ""
                group_company_id = 0
                group_pick_type_id = 0
                group_src_loc_id = 0
                group_dest_loc_id = 0
                group_error = ""
                group_batch_tail_signature = None
                group_batch_deferred_at_limit = False

            for index, row in enumerate(active_sorted, start=1):
                if self.run_control.is_stop_requested():
                    stopped = True
                    break

                progress_current = index
                self._perf_row_number = int(row.row_number or index)

                if index % max(1, self.settings.doevents_every) == 0:
                    repo.write_status(f"Upload Internal Transfer: {index}/{len(active_sorted)}")
                    transfer_ctx = {
                        "company_name": normalize_text(row.company_name) or (company_name or "-"),
                        "item_count": max(1, len(group_rows)),
                        "src": normalize_text(row.src_loc),
                        "dest": normalize_text(row.dest_loc),
                    }
                    self._set_active_transfer_context(
                        company_name=transfer_ctx.get("company_name"),
                        src=transfer_ctx.get("src"),
                        dest=transfer_ctx.get("dest"),
                        item_count=int(transfer_ctx.get("item_count") or 0),
                    )
                    self._publish_progress(
                        stage="TRANSFER_PROGRESS",
                        current=index,
                        total=len(active_sorted),
                        message=stage_to_business_message("TRANSFER_PROGRESS", transfer_ctx),
                        stats=stats,
                        started_at=start_time,
                        extra=transfer_ctx,
                    )

                self._emit_audit(stage="ROW_START", row_number=row.row_number, group_key=group_key, batch_seq=self.batch_seq)
                date_done_utc = self._format_date_to_odoo_utc(row)
                if not date_done_utc and self.settings.date_required:
                    row.mark_error("Tanggal Efektif (Kolom A) wajib diisi.")
                    self._emit_audit(stage="ROW_ERROR", row_number=row.row_number, group_key=group_key, batch_seq=self.batch_seq, result="ERROR", message="Tanggal Efektif (Kolom A) wajib diisi.")
                    continue
                if row.company_id <= 0:
                    row.mark_error("company_id kolom B wajib angka > 0.")
                    self._emit_audit(stage="ROW_ERROR", row_number=row.row_number, group_key=group_key, batch_seq=self.batch_seq, result="ERROR", message="company_id kolom B wajib angka > 0.")
                    continue
                if not normalize_text(row.op_type):
                    row.mark_error("Operation Type (Kolom D) wajib diisi.")
                    self._emit_audit(
                        stage="ROW_ERROR",
                        row_number=row.row_number,
                        group_key=group_key,
                        batch_seq=self.batch_seq,
                        result="ERROR",
                        message="Operation Type (Kolom D) wajib diisi.",
                    )
                    continue

                row_key = self._build_group_key(
                    company_id=row.company_id,
                    op_type=row.op_type,
                    src_loc=row.src_loc,
                    dest_loc=row.dest_loc,
                    date_done_utc=date_done_utc,
                )

                if group_key and row_key != group_key:
                    await finalize_group(allow_submit=True)

                if not group_key:
                    stats["groups"] += 1
                    group_key = row_key
                    group_date_done_utc = date_done_utc
                    group_company_id = row.company_id
                    group_rows = []
                    group_batch = []
                    group_pick_ids = []
                    group_entries_by_picking_id = {}
                    group_origin_key_by_picking_id = {}
                    group_error = ""
                    group_batch_tail_signature = None
                    group_batch_deferred_at_limit = False

                    transfer_ctx = {
                        "company_name": normalize_text(row.company_name) or (company_name or "-"),
                        "item_count": 1,
                        "src": normalize_text(row.src_loc),
                        "dest": normalize_text(row.dest_loc),
                    }
                    self._set_active_transfer_context(
                        company_name=transfer_ctx.get("company_name"),
                        src=transfer_ctx.get("src"),
                        dest=transfer_ctx.get("dest"),
                        item_count=int(transfer_ctx.get("item_count") or 0),
                        reset_items=True,
                    )
                    self._publish_progress(
                        stage="TRANSFER_START",
                        current=index,
                        total=len(active_sorted),
                        message=stage_to_business_message("TRANSFER_START", transfer_ctx),
                        extra=transfer_ctx,
                    )

                    group_pick_type_id = await self._get_picking_type_id_cached(op_type_name=row.op_type, company_id=row.company_id)
                    group_src_loc_id = await self._resolve_location_id_cached(row.src_loc, row.company_id)
                    group_dest_loc_id = await self._resolve_location_id_cached(row.dest_loc, row.company_id)
                    if group_pick_type_id <= 0:
                        if row.op_type:
                            group_error = append_error(group_error, f"Operation Type tidak ditemukan: {row.op_type}")
                        else:
                            group_error = append_error(group_error, "Operation Type (Kolom D) wajib diisi.")
                    if group_src_loc_id <= 0 and row.src_loc:
                        group_error = append_error(group_error, f"Source Location tidak ditemukan: {row.src_loc}")
                    if group_dest_loc_id <= 0 and row.dest_loc:
                        group_error = append_error(group_error, f"Destination Location tidak ditemukan: {row.dest_loc}")

                group_rows.append(row)

                product, product_err = await self._resolve_product_for_row(row, group_company_id)
                if product_err:
                    row.mark_error(product_err)
                    self._emit_audit(stage="ROW_ERROR", row_number=row.row_number, group_key=group_key, batch_seq=self.batch_seq, result="ERROR", message=product_err, picking_id=0)
                    continue
                if product is None or product.product_id <= 0:
                    row.mark_error("Product tidak ditemukan.")
                    self._emit_audit(stage="ROW_ERROR", row_number=row.row_number, group_key=group_key, batch_seq=self.batch_seq, result="ERROR", message="Product tidak ditemukan.", picking_id=0)
                    continue
                self._row_cost_by_number[int(row.row_number or 0)] = product.standard_price

                uom_id = await self._resolve_uom_id(row.uom, product.uom_id)
                if row.uom and uom_id <= 0:
                    row.mark_error(f"UoM tidak ditemukan: {row.uom}")
                    self._emit_audit(stage="ROW_ERROR", row_number=row.row_number, group_key=group_key, batch_seq=self.batch_seq, result="ERROR", message=f"UoM tidak ditemukan: {row.uom}", picking_id=0)
                    continue
                if not row.uom and uom_id <= 0:
                    row.mark_error("UoM produk tidak ditemukan.")
                    self._emit_audit(stage="ROW_ERROR", row_number=row.row_number, group_key=group_key, batch_seq=self.batch_seq, result="ERROR", message="UoM produk tidak ditemukan.", picking_id=0)
                    continue
                if float(row.qty or 0.0) <= 0.0:
                    row.mark_error(f"Qty harus > 0. Ditemukan: {row.qty}")
                    self._emit_audit(
                        stage="ROW_ERROR",
                        row_number=row.row_number,
                        group_key=group_key,
                        batch_seq=self.batch_seq,
                        result="ERROR",
                        message=f"Qty harus > 0. Ditemukan: {row.qty}",
                        picking_id=0,
                    )
                    continue

                row_signature = self._build_internal_batch_signature(
                    product_id=int(product.product_id or 0),
                    company_id=int(group_company_id or row.company_id or 0),
                    date_done_utc=group_date_done_utc or date_done_utc,
                    src_loc=row.src_loc,
                    dest_loc=row.dest_loc,
                )
                if (
                    group_batch
                    and len(group_batch) >= self.settings.batch_limit
                    and group_batch_tail_signature is not None
                ):
                    if row_signature != group_batch_tail_signature:
                        submitted = await flush_batch()
                        if not submitted and self.run_control.is_stop_requested():
                            stopped = True
                            break
                    elif not group_batch_deferred_at_limit:
                        group_batch_deferred_at_limit = True
                        self._emit_audit(
                            stage="BATCH_SIGNATURE_DEFERRED",
                            group_key=group_key,
                            batch_seq=self.batch_seq,
                            result="DEFERRED",
                            message=(
                                f"batch_limit={self.settings.batch_limit} deferred_rows={len(group_batch)} "
                                f"signature={row_signature[0]}|{row_signature[1]}|{row_signature[2]}|{row_signature[3]}|{row_signature[4]}"
                            ),
                            picking_id=0,
                        )

                line_name = product.name or row.prod_key or row.prod_id_text
                move_line_vals = {
                    "product_id": product.product_id,
                    "quantity": row.qty,
                    "location_id": group_src_loc_id,
                    "location_dest_id": group_dest_loc_id,
                    "product_uom_id": uom_id,
                    "company_id": group_company_id,
                }
                move_vals = {
                    "name": line_name,
                    "product_id": product.product_id,
                    "product_uom_qty": row.qty,
                    "location_id": group_src_loc_id,
                    "location_dest_id": group_dest_loc_id,
                    "company_id": group_company_id,
                    "product_uom": uom_id,
                    "move_line_ids": [[0, 0, move_line_vals]],
                }
                group_batch.append(
                    BatchEntry(
                        row=row,
                        move_command=[0, 0, move_vals],
                        row_meta={
                            "row_number": int(row.row_number or 0),
                            "product_id": int(product.product_id or 0),
                            "uom_id": int(uom_id or 0),
                            "src_loc_id": int(group_src_loc_id or 0),
                            "dest_loc_id": int(group_dest_loc_id or 0),
                            "qty": float(row.qty or 0.0),
                            "product_name": normalize_text(product.name) or line_name,
                            "standard_price": product.standard_price,
                        },
                    )
                )
                self._append_active_transfer_item(
                    product_name=normalize_text(product.name) or line_name,
                    qty=row.qty,
                )
                row_number = int(row.row_number or 0)
                if row_number > 0:
                    self._row_specs_by_number[row_number] = {
                        "row_number": row_number,
                        "company_id": int(group_company_id or row.company_id or 0),
                        "product_id": int(product.product_id or 0),
                        "src_loc_id": int(group_src_loc_id or 0),
                        "dest_loc_id": int(group_dest_loc_id or 0),
                    }
                group_batch_tail_signature = row_signature

            await finalize_group(allow_submit=not stopped)
            if not stopped and self.pending_stj_recoveries:
                await self._process_pending_stj_recoveries(repo=repo, wait_until_empty=True)
            if not dry_run:
                await self._enforce_stj_cost_gate(active_rows)

            if stopped:
                not_processed = [row for row in active_rows if not normalize_text(row.result)]
                if not_processed:
                    repo.mark_rows_stopped(not_processed, "Dihentikan user sebelum batch diproses.")
                    for row in not_processed:
                        self._emit_audit(stage="ROW_STOPPED", row_number=row.row_number, batch_seq=self.batch_seq, result="STOPPED", message="Dihentikan user sebelum batch diproses.")
                repo.write_status("STOPPED (run-to-batch-stop)")
                stats["stopped"] = True
                stats["rows_stopped"] = len(not_processed)
                self._emit_audit(stage="RUN_STOPPED", result="STOPPED", message=f"rows_stopped={len(not_processed)}")
            else:
                repo.write_status("DONE Upload Internal Transfer")

            stats["rows_success"] = 0
            stats["rows_error"] = 0
            stats["rows_stopped"] = 0
            for row in rows:
                if row.result == "[Error]":
                    stats["rows_error"] += 1
                elif row.result == "[Stopped]":
                    stats["rows_stopped"] += 1
                elif normalize_text(row.result):
                    stats["rows_success"] += 1

            writeback_start = time.perf_counter()
            repo.write_rows(rows)
            self._record_stage_span("WRITEBACK", writeback_start, "Writeback hasil ke workbook")
            self._flush_audit_to_repo(repo, force=True)

            elapsed = time.perf_counter() - start_time
            self.logger.info(
                "PERF summary: groups=%s, batches=%s, adaptive_split=%s, rows_success=%s, rows_error=%s, rows_stopped=%s, t_total=%.2fs",
                stats["groups"],
                stats["batches"],
                stats["adaptive_split"],
                stats["rows_success"],
                stats["rows_error"],
                stats["rows_stopped"],
                elapsed,
            )
            self._emit_audit(stage="RUN_DONE", result="DONE", message=f"t_total={elapsed:.2f}s")
            self._flush_audit_to_repo(repo, force=True)
            if self.perf_reporter is not None:
                self.perf_reporter.emit(
                    event_type="run_done",
                    stage="RUN_DONE",
                    severity="info",
                    cause_code="unknown",
                    duration_ms=int(elapsed * 1000),
                    batch_seq=self.batch_seq,
                    row_number=0,
                    message=f"t_total={elapsed:.2f}s",
                    details={"stats": dict(stats)},
                )
                stats["perf_summary"] = self.perf_reporter.close()
                stats["perf_summary_path"] = (
                    str(self.perf_reporter.summary_path)
                    if self.perf_reporter.summary_path is not None
                    else "(disabled)"
                )
                stats["perf_events_path"] = (
                    str(self.perf_reporter.events_path)
                    if self.perf_reporter.events_path is not None
                    else "(disabled)"
                )
            transfer_results, transfer_refs, journal_refs = self._build_transfer_results(active_rows)
            stats["transfer_results"] = transfer_results
            stats["transfer_refs"] = transfer_refs
            stats["journal_refs"] = journal_refs
            self._narrate_transfer_results(transfer_results)
            return stats
        finally:
            await self._stop_perf_monitor()
