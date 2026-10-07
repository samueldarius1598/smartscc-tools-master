"""Async service for validating and creating JE from orphan SVLs."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime
import logging
import threading
import time
from typing import Any, Callable

from smartscc_tools.services.odoo.gateway import build_company_context

from .excel_reader import read_excel
from .models import (
    SvlFixJeExcelRow,
    SvlFixJeProgressSnapshot,
    SvlFixJeRowResult,
    SvlFixJeRunRequest,
    SvlFixJeRunSummary,
    SvlFixJeValidatedRow,
)
from .validators import (
    detect_account_company_field,
    detect_svl_link_field_supported,
    prefetch_accounts,
    prefetch_journals,
    prefetch_svls,
    validate_row,
)


LogCallback = Callable[[str], None]
ProgressCallback = Callable[[SvlFixJeProgressSnapshot], None]
ResultCallback = Callable[[SvlFixJeRowResult], None]
StateCallback = Callable[[str, str, SvlFixJeRunSummary | None, str], None]


def build_reference(row: SvlFixJeExcelRow, ref_prefix: str) -> str:
    if row.note:
        return row.note
    return f"{ref_prefix}: SVL {row.svl_id} / {row.svl_ref}"


def build_line_name(row: SvlFixJeExcelRow) -> str:
    if row.note:
        return row.note
    return (
        f"SVL {row.svl_id} | {row.svl_ref} | {row.default_code} | "
        f"Qty: {row.qty} {row.uom} @ {row.unit_cost}"
    )


def build_move_values(row: SvlFixJeExcelRow, validated: SvlFixJeValidatedRow, ref_prefix: str) -> dict[str, Any]:
    total_value = float(row.total_value or 0.0)
    abs_value = abs(total_value)
    if total_value >= 0:
        debit_account_id = validated.credit_account_id
        credit_account_id = validated.debit_account_id
    else:
        debit_account_id = validated.debit_account_id
        credit_account_id = validated.credit_account_id

    line_name = build_line_name(row)
    move_values: dict[str, Any] = {
        "company_id": validated.company_id,
        "journal_id": validated.journal_id,
        "date": row.je_date,
        "ref": build_reference(row, ref_prefix),
        "move_type": "entry",
        "line_ids": [
            [
                0,
                0,
                {
                    "name": line_name,
                    "account_id": debit_account_id,
                    "debit": abs_value,
                    "credit": 0.0,
                },
            ],
            [
                0,
                0,
                {
                    "name": line_name,
                    "account_id": credit_account_id,
                    "debit": 0.0,
                    "credit": abs_value,
                },
            ],
        ],
    }
    if validated.product_id > 0:
        for line in move_values["line_ids"]:
            line[2]["product_id"] = validated.product_id
    return move_values


class SvlFixJeServiceAsync:
    def __init__(
        self,
        *,
        rpc: Any,
        logger: logging.Logger,
        on_log: LogCallback | None = None,
        on_progress: ProgressCallback | None = None,
        on_result: ResultCallback | None = None,
        on_state: StateCallback | None = None,
    ) -> None:
        self.rpc = rpc
        self.logger = logger
        self.on_log = on_log
        self.on_progress = on_progress
        self.on_result = on_result
        self.on_state = on_state
        self._stop_event = threading.Event()

    def request_stop(self) -> None:
        self._stop_event.set()
        self._log("Permintaan stop diterima. Task baru tidak akan dimulai.")

    async def validate(self, request: SvlFixJeRunRequest) -> SvlFixJeRunSummary:
        return await self._run("validate", request)

    async def execute(self, request: SvlFixJeRunRequest) -> SvlFixJeRunSummary:
        return await self._run("execute", request)

    def _log(self, message: str, *, level: int = logging.INFO) -> None:
        self.logger.log(level, message)
        if self.on_log is not None:
            self.on_log(message)

    def _emit_progress(self, snapshot: SvlFixJeProgressSnapshot) -> None:
        if self.on_progress is not None:
            self.on_progress(snapshot)

    def _emit_result(self, result: SvlFixJeRowResult) -> None:
        if self.on_result is not None:
            self.on_result(result)

    def _emit_state(
        self,
        status: str,
        message: str,
        summary: SvlFixJeRunSummary | None,
        database: str,
    ) -> None:
        if self.on_state is not None:
            self.on_state(status, message, summary, database)

    def _make_result(
        self,
        row: SvlFixJeExcelRow,
        *,
        status: str,
        move_id: int | None = None,
        error_message: str = "",
    ) -> SvlFixJeRowResult:
        return SvlFixJeRowResult(
            row_number=row.row_number,
            svl_id=row.svl_id,
            svl_ref=row.svl_ref,
            total_value=row.total_value,
            status=status,
            move_id=move_id,
            error_message=error_message,
            timestamp=datetime.now().isoformat(timespec="seconds"),
        )

    def _counts_for_mode(
        self,
        *,
        mode: str,
        valid_count: int,
        invalid_count: int,
        created_count: int,
        execution_errors: int,
        canceled_count: int,
    ) -> dict[str, int]:
        if mode == "validate":
            return {"ok": valid_count, "error": invalid_count, "skip": 0}
        return {
            "ok": created_count,
            "error": execution_errors,
            "skip": invalid_count + canceled_count,
        }

    def _phase_progress(
        self,
        *,
        mode: str,
        phase: str,
        processed: int,
        total: int,
        current: str,
        counts: dict[str, int],
        started_at: float,
        auto_post: bool,
    ) -> None:
        eta_seconds = None
        if processed > 0 and total > 0:
            elapsed = time.monotonic() - started_at
            eta_seconds = max(0, int((elapsed / processed) * (total - processed)))

        if mode == "validate":
            progress = processed / total if total else 0.0
        elif phase == "validate":
            progress = 0.4 * (processed / total if total else 0.0)
        elif phase == "create":
            progress = 0.4 + 0.45 * (processed / total if total else 0.0)
        elif auto_post:
            progress = 0.85 + 0.15 * (processed / total if total else 0.0)
        else:
            progress = 1.0

        self._emit_progress(
            SvlFixJeProgressSnapshot(
                phase=phase,
                processed=processed,
                total=total,
                current=current,
                eta_seconds=eta_seconds,
                counts=counts,
                progress=max(0.0, min(progress, 1.0)),
            )
        )

    async def _prefetch_cache(
        self,
        rows: list[SvlFixJeExcelRow],
        *,
        default_journal_code: str,
    ) -> dict[str, Any]:
        cache: dict[str, Any] = {
            "svls": {},
            "accounts": {},
            "journals": {},
            "account_company_field": await detect_account_company_field(self.rpc),
            "svl_link_supported": await detect_svl_link_field_supported(self.rpc),
        }

        svl_ids = [row.svl_id for row in rows if int(row.svl_id) > 0]
        self._log(f"Prefetch {len(set(svl_ids))} SVL dari Odoo...")
        cache["svls"] = await prefetch_svls(self.rpc, svl_ids)

        account_pairs: set[tuple[str, int | None]] = set()
        journal_pairs: set[tuple[str, int | None]] = set()
        for row in rows:
            svl_record = cache["svls"].get(row.svl_id)
            company_id = 0
            if svl_record and svl_record.get("company_id"):
                try:
                    company_id = int(svl_record["company_id"][0])
                except (TypeError, ValueError, IndexError):
                    company_id = 0
            if row.coa_credit:
                account_pairs.add((row.coa_credit, company_id or None))
            if row.coa_debit:
                account_pairs.add((row.coa_debit, company_id or None))
            journal_pairs.add(((row.journal_code or default_journal_code), company_id or None))

        self._log(f"Prefetch {len(account_pairs)} kombinasi COA...")
        cache["accounts"] = await prefetch_accounts(
            self.rpc,
            account_pairs,
            company_field_name=cache["account_company_field"],
            logger=self.logger,
        )
        self._log(f"Prefetch {len(journal_pairs)} kombinasi Journal...")
        cache["journals"] = await prefetch_journals(self.rpc, journal_pairs)
        return cache

    async def _create_move(self, validated: SvlFixJeValidatedRow, request: SvlFixJeRunRequest) -> int:
        move_values = build_move_values(validated.row, validated, request.ref_prefix)
        return await self.rpc.create(
            "account.move",
            move_values,
            context=build_company_context(validated.company_id),
            stage="SVL_FIX_CREATE_MOVE",
            excel_row=validated.row.row_number,
        )

    async def _link_svl_to_move(self, validated: SvlFixJeValidatedRow, move_id: int, *, link_supported: bool) -> None:
        if not link_supported:
            self._log("Field account_move_id tidak tersedia pada stock.valuation.layer. Link SVL dilewati.")
            return
        if validated.svl_record.get("account_move_id"):
            return
        svl_id = int(validated.svl_record.get("id") or 0)
        if svl_id <= 0:
            return
        try:
            await self.rpc.write(
                "stock.valuation.layer",
                [svl_id],
                {"account_move_id": move_id},
                context=build_company_context(validated.company_id),
                stage="SVL_FIX_LINK_MOVE",
                excel_row=validated.row.row_number,
            )
            self._log(f"SVL {svl_id} di-link ke JE {move_id}")
        except Exception as exc:  # noqa: BLE001
            self._log(
                f"Tidak bisa link SVL {svl_id} ke JE {move_id} (field mungkin readonly): {exc}",
                level=logging.WARNING,
            )

    async def _post_move_ids(
        self,
        created_rows: list[tuple[SvlFixJeValidatedRow, int]],
        request: SvlFixJeRunRequest,
        *,
        valid_count: int,
        invalid_count: int,
        created_count: int,
        execution_errors: int,
        canceled_count: int,
    ) -> tuple[list[SvlFixJeRowResult], int, int]:
        if not created_rows:
            return [], execution_errors, 0

        move_ids = [move_id for _validated, move_id in created_rows]
        posted_ids: set[int] = set()
        post_errors: dict[int, str] = {}
        post_started_at = time.monotonic()

        try:
            await self.rpc.execute_kw(
                "account.move",
                "action_post",
                [move_ids],
                kwargs={"context": build_company_context(created_rows[0][0].company_id)},
                stage="SVL_FIX_POST_BATCH",
                mutating=True,
            )
            posted_ids = set(move_ids)
        except Exception as batch_exc:  # noqa: BLE001
            self._log(f"Batch post gagal ({batch_exc}). Fallback per JE.", level=logging.WARNING)
            for index, (validated, move_id) in enumerate(created_rows, start=1):
                try:
                    await self.rpc.execute_kw(
                        "account.move",
                        "action_post",
                        [[move_id]],
                        kwargs={"context": build_company_context(validated.company_id)},
                        stage="SVL_FIX_POST_SINGLE",
                        excel_row=validated.row.row_number,
                        mutating=True,
                    )
                    posted_ids.add(move_id)
                except Exception as exc:  # noqa: BLE001
                    post_errors[move_id] = str(exc)
                self._phase_progress(
                    mode="execute",
                    phase="post",
                    processed=index,
                    total=len(created_rows),
                    current=f"Posting move {move_id}",
                    counts=self._counts_for_mode(
                        mode="execute",
                        valid_count=valid_count,
                        invalid_count=invalid_count,
                        created_count=created_count,
                        execution_errors=execution_errors + len(post_errors),
                        canceled_count=canceled_count,
                    ),
                    started_at=post_started_at,
                    auto_post=request.auto_post,
                )

        results: list[SvlFixJeRowResult] = []
        posted_count = 0
        for index, (validated, move_id) in enumerate(created_rows, start=1):
            if move_id in posted_ids:
                posted_count += 1
                result = self._make_result(validated.row, status="POSTED", move_id=move_id)
            else:
                execution_errors += 1
                result = self._make_result(
                    validated.row,
                    status="ERROR",
                    move_id=move_id,
                    error_message=post_errors.get(move_id, "Gagal post JE."),
                )
            results.append(result)
            self._emit_result(result)
            if len(posted_ids) == len(move_ids):
                self._phase_progress(
                    mode="execute",
                    phase="post",
                    processed=index,
                    total=len(created_rows),
                    current=f"Posting move {move_id}",
                    counts=self._counts_for_mode(
                        mode="execute",
                        valid_count=valid_count,
                        invalid_count=invalid_count,
                        created_count=created_count,
                        execution_errors=execution_errors,
                        canceled_count=canceled_count,
                    ),
                    started_at=post_started_at,
                    auto_post=request.auto_post,
                )
        return results, execution_errors, posted_count

    async def _run(self, mode: str, request: SvlFixJeRunRequest) -> SvlFixJeRunSummary:
        self._stop_event.clear()
        self._emit_state("running", "Memulai proses...", None, request.database)

        self._log("Membaca file Excel...")
        rows = read_excel(request.excel_path)
        self._log(f"Berhasil membaca {len(rows)} baris data dari Excel.")
        if not rows:
            summary = SvlFixJeRunSummary(
                mode=mode,
                database=request.database,
                results=[],
                validated_rows=[],
                total_rows=0,
                total_value=0.0,
                created_count=0,
                posted_count=0,
                execution_errors=0,
                validation_errors=0,
                canceled_count=0,
                stopped=False,
                auto_post=request.auto_post,
                max_workers=request.max_workers,
                excel_path=request.excel_path,
                ref_prefix=request.ref_prefix,
            )
            self._emit_state("completed", "Tidak ada data.", summary, request.database)
            return summary

        await self.rpc.ensure_login()
        self._emit_state("connected", "Connected", None, request.database)
        self._emit_progress(
            SvlFixJeProgressSnapshot(
                phase="prefetch",
                processed=0,
                total=len(rows),
                current="Prefetch lookup Odoo...",
                eta_seconds=None,
                counts={"ok": 0, "error": 0, "skip": 0},
                progress=0.0,
            )
        )
        cache = await self._prefetch_cache(rows, default_journal_code=request.default_journal_code)

        validation_started_at = time.monotonic()
        validated_rows: list[SvlFixJeValidatedRow] = []
        results: list[SvlFixJeRowResult] = []
        valid_count = 0
        invalid_count = 0
        canceled_count = 0

        for index, row in enumerate(rows, start=1):
            if self._stop_event.is_set():
                for canceled_row in rows[index - 1 :]:
                    result = self._make_result(
                        canceled_row,
                        status="CANCELED",
                        error_message="Processing canceled by user.",
                    )
                    results.append(result)
                    self._emit_result(result)
                    canceled_count += 1
                break

            self._log(f"Baris {row.row_number}: SVL ID {row.svl_id}")
            is_valid, validated, errors = await validate_row(
                self.rpc,
                row,
                cache,
                default_journal_code=request.default_journal_code,
                logger=self.logger,
            )
            if errors:
                for error in errors:
                    self._log(f"WARNING: {error}", level=logging.WARNING)
            if is_valid and validated is not None:
                valid_count += 1
                validated_rows.append(validated)
                if mode == "validate":
                    result = self._make_result(row, status="VALID")
                    results.append(result)
                    self._emit_result(result)
                    self._log("VALID")
            else:
                invalid_count += 1
                result = self._make_result(row, status="ERROR", error_message=" | ".join(errors))
                results.append(result)
                self._emit_result(result)
                self._log(f"TIDAK VALID - skip baris {row.row_number}", level=logging.ERROR)

            self._phase_progress(
                mode=mode,
                phase="validate",
                processed=index,
                total=len(rows),
                current=f"Validating SVL {row.svl_id}",
                counts=self._counts_for_mode(
                    mode=mode,
                    valid_count=valid_count,
                    invalid_count=invalid_count,
                    created_count=0,
                    execution_errors=0,
                    canceled_count=canceled_count,
                ),
                started_at=validation_started_at,
                auto_post=request.auto_post,
            )

        execution_errors = 0
        created_count = 0
        posted_count = 0

        if mode == "execute" and validated_rows and not self._stop_event.is_set():
            create_started_at = time.monotonic()
            pending_queue: asyncio.Queue[SvlFixJeValidatedRow] = asyncio.Queue()
            for item in validated_rows:
                pending_queue.put_nowait(item)

            created_rows: list[tuple[SvlFixJeValidatedRow, int]] = []
            created_rows_lock = asyncio.Lock()
            results_lock = asyncio.Lock()
            progress_counter = {"processed": 0}
            link_supported = bool(cache.get("svl_link_supported", True))

            async def worker() -> None:
                nonlocal created_count, execution_errors
                while not self._stop_event.is_set():
                    try:
                        validated = pending_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        return
                    try:
                        move_id = await self._create_move(validated, request)
                        await self._link_svl_to_move(validated, move_id, link_supported=link_supported)
                        async with created_rows_lock:
                            created_count += 1
                            created_rows.append((validated, move_id))
                        if not request.auto_post:
                            result = self._make_result(validated.row, status="CREATED", move_id=move_id)
                            async with results_lock:
                                results.append(result)
                            self._emit_result(result)
                        self._log(
                            f"Journal Entry dibuat untuk baris {validated.row.row_number} (move_id={move_id})"
                        )
                    except Exception as exc:  # noqa: BLE001
                        execution_errors += 1
                        result = self._make_result(
                            validated.row,
                            status="ERROR",
                            error_message=str(exc),
                        )
                        async with results_lock:
                            results.append(result)
                        self._emit_result(result)
                        self._log(
                            f"GAGAL baris {validated.row.row_number}: {exc}",
                            level=logging.ERROR,
                        )
                    finally:
                        progress_counter["processed"] += 1
                        self._phase_progress(
                            mode=mode,
                            phase="create",
                            processed=progress_counter["processed"],
                            total=len(validated_rows),
                            current=f"Creating JE for SVL {validated.row.svl_id}",
                            counts=self._counts_for_mode(
                                mode=mode,
                                valid_count=valid_count,
                                invalid_count=invalid_count,
                                created_count=created_count,
                                execution_errors=execution_errors,
                                canceled_count=canceled_count,
                            ),
                            started_at=create_started_at,
                            auto_post=request.auto_post,
                        )

            worker_count = max(1, int(request.max_workers or 1))
            await asyncio.gather(*(worker() for _ in range(min(worker_count, len(validated_rows)))))

            while True:
                try:
                    pending = pending_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                canceled_count += 1
                result = self._make_result(
                    pending.row,
                    status="CANCELED",
                    error_message="Processing canceled by user.",
                )
                results.append(result)
                self._emit_result(result)

            if request.auto_post and created_rows:
                post_results, execution_errors, posted_count = await self._post_move_ids(
                    created_rows,
                    request,
                    valid_count=valid_count,
                    invalid_count=invalid_count,
                    created_count=created_count,
                    execution_errors=execution_errors,
                    canceled_count=canceled_count,
                )
                results.extend(post_results)

        total_value = sum(float(item.row.total_value or 0.0) for item in validated_rows)
        summary = SvlFixJeRunSummary(
            mode=mode,
            database=request.database,
            results=sorted(results, key=lambda item: item.row_number),
            validated_rows=[replace(item, row=replace(item.row)) for item in validated_rows],
            total_rows=len(rows),
            total_value=total_value,
            created_count=created_count,
            posted_count=posted_count,
            execution_errors=execution_errors,
            validation_errors=invalid_count,
            canceled_count=canceled_count,
            stopped=self._stop_event.is_set(),
            auto_post=request.auto_post,
            max_workers=request.max_workers,
            excel_path=request.excel_path,
            ref_prefix=request.ref_prefix,
        )
        if not self._stop_event.is_set():
            self._emit_progress(
                SvlFixJeProgressSnapshot(
                    phase="completed",
                    processed=len(rows),
                    total=len(rows),
                    current="Selesai",
                    eta_seconds=0,
                    counts=self._counts_for_mode(
                        mode=mode,
                        valid_count=valid_count,
                        invalid_count=invalid_count,
                        created_count=created_count,
                        execution_errors=execution_errors,
                        canceled_count=canceled_count,
                    ),
                    progress=1.0,
                )
            )
        self._emit_state(
            "canceled" if self._stop_event.is_set() else "completed",
            "Dibatalkan" if self._stop_event.is_set() else "Selesai",
            summary,
            request.database,
        )
        return summary
