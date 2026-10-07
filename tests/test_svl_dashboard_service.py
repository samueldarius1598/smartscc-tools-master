import asyncio
import logging
import unittest

from smartscc_tools.features.svl_fix_je.dashboard_service import SvlDashboardServiceAsync
from smartscc_tools.features.svl_fix_je.models import (
    SvlDashboardCycleAccountRow,
    SvlDashboardCycleItemRow,
    SvlDashboardPcbAdjustmentAuditRow,
    SvlDashboardPurchaseCycle,
    SvlDashboardRequest,
)


class _FakeDashboardRpc:
    def __init__(
        self,
        *,
        include_issues: bool = True,
        purchase_line_supported: bool = True,
        svl_move_supported: bool = True,
        svl_move_read_fail: bool = False,
        include_unassigned_journal: bool = False,
        include_category_accounts: bool = True,
        account_rows: list[dict] | None = None,
        svl_native_date_supported: bool = True,
    ) -> None:
        self.login_calls = 0
        self.svl_move_read_fail = svl_move_read_fail
        self.fields_map = {
            "stock.valuation.layer": {
                "create_date": {"type": "datetime"},
                "account_move_id": {"type": "many2one"},
            },
            "account.move.line": {
                "parent_state": {"type": "char"},
                "display_type": {"type": "char"},
            },
            "account.account": {
                "company_ids": {"type": "many2many"},
                "account_type": {"type": "selection"},
            },
            "stock.move": {
                "reference": {"type": "char"},
                "origin": {"type": "char"},
                "picking_id": {"type": "many2one"},
            },
            "product.product": {
                "default_code": {"type": "char"},
                "name": {"type": "char"},
                "categ_id": {"type": "many2one"},
            },
            "product.category": {
                "property_stock_valuation_account_id": {"type": "many2one"},
                "property_account_expense_categ_id": {"type": "many2one"},
                "property_stock_account_input_categ_id": {"type": "many2one"},
                "property_stock_account_output_categ_id": {"type": "many2one"},
            },
        }
        if svl_native_date_supported:
            self.fields_map["stock.valuation.layer"]["date"] = {"type": "date"}
        if svl_move_supported:
            self.fields_map["stock.valuation.layer"]["stock_move_id"] = {"type": "many2one"}
        if purchase_line_supported:
            self.fields_map["account.move.line"]["purchase_line_id"] = {"type": "many2one"}

        self.company_rows = [
            {"id": 2, "name": "Beta Company"},
            {"id": 1, "name": "Alpha Company"},
        ]
        self.category_rows = (
            [
                {
                    "id": 1,
                    "name": "Raw Material",
                    "property_valuation": "real_time",
                    "property_stock_valuation_account_id": [10, "Persediaan"],
                    "property_account_expense_categ_id": [20, "COGS"],
                    "property_stock_account_input_categ_id": [30, "Stock Input"],
                    "property_stock_account_output_categ_id": [40, "Stock Output"],
                }
            ]
            if include_category_accounts
            else []
        )
        self.account_rows = account_rows or [
            {"id": 10, "code": "114001", "name": "Persediaan Barang", "company_ids": [1], "account_type": "asset_current"},
            {"id": 20, "code": "510001", "name": "Beban Pokok", "company_ids": [1], "account_type": "expense"},
            {"id": 30, "code": "210001", "name": "Stock Input", "company_ids": [1], "account_type": "liability_current"},
            {"id": 40, "code": "410001", "name": "Stock Output", "company_ids": [1], "account_type": "income"},
            {"id": 60, "code": "211001", "name": "Hutang Vendor", "company_ids": [1], "account_type": "liability_payable"},
        ]
        self.stock_move_rows = {
            9001: {"id": 9001, "reference": "WH/IN/001"},
            9002: {"id": 9002, "reference": "WCGT/INT/00036"},
            9003: {"id": 9003, "reference": "WH/IN/003"},
        }
        self.account_move_rows = {
            5001: {"id": 5001, "name": "STJ/2026/0001", "state": "posted", "journal_id": [81, "Stock Journal"], "date": "2026-01-05"},
            5002: {"id": 5002, "name": "STJ/2026/0002", "state": "posted", "journal_id": [81, "Stock Journal"], "date": "2026-01-07"},
            5999: {"id": 5999, "name": "MISC/2026/0001", "state": "posted", "journal_id": [82, "Misc Journal"], "date": "2026-01-08"},
            5212: {"id": 5212, "name": "STJ/2026/02/1212", "state": "posted", "journal_id": [81, "Stock Journal"], "date": "2026-01-09"},
            5367: {"id": 5367, "name": "STJ/2026/03/0367", "state": "posted", "journal_id": [81, "Stock Journal"], "date": "2026-01-10"},
        }
        self.product_rows = [
            {"id": 101, "name": "Produk A", "default_code": "SKU-A", "categ_id": [1, "Raw"], "standard_price": 50.0, "cost_method": "standard"},
            {"id": 102, "name": "Produk B", "default_code": "SKU-B", "categ_id": [1, "Raw"], "standard_price": 25.0, "cost_method": "standard"},
            {"id": 103, "name": "Produk C", "default_code": "SKU-C", "categ_id": [1, "Raw"], "standard_price": 30.0, "cost_method": "standard"},
        ]
        self.svl_rows = [
            {
                "id": 1001,
                "company_id": 1,
                "product_id": [101, "Produk A"],
                "quantity": 2.0,
                "unit_cost": 50.0,
                "value": 100.0,
                "description": "WH/IN/001 - Produk A",
                "date": "2026-01-05",
                "create_date": "2026-01-05 10:00:00",
                "account_move_id": [5001, "STJ/2026/0001"],
                "account_move_id.date": "2026-01-05",
                "stock_move_id": [9001, "MOVE/9001"],
            },
            {
                "id": 1002,
                "company_id": 1,
                "product_id": [101, "Produk A"],
                "quantity": 1.0,
                "unit_cost": 50.0,
                "value": 50.0,
                "description": "Inventory Valuation",
                "date": "2026-01-06",
                "create_date": "2026-01-06 10:00:00",
                "account_move_id": False,
                "stock_move_id": [9002, "MOVE/9002"],
            },
            {
                "id": 1003,
                "company_id": 1,
                "product_id": [102, "Produk B"],
                "quantity": 2.0,
                "unit_cost": 25.0,
                "value": 50.0,
                "description": "WH/IN/003 - Produk B",
                "date": "2026-01-07",
                "create_date": "2026-01-07 10:00:00",
                "account_move_id": [5002, "STJ/2026/0002"],
                "account_move_id.date": "2026-01-07",
                "stock_move_id": [9003, "MOVE/9003"],
            },
        ]
        if not include_issues:
            self.svl_rows = [
                {
                    "id": 1001,
                    "company_id": 1,
                    "product_id": [101, "Produk A"],
                    "quantity": 2.0,
                    "unit_cost": 50.0,
                    "value": 100.0,
                    "description": "WH/IN/001 - Produk A",
                    "date": "2026-01-05",
                    "create_date": "2026-01-05 10:00:00",
                    "account_move_id": [5001, "STJ/2026/0001"],
                    "account_move_id.date": "2026-01-05",
                    "stock_move_id": [9001, "MOVE/9001"],
                }
            ]

        self.journal_rows = [
            {
                "id": 2001,
                "company_id": 1,
                "account_id": 10,
                "product_id": [101, "Produk A"],
                "move_id": [5001, "STJ/2026/0001"],
                "move_id.state": "posted",
                "date": "2026-01-05",
                "debit": 100.0,
                "credit": 0.0,
                "ref": "WH/IN/001",
                "name": "Receipt A",
                "parent_state": "posted",
                "display_type": False,
            },
        ]
        if include_issues:
            self.journal_rows.append(
                {
                    "id": 2002,
                    "company_id": 1,
                    "account_id": 10,
                    "product_id": [102, "Produk B"],
                    "move_id": [5002, "STJ/2026/0002"],
                    "move_id.state": "posted",
                    "date": "2026-01-07",
                    "debit": 50.0,
                    "credit": 0.0,
                    "ref": "WH/IN/003",
                    "name": "Receipt B",
                    "parent_state": "posted",
                    "display_type": False,
                }
            )
        if include_issues:
            self.journal_rows.append(
                {
                    "id": 2003,
                    "company_id": 1,
                    "account_id": 10,
                    "product_id": [103, "Produk C"],
                    "move_id": [5999, "MISC/2026/0001"],
                    "move_id.state": "posted",
                    "date": "2026-01-08",
                    "debit": 30.0,
                    "credit": 0.0,
                    "ref": "Revaluation",
                    "name": "Revaluation",
                    "parent_state": "posted",
                    "display_type": False,
                }
            )
        if include_unassigned_journal:
            self.journal_rows.append(
                {
                    "id": 2004,
                    "company_id": 1,
                    "account_id": 10,
                    "product_id": False,
                    "move_id": [5212, "STJ/2026/02/1212"],
                    "move_id.state": "posted",
                    "date": "2026-01-09",
                    "debit": 40.0,
                    "credit": 0.0,
                    "ref": "Unassigned Valuation",
                    "name": "Unassigned Valuation",
                    "parent_state": "posted",
                    "display_type": False,
                }
            )
            self.journal_rows.append(
                {
                    "id": 2005,
                    "company_id": 1,
                    "account_id": 10,
                    "product_id": False,
                    "move_id": [5367, "STJ/2026/03/0367"],
                    "move_id.state": "posted",
                    "date": "2026-01-10",
                    "debit": 0.0,
                    "credit": 15.0,
                    "ref": "Unassigned Valuation 2",
                    "name": "Unassigned Valuation 2",
                    "parent_state": "posted",
                    "display_type": False,
                }
            )

        self.po_rows = [
            {
                "id": 3001,
                "company_id": 1,
                "product_id": [101, "Produk A"],
                "order_id": [7001, "PO/2026/0001"],
                "order_id.state": "purchase",
                "price_unit": 45.0,
                "qty_received": 3.0,
                "qty_invoiced": 2.0,
            }
        ]
        self.bill_rows = [
            {
                "id": 4001,
                "company_id": 1,
                "purchase_line_id": [3001, "PO Line A"],
                "move_id": [8001, "BILL/2026/0001"],
                "move_id.state": "posted",
                "price_unit": 47.0,
                "quantity": 2.0,
                "price_subtotal": 94.0,
                "date": "2026-01-09",
                "parent_state": "posted",
                "display_type": False,
            }
        ]
        self.payable_aml_rows = [
            {
                "id": 4501,
                "company_id": 1,
                "move_id": [8001, "BILL/2026/0001"],
                "date": "2026-01-09",
                "debit": 0.0,
                "credit": 94.0,
                "account_id": [60, "Hutang Vendor"],
                "ref": "BILL/2026/0001",
                "name": "Vendor Payable",
                "parent_state": "posted",
                "display_type": False,
            }
        ]

    async def ensure_login(self) -> int:
        self.login_calls += 1
        return 77

    async def fields_get(self, model, attributes=None, context=None, stage=""):  # noqa: ANN001
        return self.fields_map.get(model, {})

    async def read(self, model, ids, fields=None, context=None, stage="", excel_row=0):  # noqa: ANN001
        if model == "res.company":
            return [row for row in self.company_rows if row["id"] in ids]
        if model == "account.account":
            return [row for row in self.account_rows if row["id"] in ids]
        if model == "product.category":
            return [row for row in self.category_rows if row["id"] in ids]
        if model == "product.product":
            return [row for row in self.product_rows if row["id"] in ids]
        if model == "stock.move":
            if self.svl_move_read_fail:
                raise RuntimeError("stock_move_id read blocked")
            return [self.stock_move_rows[row_id] for row_id in ids if row_id in self.stock_move_rows]
        if model == "account.move":
            return [self.account_move_rows[row_id] for row_id in ids if row_id in self.account_move_rows]
        return []

    async def search_read(self, model, domain, fields=None, limit=None, context=None, stage="", order=None):  # noqa: ANN001
        source = []
        if model == "res.company":
            source = list(self.company_rows)
        elif model == "product.category":
            source = list(self.category_rows)
        elif model == "account.account":
            source = list(self.account_rows)
        elif model == "stock.valuation.layer":
            source = list(self.svl_rows)
        elif model == "account.move.line":
            if any(item[0] == "purchase_line_id" for item in domain):
                source = list(self.bill_rows)
            elif any(item[0] == "move_id" for item in domain) and any(item[0] == "account_id" for item in domain):
                source = list(self.payable_aml_rows)
            else:
                source = list(self.journal_rows)
        elif model == "account.move":
            source = list(self.account_move_rows.values())
        elif model == "purchase.order.line":
            source = list(self.po_rows)
        elif model == "product.product":
            source = list(self.product_rows)

        rows = [row.copy() for row in source if self._matches(row, domain)]
        if order == "name":
            rows.sort(key=lambda item: item.get("name", ""))
        if limit:
            rows = rows[:limit]
        return rows

    async def read_group(self, model, domain, fields, groupby, lazy=True, context=None, stage="", excel_row=0):  # noqa: ANN001
        if model == "stock.valuation.layer":
            rows = [row for row in self.svl_rows if self._matches(row, domain)]
            return self._group_rows(rows, groupby, include_credit=False)
        if model == "account.move.line":
            rows = [row for row in self.journal_rows if self._matches(row, domain)]
            return self._group_rows(rows, groupby, include_credit=True)
        return []

    def _group_rows(self, rows: list[dict], groupby: list[str], *, include_credit: bool) -> list[dict]:
        if groupby == ["company_id"]:
            totals = {"company_id": [1, "Alpha Company"], "value": 0.0, "quantity": 0.0, "debit": 0.0, "credit": 0.0}
            for row in rows:
                totals["value"] += float(row.get("value") or 0.0)
                totals["quantity"] += float(row.get("quantity") or 0.0)
                totals["debit"] += float(row.get("debit") or 0.0)
                totals["credit"] += float(row.get("credit") or 0.0)
            return [totals]

        key_name = groupby[0]
        grouped: dict[int, dict] = {}
        for row in rows:
            value = row.get(key_name)
            key = value[0] if isinstance(value, (list, tuple)) and value else value
            if not key:
                continue
            bucket = grouped.setdefault(key, {key_name: value, "value": 0.0, "quantity": 0.0, "debit": 0.0, "credit": 0.0})
            bucket["value"] += float(row.get("value") or 0.0)
            bucket["quantity"] += float(row.get("quantity") or 0.0)
            bucket["debit"] += float(row.get("debit") or 0.0)
            bucket["credit"] += float(row.get("credit") or 0.0)
        result = []
        for key in sorted(grouped):
            bucket = grouped[key]
            if include_credit:
                result.append({key_name: bucket[key_name], "debit": bucket["debit"], "credit": bucket["credit"]})
            else:
                result.append({key_name: bucket[key_name], "value": bucket["value"], "quantity": bucket["quantity"]})
        return result

    def _matches(self, row: dict, domain: list[tuple]) -> bool:
        for field_name, operator, value in domain:
            current = row.get(field_name)
            if current is None and field_name == "account_move_id.date":
                move_id = row.get("account_move_id")
                move_id = move_id[0] if isinstance(move_id, (list, tuple)) and move_id else move_id
                current = (self.account_move_rows.get(move_id) or {}).get("date")
            current_id = current[0] if isinstance(current, (list, tuple)) and current else current
            if operator == "=":
                if value is False:
                    if current not in (False, None, ""):
                        return False
                elif current_id != value:
                    return False
            elif operator == "!=":
                if value is False:
                    if current in (False, None, ""):
                        return False
                elif current_id == value:
                    return False
            elif operator == "in":
                target = set(value)
                if isinstance(current, list) and current and not isinstance(current[0], (str, bytes)):
                    if not target.intersection(set(current)):
                        return False
                elif current in ([], (), None, False, ""):
                    return False
                elif current_id not in target:
                    return False
            elif operator == ">":
                if float(current or 0.0) <= float(value):
                    return False
            elif operator == ">=":
                if str(current or "") < str(value):
                    return False
            elif operator == "<=":
                if str(current or "") > str(value):
                    return False
            else:
                raise AssertionError(f"Unsupported operator in fake RPC: {operator}")
        return True


def _build_case1_account_rows() -> list[SvlDashboardCycleAccountRow]:
    return [
        SvlDashboardCycleAccountRow(
            account_id=301,
            code="2103006",
            name="Hutang Suspend",
            account_type="liability_current",
            account_group="liability",
            debit=0.0,
            credit=300.0,
            net_balance=-300.0,
            status="problem",
        ),
        SvlDashboardCycleAccountRow(
            account_id=302,
            code="1108099",
            name="Clearing",
            account_type="asset_current",
            account_group="asset",
            debit=300.0,
            credit=0.0,
            net_balance=300.0,
            status="problem",
        ),
    ]


def _build_case1_item_rows(
    *,
    entries: list[tuple[int, float, float]] | None = None,
) -> list[SvlDashboardCycleItemRow]:
    clean_entries = entries or [(101, -300.0, 300.0)]
    rows: list[SvlDashboardCycleItemRow] = []
    for product_id, suspend_balance, clearing_balance in clean_entries:
        rows.append(
            SvlDashboardCycleItemRow(
                product_id=product_id,
                product_name=f"Produk {product_id}",
                default_code=f"SKU-{product_id}",
                valuation_method="automated",
                account_rows=[
                    SvlDashboardCycleAccountRow(
                        account_id=301,
                        code="2103006",
                        name="Hutang Suspend",
                        account_type="liability_current",
                        account_group="liability",
                        debit=max(float(suspend_balance), 0.0),
                        credit=max(-float(suspend_balance), 0.0),
                        net_balance=float(suspend_balance),
                        status="problem",
                    ),
                    SvlDashboardCycleAccountRow(
                        account_id=302,
                        code="1108099",
                        name="Clearing",
                        account_type="asset_current",
                        account_group="asset",
                        debit=max(float(clearing_balance), 0.0),
                        credit=max(-float(clearing_balance), 0.0),
                        net_balance=float(clearing_balance),
                        status="problem",
                    ),
                ],
            )
        )
    return rows


def _build_case1_purchase_cycle_inputs(
    *,
    include_second_cycle: bool = False,
) -> tuple[dict, list[dict], dict[int, dict], dict[int, dict], dict[int, dict]]:
    trace: dict = {
        "picking_rows_by_id": {
            7001: {
                "id": 7001,
                "name": "WCGT/IN/01062",
                "scheduled_date": "2026-03-11",
                "partner_id": [77, "Vendor Alpha"],
            },
        },
        "po_id_by_picking": {7001: 5001},
        "stj_ids_by_picking": {7001: {8801}},
        "product_ids_by_picking": {7001: {101}},
        "inventory_types_by_picking": {7001: ["purchase"]},
        "bill_ids_by_product": {101: {8001}},
        "payment_rows_by_bill_id": {},
        "bill_rows_by_id": {
            8001: {
                "id": 8001,
                "name": "BILL/2026/02/0114",
                "invoice_date": "2026-02-10",
                "date": "2026-02-10",
                "invoice_origin": "PO/WCGT/2026/01/01001",
                "payment_state": "not_paid",
            },
        },
        "purchase_order_rows_by_id": {
            5001: {
                "id": 5001,
                "name": "PO/WCGT/2026/01/01001",
                "partner_id": [77, "Vendor Alpha"],
            },
        },
        "purchase_line_rows_by_id": {
            3001: {
                "id": 3001,
                "product_id": [101, "ANGGUR HITAM / BLACK GRAPES"],
                "order_id": [5001, "PO/WCGT/2026/01/01001"],
            },
        },
        "stock_move_rows_by_id": {
            8401: {
                "id": 8401,
                "product_id": [101, "ANGGUR HITAM / BLACK GRAPES"],
                "picking_id": [7001, "WCGT/IN/01062"],
                "purchase_line_id": [3001, "PO Line 3001"],
                "account_move_ids": [8801],
                "price_unit": 0.0,
                "product_qty": 3.0,
            },
        },
        "bill_line_rows_by_product": {
            101: [
                {
                    "id": 8101,
                    "move_id": [8001, "BILL/2026/02/0114"],
                    "account_id": [301, "Hutang Suspend"],
                    "product_id": [101, "ANGGUR HITAM / BLACK GRAPES"],
                    "purchase_line_id": [3001, "PO Line 3001"],
                    "price_unit": 80000.0,
                    "quantity": 3.0,
                    "price_subtotal": 240000.0,
                    "balance": 240000.0,
                },
                {
                    "id": 8102,
                    "move_id": [8001, "BILL/2026/02/0114"],
                    "account_id": [301, "Hutang Suspend"],
                    "product_id": [101, "ANGGUR HITAM / BLACK GRAPES"],
                    "purchase_line_id": [3001, "PO Line 3001"],
                    "price_unit": 7700.0,
                    "quantity": 3.0,
                    "price_subtotal": 23100.0,
                    "balance": 23100.0,
                },
            ],
        },
        "purchase_line_product_map": {3001: 101},
        "purchase_line_po_name_map": {3001: "PO/WCGT/2026/01/01001"},
        "payment_move_ids_by_product": {},
        "bank_move_ids_by_product": {},
        "matching_numbers_by_payment_move_id": {},
        "bank_move_ids_by_matching": {},
        "direct_bank_move_ids_by_bill": {},
        "direct_bills_by_bank_move_id": {},
        "expansion_bills_by_bill": {},
        "expansion_stjs_by_bill": {},
        "expansion_bills_by_stj": {},
        "pcb_correction_move_ids": [8811],
        "stj_value_by_move_product": {8801: {101: 263100.0}},
        "return_picking_pairs": [],
    }
    all_ledger_rows = [
        {
            "id": 9101,
            "move_id": [8801, "STJ/2026/03/0447"],
            "account_id": [401, "Persediaan Makanan"],
            "product_id": [101, "ANGGUR HITAM / BLACK GRAPES"],
            "purchase_line_id": [3001, "PO Line 3001"],
            "stock_move_id": [8401, "MOVE/8401"],
            "date": "2026-03-16",
            "name": "Koreksi Inventory",
            "debit": 263100.0,
            "credit": 0.0,
            "balance": 263100.0,
        },
        {
            "id": 9102,
            "move_id": [8801, "STJ/2026/03/0447"],
            "account_id": [302, "Clearing"],
            "product_id": [101, "ANGGUR HITAM / BLACK GRAPES"],
            "purchase_line_id": [3001, "PO Line 3001"],
            "stock_move_id": [8401, "MOVE/8401"],
            "date": "2026-03-16",
            "name": "Koreksi Clearing",
            "debit": 0.0,
            "credit": 263100.0,
            "balance": -263100.0,
        },
        {
            "id": 8101,
            "move_id": [8001, "BILL/2026/02/0114"],
            "account_id": [301, "Hutang Suspend"],
            "product_id": [101, "ANGGUR HITAM / BLACK GRAPES"],
            "purchase_line_id": [3001, "PO Line 3001"],
            "product_uom_id": [11, "KG"],
            "currency_id": [13, "IDR"],
            "date": "2026-02-10",
            "name": "Bill suspend utama",
            "debit": 240000.0,
            "credit": 0.0,
            "balance": 240000.0,
            "price_unit": 80000.0,
            "quantity": 3.0,
            "price_subtotal": 240000.0,
            "amount_currency": 240000.0,
            "analytic_distribution": {"1540": 100.0},
        },
        {
            "id": 8102,
            "move_id": [8001, "BILL/2026/02/0114"],
            "account_id": [301, "Hutang Suspend"],
            "product_id": [101, "ANGGUR HITAM / BLACK GRAPES"],
            "purchase_line_id": [3001, "PO Line 3001"],
            "product_uom_id": [11, "KG"],
            "currency_id": [13, "IDR"],
            "date": "2026-02-10",
            "name": "Bill suspend selisih",
            "debit": 23100.0,
            "credit": 0.0,
            "balance": 23100.0,
            "price_unit": 7700.0,
            "quantity": 3.0,
            "price_subtotal": 23100.0,
            "amount_currency": 23100.0,
            "analytic_distribution": {"1540": 100.0},
        },
        {
            "id": 8103,
            "move_id": [8001, "BILL/2026/02/0114"],
            "account_id": [303, "Selisih HPP / COGS Variance"],
            "product_id": [101, "ANGGUR HITAM / BLACK GRAPES"],
            "purchase_line_id": [3001, "PO Line 3001"],
            "date": "2026-02-10",
            "name": "COGS variance",
            "debit": 0.0,
            "credit": 23100.0,
            "balance": -23100.0,
        },
        {
            "id": 9111,
            "move_id": [8811, "STJ/2026/03/0811"],
            "account_id": [302, "Clearing"],
            "product_id": [101, "ANGGUR HITAM / BLACK GRAPES"],
            "date": "2026-03-26",
            "name": "Correction clearing",
            "debit": 263100.0,
            "credit": 0.0,
            "balance": 263100.0,
        },
        {
            "id": 9112,
            "move_id": [8811, "STJ/2026/03/0811"],
            "account_id": [301, "Hutang Suspend"],
            "product_id": [101, "ANGGUR HITAM / BLACK GRAPES"],
            "date": "2026-03-26",
            "name": "Correction suspend",
            "debit": 0.0,
            "credit": 263100.0,
            "balance": -263100.0,
        },
    ]
    move_info_map = {
        8001: {
            "id": 8001,
            "name": "BILL/2026/02/0114",
            "move_type": "in_invoice",
            "partner_id": [77, "Vendor Alpha"],
            "ref": "Vendor Bill 0114",
        },
        8801: {
            "id": 8801,
            "name": "STJ/2026/03/0447",
            "move_type": "entry",
            "ref": "Original STJ",
        },
        8811: {
            "id": 8811,
            "name": "STJ/2026/03/0811",
            "move_type": "entry",
            "ref": "Corrrection: Purchase Cycle Balance: WCGT/IN/01062 / BILL/2026/02/0114 / F-FHVF-0006 - ANGGUR HITAM / BLACK GRAPES",
        },
    }
    if include_second_cycle:
        trace["picking_rows_by_id"][7002] = {
            "id": 7002,
            "name": "WCGT/IN/01063",
            "scheduled_date": "2026-03-12",
            "partner_id": [77, "Vendor Alpha"],
        }
        trace["po_id_by_picking"][7002] = 5002
        trace["stj_ids_by_picking"][7002] = {8802}
        trace["product_ids_by_picking"][7002] = {101}
        trace["inventory_types_by_picking"][7002] = ["purchase"]
        trace["bill_rows_by_id"][8002] = {
            "id": 8002,
            "name": "BILL/2026/02/0115",
            "invoice_date": "2026-02-11",
            "date": "2026-02-11",
            "invoice_origin": "PO/WCGT/2026/01/01002",
            "payment_state": "not_paid",
        }
        trace["purchase_order_rows_by_id"][5002] = {
            "id": 5002,
            "name": "PO/WCGT/2026/01/01002",
            "partner_id": [77, "Vendor Alpha"],
        }
        trace["purchase_line_rows_by_id"][3002] = {
            "id": 3002,
            "product_id": [101, "ANGGUR HITAM / BLACK GRAPES"],
            "order_id": [5002, "PO/WCGT/2026/01/01002"],
        }
        trace["stock_move_rows_by_id"][8402] = {
            "id": 8402,
            "product_id": [101, "ANGGUR HITAM / BLACK GRAPES"],
            "picking_id": [7002, "WCGT/IN/01063"],
            "purchase_line_id": [3002, "PO Line 3002"],
            "account_move_ids": [8802],
            "price_unit": 0.0,
            "product_qty": 2.0,
        }
        trace["bill_line_rows_by_product"][101].append(
            {
                "id": 8201,
                "move_id": [8002, "BILL/2026/02/0115"],
                "account_id": [301, "Hutang Suspend"],
                "product_id": [101, "ANGGUR HITAM / BLACK GRAPES"],
                "purchase_line_id": [3002, "PO Line 3002"],
                "price_unit": 50000.0,
                "quantity": 2.0,
                "price_subtotal": 100000.0,
                "balance": 100000.0,
            }
        )
        trace["purchase_line_product_map"][3002] = 101
        trace["purchase_line_po_name_map"][3002] = "PO/WCGT/2026/01/01002"
        trace["stj_value_by_move_product"][8802] = {101: 100000.0}
        all_ledger_rows.extend(
            [
                {
                    "id": 9201,
                    "move_id": [8802, "STJ/2026/03/0448"],
                    "account_id": [401, "Persediaan Makanan"],
                    "product_id": [101, "ANGGUR HITAM / BLACK GRAPES"],
                    "purchase_line_id": [3002, "PO Line 3002"],
                    "stock_move_id": [8402, "MOVE/8402"],
                    "date": "2026-03-16",
                    "name": "Inventory second cycle",
                    "debit": 100000.0,
                    "credit": 0.0,
                    "balance": 100000.0,
                },
                {
                    "id": 9202,
                    "move_id": [8802, "STJ/2026/03/0448"],
                    "account_id": [302, "Clearing"],
                    "product_id": [101, "ANGGUR HITAM / BLACK GRAPES"],
                    "purchase_line_id": [3002, "PO Line 3002"],
                    "stock_move_id": [8402, "MOVE/8402"],
                    "date": "2026-03-16",
                    "name": "Clearing second cycle",
                    "debit": 0.0,
                    "credit": 100000.0,
                    "balance": -100000.0,
                },
                {
                    "id": 8201,
                    "move_id": [8002, "BILL/2026/02/0115"],
                    "account_id": [301, "Hutang Suspend"],
                    "product_id": [101, "ANGGUR HITAM / BLACK GRAPES"],
                    "purchase_line_id": [3002, "PO Line 3002"],
                    "product_uom_id": [11, "KG"],
                    "date": "2026-02-11",
                    "name": "Bill suspend second cycle",
                    "debit": 100000.0,
                    "credit": 0.0,
                    "balance": 100000.0,
                    "price_unit": 50000.0,
                    "quantity": 2.0,
                    "price_subtotal": 100000.0,
                },
            ]
        )
        move_info_map[8002] = {
            "id": 8002,
            "name": "BILL/2026/02/0115",
            "move_type": "in_invoice",
            "partner_id": [77, "Vendor Alpha"],
            "ref": "Vendor Bill 0115",
        }
        move_info_map[8802] = {
            "id": 8802,
            "name": "STJ/2026/03/0448",
            "move_type": "entry",
            "ref": "Original STJ second cycle",
        }
    account_info_map = {
        301: {"code": "2103006", "name": "Hutang Suspend", "account_type": "liability_current"},
        302: {"code": "1108099", "name": "Clearing", "account_type": "asset_current"},
        303: {"code": "5101010", "name": "Selisih HPP / COGS Variance", "account_type": "expense_direct_cost"},
        401: {"code": "1105003", "name": "Persediaan Makanan / Food Inventory", "account_type": "asset_current"},
    }
    product_info_map = {
        101: {
            "default_code": "F-FHVF-0006",
            "name": "ANGGUR HITAM / BLACK GRAPES",
            "categ_name": "VEGETABLES & FRUITS",
        }
    }
    return trace, all_ledger_rows, account_info_map, move_info_map, product_info_map


def _build_pcb_adjustment_move(
    *,
    move_id: int = 9901,
    move_name: str = "RAC/2026/01/0026",
    move_ref: str = "Adjustment clearing from standard price",
    move_date: str = "2026-03-27",
    product_id: int = 101,
    purchase_line_id: int = 3001,
    stock_move_id: int = 0,
    bill_move_id: int = 0,
    picking_id: int = 0,
    amount: float = 23100.0,
    source_line_name: str = "",
    origin_moves_by_name: dict[str, dict] | None = None,
) -> dict:
    source_row = {
        "id": move_id * 10 + 1,
        "move_id": [move_id, move_name],
        "account_id": [303, "Selisih HPP / COGS Variance"],
        "name": source_line_name,
        "product_id": [product_id, f"Produk {product_id}"] if product_id > 0 else False,
        "purchase_line_id": [purchase_line_id, f"PO Line {purchase_line_id}"] if purchase_line_id > 0 else False,
        "stock_move_id": [stock_move_id, f"MOVE/{stock_move_id}"] if stock_move_id > 0 else False,
        "bill_move_id": [bill_move_id, f"BILL/{bill_move_id}"] if bill_move_id > 0 else False,
        "picking_id": [picking_id, f"PICK/{picking_id}"] if picking_id > 0 else False,
        "debit": 0.0,
        "credit": amount,
        "balance": -amount,
    }
    clearing_row = {
        "id": move_id * 10 + 2,
        "move_id": [move_id, move_name],
        "account_id": [302, "Clearing"],
        "name": "",
        "product_id": [product_id, f"Produk {product_id}"] if product_id > 0 else False,
        "purchase_line_id": [purchase_line_id, f"PO Line {purchase_line_id}"] if purchase_line_id > 0 else False,
        "stock_move_id": [stock_move_id, f"MOVE/{stock_move_id}"] if stock_move_id > 0 else False,
        "bill_move_id": [bill_move_id, f"BILL/{bill_move_id}"] if bill_move_id > 0 else False,
        "picking_id": [picking_id, f"PICK/{picking_id}"] if picking_id > 0 else False,
        "debit": amount,
        "credit": 0.0,
        "balance": amount,
    }
    return {
        "move_id": move_id,
        "move_name": move_name,
        "move_ref": move_ref,
        "move_date": move_date,
        "lines": [source_row, clearing_row],
        "origin_moves_by_name": dict(origin_moves_by_name or {}),
    }


class SvlDashboardServiceTest(unittest.IsolatedAsyncioTestCase):
    def test_pcb_payment_link_field_order_prefers_invoice_ids(self) -> None:
        self.assertEqual(
            SvlDashboardServiceAsync._pcb_payment_link_field_order(
                {"invoice_ids": {"type": "many2many"}, "reconciled_bill_ids": {"type": "many2many"}}
            ),
            ["invoice_ids"],
        )
        self.assertEqual(
            SvlDashboardServiceAsync._pcb_payment_link_field_order({"reconciled_bill_ids": {"type": "many2many"}}),
            ["reconciled_bill_ids"],
        )
        self.assertEqual(SvlDashboardServiceAsync._pcb_payment_link_field_order({}), [])

    async def test_search_read_in_chunks_runs_chunk_requests_concurrently(self) -> None:
        class _ChunkTrackingRpc:
            def __init__(self) -> None:
                self.max_inflight = 0
                self.inflight = 0
                self.calls: list[tuple[list[tuple], str]] = []

            async def search_read(self, model, domain, fields=None, limit=None, context=None, stage="", order=None):  # noqa: ANN001
                self.calls.append((list(domain), str(stage)))
                self.inflight += 1
                self.max_inflight = max(self.max_inflight, self.inflight)
                await asyncio.sleep(0.01)
                self.inflight -= 1
                target_ids = []
                for field_name, operator, value in domain:
                    if field_name == "id" and operator == "in":
                        target_ids = [int(current) for current in list(value or [])]
                        break
                return [{"id": value} for value in target_ids]

        rpc = _ChunkTrackingRpc()
        service = SvlDashboardServiceAsync(rpc=rpc, logger=logging.getLogger("test.dashboard"))

        rows = await service._search_read_in_chunks(
            "stock.move",
            ids_field="id",
            ids=list(range(1, 401)),
            fields=["id"],
            context={"lang": "en_US"},
            stage="TEST_CHUNKS",
            base_domain=[("state", "=", "done")],
            order="id",
        )

        self.assertEqual([row["id"] for row in rows], list(range(1, 401)))
        self.assertEqual(len(rpc.calls), 2)
        self.assertGreaterEqual(rpc.max_inflight, 2)
        self.assertEqual([stage for _domain, stage in rpc.calls], ["TEST_CHUNKS_1", "TEST_CHUNKS_2"])
        self.assertTrue(all(("state", "=", "done") in domain for domain, _stage in rpc.calls))

    async def test_group_aml_by_move_and_account_falls_back_when_read_group_shape_is_incompatible(self) -> None:
        rpc = _FakeDashboardRpc(include_issues=True)
        service = SvlDashboardServiceAsync(rpc=rpc, logger=logging.getLogger("test.dashboard"))
        aml_fields = await service._fields_get_cached("account.move.line")

        rows = await service._group_aml_by_move_and_account(
            move_ids=[5001, 5002, 5999],
            company_id=1,
            context={},
            aml_fields=aml_fields,
            stage="TEST_AML_GROUPED",
        )

        grouped = {
            (
                row["move_id"][0] if isinstance(row.get("move_id"), (list, tuple)) else row.get("move_id"),
                row["account_id"][0] if isinstance(row.get("account_id"), (list, tuple)) else row.get("account_id"),
            ): (float(row.get("debit") or 0.0), float(row.get("credit") or 0.0))
            for row in rows
        }
        self.assertEqual(grouped[(5001, 10)], (100.0, 0.0))
        self.assertEqual(grouped[(5002, 10)], (50.0, 0.0))
        self.assertEqual(grouped[(5999, 10)], (30.0, 0.0))

    async def test_list_companies_returns_sorted_companies(self) -> None:
        rpc = _FakeDashboardRpc()
        service = SvlDashboardServiceAsync(rpc=rpc, logger=logging.getLogger("test.dashboard"))

        companies = await service.list_companies()

        self.assertEqual([company.name for company in companies], ["Alpha Company", "Beta Company"])
        self.assertEqual(companies[0].label, "Alpha Company (#1)")

    async def test_analyze_builds_snapshot_with_orphans_and_po_bill(self) -> None:
        rpc = _FakeDashboardRpc()
        service = SvlDashboardServiceAsync(rpc=rpc, logger=logging.getLogger("test.dashboard"))
        request = SvlDashboardRequest(
            database="hwgroup_erp",
            company_id=1,
            date_from="2026-01-01",
            date_to="2026-01-31",
            inventory_coa_codes=["114001"],
        )

        snapshot = await service.analyze(request)

        self.assertEqual(snapshot.company_name, "Alpha Company")
        self.assertEqual(snapshot.account_count, 1)
        self.assertEqual([item.pid for item in snapshot.items], [101, 103])
        first = snapshot.items[0]
        self.assertEqual(first.svl_orphan_count, 1)
        self.assertEqual(first.difference, 50.0)
        self.assertEqual(first.total_po_value, 0.0)
        self.assertEqual(first.total_bill_value, 0.0)
        self.assertEqual(len(first.svl_records), 2)
        self.assertEqual(len(first.jnl_records), 1)
        self.assertEqual(first.po_lines, [])
        self.assertEqual(first.bill_lines, [])
        self.assertEqual(first.svl_record_count, 2)
        self.assertEqual(first.jnl_record_count, 1)
        self.assertEqual(first.po_line_count, 0)
        self.assertEqual(first.bill_line_count, 0)
        second = snapshot.items[1]
        self.assertEqual(second.jnl_orphan_count, 1)
        self.assertEqual(second.difference, -30.0)

        detail = await service.fetch_item_detail(request, first.pid)

        self.assertIn("WCGT/INT/00036", [record.reference for record in detail.svl_records])
        self.assertEqual(len(detail.jnl_records), 1)
        self.assertEqual(len(detail.po_lines), 1)
        self.assertEqual(len(detail.bill_lines), 1)
        self.assertEqual(detail.total_po_value, 135.0)
        self.assertEqual(detail.total_bill_value, 94.0)
        self.assertEqual(detail.po_line_count, 1)
        self.assertEqual(detail.bill_line_count, 1)

    async def test_analyze_warns_when_purchase_line_is_missing_and_uses_date_fallback(self) -> None:
        rpc = _FakeDashboardRpc(purchase_line_supported=False)
        service = SvlDashboardServiceAsync(rpc=rpc, logger=logging.getLogger("test.dashboard"))
        request = SvlDashboardRequest(
            database="hwgroup_erp",
            company_id=1,
            date_from="2026-01-01",
            date_to="2026-01-31",
            inventory_coa_codes=["114001"],
        )

        snapshot = await service.analyze(request)

        self.assertEqual(snapshot.items[0].po_lines, [])
        detail = await service.fetch_item_detail(request, snapshot.items[0].pid)
        self.assertEqual(detail.po_lines, [])
        self.assertEqual(detail.bill_lines, [])
        self.assertEqual(detail.svl_records[0].date, "2026-01-05")
        self.assertEqual(detail.jnl_records[0].account_code, "114001")
        self.assertEqual(detail.jnl_records[0].account_name, "Persediaan Barang")

    async def test_analyze_returns_empty_when_no_issue_exists(self) -> None:
        rpc = _FakeDashboardRpc(include_issues=False)
        service = SvlDashboardServiceAsync(rpc=rpc, logger=logging.getLogger("test.dashboard"))

        snapshot = await service.analyze(
            SvlDashboardRequest(
                database="hwgroup_erp",
                company_id=1,
                date_from="2026-01-01",
                date_to="2026-01-31",
                inventory_coa_codes=["114001"],
            )
        )

        self.assertEqual(snapshot.items, [])

    async def test_analyze_creates_unassigned_journal_pseudo_product(self) -> None:
        rpc = _FakeDashboardRpc(include_unassigned_journal=True)
        service = SvlDashboardServiceAsync(rpc=rpc, logger=logging.getLogger("test.dashboard"))
        request = SvlDashboardRequest(
            database="hwgroup_erp",
            company_id=1,
            date_from="2026-01-01",
            date_to="2026-01-31",
            inventory_coa_codes=["114001"],
        )

        snapshot = await service.analyze(request)

        pseudo = next(item for item in snapshot.items if item.pid == -1)
        self.assertEqual(pseudo.item_kind, "unassigned_journal")
        self.assertEqual(pseudo.code, "UNASSIGNED-JNL")
        self.assertEqual([row.journal_entry for row in pseudo.jnl_records], ["STJ/2026/02/1212", "STJ/2026/03/0367"])
        self.assertEqual(pseudo.jnl_record_count, 2)
        detail = await service.fetch_item_detail(request, pseudo.pid)
        self.assertEqual(
            [row.journal_entry for row in detail.jnl_records],
            ["STJ/2026/02/1212", "STJ/2026/03/0367"],
        )
        self.assertFalse(any("STJ/2026/02/1212" in warning for warning in snapshot.warnings))

    async def test_fetch_many_item_details_returns_product_and_unassigned_payloads(self) -> None:
        rpc = _FakeDashboardRpc(include_unassigned_journal=True)
        service = SvlDashboardServiceAsync(rpc=rpc, logger=logging.getLogger("test.dashboard"))
        request = SvlDashboardRequest(
            database="hwgroup_erp",
            company_id=1,
            date_from="2026-01-01",
            date_to="2026-01-31",
            inventory_coa_codes=["114001"],
        )

        detail_by_pid = await service.fetch_many_item_details(request, [101, -1])

        self.assertEqual(sorted(detail_by_pid), [-1, 101])
        self.assertEqual(len(detail_by_pid[101].svl_records), 2)
        self.assertEqual(len(detail_by_pid[101].jnl_records), 1)
        self.assertEqual(detail_by_pid[101].po_line_count, 1)
        self.assertEqual(detail_by_pid[101].bill_line_count, 1)
        self.assertEqual(
            [row.journal_entry for row in detail_by_pid[-1].jnl_records],
            ["STJ/2026/02/1212", "STJ/2026/03/0367"],
        )

    async def test_analyze_falls_back_to_description_when_stock_move_reference_read_fails(self) -> None:
        rpc = _FakeDashboardRpc(svl_move_read_fail=True)
        service = SvlDashboardServiceAsync(rpc=rpc, logger=logging.getLogger("test.dashboard"))
        request = SvlDashboardRequest(
            database="hwgroup_erp",
            company_id=1,
            date_from="2026-01-01",
            date_to="2026-01-31",
            inventory_coa_codes=["114199"],
        )

        snapshot = await service.analyze(request)

        self.assertTrue(any("stock.move" in warning for warning in snapshot.warnings))
        detail = await service.fetch_item_detail(request, snapshot.items[0].pid)
        self.assertEqual(detail.svl_records[0].reference, "WH/IN/001")

    async def test_hydrate_snapshot_details_returns_copy_with_full_item_detail(self) -> None:
        rpc = _FakeDashboardRpc()
        service = SvlDashboardServiceAsync(rpc=rpc, logger=logging.getLogger("test.dashboard"))
        request = SvlDashboardRequest(
            database="hwgroup_erp",
            company_id=1,
            date_from="2026-01-01",
            date_to="2026-01-31",
            inventory_coa_codes=["114001"],
        )

        snapshot = await service.analyze(request)
        hydrated = await service.hydrate_snapshot_details(request, snapshot, product_ids=[snapshot.items[0].pid])

        self.assertIsNot(hydrated, snapshot)
        self.assertGreater(len(snapshot.items[0].svl_records), 0)
        self.assertGreater(len(hydrated.items[0].svl_records), 0)
        self.assertGreater(len(hydrated.items[0].jnl_records), 0)
        self.assertGreater(len(hydrated.items[0].po_lines), 0)
        self.assertGreater(len(hydrated.items[0].bill_lines), 0)

    async def test_analyze_uses_conservative_valuation_account_fallback(self) -> None:
        rpc = _FakeDashboardRpc(
            include_category_accounts=False,
            account_rows=[
                {"id": 10, "code": "114199", "name": "Persediaan Transit", "company_ids": [1], "account_type": "asset_current"},
                {"id": 11, "code": "120001", "name": "Piutang Karyawan", "company_ids": [1], "account_type": "asset_current"},
            ],
        )
        service = SvlDashboardServiceAsync(rpc=rpc, logger=logging.getLogger("test.dashboard"))

        snapshot = await service.analyze(
            SvlDashboardRequest(
                database="hwgroup_erp",
                company_id=1,
                date_from="2026-01-01",
                date_to="2026-01-31",
                inventory_coa_codes=["114199"],
            )
        )

        self.assertEqual(snapshot.valuation_account_ids, [10])

    async def test_analyze_builds_company_summary_from_manual_inventory_coa_codes(self) -> None:
        rpc = _FakeDashboardRpc()
        service = SvlDashboardServiceAsync(rpc=rpc, logger=logging.getLogger("test.dashboard"))

        snapshot = await service.analyze(
            SvlDashboardRequest(
                database="hwgroup_erp",
                company_id=1,
                date_from="2026-01-01",
                date_to="2026-01-31",
                inventory_coa_codes=["114001"],
            )
        )

        self.assertEqual(snapshot.company_summary.total_svl_value, 200.0)
        self.assertEqual(snapshot.company_summary.total_svl_qty, 5.0)
        self.assertEqual(snapshot.company_summary.inventory_bs_total, 180.0)
        self.assertEqual(snapshot.company_summary.difference, 20.0)
        self.assertEqual(snapshot.company_summary.problematic_items_total_value, 20.0)
        self.assertEqual(snapshot.company_summary.unmapped_difference, 0.0)
        self.assertEqual(snapshot.company_summary.coa_rows[0].code, "114001")

    async def test_analyze_reports_missing_manual_inventory_coa_codes(self) -> None:
        rpc = _FakeDashboardRpc()
        service = SvlDashboardServiceAsync(rpc=rpc, logger=logging.getLogger("test.dashboard"))

        snapshot = await service.analyze(
            SvlDashboardRequest(
                database="hwgroup_erp",
                company_id=1,
                date_from="2026-01-01",
                date_to="2026-01-31",
                inventory_coa_codes=["999999"],
            )
        )

        self.assertEqual(snapshot.company_summary.coa_rows, [])
        self.assertEqual(snapshot.company_summary.missing_codes, ["999999"])
        self.assertTrue(any("999999" in warning for warning in snapshot.warnings))

    async def test_analyze_uses_account_move_date_when_svl_has_only_create_date(self) -> None:
        rpc = _FakeDashboardRpc(include_issues=False, svl_native_date_supported=False)
        rpc.product_rows.append(
            {
                "id": 104,
                "name": "Produk D",
                "default_code": "SKU-D",
                "categ_id": [1, "Raw"],
                "standard_price": 70.0,
                "cost_method": "standard",
            }
        )
        rpc.stock_move_rows[9004] = {"id": 9004, "reference": "WCGT/INT/00036"}
        rpc.account_move_rows[7007] = {"id": 7007, "date": "2026-03-16"}
        rpc.svl_rows.append(
            {
                "id": 1004,
                "company_id": 1,
                "product_id": [104, "Produk D"],
                "quantity": 1.0,
                "unit_cost": 70.0,
                "value": 70.0,
                "description": "Inventory Valuation",
                "create_date": "2026-01-06 10:00:00",
                "account_move_id": [7007, "STJ/2026/03/0367"],
                "account_move_id.date": "2026-03-16",
                "stock_move_id": [9004, "MOVE/9004"],
            }
        )
        rpc.journal_rows.append(
            {
                "id": 2006,
                "company_id": 1,
                "account_id": 10,
                "product_id": [104, "Produk D"],
                "move_id": [7007, "STJ/2026/03/0367"],
                "move_id.state": "posted",
                "date": "2026-03-16",
                "debit": 70.0,
                "credit": 0.0,
                "ref": "WCGT/INT/00036 - Produk D",
                "name": "WCGT/INT/00036 - Produk D",
                "parent_state": "posted",
                "display_type": False,
            }
        )
        service = SvlDashboardServiceAsync(rpc=rpc, logger=logging.getLogger("test.dashboard"))

        before_snapshot = await service.analyze(
            SvlDashboardRequest(
                database="hwgroup_erp",
                company_id=1,
                date_from="",
                date_to="2026-03-15",
                inventory_coa_codes=["114001"],
            )
        )
        self.assertFalse(any(item.pid == 104 for item in before_snapshot.items))

        capabilities = await service._detect_capabilities()
        detail = await service._fetch_svl_detail(
            product_ids=[104],
            company_id=1,
            context={"company_id": 1, "force_company": 1, "allowed_company_ids": [1]},
            date_from="",
            date_to="2026-03-16",
            svl_date_field=capabilities["svl_date_field"],
            link_supported=capabilities["svl_link_supported"],
            move_link_supported=capabilities["svl_move_link_supported"],
            warnings=[],
            move_fields=capabilities["stock_move_fields"],
        )
        self.assertEqual(detail[104][0].date, "2026-03-16")
        self.assertEqual(detail[104][0].reference, "WCGT/INT/00036")

    async def test_analyze_marks_linked_empty_move_without_orphan_counts(self) -> None:
        rpc = _FakeDashboardRpc(include_issues=False)
        rpc.product_rows.append(
            {
                "id": 104,
                "name": "Produk D",
                "default_code": "SKU-D",
                "categ_id": [1, "Raw"],
                "standard_price": 75.0,
                "cost_method": "standard",
            }
        )
        rpc.account_move_rows[5435] = {
            "id": 5435,
            "name": "STJ/2025/10/0435",
            "state": "posted",
            "journal_id": [81, "Stock Journal"],
            "date": "2026-01-15",
        }
        rpc.svl_rows.append(
            {
                "id": 1104,
                "company_id": 1,
                "product_id": [104, "Produk D"],
                "quantity": 0.0,
                "unit_cost": 0.0,
                "value": 75.0,
                "description": "Product value manually modified",
                "date": "2026-01-15",
                "create_date": "2026-01-15 10:00:00",
                "account_move_id": [5435, "STJ/2025/10/0435"],
                "account_move_id.date": "2026-01-15",
                "stock_move_id": False,
            }
        )
        service = SvlDashboardServiceAsync(rpc=rpc, logger=logging.getLogger("test.dashboard"))

        snapshot = await service.analyze(
            SvlDashboardRequest(
                database="hwgroup_erp",
                company_id=1,
                date_from="2026-01-01",
                date_to="2026-01-31",
                inventory_coa_codes=["114001"],
            )
        )

        item = next(entry for entry in snapshot.items if entry.pid == 104)
        self.assertEqual(item.difference, 75.0)
        self.assertEqual(item.svl_orphan_count, 0)
        self.assertEqual(item.jnl_orphan_count, 0)
        self.assertEqual(item.linked_empty_journal_count, 1)
        self.assertTrue(any("STJ/2025/10/0435" in warning for warning in item.warnings))
        self.assertEqual(len(item.merged_records), 1)
        self.assertEqual(item.merged_records[0].row_type, "svl_linked_empty_move")
        self.assertEqual(item.merged_records[0].move_name, "STJ/2025/10/0435")
        self.assertEqual(item.merged_records[0].account_code, "")
        self.assertTrue(item.merged_records[0].repair_candidate)

    async def test_analyze_populates_repair_account_candidates_from_product_category(self) -> None:
        rpc = _FakeDashboardRpc()
        service = SvlDashboardServiceAsync(rpc=rpc, logger=logging.getLogger("test.dashboard"))

        snapshot = await service.analyze(
            SvlDashboardRequest(
                database="hwgroup_erp",
                company_id=1,
                date_from="2026-01-01",
                date_to="2026-01-31",
                inventory_coa_codes=["114001"],
            )
        )

        item = next(entry for entry in snapshot.items if entry.pid == 101)
        candidate_codes = [candidate.code for candidate in item.repair_account_candidates]
        self.assertEqual(candidate_codes[:3], ["114001", "510001", "210001"])
        self.assertEqual(item.repair_account_candidates[0].source, "Category Stock Valuation")
        self.assertEqual(item.repair_account_candidates[0].role, "valuation")
        self.assertEqual(item.repair_account_candidates[0].field_name, "property_stock_valuation_account_id")

    async def test_analyze_dedupes_category_account_candidates_by_code(self) -> None:
        rpc = _FakeDashboardRpc()
        rpc.category_rows[0]["property_account_expense_categ_id"] = [10, "Persediaan"]
        rpc.category_rows[0]["property_stock_account_input_categ_id"] = [10, "Persediaan"]
        service = SvlDashboardServiceAsync(rpc=rpc, logger=logging.getLogger("test.dashboard"))

        snapshot = await service.analyze(
            SvlDashboardRequest(
                database="hwgroup_erp",
                company_id=1,
                date_from="2026-01-01",
                date_to="2026-01-31",
                inventory_coa_codes=["114001"],
            )
        )

        item = next(entry for entry in snapshot.items if entry.pid == 101)
        valuation_candidate = next(candidate for candidate in item.repair_account_candidates if candidate.code == "114001")
        self.assertEqual(sum(1 for candidate in item.repair_account_candidates if candidate.code == "114001"), 1)
        self.assertIn("Category Stock Valuation", valuation_candidate.source)
        self.assertIn("Category Expense", valuation_candidate.source)
        self.assertIn("Category Input", valuation_candidate.source)

    async def test_analyze_marks_nonstandard_category_inventory_accounts_as_other(self) -> None:
        rpc = _FakeDashboardRpc()
        rpc.fields_map["product.category"]["x_inventory_buffer_account_id"] = {"type": "many2one"}
        rpc.category_rows[0]["x_inventory_buffer_account_id"] = [50, "Inventory Buffer"]
        rpc.account_rows.append(
            {"id": 50, "code": "119999", "name": "Inventory Buffer", "company_ids": [1], "account_type": "asset_current"}
        )
        service = SvlDashboardServiceAsync(rpc=rpc, logger=logging.getLogger("test.dashboard"))

        snapshot = await service.analyze(
            SvlDashboardRequest(
                database="hwgroup_erp",
                company_id=1,
                date_from="2026-01-01",
                date_to="2026-01-31",
                inventory_coa_codes=["114001"],
            )
        )

        item = next(entry for entry in snapshot.items if entry.pid == 101)
        valuation_candidate = next(candidate for candidate in item.repair_account_candidates if candidate.code == "114001")
        other_candidate = next(candidate for candidate in item.repair_account_candidates if candidate.code == "119999")
        self.assertEqual(valuation_candidate.role, "valuation")
        self.assertEqual(other_candidate.role, "other")
        self.assertEqual(other_candidate.source, "Category Other")

    async def test_analyze_leaves_valuation_missing_when_category_has_no_stock_valuation_account(self) -> None:
        rpc = _FakeDashboardRpc()
        rpc.category_rows[0]["property_stock_valuation_account_id"] = False
        rpc.fields_map["product.category"]["x_inventory_buffer_account_id"] = {"type": "many2one"}
        rpc.category_rows[0]["x_inventory_buffer_account_id"] = [50, "Inventory Buffer"]
        rpc.account_rows.append(
            {"id": 50, "code": "119999", "name": "Inventory Buffer", "company_ids": [1], "account_type": "asset_current"}
        )
        service = SvlDashboardServiceAsync(rpc=rpc, logger=logging.getLogger("test.dashboard"))

        snapshot = await service.analyze(
            SvlDashboardRequest(
                database="hwgroup_erp",
                company_id=1,
                date_from="2026-01-01",
                date_to="2026-01-31",
                inventory_coa_codes=["114001"],
            )
        )

        item = next(entry for entry in snapshot.items if entry.pid == 101)
        self.assertFalse(any(candidate.role == "valuation" for candidate in item.repair_account_candidates))
        self.assertTrue(any(candidate.role == "other" for candidate in item.repair_account_candidates))

    async def test_analyze_uses_reference_hint_only_as_non_authoritative_match(self) -> None:
        rpc = _FakeDashboardRpc(include_issues=False)
        rpc.product_rows.append(
            {
                "id": 105,
                "name": "Produk E",
                "default_code": "SKU-E",
                "categ_id": [1, "Raw"],
                "standard_price": 60.0,
                "cost_method": "standard",
            }
        )
        rpc.svl_rows.append(
            {
                "id": 1105,
                "company_id": 1,
                "product_id": [105, "Produk E"],
                "quantity": 1.0,
                "unit_cost": 60.0,
                "value": 60.0,
                "description": "WH/IN/009 - Produk E",
                "date": "2026-01-12",
                "create_date": "2026-01-12 10:00:00",
                "account_move_id": False,
                "stock_move_id": False,
            }
        )
        rpc.account_move_rows[6005] = {
            "id": 6005,
            "name": "STJ/2026/0005",
            "state": "posted",
            "journal_id": [81, "Stock Journal"],
            "date": "2026-01-12",
        }
        rpc.journal_rows.append(
            {
                "id": 2105,
                "company_id": 1,
                "account_id": 10,
                "product_id": [105, "Produk E"],
                "move_id": [6005, "STJ/2026/0005"],
                "move_id.state": "posted",
                "date": "2026-01-12",
                "debit": 60.0,
                "credit": 0.0,
                "ref": "WH/IN/009",
                "name": "WH/IN/009 SKU-E Produk E",
                "parent_state": "posted",
                "display_type": False,
            }
        )
        service = SvlDashboardServiceAsync(rpc=rpc, logger=logging.getLogger("test.dashboard"))

        snapshot = await service.analyze(
            SvlDashboardRequest(
                database="hwgroup_erp",
                company_id=1,
                date_from="2026-01-01",
                date_to="2026-01-31",
                inventory_coa_codes=["114001"],
            )
        )

        item = next(entry for entry in snapshot.items if entry.pid == 105)
        self.assertEqual(item.difference, 0.0)
        self.assertEqual(item.svl_orphan_count, 1)
        self.assertEqual(item.jnl_orphan_count, 1)
        self.assertEqual(len(item.merged_records), 1)
        self.assertEqual(item.merged_records[0].row_type, "svl_reference_hint")
        self.assertEqual(item.merged_records[0].match_basis, "reference/date/amount")
        self.assertFalse(item.merged_records[0].repair_candidate)

    def test_build_pcb_case1_link_rows_prefers_bill_line_source(self) -> None:
        service = SvlDashboardServiceAsync(rpc=_FakeDashboardRpc(), logger=logging.getLogger("test.dashboard"))
        bill_line = {
            "id": 8101,
            "move_id": [8001, "BILL/2026/0001"],
            "account_id": [301, "Hutang Suspend"],
            "product_id": [101, "Produk A"],
            "product_uom_id": [11, "PCS"],
            "purchase_line_id": [3001, "PO Line A"],
            "balance": 96.0,
            "price_unit": 47.0,
            "quantity": 2.0,
            "price_subtotal": 94.0,
            "currency_id": [13, "IDR"],
            "amount_currency": 94.0,
            "analytic_distribution": {"CC-01": 100.0},
        }

        rows = service._build_pcb_case1_link_rows(
            cycle_status="problem",
            picking_id=7001,
            picking_name="LHPK/IN/0001",
            gr_date="2026-01-08",
            partner_name="Vendor Alpha",
            group_picking_ids=[7001],
            cycle_stj_move_ids=[8801],
            product_ids_in_picking=[101],
            account_rows=_build_case1_account_rows(),
            bill_move_ids=[8001],
            payment_move_ids=[8501],
            bank_move_ids=[8601],
            all_cycle_move_ids=[],
            lines_by_move={8001: [bill_line]},
            account_info_map={301: {"code": "2103006"}, 302: {"code": "1108099"}},
            move_info_map={
                8001: {"name": "BILL/2026/0001", "partner_id": [77, "Vendor Alpha"]},
                8801: {"name": "STJ/2026/0451"},
            },
            product_info_map={101: {"default_code": "SKU-A", "name": "Produk A", "categ_name": "Raw"}},
            bill_rows_by_id={8001: {"id": 8001, "invoice_date": "2026-01-09", "invoice_origin": "PO/2026/0001"}},
            bill_line_rows_by_product={101: [bill_line]},
            purchase_line_rows_by_id={3001: {"id": 3001, "product_id": [101, "Produk A"], "order_id": [7002, "PO/2026/0001"]}},
            purchase_line_product_map={3001: 101},
            purchase_line_po_name_map={3001: "PO/2026/0001"},
            stock_move_rows_by_id={
                8401: {
                    "id": 8401,
                    "product_id": [101, "Produk A"],
                    "picking_id": [7001, "LHPK/IN/0001"],
                    "purchase_line_id": [3001, "PO Line A"],
                    "account_move_ids": [8801],
                    "price_unit": 40.0,
                    "product_qty": 2.0,
                }
            },
            payment_move_ids_by_product={101: [8501]},
            bank_move_ids_by_product={101: [8601]},
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].source_kind, "bill_line")
        self.assertEqual(rows[0].bill_line_id, 8101)
        self.assertEqual(rows[0].product_uom_id, 11)
        self.assertEqual(rows[0].payment_move_ids, [8501])
        self.assertEqual(rows[0].bank_move_ids, [8601])
        self.assertEqual(rows[0].stj_move_ids, [8801])
        self.assertEqual(rows[0].stj_refs, ["STJ/2026/0451"])
        self.assertEqual(rows[0].stj_link_basis, "stock_move.account_move_ids")
        self.assertEqual(rows[0].stj_candidate_count, 1)
        self.assertEqual(rows[0].amount_currency_basis, 96.0)

    def test_build_pcb_case1_link_rows_falls_back_to_price_subtotal_for_amount_currency_basis(self) -> None:
        service = SvlDashboardServiceAsync(rpc=_FakeDashboardRpc(), logger=logging.getLogger("test.dashboard"))
        bill_line = {
            "id": 8101,
            "move_id": [8001, "BILL/2026/0001"],
            "account_id": [301, "Hutang Suspend"],
            "product_id": [101, "Produk A"],
            "product_uom_id": [11, "PCS"],
            "purchase_line_id": [3001, "PO Line A"],
            "price_unit": 47.0,
            "quantity": 2.0,
            "price_subtotal": 94.0,
            "currency_id": [13, "IDR"],
            "amount_currency": 94.0,
        }

        rows = service._build_pcb_case1_link_rows(
            cycle_status="problem",
            picking_id=7001,
            picking_name="LHPK/IN/0001",
            gr_date="2026-01-08",
            partner_name="Vendor Alpha",
            group_picking_ids=[7001],
            cycle_stj_move_ids=[8801],
            product_ids_in_picking=[101],
            account_rows=_build_case1_account_rows(),
            bill_move_ids=[8001],
            payment_move_ids=[],
            bank_move_ids=[],
            all_cycle_move_ids=[],
            lines_by_move={8001: [bill_line]},
            account_info_map={301: {"code": "2103006"}, 302: {"code": "1108099"}},
            move_info_map={
                8001: {"name": "BILL/2026/0001", "partner_id": [77, "Vendor Alpha"]},
                8801: {"name": "STJ/2026/0451"},
            },
            product_info_map={101: {"default_code": "SKU-A", "name": "Produk A", "categ_name": "Raw"}},
            bill_rows_by_id={8001: {"id": 8001, "invoice_date": "2026-01-09", "invoice_origin": "PO/2026/0001"}},
            bill_line_rows_by_product={101: [bill_line]},
            purchase_line_rows_by_id={3001: {"id": 3001, "product_id": [101, "Produk A"], "order_id": [7002, "PO/2026/0001"]}},
            purchase_line_product_map={3001: 101},
            purchase_line_po_name_map={3001: "PO/2026/0001"},
            stock_move_rows_by_id={
                8401: {
                    "id": 8401,
                    "product_id": [101, "Produk A"],
                    "picking_id": [7001, "LHPK/IN/0001"],
                    "purchase_line_id": [3001, "PO Line A"],
                    "account_move_ids": [8801],
                    "price_unit": 40.0,
                    "product_qty": 2.0,
                }
            },
            payment_move_ids_by_product={},
            bank_move_ids_by_product={},
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].amount_currency_basis, 94.0)

    def test_build_pcb_case1_link_rows_falls_back_to_purchase_line_then_stock_move(self) -> None:
        service = SvlDashboardServiceAsync(rpc=_FakeDashboardRpc(), logger=logging.getLogger("test.dashboard"))

        rows = service._build_pcb_case1_link_rows(
            cycle_status="problem",
            picking_id=7001,
            picking_name="LHPK/IN/0001",
            gr_date="2026-01-08",
            partner_name="Vendor Alpha",
            group_picking_ids=[7001],
            cycle_stj_move_ids=[],
            product_ids_in_picking=[101, 102],
            account_rows=_build_case1_account_rows(),
            bill_move_ids=[],
            payment_move_ids=[],
            bank_move_ids=[],
            all_cycle_move_ids=[],
            lines_by_move={},
            account_info_map={301: {"code": "2103006"}, 302: {"code": "1108099"}},
            move_info_map={},
            product_info_map={
                101: {"default_code": "SKU-A", "name": "Produk A", "categ_name": "Raw"},
                102: {"default_code": "SKU-B", "name": "Produk B", "categ_name": "Raw"},
            },
            bill_rows_by_id={},
            bill_line_rows_by_product={},
            purchase_line_rows_by_id={3001: {"id": 3001, "product_id": [101, "Produk A"], "order_id": [7002, "PO/2026/0001"]}},
            purchase_line_product_map={3001: 101},
            purchase_line_po_name_map={3001: "PO/2026/0001"},
            stock_move_rows_by_id={
                8401: {
                    "id": 8401,
                    "product_id": [101, "Produk A"],
                    "picking_id": [7001, "LHPK/IN/0001"],
                    "purchase_line_id": [3001, "PO Line A"],
                    "price_unit": 40.0,
                    "product_qty": 2.0,
                },
                8402: {
                    "id": 8402,
                    "product_id": [102, "Produk B"],
                    "picking_id": [7001, "LHPK/IN/0001"],
                    "purchase_line_id": False,
                    "price_unit": 20.0,
                    "product_qty": 3.0,
                },
            },
            payment_move_ids_by_product={},
            bank_move_ids_by_product={},
        )

        self.assertEqual([row.source_kind for row in rows], ["purchase_line", "stock_move"])
        self.assertEqual(sum(row.allocated_amount for row in rows), 300.0)
        self.assertEqual(sorted(row.allocated_amount for row in rows), [120.0, 180.0])

    def test_build_pcb_case1_link_rows_allocates_by_price_gap_and_keeps_total_exact(self) -> None:
        service = SvlDashboardServiceAsync(rpc=_FakeDashboardRpc(), logger=logging.getLogger("test.dashboard"))
        bill_line_a = {
            "id": 8101,
            "move_id": [8001, "BILL/2026/0001"],
            "account_id": [301, "Hutang Suspend"],
            "product_id": [101, "Produk A"],
            "purchase_line_id": [3001, "PO Line A"],
            "price_unit": 110.0,
            "quantity": 2.0,
            "price_subtotal": 220.0,
        }
        bill_line_b = {
            "id": 8102,
            "move_id": [8002, "BILL/2026/0002"],
            "account_id": [301, "Hutang Suspend"],
            "product_id": [102, "Produk B"],
            "purchase_line_id": [3002, "PO Line B"],
            "price_unit": 100.0,
            "quantity": 1.0,
            "price_subtotal": 100.0,
        }

        rows = service._build_pcb_case1_link_rows(
            cycle_status="problem",
            picking_id=7001,
            picking_name="LHPK/IN/0001",
            gr_date="2026-01-08",
            partner_name="Vendor Alpha",
            group_picking_ids=[7001],
            cycle_stj_move_ids=[],
            product_ids_in_picking=[101, 102],
            account_rows=_build_case1_account_rows(),
            bill_move_ids=[8001, 8002],
            payment_move_ids=[],
            bank_move_ids=[],
            all_cycle_move_ids=[],
            lines_by_move={8001: [bill_line_a], 8002: [bill_line_b]},
            account_info_map={301: {"code": "2103006"}, 302: {"code": "1108099"}},
            move_info_map={8001: {"name": "BILL/2026/0001"}, 8002: {"name": "BILL/2026/0002"}},
            product_info_map={
                101: {"default_code": "SKU-A", "name": "Produk A", "categ_name": "Raw"},
                102: {"default_code": "SKU-B", "name": "Produk B", "categ_name": "Raw"},
            },
            bill_rows_by_id={8001: {"id": 8001}, 8002: {"id": 8002}},
            bill_line_rows_by_product={101: [bill_line_a], 102: [bill_line_b]},
            purchase_line_rows_by_id={
                3001: {"id": 3001, "product_id": [101, "Produk A"], "order_id": [7002, "PO/2026/0001"]},
                3002: {"id": 3002, "product_id": [102, "Produk B"], "order_id": [7003, "PO/2026/0002"]},
            },
            purchase_line_product_map={3001: 101, 3002: 102},
            purchase_line_po_name_map={3001: "PO/2026/0001", 3002: "PO/2026/0002"},
            stock_move_rows_by_id={
                8401: {
                    "id": 8401,
                    "product_id": [101, "Produk A"],
                    "picking_id": [7001, "LHPK/IN/0001"],
                    "purchase_line_id": [3001, "PO Line A"],
                    "price_unit": 10.0,
                    "product_qty": 2.0,
                },
                8402: {
                    "id": 8402,
                    "product_id": [102, "Produk B"],
                    "picking_id": [7001, "LHPK/IN/0001"],
                    "purchase_line_id": [3002, "PO Line B"],
                    "price_unit": 0.0,
                    "product_qty": 1.0,
                },
            },
            payment_move_ids_by_product={},
            bank_move_ids_by_product={},
        )

        self.assertEqual(len(rows), 2)
        self.assertEqual(sum(row.allocated_amount for row in rows), 300.0)
        allocations = {row.product_id: row.allocated_amount for row in rows}
        self.assertEqual(allocations[101], 206.25)
        self.assertEqual(allocations[102], 93.75)

    def test_build_pcb_case1_link_rows_uses_item_residual_total_for_two_source_same_item(self) -> None:
        service = SvlDashboardServiceAsync(rpc=_FakeDashboardRpc(), logger=logging.getLogger("test.dashboard"))
        bill_line_a = {
            "id": 8101,
            "move_id": [8001, "BILL/2026/0001"],
            "account_id": [301, "Hutang Suspend"],
            "product_id": [101, "Produk A"],
            "purchase_line_id": [3001, "PO Line A"],
            "balance": 250.0,
            "price_unit": 50.0,
            "quantity": 1.0,
            "price_subtotal": 250.0,
        }
        bill_line_b = {
            "id": 8102,
            "move_id": [8002, "BILL/2026/0002"],
            "account_id": [301, "Hutang Suspend"],
            "product_id": [101, "Produk A"],
            "purchase_line_id": [3002, "PO Line B"],
            "balance": 250.0,
            "price_unit": 55.0,
            "quantity": 1.0,
            "price_subtotal": 250.0,
        }

        rows = service._build_pcb_case1_link_rows(
            cycle_status="problem",
            picking_id=7001,
            picking_name="LHPK/IN/0001",
            gr_date="2026-01-08",
            partner_name="Vendor Alpha",
            group_picking_ids=[7001],
            cycle_stj_move_ids=[],
            product_ids_in_picking=[101],
            account_rows=_build_case1_account_rows(),
            item_rows=_build_case1_item_rows(entries=[(101, -175.0, 175.0)]),
            bill_move_ids=[8001, 8002],
            payment_move_ids=[],
            bank_move_ids=[],
            all_cycle_move_ids=[],
            lines_by_move={8001: [bill_line_a], 8002: [bill_line_b]},
            account_info_map={301: {"code": "2103006"}, 302: {"code": "1108099"}},
            move_info_map={8001: {"name": "BILL/2026/0001"}, 8002: {"name": "BILL/2026/0002"}},
            product_info_map={101: {"default_code": "SKU-A", "name": "Produk A", "categ_name": "Raw"}},
            bill_rows_by_id={8001: {"id": 8001}, 8002: {"id": 8002}},
            bill_line_rows_by_product={101: [bill_line_a, bill_line_b]},
            purchase_line_rows_by_id={
                3001: {"id": 3001, "product_id": [101, "Produk A"], "order_id": [7002, "PO/2026/0001"]},
                3002: {"id": 3002, "product_id": [101, "Produk A"], "order_id": [7003, "PO/2026/0002"]},
            },
            purchase_line_product_map={3001: 101, 3002: 101},
            purchase_line_po_name_map={3001: "PO/2026/0001", 3002: "PO/2026/0002"},
            stock_move_rows_by_id={
                8401: {
                    "id": 8401,
                    "product_id": [101, "Produk A"],
                    "picking_id": [7001, "LHPK/IN/0001"],
                    "purchase_line_id": [3001, "PO Line A"],
                    "price_unit": 10.0,
                    "product_qty": 1.0,
                },
                8402: {
                    "id": 8402,
                    "product_id": [101, "Produk A"],
                    "picking_id": [7001, "LHPK/IN/0001"],
                    "purchase_line_id": [3002, "PO Line B"],
                    "price_unit": 15.0,
                    "product_qty": 1.0,
                },
            },
            payment_move_ids_by_product={},
            bank_move_ids_by_product={},
        )

        self.assertEqual(len(rows), 2)
        self.assertEqual(sum(row.allocated_amount for row in rows), 175.0)
        self.assertEqual(sorted(row.allocated_amount for row in rows), [87.5, 87.5])

    def test_build_pcb_case1_link_rows_uses_item_direction_instead_of_cycle_direction(self) -> None:
        service = SvlDashboardServiceAsync(rpc=_FakeDashboardRpc(), logger=logging.getLogger("test.dashboard"))
        bill_line = {
            "id": 8101,
            "move_id": [8001, "BILL/2026/0001"],
            "account_id": [301, "Hutang Suspend"],
            "product_id": [101, "Produk A"],
            "purchase_line_id": [3001, "PO Line A"],
            "balance": 180.0,
            "price_unit": 90.0,
            "quantity": 1.0,
            "price_subtotal": 180.0,
        }

        rows = service._build_pcb_case1_link_rows(
            cycle_status="problem",
            picking_id=7001,
            picking_name="LHPK/IN/0001",
            gr_date="2026-01-08",
            partner_name="Vendor Alpha",
            group_picking_ids=[7001],
            cycle_stj_move_ids=[],
            product_ids_in_picking=[101],
            account_rows=_build_case1_account_rows(),
            item_rows=_build_case1_item_rows(entries=[(101, 125.0, -125.0)]),
            bill_move_ids=[8001],
            payment_move_ids=[],
            bank_move_ids=[],
            all_cycle_move_ids=[],
            lines_by_move={8001: [bill_line]},
            account_info_map={301: {"code": "2103006"}, 302: {"code": "1108099"}},
            move_info_map={8001: {"name": "BILL/2026/0001"}},
            product_info_map={101: {"default_code": "SKU-A", "name": "Produk A", "categ_name": "Raw"}},
            bill_rows_by_id={8001: {"id": 8001}},
            bill_line_rows_by_product={101: [bill_line]},
            purchase_line_rows_by_id={3001: {"id": 3001, "product_id": [101, "Produk A"], "order_id": [7002, "PO/2026/0001"]}},
            purchase_line_product_map={3001: 101},
            purchase_line_po_name_map={3001: "PO/2026/0001"},
            stock_move_rows_by_id={
                8401: {
                    "id": 8401,
                    "product_id": [101, "Produk A"],
                    "picking_id": [7001, "LHPK/IN/0001"],
                    "purchase_line_id": [3001, "PO Line A"],
                    "price_unit": 40.0,
                    "product_qty": 1.0,
                }
            },
            payment_move_ids_by_product={},
            bank_move_ids_by_product={},
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].allocated_amount, 125.0)
        self.assertEqual(rows[0].debit_account_code, "1108099")
        self.assertEqual(rows[0].credit_account_code, "2103006")

    def test_build_pcb_case1_link_rows_uses_suspend_bill_lines_only_for_case1_source(self) -> None:
        service = SvlDashboardServiceAsync(rpc=_FakeDashboardRpc(), logger=logging.getLogger("test.dashboard"))
        expense_line = {
            "id": 8101,
            "move_id": [8001, "BILL/2026/0001"],
            "account_id": [303, "Selisih HPP / COGS Variance"],
            "product_id": [101, "Produk A"],
            "purchase_line_id": [3001, "PO Line A"],
            "price_unit": 120.0,
            "quantity": 1.0,
            "price_subtotal": 120.0,
        }
        suspend_line = {
            "id": 8102,
            "move_id": [8001, "BILL/2026/0001"],
            "account_id": [301, "Hutang Suspend"],
            "product_id": [101, "Produk A"],
            "purchase_line_id": [3001, "PO Line A"],
            "price_unit": 90.0,
            "quantity": 1.0,
            "price_subtotal": 90.0,
        }

        rows = service._build_pcb_case1_link_rows(
            cycle_status="problem",
            picking_id=7001,
            picking_name="LHPK/IN/0001",
            gr_date="2026-01-08",
            partner_name="Vendor Alpha",
            group_picking_ids=[7001],
            cycle_stj_move_ids=[],
            product_ids_in_picking=[101],
            account_rows=_build_case1_account_rows(),
            bill_move_ids=[8001],
            payment_move_ids=[],
            bank_move_ids=[],
            all_cycle_move_ids=[],
            lines_by_move={8001: [expense_line, suspend_line]},
            account_info_map={
                301: {"code": "2103006"},
                302: {"code": "1108099"},
                303: {"code": "5101010"},
            },
            move_info_map={8001: {"name": "BILL/2026/0001"}},
            product_info_map={101: {"default_code": "SKU-A", "name": "Produk A", "categ_name": "Raw"}},
            bill_rows_by_id={8001: {"id": 8001}},
            bill_line_rows_by_product={101: [expense_line, suspend_line]},
            purchase_line_rows_by_id={3001: {"id": 3001, "product_id": [101, "Produk A"], "order_id": [7002, "PO/2026/0001"]}},
            purchase_line_product_map={3001: 101},
            purchase_line_po_name_map={3001: "PO/2026/0001"},
            stock_move_rows_by_id={
                8401: {
                    "id": 8401,
                    "product_id": [101, "Produk A"],
                    "picking_id": [7001, "LHPK/IN/0001"],
                    "purchase_line_id": [3001, "PO Line A"],
                    "price_unit": 0.0,
                    "product_qty": 1.0,
                }
            },
            payment_move_ids_by_product={},
            bank_move_ids_by_product={},
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].source_kind, "bill_line")
        self.assertEqual(rows[0].bill_line_id, 8102)
        self.assertEqual(rows[0].source_id, 8102)

    def test_build_pcb_case1_link_rows_drops_zero_gap_suspend_source_when_price_gap_allocates_zero(self) -> None:
        service = SvlDashboardServiceAsync(rpc=_FakeDashboardRpc(), logger=logging.getLogger("test.dashboard"))
        zero_gap_line = {
            "id": 8101,
            "move_id": [8001, "BILL/2026/0001"],
            "account_id": [301, "Hutang Suspend"],
            "product_id": [101, "Produk A"],
            "purchase_line_id": [3001, "PO Line A"],
            "price_unit": 100.0,
            "quantity": 1.0,
            "price_subtotal": 100.0,
        }
        positive_gap_line = {
            "id": 8102,
            "move_id": [8002, "BILL/2026/0002"],
            "account_id": [301, "Hutang Suspend"],
            "product_id": [102, "Produk B"],
            "purchase_line_id": [3002, "PO Line B"],
            "price_unit": 100.0,
            "quantity": 1.0,
            "price_subtotal": 100.0,
        }

        rows = service._build_pcb_case1_link_rows(
            cycle_status="problem",
            picking_id=7001,
            picking_name="LHPK/IN/0001",
            gr_date="2026-01-08",
            partner_name="Vendor Alpha",
            group_picking_ids=[7001],
            cycle_stj_move_ids=[],
            product_ids_in_picking=[101, 102],
            account_rows=_build_case1_account_rows(),
            bill_move_ids=[8001, 8002],
            payment_move_ids=[],
            bank_move_ids=[],
            all_cycle_move_ids=[],
            lines_by_move={8001: [zero_gap_line], 8002: [positive_gap_line]},
            account_info_map={301: {"code": "2103006"}, 302: {"code": "1108099"}},
            move_info_map={8001: {"name": "BILL/2026/0001"}, 8002: {"name": "BILL/2026/0002"}},
            product_info_map={
                101: {"default_code": "SKU-A", "name": "Produk A", "categ_name": "Raw"},
                102: {"default_code": "SKU-B", "name": "Produk B", "categ_name": "Raw"},
            },
            bill_rows_by_id={8001: {"id": 8001}, 8002: {"id": 8002}},
            bill_line_rows_by_product={101: [zero_gap_line], 102: [positive_gap_line]},
            purchase_line_rows_by_id={
                3001: {"id": 3001, "product_id": [101, "Produk A"], "order_id": [7002, "PO/2026/0001"]},
                3002: {"id": 3002, "product_id": [102, "Produk B"], "order_id": [7003, "PO/2026/0002"]},
            },
            purchase_line_product_map={3001: 101, 3002: 102},
            purchase_line_po_name_map={3001: "PO/2026/0001", 3002: "PO/2026/0002"},
            stock_move_rows_by_id={
                8401: {
                    "id": 8401,
                    "product_id": [101, "Produk A"],
                    "picking_id": [7001, "LHPK/IN/0001"],
                    "purchase_line_id": [3001, "PO Line A"],
                    "price_unit": 100.0,
                    "product_qty": 1.0,
                },
                8402: {
                    "id": 8402,
                    "product_id": [102, "Produk B"],
                    "picking_id": [7001, "LHPK/IN/0001"],
                    "purchase_line_id": [3002, "PO Line B"],
                    "price_unit": 0.0,
                    "product_qty": 1.0,
                },
            },
            payment_move_ids_by_product={},
            bank_move_ids_by_product={},
        )

        self.assertEqual(len(rows), 2)
        allocations = {row.product_id: row.allocated_amount for row in rows}
        self.assertEqual(allocations[101], 150.0)
        self.assertEqual(allocations[102], 150.0)

    def test_build_pcb_case1_link_rows_skips_single_sided_item_residual(self) -> None:
        service = SvlDashboardServiceAsync(rpc=_FakeDashboardRpc(), logger=logging.getLogger("test.dashboard"))
        bill_line = {
            "id": 8101,
            "move_id": [8001, "BILL/2026/0001"],
            "account_id": [301, "Hutang Suspend"],
            "product_id": [101, "Produk A"],
            "purchase_line_id": [3001, "PO Line A"],
            "balance": 125.0,
            "price_unit": 125.0,
            "quantity": 1.0,
            "price_subtotal": 125.0,
        }

        rows = service._build_pcb_case1_link_rows(
            cycle_status="problem",
            picking_id=7001,
            picking_name="LHPK/IN/0001",
            gr_date="2026-01-08",
            partner_name="Vendor Alpha",
            group_picking_ids=[7001],
            cycle_stj_move_ids=[],
            product_ids_in_picking=[101],
            account_rows=[
                SvlDashboardCycleAccountRow(
                    account_id=302,
                    code="1108099",
                    name="Clearing",
                    account_type="asset_current",
                    account_group="asset",
                    debit=125.0,
                    credit=0.0,
                    net_balance=125.0,
                    status="problem",
                ),
            ],
            item_rows=_build_case1_item_rows(entries=[(101, 0.0, 125.0)]),
            bill_move_ids=[8001],
            payment_move_ids=[],
            bank_move_ids=[],
            all_cycle_move_ids=[],
            lines_by_move={8001: [bill_line]},
            account_info_map={301: {"code": "2103006"}, 302: {"code": "1108099"}},
            move_info_map={8001: {"name": "BILL/2026/0001"}},
            product_info_map={101: {"default_code": "SKU-A", "name": "Produk A", "categ_name": "Raw"}},
            bill_rows_by_id={8001: {"id": 8001}},
            bill_line_rows_by_product={101: [bill_line]},
            purchase_line_rows_by_id={3001: {"id": 3001, "product_id": [101, "Produk A"], "order_id": [7002, "PO/2026/0001"]}},
            purchase_line_product_map={3001: 101},
            purchase_line_po_name_map={3001: "PO/2026/0001"},
            stock_move_rows_by_id={},
            payment_move_ids_by_product={},
            bank_move_ids_by_product={},
        )

        self.assertEqual(rows, [])

    def test_build_pcb_case1_link_rows_falls_back_to_best_cycle_stj_match_when_direct_link_empty(self) -> None:
        service = SvlDashboardServiceAsync(rpc=_FakeDashboardRpc(), logger=logging.getLogger("test.dashboard"))
        bill_line = {
            "id": 8101,
            "move_id": [8001, "BILL/2026/0001"],
            "account_id": [301, "Hutang Suspend"],
            "product_id": [101, "Produk A"],
            "purchase_line_id": [3001, "PO Line A"],
            "price_unit": 47.0,
            "quantity": 2.0,
            "price_subtotal": 94.0,
        }

        rows = service._build_pcb_case1_link_rows(
            cycle_status="problem",
            picking_id=7001,
            picking_name="LHPK/IN/0001",
            gr_date="2026-01-08",
            partner_name="Vendor Alpha",
            group_picking_ids=[7001],
            cycle_stj_move_ids=[8801, 8802],
            product_ids_in_picking=[101],
            account_rows=_build_case1_account_rows(),
            bill_move_ids=[8001],
            payment_move_ids=[],
            bank_move_ids=[],
            all_cycle_move_ids=[8001, 8801, 8802],
            lines_by_move={
                8001: [bill_line],
                8801: [
                    {
                        "id": 9101,
                        "move_id": [8801, "STJ/2026/0451"],
                        "product_id": [101, "Produk A"],
                        "purchase_line_id": [3001, "PO Line A"],
                        "stock_move_id": [8401, "MOVE/8401"],
                    }
                ],
                8802: [
                    {
                        "id": 9102,
                        "move_id": [8802, "STJ/2026/0452"],
                        "product_id": [101, "Produk A"],
                        "purchase_line_id": [3009, "PO Line Z"],
                        "stock_move_id": [8499, "MOVE/8499"],
                    }
                ],
            },
            account_info_map={301: {"code": "2103006"}, 302: {"code": "1108099"}},
            move_info_map={
                8001: {"name": "BILL/2026/0001", "partner_id": [77, "Vendor Alpha"]},
                8801: {"name": "STJ/2026/0451"},
                8802: {"name": "STJ/2026/0452"},
            },
            product_info_map={101: {"default_code": "SKU-A", "name": "Produk A", "categ_name": "Raw"}},
            bill_rows_by_id={8001: {"id": 8001, "invoice_date": "2026-01-09", "invoice_origin": "PO/2026/0001"}},
            bill_line_rows_by_product={101: [bill_line]},
            purchase_line_rows_by_id={3001: {"id": 3001, "product_id": [101, "Produk A"], "order_id": [7002, "PO/2026/0001"]}},
            purchase_line_product_map={3001: 101},
            purchase_line_po_name_map={3001: "PO/2026/0001"},
            stock_move_rows_by_id={
                8401: {
                    "id": 8401,
                    "product_id": [101, "Produk A"],
                    "picking_id": [7001, "LHPK/IN/0001"],
                    "purchase_line_id": [3001, "PO Line A"],
                    "price_unit": 40.0,
                    "product_qty": 2.0,
                },
                8499: {
                    "id": 8499,
                    "product_id": [101, "Produk A"],
                    "picking_id": [7001, "LHPK/IN/0001"],
                    "purchase_line_id": [3009, "PO Line Z"],
                    "price_unit": 41.0,
                    "product_qty": 2.0,
                },
            },
            payment_move_ids_by_product={},
            bank_move_ids_by_product={},
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].stj_move_ids, [8801])
        self.assertEqual(rows[0].stj_refs, ["STJ/2026/0451"])
        self.assertEqual(rows[0].stj_link_basis, "cycle_match.stock_move")
        self.assertEqual(rows[0].stj_candidate_count, 1)

    def test_build_pcb_case1_link_rows_prefers_cycle_clearing_stj_when_direct_stock_move_stj_is_stale(self) -> None:
        service = SvlDashboardServiceAsync(rpc=_FakeDashboardRpc(), logger=logging.getLogger("test.dashboard"))
        bill_line = {
            "id": 8101,
            "move_id": [8001, "BILL/2026/0001"],
            "account_id": [301, "Hutang Suspend"],
            "product_id": [101, "Produk A"],
            "purchase_line_id": [3001, "PO Line A"],
            "price_unit": 47.0,
            "quantity": 2.0,
            "price_subtotal": 94.0,
        }

        rows = service._build_pcb_case1_link_rows(
            cycle_status="problem",
            picking_id=7001,
            picking_name="LHPK/IN/0001",
            gr_date="2026-01-08",
            partner_name="Vendor Alpha",
            group_picking_ids=[7001],
            cycle_stj_move_ids=[8801, 8802],
            product_ids_in_picking=[101],
            account_rows=_build_case1_account_rows(),
            bill_move_ids=[8001],
            payment_move_ids=[],
            bank_move_ids=[],
            all_cycle_move_ids=[8001, 8801, 8802],
            lines_by_move={
                8001: [bill_line],
                8801: [
                    {
                        "id": 9101,
                        "move_id": [8801, "STJ/2026/0451"],
                        "account_id": [401, "Persediaan Barang"],
                        "product_id": [101, "Produk A"],
                        "purchase_line_id": [3001, "PO Line A"],
                        "stock_move_id": [8401, "MOVE/8401"],
                    }
                ],
                8802: [
                    {
                        "id": 9102,
                        "move_id": [8802, "STJ/2026/0452"],
                        "account_id": [302, "Clearing"],
                        "product_id": [101, "Produk A"],
                        "purchase_line_id": [3001, "PO Line A"],
                        "stock_move_id": [8401, "MOVE/8401"],
                    }
                ],
            },
            account_info_map={
                301: {"code": "2103006"},
                302: {"code": "1108099"},
                401: {"code": "1105003"},
            },
            move_info_map={
                8001: {"name": "BILL/2026/0001", "partner_id": [77, "Vendor Alpha"]},
                8801: {"name": "STJ/2026/0451"},
                8802: {"name": "STJ/2026/0452"},
            },
            product_info_map={101: {"default_code": "SKU-A", "name": "Produk A", "categ_name": "Raw"}},
            bill_rows_by_id={8001: {"id": 8001, "invoice_date": "2026-01-09", "invoice_origin": "PO/2026/0001"}},
            bill_line_rows_by_product={101: [bill_line]},
            purchase_line_rows_by_id={3001: {"id": 3001, "product_id": [101, "Produk A"], "order_id": [7002, "PO/2026/0001"]}},
            purchase_line_product_map={3001: 101},
            purchase_line_po_name_map={3001: "PO/2026/0001"},
            stock_move_rows_by_id={
                8401: {
                    "id": 8401,
                    "product_id": [101, "Produk A"],
                    "picking_id": [7001, "LHPK/IN/0001"],
                    "purchase_line_id": [3001, "PO Line A"],
                    "account_move_ids": [8801],
                    "price_unit": 40.0,
                    "product_qty": 2.0,
                }
            },
            payment_move_ids_by_product={},
            bank_move_ids_by_product={},
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].stj_move_ids, [8802])
        self.assertEqual(rows[0].stj_refs, ["STJ/2026/0452"])
        self.assertEqual(rows[0].stj_link_basis, "cycle_match.stock_move")
        self.assertEqual(rows[0].stj_candidate_count, 1)

    def test_build_pcb_case1_link_rows_keeps_direct_stock_move_stj_when_it_is_best_clearing_match(self) -> None:
        service = SvlDashboardServiceAsync(rpc=_FakeDashboardRpc(), logger=logging.getLogger("test.dashboard"))
        bill_line = {
            "id": 8101,
            "move_id": [8001, "BILL/2026/0001"],
            "account_id": [301, "Hutang Suspend"],
            "product_id": [101, "Produk A"],
            "purchase_line_id": [3001, "PO Line A"],
            "price_unit": 47.0,
            "quantity": 2.0,
            "price_subtotal": 94.0,
        }

        rows = service._build_pcb_case1_link_rows(
            cycle_status="problem",
            picking_id=7001,
            picking_name="LHPK/IN/0001",
            gr_date="2026-01-08",
            partner_name="Vendor Alpha",
            group_picking_ids=[7001],
            cycle_stj_move_ids=[8801, 8802],
            product_ids_in_picking=[101],
            account_rows=_build_case1_account_rows(),
            bill_move_ids=[8001],
            payment_move_ids=[],
            bank_move_ids=[],
            all_cycle_move_ids=[8001, 8801, 8802],
            lines_by_move={
                8001: [bill_line],
                8801: [
                    {
                        "id": 9101,
                        "move_id": [8801, "STJ/2026/0451"],
                        "account_id": [302, "Clearing"],
                        "product_id": [101, "Produk A"],
                        "purchase_line_id": [3001, "PO Line A"],
                        "stock_move_id": [8401, "MOVE/8401"],
                    }
                ],
                8802: [
                    {
                        "id": 9102,
                        "move_id": [8802, "STJ/2026/0452"],
                        "account_id": [302, "Clearing"],
                        "product_id": [101, "Produk A"],
                        "purchase_line_id": [3999, "PO Line Z"],
                        "stock_move_id": [8499, "MOVE/8499"],
                    }
                ],
            },
            account_info_map={301: {"code": "2103006"}, 302: {"code": "1108099"}},
            move_info_map={
                8001: {"name": "BILL/2026/0001", "partner_id": [77, "Vendor Alpha"]},
                8801: {"name": "STJ/2026/0451"},
                8802: {"name": "STJ/2026/0452"},
            },
            product_info_map={101: {"default_code": "SKU-A", "name": "Produk A", "categ_name": "Raw"}},
            bill_rows_by_id={8001: {"id": 8001, "invoice_date": "2026-01-09", "invoice_origin": "PO/2026/0001"}},
            bill_line_rows_by_product={101: [bill_line]},
            purchase_line_rows_by_id={3001: {"id": 3001, "product_id": [101, "Produk A"], "order_id": [7002, "PO/2026/0001"]}},
            purchase_line_product_map={3001: 101},
            purchase_line_po_name_map={3001: "PO/2026/0001"},
            stock_move_rows_by_id={
                8401: {
                    "id": 8401,
                    "product_id": [101, "Produk A"],
                    "picking_id": [7001, "LHPK/IN/0001"],
                    "purchase_line_id": [3001, "PO Line A"],
                    "account_move_ids": [8801],
                    "price_unit": 40.0,
                    "product_qty": 2.0,
                },
                8499: {
                    "id": 8499,
                    "product_id": [101, "Produk A"],
                    "picking_id": [7001, "LHPK/IN/0001"],
                    "purchase_line_id": [3999, "PO Line Z"],
                    "price_unit": 41.0,
                    "product_qty": 2.0,
                },
            },
            payment_move_ids_by_product={},
            bank_move_ids_by_product={},
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].stj_move_ids, [8801])
        self.assertEqual(rows[0].stj_refs, ["STJ/2026/0451"])
        self.assertEqual(rows[0].stj_link_basis, "stock_move.account_move_ids")
        self.assertEqual(rows[0].stj_candidate_count, 1)

    def test_build_pcb_case1_link_rows_keeps_tied_cycle_stj_candidates_when_only_picking_match_exists(self) -> None:
        service = SvlDashboardServiceAsync(rpc=_FakeDashboardRpc(), logger=logging.getLogger("test.dashboard"))

        rows = service._build_pcb_case1_link_rows(
            cycle_status="problem",
            picking_id=7001,
            picking_name="LHPK/IN/0001",
            gr_date="2026-01-08",
            partner_name="Vendor Alpha",
            group_picking_ids=[7001],
            cycle_stj_move_ids=[8801, 8802],
            product_ids_in_picking=[101],
            account_rows=_build_case1_account_rows(),
            bill_move_ids=[],
            payment_move_ids=[],
            bank_move_ids=[],
            all_cycle_move_ids=[8801, 8802],
            lines_by_move={8801: [], 8802: []},
            account_info_map={301: {"code": "2103006"}, 302: {"code": "1108099"}},
            move_info_map={
                8801: {"name": "STJ/2026/0451"},
                8802: {"name": "STJ/2026/0452"},
            },
            product_info_map={101: {"default_code": "SKU-A", "name": "Produk A", "categ_name": "Raw"}},
            bill_rows_by_id={},
            bill_line_rows_by_product={},
            purchase_line_rows_by_id={},
            purchase_line_product_map={},
            purchase_line_po_name_map={},
            stock_move_rows_by_id={
                8401: {
                    "id": 8401,
                    "product_id": [101, "Produk A"],
                    "picking_id": [7001, "LHPK/IN/0001"],
                    "purchase_line_id": False,
                    "price_unit": 40.0,
                    "product_qty": 2.0,
                }
            },
            payment_move_ids_by_product={},
            bank_move_ids_by_product={},
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].stj_move_ids, [8801, 8802])
        self.assertEqual(rows[0].stj_refs, ["STJ/2026/0451", "STJ/2026/0452"])
        self.assertEqual(rows[0].stj_link_basis, "cycle_match.picking")
        self.assertEqual(rows[0].stj_candidate_count, 2)

    def test_apply_pcb_item_classifier_assigns_case5_case6_and_cycle_edges(self) -> None:
        cycle = SvlDashboardPurchaseCycle(
            picking_id=7001,
            picking_name="LHPK/IN/0001",
            gr_date="2026-03-18",
            partner_name="Vendor Alpha",
            cycle_status="problem",
            raw_lines=[
                {
                    "jenis": "BILL",
                    "akun_code": "2103006",
                    "akun_name": "Hutang Suspend",
                    "tipe_akun": "liability_current",
                    "kode_item": "SKU-CASE5",
                    "nama_item": "Produk Case 5",
                },
                {
                    "jenis": "BILL",
                    "akun_code": "510002",
                    "akun_name": "COGS - Case 6",
                    "tipe_akun": "expense_direct_cost",
                    "kode_item": "SKU-CASE6",
                    "nama_item": "Produk Case 6",
                },
            ],
            item_rows=[
                SvlDashboardCycleItemRow(
                    product_id=101,
                    product_name="Produk Case 5",
                    default_code="SKU-CASE5",
                    has_item_bill=True,
                    gr_quantity=2.0,
                    bill_quantity=2.0,
                    standard_price=15.0,
                    svl_zero_at_gr=True,
                    account_rows=[
                        SvlDashboardCycleAccountRow(
                            account_id=301,
                            code="2103006",
                            name="Hutang Suspend",
                            account_type="liability_current",
                            account_group="liability",
                            debit=0.0,
                            credit=30.0,
                            net_balance=-30.0,
                            status="problem",
                        )
                    ],
                ),
                SvlDashboardCycleItemRow(
                    product_id=102,
                    product_name="Produk Case 6",
                    default_code="SKU-CASE6",
                    has_item_bill=True,
                    gr_quantity=2.0,
                    bill_quantity=1.0,
                    standard_price=20.0,
                    svl_zero_at_gr=True,
                    account_rows=[
                        SvlDashboardCycleAccountRow(
                            account_id=302,
                            code="1108099",
                            name="Clearing",
                            account_type="asset_current",
                            account_group="asset",
                            debit=0.0,
                            credit=40.0,
                            net_balance=-40.0,
                            status="problem",
                        )
                    ],
                ),
            ],
            has_return_picking=True,
            has_refund_bill=False,
        )

        SvlDashboardServiceAsync._apply_pcb_item_classifier(
            cycle=cycle,
            product_info_map={
                101: {
                    "expense_account_code": "510001",
                    "expense_account_name": "COGS Case 5",
                    "standard_price": 15.0,
                },
                102: {
                    "expense_account_code": "510002",
                    "expense_account_name": "COGS Case 6",
                    "standard_price": 20.0,
                },
            },
        )

        item_case5, item_case6 = cycle.item_rows
        self.assertEqual(item_case5.primary_case, "case5")
        self.assertEqual(item_case5.repair_basis_amount, 0.0)  # No actual SVL evidence: do not use standard cost.
        self.assertFalse(item_case5.auto_repairable)
        self.assertEqual(item_case6.primary_case, "case6")
        self.assertEqual(item_case6.repair_basis_amount, 0.0)
        self.assertIn("edge_partial_bill", item_case6.secondary_flags)
        self.assertFalse(item_case6.auto_repairable)
        self.assertEqual(cycle.primary_case, "case5")
        self.assertEqual(cycle.case_counts, {"case5": 1, "case6": 1})
        self.assertIn("edge_partial_bill", cycle.edge_flags)
        self.assertIn("edge_return_no_credit_memo", cycle.edge_flags)
        self.assertEqual(cycle.mixed_case_summary, "case5:1, case6:1")

    def test_apply_pcb_item_classifier_requires_two_sided_residual_for_case1(self) -> None:
        cycle = SvlDashboardPurchaseCycle(
            picking_id=7003,
            picking_name="LHPK/IN/0003",
            gr_date="2026-03-20",
            partner_name="Vendor Alpha",
            cycle_status="problem",
            raw_lines=[
                {
                    "jenis": "STJ",
                    "akun_code": "1108099",
                    "akun_name": "Clearing",
                    "tipe_akun": "asset_current",
                    "kode_item": "SKU-CASE1",
                    "nama_item": "Produk Case 1",
                },
                {
                    "jenis": "BILL",
                    "akun_code": "2103006",
                    "akun_name": "Hutang Suspend",
                    "tipe_akun": "liability_current",
                    "kode_item": "SKU-CASE1",
                    "nama_item": "Produk Case 1",
                },
                {
                    "jenis": "STJ",
                    "akun_code": "1108099",
                    "akun_name": "Clearing",
                    "tipe_akun": "asset_current",
                    "kode_item": "SKU-SINGLE",
                    "nama_item": "Produk Single Residual",
                },
                {
                    "jenis": "BILL",
                    "akun_code": "2103006",
                    "akun_name": "Hutang Suspend",
                    "tipe_akun": "liability_current",
                    "kode_item": "SKU-SINGLE",
                    "nama_item": "Produk Single Residual",
                },
            ],
            item_rows=[
                SvlDashboardCycleItemRow(
                    product_id=111,
                    product_name="Produk Case 1",
                    default_code="SKU-CASE1",
                    has_item_bill=True,
                    has_item_stj=True,
                    gr_quantity=1.0,
                    bill_quantity=1.0,
                    svl_zero_at_gr=False,
                    account_rows=[
                        SvlDashboardCycleAccountRow(
                            account_id=301,
                            code="2103006",
                            name="Hutang Suspend",
                            account_type="liability_current",
                            account_group="liability",
                            debit=0.0,
                            credit=120.0,
                            net_balance=-120.0,
                            status="problem",
                        ),
                        SvlDashboardCycleAccountRow(
                            account_id=302,
                            code="1108099",
                            name="Clearing",
                            account_type="asset_current",
                            account_group="asset",
                            debit=120.0,
                            credit=0.0,
                            net_balance=120.0,
                            status="problem",
                        ),
                    ],
                ),
                SvlDashboardCycleItemRow(
                    product_id=112,
                    product_name="Produk Single Residual",
                    default_code="SKU-SINGLE",
                    has_item_bill=True,
                    has_item_stj=True,
                    gr_quantity=1.0,
                    bill_quantity=1.0,
                    svl_zero_at_gr=False,
                    account_rows=[
                        SvlDashboardCycleAccountRow(
                            account_id=302,
                            code="1108099",
                            name="Clearing",
                            account_type="asset_current",
                            account_group="asset",
                            debit=80.0,
                            credit=0.0,
                            net_balance=80.0,
                            status="problem",
                        ),
                    ],
                ),
            ],
        )

        SvlDashboardServiceAsync._apply_pcb_item_classifier(
            cycle=cycle,
            product_info_map={
                111: {"expense_account_code": "510001", "expense_account_name": "COGS Case 1"},
                112: {"expense_account_code": "510002", "expense_account_name": "COGS Single"},
            },
        )

        full_case_item, single_sided_item = cycle.item_rows
        self.assertEqual(full_case_item.primary_case, "case1")
        self.assertEqual(single_sided_item.primary_case, "case_lainnya")
        self.assertFalse(single_sided_item.auto_repairable)

    def test_case9_uom_scale_mismatch_uses_standard_price_and_downstream_clearing_holder(self) -> None:
        service = SvlDashboardServiceAsync(rpc=_FakeDashboardRpc(), logger=logging.getLogger("test.dashboard"))
        item_row = SvlDashboardCycleItemRow(
            product_id=901,
            product_name="MIE RAMEN",
            default_code="F-FZRP-0202",
            has_item_bill=True,
            has_item_stj=True,
            standard_price=40000.0,
            external_clearing_amount=37620000.0,
            external_clearing_refs=["PIKKP/INT/00054"],
            account_rows=[
                SvlDashboardCycleAccountRow(
                    account_id=5101003,
                    code="5101003",
                    name="HPP - Makanan",
                    account_type="expense_direct_cost",
                    account_group="expense",
                    debit=0.0,
                    credit=37620000.0,
                    net_balance=-37620000.0,
                    status="acceptable",
                )
            ],
        )
        cycle = SvlDashboardPurchaseCycle(
            picking_id=7001,
            picking_name="PIKKP/IN/01509",
            gr_date="2026-04-07",
            partner_name="Vendor",
            cycle_status="problem",
            bill_move_ids=[8201],
            picking_ids=[7001],
            item_rows=[item_row],
        )
        trace = {
            "stock_move_rows_by_id": {
                8401: {
                    "id": 8401,
                    "product_id": [901, "MIE RAMEN"],
                    "picking_id": [7001, "PIKKP/IN/01509"],
                    "purchase_line_id": [8301, "PO line"],
                    "product_qty": 1000.0,
                    "product_uom": [2515, "PACK @10 PORSI"],
                    "price_unit": 38000.0,
                }
            },
            "svl_rows_by_stock_move_id": {
                8401: [{"id": 9001, "stock_move_id": [8401, "Move"], "quantity": 1000.0, "value": 38000000.0}]
            },
            "bill_line_rows_by_product": {
                901: [
                    {
                        "id": 8101,
                        "move_id": [8201, "BILL/2026/02/0225"],
                        "product_id": [901, "MIE RAMEN"],
                        "purchase_line_id": [8301, "PO line"],
                        "quantity": 10.0,
                        "price_subtotal": 380000.0,
                        "product_uom_id": [15, "KG"],
                    }
                ]
            },
            "purchase_line_rows_by_id": {
                8301: {
                    "id": 8301,
                    "product_id": [901, "MIE RAMEN"],
                    "product_qty": 10.0,
                    "product_uom": [15, "KG"],
                    "price_unit": 38000.0,
                }
            },
            "uom_info_by_id": {
                15: {"id": 15, "name": "KG", "parent_path": "14/15/"},
                2515: {"id": 2515, "name": "PACK @10 PORSI", "parent_path": "2514/2515/"},
            },
            "return_picking_pairs": [],
        }

        service._populate_pcb_case8_case9_evidence_for_cycle(
            cycle=cycle,
            trace=trace,
            product_info_map={
                901: {
                    "standard_price": 40000.0,
                    "expense_account_code": "5101003",
                    "expense_account_name": "HPP - Makanan",
                    "valuation_account_code": "1105003",
                    "valuation_account_name": "Persediaan Makanan",
                }
            },
        )
        SvlDashboardServiceAsync._apply_pcb_item_classifier(
            cycle=cycle,
            product_info_map={
                901: {
                    "standard_price": 40000.0,
                    "expense_account_code": "5101003",
                    "expense_account_name": "HPP - Makanan",
                }
            },
        )

        self.assertEqual(item_row.primary_case, "case9")
        self.assertEqual(item_row.case9_evidence["scale_factor"], 100.0)
        self.assertEqual(item_row.case9_evidence["expected_value"], 400000.0)
        self.assertEqual(item_row.case9_evidence["value_gap"], 37600000.0)
        self.assertEqual(item_row.case9_evidence["correction_amount"], 37620000.0)
        self.assertEqual(item_row.case9_evidence["holder_basis"], "case2_downstream_clearing")

    def test_case9_closed_suspense_with_correction_stj_promotes_downstream_clearing_holder(self) -> None:
        service = SvlDashboardServiceAsync(rpc=_FakeDashboardRpc(), logger=logging.getLogger("test.dashboard"))
        item_row = SvlDashboardCycleItemRow(
            product_id=901,
            product_name="MIE RAMEN",
            default_code="F-FZRP-0202",
            has_item_bill=True,
            has_item_stj=True,
            standard_price=40000.0,
            correction_stj_move_ids=[8811],
            correction_stj_refs=["STJ/2026/03/67112"],
            account_rows=[
                SvlDashboardCycleAccountRow(
                    account_id=1105003,
                    code="1105003",
                    name="Persediaan Makanan",
                    account_type="asset_current",
                    account_group="asset",
                    debit=38000000.0,
                    credit=0.0,
                    net_balance=38000000.0,
                    status="acceptable",
                ),
                SvlDashboardCycleAccountRow(
                    account_id=5101003,
                    code="5101003",
                    name="HPP - Makanan",
                    account_type="expense_direct_cost",
                    account_group="expense",
                    debit=0.0,
                    credit=37620000.0,
                    net_balance=-37620000.0,
                    status="acceptable",
                ),
            ],
        )
        cycle = SvlDashboardPurchaseCycle(
            picking_id=7001,
            picking_name="PIKKP/IN/01509",
            gr_date="2026-04-07",
            partner_name="Vendor",
            cycle_status="problem",
            bill_move_ids=[8201],
            picking_ids=[7001],
            item_rows=[item_row],
        )
        trace = {
            "stock_move_rows_by_id": {
                8401: {
                    "id": 8401,
                    "product_id": [901, "MIE RAMEN"],
                    "picking_id": [7001, "PIKKP/IN/01509"],
                    "purchase_line_id": [8301, "PO line"],
                    "product_qty": 1000.0,
                    "product_uom": [2515, "PACK @10 PORSI"],
                    "price_unit": 38000.0,
                }
            },
            "svl_rows_by_stock_move_id": {
                8401: [{"id": 9001, "stock_move_id": [8401, "Move"], "quantity": 1000.0, "value": 38000000.0}]
            },
            "bill_line_rows_by_product": {
                901: [
                    {
                        "id": 8101,
                        "move_id": [8201, "BILL/2026/02/0225"],
                        "product_id": [901, "MIE RAMEN"],
                        "purchase_line_id": [8301, "PO line"],
                        "quantity": 10.0,
                        "price_subtotal": 380000.0,
                        "product_uom_id": [15, "KG"],
                    }
                ]
            },
            "purchase_line_rows_by_id": {
                8301: {
                    "id": 8301,
                    "product_id": [901, "MIE RAMEN"],
                    "product_qty": 10.0,
                    "product_uom": [15, "KG"],
                    "price_unit": 38000.0,
                }
            },
            "uom_info_by_id": {
                15: {"id": 15, "name": "KG", "parent_path": "14/15/"},
                2515: {"id": 2515, "name": "PACK @10 PORSI", "parent_path": "2514/2515/"},
            },
            "return_picking_pairs": [],
        }

        service._populate_pcb_case8_case9_evidence_for_cycle(
            cycle=cycle,
            trace=trace,
            product_info_map={
                901: {
                    "standard_price": 40000.0,
                    "expense_account_code": "5101003",
                    "expense_account_name": "HPP - Makanan",
                    "valuation_account_code": "1105003",
                    "valuation_account_name": "Persediaan Makanan",
                }
            },
        )

        self.assertEqual(item_row.case9_evidence["holder_basis"], "case2_downstream_clearing")
        self.assertEqual(item_row.case9_evidence["correction_amount"], 37620000.0)
        self.assertEqual(item_row.case9_evidence["downstream_refs"], ["STJ/2026/03/67112"])
        self.assertEqual(item_row.case9_evidence["inventory_balance"], 38000000.0)
        self.assertEqual(item_row.case9_evidence["hpp_balance"], -37620000.0)
        self.assertEqual(item_row.case9_evidence["suspend_balance"], 0.0)

    def test_case9_ignores_entry_move_lines_when_building_bill_source_evidence(self) -> None:
        service = SvlDashboardServiceAsync(rpc=_FakeDashboardRpc(), logger=logging.getLogger("test.dashboard"))
        item_row = SvlDashboardCycleItemRow(
            product_id=901,
            product_name="MIE RAMEN",
            default_code="F-FZRP-0202",
            has_item_bill=True,
            has_item_stj=True,
            standard_price=40000.0,
            external_clearing_amount=37620000.0,
            external_clearing_refs=["PIKKP/INT/00054"],
            account_rows=[
                SvlDashboardCycleAccountRow(
                    account_id=5101003,
                    code="5101003",
                    name="HPP - Makanan",
                    account_type="expense_direct_cost",
                    account_group="expense",
                    debit=0.0,
                    credit=37620000.0,
                    net_balance=-37620000.0,
                    status="acceptable",
                )
            ],
        )
        cycle = SvlDashboardPurchaseCycle(
            picking_id=7001,
            picking_name="PIKKP/IN/01509",
            gr_date="2026-04-07",
            partner_name="Vendor",
            cycle_status="problem",
            bill_move_ids=[8201, 8811],
            picking_ids=[7001],
            item_rows=[item_row],
        )
        trace = {
            "stock_move_rows_by_id": {
                8401: {
                    "id": 8401,
                    "product_id": [901, "MIE RAMEN"],
                    "picking_id": [7001, "PIKKP/IN/01509"],
                    "purchase_line_id": [8301, "PO line"],
                    "product_qty": 1000.0,
                    "product_uom": [2515, "PACK @10 PORSI"],
                    "price_unit": 38000.0,
                }
            },
            "svl_rows_by_stock_move_id": {
                8401: [{"id": 9001, "stock_move_id": [8401, "Move"], "quantity": 1000.0, "value": 38000000.0}]
            },
            "bill_rows_by_id": {
                8201: {"id": 8201, "name": "BILL/2026/02/0225", "move_type": "in_invoice"},
                8811: {"id": 8811, "name": "STJ/2026/03/67112", "move_type": "entry"},
            },
            "bill_line_rows_by_product": {
                901: [
                    {
                        "id": 8101,
                        "move_id": [8201, "BILL/2026/02/0225"],
                        "product_id": [901, "MIE RAMEN"],
                        "purchase_line_id": [8301, "PO line"],
                        "quantity": 10.0,
                        "price_subtotal": 380000.0,
                        "product_uom_id": [15, "KG"],
                    },
                    {
                        "id": 9101,
                        "move_id": [8811, "STJ/2026/03/67112"],
                        "product_id": [901, "MIE RAMEN"],
                        "purchase_line_id": [8301, "PO line"],
                        "quantity": 1.0,
                        "price_subtotal": 37620000.0,
                        "product_uom_id": [15, "KG"],
                    },
                    {
                        "id": 9102,
                        "move_id": [8811, "STJ/2026/03/67112"],
                        "product_id": [901, "MIE RAMEN"],
                        "purchase_line_id": [8301, "PO line"],
                        "quantity": 1.0,
                        "price_subtotal": 0.0,
                        "balance": -37620000.0,
                        "product_uom_id": [15, "KG"],
                    },
                ]
            },
            "purchase_line_rows_by_id": {
                8301: {
                    "id": 8301,
                    "product_id": [901, "MIE RAMEN"],
                    "product_qty": 10.0,
                    "product_uom": [15, "KG"],
                    "price_unit": 38000.0,
                }
            },
            "uom_info_by_id": {
                15: {"id": 15, "name": "KG", "parent_path": "14/15/"},
                2515: {"id": 2515, "name": "PACK @10 PORSI", "parent_path": "2514/2515/"},
            },
            "return_picking_pairs": [],
        }

        service._populate_pcb_case8_case9_evidence_for_cycle(
            cycle=cycle,
            trace=trace,
            product_info_map={
                901: {
                    "standard_price": 40000.0,
                    "expense_account_code": "5101003",
                    "expense_account_name": "HPP - Makanan",
                    "valuation_account_code": "1105003",
                    "valuation_account_name": "Persediaan Makanan",
                }
            },
        )

        self.assertEqual(item_row.case9_evidence["bill_line_ids"], [8101])
        self.assertEqual(item_row.case9_evidence["source_qty"], 10.0)
        self.assertEqual(item_row.case9_evidence["scale_factor"], 100.0)
        self.assertEqual(item_row.case9_evidence["expected_value"], 400000.0)

    def test_case9_same_root_uom_is_not_classified(self) -> None:
        service = SvlDashboardServiceAsync(rpc=_FakeDashboardRpc(), logger=logging.getLogger("test.dashboard"))
        item_row = SvlDashboardCycleItemRow(
            product_id=902,
            product_name="Same Root",
            default_code="SAME-ROOT",
            has_item_bill=True,
            standard_price=10.0,
        )
        cycle = SvlDashboardPurchaseCycle(
            picking_id=7002,
            picking_name="PICK/IN/0002",
            gr_date="2026-04-07",
            partner_name="Vendor",
            cycle_status="healthy",
            bill_move_ids=[8202],
            picking_ids=[7002],
            item_rows=[item_row],
        )
        trace = {
            "stock_move_rows_by_id": {
                8402: {
                    "id": 8402,
                    "product_id": [902, "Same Root"],
                    "picking_id": [7002, "PICK/IN/0002"],
                    "purchase_line_id": [8302, "PO line"],
                    "product_qty": 1000.0,
                    "product_uom": [16, "GRAM"],
                    "price_unit": 10.0,
                }
            },
            "svl_rows_by_stock_move_id": {8402: [{"id": 9002, "quantity": 1000.0, "value": 10000.0}]},
            "bill_line_rows_by_product": {
                902: [
                    {
                        "id": 8102,
                        "move_id": [8202, "BILL"],
                        "product_id": [902, "Same Root"],
                        "purchase_line_id": [8302, "PO line"],
                        "quantity": 10.0,
                        "price_subtotal": 100.0,
                        "product_uom_id": [15, "KG"],
                    }
                ]
            },
            "purchase_line_rows_by_id": {8302: {"id": 8302, "product_qty": 10.0, "product_uom": [15, "KG"]}},
            "uom_info_by_id": {
                15: {"id": 15, "name": "KG", "parent_path": "14/15/"},
                16: {"id": 16, "name": "GRAM", "parent_path": "14/16/"},
            },
            "return_picking_pairs": [],
        }

        service._populate_pcb_case8_case9_evidence_for_cycle(cycle=cycle, trace=trace, product_info_map={902: {"standard_price": 10.0}})
        SvlDashboardServiceAsync._apply_pcb_item_classifier(cycle=cycle, product_info_map={902: {"standard_price": 10.0}})

        self.assertEqual(item_row.case9_evidence, {})
        self.assertEqual(item_row.primary_case, "")

    def test_case8_full_and_partial_return_value_mismatch_classification(self) -> None:
        service = SvlDashboardServiceAsync(rpc=_FakeDashboardRpc(), logger=logging.getLogger("test.dashboard"))
        full_item = SvlDashboardCycleItemRow(product_id=903, product_name="Full Return", default_code="RET-FULL")
        partial_item = SvlDashboardCycleItemRow(product_id=904, product_name="Partial Return", default_code="RET-PART")
        cycle = SvlDashboardPurchaseCycle(
            picking_id=7003,
            picking_name="PICK/IN/0003",
            gr_date="2026-04-07",
            partner_name="Vendor",
            cycle_status="problem",
            picking_ids=[7003, 7004],
            item_rows=[full_item, partial_item],
            has_return_picking=True,
        )
        trace = {
            "stock_move_rows_by_id": {
                8403: {"id": 8403, "product_id": [903, "Full Return"], "picking_id": [7003, "PICK/IN/0003"], "returned_move_ids": [8404]},
                8404: {"id": 8404, "product_id": [903, "Full Return"], "picking_id": [7004, "PICK/OUT/0001"], "origin_returned_move_id": [8403, "Move"]},
                8405: {"id": 8405, "product_id": [904, "Partial Return"], "picking_id": [7003, "PICK/IN/0003"], "returned_move_ids": [8406]},
                8406: {"id": 8406, "product_id": [904, "Partial Return"], "picking_id": [7004, "PICK/OUT/0001"], "origin_returned_move_id": [8405, "Move"]},
            },
            "svl_rows_by_stock_move_id": {
                8403: [{"id": 9003, "quantity": 10.0, "value": 10000.0}],
                8404: [{"id": 9004, "quantity": -10.0, "value": -15000.0}],
                8405: [{"id": 9005, "quantity": 10.0, "value": 10000.0}],
                8406: [{"id": 9006, "quantity": -4.0, "value": -7000.0}],
            },
            "bill_line_rows_by_product": {},
            "purchase_line_rows_by_id": {},
            "uom_info_by_id": {},
            "return_picking_pairs": [(7004, 7003)],
        }

        case89_context = service._build_pcb_case8_case9_context(trace)
        service._populate_pcb_case8_case9_evidence_for_cycle(
            cycle=cycle,
            trace=trace,
            product_info_map={},
            case89_context=case89_context,
        )
        SvlDashboardServiceAsync._apply_pcb_item_classifier(cycle=cycle, product_info_map={})

        self.assertEqual(full_item.primary_case, "case8a")
        self.assertEqual(full_item.case8_evidence["return_gap"], 5000.0)
        self.assertEqual(partial_item.primary_case, "case8b")
        self.assertEqual(partial_item.case8_evidence["return_gap"], 3000.0)
        self.assertEqual(cycle.primary_case, "case8a")

    def test_case8_and_case9_promote_healthy_cycles_to_problem(self) -> None:
        case8_item = SvlDashboardCycleItemRow(
            product_id=905,
            product_name="Case 8 Hidden",
            default_code="RET-HIDDEN",
            case8_evidence={"case_key": "case8a", "return_gap": 5000.0, "correction_amount": 5000.0},
        )
        case8_cycle = SvlDashboardPurchaseCycle(
            picking_id=7008,
            picking_name="PICK/IN/0008",
            gr_date="2026-04-07",
            partner_name="Vendor",
            cycle_status="healthy",
            partial_group_key="partial_other",
            partial_group_label="Partial Lainnya",
            item_rows=[case8_item],
        )
        case9_item = SvlDashboardCycleItemRow(
            product_id=906,
            product_name="Case 9 Hidden",
            default_code="UOM-HIDDEN",
            case9_evidence={
                "value_gap": 1000.0,
                "correction_amount": 1000.0,
                "source_uom_name": "BOX",
                "product_uom_name": "PCS",
                "scale_factor": 10.0,
                "target_basis": "open_suspense_revalued",
            },
        )
        case9_cycle = SvlDashboardPurchaseCycle(
            picking_id=7009,
            picking_name="PICK/IN/0009",
            gr_date="2026-04-07",
            partner_name="Vendor",
            cycle_status="healthy",
            item_rows=[case9_item],
        )

        SvlDashboardServiceAsync._apply_pcb_item_classifier(cycle=case8_cycle, product_info_map={})
        SvlDashboardServiceAsync._apply_pcb_item_classifier(cycle=case9_cycle, product_info_map={})

        self.assertEqual(case8_item.primary_case, "case8a")
        self.assertEqual(case8_cycle.primary_case, "case8a")
        self.assertEqual(case8_cycle.cycle_status, "problem")
        self.assertEqual(case8_cycle.partial_group_key, "")
        self.assertEqual(case8_cycle.partial_group_label, "")
        self.assertEqual(case9_item.primary_case, "case9")
        self.assertEqual(case9_cycle.primary_case, "case9")
        self.assertEqual(case9_cycle.cycle_status, "problem")

    def test_grni_guard_keeps_unbilled_items_without_value_gap_out_of_repair(self) -> None:
        for evidence in ({}, {"case8_evidence": {"return_gap": 0.0}}, {"case9_evidence": {"scale_factor": 100.0, "value_gap": 0.0}}):
            with self.subTest(evidence=evidence):
                item = SvlDashboardCycleItemRow(product_id=901, product_name="Unbilled", **evidence)
                cycle = SvlDashboardPurchaseCycle(
                    picking_id=7001, picking_name="TEST/GRNI", gr_date="2026-04-07",
                    partner_name="Test Vendor", cycle_status="healthy", item_rows=[item],
                )
                SvlDashboardServiceAsync._apply_pcb_item_classifier(cycle=cycle, product_info_map={})
                self.assertEqual(item.primary_case, "")
                self.assertFalse(item.auto_repairable)
                self.assertEqual(cycle.cycle_status, "healthy")

    def test_case9_downstream_lines_clear_holder_and_preserve_economic_value(self) -> None:
        for hpp, inventory in ((-90.0, 100.0), (90.0, -100.0), (-100.0, 90.0), (90.0, 100.0), (-90.0, 90.0)):
            with self.subTest(hpp=hpp, inventory=inventory):
                lines = SvlDashboardServiceAsync._build_pcb_case9_planned_lines(
                    holder_basis="case2_downstream_clearing", amount=abs(hpp),
                    hpp_balance=hpp, inventory_balance=inventory,
                    valuation_account_code="1105003", valuation_account_name="Inventory",
                    expense_account_code="5101003", expense_account_name="HPP",
                )
                balances = {"5101003": hpp, "1105003": inventory, "1108099": 0.0}
                for line in lines:
                    balances[line.account_code] += line.amount if line.side == "debit" else -line.amount
                self.assertEqual(balances["5101003"], hpp + inventory)
                self.assertEqual(balances["1105003"], 0.0)
                self.assertEqual(balances["1108099"], 0.0)
                self.assertEqual(len(lines), 5 if hpp + inventory else 4)
                self.assertEqual(sum(line.amount if line.side == "debit" else -line.amount for line in lines), 0.0)

    def test_case8_and_case9_planned_line_directions(self) -> None:
        case8_too_large = SvlDashboardServiceAsync._build_pcb_case8_planned_lines(
            valuation_account_code="1105003",
            valuation_account_name="Persediaan",
            expense_account_code="5101003",
            expense_account_name="HPP",
            return_gap=5000.0,
        )
        self.assertEqual([(line.account_code, line.side) for line in case8_too_large], [("1105003", "debit"), ("5101003", "credit")])

        case8_too_small = SvlDashboardServiceAsync._build_pcb_case8_planned_lines(
            valuation_account_code="1105003",
            valuation_account_name="Persediaan",
            expense_account_code="5101003",
            expense_account_name="HPP",
            return_gap=-5000.0,
        )
        self.assertEqual([(line.account_code, line.side) for line in case8_too_small], [("5101003", "debit"), ("1105003", "credit")])

        case9_downstream = SvlDashboardServiceAsync._build_pcb_case9_planned_lines(
            holder_basis="case2_downstream_clearing",
            amount=37620000.0,
            hpp_balance=-37620000.0,
            inventory_balance=38000000.0,
            valuation_account_code="1105003",
            valuation_account_name="Persediaan",
            expense_account_code="5101003",
            expense_account_name="HPP",
        )
        self.assertEqual(
            [(line.role, line.account_code, line.side, line.amount) for line in case9_downstream],
            [
                ("case9_hpp_reclass", "5101003", "debit", 37620000.0),
                ("case9_hpp_clearing_offset", "1108099", "credit", 37620000.0),
                ("case9_inventory_offset", "1105003", "credit", 38000000.0),
                ("case9_inventory_clearing_offset", "1108099", "debit", 37620000.0),
                ("case9_price_gap_to_expense", "5101003", "debit", 380000.0),
            ],
        )

        case9_revalued = SvlDashboardServiceAsync._build_pcb_case9_planned_lines(
            holder_basis="open_suspense_revalued",
            amount=37600000.0,
            valuation_account_code="1105003",
            valuation_account_name="Persediaan",
            expense_account_code="5101003",
            expense_account_name="HPP",
        )
        self.assertEqual([(line.account_code, line.side) for line in case9_revalued], [("2103006", "debit"), ("5101003", "credit")])

        case9_inventory = SvlDashboardServiceAsync._build_pcb_case9_planned_lines(
            holder_basis="open_suspense_inventory",
            amount=37600000.0,
            valuation_account_code="1105003",
            valuation_account_name="Persediaan",
            expense_account_code="5101003",
            expense_account_name="HPP",
        )
        self.assertEqual([(line.account_code, line.side) for line in case9_inventory], [("2103006", "debit"), ("1105003", "credit")])

    def test_build_pcb_case2_repair_rows_case9_closed_suspense_uses_case2_amount_and_correction_refs(self) -> None:
        service = SvlDashboardServiceAsync(rpc=_FakeDashboardRpc(), logger=logging.getLogger("test.dashboard"))

        rows = service._build_pcb_case2_repair_rows(
            pcb_case="case9",
            cycle_status="problem",
            picking_id=7001,
            picking_name="PIKKP/IN/01509",
            gr_date="2026-04-07",
            partner_name="Vendor",
            po_names=["PO/PIKKPH/2025/12/01339"],
            bill_move_ids=[8201],
            item_rows=[
                SvlDashboardCycleItemRow(
                    product_id=901,
                    product_name="MIE RAMEN",
                    default_code="F-FZRP-0202",
                    has_item_bill=True,
                    has_item_stj=True,
                    correction_stj_move_ids=[8811],
                    correction_stj_refs=["STJ/2026/03/67112"],
                    case9_evidence={
                        "source_uom_name": "KG",
                        "product_uom_name": "PACK @10 PORSI",
                        "scale_factor": 100.0,
                        "expected_value": 400000.0,
                        "actual_svl_value": 38000000.0,
                        "value_gap": 37600000.0,
                        "correction_amount": 37620000.0,
                        "holder_basis": "case2_downstream_clearing",
                        "downstream_refs": ["STJ/2026/03/67112"],
                        "inventory_balance": 38000000.0,
                        "hpp_balance": -37620000.0,
                        "suspend_balance": 0.0,
                    },
                    account_rows=[
                        SvlDashboardCycleAccountRow(
                            account_id=1105003,
                            code="1105003",
                            name="Persediaan Makanan",
                            account_type="asset_current",
                            account_group="asset",
                            debit=38000000.0,
                            credit=0.0,
                            net_balance=38000000.0,
                            status="acceptable",
                        ),
                        SvlDashboardCycleAccountRow(
                            account_id=5101003,
                            code="5101003",
                            name="HPP - Makanan",
                            account_type="expense_direct_cost",
                            account_group="expense",
                            debit=0.0,
                            credit=37620000.0,
                            net_balance=-37620000.0,
                            status="acceptable",
                        ),
                    ],
                )
            ],
            raw_lines=[],
            lines_by_move={},
            account_info_map={},
            move_info_map={
                8201: {"name": "BILL/2026/02/0225", "partner_id": [88, "Vendor"]},
            },
            product_info_map={
                901: {
                    "default_code": "F-FZRP-0202",
                    "name": "MIE RAMEN",
                    "categ_name": "Food",
                    "valuation_account_code": "1105003",
                    "valuation_account_name": "Persediaan Makanan",
                    "expense_account_code": "5101003",
                    "expense_account_name": "HPP - Makanan",
                }
            },
            bill_rows_by_id={
                8201: {"invoice_date": "2026-02-25"},
            },
            purchase_line_product_map={},
            purchase_line_po_name_map={},
        )

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.amount, 38000000.0)
        self.assertEqual(row.external_clearing_amount, 37620000.0)
        self.assertEqual(row.external_clearing_refs, ["STJ/2026/03/67112"])
        self.assertTrue(row.review_required)
        self.assertNotIn("case9_holder_ambiguous", row.guard_flags)
        self.assertNotIn("zero_planned_lines", row.guard_flags)
        self.assertEqual(
            [(line.role, line.side, line.account_code, line.amount) for line in row.planned_lines],
            [
                ("case9_hpp_reclass", "debit", "5101003", 37620000.0),
                ("case9_hpp_clearing_offset", "credit", "1108099", 37620000.0),
                ("case9_inventory_offset", "credit", "1105003", 38000000.0),
                ("case9_inventory_clearing_offset", "debit", "1108099", 37620000.0),
                ("case9_price_gap_to_expense", "debit", "5101003", 380000.0),
            ],
        )

    def test_apply_pcb_item_classifier_keeps_correction_only_case5_residual_out_of_case1(self) -> None:
        service = SvlDashboardServiceAsync(rpc=_FakeDashboardRpc(), logger=logging.getLogger("test.dashboard"))
        cycle = SvlDashboardPurchaseCycle(
            picking_id=7004,
            picking_name="CBG/IN/01023",
            gr_date="2026-03-29",
            partner_name="Vendor Alpha",
            cycle_status="problem",
            picking_ids=[7004],
            correction_stj_move_ids=[8811],
            correction_stj_refs=["STJ/2026/03/1582"],
            raw_lines=[
                {
                    "jenis": "STJ",
                    "kode_transaksi": "STJ/2026/03/1582",
                    "akun_code": "1108099",
                    "akun_name": "Clearing",
                    "tipe_akun": "asset_current",
                    "kode_item": "F-FHVF-0167",
                    "nama_item": "NANAS MADU",
                },
                {
                    "jenis": "BILL",
                    "kode_transaksi": "BILL/2026/02/0091",
                    "akun_code": "2103006",
                    "akun_name": "Hutang Suspend",
                    "tipe_akun": "liability_current",
                    "kode_item": "F-FHVF-0167",
                    "nama_item": "NANAS MADU",
                },
            ],
            item_rows=[
                SvlDashboardCycleItemRow(
                    product_id=167,
                    product_name="NANAS MADU",
                    default_code="F-FHVF-0167",
                    standard_price=53000.0,
                    svl_zero_at_gr=True,
                    account_rows=[
                        SvlDashboardCycleAccountRow(
                            account_id=302,
                            code="1108099",
                            name="Clearing",
                            account_type="asset_current",
                            account_group="asset",
                            debit=53000.0,
                            credit=0.0,
                            net_balance=53000.0,
                            status="problem",
                        ),
                    ],
                ),
            ],
        )

        service._populate_pcb_case34_item_evidence_for_cycle(
            cycle=cycle,
            cycle_bill_move_ids=[8201],
            trace={"stock_move_rows_by_id": {}},
            lines_by_move={
                8201: [
                    {
                        "id": 91001,
                        "product_id": [167, "NANAS MADU"],
                        "purchase_line_id": [8301, "PO Line 1"],
                    },
                ],
            },
            move_info_map={
                8201: {"name": "BILL/2026/02/0091"},
                8811: {"name": "STJ/2026/03/1582"},
            },
            purchase_line_product_map={8301: 167},
        )
        service._finalize_pcb_case34_cycle_items(cycle=cycle)
        SvlDashboardServiceAsync._apply_pcb_item_classifier(
            cycle=cycle,
            product_info_map={
                167: {
                    "expense_account_code": "5101003",
                    "expense_account_name": "HPP - Makanan / COGS - Food",
                    "standard_price": 53000.0,
                }
            },
        )

        item_row = cycle.item_rows[0]
        self.assertFalse(item_row.has_item_stj)
        self.assertEqual(item_row.correction_stj_move_ids, [8811])
        self.assertEqual(item_row.primary_case, "case_lainnya")
        self.assertNotEqual(item_row.primary_case, "case1")

    def test_apply_pcb_item_classifier_marks_corrupt_stj_edge_when_bill_exists_without_valid_lines(self) -> None:
        cycle = SvlDashboardPurchaseCycle(
            picking_id=7002,
            picking_name="LHPK/IN/0002",
            gr_date="2026-03-19",
            partner_name="Vendor Alpha",
            cycle_status="problem",
            raw_lines=[
                {
                    "jenis": "BILL",
                    "akun_code": "510003",
                    "akun_name": "COGS - Corrupt",
                    "tipe_akun": "expense_direct_cost",
                    "kode_item": "SKU-CORRUPT",
                    "nama_item": "Produk Corrupt",
                }
            ],
            item_rows=[
                SvlDashboardCycleItemRow(
                    product_id=103,
                    product_name="Produk Corrupt",
                    default_code="SKU-CORRUPT",
                    has_item_bill=True,
                    stock_move_ids=[8403],
                    gr_quantity=1.0,
                    bill_quantity=1.0,
                    svl_zero_at_gr=False,
                    account_rows=[
                        SvlDashboardCycleAccountRow(
                            account_id=302,
                            code="1108099",
                            name="Clearing",
                            account_type="asset_current",
                            account_group="asset",
                            debit=50.0,
                            credit=0.0,
                            net_balance=50.0,
                            status="problem",
                        )
                    ],
                )
            ],
        )

        SvlDashboardServiceAsync._apply_pcb_item_classifier(
            cycle=cycle,
            product_info_map={
                103: {
                    "expense_account_code": "510003",
                    "expense_account_name": "COGS Corrupt",
                    "standard_price": 50.0,
                }
            },
        )

        item_row = cycle.item_rows[0]
        self.assertEqual(item_row.primary_case, "edge_stj_corrupt")
        self.assertEqual(item_row.stj_state, "corrupt")
        self.assertFalse(item_row.auto_repairable)
        self.assertIn("edge_stj_corrupt", item_row.secondary_flags)
        self.assertEqual(cycle.primary_case, "edge_stj_corrupt")

    def test_build_purchase_cycles_includes_matching_pcb_correction_move_in_raw_lines_and_reduces_problem_status(self) -> None:
        service = SvlDashboardServiceAsync(rpc=_FakeDashboardRpc(), logger=logging.getLogger("test.dashboard"))
        trace, all_ledger_rows, account_info_map, move_info_map, product_info_map = _build_case1_purchase_cycle_inputs()

        cycles = service._build_purchase_cycles(
            trace=trace,
            all_ledger_rows=all_ledger_rows,
            account_info_map=account_info_map,
            move_info_map=move_info_map,
            product_info_map=product_info_map,
            warnings=[],
            problem_codes=frozenset({"2103006", "1108099"}),
            info_codes=frozenset({"1105003", "5101010"}),
        )

        self.assertEqual(len(cycles), 1)
        cycle = cycles[0]
        self.assertNotEqual(cycle.cycle_status, "problem")
        self.assertEqual(cycle.partner_id, 77)
        self.assertEqual(cycle.bill_move_ids, [8001])
        self.assertEqual(cycle.correction_stj_move_ids, [8811])
        self.assertEqual(cycle.correction_stj_refs, ["STJ/2026/03/0811"])
        self.assertIn("STJ/2026/03/0811", cycle.stj_refs)
        self.assertIn("STJ/2026/03/0811", [row["kode_transaksi"] for row in cycle.raw_lines])
        by_code = {row.code: row.net_balance for row in cycle.account_rows}
        self.assertEqual(by_code["2103006"], 0.0)
        self.assertEqual(by_code["1108099"], 0.0)

    def test_build_purchase_cycles_ignores_entry_moves_in_bill_refs_and_item_bill_evidence(self) -> None:
        service = SvlDashboardServiceAsync(rpc=_FakeDashboardRpc(), logger=logging.getLogger("test.dashboard"))
        trace, all_ledger_rows, account_info_map, move_info_map, product_info_map = _build_case1_purchase_cycle_inputs()
        trace["bill_ids_by_product"][101].add(8811)
        trace["bill_rows_by_id"][8811] = {
            "id": 8811,
            "name": "STJ/2026/03/0811",
            "invoice_origin": "PO/WCGT/2026/01/01001",
            "payment_state": "not_paid",
            "move_type": "entry",
        }
        trace["bill_line_rows_by_product"][101].append(
            {
                "id": 9113,
                "move_id": [8811, "STJ/2026/03/0811"],
                "account_id": [301, "Hutang Suspend"],
                "product_id": [101, "ANGGUR HITAM / BLACK GRAPES"],
                "purchase_line_id": [3001, "PO Line 3001"],
                "price_unit": 263100.0,
                "quantity": 1.0,
                "price_subtotal": 263100.0,
                "balance": 263100.0,
            }
        )

        cycles = service._build_purchase_cycles(
            trace=trace,
            all_ledger_rows=all_ledger_rows,
            account_info_map=account_info_map,
            move_info_map=move_info_map,
            product_info_map=product_info_map,
            warnings=[],
            problem_codes=frozenset({"2103006", "1108099"}),
            info_codes=frozenset({"1105003", "5101010"}),
        )

        self.assertEqual(len(cycles), 1)
        cycle = cycles[0]
        item_row = cycle.item_rows[0]
        self.assertEqual(cycle.bill_move_ids, [8001])
        self.assertEqual(cycle.bill_refs, ["BILL/2026/02/0114"])
        self.assertEqual(item_row.bill_move_ids, [8001])
        self.assertEqual(item_row.bill_refs, ["BILL/2026/02/0114"])
        self.assertEqual(item_row.bill_quantity, 6.0)
        self.assertFalse(any("STJ/2026/03/0811" in pattern for pattern in cycle.issue_patterns))

    def test_build_purchase_cycles_can_skip_repair_rebuild_for_analyze_pipeline(self) -> None:
        class _CountingService(SvlDashboardServiceAsync):
            def __init__(self) -> None:
                super().__init__(rpc=_FakeDashboardRpc(), logger=logging.getLogger("test.dashboard"))
                self.rebuild_calls = 0

            def _rebuild_pcb_case2_repair_rows_for_cycles(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
                self.rebuild_calls += 1

        service = _CountingService()
        trace, all_ledger_rows, account_info_map, move_info_map, product_info_map = _build_case1_purchase_cycle_inputs()

        cycles = service._build_purchase_cycles(
            trace=trace,
            all_ledger_rows=all_ledger_rows,
            account_info_map=account_info_map,
            move_info_map=move_info_map,
            product_info_map=product_info_map,
            warnings=[],
            problem_codes=frozenset({"2103006", "1108099"}),
            info_codes=frozenset({"1105003", "5101010"}),
            build_repair_rows=False,
        )

        self.assertEqual(service.rebuild_calls, 0)
        self.assertEqual(len(cycles), 1)
        self.assertEqual(cycles[0].case2_repair_rows, [])

    def test_build_purchase_cycles_unpaid_bill_becomes_partial_bill_unpaid_group(self) -> None:
        service = SvlDashboardServiceAsync(rpc=_FakeDashboardRpc(), logger=logging.getLogger("test.dashboard"))
        trace, all_ledger_rows, account_info_map, move_info_map, product_info_map = _build_case1_purchase_cycle_inputs()

        cycles = service._build_purchase_cycles(
            trace=trace,
            all_ledger_rows=all_ledger_rows,
            account_info_map=account_info_map,
            move_info_map=move_info_map,
            product_info_map=product_info_map,
            warnings=[],
            problem_codes=frozenset({"2103006", "1108099"}),
            info_codes=frozenset({"1105003", "5101010"}),
        )

        self.assertEqual(len(cycles), 1)
        cycle = cycles[0]
        self.assertEqual(cycle.cycle_status, "partial")
        self.assertEqual(cycle.partial_group_key, "bill_unpaid")
        self.assertEqual(cycle.partial_group_label, "Bill Belum Paid")
        self.assertTrue(any("Pola 3A" in pattern for pattern in cycle.issue_patterns))

    def test_build_purchase_cycles_paid_bill_without_payment_becomes_partial_bill_unmatched_group(self) -> None:
        service = SvlDashboardServiceAsync(rpc=_FakeDashboardRpc(), logger=logging.getLogger("test.dashboard"))
        trace, all_ledger_rows, account_info_map, move_info_map, product_info_map = _build_case1_purchase_cycle_inputs()
        trace["bill_rows_by_id"][8001]["payment_state"] = "paid"

        cycles = service._build_purchase_cycles(
            trace=trace,
            all_ledger_rows=all_ledger_rows,
            account_info_map=account_info_map,
            move_info_map=move_info_map,
            product_info_map=product_info_map,
            warnings=[],
            problem_codes=frozenset({"2103006", "1108099"}),
            info_codes=frozenset({"1105003", "5101010"}),
        )

        self.assertEqual(len(cycles), 1)
        cycle = cycles[0]
        self.assertEqual(cycle.cycle_status, "partial")
        self.assertEqual(cycle.partial_group_key, "bill_unmatched")
        self.assertEqual(cycle.partial_group_label, "Bill Belum Matching")
        self.assertTrue(any("Pola 3B" in pattern for pattern in cycle.issue_patterns))

    def test_build_purchase_cycles_payment_without_bank_becomes_partial_reconcile_group(self) -> None:
        service = SvlDashboardServiceAsync(rpc=_FakeDashboardRpc(), logger=logging.getLogger("test.dashboard"))
        trace, all_ledger_rows, account_info_map, move_info_map, product_info_map = _build_case1_purchase_cycle_inputs()
        trace["bill_rows_by_id"][8001]["payment_state"] = "paid"
        trace["payment_rows_by_bill_id"] = {
            8001: [{"move_id": [8501, "PBK/2026/02/0001"]}],
        }
        move_info_map[8501] = {
            "id": 8501,
            "name": "PBK/2026/02/0001",
            "move_type": "entry",
            "ref": "Manual Payment",
        }

        cycles = service._build_purchase_cycles(
            trace=trace,
            all_ledger_rows=all_ledger_rows,
            account_info_map=account_info_map,
            move_info_map=move_info_map,
            product_info_map=product_info_map,
            warnings=[],
            problem_codes=frozenset({"2103006", "1108099"}),
            info_codes=frozenset({"1105003", "5101010"}),
        )

        self.assertEqual(len(cycles), 1)
        cycle = cycles[0]
        self.assertEqual(cycle.cycle_status, "partial")
        self.assertEqual(cycle.partial_group_key, "payment_unreconciled")
        self.assertEqual(cycle.partial_group_label, "Payment Belum Reconcile")
        self.assertEqual(cycle.payment_move_ids, [8501])
        self.assertEqual(cycle.bank_move_ids, [])
        self.assertTrue(any("Pola 4" in pattern for pattern in cycle.issue_patterns))

    def test_build_purchase_cycles_classifies_document_flow_as_purchase_backed_when_po_chain_exists(self) -> None:
        service = SvlDashboardServiceAsync(rpc=_FakeDashboardRpc(), logger=logging.getLogger("test.dashboard"))
        trace, all_ledger_rows, account_info_map, move_info_map, product_info_map = _build_case1_purchase_cycle_inputs()

        cycles = service._build_purchase_cycles(
            trace=trace,
            all_ledger_rows=all_ledger_rows,
            account_info_map=account_info_map,
            move_info_map=move_info_map,
            product_info_map=product_info_map,
            warnings=[],
            problem_codes=frozenset({"2103006", "1108099"}),
            info_codes=frozenset({"1105003", "5101010"}),
        )

        self.assertEqual(len(cycles), 1)
        cycle = cycles[0]
        self.assertEqual(cycle.document_classification, "purchase-backed")
        self.assertEqual(cycle.document_classification_label, "Purchase-Backed")
        self.assertIn("PO terhubung ke picking cycle", cycle.document_classification_reasons)

    def test_build_purchase_cycles_classifies_document_flow_as_purchase_likely_when_bill_exists_without_hard_po_link(self) -> None:
        service = SvlDashboardServiceAsync(rpc=_FakeDashboardRpc(), logger=logging.getLogger("test.dashboard"))
        trace, all_ledger_rows, account_info_map, move_info_map, product_info_map = _build_case1_purchase_cycle_inputs()
        trace["inventory_types_by_picking"] = {}
        trace["po_id_by_picking"] = {}
        trace["purchase_order_rows_by_id"] = {}
        trace["purchase_line_rows_by_id"] = {}
        trace["purchase_line_product_map"] = {}
        trace["purchase_line_po_name_map"] = {}
        trace["stock_move_rows_by_id"][8401]["purchase_line_id"] = False
        for bill_line in trace["bill_line_rows_by_product"][101]:
            bill_line["purchase_line_id"] = False

        cycles = service._build_purchase_cycles(
            trace=trace,
            all_ledger_rows=all_ledger_rows,
            account_info_map=account_info_map,
            move_info_map=move_info_map,
            product_info_map=product_info_map,
            warnings=[],
            problem_codes=frozenset({"2103006", "1108099"}),
            info_codes=frozenset({"1105003", "5101010"}),
        )

        self.assertEqual(len(cycles), 1)
        cycle = cycles[0]
        self.assertEqual(cycle.document_classification, "purchase-likely")
        self.assertEqual(cycle.document_classification_label, "Purchase-Likely")
        self.assertIn("Vendor bill ditemukan tanpa link PO yang utuh", cycle.document_classification_reasons)

    def test_build_purchase_cycles_classifies_document_flow_as_purchase_backed_when_inventory_type_is_purchase(self) -> None:
        service = SvlDashboardServiceAsync(rpc=_FakeDashboardRpc(), logger=logging.getLogger("test.dashboard"))
        trace, all_ledger_rows, account_info_map, move_info_map, product_info_map = _build_case1_purchase_cycle_inputs()
        trace["po_id_by_picking"] = {}
        trace["purchase_order_rows_by_id"] = {}
        trace["purchase_line_rows_by_id"] = {}
        trace["purchase_line_product_map"] = {}
        trace["purchase_line_po_name_map"] = {}
        trace["stock_move_rows_by_id"][8401]["purchase_line_id"] = False
        trace["bill_ids_by_product"] = {}
        trace["payment_rows_by_bill_id"] = {}
        trace["inventory_types_by_picking"] = {7001: ["purchase"]}

        cycles = service._build_purchase_cycles(
            trace=trace,
            all_ledger_rows=all_ledger_rows,
            account_info_map=account_info_map,
            move_info_map=move_info_map,
            product_info_map=product_info_map,
            warnings=[],
            problem_codes=frozenset({"2103006", "1108099"}),
            info_codes=frozenset({"1105003", "5101010"}),
        )

        self.assertEqual(len(cycles), 1)
        cycle = cycles[0]
        self.assertEqual(cycle.document_classification, "purchase-backed")
        self.assertEqual(cycle.inventory_types, ["purchase"])
        self.assertTrue(any("Inventory Type = Purchase" == reason for reason in cycle.document_classification_reasons))

    def test_build_purchase_cycles_classifies_document_flow_as_non_purchase_when_internal_signal_without_po_or_bill(self) -> None:
        service = SvlDashboardServiceAsync(rpc=_FakeDashboardRpc(), logger=logging.getLogger("test.dashboard"))
        trace, all_ledger_rows, account_info_map, move_info_map, product_info_map = _build_case1_purchase_cycle_inputs()
        trace["picking_rows_by_id"][7001]["name"] = "WCGT/INT/00036"
        trace["picking_rows_by_id"][7001]["origin"] = "Mutasi Antar Company"
        trace["picking_rows_by_id"][7001]["partner_id"] = False
        trace["inventory_types_by_picking"] = {7001: ["mutation"]}
        trace["po_id_by_picking"] = {}
        trace["purchase_order_rows_by_id"] = {}
        trace["purchase_line_rows_by_id"] = {}
        trace["purchase_line_product_map"] = {}
        trace["purchase_line_po_name_map"] = {}
        trace["stock_move_rows_by_id"][8401]["purchase_line_id"] = False
        trace["bill_ids_by_product"] = {}

        cycles = service._build_purchase_cycles(
            trace=trace,
            all_ledger_rows=all_ledger_rows,
            account_info_map=account_info_map,
            move_info_map=move_info_map,
            product_info_map=product_info_map,
            warnings=[],
            problem_codes=frozenset({"2103006", "1108099"}),
            info_codes=frozenset({"1105003", "5101010"}),
        )

        self.assertEqual(len(cycles), 1)
        cycle = cycles[0]
        self.assertEqual(cycle.bill_refs, [])
        self.assertEqual(cycle.document_classification, "non-purchase/intercompany")
        self.assertEqual(cycle.document_classification_label, "Non-Purchase / Intercompany")
        self.assertTrue(
            any("Inventory Type = Mutation" == reason for reason in cycle.document_classification_reasons)
        )

    def test_build_purchase_cycles_non_purchase_inventory_type_cannot_stay_problem_or_partial(self) -> None:
        service = SvlDashboardServiceAsync(rpc=_FakeDashboardRpc(), logger=logging.getLogger("test.dashboard"))
        trace, all_ledger_rows, account_info_map, move_info_map, product_info_map = _build_case1_purchase_cycle_inputs()
        trace["inventory_types_by_picking"] = {7001: ["mutation"]}

        cycles = service._build_purchase_cycles(
            trace=trace,
            all_ledger_rows=all_ledger_rows,
            account_info_map=account_info_map,
            move_info_map=move_info_map,
            product_info_map=product_info_map,
            warnings=[],
            problem_codes=frozenset({"2103006", "1108099"}),
            info_codes=frozenset({"1105003", "5101010"}),
        )

        self.assertEqual(len(cycles), 1)
        cycle = cycles[0]
        self.assertEqual(cycle.document_classification, "non-purchase/intercompany")
        self.assertEqual(cycle.cycle_status, "healthy")
        self.assertEqual(cycle.problem_account_count, 0)
        self.assertEqual(cycle.partial_group_key, "")
        self.assertTrue(any("Cycle non-purchase tidak masuk bucket problem/partial" == reason for reason in cycle.document_classification_reasons))
        self.assertFalse(any(row.status == "problem" for row in cycle.account_rows))

    def test_build_purchase_cycles_clearing_reclass_allows_mixed_cycle_when_only_zero_svl_problem_item_remains(self) -> None:
        service = SvlDashboardServiceAsync(rpc=_FakeDashboardRpc(), logger=logging.getLogger("test.dashboard"))
        trace = {
            "picking_rows_by_id": {
                7001: {
                    "id": 7001,
                    "name": "CBG/IN/01023",
                    "scheduled_date": "2026-03-29",
                    "partner_id": [77, "Vendor Alpha"],
                },
            },
            "po_id_by_picking": {7001: 5001},
            "stj_ids_by_picking": {7001: {8801}},
            "product_ids_by_picking": {7001: {101, 102}},
            "bill_ids_by_product": {101: {8001}, 102: {8001}},
            "payment_rows_by_bill_id": {},
            "bill_rows_by_id": {
                8001: {
                    "id": 8001,
                    "name": "BILL/2026/02/0091",
                    "invoice_date": "2026-02-10",
                    "date": "2026-02-10",
                    "invoice_origin": "PO/CB/2026/02/00946",
                    "payment_state": "not_paid",
                },
            },
            "purchase_order_rows_by_id": {
                5001: {
                    "id": 5001,
                    "name": "PO/CB/2026/02/00946",
                    "partner_id": [77, "Vendor Alpha"],
                },
            },
            "purchase_line_rows_by_id": {
                3001: {"id": 3001, "product_id": [101, "Produk Direct STJ"], "order_id": [5001, "PO/CB/2026/02/00946"]},
                3002: {"id": 3002, "product_id": [102, "NANAS MADU"], "order_id": [5001, "PO/CB/2026/02/00946"]},
            },
            "stock_move_rows_by_id": {
                8401: {
                    "id": 8401,
                    "product_id": [101, "Produk Direct STJ"],
                    "picking_id": [7001, "CBG/IN/01023"],
                    "purchase_line_id": [3001, "PO Line 3001"],
                    "account_move_ids": [8801],
                    "price_unit": 100.0,
                    "product_qty": 1.0,
                },
                8402: {
                    "id": 8402,
                    "product_id": [102, "NANAS MADU"],
                    "picking_id": [7001, "CBG/IN/01023"],
                    "purchase_line_id": [3002, "PO Line 3002"],
                    "account_move_ids": [],
                    "price_unit": 0.0,
                    "product_qty": 1.0,
                },
            },
            "bill_line_rows_by_product": {
                101: [
                    {
                        "id": 8101,
                        "move_id": [8001, "BILL/2026/02/0091"],
                        "account_id": [301, "Hutang Suspend"],
                        "product_id": [101, "Produk Direct STJ"],
                        "purchase_line_id": [3001, "PO Line 3001"],
                        "price_unit": 100.0,
                        "quantity": 1.0,
                        "price_subtotal": 100.0,
                        "balance": 100.0,
                    },
                ],
                102: [
                    {
                        "id": 8102,
                        "move_id": [8001, "BILL/2026/02/0091"],
                        "account_id": [301, "Hutang Suspend"],
                        "product_id": [102, "NANAS MADU"],
                        "purchase_line_id": [3002, "PO Line 3002"],
                        "price_unit": 53.0,
                        "quantity": 1.0,
                        "price_subtotal": 53.0,
                        "balance": 53.0,
                    },
                ],
            },
            "purchase_line_product_map": {3001: 101, 3002: 102},
            "purchase_line_po_name_map": {3001: "PO/CB/2026/02/00946", 3002: "PO/CB/2026/02/00946"},
            "payment_move_ids_by_product": {},
            "bank_move_ids_by_product": {},
            "matching_numbers_by_payment_move_id": {},
            "bank_move_ids_by_matching": {},
            "direct_bank_move_ids_by_bill": {},
            "direct_bills_by_bank_move_id": {},
            "expansion_bills_by_bill": {},
            "expansion_stjs_by_bill": {},
            "expansion_bills_by_stj": {},
            "pcb_correction_move_ids": [8811],
            "stj_value_by_move_product": {8801: {101: 100.0}},
            "return_picking_pairs": [],
        }
        all_ledger_rows = [
            {
                "id": 9101,
                "move_id": [8801, "STJ/2026/03/0501"],
                "account_id": [401, "Persediaan Makanan"],
                "product_id": [101, "Produk Direct STJ"],
                "purchase_line_id": [3001, "PO Line 3001"],
                "stock_move_id": [8401, "MOVE/8401"],
                "date": "2026-03-20",
                "name": "Inventory Direct",
                "debit": 100.0,
                "credit": 0.0,
                "balance": 100.0,
            },
            {
                "id": 9102,
                "move_id": [8801, "STJ/2026/03/0501"],
                "account_id": [301, "Hutang Suspend"],
                "product_id": [101, "Produk Direct STJ"],
                "purchase_line_id": [3001, "PO Line 3001"],
                "stock_move_id": [8401, "MOVE/8401"],
                "date": "2026-03-20",
                "name": "Suspend Direct",
                "debit": 0.0,
                "credit": 100.0,
                "balance": -100.0,
            },
            {
                "id": 8101,
                "move_id": [8001, "BILL/2026/02/0091"],
                "account_id": [301, "Hutang Suspend"],
                "product_id": [101, "Produk Direct STJ"],
                "purchase_line_id": [3001, "PO Line 3001"],
                "date": "2026-02-10",
                "name": "Bill direct item",
                "debit": 100.0,
                "credit": 0.0,
                "balance": 100.0,
            },
            {
                "id": 8102,
                "move_id": [8001, "BILL/2026/02/0091"],
                "account_id": [301, "Hutang Suspend"],
                "product_id": [102, "NANAS MADU"],
                "purchase_line_id": [3002, "PO Line 3002"],
                "date": "2026-02-10",
                "name": "Bill nanas madu",
                "debit": 53.0,
                "credit": 0.0,
                "balance": 53.0,
            },
            {
                "id": 8103,
                "move_id": [8001, "BILL/2026/02/0091"],
                "account_id": [501, "Hutang Pihak Ketiga"],
                "date": "2026-02-10",
                "name": "Payable",
                "debit": 0.0,
                "credit": 153.0,
                "balance": -153.0,
                "display_type": "payment_term",
            },
            {
                "id": 9111,
                "move_id": [8811, "STJ/2026/03/1582"],
                "account_id": [302, "Clearing"],
                "product_id": [102, "NANAS MADU"],
                "purchase_line_id": [3002, "PO Line 3002"],
                "date": "2026-03-29",
                "name": "Synthetic Clearing",
                "debit": 53.0,
                "credit": 0.0,
                "balance": 53.0,
            },
            {
                "id": 9112,
                "move_id": [8811, "STJ/2026/03/1582"],
                "account_id": [301, "Hutang Suspend"],
                "product_id": [102, "NANAS MADU"],
                "purchase_line_id": [3002, "PO Line 3002"],
                "date": "2026-03-29",
                "name": "Case 5 Repair",
                "debit": 0.0,
                "credit": 53.0,
                "balance": -53.0,
            },
        ]
        account_info_map = {
            301: {"code": "2103006", "name": "Hutang Suspend", "account_type": "liability_current"},
            302: {"code": "1108099", "name": "Clearing", "account_type": "asset_current"},
            401: {"code": "1105003", "name": "Persediaan Makanan", "account_type": "asset_current"},
            501: {"code": "2101002", "name": "Hutang Pihak Ketiga", "account_type": "liability_payable"},
        }
        move_info_map = {
            8001: {"id": 8001, "name": "BILL/2026/02/0091", "move_type": "in_invoice", "partner_id": [77, "Vendor Alpha"]},
            8801: {"id": 8801, "name": "STJ/2026/03/0501", "move_type": "entry", "ref": "Original STJ"},
            8811: {
                "id": 8811,
                "name": "STJ/2026/03/1582",
                "move_type": "entry",
                "ref": "Corrrection: Purchase Cycle Balance: CBG/IN/01023 / BILL/2026/02/0091 / F-FHVF-0167 - NANAS MADU",
            },
        }
        product_info_map = {
            101: {"default_code": "SKU-101", "name": "Produk Direct STJ", "categ_name": "VEGETABLES & FRUITS"},
            102: {"default_code": "F-FHVF-0167", "name": "NANAS MADU", "categ_name": "VEGETABLES & FRUITS"},
        }

        cycles = service._build_purchase_cycles(
            trace=trace,
            all_ledger_rows=all_ledger_rows,
            account_info_map=account_info_map,
            move_info_map=move_info_map,
            product_info_map=product_info_map,
            warnings=[],
            problem_codes=frozenset({"2103006", "1108099"}),
            info_codes=frozenset({"1105003", "5101010"}),
        )

        self.assertEqual(len(cycles), 1)
        cycle = cycles[0]
        self.assertEqual(cycle.cycle_status, "partial")
        self.assertEqual(cycle.partial_group_key, "clearing_reclass")
        self.assertEqual(cycle.partial_group_label, "Clearing via Jurnal Reclass")
        self.assertEqual(cycle.case1_link_rows, [])
        by_code = {row.code: row for row in cycle.account_rows}
        self.assertEqual(by_code["1108099"].status, "acceptable")
        self.assertEqual(round(float(by_code["1108099"].net_balance or 0.0), 2), 53.0)

    def test_build_purchase_cycles_clearing_reclass_does_not_downgrade_non_zero_svl_clearing_residual(self) -> None:
        service = SvlDashboardServiceAsync(rpc=_FakeDashboardRpc(), logger=logging.getLogger("test.dashboard"))
        trace = {
            "picking_rows_by_id": {
                7001: {
                    "id": 7001,
                    "name": "CBG/IN/02001",
                    "scheduled_date": "2026-03-29",
                    "partner_id": [77, "Vendor Alpha"],
                },
            },
            "po_id_by_picking": {7001: 5001},
            "stj_ids_by_picking": {7001: {8801}},
            "product_ids_by_picking": {7001: {101}},
            "bill_ids_by_product": {101: {8001}},
            "payment_rows_by_bill_id": {},
            "bill_rows_by_id": {
                8001: {
                    "id": 8001,
                    "name": "BILL/2026/02/0100",
                    "invoice_date": "2026-02-10",
                    "date": "2026-02-10",
                    "invoice_origin": "PO/CB/2026/02/01000",
                    "payment_state": "not_paid",
                },
            },
            "purchase_order_rows_by_id": {
                5001: {
                    "id": 5001,
                    "name": "PO/CB/2026/02/01000",
                    "partner_id": [77, "Vendor Alpha"],
                },
            },
            "purchase_line_rows_by_id": {
                3001: {"id": 3001, "product_id": [101, "Produk Direct Clearing"], "order_id": [5001, "PO/CB/2026/02/01000"]},
            },
            "stock_move_rows_by_id": {
                8401: {
                    "id": 8401,
                    "product_id": [101, "Produk Direct Clearing"],
                    "picking_id": [7001, "CBG/IN/02001"],
                    "purchase_line_id": [3001, "PO Line 3001"],
                    "account_move_ids": [8801],
                    "price_unit": 100.0,
                    "product_qty": 1.0,
                },
            },
            "bill_line_rows_by_product": {
                101: [
                    {
                        "id": 8101,
                        "move_id": [8001, "BILL/2026/02/0100"],
                        "account_id": [601, "HPP Makanan"],
                        "product_id": [101, "Produk Direct Clearing"],
                        "purchase_line_id": [3001, "PO Line 3001"],
                        "price_unit": 100.0,
                        "quantity": 1.0,
                        "price_subtotal": 100.0,
                        "balance": 100.0,
                    },
                ],
            },
            "purchase_line_product_map": {3001: 101},
            "purchase_line_po_name_map": {3001: "PO/CB/2026/02/01000"},
            "payment_move_ids_by_product": {},
            "bank_move_ids_by_product": {},
            "matching_numbers_by_payment_move_id": {},
            "bank_move_ids_by_matching": {},
            "direct_bank_move_ids_by_bill": {},
            "direct_bills_by_bank_move_id": {},
            "expansion_bills_by_bill": {},
            "expansion_stjs_by_bill": {},
            "expansion_bills_by_stj": {},
            "pcb_correction_move_ids": [],
            "stj_value_by_move_product": {8801: {101: 100.0}},
            "return_picking_pairs": [],
        }
        all_ledger_rows = [
            {
                "id": 9101,
                "move_id": [8801, "STJ/2026/03/0601"],
                "account_id": [401, "Persediaan Makanan"],
                "product_id": [101, "Produk Direct Clearing"],
                "purchase_line_id": [3001, "PO Line 3001"],
                "stock_move_id": [8401, "MOVE/8401"],
                "date": "2026-03-20",
                "name": "Inventory Direct",
                "debit": 100.0,
                "credit": 0.0,
                "balance": 100.0,
            },
            {
                "id": 9102,
                "move_id": [8801, "STJ/2026/03/0601"],
                "account_id": [302, "Clearing"],
                "product_id": [101, "Produk Direct Clearing"],
                "purchase_line_id": [3001, "PO Line 3001"],
                "stock_move_id": [8401, "MOVE/8401"],
                "date": "2026-03-20",
                "name": "Clearing Direct",
                "debit": 0.0,
                "credit": 100.0,
                "balance": -100.0,
            },
            {
                "id": 8101,
                "move_id": [8001, "BILL/2026/02/0100"],
                "account_id": [601, "HPP Makanan"],
                "product_id": [101, "Produk Direct Clearing"],
                "purchase_line_id": [3001, "PO Line 3001"],
                "date": "2026-02-10",
                "name": "Bill expense",
                "debit": 100.0,
                "credit": 0.0,
                "balance": 100.0,
            },
            {
                "id": 8102,
                "move_id": [8001, "BILL/2026/02/0100"],
                "account_id": [501, "Hutang Pihak Ketiga"],
                "date": "2026-02-10",
                "name": "Payable",
                "debit": 0.0,
                "credit": 100.0,
                "balance": -100.0,
                "display_type": "payment_term",
            },
        ]
        account_info_map = {
            302: {"code": "1108099", "name": "Clearing", "account_type": "asset_current"},
            401: {"code": "1105003", "name": "Persediaan Makanan", "account_type": "asset_current"},
            501: {"code": "2101002", "name": "Hutang Pihak Ketiga", "account_type": "liability_payable"},
            601: {"code": "5101003", "name": "HPP Makanan", "account_type": "expense_direct_cost"},
        }
        move_info_map = {
            8001: {"id": 8001, "name": "BILL/2026/02/0100", "move_type": "in_invoice", "partner_id": [77, "Vendor Alpha"]},
            8801: {"id": 8801, "name": "STJ/2026/03/0601", "move_type": "entry", "ref": "Original STJ"},
        }
        product_info_map = {
            101: {"default_code": "SKU-101", "name": "Produk Direct Clearing", "categ_name": "VEGETABLES & FRUITS"},
        }

        cycles = service._build_purchase_cycles(
            trace=trace,
            all_ledger_rows=all_ledger_rows,
            account_info_map=account_info_map,
            move_info_map=move_info_map,
            product_info_map=product_info_map,
            warnings=[],
            problem_codes=frozenset({"2103006", "1108099"}),
            info_codes=frozenset({"1105003", "5101010"}),
        )

        self.assertEqual(len(cycles), 1)
        cycle = cycles[0]
        self.assertEqual(cycle.cycle_status, "problem")
        self.assertEqual(cycle.partial_group_key, "")

    def test_populate_pcb_case34_item_evidence_uses_cycle_correction_stj_for_all_items_without_touching_direct_stj(self) -> None:
        service = SvlDashboardServiceAsync(rpc=_FakeDashboardRpc(), logger=logging.getLogger("test.dashboard"))
        cycle = SvlDashboardPurchaseCycle(
            picking_id=7001,
            picking_name="CBG/IN/00889",
            gr_date="2026-03-20",
            partner_name="Vendor Alpha",
            picking_ids=[7001],
            correction_stj_move_ids=[8811],
            correction_stj_refs=["STJ/2026/03/0160"],
            bill_refs=["BILL/2026/02/0024"],
            item_rows=[
                SvlDashboardCycleItemRow(
                    product_id=901,
                    product_name="Produk A",
                    default_code="SKU-901",
                ),
                SvlDashboardCycleItemRow(
                    product_id=902,
                    product_name="Produk B",
                    default_code="SKU-902",
                ),
            ],
        )

        service._populate_pcb_case34_item_evidence_for_cycle(
            cycle=cycle,
            cycle_bill_move_ids=[8201],
            trace={"stock_move_rows_by_id": {}},
            lines_by_move={
                8201: [
                    {
                        "id": 91001,
                        "product_id": [901, "Produk A"],
                        "purchase_line_id": [8301, "PO Line 1"],
                    },
                    {
                        "id": 91002,
                        "product_id": [902, "Produk B"],
                        "purchase_line_id": [8302, "PO Line 2"],
                    },
                ]
            },
            move_info_map={8201: {"name": "BILL/2026/02/0024"}},
            purchase_line_product_map={8301: 901, 8302: 902},
        )
        service._finalize_pcb_case34_cycle_items(cycle=cycle)

        for item_row in cycle.item_rows:
            self.assertEqual(item_row.bill_refs, ["BILL/2026/02/0024"])
            self.assertEqual(item_row.stj_move_ids, [])
            self.assertEqual(item_row.stj_refs, [])
            self.assertFalse(item_row.has_item_stj)
            self.assertEqual(item_row.correction_stj_move_ids, [8811])
            self.assertEqual(item_row.correction_stj_refs, ["STJ/2026/03/0160"])
            self.assertTrue(item_row.has_stj_evidence)
            self.assertTrue(item_row.eligible_case34)

    def test_populate_pcb_case34_item_evidence_falls_back_to_raw_detail_stj_when_stock_move_link_is_missing(self) -> None:
        service = SvlDashboardServiceAsync(rpc=_FakeDashboardRpc(), logger=logging.getLogger("test.dashboard"))
        cycle = SvlDashboardPurchaseCycle(
            picking_id=7005,
            picking_name="AC/IN/00900",
            gr_date="2026-03-20",
            partner_name="Vendor Alpha",
            cycle_status="problem",
            picking_ids=[7005],
            raw_lines=[
                {
                    "jenis": "STJ",
                    "kode_transaksi": "STJ/2026/03/0390",
                    "akun_code": "1105004",
                    "akun_name": "Persediaan Rokok / Cigarette Inventory",
                    "tipe_akun": "asset_current",
                    "kode_item": "T-DGTC-0050",
                    "nama_item": "KOREK API (AMBYAR SUPERCLUB)",
                },
                {
                    "jenis": "STJ",
                    "kode_transaksi": "STJ/2026/03/0390",
                    "akun_code": "1108099",
                    "akun_name": "Clearing - System Pending Entries",
                    "tipe_akun": "asset_current",
                    "kode_item": "T-DGTC-0050",
                    "nama_item": "KOREK API (AMBYAR SUPERCLUB)",
                },
                {
                    "jenis": "BILL",
                    "kode_transaksi": "BILL/2026/01/0208",
                    "akun_code": "5101004",
                    "akun_name": "HPP - Rokok / COGS - Cigarette",
                    "tipe_akun": "expense_direct_cost",
                    "kode_item": "T-DGTC-0050",
                    "nama_item": "KOREK API (AMBYAR SUPERCLUB)",
                },
            ],
            item_rows=[
                SvlDashboardCycleItemRow(
                    product_id=905,
                    product_name="KOREK API (AMBYAR SUPERCLUB)",
                    default_code="T-DGTC-0050",
                    stock_move_ids=[8405],
                    account_rows=[
                        SvlDashboardCycleAccountRow(
                            account_id=305,
                            code="1108099",
                            name="Clearing - System Pending Entries",
                            account_type="asset_current",
                            account_group="asset",
                            debit=0.0,
                            credit=4685000.0,
                            net_balance=-4685000.0,
                            status="problem",
                        )
                    ],
                )
            ],
        )

        service._populate_pcb_case34_item_evidence_for_cycle(
            cycle=cycle,
            cycle_bill_move_ids=[8205],
            trace={
                "stock_move_rows_by_id": {
                    8405: {
                        "id": 8405,
                        "product_id": [905, "KOREK API (AMBYAR SUPERCLUB)"],
                        "picking_id": [7005, "AC/IN/00900"],
                        "purchase_line_id": [8305, "PO Line 5"],
                        "product_qty": 20.0,
                        "account_move_ids": [],
                    }
                }
            },
            lines_by_move={
                8205: [
                    {
                        "id": 9205,
                        "move_id": [8205, "BILL/2026/01/0208"],
                        "account_id": [505, "HPP - Rokok / COGS - Cigarette"],
                        "product_id": [905, "KOREK API (AMBYAR SUPERCLUB)"],
                        "purchase_line_id": [8305, "PO Line 5"],
                    }
                ],
                8805: [
                    {
                        "id": 9305,
                        "move_id": [8805, "STJ/2026/03/0390"],
                        "account_id": [405, "Clearing - System Pending Entries"],
                        "product_id": [905, "KOREK API (AMBYAR SUPERCLUB)"],
                        "purchase_line_id": [8305, "PO Line 5"],
                        "stock_move_id": [8405, "MOVE/8405"],
                        "partner_id": [77, "Vendor Alpha"],
                    }
                ],
            },
            move_info_map={
                8205: {"name": "BILL/2026/01/0208", "partner_id": [77, "Vendor Alpha"]},
                8805: {"name": "STJ/2026/03/0390"},
            },
            purchase_line_product_map={8305: 905},
        )
        service._finalize_pcb_case34_cycle_items(cycle=cycle)

        item_row = cycle.item_rows[0]
        self.assertEqual(item_row.bill_move_ids, [8205])
        self.assertEqual(item_row.purchase_line_ids, [8305])
        self.assertEqual(item_row.stj_move_ids, [8805])
        self.assertEqual(item_row.stj_refs, ["STJ/2026/03/0390"])
        self.assertTrue(item_row.has_item_stj)
        self.assertTrue(item_row.eligible_case34)

        service._apply_pcb_item_classifier(
            cycle=cycle,
            product_info_map={
                905: {
                    "expense_account_code": "5101004",
                    "expense_account_name": "HPP - Rokok / COGS - Cigarette",
                }
            },
            lines_by_move={
                8205: [
                    {
                        "id": 9205,
                        "move_id": [8205, "BILL/2026/01/0208"],
                        "account_id": [505, "HPP - Rokok / COGS - Cigarette"],
                        "product_id": [905, "KOREK API (AMBYAR SUPERCLUB)"],
                        "purchase_line_id": [8305, "PO Line 5"],
                    }
                ],
                8805: [
                    {
                        "id": 9305,
                        "move_id": [8805, "STJ/2026/03/0390"],
                        "account_id": [405, "Clearing - System Pending Entries"],
                        "product_id": [905, "KOREK API (AMBYAR SUPERCLUB)"],
                        "purchase_line_id": [8305, "PO Line 5"],
                        "stock_move_id": [8405, "MOVE/8405"],
                        "partner_id": [77, "Vendor Alpha"],
                    }
                ],
            },
            account_info_map={
                405: {"code": "1108099", "account_type": "asset_current"},
                505: {"code": "5101004", "account_type": "expense_direct_cost"},
            },
            cycle_bill_move_ids=[8205],
        )

        self.assertEqual(item_row.primary_case, "case3")

        rows = service._build_pcb_case2_repair_rows(
            pcb_case="case3",
            cycle_status="problem",
            picking_id=7005,
            picking_ids=[7005],
            picking_name="AC/IN/00900",
            gr_date="2026-03-20",
            partner_name="Vendor Alpha",
            po_names=["PO/AC/2026/01/00740"],
            bill_move_ids=[8205],
            all_cycle_move_ids=[8205, 8805],
            item_rows=[item_row],
            raw_lines=list(cycle.raw_lines),
            lines_by_move={
                8205: [
                    {
                        "id": 9205,
                        "move_id": [8205, "BILL/2026/01/0208"],
                        "account_id": [505, "HPP - Rokok / COGS - Cigarette"],
                        "product_id": [905, "KOREK API (AMBYAR SUPERCLUB)"],
                        "purchase_line_id": [8305, "PO Line 5"],
                    }
                ],
                8805: [
                    {
                        "id": 9305,
                        "move_id": [8805, "STJ/2026/03/0390"],
                        "account_id": [405, "Clearing - System Pending Entries"],
                        "product_id": [905, "KOREK API (AMBYAR SUPERCLUB)"],
                        "purchase_line_id": [8305, "PO Line 5"],
                        "stock_move_id": [8405, "MOVE/8405"],
                        "partner_id": [77, "Vendor Alpha"],
                    }
                ],
            },
            account_info_map={
                405: {"code": "1108099", "account_type": "asset_current"},
                505: {"code": "5101004", "account_type": "expense_direct_cost"},
            },
            move_info_map={
                8205: {"name": "BILL/2026/01/0208", "partner_id": [77, "Vendor Alpha"]},
                8805: {"name": "STJ/2026/03/0390"},
            },
            product_info_map={
                905: {
                    "default_code": "T-DGTC-0050",
                    "name": "KOREK API (AMBYAR SUPERCLUB)",
                    "categ_name": "TOBACCO",
                    "valuation_account_code": "1105004",
                    "valuation_account_name": "Persediaan Rokok / Cigarette Inventory",
                    "expense_account_code": "5101004",
                    "expense_account_name": "HPP - Rokok / COGS - Cigarette",
                }
            },
            bill_rows_by_id={8205: {"invoice_date": "2026-01-22", "invoice_origin": "PO/AC/2026/01/00740"}},
            purchase_line_product_map={8305: 905},
            purchase_line_po_name_map={8305: "PO/AC/2026/01/00740"},
            stock_move_rows_by_id={
                8405: {
                    "id": 8405,
                    "product_id": [905, "KOREK API (AMBYAR SUPERCLUB)"],
                    "picking_id": [7005, "AC/IN/00900"],
                    "purchase_line_id": [8305, "PO Line 5"],
                    "product_qty": 20.0,
                    "price_unit": 234250.0,
                }
            },
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].stj_move_ids, [8805])
        self.assertEqual(rows[0].stj_refs, ["STJ/2026/03/0390"])
        self.assertEqual(rows[0].clearing_target_aml_ids, [9305])

    def test_rebuild_pcb_case2_repair_rows_uses_stored_cycle_bill_ids_when_bill_ref_name_lookup_fails(self) -> None:
        service = SvlDashboardServiceAsync(rpc=_FakeDashboardRpc(), logger=logging.getLogger("test.dashboard"))
        cycle = SvlDashboardPurchaseCycle(
            picking_id=7001,
            picking_name="CBG/IN/00678",
            gr_date="2026-01-06",
            partner_name="",
            partner_id=77,
            cycle_status="problem",
            picking_ids=[7001],
            bill_move_ids=[8201],
            payment_move_ids=[8501],
            bank_move_ids=[8601],
            bill_refs=["BILL-ALIAS/YANG-TIDAK-COCOK"],
            account_rows=[
                SvlDashboardCycleAccountRow(
                    account_id=301,
                    code="2103006",
                    name="Hutang Suspend",
                    account_type="liability_current",
                    account_group="liability",
                    debit=4200000.0,
                    credit=0.0,
                    net_balance=4200000.0,
                    status="problem",
                )
            ],
            item_rows=[
                SvlDashboardCycleItemRow(
                    product_id=901,
                    product_name="BOMBAY SAPHIRE",
                    default_code="A-SPGN-0001",
                    bill_move_ids=[8201],
                    purchase_line_ids=[8301],
                    stock_move_ids=[8401],
                    stj_move_ids=[8801],
                    stj_refs=["STJ/2025/12/1168"],
                    has_item_bill=True,
                    has_item_stj=True,
                    account_rows=[
                        SvlDashboardCycleAccountRow(
                            account_id=301,
                            code="2103006",
                            name="Hutang Suspend",
                            account_type="liability_current",
                            account_group="liability",
                            debit=4200000.0,
                            credit=0.0,
                            net_balance=4200000.0,
                            status="problem",
                        )
                    ],
                )
            ],
        )

        service._rebuild_pcb_case2_repair_rows_for_cycles(
            cycles=[cycle],
            adjustment_moves=[],
            trace={
                "bill_rows_by_id": {
                    8201: {
                        "id": 8201,
                        "name": "BILL/2026/01/0077",
                        "invoice_date": "2026-01-06",
                        "invoice_origin": "PO/CB/2025/12/00652",
                        "partner_id": [77, "Vendor Alpha"],
                    }
                },
                "purchase_line_product_map": {8301: 901},
                "purchase_line_po_name_map": {8301: "PO/CB/2025/12/00652"},
                "payment_move_ids_by_product": {901: [8501]},
                "bank_move_ids_by_product": {901: [8601]},
                "stock_move_rows_by_id": {
                    8401: {
                        "id": 8401,
                        "product_id": [901, "BOMBAY SAPHIRE"],
                        "picking_id": [7001, "CBG/IN/00678"],
                        "purchase_line_id": [8301, "PO Line 8301"],
                        "account_move_ids": [8801],
                    }
                },
            },
            all_ledger_rows=[
                {
                    "id": 91001,
                    "move_id": [8201, "BILL/2026/01/0077"],
                    "account_id": [401, "HPP Minuman"],
                    "product_id": [901, "BOMBAY SAPHIRE"],
                    "purchase_line_id": [8301, "PO Line 8301"],
                    "debit": 4200000.0,
                    "credit": 0.0,
                    "balance": 4200000.0,
                },
                {
                    "id": 91002,
                    "move_id": [8201, "BILL/2026/01/0077"],
                    "account_id": [301, "Hutang Suspend"],
                    "product_id": [901, "BOMBAY SAPHIRE"],
                    "purchase_line_id": [8301, "PO Line 8301"],
                    "partner_id": [77, "Vendor Alpha"],
                    "debit": 4200000.0,
                    "credit": 0.0,
                    "balance": 4200000.0,
                },
                {
                    "id": 91003,
                    "move_id": [8801, "STJ/2025/12/1168"],
                    "account_id": [302, "Clearing"],
                    "product_id": [901, "BOMBAY SAPHIRE"],
                    "purchase_line_id": [8301, "PO Line 8301"],
                    "stock_move_id": [8401, "MOVE/8401"],
                    "partner_id": [77, "Vendor Alpha"],
                    "debit": 0.0,
                    "credit": 4200000.0,
                    "balance": -4200000.0,
                }
            ],
            account_info_map={
                301: {
                    "code": "2103006",
                    "name": "Hutang Suspend",
                    "account_type": "liability_current",
                },
                302: {
                    "code": "1108099",
                    "name": "Clearing",
                    "account_type": "asset_current",
                },
                401: {
                    "code": "5101001",
                    "name": "HPP Minuman",
                    "account_type": "expense_direct_cost",
                }
            },
            move_info_map={
                8201: {
                    "id": 8201,
                    "name": "BILL/2026/01/0077",
                    "partner_id": [77, "Vendor Alpha"],
                    "move_type": "in_invoice",
                },
                8801: {
                    "id": 8801,
                    "name": "STJ/2025/12/1168",
                    "move_type": "entry",
                },
            },
            product_info_map={
                901: {
                    "default_code": "A-SPGN-0001",
                    "name": "BOMBAY SAPHIRE",
                    "categ_name": "GIN",
                    "expense_account_code": "5101001",
                    "expense_account_name": "HPP Minuman",
                    "valuation_account_code": "1105001",
                    "valuation_account_name": "Persediaan Alkohol",
                }
            },
        )

        self.assertEqual(len(cycle.case2_repair_rows), 1)
        repair_row = cycle.case2_repair_rows[0]
        self.assertEqual(repair_row.pcb_case, "case4")
        self.assertEqual(repair_row.bill_move_id, 8201)
        self.assertEqual(repair_row.bill_name, "BILL/2026/01/0077")
        self.assertEqual(repair_row.po_name, "PO/CB/2025/12/00652")
        self.assertEqual(repair_row.partner_id, 77)
        self.assertEqual(repair_row.partner_name, "Vendor Alpha")
        self.assertEqual(repair_row.purchase_line_id, 8301)
        self.assertEqual(repair_row.stock_move_id, 8401)
        self.assertEqual(repair_row.stock_move_ids, [8401])
        self.assertEqual(repair_row.payment_move_ids, [8501])
        self.assertEqual(repair_row.bank_move_ids, [8601])
        self.assertEqual(repair_row.suspend_target_aml_ids, [91002])
        self.assertEqual(repair_row.clearing_target_aml_ids, [91003])

    def test_rebuild_pcb_case2_repair_rows_early_exit_skips_healthy_cycles_after_classifier(self) -> None:
        class _TrackingService(SvlDashboardServiceAsync):
            def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
                super().__init__(*args, **kwargs)
                self.classify_cycle_case_calls = 0
                self.build_case2_calls = 0

            def _classify_pcb_cycle_case(self, *args, **kwargs):  # noqa: ANN002, ANN003
                self.classify_cycle_case_calls += 1
                return "case_lainnya"

            def _build_pcb_case2_repair_rows(self, *args, **kwargs):  # noqa: ANN002, ANN003
                self.build_case2_calls += 1
                return []

        service = _TrackingService(rpc=_FakeDashboardRpc(), logger=logging.getLogger("test.dashboard"))
        cycle = SvlDashboardPurchaseCycle(
            picking_id=7001,
            picking_name="CBG/IN/00678",
            gr_date="2026-01-06",
            partner_name="Vendor Alpha",
            partner_id=77,
            cycle_status="healthy",
            picking_ids=[7001],
            account_rows=[],
            item_rows=[],
        )

        service._rebuild_pcb_case2_repair_rows_for_cycles(
            cycles=[cycle],
            adjustment_moves=[],
            trace={},
            all_ledger_rows=[],
            account_info_map={},
            move_info_map={},
            product_info_map={},
        )

        self.assertEqual(service.classify_cycle_case_calls, 0)
        self.assertEqual(service.build_case2_calls, 0)
        self.assertEqual(cycle.cycle_status, "healthy")
        self.assertEqual(cycle.primary_case, "")
        self.assertEqual(cycle.case1_link_rows, [])
        self.assertEqual(cycle.case2_repair_rows, [])

    def test_rebuild_pcb_case2_repair_rows_does_not_skip_cycle_promoted_from_healthy_by_case9(self) -> None:
        class _TrackingService(SvlDashboardServiceAsync):
            def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
                super().__init__(*args, **kwargs)
                self.classify_cycle_case_calls = 0
                self.build_case2_calls = 0

            def _populate_pcb_case8_case9_evidence_for_cycle(self, *, cycle, trace, product_info_map, case89_context=None):  # noqa: ANN001
                for item_row in list(getattr(cycle, "item_rows", None) or []):
                    item_row.case8_evidence = {}
                    item_row.case9_evidence = {
                        "source": "bill_standard_price",
                        "source_qty": 4.0,
                        "source_value": 4000.0,
                        "expected_value": 4000.0,
                        "actual_value": 400000.0,
                        "value_gap": 396000.0,
                        "correction_amount": 396000.0,
                        "scale_factor": 100.0,
                        "source_uom_id": 11,
                        "product_uom_id": 22,
                        "source_uom_name": "BOX",
                        "product_uom_name": "PCS",
                        "target_basis": "uom_scale_expected_value",
                        "holder_basis": "case2_downstream_clearing",
                        "stock_move_id": 8401,
                        "stock_move_ids": [8401],
                        "svl_ids": [9101],
                    }

            def _classify_pcb_cycle_case(self, *args, **kwargs):  # noqa: ANN002, ANN003
                self.classify_cycle_case_calls += 1
                return "case9"

            def _build_pcb_case2_repair_rows(self, *args, **kwargs):  # noqa: ANN002, ANN003
                self.build_case2_calls += 1
                return []

        service = _TrackingService(rpc=_FakeDashboardRpc(), logger=logging.getLogger("test.dashboard"))
        cycle = SvlDashboardPurchaseCycle(
            picking_id=7001,
            picking_name="CBG/IN/00678",
            gr_date="2026-01-06",
            partner_name="Vendor Alpha",
            partner_id=77,
            cycle_status="healthy",
            picking_ids=[7001],
            account_rows=[],
            item_rows=[
                SvlDashboardCycleItemRow(
                    product_id=901,
                    product_name="Produk UoM Mismatch",
                    default_code="SKU-901",
                )
            ],
        )

        service._rebuild_pcb_case2_repair_rows_for_cycles(
            cycles=[cycle],
            adjustment_moves=[],
            trace={},
            all_ledger_rows=[],
            account_info_map={},
            move_info_map={},
            product_info_map={},
        )

        self.assertEqual(cycle.item_rows[0].primary_case, "case9")
        self.assertEqual(cycle.cycle_status, "problem")
        self.assertEqual(cycle.primary_case, "case9")
        self.assertEqual(service.classify_cycle_case_calls, 1)
        self.assertEqual(service.build_case2_calls, 1)
        self.assertEqual(cycle.case2_repair_rows, [])

    def test_find_pcb_correction_move_ids_accepts_stock_picking_and_invoice_alias_fields(self) -> None:
        rpc = _FakeDashboardRpc()
        rpc.fields_map["account.move"] = {
            "name": {"type": "char"},
            "ref": {"type": "char"},
            "date": {"type": "date"},
            "state": {"type": "selection"},
            "journal_id": {"type": "many2one"},
            "company_id": {"type": "many2one"},
            "partner_id": {"type": "many2one"},
            "stock_picking_id": {"type": "many2one"},
            "invoice_id": {"type": "many2one"},
        }
        rpc.fields_map["account.move.line"]["stock_picking_id"] = {"type": "many2one"}
        rpc.fields_map["account.move.line"]["invoice_id"] = {"type": "many2one"}
        rpc.account_rows.append(
            {
                "id": 301,
                "code": "2103006",
                "name": "Hutang Suspend",
                "company_ids": [1],
                "account_type": "liability_current",
            }
        )
        service = SvlDashboardServiceAsync(rpc=rpc, logger=logging.getLogger("test.dashboard"))

        async def custom_search_read(model, domain, fields=None, limit=None, context=None, stage="", order=None):  # noqa: ANN001
            if model == "account.move":
                return [
                    {
                        "id": 8811,
                        "name": "STJ/2026/03/0689",
                        "ref": "Corrrection: Purchase Cycle Balance: CBG/IN/00678 / A-SPGN-0001 - BOMBAY SAPHIRE",
                        "date": "2026-03-26",
                        "state": "posted",
                        "journal_id": [81, "Stock Journal"],
                        "company_id": [1, "Alpha Company"],
                        "partner_id": False,
                        "stock_picking_id": [7001, "CBG/IN/00678"],
                        "invoice_id": [8201, "BILL/2026/01/0077"],
                    }
                ]
            if model == "account.move.line":
                return [
                    {
                        "id": 9111,
                        "move_id": [8811, "STJ/2026/03/0689"],
                        "account_id": [301, "Hutang Suspend"],
                        "product_id": [901, "BOMBAY SAPHIRE"],
                        "stock_picking_id": [7001, "CBG/IN/00678"],
                        "invoice_id": [8201, "BILL/2026/01/0077"],
                        "display_type": False,
                        "company_id": [1, "Alpha Company"],
                    }
                ]
            return await _FakeDashboardRpc.search_read(
                rpc,
                model,
                domain,
                fields=fields,
                limit=limit,
                context=context,
                stage=stage,
                order=order,
            )

        rpc.search_read = custom_search_read
        matched_ids = asyncio.run(
            service._find_pcb_correction_move_ids(
                request=SvlDashboardRequest(database="hwgroup_erp", company_id=1),
                date_from="",
                date_to="",
                context={},
                trace={
                    "product_ids": [901],
                    "picking_rows_by_id": {7001: {"id": 7001, "name": "CBG/IN/00678"}},
                    "bill_rows_by_id": {8201: {"id": 8201, "name": "BILL/2026/01/0077"}},
                },
                problem_codes=frozenset({"2103006", "1108099"}),
            )
        )

        self.assertEqual(matched_ids, [8811])

    def test_match_pcb_correction_move_ids_to_cycle_accepts_stock_picking_and_invoice_alias_from_ledger_rows(self) -> None:
        service = SvlDashboardServiceAsync(rpc=_FakeDashboardRpc(), logger=logging.getLogger("test.dashboard"))

        matched_ids = service._match_pcb_correction_move_ids_to_cycle(
            candidate_move_ids=[8811],
            group_picking_ids=[7001],
            all_picking_names=["CBG/IN/00678"],
            product_ids_in_picking=[901],
            bill_move_ids=[8201],
            lines_by_move={
                8811: [
                    {
                        "id": 9111,
                        "account_id": [301, "Hutang Suspend"],
                        "product_id": [901, "BOMBAY SAPHIRE"],
                        "stock_picking_id": [7001, "CBG/IN/00678"],
                        "invoice_id": [8201, "BILL/2026/01/0077"],
                    }
                ]
            },
            account_info_map={301: {"code": "2103006"}},
            move_info_map={
                8811: {
                    "id": 8811,
                    "name": "STJ/2026/03/0689",
                    "ref": "Corrrection: Purchase Cycle Balance: CBG/IN/00678 / A-SPGN-0001 - BOMBAY SAPHIRE",
                },
                8201: {"id": 8201, "name": "BILL/2026/01/0077"},
            },
            stock_move_rows_by_id={},
            problem_codes=frozenset({"2103006", "1108099"}),
        )

        self.assertEqual(matched_ids, [8811])

    def test_build_purchase_cycles_does_not_leak_pcb_correction_move_to_other_cycle_with_same_product(self) -> None:
        service = SvlDashboardServiceAsync(rpc=_FakeDashboardRpc(), logger=logging.getLogger("test.dashboard"))
        trace, all_ledger_rows, account_info_map, move_info_map, product_info_map = _build_case1_purchase_cycle_inputs(
            include_second_cycle=True
        )

        cycles = service._build_purchase_cycles(
            trace=trace,
            all_ledger_rows=all_ledger_rows,
            account_info_map=account_info_map,
            move_info_map=move_info_map,
            product_info_map=product_info_map,
            warnings=[],
            problem_codes=frozenset({"2103006", "1108099"}),
            info_codes=frozenset({"1105003", "5101010"}),
        )

        self.assertEqual(len(cycles), 2)
        cycles_by_name = {cycle.picking_name: cycle for cycle in cycles}
        first_cycle = cycles_by_name["WCGT/IN/01062"]
        second_cycle = cycles_by_name["WCGT/IN/01063"]
        self.assertEqual(first_cycle.correction_stj_refs, ["STJ/2026/03/0811"])
        self.assertEqual(second_cycle.correction_stj_refs, [])
        self.assertIn("STJ/2026/03/0811", [row["kode_transaksi"] for row in first_cycle.raw_lines])
        self.assertNotIn("STJ/2026/03/0811", [row["kode_transaksi"] for row in second_cycle.raw_lines])
        self.assertNotEqual(first_cycle.cycle_status, "problem")
        self.assertEqual(second_cycle.cycle_status, "problem")

    def test_attach_pcb_adjustment_audit_rows_matches_rac_exact_item_without_changing_cycle_balances(self) -> None:
        service = SvlDashboardServiceAsync(rpc=_FakeDashboardRpc(), logger=logging.getLogger("test.dashboard"))
        trace, all_ledger_rows, account_info_map, move_info_map, product_info_map = _build_case1_purchase_cycle_inputs()
        trace["pcb_correction_move_ids"] = []
        all_ledger_rows = [row for row in all_ledger_rows if row.get("move_id", [0])[0] != 8811]

        cycles = service._build_purchase_cycles(
            trace=trace,
            all_ledger_rows=all_ledger_rows,
            account_info_map=account_info_map,
            move_info_map=move_info_map,
            product_info_map=product_info_map,
            warnings=[],
            problem_codes=frozenset({"2103006", "1108099"}),
            info_codes=frozenset({"1105003", "5101010"}),
        )

        self.assertEqual(len(cycles), 1)
        cycle = cycles[0]
        base_cycle_status = cycle.cycle_status
        base_cycle_case = service._classify_pcb_cycle_case(
            account_rows=cycle.account_rows,
            raw_lines=cycle.raw_lines,
            bill_refs=cycle.bill_refs,
        )
        base_account_signature = [
            (row.code, row.debit, row.credit, row.net_balance, row.status)
            for row in cycle.account_rows
        ]
        base_item_signature = {
            item_row.product_id: [
                (row.code, row.debit, row.credit, row.net_balance, row.status)
                for row in item_row.account_rows
            ]
            for item_row in cycle.item_rows
        }
        base_case1_keys = [row.source_key for row in cycle.case1_link_rows]
        base_case2_keys = [row.row_key for row in cycle.case2_repair_rows]

        service._attach_pcb_adjustment_audit_rows(
            cycles=cycles,
            adjustment_moves=[
                _build_pcb_adjustment_move(
                    move_id=9901,
                    move_name="RAC/2026/01/0026",
                    move_ref="Adjustment clearing from standard price",
                    purchase_line_id=3001,
                )
            ],
            trace=trace,
            move_info_map=move_info_map,
            product_info_map=product_info_map,
            account_info_map=account_info_map,
        )

        self.assertEqual(cycle.cycle_status, base_cycle_status)
        self.assertEqual(
            service._classify_pcb_cycle_case(
                account_rows=cycle.account_rows,
                raw_lines=cycle.raw_lines,
                bill_refs=cycle.bill_refs,
            ),
            base_cycle_case,
        )
        self.assertEqual(
            [(row.code, row.debit, row.credit, row.net_balance, row.status) for row in cycle.account_rows],
            base_account_signature,
        )
        self.assertEqual(
            {
                item_row.product_id: [
                    (row.code, row.debit, row.credit, row.net_balance, row.status)
                    for row in item_row.account_rows
                ]
                for item_row in cycle.item_rows
            },
            base_item_signature,
        )
        self.assertEqual([row.source_key for row in cycle.case1_link_rows], base_case1_keys)
        self.assertEqual([row.row_key for row in cycle.case2_repair_rows], base_case2_keys)
        self.assertEqual(len(cycle.adjustment_audit_rows), 1)
        self.assertEqual(cycle.adjustment_warning_text, "Warning: Clearing adjustment -> RAC/2026/01/0026")
        self.assertEqual(cycle.adjustment_audit_rows[0].matched_basis, "exact.purchase_line_id")
        self.assertFalse(cycle.adjustment_audit_rows[0].ambiguous)
        self.assertEqual(len(cycle.item_rows), 1)
        self.assertEqual(len(cycle.item_rows[0].adjustment_audit_rows), 1)
        self.assertEqual(cycle.item_rows[0].adjustment_audit_rows[0].move_name, "RAC/2026/01/0026")

    def test_attach_pcb_adjustment_audit_rows_keeps_ambiguous_match_at_cycle_level_only(self) -> None:
        service = SvlDashboardServiceAsync(rpc=_FakeDashboardRpc(), logger=logging.getLogger("test.dashboard"))
        trace, all_ledger_rows, account_info_map, move_info_map, product_info_map = _build_case1_purchase_cycle_inputs(
            include_second_cycle=True
        )
        trace["pcb_correction_move_ids"] = []
        all_ledger_rows = [row for row in all_ledger_rows if row.get("move_id", [0])[0] != 8811]

        cycles = service._build_purchase_cycles(
            trace=trace,
            all_ledger_rows=all_ledger_rows,
            account_info_map=account_info_map,
            move_info_map=move_info_map,
            product_info_map=product_info_map,
            warnings=[],
            problem_codes=frozenset({"2103006", "1108099"}),
            info_codes=frozenset({"1105003", "5101010"}),
        )

        service._attach_pcb_adjustment_audit_rows(
            cycles=cycles,
            adjustment_moves=[
                _build_pcb_adjustment_move(
                    move_id=9902,
                    move_name="RAC/2026/01/0027",
                    move_ref="Ambiguous adjustment across same SKU",
                    product_id=101,
                    purchase_line_id=0,
                    stock_move_id=0,
                    bill_move_id=0,
                    picking_id=0,
                )
            ],
            trace=trace,
            move_info_map=move_info_map,
            product_info_map=product_info_map,
            account_info_map=account_info_map,
        )

        self.assertEqual(len(cycles), 2)
        for cycle in cycles:
            self.assertEqual(len(cycle.adjustment_audit_rows), 1)
            self.assertTrue(cycle.adjustment_audit_rows[0].ambiguous)
            self.assertIn("Warning: Clearing adjustment -> RAC/2026/01/0027", cycle.adjustment_warning_text)
            self.assertIn("match ambiguous", cycle.adjustment_warning_text)
            self.assertTrue(all(not item_row.adjustment_audit_rows for item_row in cycle.item_rows))

    def test_attach_pcb_adjustment_audit_rows_matches_exact_relation_without_rac_prefix(self) -> None:
        service = SvlDashboardServiceAsync(rpc=_FakeDashboardRpc(), logger=logging.getLogger("test.dashboard"))
        trace, all_ledger_rows, account_info_map, move_info_map, product_info_map = _build_case1_purchase_cycle_inputs()
        trace["pcb_correction_move_ids"] = []
        all_ledger_rows = [row for row in all_ledger_rows if row.get("move_id", [0])[0] != 8811]

        cycles = service._build_purchase_cycles(
            trace=trace,
            all_ledger_rows=all_ledger_rows,
            account_info_map=account_info_map,
            move_info_map=move_info_map,
            product_info_map=product_info_map,
            warnings=[],
            problem_codes=frozenset({"2103006", "1108099"}),
            info_codes=frozenset({"1105003", "5101010"}),
        )

        service._attach_pcb_adjustment_audit_rows(
            cycles=cycles,
            adjustment_moves=[
                _build_pcb_adjustment_move(
                    move_id=9903,
                    move_name="MISC/2026/01/0008",
                    move_ref="Clearing reclass after standard price update",
                    purchase_line_id=0,
                    stock_move_id=8401,
                )
            ],
            trace=trace,
            move_info_map=move_info_map,
            product_info_map=product_info_map,
            account_info_map=account_info_map,
        )

        self.assertEqual(len(cycles), 1)
        self.assertEqual(len(cycles[0].adjustment_audit_rows), 1)
        self.assertEqual(cycles[0].adjustment_audit_rows[0].move_name, "MISC/2026/01/0008")
        self.assertEqual(cycles[0].adjustment_audit_rows[0].matched_basis, "exact.stock_move_id")
        self.assertFalse(cycles[0].adjustment_audit_rows[0].ambiguous)
        self.assertEqual(len(cycles[0].item_rows[0].adjustment_audit_rows), 1)

    def test_attach_pcb_adjustment_audit_rows_resolves_origin_move_from_explicit_source_reference(self) -> None:
        service = SvlDashboardServiceAsync(rpc=_FakeDashboardRpc(), logger=logging.getLogger("test.dashboard"))
        trace, all_ledger_rows, account_info_map, move_info_map, product_info_map = _build_case1_purchase_cycle_inputs()
        trace["pcb_correction_move_ids"] = []
        all_ledger_rows = [row for row in all_ledger_rows if row.get("move_id", [0])[0] != 8811]
        move_info_map[8801] = {
            "id": 8801,
            "name": "STJ/2026/01/0690",
            "move_type": "entry",
            "ref": "Original source STJ",
        }

        cycles = service._build_purchase_cycles(
            trace=trace,
            all_ledger_rows=all_ledger_rows,
            account_info_map=account_info_map,
            move_info_map=move_info_map,
            product_info_map=product_info_map,
            warnings=[],
            problem_codes=frozenset({"2103006", "1108099"}),
            info_codes=frozenset({"1105003", "5101010"}),
        )

        service._attach_pcb_adjustment_audit_rows(
            cycles=cycles,
            adjustment_moves=[
                _build_pcb_adjustment_move(
                    move_id=9904,
                    move_name="RAC/2026/01/0030",
                    move_ref="Reclass from STJ/2026/01/0690",
                    product_id=0,
                    purchase_line_id=0,
                    stock_move_id=0,
                    source_line_name="Reclass source STJ/2026/01/0690",
                    origin_moves_by_name={
                        "STJ/2026/01/0690": {
                            "move_id": 8801,
                            "move_name": "STJ/2026/01/0690",
                            "lines": [
                                {
                                    "id": 88011,
                                    "move_id": [8801, "STJ/2026/01/0690"],
                                    "product_id": [101, "ANGGUR HITAM / BLACK GRAPES"],
                                    "purchase_line_id": [3001, "PO Line 3001"],
                                    "stock_move_id": [8401, "MOVE/8401"],
                                    "picking_id": [7001, "PICK/7001"],
                                    "bill_move_id": [8001, "BILL/2026/01/0166"],
                                }
                            ],
                        }
                    },
                )
            ],
            trace=trace,
            move_info_map=move_info_map,
            product_info_map=product_info_map,
            account_info_map=account_info_map,
        )

        self.assertEqual(len(cycles), 1)
        audit_row = cycles[0].adjustment_audit_rows[0]
        self.assertEqual(audit_row.origin_move_name, "STJ/2026/01/0690")
        self.assertEqual(audit_row.origin_basis, "explicit.line_name")
        self.assertEqual(audit_row.product_id, 101)
        self.assertEqual(audit_row.origin_purchase_line_id, 3001)
        self.assertFalse(audit_row.ambiguous)
        self.assertEqual(len(cycles[0].item_rows[0].adjustment_audit_rows), 1)

    def test_classify_pcb_cycle_case_case3_falls_back_when_no_eligible_item(self) -> None:
        service = SvlDashboardServiceAsync(rpc=_FakeDashboardRpc(), logger=logging.getLogger("test.dashboard"))

        self.assertEqual(
            service._classify_pcb_cycle_case(
                account_rows=[
                    SvlDashboardCycleAccountRow(
                        account_id=301,
                        code="2103006",
                        name="Hutang Suspend",
                        account_type="liability_current",
                        account_group="liability",
                        debit=0.0,
                        credit=120.0,
                        net_balance=-120.0,
                        status="problem",
                    ),
                    SvlDashboardCycleAccountRow(
                        account_id=302,
                        code="1108099",
                        name="Clearing",
                        account_type="asset_current",
                        account_group="asset",
                        debit=70.0,
                        credit=0.0,
                        net_balance=70.0,
                        status="problem",
                    ),
                ],
                raw_lines=[],
                bill_refs=["BILL/2026/0003"],
                item_rows=[
                    SvlDashboardCycleItemRow(
                        product_id=901,
                        product_name="Produk No Gate",
                        default_code="SKU-901",
                        has_item_bill=True,
                        has_item_stj=False,
                        verified_audit_clearing_amount=0.0,
                        eligible_case34=False,
                    )
                ],
            ),
            "case_lainnya",
        )

    def test_verify_pcb_case34_adjustment_rows_promotes_unique_amount_source_match(self) -> None:
        service = SvlDashboardServiceAsync(rpc=_FakeDashboardRpc(), logger=logging.getLogger("test.dashboard"))
        cycle = SvlDashboardPurchaseCycle(
            picking_id=7001,
            picking_name="LHPK/IN/7001",
            gr_date="2026-03-18",
            partner_name="Vendor Alpha",
            cycle_status="problem",
            account_rows=[],
            item_rows=[
                SvlDashboardCycleItemRow(
                    product_id=901,
                    product_name="Produk Unique Match",
                    default_code="SKU-901",
                    has_item_bill=True,
                    bill_move_ids=[8201],
                    bill_refs=["BILL/2026/0001"],
                    account_rows=[
                        SvlDashboardCycleAccountRow(
                            account_id=301,
                            code="2103006",
                            name="Hutang Suspend",
                            account_type="liability_current",
                            account_group="liability",
                            debit=120.0,
                            credit=0.0,
                            net_balance=120.0,
                            status="problem",
                        ),
                        SvlDashboardCycleAccountRow(
                            account_id=303,
                            code="5101010",
                            name="Selisih HPP / COGS Variance",
                            account_type="expense_direct_cost",
                            account_group="expense",
                            debit=0.0,
                            credit=120.0,
                            net_balance=-120.0,
                            status="info",
                        ),
                    ],
                ),
                SvlDashboardCycleItemRow(
                    product_id=902,
                    product_name="Produk Other",
                    default_code="SKU-902",
                    has_item_bill=True,
                    bill_move_ids=[8201],
                    bill_refs=["BILL/2026/0001"],
                    account_rows=[
                        SvlDashboardCycleAccountRow(
                            account_id=304,
                            code="5101010",
                            name="Selisih HPP / COGS Variance",
                            account_type="expense_direct_cost",
                            account_group="expense",
                            debit=0.0,
                            credit=80.0,
                            net_balance=-80.0,
                            status="info",
                        ),
                    ],
                ),
            ],
            adjustment_audit_rows=[
                SvlDashboardPcbAdjustmentAuditRow(
                    move_id=9950,
                    move_name="RAC/2026/01/0050",
                    move_date="2026-03-27",
                    move_ref="No relation, unique amount/source fallback",
                    product_id=0,
                    source_account_code="5101010",
                    source_account_name="Selisih HPP / COGS Variance",
                    clearing_account_code="1108099",
                    repair_clearing_amount=120.0,
                    matched_basis="fallback.bill_picking_token",
                    ambiguous=False,
                )
            ],
        )

        service._verify_pcb_case34_adjustment_rows_for_cycle(
            cycle=cycle,
            trace={"purchase_line_product_map": {}, "stock_move_rows_by_id": {}},
            product_info_map={901: {"default_code": "SKU-901", "name": "Produk Unique Match"}},
        )

        self.assertEqual(len(cycle.item_rows[0].adjustment_audit_rows), 1)
        self.assertTrue(cycle.adjustment_audit_rows[0].verified_for_case34)
        self.assertEqual(cycle.adjustment_audit_rows[0].origin_basis, "fallback.unique_amount_source_account")
        self.assertEqual(cycle.item_rows[0].verified_audit_clearing_amount, 120.0)
        self.assertTrue(cycle.item_rows[0].eligible_case34)
        self.assertFalse(cycle.item_rows[1].adjustment_audit_rows)

    def test_verify_pcb_case34_adjustment_rows_keeps_ambiguous_unique_match_cycle_only(self) -> None:
        service = SvlDashboardServiceAsync(rpc=_FakeDashboardRpc(), logger=logging.getLogger("test.dashboard"))
        cycle = SvlDashboardPurchaseCycle(
            picking_id=7001,
            picking_name="LHPK/IN/7001",
            gr_date="2026-03-18",
            partner_name="Vendor Alpha",
            cycle_status="problem",
            account_rows=[],
            item_rows=[
                SvlDashboardCycleItemRow(
                    product_id=901,
                    product_name="Produk A",
                    default_code="SKU-901",
                    has_item_bill=True,
                    account_rows=[
                        SvlDashboardCycleAccountRow(
                            account_id=303,
                            code="5101010",
                            name="Selisih HPP / COGS Variance",
                            account_type="expense_direct_cost",
                            account_group="expense",
                            debit=0.0,
                            credit=120.0,
                            net_balance=-120.0,
                            status="info",
                        )
                    ],
                ),
                SvlDashboardCycleItemRow(
                    product_id=902,
                    product_name="Produk B",
                    default_code="SKU-902",
                    has_item_bill=True,
                    account_rows=[
                        SvlDashboardCycleAccountRow(
                            account_id=304,
                            code="5101010",
                            name="Selisih HPP / COGS Variance",
                            account_type="expense_direct_cost",
                            account_group="expense",
                            debit=0.0,
                            credit=120.0,
                            net_balance=-120.0,
                            status="info",
                        )
                    ],
                ),
            ],
            adjustment_audit_rows=[
                SvlDashboardPcbAdjustmentAuditRow(
                    move_id=9951,
                    move_name="RAC/2026/01/0051",
                    move_date="2026-03-27",
                    move_ref="Ambiguous unique amount/source fallback",
                    source_account_code="5101010",
                    source_account_name="Selisih HPP / COGS Variance",
                    clearing_account_code="1108099",
                    repair_clearing_amount=120.0,
                    matched_basis="fallback.bill_picking_token",
                    ambiguous=False,
                )
            ],
        )

        service._verify_pcb_case34_adjustment_rows_for_cycle(
            cycle=cycle,
            trace={"purchase_line_product_map": {}, "stock_move_rows_by_id": {}},
            product_info_map={},
        )

        self.assertFalse(cycle.adjustment_audit_rows[0].verified_for_case34)
        self.assertEqual(cycle.item_rows[0].adjustment_audit_rows, [])
        self.assertEqual(cycle.item_rows[1].adjustment_audit_rows, [])

    def test_augment_pcb_case34_external_clearing_resolves_explicit_origin_without_cycle_audit_attachment(self) -> None:
        service = SvlDashboardServiceAsync(rpc=_FakeDashboardRpc(), logger=logging.getLogger("test.dashboard"))
        cycle = SvlDashboardPurchaseCycle(
            picking_id=7004,
            picking_name="CBG/IN/00812",
            gr_date="2026-03-26",
            partner_name="Vendor Alpha",
            cycle_status="problem",
            account_rows=[
                SvlDashboardCycleAccountRow(
                    account_id=301,
                    code="2103006",
                    name="Hutang Suspend",
                    account_type="liability_current",
                    account_group="liability",
                    debit=120.0,
                    credit=0.0,
                    net_balance=120.0,
                    status="problem",
                )
            ],
            bill_refs=["BILL/2026/0010"],
            item_rows=[
                SvlDashboardCycleItemRow(
                    product_id=901,
                    product_name="Produk External Origin",
                    default_code="SKU-901",
                    has_item_bill=True,
                    bill_move_ids=[8210],
                    bill_refs=["BILL/2026/0010"],
                    purchase_line_ids=[8310],
                    has_item_stj=False,
                    account_rows=[
                        SvlDashboardCycleAccountRow(
                            account_id=301,
                            code="2103006",
                            name="Hutang Suspend",
                            account_type="liability_current",
                            account_group="liability",
                            debit=120.0,
                            credit=0.0,
                            net_balance=120.0,
                            status="problem",
                        ),
                        SvlDashboardCycleAccountRow(
                            account_id=303,
                            code="5101010",
                            name="Selisih HPP / COGS Variance",
                            account_type="expense_direct_cost",
                            account_group="expense",
                            debit=0.0,
                            credit=120.0,
                            net_balance=-120.0,
                            status="info",
                        ),
                    ],
                )
            ],
            adjustment_audit_rows=[],
        )

        service._augment_pcb_case34_external_clearing_for_cycles(
            cycles=[cycle],
            adjustment_moves=[
                {
                    "move_id": 9955,
                    "move_name": "RAC/2026/01/0026",
                    "move_ref": "Transfer from STJ/2026/01/0690",
                    "move_date": "2026-03-26",
                    "origin_moves_by_name": {
                        "STJ/2026/01/0690": {
                            "move_id": 8801,
                            "move_name": "STJ/2026/01/0690",
                            "lines": [
                                {
                                    "id": 70001,
                                    "purchase_line_id": [8310, "PO Line 10"],
                                    "product_id": [901, "Produk External Origin"],
                                }
                            ],
                        }
                    },
                    "lines": [
                        {"id": 60001, "account_id": [401, "Clearing"], "balance": 120.0},
                        {
                            "id": 60002,
                            "account_id": [402, "Selisih HPP"],
                            "name": "Transfer from STJ/2026/01/0690",
                            "balance": -120.0,
                        },
                    ],
                }
            ],
            trace={"purchase_line_product_map": {8310: 901}, "stock_move_rows_by_id": {}},
            account_info_map={
                401: {"code": "1108099", "name": "Clearing", "account_type": "asset_current"},
                402: {"code": "5101010", "name": "Selisih HPP / COGS Variance", "account_type": "expense_direct_cost"},
            },
            product_info_map={
                901: {
                    "default_code": "SKU-901",
                    "name": "Produk External Origin",
                    "expense_account_code": "5101003",
                    "expense_account_name": "HPP Target",
                    "valuation_account_code": "1105003",
                    "valuation_account_name": "Persediaan",
                }
            },
        )

        item_row = cycle.item_rows[0]
        self.assertEqual(item_row.external_clearing_amount, 120.0)
        self.assertTrue(item_row.external_clearing_verified)
        self.assertTrue(item_row.eligible_case34)
        self.assertIn("RAC/2026/01/0026 <- STJ/2026/01/0690", item_row.external_clearing_refs)
        self.assertEqual(
            service._classify_pcb_cycle_case(
                account_rows=cycle.account_rows,
                raw_lines=[],
                bill_refs=cycle.bill_refs,
                item_rows=cycle.item_rows,
            ),
            "case4",
        )

    def test_augment_pcb_case34_external_clearing_unique_amount_source_without_cycle_audit_attachment(self) -> None:
        service = SvlDashboardServiceAsync(rpc=_FakeDashboardRpc(), logger=logging.getLogger("test.dashboard"))
        winning_cycle = SvlDashboardPurchaseCycle(
            picking_id=7005,
            picking_name="CBG/IN/00821",
            gr_date="2026-03-26",
            partner_name="Vendor Alpha",
            cycle_status="problem",
            account_rows=[],
            bill_refs=["BILL/2026/0011"],
            item_rows=[
                SvlDashboardCycleItemRow(
                    product_id=911,
                    product_name="Produk Unique",
                    default_code="SKU-911",
                    has_item_bill=True,
                    account_rows=[
                        SvlDashboardCycleAccountRow(
                            account_id=301,
                            code="2103006",
                            name="Hutang Suspend",
                            account_type="liability_current",
                            account_group="liability",
                            debit=120.0,
                            credit=0.0,
                            net_balance=120.0,
                            status="problem",
                        ),
                        SvlDashboardCycleAccountRow(
                            account_id=303,
                            code="5101010",
                            name="Selisih HPP / COGS Variance",
                            account_type="expense_direct_cost",
                            account_group="expense",
                            debit=0.0,
                            credit=120.0,
                            net_balance=-120.0,
                            status="info",
                        ),
                    ],
                )
            ],
        )
        other_cycle = SvlDashboardPurchaseCycle(
            picking_id=7006,
            picking_name="CBG/IN/00822",
            gr_date="2026-03-26",
            partner_name="Vendor Alpha",
            cycle_status="problem",
            account_rows=[],
            bill_refs=["BILL/2026/0012"],
            item_rows=[
                SvlDashboardCycleItemRow(
                    product_id=912,
                    product_name="Produk Other",
                    default_code="SKU-912",
                    has_item_bill=True,
                    account_rows=[
                        SvlDashboardCycleAccountRow(
                            account_id=301,
                            code="2103006",
                            name="Hutang Suspend",
                            account_type="liability_current",
                            account_group="liability",
                            debit=80.0,
                            credit=0.0,
                            net_balance=80.0,
                            status="problem",
                        ),
                        SvlDashboardCycleAccountRow(
                            account_id=303,
                            code="5101010",
                            name="Selisih HPP / COGS Variance",
                            account_type="expense_direct_cost",
                            account_group="expense",
                            debit=0.0,
                            credit=80.0,
                            net_balance=-80.0,
                            status="info",
                        ),
                    ],
                )
            ],
        )

        service._augment_pcb_case34_external_clearing_for_cycles(
            cycles=[winning_cycle, other_cycle],
            adjustment_moves=[
                {
                    "move_id": 9956,
                    "move_name": "RAC/2026/01/0027",
                    "move_ref": "Unique amount source fallback",
                    "move_date": "2026-03-26",
                    "lines": [
                        {"id": 60011, "account_id": [401, "Clearing"], "balance": 120.0},
                        {"id": 60012, "account_id": [402, "Selisih HPP"], "balance": -120.0},
                    ],
                }
            ],
            trace={"purchase_line_product_map": {}, "stock_move_rows_by_id": {}},
            account_info_map={
                401: {"code": "1108099", "name": "Clearing", "account_type": "asset_current"},
                402: {"code": "5101010", "name": "Selisih HPP / COGS Variance", "account_type": "expense_direct_cost"},
            },
            product_info_map={},
        )

        self.assertEqual(winning_cycle.item_rows[0].external_clearing_amount, 120.0)
        self.assertTrue(winning_cycle.item_rows[0].external_clearing_verified)
        self.assertEqual(other_cycle.item_rows[0].external_clearing_amount, 0.0)
        self.assertFalse(other_cycle.item_rows[0].external_clearing_verified)

    def test_augment_pcb_case34_external_clearing_keeps_ambiguous_unique_match_unverified(self) -> None:
        service = SvlDashboardServiceAsync(rpc=_FakeDashboardRpc(), logger=logging.getLogger("test.dashboard"))
        cycle_a = SvlDashboardPurchaseCycle(
            picking_id=7007,
            picking_name="CBG/IN/00823",
            gr_date="2026-03-26",
            partner_name="Vendor Alpha",
            cycle_status="problem",
            account_rows=[],
            bill_refs=["BILL/2026/0013"],
            item_rows=[
                SvlDashboardCycleItemRow(
                    product_id=913,
                    product_name="Produk A",
                    default_code="SKU-913",
                    has_item_bill=True,
                    account_rows=[
                        SvlDashboardCycleAccountRow(
                            account_id=301,
                            code="2103006",
                            name="Hutang Suspend",
                            account_type="liability_current",
                            account_group="liability",
                            debit=120.0,
                            credit=0.0,
                            net_balance=120.0,
                            status="problem",
                        ),
                        SvlDashboardCycleAccountRow(
                            account_id=303,
                            code="5101010",
                            name="Selisih HPP / COGS Variance",
                            account_type="expense_direct_cost",
                            account_group="expense",
                            debit=0.0,
                            credit=120.0,
                            net_balance=-120.0,
                            status="info",
                        ),
                    ],
                )
            ],
        )
        cycle_b = SvlDashboardPurchaseCycle(
            picking_id=7008,
            picking_name="CBG/IN/00824",
            gr_date="2026-03-26",
            partner_name="Vendor Alpha",
            cycle_status="problem",
            account_rows=[],
            bill_refs=["BILL/2026/0014"],
            item_rows=[
                SvlDashboardCycleItemRow(
                    product_id=914,
                    product_name="Produk B",
                    default_code="SKU-914",
                    has_item_bill=True,
                    account_rows=[
                        SvlDashboardCycleAccountRow(
                            account_id=301,
                            code="2103006",
                            name="Hutang Suspend",
                            account_type="liability_current",
                            account_group="liability",
                            debit=120.0,
                            credit=0.0,
                            net_balance=120.0,
                            status="problem",
                        ),
                        SvlDashboardCycleAccountRow(
                            account_id=303,
                            code="5101010",
                            name="Selisih HPP / COGS Variance",
                            account_type="expense_direct_cost",
                            account_group="expense",
                            debit=0.0,
                            credit=120.0,
                            net_balance=-120.0,
                            status="info",
                        ),
                    ],
                )
            ],
        )

        service._augment_pcb_case34_external_clearing_for_cycles(
            cycles=[cycle_a, cycle_b],
            adjustment_moves=[
                {
                    "move_id": 9957,
                    "move_name": "RAC/2026/01/0028",
                    "move_ref": "Ambiguous unique amount source fallback",
                    "move_date": "2026-03-26",
                    "lines": [
                        {"id": 60021, "account_id": [401, "Clearing"], "balance": 120.0},
                        {"id": 60022, "account_id": [402, "Selisih HPP"], "balance": -120.0},
                    ],
                }
            ],
            trace={"purchase_line_product_map": {}, "stock_move_rows_by_id": {}},
            account_info_map={
                401: {"code": "1108099", "name": "Clearing", "account_type": "asset_current"},
                402: {"code": "5101010", "name": "Selisih HPP / COGS Variance", "account_type": "expense_direct_cost"},
            },
            product_info_map={},
        )

        self.assertEqual(cycle_a.item_rows[0].external_clearing_amount, 0.0)
        self.assertEqual(cycle_b.item_rows[0].external_clearing_amount, 0.0)
        self.assertFalse(cycle_a.item_rows[0].eligible_case34)
        self.assertFalse(cycle_b.item_rows[0].eligible_case34)

    def test_build_purchase_cycles_product_fallback_for_no_po_picking_keeps_same_partner_only(self) -> None:
        service = SvlDashboardServiceAsync(rpc=_FakeDashboardRpc(), logger=logging.getLogger("test.dashboard"))
        trace, all_ledger_rows, account_info_map, move_info_map, product_info_map = _build_case1_purchase_cycle_inputs()
        trace["po_id_by_picking"] = {}
        trace["purchase_order_rows_by_id"] = {}
        trace["bill_rows_by_id"][8001]["partner_id"] = [77, "Vendor Alpha"]
        trace["bill_rows_by_id"][8002] = {
            "id": 8002,
            "name": "BILL/2025/11/0180",
            "invoice_date": "2025-11-25",
            "date": "2025-11-25",
            "invoice_origin": "PO/OTHER/2025/11/00315",
            "payment_state": "paid",
            "partner_id": [88, "Vendor Beta"],
        }
        trace["bill_ids_by_product"][101] = {8001, 8002}
        all_ledger_rows.append(
            {
                "id": 8201,
                "move_id": [8002, "BILL/2025/11/0180"],
                "account_id": [301, "Hutang Suspend"],
                "product_id": [101, "ANGGUR HITAM / BLACK GRAPES"],
                "date": "2025-11-25",
                "name": "Bill vendor beta",
                "debit": 999999.0,
                "credit": 0.0,
                "balance": 999999.0,
            }
        )
        move_info_map[8002] = {
            "id": 8002,
            "name": "BILL/2025/11/0180",
            "move_type": "in_invoice",
            "partner_id": [88, "Vendor Beta"],
            "ref": "Vendor Bill Beta",
        }

        cycles = service._build_purchase_cycles(
            trace=trace,
            all_ledger_rows=all_ledger_rows,
            account_info_map=account_info_map,
            move_info_map=move_info_map,
            product_info_map=product_info_map,
            warnings=[],
            problem_codes=frozenset({"2103006", "1108099"}),
            info_codes=frozenset({"1105003", "5101010"}),
        )

        self.assertEqual(len(cycles), 1)
        self.assertEqual(cycles[0].bill_refs, ["BILL/2026/02/0114"])

    def test_build_purchase_cycles_product_fallback_for_no_po_picking_skips_when_partner_unknown(self) -> None:
        service = SvlDashboardServiceAsync(rpc=_FakeDashboardRpc(), logger=logging.getLogger("test.dashboard"))
        trace, all_ledger_rows, account_info_map, move_info_map, product_info_map = _build_case1_purchase_cycle_inputs()
        trace["po_id_by_picking"] = {}
        trace["purchase_order_rows_by_id"] = {}
        trace["picking_rows_by_id"][7001]["partner_id"] = False
        trace["bill_rows_by_id"][8001]["partner_id"] = [77, "Vendor Alpha"]

        cycles = service._build_purchase_cycles(
            trace=trace,
            all_ledger_rows=all_ledger_rows,
            account_info_map=account_info_map,
            move_info_map=move_info_map,
            product_info_map=product_info_map,
            warnings=[],
            problem_codes=frozenset({"2103006", "1108099"}),
            info_codes=frozenset({"1105003", "5101010"}),
        )

        self.assertEqual(len(cycles), 1)
        self.assertEqual(cycles[0].bill_refs, [])
        self.assertEqual(cycles[0].payment_refs, [])
        self.assertEqual(cycles[0].bank_refs, [])

    def test_build_purchase_cycles_direct_bk_merges_same_vendor_pickings(self) -> None:
        service = SvlDashboardServiceAsync(rpc=_FakeDashboardRpc(), logger=logging.getLogger("test.dashboard"))
        trace, all_ledger_rows, account_info_map, move_info_map, product_info_map = _build_case1_purchase_cycle_inputs(
            include_second_cycle=True
        )
        trace["bill_rows_by_id"][8001]["partner_id"] = [77, "Vendor Alpha"]
        trace["bill_rows_by_id"][8002]["partner_id"] = [77, "Vendor Alpha"]
        trace["direct_bank_move_ids_by_bill"] = {8001: [9901], 8002: [9901]}
        trace["direct_bills_by_bank_move_id"] = {9901: [8001, 8002]}
        all_ledger_rows.extend(
            [
                {
                    "id": 99011,
                    "move_id": [9901, "BK017/2026/00999"],
                    "account_id": [501, "BCA Bogor"],
                    "date": "2026-03-20",
                    "name": "Direct bank payment",
                    "debit": 0.0,
                    "credit": 340000.0,
                    "balance": -340000.0,
                },
                {
                    "id": 99012,
                    "move_id": [9901, "BK017/2026/00999"],
                    "account_id": [502, "Hutang Vendor"],
                    "date": "2026-03-20",
                    "name": "Direct bank payment payable",
                    "debit": 340000.0,
                    "credit": 0.0,
                    "balance": 340000.0,
                },
            ]
        )
        account_info_map[501] = {"code": "1101060", "name": "BCA Bogor", "account_type": "asset_cash"}
        account_info_map[502] = {"code": "2101002", "name": "Hutang Vendor", "account_type": "liability_payable"}
        move_info_map[9901] = {
            "id": 9901,
            "name": "BK017/2026/00999",
            "move_type": "entry",
            "ref": "Direct BK shared for same vendor",
        }

        cycles = service._build_purchase_cycles(
            trace=trace,
            all_ledger_rows=all_ledger_rows,
            account_info_map=account_info_map,
            move_info_map=move_info_map,
            product_info_map=product_info_map,
            warnings=[],
            problem_codes=frozenset({"2103006", "1108099"}),
            info_codes=frozenset({"1105003", "5101010"}),
        )

        self.assertEqual(len(cycles), 1)
        self.assertEqual(set(cycles[0].picking_names), {"WCGT/IN/01062", "WCGT/IN/01063"})
        self.assertIn("BK017/2026/00999", cycles[0].bank_refs)

    def test_build_purchase_cycles_direct_bk_does_not_merge_different_vendors(self) -> None:
        service = SvlDashboardServiceAsync(rpc=_FakeDashboardRpc(), logger=logging.getLogger("test.dashboard"))
        trace, all_ledger_rows, account_info_map, move_info_map, product_info_map = _build_case1_purchase_cycle_inputs(
            include_second_cycle=True
        )
        trace["picking_rows_by_id"][7002]["partner_id"] = [88, "Vendor Beta"]
        trace["purchase_order_rows_by_id"][5002]["partner_id"] = [88, "Vendor Beta"]
        trace["bill_rows_by_id"][8001]["partner_id"] = [77, "Vendor Alpha"]
        trace["bill_rows_by_id"][8002]["partner_id"] = [88, "Vendor Beta"]
        move_info_map[8002]["partner_id"] = [88, "Vendor Beta"]
        trace["direct_bank_move_ids_by_bill"] = {8001: [9901], 8002: [9901]}
        trace["direct_bills_by_bank_move_id"] = {9901: [8001, 8002]}
        all_ledger_rows.extend(
            [
                {
                    "id": 99011,
                    "move_id": [9901, "BK017/2026/00999"],
                    "account_id": [501, "BCA Bogor"],
                    "date": "2026-03-20",
                    "name": "Direct bank payment",
                    "debit": 0.0,
                    "credit": 340000.0,
                    "balance": -340000.0,
                },
                {
                    "id": 99012,
                    "move_id": [9901, "BK017/2026/00999"],
                    "account_id": [502, "Hutang Vendor"],
                    "date": "2026-03-20",
                    "name": "Direct bank payment payable",
                    "debit": 340000.0,
                    "credit": 0.0,
                    "balance": 340000.0,
                },
            ]
        )
        account_info_map[501] = {"code": "1101060", "name": "BCA Bogor", "account_type": "asset_cash"}
        account_info_map[502] = {"code": "2101002", "name": "Hutang Vendor", "account_type": "liability_payable"}
        move_info_map[9901] = {
            "id": 9901,
            "name": "BK017/2026/00999",
            "move_type": "entry",
            "ref": "Direct BK shared across vendors",
        }

        cycles = service._build_purchase_cycles(
            trace=trace,
            all_ledger_rows=all_ledger_rows,
            account_info_map=account_info_map,
            move_info_map=move_info_map,
            product_info_map=product_info_map,
            warnings=[],
            problem_codes=frozenset({"2103006", "1108099"}),
            info_codes=frozenset({"1105003", "5101010"}),
        )

        self.assertEqual(len(cycles), 2)
        cycles_by_name = {cycle.picking_name: cycle for cycle in cycles}
        self.assertIn("BK017/2026/00999", cycles_by_name["WCGT/IN/01062"].bank_refs)
        self.assertIn("BK017/2026/00999", cycles_by_name["WCGT/IN/01063"].bank_refs)

    def test_matching_compatible_seed_bills_blocks_other_vendor_and_keeps_same_vendor(self) -> None:
        compatible = SvlDashboardServiceAsync._matching_compatible_seed_bills(
            {8001, 8002, 8003},
            candidate_partner_key="id:77",
            bill_partner_key_by_move_id={
                8001: "id:77",
                8002: "id:88",
                8003: "id:77",
            },
        )

        self.assertEqual(compatible, {8001, 8003})

    def test_classify_matching_entry_candidate_treats_misc_entry_without_payment_accounts_as_other_entry(self) -> None:
        kind = SvlDashboardServiceAsync._classify_matching_entry_candidate(
            move_row={
                "id": 9901,
                "move_type": "entry",
                "name": "MISC/2025/12/0009",
            },
            aml_rows=[
                {
                    "id": 7001,
                    "move_id": [9901, "MISC/2025/12/0009"],
                    "account_id": [301, "Hutang Suspend"],
                }
            ],
            account_info_map={
                301: {"code": "2103006", "name": "Hutang Suspend", "account_type": "liability_current"},
            },
        )

        self.assertEqual(kind, "other_entry")

    def test_classify_matching_entry_candidate_treats_pbk_prefix_as_payment_bank(self) -> None:
        kind = SvlDashboardServiceAsync._classify_matching_entry_candidate(
            move_row={
                "id": 9902,
                "move_type": "entry",
                "name": "PBK017/2025/00342",
            },
            aml_rows=[
                {
                    "id": 7002,
                    "move_id": [9902, "PBK017/2025/00342"],
                    "account_id": [401, "Hutang Vendor"],
                }
            ],
            account_info_map={
                401: {"code": "2101002", "name": "Hutang Vendor", "account_type": "liability_payable"},
            },
        )

        self.assertEqual(kind, "payment_bank")

    def test_classify_matching_entry_candidate_treats_outstanding_payment_account_as_payment_bank(self) -> None:
        kind = SvlDashboardServiceAsync._classify_matching_entry_candidate(
            move_row={
                "id": 9903,
                "move_type": "entry",
                "name": "MANUAL/OUTSTANDING/0001",
            },
            aml_rows=[
                {
                    "id": 7003,
                    "move_id": [9903, "MANUAL/OUTSTANDING/0001"],
                    "account_id": [402, "Outstanding Payments"],
                }
            ],
            account_info_map={
                402: {"code": "11120003", "name": "Outstanding Payments", "account_type": "asset_current"},
            },
        )

        self.assertEqual(kind, "payment_bank")

    async def test_fetch_pcb_product_info_map_prefers_category_expense_account_over_item_fallback(self) -> None:
        rpc = _FakeDashboardRpc()
        rpc.fields_map["product.product"]["property_account_expense_id"] = {"type": "many2one"}
        rpc.product_rows = [
            {
                "id": 101,
                "name": "Produk A",
                "default_code": "SKU-A",
                "categ_id": [1, "Raw"],
                "property_account_expense_id": [60, "Item Expense"],
            }
        ]
        service = SvlDashboardServiceAsync(rpc=rpc, logger=logging.getLogger("test.dashboard"))

        result = await service._fetch_pcb_product_info_map(
            product_ids=[101],
            context={},
            account_info_map={
                10: {"code": "114001", "name": "Persediaan Barang"},
                20: {"code": "510001", "name": "Beban Pokok"},
                60: {"code": "599999", "name": "Item Expense"},
            },
        )

        self.assertEqual(result[101]["valuation_account_code"], "114001")
        self.assertEqual(result[101]["expense_account_code"], "510001")
        self.assertEqual(result[101]["expense_account_source"], "Category Expense")
        self.assertEqual(result[101]["expense_account_field_name"], "property_account_expense_categ_id")

    async def test_fetch_pcb_product_info_map_falls_back_to_item_expense_when_category_expense_missing(self) -> None:
        rpc = _FakeDashboardRpc()
        rpc.fields_map["product.product"]["property_account_expense_id"] = {"type": "many2one"}
        rpc.category_rows = [
            {
                "id": 1,
                "name": "Raw Material",
                "property_valuation": "real_time",
                "property_stock_valuation_account_id": [10, "Persediaan"],
                "property_account_expense_categ_id": False,
                "property_stock_account_input_categ_id": [30, "Stock Input"],
                "property_stock_account_output_categ_id": [40, "Stock Output"],
            }
        ]
        rpc.product_rows = [
            {
                "id": 101,
                "name": "Produk A",
                "default_code": "SKU-A",
                "categ_id": [1, "Raw"],
                "property_account_expense_id": [60, "Item Expense"],
            }
        ]
        service = SvlDashboardServiceAsync(rpc=rpc, logger=logging.getLogger("test.dashboard"))

        result = await service._fetch_pcb_product_info_map(
            product_ids=[101],
            context={},
            account_info_map={
                10: {"code": "114001", "name": "Persediaan Barang"},
                60: {"code": "510999", "name": "Item Expense"},
            },
        )

        self.assertEqual(result[101]["expense_account_code"], "510999")
        self.assertEqual(result[101]["expense_account_source"], "Item Expense")
        self.assertEqual(result[101]["expense_account_field_name"], "property_account_expense_id")

    def test_build_pcb_case2_repair_rows_falls_back_to_bill_expense_account_when_product_expense_missing(self) -> None:
        service = SvlDashboardServiceAsync(rpc=_FakeDashboardRpc(), logger=logging.getLogger("test.dashboard"))

        rows = service._build_pcb_case2_repair_rows(
            cycle_status="problem",
            picking_id=70562,
            picking_name="CBG/IN/00562",
            gr_date="2026-03-26",
            partner_name="Vendor Bravo",
            po_names=["PO/CB/2025/12/00547"],
            bill_move_ids=[8562],
            item_rows=[
                SvlDashboardCycleItemRow(
                    product_id=952,
                    product_name="GARLIC PEELED",
                    default_code="F-FHVF-0029",
                    account_rows=[
                        SvlDashboardCycleAccountRow(
                            account_id=301,
                            code="2103006",
                            name="Hutang Suspend",
                            account_type="liability_current",
                            account_group="liability",
                            debit=507.77,
                            credit=0.0,
                            net_balance=507.77,
                            status="problem",
                        ),
                        SvlDashboardCycleAccountRow(
                            account_id=401,
                            code="51000010",
                            name="Cost of Goods Sold",
                            account_type="expense_direct_cost",
                            account_group="expense",
                            debit=0.0,
                            credit=1706.16,
                            net_balance=-1706.16,
                            status="problem",
                        ),
                        SvlDashboardCycleAccountRow(
                            account_id=402,
                            code="1105003",
                            name="Persediaan Makanan",
                            account_type="asset_current",
                            account_group="asset",
                            debit=41692.23,
                            credit=0.0,
                            net_balance=41692.23,
                            status="acceptable",
                        ),
                        SvlDashboardCycleAccountRow(
                            account_id=403,
                            code="1101060",
                            name="BCA Bogor",
                            account_type="asset_cash",
                            account_group="asset",
                            debit=0.0,
                            credit=40493.84,
                            net_balance=-40493.84,
                            status="acceptable",
                        ),
                    ],
                )
            ],
            raw_lines=[
                {"jenis": "BILL", "tipe_akun": "expense_direct_cost", "akun_code": "51000010"},
                {"jenis": "BK", "akun_code": "1101060"},
            ],
            lines_by_move={
                8562: [
                    {
                        "id": 9562,
                        "account_id": [562, "Cost of Goods Sold"],
                        "product_id": [952, "GARLIC PEELED"],
                        "purchase_line_id": [83562, "PO Line 562"],
                    }
                ]
            },
            account_info_map={
                562: {"code": "51000010", "name": "Cost of Goods Sold", "account_type": "expense_direct_cost"},
            },
            move_info_map={
                8562: {"name": "BILL/2026/0562", "partner_id": [88, "Vendor Bravo"]},
            },
            product_info_map={
                952: {
                    "default_code": "F-FHVF-0029",
                    "name": "GARLIC PEELED",
                    "categ_name": "Food",
                    "valuation_account_code": "1105003",
                    "valuation_account_name": "Persediaan Makanan",
                    "expense_account_code": "",
                    "expense_account_name": "",
                }
            },
            bill_rows_by_id={
                8562: {"invoice_date": "2026-03-26", "invoice_origin": "PO/CB/2025/12/00547"},
            },
            purchase_line_product_map={83562: 952},
            purchase_line_po_name_map={83562: "PO/CB/2025/12/00547"},
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].expense_account_code, "51000010")
        self.assertEqual(rows[0].hpp_balance, -1706.16)
        self.assertEqual(rows[0].selisih_hpp_amount, 1198.39)
        self.assertEqual([(line.role, line.side, line.account_code, line.amount) for line in rows[0].planned_lines], [
            ("problem_2103006", "credit", "2103006", 507.77),
            ("hpp_zero", "debit", "51000010", 1706.16),
            ("selisih_hpp", "credit", "51000010", 1198.39),
        ])
        self.assertIn("Cost of Goods Sold", [line.account_name for line in rows[0].planned_lines])

    def test_build_pcb_case2_repair_rows_falls_back_to_cycle_bill_code_when_bill_line_item_mapping_missing(self) -> None:
        service = SvlDashboardServiceAsync(rpc=_FakeDashboardRpc(), logger=logging.getLogger("test.dashboard"))

        rows = service._build_pcb_case2_repair_rows(
            cycle_status="problem",
            picking_id=70563,
            picking_name="CBG/IN/00562",
            gr_date="2026-03-26",
            partner_name="Vendor Bravo",
            po_names=["PO/CB/2025/12/00547"],
            bill_move_ids=[8563],
            item_rows=[
                SvlDashboardCycleItemRow(
                    product_id=953,
                    product_name="GARLIC PEELED",
                    default_code="F-FHVF-0029",
                    account_rows=[
                        SvlDashboardCycleAccountRow(
                            account_id=301,
                            code="2103006",
                            name="Hutang Suspend",
                            account_type="liability_current",
                            account_group="liability",
                            debit=507.77,
                            credit=0.0,
                            net_balance=507.77,
                            status="problem",
                        ),
                        SvlDashboardCycleAccountRow(
                            account_id=401,
                            code="51000010",
                            name="Cost of Goods Sold",
                            account_type="expense_direct_cost",
                            account_group="expense",
                            debit=0.0,
                            credit=1706.16,
                            net_balance=-1706.16,
                            status="problem",
                        ),
                        SvlDashboardCycleAccountRow(
                            account_id=402,
                            code="1105003",
                            name="Persediaan Makanan",
                            account_type="asset_current",
                            account_group="asset",
                            debit=41692.23,
                            credit=0.0,
                            net_balance=41692.23,
                            status="acceptable",
                        ),
                    ],
                )
            ],
            raw_lines=[
                {"jenis": "BILL", "tipe_akun": "expense_direct_cost", "akun_code": "51000010"},
            ],
            lines_by_move={
                8563: [
                    {
                        "id": 9563,
                        "account_id": [563, "Cost of Goods Sold"],
                        "product_id": False,
                        "purchase_line_id": False,
                    }
                ]
            },
            account_info_map={
                563: {"code": "51000010", "name": "Cost of Goods Sold", "account_type": "expense_direct_cost"},
            },
            move_info_map={
                8563: {"name": "BILL/2026/0563", "partner_id": [88, "Vendor Bravo"]},
            },
            product_info_map={
                953: {
                    "default_code": "F-FHVF-0029",
                    "name": "GARLIC PEELED",
                    "categ_name": "Food",
                    "valuation_account_code": "1105003",
                    "valuation_account_name": "Persediaan Makanan",
                    "expense_account_code": "",
                    "expense_account_name": "",
                }
            },
            bill_rows_by_id={
                8563: {"invoice_date": "2026-03-26", "invoice_origin": "PO/CB/2025/12/00547"},
            },
            purchase_line_product_map={},
            purchase_line_po_name_map={},
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].expense_account_code, "51000010")
        self.assertEqual(rows[0].bill_move_id, 8563)
        self.assertEqual(rows[0].problem_balances_by_code, {"2103006": 507.77})
        self.assertEqual([(line.role, line.side, line.account_code, line.amount) for line in rows[0].planned_lines], [
            ("problem_2103006", "credit", "2103006", 507.77),
            ("hpp_zero", "debit", "51000010", 1706.16),
            ("selisih_hpp", "credit", "51000010", 1198.39),
        ])

    def test_build_pcb_case2_repair_rows_moves_generic_hpp_into_category_hpp_when_problem_zero(self) -> None:
        service = SvlDashboardServiceAsync(rpc=_FakeDashboardRpc(), logger=logging.getLogger("test.dashboard"))

        rows = service._build_pcb_case2_repair_rows(
            cycle_status="problem",
            picking_id=70564,
            picking_name="CBG/IN/00562",
            gr_date="2026-03-26",
            partner_name="Vendor Bravo",
            po_names=["PO/CB/2025/12/00547"],
            bill_move_ids=[8564],
            item_rows=[
                SvlDashboardCycleItemRow(
                    product_id=954,
                    product_name="GARLIC PEELED",
                    default_code="F-FHVF-0029",
                    account_rows=[
                        SvlDashboardCycleAccountRow(
                            account_id=401,
                            code="51000010",
                            name="Cost of Goods Sold",
                            account_type="expense_direct_cost",
                            account_group="expense",
                            debit=0.0,
                            credit=1706.16,
                            net_balance=-1706.16,
                            status="problem",
                        ),
                        SvlDashboardCycleAccountRow(
                            account_id=402,
                            code="5101003",
                            name="HPP - Makanan / COGS - Food",
                            account_type="expense_direct_cost",
                            account_group="expense",
                            debit=507.77,
                            credit=0.0,
                            net_balance=507.77,
                            status="problem",
                        ),
                        SvlDashboardCycleAccountRow(
                            account_id=403,
                            code="1105003",
                            name="Persediaan Makanan",
                            account_type="asset_current",
                            account_group="asset",
                            debit=41692.23,
                            credit=0.0,
                            net_balance=41692.23,
                            status="acceptable",
                        ),
                    ],
                )
            ],
            raw_lines=[
                {"jenis": "BILL", "tipe_akun": "expense_direct_cost", "akun_code": "51000010"},
            ],
            lines_by_move={
                8564: [
                    {
                        "id": 9564,
                        "account_id": [564, "Cost of Goods Sold"],
                        "product_id": False,
                        "purchase_line_id": False,
                    }
                ]
            },
            account_info_map={
                564: {"code": "51000010", "name": "Cost of Goods Sold", "account_type": "expense_direct_cost"},
            },
            move_info_map={
                8564: {"name": "BILL/2026/0564", "partner_id": [88, "Vendor Bravo"]},
            },
            product_info_map={
                954: {
                    "default_code": "F-FHVF-0029",
                    "name": "GARLIC PEELED",
                    "categ_name": "Food",
                    "valuation_account_code": "1105003",
                    "valuation_account_name": "Persediaan Makanan",
                    "expense_account_code": "5101003",
                    "expense_account_name": "HPP - Makanan / COGS - Food",
                }
            },
            bill_rows_by_id={
                8564: {"invoice_date": "2026-03-26", "invoice_origin": "PO/CB/2025/12/00547"},
            },
            purchase_line_product_map={},
            purchase_line_po_name_map={},
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].problem_balances_by_code, {})
        self.assertEqual(rows[0].expense_account_code, "5101003")
        self.assertEqual(rows[0].hpp_balances_by_code, {"51000010": -1706.16, "5101003": 507.77})
        self.assertEqual(rows[0].hpp_balance, -1198.39)
        self.assertEqual(rows[0].selisih_hpp_amount, 1198.39)
        self.assertEqual([(line.role, line.side, line.account_code, line.amount) for line in rows[0].planned_lines], [
            ("hpp_zero_51000010", "debit", "51000010", 1706.16),
            ("hpp_zero", "credit", "5101003", 507.77),
            ("selisih_hpp", "credit", "5101003", 1198.39),
        ])

    def test_build_pcb_case2_repair_rows_uses_problem_hpp_inventory_denominator_for_coefficient_variance(self) -> None:
        service = SvlDashboardServiceAsync(rpc=_FakeDashboardRpc(), logger=logging.getLogger("test.dashboard"))

        rows = service._build_pcb_case2_repair_rows(
            cycle_status="problem",
            picking_id=7001,
            picking_name="LHPK/IN/7001",
            gr_date="2026-03-18",
            partner_name="Vendor Alpha",
            po_names=["PO/2026/0001"],
            bill_move_ids=[8201],
            item_rows=[
                SvlDashboardCycleItemRow(
                    product_id=901,
                    product_name="Produk PCB",
                    default_code="SKU-001",
                    account_rows=[
                        SvlDashboardCycleAccountRow(
                            account_id=301,
                            code="2103006",
                            name="Hutang Suspend",
                            account_type="liability_current",
                            account_group="liability",
                            debit=0.0,
                            credit=100.0,
                            net_balance=-100.0,
                            status="problem",
                        ),
                        SvlDashboardCycleAccountRow(
                            account_id=401,
                            code="1105004",
                            name="Persediaan Rokok",
                            account_type="asset_current",
                            account_group="asset",
                            debit=70.0,
                            credit=0.0,
                            net_balance=70.0,
                            status="acceptable",
                        ),
                        SvlDashboardCycleAccountRow(
                            account_id=402,
                            code="5101004",
                            name="HPP Rokok",
                            account_type="expense_direct_cost",
                            account_group="expense",
                            debit=70.0,
                            credit=0.0,
                            net_balance=70.0,
                            status="problem",
                        ),
                        SvlDashboardCycleAccountRow(
                            account_id=403,
                            code="1101060",
                            name="BCA Bogor",
                            account_type="asset_cash",
                            account_group="asset",
                            debit=0.0,
                            credit=55.0,
                            net_balance=-55.0,
                            status="acceptable",
                        ),
                        SvlDashboardCycleAccountRow(
                            account_id=404,
                            code="5101010",
                            name="COGS Variance",
                            account_type="expense",
                            account_group="expense",
                            debit=22.0,
                            credit=0.0,
                            net_balance=22.0,
                            status="info",
                        ),
                    ],
                )
            ],
            raw_lines=[
                {"jenis": "BILL", "tipe_akun": "expense_direct_cost", "akun_code": "5101004"},
                {"jenis": "BK", "akun_code": "1101060"},
            ],
            lines_by_move={
                8201: [
                    {
                        "id": 9101,
                        "account_id": [501, "HPP Rokok"],
                        "product_id": [901, "Produk PCB"],
                        "purchase_line_id": [8301, "PO Line 1"],
                    }
                ]
            },
            account_info_map={
                501: {"code": "5101004", "account_type": "expense_direct_cost"},
            },
            move_info_map={
                8201: {"name": "BILL/2026/0001", "partner_id": [77, "Vendor Alpha"]},
            },
            product_info_map={
                901: {
                    "default_code": "SKU-001",
                    "name": "Produk PCB",
                    "categ_name": "Rokok",
                    "valuation_account_code": "1105004",
                    "valuation_account_name": "Persediaan Rokok",
                    "expense_account_code": "5101004",
                    "expense_account_name": "HPP Rokok",
                }
            },
            bill_rows_by_id={
                8201: {"invoice_date": "2026-03-19", "invoice_origin": "PO/2026/0001"},
            },
            purchase_line_product_map={8301: 901},
            purchase_line_po_name_map={8301: "PO/2026/0001"},
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].row_key, "case2::7001::901")
        self.assertEqual(rows[0].inventory_account_code, "1105004")
        self.assertEqual(rows[0].expense_account_code, "5101004")
        self.assertEqual(rows[0].problem_balances_by_code, {"2103006": -100.0})
        self.assertEqual(rows[0].hpp_balance, 70.0)
        self.assertEqual(rows[0].selisih_hpp_amount, 30.0)
        self.assertEqual(rows[0].bank_balances_by_code, {"1101060": -55.0})
        self.assertEqual(rows[0].coefficient_variance, 30.0)
        self.assertEqual([(line.role, line.side, line.account_code, line.amount) for line in rows[0].planned_lines], [
            ("problem_2103006", "debit", "2103006", 100.0),
            ("hpp_zero", "credit", "5101004", 70.0),
            ("selisih_hpp", "credit", "5101004", 30.0),
        ])
        self.assertFalse(any(line.role.startswith("variance_zero_") for line in rows[0].planned_lines))
        self.assertIn("bank_non_zero:1101060", rows[0].guard_flags)
        self.assertIn("problem_non_zero:2103006", rows[0].guard_flags)
        self.assertIn("hpp_non_zero", rows[0].guard_flags)
        self.assertIn("inventory_non_zero", rows[0].guard_flags)

    def test_build_pcb_case2_repair_rows_zeroes_variance_first_when_hpp_item_balance_absent_and_amount_matches(self) -> None:
        service = SvlDashboardServiceAsync(rpc=_FakeDashboardRpc(), logger=logging.getLogger("test.dashboard"))

        rows = service._build_pcb_case2_repair_rows(
            cycle_status="problem",
            picking_id=7002,
            picking_name="CBG/IN/01048",
            gr_date="2026-03-18",
            partner_name="Vendor Alpha",
            po_names=["PO/2026/0002"],
            bill_move_ids=[8202],
            item_rows=[
                SvlDashboardCycleItemRow(
                    product_id=902,
                    product_name="Produk Variance",
                    default_code="SKU-002",
                    account_rows=[
                        SvlDashboardCycleAccountRow(
                            account_id=302,
                            code="2103006",
                            name="Hutang Suspend",
                            account_type="liability_current",
                            account_group="liability",
                            debit=0.0,
                            credit=100.0,
                            net_balance=-100.0,
                            status="problem",
                        ),
                        SvlDashboardCycleAccountRow(
                            account_id=402,
                            code="1105003",
                            name="Persediaan Makanan",
                            account_type="asset_current",
                            account_group="asset",
                            debit=100.0,
                            credit=0.0,
                            net_balance=100.0,
                            status="acceptable",
                        ),
                        SvlDashboardCycleAccountRow(
                            account_id=403,
                            code="5101010",
                            name="Selisih HPP / COGS Variance",
                            account_type="expense",
                            account_group="expense",
                            debit=100.0,
                            credit=0.0,
                            net_balance=100.0,
                            status="info",
                        ),
                    ],
                )
            ],
            raw_lines=[{"jenis": "BILL", "tipe_akun": "expense_direct_cost", "akun_code": "5101003"}],
            lines_by_move={
                8202: [
                    {
                        "id": 9102,
                        "account_id": [502, "HPP Makanan"],
                        "product_id": [902, "Produk Variance"],
                        "purchase_line_id": [8302, "PO Line 2"],
                    }
                ]
            },
            account_info_map={502: {"code": "5101003", "account_type": "expense_direct_cost"}},
            move_info_map={8202: {"name": "BILL/2026/0002", "partner_id": [77, "Vendor Alpha"]}},
            product_info_map={
                902: {
                    "default_code": "SKU-002",
                    "name": "Produk Variance",
                    "categ_name": "Food",
                    "valuation_account_code": "1105003",
                    "valuation_account_name": "Persediaan Makanan",
                    "expense_account_code": "5101003",
                    "expense_account_name": "HPP Makanan",
                }
            },
            bill_rows_by_id={8202: {"invoice_date": "2026-03-19", "invoice_origin": "PO/2026/0002"}},
            purchase_line_product_map={8302: 902},
            purchase_line_po_name_map={8302: "PO/2026/0002"},
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].expense_account_code, "5101003")
        self.assertEqual(rows[0].hpp_balance, 0.0)
        self.assertEqual(rows[0].cogs_variance_balance, 100.0)
        self.assertEqual(rows[0].selisih_hpp_amount, 0.0)
        self.assertEqual([(line.role, line.side, line.account_code, line.amount) for line in rows[0].planned_lines], [
            ("problem_2103006", "debit", "2103006", 100.0),
            ("variance_zero_5101010", "credit", "5101010", 100.0),
        ])

    def test_build_pcb_case2_repair_rows_sends_residual_to_hpp_after_variance_zero_when_hpp_item_balance_absent(self) -> None:
        service = SvlDashboardServiceAsync(rpc=_FakeDashboardRpc(), logger=logging.getLogger("test.dashboard"))

        rows = service._build_pcb_case2_repair_rows(
            cycle_status="problem",
            picking_id=7003,
            picking_name="CBG/IN/01049",
            gr_date="2026-03-18",
            partner_name="Vendor Alpha",
            po_names=["PO/2026/0003"],
            bill_move_ids=[8203],
            item_rows=[
                SvlDashboardCycleItemRow(
                    product_id=903,
                    product_name="Produk Residual",
                    default_code="SKU-003",
                    account_rows=[
                        SvlDashboardCycleAccountRow(
                            account_id=303,
                            code="2103006",
                            name="Hutang Suspend",
                            account_type="liability_current",
                            account_group="liability",
                            debit=0.0,
                            credit=100.0,
                            net_balance=-100.0,
                            status="problem",
                        ),
                        SvlDashboardCycleAccountRow(
                            account_id=403,
                            code="1105003",
                            name="Persediaan Makanan",
                            account_type="asset_current",
                            account_group="asset",
                            debit=100.0,
                            credit=0.0,
                            net_balance=100.0,
                            status="acceptable",
                        ),
                        SvlDashboardCycleAccountRow(
                            account_id=404,
                            code="5101010",
                            name="Selisih HPP / COGS Variance",
                            account_type="expense",
                            account_group="expense",
                            debit=40.0,
                            credit=0.0,
                            net_balance=40.0,
                            status="info",
                        ),
                    ],
                )
            ],
            raw_lines=[{"jenis": "BILL", "tipe_akun": "expense_direct_cost", "akun_code": "5101003"}],
            lines_by_move={
                8203: [
                    {
                        "id": 9103,
                        "account_id": [503, "HPP Makanan"],
                        "product_id": [903, "Produk Residual"],
                        "purchase_line_id": [8303, "PO Line 3"],
                    }
                ]
            },
            account_info_map={503: {"code": "5101003", "account_type": "expense_direct_cost"}},
            move_info_map={8203: {"name": "BILL/2026/0003", "partner_id": [77, "Vendor Alpha"]}},
            product_info_map={
                903: {
                    "default_code": "SKU-003",
                    "name": "Produk Residual",
                    "categ_name": "Food",
                    "valuation_account_code": "1105003",
                    "valuation_account_name": "Persediaan Makanan",
                    "expense_account_code": "5101003",
                    "expense_account_name": "HPP Makanan",
                }
            },
            bill_rows_by_id={8203: {"invoice_date": "2026-03-19", "invoice_origin": "PO/2026/0003"}},
            purchase_line_product_map={8303: 903},
            purchase_line_po_name_map={8303: "PO/2026/0003"},
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].hpp_balance, 0.0)
        self.assertEqual(rows[0].cogs_variance_balance, 40.0)
        self.assertEqual(rows[0].selisih_hpp_amount, 60.0)
        self.assertEqual([(line.role, line.side, line.account_code, line.amount) for line in rows[0].planned_lines], [
            ("problem_2103006", "debit", "2103006", 100.0),
            ("variance_zero_5101010", "credit", "5101010", 40.0),
            ("selisih_hpp", "credit", "5101003", 60.0),
        ])

    def test_build_pcb_case2_repair_rows_zeroes_variance_first_for_clearing_problem_when_hpp_item_balance_absent(self) -> None:
        service = SvlDashboardServiceAsync(rpc=_FakeDashboardRpc(), logger=logging.getLogger("test.dashboard"))

        rows = service._build_pcb_case2_repair_rows(
            cycle_status="problem",
            picking_id=7004,
            picking_name="CBG/IN/01050",
            gr_date="2026-03-18",
            partner_name="Vendor Alpha",
            po_names=["PO/2026/0004"],
            bill_move_ids=[8204],
            item_rows=[
                SvlDashboardCycleItemRow(
                    product_id=904,
                    product_name="Produk Clearing Variance",
                    default_code="SKU-004",
                    account_rows=[
                        SvlDashboardCycleAccountRow(
                            account_id=304,
                            code="1108099",
                            name="Clearing",
                            account_type="asset_current",
                            account_group="asset",
                            debit=0.0,
                            credit=80.0,
                            net_balance=-80.0,
                            status="problem",
                        ),
                        SvlDashboardCycleAccountRow(
                            account_id=404,
                            code="1105002",
                            name="Persediaan Beverage",
                            account_type="asset_current",
                            account_group="asset",
                            debit=80.0,
                            credit=0.0,
                            net_balance=80.0,
                            status="acceptable",
                        ),
                        SvlDashboardCycleAccountRow(
                            account_id=405,
                            code="5101010",
                            name="Selisih HPP / COGS Variance",
                            account_type="expense",
                            account_group="expense",
                            debit=80.0,
                            credit=0.0,
                            net_balance=80.0,
                            status="info",
                        ),
                    ],
                )
            ],
            raw_lines=[{"jenis": "BILL", "tipe_akun": "expense_direct_cost", "akun_code": "5101002"}],
            lines_by_move={
                8204: [
                    {
                        "id": 9104,
                        "account_id": [504, "HPP Beverage"],
                        "product_id": [904, "Produk Clearing Variance"],
                        "purchase_line_id": [8304, "PO Line 4"],
                    }
                ]
            },
            account_info_map={504: {"code": "5101002", "account_type": "expense_direct_cost"}},
            move_info_map={8204: {"name": "BILL/2026/0004", "partner_id": [77, "Vendor Alpha"]}},
            product_info_map={
                904: {
                    "default_code": "SKU-004",
                    "name": "Produk Clearing Variance",
                    "categ_name": "Beverage",
                    "valuation_account_code": "1105002",
                    "valuation_account_name": "Persediaan Beverage",
                    "expense_account_code": "5101002",
                    "expense_account_name": "HPP Beverage",
                }
            },
            bill_rows_by_id={8204: {"invoice_date": "2026-03-19", "invoice_origin": "PO/2026/0004"}},
            purchase_line_product_map={8304: 904},
            purchase_line_po_name_map={8304: "PO/2026/0004"},
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].problem_balances_by_code, {"1108099": -80.0})
        self.assertEqual(rows[0].hpp_balance, 0.0)
        self.assertEqual([(line.role, line.side, line.account_code, line.amount) for line in rows[0].planned_lines], [
            ("problem_1108099", "debit", "1108099", 80.0),
            ("variance_zero_5101010", "credit", "5101010", 80.0),
        ])

    def test_build_pcb_case2_repair_rows_uses_clearing_problem_account_when_suspend_zero(self) -> None:
        service = SvlDashboardServiceAsync(rpc=_FakeDashboardRpc(), logger=logging.getLogger("test.dashboard"))

        rows = service._build_pcb_case2_repair_rows(
            cycle_status="problem",
            picking_id=7002,
            picking_name="LHPK/IN/7002",
            gr_date="2026-03-18",
            partner_name="Vendor Alpha",
            po_names=["PO/2026/0002"],
            bill_move_ids=[8202],
            item_rows=[
                SvlDashboardCycleItemRow(
                    product_id=902,
                    product_name="Produk Clearing",
                    default_code="SKU-002",
                    account_rows=[
                        SvlDashboardCycleAccountRow(
                            account_id=302,
                            code="1108099",
                            name="Clearing",
                            account_type="asset_current",
                            account_group="asset",
                            debit=0.0,
                            credit=80.0,
                            net_balance=-80.0,
                            status="problem",
                        ),
                        SvlDashboardCycleAccountRow(
                            account_id=402,
                            code="5101002",
                            name="HPP Beverage",
                            account_type="expense_direct_cost",
                            account_group="expense",
                            debit=50.0,
                            credit=0.0,
                            net_balance=50.0,
                            status="problem",
                        ),
                        SvlDashboardCycleAccountRow(
                            account_id=403,
                            code="1105002",
                            name="Persediaan Beverage",
                            account_type="asset_current",
                            account_group="asset",
                            debit=50.0,
                            credit=0.0,
                            net_balance=50.0,
                            status="acceptable",
                        ),
                    ],
                )
            ],
            raw_lines=[{"jenis": "BILL", "tipe_akun": "expense_direct_cost", "akun_code": "5101002"}],
            lines_by_move={
                8202: [
                    {
                        "id": 9102,
                        "account_id": [502, "HPP Beverage"],
                        "product_id": [902, "Produk Clearing"],
                        "purchase_line_id": [8302, "PO Line 2"],
                    }
                ]
            },
            account_info_map={502: {"code": "5101002", "account_type": "expense_direct_cost"}},
            move_info_map={8202: {"name": "BILL/2026/0002", "partner_id": [77, "Vendor Alpha"]}},
            product_info_map={
                902: {
                    "default_code": "SKU-002",
                    "name": "Produk Clearing",
                    "categ_name": "Minuman",
                    "valuation_account_code": "1105002",
                    "valuation_account_name": "Persediaan Beverage",
                    "expense_account_code": "5101002",
                    "expense_account_name": "HPP Beverage",
                }
            },
            bill_rows_by_id={8202: {"invoice_date": "2026-03-19", "invoice_origin": "PO/2026/0002"}},
            purchase_line_product_map={8302: 902},
            purchase_line_po_name_map={8302: "PO/2026/0002"},
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].problem_balances_by_code, {"1108099": -80.0})
        self.assertEqual([(line.role, line.account_code) for line in rows[0].planned_lines], [
            ("problem_1108099", "1108099"),
            ("hpp_zero", "5101002"),
            ("selisih_hpp", "5101002"),
        ])
        self.assertFalse(any(line.role.startswith("variance_zero_") for line in rows[0].planned_lines))

    def test_build_pcb_case2_repair_rows_builds_case3_problem_offset_then_residual_to_target(self) -> None:
        service = SvlDashboardServiceAsync(rpc=_FakeDashboardRpc(), logger=logging.getLogger("test.dashboard"))

        rows = service._build_pcb_case2_repair_rows(
            pcb_case="case3",
            cycle_status="problem",
            picking_id=7005,
            picking_name="CBG/IN/00682",
            gr_date="2026-03-18",
            partner_name="Vendor Alpha",
            po_names=["PO/2026/0005"],
            bill_move_ids=[8205],
            item_rows=[
                SvlDashboardCycleItemRow(
                    product_id=905,
                    product_name="Produk Case 3",
                    default_code="SKU-005",
                    account_rows=[
                        SvlDashboardCycleAccountRow(
                            account_id=305,
                            code="1108099",
                            name="Clearing",
                            account_type="asset_current",
                            account_group="asset",
                            debit=100.0,
                            credit=0.0,
                            net_balance=100.0,
                            status="problem",
                        ),
                        SvlDashboardCycleAccountRow(
                            account_id=306,
                            code="2103006",
                            name="Hutang Suspend",
                            account_type="liability_current",
                            account_group="liability",
                            debit=0.0,
                            credit=120.0,
                            net_balance=-120.0,
                            status="problem",
                        ),
                        SvlDashboardCycleAccountRow(
                            account_id=406,
                            code="1105003",
                            name="Persediaan Makanan",
                            account_type="asset_current",
                            account_group="asset",
                            debit=120.0,
                            credit=0.0,
                            net_balance=120.0,
                            status="acceptable",
                        ),
                    ],
                )
            ],
            raw_lines=[{"jenis": "BILL", "tipe_akun": "expense_direct_cost", "akun_code": "5101003"}],
            lines_by_move={
                8205: [
                    {
                        "id": 9105,
                        "account_id": [505, "HPP Makanan"],
                        "product_id": [905, "Produk Case 3"],
                        "purchase_line_id": [8305, "PO Line 5"],
                    }
                ]
            },
            account_info_map={505: {"code": "5101003", "account_type": "expense_direct_cost"}},
            move_info_map={8205: {"name": "BILL/2026/0005", "partner_id": [77, "Vendor Alpha"]}},
            product_info_map={
                905: {
                    "default_code": "SKU-005",
                    "name": "Produk Case 3",
                    "categ_name": "Food",
                    "valuation_account_code": "1105003",
                    "valuation_account_name": "Persediaan Makanan",
                    "expense_account_code": "5101003",
                    "expense_account_name": "HPP Makanan",
                }
            },
            bill_rows_by_id={8205: {"invoice_date": "2026-03-19", "invoice_origin": "PO/2026/0005"}},
            purchase_line_product_map={8305: 905},
            purchase_line_po_name_map={8305: "PO/2026/0005"},
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].pcb_case, "case3")
        self.assertEqual(rows[0].row_key, "case3::7005::905")
        self.assertEqual(rows[0].selisih_hpp_amount, 20.0)
        self.assertEqual([(line.role, line.side, line.account_code, line.amount) for line in rows[0].planned_lines], [
            ("problem_1108099", "credit", "1108099", 100.0),
            ("problem_2103006", "debit", "2103006", 120.0),
            ("selisih_hpp", "credit", "5101003", 20.0),
        ])

    def test_build_pcb_case2_repair_rows_builds_case3_zeroes_variance_and_non_target_source_before_target(self) -> None:
        service = SvlDashboardServiceAsync(rpc=_FakeDashboardRpc(), logger=logging.getLogger("test.dashboard"))

        rows = service._build_pcb_case2_repair_rows(
            pcb_case="case3",
            cycle_status="problem",
            picking_id=7006,
            picking_name="CBG/IN/00800",
            gr_date="2026-03-18",
            partner_name="Vendor Alpha",
            po_names=["PO/2026/0006"],
            bill_move_ids=[8206],
            item_rows=[
                SvlDashboardCycleItemRow(
                    product_id=906,
                    product_name="Produk Case 3 Variance",
                    default_code="SKU-006",
                    account_rows=[
                        SvlDashboardCycleAccountRow(
                            account_id=306,
                            code="1108099",
                            name="Clearing",
                            account_type="asset_current",
                            account_group="asset",
                            debit=70.0,
                            credit=0.0,
                            net_balance=70.0,
                            status="problem",
                        ),
                        SvlDashboardCycleAccountRow(
                            account_id=307,
                            code="2103006",
                            name="Hutang Suspend",
                            account_type="liability_current",
                            account_group="liability",
                            debit=0.0,
                            credit=120.0,
                            net_balance=-120.0,
                            status="problem",
                        ),
                        SvlDashboardCycleAccountRow(
                            account_id=407,
                            code="5101010",
                            name="Selisih HPP / COGS Variance",
                            account_type="expense",
                            account_group="expense",
                            debit=10.0,
                            credit=0.0,
                            net_balance=10.0,
                            status="info",
                        ),
                        SvlDashboardCycleAccountRow(
                            account_id=408,
                            code="5101999",
                            name="COGS Umum",
                            account_type="expense_direct_cost",
                            account_group="expense",
                            debit=25.0,
                            credit=0.0,
                            net_balance=25.0,
                            status="problem",
                        ),
                        SvlDashboardCycleAccountRow(
                            account_id=409,
                            code="1105003",
                            name="Persediaan Makanan",
                            account_type="asset_current",
                            account_group="asset",
                            debit=120.0,
                            credit=0.0,
                            net_balance=120.0,
                            status="acceptable",
                        ),
                    ],
                )
            ],
            raw_lines=[{"jenis": "BILL", "tipe_akun": "expense_direct_cost", "akun_code": "5101003"}],
            lines_by_move={
                8206: [
                    {
                        "id": 9106,
                        "account_id": [506, "HPP Makanan"],
                        "product_id": [906, "Produk Case 3 Variance"],
                        "purchase_line_id": [8306, "PO Line 6"],
                    }
                ]
            },
            account_info_map={506: {"code": "5101003", "account_type": "expense_direct_cost"}},
            move_info_map={8206: {"name": "BILL/2026/0006", "partner_id": [77, "Vendor Alpha"]}},
            product_info_map={
                906: {
                    "default_code": "SKU-006",
                    "name": "Produk Case 3 Variance",
                    "categ_name": "Food",
                    "valuation_account_code": "1105003",
                    "valuation_account_name": "Persediaan Makanan",
                    "expense_account_code": "5101003",
                    "expense_account_name": "HPP Makanan",
                }
            },
            bill_rows_by_id={8206: {"invoice_date": "2026-03-19", "invoice_origin": "PO/2026/0006"}},
            purchase_line_product_map={8306: 906},
            purchase_line_po_name_map={8306: "PO/2026/0006"},
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].selisih_hpp_amount, 15.0)
        self.assertEqual([(line.role, line.side, line.account_code, line.amount) for line in rows[0].planned_lines], [
            ("problem_1108099", "credit", "1108099", 70.0),
            ("problem_2103006", "debit", "2103006", 120.0),
            ("variance_zero_5101010", "credit", "5101010", 10.0),
            ("hpp_zero_5101999", "credit", "5101999", 25.0),
            ("selisih_hpp", "credit", "5101003", 15.0),
        ])

    def test_build_pcb_case2_repair_rows_case3_uses_full_audit_linked_clearing_before_hpp(self) -> None:
        service = SvlDashboardServiceAsync(rpc=_FakeDashboardRpc(), logger=logging.getLogger("test.dashboard"))

        rows = service._build_pcb_case2_repair_rows(
            pcb_case="case3",
            cycle_status="problem",
            picking_id=7010,
            picking_name="CBG/IN/00812",
            gr_date="2026-03-26",
            partner_name="Vendor Alpha",
            po_names=["PO/2026/0010"],
            bill_move_ids=[8210],
            item_rows=[
                SvlDashboardCycleItemRow(
                    product_id=910,
                    product_name="Produk Audit Full",
                    default_code="SKU-010",
                    account_rows=[
                        SvlDashboardCycleAccountRow(
                            account_id=310,
                            code="2103006",
                            name="Hutang Suspend",
                            account_type="liability_current",
                            account_group="liability",
                            debit=100.0,
                            credit=0.0,
                            net_balance=100.0,
                            status="problem",
                        ),
                        SvlDashboardCycleAccountRow(
                            account_id=410,
                            code="1105003",
                            name="Persediaan Makanan",
                            account_type="asset_current",
                            account_group="asset",
                            debit=0.0,
                            credit=100.0,
                            net_balance=-100.0,
                            status="acceptable",
                        ),
                    ],
                    adjustment_audit_rows=[
                        SvlDashboardPcbAdjustmentAuditRow(
                            move_id=9910,
                            move_name="RAC/2026/01/0026",
                            move_date="2026-03-26",
                            move_ref="Audit-linked clearing",
                            product_id=910,
                            item_code="SKU-010",
                            item_name="Produk Audit Full",
                            source_account_code="5101010",
                            source_account_name="Selisih HPP / COGS Variance",
                            clearing_account_code="1108099",
                            repair_clearing_amount=100.0,
                            matched_basis="exact.purchase_line_id",
                            ambiguous=False,
                        )
                    ],
                )
            ],
            raw_lines=[{"jenis": "BILL", "tipe_akun": "expense_direct_cost", "akun_code": "5101003"}],
            lines_by_move={
                8210: [
                    {
                        "id": 9110,
                        "account_id": [510, "HPP Makanan"],
                        "product_id": [910, "Produk Audit Full"],
                        "purchase_line_id": [8310, "PO Line 10"],
                    }
                ]
            },
            account_info_map={510: {"code": "5101003", "account_type": "expense_direct_cost"}},
            move_info_map={8210: {"name": "BILL/2026/0010", "partner_id": [77, "Vendor Alpha"]}},
            product_info_map={
                910: {
                    "default_code": "SKU-010",
                    "name": "Produk Audit Full",
                    "categ_name": "Food",
                    "valuation_account_code": "1105003",
                    "valuation_account_name": "Persediaan Makanan",
                    "expense_account_code": "5101003",
                    "expense_account_name": "HPP Makanan",
                }
            },
            bill_rows_by_id={8210: {"invoice_date": "2026-03-26", "invoice_origin": "PO/2026/0010"}},
            purchase_line_product_map={8310: 910},
            purchase_line_po_name_map={8310: "PO/2026/0010"},
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].selisih_hpp_amount, 0.0)
        self.assertEqual([(line.role, line.side, line.account_code, line.amount) for line in rows[0].planned_lines], [
            ("problem_2103006", "credit", "2103006", 100.0),
            ("audit_clearing_1108099", "debit", "1108099", 100.0),
        ])

    def test_build_pcb_case2_repair_rows_case3_uses_partial_audit_linked_clearing_then_hpp_residual(self) -> None:
        service = SvlDashboardServiceAsync(rpc=_FakeDashboardRpc(), logger=logging.getLogger("test.dashboard"))

        rows = service._build_pcb_case2_repair_rows(
            pcb_case="case3",
            cycle_status="problem",
            picking_id=7011,
            picking_name="CBG/IN/00821",
            gr_date="2026-03-26",
            partner_name="Vendor Alpha",
            po_names=["PO/2026/0011"],
            bill_move_ids=[8211],
            item_rows=[
                SvlDashboardCycleItemRow(
                    product_id=911,
                    product_name="Produk Audit Partial",
                    default_code="SKU-011",
                    account_rows=[
                        SvlDashboardCycleAccountRow(
                            account_id=311,
                            code="2103006",
                            name="Hutang Suspend",
                            account_type="liability_current",
                            account_group="liability",
                            debit=120.0,
                            credit=0.0,
                            net_balance=120.0,
                            status="problem",
                        ),
                        SvlDashboardCycleAccountRow(
                            account_id=411,
                            code="1105003",
                            name="Persediaan Makanan",
                            account_type="asset_current",
                            account_group="asset",
                            debit=0.0,
                            credit=120.0,
                            net_balance=-120.0,
                            status="acceptable",
                        ),
                    ],
                    adjustment_audit_rows=[
                        SvlDashboardPcbAdjustmentAuditRow(
                            move_id=9911,
                            move_name="RAC/2026/01/0027",
                            move_date="2026-03-26",
                            move_ref="Audit-linked partial clearing",
                            product_id=911,
                            item_code="SKU-011",
                            item_name="Produk Audit Partial",
                            source_account_code="5101010",
                            source_account_name="Selisih HPP / COGS Variance",
                            clearing_account_code="1108099",
                            repair_clearing_amount=70.0,
                            matched_basis="exact.purchase_line_id",
                            ambiguous=False,
                        )
                    ],
                )
            ],
            raw_lines=[{"jenis": "BILL", "tipe_akun": "expense_direct_cost", "akun_code": "5101003"}],
            lines_by_move={
                8211: [
                    {
                        "id": 9111,
                        "account_id": [511, "HPP Makanan"],
                        "product_id": [911, "Produk Audit Partial"],
                        "purchase_line_id": [8311, "PO Line 11"],
                    }
                ]
            },
            account_info_map={511: {"code": "5101003", "account_type": "expense_direct_cost"}},
            move_info_map={8211: {"name": "BILL/2026/0011", "partner_id": [77, "Vendor Alpha"]}},
            product_info_map={
                911: {
                    "default_code": "SKU-011",
                    "name": "Produk Audit Partial",
                    "categ_name": "Food",
                    "valuation_account_code": "1105003",
                    "valuation_account_name": "Persediaan Makanan",
                    "expense_account_code": "5101003",
                    "expense_account_name": "HPP Makanan",
                }
            },
            bill_rows_by_id={8211: {"invoice_date": "2026-03-26", "invoice_origin": "PO/2026/0011"}},
            purchase_line_product_map={8311: 911},
            purchase_line_po_name_map={8311: "PO/2026/0011"},
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].selisih_hpp_amount, 50.0)
        self.assertEqual([(line.role, line.side, line.account_code, line.amount) for line in rows[0].planned_lines], [
            ("problem_2103006", "credit", "2103006", 120.0),
            ("audit_clearing_1108099", "debit", "1108099", 70.0),
            ("selisih_hpp", "debit", "5101003", 50.0),
        ])

    def test_build_pcb_case2_repair_rows_case3_ignores_ambiguous_audit_linked_clearing(self) -> None:
        service = SvlDashboardServiceAsync(rpc=_FakeDashboardRpc(), logger=logging.getLogger("test.dashboard"))

        rows = service._build_pcb_case2_repair_rows(
            pcb_case="case3",
            cycle_status="problem",
            picking_id=7012,
            picking_name="CBG/IN/00822",
            gr_date="2026-03-26",
            partner_name="Vendor Alpha",
            po_names=["PO/2026/0012"],
            bill_move_ids=[8212],
            item_rows=[
                SvlDashboardCycleItemRow(
                    product_id=912,
                    product_name="Produk Audit Ambiguous",
                    default_code="SKU-012",
                    account_rows=[
                        SvlDashboardCycleAccountRow(
                            account_id=312,
                            code="2103006",
                            name="Hutang Suspend",
                            account_type="liability_current",
                            account_group="liability",
                            debit=120.0,
                            credit=0.0,
                            net_balance=120.0,
                            status="problem",
                        )
                    ],
                    adjustment_audit_rows=[
                        SvlDashboardPcbAdjustmentAuditRow(
                            move_id=9912,
                            move_name="RAC/2026/01/0028",
                            move_date="2026-03-26",
                            move_ref="Ambiguous audit-linked clearing",
                            product_id=912,
                            item_code="SKU-012",
                            item_name="Produk Audit Ambiguous",
                            source_account_code="5101010",
                            source_account_name="Selisih HPP / COGS Variance",
                            clearing_account_code="1108099",
                            repair_clearing_amount=90.0,
                            matched_basis="fallback.bill_picking_token",
                            ambiguous=True,
                        )
                    ],
                )
            ],
            raw_lines=[{"jenis": "BILL", "tipe_akun": "expense_direct_cost", "akun_code": "5101003"}],
            lines_by_move={
                8212: [
                    {
                        "id": 9112,
                        "account_id": [512, "HPP Makanan"],
                        "product_id": [912, "Produk Audit Ambiguous"],
                        "purchase_line_id": [8312, "PO Line 12"],
                    }
                ]
            },
            account_info_map={512: {"code": "5101003", "account_type": "expense_direct_cost"}},
            move_info_map={8212: {"name": "BILL/2026/0012", "partner_id": [77, "Vendor Alpha"]}},
            product_info_map={
                912: {
                    "default_code": "SKU-012",
                    "name": "Produk Audit Ambiguous",
                    "categ_name": "Food",
                    "valuation_account_code": "1105003",
                    "valuation_account_name": "Persediaan Makanan",
                    "expense_account_code": "5101003",
                    "expense_account_name": "HPP Makanan",
                }
            },
            bill_rows_by_id={8212: {"invoice_date": "2026-03-26", "invoice_origin": "PO/2026/0012"}},
            purchase_line_product_map={8312: 912},
            purchase_line_po_name_map={8312: "PO/2026/0012"},
        )

        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0].review_required)
        self.assertFalse(rows[0].review_confirmed)
        self.assertIn("review", rows[0].review_reason.lower())
        self.assertEqual([(line.role, line.side, line.account_code, line.amount) for line in rows[0].planned_lines], [
            ("problem_2103006", "credit", "2103006", 120.0),
            ("selisih_hpp", "debit", "5101003", 120.0),
        ])

    def test_build_pcb_case2_repair_rows_builds_case4_zeroes_variance_before_target(self) -> None:
        service = SvlDashboardServiceAsync(rpc=_FakeDashboardRpc(), logger=logging.getLogger("test.dashboard"))

        rows = service._build_pcb_case2_repair_rows(
            pcb_case="case4",
            cycle_status="problem",
            picking_id=7007,
            picking_name="CBG/IN/00683",
            gr_date="2026-03-18",
            partner_name="Vendor Alpha",
            po_names=["PO/2026/0007"],
            bill_move_ids=[8207],
            item_rows=[
                SvlDashboardCycleItemRow(
                    product_id=907,
                    product_name="Produk Case 4 Variance",
                    default_code="SKU-007",
                    account_rows=[
                        SvlDashboardCycleAccountRow(
                            account_id=307,
                            code="2103006",
                            name="Hutang Suspend",
                            account_type="liability_current",
                            account_group="liability",
                            debit=0.0,
                            credit=100.0,
                            net_balance=-100.0,
                            status="problem",
                        ),
                        SvlDashboardCycleAccountRow(
                            account_id=407,
                            code="5101010",
                            name="Selisih HPP / COGS Variance",
                            account_type="expense",
                            account_group="expense",
                            debit=40.0,
                            credit=0.0,
                            net_balance=40.0,
                            status="info",
                        ),
                        SvlDashboardCycleAccountRow(
                            account_id=408,
                            code="1105003",
                            name="Persediaan Makanan",
                            account_type="asset_current",
                            account_group="asset",
                            debit=100.0,
                            credit=0.0,
                            net_balance=100.0,
                            status="acceptable",
                        ),
                    ],
                )
            ],
            raw_lines=[{"jenis": "BILL", "tipe_akun": "expense_direct_cost", "akun_code": "5101003"}],
            lines_by_move={
                8207: [
                    {
                        "id": 9107,
                        "account_id": [507, "HPP Makanan"],
                        "product_id": [907, "Produk Case 4 Variance"],
                        "purchase_line_id": [8307, "PO Line 7"],
                    }
                ]
            },
            account_info_map={507: {"code": "5101003", "account_type": "expense_direct_cost"}},
            move_info_map={8207: {"name": "BILL/2026/0007", "partner_id": [77, "Vendor Alpha"]}},
            product_info_map={
                907: {
                    "default_code": "SKU-007",
                    "name": "Produk Case 4 Variance",
                    "categ_name": "Food",
                    "valuation_account_code": "1105003",
                    "valuation_account_name": "Persediaan Makanan",
                    "expense_account_code": "5101003",
                    "expense_account_name": "HPP Makanan",
                }
            },
            bill_rows_by_id={8207: {"invoice_date": "2026-03-19", "invoice_origin": "PO/2026/0007"}},
            purchase_line_product_map={8307: 907},
            purchase_line_po_name_map={8307: "PO/2026/0007"},
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].pcb_case, "case4")
        self.assertEqual(rows[0].selisih_hpp_amount, 60.0)
        self.assertEqual([(line.role, line.side, line.account_code, line.amount) for line in rows[0].planned_lines], [
            ("problem_2103006", "debit", "2103006", 100.0),
            ("variance_zero_5101010", "credit", "5101010", 40.0),
            ("selisih_hpp", "credit", "5101003", 60.0),
        ])

    def test_build_pcb_case2_repair_rows_builds_case4_direct_to_target_without_source_hpp(self) -> None:
        service = SvlDashboardServiceAsync(rpc=_FakeDashboardRpc(), logger=logging.getLogger("test.dashboard"))

        rows = service._build_pcb_case2_repair_rows(
            pcb_case="case4",
            cycle_status="problem",
            picking_id=7008,
            picking_name="CBG/IN/00744",
            gr_date="2026-03-18",
            partner_name="Vendor Alpha",
            po_names=["PO/2026/0008"],
            bill_move_ids=[8208],
            item_rows=[
                SvlDashboardCycleItemRow(
                    product_id=908,
                    product_name="Produk Case 4 Direct",
                    default_code="SKU-008",
                    account_rows=[
                        SvlDashboardCycleAccountRow(
                            account_id=308,
                            code="2103006",
                            name="Hutang Suspend",
                            account_type="liability_current",
                            account_group="liability",
                            debit=0.0,
                            credit=100.0,
                            net_balance=-100.0,
                            status="problem",
                        ),
                        SvlDashboardCycleAccountRow(
                            account_id=408,
                            code="1105003",
                            name="Persediaan Makanan",
                            account_type="asset_current",
                            account_group="asset",
                            debit=100.0,
                            credit=0.0,
                            net_balance=100.0,
                            status="acceptable",
                        ),
                    ],
                )
            ],
            raw_lines=[{"jenis": "BILL", "tipe_akun": "expense_direct_cost", "akun_code": "5101003"}],
            lines_by_move={
                8208: [
                    {
                        "id": 9108,
                        "account_id": [508, "HPP Makanan"],
                        "product_id": [908, "Produk Case 4 Direct"],
                        "purchase_line_id": [8308, "PO Line 8"],
                    }
                ]
            },
            account_info_map={508: {"code": "5101003", "account_type": "expense_direct_cost"}},
            move_info_map={8208: {"name": "BILL/2026/0008", "partner_id": [77, "Vendor Alpha"]}},
            product_info_map={
                908: {
                    "default_code": "SKU-008",
                    "name": "Produk Case 4 Direct",
                    "categ_name": "Food",
                    "valuation_account_code": "1105003",
                    "valuation_account_name": "Persediaan Makanan",
                    "expense_account_code": "5101003",
                    "expense_account_name": "HPP Makanan",
                }
            },
            bill_rows_by_id={8208: {"invoice_date": "2026-03-19", "invoice_origin": "PO/2026/0008"}},
            purchase_line_product_map={8308: 908},
            purchase_line_po_name_map={8308: "PO/2026/0008"},
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual([(line.role, line.side, line.account_code, line.amount) for line in rows[0].planned_lines], [
            ("problem_2103006", "debit", "2103006", 100.0),
            ("selisih_hpp", "credit", "5101003", 100.0),
        ])

    def test_build_pcb_case2_repair_rows_case4_bill_only_marks_review_required(self) -> None:
        service = SvlDashboardServiceAsync(rpc=_FakeDashboardRpc(), logger=logging.getLogger("test.dashboard"))

        rows = service._build_pcb_case2_repair_rows(
            pcb_case="case4",
            cycle_status="problem",
            picking_id=7014,
            picking_name="CBG/IN/00824",
            gr_date="2026-03-26",
            partner_name="Vendor Alpha",
            po_names=["PO/2026/0014"],
            bill_move_ids=[8214],
            item_rows=[
                SvlDashboardCycleItemRow(
                    product_id=914,
                    product_name="Produk Bill Only",
                    default_code="SKU-014",
                    has_item_bill=True,
                    has_item_stj=False,
                    stock_move_ids=[],
                    stj_move_ids=[],
                    stj_refs=[],
                    account_rows=[
                        SvlDashboardCycleAccountRow(
                            account_id=314,
                            code="2103006",
                            name="Hutang Suspend",
                            account_type="liability_current",
                            account_group="liability",
                            debit=0.0,
                            credit=100.0,
                            net_balance=-100.0,
                            status="problem",
                        ),
                        SvlDashboardCycleAccountRow(
                            account_id=414,
                            code="1105003",
                            name="Persediaan Makanan",
                            account_type="asset_current",
                            account_group="asset",
                            debit=100.0,
                            credit=0.0,
                            net_balance=100.0,
                            status="acceptable",
                        ),
                    ],
                )
            ],
            raw_lines=[{"jenis": "BILL", "tipe_akun": "expense_direct_cost", "akun_code": "5101003"}],
            lines_by_move={
                8214: [
                    {
                        "id": 9114,
                        "account_id": [514, "HPP Makanan"],
                        "product_id": [914, "Produk Bill Only"],
                        "purchase_line_id": [8314, "PO Line 14"],
                    }
                ]
            },
            account_info_map={514: {"code": "5101003", "account_type": "expense_direct_cost"}},
            move_info_map={8214: {"name": "BILL/2026/0014", "partner_id": [77, "Vendor Alpha"]}},
            product_info_map={
                914: {
                    "default_code": "SKU-014",
                    "name": "Produk Bill Only",
                    "categ_name": "Food",
                    "valuation_account_code": "1105003",
                    "valuation_account_name": "Persediaan Makanan",
                    "expense_account_code": "5101003",
                    "expense_account_name": "HPP Makanan",
                }
            },
            bill_rows_by_id={8214: {"invoice_date": "2026-03-26", "invoice_origin": "PO/2026/0014"}},
            purchase_line_product_map={8314: 914},
            purchase_line_po_name_map={8314: "PO/2026/0014"},
        )

        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0].review_required)
        self.assertFalse(rows[0].review_confirmed)
        self.assertIn("review", rows[0].review_reason.lower())
        self.assertEqual([(line.role, line.side, line.account_code, line.amount) for line in rows[0].planned_lines], [
            ("problem_2103006", "debit", "2103006", 100.0),
            ("selisih_hpp", "credit", "5101003", 100.0),
        ])

    def test_build_pcb_case2_repair_rows_case4_uses_verified_audit_linked_clearing_before_hpp(self) -> None:
        service = SvlDashboardServiceAsync(rpc=_FakeDashboardRpc(), logger=logging.getLogger("test.dashboard"))

        rows = service._build_pcb_case2_repair_rows(
            pcb_case="case4",
            cycle_status="problem",
            picking_id=7013,
            picking_name="CBG/IN/00823",
            gr_date="2026-03-26",
            partner_name="Vendor Alpha",
            po_names=["PO/2026/0013"],
            bill_move_ids=[8213],
            item_rows=[
                SvlDashboardCycleItemRow(
                    product_id=913,
                    product_name="Produk Case 4 Audit",
                    default_code="SKU-013",
                    has_item_bill=True,
                    has_item_stj=False,
                    verified_audit_clearing_amount=100.0,
                    eligible_case34=True,
                    account_rows=[
                        SvlDashboardCycleAccountRow(
                            account_id=313,
                            code="2103006",
                            name="Hutang Suspend",
                            account_type="liability_current",
                            account_group="liability",
                            debit=0.0,
                            credit=100.0,
                            net_balance=-100.0,
                            status="problem",
                        ),
                        SvlDashboardCycleAccountRow(
                            account_id=413,
                            code="1105003",
                            name="Persediaan Makanan",
                            account_type="asset_current",
                            account_group="asset",
                            debit=100.0,
                            credit=0.0,
                            net_balance=100.0,
                            status="acceptable",
                        ),
                    ],
                    adjustment_audit_rows=[
                        SvlDashboardPcbAdjustmentAuditRow(
                            move_id=9913,
                            move_name="RAC/2026/01/0031",
                            move_date="2026-03-26",
                            move_ref="Verified case4 clearing",
                            product_id=913,
                            item_code="SKU-013",
                            item_name="Produk Case 4 Audit",
                            source_account_code="5101010",
                            source_account_name="Selisih HPP / COGS Variance",
                            clearing_account_code="1108099",
                            repair_clearing_amount=100.0,
                            verified_for_case34=True,
                            matched_basis="exact.purchase_line_id",
                            ambiguous=False,
                        )
                    ],
                )
            ],
            raw_lines=[{"jenis": "BILL", "tipe_akun": "expense_direct_cost", "akun_code": "5101003"}],
            lines_by_move={
                8213: [
                    {
                        "id": 9113,
                        "account_id": [513, "HPP Makanan"],
                        "product_id": [913, "Produk Case 4 Audit"],
                        "purchase_line_id": [8313, "PO Line 13"],
                    }
                ]
            },
            account_info_map={513: {"code": "5101003", "account_type": "expense_direct_cost"}},
            move_info_map={8213: {"name": "BILL/2026/0013", "partner_id": [77, "Vendor Alpha"]}},
            product_info_map={
                913: {
                    "default_code": "SKU-013",
                    "name": "Produk Case 4 Audit",
                    "categ_name": "Food",
                    "valuation_account_code": "1105003",
                    "valuation_account_name": "Persediaan Makanan",
                    "expense_account_code": "5101003",
                    "expense_account_name": "HPP Makanan",
                }
            },
            bill_rows_by_id={8213: {"invoice_date": "2026-03-26", "invoice_origin": "PO/2026/0013"}},
            purchase_line_product_map={8313: 913},
            purchase_line_po_name_map={8313: "PO/2026/0013"},
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual([(line.role, line.side, line.account_code, line.amount) for line in rows[0].planned_lines], [
            ("problem_2103006", "debit", "2103006", 100.0),
            ("audit_clearing_1108099", "credit", "1108099", 100.0),
        ])

    def test_build_pcb_case2_repair_rows_marks_case4_missing_expense_account_as_guarded(self) -> None:
        service = SvlDashboardServiceAsync(rpc=_FakeDashboardRpc(), logger=logging.getLogger("test.dashboard"))

        rows = service._build_pcb_case2_repair_rows(
            pcb_case="case4",
            cycle_status="problem",
            picking_id=7009,
            picking_name="CBG/IN/00745",
            gr_date="2026-03-18",
            partner_name="Vendor Alpha",
            po_names=["PO/2026/0009"],
            bill_move_ids=[8209],
            item_rows=[
                SvlDashboardCycleItemRow(
                    product_id=909,
                    product_name="Produk Missing Expense",
                    default_code="SKU-009",
                    account_rows=[
                        SvlDashboardCycleAccountRow(
                            account_id=309,
                            code="2103006",
                            name="Hutang Suspend",
                            account_type="liability_current",
                            account_group="liability",
                            debit=0.0,
                            credit=100.0,
                            net_balance=-100.0,
                            status="problem",
                        )
                    ],
                )
            ],
            raw_lines=[],
            lines_by_move={8209: []},
            account_info_map={},
            move_info_map={8209: {"name": "BILL/2026/0009", "partner_id": [77, "Vendor Alpha"]}},
            product_info_map={
                909: {
                    "default_code": "SKU-009",
                    "name": "Produk Missing Expense",
                    "categ_name": "Food",
                    "valuation_account_code": "",
                    "valuation_account_name": "",
                    "expense_account_code": "",
                    "expense_account_name": "",
                }
            },
            bill_rows_by_id={8209: {"invoice_date": "2026-03-19", "invoice_origin": "PO/2026/0009"}},
            purchase_line_product_map={},
            purchase_line_po_name_map={},
        )

        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0].review_required)
        self.assertFalse(rows[0].review_confirmed)
        self.assertEqual(rows[0].review_reason, "")
        self.assertIn("missing_expense_account", rows[0].guard_flags)

    def test_build_pcb_case2_repair_rows_skips_item_without_problem_account_balance(self) -> None:
        service = SvlDashboardServiceAsync(rpc=_FakeDashboardRpc(), logger=logging.getLogger("test.dashboard"))

        rows = service._build_pcb_case2_repair_rows(
            cycle_status="problem",
            picking_id=7003,
            picking_name="LHPK/IN/7003",
            gr_date="2026-03-18",
            partner_name="Vendor Alpha",
            po_names=["PO/2026/0003"],
            bill_move_ids=[8203],
            item_rows=[
                SvlDashboardCycleItemRow(
                    product_id=903,
                    product_name="Produk Skip",
                    default_code="SKU-003",
                    account_rows=[
                        SvlDashboardCycleAccountRow(
                            account_id=403,
                            code="5101003",
                            name="HPP Produk",
                            account_type="expense_direct_cost",
                            account_group="expense",
                            debit=50.0,
                            credit=0.0,
                            net_balance=50.0,
                            status="problem",
                        ),
                        SvlDashboardCycleAccountRow(
                            account_id=404,
                            code="1105003",
                            name="Persediaan Produk",
                            account_type="asset_current",
                            account_group="asset",
                            debit=50.0,
                            credit=0.0,
                            net_balance=50.0,
                            status="acceptable",
                        ),
                    ],
                )
            ],
            raw_lines=[{"jenis": "BILL", "tipe_akun": "expense_direct_cost", "akun_code": "5101003"}],
            lines_by_move={
                8203: [
                    {
                        "id": 9103,
                        "account_id": [503, "HPP Produk"],
                        "product_id": [903, "Produk Skip"],
                        "purchase_line_id": [8303, "PO Line 3"],
                    }
                ]
            },
            account_info_map={503: {"code": "5101003", "account_type": "expense_direct_cost"}},
            move_info_map={8203: {"name": "BILL/2026/0003", "partner_id": [77, "Vendor Alpha"]}},
            product_info_map={
                903: {
                    "default_code": "SKU-003",
                    "name": "Produk Skip",
                    "categ_name": "Makanan",
                    "valuation_account_code": "1105003",
                    "valuation_account_name": "Persediaan Produk",
                    "expense_account_code": "5101003",
                    "expense_account_name": "HPP Produk",
                }
            },
            bill_rows_by_id={8203: {"invoice_date": "2026-03-19", "invoice_origin": "PO/2026/0003"}},
            purchase_line_product_map={8303: 903},
            purchase_line_po_name_map={8303: "PO/2026/0003"},
        )

        self.assertEqual(rows, [])

    def test_build_pcb_case2_repair_rows_case4_carries_relation_trace_fields(self) -> None:
        service = SvlDashboardServiceAsync(rpc=_FakeDashboardRpc(), logger=logging.getLogger("test.dashboard"))

        rows = service._build_pcb_case2_repair_rows(
            pcb_case="case4",
            cycle_status="problem",
            picking_id=7001,
            picking_ids=[7001],
            picking_name="CBG/IN/00678",
            gr_date="2026-01-06",
            partner_name="Vendor Alpha",
            po_names=["PO/CB/2025/12/00652"],
            bill_move_ids=[8201],
            payment_move_ids=[8501],
            bank_move_ids=[8601],
            all_cycle_move_ids=[8201, 8501, 8601, 8801],
            item_rows=[
                SvlDashboardCycleItemRow(
                    product_id=901,
                    product_name="BOMBAY SAPHIRE",
                    default_code="A-SPGN-0001",
                    bill_move_ids=[8201],
                    bill_refs=["BILL/2026/01/0077"],
                    purchase_line_ids=[8301],
                    has_item_bill=True,
                    stock_move_ids=[8402, 8401],
                    stj_move_ids=[8802, 8801],
                    stj_refs=["STJ/2025/12/1170", "STJ/2025/12/1168"],
                    has_item_stj=True,
                    account_rows=[
                        SvlDashboardCycleAccountRow(
                            account_id=301,
                            code="2103006",
                            name="Hutang Suspend",
                            account_type="liability_current",
                            account_group="liability",
                            debit=4200000.0,
                            credit=0.0,
                            net_balance=4200000.0,
                            status="problem",
                        )
                    ],
                )
            ],
            raw_lines=[],
            lines_by_move={
                8201: [
                    {
                        "id": 91001,
                        "product_uom_id": [11, "PCS"],
                        "product_id": [901, "BOMBAY SAPHIRE"],
                        "purchase_line_id": [8301, "PO Line 8301"],
                        "price_unit": 350000.0,
                        "quantity": 12.0,
                        "price_subtotal": 4200000.0,
                        "currency_id": [13, "IDR"],
                        "amount_currency": 4200000.0,
                        "analytic_distribution": {"CC-01": 100.0},
                    },
                    {
                        "id": 91002,
                        "move_id": [8201, "BILL/2026/01/0077"],
                        "account_id": [301, "Hutang Suspend"],
                        "product_id": [901, "BOMBAY SAPHIRE"],
                        "purchase_line_id": [8399, "PO Line 8399"],
                        "partner_id": [77, "Vendor Alpha"],
                    },
                ],
                8801: [
                    {
                        "id": 91003,
                        "move_id": [8801, "STJ/2025/12/1168"],
                        "account_id": [302, "Clearing"],
                        "product_id": [901, "BOMBAY SAPHIRE"],
                        "purchase_line_id": [8301, "PO Line 8301"],
                        "stock_move_id": [8401, "MOVE/8401"],
                        "partner_id": [77, "Vendor Alpha"],
                    }
                ]
            },
            account_info_map={
                301: {"code": "2103006"},
                302: {"code": "1108099"},
            },
            move_info_map={
                8201: {"name": "BILL/2026/01/0077", "partner_id": [77, "Vendor Alpha"]},
                8801: {"name": "STJ/2025/12/1168"},
                8802: {"name": "STJ/2025/12/1170"},
            },
            product_info_map={
                901: {
                    "default_code": "A-SPGN-0001",
                    "name": "BOMBAY SAPHIRE",
                    "categ_name": "GIN",
                    "valuation_account_code": "1105001",
                    "valuation_account_name": "Persediaan Alkohol",
                    "expense_account_code": "5101001",
                    "expense_account_name": "HPP Minuman",
                }
            },
            bill_rows_by_id={8201: {"invoice_date": "2026-01-06", "invoice_origin": "PO/CB/2025/12/00652"}},
            purchase_line_product_map={8301: 901},
            purchase_line_po_name_map={8301: "PO/CB/2025/12/00652"},
            payment_move_ids_by_product={901: [8501]},
            bank_move_ids_by_product={901: [8601]},
            stock_move_rows_by_id={
                8401: {
                    "id": 8401,
                    "product_id": [901, "BOMBAY SAPHIRE"],
                    "picking_id": [7001, "CBG/IN/00678"],
                    "purchase_line_id": [8301, "PO Line 8301"],
                    "price_unit": 300000.0,
                },
                8402: {
                    "id": 8402,
                    "product_id": [901, "BOMBAY SAPHIRE"],
                    "picking_id": [7001, "CBG/IN/00678"],
                    "purchase_line_id": [8302, "PO Line 8302"],
                },
            },
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].bill_line_id, 91001)
        self.assertEqual(rows[0].purchase_line_id, 8301)
        self.assertEqual(rows[0].stock_move_id, 8401)
        self.assertEqual(rows[0].stock_move_ids, [8401, 8402])
        self.assertEqual(rows[0].stj_move_ids, [8801, 8802])
        self.assertEqual(rows[0].stj_refs, ["STJ/2025/12/1168", "STJ/2025/12/1170"])
        self.assertEqual(rows[0].payment_move_ids, [8501])
        self.assertEqual(rows[0].bank_move_ids, [8601])
        self.assertEqual(rows[0].suspend_target_aml_ids, [91002])
        self.assertEqual(rows[0].clearing_target_aml_ids, [91003])
        self.assertEqual(rows[0].product_uom_id, 11)
        self.assertEqual(rows[0].quantity, 12.0)
        self.assertEqual(rows[0].currency_id, 13)
        self.assertEqual(rows[0].amount_currency, 4200000.0)
        self.assertEqual(rows[0].analytic_distribution, {"CC-01": 100.0})
        self.assertEqual(rows[0].bill_price_unit, 350000.0)
        self.assertEqual(rows[0].gr_price_unit, 300000.0)
        self.assertEqual(rows[0].price_gap_value, 600000.0)
        self.assertEqual(rows[0].allocated_amount, 4200000.0)

    def test_build_pcb_case2_repair_rows_case2_keeps_trace_scoped_and_target_hints_empty(self) -> None:
        service = SvlDashboardServiceAsync(rpc=_FakeDashboardRpc(), logger=logging.getLogger("test.dashboard"))

        rows = service._build_pcb_case2_repair_rows(
            pcb_case="case2",
            cycle_status="problem",
            picking_id=7001,
            picking_ids=[7001],
            picking_name="CBG/IN/00678",
            gr_date="2026-01-06",
            partner_name="Vendor Alpha",
            po_names=["PO/CB/2025/12/00652"],
            bill_move_ids=[8201],
            payment_move_ids=[8501],
            bank_move_ids=[8601],
            all_cycle_move_ids=[8201, 8501, 8601],
            item_rows=[
                SvlDashboardCycleItemRow(
                    product_id=901,
                    product_name="BOMBAY SAPHIRE",
                    default_code="A-SPGN-0001",
                    bill_move_ids=[8201],
                    bill_refs=["BILL/2026/01/0077"],
                    purchase_line_ids=[8301],
                    has_item_bill=True,
                    stock_move_ids=[8401],
                    stj_move_ids=[8801],
                    stj_refs=["STJ/2025/12/1168"],
                    has_item_stj=True,
                    account_rows=[
                        SvlDashboardCycleAccountRow(
                            account_id=301,
                            code="2103006",
                            name="Hutang Suspend",
                            account_type="liability_current",
                            account_group="liability",
                            debit=120.0,
                            credit=0.0,
                            net_balance=120.0,
                            status="problem",
                        ),
                        SvlDashboardCycleAccountRow(
                            account_id=401,
                            code="5101001",
                            name="HPP Minuman",
                            account_type="expense_direct_cost",
                            account_group="expense",
                            debit=0.0,
                            credit=70.0,
                            net_balance=-70.0,
                            status="acceptable",
                        ),
                    ],
                )
            ],
            raw_lines=[
                {
                    "jenis": "BILL",
                    "tipe_akun": "expense_direct_cost",
                    "akun_code": "5101001",
                }
            ],
            lines_by_move={
                8201: [
                    {
                        "id": 91001,
                        "account_id": [401, "HPP Minuman"],
                        "product_id": [901, "BOMBAY SAPHIRE"],
                        "purchase_line_id": [8301, "PO Line 8301"],
                    }
                ]
            },
            account_info_map={
                401: {
                    "code": "5101001",
                    "name": "HPP Minuman",
                    "account_type": "expense_direct_cost",
                }
            },
            move_info_map={
                8201: {"name": "BILL/2026/01/0077", "partner_id": [77, "Vendor Alpha"]},
            },
            product_info_map={
                901: {
                    "default_code": "A-SPGN-0001",
                    "name": "BOMBAY SAPHIRE",
                    "categ_name": "GIN",
                    "valuation_account_code": "1105001",
                    "valuation_account_name": "Persediaan Alkohol",
                    "expense_account_code": "5101001",
                    "expense_account_name": "HPP Minuman",
                }
            },
            bill_rows_by_id={8201: {"invoice_date": "2026-01-06", "invoice_origin": "PO/CB/2025/12/00652"}},
            purchase_line_product_map={8301: 901},
            purchase_line_po_name_map={8301: "PO/CB/2025/12/00652"},
            payment_move_ids_by_product={},
            bank_move_ids_by_product={},
            stock_move_rows_by_id={
                8401: {
                    "id": 8401,
                    "product_id": [901, "BOMBAY SAPHIRE"],
                    "picking_id": [7001, "CBG/IN/00678"],
                    "purchase_line_id": [8301, "PO Line 8301"],
                }
            },
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].payment_move_ids, [])
        self.assertEqual(rows[0].bank_move_ids, [])
        self.assertEqual(rows[0].suspend_target_aml_ids, [])
        self.assertEqual(rows[0].clearing_target_aml_ids, [])

    def test_build_pcb_case2_repair_rows_case3_builds_target_hints_from_bill_and_stj(self) -> None:
        service = SvlDashboardServiceAsync(rpc=_FakeDashboardRpc(), logger=logging.getLogger("test.dashboard"))

        rows = service._build_pcb_case2_repair_rows(
            pcb_case="case3",
            cycle_status="problem",
            picking_id=7001,
            picking_ids=[7001],
            picking_name="CBG/IN/00678",
            gr_date="2026-01-06",
            partner_name="Vendor Alpha",
            po_names=["PO/CB/2025/12/00652"],
            bill_move_ids=[8201],
            payment_move_ids=[8501],
            bank_move_ids=[8601],
            all_cycle_move_ids=[8201, 8501, 8601, 8801],
            item_rows=[
                SvlDashboardCycleItemRow(
                    product_id=901,
                    product_name="BOMBAY SAPHIRE",
                    default_code="A-SPGN-0001",
                    bill_move_ids=[8201],
                    bill_refs=["BILL/2026/01/0077"],
                    purchase_line_ids=[8301],
                    has_item_bill=True,
                    stock_move_ids=[8401],
                    stj_move_ids=[8801],
                    stj_refs=["STJ/2025/12/1168"],
                    has_item_stj=True,
                    account_rows=[
                        SvlDashboardCycleAccountRow(
                            account_id=301,
                            code="2103006",
                            name="Hutang Suspend",
                            account_type="liability_current",
                            account_group="liability",
                            debit=4200000.0,
                            credit=0.0,
                            net_balance=4200000.0,
                            status="problem",
                        ),
                        SvlDashboardCycleAccountRow(
                            account_id=302,
                            code="1108099",
                            name="Clearing",
                            account_type="asset_current",
                            account_group="asset",
                            debit=0.0,
                            credit=700000.0,
                            net_balance=-700000.0,
                            status="problem",
                        ),
                    ],
                )
            ],
            raw_lines=[],
            lines_by_move={
                8201: [
                    {
                        "id": 91002,
                        "move_id": [8201, "BILL/2026/01/0077"],
                        "account_id": [301, "Hutang Suspend"],
                        "product_id": [901, "BOMBAY SAPHIRE"],
                        "purchase_line_id": [8301, "PO Line 8301"],
                        "partner_id": [77, "Vendor Alpha"],
                    }
                ],
                8801: [
                    {
                        "id": 91003,
                        "move_id": [8801, "STJ/2025/12/1168"],
                        "account_id": [302, "Clearing"],
                        "product_id": [901, "BOMBAY SAPHIRE"],
                        "purchase_line_id": [8301, "PO Line 8301"],
                        "stock_move_id": [8401, "MOVE/8401"],
                        "partner_id": [77, "Vendor Alpha"],
                    }
                ],
            },
            account_info_map={
                301: {"code": "2103006"},
                302: {"code": "1108099"},
            },
            move_info_map={
                8201: {"name": "BILL/2026/01/0077", "partner_id": [77, "Vendor Alpha"]},
                8801: {"name": "STJ/2025/12/1168"},
            },
            product_info_map={
                901: {
                    "default_code": "A-SPGN-0001",
                    "name": "BOMBAY SAPHIRE",
                    "categ_name": "GIN",
                    "valuation_account_code": "1105001",
                    "valuation_account_name": "Persediaan Alkohol",
                    "expense_account_code": "5101001",
                    "expense_account_name": "HPP Minuman",
                }
            },
            bill_rows_by_id={8201: {"invoice_date": "2026-01-06", "invoice_origin": "PO/CB/2025/12/00652"}},
            purchase_line_product_map={8301: 901},
            purchase_line_po_name_map={8301: "PO/CB/2025/12/00652"},
            payment_move_ids_by_product={901: [8501]},
            bank_move_ids_by_product={901: [8601]},
            stock_move_rows_by_id={
                8401: {
                    "id": 8401,
                    "product_id": [901, "BOMBAY SAPHIRE"],
                    "picking_id": [7001, "CBG/IN/00678"],
                    "purchase_line_id": [8301, "PO Line 8301"],
                    "price_unit": 300000.0,
                }
            },
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].suspend_target_aml_ids, [91002])
        self.assertEqual(rows[0].clearing_target_aml_ids, [91003])

    def test_build_pcb_case2_repair_rows_case4_purchase_line_falls_back_after_primary_stock_move_pick(self) -> None:
        service = SvlDashboardServiceAsync(rpc=_FakeDashboardRpc(), logger=logging.getLogger("test.dashboard"))

        rows = service._build_pcb_case2_repair_rows(
            pcb_case="case4",
            cycle_status="problem",
            picking_id=7001,
            picking_ids=[7001],
            picking_name="CBG/IN/00678",
            gr_date="2026-01-06",
            partner_name="Vendor Alpha",
            po_names=["PO/CB/2025/12/00652"],
            bill_move_ids=[8201],
            item_rows=[
                SvlDashboardCycleItemRow(
                    product_id=901,
                    product_name="BOMBAY SAPHIRE",
                    default_code="A-SPGN-0001",
                    bill_move_ids=[8201],
                    bill_refs=["BILL/2026/01/0077"],
                    purchase_line_ids=[8309],
                    has_item_bill=True,
                    stock_move_ids=[8401, 8402],
                    stj_move_ids=[8801],
                    stj_refs=["STJ/2025/12/1168"],
                    has_item_stj=True,
                    account_rows=[
                        SvlDashboardCycleAccountRow(
                            account_id=301,
                            code="2103006",
                            name="Hutang Suspend",
                            account_type="liability_current",
                            account_group="liability",
                            debit=4200000.0,
                            credit=0.0,
                            net_balance=4200000.0,
                            status="problem",
                        )
                    ],
                )
            ],
            raw_lines=[],
            lines_by_move={
                8201: [
                    {
                        "id": 91009,
                        "product_id": [901, "BOMBAY SAPHIRE"],
                        "purchase_line_id": [8309, "PO Line 8309"],
                    }
                ]
            },
            account_info_map={},
            move_info_map={
                8201: {"name": "BILL/2026/01/0077", "partner_id": [77, "Vendor Alpha"]},
                8801: {"name": "STJ/2025/12/1168"},
            },
            product_info_map={
                901: {
                    "default_code": "A-SPGN-0001",
                    "name": "BOMBAY SAPHIRE",
                    "categ_name": "GIN",
                    "valuation_account_code": "1105001",
                    "valuation_account_name": "Persediaan Alkohol",
                    "expense_account_code": "5101001",
                    "expense_account_name": "HPP Minuman",
                }
            },
            bill_rows_by_id={8201: {"invoice_date": "2026-01-06", "invoice_origin": "PO/CB/2025/12/00652"}},
            purchase_line_product_map={8309: 901},
            purchase_line_po_name_map={8309: "PO/CB/2025/12/00652"},
            stock_move_rows_by_id={
                8401: {
                    "id": 8401,
                    "product_id": [901, "BOMBAY SAPHIRE"],
                    "picking_id": [7001, "CBG/IN/00678"],
                    "purchase_line_id": False,
                },
                8402: {
                    "id": 8402,
                    "product_id": [901, "BOMBAY SAPHIRE"],
                    "picking_id": [7001, "CBG/IN/00678"],
                    "purchase_line_id": [8308, "PO Line 8308"],
                },
            },
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].stock_move_id, 8401)
        self.assertEqual(rows[0].purchase_line_id, 8309)
        self.assertEqual(rows[0].bill_line_id, 91009)

    def test_match_pcb_case1_target_line_ids_scopes_suspend_to_bill_and_clearing_to_stj(self) -> None:
        service = SvlDashboardServiceAsync(rpc=_FakeDashboardRpc(), logger=logging.getLogger("test.dashboard"))

        suspend_targets, clearing_targets = service._match_pcb_case1_target_line_ids(
            all_cycle_move_ids=[8001, 8801, 8901],
            stj_move_ids=[8801],
            lines_by_move={
                8001: [
                    {
                        "id": 9102,
                        "move_id": [8001, "BILL/2026/0001"],
                        "account_id": [301, "Hutang Suspend"],
                        "product_id": [101, "Produk A"],
                        "purchase_line_id": [3001, "PO Line A"],
                    }
                ],
                8801: [
                    {
                        "id": 9101,
                        "move_id": [8801, "STJ/2026/0451"],
                        "account_id": [302, "Clearing"],
                        "product_id": [101, "Produk A"],
                        "purchase_line_id": [3001, "PO Line A"],
                        "stock_move_id": [8401, "MOVE/8401"],
                    }
                ],
                8901: [
                    {
                        "id": 9199,
                        "move_id": [8901, "PBK/2026/0001"],
                        "account_id": [302, "Clearing"],
                        "product_id": [101, "Produk A"],
                        "purchase_line_id": [3001, "PO Line A"],
                        "stock_move_id": [8401, "MOVE/8401"],
                    }
                ],
            },
            account_info_map={
                301: {"code": "2103006"},
                302: {"code": "1108099"},
            },
            product_id=101,
            purchase_line_id=3001,
            bill_move_id=8001,
            stock_move_id=8401,
        )

        self.assertEqual(suspend_targets, [9102])
        self.assertEqual(clearing_targets, [9101])

    def test_match_pcb_case1_target_line_ids_falls_back_to_move_scope_when_link_fields_missing(self) -> None:
        service = SvlDashboardServiceAsync(rpc=_FakeDashboardRpc(), logger=logging.getLogger("test.dashboard"))

        suspend_targets, clearing_targets = service._match_pcb_case1_target_line_ids(
            all_cycle_move_ids=[8001, 8801],
            stj_move_ids=[8801],
            lines_by_move={
                8001: [
                    {
                        "id": 9102,
                        "move_id": [8001, "BILL/2026/0001"],
                        "account_id": [301, "Hutang Suspend"],
                        "partner_id": [77, "Vendor Alpha"],
                    }
                ],
                8801: [
                    {
                        "id": 9101,
                        "move_id": [8801, "STJ/2026/0451"],
                        "account_id": [302, "Clearing"],
                        "partner_id": [77, "Vendor Alpha"],
                    }
                ],
            },
            account_info_map={
                301: {"code": "2103006"},
                302: {"code": "1108099"},
            },
            product_id=101,
            purchase_line_id=3001,
            bill_move_id=8001,
            stock_move_id=8401,
            partner_id=77,
        )

        self.assertEqual(suspend_targets, [9102])
        self.assertEqual(clearing_targets, [9101])

    def test_pcb_case1_direction_keeps_suspend_minus_as_debit_suspend_credit_clearing(self) -> None:
        service = SvlDashboardServiceAsync(rpc=_FakeDashboardRpc(), logger=logging.getLogger("test.dashboard"))

        is_case1, debit_code, credit_code, cycle_amount = service._pcb_case1_direction(_build_case1_account_rows())

        self.assertTrue(is_case1)
        self.assertEqual(debit_code, "2103006")
        self.assertEqual(credit_code, "1108099")
        self.assertEqual(cycle_amount, 300.0)


if __name__ == "__main__":
    unittest.main()
