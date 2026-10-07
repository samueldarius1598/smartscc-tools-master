"""Async service for Update Standard Cost workflow."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
import threading
from typing import Any, Callable, Sequence

import requests

from smartscc_tools.services.odoo.gateway import AsyncOdooJsonRpcClient, build_company_context
from smartscc_tools.services.odoo.master_cache import SessionMasterCache, rpc_cache_scope
from smartscc_tools.features.update_std_cost.config import AREA_GID, CODE_CHUNK_SIZE, LOG_SHEET_NAME, WRITE_CHUNK_SIZE
from smartscc_tools.features.update_std_cost.excel_repo import (
    PREP_DATA_START_ROW,
    PrepRow,
    append_log_summary,
    ensure_log_header,
    ensure_update_status_header,
    get_or_create_sheet,
    get_required_sheet,
    load_workbook_keep_vba,
    read_companies_from_client_config,
    read_note,
    read_preparation_rows,
    resolve_log_user,
    write_statuses,
)
from smartscc_tools.features.update_std_cost.gas_client import fetch_company_area_map
from smartscc_tools.features.update_std_cost.models import (
    UpdateStdCostModeSummary,
    UpdateStdCostProgressSnapshot,
    UpdateStdCostRowResult,
    UpdateStdCostRunRequest,
    UpdateStdCostRunSummary,
)
from smartscc_tools.features.update_std_cost.parsers import chunked, json_number, normalize_text, parse_number_smart_local


LogCallback = Callable[[str], None]
ProgressCallback = Callable[[UpdateStdCostProgressSnapshot], None]
StateCallback = Callable[[str, str, UpdateStdCostRunSummary | None, str], None]


@dataclass
class _ModeContext:
    request: UpdateStdCostRunRequest
    workbook: Any
    prep_rows: list[PrepRow]
    area_companies: dict[str, dict[str, int]]
    company_names: dict[str, str]
    log_user: str
    log_note: str
    total_modes: int
    mode_index: int


class UpdateStdCostServiceAsync:
    def __init__(
        self,
        *,
        rpc: AsyncOdooJsonRpcClient,
        logger,
        on_log: LogCallback | None = None,
        on_progress: ProgressCallback | None = None,
        on_state: StateCallback | None = None,
        master_cache: SessionMasterCache | None = None,
    ) -> None:
        self.rpc = rpc
        self.logger = logger
        self._on_log = on_log or (lambda _message: None)
        self._on_progress = on_progress or (lambda _snapshot: None)
        self._on_state = on_state or (lambda _status, _message, _summary, _database: None)
        self._stop_requested = threading.Event()
        self.master_cache = master_cache

    def request_stop(self) -> None:
        self._stop_requested.set()

    async def validate(self, request: UpdateStdCostRunRequest) -> UpdateStdCostRunSummary:
        return await self._run(request, force_dry_run=True, status_mode="validate")

    async def execute(self, request: UpdateStdCostRunRequest) -> UpdateStdCostRunSummary:
        return await self._run(request, force_dry_run=bool(request.dry_run), status_mode="execute")

    async def _run(
        self,
        request: UpdateStdCostRunRequest,
        *,
        force_dry_run: bool,
        status_mode: str,
    ) -> UpdateStdCostRunSummary:
        summary = UpdateStdCostRunSummary(
            requested_mode=request.mode,
            database=request.database,
            workbook_path=request.workbook_path,
            dry_run=force_dry_run,
        )
        self._emit_state("connected", f"Connected: {request.database}", None, request.database)
        self._emit_progress("Membuka workbook...", "", 0, 1)

        workbook_path = Path(request.workbook_path)
        workbook = load_workbook_keep_vba(str(workbook_path))
        prep_sheet = get_required_sheet(workbook, request.sheet_name)
        prep_rows, last_row = read_preparation_rows(prep_sheet)
        summary.total_rows = len(prep_rows)
        if last_row < PREP_DATA_START_ROW:
            message = "Tidak ada data Preparation Cost untuk diproses."
            self._emit_log(message)
            mode_summary = UpdateStdCostModeSummary(mode=request.mode, message=message)
            summary.mode_summaries.append(mode_summary)
            self._emit_state("completed", message, summary, request.database)
            return summary

        ensure_update_status_header(prep_sheet)
        log_user = resolve_log_user()
        log_note = read_note(prep_sheet)
        self._emit_log(f"Workbook loaded: {workbook_path}")
        self._emit_log("Mengambil mapping area-company dari GAS...")
        area_map = fetch_company_area_map(area_gid=AREA_GID, session=requests.Session())
        if not area_map:
            message = "Gagal membaca daftar company per area."
            mode_summary = UpdateStdCostModeSummary(mode=request.mode, fatal=True, message=message)
            summary.mode_summaries.append(mode_summary)
            self._emit_state("error", message, summary, request.database)
            return summary

        area_companies = self._build_area_companies(area_map)
        company_names = {str(company_id): name for company_id, name in read_companies_from_client_config(workbook)}
        modes = ["template", "variant"] if request.mode == "both" else [request.mode]

        for mode_index, mode in enumerate(modes):
            if self._stop_requested.is_set():
                summary.stopped = True
                break
            mode_context = _ModeContext(
                request=request,
                workbook=workbook,
                prep_rows=prep_rows,
                area_companies=area_companies,
                company_names=company_names,
                log_user=log_user,
                log_note=log_note,
                total_modes=len(modes),
                mode_index=mode_index,
            )
            self._emit_log(f"[{mode}] Mulai proses. Dry-run={force_dry_run}.")
            if mode == "template":
                mode_summary, mode_results = await self._run_template_mode(mode_context, dry_run=force_dry_run)
            else:
                mode_summary, mode_results = await self._run_variant_mode(mode_context, dry_run=force_dry_run)
            summary.mode_summaries.append(mode_summary)
            summary.results.extend(mode_results)
            if mode_summary.modified:
                workbook.save(str(workbook_path))
            if mode_summary.fatal:
                self._emit_state("error", mode_summary.message or f"Gagal mode {mode}.", summary, request.database)
                return summary
            if mode_summary.stopped:
                summary.stopped = True
                break

        workbook.close()
        final_status = "canceled" if summary.stopped else "completed"
        final_message = "Proses dihentikan." if summary.stopped else "Update Standard Cost selesai."
        self._emit_state(final_status, final_message, summary, request.database)
        return summary

    async def _run_template_mode(
        self,
        mode_context: _ModeContext,
        *,
        dry_run: bool,
    ) -> tuple[UpdateStdCostModeSummary, list[UpdateStdCostRowResult]]:
        summary = UpdateStdCostModeSummary(mode="template")
        return await self._run_mode(mode_context, summary=summary, model="product.template", dry_run=dry_run)

    async def _run_variant_mode(
        self,
        mode_context: _ModeContext,
        *,
        dry_run: bool,
    ) -> tuple[UpdateStdCostModeSummary, list[UpdateStdCostRowResult]]:
        summary = UpdateStdCostModeSummary(mode="variant")
        return await self._run_mode(mode_context, summary=summary, model="product.product", dry_run=dry_run)

    async def _run_mode(
        self,
        mode_context: _ModeContext,
        *,
        summary: UpdateStdCostModeSummary,
        model: str,
        dry_run: bool,
    ) -> tuple[UpdateStdCostModeSummary, list[UpdateStdCostRowResult]]:
        row_count = len(mode_context.prep_rows)
        status_arr = [""] * row_count
        row_target_count = [0] * row_count
        row_success_count = [0] * row_count
        row_has_issue = [False] * row_count
        row_area_key = [""] * row_count
        row_code = [""] * row_count
        row_price = [0.0] * row_count
        results: list[UpdateStdCostRowResult] = []
        code_set: dict[str, str] = {}

        for index, row in enumerate(mode_context.prep_rows):
            if self._stop_requested.is_set():
                summary.stopped = True
                break
            area_text = normalize_text(row.area)
            code_text = normalize_text(row.product_code)
            if not area_text or not code_text:
                continue
            try:
                price = parse_number_smart_local(row.value_per_unit)
            except ValueError:
                status_arr[index] = "No"
                row_has_issue[index] = True
                results.append(self._build_row_result(row, summary.mode, "NO", "Value per Unit tidak valid"))
                continue
            if price == 0:
                status_arr[index] = "No"
                row_has_issue[index] = True
                results.append(self._build_row_result(row, summary.mode, "NO", "Value per Unit tidak boleh 0"))
                continue
            area_key = area_text.lower()
            companies = mode_context.area_companies.get(area_key) or {}
            if not companies:
                status_arr[index] = "No"
                row_has_issue[index] = True
                summary.missing_areas += 1
                results.append(self._build_row_result(row, summary.mode, "NO", "Area belum punya mapping company"))
                continue
            row_area_key[index] = area_key
            row_code[index] = code_text
            row_price[index] = price
            code_set.setdefault(code_text.lower(), code_text)
            if model == "product.template":
                row_target_count[index] = len(companies)

        if summary.stopped:
            return summary, results
        if not code_set:
            summary.message = "Tidak ada data valid (Area, Product Code, Value per Unit)."
            return summary, results

        if model == "product.template":
            code_to_record_id, summary.duplicate_codes = await self._fetch_template_ids_by_default_code_global(
                list(code_set.values())
            )
        else:
            code_to_record_id = {}

        comp_code_to_prod_id: dict[str, dict[str, int]] = {}
        if model == "product.product":
            all_companies: dict[str, int] = {}
            for comp_map in mode_context.area_companies.values():
                for company_key, company_id in comp_map.items():
                    all_companies[company_key] = company_id

            async def fetch_company_products(company_key: str, company_id: int) -> tuple[str, dict[str, int]]:
                if self._stop_requested.is_set():
                    return company_key, {}
                return company_key, await self._fetch_product_ids_by_default_code_with_context(
                    list(code_set.values()),
                    company_id=company_id,
                )

            if self._stop_requested.is_set():
                summary.stopped = True
            elif all_companies:
                company_results = await asyncio.gather(
                    *(
                        fetch_company_products(company_key, company_id)
                        for company_key, company_id in all_companies.items()
                    )
                )
                if self._stop_requested.is_set():
                    summary.stopped = True
                comp_code_to_prod_id = dict(company_results)
            if summary.stopped:
                return summary, results

        batches: dict[str, dict[str, dict[str, int]]] = {}
        batch_rows: dict[str, dict[str, list[int]]] = {}

        for index, row in enumerate(mode_context.prep_rows):
            if row_has_issue[index]:
                continue
            price_key = json_number(row_price[index])
            companies = mode_context.area_companies.get(row_area_key[index], {})
            code_key = row_code[index].lower()

            if model == "product.template":
                record_id = int(code_to_record_id.get(code_key, 0))
                if record_id <= 0:
                    status_arr[index] = "No"
                    row_has_issue[index] = True
                    summary.missing_products += 1
                    results.append(self._build_row_result(row, summary.mode, "NO", "Product template tidak ditemukan"))
                    continue
                for company_id in companies.values():
                    self._add_batch_entry(
                        batches=batches,
                        batch_rows=batch_rows,
                        company_id=company_id,
                        price_key=price_key,
                        record_id=record_id,
                        row_index=index,
                    )
            else:
                for company_id in companies.values():
                    company_key = str(company_id)
                    comp_map = comp_code_to_prod_id.get(company_key) or {}
                    record_id = int(comp_map.get(code_key, 0))
                    if record_id <= 0:
                        summary.missing_products += 1
                        continue
                    row_target_count[index] += 1
                    self._add_batch_entry(
                        batches=batches,
                        batch_rows=batch_rows,
                        company_id=company_id,
                        price_key=price_key,
                        record_id=record_id,
                        row_index=index,
                    )
                if row_target_count[index] == 0:
                    status_arr[index] = "No"
                    row_has_issue[index] = True
                    results.append(self._build_row_result(row, summary.mode, "NO", "Product variant tidak ditemukan"))

        if not batches:
            summary.message = "Tidak ada data yang dapat diupload."
            self._finalize_statuses(summary, status_arr, row_has_issue, row_target_count, row_success_count, mode_context)
            return summary, results

        total_groups = sum(len(price_map) for price_map in batches.values())
        processed_groups = 0
        for company_key, price_groups in batches.items():
            if self._stop_requested.is_set():
                summary.stopped = True
                break
            company_id = int(company_key)
            company_label = self._format_company_label(company_id, mode_context.company_names)
            for price_key, record_ids in price_groups.items():
                if self._stop_requested.is_set():
                    summary.stopped = True
                    break
                ids = list(record_ids.values())
                self._emit_log(
                    f"[{summary.mode}] Company {company_label} | price={price_key} | ids={len(ids)} | dry_run={dry_run}"
                )
                batch_success = True
                for id_chunk in chunked(ids, WRITE_CHUNK_SIZE):
                    if self._stop_requested.is_set():
                        summary.stopped = True
                        batch_success = False
                        break
                    if dry_run:
                        summary.write_ok += 1
                        continue
                    ok = await self._write_standard_price(
                        model=model,
                        record_ids=id_chunk,
                        standard_price=float(price_key),
                        company_id=company_id,
                    )
                    if not ok:
                        summary.write_fail += 1
                        batch_success = False
                        break
                    summary.write_ok += 1
                if batch_success:
                    row_indexes = batch_rows[str(company_id)][price_key]
                    for row_index in row_indexes:
                        row_success_count[row_index] += 1
                processed_groups += 1
                self._emit_progress(
                    f"{summary.mode}: write group",
                    f"{company_label} | price {price_key}",
                    processed_groups,
                    total_groups,
                )

        self._finalize_statuses(summary, status_arr, row_has_issue, row_target_count, row_success_count, mode_context)
        results.extend(
            self._results_from_statuses(
                mode_context.prep_rows,
                summary.mode,
                status_arr,
                existing_results=results,
            )
        )

        prep_sheet = get_required_sheet(mode_context.workbook, mode_context.request.sheet_name)
        write_statuses(prep_sheet, status_arr, start_row=PREP_DATA_START_ROW)
        log_sheet = get_or_create_sheet(mode_context.workbook, LOG_SHEET_NAME)
        ensure_log_header(log_sheet)
        append_log_summary(
            ws=log_sheet,
            user=mode_context.log_user,
            message=(
                f"Update Standard Cost {summary.mode.upper()} batch. "
                f"Success={summary.updated_rows}, Failed={summary.failed_rows}, "
                f"Write OK={summary.write_ok}, Write Fail={summary.write_fail}"
            ),
            note=mode_context.log_note,
        )
        summary.modified = True
        if summary.stopped:
            summary.message = f"Update Standard Cost {summary.mode.upper()} dihentikan."
        else:
            summary.message = f"Update Standard Cost {summary.mode.upper()} selesai."
        return summary, results

    async def _fetch_template_ids_by_default_code_global(
        self,
        codes: Sequence[str],
    ) -> tuple[dict[str, int], int]:
        result: dict[str, int] = {}
        duplicate_count = 0
        pending_codes: list[str] = []
        for code in codes:
            cached_row = self._master_cache_get_product_record(
                model="product.template",
                company_id=0,
                code=code,
                required_fields=["id", "default_code"],
            )
            if cached_row is None:
                pending_codes.append(code)
                continue
            try:
                template_id = int(float(cached_row.get("id") or 0))
            except (TypeError, ValueError):
                template_id = 0
            if template_id > 0:
                result[normalize_text(cached_row.get("default_code") or code).lower()] = template_id

        for code_chunk in chunked(pending_codes, CODE_CHUNK_SIZE):
            rows = await self.rpc.search_read(
                "product.product",
                [["default_code", "in", list(code_chunk)]],
                fields=["default_code", "product_tmpl_id"],
                stage="STD_COST_TEMPLATE_LOOKUP",
            )
            for item in rows:
                code = str(item.get("default_code") or "").strip()
                if not code:
                    continue
                key = code.lower()
                raw_template = item.get("product_tmpl_id")
                if isinstance(raw_template, (list, tuple)) and raw_template:
                    template_id = int(raw_template[0])
                else:
                    try:
                        template_id = int(float(raw_template or 0))
                    except (TypeError, ValueError):
                        template_id = 0
                if template_id <= 0:
                    continue
                if key in result and int(result[key]) != template_id:
                    duplicate_count += 1
                    result[key] = 0
                else:
                    result[key] = template_id
                    self._master_cache_set_product_record(
                        model="product.template",
                        company_id=0,
                        row={"id": template_id, "default_code": code},
                        fields=["id", "default_code"],
                    )
        return result, duplicate_count

    async def _fetch_product_ids_by_default_code_with_context(
        self,
        codes: Sequence[str],
        *,
        company_id: int,
    ) -> dict[str, int]:
        context = build_company_context(company_id)
        result: dict[str, int] = {}
        pending_codes: list[str] = []
        for code in codes:
            cached_row = self._master_cache_get_product_record(
                model="product.product",
                company_id=company_id,
                code=code,
                required_fields=["id", "default_code"],
            )
            if cached_row is None:
                pending_codes.append(code)
                continue
            try:
                product_id = int(float(cached_row.get("id") or 0))
            except (TypeError, ValueError):
                product_id = 0
            if product_id > 0:
                result[normalize_text(cached_row.get("default_code") or code).lower()] = product_id

        for code_chunk in chunked(pending_codes, CODE_CHUNK_SIZE):
            rows = await self.rpc.search_read(
                "product.product",
                [["default_code", "in", list(code_chunk)]],
                fields=["id", "default_code"],
                context=context,
                stage="STD_COST_VARIANT_LOOKUP",
            )
            for item in rows:
                code = str(item.get("default_code") or "").strip()
                if not code:
                    continue
                try:
                    product_id = int(float(item.get("id") or 0))
                except (TypeError, ValueError):
                    product_id = 0
                if product_id > 0:
                    result[code.lower()] = product_id
                    self._master_cache_set_product_record(
                        model="product.product",
                        company_id=company_id,
                        row={"id": product_id, "default_code": code},
                        fields=["id", "default_code"],
                    )
        return result

    def _master_cache_get_product_record(
        self,
        *,
        model: str,
        company_id: int,
        code: str,
        required_fields: list[str] | None = None,
    ) -> dict[str, Any] | None:
        if self.master_cache is None:
            return None
        base_url, database = rpc_cache_scope(self.rpc)
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
        row: dict[str, Any],
        fields: list[str] | None = None,
    ) -> None:
        if self.master_cache is None:
            return
        base_url, database = rpc_cache_scope(self.rpc)
        self.master_cache.set_product_record(
            base_url=base_url,
            database=database,
            model=model,
            company_id=company_id,
            row=row,
            fields=fields,
        )

    async def _write_standard_price(
        self,
        *,
        model: str,
        record_ids: Sequence[int],
        standard_price: float,
        company_id: int,
    ) -> bool:
        return await self.rpc.write(
            model,
            list(record_ids),
            {"standard_price": float(standard_price)},
            context=build_company_context(company_id),
            stage="STD_COST_WRITE",
        )

    def _finalize_statuses(
        self,
        summary: UpdateStdCostModeSummary,
        status_arr: list[str],
        row_has_issue: list[bool],
        row_target_count: list[int],
        row_success_count: list[int],
        mode_context: _ModeContext,
    ) -> None:
        updated_rows = 0
        failed_rows = 0
        for index, current_status in enumerate(status_arr):
            if row_has_issue[index]:
                if not str(current_status).strip():
                    status_arr[index] = "No"
            elif row_target_count[index] > 0:
                status_arr[index] = "Yes" if row_success_count[index] >= row_target_count[index] else "No"

            status_text = str(status_arr[index]).strip().lower()
            if not status_text:
                continue
            if status_text == "yes":
                updated_rows += 1
            else:
                failed_rows += 1

        summary.updated_rows = updated_rows
        summary.failed_rows = failed_rows

    def _results_from_statuses(
        self,
        prep_rows: Sequence[PrepRow],
        mode: str,
        status_arr: Sequence[str],
        *,
        existing_results: Sequence[UpdateStdCostRowResult],
    ) -> list[UpdateStdCostRowResult]:
        existing_keys = {(result.row_number, result.mode) for result in existing_results}
        output: list[UpdateStdCostRowResult] = []
        for index, status in enumerate(status_arr):
            clean_status = str(status or "").strip()
            if not clean_status:
                continue
            row = prep_rows[index]
            key = (row.row_number, mode)
            if key in existing_keys:
                continue
            output.append(
                UpdateStdCostRowResult(
                    row_number=row.row_number,
                    mode=mode,
                    status=clean_status.upper(),
                    area=normalize_text(row.area),
                    product_code=normalize_text(row.product_code),
                    message="",
                )
            )
        return output

    def _build_row_result(self, row: PrepRow, mode: str, status: str, message: str) -> UpdateStdCostRowResult:
        return UpdateStdCostRowResult(
            row_number=row.row_number,
            mode=mode,
            status=status,
            area=normalize_text(row.area),
            product_code=normalize_text(row.product_code),
            message=message,
        )

    def _add_batch_entry(
        self,
        *,
        batches: dict[str, dict[str, dict[str, int]]],
        batch_rows: dict[str, dict[str, list[int]]],
        company_id: int,
        price_key: str,
        record_id: int,
        row_index: int,
    ) -> None:
        company_key = str(company_id)
        price_map = batches.setdefault(company_key, {})
        row_map = batch_rows.setdefault(company_key, {})
        ids = price_map.setdefault(price_key, {})
        row_indexes = row_map.setdefault(price_key, [])
        ids[str(record_id)] = record_id
        row_indexes.append(row_index)

    def _build_area_companies(self, area_map: dict[str, str]) -> dict[str, dict[str, int]]:
        area_companies: dict[str, dict[str, int]] = {}
        for comp_key, area_text_raw in area_map.items():
            try:
                company_id = int(float(comp_key))
            except (TypeError, ValueError):
                continue
            area_text = normalize_text(area_text_raw)
            if not area_text:
                continue
            area_companies.setdefault(area_text.lower(), {})[str(company_id)] = company_id
        return area_companies

    def _format_company_label(self, company_id: int, company_names: dict[str, str]) -> str:
        company_key = str(company_id)
        company_name = normalize_text(company_names.get(company_key, ""))
        if not company_name:
            return company_key
        return f"{company_key} ({company_name})"

    def _emit_log(self, message: str) -> None:
        self._on_log(message)

    def _emit_progress(self, phase: str, current: str, processed: int, total: int) -> None:
        ratio = 0.0 if total <= 0 else max(0.0, min(1.0, processed / total))
        self._on_progress(
            UpdateStdCostProgressSnapshot(
                phase=phase,
                current=current,
                processed=max(0, int(processed)),
                total=max(0, int(total)),
                progress=ratio,
            )
        )

    def _emit_state(
        self,
        status: str,
        message: str,
        summary: UpdateStdCostRunSummary | None,
        database: str,
    ) -> None:
        self._on_state(status, message, summary, database)
