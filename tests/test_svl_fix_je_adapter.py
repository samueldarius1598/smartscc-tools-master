import inspect
import json
import logging
import tempfile
import tkinter as tk
import unittest
from collections import OrderedDict
from datetime import date
from pathlib import Path
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText
from unittest import mock

from smartscc_tools.features.item_journal.utils import normalize_text
from smartscc_tools.core import theme as T
from smartscc_tools.services.odoo.profiles import FOLLOW_GLOBAL_PROFILE_ID
from smartscc_tools.core.global_config import GlobalPersistentState, GlobalSettings, InventoryCoaEntry, RepairAccountEntry
from smartscc_tools.core.module_base import ModuleContext
from smartscc_tools.modules.svl_fix_je_dashboard_page import (
    DETAIL_TAB_COLORS,
    REPAIR_DIALOG_SELECTION_ALL,
    SvlFixJeDashboardPage,
)
from smartscc_tools.modules.svl_fix_je_module import SvlFixJeModule, _SvlFixJePanel, _SvlFixJeShell, _SvlFixJeStateStore
from smartscc_tools.widgets.collapsible_section import CollapsibleSection
from smartscc_tools.widgets.scrollable_frame import ScrollableFrame
from smartscc_tools.features.svl_fix_je.config import DEFAULT_REF_PREFIX, SvlFixJeSettings
from smartscc_tools.features.svl_fix_je.models import (
    SvlDashboardCompany,
    SvlDashboardCompanySummary,
    SvlDashboardCycleAccountRow,
    SvlDashboardCycleItemRow,
    SvlDashboardInventoryCoaRow,
    SvlDashboardItemDetail,
    SvlDashboardPcbAdjustmentAuditRow,
    SvlDashboardPcbCase1RepairBatchResult,
    SvlDashboardPcbCase1LinkRow,
    SvlDashboardPcbCase1RepairRowResult,
    SvlDashboardPcbCase2RepairBatchResult,
    SvlDashboardPcbCase2RepairRow,
    SvlDashboardPcbCase2RepairRowResult,
    SvlDashboardPcbRepairPlannedLine,
    SvlDashboardRequest,
    SvlDashboardRepairAccountCandidate,
    SvlDashboardRepairProgressSnapshot,
    SvlDashboardRepairRowResult,
    SvlDashboardPurchaseCycle,
    SvlDashboardSnapshot,
    SvlFixJeRowResult,
    SvlFixJeRunSummary,
)


class _FakeVar:
    def __init__(self, value="") -> None:
        self.value = value

    def get(self):
        return self.value

    def set(self, value) -> None:
        self.value = value


class _FakeButton:
    def __init__(self) -> None:
        self.last_config: dict[str, object] = {}
        self.rootx = 20
        self.rooty = 30
        self.height = 24

    def configure(self, **kwargs) -> None:
        self.last_config.update(kwargs)

    def winfo_rootx(self) -> int:
        return self.rootx

    def winfo_rooty(self) -> int:
        return self.rooty

    def winfo_height(self) -> int:
        return self.height


class _FakeMenu:
    def __init__(self) -> None:
        self.post_calls: list[tuple[int, int]] = []
        self.entries: list[dict[str, object]] = []

    def post(self, x: int, y: int) -> None:
        self.post_calls.append((x, y))

    def add_command(self, **kwargs) -> None:
        self.entries.append({"kind": "command", **kwargs})

    def add_cascade(self, **kwargs) -> None:
        self.entries.append({"kind": "cascade", **kwargs})

    def entryconfigure(self, index: int, **kwargs) -> None:
        while len(self.entries) <= index:
            self.entries.append({"kind": "command"})
        self.entries[index].update(kwargs)


class _FakeWidget:
    def __init__(self) -> None:
        self.last_config: dict[str, object] = {}
        self.pack_calls: list[dict[str, object]] = []
        self.pack_forget_calls = 0
        self.event_generate_calls: list[str] = []

    def configure(self, **kwargs) -> None:
        self.last_config.update(kwargs)

    def pack(self, **kwargs) -> None:
        self.pack_calls.append(dict(kwargs))

    def pack_forget(self) -> None:
        self.pack_forget_calls += 1

    def event_generate(self, sequence: str) -> None:
        self.event_generate_calls.append(sequence)

    def cget(self, key: str):
        return self.last_config.get(key)


class _FakeRoot:
    def __init__(self) -> None:
        self.after_calls: list[tuple[int, object]] = []
        self.after_cancel_calls: list[object] = []

    def after(self, delay_ms: int, callback):  # noqa: ANN001
        self.after_calls.append((delay_ms, callback))
        return "after#1"

    def after_cancel(self, after_id) -> None:  # noqa: ANN001
        self.after_cancel_calls.append(after_id)

    def after_idle(self, callback):  # noqa: ANN001
        self.after_calls.append((0, callback))
        return "after#idle"


class _FakeSizedWidget:
    def __init__(self, *, width: int = 0, reqwidth: int = 0) -> None:
        self.width = width
        self.reqwidth = reqwidth

    def winfo_width(self) -> int:
        return self.width

    def winfo_reqwidth(self) -> int:
        return self.reqwidth


class _FakeLayoutWidget(_FakeSizedWidget):
    def __init__(self, *, width: int = 0, reqwidth: int = 0, master=None) -> None:  # noqa: ANN001
        super().__init__(width=width, reqwidth=reqwidth)
        self.master = master
        self.last_config: dict[str, object] = {}
        self.bind_calls: list[tuple[str, object, object]] = []
        self.grid_columns: dict[int, dict[str, object]] = {}

    def configure(self, **kwargs) -> None:
        self.last_config.update(kwargs)

    def cget(self, key: str):
        return self.last_config.get(key)

    def bind(self, sequence: str, callback, add=None) -> None:  # noqa: ANN001
        self.bind_calls.append((sequence, callback, add))

    def grid_columnconfigure(self, index: int, **kwargs):
        if kwargs:
            current = dict(self.grid_columns.get(index, {}))
            current.update(kwargs)
            self.grid_columns[index] = current
        return dict(self.grid_columns.get(index, {}))


class _FakeQueue:
    def __init__(self, values=None) -> None:
        import queue

        self._queue = queue.Queue()
        for item in values or []:
            self._queue.put(item)

    def put(self, value) -> None:
        self._queue.put(value)

    def get_nowait(self):
        return self._queue.get_nowait()


class _FakeSidebarTree:
    def __init__(self, *, selection=(), focus: str = "") -> None:
        self._selection = tuple(selection)
        self._focus = focus

    def selection(self):
        return self._selection

    def focus(self, value=None):  # noqa: ANN001
        if value is not None:
            self._focus = value
        return self._focus

    def selection_set(self, value) -> None:  # noqa: ANN001
        if isinstance(value, (list, tuple)):
            self._selection = tuple(value)
        else:
            self._selection = (value,)


PCB_CASE1_LABEL = "Case 1 - STJ Bill Miss Match (Clearing - Suspend)"
PCB_CASE2_LABEL = "Case 2 - STJ Bill Price Diff (Suspend - Suspend)"
PCB_CASE3_LABEL = "Case 3 - STJ Bill Hit Expenses (Clearing - Expenses)"
PCB_CASE4_LABEL = "Case 4 - STJ Bill Hit Expenses (Suspend - Expenses)"


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


def _pcb_case1_line_label(
    *,
    item_code: str = "",
    item_name: str = "",
    case_label: str = PCB_CASE1_LABEL,
) -> str:
    return " - ".join(part for part in (normalize_text(item_code), normalize_text(item_name), normalize_text(case_label)) if part)

    def put(self, value) -> None:  # noqa: ANN001
        self._queue.put(value)

def _build_pcb_case2_row(**overrides) -> SvlDashboardPcbCase2RepairRow:
    payload = {
        "row_key": "case2::7001::901",
        "cycle_key": "case2::7001",
        "company_id": 0,
        "company_name": "",
        "amount": 100.0,
        "date": "2026-03-18",
        "reference": "",
        "line_label": "",
        "journal_code": "STJ",
        "pcb_case": "case2",
        "pcb_case_label": PCB_CASE2_LABEL,
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
        "picking_name": "LHPK/IN/7001",
        "po_name": "PO/2026/0001",
        "bill_move_id": 8201,
        "bill_name": "BILL/2026/0001",
        "partner_id": 77,
        "partner_name": "Vendor Alpha",
        "product_uom_id": 11,
        "quantity": 2.0,
        "currency_id": 13,
        "amount_currency": 100.0,
        "amount_currency_basis": 94.0,
        "analytic_distribution": {"CC-01": 100.0},
        "bill_price_unit": 50.0,
        "gr_price_unit": 0.0,
        "price_gap_value": 100.0,
        "allocated_amount": 100.0,
        "suspend_account_code": "2103006",
        "inventory_account_code": "1105004",
        "expense_account_code": "5101004",
        "problem_balances_by_code": {"2103006": -100.0},
        "hpp_balances_by_code": {"5101004": 70.0},
        "suspend_balance": -100.0,
        "hpp_balance": 70.0,
        "inventory_balance": 70.0,
        "cogs_variance_balance": 20.0,
        "selisih_hpp_amount": 30.0,
        "external_clearing_amount": 0.0,
        "external_clearing_refs": [],
        "external_clearing_basis": "",
        "external_clearing_verified": False,
        "coefficient_variance": 20.0,
        "bank_balances_by_code": {"1101060": -55.0},
        "bank_account_codes": ["1101060"],
        "guard_flags": ["problem_non_zero:2103006", "hpp_non_zero", "bank_non_zero:1101060"],
        "guard_messages": [
            "Saldo akun problem 2103006 item masih -100.00.",
            "Saldo HPP item (5101004) masih +70.00.",
            "Saldo akun BK/PBK 1101060 pada item masih -55.00.",
        ],
        "planned_lines": [
            SvlDashboardPcbRepairPlannedLine(
                role="problem_2103006",
                account_code="2103006",
                account_name="Hutang Suspend",
                amount=100.0,
                side="debit",
            ),
            SvlDashboardPcbRepairPlannedLine(
                role="hpp_zero",
                account_code="5101004",
                account_name="HPP Rokok",
                amount=70.0,
                side="credit",
                line_label="SKU-001 - Produk PCB - Case 2 - STJ Bill Price Diff (Suspend - Suspend) - Zero HPP",
            ),
            SvlDashboardPcbRepairPlannedLine(
                role="selisih_hpp",
                account_code="5101004",
                account_name="HPP Rokok",
                amount=30.0,
                side="credit",
                line_label="SKU-001 - Produk PCB - Case 2 - STJ Bill Price Diff (Suspend - Suspend) - Selisih HPP",
            ),
        ],
    }
    payload.update(overrides)
    return SvlDashboardPcbCase2RepairRow(**payload)


def _build_pcb_adjustment_audit_row(**overrides) -> SvlDashboardPcbAdjustmentAuditRow:
    payload = {
        "move_id": 9901,
        "move_name": "RAC/2026/01/0026",
        "move_date": "2026-03-27",
        "move_ref": "Adjustment clearing from standard price",
        "product_id": 901,
        "item_code": "SKU-001",
        "item_name": "Produk PCB",
        "source_account_code": "5101010",
        "source_account_name": "Selisih HPP / COGS Variance",
        "clearing_account_code": "1108099",
        "repair_clearing_amount": 100.0,
        "origin_move_id": 8801,
        "origin_move_name": "STJ/2026/01/0690",
        "origin_basis": "explicit.move_ref",
        "origin_product_id": 901,
        "origin_purchase_line_id": 8301,
        "origin_stock_move_id": 8401,
        "verified_for_case34": True,
        "matched_basis": "exact.purchase_line_id",
        "ambiguous": False,
    }
    payload.update(overrides)
    return SvlDashboardPcbAdjustmentAuditRow(**payload)


def _build_pcb_cycle(
    *,
    picking_id: int,
    case1_link_rows: list[SvlDashboardPcbCase1LinkRow] | None = None,
    case2_repair_rows: list[SvlDashboardPcbCase2RepairRow] | None = None,
    account_rows: list[SvlDashboardCycleAccountRow] | None = None,
    item_rows: list[SvlDashboardCycleItemRow] | None = None,
    raw_lines: list[dict[str, object]] | None = None,
    bill_refs: list[str] | None = None,
    cycle_status: str = "problem",
    adjustment_warning_text: str = "",
    adjustment_audit_rows: list[SvlDashboardPcbAdjustmentAuditRow] | None = None,
) -> SvlDashboardPurchaseCycle:
    case1_link_rows = list(case1_link_rows or [])
    case2_repair_rows = list(case2_repair_rows or [])
    if account_rows is None:
        if case2_repair_rows and not case1_link_rows:
            account_rows = [
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
                    code="5101004",
                    name="HPP Rokok",
                    account_type="expense_direct_cost",
                    account_group="expense",
                    debit=100.0,
                    credit=0.0,
                    net_balance=100.0,
                    status="problem",
                ),
            ]
        else:
            account_rows = [
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
                    account_id=302,
                    code="1108099",
                    name="Clearing",
                    account_type="asset_current",
                    account_group="asset",
                    debit=100.0,
                    credit=0.0,
                    net_balance=100.0,
                    status="problem",
                ),
            ]
    if raw_lines is None:
        raw_lines = []
        if case2_repair_rows and not case1_link_rows:
            raw_lines.append(
                {
                    "jenis": "BILL",
                    "tipe_akun": "expense_direct_cost",
                    "akun_code": "5101004",
                }
            )
    if bill_refs is None:
        bill_refs = ["BILL/2026/0001"] if (case1_link_rows or case2_repair_rows) else []
    return SvlDashboardPurchaseCycle(
        picking_id=picking_id,
        picking_name=f"LHPK/IN/{picking_id:04d}",
        gr_date="2026-03-18",
        partner_name="Vendor Alpha",
        bill_refs=list(bill_refs),
        cycle_status=cycle_status,
        adjustment_warning_text=adjustment_warning_text,
        account_rows=list(account_rows),
        item_rows=list(item_rows or []),
        adjustment_audit_rows=list(adjustment_audit_rows or []),
        case1_link_rows=case1_link_rows,
        case2_repair_rows=case2_repair_rows,
        raw_lines=list(raw_lines),
    )


class SvlFixJeAdapterTest(unittest.TestCase):
    @staticmethod
    def _repair_tree_leaf_ids(widgets: dict[str, object]) -> tuple[str, ...]:
        return tuple(widgets["left_leaf_item_ids"]())

    @staticmethod
    def _repair_tree_group_ids(widgets: dict[str, object]) -> tuple[str, ...]:
        return tuple(widgets["left_group_item_ids"]())

    def _select_repair_tree_indices(self, widgets: dict[str, object], *indices: int) -> None:
        tree = widgets["left_list"]
        item_ids = [
            widgets["left_item_id_for_index"](index)
            for index in indices
            if widgets["left_item_id_for_index"](index)
        ]
        tree.selection_set(tuple(item_ids))
        if item_ids:
            tree.focus(item_ids[0])
        tree.event_generate("<<TreeviewSelect>>")
        self.root.update_idletasks()

    def _drain_tk_events(self, *, limit: int = 50) -> None:
        for _ in range(limit):
            self.root.update()

    @staticmethod
    def _build_repair_dialog_rows(
        count: int,
        *,
        account_candidates: list[SvlDashboardRepairAccountCandidate] | None = None,
    ) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for index in range(count):
            rows.append(
                {
                    "row_key": f"row-{index + 1}",
                    "company_id": 7,
                    "item_product_id": 1000 + index,
                    "item_code": f"ITEM-{index + 1:04d}",
                    "item_name": f"Item {index + 1}",
                    "amount": 100.0 + index,
                    "date": "2026-03-18",
                    "journal_code": "STJ",
                    "signed_amount": 100.0 + index,
                    "base_reference": f"ITEM-{index + 1:04d}",
                    "base_line_label": f"ITEM-{index + 1:04d} Item {index + 1}",
                    "move_id": 0,
                    "move_name": "",
                    "move_state": "",
                    "account_candidates": list(account_candidates or []),
                }
            )
        return rows

    def test_state_store_round_trips_settings_and_global_output_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            global_path = Path(tmp_dir) / "global_state.json"
            global_settings = GlobalSettings(default_output_dir=r"C:\shared\output")
            store = _SvlFixJeStateStore(
                global_settings=global_settings,
                global_state_store=GlobalPersistentState(path=global_path),
            )

            settings = store.load()
            self.assertEqual(settings.last_output_dir, r"C:\shared\output")

            store.save(
                SvlFixJeSettings(
                    database_profile_id="db_live",
                    last_excel_file=r"C:\data\svl.xlsx",
                    last_prefix="FIX-A",
                    auto_post=True,
                    max_workers=4,
                    last_output_dir=r"C:\exports",
                    logs_section_open=True,
                    view_mode="dashboard",
                    dashboard_company_id=77,
                    dashboard_date_from="2026-01-01",
                    dashboard_date_to="2026-01-31",
                    dashboard_logs_section_open=True,
                    dashboard_repair_last_target_mode="fill_existing",
                    dashboard_repair_last_posting_mode="post",
                    dashboard_repair_last_target_account_role="valuation",
                    dashboard_repair_last_resolve_account_code="1108099",
                    dashboard_dataset_mode="account_balance",
                    dashboard_include_inventory_accounts=False,
                    dashboard_include_non_inventory_accounts=True,
                )
            )
            saved = json.loads(global_path.read_text(encoding="utf-8"))

        payload = saved["module_settings"]["svl_fix_je"]
        self.assertEqual(payload["database_profile_id"], "db_live")
        self.assertEqual(payload["last_prefix"], "FIX-A")
        self.assertTrue(payload["logs_section_open"])
        self.assertEqual(payload["view_mode"], "dashboard")
        self.assertEqual(payload["dashboard_company_id"], 77)
        self.assertEqual(payload["dashboard_repair_last_target_mode"], "fill_existing")
        self.assertEqual(payload["dashboard_repair_last_target_account_role"], "valuation")
        self.assertEqual(payload["dashboard_repair_last_resolve_account_code"], "1108099")
        self.assertEqual(payload["dashboard_dataset_mode"], "account_balance")
        self.assertFalse(payload["dashboard_include_inventory_accounts"])
        self.assertTrue(payload["dashboard_include_non_inventory_accounts"])

    def test_module_create_ui_uses_shell(self) -> None:
        module = SvlFixJeModule()
        context = ModuleContext(
            global_settings=GlobalSettings(),
            auth_info=None,
            logger=logging.getLogger("test.svl_fix.adapter"),
            technical_logger=logging.getLogger("test.svl_fix.adapter.tech"),
            root=object(),
        )
        parent = object()

        with mock.patch("smartscc_tools.modules.svl_fix_je_module._SvlFixJeShell") as panel_cls:
            result = module.create_ui(parent, context)

        self.assertIs(result, parent)
        panel_cls.assert_called_once()

    def test_module_display_name_uses_fixing_unlink_svl_label(self) -> None:
        self.assertEqual(SvlFixJeModule().display_name, "Fixing Unlink SVL - Odoo")

    def test_panel_resolves_database_choice_precedence(self) -> None:
        panel = _SvlFixJePanel.__new__(_SvlFixJePanel)
        panel.context = mock.Mock(
            global_settings=GlobalSettings(
                database_profiles=[],
                default_database_profile_id="",
            )
        )
        panel._module_settings = SvlFixJeSettings(database_profile_id="hwgroup_erp")

        self.assertEqual(panel._resolve_database("default-db"), "hwgroup_erp")

        panel._module_settings.database_profile_id = FOLLOW_GLOBAL_PROFILE_ID
        panel.context.global_settings = GlobalSettings(
            database_profiles=[],
            default_database_profile_id="global-db",
        )
        self.assertEqual(panel._resolve_database("default-db"), "default-db")

        panel.context.global_settings = GlobalSettings()
        self.assertEqual(panel._resolve_database("default-db"), "default-db")

    def test_start_run_execute_respects_confirmation_gate(self) -> None:
        panel = _SvlFixJePanel.__new__(_SvlFixJePanel)
        panel.excel_path_var = _FakeVar(r"C:\data\svl.xlsx")
        panel.worker = None
        panel._confirm_execute = lambda: False
        panel._save_module_settings = lambda: None
        panel._apply_summary = lambda summary: None  # noqa: ARG005
        panel.status_var = _FakeVar("")
        panel.ui_queue = _FakeQueue()

        with mock.patch("smartscc_tools.modules.svl_fix_je_module.threading.Thread") as thread_cls:
            panel._start_run("execute")

        thread_cls.assert_not_called()

    def test_handle_completed_state_enables_export_button(self) -> None:
        panel = _SvlFixJePanel.__new__(_SvlFixJePanel)
        panel.btn_export = _FakeButton()
        panel.summary_var = _FakeVar("")
        panel.status_var = _FakeVar("")
        panel.context = mock.Mock(status_callback=lambda message: None)
        panel._latest_summary = None
        panel._active_service = object()
        panel._set_busy = lambda busy: None  # noqa: ARG005

        summary = SvlFixJeRunSummary(mode="validate", database="hwgroup_erp", total_rows=1)
        summary.results.append(SvlFixJeRowResult(row_number=2, status="VALID"))
        panel._handle_ui_event(
            {
                "type": "state",
                "status": "completed",
                "message": "Selesai",
                "summary": summary,
                "database": "hwgroup_erp",
            }
        )

        self.assertIs(panel._latest_summary, summary)
        self.assertEqual(panel.btn_export.last_config["state"], "normal")
        self.assertIn("Mode: VALIDATE", panel.summary_var.get())

    def test_shell_label_for_mode_restores_dashboard_option(self) -> None:
        shell = _SvlFixJeShell.__new__(_SvlFixJeShell)

        self.assertEqual(shell._label_for_mode("dashboard"), "Dashboard Control")
        self.assertEqual(shell._label_for_mode("unknown"), "Upload")

    def test_shell_bulk_collect_all_svl_without_je_delegates_to_dashboard_page(self) -> None:
        shell = _SvlFixJeShell.__new__(_SvlFixJeShell)
        shell.dashboard_page = mock.Mock()

        shell._on_bulk_collect_all_svl_without_je(filter_automated_only=True)

        shell.dashboard_page.collect_all_svl_without_je.assert_called_once_with(filter_automated_only=True)

    def test_shell_bulk_action_uses_pcb_menu_in_purchase_cycle_mode(self) -> None:
        shell = _SvlFixJeShell.__new__(_SvlFixJeShell)
        shell.btn_bulk_action = _FakeButton()
        shell._bulk_action_menu = _FakeMenu()
        shell._bulk_action_menu_pcb = _FakeMenu()
        shell.dashboard_page = mock.Mock()
        shell.dashboard_page.get_pcb_collection_ui_state.return_value = {"is_pcb_mode": True}

        shell._show_bulk_action_menu()

        self.assertEqual(shell._bulk_action_menu.post_calls, [])
        self.assertEqual(shell._bulk_action_menu_pcb.post_calls, [(20, 54)])

    def test_shell_sync_pcb_bulk_action_menu_state_updates_case_entries(self) -> None:
        shell = _SvlFixJeShell.__new__(_SvlFixJeShell)
        shell._bulk_action_menu_pcb = _FakeMenu()
        shell._bulk_action_menu_pcb.add_cascade(label="Collect PCB Repair (Visible)")
        shell._bulk_action_menu_pcb_collect_visible = _FakeMenu()
        for label in (
            "Semua Case Bermasalah",
            "Case 1 saja",
            "Case 2 saja",
            "Case 3 saja",
            "Case 4 saja",
            "Case Lainnya saja",
        ):
            shell._bulk_action_menu_pcb_collect_visible.add_command(label=label)

        shell._sync_pcb_bulk_action_menu_state(
            {
                "has_visible_actionable_cycles": True,
                "visible_actionable_cases": {
                    "case1": True,
                    "case2": False,
                    "case3": True,
                    "case4": False,
                    "case_lainnya": True,
                },
            }
        )

        self.assertEqual(shell._bulk_action_menu_pcb.entries[0]["state"], "normal")
        self.assertEqual(shell._bulk_action_menu_pcb_collect_visible.entries[0]["state"], "normal")
        self.assertEqual(shell._bulk_action_menu_pcb_collect_visible.entries[1]["state"], "normal")
        self.assertEqual(shell._bulk_action_menu_pcb_collect_visible.entries[2]["state"], "disabled")
        self.assertEqual(shell._bulk_action_menu_pcb_collect_visible.entries[3]["state"], "normal")
        self.assertEqual(shell._bulk_action_menu_pcb_collect_visible.entries[4]["state"], "disabled")
        self.assertEqual(shell._bulk_action_menu_pcb_collect_visible.entries[5]["state"], "normal")

    def test_shell_save_shared_configuration_updates_state_and_notifies_pages(self) -> None:
        shell = _SvlFixJeShell.__new__(_SvlFixJeShell)
        shell._shared_config_sync_active = False
        shell._module_settings = SvlFixJeSettings(database_profile_id="db_old", last_prefix="OLD", max_workers=4, auto_post=False)
        shell._state_store = mock.Mock()
        shell._state_store.load.return_value = SvlFixJeSettings(database_profile_id="db_old", last_prefix="OLD", max_workers=4, auto_post=False)
        shell._state_store.save = mock.Mock()
        shell._db_profile_id_by_label = {"Live ERP - hwgroup_erp [Database Live]": "db_live"}
        shell._db_label_by_profile_id = {"db_live": "Live ERP - hwgroup_erp [Database Live]"}
        shell.database_choice_var = _FakeVar("Live ERP - hwgroup_erp [Database Live]")
        shell.prefix_var = _FakeVar("FIX-NEW")
        shell.max_workers_var = _FakeVar("12")
        shell.auto_post_var = _FakeVar(True)
        shell.upload_page = mock.Mock()
        shell.dashboard_page = mock.Mock()
        shell._load_shared_configuration_values = mock.Mock()
        shell._refresh_repair_collection_controls = mock.Mock()

        shell._save_shared_configuration()

        saved_settings = shell._state_store.save.call_args[0][0]
        self.assertEqual(saved_settings.database_profile_id, "db_live")
        self.assertEqual(saved_settings.last_prefix, "FIX-NEW")
        self.assertEqual(saved_settings.max_workers, 12)
        self.assertTrue(saved_settings.auto_post)
        shell.upload_page.refresh_database_options.assert_called_once()
        shell.dashboard_page.on_shared_configuration_updated.assert_called_once_with(database_changed=True, prefix_changed=True)

    def test_upload_save_module_settings_preserves_shared_configuration_values(self) -> None:
        panel = _SvlFixJePanel.__new__(_SvlFixJePanel)
        panel._module_settings = SvlFixJeSettings(database_profile_id="db_live", last_prefix="FIX-SHARED", auto_post=True, max_workers=9)
        panel._state_store = mock.Mock()
        panel._state_store.load.return_value = SvlFixJeSettings(
            database_profile_id="db_live",
            last_prefix="FIX-SHARED",
            auto_post=True,
            max_workers=9,
        )
        panel._state_store.save = mock.Mock()
        panel.excel_path_var = _FakeVar(r"C:\data\svl.xlsx")
        panel.log_section = mock.Mock(is_open=True)

        panel._save_module_settings()

        saved_settings = panel._state_store.save.call_args[0][0]
        self.assertEqual(saved_settings.database_profile_id, "db_live")
        self.assertEqual(saved_settings.last_prefix, "FIX-SHARED")
        self.assertTrue(saved_settings.auto_post)
        self.assertEqual(saved_settings.max_workers, 9)
        self.assertEqual(saved_settings.last_excel_file, r"C:\data\svl.xlsx")

    def test_dashboard_repair_collection_adds_rows_and_moves_duplicate_to_top(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page._repair_collection = OrderedDict()
        page._repair_collection_scope = None
        page._repair_collection_sequence = 0
        page._repair_collection_changed_callback = None
        page.status_var = _FakeVar("")
        page.company_choice_var = _FakeVar("Alpha Company (#7)")
        page._display_name = "Fixing Unlink SVL - Odoo"
        page._selected_company_id = lambda: 7
        page._selected_database_profile_id = lambda: "db_live"
        page._label_for_company_id = lambda _company_id: "Alpha Company (#7)"
        page._resolve_effective_database_state = lambda selected_profile_id=None: {  # noqa: ARG005
            "selected_profile_id": "db_live",
            "selected_label": "Live ERP - hwgroup_erp [Database Live]",
            "effective_label": "Live ERP - hwgroup_erp [Database Live]",
            "effective_database": "hwgroup_erp",
        }

        seed_a = {"row_key": "row-a", "amount": 100.0, "latest_snapshot_status": "Linked JE header kosong"}
        seed_b = {"row_key": "row-b", "amount": 200.0, "latest_snapshot_status": "SVL tanpa JE"}

        self.assertEqual(page._add_repair_collection_seeds([seed_a]), (1, 0))
        self.assertEqual(page._add_repair_collection_seeds([seed_b]), (1, 0))
        self.assertEqual(page._add_repair_collection_seeds([dict(seed_a, amount=150.0)]), (0, 1))

        self.assertEqual(list(page._repair_collection), ["row-a", "row-b"])
        self.assertEqual(page._repair_collection["row-a"].seed["amount"], 150.0)
        self.assertEqual(page._repair_collection["row-a"].draft["target_mode"], "")
        self.assertEqual(page._repair_collection["row-a"].draft["posting_mode"], "")
        self.assertEqual(page._repair_collection["row-a"].draft["resolve_account_code"], "")
        self.assertEqual(page._repair_collection["row-a"].draft["date"], date.today().strftime("%Y-%m-%d"))
        self.assertEqual(page.get_repair_collection_ui_state()["summary_text"], "2 record | Total 350.00 | Incomplete 2")

    def test_dashboard_repair_collection_reconciles_snapshot_and_removes_solved_rows(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page._repair_collection = OrderedDict()
        page._repair_collection_scope = None
        page._repair_collection_sequence = 0
        page._repair_collection_changed_callback = None
        page.status_var = _FakeVar("")
        page.company_choice_var = _FakeVar("Alpha Company (#7)")
        page._display_name = "Fixing Unlink SVL - Odoo"
        page._selected_company_id = lambda: 7
        page._selected_database_profile_id = lambda: "db_live"
        page._label_for_company_id = lambda _company_id: "Alpha Company (#7)"
        page._resolve_effective_database_state = lambda selected_profile_id=None: {  # noqa: ARG005
            "selected_profile_id": "db_live",
            "selected_label": "Live ERP - hwgroup_erp [Database Live]",
            "effective_label": "Live ERP - hwgroup_erp [Database Live]",
            "effective_database": "hwgroup_erp",
        }
        page._append_log = mock.Mock()

        page._add_repair_collection_seeds(
            [
                {"row_key": "row-a", "amount": 100.0, "latest_snapshot_status": "Linked JE header kosong"},
                {"row_key": "row-b", "amount": 200.0, "latest_snapshot_status": "SVL tanpa JE"},
            ]
        )
        page._snapshot_repair_candidates_by_row_key = lambda _snapshot: {  # noqa: ARG005
            "row-a": ({"row_key": "row-a", "amount": 175.0, "latest_snapshot_status": "Linked JE header kosong"}, "Linked JE header kosong")
        }

        page._reconcile_repair_collection_with_snapshot(mock.Mock())

        self.assertEqual(list(page._repair_collection), ["row-a"])
        self.assertEqual(page._repair_collection["row-a"].seed["amount"], 175.0)
        page._append_log.assert_called_once()
        self.assertIn("1 removed, 1 refreshed", page._append_log.call_args[0][0])

    def test_dashboard_repair_collection_reconcile_refreshes_target_account_from_latest_snapshot(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page.context = mock.Mock(
            global_settings=GlobalSettings(
                repair_account_entries=[RepairAccountEntry(entry_id="repair_1", coa_code="1108099", label="Correction")]
            )
        )
        page._repair_collection = OrderedDict()
        page._repair_collection_scope = None
        page._repair_collection_sequence = 0
        page._repair_collection_changed_callback = None
        page.status_var = _FakeVar("")
        page.company_choice_var = _FakeVar("Alpha Company (#7)")
        page._display_name = "Fixing Unlink SVL - Odoo"
        page._selected_company_id = lambda: 7
        page._selected_database_profile_id = lambda: "db_live"
        page._label_for_company_id = lambda _company_id: "Alpha Company (#7)"
        page._resolve_effective_database_state = lambda selected_profile_id=None: {  # noqa: ARG005
            "selected_profile_id": "db_live",
            "selected_label": "Live ERP - hwgroup_erp [Database Live]",
            "effective_label": "Live ERP - hwgroup_erp [Database Live]",
            "effective_database": "hwgroup_erp",
        }
        page._append_log = mock.Mock()

        page._add_repair_collection_seeds(
            [
                {
                    "row_key": "row-a",
                    "item_code": "ITEM-001",
                    "item_name": "First Item",
                    "item_category_name": "ALCOHOL / LIQUEUR / SWEET & CREAMY",
                    "amount": 100.0,
                    "signed_amount": 100.0,
                    "latest_snapshot_status": "Linked JE header kosong",
                    "account_candidates": [
                        SvlDashboardRepairAccountCandidate(
                            code="1103004",
                            name="Intercompany Output",
                            source="Category Other",
                            role="other",
                            field_name="property_stock_inter_company_output_account_id",
                        )
                    ],
                }
            ]
        )
        draft = page._repair_collection["row-a"].draft
        draft["target_mode"] = "new_and_relink"
        draft["posting_mode"] = "post"
        draft["target_account_role"] = "valuation"
        draft["resolve_account_code"] = "1108099"
        page._refresh_repair_row_state(draft)
        self.assertEqual(draft["row_status"], "incomplete")

        page._snapshot_repair_candidates_by_row_key = lambda _snapshot: {  # noqa: ARG005
            "row-a": (
                {
                    "row_key": "row-a",
                    "item_code": "ITEM-001",
                    "item_name": "First Item",
                    "item_category_name": "ALCOHOL / LIQUEUR / SWEET & CREAMY",
                    "amount": 100.0,
                    "signed_amount": 100.0,
                    "latest_snapshot_status": "Linked JE header kosong",
                    "account_candidates": [
                        SvlDashboardRepairAccountCandidate(
                            code="1105001",
                            name="Persediaan Alkohol",
                            source="Category Stock Valuation",
                            role="valuation",
                            field_name="property_stock_valuation_account_id",
                        )
                    ],
                },
                "Linked JE header kosong",
            )
        }

        page._reconcile_repair_collection_with_snapshot(mock.Mock())

        refreshed = page._repair_collection["row-a"].draft
        self.assertEqual(refreshed["target_account_code"], "1105001")

    def test_dashboard_pcb_repair_done_triggers_analysis_refresh_when_requested(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page.root = _FakeRoot()
        page.status_var = _FakeVar("")
        page._analysis_refresh_pending = False
        page.start_analysis = mock.Mock()

        page._handle_ui_event(
            {
                "type": "_pcb_repair_done",
                "msg": "Selesai PCB",
                "refresh_analysis": True,
            }
        )

        self.assertEqual(page.status_var.get(), "Selesai PCB")
        self.assertTrue(page._analysis_refresh_pending)
        self.assertEqual(len(page.root.after_calls), 1)
        self.assertEqual(page.root.after_calls[0][0], 50)
        self.assertIs(page.root.after_calls[0][1], page.start_analysis)

    def test_dashboard_pcb_repair_done_skips_analysis_refresh_when_not_requested(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page.root = _FakeRoot()
        page.status_var = _FakeVar("")
        page._analysis_refresh_pending = False
        page.start_analysis = mock.Mock()

        page._handle_ui_event(
            {
                "type": "_pcb_repair_done",
                "msg": "Selesai PCB",
                "refresh_analysis": False,
            }
        )

        self.assertEqual(page.status_var.get(), "Selesai PCB")
        self.assertFalse(page._analysis_refresh_pending)
        self.assertEqual(page.root.after_calls, [])

    def test_dashboard_pcb_collection_reconciles_snapshot_and_preserves_repaired_rows(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page._selected_company_id = lambda: 7
        page._selected_company_name = lambda: "Alpha Company"
        page._module_settings = SvlFixJeSettings(last_prefix="PCB")
        page._last_pcb_case1_dialog_widgets = {}
        page._append_log = mock.Mock()
        page._update_pcb_collection_button = mock.Mock()

        class _MatchingScope:
            database_profile_id = "db_live"
            database_label = "Live ERP - hwgroup_erp [Database Live]"
            database_value = "hwgroup_erp"
            company_id = 7
            company_label = "Alpha Company (#7)"

            def matches(self, other) -> bool:  # noqa: ANN001
                return True

        page._pcb_repair_collection_scope = _MatchingScope()
        page._current_repair_collection_scope = lambda: _MatchingScope()
        page._pcb_repair_collection = OrderedDict(
            {
                "row-repaired": {
                    "row_key": "row-repaired",
                    "row_status": "repaired",
                    "row_status_message": "JE existing",
                    "latest_snapshot_status": "problem",
                    "reference": "Manual repaired ref",
                    "reference_generated": False,
                    "line_label": "Manual repaired label",
                    "line_label_generated": False,
                },
                "row-remove": {
                    "row_key": "row-remove",
                    "row_status": "ready",
                    "row_status_message": "Ready",
                    "latest_snapshot_status": "problem",
                    "reference": "Manual remove ref",
                    "reference_generated": False,
                    "line_label": "Manual remove label",
                    "line_label_generated": False,
                },
                "case1::7001::901::bill_line::8101": {
                    "row_key": "case1::7001::901::bill_line::8101",
                    "cycle_key": "case1::7001",
                    "pcb_case": "case1",
                    "pcb_case_label": PCB_CASE1_LABEL,
                    "company_id": 7,
                    "company_name": "Alpha Company",
                    "picking_name": "LHPK/IN/7001",
                    "source_label": "Bill Line #8101",
                    "po_name": "PO/2026/0001",
                    "bill_name": "BILL/2026/0001",
                    "date": "2026-03-20",
                    "reference": "Manual Ref",
                    "line_label": "Manual Label",
                    "reference_generated": False,
                    "line_label_generated": False,
                    "amount": 120.0,
                    "debit_account_code": "1108099",
                    "credit_account_code": "2103006",
                    "reconcile_readiness_label": "Exact Auto",
                    "row_status": "ready",
                    "row_status_message": "Ready",
                    "latest_snapshot_status": "problem",
                    "item_code": "SKU-001",
                    "item_name": "Produk A",
                    "product_id": 901,
                },
            }
        )

        snapshot = SvlDashboardSnapshot(
            database="hwgroup_erp",
            company_id=7,
            company_name="Alpha Company",
            generated_at="2026-03-27T10:00:00",
            period="2026-03-01..2026-03-31",
            purchase_cycles=[
                _build_pcb_cycle(
                    picking_id=7001,
                    case1_link_rows=[
                        SvlDashboardPcbCase1LinkRow(
                            cycle_key="case1::7001",
                            source_key="case1::7001::901::bill_line::8101",
                            source_kind="bill_line",
                            source_id=8101,
                            product_id=901,
                            item_code="SKU-001",
                            item_name="Produk A",
                            bill_line_id=8101,
                            bill_move_id=8201,
                            purchase_line_id=8301,
                            stock_move_id=8401,
                            stock_move_ids=[8401],
                            stj_move_ids=[8801],
                            stj_refs=["STJ/2026/0451"],
                            picking_id=7001,
                            picking_name="LHPK/IN/7001",
                            po_name="PO/2026/0001",
                            bill_name="BILL/2026/0001",
                            partner_id=77,
                            partner_name="Vendor Alpha",
                            debit_account_code="1108099",
                            credit_account_code="2103006",
                            allocated_amount=175.0,
                        )
                    ],
                )
            ],
        )

        page._reconcile_pcb_case1_collection_with_snapshot(snapshot)

        self.assertEqual(list(page._pcb_repair_collection.keys()), ["row-repaired", "case1::7001::901::bill_line::8101"])
        refreshed_row = page._pcb_repair_collection["case1::7001::901::bill_line::8101"]
        self.assertEqual(refreshed_row["amount"], 175.0)
        self.assertEqual(refreshed_row["latest_snapshot_status"], "problem")
        self.assertEqual(refreshed_row["reference"], "Manual Ref")
        self.assertEqual(refreshed_row["line_label"], "Manual Label")
        self.assertEqual(refreshed_row["row_status"], "ready")
        repaired_row = page._pcb_repair_collection["row-repaired"]
        self.assertEqual(repaired_row["row_status"], "repaired")
        page._update_pcb_collection_button.assert_called_once_with()
        page._append_log.assert_called_once()
        self.assertIn("1 removed, 1 refreshed", page._append_log.call_args[0][0])

    def test_dashboard_pcb_collection_reconcile_preserves_manual_simulation_lines(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page.context = mock.Mock(global_settings=GlobalSettings())
        page._selected_company_id = lambda: 7
        page._selected_company_name = lambda: "Alpha Company"
        page._module_settings = SvlFixJeSettings(last_prefix="PCB")
        page._last_pcb_case1_dialog_widgets = {}
        page._append_log = mock.Mock()
        page._update_pcb_collection_button = mock.Mock()

        class _MatchingScope:
            database_profile_id = "db_live"
            database_label = "Live ERP - hwgroup_erp [Database Live]"
            database_value = "hwgroup_erp"
            company_id = 7
            company_label = "Alpha Company (#7)"

            def matches(self, other) -> bool:  # noqa: ANN001
                return True

        page._pcb_repair_collection_scope = _MatchingScope()
        page._current_repair_collection_scope = lambda: _MatchingScope()
        cycle = _build_pcb_cycle(
            picking_id=7115,
            case2_repair_rows=[
                _build_pcb_case2_row(
                    row_key="case4::7115::901",
                    cycle_key="case4::7115",
                    pcb_case="case4",
                    pcb_case_label=PCB_CASE4_LABEL,
                    expense_account_code="5101004",
                    review_required=False,
                    review_confirmed=False,
                    planned_lines=[
                        SvlDashboardPcbRepairPlannedLine(
                            role="problem_2103006",
                            account_code="2103006",
                            account_name="Hutang Suspend",
                            amount=100.0,
                            side="debit",
                        ),
                        SvlDashboardPcbRepairPlannedLine(
                            role="selisih_hpp",
                            account_code="5101004",
                            account_name="HPP Rokok",
                            amount=100.0,
                            side="credit",
                            line_label="Default target expense",
                        ),
                    ],
                )
            ],
            item_rows=[
                SvlDashboardCycleItemRow(
                    product_id=901,
                    product_name="Produk PCB",
                    default_code="SKU-001",
                    has_item_bill=True,
                    has_item_stj=True,
                    eligible_case34=True,
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
                        )
                    ],
                )
            ],
        )
        snapshot = SvlDashboardSnapshot(
            database="hwgroup_erp",
            company_id=7,
            company_name="Alpha Company",
            generated_at="2026-03-28 10:00:00",
            period="2026-03-01..2026-03-31",
            purchase_cycles=[cycle],
        )
        latest_seed = page._build_pcb_seeds_for_cycle(cycle)[0]
        current_row = dict(latest_seed)
        current_row["planned_lines_manual"] = True
        current_row["planned_lines"] = [
            {
                "role": "problem_2103006",
                "account_code": "2103006",
                "account_name": "Hutang Suspend",
                "amount": 100.0,
                "side": "debit",
            },
            {
                "role": "selisih_hpp",
                "account_code": "1108099",
                "account_name": "Clearing - System Pending Entries",
                "amount": 100.0,
                "side": "credit",
                "line_label": "Override manual",
                "manual_account_override": True,
                "manual_line": True,
            },
        ]
        page._pcb_repair_collection = OrderedDict({current_row["row_key"]: current_row})

        page._reconcile_pcb_case1_collection_with_snapshot(snapshot)

        row = page._pcb_repair_collection[current_row["row_key"]]
        self.assertTrue(row["planned_lines_manual"])
        self.assertEqual(row["planned_lines"][1]["account_code"], "1108099")
        self.assertEqual(row["planned_lines"][1]["line_label"], "Override manual")
        self.assertEqual(row["base_planned_lines"][1]["account_code"], "5101004")
        page._update_pcb_collection_button.assert_called_once_with()
        page._append_log.assert_called_once()

    def test_dashboard_scope_change_clears_repair_and_pcb_collections(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page.status_var = _FakeVar("")
        page._append_log = mock.Mock()
        page._repair_collection = OrderedDict({"row-1": object()})
        page._pcb_repair_collection = OrderedDict({"pcb-row-1": {"row_key": "pcb-row-1"}})

        class _OldScope:
            def matches(self, other) -> bool:  # noqa: ANN001
                return False

        page._repair_collection_scope = _OldScope()
        page._pcb_repair_collection_scope = _OldScope()
        page._current_repair_collection_scope = lambda: object()
        page._close_repair_dialog_if_collection_source = mock.Mock()
        page._refresh_open_pcb_collection_dialog = mock.Mock()
        page._update_pcb_collection_button = mock.Mock()
        page._notify_repair_collection_changed = mock.Mock()

        page._clear_repair_collections_for_scope_change()

        self.assertEqual(len(page._repair_collection), 0)
        self.assertEqual(len(page._pcb_repair_collection), 0)
        self.assertIsNone(page._repair_collection_scope)
        self.assertIsNone(page._pcb_repair_collection_scope)
        page._close_repair_dialog_if_collection_source.assert_called_once_with()
        page._refresh_open_pcb_collection_dialog.assert_called_once_with()
        page._update_pcb_collection_button.assert_called_once_with()
        self.assertIn("dibersihkan karena analyze berpindah company / scope", page.status_var.get())
        page._append_log.assert_called_once()

    def test_dashboard_pcb_builds_collection_rows_from_case1_link_rows(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page._selected_company_id = lambda: 7
        page._selected_company_name = lambda: "Alpha Company"
        cycle = _build_pcb_cycle(
            picking_id=7001,
            case1_link_rows=[
                SvlDashboardPcbCase1LinkRow(
                    cycle_key="case1::7001",
                    source_key="case1::7001::901::bill_line::8101",
                    source_kind="bill_line",
                    source_id=8101,
                    product_id=901,
                    item_code="SKU-001",
                    item_name="Produk A",
                    bill_line_id=8101,
                    bill_move_id=8201,
                    purchase_line_id=8301,
                    stock_move_id=8401,
                    stj_move_ids=[8801],
                    stj_refs=["STJ/2026/0451"],
                    stj_link_basis="cycle_match.stock_move",
                    stj_candidate_count=1,
                    picking_id=7001,
                    picking_name="LHPK/IN/7001",
                    po_name="PO/2026/0001",
                    bill_name="BILL/2026/0001",
                    partner_id=77,
                    partner_name="Vendor Alpha",
                    payment_move_ids=[8501],
                    bank_move_ids=[8601],
                    product_uom_id=11,
                    quantity=2.0,
                    currency_id=13,
                    amount_currency=100.0,
                    amount_currency_basis=94.0,
                    journal_code="STJ",
                    debit_account_code="1108099",
                    credit_account_code="2103006",
                    allocated_amount=100.0,
                    gr_date="2026-03-18",
                    bill_date="2026-03-19",
                ),
                SvlDashboardPcbCase1LinkRow(
                    cycle_key="case1::7001",
                    source_key="case1::7001::902::stock_move::8402",
                    source_kind="stock_move",
                    source_id=8402,
                    product_id=902,
                    item_code="SKU-002",
                    item_name="Produk B",
                    stock_move_id=8402,
                    picking_id=7001,
                    picking_name="LHPK/IN/7001",
                    partner_name="Vendor Alpha",
                    journal_code="STJ",
                    debit_account_code="1108099",
                    credit_account_code="2103006",
                    allocated_amount=50.0,
                    gr_date="2026-03-18",
                ),
            ],
        )

        seeds = page._build_pcb_case1_seeds_for_cycle(cycle)

        self.assertEqual(len(seeds), 2)
        self.assertEqual(seeds[0]["row_key"], "case1::7001::901::bill_line::8101")
        self.assertEqual(seeds[0]["source_label"], "Bill Line #8101")
        self.assertEqual(seeds[0]["stj_refs"], ["STJ/2026/0451"])
        self.assertEqual(seeds[0]["stj_link_basis"], "cycle_match.stock_move")
        self.assertEqual(seeds[0]["stj_candidate_count"], 1)
        self.assertEqual(seeds[0]["row_status"], "ready")
        self.assertEqual(seeds[0]["amount_currency_basis"], 94.0)
        self.assertEqual(seeds[1]["source_label"], "Stock Move #8402")
        self.assertEqual(seeds[0]["date"], date.today().strftime("%Y-%m-%d"))
        self.assertEqual(
            seeds[0]["reference"],
            _pcb_case1_reference(
                picking_name="LHPK/IN/7001",
                bill_name="BILL/2026/0001",
                item_code="SKU-001",
                item_name="Produk A",
            ),
        )
        self.assertEqual(
            seeds[0]["line_label"],
            _pcb_case1_line_label(item_code="SKU-001", item_name="Produk A"),
        )
        self.assertTrue(seeds[0]["reference_generated"])
        self.assertTrue(seeds[0]["line_label_generated"])
        self.assertEqual(seeds[0]["pcb_case_label"], PCB_CASE1_LABEL)

    def test_build_pcb_case1_seed_defaults_to_today_even_when_last_used_date_exists(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page._module_settings = SvlFixJeSettings(pcb_case1_last_date="2026-03-25")
        page._selected_company_id = lambda: 7
        page._selected_company_name = lambda: "Alpha Company"
        cycle = _build_pcb_cycle(
            picking_id=7001,
            case1_link_rows=[
                SvlDashboardPcbCase1LinkRow(
                    cycle_key="case1::7001",
                    source_key="case1::7001::901::bill_line::8101",
                    source_kind="bill_line",
                    source_id=8101,
                    product_id=901,
                    allocated_amount=100.0,
                    gr_date="2026-03-18",
                    bill_date="2026-03-19",
                )
            ],
        )

        seeds = page._build_pcb_case1_seeds_for_cycle(cycle)

        self.assertEqual(seeds[0]["date"], date.today().strftime("%Y-%m-%d"))

    def test_build_pcb_case2_seed_defaults_to_today(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page._module_settings = SvlFixJeSettings(pcb_case1_last_date="2026-03-25")
        page._selected_company_id = lambda: 7
        page._selected_company_name = lambda: "Alpha Company"
        cycle = _build_pcb_cycle(
            picking_id=7002,
            case2_repair_rows=[
                _build_pcb_case2_row(
                    row_key="case2::7002::901",
                    cycle_key="case2::7002",
                    picking_id=7002,
                    picking_name="LHPK/IN/7002",
                )
            ],
        )

        seeds = page._build_pcb_case2_seeds_for_cycle(cycle)

        self.assertEqual(seeds[0]["date"], date.today().strftime("%Y-%m-%d"))

    def test_build_pcb_case1_seed_uses_runtime_lookup_when_scope_exists_without_target_ids(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page._selected_company_id = lambda: 7
        page._selected_company_name = lambda: "Alpha Company"
        cycle = _build_pcb_cycle(
            picking_id=7001,
            case1_link_rows=[
                SvlDashboardPcbCase1LinkRow(
                    cycle_key="case1::7001",
                    source_key="case1::7001::901::bill_line::8101",
                    source_kind="bill_line",
                    source_id=8101,
                    product_id=901,
                    bill_move_id=8201,
                    stj_move_ids=[8801],
                    allocated_amount=100.0,
                    gr_date="2026-03-18",
                    bill_date="2026-03-19",
                )
            ],
        )

        seeds = page._build_pcb_case1_seeds_for_cycle(cycle)

        self.assertFalse(seeds[0]["reconcile_ready"])
        self.assertEqual(seeds[0]["reconcile_readiness_label"], "Disabled")
        self.assertEqual(seeds[0]["reconcile_target_count"], 0)

    def test_build_pcb_case1_seed_uses_runtime_lookup_when_only_one_problem_side_is_seeded(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page._selected_company_id = lambda: 7
        page._selected_company_name = lambda: "Alpha Company"
        cycle = _build_pcb_cycle(
            picking_id=7001,
            case1_link_rows=[
                SvlDashboardPcbCase1LinkRow(
                    cycle_key="case1::7001",
                    source_key="case1::7001::901::bill_line::8101",
                    source_kind="bill_line",
                    source_id=8101,
                    product_id=901,
                    bill_move_id=8201,
                    stj_move_ids=[8801],
                    suspend_target_aml_ids=[9301],
                    clearing_target_aml_ids=[],
                    allocated_amount=100.0,
                    gr_date="2026-03-18",
                    bill_date="2026-03-19",
                )
            ],
        )

        seeds = page._build_pcb_case1_seeds_for_cycle(cycle)

        self.assertFalse(seeds[0]["reconcile_ready"])
        self.assertEqual(seeds[0]["reconcile_readiness_label"], "Disabled")
        self.assertEqual(seeds[0]["reconcile_target_count"], 1)

    def test_dashboard_pcb_builds_collection_rows_from_case2_item_balance_rows(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page._selected_company_id = lambda: 7
        page._selected_company_name = lambda: "Alpha Company"
        cycle = _build_pcb_cycle(
            picking_id=7001,
            case2_repair_rows=[
                _build_pcb_case2_row(
                    payment_move_ids=[8501],
                    bank_move_ids=[8601],
                    suspend_target_aml_ids=[9102],
                    clearing_target_aml_ids=[9101],
                )
            ],
        )

        seeds = page._build_pcb_case2_seeds_for_cycle(cycle)

        self.assertEqual(len(seeds), 1)
        self.assertEqual(seeds[0]["row_key"], "case2::7001::901")
        self.assertEqual(seeds[0]["source_label"], "Item Balance")
        self.assertEqual(seeds[0]["pcb_case_label"], PCB_CASE2_LABEL)
        self.assertEqual(seeds[0]["company_id"], 7)
        self.assertEqual(seeds[0]["company_name"], "Alpha Company")
        self.assertEqual(len(seeds[0]["planned_lines"]), 3)
        self.assertEqual(seeds[0]["planned_lines"][0]["account_code"], "2103006")
        self.assertEqual(seeds[0]["planned_lines"][1]["account_code"], "5101004")
        self.assertEqual(seeds[0]["bill_line_id"], 8101)
        self.assertEqual(seeds[0]["purchase_line_id"], 8301)
        self.assertEqual(seeds[0]["stock_move_id"], 8401)
        self.assertEqual(seeds[0]["stock_move_ids"], [8401])
        self.assertEqual(seeds[0]["stj_move_ids"], [8801])
        self.assertEqual(seeds[0]["stj_refs"], ["STJ/2026/0451"])
        self.assertEqual(seeds[0]["payment_move_ids"], [8501])
        self.assertEqual(seeds[0]["bank_move_ids"], [8601])
        self.assertEqual(seeds[0]["suspend_target_aml_ids"], [9102])
        self.assertEqual(seeds[0]["clearing_target_aml_ids"], [9101])
        self.assertEqual(seeds[0]["product_uom_id"], 11)
        self.assertEqual(seeds[0]["quantity"], 2.0)
        self.assertEqual(seeds[0]["currency_id"], 13)
        self.assertEqual(seeds[0]["amount_currency"], 100.0)
        self.assertEqual(seeds[0]["analytic_distribution"], {"CC-01": 100.0})
        self.assertEqual(seeds[0]["bill_price_unit"], 50.0)
        self.assertEqual(seeds[0]["gr_price_unit"], 0.0)
        self.assertEqual(seeds[0]["price_gap_value"], 100.0)
        self.assertEqual(seeds[0]["allocated_amount"], 100.0)
        self.assertEqual(seeds[0]["problem_balances_by_code"], {"2103006": -100.0})
        self.assertEqual(seeds[0]["hpp_balance"], 70.0)
        self.assertEqual(seeds[0]["selisih_hpp_amount"], 30.0)
        self.assertEqual(seeds[0]["bank_balances_by_code"], {"1101060": -55.0})
        self.assertIn("Saldo HPP item (5101004)", seeds[0]["guard_messages"][1])
        self.assertEqual(
            seeds[0]["line_label"],
            "SKU-001 - Produk PCB - Case 2 - STJ Bill Price Diff (Suspend - Suspend)",
        )

    def test_dashboard_pcb_case8_case9_labels_are_registered(self) -> None:
        self.assertIn("case8a", SvlFixJeDashboardPage._PCB_PROBLEM_CASE_ORDER)
        self.assertIn("case8b", SvlFixJeDashboardPage._PCB_PROBLEM_CASE_ORDER)
        self.assertIn("case9", SvlFixJeDashboardPage._PCB_PROBLEM_CASE_ORDER)
        self.assertEqual(
            SvlFixJeDashboardPage._PCB_PROBLEM_CASE_LABEL["case8a"],
            "Case 8A - Full Return Value Mismatch",
        )
        self.assertEqual(
            SvlFixJeDashboardPage._PCB_PROBLEM_CASE_LABEL["case9"],
            "Case 9 - UoM Scale Mismatch",
        )

    def test_dashboard_pcb_case9_seed_preserves_evidence_and_planned_lines(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page._selected_company_id = lambda: 7
        page._selected_company_name = lambda: "Alpha Company"
        cycle = _build_pcb_cycle(
            picking_id=7009,
            case2_repair_rows=[
                _build_pcb_case2_row(
                    row_key="case9::7009::901",
                    cycle_key="case9::7009",
                    pcb_case="case9",
                    pcb_case_label="Case 9 - UoM Scale Mismatch",
                    review_required=True,
                    review_reason="Case 9 wajib review.",
                    planned_lines=[
                        {
                            "role": "case9_hpp_reclass",
                            "account_code": "5101004",
                            "account_name": "COGS Other",
                            "amount": 37620000.0,
                            "side": "debit",
                        },
                        {
                            "role": "case9_clearing_offset",
                            "account_code": "1108099",
                            "account_name": "Clearing",
                            "amount": 37620000.0,
                            "side": "credit",
                        },
                    ],
                    case_evidence={
                        "source_uom_name": "KG",
                        "product_uom_name": "PACK @10 PORSI",
                        "scale_factor": 100.0,
                        "holder_basis": "case2_downstream_clearing",
                    },
                )
            ],
        )

        seed = page._build_pcb_case2_seeds_for_cycle(cycle)[0]

        self.assertEqual(seed["pcb_case"], "case9")
        self.assertTrue(seed["review_required"])
        self.assertEqual(seed["case_evidence"]["scale_factor"], 100.0)
        self.assertEqual(seed["planned_lines"][0]["role"], "case9_hpp_reclass")
        self.assertEqual(seed["planned_lines"][1]["account_code"], "1108099")
        self.assertIn("DR 5101004 37,620,000.00", page._pcb_je_preview_text(seed))

    def test_pcb_case2_dialog_shows_trace_and_target_hint_lists_when_present(self) -> None:
        if not hasattr(self, "root"):
            try:
                self.root = tk.Tk()
                self.root.withdraw()
                self.addCleanup(self.root.destroy)
            except tk.TclError as exc:
                self.skipTest(f"Tk unavailable: {exc}")
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page.root = self.root
        page._display_name = "Fixing Unlink SVL - Odoo"
        page._last_pcb_case1_dialog_widgets = {}
        page.status_var = tk.StringVar(master=self.root, value="")
        page.context = mock.Mock(global_settings=GlobalSettings(default_output_dir=r"C:\shared\output"))
        page._module_settings = SvlFixJeSettings(last_output_dir=r"C:\exports")
        page._state_store = mock.Mock()
        page._pcb_repair_collection_scope = None
        page._current_repair_collection_scope = lambda: None
        page._update_pcb_collection_button = lambda: None
        page._selected_company_id = lambda: 7
        page._selected_company_name = lambda: "Alpha Company"
        cycle = _build_pcb_cycle(
            picking_id=7004,
            case2_repair_rows=[
                _build_pcb_case2_row(
                    row_key="case4::7004::901",
                    cycle_key="case4::7004",
                    pcb_case="case4",
                    pcb_case_label=PCB_CASE4_LABEL,
                    line_label="SKU-001 - Produk PCB - Case 4 - STJ Bill Hit Expenses (Suspend - Expenses)",
                    payment_move_ids=[8501],
                    bank_move_ids=[8601],
                    suspend_target_aml_ids=[9102],
                    clearing_target_aml_ids=[9101],
                )
            ],
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
                )
            ],
            raw_lines=[],
            bill_refs=["BILL/2026/0004"],
        )
        seed = page._build_pcb_case2_seeds_for_cycle(cycle)[0]
        page._pcb_repair_collection = OrderedDict({seed["row_key"]: seed})

        page._open_pcb_case1_repair_dialog_v2()
        self.root.update_idletasks()
        widgets = page._last_pcb_case1_dialog_widgets
        tree = widgets["tree"]
        first_iid = next(iter(widgets["row_by_iid"]))
        tree.selection_set((first_iid,))
        tree.focus(first_iid)
        tree.event_generate("<<TreeviewSelect>>")
        self.root.update_idletasks()

        self.assertEqual(widgets["advanced_vars"]["payment_move_ids"].get(), "8501")
        self.assertEqual(widgets["advanced_vars"]["bank_move_ids"].get(), "8601")
        self.assertEqual(widgets["advanced_vars"]["suspend_target_aml_ids"].get(), "9102")
        self.assertEqual(widgets["advanced_vars"]["clearing_target_aml_ids"].get(), "9101")
        self.assertEqual(widgets["advanced_vars"]["quantity"].get(), "2.0")
        self.assertEqual(widgets["advanced_vars"]["amount_currency"].get(), "100.0")
        self.assertEqual(widgets["advanced_vars"]["bill_price_unit"].get(), "50.0")
        self.assertEqual(widgets["advanced_vars"]["allocated_amount"].get(), "100.0")

        widgets["close_button"].invoke()
        self.root.update_idletasks()

    def test_dashboard_pcb_classifies_case3_and_case4_separately(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        case3_cycle = _build_pcb_cycle(
            picking_id=7003,
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
                    product_name="Produk Case 3",
                    default_code="SKU-901",
                    has_item_bill=True,
                    has_item_stj=True,
                    eligible_case34=True,
                )
            ],
        )
        case4_cycle = _build_pcb_cycle(
            picking_id=7004,
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
                )
            ],
            raw_lines=[],
            bill_refs=["BILL/2026/0004"],
            item_rows=[
                SvlDashboardCycleItemRow(
                    product_id=902,
                    product_name="Produk Case 4",
                    default_code="SKU-902",
                    has_item_bill=True,
                    verified_audit_clearing_amount=120.0,
                    eligible_case34=True,
                )
            ],
        )

        self.assertEqual(page._classify_pcb_cycle_case(case3_cycle), "case3")
        self.assertEqual(page._classify_pcb_cycle_case(case4_cycle), "case4")

    def test_classify_pcb_cycle_case_case34_falls_back_when_item_not_eligible(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        case3_cycle = _build_pcb_cycle(
            picking_id=7010,
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
            bill_refs=["BILL/2026/0010"],
            item_rows=[
                SvlDashboardCycleItemRow(
                    product_id=901,
                    product_name="Produk No Gate",
                    default_code="SKU-901",
                    has_item_bill=True,
                    eligible_case34=False,
                )
            ],
        )

        self.assertEqual(page._classify_pcb_cycle_case(case3_cycle), "case_lainnya")

    def test_classify_pcb_cycle_case_accepts_cycle_correction_stj_evidence_once_item_is_eligible(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        case3_cycle = _build_pcb_cycle(
            picking_id=7010,
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
            bill_refs=["BILL/2026/0010"],
            item_rows=[
                SvlDashboardCycleItemRow(
                    product_id=901,
                    product_name="Produk Correction",
                    default_code="SKU-901",
                    has_item_bill=True,
                    correction_stj_refs=["STJ/2026/03/0160"],
                    has_stj_evidence=True,
                    eligible_case34=True,
                )
            ],
        )

        self.assertEqual(page._classify_pcb_cycle_case(case3_cycle), "case3")

    def test_pcb_cycle_non_actionable_reason_mentions_external_clearing_status_for_case34_like_cycle(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        cycle = _build_pcb_cycle(
            picking_id=7011,
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
            bill_refs=["BILL/2026/0011"],
            item_rows=[
                SvlDashboardCycleItemRow(
                    product_id=901,
                    product_name="Produk No External",
                    default_code="SKU-901",
                    has_item_bill=True,
                    has_item_stj=False,
                    external_clearing_amount=0.0,
                    external_clearing_verified=False,
                    eligible_case34=False,
                )
            ],
        )

        reason = page._pcb_cycle_non_actionable_reason(cycle)

        self.assertIn("SKU-901 | Produk No External", reason)
        self.assertIn("External Clearing 0.00 (not verified)", reason)

    def test_pcb_cycle_non_actionable_reason_reports_stj_yes_for_cycle_correction_evidence(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        cycle = _build_pcb_cycle(
            picking_id=7012,
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
            bill_refs=["BILL/2026/0012"],
            item_rows=[
                SvlDashboardCycleItemRow(
                    product_id=901,
                    product_name="Produk Correction",
                    default_code="SKU-901",
                    has_item_bill=True,
                    correction_stj_refs=["STJ/2026/03/0160"],
                    has_stj_evidence=True,
                    external_clearing_amount=0.0,
                    external_clearing_verified=False,
                    eligible_case34=False,
                )
            ],
        )

        reason = page._pcb_cycle_non_actionable_reason(cycle)

        self.assertIn("STJ Yes", reason)
        self.assertNotIn("STJ No", reason)

    def test_dashboard_pcb_builds_collection_rows_from_case3_item_balance_rows(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page._selected_company_id = lambda: 7
        page._selected_company_name = lambda: "Alpha Company"
        cycle = _build_pcb_cycle(
            picking_id=7003,
            case2_repair_rows=[
                _build_pcb_case2_row(
                    row_key="case3::7003::901",
                    cycle_key="case3::7003",
                    pcb_case="case3",
                    pcb_case_label=PCB_CASE3_LABEL,
                    line_label="SKU-001 - Produk PCB - Case 3 - STJ Bill Hit Expenses (Clearing - Expenses)",
                )
            ],
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
        )

        seeds = page._build_pcb_seeds_for_cycle(cycle)

        self.assertEqual(len(seeds), 1)
        self.assertEqual(seeds[0]["row_key"], "case3::7003::901")
        self.assertEqual(seeds[0]["pcb_case"], "case3")
        self.assertEqual(seeds[0]["pcb_case_label"], PCB_CASE3_LABEL)
        self.assertEqual(
            seeds[0]["line_label"],
            "SKU-001 - Produk PCB - Case 3 - STJ Bill Hit Expenses (Clearing - Expenses)",
        )

    def test_dashboard_pcb_builds_hybrid_cycle_seeds_from_case1_and_multiline_rows(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page._selected_company_id = lambda: 7
        page._selected_company_name = lambda: "Alpha Company"
        cycle = _build_pcb_cycle(
            picking_id=7015,
            case1_link_rows=[
                SvlDashboardPcbCase1LinkRow(
                    cycle_key="case1::7015",
                    source_key="case1::7015::901::bill_line::8101",
                    source_kind="bill_line",
                    source_id=8101,
                    product_id=901,
                    item_code="SKU-001",
                    item_name="Produk Hybrid",
                    bill_line_id=8101,
                    bill_move_id=8201,
                    purchase_line_id=8301,
                    stock_move_id=8401,
                    stj_move_ids=[8801],
                    stj_refs=["STJ/2026/0451"],
                    picking_id=7015,
                    picking_name="LHPK/IN/7015",
                    po_name="PO/2026/7015",
                    bill_name="BILL/2026/7015",
                    partner_id=77,
                    partner_name="Vendor Alpha",
                    journal_code="STJ",
                    debit_account_code="1108099",
                    credit_account_code="2103006",
                    debit_amount=120.0,
                    credit_amount=100.0,
                    diff_account_code="510001",
                    diff_account_name="COGS",
                    diff_side="credit",
                    diff_amount=20.0,
                    allocated_amount=120.0,
                )
            ],
            case2_repair_rows=[
                _build_pcb_case2_row(
                    row_key="case5::7015::902",
                    cycle_key="case5::7015",
                    pcb_case="case5",
                    pcb_case_label="Case 5 - No STJ Bill Miss Match (Undirect Clearing - Suspend)",
                )
            ],
        )
        cycle.primary_case = "case5"
        cycle.case_counts = {"case1": 1, "case5": 1}

        seeds = page._build_pcb_seeds_for_cycle(cycle)

        self.assertEqual(len(seeds), 2)
        self.assertEqual({seed["row_key"] for seed in seeds}, {"case1::7015::901::bill_line::8101", "case5::7015::902"})
        case1_seed = next(seed for seed in seeds if seed["pcb_case"] == "case1")
        self.assertEqual(case1_seed["diff_account_code"], "510001")
        self.assertEqual(case1_seed["diff_side"], "credit")
        self.assertEqual(case1_seed["diff_amount"], 20.0)
        case5_seed = next(seed for seed in seeds if seed["pcb_case"] == "case5")
        self.assertEqual(case5_seed["pcb_case_label"], "Case 5 - No STJ Bill Miss Match (Undirect Clearing - Suspend)")

    def test_dashboard_pcb_case1_simulated_journal_lines_use_row_amounts_and_diff_line(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        row = {
            "pcb_case": "case1",
            "item_code": "A-SPWH-0076",
            "item_name": "MARTELL CORDON BLUE",
            "line_label": "Correction A-SPWH-0076 - MARTELL CORDON BLUE",
            "amount": 6750000.0,
            "debit_account_code": "1108099",
            "credit_account_code": "2103006",
            "debit_amount": 5764827.69,
            "credit_amount": 6750000.0,
            "diff_account_code": "5101001",
            "diff_account_name": "HPP Alkohol",
            "diff_side": "debit",
            "diff_amount": 985172.31,
            "account_name_by_code": {
                "1108099": "Clearing - System Pending Entries",
                "2103006": "Hutang Suspensed Pengadaan Barang/Jasa / Suspensed Payable for Procurement",
            },
        }

        simulated_lines = page._pcb_simulated_journal_lines(row)

        self.assertEqual(
            [(line["side_label"], line["account_code"], line["amount"]) for line in simulated_lines],
            [
                ("DR", "1108099", 5764827.69),
                ("DR", "5101001", 985172.31),
                ("CR", "2103006", 6750000.0),
            ],
        )
        self.assertEqual(round(sum(line["signed_amount"] for line in simulated_lines), 2), 0.0)

    def test_dashboard_pcb_case1_preview_text_uses_case1_line_amounts(self) -> None:
        preview = SvlFixJeDashboardPage._pcb_je_preview_text(
            {
                "pcb_case": "case1",
                "item_code": "A-SPWH-0076",
                "item_name": "MARTELL CORDON BLUE",
                "line_label": "Correction A-SPWH-0076 - MARTELL CORDON BLUE",
                "amount": 6750000.0,
                "debit_account_code": "1108099",
                "credit_account_code": "2103006",
                "debit_amount": 5764827.69,
                "credit_amount": 6750000.0,
                "diff_account_code": "5101001",
                "diff_side": "debit",
                "diff_amount": 985172.31,
            }
        )

        self.assertEqual(
            preview,
            "DR 1108099 5,764,827.69 | DR 5101001 985,172.31 | CR 2103006 6,750,000.00",
        )

    def test_dashboard_pcb_builds_case6_multiline_seed(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page._selected_company_id = lambda: 7
        page._selected_company_name = lambda: "Alpha Company"
        cycle = _build_pcb_cycle(
            picking_id=7016,
            case2_repair_rows=[
                _build_pcb_case2_row(
                    row_key="case6::7016::903",
                    cycle_key="case6::7016",
                    pcb_case="case6",
                    pcb_case_label="Case 6 - No STJ Bill Hit Expenses (Undirect Clearing - Expenses)",
                    line_label="SKU-003 - Produk Case 6 - Case 6 - No STJ Bill Hit Expenses (Undirect Clearing - Expenses)",
                    repair_basis_amount=66.0,
                    repair_basis_source="standard_cost_x_qty",
                )
            ],
        )
        cycle.primary_case = "case6"
        cycle.case_counts = {"case6": 1}

        seeds = page._build_pcb_seeds_for_cycle(cycle)

        self.assertEqual(len(seeds), 1)
        self.assertEqual(seeds[0]["pcb_case"], "case6")
        self.assertEqual(seeds[0]["pcb_case_label"], "Case 6 - No STJ Bill Hit Expenses (Undirect Clearing - Expenses)")
        self.assertEqual(seeds[0]["repair_basis_amount"], 66.0)
        self.assertEqual(seeds[0]["repair_basis_source"], "standard_cost_x_qty")

    def test_dashboard_pcb_case3_seed_includes_external_clearing_summary(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page._selected_company_id = lambda: 7
        page._selected_company_name = lambda: "Alpha Company"
        cycle = _build_pcb_cycle(
            picking_id=7003,
            case2_repair_rows=[
                _build_pcb_case2_row(
                    row_key="case3::7003::901",
                    cycle_key="case3::7003",
                    pcb_case="case3",
                    pcb_case_label=PCB_CASE3_LABEL,
                    external_clearing_amount=120.0,
                    external_clearing_refs=["RAC/2026/01/0026 <- STJ/2026/01/0690"],
                    external_clearing_basis="external.origin_purchase_line_id",
                    external_clearing_verified=True,
                )
            ],
            bill_refs=["BILL/2026/0003"],
        )

        seeds = page._build_pcb_seeds_for_cycle(cycle)

        self.assertEqual(seeds[0]["source_label"], "Item Balance | ExtClr 120.00")
        self.assertEqual(seeds[0]["external_clearing_amount"], 120.0)
        self.assertEqual(seeds[0]["external_clearing_refs"], ["RAC/2026/01/0026 <- STJ/2026/01/0690"])
        self.assertEqual(seeds[0]["external_clearing_basis"], "external.origin_purchase_line_id")
        self.assertTrue(seeds[0]["external_clearing_verified"])

    def test_dashboard_pcb_case4_seed_marks_missing_expense_account_incomplete(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page._selected_company_id = lambda: 7
        page._selected_company_name = lambda: "Alpha Company"
        cycle = _build_pcb_cycle(
            picking_id=7004,
            case2_repair_rows=[
                _build_pcb_case2_row(
                    row_key="case4::7004::901",
                    cycle_key="case4::7004",
                    pcb_case="case4",
                    pcb_case_label=PCB_CASE4_LABEL,
                    expense_account_code="",
                    guard_flags=["missing_expense_account"],
                    guard_messages=["Akun HPP/expense item dari kategori tidak ditemukan untuk residual HPP kategori."],
                    planned_lines=[
                        SvlDashboardPcbRepairPlannedLine(
                            role="problem_2103006",
                            account_code="2103006",
                            amount=100.0,
                            side="debit",
                        ),
                        SvlDashboardPcbRepairPlannedLine(
                            role="selisih_hpp",
                            account_code="",
                            amount=100.0,
                            side="credit",
                        ),
                    ],
                    line_label="SKU-001 - Produk PCB - Case 4 - STJ Bill Hit Expenses (Suspend - Expenses)",
                )
            ],
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
                )
            ],
            raw_lines=[],
            bill_refs=["BILL/2026/0004"],
        )

        seeds = page._build_pcb_seeds_for_cycle(cycle)

        self.assertEqual(len(seeds), 1)
        self.assertEqual(seeds[0]["pcb_case"], "case4")
        self.assertEqual(seeds[0]["row_status"], "incomplete")
        self.assertIn("missing_expense_account", seeds[0]["guard_flags"])
        self.assertIn("kategori", seeds[0]["row_status_message"].lower())

    def test_dashboard_pcb_case34_bill_only_seed_marks_needs_review_even_when_cycle_stays_case_lainnya(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page._selected_company_id = lambda: 7
        page._selected_company_name = lambda: "Alpha Company"
        cycle = _build_pcb_cycle(
            picking_id=7005,
            case2_repair_rows=[
                _build_pcb_case2_row(
                    row_key="case4::7005::901",
                    cycle_key="case4::7005",
                    pcb_case="case4",
                    pcb_case_label=PCB_CASE4_LABEL,
                    review_required=True,
                    review_confirmed=False,
                    review_reason="Bill item sudah ada, tetapi belum ada direct STJ / correction STJ / external clearing terverifikasi.",
                    guard_flags=["review_required_case34_bill_only", "problem_non_zero:2103006"],
                    guard_messages=[
                        "Bill item sudah ada, tetapi belum ada direct STJ / correction STJ / external clearing terverifikasi.",
                        "Saldo akun problem 2103006 item masih -100.00.",
                    ],
                    planned_lines=[
                        SvlDashboardPcbRepairPlannedLine(
                            role="problem_2103006",
                            account_code="2103006",
                            amount=100.0,
                            side="debit",
                        ),
                        SvlDashboardPcbRepairPlannedLine(
                            role="selisih_hpp",
                            account_code="5101004",
                            amount=100.0,
                            side="credit",
                        ),
                    ],
                )
            ],
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
                )
            ],
            item_rows=[
                SvlDashboardCycleItemRow(
                    product_id=901,
                    product_name="Produk Bill Only",
                    default_code="SKU-901",
                    has_item_bill=True,
                    has_item_stj=False,
                    eligible_case34=False,
                )
            ],
            raw_lines=[],
            bill_refs=["BILL/2026/0005"],
        )

        self.assertEqual(page._classify_pcb_cycle_case(cycle), "case_lainnya")
        seeds = page._build_pcb_seeds_for_cycle(cycle)

        self.assertEqual(len(seeds), 1)
        self.assertEqual(seeds[0]["pcb_case"], "case4")
        self.assertEqual(seeds[0]["row_status"], "needs_review")
        self.assertTrue(seeds[0]["review_required"])
        self.assertFalse(seeds[0]["review_confirmed"])
        self.assertIn("terverifikasi", seeds[0]["row_status_message"].lower())

    def test_dashboard_pcb_seed_builds_resolve_candidates_and_defaults(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page.context = mock.Mock(
            global_settings=GlobalSettings(
                repair_account_entries=[RepairAccountEntry(coa_code="9999999", label="Manual Override Account")]
            )
        )
        page._selected_company_id = lambda: 7
        page._selected_company_name = lambda: "Alpha Company"
        cycle = _build_pcb_cycle(
            picking_id=7006,
            case2_repair_rows=[_build_pcb_case2_row(row_key="case2::7006::901", cycle_key="case2::7006")],
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
                    account_id=202,
                    code="5101004",
                    name="HPP Rokok",
                    account_type="expense_direct_cost",
                    account_group="expense",
                    debit=70.0,
                    credit=0.0,
                    net_balance=70.0,
                    status="acceptable",
                ),
            ],
        )

        seeds = page._build_pcb_seeds_for_cycle(cycle)

        self.assertEqual(len(seeds), 1)
        seed = seeds[0]
        self.assertEqual(seed["resolve_account_code"], "5101004")
        self.assertEqual(seed["suggested_expense_account_code"], "5101004")
        self.assertEqual(seed["resolve_account_preview"], "5101004 - HPP Rokok")
        self.assertNotIn("[", seed["resolve_account_preview"])
        self.assertNotIn("PCB", seed["resolve_account_preview"])
        local_candidate_codes = {
            normalize_text(candidate.code).upper()
            for candidate in list(seed["account_candidates"] or [])
        }
        self.assertIn("2103006", local_candidate_codes)
        self.assertIn("5101004", local_candidate_codes)
        combined_candidate_codes = {
            normalize_text(candidate.code).upper()
            for candidate in page._repair_account_candidates_for_seed(seed)
        }
        self.assertIn("9999999", combined_candidate_codes)

    def test_dashboard_pcb_manual_resolve_override_rewrites_target_lines_and_unblocks_missing_expense(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page.context = mock.Mock(global_settings=GlobalSettings())
        row = {
            "row_key": "case4::7007::901",
            "cycle_key": "case4::7007",
            "pcb_case": "case4",
            "company_id": 7,
            "company_name": "Alpha Company",
            "amount": 100.0,
            "date": "2026-03-19",
            "reference": "Correction",
            "line_label": "SKU-001 - Produk PCB - Case 4",
            "expense_account_code": "",
            "suggested_expense_account_code": "",
            "resolve_account_code": "1108099",
            "resolve_account_manual": True,
            "guard_flags": ["missing_expense_account", "review_required_case34_bill_only"],
            "guard_messages": [
                "Akun HPP/expense item dari kategori tidak ditemukan untuk line Zero HPP / Selisih HPP.",
                "Bill item sudah ada, tetapi belum ada direct STJ / correction STJ / external clearing terverifikasi.",
            ],
            "review_required": True,
            "review_confirmed": False,
            "review_reason": "Bill item sudah ada, tetapi belum ada direct STJ / correction STJ / external clearing terverifikasi.",
            "problem_balances_by_code": {"2103006": -100.0},
            "planned_lines": [
                {
                    "role": "problem_2103006",
                    "account_code": "2103006",
                    "account_name": "Hutang Suspend",
                    "amount": 100.0,
                    "side": "debit",
                },
                {
                    "role": "selisih_hpp",
                    "account_code": "",
                    "account_name": "",
                    "amount": 100.0,
                    "side": "credit",
                },
            ],
            "account_candidates": [
                SvlDashboardRepairAccountCandidate(code="1108099", name="Clearing", source="PCB Problem", role="other")
            ],
        }

        page._sync_pcb_case2_row_defaults(row)

        self.assertEqual(row["expense_account_code"], "1108099")
        self.assertEqual(row["resolve_account_code"], "1108099")
        self.assertEqual(row["planned_lines"][1]["account_code"], "1108099")
        self.assertEqual(row["row_status"], "needs_review")
        self.assertNotIn("kategori", row["row_status_message"].lower())

    def test_dashboard_pcb_seed_marks_needs_review_when_projected_problem_accounts_remain(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page.context = mock.Mock(global_settings=GlobalSettings())
        page._selected_company_id = lambda: 7
        page._selected_company_name = lambda: "Alpha Company"
        cycle = _build_pcb_cycle(
            picking_id=7111,
            case2_repair_rows=[
                _build_pcb_case2_row(
                    row_key="case2::7111::901",
                    cycle_key="case2::7111",
                    picking_id=7111,
                    picking_name="LHPK/IN/7111",
                    review_required=False,
                    review_reason="",
                    planned_lines=[
                        SvlDashboardPcbRepairPlannedLine(
                            role="problem_2103006",
                            account_code="2103006",
                            amount=50.0,
                            side="debit",
                        ),
                        SvlDashboardPcbRepairPlannedLine(
                            role="selisih_hpp",
                            account_code="5101004",
                            amount=50.0,
                            side="credit",
                        ),
                    ],
                )
            ],
            item_rows=[
                SvlDashboardCycleItemRow(
                    product_id=901,
                    product_name="Produk Review",
                    default_code="SKU-901",
                    has_item_bill=True,
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
                        )
                    ],
                )
            ],
        )

        seed = page._build_pcb_seeds_for_cycle(cycle)[0]

        self.assertTrue(seed["review_required_projected"])
        self.assertTrue(seed["review_required"])
        self.assertEqual(seed["review_confirmed"], False)
        self.assertEqual(seed["projected_problem_codes"], ["2103006"])
        self.assertEqual(seed["row_status"], "needs_review")
        self.assertIn("Proyeksi Jurnal Per Item", seed["review_reason"])
        self.assertIn("2103006 -50.00", seed["review_reason"])

    def test_dashboard_pcb_projected_review_reset_clears_confirmed_and_preserves_base_reason(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page.context = mock.Mock(global_settings=GlobalSettings())
        page._selected_company_id = lambda: 7
        page._selected_company_name = lambda: "Alpha Company"
        cycle = _build_pcb_cycle(
            picking_id=7112,
            case2_repair_rows=[
                _build_pcb_case2_row(
                    row_key="case4::7112::901",
                    cycle_key="case4::7112",
                    pcb_case="case4",
                    pcb_case_label=PCB_CASE4_LABEL,
                    review_required=True,
                    review_reason="Manual review wajib sebelum execute.",
                    planned_lines=[
                        SvlDashboardPcbRepairPlannedLine(
                            role="problem_2103006",
                            account_code="2103006",
                            amount=50.0,
                            side="debit",
                        ),
                        SvlDashboardPcbRepairPlannedLine(
                            role="selisih_hpp",
                            account_code="5101004",
                            amount=50.0,
                            side="credit",
                        ),
                    ],
                )
            ],
            item_rows=[
                SvlDashboardCycleItemRow(
                    product_id=901,
                    product_name="Produk Review Reset",
                    default_code="SKU-901",
                    has_item_bill=True,
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
                        )
                    ],
                )
            ],
        )
        row = page._build_pcb_seeds_for_cycle(cycle)[0]
        item_row = cycle.item_rows[0]
        row["review_confirmed"] = True

        page._sync_pcb_case2_row_defaults(row, cycle=cycle, item_row=item_row)

        self.assertFalse(row["review_confirmed"])
        self.assertTrue(row["review_required"])
        self.assertTrue(row["review_required_projected"])
        self.assertEqual(row["row_status"], "needs_review")
        self.assertIn("Manual review wajib sebelum execute.", row["review_reason"])
        self.assertIn("Proyeksi Jurnal Per Item", row["review_reason"])

    def test_dashboard_pcb_case9_downstream_projected_review_requires_zero_clearing(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page.context = mock.Mock(global_settings=GlobalSettings())
        page._selected_company_id = lambda: 7
        page._selected_company_name = lambda: "Alpha Company"
        cycle = _build_pcb_cycle(
            picking_id=7113,
            case2_repair_rows=[
                _build_pcb_case2_row(
                    row_key="case9::7113::901",
                    cycle_key="case9::7113",
                    pcb_case="case9",
                    pcb_case_label="Case 9 - UoM Scale Mismatch",
                    review_required=True,
                    review_reason="Case 9 wajib review.",
                    planned_lines=[
                        SvlDashboardPcbRepairPlannedLine(
                            role="case9_hpp_reclass",
                            account_code="5101003",
                            account_name="HPP - Makanan",
                            amount=37620000.0,
                            side="debit",
                        ),
                        SvlDashboardPcbRepairPlannedLine(
                            role="case9_hpp_clearing_offset",
                            account_code="1108099",
                            account_name="Clearing",
                            amount=37620000.0,
                            side="credit",
                        ),
                        SvlDashboardPcbRepairPlannedLine(
                            role="case9_inventory_offset",
                            account_code="1105003",
                            account_name="Persediaan Makanan",
                            amount=38000000.0,
                            side="credit",
                        ),
                        SvlDashboardPcbRepairPlannedLine(
                            role="case9_inventory_clearing_offset",
                            account_code="1108099",
                            account_name="Clearing",
                            amount=38000000.0,
                            side="debit",
                        ),
                    ],
                    case_evidence={"holder_basis": "case2_downstream_clearing"},
                )
            ],
            item_rows=[
                SvlDashboardCycleItemRow(
                    product_id=901,
                    product_name="MIE RAMEN",
                    default_code="F-FZRP-0202",
                    has_item_bill=True,
                    has_item_stj=True,
                    account_rows=[
                        SvlDashboardCycleAccountRow(
                            account_id=301,
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
                            account_id=302,
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
        )

        seed = page._build_pcb_seeds_for_cycle(cycle)[0]

        self.assertTrue(seed["review_required_projected"])
        self.assertEqual(seed["projected_problem_codes"], ["1108099"])
        self.assertIn("Case 9 wajib review.", seed["review_reason"])
        self.assertTrue(seed["review_required"])
        self.assertEqual(seed["row_status"], "needs_review")

        # Approved five-line plan: clearing is zero, economic gap stays in HPP.
        lines = cycle.case2_repair_rows[0].planned_lines
        lines[3].amount = 37620000.0
        lines.append(SvlDashboardPcbRepairPlannedLine(
            role="case9_price_gap_to_expense", account_code="5101003",
            account_name="HPP - Makanan", amount=380000.0, side="debit",
        ))
        seed = page._build_pcb_seeds_for_cycle(cycle)[0]
        self.assertFalse(seed["review_required_projected"])
        self.assertEqual(seed["projected_problem_codes"], [])
        self.assertTrue(seed["review_required"])
        self.assertEqual(seed["row_status"], "needs_review")

    def test_merge_pcb_seed_preserves_manual_resolve_override_for_same_row_key(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page.context = mock.Mock(global_settings=GlobalSettings())
        page._module_settings = SvlFixJeSettings()
        page._selected_company_id = lambda: 7
        page._selected_company_name = lambda: "Alpha Company"
        latest_cycle = _build_pcb_cycle(
            picking_id=7008,
            case2_repair_rows=[_build_pcb_case2_row(row_key="case2::7008::901", cycle_key="case2::7008")],
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
                    account_id=202,
                    code="5101004",
                    name="HPP Rokok",
                    account_type="expense_direct_cost",
                    account_group="expense",
                    debit=70.0,
                    credit=0.0,
                    net_balance=70.0,
                    status="acceptable",
                ),
            ],
        )
        latest_seed = page._build_pcb_seeds_for_cycle(latest_cycle)[0]
        current_row = dict(latest_seed)
        current_row["resolve_account_manual"] = True
        current_row["resolve_account_code"] = "1108099"
        page._sync_pcb_case2_row_defaults(current_row)

        self.assertEqual(current_row["expense_account_code"], "1108099")

        page._merge_pcb_case1_seed_into_row(current_row, latest_seed)

        self.assertTrue(current_row["resolve_account_manual"])
        self.assertEqual(current_row["resolve_account_code"], "1108099")
        self.assertEqual(current_row["suggested_expense_account_code"], "5101004")
        self.assertEqual(current_row["expense_account_code"], "1108099")
        self.assertEqual(current_row["planned_lines"][-1]["account_code"], "1108099")

    def test_collect_all_pcb_bermasalah_includes_case1_and_case2_visible_cycles(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page._display_name = "Fixing Unlink SVL - Odoo"
        page.status_var = _FakeVar("")
        page._selected_company_id = lambda: 7
        page._selected_company_name = lambda: "Alpha Company"
        page._pcb_repair_collection = OrderedDict()
        case1_cycle = _build_pcb_cycle(
            picking_id=7001,
            case1_link_rows=[
                SvlDashboardPcbCase1LinkRow(
                    cycle_key="case1::7001",
                    source_key="case1::7001::901::bill_line::8101",
                    source_kind="bill_line",
                    source_id=8101,
                    product_id=901,
                    item_code="SKU-001",
                    item_name="Produk A",
                    bill_move_id=8201,
                    picking_id=7001,
                    picking_name="LHPK/IN/7001",
                    bill_name="BILL/2026/0001",
                    partner_id=77,
                    partner_name="Vendor Alpha",
                    debit_account_code="1108099",
                    credit_account_code="2103006",
                    allocated_amount=120.0,
                )
            ],
        )
        case2_cycle = _build_pcb_cycle(
            picking_id=7002,
            case2_repair_rows=[
                _build_pcb_case2_row(
                    row_key="case2::7002::901",
                    cycle_key="case2::7002",
                    picking_id=7002,
                    picking_name="LHPK/IN/7002",
                    coefficient_variance=42.0,
                    guard_flags=["problem_non_zero:2103006", "coefficient_variance_high"],
                    guard_messages=[
                        "Saldo akun problem 2103006 item masih -100.00.",
                        "Coefficient Variance 42.00% melebihi batas 35.00%.",
                    ],
                )
            ],
        )
        page._pcb_visible_cycles = [case1_cycle, case2_cycle]
        page._confirm_pcb_case2_guard_rows = mock.Mock(return_value=True)
        page._add_pcb_cycle_to_collection = mock.Mock(side_effect=[True, True])

        page._collect_all_pcb_bermasalah()

        page._confirm_pcb_case2_guard_rows.assert_called_once()
        confirmed_seeds = page._confirm_pcb_case2_guard_rows.call_args.args[0]
        self.assertEqual(len(confirmed_seeds), 1)
        self.assertEqual(confirmed_seeds[0]["row_key"], "case2::7002::901")
        self.assertEqual(
            page._confirm_pcb_case2_guard_rows.call_args.kwargs["source_label"],
            "2 cycle visible (Semua Case Bermasalah)",
        )
        self.assertEqual(page._add_pcb_cycle_to_collection.call_count, 2)

    def test_collect_all_pcb_bermasalah_filters_visible_cycles_by_case(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page._display_name = "Fixing Unlink SVL - Odoo"
        page.status_var = _FakeVar("")
        page._selected_company_id = lambda: 7
        page._selected_company_name = lambda: "Alpha Company"
        page._pcb_repair_collection = OrderedDict()
        case1_cycle = _build_pcb_cycle(
            picking_id=7101,
            case1_link_rows=[
                SvlDashboardPcbCase1LinkRow(
                    cycle_key="case1::7101",
                    source_key="case1::7101::901::bill_line::8101",
                    source_kind="bill_line",
                    source_id=8101,
                    product_id=901,
                    allocated_amount=120.0,
                )
            ],
        )
        case2_cycle = _build_pcb_cycle(
            picking_id=7102,
            case2_repair_rows=[
                _build_pcb_case2_row(
                    row_key="case2::7102::901",
                    cycle_key="case2::7102",
                    picking_id=7102,
                    picking_name="LHPK/IN/7102",
                )
            ],
        )
        page._pcb_visible_cycles = [case1_cycle, case2_cycle]
        page._confirm_pcb_case2_guard_rows = mock.Mock(return_value=True)
        page._add_pcb_cycle_to_collection = mock.Mock(return_value=True)

        page._collect_all_pcb_bermasalah(case_filter="case2")

        page._confirm_pcb_case2_guard_rows.assert_called_once()
        confirmed_seeds = page._confirm_pcb_case2_guard_rows.call_args.args[0]
        self.assertEqual([seed["row_key"] for seed in confirmed_seeds], ["case2::7102::901"])
        page._add_pcb_cycle_to_collection.assert_called_once_with(case2_cycle, skip_case2_guard_confirm=True)
        self.assertIn("Case 2", page.status_var.get())

    def test_collect_all_pcb_bermasalah_filters_case_lainnya_visible_cycles(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page._display_name = "Fixing Unlink SVL - Odoo"
        page.status_var = _FakeVar("")
        page._selected_company_id = lambda: 7
        page._selected_company_name = lambda: "Alpha Company"
        page._pcb_repair_collection = OrderedDict()
        case4_cycle = _build_pcb_cycle(
            picking_id=7103,
            case2_repair_rows=[
                _build_pcb_case2_row(
                    row_key="case4::7103::901",
                    cycle_key="case4::7103",
                    pcb_case="case4",
                    pcb_case_label=PCB_CASE4_LABEL,
                    review_required=True,
                    review_confirmed=False,
                    review_reason="Bill item sudah ada, tetapi belum ada direct STJ / correction STJ / external clearing terverifikasi.",
                    guard_flags=["review_required_case34_bill_only", "problem_non_zero:2103006"],
                    guard_messages=[
                        "Bill item sudah ada, tetapi belum ada direct STJ / correction STJ / external clearing terverifikasi.",
                        "Saldo akun problem 2103006 item masih -100.00.",
                    ],
                    planned_lines=[
                        SvlDashboardPcbRepairPlannedLine(
                            role="problem_2103006",
                            account_code="2103006",
                            amount=100.0,
                            side="debit",
                        ),
                        SvlDashboardPcbRepairPlannedLine(
                            role="selisih_hpp",
                            account_code="5101004",
                            amount=100.0,
                            side="credit",
                        ),
                    ],
                )
            ],
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
                )
            ],
            item_rows=[
                SvlDashboardCycleItemRow(
                    product_id=901,
                    product_name="Produk Bill Only",
                    default_code="SKU-901",
                    has_item_bill=True,
                    has_item_stj=False,
                    eligible_case34=False,
                )
            ],
            raw_lines=[],
            bill_refs=["BILL/2026/0005"],
        )
        case1_cycle = _build_pcb_cycle(
            picking_id=7104,
            case1_link_rows=[
                SvlDashboardPcbCase1LinkRow(
                    cycle_key="case1::7104",
                    source_key="case1::7104::902::bill_line::8102",
                    source_kind="bill_line",
                    source_id=8102,
                    product_id=902,
                    allocated_amount=120.0,
                )
            ],
        )
        page._pcb_visible_cycles = [case4_cycle, case1_cycle]
        page._confirm_pcb_case2_guard_rows = mock.Mock(return_value=True)
        page._add_pcb_cycle_to_collection = mock.Mock(return_value=True)

        self.assertEqual(page._classify_pcb_cycle_case(case4_cycle), "case_lainnya")

        page._collect_all_pcb_bermasalah(case_filter="case_lainnya")

        page._add_pcb_cycle_to_collection.assert_called_once_with(case4_cycle, skip_case2_guard_confirm=True)
        self.assertIn("Case Lainnya", page.status_var.get())

    def test_dashboard_pcb_case2_guard_gate_ignores_non_zero_balances_below_35_percent(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        seed = {
            "pcb_case": "case2",
            "coefficient_variance": 8.45,
            "guard_flags": ["problem_non_zero:2103006", "hpp_non_zero", "inventory_non_zero"],
            "guard_messages": [
                "Saldo akun problem 2103006 item masih -100.00.",
                "Saldo HPP item (5101004) masih +70.00.",
                "Saldo persediaan item (1105004) masih +70.00.",
            ],
        }

        self.assertFalse(page._pcb_case2_guard_required(seed))

    def test_apply_pcb_search_filter_matches_adjustment_move_name_and_ref(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        cycle = _build_pcb_cycle(
            picking_id=7001,
            adjustment_audit_rows=[
                _build_pcb_adjustment_audit_row(
                    move_name="RAC/2026/01/0026",
                    move_ref="Adjustment clearing from standard price",
                )
            ],
        )
        page._latest_snapshot = SvlDashboardSnapshot(
            database="hwgroup_erp",
            company_id=7,
            company_name="Alpha Company",
            generated_at="2026-03-27T10:00:00",
            period="2026-03-01..2026-03-31",
            purchase_cycles=[cycle],
        )
        page._pcb_show_problem_var = _FakeVar(True)
        page._pcb_show_partial_var = _FakeVar(True)
        page._pcb_show_healthy_var = _FakeVar(True)
        page._render_purchase_cycle_sidebar = mock.Mock()

        page._current_search_query = lambda: "rac/2026/01/0026"
        page._apply_pcb_search_filter()
        self.assertEqual(page._render_purchase_cycle_sidebar.call_args.kwargs["filtered_cycles"], [cycle])

        page._current_search_query = lambda: "standard price"
        page._apply_pcb_search_filter()
        self.assertEqual(page._render_purchase_cycle_sidebar.call_args.kwargs["filtered_cycles"], [cycle])

    def test_render_purchase_cycle_sidebar_groups_partial_cycles_by_bill_state(self) -> None:
        try:
            root = tk.Tk()
            root.withdraw()
        except tk.TclError as exc:
            self.skipTest(f"Tk unavailable: {exc}")
        try:
            page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
            page.root = root
            page.sidebar_tree = ttk.Treeview(root)
            page.sidebar_summary_var = tk.StringVar(master=root, value="")
            page._sidebar_item_frames = {}
            page._sidebar_tree_meta = {}
            page._sidebar_tree_item_id_by_pid = {}
            page._sidebar_tree_pid_by_item_id = {}
            page._set_empty_detail = mock.Mock()

            unpaid_cycle = _build_pcb_cycle(picking_id=7001, cycle_status="partial", bill_refs=["BILL/2026/0001"])
            unpaid_cycle.partial_group_key = "bill_unpaid"
            unpaid_cycle.partial_group_label = "Bill Belum Paid"
            unmatched_cycle = _build_pcb_cycle(picking_id=7002, cycle_status="partial", bill_refs=["BILL/2026/0002"])
            unmatched_cycle.partial_group_key = "bill_unmatched"
            unmatched_cycle.partial_group_label = "Bill Belum Matching"
            unreconciled_cycle = _build_pcb_cycle(picking_id=7003, cycle_status="partial", bill_refs=["BILL/2026/0003"])
            unreconciled_cycle.partial_group_key = "payment_unreconciled"
            unreconciled_cycle.partial_group_label = "Payment Belum Reconcile"

            page._latest_snapshot = SvlDashboardSnapshot(
                database="hwgroup_erp",
                company_id=7,
                company_name="Alpha Company",
                generated_at="2026-03-28T10:00:00",
                period="2026-03-01..2026-03-31",
                purchase_cycles=[unpaid_cycle, unmatched_cycle, unreconciled_cycle],
            )

            page._render_purchase_cycle_sidebar()
            root.update_idletasks()

            top_level = page.sidebar_tree.get_children("")
            self.assertEqual(len(top_level), 1)
            partial_root = top_level[0]
            self.assertIn("Cycle Sebagian (3)", page.sidebar_tree.item(partial_root, "text"))
            partial_groups = [page.sidebar_tree.item(item_id, "text") for item_id in page.sidebar_tree.get_children(partial_root)]
            self.assertEqual(partial_groups, [
                "  Bill Belum Paid (1)",
                "  Bill Belum Matching (1)",
                "  Payment Belum Reconcile (1)",
            ])
        finally:
            root.destroy()

    def test_render_purchase_cycle_detail_shows_adjustment_warning_and_audit_rows(self) -> None:
        try:
            root = tk.Tk()
            root.withdraw()
        except tk.TclError as exc:
            self.skipTest(f"Tk unavailable: {exc}")
        try:
            page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
            page.root = root
            page.pcb_detail_tree = ttk.Treeview(
                root,
                columns=tuple(f"c{index}" for index in range(12)),
                show="headings",
            )
            page._pcb_item_header_var = _FakeVar("")
            page._populate_pcb_acct_summary = mock.Mock()
            page._populate_pcb_raw_tree = mock.Mock()
            cycle = _build_pcb_cycle(
                picking_id=7001,
                adjustment_warning_text="Warning: Clearing adjustment -> RAC/2026/01/0026",
                adjustment_audit_rows=[_build_pcb_adjustment_audit_row()],
            )

            page._render_purchase_cycle_detail(cycle)
            root.update_idletasks()

            rows = [page.pcb_detail_tree.item(item_id, "values") for item_id in page.pcb_detail_tree.get_children()]
            self.assertTrue(any("Warning: Clearing adjustment -> RAC/2026/01/0026" in row[7] for row in rows))
            self.assertTrue(any(row[1] == "Adj Audit" and row[2] == "RAC/2026/01/0026" for row in rows))
            self.assertTrue(any(row[1] == "Adj Audit" and "Origin: STJ/2026/01/0690" in row[7] for row in rows))
            page._populate_pcb_acct_summary.assert_called_once_with(cycle.account_rows, cycle=cycle)
            page._populate_pcb_raw_tree.assert_called_once_with(cycle)
        finally:
            root.destroy()

    def test_render_purchase_cycle_item_detail_shows_item_adjustment_audit_rows(self) -> None:
        try:
            root = tk.Tk()
            root.withdraw()
        except tk.TclError as exc:
            self.skipTest(f"Tk unavailable: {exc}")
        try:
            page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
            page.root = root
            page.pcb_detail_tree = ttk.Treeview(
                root,
                columns=tuple(f"c{index}" for index in range(12)),
                show="headings",
            )
            page._pcb_item_header_var = _FakeVar("")
            page._populate_pcb_acct_summary = mock.Mock()
            page._populate_pcb_raw_tree = mock.Mock()
            item_row = SvlDashboardCycleItemRow(
                product_id=901,
                product_name="Produk PCB",
                default_code="SKU-001",
                valuation_method="automated",
                bill_refs=["BILL/2026/0001"],
                has_item_bill=True,
                stj_refs=["STJ/2026/01/0690"],
                has_item_stj=True,
                external_clearing_amount=100.0,
                external_clearing_refs=["RAC/2026/01/0026 <- STJ/2026/01/0690"],
                external_clearing_basis="external.origin_purchase_line_id",
                external_clearing_verified=True,
                verified_audit_clearing_amount=100.0,
                eligible_case34=True,
                account_rows=[
                    SvlDashboardCycleAccountRow(
                        account_id=302,
                        code="1108099",
                        name="Clearing",
                        account_type="asset_current",
                        account_group="asset",
                        debit=100.0,
                        credit=0.0,
                        net_balance=100.0,
                        status="problem",
                    )
                ],
                adjustment_audit_rows=[_build_pcb_adjustment_audit_row()],
            )
            cycle = _build_pcb_cycle(picking_id=7001, item_rows=[item_row])

            page._render_purchase_cycle_item_detail(cycle, item_row)
            root.update_idletasks()

            rows = [page.pcb_detail_tree.item(item_id, "values") for item_id in page.pcb_detail_tree.get_children()]
            self.assertTrue(any(row[1] == "Adj Audit" and row[2] == "RAC/2026/01/0026" for row in rows))
            self.assertTrue(any(row[1] == "Item Evidence" and "External Clearing: 100.00" in row[7] for row in rows))
            self.assertTrue(any(row[1] == "External Clearing" and "RAC/2026/01/0026 <- STJ/2026/01/0690" in row[3] for row in rows))
            self.assertTrue(any(row[1] == "Adj Audit" and "Origin: STJ/2026/01/0690" in row[7] for row in rows))
            page._populate_pcb_acct_summary.assert_called_once_with(item_row.account_rows, cycle=cycle, item_row=item_row)
            page._populate_pcb_raw_tree.assert_called_once_with(cycle)
        finally:
            root.destroy()

    def test_render_purchase_cycle_item_detail_shows_cycle_correction_stj_evidence(self) -> None:
        try:
            root = tk.Tk()
            root.withdraw()
        except tk.TclError as exc:
            self.skipTest(f"Tk unavailable: {exc}")
        try:
            page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
            page.root = root
            page.pcb_detail_tree = ttk.Treeview(
                root,
                columns=tuple(f"c{index}" for index in range(12)),
                show="headings",
            )
            page._pcb_item_header_var = _FakeVar("")
            page._populate_pcb_acct_summary = mock.Mock()
            page._populate_pcb_raw_tree = mock.Mock()
            item_row = SvlDashboardCycleItemRow(
                product_id=901,
                product_name="Produk PCB",
                default_code="SKU-001",
                valuation_method="automated",
                bill_refs=["BILL/2026/0001"],
                has_item_bill=True,
                correction_stj_refs=["STJ/2026/03/0160"],
                has_stj_evidence=True,
                eligible_case34=True,
                account_rows=[
                    SvlDashboardCycleAccountRow(
                        account_id=302,
                        code="1108099",
                        name="Clearing",
                        account_type="asset_current",
                        account_group="asset",
                        debit=100.0,
                        credit=0.0,
                        net_balance=100.0,
                        status="problem",
                    )
                ],
            )
            cycle = _build_pcb_cycle(picking_id=7001, item_rows=[item_row])

            page._render_purchase_cycle_item_detail(cycle, item_row)
            root.update_idletasks()

            rows = [page.pcb_detail_tree.item(item_id, "values") for item_id in page.pcb_detail_tree.get_children()]
            self.assertTrue(
                any(row[1] == "Item Evidence" and "STJ/2026/03/0160 (cycle correction)" in row[3] for row in rows)
            )
            page._populate_pcb_acct_summary.assert_called_once_with(item_row.account_rows, cycle=cycle, item_row=item_row)
            page._populate_pcb_raw_tree.assert_called_once_with(cycle)
        finally:
            root.destroy()

    def test_case2_planned_line_role_text_labels_audit_clearing(self) -> None:
        self.assertEqual(
            SvlFixJeDashboardPage._pcb_case2_planned_line_role_text("audit_clearing_1108099"),
            "Audit Clearing",
        )

    def test_build_pcb_seeds_ignore_adjustment_audit_rows_for_case1_to_case4(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page._selected_company_id = lambda: 7
        page._selected_company_name = lambda: "Alpha Company"

        audit_row = _build_pcb_adjustment_audit_row()
        case1_base = _build_pcb_cycle(
            picking_id=7001,
            case1_link_rows=[
                SvlDashboardPcbCase1LinkRow(
                    cycle_key="case1::7001",
                    source_key="case1::7001::901::bill_line::8101",
                    source_kind="bill_line",
                    source_id=8101,
                    product_id=901,
                    item_code="SKU-001",
                    item_name="Produk PCB",
                    bill_move_id=8201,
                    picking_id=7001,
                    picking_name="LHPK/IN/7001",
                    bill_name="BILL/2026/0001",
                    partner_id=77,
                    partner_name="Vendor Alpha",
                    debit_account_code="1108099",
                    credit_account_code="2103006",
                    allocated_amount=120.0,
                )
            ],
        )
        case1_audited = _build_pcb_cycle(
            picking_id=7001,
            case1_link_rows=list(case1_base.case1_link_rows),
            adjustment_warning_text="Warning: Clearing adjustment -> RAC/2026/01/0026",
            adjustment_audit_rows=[audit_row],
        )
        case2_base = _build_pcb_cycle(picking_id=7002, case2_repair_rows=[_build_pcb_case2_row()])
        case2_audited = _build_pcb_cycle(
            picking_id=7002,
            case2_repair_rows=[_build_pcb_case2_row()],
            adjustment_warning_text="Warning: Clearing adjustment -> RAC/2026/01/0026",
            adjustment_audit_rows=[audit_row],
        )
        case3_base = _build_pcb_cycle(
            picking_id=7003,
            case2_repair_rows=[
                _build_pcb_case2_row(
                    row_key="case3::7003::901",
                    cycle_key="case3::7003",
                    pcb_case="case3",
                    pcb_case_label=PCB_CASE3_LABEL,
                    line_label="SKU-001 - Produk PCB - Case 3 - STJ Bill Hit Expenses (Clearing - Expenses)",
                )
            ],
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
        )
        case3_audited = _build_pcb_cycle(
            picking_id=7003,
            case2_repair_rows=[
                _build_pcb_case2_row(
                    row_key="case3::7003::901",
                    cycle_key="case3::7003",
                    pcb_case="case3",
                    pcb_case_label=PCB_CASE3_LABEL,
                    line_label="SKU-001 - Produk PCB - Case 3 - STJ Bill Hit Expenses (Clearing - Expenses)",
                )
            ],
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
            adjustment_warning_text="Warning: Clearing adjustment -> RAC/2026/01/0026",
            adjustment_audit_rows=[audit_row],
        )
        case4_base = _build_pcb_cycle(
            picking_id=7004,
            case2_repair_rows=[
                _build_pcb_case2_row(
                    row_key="case4::7004::901",
                    cycle_key="case4::7004",
                    pcb_case="case4",
                    pcb_case_label=PCB_CASE4_LABEL,
                    line_label="SKU-001 - Produk PCB - Case 4 - STJ Bill Hit Expenses (Suspend - Expenses)",
                )
            ],
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
                )
            ],
            raw_lines=[],
            bill_refs=["BILL/2026/0004"],
        )
        case4_audited = _build_pcb_cycle(
            picking_id=7004,
            case2_repair_rows=[
                _build_pcb_case2_row(
                    row_key="case4::7004::901",
                    cycle_key="case4::7004",
                    pcb_case="case4",
                    pcb_case_label=PCB_CASE4_LABEL,
                    line_label="SKU-001 - Produk PCB - Case 4 - STJ Bill Hit Expenses (Suspend - Expenses)",
                )
            ],
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
                )
            ],
            raw_lines=[],
            bill_refs=["BILL/2026/0004"],
            adjustment_warning_text="Warning: Clearing adjustment -> RAC/2026/01/0026",
            adjustment_audit_rows=[audit_row],
        )

        for base_cycle, audited_cycle in (
            (case1_base, case1_audited),
            (case2_base, case2_audited),
            (case3_base, case3_audited),
            (case4_base, case4_audited),
        ):
            self.assertEqual(page._build_pcb_seeds_for_cycle(base_cycle), page._build_pcb_seeds_for_cycle(audited_cycle))

    def test_dashboard_pcb_resolve_sidebar_cycle_falls_back_to_previous_selection(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        cycle = _build_pcb_cycle(picking_id=7002)
        page._pcb_cycle_by_iid = {"cycle::7002": cycle}
        page.sidebar_tree = _FakeSidebarTree(selection=("cycle::7002",), focus="cycle::7002")

        resolved_cycle = page._resolve_pcb_sidebar_cycle("group::case2", fallback_item_ids=["cycle::7002"])

        self.assertIs(resolved_cycle, cycle)

    def test_dashboard_pcb_case2_non_actionable_reason_explains_missing_item_problem_balance(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        cycle = _build_pcb_cycle(
            picking_id=7050,
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
                    code="5101002",
                    name="HPP Beverage",
                    account_type="expense_direct_cost",
                    account_group="expense",
                    debit=100.0,
                    credit=0.0,
                    net_balance=100.0,
                    status="problem",
                ),
            ],
            item_rows=[
                SvlDashboardCycleItemRow(
                    product_id=901,
                    product_name="Produk X",
                    default_code="SKU-X",
                    account_rows=[
                        SvlDashboardCycleAccountRow(
                            account_id=401,
                            code="5101002",
                            name="HPP Beverage",
                            account_type="expense_direct_cost",
                            account_group="expense",
                            debit=100.0,
                            credit=0.0,
                            net_balance=100.0,
                            status="problem",
                        ),
                    ],
                )
            ],
            raw_lines=[{"jenis": "BILL", "tipe_akun": "expense_direct_cost", "akun_code": "5101002"}],
            bill_refs=["BILL/2026/0009"],
        )

        reason = page._pcb_cycle_non_actionable_reason(cycle)

        self.assertIn("belum ada item yang actionable", normalize_text(reason).lower())
        self.assertIn("1108099", reason)
        self.assertIn("2103006", reason)

    def test_dashboard_pcb_case_lainnya_builds_manual_reclass_seed_from_bill_expense_line(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page._selected_company_id = lambda: 7
        page._selected_company_name = lambda: "Alpha Company"
        cycle = _build_pcb_cycle(
            picking_id=7051,
            account_rows=[
                SvlDashboardCycleAccountRow(
                    account_id=301,
                    code="1101435",
                    name="MANDIRI - PT KREASI PONDOK INDAH BERJAYA",
                    account_type="asset_cash",
                    account_group="asset",
                    debit=0.0,
                    credit=370000.0,
                    net_balance=-370000.0,
                    status="acceptable",
                ),
                SvlDashboardCycleAccountRow(
                    account_id=302,
                    code="1105006",
                    name="Persediaan Habis Pakai / Consumables Inventory",
                    account_type="asset_current",
                    account_group="asset",
                    debit=550000.0,
                    credit=0.0,
                    net_balance=550000.0,
                    status="acceptable",
                ),
                SvlDashboardCycleAccountRow(
                    account_id=303,
                    code="1108099",
                    name="Clearing - System Pending Entries",
                    account_type="asset_current",
                    account_group="asset",
                    debit=0.0,
                    credit=550000.0,
                    net_balance=-550000.0,
                    status="problem",
                ),
                SvlDashboardCycleAccountRow(
                    account_id=304,
                    code="11120003",
                    name="Outstanding Payments",
                    account_type="asset_current",
                    account_group="asset",
                    debit=370000.0,
                    credit=370000.0,
                    net_balance=0.0,
                    status="info",
                ),
                SvlDashboardCycleAccountRow(
                    account_id=305,
                    code="2101002",
                    name="Hutang Pihak Ketiga - Pengadaan Barang/Jasa",
                    account_type="liability_payable",
                    account_group="liability",
                    debit=370000.0,
                    credit=370000.0,
                    net_balance=0.0,
                    status="acceptable",
                ),
                SvlDashboardCycleAccountRow(
                    account_id=306,
                    code="6201003",
                    name="Beban Perlengkapan Tamu / Guest Supplies Expense",
                    account_type="expense_direct_cost",
                    account_group="expense",
                    debit=370000.0,
                    credit=0.0,
                    net_balance=370000.0,
                    status="info",
                ),
            ],
            item_rows=[
                SvlDashboardCycleItemRow(
                    product_id=901,
                    product_name="COCKTAIL SKEWER (@100PCS)",
                    default_code="C-DGGP-0002",
                    account_rows=[
                        SvlDashboardCycleAccountRow(
                            account_id=303,
                            code="1108099",
                            name="Clearing - System Pending Entries",
                            account_type="asset_current",
                            account_group="asset",
                            debit=0.0,
                            credit=550000.0,
                            net_balance=-550000.0,
                            status="problem",
                        ),
                        SvlDashboardCycleAccountRow(
                            account_id=302,
                            code="1105006",
                            name="Persediaan Habis Pakai / Consumables Inventory",
                            account_type="asset_current",
                            account_group="asset",
                            debit=550000.0,
                            credit=0.0,
                            net_balance=550000.0,
                            status="acceptable",
                        ),
                    ],
                )
            ],
            raw_lines=[
                {
                    "kode_transaksi": "BILL/2026/01/0202",
                    "jenis": "BILL",
                    "tipe_akun": "expense_direct_cost",
                    "akun_code": "6201003",
                    "akun_name": "Beban Perlengkapan Tamu / Guest Supplies Expense",
                    "kode_item": "C-DGGP-0002",
                    "nama_item": "COCKTAIL SKEWER (@100PCS)",
                    "kategori_produk": "GUEST SUPPLIES",
                    "no_po": "PO/ATB/2026/01/00869",
                    "qty_item": 10.0,
                    "debit": 370000.0,
                    "kredit": 0.0,
                    "saldo": 370000.0,
                }
            ],
            bill_refs=["BILL/2026/01/0202"],
        )

        seeds = page._build_pcb_seeds_for_cycle(cycle)

        self.assertEqual(len(seeds), 1)
        self.assertEqual(seeds[0]["pcb_case"], "case_lainnya")
        self.assertEqual(seeds[0]["suggested_expense_account_code"], "6201003")
        self.assertEqual(seeds[0]["row_status"], "incomplete")
        self.assertIn("Manual reklas diperlukan", seeds[0]["review_reason"])
        self.assertEqual(seeds[0]["amount"], 370000.0)
        self.assertEqual(seeds[0]["account_name_by_code"]["6201003"], "Beban Perlengkapan Tamu / Guest Supplies Expense")

    def test_dashboard_pcb_add_non_actionable_cycle_shows_reason_message(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page._display_name = "Fixing Unlink SVL - Odoo"
        page.status_var = _FakeVar("")
        cycle = _build_pcb_cycle(
            picking_id=7051,
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
                    code="5101002",
                    name="HPP Beverage",
                    account_type="expense_direct_cost",
                    account_group="expense",
                    debit=100.0,
                    credit=0.0,
                    net_balance=100.0,
                    status="problem",
                ),
            ],
            item_rows=[
                SvlDashboardCycleItemRow(
                    product_id=901,
                    product_name="Produk X",
                    default_code="SKU-X",
                    account_rows=[],
                )
            ],
            raw_lines=[],
            bill_refs=[],
        )

        with mock.patch("smartscc_tools.modules.svl_fix_je_dashboard_page.messagebox.showinfo") as showinfo:
            result = page._add_pcb_cycle_to_collection(cycle)

        self.assertFalse(result)
        showinfo.assert_called_once()
        self.assertIn("belum punya row pcb repair yang actionable", normalize_text(showinfo.call_args.args[1]).lower())

    def test_dashboard_pcb_bulk_collect_uses_visible_case1_cycles_only(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page._display_name = "Fixing Unlink SVL - Odoo"
        page.status_var = _FakeVar("")
        page._selected_company_id = lambda: 1
        page._selected_company_name = lambda: "Alpha Company"
        visible_cycle = _build_pcb_cycle(
            picking_id=7001,
            case1_link_rows=[
                SvlDashboardPcbCase1LinkRow(
                    cycle_key="case1::7001",
                    source_key="case1::7001::901::bill_line::8101",
                    source_kind="bill_line",
                    source_id=8101,
                    product_id=901,
                    allocated_amount=100.0,
                )
            ],
        )
        hidden_cycle = _build_pcb_cycle(
            picking_id=7002,
            case1_link_rows=[
                SvlDashboardPcbCase1LinkRow(
                    cycle_key="case1::7002",
                    source_key="case1::7002::902::bill_line::8102",
                    source_kind="bill_line",
                    source_id=8102,
                    product_id=902,
                    allocated_amount=120.0,
                )
            ],
        )
        page._pcb_visible_cycles = [visible_cycle]
        page._latest_snapshot = SvlDashboardSnapshot(
            database="hwgroup_erp",
            company_id=1,
            company_name="Alpha Company",
            generated_at="2026-03-18T10:00:00",
            period="2026-03-01..2026-03-31",
            purchase_cycles=[visible_cycle, hidden_cycle],
        )
        page._add_pcb_cycle_to_collection = mock.Mock(return_value=True)

        with mock.patch("smartscc_tools.modules.svl_fix_je_dashboard_page.messagebox.showinfo") as info_box:
            page._collect_all_pcb_bermasalah()

        info_box.assert_not_called()
        page._add_pcb_cycle_to_collection.assert_called_once_with(visible_cycle, skip_case2_guard_confirm=True)
        self.assertIn("1 cycle visible", page.status_var.get())

    def test_apply_repair_global_defaults_to_row_passes_accounts_already_synced_to_state_refresh(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page._module_settings = SvlFixJeSettings()
        row = {
            "row_key": "row-1",
            "item_code": "ITEM-001",
            "item_name": "First Item",
            "base_reference": "ITEM-001",
            "base_line_label": "ITEM-001 First Item",
            "amount": 100.0,
            "signed_amount": 100.0,
            "date": "",
            "target_mode": "",
            "posting_mode": "",
            "target_account_role": "",
            "resolve_account_code": "",
        }
        page._sync_repair_row_account_codes = mock.Mock()
        page._refresh_repair_row_state = mock.Mock()

        applied = page._apply_repair_global_defaults_to_row(
            row,
            target_mode_value="new_and_relink",
            posting_mode_value="post",
            role_value="valuation",
            resolve_code="1108099",
            reference_prefix="FIX",
            global_candidates=[],
            category_candidates_cache={},
            combined_candidates_cache={},
            target_candidate_cache={},
            preview_map_cache={},
        )

        self.assertTrue(applied)
        self.assertEqual(page._sync_repair_row_account_codes.call_count, 1)
        page._refresh_repair_row_state.assert_called_once()
        self.assertTrue(page._refresh_repair_row_state.call_args.kwargs["accounts_already_synced"])
        self.assertEqual(row["target_mode"], "new_and_relink")
        self.assertEqual(row["posting_mode"], "post")
        self.assertEqual(row["target_account_role"], "valuation")
        self.assertEqual(row["resolve_account_code"], "1108099")
        self.assertTrue(normalize_text(row["reference"]).startswith("FIX"))

    def test_dashboard_open_repair_collection_dialog_uses_collection_order(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page._repair_collection = OrderedDict()
        page._repair_collection_scope = None
        page._repair_collection_sequence = 0
        page._repair_collection_changed_callback = None
        page.status_var = _FakeVar("")
        page.company_choice_var = _FakeVar("Alpha Company (#7)")
        page._display_name = "Fixing Unlink SVL - Odoo"
        page._selected_company_id = lambda: 7
        page._selected_database_profile_id = lambda: "db_live"
        page._label_for_company_id = lambda _company_id: "Alpha Company (#7)"
        page._resolve_effective_database_state = lambda selected_profile_id=None: {  # noqa: ARG005
            "selected_profile_id": "db_live",
            "selected_label": "Live ERP - hwgroup_erp [Database Live]",
            "effective_label": "Live ERP - hwgroup_erp [Database Live]",
            "effective_database": "hwgroup_erp",
        }
        captured: dict[str, object] = {}
        page._open_repair_dialog_for_rows = lambda rows, *, source_title, source_note: captured.update(  # type: ignore[assignment]
            {"rows": rows, "source_title": source_title, "source_note": source_note}
        )

        page._add_repair_collection_seeds(
            [
                {"row_key": "row-a", "amount": 100.0, "latest_snapshot_status": "Linked JE header kosong"},
                {"row_key": "row-b", "amount": 200.0, "latest_snapshot_status": "SVL tanpa JE"},
            ]
        )

        page.open_repair_collection_dialog()

        self.assertEqual([row["row_key"] for row in captured["rows"]], ["row-b", "row-a"])
        self.assertEqual(captured["source_title"], "Repair Collection")
        self.assertIn("lintas item", str(captured["source_note"]))

    def test_build_repair_seed_from_merged_row_includes_source_and_svl_metadata(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page._selected_company_id = lambda: 7
        item = mock.Mock(
            pid=11,
            code="ITEM-001",
            name="Baileys",
            categ="ALCOHOL / LIQUEUR / SWEET & CREAMY",
            repair_account_candidates=[],
        )
        row = mock.Mock(
            repair_candidate=True,
            row_key="svl_no_move:svl:501",
            row_type="svl_no_move",
            svl_value=125.5,
            net=0.0,
            move_date="",
            svl_date="2026-03-18",
            aml_date="",
            move_name="",
            svl_id=501,
            svl_qty=2.0,
            svl_unit_cost=62.75,
            svl_reference="SVL/BAILEYS/001",
            move_id=0,
            move_state="",
            status="SVL tanpa JE",
        )

        seed = page._build_repair_seed_from_merged_row(item, row)

        self.assertIsNotNone(seed)
        self.assertEqual(seed["repair_source_kind"], "svl_no_move")
        self.assertEqual(seed["repair_source_label"], "SVL tanpa JE")
        self.assertEqual(seed["svl_id"], 501)
        self.assertEqual(seed["svl_date"], "2026-03-18")
        self.assertEqual(seed["svl_qty"], 2.0)
        self.assertEqual(seed["svl_unit_cost"], 62.75)
        self.assertEqual(seed["svl_value"], 125.5)
        self.assertEqual(seed["svl_reference"], "SVL/BAILEYS/001")

    def test_build_repair_seed_from_merged_row_skips_zero_value_svl_without_je(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page._selected_company_id = lambda: 7
        item = mock.Mock(pid=11, code="ITEM-001", name="Baileys", categ="ALCOHOL", repair_account_candidates=[])
        row = mock.Mock(
            repair_candidate=True,
            row_key="svl_no_move:svl:502",
            row_type="svl_no_move",
            svl_value=0.0,
            net=0.0,
            move_date="",
            svl_date="2026-03-18",
            aml_date="",
            move_name="",
            svl_id=502,
            svl_qty=0.0,
            svl_unit_cost=0.0,
            svl_reference="SVL/BAILEYS/002",
            move_id=0,
            move_state="",
            status="SVL tanpa JE",
        )

        self.assertIsNone(page._build_repair_seed_from_merged_row(item, row))

    def test_collect_all_svl_without_je_filters_automated_and_skips_zero_value(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page._display_name = "Fixing Unlink SVL - Odoo"
        page.status_var = _FakeVar("")
        page._selected_company_id = lambda: 7
        page._latest_snapshot = mock.Mock(
            items=[
                mock.Mock(
                    item_kind="product",
                    automated_valuation=True,
                    merged_records=[
                        mock.Mock(
                            row_type="svl_no_move",
                            repair_candidate=True,
                            row_key="row-1",
                            svl_value=150.0,
                            net=0.0,
                            move_date="",
                            svl_date="2026-03-18",
                            aml_date="",
                            move_name="",
                            svl_id=501,
                            svl_qty=1.0,
                            svl_unit_cost=150.0,
                            svl_reference="SVL/001",
                            move_id=0,
                            move_state="",
                            status="SVL tanpa JE",
                        ),
                        mock.Mock(
                            row_type="svl_no_move",
                            repair_candidate=True,
                            row_key="row-2",
                            svl_value=0.0,
                            net=0.0,
                            move_date="",
                            svl_date="2026-03-18",
                            aml_date="",
                            move_name="",
                            svl_id=502,
                            svl_qty=0.0,
                            svl_unit_cost=0.0,
                            svl_reference="SVL/002",
                            move_id=0,
                            move_state="",
                            status="SVL tanpa JE",
                        ),
                    ],
                    pid=11,
                    code="ITEM-001",
                    name="Baileys",
                    categ="ALCOHOL",
                    repair_account_candidates=[],
                ),
                mock.Mock(
                    item_kind="product",
                    automated_valuation=False,
                    merged_records=[
                        mock.Mock(
                            row_type="svl_no_move",
                            repair_candidate=True,
                            row_key="row-3",
                            svl_value=200.0,
                            net=0.0,
                            move_date="",
                            svl_date="2026-03-18",
                            aml_date="",
                            move_name="",
                            svl_id=503,
                            svl_qty=1.0,
                            svl_unit_cost=200.0,
                            svl_reference="SVL/003",
                            move_id=0,
                            move_state="",
                            status="SVL tanpa JE",
                        )
                    ],
                    pid=12,
                    code="ITEM-002",
                    name="Whisky",
                    categ="SPIRIT",
                    repair_account_candidates=[],
                ),
            ]
        )
        captured_seeds: list[dict[str, object]] = []
        page._add_repair_collection_seeds = lambda seeds: (captured_seeds.extend(seeds), (len(seeds), 0))[1]  # type: ignore[assignment]

        added, moved = page.collect_all_svl_without_je(filter_automated_only=True)

        self.assertEqual((added, moved), (1, 0))
        self.assertEqual([seed["row_key"] for seed in captured_seeds], ["row-1"])
        self.assertEqual(captured_seeds[0]["repair_source_label"], "SVL tanpa JE")
        self.assertIn("1 row baru", page.status_var.get())

    def test_dashboard_company_filter_matches_name_and_id(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page._company_by_label = {
            "Alpha Company (#1)": SvlDashboardCompany(company_id=1, name="Alpha Company"),
            "Beta Ops (#22)": SvlDashboardCompany(company_id=22, name="Beta Ops"),
        }
        page._company_labels = list(page._company_by_label.keys())

        self.assertEqual(page._filtered_company_labels("alpha"), ["Alpha Company (#1)"])
        self.assertEqual(page._filtered_company_labels("22"), ["Beta Ops (#22)"])
        self.assertEqual(page._filtered_company_labels(""), page._company_labels)
        self.assertEqual(page._filtered_company_labels("alpah"), ["Alpha Company (#1)"])

    def test_dashboard_company_requires_explicit_selection(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page._company_by_label = {
            "Alpha Company (#1)": SvlDashboardCompany(company_id=1, name="Alpha Company"),
        }
        page._company_labels = list(page._company_by_label.keys())
        page._selected_company_id_value = 0
        page.company_choice_var = _FakeVar("Alpha Company (#1)")

        self.assertEqual(page._selected_company_id(), 0)

        page._selected_company_id_value = 1
        self.assertEqual(page._selected_company_id(), 1)

    def test_dashboard_company_search_refreshes_active_combobox_values(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page._busy = False
        page.root = _FakeRoot()
        page._selected_company_id_value = 0
        page._company_last_query_for_autodrop = ""
        page._company_dropdown_after_id = None
        page._company_by_label = {
            "AMBYAR MAG (#529)": SvlDashboardCompany(company_id=529, name="AMBYAR MAG"),
            "AMBYAR BASRA (#526)": SvlDashboardCompany(company_id=526, name="AMBYAR BASRA"),
            "AGAM BENHIL (#704)": SvlDashboardCompany(company_id=704, name="AGAM BENHIL"),
        }
        page._company_labels = list(page._company_by_label.keys())
        page._company_search_cache = {}
        page._company_active_labels = list(page._company_labels)
        page.company_choice_var = _FakeVar("AM")
        page.company_combo = _FakeWidget()

        page._on_company_search(mock.Mock(keysym="M"))

        self.assertEqual(page._company_active_labels, page._company_labels)
        self.assertEqual(page.company_combo.last_config["values"], page._company_labels)
        self.assertEqual(page.root.after_calls, [])
        self.assertEqual(page.company_combo.event_generate_calls, [])
        self.assertEqual(page._company_last_query_for_autodrop, "")

    def test_dashboard_company_dropdown_requested_uses_current_query(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page._busy = False
        page.root = _FakeRoot()
        page._selected_company_id_value = 0
        page._company_last_query_for_autodrop = ""
        page._company_dropdown_after_id = None
        page._company_by_label = {
            "AMBYAR MAG (#529)": SvlDashboardCompany(company_id=529, name="AMBYAR MAG"),
            "AMBYAR BASRA (#526)": SvlDashboardCompany(company_id=526, name="AMBYAR BASRA"),
            "AGAM BENHIL (#704)": SvlDashboardCompany(company_id=704, name="AGAM BENHIL"),
        }
        page._company_labels = list(page._company_by_label.keys())
        page._company_search_cache = {}
        page._company_active_labels = list(page._company_labels)
        page.company_choice_var = _FakeVar("AMBY")
        page.company_combo = _FakeWidget()

        page._on_company_dropdown_requested()

        expected = ["AMBYAR MAG (#529)", "AMBYAR BASRA (#526)"]
        self.assertEqual(page._company_active_labels, expected)
        self.assertEqual(page.company_combo.last_config["values"], expected)

    def test_dashboard_company_search_three_chars_schedules_native_dropdown_open(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page._busy = False
        page.root = _FakeRoot()
        page._selected_company_id_value = 0
        page._company_last_query_for_autodrop = ""
        page._company_dropdown_after_id = None
        page._company_by_label = {
            "AMBYAR MAG (#529)": SvlDashboardCompany(company_id=529, name="AMBYAR MAG"),
            "AMBYAR BASRA (#526)": SvlDashboardCompany(company_id=526, name="AMBYAR BASRA"),
            "AGAM BENHIL (#704)": SvlDashboardCompany(company_id=704, name="AGAM BENHIL"),
        }
        page._company_labels = list(page._company_by_label.keys())
        page._company_search_cache = {}
        page._company_active_labels = list(page._company_labels)
        page.company_choice_var = _FakeVar("AMB")
        page.company_combo = _FakeWidget()

        page._on_company_search(mock.Mock(keysym="B"))

        expected = page._filtered_company_labels("AMB")
        self.assertEqual(page._company_active_labels, expected)
        self.assertEqual(len(page.root.after_calls), 1)
        self.assertEqual(page._company_last_query_for_autodrop, "amb")
        _delay, callback = page.root.after_calls[0]
        callback()
        self.assertEqual(page.company_combo.event_generate_calls, ["<Down>"])

    def test_dashboard_company_search_fourth_char_reuses_existing_autodrop_cycle(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page._busy = False
        page.root = _FakeRoot()
        page._selected_company_id_value = 0
        page._company_last_query_for_autodrop = "amb"
        page._company_dropdown_after_id = "after#idle"
        page._company_by_label = {
            "AMBYAR MAG (#529)": SvlDashboardCompany(company_id=529, name="AMBYAR MAG"),
            "AMBYAR BASRA (#526)": SvlDashboardCompany(company_id=526, name="AMBYAR BASRA"),
            "AGAM BENHIL (#704)": SvlDashboardCompany(company_id=704, name="AGAM BENHIL"),
        }
        page._company_labels = list(page._company_by_label.keys())
        page._company_search_cache = {}
        page._company_active_labels = list(page._company_labels)
        page.company_choice_var = _FakeVar("AMBY")
        page.company_combo = _FakeWidget()

        page._on_company_search(mock.Mock(keysym="Y"))

        expected = ["AMBYAR MAG (#529)", "AMBYAR BASRA (#526)"]
        self.assertEqual(page._company_active_labels, expected)
        self.assertEqual(page.root.after_calls, [])
        self.assertEqual(page._company_last_query_for_autodrop, "amby")

    def test_dashboard_company_commit_uses_selected_filtered_label(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page._company_by_label = {
            "AGAM BENHIL (#704)": SvlDashboardCompany(company_id=704, name="AGAM BENHIL"),
            "AMBYAR MAG (#529)": SvlDashboardCompany(company_id=529, name="AMBYAR MAG"),
            "AMBYAR BASRA (#526)": SvlDashboardCompany(company_id=526, name="AMBYAR BASRA"),
        }
        page._company_labels = list(page._company_by_label.keys())
        page._company_search_cache = {}
        page._company_active_labels = ["AMBYAR MAG (#529)", "AMBYAR BASRA (#526)"]
        page._selected_company_id_value = 704
        page.company_choice_var = _FakeVar("AMBYAR BASRA (#526)")
        page.company_combo = _FakeWidget()
        page._save_settings = mock.Mock()
        page._clear_snapshot = mock.Mock()

        page._commit_company_selection()

        self.assertEqual(page._selected_company_id_value, 526)
        self.assertEqual(page.company_choice_var.get(), "AMBYAR BASRA (#526)")
        self.assertEqual(page.company_combo.last_config["values"], page._company_labels)
        page._save_settings.assert_called_once_with()
        page._clear_snapshot.assert_called_once_with(reset_company=False)

    def test_dashboard_busy_state_keeps_company_combo_editable_when_idle(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page._busy = False
        page._latest_snapshot = None
        page.database_combo = _FakeWidget()
        page.company_combo = _FakeWidget()
        page.date_from_entry = _FakeWidget()
        page.date_to_entry = _FakeWidget()
        page.btn_refresh_companies = _FakeWidget()
        page.btn_analyze = _FakeWidget()
        page.btn_export_html = _FakeWidget()
        page.btn_export_json = _FakeWidget()

        page._refresh_busy_state()

        self.assertEqual(page.database_combo.last_config["state"], "readonly")
        self.assertEqual(page.company_combo.last_config["state"], "normal")

    def test_dashboard_resolves_follow_global_to_dummy_summary_and_warning(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page.context = mock.Mock(global_settings=GlobalSettings(default_database_profile_id="db_dummy"))
        page._db_label_by_profile_id = {FOLLOW_GLOBAL_PROFILE_ID: "Follow Global Default"}
        page._db_profile_id_by_label = {"Follow Global Default": FOLLOW_GLOBAL_PROFILE_ID}
        page.database_choice_var = _FakeVar("Follow Global Default")

        state = page._resolve_effective_database_state(selected_profile_id=FOLLOW_GLOBAL_PROFILE_ID)

        self.assertEqual(
            state["effective_display"],
            "Effective DB: Follow Global Default -> Dummy ERP - hwgroup_erp_22022026 [Database Dummy]",
        )
        self.assertTrue(state["is_dummy"])
        self.assertIn("hwgroup_erp_22022026", state["warning_text"])

    def test_dashboard_resolves_explicit_live_without_dummy_warning(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page.context = mock.Mock(global_settings=GlobalSettings(default_database_profile_id="db_dummy"))
        page._db_label_by_profile_id = {
            FOLLOW_GLOBAL_PROFILE_ID: "Follow Global Default",
            "db_live": "Live ERP - hwgroup_erp [Database Live]",
        }
        page._db_profile_id_by_label = {
            "Follow Global Default": FOLLOW_GLOBAL_PROFILE_ID,
            "Live ERP - hwgroup_erp [Database Live]": "db_live",
        }
        page.database_choice_var = _FakeVar("Live ERP - hwgroup_erp [Database Live]")

        state = page._resolve_effective_database_state(selected_profile_id="db_live")

        self.assertEqual(state["effective_display"], "Effective DB: Live ERP - hwgroup_erp [Database Live]")
        self.assertFalse(state["is_dummy"])
        self.assertEqual(state["warning_text"], "")

    def test_dashboard_formats_snapshot_source_database_with_profile_alias(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page.context = mock.Mock(global_settings=GlobalSettings(default_database_profile_id="db_dummy"))

        self.assertEqual(
            page._format_snapshot_source_database("hwgroup_erp_22022026"),
            "Source DB: Dummy ERP / hwgroup_erp_22022026",
        )
        self.assertEqual(
            page._format_snapshot_source_database("hwgroup_erp"),
            "Source DB: Live ERP / hwgroup_erp",
        )

    def test_dashboard_refresh_effective_database_display_updates_summary_and_notice(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page.context = mock.Mock(global_settings=GlobalSettings(default_database_profile_id="db_dummy"))
        page._db_label_by_profile_id = {FOLLOW_GLOBAL_PROFILE_ID: "Follow Global Default"}
        page._db_profile_id_by_label = {"Follow Global Default": FOLLOW_GLOBAL_PROFILE_ID}
        page.database_choice_var = _FakeVar("Follow Global Default")
        page.effective_db_var = _FakeVar("")
        page.database_notice_var = _FakeVar("")
        page.warning_var = _FakeVar("")
        page.database_notice_label = _FakeWidget()
        page.warning_label = _FakeWidget()

        page._refresh_effective_database_display()

        self.assertIn("Dummy ERP - hwgroup_erp_22022026", page.effective_db_var.get())
        self.assertIn("hwgroup_erp_22022026", page.database_notice_var.get())
        self.assertEqual(len(page.database_notice_label.pack_calls), 1)

    def test_dashboard_apply_snapshot_status_mentions_source_database(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page.context = mock.Mock(global_settings=GlobalSettings(default_database_profile_id="db_dummy"))
        page._selected_product_id = 0
        page._latest_snapshot = None
        page.warning_var = _FakeVar("")
        page.database_notice_var = _FakeVar("")
        page.status_var = _FakeVar("")
        page.database_notice_label = _FakeWidget()
        page.warning_label = _FakeWidget()
        page._apply_company_summary = lambda snapshot: None
        page.render_item_cards = lambda: None
        page._render_selected_item = lambda: None
        page._refresh_busy_state = lambda: None
        page._notify_repair_collection_changed = lambda: None
        page._reconcile_repair_collection_with_snapshot = lambda snapshot: None
        page.sidebar_scroll = mock.Mock(canvas=mock.Mock())
        snapshot = mock.Mock()
        snapshot.items = []
        snapshot.warnings = []
        snapshot.database = "hwgroup_erp_22022026"

        page._apply_snapshot(snapshot)

        self.assertIn("Source DB: Dummy ERP / hwgroup_erp_22022026", page.status_var.get())
        page.sidebar_scroll.canvas.yview_moveto.assert_called_once_with(0)

    def test_dashboard_snapshot_event_sets_render_phase_before_apply(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page.root = _FakeRoot()
        page.log_queue = _FakeQueue()
        page._latest_base_url = ""
        page._repair_refresh_pending = False
        page._latest_analysis_request = None
        page._latest_analysis_profile_id = ""
        page.progress_value = _FakeVar(0.0)
        page.percent_var = _FakeVar("0%")
        page.phase_var = _FakeVar("")
        page.status_var = _FakeVar("")
        captured: list[tuple[str, str, str]] = []
        page._apply_snapshot = lambda snapshot: captured.append(  # noqa: ARG005
            (
                page.phase_var.get(),
                page.percent_var.get(),
                page._latest_analysis_request.database if page._latest_analysis_request else "",
            )
        )

        page._handle_ui_event(
            {
                "type": "snapshot",
                "snapshot": mock.Mock(),
                "base_url": "https://odoo.test",
                "request": SvlDashboardRequest(
                    database="hwgroup_erp",
                    company_id=1,
                    date_from="2026-01-01",
                    date_to="2026-01-31",
                ),
                "database_profile_id": "db_live",
            }
        )

        self.assertEqual(captured, [])
        self.assertEqual(len(page.root.after_calls), 1)
        _delay_ms, callback = page.root.after_calls[0]
        callback()
        self.assertEqual(captured[0][0], "Menyiapkan tampilan...")
        self.assertEqual(captured[0][1], "96%")
        self.assertEqual(captured[0][2], "hwgroup_erp")
        self.assertEqual(page._latest_base_url, "https://odoo.test")
        self.assertEqual(page._latest_analysis_profile_id, "db_live")

    def test_dashboard_detail_loaded_for_stale_item_only_updates_cache(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        key_a = ("hwgroup_erp", 1, "2026-01-01", "2026-01-31", 101)
        key_b = ("hwgroup_erp", 1, "2026-01-01", "2026-01-31", 103)
        detail = SvlDashboardItemDetail(pid=101, item_kind="product")
        page._detail_session_token = 5
        page._detail_pending_keys = {key_a}
        page._detail_error_by_key = {}
        page._detail_cache = {}
        page._current_detail_payload = None
        page._dirty_detail_tabs = set()
        page._active_detail_tab = "svl"
        page._active_detail_cache_key = lambda: key_b
        scheduled: list[int] = []
        page._schedule_active_detail_render = lambda: scheduled.append(1)

        page._handle_ui_event(
            {
                "type": "detail_loaded",
                "cache_key": key_a,
                "detail": detail,
                "session_token": 5,
            }
        )

        self.assertIs(page._detail_cache[key_a], detail)
        self.assertEqual(page._current_detail_payload, None)
        self.assertEqual(scheduled, [])

    def test_dashboard_detail_payload_falls_back_to_snapshot_data(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page._latest_analysis_request = SvlDashboardRequest(
            database="hwgroup_erp",
            company_id=1,
            date_from="2026-01-01",
            date_to="2026-01-31",
        )
        page._detail_cache = {}
        item = mock.Mock(
            pid=101,
            item_kind="product",
            svl_records=["svl-row"],
            jnl_records=["jnl-row"],
            po_line_count=0,
            bill_line_count=0,
            total_po_value=0.0,
            total_bill_value=0.0,
            po_lines=[],
            bill_lines=[],
        )

        detail = SvlFixJeDashboardPage._detail_payload_for_item(page, item)

        self.assertEqual(detail.svl_records, ["svl-row"])
        self.assertEqual(detail.jnl_records, ["jnl-row"])

    def test_dashboard_compare_tab_waits_for_warm_cache_before_lazy_fetch(self) -> None:
        class _ColumnsTree:
            def __init__(self, columns: tuple[str, ...]) -> None:
                self._columns = columns

            def __getitem__(self, key: str) -> tuple[str, ...]:
                if key != "columns":
                    raise KeyError(key)
                return self._columns

        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        request = SvlDashboardRequest(
            database="hwgroup_erp",
            company_id=1,
            date_from="2026-01-01",
            date_to="2026-01-31",
        )
        key = ("hwgroup_erp|issues|inv_1_noninv_1", 1, "2026-01-01", "2026-01-31", 101)
        item = mock.Mock(
            pid=101,
            item_kind="product",
            svl_records=[],
            jnl_records=[],
            po_lines=[],
            bill_lines=[],
            po_line_count=0,
            bill_line_count=0,
            total_po_value=0.0,
            total_bill_value=0.0,
        )
        page._dirty_detail_tabs = {"compare"}
        page._current_detail_item = item
        page._detail_render_token = 3
        page._latest_analysis_request = request
        page._detail_cache = {}
        page._detail_pending_keys = set()
        page._detail_error_by_key = {}
        page._detail_warm_state = "scheduled"
        page._detail_warm_target_keys = {key}
        page.compare_po_tree = _ColumnsTree(("po",))
        page.compare_bill_tree = _ColumnsTree(("bill",))
        fetch_calls: list[int] = []
        rendered_po_rows: list[list[dict[str, object]]] = []
        rendered_bill_rows: list[list[dict[str, object]]] = []
        page._ensure_item_detail_loaded = lambda *args, **kwargs: fetch_calls.append(1)
        page._finish_detail_render = lambda *args, **kwargs: None

        def render_tree(tree, rows, *, token, on_complete=None):  # noqa: ANN001
            if tree is page.compare_po_tree:
                rendered_po_rows.append(list(rows))
            else:
                rendered_bill_rows.append(list(rows))
            if on_complete is not None:
                on_complete()

        page._render_plain_tree_in_batches = render_tree

        SvlFixJeDashboardPage._render_tab_if_needed(page, "compare", token=3, allow_autoload=True)

        self.assertEqual(fetch_calls, [])
        self.assertEqual(rendered_po_rows[0][0]["values"][0], "Menyiapkan cache detail lokal...")
        self.assertEqual(rendered_bill_rows[0][0]["values"][0], "Menyiapkan cache detail lokal...")

    def test_dashboard_detail_batch_loaded_updates_active_compare_cache(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        key = ("hwgroup_erp|issues|inv_1_noninv_1", 1, "2026-01-01", "2026-01-31", 101)
        detail = SvlDashboardItemDetail(pid=101, item_kind="product")
        current_item = mock.Mock(pid=101)
        page._detail_session_token = 9
        page._detail_pending_keys = {key}
        page._detail_error_by_key = {}
        page._detail_cache = {}
        page._detail_warm_state = "running"
        page._detail_warm_target_keys = {key}
        page._post_paint_prefetch_cache_key = key
        page._current_detail_item = current_item
        page._current_detail_payload = None
        page._dirty_detail_tabs = set()
        page._active_detail_tab = "compare"
        page.phase_var = _FakeVar("")
        page.status_var = _FakeVar("")
        page._detail_payload_for_item = lambda item: detail
        page._full_detail_payload_for_item = lambda item: detail
        page._snapshot_ready_status_text = lambda snapshot=None: "Analisis selesai."
        summary_payloads: list[SvlDashboardItemDetail | None] = []
        page._render_selected_item_summary = lambda item, payload: summary_payloads.append(payload)
        scheduled: list[bool] = []
        page._schedule_active_detail_render = lambda *, allow_autoload=True: scheduled.append(allow_autoload)
        logs: list[str] = []
        page._append_log = lambda text: logs.append(text)

        page._handle_ui_event(
            {
                "type": "detail_batch_loaded",
                "entries": [{"cache_key": key, "detail": detail}],
                "session_token": 9,
                "total": 1,
            }
        )

        self.assertIs(page._detail_cache[key], detail)
        self.assertEqual(page._detail_warm_state, "completed")
        self.assertEqual(page._current_detail_payload, detail)
        self.assertEqual(summary_payloads, [detail])
        self.assertEqual(scheduled, [False])
        self.assertIn("1/1", page.phase_var.get())

    def test_dashboard_sidebar_tree_selected_unassigned_item_is_clickable(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page._sidebar_tree_syncing_selection = False
        page._selected_product_id = 101
        item_id = "item-unassigned"
        tree = mock.Mock()
        tree.selection.return_value = (item_id,)
        tree.focus.return_value = item_id
        page.sidebar_tree = tree
        page._sidebar_tree_pid_by_item_id = {item_id: -1}
        page._sidebar_tree_item_id_by_pid = {-1: [item_id]}
        selected: list[tuple[int, str]] = []
        page._select_item = lambda pid, **kwargs: selected.append((pid, kwargs.get("source", "")))

        SvlFixJeDashboardPage._on_sidebar_tree_selected(page)

        self.assertEqual(selected, [(-1, "sidebar_tree")])

    def test_dashboard_configured_inventory_coa_codes_use_global_entries(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page.context = mock.Mock(
            global_settings=GlobalSettings(
                inventory_coa_entries=[
                    InventoryCoaEntry(entry_id="coa_1", coa_code="1105001", label="Persediaan Alkohol"),
                    InventoryCoaEntry(entry_id="coa_2", coa_code="1105002", label="Persediaan Minuman"),
                ]
            )
        )

        self.assertEqual(page._configured_inventory_coa_codes(), ["1105001", "1105002"])

    def test_dashboard_groups_sidebar_items_by_valuation_then_category(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page._is_current_asset_dataset_mode = lambda value=None: False  # noqa: ARG005
        items = [
            mock.Mock(pid=1, categ="Raw Material", item_kind="product", automated_valuation=True, difference=10.0),
            mock.Mock(pid=2, categ="Service", item_kind="product", automated_valuation=False, difference=-5.0),
            mock.Mock(pid=-1, categ="VALUATION AML", item_kind="unassigned_journal", automated_valuation=False, difference=-30.0),
        ]

        groups = page._group_sidebar_items(items)

        self.assertEqual(groups[0]["title"], "Automated / Track Inventory")
        self.assertEqual(groups[0]["children"][0]["title"], "Raw Material")
        self.assertEqual(groups[0]["children"][0]["count"], 1)
        self.assertEqual(groups[1]["title"], "Non Product")
        self.assertEqual(groups[1]["children"][0]["title"], "System / Journal Tanpa Product")
        self.assertEqual(groups[2]["title"], "Manual / Non Inventory")
        self.assertEqual(groups[2]["children"][0]["title"], "Service")

    def test_dashboard_current_search_query_ignores_placeholder(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page.search_var = _FakeVar("Cari disini...")
        page._search_placeholder_active = True

        self.assertEqual(page._current_search_query(), "")

        page.search_var = _FakeVar("produk a")
        page._search_placeholder_active = False
        self.assertEqual(page._current_search_query(), "produk a")

    def test_dashboard_apply_search_placeholder_sets_muted_text(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page.search_var = _FakeVar("")
        page.search_entry = _FakeWidget()

        page._apply_search_placeholder(render=False)

        self.assertTrue(page._search_placeholder_active)
        self.assertEqual(page.search_var.get(), "Cari disini...")
        self.assertEqual(page.search_entry.last_config["fg"], T.TEXT_MUTED)

    def test_dashboard_format_sidebar_summary_uses_signed_total(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page._item_primary_amount = lambda item: float(getattr(item, "difference", 0.0) or 0.0)
        items = [
            mock.Mock(difference=10.0),
            mock.Mock(difference=-4.25),
            mock.Mock(difference=1.0),
        ]

        self.assertEqual(SvlFixJeDashboardPage._format_sidebar_summary(page, items), "3 item | Total 6.75")

    def test_dashboard_format_sidebar_item_text_uses_multiline_wrap(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page._sidebar_item_wrap_chars = lambda: 42
        page._item_record_count = lambda item, count_attr, list_attr: int(getattr(item, count_attr, 0) or 0)  # noqa: ARG005

        item = mock.Mock()
        item.code = "CP-INTERIOR19"
        item.name = "CONSTRUCTION PROJECT / INTERIOR FIT OUT LONG NAME"
        item.difference = 18115360.0
        item.svl_orphan_count = 2
        item.jnl_orphan_count = 1
        item.po_line_count = 3

        text = SvlFixJeDashboardPage._format_sidebar_item_text(page, item)

        self.assertGreaterEqual(text.count("\n"), 1)
        self.assertIn("CP-INTERIOR19", text)
        self.assertIn("CONSTRUCTION", text)
        self.assertIn("Diff 18,115,360.00", text)
        self.assertIn("2 SVL", text)
        self.assertIn("1 JNL", text)
        self.assertIn("3 PO", text)

    def test_dashboard_apply_company_summary_sets_unmapped_metric(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page.company_total_svl_value_var = _FakeVar("")
        page.company_total_svl_qty_var = _FakeVar("")
        page.company_total_bs_var = _FakeVar("")
        page.company_total_diff_var = _FakeVar("")
        page.company_total_unmapped_var = _FakeVar("")
        page.company_coa_total_var = _FakeVar("")
        page.company_coa_notice_var = _FakeVar("")
        page.company_coa_tree = mock.Mock()
        page._fill_tree = mock.Mock()
        page._configured_inventory_coa_codes = lambda: ["1105001"]
        page._is_current_asset_dataset_mode = lambda: False

        snapshot = SvlDashboardSnapshot(
            database="hwgroup_erp",
            company_id=1,
            company_name="Alpha Company",
            generated_at="2026-03-17T10:00:00",
            period="Awal - Sekarang",
            company_summary=SvlDashboardCompanySummary(
                total_svl_value=200.0,
                total_svl_qty=5.0,
                inventory_bs_total=180.0,
                difference=20.0,
                problematic_items_total_value=5.0,
                unmapped_difference=15.0,
                coa_rows=[SvlDashboardInventoryCoaRow(code="1105001", name="Persediaan Alkohol", balance=180.0)],
            ),
        )

        page._apply_company_summary(snapshot)

        self.assertEqual(page.company_total_svl_value_var.get(), "200.00")
        self.assertEqual(page.company_total_svl_qty_var.get(), "5.00")
        self.assertEqual(page.company_total_unmapped_var.get(), "15.00")

    def test_poll_queues_forwards_logs_and_events(self) -> None:
        panel = _SvlFixJePanel.__new__(_SvlFixJePanel)
        panel.log_queue = _FakeQueue(["log-1", "log-2"])
        panel.ui_queue = _FakeQueue([{"type": "busy", "value": True}])
        panel.root = _FakeRoot()
        seen_logs: list[str] = []
        seen_events: list[dict] = []
        panel._append_log = seen_logs.append
        panel._handle_ui_event = seen_events.append

        panel._poll_queues()

        self.assertEqual(seen_logs, ["log-1", "log-2"])
        self.assertEqual(seen_events, [{"type": "busy", "value": True}])
        self.assertEqual(panel._poll_after_id, "after#1")


class SvlDashboardPageWidgetTest(unittest.TestCase):
    @staticmethod
    def _repair_tree_leaf_ids(widgets: dict[str, object]) -> tuple[str, ...]:
        return tuple(widgets["left_leaf_item_ids"]())

    @staticmethod
    def _repair_tree_group_ids(widgets: dict[str, object]) -> tuple[str, ...]:
        return tuple(widgets["left_group_item_ids"]())

    def _select_repair_tree_indices(self, widgets: dict[str, object], *indices: int) -> None:
        tree = widgets["left_list"]
        item_ids = [
            widgets["left_item_id_for_index"](index)
            for index in indices
            if widgets["left_item_id_for_index"](index)
        ]
        tree.selection_set(tuple(item_ids))
        if item_ids:
            tree.focus(item_ids[0])
        tree.event_generate("<<TreeviewSelect>>")
        self.root.update_idletasks()

    def _drain_tk_events(self, *, limit: int = 50) -> None:
        for _ in range(limit):
            self.root.update()

    @staticmethod
    def _build_repair_dialog_rows(
        count: int,
        *,
        account_candidates: list[SvlDashboardRepairAccountCandidate] | None = None,
    ) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for index in range(count):
            rows.append(
                {
                    "row_key": f"row-{index + 1}",
                    "company_id": 7,
                    "item_product_id": 1000 + index,
                    "item_code": f"ITEM-{index + 1:04d}",
                    "item_name": f"Item {index + 1}",
                    "amount": 100.0 + index,
                    "date": "2026-03-18",
                    "journal_code": "STJ",
                    "signed_amount": 100.0 + index,
                    "base_reference": f"ITEM-{index + 1:04d}",
                    "base_line_label": f"ITEM-{index + 1:04d} Item {index + 1}",
                    "move_id": 0,
                    "move_name": "",
                    "move_state": "",
                    "account_candidates": list(account_candidates or []),
                }
            )
        return rows

    def setUp(self) -> None:
        try:
            self.root = tk.Tk()
            self.root.withdraw()
        except tk.TclError as exc:
            self.skipTest(f"Tk unavailable: {exc}")

    def tearDown(self) -> None:
        if hasattr(self, "root"):
            self.root.destroy()

    def test_repair_summary_dialog_formats_transaction_amount_item_and_fallbacks(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page.root = self.root
        page._last_repair_summary_widgets = {}

        results = [
            SvlDashboardRepairRowResult(
                row_key="row-1",
                status="POSTED",
                company_id=7,
                company_name="Alpha Company",
                item_code="A-SPWH-0043",
                item_name="JOHN JAMESON",
                amount=7513054.56,
                message="Move baru dibuat dan SVL direlink.",
                selected_target_mode="new_and_relink",
                effective_date="2026-03-19",
                move_id=901,
                move_name="STJ/2026/0901",
                old_move_action="mark_only",
                old_move_id=435,
                old_move_name="STJ/2025/10/0435",
                svl_reference="WCGT/IN/00034",
                debit_account_code="114001",
                debit_account_name="Persediaan Barang",
                credit_account_code="1108099",
                credit_account_name="Akun koreksi default",
            ),
            SvlDashboardRepairRowResult(
                row_key="row-2",
                status="ERROR",
                company_id=7,
                company_name="Alpha Company",
                item_code="B-DGSD-0003",
                item_name="AIR MINERAL (@330ML)",
                amount=1442.01,
                message="Akun debit tidak ditemukan.",
                selected_target_mode="fill_existing",
                effective_date="2026-03-19",
                error_kind="account_not_found",
                move_id=902,
                move_name="",
                svl_reference="WCGT/IN/00035",
            ),
        ]

        page._show_repair_result_summary(results, database="hwgroup_erp")
        self.root.update_idletasks()

        widgets = page._last_repair_summary_widgets
        self.assertTrue(widgets["dialog"].winfo_exists())
        self.assertEqual(widgets["summary_label"].cget("text"), "Repair selesai. 1 berhasil, 1 gagal.")
        self.assertIn("1. POSTED | Transaksi: STJ/2026/0901", widgets["body_message"])
        self.assertIn("Company : Alpha Company - [7]", widgets["body_message"])
        self.assertIn("Nominal : 7,513,054.56", widgets["body_message"])
        self.assertIn("Item    : A-SPWH-0043 | JOHN JAMESON", widgets["body_message"])
        self.assertIn("SVL     : WCGT/IN/00034", widgets["body_message"])
        self.assertIn("Lama    : STJ/2025/10/0435 (mark only)", widgets["body_message"])
        self.assertIn("Dibuat Journal Entry Valuation baru", widgets["body_message"])
        self.assertIn("2. ERROR | Transaksi: 902", widgets["body_message"])
        self.assertIn("Error   : Akun tidak ditemukan", widgets["body_message"])
        self.assertEqual(widgets["database"], "hwgroup_erp")
        self.assertEqual(widgets["export_html_button"].cget("text"), "Export HTML")
        self.assertEqual(widgets["export_excel_button"].cget("text"), "Export Excel")

        widgets["ok_button"].invoke()
        self.root.update_idletasks()
        self.assertEqual(page._last_repair_summary_widgets, {})

    def test_repair_summary_dialog_uses_scrolled_text_with_fixed_footer_for_long_batches(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page.root = self.root
        page._last_repair_summary_widgets = {}

        results = [
            SvlDashboardRepairRowResult(
                row_key=f"row-{index}",
                status="POSTED",
                item_code=f"ITEM-{index:03d}",
                item_name=f"Item {index}",
                amount=1000.0 + index,
                message="Move baru dibuat dan SVL direlink.",
                selected_target_mode="new_and_relink",
                effective_date="2026-03-19",
                move_id=190000 + index,
                move_name=f"STJ/2026/{190000 + index}",
            )
            for index in range(1, 41)
        ]

        page._show_repair_result_summary(results)
        self.root.update_idletasks()

        widgets = page._last_repair_summary_widgets
        body = widgets["body_text"]
        self.assertIsInstance(body, ScrolledText)
        self.assertEqual(str(body.cget("state")), "disabled")
        self.assertIs(widgets["ok_button"].master, widgets["footer"])
        self.assertIsNot(body.master, widgets["footer"])
        self.assertLess(float(body.yview()[1]), 1.0)
        self.assertEqual(widgets["export_excel_button"].master, widgets["footer"])
        self.assertEqual(widgets["export_html_button"].master, widgets["footer"])

        widgets["ok_button"].invoke()
        self.root.update_idletasks()

    def test_repair_summary_export_excel_asks_where_to_save_and_remembers_last_dir(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page.root = self.root
        page._latest_snapshot = None
        page._last_repair_summary_widgets = {}
        page.status_var = tk.StringVar(master=self.root, value="")
        page.context = mock.Mock(global_settings=GlobalSettings(default_output_dir=r"C:\shared\output"))
        saved_settings = SvlFixJeSettings(last_output_dir=r"C:\exports")
        page._module_settings = saved_settings
        page._state_store = mock.Mock()
        page._state_store.load.side_effect = lambda: saved_settings
        page._state_store.save.side_effect = lambda latest: None

        results = [
            SvlDashboardRepairRowResult(
                row_key="row-1",
                status="POSTED",
                company_id=7,
                company_name="Alpha Company",
                item_code="A-SPWH-0043",
                item_name="JOHN JAMESON",
                amount=7513054.56,
                selected_target_mode="new_and_relink",
                effective_date="2026-03-19",
                move_id=901,
                move_name="STJ/2026/0901",
                svl_reference="WCGT/IN/00034",
                debit_account_code="114001",
                debit_account_name="Persediaan Barang",
                credit_account_code="1108099",
                credit_account_name="Akun koreksi default",
            )
        ]
        page._show_repair_result_summary(results, database="hwgroup_erp")
        self.root.update_idletasks()
        widgets = page._last_repair_summary_widgets

        with mock.patch(
            "smartscc_tools.modules.svl_fix_je_dashboard_page.filedialog.asksaveasfilename",
            return_value=r"C:\temp\repair_summary.xlsx",
        ) as save_dialog, mock.patch(
            "smartscc_tools.modules.svl_fix_je_dashboard_page.export_repair_summary_excel",
            return_value=Path(r"C:\temp\repair_summary.xlsx"),
        ) as export_excel:
            widgets["export_excel_button"].invoke()

        save_dialog.assert_called_once()
        export_excel.assert_called_once()
        self.assertEqual(export_excel.call_args.kwargs["database"], "hwgroup_erp")
        self.assertEqual(saved_settings.last_output_dir, r"C:\temp")
        self.assertIn(r"C:\temp\repair_summary.xlsx", page.status_var.get())

    def test_pcb_case1_dialog_export_excel_asks_where_to_save_and_remembers_last_dir(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page.root = self.root
        page._display_name = "Fixing Unlink SVL - Odoo"
        page._last_pcb_case1_dialog_widgets = {}
        page.status_var = tk.StringVar(master=self.root, value="")
        page.context = mock.Mock(global_settings=GlobalSettings(default_output_dir=r"C:\shared\output"))
        saved_settings = SvlFixJeSettings(last_output_dir=r"C:\exports")
        page._module_settings = saved_settings
        page._state_store = mock.Mock()
        page._state_store.load.side_effect = lambda: saved_settings
        page._state_store.save.side_effect = lambda latest: None
        page._pcb_repair_collection_scope = None
        page._current_repair_collection_scope = lambda: None
        page._update_pcb_collection_button = lambda: None
        page._latest_snapshot = SvlDashboardSnapshot(
            database="hwgroup_erp",
            company_id=7,
            company_name="Alpha Company",
            generated_at="2026-03-20T10:00:00",
            period="2026-03-01..2026-03-31",
        )
        page._pcb_repair_collection = OrderedDict(
            {
                "case1::7001::901::bill_line::8101": {
                    "row_key": "case1::7001::901::bill_line::8101",
                    "cycle_key": "case1::7001",
                    "company_id": 7,
                    "company_name": "Alpha Company",
                    "picking_name": "LHPK/IN/7001",
                    "source_label": "Bill Line #8101",
                    "po_name": "PO/2026/0001",
                    "bill_name": "BILL/2026/0001",
                    "stj_refs": ["STJ/2026/0451"],
                    "date": "2026-03-19",
                    "reference": _pcb_case1_reference(
                        picking_name="LHPK/IN/7001",
                        bill_name="BILL/2026/0001",
                        item_code="SKU-001",
                        item_name="Produk A",
                    ),
                    "journal_code": "STJ",
                    "amount": 120.0,
                    "debit_account_code": "1108099",
                    "credit_account_code": "2103006",
                    "reconcile_ready": True,
                    "reconcile_readiness_label": "Exact Auto",
                    "reconcile_attempted": True,
                    "reconcile_performed": True,
                    "reconcile_skipped": False,
                    "reconcile_message": "2103006: exact reconcile berhasil. | 1108099: exact reconcile berhasil.",
                    "row_status": "repaired",
                    "row_status_message": "JE STJ/2026/0901 dibuat dan dipost.",
                    "result_status": "POSTED",
                    "result_posted": True,
                    "result_error_kind": "",
                    "result_move_id": 901,
                    "result_move_name": "STJ/2026/0901",
                    "product_id": 901,
                    "item_code": "SKU-001",
                    "item_name": "Produk A",
                    "item_category_name": "Raw",
                    "bill_line_id": 8101,
                    "bill_move_id": 8201,
                    "purchase_line_id": 8301,
                    "stock_move_id": 8401,
                    "stock_move_ids": [8401],
                    "stj_move_ids": [8801],
                    "stj_link_basis": "cycle_match.stock_move",
                    "stj_candidate_count": 1,
                    "picking_id": 7001,
                    "payment_move_ids": [8501],
                    "bank_move_ids": [8601],
                    "suspend_target_aml_ids": [9102],
                    "clearing_target_aml_ids": [9101],
                    "partner_id": 77,
                    "partner_name": "Vendor Alpha",
                    "currency_id": 13,
                    "product_uom_id": 11,
                    "quantity": 2.0,
                    "amount_currency": 120.0,
                    "analytic_distribution": {"CC-01": 100.0},
                    "bill_price_unit": 60.0,
                    "gr_price_unit": 0.0,
                    "price_gap_value": 120.0,
                    "allocated_amount": 120.0,
                }
            }
        )

        page._open_pcb_case1_repair_dialog_v2()
        self.root.update_idletasks()
        widgets = page._last_pcb_case1_dialog_widgets

        with mock.patch(
            "smartscc_tools.modules.svl_fix_je_dashboard_page.filedialog.asksaveasfilename",
            return_value=r"C:\temp\pcb_case1_summary.xlsx",
        ) as save_dialog, mock.patch(
            "smartscc_tools.modules.svl_fix_je_dashboard_page.export_pcb_case1_summary_excel",
            return_value=Path(r"C:\temp\pcb_case1_summary.xlsx"),
        ) as export_excel:
            widgets["export_excel_button"].invoke()

        save_dialog.assert_called_once()
        export_excel.assert_called_once()
        self.assertEqual(export_excel.call_args.kwargs["database"], "hwgroup_erp")
        self.assertEqual(export_excel.call_args.kwargs["rows"][0]["result_move_name"], "STJ/2026/0901")
        self.assertEqual(saved_settings.last_output_dir, r"C:\temp")
        self.assertIn(r"C:\temp\pcb_case1_summary.xlsx", page.status_var.get())
        widgets["close_button"].invoke()
        self.root.update_idletasks()

    def test_pcb_cycle_detail_export_excel_asks_where_to_save_and_remembers_last_dir(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page.root = self.root
        page._display_name = "Fixing Unlink SVL - Odoo"
        page.status_var = tk.StringVar(master=self.root, value="")
        page.context = mock.Mock(global_settings=GlobalSettings(default_output_dir=r"C:\shared\output"))
        saved_settings = SvlFixJeSettings(last_output_dir=r"C:\exports")
        page._module_settings = saved_settings
        page._state_store = mock.Mock()
        page._state_store.load.side_effect = lambda: saved_settings
        page._state_store.save.side_effect = lambda latest: None
        page._latest_snapshot = SvlDashboardSnapshot(
            database="hwgroup_erp",
            company_id=7,
            company_name="Alpha Company",
            generated_at="2026-03-20T10:00:00",
            period="2026-03-01..2026-03-31",
        )
        page._pcb_raw_sort_var = _FakeVar("Sort by Process")
        item_row = SvlDashboardCycleItemRow(
            product_id=901,
            product_name="Produk PCB",
            default_code="SKU-001",
            valuation_method="automated",
            bill_refs=["BILL/2026/0001"],
            has_item_bill=True,
            stj_refs=["STJ/2026/01/0690"],
            has_item_stj=True,
            external_clearing_amount=100.0,
            external_clearing_refs=["RAC/2026/01/0026 <- STJ/2026/01/0690"],
            external_clearing_basis="external.origin_purchase_line_id",
            external_clearing_verified=True,
            verified_audit_clearing_amount=100.0,
            eligible_case34=True,
            account_rows=[
                SvlDashboardCycleAccountRow(
                    account_id=302,
                    code="1108099",
                    name="Clearing",
                    account_type="asset_current",
                    account_group="asset",
                    debit=100.0,
                    credit=0.0,
                    net_balance=100.0,
                    status="problem",
                )
            ],
            adjustment_audit_rows=[_build_pcb_adjustment_audit_row()],
        )
        cycle = _build_pcb_cycle(
            picking_id=7001,
            item_rows=[item_row],
            raw_lines=[
                {
                    "tanggal": "2026-03-02",
                    "kode_transaksi": "STJ/2026/03/0262",
                    "jenis": "STJ",
                    "tipe_akun": "asset_current",
                    "akun_code": "1105003",
                    "akun_name": "Persediaan Makanan",
                    "kode_item": "SKU-001",
                    "nama_item": "Produk PCB",
                    "uom": "PCS",
                    "qty_item": 2.0,
                    "kategori_produk": "Raw",
                    "no_po": "PO/2026/0001",
                    "komunikasi": "Stock Journal",
                    "debit": 100.0,
                    "kredit": 0.0,
                    "saldo": 100.0,
                    "matching": "M-001",
                    "partner": "Vendor Alpha",
                },
                {
                    "tanggal": "2026-03-06",
                    "kode_transaksi": "BILL/2026/03/0082",
                    "jenis": "BILL",
                    "tipe_akun": "liability_current",
                    "akun_code": "2103006",
                    "akun_name": "Hutang Suspensed",
                    "kode_item": "SKU-001",
                    "nama_item": "Produk PCB",
                    "uom": "PCS",
                    "qty_item": 2.0,
                    "kategori_produk": "Raw",
                    "no_po": "PO/2026/0001",
                    "komunikasi": "Vendor Bill",
                    "debit": 0.0,
                    "kredit": 100.0,
                    "saldo": -100.0,
                    "matching": "",
                    "partner": "Vendor Alpha",
                },
            ],
            adjustment_warning_text="Warning: Clearing adjustment -> RAC/2026/01/0026",
            adjustment_audit_rows=[_build_pcb_adjustment_audit_row()],
        )
        page._pcb_raw_current_cycle = cycle

        with mock.patch(
            "smartscc_tools.modules.svl_fix_je_dashboard_page.filedialog.asksaveasfilename",
            return_value=r"C:\temp\pcb_cycle_detail.xlsx",
        ) as save_dialog, mock.patch(
            "smartscc_tools.modules.svl_fix_je_dashboard_page.export_pcb_cycle_detail_excel",
            return_value=Path(r"C:\temp\pcb_cycle_detail.xlsx"),
        ) as export_excel:
            page._export_pcb_cycle_detail_excel()

        save_dialog.assert_called_once()
        export_excel.assert_called_once()
        payload = export_excel.call_args.kwargs["payload"]
        self.assertEqual(payload["database"], "hwgroup_erp")
        self.assertEqual(payload["raw_rows"][0]["kode_transaksi"], "STJ/2026/03/0262")
        self.assertTrue(any(row["row_kind"] == "item_evidence" for row in payload["detail_rows"]))
        self.assertTrue(any(row["field_name"] == "external_clearing_refs" for row in payload["hidden_rows"]))
        self.assertEqual(saved_settings.last_output_dir, r"C:\temp")
        self.assertIn(r"C:\temp\pcb_cycle_detail.xlsx", page.status_var.get())

    def test_pcb_repair_dialog_reload_callback_ignores_destroyed_tree(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page.root = self.root
        page._display_name = "Fixing Unlink SVL - Odoo"
        page._last_pcb_case1_dialog_widgets = {}
        page.status_var = tk.StringVar(master=self.root, value="")
        page.context = mock.Mock(global_settings=GlobalSettings(default_output_dir=r"C:\shared\output"))
        page._module_settings = SvlFixJeSettings(last_output_dir=r"C:\exports", max_workers=2)
        page._state_store = mock.Mock()
        page._pcb_repair_collection_scope = None
        page._current_repair_collection_scope = lambda: None
        page._update_pcb_collection_button = lambda: None
        page._pcb_repair_collection = OrderedDict(
            {
                "case1::7001::901::bill_line::8101": {
                    "row_key": "case1::7001::901::bill_line::8101",
                    "cycle_key": "case1::7001",
                    "pcb_case": "case1",
                    "pcb_case_label": PCB_CASE1_LABEL,
                    "company_id": 7,
                    "company_name": "Alpha Company",
                    "picking_name": "LHPK/IN/7001",
                    "source_label": "Bill Line #8101",
                    "po_name": "PO/2026/0001",
                    "bill_name": "BILL/2026/0001",
                    "date": "2026-03-19",
                    "reference": _pcb_case1_reference(
                        picking_name="LHPK/IN/7001",
                        bill_name="BILL/2026/0001",
                        item_code="SKU-001",
                        item_name="Produk A",
                    ),
                    "line_label": _pcb_case1_line_label(item_code="SKU-001", item_name="Produk A"),
                    "journal_code": "STJ",
                    "amount": 120.0,
                    "debit_account_code": "1108099",
                    "credit_account_code": "2103006",
                    "reconcile_ready": True,
                    "reconcile_readiness_label": "Exact Auto",
                    "row_status": "ready",
                    "row_status_message": "Ready",
                    "result_status": "",
                    "result_posted": False,
                    "result_error_kind": "",
                    "result_move_id": 0,
                    "result_move_name": "",
                    "product_id": 901,
                    "item_code": "SKU-001",
                    "item_name": "Produk A",
                    "item_category_name": "Raw",
                    "bill_line_id": 8101,
                    "bill_move_id": 8201,
                    "purchase_line_id": 8301,
                    "stock_move_id": 8401,
                    "stock_move_ids": [8401],
                    "stj_move_ids": [8801],
                    "picking_id": 7001,
                    "partner_id": 77,
                    "partner_name": "Vendor Alpha",
                    "quantity": 2.0,
                    "amount_currency": 120.0,
                    "bill_price_unit": 60.0,
                    "gr_price_unit": 0.0,
                    "price_gap_value": 120.0,
                    "allocated_amount": 120.0,
                }
            }
        )

        page._open_pcb_case1_repair_dialog_v2()
        self.root.update_idletasks()
        widgets = page._last_pcb_case1_dialog_widgets
        reload_row_list = widgets["reload_row_list"]

        widgets["close_button"].invoke()
        self.root.update_idletasks()

        self.assertEqual(page._last_pcb_case1_dialog_widgets, {})
        self.assertFalse(bool(widgets["dialog"].winfo_exists()))
        reload_row_list()

    def test_pcb_case1_dialog_shows_compact_summary_and_collapsed_advanced_section(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page.root = self.root
        page._display_name = "Fixing Unlink SVL - Odoo"
        page._last_pcb_case1_dialog_widgets = {}
        page.status_var = tk.StringVar(master=self.root, value="")
        page.context = mock.Mock(global_settings=GlobalSettings(default_output_dir=r"C:\shared\output"))
        page._module_settings = SvlFixJeSettings(last_output_dir=r"C:\exports")
        page._state_store = mock.Mock()
        page._pcb_repair_collection_scope = None
        page._current_repair_collection_scope = lambda: None
        page._update_pcb_collection_button = lambda: None
        page._pcb_repair_collection = OrderedDict(
            {
                "case1::7001::901::bill_line::8101": {
                    "row_key": "case1::7001::901::bill_line::8101",
                    "cycle_key": "case1::7001",
                    "company_id": 7,
                    "company_name": "Alpha Company",
                    "picking_name": "LHPK/IN/7001",
                    "source_label": "Bill Line #8101",
                    "po_name": "PO/2026/0001",
                    "bill_name": "BILL/2026/0001",
                    "stj_refs": ["STJ/2026/0451"],
                    "stj_move_ids": [8801],
                    "stj_link_basis": "cycle_match.stock_move",
                    "stj_candidate_count": 1,
                    "date": "2026-03-19",
                    "reference": _pcb_case1_reference(
                        picking_name="LHPK/IN/7001",
                        bill_name="BILL/2026/0001",
                        item_code="SKU-001",
                        item_name="Produk A",
                    ),
                    "journal_code": "STJ",
                    "amount": 120.0,
                    "debit_account_code": "1108099",
                    "credit_account_code": "2103006",
                    "reconcile_ready": True,
                    "reconcile_readiness_label": "Exact Auto",
                    "reconcile_target_count": 2,
                    "row_status": "repaired",
                    "row_status_message": "JE STJ/2026/0901 dibuat dan dipost.",
                    "result_move_name": "STJ/2026/0901",
                    "product_id": 901,
                    "item_code": "SKU-001",
                    "item_name": "Produk A",
                    "bill_line_id": 8101,
                    "bill_move_id": 8201,
                    "purchase_line_id": 8301,
                    "stock_move_id": 8401,
                    "stock_move_ids": [8401],
                    "picking_id": 7001,
                    "payment_move_ids": [8501],
                    "bank_move_ids": [8601],
                    "suspend_target_aml_ids": [9102],
                    "clearing_target_aml_ids": [9101],
                    "partner_id": 77,
                    "currency_id": 13,
                    "analytic_distribution": {"CC-01": 100.0},
                    "reconcile_message": "",
                }
            }
        )

        page._open_pcb_case1_repair_dialog_v2()
        self.root.update_idletasks()
        widgets = page._last_pcb_case1_dialog_widgets
        tree = widgets["tree"]
        first_iid = next(iter(widgets["row_by_iid"]))
        tree.selection_set((first_iid,))
        tree.focus(first_iid)
        tree.event_generate("<<TreeviewSelect>>")
        self.root.update_idletasks()

        self.assertIsInstance(widgets["right_scroll"], ScrollableFrame)
        self.assertEqual(widgets["right_scroll"].master, widgets["right_host"])
        self.assertEqual(widgets["right_body"].master, widgets["right_scroll"].interior)
        self.assertFalse(widgets["advanced_section"].is_open)
        self.assertEqual(widgets["summary_vars"]["stj_refs"].get(), "STJ/2026/0451")
        self.assertEqual(widgets["summary_vars"]["result_move_name"].get(), "STJ/2026/0901")
        self.assertEqual(widgets["summary_vars"]["reconcile_status"].get(), "Exact Auto | 2 target AML")

        widgets["close_button"].invoke()
        self.root.update_idletasks()

    def test_pcb_case1_dialog_summary_shows_unresolved_stj_message_when_stj_refs_missing(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page.root = self.root
        page._display_name = "Fixing Unlink SVL - Odoo"
        page._last_pcb_case1_dialog_widgets = {}
        page.status_var = tk.StringVar(master=self.root, value="")
        page.context = mock.Mock(global_settings=GlobalSettings(default_output_dir=r"C:\shared\output"))
        page._module_settings = SvlFixJeSettings(last_output_dir=r"C:\exports")
        page._state_store = mock.Mock()
        page._pcb_repair_collection_scope = None
        page._current_repair_collection_scope = lambda: None
        page._update_pcb_collection_button = lambda: None
        page._pcb_repair_collection = OrderedDict(
            {
                "case1::7001::901::bill_line::8101": {
                    "row_key": "case1::7001::901::bill_line::8101",
                    "cycle_key": "case1::7001",
                    "company_id": 7,
                    "company_name": "Alpha Company",
                    "picking_name": "LHPK/IN/7001",
                    "source_label": "Bill Line #8101",
                    "date": "2026-03-19",
                    "reference": _pcb_case1_reference(
                        picking_name="LHPK/IN/7001",
                        bill_name="BILL/2026/0001",
                        item_code="SKU-001",
                        item_name="Produk A",
                    ),
                    "amount": 120.0,
                    "debit_account_code": "1108099",
                    "credit_account_code": "2103006",
                    "reconcile_readiness_label": "No Target",
                    "row_status": "ready",
                    "row_status_message": "Ready",
                    "product_id": 901,
                    "bill_line_id": 8101,
                    "purchase_line_id": 8301,
                    "stock_move_id": 8401,
                    "stock_move_ids": [8401],
                    "stj_refs": [],
                    "stj_move_ids": [],
                    "stj_link_basis": "",
                    "stj_candidate_count": 0,
                    "picking_id": 7001,
                    "partner_id": 77,
                    "currency_id": 13,
                    "analytic_distribution": False,
                    "reconcile_message": "",
                }
            }
        )

        page._open_pcb_case1_repair_dialog_v2()
        self.root.update_idletasks()
        widgets = page._last_pcb_case1_dialog_widgets
        tree = widgets["tree"]
        first_iid = next(iter(widgets["row_by_iid"]))
        tree.selection_set((first_iid,))
        tree.focus(first_iid)
        tree.event_generate("<<TreeviewSelect>>")
        self.root.update_idletasks()

        self.assertEqual(widgets["summary_vars"]["stj_refs"].get(), "Belum ter-resolve dari source STJ")
        self.assertEqual(widgets["advanced_vars"]["stj_move_ids"].get(), "-")

        widgets["close_button"].invoke()
        self.root.update_idletasks()

    def test_pcb_case1_dialog_runtime_lookup_shows_non_blank_target_explanation(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page.root = self.root
        page._display_name = "Fixing Unlink SVL - Odoo"
        page._last_pcb_case1_dialog_widgets = {}
        page.status_var = tk.StringVar(master=self.root, value="")
        page.context = mock.Mock(global_settings=GlobalSettings(default_output_dir=r"C:\shared\output"))
        page._module_settings = SvlFixJeSettings(last_output_dir=r"C:\exports")
        page._state_store = mock.Mock()
        page._pcb_repair_collection_scope = None
        page._current_repair_collection_scope = lambda: None
        page._update_pcb_collection_button = lambda: None
        page._pcb_repair_collection = OrderedDict(
            {
                "case1::7001::901::bill_line::8101": {
                    "row_key": "case1::7001::901::bill_line::8101",
                    "cycle_key": "case1::7001",
                    "company_id": 7,
                    "company_name": "Alpha Company",
                    "picking_name": "LHPK/IN/7001",
                    "source_label": "Bill Line #8101",
                    "date": "2026-03-19",
                    "reference": _pcb_case1_reference(
                        picking_name="LHPK/IN/7001",
                        bill_name="BILL/2026/0001",
                        item_code="SKU-001",
                        item_name="Produk A",
                    ),
                    "amount": 120.0,
                    "debit_account_code": "1108099",
                    "credit_account_code": "2103006",
                    "reconcile_ready": True,
                    "reconcile_readiness_label": "Runtime Lookup",
                    "reconcile_target_count": 0,
                    "row_status": "ready",
                    "row_status_message": "Ready",
                    "product_id": 901,
                    "bill_line_id": 8101,
                    "bill_move_id": 8201,
                    "purchase_line_id": 8301,
                    "stock_move_id": 8401,
                    "stock_move_ids": [8401],
                    "stj_refs": ["STJ/2026/0451"],
                    "stj_move_ids": [8801],
                    "stj_link_basis": "cycle_match.stock_move",
                    "stj_candidate_count": 1,
                    "picking_id": 7001,
                    "partner_id": 77,
                    "currency_id": 13,
                    "analytic_distribution": False,
                    "reconcile_message": "",
                    "suspend_target_aml_ids": [],
                    "clearing_target_aml_ids": [],
                }
            }
        )

        page._open_pcb_case1_repair_dialog_v2()
        self.root.update_idletasks()
        widgets = page._last_pcb_case1_dialog_widgets
        tree = widgets["tree"]
        first_iid = next(iter(widgets["row_by_iid"]))
        tree.selection_set((first_iid,))
        tree.focus(first_iid)
        tree.event_generate("<<TreeviewSelect>>")
        self.root.update_idletasks()

        self.assertEqual(widgets["summary_vars"]["reconcile_status"].get(), "Runtime Lookup")
        self.assertEqual(widgets["advanced_vars"]["suspend_target_aml_ids"].get(), "Runtime lookup via bill move")
        self.assertEqual(widgets["advanced_vars"]["clearing_target_aml_ids"].get(), "Runtime lookup via STJ move(s)")

        widgets["close_button"].invoke()
        self.root.update_idletasks()

    def test_pcb_case1_dialog_execute_selected_scope_runs_only_selected_rows(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page.root = self.root
        page._display_name = "Fixing Unlink SVL - Odoo"
        page._last_pcb_case1_dialog_widgets = {}
        page.status_var = tk.StringVar(master=self.root, value="")
        page.context = mock.Mock(global_settings=GlobalSettings(default_output_dir=r"C:\shared\output"))
        page._module_settings = SvlFixJeSettings(last_output_dir=r"C:\exports", max_workers=4)
        page._state_store = mock.Mock()
        page._pcb_repair_collection_scope = None
        page._current_repair_collection_scope = lambda: None
        page._update_pcb_collection_button = lambda: None
        page._run_pcb_case1_repair_collection_v2 = mock.Mock()
        page._pcb_repair_collection = OrderedDict(
            {
                "row-1": {
                    "row_key": "row-1",
                    "cycle_key": "case1::7001",
                    "pcb_case": "case1",
                    "pcb_case_label": PCB_CASE1_LABEL,
                    "reference_generated": True,
                    "line_label_generated": True,
                    "company_id": 7,
                    "company_name": "Alpha Company",
                    "picking_name": "LHPK/IN/7001",
                    "source_label": "Bill Line #8101",
                    "po_name": "PO/2026/0001",
                    "bill_name": "BILL/2026/0001",
                    "date": "2026-03-19",
                    "reference": _pcb_case1_reference(
                        picking_name="LHPK/IN/7001",
                        bill_name="BILL/2026/0001",
                        item_code="SKU-001",
                        item_name="Produk A",
                    ),
                    "line_label": _pcb_case1_line_label(item_code="SKU-001", item_name="Produk A"),
                    "amount": 120.0,
                    "debit_account_code": "1108099",
                    "credit_account_code": "2103006",
                    "reconcile_readiness_label": "Exact Auto",
                    "row_status": "ready",
                    "row_status_message": "Ready",
                    "product_id": 901,
                    "item_code": "SKU-001",
                    "item_name": "Produk A",
                    "bill_line_id": 8101,
                    "bill_move_id": 8201,
                    "purchase_line_id": 8301,
                    "stock_move_id": 8401,
                    "stock_move_ids": [8401],
                    "stj_move_ids": [8801],
                    "picking_id": 7001,
                },
                "row-2": {
                    "row_key": "row-2",
                    "cycle_key": "case1::7002",
                    "pcb_case": "case1",
                    "pcb_case_label": PCB_CASE1_LABEL,
                    "reference_generated": True,
                    "line_label_generated": True,
                    "company_id": 7,
                    "company_name": "Alpha Company",
                    "picking_name": "LHPK/IN/7002",
                    "source_label": "Bill Line #8102",
                    "po_name": "PO/2026/0002",
                    "bill_name": "BILL/2026/0002",
                    "date": "2026-03-19",
                    "reference": _pcb_case1_reference(
                        picking_name="LHPK/IN/7002",
                        bill_name="BILL/2026/0002",
                        item_code="SKU-002",
                        item_name="Produk B",
                    ),
                    "line_label": _pcb_case1_line_label(item_code="SKU-002", item_name="Produk B"),
                    "amount": 90.0,
                    "debit_account_code": "1108099",
                    "credit_account_code": "2103006",
                    "reconcile_readiness_label": "Exact Auto",
                    "row_status": "ready",
                    "row_status_message": "Ready",
                    "product_id": 902,
                    "item_code": "SKU-002",
                    "item_name": "Produk B",
                    "bill_line_id": 8102,
                    "bill_move_id": 8202,
                    "purchase_line_id": 8302,
                    "stock_move_id": 8402,
                    "stock_move_ids": [8402],
                    "stj_move_ids": [8802],
                    "picking_id": 7002,
                },
            }
        )

        page._open_pcb_case1_repair_dialog_v2()
        self.root.update_idletasks()
        widgets = page._last_pcb_case1_dialog_widgets
        tree = widgets["tree"]
        first_iid = next(iter(widgets["row_by_iid"]))
        tree.selection_set((first_iid,))
        tree.focus(first_iid)
        tree.event_generate("<<TreeviewSelect>>")
        widgets["scope_var"].set("selected")
        self.root.update_idletasks()

        with mock.patch(
            "smartscc_tools.modules.svl_fix_je_dashboard_page.messagebox.askyesno",
            return_value=True,
        ) as ask_yes_no:
            widgets["execute_draft_button"].invoke()

        page._run_pcb_case1_repair_collection_v2.assert_called_once()
        execute_rows = page._run_pcb_case1_repair_collection_v2.call_args.args[0]
        self.assertEqual(len(execute_rows), 1)
        self.assertEqual(execute_rows[0]["row_key"], "row-1")
        self.assertFalse(page._run_pcb_case1_repair_collection_v2.call_args.kwargs["post"])
        self.assertIn("Execute Selected Row(s): 1 row.", ask_yes_no.call_args.args[1])
        self.assertIn("Selected Row(s) | 1 row", widgets["result_var"].get())

        widgets["close_button"].invoke()
        self.root.update_idletasks()

    def test_pcb_case1_dialog_execute_all_scope_runs_all_rows_without_selection(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page.root = self.root
        page._display_name = "Fixing Unlink SVL - Odoo"
        page._last_pcb_case1_dialog_widgets = {}
        page.status_var = tk.StringVar(master=self.root, value="")
        page.context = mock.Mock(global_settings=GlobalSettings(default_output_dir=r"C:\shared\output"))
        page._module_settings = SvlFixJeSettings(last_output_dir=r"C:\exports", max_workers=3)
        page._state_store = mock.Mock()
        page._pcb_repair_collection_scope = None
        page._current_repair_collection_scope = lambda: None
        page._update_pcb_collection_button = lambda: None
        page._run_pcb_case1_repair_collection_v2 = mock.Mock()
        page._pcb_repair_collection = OrderedDict(
            {
                "row-1": {
                    "row_key": "row-1",
                    "cycle_key": "case1::7001",
                    "pcb_case": "case1",
                    "pcb_case_label": PCB_CASE1_LABEL,
                    "reference_generated": True,
                    "line_label_generated": True,
                    "company_id": 7,
                    "company_name": "Alpha Company",
                    "picking_name": "LHPK/IN/7001",
                    "source_label": "Bill Line #8101",
                    "bill_name": "BILL/2026/0001",
                    "date": "2026-03-19",
                    "reference": _pcb_case1_reference(
                        picking_name="LHPK/IN/7001",
                        bill_name="BILL/2026/0001",
                        item_code="SKU-001",
                        item_name="Produk A",
                    ),
                    "line_label": _pcb_case1_line_label(item_code="SKU-001", item_name="Produk A"),
                    "amount": 120.0,
                    "debit_account_code": "1108099",
                    "credit_account_code": "2103006",
                    "row_status": "ready",
                    "row_status_message": "Ready",
                    "item_code": "SKU-001",
                    "item_name": "Produk A",
                },
                "row-2": {
                    "row_key": "row-2",
                    "cycle_key": "case1::7002",
                    "pcb_case": "case1",
                    "pcb_case_label": PCB_CASE1_LABEL,
                    "reference_generated": True,
                    "line_label_generated": True,
                    "company_id": 7,
                    "company_name": "Alpha Company",
                    "picking_name": "LHPK/IN/7002",
                    "source_label": "Bill Line #8102",
                    "bill_name": "BILL/2026/0002",
                    "date": "2026-03-19",
                    "reference": _pcb_case1_reference(
                        picking_name="LHPK/IN/7002",
                        bill_name="BILL/2026/0002",
                        item_code="SKU-002",
                        item_name="Produk B",
                    ),
                    "line_label": _pcb_case1_line_label(item_code="SKU-002", item_name="Produk B"),
                    "amount": 90.0,
                    "debit_account_code": "1108099",
                    "credit_account_code": "2103006",
                    "row_status": "ready",
                    "row_status_message": "Ready",
                    "item_code": "SKU-002",
                    "item_name": "Produk B",
                },
            }
        )

        page._open_pcb_case1_repair_dialog_v2()
        self.root.update_idletasks()
        widgets = page._last_pcb_case1_dialog_widgets
        widgets["scope_var"].set("all")
        self.root.update_idletasks()

        with mock.patch(
            "smartscc_tools.modules.svl_fix_je_dashboard_page.messagebox.askyesno",
            return_value=True,
        ) as ask_yes_no:
            widgets["execute_post_button"].invoke()

        page._run_pcb_case1_repair_collection_v2.assert_called_once()
        execute_rows = page._run_pcb_case1_repair_collection_v2.call_args.args[0]
        self.assertEqual([row["row_key"] for row in execute_rows], ["row-1", "row-2"])
        self.assertTrue(page._run_pcb_case1_repair_collection_v2.call_args.kwargs["post"])
        self.assertIn("Execute All Rows: 2 row.", ask_yes_no.call_args.args[1])
        self.assertIn("All Rows | 2 row", widgets["result_var"].get())

        widgets["close_button"].invoke()
        self.root.update_idletasks()

    def test_pcb_case1_dialog_execute_selected_scope_requires_selection(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page.root = self.root
        page._display_name = "Fixing Unlink SVL - Odoo"
        page._last_pcb_case1_dialog_widgets = {}
        page.status_var = tk.StringVar(master=self.root, value="")
        page.context = mock.Mock(global_settings=GlobalSettings(default_output_dir=r"C:\shared\output"))
        page._module_settings = SvlFixJeSettings(last_output_dir=r"C:\exports", max_workers=2)
        page._state_store = mock.Mock()
        page._pcb_repair_collection_scope = None
        page._current_repair_collection_scope = lambda: None
        page._update_pcb_collection_button = lambda: None
        page._run_pcb_case1_repair_collection_v2 = mock.Mock()
        page._pcb_repair_collection = OrderedDict(
            {
                "row-1": {
                    "row_key": "row-1",
                    "cycle_key": "case1::7001",
                    "pcb_case": "case1",
                    "pcb_case_label": PCB_CASE1_LABEL,
                    "reference_generated": True,
                    "line_label_generated": True,
                    "company_id": 7,
                    "company_name": "Alpha Company",
                    "picking_name": "LHPK/IN/7001",
                    "source_label": "Bill Line #8101",
                    "bill_name": "BILL/2026/0001",
                    "date": "2026-03-19",
                    "reference": _pcb_case1_reference(
                        picking_name="LHPK/IN/7001",
                        bill_name="BILL/2026/0001",
                        item_code="SKU-001",
                        item_name="Produk A",
                    ),
                    "line_label": _pcb_case1_line_label(item_code="SKU-001", item_name="Produk A"),
                    "amount": 120.0,
                    "debit_account_code": "1108099",
                    "credit_account_code": "2103006",
                    "row_status": "ready",
                    "row_status_message": "Ready",
                    "item_code": "SKU-001",
                    "item_name": "Produk A",
                }
            }
        )

        page._open_pcb_case1_repair_dialog_v2()
        self.root.update_idletasks()
        widgets = page._last_pcb_case1_dialog_widgets
        widgets["scope_var"].set("selected")

        with mock.patch(
            "smartscc_tools.modules.svl_fix_je_dashboard_page.messagebox.showwarning",
        ) as show_warning:
            widgets["execute_draft_button"].invoke()

        page._run_pcb_case1_repair_collection_v2.assert_not_called()
        show_warning.assert_called_once()
        self.assertIn("Selected Row(s)", show_warning.call_args.args[1])

        widgets["close_button"].invoke()
        self.root.update_idletasks()

    def test_pcb_case1_run_marks_posted_reconcile_warning_as_repaired(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page.root = self.root
        page._display_name = "Fixing Unlink SVL - Odoo"
        page.context = mock.Mock(global_settings=GlobalSettings(default_output_dir=r"C:\shared\output"))
        page.logger = logging.getLogger("test.pcb.case1")
        page._module_settings = SvlFixJeSettings(last_output_dir=r"C:\exports", max_workers=1)
        page._selected_database_profile_id = lambda: "db_live"
        page._runtime_builder = lambda *_args, **_kwargs: (  # noqa: ARG005
            object(),
            mock.Mock(base_url="https://odoo.test", database="hwgroup_erp"),
        )
        page._save_pcb_case1_last_date = lambda _value: None
        page._pcb_repair_collection = OrderedDict(
            {
                "case1::7001::901::bill_line::8101": {
                    "row_key": "case1::7001::901::bill_line::8101",
                    "company_id": 7,
                    "company_name": "Alpha Company",
                    "amount": 120.0,
                    "date": "2026-03-19",
                    "reference": _pcb_case1_reference(
                        picking_name="LHPK/IN/7001",
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
                    "bill_line_id": 8101,
                    "bill_move_id": 8201,
                    "purchase_line_id": 8301,
                    "stock_move_id": 8401,
                    "stock_move_ids": [8401],
                    "picking_id": 7001,
                    "picking_name": "LHPK/IN/7001",
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
                    "analytic_distribution": {"CC-01": 100.0},
                    "bill_price_unit": 40.0,
                    "gr_price_unit": 10.0,
                    "price_gap_value": 120.0,
                    "allocated_amount": 120.0,
                    "reconcile_readiness_label": "Exact Auto",
                    "reconcile_target_count": 2,
                    "row_status": "ready",
                    "row_status_message": "Ready",
                    "result_status": "",
                    "result_posted": False,
                    "result_error_kind": "",
                    "result_move_id": 0,
                    "result_move_name": "",
                    "existing_move_detected": False,
                    "reconcile_attempted": False,
                    "reconcile_performed": False,
                    "reconcile_skipped": False,
                    "reconcile_message": "",
                    "reconcile_error_kind": "",
                }
            }
        )
        page.ui_queue = _FakeQueue()
        page.log_queue = _FakeQueue()

        fake_service = mock.Mock()
        fake_service.execute_pcb_case1 = mock.AsyncMock(
            return_value=SvlDashboardPcbCase1RepairBatchResult(
                database="hwgroup_erp",
                results=[
                    SvlDashboardPcbCase1RepairRowResult(
                        row_key="case1::7001::901::bill_line::8101",
                        status="POSTED",
                        message=(
                            "JE STJ/2026/0901 dibuat dan dipost. Reconcile warning: "
                            "account.partial.reconcile.create Odoo error"
                        ),
                        move_id=901,
                        move_name="STJ/2026/0901",
                        posted=True,
                        reconcile_attempted=True,
                        reconcile_performed=False,
                        reconcile_skipped=True,
                        reconcile_message="account.partial.reconcile.create Odoo error",
                        reconcile_error_kind="reconcile_create_failed",
                    )
                ],
                created_count=1,
                posted_count=1,
                existing_count=0,
                reconciled_count=0,
                reconcile_skipped_count=1,
                error_count=0,
            )
        )

        class _FakeAsyncClient:
            def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
                pass

            async def __aenter__(self):
                return object()

            async def __aexit__(self, exc_type, exc, tb):  # noqa: ANN001
                return False

        class _ImmediateThread:
            def __init__(self, *, target, **kwargs) -> None:  # noqa: ANN003
                self._target = target

            def start(self) -> None:
                self._target()

        with mock.patch(
            "smartscc_tools.modules.svl_fix_je_dashboard_page.AsyncOdooJsonRpcClient",
            _FakeAsyncClient,
        ), mock.patch(
            "smartscc_tools.modules.svl_fix_je_dashboard_page.SvlDashboardRepairServiceAsync",
            return_value=fake_service,
        ), mock.patch(
            "smartscc_tools.modules.svl_fix_je_dashboard_page.threading.Thread",
            side_effect=lambda **kwargs: _ImmediateThread(**kwargs),
        ):
            page._run_pcb_case1_repair_collection_v2(list(page._pcb_repair_collection.values()), post=True)

        row = page._pcb_repair_collection["case1::7001::901::bill_line::8101"]
        self.assertEqual(row["row_status"], "repaired")
        self.assertEqual(row["result_status"], "POSTED")
        self.assertEqual(row["result_move_name"], "STJ/2026/0901")
        self.assertEqual(row["reconcile_readiness_label"], "Disabled")
        self.assertTrue(row["reconcile_skipped"])
        self.assertEqual(row["reconcile_error_kind"], "reconcile_create_failed")
        self.assertIn("Reconcile warning:", row["row_status_message"])
        done_event = page.ui_queue.get_nowait()
        self.assertEqual(done_event["type"], "_pcb_repair_done")
        self.assertIn("0 error. Auto reconcile PCB Case 1 disabled.", done_event["msg"])

    def test_pcb_repair_dialog_shows_case2_je_preview_and_guard_detail(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page.root = self.root
        page._display_name = "Fixing Unlink SVL - Odoo"
        page._last_pcb_case1_dialog_widgets = {}
        page.status_var = tk.StringVar(master=self.root, value="")
        page.context = mock.Mock(global_settings=GlobalSettings(default_output_dir=r"C:\shared\output"))
        page._module_settings = SvlFixJeSettings(last_output_dir=r"C:\exports", max_workers=2)
        page._state_store = mock.Mock()
        page._pcb_repair_collection_scope = None
        page._current_repair_collection_scope = lambda: None
        page._update_pcb_collection_button = lambda: None
        page._pcb_repair_collection = OrderedDict(
            {
                "case2::7001::901": {
                    "row_key": "case2::7001::901",
                    "cycle_key": "case2::7001",
                    "pcb_case": "case2",
                    "pcb_case_label": PCB_CASE2_LABEL,
                    "reference_generated": True,
                    "line_label_generated": True,
                    "company_id": 7,
                    "company_name": "Alpha Company",
                    "picking_name": "LHPK/IN/7001",
                    "source_label": "Item Balance",
                    "po_name": "PO/2026/0001",
                    "bill_name": "BILL/2026/0001",
                    "date": "2026-03-19",
                    "reference": _pcb_case1_reference(
                        picking_name="LHPK/IN/7001",
                        bill_name="BILL/2026/0001",
                        item_code="SKU-001",
                        item_name="Produk PCB",
                    ),
                    "line_label": "SKU-001 - Produk PCB - Case 2 - STJ Bill Price Diff (Suspend - Suspend)",
                    "amount": 100.0,
                    "product_id": 901,
                    "item_code": "SKU-001",
                    "item_name": "Produk PCB",
                    "item_category_name": "Raw Material",
                    "picking_id": 7001,
                    "bill_move_id": 8201,
                    "partner_id": 77,
                    "partner_name": "Vendor Alpha",
                    "journal_code": "STJ",
                    "problem_balances_by_code": {"2103006": -100.0},
                    "hpp_balances_by_code": {"5101004": 70.0},
                    "suspend_balance": -100.0,
                    "hpp_balance": 70.0,
                    "inventory_balance": 70.0,
                    "cogs_variance_balance": 42.0,
                    "selisih_hpp_amount": 30.0,
                    "coefficient_variance": 42.0,
                    "bank_balances_by_code": {"1101060": -55.0},
                    "guard_messages": [
                        "Saldo akun problem 2103006 item masih -100.00.",
                        "Saldo HPP item (5101004) masih +70.00.",
                        "Coefficient Variance 42.00% melebihi batas 35.00%.",
                    ],
                    "planned_lines": [
                        {
                            "role": "problem_2103006",
                            "account_code": "2103006",
                            "amount": 100.0,
                            "side": "debit",
                        },
                        {
                            "role": "hpp_zero",
                            "account_code": "5101004",
                            "amount": 70.0,
                            "side": "credit",
                            "line_label": "SKU-001 - Produk PCB - Case 2 - STJ Bill Price Diff (Suspend - Suspend) - Zero HPP",
                        },
                        {
                            "role": "selisih_hpp",
                            "account_code": "5101004",
                            "amount": 30.0,
                            "side": "credit",
                            "line_label": "SKU-001 - Produk PCB - Case 2 - STJ Bill Price Diff (Suspend - Suspend) - Selisih HPP",
                        },
                    ],
                    "row_status": "ready",
                    "row_status_message": "Ready",
                    "result_status": "",
                    "result_posted": False,
                    "result_error_kind": "",
                    "result_move_id": 0,
                    "result_move_name": "",
                    "existing_move_detected": False,
                }
            }
        )

        page._open_pcb_case1_repair_dialog_v2()
        self.root.update_idletasks()
        widgets = page._last_pcb_case1_dialog_widgets
        tree = widgets["tree"]
        group_ids = tree.get_children()
        self.assertEqual(len(group_ids), 1)
        self.assertIn("Case 2", tree.item(group_ids[0], "text"))
        first_iid = next(iter(widgets["row_by_iid"]))
        values = tree.item(first_iid, "values")
        self.assertIn("DR 2103006 100.00", values[6])
        self.assertIn("Guard", tree.heading("reconcile", "text"))

        tree.selection_set((first_iid,))
        tree.focus(first_iid)
        tree.event_generate("<<TreeviewSelect>>")
        self.root.update_idletasks()

        row = page._pcb_repair_collection["case2::7001::901"]
        self.assertEqual(
            row["planned_lines"][2]["line_label"],
            "Selisih HPP - SKU-001 - Produk PCB - Case 2 - STJ Bill Price Diff (Suspend - Suspend)",
        )
        self.assertIn("42.00%", widgets["summary_vars"]["coefficient_variance"].get())
        self.assertIn("DR 2103006 100.00", widgets["summary_vars"]["planned_line_preview"].get())
        self.assertIn("2103006 -100.00", widgets["summary_vars"]["problem_balances_by_code"].get())
        self.assertIn("+70.00", widgets["summary_vars"]["hpp_balance"].get())
        self.assertIn("problem_2103006: DR 2103006 100.00", widgets["advanced_vars"]["planned_lines"].get())
        self.assertIn("5101004 +70.00", widgets["advanced_vars"]["hpp_balances_by_code"].get())
        self.assertIn("Selisih HPP", widgets["advanced_vars"]["planned_lines"].get())
        self.assertIn("Coefficient Variance 42.00%", widgets["advanced_vars"]["guard_messages"].get())
        simulated_rows = [
            widgets["simulated_tree"].item(item_id, "values")
            for item_id in widgets["simulated_tree"].get_children()
        ]
        self.assertEqual(
            simulated_rows[2][4],
            "Selisih HPP - SKU-001 - Produk PCB - Case 2 - STJ Bill Price Diff (Suspend - Suspend)",
        )

        widgets["close_button"].invoke()
        self.root.update_idletasks()

    def test_pcb_repair_dialog_defaults_to_60_40_split_and_left_horizontal_scrollbar(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page.root = self.root
        page._display_name = "Fixing Unlink SVL - Odoo"
        page._last_pcb_case1_dialog_widgets = {}
        page.status_var = tk.StringVar(master=self.root, value="")
        page.context = mock.Mock(global_settings=GlobalSettings(default_output_dir=r"C:\shared\output"))
        page._module_settings = SvlFixJeSettings(last_output_dir=r"C:\exports", max_workers=2)
        page._state_store = mock.Mock()
        page._pcb_repair_collection_scope = None
        page._current_repair_collection_scope = lambda: None
        page._update_pcb_collection_button = lambda: None
        page._pcb_repair_collection = OrderedDict(
            {
                "case_lainnya::7005::6201003::SKU-001": {
                    "row_key": "case_lainnya::7005::6201003::SKU-001",
                    "cycle_key": "case_lainnya::7005",
                    "pcb_case": "case_lainnya",
                    "pcb_case_label": "Case Lainnya - Manual Reclass",
                    "reference_generated": True,
                    "line_label_generated": True,
                    "company_id": 7,
                    "company_name": "Alpha Company",
                    "picking_name": "ABSR/IN/01052",
                    "source_label": "Manual Reclass Review",
                    "po_name": "PO/ATB/2026/01/00869",
                    "bill_name": "BILL/2026/01/0202",
                    "date": "2026-03-20",
                    "reference": _pcb_case1_reference(
                        picking_name="ABSR/IN/01052",
                        bill_name="BILL/2026/01/0202",
                        item_code="C-DGGP-0002",
                        item_name="COCKTAIL SKEWER (@100PCS)",
                    ),
                    "line_label": "C-DGGP-0002 - COCKTAIL SKEWER (@100PCS) - Case Lainnya - Manual Reclass",
                    "amount": 370000.0,
                    "product_id": 901,
                    "item_code": "C-DGGP-0002",
                    "item_name": "COCKTAIL SKEWER (@100PCS)",
                    "item_category_name": "GUEST SUPPLIES",
                    "picking_id": 7005,
                    "bill_move_id": 8205,
                    "partner_id": 77,
                    "partner_name": "Vendor Alpha",
                    "journal_code": "STJ",
                    "problem_balances_by_code": {"1108099": -550000.0},
                    "hpp_balances_by_code": {"6201003": 370000.0},
                    "suspend_balance": 0.0,
                    "hpp_balance": 370000.0,
                    "inventory_balance": 0.0,
                    "cogs_variance_balance": 0.0,
                    "selisih_hpp_amount": 0.0,
                    "coefficient_variance": 0.0,
                    "bank_balances_by_code": {},
                    "guard_messages": [],
                    "planned_lines": [],
                    "review_required": True,
                    "review_confirmed": False,
                    "review_reason": "Manual reklas diperlukan.",
                    "row_status": "incomplete",
                    "row_status_message": "Jurnal simulasi belum memiliki line.",
                    "result_status": "",
                    "result_posted": False,
                    "result_error_kind": "",
                    "result_move_id": 0,
                    "result_move_name": "",
                    "existing_move_detected": False,
                }
            }
        )

        page._open_pcb_case1_repair_dialog_v2()
        self.root.update_idletasks()

        widgets = page._last_pcb_case1_dialog_widgets
        widgets["apply_split_layout"]()
        self.root.update_idletasks()

        self.assertIsInstance(widgets["content_pane"], tk.PanedWindow)
        self.assertEqual(str(widgets["left_x_scrollbar"].winfo_manager()), "grid")
        self.assertTrue(str(widgets["tree"].cget("xscrollcommand")))
        self.assertEqual(str(widgets["tree"].column("item", "stretch")), "1")
        self.assertEqual(str(widgets["tree"].column("bill", "stretch")), "1")
        self.assertGreaterEqual(int(widgets["tree"].column("item", "width")), 260)
        self.assertGreaterEqual(int(widgets["tree"].column("bill", "width")), 200)
        sash_x = widgets["content_pane"].sash_coord(0)[0]
        pane_width = widgets["measure_split_width"]()
        self.assertGreater(pane_width, 0)
        self.assertAlmostEqual(sash_x / pane_width, 0.60, delta=0.08)

        widgets["close_button"].invoke()
        self.root.update_idletasks()

    def test_pcb_repair_dialog_refreshes_stale_multiline_rows_from_latest_snapshot(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page.root = self.root
        page._display_name = "Fixing Unlink SVL - Odoo"
        page._last_pcb_case1_dialog_widgets = {}
        page.status_var = tk.StringVar(master=self.root, value="")
        page.context = mock.Mock(global_settings=GlobalSettings(default_output_dir=r"C:\shared\output"))
        page._module_settings = SvlFixJeSettings(last_output_dir=r"C:\exports", max_workers=2)
        page._state_store = mock.Mock()
        page._append_log = mock.Mock()
        page._update_pcb_collection_button = mock.Mock()
        page._selected_company_id = lambda: 7
        page._selected_company_name = lambda: "Alpha Company"

        class _MatchingScope:
            database_profile_id = "db_live"
            database_label = "Live ERP - hwgroup_erp [Database Live]"
            database_value = "hwgroup_erp"
            company_id = 7
            company_label = "Alpha Company (#7)"

            def matches(self, other) -> bool:  # noqa: ANN001
                return True

        page._pcb_repair_collection_scope = _MatchingScope()
        page._current_repair_collection_scope = lambda: _MatchingScope()
        cycle = _build_pcb_cycle(
            picking_id=7701,
            case2_repair_rows=[
                _build_pcb_case2_row(
                    row_key="case3::7701::901",
                    cycle_key="case3::7701",
                    pcb_case="case3",
                    pcb_case_label=PCB_CASE3_LABEL,
                    expense_account_code="5101003",
                    review_required=False,
                    review_confirmed=False,
                    amount=29623.47,
                    planned_lines=[
                        SvlDashboardPcbRepairPlannedLine(
                            role="problem_1108099",
                            account_code="1108099",
                            account_name="Clearing - System Pending Entries",
                            amount=29623.47,
                            side="debit",
                        ),
                        SvlDashboardPcbRepairPlannedLine(
                            role="problem_2103006",
                            account_code="2103006",
                            account_name="Hutang Suspensed Pengadaan Barang/Jasa / Suspensed Payable",
                            amount=28500.0,
                            side="credit",
                        ),
                        SvlDashboardPcbRepairPlannedLine(
                            role="selisih_hpp",
                            account_code="5101003",
                            account_name="HPP - Makanan / COGS - Food",
                            amount=1123.47,
                            side="credit",
                            line_label="Selisih HPP",
                        ),
                    ],
                )
            ],
            item_rows=[
                SvlDashboardCycleItemRow(
                    product_id=901,
                    product_name="TEMPE DAUN",
                    default_code="F-FHVF-0219",
                    has_item_bill=True,
                    has_item_stj=True,
                    eligible_case34=True,
                    account_rows=[
                        SvlDashboardCycleAccountRow(
                            account_id=1,
                            code="2101002",
                            name="Payable to Third",
                            account_type="liability_payable",
                            account_group="liability",
                            debit=26207.94,
                            credit=26207.94,
                            net_balance=0.0,
                            status="balanced",
                        ),
                        SvlDashboardCycleAccountRow(
                            account_id=2,
                            code="1101060",
                            name="BCA",
                            account_type="asset_cash",
                            account_group="asset",
                            debit=0.0,
                            credit=26207.94,
                            net_balance=-26207.94,
                            status="acceptable",
                        ),
                        SvlDashboardCycleAccountRow(
                            account_id=3,
                            code="2103006",
                            name="Hutang Suspensed Pengadaan Barang/Jasa / Suspensed Payable",
                            account_type="liability_current",
                            account_group="liability",
                            debit=28500.0,
                            credit=0.0,
                            net_balance=28500.0,
                            status="problem",
                        ),
                        SvlDashboardCycleAccountRow(
                            account_id=4,
                            code="1105003",
                            name="Persediaan Makanan / Food Inventory",
                            account_type="asset_current",
                            account_group="asset",
                            debit=29623.47,
                            credit=0.0,
                            net_balance=29623.47,
                            status="acceptable",
                        ),
                        SvlDashboardCycleAccountRow(
                            account_id=5,
                            code="1108099",
                            name="Clearing - System Pending Entries",
                            account_type="liability_current",
                            account_group="liability",
                            debit=0.0,
                            credit=29623.47,
                            net_balance=-29623.47,
                            status="problem",
                        ),
                        SvlDashboardCycleAccountRow(
                            account_id=6,
                            code="5101003",
                            name="HPP - Makanan / COGS - Food",
                            account_type="expense_direct_cost",
                            account_group="expense",
                            debit=29623.47,
                            credit=0.0,
                            net_balance=29623.47,
                            status="acceptable",
                        ),
                    ],
                )
            ],
        )
        snapshot = SvlDashboardSnapshot(
            database="hwgroup_erp",
            company_id=7,
            company_name="Alpha Company",
            generated_at="2026-03-28 10:00:00",
            period="2026-03-01..2026-03-31",
            purchase_cycles=[cycle],
        )
        page._latest_snapshot = snapshot
        latest_seed = page._build_pcb_seeds_for_cycle(cycle)[0]
        stale_row = dict(latest_seed)
        stale_row["amount"] = 9874.49
        stale_row["planned_lines"] = [
            {
                "role": "problem_1108099",
                "account_code": "1108099",
                "account_name": "Clearing - System Pending Entries",
                "amount": 9874.49,
                "side": "debit",
            },
            {
                "role": "problem_2103006",
                "account_code": "2103006",
                "account_name": "Hutang Suspensed Pengadaan Barang/Jasa / Suspensed Payable",
                "amount": 9500.0,
                "side": "credit",
            },
            {
                "role": "selisih_hpp",
                "account_code": "5101003",
                "account_name": "HPP - Makanan / COGS - Food",
                "amount": 374.49,
                "side": "credit",
                "line_label": "Selisih HPP",
            },
        ]
        stale_row["row_status"] = "ready"
        stale_row["row_status_message"] = "Ready"
        page._pcb_repair_collection = OrderedDict({stale_row["row_key"]: stale_row})

        page._open_pcb_case1_repair_dialog_v2()
        self.root.update_idletasks()

        refreshed_row = page._pcb_repair_collection[stale_row["row_key"]]
        self.assertEqual([line["amount"] for line in refreshed_row["planned_lines"]], [29623.47, 28500.0, 1123.47])

        widgets = page._last_pcb_case1_dialog_widgets
        tree = widgets["tree"]
        row_iid = next(iter(widgets["row_by_iid"]))
        tree.selection_set((row_iid,))
        tree.focus(row_iid)
        tree.event_generate("<<TreeviewSelect>>")
        self.root.update_idletasks()

        current_rows = {
            values[1]: values
            for values in (
                widgets["current_cycle_tree"].item(item_id, "values")
                for item_id in widgets["current_cycle_tree"].get_children()
            )
        }
        simulated_rows = [
            widgets["simulated_tree"].item(item_id, "values")
            for item_id in widgets["simulated_tree"].get_children()
        ]
        projected_rows = {
            values[1]: values
            for values in (
                widgets["projected_cycle_tree"].item(item_id, "values")
                for item_id in widgets["projected_cycle_tree"].get_children()
            )
        }

        self.assertEqual(current_rows["2103006"][5], "+28,500.00")
        self.assertEqual(current_rows["1108099"][5], "-29,623.47")
        self.assertEqual([values[3] for values in simulated_rows], ["29,623.47", "28,500.00", "1,123.47"])
        self.assertEqual(projected_rows["2103006"][5], "+0.00")
        self.assertEqual(projected_rows["1108099"][5], "+0.00")

        widgets["close_button"].invoke()
        self.root.update_idletasks()

    def test_pcb_repair_dialog_renders_variance_zero_role_with_human_label(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page.root = self.root
        page._display_name = "Fixing Unlink SVL - Odoo"
        page._last_pcb_case1_dialog_widgets = {}
        page.status_var = tk.StringVar(master=self.root, value="")
        page.context = mock.Mock(global_settings=GlobalSettings(default_output_dir=r"C:\shared\output"))
        page._module_settings = SvlFixJeSettings(last_output_dir=r"C:\exports", max_workers=2)
        page._state_store = mock.Mock()
        page._pcb_repair_collection_scope = None
        page._current_repair_collection_scope = lambda: None
        page._update_pcb_collection_button = lambda: None
        page._pcb_repair_collection = OrderedDict(
            {
                "case2::7002::902": {
                    "row_key": "case2::7002::902",
                    "cycle_key": "case2::7002",
                    "pcb_case": "case2",
                    "pcb_case_label": PCB_CASE2_LABEL,
                    "reference_generated": True,
                    "line_label_generated": True,
                    "company_id": 7,
                    "company_name": "Alpha Company",
                    "picking_name": "CBG/IN/01048",
                    "source_label": "Item Balance",
                    "po_name": "PO/2026/0002",
                    "bill_name": "BILL/2026/0002",
                    "date": "2026-03-19",
                    "reference": _pcb_case1_reference(
                        picking_name="CBG/IN/01048",
                        bill_name="BILL/2026/0002",
                        item_code="SKU-002",
                        item_name="Produk Variance",
                    ),
                    "line_label": "SKU-002 - Produk Variance - Case 2 - STJ Bill Price Diff (Suspend - Suspend)",
                    "amount": 100.0,
                    "product_id": 902,
                    "item_code": "SKU-002",
                    "item_name": "Produk Variance",
                    "item_category_name": "Food",
                    "picking_id": 7002,
                    "bill_move_id": 8202,
                    "partner_id": 77,
                    "partner_name": "Vendor Alpha",
                    "journal_code": "STJ",
                    "problem_balances_by_code": {"2103006": -100.0},
                    "hpp_balances_by_code": {},
                    "suspend_balance": -100.0,
                    "hpp_balance": 0.0,
                    "inventory_balance": 100.0,
                    "cogs_variance_balance": 100.0,
                    "selisih_hpp_amount": 0.0,
                    "coefficient_variance": 0.0,
                    "bank_balances_by_code": {},
                    "guard_messages": [
                        "Saldo akun problem 2103006 item masih -100.00.",
                    ],
                    "planned_lines": [
                        {
                            "role": "problem_2103006",
                            "account_code": "2103006",
                            "amount": 100.0,
                            "side": "debit",
                        },
                        {
                            "role": "variance_zero_5101010",
                            "account_code": "5101010",
                            "amount": 100.0,
                            "side": "credit",
                            "line_label": "SKU-002 - Produk Variance - Case 2 - STJ Bill Price Diff (Suspend - Suspend) - Zero Selisih HPP",
                        },
                    ],
                    "row_status": "ready",
                    "row_status_message": "Ready",
                    "result_status": "",
                    "result_posted": False,
                    "result_error_kind": "",
                    "result_move_id": 0,
                    "result_move_name": "",
                    "existing_move_detected": False,
                }
            }
        )

        page._open_pcb_case1_repair_dialog_v2()
        self.root.update_idletasks()
        widgets = page._last_pcb_case1_dialog_widgets
        first_iid = next(iter(widgets["row_by_iid"]))
        widgets["tree"].selection_set((first_iid,))
        widgets["tree"].focus(first_iid)
        widgets["tree"].event_generate("<<TreeviewSelect>>")
        self.root.update_idletasks()

        planned_lines_text = widgets["advanced_vars"]["planned_lines"].get()
        self.assertIn("Zero Selisih HPP: CR 5101010 100.00", planned_lines_text)
        self.assertNotIn("variance_zero_5101010", planned_lines_text)

        widgets["close_button"].invoke()
        self.root.update_idletasks()

    def test_pcb_repair_dialog_resolve_override_editor_only_enables_for_editable_rows(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page.root = self.root
        page._display_name = "Fixing Unlink SVL - Odoo"
        page._last_pcb_case1_dialog_widgets = {}
        page.status_var = tk.StringVar(master=self.root, value="")
        page.context = mock.Mock(
            global_settings=GlobalSettings(
                default_output_dir=r"C:\shared\output",
                repair_account_entries=[RepairAccountEntry(coa_code="1108099", label="Clearing Override")],
            )
        )
        page._module_settings = SvlFixJeSettings(last_output_dir=r"C:\exports", max_workers=2)
        page._state_store = mock.Mock()
        page._pcb_repair_collection_scope = None
        page._current_repair_collection_scope = lambda: None
        page._update_pcb_collection_button = lambda: None
        page._pcb_repair_collection = OrderedDict(
            {
                "row-incomplete": {
                    "row_key": "row-incomplete",
                    "cycle_key": "case4::7003",
                    "pcb_case": "case4",
                    "pcb_case_label": "Case 4 - STJ Bill Hit Expenses (Suspend - Expenses)",
                    "reference_generated": True,
                    "line_label_generated": True,
                    "company_id": 7,
                    "company_name": "Alpha Company",
                    "picking_name": "LHPK/IN/7003",
                    "source_label": "Item Balance",
                    "po_name": "PO/2026/0003",
                    "bill_name": "BILL/2026/0003",
                    "date": "2026-03-19",
                    "reference": _pcb_case1_reference(
                        picking_name="LHPK/IN/7003",
                        bill_name="BILL/2026/0003",
                        item_code="SKU-003",
                        item_name="Produk Needs Review",
                    ),
                    "line_label": "SKU-003 - Produk Needs Review - Case 4 - STJ Bill Hit Expenses (Suspend - Expenses)",
                    "amount": 100.0,
                    "product_id": 903,
                    "item_code": "SKU-003",
                    "item_name": "Produk Needs Review",
                    "item_category_name": "Raw Material",
                    "picking_id": 7003,
                    "bill_move_id": 8203,
                    "partner_id": 77,
                    "partner_name": "Vendor Alpha",
                    "journal_code": "STJ",
                    "expense_account_code": "",
                    "suggested_expense_account_code": "",
                    "review_required": True,
                    "review_confirmed": False,
                    "review_reason": "Manual review wajib sebelum execute.",
                    "guard_flags": ["missing_expense_account", "review_required_case34_bill_only"],
                    "guard_messages": [
                        "Akun HPP/expense item dari kategori tidak ditemukan untuk line Zero HPP / Selisih HPP.",
                        "Manual review wajib sebelum execute.",
                    ],
                    "planned_lines": [
                        {"role": "problem_2103006", "account_code": "2103006", "amount": 100.0, "side": "debit"},
                        {"role": "selisih_hpp", "account_code": "", "amount": 100.0, "side": "credit"},
                    ],
                    "problem_balances_by_code": {"2103006": -100.0},
                    "row_status": "incomplete",
                    "row_status_message": "Belum siap dijalankan.",
                },
                "row-ready": {
                    "row_key": "row-ready",
                    "cycle_key": "case2::7004",
                    "pcb_case": "case2",
                    "pcb_case_label": PCB_CASE2_LABEL,
                    "reference_generated": True,
                    "line_label_generated": True,
                    "company_id": 7,
                    "company_name": "Alpha Company",
                    "picking_name": "LHPK/IN/7004",
                    "source_label": "Item Balance",
                    "po_name": "PO/2026/0004",
                    "bill_name": "BILL/2026/0004",
                    "date": "2026-03-19",
                    "reference": _pcb_case1_reference(
                        picking_name="LHPK/IN/7004",
                        bill_name="BILL/2026/0004",
                        item_code="SKU-004",
                        item_name="Produk Ready",
                    ),
                    "line_label": "SKU-004 - Produk Ready - Case 2 - STJ Bill Price Diff (Suspend - Suspend)",
                    "amount": 100.0,
                    "product_id": 904,
                    "item_code": "SKU-004",
                    "item_name": "Produk Ready",
                    "item_category_name": "Raw Material",
                    "picking_id": 7004,
                    "bill_move_id": 8204,
                    "partner_id": 77,
                    "partner_name": "Vendor Alpha",
                    "journal_code": "STJ",
                    "expense_account_code": "5101004",
                    "suggested_expense_account_code": "5101004",
                    "planned_lines": [
                        {"role": "problem_2103006", "account_code": "2103006", "amount": 100.0, "side": "debit"},
                        {"role": "hpp_zero", "account_code": "5101004", "amount": 70.0, "side": "credit"},
                        {"role": "selisih_hpp", "account_code": "5101004", "amount": 30.0, "side": "credit"},
                    ],
                    "problem_balances_by_code": {"2103006": -100.0},
                    "hpp_balances_by_code": {"5101004": 70.0},
                    "row_status": "ready",
                    "row_status_message": "Ready",
                },
            }
        )

        page._open_pcb_case1_repair_dialog_v2()
        self.root.update_idletasks()
        widgets = page._last_pcb_case1_dialog_widgets
        tree = widgets["tree"]
        incomplete_iid = next(iid for iid, row in widgets["row_by_iid"].items() if row["row_key"] == "row-incomplete")
        ready_iid = next(iid for iid, row in widgets["row_by_iid"].items() if row["row_key"] == "row-ready")

        tree.selection_set((incomplete_iid,))
        tree.focus(incomplete_iid)
        tree.event_generate("<<TreeviewSelect>>")
        self.root.update_idletasks()
        self.assertEqual(widgets["resolve_picker"].entry.cget("state"), "normal")

        tree.selection_set((ready_iid,))
        tree.focus(ready_iid)
        tree.event_generate("<<TreeviewSelect>>")
        self.root.update_idletasks()
        self.assertEqual(widgets["resolve_picker"].entry.cget("state"), "disabled")

        widgets["close_button"].invoke()
        self.root.update_idletasks()

    def test_pcb_repair_dialog_apply_resolve_override_rewrites_planned_lines(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page.root = self.root
        page._display_name = "Fixing Unlink SVL - Odoo"
        page._last_pcb_case1_dialog_widgets = {}
        page.status_var = tk.StringVar(master=self.root, value="")
        page.context = mock.Mock(
            global_settings=GlobalSettings(
                default_output_dir=r"C:\shared\output",
                repair_account_entries=[RepairAccountEntry(coa_code="1108099", label="Clearing Override")],
            )
        )
        page._module_settings = SvlFixJeSettings(last_output_dir=r"C:\exports", max_workers=2)
        page._state_store = mock.Mock()
        page._pcb_repair_collection_scope = None
        page._current_repair_collection_scope = lambda: None
        page._update_pcb_collection_button = lambda: None
        page._latest_snapshot = SvlDashboardSnapshot(
            database="hwgroup_erp",
            company_id=7,
            company_name="Alpha Company",
            generated_at="2026-03-28 10:00:00",
            period="2026-03-01..2026-03-31",
            purchase_cycles=[
                _build_pcb_cycle(
                    picking_id=7010,
                    account_rows=[
                        SvlDashboardCycleAccountRow(
                            account_id=301,
                            code="2101002",
                            name="Hutang Pihak Ketiga",
                            account_type="liability_payable",
                            account_group="liability",
                            debit=100.0,
                            credit=100.0,
                            net_balance=0.0,
                            status="balanced",
                        ),
                        SvlDashboardCycleAccountRow(
                            account_id=302,
                            code="2103006",
                            name="Hutang Suspend",
                            account_type="liability_current",
                            account_group="liability",
                            debit=0.0,
                            credit=500.0,
                            net_balance=-500.0,
                            status="problem",
                        ),
                        SvlDashboardCycleAccountRow(
                            account_id=303,
                            code="11120003",
                            name="Outstanding Payments",
                            account_type="asset_current",
                            account_group="asset",
                            debit=200.0,
                            credit=200.0,
                            net_balance=0.0,
                            status="balanced",
                        ),
                        SvlDashboardCycleAccountRow(
                            account_id=304,
                            code="1108099",
                            name="Clearing",
                            account_type="asset_current",
                            account_group="asset",
                            debit=400.0,
                            credit=0.0,
                            net_balance=400.0,
                            status="problem",
                        ),
                    ],
                    item_rows=[
                        SvlDashboardCycleItemRow(
                            product_id=910,
                            product_name="Produk Override",
                            default_code="SKU-010",
                            account_rows=[
                                SvlDashboardCycleAccountRow(
                                    account_id=501,
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
                )
            ],
        )
        page._pcb_repair_collection = OrderedDict(
            {
                "row-needs-review": {
                    "row_key": "row-needs-review",
                    "cycle_key": "case4::7010",
                    "pcb_case": "case4",
                    "pcb_case_label": "Case 4 - STJ Bill Hit Expenses (Suspend - Expenses)",
                    "reference_generated": True,
                    "line_label_generated": True,
                    "company_id": 7,
                    "company_name": "Alpha Company",
                    "picking_name": "LHPK/IN/7010",
                    "source_label": "Item Balance",
                    "po_name": "PO/2026/0010",
                    "bill_name": "BILL/2026/0010",
                    "date": "2026-03-19",
                    "reference": _pcb_case1_reference(
                        picking_name="LHPK/IN/7010",
                        bill_name="BILL/2026/0010",
                        item_code="SKU-010",
                        item_name="Produk Override",
                    ),
                    "line_label": "SKU-010 - Produk Override - Case 4 - STJ Bill Hit Expenses (Suspend - Expenses)",
                    "amount": 100.0,
                    "product_id": 910,
                    "item_code": "SKU-010",
                    "item_name": "Produk Override",
                    "item_category_name": "Raw Material",
                    "picking_id": 7010,
                    "bill_move_id": 8210,
                    "partner_id": 77,
                    "partner_name": "Vendor Alpha",
                    "journal_code": "STJ",
                    "expense_account_code": "",
                    "suggested_expense_account_code": "",
                    "review_required": True,
                    "review_confirmed": False,
                    "review_reason": "Manual review wajib sebelum execute.",
                    "guard_flags": ["missing_expense_account", "review_required_case34_bill_only"],
                    "guard_messages": [
                        "Akun HPP/expense item dari kategori tidak ditemukan untuk line Zero HPP / Selisih HPP.",
                        "Manual review wajib sebelum execute.",
                    ],
                    "planned_lines": [
                        {"role": "problem_2103006", "account_code": "2103006", "amount": 100.0, "side": "debit"},
                        {"role": "selisih_hpp", "account_code": "", "amount": 100.0, "side": "credit"},
                    ],
                    "problem_balances_by_code": {"2103006": -100.0},
                    "row_status": "incomplete",
                    "row_status_message": "Belum siap dijalankan.",
                }
            }
        )

        page._open_pcb_case1_repair_dialog_v2()
        self.root.update_idletasks()
        widgets = page._last_pcb_case1_dialog_widgets
        tree = widgets["tree"]
        row_iid = next(iter(widgets["row_by_iid"]))
        tree.selection_set((row_iid,))
        tree.focus(row_iid)
        tree.event_generate("<<TreeviewSelect>>")
        self.root.update_idletasks()

        simulated_rows_before = [
            widgets["simulated_tree"].item(item_id, "values")
            for item_id in widgets["simulated_tree"].get_children()
        ]
        projected_rows_before = [
            widgets["projected_cycle_tree"].item(item_id, "values")
            for item_id in widgets["projected_cycle_tree"].get_children()
        ]
        current_rows_before = [
            widgets["current_cycle_tree"].item(item_id, "values")
            for item_id in widgets["current_cycle_tree"].get_children()
        ]
        current_codes_before = [values[1] for values in current_rows_before]
        self.assertEqual(simulated_rows_before[1][1], "?")
        self.assertIn("tanpa account code final", widgets["simulated_note_var"].get())
        self.assertEqual(current_codes_before, ["2103006"])
        self.assertIn("?", [values[1] for values in projected_rows_before])
        self.assertNotIn("2101002", [values[1] for values in projected_rows_before])

        widgets["resolve_var"].set("1108099")
        widgets["apply_button"].invoke()
        self.root.update_idletasks()

        row = page._pcb_repair_collection["row-needs-review"]
        self.assertEqual(row["resolve_account_code"], "1108099")
        self.assertEqual(row["expense_account_code"], "1108099")
        self.assertTrue(row["resolve_account_manual"])
        self.assertEqual(row["planned_lines"][1]["account_code"], "1108099")
        self.assertEqual(row["row_status"], "needs_review")
        self.assertIn("1108099", widgets["summary_vars"]["resolve_account_preview"].get())
        self.assertTrue(widgets["resolve_preview_var"].get().startswith("1108099"))
        self.assertNotIn("[", widgets["resolve_preview_var"].get())
        simulated_rows_after = [
            widgets["simulated_tree"].item(item_id, "values")
            for item_id in widgets["simulated_tree"].get_children()
        ]
        projected_rows_after = [
            widgets["projected_cycle_tree"].item(item_id, "values")
            for item_id in widgets["projected_cycle_tree"].get_children()
        ]
        projected_by_code = {values[1]: values for values in projected_rows_after}
        self.assertEqual(simulated_rows_after[1][1], "1108099")
        self.assertEqual(widgets["simulated_note_var"].get(), "Belum execute - preview JE only.")
        self.assertNotIn("?", projected_by_code)
        self.assertNotIn("2101002", projected_by_code)
        self.assertNotIn("11120003", projected_by_code)
        self.assertEqual(projected_by_code["2103006"][5], "+0.00")
        self.assertEqual(projected_by_code["1108099"][5], "-100.00")

        widgets["close_button"].invoke()
        self.root.update_idletasks()

    def test_pcb_repair_dialog_simulation_editor_suggests_deduped_cycle_problem_and_fixed_accounts(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page.root = self.root
        page._display_name = "Fixing Unlink SVL - Odoo"
        page._last_pcb_case1_dialog_widgets = {}
        page.status_var = tk.StringVar(master=self.root, value="")
        page.context = mock.Mock(global_settings=GlobalSettings(default_output_dir=r"C:\shared\output"))
        page._module_settings = SvlFixJeSettings(last_output_dir=r"C:\exports", max_workers=2)
        page._state_store = mock.Mock()
        page._pcb_repair_collection_scope = None
        page._current_repair_collection_scope = lambda: None
        page._update_pcb_collection_button = lambda: None
        page._selected_company_id = lambda: 7
        page._selected_company_name = lambda: "Alpha Company"
        cycle = _build_pcb_cycle(
            picking_id=7021,
            case2_repair_rows=[
                _build_pcb_case2_row(
                    row_key="case4::7021::901",
                    cycle_key="case4::7021",
                    pcb_case="case4",
                    pcb_case_label=PCB_CASE4_LABEL,
                    review_required=True,
                    review_confirmed=False,
                    review_reason="Manual review wajib sebelum execute.",
                )
            ],
            item_rows=[
                SvlDashboardCycleItemRow(
                    product_id=901,
                    product_name="Produk PCB",
                    default_code="SKU-001",
                    has_item_bill=True,
                    has_item_stj=True,
                    eligible_case34=True,
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
                            account_id=302,
                            code="5101004",
                            name="HPP Rokok",
                            account_type="expense_direct_cost",
                            account_group="expense",
                            debit=70.0,
                            credit=0.0,
                            net_balance=70.0,
                            status="problem",
                        ),
                    ],
                ),
                SvlDashboardCycleItemRow(
                    product_id=902,
                    product_name="Produk Problem Lain",
                    default_code="SKU-002",
                    has_item_bill=True,
                    has_item_stj=False,
                    eligible_case34=False,
                    account_rows=[
                        SvlDashboardCycleAccountRow(
                            account_id=401,
                            code="1105004",
                            name="Persediaan Bahan Baku",
                            account_type="asset_current",
                            account_group="asset",
                            debit=55.0,
                            credit=0.0,
                            net_balance=55.0,
                            status="problem",
                        )
                    ],
                ),
            ],
        )
        page._latest_snapshot = SvlDashboardSnapshot(
            database="hwgroup_erp",
            company_id=7,
            company_name="Alpha Company",
            generated_at="2026-03-28 10:00:00",
            period="2026-03-01..2026-03-31",
            purchase_cycles=[cycle],
        )
        seed = page._build_pcb_case2_seeds_for_cycle(cycle)[0]
        page._pcb_repair_collection = OrderedDict({seed["row_key"]: seed})

        page._open_pcb_case1_repair_dialog_v2()
        self.root.update_idletasks()
        widgets = page._last_pcb_case1_dialog_widgets
        tree = widgets["tree"]
        row_iid = next(iter(widgets["row_by_iid"]))
        tree.selection_set((row_iid,))
        tree.focus(row_iid)
        tree.event_generate("<<TreeviewSelect>>")
        self.root.update_idletasks()

        candidates = widgets["simulated_line_picker"]._all_candidates  # noqa: SLF001
        codes = [candidate.code for candidate in candidates]
        names_by_code = {candidate.code: candidate.name for candidate in candidates}

        self.assertEqual(len(codes), len(set(codes)))
        self.assertIn("1105004", codes)
        self.assertIn("5101004", codes)
        self.assertIn("1108099", codes)
        self.assertIn("2103006", codes)
        self.assertEqual(names_by_code["1108099"], "Clearing - System Pending Entries")
        self.assertEqual(names_by_code["2103006"], "Hutang Suspensed Pengadaan Barang/Jasa / Suspensed")

        widgets["close_button"].invoke()
        self.root.update_idletasks()

    def test_pcb_repair_dialog_simulation_editor_updates_and_adds_manual_lines(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page.root = self.root
        page._display_name = "Fixing Unlink SVL - Odoo"
        page._last_pcb_case1_dialog_widgets = {}
        page.status_var = tk.StringVar(master=self.root, value="")
        page.context = mock.Mock(global_settings=GlobalSettings(default_output_dir=r"C:\shared\output"))
        page._module_settings = SvlFixJeSettings(last_output_dir=r"C:\exports", max_workers=2)
        page._state_store = mock.Mock()
        page._pcb_repair_collection_scope = None
        page._current_repair_collection_scope = lambda: None
        page._update_pcb_collection_button = lambda: None
        page._selected_company_id = lambda: 7
        page._selected_company_name = lambda: "Alpha Company"
        cycle = _build_pcb_cycle(
            picking_id=7022,
            case2_repair_rows=[
                _build_pcb_case2_row(
                    row_key="case4::7022::901",
                    cycle_key="case4::7022",
                    pcb_case="case4",
                    pcb_case_label=PCB_CASE4_LABEL,
                    expense_account_code="",
                    review_required=True,
                    review_confirmed=False,
                    review_reason="Manual review wajib sebelum execute.",
                    guard_flags=["missing_expense_account", "review_required_case34_bill_only"],
                    guard_messages=[
                        "Akun HPP/expense item dari kategori tidak ditemukan untuk line Zero HPP / Selisih HPP.",
                        "Manual review wajib sebelum execute.",
                    ],
                    planned_lines=[
                        SvlDashboardPcbRepairPlannedLine(
                            role="problem_2103006",
                            account_code="2103006",
                            account_name="Hutang Suspend",
                            amount=100.0,
                            side="debit",
                        ),
                        SvlDashboardPcbRepairPlannedLine(
                            role="selisih_hpp",
                            account_code="",
                            account_name="",
                            amount=100.0,
                            side="credit",
                            line_label="Target expense belum ditentukan",
                        ),
                    ],
                )
            ],
            item_rows=[
                SvlDashboardCycleItemRow(
                    product_id=901,
                    product_name="Produk PCB",
                    default_code="SKU-001",
                    has_item_bill=True,
                    has_item_stj=True,
                    eligible_case34=True,
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
                        )
                    ],
                )
            ],
        )
        page._latest_snapshot = SvlDashboardSnapshot(
            database="hwgroup_erp",
            company_id=7,
            company_name="Alpha Company",
            generated_at="2026-03-28 10:00:00",
            period="2026-03-01..2026-03-31",
            purchase_cycles=[cycle],
        )
        seed = page._build_pcb_case2_seeds_for_cycle(cycle)[0]
        page._pcb_repair_collection = OrderedDict({seed["row_key"]: seed})

        page._open_pcb_case1_repair_dialog_v2()
        self.root.update_idletasks()
        widgets = page._last_pcb_case1_dialog_widgets
        tree = widgets["tree"]
        row_iid = next(iter(widgets["row_by_iid"]))
        tree.selection_set((row_iid,))
        tree.focus(row_iid)
        tree.event_generate("<<TreeviewSelect>>")
        self.root.update_idletasks()

        simulated_tree = widgets["simulated_tree"]
        simulated_line_ids = simulated_tree.get_children()
        simulated_tree.selection_set((simulated_line_ids[1],))
        simulated_tree.focus(simulated_line_ids[1])
        simulated_tree.event_generate("<<TreeviewSelect>>")
        self.root.update_idletasks()

        widgets["simulated_line_account_var"].set("1108099")
        widgets["simulated_line_name_var"].set("Clearing - System Pending Entries")
        widgets["simulated_line_amount_var"].set("100")
        widgets["simulated_line_label_var"].set("Override target expense")
        widgets["simulated_line_update_button"].invoke()
        self.root.update_idletasks()

        row = page._pcb_repair_collection[seed["row_key"]]
        self.assertTrue(row["planned_lines_manual"])
        self.assertEqual(row["planned_lines"][1]["account_code"], "1108099")
        self.assertEqual(row["planned_lines"][1]["line_label"], "Override target expense")

        widgets["simulated_line_side_var"].set("credit")
        widgets["simulated_line_account_var"].set("2103006")
        widgets["simulated_line_name_var"].set("Hutang Suspensed Pengadaan Barang/Jasa / Suspensed")
        widgets["simulated_line_amount_var"].set("15")
        widgets["simulated_line_label_var"].set("Tambahan Manual")
        widgets["simulated_line_add_button"].invoke()
        self.root.update_idletasks()

        row = page._pcb_repair_collection[seed["row_key"]]
        self.assertEqual(len(row["planned_lines"]), 3)
        self.assertEqual(row["planned_lines"][-1]["role"], "manual_simulation")
        self.assertEqual(row["planned_lines"][-1]["account_code"], "2103006")
        self.assertEqual(row["row_status"], "incomplete")
        self.assertIn("belum balance", row["row_status_message"].lower())

        simulated_rows = [
            widgets["simulated_tree"].item(item_id, "values")
            for item_id in widgets["simulated_tree"].get_children()
        ]
        self.assertTrue(any("Manual Simulasi" in values[4] and "Tambahan Manual" in values[4] for values in simulated_rows))

        widgets["close_button"].invoke()
        self.root.update_idletasks()

    def test_pcb_repair_dialog_simulation_panel_renders_case1_and_multiselect_placeholder(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page.root = self.root
        page._display_name = "Fixing Unlink SVL - Odoo"
        page._last_pcb_case1_dialog_widgets = {}
        page.status_var = tk.StringVar(master=self.root, value="")
        page.context = mock.Mock(global_settings=GlobalSettings(default_output_dir=r"C:\shared\output"))
        page._module_settings = SvlFixJeSettings(last_output_dir=r"C:\exports", max_workers=2)
        page._state_store = mock.Mock()
        page._pcb_repair_collection_scope = None
        page._current_repair_collection_scope = lambda: None
        page._update_pcb_collection_button = lambda: None
        page._latest_snapshot = SvlDashboardSnapshot(
            database="hwgroup_erp",
            company_id=7,
            company_name="Alpha Company",
            generated_at="2026-03-28 10:00:00",
            period="2026-03-01..2026-03-31",
            purchase_cycles=[
                _build_pcb_cycle(
                    picking_id=7001,
                    account_rows=[
                        SvlDashboardCycleAccountRow(
                            account_id=301,
                            code="2101002",
                            name="Hutang Pihak Ketiga",
                            account_type="liability_payable",
                            account_group="liability",
                            debit=0.0,
                            credit=200.0,
                            net_balance=-200.0,
                            status="acceptable",
                        ),
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
                            account_id=303,
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
                            account_id=304,
                            code="11120003",
                            name="Outstanding Payments",
                            account_type="asset_current",
                            account_group="asset",
                            debit=0.0,
                            credit=200.0,
                            net_balance=-200.0,
                            status="balanced",
                        ),
                    ],
                    item_rows=[
                        SvlDashboardCycleItemRow(
                            product_id=901,
                            product_name="Produk PCB",
                            default_code="SKU-001",
                            account_rows=[
                                SvlDashboardCycleAccountRow(
                                    account_id=501,
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
                                    account_id=502,
                                    code="1108099",
                                    name="Clearing",
                                    account_type="asset_current",
                                    account_group="asset",
                                    debit=100.0,
                                    credit=0.0,
                                    net_balance=100.0,
                                    status="problem",
                                ),
                            ],
                        )
                    ],
                ),
                _build_pcb_cycle(
                    picking_id=7002,
                    account_rows=[
                        SvlDashboardCycleAccountRow(
                            account_id=401,
                            code="2103006",
                            name="Hutang Suspend",
                            account_type="liability_current",
                            account_group="liability",
                            debit=0.0,
                            credit=50.0,
                            net_balance=-50.0,
                            status="problem",
                        ),
                        SvlDashboardCycleAccountRow(
                            account_id=402,
                            code="5101004",
                            name="HPP Rokok",
                            account_type="expense_direct_cost",
                            account_group="expense",
                            debit=50.0,
                            credit=0.0,
                            net_balance=50.0,
                            status="acceptable",
                        ),
                    ],
                    item_rows=[
                        SvlDashboardCycleItemRow(
                            product_id=902,
                            product_name="Produk Kedua",
                            default_code="SKU-002",
                            account_rows=[
                                SvlDashboardCycleAccountRow(
                                    account_id=601,
                                    code="2103006",
                                    name="Hutang Suspend",
                                    account_type="liability_current",
                                    account_group="liability",
                                    debit=0.0,
                                    credit=50.0,
                                    net_balance=-50.0,
                                    status="problem",
                                ),
                                SvlDashboardCycleAccountRow(
                                    account_id=602,
                                    code="5101004",
                                    name="HPP Rokok",
                                    account_type="expense_direct_cost",
                                    account_group="expense",
                                    debit=50.0,
                                    credit=0.0,
                                    net_balance=50.0,
                                    status="acceptable",
                                ),
                            ],
                        )
                    ],
                ),
            ],
        )
        page._pcb_repair_collection = OrderedDict(
            {
                "case1::7001::901::bill_line::8101": {
                    "row_key": "case1::7001::901::bill_line::8101",
                    "cycle_key": "case1::7001",
                    "pcb_case": "case1",
                    "company_id": 7,
                    "company_name": "Alpha Company",
                    "amount": 100.0,
                    "date": "2026-03-19",
                    "reference": _pcb_case1_reference(
                        picking_name="LHPK/IN/7001",
                        bill_name="BILL/2026/0001",
                        item_code="SKU-001",
                        item_name="Produk PCB",
                    ),
                    "line_label": _pcb_case1_line_label(item_code="SKU-001", item_name="Produk PCB"),
                    "journal_code": "STJ",
                    "debit_account_code": "2103006",
                    "credit_account_code": "1108099",
                    "source_kind": "bill_line",
                    "source_id": 8101,
                    "source_label": "Bill Line #8101",
                    "product_id": 901,
                    "item_code": "SKU-001",
                    "item_name": "Produk PCB",
                    "picking_id": 7001,
                    "picking_name": "LHPK/IN/7001",
                    "po_name": "PO/2026/0001",
                    "bill_move_id": 8201,
                    "bill_name": "BILL/2026/0001",
                    "partner_id": 77,
                    "partner_name": "Vendor Alpha",
                    "reconcile_readiness_label": "Disabled",
                    "row_status": "ready",
                    "row_status_message": "Ready",
                },
                "case2::7002::902": {
                    "row_key": "case2::7002::902",
                    "cycle_key": "case2::7002",
                    "pcb_case": "case2",
                    "pcb_case_label": PCB_CASE2_LABEL,
                    "company_id": 7,
                    "company_name": "Alpha Company",
                    "amount": 50.0,
                    "date": "2026-03-19",
                    "reference": _pcb_case1_reference(
                        picking_name="LHPK/IN/7002",
                        bill_name="BILL/2026/0002",
                        item_code="SKU-002",
                        item_name="Produk Kedua",
                    ),
                    "line_label": "SKU-002 - Produk Kedua - Case 2 - STJ Bill Price Diff (Suspend - Suspend)",
                    "journal_code": "STJ",
                    "product_id": 902,
                    "item_code": "SKU-002",
                    "item_name": "Produk Kedua",
                    "picking_id": 7002,
                    "picking_name": "LHPK/IN/7002",
                    "po_name": "PO/2026/0002",
                    "bill_move_id": 8202,
                    "bill_name": "BILL/2026/0002",
                    "partner_id": 77,
                    "partner_name": "Vendor Alpha",
                    "expense_account_code": "5101004",
                    "suggested_expense_account_code": "5101004",
                    "planned_lines": [
                        {"role": "problem_2103006", "account_code": "2103006", "amount": 50.0, "side": "debit"},
                        {"role": "selisih_hpp", "account_code": "5101004", "amount": 50.0, "side": "credit"},
                    ],
                    "row_status": "ready",
                    "row_status_message": "Ready",
                },
            }
        )

        page._open_pcb_case1_repair_dialog_v2()
        self.root.update_idletasks()
        widgets = page._last_pcb_case1_dialog_widgets
        tree = widgets["tree"]
        case1_iid = next(iid for iid, row in widgets["row_by_iid"].items() if row["row_key"] == "case1::7001::901::bill_line::8101")
        case2_iid = next(iid for iid, row in widgets["row_by_iid"].items() if row["row_key"] == "case2::7002::902")

        self.assertEqual(widgets["projected_cycle_title"].cget("text"), "Proyeksi Jurnal Per Item")
        self.assertLess(int(widgets["projected_cycle_title"].grid_info()["row"]), int(widgets["current_cycle_title"].grid_info()["row"]))
        self.assertLess(int(widgets["current_cycle_title"].grid_info()["row"]), int(widgets["simulated_title"].grid_info()["row"]))
        self.assertLess(int(widgets["projected_cycle_title"].grid_info()["row"]), int(widgets["summary_section"].grid_info()["row"]))
        self.assertLess(int(widgets["summary_section"].grid_info()["row"]), int(widgets["advanced_section"].grid_info()["row"]))

        tree.selection_set((case1_iid,))
        tree.focus(case1_iid)
        tree.event_generate("<<TreeviewSelect>>")
        self.root.update_idletasks()

        current_rows = [
            widgets["current_cycle_tree"].item(item_id, "values")
            for item_id in widgets["current_cycle_tree"].get_children()
        ]
        simulated_rows = [
            widgets["simulated_tree"].item(item_id, "values")
            for item_id in widgets["simulated_tree"].get_children()
        ]
        projected_rows = [
            widgets["projected_cycle_tree"].item(item_id, "values")
            for item_id in widgets["projected_cycle_tree"].get_children()
        ]
        projected_by_code = {values[1]: values for values in projected_rows}

        self.assertEqual([values[1] for values in current_rows], ["2103006", "1108099"])
        self.assertEqual([values[1] for values in simulated_rows], ["2103006", "1108099"])
        self.assertIn("Snapshot item aktif", widgets["current_cycle_note_var"].get())
        self.assertIn("Correction SKU-001 - Produk PCB", simulated_rows[0][4])
        self.assertNotIn("2101002", projected_by_code)
        self.assertNotIn("11120003", projected_by_code)
        self.assertEqual(projected_by_code["2103006"][5], "+0.00")
        self.assertEqual(projected_by_code["1108099"][5], "+0.00")

        tree.selection_set((case1_iid, case2_iid))
        tree.focus(case1_iid)
        tree.event_generate("<<TreeviewSelect>>")
        self.root.update_idletasks()

        self.assertEqual(widgets["simulated_note_var"].get(), "Simulasi hanya tersedia untuk 1 row terpilih.")
        self.assertFalse(widgets["current_cycle_tree"].get_children())
        self.assertFalse(widgets["simulated_tree"].get_children())
        self.assertFalse(widgets["projected_cycle_tree"].get_children())
        self.assertEqual(widgets["resolve_picker"].entry.cget("state"), "disabled")
        self.assertEqual(widgets["simulated_line_add_button"].cget("state"), "disabled")

        widgets["close_button"].invoke()
        self.root.update_idletasks()

    def test_pcb_repair_dialog_case1_same_item_selection_supports_partial_and_aggregate_preview(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page.root = self.root
        page._display_name = "Fixing Unlink SVL - Odoo"
        page._last_pcb_case1_dialog_widgets = {}
        page.status_var = tk.StringVar(master=self.root, value="")
        page.context = mock.Mock(global_settings=GlobalSettings(default_output_dir=r"C:\shared\output"))
        page._module_settings = SvlFixJeSettings(last_output_dir=r"C:\exports", max_workers=2)
        page._state_store = mock.Mock()
        page._pcb_repair_collection_scope = None
        page._current_repair_collection_scope = lambda: None
        page._update_pcb_collection_button = lambda: None
        page._latest_snapshot = SvlDashboardSnapshot(
            database="hwgroup_erp",
            company_id=7,
            company_name="Alpha Company",
            generated_at="2026-03-28 10:00:00",
            period="2026-03-01..2026-03-31",
            purchase_cycles=[
                _build_pcb_cycle(
                    picking_id=7001,
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
                            account_id=302,
                            code="1108099",
                            name="Clearing",
                            account_type="asset_current",
                            account_group="asset",
                            debit=100.0,
                            credit=0.0,
                            net_balance=100.0,
                            status="problem",
                        ),
                    ],
                    item_rows=[
                        SvlDashboardCycleItemRow(
                            product_id=901,
                            product_name="Produk PCB",
                            default_code="SKU-001",
                            account_rows=[
                                SvlDashboardCycleAccountRow(
                                    account_id=401,
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
                                    code="1108099",
                                    name="Clearing",
                                    account_type="asset_current",
                                    account_group="asset",
                                    debit=100.0,
                                    credit=0.0,
                                    net_balance=100.0,
                                    status="problem",
                                ),
                            ],
                        )
                    ],
                )
            ],
        )
        page._pcb_repair_collection = OrderedDict(
            {
                "case1::7001::901::bill_line::8101": {
                    "row_key": "case1::7001::901::bill_line::8101",
                    "cycle_key": "case1::7001",
                    "pcb_case": "case1",
                    "company_id": 7,
                    "company_name": "Alpha Company",
                    "amount": 60.0,
                    "date": "2026-03-19",
                    "reference": _pcb_case1_reference(
                        picking_name="LHPK/IN/7001",
                        bill_name="BILL/2026/0001",
                        item_code="SKU-001",
                        item_name="Produk PCB",
                    ),
                    "line_label": _pcb_case1_line_label(item_code="SKU-001", item_name="Produk PCB"),
                    "journal_code": "STJ",
                    "debit_account_code": "2103006",
                    "credit_account_code": "1108099",
                    "source_kind": "bill_line",
                    "source_id": 8101,
                    "source_label": "Bill Line #8101",
                    "product_id": 901,
                    "item_code": "SKU-001",
                    "item_name": "Produk PCB",
                    "picking_id": 7001,
                    "picking_name": "LHPK/IN/7001",
                    "po_name": "PO/2026/0001",
                    "bill_move_id": 8201,
                    "bill_name": "BILL/2026/0001",
                    "partner_id": 77,
                    "partner_name": "Vendor Alpha",
                    "reconcile_readiness_label": "Disabled",
                    "row_status": "ready",
                    "row_status_message": "Ready",
                },
                "case1::7001::901::bill_line::8102": {
                    "row_key": "case1::7001::901::bill_line::8102",
                    "cycle_key": "case1::7001",
                    "pcb_case": "case1",
                    "company_id": 7,
                    "company_name": "Alpha Company",
                    "amount": 40.0,
                    "date": "2026-03-19",
                    "reference": _pcb_case1_reference(
                        picking_name="LHPK/IN/7001",
                        bill_name="BILL/2026/0001",
                        item_code="SKU-001",
                        item_name="Produk PCB",
                    ),
                    "line_label": _pcb_case1_line_label(item_code="SKU-001", item_name="Produk PCB"),
                    "journal_code": "STJ",
                    "debit_account_code": "2103006",
                    "credit_account_code": "1108099",
                    "source_kind": "bill_line",
                    "source_id": 8102,
                    "source_label": "Bill Line #8102",
                    "product_id": 901,
                    "item_code": "SKU-001",
                    "item_name": "Produk PCB",
                    "picking_id": 7001,
                    "picking_name": "LHPK/IN/7001",
                    "po_name": "PO/2026/0001",
                    "bill_move_id": 8201,
                    "bill_name": "BILL/2026/0001",
                    "partner_id": 77,
                    "partner_name": "Vendor Alpha",
                    "reconcile_readiness_label": "Disabled",
                    "row_status": "ready",
                    "row_status_message": "Ready",
                },
            }
        )

        page._open_pcb_case1_repair_dialog_v2()
        self.root.update_idletasks()
        widgets = page._last_pcb_case1_dialog_widgets
        tree = widgets["tree"]
        first_iid = next(iid for iid, row in widgets["row_by_iid"].items() if row["row_key"] == "case1::7001::901::bill_line::8101")
        second_iid = next(iid for iid, row in widgets["row_by_iid"].items() if row["row_key"] == "case1::7001::901::bill_line::8102")

        tree.selection_set((first_iid,))
        tree.focus(first_iid)
        tree.event_generate("<<TreeviewSelect>>")
        self.root.update_idletasks()

        projected_rows = {
            values[1]: values
            for values in (
                widgets["projected_cycle_tree"].item(item_id, "values")
                for item_id in widgets["projected_cycle_tree"].get_children()
            )
        }
        self.assertIn("Preview ini parsial", widgets["detail_info_var"].get())
        self.assertIn("Preview ini parsial", widgets["simulated_note_var"].get())
        self.assertIn("bukan saldo per source line", widgets["current_cycle_note_var"].get())
        self.assertEqual(projected_rows["2103006"][5], "-40.00")
        self.assertEqual(projected_rows["1108099"][5], "+40.00")

        tree.selection_set((first_iid, second_iid))
        tree.focus(first_iid)
        tree.event_generate("<<TreeviewSelect>>")
        self.root.update_idletasks()

        simulated_rows = [
            widgets["simulated_tree"].item(item_id, "values")
            for item_id in widgets["simulated_tree"].get_children()
        ]
        projected_rows = {
            values[1]: values
            for values in (
                widgets["projected_cycle_tree"].item(item_id, "values")
                for item_id in widgets["projected_cycle_tree"].get_children()
            )
        }
        self.assertIn("row source item terpilih", widgets["detail_info_var"].get())
        self.assertIn("preview je gabungan row terpilih", normalize_text(widgets["simulated_note_var"].get()).lower())
        self.assertIn("saldo item agregat", normalize_text(widgets["current_cycle_note_var"].get()).lower())
        self.assertIn("saldo item agregat", normalize_text(widgets["projected_cycle_note_var"].get()).lower())
        self.assertEqual(len(simulated_rows), 4)
        self.assertTrue(any("Bill Line #8101" in values[4] for values in simulated_rows))
        self.assertTrue(any("Bill Line #8102" in values[4] for values in simulated_rows))
        self.assertEqual(projected_rows["2103006"][5], "+0.00")
        self.assertEqual(projected_rows["1108099"][5], "+0.00")
        self.assertEqual(widgets["simulated_line_add_button"].cget("state"), "disabled")

        widgets["close_button"].invoke()
        self.root.update_idletasks()

    def test_pcb_repair_runner_executes_case1_and_case2_rows_in_one_batch(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page.root = self.root
        page._display_name = "Fixing Unlink SVL - Odoo"
        page.context = mock.Mock(global_settings=GlobalSettings(default_output_dir=r"C:\shared\output"))
        page.logger = logging.getLogger("test.pcb.repair.mixed")
        page._module_settings = SvlFixJeSettings(last_output_dir=r"C:\exports", max_workers=2)
        page._selected_database_profile_id = lambda: "db_live"
        page._runtime_builder = lambda *_args, **_kwargs: (  # noqa: ARG005
            object(),
            mock.Mock(base_url="https://odoo.test", database="hwgroup_erp"),
        )
        page._save_pcb_case1_last_date = lambda _value: None
        page._pcb_repair_collection = OrderedDict(
            {
                "case1::7001::901::bill_line::8101": {
                    "row_key": "case1::7001::901::bill_line::8101",
                    "cycle_key": "case1::7001",
                    "pcb_case": "case1",
                    "company_id": 7,
                    "company_name": "Alpha Company",
                    "amount": 120.0,
                    "date": "2026-03-19",
                    "reference": _pcb_case1_reference(
                        picking_name="LHPK/IN/7001",
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
                    "picking_id": 7001,
                    "picking_name": "LHPK/IN/7001",
                    "po_name": "PO/2026/0001",
                    "bill_move_id": 8201,
                    "bill_name": "BILL/2026/0001",
                    "partner_id": 77,
                    "partner_name": "Vendor Alpha",
                    "row_status": "ready",
                    "row_status_message": "Ready",
                    "result_status": "",
                    "result_posted": False,
                    "result_error_kind": "",
                    "result_move_id": 0,
                    "result_move_name": "",
                    "existing_move_detected": False,
                    "reconcile_attempted": False,
                    "reconcile_performed": False,
                    "reconcile_skipped": False,
                    "reconcile_message": "",
                    "reconcile_error_kind": "",
                },
                "case2::7001::901": {
                    "row_key": "case2::7001::901",
                    "cycle_key": "case2::7001",
                    "pcb_case": "case2",
                    "company_id": 7,
                    "company_name": "Alpha Company",
                    "amount": 100.0,
                    "date": "2026-03-19",
                    "reference": _pcb_case1_reference(
                        picking_name="LHPK/IN/7001",
                        bill_name="BILL/2026/0001",
                        item_code="SKU-001",
                        item_name="Produk PCB",
                    ),
                    "line_label": "SKU-001 - Produk PCB - Case 2 - STJ Bill Price Diff (Suspend - Suspend)",
                    "journal_code": "STJ",
                    "product_id": 901,
                    "item_code": "SKU-001",
                    "item_name": "Produk PCB",
                    "picking_id": 7001,
                    "picking_name": "LHPK/IN/7001",
                    "po_name": "PO/2026/0001",
                    "bill_move_id": 8201,
                    "bill_name": "BILL/2026/0001",
                    "partner_id": 77,
                    "partner_name": "Vendor Alpha",
                    "expense_account_code": "510001",
                    "planned_lines": [
                        {"role": "suspend", "account_code": "2103006", "amount": 100.0, "side": "debit"},
                        {"role": "inventory", "account_code": "1105004", "amount": 70.0, "side": "credit"},
                        {"role": "expense", "account_code": "5101004", "amount": 30.0, "side": "credit"},
                    ],
                    "row_status": "ready",
                    "row_status_message": "Ready",
                    "result_status": "",
                    "result_posted": False,
                    "result_error_kind": "",
                    "result_move_id": 0,
                    "result_move_name": "",
                    "existing_move_detected": False,
                },
            }
        )
        page.ui_queue = _FakeQueue()
        page.log_queue = _FakeQueue()

        fake_service = mock.Mock()
        fake_service.execute_pcb_case1 = mock.AsyncMock(
            return_value=SvlDashboardPcbCase1RepairBatchResult(
                database="hwgroup_erp",
                results=[
                    SvlDashboardPcbCase1RepairRowResult(
                        row_key="case1::7001::901::bill_line::8101",
                        status="POSTED",
                        message="JE STJ/2026/0901 dibuat dan dipost.",
                        move_id=901,
                        move_name="STJ/2026/0901",
                        posted=True,
                    )
                ],
                created_count=1,
                posted_count=1,
                existing_count=0,
                error_count=0,
            )
        )
        fake_service.execute_pcb_case2 = mock.AsyncMock(
            return_value=SvlDashboardPcbCase2RepairBatchResult(
                database="hwgroup_erp",
                results=[
                    SvlDashboardPcbCase2RepairRowResult(
                        row_key="case2::7001::901",
                        status="CREATED",
                        message="JE STJ/2026/0902 dibuat.",
                        move_id=902,
                        move_name="STJ/2026/0902",
                        posted=False,
                    )
                ],
                created_count=1,
                posted_count=0,
                existing_count=0,
                error_count=0,
            )
        )

        class _FakeAsyncClient:
            def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
                pass

            async def __aenter__(self):
                return object()

            async def __aexit__(self, exc_type, exc, tb):  # noqa: ANN001
                return False

        class _ImmediateThread:
            def __init__(self, *, target, **kwargs) -> None:  # noqa: ANN003
                self._target = target

            def start(self) -> None:
                self._target()

        with mock.patch(
            "smartscc_tools.modules.svl_fix_je_dashboard_page.AsyncOdooJsonRpcClient",
            _FakeAsyncClient,
        ), mock.patch(
            "smartscc_tools.modules.svl_fix_je_dashboard_page.SvlDashboardRepairServiceAsync",
            return_value=fake_service,
        ), mock.patch(
            "smartscc_tools.modules.svl_fix_je_dashboard_page.threading.Thread",
            side_effect=lambda **kwargs: _ImmediateThread(**kwargs),
        ):
            page._run_pcb_case1_repair_collection_v2(list(page._pcb_repair_collection.values()), post=True)

        fake_service.execute_pcb_case1.assert_called_once()
        fake_service.execute_pcb_case2.assert_called_once()
        self.assertEqual(page._pcb_repair_collection["case1::7001::901::bill_line::8101"]["row_status"], "repaired")
        self.assertEqual(page._pcb_repair_collection["case2::7001::901"]["row_status"], "repaired")
        self.assertEqual(page._pcb_repair_collection["case2::7001::901"]["result_move_name"], "STJ/2026/0902")
        done_event = page.ui_queue.get_nowait()
        self.assertEqual(done_event["type"], "_pcb_repair_done")
        self.assertIn("2 JE dibuat", done_event["msg"])
        self.assertIn("1 posted", done_event["msg"])
        self.assertIn("Auto reconcile PCB Case 1 disabled.", done_event["msg"])

    def test_pcb_repair_runner_routes_case3_and_case4_rows_to_multiline_executor(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page.root = self.root
        page._display_name = "Fixing Unlink SVL - Odoo"
        page.context = mock.Mock(global_settings=GlobalSettings(default_output_dir=r"C:\shared\output"))
        page.logger = logging.getLogger("test.pcb.repair.case34")
        page._module_settings = SvlFixJeSettings(last_output_dir=r"C:\exports", max_workers=2)
        page._selected_database_profile_id = lambda: "db_live"
        page._runtime_builder = lambda *_args, **_kwargs: (  # noqa: ARG005
            object(),
            mock.Mock(base_url="https://odoo.test", database="hwgroup_erp"),
        )
        page._save_pcb_case1_last_date = lambda _value: None
        page._pcb_repair_collection = OrderedDict(
            {
                "case3::7003::901": {
                    "row_key": "case3::7003::901",
                    "cycle_key": "case3::7003",
                    "pcb_case": "case3",
                    "pcb_case_label": PCB_CASE3_LABEL,
                    "company_id": 7,
                    "company_name": "Alpha Company",
                    "amount": 50.0,
                    "date": "2026-03-19",
                    "reference": _pcb_case1_reference(
                        picking_name="LHPK/IN/7003",
                        bill_name="BILL/2026/0003",
                        item_code="SKU-001",
                        item_name="Produk PCB",
                    ),
                    "line_label": "SKU-001 - Produk PCB - Case 3 - STJ Bill Hit Expenses (Clearing - Expenses)",
                    "journal_code": "STJ",
                    "product_id": 901,
                    "item_code": "SKU-001",
                    "item_name": "Produk PCB",
                    "picking_id": 7003,
                    "picking_name": "LHPK/IN/7003",
                    "po_name": "PO/2026/0003",
                    "bill_move_id": 8203,
                    "bill_name": "BILL/2026/0003",
                    "partner_id": 77,
                    "partner_name": "Vendor Alpha",
                    "planned_lines": [
                        {"role": "problem_1108099", "account_code": "1108099", "amount": 70.0, "side": "credit"},
                        {"role": "problem_2103006", "account_code": "2103006", "amount": 120.0, "side": "debit"},
                        {"role": "selisih_hpp", "account_code": "5101003", "amount": 50.0, "side": "credit"},
                    ],
                    "row_status": "ready",
                    "row_status_message": "Ready",
                },
                "case4::7004::902": {
                    "row_key": "case4::7004::902",
                    "cycle_key": "case4::7004",
                    "pcb_case": "case4",
                    "pcb_case_label": PCB_CASE4_LABEL,
                    "company_id": 7,
                    "company_name": "Alpha Company",
                    "amount": 100.0,
                    "date": "2026-03-19",
                    "reference": _pcb_case1_reference(
                        picking_name="LHPK/IN/7004",
                        bill_name="BILL/2026/0004",
                        item_code="SKU-002",
                        item_name="Produk PCB 2",
                    ),
                    "line_label": "SKU-002 - Produk PCB 2 - Case 4 - STJ Bill Hit Expenses (Suspend - Expenses)",
                    "journal_code": "STJ",
                    "product_id": 902,
                    "item_code": "SKU-002",
                    "item_name": "Produk PCB 2",
                    "picking_id": 7004,
                    "picking_name": "LHPK/IN/7004",
                    "po_name": "PO/2026/0004",
                    "bill_move_id": 8204,
                    "bill_name": "BILL/2026/0004",
                    "partner_id": 77,
                    "partner_name": "Vendor Alpha",
                    "planned_lines": [
                        {"role": "problem_2103006", "account_code": "2103006", "amount": 100.0, "side": "debit"},
                        {"role": "selisih_hpp", "account_code": "5101003", "amount": 100.0, "side": "credit"},
                    ],
                    "row_status": "ready",
                    "row_status_message": "Ready",
                },
            }
        )
        page.ui_queue = _FakeQueue()
        page.log_queue = _FakeQueue()

        fake_service = mock.Mock()
        fake_service.execute_pcb_case1 = mock.AsyncMock(
            return_value=SvlDashboardPcbCase1RepairBatchResult(database="hwgroup_erp")
        )
        fake_service.execute_pcb_case2 = mock.AsyncMock(
            return_value=SvlDashboardPcbCase2RepairBatchResult(
                database="hwgroup_erp",
                results=[
                    SvlDashboardPcbCase2RepairRowResult(
                        row_key="case3::7003::901",
                        status="CREATED",
                        message="JE STJ/2026/0903 dibuat.",
                        move_id=903,
                        move_name="STJ/2026/0903",
                        posted=False,
                    ),
                    SvlDashboardPcbCase2RepairRowResult(
                        row_key="case4::7004::902",
                        status="CREATED",
                        message="JE STJ/2026/0904 dibuat.",
                        move_id=904,
                        move_name="STJ/2026/0904",
                        posted=False,
                    ),
                ],
                created_count=2,
                posted_count=0,
                existing_count=0,
                error_count=0,
            )
        )

        class _FakeAsyncClient:
            def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
                pass

            async def __aenter__(self):
                return object()

            async def __aexit__(self, exc_type, exc, tb):  # noqa: ANN001
                return False

        class _ImmediateThread:
            def __init__(self, *, target, **kwargs) -> None:  # noqa: ANN003
                self._target = target

            def start(self) -> None:
                self._target()

        with mock.patch(
            "smartscc_tools.modules.svl_fix_je_dashboard_page.AsyncOdooJsonRpcClient",
            _FakeAsyncClient,
        ), mock.patch(
            "smartscc_tools.modules.svl_fix_je_dashboard_page.SvlDashboardRepairServiceAsync",
            return_value=fake_service,
        ), mock.patch(
            "smartscc_tools.modules.svl_fix_je_dashboard_page.threading.Thread",
            side_effect=lambda **kwargs: _ImmediateThread(**kwargs),
        ):
            page._run_pcb_case1_repair_collection_v2(list(page._pcb_repair_collection.values()), post=False)

        fake_service.execute_pcb_case1.assert_not_called()
        fake_service.execute_pcb_case2.assert_called_once()
        request = fake_service.execute_pcb_case2.call_args.args[0]
        self.assertEqual([row.pcb_case for row in request.rows], ["case3", "case4"])
        self.assertEqual(page._pcb_repair_collection["case3::7003::901"]["row_status"], "repaired")
        self.assertEqual(page._pcb_repair_collection["case4::7004::902"]["row_status"], "repaired")
        done_event = page.ui_queue.get_nowait()
        self.assertEqual(done_event["type"], "_pcb_repair_done")
        self.assertIn("2 JE dibuat", done_event["msg"])

    def test_pcb_repair_runner_preserves_normalized_simulation_lines_in_execute_request(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page.root = self.root
        page._display_name = "Fixing Unlink SVL - Odoo"
        page.context = mock.Mock(global_settings=GlobalSettings(default_output_dir=r"C:\shared\output"))
        page.logger = logging.getLogger("test.pcb.repair.parity")
        page._module_settings = SvlFixJeSettings(last_output_dir=r"C:\exports", max_workers=2)
        page._selected_database_profile_id = lambda: "db_live"
        page._runtime_builder = lambda *_args, **_kwargs: (  # noqa: ARG005
            object(),
            mock.Mock(base_url="https://odoo.test", database="hwgroup_erp"),
        )
        page._save_pcb_case1_last_date = lambda _value: None
        page.ui_queue = _FakeQueue()
        page.log_queue = _FakeQueue()
        page._pcb_repair_collection = OrderedDict(
            {
                "case2::7001::901": {
                    "row_key": "case2::7001::901",
                    "cycle_key": "case2::7001",
                    "pcb_case": "case2",
                    "pcb_case_label": PCB_CASE2_LABEL,
                    "company_id": 7,
                    "company_name": "Alpha Company",
                    "amount": 11008000.0,
                    "date": "2026-03-19",
                    "reference": _pcb_case1_reference(
                        picking_name="CBG/IN/00889",
                        bill_name="BILL/2026/0001",
                        item_code="A-BESJ-0001",
                        item_name="JINRO CHAMISUL ORI FRESH",
                    ),
                    "line_label": "A-BESJ-0001 - JINRO CHAMISUL ORI FRESH",
                    "journal_code": "STJ",
                    "product_id": 901,
                    "item_code": "A-BESJ-0001",
                    "item_name": "JINRO CHAMISUL ORI FRESH",
                    "picking_id": 7001,
                    "picking_name": "CBG/IN/00889",
                    "po_name": "PO/CB/2026/0001",
                    "bill_move_id": 8201,
                    "bill_name": "BILL/2026/0001",
                    "partner_id": 77,
                    "partner_name": "Vendor Alpha",
                    "expense_account_code": "510001",
                    "planned_lines": [
                        {"role": "problem_1108099", "account_code": "1108099", "amount": 11008000.0, "side": "debit", "line_label": "A-BESJ-0001 - JINRO CHAMISUL ORI FRESH"},
                        {"role": "problem_2103006", "account_code": "2103006", "amount": 11000000.0, "side": "credit", "line_label": "A-BESJ-0001 - JINRO CHAMISUL ORI FRESH"},
                        {"role": "selisih_hpp", "account_code": "510001", "amount": 8000.0, "side": "credit", "line_label": "A-BESJ-0001 - JINRO CHAMISUL ORI FRESH - Selisih HPP"},
                    ],
                    "row_status": "ready",
                    "row_status_message": "Ready",
                }
            }
        )
        row = page._pcb_repair_collection["case2::7001::901"]
        page._sync_pcb_case2_row_defaults(row, preserve_terminal=True)

        fake_service = mock.Mock()
        fake_service.execute_pcb_case1 = mock.AsyncMock(return_value=SvlDashboardPcbCase1RepairBatchResult(database="hwgroup_erp"))
        fake_service.execute_pcb_case2 = mock.AsyncMock(
            return_value=SvlDashboardPcbCase2RepairBatchResult(
                database="hwgroup_erp",
                results=[
                    SvlDashboardPcbCase2RepairRowResult(
                        row_key="case2::7001::901",
                        status="CREATED",
                        message="JE STJ/2026/0902 dibuat.",
                        move_id=902,
                        move_name="STJ/2026/0902",
                        posted=False,
                    )
                ],
                created_count=1,
                posted_count=0,
                existing_count=0,
                error_count=0,
            )
        )

        class _FakeAsyncClient:
            def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
                pass

            async def __aenter__(self):
                return object()

            async def __aexit__(self, exc_type, exc, tb):  # noqa: ANN001
                return False

        class _ImmediateThread:
            def __init__(self, *, target, **kwargs) -> None:  # noqa: ANN003
                self._target = target

            def start(self) -> None:
                self._target()

        with mock.patch(
            "smartscc_tools.modules.svl_fix_je_dashboard_page.AsyncOdooJsonRpcClient",
            _FakeAsyncClient,
        ), mock.patch(
            "smartscc_tools.modules.svl_fix_je_dashboard_page.SvlDashboardRepairServiceAsync",
            return_value=fake_service,
        ), mock.patch(
            "smartscc_tools.modules.svl_fix_je_dashboard_page.threading.Thread",
            side_effect=lambda **kwargs: _ImmediateThread(**kwargs),
        ):
            page._run_pcb_case1_repair_collection_v2([row], post=False)

        request = fake_service.execute_pcb_case2.call_args.args[0]
        planned_lines = request.rows[0].planned_lines
        self.assertEqual(
            [(line["account_code"], line["side"], line["amount"], line["line_label"]) for line in planned_lines],
            [
                ("1108099", "debit", 11008000.0, "A-BESJ-0001 - JINRO CHAMISUL ORI FRESH"),
                ("2103006", "credit", 11000000.0, "A-BESJ-0001 - JINRO CHAMISUL ORI FRESH"),
                ("510001", "credit", 8000.0, "Selisih HPP - A-BESJ-0001 - JINRO CHAMISUL ORI FRESH"),
            ],
        )

    def test_pcb_repair_runner_keeps_manual_selisih_hpp_label_in_execute_request(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page.root = self.root
        page._display_name = "Fixing Unlink SVL - Odoo"
        page.context = mock.Mock(global_settings=GlobalSettings(default_output_dir=r"C:\shared\output"))
        page.logger = logging.getLogger("test.pcb.repair.manual-label")
        page._module_settings = SvlFixJeSettings(last_output_dir=r"C:\exports", max_workers=2)
        page._selected_database_profile_id = lambda: "db_live"
        page._runtime_builder = lambda *_args, **_kwargs: (  # noqa: ARG005
            object(),
            mock.Mock(base_url="https://odoo.test", database="hwgroup_erp"),
        )
        page._save_pcb_case1_last_date = lambda _value: None
        page.ui_queue = _FakeQueue()
        page.log_queue = _FakeQueue()
        page._pcb_repair_collection = OrderedDict(
            {
                "case2::7002::901": {
                    "row_key": "case2::7002::901",
                    "cycle_key": "case2::7002",
                    "pcb_case": "case2",
                    "pcb_case_label": PCB_CASE2_LABEL,
                    "company_id": 7,
                    "company_name": "Alpha Company",
                    "amount": 100.0,
                    "date": "2026-03-19",
                    "reference": _pcb_case1_reference(
                        picking_name="LHPK/IN/7002",
                        bill_name="BILL/2026/0002",
                        item_code="SKU-001",
                        item_name="Produk PCB",
                    ),
                    "line_label": "SKU-001 - Produk PCB - Case 2 - STJ Bill Price Diff (Suspend - Suspend)",
                    "journal_code": "STJ",
                    "product_id": 901,
                    "item_code": "SKU-001",
                    "item_name": "Produk PCB",
                    "picking_id": 7002,
                    "picking_name": "LHPK/IN/7002",
                    "po_name": "PO/2026/0002",
                    "bill_move_id": 8202,
                    "bill_name": "BILL/2026/0002",
                    "partner_id": 77,
                    "partner_name": "Vendor Alpha",
                    "expense_account_code": "510001",
                    "planned_lines": [
                        {"role": "problem_2103006", "account_code": "2103006", "amount": 100.0, "side": "debit"},
                        {"role": "selisih_hpp", "account_code": "510001", "amount": 100.0, "side": "credit", "line_label": "Manual Selisih Custom", "manual_line": True},
                    ],
                    "row_status": "ready",
                    "row_status_message": "Ready",
                }
            }
        )
        row = page._pcb_repair_collection["case2::7002::901"]
        page._sync_pcb_case2_row_defaults(row, preserve_terminal=True)
        self.assertEqual(row["planned_lines"][1]["line_label"], "Manual Selisih Custom")

        fake_service = mock.Mock()
        fake_service.execute_pcb_case1 = mock.AsyncMock(return_value=SvlDashboardPcbCase1RepairBatchResult(database="hwgroup_erp"))
        fake_service.execute_pcb_case2 = mock.AsyncMock(return_value=SvlDashboardPcbCase2RepairBatchResult(database="hwgroup_erp"))

        class _FakeAsyncClient:
            def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
                pass

            async def __aenter__(self):
                return object()

            async def __aexit__(self, exc_type, exc, tb):  # noqa: ANN001
                return False

        class _ImmediateThread:
            def __init__(self, *, target, **kwargs) -> None:  # noqa: ANN003
                self._target = target

            def start(self) -> None:
                self._target()

        with mock.patch(
            "smartscc_tools.modules.svl_fix_je_dashboard_page.AsyncOdooJsonRpcClient",
            _FakeAsyncClient,
        ), mock.patch(
            "smartscc_tools.modules.svl_fix_je_dashboard_page.SvlDashboardRepairServiceAsync",
            return_value=fake_service,
        ), mock.patch(
            "smartscc_tools.modules.svl_fix_je_dashboard_page.threading.Thread",
            side_effect=lambda **kwargs: _ImmediateThread(**kwargs),
        ):
            page._run_pcb_case1_repair_collection_v2([row], post=False)

        request = fake_service.execute_pcb_case2.call_args.args[0]
        self.assertEqual(request.rows[0].planned_lines[1]["line_label"], "Manual Selisih Custom")

    def test_dashboard_export_snapshot_hydrates_full_snapshot_before_writing_file(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page.context = mock.Mock(global_settings=GlobalSettings(default_output_dir=r"C:\exports"))
        page.logger = logging.getLogger("test.dashboard.export")
        page._module_settings = SvlFixJeSettings(last_output_dir=r"C:\exports")
        page._latest_snapshot = mock.Mock(company_id=7)
        page._latest_analysis_request = SvlDashboardRequest(
            database="hwgroup_erp",
            company_id=7,
            date_from="2026-01-01",
            date_to="2026-01-31",
        )
        page._latest_analysis_profile_id = "db_live"
        page._selected_database_profile_id = lambda: "db_live"
        page._runtime_builder = lambda *_args, **_kwargs: (  # noqa: ARG005
            object(),
            mock.Mock(base_url="https://odoo.test", database="hwgroup_erp"),
        )
        page.ui_queue = _FakeQueue()
        page.log_queue = _FakeQueue()
        page.phase_var = _FakeVar("")
        page.status_var = _FakeVar("")
        busy_calls: list[bool] = []
        page._set_busy = lambda busy: busy_calls.append(bool(busy))

        hydrated_snapshot = mock.Mock(company_id=7)
        fake_service = mock.Mock()
        fake_service.hydrate_snapshot_details = mock.AsyncMock(return_value=hydrated_snapshot)

        class _FakeAsyncClient:
            def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
                pass

            async def __aenter__(self):
                return object()

            async def __aexit__(self, exc_type, exc, tb):  # noqa: ANN001
                return False

        class _ImmediateThread:
            def __init__(self, *, target, **kwargs) -> None:  # noqa: ANN003
                self._target = target

            def start(self) -> None:
                self._target()

        selected_path = str(Path(tempfile.gettempdir()) / "dashboard.json")
        with mock.patch(
            "smartscc_tools.modules.svl_fix_je_dashboard_page.filedialog.asksaveasfilename",
            return_value=selected_path,
        ), mock.patch(
            "smartscc_tools.modules.svl_fix_je_dashboard_page.AsyncOdooJsonRpcClient",
            _FakeAsyncClient,
        ), mock.patch(
            "smartscc_tools.modules.svl_fix_je_dashboard_page.SvlDashboardServiceAsync",
            return_value=fake_service,
        ), mock.patch(
            "smartscc_tools.modules.svl_fix_je_dashboard_page.export_dashboard_json",
            side_effect=lambda snapshot, output_path: Path(output_path),
        ) as export_json, mock.patch(
            "smartscc_tools.modules.svl_fix_je_dashboard_page.threading.Thread",
            side_effect=lambda **kwargs: _ImmediateThread(**kwargs),
        ):
            page._export_snapshot("json")

        self.assertEqual(busy_calls, [True])
        fake_service.hydrate_snapshot_details.assert_awaited_once()
        hydrated_request, hydrated_source_snapshot = fake_service.hydrate_snapshot_details.await_args.args
        self.assertEqual(hydrated_request.database, "hwgroup_erp")
        self.assertEqual(hydrated_request.company_id, 7)
        self.assertEqual(hydrated_source_snapshot, page._latest_snapshot)
        self.assertIs(export_json.call_args.args[0], hydrated_snapshot)
        self.assertEqual(export_json.call_args.args[1], selected_path)

    def test_build_card_can_expand_both_axes(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)

        body = page._build_card(self.root, title="SVL vs Balance Sheet", fill="both", expand=True)
        card = body.master.master
        self.root.update_idletasks()

        self.assertEqual(card.pack_info()["fill"], "both")
        self.assertEqual(str(card.pack_info()["expand"]), "1")
        self.assertEqual(body.pack_info()["fill"], "both")
        self.assertEqual(str(body.pack_info()["expand"]), "1")

    def test_shell_header_shows_repair_collection_controls_and_summary(self) -> None:
        parent = tk.Frame(self.root, bg=T.BG_MAIN)
        parent.pack(fill="both", expand=True)
        context = ModuleContext(
            global_settings=GlobalSettings(
                module_settings={
                    "svl_fix_je": {
                        "view_mode": "dashboard",
                    }
                }
            ),
            auth_info=None,
            logger=logging.getLogger("test.svl.fix.shell.header"),
            technical_logger=logging.getLogger("test.svl.fix.shell.header.tech"),
            root=self.root,
        )

        class _FakeUploadPage:
            def __init__(self, parent, context, **kwargs):  # noqa: ANN001
                self.parent = parent

            def refresh_database_options(self) -> None:
                return None

            def shutdown(self) -> None:
                return None

        class _FakeDashboardPage:
            def __init__(self, parent, context, **kwargs):  # noqa: ANN001
                self.parent = parent
                self.collection_callback = None
                self.ui_state = {
                    "summary_text": "2 record | Total 500.00",
                    "can_open": True,
                    "can_clear": True,
                    "has_rows": True,
                    "scope_matches_current": True,
                }

            def set_repair_collection_changed_callback(self, callback) -> None:  # noqa: ANN001
                self.collection_callback = callback

            def get_repair_collection_ui_state(self) -> dict[str, object]:
                return dict(self.ui_state)

            def get_pcb_collection_ui_state(self) -> dict[str, object]:
                return {"is_pcb_mode": False, "can_open": False}

            def on_page_activated(self) -> None:
                return None

            def refresh_database_options(self) -> None:
                return None

            def shutdown(self) -> None:
                return None

            def open_repair_collection_dialog(self) -> None:
                return None

            def clear_repair_collection(self) -> None:
                return None

        with mock.patch("smartscc_tools.modules.svl_fix_je_module._SvlFixJePanel", _FakeUploadPage), mock.patch(
            "smartscc_tools.modules.svl_fix_je_module._load_svl_fix_je_dashboard_page_class",
            return_value=_FakeDashboardPage,
        ):
            shell = _SvlFixJeShell(parent, context)

        self.root.update_idletasks()

        self.assertEqual(shell.btn_repair_collection.cget("text"), "Repair Collection")
        self.assertEqual(shell.btn_clear_repair_collection.cget("text"), "Clear Collection")
        self.assertEqual(shell.repair_collection_summary_var.get(), "2 record | Total 500.00")
        self.assertEqual(str(shell.btn_repair_collection.cget("state")), "normal")
        self.assertEqual(str(shell.btn_clear_repair_collection.cget("state")), "normal")

    def test_upload_panel_uses_legacy_pack_progress_row(self) -> None:
        source = inspect.getsource(_SvlFixJePanel._build_ui)

        self.assertNotIn("build_stable_progress_row", source)
        self.assertIn("self.progress_row = tk.Frame(action_card, bg=T.BG_CARD)", source)
        self.assertIn('text="Phase:"', source)

    def test_dashboard_progress_row_widths_keep_80_20_target_and_minimum_info(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)

        self.assertEqual(page._dashboard_progress_row_widths(1000), (800, 200))
        self.assertEqual(page._dashboard_progress_row_widths(500), (360, 140))
        self.assertEqual(page._dashboard_progress_row_widths(360), (240, 120))

    def test_repair_progress_row_widths_keep_65_35_target_and_wider_info(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)

        self.assertEqual(page._repair_progress_row_widths(1000), (650, 350))
        self.assertEqual(page._repair_progress_row_widths(500), (280, 220))
        self.assertEqual(page._repair_progress_row_widths(360), (240, 120))

    def test_repair_progress_measurement_prefers_actual_repair_local_width(self) -> None:
        row = _FakeSizedWidget(width=480, reqwidth=1300)
        footer = _FakeSizedWidget(width=520, reqwidth=1400)
        right_host = _FakeSizedWidget(width=560, reqwidth=1500)

        self.assertEqual(SvlFixJeDashboardPage._priority_widget_width(row, footer, right_host), 480)

    def test_repair_progress_measurement_falls_back_in_priority_order(self) -> None:
        row = _FakeSizedWidget(width=0, reqwidth=0)
        footer = _FakeSizedWidget(width=0, reqwidth=540)
        right_host = _FakeSizedWidget(width=0, reqwidth=680)

        self.assertEqual(SvlFixJeDashboardPage._priority_widget_width(row, footer, right_host), 540)

    def test_bind_progress_row_layout_uses_repair_local_measurement_callback(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page.root = _FakeRoot()
        right_host = _FakeLayoutWidget(width=560, reqwidth=1600)
        footer = _FakeLayoutWidget(width=520, reqwidth=1400)
        row = _FakeLayoutWidget(width=480, reqwidth=1300, master=footer)
        progress_host = _FakeLayoutWidget()
        progress_bar = _FakeLayoutWidget()
        info_host = _FakeLayoutWidget()
        flexible_label = _FakeLayoutWidget()
        count_label = _FakeLayoutWidget(width=60, reqwidth=60)
        percent_label = _FakeLayoutWidget(width=40, reqwidth=40)

        refresh = page._bind_progress_row_layout(
            row,
            progress_host,
            progress_bar,
            info_host,
            flexible_label,
            count_label,
            percent_label,
            width_resolver=page._repair_progress_row_widths,
            left_weight=13,
            right_weight=7,
            measure_width=lambda: page._priority_widget_width(row, footer, right_host),
            watch_widgets=(right_host,),
        )

        refresh()

        self.assertEqual(row.grid_columnconfigure(0)["minsize"], 260)
        self.assertEqual(row.grid_columnconfigure(1)["minsize"], 220)
        self.assertEqual(progress_bar.last_config["length"], 260)
        self.assertEqual(info_host.last_config["width"], 220)
        self.assertEqual(flexible_label.last_config["wraplength"], 120)
        self.assertEqual(len(page.root.after_calls), 1)
        self.assertEqual(len(right_host.bind_calls), 1)

    def test_dashboard_page_uses_dynamic_progress_hosts(self) -> None:
        parent = tk.Frame(self.root)
        parent.pack(fill="both", expand=True)
        context = mock.Mock(
            global_settings=GlobalSettings(),
            logger=logging.getLogger("test.svl_fix.adapter.dashboard"),
        )
        state_store = mock.Mock()
        state_store.load.return_value = SvlFixJeSettings()
        state_store.save = mock.Mock()

        page = SvlFixJeDashboardPage(
            parent,
            context,
            state_store=state_store,
            runtime_builder=lambda *_args, **_kwargs: (None, None),
            display_name="Fixing Unlink SVL - Odoo",
        )
        self.root.update_idletasks()

        self.assertIs(page.progress_bar_host.master, page.progress_row)
        self.assertEqual(page.progress_bar_host.winfo_manager(), "grid")
        self.assertEqual(int(page.progress_bar_host.grid_info()["column"]), 0)
        self.assertEqual(int(page.progress_row.grid_columnconfigure(0)["weight"]), 4)
        self.assertGreaterEqual(int(page.progress_row.grid_columnconfigure(1)["minsize"]), 140)
        self.assertIs(page.progress_bar.master, page.progress_bar_host)
        self.assertEqual(page.progress_bar.winfo_manager(), "pack")
        self.assertGreaterEqual(int(page.progress_bar.cget("length")), 320)
        self.assertIs(page.progress_info_host.master, page.progress_row)
        self.assertEqual(page.progress_info_host.winfo_manager(), "grid")
        self.assertEqual(int(page.progress_info_host.grid_info()["column"]), 1)
        self.assertIs(page.progress_percent_label.master, page.progress_info_host)
        self.assertEqual(page.progress_percent_label.winfo_manager(), "grid")
        self.assertEqual(int(page.progress_percent_label.grid_info()["column"]), 0)
        self.assertIs(page.progress_message_label.master, page.progress_info_host)
        self.assertEqual(page.progress_message_label.winfo_manager(), "grid")
        self.assertEqual(int(page.progress_message_label.grid_info()["column"]), 1)
        self.assertGreaterEqual(int(page.progress_message_label.cget("wraplength")), 120)

        page.shutdown()

    def test_build_tree_adds_vertical_and_horizontal_scrollbars(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        frame = tk.Frame(self.root)
        frame.pack(fill="both", expand=True)

        tree = page._build_tree(frame, ("id", "reference", "journal"))
        self.root.update_idletasks()

        self.assertTrue(tree.cget("xscrollcommand"))
        self.assertTrue(tree.cget("yscrollcommand"))
        scrollbars = [child for child in frame.winfo_children() if isinstance(child, tk.Scrollbar) or child.winfo_class() == "TScrollbar"]
        self.assertEqual(len(scrollbars), 2)

    def test_sidebar_category_group_defaults_to_open_without_shared_collapsible_section(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page._sidebar_category_open = {}
        page._selected_product_id = 1
        page._sidebar_item_frames = {}
        page._select_item = lambda _pid: None

        group = {
            "title": "Raw Material",
            "count": 1,
            "total_diff": 10.0,
            "items": [
                mock.Mock(
                    pid=1,
                    code="RM-001",
                    name="Raw Material A",
                    difference=10.0,
                    svl_orphan_count=1,
                    jnl_orphan_count=0,
                    po_lines=[],
                )
            ],
        }
        frame = tk.Frame(self.root)
        frame.pack(fill="both", expand=True)

        section = page._build_sidebar_category_group(frame, group)
        self.root.update_idletasks()

        self.assertNotIsInstance(section, CollapsibleSection)
        self.assertEqual(section._sidebar_toggle_button.cget("text"), "Hide")
        self.assertEqual(str(section._sidebar_body.winfo_manager()), "pack")

    def test_sidebar_category_group_toggle_hides_and_shows_items(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page._sidebar_category_open = {}
        page._selected_product_id = 1
        page._sidebar_item_frames = {}
        page._select_item = lambda _pid: None

        group = {
            "title": "Raw Material",
            "count": 1,
            "total_diff": 10.0,
            "items": [
                mock.Mock(
                    pid=1,
                    code="RM-001",
                    name="Raw Material A",
                    difference=10.0,
                    svl_orphan_count=1,
                    jnl_orphan_count=0,
                    po_lines=[],
                )
            ],
        }
        frame = tk.Frame(self.root)
        frame.pack(fill="both", expand=True)
        section = page._build_sidebar_category_group(frame, group)
        self.root.update_idletasks()

        section._sidebar_toggle_button.invoke()
        self.root.update_idletasks()
        self.assertEqual(section._sidebar_toggle_button.cget("text"), "Show More")
        self.assertEqual(section._sidebar_summary_var.get(), "1 item | Total 10.00")
        self.assertEqual(section._sidebar_body.winfo_manager(), "")

        section._sidebar_toggle_button.invoke()
        self.root.update_idletasks()
        self.assertEqual(section._sidebar_toggle_button.cget("text"), "Hide")
        self.assertEqual(str(section._sidebar_body.winfo_manager()), "pack")

    def test_sidebar_category_group_wraps_long_title_and_keeps_summary_visible_when_collapsed(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page._sidebar_category_open = {}
        page._selected_product_id = 1
        page._sidebar_item_frames = {}
        page._select_item = lambda _pid: None

        group = {
            "title": "MARKETING / MARKETING TOOLS / OPERASIONAL MARKETING PANJANG SEKALI",
            "count": 2,
            "total_diff": 12345.67,
            "items": [
                mock.Mock(
                    pid=1,
                    code="RM-001",
                    name="Raw Material A",
                    difference=10.0,
                    svl_orphan_count=1,
                    jnl_orphan_count=0,
                    po_lines=[],
                )
            ],
        }
        frame = tk.Frame(self.root, width=260, bg=T.BG_CARD)
        frame.pack(fill="both", expand=True)
        frame.pack_propagate(False)

        section = page._build_sidebar_category_group(frame, group)
        self.root.update_idletasks()

        wraplength = int(section._sidebar_title_label.cget("wraplength"))
        self.assertGreaterEqual(wraplength, 120)

        section._sidebar_toggle_button.invoke()
        self.root.update_idletasks()

        self.assertEqual(section._sidebar_toggle_button.cget("text"), "Show More")
        self.assertEqual(section._sidebar_summary_var.get(), "2 item | Total 12,345.67")
        self.assertEqual(str(section._sidebar_summary_label.winfo_manager()), "grid")

    def test_build_detail_tabs_uses_custom_buttons_and_extended_selection(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page.root = self.root
        page._detail_tab_buttons = {}
        page._detail_tab_frames = {}
        page._tree_row_meta = {}
        page._tree_active_cell = {}
        page._tree_role_by_widget = {}
        page._analysis_rows = []
        page._active_detail_tab = "svl"
        page.compare_summary_var = tk.StringVar(master=self.root, value="")
        frame = tk.Frame(self.root, bg=T.BG_CARD)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        page._build_detail_tabs(frame, row=0, column=0)
        self.root.update_idletasks()

        self.assertEqual(set(page._detail_tab_buttons), {"svl", "jnl", "merged", "current_asset", "compare", "analysis"})
        self.assertEqual(page._detail_tab_buttons["merged"].cget("bg"), DETAIL_TAB_COLORS["merged"]["inactive"])
        self.assertEqual(str(page.svl_tree.cget("selectmode")), "extended")
        self.assertEqual(str(page.jnl_tree.cget("selectmode")), "extended")
        self.assertEqual(str(page.merged_tree.cget("selectmode")), "extended")
        self.assertEqual(str(page.compare_po_tree.cget("selectmode")), "extended")
        self.assertEqual(str(page.compare_bill_tree.cget("selectmode")), "extended")
        merged_tree_show = str(page.merged_tree.cget("show"))
        self.assertIn("tree", merged_tree_show)
        self.assertIn("headings", merged_tree_show)

        page._switch_detail_tab("merged")
        self.root.update_idletasks()

        self.assertEqual(page._detail_tab_buttons["merged"].cget("bg"), DETAIL_TAB_COLORS["merged"]["active"])
        self.assertEqual(str(page.merged_tab.winfo_manager()), "grid")
        self.assertEqual(page.svl_tab.winfo_manager(), "")

    def test_grouped_merged_tree_uses_status_headers_and_default_open_state(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page.root = self.root
        page.status_var = tk.StringVar(master=self.root, value="")
        page._detail_tab_buttons = {}
        page._detail_tab_frames = {}
        page._tree_row_meta = {}
        page._tree_active_cell = {}
        page._tree_role_by_widget = {}
        page._analysis_rows = []
        page._active_detail_tab = "merged"
        page._merged_group_open = {}
        page.compare_summary_var = tk.StringVar(master=self.root, value="")
        frame = tk.Frame(self.root, bg=T.BG_CARD)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        page._build_detail_tabs(frame, row=0, column=0)
        page._fill_grouped_merged_tree(
            page.merged_tree,
            [
                {
                    "values": ("direct_move", 101, "2026-01-10", "150.00", "STJ/2026/0010", "posted", 201, "114001", "Inventory", "150.00", "-", "150.00", "REF-1", "-"),
                    "meta": {"group_status": "Linked JE header kosong", "group_amount": 150.0, "repair_candidate": True, "repair_seed": {"row_key": "row-1"}},
                },
                {
                    "values": ("direct_move", 102, "2026-01-11", "99.00", "STJ/2026/0011", "posted", 202, "114001", "Inventory", "99.00", "-", "99.00", "REF-2", "-"),
                    "meta": {"group_status": "Linked via move", "group_amount": 99.0, "repair_candidate": False, "repair_seed": None},
                },
            ],
        )
        self.root.update_idletasks()

        group_ids = page.merged_tree.get_children()
        self.assertEqual(len(group_ids), 2)
        self.assertIn("Linked JE header kosong | 1 row | Total 150.00", page.merged_tree.item(group_ids[0], "text"))
        self.assertTrue(bool(page.merged_tree.item(group_ids[0], "open")))
        self.assertFalse(bool(page.merged_tree.item(group_ids[1], "open")))

        click_event = mock.Mock(x=8, y=8)
        with mock.patch.object(page.merged_tree, "identify_row", return_value=group_ids[0]), mock.patch.object(
            page.merged_tree,
            "identify_column",
            return_value="#0",
        ):
            result = page._on_tree_left_click(page.merged_tree, click_event)

        self.assertEqual(result, "break")
        self.assertFalse(bool(page.merged_tree.item(group_ids[0], "open")))
        self.assertEqual(page._selected_tree_leaf_item_ids(page.merged_tree), [])

    def test_grouped_merged_tree_copy_and_repair_ignore_group_headers(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page.root = self.root
        page.status_var = tk.StringVar(master=self.root, value="")
        page._tree_row_meta = {}
        page._tree_active_cell = {}
        page._tree_role_by_widget = {}
        page._merged_group_open = {}
        page._repair_collection = OrderedDict()
        page._repair_collection_scope = None
        page._repair_collection_sequence = 0
        page._repair_collection_changed_callback = None
        page._display_name = "Fixing Unlink SVL - Odoo"
        page.company_choice_var = tk.StringVar(master=self.root, value="Alpha Company (#7)")
        page._selected_company_id = lambda: 7
        page._selected_database_profile_id = lambda: "db_live"
        page._label_for_company_id = lambda _company_id: "Alpha Company (#7)"
        page._resolve_effective_database_state = lambda selected_profile_id=None: {  # noqa: ARG005
            "selected_profile_id": "db_live",
            "selected_label": "Live ERP - hwgroup_erp [Database Live]",
            "effective_label": "Live ERP - hwgroup_erp [Database Live]",
            "effective_database": "hwgroup_erp",
        }
        frame = tk.Frame(self.root)
        frame.pack(fill="both", expand=True)
        tree = page._build_tree(
            frame,
            ("match_basis", "svl_id", "svl_date", "svl_value", "journal", "move_state", "aml_id", "account_code", "account_name", "debit", "credit", "net", "reference", "note"),
            selectmode="extended",
            role="merged",
            show="tree headings",
        )
        tree.heading("#0", text="STATUS")
        tree.column("#0", width=240, minwidth=180, anchor="w", stretch=False)
        copied: list[str] = []
        page._copy_text_to_clipboard = copied.append

        page._fill_grouped_merged_tree(
            tree,
            [
                {
                    "values": ("direct_move", 101, "2026-01-10", "150.00", "STJ/2026/0010", "posted", 201, "114001", "Inventory", "150.00", "-", "150.00", "REF-1", "-"),
                    "meta": {
                        "group_status": "Linked JE header kosong",
                        "group_amount": 150.0,
                        "odoo_model": "account.move.line",
                        "odoo_id": 201,
                        "move_id": 501,
                        "repair_candidate": True,
                        "repair_seed": {"row_key": "row-1"},
                    },
                }
            ],
        )
        self.root.update_idletasks()

        group_id = tree.get_children()[0]
        leaf_id = tree.get_children(group_id)[0]

        tree.selection_set((group_id, leaf_id))
        page._copy_tree_selected_rows(tree)
        self.assertIn("Linked JE header kosong", copied[-1])
        self.assertIn("114001", copied[-1])

        tree.selection_set(group_id)
        copied_count = len(copied)
        page._copy_tree_selected_rows(tree)
        self.assertEqual(len(copied), copied_count)
        self.assertEqual(page._repair_candidates_for_tree(tree), [])
        self.assertFalse(page._can_add_selected_to_repair_collection(tree))

        tree.selection_set((group_id, leaf_id))
        candidates = page._repair_candidates_for_tree(tree)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["repair_seed"]["row_key"], "row-1")
        self.assertTrue(page._can_add_selected_to_repair_collection(tree))

        page.add_selected_to_repair_collection(tree)
        self.assertTrue(page._can_remove_selected_from_repair_collection(tree))

    def test_open_repair_dialog_uses_separate_scroll_areas_and_fixed_footer(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page.root = self.root
        page.context = mock.Mock(global_settings=GlobalSettings())
        page._display_name = "Fixing Unlink SVL - Odoo"
        page._last_repair_dialog_widgets = {}
        page._selected_company_id = lambda: 7
        page._repair_candidates_for_tree = lambda _tree: [  # noqa: ARG005
            {
                "repair_seed": {
                    "row_key": "row-1",
                    "company_id": 7,
                    "item_product_id": 11,
                    "item_code": "F-DGCP-0124",
                    "item_name": "GARAM HALUS / FINE SALT",
                    "amount": 20989.60,
                    "date": date.today().strftime("%Y-%m-%d"),
                    "reference": "Correction New JE + Relink SVL F-DGCP-0124",
                    "line_label": "Correction New JE + Relink SVL F-DGCP-0124 GARAM HALUS / FINE SALT",
                    "journal_code": "STJ",
                    "target_mode": "new_and_relink",
                    "posting_mode": "post",
                    "signed_amount": 20989.60,
                    "base_reference": "F-DGCP-0124",
                    "base_line_label": "F-DGCP-0124 GARAM HALUS / FINE SALT",
                    "move_id": 0,
                    "move_name": "",
                    "move_state": "",
                    "account_candidates": [],
                }
            }
        ]

        tree = ttk.Treeview(self.root)
        page._open_repair_dialog(tree)
        self.root.update_idletasks()

        widgets = page._last_repair_dialog_widgets
        widgets["apply_split_layout"]()
        self.root.update_idletasks()
        self.assertIsInstance(widgets["content_pane"], tk.PanedWindow)
        self.assertEqual(str(widgets["left_panel"].winfo_manager()), "panedwindow")
        self.assertEqual(str(widgets["right_host"].winfo_manager()), "panedwindow")
        self.assertIsInstance(widgets["right_scroll"], ScrollableFrame)
        self.assertEqual(str(widgets["left_scrollbar"].winfo_manager()), "grid")
        self.assertEqual(str(widgets["left_list"].cget("selectmode")), "extended")
        self.assertEqual(str(widgets["footer"].winfo_manager()), "grid")
        self.assertEqual(str(widgets["action_row"].winfo_manager()), "pack")
        self.assertEqual(widgets["footer"].master, widgets["right_scroll"].master)
        self.assertNotEqual(widgets["footer"].master, widgets["right_body"])
        self.assertEqual(widgets["resolve_candidates"].winfo_class(), "Listbox")
        self.assertIsInstance(widgets["repair_progress_bar"], ttk.Progressbar)
        self.assertEqual(widgets["repair_progress_bar_host"].master, widgets["progress_row"])
        self.assertEqual(widgets["repair_progress_bar_host"].winfo_manager(), "grid")
        self.assertEqual(int(widgets["repair_progress_bar_host"].grid_info()["column"]), 0)
        self.assertEqual(int(widgets["progress_row"].grid_columnconfigure(0)["weight"]), 13)
        self.assertGreaterEqual(int(widgets["progress_row"].grid_columnconfigure(1)["minsize"]), 120)
        self.assertEqual(widgets["repair_progress_bar"].master, widgets["repair_progress_bar_host"])
        self.assertEqual(widgets["repair_progress_bar"].winfo_manager(), "pack")
        self.assertGreaterEqual(int(widgets["repair_progress_bar"].cget("length")), 120)
        self.assertEqual(widgets["repair_progress_info_host"].master, widgets["progress_row"])
        self.assertEqual(widgets["repair_progress_info_host"].winfo_manager(), "grid")
        self.assertEqual(int(widgets["repair_progress_info_host"].grid_info()["column"]), 1)
        self.assertEqual(widgets["repair_progress_count_label"].master, widgets["repair_progress_info_host"])
        self.assertEqual(widgets["repair_progress_count_label"].winfo_manager(), "grid")
        self.assertEqual(int(widgets["repair_progress_count_label"].grid_info()["column"]), 0)
        self.assertEqual(widgets["repair_progress_percent_label"].master, widgets["repair_progress_info_host"])
        self.assertEqual(widgets["repair_progress_percent_label"].winfo_manager(), "grid")
        self.assertEqual(int(widgets["repair_progress_percent_label"].grid_info()["column"]), 1)
        self.assertEqual(widgets["repair_progress_current_label"].master, widgets["repair_progress_info_host"])
        self.assertEqual(widgets["repair_progress_current_label"].winfo_manager(), "grid")
        self.assertEqual(int(widgets["repair_progress_current_label"].grid_info()["column"]), 0)
        self.assertEqual(int(widgets["repair_progress_current_label"].grid_info()["columnspan"]), 2)
        self.assertGreaterEqual(int(widgets["repair_progress_current_label"].cget("wraplength")), 120)
        self.assertEqual(widgets["repair_progress_count_var"].get(), "0 / 0")
        self.assertEqual(widgets["repair_progress_percent_var"].get(), "0%")
        self.assertEqual(widgets["apply_global_button"].cget("text"), "Apply Global to Selected")
        self.assertEqual(widgets["repair_selected_button"].cget("text"), "Repair Selected Row(s)")
        self.assertEqual(widgets["repair_all_button"].cget("text"), "Repair All")
        self.assertIn("Selected rows", widgets["source_label_var"].get())
        self.assertEqual(len(self._repair_tree_leaf_ids(widgets)), 1)
        self.assertEqual(len(self._repair_tree_group_ids(widgets)), 1)
        self.assertIn("SVL tanpa JE", widgets["left_list"].item(self._repair_tree_group_ids(widgets)[0], "text"))
        self.assertEqual(widgets["source_status_var"].get(), "SVL tanpa JE")
        sash_x = widgets["content_pane"].sash_coord(0)[0]
        pane_width = widgets["measure_split_width"]()
        self.assertGreater(pane_width, 0)
        self.assertAlmostEqual(sash_x / pane_width, 0.6, delta=0.08)

        widgets["dialog"].destroy()

    def test_repair_progress_event_updates_dialog_progress_widgets(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page.root = self.root
        page.context = mock.Mock(global_settings=GlobalSettings())
        page._display_name = "Fixing Unlink SVL - Odoo"
        page._last_repair_dialog_widgets = {}
        page._selected_company_id = lambda: 7
        page.status_var = tk.StringVar(master=self.root, value="")
        page._repair_candidates_for_tree = lambda _tree: [  # noqa: ARG005
            {
                "repair_seed": {
                    "row_key": "row-1",
                    "company_id": 7,
                    "item_product_id": 11,
                    "item_code": "F-DGCP-0124",
                    "item_name": "GARAM HALUS / FINE SALT",
                    "amount": 20989.60,
                    "date": date.today().strftime("%Y-%m-%d"),
                    "reference": "Correction New JE + Relink SVL F-DGCP-0124",
                    "line_label": "Correction New JE + Relink SVL F-DGCP-0124 GARAM HALUS / FINE SALT",
                    "journal_code": "STJ",
                    "target_mode": "new_and_relink",
                    "posting_mode": "post",
                    "signed_amount": 20989.60,
                    "base_reference": "F-DGCP-0124",
                    "base_line_label": "F-DGCP-0124 GARAM HALUS / FINE SALT",
                    "move_id": 0,
                    "move_name": "",
                    "move_state": "",
                    "account_candidates": [],
                }
            }
        ]

        tree = ttk.Treeview(self.root)
        page._open_repair_dialog(tree)
        self.root.update_idletasks()

        page._handle_ui_event(
            {
                "type": "repair_progress",
                "snapshot": SvlDashboardRepairProgressSnapshot(
                    phase="repair",
                    processed=3,
                    total=5,
                    current="F-DGCP-0124 | GARAM HALUS / FINE SALT",
                    progress=0.6,
                    success_count=2,
                    error_count=1,
                ),
            }
        )

        widgets = page._last_repair_dialog_widgets
        self.assertEqual(widgets["repair_progress_count_var"].get(), "3 / 5")
        self.assertEqual(widgets["repair_progress_percent_var"].get(), "60%")
        self.assertEqual(
            widgets["repair_progress_current_var"].get(),
            "F-DGCP-0124 | GARAM HALUS / FINE SALT",
        )
        self.assertEqual(widgets["repair_progress_meta_var"].get(), "Berhasil 2 | Gagal 1")
        self.assertIn("Repair berjalan 3 / 5 (60%)", page.status_var.get())

        widgets["dialog"].destroy()

    def test_repair_dialog_split_keeps_manual_ratio_after_resize(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page.root = self.root
        page.context = mock.Mock(global_settings=GlobalSettings())
        page._display_name = "Fixing Unlink SVL - Odoo"
        page._last_repair_dialog_widgets = {}
        page._selected_company_id = lambda: 7
        page._repair_candidates_for_tree = lambda _tree: [  # noqa: ARG005
            {
                "repair_seed": {
                    "row_key": "row-1",
                    "company_id": 7,
                    "item_product_id": 11,
                    "item_code": "F-DGCP-0124",
                    "item_name": "GARAM HALUS / FINE SALT",
                    "amount": 20989.60,
                    "date": date.today().strftime("%Y-%m-%d"),
                    "reference": "Correction New JE + Relink SVL F-DGCP-0124",
                    "line_label": "Correction New JE + Relink SVL F-DGCP-0124 GARAM HALUS / FINE SALT",
                    "journal_code": "STJ",
                    "target_mode": "new_and_relink",
                    "posting_mode": "post",
                    "signed_amount": 20989.60,
                    "base_reference": "F-DGCP-0124",
                    "base_line_label": "F-DGCP-0124 GARAM HALUS / FINE SALT",
                    "move_id": 0,
                    "move_name": "",
                    "move_state": "",
                    "account_candidates": [],
                }
            }
        ]

        tree = ttk.Treeview(self.root)
        page._open_repair_dialog(tree)
        self.root.update_idletasks()

        widgets = page._last_repair_dialog_widgets
        pane = widgets["content_pane"]
        pane.configure(width=1120)
        widgets["apply_split_layout"]()
        self.root.update_idletasks()
        initial_width = widgets["measure_split_width"]()
        pane.sash_place(0, int(initial_width * 0.68), 0)
        widgets["remember_split_ratio"]()

        pane.configure(width=1320)
        self.root.update_idletasks()
        widgets["apply_split_layout"]()
        self.root.update_idletasks()

        resized_width = widgets["measure_split_width"]()
        sash_x = pane.sash_coord(0)[0]
        self.assertGreater(resized_width, initial_width)
        self.assertAlmostEqual(sash_x / resized_width, 0.68, delta=0.08)

        widgets["dialog"].destroy()

    def test_repair_dialog_groups_rows_by_source_and_updates_source_detail(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page.root = self.root
        page.context = mock.Mock(global_settings=GlobalSettings())
        page._display_name = "Fixing Unlink SVL - Odoo"
        page._last_repair_dialog_widgets = {}
        page._busy = False
        page._repair_collection = OrderedDict()
        page.status_var = tk.StringVar(master=self.root, value="")
        page._selected_company_id = lambda: 7
        page._notify_repair_collection_changed = lambda: None
        page._module_settings = SvlFixJeSettings()

        page._open_repair_dialog_for_rows(
            [
                {
                    "row_key": "row-no-je",
                    "company_id": 7,
                    "item_product_id": 11,
                    "item_code": "ITEM-001",
                    "item_name": "Baileys",
                    "amount": 125.5,
                    "date": "2026-03-18",
                    "journal_code": "STJ",
                    "signed_amount": 125.5,
                    "base_reference": "ITEM-001",
                    "base_line_label": "ITEM-001 Baileys",
                    "move_id": 0,
                    "move_name": "",
                    "move_state": "",
                    "svl_id": 501,
                    "svl_date": "2026-03-18",
                    "svl_qty": 2.0,
                    "svl_unit_cost": 62.75,
                    "svl_value": 125.5,
                    "svl_reference": "SVL/001",
                    "repair_source_kind": "svl_no_move",
                    "repair_source_label": "SVL tanpa JE",
                    "account_candidates": [],
                },
                {
                    "row_key": "row-linked-empty",
                    "company_id": 7,
                    "item_product_id": 12,
                    "item_code": "ITEM-002",
                    "item_name": "Guinness",
                    "amount": 75.0,
                    "date": "2026-03-18",
                    "journal_code": "STJ",
                    "signed_amount": 75.0,
                    "base_reference": "ITEM-002",
                    "base_line_label": "ITEM-002 Guinness",
                    "move_id": 901,
                    "move_name": "STJ/2026/0901",
                    "move_state": "posted",
                    "svl_id": 601,
                    "svl_date": "2026-03-17",
                    "svl_qty": 1.0,
                    "svl_unit_cost": 75.0,
                    "svl_value": 75.0,
                    "svl_reference": "SVL/002",
                    "repair_source_kind": "svl_linked_empty_move",
                    "repair_source_label": "Linked JE Header Kosong",
                    "account_candidates": [],
                },
            ],
            source_title="Repair Collection",
            source_note="Collected rows lintas item.",
        )
        self.root.update_idletasks()

        widgets = page._last_repair_dialog_widgets
        group_ids = self._repair_tree_group_ids(widgets)
        self.assertEqual(len(group_ids), 2)
        self.assertIn("SVL tanpa JE", widgets["left_list"].item(group_ids[0], "text"))
        self.assertIn("Linked JE Header Kosong", widgets["left_list"].item(group_ids[1], "text"))
        self.assertIn("SVL tanpa JE: create JE baru lalu link ke SVL.", widgets["source_label_var"].get())
        self.assertIn("Linked JE Header Kosong: create JE baru, relink SVL, old move mark only.", widgets["source_label_var"].get())
        self.assertEqual(widgets["source_status_var"].get(), "SVL tanpa JE")
        self.assertEqual(widgets["source_linked_move_var"].get(), "Tidak ada JE")

        self._select_repair_tree_indices(widgets, 1)
        self.assertEqual(widgets["source_status_var"].get(), "Linked JE Header Kosong")
        self.assertEqual(widgets["source_linked_move_var"].get(), "STJ/2026/0901")
        group_ids = self._repair_tree_group_ids(widgets)
        widgets["toggle_row_group"](group_ids[0])
        self.root.update_idletasks()
        group_ids = self._repair_tree_group_ids(widgets)
        self.assertFalse(bool(widgets["left_list"].item(group_ids[0], "open")))
        self.assertEqual(widgets["source_status_var"].get(), "Linked JE Header Kosong")
        widgets["toggle_row_group"](group_ids[0])
        self.root.update_idletasks()
        group_ids = self._repair_tree_group_ids(widgets)
        self.assertTrue(bool(widgets["left_list"].item(group_ids[0], "open")))

        widgets["dialog"].destroy()

    def test_repair_row_preview_includes_explicit_row_status_marker(self) -> None:
        ready = SvlFixJeDashboardPage._repair_row_preview(
            {
                "item_code": "A-001",
                "item_name": "Ready Item",
                "amount": 15.0,
                "move_name": "STJ/2026/0001",
                "row_status": "ready",
                "row_status_message": "Ready",
            }
        )
        missing = SvlFixJeDashboardPage._repair_row_preview(
            {
                "item_code": "A-002",
                "item_name": "Needs COA",
                "amount": 25.0,
                "move_name": "STJ/2026/0002",
                "row_status": "incomplete",
                "row_status_message": "Target, Posting",
            }
        )

        self.assertIn("[Ready]", ready)
        self.assertIn("[Incomplete]", missing)
        self.assertIn("Target, Posting", missing)

    def test_repair_dialog_apply_global_to_selected_derives_accounts_by_sign(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page.root = self.root
        page.context = mock.Mock(global_settings=GlobalSettings())
        page._display_name = "Fixing Unlink SVL - Odoo"
        page._last_repair_dialog_widgets = {}
        page._busy = False
        page._repair_collection = OrderedDict()
        page.status_var = tk.StringVar(master=self.root, value="")
        page._selected_company_id = lambda: 7
        page._notify_repair_collection_changed = lambda: None
        page._module_settings = SvlFixJeSettings()

        valuation_candidate = SvlDashboardRepairAccountCandidate(
            code="114001",
            name="Inventory",
            source="Category Stock Valuation",
            role="valuation",
            field_name="property_stock_valuation_account_id",
        )
        extra_candidate = SvlDashboardRepairAccountCandidate(
            code="1108099",
            name="Correction",
            source="Global Extra",
            role="extra",
        )

        draft_rows = [
            {
                "row_key": "row-1",
                "company_id": 7,
                "item_product_id": 11,
                "item_code": "ITEM-001",
                "item_name": "First Item",
                "amount": 100.0,
                "date": "2026-03-18",
                "journal_code": "STJ",
                "signed_amount": 100.0,
                "base_reference": "ITEM-001",
                "base_line_label": "ITEM-001 First Item",
                "move_id": 0,
                "move_name": "",
                "move_state": "",
                "account_candidates": [valuation_candidate, extra_candidate],
            },
            {
                "row_key": "row-2",
                "company_id": 7,
                "item_product_id": 12,
                "item_code": "ITEM-002",
                "item_name": "Second Item",
                "amount": 250.0,
                "date": "2026-03-18",
                "journal_code": "STJ",
                "signed_amount": -250.0,
                "base_reference": "ITEM-002",
                "base_line_label": "ITEM-002 Second Item",
                "move_id": 0,
                "move_name": "",
                "move_state": "",
                "account_candidates": [valuation_candidate, extra_candidate],
            },
        ]

        page._open_repair_dialog_for_rows(
            draft_rows,
            source_title="Repair Collection",
            source_note="Collected rows lintas item.",
        )
        self.root.update_idletasks()

        widgets = page._last_repair_dialog_widgets
        draft_state = widgets["draft_rows"]
        leaf_ids = self._repair_tree_leaf_ids(widgets)

        self.assertEqual(draft_state[0]["debit_account_code"], "")
        self.assertEqual(draft_state[0]["credit_account_code"], "")
        self.assertIn("[Incomplete]", widgets["left_list"].item(leaf_ids[0], "text"))

        self._select_repair_tree_indices(widgets, 0, 1)

        widgets["global_target_combo"].set("New JE + Relink SVL")
        widgets["global_posting_combo"].set("Auto Post")
        widgets["global_role_combo"].set("Valuation")
        widgets["global_resolve_var"].set("1108099")
        widgets["apply_global_button"].invoke()
        self._drain_tk_events()

        self.assertEqual(draft_state[0]["target_mode"], "new_and_relink")
        self.assertEqual(draft_state[0]["posting_mode"], "post")
        self.assertEqual(draft_state[0]["debit_account_code"], "114001")
        self.assertEqual(draft_state[0]["credit_account_code"], "1108099")
        self.assertEqual(draft_state[1]["debit_account_code"], "1108099")
        self.assertEqual(draft_state[1]["credit_account_code"], "114001")
        self.assertEqual(draft_state[0]["row_status"], "ready")
        self.assertEqual(draft_state[1]["row_status"], "ready")
        leaf_ids = self._repair_tree_leaf_ids(widgets)
        self.assertIn("[Ready]", widgets["left_list"].item(leaf_ids[1], "text"))
        self.assertIn("2 row terpilih", page.status_var.get())

        widgets["dialog"].destroy()

    def test_repair_dialog_apply_global_to_selected_keeps_missing_category_valuation_incomplete(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page.root = self.root
        page.context = mock.Mock(global_settings=GlobalSettings())
        page._display_name = "Fixing Unlink SVL - Odoo"
        page._last_repair_dialog_widgets = {}
        page._busy = False
        page._repair_collection = OrderedDict()
        page.status_var = tk.StringVar(master=self.root, value="")
        page._selected_company_id = lambda: 7
        page._notify_repair_collection_changed = lambda: None
        page._module_settings = SvlFixJeSettings()

        page._open_repair_dialog_for_rows(
            [
                {
                    "row_key": "row-1",
                    "company_id": 7,
                    "item_product_id": 11,
                    "item_code": "ITEM-001",
                    "item_name": "Baileys",
                    "item_category_name": "ALCOHOL / LIQUEUR / SWEET & CREAMY",
                    "amount": 100.0,
                    "date": "2026-03-18",
                    "journal_code": "STJ",
                    "signed_amount": 100.0,
                    "base_reference": "ITEM-001",
                    "base_line_label": "ITEM-001 Baileys",
                    "move_id": 0,
                    "move_name": "",
                    "move_state": "",
                    "account_candidates": [
                        SvlDashboardRepairAccountCandidate(
                            code="1103004",
                            name="Intercompany Output",
                            source="Category Other",
                            role="other",
                            field_name="property_stock_inter_company_output_account_id",
                        )
                    ],
                }
            ],
            source_title="Repair Collection",
            source_note="Collected rows lintas item.",
        )
        self.root.update_idletasks()

        widgets = page._last_repair_dialog_widgets
        draft_state = widgets["draft_rows"]

        self._select_repair_tree_indices(widgets, 0)

        widgets["global_target_combo"].set("New JE + Relink SVL")
        widgets["global_posting_combo"].set("Auto Post")
        widgets["global_role_combo"].set("Valuation")
        widgets["global_resolve_var"].set("1108099")
        widgets["apply_global_button"].invoke()
        self._drain_tk_events()

        self.assertEqual(draft_state[0]["target_account_code"], "")
        self.assertEqual(draft_state[0]["debit_account_code"], "")
        self.assertEqual(draft_state[0]["credit_account_code"], "")
        self.assertEqual(draft_state[0]["row_status"], "incomplete")
        self.assertIn("Stock Valuation Account", draft_state[0]["row_status_message"])
        self.assertIn("ALCOHOL / LIQUEUR / SWEET & CREAMY", draft_state[0]["row_status_message"])
        self.assertIn("Stock Valuation Account", draft_state[0]["target_account_preview"])
        self.assertIn("[Incomplete]", widgets["left_list"].item(self._repair_tree_leaf_ids(widgets)[0], "text"))

        widgets["dialog"].destroy()

    def test_repair_dialog_select_all_uses_physical_selection_under_threshold(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page.root = self.root
        page.context = mock.Mock(global_settings=GlobalSettings())
        page._display_name = "Fixing Unlink SVL - Odoo"
        page._last_repair_dialog_widgets = {}
        page._busy = False
        page._repair_collection = OrderedDict()
        page._repair_virtual_select_all_threshold = 3
        page.status_var = tk.StringVar(master=self.root, value="")
        page._selected_company_id = lambda: 7
        page._notify_repair_collection_changed = lambda: None
        page._module_settings = SvlFixJeSettings()

        rows = self._build_repair_dialog_rows(3)
        page._open_repair_dialog_for_rows(
            rows,
            source_title="Repair Collection",
            source_note="Collected rows lintas item.",
        )
        self.root.update_idletasks()

        widgets = page._last_repair_dialog_widgets
        widgets["select_all_button"].invoke()
        self.root.update_idletasks()

        self.assertFalse(widgets["is_virtual_select_all"]())
        self.assertEqual(widgets["current_selection_count"](), 3)
        self.assertEqual(len(widgets["left_list"].selection()), 3)
        self.assertEqual(widgets["selection_summary_var"].get(), "3 rows selected")

        widgets["dialog"].destroy()

    def test_repair_dialog_select_all_uses_virtual_selection_over_threshold(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page.root = self.root
        page.context = mock.Mock(global_settings=GlobalSettings())
        page._display_name = "Fixing Unlink SVL - Odoo"
        page._last_repair_dialog_widgets = {}
        page._busy = False
        page._repair_collection = OrderedDict()
        page._repair_virtual_select_all_threshold = 3
        page.status_var = tk.StringVar(master=self.root, value="")
        page._selected_company_id = lambda: 7
        page._notify_repair_collection_changed = lambda: None
        page._module_settings = SvlFixJeSettings()

        rows = self._build_repair_dialog_rows(4)
        page._open_repair_dialog_for_rows(
            rows,
            source_title="Repair Collection",
            source_note="Collected rows lintas item.",
        )
        self.root.update_idletasks()

        widgets = page._last_repair_dialog_widgets
        widgets["select_all_button"].invoke()
        self.root.update_idletasks()

        self.assertTrue(widgets["is_virtual_select_all"]())
        self.assertEqual(widgets["current_selection_count"](), 4)
        self.assertEqual(widgets["current_selection_snapshot"](), REPAIR_DIALOG_SELECTION_ALL)
        self.assertLess(len(widgets["left_list"].selection()), 4)
        self.assertIn("Mode virtual aktif", page.status_var.get())
        self.assertEqual(widgets["selection_summary_var"].get(), "All 4 rows selected (virtual)")

        widgets["dialog"].destroy()

    def test_repair_dialog_virtual_select_all_bulk_apply_runs_chunked_and_notifies_once(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page.root = self.root
        page.context = mock.Mock(global_settings=GlobalSettings())
        page._display_name = "Fixing Unlink SVL - Odoo"
        page._last_repair_dialog_widgets = {}
        page._busy = False
        page._repair_virtual_select_all_threshold = 3
        page._repair_bulk_apply_chunk_size = 2
        page.status_var = tk.StringVar(master=self.root, value="")
        page._selected_company_id = lambda: 7
        page._notify_repair_collection_changed = mock.Mock()
        page._module_settings = SvlFixJeSettings()

        valuation_candidate = SvlDashboardRepairAccountCandidate(
            code="114001",
            name="Inventory",
            source="Category Stock Valuation",
            role="valuation",
            field_name="property_stock_valuation_account_id",
        )
        extra_candidate = SvlDashboardRepairAccountCandidate(
            code="1108099",
            name="Correction",
            source="Global Extra",
            role="extra",
        )
        rows = self._build_repair_dialog_rows(4, account_candidates=[valuation_candidate, extra_candidate])
        page._repair_collection = OrderedDict(
            (row["row_key"], mock.Mock(draft=row))
            for row in rows
        )

        page._open_repair_dialog_for_rows(
            rows,
            source_title="Repair Collection",
            source_note="Collected rows lintas item.",
        )
        self.root.update_idletasks()

        widgets = page._last_repair_dialog_widgets
        widgets["select_all_button"].invoke()
        widgets["global_target_combo"].set("New JE + Relink SVL")
        widgets["global_posting_combo"].set("Auto Post")
        widgets["global_role_combo"].set("Valuation")
        widgets["global_resolve_var"].set("1108099")
        widgets["apply_global_button"].invoke()

        self.assertTrue(widgets["is_bulk_apply_running"]())
        self.assertEqual(str(widgets["apply_global_button"].cget("state")), "disabled")

        for _ in range(50):
            self.root.update()
            if not widgets["is_bulk_apply_running"]():
                break

        self.assertFalse(widgets["is_bulk_apply_running"]())
        self.assertTrue(widgets["is_virtual_select_all"]())
        self.assertEqual(widgets["repair_progress_count_var"].get(), "4 / 4")
        self.assertEqual(widgets["repair_progress_percent_var"].get(), "100%")
        self.assertIn("Apply global selesai", widgets["repair_progress_current_var"].get())
        self.assertIn("Applied 4", widgets["repair_progress_meta_var"].get())
        self.assertTrue(all(row["row_status"] == "ready" for row in widgets["draft_rows"]))
        self.assertEqual(page._notify_repair_collection_changed.call_count, 1)

        widgets["dialog"].destroy()

    def test_repair_dialog_repair_selected_uses_virtual_selection_scope(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page.root = self.root
        page.context = mock.Mock(global_settings=GlobalSettings())
        page._display_name = "Fixing Unlink SVL - Odoo"
        page._last_repair_dialog_widgets = {}
        page._busy = False
        page._repair_virtual_select_all_threshold = 3
        page._repair_bulk_apply_chunk_size = 2
        page.status_var = tk.StringVar(master=self.root, value="")
        page._selected_company_id = lambda: 7
        page._notify_repair_collection_changed = mock.Mock()
        page._module_settings = SvlFixJeSettings()
        captured_rows: list[list[object]] = []
        page._start_repair = lambda rows: captured_rows.append(rows)  # type: ignore[assignment]

        valuation_candidate = SvlDashboardRepairAccountCandidate(
            code="114001",
            name="Inventory",
            source="Category Stock Valuation",
            role="valuation",
            field_name="property_stock_valuation_account_id",
        )
        extra_candidate = SvlDashboardRepairAccountCandidate(
            code="1108099",
            name="Correction",
            source="Global Extra",
            role="extra",
        )
        rows = self._build_repair_dialog_rows(4, account_candidates=[valuation_candidate, extra_candidate])
        page._repair_collection = OrderedDict(
            (row["row_key"], mock.Mock(draft=row))
            for row in rows
        )

        page._open_repair_dialog_for_rows(
            rows,
            source_title="Repair Collection",
            source_note="Collected rows lintas item.",
        )
        self.root.update_idletasks()

        widgets = page._last_repair_dialog_widgets
        widgets["select_all_button"].invoke()
        widgets["global_target_combo"].set("New JE + Relink SVL")
        widgets["global_posting_combo"].set("Auto Post")
        widgets["global_role_combo"].set("Valuation")
        widgets["global_resolve_var"].set("1108099")
        widgets["apply_global_button"].invoke()
        for _ in range(50):
            self.root.update()
            if not widgets["is_bulk_apply_running"]():
                break

        widgets["repair_selected_button"].invoke()
        self.root.update_idletasks()

        self.assertEqual(len(captured_rows), 1)
        self.assertEqual(len(captured_rows[0]), 4)
        self.assertTrue(widgets["is_virtual_select_all"]())

        widgets["dialog"].destroy()

    def test_repair_dialog_repair_all_blocks_when_any_row_incomplete(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page.root = self.root
        page.context = mock.Mock(global_settings=GlobalSettings())
        page._display_name = "Fixing Unlink SVL - Odoo"
        page._last_repair_dialog_widgets = {}
        page._busy = False
        page._repair_collection = OrderedDict()
        page.status_var = tk.StringVar(master=self.root, value="")
        page._selected_company_id = lambda: 7
        captured_rows: list[list[object]] = []
        page._start_repair = lambda rows: captured_rows.append(rows)  # type: ignore[assignment]
        page._notify_repair_collection_changed = lambda: None
        page._module_settings = SvlFixJeSettings()

        valuation_candidate = SvlDashboardRepairAccountCandidate(
            code="114001",
            name="Inventory",
            source="Category Stock Valuation",
            role="valuation",
            field_name="property_stock_valuation_account_id",
        )
        extra_candidate = SvlDashboardRepairAccountCandidate(
            code="1108099",
            name="Correction",
            source="Global Extra",
            role="extra",
        )

        page._open_repair_dialog_for_rows(
            [
                {
                    "row_key": "row-1",
                    "company_id": 7,
                    "item_product_id": 11,
                    "item_code": "ITEM-001",
                    "item_name": "First Item",
                    "amount": 100.0,
                    "date": "2026-03-18",
                    "journal_code": "STJ",
                    "signed_amount": 100.0,
                    "base_reference": "ITEM-001",
                    "base_line_label": "ITEM-001 First Item",
                    "move_id": 0,
                    "move_name": "",
                    "move_state": "",
                    "account_candidates": [valuation_candidate, extra_candidate],
                },
                {
                    "row_key": "row-2",
                    "company_id": 7,
                    "item_product_id": 12,
                    "item_code": "ITEM-002",
                    "item_name": "Second Item",
                    "amount": 250.0,
                    "date": "2026-03-18",
                    "journal_code": "STJ",
                    "signed_amount": 250.0,
                    "base_reference": "ITEM-002",
                    "base_line_label": "ITEM-002 Second Item",
                    "move_id": 0,
                    "move_name": "",
                    "move_state": "",
                    "account_candidates": [],
                },
            ],
            source_title="Repair Collection",
            source_note="Collected rows lintas item.",
        )
        self.root.update_idletasks()

        widgets = page._last_repair_dialog_widgets
        self._select_repair_tree_indices(widgets, 0)
        widgets["global_target_combo"].set("New JE + Relink SVL")
        widgets["global_posting_combo"].set("Auto Post")
        widgets["global_role_combo"].set("Valuation")
        widgets["global_resolve_var"].set("1108099")
        widgets["apply_global_button"].invoke()
        self.root.update_idletasks()

        with mock.patch("smartscc_tools.modules.svl_fix_je_dashboard_page.messagebox.showwarning") as warn_box:
            widgets["repair_all_button"].invoke()
            self.root.update_idletasks()

        warn_box.assert_called_once()
        self.assertEqual(captured_rows, [])
        self.assertTrue(bool(widgets["dialog"].winfo_exists()))
        self.assertEqual(len(self._repair_tree_leaf_ids(widgets)), 2)
        self.assertIn("ITEM-002", widgets["left_list"].item(self._repair_tree_leaf_ids(widgets)[1], "text"))

        widgets["dialog"].destroy()

    def test_repair_dialog_repair_selected_runs_only_selected_ready_rows(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page.root = self.root
        page.context = mock.Mock(global_settings=GlobalSettings())
        page._display_name = "Fixing Unlink SVL - Odoo"
        page._last_repair_dialog_widgets = {}
        page._busy = False
        page._repair_collection = OrderedDict()
        page.status_var = tk.StringVar(master=self.root, value="")
        page._selected_company_id = lambda: 7
        captured_rows: list[list[object]] = []
        page._start_repair = lambda rows: captured_rows.append(rows)  # type: ignore[assignment]
        page._notify_repair_collection_changed = lambda: None
        page._module_settings = SvlFixJeSettings()

        page._open_repair_dialog_for_rows(
            [
                {
                    "row_key": "row-1",
                    "company_id": 7,
                    "item_product_id": 11,
                    "item_code": "ITEM-001",
                    "item_name": "First Item",
                    "amount": 100.0,
                    "date": "2026-03-18",
                    "journal_code": "STJ",
                    "signed_amount": 100.0,
                    "base_reference": "ITEM-001",
                    "base_line_label": "ITEM-001 First Item",
                    "move_id": 0,
                    "move_name": "",
                    "move_state": "",
                    "account_candidates": [
                        SvlDashboardRepairAccountCandidate(
                            code="114001",
                            name="Inventory",
                            source="Category Stock Valuation",
                            role="valuation",
                            field_name="property_stock_valuation_account_id",
                        ),
                        SvlDashboardRepairAccountCandidate(
                            code="1108099",
                            name="Correction",
                            source="Global Extra",
                            role="extra",
                        ),
                    ],
                },
                {
                    "row_key": "row-2",
                    "company_id": 7,
                    "item_product_id": 12,
                    "item_code": "ITEM-002",
                    "item_name": "Second Item",
                    "amount": 50.0,
                    "date": "2026-03-18",
                    "journal_code": "STJ",
                    "signed_amount": 50.0,
                    "base_reference": "ITEM-002",
                    "base_line_label": "ITEM-002 Second Item",
                    "move_id": 0,
                    "move_name": "",
                    "move_state": "",
                    "account_candidates": [],
                }
            ],
            source_title="Repair Collection",
            source_note="Collected rows lintas item.",
        )
        self.root.update_idletasks()

        widgets = page._last_repair_dialog_widgets
        self._select_repair_tree_indices(widgets, 0)
        widgets["global_target_combo"].set("New JE + Relink SVL")
        widgets["global_posting_combo"].set("Auto Post")
        widgets["global_role_combo"].set("Valuation")
        widgets["global_resolve_var"].set("1108099")
        widgets["apply_global_button"].invoke()
        self.root.update_idletasks()

        widgets["repair_selected_button"].invoke()
        self.root.update_idletasks()

        self.assertEqual(len(captured_rows), 1)
        self.assertEqual(len(captured_rows[0]), 1)
        self.assertEqual(captured_rows[0][0].row_key, "row-1")
        self.assertEqual(captured_rows[0][0].debit_account_code, "114001")
        self.assertEqual(captured_rows[0][0].credit_account_code, "1108099")
        self.assertEqual(widgets["draft_rows"][0]["row_status"], "running")
        self.assertTrue(bool(widgets["dialog"].winfo_exists()))
        self.assertEqual(len(self._repair_tree_leaf_ids(widgets)), 2)

        widgets["dialog"].destroy()

    def test_copy_helpers_and_locator_use_tree_meta(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page.root = self.root
        page.status_var = tk.StringVar(master=self.root, value="")
        page._tree_row_meta = {}
        page._tree_active_cell = {}
        page._tree_role_by_widget = {}
        page._latest_base_url = "https://odoo.test"
        frame = tk.Frame(self.root)
        frame.pack(fill="both", expand=True)
        tree = page._build_tree(frame, ("status", "account_code"), selectmode="extended", role="merged")
        copied: list[str] = []
        page._copy_text_to_clipboard = copied.append

        page._fill_tree(
            tree,
            [
                {
                    "values": ("Linked JE header kosong", "114001"),
                    "meta": {
                        "odoo_model": "account.move.line",
                        "odoo_id": 77,
                        "move_id": 501,
                        "repair_candidate": True,
                        "repair_seed": {"row_key": "row-1"},
                    },
                }
            ],
        )
        item_id = tree.get_children()[0]
        tree.selection_set(item_id)
        page._tree_active_cell[tree] = (item_id, "#2")

        page._copy_tree_active_cell(tree)
        self.assertEqual(copied[-1], "114001")

        page._copy_tree_selected_rows(tree)
        self.assertIn("Linked JE header kosong", copied[-1])
        self.assertIn("114001", copied[-1])

        page._copy_tree_locator(tree)
        self.assertEqual(copied[-1], "https://odoo.test/web#id=77&model=account.move.line&view_type=form")

    def test_repair_candidates_require_all_selected_rows_to_be_repairable(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page.root = self.root
        page._tree_row_meta = {}
        page._tree_active_cell = {}
        page._tree_role_by_widget = {}
        frame = tk.Frame(self.root)
        frame.pack(fill="both", expand=True)
        tree = page._build_tree(frame, ("status", "note"), selectmode="extended", role="merged")

        page._fill_tree(
            tree,
            [
                {
                    "values": ("Linked JE header kosong", "repairable"),
                    "meta": {"repair_candidate": True, "repair_seed": {"row_key": "row-1"}},
                },
                {
                    "values": ("Fallback hint", "read only"),
                    "meta": {"repair_candidate": False, "repair_seed": None},
                },
            ],
        )
        first_item, second_item = tree.get_children()

        tree.selection_set((first_item, second_item))
        self.assertEqual(page._repair_candidates_for_tree(tree), [])

        tree.selection_set(first_item)
        candidates = page._repair_candidates_for_tree(tree)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["repair_seed"]["row_key"], "row-1")

    def test_default_repair_date_prefers_today_for_posted_historical_seed(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)

        result = page._default_repair_date_for_seed(
            {
                "date": "2025-10-23",
                "move_id": 435,
                "move_state": "posted",
            }
        )

        self.assertEqual(result, date.today().strftime("%Y-%m-%d"))

    def test_repair_target_options_keep_new_move_first_and_include_rewrite_for_existing_move(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)

        options = page._repair_target_options([{"move_id": 435}])

        self.assertEqual(options[0], ("New JE + Relink SVL", "new_and_relink"))
        self.assertEqual(options[1], ("Rewrite Existing JE", "fill_existing"))

    def test_repair_generated_reference_and_label_use_shared_prefix_and_target_text(self) -> None:
        seed = {
            "item_code": "A-SPWH-0043",
            "item_name": "JOHN JAMESON",
            "base_reference": "A-SPWH-0043",
            "base_line_label": "A-SPWH-0043 JOHN JAMESON",
        }

        reference = SvlFixJeDashboardPage._repair_generated_reference("new_and_relink", seed, prefix="FIX-NEW")
        line_label = SvlFixJeDashboardPage._repair_generated_line_label("fill_existing", seed, prefix="FIX-NEW")

        self.assertEqual(reference, "FIX-NEW New JE + Relink SVL A-SPWH-0043")
        self.assertEqual(line_label, "FIX-NEW Rewrite Existing JE A-SPWH-0043 JOHN JAMESON")

    def test_dashboard_shared_prefix_refresh_updates_only_generated_repair_texts(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page.context = mock.Mock(global_settings=GlobalSettings())
        page._module_settings = SvlFixJeSettings(last_prefix="OLD")
        page._repair_collection = OrderedDict(
            [
                (
                    "row-auto",
                    mock.Mock(
                        draft={
                            "row_key": "row-auto",
                            "target_mode": "new_and_relink",
                            "item_code": "ITEM-001",
                            "item_name": "Baileys",
                            "base_reference": "ITEM-001",
                            "base_line_label": "ITEM-001 Baileys",
                            "reference": "OLD New JE + Relink SVL ITEM-001",
                            "line_label": "OLD New JE + Relink SVL ITEM-001 Baileys",
                            "reference_generated": True,
                            "line_label_generated": True,
                        }
                    ),
                ),
                (
                    "row-manual",
                    mock.Mock(
                        draft={
                            "row_key": "row-manual",
                            "target_mode": "new_and_relink",
                            "item_code": "ITEM-002",
                            "item_name": "Guinness",
                            "base_reference": "ITEM-002",
                            "base_line_label": "ITEM-002 Guinness",
                            "reference": "Manual Ref",
                            "line_label": "Manual Label",
                            "reference_generated": False,
                            "line_label_generated": False,
                        }
                    ),
                ),
            ]
        )
        page._last_repair_dialog_widgets = {}
        page.refresh_database_options = lambda: setattr(page, "_module_settings", SvlFixJeSettings(last_prefix="FIX-NEW"))
        page._notify_repair_collection_changed = lambda: None

        page.on_shared_configuration_updated(database_changed=False, prefix_changed=True)

        auto_row = page._repair_collection["row-auto"].draft
        manual_row = page._repair_collection["row-manual"].draft
        self.assertEqual(auto_row["reference"], "FIX-NEW New JE + Relink SVL ITEM-001")
        self.assertEqual(auto_row["line_label"], "FIX-NEW New JE + Relink SVL ITEM-001 Baileys")
        self.assertEqual(manual_row["reference"], "Manual Ref")
        self.assertEqual(manual_row["line_label"], "Manual Label")

    def test_on_shared_configuration_updated_refreshes_generated_pcb_case1_reference_only(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page._module_settings = SvlFixJeSettings(last_prefix="OLD")
        page._repair_collection = OrderedDict()
        page._pcb_repair_collection = OrderedDict(
            [
                (
                    "row-auto",
                    {
                        "row_key": "row-auto",
                        "pcb_case": "case1",
                        "pcb_case_label": PCB_CASE1_LABEL,
                        "picking_name": "LHPK/IN/7001",
                        "bill_name": "BILL/2026/0001",
                        "item_code": "SKU-001",
                        "item_name": "Produk A",
                        "reference": _pcb_case1_reference(
                            prefix="OLD",
                            picking_name="LHPK/IN/7001",
                            bill_name="BILL/2026/0001",
                            item_code="SKU-001",
                            item_name="Produk A",
                        ),
                        "line_label": _pcb_case1_line_label(item_code="SKU-001", item_name="Produk A"),
                        "reference_generated": True,
                        "line_label_generated": True,
                    },
                ),
                (
                    "row-manual",
                    {
                        "row_key": "row-manual",
                        "pcb_case": "case1",
                        "pcb_case_label": PCB_CASE1_LABEL,
                        "picking_name": "LHPK/IN/7002",
                        "bill_name": "BILL/2026/0002",
                        "item_code": "SKU-002",
                        "item_name": "Produk B",
                        "reference": "Manual Ref",
                        "line_label": "Manual Label",
                        "reference_generated": False,
                        "line_label_generated": False,
                    },
                ),
            ]
        )
        page._last_repair_dialog_widgets = {}
        page._last_pcb_case1_dialog_widgets = {}
        page.refresh_database_options = lambda: setattr(page, "_module_settings", SvlFixJeSettings(last_prefix="PCB-NEW"))
        page._notify_repair_collection_changed = lambda: None

        page.on_shared_configuration_updated(database_changed=False, prefix_changed=True)

        auto_row = page._pcb_repair_collection["row-auto"]
        manual_row = page._pcb_repair_collection["row-manual"]
        self.assertEqual(
            auto_row["reference"],
            _pcb_case1_reference(
                prefix="PCB-NEW",
                picking_name="LHPK/IN/7001",
                bill_name="BILL/2026/0001",
                item_code="SKU-001",
                item_name="Produk A",
            ),
        )
        self.assertEqual(auto_row["line_label"], _pcb_case1_line_label(item_code="SKU-001", item_name="Produk A"))
        self.assertEqual(manual_row["reference"], "Manual Ref")
        self.assertEqual(manual_row["line_label"], "Manual Label")

    def test_repair_account_candidates_merge_category_and_global_extra_without_duplicates(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page.context = mock.Mock(
            global_settings=GlobalSettings(
                repair_account_entries=[
                    RepairAccountEntry(entry_id="repair_1", coa_code="1108099", label="Correction Account")
                ]
            )
        )
        seed = {
            "account_candidates": [
                SvlDashboardRepairAccountCandidate(
                    code="114001",
                    name="Persediaan Barang",
                    source="Category Stock Valuation",
                    role="valuation",
                    field_name="property_stock_valuation_account_id",
                ),
                SvlDashboardRepairAccountCandidate(
                    code="114001",
                    name="Persediaan Barang",
                    source="Category Input",
                    role="input",
                ),
            ]
        }

        candidates = page._repair_account_candidates_for_seed(seed)

        self.assertEqual([candidate.code for candidate in candidates], ["114001", "1108099"])
        self.assertIn("Category Stock Valuation", candidates[0].source)
        self.assertIn("Category Input", candidates[0].source)
        self.assertEqual(candidates[0].role, "valuation")
        self.assertEqual(candidates[0].field_name, "property_stock_valuation_account_id")
        self.assertEqual(candidates[1].source, "Global Extra")

    def test_repair_account_suggestion_uses_valuation_vs_global_extra_by_sign(self) -> None:
        candidates = [
            SvlDashboardRepairAccountCandidate(
                code="114001",
                name="Persediaan Barang",
                source="Category Stock Valuation",
                role="valuation",
                field_name="property_stock_valuation_account_id",
            ),
            SvlDashboardRepairAccountCandidate(
                code="1108099",
                name="Correction Account",
                source="Global Extra",
                role="extra",
            ),
        ]

        positive = SvlFixJeDashboardPage._suggest_repair_account_codes({"signed_amount": 15.0}, candidates)
        negative = SvlFixJeDashboardPage._suggest_repair_account_codes({"signed_amount": -15.0}, candidates)

        self.assertEqual(positive, ("114001", "1108099"))
        self.assertEqual(negative, ("1108099", "114001"))

    def test_repair_account_filter_and_preview_support_scoped_fuzzy_candidates_and_manual_text(self) -> None:
        candidates = [
            SvlDashboardRepairAccountCandidate(
                code="114001",
                name="Persediaan Barang",
                source="Category Stock Valuation",
                role="valuation",
                field_name="property_stock_valuation_account_id",
            ),
            SvlDashboardRepairAccountCandidate(
                code="1108099",
                name="Correction Account",
                source="Global Extra",
                role="extra",
            ),
        ]

        filtered = SvlFixJeDashboardPage._filter_repair_account_candidates("persd barang", candidates)

        self.assertEqual(filtered[0].code, "114001")
        self.assertEqual(
            SvlFixJeDashboardPage._lookup_repair_candidate_preview("9999999", candidates),
            "9999999 - Manual entry (akan resolve exact saat check/run)",
        )

    def test_repair_complete_shows_popup_summary_for_error_batch(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page.root = _FakeRoot()
        page._latest_base_url = ""
        page._repair_refresh_pending = False
        page._recent_repair_results_by_svl_id = {}
        page._last_repair_summary_widgets = {}
        page.status_var = _FakeVar("")
        page._set_busy = lambda busy: None  # noqa: ARG005
        page.start_analysis = lambda: None

        result = SvlDashboardRepairRowResult(
            row_key="row-1",
            status="ERROR",
            item_code="A-SPWH-0043",
            item_name="JOHN JAMESON",
            amount=7513054.56,
            reference="A-SPWH-0043",
            message="Tanggal STJ tidak dapat diubah karena lock date.",
            selected_target_mode="fill_existing",
            effective_date="2025-10-23",
            error_kind="lock_date",
            move_id=435,
            move_name="STJ/2025/10/0435",
            relinked_svl_id=1173078,
            old_move_action="mark_only",
            old_move_id=435,
            old_move_name="STJ/2025/10/0435",
        )

        page._handle_ui_event({"type": "repair_complete", "results": [result], "base_url": "https://odoo.test"})

        self.assertIn("0 berhasil, 1 gagal", page.status_var.get())
        self.assertEqual(page._latest_base_url, "https://odoo.test")
        self.assertEqual(page._recent_repair_results_by_svl_id, {})
        self.assertTrue(page._last_repair_summary_widgets["has_error"])
        self.assertIn("Repair selesai. 0 berhasil, 1 gagal.", page._last_repair_summary_widgets["summary_text"])
        self.assertIn("Transaksi: STJ/2025/10/0435", page._last_repair_summary_widgets["body_text"])
        self.assertIn("Nominal : 7,513,054.56", page._last_repair_summary_widgets["body_text"])

    def test_repair_complete_tracks_recent_successful_relink_for_merged_note(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page.root = _FakeRoot()
        page._latest_base_url = ""
        page._repair_refresh_pending = False
        page._recent_repair_results_by_svl_id = {}
        page._last_repair_summary_widgets = {}
        page.status_var = _FakeVar("")
        page._set_busy = lambda busy: None  # noqa: ARG005
        page.start_analysis = lambda: None

        result = SvlDashboardRepairRowResult(
            row_key="row-1",
            status="POSTED",
            item_code="A-SPWH-0043",
            item_name="JOHN JAMESON",
            amount=7513054.56,
            reference="A-SPWH-0043",
            message="Move baru dibuat dan SVL direlink.",
            selected_target_mode="new_and_relink",
            effective_date=date.today().strftime("%Y-%m-%d"),
            move_id=901,
            move_name="STJ/2026/0901",
            posted=True,
            relinked_svl_id=1173078,
            old_move_action="mark_only",
            old_move_id=435,
            old_move_name="STJ/2025/10/0435",
        )

        page._handle_ui_event({"type": "repair_complete", "results": [result], "base_url": "https://odoo.test"})

        self.assertEqual(page._recent_repair_results_by_svl_id[1173078].old_move_action, "mark_only")
        self.assertFalse(page._last_repair_summary_widgets["has_error"])
        self.assertIn("Transaksi: STJ/2026/0901", page._last_repair_summary_widgets["body_text"])

    def test_repair_complete_marks_collection_row_repaired_and_auto_clears_when_all_done(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page.root = _FakeRoot()
        page._latest_base_url = ""
        page._repair_refresh_pending = False
        page._recent_repair_results_by_svl_id = {}
        page._last_repair_summary_widgets = {}
        page._repair_collection = OrderedDict(
            {
                "row-1": mock.Mock(
                    row_key="row-1",
                    draft={"row_key": "row-1", "row_status": "running", "row_status_message": "Running"},
                )
            }
        )
        page._repair_collection_scope = mock.Mock()
        page._repair_collection_changed_callback = None
        page._active_repair_row_keys = {"row-1"}
        page._last_repair_dialog_widgets = {}
        page.status_var = _FakeVar("")
        page._set_busy = lambda busy: None  # noqa: ARG005
        page.start_analysis = lambda: None
        page.clear_repair_collection = mock.Mock()

        result = SvlDashboardRepairRowResult(
            row_key="row-1",
            status="POSTED",
            item_code="A-SPWH-0043",
            item_name="JOHN JAMESON",
            amount=7513054.56,
            reference="A-SPWH-0043",
            message="Move baru dibuat dan SVL direlink.",
            selected_target_mode="new_and_relink",
            effective_date=date.today().strftime("%Y-%m-%d"),
            move_id=901,
            move_name="STJ/2026/0901",
            posted=True,
            relinked_svl_id=1173078,
        )

        page._handle_ui_event({"type": "repair_complete", "results": [result], "base_url": "https://odoo.test"})

        self.assertEqual(page._repair_collection["row-1"].draft["row_status"], "repaired")
        page.clear_repair_collection.assert_called_once()

    # -------------------------------------------------------------------------
    # Performance fix tests
    # -------------------------------------------------------------------------

    def test_build_sidebar_item_card_registers_frame_in_sidebar_item_frames(self) -> None:
        """_build_sidebar_item_card() harus menyimpan referensi frame ke _sidebar_item_frames[pid]."""
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page._sidebar_item_frames = {}
        page._selected_product_id = 0
        page._select_item = lambda _pid: None

        item = mock.Mock(pid=42, code="RM-001", name="Raw Material A", difference=10.0,
                         svl_orphan_count=1, jnl_orphan_count=0, po_lines=[])
        parent = tk.Frame(self.root)
        parent.pack()

        page._build_sidebar_item_card(parent, item)
        self.root.update_idletasks()

        self.assertIn(42, page._sidebar_item_frames)
        self.assertIsInstance(page._sidebar_item_frames[42], tk.Frame)

    def test_build_sidebar_item_card_does_not_use_winfo_children(self) -> None:
        """Widget clickable dikumpulkan tanpa memanggil winfo_children() di Tcl — harus register semua widget."""
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page._sidebar_item_frames = {}
        page._selected_product_id = 5
        page._select_item = lambda _pid: None

        item = mock.Mock(pid=5, code="WH-002", name="Spare Part", difference=-50.0,
                         svl_orphan_count=2, jnl_orphan_count=1, po_lines=["po1"])
        parent = tk.Frame(self.root)
        parent.pack()

        page._build_sidebar_item_card(parent, item)
        self.root.update_idletasks()

        frame = page._sidebar_item_frames[5]
        # Selected item harus punya border BRAND_PRIMARY
        self.assertEqual(frame.cget("highlightbackground"), T.BRAND_PRIMARY)

    def test_select_item_updates_highlight_without_rebuilding_sidebar(self) -> None:
        """_select_item() hanya harus update highlight + render detail, TIDAK rebuild seluruh sidebar."""
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page._sidebar_item_frames = {}
        page._selected_product_id = 1
        page._select_item = page._select_item  # real method — bind via __get__ workaround below

        # Buat dua frame mock sebagai pengganti card widget nyata
        frame_a = tk.Frame(self.root, highlightbackground=T.BRAND_PRIMARY, highlightthickness=1)
        frame_b = tk.Frame(self.root, highlightbackground=T.BORDER_LIGHT, highlightthickness=1)
        page._sidebar_item_frames[1] = frame_a  # item 1 = currently selected
        page._sidebar_item_frames[2] = frame_b  # item 2 = target selection

        render_calls = []
        page._render_selected_item = lambda: render_calls.append(1)
        rebuild_calls = []
        page.render_item_cards = lambda: rebuild_calls.append(1)

        # Panggil method sesungguhnya via unbound call
        SvlFixJeDashboardPage._select_item(page, 2)

        # Sidebar TIDAK direbuild
        self.assertEqual(rebuild_calls, [], "render_item_cards() seharusnya tidak dipanggil saat _select_item()")
        # Detail panel di-render sekali
        self.assertEqual(len(render_calls), 1, "_render_selected_item() harus dipanggil tepat sekali")
        # Highlight lama dimatikan
        self.assertEqual(frame_a.cget("highlightbackground"), T.BORDER_LIGHT)
        # Highlight baru diaktifkan
        self.assertEqual(frame_b.cget("highlightbackground"), T.BRAND_PRIMARY)
        # State ter-update
        self.assertEqual(page._selected_product_id, 2)

    def test_select_item_same_pid_is_noop(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page.root = _FakeRoot()
        page._selected_product_id = 7
        page._post_paint_prefetch_after_id = "after#prefetch"
        sync_calls: list[bool] = []
        render_calls: list[int] = []
        prefetch_calls: list[int] = []
        page._sync_sidebar_selection = lambda *, ensure_visible=False: sync_calls.append(bool(ensure_visible))
        page._render_selected_item = lambda: render_calls.append(1)
        page._schedule_post_paint_prefetch = lambda *, delay_ms=150: prefetch_calls.append(delay_ms)
        page._sidebar_item_frames = {}

        SvlFixJeDashboardPage._select_item(page, 7)

        self.assertEqual(sync_calls, [])
        self.assertEqual(render_calls, [])
        self.assertEqual(prefetch_calls, [])
        self.assertEqual(page.root.after_cancel_calls, [])
        self.assertEqual(page._post_paint_prefetch_after_id, "after#prefetch")

    def test_sidebar_tree_selected_same_pid_does_not_reenter_select_item(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page._sidebar_tree_syncing_selection = False
        page._selected_product_id = 101
        tree = ttk.Treeview(self.root, show="tree")
        item_id = tree.insert("", "end", text="A-101")
        tree.selection_set(item_id)
        tree.focus(item_id)
        page.sidebar_tree = tree
        page._sidebar_tree_pid_by_item_id = {item_id: 101}
        page._sidebar_tree_item_id_by_pid = {101: [item_id]}
        select_calls: list[int] = []
        page._select_item = lambda pid, **kwargs: select_calls.append(pid)

        page._on_sidebar_tree_selected()

        self.assertEqual(select_calls, [])

    def test_fill_tree_uses_batch_delete_and_repopulates(self) -> None:
        """_fill_tree() harus menghapus semua row lama dan mengisi row baru dengan benar."""
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page._tree_row_meta = {}
        page._tree_active_cell = {}

        parent = tk.Frame(self.root)
        parent.pack(fill="both", expand=True)
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)
        tree = ttk.Treeview(parent, columns=("id", "date", "value"), show="headings")
        tree.grid(row=0, column=0, sticky="nsew")

        # Isi dulu dengan data lama
        for i in range(5):
            tree.insert("", "end", values=(i, f"2026-01-{i+1:02d}", str(i * 10.0)))
        self.assertEqual(len(tree.get_children()), 5)

        # Fill dengan data baru — harus hapus yang lama
        new_rows = [
            {"values": (101, "2026-02-01", "99.00"), "tags": (), "meta": {}},
            {"values": (102, "2026-02-02", "50.00"), "tags": ("svl_orphan",), "meta": {"odoo_id": 102}},
        ]
        page._fill_tree(tree, new_rows)
        self.root.update_idletasks()

        children = tree.get_children()
        self.assertEqual(len(children), 2, "Harus ada tepat 2 row setelah _fill_tree()")
        self.assertEqual(tree.item(children[0], "values"), ("101", "2026-02-01", "99.00"))
        self.assertEqual(tree.item(children[1], "tags"), ("svl_orphan",))
        self.assertEqual(page._tree_row_meta[tree][children[1]]["odoo_id"], 102)

    def test_fill_tree_on_empty_tree_does_not_raise(self) -> None:
        """_fill_tree() pada tree kosong (tree.delete(*()) → no-arg) tidak boleh raise error."""
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page._tree_row_meta = {}
        page._tree_active_cell = {}

        parent = tk.Frame(self.root)
        parent.pack()
        tree = ttk.Treeview(parent, columns=("a",), show="headings")

        # Tree kosong — tidak boleh error
        try:
            page._fill_tree(tree, [{"values": ("x",), "tags": (), "meta": {}}])
        except Exception as exc:
            self.fail(f"_fill_tree() pada tree kosong raised: {exc}")

        self.assertEqual(len(tree.get_children()), 1)

    def test_render_item_cards_clears_sidebar_item_frames_registry(self) -> None:
        """render_item_cards() harus clear _sidebar_item_frames setelah destroy semua widget."""
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        # Pre-populate with stale frames
        page._sidebar_item_frames = {99: tk.Frame(self.root), 100: tk.Frame(self.root)}
        page._latest_snapshot = None
        page._selected_product_id = 0
        page._search_placeholder_active = False
        page.search_var = tk.StringVar(master=self.root, value="")

        # Mock scrollable frame interior
        interior = tk.Frame(self.root)
        interior.pack()
        fake_scroll = mock.Mock()
        fake_scroll.interior = interior
        page.sidebar_scroll = fake_scroll

        page.sidebar_summary_var = tk.StringVar(master=self.root, value="")
        page._filtered_items = lambda: []  # no items
        page._set_empty_detail = lambda: None
        page._render_selected_item = lambda: None

        page.render_item_cards()

        self.assertEqual(page._sidebar_item_frames, {}, "_sidebar_item_frames harus dikosongkan setelah rebuild")

    def test_rebuild_sidebar_tree_defers_first_leaf_batch(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page.root = _FakeRoot()
        page.sidebar_tree = ttk.Treeview(self.root)
        page._sidebar_rebuild_token = 0
        page._sidebar_tree_meta = {}
        page._sidebar_tree_item_id_by_pid = {}
        page._sidebar_tree_pid_by_item_id = {}
        page._sidebar_valuation_open = {}
        page._sidebar_category_open = {}
        page._log_ui_stage = lambda message: None  # noqa: ARG005
        page._format_valuation_summary = lambda group: "2 item"
        page._format_category_summary = lambda group: "2 item"
        page._format_sidebar_item_text = lambda item: getattr(item, "code", "-")
        item_a = mock.Mock(pid=101, code="A-101", difference=10.0)
        item_b = mock.Mock(pid=102, code="A-102", difference=-5.0)
        page._group_sidebar_items = lambda items: [  # noqa: ARG005
            {
                "title": "Automated / Track Inventory",
                "children": [
                    {
                        "title": "Category A",
                        "state_key": "category-a",
                        "children": [],
                        "items": [
                            {"pid": 101, "item": item_a, "amount": 10.0},
                            {"pid": 102, "item": item_b, "amount": -5.0},
                        ],
                    }
                ],
                "items": [],
            }
        ]
        completed: list[str] = []

        page._rebuild_sidebar_tree([item_a, item_b], on_complete=lambda: completed.append("done"), log_stage=True)

        self.assertEqual(completed, [])
        self.assertEqual(page._sidebar_tree_item_id_by_pid, {})
        self.assertEqual(len(page.root.after_calls), 1)

        _delay_ms, callback = page.root.after_calls[0]
        callback()

        self.assertEqual(completed, ["done"])
        self.assertIn(101, page._sidebar_tree_item_id_by_pid)
        self.assertIn(102, page._sidebar_tree_item_id_by_pid)

    def test_debug_select_pcb_sidebar_target_selects_first_cycle_node(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page.root = self.root
        page._is_purchase_cycle_mode = lambda value=None: True
        page._on_pcb_cycle_selected = lambda event=None: None
        page.sidebar_tree = ttk.Treeview(self.root, show="tree")
        group_iid = page.sidebar_tree.insert("", "end", text="Problem")
        cycle_iid = page.sidebar_tree.insert(group_iid, "end", text="PICK/001")
        item_iid = page.sidebar_tree.insert(cycle_iid, "end", text="ITEM-001")
        cycle = mock.Mock(picking_name="PICK/001")
        page._pcb_cycle_by_iid = {cycle_iid: cycle, item_iid: cycle}
        page._pcb_item_by_iid = {item_iid: mock.Mock(product_id=99)}
        page._pcb_cycle_node_ids = {cycle_iid}
        page._pcb_item_node_ids = {item_iid}

        selected = page.debug_select_pcb_sidebar_target()

        self.assertTrue(selected)
        self.assertEqual(page.sidebar_tree.selection(), (cycle_iid,))
        self.assertEqual(page.sidebar_tree.focus(), cycle_iid)

    def test_build_gui_debug_snapshot_captures_sidebar_and_pcb_tables(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page.root = self.root
        page._is_purchase_cycle_mode = lambda value=None: True
        page._resolve_pcb_sidebar_cycle = lambda *args, **kwargs: cycle
        page._build_pcb_cycle_detail_export_payload = lambda current_cycle: {  # noqa: ARG005
            "picking_name": current_cycle.picking_name,
            "document_classification_label": current_cycle.document_classification_label,
        }
        page._busy = False
        page._snapshot_interactive_ready = True
        page._detail_warm_state = "idle"
        page._selected_company_id_value = 755
        page.database_choice_var = tk.StringVar(master=self.root, value="Live ERP - hwgroup_erp [Database Live]")
        page.dataset_mode_var = tk.StringVar(master=self.root, value="Balance Cycle Pembelian")
        page.company_choice_var = tk.StringVar(master=self.root, value="755 - Phoenix")
        page.date_from_var = tk.StringVar(master=self.root, value="")
        page.date_to_var = tk.StringVar(master=self.root, value="")
        page.status_var = tk.StringVar(master=self.root, value="Analisis selesai.")
        page.phase_var = tk.StringVar(master=self.root, value="Analisis selesai.")
        page.sidebar_summary_var = tk.StringVar(master=self.root, value="1 cycle | problem 1")
        page.effective_db_var = tk.StringVar(master=self.root, value="Effective DB: hwgroup_erp")
        page.source_db_var = tk.StringVar(master=self.root, value="Source DB: hwgroup_erp")
        page.search_var = tk.StringVar(master=self.root, value="")
        page.warning_var = tk.StringVar(master=self.root, value="")
        page.latest_log_line_var = tk.StringVar(master=self.root, value="UI snapshot interactive")
        page._pcb_raw_notice_var = tk.StringVar(master=self.root, value="Raw AML lines")
        page._pcb_partner_header_var = tk.StringVar(master=self.root, value="Partner: Vendor A")
        page._pcb_item_header_var = tk.StringVar(master=self.root, value="Item: ITEM-001")
        page._search_placeholder_active = False

        page.sidebar_tree = ttk.Treeview(self.root, show="tree")
        sidebar_group_iid = page.sidebar_tree.insert("", "end", text="Problem")
        cycle_iid = page.sidebar_tree.insert(sidebar_group_iid, "end", text="PICK/001")
        item_iid = page.sidebar_tree.insert(cycle_iid, "end", text="ITEM-001")
        page.sidebar_tree.selection_set(cycle_iid)
        page.sidebar_tree.focus(cycle_iid)

        page.pcb_raw_tree = ttk.Treeview(self.root, columns=("a",), show="headings")
        raw_iid = page.pcb_raw_tree.insert("", "end", values=("raw-1",))
        page.pcb_detail_tree = ttk.Treeview(self.root, columns=("a",), show="headings")
        detail_iid = page.pcb_detail_tree.insert("", "end", values=("detail-1",))
        page.pcb_acct_summary_tree = ttk.Treeview(self.root, columns=("a",), show="headings")
        acct_iid = page.pcb_acct_summary_tree.insert("", "end", values=("acct-1",))

        item_row = mock.Mock(
            product_id=101,
            default_code="ITEM-001",
            product_name="Sample Item",
            valuation_method="automated",
        )
        cycle = mock.Mock(
            picking_name="PICK/001",
            cycle_status="problem",
            document_classification="purchase-backed",
            document_classification_label="Purchase-Backed",
            partner_name="Vendor A",
            purchase_orders=["PO/001"],
            bill_refs=["BILL/001"],
            inventory_types=["purchase"],
            problem_account_count=2,
            info_account_count=1,
            item_rows=[item_row],
        )
        page._pcb_cycle_by_iid = {cycle_iid: cycle, item_iid: cycle}
        page._pcb_item_by_iid = {item_iid: item_row}
        page._pcb_cycle_node_ids = {cycle_iid}
        page._pcb_item_node_ids = {item_iid}
        page._pcb_visible_cycles = [cycle]
        page._latest_snapshot = mock.Mock(
            dataset_mode="purchase_cycle_balance",
            company_id=755,
            purchase_cycles=[cycle],
        )
        page._tree_row_meta = {
            page.pcb_raw_tree: {raw_iid: {"row_key": "raw::1"}},
            page.pcb_detail_tree: {detail_iid: {"row_key": "detail::1", "odoo_id": 901}},
            page.pcb_acct_summary_tree: {acct_iid: {"row_key": "acct::1"}},
        }

        snapshot = page.build_gui_debug_snapshot(max_rows_per_tree=20)

        self.assertTrue(snapshot["purchase_cycle_mode"])
        self.assertEqual(snapshot["selected_sidebar_cycle"]["picking_name"], "PICK/001")
        self.assertEqual(snapshot["selected_sidebar_cycle_payload"]["picking_name"], "PICK/001")
        self.assertEqual(snapshot["trees"]["sidebar"]["row_count"], 3)
        self.assertEqual(snapshot["trees"]["pcb_detail"]["row_count"], 1)
        self.assertEqual(
            snapshot["trees"]["sidebar"]["rows"][0]["children"][0]["meta"]["node_kind"],
            "cycle",
        )

    def test_build_gui_debug_snapshot_includes_pcb_repair_collection_and_live_dialog(self) -> None:
        page = SvlFixJeDashboardPage.__new__(SvlFixJeDashboardPage)
        page.root = self.root
        page._is_purchase_cycle_mode = lambda value=None: True
        page._resolve_pcb_sidebar_cycle = lambda *args, **kwargs: None
        page._busy = False
        page._snapshot_interactive_ready = True
        page._detail_warm_state = "idle"
        page._selected_company_id_value = 755
        page.database_choice_var = tk.StringVar(master=self.root, value="Live ERP - hwgroup_erp [Database Live]")
        page.dataset_mode_var = tk.StringVar(master=self.root, value="Balance Cycle Pembelian")
        page.company_choice_var = tk.StringVar(master=self.root, value="755 - Phoenix")
        page.date_from_var = tk.StringVar(master=self.root, value="")
        page.date_to_var = tk.StringVar(master=self.root, value="")
        page.status_var = tk.StringVar(master=self.root, value="Analisis selesai.")
        page.phase_var = tk.StringVar(master=self.root, value="Analisis selesai.")
        page.sidebar_summary_var = tk.StringVar(master=self.root, value="0 cycle")
        page.effective_db_var = tk.StringVar(master=self.root, value="Effective DB: hwgroup_erp")
        page.source_db_var = tk.StringVar(master=self.root, value="Source DB: hwgroup_erp")
        page.search_var = tk.StringVar(master=self.root, value="")
        page.warning_var = tk.StringVar(master=self.root, value="")
        page.latest_log_line_var = tk.StringVar(master=self.root, value="PCB dialog snapshot")
        page._pcb_raw_notice_var = tk.StringVar(master=self.root, value="Raw AML lines")
        page._pcb_partner_header_var = tk.StringVar(master=self.root, value="Partner: Vendor A")
        page._pcb_item_header_var = tk.StringVar(master=self.root, value="Item: ITEM-001")
        page._search_placeholder_active = False
        page.sidebar_tree = ttk.Treeview(self.root, show="tree")
        page._latest_snapshot = mock.Mock(
            dataset_mode="purchase_cycle_balance",
            company_id=755,
            purchase_cycles=[],
        )
        page._tree_row_meta = {}
        page._pcb_visible_cycles = []
        page._pcb_repair_collection_scope = mock.Mock()
        page._pcb_collection_scope_text = lambda: "Live ERP - hwgroup_erp / 755 - Phoenix"
        page.get_pcb_collection_ui_state = lambda: {
            "has_rows": True,
            "count": 1,
            "is_pcb_mode": True,
            "can_open": True,
        }
        row = {
            "row_key": "case9::1509::ITEM-001",
            "cycle_key": "case9::1509",
            "pcb_case": "case9",
            "picking_name": "PIKKP/IN/01509",
            "item_code": "ITEM-001",
            "item_name": "MIE RAMEN",
            "row_status": "needs_review",
            "review_required": True,
            "review_confirmed": False,
            "account_candidates": [
                SvlDashboardRepairAccountCandidate(
                    code="1108099",
                    name="Clearing",
                    source="PCB Problem",
                    role="other",
                )
            ],
        }
        page._pcb_repair_collection = OrderedDict([(row["row_key"], dict(row))])

        dialog = tk.Toplevel(self.root)
        dialog.title("PCB Repair Collection - 1 row")
        try:
            tree = ttk.Treeview(dialog, columns=("picking",), show="tree headings")
            tree.heading("#0", text="Case")
            tree.heading("picking", text="Picking")
            group_iid = tree.insert("", "end", text="Case 9")
            row_iid = tree.insert(group_iid, "end", text="MIE RAMEN", values=(row["picking_name"],))
            tree.selection_set(row_iid)
            tree.focus(row_iid)

            current_cycle_tree = ttk.Treeview(dialog, columns=("a",), show="headings")
            current_cycle_tree.heading("a", text="Current")
            current_cycle_tree.insert("", "end", values=("cycle-1",))
            simulated_tree = ttk.Treeview(dialog, columns=("a",), show="headings")
            simulated_tree.heading("a", text="Sim")
            simulated_tree.insert("", "end", values=("DR 1108099",))
            projected_tree = ttk.Treeview(dialog, columns=("a",), show="headings")
            projected_tree.heading("a", text="Projected")
            projected_tree.insert("", "end", values=("problem 1108099 -> 0",))

            page._last_pcb_case1_dialog_widgets = {
                "dialog": dialog,
                "tree": tree,
                "row_by_iid": {row_iid: row},
                "result_var": tk.StringVar(master=self.root, value="Ready"),
                "detail_info_var": tk.StringVar(master=self.root, value="Preview case9 selected."),
                "current_cycle_title": tk.Label(dialog, text="Current Cycle"),
                "current_cycle_note_var": tk.StringVar(master=self.root, value="Current balances"),
                "current_cycle_tree": current_cycle_tree,
                "simulated_title": tk.Label(dialog, text="Simulated JE"),
                "simulated_note_var": tk.StringVar(master=self.root, value="Simulation preview"),
                "simulated_editor_note_var": tk.StringVar(master=self.root, value="Editor note"),
                "simulated_tree": simulated_tree,
                "projected_cycle_title": tk.Label(dialog, text="Projected Cycle"),
                "projected_cycle_note_var": tk.StringVar(master=self.root, value="Projected balances"),
                "projected_cycle_tree": projected_tree,
                "summary_vars": {
                    "product_id": tk.StringVar(master=self.root, value="101"),
                    "resolve_account_preview": tk.StringVar(master=self.root, value="1108099 - Clearing"),
                },
                "advanced_vars": {
                    "planned_lines": tk.StringVar(master=self.root, value="DR 1108099 37,620,000"),
                    "guard_messages": tk.StringVar(master=self.root, value="Case 9 wajib review."),
                },
                "edit_date_var": tk.StringVar(master=self.root, value="2026-04-12"),
                "edit_ref_var": tk.StringVar(master=self.root, value="PCB/TEST"),
                "resolve_var": tk.StringVar(master=self.root, value="1108099"),
                "resolve_preview_var": tk.StringVar(master=self.root, value="1108099 - Clearing"),
                "scope_var": tk.StringVar(master=self.root, value="all"),
                "apply_button": tk.Button(dialog, state="normal"),
                "confirm_review_button": tk.Button(dialog, state="normal"),
                "execute_draft_button": tk.Button(dialog, state="disabled"),
                "execute_post_button": tk.Button(dialog, state="disabled"),
                "get_selected_rows": lambda: [row],
            }

            snapshot = page.build_gui_debug_snapshot(max_rows_per_tree=20)
        finally:
            dialog.destroy()

        pcb_collection = snapshot["pcb_repair_collection"]
        self.assertEqual(pcb_collection["count"], 1)
        self.assertEqual(pcb_collection["rows_by_case"]["case9"], 1)
        self.assertEqual(pcb_collection["rows"][0]["account_candidates"][0]["code"], "1108099")
        self.assertEqual(pcb_collection["ui_state"]["count"], 1)
        live_dialog = pcb_collection["live_dialog"]
        self.assertEqual(live_dialog["selected_row_keys"], [row["row_key"]])
        self.assertEqual(live_dialog["summary"]["product_id"], "101")
        self.assertIn("1108099", live_dialog["advanced"]["planned_lines"])
        self.assertEqual(
            live_dialog["trees"]["rows"]["rows"][0]["children"][0]["meta"]["row_key"],
            row["row_key"],
        )
