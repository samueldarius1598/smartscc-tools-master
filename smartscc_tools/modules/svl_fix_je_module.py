"""Shell-native module for SVL Fix JE workflow."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import datetime
from functools import lru_cache
import os
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText
from typing import Any

from smartscc_tools.branding import FIXING_UNLINK_SVL_ICON
from smartscc_tools.core import theme as T
from smartscc_tools.services.odoo.profiles import (
    FOLLOW_GLOBAL_PROFILE_ID,
    build_module_database_options,
    build_option_maps,
    ensure_database_profile,
    normalize_module_database_profile_id,
    render_database_profile_label,
)
from smartscc_tools.core.global_config import GlobalPersistentState, GlobalSettings
from smartscc_tools.core.module_base import ModuleBase, ModuleContext
from smartscc_tools.widgets.collapsible_section import CollapsibleSection, build_compact_preview_text
from smartscc_tools.widgets.log_text import append_bounded_text_lines
from smartscc_tools.widgets.scrollable_frame import ScrollableFrame
from smartscc_tools.features.svl_fix_je.config import (
    DEFAULT_JOURNAL_CODE,
    DEFAULT_REF_PREFIX,
    DISPLAY_NAME,
    MODULE_ID,
    TEMPLATE_PATH,
    SvlFixJeSettings,
    default_input_browse_dir,
    default_output_browse_dir,
    normalize_dashboard_dataset_mode,
    normalize_database_profile_id,
    normalize_view_mode,
    normalize_worker_count,
    resolve_effective_database,
)


def build_runtime_settings(*args, **kwargs):
    from smartscc_tools.features.item_journal.config import build_runtime_settings as _build_runtime_settings

    return _build_runtime_settings(*args, **kwargs)


def fetch_odoo_config(*args, **kwargs):
    from smartscc_tools.services.odoo.gateway import fetch_odoo_config as _fetch_odoo_config

    return _fetch_odoo_config(*args, **kwargs)


def AsyncOdooJsonRpcClient(*args, **kwargs):
    from smartscc_tools.services.odoo.gateway import AsyncOdooJsonRpcClient as _AsyncOdooJsonRpcClient

    return _AsyncOdooJsonRpcClient(*args, **kwargs)


@lru_cache(maxsize=1)
def _load_svl_fix_je_dashboard_page_class():
    from smartscc_tools.modules.svl_fix_je_dashboard_page import SvlFixJeDashboardPage

    return SvlFixJeDashboardPage


@lru_cache(maxsize=1)
def _load_svl_fix_je_models_module():
    from smartscc_tools.features.svl_fix_je import models

    return models


@lru_cache(maxsize=1)
def _load_svl_fix_je_service_class():
    from smartscc_tools.features.svl_fix_je.service import SvlFixJeServiceAsync

    return SvlFixJeServiceAsync


def export_results_to_excel(*args, **kwargs):
    from smartscc_tools.features.svl_fix_je.export_results import export_results_to_excel as _export_results_to_excel

    return _export_results_to_excel(*args, **kwargs)


def build_runtime_connection(
    global_settings: GlobalSettings,
    logger,
    *,
    database_profile_id: str = FOLLOW_GLOBAL_PROFILE_ID,
) -> tuple[Any, Any]:
    settings, _ = build_runtime_settings(preset="safe-fast", set_args=[])
    config = fetch_odoo_config(settings=settings)
    effective_db = resolve_effective_database(
        database_profile_id=database_profile_id,
        global_settings=global_settings,
        default_database=config.database,
    )
    if effective_db:
        config.database = effective_db
        logger.info("DB efektif awal untuk %s: %s", DISPLAY_NAME, config.database)
    return settings, config


class _SvlFixJeStateStore:
    def __init__(
        self,
        global_settings: GlobalSettings,
        global_state_store: GlobalPersistentState | None = None,
    ) -> None:
        self._global_settings = global_settings
        self._global_state_store = global_state_store or GlobalPersistentState()

    def load(self) -> SvlFixJeSettings:
        payload = self._global_settings.module_settings.get(MODULE_ID)
        if not isinstance(payload, dict):
            return SvlFixJeSettings(
                last_output_dir=self._global_settings.default_output_dir.strip(),
            )
        database_profile_id = self._migrate_database_profile_id(payload)
        try:
            dashboard_company_id = int(payload.get("dashboard_company_id") or 0)
        except (TypeError, ValueError):
            dashboard_company_id = 0
        return SvlFixJeSettings(
            database_profile_id=database_profile_id,
            view_mode=normalize_view_mode(payload.get("view_mode")),
            last_excel_file=str(payload.get("last_excel_file") or ""),
            last_prefix=str(payload.get("last_prefix") or DEFAULT_REF_PREFIX),
            auto_post=bool(payload.get("auto_post", False)),
            max_workers=normalize_worker_count(payload.get("max_workers")),
            last_output_dir=(
                str(payload.get("last_output_dir") or "").strip()
                or self._global_settings.default_output_dir.strip()
            ),
            logs_section_open=bool(payload.get("logs_section_open", False)),
            dashboard_company_id=dashboard_company_id,
            dashboard_date_from=str(payload.get("dashboard_date_from") or ""),
            dashboard_date_to=str(payload.get("dashboard_date_to") or ""),
            dashboard_dataset_mode=normalize_dashboard_dataset_mode(payload.get("dashboard_dataset_mode")),
            dashboard_hide_inventory_accounts=bool(payload.get("dashboard_hide_inventory_accounts", False)),
            dashboard_include_inventory_accounts=self._load_dashboard_include_inventory_accounts(payload),
            dashboard_include_non_inventory_accounts=self._load_dashboard_include_non_inventory_accounts(payload),
            dashboard_logs_section_open=bool(payload.get("dashboard_logs_section_open", False)),
            dashboard_repair_last_target_mode=str(payload.get("dashboard_repair_last_target_mode") or ""),
            dashboard_repair_last_posting_mode=str(payload.get("dashboard_repair_last_posting_mode") or ""),
            dashboard_repair_last_target_account_role=str(payload.get("dashboard_repair_last_target_account_role") or ""),
            dashboard_repair_last_resolve_account_code=str(payload.get("dashboard_repair_last_resolve_account_code") or ""),
            pcb_case1_last_date=str(payload.get("pcb_case1_last_date") or ""),
        )

    def save(self, settings: SvlFixJeSettings) -> None:
        payload = asdict(settings)
        payload["database_profile_id"] = normalize_database_profile_id(payload.get("database_profile_id"))
        payload.pop("database_choice", None)
        payload["max_workers"] = normalize_worker_count(payload.get("max_workers"))
        payload.pop("dashboard_hide_inventory_accounts", None)
        self._global_settings.module_settings[MODULE_ID] = payload
        self._global_state_store.save(self._global_settings)

    def _migrate_database_profile_id(self, payload: dict[str, Any]) -> str:
        current_value = payload.get("database_profile_id")
        if normalize_module_database_profile_id(current_value) != FOLLOW_GLOBAL_PROFILE_ID or str(current_value or "").strip():
            return normalize_database_profile_id(current_value)

        legacy_choice = str(payload.get("database_choice") or "").strip()
        if not legacy_choice or legacy_choice == "follow_global":
            return FOLLOW_GLOBAL_PROFILE_ID
        if legacy_choice == "hwgroup_erp":
            return ensure_database_profile(
                self._global_settings.database_profiles,
                "hwgroup_erp",
                alias="Live ERP",
                note="Database Live",
            )
        if legacy_choice == "hwgroup_erp_22022026":
            return ensure_database_profile(
                self._global_settings.database_profiles,
                "hwgroup_erp_22022026",
                alias="Dummy ERP",
                note="Database Dummy",
            )
        return ensure_database_profile(self._global_settings.database_profiles, legacy_choice, alias=legacy_choice)

    @staticmethod
    def _load_dashboard_include_inventory_accounts(payload: dict[str, Any]) -> bool:
        if "dashboard_include_inventory_accounts" in payload:
            return bool(payload.get("dashboard_include_inventory_accounts", True))
        if "dashboard_hide_inventory_accounts" in payload:
            return not bool(payload.get("dashboard_hide_inventory_accounts", False))
        return True

    @staticmethod
    def _load_dashboard_include_non_inventory_accounts(payload: dict[str, Any]) -> bool:
        if "dashboard_include_non_inventory_accounts" in payload:
            return bool(payload.get("dashboard_include_non_inventory_accounts", True))
        return True


class SvlFixJeModule(ModuleBase):
    @property
    def module_id(self) -> str:
        return MODULE_ID

    @property
    def display_name(self) -> str:
        return DISPLAY_NAME

    @property
    def description(self) -> str:
        return "Validasi dan buat Journal Entry dari orphan SVL berdasarkan SVL ID"

    @property
    def icon_path(self) -> str | None:
        return str(FIXING_UNLINK_SVL_ICON) if FIXING_UNLINK_SVL_ICON.exists() else None

    def create_ui(self, parent: tk.Frame, context: ModuleContext) -> tk.Frame:
        self._panel = _SvlFixJeShell(parent, context)
        return parent

    def get_shell(self) -> object | None:
        return getattr(self, "_panel", None)

    def build_gui_debug_snapshot(self, **kwargs: Any) -> dict[str, Any]:
        shell = getattr(self, "_panel", None)
        build_snapshot = getattr(shell, "build_gui_debug_snapshot", None)
        return {
            "module_id": self.module_id,
            "display_name": self.display_name,
            "shell_state": build_snapshot(**kwargs) if callable(build_snapshot) else None,
        }

    def on_activate(self) -> None:
        if hasattr(self, "_panel"):
            self._panel.resume()

    def on_deactivate(self) -> None:
        if hasattr(self, "_panel"):
            self._panel.pause()

    def on_shutdown(self) -> None:
        if hasattr(self, "_panel"):
            self._panel.shutdown()


class _SvlFixJeShell:
    VIEW_OPTIONS = {
        "Upload": "upload",
        "Dashboard Control": "dashboard",
    }

    def __init__(self, parent: tk.Frame, context: ModuleContext) -> None:
        self.parent = parent
        self.context = context
        self._state_store = _SvlFixJeStateStore(context.global_settings)
        self._module_settings = self._state_store.load()
        self._db_label_by_profile_id: dict[str, str] = {}
        self._db_profile_id_by_label: dict[str, str] = {}
        self._shared_config_sync_active = False
        self.upload_page: _SvlFixJePanel | None = None
        self.dashboard_page: Any | None = None

        self.view_choice_var = tk.StringVar(value=self._label_for_mode(self._module_settings.view_mode))
        self.repair_collection_summary_var = tk.StringVar(value="0 record | Total 0.00")
        self.database_choice_var = tk.StringVar(value="")
        self.prefix_var = tk.StringVar(value=self._module_settings.last_prefix or DEFAULT_REF_PREFIX)
        self.max_workers_var = tk.StringVar(value=str(self._module_settings.max_workers))
        self.auto_post_var = tk.BooleanVar(value=bool(self._module_settings.auto_post))
        self._build_ui()

    def _build_card(self, parent: tk.Widget, *, title: str, pady: tuple[int, int] = (0, 8)) -> tk.Frame:
        frame = tk.Frame(
            parent,
            bg=T.BG_CARD,
            bd=1,
            relief="solid",
            highlightbackground=T.BORDER_LIGHT,
            highlightthickness=1,
        )
        frame.pack(fill="x", padx=16, pady=pady)
        inner = tk.Frame(frame, bg=T.BG_CARD, padx=16, pady=12)
        inner.pack(fill="x")
        tk.Label(
            inner,
            text=title,
            bg=T.BG_CARD,
            fg=T.TEXT_ON_LIGHT,
            font=T.font(T.FONT_BODY_SIZE, bold=True),
        ).pack(anchor="w", pady=(0, 10))
        body = tk.Frame(inner, bg=T.BG_CARD)
        body.pack(fill="x")
        return body

    def _build_ui(self) -> None:
        self.parent.configure(bg=T.BG_MAIN)

        selector_frame = tk.Frame(
            self.parent,
            bg=T.BG_CARD,
            bd=1,
            relief="solid",
            highlightbackground=T.BORDER_LIGHT,
            highlightthickness=1,
        )
        selector_frame.pack(fill="x", padx=16, pady=(16, 8))
        selector_inner = tk.Frame(selector_frame, bg=T.BG_CARD, padx=16, pady=12)
        selector_inner.pack(fill="x")
        tk.Label(
            selector_inner,
            text="Page:",
            bg=T.BG_CARD,
            fg=T.TEXT_ON_LIGHT,
            font=T.font(T.FONT_BODY_SIZE, bold=True),
        ).pack(side="left")
        self.view_combo = ttk.Combobox(
            selector_inner,
            state="readonly",
            values=list(self.VIEW_OPTIONS.keys()),
            textvariable=self.view_choice_var,
            width=24,
        )
        self.view_combo.pack(side="left", padx=(12, 0))
        self.view_combo.bind("<<ComboboxSelected>>", self._on_view_selected)
        self.btn_repair_collection = tk.Button(
            selector_inner,
            text="Repair Collection",
            bg=T.BRAND_PRIMARY,
            fg=T.TEXT_ON_DARK,
            font=T.font(T.FONT_BODY_SIZE, bold=True),
            activebackground=T.BRAND_PRIMARY_DARK,
            activeforeground=T.TEXT_ON_DARK,
            relief="flat",
            padx=14,
            pady=6,
            state="disabled",
            command=self._open_repair_collection,
        )
        self.btn_repair_collection.pack(side="left", padx=(16, 0))
        self.btn_clear_repair_collection = tk.Button(
            selector_inner,
            text="Clear Collection",
            bg=T.BG_INPUT,
            fg=T.TEXT_ON_LIGHT,
            font=T.font(T.FONT_SMALL_SIZE, bold=True),
            relief="flat",
            padx=12,
            pady=6,
            state="disabled",
            command=self._clear_repair_collection,
        )
        self.btn_clear_repair_collection.pack(side="left", padx=(8, 0))

        # PCB Collection button (only active in Balance Cycle Pembelian mode)
        self.btn_pcb_collection = tk.Button(
            selector_inner,
            text="PCB Repair Collection",
            bg=T.BRAND_PRIMARY,
            fg=T.TEXT_ON_DARK,
            font=T.font(T.FONT_BODY_SIZE, bold=True),
            activebackground=T.BRAND_PRIMARY_DARK,
            activeforeground=T.TEXT_ON_DARK,
            relief="flat",
            padx=14,
            pady=6,
            state="disabled",
            command=self._open_pcb_collection,
        )
        self.btn_pcb_collection.pack(side="left", padx=(16, 0))

        self._bulk_action_menu = tk.Menu(selector_inner, tearoff=0)
        collect_all_menu = tk.Menu(self._bulk_action_menu, tearoff=0)
        collect_all_menu.add_command(
            label="Seluruh Item SVL vs Balance Sheet",
            command=lambda: self._on_bulk_collect_all_linked_empty_je(filter_automated_only=False),
        )
        collect_all_menu.add_command(
            label="Hanya Kategori Automated (real_time)",
            command=lambda: self._on_bulk_collect_all_linked_empty_je(filter_automated_only=True),
        )
        self._bulk_action_menu.add_cascade(label="Collect All: Linked JE Header Kosong", menu=collect_all_menu)
        collect_all_no_je_menu = tk.Menu(self._bulk_action_menu, tearoff=0)
        collect_all_no_je_menu.add_command(
            label="Seluruh Item SVL vs Balance Sheet",
            command=lambda: self._on_bulk_collect_all_svl_without_je(filter_automated_only=False),
        )
        collect_all_no_je_menu.add_command(
            label="Hanya Kategori Automated (real_time)",
            command=lambda: self._on_bulk_collect_all_svl_without_je(filter_automated_only=True),
        )
        self._bulk_action_menu.add_cascade(label="Collect All: SVL tanpa JE", menu=collect_all_no_je_menu)
        self._bulk_action_menu_pcb = tk.Menu(selector_inner, tearoff=0)
        self._bulk_action_menu_pcb_collect_visible = tk.Menu(self._bulk_action_menu_pcb, tearoff=0)
        self._bulk_action_menu_pcb_collect_visible.add_command(
            label="Semua Case Bermasalah",
            command=lambda: self._on_bulk_collect_all_pcb_visible(),
        )
        _PCB_COLLECT_VISIBLE_CASES = [
            ("case1", "Case 1 - STJ Bill Miss Match (Clearing - Suspend)"),
            ("case2", "Case 2 - STJ Bill Price Diff (Suspend - Suspend)"),
            ("case3", "Case 3 - STJ Bill Hit Expenses (Clearing - Expenses)"),
            ("case4", "Case 4 - STJ Bill Hit Expenses (Suspend - Expenses)"),
            ("case5", "Case 5 - Pemulihan SVL (Inventory - Suspense)"),
            ("case6", "Case 6 - Pemulihan SVL (Inventory - Bill Expense)"),
            ("edge_partial_bill", "Edge - Partial Bill"),
            ("edge_return_no_credit_memo", "Edge - Return No Credit Memo"),
            ("edge_stj_corrupt", "Edge - STJ Corrupt"),
            ("case_lainnya", "Case Lainnya"),
        ]
        for _case_key, _case_label in _PCB_COLLECT_VISIBLE_CASES:
            self._bulk_action_menu_pcb_collect_visible.add_command(
                label=f"{_case_label} saja",
                command=lambda ck=_case_key: self._on_bulk_collect_all_pcb_visible(case_filter=ck),
            )
        self._bulk_action_menu_pcb.add_cascade(
            label="Collect PCB Repair (Visible)",
            menu=self._bulk_action_menu_pcb_collect_visible,
        )
        self.btn_bulk_action = tk.Button(
            selector_inner,
            text="Bulk Action ▾",
            bg=T.BG_INPUT,
            fg=T.TEXT_ON_LIGHT,
            font=T.font(T.FONT_SMALL_SIZE, bold=True),
            relief="flat",
            padx=12,
            pady=6,
            state="disabled",
            command=self._show_bulk_action_menu,
        )
        self.btn_bulk_action.pack(side="left", padx=(8, 0))

        self.repair_collection_summary_label = tk.Label(
            selector_inner,
            textvariable=self.repair_collection_summary_var,
            bg=T.BG_CARD,
            fg=T.TEXT_MUTED,
            font=T.font(T.FONT_SMALL_SIZE),
            justify="left",
            anchor="w",
        )
        self.repair_collection_summary_label.pack(side="left", fill="x", expand=True, padx=(12, 0))

        config_card = self._build_card(self.parent, title="Konfigurasi")
        config_grid = tk.Frame(config_card, bg=T.BG_CARD)
        config_grid.pack(fill="x")
        config_grid.grid_columnconfigure(1, weight=1)
        config_grid.grid_columnconfigure(3, weight=1)

        tk.Label(config_grid, text="Database:", bg=T.BG_CARD, fg=T.TEXT_ON_LIGHT, font=T.font()).grid(
            row=0, column=0, sticky="w", pady=4
        )
        self.database_combo = ttk.Combobox(
            config_grid,
            state="readonly",
            textvariable=self.database_choice_var,
            width=32,
        )
        self.database_combo.grid(row=0, column=1, sticky="ew", padx=(8, 16), pady=4)
        self.database_combo.bind("<<ComboboxSelected>>", self._on_shared_database_selected)

        tk.Label(config_grid, text="Reference Prefix:", bg=T.BG_CARD, fg=T.TEXT_ON_LIGHT, font=T.font()).grid(
            row=0, column=2, sticky="w", pady=4
        )
        self.prefix_entry = tk.Entry(
            config_grid,
            textvariable=self.prefix_var,
            font=T.font(T.FONT_BODY_SIZE),
            bg=T.BG_INPUT,
            relief="solid",
            bd=1,
        )
        self.prefix_entry.grid(row=0, column=3, sticky="ew", pady=4)
        self.prefix_entry.bind("<FocusOut>", self._on_shared_prefix_commit)
        self.prefix_entry.bind("<Return>", self._on_shared_prefix_commit)

        tk.Label(config_grid, text="Max Workers:", bg=T.BG_CARD, fg=T.TEXT_ON_LIGHT, font=T.font()).grid(
            row=1, column=0, sticky="w", pady=4
        )
        self.max_workers_spin = tk.Spinbox(
            config_grid,
            from_=1,
            to=32,
            textvariable=self.max_workers_var,
            width=8,
            command=self._on_shared_max_workers_commit,
        )
        self.max_workers_spin.grid(row=1, column=1, sticky="w", padx=(8, 16), pady=4)
        self.max_workers_spin.bind("<FocusOut>", self._on_shared_max_workers_commit)
        self.max_workers_spin.bind("<Return>", self._on_shared_max_workers_commit)

        self.auto_post_check = tk.Checkbutton(
            config_grid,
            text="Auto Post Transaction Journal Upload Excel",
            variable=self.auto_post_var,
            bg=T.BG_CARD,
            fg=T.TEXT_ON_LIGHT,
            activebackground=T.BG_CARD,
            activeforeground=T.TEXT_ON_LIGHT,
            selectcolor=T.BG_CARD,
            font=T.font(T.FONT_BODY_SIZE, bold=True),
            command=self._on_shared_auto_post_toggled,
        )
        self.auto_post_check.grid(row=1, column=2, columnspan=2, sticky="w", pady=4)

        self.content = tk.Frame(self.parent, bg=T.BG_MAIN)
        self.content.pack(fill="both", expand=True)

        self.upload_host = tk.Frame(self.content, bg=T.BG_MAIN)
        self.dashboard_host = tk.Frame(self.content, bg=T.BG_MAIN)

        self._load_shared_configuration_values()
        self._show_current_page()
        self._refresh_repair_collection_controls()

    def _ensure_upload_page(self) -> _SvlFixJePanel:
        if self.upload_page is None:
            self.upload_page = _SvlFixJePanel(self.upload_host, self.context, state_store=self._state_store)
        return self.upload_page

    def _ensure_dashboard_page(self) -> Any:
        if self.dashboard_page is None:
            dashboard_page_cls = _load_svl_fix_je_dashboard_page_class()
            self.dashboard_page = dashboard_page_cls(
                self.dashboard_host,
                self.context,
                state_store=self._state_store,
                runtime_builder=build_runtime_connection,
                display_name=DISPLAY_NAME,
            )
            self.dashboard_page.set_repair_collection_changed_callback(self._refresh_repair_collection_controls)
        return self.dashboard_page

    def ensure_dashboard_mode(self) -> Any:
        if self._current_mode() != "dashboard":
            self.view_choice_var.set(self._label_for_mode("dashboard"))
            self._save_view_mode()
            self._show_current_page()
        return self._ensure_dashboard_page()

    def set_database_profile_for_debug(self, profile_id: str) -> None:
        normalized_profile_id = normalize_database_profile_id(profile_id)
        label = self._db_label_by_profile_id.get(normalized_profile_id, "")
        if not label:
            raise RuntimeError(f"Database profile tidak ditemukan di GUI: {normalized_profile_id}")
        self.database_choice_var.set(label)
        self._save_shared_configuration()

    def build_gui_debug_snapshot(self, **kwargs: Any) -> dict[str, Any]:
        page_snapshot = None
        if self.dashboard_page is not None:
            build_snapshot = getattr(self.dashboard_page, "build_gui_debug_snapshot", None)
            if callable(build_snapshot):
                page_snapshot = build_snapshot(**kwargs)
        return {
            "current_mode": self._current_mode(),
            "selected_page_label": str(self.view_choice_var.get() or ""),
            "shared_database_label": str(self.database_choice_var.get() or ""),
            "shared_reference_prefix": str(self.prefix_var.get() or ""),
            "shared_max_workers": str(self.max_workers_var.get() or ""),
            "shared_auto_post": bool(self.auto_post_var.get()),
            "repair_collection_summary": str(self.repair_collection_summary_var.get() or ""),
            "dashboard_page": page_snapshot,
        }

    def pause(self) -> None:
        for page in (self.upload_page, self.dashboard_page):
            pause = getattr(page, "pause", None)
            if callable(pause):
                pause()

    def resume(self) -> None:
        self.refresh_database_options()

    def _label_for_mode(self, mode: str) -> str:
        clean_mode = normalize_view_mode(mode)
        for label, value in self.VIEW_OPTIONS.items():
            if value == clean_mode:
                return label
        return "Upload"

    def _current_mode(self) -> str:
        return self.VIEW_OPTIONS.get(str(self.view_choice_var.get() or "").strip(), "upload")

    def _save_view_mode(self) -> None:
        latest = self._state_store.load()
        latest.view_mode = self._current_mode()
        self._module_settings = latest
        self._state_store.save(latest)

    def _on_view_selected(self, _event: tk.Event | None = None) -> None:
        self._save_view_mode()
        self._show_current_page()

    def _show_bulk_action_menu(self) -> None:
        btn = self.btn_bulk_action
        menu = self._bulk_action_menu
        pcb_state = (
            self.dashboard_page.get_pcb_collection_ui_state()
            if self.dashboard_page is not None
            else {"is_pcb_mode": False}
        )
        if bool(pcb_state.get("is_pcb_mode")):
            self._sync_pcb_bulk_action_menu_state(pcb_state)
            menu = self._bulk_action_menu_pcb
        menu.post(btn.winfo_rootx(), btn.winfo_rooty() + btn.winfo_height())

    @staticmethod
    def _pcb_collect_case_key_from_menu_label(label: str) -> str:
        clean = str(label or "").strip().lower()
        if clean.startswith("case 1"):
            return "case1"
        if clean.startswith("case 2"):
            return "case2"
        if clean.startswith("case 3"):
            return "case3"
        if clean.startswith("case 4"):
            return "case4"
        if clean.startswith("case 5"):
            return "case5"
        if clean.startswith("case 6"):
            return "case6"
        if "partial bill" in clean:
            return "edge_partial_bill"
        if "return no credit memo" in clean:
            return "edge_return_no_credit_memo"
        if "stj corrupt" in clean:
            return "edge_stj_corrupt"
        if "case lainnya" in clean:
            return "case_lainnya"
        return ""

    def _sync_pcb_bulk_action_menu_state(self, pcb_state: dict[str, object]) -> None:
        collect_menu = getattr(self, "_bulk_action_menu_pcb_collect_visible", None)
        parent_menu = getattr(self, "_bulk_action_menu_pcb", None)
        visible_cases = dict(pcb_state.get("visible_actionable_cases") or {})
        has_any = bool(pcb_state.get("has_visible_actionable_cycles"))
        if hasattr(parent_menu, "entryconfigure"):
            parent_menu.entryconfigure(0, state="normal" if has_any else "disabled")
        if not hasattr(collect_menu, "entryconfigure"):
            return
        collect_menu.entryconfigure(0, state="normal" if has_any else "disabled")
        entry_labels: list[tuple[int, str]] = []
        if hasattr(collect_menu, "entrycget") and hasattr(collect_menu, "index"):
            try:
                last_index = collect_menu.index("end")
            except Exception:
                last_index = None
            if isinstance(last_index, int):
                for idx in range(1, last_index + 1):
                    try:
                        label = str(collect_menu.entrycget(idx, "label") or "")
                    except Exception:
                        label = ""
                    entry_labels.append((idx, label))
        if not entry_labels:
            for idx, entry in enumerate(list(getattr(collect_menu, "entries", None) or [])):
                if idx <= 0:
                    continue
                entry_labels.append((idx, str((entry or {}).get("label") or "")))
        for idx, label in entry_labels:
            case_key = self._pcb_collect_case_key_from_menu_label(label)
            if not case_key:
                continue
            collect_menu.entryconfigure(idx, state="normal" if bool(visible_cases.get(case_key)) else "disabled")

    def _selected_database_choice(self) -> str:
        label = str(self.database_choice_var.get() or "").strip()
        return self._db_profile_id_by_label.get(label, normalize_database_profile_id(label))

    def _load_shared_configuration_values(self) -> None:
        self._module_settings = self._state_store.load()
        options = build_module_database_options(self.context.global_settings.database_profiles)
        self._db_label_by_profile_id, self._db_profile_id_by_label = build_option_maps(options)
        labels = [label for _option_id, label in options]
        selected_profile_id = normalize_database_profile_id(self._module_settings.database_profile_id)
        selected_label = self._db_label_by_profile_id.get(selected_profile_id)
        if selected_label is None:
            selected_profile_id = FOLLOW_GLOBAL_PROFILE_ID
            selected_label = self._db_label_by_profile_id.get(selected_profile_id, "")
        self._shared_config_sync_active = True
        try:
            self.database_combo.configure(values=labels)
            self.database_choice_var.set(selected_label)
            self.prefix_var.set(self._module_settings.last_prefix or DEFAULT_REF_PREFIX)
            self.max_workers_var.set(str(self._module_settings.max_workers))
            self.auto_post_var.set(bool(self._module_settings.auto_post))
        finally:
            self._shared_config_sync_active = False

    def _broadcast_shared_configuration_change(self, *, database_changed: bool, prefix_changed: bool) -> None:
        if self.upload_page is not None:
            self.upload_page.refresh_database_options()
        if self.dashboard_page is not None:
            on_shared_configuration_updated = getattr(self.dashboard_page, "on_shared_configuration_updated", None)
            if callable(on_shared_configuration_updated):
                on_shared_configuration_updated(database_changed=database_changed, prefix_changed=prefix_changed)
            else:
                self.dashboard_page.refresh_database_options()
        self._refresh_repair_collection_controls()

    def _save_shared_configuration(self) -> None:
        if self._shared_config_sync_active:
            return
        previous = self._module_settings
        latest = self._state_store.load()
        latest.database_profile_id = self._selected_database_choice()
        latest.last_prefix = str(self.prefix_var.get() or DEFAULT_REF_PREFIX).strip() or DEFAULT_REF_PREFIX
        latest.max_workers = normalize_worker_count(self.max_workers_var.get())
        latest.auto_post = bool(self.auto_post_var.get())
        self._module_settings = latest
        self._state_store.save(latest)
        self._load_shared_configuration_values()
        self._broadcast_shared_configuration_change(
            database_changed=normalize_database_profile_id(previous.database_profile_id) != normalize_database_profile_id(latest.database_profile_id),
            prefix_changed=(str(previous.last_prefix or DEFAULT_REF_PREFIX).strip() or DEFAULT_REF_PREFIX)
            != (str(latest.last_prefix or DEFAULT_REF_PREFIX).strip() or DEFAULT_REF_PREFIX),
        )

    def _on_shared_database_selected(self, _event: tk.Event | None = None) -> None:
        self._save_shared_configuration()

    def _on_shared_prefix_commit(self, _event: tk.Event | None = None) -> str | None:
        self._save_shared_configuration()
        return None

    def _on_shared_max_workers_commit(self, _event: tk.Event | None = None) -> str | None:
        self._save_shared_configuration()
        return None

    def _on_shared_auto_post_toggled(self) -> None:
        self._save_shared_configuration()

    def _on_bulk_collect_all_linked_empty_je(self, *, filter_automated_only: bool) -> None:
        self._ensure_dashboard_page().collect_all_linked_empty_je(filter_automated_only=filter_automated_only)

    def _on_bulk_collect_all_svl_without_je(self, *, filter_automated_only: bool) -> None:
        self._ensure_dashboard_page().collect_all_svl_without_je(filter_automated_only=filter_automated_only)

    def _on_bulk_collect_all_pcb_visible(self, *, case_filter: str | None = None) -> None:
        self._ensure_dashboard_page().collect_pcb_visible(case_filter=case_filter)

    def _on_bulk_collect_all_pcb_case1_visible(self) -> None:
        self._on_bulk_collect_all_pcb_visible()

    def _open_repair_collection(self) -> None:
        self._ensure_dashboard_page().open_repair_collection_dialog()

    def _open_pcb_collection(self) -> None:
        self._ensure_dashboard_page().open_pcb_repair_collection_dialog()

    def _clear_repair_collection(self) -> None:
        if self.dashboard_page is not None:
            self.dashboard_page.clear_repair_collection()

    def _refresh_repair_collection_controls(self) -> None:
        state = (
            self.dashboard_page.get_repair_collection_ui_state()
            if self.dashboard_page is not None
            else {
                "summary_text": "0 record | Total 0.00",
                "can_open": False,
                "can_clear": False,
                "has_rows": False,
                "scope_matches_current": True,
            }
        )
        pcb_state = (
            self.dashboard_page.get_pcb_collection_ui_state()
            if self.dashboard_page is not None
            else {"can_open": False}
        )
        self.repair_collection_summary_var.set(str(state.get("summary_text") or "0 record | Total 0.00"))
        is_dashboard = self._current_mode() == "dashboard"
        self.btn_repair_collection.configure(state="normal" if is_dashboard and bool(state.get("can_open")) else "disabled")
        self.btn_clear_repair_collection.configure(state="normal" if is_dashboard and bool(state.get("can_clear")) else "disabled")
        self.btn_pcb_collection.configure(state="normal" if is_dashboard and bool(pcb_state.get("can_open")) else "disabled")
        has_snapshot_fn = getattr(self.dashboard_page, "has_latest_snapshot", None)
        has_snapshot = bool(has_snapshot_fn()) if callable(has_snapshot_fn) else False
        self.btn_bulk_action.configure(state="normal" if is_dashboard and has_snapshot else "disabled")
        summary_fg = T.TEXT_MUTED
        if bool(state.get("has_rows")) and not bool(state.get("scope_matches_current")):
            summary_fg = T.STATUS_WARNING
        self.repair_collection_summary_label.configure(fg=summary_fg)

    def _show_current_page(self) -> None:
        self.pause()
        self.upload_host.pack_forget()
        self.dashboard_host.pack_forget()
        if self._current_mode() == "dashboard":
            dashboard_page = self._ensure_dashboard_page()
            self.dashboard_host.pack(fill="both", expand=True)
            resume = getattr(dashboard_page, "resume", None)
            if callable(resume):
                resume()
            dashboard_page.on_page_activated()
        else:
            upload_page = self._ensure_upload_page()
            self.upload_host.pack(fill="both", expand=True)
            upload_page.resume()
            upload_page.refresh_database_options()
        self._refresh_repair_collection_controls()

    def refresh_database_options(self) -> None:
        self._load_shared_configuration_values()
        self._show_current_page()

    def shutdown(self) -> None:
        self.pause()
        if self.upload_page is not None:
            self.upload_page.shutdown()
        if self.dashboard_page is not None:
            self.dashboard_page.shutdown()


class _SvlFixJePanel:
    def __init__(self, parent: tk.Frame, context: ModuleContext, *, state_store: _SvlFixJeStateStore | None = None) -> None:
        self.parent = parent
        self.context = context
        self.root = parent.winfo_toplevel()
        self.logger = context.logger
        self.log_queue: queue.Queue[str] = queue.Queue()
        self.ui_queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self.worker: threading.Thread | None = None
        self._poll_after_id: str | None = None
        self._poll_active = False
        self._busy = False
        self._active_service: Any | None = None
        self._state_store = state_store or _SvlFixJeStateStore(context.global_settings)
        self._module_settings = self._state_store.load()
        self._latest_summary: Any | None = None
        self._result_index: dict[int, Any] = {}

        self._db_label_by_profile_id: dict[str, str] = {}
        self._db_profile_id_by_label: dict[str, str] = {}
        self.database_choice_var = tk.StringVar(value="")
        self.prefix_var = tk.StringVar(value=self._module_settings.last_prefix or DEFAULT_REF_PREFIX)
        self.max_workers_var = tk.StringVar(value=str(self._module_settings.max_workers))
        self.auto_post_var = tk.BooleanVar(value=bool(self._module_settings.auto_post))

        self.latest_log_line_var = tk.StringVar(value="Belum ada log.")
        self.summary_var = tk.StringVar(value="Belum ada hasil proses.")
        self.phase_var = tk.StringVar(value="Idle")
        self.percent_var = tk.StringVar(value="0%")
        self.count_var = tk.StringVar(value="0 / 0")
        self.eta_var = tk.StringVar(value="-")
        self.status_var = tk.StringVar(value="Siap.")
        self.progress_value = tk.DoubleVar(value=0.0)

        self._build_ui()
        self._refresh_database_options()
        self.resume()

    def _build_card(self, parent: tk.Widget, *, title: str, pady: tuple[int, int] = (0, 8)) -> tk.Frame:
        frame = tk.Frame(
            parent,
            bg=T.BG_CARD,
            bd=1,
            relief="solid",
            highlightbackground=T.BORDER_LIGHT,
            highlightthickness=1,
        )
        frame.pack(fill="x", padx=16, pady=pady)
        inner = tk.Frame(frame, bg=T.BG_CARD, padx=16, pady=12)
        inner.pack(fill="x")
        tk.Label(
            inner,
            text=title,
            bg=T.BG_CARD,
            fg=T.TEXT_ON_LIGHT,
            font=T.font(T.FONT_BODY_SIZE, bold=True),
        ).pack(anchor="w", pady=(0, 10))
        return inner

    def _build_ui(self) -> None:
        self._scrollable = ScrollableFrame(self.parent, bg=T.BG_MAIN)
        self._scrollable.pack(fill="both", expand=True)
        main = self._scrollable.interior

        input_card = self._build_card(main, title="Input", pady=(16, 8))
        input_row = tk.Frame(input_card, bg=T.BG_CARD)
        input_row.pack(fill="x")
        tk.Label(
            input_row,
            text="Excel File:",
            bg=T.BG_CARD,
            fg=T.TEXT_ON_LIGHT,
            font=T.font(T.FONT_BODY_SIZE, bold=True),
        ).pack(side="left")
        self.excel_path_var = tk.StringVar(value=self._module_settings.last_excel_file)
        self.excel_entry = tk.Entry(
            input_row,
            textvariable=self.excel_path_var,
            font=T.font(T.FONT_BODY_SIZE),
            bg=T.BG_INPUT,
            relief="solid",
            bd=1,
        )
        self.excel_entry.pack(side="left", fill="x", expand=True, padx=(12, 8))
        self.btn_browse = tk.Button(
            input_row,
            text="Browse...",
            bg=T.BRAND_PRIMARY,
            fg=T.TEXT_ON_DARK,
            font=T.font(T.FONT_BODY_SIZE, bold=True),
            activebackground=T.BRAND_PRIMARY_DARK,
            activeforeground=T.TEXT_ON_DARK,
            relief="flat",
            padx=16,
            pady=4,
            command=self._browse_excel,
        )
        self.btn_browse.pack(side="left")
        self.btn_open_template = tk.Button(
            input_row,
            text="Open Template",
            bg=T.BRAND_SECONDARY,
            fg=T.TEXT_ON_DARK,
            font=T.font(T.FONT_BODY_SIZE, bold=True),
            activebackground=T.BRAND_ACCENT,
            activeforeground=T.TEXT_ON_DARK,
            relief="flat",
            padx=16,
            pady=4,
            command=self._open_template,
        )
        self.btn_open_template.pack(side="left", padx=(8, 0))

        action_card = self._build_card(main, title="Action & Progress")
        button_row = tk.Frame(action_card, bg=T.BG_CARD)
        button_row.pack(fill="x")
        self.btn_validate = tk.Button(
            button_row,
            text="Validate (Dry Run)",
            bg=T.BRAND_SECONDARY,
            fg=T.TEXT_ON_DARK,
            font=T.font(T.FONT_BODY_SIZE, bold=True),
            activebackground=T.BRAND_ACCENT,
            activeforeground=T.TEXT_ON_DARK,
            relief="flat",
            padx=18,
            pady=6,
            command=lambda: self._start_run("validate"),
        )
        self.btn_validate.pack(side="left")
        self.btn_execute = tk.Button(
            button_row,
            text="Execute",
            bg=T.BRAND_PRIMARY,
            fg=T.TEXT_ON_DARK,
            font=T.font(T.FONT_BODY_SIZE, bold=True),
            activebackground=T.BRAND_PRIMARY_DARK,
            activeforeground=T.TEXT_ON_DARK,
            relief="flat",
            padx=18,
            pady=6,
            command=lambda: self._start_run("execute"),
        )
        self.btn_execute.pack(side="left", padx=(8, 0))
        self.btn_stop = tk.Button(
            button_row,
            text="Stop",
            bg=T.STATUS_ERROR,
            fg=T.TEXT_ON_DARK,
            font=T.font(T.FONT_BODY_SIZE, bold=True),
            activebackground=T.STATUS_ERROR,
            activeforeground=T.TEXT_ON_DARK,
            relief="flat",
            padx=18,
            pady=6,
            command=self._stop_run,
            state="disabled",
        )
        self.btn_stop.pack(side="left", padx=(8, 0))

        self.progress_row = tk.Frame(action_card, bg=T.BG_CARD)
        self.progress_row.pack(fill="x", pady=(12, 4))
        self.progress_bar = ttk.Progressbar(self.progress_row, variable=self.progress_value, maximum=100)
        self.progress_bar.pack(side="left", fill="x", expand=True)
        tk.Label(
            self.progress_row,
            textvariable=self.count_var,
            bg=T.BG_CARD,
            fg=T.TEXT_ON_LIGHT,
            font=T.font(),
        ).pack(side="left", padx=(12, 8))
        tk.Label(
            self.progress_row,
            textvariable=self.percent_var,
            bg=T.BG_CARD,
            fg=T.TEXT_ON_LIGHT,
            font=T.font(T.FONT_BODY_SIZE, bold=True),
        ).pack(side="left")

        meta_row = tk.Frame(action_card, bg=T.BG_CARD)
        meta_row.pack(fill="x", pady=(4, 0))
        tk.Label(meta_row, text="Phase:", bg=T.BG_CARD, fg=T.TEXT_MUTED, font=T.font(T.FONT_SMALL_SIZE)).pack(
            side="left"
        )
        tk.Label(meta_row, textvariable=self.phase_var, bg=T.BG_CARD, fg=T.TEXT_ON_LIGHT, font=T.font()).pack(
            side="left", padx=(6, 12)
        )
        tk.Label(meta_row, text="ETA:", bg=T.BG_CARD, fg=T.TEXT_MUTED, font=T.font(T.FONT_SMALL_SIZE)).pack(
            side="left"
        )
        tk.Label(meta_row, textvariable=self.eta_var, bg=T.BG_CARD, fg=T.TEXT_ON_LIGHT, font=T.font()).pack(
            side="left", padx=(6, 0)
        )

        results_card = self._build_card(main, title="Results")
        tk.Label(
            results_card,
            textvariable=self.summary_var,
            bg=T.BG_CARD,
            fg=T.TEXT_ON_LIGHT,
            font=T.font(T.FONT_BODY_SIZE),
            justify="left",
            wraplength=960,
        ).pack(anchor="w", fill="x")
        result_action_row = tk.Frame(results_card, bg=T.BG_CARD)
        result_action_row.pack(fill="x", pady=(12, 0))
        self.btn_export = tk.Button(
            result_action_row,
            text="Export Results...",
            bg=T.BRAND_PRIMARY,
            fg=T.TEXT_ON_DARK,
            font=T.font(T.FONT_BODY_SIZE, bold=True),
            activebackground=T.BRAND_PRIMARY_DARK,
            activeforeground=T.TEXT_ON_DARK,
            relief="flat",
            padx=18,
            pady=6,
            state="disabled",
            command=self._export_results,
        )
        self.btn_export.pack(side="left")
        tk.Label(
            result_action_row,
            textvariable=self.status_var,
            bg=T.BG_CARD,
            fg=T.TEXT_MUTED,
            font=T.font(T.FONT_SMALL_SIZE),
        ).pack(side="left", padx=(12, 0))

        self.log_section = CollapsibleSection(
            main,
            key="logs",
            title="Logs",
            expanded=bool(self._module_settings.logs_section_open),
            on_toggle=self._on_logs_section_toggled,
            body_fill="x",
            body_expand=False,
            compact_fill="x",
            compact_expand=False,
            show_compact_when_open=False,
        )
        self.log_section.pack(fill="x", padx=16, pady=(0, 16))
        tk.Label(
            self.log_section.compact_body,
            textvariable=self.latest_log_line_var,
            bg=T.BG_CARD,
            fg=T.TEXT_MUTED,
            font=T.font(T.FONT_SMALL_SIZE),
            anchor="w",
            justify="left",
        ).pack(fill="x")

        inner_log = tk.Frame(self.log_section.body, bg=T.BG_CARD)
        inner_log.pack(fill="x")
        tk.Label(
            inner_log,
            text="Log:",
            bg=T.BG_CARD,
            fg=T.TEXT_ON_LIGHT,
            font=T.font(T.FONT_SMALL_SIZE, bold=True),
        ).pack(anchor="w")
        self.log_text = ScrolledText(
            inner_log,
            height=max(6, T.MIN_LOG_VISIBLE_LINES),
            state="disabled",
            wrap="word",
            font=T.font(T.FONT_SMALL_SIZE),
        )
        self.log_text.pack(fill="x", pady=(4, 0))
        self.log_section.refresh_layout()
        self._refresh_busy_state()

    def _selected_database_choice(self) -> str:
        label = str(self.database_choice_var.get() or "").strip()
        return self._db_profile_id_by_label.get(label, normalize_database_profile_id(label))

    def _resolve_database(self, default_database: str) -> str:
        return resolve_effective_database(
            database_profile_id=self._module_settings.database_profile_id,
            global_settings=self.context.global_settings,
            default_database=default_database,
        )

    def _save_module_settings(self) -> None:
        latest = self._state_store.load()
        latest.last_excel_file = str(self.excel_path_var.get() or "").strip()
        latest.logs_section_open = bool(self.log_section.is_open)
        self._module_settings = latest
        self._state_store.save(latest)

    def refresh_database_options(self) -> None:
        self._refresh_database_options()

    def _refresh_database_options(self) -> None:
        self._module_settings = self._state_store.load()
        options = build_module_database_options(self.context.global_settings.database_profiles)
        self._db_label_by_profile_id, self._db_profile_id_by_label = build_option_maps(options)
        selected_profile_id = normalize_database_profile_id(self._module_settings.database_profile_id)
        selected_label = self._db_label_by_profile_id.get(selected_profile_id)
        if selected_label is None:
            selected_profile_id = FOLLOW_GLOBAL_PROFILE_ID
            self._module_settings.database_profile_id = selected_profile_id
            selected_label = self._db_label_by_profile_id.get(selected_profile_id, "")
        self.database_choice_var.set(selected_label)
        self.prefix_var.set(self._module_settings.last_prefix or DEFAULT_REF_PREFIX)
        self.max_workers_var.set(str(self._module_settings.max_workers))
        self.auto_post_var.set(bool(self._module_settings.auto_post))

    def _append_log_batch(self, lines: list[str]) -> None:
        if not lines:
            return
        self.latest_log_line_var.set(
            build_compact_preview_text(lines[-1], empty_text="Belum ada log.", max_chars=140)
        )
        append_bounded_text_lines(self.log_text, lines)

    def _append_log(self, text: str) -> None:
        self.latest_log_line_var.set(
            build_compact_preview_text(text, empty_text="Belum ada log.", max_chars=140)
        )
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _on_logs_section_toggled(self, _key: str, is_open: bool) -> None:
        latest = self._state_store.load()
        latest.logs_section_open = bool(is_open)
        self._module_settings = latest
        self._state_store.save(latest)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._refresh_busy_state()

    def _refresh_busy_state(self) -> None:
        action_state = "disabled" if self._busy else "normal"
        self.btn_browse.configure(state=action_state)
        self.btn_open_template.configure(state=action_state)
        self.btn_validate.configure(state=action_state)
        self.btn_execute.configure(state=action_state)
        self.btn_stop.configure(state="normal" if self._busy else "disabled")
        if not self._busy:
            self.status_var.set("Siap.")

    def _format_eta(self, eta_seconds: int | None) -> str:
        if eta_seconds is None:
            return "-"
        minutes, seconds = divmod(max(0, int(eta_seconds)), 60)
        hours, minutes = divmod(minutes, 60)
        if hours > 0:
            return f"{hours}j {minutes}m {seconds}d"
        if minutes > 0:
            return f"{minutes}m {seconds}d"
        return f"{seconds}d"

    def _apply_progress(self, snapshot) -> None:
        self.progress_value.set(round(float(snapshot.progress or 0.0) * 100, 2))
        self.percent_var.set(f"{int(round(float(snapshot.progress or 0.0) * 100))}%")
        self.count_var.set(f"{int(snapshot.processed)} / {int(snapshot.total)}")
        self.phase_var.set(snapshot.current or snapshot.phase or "-")
        self.eta_var.set(self._format_eta(snapshot.eta_seconds))

    def _compose_summary_text(self, summary) -> str:
        latest_status = "Dibatalkan" if summary.stopped else "Selesai"
        success_count = summary.created_count if summary.mode == "execute" else len(summary.validated_rows)
        if summary.mode == "execute" and summary.auto_post:
            success_count = summary.posted_count
        return (
            f"{latest_status}. Mode: {summary.mode.upper()} | DB: {summary.database} | "
            f"Total {summary.total_rows} baris | Valid {len(summary.validated_rows)} | "
            f"Success {success_count} | Error Validasi {summary.validation_errors} | "
            f"Error Eksekusi {summary.execution_errors} | Canceled {summary.canceled_count} | "
            f"Total Value {summary.total_value:,.2f}"
        )

    def _apply_summary(self, summary) -> None:
        self._latest_summary = summary
        self.btn_export.configure(state="normal" if summary and summary.results else "disabled")
        if summary is None:
            self.summary_var.set("Belum ada hasil proses.")
            return
        self.summary_var.set(self._compose_summary_text(summary))

    def _handle_ui_event(self, event: dict[str, Any]) -> None:
        event_type = str(event.get("type") or "")
        if event_type == "busy":
            self._set_busy(bool(event.get("value", False)))
        elif event_type == "progress":
            self._apply_progress(event["snapshot"])
        elif event_type == "result":
            result = event["result"]
            self._result_index[int(result.row_number)] = result
        elif event_type == "state":
            status = str(event.get("status") or "")
            message = str(event.get("message") or "")
            summary = event.get("summary")
            database = str(event.get("database") or "")
            self.context.status_callback(message or status or DISPLAY_NAME)
            self.status_var.set(message or status or DISPLAY_NAME)
            if status in {"completed", "canceled"} and summary is not None:
                self._apply_summary(summary)
                self._set_busy(False)
                self._active_service = None
            elif status == "error":
                self._set_busy(False)
                self._active_service = None
                if message:
                    messagebox.showerror(DISPLAY_NAME, message)
            elif status == "connected":
                self.status_var.set(f"Connected: {database}")
        elif event_type == "error":
            message = str(event.get("message") or "")
            self._set_busy(False)
            self._active_service = None
            self.status_var.set(message or "Terjadi error.")
            if message:
                messagebox.showerror(DISPLAY_NAME, message)

    def _poll_queues(self) -> None:
        if not getattr(self, "_poll_active", True):
            return
        log_lines: list[str] = []
        while True:
            try:
                line = self.log_queue.get_nowait()
            except queue.Empty:
                break
            log_lines.append(line)
        append_log_override = self.__dict__.get("_append_log") if hasattr(self, "__dict__") else None
        if callable(append_log_override):
            for line in log_lines:
                append_log_override(line)
        else:
            self._append_log_batch(log_lines)
        while True:
            try:
                event = self.ui_queue.get_nowait()
            except queue.Empty:
                break
            self._handle_ui_event(event)
        self._poll_after_id = self.root.after(33 if getattr(self, "_busy", False) else 250, self._poll_queues)

    def resume(self) -> None:
        if self._poll_active:
            return
        self._poll_active = True
        self._poll_queues()

    def pause(self) -> None:
        self._poll_active = False
        if self._poll_after_id is not None:
            try:
                self.root.after_cancel(self._poll_after_id)
            except Exception:  # noqa: BLE001
                pass
            self._poll_after_id = None

    def _browse_excel(self) -> None:
        initial_dir = default_input_browse_dir(self.context.global_settings, self.excel_path_var.get())
        selected = filedialog.askopenfilename(
            title="Pilih file Excel SVL Fix",
            initialdir=initial_dir or None,
            filetypes=[("Excel Workbook", "*.xlsx;*.xlsm"), ("All Files", "*.*")],
        )
        if not selected:
            return
        self.excel_path_var.set(selected)
        self._save_module_settings()

    def _open_template(self) -> None:
        if not TEMPLATE_PATH.exists():
            messagebox.showerror(DISPLAY_NAME, f"Template tidak ditemukan: {TEMPLATE_PATH}")
            return
        if hasattr(os, "startfile"):
            os.startfile(str(TEMPLATE_PATH))  # type: ignore[attr-defined]
            return
        messagebox.showinfo(DISPLAY_NAME, f"Template tersedia di:\n{TEMPLATE_PATH}")

    def _build_run_request(self, database: str):
        request_cls = _load_svl_fix_je_models_module().SvlFixJeRunRequest
        return request_cls(
            excel_path=str(self.excel_path_var.get() or "").strip(),
            database=database,
            ref_prefix=str(self.prefix_var.get() or DEFAULT_REF_PREFIX).strip() or DEFAULT_REF_PREFIX,
            auto_post=bool(self.auto_post_var.get()),
            max_workers=normalize_worker_count(self.max_workers_var.get()),
            default_journal_code=DEFAULT_JOURNAL_CODE,
        )

    def _confirm_execute(self) -> bool:
        return messagebox.askyesno(
            "Konfirmasi Execute",
            "Execute akan membuat Journal Entry di Odoo. Lanjutkan?",
            icon="warning",
        )

    def _start_run(self, mode: str) -> None:
        excel_path = str(self.excel_path_var.get() or "").strip()
        if not excel_path:
            messagebox.showwarning("Input", "Pilih file Excel terlebih dahulu.")
            return
        if self.worker is not None and self.worker.is_alive():
            return
        if mode == "execute" and not self._confirm_execute():
            return

        self._save_module_settings()
        self._result_index = {}
        self._apply_summary(None)
        self.status_var.set("Memulai proses...")
        self.ui_queue.put({"type": "busy", "value": True})

        def worker() -> None:
            try:
                settings, config = build_runtime_connection(
                    self.context.global_settings,
                    self.logger,
                    database_profile_id=self._selected_database_choice(),
                )
                effective_db = self._resolve_database(config.database)
                config.database = effective_db
                request = self._build_run_request(effective_db)
                self.log_queue.put(f"Connecting to {config.base_url} [{effective_db}]...")
                service_cls = _load_svl_fix_je_service_class()

                async def _run():
                    async with AsyncOdooJsonRpcClient(config=config, settings=settings, logger=self.logger) as rpc:
                        service = service_cls(
                            rpc=rpc,
                            logger=self.logger,
                            on_log=self.log_queue.put,
                            on_progress=lambda snapshot: self.ui_queue.put(
                                {"type": "progress", "snapshot": snapshot}
                            ),
                            on_result=lambda result: self.ui_queue.put({"type": "result", "result": result}),
                            on_state=lambda status, message, summary, database: self.ui_queue.put(
                                {
                                    "type": "state",
                                    "status": status,
                                    "message": message,
                                    "summary": summary,
                                    "database": database,
                                }
                            ),
                        )
                        self._active_service = service
                        if mode == "validate":
                            return await service.validate(request)
                        return await service.execute(request)

                asyncio.run(_run())
            except Exception as exc:  # noqa: BLE001
                self.log_queue.put(f"ERROR: {exc}")
                self.ui_queue.put({"type": "error", "message": str(exc)})
            finally:
                self._active_service = None
                self.ui_queue.put({"type": "busy", "value": False})

        self.worker = threading.Thread(target=worker, daemon=True, name="svl-fix-je-worker")
        self.worker.start()

    def _stop_run(self) -> None:
        if self._active_service is None:
            return
        self._active_service.request_stop()
        self.status_var.set("Menghentikan proses...")

    def _suggest_export_filename(self) -> str:
        source_name = "svl_fix"
        if self._latest_summary and self._latest_summary.excel_path:
            source_name = Path(self._latest_summary.excel_path).stem
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"{source_name}_svl_fix_results_{timestamp}.xlsx"

    def _export_results(self) -> None:
        if self._latest_summary is None:
            return
        initial_dir = default_output_browse_dir(
            self.context.global_settings,
            self._module_settings.last_output_dir,
        )
        selected = filedialog.asksaveasfilename(
            title="Simpan hasil SVL Fix",
            defaultextension=".xlsx",
            initialdir=initial_dir or None,
            initialfile=self._suggest_export_filename(),
            filetypes=[("Excel Workbook", "*.xlsx")],
        )
        if not selected:
            return
        output_path = export_results_to_excel(self._latest_summary, selected)
        self._module_settings.last_output_dir = str(Path(output_path).parent)
        self._state_store.save(self._module_settings)
        self.status_var.set(f"Results exported: {output_path}")
        self.context.status_callback(f"Results {DISPLAY_NAME} berhasil diexport.")

    def shutdown(self) -> None:
        self.pause()
        if self._active_service is not None:
            self._active_service.request_stop()
