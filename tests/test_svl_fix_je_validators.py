import logging
import unittest

from smartscc_tools.features.svl_fix_je.models import SvlFixJeExcelRow
from smartscc_tools.features.svl_fix_je.validators import validate_row


class _FakeRpc:
    def __init__(self) -> None:
        self.fields_map = {
            "account.account": {"company_ids": {"type": "many2many"}},
            "stock.valuation.layer": {"account_move_id": {"readonly": False}},
        }
        self.svls: dict[int, dict] = {}
        self.accounts: list[dict] = []
        self.journals: list[dict] = []

    async def fields_get(self, model, attributes=None, context=None, stage=""):  # noqa: ANN001
        return self.fields_map.get(model, {})

    async def read(self, model, ids, fields=None, context=None, stage="", excel_row=0):  # noqa: ANN001
        if model == "stock.valuation.layer":
            return [self.svls[item] for item in ids if item in self.svls]
        return []

    async def search_read(self, model, domain, fields=None, limit=None, context=None, stage="", order=None):  # noqa: ANN001
        records = self.accounts if model == "account.account" else self.journals
        code_eq = None
        code_in = None
        code_prefix = None
        company_id = None
        for field_name, operator, value in domain:
            if field_name == "code" and operator == "=":
                code_eq = value
            elif field_name == "code" and operator == "in":
                code_in = set(value)
            elif field_name == "code" and operator == "=like":
                code_prefix = str(value).replace("%", "")
            elif field_name in {"company_ids", "company_id"}:
                company_id = value[0] if isinstance(value, list) else value

        matched = []
        for record in records:
            code = record.get("code")
            if code_eq is not None and code != code_eq:
                continue
            if code_in is not None and code not in code_in:
                continue
            if code_prefix is not None and not str(code).startswith(code_prefix):
                continue
            if company_id is not None:
                company_values = record.get("company_ids") or []
                if isinstance(company_values, int):
                    company_values = [company_values]
                if company_id not in company_values and record.get("company_id") != company_id:
                    continue
            matched.append(record)

        if limit:
            matched = matched[:limit]
        return matched


def _base_svl(account_move_id=None, value=500.0, company_id=1) -> dict:
    return {
        "id": 5139103,
        "description": "WCGT/IN/00892 - Receipt",
        "product_id": [101, "Demo Product"],
        "quantity": 20.0,
        "value": value,
        "unit_cost": 25.0,
        "account_move_id": account_move_id,
        "stock_move_id": [91, "MOVE/91"],
        "remaining_qty": 0.0,
        "remaining_value": 0.0,
        "company_id": [company_id, "HWG"],
        "reference": "WCGT/IN/00892",
        "product_default_code": "F-FHVF-0167",
        "uom_name": "Units",
    }


def _base_row() -> SvlFixJeExcelRow:
    return SvlFixJeExcelRow(
        row_number=2,
        svl_id=5139103,
        svl_ref="WCGT/IN/00892",
        default_code="F-FHVF-0167",
        qty=20.0,
        uom="Units",
        unit_cost=25.0,
        total_value=500.0,
        coa_credit="1.1.03.01",
        coa_debit="5.1.01.01",
        journal_code="STJ",
        je_date="2026-01-07",
        note="",
    )


class SvlFixJeValidatorsTest(unittest.IsolatedAsyncioTestCase):
    async def test_validate_row_rejects_missing_svl(self) -> None:
        rpc = _FakeRpc()
        cache = {"svls": {}, "accounts": {}, "journals": {}, "account_company_field": "company_ids"}

        valid, validated, errors = await validate_row(rpc, _base_row(), cache)

        self.assertFalse(valid)
        self.assertIsNone(validated)
        self.assertIn("tidak ditemukan di Odoo", errors[0])

    async def test_validate_row_rejects_existing_journal_entry(self) -> None:
        rpc = _FakeRpc()
        rpc.svls[5139103] = _base_svl(account_move_id=[999, "MOVE/999"])
        rpc.accounts = [
            {"id": 10, "code": "1.1.03.01", "name": "Persediaan", "company_ids": [1]},
            {"id": 11, "code": "5.1.01.01", "name": "Selisih", "company_ids": [1]},
        ]
        rpc.journals = [{"id": 12, "code": "STJ", "name": "Stock Journal", "company_id": 1}]
        cache = {"svls": {5139103: rpc.svls[5139103]}, "accounts": {}, "journals": {}, "account_company_field": "company_ids"}

        valid, _validated, errors = await validate_row(rpc, _base_row(), cache)

        self.assertFalse(valid)
        self.assertTrue(any("sudah punya Journal Entry" in item for item in errors))

    async def test_validate_row_rejects_company_scoped_missing_coa(self) -> None:
        rpc = _FakeRpc()
        rpc.svls[5139103] = _base_svl(company_id=2)
        rpc.accounts = [{"id": 11, "code": "5.1.01.01", "name": "Selisih", "company_ids": [1]}]
        rpc.journals = [{"id": 12, "code": "STJ", "name": "Stock Journal", "company_id": 2}]
        cache = {"svls": {5139103: rpc.svls[5139103]}, "accounts": {}, "journals": {}, "account_company_field": "company_ids"}

        valid, _validated, errors = await validate_row(rpc, _base_row(), cache, logger=logging.getLogger("test"))

        self.assertFalse(valid)
        self.assertTrue(any("COA '1.1.03.01' tidak ditemukan" in item for item in errors))

    async def test_validate_row_rejects_missing_journal_for_company(self) -> None:
        rpc = _FakeRpc()
        rpc.svls[5139103] = _base_svl()
        rpc.accounts = [
            {"id": 10, "code": "1.1.03.01", "name": "Persediaan", "company_ids": [1]},
            {"id": 11, "code": "5.1.01.01", "name": "Selisih", "company_ids": [1]},
        ]
        cache = {"svls": {5139103: rpc.svls[5139103]}, "accounts": {}, "journals": {}, "account_company_field": "company_ids"}

        valid, _validated, errors = await validate_row(rpc, _base_row(), cache)

        self.assertFalse(valid)
        self.assertTrue(any("Journal 'STJ' tidak ditemukan" in item for item in errors))

    async def test_validate_row_rejects_cross_check_mismatch(self) -> None:
        rpc = _FakeRpc()
        rpc.svls[5139103] = _base_svl()
        rpc.accounts = [
            {"id": 10, "code": "1.1.03.01", "name": "Persediaan", "company_ids": [1]},
            {"id": 11, "code": "5.1.01.01", "name": "Selisih", "company_ids": [1]},
        ]
        rpc.journals = [{"id": 12, "code": "STJ", "name": "Stock Journal", "company_id": 1}]
        row = _base_row()
        row.default_code = "WRONG"
        cache = {"svls": {5139103: rpc.svls[5139103]}, "accounts": {}, "journals": {}, "account_company_field": "company_ids"}

        valid, _validated, errors = await validate_row(rpc, row, cache)

        self.assertFalse(valid)
        self.assertTrue(any("default_code mismatch" in item for item in errors))

    async def test_validate_row_rejects_zero_value(self) -> None:
        rpc = _FakeRpc()
        rpc.svls[5139103] = _base_svl(value=0.0)
        rpc.accounts = [
            {"id": 10, "code": "1.1.03.01", "name": "Persediaan", "company_ids": [1]},
            {"id": 11, "code": "5.1.01.01", "name": "Selisih", "company_ids": [1]},
        ]
        rpc.journals = [{"id": 12, "code": "STJ", "name": "Stock Journal", "company_id": 1}]
        cache = {"svls": {5139103: rpc.svls[5139103]}, "accounts": {}, "journals": {}, "account_company_field": "company_ids"}

        valid, _validated, errors = await validate_row(rpc, _base_row(), cache)

        self.assertFalse(valid)
        self.assertTrue(any("Total Value SVL = 0" in item for item in errors))

    async def test_validate_row_rejects_bad_date_format(self) -> None:
        rpc = _FakeRpc()
        rpc.svls[5139103] = _base_svl()
        rpc.accounts = [
            {"id": 10, "code": "1.1.03.01", "name": "Persediaan", "company_ids": [1]},
            {"id": 11, "code": "5.1.01.01", "name": "Selisih", "company_ids": [1]},
        ]
        rpc.journals = [{"id": 12, "code": "STJ", "name": "Stock Journal", "company_id": 1}]
        row = _base_row()
        row.je_date = "07/01/2026"
        cache = {"svls": {5139103: rpc.svls[5139103]}, "accounts": {}, "journals": {}, "account_company_field": "company_ids"}

        valid, _validated, errors = await validate_row(rpc, row, cache)

        self.assertFalse(valid)
        self.assertTrue(any("Format tanggal salah" in item for item in errors))
