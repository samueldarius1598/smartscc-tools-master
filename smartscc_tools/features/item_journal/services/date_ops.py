"""Canonical date sync and lock-date services for Item Journal."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime
import logging
from typing import Any, Dict, Iterable, List

from smartscc_tools.features.item_journal.config import RuntimeSettings
from smartscc_tools.services.odoo.gateway import AsyncOdooJsonRpcClient, build_company_context, extract_many2one_id
from smartscc_tools.features.item_journal.runtime import get_technical_logger
from smartscc_tools.features.item_journal.utils import (
    append_error,
    excel_value_to_date,
    local_date_from_utc_text,
    normalize_text,
    parse_iso_date,
)
from smartscc_tools.features.item_journal.workbook import ITEM_JOURNAL_SHEET_NAME, ItemJournalRow, ItemJournalWorkbookRepo


@dataclass
class DateFieldCapability:
    field_name: str
    is_date_only: bool


@dataclass
class DateSyncCapabilities:
    stock_picking_done: DateFieldCapability
    stock_picking_scheduled: DateFieldCapability
    stock_move: DateFieldCapability
    stock_move_line: DateFieldCapability
    stock_valuation_layer: DateFieldCapability | None
    account_move_due_primary: DateFieldCapability | None
    account_move_due_fallback: DateFieldCapability | None


@dataclass
class LockDateFetchResult:
    lock_date_map: Dict[int, date | None]
    status_map: Dict[int, str]


def _variant_to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = normalize_text(value).lower()
    return text in {"1", "true", "yes", "y", "on"}


def _build_force_date_context(company_id: int, date_done_utc: str, settings: RuntimeSettings) -> Dict[str, Any]:
    ctx = build_company_context(company_id)
    if settings.use_force_date_context and normalize_text(date_done_utc):
        local_day = local_date_from_utc_text(
            utc_text=date_done_utc,
            local_tz_offset_hours=settings.local_tz_offset,
            use_local_as_utc=settings.use_local_time_as_utc,
        )
        if local_day:
            ctx["force_date"] = date_done_utc
            ctx["force_period_date"] = local_day.strftime("%Y-%m-%d")
    ctx["check_move_validity"] = False
    return ctx


def _build_move_line_context(company_id: int) -> Dict[str, Any]:
    ctx = build_company_context(company_id)
    ctx["check_move_validity"] = False
    return ctx


def _normalize_datetime_compare(text: str) -> str:
    value = normalize_text(text).replace("T", " ")
    if "." in value:
        value = value.split(".", 1)[0]
    if len(value) >= 19:
        value = value[:19]
    return value


def _date_field_matches_expected(
    actual_value: str,
    expected_utc: str,
    is_date_only: bool,
    settings: RuntimeSettings,
) -> bool:
    actual_text = normalize_text(actual_value)
    expected_text = normalize_text(expected_utc)
    if not actual_text or not expected_text:
        return False

    if not is_date_only:
        return _normalize_datetime_compare(actual_text) == _normalize_datetime_compare(expected_text)

    actual_date = local_date_from_utc_text(
        utc_text=actual_text,
        local_tz_offset_hours=settings.local_tz_offset,
        use_local_as_utc=settings.use_local_time_as_utc,
    )
    if actual_date is None and len(actual_text) >= 10:
        try:
            actual_date = datetime.strptime(actual_text[:10], "%Y-%m-%d").date()
        except ValueError:
            return False

    expected_date = local_date_from_utc_text(
        utc_text=expected_text,
        local_tz_offset_hours=settings.local_tz_offset,
        use_local_as_utc=settings.use_local_time_as_utc,
    )
    if actual_date is None or expected_date is None:
        return False
    return actual_date == expected_date


def _build_write_value(date_done_utc: str, is_date_only: bool, settings: RuntimeSettings) -> str:
    if not is_date_only:
        return date_done_utc
    local_day = local_date_from_utc_text(
        utc_text=date_done_utc,
        local_tz_offset_hours=settings.local_tz_offset,
        use_local_as_utc=settings.use_local_time_as_utc,
    )
    if local_day is None:
        return date_done_utc[:10]
    return local_day.strftime("%Y-%m-%d")


async def read_picking_name(
    rpc: AsyncOdooJsonRpcClient,
    pick_id: int,
    context: Dict[str, Any],
) -> str:
    rows = await rpc.read(
        model="stock.picking",
        ids=[pick_id],
        fields=["name"],
        context=context,
        stage="READ_PICKING_NAME",
    )
    if not rows:
        return ""
    return normalize_text(rows[0].get("name"))


def collect_active_company_ids(rows: Iterable[ItemJournalRow]) -> tuple[List[int], str]:
    company_ids: Dict[int, bool] = {}
    for row in rows:
        if not row.is_active():
            continue
        if row.company_id <= 0:
            return [], f"company_id kolom B wajib angka > 0 pada baris {row.row_number}."
        company_ids[row.company_id] = True

    sorted_ids = sorted(company_ids.keys())
    if not sorted_ids:
        return [], f"Tidak ada company aktif pada sheet '{ITEM_JOURNAL_SHEET_NAME}'."
    return sorted_ids, ""


class LockDateServiceAsync:
    def __init__(self, rpc: AsyncOdooJsonRpcClient, logger: logging.Logger) -> None:
        self.rpc = rpc
        self.logger = logger

    async def get_close_acc_date_by_company_id(self, company_id: int) -> tuple[date | None, str]:
        if company_id <= 0:
            return None, "company_id tidak valid."

        context = build_company_context(company_id)
        # A transient lock wizard can belong to another company or be unapplied.
        # Read only the authoritative company, never the globally latest wizard.
        try:
            meta = await self.rpc.fields_get(
                "res.company", attributes=["type"], context=context, stage="CHECK_CLOSE_ACC_DATE",
            )
            fields = ["fiscalyear_lock_date"] + [
                field for field in ("user_fiscalyear_lock_date", "hard_lock_date", "user_hard_lock_date")
                if field in meta
            ]
            rows = await self.rpc.read(
                model="res.company",
                ids=[company_id],
                fields=["id", *fields],
                context=context,
                stage="CHECK_CLOSE_ACC_DATE",
            )
        except Exception as exc:  # noqa: BLE001
            return None, str(exc)
        if not rows or int(rows[0].get("id") or 0) != company_id:
            return None, f"res.company {company_id} tidak ditemukan saat membaca lock date."
        lock_dates: list[date] = []
        for field in fields:
            text = normalize_text(rows[0].get(field))
            if not text:
                continue
            try:
                # Odoo may render its no-lock sentinel as "1-01-01".
                lock_date = date(*map(int, text.split("-")))
            except (ValueError, TypeError):
                return None, f"Format {field} tidak valid: '{text}'."
            if lock_date > date.min:
                lock_dates.append(lock_date)
        return max(lock_dates, default=None), ""

    async def fetch_lock_dates(self, company_ids: Iterable[int]) -> LockDateFetchResult:
        lock_date_map: Dict[int, date | None] = {}
        status_map: Dict[int, str] = {}
        for company_id in sorted(set(company_ids)):
            lock_date, err = await self.get_close_acc_date_by_company_id(company_id)
            lock_date_map[company_id] = lock_date
            if lock_date:
                status_map[company_id] = lock_date.strftime("%Y-%m-%d")
            elif err:
                status_map[company_id] = f"ERROR: {err}"
            else:
                status_map[company_id] = "(kosong)"
        return LockDateFetchResult(lock_date_map=lock_date_map, status_map=status_map)

    async def check_close_acc_date(self, repo: ItemJournalWorkbookRepo) -> date | None:
        rows = repo.read_rows()
        company_ids, err = collect_active_company_ids(rows)
        if err:
            raise RuntimeError(err)
        if len(company_ids) > 1:
            company_list = ", ".join(str(item) for item in company_ids)
            raise RuntimeError(
                f"Company aktif pada sheet '{ITEM_JOURNAL_SHEET_NAME}' kolom B harus 1 unik. Ditemukan: {company_list}."
            )

        company_id = company_ids[0]
        lock_date, fetch_err = await self.get_close_acc_date_by_company_id(company_id)
        if fetch_err:
            raise RuntimeError(fetch_err)
        if lock_date is None:
            self.logger.warning("fiscalyear_lock_date kosong untuk company_id=%s", company_id)
        else:
            self.logger.info("Lock date company_id=%s = %s", company_id, lock_date.strftime("%Y-%m-%d"))
        return lock_date


class JournalResolverAsync:
    """Resolve related account.move ids linked to a picking with compatibility fallbacks."""

    def __init__(self, rpc: AsyncOdooJsonRpcClient, logger: logging.Logger) -> None:
        self.rpc = rpc
        self.logger = logger
        self._fields_meta_cache: Dict[str, Dict[str, Any]] = {}

    async def resolve_account_move_ids_for_picking(
        self,
        *,
        picking_id: int,
        move_ids: List[int],
        company_id: int,
        picking_name: str = "",
    ) -> tuple[List[int], str]:
        context = build_company_context(company_id)
        sources: List[str] = []
        account_move_ids: set[int] = set()

        pick_ids = await self._collect_from_picking_account_move_ids(
            picking_id=picking_id,
            context=context,
        )
        if pick_ids:
            account_move_ids.update(pick_ids)
            sources.append("picking_account_move_ids")

        move_account_ids = await self._collect_from_move_account_move_ids(
            move_ids=move_ids,
            context=context,
        )
        if move_account_ids:
            account_move_ids.update(move_account_ids)
            sources.append("move_account_move_ids")

        stock_move_link_ids = await self._collect_from_account_move_stock_move_id(
            move_ids=move_ids,
            context=context,
        )
        if stock_move_link_ids:
            account_move_ids.update(stock_move_link_ids)
            sources.append("account_move_stock_move_id")

        svl_account_ids = await self._collect_from_svl_account_move_id(
            move_ids=move_ids,
            context=context,
        )
        if svl_account_ids:
            account_move_ids.update(svl_account_ids)
            sources.append("svl_account_move_id")

        if not account_move_ids:
            clean_picking_name = normalize_text(picking_name)
            if not clean_picking_name:
                clean_picking_name = await self._read_picking_name(picking_id=picking_id, context=context)
            if clean_picking_name:
                legacy_ids = await self.rpc.search(
                    model="account.move",
                    domain=[["ref", "=", clean_picking_name]],
                    context=context,
                    stage="JOURNAL_RESOLVE_LEGACY_REF",
                )
                if legacy_ids:
                    account_move_ids.update(int(item) for item in legacy_ids if int(item) > 0)
                    sources.append("ref")

        resolved_ids = sorted(account_move_ids)
        source = "+".join(sources) if sources else "none"
        self.logger.info(
            "Resolved journal links for picking=%s source=%s count=%s",
            picking_id,
            source,
            len(resolved_ids),
        )
        return resolved_ids, source

    async def _collect_from_picking_account_move_ids(
        self,
        *,
        picking_id: int,
        context: Dict[str, Any],
    ) -> List[int]:
        if picking_id <= 0:
            return []
        if not await self._supports_field(
            model="stock.picking",
            field_name="account_move_ids",
            expected_type="many2many",
            expected_relation="account.move",
            context=context,
            stage="JOURNAL_RESOLVE_PREFLIGHT",
        ):
            return []
        rows = await self.rpc.read(
            model="stock.picking",
            ids=[picking_id],
            fields=["id", "account_move_ids"],
            context=context,
            stage="JOURNAL_RESOLVE_PICKING_ACCOUNT_MOVE",
        )
        if not rows:
            return []
        raw_ids = rows[0].get("account_move_ids")
        if not isinstance(raw_ids, list):
            return []
        return sorted({int(item) for item in raw_ids if int(item) > 0})

    async def _collect_from_move_account_move_ids(
        self,
        *,
        move_ids: List[int],
        context: Dict[str, Any],
    ) -> List[int]:
        clean_move_ids = sorted({int(item) for item in move_ids if int(item) > 0})
        if not clean_move_ids:
            return []
        if not await self._supports_field(
            model="stock.move",
            field_name="account_move_ids",
            expected_type="many2many",
            expected_relation="account.move",
            context=context,
            stage="JOURNAL_RESOLVE_PREFLIGHT",
        ):
            return []
        rows = await self.rpc.read(
            model="stock.move",
            ids=clean_move_ids,
            fields=["id", "account_move_ids"],
            context=context,
            stage="JOURNAL_RESOLVE_MOVE_ACCOUNT_MOVE",
        )
        account_move_ids: set[int] = set()
        for row in rows:
            raw_ids = row.get("account_move_ids")
            if not isinstance(raw_ids, list):
                continue
            for item in raw_ids:
                item_id = int(item or 0)
                if item_id > 0:
                    account_move_ids.add(item_id)
        return sorted(account_move_ids)

    async def _collect_from_account_move_stock_move_id(
        self,
        *,
        move_ids: List[int],
        context: Dict[str, Any],
    ) -> List[int]:
        clean_move_ids = sorted({int(item) for item in move_ids if int(item) > 0})
        if not clean_move_ids:
            return []
        if not await self._supports_field(
            model="account.move",
            field_name="stock_move_id",
            expected_type="many2one",
            expected_relation="stock.move",
            context=context,
            stage="JOURNAL_RESOLVE_PREFLIGHT",
        ):
            return []
        rows = await self.rpc.search_read(
            model="account.move",
            domain=[["stock_move_id", "in", clean_move_ids]],
            fields=["id"],
            context=context,
            stage="JOURNAL_RESOLVE_ACCOUNT_MOVE_STOCK_MOVE",
        )
        return sorted({int(row.get("id") or 0) for row in rows if int(row.get("id") or 0) > 0})

    async def _collect_from_svl_account_move_id(
        self,
        *,
        move_ids: List[int],
        context: Dict[str, Any],
    ) -> List[int]:
        clean_move_ids = sorted({int(item) for item in move_ids if int(item) > 0})
        if not clean_move_ids:
            return []
        if not await self._supports_field(
            model="stock.valuation.layer",
            field_name="account_move_id",
            expected_type="many2one",
            expected_relation="account.move",
            context=context,
            stage="JOURNAL_RESOLVE_PREFLIGHT",
        ):
            return []
        rows = await self.rpc.search_read(
            model="stock.valuation.layer",
            domain=[["stock_move_id", "in", clean_move_ids]],
            fields=["account_move_id"],
            context=context,
            stage="JOURNAL_RESOLVE_SVL_ACCOUNT_MOVE",
        )
        account_move_ids: set[int] = set()
        for row in rows:
            account_move_id = extract_many2one_id(row.get("account_move_id"))
            if account_move_id > 0:
                account_move_ids.add(account_move_id)
        return sorted(account_move_ids)

    async def _supports_field(
        self,
        *,
        model: str,
        field_name: str,
        expected_type: str,
        expected_relation: str,
        context: Dict[str, Any],
        stage: str,
    ) -> bool:
        meta = await self._read_fields_meta(model=model, context=context, stage=stage)
        field_meta = meta.get(field_name, {})
        if not isinstance(field_meta, dict):
            return False
        field_type = normalize_text(field_meta.get("type")).lower()
        relation = normalize_text(field_meta.get("relation")).lower()
        return field_type == expected_type and relation == expected_relation

    async def _read_fields_meta(
        self,
        *,
        model: str,
        context: Dict[str, Any],
        stage: str,
    ) -> Dict[str, Any]:
        if model not in self._fields_meta_cache:
            self._fields_meta_cache[model] = await self.rpc.fields_get(
                model=model,
                attributes=["type", "relation"],
                context=context,
                stage=stage,
            )
        return self._fields_meta_cache[model]

    async def _read_picking_name(
        self,
        *,
        picking_id: int,
        context: Dict[str, Any],
    ) -> str:
        rows = await self.rpc.read(
            model="stock.picking",
            ids=[picking_id],
            fields=["name"],
            context=context,
            stage="JOURNAL_RESOLVE_PICKING_NAME",
        )
        if not rows:
            return ""
        return normalize_text(rows[0].get("name"))


class AccountMoveDateSyncServiceAsync:
    """Sync account.move.date with lock-date and posted-state handling."""

    def __init__(self, rpc: AsyncOdooJsonRpcClient, settings: RuntimeSettings, logger: logging.Logger) -> None:
        self.rpc = rpc
        self.settings = settings
        self.logger = logger
        self.lock_date_service = LockDateServiceAsync(rpc=rpc, logger=logger)

    async def precheck_account_move_dates(
        self,
        *,
        journal_ids: List[int],
        company_id: int,
        target_date_utc: str,
    ) -> tuple[bool, str, List[int]]:
        clean_journal_ids = sorted({int(item) for item in journal_ids if int(item) > 0})
        if not clean_journal_ids:
            return False, "Account move terkait tidak ditemukan untuk sinkronisasi tanggal STJ.", []

        target_day = local_date_from_utc_text(
            utc_text=target_date_utc,
            local_tz_offset_hours=self.settings.local_tz_offset,
            use_local_as_utc=self.settings.use_local_time_as_utc,
        ) or parse_iso_date(target_date_utc)
        if target_day is None:
            return False, f"Format tanggal STJ tidak valid: {normalize_text(target_date_utc) or '(kosong)'}", []

        lock_date, lock_err = await self.lock_date_service.get_close_acc_date_by_company_id(company_id)
        if lock_err and lock_date is None:
            return False, f"Gagal membaca accounting lock date company: {lock_err}", []
        if lock_date is not None and target_day <= lock_date:
            return (
                False,
                (
                    f"Tanggal STJ {target_day.strftime('%Y-%m-%d')} tidak dapat diubah karena accounting lock date "
                    f"company adalah {lock_date.strftime('%Y-%m-%d')}. Ubah ke tanggal setelah "
                    f"{lock_date.strftime('%Y-%m-%d')} atau buka lock period di Odoo."
                ),
                [],
            )

        context = build_company_context(company_id)
        rows = await self.rpc.read(
            model="account.move",
            ids=clean_journal_ids,
            fields=["id", "state"],
            context=context,
            stage="JOURNAL_DATE_PRECHECK",
        )
        resolved_ids = {int(row.get("id") or 0) for row in rows if int(row.get("id") or 0) > 0}
        missing_ids = [item for item in clean_journal_ids if item not in resolved_ids]
        if missing_ids:
            return False, f"Account move terkait tidak ditemukan untuk ids={missing_ids}.", []

        posted_ids = sorted(
            int(row.get("id") or 0)
            for row in rows
            if int(row.get("id") or 0) > 0 and normalize_text(row.get("state")).lower() == "posted"
        )
        return True, "", posted_ids

    async def sync_account_move_dates(
        self,
        *,
        journal_ids: List[int],
        company_id: int,
        target_date_utc: str,
    ) -> tuple[bool, str, str]:
        ok, err, originally_posted_ids = await self.precheck_account_move_dates(
            journal_ids=journal_ids,
            company_id=company_id,
            target_date_utc=target_date_utc,
        )
        if not ok:
            return False, err, ""

        context = build_company_context(company_id)
        target_day = local_date_from_utc_text(
            utc_text=target_date_utc,
            local_tz_offset_hours=self.settings.local_tz_offset,
            use_local_as_utc=self.settings.use_local_time_as_utc,
        ) or parse_iso_date(target_date_utc)
        if target_day is None:
            return False, f"Format tanggal STJ tidak valid: {normalize_text(target_date_utc) or '(kosong)'}", ""

        warnings = ""
        drafted_ids: List[int] = []
        if originally_posted_ids:
            ok, err, draft_method = await self._move_posted_journals_to_draft(
                journal_ids=originally_posted_ids,
                context=context,
            )
            if not ok:
                return False, err, warnings
            drafted_ids = originally_posted_ids[:]
            draft_message = "Posted STJ dipindahkan ke draft sebelum update tanggal."
            if draft_method and draft_method != "button_draft":
                draft_message = f"Posted STJ dipindahkan ke draft memakai {draft_method} sebelum update tanggal."
            warnings = append_error(warnings, draft_message)

        write_error = ""
        try:
            write_ok = await self.rpc.write(
                model="account.move",
                ids=sorted({int(item) for item in journal_ids if int(item) > 0}),
                values={"date": target_day.strftime("%Y-%m-%d")},
                context=context,
                stage="JOURNAL_DATE_WRITE",
            )
            if not write_ok:
                write_error = "RPC account.move.write mengembalikan False."
            else:
                verify_rows = await self.rpc.read(
                    model="account.move",
                    ids=sorted({int(item) for item in journal_ids if int(item) > 0}),
                    fields=["id", "date"],
                    context=context,
                    stage="JOURNAL_DATE_VERIFY",
                )
                expected_text = target_day.strftime("%Y-%m-%d")
                mismatches = [
                    f"id={int(row.get('id') or 0)} actual={normalize_text(row.get('date')) or '(missing)'}"
                    for row in verify_rows
                    if normalize_text(row.get("date")) != expected_text
                ]
                if mismatches:
                    write_error = (
                        f"Tanggal account.move tidak sinkron ke {expected_text}. sample={' | '.join(mismatches[:5])}"
                    )
        except Exception as exc:  # noqa: BLE001
            write_error = str(exc)
        finally:
            if drafted_ids:
                repost_ok, repost_err = await self._repost_journals(journal_ids=drafted_ids, context=context)
                if not repost_ok:
                    return False, repost_err, warnings

        if write_error:
            return False, write_error, warnings
        return True, "", warnings

    async def _move_posted_journals_to_draft(
        self,
        *,
        journal_ids: List[int],
        context: Dict[str, Any],
    ) -> tuple[bool, str, str]:
        attempts: List[str] = []
        for method_name in ("button_draft", "action_draft"):
            try:
                await self.rpc.execute_kw(
                    model="account.move",
                    method=method_name,
                    args=[journal_ids],
                    kwargs={"context": context},
                    stage="JOURNAL_TO_DRAFT",
                    mutating=True,
                )
            except Exception as exc:  # noqa: BLE001
                attempts.append(f"{method_name}: {exc}")
                continue

            remaining_posted = await self._collect_ids_by_state(
                journal_ids=journal_ids,
                state="posted",
                context=context,
                stage="JOURNAL_TO_DRAFT_VERIFY",
            )
            if not remaining_posted:
                return True, "", method_name
            attempts.append(f"{method_name}: state tetap posted untuk ids={remaining_posted}")

        detail = " | ".join(attempts) if attempts else "Metode draft tidak tersedia."
        return False, f"Gagal memindahkan STJ posted ke draft: {detail}", ""

    async def _repost_journals(
        self,
        *,
        journal_ids: List[int],
        context: Dict[str, Any],
    ) -> tuple[bool, str]:
        try:
            await self.rpc.execute_kw(
                model="account.move",
                method="action_post",
                args=[journal_ids],
                kwargs={"context": context},
                stage="JOURNAL_REPOST",
                mutating=True,
            )
        except Exception as exc:  # noqa: BLE001
            return False, f"Gagal restore STJ ke posted: {exc}"

        remaining_non_posted = await self._collect_non_posted_ids(
            journal_ids=journal_ids,
            context=context,
            stage="JOURNAL_REPOST_VERIFY",
        )
        if remaining_non_posted:
            return False, f"Gagal restore STJ ke posted: state akhir bukan posted untuk ids={remaining_non_posted}"
        return True, ""

    async def _collect_ids_by_state(
        self,
        *,
        journal_ids: List[int],
        state: str,
        context: Dict[str, Any],
        stage: str,
    ) -> List[int]:
        rows = await self.rpc.read(
            model="account.move",
            ids=journal_ids,
            fields=["id", "state"],
            context=context,
            stage=stage,
        )
        return sorted(
            int(row.get("id") or 0)
            for row in rows
            if int(row.get("id") or 0) > 0 and normalize_text(row.get("state")).lower() == state
        )

    async def _collect_non_posted_ids(
        self,
        *,
        journal_ids: List[int],
        context: Dict[str, Any],
        stage: str,
    ) -> List[int]:
        rows = await self.rpc.read(
            model="account.move",
            ids=journal_ids,
            fields=["id", "state"],
            context=context,
            stage=stage,
        )
        return sorted(
            int(row.get("id") or 0)
            for row in rows
            if int(row.get("id") or 0) > 0 and normalize_text(row.get("state")).lower() != "posted"
        )


class DateSyncServiceAsync:
    def __init__(self, rpc: AsyncOdooJsonRpcClient, settings: RuntimeSettings, logger: logging.Logger) -> None:
        self.rpc = rpc
        self.settings = settings
        self.logger = logger
        self.technical_logger = get_technical_logger()
        if not self.technical_logger.handlers:
            self.technical_logger.addHandler(logging.NullHandler())
        self.technical_logger.propagate = False
        self.capabilities: DateSyncCapabilities | None = None
        self._move_line_done_field: str | None = None
        self._move_line_demand_field: str | None = None
        self._compatibility_mode_announced = False

    async def initialize_capabilities(self) -> DateSyncCapabilities:
        if self.capabilities is not None:
            return self.capabilities

        done_cap = await self._resolve_date_field("stock.picking", ["date_done"], True, False)
        scheduled_cap = await self._resolve_date_field("stock.picking", ["scheduled_date"], True, False)
        move_cap = await self._resolve_date_field("stock.move", ["date"], True, False)
        move_line_cap = await self._resolve_date_field("stock.move.line", ["date"], True, False)
        svl_cap = await self._resolve_date_field("stock.valuation.layer", ["accounting_date", "date"], True, True)
        due_primary = await self._resolve_date_field("account.move", ["date_due_payment"], True, True)
        due_fallback = await self._resolve_date_field("account.move", ["invoice_date_due"], True, True)

        if svl_cap is None and self.settings.strict_svl_date_sync:
            raise RuntimeError("stock.valuation.layer tidak memiliki field tanggal writable (accounting_date/date).")

        self.capabilities = DateSyncCapabilities(
            stock_picking_done=done_cap,
            stock_picking_scheduled=scheduled_cap,
            stock_move=move_cap,
            stock_move_line=move_line_cap,
            stock_valuation_layer=svl_cap,
            account_move_due_primary=due_primary,
            account_move_due_fallback=due_fallback,
        )
        self.logger.info("DATE_SYNC_PREFLIGHT sukses.")
        return self.capabilities

    async def _resolve_date_field(
        self,
        model: str,
        candidates: List[str],
        require_writable: bool,
        allow_missing: bool,
    ) -> DateFieldCapability | None:
        meta = await self.rpc.fields_get(
            model=model,
            attributes=["type", "readonly"],
            stage="DATE_SYNC_PREFLIGHT",
        )
        for candidate in candidates:
            if candidate not in meta:
                continue
            field_meta = meta.get(candidate, {})
            field_type = normalize_text(field_meta.get("type")).lower()
            if field_type not in {"date", "datetime"}:
                continue
            is_readonly = _variant_to_bool(field_meta.get("readonly"))
            if require_writable and is_readonly:
                continue
            return DateFieldCapability(field_name=candidate, is_date_only=(field_type == "date"))

        if require_writable and not allow_missing:
            for candidate in candidates:
                if candidate not in meta:
                    continue
                field_meta = meta.get(candidate, {})
                field_type = normalize_text(field_meta.get("type")).lower()
                if field_type in {"date", "datetime"}:
                    if not self._compatibility_mode_announced:
                        self.logger.info("Sinkronisasi tanggal berjalan dalam mode kompatibel Odoo.")
                        self._compatibility_mode_announced = True
                    self.technical_logger.warning(
                        "Preflight %s: field %s readonly via fields_get, tetap digunakan dengan verifikasi ketat.",
                        model,
                        candidate,
                    )
                    return DateFieldCapability(field_name=candidate, is_date_only=(field_type == "date"))
            raise RuntimeError(f"{model}: Tidak ada field tanggal writable. kandidat={candidates}")

        if allow_missing:
            return None
        raise RuntimeError(f"{model}: Tidak ada field tanggal valid. kandidat={candidates}")

    async def force_date_and_validate(
        self,
        pick_id: int,
        company_id: int,
        date_done_utc: str,
    ) -> tuple[bool, str, str]:
        warnings = ""
        if pick_id <= 0:
            return False, "Picking belum dibuat.", warnings

        capabilities = await self.initialize_capabilities()
        company_ctx = build_company_context(company_id)
        force_ctx = _build_force_date_context(company_id, date_done_utc, self.settings)
        move_ids: List[int] = []
        journal_ids: List[int] = []
        journal_source = "none"
        journal_sync_service: AccountMoveDateSyncServiceAsync | None = None

        move_ids = await self.rpc.search(
            model="stock.move",
            domain=[["picking_id", "=", pick_id]],
            context=company_ctx,
            stage="POST_VALIDATE_CORE_SYNC",
        )
        if normalize_text(date_done_utc):
            picking_name = await self._read_picking_name(pick_id, company_ctx)
            resolver = JournalResolverAsync(rpc=self.rpc, logger=self.logger)
            journal_ids, journal_source = await resolver.resolve_account_move_ids_for_picking(
                picking_id=pick_id,
                move_ids=move_ids,
                company_id=company_id,
                picking_name=picking_name,
            )
            if journal_ids:
                journal_sync_service = AccountMoveDateSyncServiceAsync(
                    rpc=self.rpc,
                    settings=self.settings,
                    logger=self.logger,
                )
                ok, err, _posted_ids = await journal_sync_service.precheck_account_move_dates(
                    journal_ids=journal_ids,
                    company_id=company_id,
                    target_date_utc=date_done_utc,
                )
                if not ok:
                    return False, f"Gagal sinkronisasi date account.move: {err}", warnings

        if not normalize_text(date_done_utc):
            ok, err = await self._run_action_assign(pick_id=pick_id, context=company_ctx)
            if not ok:
                return False, err, warnings
            if bool(getattr(self.settings, "validate_enforce_done_qty", True)):
                ok, err = await self._enforce_done_qty_from_demand(
                    pick_id=pick_id,
                    company_id=company_id,
                    context=company_ctx,
                )
                if not ok:
                    return False, err, warnings
            ok, err, warn = await self._safe_validate_picking(
                pick_id=pick_id,
                company_id=company_id,
                context=company_ctx,
            )
            if not ok:
                return False, err, append_error(warnings, warn)
            warnings = append_error(warnings, warn)
            done, done_err = await self._ensure_picking_done(pick_id=pick_id, context=company_ctx)
            if not done:
                return False, done_err, warnings
            return True, "", warnings

        preset_ok, preset_err = await self._write_date_for_ids(
            model="stock.picking",
            ids=[pick_id],
            field=capabilities.stock_picking_scheduled,
            company_id=company_id,
            date_done_utc=date_done_utc,
            verify_only=False,
            context_override=company_ctx,
        )
        if not preset_ok:
            lower = preset_err.lower()
            if "done" in lower or "cancel" in lower:
                warnings = append_error(warnings, f"Preset scheduled_date dilewati: {preset_err}")
            else:
                return False, f"Gagal preset scheduled_date: {preset_err}", warnings

        ok, err = await self._run_action_assign(pick_id=pick_id, context=company_ctx)
        if not ok:
            return False, err, warnings
        if bool(getattr(self.settings, "validate_enforce_done_qty", True)):
            ok, err = await self._enforce_done_qty_from_demand(
                pick_id=pick_id,
                company_id=company_id,
                context=force_ctx,
            )
            if not ok:
                return False, err, warnings
        ok, err, warn = await self._safe_validate_picking(
            pick_id=pick_id,
            company_id=company_id,
            context=force_ctx,
        )
        if not ok:
            return False, err, append_error(warnings, warn)
        warnings = append_error(warnings, warn)
        done, done_err = await self._ensure_picking_done(pick_id=pick_id, context=company_ctx)
        if not done:
            return False, done_err, warnings

        ok, err = await self._write_date_for_ids(
            model="stock.picking",
            ids=[pick_id],
            field=capabilities.stock_picking_done,
            company_id=company_id,
            date_done_utc=date_done_utc,
            verify_only=False,
            context_override=force_ctx,
        )
        if not ok:
            return False, f"Gagal sinkronisasi stock.picking.{capabilities.stock_picking_done.field_name}: {err}", warnings

        sync_ctx = _build_move_line_context(company_id)
        if move_ids:
            ok, err = await self._write_date_for_ids(
                model="stock.move",
                ids=move_ids,
                field=capabilities.stock_move,
                company_id=company_id,
                date_done_utc=date_done_utc,
                verify_only=False,
                context_override=sync_ctx,
            )
            if not ok:
                return False, f"Gagal sinkronisasi stock.move.{capabilities.stock_move.field_name}: {err}", warnings

            line_ids = await self.rpc.search(
                model="stock.move.line",
                domain=[["move_id", "in", move_ids]],
                context=company_ctx,
                stage="POST_VALIDATE_CORE_SYNC",
            )
            if line_ids:
                ok, err = await self._write_date_for_ids(
                    model="stock.move.line",
                    ids=line_ids,
                    field=capabilities.stock_move_line,
                    company_id=company_id,
                    date_done_utc=date_done_utc,
                    verify_only=False,
                    context_override=sync_ctx,
                )
                if not ok:
                    return (
                        False,
                        f"Gagal sinkronisasi stock.move.line.{capabilities.stock_move_line.field_name}: {err}",
                        warnings,
                    )

            svl_cap = capabilities.stock_valuation_layer
            if svl_cap is not None:
                svl_ids = await self.rpc.search(
                    model="stock.valuation.layer",
                    domain=[["stock_move_id", "in", move_ids]],
                    context=company_ctx,
                    stage="POST_VALIDATE_CORE_SYNC",
                )
                if svl_ids:
                    ok, err = await self._write_date_for_ids(
                        model="stock.valuation.layer",
                        ids=svl_ids,
                        field=svl_cap,
                        company_id=company_id,
                        date_done_utc=date_done_utc,
                        verify_only=False,
                        context_override=sync_ctx,
                    )
                    if not ok:
                        return False, f"Gagal sinkronisasi stock.valuation.layer.{svl_cap.field_name}: {err}", warnings

        ok, err = await self._write_date_for_ids(
            model="stock.picking",
            ids=[pick_id],
            field=capabilities.stock_picking_scheduled,
            company_id=company_id,
            date_done_utc=date_done_utc,
            verify_only=True,
            context_override=company_ctx,
        )
        if not ok:
            return False, f"Verifikasi scheduled_date gagal: {err}", warnings

        if journal_ids:
            if journal_sync_service is None:
                journal_sync_service = AccountMoveDateSyncServiceAsync(
                    rpc=self.rpc,
                    settings=self.settings,
                    logger=self.logger,
                )
            ok, err, warn = await journal_sync_service.sync_account_move_dates(
                journal_ids=journal_ids,
                company_id=company_id,
                target_date_utc=date_done_utc,
            )
            warnings = append_error(warnings, warn)
            if not ok:
                return False, f"Gagal sinkronisasi date account.move: {err}", warnings

            due_primary = capabilities.account_move_due_primary
            due_fallback = capabilities.account_move_due_fallback
            due_written = False
            primary_err = ""
            if due_primary is not None:
                due_written, primary_err = await self._write_date_for_ids(
                    model="account.move",
                    ids=journal_ids,
                    field=due_primary,
                    company_id=company_id,
                    date_done_utc=date_done_utc,
                    verify_only=False,
                    context_override=company_ctx,
                )
            if not due_written and due_fallback is not None:
                due_written, fallback_err = await self._write_date_for_ids(
                    model="account.move",
                    ids=journal_ids,
                    field=due_fallback,
                    company_id=company_id,
                    date_done_utc=date_done_utc,
                    verify_only=False,
                    context_override=company_ctx,
                )
                if not due_written:
                    detail = append_error(primary_err, fallback_err)
                    return False, f"Gagal sinkronisasi due date account.move: {detail}", warnings
                if primary_err:
                    warnings = append_error(
                        warnings,
                        f"Primary due date gagal ({primary_err}), memakai fallback {due_fallback.field_name}.",
                    )
        else:
            warnings = append_error(
                warnings,
                f"account.move terkait belum ditemukan untuk sinkronisasi tanggal jurnal (source={journal_source}).",
            )

        return True, "", warnings

    async def _run_action_assign(self, pick_id: int, context: Dict[str, Any]) -> tuple[bool, str]:
        try:
            await self.rpc.execute_kw(
                model="stock.picking",
                method="action_assign",
                args=[[pick_id]],
                kwargs={"context": context},
                stage="VALIDATE",
            )
            return True, ""
        except Exception as exc:  # noqa: BLE001
            msg = normalize_text(str(exc)).lower()
            if "done" in msg or "already" in msg:
                return True, ""
            return False, str(exc)

    async def _safe_validate_picking(
        self,
        pick_id: int,
        company_id: int,
        context: Dict[str, Any],
    ) -> tuple[bool, str, str]:
        _ = company_id
        warnings = ""
        try:
            result = await self.rpc.execute_kw(
                model="stock.picking",
                method="button_validate",
                args=[[pick_id]],
                kwargs={"context": context},
                stage="VALIDATE",
            )
        except Exception as exc:  # noqa: BLE001
            msg = normalize_text(str(exc)).lower()
            if "done" in msg or "already" in msg or "validated" in msg:
                return True, "", warnings
            return False, str(exc), warnings

        if not isinstance(result, dict):
            return True, "", warnings

        wizard_model = normalize_text(result.get("res_model")).lower()
        if not wizard_model:
            return True, "", warnings

        wizard_ctx = dict(context)
        wizard_ctx.update(
            {
                "active_model": "stock.picking",
                "active_id": int(pick_id),
                "active_ids": [int(pick_id)],
            }
        )
        wiz_values = {"pick_ids": [[6, 0, [int(pick_id)]]]}

        if wizard_model == "stock.immediate.transfer":
            try:
                wiz_id = await self.rpc.create(
                    model=wizard_model,
                    values=wiz_values,
                    context=wizard_ctx,
                    stage="VALIDATE_WIZARD",
                )
                if wiz_id <= 0:
                    return False, "Wizard Immediate Transfer gagal dibuat.", warnings
                await self.rpc.execute_kw(
                    model=wizard_model,
                    method="process",
                    args=[[wiz_id]],
                    kwargs={"context": wizard_ctx},
                    stage="VALIDATE_WIZARD",
                )
            except Exception as exc:  # noqa: BLE001
                return False, f"Wizard immediate transfer gagal: {exc}", warnings
            return True, "", warnings

        if wizard_model == "stock.backorder.confirmation":
            policy = normalize_text(getattr(self.settings, "validate_backorder_policy", "fail")).lower() or "fail"
            if policy == "fail":
                return (
                    False,
                    (
                        "Backorder terdeteksi saat validate dan ditolak "
                        "(VALIDATE_BACKORDER_POLICY=fail, residual qty tidak diizinkan)."
                    ),
                    warnings,
                )
            if policy not in {"create_backorder", "cancel_backorder"}:
                return False, f"VALIDATE_BACKORDER_POLICY tidak dikenal: {policy}", warnings

            method_name = "process" if policy == "create_backorder" else "process_cancel_backorder"
            try:
                wiz_id = await self.rpc.create(
                    model=wizard_model,
                    values=wiz_values,
                    context=wizard_ctx,
                    stage="VALIDATE_WIZARD",
                )
                if wiz_id <= 0:
                    return False, "Wizard backorder gagal dibuat.", warnings
                await self.rpc.execute_kw(
                    model=wizard_model,
                    method=method_name,
                    args=[[wiz_id]],
                    kwargs={"context": wizard_ctx},
                    stage="VALIDATE_WIZARD",
                )
            except Exception as exc:  # noqa: BLE001
                return False, f"Wizard backorder gagal: {exc}", warnings
            warnings = append_error(warnings, f"Backorder wizard diproses dengan method={method_name}.")
            return True, "", warnings

        warnings = append_error(warnings, f"Wizard validate tidak dikenali: {wizard_model}")
        return True, "", warnings

    async def _ensure_picking_done(self, pick_id: int, context: Dict[str, Any]) -> tuple[bool, str]:
        rows = await self.rpc.read(
            model="stock.picking",
            ids=[pick_id],
            fields=["id", "name", "state"],
            context=context,
            stage="VALIDATE_VERIFY",
        )
        if not rows:
            return False, f"Picking {pick_id} tidak ditemukan setelah validate."
        rec = rows[0]
        pick_name = normalize_text(rec.get("name")) or str(pick_id)
        state = normalize_text(rec.get("state")).lower()
        if state == "done":
            return True, ""
        if state == "cancel":
            return False, f"Picking {pick_name} berstatus cancel setelah validate."
        return False, f"Picking {pick_name} belum done setelah validate (state={state or '-'})."

    async def _resolve_move_line_qty_fields(self, company_id: int) -> tuple[str, str]:
        if self._move_line_done_field is not None:
            return self._move_line_done_field, self._move_line_demand_field or ""

        context = build_company_context(company_id)
        try:
            meta = await self.rpc.fields_get(
                model="stock.move.line",
                attributes=["type"],
                context=context,
                stage="VALIDATE_PREFLIGHT",
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("Gagal fields_get stock.move.line untuk qty enforcement: %s", exc)
            self._move_line_done_field = "qty_done"
            self._move_line_demand_field = ""
            return self._move_line_done_field, self._move_line_demand_field

        def _exists_numeric(field_name: str) -> bool:
            info = meta.get(field_name, {})
            if not isinstance(info, dict):
                return False
            field_type = normalize_text(info.get("type")).lower()
            return field_type in {"float", "integer", "monetary"}

        done_field = ""
        for candidate in ["quantity", "qty_done"]:
            if _exists_numeric(candidate):
                done_field = candidate
                break
        if not done_field:
            done_field = "qty_done"

        demand_field = ""
        for candidate in ["product_uom_qty", "reserved_uom_qty"]:
            if candidate != done_field and _exists_numeric(candidate):
                demand_field = candidate
                break

        self._move_line_done_field = done_field
        self._move_line_demand_field = demand_field
        return done_field, demand_field

    async def _enforce_done_qty_from_demand(
        self,
        pick_id: int,
        company_id: int,
        context: Dict[str, Any],
    ) -> tuple[bool, str]:
        done_field, demand_field = await self._resolve_move_line_qty_fields(company_id)
        if not normalize_text(done_field):
            return False, "Field done quantity stock.move.line tidak tersedia."

        line_fields = ["id", "move_id", done_field]
        if demand_field:
            line_fields.append(demand_field)

        line_rows = await self.rpc.search_read(
            model="stock.move.line",
            domain=[["picking_id", "=", int(pick_id)]],
            fields=line_fields,
            context=context,
            stage="VALIDATE_DONE_QTY_SYNC",
        )
        if not line_rows:
            return True, ""

        move_qty_map: Dict[int, float] = {}
        if not demand_field:
            move_rows = await self.rpc.search_read(
                model="stock.move",
                domain=[["picking_id", "=", int(pick_id)]],
                fields=["id", "product_uom_qty"],
                context=context,
                stage="VALIDATE_DONE_QTY_SYNC",
            )
            for move in move_rows:
                move_id = int(move.get("id") or 0)
                if move_id <= 0:
                    continue
                try:
                    move_qty_map[move_id] = float(move.get("product_uom_qty") or 0.0)
                except (TypeError, ValueError):
                    move_qty_map[move_id] = 0.0

        line_targets: List[tuple[int, float]] = []
        for line in line_rows:
            line_id = int(line.get("id") or 0)
            if line_id <= 0:
                continue
            try:
                current_done = float(line.get(done_field) or 0.0)
            except (TypeError, ValueError):
                current_done = 0.0

            if demand_field:
                try:
                    target_qty = float(line.get(demand_field) or 0.0)
                except (TypeError, ValueError):
                    target_qty = 0.0
            else:
                move_id = extract_many2one_id(line.get("move_id"))
                target_qty = float(move_qty_map.get(move_id, current_done))

            if abs(current_done - target_qty) <= 1e-9:
                continue
            line_targets.append((line_id, target_qty))

        for line_id, target_qty in line_targets:
            ok = await self.rpc.write(
                model="stock.move.line",
                ids=[int(line_id)],
                values={done_field: float(target_qty)},
                context=context,
                stage="VALIDATE_DONE_QTY_SYNC",
            )
            if not ok:
                return False, f"Gagal write done quantity stock.move.line id={line_id}."

        return True, ""

    async def _read_picking_name(self, pick_id: int, context: Dict[str, Any]) -> str:
        return await read_picking_name(self.rpc, pick_id=pick_id, context=context)

    async def _write_date_for_ids(
        self,
        model: str,
        ids: List[int],
        field: DateFieldCapability,
        company_id: int,
        date_done_utc: str,
        verify_only: bool,
        context_override: Dict[str, Any] | None = None,
    ) -> tuple[bool, str]:
        if not ids:
            return True, ""
        if not field.field_name:
            return False, f"Field tanggal model {model} belum ditentukan."

        write_value = _build_write_value(date_done_utc, field.is_date_only, self.settings)
        context = context_override or _build_force_date_context(company_id, date_done_utc, self.settings)

        last_mismatch = ""
        for _attempt in range(1, self.settings.move_date_retry_count + 1):
            if not verify_only:
                ok = await self.rpc.write(
                    model=model,
                    ids=ids,
                    values={field.field_name: write_value},
                    context=context,
                    stage="DATE_WRITE",
                )
                if not ok:
                    return False, f"RPC {model}.write mengembalikan False."

            rows = await self.rpc.read(
                model=model,
                ids=ids,
                fields=["id", field.field_name],
                context=build_company_context(company_id),
                stage="DATE_VERIFY",
            )
            value_by_id: Dict[int, str] = {}
            for item in rows:
                try:
                    rec_id = int(item.get("id"))
                except (TypeError, ValueError):
                    continue
                value_by_id[rec_id] = normalize_text(item.get(field.field_name))

            mismatch: List[str] = []
            for record_id in ids:
                actual = value_by_id.get(record_id, "")
                if not _date_field_matches_expected(actual, date_done_utc, field.is_date_only, self.settings):
                    mismatch.append(f"id={record_id} actual={actual or '(missing)'}")

            if not mismatch:
                return True, ""

            last_mismatch = " | ".join(mismatch[:5])
            if self.settings.move_date_retry_delay_ms > 0:
                time_to_sleep = self.settings.move_date_retry_delay_ms / 1000.0
                if time_to_sleep > 0:
                    await asyncio.sleep(time_to_sleep)

        return False, f"Tanggal {model}.{field.field_name} tidak sinkron. sample={last_mismatch}"


__all__ = [
    "AccountMoveDateSyncServiceAsync",
    "DateFieldCapability",
    "DateSyncCapabilities",
    "DateSyncServiceAsync",
    "JournalResolverAsync",
    "LockDateFetchResult",
    "LockDateServiceAsync",
    "collect_active_company_ids",
    "read_picking_name",
]
