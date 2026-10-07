"""Async repair helpers for dashboard-driven JE fixes."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import date
import inspect
import logging
from typing import Any

from smartscc_tools.services.odoo.gateway import build_company_context
from smartscc_tools.services.odoo.master_cache import SessionMasterCache, rpc_cache_scope
from smartscc_tools.features.item_journal.services.date_ops import LockDateServiceAsync
from smartscc_tools.features.item_journal.utils import normalize_text, parse_iso_date

from .config import DEFAULT_JOURNAL_CODE
from .models import (
    SvlDashboardPcbCase1RepairBatchResult,
    SvlDashboardPcbCase1RepairRequest,
    SvlDashboardPcbCase1RepairRow,
    SvlDashboardPcbCase1RepairRowResult,
    SvlDashboardPcbCase2RepairBatchResult,
    SvlDashboardPcbCase2RepairRequest,
    SvlDashboardPcbCase2RepairRow,
    SvlDashboardPcbCase2RepairRowResult,
    SvlDashboardPcbRepairPlannedLine,
    SvlDashboardRepairBatchResult,
    SvlDashboardRepairProgressSnapshot,
    SvlDashboardRepairRequest,
    SvlDashboardRepairRow,
    SvlDashboardRepairRowResult,
)
from .pcb_repair_labels import normalize_pcb_planned_line_label
from .validators import detect_account_company_field


_FIELD_NOT_PRESENT = object()
_PARTNER_PREFETCH_CHUNK_SIZE = 200


@dataclass
class ResolvedAccount:
    account_id: int
    code: str
    name: str


@dataclass
class ResolvedJournal:
    journal_id: int
    code: str
    name: str


class RepairOperationError(RuntimeError):
    def __init__(self, error_kind: str, message: str) -> None:
        super().__init__(message)
        self.error_kind = normalize_text(error_kind) or "unknown"
        self.message = message


class SvlDashboardRepairServiceAsync:
    def __init__(
        self,
        *,
        rpc: Any,
        logger: logging.Logger,
        master_cache: SessionMasterCache | None = None,
    ) -> None:
        self.rpc = rpc
        self.logger = logger
        self.master_cache = master_cache
        self._lock_date_service = LockDateServiceAsync(rpc=rpc, logger=logger)
        self._account_company_field = ""
        self._model_fields_cache: dict[str, dict[str, Any]] = {}
        self._pcb_case1_reconcile_locks: dict[str, asyncio.Lock] = {}
        self._partner_cache: dict[tuple[int, str], int] = {}
        self._partner_exact_prefetch_misses: set[tuple[int, str]] = set()

    async def resolve_account(self, *, company_id: int, code: str) -> ResolvedAccount | None:
        clean_code = normalize_text(code)
        if not clean_code:
            return None
        domain: list[Any] = [("code", "=", clean_code)]
        domain.extend(await self._company_domain(company_id))
        rows = await self.rpc.search_read(
            "account.account",
            domain,
            fields=["id", "code", "name"],
            limit=1,
            context=build_company_context(company_id),
            stage="SVL_DASH_REPAIR_ACCOUNT_RESOLVE",
        )
        if not rows:
            return None
        row = rows[0]
        return ResolvedAccount(
            account_id=int(row.get("id") or 0),
            code=normalize_text(row.get("code")),
            name=normalize_text(row.get("name")),
        )

    async def resolve_journal(self, *, company_id: int, code: str) -> ResolvedJournal | None:
        clean_code = normalize_text(code) or DEFAULT_JOURNAL_CODE
        rows = await self.rpc.search_read(
            "account.journal",
            [("code", "=", clean_code), ("company_id", "=", company_id)],
            fields=["id", "code", "name"],
            limit=1,
            context=build_company_context(company_id),
            stage="SVL_DASH_REPAIR_JOURNAL_RESOLVE",
        )
        if not rows:
            return None
        row = rows[0]
        return ResolvedJournal(
            journal_id=int(row.get("id") or 0),
            code=normalize_text(row.get("code")),
            name=normalize_text(row.get("name")),
        )

    async def _fields_get_cached(self, model: str) -> dict[str, Any]:
        if model not in self._model_fields_cache:
            self._model_fields_cache[model] = await self.rpc.fields_get(model, attributes=["type"])
        return self._model_fields_cache[model]

    @staticmethod
    def _pick_supported_field(meta: dict[str, Any], *field_names: str) -> str:
        for field_name in field_names:
            if field_name and field_name in meta:
                return field_name
        return ""

    @staticmethod
    def _many2one_id(value: Any) -> int:
        if isinstance(value, (list, tuple)):
            if not value:
                return 0
            return int(value[0] or 0)
        return int(value or 0)

    @staticmethod
    def _move_line_balance(row: dict[str, Any]) -> float:
        balance = row.get("balance")
        if balance not in (None, False, ""):
            return float(balance or 0.0)
        return float(row.get("debit") or 0.0) - float(row.get("credit") or 0.0)

    @staticmethod
    def _move_line_amount_currency_raw(row: dict[str, Any]) -> Any:
        return row["amount_currency"] if "amount_currency" in row else _FIELD_NOT_PRESENT

    @staticmethod
    def _move_line_numeric_field_raw(row: dict[str, Any], field_name: str) -> Any:
        return row[field_name] if field_name in row else _FIELD_NOT_PRESENT

    @classmethod
    def _move_line_numeric_field_value(cls, row: dict[str, Any], field_name: str) -> float | None | object:
        raw = cls._move_line_numeric_field_raw(row, field_name)
        if raw is _FIELD_NOT_PRESENT:
            return _FIELD_NOT_PRESENT
        if raw is None or raw is False or raw == "":
            return None
        return round(float(raw), 2)

    @classmethod
    def _move_line_amount_currency_value(cls, row: dict[str, Any]) -> float | None | object:
        raw = cls._move_line_amount_currency_raw(row)
        if raw is _FIELD_NOT_PRESENT:
            return _FIELD_NOT_PRESENT
        if raw is None or raw is False or raw == "":
            return None
        return round(float(raw), 2)

    @classmethod
    def _move_line_amount_residual_value(cls, row: dict[str, Any]) -> float | None | object:
        return cls._move_line_numeric_field_value(row, "amount_residual")

    @classmethod
    def _move_line_amount_residual_currency_value(cls, row: dict[str, Any]) -> float | None | object:
        return cls._move_line_numeric_field_value(row, "amount_residual_currency")

    @classmethod
    def _move_line_open_balance_value(cls, row: dict[str, Any]) -> float | None | object:
        balance = round(cls._move_line_balance(row), 2)
        residual = cls._move_line_amount_residual_value(row)
        if residual is _FIELD_NOT_PRESENT:
            return balance
        if residual is None:
            return None
        sign_source = balance if abs(balance) >= 0.01 else float(residual)
        signed_residual = abs(float(residual))
        if sign_source < 0:
            signed_residual *= -1.0
        return round(signed_residual, 2)

    @classmethod
    def _move_line_open_amount_currency_value(cls, row: dict[str, Any]) -> float | None | object:
        residual_currency = cls._move_line_amount_residual_currency_value(row)
        if residual_currency is _FIELD_NOT_PRESENT:
            return cls._move_line_amount_currency_value(row)
        if residual_currency is None:
            return None
        sign_source = cls._move_line_amount_currency_value(row)
        if sign_source in {_FIELD_NOT_PRESENT, None}:
            sign_source = cls._move_line_open_balance_value(row)
        if sign_source in {_FIELD_NOT_PRESENT, None}:
            sign_source = 0.0
        signed_residual_currency = abs(float(residual_currency))
        if float(sign_source or 0.0) < 0:
            signed_residual_currency *= -1.0
        return round(signed_residual_currency, 2)

    @classmethod
    def _move_line_null_amount_fields(cls, row: dict[str, Any]) -> list[str]:
        null_fields: list[str] = []
        for field_name in ("amount_currency", "amount_residual", "amount_residual_currency"):
            raw = cls._move_line_numeric_field_raw(row, field_name)
            if raw is None or raw is False or raw == "":
                null_fields.append(field_name)
        return null_fields

    @classmethod
    def _move_line_open_balance_or_balance(cls, row: dict[str, Any]) -> float | None | object:
        open_balance = cls._move_line_open_balance_value(row)
        if open_balance is _FIELD_NOT_PRESENT:
            return round(cls._move_line_balance(row), 2)
        return open_balance

    @staticmethod
    def _pcb_case1_partner_optional_for_account(account_code: str) -> bool:
        return normalize_text(account_code) == "1108099"

    def _pcb_case1_partner_compatible(
        self,
        *,
        account_code: str,
        new_line: dict[str, Any],
        target_line: dict[str, Any],
    ) -> bool:
        new_partner_id = self._many2one_id(new_line.get("partner_id"))
        target_partner_id = self._many2one_id(target_line.get("partner_id"))
        if self._pcb_case1_partner_optional_for_account(account_code) and target_partner_id <= 0:
            return True
        if (new_partner_id or target_partner_id) and new_partner_id != target_partner_id:
            return False
        return True

    def _build_pcb_case1_target_candidates(
        self,
        *,
        row: SvlDashboardPcbCase1RepairRow,
        account_code: str,
        new_line: dict[str, Any],
        target_lines: list[dict[str, Any]],
        exact_only: bool,
    ) -> tuple[list[dict[str, Any]], str]:
        new_account_id = self._many2one_id(new_line.get("account_id"))
        new_currency_id = self._many2one_id(new_line.get("currency_id"))
        new_open_balance = self._move_line_open_balance_or_balance(new_line)
        new_open_amount_currency = self._move_line_open_amount_currency_value(new_line)
        if new_open_balance in {_FIELD_NOT_PRESENT, None}:
            return [], "Saldo line JE hasil create tidak tersedia."
        new_balance = round(float(new_open_balance or 0.0), 2)
        if abs(new_balance) < 0.01:
            return [], "Saldo line JE hasil create = 0."

        candidates: list[dict[str, Any]] = []
        stj_move_id_set = {
            int(move_id or 0)
            for move_id in list(row.stj_move_ids or [])
            if int(move_id or 0) > 0
        }
        for target_line in target_lines:
            if bool(target_line.get("reconciled")):
                continue
            target_account_id = self._many2one_id(target_line.get("account_id"))
            if target_account_id != new_account_id:
                continue
            if not self._pcb_case1_partner_compatible(
                account_code=account_code,
                new_line=new_line,
                target_line=target_line,
            ):
                continue
            target_currency_id = self._many2one_id(target_line.get("currency_id"))
            if (new_currency_id or target_currency_id) and new_currency_id != target_currency_id:
                continue
            target_open_balance = self._move_line_open_balance_or_balance(target_line)
            if target_open_balance in {_FIELD_NOT_PRESENT, None}:
                continue
            target_balance = round(float(target_open_balance or 0.0), 2)
            if abs(target_balance) < 0.01 or new_balance * target_balance >= 0:
                continue
            target_open_amount_currency = self._move_line_open_amount_currency_value(target_line)
            if exact_only:
                if abs(abs(new_balance) - abs(target_balance)) > 0.01:
                    continue
                if (
                    isinstance(new_open_amount_currency, float)
                    and isinstance(target_open_amount_currency, float)
                    and (abs(new_open_amount_currency) >= 0.01 or abs(target_open_amount_currency) >= 0.01)
                    and abs(abs(new_open_amount_currency) - abs(target_open_amount_currency)) > 0.01
                ):
                    continue
            line_id = int(target_line.get("id") or 0)
            purchase_line_id = self._many2one_id(target_line.get("purchase_line_id"))
            product_id = self._many2one_id(target_line.get("product_id"))
            move_id = self._many2one_id(target_line.get("move_id"))
            if normalize_text(account_code) == "2103006":
                specificity = (
                    1 if row.bill_line_id > 0 and line_id == int(row.bill_line_id or 0) else 0,
                    1 if row.purchase_line_id > 0 and purchase_line_id == int(row.purchase_line_id or 0) else 0,
                    1 if row.product_id > 0 and product_id == int(row.product_id or 0) else 0,
                    1 if row.bill_move_id > 0 and move_id == int(row.bill_move_id or 0) else 0,
                )
            else:
                specificity = (
                    1 if move_id > 0 and move_id in stj_move_id_set else 0,
                    1 if row.product_id > 0 and product_id == int(row.product_id or 0) else 0,
                )
            candidates.append(
                {
                    "line": target_line,
                    "line_id": line_id,
                    "open_abs": round(abs(target_balance), 2),
                    "specificity": specificity,
                }
            )
        if candidates:
            return candidates, ""
        if exact_only:
            return [], "Tidak ada open line exact match untuk direconcile."
        return [], "Tidak ada target open line yang compatible untuk partial reconcile."

    @staticmethod
    def _pcb_case1_sort_target_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(
            candidates,
            key=lambda candidate: (
                tuple(-int(part) for part in candidate.get("specificity", ())),
                -float(candidate.get("open_abs") or 0.0),
                int(candidate.get("line_id") or 0),
            ),
        )

    @staticmethod
    def _pcb_case1_candidate_is_ambiguous(
        first_candidate: dict[str, Any],
        second_candidate: dict[str, Any],
    ) -> bool:
        if tuple(first_candidate.get("specificity", ())) != tuple(second_candidate.get("specificity", ())):
            return False
        return abs(float(first_candidate.get("open_abs") or 0.0) - float(second_candidate.get("open_abs") or 0.0)) <= 0.01

    def _pick_exact_reconcile_target(
        self,
        *,
        row: SvlDashboardPcbCase1RepairRow,
        account_code: str,
        new_line: dict[str, Any],
        target_lines: list[dict[str, Any]],
    ) -> tuple[dict[str, Any] | None, str]:
        candidates, reason = self._build_pcb_case1_target_candidates(
            row=row,
            account_code=account_code,
            new_line=new_line,
            target_lines=target_lines,
            exact_only=True,
        )
        if not candidates:
            return None, reason
        ranked_candidates = self._pcb_case1_sort_target_candidates(candidates)
        if len(ranked_candidates) > 1 and self._pcb_case1_candidate_is_ambiguous(ranked_candidates[0], ranked_candidates[1]):
            top_specificity = tuple(ranked_candidates[0].get("specificity", ()))
            top_abs = float(ranked_candidates[0].get("open_abs") or 0.0)
            top_rank_count = sum(
                1
                for candidate in ranked_candidates
                if tuple(candidate.get("specificity", ())) == top_specificity
                and abs(float(candidate.get("open_abs") or 0.0) - top_abs) <= 0.01
            )
            return None, f"Target reconcile ambigu ({top_rank_count} candidate top-rank)."
        return ranked_candidates[0]["line"], ""

    def _pick_partial_reconcile_target(
        self,
        *,
        row: SvlDashboardPcbCase1RepairRow,
        account_code: str,
        new_line: dict[str, Any],
        target_lines: list[dict[str, Any]],
    ) -> tuple[dict[str, Any] | None, str]:
        candidates, reason = self._build_pcb_case1_target_candidates(
            row=row,
            account_code=account_code,
            new_line=new_line,
            target_lines=target_lines,
            exact_only=False,
        )
        if not candidates:
            return None, reason
        ranked_candidates = self._pcb_case1_sort_target_candidates(candidates)
        if len(ranked_candidates) > 1 and self._pcb_case1_candidate_is_ambiguous(ranked_candidates[0], ranked_candidates[1]):
            top_specificity = tuple(ranked_candidates[0].get("specificity", ()))
            top_abs = float(ranked_candidates[0].get("open_abs") or 0.0)
            top_rank_count = sum(
                1
                for candidate in ranked_candidates
                if tuple(candidate.get("specificity", ())) == top_specificity
                and abs(float(candidate.get("open_abs") or 0.0) - top_abs) <= 0.01
            )
            return None, f"Target reconcile ambigu ({top_rank_count} candidate top-rank)."
        return ranked_candidates[0]["line"], ""

    def _build_reconcile_line_pair(
        self,
        *,
        new_line: dict[str, Any],
        target_line: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any], str]:
        new_open_balance = self._move_line_open_balance_or_balance(new_line)
        target_open_balance = self._move_line_open_balance_or_balance(target_line)
        if new_open_balance in {_FIELD_NOT_PRESENT, None} or target_open_balance in {_FIELD_NOT_PRESENT, None}:
            return {}, {}, "saldo open target/new AML tidak tersedia."
        new_balance = float(new_open_balance or 0.0)
        target_balance = float(target_open_balance or 0.0)
        debit_line = new_line if new_balance > 0 else target_line if target_balance > 0 else {}
        credit_line = new_line if new_balance < 0 else target_line if target_balance < 0 else {}
        if not debit_line or not credit_line:
            return {}, {}, "arah debit/kredit reconcile tidak valid."
        return debit_line, credit_line, ""

    def _move_line_has_open_balance(self, row: dict[str, Any]) -> bool:
        open_balance = self._move_line_open_balance_or_balance(row)
        if open_balance in {_FIELD_NOT_PRESENT, None}:
            return False
        return abs(float(open_balance or 0.0)) >= 0.01

    @classmethod
    def _partial_reconcile_amount_value(
        cls,
        *,
        debit_line: dict[str, Any],
        credit_line: dict[str, Any],
    ) -> float:
        debit_open_balance = cls._move_line_open_balance_or_balance(debit_line)
        credit_open_balance = cls._move_line_open_balance_or_balance(credit_line)
        debit_balance = abs(float(debit_open_balance or 0.0)) if debit_open_balance not in {_FIELD_NOT_PRESENT, None} else abs(cls._move_line_balance(debit_line))
        credit_balance = abs(float(credit_open_balance or 0.0)) if credit_open_balance not in {_FIELD_NOT_PRESENT, None} else abs(cls._move_line_balance(credit_line))
        return round(min(debit_balance, credit_balance), 2)

    @staticmethod
    def _pcb_case1_reconcile_scope_key(row: SvlDashboardPcbCase1RepairRow) -> str:
        company_id = int(row.company_id or 0)
        cycle_key = normalize_text(row.cycle_key)
        if cycle_key:
            return f"{company_id}::{cycle_key}"
        bill_move_id = int(row.bill_move_id or 0)
        stj_move_ids = sorted(int(move_id or 0) for move_id in list(row.stj_move_ids or []) if int(move_id or 0) > 0)
        if bill_move_id > 0 or stj_move_ids:
            stj_scope = ",".join(str(move_id) for move_id in stj_move_ids)
            return f"{company_id}::bill:{bill_move_id}::stj:{stj_scope}"
        return f"{company_id}::{normalize_text(row.row_key) or 'pcb_case1'}"

    def _get_pcb_case1_reconcile_lock(self, row: SvlDashboardPcbCase1RepairRow) -> asyncio.Lock:
        lock_key = self._pcb_case1_reconcile_scope_key(row)
        lock = self._pcb_case1_reconcile_locks.get(lock_key)
        if lock is None:
            lock = asyncio.Lock()
            self._pcb_case1_reconcile_locks[lock_key] = lock
        return lock

    @staticmethod
    def _normalize_analytic_distribution(value: Any) -> Any:
        if not isinstance(value, dict):
            return False
        normalized: dict[str, float] = {}
        for key, amount in value.items():
            clean_key = normalize_text(key)
            if not clean_key:
                continue
            clean_amount = round(float(amount or 0.0), 2)
            if abs(clean_amount) < 0.01:
                continue
            normalized[clean_key] = clean_amount
        return normalized or False

    async def execute(self, request: SvlDashboardRepairRequest) -> SvlDashboardRepairBatchResult:
        await self.rpc.ensure_login()
        worker_count = max(1, int(request.max_workers or 1))
        semaphore = asyncio.Semaphore(worker_count)
        total_rows = len(request.rows)
        processed_count = 0
        success_count = 0
        error_count = 0

        async def run_row(index: int, row: SvlDashboardRepairRow) -> tuple[int, SvlDashboardRepairRowResult]:
            async with semaphore:
                return index, await self._execute_row_safe(row)

        ordered_results: list[SvlDashboardRepairRowResult | None] = [None] * len(request.rows)
        if request.rows:
            await self._notify_progress(
                self._build_progress_snapshot(
                    processed=0,
                    total=total_rows,
                    current="Memulai repair...",
                    success_count=0,
                    error_count=0,
                ),
                request=request,
            )
            tasks = [asyncio.create_task(run_row(index, row)) for index, row in enumerate(request.rows)]
            for task in asyncio.as_completed(tasks):
                index, result = await task
                ordered_results[index] = result
                processed_count += 1
                if normalize_text(result.status).upper() == "ERROR":
                    error_count += 1
                else:
                    success_count += 1
                await self._notify_progress(
                    self._build_progress_snapshot(
                        processed=processed_count,
                        total=total_rows,
                        current=self._progress_row_label(request.rows[index], result=result),
                        success_count=success_count,
                        error_count=error_count,
                    ),
                    request=request,
                )
        results = [result for result in ordered_results if result is not None]
        warnings: list[str] = []
        created_count = 0
        posted_count = 0
        error_count = 0

        for result in results:
            if result.status in {"CREATED", "UPDATED", "POSTED"}:
                created_count += 1
            if result.posted:
                posted_count += 1
            if result.status == "ERROR":
                error_count += 1
            if result.message and "draft only" in result.message.lower():
                warnings.append(result.message)

        return SvlDashboardRepairBatchResult(
            database=request.database,
            results=results,
            created_count=created_count,
            posted_count=posted_count,
            error_count=error_count,
            warnings=warnings,
        )

    async def _notify_progress(
        self,
        snapshot: SvlDashboardRepairProgressSnapshot,
        *,
        request: SvlDashboardRepairRequest,
    ) -> None:
        callback = getattr(request, "on_progress", None)
        if callback is None:
            return
        callback_result = callback(snapshot)
        if inspect.isawaitable(callback_result):
            await callback_result

    @staticmethod
    def _progress_row_label(row: SvlDashboardRepairRow, *, result: SvlDashboardRepairRowResult | None = None) -> str:
        item_code = normalize_text(row.item_code)
        item_name = normalize_text(row.item_name)
        move_name = normalize_text(result.move_name if result is not None else "")
        base_label = " | ".join(part for part in (item_code, item_name) if part) or normalize_text(row.row_key) or "-"
        return f"{base_label} [{move_name}]" if move_name else base_label

    @staticmethod
    def _build_progress_snapshot(
        *,
        processed: int,
        total: int,
        current: str,
        success_count: int,
        error_count: int,
    ) -> SvlDashboardRepairProgressSnapshot:
        progress = float(processed) / float(total) if total > 0 else 0.0
        return SvlDashboardRepairProgressSnapshot(
            phase="repair",
            processed=processed,
            total=total,
            current=normalize_text(current),
            progress=progress,
            success_count=success_count,
            error_count=error_count,
        )

    async def _execute_row_safe(self, row: SvlDashboardRepairRow) -> SvlDashboardRepairRowResult:
        selected_target_mode = normalize_text(row.target_mode).lower() or "new_and_relink"
        effective_date = normalize_text(row.date)
        try:
            return await self._execute_row(row)
        except RepairOperationError as exc:
            return self._build_row_result(
                row=row,
                status="ERROR",
                message=exc.message,
                selected_target_mode=selected_target_mode,
                effective_date=effective_date,
                error_kind=exc.error_kind,
            )
        except Exception as exc:  # noqa: BLE001
            return self._build_row_result(
                row=row,
                status="ERROR",
                message=str(exc),
                selected_target_mode=selected_target_mode,
                effective_date=effective_date,
                error_kind="unknown",
            )

    async def _execute_row(self, row: SvlDashboardRepairRow) -> SvlDashboardRepairRowResult:
        clean_target_mode = normalize_text(row.target_mode).lower() or "new_and_relink"
        clean_posting_mode = normalize_text(row.posting_mode).lower() or "draft"
        if clean_target_mode not in {"fill_existing", "new_and_relink"}:
            raise RepairOperationError("mode_invalid", f"Mode repair tidak dikenal: {row.target_mode}")
        if clean_posting_mode not in {"draft", "post"}:
            raise RepairOperationError("mode_invalid", f"Mode posting tidak dikenal: {row.posting_mode}")

        target_date = self._parse_date(row.date)
        debit_account = await self.resolve_account(company_id=row.company_id, code=row.debit_account_code)
        if debit_account is None:
            raise RepairOperationError("account_not_found", f"Akun debit '{row.debit_account_code}' tidak ditemukan secara exact.")
        credit_account = await self.resolve_account(company_id=row.company_id, code=row.credit_account_code)
        if credit_account is None:
            raise RepairOperationError("account_not_found", f"Akun kredit '{row.credit_account_code}' tidak ditemukan secara exact.")

        if clean_target_mode == "fill_existing":
            if row.move_id <= 0:
                raise RepairOperationError("move_not_found", "Rewrite Existing JE membutuhkan move_id existing.")
            return await self._fill_existing_move(
                row=row,
                target_date=target_date,
                debit_account=debit_account,
                credit_account=credit_account,
                posting_mode=clean_posting_mode,
            )
        return await self._create_new_move_and_relink(
            row=row,
            target_date=target_date,
            debit_account=debit_account,
            credit_account=credit_account,
            posting_mode=clean_posting_mode,
        )

    async def _fill_existing_move(
        self,
        *,
        row: SvlDashboardRepairRow,
        target_date: date,
        debit_account: ResolvedAccount,
        credit_account: ResolvedAccount,
        posting_mode: str,
    ) -> SvlDashboardRepairRowResult:
        selected_target_mode = "fill_existing"
        effective_date = target_date.strftime("%Y-%m-%d")
        context = build_company_context(row.company_id)
        move_rows = await self.rpc.read(
            "account.move",
            [row.move_id],
            fields=["id", "name", "state", "journal_id", "date"],
            context=context,
            stage="SVL_DASH_REPAIR_MOVE_READ",
        )
        if not move_rows:
            raise RepairOperationError("move_not_found", f"Account move {row.move_id} tidak ditemukan.")
        move_row = move_rows[0]
        move_name = normalize_text(move_row.get("name")) or row.move_name or str(row.move_id)
        move_state = normalize_text(move_row.get("state")).lower()
        if move_state == "posted":
            posting_mode = "post"
            await self._ensure_postable_date(company_id=row.company_id, target_date=target_date)
            draft_ok, draft_err, _draft_method = await self._move_posted_to_draft(journal_ids=[row.move_id], context=context)
            if not draft_ok:
                raise RepairOperationError("draft_failed", draft_err)

        write_values = {
            "date": target_date.strftime("%Y-%m-%d"),
            "ref": normalize_text(row.reference),
            "line_ids": self._build_line_commands(
                row=row,
                debit_account_id=debit_account.account_id,
                credit_account_id=credit_account.account_id,
            ),
        }
        try:
            write_ok = await self.rpc.write(
                "account.move",
                [row.move_id],
                write_values,
                context=context,
                stage="SVL_DASH_REPAIR_FILL_EXISTING",
            )
            if not write_ok:
                raise RepairOperationError("write_failed", "account.move.write mengembalikan False.")
            posted = False
            if posting_mode == "post":
                repost_ok, repost_err = await self._repost_moves(journal_ids=[row.move_id], context=context)
                if not repost_ok:
                    raise RepairOperationError("repost_failed", repost_err)
                posted = True
                move_name = await self._read_move_name(move_id=row.move_id, context=context, fallback=move_name)
                return self._build_row_result(
                    row=row,
                    status="POSTED",
                    message=f"JE existing {move_name} diperbarui dan dipost ulang.",
                    selected_target_mode=selected_target_mode,
                    effective_date=effective_date,
                    move_id=row.move_id,
                    move_name=move_name,
                    posted=posted,
                    debit_account=debit_account,
                    credit_account=credit_account,
                )
            return self._build_row_result(
                row=row,
                status="UPDATED",
                message=f"JE existing {move_name} diperbarui dalam draft only; dashboard belum balance sampai move diposting.",
                selected_target_mode=selected_target_mode,
                effective_date=effective_date,
                move_id=row.move_id,
                move_name=move_name,
                posted=False,
                debit_account=debit_account,
                credit_account=credit_account,
            )
        except Exception:
            if move_state == "posted":
                repost_ok, repost_err = await self._repost_moves(journal_ids=[row.move_id], context=context)
                if not repost_ok:
                    raise RepairOperationError("repost_failed", repost_err)
            raise

    async def _create_new_move_and_relink(
        self,
        *,
        row: SvlDashboardRepairRow,
        target_date: date,
        debit_account: ResolvedAccount,
        credit_account: ResolvedAccount,
        posting_mode: str,
    ) -> SvlDashboardRepairRowResult:
        selected_target_mode = "new_and_relink"
        effective_date = target_date.strftime("%Y-%m-%d")
        if posting_mode == "post":
            await self._ensure_postable_date(company_id=row.company_id, target_date=target_date)

        journal = await self.resolve_journal(company_id=row.company_id, code=row.journal_code or DEFAULT_JOURNAL_CODE)
        if journal is None:
            raise RepairOperationError("journal_not_found", f"Journal '{row.journal_code or DEFAULT_JOURNAL_CODE}' tidak ditemukan.")

        context = build_company_context(row.company_id)
        move_values = {
            "company_id": row.company_id,
            "journal_id": journal.journal_id,
            "date": target_date.strftime("%Y-%m-%d"),
            "ref": normalize_text(row.reference),
            "move_type": "entry",
            "line_ids": self._build_line_commands(
                row=row,
                debit_account_id=debit_account.account_id,
                credit_account_id=credit_account.account_id,
            ),
        }
        move_id = await self.rpc.create(
            "account.move",
            move_values,
            context=context,
            stage="SVL_DASH_REPAIR_CREATE_MOVE",
        )
        if move_id <= 0:
            raise RepairOperationError("create_failed", "Gagal membuat account.move baru.")

        move_name = ""
        move_rows = await self.rpc.read(
            "account.move",
            [move_id],
            fields=["name"],
            context=context,
            stage="SVL_DASH_REPAIR_CREATE_VERIFY",
        )
        if move_rows:
            move_name = normalize_text(move_rows[0].get("name"))

        if row.svl_id > 0:
            relink_ok = await self.rpc.write(
                "stock.valuation.layer",
                [row.svl_id],
                {"account_move_id": move_id},
                context=context,
                stage="SVL_DASH_REPAIR_RELINK",
            )
            if not relink_ok:
                raise RepairOperationError("relink_failed", f"Gagal relink SVL {row.svl_id} ke move baru {move_id}.")

        if posting_mode == "post":
            repost_ok, repost_err = await self._repost_moves(journal_ids=[move_id], context=context)
            if not repost_ok:
                raise RepairOperationError("repost_failed", repost_err)
            move_name = await self._read_move_name(move_id=move_id, context=context, fallback=move_name or str(move_id))
            return self._build_row_result(
                row=row,
                status="POSTED",
                message=f"Move baru {move_name} dibuat, SVL direlink, dan dipost.",
                selected_target_mode=selected_target_mode,
                effective_date=effective_date,
                move_id=move_id,
                move_name=move_name,
                posted=True,
                relinked_svl_id=row.svl_id if row.svl_id > 0 else 0,
                debit_account=debit_account,
                credit_account=credit_account,
            )

        return self._build_row_result(
            row=row,
            status="CREATED",
            message=f"Move baru {move_name or move_id} dibuat dalam draft only; dashboard belum balance sampai move diposting.",
            selected_target_mode=selected_target_mode,
            effective_date=effective_date,
            move_id=move_id,
            move_name=move_name,
            posted=False,
            relinked_svl_id=row.svl_id if row.svl_id > 0 else 0,
            debit_account=debit_account,
            credit_account=credit_account,
        )

    def _build_row_result(
        self,
        *,
        row: SvlDashboardRepairRow,
        status: str,
        message: str,
        selected_target_mode: str,
        effective_date: str,
        move_id: int = 0,
        move_name: str = "",
        posted: bool = False,
        relinked_svl_id: int = 0,
        error_kind: str = "",
        debit_account: ResolvedAccount | None = None,
        credit_account: ResolvedAccount | None = None,
    ) -> SvlDashboardRepairRowResult:
        old_move_action = ""
        old_move_id = 0
        old_move_name = ""
        if selected_target_mode == "new_and_relink" and int(row.move_id or 0) > 0:
            old_move_action = "mark_only"
            old_move_id = int(row.move_id or 0)
            old_move_name = normalize_text(row.move_name)
        return SvlDashboardRepairRowResult(
            row_key=row.row_key,
            status=status,
            company_id=int(row.company_id or 0),
            company_name=normalize_text(row.company_name),
            item_code=normalize_text(row.item_code),
            item_name=normalize_text(row.item_name),
            amount=abs(float(row.amount or 0.0)),
            reference=normalize_text(row.reference),
            message=message,
            selected_target_mode=selected_target_mode,
            effective_date=effective_date,
            error_kind=normalize_text(error_kind),
            move_id=move_id,
            move_name=move_name,
            posted=posted,
            relinked_svl_id=relinked_svl_id,
            old_move_action=old_move_action,
            old_move_id=old_move_id,
            old_move_name=old_move_name,
            svl_reference=normalize_text(row.svl_reference),
            repair_source_kind=normalize_text(row.repair_source_kind),
            repair_source_label=normalize_text(row.repair_source_label),
            debit_account_code=normalize_text((debit_account.code if debit_account is not None else row.debit_account_code)),
            debit_account_name=normalize_text(debit_account.name if debit_account is not None else ""),
            credit_account_code=normalize_text((credit_account.code if credit_account is not None else row.credit_account_code)),
            credit_account_name=normalize_text(credit_account.name if credit_account is not None else ""),
        )

    async def _read_move_name(self, *, move_id: int, context: dict[str, Any], fallback: str = "") -> str:
        if int(move_id or 0) <= 0:
            return normalize_text(fallback)
        move_rows = await self.rpc.read(
            "account.move",
            [move_id],
            fields=["name"],
            context=context,
            stage="SVL_DASH_REPAIR_MOVE_NAME_READ",
        )
        if move_rows:
            move_name = normalize_text(move_rows[0].get("name"))
            if move_name and move_name not in {"/", "False"}:
                return move_name
        clean_fallback = normalize_text(fallback)
        if clean_fallback and clean_fallback not in {"/", "False"}:
            return clean_fallback
        return str(int(move_id or 0))

    async def _read_move_row_by_id(
        self,
        *,
        move_id: int,
        context: dict[str, Any],
    ) -> dict[str, Any] | None:
        if int(move_id or 0) <= 0:
            return None
        move_rows = await self.rpc.read(
            "account.move",
            [int(move_id or 0)],
            fields=["id", "name", "state", "journal_id", "company_id", "partner_id", "date", "ref"],
            context=context,
            stage="SVL_DASH_PCB_CASE1_MOVE_READ",
        )
        return move_rows[0] if move_rows else None

    async def _search_move_rows(
        self,
        *,
        domain: list[Any],
        context: dict[str, Any],
    ) -> list[dict[str, Any]]:
        return await self.rpc.search_read(
            "account.move",
            domain,
            fields=["id", "name", "state", "journal_id", "company_id", "partner_id", "date", "ref"],
            context=context,
            stage="SVL_DASH_PCB_CASE1_MOVE_SEARCH",
            order="id",
        )

    async def _move_matches_pcb_case1_signature(
        self,
        *,
        move_row: dict[str, Any],
        row: SvlDashboardPcbCase1RepairRow,
        debit_account: ResolvedAccount,
        credit_account: ResolvedAccount,
        diff_account: ResolvedAccount | None,
        partner_id: int,
        context: dict[str, Any],
    ) -> bool:
        move_id = int(move_row.get("id") or 0)
        if move_id <= 0:
            return False
        move_lines = await self._read_move_line_rows(move_id=move_id, context=context)
        active_lines = self._pcb_case1_active_lines(move_lines)
        expected_signature = self._build_pcb_case1_expected_signature(
            row=row,
            debit_account=debit_account,
            credit_account=credit_account,
            diff_account=diff_account,
        )
        if len(active_lines) != len(expected_signature):
            return False
        actual_signature = self._build_pcb_case1_actual_signature(active_lines=active_lines)
        for line in active_lines:
            line_partner_id = self._many2one_id(line.get("partner_id"))
            if partner_id > 0 and line_partner_id not in {0, partner_id}:
                return False
        return sorted(actual_signature) == sorted(expected_signature)

    @staticmethod
    def _pcb_case1_active_lines(move_lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [line for line in move_lines if abs(SvlDashboardRepairServiceAsync._move_line_balance(line)) >= 0.01]

    @staticmethod
    def _build_pcb_case1_expected_signature(
        *,
        row: SvlDashboardPcbCase1RepairRow,
        debit_account: ResolvedAccount,
        credit_account: ResolvedAccount,
        diff_account: ResolvedAccount | None,
    ) -> list[tuple[int, float, str]]:
        expected_debit_amount = round(abs(float(row.debit_amount or row.amount or 0.0)), 2)
        expected_credit_amount = round(abs(float(row.credit_amount or row.amount or 0.0)), 2)
        expected_signature: list[tuple[int, float, str]] = []
        if expected_debit_amount >= 0.01:
            expected_signature.append((int(debit_account.account_id or 0), expected_debit_amount, "debit"))
        if expected_credit_amount >= 0.01:
            expected_signature.append((int(credit_account.account_id or 0), expected_credit_amount, "credit"))
        diff_amount = round(abs(float(row.diff_amount or 0.0)), 2)
        diff_side = normalize_text(row.diff_side).lower()
        if diff_account is not None and diff_amount >= 0.01 and diff_side in {"debit", "credit"}:
            expected_signature.append((int(diff_account.account_id or 0), diff_amount, diff_side))
        return expected_signature

    @classmethod
    def _build_pcb_case1_actual_signature(cls, *, active_lines: list[dict[str, Any]]) -> list[tuple[int, float, str]]:
        actual_signature: list[tuple[int, float, str]] = []
        for line in active_lines:
            account_id = cls._many2one_id(line.get("account_id"))
            balance = round(cls._move_line_balance(line), 2)
            if abs(balance) < 0.01:
                continue
            actual_signature.append((account_id, abs(balance), "debit" if balance > 0 else "credit"))
        return actual_signature

    @staticmethod
    def _format_pcb_case1_signature(signature: list[tuple[int, float, str]]) -> str:
        if not signature:
            return "-"
        return "; ".join(
            f"account_id={account_id} side={side} amount={amount:,.2f}"
            for account_id, amount, side in signature
        )

    @classmethod
    def _format_pcb_case1_move_lines(cls, move_lines: list[dict[str, Any]]) -> str:
        if not move_lines:
            return "-"
        formatted_lines: list[str] = []
        for line in move_lines:
            formatted_lines.append(
                (
                    "line_id={line_id} account_id={account_id} partner_id={partner_id} "
                    "debit={debit:,.2f} credit={credit:,.2f} balance={balance:,.2f}"
                ).format(
                    line_id=int(line.get("id") or 0),
                    account_id=cls._many2one_id(line.get("account_id")),
                    partner_id=cls._many2one_id(line.get("partner_id")),
                    debit=float(line.get("debit") or 0.0),
                    credit=float(line.get("credit") or 0.0),
                    balance=cls._move_line_balance(line),
                )
            )
        return " | ".join(formatted_lines)

    async def _find_existing_pcb_case1_move(
        self,
        *,
        row: SvlDashboardPcbCase1RepairRow,
        journal: ResolvedJournal,
        debit_account: ResolvedAccount,
        credit_account: ResolvedAccount,
        diff_account: ResolvedAccount | None,
        partner_id: int,
        move_meta: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any] | None:
        seen_move_ids: set[int] = set()

        async def _check_candidate(candidate_row: dict[str, Any] | None) -> dict[str, Any] | None:
            if not candidate_row:
                return None
            move_id = int(candidate_row.get("id") or 0)
            if move_id <= 0 or move_id in seen_move_ids:
                return None
            candidate_company_id = self._many2one_id(candidate_row.get("company_id"))
            if candidate_company_id not in {0, int(row.company_id or 0)}:
                return None
            candidate_journal_id = self._many2one_id(candidate_row.get("journal_id"))
            if candidate_journal_id not in {0, int(journal.journal_id or 0)}:
                return None
            candidate_date = normalize_text(candidate_row.get("date"))
            if candidate_date and candidate_date != normalize_text(row.date):
                return None
            candidate_ref = normalize_text(candidate_row.get("ref"))
            if candidate_ref and normalize_text(row.reference) and candidate_ref != normalize_text(row.reference):
                return None
            candidate_partner_id = self._many2one_id(candidate_row.get("partner_id"))
            if partner_id > 0 and candidate_partner_id not in {0, partner_id}:
                return None
            seen_move_ids.add(move_id)
            if await self._move_matches_pcb_case1_signature(
                move_row=candidate_row,
                row=row,
                debit_account=debit_account,
                credit_account=credit_account,
                diff_account=diff_account,
                partner_id=partner_id,
                context=context,
            ):
                return candidate_row
            return None

        local_move_id = int(row.result_move_id or 0)
        if local_move_id > 0:
            local_candidate = await _check_candidate(
                await self._read_move_row_by_id(move_id=local_move_id, context=context)
            )
            if local_candidate is not None:
                return local_candidate

        move_domain: list[Any] = []
        self._apply_supported_value(move_domain, move_meta, ("company_id",), None)
        if "company_id" in move_meta:
            move_domain.append(("company_id", "=", int(row.company_id or 0)))
        if "journal_id" in move_meta:
            move_domain.append(("journal_id", "=", int(journal.journal_id or 0)))
        if "date" in move_meta:
            move_domain.append(("date", "=", normalize_text(row.date)))
        if "ref" in move_meta and normalize_text(row.reference):
            move_domain.append(("ref", "=", normalize_text(row.reference)))
        if "partner_id" in move_meta and partner_id > 0:
            move_domain.append(("partner_id", "=", partner_id))
        picking_field = self._pick_supported_field(move_meta, "stock_picking_id", "picking_id")
        if picking_field and int(row.picking_id or 0) > 0:
            move_domain.append((picking_field, "=", int(row.picking_id or 0)))
        bill_field = self._pick_supported_field(move_meta, "bill_move_id", "invoice_id", "bill_id")
        if bill_field and int(row.bill_move_id or 0) > 0:
            move_domain.append((bill_field, "=", int(row.bill_move_id or 0)))
        if not move_domain:
            return None

        search_rows = await self._search_move_rows(domain=move_domain, context=context)
        preferred_move_name = normalize_text(row.result_move_name)
        if preferred_move_name:
            search_rows.sort(key=lambda item: 0 if normalize_text(item.get("name")) == preferred_move_name else 1)
        for candidate in search_rows:
            matched = await _check_candidate(candidate)
            if matched is not None:
                return matched
        return None

    async def _ensure_pcb_case1_move_signature(
        self,
        *,
        move_id: int,
        row: SvlDashboardPcbCase1RepairRow,
        debit_account: ResolvedAccount,
        credit_account: ResolvedAccount,
        diff_account: ResolvedAccount | None,
        partner_id: int,
        line_commands: list[list[Any]],
        context: dict[str, Any],
    ) -> list[str]:
        move_row = await self._read_move_row_by_id(move_id=move_id, context=context)
        if move_row is None:
            raise RepairOperationError(
                "create_readback_failed",
                "JE PCB berhasil dibuat tetapi gagal dibaca ulang untuk verifikasi nominal line.",
            )
        if await self._move_matches_pcb_case1_signature(
            move_row=move_row,
            row=row,
            debit_account=debit_account,
            credit_account=credit_account,
            diff_account=diff_account,
            partner_id=partner_id,
            context=context,
        ):
            return []

        move_lines = await self._read_move_line_rows(move_id=move_id, context=context)
        expected_signature = self._build_pcb_case1_expected_signature(
            row=row,
            debit_account=debit_account,
            credit_account=credit_account,
            diff_account=diff_account,
        )
        actual_active_lines = self._pcb_case1_active_lines(move_lines)
        actual_signature = self._build_pcb_case1_actual_signature(active_lines=actual_active_lines)

        self.logger.warning(
            (
                "PCB Case 1 move %s row %s mismatch setelah create; "
                "expected_active=[%s] actual_active=[%s] actual_lines=[%s]; "
                "rewrite draft line_ids dijalankan."
            ),
            move_id,
            normalize_text(row.row_key),
            self._format_pcb_case1_signature(expected_signature),
            self._format_pcb_case1_signature(actual_signature),
            self._format_pcb_case1_move_lines(move_lines),
        )
        rewrite_context = dict(context or {})
        rewrite_context["check_move_validity"] = False
        rewrite_ok = await self.rpc.write(
            "account.move",
            [int(move_id or 0)],
            {"line_ids": [[5, 0, 0], *line_commands]},
            context=rewrite_context,
            stage="SVL_DASH_PCB_CASE1_CREATE_HEAL",
        )
        if not rewrite_ok:
            raise RepairOperationError(
                "create_line_mismatch",
                "JE PCB berhasil dibuat tetapi nominal line runtime tidak sesuai dan rewrite draft gagal.",
            )
        healed_move_row = await self._read_move_row_by_id(move_id=move_id, context=context)
        if healed_move_row is None or not await self._move_matches_pcb_case1_signature(
            move_row=healed_move_row,
            row=row,
            debit_account=debit_account,
            credit_account=credit_account,
            diff_account=diff_account,
            partner_id=partner_id,
            context=context,
        ):
            healed_move_lines = await self._read_move_line_rows(move_id=move_id, context=context)
            healed_actual_signature = self._build_pcb_case1_actual_signature(
                active_lines=self._pcb_case1_active_lines(healed_move_lines)
            )
            self.logger.error(
                (
                    "PCB Case 1 move %s row %s tetap mismatch setelah rewrite; "
                    "expected_active=[%s] actual_active=[%s] actual_lines=[%s]."
                ),
                move_id,
                normalize_text(row.row_key),
                self._format_pcb_case1_signature(expected_signature),
                self._format_pcb_case1_signature(healed_actual_signature),
                self._format_pcb_case1_move_lines(healed_move_lines),
            )
            raise RepairOperationError(
                "create_line_mismatch",
                "JE PCB berhasil dibuat tetapi nominal line runtime tetap tidak sesuai setelah rewrite draft.",
            )
        return ["Line draft diselaraskan ulang setelah create karena nominal runtime tidak sesuai."]

    async def _resolve_pcb_case2_partner_id(
        self,
        *,
        row: SvlDashboardPcbCase2RepairRow,
        context: dict[str, Any],
    ) -> int:
        explicit_partner_id = int(row.partner_id or 0)
        if explicit_partner_id > 0:
            return explicit_partner_id
        bill_partner_id = await self._read_move_partner_id(move_id=int(row.bill_move_id or 0), context=context)
        if bill_partner_id > 0:
            return bill_partner_id
        return await self._resolve_partner_id(row.company_id, row.partner_name)

    async def _move_matches_pcb_case2_signature(
        self,
        *,
        move_row: dict[str, Any],
        planned_accounts: list[tuple[SvlDashboardPcbRepairPlannedLine, ResolvedAccount]],
        partner_id: int,
        context: dict[str, Any],
    ) -> bool:
        move_id = int(move_row.get("id") or 0)
        if move_id <= 0:
            return False
        move_lines = await self._read_move_line_rows(move_id=move_id, context=context)
        active_lines = [line for line in move_lines if abs(self._move_line_balance(line)) >= 0.01]
        expected_signature = sorted(
            (
                int(account.account_id or 0),
                round(abs(float(planned_line.amount or 0.0)), 2),
                normalize_text(planned_line.side).lower(),
            )
            for planned_line, account in planned_accounts
            if abs(float(planned_line.amount or 0.0)) >= 0.01
        )
        if len(active_lines) != len(expected_signature):
            return False
        actual_signature: list[tuple[int, float, str]] = []
        for line in active_lines:
            account_id = self._many2one_id(line.get("account_id"))
            balance = round(self._move_line_balance(line), 2)
            if abs(balance) < 0.01:
                return False
            line_partner_id = self._many2one_id(line.get("partner_id"))
            if partner_id > 0 and line_partner_id not in {0, partner_id}:
                return False
            actual_signature.append((account_id, abs(balance), "debit" if balance > 0 else "credit"))
        return sorted(actual_signature) == expected_signature

    async def _find_existing_pcb_case2_move(
        self,
        *,
        row: SvlDashboardPcbCase2RepairRow,
        journal: ResolvedJournal,
        planned_accounts: list[tuple[SvlDashboardPcbRepairPlannedLine, ResolvedAccount]],
        partner_id: int,
        move_meta: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any] | None:
        seen_move_ids: set[int] = set()

        async def _check_candidate(candidate_row: dict[str, Any] | None) -> dict[str, Any] | None:
            if not candidate_row:
                return None
            move_id = int(candidate_row.get("id") or 0)
            if move_id <= 0 or move_id in seen_move_ids:
                return None
            candidate_company_id = self._many2one_id(candidate_row.get("company_id"))
            if candidate_company_id not in {0, int(row.company_id or 0)}:
                return None
            candidate_journal_id = self._many2one_id(candidate_row.get("journal_id"))
            if candidate_journal_id not in {0, int(journal.journal_id or 0)}:
                return None
            candidate_date = normalize_text(candidate_row.get("date"))
            if candidate_date and candidate_date != normalize_text(row.date):
                return None
            candidate_ref = normalize_text(candidate_row.get("ref"))
            if candidate_ref and normalize_text(row.reference) and candidate_ref != normalize_text(row.reference):
                return None
            candidate_partner_id = self._many2one_id(candidate_row.get("partner_id"))
            if partner_id > 0 and candidate_partner_id not in {0, partner_id}:
                return None
            seen_move_ids.add(move_id)
            if await self._move_matches_pcb_case2_signature(
                move_row=candidate_row,
                planned_accounts=planned_accounts,
                partner_id=partner_id,
                context=context,
            ):
                return candidate_row
            return None

        local_move_id = int(row.result_move_id or 0)
        if local_move_id > 0:
            local_candidate = await _check_candidate(
                await self._read_move_row_by_id(move_id=local_move_id, context=context)
            )
            if local_candidate is not None:
                return local_candidate

        move_domain: list[Any] = []
        self._apply_supported_value(move_domain, move_meta, ("company_id",), None)
        if "company_id" in move_meta:
            move_domain.append(("company_id", "=", int(row.company_id or 0)))
        if "journal_id" in move_meta:
            move_domain.append(("journal_id", "=", int(journal.journal_id or 0)))
        if "date" in move_meta:
            move_domain.append(("date", "=", normalize_text(row.date)))
        if "ref" in move_meta and normalize_text(row.reference):
            move_domain.append(("ref", "=", normalize_text(row.reference)))
        if "partner_id" in move_meta and partner_id > 0:
            move_domain.append(("partner_id", "=", partner_id))
        picking_field = self._pick_supported_field(move_meta, "stock_picking_id", "picking_id")
        if picking_field and int(row.picking_id or 0) > 0:
            move_domain.append((picking_field, "=", int(row.picking_id or 0)))
        bill_field = self._pick_supported_field(move_meta, "bill_move_id", "invoice_id", "bill_id")
        if bill_field and int(row.bill_move_id or 0) > 0:
            move_domain.append((bill_field, "=", int(row.bill_move_id or 0)))
        if not move_domain:
            return None

        search_rows = await self._search_move_rows(domain=move_domain, context=context)
        preferred_move_name = normalize_text(row.result_move_name)
        if preferred_move_name:
            search_rows.sort(key=lambda item: 0 if normalize_text(item.get("name")) == preferred_move_name else 1)
        for candidate in search_rows:
            matched = await _check_candidate(candidate)
            if matched is not None:
                return matched
        return None

    def _build_line_commands(
        self,
        *,
        row: SvlDashboardRepairRow,
        debit_account_id: int,
        credit_account_id: int,
    ) -> list[list[Any]]:
        amount = abs(float(row.amount or 0.0))
        line_label = normalize_text(row.line_label)
        debit_line = {
            "name": line_label,
            "account_id": debit_account_id,
            "debit": amount,
            "credit": 0.0,
        }
        credit_line = {
            "name": line_label,
            "account_id": credit_account_id,
            "debit": 0.0,
            "credit": amount,
        }
        if row.item_product_id > 0:
            debit_line["product_id"] = row.item_product_id
            credit_line["product_id"] = row.item_product_id
        return [[0, 0, debit_line], [0, 0, credit_line]]

    async def _ensure_postable_date(self, *, company_id: int, target_date: date) -> None:
        lock_date, lock_err = await self._lock_date_service.get_close_acc_date_by_company_id(company_id)
        if lock_err and lock_date is None:
            raise RepairOperationError("lock_date_lookup_failed", f"Gagal membaca accounting lock date company: {lock_err}")
        if lock_date is not None and target_date <= lock_date:
            raise RepairOperationError(
                "lock_date",
                "Tanggal STJ "
                f"{target_date.strftime('%Y-%m-%d')} tidak dapat diubah karena accounting lock date company adalah "
                f"{lock_date.strftime('%Y-%m-%d')}."
            )

    async def _move_posted_to_draft(
        self,
        *,
        journal_ids: list[int],
        context: dict[str, Any],
    ) -> tuple[bool, str, str]:
        attempts: list[str] = []
        for method_name in ("button_draft", "action_draft"):
            try:
                await self.rpc.execute_kw(
                    "account.move",
                    method_name,
                    [journal_ids],
                    kwargs={"context": context},
                    stage="SVL_DASH_REPAIR_TO_DRAFT",
                    mutating=True,
                )
            except Exception as exc:  # noqa: BLE001
                attempts.append(f"{method_name}: {exc}")
                continue

            rows = await self.rpc.read(
                "account.move",
                journal_ids,
                fields=["id", "state"],
                context=context,
                stage="SVL_DASH_REPAIR_TO_DRAFT_VERIFY",
            )
            if all(normalize_text(row.get("state")).lower() != "posted" for row in rows):
                return True, "", method_name
            attempts.append(f"{method_name}: state tetap posted")
        detail = " | ".join(attempts) if attempts else "Metode draft tidak tersedia."
        return False, f"Gagal memindahkan STJ posted ke draft: {detail}", ""

    async def _repost_moves(
        self,
        *,
        journal_ids: list[int],
        context: dict[str, Any],
    ) -> tuple[bool, str]:
        try:
            await self.rpc.execute_kw(
                "account.move",
                "action_post",
                [journal_ids],
                kwargs={"context": context},
                stage="SVL_DASH_REPAIR_POST",
                mutating=True,
            )
        except Exception as exc:  # noqa: BLE001
            return False, f"Gagal restore STJ ke posted: {exc}"
        rows = await self.rpc.read(
            "account.move",
            journal_ids,
            fields=["id", "state"],
            context=context,
            stage="SVL_DASH_REPAIR_POST_VERIFY",
        )
        non_posted = [
            int(row.get("id") or 0)
            for row in rows
            if int(row.get("id") or 0) > 0 and normalize_text(row.get("state")).lower() != "posted"
        ]
        if non_posted:
            return False, f"Gagal restore STJ ke posted: state akhir bukan posted untuk ids={non_posted}"
        return True, ""

    async def _company_domain(self, company_id: int) -> list[Any]:
        if not self._account_company_field:
            self._account_company_field = await detect_account_company_field(self.rpc)
        if not self._account_company_field or company_id <= 0:
            return []
        if self._account_company_field == "company_ids":
            return [(self._account_company_field, "in", [company_id])]
        return [(self._account_company_field, "=", company_id)]

    @staticmethod
    def _parse_date(value: str) -> date:
        parsed = parse_iso_date(normalize_text(value))
        if parsed is None:
            raise RepairOperationError("invalid_date", f"Format tanggal tidak valid: {value}")
        return parsed

    @staticmethod
    def _partner_cache_key(company_id: int, partner_name: str) -> tuple[int, str]:
        return int(company_id or 0), normalize_text(partner_name).lower()

    @staticmethod
    def _chunk_partner_names(names: list[str]) -> list[list[str]]:
        return [
            names[index:index + _PARTNER_PREFETCH_CHUNK_SIZE]
            for index in range(0, len(names), _PARTNER_PREFETCH_CHUNK_SIZE)
        ]

    async def _prefetch_partner_ids(self, partner_refs: Iterable[tuple[int, str]]) -> None:
        key_to_name: dict[tuple[int, str], str] = {}
        for company_id, partner_name in partner_refs:
            clean_name = normalize_text(partner_name)
            if not clean_name:
                continue
            key = self._partner_cache_key(company_id, clean_name)
            if key in self._partner_cache:
                continue
            shared_partner_id = self._master_cache_get_partner_id(company_id=company_id, partner_name=clean_name)
            if shared_partner_id is not None and int(shared_partner_id or 0) > 0:
                self._partner_cache[key] = int(shared_partner_id)
                continue
            key_to_name.setdefault(key, clean_name)
        if not key_to_name:
            return

        names: list[str] = []
        seen_names: set[str] = set()
        for name in key_to_name.values():
            name_key = name.lower()
            if name_key in seen_names:
                continue
            seen_names.add(name_key)
            names.append(name)

        found_by_name: dict[str, int] = {}
        try:
            for name_chunk in self._chunk_partner_names(names):
                rows = await self.rpc.search_read(
                    "res.partner",
                    [("name", "in", name_chunk)],
                    fields=["id", "name"],
                    stage="SVL_DASH_REPAIR_PARTNER_PREFETCH",
                )
                for row in rows:
                    clean_name = normalize_text(row.get("name"))
                    if not clean_name:
                        continue
                    partner_id = int(row.get("id") or 0)
                    if partner_id > 0:
                        found_by_name.setdefault(clean_name.lower(), partner_id)
        except Exception:  # noqa: BLE001
            return

        for key, name in key_to_name.items():
            partner_id = found_by_name.get(name.lower(), 0)
            if partner_id > 0:
                self._partner_cache[key] = partner_id
                self._master_cache_set_partner_id(
                    company_id=key[0],
                    partner_name=name,
                    partner_id=partner_id,
                )
            else:
                self._partner_exact_prefetch_misses.add(key)

    async def _resolve_partner_id(self, company_id: int, partner_name: str) -> int:
        """Best-effort: resolve partner_id by name within the given company. Returns 0 if not found."""
        clean = normalize_text(partner_name)
        if not clean:
            return 0
        key = self._partner_cache_key(company_id, clean)
        if key in self._partner_cache:
            return self._partner_cache[key]
        shared_partner_id = self._master_cache_get_partner_id(company_id=company_id, partner_name=clean)
        if shared_partner_id is not None and int(shared_partner_id or 0) > 0:
            self._partner_cache[key] = int(shared_partner_id)
            return self._partner_cache[key]
        try:
            if key not in self._partner_exact_prefetch_misses:
                domain: list[Any] = [("name", "=", clean)]
                rows = await self.rpc.search_read("res.partner", domain, fields=["id", "name"], limit=1)
                if rows:
                    partner_id = int(rows[0].get("id") or 0)
                    self._partner_cache[key] = partner_id
                    self._master_cache_set_partner_id(
                        company_id=company_id,
                        partner_name=clean,
                        partner_id=partner_id,
                    )
                    return partner_id
            # Fallback: ilike search
            rows = await self.rpc.search_read(
                "res.partner", [("name", "ilike", clean)], fields=["id", "name"], limit=1
            )
            partner_id = int(rows[0].get("id") or 0) if rows else 0
            self._partner_cache[key] = partner_id
            if partner_id > 0:
                self._master_cache_set_partner_id(
                    company_id=company_id,
                    partner_name=clean,
                    partner_id=partner_id,
                )
            return partner_id
        except Exception:  # noqa: BLE001
            self._partner_cache[key] = 0
            return 0

    def _master_cache_get_partner_id(self, *, company_id: int, partner_name: str) -> int | None:
        if self.master_cache is None:
            return None
        base_url, database = rpc_cache_scope(self.rpc)
        return self.master_cache.get_partner_id(
            base_url=base_url,
            database=database,
            company_id=company_id,
            partner_name=partner_name,
        )

    def _master_cache_set_partner_id(self, *, company_id: int, partner_name: str, partner_id: int) -> None:
        if self.master_cache is None:
            return
        base_url, database = rpc_cache_scope(self.rpc)
        self.master_cache.set_partner_id(
            base_url=base_url,
            database=database,
            company_id=company_id,
            partner_name=partner_name,
            partner_id=partner_id,
        )

    async def _read_move_partner_id(self, *, move_id: int, context: dict[str, Any]) -> int:
        clean_move_id = int(move_id or 0)
        if clean_move_id <= 0:
            return 0
        try:
            rows = await self.rpc.read(
                "account.move",
                [clean_move_id],
                fields=["id", "partner_id"],
                context=context,
                stage="SVL_DASH_PCB_CASE1_MOVE_PARTNER_READ",
            )
        except Exception:  # noqa: BLE001
            return 0
        if not rows:
            return 0
        return self._many2one_id(rows[0].get("partner_id"))

    async def _resolve_pcb_case1_partner_id(
        self,
        *,
        row: SvlDashboardPcbCase1RepairRow,
        context: dict[str, Any],
    ) -> int:
        explicit_partner_id = int(row.partner_id or 0)
        if explicit_partner_id > 0:
            return explicit_partner_id

        candidate_partner_ids: set[int] = set()
        suspend_rows = await self._read_target_move_line_rows(
            target_ids=list(row.suspend_target_aml_ids or []),
            context=context,
        )
        for target_row in suspend_rows:
            partner_id = self._many2one_id(target_row.get("partner_id"))
            if partner_id > 0:
                candidate_partner_ids.add(partner_id)
        if len(candidate_partner_ids) == 1:
            return next(iter(candidate_partner_ids))

        bill_partner_id = await self._read_move_partner_id(move_id=int(row.bill_move_id or 0), context=context)
        if bill_partner_id > 0:
            return bill_partner_id

        if len(candidate_partner_ids) > 1:
            return 0
        return await self._resolve_partner_id(row.company_id, row.partner_name)

    async def execute_pcb_case1(self, request: SvlDashboardPcbCase1RepairRequest) -> SvlDashboardPcbCase1RepairBatchResult:
        await self.rpc.ensure_login()
        await self._prefetch_partner_ids(
            (row.company_id, row.partner_name)
            for row in request.rows
            if int(row.partner_id or 0) <= 0
        )
        worker_count = max(1, int(request.max_workers or 1))
        semaphore = asyncio.Semaphore(worker_count)

        async def run_row(index: int, row: SvlDashboardPcbCase1RepairRow) -> tuple[int, SvlDashboardPcbCase1RepairRowResult]:
            async with semaphore:
                return index, await self._execute_pcb_case1_row_safe(
                    row,
                    posting_mode=normalize_text(request.posting_mode).lower() or "draft",
                )

        ordered_results: list[SvlDashboardPcbCase1RepairRowResult | None] = [None] * len(request.rows)
        if request.rows:
            tasks = [asyncio.create_task(run_row(index, row)) for index, row in enumerate(request.rows)]
            for task in asyncio.as_completed(tasks):
                index, result = await task
                ordered_results[index] = result
        results = [result for result in ordered_results if result is not None]
        warnings = [result.reconcile_message for result in results if result.reconcile_skipped and result.reconcile_message]
        return SvlDashboardPcbCase1RepairBatchResult(
            database=request.database,
            results=results,
            created_count=sum(
                1 for result in results if result.status in {"CREATED", "POSTED"} and not bool(result.existing_move_detected)
            ),
            posted_count=sum(1 for result in results if result.posted),
            existing_count=sum(1 for result in results if bool(result.existing_move_detected)),
            reconciled_count=sum(1 for result in results if result.reconcile_performed),
            reconcile_skipped_count=sum(1 for result in results if result.reconcile_skipped),
            error_count=sum(1 for result in results if normalize_text(result.status).upper() == "ERROR"),
            warnings=warnings,
        )

    async def execute_pcb_case2(self, request: SvlDashboardPcbCase2RepairRequest) -> SvlDashboardPcbCase2RepairBatchResult:
        await self.rpc.ensure_login()
        await self._prefetch_partner_ids(
            (row.company_id, row.partner_name)
            for row in request.rows
            if int(row.partner_id or 0) <= 0
        )
        worker_count = max(1, int(request.max_workers or 1))
        semaphore = asyncio.Semaphore(worker_count)

        async def run_row(index: int, row: SvlDashboardPcbCase2RepairRow) -> tuple[int, SvlDashboardPcbCase2RepairRowResult]:
            async with semaphore:
                return index, await self._execute_pcb_case2_row_safe(
                    row,
                    posting_mode=normalize_text(request.posting_mode).lower() or "draft",
                )

        ordered_results: list[SvlDashboardPcbCase2RepairRowResult | None] = [None] * len(request.rows)
        if request.rows:
            tasks = [asyncio.create_task(run_row(index, row)) for index, row in enumerate(request.rows)]
            for task in asyncio.as_completed(tasks):
                index, result = await task
                ordered_results[index] = result
        results = [result for result in ordered_results if result is not None]
        return SvlDashboardPcbCase2RepairBatchResult(
            database=request.database,
            results=results,
            created_count=sum(
                1 for result in results if result.status in {"CREATED", "POSTED"} and not bool(result.existing_move_detected)
            ),
            posted_count=sum(1 for result in results if result.posted),
            existing_count=sum(1 for result in results if bool(result.existing_move_detected)),
            error_count=sum(1 for result in results if normalize_text(result.status).upper() == "ERROR"),
            warnings=[],
        )

    async def _execute_pcb_case1_row_safe(
        self,
        row: SvlDashboardPcbCase1RepairRow,
        *,
        posting_mode: str,
    ) -> SvlDashboardPcbCase1RepairRowResult:
        try:
            return await self._execute_pcb_case1_row(row, posting_mode=posting_mode)
        except RepairOperationError as exc:
            return SvlDashboardPcbCase1RepairRowResult(
                row_key=row.row_key,
                status="ERROR",
                message=exc.message,
                error_kind=exc.error_kind,
            )
        except Exception as exc:  # noqa: BLE001
            return SvlDashboardPcbCase1RepairRowResult(
                row_key=row.row_key,
                status="ERROR",
                message=str(exc),
                error_kind="unknown",
            )

    async def _execute_pcb_case2_row_safe(
        self,
        row: SvlDashboardPcbCase2RepairRow,
        *,
        posting_mode: str,
    ) -> SvlDashboardPcbCase2RepairRowResult:
        try:
            return await self._execute_pcb_case2_row(row, posting_mode=posting_mode)
        except RepairOperationError as exc:
            return SvlDashboardPcbCase2RepairRowResult(
                row_key=row.row_key,
                status="ERROR",
                message=exc.message,
                error_kind=exc.error_kind,
            )
        except Exception as exc:  # noqa: BLE001
            return SvlDashboardPcbCase2RepairRowResult(
                row_key=row.row_key,
                status="ERROR",
                message=str(exc),
                error_kind="unknown",
            )

    @staticmethod
    def _pcb_case1_fallback_line_label(row: Any) -> str:
        item_label = " - ".join(
            part
            for part in (
                normalize_text(row.get("item_code")) if isinstance(row, dict) else normalize_text(getattr(row, "item_code", "")),
                normalize_text(row.get("item_name")) if isinstance(row, dict) else normalize_text(getattr(row, "item_name", "")),
            )
            if part
        )
        picking_name = normalize_text(row.get("picking_name")) if isinstance(row, dict) else normalize_text(getattr(row, "picking_name", ""))
        if item_label:
            return f"{item_label} - Purchase Cycle Balance"
        return picking_name or "Purchase Cycle Balance"

    @staticmethod
    def _build_pcb_case1_row_from_mapping(row: dict[str, Any]) -> SvlDashboardPcbCase1RepairRow:
        allowed_fields = set(SvlDashboardPcbCase1RepairRow.__dataclass_fields__)
        payload = {field_name: row.get(field_name) for field_name in allowed_fields if field_name in row}
        payload["row_key"] = normalize_text(payload.get("row_key")) or f"pcb::{normalize_text(row.get('source_key'))}"
        payload["company_id"] = int(payload.get("company_id") or 0)
        payload["amount"] = abs(float(payload.get("amount") or 0.0))
        payload["amount_currency_basis"] = abs(float(payload.get("amount_currency_basis") or 0.0))
        payload["debit_amount"] = round(abs(float(payload.get("debit_amount") or 0.0)), 2)
        payload["credit_amount"] = round(abs(float(payload.get("credit_amount") or 0.0)), 2)
        payload["diff_amount"] = round(abs(float(payload.get("diff_amount") or 0.0)), 2)
        payload["diff_account_code"] = normalize_text(payload.get("diff_account_code")).upper()
        payload["diff_account_name"] = normalize_text(payload.get("diff_account_name"))
        payload["diff_side"] = normalize_text(payload.get("diff_side")).lower()
        payload["date"] = normalize_text(payload.get("date")) or date.today().strftime("%Y-%m-%d")
        payload["reference"] = normalize_text(payload.get("reference"))
        payload["line_label"] = normalize_text(payload.get("line_label")) or SvlDashboardRepairServiceAsync._pcb_case1_fallback_line_label(row)
        for field_name in (
            "stock_move_ids",
            "stj_move_ids",
            "payment_move_ids",
            "bank_move_ids",
            "suspend_target_aml_ids",
            "clearing_target_aml_ids",
        ):
            value = payload.get(field_name)
            if isinstance(value, list):
                payload[field_name] = [int(item or 0) for item in value if int(item or 0) > 0]
            else:
                payload[field_name] = []
        stj_refs = payload.get("stj_refs")
        if isinstance(stj_refs, list):
            payload["stj_refs"] = [normalize_text(item) for item in stj_refs if normalize_text(item)]
        else:
            payload["stj_refs"] = []
        payload["analytic_distribution"] = row.get("analytic_distribution", False)
        return SvlDashboardPcbCase1RepairRow(**payload)

    @staticmethod
    def _pcb_case2_fallback_line_label(row: Any) -> str:
        item_label = " - ".join(
            part
            for part in (
                normalize_text(row.get("item_code")) if isinstance(row, dict) else normalize_text(getattr(row, "item_code", "")),
                normalize_text(row.get("item_name")) if isinstance(row, dict) else normalize_text(getattr(row, "item_name", "")),
            )
            if part
        )
        if item_label:
            return f"{item_label} - Purchase Cycle Balance"
        picking_name = normalize_text(row.get("picking_name")) if isinstance(row, dict) else normalize_text(getattr(row, "picking_name", ""))
        return picking_name or "Purchase Cycle Balance"

    @staticmethod
    def _pcb_case2_role_label(role: Any) -> str:
        normalized_role = normalize_text(role).lower()
        if normalized_role.startswith("case8_"):
            return "Case 8 return value"
        if normalized_role.startswith("case9_"):
            return "Case 9 UoM scale"
        if normalized_role.startswith("variance_zero_"):
            return "Zero Selisih HPP"
        if normalized_role == "hpp_zero" or normalized_role.startswith("hpp_zero_"):
            return "Zero HPP"
        if normalized_role == "selisih_hpp":
            return "Selisih HPP"
        if normalized_role.startswith("problem_"):
            account_code = normalized_role.split("_", 1)[1]
            return f"problem account {account_code}".strip()
        return normalize_text(role) or "planned"

    @staticmethod
    def _build_pcb_case2_planned_line_from_mapping(value: Any) -> SvlDashboardPcbRepairPlannedLine | None:
        if isinstance(value, SvlDashboardPcbRepairPlannedLine):
            return value
        if not isinstance(value, dict):
            return None
        try:
            amount = abs(float(value.get("amount") or 0.0))
        except (TypeError, ValueError):
            amount = 0.0
        return SvlDashboardPcbRepairPlannedLine(
            role=normalize_text(value.get("role")),
            account_code=normalize_text(value.get("account_code")).upper(),
            account_name=normalize_text(value.get("account_name")),
            amount=amount,
            side=normalize_text(value.get("side")).lower(),
            line_label=normalize_pcb_planned_line_label(
                role=value.get("role"),
                line_label=value.get("line_label"),
            ),
            source_balance=round(float(value.get("source_balance") or 0.0), 2),
        )

    @staticmethod
    def _build_pcb_case2_row_from_mapping(row: dict[str, Any]) -> SvlDashboardPcbCase2RepairRow:
        allowed_fields = set(SvlDashboardPcbCase2RepairRow.__dataclass_fields__)
        payload = {field_name: row.get(field_name) for field_name in allowed_fields if field_name in row}
        payload["row_key"] = normalize_text(payload.get("row_key")) or f"pcb-case2::{normalize_text(row.get('cycle_key'))}"
        payload["company_id"] = int(payload.get("company_id") or 0)
        payload["amount"] = abs(float(payload.get("amount") or 0.0))
        payload["date"] = normalize_text(payload.get("date")) or date.today().strftime("%Y-%m-%d")
        payload["reference"] = normalize_text(payload.get("reference"))
        payload["line_label"] = normalize_text(payload.get("line_label")) or SvlDashboardRepairServiceAsync._pcb_case2_fallback_line_label(row)
        payload["bank_account_codes"] = [
            normalize_text(value).upper()
            for value in list(payload.get("bank_account_codes") or [])
            if normalize_text(value)
        ]
        raw_bank_balances = payload.get("bank_balances_by_code")
        if isinstance(raw_bank_balances, dict):
            payload["bank_balances_by_code"] = {
                normalize_text(key).upper(): round(float(value or 0.0), 2)
                for key, value in raw_bank_balances.items()
                if normalize_text(key)
            }
        else:
            payload["bank_balances_by_code"] = {}
        raw_problem_balances = payload.get("problem_balances_by_code")
        if isinstance(raw_problem_balances, dict):
            payload["problem_balances_by_code"] = {
                normalize_text(key).upper(): round(float(value or 0.0), 2)
                for key, value in raw_problem_balances.items()
                if normalize_text(key)
            }
        else:
            payload["problem_balances_by_code"] = {}
        raw_hpp_balances = payload.get("hpp_balances_by_code")
        if isinstance(raw_hpp_balances, dict):
            payload["hpp_balances_by_code"] = {
                normalize_text(key).upper(): round(float(value or 0.0), 2)
                for key, value in raw_hpp_balances.items()
                if normalize_text(key)
            }
        else:
            payload["hpp_balances_by_code"] = {}
        if not payload["problem_balances_by_code"]:
            legacy_suspend_balance = round(float(payload.get("suspend_balance") or 0.0), 2)
            if abs(legacy_suspend_balance) >= 0.01:
                payload["problem_balances_by_code"] = {"2103006": legacy_suspend_balance}
        payload["guard_flags"] = [normalize_text(value) for value in list(payload.get("guard_flags") or []) if normalize_text(value)]
        payload["guard_messages"] = [normalize_text(value) for value in list(payload.get("guard_messages") or []) if normalize_text(value)]
        payload["stock_move_ids"] = [
            int(value or 0)
            for value in list(payload.get("stock_move_ids") or [])
            if int(value or 0) > 0
        ]
        payload["stj_move_ids"] = [
            int(value or 0)
            for value in list(payload.get("stj_move_ids") or [])
            if int(value or 0) > 0
        ]
        payload["stj_refs"] = [
            normalize_text(value)
            for value in list(payload.get("stj_refs") or [])
            if normalize_text(value)
        ]
        payload["payment_move_ids"] = [
            int(value or 0)
            for value in list(payload.get("payment_move_ids") or [])
            if int(value or 0) > 0
        ]
        payload["bank_move_ids"] = [
            int(value or 0)
            for value in list(payload.get("bank_move_ids") or [])
            if int(value or 0) > 0
        ]
        payload["suspend_target_aml_ids"] = [
            int(value or 0)
            for value in list(payload.get("suspend_target_aml_ids") or [])
            if int(value or 0) > 0
        ]
        payload["clearing_target_aml_ids"] = [
            int(value or 0)
            for value in list(payload.get("clearing_target_aml_ids") or [])
            if int(value or 0) > 0
        ]
        planned_lines: list[SvlDashboardPcbRepairPlannedLine] = []
        for planned_line in list(payload.get("planned_lines") or []):
            normalized_line = SvlDashboardRepairServiceAsync._build_pcb_case2_planned_line_from_mapping(planned_line)
            if normalized_line is not None:
                normalized_line.line_label = normalize_pcb_planned_line_label(
                    role=normalized_line.role,
                    line_label=normalized_line.line_label,
                    default_line_label=payload["line_label"],
                )
                planned_lines.append(normalized_line)
        payload["planned_lines"] = planned_lines
        payload["product_uom_id"] = int(payload.get("product_uom_id") or 0)
        payload["quantity"] = round(float(payload.get("quantity") or 0.0), 2)
        payload["currency_id"] = int(payload.get("currency_id") or 0)
        payload["amount_currency"] = round(float(payload.get("amount_currency") or 0.0), 2)
        payload["amount_currency_basis"] = round(float(payload.get("amount_currency_basis") or 0.0), 2)
        payload["analytic_distribution"] = payload.get("analytic_distribution", False)
        payload["bill_price_unit"] = round(float(payload.get("bill_price_unit") or 0.0), 2)
        payload["gr_price_unit"] = round(float(payload.get("gr_price_unit") or 0.0), 2)
        payload["price_gap_value"] = round(float(payload.get("price_gap_value") or 0.0), 2)
        payload["allocated_amount"] = round(float(payload.get("allocated_amount") or 0.0), 2)
        payload["repair_basis_amount"] = round(float(payload.get("repair_basis_amount") or 0.0), 2)
        payload["repair_basis_source"] = normalize_text(payload.get("repair_basis_source"))
        payload["standard_price"] = round(float(payload.get("standard_price") or 0.0), 2)
        payload["suspend_balance"] = round(float(payload.get("suspend_balance") or 0.0), 2)
        payload["hpp_balance"] = round(float(payload.get("hpp_balance") or 0.0), 2)
        payload["inventory_balance"] = round(float(payload.get("inventory_balance") or 0.0), 2)
        payload["cogs_variance_balance"] = round(float(payload.get("cogs_variance_balance") or 0.0), 2)
        payload["selisih_hpp_amount"] = round(float(payload.get("selisih_hpp_amount") or 0.0), 2)
        payload["coefficient_variance"] = round(float(payload.get("coefficient_variance") or 0.0), 2)
        payload["resolve_account_code"] = normalize_text(payload.get("resolve_account_code")).upper()
        payload["resolve_account_preview"] = normalize_text(payload.get("resolve_account_preview"))
        payload["review_required"] = bool(payload.get("review_required"))
        payload["review_confirmed"] = bool(payload.get("review_confirmed"))
        payload["review_reason"] = normalize_text(payload.get("review_reason"))
        return SvlDashboardPcbCase2RepairRow(**payload)

    @staticmethod
    def _apply_supported_value(
        values: dict[str, Any],
        meta: dict[str, Any],
        field_names: tuple[str, ...],
        value: Any,
        *,
        allow_zero: bool = False,
    ) -> None:
        if value is None or value is False or value == "" or value in ([], {}, ()):
            return
        if (
            not allow_zero
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
            and abs(float(value)) < 0.0000001
        ):
            return
        field_name = SvlDashboardRepairServiceAsync._pick_supported_field(meta, *field_names)
        if field_name:
            values[field_name] = value

    def _build_pcb_case2_simulation_lines(
        self,
        *,
        row: SvlDashboardPcbCase2RepairRow,
    ) -> list[dict[str, Any]]:
        default_line_label = normalize_text(row.line_label) or self._pcb_case2_fallback_line_label(row)
        simulated_lines: list[dict[str, Any]] = []
        for planned_line_index, planned_line in enumerate(list(row.planned_lines or [])):
            amount = abs(round(float(planned_line.amount or 0.0), 2))
            side = normalize_text(planned_line.side).lower()
            if amount <= 0.0 or side not in {"debit", "credit"}:
                continue
            simulated_lines.append(
                {
                    "line_index": len(simulated_lines),
                    "planned_line_index": planned_line_index,
                    "role": normalize_text(planned_line.role),
                    "line_label": normalize_pcb_planned_line_label(
                        role=planned_line.role,
                        line_label=planned_line.line_label,
                        default_line_label=default_line_label,
                    ),
                    "side": side,
                    "account_code": normalize_text(planned_line.account_code).upper(),
                    "account_name": normalize_text(planned_line.account_name),
                    "amount": amount,
                }
            )
        return simulated_lines

    @staticmethod
    def _build_pcb_case2_signature_from_simulated_lines(
        resolved_simulation_lines: list[tuple[dict[str, Any], ResolvedAccount]],
    ) -> list[tuple[int, str, str, float, str]]:
        return [
            (
                int(line.get("line_index") or index),
                normalize_text(account.code).upper(),
                normalize_text(line.get("side")).lower(),
                round(abs(float(line.get("amount") or 0.0)), 2),
                normalize_text(line.get("line_label")),
            )
            for index, (line, account) in enumerate(resolved_simulation_lines)
        ]

    @classmethod
    def _build_pcb_case2_signature_from_line_commands(
        cls,
        *,
        line_commands: list[list[Any]],
        account_code_by_id: dict[int, str],
    ) -> list[tuple[int, str, str, float, str]]:
        signature: list[tuple[int, str, str, float, str]] = []
        for index, command in enumerate(line_commands):
            line_values = dict(command[2]) if isinstance(command, (list, tuple)) and len(command) >= 3 else {}
            debit_amount = round(abs(float(line_values.get("debit") or 0.0)), 2)
            credit_amount = round(abs(float(line_values.get("credit") or 0.0)), 2)
            side = "debit" if debit_amount >= 0.01 else "credit" if credit_amount >= 0.01 else ""
            amount = debit_amount if side == "debit" else credit_amount
            account_id = int(line_values.get("account_id") or 0)
            signature.append(
                (
                    index,
                    normalize_text(account_code_by_id.get(account_id, "")).upper() or "?",
                    side,
                    amount,
                    normalize_text(line_values.get("name")),
                )
            )
        return signature

    @classmethod
    def _assert_pcb_case2_payload_matches_simulation(
        cls,
        *,
        resolved_simulation_lines: list[tuple[dict[str, Any], ResolvedAccount]],
        line_commands: list[list[Any]],
    ) -> None:
        expected_signature = cls._build_pcb_case2_signature_from_simulated_lines(resolved_simulation_lines)
        account_code_by_id = {
            int(account.account_id or 0): normalize_text(account.code).upper()
            for _line, account in resolved_simulation_lines
            if int(account.account_id or 0) > 0
        }
        actual_signature = cls._build_pcb_case2_signature_from_line_commands(
            line_commands=line_commands,
            account_code_by_id=account_code_by_id,
        )
        if actual_signature != expected_signature:
            raise RepairOperationError(
                "payload_mismatch",
                "Payload PCB Case 2 berbeda dari jurnal simulasi final; execute diblok untuk mencegah nominal/label yang tidak sama.",
            )

    def _build_pcb_case1_line_commands(
        self,
        *,
        row: SvlDashboardPcbCase1RepairRow,
        debit_account: ResolvedAccount,
        credit_account: ResolvedAccount,
        diff_account: ResolvedAccount | None,
        partner_id: int,
        move_line_meta: dict[str, Any],
    ) -> tuple[list[list[Any]], list[str]]:
        debit_amount = round(abs(float(row.debit_amount or row.amount or 0.0)), 2)
        credit_amount = round(abs(float(row.credit_amount or row.amount or 0.0)), 2)
        if debit_amount <= 0.0 and credit_amount <= 0.0:
            raise RepairOperationError("zero_amount", "Nominal 0, tidak ada yang perlu dikoreksi.")
        expected_diff_amount = round(abs(debit_amount - credit_amount), 2)
        diff_amount = round(abs(float(row.diff_amount or expected_diff_amount or 0.0)), 2)
        diff_side = normalize_text(row.diff_side).lower()
        include_diff = bool(
            diff_account is not None
            and diff_amount >= 0.01
            and diff_side in {"debit", "credit"}
        )
        if expected_diff_amount >= 0.01 and not include_diff:
            raise RepairOperationError(
                "diff_account_missing",
                "PCB Case 1 memiliki selisih nominal tetapi akun selisih/arah line belum lengkap.",
        )
        line_label = normalize_text(row.line_label) or self._pcb_case1_fallback_line_label(row)
        analytic_distribution = self._normalize_analytic_distribution(row.analytic_distribution)
        line_warnings: list[str] = []

        def build_line(*, account_id: int, debit: float, credit: float) -> list[Any]:
            line_values: dict[str, Any] = {
                "name": line_label,
                "account_id": account_id,
                "debit": round(float(debit or 0.0), 2),
                "credit": round(float(credit or 0.0), 2),
            }
            self._apply_supported_value(line_values, move_line_meta, ("product_id",), int(row.product_id or 0))
            self._apply_supported_value(line_values, move_line_meta, ("product_uom_id",), int(row.product_uom_id or 0))
            self._apply_supported_value(line_values, move_line_meta, ("quantity",), float(row.quantity or 0.0))
            self._apply_supported_value(line_values, move_line_meta, ("purchase_line_id",), int(row.purchase_line_id or 0))
            self._apply_supported_value(line_values, move_line_meta, ("partner_id",), partner_id)
            self._apply_supported_value(line_values, move_line_meta, ("analytic_distribution",), analytic_distribution)
            self._apply_supported_value(line_values, move_line_meta, ("stock_move_id",), int(row.stock_move_id or 0))
            self._apply_supported_value(line_values, move_line_meta, ("stock_picking_id", "picking_id"), int(row.picking_id or 0))
            self._apply_supported_value(line_values, move_line_meta, ("bill_line_id", "vendor_bill_line_id"), int(row.bill_line_id or 0))
            self._apply_supported_value(line_values, move_line_meta, ("bill_move_id", "invoice_id", "bill_id"), int(row.bill_move_id or 0))
            return [0, 0, line_values]

        line_commands = [
            build_line(
                account_id=debit_account.account_id,
                debit=debit_amount,
                credit=0.0,
            ),
            build_line(
                account_id=credit_account.account_id,
                debit=0.0,
                credit=credit_amount,
            ),
        ]
        if include_diff and diff_account is not None:
            line_commands.append(
                build_line(
                    account_id=diff_account.account_id,
                    debit=(diff_amount if diff_side == "debit" else 0.0),
                    credit=(diff_amount if diff_side == "credit" else 0.0),
                )
            )
        total_debit = round(sum(float(command[2].get("debit") or 0.0) for command in line_commands), 2)
        total_credit = round(sum(float(command[2].get("credit") or 0.0) for command in line_commands), 2)
        if abs(round(total_debit - total_credit, 2)) >= 0.01:
            raise RepairOperationError(
                "unbalanced_move",
                f"Planned line PCB Case 1 tidak balance: debit {total_debit:,.2f} vs credit {total_credit:,.2f}.",
            )
        return (
            line_commands,
            line_warnings,
        )

    def _build_pcb_case2_line_commands(
        self,
        *,
        row: SvlDashboardPcbCase2RepairRow,
        resolved_simulation_lines: list[tuple[dict[str, Any], ResolvedAccount]],
        partner_id: int,
        move_line_meta: dict[str, Any],
    ) -> list[list[Any]]:
        if not resolved_simulation_lines:
            raise RepairOperationError("zero_line", "Planned line PCB Case 2 kosong, tidak ada yang perlu dikoreksi.")
        line_label = normalize_text(row.line_label) or self._pcb_case2_fallback_line_label(row)
        line_commands: list[list[Any]] = []
        total_debit = 0.0
        total_credit = 0.0
        for simulated_line, account in resolved_simulation_lines:
            amount = abs(round(float(simulated_line.get("amount") or 0.0), 2))
            side = normalize_text(simulated_line.get("side")).lower()
            if amount <= 0.0 or side not in {"debit", "credit"}:
                continue
            line_values: dict[str, Any] = {
                "name": normalize_text(simulated_line.get("line_label")) or line_label,
                "account_id": int(account.account_id or 0),
                "debit": amount if side == "debit" else 0.0,
                "credit": amount if side == "credit" else 0.0,
            }
            self._apply_supported_value(line_values, move_line_meta, ("product_id",), int(row.product_id or 0))
            self._apply_supported_value(line_values, move_line_meta, ("partner_id",), partner_id)
            self._apply_supported_value(line_values, move_line_meta, ("purchase_line_id",), int(row.purchase_line_id or 0))
            if dict(row.case_evidence or {}).get("flow") == "normal_receipt":
                role = normalize_text(simulated_line.get("role"))
                qty = float(row.case_evidence.get("missing_qty") or 0) if role in {"receipt_inventory", "receipt_counterpart"} else 0.0
                self._apply_supported_value(line_values, move_line_meta, ("quantity",), qty, allow_zero=True)
                self._apply_supported_value(line_values, move_line_meta, ("product_uom_id",), int(row.product_uom_id or 0))
                self._apply_supported_value(line_values, move_line_meta, ("analytic_distribution",), row.analytic_distribution)
            self._apply_supported_value(line_values, move_line_meta, ("stock_move_id",), int(row.stock_move_id or 0))
            self._apply_supported_value(line_values, move_line_meta, ("stock_picking_id", "picking_id"), int(row.picking_id or 0))
            self._apply_supported_value(line_values, move_line_meta, ("bill_line_id", "vendor_bill_line_id"), int(row.bill_line_id or 0))
            self._apply_supported_value(line_values, move_line_meta, ("bill_move_id", "invoice_id", "bill_id"), int(row.bill_move_id or 0))
            line_commands.append([0, 0, line_values])
            total_debit = round(total_debit + float(line_values["debit"]), 2)
            total_credit = round(total_credit + float(line_values["credit"]), 2)
        if not line_commands:
            raise RepairOperationError("zero_line", "Planned line PCB Case 2 kosong setelah filtering nominal nol.")
        if abs(round(total_debit - total_credit, 2)) >= 0.01:
            raise RepairOperationError(
                "unbalanced_move",
                f"Planned line PCB Case 2 tidak balance: debit {total_debit:,.2f} vs credit {total_credit:,.2f}.",
            )
        self._assert_pcb_case2_payload_matches_simulation(
            resolved_simulation_lines=resolved_simulation_lines,
            line_commands=line_commands,
        )
        return line_commands

    async def _read_move_line_rows(self, *, move_id: int, context: dict[str, Any]) -> list[dict[str, Any]]:
        move_line_meta = await self._fields_get_cached("account.move.line")
        fields = self._build_target_move_line_fields(move_line_meta)
        return await self.rpc.search_read(
            "account.move.line",
            [("move_id", "=", int(move_id or 0))],
            fields=list(dict.fromkeys(fields)),
            context=context,
            stage="SVL_DASH_PCB_CASE1_MOVE_LINES",
            order="id",
        )

    async def _read_target_move_line_rows(
        self,
        *,
        target_ids: list[int],
        context: dict[str, Any],
    ) -> list[dict[str, Any]]:
        clean_ids = [int(item or 0) for item in target_ids if int(item or 0) > 0]
        if not clean_ids:
            return []
        move_line_meta = await self._fields_get_cached("account.move.line")
        fields = self._build_target_move_line_fields(move_line_meta)
        return await self.rpc.search_read(
            "account.move.line",
            [("id", "in", clean_ids)],
            fields=list(dict.fromkeys(fields)),
            context=context,
            stage="SVL_DASH_PCB_CASE1_TARGET_LINES",
            order="id",
        )

    @staticmethod
    def _build_target_move_line_fields(move_line_meta: dict[str, Any]) -> list[str]:
        fields = [
            "id",
            "move_id",
            "account_id",
            "partner_id",
            "currency_id",
            "debit",
            "credit",
            "reconciled",
        ]
        for optional_field in ("balance", "amount_currency", "amount_residual", "amount_residual_currency"):
            if optional_field in move_line_meta:
                fields.append(optional_field)
        return fields

    async def _read_runtime_target_move_line_rows(
        self,
        *,
        move_ids: list[int],
        account_id: int,
        context: dict[str, Any],
    ) -> list[dict[str, Any]]:
        clean_move_ids = [int(item or 0) for item in move_ids if int(item or 0) > 0]
        clean_account_id = int(account_id or 0)
        if not clean_move_ids or clean_account_id <= 0:
            return []
        move_line_meta = await self._fields_get_cached("account.move.line")
        fields = self._build_target_move_line_fields(move_line_meta)
        return await self.rpc.search_read(
            "account.move.line",
            [("move_id", "in", clean_move_ids), ("account_id", "=", clean_account_id)],
            fields=list(dict.fromkeys(fields)),
            context=context,
            stage="SVL_DASH_PCB_CASE1_RUNTIME_TARGET_LINES",
            order="move_id,id",
        )

    @staticmethod
    def _merge_move_line_rows(*row_groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
        merged: dict[int | str, dict[str, Any]] = {}
        synthetic_index = 0
        for row_group in row_groups:
            for row in row_group:
                line_id = int(row.get("id") or 0)
                key: int | str = line_id if line_id > 0 else f"line::{synthetic_index}"
                merged[key] = row
                synthetic_index += 1
        return list(merged.values())

    @staticmethod
    def _pcb_case1_runtime_target_move_ids(
        row: SvlDashboardPcbCase1RepairRow,
        *,
        account_code: str,
    ) -> list[int]:
        clean_account_code = normalize_text(account_code)
        if clean_account_code == "2103006":
            move_id = int(row.bill_move_id or 0)
            return [move_id] if move_id > 0 else []
        if clean_account_code == "1108099":
            return [int(move_id) for move_id in list(row.stj_move_ids or []) if int(move_id or 0) > 0]
        return []

    async def _heal_move_line_amount_currency(
        self,
        *,
        lines: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], list[str]]:
        refreshed_by_id = {
            int(line.get("id") or 0): line
            for line in lines
            if int(line.get("id") or 0) > 0
        }
        messages: list[str] = []
        for line_id, line in list(refreshed_by_id.items()):
            null_fields = self._move_line_null_amount_fields(line)
            if not null_fields:
                continue
            current_amount_currency = self._move_line_amount_currency_value(line)
            sanitized_amount_currency = round(float(current_amount_currency or 0.0), 2) if current_amount_currency is not _FIELD_NOT_PRESENT else 0.0
            try:
                write_ok = await self.rpc.write(
                    "account.move.line",
                    [line_id],
                    {"amount_currency": sanitized_amount_currency},
                    context=context,
                    stage="SVL_DASH_PCB_CASE1_TARGET_HEAL",
                )
            except Exception as exc:  # noqa: BLE001
                messages.append(
                    f"AML {line_id}: self-heal amount_currency gagal ({', '.join(null_fields)}): {exc}"
                )
                continue
            if not write_ok:
                messages.append(
                    f"AML {line_id}: self-heal amount_currency gagal ({', '.join(null_fields)})."
                )
                continue
            reread_rows = await self._read_target_move_line_rows(target_ids=[line_id], context=context)
            refreshed_line = reread_rows[0] if reread_rows else line
            refreshed_by_id[line_id] = refreshed_line
            remaining_null_fields = self._move_line_null_amount_fields(refreshed_line)
            if remaining_null_fields:
                messages.append(
                    f"AML {line_id}: self-heal amount_currency dicoba, field null tersisa: {', '.join(remaining_null_fields)}."
                )
            else:
                messages.append(f"AML {line_id}: self-heal amount_currency berhasil.")
        refreshed_lines = [
            refreshed_by_id.get(int(line.get("id") or 0), line)
            for line in lines
        ]
        return refreshed_lines, messages

    def _partial_reconcile_currency_guard(
        self,
        *,
        debit_line: dict[str, Any],
        credit_line: dict[str, Any],
    ) -> str:
        debit_currency_id = self._many2one_id(debit_line.get("currency_id"))
        credit_currency_id = self._many2one_id(credit_line.get("currency_id"))
        if debit_currency_id <= 0 or debit_currency_id != credit_currency_id:
            return ""
        debit_amount_currency = self._move_line_open_amount_currency_value(debit_line)
        credit_amount_currency = self._move_line_open_amount_currency_value(credit_line)
        if debit_amount_currency is None or credit_amount_currency is None:
            return "target/new AML memiliki amount_currency/residual_currency null di server."
        if (
            debit_amount_currency is _FIELD_NOT_PRESENT and credit_amount_currency is not _FIELD_NOT_PRESENT
        ) or (
            credit_amount_currency is _FIELD_NOT_PRESENT and debit_amount_currency is not _FIELD_NOT_PRESENT
        ):
            return "target/new AML memiliki amount_currency/residual_currency tidak konsisten di server."
        return ""

    async def _create_partial_reconcile(
        self,
        *,
        debit_line: dict[str, Any],
        credit_line: dict[str, Any],
        partial_meta: dict[str, Any],
        context: dict[str, Any],
    ) -> int:
        amount = self._partial_reconcile_amount_value(
            debit_line=debit_line,
            credit_line=credit_line,
        )
        if amount <= 0.0:
            raise RepairOperationError("reconcile_invalid", "Nominal reconcile tidak valid.")
        values: dict[str, Any] = {
            "debit_move_id": int(debit_line.get("id") or 0),
            "credit_move_id": int(credit_line.get("id") or 0),
            "amount": round(float(amount or 0.0), 2),
        }
        debit_currency_id = self._many2one_id(debit_line.get("currency_id"))
        credit_currency_id = self._many2one_id(credit_line.get("currency_id"))
        if debit_currency_id > 0 and debit_currency_id == credit_currency_id:
            debit_amount_currency = self._move_line_open_amount_currency_value(debit_line)
            credit_amount_currency = self._move_line_open_amount_currency_value(credit_line)
            if isinstance(debit_amount_currency, float) and isinstance(credit_amount_currency, float):
                self._apply_supported_value(values, partial_meta, ("currency_id",), debit_currency_id)
                amount_currency = round(min(abs(debit_amount_currency), abs(credit_amount_currency)), 2)
                self._apply_supported_value(
                    values,
                    partial_meta,
                    ("amount_currency",),
                    amount_currency,
                    allow_zero=True,
                )
        return int(
            await self.rpc.create(
                "account.partial.reconcile",
                values,
                context=context,
                stage="SVL_DASH_PCB_CASE1_RECONCILE_CREATE",
            )
            or 0
        )

    @classmethod
    def _describe_null_amount_lines(cls, lines: list[dict[str, Any]]) -> list[str]:
        descriptions: list[str] = []
        for line in lines:
            line_id = int(line.get("id") or 0)
            null_fields = cls._move_line_null_amount_fields(line)
            if line_id > 0 and null_fields:
                descriptions.append(f"AML {line_id}: {', '.join(null_fields)}")
        return descriptions

    async def _attempt_exact_pcb_case1_reconcile(
        self,
        *,
        row: SvlDashboardPcbCase1RepairRow,
        new_lines: list[dict[str, Any]],
        debit_account: ResolvedAccount,
        credit_account: ResolvedAccount,
        context: dict[str, Any],
    ) -> tuple[bool, bool, str, str]:
        partial_meta = await self._fields_get_cached("account.partial.reconcile")
        if not partial_meta:
            return False, True, "Schema account.partial.reconcile tidak tersedia.", "schema_missing"

        result_messages: list[str] = []
        performed_any = False
        fully_reconciled = True
        warning_kind = ""
        line_specs = [
            (debit_account, row.suspend_target_aml_ids if debit_account.code == "2103006" else row.clearing_target_aml_ids),
            (credit_account, row.suspend_target_aml_ids if credit_account.code == "2103006" else row.clearing_target_aml_ids),
        ]
        line_by_account_id = {
            self._many2one_id(line.get("account_id")): line
            for line in new_lines
        }

        for account, target_ids in line_specs:
            if account.code not in {"2103006", "1108099"}:
                continue
            new_line = line_by_account_id.get(account.account_id)
            if new_line is None:
                fully_reconciled = False
                result_messages.append(f"{account.code}: line JE baru tidak ditemukan.")
                continue
            account_messages: list[str] = []
            target_rows = await self._read_target_move_line_rows(target_ids=target_ids, context=context)
            runtime_move_ids = self._pcb_case1_runtime_target_move_ids(row, account_code=account.code)
            if runtime_move_ids:
                runtime_rows = await self._read_runtime_target_move_line_rows(
                    move_ids=runtime_move_ids,
                    account_id=account.account_id,
                    context=context,
                )
                target_rows = self._merge_move_line_rows(target_rows, runtime_rows)

            if target_rows:
                target_rows, heal_messages = await self._heal_move_line_amount_currency(
                    lines=target_rows,
                    context=context,
                )
                if heal_messages:
                    account_messages.extend(heal_messages)
            used_target_ids: list[int] = []
            used_modes: list[str] = []
            while self._move_line_has_open_balance(new_line):
                target_line, exact_reason = self._pick_exact_reconcile_target(
                    row=row,
                    account_code=account.code,
                    new_line=new_line,
                    target_lines=target_rows,
                )
                reconcile_mode = "exact"
                if target_line is None:
                    if "ambigu" in normalize_text(exact_reason).lower():
                        warning_kind = warning_kind or "reconcile_target_ambiguous"
                        account_messages.append(exact_reason)
                        break
                    target_line, partial_reason = self._pick_partial_reconcile_target(
                        row=row,
                        account_code=account.code,
                        new_line=new_line,
                        target_lines=target_rows,
                    )
                    reconcile_mode = "partial"
                    if target_line is None:
                        null_amount_descriptions = self._describe_null_amount_lines(target_rows)
                        if null_amount_descriptions:
                            warning_kind = warning_kind or "amount_currency_null"
                            account_messages.append(
                                f"candidate target AML masih punya field null: {'; '.join(null_amount_descriptions)}."
                            )
                        if not target_rows and runtime_move_ids:
                            partial_reason = "runtime lookup tidak menemukan target AML."
                        elif not target_rows:
                            partial_reason = "target AML tidak ditemukan."
                        if normalize_text(exact_reason):
                            account_messages.append(exact_reason)
                        if normalize_text(partial_reason) and normalize_text(partial_reason) != normalize_text(exact_reason):
                            account_messages.append(partial_reason)
                        break
                debit_line, credit_line, pair_reason = self._build_reconcile_line_pair(
                    new_line=new_line,
                    target_line=target_line,
                )
                if pair_reason:
                    warning_kind = warning_kind or "reconcile_invalid"
                    account_messages.append(pair_reason)
                    break
                currency_guard_error = self._partial_reconcile_currency_guard(
                    debit_line=debit_line,
                    credit_line=credit_line,
                )
                if currency_guard_error:
                    refreshed_rows, line_heal_messages = await self._heal_move_line_amount_currency(
                        lines=self._merge_move_line_rows([new_line], [target_line]),
                        context=context,
                    )
                    if line_heal_messages:
                        account_messages.extend(line_heal_messages)
                        refreshed_by_id = {
                            int(line.get("id") or 0): line
                            for line in refreshed_rows
                            if int(line.get("id") or 0) > 0
                        }
                        new_line = refreshed_by_id.get(int(new_line.get("id") or 0), new_line)
                        target_line = refreshed_by_id.get(int(target_line.get("id") or 0), target_line)
                        target_rows = self._merge_move_line_rows(target_rows, list(refreshed_by_id.values()))
                        debit_line, credit_line, pair_reason = self._build_reconcile_line_pair(
                            new_line=new_line,
                            target_line=target_line,
                        )
                        if not pair_reason:
                            currency_guard_error = self._partial_reconcile_currency_guard(
                                debit_line=debit_line,
                                credit_line=credit_line,
                            )
                    if pair_reason:
                        warning_kind = warning_kind or "reconcile_invalid"
                        account_messages.append(pair_reason)
                        break
                if currency_guard_error:
                    warning_kind = warning_kind or "amount_currency_null"
                    account_messages.append(currency_guard_error)
                    break
                reconcile_amount = self._partial_reconcile_amount_value(
                    debit_line=debit_line,
                    credit_line=credit_line,
                )
                try:
                    await self._create_partial_reconcile(
                        debit_line=debit_line,
                        credit_line=credit_line,
                        partial_meta=partial_meta,
                        context=context,
                    )
                except RepairOperationError as exc:
                    warning_kind = warning_kind or normalize_text(exc.error_kind) or "reconcile_failed"
                    account_messages.append(exc.message)
                    break
                except Exception as exc:  # noqa: BLE001
                    warning_kind = warning_kind or "reconcile_create_failed"
                    account_messages.append(str(exc))
                    break
                performed_any = True
                used_modes.append(reconcile_mode)
                target_line_id = int(target_line.get("id") or 0)
                if target_line_id > 0:
                    used_target_ids.append(target_line_id)
                    refreshed_rows = await self._read_target_move_line_rows(
                        target_ids=[int(new_line.get("id") or 0), target_line_id],
                        context=context,
                    )
                    refreshed_by_id = {
                        int(line.get("id") or 0): line
                        for line in refreshed_rows
                        if int(line.get("id") or 0) > 0
                    }
                    new_line = refreshed_by_id.get(int(new_line.get("id") or 0), new_line)
                    target_rows = self._merge_move_line_rows(target_rows, [line for line in refreshed_by_id.values() if int(line.get("id") or 0) != int(new_line.get("id") or 0)])
                account_messages.append(
                    f"{'exact' if reconcile_mode == 'exact' else 'partial'} reconcile ke AML {target_line_id or '-'} amount {reconcile_amount:.2f} berhasil."
                )
            if self._move_line_has_open_balance(new_line):
                fully_reconciled = False
                remaining_open_balance = self._move_line_open_balance_or_balance(new_line)
                remaining_text = (
                    f"sisa open JE {float(remaining_open_balance or 0.0):.2f}."
                    if remaining_open_balance not in {_FIELD_NOT_PRESENT, None}
                    else "sisa open JE tidak dapat dibaca."
                )
                if used_target_ids:
                    warning_kind = warning_kind or "reconcile_partial_remaining"
                    account_messages.append(
                        f"partial reconcile memakai {len(used_target_ids)} target AML ({', '.join(str(line_id) for line_id in used_target_ids)}), {remaining_text}"
                    )
                elif not warning_kind:
                    warning_kind = "reconcile_skipped"
            elif used_target_ids:
                if "partial" in used_modes:
                    account_messages.append(
                        f"partial reconcile penuh via {len(used_target_ids)} target AML ({', '.join(str(line_id) for line_id in used_target_ids)})."
                    )
                else:
                    account_messages.append("exact reconcile berhasil.")
            result_messages.append(f"{account.code}: {' '.join(part for part in account_messages if part)}")

        if performed_any and fully_reconciled:
            return True, False, " | ".join(result_messages), ""
        return (
            True,
            True,
            " | ".join(result_messages) if result_messages else "Tidak ada target open line untuk auto reconcile.",
            warning_kind or "reconcile_skipped",
        )

    async def _execute_pcb_case1_row(
        self,
        row: SvlDashboardPcbCase1RepairRow,
        *,
        posting_mode: str,
    ) -> SvlDashboardPcbCase1RepairRowResult:
        clean_posting_mode = normalize_text(posting_mode).lower() or "draft"
        if clean_posting_mode not in {"draft", "post"}:
            raise RepairOperationError("mode_invalid", f"Mode posting PCB tidak dikenal: {posting_mode}")
        if int(row.company_id or 0) <= 0:
            raise RepairOperationError("company_missing", "Company belum terisi.")

        target_date = self._parse_date(row.date)
        if clean_posting_mode == "post":
            await self._ensure_postable_date(company_id=row.company_id, target_date=target_date)

        debit_account = await self.resolve_account(company_id=row.company_id, code=row.debit_account_code)
        if debit_account is None:
            raise RepairOperationError("account_not_found", f"Akun debit '{row.debit_account_code}' tidak ditemukan.")
        credit_account = await self.resolve_account(company_id=row.company_id, code=row.credit_account_code)
        if credit_account is None:
            raise RepairOperationError("account_not_found", f"Akun kredit '{row.credit_account_code}' tidak ditemukan.")
        diff_account: ResolvedAccount | None = None
        diff_amount = round(abs(float(row.diff_amount or 0.0)), 2)
        if diff_amount >= 0.01:
            diff_account_code = normalize_text(row.diff_account_code).upper()
            if not diff_account_code:
                raise RepairOperationError(
                    "account_missing",
                    "Akun selisih PCB Case 1 belum terisi padahal nominal debit/kredit item berbeda.",
                )
            diff_account = await self.resolve_account(company_id=row.company_id, code=diff_account_code)
            if diff_account is None:
                raise RepairOperationError("account_not_found", f"Akun selisih '{diff_account_code}' tidak ditemukan.")
        journal = await self.resolve_journal(company_id=row.company_id, code=row.journal_code or DEFAULT_JOURNAL_CODE)
        if journal is None:
            raise RepairOperationError("journal_not_found", f"Journal '{row.journal_code or DEFAULT_JOURNAL_CODE}' tidak ditemukan.")

        context = build_company_context(row.company_id)
        move_meta = await self._fields_get_cached("account.move")
        move_line_meta = await self._fields_get_cached("account.move.line")
        partner_id = await self._resolve_pcb_case1_partner_id(row=row, context=context)
        line_commands, line_warnings = self._build_pcb_case1_line_commands(
            row=row,
            debit_account=debit_account,
            credit_account=credit_account,
            diff_account=diff_account,
            partner_id=partner_id,
            move_line_meta=move_line_meta,
        )
        line_warning_text = " ".join(part for part in line_warnings if normalize_text(part))

        existing_move = await self._find_existing_pcb_case1_move(
            row=row,
            journal=journal,
            debit_account=debit_account,
            credit_account=credit_account,
            diff_account=diff_account,
            partner_id=partner_id,
            move_meta=move_meta,
            context=context,
        )
        if existing_move is not None:
            existing_move_id = int(existing_move.get("id") or 0)
            existing_move_name = await self._read_move_name(
                move_id=existing_move_id,
                context=context,
                fallback=normalize_text(existing_move.get("name")) or normalize_text(row.result_move_name),
            )
            existing_posted = normalize_text(existing_move.get("state")).lower() == "posted"
            if clean_posting_mode == "post" and not existing_posted:
                repost_ok, repost_err = await self._repost_moves(journal_ids=[existing_move_id], context=context)
                if not repost_ok:
                    raise RepairOperationError("post_failed", repost_err)
                existing_move_name = await self._read_move_name(
                    move_id=existing_move_id,
                    context=context,
                    fallback=existing_move_name,
                )
                return SvlDashboardPcbCase1RepairRowResult(
                    row_key=row.row_key,
                    status="POSTED",
                    message=f"JE existing draft {existing_move_name} ditemukan di Odoo dan langsung dipost.",
                    move_id=existing_move_id,
                    move_name=existing_move_name,
                    posted=True,
                    existing_move_detected=True,
                )
            return SvlDashboardPcbCase1RepairRowResult(
                row_key=row.row_key,
                status="POSTED" if existing_posted else "CREATED",
                message=(
                    f"JE existing {existing_move_name} ditemukan di Odoo dan tidak dibuat ulang."
                    if existing_posted
                    else f"JE existing draft {existing_move_name} ditemukan di Odoo dan tidak dibuat ulang."
                ),
                move_id=existing_move_id,
                move_name=existing_move_name,
                posted=existing_posted,
                existing_move_detected=True,
            )

        move_values: dict[str, Any] = {
            "company_id": int(row.company_id or 0),
            "journal_id": journal.journal_id,
            "date": target_date.strftime("%Y-%m-%d"),
            "ref": normalize_text(row.reference),
            "line_ids": line_commands,
        }
        if "move_type" in move_meta:
            move_values["move_type"] = "entry"
        self._apply_supported_value(move_values, move_meta, ("partner_id",), partner_id)
        self._apply_supported_value(move_values, move_meta, ("invoice_origin",), normalize_text(row.po_name))
        self._apply_supported_value(move_values, move_meta, ("stock_picking_id", "picking_id"), int(row.picking_id or 0))
        self._apply_supported_value(move_values, move_meta, ("bill_move_id", "invoice_id", "bill_id"), int(row.bill_move_id or 0))

        move_id = await self.rpc.create(
            "account.move",
            move_values,
            context=context,
            stage="SVL_DASH_PCB_CASE1_CREATE_MOVE",
        )
        if int(move_id or 0) <= 0:
            raise RepairOperationError("create_failed", "Gagal membuat account.move PCB.")
        post_create_warnings = await self._ensure_pcb_case1_move_signature(
            move_id=int(move_id or 0),
            row=row,
            debit_account=debit_account,
            credit_account=credit_account,
            diff_account=diff_account,
            partner_id=partner_id,
            line_commands=line_commands,
            context=context,
        )
        line_warning_text = " ".join(
            part
            for part in [line_warning_text, *post_create_warnings]
            if normalize_text(part)
        )
        move_name = await self._read_move_name(move_id=move_id, context=context, fallback=str(move_id))
        result = SvlDashboardPcbCase1RepairRowResult(
            row_key=row.row_key,
            status="CREATED",
            move_id=int(move_id or 0),
            move_name=move_name,
            posted=False,
            message=(
                f"JE {move_name} dibuat. {line_warning_text}"
                if line_warning_text
                else f"JE {move_name} dibuat."
            ),
        )

        if clean_posting_mode == "draft":
            return result

        repost_ok, repost_err = await self._repost_moves(journal_ids=[move_id], context=context)
        if not repost_ok:
            raise RepairOperationError("post_failed", repost_err)
        move_name = await self._read_move_name(move_id=move_id, context=context, fallback=move_name)
        result.status = "POSTED"
        result.posted = True
        result.move_name = move_name
        result.message = (
            f"JE {move_name} dibuat dan dipost. {line_warning_text}"
            if line_warning_text
            else f"JE {move_name} dibuat dan dipost."
        )
        return result

    async def _validate_normal_receipt_recovery(self, *, row, context, allowed_move_id=0):
        evidence = dict(row.case_evidence or {})
        if normalize_text(row.pcb_case).lower() not in {"case5", "case6"}:
            return []
        if evidence.get("flow") != "normal_receipt" or evidence.get("blockers"):
            raise RepairOperationError("receipt_recovery_blocked", "Re-analyze: evidence pemulihan SVL aktual belum lengkap.")
        ids = sorted({int(v) for v in evidence.get("missing_svl_ids", []) if int(v) > 0})
        if not ids or not row.review_confirmed:
            raise RepairOperationError("review_required", "Review SVL, bill, revaluasi/pemakaian wajib dikonfirmasi.")
        fields = ["id", "company_id", "product_id", "stock_move_id", "account_move_id", "quantity", "value"]
        meta = await self._fields_get_cached("stock.valuation.layer")
        if not all(field in meta for field in fields):
            raise RepairOperationError("receipt_recovery_blocked", "Schema SVL tidak cukup untuk validasi pemulihan.")
        layers = await self.rpc.read("stock.valuation.layer", ids, fields=fields, context=context, stage="PCB_RECEIPT_PREFLIGHT")
        if {int(r["id"]) for r in layers} != set(ids) or any(
            self._many2one_id(r.get("company_id")) != row.company_id
            or self._many2one_id(r.get("product_id")) != row.product_id
            or self._many2one_id(r.get("stock_move_id")) not in set(evidence.get("stock_move_ids", []))
            or self._many2one_id(r.get("account_move_id")) not in {0, int(allowed_move_id)}
            or float(r.get("quantity") or 0) <= 0 or float(r.get("value") or 0) <= 0
            for r in layers
        ):
            raise RepairOperationError("receipt_recovery_changed", "SVL sudah berubah/terhubung; re-analyze agar tidak menggandakan jurnal.")
        value = round(sum(float(r["value"]) for r in layers), 2)
        qty = sum(float(r["quantity"]) for r in layers)
        inventory = sum(l.amount * (1 if l.side == "debit" else -1) for l in row.planned_lines if l.account_code == row.inventory_account_code)
        if abs(value-float(evidence.get("missing_value") or 0)) >= .01 or abs(qty-float(evidence.get("missing_qty") or 0)) >= .00001 or abs(inventory-value) >= .01:
            raise RepairOperationError("receipt_recovery_changed", "Nilai/qty pemulihan harus sama dengan SVL aktual.")
        if any(l.account_code == "1108099" for l in row.planned_lines):
            raise RepairOperationError("receipt_recovery_blocked", "Normal receipt tidak memakai clearing.")
        receipt_inventory = [l for l in row.planned_lines if l.role == "receipt_inventory"]
        receipt_counterpart = [l for l in row.planned_lines if l.role == "receipt_counterpart"]
        if len(receipt_inventory) != 1 or len(receipt_counterpart) != 1 or receipt_inventory[0].side != "debit" or receipt_inventory[0].account_code != row.inventory_account_code or abs(receipt_inventory[0].amount-value) >= .01 or receipt_counterpart[0].side != "credit" or abs(receipt_counterpart[0].amount-value) >= .01:
            raise RepairOperationError("receipt_recovery_blocked", "Dua receipt legs harus mempertahankan akun, nilai dan qty SVL aktual.")
        if not row.bill_line_id:
            raise RepairOperationError("receipt_recovery_blocked", "Bill source harus unik dan terverifikasi.")
        bill = await self.rpc.read("account.move.line", [row.bill_line_id], fields=["id", "move_id", "company_id", "product_id", "account_id", "balance", "amount_residual"], context=context, stage="PCB_RECEIPT_BILL_PREFLIGHT")
        if len(bill) != 1 or self._many2one_id(bill[0].get("move_id")) != row.bill_move_id or self._many2one_id(bill[0].get("company_id")) != row.company_id or self._many2one_id(bill[0].get("product_id")) != row.product_id:
            raise RepairOperationError("receipt_recovery_changed", "Bill source tidak lagi cocok; re-analyze.")
        counterpart_code = "2103006" if row.pcb_case == "case5" else evidence.get("bill_expense_account_code")
        if receipt_counterpart[0].account_code != counterpart_code:
            raise RepairOperationError("receipt_recovery_blocked", "Receipt counterpart harus sesuai bill aktual.")
        counterpart = await self.resolve_account(company_id=row.company_id, code=counterpart_code)
        if counterpart is None or counterpart.account_id != self._many2one_id(bill[0].get("account_id")):
            raise RepairOperationError("receipt_recovery_changed", "Counterpart tidak sesuai akun bill aktual.")
        if row.pcb_case == "case5":
            suspense_credit = -sum(l.amount * (1 if l.side == "debit" else -1) for l in row.planned_lines if l.account_code == "2103006")
            if abs(suspense_credit-float(bill[0].get("balance") or 0)) >= .01 or (not allowed_move_id and abs(suspense_credit-float(bill[0].get("amount_residual") or 0)) >= .01):
                raise RepairOperationError("receipt_recovery_changed", "Suspense bill sudah dikoreksi/direkonsiliasi atau nominal berubah; re-analyze.")
        return ids

    async def _link_normal_receipt_layers(self, *, row, ids, move_id, context):
        if not ids:
            return
        # Link only after posting. Do not create a valuation layer or change qty/value.
        await self._validate_normal_receipt_recovery(row=row, context=context, allowed_move_id=move_id)
        linked = await self.rpc.write("stock.valuation.layer", ids, {"account_move_id": int(move_id)}, context=context, stage="PCB_RECEIPT_RELINK")
        readback = await self.rpc.read("stock.valuation.layer", ids, fields=["id", "account_move_id"], context=context, stage="PCB_RECEIPT_RELINK_VERIFY")
        if not linked or {int(r["id"]) for r in readback} != set(ids) or any(self._many2one_id(r.get("account_move_id")) != move_id for r in readback):
            raise RepairOperationError("receipt_relink_failed", f"JE {move_id} sudah posted; link SVL belum terverifikasi. Review existing JE sebelum retry.")

    async def _execute_pcb_case2_row(
        self,
        row: SvlDashboardPcbCase2RepairRow,
        *,
        posting_mode: str,
    ) -> SvlDashboardPcbCase2RepairRowResult:
        clean_posting_mode = normalize_text(posting_mode).lower() or "draft"
        if clean_posting_mode not in {"draft", "post"}:
            raise RepairOperationError("mode_invalid", f"Mode posting PCB tidak dikenal: {posting_mode}")
        if int(row.company_id or 0) <= 0:
            raise RepairOperationError("company_missing", "Company belum terisi.")

        target_date = self._parse_date(row.date)
        if clean_posting_mode == "post":
            await self._ensure_postable_date(company_id=row.company_id, target_date=target_date)

        normalized_row = self._build_pcb_case2_row_from_mapping(
            {
                field_name: getattr(row, field_name)
                for field_name in SvlDashboardPcbCase2RepairRow.__dataclass_fields__
            }
        )
        if normalized_row.review_required and not normalized_row.review_confirmed:
            raise RepairOperationError(
                "review_required",
                normalize_text(normalized_row.review_reason)
                or "Row PCB multi-line ini masih butuh review manual sebelum execute.",
            )
        if not normalized_row.planned_lines:
            raise RepairOperationError("zero_line", "Planned line PCB Case 2 kosong, tidak ada yang perlu dikoreksi.")

        context = build_company_context(row.company_id)
        move_meta = await self._fields_get_cached("account.move")
        move_line_meta = await self._fields_get_cached("account.move.line")
        partner_id = await self._resolve_pcb_case2_partner_id(row=normalized_row, context=context)
        journal = await self.resolve_journal(company_id=row.company_id, code=row.journal_code or DEFAULT_JOURNAL_CODE)
        if journal is None:
            raise RepairOperationError("journal_not_found", f"Journal '{row.journal_code or DEFAULT_JOURNAL_CODE}' tidak ditemukan.")

        simulation_lines = self._build_pcb_case2_simulation_lines(row=normalized_row)
        planned_accounts: list[tuple[SvlDashboardPcbRepairPlannedLine, ResolvedAccount]] = []
        resolved_simulation_lines: list[tuple[dict[str, Any], ResolvedAccount]] = []
        for simulation_line in simulation_lines:
            planned_line_index = int(simulation_line.get("planned_line_index") or 0)
            if planned_line_index < 0 or planned_line_index >= len(normalized_row.planned_lines):
                raise RepairOperationError(
                    "payload_mismatch",
                    "Jurnal simulasi PCB Case 2 tidak sinkron dengan planned line sumbernya.",
                )
            planned_line = normalized_row.planned_lines[planned_line_index]
            amount = abs(round(float(simulation_line.get("amount") or 0.0), 2))
            side = normalize_text(simulation_line.get("side")).lower()
            if amount <= 0.0 or side not in {"debit", "credit"}:
                continue
            account_code = normalize_text(simulation_line.get("account_code")).upper()
            if not account_code:
                role_label = self._pcb_case2_role_label(planned_line.role)
                raise RepairOperationError(
                    "account_missing",
                    f"Akun {role_label} PCB Case 2 belum terisi untuk nominal {amount:,.2f}.",
                )
            account = await self.resolve_account(company_id=row.company_id, code=account_code)
            if account is None:
                raise RepairOperationError("account_not_found", f"Akun '{account_code}' tidak ditemukan.")
            planned_accounts.append((planned_line, account))
            resolved_simulation_lines.append((simulation_line, account))
        if not planned_accounts:
            raise RepairOperationError("zero_line", "Planned line PCB Case 2 kosong setelah filtering nominal nol.")

        line_commands = self._build_pcb_case2_line_commands(
            row=normalized_row,
            resolved_simulation_lines=resolved_simulation_lines,
            partner_id=partner_id,
            move_line_meta=move_line_meta,
        )
        existing_move = await self._find_existing_pcb_case2_move(
            row=normalized_row,
            journal=journal,
            planned_accounts=planned_accounts,
            partner_id=partner_id,
            move_meta=move_meta,
            context=context,
        )
        recovery_ids = await self._validate_normal_receipt_recovery(
            row=normalized_row, context=context, allowed_move_id=int((existing_move or {}).get("id") or 0),
        )
        if existing_move is not None:
            existing_move_id = int(existing_move.get("id") or 0)
            existing_move_name = await self._read_move_name(
                move_id=existing_move_id,
                context=context,
                fallback=normalize_text(existing_move.get("name")) or normalize_text(row.result_move_name),
            )
            existing_posted = normalize_text(existing_move.get("state")).lower() == "posted"
            if clean_posting_mode == "post" and not existing_posted:
                repost_ok, repost_err = await self._repost_moves(journal_ids=[existing_move_id], context=context)
                if not repost_ok:
                    raise RepairOperationError("post_failed", repost_err)
                existing_move_name = await self._read_move_name(
                    move_id=existing_move_id,
                    context=context,
                    fallback=existing_move_name,
                )
                await self._link_normal_receipt_layers(row=normalized_row, ids=recovery_ids, move_id=existing_move_id, context=context)
                return SvlDashboardPcbCase2RepairRowResult(
                    row_key=row.row_key,
                    status="POSTED",
                    message=f"JE existing draft {existing_move_name} ditemukan di Odoo dan langsung dipost.",
                    move_id=existing_move_id,
                    move_name=existing_move_name,
                    posted=True,
                    existing_move_detected=True,
                )
            if existing_posted:
                await self._link_normal_receipt_layers(row=normalized_row, ids=recovery_ids, move_id=existing_move_id, context=context)
            return SvlDashboardPcbCase2RepairRowResult(
                row_key=row.row_key,
                status="POSTED" if existing_posted else "CREATED",
                message=(
                    f"JE existing {existing_move_name} ditemukan di Odoo dan tidak dibuat ulang."
                    if existing_posted
                    else f"JE existing draft {existing_move_name} ditemukan di Odoo dan tidak dibuat ulang."
                ),
                move_id=existing_move_id,
                move_name=existing_move_name,
                posted=existing_posted,
                existing_move_detected=True,
            )

        move_values: dict[str, Any] = {
            "company_id": int(row.company_id or 0),
            "journal_id": journal.journal_id,
            "date": target_date.strftime("%Y-%m-%d"),
            "ref": normalize_text(row.reference),
            "line_ids": line_commands,
        }
        if "move_type" in move_meta:
            move_values["move_type"] = "entry"
        self._apply_supported_value(move_values, move_meta, ("partner_id",), partner_id)
        self._apply_supported_value(move_values, move_meta, ("invoice_origin",), normalize_text(row.po_name))
        self._apply_supported_value(move_values, move_meta, ("stock_picking_id", "picking_id"), int(row.picking_id or 0))
        self._apply_supported_value(move_values, move_meta, ("bill_move_id", "invoice_id", "bill_id"), int(row.bill_move_id or 0))
        if recovery_ids and len(normalized_row.case_evidence.get("stock_move_ids", [])) == 1:
            self._apply_supported_value(move_values, move_meta, ("stock_move_id",), normalized_row.case_evidence["stock_move_ids"][0])

        move_id = await self.rpc.create(
            "account.move",
            move_values,
            context=context,
            stage="SVL_DASH_PCB_CASE2_CREATE_MOVE",
        )
        if int(move_id or 0) <= 0:
            raise RepairOperationError("create_failed", "Gagal membuat account.move PCB Case 2.")
        move_name = await self._read_move_name(move_id=move_id, context=context, fallback=str(move_id))
        result = SvlDashboardPcbCase2RepairRowResult(
            row_key=row.row_key,
            status="CREATED",
            move_id=int(move_id or 0),
            move_name=move_name,
            posted=False,
            message=f"JE {move_name} dibuat.",
        )

        if clean_posting_mode == "draft":
            return result

        repost_ok, repost_err = await self._repost_moves(journal_ids=[move_id], context=context)
        if not repost_ok:
            raise RepairOperationError("post_failed", repost_err)
        move_name = await self._read_move_name(move_id=move_id, context=context, fallback=move_name)
        result.status = "POSTED"
        result.posted = True
        result.move_name = move_name
        result.message = f"JE {move_name} dibuat dan dipost."
        await self._link_normal_receipt_layers(row=normalized_row, ids=recovery_ids, move_id=int(move_id), context=context)

        # Case 9: buat SVL koreksi negatif (value-only, qty=0) agar stock.valuation.layer
        # konsisten dengan Balance Sheet setelah persediaan di-clear via repair JE.
        if normalize_text(row.pcb_case).lower() == "case9":
            svl_warn = await self._create_case9_svl_correction(
                row=row,
                move_id=int(move_id or 0),
                move_name=move_name,
                context=context,
            )
            if svl_warn:
                result.message += f" {svl_warn}"

        return result

    async def _create_case9_svl_correction(
        self,
        *,
        row: SvlDashboardPcbCase2RepairRow,
        move_id: int,
        move_name: str,
        context: dict[str, Any],
    ) -> str:
        """Buat SVL koreksi value-only (qty=0) untuk Case 9 setelah JE di-post.

        Tujuan: menjaga konsistensi antara stock.valuation.layer total dan
        Balance Sheet akun persediaan setelah repair JE meng-clear saldo persediaan.

        Returns pesan warning jika SVL gagal dibuat (non-fatal), string kosong jika sukses.
        """
        inventory_amount = abs(round(float(row.inventory_balance or 0.0), 2))
        if inventory_amount < 0.01:
            return ""
        product_id = int(row.product_id or 0)
        company_id = int(row.company_id or 0)
        if product_id <= 0 or company_id <= 0:
            return "[SVL koreksi dilewati: product_id/company_id tidak tersedia]"

        # Nilai SVL koreksi = negatif dari inventory_balance yang di-clear
        svl_value = -inventory_amount

        # Ambil svl_ids dari case_evidence untuk referensi description
        case_evidence = dict(row.case_evidence or {})
        svl_ids = list(case_evidence.get("svl_ids") or [])
        origin_ref = normalize_text(row.picking_name) or normalize_text(row.po_name) or ""
        description = f"Case 9 UoM Scale Correction - {origin_ref}" if origin_ref else "Case 9 UoM Scale Correction"

        svl_values: dict[str, Any] = {
            "company_id": company_id,
            "product_id": product_id,
            "quantity": 0.0,
            "unit_cost": 0.0,
            "value": svl_value,
            "description": description,
        }
        # Link ke account.move repair jika field tersedia
        svl_meta = await self._fields_get_cached("stock.valuation.layer")
        self._apply_supported_value(svl_values, svl_meta, ("account_move_id",), move_id)
        # Link ke SVL asal jika ada (untuk audit trail)
        if svl_ids and len(svl_ids) == 1:
            self._apply_supported_value(svl_values, svl_meta, ("stock_valuation_layer_id",), int(svl_ids[0]))

        try:
            svl_id = await self.rpc.create(
                "stock.valuation.layer",
                svl_values,
                context=context,
                stage="SVL_DASH_PCB_CASE9_CREATE_SVL",
            )
            if int(svl_id or 0) <= 0:
                return f"[SVL koreksi gagal dibuat setelah {move_name}]"
            self.logger.info(
                "Case 9 SVL correction created: svl_id=%s value=%s product_id=%s move=%s",
                svl_id, svl_value, product_id, move_name,
            )
            return ""
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("Case 9 SVL correction failed: %s", exc)
            return f"[SVL koreksi gagal: {exc}]"

    async def execute_pcb_repair_row(self, row: dict[str, Any], *, post: bool = False) -> dict[str, Any]:
        posting_mode = "post" if post else "draft"
        if normalize_text(row.get("pcb_case")).lower() in {"case2", "case3", "case4", "case5", "case6", "case8a", "case8b", "case9"}:
            pcb_row = self._build_pcb_case2_row_from_mapping(row)
            result = await self._execute_pcb_case2_row_safe(pcb_row, posting_mode=posting_mode)
            return {
                "row_key": result.row_key,
                "status": result.status,
                "message": result.message,
                "move_id": result.move_id,
                "move_name": result.move_name,
                "posted": result.posted,
                "existing_move_detected": result.existing_move_detected,
                "error_kind": result.error_kind,
            }
        pcb_row = self._build_pcb_case1_row_from_mapping(row)
        result = await self._execute_pcb_case1_row_safe(pcb_row, posting_mode=posting_mode)
        return {
            "row_key": result.row_key,
            "status": result.status,
            "message": result.message,
            "move_id": result.move_id,
            "move_name": result.move_name,
            "posted": result.posted,
            "existing_move_detected": result.existing_move_detected,
            "reconcile_attempted": result.reconcile_attempted,
            "reconcile_performed": result.reconcile_performed,
            "reconcile_skipped": result.reconcile_skipped,
            "reconcile_message": result.reconcile_message,
            "reconcile_error_kind": result.reconcile_error_kind,
            "error_kind": result.error_kind,
        }

        """Create a correction JE (account.move) for one PCB Case 1 repair seed.

        The JE offsets the remaining balance between accounts 2103006 (Hutang Suspend)
        and 1108099 (Clearing) as recorded in the cycle's account_rows.
        """
        await self.rpc.ensure_login()
        company_id = int(row.get("company_id") or 0)
        context = build_company_context(company_id)

        debit_account = await self.resolve_account(company_id=company_id, code=row.get("debit_account_code", ""))
        if debit_account is None:
            return {"status": "ERROR", "message": f"Akun debit '{row.get('debit_account_code')}' tidak ditemukan."}

        credit_account = await self.resolve_account(company_id=company_id, code=row.get("credit_account_code", ""))
        if credit_account is None:
            return {"status": "ERROR", "message": f"Akun kredit '{row.get('credit_account_code')}' tidak ditemukan."}

        journal = await self.resolve_journal(company_id=company_id, code=row.get("journal_code") or DEFAULT_JOURNAL_CODE)
        if journal is None:
            return {"status": "ERROR", "message": "Journal tidak ditemukan."}

        partner_id = await self._resolve_partner_id(company_id, row.get("partner_name", ""))
        amount = abs(float(row.get("amount") or 0.0))
        if amount <= 0:
            return {"status": "ERROR", "message": "Nominal 0 — tidak ada yang perlu dikoreksi."}

        line_label = normalize_text(row.get("line_label")) or self._pcb_case1_fallback_line_label(row)

        def _build_line(acct_id: int, debit: float, credit: float) -> list[Any]:
            ln: dict[str, Any] = {
                "name": line_label,
                "account_id": acct_id,
                "debit": debit,
                "credit": credit,
            }
            if partner_id > 0:
                ln["partner_id"] = partner_id
            return [0, 0, ln]

        move_values: dict[str, Any] = {
            "company_id": company_id,
            "journal_id": journal.journal_id,
            "date": normalize_text(row.get("date")) or date.today().strftime("%Y-%m-%d"),
            "ref": normalize_text(row.get("reference")) or "",
            "move_type": "entry",
            "line_ids": [
                _build_line(debit_account.account_id, amount, 0.0),
                _build_line(credit_account.account_id, 0.0, amount),
            ],
        }
        if partner_id > 0:
            move_values["partner_id"] = partner_id

        move_id: int = await self.rpc.create("account.move", move_values, context=context)
        result: dict[str, Any] = {
            "status": "CREATED",
            "move_id": move_id,
            "posted": False,
            "message": f"JE #{move_id} dibuat.",
        }

        if post:
            try:
                await self.rpc.call("account.move", "action_post", [[move_id]], context=context)
                result["posted"] = True
                result["status"] = "POSTED"
                result["message"] = f"JE #{move_id} dibuat dan di-post."
            except Exception as exc:  # noqa: BLE001
                result["post_error"] = str(exc)
                result["message"] = f"JE #{move_id} dibuat tapi gagal di-post: {exc}"

        return result
