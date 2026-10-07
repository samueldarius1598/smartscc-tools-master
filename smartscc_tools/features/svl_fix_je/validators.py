"""Validation helpers for SVL Fix JE async service."""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime
from typing import Any, Iterable

from smartscc_tools.services.odoo.gateway import build_company_context
from smartscc_tools.features.item_journal.utils import chunked, normalize_text

from .config import DEFAULT_JOURNAL_CODE
from .models import SvlFixJeExcelRow, SvlFixJeValidatedRow


def float_matches(left: float, right: float, tolerance: float = 0.01) -> bool:
    return abs(float(left) - float(right)) <= tolerance


def extract_reference_from_description(description: str | None) -> str:
    text = normalize_text(description)
    if not text:
        return ""
    if " - " in text:
        return normalize_text(text.split(" - ", 1)[0])
    return text


async def detect_account_company_field(rpc: Any) -> str:
    fields = await rpc.fields_get(
        "account.account",
        attributes=["type", "store", "readonly"],
        stage="SVL_FIX_ACCOUNT_FIELDS",
    )
    if "company_ids" in fields:
        return "company_ids"
    if "company_id" in fields:
        return "company_id"
    return ""


async def detect_svl_link_field_supported(rpc: Any) -> bool:
    fields = await rpc.fields_get(
        "stock.valuation.layer",
        attributes=["readonly", "type"],
        stage="SVL_FIX_SVL_FIELDS",
    )
    return "account_move_id" in fields


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


def _company_domain(field_name: str, company_id: int | None) -> list[Any]:
    if not company_id or not field_name:
        return []
    if field_name == "company_ids":
        return [(field_name, "in", [company_id])]
    return [(field_name, "=", company_id)]


async def _enrich_svl_records(
    rpc: Any,
    svl_records: dict[int, dict[str, Any]],
    *,
    chunk_size: int = 500,
) -> dict[int, dict[str, Any]]:
    move_ids = sorted(
        {
            _many2one_id(record.get("stock_move_id"))
            for record in svl_records.values()
            if _many2one_id(record.get("stock_move_id")) > 0
        }
    )
    product_ids = sorted(
        {
            _many2one_id(record.get("product_id"))
            for record in svl_records.values()
            if _many2one_id(record.get("product_id")) > 0
        }
    )

    move_map: dict[int, dict[str, Any]] = {}
    for ids in chunked(move_ids, chunk_size):
        for move in await rpc.read(
            "stock.move",
            ids,
            fields=["id", "reference"],
            stage="SVL_FIX_PREFETCH_MOVE",
        ):
            move_map[int(move["id"])] = move

    product_map: dict[int, dict[str, Any]] = {}
    for ids in chunked(product_ids, chunk_size):
        for product in await rpc.read(
            "product.product",
            ids,
            fields=["id", "default_code", "uom_id"],
            stage="SVL_FIX_PREFETCH_PRODUCT",
        ):
            product_map[int(product["id"])] = product

    for svl in svl_records.values():
        svl["reference"] = extract_reference_from_description(svl.get("description"))
        svl["product_default_code"] = ""
        svl["uom_name"] = ""

        move_id = _many2one_id(svl.get("stock_move_id"))
        if move_id and move_id in move_map:
            reference = normalize_text(move_map[move_id].get("reference"))
            if reference:
                svl["reference"] = reference

        product_id = _many2one_id(svl.get("product_id"))
        if product_id and product_id in product_map:
            product = product_map[product_id]
            svl["product_default_code"] = normalize_text(product.get("default_code"))
            uom = product.get("uom_id")
            if isinstance(uom, (list, tuple)) and len(uom) > 1:
                svl["uom_name"] = normalize_text(uom[1])

    return svl_records


async def prefetch_svls(
    rpc: Any,
    svl_ids: Iterable[int],
    *,
    chunk_size: int = 500,
) -> dict[int, dict[str, Any] | None]:
    unique_ids = sorted({int(svl_id) for svl_id in svl_ids if int(svl_id) > 0})
    cache: dict[int, dict[str, Any] | None] = {svl_id: None for svl_id in unique_ids}
    if not unique_ids:
        return cache

    svl_records: dict[int, dict[str, Any]] = {}
    for ids in chunked(unique_ids, chunk_size):
        records = await rpc.read(
            "stock.valuation.layer",
            ids,
            fields=[
                "id",
                "description",
                "product_id",
                "quantity",
                "value",
                "unit_cost",
                "account_move_id",
                "stock_move_id",
                "remaining_qty",
                "remaining_value",
                "company_id",
            ],
            stage="SVL_FIX_PREFETCH_SVL",
        )
        for record in records:
            svl_records[int(record["id"])] = record

    enriched = await _enrich_svl_records(rpc, svl_records, chunk_size=chunk_size)
    for svl_id in unique_ids:
        cache[svl_id] = enriched.get(svl_id)
    return cache


async def find_svl_by_id(rpc: Any, svl_id: int) -> dict[str, Any] | None:
    records = await rpc.read(
        "stock.valuation.layer",
        [svl_id],
        fields=[
            "id",
            "description",
            "product_id",
            "quantity",
            "value",
            "unit_cost",
            "account_move_id",
            "stock_move_id",
            "remaining_qty",
            "remaining_value",
            "company_id",
        ],
        stage="SVL_FIX_FIND_SVL",
        excel_row=0,
    )
    if not records:
        return None
    enriched = await _enrich_svl_records(rpc, {svl_id: records[0]}, chunk_size=1)
    return enriched.get(svl_id)


async def find_account_id(
    rpc: Any,
    code: str,
    company_id: int | None,
    *,
    company_field_name: str,
    logger: logging.Logger | None = None,
) -> int | None:
    domain: list[Any] = [("code", "=", code)]
    domain.extend(_company_domain(company_field_name, company_id))
    context = build_company_context(int(company_id or 0))
    results = await rpc.search_read(
        "account.account",
        domain,
        fields=["id", "code", "name"],
        limit=1,
        context=context,
        stage="SVL_FIX_FIND_ACCOUNT",
    )
    if results:
        return int(results[0]["id"])

    fallback_domain: list[Any] = [("code", "=like", f"{code}%")]
    fallback_domain.extend(_company_domain(company_field_name, company_id))
    results = await rpc.search_read(
        "account.account",
        fallback_domain,
        fields=["id", "code", "name"],
        limit=1,
        context=context,
        stage="SVL_FIX_FIND_ACCOUNT_PREFIX",
    )
    if results:
        if logger is not None:
            logger.warning(
                "COA '%s' exact match tidak ditemukan, menggunakan '%s' - %s",
                code,
                results[0]["code"],
                results[0]["name"],
            )
        return int(results[0]["id"])
    return None


async def prefetch_accounts(
    rpc: Any,
    account_pairs: Iterable[tuple[str, int | None]],
    *,
    company_field_name: str,
    logger: logging.Logger | None = None,
) -> dict[tuple[str, int | None], int | None]:
    grouped: dict[int | None, set[str]] = defaultdict(set)
    for code, company_id in account_pairs:
        if normalize_text(code):
            grouped[company_id].add(normalize_text(code))

    cache: dict[tuple[str, int | None], int | None] = {}
    for company_id, codes in grouped.items():
        domain: list[Any] = [("code", "in", sorted(codes))]
        domain.extend(_company_domain(company_field_name, company_id))
        context = build_company_context(int(company_id or 0))
        exact_matches = await rpc.search_read(
            "account.account",
            domain,
            fields=["id", "code", "name"],
            context=context,
            stage="SVL_FIX_PREFETCH_ACCOUNT",
        )
        matched_codes = {normalize_text(record["code"]) for record in exact_matches}
        for record in exact_matches:
            cache[(normalize_text(record["code"]), company_id)] = int(record["id"])

        for code in codes - matched_codes:
            cache[(code, company_id)] = await find_account_id(
                rpc,
                code,
                company_id,
                company_field_name=company_field_name,
                logger=logger,
            )
    return cache


async def find_journal_id(rpc: Any, code: str, company_id: int | None) -> int | None:
    domain: list[Any] = [("code", "=", code)]
    if company_id:
        domain.append(("company_id", "=", company_id))
    results = await rpc.search_read(
        "account.journal",
        domain,
        fields=["id", "code", "name"],
        limit=1,
        context=build_company_context(int(company_id or 0)),
        stage="SVL_FIX_FIND_JOURNAL",
    )
    if results:
        return int(results[0]["id"])
    return None


async def prefetch_journals(
    rpc: Any,
    journal_pairs: Iterable[tuple[str, int | None]],
) -> dict[tuple[str, int | None], int | None]:
    grouped: dict[int | None, set[str]] = defaultdict(set)
    for code, company_id in journal_pairs:
        if normalize_text(code):
            grouped[company_id].add(normalize_text(code))

    cache: dict[tuple[str, int | None], int | None] = {}
    for company_id, codes in grouped.items():
        domain: list[Any] = [("code", "in", sorted(codes))]
        if company_id:
            domain.append(("company_id", "=", company_id))
        records = await rpc.search_read(
            "account.journal",
            domain,
            fields=["id", "code", "name"],
            context=build_company_context(int(company_id or 0)),
            stage="SVL_FIX_PREFETCH_JOURNAL",
        )
        for record in records:
            cache[(normalize_text(record["code"]), company_id)] = int(record["id"])
        for code in codes:
            cache.setdefault((code, company_id), None)
    return cache


async def validate_row(
    rpc: Any,
    row: SvlFixJeExcelRow,
    cache: dict[str, Any],
    *,
    default_journal_code: str = DEFAULT_JOURNAL_CODE,
    logger: logging.Logger | None = None,
) -> tuple[bool, SvlFixJeValidatedRow | None, list[str]]:
    errors: list[str] = []

    svl_record = cache["svls"].get(row.svl_id)
    if row.svl_id not in cache["svls"]:
        svl_record = await find_svl_by_id(rpc, row.svl_id)
        cache["svls"][row.svl_id] = svl_record

    if not svl_record:
        errors.append(f"SVL ID {row.svl_id} tidak ditemukan di Odoo")
        return False, None, errors

    company_id = _many2one_id(svl_record.get("company_id"))
    company_name = ""
    if isinstance(svl_record.get("company_id"), (list, tuple)) and len(svl_record["company_id"]) > 1:
        company_name = normalize_text(svl_record["company_id"][1])
    if company_id <= 0:
        errors.append(f"SVL ID {row.svl_id} tidak punya company_id")

    actual_ref = normalize_text(svl_record.get("reference"))
    actual_code = normalize_text(svl_record.get("product_default_code"))
    actual_qty = float(svl_record.get("quantity") or 0)
    actual_unit_cost = float(svl_record.get("unit_cost") or 0)
    actual_total_value = float(svl_record.get("value") or 0)
    actual_uom = normalize_text(svl_record.get("uom_name"))

    if row.svl_ref and row.svl_ref != actual_ref:
        errors.append(f"SVL Reference mismatch: Excel='{row.svl_ref}' vs Odoo='{actual_ref}'")
    if row.default_code and row.default_code != actual_code:
        errors.append(f"default_code mismatch: Excel='{row.default_code}' vs Odoo='{actual_code}'")
    if row.qty is not None and not float_matches(row.qty, actual_qty):
        errors.append(f"Qty mismatch: Excel={row.qty} vs Odoo={actual_qty}")
    if row.unit_cost is not None and not float_matches(row.unit_cost, actual_unit_cost):
        errors.append(f"Unit Cost mismatch: Excel={row.unit_cost} vs Odoo={actual_unit_cost}")
    if row.total_value is not None and not float_matches(row.total_value, actual_total_value):
        errors.append(f"Total Value mismatch: Excel={row.total_value} vs Odoo={actual_total_value}")
    if row.uom and actual_uom and row.uom.strip().lower() != actual_uom.strip().lower():
        errors.append(f"UoM mismatch: Excel='{row.uom}' vs Odoo='{actual_uom}'")
    if svl_record.get("account_move_id"):
        errors.append(
            f"SVL ID {row.svl_id} sudah punya Journal Entry (move_id={_many2one_id(svl_record['account_move_id'])})"
        )

    row.svl_ref = actual_ref
    row.default_code = actual_code
    row.qty = actual_qty
    row.unit_cost = actual_unit_cost
    row.total_value = actual_total_value
    row.uom = actual_uom or row.uom

    if row.total_value == 0:
        errors.append("Total Value SVL = 0, tidak ada yang perlu dijurnal")

    if not normalize_text(row.coa_credit):
        errors.append("COA Persediaan (Credit) kosong")
        credit_account_id = 0
    else:
        account_key = (normalize_text(row.coa_credit), company_id)
        if account_key not in cache["accounts"]:
            cache["accounts"][account_key] = await find_account_id(
                rpc,
                row.coa_credit,
                company_id,
                company_field_name=cache["account_company_field"],
                logger=logger,
            )
        credit_account_id = int(cache["accounts"][account_key] or 0)
        if credit_account_id <= 0:
            errors.append(
                f"COA '{row.coa_credit}' tidak ditemukan di Odoo untuk company '{company_name or '-'}'"
            )

    if not normalize_text(row.coa_debit):
        errors.append("COA Debit (Selisih HPP) kosong")
        debit_account_id = 0
    else:
        account_key = (normalize_text(row.coa_debit), company_id)
        if account_key not in cache["accounts"]:
            cache["accounts"][account_key] = await find_account_id(
                rpc,
                row.coa_debit,
                company_id,
                company_field_name=cache["account_company_field"],
                logger=logger,
            )
        debit_account_id = int(cache["accounts"][account_key] or 0)
        if debit_account_id <= 0:
            errors.append(
                f"COA '{row.coa_debit}' tidak ditemukan di Odoo untuk company '{company_name or '-'}'"
            )

    row.journal_code = normalize_text(row.journal_code) or normalize_text(default_journal_code)
    journal_key = (row.journal_code, company_id)
    if journal_key not in cache["journals"]:
        cache["journals"][journal_key] = await find_journal_id(rpc, row.journal_code, company_id)
    journal_id = int(cache["journals"][journal_key] or 0)
    if journal_id <= 0:
        errors.append(
            f"Journal '{row.journal_code}' tidak ditemukan di Odoo untuk company '{company_name or '-'}'"
        )

    try:
        datetime.strptime(row.je_date, "%Y-%m-%d")
    except ValueError:
        errors.append(f"Format tanggal salah: '{row.je_date}' (harus YYYY-MM-DD)")

    validated = SvlFixJeValidatedRow(
        row=row,
        svl_record=svl_record,
        company_id=company_id,
        company_name=company_name,
        product_id=_many2one_id(svl_record.get("product_id")),
        credit_account_id=credit_account_id,
        debit_account_id=debit_account_id,
        journal_id=journal_id,
    )
    return len(errors) == 0, validated, errors
