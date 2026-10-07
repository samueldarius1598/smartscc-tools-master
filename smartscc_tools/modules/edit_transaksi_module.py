"""Module 2: Edit Transaksi Item Movement."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from functools import lru_cache
import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText
from typing import Any

from smartscc_tools.branding import EDIT_STOCK_MOVEMENT_ICON
from smartscc_tools.core import theme as T
from smartscc_tools.services.odoo.profiles import (
    FOLLOW_GLOBAL_PROFILE_ID,
    build_module_database_options,
    build_option_maps,
    normalize_database_profile_id,
    resolve_database_selection,
)
from smartscc_tools.core.global_config import GlobalPersistentState, GlobalSettings
from smartscc_tools.core.module_base import ModuleBase, ModuleContext
from smartscc_tools.widgets.collapsible_section import CollapsibleSection, build_compact_preview_text
from smartscc_tools.widgets.log_text import append_bounded_text_lines
from smartscc_tools.widgets.scrollable_frame import ScrollableFrame


def build_runtime_settings(*args, **kwargs):
    from smartscc_tools.features.item_journal.config import build_runtime_settings as _build_runtime_settings

    return _build_runtime_settings(*args, **kwargs)


def fetch_odoo_config(*args, **kwargs):
    from smartscc_tools.services.odoo.gateway import fetch_odoo_config as _fetch_odoo_config

    return _fetch_odoo_config(*args, **kwargs)


def AsyncOdooJsonRpcClient(*args, **kwargs):
    from smartscc_tools.services.odoo.gateway import AsyncOdooJsonRpcClient as _AsyncOdooJsonRpcClient

    return _AsyncOdooJsonRpcClient(*args, **kwargs)


def extract_many2one_id(*args, **kwargs):
    from smartscc_tools.services.odoo.gateway import extract_many2one_id as _extract_many2one_id

    return _extract_many2one_id(*args, **kwargs)


@lru_cache(maxsize=1)
def _load_datetime_picker_class():
    from smartscc_tools.widgets.datetime_picker import DateTimePickerField

    return DateTimePickerField


@lru_cache(maxsize=1)
def _load_edit_transaksi_settings_class():
    from smartscc_tools.features.edit_transaksi.config import EditTransaksiSettings

    return EditTransaksiSettings


@lru_cache(maxsize=1)
def _load_transaction_edit_request_class():
    from smartscc_tools.features.edit_transaksi.models import TransactionEditRequest

    return TransactionEditRequest


@lru_cache(maxsize=1)
def _load_picking_reader_service_class():
    from smartscc_tools.features.edit_transaksi.services.picking_reader import PickingReaderServiceAsync

    return PickingReaderServiceAsync


@lru_cache(maxsize=1)
def _load_transaction_date_editor_service_class():
    from smartscc_tools.features.edit_transaksi.services.date_editor import TransactionDateEditorServiceAsync

    return TransactionDateEditorServiceAsync


def build_runtime_connection(
    global_settings: GlobalSettings,
    logger,
    *,
    database_profile_id: str = FOLLOW_GLOBAL_PROFILE_ID,
) -> tuple[Any, Any]:
    settings, _ = build_runtime_settings(preset="safe-fast", set_args=[])
    config = fetch_odoo_config(settings=settings)
    effective_database = resolve_database_selection(
        profiles=global_settings.database_profiles,
        module_profile_id=database_profile_id,
        default_profile_id=global_settings.default_database_profile_id,
        gas_default_database=config.database,
    )
    if effective_database:
        config.database = effective_database
        logger.info("DB efektif untuk Edit Transaksi: %s", config.database)
    return settings, config


class _EditTransaksiStateStore:
    def __init__(
        self,
        global_settings: GlobalSettings,
        global_state_store: GlobalPersistentState | None = None,
    ) -> None:
        self._global_settings = global_settings
        self._global_state_store = global_state_store or GlobalPersistentState()

    def load(self):
        settings_cls = _load_edit_transaksi_settings_class()
        payload = self._global_settings.module_settings.get("edit_transaksi")
        if not isinstance(payload, dict):
            return settings_cls()
        return settings_cls(
            http_timeout_read=int(payload.get("http_timeout_read") or 30),
            max_retry=int(payload.get("max_retry") or 2),
            database_profile_id=normalize_database_profile_id(payload.get("database_profile_id")),
            logs_section_open=bool(payload.get("logs_section_open", False)),
        )

    def save(self, settings) -> None:
        self._global_settings.module_settings["edit_transaksi"] = asdict(settings)
        self._global_state_store.save(self._global_settings)


@dataclass
class _DateRowWidgets:
    row: tk.Frame
    toggle: tk.Checkbutton
    picker: Any


class EditTransaksiModule(ModuleBase):

    @property
    def module_id(self) -> str:
        return "edit_transaksi"

    @property
    def display_name(self) -> str:
        return "Edit Transaksi Item Movement - Odoo"

    @property
    def description(self) -> str:
        return "Edit tanggal dan qty stock picking, SVL, dan jurnal di Odoo"

    @property
    def icon_path(self) -> str | None:
        return str(EDIT_STOCK_MOVEMENT_ICON) if EDIT_STOCK_MOVEMENT_ICON.exists() else None

    def create_ui(self, parent: tk.Frame, context: ModuleContext) -> tk.Frame:
        self._panel = _EditTransaksiPanel(parent, context)
        return parent

    def on_activate(self) -> None:
        if hasattr(self, "_panel"):
            self._panel.resume()
            self._panel.refresh_database_options()

    def on_deactivate(self) -> None:
        if hasattr(self, "_panel"):
            self._panel.pause()

    def on_shutdown(self) -> None:
        if hasattr(self, "_panel"):
            self._panel.shutdown()


class _EditTransaksiPanel:
    """Internal panel UI for Edit Transaksi module."""

    def __init__(self, parent: tk.Frame, context: ModuleContext) -> None:
        self.parent = parent
        self.context = context
        self.root = parent.winfo_toplevel()
        self.logger = context.logger
        self.log_queue: queue.Queue[str] = queue.Queue()
        self.ui_queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self.worker: threading.Thread | None = None
        self._busy = False
        self._picking_detail: Any = None
        self._selected_line: PickingLineItem | None = None
        self._tree_line_map: dict[str, PickingLineItem] = {}
        self._poll_after_id: str | None = None
        self._poll_active = False
        self._state_store = _EditTransaksiStateStore(context.global_settings)
        self._module_settings = self._state_store.load()
        self._db_label_by_profile_id: dict[str, str] = {}
        self._db_profile_id_by_label: dict[str, str] = {}
        self.latest_log_line_var = tk.StringVar(value="Belum ada log.")
        self.log_section: CollapsibleSection | None = None

        self._build_ui()
        self.resume()

    def _build_ui(self) -> None:
        self._scrollable = ScrollableFrame(self.parent, bg=T.BG_MAIN)
        self._scrollable.pack(fill="both", expand=True)
        main = self._scrollable.interior

        database_frame = tk.Frame(
            main,
            bg=T.BG_CARD,
            bd=1,
            relief="solid",
            highlightbackground=T.BORDER_LIGHT,
            highlightthickness=1,
        )
        database_frame.pack(fill="x", padx=16, pady=(16, 8))
        inner_database = tk.Frame(database_frame, bg=T.BG_CARD, padx=16, pady=12)
        inner_database.pack(fill="x")
        tk.Label(
            inner_database,
            text="Database",
            bg=T.BG_CARD,
            fg=T.TEXT_ON_LIGHT,
            font=T.font(T.FONT_BODY_SIZE, bold=True),
        ).grid(row=0, column=0, sticky="w")
        self.database_choice_var = tk.StringVar(value="")
        self.database_combo = ttk.Combobox(
            inner_database,
            state="readonly",
            textvariable=self.database_choice_var,
            width=42,
        )
        self.database_combo.grid(row=0, column=1, sticky="w", padx=(12, 0))
        tk.Label(
            inner_database,
            text="Pilih database aktif untuk operasi Edit Transaksi.",
            bg=T.BG_CARD,
            fg=T.TEXT_MUTED,
            font=T.font(T.FONT_SMALL_SIZE),
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))
        self._refresh_database_options()

        search_frame = tk.Frame(
            main,
            bg=T.BG_CARD,
            bd=1,
            relief="solid",
            highlightbackground=T.BORDER_LIGHT,
            highlightthickness=1,
        )
        search_frame.pack(fill="x", padx=16, pady=(16, 8))

        inner_search = tk.Frame(search_frame, bg=T.BG_CARD, padx=16, pady=12)
        inner_search.pack(fill="x")

        tk.Label(
            inner_search,
            text="No. Item Movement / Picking:",
            bg=T.BG_CARD,
            fg=T.TEXT_ON_LIGHT,
            font=T.font(T.FONT_BODY_SIZE, bold=True),
        ).pack(side="left")

        self.picking_var = tk.StringVar(value="")
        self.picking_entry = tk.Entry(
            inner_search,
            textvariable=self.picking_var,
            font=T.font(T.FONT_BODY_SIZE),
            width=30,
            bg=T.BG_INPUT,
            relief="solid",
            bd=1,
        )
        self.picking_entry.pack(side="left", padx=(12, 8))
        self.picking_entry.bind("<Return>", lambda _event: self._fetch_picking())

        self.btn_fetch = tk.Button(
            inner_search,
            text="Cari",
            bg=T.BRAND_PRIMARY,
            fg=T.TEXT_ON_DARK,
            font=T.font(T.FONT_BODY_SIZE, bold=True),
            activebackground=T.BRAND_PRIMARY_DARK,
            activeforeground=T.TEXT_ON_DARK,
            relief="flat",
            padx=16,
            pady=4,
            command=self._fetch_picking,
        )
        self.btn_fetch.pack(side="left")

        self.status_label = tk.Label(
            inner_search,
            text="",
            bg=T.BG_CARD,
            fg=T.TEXT_MUTED,
            font=T.font(T.FONT_SMALL_SIZE),
        )
        self.status_label.pack(side="left", padx=(16, 0))

        detail_frame = tk.Frame(
            main,
            bg=T.BG_CARD,
            bd=1,
            relief="solid",
            highlightbackground=T.BORDER_LIGHT,
            highlightthickness=1,
        )
        detail_frame.pack(fill="x", padx=16, pady=8)

        inner_detail = tk.Frame(detail_frame, bg=T.BG_CARD, padx=16, pady=12)
        inner_detail.pack(fill="x")

        info_frame = tk.Frame(inner_detail, bg=T.BG_CARD)
        info_frame.pack(fill="x", pady=(0, 8))

        for col, label_text in enumerate(["Picking:", "State:", "Origin:", "Warehouse:"]):
            tk.Label(
                info_frame,
                text=label_text,
                bg=T.BG_CARD,
                fg=T.TEXT_MUTED,
                font=T.font(T.FONT_SMALL_SIZE),
            ).grid(row=0, column=col * 2, sticky="w", padx=(0, 4))

        self.info_picking_var = tk.StringVar(value="-")
        self.info_state_var = tk.StringVar(value="-")
        self.info_origin_var = tk.StringVar(value="-")
        self.info_warehouse_var = tk.StringVar(value="-")
        for col, var in enumerate(
            [self.info_picking_var, self.info_state_var, self.info_origin_var, self.info_warehouse_var]
        ):
            tk.Label(
                info_frame,
                textvariable=var,
                bg=T.BG_CARD,
                fg=T.TEXT_ON_LIGHT,
                font=T.font(T.FONT_SMALL_SIZE, bold=True),
            ).grid(row=0, column=col * 2 + 1, sticky="w", padx=(0, 20))

        tk.Label(
            inner_detail,
            text="Items dalam Picking (per stock.move.line):",
            bg=T.BG_CARD,
            fg=T.TEXT_ON_LIGHT,
            font=T.font(T.FONT_BODY_SIZE, bold=True),
        ).pack(anchor="w")

        tree_frame = tk.Frame(inner_detail, bg=T.BG_CARD)
        tree_frame.pack(fill="x", pady=(4, 8))
        columns = ("product", "line_qty", "move_qty", "svl_qty", "uom", "date", "state")
        self.tree = ttk.Treeview(tree_frame, columns=columns, show="headings", height=8)
        self.tree.heading("product", text="Product")
        self.tree.heading("line_qty", text="Line Qty")
        self.tree.heading("move_qty", text="Move Qty")
        self.tree.heading("svl_qty", text="SVL Qty")
        self.tree.heading("uom", text="UoM")
        self.tree.heading("date", text="Date")
        self.tree.heading("state", text="State")
        self.tree.column("product", width=260)
        self.tree.column("line_qty", width=90, anchor="e")
        self.tree.column("move_qty", width=90, anchor="e")
        self.tree.column("svl_qty", width=90, anchor="e")
        self.tree.column("uom", width=90)
        self.tree.column("date", width=160)
        self.tree.column("state", width=90)
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        edit_frame = tk.Frame(
            main,
            bg=T.BG_CARD,
            bd=1,
            relief="solid",
            highlightbackground=T.BORDER_LIGHT,
            highlightthickness=1,
        )
        edit_frame.pack(fill="x", padx=16, pady=(0, 8))

        inner_edit = tk.Frame(edit_frame, bg=T.BG_CARD, padx=16, pady=12)
        inner_edit.pack(fill="x")
        inner_edit.grid_columnconfigure(0, weight=1)
        inner_edit.grid_columnconfigure(2, weight=1)

        date_panel = tk.Frame(inner_edit, bg=T.BG_CARD)
        date_panel.grid(row=0, column=0, sticky="nsew")

        tk.Label(
            date_panel,
            text="Edit Tanggal",
            bg=T.BG_CARD,
            fg=T.TEXT_ON_LIGHT,
            font=T.font(T.FONT_BODY_SIZE, bold=True),
        ).pack(anchor="w")
        tk.Label(
            date_panel,
            text="Pilih field yang ingin diubah. Date picker dipakai untuk semua input.",
            bg=T.BG_CARD,
            fg=T.TEXT_MUTED,
            font=T.font(T.FONT_SMALL_SIZE),
        ).pack(anchor="w", pady=(2, 10))

        self.sync_dates_var = tk.BooleanVar(value=False)
        self._sync_prev_date_states = {"stock": False, "svl": False, "journal": False}
        sync_row = tk.Frame(date_panel, bg=T.BG_CARD)
        sync_row.pack(anchor="w", fill="x", pady=(0, 8))
        self.sync_dates_check = tk.Checkbutton(
            sync_row,
            text="Gunakan satu tanggal untuk semua",
            variable=self.sync_dates_var,
            bg=T.BG_CARD,
            fg=T.TEXT_ON_LIGHT,
            activebackground=T.BG_CARD,
            activeforeground=T.TEXT_ON_LIGHT,
            selectcolor=T.BG_CARD,
            font=T.font(T.FONT_BODY_SIZE, bold=True),
            command=self._on_sync_toggled,
        )
        self.sync_dates_check.pack(side="left")

        date_time_picker_cls = _load_datetime_picker_class()
        self.master_date_row = tk.Frame(date_panel, bg=T.BG_CARD)
        self.master_date_picker = date_time_picker_cls(self.master_date_row, label="Tanggal & Jam:", show_time=True)
        self.master_date_picker.pack(anchor="w", fill="x")

        self.stock_date_enabled_var = tk.BooleanVar(value=False)
        self.svl_date_enabled_var = tk.BooleanVar(value=False)
        self.journal_date_enabled_var = tk.BooleanVar(value=False)

        self.stock_date_widgets = self._build_date_row(
            date_panel,
            toggle_var=self.stock_date_enabled_var,
            label="Stock Date (picking / move / line)",
        )
        self.stock_date_picker = self.stock_date_widgets.picker
        self.stock_date_toggle = self.stock_date_widgets.toggle
        self.svl_date_widgets = self._build_date_row(
            date_panel,
            toggle_var=self.svl_date_enabled_var,
            label="SVL Date (valuation layer)",
        )
        self.svl_date_picker = self.svl_date_widgets.picker
        self.svl_date_toggle = self.svl_date_widgets.toggle
        self.journal_date_widgets = self._build_date_row(
            date_panel,
            toggle_var=self.journal_date_enabled_var,
            label="Journal Date (account.move)",
        )
        self.journal_date_picker = self.journal_date_widgets.picker
        self.journal_date_toggle = self.journal_date_widgets.toggle
        self.master_date_row.pack_forget()

        tk.Label(
            date_panel,
            text="Jam pada field date-only akan diabaikan saat dikirim ke Odoo.",
            bg=T.BG_CARD,
            fg=T.TEXT_MUTED,
            font=T.font(T.FONT_SMALL_SIZE),
        ).pack(anchor="w", pady=(8, 6))

        self.btn_apply_dates = tk.Button(
            date_panel,
            text="Apply Date Changes",
            bg=T.BRAND_SECONDARY,
            fg=T.TEXT_ON_DARK,
            font=T.font(T.FONT_BODY_SIZE, bold=True),
            activebackground=T.BRAND_ACCENT,
            activeforeground=T.TEXT_ON_DARK,
            relief="flat",
            padx=20,
            pady=6,
            command=self._apply_dates,
            state="disabled",
        )
        self.btn_apply_dates.pack(anchor="w", pady=(6, 0))

        separator = tk.Frame(inner_edit, bg=T.BORDER_LIGHT, width=1)
        separator.grid(row=0, column=1, sticky="ns", padx=20)

        qty_panel = tk.Frame(inner_edit, bg=T.BG_CARD)
        qty_panel.grid(row=0, column=2, sticky="nsew")

        tk.Label(
            qty_panel,
            text="Edit Qty",
            bg=T.BG_CARD,
            fg=T.TEXT_ON_LIGHT,
            font=T.font(T.FONT_BODY_SIZE, bold=True),
        ).pack(anchor="w")
        tk.Label(
            qty_panel,
            text="Qty diupdate untuk row stock.move.line yang dipilih dan disinkronkan ke move + SVL.",
            bg=T.BG_CARD,
            fg=T.TEXT_MUTED,
            font=T.font(T.FONT_SMALL_SIZE),
            justify="left",
        ).pack(anchor="w", pady=(2, 10))

        qty_info = tk.Frame(qty_panel, bg=T.BG_CARD)
        qty_info.pack(fill="x")
        self.qty_product_var = tk.StringVar(value="-")
        self.qty_done_var = tk.StringVar(value="-")
        self.qty_move_var = tk.StringVar(value="-")
        self.qty_svl_var = tk.StringVar(value="-")
        for row, (label_text, var) in enumerate(
            [
                ("Product:", self.qty_product_var),
                ("Current Line Qty:", self.qty_done_var),
                ("Current Move Qty:", self.qty_move_var),
                ("Current Linked SVL Qty:", self.qty_svl_var),
            ]
        ):
            tk.Label(
                qty_info,
                text=label_text,
                bg=T.BG_CARD,
                fg=T.TEXT_ON_LIGHT,
                font=T.font(T.FONT_BODY_SIZE),
            ).grid(row=row, column=0, sticky="w", pady=2)
            tk.Label(
                qty_info,
                textvariable=var,
                bg=T.BG_CARD,
                fg=T.TEXT_ON_LIGHT,
                font=T.font(T.FONT_BODY_SIZE, bold=True),
            ).grid(row=row, column=1, sticky="w", padx=(8, 0), pady=2)

        input_row = tk.Frame(qty_panel, bg=T.BG_CARD)
        input_row.pack(anchor="w", pady=(12, 0))
        tk.Label(
            input_row,
            text="New Qty:",
            bg=T.BG_CARD,
            fg=T.TEXT_ON_LIGHT,
            font=T.font(T.FONT_BODY_SIZE),
        ).pack(side="left")
        self.qty_input_var = tk.StringVar()
        self.qty_input_entry = tk.Entry(
            input_row,
            textvariable=self.qty_input_var,
            font=T.font(T.FONT_BODY_SIZE),
            width=16,
            bg=T.BG_INPUT,
            relief="solid",
            bd=1,
        )
        self.qty_input_entry.pack(side="left", padx=(8, 0))
        self.qty_input_entry.bind("<KeyRelease>", lambda _event: self._refresh_action_states())

        self.qty_warning_var = tk.StringVar(value="Pilih satu row stock.move.line untuk mengaktifkan editor qty.")
        tk.Label(
            qty_panel,
            textvariable=self.qty_warning_var,
            bg=T.BG_CARD,
            fg=T.STATUS_WARNING,
            font=T.font(T.FONT_SMALL_SIZE),
            justify="left",
            wraplength=360,
        ).pack(anchor="w", pady=(10, 6))

        self.btn_apply_qty = tk.Button(
            qty_panel,
            text="Apply Qty Changes",
            bg=T.BRAND_PRIMARY,
            fg=T.TEXT_ON_DARK,
            font=T.font(T.FONT_BODY_SIZE, bold=True),
            activebackground=T.BRAND_PRIMARY_DARK,
            activeforeground=T.TEXT_ON_DARK,
            relief="flat",
            padx=20,
            pady=6,
            command=self._apply_qty,
            state="disabled",
        )
        self.btn_apply_qty.pack(anchor="w", pady=(2, 0))

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

    def refresh_database_options(self) -> None:
        self._refresh_database_options()

    def _refresh_database_options(self) -> None:
        options = build_module_database_options(self.context.global_settings.database_profiles)
        self._db_label_by_profile_id, self._db_profile_id_by_label = build_option_maps(options)
        labels = [label for _option_id, label in options]
        selected_id = normalize_database_profile_id(self._module_settings.database_profile_id)
        if selected_id not in self._db_label_by_profile_id:
            selected_id = FOLLOW_GLOBAL_PROFILE_ID
            self._module_settings.database_profile_id = selected_id
        self.database_combo.configure(values=labels)
        self.database_choice_var.set(self._db_label_by_profile_id.get(selected_id, ""))

    def _selected_database_profile_id(self) -> str:
        return self._db_profile_id_by_label.get(
            str(self.database_choice_var.get() or "").strip(),
            FOLLOW_GLOBAL_PROFILE_ID,
        )

    def _build_date_row(self, parent: tk.Widget, *, toggle_var: tk.BooleanVar, label: str) -> _DateRowWidgets:
        row = tk.Frame(parent, bg=T.BG_CARD)
        row.pack(anchor="w", fill="x", pady=(0, 8))
        toggle = tk.Checkbutton(
            row,
            variable=toggle_var,
            bg=T.BG_CARD,
            activebackground=T.BG_CARD,
            selectcolor=T.BG_CARD,
            command=self._refresh_action_states,
        )
        toggle.pack(side="left", anchor="n", padx=(0, 8), pady=(16, 0))
        picker_cls = _load_datetime_picker_class()
        picker = picker_cls(row, label=label, show_time=True)
        picker.pack(side="left", anchor="w")
        return _DateRowWidgets(row=row, toggle=toggle, picker=picker)

    def _show_master_date_picker(self, visible: bool) -> None:
        if visible:
            self.master_date_row.pack(anchor="w", fill="x", pady=(0, 8), before=self.stock_date_widgets.row)
        else:
            self.master_date_row.pack_forget()

    def _set_individual_date_controls_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        for toggle in (self.stock_date_toggle, self.svl_date_toggle, self.journal_date_toggle):
            toggle.configure(state=state)
        for picker in (self.stock_date_picker, self.svl_date_picker, self.journal_date_picker):
            picker.set_enabled(enabled)

    def _sync_master_picker_from_individuals(self) -> None:
        for picker in (self.stock_date_picker, self.svl_date_picker, self.journal_date_picker):
            current_value = picker.get_value(date_only=False)
            if current_value:
                self.master_date_picker.set_value(current_value)
                return
        self.master_date_picker.clear()

    def _reset_date_pickers(self) -> None:
        for picker in (
            self.master_date_picker,
            self.stock_date_picker,
            self.svl_date_picker,
            self.journal_date_picker,
        ):
            picker.set_enabled(True)
            picker.clear()

    def _on_sync_toggled(self) -> None:
        if self.sync_dates_var.get():
            self._sync_prev_date_states = {
                "stock": bool(self.stock_date_enabled_var.get()),
                "svl": bool(self.svl_date_enabled_var.get()),
                "journal": bool(self.journal_date_enabled_var.get()),
            }
            self._sync_master_picker_from_individuals()
            self.stock_date_enabled_var.set(True)
            self.svl_date_enabled_var.set(True)
            self.journal_date_enabled_var.set(True)
            self._show_master_date_picker(True)
            self._set_individual_date_controls_enabled(False)
        else:
            self._show_master_date_picker(False)
            self.stock_date_enabled_var.set(bool(self._sync_prev_date_states.get("stock", False)))
            self.svl_date_enabled_var.set(bool(self._sync_prev_date_states.get("svl", False)))
            self.journal_date_enabled_var.set(bool(self._sync_prev_date_states.get("journal", False)))
            self._set_individual_date_controls_enabled(True)
        self._refresh_action_states()

    def _append_log_batch(self, lines: list[str]) -> None:
        if not lines:
            return
        self.latest_log_line_var.set(
            build_compact_preview_text(lines[-1], empty_text="Belum ada log.", max_chars=120)
        )
        append_bounded_text_lines(self.log_text, lines)

    def _append_log(self, text: str) -> None:
        self.latest_log_line_var.set(
            build_compact_preview_text(text, empty_text="Belum ada log.", max_chars=120)
        )
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _on_logs_section_toggled(self, _key: str, is_open: bool) -> None:
        self._module_settings.logs_section_open = bool(is_open)
        self._save_module_settings()

    def _poll_queues(self) -> None:
        if not self._poll_active:
            return
        log_lines: list[str] = []
        while True:
            try:
                line = self.log_queue.get_nowait()
            except queue.Empty:
                break
            log_lines.append(line)
        self._append_log_batch(log_lines)
        while True:
            try:
                event = self.ui_queue.get_nowait()
            except queue.Empty:
                break
            self._handle_ui_event(event)
        self._poll_after_id = self.root.after(33 if self._busy else 250, self._poll_queues)

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

    def _handle_ui_event(self, event: dict[str, Any]) -> None:
        event_type = str(event.get("type", ""))
        if event_type == "picking_loaded":
            self._display_picking_detail(event.get("detail"))
            self.context.status_callback("Picking detail loaded.")
        elif event_type == "edit_done":
            result = event.get("result")
            action = str(event.get("action") or "transaksi")
            detail = event.get("detail")
            selected_move_line_id = int(event.get("selected_move_line_id") or 0)
            if detail is not None:
                self._display_picking_detail(detail, selected_move_line_id=selected_move_line_id)
            if result and result.success:
                self.status_label.configure(text=f"{action.capitalize()} berhasil diupdate!", fg=T.STATUS_SUCCESS)
                self.context.status_callback(f"{action.capitalize()} transaksi berhasil diupdate.")
            else:
                errs = "\n".join(result.errors) if result else "Unknown error"
                self.status_label.configure(text=f"Gagal update {action}", fg=T.STATUS_ERROR)
                self.context.status_callback(f"{action.capitalize()} transaksi gagal diupdate.")
                messagebox.showerror("Error", errs)
            self._set_busy(False)
        elif event_type == "error":
            message = str(event.get("message", ""))
            self.status_label.configure(text=message, fg=T.STATUS_ERROR)
            self.context.status_callback(message or "Error pada Edit Transaksi.")
            if message:
                messagebox.showerror("Error", message)
            self._set_busy(False)
        elif event_type == "busy":
            self._set_busy(bool(event.get("value", False)))

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.btn_fetch.configure(state="disabled" if busy else "normal")
        if busy:
            self.status_label.configure(text="Loading...", fg=T.STATUS_INFO)
            self.context.status_callback("Edit Transaksi sedang berjalan...")
        self._refresh_action_states()

    def _display_picking_detail(self, detail: Any, selected_move_line_id: int = 0) -> None:
        if detail is None:
            return
        self._picking_detail = detail

        picking = detail.picking
        self.info_picking_var.set(str(picking.get("name", "-")))
        self.info_state_var.set(str(picking.get("state", "-")))
        self.info_origin_var.set(str(picking.get("origin", "-") or "-"))

        loc_src = picking.get("location_id")
        loc_dest = picking.get("location_dest_id")
        src_name = loc_src[1] if isinstance(loc_src, (list, tuple)) and len(loc_src) > 1 else str(loc_src)
        dest_name = loc_dest[1] if isinstance(loc_dest, (list, tuple)) and len(loc_dest) > 1 else str(loc_dest)
        self.info_warehouse_var.set(f"{src_name} -> {dest_name}")

        stock_date = str(picking.get("date_done") or picking.get("scheduled_date") or "")
        if not stock_date and detail.line_items:
            stock_date = detail.line_items[0].stock_date
        svl_date = self._first_related_date(detail.svl_records, "_svl_date_field")
        journal_date = self._first_related_date(detail.journal_entries, "date")
        self._reset_date_pickers()
        if stock_date:
            self.stock_date_picker.set_value(stock_date)
        if svl_date:
            self.svl_date_picker.set_value(svl_date)
        if journal_date:
            self.journal_date_picker.set_value(journal_date)
        if self.sync_dates_var.get():
            self._sync_master_picker_from_individuals()
            self.stock_date_enabled_var.set(True)
            self.svl_date_enabled_var.set(True)
            self.journal_date_enabled_var.set(True)
            self._show_master_date_picker(True)
            self.master_date_picker.set_enabled(True)
            self._set_individual_date_controls_enabled(False)
        else:
            self.stock_date_enabled_var.set(False)
            self.svl_date_enabled_var.set(False)
            self.journal_date_enabled_var.set(False)
            self._show_master_date_picker(False)
            self.master_date_picker.set_enabled(True)
            self._set_individual_date_controls_enabled(True)

        self.tree.delete(*self.tree.get_children())
        self._tree_line_map.clear()
        for line_item in detail.line_items:
            tree_id = str(line_item.move_line_id)
            self._tree_line_map[tree_id] = line_item
            svl_display = "-" if line_item.svl_qty is None else self._format_number(line_item.svl_qty)
            self.tree.insert(
                "",
                "end",
                iid=tree_id,
                values=(
                    line_item.product_name,
                    self._format_number(line_item.line_qty),
                    self._format_number(line_item.move_qty),
                    svl_display,
                    line_item.uom_name,
                    line_item.stock_date,
                    line_item.state,
                ),
            )

        self._append_log(f"--- Detail Picking: {picking.get('name')} ---")
        self._append_log(f"State: {picking.get('state')} | Origin: {picking.get('origin') or '-'}")
        self._append_log(
            f"Move Lines: {len(detail.line_items)} | SVL: {len(detail.svl_records)} | Jurnal: {len(detail.journal_entries)}"
        )
        for journal in detail.journal_entries:
            self._append_log(
                f"  Journal: {journal.get('name')} | date={journal.get('date')} | state={journal.get('state')}"
            )

        if self._tree_line_map:
            if selected_move_line_id and str(selected_move_line_id) in self._tree_line_map:
                default_tree_id = str(selected_move_line_id)
            else:
                default_tree_id = next(iter(self._tree_line_map))
            self.tree.selection_set(default_tree_id)
            self.tree.focus(default_tree_id)
            self._update_selected_line(self._tree_line_map[default_tree_id])
        else:
            self._update_selected_line(None)

        self.status_label.configure(text=f"Loaded: {len(detail.line_items)} lines", fg=T.STATUS_SUCCESS)
        self._refresh_action_states()

    def _first_related_date(self, records: list[dict[str, Any]], field_key: str) -> str:
        for record in records:
            if field_key.startswith("_"):
                field_name = str(record.get(field_key) or "")
                if not field_name:
                    continue
                value = str(record.get(field_name) or "").strip()
            else:
                value = str(record.get(field_key) or "").strip()
            if value:
                return value
        return ""

    def _on_tree_select(self, _event: Any) -> None:
        selection = self.tree.selection()
        if not selection:
            self._update_selected_line(None)
            return
        self._update_selected_line(self._tree_line_map.get(selection[0]))

    def _update_selected_line(self, line_item: PickingLineItem | None) -> None:
        self._selected_line = line_item
        if line_item is None:
            self.qty_product_var.set("-")
            self.qty_done_var.set("-")
            self.qty_move_var.set("-")
            self.qty_svl_var.set("-")
            self.qty_warning_var.set("Pilih satu row stock.move.line untuk mengaktifkan editor qty.")
            self.qty_input_var.set("")
            self._refresh_action_states()
            return

        self.qty_product_var.set(line_item.product_name or "-")
        self.qty_done_var.set(self._format_number(line_item.line_qty))
        self.qty_move_var.set(self._format_number(line_item.move_qty))
        self.qty_svl_var.set("-" if line_item.svl_qty is None else self._format_number(line_item.svl_qty))
        self.qty_input_var.set(self._format_number(line_item.line_qty))
        if line_item.svl_qty_editable:
            self.qty_warning_var.set("Qty aman diupdate: stock.move.line, stock.move, dan 1 SVL terkait.")
        else:
            self.qty_warning_var.set(line_item.svl_warning or "Qty diblok karena mapping SVL tidak aman.")
        self._refresh_action_states()

    def _refresh_action_states(self) -> None:
        date_enabled = any(
            [
                self.stock_date_enabled_var.get(),
                self.svl_date_enabled_var.get(),
                self.journal_date_enabled_var.get(),
            ]
        )
        self.btn_apply_dates.configure(
            state="disabled" if self._busy or self._picking_detail is None or not date_enabled else "normal"
        )

        qty_enabled = (
            not self._busy
            and self._picking_detail is not None
            and self._selected_line is not None
            and self._selected_line.svl_qty_editable
            and str(self.qty_input_var.get() or "").strip() != ""
        )
        self.btn_apply_qty.configure(state="normal" if qty_enabled else "disabled")

    def _fetch_picking(self) -> None:
        name = self.picking_var.get().strip()
        if not name:
            messagebox.showwarning("Input", "Masukkan No. Item Movement / Picking.")
            return
        if self.worker is not None and self.worker.is_alive():
            return

        self.ui_queue.put({"type": "busy", "value": True})

        def worker() -> None:
            try:
                settings, config = build_runtime_connection(
                    self.context.global_settings,
                    self.logger,
                    database_profile_id=self._selected_database_profile_id(),
                )
                self.log_queue.put(f"Connecting to {config.base_url}...")
                reader_cls = _load_picking_reader_service_class()

                async def _run() -> Any:
                    async with AsyncOdooJsonRpcClient(config=config, settings=settings, logger=self.logger) as rpc:
                        reader = reader_cls(rpc=rpc, logger=self.logger)
                        return await reader.fetch_picking_by_name(name)

                detail = asyncio.run(_run())
                self.ui_queue.put({"type": "picking_loaded", "detail": detail})
                self.ui_queue.put({"type": "busy", "value": False})
            except Exception as exc:  # noqa: BLE001
                self.log_queue.put(f"ERROR: {exc}")
                self.ui_queue.put({"type": "error", "message": str(exc)})

        self.worker = threading.Thread(target=worker, daemon=True)
        self.worker.start()

    def _apply_dates(self) -> None:
        if self._picking_detail is None:
            return
        if self.worker is not None and self.worker.is_alive():
            return

        try:
            if self.sync_dates_var.get():
                stock_date = self.master_date_picker.get_value(date_only=False)
                svl_date = self.master_date_picker.get_value(date_only=True)
                journal_date = self.master_date_picker.get_value(date_only=True)
            else:
                stock_date = self.stock_date_picker.get_value(date_only=False) if self.stock_date_enabled_var.get() else None
                svl_date = self.svl_date_picker.get_value(date_only=True) if self.svl_date_enabled_var.get() else None
                journal_date = self.journal_date_picker.get_value(date_only=True) if self.journal_date_enabled_var.get() else None
        except ValueError as exc:
            messagebox.showwarning("Input", str(exc))
            return

        if not any([stock_date, svl_date, journal_date]):
            messagebox.showwarning("Input", "Pilih minimal satu field tanggal yang ingin diupdate.")
            return

        picking_id = int(self._picking_detail.picking["id"])
        company_id = extract_many2one_id(self._picking_detail.picking.get("company_id"))
        picking_name = str(self._picking_detail.picking.get("name") or "")

        self.ui_queue.put({"type": "busy", "value": True})
        self._append_log(f"Updating dates for picking {picking_id}...")

        def worker() -> None:
            try:
                settings, config = build_runtime_connection(
                    self.context.global_settings,
                    self.logger,
                    database_profile_id=self._selected_database_profile_id(),
                )
                request_cls = _load_transaction_edit_request_class()
                editor_cls = _load_transaction_date_editor_service_class()
                reader_cls = _load_picking_reader_service_class()
                request = request_cls(
                    picking_id=picking_id,
                    company_id=company_id,
                    new_stock_date=stock_date,
                    new_svl_date=svl_date,
                    new_journal_date=journal_date,
                )

                async def _run() -> tuple[Any, Any]:
                    async with AsyncOdooJsonRpcClient(config=config, settings=settings, logger=self.logger) as rpc:
                        editor = editor_cls(rpc=rpc, settings=settings, logger=self.logger)
                        result = await editor.update_dates(request)
                        detail = None
                        if result.success and picking_name:
                            reader = reader_cls(rpc=rpc, logger=self.logger)
                            detail = await reader.fetch_picking_by_name(picking_name)
                        return result, detail

                result, detail = asyncio.run(_run())
                self._log_result(result)
                self.ui_queue.put({"type": "edit_done", "result": result, "detail": detail, "action": "tanggal"})
            except Exception as exc:  # noqa: BLE001
                self.log_queue.put(f"ERROR: {exc}")
                self.ui_queue.put({"type": "error", "message": str(exc)})

        self.worker = threading.Thread(target=worker, daemon=True)
        self.worker.start()

    def _apply_qty(self) -> None:
        if self._picking_detail is None or self._selected_line is None:
            return
        if self.worker is not None and self.worker.is_alive():
            return
        if not self._selected_line.svl_qty_editable:
            messagebox.showwarning("Qty", self._selected_line.svl_warning or "Mapping SVL tidak aman.")
            return

        try:
            new_qty = self._normalize_qty_input(self.qty_input_var.get().strip())
        except RuntimeError as exc:
            messagebox.showwarning("Input", str(exc))
            return

        company_id = extract_many2one_id(self._picking_detail.picking.get("company_id"))
        picking_id = int(self._picking_detail.picking["id"])
        picking_name = str(self._picking_detail.picking.get("name") or "")
        selected_move_line_id = int(self._selected_line.move_line_id)
        selected_move_id = int(self._selected_line.move_id)
        selected_svl_id = int(self._selected_line.svl_id)
        self.ui_queue.put({"type": "busy", "value": True})
        self._append_log(f"Updating qty for move line {selected_move_line_id} -> {new_qty}...")

        def worker() -> None:
            try:
                settings, config = build_runtime_connection(
                    self.context.global_settings,
                    self.logger,
                    database_profile_id=self._selected_database_profile_id(),
                )
                request_cls = _load_transaction_edit_request_class()
                editor_cls = _load_transaction_date_editor_service_class()
                reader_cls = _load_picking_reader_service_class()
                request = request_cls(
                    picking_id=picking_id,
                    company_id=company_id,
                    target_move_line_id=selected_move_line_id,
                    target_move_id=selected_move_id,
                    target_svl_id=selected_svl_id,
                    new_qty=new_qty,
                )

                async def _run() -> tuple[Any, Any]:
                    async with AsyncOdooJsonRpcClient(config=config, settings=settings, logger=self.logger) as rpc:
                        editor = editor_cls(rpc=rpc, settings=settings, logger=self.logger)
                        result = await editor.update_qty(request)
                        detail = None
                        if result.success and picking_name:
                            reader = reader_cls(rpc=rpc, logger=self.logger)
                            detail = await reader.fetch_picking_by_name(picking_name)
                        return result, detail

                result, detail = asyncio.run(_run())
                self._log_result(result)
                self.ui_queue.put(
                    {
                        "type": "edit_done",
                        "result": result,
                        "detail": detail,
                        "action": "qty",
                        "selected_move_line_id": selected_move_line_id,
                    }
                )
            except Exception as exc:  # noqa: BLE001
                self.log_queue.put(f"ERROR: {exc}")
                self.ui_queue.put({"type": "error", "message": str(exc)})

        self.worker = threading.Thread(target=worker, daemon=True)
        self.worker.start()

    def _log_result(self, result: TransactionEditResult) -> None:
        for msg in result.messages:
            self.log_queue.put(f"OK: {msg}")
        for err in result.errors:
            self.log_queue.put(f"ERROR: {err}")

    @staticmethod
    def _normalize_qty_input(value: str) -> float:
        clean = str(value or "").strip().replace(",", ".")
        if not clean:
            raise RuntimeError("Qty baru wajib diisi.")
        try:
            parsed = float(clean)
        except ValueError as exc:
            raise RuntimeError("Qty baru harus berupa angka.") from exc
        if parsed < 0:
            raise RuntimeError("Qty baru tidak boleh negatif.")
        return parsed

    @staticmethod
    def _format_number(value: float | int | None) -> str:
        if value is None:
            return "-"
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return str(value)
        if parsed.is_integer():
            return str(int(parsed))
        return f"{parsed:.4f}".rstrip("0").rstrip(".")

    def _save_module_settings(self) -> None:
        self._module_settings.database_profile_id = self._selected_database_profile_id()
        self._state_store.save(self._module_settings)

    def shutdown(self) -> None:
        self.pause()
        self._save_module_settings()
