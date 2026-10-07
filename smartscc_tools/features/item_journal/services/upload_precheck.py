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


class UploadPrecheckMixin:
    def _append_row_issue(self, row_issues: Dict[int, List[str]], row_number: int, message: str) -> None:
        clean = normalize_text(message)
        row_no = int(row_number or 0)
        if row_no <= 0 or not clean:
            return
        messages = row_issues.setdefault(row_no, [])
        if clean not in messages:
            messages.append(clean)

    def _merge_row_issues(self, target: Dict[int, List[str]], source: Dict[int, List[str]]) -> None:
        for row_number, messages in source.items():
            for message in messages:
                self._append_row_issue(target, int(row_number or 0), message)

    def _apply_precheck_failure_rows(
        self,
        active_rows: List[ItemJournalRow],
        row_errors: Dict[int, List[str]],
        stop_reason: str,
    ) -> None:
        clean_stop_reason = normalize_text(stop_reason) or "Precheck upload gagal. Proses dihentikan sebelum create Internal Transfer."

        for row in active_rows:
            row_number = int(row.row_number or 0)
            messages = [normalize_text(item) for item in row_errors.get(row_number, []) if normalize_text(item)]
            if messages:
                row.result = "[Error]"
                row.stj = ""
                row.error = ""
                for message in messages:
                    row.append_error(message)
                self._emit_audit(
                    stage="ROW_ERROR",
                    row_number=row.row_number,
                    batch_seq=self.batch_seq,
                    result="ERROR",
                    message=row.error,
                    picking_id=0,
                )
                continue

            if row.result == "[Error]":
                continue
            row.result = "[Stopped]"
            row.stj = ""
            row.append_error(clean_stop_reason)
            self._emit_audit(
                stage="ROW_STOPPED",
                row_number=row.row_number,
                batch_seq=self.batch_seq,
                result="STOPPED",
                message=clean_stop_reason,
                picking_id=0,
            )

    async def _run_global_precheck(self, active_rows: List[ItemJournalRow]) -> Dict[str, Any]:
        def _raise_precheck_failure(category_map: Dict[str, List[str]], row_issues: Dict[int, List[str]]) -> None:
            parts = ["Precheck upload gagal. Proses dihentikan sebelum create Internal Transfer."]
            for category, items in category_map.items():
                clean_items = [normalize_text(item) for item in items if normalize_text(item)]
                if not clean_items:
                    continue
                parts.extend(["", f"Kategori {category}:"])
                parts.extend(f"- {item}" for item in clean_items)
            raise GlobalPrecheckError("\n".join(parts), row_errors=row_issues)

        row_issues: Dict[int, List[str]] = {}
        company_issues: List[str] = []
        date_issues: List[str] = []
        lock_issues: List[str] = []

        company_ids, company_err = collect_active_company_ids(active_rows)
        if company_err:
            company_issues.append(company_err)
            for row in active_rows:
                if int(row.company_id or 0) <= 0:
                    self._append_row_issue(row_issues, row.row_number, company_err)
        if len(company_ids) > 1:
            company_list = ", ".join(str(item) for item in company_ids)
            issue = (
                "Company aktif pada sheet 'Item Journal' kolom B harus 1 unik. "
                f"Ditemukan: {company_list}."
            )
            company_issues.append(issue)
            for row in active_rows:
                self._append_row_issue(row_issues, row.row_number, issue)
        if company_issues:
            _raise_precheck_failure({"Company": company_issues}, row_issues)

        company_id = int(company_ids[0] or 0)

        date_unique: Dict[date, bool] = {}
        for row in active_rows:
            row_date = row.parsed_date()
            if row_date is None:
                issue = f"Tanggal kolom A tidak valid pada baris {row.row_number}."
                date_issues.append(issue)
                self._append_row_issue(row_issues, row.row_number, issue)
                continue
            date_unique[row_date] = True

        if len(date_unique) > 1:
            listed = ", ".join(sorted(item.strftime("%Y-%m-%d") for item in date_unique.keys()))
            issue = f"Tanggal kolom A harus 1 unik. Ditemukan: {listed}."
            date_issues.append(issue)
            for row in active_rows:
                self._append_row_issue(row_issues, row.row_number, issue)
        elif date_unique:
            only_date = next(iter(date_unique.keys()))
            if only_date > date.today():
                issue = (
                    f"Tanggal kolom A berada di masa depan "
                    f"({only_date.strftime('%Y-%m-%d')} > {date.today().strftime('%Y-%m-%d')})."
                )
                date_issues.append(issue)
                for row in active_rows:
                    self._append_row_issue(row_issues, row.row_number, issue)

        lock_result = await self.lock_date_service.fetch_lock_dates([company_id])
        lock_date = lock_result.lock_date_map.get(company_id)
        lock_status = lock_result.status_map.get(company_id, "")
        if lock_status.startswith("ERROR:"):
            lock_issue = (
                f"Gagal mengambil fiscalyear_lock_date untuk company_id={company_id}. "
                f"{lock_status[6:].strip()}"
            )
            lock_issues.append(lock_issue)
            for row in active_rows:
                self._append_row_issue(row_issues, row.row_number, lock_issue)
        if lock_date and date_unique:
            only_date = next(iter(date_unique.keys()))
            if lock_date >= only_date:
                lock_issue = "Date Accounting sudah ditutup, silakan hubungi Accounting untuk dibuka"
                lock_issues.append(lock_issue)
                for row in active_rows:
                    self._append_row_issue(row_issues, row.row_number, lock_issue)

        location_issues, location_row_issues = await self._validate_locations_precheck(active_rows, company_id)
        self._merge_row_issues(row_issues, location_row_issues)

        product_issues, product_row_issues = await self._validate_products_precheck(active_rows, company_id)
        self._merge_row_issues(row_issues, product_row_issues)

        if location_issues or lock_issues or date_issues or product_issues:
            _raise_precheck_failure(
                {
                    "Lokasi": location_issues,
                    "Lock period": lock_issues,
                    "Tanggal": date_issues,
                    "Produk": product_issues,
                },
                row_issues,
            )

        return {"company_id": company_id, "unique_dates": sorted(date_unique.keys()), "lock_date": lock_date}

    async def _validate_locations_precheck(
        self,
        active_rows: List[ItemJournalRow],
        company_id: int,
    ) -> tuple[List[str], Dict[int, List[str]]]:
        issues: List[str] = []
        seen: Dict[str, bool] = {}
        row_issues: Dict[int, List[str]] = {}
        requested: Dict[str, str] = {}
        requested_rows: Dict[str, set[int]] = {}

        for row in active_rows:
            src = normalize_text(row.src_loc)
            dest = normalize_text(row.dest_loc)
            if not src:
                issue = f"Source Location (Kolom E) wajib diisi pada baris {row.row_number}."
                if issue not in seen:
                    issues.append(issue)
                    seen[issue] = True
                self._append_row_issue(row_issues, row.row_number, issue)
            else:
                key = src.lower()
                requested[key] = src
                requested_rows.setdefault(key, set()).add(int(row.row_number or 0))

            if not dest:
                issue = f"Destination Location (Kolom F) wajib diisi pada baris {row.row_number}."
                if issue not in seen:
                    issues.append(issue)
                    seen[issue] = True
                self._append_row_issue(row_issues, row.row_number, issue)
            else:
                key = dest.lower()
                requested[key] = dest
                requested_rows.setdefault(key, set()).add(int(row.row_number or 0))

        for key, location_name in requested.items():
            loc_id = await self._resolve_location_id_cached(location_name, company_id)
            row_numbers = sorted(item for item in requested_rows.get(key, set()) if int(item or 0) > 0)
            if loc_id <= 0:
                issue = f"Lokasi tidak ditemukan di Odoo (complete_name): '{location_name}'."
                if issue not in seen:
                    issues.append(issue)
                    seen[issue] = True
                for row_number in row_numbers:
                    self._append_row_issue(row_issues, row_number, issue)
                continue
            valid, err = await self._validate_location_company(loc_id, company_id)
            if not valid:
                issue = f"Lokasi '{location_name}' tidak sesuai company: {err}"
                if issue not in seen:
                    issues.append(issue)
                    seen[issue] = True
                for row_number in row_numbers:
                    self._append_row_issue(row_issues, row_number, issue)
        return issues, row_issues

    async def _validate_products_precheck(
        self,
        active_rows: List[ItemJournalRow],
        company_id: int,
    ) -> tuple[List[str], Dict[int, List[str]]]:
        issues: List[str] = []
        seen: Dict[str, bool] = {}
        row_issues: Dict[int, List[str]] = {}
        rows_by_product_id: Dict[int, List[int]] = {}
        product_by_id: Dict[int, ProductInfo] = {}

        await self._prefetch_products(active_rows, company_id)
        for row in active_rows:
            product, product_err = await self._resolve_product_for_row(row, company_id)
            if product_err:
                issue = normalize_text(product_err) or "Product tidak ditemukan."
                if issue not in seen:
                    issues.append(issue)
                    seen[issue] = True
                self._append_row_issue(row_issues, row.row_number, issue)
                continue
            if product is None or int(product.product_id or 0) <= 0:
                issue = "Product tidak ditemukan."
                if issue not in seen:
                    issues.append(issue)
                    seen[issue] = True
                self._append_row_issue(row_issues, row.row_number, issue)
                continue

            product_id = int(product.product_id or 0)
            product_by_id[product_id] = product
            rows_by_product_id.setdefault(product_id, []).append(int(row.row_number or 0))

        for product_id, product in product_by_id.items():
            row_numbers = [item for item in rows_by_product_id.get(product_id, []) if int(item or 0) > 0]
            is_storable = self._is_product_storeable(product)
            if is_storable is None:
                issue = (
                    "Item ini tidak dapat divalidasi Internal Transfer karena metadata "
                    "Track Inventory by Quantity tidak tersedia."
                )
                if issue not in seen:
                    issues.append(issue)
                    seen[issue] = True
                for row_number in row_numbers:
                    self._append_row_issue(row_issues, row_number, issue)
            elif not is_storable:
                issue = "Item ini tidak dapat dijurnal Internal Transfer karena bukan Track Inventory by Quantity."
                if issue not in seen:
                    issues.append(issue)
                    seen[issue] = True
                for row_number in row_numbers:
                    self._append_row_issue(row_issues, row_number, issue)

            standard_price = product.standard_price
            if standard_price is None:
                issue = "Item ini Cost nya belum terisi, silakan diperbarui terlebih dahulu"
                if issue not in seen:
                    issues.append(issue)
                    seen[issue] = True
                for row_number in row_numbers:
                    self._append_row_issue(row_issues, row_number, issue)
                continue

            if abs(float(standard_price)) <= 1e-9:
                issue = "Item ini Cost nya masih 0, silakah diperbarui terlebih dahulu"
                if issue not in seen:
                    issues.append(issue)
                    seen[issue] = True
                for row_number in row_numbers:
                    self._append_row_issue(row_issues, row_number, issue)
                continue

            if float(standard_price) < 0.0:
                issue = "Item ini Cost nya bernilai negatif, silakan diperbarui terlebih dahulu"
                if issue not in seen:
                    issues.append(issue)
                    seen[issue] = True
                for row_number in row_numbers:
                    self._append_row_issue(row_issues, row_number, issue)

        return issues, row_issues

    async def _get_product_storeable_mode(self, company_id: int) -> str:
        if self._product_storeable_mode:
            return self._product_storeable_mode
        context = build_company_context(company_id)
        try:
            meta = await self.rpc.fields_get(
                model="product.product",
                attributes=["type"],
                context=context,
                stage="PREFETCH_PRODUCT",
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("Gagal fields_get product.product: %s", exc)
            self._product_storeable_mode = "none"
            return self._product_storeable_mode

        if isinstance(meta, dict) and isinstance(meta.get("is_storable"), dict):
            self._product_storeable_mode = "is_storable"
            return self._product_storeable_mode
        if isinstance(meta, dict) and isinstance(meta.get("detailed_type"), dict):
            self._product_storeable_mode = "detailed_type"
            return self._product_storeable_mode
        self._product_storeable_mode = "none"
        return self._product_storeable_mode

    async def _get_product_query_fields(self, company_id: int) -> List[str]:
        if self._product_query_fields is not None:
            return self._product_query_fields
        fields = ["id", "default_code", "display_name", "name", "uom_id", "company_id", "standard_price"]
        mode = await self._get_product_storeable_mode(company_id)
        if mode == "is_storable":
            fields.append("is_storable")
        elif mode == "detailed_type":
            fields.append("detailed_type")
        self._product_query_fields = fields
        return self._product_query_fields

    def _is_product_storeable(self, product: ProductInfo) -> bool | None:
        if product.is_storable is not None:
            return bool(product.is_storable)
        detailed_type = normalize_text(product.detailed_type).lower()
        if detailed_type:
            return detailed_type == "product"
        return None

    async def _prefetch_products(self, active_rows: List[ItemJournalRow], company_id: int) -> None:
        product_fields = await self._get_product_query_fields(company_id)
        id_requests = sorted(
            {
                int(item.prod_id_text)
                for item in active_rows
                if normalize_text(item.prod_id_text).isdigit() and int(item.prod_id_text) > 0
            }
        )

        async def fetch_id_chunk(id_chunk: List[int]) -> None:
            rows = await self.rpc.search_read(
                model="product.product",
                domain=[["id", "in", id_chunk], ["active", "=", True], ["company_id", "in", [False, company_id]]],
                fields=product_fields,
                context=build_company_context(company_id),
                stage="PREFETCH_PRODUCT",
            )
            self._cache_products(rows, company_id)
            found_ids = {int(rec.get("id") or 0) for rec in rows}
            for requested_id in id_chunk:
                cache_key = self._product_id_cache_key(requested_id, company_id)
                if requested_id not in found_ids and cache_key not in self.cache_product_by_id:
                    self.cache_product_by_id[cache_key] = ProductInfo(0, "", 0)

        await asyncio.gather(*(fetch_id_chunk(chunk) for chunk in chunked(id_requests, self.settings.prefetch_chunk_size)))

        requested_keys = sorted(
            {normalize_text(item.prod_key) for item in active_rows if not item.prod_id_text and normalize_text(item.prod_key)}
        )
        key_requests: List[str] = []
        for requested_key in requested_keys:
            cache_key = self._product_key_cache_key(requested_key, company_id)
            if cache_key in self.cache_product_by_key:
                continue
            cached_row = self._master_cache_get_product_record(
                model="product.product",
                company_id=company_id,
                code=requested_key,
                required_fields=product_fields,
            )
            if cached_row is None:
                key_requests.append(requested_key)
                continue
            self._cache_products([cached_row], company_id)
            if cache_key not in self.cache_product_by_key:
                key_requests.append(requested_key)

        async def fetch_key_chunk(key_chunk: List[str]) -> None:
            rows = await self.rpc.search_read(
                model="product.product",
                domain=[["default_code", "in", key_chunk], ["active", "=", True], ["company_id", "in", [False, company_id]]],
                fields=product_fields,
                context=build_company_context(company_id),
                stage="PREFETCH_PRODUCT",
            )
            self._cache_products(rows, company_id)
            found_codes = {normalize_text(rec.get("default_code")).lower() for rec in rows}
            for requested_key in key_chunk:
                cache_key = self._product_key_cache_key(requested_key, company_id)
                if requested_key.lower() not in found_codes and cache_key not in self.cache_product_by_key:
                    ilike_rows = await self.rpc.search_read(
                        model="product.product",
                        domain=[["default_code", "=ilike", requested_key], ["active", "=", True], ["company_id", "in", [False, company_id]]],
                        fields=product_fields,
                        limit=1,
                        context=build_company_context(company_id),
                        stage="PREFETCH_PRODUCT",
                    )
                    self._cache_products(ilike_rows, company_id)
                if cache_key not in self.cache_product_by_key:
                    self.cache_product_by_key[cache_key] = ProductInfo(0, "", 0)

        await asyncio.gather(*(fetch_key_chunk(chunk) for chunk in chunked(key_requests, self.settings.prefetch_chunk_size)))

    def _cache_products(self, product_rows: List[Dict[str, Any]], company_id: int) -> None:
        for rec in product_rows:
            prod_id = int(rec.get("id") or 0)
            if prod_id <= 0:
                continue
            prod_name = normalize_text(rec.get("display_name")) or normalize_text(rec.get("name"))
            uom_id = extract_many2one_id(rec.get("uom_id"))
            standard_price = _safe_float_or_none(rec.get("standard_price"))
            detailed_type = normalize_text(rec.get("detailed_type")).lower()
            raw_is_storable = rec.get("is_storable")
            is_storable: bool | None = None
            if isinstance(raw_is_storable, bool):
                is_storable = raw_is_storable
            elif raw_is_storable in {0, 1}:
                is_storable = bool(raw_is_storable)
            elif detailed_type:
                is_storable = detailed_type == "product"
            info = ProductInfo(
                product_id=prod_id,
                name=prod_name,
                uom_id=uom_id,
                standard_price=standard_price,
                is_storable=is_storable,
                detailed_type=detailed_type,
            )

            id_key = self._product_id_cache_key(prod_id, company_id)
            self.cache_product_by_id[id_key] = info

            code = normalize_text(rec.get("default_code"))
            if code:
                key_key = self._product_key_cache_key(code, company_id)
                if key_key not in self.cache_product_by_key or self.cache_product_by_key[key_key].product_id <= 0:
                    self.cache_product_by_key[key_key] = info
                self._master_cache_set_product_record(
                    model="product.product",
                    company_id=company_id,
                    row=rec,
                )

    async def _prefetch_uoms(self, active_rows: List[ItemJournalRow]) -> None:
        uom_names = sorted({normalize_text(item.uom) for item in active_rows if normalize_text(item.uom)})
        if not uom_names:
            return

        async def fetch_uom_chunk(name_chunk: List[str]) -> None:
            rows = await self.rpc.search_read(
                model="uom.uom",
                domain=[["name", "in", name_chunk]],
                fields=["id", "name"],
                stage="PREFETCH_UOM",
            )
            for rec in rows:
                name = normalize_text(rec.get("name")).lower()
                uom_id = int(rec.get("id") or 0)
                if name:
                    self.cache_uom[name] = uom_id
            for requested_name in name_chunk:
                key = requested_name.lower()
                if key not in self.cache_uom:
                    ilike_rows = await self.rpc.search_read(
                        model="uom.uom",
                        domain=[["name", "=ilike", requested_name]],
                        fields=["id", "name"],
                        limit=1,
                        stage="PREFETCH_UOM",
                    )
                    self.cache_uom[key] = int(ilike_rows[0].get("id") or 0) if ilike_rows else 0

        await asyncio.gather(*(fetch_uom_chunk(chunk) for chunk in chunked(uom_names, self.settings.prefetch_chunk_size)))

    async def _resolve_product_for_row(self, row: ItemJournalRow, company_id: int) -> tuple[ProductInfo | None, str]:
        self._emit_audit(stage="LOOKUP_PRODUCT", row_number=row.row_number, result="START", message=f"product_id={normalize_text(row.prod_id_text) or '-'} key={normalize_text(row.prod_key) or '-'}")
        product_fields = await self._get_product_query_fields(company_id)
        if normalize_text(row.prod_id_text):
            if not row.prod_id_text.isdigit():
                return None, f"Product ID harus angka > 0: {row.prod_id_text}"
            product_id = int(row.prod_id_text)
            if product_id <= 0:
                return None, f"Product ID harus angka > 0: {row.prod_id_text}"
            key = self._product_id_cache_key(product_id, company_id)
            info = self.cache_product_by_id.get(key)
            if info is None:
                rows = await self.rpc.search_read(
                    model="product.product",
                    domain=[["id", "in", [product_id]], ["active", "=", True], ["company_id", "in", [False, company_id]]],
                    fields=product_fields,
                    context=build_company_context(company_id),
                    limit=2,
                    stage="ROW_LOOP",
                    excel_row=row.row_number,
                )
                self._cache_products(rows, company_id)
                info = self.cache_product_by_id.get(key)
            if info is None or info.product_id <= 0:
                return None, f"Product ID tidak ditemukan: {product_id}"
            return info, ""

        product_key = normalize_text(row.prod_key)
        if not product_key:
            return None, "Product ID/Product key wajib diisi."
        key = self._product_key_cache_key(product_key, company_id)
        info = self.cache_product_by_key.get(key)
        if info is None:
            cached_row = self._master_cache_get_product_record(
                model="product.product",
                company_id=company_id,
                code=product_key,
                required_fields=product_fields,
            )
            if cached_row is not None:
                self._cache_products([cached_row], company_id)
                info = self.cache_product_by_key.get(key)
        if info is None:
            rows = await self.rpc.search_read(
                model="product.product",
                domain=[["default_code", "=", product_key], ["active", "=", True], ["company_id", "in", [False, company_id]]],
                fields=product_fields,
                context=build_company_context(company_id),
                limit=1,
                stage="ROW_LOOP",
                excel_row=row.row_number,
            )
            self._cache_products(rows, company_id)
            info = self.cache_product_by_key.get(key)
            if info is None:
                rows = await self.rpc.search_read(
                    model="product.product",
                    domain=[["default_code", "=ilike", product_key], ["active", "=", True], ["company_id", "in", [False, company_id]]],
                    fields=product_fields,
                    context=build_company_context(company_id),
                    limit=1,
                    stage="ROW_LOOP",
                    excel_row=row.row_number,
                )
                self._cache_products(rows, company_id)
                info = self.cache_product_by_key.get(key)
        if info is None or info.product_id <= 0:
            return None, f"Product tidak ditemukan: {product_key}"
        return info, ""

    async def _resolve_uom_id(self, uom_name: str, fallback_uom_id: int) -> int:
        clean_name = normalize_text(uom_name)
        if clean_name:
            self._emit_audit(stage="LOOKUP_UOM", result="START", message=f"uom={clean_name}")
            key = clean_name.lower()
            if key in self.cache_uom:
                return self.cache_uom[key]
            rows = await self.rpc.search_read(model="uom.uom", domain=[["name", "=", clean_name]], fields=["id"], limit=1, stage="ROW_LOOP")
            if rows:
                uom_id = int(rows[0].get("id") or 0)
                self.cache_uom[key] = uom_id
                return uom_id
            rows = await self.rpc.search_read(model="uom.uom", domain=[["name", "=ilike", clean_name]], fields=["id"], limit=1, stage="ROW_LOOP")
            uom_id = int(rows[0].get("id") or 0) if rows else 0
            self.cache_uom[key] = uom_id
            return uom_id
        return fallback_uom_id

    def _product_id_cache_key(self, product_id: int, company_id: int) -> str:
        return f"{product_id}|{company_id}"

    def _product_key_cache_key(self, product_key: str, company_id: int) -> str:
        return f"{normalize_text(product_key).lower()}|{company_id}"
