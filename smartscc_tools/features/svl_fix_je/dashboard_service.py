"""Async read-only dashboard analysis for SVL vs balance-sheet reconciliation."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from contextlib import suppress
from dataclasses import replace
from datetime import datetime
import logging
import re
from time import perf_counter
from typing import Any, Callable

from smartscc_tools.services.odoo.gateway import build_company_context
from smartscc_tools.features.item_journal.utils import chunked, normalize_text, parse_iso_date, to_float

from .config import DEFAULT_JOURNAL_CODE, normalize_dashboard_dataset_mode, PURCHASE_CYCLE_BALANCE_DATASET_MODE
from .models import (
    SvlDashboardBillLine,
    SvlDashboardCompany,
    SvlDashboardCompanySummary,
    SvlDashboardCycleAccountRow,
    SvlDashboardCycleItemRow,
    SvlDashboardItemDetail,
    SvlDashboardItem,
    SvlDashboardInventoryCoaRow,
    SvlDashboardJournalRecord,
    SvlDashboardPcbAdjustmentAuditRow,
    SvlDashboardPcbCase2RepairRow,
    SvlDashboardMergedRecord,
    SvlDashboardPcbCase1LinkRow,
    SvlDashboardPcbRepairPlannedLine,
    SvlDashboardPayableLine,
    SvlDashboardPoLine,
    SvlDashboardProgressSnapshot,
    SvlDashboardPurchaseCycle,
    SvlDashboardRepairAccountCandidate,
    SvlDashboardRequest,
    SvlDashboardSnapshot,
    SvlDashboardSvlRecord,
)
from .pcb_repair_labels import build_pcb_selisih_hpp_label
from .validators import detect_account_company_field, extract_reference_from_description


LogCallback = Callable[[str], None]
ProgressCallback = Callable[[SvlDashboardProgressSnapshot], None]

_VALUATION_NAME_TOKENS = ("persediaan", "inventory", "stock", "valuation", "barang")
_UNASSIGNED_JOURNAL_PID = -1
_CATEGORY_ACCOUNT_SOURCE_PRIORITY = {
    "Category Stock Valuation": 0,
    "Category Expense": 20,
    "Category Input": 30,
    "Category Output": 40,
    "Category Cost": 50,
    "Category Other": 90,
}
_PCB_COGS_VARIANCE_CODES = frozenset({"5101010"})
_PCB_CASE2_PROBLEM_CODES = ("1108099", "2103006")
_PCB_ADJUSTMENT_CLEARING_CODE = "1108099"
_PCB_REPAIRABLE_CASES = ("case1", "case2", "case3", "case4", "case5", "case6", "case8a", "case8b", "case9")
_PCB_EDGE_CASES = ("edge_partial_bill", "edge_return_no_credit_memo", "edge_stj_corrupt", "case10")
_PCB_DOCUMENT_CLASS_PURCHASE_BACKED = "purchase-backed"
_PCB_DOCUMENT_CLASS_PURCHASE_LIKELY = "purchase-likely"
_PCB_DOCUMENT_CLASS_NON_PURCHASE = "non-purchase/intercompany"
_PCB_DOCUMENT_CLASS_LABEL = {
    _PCB_DOCUMENT_CLASS_PURCHASE_BACKED: "Purchase-Backed",
    _PCB_DOCUMENT_CLASS_PURCHASE_LIKELY: "Purchase-Likely",
    _PCB_DOCUMENT_CLASS_NON_PURCHASE: "Non-Purchase / Intercompany",
}
_PCB_PURCHASE_INVENTORY_TYPES = frozenset({"purchase", "purchase_return"})
_PCB_NON_PURCHASE_INVENTORY_TYPES = frozenset(
    {"mutation", "internal_transfer", "sales", "sales_return", "production", "stock_adjustment", "other"}
)
_PCB_INVENTORY_TYPE_LABEL = {
    "purchase": "Purchase",
    "purchase_return": "Purchase Return",
    "sales": "Sales",
    "sales_return": "Sales Return",
    "mutation": "Mutation",
    "production": "Production",
    "internal_transfer": "Internal Transfer",
    "stock_adjustment": "Stock Adjustment",
    "other": "Other",
}
_PCB_CASE_PRIORITY = {
    "case5": 0,
    "case6": 1,
    "case1": 2,
    "case2": 3,
    "case3": 4,
    "case4": 5,
    "case8a": 6,
    "case8b": 7,
    "case9": 8,
    "case10": 9,
    "edge_partial_bill": 10,
    "edge_return_no_credit_memo": 11,
    "edge_stj_corrupt": 12,
    "case_lainnya": 13,
}
_PCB_MULTI_LINE_CASE_LABELS = {
    "case2": "Case 2 - STJ Bill Price Diff (Suspend - Suspend)",
    "case3": "Case 3 - STJ Bill Hit Expenses (Clearing - Expenses)",
    "case4": "Case 4 - STJ Bill Hit Expenses (Suspend - Expenses)",
    "case5": "Case 5 - Pemulihan SVL (Inventory - Suspense)",
    "case6": "Case 6 - Pemulihan SVL (Inventory - Bill Expense)",
    "case8a": "Case 8A - Full Return Value Mismatch",
    "case8b": "Case 8B - Partial Return Value Mismatch",
    "case9": "Case 9 - UoM Scale Mismatch",
}
# Deskripsi singkat per case — ditampilkan di kolom "Keterangan Case" pada export cycle.
_PCB_CASE_DESCRIPTIONS: dict[str, str] = {
    "case1":                      "Harga STJ berbeda dengan harga Bill; selisih masih di akun Suspend (2103006). Koreksi: jurnal penyesuaian harga.",
    "case2":                      "Harga STJ dan Bill sama-sama masuk Suspend (2103006); double-entry suspend belum ter-clear. Koreksi: jurnal clearing antar suspend.",
    "case3":                      "STJ hit Expenses langsung via Clearing (1108099); Bill sudah ter-match ke akun beban. Koreksi: jurnal balik Clearing ke Persediaan.",
    "case4":                      "STJ hit Expenses langsung via Suspend (2103006); Bill sudah ter-match ke akun beban. Koreksi: jurnal balik Suspend ke Persediaan.",
    "case5":                      "Pulihkan SVL aktual: Dr Persediaan / Cr Suspense; sisa suspense ke beban setelah review revaluasi/pemakaian.",
    "case6":                      "Pulihkan SVL aktual: Dr Persediaan / Cr akun expense bill aktual; wajib review jurnal existing.",
    "case8a":                     "Return picking ada namun nilai retur tidak sesuai dengan nilai GR awal (Full Return Value Mismatch). Koreksi: penyesuaian jurnal retur.",
    "case8b":                     "Return picking sebagian dan nilai retur tidak sesuai dengan proporsi GR (Partial Return Value Mismatch). Koreksi: penyesuaian jurnal retur parsial.",
    "case9":                      "Satuan (UoM) antara GR dan Bill tidak konsisten sehingga terjadi selisih nilai. Koreksi: konversi UoM dan penyesuaian harga.",
    "case10":                     "GR sudah done dan STJ terbentuk, tetapi vendor belum mengirimkan bill sama sekali (invoice_count = 0). 2103006 outstanding menunggu tagihan vendor. Tidak ada koreksi jurnal; perlu follow-up ke vendor atau tim procurement.",
    "edge_partial_bill":          "Bill hanya mencakup sebagian qty GR; saldo 2103006 tersisa adalah GRNI yang masih menunggu billing lanjutan. Tidak perlu koreksi segera.",
    "edge_return_no_credit_memo": "Ada return picking ke vendor tetapi belum ada credit memo (in_refund) yang ter-posting. Saldo retur belum ter-offset; perlu credit memo atau jurnal koreksi manual.",
    "edge_stj_corrupt":           "Header STJ/GR terdeteksi tetapi line item STJ tidak valid atau kosong. Data integrity perlu diperiksa sebelum cycle ini bisa dianalisis lebih lanjut.",
    "case_lainnya":               "Cycle tidak memenuhi kriteria case 1-9 maupun edge case yang dikenal. Perlu review manual untuk menentukan aksi koreksi.",
}
_PCB_MULTI_LINE_CASES = frozenset(_PCB_MULTI_LINE_CASE_LABELS)
_PCB_MOVE_NAME_TOKEN_RE = re.compile(r"\b[A-Z0-9]+(?:/[A-Z0-9]+){2,5}\b")
_PCB_INTERNAL_SIGNAL_PHRASES = (
    "intercompany",
    "inter company",
    "inter_company",
    "interco",
    "mutasi",
    "internal transfer",
    "transfer antar company",
    "transfer antar gudang",
)


def _many2one_id(value: Any) -> int:
    if isinstance(value, (list, tuple)) and value:
        try:
            return int(value[0])
        except (TypeError, ValueError):
            return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _many2one_name(value: Any) -> str:
    if isinstance(value, (list, tuple)) and len(value) > 1:
        return normalize_text(value[1])
    return ""


def _many2many_ids(value: Any) -> list[int]:
    if isinstance(value, (list, tuple)):
        result: list[int] = []
        seen: set[int] = set()
        for entry in value:
            try:
                current = int(entry or 0)
            except (TypeError, ValueError):
                continue
            if current <= 0 or current in seen:
                continue
            seen.add(current)
            result.append(current)
        return result
    return []


def _round2(value: Any) -> float:
    return round(to_float(value), 2)


def _normalized_match_text(value: str | None) -> str:
    return "".join(ch for ch in normalize_text(value).strip().lower() if ch.isalnum())


def _amount_matches(left: Any, right: Any, *, tolerance: float = 0.01) -> bool:
    return abs(_round2(left) - _round2(right)) <= tolerance


def _normalize_date_filter(value: str) -> str:
    clean = normalize_text(value)
    if not clean:
        return ""
    parsed = parse_iso_date(clean)
    if parsed is None:
        raise ValueError(f"Format tanggal harus YYYY-MM-DD, dapat '{clean}'.")
    return parsed.isoformat()


def _normalize_inventory_coa_codes(values: list[str] | None) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        code = normalize_text(value).strip().upper()
        if not code or code in seen:
            continue
        seen.add(code)
        normalized.append(code)
    return normalized


def _split_origin_names(value: Any) -> list[str]:
    clean = normalize_text(value)
    if not clean:
        return []
    return [part.strip() for part in clean.split(",") if part.strip()]


def _sorted_unique_text(values: Any) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        clean = normalize_text(value)
        if not clean or clean in seen:
            continue
        seen.add(clean)
        normalized.append(clean)
    normalized.sort()
    return normalized


def _join_text(values: Any, *, separator: str = ", ") -> str:
    return separator.join(_sorted_unique_text(values))


def _extract_move_name_tokens(*values: Any) -> list[str]:
    tokens: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = normalize_text(value).upper()
        if not clean:
            continue
        for match in _PCB_MOVE_NAME_TOKEN_RE.findall(clean):
            token = normalize_text(match).upper()
            if not token or not any(ch.isdigit() for ch in token) or token in seen:
                continue
            seen.add(token)
            tokens.append(token)
    return tokens


def _partner_match_key(value: Any) -> str:
    partner_id = _many2one_id(value)
    if partner_id > 0:
        return f"id:{partner_id}"
    if isinstance(value, (list, tuple)):
        partner_name = _many2one_name(value)
    else:
        partner_name = normalize_text(value)
    if partner_name:
        return f"name:{_normalized_match_text(partner_name)}"
    return ""


def _move_name_prefix(value: Any) -> str:
    clean_name = normalize_text(value).strip().upper()
    if "/" not in clean_name:
        return clean_name
    return clean_name.split("/", 1)[0]


def _account_is_payment_bank_like(account_row: dict[str, Any]) -> bool:
    account_code = normalize_text(account_row.get("code")).strip().upper()
    account_type = normalize_text(account_row.get("account_type")).strip().lower()
    account_name = normalize_text(account_row.get("name")).strip().lower()
    normalized_account_name = _normalized_match_text(account_name)
    if account_code in {"2101002", "11120003"}:
        return True
    if account_type in {"asset_cash", "liquidity"}:
        return True
    if "cash" in account_type or "liquidity" in account_type:
        return True
    return any(token in normalized_account_name for token in ("bank", "cash", "kas", "bca", "bni", "bri", "mandiri"))


class SvlDashboardServiceAsync:
    def __init__(
        self,
        *,
        rpc: Any,
        logger: logging.Logger,
        on_log: LogCallback | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> None:
        self.rpc = rpc
        self.logger = logger
        self.on_log = on_log
        self.on_progress = on_progress
        self._fields_cache: dict[str, dict[str, Any]] = {}

    def _log(self, message: str, *, level: int = logging.INFO) -> None:
        self.logger.log(level, message)
        if self.on_log is not None:
            self.on_log(message)

    def _emit_progress(
        self,
        *,
        phase: str,
        processed: int,
        total: int,
        current: str,
        progress: float,
    ) -> None:
        if self.on_progress is None:
            return
        self.on_progress(
            SvlDashboardProgressSnapshot(
                phase=phase,
                processed=processed,
                total=total,
                current=current,
                progress=max(0.0, min(float(progress), 1.0)),
            )
        )

    def _warn_once(self, warnings: list[str], message: str) -> None:
        if message in warnings:
            return
        warnings.append(message)
        self._log(message, level=logging.WARNING)

    async def _fields_get_cached(self, model: str) -> dict[str, Any]:
        cached = self._fields_cache.get(model)
        if cached is not None:
            return cached
        try:
            cached = await self.rpc.fields_get(
                model,
                attributes=["type", "readonly", "relation"],
                stage=f"SVL_DASH_FIELDS_{model}",
            )
        except Exception:  # noqa: BLE001
            cached = {}
        self._fields_cache[model] = cached
        return cached

    async def _supported_fields(self, model: str, preferred_fields: list[str]) -> list[str]:
        meta = await self._fields_get_cached(model)
        if not meta:
            return list(preferred_fields)
        return [field_name for field_name in preferred_fields if field_name in meta]

    @staticmethod
    def _resolve_matching_partner_key_from_move_row(
        move_row: dict[str, Any] | None,
        *,
        stock_move_rows_by_id: dict[int, dict[str, Any]] | None = None,
        picking_rows_by_id: dict[int, dict[str, Any]] | None = None,
    ) -> str:
        move_row = move_row or {}
        partner_key = _partner_match_key(move_row.get("partner_id"))
        if partner_key:
            return partner_key
        stock_move_rows_by_id = stock_move_rows_by_id or {}
        picking_rows_by_id = picking_rows_by_id or {}
        stock_move_id = _many2one_id(move_row.get("stock_move_id"))
        if stock_move_id <= 0:
            return ""
        stock_move_row = stock_move_rows_by_id.get(stock_move_id) or {}
        picking_id = _many2one_id(stock_move_row.get("picking_id"))
        if picking_id <= 0:
            return ""
        picking_row = picking_rows_by_id.get(picking_id) or {}
        return _partner_match_key(picking_row.get("partner_id"))

    @staticmethod
    def _matching_partner_keys_compatible(seed_partner_key: str, candidate_partner_key: str) -> bool:
        if not seed_partner_key or not candidate_partner_key:
            return False
        return seed_partner_key == candidate_partner_key

    @classmethod
    def _matching_compatible_seed_bills(
        cls,
        seed_bill_ids: set[int],
        *,
        candidate_partner_key: str,
        bill_partner_key_by_move_id: dict[int, str],
    ) -> set[int]:
        if not candidate_partner_key:
            return set()
        return {
            seed_bill_id
            for seed_bill_id in seed_bill_ids
            if cls._matching_partner_keys_compatible(
                bill_partner_key_by_move_id.get(seed_bill_id, ""),
                candidate_partner_key,
            )
        }

    @staticmethod
    def _is_vendor_bill_move_type(move_type: Any) -> bool:
        return normalize_text(move_type).strip().lower() in {"in_invoice", "in_refund"}

    @classmethod
    def _is_vendor_bill_move(
        cls,
        move_id: Any,
        *,
        move_info_map: dict[int, dict[str, Any]] | None = None,
        bill_rows_by_id: dict[int, dict[str, Any]] | None = None,
    ) -> bool:
        clean_move_id = int(move_id or 0)
        if clean_move_id <= 0:
            return False
        move_row = dict((move_info_map or {}).get(clean_move_id) or {})
        move_type = normalize_text(move_row.get("move_type"))
        if move_type:
            return cls._is_vendor_bill_move_type(move_type)
        if move_row:
            inferred_prefix = _move_name_prefix(move_row.get("name"))
            if inferred_prefix in {"STJ", "PBK", "BK"}:
                return False
            if inferred_prefix == "BILL":
                return True
        bill_row = dict((bill_rows_by_id or {}).get(clean_move_id) or {})
        bill_move_type = normalize_text(bill_row.get("move_type"))
        if bill_move_type:
            return cls._is_vendor_bill_move_type(bill_move_type)
        if bill_row:
            inferred_prefix = _move_name_prefix(bill_row.get("name"))
            if inferred_prefix in {"STJ", "PBK", "BK"}:
                return False
            return True
        return False

    @classmethod
    def _filter_vendor_bill_move_ids(
        cls,
        move_ids: list[int] | set[int] | tuple[int, ...] | None,
        *,
        move_info_map: dict[int, dict[str, Any]] | None = None,
        bill_rows_by_id: dict[int, dict[str, Any]] | None = None,
    ) -> list[int]:
        return sorted(
            {
                int(move_id or 0)
                for move_id in list(move_ids or [])
                if cls._is_vendor_bill_move(
                    move_id,
                    move_info_map=move_info_map,
                    bill_rows_by_id=bill_rows_by_id,
                )
            }
        )

    @staticmethod
    def _classify_matching_entry_candidate(
        *,
        move_row: dict[str, Any] | None,
        aml_rows: list[dict[str, Any]],
        account_info_map: dict[int, dict[str, Any]],
    ) -> str:
        move_row = move_row or {}
        move_type = normalize_text(move_row.get("move_type"))
        if SvlDashboardServiceAsync._is_vendor_bill_move_type(move_type):
            return "bill"
        if move_type != "entry":
            return "other_entry"
        if _many2one_id(move_row.get("stock_move_id")) > 0:
            return "stj"
        move_name = normalize_text(move_row.get("name"))
        name_prefix = _move_name_prefix(move_name)
        if name_prefix == "STJ":
            return "stj"
        if name_prefix in {"PBK", "BK"}:
            return "payment_bank"
        account_ids = {
            _many2one_id(row.get("account_id"))
            for row in aml_rows
            if _many2one_id(row.get("account_id")) > 0
        }
        if any(
            _account_is_payment_bank_like(account_info_map.get(account_id) or {})
            for account_id in account_ids
        ):
            return "payment_bank"
        return "other_entry"

    async def list_companies(self) -> list[SvlDashboardCompany]:
        await self.rpc.ensure_login()
        self._log("Memuat daftar company dari Odoo...")
        rows = await self.rpc.search_read(
            "res.company",
            [],
            fields=["name"],
            order="name",
            stage="SVL_DASH_LIST_COMPANIES",
        )
        companies = [
            SvlDashboardCompany(
                company_id=int(row.get("id") or 0),
                name=normalize_text(row.get("name")) or f"Company #{int(row.get('id') or 0)}",
            )
            for row in rows
            if int(row.get("id") or 0) > 0
        ]
        self._log(f"{len(companies)} company ditemukan.")
        return companies

    async def analyze(self, request: SvlDashboardRequest) -> SvlDashboardSnapshot:
        await self.rpc.ensure_login()
        date_from = _normalize_date_filter(request.date_from)
        date_to = _normalize_date_filter(request.date_to)
        if date_from and date_to and date_from > date_to:
            raise ValueError("Date From tidak boleh lebih besar dari Date To.")

        dataset_mode = normalize_dashboard_dataset_mode(getattr(request, "dataset_mode", ""))
        context = build_company_context(request.company_id)
        self._emit_progress(
            phase="prepare",
            processed=0,
            total=6,
            current="Mempersiapkan capability Odoo...",
            progress=0.05,
        )
        capabilities = await self._detect_capabilities()
        warnings: list[str] = []
        if dataset_mode == PURCHASE_CYCLE_BALANCE_DATASET_MODE:
            return await self._analyze_purchase_cycle_balance(
                request=request,
                date_from=date_from,
                date_to=date_to,
                context=context,
                capabilities=capabilities,
                warnings=warnings,
            )

        company_name = await self._fetch_company_name(request.company_id, context=context)

        valuation_account_ids = await self._detect_valuation_accounts(
            company_id=request.company_id,
            context=context,
            account_company_field=capabilities["account_company_field"],
            account_fields=capabilities["account_fields"],
            category_fields=capabilities["category_fields"],
        )
        if not valuation_account_ids:
            self._warn_once(warnings, "Akun valuasi persediaan tidak terdeteksi; layer journal dan BS bisa tidak lengkap.")

        company_summary = await self._fetch_company_summary(
            company_id=request.company_id,
            context=context,
            date_from=date_from,
            date_to=date_to,
            svl_date_field=capabilities["svl_date_field"],
            link_supported=capabilities["svl_link_supported"],
            warnings=warnings,
            inventory_coa_codes=request.inventory_coa_codes,
            account_company_field=capabilities["account_company_field"],
            aml_fields=capabilities["aml_fields"],
        )

        self._emit_progress(
            phase="aggregate",
            processed=1,
            total=6,
            current="Mengambil agregasi SVL dan journal...",
            progress=0.18,
        )
        svl_totals = await self._fetch_svl_totals(
            company_id=request.company_id,
            context=context,
            date_from=date_from,
            date_to=date_to,
            svl_date_field=capabilities["svl_date_field"],
            link_supported=capabilities["svl_link_supported"],
            warnings=warnings,
        )
        journal_totals = await self._fetch_journal_totals(
            company_id=request.company_id,
            context=context,
            date_from=date_from,
            date_to=date_to,
            valuation_account_ids=valuation_account_ids,
            aml_fields=capabilities["aml_fields"],
        )

        self._emit_progress(
            phase="orphan",
            processed=2,
            total=6,
            current="Mencari orphan SVL dan journal...",
            progress=0.34,
        )
        svl_without_journal = await self._fetch_svl_without_journal(
            company_id=request.company_id,
            context=context,
            date_from=date_from,
            date_to=date_to,
            svl_date_field=capabilities["svl_date_field"],
            link_supported=capabilities["svl_link_supported"],
            move_link_supported=capabilities["svl_move_link_supported"],
            warnings=warnings,
            move_fields=capabilities["stock_move_fields"],
        )
        journal_without_svl = await self._fetch_journal_without_svl(
            company_id=request.company_id,
            context=context,
            date_from=date_from,
            date_to=date_to,
            valuation_account_ids=valuation_account_ids,
            aml_fields=capabilities["aml_fields"],
            link_supported=capabilities["svl_link_supported"],
            warnings=warnings,
        )

        mismatch_product_ids: set[int] = set()
        for pid in set(svl_totals) | set(journal_totals):
            if abs(_round2(svl_totals.get(pid, 0.0) - journal_totals.get(pid, 0.0))) >= 0.01:
                mismatch_product_ids.add(pid)

        orphan_product_ids = {
            record.product_id
            for record in svl_without_journal + journal_without_svl
            if record.product_id > 0
        }
        issue_product_ids = sorted(mismatch_product_ids | orphan_product_ids)

        self._emit_progress(
            phase="detail",
            processed=3,
            total=6,
            current="Mengambil detail produk...",
            progress=0.56,
        )
        product_info, svl_detail, journal_detail, unassigned_journal_records = await asyncio.gather(
            self._fetch_product_info(
                issue_product_ids,
                company_id=request.company_id,
                context=context,
                category_fields=capabilities["category_fields"],
            ),
            self._fetch_svl_detail(
                product_ids=issue_product_ids,
                company_id=request.company_id,
                context=context,
                date_from=date_from,
                date_to=date_to,
                svl_date_field=capabilities["svl_date_field"],
                link_supported=capabilities["svl_link_supported"],
                move_link_supported=capabilities["svl_move_link_supported"],
                warnings=warnings,
                move_fields=capabilities["stock_move_fields"],
            ),
            self._fetch_journal_detail(
                product_ids=issue_product_ids,
                company_id=request.company_id,
                context=context,
                date_from=date_from,
                date_to=date_to,
                valuation_account_ids=valuation_account_ids,
                aml_fields=capabilities["aml_fields"],
                link_supported=capabilities["svl_link_supported"],
                warnings=warnings,
            ),
            self._fetch_unassigned_journal_detail(
                company_id=request.company_id,
                context=context,
                date_from=date_from,
                date_to=date_to,
                valuation_account_ids=valuation_account_ids,
                aml_fields=capabilities["aml_fields"],
                link_supported=capabilities["svl_link_supported"],
                warnings=warnings,
            ),
        )
        self._log(
            f"Journal detail loaded: {sum(len(rows) for rows in journal_detail.values())} assigned row(s), "
            f"{len(unassigned_journal_records)} unassigned row(s)."
        )

        self._emit_progress(
            phase="merge",
            processed=4,
            total=6,
            current="Menyusun ringkasan item dashboard...",
            progress=0.74,
        )
        linked_move_ids = sorted(
            {
                record.move_id
                for rows in svl_detail.values()
                for record in rows
                if record.move_id > 0
            }
            | {
                record.move_id
                for rows in journal_detail.values()
                for record in rows
                if record.move_id > 0
            }
            | {
                record.move_id
                for record in unassigned_journal_records
                if record.move_id > 0
            }
        )
        move_line_summary = await self._fetch_move_line_summary(
            move_ids=linked_move_ids,
            context=context,
            aml_fields=capabilities["aml_fields"],
        )

        items: list[SvlDashboardItem] = []
        for pid in issue_product_ids:
            info = product_info.get(pid, {})
            svl_records = svl_detail.get(pid, [])
            jnl_records = journal_detail.get(pid, [])
            merged_records = self._build_merged_records(
                product_id=pid,
                svl_records=svl_records,
                journal_records=jnl_records,
                move_line_summary=move_line_summary,
            )
            svl_orphan_count = sum(1 for record in svl_records if not record.has_journal)
            svl_orphan_value = _round2(sum(record.value for record in svl_records if not record.has_journal))
            jnl_orphan_count = sum(1 for record in jnl_records if not record.has_svl)
            jnl_orphan_value = _round2(sum(record.net for record in jnl_records if not record.has_svl))
            linked_empty_journal_rows = [record for record in merged_records if record.row_type == "svl_linked_empty_move"]
            item_warnings: list[str] = []
            if linked_empty_journal_rows:
                linked_refs = ", ".join(
                    sorted(
                        {
                            record.move_name
                            for record in linked_empty_journal_rows
                            if normalize_text(record.move_name)
                        }
                    )
                )
                message = "Linked JE header tidak punya line valuasi."
                if linked_refs:
                    message = f"{message} {linked_refs}"
                item_warnings.append(message)
            svl_value = _round2(svl_totals.get(pid, 0.0))
            bs_value = _round2(journal_totals.get(pid, 0.0))
            difference = _round2(svl_value - bs_value)
            items.append(
                SvlDashboardItem(
                    pid=pid,
                    code=normalize_text(info.get("code")),
                    name=normalize_text(info.get("name")) or f"Product #{pid}",
                    categ=normalize_text(info.get("categ")),
                    cost_method=normalize_text(info.get("cost_method")),
                    standard_price=_round2(info.get("standard_price")),
                    svl_value=svl_value,
                    bs_value=bs_value,
                    difference=difference,
                    position="DEBIT" if bs_value >= 0 else "KREDIT",
                    svl_orphan_count=svl_orphan_count,
                    svl_orphan_value=svl_orphan_value,
                    jnl_orphan_count=jnl_orphan_count,
                    jnl_orphan_value=jnl_orphan_value,
                    linked_empty_journal_count=len(linked_empty_journal_rows),
                    linked_empty_journal_value=_round2(sum(record.svl_value for record in linked_empty_journal_rows)),
                    svl_record_count=len(svl_records),
                    jnl_record_count=len(jnl_records),
                    svl_records=list(svl_records),
                    jnl_records=list(jnl_records),
                    merged_records=merged_records,
                    repair_account_candidates=list(info.get("repair_account_candidates") or []),
                    automated_valuation=bool(info.get("automated_valuation", True)),
                    po_line_count=0,
                    bill_line_count=0,
                    po_lines=[],
                    bill_lines=[],
                    total_po_value=0.0,
                    total_bill_value=0.0,
                    item_kind="product",
                    warnings=item_warnings,
                )
            )
        if unassigned_journal_records:
            unassigned_merged_records = [
                self._build_merged_record_from_journal(
                    record,
                    row_type="journal_unassigned",
                    status="Journal tanpa product",
                    match_basis="move_link",
                    note="Journal valuation berada di akun persediaan tetapi product_id kosong.",
                    repair_candidate=False,
                )
                for record in unassigned_journal_records
            ]
            unassigned_bs_value = _round2(sum(record.net for record in unassigned_journal_records))
            unassigned_orphan_value = _round2(sum(record.net for record in unassigned_journal_records if not record.has_svl))
            items.append(
                SvlDashboardItem(
                    pid=_UNASSIGNED_JOURNAL_PID,
                    code="UNASSIGNED-JNL",
                    name="Journal Valuation Tanpa Product",
                    categ="VALUATION AML",
                    cost_method="",
                    standard_price=0.0,
                    svl_value=0.0,
                    bs_value=unassigned_bs_value,
                    difference=_round2(0.0 - unassigned_bs_value),
                    position="DEBIT" if unassigned_bs_value >= 0 else "KREDIT",
                    svl_orphan_count=0,
                    svl_orphan_value=0.0,
                    jnl_orphan_count=sum(1 for record in unassigned_journal_records if not record.has_svl),
                    jnl_orphan_value=unassigned_orphan_value,
                    linked_empty_journal_count=0,
                    linked_empty_journal_value=0.0,
                    svl_record_count=0,
                    jnl_record_count=len(unassigned_journal_records),
                    svl_records=[],
                    jnl_records=list(unassigned_journal_records),
                    merged_records=unassigned_merged_records,
                    repair_account_candidates=[],
                    po_line_count=0,
                    bill_line_count=0,
                    po_lines=[],
                    bill_lines=[],
                    total_po_value=0.0,
                    total_bill_value=0.0,
                    item_kind="unassigned_journal",
                    warnings=[],
                )
            )
        items.sort(key=lambda item: abs(item.difference), reverse=True)
        company_summary.problematic_items_total_value = _round2(sum(item.difference for item in items))
        company_summary.unmapped_difference = _round2(company_summary.difference - company_summary.problematic_items_total_value)

        self._emit_progress(
            phase="finalize",
            processed=6,
            total=6,
            current="Menyusun ringkasan dashboard...",
            progress=0.9,
        )
        self._log(f"Analisis selesai. {len(items)} item bermasalah.")
        return SvlDashboardSnapshot(
            database=request.database,
            company_id=request.company_id,
            company_name=company_name,
            generated_at=datetime.now().isoformat(timespec="seconds"),
            period=f"{date_from or 'Awal'} - {date_to or 'Sekarang'}",
            dataset_mode=dataset_mode,
            valuation_account_ids=valuation_account_ids,
            company_summary=company_summary,
            warnings=warnings,
            items=items,
        )


    async def _search_read_in_chunks(
        self,
        model: str,
        *,
        ids_field: str,
        ids: list[int],
        fields: list[str],
        context: dict[str, Any],
        stage: str,
        base_domain: list[Any] | None = None,
        order: str | None = None,
    ) -> list[dict[str, Any]]:
        normalized_ids = sorted({int(value or 0) for value in ids if int(value or 0) > 0})
        if not normalized_ids:
            return []
        chunk_tasks: list[asyncio.Future[list[dict[str, Any]]] | asyncio.Task[list[dict[str, Any]]] | Any] = []
        for index, chunk in enumerate(chunked(normalized_ids, 200), start=1):
            domain = list(base_domain or [])
            domain.append((ids_field, "in", list(chunk)))
            chunk_tasks.append(
                self.rpc.search_read(
                    model,
                    domain,
                    fields=fields,
                    order=order or "",
                    context=context,
                    stage=f"{stage}_{index}",
                )
            )
        rows: list[dict[str, Any]] = []
        for batch in await asyncio.gather(*chunk_tasks):
            rows.extend(batch)
        return rows

    async def _read_in_chunks(
        self,
        model: str,
        *,
        ids: list[int],
        fields: list[str],
        context: dict[str, Any],
        stage: str,
        chunk_size: int = 500,
        continue_on_error: bool = False,
    ) -> list[dict[str, Any]]:
        normalized_ids = sorted({int(value or 0) for value in ids if int(value or 0) > 0})
        if not normalized_ids:
            return []
        chunk_tasks: list[asyncio.Future[list[dict[str, Any]]] | asyncio.Task[list[dict[str, Any]]] | Any] = []
        safe_chunk_size = max(1, int(chunk_size or 500))
        for index, chunk in enumerate(chunked(normalized_ids, safe_chunk_size), start=1):
            chunk_tasks.append(
                self.rpc.read(
                    model,
                    list(chunk),
                    fields=fields,
                    context=context,
                    stage=f"{stage}_{index}",
                )
            )
        rows: list[dict[str, Any]] = []
        for batch in await asyncio.gather(*chunk_tasks, return_exceptions=continue_on_error):
            if isinstance(batch, Exception):
                continue
            rows.extend(batch)
        return rows

    @staticmethod
    def _build_pcb_stock_move_indexes(
        stock_move_rows_by_id: dict[int, dict[str, Any]] | None,
    ) -> dict[str, dict[int, Any]]:
        stock_move_rows_by_picking_id: dict[int, list[dict[str, Any]]] = defaultdict(list)
        stock_move_ids_by_picking_id: dict[int, set[int]] = defaultdict(set)
        purchase_line_ids_by_picking_id: dict[int, set[int]] = defaultdict(set)
        for stock_move_row in dict(stock_move_rows_by_id or {}).values():
            picking_id = _many2one_id(stock_move_row.get("picking_id"))
            if picking_id <= 0:
                continue
            stock_move_rows_by_picking_id[picking_id].append(stock_move_row)
            stock_move_id = int(stock_move_row.get("id") or 0)
            if stock_move_id > 0:
                stock_move_ids_by_picking_id[picking_id].add(stock_move_id)
            purchase_line_id = _many2one_id(stock_move_row.get("purchase_line_id"))
            if purchase_line_id > 0:
                purchase_line_ids_by_picking_id[picking_id].add(purchase_line_id)
        return {
            "stock_move_rows_by_picking_id": dict(stock_move_rows_by_picking_id),
            "stock_move_ids_by_picking_id": {
                int(picking_id): {int(value) for value in values if int(value or 0) > 0}
                for picking_id, values in stock_move_ids_by_picking_id.items()
                if int(picking_id or 0) > 0
            },
            "purchase_line_ids_by_picking_id": {
                int(picking_id): {int(value) for value in values if int(value or 0) > 0}
                for picking_id, values in purchase_line_ids_by_picking_id.items()
                if int(picking_id or 0) > 0
            },
        }

    @classmethod
    def _get_pcb_stock_move_indexes(
        cls,
        trace: dict[str, Any],
    ) -> dict[str, dict[int, Any]]:
        stock_move_rows_by_picking_id = {
            int(picking_id): [
                row
                for row in list(rows or [])
                if isinstance(row, dict)
            ]
            for picking_id, rows in dict(trace.get("stock_move_rows_by_picking_id") or {}).items()
            if int(picking_id or 0) > 0
        }
        stock_move_ids_by_picking_id = {
            int(picking_id): {
                int(value or 0)
                for value in list(values or [])
                if int(value or 0) > 0
            }
            for picking_id, values in dict(trace.get("stock_move_ids_by_picking_id") or {}).items()
            if int(picking_id or 0) > 0
        }
        purchase_line_ids_by_picking_id = {
            int(picking_id): {
                int(value or 0)
                for value in list(values or [])
                if int(value or 0) > 0
            }
            for picking_id, values in dict(trace.get("purchase_line_ids_by_picking_id") or {}).items()
            if int(picking_id or 0) > 0
        }
        if stock_move_rows_by_picking_id or stock_move_ids_by_picking_id or purchase_line_ids_by_picking_id:
            return {
                "stock_move_rows_by_picking_id": stock_move_rows_by_picking_id,
                "stock_move_ids_by_picking_id": stock_move_ids_by_picking_id,
                "purchase_line_ids_by_picking_id": purchase_line_ids_by_picking_id,
            }
        return cls._build_pcb_stock_move_indexes(
            {
                int(key): dict(value or {})
                for key, value in dict(trace.get("stock_move_rows_by_id") or {}).items()
                if int(key or 0) > 0
            }
        )

    async def _account_balance_invoice_qty_field(self) -> str:
        fields = await self._fields_get_cached("purchase.order.line")
        if not fields:
            return "qty_invoiced"
        if "qty_invoiced" in fields:
            return "qty_invoiced"
        if "qty_billed" in fields:
            return "qty_billed"
        return "qty_invoiced"

    @staticmethod
    def _pcb_payment_link_field_order(payment_meta: dict[str, Any]) -> list[str]:
        # In hwgroup_erp, account.payment.reconciled_bill_ids is visible in fields_get
        # but cannot be used in a search domain. Prefer the cheaper invoice_ids path
        # when it is available, and only fall back to reconciled_bill_ids on older schemas.
        if "invoice_ids" in payment_meta:
            return ["invoice_ids"]
        if "reconciled_bill_ids" in payment_meta:
            return ["reconciled_bill_ids"]
        return []

    async def _collect_account_balance_trace(
        self,
        *,
        request: SvlDashboardRequest,
        context: dict[str, Any],
        date_from: str,
        date_to: str,
        product_ids: list[int] | None,
        warnings: list[str],
    ) -> dict[str, Any]:
        return await self._collect_account_balance_trace_gr_cycle(
            request=request,
            context=context,
            date_from=date_from,
            date_to=date_to,
            product_ids=product_ids,
            warnings=warnings,
        )

    async def _collect_account_balance_trace_gr_cycle(
        self,
        *,
        request: SvlDashboardRequest,
        context: dict[str, Any],
        date_from: str,
        date_to: str,
        product_ids: list[int] | None,
        warnings: list[str],
    ) -> dict[str, Any]:
        _t0 = perf_counter()
        stock_move_line_meta = await self._fields_get_cached("stock.move.line")
        stock_move_meta = await self._fields_get_cached("stock.move")
        seed_fields = list(
            dict.fromkeys(
                [
                    "id",
                    *await self._supported_fields(
                        "stock.move.line",
                        ["move_id", "product_id", "date", "reference", "picking_code", "picking_id", "qty_done", "company_id", "inventory_type"],
                    ),
                ]
            )
        )
        seed_domain: list[Any] = []
        if "company_id" in stock_move_line_meta:
            seed_domain.append(("company_id", "=", request.company_id))
        if "picking_code" in stock_move_line_meta:
            seed_domain.append(("picking_code", "=", "incoming"))
        if "move_id" in stock_move_line_meta:
            seed_domain.append(("move_id", "!=", False))
        if product_ids and "product_id" in stock_move_line_meta:
            seed_domain.append(("product_id", "in", list(product_ids)))
        if date_from and "date" in stock_move_line_meta:
            seed_domain.append(("date", ">=", date_from))
        if date_to and "date" in stock_move_line_meta:
            seed_domain.append(("date", "<=", date_to))
        seed_rows = await self.rpc.search_read(
            "stock.move.line",
            seed_domain,
            fields=seed_fields,
            order="date,id",
            context=context,
            stage="SVL_DASH_ACCOUNT_BALANCE_GR_SEED",
        )
        seed_rows = [row for row in seed_rows if _many2one_id(row.get("move_id")) > 0]
        self._log(f"[BENCH]   trace/seed_sml: {(perf_counter() - _t0) * 1000.0:.0f}ms | {len(seed_rows)} rows")
        _t0 = perf_counter()

        stock_move_fields = list(
            dict.fromkeys(
                [
                    "id",
                    *await self._supported_fields(
                        "stock.move",
                        [
                            "product_id",
                            "date",
                            "reference",
                            "origin",
                            "picking_id",
                            "purchase_line_id",
                            "account_move_ids",
                            "price_unit",
                            "product_qty",
                            "quantity",
                            "product_uom",
                            "product_uom_id",
                            "state",
                            "company_id",
                            "origin_returned_move_id",
                            "returned_move_ids",
                        ],
                    ),
                ]
            )
        )
        stock_move_rows_by_id: dict[int, dict[str, Any]] = {}
        stock_move_ids = sorted({_many2one_id(row.get("move_id")) for row in seed_rows if _many2one_id(row.get("move_id")) > 0})
        if stock_move_ids:
            for row in await self._search_read_in_chunks(
                "stock.move",
                ids_field="id",
                ids=stock_move_ids,
                fields=stock_move_fields,
                context=context,
                stage="SVL_DASH_ACCOUNT_BALANCE_STOCK_MOVE_SEED",
            ):
                row_id = int(row.get("id") or 0)
                if row_id > 0:
                    stock_move_rows_by_id[row_id] = row

        stock_move_rows = list(stock_move_rows_by_id.values())
        self._log(
            f"[BENCH]   trace/seed_sm: {(perf_counter() - _t0) * 1000.0:.0f}ms | {len(stock_move_rows)} rows"
        )
        self._log(f"Seed stock.move.line incoming saldo akun: {len(seed_rows)} row(s), {len(stock_move_rows)} stock.move row(s).")
        if not stock_move_rows:
            self._warn_once(
                warnings,
                "Tidak ada stock.move.line receipt incoming yang cocok untuk company/periode ini.",
            )
            return {
                "product_ids": [],
                "po_line_rows_by_product": {},
                "bill_line_rows_by_product": {},
                "bill_rows_by_id": {},
                "payment_rows_by_bill_id": {},
                "relevant_move_ids": [],
                "invoice_qty_field": "qty_invoiced",
            }

        traced_product_ids = sorted(
            {
                _many2one_id(row.get("product_id"))
                for row in stock_move_rows
                if _many2one_id(row.get("product_id")) > 0
            }
        )
        stock_move_ids = sorted(stock_move_rows_by_id)
        stj_move_ids_by_product: dict[int, set[int]] = defaultdict(set)
        purchase_line_ids: set[int] = set()
        purchase_order_ids_by_product: dict[int, set[int]] = defaultdict(set)
        picking_ids: set[int] = set()
        # picking-level maps for Purchase Cycle Balance mode
        stj_ids_by_picking: dict[int, set[int]] = defaultdict(set)
        product_ids_by_picking: dict[int, set[int]] = defaultdict(set)
        inventory_types_by_picking: dict[int, set[str]] = defaultdict(set)

        for seed_row in seed_rows:
            picking_id_seed = _many2one_id(seed_row.get("picking_id"))
            inventory_type = normalize_text(seed_row.get("inventory_type")).strip().lower()
            if picking_id_seed > 0 and inventory_type:
                inventory_types_by_picking[picking_id_seed].add(inventory_type)

        for row in stock_move_rows:
            product_id = _many2one_id(row.get("product_id"))
            if product_id <= 0:
                continue
            picking_id = _many2one_id(row.get("picking_id"))
            for move_id in _many2many_ids(row.get("account_move_ids")):
                stj_move_ids_by_product[product_id].add(move_id)
                if picking_id > 0:
                    stj_ids_by_picking[picking_id].add(move_id)
            purchase_line_id = _many2one_id(row.get("purchase_line_id"))
            if purchase_line_id > 0:
                purchase_line_ids.add(purchase_line_id)
            if picking_id > 0:
                picking_ids.add(picking_id)
                product_ids_by_picking[picking_id].add(product_id)

        _t0 = perf_counter()
        account_move_meta = await self._fields_get_cached("account.move")
        stj_move_fields = list(
            dict.fromkeys(
                ["id", *await self._supported_fields("account.move", ["name", "stock_move_id", "partner_id", "move_type"])]
            )
        )
        picking_fields = (
            list(
                dict.fromkeys(
                    [
                        "id",
                        *await self._supported_fields(
                            "stock.picking",
                            ["name", "origin", "purchase_id", "partner_id", "scheduled_date", "date_done"],
                        ),
                    ]
                )
            )
            if picking_ids
            else []
        )
        move_rows, svl_am_rows, picking_rows = await asyncio.gather(
            self.rpc.search_read(
                "account.move",
                [("stock_move_id", "in", stock_move_ids)],
                fields=stj_move_fields,
                context=context,
                stage="SVL_DASH_ACCOUNT_BALANCE_STJ_FALLBACK",
            )
            if stock_move_ids and "stock_move_id" in account_move_meta
            else asyncio.sleep(0, result=[]),
            self.rpc.search_read(
                "stock.valuation.layer",
                [("stock_move_id", "in", stock_move_ids)],
                fields=["id", "stock_move_id", "account_move_id"],
                context=context,
                stage="SVL_DASH_PCB_STJ_SVL_PATH",
            )
            if stock_move_ids
            else asyncio.sleep(0, result=[]),
            self._search_read_in_chunks(
                "stock.picking",
                ids_field="id",
                ids=list(picking_ids),
                fields=picking_fields,
                context=context,
                stage="SVL_DASH_ACCOUNT_BALANCE_PICKINGS",
            )
            if picking_ids
            else asyncio.sleep(0, result=[]),
        )
        for row in move_rows:
            move_id = int(row.get("id") or 0)
            stock_move_id = _many2one_id(row.get("stock_move_id"))
            if move_id <= 0 or stock_move_id <= 0:
                continue
            sm_row = stock_move_rows_by_id.get(stock_move_id) or {}
            product_id = _many2one_id(sm_row.get("product_id"))
            if product_id > 0:
                stj_move_ids_by_product[product_id].add(move_id)
                # Also update picking-level map so PCB mode captures these STJ lines
                picking_id_fb = _many2one_id(sm_row.get("picking_id"))
                if picking_id_fb > 0:
                    stj_ids_by_picking[picking_id_fb].add(move_id)

        # Path 3: SVL linkage — stock.move → stock.valuation.layer.account_move_id
        # Covers cases where account.move.stock_move_id is False (correction/relinked STJ)
        # but SVL still points from the stock_move to the correct account.move.
        for svl_row in svl_am_rows:
            am_id = _many2one_id(svl_row.get("account_move_id"))
            sm_id = _many2one_id(svl_row.get("stock_move_id"))
            if am_id <= 0 or sm_id <= 0:
                continue
            sm_row = stock_move_rows_by_id.get(sm_id) or {}
            product_id = _many2one_id(sm_row.get("product_id"))
            if product_id > 0:
                stj_move_ids_by_product[product_id].add(am_id)
            picking_id_svl = _many2one_id(sm_row.get("picking_id"))
            if picking_id_svl > 0:
                stj_ids_by_picking[picking_id_svl].add(am_id)

        picking_rows_by_id: dict[int, dict[str, Any]] = {}
        self._log(
            f"[BENCH]   trace/stj_svl_picking: {(perf_counter() - _t0) * 1000.0:.0f}ms | "
            f"{len(move_rows)} stj | {len(svl_am_rows)} svl | {len(picking_rows)} pickings"
        )
        if picking_rows:
            picking_rows_by_id = {
                int(row.get("id") or 0): row
                for row in picking_rows
                if int(row.get("id") or 0) > 0
            }
            # picking → PO map (for picking-level cycle grouping)
            po_id_by_picking: dict[int, int] = {}
            for row in stock_move_rows:
                product_id = _many2one_id(row.get("product_id"))
                picking_id = _many2one_id(row.get("picking_id"))
                purchase_id = _many2one_id((picking_rows_by_id.get(picking_id) or {}).get("purchase_id"))
                if product_id > 0 and purchase_id > 0:
                    purchase_order_ids_by_product[product_id].add(purchase_id)
                if picking_id > 0 and purchase_id > 0:
                    po_id_by_picking[picking_id] = purchase_id

            # ── Return picking linkage ────────────────────────────────────────
            # If a stock.move has origin_returned_move_id pointing to a move whose
            # picking is already in our set, the return picking must join that same
            # cycle. Collect (return_picking_id, original_picking_id) pairs and
            # also fetch any return pickings not yet in picking_rows_by_id.
            return_picking_pairs: list[tuple[int, int]] = []  # (return_pid, orig_pid)
            return_picking_ids_needed: set[int] = set()
            # return moves whose picking_id can't be resolved from cache
            # (outgoing moves not fetched by incoming-only seed phase)
            _unresolved_ret: dict[int, int] = {}  # ret_move_id -> orig_picking_id
            for row in stock_move_rows:
                orig_move_id = _many2one_id(row.get("origin_returned_move_id"))
                return_picking_id = _many2one_id(row.get("picking_id"))
                if orig_move_id > 0 and return_picking_id > 0:
                    orig_sm_row = stock_move_rows_by_id.get(orig_move_id) or {}
                    orig_picking_id = _many2one_id(orig_sm_row.get("picking_id"))
                    if orig_picking_id > 0 and orig_picking_id in picking_rows_by_id:
                        return_picking_pairs.append((return_picking_id, orig_picking_id))
                        if return_picking_id not in picking_rows_by_id:
                            return_picking_ids_needed.add(return_picking_id)
                # Also handle the reverse: returned_move_ids on the original move
                for ret_move_id in _many2many_ids(row.get("returned_move_ids")):
                    orig_picking_id2 = _many2one_id(row.get("picking_id"))
                    if orig_picking_id2 <= 0 or orig_picking_id2 not in picking_rows_by_id:
                        continue
                    ret_sm_row = stock_move_rows_by_id.get(ret_move_id) or {}
                    ret_picking_id = _many2one_id(ret_sm_row.get("picking_id"))
                    if ret_picking_id > 0:
                        return_picking_pairs.append((ret_picking_id, orig_picking_id2))
                        if ret_picking_id not in picking_rows_by_id:
                            return_picking_ids_needed.add(ret_picking_id)
                    elif ret_move_id > 0:
                        # Outgoing return move not in cache — defer to batch fetch below
                        _unresolved_ret[ret_move_id] = orig_picking_id2

            # ── Resolve returned_move_ids whose moves weren't in the seed cache ─────
            # Seed phase only fetches incoming stock.move.line rows, so return-to-vendor
            # moves (outgoing) are absent from stock_move_rows_by_id.  Batch-fetch here.
            if _unresolved_ret:
                _resolve_rows = await self._search_read_in_chunks(
                    "stock.move",
                    ids_field="id",
                    ids=sorted(_unresolved_ret),
                    fields=["id", "picking_id"],
                    context=context,
                    stage="SVL_DASH_PCB_RETURN_MOVE_RESOLVE",
                )
                for _rr in _resolve_rows:
                    _rr_id = int(_rr.get("id") or 0)
                    _rr_pk = _many2one_id(_rr.get("picking_id"))
                    _orig_pk = _unresolved_ret.get(_rr_id, 0)
                    if _rr_pk > 0 and _orig_pk > 0:
                        return_picking_pairs.append((_rr_pk, _orig_pk))
                        if _rr_pk not in picking_rows_by_id:
                            return_picking_ids_needed.add(_rr_pk)

            if return_picking_ids_needed:
                ret_picking_rows = await self._search_read_in_chunks(
                    "stock.picking",
                    ids_field="id",
                    ids=list(return_picking_ids_needed),
                    fields=picking_fields,
                    context=context,
                    stage="SVL_DASH_PCB_RETURN_PICKINGS",
                )
                for rp_row in ret_picking_rows:
                    rp_id = int(rp_row.get("id") or 0)
                    if rp_id > 0:
                        picking_rows_by_id[rp_id] = rp_row

            # For return pickings, also collect their stock.moves so STJs are captured
            if return_picking_ids_needed:
                ret_move_rows = await self.rpc.search_read(
                    "stock.move",
                    [("picking_id", "in", list(return_picking_ids_needed)), ("state", "=", "done")],
                    fields=stock_move_fields,
                    context=context,
                    stage="SVL_DASH_PCB_RETURN_MOVES",
                )
                for rm_row in ret_move_rows:
                    rm_id = int(rm_row.get("id") or 0)
                    if rm_id > 0:
                        stock_move_rows_by_id[rm_id] = rm_row
                    rp_id = _many2one_id(rm_row.get("picking_id"))
                    rp_product_id = _many2one_id(rm_row.get("product_id"))
                    for am_id in _many2many_ids(rm_row.get("account_move_ids")):
                        if rp_id > 0:
                            stj_ids_by_picking[rp_id].add(am_id)
                        if rp_product_id > 0:
                            stj_move_ids_by_product[rp_product_id].add(am_id)
                    if rp_id > 0 and rp_product_id > 0:
                        product_ids_by_picking[rp_id].add(rp_product_id)
            # Set of all return picking IDs — used in BFS guard to allow their STJs
            _return_picking_ids: set[int] = {ret_pid for ret_pid, _ in return_picking_pairs}

            # Fetch res.partner ref_company_ids for all partner_ids in picking set.
            # ref_company_ids != [] means the partner represents an internal HW Group company.
            _all_partner_ids: set[int] = {
                _many2one_id(row.get("partner_id"))
                for row in picking_rows_by_id.values()
                if _many2one_id(row.get("partner_id")) > 0
            }
            _intercompany_partner_ids: frozenset[int] = frozenset()
            if _all_partner_ids:
                try:
                    _partner_ref_rows = await self.rpc.search_read(
                        "res.partner",
                        [("id", "in", list(_all_partner_ids))],
                        fields=["id", "ref_company_ids"],
                        stage="SVL_DASH_PCB_PARTNER_INTERCO",
                    )
                    _intercompany_partner_ids = frozenset(
                        int(row.get("id") or 0)
                        for row in _partner_ref_rows
                        if _many2many_ids(row.get("ref_company_ids"))
                    )
                except Exception:
                    pass  # non-fatal; treat all as external if fetch fails
        else:
            po_id_by_picking = {}
            return_picking_pairs = []
            _return_picking_ids: set[int] = set()
            _intercompany_partner_ids: frozenset[int] = frozenset()

        _t0 = perf_counter()
        invoice_qty_field = await self._account_balance_invoice_qty_field()
        po_line_fields = list(
            dict.fromkeys(
                [
                    "id",
                    *await self._supported_fields(
                        "purchase.order.line",
                        [
                            "order_id",
                            "product_id",
                            "price_unit",
                            "product_qty",
                            "product_uom",
                            "product_uom_id",
                            "price_subtotal",
                            "qty_received",
                            invoice_qty_field,
                        ],
                    ),
                ]
            )
        )
        po_line_rows_by_id: dict[int, dict[str, Any]] = {}
        fallback_purchase_order_ids = sorted({purchase_id for ids in purchase_order_ids_by_product.values() for purchase_id in ids})
        direct_po_line_rows, fallback_po_line_rows = await asyncio.gather(
            self._search_read_in_chunks(
                "purchase.order.line",
                ids_field="id",
                ids=list(purchase_line_ids),
                fields=po_line_fields,
                context=context,
                stage="SVL_DASH_ACCOUNT_BALANCE_PO_LINES_DIRECT",
            )
            if purchase_line_ids
            else asyncio.sleep(0, result=[]),
            self.rpc.search_read(
                "purchase.order.line",
                [("order_id", "in", fallback_purchase_order_ids), ("product_id", "in", traced_product_ids)],
                fields=po_line_fields,
                order="product_id,id",
                context=context,
                stage="SVL_DASH_ACCOUNT_BALANCE_PO_LINES_FALLBACK",
            )
            if fallback_purchase_order_ids
            else asyncio.sleep(0, result=[]),
        )
        for row in [*direct_po_line_rows, *fallback_po_line_rows]:
            row_id = int(row.get("id") or 0)
            if row_id > 0:
                po_line_rows_by_id.setdefault(row_id, row)

        po_line_rows_by_product: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for row in po_line_rows_by_id.values():
            product_id = _many2one_id(row.get("product_id"))
            if product_id <= 0:
                continue
            po_line_rows_by_product[product_id].append(row)
            purchase_order_id = _many2one_id(row.get("order_id"))
            if purchase_order_id > 0:
                purchase_order_ids_by_product[product_id].add(purchase_order_id)
        for rows in po_line_rows_by_product.values():
            rows.sort(key=lambda current: (normalize_text(_many2one_name(current.get("order_id"))), int(current.get("id") or 0)))

        purchase_order_ids = sorted({purchase_id for ids in purchase_order_ids_by_product.values() for purchase_id in ids})
        purchase_order_fields = list(dict.fromkeys(["id", *await self._supported_fields("purchase.order", ["name", "invoice_ids", "invoice_status", "state"])]))
        purchase_order_rows_by_id: dict[int, dict[str, Any]] = {}
        if purchase_order_ids:
            purchase_order_rows = await self._search_read_in_chunks(
                "purchase.order",
                ids_field="id",
                ids=purchase_order_ids,
                fields=purchase_order_fields,
                context=context,
                stage="SVL_DASH_ACCOUNT_BALANCE_PURCHASE_ORDERS",
            )
            purchase_order_rows_by_id = {
                int(row.get("id") or 0): row
                for row in purchase_order_rows
                if int(row.get("id") or 0) > 0
            }

        purchase_order_name_by_id = {
            order_id: normalize_text(row.get("name"))
            for order_id, row in purchase_order_rows_by_id.items()
            if int(order_id or 0) > 0 and normalize_text(row.get("name"))
        }
        product_ids_by_po_name: dict[str, set[int]] = defaultdict(set)
        po_names: list[str] = []
        for product_id, order_ids in purchase_order_ids_by_product.items():
            for order_id in order_ids:
                po_name = purchase_order_name_by_id.get(order_id, "")
                if not po_name:
                    continue
                product_ids_by_po_name[po_name].add(product_id)
        if product_ids_by_po_name:
            po_names = sorted(product_ids_by_po_name)
        bill_ids_by_product: dict[int, set[int]] = defaultdict(set)
        bill_ids: set[int] = set()
        for product_id, order_ids in purchase_order_ids_by_product.items():
            for order_id in order_ids:
                current_bill_ids = _many2many_ids((purchase_order_rows_by_id.get(order_id) or {}).get("invoice_ids"))
                if not current_bill_ids:
                    continue
                bill_ids.update(current_bill_ids)
                bill_ids_by_product[product_id].update(current_bill_ids)

        bill_fields = list(
            dict.fromkeys(
                [
                    "id",
                    *await self._supported_fields(
                        "account.move",
                        ["name", "date", "invoice_date", "invoice_origin", "move_type", "amount_total", "amount_residual", "payment_state", "state", "ref", "partner_id"],
                    ),
                ]
            )
        )
        bill_rows_by_id: dict[int, dict[str, Any]] = {}
        fallback_bill_domain: list[Any] = []
        if po_names and "invoice_origin" in account_move_meta:
            fallback_bill_domain = [("invoice_origin", "in", po_names)]
            if "move_type" in account_move_meta:
                fallback_bill_domain.append(("move_type", "in", ["in_invoice", "in_refund"]))
        bill_rows_direct, bill_rows_fallback = await asyncio.gather(
            self._search_read_in_chunks(
                "account.move",
                ids_field="id",
                ids=list(bill_ids),
                fields=bill_fields,
                context=context,
                stage="SVL_DASH_ACCOUNT_BALANCE_BILLS_BY_ID",
            )
            if bill_ids
            else asyncio.sleep(0, result=[]),
            self.rpc.search_read(
                "account.move",
                fallback_bill_domain,
                fields=bill_fields,
                order="date,id",
                context=context,
                stage="SVL_DASH_ACCOUNT_BALANCE_BILLS_FALLBACK",
            )
            if fallback_bill_domain
            else asyncio.sleep(0, result=[]),
        )
        for row in bill_rows_direct:
            bill_id = int(row.get("id") or 0)
            if bill_id <= 0 or not self._is_vendor_bill_move_type(row.get("move_type")):
                continue
            bill_rows_by_id[bill_id] = row
        for row in bill_rows_fallback:
            bill_id = int(row.get("id") or 0)
            if bill_id <= 0 or not self._is_vendor_bill_move_type(row.get("move_type")):
                continue
            bill_rows_by_id.setdefault(bill_id, row)
            origin_name = normalize_text(row.get("invoice_origin"))
            if not origin_name:
                continue
            for product_id in product_ids_by_po_name.get(origin_name, set()):
                bill_ids_by_product[product_id].add(bill_id)

        bill_ids = set(bill_rows_by_id)
        for product_id, product_bill_ids in list(bill_ids_by_product.items()):
            filtered_bill_ids = {
                int(bill_id or 0)
                for bill_id in list(product_bill_ids or [])
                if int(bill_id or 0) in bill_ids
            }
            if filtered_bill_ids:
                bill_ids_by_product[product_id] = filtered_bill_ids
            else:
                bill_ids_by_product.pop(product_id, None)
        self._log(
            f"[BENCH]   trace/po_bills: {(perf_counter() - _t0) * 1000.0:.0f}ms | "
            f"{len(po_line_rows_by_id)} po_lines | {len(purchase_order_rows_by_id)} orders | {len(bill_rows_by_id)} bills"
        )
        _t0 = perf_counter()
        aml_fields = await self._fields_get_cached("account.move.line")
        bill_line_fields = list(
            dict.fromkeys(
                [
                    "id",
                    *await self._supported_fields(
                        "account.move.line",
                        [
                            "move_id",
                            "product_id",
                            "product_uom_id",
                            "purchase_line_id",
                            "balance",
                            "price_unit",
                            "quantity",
                            "price_subtotal",
                            "currency_id",
                            "amount_currency",
                            "analytic_distribution",
                            "date",
                            "name",
                        ],
                    ),
                ]
            )
        )
        bill_line_rows_by_product: dict[int, list[dict[str, Any]]] = defaultdict(list)
        bill_line_domain: list[Any] = []
        if bill_ids:
            bill_line_domain = [("move_id", "in", sorted(bill_ids))]
            bill_line_domain.extend(self._build_posted_domain(aml_fields))
            bill_line_domain.extend(self._build_current_asset_display_type_domain(aml_fields))

        payment_rows_by_bill_id: dict[int, list[dict[str, Any]]] = defaultdict(list)
        bill_ids_by_payment_id: dict[int, set[int]] = defaultdict(set)
        payment_rows_by_id: dict[int, dict[str, Any]] = {}
        payment_move_ids_by_product: dict[int, set[int]] = defaultdict(set)
        bank_move_ids_by_product: dict[int, set[int]] = defaultdict(set)
        payment_move_ids_by_matching: dict[str, set[int]] = defaultdict(set)
        bank_move_ids_by_matching: dict[str, set[int]] = defaultdict(set)
        matching_numbers_by_payment_move_id: dict[int, set[str]] = defaultdict(set)
        matching_numbers_by_bank_move_id: dict[int, set[str]] = defaultdict(set)
        # Path: bill payable matching_number → direct BK (no intermediate account.payment)
        direct_bank_move_ids_by_bill: dict[int, set[int]] = defaultdict(set)
        direct_bills_by_bank_move_id: dict[int, set[int]] = defaultdict(set)
        # Expansion-discovered bills/STJs keyed by seed bill (avoid cross-cycle contamination)
        expansion_bills_by_bill: dict[int, set[int]] = defaultdict(set)
        expansion_stjs_by_bill: dict[int, set[int]] = defaultdict(set)
        # Expansion-discovered bills keyed by orphaned STJ seed (STJ with no known bill yet)
        expansion_bills_by_stj: dict[int, set[int]] = defaultdict(set)
        matching_account_info_map: dict[int, dict[str, Any]] = {}
        matching_move_rows_by_id: dict[int, dict[str, Any]] = {}
        matching_move_fields = list(dict.fromkeys(
            ["id", *await self._supported_fields("account.move", ["move_type", "name", "partner_id", "stock_move_id"])]
        ))
        bill_partner_key_by_move_id: dict[int, str] = {}
        stj_partner_key_by_move_id: dict[int, str] = {}
        for seed_row in [*move_rows, *bill_rows_by_id.values()]:
            seed_move_id = int(seed_row.get("id") or 0)
            if seed_move_id <= 0:
                continue
            existing_seed = matching_move_rows_by_id.get(seed_move_id) or {}
            matching_move_rows_by_id[seed_move_id] = {
                **existing_seed,
                **seed_row,
            }

        async def _ensure_matching_account_info(rows: list[dict[str, Any]]) -> None:
            missing_account_ids = sorted(
                {
                    account_id
                    for row in rows
                    for account_id in [_many2one_id(row.get("account_id"))]
                    if account_id > 0 and account_id not in matching_account_info_map
                }
            )
            if not missing_account_ids:
                return
            matching_account_info_map.update(
                await self._fetch_account_info_map(account_ids=missing_account_ids, context=context)
            )

        async def _fetch_matching_move_rows(move_ids: set[int] | list[int], *, stage: str) -> dict[int, dict[str, Any]]:
            ids_needed = sorted({int(move_id or 0) for move_id in move_ids if int(move_id or 0) > 0 and int(move_id or 0) not in matching_move_rows_by_id})
            if ids_needed:
                for row in await self._search_read_in_chunks(
                    "account.move",
                    ids_field="id",
                    ids=ids_needed,
                    fields=matching_move_fields,
                    context=context,
                    stage=stage,
                ):
                    move_id = int(row.get("id") or 0)
                    if move_id > 0:
                        matching_move_rows_by_id[move_id] = row
            return {
                int(move_id or 0): matching_move_rows_by_id.get(int(move_id or 0), {})
                for move_id in move_ids
                if int(move_id or 0) > 0
            }

        def _bill_partner_key(move_id: int) -> str:
            clean_move_id = int(move_id or 0)
            if clean_move_id <= 0:
                return ""
            if clean_move_id not in bill_partner_key_by_move_id:
                bill_partner_key_by_move_id[clean_move_id] = self._resolve_matching_partner_key_from_move_row(
                    bill_rows_by_id.get(clean_move_id),
                )
            return bill_partner_key_by_move_id[clean_move_id]

        async def _ensure_stj_partner_keys(move_ids: set[int] | list[int], *, stage: str) -> None:
            move_rows = await _fetch_matching_move_rows(move_ids, stage=stage)
            for move_id, move_row in move_rows.items():
                if move_id in stj_partner_key_by_move_id:
                    continue
                stj_partner_key_by_move_id[move_id] = self._resolve_matching_partner_key_from_move_row(
                    move_row,
                    stock_move_rows_by_id=stock_move_rows_by_id,
                    picking_rows_by_id=picking_rows_by_id,
                )

        async def _fetch_payment_rows_for_bill_ids() -> dict[int, dict[str, Any]]:
            if not bill_ids:
                return {}
            payment_rows: dict[int, dict[str, Any]] = {}
            payment_meta = await self._fields_get_cached("account.payment")
            payment_link_fields = self._pcb_payment_link_field_order(payment_meta)
            payment_fields = list(
                dict.fromkeys(
                    [
                        "id",
                        *await self._supported_fields(
                            "account.payment",
                            ["name", "ref", "date", "amount", "state", "move_id", *payment_link_fields],
                        ),
                    ]
                )
            )
            payment_link_stage_by_field = {
                "invoice_ids": "SVL_DASH_ACCOUNT_BALANCE_PAYMENTS_INVOICE_IDS",
                "reconciled_bill_ids": "SVL_DASH_ACCOUNT_BALANCE_PAYMENTS_RECONCILED",
            }
            payment_link_warning_by_field = {
                "invoice_ids": "Field account.payment.invoice_ids tidak bisa dipakai untuk payment tracing pada environment ini.",
                "reconciled_bill_ids": (
                    "Field account.payment.reconciled_bill_ids terdeteksi tetapi domain search_read gagal; "
                    "payment tracing lanjut tanpa field ini."
                ),
            }
            payment_link_log_by_field = {
                "invoice_ids": "Gagal membaca payment via invoice_ids",
                "reconciled_bill_ids": "Fallback payment tracing tanpa reconciled_bill_ids",
            }
            for payment_link_field in payment_link_fields:
                try:
                    for row in await self._search_read_in_chunks(
                        "account.payment",
                        ids_field=payment_link_field,
                        ids=sorted(bill_ids),
                        fields=payment_fields,
                        context=context,
                        stage=payment_link_stage_by_field.get(payment_link_field, "SVL_DASH_ACCOUNT_BALANCE_PAYMENTS"),
                        order="date,id",
                    ):
                        payment_rows[int(row.get("id") or 0)] = row
                except Exception as exc:  # noqa: BLE001
                    self._warn_once(
                        warnings,
                        payment_link_warning_by_field.get(
                            payment_link_field,
                            f"Field account.payment.{payment_link_field} tidak bisa dipakai untuk payment tracing pada environment ini.",
                        ),
                    )
                    self._log(
                        f"{payment_link_log_by_field.get(payment_link_field, f'Gagal membaca payment via {payment_link_field}')}: {exc}"
                    )
            return payment_rows

        if bill_ids:
            payment_rows_by_id, bill_line_rows = await asyncio.gather(
                _fetch_payment_rows_for_bill_ids(),
                self.rpc.search_read(
                    "account.move.line",
                    bill_line_domain,
                    fields=bill_line_fields,
                    order="date,id",
                    context=context,
                    stage="SVL_DASH_ACCOUNT_BALANCE_BILL_LINES",
                ),
            )
            for row in bill_line_rows:
                product_id = _many2one_id(row.get("product_id"))
                if product_id <= 0:
                    purchase_line_id = _many2one_id(row.get("purchase_line_id"))
                    product_id = _many2one_id((po_line_rows_by_id.get(purchase_line_id) or {}).get("product_id"))
                move_id = _many2one_id(row.get("move_id"))
                if (
                    product_id <= 0
                    or move_id <= 0
                    or not self._is_vendor_bill_move(move_id, bill_rows_by_id=bill_rows_by_id)
                ):
                    continue
                bill_line_rows_by_product[product_id].append(row)
                bill_ids_by_product[product_id].add(move_id)
            for rows in bill_line_rows_by_product.values():
                rows.sort(key=lambda current: (normalize_text(current.get("date")), int(current.get("id") or 0)))

            linked_bill_ids_all: set[int] = set()
            for row in payment_rows_by_id.values():
                payment_id = int(row.get("id") or 0)
                linked_bill_ids = set(_many2many_ids(row.get("reconciled_bill_ids"))) | set(_many2many_ids(row.get("invoice_ids")))
                linked_bill_ids_all.update(linked_bill_ids)
                for bill_id in sorted(linked_bill_ids):
                    payment_rows_by_bill_id[bill_id].append(row)
                    if payment_id > 0:
                        bill_ids_by_payment_id[payment_id].add(bill_id)
            extra_bill_ids = sorted(linked_bill_ids_all.difference(bill_ids))
            if extra_bill_ids:
                extra_bill_line_domain: list[Any] = [("move_id", "in", extra_bill_ids)]
                extra_bill_line_domain.extend(self._build_posted_domain(aml_fields))
                extra_bill_line_domain.extend(self._build_current_asset_display_type_domain(aml_fields))
                extra_bill_line_fields = list(
                    dict.fromkeys(
                        [
                            "id",
                            *await self._supported_fields(
                                "account.move.line",
                                [
                                    "move_id",
                                    "product_id",
                                    "product_uom_id",
                                    "purchase_line_id",
                                    "balance",
                                    "price_unit",
                                    "quantity",
                                    "price_subtotal",
                                    "currency_id",
                                    "amount_currency",
                                    "analytic_distribution",
                                    "date",
                                    "name",
                                ],
                            ),
                        ]
                    )
                )
                extra_bill_move_rows, extra_bill_rows = await asyncio.gather(
                    self._search_read_in_chunks(
                        "account.move",
                        ids_field="id",
                        ids=extra_bill_ids,
                        fields=bill_fields,
                        context=context,
                        stage="SVL_DASH_ACCOUNT_BALANCE_BILLS_PAYMENT_LINKED",
                    ),
                    self.rpc.search_read(
                        "account.move.line",
                        extra_bill_line_domain,
                        fields=extra_bill_line_fields,
                        order="date,id",
                        context=context,
                        stage="SVL_DASH_ACCOUNT_BALANCE_BILL_LINES_PAYMENT_LINKED",
                    ),
                )
                for row in extra_bill_move_rows:
                    bill_id = int(row.get("id") or 0)
                    if bill_id <= 0 or not self._is_vendor_bill_move_type(row.get("move_type")):
                        continue
                    bill_rows_by_id[bill_id] = row
                    bill_ids.add(bill_id)
                missing_po_line_ids = sorted(
                    {
                        _many2one_id(row.get("purchase_line_id"))
                        for row in extra_bill_rows
                        if _many2one_id(row.get("purchase_line_id")) > 0 and _many2one_id(row.get("purchase_line_id")) not in po_line_rows_by_id
                    }
                )
                if missing_po_line_ids:
                    for row in await self._search_read_in_chunks(
                        "purchase.order.line",
                        ids_field="id",
                        ids=missing_po_line_ids,
                        fields=po_line_fields,
                        context=context,
                        stage="SVL_DASH_ACCOUNT_BALANCE_PO_LINES_PAYMENT_LINKED",
                    ):
                        row_id = int(row.get("id") or 0)
                        if row_id > 0:
                            po_line_rows_by_id[row_id] = row
                extra_purchase_line_ids: set[int] = set()
                for row in extra_bill_rows:
                    product_id = _many2one_id(row.get("product_id"))
                    purchase_line_id = _many2one_id(row.get("purchase_line_id"))
                    if product_id <= 0:
                        product_id = _many2one_id((po_line_rows_by_id.get(purchase_line_id) or {}).get("product_id"))
                    move_id = _many2one_id(row.get("move_id"))
                    if (
                        product_id <= 0
                        or move_id <= 0
                        or not self._is_vendor_bill_move(move_id, bill_rows_by_id=bill_rows_by_id)
                    ):
                        continue
                    bill_line_rows_by_product[product_id].append(row)
                    bill_ids_by_product[product_id].add(move_id)
                    if product_id not in traced_product_ids:
                        traced_product_ids.append(product_id)
                    if purchase_line_id > 0:
                        extra_purchase_line_ids.add(purchase_line_id)
                        order_id = _many2one_id((po_line_rows_by_id.get(purchase_line_id) or {}).get("order_id"))
                        if order_id > 0:
                            purchase_order_ids_by_product[product_id].add(order_id)
                if extra_purchase_line_ids and "purchase_line_id" in stock_move_meta:
                    extra_stock_rows = await self.rpc.search_read(
                        "stock.move",
                        [("purchase_line_id", "in", sorted(extra_purchase_line_ids)), ("state", "=", "done")],
                        fields=stock_move_fields,
                        order="date,id",
                        context=context,
                        stage="SVL_DASH_ACCOUNT_BALANCE_STOCK_MOVE_PAYMENT_LINKED",
                    )
                    for row in extra_stock_rows:
                        row_id = int(row.get("id") or 0)
                        product_id = _many2one_id(row.get("product_id"))
                        if row_id <= 0 or product_id <= 0:
                            continue
                        stock_move_rows_by_id[row_id] = row
                        for move_id in _many2many_ids(row.get("account_move_ids")):
                            stj_move_ids_by_product[product_id].add(move_id)
                        if product_id not in traced_product_ids:
                            traced_product_ids.append(product_id)

            for rows in payment_rows_by_bill_id.values():
                rows.sort(key=lambda current: (normalize_text(current.get("date")), int(current.get("id") or 0)))

            self._log(
                f"[BENCH]   trace/payments_billlines: {(perf_counter() - _t0) * 1000.0:.0f}ms | "
                f"{len(payment_rows_by_id)} payments | {len(bill_line_rows_by_product)} bill_line_products"
            )
            _t0 = perf_counter()
            payment_move_ids = sorted({_many2one_id(row.get("move_id")) for row in payment_rows_by_id.values() if _many2one_id(row.get("move_id")) > 0})
            if payment_move_ids and "matching_number" in aml_fields:
                payment_move_line_fields = list(
                    dict.fromkeys(["id", *await self._supported_fields("account.move.line", ["move_id", "matching_number"])])
                )
                payment_move_lines = await self._search_read_in_chunks(
                    "account.move.line",
                    ids_field="move_id",
                    ids=payment_move_ids,
                    fields=payment_move_line_fields,
                    context=context,
                    stage="SVL_DASH_ACCOUNT_BALANCE_PAYMENT_MOVE_LINES",
                    base_domain=[*self._build_posted_domain(aml_fields), *self._build_current_asset_display_type_domain(aml_fields)],
                    order="date,id",
                )
                matching_numbers = sorted(
                    {
                        normalize_text(row.get("matching_number"))
                        for row in payment_move_lines
                        if normalize_text(row.get("matching_number"))
                    }
                )
                for row in payment_move_lines:
                    move_id = _many2one_id(row.get("move_id"))
                    matching = normalize_text(row.get("matching_number"))
                    if move_id > 0 and matching:
                        matching_numbers_by_payment_move_id[move_id].add(matching)
                        payment_move_ids_by_matching[matching].add(move_id)
                if matching_numbers:
                    related_matching_lines = await self.rpc.search_read(
                        "account.move.line",
                        [("matching_number", "in", matching_numbers)],
                        fields=list(dict.fromkeys(["id", *await self._supported_fields("account.move.line", ["move_id", "matching_number", "account_id"])])),
                        order="date,id",
                        context=context,
                        stage="SVL_DASH_ACCOUNT_BALANCE_MATCHING_BANK_LINES",
                    )
                    stj_move_id_set = {move_id for move_ids in stj_move_ids_by_product.values() for move_id in move_ids}
                    candidate_bank_move_ids: set[int] = set()
                    for row in related_matching_lines:
                        move_id = _many2one_id(row.get("move_id"))
                        matching = normalize_text(row.get("matching_number"))
                        if move_id <= 0 or not matching:
                            continue
                        if move_id in payment_move_ids or move_id in bill_rows_by_id or move_id in stj_move_id_set:
                            continue
                        candidate_bank_move_ids.add(move_id)
                    candidate_move_rows = await _fetch_matching_move_rows(
                        candidate_bank_move_ids,
                        stage="SVL_DASH_ACCOUNT_BALANCE_MATCHING_BANK_CLASSIFY",
                    )
                    await _ensure_matching_account_info(related_matching_lines)
                    lines_by_candidate_move: dict[int, list[dict[str, Any]]] = defaultdict(list)
                    for row in related_matching_lines:
                        move_id = _many2one_id(row.get("move_id"))
                        if move_id > 0:
                            lines_by_candidate_move[move_id].append(row)
                    for move_id in sorted(candidate_bank_move_ids):
                        move_row = candidate_move_rows.get(move_id) or {}
                        if self._classify_matching_entry_candidate(
                            move_row=move_row,
                            aml_rows=lines_by_candidate_move.get(move_id, []),
                            account_info_map=matching_account_info_map,
                        ) != "payment_bank":
                            continue
                        for row in lines_by_candidate_move.get(move_id, []):
                            matching = normalize_text(row.get("matching_number"))
                            if not matching:
                                continue
                            bank_move_ids_by_matching[matching].add(move_id)
                            matching_numbers_by_bank_move_id[move_id].add(matching)

            self._log(
                f"[BENCH]   trace/matching_init: {(perf_counter() - _t0) * 1000.0:.0f}ms | "
                f"{len(payment_move_ids)} payment_moves | "
                f"{sum(len(v) for v in bank_move_ids_by_matching.values())} bank_moves"
            )

            # ── Iterative expansion via matching_number (reconciliation graph) ──
            # Starting from known bills, follow matching_number links to discover:
            # 1. Direct BK entries (bank JEs that reconcile via matching on payable account)
            # 2. Additional bills connected in the same reconciliation group
            # 3. STJs linked to additional bills (via 2103006 reconciliation)
            # Results keyed by seed bill to avoid cross-cycle contamination.
            # E.g.: BILL A ↔ matching '127539' ↔ BK196 + BILL B → BILL B's STJs also included.
            _has_stj_seeds = bool(stj_move_ids_by_product)
            if "matching_number" in aml_fields and (bill_ids or _has_stj_seeds):
                _exp_initial_bills: set[int] = set(bill_rows_by_id)
                _exp_initial_stjs: set[int] = {mid for mids in stj_move_ids_by_product.values() for mid in mids}
                _seed_partner_t0 = perf_counter()
                for _bill_id in _exp_initial_bills:
                    _bill_partner_key(_bill_id)
                await _ensure_stj_partner_keys(
                    _exp_initial_stjs,
                    stage="SVL_DASH_EXP_INITIAL_STJ_PARTNERS",
                )
                self._log(
                    f"[BENCH]   trace/bfs_seed_partners: {(perf_counter() - _seed_partner_t0) * 1000.0:.0f}ms | "
                    f"{len(_exp_initial_bills)} bills | {len(_exp_initial_stjs)} stj"
                )
                _seed_init_t0 = perf_counter()
                _exp_known: set[int] = (
                    _exp_initial_bills
                    | _exp_initial_stjs
                    | set(payment_rows_by_id)
                )
                _exp_done_mns: set[str] = set()
                for _pmid in payment_move_ids:
                    _exp_done_mns.update(matching_numbers_by_payment_move_id.get(_pmid, set()))
                # Track: new move → set of original seed bills that discovered it
                _exp_move_seeds: dict[int, set[int]] = {bid: {bid} for bid in _exp_initial_bills}
                # STJ seeds: map each STJ to its associated bills (via shared product).
                # Orphaned STJs (no bill yet) get an empty seed set and are tracked via
                # expansion_bills_by_stj so cycle assembly can still pick up discovered bills.
                _stj_bill_seeds: dict[int, set[int]] = defaultdict(set)
                for _pid, _bids_pid in bill_ids_by_product.items():
                    for _stj_id in stj_move_ids_by_product.get(_pid, set()):
                        _stj_partner_key = stj_partner_key_by_move_id.get(_stj_id, "")
                        if not _stj_partner_key:
                            continue
                        for _bill_id in _bids_pid & _exp_initial_bills:
                            if self._matching_partner_keys_compatible(_bill_partner_key(_bill_id), _stj_partner_key):
                                _stj_bill_seeds[_stj_id].add(_bill_id)
                for _stj_id in _exp_initial_stjs:
                    _bill_seeds = _stj_bill_seeds.get(_stj_id)
                    _exp_move_seeds[_stj_id] = set(_bill_seeds) if _bill_seeds else set()
                _exp_frontier: set[int] = set(_exp_initial_bills) | _exp_initial_stjs
                _exp_all_pids: set[int] = {p for pids in product_ids_by_picking.values() for p in pids}
                self._log(
                    f"[BENCH]   trace/bfs_seed_init: {(perf_counter() - _seed_init_t0) * 1000.0:.0f}ms | "
                    f"known={len(_exp_known)} | frontier={len(_exp_frontier)}"
                )
                _has_stj_field = "stock_move_id" in account_move_meta
                _stock_picking_meta = await self._fields_get_cached("stock.picking") if _has_stj_field else {}
                _has_picking_type_code = "picking_type_code" in _stock_picking_meta
                _guard_picking_id_by_stock_move_id: dict[int, int] = {}
                _valid_guard_picking_ids: set[int] = set()
                _invalid_guard_picking_ids: set[int] = set()
                _bfs_t0 = perf_counter()

                for _rnd in range(5):
                    if not _exp_frontier:
                        break
                    _rnd_t0 = perf_counter()

                    # Step A: collect new matching_numbers from frontier moves
                    _exp_f_amls = await self._search_read_in_chunks(
                        "account.move.line",
                        ids_field="move_id",
                        ids=sorted(_exp_frontier),
                        fields=["id", "move_id", "matching_number", "account_id"],
                        base_domain=[*self._build_posted_domain(aml_fields), ("matching_number", "!=", False)],
                        context=context,
                        stage=f"SVL_DASH_EXP_FRONTIER_{_rnd}",
                        order="id",
                    )
                    _exp_new_mns: set[str] = {
                        normalize_text(r.get("matching_number"))
                        for r in _exp_f_amls
                        if normalize_text(r.get("matching_number"))
                    } - _exp_done_mns
                    if not _exp_new_mns:
                        break
                    _exp_done_mns.update(_exp_new_mns)

                    # Step B: find all moves sharing those matching_numbers
                    _exp_g_amls = await self.rpc.search_read(
                        "account.move.line",
                        [("matching_number", "in", sorted(_exp_new_mns))],
                        fields=["id", "move_id", "matching_number", "account_id"],
                        order="id",
                        context=context,
                        stage=f"SVL_DASH_EXP_MATCHED_{_rnd}",
                    )
                    _exp_mn_to_mids: dict[str, set[int]] = defaultdict(set)
                    for _r in _exp_g_amls:
                        _mn2 = normalize_text(_r.get("matching_number"))
                        _mid2 = _many2one_id(_r.get("move_id"))
                        if _mn2 and _mid2 > 0:
                            _exp_mn_to_mids[_mn2].add(_mid2)
                    _exp_candidate_ids: set[int] = {
                        mid for mids in _exp_mn_to_mids.values() for mid in mids
                        if mid > 0
                    } - _exp_known
                    if not _exp_candidate_ids:
                        break

                    # Step C: classify candidates by move_type + matching account roles
                    _exp_cand_rows = await _fetch_matching_move_rows(
                        _exp_candidate_ids,
                        stage=f"SVL_DASH_EXP_CLASSIFY_{_rnd}",
                    )
                    await _ensure_matching_account_info(_exp_f_amls)
                    await _ensure_matching_account_info(_exp_g_amls)
                    _exp_aml_rows_by_move: dict[int, list[dict[str, Any]]] = defaultdict(list)
                    for _row in _exp_g_amls:
                        _move_id = _many2one_id(_row.get("move_id"))
                        if _move_id > 0:
                            _exp_aml_rows_by_move[_move_id].append(_row)
                    _exp_new_bill_ids: set[int] = set()
                    _exp_new_stj_ids: set[int] = set()
                    _exp_new_bank_ids: set[int] = set()
                    for _cid, _cr in _exp_cand_rows.items():
                        _candidate_kind = self._classify_matching_entry_candidate(
                            move_row=_cr,
                            aml_rows=_exp_aml_rows_by_move.get(_cid, []),
                            account_info_map=matching_account_info_map,
                        )
                        if _candidate_kind == "bill":
                            _exp_new_bill_ids.add(_cid)
                            bill_rows_by_id[_cid] = _cr
                            bill_ids.add(_cid)
                            _bill_partner_key(_cid)
                        elif _candidate_kind == "stj":
                            _exp_new_stj_ids.add(_cid)
                        elif _candidate_kind == "payment_bank":
                            _exp_new_bank_ids.add(_cid)

                    # Guard: STJ candidates must be linked to incoming done pickings.
                    # Candidates with stock_move_id > 0 are verified via stock.picking;
                    # name-only STJs (no stock_move_id) are kept as-is.
                    if _exp_new_stj_ids and _has_stj_field:
                        _stj_sm_map: dict[int, int] = {
                            _cid: _many2one_id(_exp_cand_rows[_cid].get("stock_move_id"))
                            for _cid in _exp_new_stj_ids
                        }
                        _stj_sm_map = {k: v for k, v in _stj_sm_map.items() if v > 0}
                        if _stj_sm_map:
                            _sm_ids_needed = sorted(
                                {
                                    int(stock_move_id or 0)
                                    for stock_move_id in _stj_sm_map.values()
                                    if int(stock_move_id or 0) > 0 and int(stock_move_id or 0) not in _guard_picking_id_by_stock_move_id
                                }
                            )
                            if _sm_ids_needed:
                                _sm_rows_g = await self._search_read_in_chunks(
                                    "stock.move",
                                    ids_field="id",
                                    ids=_sm_ids_needed,
                                    fields=["id", "picking_id"],
                                    context=context,
                                    stage=f"SVL_DASH_EXP_SM_GUARD_{_rnd}",
                                )
                                for _sm_row in _sm_rows_g:
                                    _sm_id = int(_sm_row.get("id") or 0)
                                    _pk_id = _many2one_id(_sm_row.get("picking_id"))
                                    if _sm_id > 0 and _pk_id > 0:
                                        _guard_picking_id_by_stock_move_id[_sm_id] = _pk_id
                            _sm_to_pk: dict[int, int] = {
                                _smid: _guard_picking_id_by_stock_move_id.get(_smid, 0)
                                for _smid in _stj_sm_map.values()
                            }
                            _pk_ids_to_check = {
                                _pk_id
                                for _pk_id in _sm_to_pk.values()
                                if _pk_id > 0 and _pk_id not in _valid_guard_picking_ids and _pk_id not in _invalid_guard_picking_ids
                            }
                            if _pk_ids_to_check:
                                _pk_fields_g = ["id", "state"]
                                if _has_picking_type_code:
                                    _pk_fields_g.append("picking_type_code")
                                _pk_rows_g = await self._search_read_in_chunks(
                                    "stock.picking",
                                    ids_field="id",
                                    ids=sorted(_pk_ids_to_check),
                                    fields=_pk_fields_g,
                                    context=context,
                                    stage=f"SVL_DASH_EXP_PK_GUARD_{_rnd}",
                                )
                                _valid_guard_picking_ids.update(
                                    int(r.get("id") or 0)
                                    for r in _pk_rows_g
                                    if normalize_text(r.get("state")) == "done"
                                    and (
                                        not _has_picking_type_code
                                        or normalize_text(r.get("picking_type_code")) == "incoming"
                                        or int(r.get("id") or 0) in _return_picking_ids
                                    )
                                )
                                _invalid_guard_picking_ids.update(
                                    int(r.get("id") or 0)
                                    for r in _pk_rows_g
                                    if int(r.get("id") or 0) > 0 and int(r.get("id") or 0) not in _valid_guard_picking_ids
                                )
                            _demote: set[int] = {
                                _cid
                                for _cid, _smid in _stj_sm_map.items()
                                if _sm_to_pk.get(_smid, 0) not in _valid_guard_picking_ids
                            }
                            if _demote:
                                _exp_new_stj_ids -= _demote
                                _exp_new_bank_ids.update(_demote)

                    await _ensure_stj_partner_keys(
                        _exp_new_stj_ids,
                        stage=f"SVL_DASH_EXP_STJ_PARTNERS_{_rnd}",
                    )

                    # Step D: assign seed bills, update bank/STJ dicts
                    for _mn in _exp_new_mns:
                        _all_bills_in_mn: set[int] = _exp_mn_to_mids[_mn] & set(bill_rows_by_id)
                        # Seeds = initial bills OR known seeds from ANY move in this MN group
                        # (bills AND non-bill moves like BK entries that carry seeds).
                        # This allows BILL B discovered via BK→mn→BILL B to inherit BK's seeds.
                        _seeds_in_mn: set[int] = set()
                        for _bm in _exp_mn_to_mids[_mn]:
                            if _bm in _exp_initial_bills:
                                _seeds_in_mn.add(_bm)
                            elif _bm in _exp_move_seeds:
                                _seeds_in_mn.update(_exp_move_seeds[_bm])

                        # New bills: register seed linkage (bidirectional)
                        for _nbid in _exp_mn_to_mids[_mn] & _exp_new_bill_ids:
                            _candidate_bill_partner_key = _bill_partner_key(_nbid)
                            _compatible_bill_seeds = self._matching_compatible_seed_bills(
                                _seeds_in_mn,
                                candidate_partner_key=_candidate_bill_partner_key,
                                bill_partner_key_by_move_id=bill_partner_key_by_move_id,
                            )
                            _exp_move_seeds[_nbid] = set(_compatible_bill_seeds)
                            for _seed in _compatible_bill_seeds:
                                expansion_bills_by_bill[_seed].add(_nbid)
                                expansion_bills_by_bill[_nbid].add(_seed)
                            # Orphaned STJ seeds (no bill yet): register discovered bill so
                            # cycle assembly can pull it in via expansion_bills_by_stj.
                            for _bm in _exp_mn_to_mids[_mn]:
                                if (
                                    _bm in _exp_initial_stjs
                                    and not _stj_bill_seeds.get(_bm)
                                    and self._matching_partner_keys_compatible(
                                        stj_partner_key_by_move_id.get(_bm, ""),
                                        _candidate_bill_partner_key,
                                    )
                                ):
                                    expansion_bills_by_stj[_bm].add(_nbid)

                        # New STJs: link to seed bills
                        for _stj_id in _exp_mn_to_mids[_mn] & _exp_new_stj_ids:
                            _candidate_stj_partner_key = stj_partner_key_by_move_id.get(_stj_id, "")
                            _compatible_stj_seeds = self._matching_compatible_seed_bills(
                                _seeds_in_mn,
                                candidate_partner_key=_candidate_stj_partner_key,
                                bill_partner_key_by_move_id=bill_partner_key_by_move_id,
                            )
                            _exp_move_seeds[_stj_id] = set(_compatible_stj_seeds)
                            for _seed in _compatible_stj_seeds:
                                expansion_stjs_by_bill[_seed].add(_stj_id)
                            # Also propagate via seeds of non-initial bills in this group
                            for _bm in _all_bills_in_mn - _exp_initial_bills:
                                for _seed in _exp_move_seeds.get(_bm, set()):
                                    if self._matching_partner_keys_compatible(
                                        _bill_partner_key(_seed),
                                        _candidate_stj_partner_key,
                                    ):
                                        expansion_stjs_by_bill[_seed].add(_stj_id)

                        # New BK entries: add to direct bank collections + record seeds
                        for _bk_id in _exp_mn_to_mids[_mn] & _exp_new_bank_ids:
                            _exp_move_seeds[_bk_id] = set(_seeds_in_mn)
                            bank_move_ids_by_matching[_mn].add(_bk_id)
                            matching_numbers_by_bank_move_id[_bk_id].add(_mn)
                            for _bid in _all_bills_in_mn:
                                direct_bank_move_ids_by_bill[_bid].add(_bk_id)
                                direct_bills_by_bank_move_id[_bk_id].update(_all_bills_in_mn)
                            for _tpid in _exp_all_pids:
                                bank_move_ids_by_product[_tpid].add(_bk_id)

                    _exp_known.update(_exp_candidate_ids)
                    _exp_frontier = _exp_candidate_ids
                    self._log(
                        f"[BENCH]   trace/bfs_round_{_rnd}: {(perf_counter() - _rnd_t0) * 1000.0:.0f}ms | "
                        f"frontier={len(_exp_candidate_ids)} | "
                        f"new_bills={len(_exp_new_bill_ids)} | new_stj={len(_exp_new_stj_ids)} | new_bk={len(_exp_new_bank_ids)}"
                    )

                self._log(
                    f"[BENCH]   trace/bfs_total: {(perf_counter() - _bfs_t0) * 1000.0:.0f}ms | known={len(_exp_known)}"
                )

            _post_bfs_t0 = perf_counter()
            for product_id, product_bill_ids in bill_ids_by_product.items():
                for bill_id in product_bill_ids:
                    for payment_row in payment_rows_by_bill_id.get(bill_id, []):
                        payment_move_id = _many2one_id(payment_row.get("move_id"))
                        if payment_move_id <= 0:
                            continue
                        payment_move_ids_by_product[product_id].add(payment_move_id)
                        for matching in matching_numbers_by_payment_move_id.get(payment_move_id, set()):
                            bank_move_ids_by_product[product_id].update(bank_move_ids_by_matching.get(matching, set()))
                    # Direct BK (bypassed account.payment) — must be in bank_move_ids_by_product
                    # so they are included in relevant_move_ids → all_ledger_rows
                    for dmid in direct_bank_move_ids_by_bill.get(bill_id, set()):
                        bank_move_ids_by_product[product_id].add(dmid)
            self._log(
                f"[BENCH]   trace/post_bfs_maps: {(perf_counter() - _post_bfs_t0) * 1000.0:.0f}ms | "
                f"{len(payment_move_ids_by_product)} payment_products | {len(bank_move_ids_by_product)} bank_products"
            )

        stock_move_rows = list(stock_move_rows_by_id.values())
        traced_product_ids = sorted({int(product_id or 0) for product_id in traced_product_ids if int(product_id or 0) > 0})
        gr_refs_by_product: dict[int, set[str]] = defaultdict(set)
        gr_dates_by_product: dict[int, set[str]] = defaultdict(set)
        po_names_by_product: dict[int, set[str]] = defaultdict(set)
        stj_value_by_move_product: dict[int, dict[int, float]] = defaultdict(dict)
        for row in stock_move_rows:
            product_id = _many2one_id(row.get("product_id"))
            if product_id <= 0:
                continue
            gr_reference = normalize_text(row.get("reference")) or normalize_text(
                (picking_rows_by_id.get(_many2one_id(row.get("picking_id"))) or {}).get("name")
            )
            if gr_reference:
                gr_refs_by_product[product_id].add(gr_reference)
            gr_date = normalize_text(row.get("date"))
            if gr_date:
                gr_dates_by_product[product_id].add(gr_date)
            quantity = to_float(row.get("product_qty")) or to_float(row.get("quantity")) or 1.0
            unit_value = abs(_round2((to_float(row.get("price_unit")) or 0.0) * quantity))
            fallback_value = abs(_round2(quantity))
            stock_value = unit_value or fallback_value
            if stock_value < 0.01:
                continue
            for move_id in _many2many_ids(row.get("account_move_ids")):
                product_values = stj_value_by_move_product.setdefault(int(move_id or 0), {})
                product_values[product_id] = _round2(product_values.get(product_id, 0.0) + stock_value)
        for product_id, order_ids in purchase_order_ids_by_product.items():
            for order_id in order_ids:
                po_name = normalize_text((purchase_order_rows_by_id.get(order_id) or {}).get("name"))
                if po_name:
                    po_names_by_product[product_id].add(po_name)
        for product_id, move_ids in bill_ids_by_product.items():
            for move_id in move_ids:
                po_name = normalize_text((bill_rows_by_id.get(move_id) or {}).get("invoice_origin"))
                if po_name:
                    po_names_by_product[product_id].add(po_name)

        _t0 = perf_counter()
        svl_rows_by_stock_move_id: dict[int, list[dict[str, Any]]] = defaultdict(list)
        if stock_move_rows_by_id:
            svl_evidence_fields = list(
                dict.fromkeys(
                    [
                        "id",
                        *await self._supported_fields(
                            "stock.valuation.layer",
                            ["stock_move_id", "account_move_id", "quantity", "value", "unit_cost", "uom_id", "description"],
                        ),
                    ]
                )
            )
            for svl_row in await self._search_read_in_chunks(
                "stock.valuation.layer",
                ids_field="stock_move_id",
                ids=sorted(stock_move_rows_by_id),
                fields=svl_evidence_fields,
                context=context,
                stage="SVL_DASH_PCB_SVL_EVIDENCE",
            ):
                sm_id = _many2one_id(svl_row.get("stock_move_id"))
                if sm_id > 0:
                    svl_rows_by_stock_move_id[sm_id].append(svl_row)

        uom_ids_needed: set[int] = set()
        for row in stock_move_rows_by_id.values():
            for field_name in ("product_uom", "product_uom_id"):
                uom_id = _many2one_id(row.get(field_name))
                if uom_id > 0:
                    uom_ids_needed.add(uom_id)
        for row in po_line_rows_by_id.values():
            for field_name in ("product_uom", "product_uom_id"):
                uom_id = _many2one_id(row.get(field_name))
                if uom_id > 0:
                    uom_ids_needed.add(uom_id)
        for rows in bill_line_rows_by_product.values():
            for row in rows:
                uom_id = _many2one_id(row.get("product_uom_id"))
                if uom_id > 0:
                    uom_ids_needed.add(uom_id)
        for rows in svl_rows_by_stock_move_id.values():
            for row in rows:
                uom_id = _many2one_id(row.get("uom_id"))
                if uom_id > 0:
                    uom_ids_needed.add(uom_id)

        uom_info_by_id: dict[int, dict[str, Any]] = {}
        if uom_ids_needed:
            uom_fields = list(
                dict.fromkeys(
                    [
                        "id",
                        *await self._supported_fields(
                            "uom.uom",
                            ["name", "display_name", "factor", "parent_path", "category_id"],
                        ),
                    ]
                )
            )
            for row in await self._search_read_in_chunks(
                "uom.uom",
                ids_field="id",
                ids=sorted(uom_ids_needed),
                fields=uom_fields,
                context=context,
                stage="SVL_DASH_PCB_UOM_EVIDENCE",
            ):
                row_id = int(row.get("id") or 0)
                if row_id > 0:
                    uom_info_by_id[row_id] = row

        self._log(
            f"[BENCH]   trace/svl_uom_evidence: {(perf_counter() - _t0) * 1000.0:.0f}ms | "
            f"{sum(len(rows) for rows in svl_rows_by_stock_move_id.values())} svl_rows | {len(uom_info_by_id)} uoms"
        )
        stock_move_indexes = self._build_pcb_stock_move_indexes(stock_move_rows_by_id)
        relevant_move_ids = sorted(
            {
                move_id
                for move_ids in stj_move_ids_by_product.values()
                for move_id in move_ids
            }
            | {
                move_id
                for move_ids in bill_ids_by_product.values()
                for move_id in move_ids
            }
            | {
                move_id
                for move_ids in payment_move_ids_by_product.values()
                for move_id in move_ids
            }
            | {
                move_id
                for move_ids in bank_move_ids_by_product.values()
                for move_id in move_ids
            }
            # Expansion-discovered bills and STJs (scoped per seed bill to avoid cross-cycle contamination)
            | {mid for mids in expansion_bills_by_bill.values() for mid in mids}
            | {mid for mids in expansion_stjs_by_bill.values() for mid in mids}
            # Bills discovered via orphaned STJ seeds (STJ found bill before PO chain)
            | {mid for mids in expansion_bills_by_stj.values() for mid in mids}
        )
        self._log(f"Trace saldo akun: {len(relevant_move_ids)} move terkait untuk {len(traced_product_ids)} product.")
        return {
            "product_ids": traced_product_ids,
            "po_line_rows_by_product": {product_id: list(rows) for product_id, rows in po_line_rows_by_product.items()},
            "bill_line_rows_by_product": {product_id: list(rows) for product_id, rows in bill_line_rows_by_product.items()},
            "bill_rows_by_id": bill_rows_by_id,
            "payment_rows_by_id": payment_rows_by_id,
            "payment_rows_by_bill_id": {bill_id: list(rows) for bill_id, rows in payment_rows_by_bill_id.items()},
            "bill_ids_by_payment_id": {payment_id: sorted(bill_ids) for payment_id, bill_ids in bill_ids_by_payment_id.items()},
            "payment_move_ids_by_product": {product_id: sorted(move_ids) for product_id, move_ids in payment_move_ids_by_product.items()},
            "bank_move_ids_by_product": {product_id: sorted(move_ids) for product_id, move_ids in bank_move_ids_by_product.items()},
            "payment_move_ids_by_matching": {matching: sorted(move_ids) for matching, move_ids in payment_move_ids_by_matching.items()},
            "bank_move_ids_by_matching": {matching: sorted(move_ids) for matching, move_ids in bank_move_ids_by_matching.items()},
            "matching_numbers_by_payment_move_id": {move_id: sorted(values) for move_id, values in matching_numbers_by_payment_move_id.items()},
            "matching_numbers_by_bank_move_id": {move_id: sorted(values) for move_id, values in matching_numbers_by_bank_move_id.items()},
            "direct_bank_move_ids_by_bill": {bill_id: sorted(move_ids) for bill_id, move_ids in direct_bank_move_ids_by_bill.items()},
            "direct_bills_by_bank_move_id": {move_id: sorted(bill_ids) for move_id, bill_ids in direct_bills_by_bank_move_id.items()},
            "expansion_bills_by_bill": {bill_id: sorted(move_ids) for bill_id, move_ids in expansion_bills_by_bill.items()},
            "expansion_stjs_by_bill": {bill_id: sorted(move_ids) for bill_id, move_ids in expansion_stjs_by_bill.items()},
            "expansion_bills_by_stj": {stj_id: sorted(move_ids) for stj_id, move_ids in expansion_bills_by_stj.items()},
            "purchase_line_product_map": {
                int(row.get("id") or 0): _many2one_id(row.get("product_id"))
                for row in po_line_rows_by_id.values()
                if int(row.get("id") or 0) > 0 and _many2one_id(row.get("product_id")) > 0
            },
            "purchase_line_rows_by_id": {
                int(row.get("id") or 0): row
                for row in po_line_rows_by_id.values()
                if int(row.get("id") or 0) > 0
            },
            "purchase_line_po_name_map": {
                int(row.get("id") or 0): normalize_text((purchase_order_rows_by_id.get(_many2one_id(row.get("order_id"))) or {}).get("name")) or _many2one_name(row.get("order_id"))
                for row in po_line_rows_by_id.values()
                if int(row.get("id") or 0) > 0
            },
            "gr_refs_by_product": {
                product_id: sorted(gr_refs_by_product.get(product_id, set()))
                for product_id in traced_product_ids
            },
            "gr_dates_by_product": {
                product_id: sorted(gr_dates_by_product.get(product_id, set()))
                for product_id in traced_product_ids
            },
            "po_names_by_product": {
                product_id: sorted(po_names_by_product.get(product_id, set()))
                for product_id in traced_product_ids
            },
            "bill_ids_by_product": {product_id: sorted(move_ids) for product_id, move_ids in bill_ids_by_product.items()},
            "stj_value_by_move_product": {
                move_id: {
                    product_id: _round2(value)
                    for product_id, value in dict(product_values or {}).items()
                    if int(product_id or 0) > 0 and abs(_round2(value)) >= 0.01
                }
                for move_id, product_values in stj_value_by_move_product.items()
                if int(move_id or 0) > 0
            },
            "move_role_by_id": {
                **{
                    move_id: "stj"
                    for move_id in {move_id for move_ids in stj_move_ids_by_product.values() for move_id in move_ids}
                },
                **{move_id: "bill" for move_id in bill_rows_by_id},
                **{
                    move_id: "payment"
                    for move_id in {move_id for move_ids in payment_move_ids_by_product.values() for move_id in move_ids}
                },
                **{
                    move_id: "bank"
                    for move_id in {move_id for move_ids in bank_move_ids_by_product.values() for move_id in move_ids}
                },
            },
            "relevant_move_ids": relevant_move_ids,
            "invoice_qty_field": invoice_qty_field,
            # ── picking-level maps (used by purchase_cycle_balance mode) ──
            "picking_rows_by_id": picking_rows_by_id,
            "po_id_by_picking": po_id_by_picking,
            "stj_ids_by_picking": {pid: sorted(ids) for pid, ids in stj_ids_by_picking.items()},
            "product_ids_by_picking": {pid: sorted(ids) for pid, ids in product_ids_by_picking.items()},
            "inventory_types_by_picking": {
                int(picking_id): sorted(normalize_text(value).lower() for value in values if normalize_text(value))
                for picking_id, values in dict(inventory_types_by_picking).items()
                if int(picking_id or 0) > 0
            },
            "purchase_order_rows_by_id": purchase_order_rows_by_id,
            "stock_move_rows_by_id": stock_move_rows_by_id,
            "stock_move_rows_by_picking_id": {
                int(picking_id): list(rows)
                for picking_id, rows in dict(stock_move_indexes.get("stock_move_rows_by_picking_id") or {}).items()
                if int(picking_id or 0) > 0
            },
            "stock_move_ids_by_picking_id": {
                int(picking_id): sorted(int(value) for value in values if int(value or 0) > 0)
                for picking_id, values in dict(stock_move_indexes.get("stock_move_ids_by_picking_id") or {}).items()
                if int(picking_id or 0) > 0
            },
            "purchase_line_ids_by_picking_id": {
                int(picking_id): sorted(int(value) for value in values if int(value or 0) > 0)
                for picking_id, values in dict(stock_move_indexes.get("purchase_line_ids_by_picking_id") or {}).items()
                if int(picking_id or 0) > 0
            },
            "svl_rows_by_stock_move_id": {move_id: list(rows) for move_id, rows in svl_rows_by_stock_move_id.items()},
            "uom_info_by_id": uom_info_by_id,
            "return_picking_pairs": list(return_picking_pairs),
            "intercompany_partner_ids": sorted(_intercompany_partner_ids),
        }

    # ══════════════════════════════════════════════════════════════════════════
    # Purchase Cycle Balance — Langkah 4–9
    # ══════════════════════════════════════════════════════════════════════════

    async def _analyze_purchase_cycle_balance(
        self,
        *,
        request: SvlDashboardRequest,
        date_from: str,
        date_to: str,
        context: dict[str, Any],
        capabilities: dict[str, Any],
        warnings: list[str],
    ) -> SvlDashboardSnapshot:
        """Analyse completeness/balance of purchase cycles, grouped by GR (stock.picking)."""
        bench_started = perf_counter()
        company_name = await self._fetch_company_name(request.company_id, context=context)
        problem_codes = frozenset(
            c.strip() for c in (request.pcb_problem_codes or frozenset())
        ) or frozenset({"2103006", "1108099"})
        info_codes = frozenset(
            c.strip() for c in (request.pcb_info_codes or frozenset())
        ) or frozenset({"11120003"})

        trace_started = perf_counter()
        self._emit_progress(phase="trace", processed=0, total=5, current="Menelusuri flow GR → Bill → Payment → Bank...", progress=0.1)
        trace = await self._collect_account_balance_trace(
            request=request,
            context=context,
            date_from=date_from,
            date_to=date_to,
            product_ids=None,
            warnings=warnings,
        )
        relevant_move_ids = list(trace.get("relevant_move_ids") or [])
        picking_rows_by_id = dict(trace.get("picking_rows_by_id") or {})
        self._log(
            f"[BENCH]   trace: {(perf_counter() - trace_started) * 1000.0:.0f}ms | "
            f"{len(relevant_move_ids)} moves | {len(picking_rows_by_id)} pickings"
        )
        if not relevant_move_ids or not picking_rows_by_id:
            self._log(
                f"[BENCH] purchase_cycle_balance total: {(perf_counter() - bench_started) * 1000.0:.0f}ms | no cycles"
            )
            self._warn_once(warnings, "Tidak ada purchase cycle yang ditemukan untuk company/periode ini.")
            return SvlDashboardSnapshot(
                database=request.database,
                company_id=request.company_id,
                company_name=company_name,
                generated_at=datetime.now().isoformat(timespec="seconds"),
                period=f"{date_from or 'Awal'} - {date_to or 'Sekarang'}",
                dataset_mode=PURCHASE_CYCLE_BALANCE_DATASET_MODE,
                warnings=warnings,
            )

        correction_started = perf_counter()
        correction_move_ids = await self._find_pcb_correction_move_ids(
            request=request,
            date_from=date_from,
            date_to=date_to,
            context=context,
            trace=trace,
            problem_codes=problem_codes,
        )
        self._log(
            f"[BENCH]   correction_moves: {(perf_counter() - correction_started) * 1000.0:.0f}ms | "
            f"{len(correction_move_ids)} moves"
        )
        if correction_move_ids:
            trace = dict(trace)
            trace["pcb_correction_move_ids"] = correction_move_ids
            relevant_move_ids = sorted(set(relevant_move_ids) | set(correction_move_ids))
            self._log(
                f"Purchase cycle correction JE discovered: {len(correction_move_ids)} move ditambahkan ke relevant_move_ids."
            )

        ledger_started = perf_counter()
        self._emit_progress(phase="ledger", processed=1, total=5, current="Mengambil semua journal lines cycle...", progress=0.32)
        # Fetch ALL lines (no display_type filter except section/note)
        aml_fields = capabilities["aml_fields"]
        ledger_fields = [
            "id", "move_id", "date", "account_id", "partner_id", "partner_bank_id",
            "name", "product_id", "product_uom_id", "quantity", "price_unit",
            "price_subtotal", "price_total", "discount", "purchase_line_id",
            "debit", "credit", "balance", "currency_id", "amount_currency", "matching_number",
            "analytic_distribution", "stock_move_id", "stock_picking_id", "picking_id",
            "bill_line_id", "bill_move_id", "invoice_id", "bill_id",
            "display_type", "company_id",
        ]
        ledger_fields = list(dict.fromkeys(
            [f for f in ledger_fields if f in aml_fields or f in ("id", "move_id", "account_id", "debit", "credit", "balance", "date")]
        ))
        ledger_domain: list[Any] = [("company_id", "=", request.company_id)]
        ledger_domain.extend(self._build_posted_domain(aml_fields))
        if "display_type" in aml_fields:
            ledger_domain.append(("display_type", "not in", ["line_section", "line_note"]))
        all_ledger_rows = await self._search_read_in_chunks(
            "account.move.line",
            ids_field="move_id",
            ids=relevant_move_ids,
            fields=ledger_fields,
            context=context,
            stage="SVL_DASH_PCB_LEDGER",
            base_domain=ledger_domain,
            order="date,move_id,id",
        )
        self._log(
            f"[BENCH]   ledger_fetch: {(perf_counter() - ledger_started) * 1000.0:.0f}ms | "
            f"{len(all_ledger_rows)} lines | {len(relevant_move_ids)} moves"
        )
        self._log(f"Purchase cycle ledger: {len(all_ledger_rows)} lines voor {len(relevant_move_ids)} moves.")

        info_started = perf_counter()
        self._emit_progress(phase="info", processed=2, total=5, current="Mengambil info account dan move...", progress=0.52)
        account_ids = sorted({_many2one_id(r.get("account_id")) for r in all_ledger_rows if _many2one_id(r.get("account_id")) > 0})
        move_ids_for_info = sorted({_many2one_id(r.get("move_id")) for r in all_ledger_rows if _many2one_id(r.get("move_id")) > 0})
        account_info_map, move_info_map = await asyncio.gather(
            self._fetch_account_info_map(account_ids=account_ids, context=context),
            self._fetch_pcb_move_info(move_ids=move_ids_for_info, context=context),
        )
        self._log(
            f"[BENCH]   account_move_info: {(perf_counter() - info_started) * 1000.0:.0f}ms | "
            f"{len(account_ids)} accounts | {len(move_ids_for_info)} moves"
        )

        build_started = perf_counter()
        self._emit_progress(phase="build", processed=3, total=5, current="Menyusun purchase cycles...", progress=0.68)
        product_ids_for_raw = sorted({
            _many2one_id(r.get("product_id"))
            for r in all_ledger_rows
            if _many2one_id(r.get("product_id")) > 0
        })
        product_info_started = perf_counter()
        product_info_map = await self._fetch_pcb_product_info_map(
            product_ids=product_ids_for_raw,
            context=context,
            account_info_map=account_info_map,
        )
        self._log(
            f"[BENCH]   build/product_info: {(perf_counter() - product_info_started) * 1000.0:.0f}ms | "
            f"{len(product_info_map)} products"
        )
        adjustment_started = perf_counter()
        adjustment_task = asyncio.create_task(self._fetch_pcb_adjustment_audit_moves(
            request=request,
            date_from=date_from,
            date_to=date_to,
            context=context,
            capabilities=capabilities,
            excluded_move_ids=relevant_move_ids,
            account_info_map=account_info_map,
            product_info_map=product_info_map,
        ))
        build_cycles_started = perf_counter()
        try:
            cycles = self._build_purchase_cycles(
                trace=trace,
                all_ledger_rows=all_ledger_rows,
                account_info_map=account_info_map,
                move_info_map=move_info_map,
                product_info_map=product_info_map,
                warnings=warnings,
                problem_codes=problem_codes,
                info_codes=info_codes,
                build_repair_rows=False,
            )
        except Exception:
            adjustment_task.cancel()
            with suppress(asyncio.CancelledError):
                await adjustment_task
            raise
        self._log(
            f"[BENCH]   build/cycle_rows: {(perf_counter() - build_cycles_started) * 1000.0:.0f}ms | "
            f"{len(cycles)} cycles"
        )
        self._log(
            f"[BENCH]   cycle_grouping: {(perf_counter() - build_started) * 1000.0:.0f}ms | "
            f"{len(cycles)} cycles | {len(product_info_map)} products"
        )
        adjustment_moves, account_info_map, product_info_map = await adjustment_task
        attach_started = perf_counter()
        self._attach_pcb_adjustment_audit_rows(
            cycles=cycles,
            adjustment_moves=adjustment_moves,
            trace=trace,
            move_info_map=move_info_map,
            product_info_map=product_info_map,
            account_info_map=account_info_map,
        )
        self._log(
            f"[BENCH]   adj/attach_rows: {(perf_counter() - attach_started) * 1000.0:.0f}ms | "
            f"{len(adjustment_moves)} moves | {len(cycles)} cycles"
        )
        rebuild_started = perf_counter()
        self._rebuild_pcb_case2_repair_rows_for_cycles(
            cycles=cycles,
            adjustment_moves=adjustment_moves,
            trace=trace,
            all_ledger_rows=all_ledger_rows,
            account_info_map=account_info_map,
            move_info_map=move_info_map,
            product_info_map=product_info_map,
        )
        self._log(
            f"[BENCH]   adj/rebuild_case2: {(perf_counter() - rebuild_started) * 1000.0:.0f}ms | "
            f"{len(cycles)} cycles"
        )
        self._log(
            f"[BENCH]   adjustment_audit: {(perf_counter() - adjustment_started) * 1000.0:.0f}ms | "
            f"{len(adjustment_moves)} moves"
        )
        self._log(f"Purchase cycles: {len(cycles)} cycles terbentuk.")
        if adjustment_moves:
            self._log(
                f"Purchase cycle adjustment audit: {len(adjustment_moves)} move kandidat dipetakan sebagai warning/audit-only."
            )

        finalize_started = perf_counter()
        self._emit_progress(phase="finalize", processed=4, total=5, current="Menyusun snapshot purchase cycle balance...", progress=0.88)
        problem_count = sum(1 for c in cycles if c.cycle_status == "problem")
        partial_count = sum(1 for c in cycles if c.cycle_status == "partial")
        self._log(f"Cycle status: {problem_count} problem, {partial_count} partial, {len(cycles) - problem_count - partial_count} healthy.")
        document_class_counts: dict[str, int] = defaultdict(int)
        for cycle in cycles:
            document_class_counts[normalize_text(getattr(cycle, "document_classification", "")) or "unclassified"] += 1
        document_class_order = (
            _PCB_DOCUMENT_CLASS_PURCHASE_BACKED,
            _PCB_DOCUMENT_CLASS_PURCHASE_LIKELY,
            _PCB_DOCUMENT_CLASS_NON_PURCHASE,
            "unclassified",
        )
        self._log(
            "Cycle document flow: "
            + ", ".join(
                f"{_PCB_DOCUMENT_CLASS_LABEL.get(classification, classification)} {count}"
                for classification in document_class_order
                if (count := document_class_counts.get(classification, 0)) > 0
            )
            + "."
        )

        snapshot = SvlDashboardSnapshot(
            database=request.database,
            company_id=request.company_id,
            company_name=company_name,
            generated_at=datetime.now().isoformat(timespec="seconds"),
            period=f"{date_from or 'Awal'} - {date_to or 'Sekarang'}",
            dataset_mode=PURCHASE_CYCLE_BALANCE_DATASET_MODE,
            warnings=warnings,
            purchase_cycles=cycles,
        )
        self._log(
            f"[BENCH]   finalize: {(perf_counter() - finalize_started) * 1000.0:.0f}ms | "
            f"{problem_count} problem | {partial_count} partial"
        )
        self._log(
            f"[BENCH] purchase_cycle_balance total: {(perf_counter() - bench_started) * 1000.0:.0f}ms"
        )
        return snapshot

    async def _fetch_pcb_product_info_map(
        self,
        *,
        product_ids: list[int],
        context: dict[str, Any],
        account_info_map: dict[int, dict[str, Any]] | None = None,
    ) -> dict[int, dict[str, Any]]:
        """Fetch product/category account metadata for Purchase Cycle Balance."""
        if not product_ids:
            return {}
        resolved_account_info_map = dict(account_info_map or {})
        result: dict[int, dict[str, Any]] = {}
        categ_ids_needed: set[int] = set()
        item_expense_fields = self._detect_product_expense_account_fields(
            await self._fields_get_cached("product.product")
        )
        product_read_fields = list(
            dict.fromkeys(
                [
                    *await self._supported_fields(
                        "product.product",
                        ["default_code", "name", "categ_id", "standard_price", "uom_id"],
                    ),
                    *item_expense_fields,
                ]
            )
        )
        product_item_expense_meta: dict[int, dict[str, Any]] = {}
        for row in await self._read_in_chunks(
            "product.product",
            ids=product_ids,
            fields=product_read_fields,
            context=context,
            stage="SVL_DASH_PCB_PRODUCT_INFO",
            chunk_size=500,
            continue_on_error=True,
        ):
            pid = int(row.get("id") or 0)
            if pid <= 0:
                continue
            categ_id = _many2one_id(row.get("categ_id"))
            first_item_expense_field = ""
            first_item_expense_id = 0
            first_item_expense_name = ""
            for field_name in item_expense_fields:
                account_id = _many2one_id(row.get(field_name))
                if account_id <= 0:
                    continue
                first_item_expense_field = normalize_text(field_name)
                first_item_expense_id = account_id
                first_item_expense_name = _many2one_name(row.get(field_name))
                break
            result[pid] = {
                "default_code": normalize_text(row.get("default_code")) or "",
                "name": normalize_text(row.get("name")) or "",
                "categ_id": categ_id,
                "categ_name": "",
                "valuation_account_id": 0,
                "valuation_account_code": "",
                "valuation_account_name": "",
                "expense_account_id": 0,
                "expense_account_code": "",
                "expense_account_name": "",
                "expense_account_source": "",
                "expense_account_field_name": "",
                "standard_price": _round2(row.get("standard_price")),
                "uom_id": _many2one_id(row.get("uom_id")),
                "uom_name": _many2one_name(row.get("uom_id")),
            }
            product_item_expense_meta[pid] = {
                "account_id": first_item_expense_id,
                "name": first_item_expense_name,
                "field_name": first_item_expense_field,
            }
            if categ_id > 0:
                categ_ids_needed.add(categ_id)
        if categ_ids_needed:
            category_fields = await self._fields_get_cached("product.category")
            category_account_specs = self._detect_category_account_specs(category_fields)
            expense_specs = [
                dict(spec)
                for spec in category_account_specs
                if normalize_text(spec.get("role")) == "expense"
            ]
            category_rows: list[dict[str, Any]] = []
            category_read_fields = list(
                dict.fromkeys(["name", "property_stock_valuation_account_id", *(spec["field_name"] for spec in expense_specs)])
            )
            category_rows.extend(
                await self._read_in_chunks(
                    "product.category",
                    ids=sorted(categ_ids_needed),
                    fields=category_read_fields,
                    context=context,
                    stage="SVL_DASH_PCB_CATEG_INFO",
                    chunk_size=500,
                    continue_on_error=True,
                )
            )
            missing_account_ids = sorted(
                {
                    _many2one_id(row.get("property_stock_valuation_account_id"))
                    for row in category_rows
                    if _many2one_id(row.get("property_stock_valuation_account_id")) > 0
                }
                | {
                    _many2one_id(row.get(spec["field_name"]))
                    for row in category_rows
                    for spec in expense_specs
                    if _many2one_id(row.get(spec["field_name"])) > 0
                }
                | {
                    int(meta.get("account_id") or 0)
                    for meta in product_item_expense_meta.values()
                    if int(meta.get("account_id") or 0) > 0
                }
            )
            missing_account_ids = [
                account_id
                for account_id in missing_account_ids
                if account_id > 0 and account_id not in resolved_account_info_map
            ]
            if missing_account_ids:
                resolved_account_info_map.update(
                    await self._fetch_account_info_map(account_ids=missing_account_ids, context=context)
                )
            categ_name_map: dict[int, str] = {}
            valuation_by_category_id: dict[int, dict[str, Any]] = {}
            expense_by_category_id: dict[int, dict[str, Any]] = {}
            for row in category_rows:
                cid = int(row.get("id") or 0)
                if cid <= 0:
                    continue
                categ_name_map[cid] = normalize_text(row.get("name")) or ""
                valuation_account_id = _many2one_id(row.get("property_stock_valuation_account_id"))
                if valuation_account_id > 0:
                    valuation_info = resolved_account_info_map.get(valuation_account_id, {})
                    valuation_by_category_id[cid] = {
                        "account_id": valuation_account_id,
                        "code": normalize_text(valuation_info.get("code")).upper(),
                        "name": normalize_text(valuation_info.get("name"))
                        or _many2one_name(row.get("property_stock_valuation_account_id")),
                    }
                for spec in expense_specs:
                    expense_account_id = _many2one_id(row.get(spec["field_name"]))
                    if expense_account_id <= 0:
                        continue
                    expense_info = resolved_account_info_map.get(expense_account_id, {})
                    expense_by_category_id[cid] = {
                        "account_id": expense_account_id,
                        "code": normalize_text(expense_info.get("code")).upper(),
                        "name": normalize_text(expense_info.get("name"))
                        or _many2one_name(row.get(spec["field_name"])),
                        "source": normalize_text(spec.get("source")) or "Category Expense",
                        "field_name": normalize_text(spec.get("field_name")),
                    }
                    break
            for pid, pid_data in result.items():
                cid = int(pid_data.get("categ_id") or 0)
                if cid in categ_name_map:
                    pid_data["categ_name"] = categ_name_map[cid]
                valuation_data = valuation_by_category_id.get(cid) or {}
                if valuation_data:
                    pid_data["valuation_account_id"] = int(valuation_data.get("account_id") or 0)
                    pid_data["valuation_account_code"] = normalize_text(valuation_data.get("code")).upper()
                    pid_data["valuation_account_name"] = normalize_text(valuation_data.get("name"))
                expense_data = expense_by_category_id.get(cid) or {}
                if expense_data:
                    pid_data["expense_account_id"] = int(expense_data.get("account_id") or 0)
                    pid_data["expense_account_code"] = normalize_text(expense_data.get("code")).upper()
                    pid_data["expense_account_name"] = normalize_text(expense_data.get("name"))
                    pid_data["expense_account_source"] = normalize_text(expense_data.get("source"))
                    pid_data["expense_account_field_name"] = normalize_text(expense_data.get("field_name"))
                elif int((product_item_expense_meta.get(pid) or {}).get("account_id") or 0) > 0:
                    item_expense = product_item_expense_meta.get(pid) or {}
                    item_expense_account_id = int(item_expense.get("account_id") or 0)
                    item_expense_info = resolved_account_info_map.get(item_expense_account_id, {})
                    pid_data["expense_account_id"] = item_expense_account_id
                    pid_data["expense_account_code"] = normalize_text(item_expense_info.get("code")).upper()
                    pid_data["expense_account_name"] = normalize_text(item_expense_info.get("name")) or normalize_text(item_expense.get("name"))
                    pid_data["expense_account_source"] = "Item Expense"
                    pid_data["expense_account_field_name"] = normalize_text(item_expense.get("field_name"))
        return result

    async def _fetch_pcb_move_info(
        self,
        *,
        move_ids: list[int],
        context: dict[str, Any],
    ) -> dict[int, dict[str, Any]]:
        """Fetch account.move fields needed for the Purchase Cycle Balance view."""
        if not move_ids:
            return {}
        result: dict[int, dict[str, Any]] = {}
        move_fields = list(
            dict.fromkeys(
                [
                    "id",
                    *await self._supported_fields(
                        "account.move",
                        [
                            "name",
                            "date",
                            "state",
                            "journal_id",
                            "partner_id",
                            "partner_bank_id",
                            "ref",
                            "create_uid",
                            "create_date",
                            "stock_picking_id",
                            "picking_id",
                            "bill_move_id",
                            "invoice_id",
                            "bill_id",
                        ],
                    ),
                ]
            )
        )
        for row in await self._read_in_chunks(
            "account.move",
            ids=move_ids,
            fields=move_fields,
            context=context,
            stage="SVL_DASH_PCB_MOVE_INFO",
            chunk_size=500,
        ):
            move_id = int(row.get("id") or 0)
            if move_id > 0:
                result[move_id] = row
        return result

    @staticmethod
    def _is_pcb_adjustment_source_account(
        *,
        account_code: str,
        account_type: str,
    ) -> bool:
        clean_code = normalize_text(account_code).upper()
        clean_type = normalize_text(account_type).lower()
        return clean_code in _PCB_COGS_VARIANCE_CODES or clean_type == "expense_direct_cost"

    @staticmethod
    def _pcb_adjustment_rac_hint(move_name: str, move_ref: str) -> int:
        for value in (move_name, move_ref):
            if normalize_text(value).upper().startswith("RAC/"):
                return 5
        return 0

    @staticmethod
    def _relation_id_from_row(row: dict[str, Any], *field_names: str) -> int:
        for field_name in field_names:
            relation_id = _many2one_id(row.get(field_name))
            if relation_id > 0:
                return relation_id
        return 0

    @classmethod
    def _first_relation_id(cls, rows: list[dict[str, Any]], *field_names: str) -> int:
        for row in rows:
            relation_id = cls._relation_id_from_row(row, *field_names)
            if relation_id > 0:
                return relation_id
        return 0

    @classmethod
    def _row_picking_relation_id(cls, row: dict[str, Any]) -> int:
        return cls._relation_id_from_row(row, "stock_picking_id", "picking_id")

    @classmethod
    def _row_bill_move_relation_id(cls, row: dict[str, Any]) -> int:
        return cls._relation_id_from_row(row, "bill_move_id", "invoice_id", "bill_id")

    def _resolve_pcb_adjustment_relation_values_from_rows(
        self,
        *,
        linked_rows: list[dict[str, Any]],
        stock_move_rows_by_id: dict[int, dict[str, Any]],
        purchase_line_product_map: dict[int, int],
    ) -> dict[str, int]:
        stock_move_id = self._first_relation_id(linked_rows, "stock_move_id")
        stock_move_row = stock_move_rows_by_id.get(stock_move_id) or {}
        purchase_line_id = self._first_relation_id(linked_rows, "purchase_line_id")
        if purchase_line_id <= 0:
            purchase_line_id = _many2one_id(stock_move_row.get("purchase_line_id"))
        product_id = self._first_relation_id(linked_rows, "product_id")
        if product_id <= 0 and purchase_line_id > 0:
            product_id = int(purchase_line_product_map.get(purchase_line_id) or 0)
        if product_id <= 0:
            product_id = _many2one_id(stock_move_row.get("product_id"))
        picking_id = self._first_relation_id(linked_rows, "stock_picking_id", "picking_id")
        if picking_id <= 0:
            picking_id = _many2one_id(stock_move_row.get("picking_id"))
        bill_move_id = self._first_relation_id(linked_rows, "bill_move_id", "invoice_id", "bill_id")
        return {
            "product_id": int(product_id or 0),
            "purchase_line_id": int(purchase_line_id or 0),
            "stock_move_id": int(stock_move_id or 0),
            "bill_move_id": int(bill_move_id or 0),
            "picking_id": int(picking_id or 0),
        }

    def _resolve_pcb_adjustment_relation_values(
        self,
        *,
        source_row: dict[str, Any],
        clearing_rows: list[dict[str, Any]],
        stock_move_rows_by_id: dict[int, dict[str, Any]],
        purchase_line_product_map: dict[int, int],
    ) -> dict[str, int]:
        return self._resolve_pcb_adjustment_relation_values_from_rows(
            linked_rows=[dict(source_row), *[dict(row) for row in list(clearing_rows or [])]],
            stock_move_rows_by_id=stock_move_rows_by_id,
            purchase_line_product_map=purchase_line_product_map,
        )

    @staticmethod
    def _relation_has_structured_values(relation: dict[str, int]) -> bool:
        return any(
            int(relation.get(field_name) or 0) > 0
            for field_name in ("bill_move_id", "stock_move_id", "purchase_line_id", "picking_id")
        )

    def _resolve_pcb_adjustment_origin_info(
        self,
        *,
        move: dict[str, Any],
        source_row: dict[str, Any],
        stock_move_rows_by_id: dict[int, dict[str, Any]],
        purchase_line_product_map: dict[int, int],
    ) -> dict[str, Any]:
        origin_moves_by_name = {
            normalize_text(name).upper(): value
            for name, value in dict(move.get("origin_moves_by_name") or {}).items()
            if normalize_text(name)
        }
        if not origin_moves_by_name:
            return {}
        token_sources = (
            ("explicit.line_name", _extract_move_name_tokens(source_row.get("name"))),
            ("explicit.move_ref", _extract_move_name_tokens(move.get("move_ref"))),
            ("explicit.move_name", _extract_move_name_tokens(move.get("move_name"))),
        )
        for basis, tokens in token_sources:
            for token in tokens:
                origin_move = origin_moves_by_name.get(normalize_text(token).upper())
                if not origin_move:
                    continue
                relation = self._resolve_pcb_adjustment_relation_values_from_rows(
                    linked_rows=[dict(row) for row in list(origin_move.get("lines") or [])],
                    stock_move_rows_by_id=stock_move_rows_by_id,
                    purchase_line_product_map=purchase_line_product_map,
                )
                if not any(int(relation.get(key) or 0) > 0 for key in relation):
                    continue
                return {
                    "origin_move_id": int(origin_move.get("move_id") or 0),
                    "origin_move_name": normalize_text(origin_move.get("move_name")),
                    "origin_basis": normalize_text(basis),
                    "origin_product_id": int(relation.get("product_id") or 0),
                    "origin_purchase_line_id": int(relation.get("purchase_line_id") or 0),
                    "origin_stock_move_id": int(relation.get("stock_move_id") or 0),
                    "relation": relation,
                }
        return {}

    @staticmethod
    def _pcb_adjustment_audit_row_key(row: SvlDashboardPcbAdjustmentAuditRow | dict[str, Any]) -> tuple[Any, ...]:
        getter = row.get if isinstance(row, dict) else lambda key, default=None: getattr(row, key, default)
        return (
            int(getter("move_id", 0) or 0),
            normalize_text(getter("move_name", "")),
            normalize_text(getter("source_account_code", "")).upper(),
            _round2(float(getter("repair_clearing_amount", 0.0) or 0.0)),
            normalize_text(getter("matched_basis", "")),
            normalize_text(getter("origin_move_name", "")),
            bool(getter("ambiguous", False)),
        )

    @staticmethod
    def _pcb_adjustment_identity_key(row: SvlDashboardPcbAdjustmentAuditRow | dict[str, Any]) -> tuple[Any, ...]:
        getter = row.get if isinstance(row, dict) else lambda key, default=None: getattr(row, key, default)
        return (
            int(getter("move_id", 0) or 0),
            normalize_text(getter("source_account_code", "")).upper(),
            normalize_text(getter("clearing_account_code", "")).upper(),
            _round2(float(getter("repair_clearing_amount", 0.0) or 0.0)),
            int(getter("product_id", 0) or 0),
            int(getter("origin_product_id", 0) or 0),
            normalize_text(getter("origin_move_name", "")),
        )

    @staticmethod
    def _pcb_adjustment_line_amount(row: dict[str, Any]) -> float:
        amount = abs(_round2(float(row.get("balance") or 0.0)))
        if amount >= 0.01:
            return amount
        return max(
            abs(_round2(float(row.get("debit") or 0.0))),
            abs(_round2(float(row.get("credit") or 0.0))),
        )

    @staticmethod
    def _pcb_adjustment_external_ref_text(row: SvlDashboardPcbAdjustmentAuditRow) -> str:
        move_label = normalize_text(getattr(row, "move_name", "")) or normalize_text(getattr(row, "move_ref", ""))
        if not move_label:
            move_id = int(getattr(row, "move_id", 0) or 0)
            move_label = f"Move #{move_id}" if move_id > 0 else ""
        origin_label = normalize_text(getattr(row, "origin_move_name", ""))
        if origin_label and origin_label.upper() != move_label.upper():
            return f"{move_label} <- {origin_label}" if move_label else origin_label
        return move_label

    @staticmethod
    def _merge_pipe_text(existing_text: str, additions: list[str] | tuple[str, ...]) -> str:
        parts: list[str] = []
        seen: set[str] = set()
        for raw_value in [*normalize_text(existing_text).split(" | "), *list(additions or [])]:
            clean_value = normalize_text(raw_value)
            if not clean_value or clean_value in seen:
                continue
            seen.add(clean_value)
            parts.append(clean_value)
        return " | ".join(parts)

    @staticmethod
    def _pcb_adjustment_external_basis_text(row: SvlDashboardPcbAdjustmentAuditRow) -> str:
        parts: list[str] = []
        seen: set[str] = set()
        for raw_value in (
            normalize_text(getattr(row, "matched_basis", "")),
            normalize_text(getattr(row, "origin_basis", "")),
        ):
            if not raw_value or raw_value in seen:
                continue
            seen.add(raw_value)
            parts.append(raw_value)
        return " | ".join(parts)

    def _accumulate_pcb_external_clearing_for_item(
        self,
        *,
        item_row: SvlDashboardCycleItemRow,
        audit_row: SvlDashboardPcbAdjustmentAuditRow,
    ) -> None:
        amount = abs(_round2(float(getattr(audit_row, "repair_clearing_amount", 0.0) or 0.0)))
        if amount < 0.01:
            return
        item_row.external_clearing_amount = _round2(
            float(getattr(item_row, "external_clearing_amount", 0.0) or 0.0) + amount
        )
        item_row.external_clearing_verified = True
        ref_text = self._pcb_adjustment_external_ref_text(audit_row)
        if ref_text and ref_text not in list(getattr(item_row, "external_clearing_refs", None) or []):
            item_row.external_clearing_refs = [
                *list(getattr(item_row, "external_clearing_refs", None) or []),
                ref_text,
            ]
        basis_text = self._pcb_adjustment_external_basis_text(audit_row)
        if basis_text:
            item_row.external_clearing_basis = self._merge_pipe_text(
                normalize_text(getattr(item_row, "external_clearing_basis", "")),
                [basis_text],
            )
        item_row.verified_audit_clearing_amount = _round2(float(getattr(item_row, "external_clearing_amount", 0.0) or 0.0))

    @staticmethod
    def _item_has_pcb_case34_stj_evidence(item_row: SvlDashboardCycleItemRow) -> bool:
        return bool(
            getattr(item_row, "has_item_stj", False)
            or any(int(move_id or 0) > 0 for move_id in list(getattr(item_row, "stj_move_ids", None) or []))
            or any(normalize_text(ref) for ref in list(getattr(item_row, "stj_refs", None) or []))
            or any(int(move_id or 0) > 0 for move_id in list(getattr(item_row, "correction_stj_move_ids", None) or []))
            or any(normalize_text(ref) for ref in list(getattr(item_row, "correction_stj_refs", None) or []))
        )

    @staticmethod
    def _finalize_pcb_case34_item_state(item_row: SvlDashboardCycleItemRow) -> None:
        item_row.verified_audit_clearing_amount = _round2(float(getattr(item_row, "external_clearing_amount", 0.0) or 0.0))
        item_row.has_stj_evidence = SvlDashboardServiceAsync._item_has_pcb_case34_stj_evidence(item_row)
        item_row.eligible_case34 = bool(
            getattr(item_row, "has_item_bill", False)
            and (
                getattr(item_row, "has_stj_evidence", False)
                or abs(_round2(float(getattr(item_row, "external_clearing_amount", 0.0) or 0.0))) >= 0.01
            )
        )

    @classmethod
    def _item_problem_balance_codes(
        cls,
        item_row: SvlDashboardCycleItemRow,
        *,
        problem_codes: tuple[str, ...] | frozenset[str] | list[str] | set[str],
    ) -> set[str]:
        clean_problem_codes = {
            normalize_text(code).upper()
            for code in list(problem_codes or [])
            if normalize_text(code)
        }
        matched_codes: set[str] = set()
        for account_row in list(getattr(item_row, "account_rows", None) or []):
            account_code = normalize_text(getattr(account_row, "code", "")).upper()
            if account_code not in clean_problem_codes:
                continue
            if abs(_round2(getattr(account_row, "net_balance", 0.0))) < 0.01:
                continue
            matched_codes.add(account_code)
        return matched_codes

    @classmethod
    def _is_pcb_clearing_reclass_candidate(
        cls,
        *,
        account_rows: list[SvlDashboardCycleAccountRow],
        item_rows: list[SvlDashboardCycleItemRow],
        has_bills: bool,
        problem_codes: tuple[str, ...] | frozenset[str] | list[str] | set[str],
    ) -> bool:
        if not has_bills:
            return False
        clean_problem_codes = {
            normalize_text(code).upper()
            for code in list(problem_codes or [])
            if normalize_text(code)
        }
        problem_rows = [
            row
            for row in list(account_rows or [])
            if normalize_text(getattr(row, "code", "")).upper() in clean_problem_codes
            and abs(_round2(getattr(row, "net_balance", 0.0))) >= 0.01
        ]
        if not problem_rows:
            return False
        if any(normalize_text(getattr(row, "code", "")).upper() != "1108099" for row in problem_rows):
            return False
        remaining_problem_items = [
            item_row
            for item_row in list(item_rows or [])
            if cls._item_problem_balance_codes(item_row, problem_codes=clean_problem_codes)
        ]
        if not remaining_problem_items:
            return False
        return all(
            bool(getattr(item_row, "svl_zero_at_gr", False))
            and cls._item_problem_balance_codes(item_row, problem_codes=clean_problem_codes) == {"1108099"}
            for item_row in remaining_problem_items
        )

    @staticmethod
    def _is_pcb_grni_suspense_downgrade(cycle: SvlDashboardPurchaseCycle) -> bool:
        """True if the only problem account is 2103006 AND every item's 2103006
        residual is fully explained by partial invoicing (GRNI state — goods received,
        invoice pending from vendor).

        This method is called AFTER ``_apply_pcb_item_classifier`` has already run,
        so item_row.primary_case is populated.  An item with a non-empty primary_case
        means the classifier found a genuine accounting problem; an item with no
        primary_case (empty string / None) means either the item is clean or the GRNI
        guard in _classify_pcb_item_case already decided it is GRNI.

        Logic:
        1. The only "problem"-status account at cycle level must be 2103006.
        2. No item may carry a non-empty primary_case (which would indicate a genuine
           Case 2 / Case 5 / … — not just GRNI).
        3. At least one item must have a non-zero 2103006 residual (otherwise there
           is nothing to downgrade).

        When this returns True the caller downgrades cycle_status to "partial" and
        marks the cycle-level 2103006 account row as "acceptable".
        """
        problem_accounts = {
            normalize_text(getattr(r, "code", "")).upper()
            for r in (getattr(cycle, "account_rows", None) or [])
            if normalize_text(getattr(r, "status", "")).lower() == "problem"
        }
        if not problem_accounts:
            return False
        if problem_accounts != {"2103006"}:
            return False
        has_any_2103006_residual = False
        for item_row in (getattr(cycle, "item_rows", None) or []):
            # If the classifier assigned a case, this item has a genuine problem.
            if normalize_text(getattr(item_row, "primary_case", "")).strip():
                return False
            item_problem = SvlDashboardServiceAsync._item_row_account_balance(item_row, "2103006")
            if abs(item_problem) >= 0.01:
                has_any_2103006_residual = True
        return has_any_2103006_residual

    def _finalize_pcb_case34_cycle_items(self, *, cycle: SvlDashboardPurchaseCycle) -> None:
        for item_row in list(getattr(cycle, "item_rows", None) or []):
            item_row.adjustment_audit_rows.sort(
                key=lambda row: (
                    normalize_text(row.move_date),
                    normalize_text(row.move_name),
                    -abs(_round2(float(getattr(row, "repair_clearing_amount", 0.0) or 0.0))),
                    normalize_text(row.source_account_code),
                    normalize_text(row.matched_basis),
                )
            )
            self._finalize_pcb_case34_item_state(item_row)

    @staticmethod
    def _item_row_account_balance(item_row: SvlDashboardCycleItemRow, account_code: str) -> float:
        clean_account_code = normalize_text(account_code).upper()
        if not clean_account_code:
            return 0.0
        for account_row in list(getattr(item_row, "account_rows", None) or []):
            if normalize_text(getattr(account_row, "code", "")).upper() == clean_account_code:
                return _round2(getattr(account_row, "net_balance", 0.0))
        return 0.0

    @staticmethod
    def _is_grni_residual(
        *,
        item_row: SvlDashboardCycleItemRow,
        problem_2103006: float,
        gr_quantity: float,
        bill_quantity: float,
        stj_quantity: float = 0.0,
    ) -> bool:
        """True if the 2103006 residual on this item is fully explained by partial
        invoicing at a consistent unit price (GRNI state — goods received, bill pending).

        Case 2 requires a genuine Anglo-Saxon price mismatch between GR and Bill.
        If STJ unit price ≈ Bill unit price, the open balance only means the
        un-invoiced portion has not been billed yet — not an accounting error.

        ``stj_quantity`` should be the sum of qty_item from STJ raw lines that post to
        2103006 for this item.  When provided it is preferred over gr_quantity for the
        STJ unit-price calculation because gr_quantity may include stock moves that did
        not produce a 2103006 credit (e.g. a third picking with no STJ yet), which
        would dilute the implied unit price and cause a false mismatch.

        Returns False (i.e. treat as potential Case 2) when:
        - no residual to explain
        - quantities are zero/unknown
        - 2103006 account row not found on item
        - STJ or Bill side is zero
        - unit price difference >= 1 IDR/unit
        """
        if abs(problem_2103006) < 0.01:
            return False
        if bill_quantity < 0.01:
            return False
        acct_2103006 = next(
            (
                ar
                for ar in (getattr(item_row, "account_rows", None) or [])
                if normalize_text(getattr(ar, "code", "")).upper() == "2103006"
            ),
            None,
        )
        if acct_2103006 is None:
            return False
        stj_cr = _round2(getattr(acct_2103006, "credit", 0.0))
        bill_dr = _round2(getattr(acct_2103006, "debit", 0.0))
        if stj_cr < 0.01 or bill_dr < 0.01:
            return False
        # Prefer stj_quantity (from raw STJ lines hitting 2103006) over gr_quantity.
        # gr_quantity is the aggregate across ALL stock moves in the merged cycle,
        # and can include moves for which no 2103006 credit was posted (e.g. a
        # picking that has no STJ yet), which would dilute the implied STJ unit price
        # and cause a spurious mismatch.  Fall back to gr_quantity only when
        # stj_quantity is not available.
        qty_for_stj_unit = stj_quantity if stj_quantity >= 0.01 else gr_quantity
        if qty_for_stj_unit < 0.01:
            return False
        stj_unit = stj_cr / qty_for_stj_unit
        bill_unit = bill_dr / bill_quantity
        # Tolerance 1 IDR/unit: prices consistent → residual is pure GRNI.
        return abs(stj_unit - bill_unit) < 1.0

    @staticmethod
    def _pcb_item_account_balance_by_type(item_row: SvlDashboardCycleItemRow, account_type: str) -> float:
        clean_account_type = normalize_text(account_type).lower()
        if not clean_account_type:
            return 0.0
        return _round2(
            sum(
                _round2(getattr(account_row, "net_balance", 0.0))
                for account_row in list(getattr(item_row, "account_rows", None) or [])
                if normalize_text(getattr(account_row, "account_type", "")).lower() == clean_account_type
            )
        )

    @staticmethod
    def _pcb_uom_name(uom_id: int, uom_info_by_id: dict[int, dict[str, Any]] | None) -> str:
        clean_uom_id = int(uom_id or 0)
        if clean_uom_id <= 0:
            return ""
        info = dict((uom_info_by_id or {}).get(clean_uom_id) or {})
        return (
            normalize_text(info.get("display_name"))
            or normalize_text(info.get("name"))
            or _many2one_name(info.get("category_id"))
            or f"UoM #{clean_uom_id}"
        )

    @staticmethod
    def _pcb_uom_root_key(uom_id: int, uom_info_by_id: dict[int, dict[str, Any]] | None) -> str:
        clean_uom_id = int(uom_id or 0)
        if clean_uom_id <= 0:
            return ""
        info = dict((uom_info_by_id or {}).get(clean_uom_id) or {})
        parent_path = normalize_text(info.get("parent_path"))
        if parent_path:
            root = normalize_text(parent_path).strip("/").split("/", 1)[0]
            if root:
                return f"path:{root}"
        category_id = _many2one_id(info.get("category_id"))
        if category_id > 0:
            return f"category:{category_id}"
        return ""

    @classmethod
    def _pcb_uom_roots_mismatch(
        cls,
        *,
        source_uom_id: int,
        product_uom_id: int,
        uom_info_by_id: dict[int, dict[str, Any]] | None,
    ) -> bool:
        clean_source_id = int(source_uom_id or 0)
        clean_product_id = int(product_uom_id or 0)
        if clean_source_id <= 0 or clean_product_id <= 0 or clean_source_id == clean_product_id:
            return False
        source_root = cls._pcb_uom_root_key(clean_source_id, uom_info_by_id)
        product_root = cls._pcb_uom_root_key(clean_product_id, uom_info_by_id)
        if source_root and product_root:
            return source_root != product_root
        # Schema fallback: when root metadata is unavailable, only let an extreme
        # quantity scale factor promote the item to Case 9.  The caller enforces
        # that threshold.
        return clean_source_id != clean_product_id

    @staticmethod
    def _pcb_svl_totals_for_stock_moves(
        *,
        stock_move_ids: list[int],
        svl_rows_by_stock_move_id: dict[int, list[dict[str, Any]]] | None,
    ) -> tuple[float, float, list[int]]:
        total_qty = 0.0
        total_value = 0.0
        svl_ids: list[int] = []
        for stock_move_id in [int(value or 0) for value in list(stock_move_ids or []) if int(value or 0) > 0]:
            for svl_row in list((svl_rows_by_stock_move_id or {}).get(stock_move_id, []) or []):
                svl_id = int(svl_row.get("id") or 0)
                if svl_id > 0:
                    svl_ids.append(svl_id)
                total_qty = _round2(total_qty + _round2(svl_row.get("quantity")))
                total_value = _round2(total_value + _round2(svl_row.get("value")))
        return (_round2(total_qty), _round2(total_value), sorted(set(svl_ids)))

    def _populate_pcb_receipt_recovery_evidence_for_cycle(self, *, cycle, trace) -> None:
        """Read existing receipt layers; standard cost is separate review evidence."""
        svl_map = trace.get("svl_rows_by_stock_move_id") or {}
        move_map = trace.get("stock_move_rows_by_id") or {}
        picking_ids = set(cycle.picking_ids or [cycle.picking_id])
        for item in cycle.item_rows:
            move_ids = sorted({int(v) for v in item.stock_move_ids if int(v) > 0
                and _many2one_id((move_map.get(int(v)) or {}).get("picking_id")) in picking_ids
                and not _many2one_id((move_map.get(int(v)) or {}).get("origin_returned_move_id"))})
            layers = [row for mid in move_ids for row in svl_map.get(mid, [])]
            missing = [row for row in layers if float(row.get("quantity") or 0) > 0
                and float(row.get("value") or 0) > 0 and not _many2one_id(row.get("account_move_id"))]
            item.receipt_recovery_evidence = {
                "flow": "normal_receipt", "stock_move_ids": move_ids, "has_return": bool(cycle.has_return_picking),
                "missing_svl_ids": sorted({int(row["id"]) for row in missing}),
                "missing_value": _round2(sum(float(row.get("value") or 0) for row in missing)),
                "missing_qty": sum(float(row.get("quantity") or 0) for row in missing),
                "receipt_qty": sum(float(row.get("quantity") or 0) for row in layers),
                "actual_receipt_value": _round2(sum(float(row.get("value") or 0) for row in layers)),
                "complete": bool(move_ids) and all(bool(svl_map.get(mid)) for mid in move_ids),
                "allocation_basis": "SVL per stock move; cost offset requires revaluation/usage review",
            }
            if layers:
                item.svl_zero_at_gr = abs(item.receipt_recovery_evidence["actual_receipt_value"]) < 0.01

    @classmethod
    def _pcb_case9_target_from_bill_or_po(
        cls,
        *,
        product_id: int,
        purchase_line_id: int,
        bill_move_ids: list[int],
        bill_line_rows_by_product: dict[int, list[dict[str, Any]]] | None,
        bill_rows_by_id: dict[int, dict[str, Any]] | None,
        purchase_line_rows_by_id: dict[int, dict[str, Any]] | None,
        standard_price: float,
    ) -> dict[str, Any]:
        clean_product_id = int(product_id or 0)
        clean_purchase_line_id = int(purchase_line_id or 0)
        bill_qty = 0.0
        bill_value = 0.0
        bill_uom_id = 0
        bill_line_ids: list[int] = []
        clean_bill_move_ids = {
            int(move_id or 0)
            for move_id in list(bill_move_ids or [])
            if int(move_id or 0) > 0
        }
        for bill_line in list((bill_line_rows_by_product or {}).get(clean_product_id, []) or []):
            line_move_id = _many2one_id(bill_line.get("move_id"))
            if clean_bill_move_ids and line_move_id not in clean_bill_move_ids:
                continue
            if not cls._is_vendor_bill_move(line_move_id, bill_rows_by_id=bill_rows_by_id):
                continue
            line_purchase_line_id = _many2one_id(bill_line.get("purchase_line_id"))
            if clean_purchase_line_id > 0 and line_purchase_line_id > 0 and line_purchase_line_id != clean_purchase_line_id:
                continue
            line_qty = abs(_round2(bill_line.get("quantity")))
            line_value = abs(_round2(bill_line.get("price_subtotal")))
            if line_value < 0.01:
                line_value = abs(_round2(bill_line.get("balance")))
            if line_qty < 0.0001 and line_value < 0.01:
                continue
            bill_qty = _round2(bill_qty + line_qty)
            bill_value = _round2(bill_value + line_value)
            if bill_uom_id <= 0:
                bill_uom_id = _many2one_id(bill_line.get("product_uom_id"))
            bill_line_id = int(bill_line.get("id") or 0)
            if bill_line_id > 0:
                bill_line_ids.append(bill_line_id)
        if bill_qty > 0.0001:
            expected_value = _round2(bill_qty * standard_price) if standard_price > 0.0 else bill_value
            return {
                "source": "bill_standard_price" if standard_price > 0.0 else "bill_subtotal",
                "source_qty": bill_qty,
                "source_value": bill_value,
                "source_uom_id": bill_uom_id,
                "expected_value": expected_value,
                "bill_line_ids": sorted(set(bill_line_ids)),
            }

        po_row = dict((purchase_line_rows_by_id or {}).get(clean_purchase_line_id) or {})
        po_qty = abs(_round2(po_row.get("product_qty") or po_row.get("qty_received")))
        po_uom_id = _many2one_id(po_row.get("product_uom")) or _many2one_id(po_row.get("product_uom_id"))
        po_subtotal = abs(_round2(po_row.get("price_subtotal")))
        if po_subtotal < 0.01:
            po_subtotal = _round2(po_qty * abs(_round2(po_row.get("price_unit"))))
        expected_value = _round2(po_qty * standard_price) if standard_price > 0.0 and po_qty > 0.0001 else po_subtotal
        return {
            "source": "po_standard_price" if standard_price > 0.0 and po_qty > 0.0001 else "po_subtotal",
            "source_qty": po_qty,
            "source_value": po_subtotal,
            "source_uom_id": po_uom_id,
            "expected_value": expected_value,
            "bill_line_ids": [],
        }

    @staticmethod
    def _build_pcb_case8_case9_context(trace: dict[str, Any]) -> dict[str, Any]:
        stock_move_rows_by_id: dict[int, dict[str, Any]] = {
            int(key): dict(value or {})
            for key, value in dict(trace.get("stock_move_rows_by_id") or {}).items()
            if int(key or 0) > 0
        }
        svl_rows_by_stock_move_id: dict[int, list[dict[str, Any]]] = {
            int(key): list(value or [])
            for key, value in dict(trace.get("svl_rows_by_stock_move_id") or {}).items()
            if int(key or 0) > 0
        }
        bill_line_rows_by_product: dict[int, list[dict[str, Any]]] = {
            int(key): list(value or [])
            for key, value in dict(trace.get("bill_line_rows_by_product") or {}).items()
            if int(key or 0) > 0
        }
        bill_rows_by_id: dict[int, dict[str, Any]] = {
            int(key): dict(value or {})
            for key, value in dict(trace.get("bill_rows_by_id") or {}).items()
            if int(key or 0) > 0
        }
        return_picking_pairs = [
            (int(return_pid or 0), int(original_pid or 0))
            for return_pid, original_pid in list(trace.get("return_picking_pairs") or [])
            if int(return_pid or 0) > 0 and int(original_pid or 0) > 0
        ]
        stock_moves_by_picking_id: dict[int, list[tuple[int, dict[str, Any]]]] = defaultdict(list)
        for move_id, stock_move_row in stock_move_rows_by_id.items():
            picking_id = _many2one_id(stock_move_row.get("picking_id"))
            if picking_id > 0:
                stock_moves_by_picking_id[picking_id].append((int(move_id), stock_move_row))
        return_picking_ids_by_original_picking: dict[int, set[int]] = defaultdict(set)
        for return_pid, original_pid in return_picking_pairs:
            return_picking_ids_by_original_picking[int(original_pid)].add(int(return_pid))
        return {
            "stock_move_rows_by_id": stock_move_rows_by_id,
            "stock_moves_by_picking_id": dict(stock_moves_by_picking_id),
            "svl_rows_by_stock_move_id": svl_rows_by_stock_move_id,
            "bill_line_rows_by_product": bill_line_rows_by_product,
            "bill_rows_by_id": bill_rows_by_id,
            "purchase_line_rows_by_id": dict(trace.get("purchase_line_rows_by_id") or {}),
            "uom_info_by_id": dict(trace.get("uom_info_by_id") or {}),
            "return_picking_pairs": return_picking_pairs,
            "return_picking_ids_by_original_picking": dict(return_picking_ids_by_original_picking),
        }

    def _populate_pcb_case8_case9_evidence_for_cycle(
        self,
        *,
        cycle: SvlDashboardPurchaseCycle,
        trace: dict[str, Any],
        product_info_map: dict[int, dict[str, Any]],
        case89_context: dict[str, Any] | None = None,
    ) -> None:
        context = case89_context or self._build_pcb_case8_case9_context(trace)
        stock_moves_by_picking_id = context.get("stock_moves_by_picking_id") or {}
        svl_rows_by_stock_move_id = context.get("svl_rows_by_stock_move_id") or {}
        bill_line_rows_by_product = context.get("bill_line_rows_by_product") or {}
        bill_rows_by_id = context.get("bill_rows_by_id") or {}
        purchase_line_rows_by_id = context.get("purchase_line_rows_by_id") or {}
        uom_info_by_id = context.get("uom_info_by_id") or {}
        return_picking_ids_by_original_picking = context.get("return_picking_ids_by_original_picking") or {}
        group_picking_ids = {
            int(value or 0)
            for value in list(getattr(cycle, "picking_ids", None) or [getattr(cycle, "picking_id", 0)])
            if int(value or 0) > 0
        }
        return_picking_ids: set[int] = set()
        for original_picking_id in group_picking_ids:
            return_picking_ids.update(
                int(value or 0)
                for value in return_picking_ids_by_original_picking.get(original_picking_id, set())
                if int(value or 0) > 0
            )
        bill_move_ids = [
            int(value or 0)
            for value in list(getattr(cycle, "bill_move_ids", None) or [])
            if int(value or 0) > 0
        ]

        receipt_moves_by_product: dict[int, list[tuple[int, dict[str, Any]]]] = defaultdict(list)
        return_moves_by_product: dict[int, list[tuple[int, dict[str, Any]]]] = defaultdict(list)
        for picking_id in group_picking_ids:
            for move_id, stock_move_row in list(stock_moves_by_picking_id.get(picking_id, []) or []):
                product_id = _many2one_id(stock_move_row.get("product_id"))
                if product_id <= 0:
                    continue
                if _many2one_id(stock_move_row.get("origin_returned_move_id")) <= 0:
                    receipt_moves_by_product[product_id].append((int(move_id), stock_move_row))
        for return_picking_id in return_picking_ids:
            for move_id, stock_move_row in list(stock_moves_by_picking_id.get(return_picking_id, []) or []):
                product_id = _many2one_id(stock_move_row.get("product_id"))
                if product_id <= 0:
                    continue
                return_moves_by_product[product_id].append((int(move_id), stock_move_row))
        if not receipt_moves_by_product and not return_moves_by_product:
            for item_row in list(getattr(cycle, "item_rows", None) or []):
                item_row.case8_evidence = {}
                item_row.case9_evidence = {}
            return

        for item_row in list(getattr(cycle, "item_rows", None) or []):
            item_row.case8_evidence = {}
            item_row.case9_evidence = {}
            product_id = int(getattr(item_row, "product_id", 0) or 0)
            product_info = dict(product_info_map.get(product_id) or {})
            standard_price = _round2(getattr(item_row, "standard_price", 0.0) or product_info.get("standard_price"))

            # Case 9: non-inline source UoM -> product/SVL UoM with an extreme scale factor.
            case9_candidates: list[dict[str, Any]] = []
            for stock_move_id, stock_move_row in receipt_moves_by_product.get(product_id, []):
                purchase_line_id = _many2one_id(stock_move_row.get("purchase_line_id"))
                stock_qty = abs(_round2(stock_move_row.get("product_qty") or stock_move_row.get("quantity")))
                product_uom_id = _many2one_id(stock_move_row.get("product_uom")) or _many2one_id(stock_move_row.get("product_uom_id"))
                if product_uom_id <= 0:
                    product_uom_id = int(product_info.get("uom_id") or 0)
                target = self._pcb_case9_target_from_bill_or_po(
                    product_id=product_id,
                    purchase_line_id=purchase_line_id,
                    bill_move_ids=bill_move_ids,
                    bill_line_rows_by_product=bill_line_rows_by_product,
                    bill_rows_by_id=bill_rows_by_id,
                    purchase_line_rows_by_id=purchase_line_rows_by_id,
                    standard_price=standard_price,
                )
                source_qty = abs(_round2(target.get("source_qty")))
                source_uom_id = int(target.get("source_uom_id") or 0)
                expected_value = abs(_round2(target.get("expected_value")))
                if source_qty <= 0.0001 or stock_qty <= 0.0001 or expected_value <= 0.01:
                    continue
                scale_factor = abs(stock_qty / source_qty)
                if not (scale_factor >= 10.0 or (0.0 < scale_factor <= 0.1)):
                    continue
                if not self._pcb_uom_roots_mismatch(
                    source_uom_id=source_uom_id,
                    product_uom_id=product_uom_id,
                    uom_info_by_id=uom_info_by_id,
                ):
                    continue
                _svl_qty, svl_value, svl_ids = self._pcb_svl_totals_for_stock_moves(
                    stock_move_ids=[stock_move_id],
                    svl_rows_by_stock_move_id=svl_rows_by_stock_move_id,
                )
                actual_value = abs(_round2(svl_value))
                if actual_value <= 0.01:
                    actual_value = abs(_round2(stock_qty * _round2(stock_move_row.get("price_unit"))))
                value_gap = _round2(actual_value - expected_value)
                if abs(value_gap) < max(1000.0, abs(expected_value) * 0.05):
                    continue
                case9_candidates.append(
                    {
                        "stock_move_id": int(stock_move_id),
                        "stock_move_ids": [int(stock_move_id)],
                        "purchase_line_id": int(purchase_line_id or 0),
                        "bill_line_ids": list(target.get("bill_line_ids") or []),
                        "source_qty": source_qty,
                        "system_qty": stock_qty,
                        "corrected_qty": source_qty,
                        "scale_factor": _round2(scale_factor),
                        "source_uom_id": int(source_uom_id or 0),
                        "source_uom_name": self._pcb_uom_name(source_uom_id, uom_info_by_id),
                        "product_uom_id": int(product_uom_id or 0),
                        "product_uom_name": self._pcb_uom_name(product_uom_id, uom_info_by_id),
                        "expected_value": expected_value,
                        "actual_svl_value": actual_value,
                        "value_gap": value_gap,
                        "target_basis": normalize_text(target.get("source")),
                        "svl_ids": svl_ids,
                    }
                )
            if case9_candidates:
                total_actual_value = _round2(sum(float(candidate.get("actual_svl_value") or 0.0) for candidate in case9_candidates))
                total_expected_value = _round2(sum(float(candidate.get("expected_value") or 0.0) for candidate in case9_candidates))
                total_gap = _round2(total_actual_value - total_expected_value)
                problem_2103006 = self._item_row_account_balance(item_row, "2103006")
                problem_1108099 = self._item_row_account_balance(item_row, "1108099")
                hpp_balance = self._pcb_item_account_balance_by_type(item_row, "expense_direct_cost")
                inventory_balance = self._item_row_account_balance(
                    item_row,
                    normalize_text(product_info.get("valuation_account_code")).upper(),
                )
                external_clearing_amount = abs(_round2(getattr(item_row, "external_clearing_amount", 0.0)))
                downstream_refs = [
                    normalize_text(ref)
                    for ref in list(getattr(item_row, "external_clearing_refs", None) or [])
                    if normalize_text(ref)
                ]
                correction_stj_refs = [
                    normalize_text(ref)
                    for ref in list(getattr(item_row, "correction_stj_refs", None) or [])
                    if normalize_text(ref)
                ]
                has_correction_stj_evidence = bool(correction_stj_refs) or any(
                    int(move_id or 0) > 0
                    for move_id in list(getattr(item_row, "correction_stj_move_ids", None) or [])
                )
                holder_basis = "review_only"
                correction_amount = abs(total_gap)
                if hpp_balance < -0.01 and external_clearing_amount >= 0.01:
                    holder_basis = "case2_downstream_clearing"
                    correction_amount = abs(_round2(hpp_balance))
                elif (
                    hpp_balance < -0.01
                    and inventory_balance > 0.01
                    and abs(problem_2103006) < 0.01
                    and has_correction_stj_evidence
                ):
                    holder_basis = "case2_downstream_clearing"
                    correction_amount = abs(_round2(hpp_balance))
                elif abs(problem_2103006) >= 0.01 and hpp_balance > 0.01:
                    holder_basis = "open_suspense_revalued"
                    correction_amount = abs(total_gap)
                elif abs(problem_2103006) >= 0.01:
                    holder_basis = "open_suspense_inventory"
                    correction_amount = abs(total_gap)
                if holder_basis == "case2_downstream_clearing" and not downstream_refs and correction_stj_refs:
                    downstream_refs = correction_stj_refs
                item_row.case9_evidence = {
                    "stock_move_ids": sorted({move_id for candidate in case9_candidates for move_id in list(candidate.get("stock_move_ids") or [])}),
                    "purchase_line_ids": sorted({int(candidate.get("purchase_line_id") or 0) for candidate in case9_candidates if int(candidate.get("purchase_line_id") or 0) > 0}),
                    "bill_line_ids": sorted({line_id for candidate in case9_candidates for line_id in list(candidate.get("bill_line_ids") or []) if int(line_id or 0) > 0}),
                    "svl_ids": sorted({svl_id for candidate in case9_candidates for svl_id in list(candidate.get("svl_ids") or []) if int(svl_id or 0) > 0}),
                    "source_uom_id": int(case9_candidates[0].get("source_uom_id") or 0),
                    "source_uom_name": normalize_text(case9_candidates[0].get("source_uom_name")),
                    "product_uom_id": int(case9_candidates[0].get("product_uom_id") or 0),
                    "product_uom_name": normalize_text(case9_candidates[0].get("product_uom_name")),
                    "source_qty": _round2(sum(float(candidate.get("source_qty") or 0.0) for candidate in case9_candidates)),
                    "system_qty": _round2(sum(float(candidate.get("system_qty") or 0.0) for candidate in case9_candidates)),
                    "corrected_qty": _round2(sum(float(candidate.get("corrected_qty") or 0.0) for candidate in case9_candidates)),
                    "scale_factor": _round2(max(abs(float(candidate.get("scale_factor") or 0.0)) for candidate in case9_candidates)),
                    "expected_value": total_expected_value,
                    "actual_svl_value": total_actual_value,
                    "value_gap": total_gap,
                    "correction_amount": _round2(correction_amount),
                    "target_basis": normalize_text(case9_candidates[0].get("target_basis")),
                    "holder_basis": holder_basis,
                    "downstream_refs": downstream_refs,
                    "inventory_balance": inventory_balance,
                    "hpp_balance": hpp_balance,
                    "suspend_balance": problem_2103006,
                    # True jika UoM mismatch sudah tidak menimbulkan saldo problem nyata:
                    # Kasus A — barang masih di persediaan: nilai persediaan == nilai SVL aktual,
                    #   2103006 = 0, HPP = 0 (belum dijual/consumed). Mismatch hanya cosmetic.
                    # Kasus B — barang sudah consumed/sold: persediaan = 0, 2103006 = 0,
                    #   HPP net = bill amount (correction STJ sudah membersihkan selisih),
                    #   ada bukti correction STJ. Cycle dianggap "selesai cukup".
                    # Kedua kasus tidak perlu repair JE tambahan — cukup masuk partial/healthy.
                    "uom_value_economically_correct": (
                        (
                            # Kasus A: barang masih di persediaan, mismatch cosmetic
                            holder_basis == "review_only"
                            and abs(_round2(hpp_balance)) < 0.01
                            and abs(_round2(problem_2103006)) < 0.01
                            and abs(_round2(inventory_balance)) >= 0.01
                            and abs(_round2(inventory_balance - total_actual_value)) < max(1.0, abs(total_actual_value) * 0.001)
                        )
                        or (
                            # Kasus B: barang sudah consumed, selisih sudah diselesaikan via correction STJ.
                            # Guard: kedua problem code (2103006 dan 1108099) harus = 0 agar tidak
                            # meng-healthy-kan cycle yang correction STJ-nya hanya parsial.
                            holder_basis == "review_only"
                            and abs(_round2(problem_2103006)) < 0.01
                            and abs(_round2(problem_1108099)) < 0.01
                            and abs(_round2(inventory_balance)) < 0.01
                            and has_correction_stj_evidence
                        )
                    ),
                }

            # Case 8: return-to-vendor value does not follow the receipt layer value proportion.
            case8_candidates: list[dict[str, Any]] = []
            receipt_move_by_id = {move_id: row for move_id, row in receipt_moves_by_product.get(product_id, [])}
            for return_move_id, return_move_row in return_moves_by_product.get(product_id, []):
                origin_move_id = _many2one_id(return_move_row.get("origin_returned_move_id"))
                if origin_move_id <= 0:
                    origin_move_id = next(
                        (
                            move_id
                            for move_id, receipt_move_row in receipt_move_by_id.items()
                            if return_move_id in _many2many_ids(receipt_move_row.get("returned_move_ids"))
                        ),
                        0,
                    )
                if origin_move_id <= 0 or origin_move_id not in receipt_move_by_id:
                    continue
                receipt_qty, receipt_value, receipt_svl_ids = self._pcb_svl_totals_for_stock_moves(
                    stock_move_ids=[origin_move_id],
                    svl_rows_by_stock_move_id=svl_rows_by_stock_move_id,
                )
                return_qty, return_value, return_svl_ids = self._pcb_svl_totals_for_stock_moves(
                    stock_move_ids=[return_move_id],
                    svl_rows_by_stock_move_id=svl_rows_by_stock_move_id,
                )
                if abs(receipt_qty) <= 0.0001 or abs(receipt_value) <= 0.01 or abs(return_qty) <= 0.0001:
                    continue
                return_ratio = min(abs(return_qty) / abs(receipt_qty), 1.0)
                expected_return_value = _round2(abs(receipt_value) * return_ratio)
                actual_return_value = abs(_round2(return_value))
                return_gap = _round2(actual_return_value - expected_return_value)
                if abs(return_gap) < max(1000.0, expected_return_value * 0.05):
                    continue
                case8_candidates.append(
                    {
                        "receipt_stock_move_id": origin_move_id,
                        "return_stock_move_id": return_move_id,
                        "receipt_qty": abs(_round2(receipt_qty)),
                        "return_qty": abs(_round2(return_qty)),
                        "return_ratio": _round2(return_ratio),
                        "expected_return_value": expected_return_value,
                        "actual_return_value": actual_return_value,
                        "return_gap": return_gap,
                        "receipt_svl_ids": receipt_svl_ids,
                        "return_svl_ids": return_svl_ids,
                        "return_picking_id": _many2one_id(return_move_row.get("picking_id")),
                    }
                )
            if case8_candidates:
                total_expected = _round2(sum(float(candidate.get("expected_return_value") or 0.0) for candidate in case8_candidates))
                total_actual = _round2(sum(float(candidate.get("actual_return_value") or 0.0) for candidate in case8_candidates))
                total_gap = _round2(total_actual - total_expected)
                full_return = all(float(candidate.get("return_ratio") or 0.0) >= 0.999 for candidate in case8_candidates)
                bill_value = abs(
                    _round2(
                        sum(
                            _round2(bill_line.get("price_subtotal")) or abs(_round2(bill_line.get("balance")))
                            for bill_line in list(bill_line_rows_by_product.get(product_id, []) or [])
                            if _many2one_id(bill_line.get("move_id")) in set(bill_move_ids)
                        )
                    )
                )
                item_row.case8_evidence = {
                    "case_key": "case8a" if full_return else "case8b",
                    "receipt_stock_move_ids": sorted({int(candidate.get("receipt_stock_move_id") or 0) for candidate in case8_candidates if int(candidate.get("receipt_stock_move_id") or 0) > 0}),
                    "return_stock_move_ids": sorted({int(candidate.get("return_stock_move_id") or 0) for candidate in case8_candidates if int(candidate.get("return_stock_move_id") or 0) > 0}),
                    "return_picking_ids": sorted({int(candidate.get("return_picking_id") or 0) for candidate in case8_candidates if int(candidate.get("return_picking_id") or 0) > 0}),
                    "receipt_svl_ids": sorted({svl_id for candidate in case8_candidates for svl_id in list(candidate.get("receipt_svl_ids") or []) if int(svl_id or 0) > 0}),
                    "return_svl_ids": sorted({svl_id for candidate in case8_candidates for svl_id in list(candidate.get("return_svl_ids") or []) if int(svl_id or 0) > 0}),
                    "receipt_qty": _round2(sum(float(candidate.get("receipt_qty") or 0.0) for candidate in case8_candidates)),
                    "return_qty": _round2(sum(float(candidate.get("return_qty") or 0.0) for candidate in case8_candidates)),
                    "return_ratio": _round2(max(float(candidate.get("return_ratio") or 0.0) for candidate in case8_candidates)),
                    "expected_return_value": total_expected,
                    "actual_return_value": total_actual,
                    "return_gap": total_gap,
                    "correction_amount": abs(total_gap),
                    "bill_value": bill_value,
                    "full_return": full_return,
                    "ambiguous": bool(full_return and bill_value >= 0.01),
                }

    @staticmethod
    def _pcb_case_group_label(case_key: str) -> str:
        return {
            "case1": "Group 1: STJ - Bill Miss Match (Clearing - Suspend)",
            "case2": "Group 2: STJ - Bill Price Diff (Suspend - Suspend)",
            "case3": "Group 3: STJ - Bill Hit Expenses (Clearing - Expenses)",
            "case4": "Group 4: STJ - Bill Hit Expenses (Suspend - Expenses)",
            "case5": "Group 5: Pemulihan SVL - Suspense",
            "case6": "Group 6: Pemulihan SVL - Bill Expense",
            "case8a": "Group 8A: Full Return Value Mismatch",
            "case8b": "Group 8B: Partial Return Value Mismatch",
            "case9": "Group 9: UoM Scale Mismatch",
            "edge_partial_bill": "Edge Case: Partial Bill",
            "edge_return_no_credit_memo": "Edge Case: Return Without Credit Memo",
            "edge_stj_corrupt": "Edge Case: STJ Corrupt",
            "case_lainnya": "Case Lainnya",
        }.get(normalize_text(case_key).lower(), "Case Lainnya")

    @staticmethod
    def _pcb_item_matches_raw_line(item_row: SvlDashboardCycleItemRow, raw_line: dict[str, Any]) -> bool:
        item_code = normalize_text(getattr(item_row, "default_code", "")).upper()
        item_name = normalize_text(getattr(item_row, "product_name", "")).upper()
        raw_code = normalize_text(raw_line.get("kode_item")).upper()
        raw_name = normalize_text(raw_line.get("nama_item")).upper()
        if item_code and raw_code:
            return item_code == raw_code
        if item_code and raw_name:
            return raw_name == item_name
        if item_name and raw_name:
            return item_name == raw_name
        return False

    @classmethod
    def _pcb_item_raw_lines(
        cls,
        *,
        raw_lines: list[dict[str, Any]],
        item_row: SvlDashboardCycleItemRow,
        jenis: str = "",
    ) -> list[dict[str, Any]]:
        clean_jenis = normalize_text(jenis).upper()
        return [
            line
            for line in list(raw_lines or [])
            if (not clean_jenis or normalize_text(line.get("jenis")).upper() == clean_jenis)
            and cls._pcb_item_matches_raw_line(item_row, line)
        ]

    @classmethod
    def _pcb_item_stj_links_from_raw_lines(
        cls,
        *,
        raw_lines: list[dict[str, Any]],
        item_row: SvlDashboardCycleItemRow,
        move_info_map: dict[int, dict[str, Any]] | None,
    ) -> tuple[list[int], list[str]]:
        stj_refs = _sorted_unique_text(
            normalize_text(line.get("kode_transaksi"))
            for line in cls._pcb_item_raw_lines(raw_lines=list(raw_lines or []), item_row=item_row, jenis="STJ")
            if normalize_text(line.get("kode_transaksi"))
        )
        if not stj_refs:
            return ([], [])
        resolved_move_ids = sorted(
            {
                int(move_id or 0)
                for move_id, move_info in dict(move_info_map or {}).items()
                if int(move_id or 0) > 0
                and normalize_text((move_info or {}).get("name")) in stj_refs
            }
        )
        return (resolved_move_ids, stj_refs)

    @classmethod
    def _pcb_item_move_lines(
        cls,
        *,
        lines_by_move: dict[int, list[dict[str, Any]]] | None,
        account_info_map: dict[int, dict[str, Any]] | None,
        move_ids: list[int],
        item_row: SvlDashboardCycleItemRow,
        jenis: str,
    ) -> list[dict[str, Any]]:
        if not lines_by_move or not account_info_map:
            return []
        item_product_id = int(getattr(item_row, "product_id", 0) or 0)
        item_purchase_line_ids = {
            int(value or 0)
            for value in list(getattr(item_row, "purchase_line_ids", None) or [])
            if int(value or 0) > 0
        }
        clean_jenis = normalize_text(jenis).upper()
        derived_lines: list[dict[str, Any]] = []
        for move_id in [int(value or 0) for value in list(move_ids or []) if int(value or 0) > 0]:
            for line in list((lines_by_move or {}).get(move_id, []) or []):
                line_product_id = _many2one_id(line.get("product_id"))
                line_purchase_line_id = _many2one_id(line.get("purchase_line_id"))
                if item_product_id > 0 and line_product_id == item_product_id:
                    matched = True
                elif item_purchase_line_ids and line_purchase_line_id in item_purchase_line_ids:
                    matched = True
                else:
                    matched = False
                if not matched:
                    continue
                account_id = _many2one_id(line.get("account_id"))
                account_info = dict((account_info_map or {}).get(account_id) or {})
                derived_lines.append(
                    {
                        "jenis": clean_jenis,
                        "akun_code": normalize_text(account_info.get("code")).upper(),
                        "akun_name": normalize_text(account_info.get("name")),
                        "tipe_akun": normalize_text(account_info.get("account_type")).lower(),
                        "kode_item": normalize_text(getattr(item_row, "default_code", "")),
                        "nama_item": normalize_text(getattr(item_row, "product_name", "")),
                    }
                )
        return derived_lines

    @classmethod
    def _classify_pcb_item_case(
        cls,
        *,
        cycle: SvlDashboardPurchaseCycle,
        item_row: SvlDashboardCycleItemRow,
        product_info_map: dict[int, dict[str, Any]],
        lines_by_move: dict[int, list[dict[str, Any]]] | None = None,
        account_info_map: dict[int, dict[str, Any]] | None = None,
        cycle_bill_move_ids: list[int] | None = None,
    ) -> dict[str, Any]:
        product_id = int(getattr(item_row, "product_id", 0) or 0)
        product_info = product_info_map.get(product_id) or {}
        expense_account_code = normalize_text(product_info.get("expense_account_code")).upper()
        raw_bill_lines = cls._pcb_item_raw_lines(raw_lines=list(getattr(cycle, "raw_lines", None) or []), item_row=item_row, jenis="BILL")
        raw_stj_lines = cls._pcb_item_raw_lines(raw_lines=list(getattr(cycle, "raw_lines", None) or []), item_row=item_row, jenis="STJ")
        if not raw_bill_lines:
            raw_bill_lines = cls._pcb_item_move_lines(
                lines_by_move=lines_by_move,
                account_info_map=account_info_map,
                move_ids=list(cycle_bill_move_ids or getattr(item_row, "bill_move_ids", None) or []),
                item_row=item_row,
                jenis="BILL",
            )
        if not raw_stj_lines:
            raw_stj_lines = cls._pcb_item_move_lines(
                lines_by_move=lines_by_move,
                account_info_map=account_info_map,
                move_ids=list(getattr(item_row, "stj_move_ids", None) or []),
                item_row=item_row,
                jenis="STJ",
            )
        if not raw_stj_lines:
            # Fallback: correction STJ (misalnya "Correction New JE + Relink SVL") tidak punya
            # stj_move_ids tetapi ada di correction_stj_move_ids. Ambil line-nya untuk deteksi
            # has_stj_clearing_line / has_stj_suspend_line.
            raw_stj_lines = cls._pcb_item_move_lines(
                lines_by_move=lines_by_move,
                account_info_map=account_info_map,
                move_ids=list(getattr(item_row, "correction_stj_move_ids", None) or []),
                item_row=item_row,
                jenis="STJ",
            )
        problem_2103006 = cls._item_row_account_balance(item_row, "2103006")
        problem_1108099 = cls._item_row_account_balance(item_row, "1108099")
        has_problem_2103006 = abs(problem_2103006) >= 0.01
        has_problem_1108099 = abs(problem_1108099) >= 0.01
        has_bill = bool(getattr(item_row, "has_item_bill", False) or list(getattr(item_row, "bill_refs", None) or []))
        svl_zero_at_gr = bool(getattr(item_row, "svl_zero_at_gr", False))
        has_item_stj = bool(getattr(item_row, "has_item_stj", False))
        has_correction_stj = bool(
            list(getattr(item_row, "correction_stj_move_ids", None) or [])
            or list(getattr(item_row, "correction_stj_refs", None) or [])
        )
        bill_quantity = _round2(getattr(item_row, "bill_quantity", 0.0))
        gr_quantity = _round2(getattr(item_row, "gr_quantity", 0.0))
        standard_price = _round2(getattr(item_row, "standard_price", 0.0) or product_info.get("standard_price"))
        # True hanya jika item ini secara eksplisit sudah di-bill pada purchase line-nya sendiri.
        # qty_invoiced = 0 (vendor belum menagih) → has_exact_bill_for_item = False → GRNI state.
        # Pakai has_bill (sudah diekstrak di atas) bukan has_item_bill yang bukan variabel sendiri.
        has_exact_bill_for_item = bool(has_bill or bill_quantity > 0.01)

        has_bill_suspend_line = any(normalize_text(line.get("akun_code")).upper() == "2103006" for line in raw_bill_lines)
        _BILL_EXPENSE_TYPE_TOKENS = frozenset({"expense_direct_cost", "expense"})
        _BILL_NON_EXPENSE_CODES = frozenset({"2101002", "2103006", "1108099", "11120003"})
        has_bill_expense_line = any(
            normalize_text(line.get("tipe_akun")).lower() in _BILL_EXPENSE_TYPE_TOKENS
            and normalize_text(line.get("akun_code")).upper() not in _PCB_COGS_VARIANCE_CODES
            and normalize_text(line.get("akun_code")).upper() not in _BILL_NON_EXPENSE_CODES
            for line in raw_bill_lines
        )
        if has_bill_expense_line:
            bill_hit_role = "expense"
        elif has_bill_suspend_line or (has_problem_2103006 and has_exact_bill_for_item):
            # has_problem_2103006 saja tidak cukup: bisa berasal dari distribusi proporsional
            # cycle-level pada item yang belum ditagih vendor (GRNI state). Fallback ini hanya
            # berlaku jika item memang punya bill sendiri (has_exact_bill_for_item).
            bill_hit_role = "suspend"
        else:
            bill_hit_role = ""

        has_stj_clearing_line = any(normalize_text(line.get("akun_code")).upper() == "1108099" for line in raw_stj_lines)
        has_stj_suspend_line = any(normalize_text(line.get("akun_code")).upper() == "2103006" for line in raw_stj_lines)
        if getattr(item_row, "receipt_recovery_evidence", {}).get("missing_svl_ids"):
            # Product/vendor expansion STJs do not prove this receipt's SVL was journaled.
            # Any existing inventory balance blocks the recovery plan below for review.
            stj_state = "missing_actual_svl"
        elif has_item_stj:
            stj_state = "direct"
        elif has_correction_stj and not svl_zero_at_gr:
            # Correction JE (misal "Correction New JE + Relink SVL") pada SVL non-zero.
            # Diperlakukan setara STJ langsung karena JE sudah terbentuk dengan pola
            # DB Persediaan / CR 1108099-atau-2103006 yang valid untuk klasifikasi.
            stj_state = "direct"
        elif has_correction_stj and svl_zero_at_gr:
            stj_state = "correction_only"
        elif svl_zero_at_gr:
            stj_state = "missing_zero_svl"
        elif has_bill and (
            list(getattr(cycle, "stj_refs", None) or [])
            or list(getattr(cycle, "correction_stj_refs", None) or [])
            or list(getattr(item_row, "stock_move_ids", None) or [])
        ):
            stj_state = "corrupt"
        else:
            stj_state = "missing"

        stj_role = ""
        if has_stj_clearing_line or (stj_state == "direct" and has_problem_1108099):
            stj_role = "clearing"
        elif has_stj_suspend_line or stj_state == "direct":
            stj_role = "suspend"

        secondary_flags: list[str] = []
        if gr_quantity > 0.01 and bill_quantity > 0.01 and gr_quantity - bill_quantity > 0.01:
            secondary_flags.append("edge_partial_bill")
        if stj_state == "corrupt":
            secondary_flags.append("edge_stj_corrupt")

        case8_evidence = dict(getattr(item_row, "case8_evidence", None) or {})
        case9_evidence = dict(getattr(item_row, "case9_evidence", None) or {})
        has_return_value_gap = abs(_round2(case8_evidence.get("return_gap"))) >= 0.01
        has_uom_value_gap = (
            abs(_round2(case9_evidence.get("value_gap"))) >= 0.01
            and abs(_round2(case9_evidence.get("correction_amount") or case9_evidence.get("value_gap"))) >= 0.01
        )
        # GRNI balances alone are not repair evidence. Return/SVL and PO-UoM
        # evidence can independently prove Case 8/9 before a vendor bill exists.
        if not has_exact_bill_for_item and not (has_return_value_gap or has_uom_value_gap):
            return {
                "primary_case": "",
                "primary_group": "",
                "secondary_flags": secondary_flags,
                "case_reason": "",
                "auto_repairable": False,
                "stj_state": stj_state,
                "svl_zero_at_gr": svl_zero_at_gr,
                "bill_hit_role": "",
                "repair_basis_amount": 0.0,
                "repair_basis_source": "",
            }

        primary_case = ""
        case_reason = ""
        repair_basis_source = ""
        repair_basis_amount = 0.0
        if has_return_value_gap:
            primary_case = normalize_text(case8_evidence.get("case_key")).lower() or (
                "case8a" if bool(case8_evidence.get("full_return")) else "case8b"
            )
            if primary_case not in {"case8a", "case8b"}:
                primary_case = "case8b"
            repair_basis_source = "return_value_gap"
            repair_basis_amount = abs(_round2(case8_evidence.get("correction_amount") or case8_evidence.get("return_gap")))
            case_reason = (
                "Full return value tidak sebanding dengan receipt layer."
                if primary_case == "case8a"
                else "Partial return value tidak sebanding dengan porsi qty yang direturn."
            )
        elif has_uom_value_gap:
            primary_case = "case9"
            repair_basis_source = normalize_text(case9_evidence.get("target_basis")) or "uom_scale_expected_value"
            repair_basis_amount = abs(_round2(case9_evidence.get("correction_amount") or case9_evidence.get("value_gap")))
            source_uom = normalize_text(case9_evidence.get("source_uom_name")) or "source UoM"
            product_uom = normalize_text(case9_evidence.get("product_uom_name")) or "product UoM"
            scale_factor = _round2(case9_evidence.get("scale_factor"))
            case_reason = (
                f"UoM PO/Bill {source_uom} tidak inline dengan product/SVL {product_uom}; "
                f"scale factor {scale_factor:,.2f}x membuat value SVL tidak wajar."
            )
        elif (
            stj_state in {"missing_zero_svl", "missing_actual_svl", "correction_only"}
            and bill_hit_role == "suspend"
            and has_bill
            and has_problem_2103006
        ):
            primary_case = "case5"
            recovery = dict(getattr(item_row, "receipt_recovery_evidence", {}) or {})
            repair_basis_source = "actual_missing_receipt_svl"
            repair_basis_amount = _round2(recovery.get("missing_value"))
            case_reason = "JE penerimaan belum lengkap, Bill debit ke suspense; pulihkan nilai SVL aktual."
        elif (
            stj_state in {"missing_zero_svl", "missing_actual_svl", "correction_only"}
            and bill_hit_role == "expense"
            and has_bill
        ):
            primary_case = "case6"
            recovery = dict(getattr(item_row, "receipt_recovery_evidence", {}) or {})
            repair_basis_source = "actual_missing_receipt_svl"
            repair_basis_amount = _round2(recovery.get("missing_value"))
            case_reason = "JE penerimaan belum lengkap, Bill debit ke expense; counterpart memakai akun bill aktual."
        elif (
            stj_state == "direct"
            and stj_role == "clearing"
            and bill_hit_role == "suspend"
            and has_bill
            and has_problem_2103006
            and has_problem_1108099
        ):
            primary_case = "case1"
            repair_basis_source = "stj_bill_overlap"
            repair_basis_amount = _round2(min(abs(problem_2103006), abs(problem_1108099)))
            case_reason = "STJ ke clearing, Bill ke suspense."
        elif stj_state == "direct" and stj_role == "suspend" and bill_hit_role == "suspend" and has_problem_2103006 and has_exact_bill_for_item:
            # Guard: if the residual is fully explained by partial invoicing at a
            # consistent unit price, this is GRNI state (goods received, bill
            # pending) — NOT a price mismatch.  Case 2 only fires when the
            # Anglo-Saxon variance entry is genuinely missing or incorrect.
            #
            # Compute the STJ quantity from raw STJ lines that post a credit to
            # 2103006.  This is more accurate than gr_quantity because gr_quantity
            # is the aggregate across ALL stock moves in the merged cycle and can
            # include moves that have no 2103006 credit yet (pending STJ), which
            # would dilute the implied unit price and produce a false mismatch.
            stj_qty_2103006 = _round2(sum(
                float(line.get("qty_item") or 0.0)
                for line in raw_stj_lines
                if normalize_text(line.get("akun_code", "")).upper() == "2103006"
                and float(line.get("kredit") or 0.0) > 0
            ))
            if not cls._is_grni_residual(
                item_row=item_row,
                problem_2103006=problem_2103006,
                gr_quantity=gr_quantity,
                bill_quantity=bill_quantity,
                stj_quantity=stj_qty_2103006,
            ):
                primary_case = "case2"
                repair_basis_source = "suspend_residual"
                repair_basis_amount = _round2(abs(problem_2103006))
                case_reason = "STJ dan Bill sama-sama ke suspense, tetapi nominal berbeda."
        elif stj_state == "direct" and bill_hit_role == "expense" and has_problem_1108099:
            primary_case = "case3"
            repair_basis_source = "clearing_residual"
            repair_basis_amount = _round2(abs(problem_1108099))
            case_reason = "Bill ke expense dengan residual clearing pada item."
        elif stj_state == "direct" and bill_hit_role == "expense" and has_problem_2103006:
            primary_case = "case4"
            repair_basis_source = "suspend_residual"
            repair_basis_amount = _round2(abs(problem_2103006))
            case_reason = "Bill ke expense dengan residual suspense pada item."
        elif stj_state == "corrupt" and has_bill:
            primary_case = "edge_stj_corrupt"
            case_reason = "Ada indikasi STJ header/jejak GR tetapi line STJ item tidak valid."
        elif has_problem_2103006 or has_problem_1108099:
            primary_case = "case_lainnya"
            case_reason = "Saldo akun problem ada, tetapi pola item belum terdefinisi."

        auto_repairable = bool(primary_case in _PCB_REPAIRABLE_CASES and "edge_partial_bill" not in secondary_flags)
        if primary_case in {"case5", "case6"}:
            auto_repairable = False  # Recovery and cost allocation require finance review.
        if primary_case.startswith("edge_"):
            auto_repairable = False
        if primary_case == "case_lainnya":
            auto_repairable = False
        if not repair_basis_source and primary_case in _PCB_REPAIRABLE_CASES:
            repair_basis_source = "residual_balance"
        return {
            "primary_case": primary_case,
            "primary_group": cls._pcb_case_group_label(primary_case),
            "secondary_flags": secondary_flags,
            "case_reason": case_reason,
            "auto_repairable": auto_repairable,
            "stj_state": stj_state,
            "svl_zero_at_gr": svl_zero_at_gr,
            "bill_hit_role": bill_hit_role,
            "repair_basis_amount": _round2(repair_basis_amount),
            "repair_basis_source": repair_basis_source,
        }

    @classmethod
    def _apply_pcb_item_classifier(
        cls,
        *,
        cycle: SvlDashboardPurchaseCycle,
        product_info_map: dict[int, dict[str, Any]],
        lines_by_move: dict[int, list[dict[str, Any]]] | None = None,
        account_info_map: dict[int, dict[str, Any]] | None = None,
        cycle_bill_move_ids: list[int] | None = None,
    ) -> None:
        case_counts: dict[str, int] = defaultdict(int)
        cycle_edge_flags: set[str] = set()
        for item_row in list(getattr(cycle, "item_rows", None) or []):
            classifier = cls._classify_pcb_item_case(
                cycle=cycle,
                item_row=item_row,
                product_info_map=product_info_map,
                lines_by_move=lines_by_move,
                account_info_map=account_info_map,
                cycle_bill_move_ids=cycle_bill_move_ids,
            )
            item_row.primary_case = normalize_text(classifier.get("primary_case"))
            item_row.primary_group = normalize_text(classifier.get("primary_group"))
            item_row.secondary_flags = list(classifier.get("secondary_flags") or [])
            item_row.case_reason = normalize_text(classifier.get("case_reason"))
            item_row.auto_repairable = bool(classifier.get("auto_repairable"))
            item_row.stj_state = normalize_text(classifier.get("stj_state"))
            item_row.svl_zero_at_gr = bool(classifier.get("svl_zero_at_gr"))
            item_row.bill_hit_role = normalize_text(classifier.get("bill_hit_role"))
            item_row.repair_basis_amount = _round2(classifier.get("repair_basis_amount"))
            item_row.repair_basis_source = normalize_text(classifier.get("repair_basis_source"))
            if item_row.primary_case:
                case_counts[item_row.primary_case] += 1
            cycle_edge_flags.update(
                flag
                for flag in item_row.secondary_flags
                if normalize_text(flag).startswith("edge_")
            )
        if bool(getattr(cycle, "has_return_picking", False)) and not bool(getattr(cycle, "has_refund_bill", False)):
            cycle_edge_flags.add("edge_return_no_credit_memo")
        # Case 10: GR done, STJ ada, tapi bill vendor belum ada sama sekali, dan partner bukan intercompany.
        # Hanya berlaku untuk purchase-backed cycle (bukan mutation/internal transfer).
        _cycle_bill_move_ids = [int(v or 0) for v in (getattr(cycle, "bill_move_ids", None) or []) if int(v or 0) > 0]
        if (
            not _cycle_bill_move_ids
            and not bool(getattr(cycle, "partner_is_intercompany", False))
            and normalize_text(getattr(cycle, "document_classification", "")) == _PCB_DOCUMENT_CLASS_PURCHASE_BACKED
        ):
            cycle_edge_flags.add("case10")
        cycle.edge_flags = sorted(cycle_edge_flags)
        cycle.case_counts = dict(sorted(case_counts.items(), key=lambda item: (_PCB_CASE_PRIORITY.get(item[0], 99), item[0])))
        ranked_cases = sorted(
            (
                case_key
                for case_key, count in cycle.case_counts.items()
                if int(count or 0) > 0
            ),
            key=lambda case_key: (_PCB_CASE_PRIORITY.get(case_key, 99), case_key),
        )
        if ranked_cases:
            cycle.primary_case = ranked_cases[0]
        elif cycle.edge_flags:
            cycle.primary_case = sorted(cycle.edge_flags, key=lambda case_key: (_PCB_CASE_PRIORITY.get(case_key, 99), case_key))[0]
        elif normalize_text(getattr(cycle, "cycle_status", "")).lower() == "problem":
            cycle.primary_case = "case_lainnya"
        else:
            cycle.primary_case = ""
        # Case 9 yang nilai ekonomisnya benar (UoM mismatch cosmetic saja) tidak perlu
        # di-force ke "problem" — tidak ada repair JE yang diperlukan.
        # Deteksi via uom_value_economically_correct di case9_evidence setiap item_row.
        all_case9_economically_correct = (
            int(cycle.case_counts.get("case9", 0) or 0) > 0
            and all(
                bool(dict(getattr(item_row, "case9_evidence", None) or {}).get("uom_value_economically_correct"))
                for item_row in list(getattr(cycle, "item_rows", None) or [])
                if normalize_text(getattr(item_row, "primary_case", "")).lower() == "case9"
            )
        )
        if all_case9_economically_correct:
            # Downgrade: hapus case9 dari case_counts, biarkan cycle_status tetap partial/healthy
            cycle.case_counts.pop("case9", None)
            for item_row in list(getattr(cycle, "item_rows", None) or []):
                if normalize_text(getattr(item_row, "primary_case", "")).lower() == "case9":
                    item_row.primary_case = ""
                    item_row.primary_group = "Group 9: UoM Scale Mismatch (Nilai Ekonomis Benar)"
                    item_row.auto_repairable = False
        has_review_required_runtime_case = any(
            int(cycle.case_counts.get(case_key, 0) or 0) > 0
            for case_key in ("case5", "case6", "case8a", "case8b", "case9")
        )
        if has_review_required_runtime_case:
            cycle.cycle_status = "problem"
            cycle.partial_group_key = ""
            cycle.partial_group_label = ""
        cycle.mixed_case_summary = ", ".join(f"{case_key}:{count}" for case_key, count in cycle.case_counts.items() if int(count or 0) > 0)

        # UoM flag: mismatch jika ada item dengan case9_evidence yang terdeteksi (scale_factor > 0)
        # berlaku bahkan jika case9 sudah di-downgrade karena economically correct.
        cycle.uom_flag = "mismatch" if any(
            abs(float(dict(getattr(item_row, "case9_evidence", None) or {}).get("scale_factor") or 0.0)) > 0.01
            for item_row in (getattr(cycle, "item_rows", None) or [])
        ) else "inline"

        # Case 10 upgrade: cycle yang akun-nya seimbang (healthy/partial) tapi belum ada bill
        # vendor sama sekali harus di-upgrade ke "problem" agar muncul di Cycle Bermasalah.
        if "case10" in (cycle.edge_flags or []) and normalize_text(getattr(cycle, "cycle_status", "")).lower() != "problem":
            cycle.cycle_status = "problem"
            cycle.primary_case = "case10"

        # GRNI downgrade: if cycle is "problem" only because of open 2103006
        # balances, and every such balance is explained by partial invoicing at a
        # consistent unit price, the cycle is legitimately in-progress (vendor has
        # not yet billed the full received quantity).  Downgrade to "partial" so it
        # does not appear under Cycle Bermasalah.
        #
        # edge_partial_bill ("ada bill tapi belum cover semua qty") is itself a
        # GRNI indicator — it confirms that the cycle has partial invoicing and the
        # open 2103006 balance is expected.  We allow the downgrade even when that
        # flag is set, but block it for any other edge flag (e.g. a return without a
        # credit memo, which is a genuine problem).
        _grni_compatible_edge_flags = frozenset({"edge_partial_bill"})
        _cycle_edge_flags = frozenset(cycle.edge_flags or [])
        if (
            normalize_text(getattr(cycle, "cycle_status", "")).lower() == "problem"
            and not cycle.case_counts
            and _cycle_edge_flags <= _grni_compatible_edge_flags
            and cls._is_pcb_grni_suspense_downgrade(cycle)
        ):
            cycle.cycle_status = "partial"
            cycle.primary_case = ""
            cycle.edge_flags = []
            for acct_row in list(getattr(cycle, "account_rows", None) or []):
                if normalize_text(getattr(acct_row, "code", "")).upper() == "2103006":
                    if abs(_round2(getattr(acct_row, "net_balance", 0.0))) >= 0.01:
                        acct_row.status = "acceptable"

    @staticmethod
    def _build_pcb_adjustment_warning_text(
        adjustment_rows: list[SvlDashboardPcbAdjustmentAuditRow],
    ) -> str:
        if not adjustment_rows:
            return ""
        labels: list[str] = []
        seen: set[str] = set()
        ambiguous_count = 0
        for row in adjustment_rows:
            label = normalize_text(row.move_name) or normalize_text(row.move_ref) or f"Move #{int(row.move_id or 0)}"
            if label and label not in seen:
                seen.add(label)
                labels.append(label)
            if bool(row.ambiguous):
                ambiguous_count += 1
        if not labels:
            return ""
        text = f"Warning: Clearing adjustment -> {labels[0]}"
        if len(labels) > 1:
            text += f" (+{len(labels) - 1})"
        if ambiguous_count > 0:
            text += " | match ambiguous"
        return text

    @staticmethod
    def _score_pcb_adjustment_cycle_match(
        *,
        cycle_context: dict[str, Any],
        relation: dict[str, int],
        token_text: str,
        rac_hint: int,
    ) -> tuple[int, str]:
        product_id = int(relation.get("product_id") or 0)
        purchase_line_id = int(relation.get("purchase_line_id") or 0)
        stock_move_id = int(relation.get("stock_move_id") or 0)
        bill_move_id = int(relation.get("bill_move_id") or 0)
        picking_id = int(relation.get("picking_id") or 0)
        cycle_product_ids = set(cycle_context.get("product_ids") or set())
        cycle_purchase_line_ids = set(cycle_context.get("purchase_line_ids") or set())
        cycle_stock_move_ids = set(cycle_context.get("stock_move_ids") or set())
        cycle_bill_move_ids = set(cycle_context.get("bill_move_ids") or set())
        cycle_picking_ids = set(cycle_context.get("picking_ids") or set())

        scores: list[tuple[int, str]] = []
        if bill_move_id > 0 and bill_move_id in cycle_bill_move_ids:
            scores.append((500 + rac_hint, "exact.bill_move_id"))
        if stock_move_id > 0 and stock_move_id in cycle_stock_move_ids:
            scores.append((450 + rac_hint, "exact.stock_move_id"))
        if purchase_line_id > 0 and purchase_line_id in cycle_purchase_line_ids:
            scores.append((400 + rac_hint, "exact.purchase_line_id"))
        if product_id > 0 and product_id in cycle_product_ids:
            scores.append((300 + rac_hint, "exact.product_id"))
        if picking_id > 0 and picking_id in cycle_picking_ids:
            scores.append((200 + rac_hint, "exact.picking_id"))

        has_bill_token = any(
            token and token in token_text
            for token in set(cycle_context.get("bill_tokens") or set())
        )
        has_picking_token = any(
            token and token in token_text
            for token in set(cycle_context.get("picking_tokens") or set())
        )
        has_structured_relation = any(
            int(relation.get(field_name) or 0) > 0
            for field_name in ("bill_move_id", "stock_move_id", "purchase_line_id", "picking_id")
        )
        if product_id > 0 and product_id not in cycle_product_ids:
            return (0, "")
        if has_bill_token and has_picking_token:
            if has_structured_relation:
                if product_id > 0 and product_id in cycle_product_ids:
                    scores.append((340 + rac_hint, "exact.product_id+fallback.bill_picking_token"))
            else:
                scores.append((180 + rac_hint, "fallback.bill_picking_token"))
        elif has_bill_token and not has_structured_relation:
            scores.append((140 + rac_hint, "fallback.bill_token"))
        elif has_picking_token and not has_structured_relation:
            scores.append((120 + rac_hint, "fallback.picking_token"))
        if not scores:
            return (0, "")
        return max(scores, key=lambda item: item[0])

    async def _fetch_pcb_adjustment_audit_moves(
        self,
        *,
        request: SvlDashboardRequest,
        date_from: str,
        date_to: str,
        context: dict[str, Any],
        capabilities: dict[str, Any],
        excluded_move_ids: list[int],
        account_info_map: dict[int, dict[str, Any]],
        product_info_map: dict[int, dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], dict[int, dict[str, Any]], dict[int, dict[str, Any]]]:
        _adj_t0 = perf_counter()
        excluded_move_id_set = {
            int(move_id or 0)
            for move_id in list(excluded_move_ids or [])
            if int(move_id or 0) > 0
        }
        # ── Step 1: resolve clearing AND source account.account IDs in parallel ──
        account_company_field = normalize_text(capabilities.get("account_company_field"))
        clearing_account_domain: list[Any] = [("code", "=", _PCB_ADJUSTMENT_CLEARING_CODE)]
        source_account_domain: list[Any] = [
            "|",
            ("code", "in", list(_PCB_COGS_VARIANCE_CODES)),
            ("account_type", "=", "expense_direct_cost"),
        ]
        if account_company_field == "company_id":
            clearing_account_domain.append(("company_id", "=", request.company_id))
            source_account_domain.append(("company_id", "=", request.company_id))
        elif account_company_field == "company_ids":
            clearing_account_domain.append(("company_ids", "in", [request.company_id]))
            source_account_domain.append(("company_ids", "in", [request.company_id]))
        account_fields = ["code", "name", "account_type"]
        clearing_accounts, source_accounts = await asyncio.gather(
            self.rpc.search_read(
                "account.account",
                clearing_account_domain,
                fields=account_fields,
                context=context,
                stage="SVL_DASH_PCB_ADJ_CLEARING_ACCOUNT",
            ),
            self.rpc.search_read(
                "account.account",
                source_account_domain,
                fields=account_fields,
                context=context,
                stage="SVL_DASH_PCB_ADJ_SOURCE_ACCOUNT",
            ),
        )
        clearing_account_ids = [
            int(row.get("id") or 0)
            for row in clearing_accounts
            if int(row.get("id") or 0) > 0
        ]
        source_account_ids = [
            int(row.get("id") or 0)
            for row in source_accounts
            if int(row.get("id") or 0) > 0
        ]
        if not clearing_account_ids or not source_account_ids:
            return [], dict(account_info_map), dict(product_info_map)

        # Pre-populate account info from the accounts we just resolved
        resolved_account_info_map = dict(account_info_map)
        for row in [*clearing_accounts, *source_accounts]:
            aid = int(row.get("id") or 0)
            if aid > 0 and aid not in resolved_account_info_map:
                resolved_account_info_map[aid] = row
        self._log(
            f"[BENCH]   adj/accounts: {(perf_counter() - _adj_t0) * 1000.0:.0f}ms | "
            f"{len(clearing_account_ids)} clearing | {len(source_account_ids)} source"
        )

        # ── Step 2: dual read_group — intersect clearing × source move_ids ──
        _adj_t1 = perf_counter()
        aml_fields = await self._fields_get_cached("account.move.line")
        _shared_aml_domain: list[Any] = [("company_id", "=", request.company_id)]
        _shared_aml_domain.extend(self._build_posted_domain(aml_fields))
        if "display_type" in aml_fields:
            _shared_aml_domain.append(("display_type", "not in", ["line_section", "line_note"]))
        if date_from:
            _shared_aml_domain.append(("date", ">=", date_from))
        if date_to:
            _shared_aml_domain.append(("date", "<=", date_to))

        clearing_aml_domain = [*_shared_aml_domain, ("account_id", "in", clearing_account_ids)]
        source_aml_domain = [*_shared_aml_domain, ("account_id", "in", source_account_ids)]

        _use_intersect = True
        try:
            clearing_group_rows, source_group_rows = await asyncio.gather(
                self.rpc.read_group(
                    "account.move.line",
                    clearing_aml_domain,
                    fields=["move_id"],
                    groupby=["move_id"],
                    lazy=False,
                    context=context,
                    stage="SVL_DASH_PCB_ADJ_CLEARING_AML_GROUPED",
                ),
                self.rpc.read_group(
                    "account.move.line",
                    source_aml_domain,
                    fields=["move_id"],
                    groupby=["move_id"],
                    lazy=False,
                    context=context,
                    stage="SVL_DASH_PCB_ADJ_SOURCE_AML_GROUPED",
                ),
            )
            clearing_move_id_set = {
                _many2one_id(row.get("move_id"))
                for row in clearing_group_rows
                if _many2one_id(row.get("move_id")) > 0
            }
            source_move_id_set = {
                _many2one_id(row.get("move_id"))
                for row in source_group_rows
                if _many2one_id(row.get("move_id")) > 0
            }
            candidate_move_ids = sorted(
                (clearing_move_id_set & source_move_id_set) - excluded_move_id_set
            )
        except Exception:  # noqa: BLE001
            # Fallback: single clearing read_group (original approach)
            _use_intersect = False
            clearing_move_id_set = set()
            source_move_id_set = set()
            try:
                clearing_group_rows = await self.rpc.read_group(
                    "account.move.line",
                    clearing_aml_domain,
                    fields=["move_id"],
                    groupby=["move_id"],
                    lazy=False,
                    context=context,
                    stage="SVL_DASH_PCB_ADJ_CLEARING_AML_GROUPED_FB",
                )
                candidate_move_ids = sorted(
                    {
                        _many2one_id(row.get("move_id"))
                        for row in clearing_group_rows
                        if _many2one_id(row.get("move_id")) > 0
                        and _many2one_id(row.get("move_id")) not in excluded_move_id_set
                    }
                )
            except Exception:  # noqa: BLE001
                base_line_fields = list(
                    dict.fromkeys(
                        [
                            field_name
                            for field_name in ["id", "move_id", "date", "account_id", "name", "product_id",
                                                "purchase_line_id", "stock_move_id", "picking_id", "bill_move_id",
                                                "display_type"]
                            if field_name in aml_fields or field_name in {"id", "move_id", "date", "account_id"}
                        ]
                    )
                )
                clearing_rows = await self.rpc.search_read(
                    "account.move.line",
                    clearing_aml_domain,
                    fields=base_line_fields,
                    order="date,move_id,id",
                    context=context,
                    stage="SVL_DASH_PCB_ADJ_CLEARING_AML",
                )
                candidate_move_ids = sorted(
                    {
                        _many2one_id(row.get("move_id"))
                        for row in clearing_rows
                        if _many2one_id(row.get("move_id")) > 0
                        and _many2one_id(row.get("move_id")) not in excluded_move_id_set
                    }
                )
        if _use_intersect:
            self._log(
                f"[BENCH]   adj/dual_read_group: {(perf_counter() - _adj_t1) * 1000.0:.0f}ms | "
                f"{len(clearing_move_id_set)} clearing | {len(source_move_id_set)} source | "
                f"{len(candidate_move_ids)} intersection (excl {len(excluded_move_id_set)})"
            )
        else:
            self._log(
                f"[BENCH]   adj/read_group_fallback: {(perf_counter() - _adj_t1) * 1000.0:.0f}ms | "
                f"{len(candidate_move_ids)} candidates (excl {len(excluded_move_id_set)})"
            )
        if not candidate_move_ids:
            return [], resolved_account_info_map, dict(product_info_map)

        # ── Step 3: account.move header — only for intersection candidates ──
        _adj_t2 = perf_counter()
        move_rows = await self.rpc.read(
            "account.move",
            candidate_move_ids,
            fields=["name", "date", "state", "journal_id", "partner_id", "partner_bank_id", "ref", "move_type"],
            context=context,
            stage="SVL_DASH_PCB_ADJ_MOVE_INFO",
        )
        move_info_by_id = {
            int(row.get("id") or 0): row
            for row in move_rows
            if int(row.get("id") or 0) > 0
            and normalize_text(row.get("state")).lower() == "posted"
            and normalize_text(row.get("move_type") or "entry").lower() == "entry"
        }
        self._log(
            f"[BENCH]   adj/move_info: {(perf_counter() - _adj_t2) * 1000.0:.0f}ms | "
            f"{len(candidate_move_ids)} requested | {len(move_info_by_id)} posted entry"
        )
        if not move_info_by_id:
            return [], resolved_account_info_map, dict(product_info_map)

        # ── Step 4: shortlist — skip _group_aml if intersection already applied ──
        _adj_t3 = perf_counter()
        if _use_intersect:
            # Intersection guarantees both clearing and source accounts exist on each move.
            # No need for _group_aml_by_move_and_account — go straight to shortlist.
            shortlisted_move_ids = sorted(move_info_by_id)
        else:
            # Fallback: original approach — group + Python-side has_clearing/has_source check
            grouped_account_rows = await self._group_aml_by_move_and_account(
                move_ids=sorted(move_info_by_id),
                company_id=request.company_id,
                context=context,
                aml_fields=aml_fields,
                stage="SVL_DASH_PCB_ADJ_MOVE_AML_GROUPED",
            )
            if not grouped_account_rows:
                return [], resolved_account_info_map, dict(product_info_map)
            grouped_account_ids = sorted(
                {
                    _many2one_id(row.get("account_id"))
                    for row in grouped_account_rows
                    if _many2one_id(row.get("account_id")) > 0
                }
            )
            missing_grouped_account_ids = [
                account_id
                for account_id in grouped_account_ids
                if account_id not in resolved_account_info_map
            ]
            if missing_grouped_account_ids:
                resolved_account_info_map.update(
                    await self._fetch_account_info_map(account_ids=missing_grouped_account_ids, context=context)
                )
            grouped_accounts_by_move_id: dict[int, set[int]] = defaultdict(set)
            for row in grouped_account_rows:
                move_id = _many2one_id(row.get("move_id"))
                account_id = _many2one_id(row.get("account_id"))
                if move_id > 0 and account_id > 0:
                    grouped_accounts_by_move_id[move_id].add(account_id)
            shortlisted_move_ids = []
            for move_id in sorted(move_info_by_id):
                account_ids_for_move = grouped_accounts_by_move_id.get(move_id, set())
                if not account_ids_for_move:
                    continue
                has_clearing = False
                has_source = False
                for account_id in account_ids_for_move:
                    account_info = resolved_account_info_map.get(account_id) or {}
                    account_code = normalize_text(account_info.get("code")).upper()
                    account_type = normalize_text(account_info.get("account_type")).lower()
                    if account_code == _PCB_ADJUSTMENT_CLEARING_CODE:
                        has_clearing = True
                    if self._is_pcb_adjustment_source_account(account_code=account_code, account_type=account_type):
                        has_source = True
                    if has_clearing and has_source:
                        shortlisted_move_ids.append(move_id)
                        break
        self._log(
            f"[BENCH]   adj/shortlist: {(perf_counter() - _adj_t3) * 1000.0:.0f}ms | "
            f"{len(shortlisted_move_ids)} shortlisted"
        )
        self._log(
            "PCB adjustment shortlist: "
            f"{len(candidate_move_ids)} candidate(s) -> "
            f"{len(move_info_by_id)} posted entry move(s) -> "
            f"{len(shortlisted_move_ids)} source-linked move(s)."
        )
        if not shortlisted_move_ids:
            return [], resolved_account_info_map, dict(product_info_map)
        move_info_by_id = {
            move_id: move_info_by_id[move_id]
            for move_id in shortlisted_move_ids
            if move_id in move_info_by_id
        }

        _adj_t4 = perf_counter()
        candidate_line_fields = [
            "id",
            "move_id",
            "date",
            "account_id",
            "name",
            "product_id",
            "purchase_line_id",
            "stock_move_id",
            "picking_id",
            "bill_move_id",
            "debit",
            "credit",
            "balance",
            "display_type",
            "company_id",
        ]
        candidate_line_fields = list(
            dict.fromkeys(
                [
                    field_name
                    for field_name in candidate_line_fields
                    if field_name in aml_fields or field_name in {"id", "move_id", "date", "account_id"}
                ]
            )
        )
        lines_by_move_id: dict[int, list[dict[str, Any]]] = defaultdict(list)
        candidate_line_rows = await self._search_read_in_chunks(
            "account.move.line",
            ids_field="move_id",
            ids=sorted(move_info_by_id),
            fields=candidate_line_fields,
            context=context,
            stage="SVL_DASH_PCB_ADJ_MOVE_AML",
            base_domain=[
                ("company_id", "=", request.company_id),
                *self._build_posted_domain(aml_fields),
                *([("display_type", "not in", ["line_section", "line_note"])] if "display_type" in aml_fields else []),
            ],
            order="date,move_id,id",
        )
        for row in candidate_line_rows:
            move_id = _many2one_id(row.get("move_id"))
            if move_id <= 0 or move_id not in move_info_by_id:
                continue
            if normalize_text(row.get("display_type")) in {"line_section", "line_note"}:
                continue
            lines_by_move_id[move_id].append(row)

        candidate_account_ids = sorted(
            {
                _many2one_id(row.get("account_id"))
                for rows in lines_by_move_id.values()
                for row in rows
                if _many2one_id(row.get("account_id")) > 0
            }
        )
        missing_account_ids = [
            account_id
            for account_id in candidate_account_ids
            if account_id not in resolved_account_info_map
        ]
        if missing_account_ids:
            resolved_account_info_map.update(
                await self._fetch_account_info_map(account_ids=missing_account_ids, context=context)
            )

        resolved_product_info_map = dict(product_info_map)
        candidate_moves: list[dict[str, Any]] = []
        candidate_product_ids: set[int] = set()
        explicit_origin_names: set[str] = set()
        for move_id, rows in lines_by_move_id.items():
            has_clearing = False
            has_source = False
            for row in rows:
                account_info = resolved_account_info_map.get(_many2one_id(row.get("account_id"))) or {}
                account_code = normalize_text(account_info.get("code")).upper()
                account_type = normalize_text(account_info.get("account_type")).lower()
                if account_code == _PCB_ADJUSTMENT_CLEARING_CODE:
                    has_clearing = True
                if self._is_pcb_adjustment_source_account(account_code=account_code, account_type=account_type):
                    has_source = True
                product_id = _many2one_id(row.get("product_id"))
                if product_id > 0:
                    candidate_product_ids.add(product_id)
            if not (has_clearing and has_source):
                continue
            move_row = move_info_by_id.get(move_id) or {}
            origin_names = [
                name
                for name in _extract_move_name_tokens(
                    move_row.get("name"),
                    move_row.get("ref"),
                    *[row.get("name") for row in rows],
                )
                if normalize_text(name).upper() != normalize_text(move_row.get("name")).upper()
            ]
            explicit_origin_names.update(origin_names)
            candidate_moves.append(
                {
                    "move_id": move_id,
                    "move_name": normalize_text(move_row.get("name")),
                    "move_ref": normalize_text(move_row.get("ref")),
                    "move_date": normalize_text(move_row.get("date"))[:10],
                    "lines": list(rows),
                    "origin_names": list(origin_names),
                }
            )
        self._log(
            f"[BENCH]   adj/detail_aml: {(perf_counter() - _adj_t4) * 1000.0:.0f}ms | "
            f"{len(candidate_moves)} candidate_moves | {len(explicit_origin_names)} origin_names"
        )
        _adj_t5 = perf_counter()
        origin_moves_by_name: dict[str, dict[str, Any]] = {}
        origin_line_rows_by_move_id: dict[int, list[dict[str, Any]]] = defaultdict(list)
        if explicit_origin_names:
            origin_move_rows = await self.rpc.search_read(
                "account.move",
                [
                    ("company_id", "=", request.company_id),
                    ("name", "in", sorted(explicit_origin_names)),
                ],
                fields=["name", "date", "state", "ref", "move_type"],
                context=context,
                stage="SVL_DASH_PCB_ADJ_ORIGIN_MOVE_INFO",
            )
            origin_move_info_by_id = {
                int(row.get("id") or 0): row
                for row in origin_move_rows
                if int(row.get("id") or 0) > 0 and normalize_text(row.get("state")).lower() == "posted"
            }
            if origin_move_info_by_id:
                origin_line_domain: list[Any] = [("company_id", "=", request.company_id)]
                origin_line_domain.extend(self._build_posted_domain(aml_fields))
                origin_line_rows = await self._search_read_in_chunks(
                    "account.move.line",
                    ids_field="move_id",
                    ids=sorted(origin_move_info_by_id),
                    fields=candidate_line_fields,
                    context=context,
                    stage="SVL_DASH_PCB_ADJ_ORIGIN_MOVE_AML",
                    base_domain=origin_line_domain,
                    order="date,move_id,id",
                )
                for row in origin_line_rows:
                    move_id = _many2one_id(row.get("move_id"))
                    if move_id <= 0 or move_id not in origin_move_info_by_id:
                        continue
                    if normalize_text(row.get("display_type")) in {"line_section", "line_note"}:
                        continue
                    origin_line_rows_by_move_id[move_id].append(row)
                    product_id = _many2one_id(row.get("product_id"))
                    if product_id > 0:
                        candidate_product_ids.add(product_id)
                for move_id, move_row in origin_move_info_by_id.items():
                    move_name = normalize_text(move_row.get("name")).upper()
                    if not move_name:
                        continue
                    origin_moves_by_name[move_name] = {
                        "move_id": move_id,
                        "move_name": normalize_text(move_row.get("name")),
                        "move_ref": normalize_text(move_row.get("ref")),
                        "move_date": normalize_text(move_row.get("date"))[:10],
                        "move_type": normalize_text(move_row.get("move_type")),
                        "lines": list(origin_line_rows_by_move_id.get(move_id, [])),
                    }
        if not candidate_moves:
            return [], resolved_account_info_map, resolved_product_info_map

        if origin_moves_by_name:
            for candidate_move in candidate_moves:
                candidate_move["origin_moves_by_name"] = {
                    name: origin_moves_by_name[name]
                    for name in list(candidate_move.get("origin_names") or [])
                    if name in origin_moves_by_name
                }

        missing_product_ids = sorted(
            product_id
            for product_id in candidate_product_ids
            if product_id > 0 and product_id not in resolved_product_info_map
        )
        if missing_product_ids:
            resolved_product_info_map.update(
                await self._fetch_pcb_product_info_map(
                    product_ids=missing_product_ids,
                    context=context,
                    account_info_map=resolved_account_info_map,
                )
            )
        self._log(
            f"[BENCH]   adj/origin_product: {(perf_counter() - _adj_t5) * 1000.0:.0f}ms | "
            f"{len(origin_moves_by_name)} origins | {len(resolved_product_info_map)} products"
        )
        return candidate_moves, resolved_account_info_map, resolved_product_info_map

    def _attach_pcb_adjustment_audit_rows(
        self,
        *,
        cycles: list[SvlDashboardPurchaseCycle],
        adjustment_moves: list[dict[str, Any]],
        trace: dict[str, Any],
        move_info_map: dict[int, dict[str, Any]],
        product_info_map: dict[int, dict[str, Any]],
        account_info_map: dict[int, dict[str, Any]],
    ) -> None:
        if not cycles:
            return
        for cycle in cycles:
            cycle.adjustment_audit_rows = []
            cycle.adjustment_warning_text = ""
            for item_row in list(getattr(cycle, "item_rows", None) or []):
                item_row.adjustment_audit_rows = []
        if not adjustment_moves:
            return

        stock_move_rows_by_id = dict(trace.get("stock_move_rows_by_id") or {})
        stock_move_indexes = self._get_pcb_stock_move_indexes(trace)
        stock_move_ids_by_picking_id: dict[int, set[int]] = {
            int(key): {int(value or 0) for value in values if int(value or 0) > 0}
            for key, values in dict(stock_move_indexes.get("stock_move_ids_by_picking_id") or {}).items()
            if int(key or 0) > 0
        }
        purchase_line_ids_by_picking_id: dict[int, set[int]] = {
            int(key): {int(value or 0) for value in values if int(value or 0) > 0}
            for key, values in dict(stock_move_indexes.get("purchase_line_ids_by_picking_id") or {}).items()
            if int(key or 0) > 0
        }
        bill_rows_by_id = dict(trace.get("bill_rows_by_id") or {})
        purchase_line_product_map: dict[int, int] = {
            int(key): int(value)
            for key, value in dict(trace.get("purchase_line_product_map") or {}).items()
            if int(key or 0) > 0 and int(value or 0) > 0
        }
        move_ids_by_name: dict[str, set[int]] = defaultdict(set)
        for move_id, move_row in dict(move_info_map or {}).items():
            clean_name = normalize_text(move_row.get("name"))
            if clean_name:
                move_ids_by_name[clean_name].add(int(move_id or 0))
        for bill_id, bill_row in bill_rows_by_id.items():
            clean_bill_id = int(bill_id or 0)
            if clean_bill_id <= 0:
                continue
            clean_name = normalize_text((move_info_map.get(clean_bill_id) or {}).get("name")) or normalize_text(bill_row.get("name"))
            if clean_name:
                move_ids_by_name[clean_name].add(clean_bill_id)

        cycle_contexts: list[dict[str, Any]] = []
        cycle_context_keys_by_product: dict[int, set[int]] = defaultdict(set)
        cycle_context_keys_by_picking: dict[int, set[int]] = defaultdict(set)
        cycle_context_keys_by_bill: dict[int, set[int]] = defaultdict(set)
        for cycle in cycles:
            cycle_picking_ids = {
                int(value or 0)
                for value in list(getattr(cycle, "picking_ids", None) or [getattr(cycle, "picking_id", 0)])
                if int(value or 0) > 0
            }
            cycle_stock_move_ids: set[int] = set()
            cycle_purchase_line_ids: set[int] = set()
            for picking_id in cycle_picking_ids:
                cycle_stock_move_ids.update(stock_move_ids_by_picking_id.get(picking_id, set()))
                cycle_purchase_line_ids.update(purchase_line_ids_by_picking_id.get(picking_id, set()))
            item_by_product_id = {
                int(getattr(item_row, "product_id", 0) or 0): item_row
                for item_row in list(getattr(cycle, "item_rows", None) or [])
                if int(getattr(item_row, "product_id", 0) or 0) > 0
            }
            cycle_product_ids = {
                int(value or 0)
                for value in list(getattr(cycle, "product_ids", None) or [])
                if int(value or 0) > 0
            } | set(item_by_product_id)
            cycle_bill_move_ids: set[int] = {
                int(value or 0)
                for value in list(getattr(cycle, "bill_move_ids", None) or [])
                if int(value or 0) > 0
            }
            for bill_ref in list(getattr(cycle, "bill_refs", None) or []):
                cycle_bill_move_ids.update(
                    int(move_id)
                    for move_id in move_ids_by_name.get(normalize_text(bill_ref), set())
                    if int(move_id or 0) > 0
                )
            context_key = int(getattr(cycle, "picking_id", 0) or 0)
            cycle_context = {
                "context_key": context_key,
                "cycle": cycle,
                "picking_ids": cycle_picking_ids,
                "stock_move_ids": cycle_stock_move_ids,
                "purchase_line_ids": cycle_purchase_line_ids,
                "product_ids": cycle_product_ids,
                "bill_move_ids": cycle_bill_move_ids,
                "item_by_product_id": item_by_product_id,
                "picking_tokens": {
                    _normalized_match_text(name)
                    for name in list(getattr(cycle, "picking_names", None) or [getattr(cycle, "picking_name", "")])
                    if _normalized_match_text(name)
                },
                "bill_tokens": {
                    _normalized_match_text(name)
                    for name in list(getattr(cycle, "bill_refs", None) or [])
                    if _normalized_match_text(name)
                },
            }
            cycle_contexts.append(cycle_context)
            for product_id in cycle_product_ids:
                cycle_context_keys_by_product[product_id].add(context_key)
            for picking_id in cycle_picking_ids:
                cycle_context_keys_by_picking[picking_id].add(context_key)
            for bill_move_id in cycle_bill_move_ids:
                cycle_context_keys_by_bill[bill_move_id].add(context_key)

        seen_cycle_keys: dict[int, set[tuple[Any, ...]]] = defaultdict(set)
        seen_item_keys: dict[tuple[int, int], set[tuple[Any, ...]]] = defaultdict(set)
        for move in adjustment_moves:
            move_id = int(move.get("move_id") or 0)
            if move_id <= 0:
                continue
            move_name = normalize_text(move.get("move_name"))
            move_ref = normalize_text(move.get("move_ref"))
            move_date = normalize_text(move.get("move_date"))[:10]
            token_text = f"{_normalized_match_text(move_name)} {_normalized_match_text(move_ref)}".strip()
            move_lines = list(move.get("lines") or [])
            clearing_rows = []
            source_rows = []
            for row in move_lines:
                account_info = account_info_map.get(_many2one_id(row.get("account_id"))) or {}
                account_code = normalize_text(account_info.get("code")).upper()
                account_type = normalize_text(account_info.get("account_type")).lower()
                if account_code == _PCB_ADJUSTMENT_CLEARING_CODE:
                    clearing_rows.append(row)
                if self._is_pcb_adjustment_source_account(account_code=account_code, account_type=account_type):
                    source_rows.append(row)
            if not clearing_rows or not source_rows:
                continue
            rac_hint = self._pcb_adjustment_rac_hint(move_name, move_ref)
            for source_row in source_rows:
                relation = self._resolve_pcb_adjustment_relation_values(
                    source_row=source_row,
                    clearing_rows=clearing_rows,
                    stock_move_rows_by_id=stock_move_rows_by_id,
                    purchase_line_product_map=purchase_line_product_map,
                )
                origin_info = self._resolve_pcb_adjustment_origin_info(
                    move=move,
                    source_row=source_row,
                    stock_move_rows_by_id=stock_move_rows_by_id,
                    purchase_line_product_map=purchase_line_product_map,
                )
                effective_relation = dict(relation)
                origin_relation = dict(origin_info.get("relation") or {})
                for field_name in ("product_id", "purchase_line_id", "stock_move_id", "bill_move_id", "picking_id"):
                    if int(effective_relation.get(field_name) or 0) > 0:
                        continue
                    if int(origin_relation.get(field_name) or 0) > 0:
                        effective_relation[field_name] = int(origin_relation.get(field_name) or 0)
                scored_matches: list[tuple[int, str, dict[str, Any]]] = []
                candidate_context_keys: set[int] = set()
                product_id = int(effective_relation.get("product_id") or 0)
                if product_id > 0:
                    candidate_context_keys.update(cycle_context_keys_by_product.get(product_id, set()))
                picking_id = int(effective_relation.get("picking_id") or 0)
                if picking_id > 0:
                    candidate_context_keys.update(cycle_context_keys_by_picking.get(picking_id, set()))
                bill_move_id = int(effective_relation.get("bill_move_id") or 0)
                if bill_move_id > 0:
                    candidate_context_keys.update(cycle_context_keys_by_bill.get(bill_move_id, set()))
                contexts_to_score = [
                    cycle_context
                    for cycle_context in cycle_contexts
                    if int(cycle_context.get("context_key") or 0) in candidate_context_keys
                ] if candidate_context_keys else cycle_contexts
                for cycle_context in contexts_to_score:
                    score, matched_basis = self._score_pcb_adjustment_cycle_match(
                        cycle_context=cycle_context,
                        relation=effective_relation,
                        token_text=token_text,
                        rac_hint=rac_hint,
                    )
                    if score <= 0:
                        continue
                    scored_matches.append((score, matched_basis, cycle_context))
                if not scored_matches:
                    continue
                best_score = max(score for score, _basis, _context in scored_matches)
                best_matches = [
                    (matched_basis, cycle_context)
                    for score, matched_basis, cycle_context in scored_matches
                    if score == best_score
                ]
                account_info = account_info_map.get(_many2one_id(source_row.get("account_id"))) or {}
                source_account_code = normalize_text(account_info.get("code")).upper()
                source_account_name = normalize_text(account_info.get("name"))
                product_id = int(effective_relation.get("product_id") or 0)
                product_info = product_info_map.get(product_id) or {}
                item_code = normalize_text(product_info.get("default_code"))
                item_name = normalize_text(product_info.get("name"))
                repair_clearing_amount = self._pcb_adjustment_line_amount(source_row)
                is_ambiguous = len(best_matches) > 1
                for matched_basis, cycle_context in best_matches:
                    cycle = cycle_context["cycle"]
                    cycle_key = int(getattr(cycle, "picking_id", 0) or 0)
                    audit_key = (
                        move_id,
                        int(source_row.get("id") or 0),
                        product_id,
                        source_account_code,
                        normalize_text(matched_basis),
                        bool(is_ambiguous),
                    )
                    if audit_key in seen_cycle_keys[cycle_key]:
                        continue
                    seen_cycle_keys[cycle_key].add(audit_key)
                    cycle_row = SvlDashboardPcbAdjustmentAuditRow(
                        move_id=move_id,
                        move_name=move_name,
                        move_date=move_date,
                        move_ref=move_ref,
                        product_id=product_id,
                        item_code=item_code,
                        item_name=item_name,
                        source_account_code=source_account_code,
                        source_account_name=source_account_name,
                        clearing_account_code=_PCB_ADJUSTMENT_CLEARING_CODE,
                        repair_clearing_amount=repair_clearing_amount,
                        origin_move_id=int(origin_info.get("origin_move_id") or 0),
                        origin_move_name=normalize_text(origin_info.get("origin_move_name")),
                        origin_basis=normalize_text(origin_info.get("origin_basis")),
                        origin_product_id=int(origin_info.get("origin_product_id") or 0),
                        origin_purchase_line_id=int(origin_info.get("origin_purchase_line_id") or 0),
                        origin_stock_move_id=int(origin_info.get("origin_stock_move_id") or 0),
                        matched_basis=matched_basis,
                        ambiguous=is_ambiguous,
                    )
                    cycle.adjustment_audit_rows.append(cycle_row)
                    if is_ambiguous:
                        continue
                    item_row = cycle_context["item_by_product_id"].get(product_id)
                    if item_row is None:
                        continue
                    item_key = (cycle_key, int(product_id or 0))
                    if audit_key in seen_item_keys[item_key]:
                        continue
                    seen_item_keys[item_key].add(audit_key)
                    item_row.adjustment_audit_rows.append(
                        SvlDashboardPcbAdjustmentAuditRow(**cycle_row.__dict__)
                    )

        for cycle in cycles:
            cycle.adjustment_audit_rows.sort(
                key=lambda row: (
                    normalize_text(row.move_date),
                    normalize_text(row.move_name),
                    normalize_text(row.item_code),
                    normalize_text(row.source_account_code),
                    normalize_text(row.matched_basis),
                )
            )
            cycle.adjustment_warning_text = self._build_pcb_adjustment_warning_text(cycle.adjustment_audit_rows)
            for item_row in list(getattr(cycle, "item_rows", None) or []):
                item_row.adjustment_audit_rows.sort(
                    key=lambda row: (
                        normalize_text(row.move_date),
                        normalize_text(row.move_name),
                        -abs(_round2(float(getattr(row, "repair_clearing_amount", 0.0) or 0.0))),
                        normalize_text(row.source_account_code),
                        normalize_text(row.matched_basis),
                    )
                )

    def _populate_pcb_case34_item_evidence_for_cycle(
        self,
        *,
        cycle: SvlDashboardPurchaseCycle,
        cycle_bill_move_ids: list[int],
        trace: dict[str, Any],
        lines_by_move: dict[int, list[dict[str, Any]]],
        move_info_map: dict[int, dict[str, Any]],
        purchase_line_product_map: dict[int, int],
        stock_move_rows_by_picking_id: dict[int, list[dict[str, Any]]] | None = None,
    ) -> None:
        item_by_product_id = {
            int(getattr(item_row, "product_id", 0) or 0): item_row
            for item_row in list(getattr(cycle, "item_rows", None) or [])
            if int(getattr(item_row, "product_id", 0) or 0) > 0
        }
        if not item_by_product_id:
            return
        cycle_picking_ids = {
            int(value or 0)
            for value in list(getattr(cycle, "picking_ids", None) or [getattr(cycle, "picking_id", 0)])
            if int(value or 0) > 0
        }
        resolved_stock_move_rows_by_picking_id: dict[int, list[dict[str, Any]]] = (
            {
                int(key): [row for row in list(value or []) if isinstance(row, dict)]
                for key, value in dict(stock_move_rows_by_picking_id or {}).items()
                if int(key or 0) > 0
            }
            if stock_move_rows_by_picking_id is not None
            else {
                int(key): [row for row in list(value or []) if isinstance(row, dict)]
                for key, value in dict(self._get_pcb_stock_move_indexes(trace).get("stock_move_rows_by_picking_id") or {}).items()
                if int(key or 0) > 0
            }
        )
        for item_row in item_by_product_id.values():
            item_row.bill_move_ids = []
            item_row.bill_refs = []
            item_row.purchase_line_ids = []
            item_row.has_item_bill = False
            item_row.bill_quantity = 0.0
            item_row.gr_quantity = 0.0
            item_row.stock_move_ids = []
            item_row.stj_move_ids = []
            item_row.stj_refs = []
            item_row.has_item_stj = False
            item_row.correction_stj_move_ids = []
            item_row.correction_stj_refs = []
            item_row.has_stj_evidence = False
            item_row.external_clearing_amount = 0.0
            item_row.external_clearing_refs = []
            item_row.external_clearing_basis = ""
            item_row.external_clearing_verified = False
            item_row.verified_audit_clearing_amount = 0.0
            item_row.eligible_case34 = False

        bill_move_ids_by_product_id: dict[int, set[int]] = defaultdict(set)
        bill_refs_by_product_id: dict[int, set[str]] = defaultdict(set)
        purchase_line_ids_by_product_id: dict[int, set[int]] = defaultdict(set)
        bill_quantity_by_product_id: dict[int, float] = defaultdict(float)
        for bill_move_id in list(cycle_bill_move_ids or []):
            if not self._is_vendor_bill_move(bill_move_id, move_info_map=move_info_map):
                continue
            bill_name = normalize_text((move_info_map.get(int(bill_move_id or 0)) or {}).get("name"))
            for line in lines_by_move.get(int(bill_move_id or 0), []):
                product_id = _many2one_id(line.get("product_id"))
                purchase_line_id = _many2one_id(line.get("purchase_line_id"))
                if product_id <= 0 and purchase_line_id > 0:
                    product_id = int(purchase_line_product_map.get(purchase_line_id) or 0)
                if product_id <= 0 or product_id not in item_by_product_id:
                    continue
                bill_move_ids_by_product_id[product_id].add(int(bill_move_id or 0))
                if bill_name:
                    bill_refs_by_product_id[product_id].add(bill_name)
                if purchase_line_id > 0:
                    purchase_line_ids_by_product_id[product_id].add(purchase_line_id)
                bill_quantity_by_product_id[product_id] = _round2(
                    bill_quantity_by_product_id[product_id] + _round2(line.get("quantity"))
                )

        stock_move_ids_by_product_id: dict[int, set[int]] = defaultdict(set)
        stj_move_ids_by_product_id: dict[int, set[int]] = defaultdict(set)
        stj_refs_by_product_id: dict[int, set[str]] = defaultdict(set)
        gr_quantity_by_product_id: dict[int, float] = defaultdict(float)
        for picking_id in cycle_picking_ids:
            for stock_move_row in resolved_stock_move_rows_by_picking_id.get(picking_id, []):
                product_id = _many2one_id(stock_move_row.get("product_id"))
                purchase_line_id = _many2one_id(stock_move_row.get("purchase_line_id"))
                if product_id <= 0 and purchase_line_id > 0:
                    product_id = int(purchase_line_product_map.get(purchase_line_id) or 0)
                if product_id <= 0 or product_id not in item_by_product_id:
                    continue
                stock_move_id = int(stock_move_row.get("id") or 0)
                if stock_move_id > 0:
                    stock_move_ids_by_product_id[product_id].add(stock_move_id)
                gr_quantity_by_product_id[product_id] = _round2(
                    gr_quantity_by_product_id[product_id]
                    + _round2(stock_move_row.get("product_qty") or stock_move_row.get("quantity"))
                )
                if purchase_line_id > 0:
                    purchase_line_ids_by_product_id[product_id].add(purchase_line_id)
                for move_id in _many2many_ids(stock_move_row.get("account_move_ids")):
                    if move_id <= 0:
                        continue
                    stj_move_ids_by_product_id[product_id].add(move_id)
                    stj_name = normalize_text((move_info_map.get(move_id) or {}).get("name"))
                    if stj_name:
                        stj_refs_by_product_id[product_id].add(stj_name)

        for product_id, item_row in item_by_product_id.items():
            item_row.bill_move_ids = sorted(bill_move_ids_by_product_id.get(product_id, set()))
            item_row.bill_refs = sorted(bill_refs_by_product_id.get(product_id, set()))
            item_row.purchase_line_ids = sorted(purchase_line_ids_by_product_id.get(product_id, set()))
            item_row.has_item_bill = bool(item_row.bill_move_ids)
            item_row.bill_quantity = _round2(bill_quantity_by_product_id.get(product_id, 0.0))
            item_row.gr_quantity = _round2(gr_quantity_by_product_id.get(product_id, 0.0))
            item_row.stock_move_ids = sorted(stock_move_ids_by_product_id.get(product_id, set()))
            item_row.stj_move_ids = sorted(stj_move_ids_by_product_id.get(product_id, set()))
            item_row.stj_refs = sorted(stj_refs_by_product_id.get(product_id, set()))
            raw_stj_move_ids, raw_stj_refs = self._pcb_item_stj_links_from_raw_lines(
                raw_lines=list(getattr(cycle, "raw_lines", None) or []),
                item_row=item_row,
                move_info_map=move_info_map,
            )
            correction_stj_move_ids = sorted(
                {int(move_id or 0) for move_id in list(getattr(cycle, "correction_stj_move_ids", None) or []) if int(move_id or 0) > 0}
            )
            correction_stj_refs = sorted(
                {
                    normalize_text(ref)
                    for ref in list(getattr(cycle, "correction_stj_refs", None) or [])
                    if normalize_text(ref)
                }
            )
            direct_raw_stj_move_ids = [
                move_id
                for move_id in list(raw_stj_move_ids or [])
                if int(move_id or 0) > 0 and int(move_id or 0) not in correction_stj_move_ids
            ]
            direct_raw_stj_refs = [
                ref
                for ref in list(raw_stj_refs or [])
                if normalize_text(ref) and normalize_text(ref) not in correction_stj_refs
            ]
            if direct_raw_stj_refs:
                item_row.stj_refs = sorted({*item_row.stj_refs, *direct_raw_stj_refs})
            if direct_raw_stj_move_ids:
                item_row.stj_move_ids = sorted({*item_row.stj_move_ids, *direct_raw_stj_move_ids})
            item_row.has_item_stj = bool(item_row.stj_move_ids or item_row.stj_refs)
            item_row.correction_stj_move_ids = correction_stj_move_ids
            item_row.correction_stj_refs = correction_stj_refs
            item_row.has_stj_evidence = self._item_has_pcb_case34_stj_evidence(item_row)

    @staticmethod
    def _item_row_account_map(item_row: SvlDashboardCycleItemRow) -> dict[str, SvlDashboardCycleAccountRow]:
        return {
            normalize_text(account_row.code).upper(): account_row
            for account_row in list(getattr(item_row, "account_rows", None) or [])
            if normalize_text(account_row.code)
        }

    def _item_matches_pcb_adjustment_source_account(
        self,
        *,
        item_row: SvlDashboardCycleItemRow,
        source_account_code: str,
        product_info_map: dict[int, dict[str, Any]],
    ) -> bool:
        clean_source_code = normalize_text(source_account_code).upper()
        if not clean_source_code:
            return False
        item_account_by_code = self._item_row_account_map(item_row)
        source_row = item_account_by_code.get(clean_source_code)
        if source_row is not None:
            if self._is_pcb_adjustment_source_account(
                account_code=clean_source_code,
                account_type=normalize_text(getattr(source_row, "account_type", "")),
            ):
                return True
        target_expense_code = normalize_text((product_info_map.get(int(getattr(item_row, "product_id", 0) or 0)) or {}).get("expense_account_code")).upper()
        return bool(target_expense_code and target_expense_code == clean_source_code)

    def _resolve_pcb_adjustment_item_from_relation(
        self,
        *,
        audit_row: SvlDashboardPcbAdjustmentAuditRow,
        item_by_product_id: dict[int, SvlDashboardCycleItemRow],
        stock_move_rows_by_id: dict[int, dict[str, Any]],
        purchase_line_product_map: dict[int, int],
    ) -> SvlDashboardCycleItemRow | None:
        for product_id in (
            int(getattr(audit_row, "product_id", 0) or 0),
            int(getattr(audit_row, "origin_product_id", 0) or 0),
        ):
            if product_id > 0 and product_id in item_by_product_id:
                return item_by_product_id[product_id]
        for purchase_line_id in (int(getattr(audit_row, "origin_purchase_line_id", 0) or 0),):
            product_id = int(purchase_line_product_map.get(purchase_line_id) or 0)
            if product_id > 0 and product_id in item_by_product_id:
                return item_by_product_id[product_id]
        origin_stock_move_id = int(getattr(audit_row, "origin_stock_move_id", 0) or 0)
        if origin_stock_move_id > 0:
            stock_move_row = stock_move_rows_by_id.get(origin_stock_move_id) or {}
            product_id = _many2one_id(stock_move_row.get("product_id"))
            if product_id <= 0:
                product_id = int(purchase_line_product_map.get(_many2one_id(stock_move_row.get("purchase_line_id"))) or 0)
            if product_id > 0 and product_id in item_by_product_id:
                return item_by_product_id[product_id]
        return None

    def _resolve_pcb_adjustment_item_by_unique_amount_source(
        self,
        *,
        cycle: SvlDashboardPurchaseCycle,
        audit_row: SvlDashboardPcbAdjustmentAuditRow,
        product_info_map: dict[int, dict[str, Any]],
    ) -> SvlDashboardCycleItemRow | None:
        source_account_code = normalize_text(getattr(audit_row, "source_account_code", "")).upper()
        amount = abs(_round2(float(getattr(audit_row, "repair_clearing_amount", 0.0) or 0.0)))
        if amount < 0.01 or not source_account_code:
            return None
        scored_candidates: list[tuple[int, SvlDashboardCycleItemRow]] = []
        for item_row in list(getattr(cycle, "item_rows", None) or []):
            if not bool(getattr(item_row, "has_item_bill", False)):
                continue
            if not self._item_matches_pcb_adjustment_source_account(
                item_row=item_row,
                source_account_code=source_account_code,
                product_info_map=product_info_map,
            ):
                continue
            item_account_by_code = self._item_row_account_map(item_row)
            problem_amount = abs(_round2(float(getattr(item_account_by_code.get("2103006"), "net_balance", 0.0) or 0.0)))
            source_amount = abs(_round2(float(getattr(item_account_by_code.get(source_account_code), "net_balance", 0.0) or 0.0)))
            score = 0
            if _amount_matches(amount, problem_amount):
                score += 30
            if _amount_matches(amount, source_amount):
                score += 20
            if score <= 0:
                continue
            scored_candidates.append((score, item_row))
        if not scored_candidates:
            return None
        best_score = max(score for score, _item_row in scored_candidates)
        best_items = [item_row for score, item_row in scored_candidates if score == best_score]
        if len(best_items) != 1:
            return None
        return best_items[0]

    def _verify_pcb_case34_adjustment_rows_for_cycle(
        self,
        *,
        cycle: SvlDashboardPurchaseCycle,
        trace: dict[str, Any],
        product_info_map: dict[int, dict[str, Any]],
    ) -> None:
        item_by_product_id = {
            int(getattr(item_row, "product_id", 0) or 0): item_row
            for item_row in list(getattr(cycle, "item_rows", None) or [])
            if int(getattr(item_row, "product_id", 0) or 0) > 0
        }
        stock_move_rows_by_id = dict(trace.get("stock_move_rows_by_id") or {})
        purchase_line_rows_by_id = dict(trace.get("purchase_line_rows_by_id") or {})
        bill_line_rows_by_product: dict[int, list[dict[str, Any]]] = {
            int(key): list(value)
            for key, value in dict(trace.get("bill_line_rows_by_product") or {}).items()
            if int(key or 0) > 0
        }
        purchase_line_product_map: dict[int, int] = {
            int(key): int(value)
            for key, value in dict(trace.get("purchase_line_product_map") or {}).items()
            if int(key or 0) > 0 and int(value or 0) > 0
        }
        for item_row in item_by_product_id.values():
            item_row.adjustment_audit_rows = []
            item_row.external_clearing_amount = 0.0
            item_row.external_clearing_refs = []
            item_row.external_clearing_basis = ""
            item_row.external_clearing_verified = False
            item_row.verified_audit_clearing_amount = 0.0
            item_row.has_stj_evidence = self._item_has_pcb_case34_stj_evidence(item_row)
            item_row.eligible_case34 = False
        seen_item_keys: dict[int, set[tuple[Any, ...]]] = defaultdict(set)
        for audit_row in list(getattr(cycle, "adjustment_audit_rows", None) or []):
            audit_row.verified_for_case34 = False
            attach_item = None
            if not bool(getattr(audit_row, "ambiguous", False)):
                attach_item = self._resolve_pcb_adjustment_item_from_relation(
                    audit_row=audit_row,
                    item_by_product_id=item_by_product_id,
                    stock_move_rows_by_id=stock_move_rows_by_id,
                    purchase_line_product_map=purchase_line_product_map,
                )
                if attach_item is None:
                    attach_item = self._resolve_pcb_adjustment_item_by_unique_amount_source(
                        cycle=cycle,
                        audit_row=audit_row,
                        product_info_map=product_info_map,
                    )
                    if attach_item is not None and not normalize_text(getattr(audit_row, "origin_basis", "")):
                        audit_row.origin_basis = "fallback.unique_amount_source_account"
                        audit_row.origin_product_id = int(getattr(attach_item, "product_id", 0) or 0)
            if attach_item is not None:
                if int(getattr(audit_row, "product_id", 0) or 0) <= 0:
                    attach_product_id = int(getattr(attach_item, "product_id", 0) or 0)
                    attach_product_info = product_info_map.get(attach_product_id) or {}
                    audit_row.product_id = attach_product_id
                    audit_row.item_code = normalize_text(attach_product_info.get("default_code")) or normalize_text(getattr(attach_item, "default_code", ""))
                    audit_row.item_name = normalize_text(attach_product_info.get("name")) or normalize_text(getattr(attach_item, "product_name", ""))
                audit_row.verified_for_case34 = bool(getattr(attach_item, "has_item_bill", False))
                item_key = int(getattr(attach_item, "product_id", 0) or 0)
                dedupe_key = self._pcb_adjustment_audit_row_key(audit_row)
                if dedupe_key not in seen_item_keys[item_key]:
                    seen_item_keys[item_key].add(dedupe_key)
                    attach_item.adjustment_audit_rows.append(SvlDashboardPcbAdjustmentAuditRow(**audit_row.__dict__))
                if audit_row.verified_for_case34:
                    self._accumulate_pcb_external_clearing_for_item(
                        item_row=attach_item,
                        audit_row=audit_row,
                    )
        self._finalize_pcb_case34_cycle_items(cycle=cycle)

    def _resolve_pcb_adjustment_relation_match_across_cycles(
        self,
        *,
        cycles: list[SvlDashboardPurchaseCycle],
        audit_row: SvlDashboardPcbAdjustmentAuditRow,
        stock_move_rows_by_id: dict[int, dict[str, Any]],
        purchase_line_product_map: dict[int, int],
    ) -> tuple[SvlDashboardPurchaseCycle, SvlDashboardCycleItemRow, str] | None:
        scored_candidates: list[tuple[int, SvlDashboardPurchaseCycle, SvlDashboardCycleItemRow, str]] = []
        origin_purchase_line_id = int(getattr(audit_row, "origin_purchase_line_id", 0) or 0)
        origin_stock_move_id = int(getattr(audit_row, "origin_stock_move_id", 0) or 0)
        origin_product_id = int(getattr(audit_row, "origin_product_id", 0) or 0)
        product_id = int(getattr(audit_row, "product_id", 0) or 0)
        for cycle in list(cycles or []):
            item_by_product_id = {
                int(getattr(item_row, "product_id", 0) or 0): item_row
                for item_row in list(getattr(cycle, "item_rows", None) or [])
                if int(getattr(item_row, "product_id", 0) or 0) > 0
            }
            attach_item = self._resolve_pcb_adjustment_item_from_relation(
                audit_row=audit_row,
                item_by_product_id=item_by_product_id,
                stock_move_rows_by_id=stock_move_rows_by_id,
                purchase_line_product_map=purchase_line_product_map,
            )
            if attach_item is None or not bool(getattr(attach_item, "has_item_bill", False)):
                continue
            attach_purchase_line_ids = {
                int(value or 0)
                for value in list(getattr(attach_item, "purchase_line_ids", None) or [])
                if int(value or 0) > 0
            }
            attach_stock_move_ids = {
                int(value or 0)
                for value in list(getattr(attach_item, "stock_move_ids", None) or [])
                if int(value or 0) > 0
            }
            attach_product_id = int(getattr(attach_item, "product_id", 0) or 0)
            score = 0
            basis = ""
            if origin_purchase_line_id > 0 and origin_purchase_line_id in attach_purchase_line_ids:
                score = 600
                basis = "external.origin_purchase_line_id"
            elif origin_stock_move_id > 0 and origin_stock_move_id in attach_stock_move_ids:
                score = 550
                basis = "external.origin_stock_move_id"
            elif origin_product_id > 0 and origin_product_id == attach_product_id:
                score = 500
                basis = "external.origin_product_id"
            elif product_id > 0 and product_id == attach_product_id:
                score = 400
                basis = "external.product_id"
            elif any(value > 0 for value in (origin_purchase_line_id, origin_stock_move_id, origin_product_id, product_id)):
                score = 200
                basis = "external.relation"
            if score <= 0:
                continue
            scored_candidates.append((score, cycle, attach_item, basis))
        if not scored_candidates:
            return None
        best_score = max(score for score, _cycle, _item_row, _basis in scored_candidates)
        best_candidates = [
            (cycle, item_row, basis)
            for score, cycle, item_row, basis in scored_candidates
            if score == best_score
        ]
        unique_candidates: list[tuple[SvlDashboardPurchaseCycle, SvlDashboardCycleItemRow, str]] = []
        seen_keys: set[tuple[int, int]] = set()
        for cycle, item_row, basis in best_candidates:
            candidate_key = (
                int(getattr(cycle, "picking_id", 0) or 0),
                int(getattr(item_row, "product_id", 0) or 0),
            )
            if candidate_key in seen_keys:
                continue
            seen_keys.add(candidate_key)
            unique_candidates.append((cycle, item_row, basis))
        if len(unique_candidates) != 1:
            return None
        return unique_candidates[0]

    def _resolve_pcb_adjustment_unique_amount_source_across_cycles(
        self,
        *,
        cycles: list[SvlDashboardPurchaseCycle],
        audit_row: SvlDashboardPcbAdjustmentAuditRow,
        product_info_map: dict[int, dict[str, Any]],
    ) -> tuple[SvlDashboardPurchaseCycle, SvlDashboardCycleItemRow] | None:
        source_account_code = normalize_text(getattr(audit_row, "source_account_code", "")).upper()
        amount = abs(_round2(float(getattr(audit_row, "repair_clearing_amount", 0.0) or 0.0)))
        if amount < 0.01 or not source_account_code:
            return None
        scored_candidates: list[tuple[int, SvlDashboardPurchaseCycle, SvlDashboardCycleItemRow]] = []
        for cycle in list(cycles or []):
            for item_row in list(getattr(cycle, "item_rows", None) or []):
                if not bool(getattr(item_row, "has_item_bill", False)):
                    continue
                if not self._item_matches_pcb_adjustment_source_account(
                    item_row=item_row,
                    source_account_code=source_account_code,
                    product_info_map=product_info_map,
                ):
                    continue
                item_account_by_code = self._item_row_account_map(item_row)
                problem_amount = abs(_round2(float(getattr(item_account_by_code.get("2103006"), "net_balance", 0.0) or 0.0)))
                source_amount = abs(_round2(float(getattr(item_account_by_code.get(source_account_code), "net_balance", 0.0) or 0.0)))
                score = 0
                if _amount_matches(amount, problem_amount):
                    score += 30
                if _amount_matches(amount, source_amount):
                    score += 20
                if score <= 0:
                    continue
                scored_candidates.append((score, cycle, item_row))
        if not scored_candidates:
            return None
        best_score = max(score for score, _cycle, _item_row in scored_candidates)
        best_candidates = [
            (cycle, item_row)
            for score, cycle, item_row in scored_candidates
            if score == best_score
        ]
        unique_candidates: list[tuple[SvlDashboardPurchaseCycle, SvlDashboardCycleItemRow]] = []
        seen_keys: set[tuple[int, int]] = set()
        for cycle, item_row in best_candidates:
            candidate_key = (
                int(getattr(cycle, "picking_id", 0) or 0),
                int(getattr(item_row, "product_id", 0) or 0),
            )
            if candidate_key in seen_keys:
                continue
            seen_keys.add(candidate_key)
            unique_candidates.append((cycle, item_row))
        if len(unique_candidates) != 1:
            return None
        return unique_candidates[0]

    def _build_pcb_case34_cross_cycle_indexes(
        self,
        *,
        cycles: list[SvlDashboardPurchaseCycle],
        product_info_map: dict[int, dict[str, Any]],
    ) -> dict[str, Any]:
        by_purchase_line_id: dict[int, list[dict[str, Any]]] = defaultdict(list)
        by_stock_move_id: dict[int, list[dict[str, Any]]] = defaultdict(list)
        by_product_id: dict[int, list[dict[str, Any]]] = defaultdict(list)
        by_source_account_code: dict[str, list[dict[str, Any]]] = defaultdict(list)
        indexed_items = 0
        indexed_bill_items = 0
        source_links = 0
        for cycle in list(cycles or []):
            cycle_key = int(getattr(cycle, "picking_id", 0) or 0)
            for item_row in list(getattr(cycle, "item_rows", None) or []):
                product_id = int(getattr(item_row, "product_id", 0) or 0)
                if product_id <= 0:
                    continue
                indexed_items += 1
                if not bool(getattr(item_row, "has_item_bill", False)):
                    continue
                indexed_bill_items += 1
                item_account_by_code = self._item_row_account_map(item_row)
                source_amount_by_code: dict[str, float] = {}
                source_codes: set[str] = set()
                for account_code, account_row in item_account_by_code.items():
                    clean_account_code = normalize_text(account_code).upper()
                    if not self._is_pcb_adjustment_source_account(
                        account_code=clean_account_code,
                        account_type=normalize_text(getattr(account_row, "account_type", "")),
                    ):
                        continue
                    source_codes.add(clean_account_code)
                    source_amount_by_code[clean_account_code] = abs(
                        _round2(float(getattr(account_row, "net_balance", 0.0) or 0.0))
                    )
                expense_account_code = normalize_text(
                    (product_info_map.get(product_id) or {}).get("expense_account_code")
                ).upper()
                if expense_account_code:
                    source_codes.add(expense_account_code)
                    source_amount_by_code.setdefault(
                        expense_account_code,
                        abs(_round2(float(getattr(item_account_by_code.get(expense_account_code), "net_balance", 0.0) or 0.0))),
                    )
                entry = {
                    "cycle": cycle,
                    "item_row": item_row,
                    "cycle_key": cycle_key,
                    "product_id": product_id,
                    "purchase_line_ids": {
                        int(value or 0)
                        for value in list(getattr(item_row, "purchase_line_ids", None) or [])
                        if int(value or 0) > 0
                    },
                    "stock_move_ids": {
                        int(value or 0)
                        for value in list(getattr(item_row, "stock_move_ids", None) or [])
                        if int(value or 0) > 0
                    },
                    "problem_amount": abs(
                        _round2(float(getattr(item_account_by_code.get("2103006"), "net_balance", 0.0) or 0.0))
                    ),
                    "source_amount_by_code": source_amount_by_code,
                }
                by_product_id[product_id].append(entry)
                for purchase_line_id in list(entry["purchase_line_ids"]):
                    by_purchase_line_id[int(purchase_line_id)].append(entry)
                for stock_move_id in list(entry["stock_move_ids"]):
                    by_stock_move_id[int(stock_move_id)].append(entry)
                for source_account_code in sorted(source_codes):
                    by_source_account_code[source_account_code].append(entry)
                    source_links += 1
        return {
            "by_purchase_line_id": by_purchase_line_id,
            "by_stock_move_id": by_stock_move_id,
            "by_product_id": by_product_id,
            "by_source_account_code": by_source_account_code,
            "indexed_items": indexed_items,
            "indexed_bill_items": indexed_bill_items,
            "source_links": source_links,
        }

    def _resolve_pcb_adjustment_relation_match_from_indexes(
        self,
        *,
        cycle_item_indexes: dict[str, Any],
        audit_row: SvlDashboardPcbAdjustmentAuditRow,
    ) -> tuple[SvlDashboardPurchaseCycle, SvlDashboardCycleItemRow, str] | None:
        origin_purchase_line_id = int(getattr(audit_row, "origin_purchase_line_id", 0) or 0)
        origin_stock_move_id = int(getattr(audit_row, "origin_stock_move_id", 0) or 0)
        origin_product_id = int(getattr(audit_row, "origin_product_id", 0) or 0)
        product_id = int(getattr(audit_row, "product_id", 0) or 0)
        candidate_entries_by_key: dict[tuple[int, int], dict[str, Any]] = {}
        for purchase_line_id in [origin_purchase_line_id]:
            if purchase_line_id <= 0:
                continue
            for entry in list((cycle_item_indexes.get("by_purchase_line_id") or {}).get(purchase_line_id, []) or []):
                candidate_entries_by_key[(int(entry.get("cycle_key") or 0), int(entry.get("product_id") or 0))] = entry
        for stock_move_id in [origin_stock_move_id]:
            if stock_move_id <= 0:
                continue
            for entry in list((cycle_item_indexes.get("by_stock_move_id") or {}).get(stock_move_id, []) or []):
                candidate_entries_by_key[(int(entry.get("cycle_key") or 0), int(entry.get("product_id") or 0))] = entry
        for resolved_product_id in [origin_product_id, product_id]:
            if resolved_product_id <= 0:
                continue
            for entry in list((cycle_item_indexes.get("by_product_id") or {}).get(resolved_product_id, []) or []):
                candidate_entries_by_key[(int(entry.get("cycle_key") or 0), int(entry.get("product_id") or 0))] = entry
        if not candidate_entries_by_key:
            return None
        scored_candidates: list[tuple[int, SvlDashboardPurchaseCycle, SvlDashboardCycleItemRow, str]] = []
        for entry in candidate_entries_by_key.values():
            score = 0
            basis = ""
            if origin_purchase_line_id > 0 and origin_purchase_line_id in set(entry.get("purchase_line_ids") or set()):
                score = 600
                basis = "external.origin_purchase_line_id"
            elif origin_stock_move_id > 0 and origin_stock_move_id in set(entry.get("stock_move_ids") or set()):
                score = 550
                basis = "external.origin_stock_move_id"
            elif origin_product_id > 0 and origin_product_id == int(entry.get("product_id") or 0):
                score = 500
                basis = "external.origin_product_id"
            elif product_id > 0 and product_id == int(entry.get("product_id") or 0):
                score = 400
                basis = "external.product_id"
            elif any(value > 0 for value in (origin_purchase_line_id, origin_stock_move_id, origin_product_id, product_id)):
                score = 200
                basis = "external.relation"
            if score <= 0:
                continue
            scored_candidates.append(
                (
                    score,
                    entry["cycle"],
                    entry["item_row"],
                    basis,
                )
            )
        if not scored_candidates:
            return None
        best_score = max(score for score, _cycle, _item_row, _basis in scored_candidates)
        best_candidates = [
            (cycle, item_row, basis)
            for score, cycle, item_row, basis in scored_candidates
            if score == best_score
        ]
        unique_candidates: list[tuple[SvlDashboardPurchaseCycle, SvlDashboardCycleItemRow, str]] = []
        seen_keys: set[tuple[int, int]] = set()
        for cycle, item_row, basis in best_candidates:
            candidate_key = (
                int(getattr(cycle, "picking_id", 0) or 0),
                int(getattr(item_row, "product_id", 0) or 0),
            )
            if candidate_key in seen_keys:
                continue
            seen_keys.add(candidate_key)
            unique_candidates.append((cycle, item_row, basis))
        if len(unique_candidates) != 1:
            return None
        return unique_candidates[0]

    def _resolve_pcb_adjustment_unique_amount_source_from_indexes(
        self,
        *,
        cycle_item_indexes: dict[str, Any],
        audit_row: SvlDashboardPcbAdjustmentAuditRow,
    ) -> tuple[SvlDashboardPurchaseCycle, SvlDashboardCycleItemRow] | None:
        source_account_code = normalize_text(getattr(audit_row, "source_account_code", "")).upper()
        amount = abs(_round2(float(getattr(audit_row, "repair_clearing_amount", 0.0) or 0.0)))
        if amount < 0.01 or not source_account_code:
            return None
        scored_candidates: list[tuple[int, SvlDashboardPurchaseCycle, SvlDashboardCycleItemRow]] = []
        for entry in list((cycle_item_indexes.get("by_source_account_code") or {}).get(source_account_code, []) or []):
            problem_amount = abs(_round2(float(entry.get("problem_amount") or 0.0)))
            source_amount = abs(
                _round2(float((dict(entry.get("source_amount_by_code") or {})).get(source_account_code, 0.0) or 0.0))
            )
            score = 0
            if _amount_matches(amount, problem_amount):
                score += 30
            if _amount_matches(amount, source_amount):
                score += 20
            if score <= 0:
                continue
            scored_candidates.append((score, entry["cycle"], entry["item_row"]))
        if not scored_candidates:
            return None
        best_score = max(score for score, _cycle, _item_row in scored_candidates)
        best_candidates = [
            (cycle, item_row)
            for score, cycle, item_row in scored_candidates
            if score == best_score
        ]
        unique_candidates: list[tuple[SvlDashboardPurchaseCycle, SvlDashboardCycleItemRow]] = []
        seen_keys: set[tuple[int, int]] = set()
        for cycle, item_row in best_candidates:
            candidate_key = (
                int(getattr(cycle, "picking_id", 0) or 0),
                int(getattr(item_row, "product_id", 0) or 0),
            )
            if candidate_key in seen_keys:
                continue
            seen_keys.add(candidate_key)
            unique_candidates.append((cycle, item_row))
        if len(unique_candidates) != 1:
            return None
        return unique_candidates[0]

    def _augment_pcb_case34_external_clearing_for_cycles(
        self,
        *,
        cycles: list[SvlDashboardPurchaseCycle],
        adjustment_moves: list[dict[str, Any]],
        trace: dict[str, Any],
        account_info_map: dict[int, dict[str, Any]],
        product_info_map: dict[int, dict[str, Any]],
    ) -> None:
        if not cycles or not adjustment_moves:
            for cycle in cycles:
                self._finalize_pcb_case34_cycle_items(cycle=cycle)
            return
        stock_move_rows_by_id = dict(trace.get("stock_move_rows_by_id") or {})
        purchase_line_product_map: dict[int, int] = {
            int(key): int(value)
            for key, value in dict(trace.get("purchase_line_product_map") or {}).items()
            if int(key or 0) > 0 and int(value or 0) > 0
        }
        index_started = perf_counter()
        cycle_item_indexes = self._build_pcb_case34_cross_cycle_indexes(
            cycles=cycles,
            product_info_map=product_info_map,
        )
        self._log(
            f"[BENCH]   adj/external_index: {(perf_counter() - index_started) * 1000.0:.0f}ms | "
            f"{int(cycle_item_indexes.get('indexed_bill_items') or 0)} bill_items | "
            f"{int(cycle_item_indexes.get('source_links') or 0)} source_links"
        )
        match_started = perf_counter()
        scanned_source_rows = 0
        attached_rows = 0
        seen_item_keys: dict[tuple[int, int], set[tuple[Any, ...]]] = defaultdict(set)
        seen_item_identity_keys: dict[tuple[int, int], set[tuple[Any, ...]]] = defaultdict(set)
        for cycle in cycles:
            cycle_key = int(getattr(cycle, "picking_id", 0) or 0)
            for item_row in list(getattr(cycle, "item_rows", None) or []):
                item_key = (cycle_key, int(getattr(item_row, "product_id", 0) or 0))
                seen_item_keys[item_key].update(
                    self._pcb_adjustment_audit_row_key(row)
                    for row in list(getattr(item_row, "adjustment_audit_rows", None) or [])
                )
                seen_item_identity_keys[item_key].update(
                    self._pcb_adjustment_identity_key(row)
                    for row in list(getattr(item_row, "adjustment_audit_rows", None) or [])
                )
        for move in adjustment_moves:
            move_id = int(move.get("move_id") or 0)
            if move_id <= 0:
                continue
            move_name = normalize_text(move.get("move_name"))
            move_ref = normalize_text(move.get("move_ref"))
            move_date = normalize_text(move.get("move_date"))[:10]
            move_lines = list(move.get("lines") or [])
            clearing_rows = []
            source_rows = []
            for row in move_lines:
                account_info = account_info_map.get(_many2one_id(row.get("account_id"))) or {}
                account_code = normalize_text(account_info.get("code")).upper()
                account_type = normalize_text(account_info.get("account_type")).lower()
                if account_code == _PCB_ADJUSTMENT_CLEARING_CODE:
                    clearing_rows.append(row)
                if self._is_pcb_adjustment_source_account(account_code=account_code, account_type=account_type):
                    source_rows.append(row)
            if not clearing_rows or not source_rows:
                continue
            for source_row in source_rows:
                scanned_source_rows += 1
                relation = self._resolve_pcb_adjustment_relation_values(
                    source_row=source_row,
                    clearing_rows=clearing_rows,
                    stock_move_rows_by_id=stock_move_rows_by_id,
                    purchase_line_product_map=purchase_line_product_map,
                )
                origin_info = self._resolve_pcb_adjustment_origin_info(
                    move=move,
                    source_row=source_row,
                    stock_move_rows_by_id=stock_move_rows_by_id,
                    purchase_line_product_map=purchase_line_product_map,
                )
                effective_relation = dict(relation)
                origin_relation = dict(origin_info.get("relation") or {})
                for field_name in ("product_id", "purchase_line_id", "stock_move_id", "bill_move_id", "picking_id"):
                    if int(effective_relation.get(field_name) or 0) > 0:
                        continue
                    if int(origin_relation.get(field_name) or 0) > 0:
                        effective_relation[field_name] = int(origin_relation.get(field_name) or 0)
                account_info = account_info_map.get(_many2one_id(source_row.get("account_id"))) or {}
                source_account_code = normalize_text(account_info.get("code")).upper()
                source_account_name = normalize_text(account_info.get("name"))
                product_id = int(effective_relation.get("product_id") or 0)
                product_info = product_info_map.get(product_id) or {}
                external_row = SvlDashboardPcbAdjustmentAuditRow(
                    move_id=move_id,
                    move_name=move_name,
                    move_date=move_date,
                    move_ref=move_ref,
                    product_id=product_id,
                    item_code=normalize_text(product_info.get("default_code")),
                    item_name=normalize_text(product_info.get("name")),
                    source_account_code=source_account_code,
                    source_account_name=source_account_name,
                    clearing_account_code=_PCB_ADJUSTMENT_CLEARING_CODE,
                    repair_clearing_amount=self._pcb_adjustment_line_amount(source_row),
                    origin_move_id=int(origin_info.get("origin_move_id") or 0),
                    origin_move_name=normalize_text(origin_info.get("origin_move_name")),
                    origin_basis=normalize_text(origin_info.get("origin_basis")),
                    origin_product_id=int(origin_info.get("origin_product_id") or 0),
                    origin_purchase_line_id=int(origin_info.get("origin_purchase_line_id") or 0),
                    origin_stock_move_id=int(origin_info.get("origin_stock_move_id") or 0),
                    matched_basis="",
                    ambiguous=bool(False),
                )
                relation_match = self._resolve_pcb_adjustment_relation_match_from_indexes(
                    cycle_item_indexes=cycle_item_indexes,
                    audit_row=external_row,
                )
                if relation_match is not None:
                    matched_cycle, matched_item, matched_basis = relation_match
                    external_row.verified_for_case34 = True
                    external_row.matched_basis = matched_basis
                else:
                    unique_match = self._resolve_pcb_adjustment_unique_amount_source_from_indexes(
                        cycle_item_indexes=cycle_item_indexes,
                        audit_row=external_row,
                    )
                    if unique_match is None:
                        continue
                    matched_cycle, matched_item = unique_match
                    external_row.verified_for_case34 = True
                    external_row.matched_basis = "external.unique_amount_source_account"
                    if not normalize_text(external_row.origin_basis):
                        external_row.origin_basis = "fallback.unique_amount_source_account.external"
                matched_product_id = int(getattr(matched_item, "product_id", 0) or 0)
                if int(getattr(external_row, "product_id", 0) or 0) <= 0 and matched_product_id > 0:
                    matched_product_info = product_info_map.get(matched_product_id) or {}
                    external_row.product_id = matched_product_id
                    external_row.item_code = normalize_text(matched_product_info.get("default_code")) or normalize_text(getattr(matched_item, "default_code", ""))
                    external_row.item_name = normalize_text(matched_product_info.get("name")) or normalize_text(getattr(matched_item, "product_name", ""))
                    if int(getattr(external_row, "origin_product_id", 0) or 0) <= 0:
                        external_row.origin_product_id = matched_product_id
                cycle_key = int(getattr(matched_cycle, "picking_id", 0) or 0)
                item_key = (cycle_key, matched_product_id)
                dedupe_key = self._pcb_adjustment_audit_row_key(external_row)
                identity_key = self._pcb_adjustment_identity_key(external_row)
                if dedupe_key in seen_item_keys[item_key] or identity_key in seen_item_identity_keys[item_key]:
                    continue
                seen_item_keys[item_key].add(dedupe_key)
                seen_item_identity_keys[item_key].add(identity_key)
                matched_item.adjustment_audit_rows.append(SvlDashboardPcbAdjustmentAuditRow(**external_row.__dict__))
                attached_rows += 1
                self._accumulate_pcb_external_clearing_for_item(
                    item_row=matched_item,
                    audit_row=external_row,
                )
        self._log(
            f"[BENCH]   adj/external_match: {(perf_counter() - match_started) * 1000.0:.0f}ms | "
            f"{scanned_source_rows} source_rows | {attached_rows} attached"
        )
        finalize_started = perf_counter()
        for cycle in cycles:
            self._finalize_pcb_case34_cycle_items(cycle=cycle)
        self._log(
            f"[BENCH]   adj/external_finalize: {(perf_counter() - finalize_started) * 1000.0:.0f}ms | "
            f"{len(cycles)} cycles"
        )

    def _rebuild_pcb_case2_repair_rows_for_cycles(
        self,
        *,
        cycles: list[SvlDashboardPurchaseCycle],
        adjustment_moves: list[dict[str, Any]],
        trace: dict[str, Any],
        all_ledger_rows: list[dict[str, Any]],
        account_info_map: dict[int, dict[str, Any]],
        move_info_map: dict[int, dict[str, Any]],
        product_info_map: dict[int, dict[str, Any]],
    ) -> None:
        if not cycles:
            return
        rebuild_prep_started = perf_counter()
        bill_rows_by_id = dict(trace.get("bill_rows_by_id") or {})
        stock_move_rows_by_id = dict(trace.get("stock_move_rows_by_id") or {})
        stock_move_indexes = self._get_pcb_stock_move_indexes(trace)
        stock_move_rows_by_picking_id: dict[int, list[dict[str, Any]]] = {
            int(key): [row for row in list(value or []) if isinstance(row, dict)]
            for key, value in dict(stock_move_indexes.get("stock_move_rows_by_picking_id") or {}).items()
            if int(key or 0) > 0
        }
        purchase_line_product_map: dict[int, int] = {
            int(key): int(value)
            for key, value in dict(trace.get("purchase_line_product_map") or {}).items()
            if int(key or 0) > 0 and int(value or 0) > 0
        }
        purchase_line_po_name_map: dict[int, str] = {
            int(key): str(value)
            for key, value in dict(trace.get("purchase_line_po_name_map") or {}).items()
            if int(key or 0) > 0 and normalize_text(value)
        }
        purchase_line_rows_by_id = dict(trace.get("purchase_line_rows_by_id") or {})
        bill_line_rows_by_product = dict(trace.get("bill_line_rows_by_product") or {})
        payment_move_ids_by_product: dict[int, list[int]] = {
            int(key): [int(value) for value in list(values or []) if int(value or 0) > 0]
            for key, values in dict(trace.get("payment_move_ids_by_product") or {}).items()
            if int(key or 0) > 0
        }
        bank_move_ids_by_product: dict[int, list[int]] = {
            int(key): [int(value) for value in list(values or []) if int(value or 0) > 0]
            for key, values in dict(trace.get("bank_move_ids_by_product") or {}).items()
            if int(key or 0) > 0
        }
        lines_by_move: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for row in all_ledger_rows:
            move_id = _many2one_id(row.get("move_id"))
            if move_id > 0:
                lines_by_move[move_id].append(row)
        case89_context = self._build_pcb_case8_case9_context(trace)
        bill_move_ids_by_name: dict[str, set[int]] = defaultdict(set)
        for bill_move_id, bill_row in bill_rows_by_id.items():
            clean_bill_move_id = int(bill_move_id or 0)
            if clean_bill_move_id <= 0:
                continue
            for bill_name in {
                normalize_text(bill_row.get("name")),
                normalize_text((move_info_map.get(clean_bill_move_id) or {}).get("name")),
            }:
                if bill_name:
                    bill_move_ids_by_name[bill_name].add(clean_bill_move_id)
        self._log(
            f"[BENCH]   adj/rebuild/index_prep: {(perf_counter() - rebuild_prep_started) * 1000.0:.0f}ms | "
            f"{len(lines_by_move)} moves | {len(cycles)} cycles"
        )
        cycle_context_by_key: dict[int, dict[str, Any]] = {}
        cycle_prefill_started = perf_counter()
        for cycle in cycles:
            bill_move_ids = sorted(
                {
                    int(move_id)
                    for move_id in list(getattr(cycle, "bill_move_ids", None) or [])
                    if int(move_id or 0) > 0
                }
                | {
                    int(move_id)
                    for link_row in list(getattr(cycle, "case1_link_rows", None) or [])
                    for move_id in [getattr(link_row, "bill_move_id", 0)]
                    if int(move_id or 0) > 0
                }
                | {
                    int(move_id)
                    for item_row in list(getattr(cycle, "item_rows", None) or [])
                    for move_id in list(getattr(item_row, "bill_move_ids", None) or [])
                    if int(move_id or 0) > 0
                }
                | {
                    int(move_id)
                    for bill_ref in list(getattr(cycle, "bill_refs", None) or [])
                    for move_id in bill_move_ids_by_name.get(normalize_text(bill_ref), set())
                    if int(move_id or 0) > 0
                }
            )
            bill_move_ids = self._filter_vendor_bill_move_ids(
                bill_move_ids,
                move_info_map=move_info_map,
                bill_rows_by_id=bill_rows_by_id,
            )
            cycle_partner_id = int(getattr(cycle, "partner_id", 0) or 0)
            if cycle_partner_id <= 0:
                cycle_partner_id = next(
                    (
                        _many2one_id((move_info_map.get(bill_move_id) or {}).get("partner_id"))
                        or _many2one_id((bill_rows_by_id.get(bill_move_id) or {}).get("partner_id"))
                        for bill_move_id in bill_move_ids
                        if (
                            _many2one_id((move_info_map.get(bill_move_id) or {}).get("partner_id")) > 0
                            or _many2one_id((bill_rows_by_id.get(bill_move_id) or {}).get("partner_id")) > 0
                        )
                    ),
                    0,
                )
            cycle_payment_move_ids = sorted(
                {
                    int(move_id)
                    for move_id in list(getattr(cycle, "payment_move_ids", None) or [])
                    if int(move_id or 0) > 0
                }
                | {
                    int(move_id)
                    for link_row in list(getattr(cycle, "case1_link_rows", None) or [])
                    for move_id in list(getattr(link_row, "payment_move_ids", None) or [])
                    if int(move_id or 0) > 0
                }
            )
            cycle_bank_move_ids = sorted(
                {
                    int(move_id)
                    for move_id in list(getattr(cycle, "bank_move_ids", None) or [])
                    if int(move_id or 0) > 0
                }
                | {
                    int(move_id)
                    for link_row in list(getattr(cycle, "case1_link_rows", None) or [])
                    for move_id in list(getattr(link_row, "bank_move_ids", None) or [])
                    if int(move_id or 0) > 0
                }
            )
            cycle_direct_stj_move_ids = sorted(
                {
                    int(move_id)
                    for item_row in list(getattr(cycle, "item_rows", None) or [])
                    for move_id in list(getattr(item_row, "stj_move_ids", None) or [])
                    if int(move_id or 0) > 0
                }
                | {
                    int(move_id)
                    for link_row in list(getattr(cycle, "case1_link_rows", None) or [])
                    for move_id in list(getattr(link_row, "stj_move_ids", None) or [])
                    if int(move_id or 0) > 0
                }
            )
            cycle_correction_move_ids = sorted(
                {
                    int(move_id)
                    for move_id in list(getattr(cycle, "correction_stj_move_ids", None) or [])
                    if int(move_id or 0) > 0
                }
            )
            all_cycle_move_ids = sorted(
                set(bill_move_ids)
                | set(cycle_payment_move_ids)
                | set(cycle_bank_move_ids)
                | set(cycle_direct_stj_move_ids)
                | set(cycle_correction_move_ids)
            )
            cycle_context_by_key[id(cycle)] = {
                "bill_move_ids": list(bill_move_ids),
                "partner_id": int(cycle_partner_id or 0),
                "payment_move_ids": cycle_payment_move_ids,
                "bank_move_ids": cycle_bank_move_ids,
                "all_cycle_move_ids": all_cycle_move_ids,
            }
            self._populate_pcb_case34_item_evidence_for_cycle(
                cycle=cycle,
                cycle_bill_move_ids=bill_move_ids,
                trace=trace,
                lines_by_move=lines_by_move,
                move_info_map=move_info_map,
                purchase_line_product_map=purchase_line_product_map,
                stock_move_rows_by_picking_id=stock_move_rows_by_picking_id,
            )
            self._verify_pcb_case34_adjustment_rows_for_cycle(
                cycle=cycle,
                trace=trace,
                product_info_map=product_info_map,
            )
            self._populate_pcb_receipt_recovery_evidence_for_cycle(cycle=cycle, trace=trace)
            self._populate_pcb_case8_case9_evidence_for_cycle(
                cycle=cycle,
                trace=trace,
                product_info_map=product_info_map,
                case89_context=case89_context,
            )
        self._log(
            f"[BENCH]   adj/rebuild/cycle_prefill: {(perf_counter() - cycle_prefill_started) * 1000.0:.0f}ms | "
            f"{len(cycles)} cycles"
        )
        external_started = perf_counter()
        self._augment_pcb_case34_external_clearing_for_cycles(
            cycles=cycles,
            adjustment_moves=adjustment_moves,
            trace=trace,
            account_info_map=account_info_map,
            product_info_map=product_info_map,
        )
        self._log(
            f"[BENCH]   adj/rebuild/external_clearing: {(perf_counter() - external_started) * 1000.0:.0f}ms | "
            f"{len(adjustment_moves)} moves"
        )
        finalize_started = perf_counter()
        classified_cycles = 0
        healthy_skipped = 0
        total_repair_rows = 0
        for cycle in cycles:
            cycle_context = cycle_context_by_key.get(id(cycle), {})
            self._apply_pcb_item_classifier(
                cycle=cycle,
                product_info_map=product_info_map,
                lines_by_move=lines_by_move,
                account_info_map=account_info_map,
                cycle_bill_move_ids=list(cycle_context.get("bill_move_ids") or []),
            )
            # Post-classifier re-promote: jika cycle di-downgrade ke "partial /
            # clearing_reclass" saat assembly (sebelum classifier berjalan), tapi
            # setelah classifier ada item Case 3 genuine (has_stj_evidence=True +
            # bill_hit_role=expense dengan clearing problem), cycle harus kembali
            # ke "problem". Item tersebut mengalami double recognition (persediaan
            # dari STJ + biaya dari bill) dan clearing 1108099-nya belum ditutup —
            # bukan post-repair transit yang aman di-reclass.
            if (
                normalize_text(getattr(cycle, "cycle_status", "")).lower() == "partial"
                and normalize_text(getattr(cycle, "partial_group_key", "")).lower() == "clearing_reclass"
            ):
                case3_clearing_items = [
                    item_row
                    for item_row in list(getattr(cycle, "item_rows", None) or [])
                    if normalize_text(getattr(item_row, "primary_case", "")).lower() == "case3"
                    and bool(getattr(item_row, "has_stj_evidence", False))
                    and normalize_text(getattr(item_row, "bill_hit_role", "")).lower() == "expense"
                    and abs(_round2(self._item_row_account_balance(item_row, "1108099"))) >= 0.01
                ]
                if case3_clearing_items:
                    cycle.cycle_status = "problem"
                    cycle.partial_group_key = ""
                    cycle.partial_group_label = ""
                    for r in list(getattr(cycle, "account_rows", None) or []):
                        if normalize_text(getattr(r, "code", "")).upper() == "1108099" and abs(_round2(r.net_balance)) >= 0.01:
                            r.status = "problem"
            cycle_status_final = normalize_text(getattr(cycle, "cycle_status", "")).lower()
            cycle.case1_link_rows = self._build_pcb_case1_link_rows(
                cycle_status=cycle_status_final,
                picking_id=int(getattr(cycle, "picking_id", 0) or 0),
                picking_name=normalize_text(getattr(cycle, "picking_name", "")),
                gr_date=normalize_text(getattr(cycle, "gr_date", "")),
                partner_name=normalize_text(getattr(cycle, "partner_name", "")),
                group_picking_ids=list(getattr(cycle, "picking_ids", None) or [getattr(cycle, "picking_id", 0)]),
                cycle_stj_move_ids=sorted(
                    {
                        int(move_id or 0)
                        for item_row in list(getattr(cycle, "item_rows", None) or [])
                        for move_id in list(getattr(item_row, "stj_move_ids", None) or [])
                        if int(move_id or 0) > 0
                    }
                    | {
                        int(move_id or 0)
                        for move_id in list(getattr(cycle, "correction_stj_move_ids", None) or [])
                        if int(move_id or 0) > 0
                    }
                ),
                product_ids_in_picking=list(getattr(cycle, "product_ids", None) or []),
                account_rows=list(getattr(cycle, "account_rows", None) or []),
                item_rows=list(getattr(cycle, "item_rows", None) or []),
                bill_move_ids=list(cycle_context.get("bill_move_ids") or []),
                payment_move_ids=list(cycle_context.get("payment_move_ids") or []),
                bank_move_ids=list(cycle_context.get("bank_move_ids") or []),
                all_cycle_move_ids=list(cycle_context.get("all_cycle_move_ids") or []),
                lines_by_move=lines_by_move,
                account_info_map=account_info_map,
                move_info_map=move_info_map,
                product_info_map=product_info_map,
                bill_rows_by_id=bill_rows_by_id,
                bill_line_rows_by_product=bill_line_rows_by_product,
                purchase_line_rows_by_id=purchase_line_rows_by_id,
                purchase_line_product_map=purchase_line_product_map,
                purchase_line_po_name_map=purchase_line_po_name_map,
                stock_move_rows_by_id=stock_move_rows_by_id,
                payment_move_ids_by_product=payment_move_ids_by_product,
                bank_move_ids_by_product=bank_move_ids_by_product,
            )
            if cycle_status_final == "healthy":
                cycle.primary_case = ""
                cycle.case2_repair_rows = []
                healthy_skipped += 1
                continue
            # If the cycle was downgraded to "partial" by the GRNI guard in
            # _apply_pcb_item_classifier, it has no genuine problem — skip the
            # cycle-level case classifier (which would otherwise fall through to
            # "case_lainnya" since all problem flags were cleared).
            if cycle_status_final == "partial" and not normalize_text(getattr(cycle, "primary_case", "")).strip():
                cycle.case2_repair_rows = []
                classified_cycles += 1
                continue
            classified_cycles += 1
            pcb_case = self._classify_pcb_cycle_case(
                account_rows=list(getattr(cycle, "account_rows", None) or []),
                raw_lines=list(getattr(cycle, "raw_lines", None) or []),
                bill_refs=list(getattr(cycle, "bill_refs", None) or []),
                item_rows=list(getattr(cycle, "item_rows", None) or []),
                primary_case=normalize_text(getattr(cycle, "primary_case", "")),
            )
            cycle.primary_case = pcb_case
            grouped_rows: list[SvlDashboardPcbCase2RepairRow] = []
            for case_key in _PCB_REPAIRABLE_CASES:
                if case_key not in _PCB_MULTI_LINE_CASES:
                    continue
                case_items = [
                    item_row
                    for item_row in list(getattr(cycle, "item_rows", None) or [])
                    if normalize_text(getattr(item_row, "primary_case", "")).lower() == case_key
                ]
                if not case_items:
                    continue
                grouped_rows.extend(
                    self._build_pcb_case2_repair_rows(
                        pcb_case=case_key,
                        cycle_status=normalize_text(getattr(cycle, "cycle_status", "")),
                        picking_id=int(getattr(cycle, "picking_id", 0) or 0),
                        picking_ids=list(getattr(cycle, "picking_ids", None) or [getattr(cycle, "picking_id", 0)]),
                        picking_name=normalize_text(getattr(cycle, "picking_name", "")),
                        gr_date=normalize_text(getattr(cycle, "gr_date", "")),
                        partner_name=normalize_text(getattr(cycle, "partner_name", "")),
                        partner_id=int(cycle_context.get("partner_id") or 0),
                        po_names=list(getattr(cycle, "purchase_orders", None) or []),
                        bill_move_ids=list(cycle_context.get("bill_move_ids") or []),
                        payment_move_ids=list(cycle_context.get("payment_move_ids") or []),
                        bank_move_ids=list(cycle_context.get("bank_move_ids") or []),
                        all_cycle_move_ids=list(cycle_context.get("all_cycle_move_ids") or []),
                        bill_refs=list(getattr(cycle, "bill_refs", None) or []),
                        item_rows=case_items,
                        raw_lines=list(getattr(cycle, "raw_lines", None) or []),
                        lines_by_move=lines_by_move,
                        account_info_map=account_info_map,
                        move_info_map=move_info_map,
                        product_info_map=product_info_map,
                        bill_rows_by_id=bill_rows_by_id,
                        purchase_line_product_map=purchase_line_product_map,
                        purchase_line_po_name_map=purchase_line_po_name_map,
                        stock_move_rows_by_id=stock_move_rows_by_id,
                        payment_move_ids_by_product=payment_move_ids_by_product,
                        bank_move_ids_by_product=bank_move_ids_by_product,
                    )
                )
            cycle.case2_repair_rows = grouped_rows
            total_repair_rows += len(grouped_rows)
        self._log(
            f"[BENCH]   adj/rebuild/finalize: {(perf_counter() - finalize_started) * 1000.0:.0f}ms | "
            f"{classified_cycles} classified | {healthy_skipped} healthy_skipped | {total_repair_rows} repair_rows"
        )

    @staticmethod
    def _pcb_text_has_internal_signal(value: Any) -> bool:
        clean = normalize_text(value).lower()
        if not clean:
            return False
        normalized = f" {clean.replace('_', ' ').replace('/', ' / ')} "
        if " / int / " in normalized:
            return True
        return any(token in normalized for token in _PCB_INTERNAL_SIGNAL_PHRASES)

    def _classify_pcb_document_flow(
        self,
        *,
        group_picking_ids: list[int],
        product_ids_in_picking: list[int],
        po_ids_in_group: list[int],
        po_names: list[str],
        partner_id: int,
        partner_name: str,
        bill_move_ids: list[int],
        payment_move_ids: list[int],
        bank_move_ids: list[int],
        purchase_line_ids_by_picking_id: dict[int, set[int]],
        bill_line_rows_by_product: dict[int, list[dict[str, Any]]],
        bill_rows_by_id: dict[int, dict[str, Any]],
        picking_rows_by_id: dict[int, dict[str, Any]],
        inventory_types_by_picking: dict[int, set[str]],
    ) -> tuple[str, str, list[str]]:
        cycle_purchase_line_ids: set[int] = set()
        for picking_id in group_picking_ids:
            cycle_purchase_line_ids.update(purchase_line_ids_by_picking_id.get(int(picking_id or 0), set()))
        cycle_inventory_types = sorted(
            {
                normalize_text(value).strip().lower()
                for picking_id in group_picking_ids
                for value in list(inventory_types_by_picking.get(int(picking_id or 0), set()) or [])
                if normalize_text(value)
            }
        )

        po_names_set = {
            normalize_text(value)
            for value in list(po_names or [])
            if normalize_text(value)
        }
        cycle_bill_move_ids = {
            int(move_id or 0)
            for move_id in list(bill_move_ids or [])
            if int(move_id or 0) > 0
        }

        has_bill_purchase_line_link = False
        for product_id in product_ids_in_picking:
            for bill_line in list(bill_line_rows_by_product.get(int(product_id or 0), []) or []):
                move_id = _many2one_id(bill_line.get("move_id"))
                if move_id not in cycle_bill_move_ids:
                    continue
                purchase_line_id = _many2one_id(bill_line.get("purchase_line_id"))
                if purchase_line_id > 0 and purchase_line_id in cycle_purchase_line_ids:
                    has_bill_purchase_line_link = True
                    break
            if has_bill_purchase_line_link:
                break

        has_bill_origin_po_link = any(
            normalize_text((bill_rows_by_id.get(bill_id) or {}).get("invoice_origin")) in po_names_set
            for bill_id in cycle_bill_move_ids
        )
        internal_signal_hits: list[str] = []
        for picking_id in group_picking_ids:
            picking_row = dict(picking_rows_by_id.get(int(picking_id or 0)) or {})
            for field_name in ("name", "origin"):
                text = normalize_text(picking_row.get(field_name))
                if text and self._pcb_text_has_internal_signal(text):
                    internal_signal_hits.append(text)

        has_po = bool(po_ids_in_group)
        has_purchase_lines = bool(cycle_purchase_line_ids)
        has_bills = bool(cycle_bill_move_ids)
        has_payment_chain = bool(payment_move_ids or bank_move_ids)
        has_partner = int(partner_id or 0) > 0 or bool(normalize_text(partner_name))
        hard_purchase_proof = has_po or has_purchase_lines or has_bill_purchase_line_link or has_bill_origin_po_link
        soft_purchase_proof = has_bills or has_payment_chain
        purchase_inventory_types = [value for value in cycle_inventory_types if value in _PCB_PURCHASE_INVENTORY_TYPES]
        non_purchase_inventory_types = [value for value in cycle_inventory_types if value in _PCB_NON_PURCHASE_INVENTORY_TYPES]

        reasons: list[str] = []
        if purchase_inventory_types and not non_purchase_inventory_types:
            classification = _PCB_DOCUMENT_CLASS_PURCHASE_BACKED
            reasons.append(
                "Inventory Type = "
                + ", ".join(_PCB_INVENTORY_TYPE_LABEL.get(value, value) for value in purchase_inventory_types)
            )
            if has_po:
                reasons.append("PO terhubung ke picking cycle")
            if has_purchase_lines:
                reasons.append("Purchase line ditemukan pada stock move incoming")
            if has_bill_purchase_line_link:
                reasons.append("Vendor bill match via purchase_line")
            elif has_bill_origin_po_link:
                reasons.append("Vendor bill match via invoice_origin PO")
            if has_payment_chain:
                reasons.append("Payment/bank chain tersedia")
        elif non_purchase_inventory_types:
            classification = _PCB_DOCUMENT_CLASS_NON_PURCHASE
            reasons.append(
                "Inventory Type = "
                + ", ".join(_PCB_INVENTORY_TYPE_LABEL.get(value, value) for value in non_purchase_inventory_types)
            )
            if purchase_inventory_types:
                reasons.append(
                    "Cycle mixed inventory type: "
                    + ", ".join(_PCB_INVENTORY_TYPE_LABEL.get(value, value) for value in purchase_inventory_types)
                )
            if not has_po:
                reasons.append("Tidak ada PO yang terhubung")
            if not has_bills:
                reasons.append("Tidak ada vendor bill yang cocok")
            if not has_payment_chain:
                reasons.append("Tidak ada payment/bank chain")
        elif hard_purchase_proof:
            classification = _PCB_DOCUMENT_CLASS_PURCHASE_BACKED
            if has_po:
                reasons.append("PO terhubung ke picking cycle")
            if has_purchase_lines:
                reasons.append("Purchase line ditemukan pada stock move incoming")
            if has_bill_purchase_line_link:
                reasons.append("Vendor bill match via purchase_line")
            elif has_bill_origin_po_link:
                reasons.append("Vendor bill match via invoice_origin PO")
            if has_payment_chain:
                reasons.append("Payment/bank chain tersedia")
        elif soft_purchase_proof and (has_partner or has_bills):
            classification = _PCB_DOCUMENT_CLASS_PURCHASE_LIKELY
            if has_bills:
                reasons.append("Vendor bill ditemukan tanpa link PO yang utuh")
            if has_payment_chain:
                reasons.append("Payment/bank chain mendukung bill")
            if has_partner:
                reasons.append("Partner cycle teridentifikasi")
            if internal_signal_hits:
                reasons.append("Ada sinyal internal pada nama/origin; perlu review")
        else:
            classification = _PCB_DOCUMENT_CLASS_NON_PURCHASE
            if internal_signal_hits:
                reasons.append("Nama/origin picking mengandung sinyal internal/intercompany")
            if not has_po:
                reasons.append("Tidak ada PO yang terhubung")
            if not has_bills:
                reasons.append("Tidak ada vendor bill yang cocok")
            if not has_payment_chain:
                reasons.append("Tidak ada payment/bank chain")

        deduped_reasons: list[str] = []
        seen_reasons: set[str] = set()
        for reason in reasons:
            clean_reason = normalize_text(reason)
            if not clean_reason or clean_reason in seen_reasons:
                continue
            seen_reasons.add(clean_reason)
            deduped_reasons.append(clean_reason)
        return (
            classification,
            _PCB_DOCUMENT_CLASS_LABEL.get(classification, classification),
            deduped_reasons[:4],
        )

    @staticmethod
    def _gate_pcb_cycle_status_by_document_classification(
        *,
        document_classification: str,
        cycle_status: str,
        partial_group_key: str,
        partial_group_label: str,
        account_rows: list[SvlDashboardCycleAccountRow],
        item_rows: list[SvlDashboardCycleItemRow],
    ) -> tuple[str, str, str, int, int, bool]:
        if normalize_text(document_classification) == _PCB_DOCUMENT_CLASS_PURCHASE_BACKED:
            problem_count = sum(1 for row in account_rows if normalize_text(getattr(row, "status", "")) == "problem")
            info_count = sum(1 for row in account_rows if normalize_text(getattr(row, "status", "")) == "info")
            return cycle_status, partial_group_key, partial_group_label, problem_count, info_count, info_count > 0

        for account_row in account_rows:
            if normalize_text(getattr(account_row, "status", "")) == "problem":
                account_row.status = "info"
        for item_row in item_rows:
            for account_row in list(getattr(item_row, "account_rows", None) or []):
                if normalize_text(getattr(account_row, "status", "")) == "problem":
                    account_row.status = "info"
        info_count = sum(1 for row in account_rows if normalize_text(getattr(row, "status", "")) == "info")
        return "healthy", "", "", 0, info_count, info_count > 0

    def _build_purchase_cycles(
        self,
        *,
        trace: dict[str, Any],
        all_ledger_rows: list[dict[str, Any]],
        account_info_map: dict[int, dict[str, Any]],
        move_info_map: dict[int, dict[str, Any]],
        product_info_map: dict[int, dict[str, Any]],
        warnings: list[str],
        problem_codes: frozenset[str],
        info_codes: frozenset[str],
        build_repair_rows: bool = True,
    ) -> list[SvlDashboardPurchaseCycle]:
        prep_started = perf_counter()
        """Build one SvlDashboardPurchaseCycle per picking (GR), Langkah 4–9.

        Bills are linked via picking → PO → invoice_ids (PO-based), NOT product-based.
        Payments shared across multiple cycles are allocated proportionally by bill amount.
        """
        picking_rows_by_id = dict(trace.get("picking_rows_by_id") or {})
        _intercompany_partner_ids: frozenset[int] = frozenset(
            int(v or 0) for v in list(trace.get("intercompany_partner_ids") or []) if int(v or 0) > 0
        )
        po_id_by_picking = dict(trace.get("po_id_by_picking") or {})
        stj_ids_by_picking = dict(trace.get("stj_ids_by_picking") or {})
        product_ids_by_picking = dict(trace.get("product_ids_by_picking") or {})
        bill_ids_by_product = dict(trace.get("bill_ids_by_product") or {})   # fallback only
        payment_rows_by_bill_id = dict(trace.get("payment_rows_by_bill_id") or {})
        bill_rows_by_id = dict(trace.get("bill_rows_by_id") or {})
        purchase_order_rows_by_id = dict(trace.get("purchase_order_rows_by_id") or {})
        purchase_line_rows_by_id = dict(trace.get("purchase_line_rows_by_id") or {})
        stock_move_rows_by_id = dict(trace.get("stock_move_rows_by_id") or {})
        stock_move_indexes = self._get_pcb_stock_move_indexes(trace)
        stock_move_ids_by_picking_id: dict[int, set[int]] = {
            int(key): {int(value or 0) for value in values if int(value or 0) > 0}
            for key, values in dict(stock_move_indexes.get("stock_move_ids_by_picking_id") or {}).items()
            if int(key or 0) > 0
        }
        purchase_line_ids_by_picking_id: dict[int, set[int]] = {
            int(key): {int(value or 0) for value in values if int(value or 0) > 0}
            for key, values in dict(stock_move_indexes.get("purchase_line_ids_by_picking_id") or {}).items()
            if int(key or 0) > 0
        }
        inventory_types_by_picking: dict[int, set[str]] = {
            int(key): {normalize_text(value).strip().lower() for value in values if normalize_text(value)}
            for key, values in dict(trace.get("inventory_types_by_picking") or {}).items()
            if int(key or 0) > 0
        }
        bill_line_rows_by_product: dict[int, list[dict[str, Any]]] = {
            int(k): list(v) for k, v in (trace.get("bill_line_rows_by_product") or {}).items()
        }
        purchase_line_product_map: dict[int, int] = {
            int(k): int(v) for k, v in (trace.get("purchase_line_product_map") or {}).items()
        }
        purchase_line_po_name_map: dict[int, str] = {
            int(k): str(v) for k, v in (trace.get("purchase_line_po_name_map") or {}).items()
        }
        payment_move_ids_by_product: dict[int, list[int]] = {
            int(k): [int(v) for v in list(values or [])]
            for k, values in (trace.get("payment_move_ids_by_product") or {}).items()
        }
        bank_move_ids_by_product: dict[int, list[int]] = {
            int(k): [int(v) for v in list(values or [])]
            for k, values in (trace.get("bank_move_ids_by_product") or {}).items()
        }
        matching_numbers_by_payment_move_id: dict[int, list[str]] = {
            int(k): list(v) for k, v in (trace.get("matching_numbers_by_payment_move_id") or {}).items()
        }
        bank_move_ids_by_matching: dict[str, list[int]] = {
            str(k): list(v) for k, v in (trace.get("bank_move_ids_by_matching") or {}).items()
        }
        direct_bank_move_ids_by_bill: dict[int, list[int]] = {
            int(k): list(v) for k, v in (trace.get("direct_bank_move_ids_by_bill") or {}).items()
        }
        direct_bills_by_bank_move_id: dict[int, list[int]] = {
            int(k): list(v) for k, v in (trace.get("direct_bills_by_bank_move_id") or {}).items()
        }
        bill_partner_key_by_move_id: dict[int, str] = {}
        for bill_id_r, bill_row_r in bill_rows_by_id.items():
            bill_id_int = int(bill_id_r or 0)
            if bill_id_int > 0:
                partner_key = self._resolve_matching_partner_key_from_move_row(bill_row_r)
                if not partner_key:
                    partner_key = _partner_match_key((move_info_map.get(bill_id_int) or {}).get("partner_id"))
                bill_partner_key_by_move_id[bill_id_int] = partner_key
        expansion_bills_by_bill: dict[int, list[int]] = {
            int(k): list(v) for k, v in (trace.get("expansion_bills_by_bill") or {}).items()
        }
        expansion_stjs_by_bill: dict[int, list[int]] = {
            int(k): list(v) for k, v in (trace.get("expansion_stjs_by_bill") or {}).items()
        }
        expansion_bills_by_stj: dict[int, list[int]] = {
            int(k): list(v) for k, v in (trace.get("expansion_bills_by_stj") or {}).items()
        }
        pcb_correction_move_ids = sorted(
            {int(value or 0) for value in list(trace.get("pcb_correction_move_ids") or []) if int(value or 0) > 0}
        )
        # SVL value per move per product — used for proportional per-item weighting
        stj_value_by_move_product: dict[int, dict[int, float]] = {
            int(k): {int(pk): float(pv) for pk, pv in (v or {}).items()}
            for k, v in (trace.get("stj_value_by_move_product") or {}).items()
        }
        # Return picking pairs: (return_picking_id, original_picking_id)
        return_picking_pairs: list[tuple[int, int]] = [
            (int(a), int(b)) for a, b in (trace.get("return_picking_pairs") or [])
        ]
        # Set of return picking IDs — used to skip product-based bill fallback for them.
        # Return pickings don't generate their own bills; any vendor refund is discovered
        # via BFS from the return STJ's matching_numbers, not via product lookup.
        _return_picking_ids: set[int] = {ret_pid for ret_pid, _ in return_picking_pairs}

        # ── Index ledger rows by move_id ──────────────────────────────────────
        lines_by_move: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for row in all_ledger_rows:
            move_id = _many2one_id(row.get("move_id"))
            if move_id > 0:
                lines_by_move[move_id].append(row)
        bill_line_ids_by_move_id: dict[int, set[int]] = {
            int(move_id): {
                int(line.get("id") or 0)
                for line in move_lines
                if int(line.get("id") or 0) > 0
            }
            for move_id, move_lines in lines_by_move.items()
            if int(move_id or 0) > 0
        }
        correction_move_ids_by_product: dict[int, set[int]] = defaultdict(set)
        correction_move_ids_by_picking: dict[int, set[int]] = defaultdict(set)
        correction_move_ids_by_bill: dict[int, set[int]] = defaultdict(set)
        for correction_move_id in pcb_correction_move_ids:
            clean_correction_move_id = int(correction_move_id or 0)
            if clean_correction_move_id <= 0:
                continue
            correction_move_row = move_info_map.get(clean_correction_move_id) or {}
            header_picking_id = self._row_picking_relation_id(correction_move_row)
            if header_picking_id > 0:
                correction_move_ids_by_picking[header_picking_id].add(clean_correction_move_id)
            header_bill_move_id = self._row_bill_move_relation_id(correction_move_row)
            if header_bill_move_id > 0:
                correction_move_ids_by_bill[header_bill_move_id].add(clean_correction_move_id)
            for line in lines_by_move.get(clean_correction_move_id, []):
                product_id = _many2one_id(line.get("product_id"))
                if product_id > 0:
                    correction_move_ids_by_product[product_id].add(clean_correction_move_id)
                picking_id = self._row_picking_relation_id(line)
                if picking_id > 0:
                    correction_move_ids_by_picking[picking_id].add(clean_correction_move_id)
                bill_move_id = self._row_bill_move_relation_id(line)
                if bill_move_id > 0:
                    correction_move_ids_by_bill[bill_move_id].add(clean_correction_move_id)
        correction_match_metadata_by_move_id = self._build_pcb_correction_match_metadata(
            candidate_move_ids=pcb_correction_move_ids,
            lines_by_move=lines_by_move,
            account_info_map=account_info_map,
            move_info_map=move_info_map,
            problem_codes=problem_codes,
        )
        self._log(
            f"[BENCH]   build/index_prep: {(perf_counter() - prep_started) * 1000.0:.0f}ms | "
            f"{len(lines_by_move)} moves | {len(pcb_correction_move_ids)} correction_candidates"
        )

        # ── Build PO-based bill grouping (picking → PO → bills) ───────────────
        # Reverse: po_name → po_id (for invoice_origin fallback)
        grouping_started = perf_counter()
        po_id_by_name: dict[str, int] = {}
        for po_id_r, po_row_r in purchase_order_rows_by_id.items():
            po_name_r = normalize_text(po_row_r.get("name"))
            if po_name_r:
                po_id_by_name[po_name_r] = int(po_id_r)

        bill_ids_by_po_id: dict[int, set[int]] = defaultdict(set)
        for _pid, bill_lines in bill_line_rows_by_product.items():
            for line in bill_lines:
                move_id = _many2one_id(line.get("move_id"))
                purchase_line_id = _many2one_id(line.get("purchase_line_id"))
                if move_id <= 0 or not self._is_vendor_bill_move(
                    move_id,
                    move_info_map=move_info_map,
                    bill_rows_by_id=bill_rows_by_id,
                ):
                    continue
                # Method 1: purchase_line_id → po_name → po_id
                po_name_from_line = normalize_text(purchase_line_po_name_map.get(purchase_line_id, ""))
                if po_name_from_line:
                    found_po_id = po_id_by_name.get(po_name_from_line, 0)
                    if found_po_id > 0:
                        bill_ids_by_po_id[found_po_id].add(move_id)
                        continue
                # Method 2: invoice_origin on bill header
                invoice_origin = normalize_text((bill_rows_by_id.get(move_id) or {}).get("invoice_origin"))
                if invoice_origin:
                    found_po_id = po_id_by_name.get(invoice_origin, 0)
                    if found_po_id > 0:
                        bill_ids_by_po_id[found_po_id].add(move_id)

        # ── Pre-compute bill totals from payment_term lines ───────────────────
        # Bill total = sum of credit on payment_term lines (= amount vendor owes us)
        bill_total_by_id: dict[int, float] = {}
        for move_id, lines in lines_by_move.items():
            if move_id not in bill_rows_by_id:
                continue
            total = _round2(sum(
                abs(_round2(line.get("credit")))
                for line in lines
                if normalize_text(line.get("display_type")) == "payment_term"
                and abs(_round2(line.get("credit"))) > 0
            ))
            if total < 0.01:
                total = _round2(sum(abs(_round2(line.get("credit"))) for line in lines))
            bill_total_by_id[move_id] = total

        # ── Pre-compute: which bills does each payment move cover? ─────────────
        bills_by_payment_move: dict[int, set[int]] = defaultdict(set)
        for bill_id_bpm, pmt_rows in payment_rows_by_bill_id.items():
            for pmt_row in pmt_rows:
                pmid = _many2one_id(pmt_row.get("move_id"))
                if pmid > 0:
                    bills_by_payment_move[pmid].add(int(bill_id_bpm))

        # ── Group pickings by shared bills (union-find, Path 4) ──────────────
        # When one vendor bill spans multiple pickings/POs, each picking would
        # independently pick up the full bill → bill triple-counted and all
        # cycles show Bermasalah.  Fix: merge pickings that share any bill into
        # ONE cycle so STJ credits + bill debits balance correctly.
        # Return pickings are also included so they merge into the same cycle as
        # their original picking (return_picking_pairs from fetch phase).
        _uf_parent: dict[int, int] = {pid: pid for pid in picking_rows_by_id}
        # Seed return pickings into union-find even if they have no bills yet
        for _ret_pid, _orig_pid in return_picking_pairs:
            if _ret_pid not in _uf_parent:
                _uf_parent[_ret_pid] = _ret_pid

        def _uf_find(pid: int) -> int:
            while _uf_parent[pid] != pid:
                _uf_parent[pid] = _uf_parent[_uf_parent[pid]]
                pid = _uf_parent[pid]
            return pid

        def _uf_union(a: int, b: int) -> None:
            ra, rb = _uf_find(a), _uf_find(b)
            if ra != rb:
                _uf_parent[ra] = rb

        _bill_to_first_picking: dict[int, int] = {}
        for _upid in sorted(picking_rows_by_id):
            _upo_id = po_id_by_picking.get(_upid, 0)
            for _ubill_id in bill_ids_by_po_id.get(_upo_id, set()):
                if _ubill_id in _bill_to_first_picking:
                    _uf_union(_upid, _bill_to_first_picking[_ubill_id])
                else:
                    _bill_to_first_picking[_ubill_id] = _upid

        # Pass 2: also merge pickings whose bills are settled by the same direct BK entry.
        # Example: BK252 has two AML lines that reconcile BILL/272 (picking A) and BILL/285
        # (picking B) via separate matching_numbers.  Pass 1 won't merge them because the
        # bills are different; Pass 2 catches this via direct_bills_by_bank_move_id.
        for _bk_bills in direct_bills_by_bank_move_id.values():
            _bk_bills_by_partner: dict[str, set[int]] = defaultdict(set)
            for _bid in _bk_bills:
                _partner_key = bill_partner_key_by_move_id.get(_bid, "")
                if not _partner_key:
                    continue
                if _bid not in _bill_to_first_picking or _bill_to_first_picking[_bid] not in _uf_parent:
                    continue
                _bk_bills_by_partner[_partner_key].add(_bid)
            for _partner_bills in _bk_bills_by_partner.values():
                _bk_picks = sorted({_bill_to_first_picking[bid] for bid in _partner_bills})
                for _i in range(1, len(_bk_picks)):
                    _uf_union(_bk_picks[0], _bk_picks[_i])

        # Pass 3: merge return pickings with their original picking.
        # stock.move.origin_returned_move_id / returned_move_ids linkage captured in fetch phase.
        for _ret_pid, _orig_pid in return_picking_pairs:
            if _ret_pid in _uf_parent and _orig_pid in _uf_parent:
                _uf_union(_ret_pid, _orig_pid)

        _groups_by_root: dict[int, list[int]] = defaultdict(list)
        for _gpid in sorted(_uf_parent):
            _groups_by_root[_uf_find(_gpid)].append(_gpid)
        picking_groups: list[list[int]] = [sorted(g) for g in _groups_by_root.values()]
        self._log(
            f"[BENCH]   build/group_merge: {(perf_counter() - grouping_started) * 1000.0:.0f}ms | "
            f"{len(picking_groups)} groups | {len(_uf_parent)} pickings"
        )

        # ── Build cycles ──────────────────────────────────────────────────────
        cycle_build_started = perf_counter()
        correction_match_elapsed_ms = 0.0
        correction_candidate_total = 0
        correction_candidate_max = 0
        correction_cycles_with_candidates = 0
        cycles: list[SvlDashboardPurchaseCycle] = []
        for group_picking_ids in picking_groups:
            picking_id = group_picking_ids[0]   # primary picking (first alphabetically)
            picking_row = picking_rows_by_id[picking_id]
            gr_date = normalize_text(picking_row.get("scheduled_date") or picking_row.get("date_done") or "")

            # Merge STJs and product IDs from all pickings in the group
            stj_move_ids = sorted(set().union(*(stj_ids_by_picking.get(pid, set()) for pid in group_picking_ids)))
            product_ids_in_picking = sorted(set().union(*(product_ids_by_picking.get(pid, set()) for pid in group_picking_ids)))

            # Collect all PO IDs in this group
            po_ids_in_group = sorted({po_id_by_picking.get(pid, 0) for pid in group_picking_ids} - {0})
            po_rows_in_group = {po_id: purchase_order_rows_by_id.get(po_id, {}) for po_id in po_ids_in_group}
            cycle_partner_key = ""
            for _po_id in po_ids_in_group:
                cycle_partner_key = _partner_match_key((po_rows_in_group.get(_po_id) or {}).get("partner_id"))
                if cycle_partner_key:
                    break
            if not cycle_partner_key:
                cycle_partner_key = _partner_match_key(picking_row.get("partner_id"))

            # Bills: union of all POs' bills + partner-guarded product fallback for no-PO pickings.
            # Return pickings are excluded from the product fallback: they don't generate
            # their own bills; any vendor refund is discovered via BFS from the return STJ.
            # Internal/no-PO pickings without a reliable partner key skip this fallback entirely.
            bill_move_ids: set[int] = set()
            for _po_id in po_ids_in_group:
                bill_move_ids.update(bill_ids_by_po_id.get(_po_id, set()))
            for _pid in group_picking_ids:
                if not po_id_by_picking.get(_pid, 0) and _pid not in _return_picking_ids and cycle_partner_key:
                    for _product_id in product_ids_by_picking.get(_pid, []):
                        for _bill_id in bill_ids_by_product.get(_product_id, []):
                            if self._matching_partner_keys_compatible(
                                cycle_partner_key,
                                bill_partner_key_by_move_id.get(int(_bill_id or 0), ""),
                            ):
                                bill_move_ids.add(int(_bill_id or 0))
            bill_move_ids_sorted = self._filter_vendor_bill_move_ids(
                bill_move_ids,
                move_info_map=move_info_map,
                bill_rows_by_id=bill_rows_by_id,
            )

            # Cycle naming: combined name when multiple pickings merged
            all_picking_names = [
                normalize_text(picking_rows_by_id[pid].get("name")) or f"Picking #{pid}"
                for pid in group_picking_ids
            ]
            if len(group_picking_ids) == 1:
                picking_name = all_picking_names[0]
            else:
                picking_name = all_picking_names[0] + f" [+{len(all_picking_names) - 1}]"

            # PO names and partner from merged group
            po_names = sorted({normalize_text(r.get("name") or "") for r in po_rows_in_group.values()} - {""})
            partner_id = 0
            partner_name = ""
            for _po_id in po_ids_in_group:
                _partner_id = _many2one_id(po_rows_in_group[_po_id].get("partner_id"))
                _pn = _many2one_name(po_rows_in_group[_po_id].get("partner_id"))
                if partner_id <= 0 and _partner_id > 0:
                    partner_id = _partner_id
                if _pn:
                    partner_name = _pn
                    break
            if partner_id <= 0:
                partner_id = _many2one_id(picking_row.get("partner_id"))
            if not partner_name:
                partner_name = _many2one_name(picking_row.get("partner_id"))

            # Payments: derived from the correct bills
            payment_move_ids: set[int] = set()
            for bill_id in bill_move_ids_sorted:
                for pmt_row in payment_rows_by_bill_id.get(bill_id, []):
                    pmid = _many2one_id(pmt_row.get("move_id"))
                    if pmid > 0:
                        payment_move_ids.add(pmid)

            # Bank JEs: derived from payment matching_numbers
            bank_move_ids: set[int] = set()
            bank_move_by_payment: dict[int, set[int]] = defaultdict(set)
            for pmid in payment_move_ids:
                for matching in matching_numbers_by_payment_move_id.get(pmid, []):
                    matched_ids = bank_move_ids_by_matching.get(matching, [])
                    bank_move_ids.update(matched_ids)
                    bank_move_by_payment[pmid].update(matched_ids)

            # Expand bill_move_ids with expansion-connected bills (discovered via matching_number)
            # e.g. BK196 reconciles both BILL A and BILL B via matching '127539'
            _expansion_extra_bills: set[int] = set()
            for _seed_bid in list(bill_move_ids_sorted):
                _expansion_extra_bills.update(expansion_bills_by_bill.get(_seed_bid, []))
            # Orphaned STJ seeds: bills discovered via STJ matching_number before PO chain knew about them
            for _seed_stj in stj_move_ids:
                _expansion_extra_bills.update(expansion_bills_by_stj.get(_seed_stj, []))
            if _expansion_extra_bills - bill_move_ids:
                bill_move_ids.update(_expansion_extra_bills)
                bill_move_ids_sorted = sorted(bill_move_ids)
            if partner_id <= 0 or not partner_name:
                for _bill_id in bill_move_ids_sorted:
                    _bill_partner_value = (
                        (move_info_map.get(_bill_id) or {}).get("partner_id")
                        or (bill_rows_by_id.get(_bill_id) or {}).get("partner_id")
                    )
                    if partner_id <= 0:
                        partner_id = _many2one_id(_bill_partner_value)
                    if not partner_name:
                        partner_name = _many2one_name(_bill_partner_value)
                    if partner_id > 0 and partner_name:
                        break

            # Direct BK via bill payable matching_number (no intermediate account.payment)
            direct_bank_move_ids_in_cycle: set[int] = set()
            for _dbill_id in bill_move_ids_sorted:
                for _dmid in direct_bank_move_ids_by_bill.get(_dbill_id, []):
                    direct_bank_move_ids_in_cycle.add(_dmid)
            bank_move_ids.update(direct_bank_move_ids_in_cycle)

            # Expansion STJs: STJs linked to expansion-connected bills via reconciliation
            _expansion_extra_stjs: set[int] = set()
            for _seed_bid in bill_move_ids_sorted:
                _expansion_extra_stjs.update(expansion_stjs_by_bill.get(_seed_bid, []))

            relevant_correction_move_ids: set[int] = set()
            for _product_id in product_ids_in_picking:
                relevant_correction_move_ids.update(correction_move_ids_by_product.get(int(_product_id or 0), set()))
            for _group_picking_id in group_picking_ids:
                relevant_correction_move_ids.update(correction_move_ids_by_picking.get(int(_group_picking_id or 0), set()))
            for _bill_move_id in bill_move_ids_sorted:
                relevant_correction_move_ids.update(correction_move_ids_by_bill.get(int(_bill_move_id or 0), set()))
            candidate_correction_move_ids = (
                sorted(relevant_correction_move_ids)
                if relevant_correction_move_ids
                else list(pcb_correction_move_ids)
            )
            correction_candidate_count = len(candidate_correction_move_ids)
            correction_candidate_total += correction_candidate_count
            correction_candidate_max = max(correction_candidate_max, correction_candidate_count)
            if correction_candidate_count > 0:
                correction_cycles_with_candidates += 1
            correction_match_started = perf_counter()
            cycle_correction_move_ids = self._match_pcb_correction_move_ids_to_cycle(
                candidate_move_ids=candidate_correction_move_ids,
                group_picking_ids=group_picking_ids,
                all_picking_names=all_picking_names,
                product_ids_in_picking=product_ids_in_picking,
                bill_move_ids=bill_move_ids_sorted,
                lines_by_move=lines_by_move,
                account_info_map=account_info_map,
                move_info_map=move_info_map,
                stock_move_rows_by_id=stock_move_rows_by_id,
                stock_move_ids_by_picking_id=stock_move_ids_by_picking_id,
                purchase_line_ids_by_picking_id=purchase_line_ids_by_picking_id,
                bill_line_ids_by_move_id=bill_line_ids_by_move_id,
                problem_codes=problem_codes,
                candidate_metadata_by_move_id=correction_match_metadata_by_move_id,
            )
            correction_match_elapsed_ms += (perf_counter() - correction_match_started) * 1000.0

            all_cycle_move_ids = sorted(
                set(stj_move_ids)
                | _expansion_extra_stjs
                | bill_move_ids
                | payment_move_ids
                | bank_move_ids
                | set(cycle_correction_move_ids)
            )
            if not all_cycle_move_ids:
                continue

            # ── Proportional allocation for shared batch payments ─────────────
            # When one payment covers bills from multiple pickings/POs, each cycle
            # gets a proportional share = (cycle bill total) / (payment bill total).
            payment_proportion: dict[int, float] = {}  # payment_move_id → proportion
            bank_proportion: dict[int, float] = {}     # bank_move_id → proportion
            for pmid in payment_move_ids:
                all_bills_for_pmt = bills_by_payment_move.get(pmid, set())
                cycle_bills_for_pmt = all_bills_for_pmt & bill_move_ids
                all_bill_total = _round2(sum(bill_total_by_id.get(bid, 0.0) for bid in all_bills_for_pmt))
                cycle_bill_total_pmt = _round2(sum(bill_total_by_id.get(bid, 0.0) for bid in cycle_bills_for_pmt))
                proportion = (cycle_bill_total_pmt / all_bill_total) if all_bill_total > 0.01 else 1.0
                proportion = min(1.0, proportion)
                payment_proportion[pmid] = proportion
                for bank_id in bank_move_by_payment.get(pmid, set()):
                    bank_proportion[bank_id] = proportion

            # Proportional allocation for direct BK moves (bypassed account.payment)
            for _dmid in direct_bank_move_ids_in_cycle:
                if _dmid in bank_proportion:
                    continue  # already set via payment path
                _all_bills_for_dmid = set(direct_bills_by_bank_move_id.get(_dmid, []))
                _cycle_bills_for_dmid = _all_bills_for_dmid & bill_move_ids
                _all_bill_total_d = _round2(sum(bill_total_by_id.get(bid, 0.0) for bid in _all_bills_for_dmid))
                _cycle_bill_total_d = _round2(sum(bill_total_by_id.get(bid, 0.0) for bid in _cycle_bills_for_dmid))
                _proportion_d = (_cycle_bill_total_d / _all_bill_total_d) if _all_bill_total_d > 0.01 else 1.0
                bank_proportion[_dmid] = min(1.0, _proportion_d)

            # Collect lines and apply proportional amounts for payment/BK moves
            account_buckets: dict[int, dict[str, float]] = defaultdict(lambda: {"debit": 0.0, "credit": 0.0})
            for move_id in all_cycle_move_ids:
                prop = payment_proportion.get(move_id, bank_proportion.get(move_id, None))
                for line in lines_by_move.get(move_id, []):
                    account_id = _many2one_id(line.get("account_id"))
                    if account_id <= 0:
                        continue
                    debit = _round2(line.get("debit"))
                    credit = _round2(line.get("credit"))
                    if prop is not None:
                        debit = _round2(debit * prop)
                        credit = _round2(credit * prop)
                    account_buckets[account_id]["debit"] = _round2(account_buckets[account_id]["debit"] + debit)
                    account_buckets[account_id]["credit"] = _round2(account_buckets[account_id]["credit"] + credit)

            # Build account rows and classify (Langkah 6 + 7)
            account_rows: list[SvlDashboardCycleAccountRow] = []
            problem_count = 0
            info_count = 0
            has_bills = bool(bill_move_ids_sorted)
            for account_id, bucket in sorted(account_buckets.items()):
                info = account_info_map.get(account_id, {})
                code = normalize_text(info.get("code")) or str(account_id)
                name = normalize_text(info.get("name")) or ""
                account_type = normalize_text(info.get("account_type")) or ""
                account_group = account_type.split("_")[0] if "_" in account_type else account_type
                debit = _round2(bucket["debit"])
                credit = _round2(bucket["credit"])
                net = _round2(debit - credit)
                if abs(net) < 1.0:
                    status = "balanced"
                elif code in problem_codes:
                    if has_bills:
                        status = "problem"
                        problem_count += 1
                    else:
                        # No bills in cycle — could be saldo awal or pending bill.
                        # Not a genuine accounting error; treat as info (warning).
                        status = "info"
                        info_count += 1
                elif code in info_codes:
                    status = "info"
                    info_count += 1
                else:
                    status = "acceptable"
                account_rows.append(SvlDashboardCycleAccountRow(
                    account_id=account_id,
                    code=code,
                    name=name,
                    account_type=account_type,
                    account_group=account_group,
                    debit=debit,
                    credit=credit,
                    net_balance=net,
                    status=status,
                ))

            # ── Per-item account breakdown ────────────────────────────────────
            # STJ/bill lines with product_id → direct assignment.
            # Shared lines (no product, or payment/BK) → distribute proportionally
            # by each item's inventory value (from SVL), falling back to equal split.
            n_items = max(len(product_ids_in_picking), 1)
            item_buckets: dict[int, dict[int, dict[str, float]]] = {
                pid: defaultdict(lambda: {"debit": 0.0, "credit": 0.0})
                for pid in product_ids_in_picking
            }
            product_name_map: dict[int, str] = {}

            # Compute per-item inventory weights from SVL values for this picking's STJ moves
            item_inv_value: dict[int, float] = {pid: 0.0 for pid in product_ids_in_picking}
            for mid in stj_move_ids:
                for pid, val in (stj_value_by_move_product.get(mid) or {}).items():
                    if pid in item_inv_value:
                        item_inv_value[pid] = _round2(item_inv_value[pid] + val)
            total_inv_value = _round2(sum(item_inv_value.values()))
            if total_inv_value > 0.01:
                item_weight: dict[int, float] = {
                    pid: item_inv_value[pid] / total_inv_value for pid in product_ids_in_picking
                }
            else:
                # Fallback: equal weight when no SVL data available
                item_weight = {pid: 1.0 / n_items for pid in product_ids_in_picking}

            # STJ + bill moves: direct by product; shared lines → proportional by inventory weight
            for move_id in sorted(set(stj_move_ids) | bill_move_ids | set(cycle_correction_move_ids)):
                for line in lines_by_move.get(move_id, []):
                    account_id_line = _many2one_id(line.get("account_id"))
                    if account_id_line <= 0:
                        continue
                    line_pid = _many2one_id(line.get("product_id"))
                    if line_pid > 0 and line_pid not in product_name_map:
                        product_name_map[line_pid] = _many2one_name(line.get("product_id")) or ""
                    d = _round2(line.get("debit"))
                    c = _round2(line.get("credit"))
                    if line_pid in item_buckets:
                        item_buckets[line_pid][account_id_line]["debit"] = _round2(item_buckets[line_pid][account_id_line]["debit"] + d)
                        item_buckets[line_pid][account_id_line]["credit"] = _round2(item_buckets[line_pid][account_id_line]["credit"] + c)
                    else:
                        # No product or product not in this picking → proportional by inventory value
                        for pid in product_ids_in_picking:
                            w = item_weight[pid]
                            item_buckets[pid][account_id_line]["debit"] = _round2(item_buckets[pid][account_id_line]["debit"] + _round2(d * w))
                            item_buckets[pid][account_id_line]["credit"] = _round2(item_buckets[pid][account_id_line]["credit"] + _round2(c * w))

            # Payment + BK moves: proportioned at cycle level, then by inventory weight across items
            for move_id in sorted(payment_move_ids | bank_move_ids):
                prop = payment_proportion.get(move_id, bank_proportion.get(move_id, 1.0))
                for line in lines_by_move.get(move_id, []):
                    account_id_line = _many2one_id(line.get("account_id"))
                    if account_id_line <= 0:
                        continue
                    d_base = _round2(_round2(line.get("debit")) * prop)
                    c_base = _round2(_round2(line.get("credit")) * prop)
                    for pid in product_ids_in_picking:
                        w = item_weight[pid]
                        item_buckets[pid][account_id_line]["debit"] = _round2(item_buckets[pid][account_id_line]["debit"] + _round2(d_base * w))
                        item_buckets[pid][account_id_line]["credit"] = _round2(item_buckets[pid][account_id_line]["credit"] + _round2(c_base * w))

            # Build SvlDashboardCycleItemRow list
            item_rows: list[SvlDashboardCycleItemRow] = []
            for pid in product_ids_in_picking:
                p_name = product_name_map.get(pid, f"Product #{pid}")
                item_acct_rows: list[SvlDashboardCycleAccountRow] = []
                for acct_id, bucket in sorted(item_buckets.get(pid, {}).items()):
                    info = account_info_map.get(acct_id, {})
                    code_i = normalize_text(info.get("code")) or str(acct_id)
                    name_i = normalize_text(info.get("name")) or ""
                    atype_i = normalize_text(info.get("account_type")) or ""
                    agroup_i = atype_i.split("_")[0] if "_" in atype_i else atype_i
                    d_i = _round2(bucket["debit"])
                    c_i = _round2(bucket["credit"])
                    net_i = _round2(d_i - c_i)
                    if d_i == 0.0 and c_i == 0.0:
                        continue
                    if abs(net_i) < 1.0:
                        st_i = "balanced"
                    elif code_i in problem_codes:
                        st_i = "problem" if has_bills else "info"
                    elif code_i in info_codes:
                        st_i = "info"
                    else:
                        st_i = "acceptable"
                    item_acct_rows.append(SvlDashboardCycleAccountRow(
                        account_id=acct_id, code=code_i, name=name_i,
                        account_type=atype_i, account_group=agroup_i,
                        debit=d_i, credit=c_i, net_balance=net_i, status=st_i,
                    ))
                item_rows.append(SvlDashboardCycleItemRow(
                    product_id=pid,
                    product_name=p_name,
                    default_code=normalize_text((product_info_map.get(pid) or {}).get("default_code")),
                    valuation_method="automated" if item_inv_value.get(pid, 0.0) > 0.01 else "manual",
                    standard_price=_round2((product_info_map.get(pid) or {}).get("standard_price")),
                    svl_zero_at_gr=abs(_round2(item_inv_value.get(pid, 0.0))) < 0.01,
                    account_rows=item_acct_rows,
                ))

            # Detect issue patterns (Langkah 7)
            # warning_patterns affect cycle_status; info_patterns displayed only
            warning_patterns, info_patterns = self._detect_cycle_patterns(
                account_rows=account_rows,
                bill_move_ids=bill_move_ids_sorted,
                bill_rows_by_id=bill_rows_by_id,
                payment_move_ids=sorted(payment_move_ids),
                bank_move_ids=sorted(bank_move_ids),
            )
            issue_patterns = warning_patterns + info_patterns

            # Classify cycle status (Langkah 8) — only warning_patterns trigger 'partial'
            has_info_accounts = info_count > 0
            partial_group_key = ""
            partial_group_label = ""

            # Clearing-via-Reclass check (Case 5 / Case 6 post-repair):
            # Jika satu-satunya akun problem adalah 1108099 (Clearing) DAN semua
            # item yang masih menyumbang saldo problem adalah item zero-SVL
            # clearing-only, ini bukan problem nyata — balance sudah diurus via
            # jurnal reclass (RAC). Sibling item direct STJ yang sudah clean
            # tidak boleh memblokir downgrade cycle ke partial.
            clearing_only_problem = False
            if problem_count > 0:
                clearing_only_problem = self._is_pcb_clearing_reclass_candidate(
                    account_rows=account_rows,
                    item_rows=item_rows,
                    has_bills=has_bills,
                    problem_codes=problem_codes,
                )
                if clearing_only_problem:
                    for r in account_rows:
                        if normalize_text(r.code).upper() == "1108099" and abs(_round2(r.net_balance)) >= 0.01:
                            r.status = "acceptable"
                    problem_count = 0

            if problem_count > 0:
                cycle_status = "problem"
            elif clearing_only_problem:
                cycle_status = "partial"
                partial_group_key = "clearing_reclass"
                partial_group_label = "Clearing via Jurnal Reclass"
            elif warning_patterns:
                cycle_status = "partial"
                partial_group_key, partial_group_label = self._classify_partial_cycle_group(
                    bill_move_ids=bill_move_ids_sorted,
                    bill_rows_by_id=bill_rows_by_id,
                    payment_move_ids=sorted(payment_move_ids),
                    bank_move_ids=sorted(bank_move_ids),
                    has_info_accounts=has_info_accounts,
                )
            else:
                cycle_status = "healthy"

            # Build ref lists from move_info_map
            stj_ref_move_ids = sorted(set(stj_move_ids) | _expansion_extra_stjs | set(cycle_correction_move_ids))
            stj_refs = sorted(
                {
                    normalize_text((move_info_map.get(mid) or {}).get("name"))
                    for mid in stj_ref_move_ids
                    if normalize_text((move_info_map.get(mid) or {}).get("name"))
                }
            )
            bill_refs = sorted(
                {
                    normalize_text((move_info_map.get(mid) or {}).get("name"))
                    for mid in bill_move_ids_sorted
                    if normalize_text((move_info_map.get(mid) or {}).get("name"))
                }
            )
            payment_refs = sorted({normalize_text((move_info_map.get(mid) or {}).get("name")) for mid in sorted(payment_move_ids) if normalize_text((move_info_map.get(mid) or {}).get("name"))})
            bank_refs = sorted({normalize_text((move_info_map.get(mid) or {}).get("name")) for mid in sorted(bank_move_ids) if normalize_text((move_info_map.get(mid) or {}).get("name"))})

            total_debit = _round2(sum(r.debit for r in account_rows))
            total_credit = _round2(sum(r.credit for r in account_rows))

            # ── Raw AML lines (data mentah sebelum agregasi) ─────────────────
            _stj_set = set(stj_move_ids) | _expansion_extra_stjs | set(cycle_correction_move_ids)
            _bill_set = set(bill_move_ids_sorted)
            _pmt_set = set(payment_move_ids)
            _bank_set = set(bank_move_ids)

            def _raw_jenis(mid: int) -> str:
                if mid in _stj_set:
                    return "STJ"
                if mid in _bill_set:
                    return "BILL"
                if mid in _pmt_set:
                    return "PBK"
                if mid in _bank_set:
                    return "BK"
                return "?"

            raw_lines: list[dict[str, Any]] = []
            for _mid in all_cycle_move_ids:
                _minfo = move_info_map.get(_mid, {})
                _kode = normalize_text(_minfo.get("name")) or f"Move#{_mid}"
                _jns = _raw_jenis(_mid)
                # PBK/BK lines: apply payment_proportion so displayed amount matches
                # the cycle's allocated share (same logic used in account_rows aggregation)
                if _mid in _pmt_set:
                    _prop = payment_proportion.get(_mid, 1.0)
                elif _mid in _bank_set:
                    _prop = bank_proportion.get(_mid, 1.0)
                else:
                    _prop = 1.0
                for _aml in lines_by_move.get(_mid, []):
                    _acct_id = _many2one_id(_aml.get("account_id"))
                    _acct = account_info_map.get(_acct_id, {})
                    _pid = _many2one_id(_aml.get("product_id"))
                    _pinfo = product_info_map.get(_pid, {}) if _pid > 0 else {}
                    _partner = (
                        _many2one_name(_aml.get("partner_id"))
                        or _many2one_name(_minfo.get("partner_id"))
                    )
                    raw_lines.append({
                        "kode_transaksi": _kode,
                        "tanggal": normalize_text(_aml.get("date")) or "",
                        "jenis": _jns,
                        "tipe_akun": normalize_text(_acct.get("account_type")) or "",
                        "akun_code": normalize_text(_acct.get("code")) or str(_acct_id),
                        "akun_name": normalize_text(_acct.get("name")) or "",
                        "kode_item": _pinfo.get("default_code", ""),
                        "nama_item": _pinfo.get("name") or _many2one_name(_aml.get("product_id")),
                        "uom": _many2one_name(_aml.get("product_uom_id")),
                        "qty_item": float(_aml.get("quantity") or 0),
                        "kategori_produk": _pinfo.get("categ_name", ""),
                        "no_po": purchase_line_po_name_map.get(_many2one_id(_aml.get("purchase_line_id")), ""),
                        "komunikasi": normalize_text(_aml.get("name")) or "",
                        "debit": _round2(float(_aml.get("debit") or 0) * _prop),
                        "kredit": _round2(float(_aml.get("credit") or 0) * _prop),
                        "saldo": _round2(float(_aml.get("balance") or 0) * _prop),
                        "matching": normalize_text(_aml.get("matching_number")) or "",
                        "partner": _partner,
                    })

            case1_link_rows = self._build_pcb_case1_link_rows(
                cycle_status=cycle_status,
                picking_id=picking_id,
                picking_name=picking_name,
                gr_date=gr_date,
                partner_name=partner_name,
                group_picking_ids=group_picking_ids,
                cycle_stj_move_ids=sorted(set(stj_move_ids) | _expansion_extra_stjs),
                product_ids_in_picking=product_ids_in_picking,
                account_rows=account_rows,
                item_rows=item_rows,
                bill_move_ids=bill_move_ids_sorted,
                payment_move_ids=sorted(payment_move_ids),
                bank_move_ids=sorted(bank_move_ids),
                all_cycle_move_ids=all_cycle_move_ids,
                lines_by_move=lines_by_move,
                account_info_map=account_info_map,
                move_info_map=move_info_map,
                product_info_map=product_info_map,
                bill_rows_by_id=bill_rows_by_id,
                bill_line_rows_by_product=bill_line_rows_by_product,
                purchase_line_rows_by_id=purchase_line_rows_by_id,
                purchase_line_product_map=purchase_line_product_map,
                purchase_line_po_name_map=purchase_line_po_name_map,
                stock_move_rows_by_id=stock_move_rows_by_id,
                payment_move_ids_by_product=payment_move_ids_by_product,
                bank_move_ids_by_product=bank_move_ids_by_product,
            )
            correction_stj_refs = sorted(
                {
                    normalize_text((move_info_map.get(mid) or {}).get("name"))
                    for mid in cycle_correction_move_ids
                    if normalize_text((move_info_map.get(mid) or {}).get("name"))
                }
            )
            document_classification, document_classification_label, document_classification_reasons = (
                self._classify_pcb_document_flow(
                    group_picking_ids=group_picking_ids,
                    product_ids_in_picking=product_ids_in_picking,
                    po_ids_in_group=po_ids_in_group,
                    po_names=po_names,
                    partner_id=partner_id,
                    partner_name=partner_name,
                    bill_move_ids=bill_move_ids_sorted,
                    payment_move_ids=sorted(payment_move_ids),
                    bank_move_ids=sorted(bank_move_ids),
                    purchase_line_ids_by_picking_id=purchase_line_ids_by_picking_id,
                    bill_line_rows_by_product=bill_line_rows_by_product,
                    bill_rows_by_id=bill_rows_by_id,
                    picking_rows_by_id=picking_rows_by_id,
                    inventory_types_by_picking=inventory_types_by_picking,
                )
            )
            cycle_status, partial_group_key, partial_group_label, problem_count, info_count, has_info_accounts = (
                self._gate_pcb_cycle_status_by_document_classification(
                    document_classification=document_classification,
                    cycle_status=cycle_status,
                    partial_group_key=partial_group_key,
                    partial_group_label=partial_group_label,
                    account_rows=account_rows,
                    item_rows=item_rows,
                )
            )
            if document_classification != _PCB_DOCUMENT_CLASS_PURCHASE_BACKED:
                gated_reason = "Cycle non-purchase tidak masuk bucket problem/partial"
                if gated_reason not in document_classification_reasons:
                    document_classification_reasons = [*document_classification_reasons, gated_reason][:4]
            cycles.append(SvlDashboardPurchaseCycle(
                picking_id=picking_id,
                picking_name=picking_name,
                gr_date=gr_date,
                partner_name=partner_name,
                partner_id=partner_id,
                purchase_orders=po_names,
                correction_stj_move_ids=sorted({int(move_id or 0) for move_id in cycle_correction_move_ids if int(move_id or 0) > 0}),
                correction_stj_refs=correction_stj_refs,
                stj_refs=stj_refs,
                bill_move_ids=bill_move_ids_sorted,
                bill_refs=bill_refs,
                payment_move_ids=sorted(payment_move_ids),
                payment_refs=payment_refs,
                bank_move_ids=sorted(bank_move_ids),
                bank_refs=bank_refs,
                product_ids=product_ids_in_picking,
                inventory_types=sorted(
                    {
                        normalize_text(value).strip().lower()
                        for group_picking_id in group_picking_ids
                        for value in list(inventory_types_by_picking.get(int(group_picking_id or 0), set()) or [])
                        if normalize_text(value)
                    }
                ),
                cycle_status=cycle_status,
                document_classification=document_classification,
                document_classification_label=document_classification_label,
                document_classification_reasons=document_classification_reasons,
                has_return_picking=bool(set(group_picking_ids) & _return_picking_ids),
                has_refund_bill=any(
                    normalize_text((bill_rows_by_id.get(mid) or {}).get("move_type")).lower() == "in_refund"
                    for mid in bill_move_ids_sorted
                ),
                partner_is_intercompany=partner_id in _intercompany_partner_ids,
                partial_group_key=partial_group_key,
                partial_group_label=partial_group_label,
                issue_patterns=issue_patterns,
                account_rows=account_rows,
                item_rows=item_rows,
                total_debit=total_debit,
                total_credit=total_credit,
                problem_account_count=problem_count,
                has_info_accounts=has_info_accounts,
                info_account_count=info_count,
                picking_ids=group_picking_ids,
                picking_names=all_picking_names,
                case1_link_rows=case1_link_rows,
                case2_repair_rows=[],
                raw_lines=raw_lines,
            ))

        # Sort: problem first, then partial, then healthy; within status by abs problem balance desc
        _status_order = {"problem": 0, "partial": 1, "healthy": 2}
        cycles.sort(key=lambda c: (
            _status_order.get(c.cycle_status, 3),
            -sum(abs(r.net_balance) for r in c.account_rows if r.status == "problem"),
            c.picking_name,
        ))
        self._log(
            f"[BENCH]   build/correction_match: {correction_match_elapsed_ms:.0f}ms | "
            f"{correction_cycles_with_candidates} cycles | {correction_candidate_total} candidates | max {correction_candidate_max}"
        )
        self._log(
            f"[BENCH]   build/cycle_assembly: {(perf_counter() - cycle_build_started) * 1000.0:.0f}ms | "
            f"{len(cycles)} cycles"
        )
        if build_repair_rows:
            self._rebuild_pcb_case2_repair_rows_for_cycles(
                cycles=cycles,
                adjustment_moves=[],
                trace=trace,
                all_ledger_rows=all_ledger_rows,
                account_info_map=account_info_map,
                move_info_map=move_info_map,
                product_info_map=product_info_map,
            )
        return cycles

    @staticmethod
    def _normalize_pcb_analytic_distribution(value: Any) -> Any:
        if not isinstance(value, dict):
            return False
        normalized: dict[str, float] = {}
        for key, amount in value.items():
            clean_key = normalize_text(key)
            if not clean_key:
                continue
            clean_amount = _round2(amount)
            if abs(clean_amount) < 0.01:
                continue
            normalized[clean_key] = clean_amount
        return normalized or False

    @staticmethod
    def _pcb_case1_direction(
        account_rows: list[SvlDashboardCycleAccountRow],
    ) -> tuple[bool, str, str, float]:
        by_code = {row.code: row for row in account_rows}
        a2103006 = by_code.get("2103006")
        a1108099 = by_code.get("1108099")
        if (
            a2103006 is None
            or a1108099 is None
            or a2103006.status != "problem"
            or a1108099.status != "problem"
            or abs(abs(a2103006.net_balance) - abs(a1108099.net_balance)) >= 1.0
        ):
            return False, "", "", 0.0
        if abs(a2103006.net_balance) > 0.01:
            if a2103006.net_balance > 0:
                return True, "1108099", "2103006", abs(a2103006.net_balance)
            return True, "2103006", "1108099", abs(a2103006.net_balance)
        if abs(a1108099.net_balance) > 0.01:
            if a1108099.net_balance > 0:
                return True, "2103006", "1108099", abs(a1108099.net_balance)
            return True, "1108099", "2103006", abs(a1108099.net_balance)
        return False, "", "", 0.0

    @staticmethod
    def _allocate_pcb_case1_amounts(
        candidates: list[dict[str, Any]],
        *,
        total_amount: float,
    ) -> list[float]:
        if not candidates:
            return []
        clean_total_amount = abs(_round2(total_amount))
        if clean_total_amount <= 0.0:
            return [0.0 for _candidate in candidates]
        weights = [_round2(abs(float(candidate.get("source_balance_weight") or 0.0))) for candidate in candidates]
        if not any(weight > 0.01 for weight in weights):
            weights = [_round2(abs(float(candidate.get("quantity") or 0.0))) for candidate in candidates]
        if not any(weight > 0.01 for weight in weights):
            weights = [_round2(abs(float(candidate.get("price_gap_value") or 0.0))) for candidate in candidates]
        if not any(weight > 0.01 for weight in weights):
            weights = [1.0 for _candidate in candidates]
        total_weight = float(sum(weights) or 0.0)
        if total_weight <= 0.0:
            weights = [1.0 for _candidate in candidates]
            total_weight = float(len(weights))
        allocations = [
            _round2((clean_total_amount * float(weight or 0.0)) / total_weight)
            for weight in weights
        ]
        remainder = _round2(clean_total_amount - sum(allocations))
        if abs(remainder) >= 0.01:
            target_index = max(
                range(len(candidates)),
                key=lambda index: (
                    float(weights[index] or 0.0),
                    int(candidates[index].get("source_id") or 0),
                    int(candidates[index].get("product_id") or 0),
                ),
            )
            allocations[target_index] = _round2(allocations[target_index] + remainder)
        return allocations

    @staticmethod
    def _stock_move_sort_key(
        stock_move_row: dict[str, Any],
        *,
        primary_picking_id: int,
    ) -> tuple[int, str, int]:
        picking_id = _many2one_id(stock_move_row.get("picking_id"))
        state = normalize_text(stock_move_row.get("state")).lower()
        return (
            0 if picking_id == primary_picking_id else 1,
            "0" if state == "done" else "1",
            int(stock_move_row.get("id") or 0),
        )

    @staticmethod
    def _pcb_case1_move_ref_label(
        move_id: int,
        *,
        move_info_map: dict[int, dict[str, Any]],
    ) -> str:
        clean_move_id = int(move_id or 0)
        if clean_move_id <= 0:
            return ""
        clean_name = normalize_text((move_info_map.get(clean_move_id) or {}).get("name"))
        return clean_name or f"Move #{clean_move_id}"

    @staticmethod
    def _is_pcb_correction_ref(value: Any) -> bool:
        return "purchasecyclebalance" in _normalized_match_text(normalize_text(value))

    async def _find_pcb_correction_move_ids(
        self,
        *,
        request: SvlDashboardRequest,
        date_from: str,
        date_to: str,
        context: dict[str, Any],
        trace: dict[str, Any],
        problem_codes: frozenset[str],
    ) -> list[int]:
        product_ids = sorted({int(value or 0) for value in list(trace.get("product_ids") or []) if int(value or 0) > 0})
        if not product_ids:
            return []
        picking_rows_by_id = dict(trace.get("picking_rows_by_id") or {})
        if not picking_rows_by_id:
            return []
        bill_rows_by_id = dict(trace.get("bill_rows_by_id") or {})
        picking_tokens = {
            _normalized_match_text(normalize_text(row.get("name")))
            for row in picking_rows_by_id.values()
            if _normalized_match_text(normalize_text(row.get("name")))
        }
        bill_tokens = {
            _normalized_match_text(normalize_text(row.get("name")))
            for row in bill_rows_by_id.values()
            if _normalized_match_text(normalize_text(row.get("name")))
        }
        move_domain: list[Any] = [
            ("company_id", "=", request.company_id),
            ("state", "=", "posted"),
            ("ref", "ilike", "Purchase Cycle Balance"),
        ]
        if date_from:
            move_domain.append(("date", ">=", date_from))
        if date_to:
            move_domain.append(("date", "<=", date_to))
        move_meta = await self._fields_get_cached("account.move")
        correction_move_fields = list(
            dict.fromkeys(
                [
                    "id",
                    *[
                        field_name
                        for field_name in (
                            "name",
                            "ref",
                            "date",
                            "state",
                            "journal_id",
                            "company_id",
                            "partner_id",
                            "stock_picking_id",
                            "picking_id",
                            "bill_move_id",
                            "invoice_id",
                            "bill_id",
                        )
                        if field_name in move_meta or field_name == "id"
                    ],
                ]
            )
        )
        move_rows = await self.rpc.search_read(
            "account.move",
            move_domain,
            fields=correction_move_fields,
            order="date,id",
            context=context,
            stage="SVL_DASH_PCB_CORR_MOVE",
        )
        candidate_moves = [
            row
            for row in move_rows
            if self._is_pcb_correction_ref(row.get("ref"))
        ]
        if not candidate_moves:
            return []
        candidate_move_ids = [int(row.get("id") or 0) for row in candidate_moves if int(row.get("id") or 0) > 0]
        if not candidate_move_ids:
            return []

        aml_meta = await self._fields_get_cached("account.move.line")
        correction_aml_fields = [
            "id",
            "move_id",
            "account_id",
            "product_id",
            "purchase_line_id",
            "stock_move_id",
            "stock_picking_id",
            "picking_id",
            "bill_line_id",
            "bill_move_id",
            "invoice_id",
            "bill_id",
            "company_id",
            "display_type",
        ]
        correction_aml_fields = list(dict.fromkeys(
            [
                field_name
                for field_name in correction_aml_fields
                if field_name in aml_meta or field_name in {"id", "move_id", "account_id", "product_id"}
            ]
        ))
        aml_domain: list[Any] = [
            ("company_id", "=", request.company_id),
            ("move_id", "in", candidate_move_ids),
        ]
        aml_domain.extend(self._build_posted_domain(aml_meta))
        if "display_type" in aml_meta:
            aml_domain.append(("display_type", "not in", ["line_section", "line_note"]))
        aml_rows = await self.rpc.search_read(
            "account.move.line",
            aml_domain,
            fields=correction_aml_fields,
            order="move_id,id",
            context=context,
            stage="SVL_DASH_PCB_CORR_AML",
        )
        if not aml_rows:
            return []
        account_ids = sorted({_many2one_id(row.get("account_id")) for row in aml_rows if _many2one_id(row.get("account_id")) > 0})
        account_info_map = await self._fetch_account_info_map(account_ids=account_ids, context=context)
        lines_by_move: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for row in aml_rows:
            move_id = _many2one_id(row.get("move_id"))
            if move_id > 0:
                lines_by_move[move_id].append(row)

        matched_move_ids: list[int] = []
        for move_row in candidate_moves:
            move_id = int(move_row.get("id") or 0)
            if move_id <= 0:
                continue
            move_lines = lines_by_move.get(move_id, [])
            if not move_lines:
                continue
            has_problem_product_line = False
            has_direct_relation_hint = False
            for line in move_lines:
                account_code = normalize_text((account_info_map.get(_many2one_id(line.get("account_id"))) or {}).get("code"))
                product_id = _many2one_id(line.get("product_id"))
                if account_code in problem_codes and product_id in product_ids:
                    has_problem_product_line = True
                if (
                    _many2one_id(line.get("stock_move_id")) > 0
                    or self._row_picking_relation_id(line) > 0
                    or int(line.get("bill_line_id") or 0) > 0
                    or self._row_bill_move_relation_id(line) > 0
                ):
                    has_direct_relation_hint = True
            if not has_direct_relation_hint and (
                self._row_picking_relation_id(move_row) > 0 or self._row_bill_move_relation_id(move_row) > 0
            ):
                has_direct_relation_hint = True
            if not has_problem_product_line:
                continue
            ref_text = _normalized_match_text(move_row.get("ref"))
            picking_match = any(token and token in ref_text for token in picking_tokens)
            bill_match = any(token and token in ref_text for token in bill_tokens)
            if has_direct_relation_hint or (picking_match and bill_match):
                matched_move_ids.append(move_id)
        return sorted(set(matched_move_ids))

    def _build_pcb_correction_match_metadata(
        self,
        *,
        candidate_move_ids: list[int],
        lines_by_move: dict[int, list[dict[str, Any]]],
        account_info_map: dict[int, dict[str, Any]],
        move_info_map: dict[int, dict[str, Any]],
        problem_codes: frozenset[str],
    ) -> dict[int, dict[str, Any]]:
        if not candidate_move_ids:
            return {}
        clean_problem_codes = {
            normalize_text(code)
            for code in problem_codes
            if normalize_text(code)
        }
        result: dict[int, dict[str, Any]] = {}
        for move_id in [int(value or 0) for value in candidate_move_ids if int(value or 0) > 0]:
            problem_lines: list[dict[str, int]] = []
            for line in list(lines_by_move.get(move_id, []) or []):
                account_code = normalize_text(
                    (account_info_map.get(_many2one_id(line.get("account_id"))) or {}).get("code")
                )
                if account_code not in clean_problem_codes:
                    continue
                problem_lines.append(
                    {
                        "product_id": _many2one_id(line.get("product_id")),
                        "picking_id": self._row_picking_relation_id(line),
                        "bill_move_id": self._row_bill_move_relation_id(line),
                        "bill_line_id": int(line.get("bill_line_id") or 0),
                        "stock_move_id": _many2one_id(line.get("stock_move_id")),
                        "purchase_line_id": _many2one_id(line.get("purchase_line_id")),
                    }
                )
            move_row = move_info_map.get(move_id) or {}
            result[move_id] = {
                "problem_lines": problem_lines,
                "header_picking_id": self._row_picking_relation_id(move_row),
                "header_bill_move_id": self._row_bill_move_relation_id(move_row),
                "ref_text": _normalized_match_text(move_row.get("ref")),
            }
        return result

    def _match_pcb_correction_move_ids_to_cycle(
        self,
        *,
        candidate_move_ids: list[int],
        group_picking_ids: list[int],
        all_picking_names: list[str],
        product_ids_in_picking: list[int],
        bill_move_ids: list[int],
        lines_by_move: dict[int, list[dict[str, Any]]],
        account_info_map: dict[int, dict[str, Any]],
        move_info_map: dict[int, dict[str, Any]],
        stock_move_rows_by_id: dict[int, dict[str, Any]],
        stock_move_ids_by_picking_id: dict[int, set[int]] | None = None,
        purchase_line_ids_by_picking_id: dict[int, set[int]] | None = None,
        bill_line_ids_by_move_id: dict[int, set[int]] | None = None,
        problem_codes: frozenset[str],
        candidate_metadata_by_move_id: dict[int, dict[str, Any]] | None = None,
    ) -> list[int]:
        if not candidate_move_ids:
            return []
        clean_problem_codes = {normalize_text(code) for code in problem_codes if normalize_text(code)}
        cycle_picking_ids = {int(value or 0) for value in group_picking_ids if int(value or 0) > 0}
        cycle_product_ids = {int(value or 0) for value in product_ids_in_picking if int(value or 0) > 0}
        cycle_bill_move_ids = {int(value or 0) for value in bill_move_ids if int(value or 0) > 0}
        cycle_bill_line_ids: set[int] = set()
        if bill_line_ids_by_move_id:
            for move_id in cycle_bill_move_ids:
                cycle_bill_line_ids.update(bill_line_ids_by_move_id.get(move_id, set()))
        else:
            cycle_bill_line_ids = {
                int(line.get("id") or 0)
                for move_id in cycle_bill_move_ids
                for line in lines_by_move.get(move_id, [])
                if int(line.get("id") or 0) > 0
            }
        cycle_stock_move_ids: set[int] = set()
        cycle_purchase_line_ids: set[int] = set()
        if stock_move_ids_by_picking_id and purchase_line_ids_by_picking_id:
            for picking_id in cycle_picking_ids:
                cycle_stock_move_ids.update(stock_move_ids_by_picking_id.get(picking_id, set()))
                cycle_purchase_line_ids.update(purchase_line_ids_by_picking_id.get(picking_id, set()))
        else:
            cycle_stock_indexes = self._build_pcb_stock_move_indexes(stock_move_rows_by_id)
            for picking_id in cycle_picking_ids:
                cycle_stock_move_ids.update(
                    cycle_stock_indexes.get("stock_move_ids_by_picking_id", {}).get(picking_id, set())
                )
                cycle_purchase_line_ids.update(
                    cycle_stock_indexes.get("purchase_line_ids_by_picking_id", {}).get(picking_id, set())
                )
        picking_tokens = {
            _normalized_match_text(name)
            for name in all_picking_names
            if _normalized_match_text(name)
        }
        bill_tokens = {
            _normalized_match_text((move_info_map.get(move_id) or {}).get("name"))
            for move_id in cycle_bill_move_ids
            if _normalized_match_text((move_info_map.get(move_id) or {}).get("name"))
        }

        matched_move_ids: list[int] = []
        for move_id in candidate_move_ids:
            has_problem_product_line = False
            has_direct_relation_match = False
            metadata = dict((candidate_metadata_by_move_id or {}).get(int(move_id or 0)) or {})
            if metadata:
                for problem_line in list(metadata.get("problem_lines") or []):
                    if int(problem_line.get("product_id") or 0) not in cycle_product_ids:
                        continue
                    has_problem_product_line = True
                    if int(problem_line.get("picking_id") or 0) in cycle_picking_ids:
                        has_direct_relation_match = True
                        break
                    if int(problem_line.get("bill_move_id") or 0) in cycle_bill_move_ids:
                        has_direct_relation_match = True
                        break
                    if int(problem_line.get("bill_line_id") or 0) in cycle_bill_line_ids:
                        has_direct_relation_match = True
                        break
                    if int(problem_line.get("stock_move_id") or 0) in cycle_stock_move_ids:
                        has_direct_relation_match = True
                        break
                    if int(problem_line.get("purchase_line_id") or 0) in cycle_purchase_line_ids:
                        has_direct_relation_match = True
                        break
                if not has_direct_relation_match:
                    if int(metadata.get("header_picking_id") or 0) in cycle_picking_ids:
                        has_direct_relation_match = True
                    elif int(metadata.get("header_bill_move_id") or 0) in cycle_bill_move_ids:
                        has_direct_relation_match = True
                ref_text = normalize_text(metadata.get("ref_text"))
            else:
                move_lines = lines_by_move.get(int(move_id or 0), [])
                if not move_lines:
                    continue
                for line in move_lines:
                    account_code = normalize_text((account_info_map.get(_many2one_id(line.get("account_id"))) or {}).get("code"))
                    if account_code not in clean_problem_codes:
                        continue
                    if _many2one_id(line.get("product_id")) not in cycle_product_ids:
                        continue
                    has_problem_product_line = True
                    if self._row_picking_relation_id(line) in cycle_picking_ids:
                        has_direct_relation_match = True
                        break
                    if self._row_bill_move_relation_id(line) in cycle_bill_move_ids:
                        has_direct_relation_match = True
                        break
                    if int(line.get("bill_line_id") or 0) in cycle_bill_line_ids:
                        has_direct_relation_match = True
                        break
                    if _many2one_id(line.get("stock_move_id")) in cycle_stock_move_ids:
                        has_direct_relation_match = True
                        break
                    if _many2one_id(line.get("purchase_line_id")) in cycle_purchase_line_ids:
                        has_direct_relation_match = True
                        break
                move_row = move_info_map.get(int(move_id or 0)) or {}
                if not has_direct_relation_match:
                    if self._row_picking_relation_id(move_row) in cycle_picking_ids:
                        has_direct_relation_match = True
                    elif self._row_bill_move_relation_id(move_row) in cycle_bill_move_ids:
                        has_direct_relation_match = True
                ref_text = _normalized_match_text(move_row.get("ref"))
            if has_direct_relation_match:
                matched_move_ids.append(int(move_id or 0))
                continue
            if not has_problem_product_line:
                continue
            picking_match = any(token and token in ref_text for token in picking_tokens)
            bill_match = any(token and token in ref_text for token in bill_tokens)
            if picking_match and bill_match:
                matched_move_ids.append(int(move_id or 0))
        return sorted({move_id for move_id in matched_move_ids if move_id > 0})

    def _resolve_pcb_case1_stj_links(
        self,
        *,
        direct_stj_move_ids: list[int],
        cycle_stj_move_ids: list[int],
        group_picking_ids: list[int],
        lines_by_move: dict[int, list[dict[str, Any]]],
        account_info_map: dict[int, dict[str, Any]],
        move_info_map: dict[int, dict[str, Any]],
        stock_move_rows_by_id: dict[int, dict[str, Any]],
        product_id: int,
        purchase_line_id: int,
        stock_move_id: int,
    ) -> tuple[list[int], list[str], str, int]:
        direct_ids = sorted({int(value or 0) for value in direct_stj_move_ids if int(value or 0) > 0})
        clean_cycle_ids = sorted({int(value or 0) for value in cycle_stj_move_ids if int(value or 0) > 0})
        candidate_move_ids = sorted(set(direct_ids) | set(clean_cycle_ids))
        if not candidate_move_ids:
            return [], [], "", 0

        clean_picking_ids = {int(value or 0) for value in group_picking_ids if int(value or 0) > 0}
        best_score = 0
        best_basis = ""
        best_move_ids: set[int] = set()
        best_clearing_score = 0
        best_clearing_basis = ""
        best_clearing_move_ids: set[int] = set()

        for move_id in candidate_move_ids:
            move_best_score = 100 if clean_picking_ids else 0
            move_best_basis = "cycle_match.picking" if clean_picking_ids else ""
            move_best_clearing_score = 0
            move_best_clearing_basis = ""
            for line in lines_by_move.get(move_id, []):
                score = 0
                basis = ""
                line_stock_move_id = _many2one_id(line.get("stock_move_id"))
                line_purchase_line_id = _many2one_id(line.get("purchase_line_id"))
                line_product_id = _many2one_id(line.get("product_id"))
                linked_stock_move_row = stock_move_rows_by_id.get(line_stock_move_id) or {}
                linked_purchase_line_id = _many2one_id(linked_stock_move_row.get("purchase_line_id"))
                linked_product_id = _many2one_id(linked_stock_move_row.get("product_id"))
                linked_picking_id = _many2one_id(linked_stock_move_row.get("picking_id"))

                if stock_move_id > 0 and line_stock_move_id == stock_move_id:
                    score = 400
                    basis = "cycle_match.stock_move"
                elif purchase_line_id > 0 and purchase_line_id in {line_purchase_line_id, linked_purchase_line_id}:
                    score = 300
                    basis = "cycle_match.purchase_line"
                elif product_id > 0 and product_id in {line_product_id, linked_product_id}:
                    score = 200
                    basis = "cycle_match.product"
                elif clean_picking_ids and linked_picking_id in clean_picking_ids:
                    score = 100
                    basis = "cycle_match.picking"

                if score > move_best_score:
                    move_best_score = score
                    move_best_basis = basis
                account_id = _many2one_id(line.get("account_id"))
                account_code = normalize_text((account_info_map.get(account_id) or {}).get("code"))
                if account_code == "1108099" and score > move_best_clearing_score:
                    move_best_clearing_score = score
                    move_best_clearing_basis = basis

            if move_best_score > best_score:
                best_score = move_best_score
                best_basis = move_best_basis
                best_move_ids = {move_id}
            elif move_best_score == best_score and move_best_score > 0:
                best_move_ids.add(move_id)
            if move_best_clearing_score > best_clearing_score:
                best_clearing_score = move_best_clearing_score
                best_clearing_basis = move_best_clearing_basis
                best_clearing_move_ids = {move_id}
            elif move_best_clearing_score == best_clearing_score and move_best_clearing_score > 0:
                best_clearing_move_ids.add(move_id)

        if best_clearing_move_ids:
            resolved_ids = sorted((set(direct_ids) & best_clearing_move_ids) or best_clearing_move_ids)
            refs = [
                self._pcb_case1_move_ref_label(move_id, move_info_map=move_info_map)
                for move_id in resolved_ids
            ]
            if direct_ids and set(resolved_ids).issubset(set(direct_ids)):
                return resolved_ids, [ref for ref in refs if ref], "stock_move.account_move_ids", len(resolved_ids)
            return resolved_ids, [ref for ref in refs if ref], best_clearing_basis, len(resolved_ids)

        if direct_ids:
            refs = [
                self._pcb_case1_move_ref_label(move_id, move_info_map=move_info_map)
                for move_id in direct_ids
            ]
            return direct_ids, [ref for ref in refs if ref], "stock_move.account_move_ids", len(direct_ids)

        if not best_move_ids:
            best_move_ids = set(candidate_move_ids)
            best_basis = "cycle_match.picking"

        resolved_ids = sorted(best_move_ids)
        refs = [
            self._pcb_case1_move_ref_label(move_id, move_info_map=move_info_map)
            for move_id in resolved_ids
        ]
        return resolved_ids, [ref for ref in refs if ref], best_basis, len(resolved_ids)

    def _match_pcb_case1_target_line_ids(
        self,
        *,
        all_cycle_move_ids: list[int],
        stj_move_ids: list[int],
        lines_by_move: dict[int, list[dict[str, Any]]],
        account_info_map: dict[int, dict[str, Any]],
        product_id: int,
        purchase_line_id: int,
        bill_move_id: int,
        stock_move_id: int,
        partner_id: int = 0,
    ) -> tuple[list[int], list[int]]:
        allowed_stj_move_ids = {int(value or 0) for value in stj_move_ids if int(value or 0) > 0}
        best_score_by_code: dict[str, int] = {"2103006": 0, "1108099": 0}
        target_ids_by_code: dict[str, set[int]] = {"2103006": set(), "1108099": set()}
        for move_id in all_cycle_move_ids:
            for row in lines_by_move.get(move_id, []):
                line_id = int(row.get("id") or 0)
                if line_id <= 0:
                    continue
                account_id = _many2one_id(row.get("account_id"))
                account_code = normalize_text((account_info_map.get(account_id) or {}).get("code"))
                if account_code not in {"2103006", "1108099"}:
                    continue
                line_move_id = _many2one_id(row.get("move_id"))
                if account_code == "2103006":
                    if bill_move_id <= 0 or line_move_id != bill_move_id:
                        continue
                elif account_code == "1108099":
                    if not allowed_stj_move_ids or line_move_id not in allowed_stj_move_ids:
                        continue
                score = 0
                line_product_id = _many2one_id(row.get("product_id"))
                line_purchase_line_id = _many2one_id(row.get("purchase_line_id"))
                line_stock_move_id = _many2one_id(row.get("stock_move_id"))
                line_partner_id = _many2one_id(row.get("partner_id"))
                if product_id > 0 and line_product_id == product_id:
                    score += 40
                if purchase_line_id > 0 and line_purchase_line_id == purchase_line_id:
                    score += 40
                if stock_move_id > 0 and line_stock_move_id == stock_move_id:
                    score += 30
                if partner_id > 0 and line_partner_id == partner_id:
                    score += 20
                if bill_move_id > 0 and line_move_id == bill_move_id:
                    score += 10
                if score <= 0:
                    score = 5
                if score > best_score_by_code[account_code]:
                    best_score_by_code[account_code] = score
                    target_ids_by_code[account_code] = {line_id}
                elif score == best_score_by_code[account_code]:
                    target_ids_by_code[account_code].add(line_id)
        return (
            sorted(target_ids_by_code["2103006"]),
            sorted(target_ids_by_code["1108099"]),
        )

    def _build_pcb_case1_link_rows(
        self,
        *,
        cycle_status: str,
        picking_id: int,
        picking_name: str,
        gr_date: str,
        partner_name: str,
        group_picking_ids: list[int],
        cycle_stj_move_ids: list[int],
        product_ids_in_picking: list[int],
        account_rows: list[SvlDashboardCycleAccountRow],
        item_rows: list[SvlDashboardCycleItemRow] | None = None,
        bill_move_ids: list[int],
        payment_move_ids: list[int],
        bank_move_ids: list[int],
        all_cycle_move_ids: list[int],
        lines_by_move: dict[int, list[dict[str, Any]]],
        account_info_map: dict[int, dict[str, Any]],
        move_info_map: dict[int, dict[str, Any]],
        product_info_map: dict[int, dict[str, Any]],
        bill_rows_by_id: dict[int, dict[str, Any]],
        bill_line_rows_by_product: dict[int, list[dict[str, Any]]],
        purchase_line_rows_by_id: dict[int, dict[str, Any]],
        purchase_line_product_map: dict[int, int],
        purchase_line_po_name_map: dict[int, str],
        stock_move_rows_by_id: dict[int, dict[str, Any]],
        payment_move_ids_by_product: dict[int, list[int]],
        bank_move_ids_by_product: dict[int, list[int]],
    ) -> list[SvlDashboardPcbCase1LinkRow]:
        if normalize_text(cycle_status) != "problem":
            return []

        picking_id_set = {int(value or 0) for value in group_picking_ids if int(value or 0) > 0}
        product_id_set = {int(value or 0) for value in product_ids_in_picking if int(value or 0) > 0}
        item_case1_map: dict[int, dict[str, Any]] = {}
        cycle_case1_fallback_amount = 0.0
        for item_row in list(item_rows or []):
            product_id = int(getattr(item_row, "product_id", 0) or 0)
            if product_id <= 0:
                continue
            item_primary_case = normalize_text(getattr(item_row, "primary_case", "")).lower()
            if item_primary_case and item_primary_case != "case1":
                continue
            balance_suspend = _round2(self._item_row_account_balance(item_row, "2103006"))
            balance_clearing = _round2(self._item_row_account_balance(item_row, "1108099"))
            if abs(balance_suspend) < 0.01 or abs(balance_clearing) < 0.01:
                continue
            debit_code = "2103006" if balance_suspend < 0 else "1108099"
            credit_code = "1108099" if debit_code == "2103006" else "2103006"
            debit_amount = abs(balance_suspend) if debit_code == "2103006" else abs(balance_clearing)
            credit_amount = abs(balance_clearing) if credit_code == "1108099" else abs(balance_suspend)
            diff_amount = abs(_round2(debit_amount - credit_amount))
            item_case1_map[product_id] = {
                "debit_code": debit_code,
                "credit_code": credit_code,
                "debit_amount": _round2(debit_amount),
                "credit_amount": _round2(credit_amount),
                "base_amount": _round2(max(debit_amount, credit_amount)),
                "diff_amount": _round2(diff_amount),
            }
        if not item_case1_map and product_id_set:
            cycle_case1_ok, cycle_debit_code, cycle_credit_code, cycle_amount = self._pcb_case1_direction(account_rows)
            if cycle_case1_ok and cycle_amount > 0.01:
                cycle_case1_fallback_amount = _round2(cycle_amount)
                for product_id in sorted(product_id_set):
                    item_case1_map[product_id] = {
                        "debit_code": cycle_debit_code,
                        "credit_code": cycle_credit_code,
                        "debit_amount": _round2(cycle_case1_fallback_amount),
                        "credit_amount": _round2(cycle_case1_fallback_amount),
                        "base_amount": _round2(cycle_case1_fallback_amount),
                        "diff_amount": 0.0,
                    }
        if not item_case1_map:
            return []
        cycle_payment_ids = {int(value or 0) for value in payment_move_ids if int(value or 0) > 0}
        cycle_bank_ids = {int(value or 0) for value in bank_move_ids if int(value or 0) > 0}
        stock_move_rows_sorted = sorted(
            (
                row
                for row in stock_move_rows_by_id.values()
                if _many2one_id(row.get("product_id")) in product_id_set
                and _many2one_id(row.get("picking_id")) in picking_id_set
            ),
            key=lambda row: self._stock_move_sort_key(row, primary_picking_id=picking_id),
        )
        stock_move_ids_by_purchase_line: dict[int, list[int]] = defaultdict(list)
        stock_move_ids_by_product: dict[int, list[int]] = defaultdict(list)
        for stock_move_row in stock_move_rows_sorted:
            stock_move_id = int(stock_move_row.get("id") or 0)
            if stock_move_id <= 0:
                continue
            product_id = _many2one_id(stock_move_row.get("product_id"))
            if product_id > 0:
                stock_move_ids_by_product[product_id].append(stock_move_id)
            purchase_line_id = _many2one_id(stock_move_row.get("purchase_line_id"))
            if purchase_line_id > 0:
                stock_move_ids_by_purchase_line[purchase_line_id].append(stock_move_id)

        candidate_rows: list[dict[str, Any]] = []
        existing_row_keys: set[str] = set()

        def register_candidate(
            *,
            product_id: int,
            source_kind: str,
            source_id: int,
            bill_line_row: dict[str, Any] | None,
            purchase_line_id: int,
            stock_move_ids: list[int],
            bill_move_id: int,
            bill_name: str,
            bill_date: str,
            po_name: str,
        ) -> None:
            clean_product_id = int(product_id or 0)
            clean_source_id = int(source_id or 0)
            if clean_product_id <= 0 or clean_source_id <= 0:
                return
            item_case1 = item_case1_map.get(clean_product_id) or {}
            item_debit_code = normalize_text(item_case1.get("debit_code"))
            item_credit_code = normalize_text(item_case1.get("credit_code"))
            row_key = f"case1::{int(picking_id or 0)}::{clean_product_id}::{normalize_text(source_kind)}::{clean_source_id}"
            if row_key in existing_row_keys:
                return
            existing_row_keys.add(row_key)
            primary_stock_move_id = int(stock_move_ids[0] or 0) if stock_move_ids else 0
            primary_stock_move_row = stock_move_rows_by_id.get(primary_stock_move_id) or {}
            direct_stj_move_ids = sorted(
                {
                    move_id
                    for stock_move_id in stock_move_ids
                    for move_id in _many2many_ids((stock_move_rows_by_id.get(int(stock_move_id or 0)) or {}).get("account_move_ids"))
                    if int(move_id or 0) > 0
                }
            )
            stj_move_ids, stj_refs, stj_link_basis, stj_candidate_count = self._resolve_pcb_case1_stj_links(
                direct_stj_move_ids=direct_stj_move_ids,
                cycle_stj_move_ids=cycle_stj_move_ids,
                group_picking_ids=group_picking_ids,
                lines_by_move=lines_by_move,
                account_info_map=account_info_map,
                move_info_map=move_info_map,
                stock_move_rows_by_id=stock_move_rows_by_id,
                product_id=clean_product_id,
                purchase_line_id=int(purchase_line_id or 0),
                stock_move_id=primary_stock_move_id,
            )
            product_info = product_info_map.get(clean_product_id) or {}
            partner_id = _many2one_id((move_info_map.get(int(bill_move_id or 0)) or {}).get("partner_id"))
            bill_price_unit = _round2((bill_line_row or {}).get("price_unit"))
            quantity = _round2(
                (bill_line_row or {}).get("quantity")
                or primary_stock_move_row.get("product_qty")
                or primary_stock_move_row.get("quantity")
            )
            stj_amount = _round2(abs(quantity * _round2(primary_stock_move_row.get("price_unit"))))
            amount_currency_basis = 0.0
            if bill_line_row is not None:
                amount_currency_basis = abs(_round2((bill_line_row or {}).get("balance")))
                if amount_currency_basis <= 0.01:
                    amount_currency_basis = abs(_round2((bill_line_row or {}).get("price_subtotal")))
            gr_price_unit = _round2(primary_stock_move_row.get("price_unit"))
            bill_amount = _round2(amount_currency_basis)
            price_gap_value = 0.0
            if bill_line_row is not None and quantity > 0.0 and (abs(bill_price_unit) > 0.0 or abs(gr_price_unit) > 0.0):
                price_gap_value = _round2(abs((bill_price_unit - gr_price_unit) * quantity))
            diff_account_code = normalize_text((product_info.get("expense_account_code"))).upper()
            diff_account_name = normalize_text(product_info.get("expense_account_name"))
            suspend_target_aml_ids, clearing_target_aml_ids = self._match_pcb_case1_target_line_ids(
                all_cycle_move_ids=all_cycle_move_ids,
                stj_move_ids=stj_move_ids,
                lines_by_move=lines_by_move,
                account_info_map=account_info_map,
                product_id=clean_product_id,
                purchase_line_id=int(purchase_line_id or 0),
                bill_move_id=int(bill_move_id or 0),
                stock_move_id=primary_stock_move_id,
                partner_id=partner_id,
            )
            candidate_rows.append(
                {
                    "cycle_key": f"case1::{int(picking_id or 0)}",
                    "source_key": row_key,
                    "source_kind": normalize_text(source_kind),
                    "source_id": clean_source_id,
                    "product_id": clean_product_id,
                    "item_code": normalize_text(product_info.get("default_code")),
                    "item_name": normalize_text(product_info.get("name")) or f"Product #{clean_product_id}",
                    "item_category_name": normalize_text(product_info.get("categ_name")),
                    "bill_line_id": int((bill_line_row or {}).get("id") or 0),
                    "bill_move_id": int(bill_move_id or 0),
                    "purchase_line_id": int(purchase_line_id or 0),
                    "stock_move_id": primary_stock_move_id,
                    "stock_move_ids": [int(value) for value in stock_move_ids if int(value or 0) > 0],
                    "stj_move_ids": stj_move_ids,
                    "stj_refs": stj_refs,
                    "stj_link_basis": stj_link_basis,
                    "stj_candidate_count": int(stj_candidate_count or 0),
                    "picking_id": int(picking_id or 0),
                    "picking_name": normalize_text(picking_name),
                    "po_name": normalize_text(po_name),
                    "bill_name": normalize_text(bill_name),
                    "partner_id": partner_id,
                    "partner_name": normalize_text(partner_name),
                    "payment_move_ids": sorted(cycle_payment_ids & set(payment_move_ids_by_product.get(clean_product_id, []))),
                    "bank_move_ids": sorted(cycle_bank_ids & set(bank_move_ids_by_product.get(clean_product_id, []))),
                    "suspend_target_aml_ids": suspend_target_aml_ids,
                    "clearing_target_aml_ids": clearing_target_aml_ids,
                    "product_uom_id": _many2one_id((bill_line_row or {}).get("product_uom_id")),
                    "quantity": quantity,
                    "currency_id": _many2one_id((bill_line_row or {}).get("currency_id")),
                    "amount_currency": _round2((bill_line_row or {}).get("amount_currency")),
                    "amount_currency_basis": amount_currency_basis,
                    "analytic_distribution": self._normalize_pcb_analytic_distribution((bill_line_row or {}).get("analytic_distribution")),
                    "journal_code": DEFAULT_JOURNAL_CODE,
                    "debit_account_code": item_debit_code,
                    "credit_account_code": item_credit_code,
                    "debit_amount": _round2(item_case1.get("debit_amount", 0.0) or 0.0),
                    "credit_amount": _round2(item_case1.get("credit_amount", 0.0) or 0.0),
                    "diff_account_code": diff_account_code,
                    "diff_account_name": diff_account_name,
                    "diff_side": "",
                    "diff_amount": _round2(item_case1.get("diff_amount", 0.0) or 0.0),
                    "stj_amount": stj_amount,
                    "bill_amount": bill_amount,
                    "bill_price_unit": bill_price_unit,
                    "gr_price_unit": gr_price_unit,
                    "price_gap_value": price_gap_value,
                    "source_balance_weight": (
                        amount_currency_basis
                        or abs(price_gap_value)
                        or abs(quantity)
                        or float(item_case1.get("base_amount", 0.0) or 0.0)
                    ),
                    "allocated_amount": 0.0,
                    "bill_date": normalize_text(bill_date)[:10],
                    "gr_date": normalize_text(gr_date)[:10],
                }
            )

        for bill_move_id in bill_move_ids:
            bill_row = bill_rows_by_id.get(int(bill_move_id or 0)) or {}
            bill_name = normalize_text((move_info_map.get(int(bill_move_id or 0)) or {}).get("name")) or normalize_text(bill_row.get("name"))
            bill_date = normalize_text(bill_row.get("invoice_date")) or normalize_text(bill_row.get("date"))
            for line in lines_by_move.get(int(bill_move_id or 0), []):
                line_id = int(line.get("id") or 0)
                if line_id <= 0:
                    continue
                account_id = _many2one_id(line.get("account_id"))
                account_code = normalize_text((account_info_map.get(account_id) or {}).get("code"))
                if account_code != "2103006":
                    continue
                product_id = _many2one_id(line.get("product_id"))
                purchase_line_id = _many2one_id(line.get("purchase_line_id"))
                if product_id <= 0 and purchase_line_id > 0:
                    product_id = int(purchase_line_product_map.get(purchase_line_id) or 0)
                if product_id <= 0 or product_id not in product_id_set:
                    continue
                stock_move_ids = list(stock_move_ids_by_purchase_line.get(purchase_line_id) or stock_move_ids_by_product.get(product_id) or [])
                po_name = normalize_text(purchase_line_po_name_map.get(purchase_line_id, "")) or normalize_text(bill_row.get("invoice_origin"))
                register_candidate(
                    product_id=product_id,
                    source_kind="bill_line",
                    source_id=line_id,
                    bill_line_row=line,
                    purchase_line_id=purchase_line_id,
                    stock_move_ids=stock_move_ids,
                    bill_move_id=int(bill_move_id or 0),
                    bill_name=bill_name,
                    bill_date=bill_date,
                    po_name=po_name,
                )

        covered_products_with_bill = {
            int(candidate.get("product_id") or 0)
            for candidate in candidate_rows
            if normalize_text(candidate.get("source_kind")) == "bill_line"
        }
        for product_id in sorted(product_id_set - covered_products_with_bill):
            product_purchase_line_ids = sorted(
                {
                    _many2one_id(row.get("purchase_line_id"))
                    for row in stock_move_rows_sorted
                    if _many2one_id(row.get("product_id")) == product_id and _many2one_id(row.get("purchase_line_id")) > 0
                }
            )
            if product_purchase_line_ids:
                for purchase_line_id in product_purchase_line_ids:
                    po_name = normalize_text(purchase_line_po_name_map.get(purchase_line_id, ""))
                    bill_move_id = 0
                    bill_name = ""
                    bill_date = ""
                    for candidate_bill_line in bill_line_rows_by_product.get(product_id, []):
                        if _many2one_id(candidate_bill_line.get("purchase_line_id")) != purchase_line_id:
                            continue
                        bill_move_id = _many2one_id(candidate_bill_line.get("move_id"))
                        bill_name = normalize_text((move_info_map.get(bill_move_id) or {}).get("name"))
                        bill_date = normalize_text((bill_rows_by_id.get(bill_move_id) or {}).get("invoice_date")) or normalize_text((bill_rows_by_id.get(bill_move_id) or {}).get("date"))
                        break
                    register_candidate(
                        product_id=product_id,
                        source_kind="purchase_line",
                        source_id=purchase_line_id,
                        bill_line_row=None,
                        purchase_line_id=purchase_line_id,
                        stock_move_ids=list(stock_move_ids_by_purchase_line.get(purchase_line_id, [])),
                        bill_move_id=bill_move_id,
                        bill_name=bill_name,
                        bill_date=bill_date,
                        po_name=po_name,
                    )
                continue
            for stock_move_id in stock_move_ids_by_product.get(product_id, []):
                stock_move_row = stock_move_rows_by_id.get(stock_move_id) or {}
                register_candidate(
                    product_id=product_id,
                    source_kind="stock_move",
                    source_id=stock_move_id,
                    bill_line_row=None,
                    purchase_line_id=_many2one_id(stock_move_row.get("purchase_line_id")),
                    stock_move_ids=[stock_move_id],
                    bill_move_id=0,
                    bill_name="",
                    bill_date="",
                    po_name=normalize_text(
                        purchase_line_po_name_map.get(_many2one_id(stock_move_row.get("purchase_line_id")), "")
                    ),
                )

        if not candidate_rows:
            return []
        results: list[SvlDashboardPcbCase1LinkRow] = []
        product_order: list[int] = []
        candidate_rows_by_product: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for candidate in candidate_rows:
            product_id = int(candidate.get("product_id") or 0)
            if product_id <= 0:
                continue
            if product_id not in candidate_rows_by_product:
                product_order.append(product_id)
            candidate_rows_by_product[product_id].append(candidate)
        if cycle_case1_fallback_amount > 0.01:
            allocations = self._allocate_pcb_case1_amounts(candidate_rows, total_amount=cycle_case1_fallback_amount)
            for candidate, allocation in zip(candidate_rows, allocations, strict=False):
                if allocation <= 0.0:
                    continue
                share = (allocation / cycle_case1_fallback_amount) if cycle_case1_fallback_amount > 0.01 else 1.0
                candidate["allocated_amount"] = _round2(allocation)
                candidate["debit_amount"] = _round2(float(candidate.get("debit_amount", 0.0) or 0.0) * share)
                candidate["credit_amount"] = _round2(float(candidate.get("credit_amount", 0.0) or 0.0) * share)
                candidate["diff_amount"] = _round2(float(candidate.get("diff_amount", 0.0) or 0.0) * share)
                signed_total = _round2(float(candidate.get("debit_amount", 0.0) or 0.0) - float(candidate.get("credit_amount", 0.0) or 0.0))
                if abs(signed_total) >= 0.01 and normalize_text(candidate.get("diff_account_code")):
                    candidate["diff_side"] = "credit" if signed_total > 0 else "debit"
                candidate.pop("source_balance_weight", None)
                results.append(SvlDashboardPcbCase1LinkRow(**candidate))
            return results
        for product_id in product_order:
            item_case1 = item_case1_map.get(product_id)
            if item_case1 is None:
                continue
            item_amount = _round2(item_case1.get("base_amount", 0.0) or 0.0)
            if item_amount <= 0.01:
                continue
            product_candidates = candidate_rows_by_product.get(product_id, [])
            allocations = self._allocate_pcb_case1_amounts(product_candidates, total_amount=item_amount)
            for candidate, allocation in zip(product_candidates, allocations, strict=False):
                if allocation <= 0.0:
                    continue
                share = (allocation / item_amount) if item_amount > 0.01 else 1.0
                candidate["allocated_amount"] = _round2(allocation)
                candidate["debit_amount"] = _round2(float(candidate.get("debit_amount", 0.0) or 0.0) * share)
                candidate["credit_amount"] = _round2(float(candidate.get("credit_amount", 0.0) or 0.0) * share)
                candidate["diff_amount"] = _round2(float(candidate.get("diff_amount", 0.0) or 0.0) * share)
                signed_total = _round2(float(candidate.get("debit_amount", 0.0) or 0.0) - float(candidate.get("credit_amount", 0.0) or 0.0))
                if abs(signed_total) >= 0.01 and normalize_text(candidate.get("diff_account_code")):
                    candidate["diff_side"] = "credit" if signed_total > 0 else "debit"
                candidate.pop("source_balance_weight", None)
                results.append(SvlDashboardPcbCase1LinkRow(**candidate))
        return results

    @staticmethod
    def _pcb_repair_side_from_balance(balance: float) -> str:
        rounded_balance = _round2(balance)
        if rounded_balance < -0.01:
            return "debit"
        if rounded_balance > 0.01:
            return "credit"
        return ""

    @staticmethod
    def _pcb_planned_line_signed_amount(line: SvlDashboardPcbRepairPlannedLine) -> float:
        amount = abs(_round2(line.amount))
        if amount <= 0.0:
            return 0.0
        return amount if normalize_text(line.side).lower() == "debit" else -amount

    @classmethod
    def _build_pcb_case2_planned_lines(
        cls,
        *,
        problem_balances_by_code: dict[str, float],
        expense_account_code: str,
        expense_account_name: str,
        hpp_balances_by_code: dict[str, float],
        hpp_account_name_by_code: dict[str, str],
        variance_balances_by_code: dict[str, float] | None = None,
        variance_account_name_by_code: dict[str, str] | None = None,
        line_label: str = "",
    ) -> list[SvlDashboardPcbRepairPlannedLine]:
        planned_lines: list[SvlDashboardPcbRepairPlannedLine] = []

        def add_reversal_line(
            *,
            role: str,
            account_code: str,
            account_name: str,
            balance: float,
            current_line_label: str,
        ) -> None:
            side = cls._pcb_repair_side_from_balance(balance)
            amount = abs(_round2(balance))
            if not side or amount <= 0.0:
                return
            planned_lines.append(
                SvlDashboardPcbRepairPlannedLine(
                    role=role,
                    account_code=normalize_text(account_code).upper(),
                    account_name=normalize_text(account_name),
                    amount=amount,
                    side=side,
                    line_label=normalize_text(current_line_label),
                    source_balance=_round2(balance),
                )
            )

        base_line_label = normalize_text(line_label)
        zero_hpp_label = f"{base_line_label} - Zero HPP" if base_line_label else "Zero HPP"
        zero_variance_label = f"{base_line_label} - Zero Selisih HPP" if base_line_label else "Zero Selisih HPP"
        selisih_hpp_label = build_pcb_selisih_hpp_label(base_line_label)

        for account_code in _PCB_CASE2_PROBLEM_CODES:
            balance = _round2(problem_balances_by_code.get(account_code, 0.0))
            if abs(balance) < 0.01:
                continue
            add_reversal_line(
                role=f"problem_{account_code}",
                account_code=account_code,
                account_name="",
                balance=balance,
                current_line_label=base_line_label,
            )
        normalized_target_expense_code = normalize_text(expense_account_code).upper()
        normalized_hpp_account_name_by_code = {
            normalize_text(code).upper(): normalize_text(name)
            for code, name in dict(hpp_account_name_by_code or {}).items()
            if normalize_text(code)
        }
        normalized_hpp_balances_by_code = {
            normalize_text(code).upper(): _round2(balance)
            for code, balance in dict(hpp_balances_by_code or {}).items()
            if normalize_text(code) and abs(_round2(balance)) >= 0.01
        }
        normalized_variance_account_name_by_code = {
            normalize_text(code).upper(): normalize_text(name)
            for code, name in dict(variance_account_name_by_code or {}).items()
            if normalize_text(code)
        }
        normalized_variance_balances_by_code = {
            normalize_text(code).upper(): _round2(balance)
            for code, balance in dict(variance_balances_by_code or {}).items()
            if normalize_text(code) and abs(_round2(balance)) >= 0.01
        }
        has_problem_balance = any(
            abs(_round2(problem_balances_by_code.get(account_code, 0.0))) >= 0.01
            for account_code in _PCB_CASE2_PROBLEM_CODES
        )
        use_variance_first = has_problem_balance and not normalized_hpp_balances_by_code and bool(normalized_variance_balances_by_code)
        if use_variance_first:
            for account_code in sorted(normalized_variance_balances_by_code):
                add_reversal_line(
                    role=f"variance_zero_{account_code}",
                    account_code=account_code,
                    account_name=normalized_variance_account_name_by_code.get(account_code, ""),
                    balance=normalized_variance_balances_by_code.get(account_code, 0.0),
                    current_line_label=zero_variance_label,
                )
        else:
            for account_code in sorted(normalized_hpp_balances_by_code):
                if account_code == normalized_target_expense_code:
                    continue
                add_reversal_line(
                    role=f"hpp_zero_{account_code}",
                    account_code=account_code,
                    account_name=normalized_hpp_account_name_by_code.get(account_code, ""),
                    balance=normalized_hpp_balances_by_code.get(account_code, 0.0),
                    current_line_label=zero_hpp_label,
                )
            add_reversal_line(
                role="hpp_zero",
                account_code=normalized_target_expense_code,
                account_name=normalize_text(expense_account_name)
                or normalized_hpp_account_name_by_code.get(normalized_target_expense_code, ""),
                balance=normalized_hpp_balances_by_code.get(normalized_target_expense_code, 0.0),
                current_line_label=zero_hpp_label,
            )
        signed_total = _round2(sum(cls._pcb_planned_line_signed_amount(line) for line in planned_lines))
        if abs(signed_total) >= 0.01:
            planned_lines.append(
                SvlDashboardPcbRepairPlannedLine(
                    role="selisih_hpp",
                    account_code=normalized_target_expense_code,
                    account_name=normalize_text(expense_account_name)
                    or normalized_hpp_account_name_by_code.get(normalized_target_expense_code, ""),
                    amount=abs(signed_total),
                    side="credit" if signed_total > 0 else "debit",
                    line_label=selisih_hpp_label,
                    source_balance=_round2(-signed_total),
                )
            )
        return [
            line
            for line in planned_lines
            if abs(_round2(line.amount)) >= 0.01 and normalize_text(line.side).lower() in {"debit", "credit"}
        ]

    @classmethod
    def _build_pcb_case34_planned_lines(
        cls,
        *,
        problem_balances_by_code: dict[str, float],
        expense_account_code: str,
        expense_account_name: str,
        hpp_balances_by_code: dict[str, float],
        hpp_account_name_by_code: dict[str, str],
        variance_balances_by_code: dict[str, float] | None = None,
        variance_account_name_by_code: dict[str, str] | None = None,
        additional_source_balances_by_code: dict[str, float] | None = None,
        additional_source_account_name_by_code: dict[str, str] | None = None,
        line_label: str = "",
    ) -> list[SvlDashboardPcbRepairPlannedLine]:
        planned_lines: list[SvlDashboardPcbRepairPlannedLine] = []

        def add_reversal_line(
            *,
            role: str,
            account_code: str,
            account_name: str,
            balance: float,
            current_line_label: str,
        ) -> None:
            side = cls._pcb_repair_side_from_balance(balance)
            amount = abs(_round2(balance))
            if not side or amount <= 0.0:
                return
            planned_lines.append(
                SvlDashboardPcbRepairPlannedLine(
                    role=role,
                    account_code=normalize_text(account_code).upper(),
                    account_name=normalize_text(account_name),
                    amount=amount,
                    side=side,
                    line_label=normalize_text(current_line_label),
                    source_balance=_round2(balance),
                )
            )

        base_line_label = normalize_text(line_label)
        zero_hpp_label = f"{base_line_label} - Zero HPP" if base_line_label else "Zero HPP"
        zero_variance_label = f"{base_line_label} - Zero Selisih HPP" if base_line_label else "Zero Selisih HPP"
        selisih_hpp_label = build_pcb_selisih_hpp_label(base_line_label)
        normalized_target_expense_code = normalize_text(expense_account_code).upper()
        normalized_hpp_account_name_by_code = {
            normalize_text(code).upper(): normalize_text(name)
            for code, name in dict(hpp_account_name_by_code or {}).items()
            if normalize_text(code)
        }
        normalized_hpp_balances_by_code = {
            normalize_text(code).upper(): _round2(balance)
            for code, balance in dict(hpp_balances_by_code or {}).items()
            if normalize_text(code) and abs(_round2(balance)) >= 0.01
        }
        normalized_variance_account_name_by_code = {
            normalize_text(code).upper(): normalize_text(name)
            for code, name in dict(variance_account_name_by_code or {}).items()
            if normalize_text(code)
        }
        normalized_variance_balances_by_code = {
            normalize_text(code).upper(): _round2(balance)
            for code, balance in dict(variance_balances_by_code or {}).items()
            if normalize_text(code) and abs(_round2(balance)) >= 0.01
        }
        normalized_additional_source_account_name_by_code = {
            normalize_text(code).upper(): normalize_text(name)
            for code, name in dict(additional_source_account_name_by_code or {}).items()
            if normalize_text(code)
        }
        normalized_additional_source_balances_by_code = {
            normalize_text(code).upper(): _round2(balance)
            for code, balance in dict(additional_source_balances_by_code or {}).items()
            if normalize_text(code) and abs(_round2(balance)) >= 0.01
        }

        for account_code in _PCB_CASE2_PROBLEM_CODES:
            balance = _round2(problem_balances_by_code.get(account_code, 0.0))
            if abs(balance) < 0.01:
                continue
            add_reversal_line(
                role=f"problem_{account_code}",
                account_code=account_code,
                account_name="",
                balance=balance,
                current_line_label=base_line_label,
            )
        audit_clearing_label = f"{base_line_label} - Audit Clearing" if base_line_label else "Audit Clearing"
        for account_code in sorted(normalized_additional_source_balances_by_code):
            add_reversal_line(
                role=f"audit_clearing_{account_code}",
                account_code=account_code,
                account_name=normalized_additional_source_account_name_by_code.get(account_code, ""),
                balance=normalized_additional_source_balances_by_code.get(account_code, 0.0),
                current_line_label=audit_clearing_label,
            )

        for account_code in sorted(normalized_variance_balances_by_code):
            add_reversal_line(
                role=f"variance_zero_{account_code}",
                account_code=account_code,
                account_name=normalized_variance_account_name_by_code.get(account_code, ""),
                balance=normalized_variance_balances_by_code.get(account_code, 0.0),
                current_line_label=zero_variance_label,
            )
        for account_code in sorted(normalized_hpp_balances_by_code):
            if account_code == normalized_target_expense_code:
                continue
            add_reversal_line(
                role=f"hpp_zero_{account_code}",
                account_code=account_code,
                account_name=normalized_hpp_account_name_by_code.get(account_code, ""),
                balance=normalized_hpp_balances_by_code.get(account_code, 0.0),
                current_line_label=zero_hpp_label,
            )

        signed_total = _round2(sum(cls._pcb_planned_line_signed_amount(line) for line in planned_lines))
        if abs(signed_total) >= 0.01:
            planned_lines.append(
                SvlDashboardPcbRepairPlannedLine(
                    role="selisih_hpp",
                    account_code=normalized_target_expense_code,
                    account_name=normalize_text(expense_account_name)
                    or normalized_hpp_account_name_by_code.get(normalized_target_expense_code, ""),
                    amount=abs(signed_total),
                    side="credit" if signed_total > 0 else "debit",
                    line_label=selisih_hpp_label,
                    source_balance=_round2(-signed_total),
                )
            )
        return [
            line
            for line in planned_lines
            if abs(_round2(line.amount)) >= 0.01 and normalize_text(line.side).lower() in {"debit", "credit"}
        ]

    @classmethod
    def _build_pcb_case5_planned_lines(
        cls, *, problem_balances_by_code, expense_account_code, expense_account_name,
        basis_amount, valuation_account_code="", valuation_account_name="",
        standard_amount=0.0, line_label="",
    ) -> list[SvlDashboardPcbRepairPlannedLine]:
        value = _round2(basis_amount)
        suspense = _round2(problem_balances_by_code.get("2103006", 0.0))
        if value <= 0 or suspense <= 0 or not valuation_account_code:
            return []
        # ponytail: one balanced JE per item; receipt and value-only legs remain distinct.
        specs = [
            ("receipt_inventory", valuation_account_code, valuation_account_name, value, "Pemulihan SVL aktual"),
            ("receipt_counterpart", "2103006", "Hutang Suspense", -value, "Suspense penerimaan"),
        ]
        residual = _round2(suspense - value)
        if abs(residual) >= 0.01:
            expected = _round2(standard_amount) if standard_amount > 0 else value
            specs.extend([
                ("cost_change_offset", expense_account_code, expense_account_name, _round2(expected-value), "Offset perubahan cost - review revaluasi/pemakaian"),
                ("selisih_hpp", expense_account_code, expense_account_name, _round2(suspense-expected), "Selisih bill terhadap standard cost - wajib review"),
                ("cost_counterpart", "2103006", "Hutang Suspense", -residual, "Sisa suspense bill"),
            ])
        return [SvlDashboardPcbRepairPlannedLine(
            role=role, account_code=code, account_name=name, amount=abs(_round2(signed)),
            side="debit" if signed > 0 else "credit",
            line_label=f"{label} - {line_label}" if line_label else label,
            source_balance=_round2(signed),
        ) for role, code, name, signed, label in specs if abs(_round2(signed)) >= 0.01]

    @classmethod
    def _build_pcb_case6_planned_lines(
        cls, *, problem_balances_by_code, expense_account_code, expense_account_name,
        expense_balance, basis_amount, valuation_account_code="", valuation_account_name="",
        bill_expense_account_code="", bill_expense_account_name="", line_label="",
    ) -> list[SvlDashboardPcbRepairPlannedLine]:
        value = _round2(basis_amount)
        if value <= 0 or not valuation_account_code or not bill_expense_account_code:
            return []
        return [
            SvlDashboardPcbRepairPlannedLine(role="receipt_inventory", account_code=valuation_account_code,
                account_name=valuation_account_name, amount=value, side="debit", line_label="Pemulihan SVL aktual"),
            SvlDashboardPcbRepairPlannedLine(role="receipt_counterpart", account_code=bill_expense_account_code,
                account_name=bill_expense_account_name, amount=value, side="credit", line_label="Reclass akun beban bill aktual"),
        ]

    @classmethod
    def _build_pcb_case8_planned_lines(
        cls,
        *,
        valuation_account_code: str,
        valuation_account_name: str,
        expense_account_code: str,
        expense_account_name: str,
        return_gap: float,
        line_label: str = "",
    ) -> list[SvlDashboardPcbRepairPlannedLine]:
        amount = abs(_round2(return_gap))
        if amount < 0.01:
            return []
        clean_valuation_code = normalize_text(valuation_account_code).upper()
        clean_expense_code = normalize_text(expense_account_code).upper()
        if not clean_valuation_code or not clean_expense_code:
            return []
        base_line_label = normalize_text(line_label) or "Case 8 Return Value Mismatch"
        if _round2(return_gap) > 0.0:
            return [
                SvlDashboardPcbRepairPlannedLine(
                    role="case8_inventory_restore",
                    account_code=clean_valuation_code,
                    account_name=normalize_text(valuation_account_name),
                    amount=amount,
                    side="debit",
                    line_label=base_line_label,
                    source_balance=amount,
                ),
                SvlDashboardPcbRepairPlannedLine(
                    role="case8_hpp_offset",
                    account_code=clean_expense_code,
                    account_name=normalize_text(expense_account_name),
                    amount=amount,
                    side="credit",
                    line_label=base_line_label,
                    source_balance=-amount,
                ),
            ]
        return [
            SvlDashboardPcbRepairPlannedLine(
                role="case8_hpp_restore",
                account_code=clean_expense_code,
                account_name=normalize_text(expense_account_name),
                amount=amount,
                side="debit",
                line_label=base_line_label,
                source_balance=amount,
            ),
            SvlDashboardPcbRepairPlannedLine(
                role="case8_inventory_offset",
                account_code=clean_valuation_code,
                account_name=normalize_text(valuation_account_name),
                amount=amount,
                side="credit",
                line_label=base_line_label,
                source_balance=-amount,
            ),
        ]

    @classmethod
    def _build_pcb_case9_planned_lines(
        cls,
        *,
        holder_basis: str,
        amount: float,
        hpp_balance: float = 0.0,
        inventory_balance: float = 0.0,
        valuation_account_code: str,
        valuation_account_name: str,
        expense_account_code: str,
        expense_account_name: str,
        line_label: str = "",
    ) -> list[SvlDashboardPcbRepairPlannedLine]:
        clean_holder_basis = normalize_text(holder_basis).lower()
        clean_valuation_code = normalize_text(valuation_account_code).upper()
        clean_expense_code = normalize_text(expense_account_code).upper()
        clean_amount = abs(_round2(amount))
        if clean_amount < 0.01:
            return []
        base_line_label = normalize_text(line_label) or "Case 9 UoM Scale Mismatch"
        if clean_holder_basis == "case2_downstream_clearing":
            hpp_amount = abs(_round2(hpp_balance))
            inventory_amount = abs(_round2(inventory_balance))
            if not clean_expense_code or (inventory_amount >= 0.01 and not clean_valuation_code):
                return []
            if hpp_amount < 0.01:
                hpp_amount = clean_amount
            planned_lines: list[SvlDashboardPcbRepairPlannedLine] = []
            if hpp_amount >= 0.01 and clean_expense_code:
                hpp_side = "debit" if _round2(hpp_balance) <= -0.01 else "credit"
                clearing_side = "credit" if hpp_side == "debit" else "debit"
                planned_lines.extend(
                    [
                        SvlDashboardPcbRepairPlannedLine(
                            role="case9_hpp_reclass",
                            account_code=clean_expense_code,
                            account_name=normalize_text(expense_account_name),
                            amount=hpp_amount,
                            side=hpp_side,
                            line_label=base_line_label,
                            source_balance=hpp_amount if hpp_side == "debit" else -hpp_amount,
                        ),
                        SvlDashboardPcbRepairPlannedLine(
                            role="case9_hpp_clearing_offset",
                            account_code=_PCB_ADJUSTMENT_CLEARING_CODE,
                            account_name="Clearing - System Pending Entries",
                            amount=hpp_amount,
                            side=clearing_side,
                            line_label=base_line_label,
                            source_balance=hpp_amount if clearing_side == "debit" else -hpp_amount,
                        ),
                    ]
                )
            if inventory_amount >= 0.01 and clean_valuation_code:
                inventory_side = "credit" if _round2(inventory_balance) >= 0.01 else "debit"
                # Cancel the HPP clearing leg exactly. The signed economic
                # difference belongs to HPP, not a remaining clearing balance.
                clearing_offset_amount = hpp_amount
                clearing_side = hpp_side
                planned_lines.extend(
                    [
                        SvlDashboardPcbRepairPlannedLine(
                            role="case9_inventory_offset",
                            account_code=clean_valuation_code,
                            account_name=normalize_text(valuation_account_name),
                            amount=inventory_amount,
                            side=inventory_side,
                            line_label=base_line_label,
                            source_balance=inventory_amount if inventory_side == "debit" else -inventory_amount,
                        ),
                        SvlDashboardPcbRepairPlannedLine(
                            role="case9_inventory_clearing_offset",
                            account_code=_PCB_ADJUSTMENT_CLEARING_CODE,
                            account_name="Clearing - System Pending Entries",
                            amount=clearing_offset_amount,
                            side=clearing_side,
                            line_label=base_line_label,
                            source_balance=clearing_offset_amount if clearing_side == "debit" else -clearing_offset_amount,
                        ),
                    ]
                )
            if planned_lines:
                price_gap = _round2(-sum(line.source_balance for line in planned_lines))
                if abs(price_gap) >= 0.01:
                    planned_lines.append(
                        SvlDashboardPcbRepairPlannedLine(
                            role="case9_price_gap_to_expense",
                            account_code=clean_expense_code,
                            account_name=normalize_text(expense_account_name),
                            amount=abs(price_gap),
                            side="debit" if price_gap > 0 else "credit",
                            line_label=base_line_label,
                            source_balance=price_gap,
                        )
                    )
                return planned_lines
            if not clean_expense_code:
                return []
            return [
                SvlDashboardPcbRepairPlannedLine(
                    role="case9_hpp_reclass",
                    account_code=clean_expense_code,
                    account_name=normalize_text(expense_account_name),
                    amount=clean_amount,
                    side="debit",
                    line_label=base_line_label,
                    source_balance=clean_amount,
                ),
                SvlDashboardPcbRepairPlannedLine(
                    role="case9_clearing_offset",
                    account_code=_PCB_ADJUSTMENT_CLEARING_CODE,
                    account_name="Clearing - System Pending Entries",
                    amount=clean_amount,
                    side="credit",
                    line_label=base_line_label,
                    source_balance=-clean_amount,
                ),
            ]
        if clean_holder_basis == "open_suspense_revalued":
            if not clean_expense_code:
                return []
            return [
                SvlDashboardPcbRepairPlannedLine(
                    role="case9_suspend_adjustment",
                    account_code="2103006",
                    account_name="Hutang Suspensed Pengadaan Barang/Jasa / Suspensed",
                    amount=clean_amount,
                    side="debit",
                    line_label=base_line_label,
                    source_balance=clean_amount,
                ),
                SvlDashboardPcbRepairPlannedLine(
                    role="case9_hpp_offset",
                    account_code=clean_expense_code,
                    account_name=normalize_text(expense_account_name),
                    amount=clean_amount,
                    side="credit",
                    line_label=base_line_label,
                    source_balance=-clean_amount,
                ),
            ]
        if clean_holder_basis == "open_suspense_inventory":
            if not clean_valuation_code:
                return []
            return [
                SvlDashboardPcbRepairPlannedLine(
                    role="case9_suspend_adjustment",
                    account_code="2103006",
                    account_name="Hutang Suspensed Pengadaan Barang/Jasa / Suspensed",
                    amount=clean_amount,
                    side="debit",
                    line_label=base_line_label,
                    source_balance=clean_amount,
                ),
                SvlDashboardPcbRepairPlannedLine(
                    role="case9_inventory_offset",
                    account_code=clean_valuation_code,
                    account_name=normalize_text(valuation_account_name),
                    amount=clean_amount,
                    side="credit",
                    line_label=base_line_label,
                    source_balance=-clean_amount,
                ),
            ]
        return []

    @staticmethod
    def _pcb_multi_line_case_label(pcb_case: str) -> str:
        return _PCB_MULTI_LINE_CASE_LABELS.get(normalize_text(pcb_case).lower(), _PCB_MULTI_LINE_CASE_LABELS["case2"])

    @staticmethod
    def _select_pcb_case2_bill_line_id(
        *,
        product_id: int,
        bill_move_ids: list[int],
        lines_by_move: dict[int, list[dict[str, Any]]],
        purchase_line_product_map: dict[int, int],
        purchase_line_id: int,
    ) -> int:
        if product_id <= 0 or not bill_move_ids:
            return 0
        exact_candidate_ids: set[int] = set()
        product_candidate_ids: set[int] = set()
        for bill_move_id in bill_move_ids:
            for line in lines_by_move.get(int(bill_move_id or 0), []):
                line_id = int(line.get("id") or 0)
                if line_id <= 0:
                    continue
                line_product_id = _many2one_id(line.get("product_id"))
                line_purchase_line_id = _many2one_id(line.get("purchase_line_id"))
                if line_product_id <= 0 and line_purchase_line_id > 0:
                    line_product_id = int(purchase_line_product_map.get(line_purchase_line_id) or 0)
                if line_product_id != product_id:
                    continue
                product_candidate_ids.add(line_id)
                if purchase_line_id > 0 and line_purchase_line_id == purchase_line_id:
                    exact_candidate_ids.add(line_id)
        if len(exact_candidate_ids) == 1:
            return next(iter(exact_candidate_ids))
        if purchase_line_id <= 0 and len(product_candidate_ids) == 1:
            return next(iter(product_candidate_ids))
        return 0

    def _build_pcb_case2_economic_fields(
        self,
        *,
        bill_line_id: int,
        bill_move_ids: list[int],
        stock_move_id: int,
        lines_by_move: dict[int, list[dict[str, Any]]],
        stock_move_rows_by_id: dict[int, dict[str, Any]] | None,
    ) -> dict[str, Any]:
        stock_move_rows_by_id = stock_move_rows_by_id or {}
        bill_line_row: dict[str, Any] = {}
        if int(bill_line_id or 0) > 0:
            for bill_move_id in bill_move_ids:
                for line in lines_by_move.get(int(bill_move_id or 0), []):
                    if int(line.get("id") or 0) == int(bill_line_id or 0):
                        bill_line_row = line
                        break
                if bill_line_row:
                    break
        stock_move_row = stock_move_rows_by_id.get(int(stock_move_id or 0)) or {}
        product_uom_id = _many2one_id((bill_line_row or {}).get("product_uom_id"))
        if product_uom_id <= 0:
            product_uom_id = _many2one_id((stock_move_row or {}).get("product_uom"))
        quantity = _round2(
            (bill_line_row or {}).get("quantity")
            or (stock_move_row or {}).get("product_qty")
            or (stock_move_row or {}).get("quantity")
        )
        currency_id = _many2one_id((bill_line_row or {}).get("currency_id"))
        amount_currency = _round2((bill_line_row or {}).get("amount_currency"))
        if abs(amount_currency) < 0.01:
            amount_currency = _round2((bill_line_row or {}).get("price_subtotal"))
        amount_currency_basis = abs(_round2((bill_line_row or {}).get("balance")))
        if amount_currency_basis <= 0.01:
            amount_currency_basis = abs(_round2((bill_line_row or {}).get("price_subtotal")))
        analytic_distribution = self._normalize_pcb_analytic_distribution((bill_line_row or {}).get("analytic_distribution"))
        bill_price_unit = _round2((bill_line_row or {}).get("price_unit"))
        gr_price_unit = _round2((stock_move_row or {}).get("price_unit"))
        price_gap_value = 0.0
        if quantity > 0.0 and (abs(bill_price_unit) > 0.0 or abs(gr_price_unit) > 0.0):
            price_gap_value = _round2(abs((bill_price_unit - gr_price_unit) * quantity))
        allocated_amount = amount_currency_basis
        if allocated_amount <= 0.01:
            allocated_amount = price_gap_value
        return {
            "product_uom_id": int(product_uom_id or 0),
            "quantity": float(quantity or 0.0),
            "currency_id": int(currency_id or 0),
            "amount_currency": float(amount_currency or 0.0),
            "amount_currency_basis": float(amount_currency_basis or 0.0),
            "analytic_distribution": analytic_distribution,
            "bill_price_unit": float(bill_price_unit or 0.0),
            "gr_price_unit": float(gr_price_unit or 0.0),
            "price_gap_value": float(price_gap_value or 0.0),
            "allocated_amount": float(allocated_amount or 0.0),
        }

    def _resolve_pcb_case2_item_relations(
        self,
        *,
        item_row: SvlDashboardCycleItemRow,
        product_id: int,
        picking_ids: list[int] | None,
        bill_move_ids: list[int],
        raw_lines: list[dict[str, Any]] | None,
        stock_move_rows_by_id: dict[int, dict[str, Any]] | None,
        lines_by_move: dict[int, list[dict[str, Any]]],
        move_info_map: dict[int, dict[str, Any]],
        purchase_line_product_map: dict[int, int],
    ) -> dict[str, Any]:
        clean_picking_ids = {
            int(value or 0)
            for value in list(picking_ids or [])
            if int(value or 0) > 0
        }
        stock_move_rows_by_id = stock_move_rows_by_id or {}
        item_stock_move_ids = sorted(
            {
                int(value or 0)
                for value in list(getattr(item_row, "stock_move_ids", None) or [])
                if int(value or 0) > 0
            }
        )
        filtered_stock_move_ids: list[int] = []
        for stock_move_id in item_stock_move_ids:
            stock_move_row = stock_move_rows_by_id.get(stock_move_id) or {}
            row_picking_id = _many2one_id(stock_move_row.get("picking_id"))
            if clean_picking_ids and row_picking_id > 0 and row_picking_id not in clean_picking_ids:
                continue
            filtered_stock_move_ids.append(stock_move_id)
        stock_move_ids = filtered_stock_move_ids or item_stock_move_ids
        stock_move_id = int(stock_move_ids[0] or 0) if stock_move_ids else 0

        purchase_line_id = 0
        if stock_move_id > 0:
            purchase_line_id = _many2one_id((stock_move_rows_by_id.get(stock_move_id) or {}).get("purchase_line_id"))
        if purchase_line_id <= 0:
            purchase_line_ids = sorted(
                {
                    int(value or 0)
                    for value in list(getattr(item_row, "purchase_line_ids", None) or [])
                    if int(value or 0) > 0
                }
            )
            if purchase_line_ids:
                purchase_line_id = purchase_line_ids[0]

        stj_move_ids = sorted(
            {
                int(value or 0)
                for value in list(getattr(item_row, "stj_move_ids", None) or [])
                if int(value or 0) > 0
            }
        )
        stj_refs = sorted(
            {
                normalize_text(value)
                for value in list(getattr(item_row, "stj_refs", None) or [])
                if normalize_text(value)
            }
        )
        raw_stj_move_ids, raw_stj_refs = self._pcb_item_stj_links_from_raw_lines(
            raw_lines=list(raw_lines or []),
            item_row=item_row,
            move_info_map=move_info_map,
        )
        if raw_stj_refs:
            stj_refs = sorted({*stj_refs, *raw_stj_refs})
        if raw_stj_move_ids:
            stj_move_ids = sorted({*stj_move_ids, *raw_stj_move_ids})
        if not stj_refs and stj_move_ids:
            stj_refs = sorted(
                {
                    normalize_text((move_info_map.get(move_id) or {}).get("name"))
                    for move_id in stj_move_ids
                    if normalize_text((move_info_map.get(move_id) or {}).get("name"))
                }
            )

        clean_bill_move_ids = sorted(
            {
                int(value or 0)
                for value in (bill_move_ids or list(getattr(item_row, "bill_move_ids", None) or []))
                if int(value or 0) > 0
            }
        )
        bill_line_id = self._select_pcb_case2_bill_line_id(
            product_id=product_id,
            bill_move_ids=clean_bill_move_ids,
            lines_by_move=lines_by_move,
            purchase_line_product_map=purchase_line_product_map,
            purchase_line_id=purchase_line_id,
        )
        return {
            "bill_line_id": int(bill_line_id or 0),
            "purchase_line_id": int(purchase_line_id or 0),
            "stock_move_id": int(stock_move_id or 0),
            "stock_move_ids": [int(value) for value in stock_move_ids if int(value or 0) > 0],
            "stj_move_ids": [int(value) for value in stj_move_ids if int(value or 0) > 0],
            "stj_refs": [normalize_text(value) for value in stj_refs if normalize_text(value)],
        }

    @staticmethod
    def _classify_pcb_cycle_case(
        *,
        account_rows: list[SvlDashboardCycleAccountRow],
        raw_lines: list[dict[str, Any]],
        bill_refs: list[str],
        item_rows: list[SvlDashboardCycleItemRow] | None = None,
        primary_case: str = "",
    ) -> str:
        normalized_primary_case = normalize_text(primary_case).lower()
        if normalized_primary_case:
            return normalized_primary_case
        for item_row in list(item_rows or []):
            normalized_item_case = normalize_text(getattr(item_row, "primary_case", "")).lower()
            if normalized_item_case:
                return normalized_item_case
        by_code = {row.code: row for row in list(account_rows or []) if normalize_text(row.code)}
        a2103006 = by_code.get("2103006")
        a1108099 = by_code.get("1108099")
        prob_2103006 = a2103006 is not None and normalize_text(a2103006.status).lower() == "problem"
        prob_1108099 = a1108099 is not None and normalize_text(a1108099.status).lower() == "problem"
        has_bill = bool(list(bill_refs or []))
        has_eligible_case34_items = any(
            bool(getattr(item_row, "eligible_case34", False))
            for item_row in list(item_rows or [])
        )
        if prob_2103006 and prob_1108099:
            if abs(abs(_round2(a2103006.net_balance)) - abs(_round2(a1108099.net_balance))) < 1.0:
                return "case1"
        account_status_by_code = {
            normalize_text(row.code).upper(): normalize_text(row.status).lower()
            for row in list(account_rows or [])
            if normalize_text(row.code)
        }
        for line in list(raw_lines or []):
            if normalize_text(line.get("jenis")).upper() != "BILL":
                continue
            if normalize_text(line.get("tipe_akun")).lower() != "expense_direct_cost":
                continue
            account_code = normalize_text(line.get("akun_code")).upper()
            if account_code in _PCB_COGS_VARIANCE_CODES:
                continue
            if account_status_by_code.get(account_code, "acceptable") != "info":
                return "case2"
        if prob_2103006 and prob_1108099 and has_bill and (not item_rows or has_eligible_case34_items):
            return "case3"
        if prob_2103006 and not prob_1108099 and has_bill and (not item_rows or has_eligible_case34_items):
            return "case4"
        return "case_lainnya"

    @staticmethod
    def _classify_pcb_cycle_case_without_item_gate(
        *,
        account_rows: list[SvlDashboardCycleAccountRow],
        raw_lines: list[dict[str, Any]],
        bill_refs: list[str],
    ) -> str:
        by_code = {row.code: row for row in list(account_rows or []) if normalize_text(row.code)}
        a2103006 = by_code.get("2103006")
        a1108099 = by_code.get("1108099")
        prob_2103006 = a2103006 is not None and normalize_text(a2103006.status).lower() == "problem"
        prob_1108099 = a1108099 is not None and normalize_text(a1108099.status).lower() == "problem"
        has_bill = bool(list(bill_refs or []))
        if prob_2103006 and prob_1108099:
            if abs(abs(_round2(a2103006.net_balance)) - abs(_round2(a1108099.net_balance))) < 1.0:
                return "case1"
        account_status_by_code = {
            normalize_text(row.code).upper(): normalize_text(row.status).lower()
            for row in list(account_rows or [])
            if normalize_text(row.code)
        }
        for line in list(raw_lines or []):
            if normalize_text(line.get("jenis")).upper() != "BILL":
                continue
            if normalize_text(line.get("tipe_akun")).lower() != "expense_direct_cost":
                continue
            account_code = normalize_text(line.get("akun_code")).upper()
            if account_code in _PCB_COGS_VARIANCE_CODES:
                continue
            if account_status_by_code.get(account_code, "acceptable") != "info":
                return "case2"
        if prob_2103006 and prob_1108099 and has_bill:
            return "case3"
        if prob_2103006 and not prob_1108099 and has_bill:
            return "case4"
        return "case_lainnya"

    def _build_pcb_case2_repair_rows(
        self,
        *,
        pcb_case: str = "case2",
        cycle_status: str,
        picking_id: int,
        picking_name: str,
        gr_date: str,
        partner_name: str,
        po_names: list[str],
        bill_move_ids: list[int],
        payment_move_ids: list[int] | None = None,
        bank_move_ids: list[int] | None = None,
        all_cycle_move_ids: list[int] | None = None,
        item_rows: list[SvlDashboardCycleItemRow] | None,
        raw_lines: list[dict[str, Any]],
        lines_by_move: dict[int, list[dict[str, Any]]],
        account_info_map: dict[int, dict[str, Any]],
        move_info_map: dict[int, dict[str, Any]],
        product_info_map: dict[int, dict[str, Any]],
        bill_rows_by_id: dict[int, dict[str, Any]],
        purchase_line_product_map: dict[int, int],
        purchase_line_po_name_map: dict[int, str],
        stock_move_rows_by_id: dict[int, dict[str, Any]] | None = None,
        picking_ids: list[int] | None = None,
        partner_id: int = 0,
        bill_refs: list[str] | None = None,
        payment_move_ids_by_product: dict[int, list[int]] | None = None,
        bank_move_ids_by_product: dict[int, list[int]] | None = None,
    ) -> list[SvlDashboardPcbCase2RepairRow]:
        normalized_pcb_case = normalize_text(pcb_case).lower() or "case2"
        if normalized_pcb_case not in _PCB_MULTI_LINE_CASES:
            return []
        if normalize_text(cycle_status) != "problem" and normalized_pcb_case not in {"case8a", "case8b", "case9"}:
            return []
        cycle_payment_ids = {int(value or 0) for value in list(payment_move_ids or []) if int(value or 0) > 0}
        cycle_bank_ids = {int(value or 0) for value in list(bank_move_ids or []) if int(value or 0) > 0}
        cycle_move_ids = [
            int(value or 0)
            for value in list(all_cycle_move_ids or [])
            if int(value or 0) > 0
        ]
        payment_move_ids_by_product = dict(payment_move_ids_by_product or {})
        bank_move_ids_by_product = dict(bank_move_ids_by_product or {})

        item_row_by_product_id = {
            int(getattr(item_row, "product_id", 0) or 0): item_row
            for item_row in list(item_rows or [])
            if int(getattr(item_row, "product_id", 0) or 0) > 0
        }
        if not item_row_by_product_id:
            return []

        bank_account_codes = sorted(
            {
                normalize_text(line.get("akun_code")).upper()
                for line in list(raw_lines or [])
                if normalize_text(line.get("jenis")).upper() in {"BK", "PBK"}
                and normalize_text(line.get("akun_code"))
            }
        )
        cycle_bill_expense_codes = sorted(
            {
                normalize_text(line.get("akun_code")).upper()
                for line in list(raw_lines or [])
                if normalize_text(line.get("jenis")).upper() == "BILL"
                and normalize_text(line.get("tipe_akun")).lower() == "expense_direct_cost"
                and normalize_text(line.get("akun_code")).upper() not in _PCB_COGS_VARIANCE_CODES
            }
        )
        candidate_meta_by_product_id: dict[int, dict[str, Any]] = {}
        for bill_move_id in bill_move_ids:
            bill_name = normalize_text((move_info_map.get(int(bill_move_id or 0)) or {}).get("name"))
            bill_row = bill_rows_by_id.get(int(bill_move_id or 0)) or {}
            bill_date = normalize_text(bill_row.get("invoice_date")) or normalize_text(bill_row.get("date"))
            partner_id = _many2one_id((move_info_map.get(int(bill_move_id or 0)) or {}).get("partner_id"))
            for line in lines_by_move.get(int(bill_move_id or 0), []):
                account_id = _many2one_id(line.get("account_id"))
                account_info = account_info_map.get(account_id, {})
                account_code = normalize_text(account_info.get("code")).upper()
                account_type = normalize_text(account_info.get("account_type")).lower()
                is_suspend_candidate = normalized_pcb_case in {"case2", "case5", "case9"} and account_code == "2103006"
                is_expense_candidate = (
                    normalized_pcb_case in {"case3", "case4", "case6", "case8a", "case8b", "case9"}
                    and account_type in {"expense_direct_cost", "expense"}
                    and account_code not in _PCB_COGS_VARIANCE_CODES
                )
                if not is_suspend_candidate and not is_expense_candidate:
                    continue
                product_id = _many2one_id(line.get("product_id"))
                purchase_line_id = _many2one_id(line.get("purchase_line_id"))
                if product_id <= 0 and purchase_line_id > 0:
                    product_id = int(purchase_line_product_map.get(purchase_line_id) or 0)
                if product_id <= 0 or product_id not in item_row_by_product_id:
                    continue
                entry = candidate_meta_by_product_id.setdefault(
                    product_id,
                    {
                        "bill_move_ids": set(),
                        "bill_names": set(),
                        "po_names": set(),
                        "bill_dates": set(),
                        "partner_id": 0,
                        "bill_suspend": False,
                        "bill_line_ids": set(),
                        "expense_account_codes": [],
                        "expense_account_name_by_code": {},
                    },
                )
                entry["bill_move_ids"].add(int(bill_move_id or 0))
                entry.setdefault("bill_line_ids", set()).add(int(line.get("id") or 0))
                if bill_name:
                    entry["bill_names"].add(bill_name)
                if bill_date:
                    entry["bill_dates"].add(bill_date)
                po_name = normalize_text(purchase_line_po_name_map.get(purchase_line_id, "")) or normalize_text(bill_row.get("invoice_origin"))
                if po_name:
                    entry["po_names"].add(po_name)
                if int(entry.get("partner_id") or 0) <= 0 and partner_id > 0:
                    entry["partner_id"] = partner_id
                if account_code == "2103006":
                    entry["bill_suspend"] = True
                if is_expense_candidate and account_code and account_code not in list(entry.get("expense_account_codes") or []):
                    entry["expense_account_codes"] = [*list(entry.get("expense_account_codes") or []), account_code]
                if is_expense_candidate and account_code:
                    expense_account_name_by_code = dict(entry.get("expense_account_name_by_code") or {})
                    expense_account_name_by_code.setdefault(
                        account_code,
                        normalize_text(account_info.get("name")) or _many2one_name(line.get("account_id")),
                    )
                    entry["expense_account_name_by_code"] = expense_account_name_by_code

        fallback_bill_move_ids = {
            int(value or 0)
            for value in list(bill_move_ids or [])
            if int(value or 0) > 0
        }
        fallback_bill_names = {
            normalize_text((move_info_map.get(bill_move_id) or {}).get("name"))
            for bill_move_id in fallback_bill_move_ids
            if normalize_text((move_info_map.get(bill_move_id) or {}).get("name"))
        }
        fallback_bill_ref_names = sorted(
            {
                normalize_text(value)
                for value in list(bill_refs or [])
                if normalize_text(value)
            }
        )
        fallback_bill_dates = {
            normalize_text((bill_rows_by_id.get(bill_move_id) or {}).get("invoice_date"))
            or normalize_text((bill_rows_by_id.get(bill_move_id) or {}).get("date"))
            for bill_move_id in fallback_bill_move_ids
            if normalize_text((bill_rows_by_id.get(bill_move_id) or {}).get("invoice_date"))
            or normalize_text((bill_rows_by_id.get(bill_move_id) or {}).get("date"))
        }
        fallback_po_names = {
            normalize_text(value)
            for value in list(po_names or [])
            if normalize_text(value)
        }
        for bill_move_id in fallback_bill_move_ids:
            invoice_origin = normalize_text((bill_rows_by_id.get(bill_move_id) or {}).get("invoice_origin"))
            if invoice_origin:
                fallback_po_names.add(invoice_origin)
        fallback_partner_id = next(
            (
                _many2one_id((move_info_map.get(bill_move_id) or {}).get("partner_id"))
                for bill_move_id in sorted(fallback_bill_move_ids)
                if _many2one_id((move_info_map.get(bill_move_id) or {}).get("partner_id")) > 0
            ),
            int(partner_id or 0),
        )
        fallback_partner_name = normalize_text(partner_name)
        if not fallback_partner_name:
            fallback_partner_name = next(
                (
                    _many2one_name((move_info_map.get(bill_move_id) or {}).get("partner_id"))
                    or _many2one_name((bill_rows_by_id.get(bill_move_id) or {}).get("partner_id"))
                    for bill_move_id in sorted(fallback_bill_move_ids)
                    if (
                        _many2one_name((move_info_map.get(bill_move_id) or {}).get("partner_id"))
                        or _many2one_name((bill_rows_by_id.get(bill_move_id) or {}).get("partner_id"))
                    )
                ),
                "",
            )

        for product_id, item_row in item_row_by_product_id.items():
            item_account_rows = list(getattr(item_row, "account_rows", []) or [])
            item_account_by_code = {
                normalize_text(account_row.code).upper(): account_row
                for account_row in item_account_rows
                if normalize_text(account_row.code)
            }
            problem_balances_for_item = {
                account_code: _round2(getattr(item_account_by_code.get(account_code), "net_balance", 0.0))
                for account_code in _PCB_CASE2_PROBLEM_CODES
                if abs(_round2(getattr(item_account_by_code.get(account_code), "net_balance", 0.0))) >= 0.01
            }
            preexisting_meta = candidate_meta_by_product_id.get(product_id) or {}
            if normalized_pcb_case in {"case5", "case6"} and preexisting_meta:
                continue  # Preserve exact bill accounts; category fallback is not bill evidence.
            matched_expense_codes = [
                code
                for code in cycle_bill_expense_codes
                if code in item_account_by_code
            ]
            product_expense_code = normalize_text((product_info_map.get(product_id) or {}).get("expense_account_code")).upper()
            if not matched_expense_codes:
                if product_expense_code and (cycle_bill_expense_codes or preexisting_meta):
                    matched_expense_codes = [product_expense_code]
            keep_problem_only_row = (
                normalized_pcb_case in {"case3", "case4", "case5"} and bool(problem_balances_for_item)
            ) or normalized_pcb_case in {"case8a", "case8b", "case9"}
            if not matched_expense_codes and not keep_problem_only_row:
                continue
            entry = candidate_meta_by_product_id.setdefault(
                product_id,
                {
                    "bill_move_ids": set(),
                    "bill_names": set(),
                    "po_names": set(),
                    "bill_dates": set(),
                    "partner_id": 0,
                    "bill_suspend": False,
                    "expense_account_codes": [],
                    "expense_account_name_by_code": {},
                },
            )
            entry["bill_move_ids"].update(fallback_bill_move_ids)
            entry["bill_names"].update(fallback_bill_names)
            entry["po_names"].update(fallback_po_names)
            entry["bill_dates"].update(fallback_bill_dates)
            if int(entry.get("partner_id") or 0) <= 0 and fallback_partner_id > 0:
                entry["partner_id"] = fallback_partner_id
            if not matched_expense_codes and normalized_pcb_case in {"case8a", "case8b", "case9"} and product_expense_code:
                matched_expense_codes = [product_expense_code]
            for expense_code in matched_expense_codes:
                if expense_code not in list(entry.get("expense_account_codes") or []):
                    entry["expense_account_codes"] = [*list(entry.get("expense_account_codes") or []), expense_code]
                expense_account_name_by_code = dict(entry.get("expense_account_name_by_code") or {})
                expense_account_name_by_code.setdefault(
                    expense_code,
                    normalize_text(getattr(item_account_by_code.get(expense_code), "name", "")),
                )
                entry["expense_account_name_by_code"] = expense_account_name_by_code
        if not candidate_meta_by_product_id:
            return []

        results: list[SvlDashboardPcbCase2RepairRow] = []
        for product_id in sorted(candidate_meta_by_product_id):
            item_row = item_row_by_product_id.get(product_id)
            if item_row is None:
                continue
            item_account_rows = list(getattr(item_row, "account_rows", []) or [])
            item_account_by_code = {
                normalize_text(account_row.code).upper(): account_row
                for account_row in item_account_rows
                if normalize_text(account_row.code)
            }
            product_info = product_info_map.get(product_id) or {}
            meta = candidate_meta_by_product_id.get(product_id) or {}
            valuation_account_code = normalize_text(product_info.get("valuation_account_code")).upper()
            valuation_account_name = normalize_text(product_info.get("valuation_account_name"))
            expense_account_code = normalize_text(product_info.get("expense_account_code")).upper()
            expense_account_name = normalize_text(product_info.get("expense_account_name"))
            has_item_bill = bool(getattr(item_row, "has_item_bill", False)) or bool(list(meta.get("bill_move_ids") or []))
            has_item_stj = self._item_has_pcb_case34_stj_evidence(item_row)
            expense_account_codes = [
                normalize_text(code).upper()
                for code in list(meta.get("expense_account_codes") or [])
                if normalize_text(code)
            ]
            expense_account_name_by_code = {
                normalize_text(code).upper(): normalize_text(name)
                for code, name in dict(meta.get("expense_account_name_by_code") or {}).items()
                if normalize_text(code)
            }
            if not expense_account_code:
                item_expense_candidates = [
                    item_account_by_code[code]
                    for code in expense_account_codes
                    if code in item_account_by_code
                ]
                if item_expense_candidates:
                    selected_expense_row = max(
                        item_expense_candidates,
                        key=lambda account_row: abs(_round2(getattr(account_row, "net_balance", 0.0))),
                    )
                    expense_account_code = normalize_text(getattr(selected_expense_row, "code", "")).upper()
                    expense_account_name = normalize_text(getattr(selected_expense_row, "name", "")) or expense_account_name_by_code.get(expense_account_code, "")
                elif expense_account_codes:
                    expense_account_code = expense_account_codes[0]
                    expense_account_name = expense_account_name_by_code.get(expense_account_code, "")
            if expense_account_code and not expense_account_name:
                expense_account_name = (
                    normalize_text(getattr(item_account_by_code.get(expense_account_code), "name", ""))
                    or expense_account_name_by_code.get(expense_account_code, "")
                )
            problem_balances_by_code = {
                account_code: _round2(getattr(item_account_by_code.get(account_code), "net_balance", 0.0))
                for account_code in _PCB_CASE2_PROBLEM_CODES
                if abs(_round2(getattr(item_account_by_code.get(account_code), "net_balance", 0.0))) >= 0.01
            }
            audit_rows_for_item = list(getattr(item_row, "adjustment_audit_rows", None) or [])
            has_verified_audit_rows = any(
                bool(getattr(audit_row, "verified_for_case34", False))
                for audit_row in audit_rows_for_item
            )
            external_clearing_refs = [
                normalize_text(value)
                for value in list(getattr(item_row, "external_clearing_refs", None) or [])
                if normalize_text(value)
            ]
            external_clearing_basis = normalize_text(getattr(item_row, "external_clearing_basis", ""))
            external_clearing_verified = bool(getattr(item_row, "external_clearing_verified", False))
            audit_linked_clearing_amount = _round2(
                float(getattr(item_row, "external_clearing_amount", 0.0) or 0.0)
                or float(getattr(item_row, "verified_audit_clearing_amount", 0.0) or 0.0)
            )
            if (
                normalized_pcb_case in {"case3", "case4"}
                and "2103006" in problem_balances_by_code
                and "1108099" not in problem_balances_by_code
                and audit_linked_clearing_amount < 0.01
            ):
                audit_linked_clearing_amount = _round2(
                    sum(
                        abs(_round2(float(getattr(audit_row, "repair_clearing_amount", 0.0) or 0.0)))
                        for audit_row in audit_rows_for_item
                        if not bool(getattr(audit_row, "ambiguous", False))
                        and (
                            bool(getattr(audit_row, "verified_for_case34", False))
                            or not has_verified_audit_rows
                        )
                        and normalize_text(getattr(audit_row, "clearing_account_code", "")).upper() == _PCB_ADJUSTMENT_CLEARING_CODE
                        and abs(_round2(float(getattr(audit_row, "repair_clearing_amount", 0.0) or 0.0))) >= 0.01
                    )
                )
            if audit_linked_clearing_amount >= 0.01 and not external_clearing_refs:
                for audit_row in audit_rows_for_item:
                    if bool(getattr(audit_row, "ambiguous", False)):
                        continue
                    if has_verified_audit_rows and not bool(getattr(audit_row, "verified_for_case34", False)):
                        continue
                    if normalize_text(getattr(audit_row, "clearing_account_code", "")).upper() != _PCB_ADJUSTMENT_CLEARING_CODE:
                        continue
                    ref_text = self._pcb_adjustment_external_ref_text(audit_row)
                    if ref_text and ref_text not in external_clearing_refs:
                        external_clearing_refs.append(ref_text)
                    basis_text = self._pcb_adjustment_external_basis_text(audit_row)
                    if basis_text:
                        external_clearing_basis = self._merge_pipe_text(external_clearing_basis, [basis_text])
                external_clearing_verified = external_clearing_verified or has_verified_audit_rows or audit_linked_clearing_amount >= 0.01
            hpp_balances_by_code: dict[str, float] = {}
            hpp_account_name_by_code: dict[str, str] = {}
            candidate_hpp_codes = list(
                dict.fromkeys(
                    [
                        *([expense_account_code] if expense_account_code else []),
                        *expense_account_codes,
                        *[
                            normalize_text(getattr(account_row, "code", "")).upper()
                            for account_row in item_account_rows
                            if normalize_text(getattr(account_row, "code", "")).upper()
                            and normalize_text(getattr(account_row, "account_type", "")).lower() == "expense_direct_cost"
                            and normalize_text(getattr(account_row, "code", "")).upper() not in _PCB_COGS_VARIANCE_CODES
                        ],
                    ]
                )
            )
            for account_code in candidate_hpp_codes:
                clean_account_code = normalize_text(account_code).upper()
                if not clean_account_code:
                    continue
                account_row = item_account_by_code.get(clean_account_code)
                if account_row is None:
                    continue
                balance = _round2(getattr(account_row, "net_balance", 0.0))
                if abs(balance) < 0.01:
                    continue
                hpp_balances_by_code[clean_account_code] = balance
                hpp_account_name_by_code[clean_account_code] = (
                    normalize_text(getattr(account_row, "name", ""))
                    or expense_account_name_by_code.get(clean_account_code, "")
                )
            variance_balances_by_code: dict[str, float] = {}
            variance_account_name_by_code: dict[str, str] = {}
            for account_code in _PCB_COGS_VARIANCE_CODES:
                account_row = item_account_by_code.get(account_code)
                if account_row is None:
                    continue
                balance = _round2(getattr(account_row, "net_balance", 0.0))
                if abs(balance) < 0.01:
                    continue
                variance_balances_by_code[account_code] = balance
                variance_account_name_by_code[account_code] = normalize_text(getattr(account_row, "name", ""))
            repair_basis_amount = _round2(getattr(item_row, "repair_basis_amount", 0.0))
            repair_basis_source = normalize_text(getattr(item_row, "repair_basis_source", ""))
            standard_price = _round2(getattr(item_row, "standard_price", 0.0) or product_info.get("standard_price"))
            recovery_evidence = dict(getattr(item_row, "receipt_recovery_evidence", {}) or {})
            if normalized_pcb_case in {"case5", "case6"}:
                repair_basis_amount = _round2(recovery_evidence.get("missing_value"))
                repair_basis_source = "actual_missing_receipt_svl"
            non_target_hpp_balances_by_code = {
                account_code: balance
                for account_code, balance in hpp_balances_by_code.items()
                if account_code != expense_account_code
            }
            case8_evidence = dict(getattr(item_row, "case8_evidence", None) or {})
            case9_evidence = dict(getattr(item_row, "case9_evidence", None) or {})
            if (
                normalized_pcb_case == "case9"
                and normalize_text(case9_evidence.get("holder_basis")).lower() == "case2_downstream_clearing"
                and not external_clearing_refs
            ):
                external_clearing_refs = [
                    normalize_text(value)
                    for value in list(case9_evidence.get("downstream_refs") or [])
                    if normalize_text(value)
                ]
            if normalized_pcb_case == "case2":
                if not problem_balances_by_code and not non_target_hpp_balances_by_code:
                    continue
            elif normalized_pcb_case == "case5":
                if "2103006" not in problem_balances_by_code:
                    continue
            elif normalized_pcb_case == "case6":
                if not has_item_bill:
                    continue
            elif normalized_pcb_case in {"case8a", "case8b"}:
                if not case8_evidence or abs(_round2(case8_evidence.get("return_gap"))) < 0.01:
                    continue
            elif normalized_pcb_case == "case9":
                if not case9_evidence or abs(_round2(case9_evidence.get("correction_amount") or case9_evidence.get("value_gap"))) < 0.01:
                    continue
            elif not problem_balances_by_code:
                continue
            inventory_row = item_account_by_code.get(valuation_account_code) if valuation_account_code else None
            inventory_balance = _round2(getattr(inventory_row, "net_balance", 0.0))
            review_required_candidate = False
            review_required = False
            review_reason = ""
            if normalized_pcb_case in {"case3", "case4"}:
                if not has_item_bill:
                    continue
                if not has_item_stj and audit_linked_clearing_amount < 0.01:
                    review_required_candidate = True
                    review_reason = (
                        "Bill item sudah ada, tetapi belum ada direct STJ / correction STJ / external clearing "
                        "terverifikasi. JE preview wajib direview manual karena basis item bisa allocation-based."
                    )
            elif normalized_pcb_case in {"case5", "case6"}:
                review_required_candidate = True
                review_reason = (
                    "Pemulihan memakai SVL aktual. Review jurnal existing, revaluasi/pemakaian di luar cycle "
                    "dan alokasi perubahan cost; standard cost bukan bukti revaluasi."
                )
            elif normalized_pcb_case in {"case8a", "case8b"}:
                review_required_candidate = True
                review_reason = (
                    "Case 8 Return Value Mismatch wajib direview manual: pastikan return relation, receipt SVL, "
                    "return SVL, dan vendor credit memo/bill state sudah benar sebelum execute."
                )
            elif normalized_pcb_case == "case9":
                review_required_candidate = True
                review_reason = (
                    "Case 9 UoM Scale Mismatch wajib direview manual: pastikan corrected qty/value, standard cost, "
                    "dan holder account sudah disetujui finance sebelum execute."
                )
            hpp_row = item_account_by_code.get(expense_account_code) if expense_account_code else None
            hpp_balance = _round2(sum(hpp_balances_by_code.values()))
            cogs_variance_balance = _round2(sum(variance_balances_by_code.values()))
            bank_balances_by_code = {
                code: _round2(getattr(item_account_by_code.get(code), "net_balance", 0.0))
                for code in bank_account_codes
            }
            problem_active_total = _round2(sum(abs(value) for value in problem_balances_by_code.values()))
            coefficient_denominator = max(
                [abs(problem_active_total), abs(hpp_balance), abs(inventory_balance)],
                default=0.0,
            )
            external_clearing_used = 0.0
            if normalized_pcb_case == "case2":
                planned_lines = self._build_pcb_case2_planned_lines(
                    problem_balances_by_code=problem_balances_by_code,
                    expense_account_code=expense_account_code,
                    expense_account_name=expense_account_name or normalize_text(getattr(hpp_row, "name", "")),
                    hpp_balances_by_code=hpp_balances_by_code,
                    hpp_account_name_by_code=hpp_account_name_by_code,
                    variance_balances_by_code=variance_balances_by_code,
                    variance_account_name_by_code=variance_account_name_by_code,
                )
            elif normalized_pcb_case == "case5":
                planned_lines = self._build_pcb_case5_planned_lines(
                    problem_balances_by_code=problem_balances_by_code,
                    expense_account_code=expense_account_code,
                    expense_account_name=expense_account_name or normalize_text(getattr(hpp_row, "name", "")),
                    basis_amount=repair_basis_amount,
                    valuation_account_code=valuation_account_code,
                    valuation_account_name=valuation_account_name,
                    standard_amount=_round2(standard_price * float(recovery_evidence.get("missing_qty") or 0)),
                )
            elif normalized_pcb_case == "case6":
                expense_balance = _round2(
                    getattr(item_account_by_code.get(expense_account_code), "net_balance", 0.0)
                ) if expense_account_code else 0.0
                planned_lines = self._build_pcb_case6_planned_lines(
                    problem_balances_by_code=problem_balances_by_code,
                    expense_account_code=expense_account_code,
                    expense_account_name=expense_account_name or normalize_text(getattr(hpp_row, "name", "")),
                    expense_balance=expense_balance,
                    basis_amount=repair_basis_amount,
                    valuation_account_code=valuation_account_code,
                    valuation_account_name=valuation_account_name,
                    bill_expense_account_code=expense_account_codes[0] if len(expense_account_codes) == 1 else "",
                    bill_expense_account_name=expense_account_name_by_code.get(expense_account_codes[0], "") if len(expense_account_codes) == 1 else "",
                )
            elif normalized_pcb_case in {"case8a", "case8b"}:
                planned_lines = self._build_pcb_case8_planned_lines(
                    valuation_account_code=valuation_account_code,
                    valuation_account_name=valuation_account_name,
                    expense_account_code=expense_account_code,
                    expense_account_name=expense_account_name or normalize_text(getattr(hpp_row, "name", "")),
                    return_gap=_round2(case8_evidence.get("return_gap")),
                )
            elif normalized_pcb_case == "case9":
                planned_lines = self._build_pcb_case9_planned_lines(
                    holder_basis=normalize_text(case9_evidence.get("holder_basis")),
                    amount=_round2(case9_evidence.get("correction_amount") or case9_evidence.get("value_gap")),
                    hpp_balance=hpp_balance,
                    inventory_balance=inventory_balance,
                    valuation_account_code=valuation_account_code,
                    valuation_account_name=valuation_account_name,
                    expense_account_code=expense_account_code,
                    expense_account_name=expense_account_name or normalize_text(getattr(hpp_row, "name", "")),
                )
            else:
                # Audit-linked clearing 1108099 dipakai sebagai counterpart prioritas ketika
                # item masih punya residual 2103006 dan adjustment audit berhasil menunjuk
                # clearing amount yang relevan. Sisa yang tidak tertutup audit-clearing baru
                # jatuh ke akun HPP target sebagai selisih.
                external_clearing_used = _round2(
                    min(
                        abs(_round2(problem_balances_by_code.get("2103006", 0.0))),
                        abs(audit_linked_clearing_amount),
                    )
                ) if (audit_linked_clearing_amount >= 0.01 and "2103006" in problem_balances_by_code) else 0.0
                planned_lines = self._build_pcb_case34_planned_lines(
                    problem_balances_by_code=problem_balances_by_code,
                    expense_account_code=expense_account_code,
                    expense_account_name=expense_account_name or normalize_text(getattr(hpp_row, "name", "")),
                    hpp_balances_by_code=non_target_hpp_balances_by_code,
                    hpp_account_name_by_code=hpp_account_name_by_code,
                    variance_balances_by_code=variance_balances_by_code,
                    variance_account_name_by_code=variance_account_name_by_code,
                    additional_source_balances_by_code=(
                        {
                            _PCB_ADJUSTMENT_CLEARING_CODE: _round2(
                                -external_clearing_used
                                if _round2(problem_balances_by_code.get("2103006", 0.0)) > 0
                                else external_clearing_used
                            )
                        }
                        if external_clearing_used >= 0.01
                        else None
                    ),
                    additional_source_account_name_by_code={
                        _PCB_ADJUSTMENT_CLEARING_CODE: "Clearing Adjustment"
                    } if external_clearing_used >= 0.01 else None,
                )
            recovery_blockers = []
            if normalized_pcb_case in {"case5", "case6"}:
                bill_line_ids = [int(v) for v in meta.get("bill_line_ids", []) if int(v) > 0]
                if len(bill_line_ids) != 1 or len(meta.get("bill_move_ids", [])) != 1:
                    recovery_blockers.append("Bill source tidak unik; review alokasi per purchase line.")
                if not recovery_evidence.get("complete") or repair_basis_amount <= 0:
                    recovery_blockers.append("Nilai SVL receipt aktual belum tersedia/non-positive.")
                if not valuation_account_code:
                    recovery_blockers.append("Akun valuation kategori belum tersedia.")
                if recovery_evidence.get("has_return"):
                    recovery_blockers.append("Return terkait harus ditelusuri sebelum pemulihan receipt dan alokasi beban.")
                if abs(inventory_balance) >= 0.01:
                    recovery_blockers.append("Saldo inventory existing harus dicocokkan dengan SVL sebelum pemulihan agar tidak berganda.")
                if abs(float(recovery_evidence.get("receipt_qty") or 0)-float(item_row.bill_quantity or 0)) >= 0.01:
                    recovery_blockers.append("Qty receipt dan bill berbeda; review seluruh receipt/return terkait sebelum alokasi beban.")
                if problem_balances_by_code.get("1108099"):
                    recovery_blockers.append("Clearing existing harus ditelusuri agar pemulihan tidak berganda.")
                if normalized_pcb_case == "case6" and len(expense_account_codes) != 1:
                    recovery_blockers.append("Akun expense bill aktual ambigu atau belum tersedia.")
                if recovery_blockers:
                    planned_lines = []
                recovery_evidence = dict(recovery_evidence,
                    standard_amount=_round2(standard_price * float(recovery_evidence.get("missing_qty") or 0)),
                    additional_expense=_round2(problem_balances_by_code.get("2103006", 0)-repair_basis_amount) if normalized_pcb_case == "case5" else 0,
                    bill_expense_account_code=expense_account_codes[0] if len(expense_account_codes) == 1 else "",
                    cost_change_offset=_round2(standard_price * float(recovery_evidence.get("missing_qty") or 0)-repair_basis_amount),
                    bill_standard_gap=_round2(problem_balances_by_code.get("2103006", 0)-standard_price * float(recovery_evidence.get("missing_qty") or 0)) if normalized_pcb_case == "case5" else 0,
                    blockers=recovery_blockers)
            selisih_hpp_amount = _round2(
                next(
                    (
                        float(line.amount or 0.0)
                        for line in planned_lines
                        if normalize_text(line.role) == "selisih_hpp"
                    ),
                    0.0,
                )
            )
            if normalized_pcb_case in {"case5", "case6"}:
                selisih_hpp_amount = abs(_round2(sum(self._pcb_planned_line_signed_amount(line)
                    for line in planned_lines if line.role in {"cost_change_offset", "selisih_hpp"})))
            auto_variance_warning = coefficient_denominator <= 0.01 and abs(selisih_hpp_amount) >= 0.01
            coefficient_variance = (
                _round2((abs(selisih_hpp_amount) / coefficient_denominator) * 100.0)
                if coefficient_denominator > 0.01
                else 0.0
            )
            guard_flags: list[str] = []
            guard_messages: list[str] = []
            if recovery_blockers:
                guard_flags.append("receipt_recovery_blocked")
                guard_messages.extend(recovery_blockers)
            for account_code in _PCB_CASE2_PROBLEM_CODES:
                balance = _round2(problem_balances_by_code.get(account_code, 0.0))
                if abs(balance) < 0.01:
                    continue
                guard_flags.append(f"problem_non_zero:{account_code}")
                guard_messages.append(f"Saldo akun problem {account_code} item masih {balance:+,.2f}.")
            if any(abs(balance) >= 0.01 for balance in hpp_balances_by_code.values()):
                guard_flags.append("hpp_non_zero")
                for account_code, balance in hpp_balances_by_code.items():
                    if abs(balance) < 0.01:
                        continue
                    guard_flags.append(f"hpp_non_zero:{account_code}")
                    guard_messages.append(f"Saldo HPP item ({account_code}) masih {balance:+,.2f}.")
            if abs(inventory_balance) >= 0.01:
                guard_flags.append("inventory_non_zero")
                account_label = valuation_account_code or "akun persediaan item"
                guard_messages.append(f"Saldo persediaan item ({account_label}) masih {inventory_balance:+,.2f}.")
            for code, balance in bank_balances_by_code.items():
                if abs(balance) < 0.01:
                    continue
                guard_flags.append(f"bank_non_zero:{code}")
                guard_messages.append(f"Saldo akun BK/PBK {code} pada item masih {balance:+,.2f}.")
            if auto_variance_warning:
                guard_flags.append("coefficient_variance_auto_warning")
                guard_messages.append(
                    f"Selisih HPP {selisih_hpp_amount:+,.2f} dengan denominator 0.00, perlu dicek manual."
                )
            elif coefficient_variance > 35.0:
                guard_flags.append("coefficient_variance_high")
                guard_messages.append(
                    f"Coefficient Variance {coefficient_variance:,.2f}% melebihi batas 35.00%."
                )
            if normalized_pcb_case in {"case5", "case6"} and repair_basis_amount < 0.01:
                guard_flags.append("missing_repair_basis")
                guard_messages.append(
                    f"Basis repair {repair_basis_source or 'standard_cost_x_qty'} item ini masih 0.00."
                )
            if normalized_pcb_case in {"case8a", "case8b"} and case8_evidence:
                guard_flags.append("case8_review_required")
                guard_messages.append(
                    "Case 8 evidence: "
                    f"return actual {float(case8_evidence.get('actual_return_value') or 0.0):,.2f}, "
                    f"expected {float(case8_evidence.get('expected_return_value') or 0.0):,.2f}, "
                    f"gap {float(case8_evidence.get('return_gap') or 0.0):+,.2f}."
                )
                if bool(case8_evidence.get("ambiguous")):
                    guard_flags.append("case8_ambiguous_bill_value")
                    guard_messages.append("Full return masih punya bill/refund value; row wajib review sebagai relation ambigu.")
            if normalized_pcb_case == "case9" and case9_evidence:
                guard_flags.append("case9_review_required")
                guard_messages.append(
                    "Case 9 evidence: "
                    f"{normalize_text(case9_evidence.get('source_uom_name')) or 'source UoM'} -> "
                    f"{normalize_text(case9_evidence.get('product_uom_name')) or 'product UoM'}, "
                    f"scale {float(case9_evidence.get('scale_factor') or 0.0):,.2f}x, "
                    f"expected {float(case9_evidence.get('expected_value') or 0.0):,.2f}, "
                    f"actual {float(case9_evidence.get('actual_svl_value') or 0.0):,.2f}."
                )
                if normalize_text(case9_evidence.get("holder_basis")).lower() == "review_only":
                    guard_flags.append("case9_holder_ambiguous")
                    guard_messages.append("Holder account Case 9 belum tegas; row review-only sampai target COA dipilih manual.")
            if abs(inventory_balance) >= 0.01 and not valuation_account_code:
                guard_flags.append("missing_inventory_account")
                guard_messages.append("Akun persediaan item dari kategori tidak ditemukan.")
            if any(
                normalize_text(line.role) in {"hpp_zero", "selisih_hpp", "cost_change_offset"}
                and abs(_round2(line.amount)) >= 0.01
                and not normalize_text(line.account_code)
                for line in planned_lines
            ):
                guard_flags.append("missing_expense_account")
                guard_messages.append("Akun HPP/expense item dari kategori tidak ditemukan untuk line Zero HPP / Selisih HPP.")
            if not planned_lines:
                guard_flags.append("zero_planned_lines")
                guard_messages.append("Saldo item tidak menghasilkan planned line setelah rounding.")
            review_required = (
                review_required_candidate
                and "missing_expense_account" not in guard_flags
                and "zero_planned_lines" not in guard_flags
            )
            if not review_required:
                review_reason = ""
            if review_required:
                if normalized_pcb_case in {"case3", "case4"}:
                    guard_flags.append("review_required_case34_bill_only")
                elif normalized_pcb_case in {"case8a", "case8b", "case9"}:
                    guard_flags.append(f"review_required_{normalized_pcb_case}")
                else:
                    guard_flags.append("review_required_basis_manual")
                if review_reason and review_reason not in guard_messages:
                    guard_messages.insert(0, review_reason)
            bill_names = sorted({normalize_text(value) for value in list(meta.get("bill_names") or []) if normalize_text(value)})
            po_name_candidates = sorted({normalize_text(value) for value in list(meta.get("po_names") or []) if normalize_text(value)})
            display_bill_name = bill_names[0] if bill_names else (fallback_bill_ref_names[0] if fallback_bill_ref_names else "")
            display_po_name = po_name_candidates[0] if po_name_candidates else (po_names[0] if po_names else "")
            row_partner_name = normalize_text(partner_name) or fallback_partner_name
            if not row_partner_name:
                row_partner_name = next(
                    (
                        _many2one_name((move_info_map.get(bill_move_id) or {}).get("partner_id"))
                        or _many2one_name((bill_rows_by_id.get(bill_move_id) or {}).get("partner_id"))
                        for bill_move_id in sorted(
                            int(value or 0)
                            for value in list(meta.get("bill_move_ids") or [])
                            if int(value or 0) > 0
                        )
                        if (
                            _many2one_name((move_info_map.get(bill_move_id) or {}).get("partner_id"))
                            or _many2one_name((bill_rows_by_id.get(bill_move_id) or {}).get("partner_id"))
                        )
                    ),
                    "",
                )
            relation_fields = self._resolve_pcb_case2_item_relations(
                item_row=item_row,
                product_id=int(product_id or 0),
                picking_ids=list(picking_ids or [picking_id]),
                bill_move_ids=sorted(
                    {
                        int(value or 0)
                        for value in list(meta.get("bill_move_ids") or getattr(item_row, "bill_move_ids", None) or [])
                        if int(value or 0) > 0
                    }
                ),
                raw_lines=list(raw_lines or []),
                stock_move_rows_by_id=stock_move_rows_by_id,
                lines_by_move=lines_by_move,
                move_info_map=move_info_map,
                purchase_line_product_map=purchase_line_product_map,
            )
            if normalized_pcb_case in {"case5", "case6"} and len(meta.get("bill_line_ids", [])) == 1:
                relation_fields["bill_line_id"] = next(iter(meta["bill_line_ids"]))
            positive_bill_move_ids = sorted(
                {
                    int(value or 0)
                    for value in list(meta.get("bill_move_ids") or [])
                    if int(value or 0) > 0
                }
            )
            row_bill_move_id = positive_bill_move_ids[0] if positive_bill_move_ids else 0
            row_partner_id = int(meta.get("partner_id") or fallback_partner_id or partner_id or 0)
            row_payment_move_ids = sorted(
                cycle_payment_ids
                & {
                    int(value or 0)
                    for value in list(payment_move_ids_by_product.get(int(product_id or 0), []) or [])
                    if int(value or 0) > 0
                }
            )
            row_bank_move_ids = sorted(
                cycle_bank_ids
                & {
                    int(value or 0)
                    for value in list(bank_move_ids_by_product.get(int(product_id or 0), []) or [])
                    if int(value or 0) > 0
                }
            )
            economic_fields = self._build_pcb_case2_economic_fields(
                bill_line_id=int(relation_fields.get("bill_line_id") or 0),
                bill_move_ids=positive_bill_move_ids,
                stock_move_id=int(relation_fields.get("stock_move_id") or 0),
                lines_by_move=lines_by_move,
                stock_move_rows_by_id=stock_move_rows_by_id,
            )
            row_suspend_target_aml_ids: list[int] = []
            row_clearing_target_aml_ids: list[int] = []
            if normalized_pcb_case in {"case3", "case4"} and cycle_move_ids:
                row_suspend_target_aml_ids, row_clearing_target_aml_ids = self._match_pcb_case1_target_line_ids(
                    all_cycle_move_ids=cycle_move_ids,
                    stj_move_ids=[
                        int(value or 0)
                        for value in list(relation_fields.get("stj_move_ids") or [])
                        if int(value or 0) > 0
                    ],
                    lines_by_move=lines_by_move,
                    account_info_map=account_info_map,
                    product_id=int(product_id or 0),
                    purchase_line_id=int(relation_fields.get("purchase_line_id") or 0),
                    bill_move_id=row_bill_move_id,
                    stock_move_id=int(relation_fields.get("stock_move_id") or 0),
                    partner_id=row_partner_id,
                )
            amount = max(
                [
                    *[abs(_round2(line.amount)) for line in planned_lines],
                    abs(_round2(repair_basis_amount)),
                    abs(_round2(case8_evidence.get("correction_amount") or case8_evidence.get("return_gap"))),
                    abs(_round2(case9_evidence.get("correction_amount") or case9_evidence.get("value_gap"))),
                ],
                default=0.0,
            )
            results.append(
                SvlDashboardPcbCase2RepairRow(
                    row_key=f"{normalized_pcb_case}::{int(picking_id or 0)}::{int(product_id or 0)}",
                    cycle_key=f"{normalized_pcb_case}::{int(picking_id or 0)}",
                    company_id=0,
                    company_name="",
                    amount=amount,
                    date=normalize_text(gr_date)[:10],
                    reference="",
                    line_label="",
                    journal_code=DEFAULT_JOURNAL_CODE,
                    pcb_case=normalized_pcb_case,
                    pcb_case_label=self._pcb_multi_line_case_label(normalized_pcb_case),
                    product_id=int(product_id or 0),
                    item_code=normalize_text(product_info.get("default_code")) or normalize_text(getattr(item_row, "default_code", "")),
                    item_name=normalize_text(product_info.get("name")) or normalize_text(getattr(item_row, "product_name", "")) or f"Product #{product_id}",
                    item_category_name=normalize_text(product_info.get("categ_name")),
                    bill_line_id=int(relation_fields.get("bill_line_id") or 0),
                    purchase_line_id=int(relation_fields.get("purchase_line_id") or 0),
                    stock_move_id=int(relation_fields.get("stock_move_id") or 0),
                    stock_move_ids=list(relation_fields.get("stock_move_ids") or []),
                    stj_move_ids=list(relation_fields.get("stj_move_ids") or []),
                    stj_refs=list(relation_fields.get("stj_refs") or []),
                    picking_id=int(picking_id or 0),
                    picking_name=normalize_text(picking_name),
                    po_name=display_po_name,
                    bill_move_id=row_bill_move_id,
                    bill_name=display_bill_name,
                    partner_id=row_partner_id,
                    partner_name=row_partner_name,
                    payment_move_ids=row_payment_move_ids,
                    bank_move_ids=row_bank_move_ids,
                    suspend_target_aml_ids=row_suspend_target_aml_ids,
                    clearing_target_aml_ids=row_clearing_target_aml_ids,
                    product_uom_id=int(economic_fields.get("product_uom_id") or 0),
                    quantity=float(economic_fields.get("quantity") or 0.0),
                    currency_id=int(economic_fields.get("currency_id") or 0),
                    amount_currency=float(economic_fields.get("amount_currency") or 0.0),
                    amount_currency_basis=float(economic_fields.get("amount_currency_basis") or 0.0),
                    analytic_distribution=economic_fields.get("analytic_distribution", False),
                    bill_price_unit=float(economic_fields.get("bill_price_unit") or 0.0),
                    gr_price_unit=float(economic_fields.get("gr_price_unit") or 0.0),
                    price_gap_value=float(economic_fields.get("price_gap_value") or 0.0),
                    allocated_amount=float(economic_fields.get("allocated_amount") or 0.0),
                    suspend_account_code="2103006",
                    inventory_account_code=valuation_account_code,
                    expense_account_code=expense_account_code,
                    repair_basis_amount=repair_basis_amount,
                    repair_basis_source=repair_basis_source,
                    standard_price=standard_price,
                    problem_balances_by_code=problem_balances_by_code,
                    hpp_balances_by_code=hpp_balances_by_code,
                    suspend_balance=_round2(problem_balances_by_code.get("2103006", 0.0)),
                    hpp_balance=hpp_balance,
                    inventory_balance=inventory_balance,
                    cogs_variance_balance=cogs_variance_balance,
                    selisih_hpp_amount=selisih_hpp_amount,
                    external_clearing_amount=(
                        external_clearing_used
                        if normalized_pcb_case in {"case3", "case4"}
                        else (
                            abs(_round2(case9_evidence.get("correction_amount") or 0.0))
                            if normalized_pcb_case == "case9"
                            and normalize_text(case9_evidence.get("holder_basis")).lower() == "case2_downstream_clearing"
                            else 0.0
                        )
                    ),
                    external_clearing_refs=external_clearing_refs,
                    external_clearing_basis=external_clearing_basis,
                    external_clearing_verified=bool(
                        external_clearing_verified
                        and (
                            external_clearing_used
                            if normalized_pcb_case in {"case3", "case4"}
                            else (
                                abs(_round2(case9_evidence.get("correction_amount") or 0.0))
                                if normalized_pcb_case == "case9"
                                and normalize_text(case9_evidence.get("holder_basis")).lower() == "case2_downstream_clearing"
                                else 0.0
                            )
                        )
                        >= 0.01
                    ),
                    coefficient_variance=coefficient_variance,
                    bank_balances_by_code=bank_balances_by_code,
                    bank_account_codes=bank_account_codes,
                    guard_flags=guard_flags,
                    guard_messages=guard_messages,
                    review_required=review_required,
                    review_confirmed=False,
                    review_reason=review_reason,
                    planned_lines=planned_lines,
                    case_evidence=case8_evidence if normalized_pcb_case in {"case8a", "case8b"} else case9_evidence if normalized_pcb_case == "case9" else recovery_evidence if normalized_pcb_case in {"case5", "case6"} else {},
                )
            )
        return results

    @staticmethod
    def _classify_partial_cycle_group(
        *,
        bill_move_ids: list[int],
        bill_rows_by_id: dict[int, dict[str, Any]],
        payment_move_ids: list[int],
        bank_move_ids: list[int],
        has_info_accounts: bool = False,
    ) -> tuple[str, str]:
        bill_move_ids = SvlDashboardServiceAsync._filter_vendor_bill_move_ids(
            bill_move_ids,
            bill_rows_by_id=bill_rows_by_id,
        )
        unpaid_bills = [
            normalize_text((bill_rows_by_id.get(bid) or {}).get("name"))
            for bid in list(bill_move_ids or [])
            if normalize_text((bill_rows_by_id.get(bid) or {}).get("payment_state")) in ("not_paid", "partial")
        ]
        if unpaid_bills:
            return ("bill_unpaid", "Bill Belum Paid")
        if bill_move_ids and not payment_move_ids:
            return ("bill_unmatched", "Bill Belum Matching")
        if payment_move_ids and not bank_move_ids:
            return ("payment_unreconciled", "Payment Belum Reconcile")
        if has_info_accounts:
            return ("partial_info", "Info Account / Variance")
        return ("partial_other", "Partial Lainnya")

    @staticmethod
    def _detect_cycle_patterns(
        *,
        account_rows: list[SvlDashboardCycleAccountRow],
        bill_move_ids: list[int],
        bill_rows_by_id: dict[int, dict[str, Any]],
        payment_move_ids: list[int],
        bank_move_ids: list[int],
    ) -> tuple[list[str], list[str]]:
        """Detect known problem patterns (Langkah 7a).

        Returns (warning_patterns, info_patterns).
        warning_patterns affect cycle_status (partial/problem).
        info_patterns are logged/displayed but do NOT cause 'partial'.
        """
        patterns: list[str] = []
        info_patterns: list[str] = []
        by_code = {r.code: r for r in account_rows}
        bill_move_ids = SvlDashboardServiceAsync._filter_vendor_bill_move_ids(
            bill_move_ids,
            bill_rows_by_id=bill_rows_by_id,
        )

        # Pola 1 — Clearing tidak match ke Hutang Suspensed
        clearing = by_code.get("1108099")
        suspensed = by_code.get("2103006")
        if clearing and suspensed:
            if abs(clearing.net_balance) > 0.01 and abs(suspensed.net_balance) > 0.01:
                patterns.append("Pola 1: Clearing (1108099) tidak offset ke Hutang Suspensed (2103006) — kemungkinan STJ salah akun kredit")

        # Pola 2 — STJ ada, Bill tidak ada (informational; tidak menyebabkan partial)
        if not bill_move_ids:
            stj_codes = {"1108099", "2103006"}
            if any(r.code in stj_codes and abs(r.net_balance) > 0.01 for r in account_rows):
                info_patterns.append("Pola 2: STJ terbentuk tetapi Bill belum ada / belum terhubung ke PO ini")

        # Pola 3 — Payment chain belum lengkap: partial, bukan masalah repair JE.
        if bill_move_ids and not payment_move_ids:
            all_bills_paid = all(
                normalize_text((bill_rows_by_id.get(bid) or {}).get("payment_state")) in ("paid", "in_payment")
                for bid in bill_move_ids
            )
            if all_bills_paid:
                patterns.append(
                    "Pola 3B: Bill sudah paid/in_payment, tetapi belum ter-match ke Payment terkait"
                )
            else:
                patterns.append("Pola 3A: Bill sudah posted tetapi belum ada Payment terkait / belum paid")
        elif bill_move_ids and payment_move_ids:
            unpaid = [
                normalize_text((bill_rows_by_id.get(bid) or {}).get("name"))
                for bid in bill_move_ids
                if normalize_text((bill_rows_by_id.get(bid) or {}).get("payment_state")) in ("not_paid", "partial")
            ]
            if unpaid:
                patterns.append(f"Pola 3A: Bill belum lunas ({', '.join(filter(None, unpaid))})")

        # Pola 4 — Payment ada, Bank JE belum terhubung: cycle masih partial.
        if payment_move_ids and not bank_move_ids:
            patterns.append("Pola 4: Payment (PBK) ada tetapi Bank JE (BK) belum ter-reconcile")

        # Pola 5 — COGS Variance bersaldo tidak nol (INFORMATIONAL; tidak menyebabkan 'partial')
        cogs_variance = by_code.get("5101010")
        if cogs_variance and abs(cogs_variance.net_balance) > 0.01:
            info_patterns.append(f"Pola 5 [info]: Selisih HPP/COGS Variance bersaldo {cogs_variance.net_balance:,.2f} — perbedaan harga GR vs Bill (normal)")

        return patterns, info_patterns


    @staticmethod
    def _normalize_detail_product_ids(product_ids: list[int] | tuple[int, ...]) -> tuple[list[int], bool]:
        normalized: list[int] = []
        include_unassigned = False
        seen: set[int] = set()
        for raw_product_id in product_ids:
            pid = int(raw_product_id or 0)
            if pid == _UNASSIGNED_JOURNAL_PID:
                include_unassigned = True
                continue
            if pid <= 0:
                raise ValueError("Product ID detail dashboard tidak valid.")
            if pid in seen:
                continue
            seen.add(pid)
            normalized.append(pid)
        return normalized, include_unassigned

    async def fetch_many_item_details(
        self,
        request: SvlDashboardRequest,
        product_ids: list[int] | tuple[int, ...],
    ) -> dict[int, SvlDashboardItemDetail]:
        await self.rpc.ensure_login()
        date_from = _normalize_date_filter(request.date_from)
        date_to = _normalize_date_filter(request.date_to)
        if date_from and date_to and date_from > date_to:
            raise ValueError("Date From tidak boleh lebih besar dari Date To.")

        normalized_product_ids, include_unassigned = self._normalize_detail_product_ids(tuple(product_ids or ()))
        if not normalized_product_ids and not include_unassigned:
            return {}

        context = build_company_context(request.company_id)
        capabilities = await self._detect_capabilities()
        warnings: list[str] = []

        valuation_account_ids = await self._detect_valuation_accounts(
            company_id=request.company_id,
            context=context,
            account_company_field=capabilities["account_company_field"],
            account_fields=capabilities["account_fields"],
            category_fields=capabilities["category_fields"],
        )
        svl_detail: dict[int, list[Any]] = {}
        journal_detail: dict[int, list[Any]] = {}
        po_bill_by_product: dict[int, dict[str, Any]] = {}
        unassigned_detail: SvlDashboardItemDetail | None = None

        tasks: list[asyncio.Future[Any] | asyncio.Task[Any] | Any] = []
        if normalized_product_ids:
            tasks.extend(
                [
                    self._fetch_svl_detail(
                        product_ids=list(normalized_product_ids),
                        company_id=request.company_id,
                        context=context,
                        date_from=date_from,
                        date_to=date_to,
                        svl_date_field=capabilities["svl_date_field"],
                        link_supported=capabilities["svl_link_supported"],
                        move_link_supported=capabilities["svl_move_link_supported"],
                        warnings=warnings,
                        move_fields=capabilities["stock_move_fields"],
                    ),
                    self._fetch_journal_detail(
                        product_ids=list(normalized_product_ids),
                        company_id=request.company_id,
                        context=context,
                        date_from=date_from,
                        date_to=date_to,
                        valuation_account_ids=valuation_account_ids,
                        aml_fields=capabilities["aml_fields"],
                        link_supported=capabilities["svl_link_supported"],
                        warnings=warnings,
                    ),
                    self._fetch_po_bill_comparison(
                        product_ids=list(normalized_product_ids),
                        company_id=request.company_id,
                        context=context,
                        aml_fields=capabilities["aml_fields"],
                        warnings=warnings,
                        include_lines=True,
                    ),
                ]
            )
        if include_unassigned:
            tasks.append(
                self._fetch_unassigned_item_detail(
                    request=request,
                    date_from=date_from,
                    date_to=date_to,
                )
            )
        if tasks:
            results = await asyncio.gather(*tasks)
        else:
            results = []

        result_index = 0
        if normalized_product_ids:
            svl_detail = results[result_index]
            journal_detail = results[result_index + 1]
            po_bill_by_product = results[result_index + 2]
            result_index += 3
        if include_unassigned:
            unassigned_detail = results[result_index]

        details_by_product_id: dict[int, SvlDashboardItemDetail] = {}
        for pid in normalized_product_ids:
            po_bill = po_bill_by_product.get(pid, {})
            details_by_product_id[pid] = SvlDashboardItemDetail(
                pid=pid,
                item_kind="product",
                po_line_count=int(po_bill.get("po_line_count", 0) or 0),
                bill_line_count=int(po_bill.get("bill_line_count", 0) or 0),
                total_po_value=_round2(po_bill.get("total_po_value", 0.0)),
                total_bill_value=_round2(po_bill.get("total_bill_value", 0.0)),
                svl_records=list(svl_detail.get(pid, [])),
                jnl_records=list(journal_detail.get(pid, [])),
                po_lines=list(po_bill.get("po_lines", [])),
                bill_lines=list(po_bill.get("bill_lines", [])),
            )
        if include_unassigned and unassigned_detail is not None:
            details_by_product_id[_UNASSIGNED_JOURNAL_PID] = unassigned_detail
        return details_by_product_id

    async def fetch_item_detail(self, request: SvlDashboardRequest, product_id: int) -> SvlDashboardItemDetail:
        pid = int(product_id or 0)
        details_by_product_id = await self.fetch_many_item_details(request, [pid])
        detail = details_by_product_id.get(pid)
        if detail is None:
            raise ValueError("Product ID detail dashboard tidak valid.")
        return detail

    async def hydrate_snapshot_details(
        self,
        request: SvlDashboardRequest,
        snapshot: SvlDashboardSnapshot,
        product_ids: list[int] | None = None,
    ) -> SvlDashboardSnapshot:
        wanted_ids = {int(pid or 0) for pid in (product_ids or []) if int(pid or 0) != 0}
        detail_ids = list(wanted_ids) if wanted_ids else [int(item.pid or 0) for item in snapshot.items]
        details_by_product_id = await self.fetch_many_item_details(request, detail_ids)
        hydrated_items: list[SvlDashboardItem] = []
        for item in snapshot.items:
            pid = int(item.pid or 0)
            if wanted_ids and pid not in wanted_ids:
                hydrated_items.append(replace(item))
                continue
            detail = details_by_product_id.get(pid)
            if detail is None:
                hydrated_items.append(replace(item))
                continue
            hydrated_items.append(
                replace(
                    item,
                    svl_records=list(detail.svl_records),
                    jnl_records=list(detail.jnl_records),
                    po_line_count=int(detail.po_line_count or 0),
                    bill_line_count=int(detail.bill_line_count or 0),
                    po_lines=list(detail.po_lines),
                    bill_lines=list(detail.bill_lines),
                    total_po_value=_round2(detail.total_po_value),
                    total_bill_value=_round2(detail.total_bill_value),
                    payable_line_count=int(detail.payable_line_count or 0),
                    total_payable_value=_round2(detail.total_payable_value),
                    payable_lines=list(detail.payable_lines),
                    total_paid_value=_round2(detail.total_paid_value),
                    total_unassigned_bill_remainder=_round2(detail.total_unassigned_bill_remainder),
                    payment_status=normalize_text(detail.payment_status),
                    unassigned_bill_lines=list(detail.unassigned_bill_lines),
                )
            )
        return replace(snapshot, items=hydrated_items)

    async def _fetch_unassigned_item_detail(
        self,
        *,
        request: SvlDashboardRequest,
        date_from: str,
        date_to: str,
    ) -> SvlDashboardItemDetail:
        context = build_company_context(request.company_id)
        capabilities = await self._detect_capabilities()
        warnings: list[str] = []
        valuation_account_ids = await self._detect_valuation_accounts(
            company_id=request.company_id,
            context=context,
            account_company_field=capabilities["account_company_field"],
            account_fields=capabilities["account_fields"],
            category_fields=capabilities["category_fields"],
        )
        records = await self._fetch_unassigned_journal_detail(
            company_id=request.company_id,
            context=context,
            date_from=date_from,
            date_to=date_to,
            valuation_account_ids=valuation_account_ids,
            aml_fields=capabilities["aml_fields"],
            link_supported=capabilities["svl_link_supported"],
            warnings=warnings,
        )
        return SvlDashboardItemDetail(
            pid=_UNASSIGNED_JOURNAL_PID,
            item_kind="unassigned_journal",
            po_line_count=0,
            bill_line_count=0,
            total_po_value=0.0,
            total_bill_value=0.0,
            svl_records=[],
            jnl_records=records,
            po_lines=[],
            bill_lines=[],
        )

    async def _fetch_company_name(self, company_id: int, *, context: dict[str, Any]) -> str:
        rows = await self.rpc.read(
            "res.company",
            [company_id],
            fields=["name"],
            context=context,
            stage="SVL_DASH_COMPANY",
        )
        if rows:
            return normalize_text(rows[0].get("name")) or f"Company #{company_id}"
        return f"Company #{company_id}"

    async def _detect_capabilities(self) -> dict[str, Any]:
        svl_fields = await self.rpc.fields_get(
            "stock.valuation.layer",
            attributes=["type", "readonly"],
            stage="SVL_DASH_SVL_FIELDS",
        )
        aml_fields = await self.rpc.fields_get(
            "account.move.line",
            attributes=["type", "readonly"],
            stage="SVL_DASH_AML_FIELDS",
        )
        account_fields = await self.rpc.fields_get(
            "account.account",
            attributes=["type", "readonly"],
            stage="SVL_DASH_ACCOUNT_FIELDS",
        )
        category_fields = await self.rpc.fields_get(
            "product.category",
            attributes=["type", "readonly"],
            stage="SVL_DASH_CATEGORY_FIELDS",
        )
        try:
            stock_move_fields = await self.rpc.fields_get(
                "stock.move",
                attributes=["type", "readonly"],
                stage="SVL_DASH_STOCK_MOVE_FIELDS",
            )
        except Exception:  # noqa: BLE001
            stock_move_fields = {}
        return {
            "svl_fields": svl_fields,
            "aml_fields": aml_fields,
            "account_fields": account_fields,
            "category_fields": category_fields,
            "stock_move_fields": stock_move_fields,
            "account_company_field": await detect_account_company_field(self.rpc),
            "svl_link_supported": "account_move_id" in svl_fields,
            "svl_move_link_supported": "stock_move_id" in svl_fields,
            "svl_date_field": self._select_svl_date_field(svl_fields),
        }

    @staticmethod
    def _select_svl_date_field(fields: dict[str, Any]) -> str:
        for field_name in ("accounting_date", "date", "create_date"):
            if field_name in fields:
                return field_name
        return "create_date"

    def _build_svl_date_domain(
        self,
        *,
        field_name: str,
        date_from: str,
        date_to: str,
    ) -> list[Any]:
        domain: list[Any] = []
        if field_name == "create_date":
            if date_from:
                domain.append((field_name, ">=", f"{date_from} 00:00:00"))
            if date_to:
                domain.append((field_name, "<=", f"{date_to} 23:59:59"))
            return domain
        if date_from:
            domain.append((field_name, ">=", date_from))
        if date_to:
            domain.append((field_name, "<=", date_to))
        return domain

    @staticmethod
    def _build_account_move_date_domain(*, date_from: str, date_to: str) -> list[Any]:
        domain: list[Any] = []
        if date_from:
            domain.append(("account_move_id.date", ">=", date_from))
        if date_to:
            domain.append(("account_move_id.date", "<=", date_to))
        return domain

    @staticmethod
    def _should_use_svl_business_date_fallback(
        *,
        svl_date_field: str,
        link_supported: bool,
        date_from: str,
        date_to: str,
    ) -> bool:
        return bool((date_from or date_to) and svl_date_field == "create_date" and link_supported)

    @staticmethod
    def _build_posted_domain(aml_fields: dict[str, Any]) -> list[Any]:
        if "parent_state" in aml_fields:
            return [("parent_state", "=", "posted")]
        return [("move_id.state", "=", "posted")]

    @staticmethod
    def _build_account_company_domain(field_name: str, company_id: int) -> list[Any]:
        if not field_name or company_id <= 0:
            return []
        if field_name == "company_ids":
            return [(field_name, "in", [company_id])]
        return [(field_name, "=", company_id)]

    @staticmethod
    def _account_company_field_candidates(account_fields: dict[str, Any], preferred_field: str = "") -> list[str]:
        ordered: list[str] = []
        for field_name in ("company_id", preferred_field, "company_ids", ""):
            if field_name in ordered:
                continue
            if field_name and field_name not in account_fields:
                continue
            ordered.append(field_name)
        return ordered or [""]

    @staticmethod
    def _build_current_asset_display_type_domain(aml_fields: dict[str, Any]) -> list[Any]:
        if "display_type" not in aml_fields:
            return []
        return [
            ("display_type", "!=", "line_section"),
            ("display_type", "!=", "line_note"),
        ]

    async def _search_account_rows_for_company(
        self,
        *,
        company_id: int,
        context: dict[str, Any],
        account_fields: dict[str, Any],
        preferred_field: str,
        extra_domain: list[Any],
        fields: list[str],
        stage: str,
        limit: int | None = None,
    ) -> tuple[list[dict[str, Any]], str]:
        candidate_fields = self._account_company_field_candidates(account_fields, preferred_field)
        attempt_logs: list[str] = []
        for field_name in candidate_fields:
            domain = self._build_account_company_domain(field_name, company_id) + list(extra_domain)
            rows = await self.rpc.search_read(
                "account.account",
                domain,
                fields=fields,
                limit=limit,
                context=context,
                stage=stage,
            )
            attempt_logs.append(f"{field_name or '<none>'}:{len(rows)}")
            if rows:
                self._log(
                    f"{stage} menggunakan filter account company '{field_name or '<none>'}' "
                    f"dan mengembalikan {len(rows)} row(s). Percobaan: {', '.join(attempt_logs)}"
                )
                return rows, field_name
        self._log(f"{stage} tidak menemukan row account. Percobaan: {', '.join(attempt_logs) or '<none>'}")
        return [], candidate_fields[0]


    async def _detect_payable_account_ids(
        self,
        *,
        company_id: int,
        context: dict[str, Any],
        account_company_field: str,
        account_fields: dict[str, Any],
        warnings: list[str],
    ) -> list[int]:
        fields = ["id", "code", "name"]
        if "account_type" in account_fields:
            rows, chosen_field = await self._search_account_rows_for_company(
                company_id=company_id,
                context=context,
                account_fields=account_fields,
                preferred_field=account_company_field,
                extra_domain=[("account_type", "=", "liability_payable")],
                fields=fields,
                stage="SVL_DASH_PAYABLE_ACCOUNTS",
            )
            result = sorted({int(row.get("id") or 0) for row in rows if int(row.get("id") or 0) > 0})
            self._log(f"Akun payable terdeteksi ({chosen_field or '<none>'}): {result}")
            return result
        if "internal_group" in account_fields:
            rows, chosen_field = await self._search_account_rows_for_company(
                company_id=company_id,
                context=context,
                account_fields=account_fields,
                preferred_field=account_company_field,
                extra_domain=[],
                fields=fields + ["internal_group"],
                stage="SVL_DASH_PAYABLE_ACCOUNTS_FALLBACK",
            )
            result = sorted(
                {
                    int(row.get("id") or 0)
                    for row in rows
                    if int(row.get("id") or 0) > 0 and normalize_text(row.get("internal_group")) == "liability"
                }
            )
            if result:
                self._warn_once(
                    warnings,
                    "Field account_type tidak tersedia pada account.account; akun payable difallback ke internal_group = liability.",
                )
                self._log(f"Akun payable fallback internal_group ({chosen_field or '<none>'}): {result}")
            return result
        return []


    async def _detect_valuation_accounts(
        self,
        *,
        company_id: int,
        context: dict[str, Any],
        account_company_field: str,
        account_fields: dict[str, Any],
        category_fields: dict[str, Any],
    ) -> list[int]:
        self._log("Mendeteksi akun valuasi persediaan...")
        account_ids: set[int] = set()
        category_account_fields = sorted(
            {
                field_name
                for field_name, meta in category_fields.items()
                if meta.get("type") == "many2one" and "valuation" in field_name and "account" in field_name
            }
            | ({"property_stock_valuation_account_id"} if "property_stock_valuation_account_id" in category_fields else set())
        )
        category_read_fields = ["name"]
        category_read_fields.extend(category_account_fields)
        categories = await self.rpc.search_read(
            "product.category",
            [("property_valuation", "=", "real_time")],
            fields=category_read_fields,
            context=context,
            stage="SVL_DASH_VALUATION_CATEGORIES",
        )
        for category in categories:
            for field_name in category_account_fields:
                account_id = _many2one_id(category.get(field_name))
                if account_id > 0:
                    account_ids.add(account_id)

        if account_ids:
            detected = sorted(account_ids)
            self._log(f"Akun valuasi terdeteksi dari category: {detected}")
            return detected

        fields = ["id", "code", "name"]
        if "account_type" in account_fields:
            fields.append("account_type")
        accounts, chosen_field = await self._search_account_rows_for_company(
            company_id=company_id,
            context=context,
            account_fields=account_fields,
            preferred_field=account_company_field,
            extra_domain=[],
            fields=fields,
            limit=300,
            stage="SVL_DASH_VALUATION_ACCOUNTS_FALLBACK",
        )
        for account in accounts:
            code = normalize_text(account.get("code"))
            name = normalize_text(account.get("name")).lower()
            if code.startswith("114") or any(token in name for token in _VALUATION_NAME_TOKENS):
                account_id = int(account.get("id") or 0)
                if account_id > 0:
                    account_ids.add(account_id)
        detected = sorted(account_ids)
        self._log(f"Akun valuasi fallback ({chosen_field or '<none>'}): {detected}")
        return detected

    async def _fetch_company_summary(
        self,
        *,
        company_id: int,
        context: dict[str, Any],
        date_from: str,
        date_to: str,
        svl_date_field: str,
        link_supported: bool,
        warnings: list[str],
        inventory_coa_codes: list[str],
        account_company_field: str,
        aml_fields: dict[str, Any],
    ) -> SvlDashboardCompanySummary:
        total_svl_value, total_svl_qty = await self._fetch_company_svl_totals(
            company_id=company_id,
            context=context,
            date_from=date_from,
            date_to=date_to,
            svl_date_field=svl_date_field,
            link_supported=link_supported,
            warnings=warnings,
        )
        coa_rows, missing_codes = await self._fetch_company_inventory_coa_rows(
            company_id=company_id,
            context=context,
            date_from=date_from,
            date_to=date_to,
            inventory_coa_codes=inventory_coa_codes,
            account_company_field=account_company_field,
            aml_fields=aml_fields,
            warnings=warnings,
        )
        inventory_bs_total = _round2(sum(row.balance for row in coa_rows))
        return SvlDashboardCompanySummary(
            total_svl_value=_round2(total_svl_value),
            total_svl_qty=_round2(total_svl_qty),
            inventory_bs_total=inventory_bs_total,
            difference=_round2(total_svl_value - inventory_bs_total),
            coa_rows=coa_rows,
            missing_codes=missing_codes,
        )

    async def _fetch_company_svl_totals(
        self,
        *,
        company_id: int,
        context: dict[str, Any],
        date_from: str,
        date_to: str,
        svl_date_field: str,
        link_supported: bool,
        warnings: list[str],
    ) -> tuple[float, float]:
        base_domain: list[Any] = [("company_id", "=", company_id)]
        rows: list[dict[str, Any]]
        if self._should_use_svl_business_date_fallback(
            svl_date_field=svl_date_field,
            link_supported=link_supported,
            date_from=date_from,
            date_to=date_to,
        ):
            try:
                linked_rows = await self.rpc.read_group(
                    "stock.valuation.layer",
                    base_domain
                    + [("account_move_id", "!=", False)]
                    + self._build_account_move_date_domain(date_from=date_from, date_to=date_to),
                    fields=["company_id", "value", "quantity"],
                    groupby=["company_id"],
                    context=context,
                    stage="SVL_DASH_COMPANY_TOTALS_LINKED",
                )
                unlinked_rows = await self.rpc.read_group(
                    "stock.valuation.layer",
                    base_domain
                    + [("account_move_id", "=", False)]
                    + self._build_svl_date_domain(field_name="create_date", date_from=date_from, date_to=date_to),
                    fields=["company_id", "value", "quantity"],
                    groupby=["company_id"],
                    context=context,
                    stage="SVL_DASH_COMPANY_TOTALS_UNLINKED",
                )
                rows = list(linked_rows) + list(unlinked_rows)
            except Exception:  # noqa: BLE001
                self._warn_once(
                    warnings,
                    "Gagal memakai account_move_id.date untuk summary total SVL; fallback ke create_date.",
                )
                rows = await self.rpc.read_group(
                    "stock.valuation.layer",
                    base_domain + self._build_svl_date_domain(field_name=svl_date_field, date_from=date_from, date_to=date_to),
                    fields=["company_id", "value", "quantity"],
                    groupby=["company_id"],
                    context=context,
                    stage="SVL_DASH_COMPANY_TOTALS",
                )
        else:
            rows = await self.rpc.read_group(
                "stock.valuation.layer",
                base_domain + self._build_svl_date_domain(field_name=svl_date_field, date_from=date_from, date_to=date_to),
                fields=["company_id", "value", "quantity"],
                groupby=["company_id"],
                context=context,
                stage="SVL_DASH_COMPANY_TOTALS",
            )
        total_value = 0.0
        total_qty = 0.0
        for row in rows:
            total_value += to_float(row.get("value"))
            total_qty += to_float(row.get("quantity"))
        return _round2(total_value), _round2(total_qty)

    async def _fetch_company_inventory_coa_rows(
        self,
        *,
        company_id: int,
        context: dict[str, Any],
        date_from: str,
        date_to: str,
        inventory_coa_codes: list[str],
        account_company_field: str,
        aml_fields: dict[str, Any],
        warnings: list[str],
    ) -> tuple[list[SvlDashboardInventoryCoaRow], list[str]]:
        normalized_codes = _normalize_inventory_coa_codes(inventory_coa_codes)
        if not normalized_codes:
            self._warn_once(
                warnings,
                "Daftar COA persediaan manual kosong; summary Balance Sheet persediaan dihitung 0.00 sampai Settings diisi.",
            )
            return [], []

        account_domain = self._build_account_company_domain(account_company_field, company_id)
        account_domain.append(("code", "in", normalized_codes))
        account_rows = await self.rpc.search_read(
            "account.account",
            account_domain,
            fields=["code", "name"],
            context=context,
            stage="SVL_DASH_COMPANY_COA_ACCOUNTS",
        )
        accounts_by_code: dict[str, list[dict[str, Any]]] = defaultdict(list)
        account_ids: list[int] = []
        for row in account_rows:
            account_id = int(row.get("id") or 0)
            code = normalize_text(row.get("code")).strip().upper()
            if account_id <= 0 or not code:
                continue
            accounts_by_code[code].append(row)
            account_ids.append(account_id)

        balances_by_account_id: dict[int, float] = {}
        if account_ids:
            aml_domain: list[Any] = [("company_id", "=", company_id), ("account_id", "in", sorted(set(account_ids)))]
            aml_domain.extend(self._build_posted_domain(aml_fields))
            if date_from:
                aml_domain.append(("date", ">=", date_from))
            if date_to:
                aml_domain.append(("date", "<=", date_to))
            balance_rows = await self.rpc.read_group(
                "account.move.line",
                aml_domain,
                fields=["account_id", "debit", "credit"],
                groupby=["account_id"],
                context=context,
                stage="SVL_DASH_COMPANY_COA_BALANCES",
            )
            for row in balance_rows:
                account_id = _many2one_id(row.get("account_id"))
                if account_id <= 0:
                    continue
                balances_by_account_id[account_id] = _round2(to_float(row.get("debit")) - to_float(row.get("credit")))

        coa_rows: list[SvlDashboardInventoryCoaRow] = []
        missing_codes: list[str] = []
        for code in normalized_codes:
            matched_accounts = accounts_by_code.get(code, [])
            if not matched_accounts:
                missing_codes.append(code)
                continue
            balance = _round2(sum(balances_by_account_id.get(int(row.get("id") or 0), 0.0) for row in matched_accounts))
            name = normalize_text(matched_accounts[0].get("name")) or "-"
            coa_rows.append(
                SvlDashboardInventoryCoaRow(
                    code=code,
                    name=name,
                    balance=balance,
                )
            )

        if missing_codes:
            self._warn_once(
                warnings,
                f"COA persediaan manual tidak ditemukan di database aktif: {', '.join(missing_codes)}.",
            )
            self._log(
                f"Inventory COA missing in database: {', '.join(missing_codes)}",
                level=logging.WARNING,
            )
        return coa_rows, missing_codes

    async def _fetch_svl_totals(
        self,
        *,
        company_id: int,
        context: dict[str, Any],
        date_from: str,
        date_to: str,
        svl_date_field: str,
        link_supported: bool,
        warnings: list[str],
    ) -> dict[int, float]:
        domain = [("company_id", "=", company_id)]
        rows: list[dict[str, Any]]
        if self._should_use_svl_business_date_fallback(
            svl_date_field=svl_date_field,
            link_supported=link_supported,
            date_from=date_from,
            date_to=date_to,
        ):
            try:
                linked_rows = await self.rpc.read_group(
                    "stock.valuation.layer",
                    domain
                    + [("account_move_id", "!=", False)]
                    + self._build_account_move_date_domain(date_from=date_from, date_to=date_to),
                    fields=["product_id", "value"],
                    groupby=["product_id"],
                    context=context,
                    stage="SVL_DASH_SVL_TOTALS_LINKED",
                )
                unlinked_rows = await self.rpc.read_group(
                    "stock.valuation.layer",
                    domain
                    + [("account_move_id", "=", False)]
                    + self._build_svl_date_domain(field_name="create_date", date_from=date_from, date_to=date_to),
                    fields=["product_id", "value"],
                    groupby=["product_id"],
                    context=context,
                    stage="SVL_DASH_SVL_TOTALS_UNLINKED",
                )
                rows = list(linked_rows) + list(unlinked_rows)
                self._log(
                    "SVL date fallback aktif: linked SVL difilter dengan account_move_id.date, orphan SVL dengan create_date."
                )
            except Exception:  # noqa: BLE001
                self._warn_once(
                    warnings,
                    "Gagal memakai account_move_id.date untuk filter periode SVL; fallback ke create_date dan hasil periode bisa bias.",
                )
                domain.extend(self._build_svl_date_domain(field_name=svl_date_field, date_from=date_from, date_to=date_to))
                rows = await self.rpc.read_group(
                    "stock.valuation.layer",
                    domain,
                    fields=["product_id", "value"],
                    groupby=["product_id"],
                    context=context,
                    stage="SVL_DASH_SVL_TOTALS",
                )
        else:
            domain.extend(self._build_svl_date_domain(field_name=svl_date_field, date_from=date_from, date_to=date_to))
            rows = await self.rpc.read_group(
                "stock.valuation.layer",
                domain,
                fields=["product_id", "value"],
                groupby=["product_id"],
                context=context,
                stage="SVL_DASH_SVL_TOTALS",
            )
        totals: dict[int, float] = {}
        for row in rows:
            pid = _many2one_id(row.get("product_id"))
            if pid > 0:
                totals[pid] = _round2(totals.get(pid, 0.0) + to_float(row.get("value")))
        return totals

    async def _fetch_journal_totals(
        self,
        *,
        company_id: int,
        context: dict[str, Any],
        date_from: str,
        date_to: str,
        valuation_account_ids: list[int],
        aml_fields: dict[str, Any],
    ) -> dict[int, float]:
        if not valuation_account_ids:
            return {}
        domain: list[Any] = [
            ("company_id", "=", company_id),
            ("account_id", "in", valuation_account_ids),
            ("product_id", "!=", False),
        ]
        domain.extend(self._build_posted_domain(aml_fields))
        if date_from:
            domain.append(("date", ">=", date_from))
        if date_to:
            domain.append(("date", "<=", date_to))
        rows = await self.rpc.read_group(
            "account.move.line",
            domain,
            fields=["product_id", "debit", "credit"],
            groupby=["product_id"],
            context=context,
            stage="SVL_DASH_JOURNAL_TOTALS",
        )
        totals: dict[int, float] = {}
        for row in rows:
            pid = _many2one_id(row.get("product_id"))
            if pid <= 0:
                continue
            totals[pid] = _round2(to_float(row.get("debit")) - to_float(row.get("credit")))
        return totals

    async def _fetch_stock_move_reference_map(
        self,
        rows: list[dict[str, Any]],
        *,
        context: dict[str, Any],
        warnings: list[str],
        move_fields: dict[str, Any],
    ) -> dict[int, str]:
        move_ids = sorted({_many2one_id(row.get("stock_move_id")) for row in rows if _many2one_id(row.get("stock_move_id")) > 0})
        if not move_ids:
            return {}
        fields: list[str] = []
        for field_name in ("reference", "picking_id", "origin"):
            if field_name in move_fields:
                fields.append(field_name)
        if not fields:
            return {}
        move_reference_map: dict[int, str] = {}
        try:
            for chunk in chunked(move_ids, 500):
                move_rows = await self.rpc.read(
                    "stock.move",
                    chunk,
                    fields=fields,
                    context=context,
                    stage="SVL_DASH_STOCK_MOVE_REFERENCE",
                )
                for move_row in move_rows:
                    move_id = int(move_row.get("id") or 0)
                    reference = (
                        normalize_text(move_row.get("reference"))
                        or _many2one_name(move_row.get("picking_id"))
                        or normalize_text(move_row.get("origin"))
                    )
                    if move_id > 0 and reference:
                        move_reference_map[move_id] = reference
        except Exception:  # noqa: BLE001
            self._warn_once(
                warnings,
                "Gagal membaca reference stock.move; referensi SVL fallback ke description.",
            )
            return {}
        return move_reference_map

    async def _fetch_account_move_date_map(
        self,
        rows: list[dict[str, Any]],
        *,
        context: dict[str, Any],
    ) -> dict[int, str]:
        move_ids = sorted({_many2one_id(row.get("account_move_id")) for row in rows if _many2one_id(row.get("account_move_id")) > 0})
        if not move_ids:
            return {}
        move_date_map: dict[int, str] = {}
        try:
            for chunk in chunked(move_ids, 500):
                move_rows = await self.rpc.read(
                    "account.move",
                    chunk,
                    fields=["date"],
                    context=context,
                    stage="SVL_DASH_ACCOUNT_MOVE_DATES",
                )
                for move_row in move_rows:
                    move_id = int(move_row.get("id") or 0)
                    move_date = normalize_text(move_row.get("date"))
                    if move_id > 0 and move_date:
                        move_date_map[move_id] = move_date
        except Exception:  # noqa: BLE001
            self._log(
                "Gagal membaca tanggal account.move untuk SVL detail; tanggal tetap memakai field layer.",
                level=logging.WARNING,
            )
            return {}
        return move_date_map

    async def _fetch_svl_rows(
        self,
        *,
        domain: list[Any],
        fields: list[str],
        order: str,
        context: dict[str, Any],
        stage: str,
        warnings: list[str],
        move_link_supported: bool,
        move_fields: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], dict[int, str]]:
        requested_fields = list(fields)
        if move_link_supported and "stock_move_id" not in requested_fields:
            requested_fields.append("stock_move_id")
        try:
            rows = await self.rpc.search_read(
                "stock.valuation.layer",
                domain,
                fields=requested_fields,
                order=order,
                context=context,
                stage=stage,
            )
        except Exception:  # noqa: BLE001
            if not move_link_supported or "stock_move_id" not in requested_fields:
                raise
            self._warn_once(
                warnings,
                "Gagal membaca stock_move_id pada stock.valuation.layer; referensi SVL fallback ke description.",
            )
            fallback_fields = [field_name for field_name in requested_fields if field_name != "stock_move_id"]
            rows = await self.rpc.search_read(
                "stock.valuation.layer",
                domain,
                fields=fallback_fields,
                order=order,
                context=context,
                stage=f"{stage}_FALLBACK",
            )
            return rows, {}
        move_reference_map = await self._fetch_stock_move_reference_map(
            rows,
            context=context,
            warnings=warnings,
            move_fields=move_fields,
        )
        return rows, move_reference_map

    async def _fetch_unassigned_journal_rows(
        self,
        *,
        company_id: int,
        context: dict[str, Any],
        date_from: str,
        date_to: str,
        valuation_account_ids: list[int],
        aml_fields: dict[str, Any],
        stage: str,
    ) -> list[dict[str, Any]]:
        if not valuation_account_ids:
            return []
        domain: list[Any] = [
            ("company_id", "=", company_id),
            ("account_id", "in", valuation_account_ids),
            ("product_id", "=", False),
        ]
        domain.extend(self._build_posted_domain(aml_fields))
        if date_from:
            domain.append(("date", ">=", date_from))
        if date_to:
            domain.append(("date", "<=", date_to))
        return await self.rpc.search_read(
            "account.move.line",
            domain,
            fields=["product_id", "debit", "credit", "move_id", "date", "name", "ref", "account_id"],
            order="date,id",
            context=context,
            stage=stage,
        )

    async def _fetch_svl_without_journal(
        self,
        *,
        company_id: int,
        context: dict[str, Any],
        date_from: str,
        date_to: str,
        svl_date_field: str,
        link_supported: bool,
        move_link_supported: bool,
        warnings: list[str],
        move_fields: dict[str, Any],
    ) -> list[SvlDashboardSvlRecord]:
        if not link_supported:
            self._warn_once(warnings, "Field account_move_id tidak tersedia pada stock.valuation.layer; orphan SVL tidak dapat dihitung.")
            return []
        domain: list[Any] = [
            ("company_id", "=", company_id),
            ("account_move_id", "=", False),
            ("value", "!=", 0),
        ]
        domain.extend(self._build_svl_date_domain(field_name=svl_date_field, date_from=date_from, date_to=date_to))
        rows, move_reference_map = await self._fetch_svl_rows(
            domain=domain,
            fields=["product_id", "quantity", "unit_cost", "value", "description", svl_date_field],
            order=f"product_id,{svl_date_field}",
            context=context,
            stage="SVL_DASH_SVL_ORPHAN",
            warnings=warnings,
            move_link_supported=move_link_supported,
            move_fields=move_fields,
        )
        return [
            self._build_svl_record(row, date_field=svl_date_field, move_reference_map=move_reference_map)
            for row in rows
        ]

    async def _fetch_journal_without_svl(
        self,
        *,
        company_id: int,
        context: dict[str, Any],
        date_from: str,
        date_to: str,
        valuation_account_ids: list[int],
        aml_fields: dict[str, Any],
        link_supported: bool,
        warnings: list[str],
    ) -> list[SvlDashboardJournalRecord]:
        if not valuation_account_ids or not link_supported:
            if valuation_account_ids and not link_supported:
                self._warn_once(warnings, "Field account_move_id tidak tersedia pada stock.valuation.layer; orphan journal tidak dapat dihitung.")
            return []
        rows = await self._fetch_journal_rows(
            company_id=company_id,
            context=context,
            date_from=date_from,
            date_to=date_to,
            valuation_account_ids=valuation_account_ids,
            aml_fields=aml_fields,
            product_ids=[],
            stage="SVL_DASH_JOURNAL_ORPHAN_BASE",
            require_product=True,
        )
        move_ids = sorted({_many2one_id(row.get("move_id")) for row in rows if _many2one_id(row.get("move_id")) > 0})
        moves_with_svl = await self._fetch_move_ids_with_svl(
            move_ids=move_ids,
            company_id=company_id,
            context=context,
        )
        return [
            self._build_journal_record(row, has_svl=False)
            for row in rows
            if _many2one_id(row.get("move_id")) > 0 and _many2one_id(row.get("move_id")) not in moves_with_svl
        ]

    async def _fetch_svl_detail(
        self,
        *,
        product_ids: list[int],
        company_id: int,
        context: dict[str, Any],
        date_from: str,
        date_to: str,
        svl_date_field: str,
        link_supported: bool,
        move_link_supported: bool,
        warnings: list[str],
        move_fields: dict[str, Any],
    ) -> dict[int, list[SvlDashboardSvlRecord]]:
        if not product_ids:
            return {}
        base_domain: list[Any] = [
            ("company_id", "=", company_id),
            ("product_id", "in", product_ids),
        ]
        fields = ["product_id", "quantity", "unit_cost", "value", "description", svl_date_field]
        if link_supported:
            fields.append("account_move_id")
        rows: list[dict[str, Any]]
        move_reference_map: dict[int, str]
        if self._should_use_svl_business_date_fallback(
            svl_date_field=svl_date_field,
            link_supported=link_supported,
            date_from=date_from,
            date_to=date_to,
        ):
            try:
                linked_rows, linked_move_reference_map = await self._fetch_svl_rows(
                    domain=base_domain
                    + [("account_move_id", "!=", False)]
                    + self._build_account_move_date_domain(date_from=date_from, date_to=date_to),
                    fields=fields,
                    order="product_id,create_date",
                    context=context,
                    stage="SVL_DASH_SVL_DETAIL_LINKED",
                    warnings=warnings,
                    move_link_supported=move_link_supported,
                    move_fields=move_fields,
                )
                unlinked_rows, unlinked_move_reference_map = await self._fetch_svl_rows(
                    domain=base_domain
                    + [("account_move_id", "=", False)]
                    + self._build_svl_date_domain(field_name="create_date", date_from=date_from, date_to=date_to),
                    fields=fields,
                    order="product_id,create_date",
                    context=context,
                    stage="SVL_DASH_SVL_DETAIL_UNLINKED",
                    warnings=warnings,
                    move_link_supported=move_link_supported,
                    move_fields=move_fields,
                )
                rows = list(linked_rows) + list(unlinked_rows)
                move_reference_map = {**linked_move_reference_map, **unlinked_move_reference_map}
            except Exception:  # noqa: BLE001
                self._warn_once(
                    warnings,
                    "Gagal memakai account_move_id.date untuk detail SVL; fallback ke create_date dan hasil periode bisa bias.",
                )
                fallback_domain = list(base_domain)
                fallback_domain.extend(
                    self._build_svl_date_domain(field_name=svl_date_field, date_from=date_from, date_to=date_to)
                )
                rows, move_reference_map = await self._fetch_svl_rows(
                    domain=fallback_domain,
                    fields=fields,
                    order=f"product_id,{svl_date_field}",
                    context=context,
                    stage="SVL_DASH_SVL_DETAIL",
                    warnings=warnings,
                    move_link_supported=move_link_supported,
                    move_fields=move_fields,
                )
        else:
            domain = list(base_domain)
            domain.extend(self._build_svl_date_domain(field_name=svl_date_field, date_from=date_from, date_to=date_to))
            rows, move_reference_map = await self._fetch_svl_rows(
                domain=domain,
                fields=fields,
                order=f"product_id,{svl_date_field}",
                context=context,
                stage="SVL_DASH_SVL_DETAIL",
                warnings=warnings,
                move_link_supported=move_link_supported,
                move_fields=move_fields,
            )
        move_ids = sorted({_many2one_id(row.get("account_move_id")) for row in rows if _many2one_id(row.get("account_move_id")) > 0})
        move_info_map = await self._fetch_account_move_info_map(move_ids=move_ids, context=context) if link_supported else {}
        grouped: dict[int, list[SvlDashboardSvlRecord]] = defaultdict(list)
        for row in rows:
            pid = _many2one_id(row.get("product_id"))
            if pid > 0:
                grouped[pid].append(
                    self._build_svl_record(
                        row,
                        date_field=svl_date_field,
                        link_supported=link_supported,
                        move_reference_map=move_reference_map,
                        move_info_map=move_info_map,
                    )
                )
        for records in grouped.values():
            records.sort(key=lambda record: (record.date, record.record_id))
        return dict(grouped)

    async def _fetch_journal_detail(
        self,
        *,
        product_ids: list[int],
        company_id: int,
        context: dict[str, Any],
        date_from: str,
        date_to: str,
        valuation_account_ids: list[int],
        aml_fields: dict[str, Any],
        link_supported: bool,
        warnings: list[str],
    ) -> dict[int, list[SvlDashboardJournalRecord]]:
        if not product_ids or not valuation_account_ids:
            return {}
        rows = await self._fetch_journal_rows(
            company_id=company_id,
            context=context,
            date_from=date_from,
            date_to=date_to,
            valuation_account_ids=valuation_account_ids,
            aml_fields=aml_fields,
            product_ids=product_ids,
            stage="SVL_DASH_JOURNAL_DETAIL",
            require_product=True,
        )
        account_ids = sorted({_many2one_id(row.get("account_id")) for row in rows if _many2one_id(row.get("account_id")) > 0})
        account_info_map = await self._fetch_account_info_map(account_ids=account_ids, context=context)
        move_ids = sorted({_many2one_id(row.get("move_id")) for row in rows if _many2one_id(row.get("move_id")) > 0})
        move_info_map = await self._fetch_account_move_info_map(move_ids=move_ids, context=context)
        moves_with_svl: set[int] = set()
        if link_supported:
            moves_with_svl = await self._fetch_move_ids_with_svl(
                move_ids=move_ids,
                company_id=company_id,
                context=context,
            )
        else:
            self._warn_once(
                warnings,
                "Field account_move_id tidak tersedia pada stock.valuation.layer; status pasangan SVL pada journal dianggap tidak tersedia.",
            )

        grouped: dict[int, list[SvlDashboardJournalRecord]] = defaultdict(list)
        for row in rows:
            pid = _many2one_id(row.get("product_id"))
            if pid <= 0:
                continue
            move_id = _many2one_id(row.get("move_id"))
            has_svl = bool(move_id and move_id in moves_with_svl) if link_supported else True
            grouped[pid].append(
                self._build_journal_record(
                    row,
                    has_svl=has_svl,
                    account_info_map=account_info_map,
                    move_info_map=move_info_map,
                )
            )
        for records in grouped.values():
            records.sort(key=lambda record: (record.date, record.record_id))
        return dict(grouped)

    async def _fetch_unassigned_journal_detail(
        self,
        *,
        company_id: int,
        context: dict[str, Any],
        date_from: str,
        date_to: str,
        valuation_account_ids: list[int],
        aml_fields: dict[str, Any],
        link_supported: bool,
        warnings: list[str],
    ) -> list[SvlDashboardJournalRecord]:
        rows = await self._fetch_unassigned_journal_rows(
            company_id=company_id,
            context=context,
            date_from=date_from,
            date_to=date_to,
            valuation_account_ids=valuation_account_ids,
            aml_fields=aml_fields,
            stage="SVL_DASH_JOURNAL_DETAIL_UNASSIGNED",
        )
        if not rows:
            return []
        account_ids = sorted({_many2one_id(row.get("account_id")) for row in rows if _many2one_id(row.get("account_id")) > 0})
        account_info_map = await self._fetch_account_info_map(account_ids=account_ids, context=context)
        move_ids = sorted({_many2one_id(row.get("move_id")) for row in rows if _many2one_id(row.get("move_id")) > 0})
        move_info_map = await self._fetch_account_move_info_map(move_ids=move_ids, context=context)
        moves_with_svl: set[int] = set()
        if link_supported:
            moves_with_svl = await self._fetch_move_ids_with_svl(
                move_ids=move_ids,
                company_id=company_id,
                context=context,
            )
        else:
            self._warn_once(
                warnings,
                "Field account_move_id tidak tersedia pada stock.valuation.layer; status pasangan SVL pada journal dianggap tidak tersedia.",
            )
        records = [
            self._build_journal_record(
                row,
                has_svl=bool(_many2one_id(row.get("move_id")) and _many2one_id(row.get("move_id")) in moves_with_svl)
                if link_supported
                else True,
                account_info_map=account_info_map,
                move_info_map=move_info_map,
            )
            for row in rows
        ]
        records.sort(key=lambda record: (record.date, record.record_id))
        self._log(f"Journal valuation tanpa product_id: {len(records)} row(s) dimasukkan ke item UNASSIGNED-JNL.")
        return records


    async def _fetch_payable_lines_by_product(
        self,
        *,
        po_bill_by_product: dict[int, dict[str, Any]],
        company_id: int,
        context: dict[str, Any],
        account_company_field: str,
        account_fields: dict[str, Any],
        aml_fields: dict[str, Any],
        warnings: list[str],
    ) -> dict[int, dict[str, Any]]:
        if not po_bill_by_product:
            return {}
        payable_account_ids = await self._detect_payable_account_ids(
            company_id=company_id,
            context=context,
            account_company_field=account_company_field,
            account_fields=account_fields,
            warnings=warnings,
        )
        if not payable_account_ids:
            return {}
        move_ids_by_product = {
            product_id: sorted(
                {
                    int(getattr(line, "move_id", 0) or 0)
                    for line in list(payload.get("bill_lines", []) or [])
                    if int(getattr(line, "move_id", 0) or 0) > 0
                }
            )
            for product_id, payload in po_bill_by_product.items()
        }
        all_move_ids = sorted({move_id for move_ids in move_ids_by_product.values() for move_id in move_ids})
        if not all_move_ids:
            return {}
        domain: list[Any] = [
            ("company_id", "=", company_id),
            ("move_id", "in", all_move_ids),
            ("account_id", "in", payable_account_ids),
        ]
        domain.extend(self._build_posted_domain(aml_fields))
        domain.extend(self._build_current_asset_display_type_domain(aml_fields))
        rows = await self.rpc.search_read(
            "account.move.line",
            domain,
            fields=["move_id", "date", "debit", "credit", "account_id", "ref", "name"],
            order="date,id",
            context=context,
            stage="SVL_DASH_PAYABLE_LINES",
        )
        rows_by_move_id: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            move_id = _many2one_id(row.get("move_id"))
            if move_id > 0:
                rows_by_move_id[move_id].append(row)
        account_info_map = await self._fetch_account_info_map(
            account_ids=sorted({_many2one_id(row.get("account_id")) for row in rows if _many2one_id(row.get("account_id")) > 0}),
            context=context,
        )
        move_info_map = await self._fetch_account_move_info_map(move_ids=all_move_ids, context=context)
        result: dict[int, dict[str, Any]] = {}
        for product_id, move_ids in move_ids_by_product.items():
            payable_lines: list[SvlDashboardPayableLine] = []
            seen_row_ids: set[int] = set()
            for move_id in move_ids:
                move_info = move_info_map.get(move_id, {})
                for row in rows_by_move_id.get(move_id, []):
                    row_id = int(row.get("id") or 0)
                    if row_id > 0 and row_id in seen_row_ids:
                        continue
                    if row_id > 0:
                        seen_row_ids.add(row_id)
                    account_id = _many2one_id(row.get("account_id"))
                    account_info = account_info_map.get(account_id, {})
                    debit = _round2(row.get("debit"))
                    credit = _round2(row.get("credit"))
                    payable_lines.append(
                        SvlDashboardPayableLine(
                            bill=normalize_text(move_info.get("name")) or _many2one_name(row.get("move_id")),
                            date=normalize_text(row.get("date")),
                            account_code=normalize_text(account_info.get("code")),
                            account_name=normalize_text(account_info.get("name")) or _many2one_name(row.get("account_id")),
                            debit=debit,
                            credit=credit,
                            balance=_round2(debit - credit),
                            reference=normalize_text(row.get("ref")) or normalize_text(row.get("name")),
                            move_id=move_id,
                        )
                    )
            payable_lines.sort(key=lambda line: (line.date, line.move_id, line.account_code))
            result[product_id] = {
                "payable_lines": payable_lines,
                "payable_line_count": len(payable_lines),
                "total_payable_value": _round2(sum((line.credit - line.debit) for line in payable_lines)),
            }
        return result

    async def _fetch_po_bill_comparison(
        self,
        *,
        product_ids: list[int],
        company_id: int,
        context: dict[str, Any],
        aml_fields: dict[str, Any],
        warnings: list[str],
        include_lines: bool = True,
    ) -> dict[int, dict[str, Any]]:
        if not product_ids:
            return {}
        if "purchase_line_id" not in aml_fields:
            self._warn_once(warnings, "Field purchase_line_id tidak tersedia pada account.move.line; perbandingan PO vs Bill dilewati.")
            return {}

        result: dict[int, dict[str, Any]] = {}
        po_rows = await self.rpc.search_read(
            "purchase.order.line",
            [
                ("company_id", "=", company_id),
                ("product_id", "in", product_ids),
                ("qty_received", ">", 0),
                ("order_id.state", "in", ["purchase", "done"]),
            ],
            fields=["product_id", "order_id", "price_unit", "qty_received", "qty_invoiced"],
            order="product_id",
            context=context,
            stage="SVL_DASH_PO_LINES",
        )
        po_line_ids = [int(row.get("id") or 0) for row in po_rows if int(row.get("id") or 0) > 0]
        bill_by_po_line: dict[int, list[dict[str, Any]]] = defaultdict(list)
        if po_line_ids:
            bill_domain: list[Any] = [("purchase_line_id", "in", po_line_ids)]
            bill_domain.extend(self._build_posted_domain(aml_fields))
            bill_domain.extend(self._build_current_asset_display_type_domain(aml_fields))
            bill_rows = await self.rpc.search_read(
                "account.move.line",
                bill_domain,
                fields=["purchase_line_id", "price_unit", "quantity", "price_subtotal", "move_id", "date"],
                context=context,
                stage="SVL_DASH_BILL_LINES",
            )
            for row in bill_rows:
                po_line_id = _many2one_id(row.get("purchase_line_id"))
                if po_line_id > 0:
                    bill_by_po_line[po_line_id].append(row)

        for row in po_rows:
            pid = _many2one_id(row.get("product_id"))
            if pid <= 0:
                continue
            bucket = result.setdefault(
                pid,
                {
                    "po_lines": [],
                    "bill_lines": [],
                    "po_line_count": 0,
                    "bill_line_count": 0,
                    "total_po_value": 0.0,
                    "total_bill_value": 0.0,
                    "warnings": [],
                },
            )
            qty_received = to_float(row.get("qty_received"))
            qty_invoiced = to_float(row.get("qty_invoiced"))
            price_unit = to_float(row.get("price_unit"))
            po_value = _round2(price_unit * qty_received)
            status = "BILLED" if qty_invoiced >= qty_received else "PARTIAL" if qty_invoiced > 0 else "NOT_BILLED"
            bucket["po_line_count"] = int(bucket.get("po_line_count", 0) or 0) + 1
            if include_lines:
                bucket["po_lines"].append(
                    SvlDashboardPoLine(
                        po=_many2one_name(row.get("order_id")) or "N/A",
                        price_unit=_round2(price_unit),
                        qty_received=_round2(qty_received),
                        qty_invoiced=_round2(qty_invoiced),
                        value=po_value,
                        status=status,
                    )
                )
            bucket["total_po_value"] = _round2(bucket["total_po_value"] + po_value)
            for bill in bill_by_po_line.get(int(row.get("id") or 0), []):
                subtotal = bill.get("price_subtotal")
                bill_value = _round2(
                    subtotal if subtotal is not None else to_float(bill.get("price_unit")) * to_float(bill.get("quantity"))
                )
                bucket["bill_line_count"] = int(bucket.get("bill_line_count", 0) or 0) + 1
                if include_lines:
                    bucket["bill_lines"].append(
                        SvlDashboardBillLine(
                            bill=_many2one_name(bill.get("move_id")) or "N/A",
                            po=_many2one_name(row.get("order_id")) or "N/A",
                            date=normalize_text(bill.get("date")),
                            price_unit=_round2(bill.get("price_unit")),
                            quantity=_round2(bill.get("quantity")),
                            value=bill_value,
                            move_id=_many2one_id(bill.get("move_id")),
                        )
                    )
                bucket["total_bill_value"] = _round2(bucket["total_bill_value"] + bill_value)
        return result

    async def _fetch_product_info(
        self,
        product_ids: list[int],
        *,
        company_id: int,
        context: dict[str, Any],
        category_fields: dict[str, Any],
    ) -> dict[int, dict[str, Any]]:
        if not product_ids:
            return {}
        rows = await self.rpc.search_read(
            "product.product",
            [("id", "in", product_ids)],
            fields=["name", "default_code", "categ_id", "standard_price", "cost_method"],
            context=context,
            stage="SVL_DASH_PRODUCTS",
        )
        category_account_specs = self._detect_category_account_specs(category_fields)
        category_ids = sorted({_many2one_id(row.get("categ_id")) for row in rows if _many2one_id(row.get("categ_id")) > 0})
        account_candidates_by_category_id: dict[int, list[SvlDashboardRepairAccountCandidate]] = {}
        real_time_category_ids: set[int] = set()
        if category_ids and category_account_specs:
            category_read_fields = ["name", "property_valuation"]
            category_read_fields.extend(spec["field_name"] for spec in category_account_specs)
            category_rows = await self.rpc.read(
                "product.category",
                category_ids,
                fields=category_read_fields,
                context=context,
                stage="SVL_DASH_PRODUCT_CATEGORY_ACCOUNTS",
            )
            real_time_category_ids = {
                int(cat_row.get("id") or 0)
                for cat_row in category_rows
                if normalize_text(cat_row.get("property_valuation")) == "real_time"
            }
            account_ids = sorted(
                {
                    _many2one_id(category_row.get(spec["field_name"]))
                    for category_row in category_rows
                    for spec in category_account_specs
                    if _many2one_id(category_row.get(spec["field_name"])) > 0
                }
            )
            account_info_map = await self._fetch_account_info_map(account_ids=account_ids, context=context)
            account_candidates_by_category_id = self._build_category_account_candidates_by_category_id(
                category_rows=category_rows,
                account_specs=category_account_specs,
                account_info_map=account_info_map,
            )
        elif category_ids:
            pv_rows = await self.rpc.read(
                "product.category",
                category_ids,
                fields=["property_valuation"],
                context=context,
                stage="SVL_DASH_PRODUCT_CATEGORY_VALUATION",
            )
            real_time_category_ids = {
                int(cat_row.get("id") or 0)
                for cat_row in pv_rows
                if normalize_text(cat_row.get("property_valuation")) == "real_time"
            }
        return {
            int(row.get("id") or 0): {
                "name": normalize_text(row.get("name")),
                "code": normalize_text(row.get("default_code")),
                "categ": _many2one_name(row.get("categ_id")),
                "cost_method": normalize_text(row.get("cost_method")),
                "standard_price": _round2(row.get("standard_price")),
                "repair_account_candidates": list(
                    account_candidates_by_category_id.get(_many2one_id(row.get("categ_id")), [])
                ),
                "automated_valuation": _many2one_id(row.get("categ_id")) in real_time_category_ids,
            }
            for row in rows
            if int(row.get("id") or 0) > 0
        }

    @staticmethod
    def _detect_product_expense_account_fields(product_fields: dict[str, Any]) -> list[str]:
        exact_matches: list[str] = []
        loose_matches: list[str] = []
        for field_name, meta in (product_fields or {}).items():
            if meta.get("type") != "many2one":
                continue
            clean_name = normalize_text(field_name).lower()
            if "account" not in clean_name or "expense" not in clean_name or "categ" in clean_name:
                continue
            if clean_name == "property_account_expense_id":
                exact_matches.append(field_name)
            else:
                loose_matches.append(field_name)
        return list(dict.fromkeys(exact_matches + sorted(loose_matches)))

    @staticmethod
    def _detect_category_account_specs(category_fields: dict[str, Any]) -> list[dict[str, Any]]:
        specs: list[dict[str, Any]] = []
        seen: set[str] = set()
        for field_name, meta in (category_fields or {}).items():
            if meta.get("type") != "many2one":
                continue
            classified = SvlDashboardServiceAsync._classify_category_account_field(field_name)
            if classified is None:
                continue
            source, role = classified
            if field_name in seen:
                continue
            seen.add(field_name)
            specs.append(
                {
                    "field_name": field_name,
                    "source": source,
                    "role": role,
                    "priority": _CATEGORY_ACCOUNT_SOURCE_PRIORITY.get(source, 99),
                }
            )
        specs.sort(key=lambda item: (int(item["priority"]), normalize_text(item["field_name"])))
        return specs

    @staticmethod
    def _classify_category_account_field(field_name: str) -> tuple[str, str] | None:
        clean_name = normalize_text(field_name).lower()
        if "account" not in clean_name:
            return None
        if clean_name == "property_stock_valuation_account_id":
            return "Category Stock Valuation", "valuation"
        if "expense" in clean_name:
            return "Category Expense", "expense"
        if "input" in clean_name and "inter_company" not in clean_name and "intercompany" not in clean_name:
            return "Category Input", "input"
        if "output" in clean_name and "inter_company" not in clean_name and "intercompany" not in clean_name:
            return "Category Output", "output"
        if "cost" in clean_name:
            return "Category Cost", "cost"
        return "Category Other", "other"

    @staticmethod
    def _build_category_account_candidates_by_category_id(
        *,
        category_rows: list[dict[str, Any]],
        account_specs: list[dict[str, Any]],
        account_info_map: dict[int, dict[str, Any]],
    ) -> dict[int, list[SvlDashboardRepairAccountCandidate]]:
        result: dict[int, list[SvlDashboardRepairAccountCandidate]] = {}
        for category_row in category_rows:
            category_id = int(category_row.get("id") or 0)
            if category_id <= 0:
                continue
            bucket: dict[str, dict[str, Any]] = {}
            for spec in account_specs:
                account_id = _many2one_id(category_row.get(spec["field_name"]))
                if account_id <= 0:
                    continue
                account_info = account_info_map.get(account_id, {})
                code = normalize_text(account_info.get("code")).upper()
                if not code:
                    continue
                name = normalize_text(account_info.get("name")) or _many2one_name(category_row.get(spec["field_name"]))
                existing = bucket.get(code)
                if existing is None:
                    bucket[code] = {
                        "code": code,
                        "name": name,
                        "source": normalize_text(spec["source"]),
                        "role": normalize_text(spec["role"]),
                        "account_id": account_id,
                        "field_name": normalize_text(spec["field_name"]),
                        "priority": int(spec["priority"]),
                    }
                    continue
                existing_sources = {
                    part.strip()
                    for part in normalize_text(existing.get("source")).split("|")
                    if normalize_text(part).strip()
                }
                existing_sources.add(normalize_text(spec["source"]))
                existing["source"] = " | ".join(sorted(existing_sources))
                if not normalize_text(existing.get("name")) and name:
                    existing["name"] = name
                if int(spec["priority"]) < int(existing.get("priority", 99)):
                    existing["priority"] = int(spec["priority"])
                    existing["role"] = normalize_text(spec["role"])
                    existing["account_id"] = account_id
                    existing["field_name"] = normalize_text(spec["field_name"])
            ordered_candidates = sorted(bucket.values(), key=lambda item: (int(item["priority"]), normalize_text(item["code"])))
            result[category_id] = [
                SvlDashboardRepairAccountCandidate(
                    code=normalize_text(candidate["code"]).upper(),
                    name=normalize_text(candidate["name"]),
                    source=normalize_text(candidate["source"]),
                    role=normalize_text(candidate["role"]),
                    account_id=int(candidate["account_id"] or 0),
                    field_name=normalize_text(candidate.get("field_name")),
                )
                for candidate in ordered_candidates
            ]
        return result

    async def _fetch_account_info_map(
        self,
        *,
        account_ids: list[int],
        context: dict[str, Any],
    ) -> dict[int, dict[str, Any]]:
        if not account_ids:
            return {}
        result: dict[int, dict[str, Any]] = {}
        for row in await self._read_in_chunks(
            "account.account",
            ids=account_ids,
            fields=["code", "name", "account_type"],
            context=context,
            stage="SVL_DASH_ACCOUNT_INFO",
            chunk_size=500,
        ):
            account_id = int(row.get("id") or 0)
            if account_id > 0:
                result[account_id] = row
        return result

    async def _fetch_account_move_info_map(
        self,
        *,
        move_ids: list[int],
        context: dict[str, Any],
    ) -> dict[int, dict[str, Any]]:
        if not move_ids:
            return {}
        result: dict[int, dict[str, Any]] = {}
        for row in await self._read_in_chunks(
            "account.move",
            ids=move_ids,
            fields=["name", "date", "state", "journal_id", "ref"],
            context=context,
            stage="SVL_DASH_MOVE_INFO",
            chunk_size=500,
        ):
            move_id = int(row.get("id") or 0)
            if move_id > 0:
                result[move_id] = row
        return result

    async def _fetch_move_line_summary(
        self,
        *,
        move_ids: list[int],
        context: dict[str, Any],
        aml_fields: dict[str, Any],
    ) -> dict[int, dict[str, int]]:
        if not move_ids:
            return {}
        domain: list[Any] = [("move_id", "in", move_ids)]
        if "display_type" in aml_fields:
            domain.append(("display_type", "=", False))
        rows = await self.rpc.search_read(
            "account.move.line",
            domain,
            fields=["move_id", "debit", "credit", "account_id"],
            context=context,
            stage="SVL_DASH_MOVE_LINE_SUMMARY",
        )
        summary: dict[int, dict[str, int]] = {}
        for row in rows:
            move_id = _many2one_id(row.get("move_id"))
            if move_id <= 0:
                continue
            bucket = summary.setdefault(move_id, {"line_count": 0, "nonzero_count": 0})
            bucket["line_count"] += 1
            account_id = _many2one_id(row.get("account_id"))
            if account_id > 0 or abs(to_float(row.get("debit"))) > 0.0 or abs(to_float(row.get("credit"))) > 0.0:
                bucket["nonzero_count"] += 1
        return summary

    async def _group_aml_by_move_and_account(
        self,
        *,
        move_ids: list[int],
        company_id: int,
        context: dict[str, Any],
        aml_fields: dict[str, Any],
        stage: str,
    ) -> list[dict[str, Any]]:
        normalized_move_ids = sorted({int(move_id or 0) for move_id in move_ids if int(move_id or 0) > 0})
        if not normalized_move_ids:
            return []
        domain: list[Any] = [
            ("company_id", "=", company_id),
            ("move_id", "in", normalized_move_ids),
        ]
        domain.extend(self._build_posted_domain(aml_fields))
        if "display_type" in aml_fields:
            domain.append(("display_type", "not in", ["line_section", "line_note"]))
        try:
            grouped_rows = await self.rpc.read_group(
                "account.move.line",
                domain,
                fields=["move_id", "account_id", "debit", "credit", "balance"],
                groupby=["move_id", "account_id"],
                lazy=False,
                context=context,
                stage=stage,
            )
            if all(
                _many2one_id(row.get("move_id")) > 0 and _many2one_id(row.get("account_id")) > 0
                for row in grouped_rows
            ):
                return list(grouped_rows)
        except Exception:  # noqa: BLE001
            pass

        fallback_rows = await self._search_read_in_chunks(
            "account.move.line",
            ids_field="move_id",
            ids=normalized_move_ids,
            fields=[
                "move_id",
                "account_id",
                "debit",
                "credit",
                "balance",
                *(["display_type"] if "display_type" in aml_fields else []),
            ],
            context=context,
            stage=f"{stage}_FALLBACK",
            base_domain=[
                ("company_id", "=", company_id),
                *self._build_posted_domain(aml_fields),
            ],
            order="move_id,id",
        )
        grouped_by_key: dict[tuple[int, int], dict[str, Any]] = {}
        for row in fallback_rows:
            if normalize_text(row.get("display_type")) in {"line_section", "line_note"}:
                continue
            move_id = _many2one_id(row.get("move_id"))
            account_id = _many2one_id(row.get("account_id"))
            if move_id <= 0 or account_id <= 0:
                continue
            key = (move_id, account_id)
            bucket = grouped_by_key.setdefault(
                key,
                {
                    "move_id": row.get("move_id") or move_id,
                    "account_id": row.get("account_id") or account_id,
                    "debit": 0.0,
                    "credit": 0.0,
                    "balance": 0.0,
                },
            )
            bucket["debit"] = _round2(to_float(bucket.get("debit")) + to_float(row.get("debit")))
            bucket["credit"] = _round2(to_float(bucket.get("credit")) + to_float(row.get("credit")))
            bucket["balance"] = _round2(bucket["debit"] - bucket["credit"])
        return [
            grouped_by_key[key]
            for key in sorted(grouped_by_key, key=lambda item: (item[0], item[1]))
        ]

    async def _fetch_journal_rows(
        self,
        *,
        company_id: int,
        context: dict[str, Any],
        date_from: str,
        date_to: str,
        valuation_account_ids: list[int],
        aml_fields: dict[str, Any],
        product_ids: list[int],
        stage: str,
        require_product: bool | None,
    ) -> list[dict[str, Any]]:
        domain: list[Any] = [
            ("company_id", "=", company_id),
            ("account_id", "in", valuation_account_ids),
        ]
        if require_product is True:
            domain.append(("product_id", "!=", False))
        elif require_product is False:
            domain.append(("product_id", "=", False))
        if product_ids:
            domain.append(("product_id", "in", product_ids))
        domain.extend(self._build_posted_domain(aml_fields))
        if date_from:
            domain.append(("date", ">=", date_from))
        if date_to:
            domain.append(("date", "<=", date_to))
        return await self.rpc.search_read(
            "account.move.line",
            domain,
            fields=["product_id", "debit", "credit", "move_id", "date", "name", "ref", "account_id"],
            order="product_id,date,id" if require_product is not False else "date,id",
            context=context,
            stage=stage,
        )

    async def _fetch_move_ids_with_svl(
        self,
        *,
        move_ids: list[int],
        company_id: int,
        context: dict[str, Any],
    ) -> set[int]:
        paired: set[int] = set()
        if not move_ids:
            return paired
        for chunk in chunked(move_ids, 500):
            rows = await self.rpc.search_read(
                "stock.valuation.layer",
                [
                    ("company_id", "=", company_id),
                    ("account_move_id", "in", chunk),
                ],
                fields=["account_move_id"],
                context=context,
                stage="SVL_DASH_MOVE_SVL_LINK",
            )
            for row in rows:
                move_id = _many2one_id(row.get("account_move_id"))
                if move_id > 0:
                    paired.add(move_id)
        return paired

    def _build_svl_record(
        self,
        row: dict[str, Any],
        *,
        date_field: str,
        link_supported: bool = True,
        move_reference_map: dict[int, str] | None = None,
        move_info_map: dict[int, dict[str, Any]] | None = None,
    ) -> SvlDashboardSvlRecord:
        move_reference_map = move_reference_map or {}
        move_info_map = move_info_map or {}
        move_id = _many2one_id(row.get("account_move_id"))
        move_info = move_info_map.get(move_id, {})
        move_reference = move_reference_map.get(_many2one_id(row.get("stock_move_id")))
        reference = move_reference or extract_reference_from_description(row.get("description"))
        effective_date = normalize_text(row.get(date_field))
        if date_field == "create_date" and move_id > 0:
            effective_date = normalize_text(move_info.get("date")) or effective_date
        return SvlDashboardSvlRecord(
            record_id=int(row.get("id") or 0),
            product_id=_many2one_id(row.get("product_id")),
            date=effective_date,
            quantity=_round2(row.get("quantity")),
            unit_cost=_round2(row.get("unit_cost")),
            value=_round2(row.get("value")),
            reference=reference,
            description=normalize_text(row.get("description")),
            has_journal=bool(row.get("account_move_id")) if link_supported else True,
            move_id=move_id,
            move_state=normalize_text(move_info.get("state")),
            journal_ref=(normalize_text(move_info.get("name")) or _many2one_name(row.get("account_move_id"))) if link_supported else "",
        )

    def _build_journal_record(
        self,
        row: dict[str, Any],
        *,
        has_svl: bool,
        account_info_map: dict[int, dict[str, Any]] | None = None,
        move_info_map: dict[int, dict[str, Any]] | None = None,
    ) -> SvlDashboardJournalRecord:
        account_info_map = account_info_map or {}
        move_info_map = move_info_map or {}
        debit = _round2(row.get("debit"))
        credit = _round2(row.get("credit"))
        move_id = _many2one_id(row.get("move_id"))
        move_info = move_info_map.get(move_id, {})
        account_id = _many2one_id(row.get("account_id"))
        account_info = account_info_map.get(account_id, {})
        return SvlDashboardJournalRecord(
            record_id=int(row.get("id") or 0),
            product_id=_many2one_id(row.get("product_id")),
            date=normalize_text(row.get("date")),
            debit=debit,
            credit=credit,
            net=_round2(debit - credit),
            journal_entry=normalize_text(move_info.get("name")) or _many2one_name(row.get("move_id")),
            reference=normalize_text(row.get("ref")) or normalize_text(row.get("name")),
            has_svl=bool(has_svl),
            move_id=move_id,
            move_state=normalize_text(move_info.get("state")),
            account_id=account_id,
            account_code=normalize_text(account_info.get("code")),
            account_name=normalize_text(account_info.get("name")) or _many2one_name(row.get("account_id")),
            line_label=normalize_text(row.get("name")),
        )

    @staticmethod
    def _journal_matches_svl_hint(svl_record: SvlDashboardSvlRecord, journal_record: SvlDashboardJournalRecord) -> bool:
        if journal_record.has_svl:
            return False
        if not _amount_matches(abs(svl_record.value), abs(journal_record.net)):
            return False
        svl_ref = _normalized_match_text(svl_record.reference)
        journal_ref = _normalized_match_text(journal_record.reference)
        if not svl_ref or not journal_ref or svl_ref != journal_ref:
            return False
        svl_date = normalize_text(svl_record.date)[:10]
        journal_date = normalize_text(journal_record.date)[:10]
        if svl_date and journal_date and svl_date != journal_date:
            return False
        return True

    def _build_merged_record_from_pair(
        self,
        svl_record: SvlDashboardSvlRecord,
        journal_record: SvlDashboardJournalRecord,
        *,
        row_type: str,
        status: str,
        match_basis: str,
        note: str,
        repair_candidate: bool,
    ) -> SvlDashboardMergedRecord:
        return SvlDashboardMergedRecord(
            row_key=f"{row_type}:svl:{svl_record.record_id}:aml:{journal_record.record_id}",
            row_type=row_type,
            status=status,
            match_basis=match_basis,
            note=note,
            repair_candidate=repair_candidate,
            product_id=svl_record.product_id or journal_record.product_id,
            svl_id=svl_record.record_id,
            svl_date=svl_record.date,
            svl_qty=svl_record.quantity,
            svl_unit_cost=svl_record.unit_cost,
            svl_value=svl_record.value,
            svl_reference=svl_record.reference,
            move_id=journal_record.move_id or svl_record.move_id,
            move_name=journal_record.journal_entry or svl_record.journal_ref,
            move_date=journal_record.date or svl_record.date,
            move_state=journal_record.move_state or svl_record.move_state,
            aml_id=journal_record.record_id,
            aml_date=journal_record.date,
            account_id=journal_record.account_id,
            account_code=journal_record.account_code,
            account_name=journal_record.account_name,
            debit=journal_record.debit,
            credit=journal_record.credit,
            net=journal_record.net,
            reference=journal_record.reference or svl_record.reference,
            line_label=journal_record.line_label,
        )

    def _build_merged_record_from_svl(
        self,
        svl_record: SvlDashboardSvlRecord,
        *,
        row_type: str,
        status: str,
        match_basis: str,
        note: str,
        repair_candidate: bool,
    ) -> SvlDashboardMergedRecord:
        return SvlDashboardMergedRecord(
            row_key=f"{row_type}:svl:{svl_record.record_id}",
            row_type=row_type,
            status=status,
            match_basis=match_basis,
            note=note,
            repair_candidate=repair_candidate,
            product_id=svl_record.product_id,
            svl_id=svl_record.record_id,
            svl_date=svl_record.date,
            svl_qty=svl_record.quantity,
            svl_unit_cost=svl_record.unit_cost,
            svl_value=svl_record.value,
            svl_reference=svl_record.reference,
            move_id=svl_record.move_id,
            move_name=svl_record.journal_ref,
            move_date=svl_record.date,
            move_state=svl_record.move_state,
            reference=svl_record.reference,
        )

    def _build_merged_record_from_journal(
        self,
        journal_record: SvlDashboardJournalRecord,
        *,
        row_type: str,
        status: str,
        match_basis: str,
        note: str,
        repair_candidate: bool,
    ) -> SvlDashboardMergedRecord:
        return SvlDashboardMergedRecord(
            row_key=f"{row_type}:aml:{journal_record.record_id}",
            row_type=row_type,
            status=status,
            match_basis=match_basis,
            note=note,
            repair_candidate=repair_candidate,
            product_id=journal_record.product_id,
            move_id=journal_record.move_id,
            move_name=journal_record.journal_entry,
            move_date=journal_record.date,
            move_state=journal_record.move_state,
            aml_id=journal_record.record_id,
            aml_date=journal_record.date,
            account_id=journal_record.account_id,
            account_code=journal_record.account_code,
            account_name=journal_record.account_name,
            debit=journal_record.debit,
            credit=journal_record.credit,
            net=journal_record.net,
            reference=journal_record.reference,
            line_label=journal_record.line_label,
        )

    def _build_merged_records(
        self,
        *,
        product_id: int,
        svl_records: list[SvlDashboardSvlRecord],
        journal_records: list[SvlDashboardJournalRecord],
        move_line_summary: dict[int, dict[str, int]],
    ) -> list[SvlDashboardMergedRecord]:
        remaining_indices = set(range(len(journal_records)))
        journal_indices_by_move: dict[int, list[int]] = defaultdict(list)
        fallback_indices_by_hint: dict[tuple[str, float], list[int]] = defaultdict(list)
        for index, record in enumerate(journal_records):
            if record.move_id > 0:
                journal_indices_by_move[record.move_id].append(index)
            if record.has_svl:
                continue
            journal_ref = _normalized_match_text(record.reference)
            if not journal_ref:
                continue
            fallback_indices_by_hint[(journal_ref, abs(_round2(record.net)))].append(index)

        merged_rows: list[SvlDashboardMergedRecord] = []
        for svl_record in svl_records:
            if svl_record.move_id > 0:
                move_indices = journal_indices_by_move.get(svl_record.move_id, [])
                if move_indices:
                    for journal_index in move_indices:
                        journal_record = journal_records[journal_index]
                        merged_rows.append(
                            self._build_merged_record_from_pair(
                                svl_record,
                                journal_record,
                                row_type="svl_linked",
                                status="Linked via move",
                                match_basis="move_link",
                                note="SVL dan AML bertemu pada account.move yang sama.",
                                repair_candidate=False,
                            )
                        )
                        remaining_indices.discard(journal_index)
                    continue

                move_summary = move_line_summary.get(svl_record.move_id, {})
                line_count = int(move_summary.get("line_count", 0))
                nonzero_count = int(move_summary.get("nonzero_count", 0))
                if line_count <= 0 or nonzero_count <= 0:
                    merged_rows.append(
                        self._build_merged_record_from_svl(
                            svl_record,
                            row_type="svl_linked_empty_move",
                            status="Linked JE header kosong",
                            match_basis="move_link",
                            note="Linked account.move tidak punya line debit/kredit yang bisa membentuk valuasi.",
                            repair_candidate=True,
                        )
                    )
                else:
                    merged_rows.append(
                        self._build_merged_record_from_svl(
                            svl_record,
                            row_type="svl_linked_no_valuation_line",
                            status="Linked JE tanpa line valuasi",
                            match_basis="move_link",
                            note="Linked account.move ada, tetapi item ini tidak punya valuation line yang cocok.",
                            repair_candidate=True,
                        )
                    )
                continue

            svl_ref = _normalized_match_text(svl_record.reference)
            fallback_candidates: list[int] = []
            if svl_ref:
                svl_date = normalize_text(svl_record.date)[:10]
                fallback_key = (svl_ref, abs(_round2(svl_record.value)))
                for journal_index in fallback_indices_by_hint.get(fallback_key, []):
                    if journal_index not in remaining_indices:
                        continue
                    journal_date = normalize_text(journal_records[journal_index].date)[:10]
                    if svl_date and journal_date and svl_date != journal_date:
                        continue
                    fallback_candidates.append(journal_index)
            if len(fallback_candidates) == 1:
                journal_index = fallback_candidates[0]
                merged_rows.append(
                    self._build_merged_record_from_pair(
                        svl_record,
                        journal_records[journal_index],
                        row_type="svl_reference_hint",
                        status="Fallback hint",
                        match_basis="reference/date/amount",
                        note="Hint unik berdasarkan reference, tanggal, dan nominal; bukan link move authoritative.",
                        repair_candidate=False,
                    )
                )
                remaining_indices.discard(journal_index)
                continue

            merged_rows.append(
                self._build_merged_record_from_svl(
                    svl_record,
                    row_type="svl_no_move",
                    status="SVL tanpa JE",
                    match_basis="none",
                    note="SVL belum punya account_move_id.",
                    repair_candidate=True,
                )
            )

        for journal_index in sorted(remaining_indices):
            journal_record = journal_records[journal_index]
            merged_rows.append(
                self._build_merged_record_from_journal(
                    journal_record,
                    row_type="journal_no_svl",
                    status="Journal tanpa SVL",
                    match_basis="none",
                    note="Valuation AML tidak punya SVL yang terhubung untuk item ini.",
                    repair_candidate=False,
                )
            )

        merged_rows.sort(
            key=lambda row: (
                normalize_text(row.move_date or row.svl_date or row.aml_date),
                normalize_text(row.move_name or row.reference),
                int(row.svl_id or 0),
                int(row.aml_id or 0),
                row.row_key,
            )
        )
        return merged_rows
