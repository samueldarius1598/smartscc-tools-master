"""Native Tk dashboard page for SVL Fix JE module."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import date, datetime
from pathlib import Path
import queue
import re
import threading
import textwrap
from time import perf_counter
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from tkinter.scrolledtext import ScrolledText
from typing import Any, Callable

from smartscc_tools.services.odoo.gateway import AsyncOdooJsonRpcClient
from smartscc_tools.features.item_journal.utils import normalize_text
from smartscc_tools.core import theme as T
from smartscc_tools.services.odoo.profiles import (
    DEFAULT_DUMMY_PROFILE_ID,
    FOLLOW_GLOBAL_PROFILE_ID,
    FOLLOW_GLOBAL_LABEL,
    USE_GAS_DEFAULT_LABEL,
    build_module_database_options,
    build_option_maps,
    find_database_profile,
    find_database_profile_by_value,
    normalize_database_profile_id,
    render_database_profile_label,
)
from smartscc_tools.core.module_base import ModuleContext
from smartscc_tools.widgets.collapsible_section import CollapsibleSection, build_compact_preview_text
from smartscc_tools.widgets.log_text import append_bounded_text_lines
from smartscc_tools.widgets.scrollable_frame import ScrollableFrame
from smartscc_tools.widgets.treeview_scroll import bind_treeview_scroll_support
from smartscc_tools.features.svl_fix_je.config import (
    DEFAULT_JOURNAL_CODE,
    DEFAULT_DASHBOARD_DATASET_MODE,
    DEFAULT_REF_PREFIX,
    default_output_browse_dir,
    normalize_dashboard_dataset_mode,
    normalize_view_mode,
)
from smartscc_tools.features.svl_fix_je.dashboard_repair_export import (
    export_pcb_case1_summary_excel,
    build_repair_summary_export_payload,
    export_repair_summary_excel,
    export_repair_summary_html,
)
from smartscc_tools.features.svl_fix_je.dashboard_pcb_export import (
    export_pcb_company_audit_excel,
    export_pcb_cycle_detail_excel,
)
from smartscc_tools.features.svl_fix_je.dashboard_repair_service import SvlDashboardRepairServiceAsync
from smartscc_tools.features.svl_fix_je.dashboard_export import export_dashboard_html, export_dashboard_json
from smartscc_tools.features.svl_fix_je.dashboard_service import SvlDashboardServiceAsync
from smartscc_tools.features.svl_fix_je.models import (
    SvlDashboardCompany,
    SvlDashboardItemDetail,
    SvlDashboardPcbCase1RepairRequest,
    SvlDashboardPcbCase1RepairRow,
    SvlDashboardPcbCase2RepairRequest,
    SvlDashboardPcbCase2RepairRow,
    SvlDashboardProgressSnapshot,
    SvlDashboardRepairAccountCandidate,
    SvlDashboardRepairProgressSnapshot,
    SvlDashboardRepairRequest,
    SvlDashboardRepairRow,
    SvlDashboardRepairRowResult,
    SvlDashboardRequest,
    SvlDashboardSnapshot,
)
from smartscc_tools.features.svl_fix_je.pcb_repair_labels import normalize_pcb_planned_line_label


RuntimeBuilder = Callable[..., tuple[Any, Any]]
SEARCH_PLACEHOLDER = "Cari disini..."
COMPANY_SUGGESTION_LIMIT = 20
COMPANY_AUTODROPDOWN_MIN_CHARS = 3
DASHBOARD_PROGRESS_BAR_TARGET_RATIO = 0.8
DASHBOARD_PROGRESS_BAR_MIN_WIDTH = 320
DASHBOARD_PROGRESS_INFO_MIN_WIDTH = 140
REPAIR_PROGRESS_BAR_TARGET_RATIO = 0.65
REPAIR_PROGRESS_BAR_MIN_WIDTH = 220
REPAIR_PROGRESS_INFO_MIN_WIDTH = 220
PROGRESS_TEXT_WRAP_MIN_WIDTH = 120
REPAIR_VIRTUAL_SELECT_ALL_THRESHOLD = 3000
REPAIR_BULK_APPLY_CHUNK_SIZE = 200
REPAIR_DIALOG_SELECTION_ALL = "__all__"
PCB_CASE1_EXECUTE_SCOPE_SELECTED = "selected"
PCB_CASE1_EXECUTE_SCOPE_ALL = "all"
DETAIL_TAB_COLORS = {
    "default": {"active": T.BRAND_PRIMARY, "inactive": T.BG_CARD},
    "merged": {"active": "#E7D77A", "inactive": "#F6EEB6"},
}
REPAIR_TARGET_ACCOUNT_ROLE_OPTIONS = [
    ("Valuation", "valuation"),
    ("Output", "output"),
    ("Input", "input"),
    ("Cost", "cost"),
    ("Expense", "expense"),
]
REPAIR_TARGET_ACCOUNT_ROLE_COMPATIBILITY_MAP = {
    "inventory": "valuation",
}
REPAIR_TARGET_ACCOUNT_ROLE_LABEL_BY_VALUE = {
    value: label for label, value in REPAIR_TARGET_ACCOUNT_ROLE_OPTIONS
}
REPAIR_TARGET_ACCOUNT_ROLE_VALUE_BY_LABEL = {
    label: value for label, value in REPAIR_TARGET_ACCOUNT_ROLE_OPTIONS
}
REPAIR_ACCOUNT_ROLE_PRIORITY = {
    "valuation": 0,
    "output": 10,
    "input": 20,
    "cost": 30,
    "expense": 40,
    "other": 50,
    "extra": 90,
}
REPAIR_ROW_STATE_LABELS = {
    "incomplete": "Incomplete",
    "needs_review": "Needs Review",
    "ready": "Ready",
    "running": "Running",
    "failed": "Failed",
    "repaired": "Repaired",
}
SIDEBAR_TREE_STYLE = "SvlFixJeSidebar.Treeview"
SIDEBAR_TREE_ROW_HEIGHT = 56
PCB_SIDEBAR_TREE_STYLE = "SvlFixJePcbSidebar.Treeview"
PCB_SIDEBAR_TREE_ROW_HEIGHT = 30
SIDEBAR_ITEM_WRAP_FALLBACK_CHARS = 38
SIDEBAR_ITEM_WRAP_MIN_CHARS = 26
SIDEBAR_ITEM_TITLE_MAX_LINES = 2
DATASET_MODE_ISSUES = "issues"
DATASET_MODE_PURCHASE_CYCLE_BALANCE = "purchase_cycle_balance"
DATASET_MODE_LABELS = {
    "SVL vs Balance Sheet": DATASET_MODE_ISSUES,
    "Balance Cycle Pembelian": DATASET_MODE_PURCHASE_CYCLE_BALANCE,
}
DATASET_MODE_LABEL_BY_VALUE = {value: label for label, value in DATASET_MODE_LABELS.items()}


def _is_subsequence_match(query: str, value: str) -> bool:
    if not query:
        return True
    iterator = iter(value)
    return all(char in iterator for char in query)


def _bounded_damerau_levenshtein(left: str, right: str, *, max_distance: int) -> int:
    if left == right:
        return 0
    if abs(len(left) - len(right)) > max_distance:
        return max_distance + 1
    previous_row = list(range(len(right) + 1))
    current_row = [0] * (len(right) + 1)
    previous_previous_row: list[int] | None = None
    for left_index, left_char in enumerate(left, start=1):
        current_row[0] = left_index
        row_best = current_row[0]
        for right_index, right_char in enumerate(right, start=1):
            cost = 0 if left_char == right_char else 1
            deletion = previous_row[right_index] + 1
            insertion = current_row[right_index - 1] + 1
            substitution = previous_row[right_index - 1] + cost
            distance = min(deletion, insertion, substitution)
            if (
                previous_previous_row is not None
                and left_index > 1
                and right_index > 1
                and left[left_index - 1] == right[right_index - 2]
                and left[left_index - 2] == right[right_index - 1]
            ):
                distance = min(distance, previous_previous_row[right_index - 2] + cost)
            current_row[right_index] = distance
            row_best = min(row_best, distance)
        if row_best > max_distance:
            return max_distance + 1
        previous_previous_row, previous_row, current_row = previous_row, current_row, previous_row
    return previous_row[-1]
REPAIR_EDITABLE_STATES = {"", "incomplete", "needs_review", "ready", "failed"}


@dataclass(frozen=True)
class _RepairCollectionScope:
    database_profile_id: str
    database_label: str
    database_value: str
    company_id: int
    company_label: str

    def matches(self, other: "_RepairCollectionScope | None") -> bool:
        return (
            other is not None
            and normalize_text(self.database_profile_id) == normalize_text(other.database_profile_id)
            and normalize_text(self.database_value) == normalize_text(other.database_value)
            and int(self.company_id or 0) == int(other.company_id or 0)
        )


@dataclass
class _RepairCollectionEntry:
    row_key: str
    scope_database_profile_id: str
    scope_company_id: int
    collected_at_order: int
    latest_snapshot_status: str
    seed: dict[str, Any]
    draft: dict[str, Any] = field(default_factory=dict)


class _RepairAccountPicker(tk.Frame):
    def __init__(
        self,
        parent: tk.Widget,
        *,
        variable: tk.StringVar,
        filter_fn: Callable[[str, list[SvlDashboardRepairAccountCandidate]], list[SvlDashboardRepairAccountCandidate]],
        label_fn: Callable[[SvlDashboardRepairAccountCandidate], str] | None = None,
        on_change: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent, bg=T.BG_CARD)
        self.columnconfigure(0, weight=1)
        self._variable = variable
        self._filter_fn = filter_fn
        self._label_fn = label_fn or (lambda candidate: candidate.display_label)
        self._on_change = on_change or (lambda: None)
        self._all_candidates: list[SvlDashboardRepairAccountCandidate] = []
        self._filtered_candidates: list[SvlDashboardRepairAccountCandidate] = []
        self._suspend_trace = False
        self._dropdown_requested = False

        self.entry = tk.Entry(self, textvariable=self._variable, bg=T.BG_INPUT, relief="solid", bd=1)
        self.entry.grid(row=0, column=0, sticky="ew")
        self.entry.bind("<FocusIn>", self._show_dropdown)
        self.entry.bind("<Down>", self._focus_listbox)
        self.entry.bind("<Escape>", self._hide_dropdown)

        self.dropdown = tk.Frame(self, bg=T.BG_CARD, bd=1, relief="solid", highlightbackground=T.BORDER_LIGHT, highlightthickness=1)
        self.dropdown.grid(row=1, column=0, sticky="ew", pady=(2, 0))
        self.dropdown.grid_remove()
        self.dropdown.columnconfigure(0, weight=1)
        self.candidate_listbox = tk.Listbox(
            self.dropdown,
            height=5,
            exportselection=False,
            font=T.font(T.FONT_SMALL_SIZE),
        )
        self.candidate_listbox.grid(row=0, column=0, sticky="ew")
        self.candidate_listbox.bind("<<ListboxSelect>>", self._apply_selected_candidate)
        self.candidate_listbox.bind("<Double-Button-1>", self._apply_selected_candidate)
        self.candidate_listbox.bind("<Return>", self._apply_selected_candidate)
        self.candidate_listbox.bind("<Escape>", self._hide_dropdown)
        scrollbar = ttk.Scrollbar(self.dropdown, orient="vertical", command=self.candidate_listbox.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.candidate_listbox.configure(yscrollcommand=scrollbar.set)
        self._variable.trace_add("write", self._on_var_changed)

    def set_candidates(self, candidates: list[SvlDashboardRepairAccountCandidate]) -> None:
        self._all_candidates = list(candidates)
        self._refresh_dropdown()

    def get_value(self) -> str:
        return normalize_text(self._variable.get())

    def set_value(self, value: str) -> None:
        self._suspend_trace = True
        try:
            self._variable.set(normalize_text(value))
        finally:
            self._suspend_trace = False
        self._refresh_dropdown()

    def _on_var_changed(self, *_args: Any) -> None:
        if self._suspend_trace:
            return
        if normalize_text(self._variable.get()):
            self._dropdown_requested = True
        self._refresh_dropdown()
        self._on_change()

    def _refresh_dropdown(self) -> None:
        query = normalize_text(self._variable.get())
        self._filtered_candidates = self._filter_fn(query, self._all_candidates)
        self.candidate_listbox.delete(0, "end")
        for candidate in self._filtered_candidates[:8]:
            self.candidate_listbox.insert("end", self._label_fn(candidate))
        if self._filtered_candidates and (self._dropdown_requested or bool(query)):
            self.dropdown.grid()
        else:
            self.dropdown.grid_remove()

    def _apply_selected_candidate(self, _event: tk.Event | None = None) -> str | None:
        selection = self.candidate_listbox.curselection()
        if not selection:
            return None
        index = int(selection[0])
        if index < 0 or index >= len(self._filtered_candidates):
            return None
        self.set_value(self._filtered_candidates[index].code)
        self.entry.icursor("end")
        self.entry.focus_set()
        self._hide_dropdown()
        self._on_change()
        return "break"

    def _focus_listbox(self, _event: tk.Event | None = None) -> str | None:
        if not self._filtered_candidates:
            self._dropdown_requested = True
            self._refresh_dropdown()
        if not self._filtered_candidates:
            return None
        self.candidate_listbox.focus_set()
        self.candidate_listbox.selection_clear(0, "end")
        self.candidate_listbox.selection_set(0)
        self.candidate_listbox.activate(0)
        return "break"

    def _show_dropdown(self, _event: tk.Event | None = None) -> str | None:
        self._dropdown_requested = True
        self._refresh_dropdown()
        return None

    def _hide_dropdown(self, _event: tk.Event | None = None) -> str | None:
        self._dropdown_requested = False
        self.dropdown.grid_remove()
        return "break"


class SvlFixJeDashboardPage:
    _DETAIL_RENDER_BATCH_SIZE = 120
    _DETAIL_RENDER_BUDGET_MS = 8.0
    _SIDEBAR_ITEM_BATCH_SIZE = 10
    _SIDEBAR_ITEM_BUDGET_MS = 5.0
    _PCB_STATUS_ICON = {"problem": "\u274c", "partial": "\u26a0\ufe0f", "healthy": "\u2705"}
    _PCB_STATUS_LABEL = {"problem": "Cycle Bermasalah", "partial": "Cycle Sebagian", "healthy": "Cycle Sehat"}
    _PCB_PARTIAL_GROUP_ORDER = [
        "clearing_reclass",
        "bill_unpaid",
        "bill_unmatched",
        "payment_unreconciled",
        "partial_info",
        "partial_other",
    ]
    _PCB_PARTIAL_GROUP_LABEL = {
        "clearing_reclass": "Clearing via Jurnal Reclass",
        "bill_unpaid": "Bill Belum Paid",
        "bill_unmatched": "Bill Belum Matching",
        "payment_unreconciled": "Payment Belum Reconcile",
        "partial_info": "Info Account / Variance",
        "partial_other": "Partial Lainnya",
    }
    _PCB_PROBLEM_CASE_ORDER = [
        "case1",
        "case2",
        "case3",
        "case4",
        "case5",
        "case6",
        "case8a",
        "case8b",
        "case9",
        "case10",
        "edge_partial_bill",
        "edge_return_no_credit_memo",
        "edge_stj_corrupt",
        "case_lainnya",
    ]
    _PCB_PROBLEM_CASE_LABEL = {
        "case1": "Case 1 - STJ Bill Miss Match (Clearing - Suspend)",
        "case2": "Case 2 - STJ Bill Price Diff (Suspend - Suspend)",
        "case3": "Case 3 - STJ Bill Hit Expenses (Clearing - Expenses)",
        "case4": "Case 4 - STJ Bill Hit Expenses (Suspend - Expenses)",
        "case5": "Case 5 - Pemulihan SVL (Inventory - Suspense)",
        "case6": "Case 6 - Pemulihan SVL (Inventory - Bill Expense)",
        "case8a": "Case 8A - Full Return Value Mismatch",
        "case8b": "Case 8B - Partial Return Value Mismatch",
        "case9": "Case 9 - UoM Scale Mismatch",
        "case10": "Case 10 - Belum Ada Bill Vendor",
        "edge_partial_bill": "Edge - Partial Bill",
        "edge_return_no_credit_memo": "Edge - Return No Credit Memo",
        "edge_stj_corrupt": "Edge - STJ Corrupt",
        "case_lainnya": "Case Lainnya",
    }
    _PCB_COGS_VARIANCE_CODES = frozenset({"5101010"})
    _PCB_SIMULATION_FIXED_ACCOUNTS = {
        "1108099": ("Clearing - System Pending Entries", "PCB Clearing Default", "other"),
        "2103006": ("Hutang Suspensed Pengadaan Barang/Jasa / Suspensed", "PCB Suspend Default", "other"),
    }

    def __init__(
        self,
        parent: tk.Frame,
        context: ModuleContext,
        *,
        state_store: Any,
        runtime_builder: RuntimeBuilder,
        display_name: str,
    ) -> None:
        self.parent = parent
        self.context = context
        self.root = parent.winfo_toplevel()
        self.logger = context.logger
        self._state_store = state_store
        self._runtime_builder = runtime_builder
        self._display_name = display_name
        self._module_settings = self._state_store.load()

        self._db_label_by_profile_id: dict[str, str] = {}
        self._db_profile_id_by_label: dict[str, str] = {}
        self._company_by_label: dict[str, SvlDashboardCompany] = {}
        self._company_labels: list[str] = []
        self._company_search_cache: dict[str, str] = {}
        self._company_active_labels: list[str] = []
        self._company_commit_after_id: str | None = None
        self._company_dropdown_after_id: str | None = None
        self._company_last_query_for_autodrop = ""
        self._companies_loaded_for_profile_id = ""
        self._latest_snapshot: SvlDashboardSnapshot | None = None
        self._latest_analysis_request: SvlDashboardRequest | None = None
        self._latest_analysis_profile_id = ""
        self._selected_product_id: int = 0
        self._selected_company_id_value = int(self._module_settings.dashboard_company_id or 0)
        self._sidebar_category_open: dict[str, bool] = {}
        self._sidebar_valuation_open: dict[str, bool] = {}
        self._sidebar_item_frames: dict[int, tk.Frame] = {}
        self._sidebar_tree_meta: dict[str, dict[str, Any]] = {}
        self._sidebar_tree_item_id_by_pid: dict[int, list[str]] = {}
        self._sidebar_tree_pid_by_item_id: dict[str, int] = {}
        self._sidebar_tree_syncing_selection = False
        self._sidebar_filter_after_id: str | None = None
        self._latest_base_url = ""
        self._active_detail_tab = "svl"
        self._busy = False
        self._poll_after_id: str | None = None
        self._poll_active = False
        self._snapshot_apply_after_id: str | None = None
        self._pending_snapshot_to_apply: SvlDashboardSnapshot | None = None
        self._post_paint_prefetch_after_id: str | None = None
        self._post_paint_prefetch_cache_key: tuple[str, int, str, str, int] | None = None
        self._snapshot_interactive_ready = False
        self._detail_render_after_id: str | None = None
        self._detail_render_token: int = 0
        self._detail_render_allow_autoload = True
        self._detail_session_token: int = 0
        self._detail_warm_state = "idle"
        self._detail_warm_target_keys: set[tuple[str, int, str, str, int]] = set()
        self._selected_item_render_token: int = 0
        self._ui_stage_seq: int = 0
        self._current_detail_item: Any | None = None
        self._current_detail_payload: SvlDashboardItemDetail | None = None
        self._detail_cache: dict[tuple[str, int, str, str, int], SvlDashboardItemDetail] = {}
        self._detail_pending_keys: set[tuple[str, int, str, str, int]] = set()
        self._detail_error_by_key: dict[tuple[str, int, str, str, int], str] = {}
        self._current_detail_repair_seeds_by_svl_id: dict[int, dict[str, Any]] = {}
        self._dirty_detail_tabs: set[str] = set()
        self._tree_row_meta: dict[Any, dict[str, dict[str, Any]]] = {}
        self._tree_active_cell: dict[Any, tuple[str, str] | None] = {}
        self._tree_role_by_widget: dict[Any, str] = {}
        self._detail_tab_buttons: dict[str, tk.Button] = {}
        self._detail_tab_frames: dict[str, tk.Frame] = {}
        self._merged_group_open: dict[str, bool] = {}
        self._repair_refresh_pending = False
        self._analysis_refresh_pending = False
        self._recent_repair_results_by_svl_id: dict[int, SvlDashboardRepairRowResult] = {}
        self._last_repair_dialog_widgets: dict[str, Any] = {}
        self._last_repair_summary_widgets: dict[str, Any] = {}
        self._repair_collection: OrderedDict[str, _RepairCollectionEntry] = OrderedDict()
        self._repair_collection_scope: _RepairCollectionScope | None = None
        self._repair_collection_sequence = 0
        self._repair_collection_changed_callback: Callable[[], None] | None = None
        # PCB Repair Collection — terpisah dari SVL collection
        self._pcb_repair_collection: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._pcb_repair_collection_scope: _RepairCollectionScope | None = None
        self._pcb_repair_collection_sequence: int = 0
        self._pcb_visible_cycles: list[Any] = []
        self._pcb_cycle_node_ids: set[str] = set()
        self._pcb_item_node_ids: set[str] = set()
        self._active_repair_row_keys: set[str] = set()
        self._sync_height_after_id: str | None = None
        self._last_content_height: int = -1
        self._sidebar_all_item_ids: dict[int, list[str]] | None = None
        self._sidebar_rebuild_token: int = 0

        self.log_queue: queue.Queue[str] = queue.Queue()
        self.ui_queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self.worker: threading.Thread | None = None

        self.database_choice_var = tk.StringVar(value="")
        self.dataset_mode_var = tk.StringVar(
            value=DATASET_MODE_LABEL_BY_VALUE.get(
                normalize_dashboard_dataset_mode(getattr(self._module_settings, "dashboard_dataset_mode", DEFAULT_DASHBOARD_DATASET_MODE)),
                "SVL vs Balance Sheet",
            )
        )
        self.include_inventory_accounts_var = tk.BooleanVar(
            value=bool(getattr(self._module_settings, "dashboard_include_inventory_accounts", True))
        )
        self.include_non_inventory_accounts_var = tk.BooleanVar(
            value=bool(getattr(self._module_settings, "dashboard_include_non_inventory_accounts", True))
        )
        self.company_choice_var = tk.StringVar(value="")
        self.date_from_var = tk.StringVar(value=self._module_settings.dashboard_date_from)
        self.date_to_var = tk.StringVar(value=self._module_settings.dashboard_date_to)
        self.search_var = tk.StringVar(value="")
        self.sidebar_summary_var = tk.StringVar(value="0 item | Total 0.00")
        self._pcb_show_problem_var = tk.IntVar(value=1)
        self._pcb_show_partial_var = tk.IntVar(value=1)
        self._pcb_show_healthy_var = tk.IntVar(value=0)
        self._pcb_uom_filter_var = tk.StringVar(value="Semua UoM")
        self._pcb_problem_codes_var = tk.StringVar(value=normalize_text(getattr(self._module_settings, "pcb_problem_codes", "2103006,1108099")) or "2103006,1108099")
        self._pcb_info_codes_var = tk.StringVar(value=normalize_text(getattr(self._module_settings, "pcb_info_codes", "11120003")) or "11120003")
        self.latest_log_line_var = tk.StringVar(value="Belum ada log dashboard.")
        self.status_var = tk.StringVar(value="Pilih company lalu jalankan Analyze.")
        self.phase_var = tk.StringVar(value="Idle")
        self.percent_var = tk.StringVar(value="0%")
        self.warning_var = tk.StringVar(value="")
        self.database_notice_var = tk.StringVar(value="")
        self.effective_db_var = tk.StringVar(value="Effective DB: -")
        self.sidebar_card_title_var = tk.StringVar(value="SVL vs Balance Sheet")
        self.detail_card_title_var = tk.StringVar(value="Detail Produk")
        self.header_code_var = tk.StringVar(value="NO CODE")
        self.header_name_var = tk.StringVar(value="Belum ada hasil analisis.")
        self.header_meta_var = tk.StringVar(value="Pilih company dan jalankan Analyze.")
        self.source_db_var = tk.StringVar(value="Source DB: -")
        self.position_var = tk.StringVar(value="-")
        self.kpi_svl_var = tk.StringVar(value="0.00")
        self.kpi_bs_var = tk.StringVar(value="0.00")
        self.kpi_diff_var = tk.StringVar(value="0.00")
        self.kpi_po_bill_var = tk.StringVar(value="0.00")
        self.kpi_svl_title_var = tk.StringVar(value="Nilai SVL")
        self.kpi_bs_title_var = tk.StringVar(value="Saldo Balance Sheet")
        self.kpi_diff_title_var = tk.StringVar(value="Selisih (SVL - BS)")
        self.kpi_po_bill_title_var = tk.StringVar(value="PO - Bill")
        self.kpi_sub_var = tk.StringVar(value="Belum ada item terpilih.")
        self.compare_summary_var = tk.StringVar(value="")
        self.current_asset_summary_var = tk.StringVar(value="")
        self.company_total_primary_title_var = tk.StringVar(value="Total SVL")
        self.company_total_primary_secondary_label_var = tk.StringVar(value="Qty")
        self.company_total_secondary_title_var = tk.StringVar(value="Total BS Persediaan")
        self.company_total_diff_title_var = tk.StringVar(value="Selisih Total (SVL - BS)")
        self.company_total_aux_title_var = tk.StringVar(value="Belum Terpetakan")
        self.company_total_svl_value_var = tk.StringVar(value="0.00")
        self.company_total_svl_qty_var = tk.StringVar(value="0.00")
        self.company_total_bs_var = tk.StringVar(value="0.00")
        self.company_total_diff_var = tk.StringVar(value="0.00")
        self.company_total_unmapped_var = tk.StringVar(value="0.00")
        self.company_coa_title_var = tk.StringVar(value="COA Persediaan Company")
        self.company_coa_total_var = tk.StringVar(value="Total Balance Persediaan: 0.00")
        self.company_coa_notice_var = tk.StringVar(value="Isi daftar COA persediaan manual di Settings untuk melihat summary Balance Sheet.")
        self.analysis_notice_var = tk.StringVar(value="")
        self.analysis_label_vars = [
            tk.StringVar(value="SVL tanpa Journal Entry"),
            tk.StringVar(value="Journal Entry tanpa SVL"),
            tk.StringVar(value="Selisih lainnya"),
        ]
        self._search_placeholder_active = False

        self.progress_value = tk.DoubleVar(value=0.0)
        self._analysis_rows: list[tuple[tk.Canvas, tk.StringVar, int, int]] = []
        self._analysis_values: tuple[float, float, float] = (0.0, 0.0, 0.0)
        self._content_min_height = 560

        self._build_ui()
        self.resume()

    @staticmethod
    def _debug_json_ready(value: Any) -> Any:
        if is_dataclass(value):
            return SvlFixJeDashboardPage._debug_json_ready(asdict(value))
        if isinstance(value, dict):
            return {
                str(key): SvlFixJeDashboardPage._debug_json_ready(val)
                for key, val in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [SvlFixJeDashboardPage._debug_json_ready(item) for item in value]
        if isinstance(value, (set, frozenset)):
            return [
                SvlFixJeDashboardPage._debug_json_ready(item)
                for item in sorted(value, key=lambda item: repr(item))
            ]
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        return str(value)

    @staticmethod
    def _treeview_descendant_count(tree: ttk.Treeview, parent: str = "") -> int:
        total = 0
        for item_id in tree.get_children(parent):
            total += 1
            total += SvlFixJeDashboardPage._treeview_descendant_count(tree, item_id)
        return total

    @classmethod
    def _build_treeview_widget_node(
        cls,
        tree: ttk.Treeview,
        item_id: str,
        *,
        selected_ids: set[str],
        focus_item_id: str,
        meta_map: dict[str, dict[str, Any]],
        captured_count: list[int],
        max_rows: int,
    ) -> dict[str, Any] | None:
        if max_rows > 0 and captured_count[0] >= max_rows:
            return None
        item_state = tree.item(item_id)
        captured_count[0] += 1
        node: dict[str, Any] = {
            "iid": item_id,
            "parent_iid": tree.parent(item_id),
            "text": item_state.get("text", ""),
            "values": list(item_state.get("values", ()) or ()),
            "tags": list(item_state.get("tags", ()) or ()),
            "open": bool(item_state.get("open", False)),
            "selected": item_id in selected_ids,
            "focused": item_id == focus_item_id,
            "children": [],
        }
        if item_id in meta_map:
            node["meta"] = cls._debug_json_ready(meta_map[item_id])
        for child_id in tree.get_children(item_id):
            child_node = cls._build_treeview_widget_node(
                tree,
                child_id,
                selected_ids=selected_ids,
                focus_item_id=focus_item_id,
                meta_map=meta_map,
                captured_count=captured_count,
                max_rows=max_rows,
            )
            if child_node is None:
                node["truncated_children"] = True
                break
            node["children"].append(child_node)
        return node

    def _build_treeview_widget_state(
        self,
        tree: ttk.Treeview | None,
        *,
        meta_map: dict[str, dict[str, Any]] | None = None,
        max_rows: int = 0,
    ) -> dict[str, Any] | None:
        if tree is None:
            return None
        try:
            if not bool(tree.winfo_exists()):
                return None
            selected_ids = {str(item_id) for item_id in tree.selection()}
            focus_item_id = str(tree.focus() or "")
            displaycolumns = tree["displaycolumns"]
            captured_count = [0]
            rows: list[dict[str, Any]] = []
            truncated = False
            for item_id in tree.get_children(""):
                node = self._build_treeview_widget_node(
                    tree,
                    item_id,
                    selected_ids=selected_ids,
                    focus_item_id=focus_item_id,
                    meta_map=dict(meta_map or {}),
                    captured_count=captured_count,
                    max_rows=max(0, int(max_rows or 0)),
                )
                if node is None:
                    truncated = True
                    break
                rows.append(node)
            return {
                "widget_class": tree.winfo_class(),
                "columns": list(tree["columns"] or ()),
                "displaycolumns": displaycolumns if isinstance(displaycolumns, str) else list(displaycolumns or ()),
                "show": str(tree.cget("show") or ""),
                "selection": list(tree.selection() or ()),
                "focus_item_id": focus_item_id,
                "row_count": self._treeview_descendant_count(tree),
                "captured_row_count": int(captured_count[0]),
                "truncated": truncated,
                "rows": rows,
            }
        except tk.TclError:
            return None

    def _build_pcb_sidebar_debug_meta(self) -> dict[str, dict[str, Any]]:
        meta_map: dict[str, dict[str, Any]] = {}
        cycle_by_iid = dict(getattr(self, "_pcb_cycle_by_iid", {}) or {})
        item_by_iid = dict(getattr(self, "_pcb_item_by_iid", {}) or {})
        cycle_node_ids = set(getattr(self, "_pcb_cycle_node_ids", set()) or set())
        item_node_ids = set(getattr(self, "_pcb_item_node_ids", set()) or set())
        for item_id, cycle in cycle_by_iid.items():
            item_row = item_by_iid.get(item_id)
            node_kind = "group"
            if item_id in item_node_ids or item_row is not None:
                node_kind = "item"
            elif item_id in cycle_node_ids:
                node_kind = "cycle"
            meta_map[item_id] = {
                "node_kind": node_kind,
                "picking_name": normalize_text(getattr(cycle, "picking_name", "")),
                "cycle_status": normalize_text(getattr(cycle, "cycle_status", "")),
                "document_classification": normalize_text(getattr(cycle, "document_classification", "")),
                "document_classification_label": normalize_text(getattr(cycle, "document_classification_label", "")),
                "partner_name": normalize_text(getattr(cycle, "partner_name", "")),
                "purchase_orders": list(getattr(cycle, "purchase_orders", None) or []),
                "bill_refs": list(getattr(cycle, "bill_refs", None) or []),
                "inventory_types": list(getattr(cycle, "inventory_types", None) or []),
            }
            if item_row is not None:
                meta_map[item_id].update(
                    {
                        "product_id": int(getattr(item_row, "product_id", 0) or 0),
                        "default_code": normalize_text(getattr(item_row, "default_code", "")),
                        "product_name": normalize_text(getattr(item_row, "product_name", "")),
                        "valuation_method": normalize_text(getattr(item_row, "valuation_method", "")),
                    }
                )
        return meta_map

    def debug_select_company(self, company_id: int) -> bool:
        target_company_id = int(company_id or 0)
        if target_company_id <= 0:
            return False
        label = self._label_for_company_id(target_company_id)
        if not label:
            return False
        self.company_choice_var.set(label)
        self._selected_company_id_value = target_company_id
        self._commit_company_selection()
        return True

    def debug_select_pcb_sidebar_target(self, *, picking_name: str = "", node_kind: str = "cycle") -> bool:
        if not self._is_purchase_cycle_mode() or not hasattr(self, "sidebar_tree"):
            return False
        tree = self.sidebar_tree
        cycle_by_iid = dict(getattr(self, "_pcb_cycle_by_iid", {}) or {})
        cycle_node_ids = set(getattr(self, "_pcb_cycle_node_ids", set()) or set())
        item_node_ids = set(getattr(self, "_pcb_item_node_ids", set()) or set())
        target_kind = normalize_text(node_kind).lower() or "cycle"
        target_picking_name = normalize_text(picking_name).lower()
        stack = list(tree.get_children(""))
        while stack:
            item_id = stack.pop(0)
            stack[0:0] = list(tree.get_children(item_id))
            cycle = cycle_by_iid.get(item_id)
            if cycle is None:
                continue
            if target_kind == "cycle" and item_id not in cycle_node_ids:
                continue
            if target_kind == "item" and item_id not in item_node_ids:
                continue
            if target_picking_name and normalize_text(getattr(cycle, "picking_name", "")).lower() != target_picking_name:
                continue
            tree.selection_set(item_id)
            tree.focus(item_id)
            try:
                tree.see(item_id)
            except tk.TclError:
                pass
            try:
                tree.event_generate("<<TreeviewSelect>>")
            except tk.TclError:
                self._on_pcb_cycle_selected()
            return True
        return False

    @staticmethod
    def _debug_string_var_value(value: Any) -> str:
        if value is None:
            return ""
        getter = getattr(value, "get", None)
        if callable(getter):
            try:
                return str(getter() or "")
            except Exception:  # noqa: BLE001
                return ""
        return str(value or "")

    @staticmethod
    def _debug_widget_text(widget: Any) -> str:
        if widget is None:
            return ""
        try:
            return str(widget.cget("text") or "")
        except Exception:  # noqa: BLE001
            return ""

    @staticmethod
    def _debug_widget_state(widget: Any) -> str:
        if widget is None:
            return ""
        try:
            return str(widget.cget("state") or "")
        except Exception:  # noqa: BLE001
            return ""

    def _build_pcb_repair_dialog_debug_snapshot(self, *, max_rows_per_tree: int = 0) -> dict[str, Any] | None:
        widgets = dict(getattr(self, "_last_pcb_case1_dialog_widgets", {}) or {})
        dialog = widgets.get("dialog")
        if dialog is None:
            return None
        try:
            if not bool(dialog.winfo_exists()):
                return None
        except Exception:  # noqa: BLE001
            return None

        row_by_iid = dict(widgets.get("row_by_iid", {}) or {})
        dialog_tree = widgets.get("tree")
        dialog_tree_meta = {
            item_id: {
                "row_key": normalize_text(row.get("row_key", "")),
                "cycle_key": normalize_text(row.get("cycle_key", "")),
                "pcb_case": normalize_text(row.get("pcb_case", "")),
                "picking_name": normalize_text(row.get("picking_name", "")),
                "item_code": normalize_text(row.get("item_code", "")),
                "item_name": normalize_text(row.get("item_name", "")),
                "row_status": normalize_text(row.get("row_status", "")),
                "review_required": bool(row.get("review_required")),
                "review_confirmed": bool(row.get("review_confirmed")),
            }
            for item_id, row in row_by_iid.items()
        }

        selected_rows_payload: list[Any] = []
        get_selected_rows = widgets.get("get_selected_rows")
        if callable(get_selected_rows):
            try:
                selected_rows_payload = self._debug_json_ready(list(get_selected_rows()))
            except Exception:  # noqa: BLE001
                selected_rows_payload = []
        selected_row_keys = [
            normalize_text(row.get("row_key", ""))
            for row in selected_rows_payload
            if isinstance(row, dict) and normalize_text(row.get("row_key", ""))
        ]

        summary_vars = {
            str(field_name): self._debug_string_var_value(variable)
            for field_name, variable in dict(widgets.get("summary_vars", {}) or {}).items()
        }
        advanced_vars = {
            str(field_name): self._debug_string_var_value(variable)
            for field_name, variable in dict(widgets.get("advanced_vars", {}) or {}).items()
        }

        return {
            "title": str(dialog.title() or ""),
            "geometry": str(dialog.geometry() or ""),
            "result_text": self._debug_string_var_value(widgets.get("result_var")),
            "detail_info_text": self._debug_string_var_value(widgets.get("detail_info_var")),
            "current_cycle_title": self._debug_widget_text(widgets.get("current_cycle_title")),
            "current_cycle_note_text": self._debug_string_var_value(widgets.get("current_cycle_note_var")),
            "simulated_title": self._debug_widget_text(widgets.get("simulated_title")),
            "simulated_note_text": self._debug_string_var_value(widgets.get("simulated_note_var")),
            "simulated_editor_note_text": self._debug_string_var_value(widgets.get("simulated_editor_note_var")),
            "projected_cycle_title": self._debug_widget_text(widgets.get("projected_cycle_title")),
            "projected_cycle_note_text": self._debug_string_var_value(widgets.get("projected_cycle_note_var")),
            "edit_date": self._debug_string_var_value(widgets.get("edit_date_var")),
            "edit_reference": self._debug_string_var_value(widgets.get("edit_ref_var")),
            "resolve_input": self._debug_string_var_value(widgets.get("resolve_var")),
            "resolve_preview": self._debug_string_var_value(widgets.get("resolve_preview_var")),
            "execute_scope": self._debug_string_var_value(widgets.get("scope_var")),
            "selected_row_keys": selected_row_keys,
            "selected_rows": selected_rows_payload,
            "button_state": {
                "apply": self._debug_widget_state(widgets.get("apply_button")),
                "confirm_review": self._debug_widget_state(widgets.get("confirm_review_button")),
                "execute_draft": self._debug_widget_state(widgets.get("execute_draft_button")),
                "execute_post": self._debug_widget_state(widgets.get("execute_post_button")),
            },
            "summary": summary_vars,
            "advanced": advanced_vars,
            "trees": {
                "rows": self._build_treeview_widget_state(
                    dialog_tree,
                    meta_map=dialog_tree_meta,
                    max_rows=max_rows_per_tree,
                ),
                "current_cycle": self._build_treeview_widget_state(
                    widgets.get("current_cycle_tree"),
                    meta_map={},
                    max_rows=max_rows_per_tree,
                ),
                "simulated": self._build_treeview_widget_state(
                    widgets.get("simulated_tree"),
                    meta_map={},
                    max_rows=max_rows_per_tree,
                ),
                "projected_cycle": self._build_treeview_widget_state(
                    widgets.get("projected_cycle_tree"),
                    meta_map={},
                    max_rows=max_rows_per_tree,
                ),
            },
        }

    def _build_pcb_repair_collection_debug_snapshot(self, *, max_rows_per_tree: int = 0) -> dict[str, Any]:
        collection = getattr(self, "_pcb_repair_collection", OrderedDict()) or OrderedDict()
        row_payload = [self._debug_json_ready(dict(row)) for row in collection.values()]
        rows_by_case: dict[str, int] = {}
        review_required_count = 0
        review_confirmed_count = 0
        for row in row_payload:
            if not isinstance(row, dict):
                continue
            case_key = normalize_text(row.get("pcb_case", "")).lower() or "unknown"
            rows_by_case[case_key] = int(rows_by_case.get(case_key, 0)) + 1
            if bool(row.get("review_required")):
                review_required_count += 1
            if bool(row.get("review_confirmed")):
                review_confirmed_count += 1

        ui_state: dict[str, Any] = {}
        get_ui_state = getattr(self, "get_pcb_collection_ui_state", None)
        if callable(get_ui_state):
            try:
                state_value = get_ui_state()
                if isinstance(state_value, dict):
                    ui_state = self._debug_json_ready(dict(state_value))
            except Exception:  # noqa: BLE001
                ui_state = {}

        scope_text = ""
        get_scope_text = getattr(self, "_pcb_collection_scope_text", None)
        if callable(get_scope_text):
            try:
                scope_text = str(get_scope_text() or "")
            except Exception:  # noqa: BLE001
                scope_text = ""

        return {
            "count": len(collection),
            "scope_text": scope_text,
            "scope": self._debug_json_ready(getattr(self, "_pcb_repair_collection_scope", None)),
            "ui_state": ui_state,
            "rows_by_case": rows_by_case,
            "review_required_count": review_required_count,
            "review_confirmed_count": review_confirmed_count,
            "rows": row_payload,
            "live_dialog": self._build_pcb_repair_dialog_debug_snapshot(max_rows_per_tree=max_rows_per_tree),
        }

    def build_gui_debug_snapshot(
        self,
        *,
        max_rows_per_tree: int = 0,
        include_selected_cycle_payload: bool = True,
    ) -> dict[str, Any]:
        purchase_cycle_mode = bool(self._is_purchase_cycle_mode())
        sidebar_tree = getattr(self, "sidebar_tree", None)
        selected_sidebar_item_ids = list(sidebar_tree.selection() or ()) if sidebar_tree is not None else []
        selected_cycle = self._resolve_pcb_sidebar_cycle(fallback_item_ids=selected_sidebar_item_ids) if purchase_cycle_mode else None
        selected_item_row = None
        if purchase_cycle_mode and selected_sidebar_item_ids:
            selected_item_row = getattr(self, "_pcb_item_by_iid", {}).get(selected_sidebar_item_ids[0])
        selected_cycle_snapshot = None
        if selected_cycle is not None:
            selected_cycle_snapshot = {
                "picking_name": normalize_text(getattr(selected_cycle, "picking_name", "")),
                "cycle_status": normalize_text(getattr(selected_cycle, "cycle_status", "")),
                "document_classification": normalize_text(getattr(selected_cycle, "document_classification", "")),
                "document_classification_label": normalize_text(getattr(selected_cycle, "document_classification_label", "")),
                "partner_name": normalize_text(getattr(selected_cycle, "partner_name", "")),
                "purchase_orders": list(getattr(selected_cycle, "purchase_orders", None) or []),
                "bill_refs": list(getattr(selected_cycle, "bill_refs", None) or []),
                "inventory_types": list(getattr(selected_cycle, "inventory_types", None) or []),
                "problem_account_count": int(getattr(selected_cycle, "problem_account_count", 0) or 0),
                "info_account_count": int(getattr(selected_cycle, "info_account_count", 0) or 0),
                "item_count": len(list(getattr(selected_cycle, "item_rows", None) or [])),
            }
            if selected_item_row is not None:
                selected_cycle_snapshot["selected_item_row"] = {
                    "product_id": int(getattr(selected_item_row, "product_id", 0) or 0),
                    "default_code": normalize_text(getattr(selected_item_row, "default_code", "")),
                    "product_name": normalize_text(getattr(selected_item_row, "product_name", "")),
                    "valuation_method": normalize_text(getattr(selected_item_row, "valuation_method", "")),
                }
        selected_cycle_payload = None
        if include_selected_cycle_payload and selected_cycle is not None and purchase_cycle_mode:
            selected_cycle_payload = self._debug_json_ready(self._build_pcb_cycle_detail_export_payload(selected_cycle))
        snapshot = getattr(self, "_latest_snapshot", None)
        purchase_cycles = list(getattr(snapshot, "purchase_cycles", None) or []) if snapshot is not None else []
        return {
            "purchase_cycle_mode": purchase_cycle_mode,
            "busy": bool(getattr(self, "_busy", False)),
            "snapshot_interactive_ready": bool(getattr(self, "_snapshot_interactive_ready", False)),
            "detail_warm_state": normalize_text(getattr(self, "_detail_warm_state", "")),
            "database_label": str(self.database_choice_var.get() or ""),
            "dataset_mode_label": str(self.dataset_mode_var.get() or ""),
            "company_label": str(self.company_choice_var.get() or ""),
            "selected_company_id": int(getattr(self, "_selected_company_id_value", 0) or 0),
            "date_from": str(self.date_from_var.get() or ""),
            "date_to": str(self.date_to_var.get() or ""),
            "status_text": str(self.status_var.get() or ""),
            "phase_text": str(self.phase_var.get() or ""),
            "sidebar_summary_text": str(self.sidebar_summary_var.get() or ""),
            "effective_db_text": str(self.effective_db_var.get() or ""),
            "source_db_text": str(self.source_db_var.get() or ""),
            "search_query": "" if bool(getattr(self, "_search_placeholder_active", False)) else str(self.search_var.get() or ""),
            "warning_text": str(self.warning_var.get() or ""),
            "latest_log_preview": str(self.latest_log_line_var.get() or ""),
            "snapshot_summary": {
                "dataset_mode": normalize_text(getattr(snapshot, "dataset_mode", "")) if snapshot is not None else "",
                "company_id": int(getattr(snapshot, "company_id", 0) or 0) if snapshot is not None else 0,
                "purchase_cycle_count": len(purchase_cycles),
                "problem_cycle_count": sum(
                    1 for cycle in purchase_cycles if normalize_text(getattr(cycle, "cycle_status", "")).lower() == "problem"
                ),
                "partial_cycle_count": sum(
                    1 for cycle in purchase_cycles if normalize_text(getattr(cycle, "cycle_status", "")).lower() == "partial"
                ),
                "healthy_cycle_count": sum(
                    1 for cycle in purchase_cycles if normalize_text(getattr(cycle, "cycle_status", "")).lower() == "healthy"
                ),
                "visible_cycle_count": len(list(getattr(self, "_pcb_visible_cycles", None) or [])),
            },
            "selected_sidebar_item_ids": selected_sidebar_item_ids,
            "selected_sidebar_cycle": selected_cycle_snapshot,
            "selected_sidebar_cycle_payload": selected_cycle_payload,
            "pcb_headers": {
                "raw_notice": str(self._pcb_raw_notice_var.get() or "") if hasattr(self, "_pcb_raw_notice_var") else "",
                "partner_header": str(self._pcb_partner_header_var.get() or "") if hasattr(self, "_pcb_partner_header_var") else "",
                "item_header": str(self._pcb_item_header_var.get() or "") if hasattr(self, "_pcb_item_header_var") else "",
            },
            "pcb_repair_collection": self._build_pcb_repair_collection_debug_snapshot(max_rows_per_tree=max_rows_per_tree),
            "trees": {
                "sidebar": self._build_treeview_widget_state(
                    sidebar_tree,
                    meta_map=self._build_pcb_sidebar_debug_meta() if purchase_cycle_mode else dict(getattr(self, "_sidebar_tree_meta", {}) or {}),
                    max_rows=max_rows_per_tree,
                ),
                "pcb_raw": self._build_treeview_widget_state(
                    getattr(self, "pcb_raw_tree", None),
                    meta_map=dict(getattr(self, "_tree_row_meta", {}).get(getattr(self, "pcb_raw_tree", None), {}) or {}),
                    max_rows=max_rows_per_tree,
                ) if purchase_cycle_mode else None,
                "pcb_detail": self._build_treeview_widget_state(
                    getattr(self, "pcb_detail_tree", None),
                    meta_map=dict(getattr(self, "_tree_row_meta", {}).get(getattr(self, "pcb_detail_tree", None), {}) or {}),
                    max_rows=max_rows_per_tree,
                ) if purchase_cycle_mode else None,
                "pcb_account_summary": self._build_treeview_widget_state(
                    getattr(self, "pcb_acct_summary_tree", None),
                    meta_map=dict(getattr(self, "_tree_row_meta", {}).get(getattr(self, "pcb_acct_summary_tree", None), {}) or {}),
                    max_rows=max_rows_per_tree,
                ) if purchase_cycle_mode else None,
                "current_asset": self._build_treeview_widget_state(
                    getattr(self, "current_asset_tree", None),
                    meta_map=dict(getattr(self, "_tree_row_meta", {}).get(getattr(self, "current_asset_tree", None), {}) or {}),
                    max_rows=max_rows_per_tree,
                ) if not purchase_cycle_mode else None,
                "cycle_link": self._build_treeview_widget_state(
                    getattr(self, "cycle_link_tree", None),
                    meta_map=dict(getattr(self, "_tree_row_meta", {}).get(getattr(self, "cycle_link_tree", None), {}) or {}),
                    max_rows=max_rows_per_tree,
                ) if not purchase_cycle_mode else None,
                "compare_po": self._build_treeview_widget_state(
                    getattr(self, "compare_po_tree", None),
                    meta_map=dict(getattr(self, "_tree_row_meta", {}).get(getattr(self, "compare_po_tree", None), {}) or {}),
                    max_rows=max_rows_per_tree,
                ) if not purchase_cycle_mode else None,
                "compare_bill": self._build_treeview_widget_state(
                    getattr(self, "compare_bill_tree", None),
                    meta_map=dict(getattr(self, "_tree_row_meta", {}).get(getattr(self, "compare_bill_tree", None), {}) or {}),
                    max_rows=max_rows_per_tree,
                ) if not purchase_cycle_mode else None,
            },
        }

    def _build_card(
        self,
        parent: tk.Widget,
        *,
        title: str | tk.StringVar,
        pady: tuple[int, int] = (0, 8),
        fill: str = "x",
        expand: bool = False,
    ) -> tk.Frame:
        frame = tk.Frame(
            parent,
            bg=T.BG_CARD,
            bd=1,
            relief="solid",
            highlightbackground=T.BORDER_LIGHT,
            highlightthickness=1,
        )
        frame.pack(fill=fill, expand=expand, pady=pady)
        inner = tk.Frame(frame, bg=T.BG_CARD, padx=16, pady=12)
        inner.pack(fill="both", expand=True)
        label_kwargs = {
            "bg": T.BG_CARD,
            "fg": T.TEXT_ON_LIGHT,
            "font": T.font(T.FONT_BODY_SIZE, bold=True),
        }
        if isinstance(title, tk.StringVar):
            tk.Label(inner, textvariable=title, **label_kwargs).pack(anchor="w", pady=(0, 10))
        else:
            tk.Label(inner, text=title, **label_kwargs).pack(anchor="w", pady=(0, 10))
        body = tk.Frame(inner, bg=T.BG_CARD)
        body.pack(fill="both", expand=True)
        return body

    def _build_ui(self) -> None:
        self.parent.configure(bg=T.BG_MAIN)
        self._scrollable = ScrollableFrame(self.parent, bg=T.BG_MAIN)
        self._scrollable.pack(fill="both", expand=True)
        main = self._scrollable.interior

        filters = self._build_card(main, title="Database & Filters", pady=(16, 8))
        grid = tk.Frame(filters, bg=T.BG_CARD)
        grid.pack(fill="x")
        for col in (1, 3):
            grid.grid_columnconfigure(col, weight=1)

        tk.Label(grid, text="Mode:", bg=T.BG_CARD, fg=T.TEXT_ON_LIGHT, font=T.font()).grid(
            row=0, column=0, sticky="w", pady=4
        )
        self.dataset_mode_combo = ttk.Combobox(
            grid,
            state="readonly",
            textvariable=self.dataset_mode_var,
            values=list(DATASET_MODE_LABELS.keys()),
            width=24,
        )
        self.dataset_mode_combo.grid(row=0, column=1, sticky="ew", padx=(8, 16), pady=4)
        self.dataset_mode_combo.bind("<<ComboboxSelected>>", self._on_dataset_mode_selected)

        tk.Label(grid, text="Company:", bg=T.BG_CARD, fg=T.TEXT_ON_LIGHT, font=T.font()).grid(
            row=0, column=2, sticky="w", pady=4
        )
        self.company_combo = ttk.Combobox(
            grid,
            state="normal",
            textvariable=self.company_choice_var,
            width=34,
            postcommand=self._on_company_dropdown_requested,
        )
        self.company_combo.grid(row=0, column=3, sticky="ew", pady=4)
        self.company_combo.bind("<<ComboboxSelected>>", self._on_company_selected)
        self.company_combo.bind("<KeyRelease>", self._on_company_search)
        self.company_combo.bind("<Return>", self._on_company_return)
        self.company_combo.bind("<KP_Enter>", self._on_company_return)
        self.company_combo.bind("<Escape>", self._restore_company_input)

        tk.Label(grid, text="Date From:", bg=T.BG_CARD, fg=T.TEXT_ON_LIGHT, font=T.font()).grid(
            row=1, column=0, sticky="w", pady=4
        )
        self.date_from_entry = tk.Entry(grid, textvariable=self.date_from_var, bg=T.BG_INPUT, relief="solid", bd=1)
        self.date_from_entry.grid(row=1, column=1, sticky="ew", padx=(8, 16), pady=4)

        tk.Label(grid, text="Date To:", bg=T.BG_CARD, fg=T.TEXT_ON_LIGHT, font=T.font()).grid(
            row=1, column=2, sticky="w", pady=4
        )
        self.date_to_entry = tk.Entry(grid, textvariable=self.date_to_var, bg=T.BG_INPUT, relief="solid", bd=1)
        self.date_to_entry.grid(row=1, column=3, sticky="ew", pady=4)
        tk.Label(
            grid,
            textvariable=self.effective_db_var,
            bg=T.BG_CARD,
            fg=T.TEXT_MUTED,
            font=T.font(T.FONT_SMALL_SIZE),
            anchor="w",
            justify="left",
        ).grid(row=2, column=0, columnspan=4, sticky="ew", pady=(4, 0))

        button_row = tk.Frame(filters, bg=T.BG_CARD)
        button_row.pack(fill="x", pady=(10, 0))
        self.btn_refresh_companies = tk.Button(
            button_row,
            text="Refresh Company",
            bg=T.BRAND_SECONDARY,
            fg=T.TEXT_ON_DARK,
            font=T.font(T.FONT_BODY_SIZE, bold=True),
            activebackground=T.BRAND_ACCENT,
            activeforeground=T.TEXT_ON_DARK,
            relief="flat",
            padx=16,
            pady=6,
            command=lambda: self.load_companies(force=True),
        )
        self.btn_refresh_companies.pack(side="left")
        self.btn_analyze = tk.Button(
            button_row,
            text="Analyze",
            bg=T.BRAND_PRIMARY,
            fg=T.TEXT_ON_DARK,
            font=T.font(T.FONT_BODY_SIZE, bold=True),
            activebackground=T.BRAND_PRIMARY_DARK,
            activeforeground=T.TEXT_ON_DARK,
            relief="flat",
            padx=18,
            pady=6,
            command=self.start_analysis,
        )
        self.btn_analyze.pack(side="left", padx=(8, 0))
        # Wrapper frame — hidden via pack_forget in PCB mode (_refresh_dataset_mode_widgets)
        self._inventory_filter_frame = tk.Frame(button_row, bg=T.BG_CARD)
        self._inventory_filter_frame.pack(side="left", padx=(16, 0))
        self.include_inventory_check = tk.Checkbutton(
            self._inventory_filter_frame,
            text="Include Inventory Accounts",
            variable=self.include_inventory_accounts_var,
            bg=T.BG_CARD,
            fg=T.TEXT_ON_LIGHT,
            selectcolor=T.BG_INPUT,
            activebackground=T.BG_CARD,
            activeforeground=T.TEXT_ON_LIGHT,
        )
        self.include_inventory_check.pack(side="left")
        self.include_non_inventory_check = tk.Checkbutton(
            self._inventory_filter_frame,
            text="Include Non-Inventory Accounts",
            variable=self.include_non_inventory_accounts_var,
            bg=T.BG_CARD,
            fg=T.TEXT_ON_LIGHT,
            selectcolor=T.BG_INPUT,
            activebackground=T.BG_CARD,
            activeforeground=T.TEXT_ON_LIGHT,
        )
        self.include_non_inventory_check.pack(side="left", padx=(8, 0))
        self.btn_export_html = tk.Button(
            button_row,
            text="Export HTML",
            bg=T.BRAND_PRIMARY,
            fg=T.TEXT_ON_DARK,
            font=T.font(T.FONT_BODY_SIZE, bold=True),
            activebackground=T.BRAND_PRIMARY_DARK,
            activeforeground=T.TEXT_ON_DARK,
            relief="flat",
            padx=18,
            pady=6,
            state="disabled",
            command=lambda: self._export_snapshot("html"),
        )
        self.btn_export_html.pack(side="left", padx=(8, 0))
        self.btn_export_json = tk.Button(
            button_row,
            text="Export JSON",
            bg=T.BRAND_PRIMARY,
            fg=T.TEXT_ON_DARK,
            font=T.font(T.FONT_BODY_SIZE, bold=True),
            activebackground=T.BRAND_PRIMARY_DARK,
            activeforeground=T.TEXT_ON_DARK,
            relief="flat",
            padx=18,
            pady=6,
            state="disabled",
            command=lambda: self._export_snapshot("json"),
        )
        self.btn_export_json.pack(side="left", padx=(8, 0))
        self._pcb_company_export_btn = tk.Button(
            button_row,
            text="↓ Excel (All Cycles)",
            bg=T.BRAND_PRIMARY,
            fg=T.TEXT_ON_DARK,
            font=T.font(T.FONT_BODY_SIZE, bold=True),
            activebackground=T.BRAND_PRIMARY_DARK,
            activeforeground=T.TEXT_ON_DARK,
            relief="flat",
            padx=18,
            pady=6,
            state="disabled",
            command=self._export_pcb_company_excel,
        )
        # hidden by default; shown only in PCB mode via _refresh_dataset_mode_widgets

        self.progress_row = tk.Frame(filters, bg=T.BG_CARD)
        self.progress_row.pack(fill="x", pady=(12, 0))
        self.progress_row.grid_columnconfigure(0, weight=4)
        self.progress_row.grid_columnconfigure(1, weight=1, minsize=DASHBOARD_PROGRESS_INFO_MIN_WIDTH)
        self.progress_bar_host = tk.Frame(self.progress_row, bg=T.BG_CARD)
        self.progress_bar_host.grid(row=0, column=0, sticky="ew")
        self.progress_bar = ttk.Progressbar(
            self.progress_bar_host,
            variable=self.progress_value,
            maximum=100,
            length=DASHBOARD_PROGRESS_BAR_MIN_WIDTH,
        )
        self.progress_bar.pack(fill="x")
        self.progress_info_host = tk.Frame(self.progress_row, bg=T.BG_CARD)
        self.progress_info_host.grid(row=0, column=1, sticky="ew", padx=(12, 0))
        self.progress_info_host.grid_columnconfigure(1, weight=1)
        self.progress_percent_label = tk.Label(
            self.progress_info_host,
            textvariable=self.percent_var,
            bg=T.BG_CARD,
            fg=T.TEXT_ON_LIGHT,
            font=T.font(T.FONT_BODY_SIZE, bold=True),
        )
        self.progress_percent_label.grid(row=0, column=0, sticky="nw", padx=(0, 8))
        self.progress_message_label = tk.Label(
            self.progress_info_host,
            textvariable=self.phase_var,
            bg=T.BG_CARD,
            fg=T.TEXT_MUTED,
            font=T.font(),
            anchor="w",
            justify="left",
            wraplength=PROGRESS_TEXT_WRAP_MIN_WIDTH,
        )
        self.progress_message_label.grid(row=0, column=1, sticky="ew")
        self._bind_progress_row_layout(
            self.progress_row,
            self.progress_bar_host,
            self.progress_bar,
            self.progress_info_host,
            self.progress_message_label,
            self.progress_percent_label,
            width_resolver=self._dashboard_progress_row_widths,
            left_weight=4,
            right_weight=1,
        )
        tk.Label(filters, textvariable=self.status_var, bg=T.BG_CARD, fg=T.TEXT_MUTED, font=T.font(T.FONT_SMALL_SIZE)).pack(
            anchor="w", pady=(8, 0)
        )

        self.notice_stack = tk.Frame(main, bg=T.BG_MAIN)
        self.notice_stack.pack(fill="x", pady=(0, 8))

        self.database_notice_label = tk.Label(
            self.notice_stack,
            textvariable=self.database_notice_var,
            bg="#fff3cd",
            fg="#7a5a00",
            justify="left",
            anchor="w",
            wraplength=1080,
            padx=12,
            pady=8,
        )

        self.warning_label = tk.Label(
            self.notice_stack,
            textvariable=self.warning_var,
            bg="#fff3cd",
            fg="#7a5a00",
            justify="left",
            anchor="w",
            wraplength=1080,
            padx=12,
            pady=8,
        )

        _main_children_before = len(main.winfo_children())
        company_summary = self._build_card(main, title="Ringkasan Company", pady=(0, 8))
        # Capture outer frame reference for show/hide toggling in PCB mode
        self._company_summary_outer = main.winfo_children()[_main_children_before]
        company_kpi_frame = tk.Frame(company_summary, bg=T.BG_CARD)
        company_kpi_frame.pack(fill="x")
        self._build_dual_kpi_card(
            company_kpi_frame,
            self.company_total_primary_title_var,
            self.company_total_svl_value_var,
            self.company_total_primary_secondary_label_var,
            self.company_total_svl_qty_var,
            0,
        )
        self._build_kpi_card(company_kpi_frame, self.company_total_secondary_title_var, self.company_total_bs_var, 1)
        self._build_kpi_card(company_kpi_frame, self.company_total_diff_title_var, self.company_total_diff_var, 2)
        self._build_kpi_card(company_kpi_frame, self.company_total_aux_title_var, self.company_total_unmapped_var, 3)

        coa_card = tk.Frame(
            company_summary,
            bg=T.BG_CARD,
            bd=1,
            relief="solid",
            highlightbackground=T.BORDER_LIGHT,
            highlightthickness=1,
        )
        coa_card.pack(fill="x", pady=(12, 0))
        coa_inner = tk.Frame(coa_card, bg=T.BG_CARD, padx=12, pady=10)
        coa_inner.pack(fill="both", expand=True)
        tk.Label(
            coa_inner,
            textvariable=self.company_coa_title_var,
            bg=T.BG_CARD,
            fg=T.TEXT_ON_LIGHT,
            font=T.font(T.FONT_BODY_SIZE, bold=True),
        ).pack(anchor="w")
        tk.Label(
            coa_inner,
            textvariable=self.company_coa_notice_var,
            bg=T.BG_CARD,
            fg=T.TEXT_MUTED,
            font=T.font(T.FONT_SMALL_SIZE),
            justify="left",
            anchor="w",
            wraplength=1040,
        ).pack(fill="x", pady=(6, 8))
        coa_tree_frame = tk.Frame(coa_inner, bg=T.BG_CARD)
        coa_tree_frame.pack(fill="x")
        self.company_coa_tree = self._build_tree(coa_tree_frame, ("code", "coa_name", "debit", "credit", "balance"))
        self.company_coa_tree.configure(height=7)
        tk.Label(
            coa_inner,
            textvariable=self.company_coa_total_var,
            bg=T.BG_CARD,
            fg=T.TEXT_ON_LIGHT,
            font=T.font(T.FONT_SMALL_SIZE, bold=True),
            anchor="e",
            justify="right",
        ).pack(fill="x", pady=(8, 0))

        # ── PCB summary card (replaces company_summary in Balance Cycle Pembelian mode) ──
        # Hidden initially; shown by _refresh_dataset_mode_widgets when PCB mode is active.
        self._pcb_summary_outer = tk.Frame(
            main,
            bg=T.BG_CARD,
            bd=1,
            relief="solid",
            highlightbackground=T.BORDER_LIGHT,
            highlightthickness=1,
        )
        # NOT packed here — toggled in _refresh_dataset_mode_widgets
        _pcb_sum_inner = tk.Frame(self._pcb_summary_outer, bg=T.BG_CARD, padx=16, pady=12)
        _pcb_sum_inner.pack(fill="both", expand=True)
        # Title — tk.Entry readonly so text is selectable/copyable
        _pcb_title_entry = tk.Entry(
            _pcb_sum_inner,
            font=T.font(T.FONT_BODY_SIZE, bold=True),
            fg=T.TEXT_ON_LIGHT,
            bg=T.BG_CARD,
            relief="flat",
            bd=0,
            state="normal",
        )
        _pcb_title_entry.insert(0, "Detail Journal Entry per Cycle Pembelian")
        _pcb_title_entry.configure(state="readonly")
        _pcb_title_entry.pack(fill="x", pady=(0, 4))
        # Notice — also readonly Entry (supports textvariable + copyable)
        self._pcb_raw_notice_var = tk.StringVar(
            value="Pilih cycle di sidebar untuk melihat data mentah transaksi."
        )
        _pcb_notice_entry = tk.Entry(
            _pcb_sum_inner,
            textvariable=self._pcb_raw_notice_var,
            font=T.font(T.FONT_SMALL_SIZE),
            fg=T.TEXT_MUTED,
            bg=T.BG_CARD,
            relief="flat",
            bd=0,
            state="readonly",
        )
        _pcb_notice_entry.pack(fill="x", pady=(0, 2))
        # Sort control row
        _pcb_sort_row = tk.Frame(_pcb_sum_inner, bg=T.BG_CARD)
        _pcb_sort_row.pack(fill="x", pady=(0, 4))
        tk.Label(
            _pcb_sort_row,
            text="Urutan:",
            bg=T.BG_CARD,
            fg=T.TEXT_MUTED,
            font=T.font(T.FONT_SMALL_SIZE),
        ).pack(side="left")
        self._pcb_raw_sort_var = tk.StringVar(value="Sort by Account")
        _pcb_sort_menu = ttk.Combobox(
            _pcb_sort_row,
            textvariable=self._pcb_raw_sort_var,
            values=["Sort by Account", "Sort by Process"],
            state="readonly",
            width=18,
            font=T.font(T.FONT_SMALL_SIZE),
        )
        _pcb_sort_menu.pack(side="left", padx=(4, 0))
        self._pcb_raw_sort_var.trace_add(
            "write",
            lambda *_: self._on_pcb_raw_sort_changed(),
        )
        self._pcb_export_excel_button = tk.Button(
            _pcb_sort_row,
            text="Download Excel",
            command=self._export_pcb_cycle_detail_excel,
            bg=T.BG_CARD,
            fg=T.TEXT_MUTED,
            relief="flat",
            font=T.font(T.FONT_SMALL_SIZE),
            cursor="hand2",
            padx=8,
            pady=2,
            state="disabled",
        )
        self._pcb_export_excel_button.pack(side="right")
        # Partner header — full-width label with auto-wrap (text set by _populate_pcb_raw_tree)
        self._pcb_partner_header_var = tk.StringVar(value="")
        _pcb_partner_lbl = tk.Label(
            _pcb_sum_inner,
            textvariable=self._pcb_partner_header_var,
            bg="#dce8f5",
            fg=T.TEXT_ON_LIGHT,
            font=T.font(T.FONT_SMALL_SIZE, bold=True),
            anchor="w",
            justify="left",
            padx=6,
            pady=3,
        )
        _pcb_partner_lbl.pack(fill="x", pady=(0, 4))
        # Auto-wrap: update wraplength whenever the label is resized
        _pcb_partner_lbl.bind(
            "<Configure>",
            lambda e: _pcb_partner_lbl.configure(wraplength=max(200, e.width - 12)),
        )
        _pcb_raw_frame = tk.Frame(_pcb_sum_inner, bg=T.BG_CARD)
        _pcb_raw_frame.pack(fill="x")
        self._pcb_raw_selected_cell: tuple[str, str] = ("", "")  # (row_iid, col_id "#N")
        self.pcb_raw_tree = self._build_tree(
            _pcb_raw_frame,
            ("tanggal", "kode_transaksi", "jenis", "tipe_akun", "akun_code", "akun_name",
             "kode_item", "nama_item", "uom", "qty_item", "kategori_produk", "no_po",
             "komunikasi", "debit", "kredit", "saldo", "matching"),
        )
        self.pcb_raw_tree.configure(height=10, selectmode="extended")
        self._configure_pcb_raw_tree()
        self.pcb_raw_tree.bind("<Button-1>", self._on_pcb_raw_cell_click)
        self.pcb_raw_tree.bind("<Button-3>", self._on_pcb_raw_right_click)
        self.pcb_raw_tree.bind("<Control-c>", self._pcb_raw_copy_cell)

        # ── Detail Transaksi per Akun (auto-shown below raw AML table) ──────────
        tk.Frame(_pcb_sum_inner, bg=T.BORDER_LIGHT, height=1).pack(fill="x", pady=(8, 0))
        _pcb_detail_hdr_row = tk.Frame(_pcb_sum_inner, bg=T.BG_CARD)
        _pcb_detail_hdr_row.pack(fill="x", pady=(4, 0))
        tk.Label(
            _pcb_detail_hdr_row,
            text="Detail Transaksi per Akun",
            bg=T.BG_CARD,
            fg=T.TEXT_MUTED,
            font=T.font(T.FONT_SMALL_SIZE, bold=True),
            anchor="w",
        ).pack(side="left")
        self._pcb_item_header_var = tk.StringVar(value="")
        _pcb_item_hdr_lbl = tk.Label(
            _pcb_sum_inner,
            textvariable=self._pcb_item_header_var,
            bg="#dce8f5",
            fg=T.TEXT_ON_LIGHT,
            font=T.font(T.FONT_SMALL_SIZE, bold=True),
            anchor="w",
            justify="left",
            padx=6,
            pady=3,
        )
        _pcb_item_hdr_lbl.pack(fill="x", pady=(2, 4))
        _pcb_item_hdr_lbl.bind(
            "<Configure>",
            lambda e: _pcb_item_hdr_lbl.configure(wraplength=max(200, e.width - 12)),
        )
        _pcb_detail_frame = tk.Frame(_pcb_sum_inner, bg=T.BG_CARD)
        _pcb_detail_frame.pack(fill="both", expand=True)
        self.pcb_detail_tree = self._build_tree(
            _pcb_detail_frame,
            ("date", "journal_source", "transaction_no", "gr_reference", "po",
             "partner_reference", "akun", "akun_name", "debit", "credit", "balance", "matching"),
            selectmode="extended",
            role="current_asset",
        )
        self.pcb_detail_tree.configure(height=10)
        self.pcb_detail_tree.column("akun_name", width=200, minwidth=120)
        self.pcb_detail_tree.column("partner_reference", width=180, minwidth=100)
        self.pcb_detail_tree.column("gr_reference", width=180, minwidth=100)
        self.pcb_detail_tree.column("transaction_no", width=160, minwidth=100)

        self.content_pane = tk.PanedWindow(main, orient="horizontal", sashrelief="flat", bg=T.BG_MAIN, bd=0)
        self.content_pane.pack(fill="both", expand=True, pady=(0, 8))

        left = tk.Frame(self.content_pane, bg=T.BG_MAIN)
        right = tk.Frame(self.content_pane, bg=T.BG_MAIN)
        self.content_pane.add(left, minsize=320)
        self.content_pane.add(right, minsize=640)
        for pane in (left, right):
            pane.configure(bg=T.BG_MAIN)

        sidebar_card = self._build_card(left, title=self.sidebar_card_title_var, fill="both", expand=True)
        sidebar_card.columnconfigure(0, weight=1)
        sidebar_card.rowconfigure(4, weight=1)

        tk.Label(
            sidebar_card,
            textvariable=self.sidebar_summary_var,
            bg=T.BG_CARD,
            fg=T.TEXT_MUTED,
            font=T.font(T.FONT_SMALL_SIZE),
            anchor="w",
            justify="left",
        ).grid(row=0, column=0, sticky="ew", pady=(0, 8))

        search_row = tk.Frame(sidebar_card, bg=T.BG_CARD)
        search_row.grid(row=1, column=0, sticky="ew")
        self.search_entry = tk.Entry(search_row, textvariable=self.search_var, bg=T.BG_INPUT, relief="solid", bd=1)
        self.search_entry.pack(fill="x")
        self.search_var.trace_add("write", self._schedule_sidebar_filter)
        self.search_entry.bind("<FocusIn>", self._on_search_focus_in)
        self.search_entry.bind("<FocusOut>", self._on_search_focus_out)

        # PCB mode filter row (hidden by default, shown when in PCB mode)
        self._pcb_filter_frame = tk.Frame(sidebar_card, bg=T.BG_CARD)
        self._pcb_filter_frame.grid(row=2, column=0, sticky="ew", pady=(4, 0))
        self._pcb_filter_frame.grid_remove()
        _cb_kw = {"bg": T.BG_CARD, "activebackground": T.BG_CARD, "font": T.font(T.FONT_SMALL_SIZE), "bd": 0, "highlightthickness": 0}
        tk.Checkbutton(
            self._pcb_filter_frame, text="❌ Bermasalah",
            variable=self._pcb_show_problem_var,
            command=self._apply_pcb_search_filter, **_cb_kw,
        ).pack(side="left")
        tk.Checkbutton(
            self._pcb_filter_frame, text=" ⚠️ Sebagian",
            variable=self._pcb_show_partial_var,
            command=self._apply_pcb_search_filter, **_cb_kw,
        ).pack(side="left")
        tk.Checkbutton(
            self._pcb_filter_frame, text=" ✅ Sehat",
            variable=self._pcb_show_healthy_var,
            command=self._apply_pcb_search_filter, **_cb_kw,
        ).pack(side="left")
        tk.Button(
            self._pcb_filter_frame, text="⚙",
            command=self._open_pcb_coa_settings,
            bg=T.BG_CARD, relief="flat", font=T.font(T.FONT_SMALL_SIZE),
            cursor="hand2",
        ).pack(side="right")
        self._pcb_collection_btn = tk.Button(
            self._pcb_filter_frame, text="📋 (0)",
            command=self._open_pcb_repair_dialog,
            bg=T.BG_CARD, relief="flat", font=T.font(T.FONT_SMALL_SIZE),
            cursor="hand2",
        )
        self._pcb_collection_btn.pack(side="right", padx=(0, 4))

        # PCB UoM filter row (baris kedua, di bawah status checkboxes)
        self._pcb_uom_filter_frame = tk.Frame(sidebar_card, bg=T.BG_CARD)
        self._pcb_uom_filter_frame.grid(row=3, column=0, sticky="ew", pady=(2, 0))
        self._pcb_uom_filter_frame.grid_remove()
        tk.Label(
            self._pcb_uom_filter_frame, text="UoM:", bg=T.BG_CARD,
            font=T.font(T.FONT_SMALL_SIZE),
        ).pack(side="left")
        self._pcb_uom_combo = ttk.Combobox(
            self._pcb_uom_filter_frame,
            textvariable=self._pcb_uom_filter_var,
            values=["Semua UoM", "UoM Inline", "UoM Mismatch"],
            state="readonly",
            width=14,
            font=T.font(T.FONT_SMALL_SIZE),
        )
        self._pcb_uom_combo.pack(side="left", padx=(4, 0))
        self._pcb_uom_filter_var.trace_add("write", lambda *_: self._apply_pcb_search_filter())

        self.sidebar_list_host = tk.Frame(
            sidebar_card,
            bg=T.BG_CARD,
            bd=1,
            relief="solid",
            highlightbackground=T.BORDER_LIGHT,
            highlightthickness=1,
        )
        self.sidebar_list_host.grid(row=4, column=0, sticky="nsew", pady=(10, 0))
        self.sidebar_list_host.columnconfigure(0, weight=1)
        self.sidebar_list_host.rowconfigure(0, weight=1)
        self._configure_sidebar_tree_style()
        self.sidebar_tree = ttk.Treeview(
            self.sidebar_list_host,
            show="tree",
            selectmode="browse",
            style=SIDEBAR_TREE_STYLE,
        )
        self.sidebar_tree.grid(row=0, column=0, sticky="nsew")
        self.sidebar_tree.column("#0", anchor="w", stretch=True, width=340, minwidth=240)
        self.sidebar_tree.tag_configure("valuation_group", background="#eef1f5")
        self.sidebar_tree.tag_configure("category_group", background="#f7f8fa")
        self.sidebar_tree.tag_configure("sidebar_item_positive", foreground=T.STATUS_SUCCESS)
        self.sidebar_tree.tag_configure("sidebar_item_negative", foreground=T.STATUS_ERROR)
        sidebar_scrollbar = ttk.Scrollbar(self.sidebar_list_host, orient="vertical", command=self.sidebar_tree.yview)
        sidebar_scrollbar.grid(row=0, column=1, sticky="ns")
        self.sidebar_tree.configure(yscrollcommand=sidebar_scrollbar.set)
        self.sidebar_tree = bind_treeview_scroll_support(self.sidebar_tree)
        self.sidebar_tree.bind("<<TreeviewSelect>>", self._on_sidebar_tree_selected, add="+")
        self.sidebar_tree.bind("<<TreeviewSelect>>", self._on_pcb_cycle_selected, add="+")
        self.sidebar_tree.bind("<<TreeviewOpen>>", self._on_sidebar_tree_toggled, add="+")
        self.sidebar_tree.bind("<<TreeviewClose>>", self._on_sidebar_tree_toggled, add="+")
        self.sidebar_tree.bind("<Button-3>", self._on_pcb_sidebar_right_click, add="+")
        self._apply_search_placeholder(render=False)

        detail_card = self._build_card(right, title=self.detail_card_title_var, fill="both", expand=True)
        detail_card.columnconfigure(0, weight=1)
        detail_card.rowconfigure(3, weight=1)

        header_row = tk.Frame(detail_card, bg=T.BG_CARD, bd=1, relief="solid", highlightbackground=T.BORDER_LIGHT, highlightthickness=1)
        header_row.grid(row=0, column=0, sticky="ew")
        header_inner = tk.Frame(header_row, bg=T.BG_CARD, padx=12, pady=10)
        header_inner.pack(fill="both", expand=True)
        header_left = tk.Frame(header_inner, bg=T.BG_CARD)
        header_left.pack(side="left", fill="x", expand=True)
        tk.Label(header_left, textvariable=self.header_code_var, bg=T.BG_CARD, fg=T.BRAND_PRIMARY, font=T.font(T.FONT_SMALL_SIZE, bold=True)).pack(anchor="w")
        tk.Label(header_left, textvariable=self.header_name_var, bg=T.BG_CARD, fg=T.TEXT_ON_LIGHT, font=T.font(T.FONT_HEADING_SIZE, bold=True)).pack(anchor="w", pady=(2, 4))
        self._header_meta_lbl = tk.Label(header_left, textvariable=self.header_meta_var, bg=T.BG_CARD, fg=T.TEXT_MUTED, font=T.font(T.FONT_SMALL_SIZE))
        self._header_meta_lbl.pack(anchor="w")
        self._header_source_db_lbl = tk.Label(header_left, textvariable=self.source_db_var, bg=T.BG_CARD, fg=T.TEXT_MUTED, font=T.font(T.FONT_SMALL_SIZE))
        self._header_source_db_lbl.pack(anchor="w", pady=(2, 0))
        self.position_badge = tk.Label(
            header_inner,
            textvariable=self.position_var,
            bg=T.BRAND_SECONDARY,
            fg=T.TEXT_ON_DARK,
            padx=10,
            pady=4,
            font=T.font(T.FONT_SMALL_SIZE, bold=True),
        )
        self.position_badge.pack(side="right")

        self.kpi_frame = tk.Frame(detail_card, bg=T.BG_CARD)
        self.kpi_frame.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        self._build_kpi_card(self.kpi_frame, self.kpi_svl_title_var, self.kpi_svl_var, 0)
        self._build_kpi_card(self.kpi_frame, self.kpi_bs_title_var, self.kpi_bs_var, 1)
        self._build_kpi_card(self.kpi_frame, self.kpi_diff_title_var, self.kpi_diff_var, 2)
        self._build_kpi_card(self.kpi_frame, self.kpi_po_bill_title_var, self.kpi_po_bill_var, 3)

        # PCB account summary frame (replaces kpi_frame in PCB mode)
        self._pcb_acct_summary_frame = tk.Frame(detail_card, bg=T.BG_CARD)
        self._pcb_acct_summary_frame.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        self._pcb_acct_summary_frame.grid_remove()
        self._pcb_acct_summary_frame.columnconfigure(0, weight=1)
        self._pcb_acct_summary_frame.rowconfigure(0, weight=1)
        self.pcb_acct_summary_tree = self._build_tree(
            self._pcb_acct_summary_frame,
            ("status", "code", "name", "saldo"),
            role="current_asset",
        )
        self.pcb_acct_summary_tree.configure(height=7)
        self.pcb_acct_summary_tree.heading("status", text="")
        self.pcb_acct_summary_tree.column("status", width=28, minwidth=28, anchor="center", stretch=False)
        self.pcb_acct_summary_tree.column("code", width=80, minwidth=70, anchor="w", stretch=False)
        self.pcb_acct_summary_tree.column("name", width=220, minwidth=120, anchor="w", stretch=True)
        self.pcb_acct_summary_tree.column("saldo", width=110, minwidth=80, anchor="e", stretch=False)

        self._kpi_sub_lbl = tk.Label(detail_card, textvariable=self.kpi_sub_var, bg=T.BG_CARD, fg=T.TEXT_MUTED, font=T.font(T.FONT_SMALL_SIZE))
        self._kpi_sub_lbl.grid(row=2, column=0, sticky="w", pady=(6, 8))

        self._build_detail_tabs(detail_card, row=3, column=0)
        self._configure_company_coa_tree()

        self.dashboard_log_section = CollapsibleSection(
            main,
            key="dashboard_logs",
            title="Dashboard Logs",
            expanded=bool(self._module_settings.dashboard_logs_section_open),
            on_toggle=self._on_logs_section_toggled,
            body_fill="x",
            body_expand=False,
            compact_fill="x",
            compact_expand=False,
            show_compact_when_open=False,
        )
        self.dashboard_log_section.pack(fill="x", pady=(0, 16))
        tk.Label(
            self.dashboard_log_section.compact_body,
            textvariable=self.latest_log_line_var,
            bg=T.BG_CARD,
            fg=T.TEXT_MUTED,
            font=T.font(T.FONT_SMALL_SIZE),
            anchor="w",
            justify="left",
        ).pack(fill="x")
        inner_log = tk.Frame(self.dashboard_log_section.body, bg=T.BG_CARD)
        inner_log.pack(fill="x")
        self.log_text = ScrolledText(
            inner_log,
            height=max(6, T.MIN_LOG_VISIBLE_LINES),
            state="disabled",
            wrap="word",
            font=T.font(T.FONT_SMALL_SIZE),
        )
        self.log_text.pack(fill="x")
        self.dashboard_log_section.refresh_layout()
        self._scrollable.canvas.bind("<Configure>", self._on_scrollable_canvas_configure, add="+")
        self.root.after_idle(self._sync_content_height)

        self.refresh_database_options()
        self._refresh_effective_database_display()
        self._refresh_dataset_mode_widgets()
        self._refresh_busy_state()
        self.render_item_cards()
        self._set_empty_detail()

    def _on_scrollable_canvas_configure(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_helpers as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._on_scrollable_canvas_configure(self, *args, **kwargs)
    def _sync_content_height(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_helpers as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._sync_content_height(self, *args, **kwargs)
    def _resolve_effective_database_state(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_helpers as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._resolve_effective_database_state(self, *args, **kwargs)
    def _format_snapshot_source_database(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_helpers as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._format_snapshot_source_database(self, *args, **kwargs)
    def _refresh_notice_area(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_helpers as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._refresh_notice_area(self, *args, **kwargs)
    def _refresh_effective_database_display(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_helpers as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._refresh_effective_database_display(self, *args, **kwargs)
    def set_repair_collection_changed_callback(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_helpers as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod.set_repair_collection_changed_callback(self, *args, **kwargs)
    def _notify_repair_collection_changed(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_helpers as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._notify_repair_collection_changed(self, *args, **kwargs)
    def _request_analysis_refresh(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_helpers as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._request_analysis_refresh(self, *args, **kwargs)
    def _current_repair_collection_scope(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_helpers as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._current_repair_collection_scope(self, *args, **kwargs)
    def _repair_collection_scope_matches_current(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_helpers as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._repair_collection_scope_matches_current(self, *args, **kwargs)
    def _can_modify_repair_collection_from_current_scope(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_helpers as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._can_modify_repair_collection_from_current_scope(self, *args, **kwargs)
    @staticmethod
    def _today_text(*args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_helpers as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._today_text(*args, **kwargs)
    @staticmethod
    def _normalize_iso_date(*args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_helpers as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._normalize_iso_date(*args, **kwargs)
    def _pcb_case1_default_date(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_helpers as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._pcb_case1_default_date(self, *args, **kwargs)
    def _save_pcb_case1_last_date(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_helpers as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._save_pcb_case1_last_date(self, *args, **kwargs)
    def _repair_collection_total_amount(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_helpers as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._repair_collection_total_amount(self, *args, **kwargs)
    def _repair_collection_scope_text(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_helpers as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._repair_collection_scope_text(self, *args, **kwargs)
    def _repair_collection_state_counts(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_helpers as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._repair_collection_state_counts(self, *args, **kwargs)
    @staticmethod
    def _repair_collection_counts_text(*args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_helpers as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._repair_collection_counts_text(*args, **kwargs)
    def _close_repair_dialog_if_collection_source(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_helpers as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._close_repair_dialog_if_collection_source(self, *args, **kwargs)
    def get_repair_collection_ui_state(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_helpers as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod.get_repair_collection_ui_state(self, *args, **kwargs)
    def is_repair_row_collected(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_helpers as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod.is_repair_row_collected(self, *args, **kwargs)
    def _reset_repair_collection_scope_if_empty(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_helpers as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._reset_repair_collection_scope_if_empty(self, *args, **kwargs)
    def clear_repair_collection(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_helpers as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod.clear_repair_collection(self, *args, **kwargs)
    def _build_kpi_card(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_helpers as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._build_kpi_card(self, *args, **kwargs)
    def _build_dual_kpi_card(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_helpers as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._build_dual_kpi_card(self, *args, **kwargs)
    def _configure_company_coa_tree(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_helpers as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._configure_company_coa_tree(self, *args, **kwargs)
    def _configure_pcb_raw_tree(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_helpers as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._configure_pcb_raw_tree(self, *args, **kwargs)
    def _configured_inventory_coa_codes(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_helpers as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._configured_inventory_coa_codes(self, *args, **kwargs)
    def _configured_repair_account_candidates(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_helpers as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._configured_repair_account_candidates(self, *args, **kwargs)
    @staticmethod
    def _normalize_repair_target_account_role(*args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._normalize_repair_target_account_role(*args, **kwargs)
    @staticmethod
    def _normalized_repair_candidate_role(*args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._normalized_repair_candidate_role(*args, **kwargs)
    @staticmethod
    def _repair_account_role_priority(*args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._repair_account_role_priority(*args, **kwargs)
    @staticmethod
    def _dedupe_repair_account_candidates(*args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._dedupe_repair_account_candidates(*args, **kwargs)
    def _repair_account_candidates_for_seed(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._repair_account_candidates_for_seed(self, *args, **kwargs)
    @staticmethod
    def _repair_account_candidates_cache_key(*args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._repair_account_candidates_cache_key(*args, **kwargs)
    @staticmethod
    def _category_repair_account_candidates_for_seed(*args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._category_repair_account_candidates_for_seed(*args, **kwargs)
    @staticmethod
    def _repair_target_account_role_label(*args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._repair_target_account_role_label(*args, **kwargs)
    @staticmethod
    def _repair_target_account_candidate(*args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._repair_target_account_candidate(*args, **kwargs)
    @staticmethod
    def _missing_repair_target_account_message(*args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._missing_repair_target_account_message(*args, **kwargs)
    @staticmethod
    def _derive_repair_account_codes(*args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._derive_repair_account_codes(*args, **kwargs)
    @staticmethod
    def _filter_repair_account_candidates(*args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._filter_repair_account_candidates(*args, **kwargs)
    @staticmethod
    def _repair_generated_reference(*args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._repair_generated_reference(*args, **kwargs)
    @staticmethod
    def _repair_generated_line_label(*args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._repair_generated_line_label(*args, **kwargs)
    @staticmethod
    def _pcb_case_label(*args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._pcb_case_label(*args, **kwargs)
    @staticmethod
    def _pcb_case1_item_display_label(*args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._pcb_case1_item_display_label(*args, **kwargs)
    @staticmethod
    def _pcb_case1_generated_reference(*args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._pcb_case1_generated_reference(*args, **kwargs)
    @staticmethod
    def _pcb_case1_generated_line_label(*args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._pcb_case1_generated_line_label(*args, **kwargs)
    @staticmethod
    def _pcb_row_uses_planned_lines(*args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._pcb_row_uses_planned_lines(*args, **kwargs)
    @staticmethod
    def _pcb_is_resolve_account_editable(*args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._pcb_is_resolve_account_editable(*args, **kwargs)
    @staticmethod
    def _pcb_missing_expense_guard_message(*args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._pcb_missing_expense_guard_message(*args, **kwargs)
    @staticmethod
    def _pcb_local_account_name_map(*args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._pcb_local_account_name_map(*args, **kwargs)
    @classmethod
    def _pcb_fixed_account_candidates(cls, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._pcb_fixed_account_candidates(cls, *args, **kwargs)
    @classmethod
    def _pcb_cycle_problem_account_candidates(cls, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._pcb_cycle_problem_account_candidates(cls, *args, **kwargs)
    @classmethod
    def _pcb_local_account_candidates_for_row(cls, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._pcb_local_account_candidates_for_row(cls, *args, **kwargs)
    def _pcb_simulation_account_candidates_for_row(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._pcb_simulation_account_candidates_for_row(self, *args, **kwargs)
    @classmethod
    def _pcb_effective_guard_flags(cls, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._pcb_effective_guard_flags(cls, *args, **kwargs)
    @classmethod
    def _pcb_effective_guard_messages(cls, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._pcb_effective_guard_messages(cls, *args, **kwargs)
    def _pcb_account_candidates_for_row(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._pcb_account_candidates_for_row(self, *args, **kwargs)
    def _pcb_lookup_account_name(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._pcb_lookup_account_name(self, *args, **kwargs)
    def _sync_pcb_case2_row_defaults(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._sync_pcb_case2_row_defaults(self, *args, **kwargs)
    @classmethod
    def _pcb_je_preview_text(cls, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._pcb_je_preview_text(cls, *args, **kwargs)
    @staticmethod
    def _pcb_case2_planned_line_role_text(*args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._pcb_case2_planned_line_role_text(*args, **kwargs)
    @staticmethod
    def _pcb_detail_amount(*args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._pcb_detail_amount(*args, **kwargs)
    @classmethod
    def _pcb_planned_lines_signed_total(cls, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._pcb_planned_lines_signed_total(cls, *args, **kwargs)
    def _pcb_problem_code_set(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._pcb_problem_code_set(self, *args, **kwargs)
    def _pcb_info_code_set(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._pcb_info_code_set(self, *args, **kwargs)
    def _pcb_detail_account_status(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._pcb_detail_account_status(self, *args, **kwargs)
    @staticmethod
    def _pcb_cycle_key_picking_id(*args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._pcb_cycle_key_picking_id(*args, **kwargs)
    def _pcb_detail_cycle_for_row(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._pcb_detail_cycle_for_row(self, *args, **kwargs)
    @staticmethod
    def _pcb_detail_item_row_for_row(*args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._pcb_detail_item_row_for_row(*args, **kwargs)
    @classmethod
    def _pcb_detail_account_name_map(cls, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._pcb_detail_account_name_map(cls, *args, **kwargs)
    @classmethod
    def _pcb_case1_simulation_keterangan(cls, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._pcb_case1_simulation_keterangan(cls, *args, **kwargs)
    @classmethod
    def _pcb_case1_simulated_journal_lines(cls, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._pcb_case1_simulated_journal_lines(cls, *args, **kwargs)
    @classmethod
    def _pcb_planned_line_simulation_keterangan(cls, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._pcb_planned_line_simulation_keterangan(cls, *args, **kwargs)
    def _pcb_simulated_journal_lines(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._pcb_simulated_journal_lines(self, *args, **kwargs)
    def _pcb_projected_cycle_account_rows(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._pcb_projected_cycle_account_rows(self, *args, **kwargs)
    @staticmethod
    def _combine_unique_text_parts(*args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._combine_unique_text_parts(*args, **kwargs)
    @staticmethod
    def _pcb_projected_review_reason_text(*args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._pcb_projected_review_reason_text(*args, **kwargs)
    def _recompute_pcb_projected_review_state(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._recompute_pcb_projected_review_state(self, *args, **kwargs)
    @staticmethod
    def _pcb_guard_summary_text(*args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._pcb_guard_summary_text(*args, **kwargs)
    @staticmethod
    def _pcb_review_required(*args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._pcb_review_required(*args, **kwargs)
    @staticmethod
    def _pcb_review_confirmed(*args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._pcb_review_confirmed(*args, **kwargs)
    @staticmethod
    def _pcb_review_reason(*args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._pcb_review_reason(*args, **kwargs)
    @classmethod
    def _pcb_review_status_text(cls, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._pcb_review_status_text(cls, *args, **kwargs)
    @staticmethod
    def _pcb_row_has_blocking_guard(*args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._pcb_row_has_blocking_guard(*args, **kwargs)
    @classmethod
    def _refresh_pcb_case2_collection_row_state(cls, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._refresh_pcb_case2_collection_row_state(cls, *args, **kwargs)
    @staticmethod
    def _suggest_repair_account_codes(*args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._suggest_repair_account_codes(*args, **kwargs)
    @staticmethod
    def _repair_candidate_preview_label(*args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._repair_candidate_preview_label(*args, **kwargs)
    @classmethod
    def _lookup_repair_candidate_preview(cls, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._lookup_repair_candidate_preview(cls, *args, **kwargs)
    @staticmethod
    def _build_collection_repair_draft(*args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._build_collection_repair_draft(*args, **kwargs)
    def _merge_seed_into_repair_draft(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._merge_seed_into_repair_draft(self, *args, **kwargs)
    def _repair_default_settings_payload(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._repair_default_settings_payload(self, *args, **kwargs)
    def _save_repair_default_settings(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._save_repair_default_settings(self, *args, **kwargs)
    @staticmethod
    def _category_title_for_item(item: Any) -> str:
        if normalize_text(getattr(item, "item_kind", "")) == "unassigned_journal":
            return "System / Journal Tanpa Product"
        return normalize_text(getattr(item, "categ", "")) or "Tanpa Kategori"

    def _valuation_title_for_item(self, item: Any) -> str:
        if normalize_text(getattr(item, "item_kind", "")) == "unassigned_journal":
            return "Non Product"
        return "Automated / Track Inventory" if bool(getattr(item, "automated_valuation", True)) else "Manual / Non Inventory"

    def _sidebar_leaf_for_item(self, item: Any, *, amount: float | None = None) -> dict[str, Any]:
        return {
            "pid": int(getattr(item, "pid", 0) or 0),
            "item": item,
            "amount": float(self._item_primary_amount(item) if amount is None else amount),
        }

    def _group_sidebar_items(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_helpers as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._group_sidebar_items(self, *args, **kwargs)
    @staticmethod
    def _format_category_summary(*args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_helpers as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._format_category_summary(*args, **kwargs)
    @staticmethod
    def _format_valuation_summary(*args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_helpers as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._format_valuation_summary(*args, **kwargs)
    @staticmethod
    def _merged_group_priority(*args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_helpers as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._merged_group_priority(*args, **kwargs)
    @staticmethod
    def _merged_group_default_open(*args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_helpers as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._merged_group_default_open(*args, **kwargs)
    def _merged_group_is_open(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_helpers as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._merged_group_is_open(self, *args, **kwargs)
    def _group_merged_rows(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_helpers as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._group_merged_rows(self, *args, **kwargs)
    @staticmethod
    def _format_merged_group_header(*args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_helpers as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._format_merged_group_header(*args, **kwargs)
    def _fill_grouped_merged_tree(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_analysis_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._fill_grouped_merged_tree(self, *args, **kwargs)
    def _toggle_merged_group(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_analysis_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._toggle_merged_group(self, *args, **kwargs)
    def _build_tree(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_analysis_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._build_tree(self, *args, **kwargs)
    def _build_detail_tabs(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_analysis_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._build_detail_tabs(self, *args, **kwargs)
    def _switch_detail_tab(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_analysis_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._switch_detail_tab(self, *args, **kwargs)
    def _build_current_asset_tab(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_analysis_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._build_current_asset_tab(self, *args, **kwargs)
    def _build_compare_tab(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_analysis_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._build_compare_tab(self, *args, **kwargs)
    def _build_analysis_tab(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_analysis_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._build_analysis_tab(self, *args, **kwargs)
    def resume(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_analysis_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod.resume(self, *args, **kwargs)
    def pause(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_analysis_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod.pause(self, *args, **kwargs)
    def _cancel_scheduled_detail_render(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_analysis_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._cancel_scheduled_detail_render(self, *args, **kwargs)
    def _schedule_active_detail_render(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_analysis_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._schedule_active_detail_render(self, *args, **kwargs)
    def _render_active_detail_tab(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_analysis_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._render_active_detail_tab(self, *args, **kwargs)
    @staticmethod
    def _normalize_tree_row_payload(*args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_helpers as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._normalize_tree_row_payload(*args, **kwargs)
    def _schedule_detail_render_batch(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_analysis_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._schedule_detail_render_batch(self, *args, **kwargs)
    def _finish_detail_render(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_analysis_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._finish_detail_render(self, *args, **kwargs)
    def _render_plain_tree_in_batches(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_analysis_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._render_plain_tree_in_batches(self, *args, **kwargs)
    def _render_grouped_merged_tree_in_batches(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_analysis_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._render_grouped_merged_tree_in_batches(self, *args, **kwargs)
    def _on_sidebar_tree_toggled(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_analysis_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._on_sidebar_tree_toggled(self, *args, **kwargs)
    def _on_sidebar_tree_selected(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_analysis_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._on_sidebar_tree_selected(self, *args, **kwargs)
    def _register_tree_support(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_analysis_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._register_tree_support(self, *args, **kwargs)
    def _on_tree_left_click(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_analysis_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._on_tree_left_click(self, *args, **kwargs)
    def _on_tree_copy_shortcut(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_analysis_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._on_tree_copy_shortcut(self, *args, **kwargs)
    def _copy_text_to_clipboard(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_helpers as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._copy_text_to_clipboard(self, *args, **kwargs)
    def _selected_tree_leaf_item_ids(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_helpers as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._selected_tree_leaf_item_ids(self, *args, **kwargs)
    def _has_copyable_tree_active_cell(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_helpers as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._has_copyable_tree_active_cell(self, *args, **kwargs)
    def _copy_tree_active_cell(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_analysis_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._copy_tree_active_cell(self, *args, **kwargs)
    def _copy_tree_selected_rows(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_analysis_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._copy_tree_selected_rows(self, *args, **kwargs)
    def _build_account_move_locator(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_helpers as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._build_account_move_locator(self, *args, **kwargs)
    def _build_locator_text(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_helpers as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._build_locator_text(self, *args, **kwargs)
    def _copy_tree_locator(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_analysis_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._copy_tree_locator(self, *args, **kwargs)
    def _selected_repairable_meta(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._selected_repairable_meta(self, *args, **kwargs)
    def _repair_candidates_for_tree(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._repair_candidates_for_tree(self, *args, **kwargs)
    def _can_add_selected_to_repair_collection(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._can_add_selected_to_repair_collection(self, *args, **kwargs)
    def _can_remove_selected_from_repair_collection(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._can_remove_selected_from_repair_collection(self, *args, **kwargs)
    def _repair_collection_rows(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._repair_collection_rows(self, *args, **kwargs)
    def _add_repair_collection_seeds(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._add_repair_collection_seeds(self, *args, **kwargs)
    def _remove_repair_collection_row_keys(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._remove_repair_collection_row_keys(self, *args, **kwargs)
    def add_selected_to_repair_collection(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod.add_selected_to_repair_collection(self, *args, **kwargs)
    def remove_selected_from_repair_collection(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod.remove_selected_from_repair_collection(self, *args, **kwargs)
    def has_latest_snapshot(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod.has_latest_snapshot(self, *args, **kwargs)
    def _collect_all_snapshot_rows(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._collect_all_snapshot_rows(self, *args, **kwargs)
    def collect_all_linked_empty_je(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod.collect_all_linked_empty_je(self, *args, **kwargs)
    def collect_all_svl_without_je(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod.collect_all_svl_without_je(self, *args, **kwargs)
    def open_repair_collection_dialog(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod.open_repair_collection_dialog(self, *args, **kwargs)
    def _show_tree_context_menu(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._show_tree_context_menu(self, *args, **kwargs)
    @staticmethod
    def _repair_row_is_editable(*args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._repair_row_is_editable(*args, **kwargs)
    @staticmethod
    def _repair_row_status_label(*args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._repair_row_status_label(*args, **kwargs)
    def _sync_repair_row_account_codes(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._sync_repair_row_account_codes(self, *args, **kwargs)
    def _repair_reference_prefix(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._repair_reference_prefix(self, *args, **kwargs)
    def _repair_generated_texts_for_row(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._repair_generated_texts_for_row(self, *args, **kwargs)
    @staticmethod
    def _set_repair_generated_flags(*args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._set_repair_generated_flags(*args, **kwargs)
    def _sync_repair_generated_flags(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._sync_repair_generated_flags(self, *args, **kwargs)
    def _pcb_case1_generated_texts_for_row(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._pcb_case1_generated_texts_for_row(self, *args, **kwargs)
    @staticmethod
    def _set_pcb_case1_generated_flags(*args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._set_pcb_case1_generated_flags(*args, **kwargs)
    def _sync_pcb_case1_generated_flags(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._sync_pcb_case1_generated_flags(self, *args, **kwargs)
    def _refresh_generated_pcb_case1_texts(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._refresh_generated_pcb_case1_texts(self, *args, **kwargs)
    def _refresh_pcb_case1_collection_prefix_texts(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._refresh_pcb_case1_collection_prefix_texts(self, *args, **kwargs)
    def _refresh_generated_repair_texts(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._refresh_generated_repair_texts(self, *args, **kwargs)
    def _refresh_repair_collection_prefix_texts(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._refresh_repair_collection_prefix_texts(self, *args, **kwargs)
    def _refresh_repair_row_state(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._refresh_repair_row_state(self, *args, **kwargs)
    def _apply_repair_global_defaults_to_row(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._apply_repair_global_defaults_to_row(self, *args, **kwargs)
    @staticmethod
    def _repair_source_kind(*args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._repair_source_kind(*args, **kwargs)
    @staticmethod
    def _repair_source_label(*args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._repair_source_label(*args, **kwargs)
    @staticmethod
    def _repair_source_behavior_text(*args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._repair_source_behavior_text(*args, **kwargs)
    def _repair_dialog_source_note(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._repair_dialog_source_note(self, *args, **kwargs)
    @staticmethod
    def _group_repair_rows_by_source(*args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._group_repair_rows_by_source(*args, **kwargs)
    @staticmethod
    def _repair_group_header_text(*args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._repair_group_header_text(*args, **kwargs)
    @staticmethod
    def _repair_linked_move_label(*args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._repair_linked_move_label(*args, **kwargs)
    @staticmethod
    def _repair_selector_row_text(*args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._repair_selector_row_text(*args, **kwargs)
    @staticmethod
    def _repair_row_state_tag(*args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._repair_row_state_tag(*args, **kwargs)
    @staticmethod
    def _repair_selector_row_values(*args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._repair_selector_row_values(*args, **kwargs)
    @staticmethod
    def _repair_row_preview(*args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._repair_row_preview(*args, **kwargs)
    def _build_dashboard_repair_row(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._build_dashboard_repair_row(self, *args, **kwargs)
    @staticmethod
    def _apply_repair_result_to_row(*args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._apply_repair_result_to_row(*args, **kwargs)
    def _apply_repair_results_to_collection_entries(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._apply_repair_results_to_collection_entries(self, *args, **kwargs)
    def _repair_collection_all_repaired(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._repair_collection_all_repaired(self, *args, **kwargs)
    def _mark_active_repair_rows_failed(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._mark_active_repair_rows_failed(self, *args, **kwargs)
    def _ensure_repair_row_defaults(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._ensure_repair_row_defaults(self, *args, **kwargs)
    def _resolve_repair_accounts_async(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._resolve_repair_accounts_async(self, *args, **kwargs)
    def _start_repair(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._start_repair(self, *args, **kwargs)
    def _open_repair_dialog(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._open_repair_dialog(self, *args, **kwargs)
    def _open_repair_dialog_for_rows(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._open_repair_dialog_for_rows(self, *args, **kwargs)
    def _open_repair_dialog_for_rows_v2(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._open_repair_dialog_for_rows_v2(self, *args, **kwargs)
    def refresh_database_options(self) -> None:
        current = self._state_store.load()
        self._module_settings = current
        options = build_module_database_options(self.context.global_settings.database_profiles)
        self._db_label_by_profile_id, self._db_profile_id_by_label = build_option_maps(options)
        selected_id = normalize_database_profile_id(current.database_profile_id)
        if selected_id not in self._db_label_by_profile_id:
            selected_id = FOLLOW_GLOBAL_PROFILE_ID
        self.database_choice_var.set(self._db_label_by_profile_id.get(selected_id, ""))
        self._refresh_effective_database_display()

    def on_page_activated(self) -> None:
        self.resume()
        self.refresh_database_options()
        self.load_companies(force=False)

    def on_shared_configuration_updated(self, *, database_changed: bool, prefix_changed: bool) -> None:
        self.refresh_database_options()
        if prefix_changed:
            self._refresh_repair_collection_prefix_texts()
            self._refresh_pcb_case1_collection_prefix_texts()
        if database_changed:
            self._companies_loaded_for_profile_id = ""
            self._clear_snapshot(reset_company=True)
            if not self._busy:
                self.load_companies(force=False)

    def _selected_database_profile_id(self) -> str:
        label = normalize_text(self.database_choice_var.get())
        if label:
            return self._db_profile_id_by_label.get(label, FOLLOW_GLOBAL_PROFILE_ID)
        return normalize_database_profile_id(getattr(self._module_settings, "database_profile_id", FOLLOW_GLOBAL_PROFILE_ID))

    def _selected_dataset_mode(self) -> str:
        dataset_var = getattr(self, "dataset_mode_var", None)
        label = normalize_text(dataset_var.get()) if dataset_var is not None and hasattr(dataset_var, "get") else ""
        if label:
            return DATASET_MODE_LABELS.get(label, DEFAULT_DASHBOARD_DATASET_MODE)
        return normalize_dashboard_dataset_mode(
            getattr(getattr(self, "_module_settings", None), "dashboard_dataset_mode", DEFAULT_DASHBOARD_DATASET_MODE)
        )

    def _is_purchase_cycle_mode(self, value: str | None = None) -> bool:
        current = normalize_dashboard_dataset_mode(value if value is not None else self._selected_dataset_mode())
        return current == DATASET_MODE_PURCHASE_CYCLE_BALANCE

    def _save_settings(self) -> None:
        latest = self._state_store.load()
        latest.database_profile_id = self._selected_database_profile_id()
        latest.dashboard_company_id = self._selected_company_id()
        latest.dashboard_date_from = normalize_text(self.date_from_var.get())
        latest.dashboard_date_to = normalize_text(self.date_to_var.get())
        latest.dashboard_dataset_mode = self._selected_dataset_mode()
        latest.dashboard_include_inventory_accounts = bool(self.include_inventory_accounts_var.get())
        latest.dashboard_include_non_inventory_accounts = bool(self.include_non_inventory_accounts_var.get())
        latest.dashboard_logs_section_open = bool(self.dashboard_log_section.is_open)
        latest.pcb_problem_codes = normalize_text(self._pcb_problem_codes_var.get()) if hasattr(self, "_pcb_problem_codes_var") else latest.pcb_problem_codes
        latest.pcb_info_codes = normalize_text(self._pcb_info_codes_var.get()) if hasattr(self, "_pcb_info_codes_var") else latest.pcb_info_codes
        self._module_settings = latest
        self._state_store.save(latest)

    def _selected_company_id(self) -> int:
        current_label = normalize_text(self.company_choice_var.get())
        selected_label = self._label_for_company_id(self._selected_company_id_value)
        if current_label and current_label == selected_label:
            return int(self._selected_company_id_value or 0)
        return 0

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._refresh_busy_state()

    def _refresh_busy_state(self) -> None:
        state = "disabled" if self._busy else "normal"
        database_combo = getattr(self, "database_combo", None)
        if database_combo is not None:
            database_combo.configure(state="disabled" if self._busy else "readonly")
        dataset_mode_combo = getattr(self, "dataset_mode_combo", None)
        if dataset_mode_combo is not None:
            dataset_mode_combo.configure(state="disabled" if self._busy else "readonly")
        self.company_combo.configure(state=state)
        self.date_from_entry.configure(state=state)
        self.date_to_entry.configure(state=state)
        self.btn_refresh_companies.configure(state=state)
        self.btn_analyze.configure(state=state)
        export_state = "normal" if (not self._busy and self._latest_snapshot is not None) else "disabled"
        self.btn_export_html.configure(state=export_state)
        self.btn_export_json.configure(state=export_state)
        self._refresh_dataset_mode_widgets()

    def _append_log_batch(self, lines: list[str]) -> None:
        if not lines:
            return
        self.latest_log_line_var.set(build_compact_preview_text(lines[-1], empty_text="Belum ada log dashboard.", max_chars=140))
        append_bounded_text_lines(self.log_text, lines)

    def _append_log(self, text: str) -> None:
        self._append_log_batch([text])

    _POLL_LOG_BATCH_LIMIT = 50
    _POLL_UI_BATCH_LIMIT = 5

    def _poll_queues(self) -> None:
        if not self._poll_active:
            return
        log_lines: list[str] = []
        for _ in range(self._POLL_LOG_BATCH_LIMIT):
            try:
                line = self.log_queue.get_nowait()
            except queue.Empty:
                break
            log_lines.append(line)
        self._append_log_batch(log_lines)
        for _ in range(self._POLL_UI_BATCH_LIMIT):
            try:
                event = self.ui_queue.get_nowait()
            except queue.Empty:
                break
            self._handle_ui_event(event)
        self._poll_after_id = self.root.after(33 if self._busy else 250, self._poll_queues)

    def _handle_ui_event(self, event: dict[str, Any]) -> None:
        event_type = normalize_text(event.get("type"))
        if event_type == "busy":
            self._set_busy(bool(event.get("value", False)))
            return
        if event_type == "progress":
            snapshot = event["snapshot"]
            self._apply_progress(snapshot)
            return
        if event_type == "repair_progress":
            snapshot = event["snapshot"]
            self._apply_repair_progress(snapshot)
            return
        if event_type == "companies":
            self._apply_companies(list(event.get("companies") or []))
            self._set_busy(False)
            return
        if event_type == "snapshot":
            self._latest_base_url = normalize_text(event.get("base_url"))
            self._repair_refresh_pending = False
            self._analysis_refresh_pending = False
            request = event.get("request")
            if isinstance(request, SvlDashboardRequest):
                self._latest_analysis_request = SvlDashboardRequest(
                    database=normalize_text(request.database),
                    company_id=int(request.company_id or 0),
                    date_from=normalize_text(request.date_from),
                    date_to=normalize_text(request.date_to),
                    inventory_coa_codes=list(request.inventory_coa_codes or []),
                    dataset_mode=normalize_dashboard_dataset_mode(getattr(request, "dataset_mode", "")),
                    include_inventory_accounts=bool(getattr(request, "include_inventory_accounts", True)),
                    include_non_inventory_accounts=bool(getattr(request, "include_non_inventory_accounts", True)),
                )
            else:
                self._latest_analysis_request = None
            self._latest_analysis_profile_id = normalize_text(event.get("database_profile_id"))
            self._apply_progress(
                SvlDashboardProgressSnapshot(
                    phase="render",
                    processed=0,
                    total=1,
                    current="Menyiapkan tampilan...",
                    progress=0.96,
                )
            )
            self._log_ui_stage("UI snapshot received")
            self._schedule_snapshot_apply(event.get("snapshot"))
            return
        if event_type == "detail_batch_loaded":
            session_token = int(event.get("session_token") or 0)
            if session_token != int(getattr(self, "_detail_session_token", 0) or 0):
                return
            entries = list(event.get("entries") or [])
            loaded_count = 0
            for entry in entries:
                cache_key = tuple(entry.get("cache_key") or ())
                if len(cache_key) != 5:
                    continue
                self._detail_pending_keys.discard(cache_key)
                self._detail_error_by_key.pop(cache_key, None)
                detail = entry.get("detail")
                if isinstance(detail, SvlDashboardItemDetail):
                    self._detail_cache[cache_key] = detail
                    loaded_count += 1
            self._detail_warm_state = "completed"
            self._detail_warm_target_keys = set()
            self._post_paint_prefetch_cache_key = None
            total = int(event.get("total") or loaded_count or len(entries))
            self.phase_var.set(f"Cache detail lokal siap. {loaded_count}/{max(total, loaded_count, 1)}")
            self.status_var.set(self._snapshot_ready_status_text())
            self._append_log(f"Cache detail lokal siap untuk {loaded_count}/{max(total, loaded_count, 1)} item.")
            current_item = getattr(self, "_current_detail_item", None)
            if current_item is not None:
                self._current_detail_payload = self._detail_payload_for_item(current_item)
                self._render_selected_item_summary(current_item, self._full_detail_payload_for_item(current_item))
                self._dirty_detail_tabs.update({"compare"})
                if self._active_detail_tab == "compare":
                    self._schedule_active_detail_render(allow_autoload=False)
            return
        if event_type == "detail_batch_error":
            session_token = int(event.get("session_token") or 0)
            if session_token != int(getattr(self, "_detail_session_token", 0) or 0):
                return
            cache_keys = [
                tuple(cache_key)
                for cache_key in list(event.get("cache_keys") or [])
                if isinstance(cache_key, (list, tuple)) and len(tuple(cache_key)) == 5
            ]
            for cache_key in cache_keys:
                self._detail_pending_keys.discard(cache_key)
            self._detail_warm_state = "failed"
            self._detail_warm_target_keys = set()
            self._post_paint_prefetch_cache_key = None
            total = int(event.get("total") or len(cache_keys) or 0)
            message = normalize_text(event.get("message")) or "Gagal menyiapkan cache detail lokal."
            self.phase_var.set(f"Cache detail lokal gagal. 0/{max(total, 1)}")
            self.status_var.set(self._snapshot_ready_status_text())
            self._append_log(f"Cache detail lokal gagal: {message}. Fallback ke load on demand.")
            current_item = getattr(self, "_current_detail_item", None)
            if current_item is not None:
                self._current_detail_payload = self._detail_payload_for_item(current_item)
                self._dirty_detail_tabs.update({"compare"})
                if self._active_detail_tab == "compare":
                    self._schedule_active_detail_render(allow_autoload=True)
            return
        if event_type == "detail_loaded":
            session_token = int(event.get("session_token") or 0)
            if session_token != int(getattr(self, "_detail_session_token", 0) or 0):
                return
            cache_key = tuple(event.get("cache_key") or ())
            if len(cache_key) == 5:
                self._detail_pending_keys.discard(cache_key)
                self._detail_error_by_key.pop(cache_key, None)
                detail = event.get("detail")
                if isinstance(detail, SvlDashboardItemDetail):
                    self._detail_cache[cache_key] = detail
                if cache_key == self._active_detail_cache_key():
                    current_item = getattr(self, "_current_detail_item", None)
                    self._current_detail_payload = self._detail_cache.get(cache_key)
                    current_item = getattr(self, "_current_detail_item", None)
                    if current_item is not None and self._current_detail_payload is not None:
                        self._render_selected_item_summary(current_item, self._full_detail_payload_for_item(current_item))
                    self._dirty_detail_tabs.update({"compare"})
                    if self._active_detail_tab == "compare":
                        self._schedule_active_detail_render(allow_autoload=True)
            return
        if event_type == "detail_error":
            session_token = int(event.get("session_token") or 0)
            if session_token != int(getattr(self, "_detail_session_token", 0) or 0):
                return
            cache_key = tuple(event.get("cache_key") or ())
            if len(cache_key) == 5:
                self._detail_pending_keys.discard(cache_key)
                self._detail_error_by_key[cache_key] = normalize_text(event.get("message")) or "Gagal memuat detail item."
                if cache_key == self._active_detail_cache_key():
                    current_item = getattr(self, "_current_detail_item", None)
                    self._current_detail_payload = self._detail_payload_for_item(current_item) if current_item is not None else None
                    current_item = getattr(self, "_current_detail_item", None)
                    if current_item is not None:
                        self._render_selected_item_summary(current_item, None)
                    self._dirty_detail_tabs.update({"compare"})
                    if self._active_detail_tab == "compare":
                        self._schedule_active_detail_render(allow_autoload=True)
            return
        if event_type == "_pcb_repair_done":
            msg = normalize_text(event.get("msg")) or "Selesai."
            self.status_var.set(msg)
            cb = event.get("on_done")
            if callable(cb):
                cb(msg)
            if bool(event.get("refresh_analysis")):
                self._request_analysis_refresh()
            return
        if event_type == "export_complete":
            self._set_busy(False)
            path = normalize_text(event.get("path"))
            if path:
                latest = self._state_store.load()
                latest.last_output_dir = str(Path(path).parent)
                self._module_settings = latest
                self._state_store.save(latest)
            self.status_var.set(f"Export selesai: {path}")
            return
        if event_type == "export_error":
            self._set_busy(False)
            message = normalize_text(event.get("message")) or "Terjadi error saat export dashboard."
            self.status_var.set(message)
            messagebox.showerror(self._display_name, message)
            return
        if event_type == "repair_complete":
            self._latest_base_url = normalize_text(event.get("base_url")) or self._latest_base_url
            self._set_busy(False)
            self._set_repair_dialog_running_state(False)
            result_rows = list(event.get("results") or [])
            latest_snapshot = getattr(self, "_latest_snapshot", None)
            repair_database = normalize_text(event.get("database")) or normalize_text(
                latest_snapshot.database if latest_snapshot is not None else ""
            )
            self._active_repair_row_keys = set()
            self._recent_repair_results_by_svl_id = {
                int(row.relinked_svl_id or 0): row
                for row in result_rows
                if int(row.relinked_svl_id or 0) > 0 and normalize_text(row.status).upper() != "ERROR"
            }
            self._apply_repair_results_to_collection_entries(result_rows)
            apply_results_to_rows = getattr(self, "_last_repair_dialog_widgets", {}).get("apply_results_to_rows")
            if callable(apply_results_to_rows):
                apply_results_to_rows(result_rows)
            success_count = sum(1 for row in result_rows if normalize_text(row.status).upper() != "ERROR")
            error_count = sum(1 for row in result_rows if normalize_text(row.status).upper() == "ERROR")
            self.status_var.set(f"Repair selesai. {success_count} berhasil, {error_count} gagal.")
            self._show_repair_result_summary(result_rows, database=repair_database)
            if self._repair_collection_all_repaired():
                self.clear_repair_collection()
            if success_count > 0:
                self._request_analysis_refresh(preserve_repair_results=True)
            return
        if event_type == "error":
            self._set_busy(False)
            self._set_repair_dialog_running_state(False)
            message = normalize_text(event.get("message")) or "Terjadi error dashboard."
            self._mark_active_repair_rows_failed(message)
            self._active_repair_row_keys = set()
            self.status_var.set(message)
            self._set_repair_progress_message(message)
            messagebox.showerror(self._display_name, message)

    def _schedule_snapshot_apply(self, snapshot: SvlDashboardSnapshot | None) -> None:
        self._cancel_scheduled_snapshot_apply()
        self._pending_snapshot_to_apply = snapshot
        self._log_ui_stage("UI snapshot apply scheduled")
        after_idle = getattr(self.root, "after_idle", None)
        callback = self._begin_snapshot_apply
        if callable(after_idle):
            self._snapshot_apply_after_id = after_idle(callback)
            return
        self._snapshot_apply_after_id = self.root.after(0, callback)

    def _begin_snapshot_apply(self) -> None:
        self._snapshot_apply_after_id = None
        snapshot = self._pending_snapshot_to_apply
        self._pending_snapshot_to_apply = None
        self._log_ui_stage("UI snapshot apply started")
        self._apply_snapshot(snapshot)

    def _schedule_post_paint_prefetch(self, *, delay_ms: int = 150) -> None:
        if not bool(getattr(self, "_snapshot_interactive_ready", False)):
            return
        warm_state = normalize_text(getattr(self, "_detail_warm_state", ""))
        if warm_state in {"scheduled", "running", "completed", "failed"}:
            return
        snapshot = getattr(self, "_latest_snapshot", None)
        request = getattr(self, "_latest_analysis_request", None)
        if snapshot is None or request is None:
            self._post_paint_prefetch_cache_key = None
            return
        cache_entries = [
            (cache_key, pid)
            for cache_key, pid in self._snapshot_detail_cache_entries(snapshot, request)
            if cache_key not in self._detail_cache and cache_key not in self._detail_pending_keys
        ]
        if not cache_entries:
            self._detail_warm_state = "completed"
            self._detail_warm_target_keys = set()
            self._post_paint_prefetch_cache_key = None
            return
        self._cancel_post_paint_prefetch(clear_cache_key=False)
        self._detail_warm_state = "scheduled"
        self._detail_warm_target_keys = {cache_key for cache_key, _pid in cache_entries}
        self._post_paint_prefetch_cache_key = cache_entries[0][0]
        total = len(cache_entries)
        self.phase_var.set(f"Menyiapkan cache detail lokal... 0/{total}")
        self._log_ui_stage("UI detail warm scheduled")

        request_copy = SvlDashboardRequest(
            database=normalize_text(request.database),
            company_id=int(request.company_id or 0),
            date_from=normalize_text(request.date_from),
            date_to=normalize_text(request.date_to),
            inventory_coa_codes=list(request.inventory_coa_codes or []),
            dataset_mode=normalize_dashboard_dataset_mode(getattr(request, "dataset_mode", "")),
            include_inventory_accounts=bool(getattr(request, "include_inventory_accounts", True)),
            include_non_inventory_accounts=bool(getattr(request, "include_non_inventory_accounts", True)),
        )
        profile_id = normalize_text(self._latest_analysis_profile_id) or self._selected_database_profile_id()
        session_token = int(getattr(self, "_detail_session_token", 0) or 0)

        def _run_prefetch() -> None:
            self._post_paint_prefetch_after_id = None
            if session_token != int(getattr(self, "_detail_session_token", 0) or 0):
                return
            if normalize_text(getattr(self, "_detail_warm_state", "")) != "scheduled":
                return
            self._detail_warm_state = "running"
            self._detail_pending_keys.update(self._detail_warm_target_keys)
            for cache_key, _pid in cache_entries:
                self._detail_error_by_key.pop(cache_key, None)
            self.log_queue.put(f"Menyiapkan cache detail lokal untuk {total} item...")
            self.phase_var.set(f"Menyiapkan cache detail lokal... 0/{total}")

            def worker() -> None:
                try:
                    settings, config = self._runtime_builder(
                        self.context.global_settings,
                        self.logger,
                        database_profile_id=profile_id,
                    )

                    async def _run() -> dict[int, SvlDashboardItemDetail]:
                        async with AsyncOdooJsonRpcClient(config=config, settings=settings, logger=self.logger) as rpc:
                            service = SvlDashboardServiceAsync(
                                rpc=rpc,
                                logger=self.logger,
                                on_log=self.log_queue.put,
                            )
                            return await service.fetch_many_item_details(
                                request_copy,
                                [pid for _cache_key, pid in cache_entries],
                            )

                    details_by_pid = asyncio.run(_run())
                    self.ui_queue.put(
                        {
                            "type": "detail_batch_loaded",
                            "entries": [
                                {"cache_key": cache_key, "detail": details_by_pid.get(pid)}
                                for cache_key, pid in cache_entries
                            ],
                            "session_token": session_token,
                            "total": total,
                        }
                    )
                except Exception as exc:  # noqa: BLE001
                    self.log_queue.put(f"ERROR: {exc}")
                    self.ui_queue.put(
                        {
                            "type": "detail_batch_error",
                            "cache_keys": [cache_key for cache_key, _pid in cache_entries],
                            "message": str(exc),
                            "session_token": session_token,
                            "total": total,
                        }
                    )

            threading.Thread(target=worker, daemon=True, name="svl-dashboard-detail-warm").start()

        self._post_paint_prefetch_after_id = self.root.after(delay_ms, _run_prefetch)

    def _apply_progress(self, snapshot: SvlDashboardProgressSnapshot) -> None:
        self.progress_value.set(round(float(snapshot.progress or 0.0) * 100, 2))
        self.percent_var.set(f"{int(round(float(snapshot.progress or 0.0) * 100))}%")
        self.phase_var.set(snapshot.current or snapshot.phase or "-")

    def _set_repair_dialog_running_state(self, running: bool) -> None:
        widgets = getattr(self, "_last_repair_dialog_widgets", {})
        widgets["repair_running"] = bool(running)
        callback = widgets.get("set_dialog_busy_state")
        if callable(callback):
            callback(bool(running))

    def _set_repair_progress_message(self, message: str) -> None:
        widgets = getattr(self, "_last_repair_dialog_widgets", {})
        current_var = widgets.get("repair_progress_current_var")
        if hasattr(current_var, "set"):
            current_var.set(normalize_text(message) or "-")

    def _apply_repair_progress(self, snapshot: SvlDashboardRepairProgressSnapshot) -> None:
        processed = int(snapshot.processed or 0)
        total = int(snapshot.total or 0)
        progress_pct = round(float(snapshot.progress or 0.0) * 100, 2)
        count_text = f"{processed} / {total}" if total > 0 else "0 / 0"
        percent_text = f"{int(round(progress_pct))}%"
        current_text = normalize_text(snapshot.current) or normalize_text(snapshot.phase) or "-"
        meta_text = f"Berhasil {int(snapshot.success_count or 0)} | Gagal {int(snapshot.error_count or 0)}"

        widgets = getattr(self, "_last_repair_dialog_widgets", {})
        progress_value_var = widgets.get("repair_progress_value_var")
        count_var = widgets.get("repair_progress_count_var")
        percent_var = widgets.get("repair_progress_percent_var")
        current_var = widgets.get("repair_progress_current_var")
        meta_var = widgets.get("repair_progress_meta_var")
        if hasattr(progress_value_var, "set"):
            progress_value_var.set(progress_pct)
        if hasattr(count_var, "set"):
            count_var.set(count_text)
        if hasattr(percent_var, "set"):
            percent_var.set(percent_text)
        if hasattr(current_var, "set"):
            current_var.set(current_text)
        if hasattr(meta_var, "set"):
            meta_var.set(meta_text)
        self.status_var.set(
            f"Repair berjalan {count_text} ({percent_text}) | Berhasil {int(snapshot.success_count or 0)} | "
            f"Gagal {int(snapshot.error_count or 0)} | {current_text}"
        )

    def _apply_companies(self, companies: list[SvlDashboardCompany]) -> None:
        self._cancel_scheduled_company_dropdown()
        self._company_last_query_for_autodrop = ""
        self._company_by_label = {company.label: company for company in companies}
        self._company_labels = [company.label for company in companies]
        self._company_search_cache = {
            company.label: " ".join(
                part
                for part in (
                    normalize_text(company.label).lower(),
                    normalize_text(company.name).lower(),
                    str(int(company.company_id or 0)),
                )
                if part
            )
            for company in companies
        }
        self._set_company_combo_values(self._company_labels)
        selected_id = int(self._module_settings.dashboard_company_id or 0)
        selected_label = self._label_for_company_id(selected_id)
        self._selected_company_id_value = selected_id if selected_label else 0
        self.company_choice_var.set(selected_label)
        if selected_id and not selected_label:
            self._clear_snapshot(reset_company=True)
        db_state = self._resolve_effective_database_state()
        self.status_var.set(
            f"Daftar company diperbarui untuk {db_state['source_text']}."
            if companies
            else f"Tidak ada company tersedia untuk {db_state['source_text']}."
        )

    def _filtered_company_labels(self, query: str) -> list[str]:
        clean_query = normalize_text(query).lower()
        if not clean_query:
            return list(self._company_labels)
        company_search_cache = getattr(self, "_company_search_cache", {})
        if not company_search_cache:
            company_search_cache = {
                label: " ".join(
                    part
                    for part in (
                        normalize_text(label).lower(),
                        normalize_text(getattr(self._company_by_label.get(label), "name", "")).lower(),
                        str(int(getattr(self._company_by_label.get(label), "company_id", 0) or 0)),
                    )
                    if part
                )
                for label in self._company_labels
            }
        ranked: list[tuple[int, int, str]] = []
        compact_query = clean_query.replace(" ", "")
        for label in self._company_labels:
            search_text = company_search_cache.get(label, "")
            if not search_text:
                continue
            tier = 99
            if search_text.startswith(clean_query):
                tier = 0
            elif any(token.startswith(clean_query) for token in search_text.split()):
                tier = 1
            elif clean_query in search_text:
                tier = 2
            elif compact_query and _is_subsequence_match(compact_query, search_text.replace(" ", "")):
                tier = 3
            elif any(_bounded_damerau_levenshtein(clean_query, token, max_distance=1) <= 1 for token in search_text.split()):
                tier = 4
            if tier < 99:
                ranked.append((tier, len(label), label))
        ranked.sort()
        return [label for _tier, _length, label in ranked[:COMPANY_SUGGESTION_LIMIT]]

    def _label_for_company_id(self, company_id: int) -> str:
        if company_id <= 0:
            return ""
        for label in getattr(self, "_company_labels", []):
            company = getattr(self, "_company_by_label", {}).get(label)
            if company is not None and company.company_id == company_id:
                return label
        return ""

    def _selected_company_name(self) -> str:
        company_id = int(self._selected_company_id() or 0)
        snapshot = getattr(self, "_latest_snapshot", None)
        try:
            snapshot_company_id = int(getattr(snapshot, "company_id", 0) or 0) if snapshot is not None else 0
        except (TypeError, ValueError):
            snapshot_company_id = 0
        if snapshot is not None and snapshot_company_id == company_id:
            return normalize_text(getattr(snapshot, "company_name", ""))
        label = self._label_for_company_id(company_id)
        company = getattr(self, "_company_by_label", {}).get(label)
        if company is not None:
            return normalize_text(company.name)
        return ""

    @staticmethod
    def _measured_widget_width(widget: Any) -> int:
        if widget is None:
            return 0
        try:
            return max(int(widget.winfo_width() or 0), int(widget.winfo_reqwidth() or 0))
        except Exception:  # noqa: BLE001
            return 0

    @staticmethod
    def _actual_widget_width(widget: Any) -> int:
        if widget is None:
            return 0
        try:
            return int(widget.winfo_width() or 0)
        except Exception:  # noqa: BLE001
            return 0

    @staticmethod
    def _requested_widget_width(widget: Any) -> int:
        if widget is None:
            return 0
        try:
            return int(widget.winfo_reqwidth() or 0)
        except Exception:  # noqa: BLE001
            return 0

    @classmethod
    def _priority_widget_width(cls, *widgets: Any) -> int:
        for widget in widgets:
            width = cls._actual_widget_width(widget)
            if width > 1:
                return width
        for widget in widgets:
            width = cls._requested_widget_width(widget)
            if width > 1:
                return width
        return 0

    @staticmethod
    def _scaled_progress_row_widths(
        total_width: int,
        *,
        target_ratio: float,
        min_bar_width: int,
        min_info_width: int,
    ) -> tuple[int, int]:
        usable_width = int(total_width or 0)
        if usable_width <= 0:
            return (min_bar_width, min_info_width)
        target_bar_width = max(min_bar_width, int(usable_width * target_ratio))
        max_bar_width = max(PROGRESS_TEXT_WRAP_MIN_WIDTH, usable_width - min_info_width)
        if usable_width >= min_bar_width + min_info_width:
            bar_width = min(target_bar_width, max_bar_width)
            info_width = max(min_info_width, usable_width - bar_width)
            return (bar_width, info_width)
        info_width = max(PROGRESS_TEXT_WRAP_MIN_WIDTH, min(min_info_width, max(usable_width // 3, PROGRESS_TEXT_WRAP_MIN_WIDTH)))
        bar_width = max(PROGRESS_TEXT_WRAP_MIN_WIDTH, usable_width - info_width)
        return (bar_width, max(PROGRESS_TEXT_WRAP_MIN_WIDTH, usable_width - bar_width))

    @staticmethod
    def _dashboard_progress_row_widths(total_width: int) -> tuple[int, int]:
        return SvlFixJeDashboardPage._scaled_progress_row_widths(
            total_width,
            target_ratio=DASHBOARD_PROGRESS_BAR_TARGET_RATIO,
            min_bar_width=DASHBOARD_PROGRESS_BAR_MIN_WIDTH,
            min_info_width=DASHBOARD_PROGRESS_INFO_MIN_WIDTH,
        )

    @staticmethod
    def _repair_progress_row_widths(total_width: int) -> tuple[int, int]:
        return SvlFixJeDashboardPage._scaled_progress_row_widths(
            total_width,
            target_ratio=REPAIR_PROGRESS_BAR_TARGET_RATIO,
            min_bar_width=REPAIR_PROGRESS_BAR_MIN_WIDTH,
            min_info_width=REPAIR_PROGRESS_INFO_MIN_WIDTH,
        )

    def _bind_progress_row_layout(
        self,
        row: Any,
        progress_host: Any,
        progress_bar: Any,
        info_host: Any,
        flexible_label: Any,
        *fixed_widgets: Any,
        width_resolver: Callable[[int], tuple[int, int]],
        left_weight: int,
        right_weight: int,
        measure_width: Callable[[], int] | None = None,
        watch_widgets: tuple[Any, ...] = (),
    ) -> Callable[[tk.Event | None], None]:
        last_layout = {"bar": -1, "info": -1, "wrap": -1}

        def refresh(_event: tk.Event | None = None) -> None:
            total_width = int(measure_width() or 0) if callable(measure_width) else 0
            if total_width <= 0:
                total_width = max(
                    self._measured_widget_width(row),
                    self._measured_widget_width(getattr(row, "master", None)),
                )
            bar_width, info_width = width_resolver(total_width)
            reserved_width = sum(self._measured_widget_width(widget) for widget in fixed_widgets)
            available_width = max(PROGRESS_TEXT_WRAP_MIN_WIDTH, info_width - reserved_width - 16)
            if (
                last_layout["bar"] == bar_width
                and last_layout["info"] == info_width
                and last_layout["wrap"] == available_width
            ):
                return
            last_layout["bar"] = bar_width
            last_layout["info"] = info_width
            last_layout["wrap"] = available_width
            try:
                row.grid_columnconfigure(0, weight=left_weight, minsize=bar_width)
                row.grid_columnconfigure(1, weight=right_weight, minsize=info_width)
                progress_host.configure(width=bar_width)
                info_host.configure(width=info_width)
                progress_bar.configure(length=bar_width)
            except Exception:  # noqa: BLE001
                pass
            try:
                flexible_label.configure(wraplength=available_width)
            except Exception:  # noqa: BLE001
                pass

        seen_widgets: set[int] = set()
        for widget in (row, getattr(row, "master", None), *watch_widgets):
            if widget is None:
                continue
            widget_id = id(widget)
            if widget_id in seen_widgets:
                continue
            seen_widgets.add(widget_id)
            try:
                widget.bind("<Configure>", refresh, add="+")
            except Exception:  # noqa: BLE001
                pass
        try:
            self.root.after_idle(refresh)
        except Exception:  # noqa: BLE001
            refresh()
        return refresh

    def _set_company_combo_values(self, labels: list[str]) -> list[str]:
        self._company_active_labels = list(labels)
        combo = getattr(self, "company_combo", None)
        if combo is not None:
            combo.configure(values=self._company_active_labels)
        return list(self._company_active_labels)

    @staticmethod
    def _normalized_company_autodrop_query(query: str) -> str:
        return normalize_text(query).strip().lower()

    def _company_search_query(self, *, dropdown_request: bool = False) -> str:
        current_label = normalize_text(self.company_choice_var.get())
        if not dropdown_request:
            return current_label
        selected_label = self._label_for_company_id(int(self._selected_company_id_value or 0))
        if current_label and current_label == selected_label:
            return ""
        return current_label

    def _refresh_company_suggestions(
        self,
        *,
        query: str | None = None,
        dropdown_request: bool = False,
    ) -> list[str]:
        clean_query = normalize_text(
            self._company_search_query(dropdown_request=dropdown_request) if query is None else query
        )
        labels = self._filtered_company_labels(clean_query) if clean_query else list(self._company_labels)
        return self._set_company_combo_values(labels)

    def _cancel_scheduled_company_dropdown(self) -> None:
        current_after_id = getattr(self, "_company_dropdown_after_id", None)
        if current_after_id is not None and hasattr(self, "root"):
            after_cancel = getattr(self.root, "after_cancel", None)
            if callable(after_cancel):
                try:
                    after_cancel(current_after_id)
                except Exception:  # noqa: BLE001
                    pass
        self._company_dropdown_after_id = None

    def _run_company_dropdown_open(self) -> None:
        self._company_dropdown_after_id = None
        query = self._normalized_company_autodrop_query(self.company_choice_var.get())
        if len(query) < COMPANY_AUTODROPDOWN_MIN_CHARS:
            return
        if not getattr(self, "_company_active_labels", None):
            return
        combo = getattr(self, "company_combo", None)
        if combo is None:
            return
        try:
            combo.event_generate("<Down>")
        except Exception:  # noqa: BLE001
            pass

    def _schedule_company_dropdown_open(self) -> None:
        self._cancel_scheduled_company_dropdown()
        try:
            self._company_dropdown_after_id = self.root.after_idle(self._run_company_dropdown_open)
        except Exception:  # noqa: BLE001
            self._run_company_dropdown_open()

    def _cancel_scheduled_company_commit(self) -> None:
        current_after_id = getattr(self, "_company_commit_after_id", None)
        if current_after_id is not None and hasattr(self, "root"):
            after_cancel = getattr(self.root, "after_cancel", None)
            if callable(after_cancel):
                try:
                    after_cancel(current_after_id)
                except Exception:  # noqa: BLE001
                    pass
        self._company_commit_after_id = None

    def _commit_company_selection(self) -> None:
        self._cancel_scheduled_company_dropdown()
        self._company_last_query_for_autodrop = ""
        label = normalize_text(self.company_choice_var.get())
        company = self._company_by_label.get(label)
        if company is None:
            self._restore_company_input()
            return
        self._selected_company_id_value = int(company.company_id or 0)
        self.company_choice_var.set(company.label)
        self._restore_company_input()
        self._save_settings()
        self._clear_snapshot(reset_company=False)

    def _run_scheduled_company_commit(self) -> None:
        self._company_commit_after_id = None
        self._commit_company_selection()

    def _schedule_company_commit(self) -> None:
        self._cancel_scheduled_company_commit()
        try:
            self._company_commit_after_id = self.root.after_idle(self._run_scheduled_company_commit)
        except Exception:  # noqa: BLE001
            self._run_scheduled_company_commit()

    def _restore_company_input(self, _event: tk.Event | None = None) -> str | None:
        self._cancel_scheduled_company_dropdown()
        self._company_last_query_for_autodrop = ""
        self._set_company_combo_values(self._company_labels)
        current_label = normalize_text(self.company_choice_var.get())
        if current_label in self._company_by_label:
            self.company_choice_var.set(current_label)
            return None
        self.company_choice_var.set(self._label_for_company_id(int(self._selected_company_id_value or 0)))
        return None

    def _current_search_query(self) -> str:
        if self._search_placeholder_active:
            return ""
        return normalize_text(self.search_var.get())

    def _apply_search_placeholder(self, *, render: bool = True) -> None:
        self._search_placeholder_active = True
        self.search_var.set(SEARCH_PLACEHOLDER)
        self.search_entry.configure(fg=T.TEXT_MUTED)
        if render and hasattr(self, "sidebar_tree"):
            self.render_item_cards()

    def _clear_search_placeholder(self, *, render: bool = True) -> None:
        self._search_placeholder_active = False
        self.search_var.set("")
        self.search_entry.configure(fg=T.TEXT_ON_LIGHT)
        if render and hasattr(self, "sidebar_tree"):
            self.render_item_cards()

    def _on_search_focus_in(self, _event: tk.Event | None = None) -> None:
        if self._search_placeholder_active:
            self._clear_search_placeholder(render=False)

    def _on_search_focus_out(self, _event: tk.Event | None = None) -> None:
        if normalize_text(self.search_var.get()):
            self.search_entry.configure(fg=T.TEXT_ON_LIGHT)
            self._search_placeholder_active = False
            return
        self._apply_search_placeholder()

    def _schedule_sidebar_filter(self, *_args) -> None:
        if not hasattr(self, "sidebar_tree") or self._search_placeholder_active:
            return
        if self._sidebar_filter_after_id is not None:
            try:
                self.root.after_cancel(self._sidebar_filter_after_id)
            except Exception:  # noqa: BLE001
                pass
        self._sidebar_filter_after_id = self.root.after(200, self._apply_sidebar_filter)

    def _apply_sidebar_filter(self) -> None:
        self._sidebar_filter_after_id = None
        if self._is_purchase_cycle_mode():
            self._apply_pcb_search_filter()
            return
        self.render_item_cards()

    def _apply_pcb_search_filter(self) -> None:
        """Re-render PCB sidebar filtered by current search query and status checkboxes."""
        query = self._current_search_query().lower()
        snapshot = self._latest_snapshot
        cycles = list(getattr(snapshot, "purchase_cycles", None) or []) if snapshot else []
        if query:
            cycles = [
                c for c in cycles
                if query in c.picking_name.lower()
                or any(query in n.lower() for n in (getattr(c, "picking_names", None) or []))
                or any(query in po.lower() for po in (c.purchase_orders or []))
                or query in (c.partner_name or "").lower()
                or any(query in ref.lower() for ref in (c.bill_refs or []))
                or any(query in ref.lower() for ref in (c.stj_refs or []))
                or any(query in ref.lower() for ref in (c.payment_refs or []))
                or any(
                    query in normalize_text(getattr(row, "move_name", "")).lower()
                    or query in normalize_text(getattr(row, "move_ref", "")).lower()
                    for row in (getattr(c, "adjustment_audit_rows", None) or [])
                )
            ]
        # Apply checkbox filter
        show_problem = bool(getattr(self, "_pcb_show_problem_var", None) and self._pcb_show_problem_var.get())
        show_partial = bool(getattr(self, "_pcb_show_partial_var", None) and self._pcb_show_partial_var.get())
        show_healthy = bool(getattr(self, "_pcb_show_healthy_var", None) and self._pcb_show_healthy_var.get())
        if not (show_problem and show_partial and show_healthy):
            cycles = [
                c for c in cycles
                if (show_problem and c.cycle_status == "problem")
                or (show_partial and c.cycle_status == "partial")
                or (show_healthy and c.cycle_status == "healthy")
            ]
        # Apply UoM filter
        uom_filter = normalize_text(
            self._pcb_uom_filter_var.get() if getattr(self, "_pcb_uom_filter_var", None) else ""
        ).lower()
        if uom_filter == "uom inline":
            cycles = [c for c in cycles if normalize_text(getattr(c, "uom_flag", "inline")).lower() != "mismatch"]
        elif uom_filter == "uom mismatch":
            cycles = [c for c in cycles if normalize_text(getattr(c, "uom_flag", "inline")).lower() == "mismatch"]
        self._render_purchase_cycle_sidebar(filtered_cycles=cycles)

    def _on_company_search(self, event: tk.Event | None = None) -> str | None:
        if self._busy:
            return None
        keysym = normalize_text(getattr(event, "keysym", ""))
        if keysym in {
            "Up",
            "Down",
            "Left",
            "Right",
            "Prior",
            "Next",
            "Home",
            "End",
            "Tab",
            "ISO_Left_Tab",
            "Return",
            "KP_Enter",
            "Shift_L",
            "Shift_R",
            "Control_L",
            "Control_R",
            "Alt_L",
            "Alt_R",
        }:
            return None
        if keysym == "Escape":
            return self._restore_company_input()
        active_labels = self._refresh_company_suggestions(query=self.company_choice_var.get())
        normalized_query = self._normalized_company_autodrop_query(self.company_choice_var.get())
        previous_query = normalize_text(getattr(self, "_company_last_query_for_autodrop", ""))
        if len(normalized_query) >= COMPANY_AUTODROPDOWN_MIN_CHARS and active_labels:
            self._company_last_query_for_autodrop = normalized_query
            if len(previous_query) < COMPANY_AUTODROPDOWN_MIN_CHARS:
                self._schedule_company_dropdown_open()
        else:
            self._cancel_scheduled_company_dropdown()
            self._company_last_query_for_autodrop = ""
        return None

    def _on_company_dropdown_requested(self) -> None:
        if self._busy:
            return
        self._cancel_scheduled_company_dropdown()
        self._refresh_company_suggestions(dropdown_request=True)

    def _on_company_return(self, _event: tk.Event | None = None) -> str | None:
        if self._busy:
            return None
        self._schedule_company_commit()
        return None

    @staticmethod
    def _placeholder_values(columns: tuple[str, ...], message: str) -> tuple[str, ...]:
        values = ["" for _ in columns]
        if values:
            values[0] = message
        return tuple(values)

    def _log_ui_stage(self, message: str) -> None:
        self._ui_stage_seq = int(getattr(self, "_ui_stage_seq", 0) or 0) + 1
        logger = getattr(self, "logger", None)
        if logger is not None:
            try:
                logger.info(message)
            except Exception:  # noqa: BLE001
                pass
        log_queue = getattr(self, "log_queue", None)
        if log_queue is not None:
            try:
                log_queue.put(message)
            except Exception:  # noqa: BLE001
                pass

    @staticmethod
    def _item_record_count(item: Any, count_attr: str, list_attr: str) -> int:
        try:
            explicit_count = int(getattr(item, count_attr, 0) or 0)
        except (TypeError, ValueError):
            explicit_count = 0
        if explicit_count > 0:
            return explicit_count
        return len(getattr(item, list_attr, []) or [])

    def _item_primary_amount(self, item: Any) -> float:
        return float(getattr(item, "difference", 0.0) or 0.0)

    def _item_primary_amount_label(self) -> str:
        return "Diff"

    def _item_position_label(self, item: Any) -> str:
        amount = self._item_primary_amount(item)
        return "DEBIT" if amount >= 0 else "KREDIT"

    def _detail_cache_key(self, request: SvlDashboardRequest, product_id: int) -> tuple[str, int, str, str, int]:
        dataset_tag = normalize_dashboard_dataset_mode(getattr(request, "dataset_mode", ""))
        scope_tag = (
            f"inv_{1 if bool(getattr(request, 'include_inventory_accounts', True)) else 0}"
            f"_noninv_{1 if bool(getattr(request, 'include_non_inventory_accounts', True)) else 0}"
        )
        return (
            f"{normalize_text(request.database)}|{dataset_tag}|{scope_tag}",
            int(request.company_id or 0),
            normalize_text(request.date_from),
            normalize_text(request.date_to),
            int(product_id or 0),
        )

    def _detail_cache_key_for_item(self, item: Any) -> tuple[str, int, str, str, int] | None:
        request = getattr(self, "_latest_analysis_request", None)
        if request is None:
            return None
        return self._detail_cache_key(request, int(getattr(item, "pid", 0) or 0))

    def _full_detail_payload_for_item(self, item: Any) -> SvlDashboardItemDetail | None:
        cache_key = self._detail_cache_key_for_item(item)
        if cache_key is None:
            return None
        return self._detail_cache.get(cache_key)

    @staticmethod
    def _coerce_int(value: Any) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _coerce_float(value: Any) -> float:
        try:
            return float(value or 0.0)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _coerce_list(value: Any) -> list[Any]:
        if isinstance(value, list):
            return list(value)
        if isinstance(value, tuple):
            return list(value)
        return []

    def _snapshot_detail_payload_for_item(self, item: Any) -> SvlDashboardItemDetail:
        return SvlDashboardItemDetail(
            pid=self._coerce_int(getattr(item, "pid", 0)),
            item_kind=normalize_text(getattr(item, "item_kind", "")) or "product",
            po_line_count=self._coerce_int(getattr(item, "po_line_count", 0)),
            bill_line_count=self._coerce_int(getattr(item, "bill_line_count", 0)),
            total_po_value=self._coerce_float(getattr(item, "total_po_value", 0.0)),
            total_bill_value=self._coerce_float(getattr(item, "total_bill_value", 0.0)),
            svl_records=self._coerce_list(getattr(item, "svl_records", [])),
            jnl_records=self._coerce_list(getattr(item, "jnl_records", [])),
            po_lines=self._coerce_list(getattr(item, "po_lines", [])),
            bill_lines=self._coerce_list(getattr(item, "bill_lines", [])),
            payable_line_count=self._coerce_int(getattr(item, "payable_line_count", 0)),
            total_payable_value=self._coerce_float(getattr(item, "total_payable_value", 0.0)),
            payable_lines=self._coerce_list(getattr(item, "payable_lines", [])),
            total_paid_value=self._coerce_float(getattr(item, "total_paid_value", 0.0)),
            total_unassigned_bill_remainder=self._coerce_float(getattr(item, "total_unassigned_bill_remainder", 0.0)),
            payment_status=normalize_text(getattr(item, "payment_status", "")),
            unassigned_bill_lines=self._coerce_list(getattr(item, "unassigned_bill_lines", [])),
        )

    def _detail_payload_for_item(self, item: Any) -> SvlDashboardItemDetail:
        return self._full_detail_payload_for_item(item) or self._snapshot_detail_payload_for_item(item)

    def _compare_summary_values(self, item: Any, detail: SvlDashboardItemDetail | None) -> tuple[int, int, float, float]:
        if detail is not None:
            return (
                int(detail.po_line_count or len(detail.po_lines or []) or 0),
                int(detail.bill_line_count or len(detail.bill_lines or []) or 0),
                float(detail.total_po_value or 0.0),
                float(detail.total_bill_value or 0.0),
            )
        return (
            self._item_record_count(item, "po_line_count", "po_lines"),
            self._item_record_count(item, "bill_line_count", "bill_lines"),
            float(getattr(item, "total_po_value", 0.0) or 0.0),
            float(getattr(item, "total_bill_value", 0.0) or 0.0),
        )

    def _detail_error_for_item(self, item: Any) -> str:
        cache_key = self._detail_cache_key_for_item(item)
        if cache_key is None:
            return ""
        return normalize_text(self._detail_error_by_key.get(cache_key))

    def _detail_waits_for_warm_cache(self, item: Any) -> bool:
        cache_key = self._detail_cache_key_for_item(item)
        if cache_key is None:
            return False
        warm_state = normalize_text(getattr(self, "_detail_warm_state", ""))
        if warm_state not in {"scheduled", "running"}:
            return False
        return cache_key in getattr(self, "_detail_warm_target_keys", set())

    def _detail_is_loading_for_item(self, item: Any) -> bool:
        cache_key = self._detail_cache_key_for_item(item)
        return bool((cache_key and cache_key in self._detail_pending_keys) or self._detail_waits_for_warm_cache(item))

    def _detail_pending_message_for_item(self, item: Any) -> str:
        if self._detail_waits_for_warm_cache(item):
            return "Menyiapkan cache detail lokal..."
        return "Memuat detail item..."

    def _snapshot_detail_cache_entries(
        self,
        snapshot: SvlDashboardSnapshot | None,
        request: SvlDashboardRequest | None = None,
    ) -> list[tuple[tuple[str, int, str, str, int], int]]:
        current_request = request or getattr(self, "_latest_analysis_request", None)
        if snapshot is None or current_request is None:
            return []
        return [
            (
                self._detail_cache_key(current_request, int(getattr(item, "pid", 0) or 0)),
                int(getattr(item, "pid", 0) or 0),
            )
            for item in getattr(snapshot, "items", []) or []
            if int(getattr(item, "pid", 0) or 0) != 0
        ]

    def _snapshot_ready_status_text(self, snapshot: SvlDashboardSnapshot | None = None) -> str:
        current_snapshot = snapshot if snapshot is not None else getattr(self, "_latest_snapshot", None)
        if current_snapshot is None:
            return "Belum ada hasil."
        dataset_mode = normalize_text(getattr(current_snapshot, "dataset_mode", ""))
        if dataset_mode == DATASET_MODE_PURCHASE_CYCLE_BALANCE:
            cycles = list(getattr(current_snapshot, "purchase_cycles", None) or [])
            problems = sum(1 for c in cycles if c.cycle_status == "problem")
            return (
                f"Analisis selesai. {len(cycles)} cycle ditemukan, {problems} bermasalah. "
                f"{self._format_snapshot_source_database(current_snapshot.database)}"
            )
        item_label = "item bermasalah"
        return (
            f"Analisis selesai. {len(current_snapshot.items)} {item_label}. "
            f"{self._format_snapshot_source_database(current_snapshot.database)}"
        )

    def _active_detail_cache_key(self) -> tuple[str, int, str, str, int] | None:
        item = getattr(self, "_current_detail_item", None)
        if item is None:
            return None
        return self._detail_cache_key_for_item(item)

    def _cancel_scheduled_snapshot_apply(self) -> None:
        current_after_id = getattr(self, "_snapshot_apply_after_id", None)
        if current_after_id is not None and hasattr(self, "root"):
            after_cancel = getattr(self.root, "after_cancel", None)
            if callable(after_cancel):
                try:
                    after_cancel(current_after_id)
                except Exception:  # noqa: BLE001
                    pass
        self._snapshot_apply_after_id = None
        self._pending_snapshot_to_apply = None

    def _cancel_post_paint_prefetch(self, *, clear_cache_key: bool = True) -> None:
        current_after_id = getattr(self, "_post_paint_prefetch_after_id", None)
        if current_after_id is not None and hasattr(self, "root"):
            after_cancel = getattr(self.root, "after_cancel", None)
            if callable(after_cancel):
                try:
                    after_cancel(current_after_id)
                except Exception:  # noqa: BLE001
                    pass
        self._post_paint_prefetch_after_id = None
        if normalize_text(getattr(self, "_detail_warm_state", "")) == "scheduled":
            self._detail_warm_state = "idle"
            self._detail_warm_target_keys = set()
        if clear_cache_key:
            self._post_paint_prefetch_cache_key = None
            if normalize_text(getattr(self, "_detail_warm_state", "")) != "running":
                self._detail_warm_state = "idle"
                self._detail_warm_target_keys = set()

    def _tab_requires_lazy_detail(self, tab_id: str, item: Any | None = None) -> bool:
        current_item = item if item is not None else getattr(self, "_current_detail_item", None)
        if current_item is not None and normalize_text(getattr(current_item, "item_kind", "")) == "unassigned_journal":
            return tab_id == "compare"
        return tab_id == "compare"

    @staticmethod
    def _parse_journal_code(move_name: str) -> str:
        clean_name = normalize_text(move_name)
        if "/" in clean_name:
            return normalize_text(clean_name.split("/", 1)[0])
        return clean_name or "STJ"

    @staticmethod
    def _parse_amount_text(value: str) -> float:
        clean_value = normalize_text(value).strip()
        if not clean_value:
            return 0.0
        if "," in clean_value and "." in clean_value:
            if clean_value.rfind(",") > clean_value.rfind("."):
                clean_value = clean_value.replace(".", "").replace(",", ".")
            else:
                clean_value = clean_value.replace(",", "")
        elif "," in clean_value:
            clean_value = clean_value.replace(".", "").replace(",", ".")
        return float(clean_value)

    @staticmethod
    def _repair_target_options(rows: list[dict[str, Any]]) -> list[tuple[str, str]]:
        options = [("New JE + Relink SVL", "new_and_relink")]
        if rows and all(int(row.get("move_id") or 0) > 0 for row in rows):
            options.append(("Rewrite Existing JE", "fill_existing"))
        return options

    @staticmethod
    def _repair_target_mode_label(value: str) -> str:
        clean_value = normalize_text(value).lower()
        if clean_value == "fill_existing":
            return "Rewrite Existing JE"
        return "New JE + Relink SVL"

    @staticmethod
    def _repair_error_label(value: str) -> str:
        mapping = {
            "lock_date": "Lock date",
            "draft_failed": "Reset to draft gagal",
            "repost_failed": "Repost gagal",
            "relink_failed": "Relink SVL gagal",
            "account_not_found": "Akun tidak ditemukan",
            "journal_not_found": "Journal tidak ditemukan",
            "invalid_date": "Tanggal tidak valid",
            "lock_date_lookup_failed": "Lock date tidak terbaca",
        }
        clean_value = normalize_text(value).lower()
        return mapping.get(clean_value, clean_value.replace("_", " ").strip().title() or "Error")

    def _default_repair_date_for_seed(self, row: dict[str, Any]) -> str:
        original_date = normalize_text(row.get("date"))[:10]
        today_text = date.today().strftime("%Y-%m-%d")
        move_state = normalize_text(row.get("move_state")).lower()
        move_id = int(row.get("move_id") or 0)
        if move_id > 0 and move_state == "posted":
            if not original_date or original_date < today_text:
                return today_text
        return original_date or today_text

    def _repair_target_warning_text(self, mode_value: str, rows: list[dict[str, Any]]) -> str:
        clean_mode = normalize_text(mode_value).lower()
        if clean_mode == "fill_existing" and any(int(row.get("move_id") or 0) > 0 for row in rows):
            return "Rewrite Existing JE akan mengubah move historis langsung. Gunakan hanya bila Anda memang ingin edit STJ lama."
        if clean_mode == "new_and_relink" and any(int(row.get("move_id") or 0) > 0 for row in rows):
            return "Move baru akan dibuat dan SVL direlink. Move lama kosong dibiarkan apa adanya sebagai mark only."
        return ""

    @staticmethod
    def _repair_result_transaction_label(move_name: str, move_id: int) -> str:
        clean_name = normalize_text(move_name)
        if clean_name:
            return clean_name
        return str(int(move_id or 0)) if int(move_id or 0) > 0 else "-"

    @staticmethod
    def _repair_result_item_label(row: SvlDashboardRepairRowResult) -> str:
        parts = [normalize_text(row.item_code), normalize_text(row.item_name)]
        compact = " | ".join(part for part in parts if part)
        return compact or normalize_text(row.row_key) or "-"

    @staticmethod
    def _repair_result_amount_label(amount: float) -> str:
        return f"{abs(float(amount or 0.0)):,.2f}"

    @staticmethod
    def _repair_summary_company_id(results: list[SvlDashboardRepairRowResult]) -> int:
        for row in results:
            if int(row.company_id or 0) > 0:
                return int(row.company_id or 0)
        return 0

    def _repair_summary_filename(self, *, kind: str, results: list[SvlDashboardRepairRowResult]) -> str:
        suffix = "html" if kind == "html" else "xlsx"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        company_id = self._repair_summary_company_id(results)
        company_part = str(company_id) if company_id > 0 else "all"
        return f"repair_summary_company_{company_part}_{timestamp}.{suffix}"

    def _remember_output_dir(self, path: Path) -> None:
        state_store = getattr(self, "_state_store", None)
        if state_store is None:
            return
        latest = state_store.load()
        latest.last_output_dir = str(path.parent)
        self._module_settings = latest
        state_store.save(latest)

    def _export_repair_summary(self, kind: str) -> None:
        widgets = getattr(self, "_last_repair_summary_widgets", {})
        results = list(widgets.get("results") or [])
        if not results:
            return
        database = normalize_text(widgets.get("database")) or normalize_text(
            self._latest_snapshot.database if getattr(self, "_latest_snapshot", None) is not None else ""
        )
        initial_dir = default_output_browse_dir(self.context.global_settings, getattr(self._module_settings, "last_output_dir", ""))
        filename = self._repair_summary_filename(kind=kind, results=results)
        suffix = "html" if kind == "html" else "xlsx"
        selected = filedialog.asksaveasfilename(
            title=f"Export {kind.upper()} Repair Summary",
            defaultextension=f".{suffix}",
            initialdir=initial_dir or None,
            initialfile=filename,
            filetypes=[
                ("HTML File", "*.html") if kind == "html" else ("Excel Workbook", "*.xlsx"),
            ],
        )
        if not selected:
            return
        if kind == "html":
            path = export_repair_summary_html(database=database, results=results, output_path=selected)
        else:
            path = export_repair_summary_excel(database=database, results=results, output_path=selected)
        self._remember_output_dir(path)
        self.status_var.set(f"Export summary selesai: {path}")

    def _pcb_case1_summary_filename(self, rows: list[dict[str, Any]]) -> str:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        company_ids = sorted({int(row.get("company_id") or 0) for row in rows if int(row.get("company_id") or 0) > 0})
        company_part = str(company_ids[0]) if len(company_ids) == 1 else "multi"
        return f"pcb_repair_summary_company_{company_part}_{timestamp}.xlsx"

    def _export_pcb_case1_summary_excel(self, rows: list[dict[str, Any]]) -> None:
        clean_rows = [row for row in rows if isinstance(row, dict)]
        if not clean_rows:
            return
        snapshot = getattr(self, "_latest_snapshot", None)
        database = normalize_text(snapshot.database if snapshot is not None else "")
        initial_dir = default_output_browse_dir(self.context.global_settings, getattr(self._module_settings, "last_output_dir", ""))
        filename = self._pcb_case1_summary_filename(clean_rows)
        selected = filedialog.asksaveasfilename(
            title="Export Excel PCB Repair Collection",
            defaultextension=".xlsx",
            initialdir=initial_dir or None,
            initialfile=filename,
            filetypes=[("Excel Workbook", "*.xlsx")],
        )
        if not selected:
            return
        path = export_pcb_case1_summary_excel(database=database, rows=clean_rows, output_path=selected)
        self._remember_output_dir(path)
        self.status_var.set(f"Export PCB Repair selesai: {path}")

    def _pcb_company_audit_filename(self) -> str:
        snapshot = getattr(self, "_latest_snapshot", None)
        company_id = int(getattr(snapshot, "company_id", 0) or 0)
        company_name = normalize_text(getattr(snapshot, "company_name", "")) or "company"
        company_part = re.sub(r"[^A-Za-z0-9]+", "_", company_name).strip("_")[:30] or str(company_id)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"pcb_audit_{company_part}_{timestamp}.xlsx"

    def _export_pcb_company_excel(self) -> None:
        snapshot = getattr(self, "_latest_snapshot", None)
        if snapshot is None:
            messagebox.showwarning(
                self._display_name,
                "Belum ada data PCB yang dimuat. Jalankan analisis terlebih dahulu.",
            )
            return
        cycles = list(getattr(snapshot, "purchase_cycles", None) or [])
        if not cycles:
            messagebox.showwarning(
                self._display_name,
                "Data PCB tidak memiliki cycle. Tidak ada yang bisa di-export.",
            )
            return
        initial_dir = default_output_browse_dir(
            self.context.global_settings,
            getattr(self._module_settings, "last_output_dir", ""),
        )
        filename = self._pcb_company_audit_filename()
        selected = filedialog.asksaveasfilename(
            title="Download Excel Audit Semua Cycle Pembelian (Company Level)",
            defaultextension=".xlsx",
            initialdir=initial_dir or None,
            initialfile=filename,
            filetypes=[("Excel Workbook", "*.xlsx")],
        )
        if not selected:
            return
        try:
            path = export_pcb_company_audit_excel(snapshot=snapshot, output_path=selected)
        except Exception as exc:
            messagebox.showerror(self._display_name, f"Export gagal:\n{exc}")
            return
        self._remember_output_dir(path)
        self.status_var.set(f"Export PCB Company Audit selesai: {path}")

    def _pcb_cycle_detail_filename(self, cycle: Any) -> str:
        snapshot = getattr(self, "_latest_snapshot", None)
        company_id = int(getattr(snapshot, "company_id", 0) or 0)
        company_part = str(company_id) if company_id > 0 else "all"
        cycle_name = normalize_text(getattr(cycle, "picking_name", "")) or "cycle"
        cycle_part = re.sub(r"[^A-Za-z0-9]+", "_", cycle_name).strip("_") or "cycle"
        cycle_part = cycle_part[:40]
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"pcb_cycle_detail_company_{company_part}_{cycle_part}_{timestamp}.xlsx"

    @staticmethod
    def _pcb_export_value_text(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, bool):
            return "Yes" if value else "No"
        if isinstance(value, int) and not isinstance(value, bool):
            return str(value)
        if isinstance(value, float):
            return f"{value:,.2f}"
        if isinstance(value, dict):
            parts = [
                f"{normalize_text(key)}={SvlFixJeDashboardPage._pcb_export_value_text(item_value)}"
                for key, item_value in value.items()
                if normalize_text(key) and SvlFixJeDashboardPage._pcb_export_value_text(item_value)
            ]
            return " | ".join(parts)
        if isinstance(value, (list, tuple, set)):
            parts = [
                SvlFixJeDashboardPage._pcb_export_value_text(item_value)
                for item_value in value
                if SvlFixJeDashboardPage._pcb_export_value_text(item_value)
            ]
            return " | ".join(parts)
        return normalize_text(value)

    def _build_pcb_cycle_detail_export_payload(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_pcb_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._build_pcb_cycle_detail_export_payload(self, *args, **kwargs)
    def _export_pcb_cycle_detail_excel(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_pcb_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._export_pcb_cycle_detail_excel(self, *args, **kwargs)
    def _format_repair_result_summary(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._format_repair_result_summary(self, *args, **kwargs)
    def _close_repair_summary_dialog(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._close_repair_summary_dialog(self, *args, **kwargs)
    def _show_repair_result_summary(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._show_repair_result_summary(self, *args, **kwargs)
    def _build_repair_seed_from_merged_row(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._build_repair_seed_from_merged_row(self, *args, **kwargs)
    def _snapshot_repair_candidates_by_row_key(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._snapshot_repair_candidates_by_row_key(self, *args, **kwargs)
    def _reconcile_repair_collection_with_snapshot(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._reconcile_repair_collection_with_snapshot(self, *args, **kwargs)
    def _snapshot_pcb_case1_candidates_by_row_key(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._snapshot_pcb_case1_candidates_by_row_key(self, *args, **kwargs)
    def _merge_pcb_case1_seed_into_row(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._merge_pcb_case1_seed_into_row(self, *args, **kwargs)
    def _reconcile_pcb_case1_collection_with_snapshot(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._reconcile_pcb_case1_collection_with_snapshot(self, *args, **kwargs)
    def _refresh_open_pcb_collection_dialog(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._refresh_open_pcb_collection_dialog(self, *args, **kwargs)
    def _clear_repair_collections_for_scope_change(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_repair_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._clear_repair_collections_for_scope_change(self, *args, **kwargs)
    def _placeholder_row(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_helpers as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._placeholder_row(self, *args, **kwargs)
    def _apply_snapshot(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_analysis_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._apply_snapshot(self, *args, **kwargs)
    def _finalize_snapshot_apply(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_analysis_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._finalize_snapshot_apply(self, *args, **kwargs)
    def _set_empty_detail(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_analysis_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._set_empty_detail(self, *args, **kwargs)
    def _render_analysis_bars(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_analysis_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._render_analysis_bars(self, *args, **kwargs)
    def _clear_snapshot(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_analysis_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._clear_snapshot(self, *args, **kwargs)
    def _filtered_items(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_analysis_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._filtered_items(self, *args, **kwargs)
    def _format_sidebar_summary(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_helpers as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._format_sidebar_summary(self, *args, **kwargs)
    def _reset_sidebar_scroll_position(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_analysis_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._reset_sidebar_scroll_position(self, *args, **kwargs)
    def _configure_sidebar_tree_style(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_analysis_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._configure_sidebar_tree_style(self, *args, **kwargs)
    def _sidebar_item_wrap_chars(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_helpers as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._sidebar_item_wrap_chars(self, *args, **kwargs)
    @staticmethod
    def _wrap_sidebar_text(*args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_helpers as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._wrap_sidebar_text(*args, **kwargs)
    @staticmethod
    def _truncate_sidebar_text(*args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_helpers as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._truncate_sidebar_text(*args, **kwargs)
    @staticmethod
    def _format_sidebar_group_text(*args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_helpers as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._format_sidebar_group_text(*args, **kwargs)
    def _format_sidebar_item_text(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_helpers as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._format_sidebar_item_text(self, *args, **kwargs)
    def _clear_sidebar_tree(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_analysis_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._clear_sidebar_tree(self, *args, **kwargs)
    def _rebuild_sidebar_tree(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_analysis_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._rebuild_sidebar_tree(self, *args, **kwargs)
    def _sync_sidebar_selection(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_analysis_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._sync_sidebar_selection(self, *args, **kwargs)
    def _render_item_cards_legacy(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_analysis_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._render_item_cards_legacy(self, *args, **kwargs)
    def _select_item(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_analysis_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._select_item(self, *args, **kwargs)
    def _build_selected_repair_seed_map(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_analysis_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._build_selected_repair_seed_map(self, *args, **kwargs)
    def _ensure_selected_repair_seed_map(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_analysis_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._ensure_selected_repair_seed_map(self, *args, **kwargs)
    def _merged_row_payload(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_analysis_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._merged_row_payload(self, *args, **kwargs)
    def _ensure_item_detail_loaded(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_analysis_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._ensure_item_detail_loaded(self, *args, **kwargs)
    def _detail_placeholder_rows(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_analysis_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._detail_placeholder_rows(self, *args, **kwargs)
    def _svl_tree_rows(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_analysis_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._svl_tree_rows(self, *args, **kwargs)
    def _journal_tree_rows(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_analysis_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._journal_tree_rows(self, *args, **kwargs)
    def _compare_po_rows(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_analysis_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._compare_po_rows(self, *args, **kwargs)
    def _compare_bill_rows(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_analysis_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._compare_bill_rows(self, *args, **kwargs)
    def _analysis_values_for_item(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_analysis_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._analysis_values_for_item(self, *args, **kwargs)
    def _merged_placeholder_rows(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_analysis_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._merged_placeholder_rows(self, *args, **kwargs)
    def _render_tab_if_needed(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_analysis_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._render_tab_if_needed(self, *args, **kwargs)
    def _render_selected_item_summary(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_analysis_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._render_selected_item_summary(self, *args, **kwargs)
    def _render_selected_item(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_analysis_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._render_selected_item(self, *args, **kwargs)
    def _fill_tree(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_analysis_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._fill_tree(self, *args, **kwargs)
    def _set_empty_company_summary(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_analysis_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._set_empty_company_summary(self, *args, **kwargs)
    def _apply_company_summary(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_analysis_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._apply_company_summary(self, *args, **kwargs)
    def _build_sidebar_item_card(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_analysis_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._build_sidebar_item_card(self, *args, **kwargs)
    def _build_sidebar_category_group(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_analysis_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._build_sidebar_category_group(self, *args, **kwargs)
    def _build_sidebar_valuation_group(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_analysis_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._build_sidebar_valuation_group(self, *args, **kwargs)
    def render_item_cards(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_analysis_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod.render_item_cards(self, *args, **kwargs)
    @staticmethod
    def _pcb_item_has_stj_evidence(*args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_pcb_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._pcb_item_has_stj_evidence(*args, **kwargs)
    @staticmethod
    def _pcb_item_stj_link_text(*args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_pcb_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._pcb_item_stj_link_text(*args, **kwargs)
    @staticmethod
    def _classify_pcb_cycle_case(*args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_pcb_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._classify_pcb_cycle_case(*args, **kwargs)
    @classmethod
    def _pcb_partial_group_key(cls, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_pcb_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._pcb_partial_group_key(cls, *args, **kwargs)
    @classmethod
    def _pcb_partial_group_label(cls, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_pcb_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._pcb_partial_group_label(cls, *args, **kwargs)
    def _render_purchase_cycle_sidebar(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_pcb_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._render_purchase_cycle_sidebar(self, *args, **kwargs)
    def _on_pcb_cycle_selected(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_pcb_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._on_pcb_cycle_selected(self, *args, **kwargs)
    def _pcb_collection_scope_text(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_pcb_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._pcb_collection_scope_text(self, *args, **kwargs)
    def _pcb_visible_case1_cycles(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_pcb_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._pcb_visible_case1_cycles(self, *args, **kwargs)
    def _pcb_visible_actionable_cycle_buckets(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_pcb_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._pcb_visible_actionable_cycle_buckets(self, *args, **kwargs)
    def _pcb_visible_actionable_cycles(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_pcb_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._pcb_visible_actionable_cycles(self, *args, **kwargs)
    def _pcb_cycle_non_actionable_reason(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_pcb_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._pcb_cycle_non_actionable_reason(self, *args, **kwargs)
    def _resolve_pcb_sidebar_cycle(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_pcb_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._resolve_pcb_sidebar_cycle(self, *args, **kwargs)
    @staticmethod
    def _pcb_case1_source_label(*args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_pcb_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._pcb_case1_source_label(*args, **kwargs)
    def _build_pcb_case1_repair_seed(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_pcb_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._build_pcb_case1_repair_seed(self, *args, **kwargs)
    def _build_pcb_case1_seeds_for_cycle(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_pcb_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._build_pcb_case1_seeds_for_cycle(self, *args, **kwargs)
    @staticmethod
    def _pcb_case2_guard_trigger_kind(*args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_pcb_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._pcb_case2_guard_trigger_kind(*args, **kwargs)
    @staticmethod
    def _pcb_case2_guard_required(*args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_pcb_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._pcb_case2_guard_required(*args, **kwargs)
    @staticmethod
    def _pcb_case2_guard_reason_preview(*args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_pcb_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._pcb_case2_guard_reason_preview(*args, **kwargs)
    def _map_pcb_case2_planned_lines(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_pcb_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._map_pcb_case2_planned_lines(self, *args, **kwargs)
    def _build_pcb_case2_repair_seed(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_pcb_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._build_pcb_case2_repair_seed(self, *args, **kwargs)
    def _build_pcb_case2_seeds_for_cycle(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_pcb_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._build_pcb_case2_seeds_for_cycle(self, *args, **kwargs)
    def _build_pcb_case_lainnya_manual_seeds_for_cycle(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_pcb_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._build_pcb_case_lainnya_manual_seeds_for_cycle(self, *args, **kwargs)
    def _build_pcb_seeds_for_cycle(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_pcb_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._build_pcb_seeds_for_cycle(self, *args, **kwargs)
    def _update_pcb_collection_button(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_pcb_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._update_pcb_collection_button(self, *args, **kwargs)
    def get_pcb_collection_ui_state(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_pcb_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod.get_pcb_collection_ui_state(self, *args, **kwargs)
    def open_pcb_repair_collection_dialog(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_pcb_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod.open_pcb_repair_collection_dialog(self, *args, **kwargs)
    def _is_pcb_cycle_in_collection(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_pcb_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._is_pcb_cycle_in_collection(self, *args, **kwargs)
    def _build_pcb_cycle_repair_seed(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_pcb_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._build_pcb_cycle_repair_seed(self, *args, **kwargs)
    def _confirm_pcb_case2_guard_rows(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_pcb_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._confirm_pcb_case2_guard_rows(self, *args, **kwargs)
    def _add_pcb_cycle_to_collection(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_pcb_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._add_pcb_cycle_to_collection(self, *args, **kwargs)
    def _remove_pcb_cycle_from_collection(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_pcb_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._remove_pcb_cycle_from_collection(self, *args, **kwargs)
    def collect_pcb_visible(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_pcb_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod.collect_pcb_visible(self, *args, **kwargs)
    def _collect_all_pcb_bermasalah(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_pcb_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._collect_all_pcb_bermasalah(self, *args, **kwargs)
    def _on_pcb_sidebar_right_click(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_pcb_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._on_pcb_sidebar_right_click(self, *args, **kwargs)
    @staticmethod
    def _pcb_adjustment_warning_text(*args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_pcb_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._pcb_adjustment_warning_text(*args, **kwargs)
    @staticmethod
    def _pcb_adjustment_match_text(*args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_pcb_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._pcb_adjustment_match_text(*args, **kwargs)
    @staticmethod
    def _pcb_adjustment_summary_text(*args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_pcb_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._pcb_adjustment_summary_text(*args, **kwargs)
    @staticmethod
    def _pcb_external_clearing_amount(*args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_pcb_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._pcb_external_clearing_amount(*args, **kwargs)
    def _render_purchase_cycle_detail(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_pcb_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._render_purchase_cycle_detail(self, *args, **kwargs)
    def _populate_pcb_acct_summary(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_pcb_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._populate_pcb_acct_summary(self, *args, **kwargs)
    @staticmethod
    def _pcb_process_order(*args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_pcb_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._pcb_process_order(*args, **kwargs)
    @classmethod
    def _pcb_process_label(cls, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_pcb_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._pcb_process_label(cls, *args, **kwargs)
    def _pcb_current_raw_sort_mode(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_pcb_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._pcb_current_raw_sort_mode(self, *args, **kwargs)
    def _pcb_sorted_raw_lines(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_pcb_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._pcb_sorted_raw_lines(self, *args, **kwargs)
    def _set_pcb_cycle_export_button_state(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_pcb_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._set_pcb_cycle_export_button_state(self, *args, **kwargs)
    def _on_pcb_raw_sort_changed(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_pcb_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._on_pcb_raw_sort_changed(self, *args, **kwargs)
    def _populate_pcb_raw_tree(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_pcb_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._populate_pcb_raw_tree(self, *args, **kwargs)
    def _pcb_raw_cell_at(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_pcb_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._pcb_raw_cell_at(self, *args, **kwargs)
    def _on_pcb_raw_cell_click(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_pcb_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._on_pcb_raw_cell_click(self, *args, **kwargs)
    def _pcb_raw_copy_cell(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_pcb_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._pcb_raw_copy_cell(self, *args, **kwargs)
    def _on_pcb_raw_right_click(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_pcb_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._on_pcb_raw_right_click(self, *args, **kwargs)
    def _pcb_raw_set_row_height(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_pcb_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._pcb_raw_set_row_height(self, *args, **kwargs)
    def _render_purchase_cycle_item_detail(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_pcb_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._render_purchase_cycle_item_detail(self, *args, **kwargs)
    def _open_pcb_coa_settings(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_pcb_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._open_pcb_coa_settings(self, *args, **kwargs)
    def _filter_sidebar_tree_in_place(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_analysis_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._filter_sidebar_tree_in_place(self, *args, **kwargs)
    def _on_database_selected(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_analysis_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._on_database_selected(self, *args, **kwargs)
    def _refresh_dataset_mode_widgets(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_analysis_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._refresh_dataset_mode_widgets(self, *args, **kwargs)
    def _on_dataset_mode_selected(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_analysis_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._on_dataset_mode_selected(self, *args, **kwargs)
    def _on_company_selected(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_analysis_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._on_company_selected(self, *args, **kwargs)
    def _on_logs_section_toggled(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_analysis_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._on_logs_section_toggled(self, *args, **kwargs)
    def load_companies(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_analysis_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod.load_companies(self, *args, **kwargs)
    def start_analysis(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_analysis_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod.start_analysis(self, *args, **kwargs)
    def _export_snapshot(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_analysis_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._export_snapshot(self, *args, **kwargs)
    def _open_pcb_case1_repair_dialog_v2(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_pcb_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._open_pcb_case1_repair_dialog_v2(self, *args, **kwargs)

    def _run_pcb_case1_repair_collection_v2(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_pcb_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._run_pcb_case1_repair_collection_v2(self, *args, **kwargs)

    def _open_pcb_repair_dialog(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_pcb_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._open_pcb_repair_dialog(self, *args, **kwargs)

    def _run_pcb_repair_collection(self, *args: Any, **kwargs: Any) -> Any:
        from smartscc_tools.modules import svl_fix_je_dashboard_pcb_ui as _helper_mod
        _helper_mod._sync_page_globals()
        return _helper_mod._run_pcb_repair_collection(self, *args, **kwargs)

    def shutdown(self) -> None:
        self.pause()
