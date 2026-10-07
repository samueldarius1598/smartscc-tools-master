import asyncio
import logging
from datetime import date
from typing import Any
import unittest

from smartscc_tools.features.item_journal.utils import normalize_text
from smartscc_tools.features.svl_fix_je.config import DEFAULT_REF_PREFIX
from smartscc_tools.features.svl_fix_je.dashboard_repair_service import SvlDashboardRepairServiceAsync
from smartscc_tools.features.svl_fix_je.models import (
    SvlDashboardPcbCase1RepairRequest,
    SvlDashboardPcbCase1RepairRow,
    SvlDashboardPcbCase2RepairRequest,
    SvlDashboardPcbCase2RepairRow,
    SvlDashboardPcbRepairPlannedLine,
    SvlDashboardRepairRequest,
    SvlDashboardRepairRow,
    SvlDashboardRepairRowResult,
)


PCB_CASE1_LABEL = "Case 1 - STJ Bill Miss Match (Clearing - Suspend)"


def _pcb_case1_reference(
    *,
    prefix: str = DEFAULT_REF_PREFIX,
    picking_name: str = "",
    bill_name: str = "",
    item_code: str = "",
    item_name: str = "",
) -> str:
    item_label = " - ".join(part for part in (normalize_text(item_code), normalize_text(item_name)) if part)
    body = " / ".join(part for part in (normalize_text(picking_name), normalize_text(bill_name), item_label) if part)
    return f"{prefix}: Purchase Cycle Balance: {body}" if body else f"{prefix}: Purchase Cycle Balance"


def _pcb_case1_line_label(*, item_code: str = "", item_name: str = "") -> str:
    return " - ".join(part for part in (normalize_text(item_code), normalize_text(item_name), PCB_CASE1_LABEL) if part)


class _FakeRepairRpc:
    def __init__(self) -> None:
        self.login_calls = 0
        self.fail_relink = False
        self.fail_post = False
        self.fail_to_draft = False
        self.fail_move_line_write = False
        self.preserve_null_residual_after_heal = False
        self.assign_name_on_post_only = False
        self.zero_move_line_amounts_on_create = False
        self.lock_date = ""
        self.partial_reconcile_error: str = ""
        self.partial_reconcile_create_delay = 0.0
        self.next_move_id = 900
        self.next_move_line_id = 3000
        self.next_partial_reconcile_id = 1
        self.execute_calls: list[tuple[str, list[int]]] = []
        self.search_read_calls: list[tuple[str, list, Any, Any, Any, str]] = []
        self.fields_map = {
            "account.account": {
                "company_ids": {"type": "many2many"},
                "code": {"type": "char"},
                "name": {"type": "char"},
            },
            "account.move": {
                "company_id": {"type": "many2one"},
                "journal_id": {"type": "many2one"},
                "date": {"type": "date"},
                "ref": {"type": "char"},
                "name": {"type": "char"},
                "state": {"type": "selection"},
                "move_type": {"type": "selection"},
                "partner_id": {"type": "many2one"},
                "invoice_origin": {"type": "char"},
            },
            "account.move.line": {
                "move_id": {"type": "many2one"},
                "account_id": {"type": "many2one"},
                "partner_id": {"type": "many2one"},
                "product_id": {"type": "many2one"},
                "product_uom_id": {"type": "many2one"},
                "quantity": {"type": "float"},
                "purchase_line_id": {"type": "many2one"},
                "currency_id": {"type": "many2one"},
                "amount_currency": {"type": "float"},
                "amount_residual": {"type": "float"},
                "amount_residual_currency": {"type": "float"},
                "analytic_distribution": {"type": "json"},
                "stock_move_id": {"type": "many2one"},
                "picking_id": {"type": "many2one"},
                "bill_line_id": {"type": "many2one"},
                "bill_move_id": {"type": "many2one"},
                "debit": {"type": "float"},
                "credit": {"type": "float"},
                "balance": {"type": "float"},
                "reconciled": {"type": "boolean"},
            },
            "account.partial.reconcile": {
                "debit_move_id": {"type": "many2one"},
                "credit_move_id": {"type": "many2one"},
                "amount": {"type": "float"},
                "currency_id": {"type": "many2one"},
                "amount_currency": {"type": "float"},
            },
        }
        self.accounts = [
            {"id": 101, "code": "114001", "name": "Persediaan Barang", "company_ids": [1]},
            {"id": 202, "code": "510001", "name": "COGS", "company_ids": [1]},
            {"id": 203, "code": "5101010", "name": "COGS Variance", "company_ids": [1]},
            {"id": 301, "code": "2103006", "name": "Hutang Suspend", "company_ids": [1]},
            {"id": 302, "code": "1108099", "name": "Clearing", "company_ids": [1]},
        ]
        self.journals = [
            {"id": 81, "code": "STJ", "name": "Stock Journal", "company_id": 1},
        ]
        self.partners = [
            {"id": 77, "name": "Vendor Alpha"},
        ]
        self.moves = {
            500: {
                "id": 500,
                "name": "STJ/2026/0500",
                "state": "draft",
                "company_id": 1,
                "journal_id": [81, "Stock Journal"],
                "date": "2026-01-11",
                "ref": "",
                "partner_id": 77,
                "line_ids": [],
            },
            501: {
                "id": 501,
                "name": "STJ/2026/0501",
                "state": "posted",
                "company_id": 1,
                "journal_id": [81, "Stock Journal"],
                "date": "2026-01-11",
                "ref": "",
                "partner_id": 77,
                "line_ids": [],
            },
        }
        self.move_line_rows: dict[int, dict] = {}
        self.partial_reconciles: dict[int, dict] = {}
        self.svls = {
            1001: {"id": 1001, "account_move_id": [501, "STJ/2026/0501"]},
            1002: {"id": 1002, "account_move_id": False},
        }

    async def ensure_login(self) -> int:
        self.login_calls += 1
        return 7

    async def fields_get(self, model, attributes=None, context=None, stage=""):  # noqa: ANN001
        return self.fields_map.get(model, {})

    async def search_read(self, model, domain, fields=None, limit=None, context=None, stage="", order=None):  # noqa: ANN001
        self.search_read_calls.append((model, domain, fields, limit, context, stage))
        if model == "account.account":
            rows = [row.copy() for row in self.accounts if self._matches(row, domain)]
        elif model == "account.journal":
            rows = [row.copy() for row in self.journals if self._matches(row, domain)]
        elif model == "account.move":
            rows = [row.copy() for row in self.moves.values() if self._matches(row, domain)]
        elif model == "account.change.lock.date":
            rows = [{"id": 1, "fiscalyear_lock_date": self.lock_date}] if self.lock_date else []
        elif model == "account.move.line":
            rows = [row.copy() for row in self.move_line_rows.values() if self._matches(row, domain)]
        elif model == "res.partner":
            rows = [row.copy() for row in self.partners if self._matches(row, domain)]
        else:
            rows = []
        if limit:
            rows = rows[:limit]
        return rows

    async def read(self, model, ids, fields=None, context=None, stage=""):  # noqa: ANN001
        if model == "account.move":
            return [self.moves[record_id].copy() for record_id in ids if record_id in self.moves]
        if model == "res.company":
            return [{"id": record_id, "fiscalyear_lock_date": self.lock_date} for record_id in ids]
        return []

    async def write(self, model, ids, values, context=None, stage=""):  # noqa: ANN001
        if model == "account.move":
            for record_id in ids:
                move = self.moves.get(record_id)
                if move is None:
                    return False
                if "date" in values:
                    move["date"] = values["date"]
                if "ref" in values:
                    move["ref"] = values["ref"]
                if "line_ids" in values:
                    for command in values["line_ids"]:
                        if not isinstance(command, (list, tuple)) or not command:
                            continue
                        operator = int(command[0] or 0)
                        if operator == 5:
                            self._clear_move_line_records(record_id)
                        elif operator == 0 and len(command) >= 3:
                            line_values = dict(command[2])
                            self._append_move_line_record(record_id, line_values)
                    self._sync_move_line_cache(record_id)
            return True
        if model == "stock.valuation.layer":
            if self.fail_relink:
                return False
            move_id = int(values.get("account_move_id") or 0)
            move_name = self.moves.get(move_id, {}).get("name", "")
            for record_id in ids:
                if record_id not in self.svls:
                    return False
                self.svls[record_id]["account_move_id"] = [move_id, move_name]
            return True
        if model == "account.move.line":
            if self.fail_move_line_write:
                raise RuntimeError("account.move.line.write blocked")
            for record_id in ids:
                line = self.move_line_rows.get(record_id)
                if line is None:
                    return False
                for field_name, value in values.items():
                    line[field_name] = value
                if "amount_currency" in values and line.get("amount_residual_currency") in (None, False, ""):
                    if not self.preserve_null_residual_after_heal:
                        sign_source = float(line.get("balance") or 0.0)
                        amount_currency = abs(float(line.get("amount_currency") or 0.0))
                        line["amount_residual_currency"] = -amount_currency if sign_source < 0 else amount_currency
                if "amount_currency" in values and line.get("amount_residual") in (None, False, ""):
                    if not self.preserve_null_residual_after_heal:
                        line["amount_residual"] = float(line.get("balance") or 0.0)
            return True
        return False

    async def create(self, model, values, context=None, stage=""):  # noqa: ANN001
        if model == "account.move":
            self.next_move_id += 1
            move_id = self.next_move_id
            journal = next((row for row in self.journals if int(row["id"]) == int(values.get("journal_id") or 0)), None)
            journal_code = journal["code"] if journal is not None else "STJ"
            line_values = [dict(command[2]) for command in values.get("line_ids", []) if len(command) >= 3]
            self.moves[move_id] = {
                "id": move_id,
                "name": "/" if self.assign_name_on_post_only else f"{journal_code}/2026/{move_id:04d}",
                "state": "draft",
                "company_id": int(values.get("company_id") or 0),
                "journal_id": [int(values.get("journal_id") or 0), journal["name"] if journal is not None else journal_code],
                "date": values.get("date"),
                "ref": values.get("ref", ""),
                "partner_id": int(values.get("partner_id") or 0),
                "line_ids": [],
            }
            for line_value in line_values:
                if self.zero_move_line_amounts_on_create:
                    line_value["debit"] = 0.0
                    line_value["credit"] = 0.0
                    if "amount_currency" in line_value:
                        line_value["amount_currency"] = 0.0
                self._append_move_line_record(move_id, line_value)
            self._sync_move_line_cache(move_id)
            return move_id
        if model == "account.partial.reconcile":
            if self.partial_reconcile_create_delay > 0:
                await asyncio.sleep(self.partial_reconcile_create_delay)
            if normalize_text(self.partial_reconcile_error):
                raise RuntimeError(self.partial_reconcile_error)
            reconcile_id = self.next_partial_reconcile_id
            self.next_partial_reconcile_id += 1
            self.partial_reconciles[reconcile_id] = dict(values, id=reconcile_id)
            debit_move_id = int(values.get("debit_move_id") or 0)
            credit_move_id = int(values.get("credit_move_id") or 0)
            self._apply_partial_reconcile_line_effect(
                debit_move_id,
                amount=float(values.get("amount") or 0.0),
                amount_currency=values.get("amount_currency"),
            )
            self._apply_partial_reconcile_line_effect(
                credit_move_id,
                amount=float(values.get("amount") or 0.0),
                amount_currency=values.get("amount_currency"),
            )
            return reconcile_id
        return 0

    async def execute_kw(self, model, method, args, kwargs=None, stage="", mutating=False):  # noqa: ANN001
        if model != "account.move":
            raise AssertionError(f"Unsupported model {model}")
        move_ids = [int(item) for item in list(args[0] or [])]
        self.execute_calls.append((method, move_ids))
        if method in {"button_draft", "action_draft"}:
            if self.fail_to_draft:
                raise RuntimeError("button_draft blocked")
            for move_id in move_ids:
                self.moves[move_id]["state"] = "draft"
            return True
        if method == "action_post":
            if self.fail_post:
                raise RuntimeError("action_post blocked")
            for move_id in move_ids:
                self.moves[move_id]["state"] = "posted"
                if self.assign_name_on_post_only and normalize_text(self.moves[move_id].get("name")) in {"", "/"}:
                    journal_id = int((self.moves[move_id].get("journal_id") or [0])[0] or 0)
                    journal = next((row for row in self.journals if int(row["id"]) == journal_id), None)
                    journal_code = journal["code"] if journal is not None else "STJ"
                    self.moves[move_id]["name"] = f"{journal_code}/2026/{move_id:04d}"
            return True
        raise AssertionError(f"Unsupported method {method}")

    def _append_move_line_record(self, move_id: int, line_values: dict) -> None:
        self.next_move_line_id += 1
        line_id = self.next_move_line_id
        payload = dict(line_values)
        payload["id"] = line_id
        payload["move_id"] = int(move_id or 0)
        payload.setdefault("debit", 0.0)
        payload.setdefault("credit", 0.0)
        payload["balance"] = float(payload.get("debit") or 0.0) - float(payload.get("credit") or 0.0)
        if "amount_residual" in self.fields_map.get("account.move.line", {}):
            payload.setdefault("amount_residual", payload["balance"])
        if "amount_residual_currency" in self.fields_map.get("account.move.line", {}) and "amount_currency" in payload:
            amount_currency = payload.get("amount_currency")
            if amount_currency in (None, False, ""):
                payload.setdefault("amount_residual_currency", amount_currency)
            else:
                sign_source = float(payload.get("balance") or 0.0)
                signed_amount_currency = abs(float(amount_currency or 0.0))
                payload.setdefault("amount_residual_currency", -signed_amount_currency if sign_source < 0 else signed_amount_currency)
        payload.setdefault("reconciled", False)
        self.move_line_rows[line_id] = payload

    def _clear_move_line_records(self, move_id: int) -> None:
        target_move_id = int(move_id or 0)
        for line_id in [
            int(line_id)
            for line_id, row in self.move_line_rows.items()
            if int(row.get("move_id") or 0) == target_move_id
        ]:
            self.move_line_rows.pop(line_id, None)

    def _sync_move_line_cache(self, move_id: int) -> None:
        move = self.moves.get(int(move_id or 0))
        if move is None:
            return
        move["line_ids"] = [
            row.copy()
            for row in sorted(
                (
                    line
                    for line in self.move_line_rows.values()
                    if int(line.get("move_id") or 0) == int(move_id or 0)
                ),
                key=lambda line: int(line.get("id") or 0),
            )
        ]

    def _apply_partial_reconcile_line_effect(self, line_id: int, *, amount: float, amount_currency) -> None:  # noqa: ANN001
        line = self.move_line_rows.get(line_id)
        if line is None:
            return
        if "amount_residual" in self.fields_map.get("account.move.line", {}):
            current_open_balance = line.get("amount_residual")
            if current_open_balance is None or current_open_balance is False or current_open_balance == "":
                current_open_balance = line.get("balance")
            signed_open_balance = float(current_open_balance or 0.0)
            remaining_abs = round(abs(signed_open_balance) - abs(float(amount or 0.0)), 2)
            line["amount_residual"] = -remaining_abs if signed_open_balance < 0 else remaining_abs
        if (
            "amount_residual_currency" in self.fields_map.get("account.move.line", {})
            and amount_currency is not None
            and line.get("amount_residual_currency") is not None
            and line.get("amount_residual_currency") is not False
            and line.get("amount_residual_currency") != ""
        ):
            signed_open_amount_currency = float(line.get("amount_residual_currency") or 0.0)
            remaining_abs_currency = round(abs(signed_open_amount_currency) - abs(float(amount_currency or 0.0)), 2)
            line["amount_residual_currency"] = (
                -remaining_abs_currency if signed_open_amount_currency < 0 else remaining_abs_currency
            )
        current_open_balance = line.get("amount_residual")
        if current_open_balance is None or current_open_balance is False or current_open_balance == "":
            current_open_balance = line.get("balance")
        line["reconciled"] = abs(float(current_open_balance or 0.0)) <= 0.01

    def _matches(self, row: dict, domain: list[tuple]) -> bool:
        for field_name, operator, value in domain:
            current = row.get(field_name)
            current_id = current[0] if isinstance(current, (list, tuple)) and current else current
            if operator == "=":
                if current_id != value:
                    return False
            elif operator == "in":
                target = set(value)
                if isinstance(current, list):
                    if not target.intersection(set(current)):
                        return False
                elif current_id not in target:
                    return False
            elif operator == "ilike":
                if str(value or "").lower() not in str(current_id or "").lower():
                    return False
            else:
                raise AssertionError(f"Unsupported operator {operator}")
        return True


def _build_repair_row(**overrides) -> SvlDashboardRepairRow:
    payload = {
        "row_key": "row-1",
        "company_id": 1,
        "company_name": "Alpha Company",
        "item_product_id": 901,
        "item_code": "A-SPWH-0043",
        "item_name": "JOHN JAMESON",
        "amount": 7513054.56,
        "date": "2026-01-11",
        "reference": "A-SPWH-0043",
        "line_label": "A-SPWH-0043 JOHN JAMESON",
        "journal_code": "STJ",
        "debit_account_code": "114001",
        "credit_account_code": "510001",
        "svl_id": 1001,
        "move_id": 501,
        "move_name": "STJ/2026/0501",
        "move_state": "posted",
        "target_mode": "fill_existing",
        "posting_mode": "post",
        "svl_reference": "WCGT/IN/00034",
        "repair_source_kind": "svl_linked_empty_move",
        "repair_source_label": "Linked JE Header Kosong",
    }
    payload.update(overrides)
    return SvlDashboardRepairRow(**payload)


def _build_pcb_case1_row(**overrides) -> SvlDashboardPcbCase1RepairRow:
    payload = {
        "row_key": "case1::7001::901::bill_line::8101",
        "company_id": 1,
        "company_name": "Alpha Company",
        "amount": 120.0,
        "date": "2026-03-18",
        "reference": _pcb_case1_reference(
            picking_name="LHPK/IN/0001",
            bill_name="BILL/2026/0001",
            item_code="SKU-001",
            item_name="Produk PCB",
        ),
        "line_label": _pcb_case1_line_label(item_code="SKU-001", item_name="Produk PCB"),
        "cycle_key": "case1::7001",
        "journal_code": "STJ",
        "debit_account_code": "1108099",
        "credit_account_code": "2103006",
        "source_kind": "bill_line",
        "source_id": 8101,
        "source_label": "Bill Line #8101",
        "product_id": 901,
        "item_code": "SKU-001",
        "item_name": "Produk PCB",
        "item_category_name": "Raw Material",
        "bill_line_id": 8101,
        "bill_move_id": 8201,
        "purchase_line_id": 8301,
        "stock_move_id": 8401,
        "stock_move_ids": [8401],
        "picking_id": 7001,
        "picking_name": "LHPK/IN/0001",
        "po_name": "PO/2026/0001",
        "bill_name": "BILL/2026/0001",
        "partner_id": 77,
        "partner_name": "Vendor Alpha",
        "payment_move_ids": [8501],
        "bank_move_ids": [8601],
        "suspend_target_aml_ids": [9102],
        "clearing_target_aml_ids": [9101],
        "product_uom_id": 11,
        "quantity": 4.0,
        "currency_id": 13,
        "amount_currency": 160.0,
        "amount_currency_basis": 120.0,
        "analytic_distribution": {"CC-01": 100.0},
        "bill_price_unit": 40.0,
        "gr_price_unit": 10.0,
        "price_gap_value": 120.0,
        "allocated_amount": 120.0,
    }
    payload.update(overrides)
    return SvlDashboardPcbCase1RepairRow(**payload)


def _build_pcb_case2_row(**overrides) -> SvlDashboardPcbCase2RepairRow:
    payload = {
        "row_key": "case2::7001::901",
        "company_id": 1,
        "company_name": "Alpha Company",
        "amount": 120.0,
        "date": "2026-03-18",
        "reference": _pcb_case1_reference(
            picking_name="LHPK/IN/0001",
            bill_name="BILL/2026/0001",
            item_code="SKU-001",
            item_name="Produk PCB",
        ),
        "line_label": "SKU-001 - Produk PCB - Case 2 - STJ Bill Price Diff (Suspend - Suspend)",
        "cycle_key": "case2::7001",
        "journal_code": "STJ",
        "pcb_case": "case2",
        "pcb_case_label": "Case 2 - STJ Bill Price Diff (Suspend - Suspend)",
        "product_id": 901,
        "item_code": "SKU-001",
        "item_name": "Produk PCB",
        "item_category_name": "Raw Material",
        "bill_line_id": 8101,
        "purchase_line_id": 8301,
        "stock_move_id": 8401,
        "stock_move_ids": [8401],
        "stj_move_ids": [8801],
        "stj_refs": ["STJ/2026/0451"],
        "picking_id": 7001,
        "picking_name": "LHPK/IN/0001",
        "po_name": "PO/2026/0001",
        "bill_move_id": 8201,
        "bill_name": "BILL/2026/0001",
        "partner_id": 77,
        "partner_name": "Vendor Alpha",
        "suspend_account_code": "2103006",
        "inventory_account_code": "114001",
        "expense_account_code": "510001",
        "product_uom_id": 11,
        "quantity": 4.0,
        "currency_id": 13,
        "amount_currency": 160.0,
        "amount_currency_basis": 120.0,
        "analytic_distribution": {"CC-01": 100.0},
        "bill_price_unit": 40.0,
        "gr_price_unit": 10.0,
        "price_gap_value": 120.0,
        "allocated_amount": 120.0,
        "problem_balances_by_code": {"2103006": -120.0},
        "suspend_balance": -120.0,
        "hpp_balance": 120.0,
        "inventory_balance": 120.0,
        "cogs_variance_balance": 0.0,
        "selisih_hpp_amount": 0.0,
        "coefficient_variance": 0.0,
        "bank_balances_by_code": {},
        "bank_account_codes": [],
        "guard_flags": [],
        "guard_messages": [],
        "planned_lines": [
            SvlDashboardPcbRepairPlannedLine(
                role="problem_2103006",
                account_code="2103006",
                account_name="Hutang Suspend",
                amount=120.0,
                side="debit",
            ),
            SvlDashboardPcbRepairPlannedLine(
                role="hpp_zero",
                account_code="510001",
                account_name="COGS",
                amount=120.0,
                side="credit",
                line_label="SKU-001 - Produk PCB - Case 2 - STJ Bill Price Diff (Suspend - Suspend) - Zero HPP",
            ),
        ],
    }
    payload.update(overrides)
    return SvlDashboardPcbCase2RepairRow(**payload)


class SvlDashboardRepairServiceTest(unittest.IsolatedAsyncioTestCase):
    def _assert_pcb_case1_post_skips_reconcile(
        self,
        *,
        result: Any,
        rpc: Any,
        summary: Any | None = None,
    ) -> None:
        self.assertFalse(result.reconcile_attempted)
        self.assertFalse(result.reconcile_performed)
        self.assertFalse(result.reconcile_skipped)
        self.assertEqual(result.reconcile_message, "")
        self.assertEqual(result.reconcile_error_kind, "")
        self.assertEqual(len(rpc.partial_reconciles), 0)
        if summary is not None:
            self.assertEqual(summary.reconciled_count, 0)
            self.assertEqual(summary.reconcile_skipped_count, 0)

    async def test_fill_existing_posted_move_reposts_after_adding_lines(self) -> None:
        rpc = _FakeRepairRpc()
        service = SvlDashboardRepairServiceAsync(rpc=rpc, logger=logging.getLogger("test.dashboard.repair"))

        summary = await service.execute(SvlDashboardRepairRequest(database="hwgroup_erp", rows=[_build_repair_row()]))

        self.assertEqual(summary.created_count, 1)
        self.assertEqual(summary.results[0].status, "POSTED")
        self.assertEqual(summary.results[0].amount, 7513054.56)
        self.assertEqual(summary.results[0].selected_target_mode, "fill_existing")
        self.assertEqual(summary.results[0].effective_date, "2026-01-11")
        self.assertTrue(summary.results[0].posted)
        self.assertEqual(summary.results[0].company_name, "Alpha Company")
        self.assertEqual(summary.results[0].svl_reference, "WCGT/IN/00034")
        self.assertEqual(summary.results[0].debit_account_code, "114001")
        self.assertEqual(summary.results[0].debit_account_name, "Persediaan Barang")
        self.assertEqual(summary.results[0].credit_account_code, "510001")
        self.assertEqual(summary.results[0].credit_account_name, "COGS")
        self.assertEqual(rpc.moves[501]["state"], "posted")
        self.assertEqual(rpc.moves[501]["ref"], "A-SPWH-0043")
        self.assertEqual(len(rpc.moves[501]["line_ids"]), 2)
        self.assertEqual(rpc.execute_calls[0][0], "button_draft")
        self.assertEqual(rpc.execute_calls[-1][0], "action_post")

    async def test_execute_with_parallel_workers_preserves_input_order(self) -> None:
        rpc = _FakeRepairRpc()
        service = SvlDashboardRepairServiceAsync(rpc=rpc, logger=logging.getLogger("test.dashboard.repair"))

        async def fake_execute_row(row: SvlDashboardRepairRow) -> SvlDashboardRepairRowResult:
            await asyncio.sleep(0.02 if row.row_key == "row-1" else 0.0)
            return SvlDashboardRepairRowResult(
                row_key=row.row_key,
                status="CREATED",
                company_id=row.company_id,
                company_name=row.company_name,
                item_code=row.item_code,
                item_name=row.item_name,
            )

        service._execute_row = fake_execute_row  # type: ignore[method-assign]

        summary = await service.execute(
            SvlDashboardRepairRequest(
                database="hwgroup_erp",
                max_workers=2,
                rows=[
                    _build_repair_row(row_key="row-1"),
                    _build_repair_row(row_key="row-2", item_code="B-DGSD-0003", item_name="AIR MINERAL"),
                ],
            )
        )

        self.assertEqual([row.row_key for row in summary.results], ["row-1", "row-2"])

    async def test_partner_prefetch_reuses_exact_match_without_per_row_lookup(self) -> None:
        rpc = _FakeRepairRpc()
        service = SvlDashboardRepairServiceAsync(rpc=rpc, logger=logging.getLogger("test.dashboard.repair"))

        await service._prefetch_partner_ids([(1, "Vendor Alpha"), (1, "Vendor Alpha")])
        partner_id = await service._resolve_partner_id(1, "Vendor Alpha")

        partner_calls = [call for call in rpc.search_read_calls if call[0] == "res.partner"]
        self.assertEqual(partner_id, 77)
        self.assertEqual(len(partner_calls), 1)
        self.assertEqual(partner_calls[0][1], [("name", "in", ["Vendor Alpha"])])

    async def test_partner_prefetch_exact_miss_keeps_ilike_fallback(self) -> None:
        rpc = _FakeRepairRpc()
        rpc.partners = [{"id": 88, "name": "Vendor Alpha Prime"}]
        service = SvlDashboardRepairServiceAsync(rpc=rpc, logger=logging.getLogger("test.dashboard.repair"))

        await service._prefetch_partner_ids([(1, "Vendor Alpha")])
        partner_id = await service._resolve_partner_id(1, "Vendor Alpha")

        partner_calls = [call for call in rpc.search_read_calls if call[0] == "res.partner"]
        self.assertEqual(partner_id, 88)
        self.assertEqual(len(partner_calls), 2)
        self.assertEqual(partner_calls[0][1], [("name", "in", ["Vendor Alpha"])])
        self.assertEqual(partner_calls[1][1], [("name", "ilike", "Vendor Alpha")])

    async def test_execute_emits_progress_snapshots_with_processed_total_counts(self) -> None:
        rpc = _FakeRepairRpc()
        service = SvlDashboardRepairServiceAsync(rpc=rpc, logger=logging.getLogger("test.dashboard.repair"))
        snapshots = []

        async def fake_execute_row(row: SvlDashboardRepairRow) -> SvlDashboardRepairRowResult:
            await asyncio.sleep(0.02 if row.row_key == "row-1" else 0.0)
            return SvlDashboardRepairRowResult(
                row_key=row.row_key,
                status="POSTED",
                company_id=row.company_id,
                company_name=row.company_name,
                item_code=row.item_code,
                item_name=row.item_name,
                move_id=900 + (1 if row.row_key == "row-1" else 2),
                move_name=f"STJ/2026/{900 + (1 if row.row_key == 'row-1' else 2):04d}",
            )

        service._execute_row = fake_execute_row  # type: ignore[method-assign]

        await service.execute(
            SvlDashboardRepairRequest(
                database="hwgroup_erp",
                max_workers=2,
                rows=[
                    _build_repair_row(row_key="row-1"),
                    _build_repair_row(row_key="row-2", item_code="B-DGSD-0003", item_name="AIR MINERAL"),
                ],
                on_progress=snapshots.append,
            )
        )

        self.assertEqual([snapshot.processed for snapshot in snapshots], [0, 1, 2])
        self.assertEqual(snapshots[-1].total, 2)
        self.assertEqual(snapshots[-1].success_count, 2)
        self.assertEqual(snapshots[-1].error_count, 0)
        self.assertEqual(snapshots[-1].progress, 1.0)
        self.assertTrue(normalize_text(snapshots[-1].current))

    async def test_fill_existing_draft_only_keeps_move_in_draft(self) -> None:
        rpc = _FakeRepairRpc()
        service = SvlDashboardRepairServiceAsync(rpc=rpc, logger=logging.getLogger("test.dashboard.repair"))
        row = _build_repair_row(
            row_key="row-draft",
            move_id=500,
            move_name="STJ/2026/0500",
            move_state="draft",
            target_mode="fill_existing",
            posting_mode="draft",
        )

        summary = await service.execute(SvlDashboardRepairRequest(database="hwgroup_erp", rows=[row]))

        self.assertEqual(summary.results[0].status, "UPDATED")
        self.assertFalse(summary.results[0].posted)
        self.assertEqual(rpc.moves[500]["state"], "draft")
        self.assertEqual(len(rpc.moves[500]["line_ids"]), 2)
        self.assertFalse(any(method == "action_post" for method, _ids in rpc.execute_calls))

    async def test_new_move_and_relink_auto_post_updates_svl_link(self) -> None:
        rpc = _FakeRepairRpc()
        service = SvlDashboardRepairServiceAsync(rpc=rpc, logger=logging.getLogger("test.dashboard.repair"))
        row = _build_repair_row(
            row_key="row-new-post",
            svl_id=1002,
            move_id=0,
            move_name="",
            move_state="",
            target_mode="new_and_relink",
            posting_mode="post",
        )

        summary = await service.execute(SvlDashboardRepairRequest(database="hwgroup_erp", rows=[row]))

        new_move_id = summary.results[0].move_id
        self.assertEqual(summary.results[0].status, "POSTED")
        self.assertGreater(new_move_id, 501)
        self.assertEqual(rpc.moves[new_move_id]["state"], "posted")
        self.assertEqual(rpc.svls[1002]["account_move_id"][0], new_move_id)
        self.assertEqual(summary.results[0].selected_target_mode, "new_and_relink")
        self.assertEqual(summary.results[0].old_move_action, "")

    async def test_new_move_and_relink_auto_post_reads_final_move_name_after_post(self) -> None:
        rpc = _FakeRepairRpc()
        rpc.assign_name_on_post_only = True
        service = SvlDashboardRepairServiceAsync(rpc=rpc, logger=logging.getLogger("test.dashboard.repair"))
        row = _build_repair_row(
            row_key="row-new-final-name",
            svl_id=1002,
            move_id=0,
            move_name="",
            move_state="",
            target_mode="new_and_relink",
            posting_mode="post",
        )

        summary = await service.execute(SvlDashboardRepairRequest(database="hwgroup_erp", rows=[row]))

        result = summary.results[0]
        self.assertEqual(result.status, "POSTED")
        self.assertEqual(result.move_name, f"STJ/2026/{result.move_id:04d}")
        self.assertIn(result.move_name, result.message)

    async def test_new_move_and_relink_draft_only_stays_draft(self) -> None:
        rpc = _FakeRepairRpc()
        service = SvlDashboardRepairServiceAsync(rpc=rpc, logger=logging.getLogger("test.dashboard.repair"))
        row = _build_repair_row(
            row_key="row-new-draft",
            svl_id=1002,
            move_id=0,
            move_name="",
            move_state="",
            target_mode="new_and_relink",
            posting_mode="draft",
        )

        summary = await service.execute(SvlDashboardRepairRequest(database="hwgroup_erp", rows=[row]))

        new_move_id = summary.results[0].move_id
        self.assertEqual(summary.results[0].status, "CREATED")
        self.assertIn("draft only", summary.results[0].message.lower())
        self.assertEqual(rpc.moves[new_move_id]["state"], "draft")
        self.assertEqual(rpc.svls[1002]["account_move_id"][0], new_move_id)

    async def test_new_move_and_relink_from_existing_move_marks_old_move_only(self) -> None:
        rpc = _FakeRepairRpc()
        service = SvlDashboardRepairServiceAsync(rpc=rpc, logger=logging.getLogger("test.dashboard.repair"))
        today_text = date.today().strftime("%Y-%m-%d")
        row = _build_repair_row(
            row_key="row-new-existing",
            date=today_text,
            svl_id=1001,
            move_id=501,
            move_name="STJ/2026/0501",
            move_state="posted",
            target_mode="new_and_relink",
            posting_mode="post",
        )

        summary = await service.execute(SvlDashboardRepairRequest(database="hwgroup_erp", rows=[row]))

        result = summary.results[0]
        self.assertEqual(result.status, "POSTED")
        self.assertEqual(result.old_move_action, "mark_only")
        self.assertEqual(result.old_move_id, 501)
        self.assertEqual(result.old_move_name, "STJ/2026/0501")
        self.assertEqual(result.relinked_svl_id, 1001)
        self.assertEqual(rpc.svls[1001]["account_move_id"][0], result.move_id)
        self.assertEqual(rpc.moves[501]["state"], "posted")

    async def test_new_move_and_relink_failure_leaves_created_move_draft(self) -> None:
        rpc = _FakeRepairRpc()
        rpc.fail_relink = True
        service = SvlDashboardRepairServiceAsync(rpc=rpc, logger=logging.getLogger("test.dashboard.repair"))
        row = _build_repair_row(
            row_key="row-relink-fail",
            svl_id=1002,
            move_id=0,
            move_name="",
            move_state="",
            target_mode="new_and_relink",
            posting_mode="post",
        )

        summary = await service.execute(SvlDashboardRepairRequest(database="hwgroup_erp", rows=[row]))

        self.assertEqual(summary.error_count, 1)
        self.assertEqual(summary.results[0].status, "ERROR")
        self.assertIn("relink", summary.results[0].message.lower())
        self.assertEqual(summary.results[0].error_kind, "relink_failed")
        new_move_id = max(rpc.moves)
        self.assertEqual(rpc.moves[new_move_id]["state"], "draft")
        self.assertFalse(rpc.svls[1002]["account_move_id"])

    async def test_lock_date_violation_blocks_posting(self) -> None:
        rpc = _FakeRepairRpc()
        rpc.lock_date = "2026-01-11"
        service = SvlDashboardRepairServiceAsync(rpc=rpc, logger=logging.getLogger("test.dashboard.repair"))
        row = _build_repair_row(
            row_key="row-lock",
            svl_id=1002,
            move_id=0,
            move_name="",
            move_state="",
            target_mode="new_and_relink",
            posting_mode="post",
        )

        summary = await service.execute(SvlDashboardRepairRequest(database="hwgroup_erp", rows=[row]))

        self.assertEqual(summary.results[0].status, "ERROR")
        self.assertIn("lock date", summary.results[0].message.lower())
        self.assertEqual(summary.results[0].error_kind, "lock_date")

    async def test_rewrite_existing_with_locked_old_date_returns_lock_date_error_kind(self) -> None:
        rpc = _FakeRepairRpc()
        rpc.lock_date = "2026-01-11"
        service = SvlDashboardRepairServiceAsync(rpc=rpc, logger=logging.getLogger("test.dashboard.repair"))

        summary = await service.execute(
            SvlDashboardRepairRequest(
                database="hwgroup_erp",
                rows=[
                    _build_repair_row(
                        row_key="row-rewrite-locked",
                        date="2026-01-11",
                        target_mode="fill_existing",
                        posting_mode="post",
                    )
                ],
            )
        )

        self.assertEqual(summary.results[0].status, "ERROR")
        self.assertEqual(summary.results[0].error_kind, "lock_date")
        self.assertEqual(summary.results[0].selected_target_mode, "fill_existing")

    async def test_rewrite_existing_with_today_open_date_can_proceed(self) -> None:
        rpc = _FakeRepairRpc()
        rpc.lock_date = "2026-01-11"
        service = SvlDashboardRepairServiceAsync(rpc=rpc, logger=logging.getLogger("test.dashboard.repair"))
        today_text = date.today().strftime("%Y-%m-%d")

        summary = await service.execute(
            SvlDashboardRepairRequest(
                database="hwgroup_erp",
                rows=[
                    _build_repair_row(
                        row_key="row-rewrite-open",
                        date=today_text,
                        target_mode="fill_existing",
                        posting_mode="post",
                    )
                ],
            )
        )

        self.assertEqual(summary.results[0].status, "POSTED")
        self.assertEqual(summary.results[0].effective_date, today_text)

    async def test_repost_failure_returns_error_for_existing_posted_move(self) -> None:
        rpc = _FakeRepairRpc()
        rpc.fail_post = True
        service = SvlDashboardRepairServiceAsync(rpc=rpc, logger=logging.getLogger("test.dashboard.repair"))

        summary = await service.execute(SvlDashboardRepairRequest(database="hwgroup_erp", rows=[_build_repair_row(row_key="row-repost-fail")]))

        self.assertEqual(summary.results[0].status, "ERROR")
        self.assertIn("restore stj ke posted", summary.results[0].message.lower())
        self.assertEqual(summary.results[0].error_kind, "repost_failed")
        self.assertEqual(rpc.moves[501]["state"], "draft")

    async def test_pcb_case1_draft_populates_strong_link_fields_when_supported(self) -> None:
        rpc = _FakeRepairRpc()
        service = SvlDashboardRepairServiceAsync(rpc=rpc, logger=logging.getLogger("test.dashboard.repair"))

        summary = await service.execute_pcb_case1(
            SvlDashboardPcbCase1RepairRequest(
                database="hwgroup_erp",
                rows=[_build_pcb_case1_row()],
                posting_mode="draft",
            )
        )

        result = summary.results[0]
        self.assertEqual(result.status, "CREATED")
        self.assertFalse(result.posted)
        self.assertFalse(result.reconcile_attempted)
        move = rpc.moves[result.move_id]
        debit_line = move["line_ids"][0]
        credit_line = move["line_ids"][1]
        self.assertEqual(debit_line["product_id"], 901)
        self.assertEqual(debit_line["product_uom_id"], 11)
        self.assertEqual(debit_line["purchase_line_id"], 8301)
        self.assertEqual(debit_line["partner_id"], 77)
        self.assertEqual(debit_line["analytic_distribution"], {"CC-01": 100.0})
        self.assertEqual(debit_line["stock_move_id"], 8401)
        self.assertEqual(debit_line["picking_id"], 7001)
        self.assertEqual(debit_line["bill_line_id"], 8101)
        self.assertEqual(debit_line["bill_move_id"], 8201)
        self.assertNotIn("currency_id", debit_line)
        self.assertNotIn("amount_currency", debit_line)
        self.assertNotIn("currency_id", credit_line)
        self.assertNotIn("amount_currency", credit_line)

    async def test_pcb_case1_draft_omits_currency_payload_even_when_currency_fields_exist(self) -> None:
        rpc = _FakeRepairRpc()
        service = SvlDashboardRepairServiceAsync(rpc=rpc, logger=logging.getLogger("test.dashboard.repair"))

        summary = await service.execute_pcb_case1(
            SvlDashboardPcbCase1RepairRequest(
                database="hwgroup_erp",
                rows=[
                    _build_pcb_case1_row(
                        amount=1_291_304.35,
                        currency_id=13,
                        amount_currency=6_750_000.0,
                        amount_currency_basis=6_750_000.0,
                        allocated_amount=1_291_304.35,
                        price_gap_value=1_291_304.35,
                    )
                ],
                posting_mode="draft",
            )
        )

        result = summary.results[0]
        self.assertEqual(result.status, "CREATED")
        move = rpc.moves[result.move_id]
        debit_line = move["line_ids"][0]
        credit_line = move["line_ids"][1]
        self.assertEqual(debit_line["debit"], 1_291_304.35)
        self.assertEqual(credit_line["credit"], 1_291_304.35)
        self.assertNotIn("currency_id", debit_line)
        self.assertNotIn("amount_currency", debit_line)
        self.assertNotIn("currency_id", credit_line)
        self.assertNotIn("amount_currency", credit_line)

    async def test_pcb_case1_post_reads_final_move_name_and_exact_reconciles(self) -> None:
        rpc = _FakeRepairRpc()
        rpc.assign_name_on_post_only = True
        rpc.move_line_rows[9101] = {
            "id": 9101,
            "move_id": 8801,
            "account_id": 302,
            "partner_id": 77,
            "currency_id": 13,
            "debit": 0.0,
            "credit": 120.0,
            "balance": -120.0,
            "amount_currency": -160.0,
            "reconciled": False,
        }
        rpc.move_line_rows[9102] = {
            "id": 9102,
            "move_id": 8802,
            "account_id": 301,
            "partner_id": 77,
            "currency_id": 13,
            "debit": 120.0,
            "credit": 0.0,
            "balance": 120.0,
            "amount_currency": 160.0,
            "reconciled": False,
        }
        service = SvlDashboardRepairServiceAsync(rpc=rpc, logger=logging.getLogger("test.dashboard.repair"))

        summary = await service.execute_pcb_case1(
            SvlDashboardPcbCase1RepairRequest(
                database="hwgroup_erp",
                rows=[_build_pcb_case1_row()],
                posting_mode="post",
            )
        )

        result = summary.results[0]
        self.assertEqual(result.status, "POSTED")
        self.assertTrue(result.posted)
        self.assertEqual(result.move_name, f"STJ/2026/{result.move_id:04d}")
        self._assert_pcb_case1_post_skips_reconcile(result=result, rpc=rpc, summary=summary)
        self.assertFalse(rpc.move_line_rows[9101]["reconciled"])
        self.assertFalse(rpc.move_line_rows[9102]["reconciled"])

    async def test_pcb_case1_post_matches_partial_target_by_amount_residual(self) -> None:
        rpc = _FakeRepairRpc()
        rpc.assign_name_on_post_only = True
        rpc.move_line_rows[9101] = {
            "id": 9101,
            "move_id": 8801,
            "account_id": 302,
            "partner_id": 77,
            "currency_id": 0,
            "debit": 0.0,
            "credit": 300.0,
            "balance": -300.0,
            "amount_residual": -120.0,
            "reconciled": False,
        }
        rpc.move_line_rows[9102] = {
            "id": 9102,
            "move_id": 8802,
            "account_id": 301,
            "partner_id": 77,
            "currency_id": 0,
            "debit": 300.0,
            "credit": 0.0,
            "balance": 300.0,
            "amount_residual": 120.0,
            "reconciled": False,
        }
        service = SvlDashboardRepairServiceAsync(rpc=rpc, logger=logging.getLogger("test.dashboard.repair"))

        summary = await service.execute_pcb_case1(
            SvlDashboardPcbCase1RepairRequest(
                database="hwgroup_erp",
                rows=[_build_pcb_case1_row(currency_id=0, amount_currency=0.0)],
                posting_mode="post",
            )
        )

        result = summary.results[0]
        self.assertEqual(result.status, "POSTED")
        self._assert_pcb_case1_post_skips_reconcile(result=result, rpc=rpc, summary=summary)

    async def test_pcb_case1_post_runtime_lookup_reconciles_without_seed_target_ids(self) -> None:
        rpc = _FakeRepairRpc()
        rpc.assign_name_on_post_only = True
        rpc.move_line_rows[9101] = {
            "id": 9101,
            "move_id": 8801,
            "account_id": 302,
            "partner_id": 77,
            "currency_id": 0,
            "debit": 0.0,
            "credit": 120.0,
            "balance": -120.0,
            "amount_residual": -120.0,
            "reconciled": False,
        }
        rpc.move_line_rows[9102] = {
            "id": 9102,
            "move_id": 8201,
            "account_id": 301,
            "partner_id": 77,
            "currency_id": 0,
            "debit": 120.0,
            "credit": 0.0,
            "balance": 120.0,
            "amount_residual": 120.0,
            "reconciled": False,
        }
        service = SvlDashboardRepairServiceAsync(rpc=rpc, logger=logging.getLogger("test.dashboard.repair"))

        summary = await service.execute_pcb_case1(
            SvlDashboardPcbCase1RepairRequest(
                database="hwgroup_erp",
                rows=[
                    _build_pcb_case1_row(
                        currency_id=0,
                        amount_currency=0.0,
                        suspend_target_aml_ids=[],
                        clearing_target_aml_ids=[],
                        stj_move_ids=[8801],
                        bill_move_id=8201,
                    )
                ],
                posting_mode="post",
            )
        )

        result = summary.results[0]
        self.assertEqual(result.status, "POSTED")
        self._assert_pcb_case1_post_skips_reconcile(result=result, rpc=rpc, summary=summary)

    async def test_pcb_case1_post_best_effort_heal_fills_null_residual_currency_and_reconciles(self) -> None:
        rpc = _FakeRepairRpc()
        rpc.assign_name_on_post_only = True
        rpc.move_line_rows[9101] = {
            "id": 9101,
            "move_id": 8801,
            "account_id": 302,
            "partner_id": 77,
            "currency_id": 13,
            "debit": 0.0,
            "credit": 120.0,
            "balance": -120.0,
            "amount_currency": -160.0,
            "amount_residual": -120.0,
            "amount_residual_currency": None,
            "reconciled": False,
        }
        rpc.move_line_rows[9102] = {
            "id": 9102,
            "move_id": 8802,
            "account_id": 301,
            "partner_id": 77,
            "currency_id": 13,
            "debit": 120.0,
            "credit": 0.0,
            "balance": 120.0,
            "amount_currency": 160.0,
            "amount_residual": 120.0,
            "amount_residual_currency": None,
            "reconciled": False,
        }
        service = SvlDashboardRepairServiceAsync(rpc=rpc, logger=logging.getLogger("test.dashboard.repair"))

        summary = await service.execute_pcb_case1(
            SvlDashboardPcbCase1RepairRequest(
                database="hwgroup_erp",
                rows=[_build_pcb_case1_row()],
                posting_mode="post",
            )
        )

        result = summary.results[0]
        self.assertEqual(result.status, "POSTED")
        self._assert_pcb_case1_post_skips_reconcile(result=result, rpc=rpc, summary=summary)

    async def test_pcb_case1_post_warns_when_null_amount_fields_remain_after_heal(self) -> None:
        rpc = _FakeRepairRpc()
        rpc.assign_name_on_post_only = True
        rpc.preserve_null_residual_after_heal = True
        rpc.move_line_rows[9101] = {
            "id": 9101,
            "move_id": 8801,
            "account_id": 302,
            "partner_id": 77,
            "currency_id": 13,
            "debit": 0.0,
            "credit": 120.0,
            "balance": -120.0,
            "amount_currency": -160.0,
            "amount_residual": None,
            "amount_residual_currency": None,
            "reconciled": False,
        }
        rpc.move_line_rows[9102] = {
            "id": 9102,
            "move_id": 8802,
            "account_id": 301,
            "partner_id": 77,
            "currency_id": 13,
            "debit": 120.0,
            "credit": 0.0,
            "balance": 120.0,
            "amount_currency": 160.0,
            "amount_residual": None,
            "amount_residual_currency": None,
            "reconciled": False,
        }
        service = SvlDashboardRepairServiceAsync(rpc=rpc, logger=logging.getLogger("test.dashboard.repair"))

        summary = await service.execute_pcb_case1(
            SvlDashboardPcbCase1RepairRequest(
                database="hwgroup_erp",
                rows=[_build_pcb_case1_row()],
                posting_mode="post",
            )
        )

        result = summary.results[0]
        self.assertEqual(result.status, "POSTED")
        self._assert_pcb_case1_post_skips_reconcile(result=result, rpc=rpc, summary=summary)

    async def test_pcb_case1_partner_resolution_prefers_bill_partner_before_name_lookup(self) -> None:
        rpc = _FakeRepairRpc()
        rpc.moves[8201] = {
            "id": 8201,
            "name": "BILL/2026/0001",
            "state": "posted",
            "company_id": 1,
            "journal_id": [81, "Stock Journal"],
            "date": "2026-03-18",
            "ref": "",
            "partner_id": 77,
            "line_ids": [],
        }
        service = SvlDashboardRepairServiceAsync(rpc=rpc, logger=logging.getLogger("test.dashboard.repair"))

        summary = await service.execute_pcb_case1(
            SvlDashboardPcbCase1RepairRequest(
                database="hwgroup_erp",
                rows=[_build_pcb_case1_row(partner_id=0, partner_name="Vendor Salah")],
                posting_mode="draft",
            )
        )

        result = summary.results[0]
        self.assertEqual(result.status, "CREATED")
        move = rpc.moves[result.move_id]
        self.assertEqual(move["partner_id"], 77)
        self.assertEqual(move["line_ids"][0]["partner_id"], 77)
        self.assertEqual(move["line_ids"][1]["partner_id"], 77)

    async def test_pcb_case1_post_preserves_zero_amount_currency_on_lines_and_partial_reconcile(self) -> None:
        rpc = _FakeRepairRpc()
        rpc.assign_name_on_post_only = True
        rpc.move_line_rows[9101] = {
            "id": 9101,
            "move_id": 8801,
            "account_id": 302,
            "partner_id": 77,
            "currency_id": 13,
            "debit": 0.0,
            "credit": 120.0,
            "balance": -120.0,
            "amount_currency": 0.0,
            "reconciled": False,
        }
        rpc.move_line_rows[9102] = {
            "id": 9102,
            "move_id": 8802,
            "account_id": 301,
            "partner_id": 77,
            "currency_id": 13,
            "debit": 120.0,
            "credit": 0.0,
            "balance": 120.0,
            "amount_currency": 0.0,
            "reconciled": False,
        }
        service = SvlDashboardRepairServiceAsync(rpc=rpc, logger=logging.getLogger("test.dashboard.repair"))

        summary = await service.execute_pcb_case1(
            SvlDashboardPcbCase1RepairRequest(
                database="hwgroup_erp",
                rows=[_build_pcb_case1_row(amount_currency=0.0)],
                posting_mode="post",
            )
        )

        result = summary.results[0]
        self.assertEqual(result.status, "POSTED")
        move = rpc.moves[result.move_id]
        self.assertNotIn("amount_currency", move["line_ids"][0])
        self.assertNotIn("amount_currency", move["line_ids"][1])
        self.assertEqual(len(rpc.partial_reconciles), 0)
        for values in rpc.partial_reconciles.values():
            self.assertIn("amount_currency", values)
            self.assertEqual(values["amount_currency"], 0.0)

    async def test_pcb_case1_post_partial_reconcile_error_keeps_posted_move_as_warning(self) -> None:
        rpc = _FakeRepairRpc()
        rpc.assign_name_on_post_only = True
        rpc.partial_reconcile_error = (
            "account.partial.reconcile.create Odoo error: "
            "builtins.TypeError: unsupported operand type(s) for -: 'float' and 'NoneType'"
        )
        rpc.move_line_rows[9101] = {
            "id": 9101,
            "move_id": 8801,
            "account_id": 302,
            "partner_id": 77,
            "currency_id": 13,
            "debit": 0.0,
            "credit": 120.0,
            "balance": -120.0,
            "amount_currency": -160.0,
            "reconciled": False,
        }
        rpc.move_line_rows[9102] = {
            "id": 9102,
            "move_id": 8802,
            "account_id": 301,
            "partner_id": 77,
            "currency_id": 13,
            "debit": 120.0,
            "credit": 0.0,
            "balance": 120.0,
            "amount_currency": 160.0,
            "reconciled": False,
        }
        service = SvlDashboardRepairServiceAsync(rpc=rpc, logger=logging.getLogger("test.dashboard.repair"))

        summary = await service.execute_pcb_case1(
            SvlDashboardPcbCase1RepairRequest(
                database="hwgroup_erp",
                rows=[_build_pcb_case1_row()],
                posting_mode="post",
            )
        )

        result = summary.results[0]
        self.assertEqual(result.status, "POSTED")
        self.assertTrue(result.posted)
        self.assertGreater(result.move_id, 0)
        self.assertEqual(summary.error_count, 0)
        self._assert_pcb_case1_post_skips_reconcile(result=result, rpc=rpc, summary=summary)

    async def test_pcb_case1_post_self_heal_allows_partial_reconcile_when_target_amount_currency_is_null(self) -> None:
        rpc = _FakeRepairRpc()
        rpc.assign_name_on_post_only = True
        rpc.move_line_rows[9101] = {
            "id": 9101,
            "move_id": 8801,
            "account_id": 302,
            "partner_id": 77,
            "currency_id": 13,
            "debit": 0.0,
            "credit": 120.0,
            "balance": -120.0,
            "amount_currency": None,
            "reconciled": False,
        }
        rpc.move_line_rows[9102] = {
            "id": 9102,
            "move_id": 8802,
            "account_id": 301,
            "partner_id": 77,
            "currency_id": 13,
            "debit": 120.0,
            "credit": 0.0,
            "balance": 120.0,
            "amount_currency": None,
            "reconciled": False,
        }
        service = SvlDashboardRepairServiceAsync(rpc=rpc, logger=logging.getLogger("test.dashboard.repair"))

        summary = await service.execute_pcb_case1(
            SvlDashboardPcbCase1RepairRequest(
                database="hwgroup_erp",
                rows=[_build_pcb_case1_row()],
                posting_mode="post",
            )
        )

        result = summary.results[0]
        self.assertEqual(result.status, "POSTED")
        self.assertEqual(result.reconcile_error_kind, "")
        self.assertEqual(summary.error_count, 0)
        self._assert_pcb_case1_post_skips_reconcile(result=result, rpc=rpc, summary=summary)

    async def test_pcb_case1_post_partial_single_target_closes_new_line_but_keeps_target_open(self) -> None:
        rpc = _FakeRepairRpc()
        rpc.assign_name_on_post_only = True
        rpc.move_line_rows[9101] = {
            "id": 9101,
            "move_id": 8801,
            "account_id": 302,
            "partner_id": 77,
            "currency_id": 0,
            "debit": 0.0,
            "credit": 300.0,
            "balance": -300.0,
            "amount_residual": -300.0,
            "reconciled": False,
        }
        rpc.move_line_rows[9102] = {
            "id": 9102,
            "move_id": 8201,
            "account_id": 301,
            "partner_id": 77,
            "currency_id": 0,
            "debit": 300.0,
            "credit": 0.0,
            "balance": 300.0,
            "amount_residual": 300.0,
            "reconciled": False,
        }
        service = SvlDashboardRepairServiceAsync(rpc=rpc, logger=logging.getLogger("test.dashboard.repair"))

        summary = await service.execute_pcb_case1(
            SvlDashboardPcbCase1RepairRequest(
                database="hwgroup_erp",
                rows=[_build_pcb_case1_row(currency_id=0, amount_currency=0.0)],
                posting_mode="post",
            )
        )

        result = summary.results[0]
        self.assertEqual(result.status, "POSTED")
        self._assert_pcb_case1_post_skips_reconcile(result=result, rpc=rpc, summary=summary)
        self.assertEqual(rpc.move_line_rows[9101]["amount_residual"], -300.0)
        self.assertEqual(rpc.move_line_rows[9102]["amount_residual"], 300.0)
        self.assertFalse(rpc.move_line_rows[9101]["reconciled"])
        self.assertFalse(rpc.move_line_rows[9102]["reconciled"])

    async def test_pcb_case1_post_partial_multi_target_consumes_multiple_bill_amls(self) -> None:
        rpc = _FakeRepairRpc()
        rpc.assign_name_on_post_only = True
        rpc.move_line_rows[9101] = {
            "id": 9101,
            "move_id": 8801,
            "account_id": 302,
            "partner_id": 77,
            "currency_id": 0,
            "debit": 0.0,
            "credit": 120.0,
            "balance": -120.0,
            "amount_residual": -120.0,
            "reconciled": False,
        }
        rpc.move_line_rows[9102] = {
            "id": 9102,
            "move_id": 8201,
            "account_id": 301,
            "partner_id": 77,
            "product_id": 901,
            "purchase_line_id": 8301,
            "currency_id": 0,
            "debit": 70.0,
            "credit": 0.0,
            "balance": 70.0,
            "amount_residual": 70.0,
            "reconciled": False,
        }
        rpc.move_line_rows[9104] = {
            "id": 9104,
            "move_id": 8201,
            "account_id": 301,
            "partner_id": 77,
            "product_id": 901,
            "purchase_line_id": 8301,
            "currency_id": 0,
            "debit": 50.0,
            "credit": 0.0,
            "balance": 50.0,
            "amount_residual": 50.0,
            "reconciled": False,
        }
        service = SvlDashboardRepairServiceAsync(rpc=rpc, logger=logging.getLogger("test.dashboard.repair"))

        summary = await service.execute_pcb_case1(
            SvlDashboardPcbCase1RepairRequest(
                database="hwgroup_erp",
                rows=[
                    _build_pcb_case1_row(
                        currency_id=0,
                        amount_currency=0.0,
                        suspend_target_aml_ids=[9102, 9104],
                        clearing_target_aml_ids=[9101],
                    )
                ],
                posting_mode="post",
            )
        )

        result = summary.results[0]
        self.assertEqual(result.status, "POSTED")
        self._assert_pcb_case1_post_skips_reconcile(result=result, rpc=rpc, summary=summary)
        self.assertFalse(rpc.move_line_rows[9102]["reconciled"])
        self.assertFalse(rpc.move_line_rows[9104]["reconciled"])

    async def test_pcb_case1_post_same_cycle_rows_share_reconcile_lock_for_stj_target(self) -> None:
        rpc = _FakeRepairRpc()
        rpc.assign_name_on_post_only = True
        rpc.partial_reconcile_create_delay = 0.03
        rpc.move_line_rows[9101] = {
            "id": 9101,
            "move_id": 8801,
            "account_id": 302,
            "partner_id": 77,
            "product_id": 901,
            "currency_id": 0,
            "debit": 0.0,
            "credit": 120.0,
            "balance": -120.0,
            "amount_residual": -120.0,
            "reconciled": False,
        }
        rpc.move_line_rows[9201] = {
            "id": 9201,
            "move_id": 8201,
            "account_id": 301,
            "partner_id": 77,
            "product_id": 901,
            "purchase_line_id": 8301,
            "currency_id": 0,
            "debit": 80.0,
            "credit": 0.0,
            "balance": 80.0,
            "amount_residual": 80.0,
            "reconciled": False,
        }
        rpc.move_line_rows[9202] = {
            "id": 9202,
            "move_id": 8202,
            "account_id": 301,
            "partner_id": 77,
            "product_id": 902,
            "purchase_line_id": 8302,
            "currency_id": 0,
            "debit": 80.0,
            "credit": 0.0,
            "balance": 80.0,
            "amount_residual": 80.0,
            "reconciled": False,
        }
        service = SvlDashboardRepairServiceAsync(rpc=rpc, logger=logging.getLogger("test.dashboard.repair"))

        row_one = _build_pcb_case1_row(
            row_key="case1::shared::1",
            cycle_key="case1::shared",
            amount=80.0,
            currency_id=0,
            amount_currency=0.0,
            bill_line_id=8101,
            bill_move_id=8201,
            purchase_line_id=8301,
            product_id=901,
            suspend_target_aml_ids=[9201],
            clearing_target_aml_ids=[9101],
            stj_move_ids=[8801],
            picking_name="LHPK/IN/0001",
            bill_name="BILL/2026/0001",
            reference=_pcb_case1_reference(
                picking_name="LHPK/IN/0001",
                bill_name="BILL/2026/0001",
                item_code="SKU-001",
                item_name="Produk PCB A",
            ),
            line_label=_pcb_case1_line_label(item_code="SKU-001", item_name="Produk PCB A"),
        )
        row_two = _build_pcb_case1_row(
            row_key="case1::shared::2",
            cycle_key="case1::shared",
            amount=80.0,
            currency_id=0,
            amount_currency=0.0,
            bill_line_id=8102,
            bill_move_id=8202,
            purchase_line_id=8302,
            product_id=902,
            item_code="SKU-002",
            item_name="Produk PCB B",
            suspend_target_aml_ids=[9202],
            clearing_target_aml_ids=[9101],
            stj_move_ids=[8801],
            picking_name="LHPK/IN/0002",
            bill_name="BILL/2026/0002",
            po_name="PO/2026/0002",
            reference=_pcb_case1_reference(
                picking_name="LHPK/IN/0002",
                bill_name="BILL/2026/0002",
                item_code="SKU-002",
                item_name="Produk PCB B",
            ),
            line_label=_pcb_case1_line_label(item_code="SKU-002", item_name="Produk PCB B"),
        )

        summary = await service.execute_pcb_case1(
            SvlDashboardPcbCase1RepairRequest(
                database="hwgroup_erp",
                rows=[row_one, row_two],
                posting_mode="post",
                max_workers=2,
            )
        )

        self.assertEqual(len(summary.results), 2)
        self.assertEqual(sum(1 for item in summary.results if item.reconcile_performed), 0)
        self.assertEqual(sum(1 for item in summary.results if item.reconcile_skipped), 0)
        self.assertEqual(summary.reconcile_skipped_count, 0)
        self.assertEqual(len(rpc.partial_reconciles), 0)
        self.assertEqual(rpc.move_line_rows[9101]["amount_residual"], -120.0)
        self.assertFalse(rpc.move_line_rows[9101]["reconciled"])

    async def test_pcb_case1_post_clearing_target_with_blank_partner_still_reconciles(self) -> None:
        rpc = _FakeRepairRpc()
        rpc.assign_name_on_post_only = True
        rpc.move_line_rows[9101] = {
            "id": 9101,
            "move_id": 8801,
            "account_id": 302,
            "partner_id": 0,
            "currency_id": 0,
            "debit": 0.0,
            "credit": 120.0,
            "balance": -120.0,
            "amount_residual": -120.0,
            "reconciled": False,
        }
        rpc.move_line_rows[9102] = {
            "id": 9102,
            "move_id": 8201,
            "account_id": 301,
            "partner_id": 77,
            "currency_id": 0,
            "debit": 120.0,
            "credit": 0.0,
            "balance": 120.0,
            "amount_residual": 120.0,
            "reconciled": False,
        }
        service = SvlDashboardRepairServiceAsync(rpc=rpc, logger=logging.getLogger("test.dashboard.repair"))

        summary = await service.execute_pcb_case1(
            SvlDashboardPcbCase1RepairRequest(
                database="hwgroup_erp",
                rows=[_build_pcb_case1_row(currency_id=0, amount_currency=0.0)],
                posting_mode="post",
            )
        )

        result = summary.results[0]
        self.assertEqual(result.status, "POSTED")
        self._assert_pcb_case1_post_skips_reconcile(result=result, rpc=rpc, summary=summary)
        self.assertFalse(rpc.move_line_rows[9101]["reconciled"])

    async def test_pcb_case1_post_warns_when_partial_targets_do_not_fully_close_new_line(self) -> None:
        rpc = _FakeRepairRpc()
        rpc.assign_name_on_post_only = True
        rpc.move_line_rows[9101] = {
            "id": 9101,
            "move_id": 8801,
            "account_id": 302,
            "partner_id": 77,
            "currency_id": 0,
            "debit": 0.0,
            "credit": 50.0,
            "balance": -50.0,
            "amount_residual": -50.0,
            "reconciled": False,
        }
        rpc.move_line_rows[9102] = {
            "id": 9102,
            "move_id": 8201,
            "account_id": 301,
            "partner_id": 77,
            "currency_id": 0,
            "debit": 120.0,
            "credit": 0.0,
            "balance": 120.0,
            "amount_residual": 120.0,
            "reconciled": False,
        }
        service = SvlDashboardRepairServiceAsync(rpc=rpc, logger=logging.getLogger("test.dashboard.repair"))

        summary = await service.execute_pcb_case1(
            SvlDashboardPcbCase1RepairRequest(
                database="hwgroup_erp",
                rows=[_build_pcb_case1_row(currency_id=0, amount_currency=0.0)],
                posting_mode="post",
            )
        )

        result = summary.results[0]
        self.assertEqual(result.status, "POSTED")
        self.assertEqual(result.reconcile_error_kind, "")
        self._assert_pcb_case1_post_skips_reconcile(result=result, rpc=rpc, summary=summary)

    async def test_pcb_case1_duplicate_local_result_move_is_not_recreated(self) -> None:
        rpc = _FakeRepairRpc()
        rpc.moves[980] = {
            "id": 980,
            "name": "STJ/2026/0980",
            "state": "posted",
            "company_id": 1,
            "journal_id": [81, "Stock Journal"],
            "date": "2026-03-18",
            "ref": _pcb_case1_reference(
                picking_name="LHPK/IN/0001",
                bill_name="BILL/2026/0001",
                item_code="SKU-001",
                item_name="Produk PCB",
            ),
            "partner_id": 77,
            "line_ids": [
                {"account_id": 302, "debit": 120.0, "credit": 0.0, "partner_id": 77},
                {"account_id": 301, "debit": 0.0, "credit": 120.0, "partner_id": 77},
            ],
        }
        rpc._append_move_line_record(980, {"account_id": 302, "debit": 120.0, "credit": 0.0, "partner_id": 77})
        rpc._append_move_line_record(980, {"account_id": 301, "debit": 0.0, "credit": 120.0, "partner_id": 77})
        service = SvlDashboardRepairServiceAsync(rpc=rpc, logger=logging.getLogger("test.dashboard.repair"))

        summary = await service.execute_pcb_case1(
            SvlDashboardPcbCase1RepairRequest(
                database="hwgroup_erp",
                rows=[_build_pcb_case1_row(result_move_id=980, result_move_name="STJ/2026/0980", result_posted=True)],
                posting_mode="post",
            )
        )

        result = summary.results[0]
        self.assertEqual(result.status, "POSTED")
        self.assertTrue(result.posted)
        self.assertTrue(result.existing_move_detected)
        self.assertEqual(result.move_id, 980)
        self.assertEqual(result.move_name, "STJ/2026/0980")
        self.assertIn("tidak dibuat ulang", result.message)
        self.assertEqual(summary.created_count, 0)
        self.assertEqual(summary.existing_count, 1)
        self.assertEqual(summary.error_count, 0)
        self.assertEqual(rpc.next_move_id, 900)

    async def test_pcb_case1_duplicate_exact_lookup_uses_existing_move_without_local_result(self) -> None:
        rpc = _FakeRepairRpc()
        rpc.moves[981] = {
            "id": 981,
            "name": "STJ/2026/0981",
            "state": "posted",
            "company_id": 1,
            "journal_id": [81, "Stock Journal"],
            "date": "2026-03-18",
            "ref": _pcb_case1_reference(
                picking_name="LHPK/IN/0001",
                bill_name="BILL/2026/0001",
                item_code="SKU-001",
                item_name="Produk PCB",
            ),
            "partner_id": 77,
            "line_ids": [
                {"account_id": 302, "debit": 120.0, "credit": 0.0, "partner_id": 77},
                {"account_id": 301, "debit": 0.0, "credit": 120.0, "partner_id": 77},
            ],
        }
        rpc._append_move_line_record(981, {"account_id": 302, "debit": 120.0, "credit": 0.0, "partner_id": 77})
        rpc._append_move_line_record(981, {"account_id": 301, "debit": 0.0, "credit": 120.0, "partner_id": 77})
        service = SvlDashboardRepairServiceAsync(rpc=rpc, logger=logging.getLogger("test.dashboard.repair"))

        summary = await service.execute_pcb_case1(
            SvlDashboardPcbCase1RepairRequest(
                database="hwgroup_erp",
                rows=[_build_pcb_case1_row(result_move_id=0, result_move_name="", result_posted=False)],
                posting_mode="post",
            )
        )

        result = summary.results[0]
        self.assertEqual(result.status, "POSTED")
        self.assertTrue(result.existing_move_detected)
        self.assertEqual(result.move_id, 981)
        self.assertEqual(result.move_name, "STJ/2026/0981")
        self.assertEqual(summary.created_count, 0)
        self.assertEqual(summary.existing_count, 1)
        self.assertEqual(rpc.next_move_id, 900)

    async def test_pcb_case1_execute_post_posts_existing_draft_match(self) -> None:
        rpc = _FakeRepairRpc()
        rpc.moves[982] = {
            "id": 982,
            "name": "STJ/2026/0982",
            "state": "draft",
            "company_id": 1,
            "journal_id": [81, "Stock Journal"],
            "date": "2026-03-18",
            "ref": _pcb_case1_reference(
                picking_name="LHPK/IN/0001",
                bill_name="BILL/2026/0001",
                item_code="SKU-001",
                item_name="Produk PCB",
            ),
            "partner_id": 77,
            "line_ids": [
                {"account_id": 302, "debit": 120.0, "credit": 0.0, "partner_id": 77},
                {"account_id": 301, "debit": 0.0, "credit": 120.0, "partner_id": 77},
            ],
        }
        rpc._append_move_line_record(982, {"account_id": 302, "debit": 120.0, "credit": 0.0, "partner_id": 77})
        rpc._append_move_line_record(982, {"account_id": 301, "debit": 0.0, "credit": 120.0, "partner_id": 77})
        service = SvlDashboardRepairServiceAsync(rpc=rpc, logger=logging.getLogger("test.dashboard.repair"))

        summary = await service.execute_pcb_case1(
            SvlDashboardPcbCase1RepairRequest(
                database="hwgroup_erp",
                rows=[_build_pcb_case1_row(result_move_id=982, result_move_name="STJ/2026/0982", result_posted=False)],
                posting_mode="post",
            )
        )

        result = summary.results[0]
        self.assertEqual(result.status, "POSTED")
        self.assertTrue(result.posted)
        self.assertTrue(result.existing_move_detected)
        self.assertIn("langsung dipost", normalize_text(result.message).lower())
        self.assertEqual(rpc.moves[982]["state"], "posted")
        self.assertIn(("action_post", [982]), rpc.execute_calls)
        self.assertEqual(summary.created_count, 0)
        self.assertEqual(summary.existing_count, 1)
        self.assertEqual(summary.posted_count, 1)
        self.assertEqual(rpc.next_move_id, 900)

    async def test_pcb_case1_line_label_fallback_uses_purchase_cycle_balance_text(self) -> None:
        rpc = _FakeRepairRpc()
        service = SvlDashboardRepairServiceAsync(rpc=rpc, logger=logging.getLogger("test.dashboard.repair"))

        summary = await service.execute_pcb_case1(
            SvlDashboardPcbCase1RepairRequest(
                database="hwgroup_erp",
                rows=[_build_pcb_case1_row(line_label="", item_code="SKU-001", item_name="Produk PCB")],
                posting_mode="draft",
            )
        )

        self.assertEqual(summary.results[0].status, "CREATED")
        created_move = rpc.moves[summary.results[0].move_id]
        line_names = [normalize_text(line.get("name")) for line in created_move.get("line_ids", [])]
        self.assertEqual(line_names, ["SKU-001 - Produk PCB - Purchase Cycle Balance"] * 2)

    async def test_pcb_case1_schema_fallback_and_ambiguous_reconcile_skip(self) -> None:
        rpc = _FakeRepairRpc()
        for field_name in ("analytic_distribution", "amount_currency", "stock_move_id", "picking_id", "bill_line_id", "bill_move_id"):
            rpc.fields_map["account.move.line"].pop(field_name, None)
        rpc.move_line_rows[9101] = {
            "id": 9101,
            "move_id": 8801,
            "account_id": 302,
            "partner_id": 77,
            "currency_id": 13,
            "debit": 0.0,
            "credit": 120.0,
            "balance": -120.0,
            "reconciled": False,
        }
        rpc.move_line_rows[9102] = {
            "id": 9102,
            "move_id": 8802,
            "account_id": 301,
            "partner_id": 77,
            "currency_id": 13,
            "debit": 120.0,
            "credit": 0.0,
            "balance": 120.0,
            "reconciled": False,
        }
        rpc.move_line_rows[9103] = dict(rpc.move_line_rows[9101], id=9103, move_id=8803)
        rpc.move_line_rows[9104] = dict(rpc.move_line_rows[9102], id=9104, move_id=8804)
        service = SvlDashboardRepairServiceAsync(rpc=rpc, logger=logging.getLogger("test.dashboard.repair"))

        summary = await service.execute_pcb_case1(
            SvlDashboardPcbCase1RepairRequest(
                database="hwgroup_erp",
                rows=[
                    _build_pcb_case1_row(
                        clearing_target_aml_ids=[9101, 9103],
                        suspend_target_aml_ids=[9102, 9104],
                    )
                ],
                posting_mode="post",
            )
        )

        result = summary.results[0]
        self.assertEqual(result.status, "POSTED")
        self.assertEqual(result.reconcile_error_kind, "")
        self._assert_pcb_case1_post_skips_reconcile(result=result, rpc=rpc, summary=summary)
        move = rpc.moves[result.move_id]
        self.assertNotIn("analytic_distribution", move["line_ids"][0])
        self.assertNotIn("amount_currency", move["line_ids"][0])
        self.assertNotIn("stock_move_id", move["line_ids"][0])

    async def test_pcb_case2_draft_equal_problem_and_hpp_creates_two_lines_without_selisih(self) -> None:
        rpc = _FakeRepairRpc()
        service = SvlDashboardRepairServiceAsync(rpc=rpc, logger=logging.getLogger("test.dashboard.repair"))

        summary = await service.execute_pcb_case2(
            SvlDashboardPcbCase2RepairRequest(
                database="hwgroup_erp",
                rows=[_build_pcb_case2_row()],
                posting_mode="draft",
            )
        )

        result = summary.results[0]
        self.assertEqual(result.status, "CREATED")
        self.assertEqual(summary.created_count, 1)
        move = rpc.moves[result.move_id]
        self.assertEqual(len(move["line_ids"]), 2)
        self.assertEqual(move["line_ids"][0]["account_id"], 301)
        self.assertEqual(move["line_ids"][0]["debit"], 120.0)
        self.assertEqual(move["line_ids"][0]["purchase_line_id"], 8301)
        self.assertEqual(move["line_ids"][0]["stock_move_id"], 8401)
        self.assertEqual(move["line_ids"][0]["bill_line_id"], 8101)
        self.assertEqual(move["line_ids"][0]["picking_id"], 7001)
        self.assertEqual(move["line_ids"][0]["bill_move_id"], 8201)
        self.assertEqual(move["line_ids"][1]["account_id"], 202)
        self.assertEqual(move["line_ids"][1]["credit"], 120.0)
        self.assertEqual(move["line_ids"][1]["purchase_line_id"], 8301)
        self.assertEqual(move["line_ids"][1]["stock_move_id"], 8401)
        self.assertEqual(move["line_ids"][1]["bill_line_id"], 8101)

    async def test_pcb_case1_draft_adds_diff_line_when_problem_amounts_do_not_match(self) -> None:
        rpc = _FakeRepairRpc()
        service = SvlDashboardRepairServiceAsync(rpc=rpc, logger=logging.getLogger("test.dashboard.repair"))

        summary = await service.execute_pcb_case1(
            SvlDashboardPcbCase1RepairRequest(
                database="hwgroup_erp",
                rows=[
                    _build_pcb_case1_row(
                        amount=120.0,
                        debit_amount=120.0,
                        credit_amount=90.0,
                        diff_account_code="510001",
                        diff_account_name="COGS",
                        diff_side="credit",
                        diff_amount=30.0,
                        amount_currency=90.0,
                        amount_currency_basis=90.0,
                    )
                ],
                posting_mode="draft",
            )
        )

        result = summary.results[0]
        self.assertEqual(result.status, "CREATED")
        move = rpc.moves[result.move_id]
        self.assertEqual(len(move["line_ids"]), 3)
        self.assertEqual(move["line_ids"][0]["account_id"], 302)
        self.assertEqual(move["line_ids"][0]["debit"], 120.0)
        self.assertEqual(move["line_ids"][1]["account_id"], 301)
        self.assertEqual(move["line_ids"][1]["credit"], 90.0)
        self.assertEqual(move["line_ids"][2]["account_id"], 202)
        self.assertEqual(move["line_ids"][2]["credit"], 30.0)
        self.assertNotIn("currency_id", move["line_ids"][0])
        self.assertNotIn("amount_currency", move["line_ids"][0])
        self.assertNotIn("currency_id", move["line_ids"][1])
        self.assertNotIn("amount_currency", move["line_ids"][1])
        self.assertNotIn("currency_id", move["line_ids"][2])
        self.assertNotIn("amount_currency", move["line_ids"][2])

    async def test_pcb_case1_draft_rewrites_move_lines_when_create_zeroes_nominal(self) -> None:
        rpc = _FakeRepairRpc()
        rpc.zero_move_line_amounts_on_create = True
        service = SvlDashboardRepairServiceAsync(rpc=rpc, logger=logging.getLogger("test.dashboard.repair"))

        summary = await service.execute_pcb_case1(
            SvlDashboardPcbCase1RepairRequest(
                database="hwgroup_erp",
                rows=[
                    _build_pcb_case1_row(
                        amount=120.0,
                        debit_amount=120.0,
                        credit_amount=90.0,
                        diff_account_code="510001",
                        diff_account_name="COGS",
                        diff_side="credit",
                        diff_amount=30.0,
                        amount_currency=90.0,
                        amount_currency_basis=90.0,
                    )
                ],
                posting_mode="draft",
            )
        )

        result = summary.results[0]
        self.assertEqual(result.status, "CREATED")
        self.assertIn("diselaraskan ulang", normalize_text(result.message).lower())
        move = rpc.moves[result.move_id]
        self.assertEqual(len(move["line_ids"]), 3)
        self.assertEqual([line["account_id"] for line in move["line_ids"]], [302, 301, 202])
        self.assertEqual(move["line_ids"][0]["debit"], 120.0)
        self.assertEqual(move["line_ids"][1]["credit"], 90.0)
        self.assertEqual(move["line_ids"][2]["credit"], 30.0)
        self.assertNotIn("currency_id", move["line_ids"][0])
        self.assertNotIn("amount_currency", move["line_ids"][0])
        self.assertNotIn("currency_id", move["line_ids"][1])
        self.assertNotIn("amount_currency", move["line_ids"][1])
        self.assertNotIn("currency_id", move["line_ids"][2])
        self.assertNotIn("amount_currency", move["line_ids"][2])

    async def test_pcb_case2_execute_post_posts_existing_draft_match(self) -> None:
        rpc = _FakeRepairRpc()
        rpc.moves[983] = {
            "id": 983,
            "name": "STJ/2026/0983",
            "state": "draft",
            "company_id": 1,
            "journal_id": [81, "Stock Journal"],
            "date": "2026-03-18",
            "ref": _pcb_case1_reference(
                picking_name="LHPK/IN/0001",
                bill_name="BILL/2026/0001",
                item_code="SKU-001",
                item_name="Produk PCB",
            ),
            "partner_id": 77,
            "line_ids": [
                {"account_id": 301, "debit": 120.0, "credit": 0.0, "partner_id": 77},
                {"account_id": 202, "debit": 0.0, "credit": 120.0, "partner_id": 77},
            ],
        }
        rpc._append_move_line_record(983, {"account_id": 301, "debit": 120.0, "credit": 0.0, "partner_id": 77})
        rpc._append_move_line_record(983, {"account_id": 202, "debit": 0.0, "credit": 120.0, "partner_id": 77})
        service = SvlDashboardRepairServiceAsync(rpc=rpc, logger=logging.getLogger("test.dashboard.repair"))

        summary = await service.execute_pcb_case2(
            SvlDashboardPcbCase2RepairRequest(
                database="hwgroup_erp",
                rows=[_build_pcb_case2_row(result_move_id=983, result_move_name="STJ/2026/0983", result_posted=False)],
                posting_mode="post",
            )
        )

        result = summary.results[0]
        self.assertEqual(result.status, "POSTED")
        self.assertTrue(result.posted)
        self.assertTrue(result.existing_move_detected)
        self.assertIn("langsung dipost", normalize_text(result.message).lower())
        self.assertEqual(rpc.moves[983]["state"], "posted")
        self.assertIn(("action_post", [983]), rpc.execute_calls)
        self.assertEqual(summary.created_count, 0)
        self.assertEqual(summary.existing_count, 1)
        self.assertEqual(summary.posted_count, 1)
        self.assertEqual(rpc.next_move_id, 900)

    async def test_pcb_case2_schema_fallback_skips_relation_fields_when_not_supported(self) -> None:
        rpc = _FakeRepairRpc()
        for field_name in ("stock_move_id", "purchase_line_id", "bill_line_id", "vendor_bill_line_id"):
            rpc.fields_map["account.move.line"].pop(field_name, None)
        service = SvlDashboardRepairServiceAsync(rpc=rpc, logger=logging.getLogger("test.dashboard.repair"))

        summary = await service.execute_pcb_case2(
            SvlDashboardPcbCase2RepairRequest(
                database="hwgroup_erp",
                rows=[_build_pcb_case2_row()],
                posting_mode="draft",
            )
        )

        result = summary.results[0]
        self.assertEqual(result.status, "CREATED")
        move = rpc.moves[result.move_id]
        self.assertNotIn("purchase_line_id", move["line_ids"][0])
        self.assertNotIn("stock_move_id", move["line_ids"][0])
        self.assertNotIn("bill_line_id", move["line_ids"][0])

    def test_build_pcb_case2_row_from_mapping_preserves_trace_and_hint_lists(self) -> None:
        row = SvlDashboardRepairServiceAsync._build_pcb_case2_row_from_mapping(
            {
                "row_key": "case4::7001::901",
                "company_id": 7,
                "amount": 120.0,
                "date": "2026-03-18",
                "reference": "Correction: Purchase Cycle Balance: LHPK/IN/0001 / BILL/2026/0001 / SKU-001",
                "line_label": "SKU-001 - Produk PCB - Case 4 - STJ Bill Hit Expenses (Suspend - Expenses)",
                "cycle_key": "case4::7001",
                "pcb_case": "case4",
                "pcb_case_label": "Case 4 - STJ Bill Hit Expenses (Suspend - Expenses)",
                "product_id": 901,
                "item_code": "SKU-001",
                "item_name": "Produk PCB",
                "bill_line_id": 8101,
                "purchase_line_id": 8301,
                "stock_move_id": 8401,
                "stock_move_ids": [8401, 8402],
                "stj_move_ids": [8801],
                "stj_refs": ["STJ/2026/0451"],
                "payment_move_ids": [8501],
                "bank_move_ids": [8601],
                "suspend_target_aml_ids": [9102],
                "clearing_target_aml_ids": [9101],
                "product_uom_id": 11,
                "quantity": 4.0,
                "currency_id": 13,
                "amount_currency": 160.0,
                "amount_currency_basis": 120.0,
                "analytic_distribution": {"CC-01": 100.0},
                "bill_price_unit": 40.0,
                "gr_price_unit": 10.0,
                "price_gap_value": 120.0,
                "allocated_amount": 120.0,
                "review_required": True,
                "review_confirmed": False,
                "review_reason": "Manual review wajib",
                "planned_lines": [
                    {
                        "role": "problem_2103006",
                        "account_code": "2103006",
                        "account_name": "Hutang Suspend",
                        "amount": 120.0,
                        "side": "debit",
                    },
                    {
                        "role": "selisih_hpp",
                        "account_code": "510001",
                        "account_name": "COGS",
                        "amount": 120.0,
                        "side": "credit",
                    },
                ],
            }
        )

        self.assertEqual(row.payment_move_ids, [8501])
        self.assertEqual(row.bank_move_ids, [8601])
        self.assertEqual(row.suspend_target_aml_ids, [9102])
        self.assertEqual(row.clearing_target_aml_ids, [9101])
        self.assertEqual(row.stock_move_ids, [8401, 8402])
        self.assertEqual(row.stj_refs, ["STJ/2026/0451"])
        self.assertEqual(row.product_uom_id, 11)
        self.assertEqual(row.quantity, 4.0)
        self.assertEqual(row.currency_id, 13)
        self.assertEqual(row.amount_currency, 160.0)
        self.assertEqual(row.analytic_distribution, {"CC-01": 100.0})
        self.assertEqual(row.bill_price_unit, 40.0)
        self.assertEqual(row.gr_price_unit, 10.0)
        self.assertEqual(row.price_gap_value, 120.0)
        self.assertEqual(row.allocated_amount, 120.0)
        self.assertTrue(row.review_required)
        self.assertFalse(row.review_confirmed)
        self.assertEqual(row.review_reason, "Manual review wajib")

    async def test_pcb_case2_execute_ignores_target_hint_lists_for_reconcile(self) -> None:
        rpc = _FakeRepairRpc()
        service = SvlDashboardRepairServiceAsync(rpc=rpc, logger=logging.getLogger("test.dashboard.repair"))

        summary = await service.execute_pcb_case2(
            SvlDashboardPcbCase2RepairRequest(
                database="hwgroup_erp",
                rows=[
                    _build_pcb_case2_row(
                        payment_move_ids=[8501],
                        bank_move_ids=[8601],
                        suspend_target_aml_ids=[9102],
                        clearing_target_aml_ids=[9101],
                    )
                ],
                posting_mode="post",
            )
        )

        result = summary.results[0]
        self.assertEqual(result.status, "POSTED")
        self.assertEqual(len(rpc.partial_reconciles), 0)

    async def test_pcb_case2_draft_zeroes_hpp_then_creates_selisih_hpp_line(self) -> None:
        rpc = _FakeRepairRpc()
        service = SvlDashboardRepairServiceAsync(rpc=rpc, logger=logging.getLogger("test.dashboard.repair"))

        summary = await service.execute_pcb_case2(
            SvlDashboardPcbCase2RepairRequest(
                database="hwgroup_erp",
                rows=[
                    _build_pcb_case2_row(
                        amount=100.0,
                        planned_lines=[
                            SvlDashboardPcbRepairPlannedLine(
                                role="problem_2103006",
                                account_code="2103006",
                                account_name="Hutang Suspend",
                                amount=100.0,
                                side="debit",
                            ),
                            SvlDashboardPcbRepairPlannedLine(
                                role="hpp_zero",
                                account_code="510001",
                                account_name="COGS",
                                amount=70.0,
                                side="credit",
                            ),
                            SvlDashboardPcbRepairPlannedLine(
                                role="selisih_hpp",
                                account_code="510001",
                                account_name="COGS",
                                amount=30.0,
                                side="credit",
                            ),
                        ],
                    )
                ],
                posting_mode="draft",
            )
        )

        result = summary.results[0]
        move = rpc.moves[result.move_id]
        self.assertEqual(len(move["line_ids"]), 3)
        self.assertEqual(move["line_ids"][2]["account_id"], 202)
        self.assertEqual(move["line_ids"][2]["credit"], 30.0)
        self.assertTrue(normalize_text(move["line_ids"][2]["name"]).startswith("Selisih HPP - "))

    async def test_pcb_case2_payload_matches_three_line_simulation_amounts_and_labels(self) -> None:
        rpc = _FakeRepairRpc()
        service = SvlDashboardRepairServiceAsync(rpc=rpc, logger=logging.getLogger("test.dashboard.repair"))
        base_label = "A-BESJ-0001 - JINRO CHAMISUL ORI FRESH"

        summary = await service.execute_pcb_case2(
            SvlDashboardPcbCase2RepairRequest(
                database="hwgroup_erp",
                rows=[
                    _build_pcb_case2_row(
                        line_label=base_label,
                        planned_lines=[
                            SvlDashboardPcbRepairPlannedLine(
                                role="problem_1108099",
                                account_code="1108099",
                                account_name="Clearing",
                                amount=11008000.0,
                                side="debit",
                                line_label=base_label,
                            ),
                            SvlDashboardPcbRepairPlannedLine(
                                role="problem_2103006",
                                account_code="2103006",
                                account_name="Hutang Suspend",
                                amount=11000000.0,
                                side="credit",
                                line_label=base_label,
                            ),
                            SvlDashboardPcbRepairPlannedLine(
                                role="selisih_hpp",
                                account_code="510001",
                                account_name="COGS",
                                amount=8000.0,
                                side="credit",
                                line_label=f"{base_label} - Selisih HPP",
                            ),
                        ],
                    )
                ],
                posting_mode="draft",
            )
        )

        result = summary.results[0]
        move = rpc.moves[result.move_id]
        actual_lines = [
            (
                int(line.get("account_id") or 0),
                float(line.get("debit") or 0.0),
                float(line.get("credit") or 0.0),
                normalize_text(line.get("name")),
            )
            for line in move["line_ids"]
        ]
        self.assertEqual(
            actual_lines,
            [
                (302, 11008000.0, 0.0, base_label),
                (301, 0.0, 11000000.0, base_label),
                (202, 0.0, 8000.0, f"Selisih HPP - {base_label}"),
            ],
        )

    def test_pcb_case2_payload_signature_guard_blocks_mismatch(self) -> None:
        service = SvlDashboardRepairServiceAsync(rpc=_FakeRepairRpc(), logger=logging.getLogger("test.dashboard.repair"))

        with self.assertRaisesRegex(RuntimeError, "Payload PCB Case 2 berbeda"):
            service._assert_pcb_case2_payload_matches_simulation(
                resolved_simulation_lines=[
                    (
                        {
                            "line_index": 0,
                            "side": "debit",
                            "amount": 100.0,
                            "line_label": "Selisih HPP - ITEM-001",
                        },
                        type("Account", (), {"account_id": 202, "code": "510001"})(),
                    )
                ],
                line_commands=[
                    [0, 0, {"name": "Mismatch Label", "account_id": 202, "debit": 90.0, "credit": 0.0}],
                ],
            )

    async def test_pcb_case2_draft_allows_variance_zero_line_without_selisih_hpp(self) -> None:
        rpc = _FakeRepairRpc()
        service = SvlDashboardRepairServiceAsync(rpc=rpc, logger=logging.getLogger("test.dashboard.repair"))

        summary = await service.execute_pcb_case2(
            SvlDashboardPcbCase2RepairRequest(
                database="hwgroup_erp",
                rows=[
                    _build_pcb_case2_row(
                        amount=120.0,
                        planned_lines=[
                            SvlDashboardPcbRepairPlannedLine(
                                role="problem_2103006",
                                account_code="2103006",
                                account_name="Hutang Suspend",
                                amount=120.0,
                                side="debit",
                            ),
                            SvlDashboardPcbRepairPlannedLine(
                                role="variance_zero_5101010",
                                account_code="5101010",
                                account_name="COGS Variance",
                                amount=120.0,
                                side="credit",
                            ),
                        ],
                    )
                ],
                posting_mode="draft",
            )
        )

        result = summary.results[0]
        self.assertEqual(result.status, "CREATED")
        move = rpc.moves[result.move_id]
        self.assertEqual(len(move["line_ids"]), 2)
        self.assertEqual(move["line_ids"][0]["account_id"], 301)
        self.assertEqual(move["line_ids"][0]["debit"], 120.0)
        self.assertEqual(move["line_ids"][1]["account_id"], 203)
        self.assertEqual(move["line_ids"][1]["credit"], 120.0)

    async def test_pcb_case2_post_negative_and_positive_sides_follow_planned_line_direction(self) -> None:
        rpc = _FakeRepairRpc()
        service = SvlDashboardRepairServiceAsync(rpc=rpc, logger=logging.getLogger("test.dashboard.repair"))

        summary = await service.execute_pcb_case2(
            SvlDashboardPcbCase2RepairRequest(
                database="hwgroup_erp",
                rows=[
                    _build_pcb_case2_row(
                        planned_lines=[
                            SvlDashboardPcbRepairPlannedLine(
                                role="problem_2103006",
                                account_code="2103006",
                                account_name="Hutang Suspend",
                                amount=50.0,
                                side="credit",
                            ),
                            SvlDashboardPcbRepairPlannedLine(
                                role="hpp_zero",
                                account_code="510001",
                                account_name="COGS",
                                amount=20.0,
                                side="debit",
                            ),
                            SvlDashboardPcbRepairPlannedLine(
                                role="selisih_hpp",
                                account_code="510001",
                                account_name="COGS",
                                amount=30.0,
                                side="debit",
                            ),
                        ],
                    )
                ],
                posting_mode="post",
            )
        )

        result = summary.results[0]
        self.assertEqual(result.status, "POSTED")
        move = rpc.moves[result.move_id]
        self.assertEqual(move["line_ids"][0]["credit"], 50.0)
        self.assertEqual(move["line_ids"][1]["debit"], 20.0)
        self.assertEqual(move["line_ids"][2]["debit"], 30.0)
        self.assertEqual(move["state"], "posted")

    async def test_pcb_case2_rejects_missing_expense_account_when_balancing_line_exists(self) -> None:
        rpc = _FakeRepairRpc()
        service = SvlDashboardRepairServiceAsync(rpc=rpc, logger=logging.getLogger("test.dashboard.repair"))

        summary = await service.execute_pcb_case2(
            SvlDashboardPcbCase2RepairRequest(
                database="hwgroup_erp",
                rows=[
                    _build_pcb_case2_row(
                        planned_lines=[
                            SvlDashboardPcbRepairPlannedLine(
                                role="problem_2103006",
                                account_code="2103006",
                                account_name="Hutang Suspend",
                                amount=100.0,
                                side="debit",
                            ),
                            SvlDashboardPcbRepairPlannedLine(
                                role="hpp_zero",
                                account_code="",
                                account_name="",
                                amount=70.0,
                                side="credit",
                            ),
                            SvlDashboardPcbRepairPlannedLine(
                                role="selisih_hpp",
                                account_code="",
                                account_name="",
                                amount=30.0,
                                side="credit",
                            ),
                        ],
                    )
                ],
                posting_mode="draft",
            )
        )

        result = summary.results[0]
        self.assertEqual(result.status, "ERROR")
        self.assertEqual(result.error_kind, "account_missing")
        self.assertIn("Akun Zero HPP PCB Case 2 belum terisi", result.message)
        self.assertEqual(summary.error_count, 1)

    async def test_pcb_case2_rejects_review_required_until_confirmed(self) -> None:
        rpc = _FakeRepairRpc()
        service = SvlDashboardRepairServiceAsync(rpc=rpc, logger=logging.getLogger("test.dashboard.repair"))

        summary = await service.execute_pcb_case2(
            SvlDashboardPcbCase2RepairRequest(
                database="hwgroup_erp",
                rows=[
                    _build_pcb_case2_row(
                        review_required=True,
                        review_confirmed=False,
                        review_reason="Manual review wajib sebelum execute.",
                    )
                ],
                posting_mode="draft",
            )
        )

        result = summary.results[0]
        self.assertEqual(result.status, "ERROR")
        self.assertEqual(result.error_kind, "review_required")
        self.assertIn("Manual review wajib", result.message)
        self.assertEqual(summary.error_count, 1)

    async def test_pcb_case2_allows_execute_after_review_confirmed(self) -> None:
        rpc = _FakeRepairRpc()
        service = SvlDashboardRepairServiceAsync(rpc=rpc, logger=logging.getLogger("test.dashboard.repair"))

        summary = await service.execute_pcb_case2(
            SvlDashboardPcbCase2RepairRequest(
                database="hwgroup_erp",
                rows=[
                    _build_pcb_case2_row(
                        review_required=True,
                        review_confirmed=True,
                        review_reason="Manual review wajib sebelum execute.",
                    )
                ],
                posting_mode="draft",
            )
        )

        result = summary.results[0]
        self.assertEqual(result.status, "CREATED")
        self.assertEqual(summary.created_count, 1)

    async def test_pcb_case2_execute_uses_final_planned_lines_not_resolve_hint(self) -> None:
        rpc = _FakeRepairRpc()
        service = SvlDashboardRepairServiceAsync(rpc=rpc, logger=logging.getLogger("test.dashboard.repair"))

        summary = await service.execute_pcb_case2(
            SvlDashboardPcbCase2RepairRequest(
                database="hwgroup_erp",
                rows=[
                    _build_pcb_case2_row(
                        expense_account_code="510001",
                        resolve_account_code="1108099",
                        resolve_account_preview="1108099 - Clearing",
                        planned_lines=[
                            SvlDashboardPcbRepairPlannedLine(
                                role="problem_2103006",
                                account_code="2103006",
                                account_name="Hutang Suspend",
                                amount=120.0,
                                side="debit",
                            ),
                            SvlDashboardPcbRepairPlannedLine(
                                role="selisih_hpp",
                                account_code="1108099",
                                account_name="Clearing",
                                amount=120.0,
                                side="credit",
                            ),
                        ],
                    )
                ],
                posting_mode="draft",
            )
        )

        result = summary.results[0]
        self.assertEqual(result.status, "CREATED")
        move = rpc.moves[result.move_id]
        self.assertEqual(move["line_ids"][0]["account_id"], 301)
        self.assertEqual(move["line_ids"][1]["account_id"], 302)

    async def test_execute_pcb_repair_row_blocks_legacy_case5_clearing_plan(self) -> None:
        rpc = _FakeRepairRpc()
        service = SvlDashboardRepairServiceAsync(rpc=rpc, logger=logging.getLogger("test.dashboard.repair"))
        row = _build_pcb_case2_row(
            row_key="case5::7001::901",
            cycle_key="case5::7001",
            pcb_case="case5",
            pcb_case_label="Case 5 - No STJ Bill Miss Match (Undirect Clearing - Suspend)",
            repair_basis_amount=100.0,
            repair_basis_source="standard_cost_x_qty",
            standard_price=25.0,
            planned_lines=[
                SvlDashboardPcbRepairPlannedLine(
                    role="problem_2103006",
                    account_code="2103006",
                    account_name="Hutang Suspend",
                    amount=120.0,
                    side="debit",
                ),
                SvlDashboardPcbRepairPlannedLine(
                    role="synthetic_clearing",
                    account_code="1108099",
                    account_name="Clearing",
                    amount=100.0,
                    side="credit",
                ),
                SvlDashboardPcbRepairPlannedLine(
                    role="selisih_hpp",
                    account_code="510001",
                    account_name="COGS",
                    amount=20.0,
                    side="credit",
                ),
            ],
        )

        result = await service.execute_pcb_repair_row(
            {
                field_name: getattr(row, field_name)
                for field_name in SvlDashboardPcbCase2RepairRow.__dataclass_fields__
            },
            post=False,
        )

        self.assertEqual(result["status"], "ERROR")
        self.assertEqual(result["error_kind"], "receipt_recovery_blocked")
        self.assertEqual(set(rpc.moves), {500, 501})

    async def test_execute_pcb_repair_row_routes_case9_and_keeps_review_gate(self) -> None:
        rpc = _FakeRepairRpc()
        service = SvlDashboardRepairServiceAsync(rpc=rpc, logger=logging.getLogger("test.dashboard.repair"))
        row = _build_pcb_case2_row(
            row_key="case9::7001::901",
            cycle_key="case9::7001",
            pcb_case="case9",
            pcb_case_label="Case 9 - UoM Scale Mismatch",
            review_required=True,
            review_confirmed=False,
            review_reason="Case 9 wajib review.",
            planned_lines=[
                SvlDashboardPcbRepairPlannedLine(
                    role="case9_hpp_reclass",
                    account_code="510001",
                    account_name="COGS",
                    amount=120.0,
                    side="debit",
                ),
                SvlDashboardPcbRepairPlannedLine(
                    role="case9_clearing_offset",
                    account_code="1108099",
                    account_name="Clearing",
                    amount=120.0,
                    side="credit",
                ),
            ],
            case_evidence={"holder_basis": "case2_downstream_clearing"},
        )
        payload = {
            field_name: getattr(row, field_name)
            for field_name in SvlDashboardPcbCase2RepairRow.__dataclass_fields__
        }

        blocked = await service.execute_pcb_repair_row(payload, post=False)
        self.assertEqual(blocked["status"], "ERROR")
        self.assertEqual(blocked["error_kind"], "review_required")

        payload["review_confirmed"] = True
        created = await service.execute_pcb_repair_row(payload, post=False)

        self.assertEqual(created["status"], "CREATED")
        move = rpc.moves[created["move_id"]]
        self.assertEqual([line["account_id"] for line in move["line_ids"]], [202, 302])

    async def test_pcb_case2_draft_allows_two_problem_accounts_in_one_move(self) -> None:
        rpc = _FakeRepairRpc()
        service = SvlDashboardRepairServiceAsync(rpc=rpc, logger=logging.getLogger("test.dashboard.repair"))

        summary = await service.execute_pcb_case2(
            SvlDashboardPcbCase2RepairRequest(
                database="hwgroup_erp",
                rows=[
                    _build_pcb_case2_row(
                        planned_lines=[
                            SvlDashboardPcbRepairPlannedLine(
                                role="problem_1108099",
                                account_code="1108099",
                                account_name="Clearing",
                                amount=40.0,
                                side="debit",
                            ),
                            SvlDashboardPcbRepairPlannedLine(
                                role="problem_2103006",
                                account_code="2103006",
                                account_name="Hutang Suspend",
                                amount=60.0,
                                side="debit",
                            ),
                            SvlDashboardPcbRepairPlannedLine(
                                role="selisih_hpp",
                                account_code="510001",
                                account_name="COGS",
                                amount=100.0,
                                side="credit",
                            ),
                        ],
                    )
                ],
                posting_mode="draft",
            )
        )

        move = rpc.moves[summary.results[0].move_id]
        self.assertEqual(len(move["line_ids"]), 3)
        self.assertEqual(move["line_ids"][0]["account_id"], 302)
        self.assertEqual(move["line_ids"][1]["account_id"], 301)
        self.assertEqual(move["line_ids"][2]["account_id"], 202)


if __name__ == "__main__":
    unittest.main()
