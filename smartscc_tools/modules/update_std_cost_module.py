"""Shell-native module for Update Standard Cost workflow."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from functools import lru_cache
import os
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText
from typing import Any

from smartscc_tools.branding import UPDATE_STANDARD_COST_ICON
from smartscc_tools.core import theme as T
from smartscc_tools.services.odoo.profiles import (
    FOLLOW_GLOBAL_PROFILE_ID,
    build_module_database_options,
    build_option_maps,
    normalize_database_profile_id,
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


@lru_cache(maxsize=1)
def _load_update_std_cost_config_module():
    from smartscc_tools.features.update_std_cost import config

    return config


@lru_cache(maxsize=1)
def _load_update_std_cost_settings_class():
    return _load_update_std_cost_config_module().UpdateStdCostSettings


@lru_cache(maxsize=1)
def _load_update_std_cost_run_request_class():
    from smartscc_tools.features.update_std_cost.models import UpdateStdCostRunRequest

    return UpdateStdCostRunRequest


@lru_cache(maxsize=1)
def _load_update_std_cost_service_class():
    from smartscc_tools.features.update_std_cost.service import UpdateStdCostServiceAsync

    return UpdateStdCostServiceAsync


def build_runtime_connection(
    global_settings: GlobalSettings,
    logger,
    *,
    database_profile_id: str = FOLLOW_GLOBAL_PROFILE_ID,
) -> tuple[Any, Any]:
    config_module = _load_update_std_cost_config_module()
    settings, _ = build_runtime_settings(preset="safe-fast", set_args=[])
    config = fetch_odoo_config(settings=settings)
    effective_database = config_module.resolve_effective_database(
        database_profile_id=database_profile_id,
        global_settings=global_settings,
        default_database=config.database,
    )
    if effective_database:
        config.database = effective_database
        logger.info("DB efektif untuk %s: %s", "Update Standard Cost Item - Odoo", config.database)
    return settings, config


class _UpdateStdCostStateStore:
    def __init__(
        self,
        global_settings: GlobalSettings,
        global_state_store: GlobalPersistentState | None = None,
    ) -> None:
        self._global_settings = global_settings
        self._global_state_store = global_state_store or GlobalPersistentState()

    def load(self):
        settings_cls = _load_update_std_cost_settings_class()
        config_module = _load_update_std_cost_config_module()
        payload = self._global_settings.module_settings.get("update_std_cost")
        if not isinstance(payload, dict):
            return settings_cls()
        return settings_cls(
            database_profile_id=normalize_database_profile_id(payload.get("database_profile_id")),
            last_workbook_file=str(payload.get("last_workbook_file") or ""),
            mode=config_module.normalize_mode(payload.get("mode")),
            execute_dry_run=bool(payload.get("execute_dry_run", False)),
            logs_section_open=bool(payload.get("logs_section_open", False)),
        )

    def save(self, settings) -> None:
        config_module = _load_update_std_cost_config_module()
        payload = asdict(settings)
        payload["database_profile_id"] = normalize_database_profile_id(payload.get("database_profile_id"))
        payload["mode"] = config_module.normalize_mode(payload.get("mode"))
        self._global_settings.module_settings["update_std_cost"] = payload
        self._global_state_store.save(self._global_settings)


class UpdateStdCostModule(ModuleBase):
    @property
    def module_id(self) -> str:
        return "update_std_cost"

    @property
    def display_name(self) -> str:
        return "Update Standard Cost Item - Odoo"

    @property
    def description(self) -> str:
        return "Update standard cost dari workbook Preparation Cost ke Odoo"

    @property
    def icon_path(self) -> str | None:
        return str(UPDATE_STANDARD_COST_ICON) if UPDATE_STANDARD_COST_ICON.exists() else None

    def create_ui(self, parent: tk.Frame, context: ModuleContext) -> tk.Frame:
        self._panel = _UpdateStdCostPanel(parent, context)
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


class _UpdateStdCostPanel:
    def __init__(self, parent: tk.Frame, context: ModuleContext) -> None:
        self.parent = parent
        self.context = context
        self.root = parent.winfo_toplevel()
        self.logger = context.logger
        self.log_queue: queue.Queue[str] = queue.Queue()
        self.ui_queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self.worker: threading.Thread | None = None
        self._active_service: Any | None = None
        self._poll_after_id: str | None = None
        self._poll_active = False
        self._busy = False
        self._state_store = _UpdateStdCostStateStore(context.global_settings)
        self._module_settings = self._state_store.load()
        self._db_label_by_profile_id: dict[str, str] = {}
        self._db_profile_id_by_label: dict[str, str] = {}
        self._latest_summary: Any | None = None

        self.latest_log_line_var = tk.StringVar(value="Belum ada log.")
        self.summary_var = tk.StringVar(value="Belum ada hasil proses.")
        self.phase_var = tk.StringVar(value="Idle")
        self.percent_var = tk.StringVar(value="0%")
        self.count_var = tk.StringVar(value="0 / 0")
        self.status_var = tk.StringVar(value="Siap.")
        self.progress_value = tk.DoubleVar(value=0.0)

        self._build_ui()
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

        workbook_card = self._build_card(main, title="Workbook", pady=(16, 8))
        workbook_row = tk.Frame(workbook_card, bg=T.BG_CARD)
        workbook_row.pack(fill="x")
        tk.Label(
            workbook_row,
            text="Workbook (.xlsm/.xlsx):",
            bg=T.BG_CARD,
            fg=T.TEXT_ON_LIGHT,
            font=T.font(T.FONT_BODY_SIZE, bold=True),
        ).pack(side="left")
        self.workbook_var = tk.StringVar(value=self._module_settings.last_workbook_file)
        self.workbook_entry = tk.Entry(
            workbook_row,
            textvariable=self.workbook_var,
            font=T.font(T.FONT_BODY_SIZE),
            bg=T.BG_INPUT,
            relief="solid",
            bd=1,
        )
        self.workbook_entry.pack(side="left", fill="x", expand=True, padx=(12, 8))
        self.btn_browse = tk.Button(
            workbook_row,
            text="Browse...",
            bg=T.BRAND_PRIMARY,
            fg=T.TEXT_ON_DARK,
            font=T.font(T.FONT_BODY_SIZE, bold=True),
            activebackground=T.BRAND_PRIMARY_DARK,
            activeforeground=T.TEXT_ON_DARK,
            relief="flat",
            padx=16,
            pady=4,
            command=self._browse_workbook,
        )
        self.btn_browse.pack(side="left")
        self.btn_open_template = tk.Button(
            workbook_row,
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

        options_card = self._build_card(main, title="Database & Mode")
        options_grid = tk.Frame(options_card, bg=T.BG_CARD)
        options_grid.pack(fill="x")
        options_grid.grid_columnconfigure(1, weight=1)
        options_grid.grid_columnconfigure(3, weight=1)

        tk.Label(options_grid, text="Database:", bg=T.BG_CARD, fg=T.TEXT_ON_LIGHT, font=T.font()).grid(
            row=0, column=0, sticky="w", pady=4
        )
        self.database_choice_var = tk.StringVar(value="")
        self.database_combo = ttk.Combobox(
            options_grid,
            state="readonly",
            textvariable=self.database_choice_var,
            width=42,
        )
        self.database_combo.grid(row=0, column=1, sticky="ew", padx=(8, 16), pady=4)

        tk.Label(options_grid, text="Mode:", bg=T.BG_CARD, fg=T.TEXT_ON_LIGHT, font=T.font()).grid(
            row=0, column=2, sticky="w", pady=4
        )
        self.mode_var = tk.StringVar(value=_load_update_std_cost_config_module().normalize_mode(self._module_settings.mode))
        self.mode_combo = ttk.Combobox(
            options_grid,
            state="readonly",
            values=["template", "variant", "both"],
            textvariable=self.mode_var,
            width=18,
        )
        self.mode_combo.grid(row=0, column=3, sticky="w", pady=4)

        self.execute_dry_run_var = tk.BooleanVar(value=bool(self._module_settings.execute_dry_run))
        self.execute_dry_run_check = tk.Checkbutton(
            options_grid,
            text="Dry-run saat Execute",
            variable=self.execute_dry_run_var,
            bg=T.BG_CARD,
            fg=T.TEXT_ON_LIGHT,
            activebackground=T.BG_CARD,
            activeforeground=T.TEXT_ON_LIGHT,
            selectcolor=T.BG_CARD,
            font=T.font(T.FONT_BODY_SIZE, bold=True),
        )
        self.execute_dry_run_check.grid(row=1, column=0, columnspan=4, sticky="w", pady=(8, 0))
        self._refresh_database_options()

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
            state="disabled",
            command=self._stop_run,
        )
        self.btn_stop.pack(side="left", padx=(8, 0))

        progress_row = tk.Frame(action_card, bg=T.BG_CARD)
        progress_row.pack(fill="x", pady=(12, 4))
        self.progress_bar = ttk.Progressbar(progress_row, variable=self.progress_value, maximum=100)
        self.progress_bar.pack(side="left", fill="x", expand=True)
        tk.Label(
            progress_row,
            textvariable=self.percent_var,
            bg=T.BG_CARD,
            fg=T.TEXT_ON_LIGHT,
            font=T.font(T.FONT_BODY_SIZE, bold=True),
        ).pack(side="left", padx=(12, 8))
        tk.Label(
            progress_row,
            textvariable=self.count_var,
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
        tk.Label(meta_row, textvariable=self.status_var, bg=T.BG_CARD, fg=T.TEXT_MUTED, font=T.font()).pack(
            side="left"
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

    def refresh_database_options(self) -> None:
        self._refresh_database_options()

    def _refresh_database_options(self) -> None:
        options = build_module_database_options(self.context.global_settings.database_profiles)
        self._db_label_by_profile_id, self._db_profile_id_by_label = build_option_maps(options)
        labels = [label for _profile_id, label in options]
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

    def _save_module_settings(self) -> None:
        config_module = _load_update_std_cost_config_module()
        self._module_settings.database_profile_id = self._selected_database_profile_id()
        self._module_settings.last_workbook_file = str(self.workbook_var.get() or "").strip()
        self._module_settings.mode = config_module.normalize_mode(self.mode_var.get())
        self._module_settings.execute_dry_run = bool(self.execute_dry_run_var.get())
        self._state_store.save(self._module_settings)

    def _append_log_batch(self, lines: list[str]) -> None:
        if not lines:
            return
        self.latest_log_line_var.set(build_compact_preview_text(lines[-1], empty_text="Belum ada log.", max_chars=140))
        append_bounded_text_lines(self.log_text, lines)

    def _append_log(self, text: str) -> None:
        self.latest_log_line_var.set(build_compact_preview_text(text, empty_text="Belum ada log.", max_chars=140))
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _on_logs_section_toggled(self, _key: str, is_open: bool) -> None:
        self._module_settings.logs_section_open = bool(is_open)
        self._save_module_settings()

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

    def _apply_progress(self, snapshot) -> None:
        self.progress_value.set(round(float(snapshot.progress or 0.0) * 100, 2))
        self.percent_var.set(f"{int(round(float(snapshot.progress or 0.0) * 100))}%")
        self.count_var.set(f"{int(snapshot.processed)} / {int(snapshot.total)}")
        self.phase_var.set(snapshot.current or snapshot.phase or "-")

    def _compose_summary_text(self, summary) -> str:
        mode_text = summary.requested_mode.upper()
        dry_run_text = "DRY-RUN" if summary.dry_run else "WRITE"
        mode_lines = []
        for mode_summary in summary.mode_summaries:
            mode_lines.append(
                f"{mode_summary.mode.upper()}: yes={mode_summary.updated_rows}, no={mode_summary.failed_rows}, "
                f"write_ok={mode_summary.write_ok}, write_fail={mode_summary.write_fail}, "
                f"missing_area={mode_summary.missing_areas}, missing_product={mode_summary.missing_products}"
            )
        suffix = " | ".join(mode_lines) if mode_lines else "Belum ada detail."
        return (
            f"Mode {mode_text} | {dry_run_text} | DB {summary.database} | "
            f"Total {summary.total_rows} baris | Success {summary.updated_rows} | Failed {summary.failed_rows}\n{suffix}"
        )

    def _apply_summary(self, summary) -> None:
        self._latest_summary = summary
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
        elif event_type == "state":
            status = str(event.get("status") or "")
            message = str(event.get("message") or "")
            summary = event.get("summary")
            database = str(event.get("database") or "")
            display_name = "Update Standard Cost Item - Odoo"
            self.context.status_callback(message or status or display_name)
            self.status_var.set(message or status or display_name)
            if status in {"completed", "canceled"} and summary is not None:
                self._apply_summary(summary)
                self._set_busy(False)
                self._active_service = None
            elif status == "error":
                self._set_busy(False)
                self._active_service = None
                if summary is not None:
                    self._apply_summary(summary)
                if message:
                    messagebox.showerror(display_name, message)
            elif status == "connected":
                self.status_var.set(f"Connected: {database}")
        elif event_type == "error":
            message = str(event.get("message") or "")
            self._set_busy(False)
            self._active_service = None
            self.status_var.set(message or "Terjadi error.")
            if message:
                messagebox.showerror("Update Standard Cost Item - Odoo", message)

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

    def _browse_workbook(self) -> None:
        config_module = _load_update_std_cost_config_module()
        initial_dir = config_module.default_input_browse_dir(self.context.global_settings, self.workbook_var.get())
        selected = filedialog.askopenfilename(
            title="Pilih workbook Update Standard Cost",
            initialdir=initial_dir or None,
            filetypes=[("Excel Workbook", "*.xlsx;*.xlsm"), ("All Files", "*.*")],
        )
        if not selected:
            return
        self.workbook_var.set(selected)
        self._save_module_settings()

    def _open_template(self) -> None:
        config_module = _load_update_std_cost_config_module()
        template_path = config_module.TEMPLATE_PATH
        if not template_path.exists():
            messagebox.showerror("Update Standard Cost Item - Odoo", f"Template tidak ditemukan: {template_path}")
            return
        if hasattr(os, "startfile"):
            os.startfile(str(template_path))  # type: ignore[attr-defined]
            return
        messagebox.showinfo("Update Standard Cost Item - Odoo", f"Template tersedia di:\n{template_path}")

    def _build_run_request(self, database: str, *, force_dry_run: bool):
        request_cls = _load_update_std_cost_run_request_class()
        config_module = _load_update_std_cost_config_module()
        return request_cls(
            workbook_path=str(self.workbook_var.get() or "").strip(),
            database=database,
            mode=config_module.normalize_mode(self.mode_var.get()),
            dry_run=bool(force_dry_run),
        )

    def _confirm_execute(self) -> bool:
        execute_mode = "DRY-RUN" if self.execute_dry_run_var.get() else "WRITE ke Odoo"
        return messagebox.askyesno(
            "Konfirmasi Execute",
            f"Execute akan menjalankan proses {execute_mode}. Lanjutkan?",
            icon="warning",
        )

    def _start_run(self, action: str) -> None:
        workbook_path = str(self.workbook_var.get() or "").strip()
        if not workbook_path:
            messagebox.showwarning("Input", "Pilih workbook terlebih dahulu.")
            return
        if self.worker is not None and self.worker.is_alive():
            return
        if action == "execute" and not self._confirm_execute():
            return

        self._save_module_settings()
        self._apply_summary(None)
        self.status_var.set("Memulai proses...")
        self.ui_queue.put({"type": "busy", "value": True})

        def worker() -> None:
            try:
                settings, config = build_runtime_connection(
                    self.context.global_settings,
                    self.logger,
                    database_profile_id=self._selected_database_profile_id(),
                )
                request = self._build_run_request(
                    config.database,
                    force_dry_run=(action == "validate") or bool(self.execute_dry_run_var.get()),
                )
                self.log_queue.put(f"Connecting to {config.base_url} [{config.database}]...")
                service_cls = _load_update_std_cost_service_class()

                async def _run():
                    async with AsyncOdooJsonRpcClient(config=config, settings=settings, logger=self.logger) as rpc:
                        service = service_cls(
                            rpc=rpc,
                            logger=self.logger,
                            on_log=self.log_queue.put,
                            on_progress=lambda snapshot: self.ui_queue.put(
                                {"type": "progress", "snapshot": snapshot}
                            ),
                            on_state=lambda status, message, summary, database: self.ui_queue.put(
                                {
                                    "type": "state",
                                    "status": status,
                                    "message": message,
                                    "summary": summary,
                                    "database": database,
                                }
                            ),
                            master_cache=self.context.master_cache,
                        )
                        self._active_service = service
                        if action == "validate":
                            return await service.validate(request)
                        return await service.execute(request)

                asyncio.run(_run())
            except Exception as exc:  # noqa: BLE001
                self.log_queue.put(f"ERROR: {exc}")
                self.ui_queue.put({"type": "error", "message": str(exc)})
            finally:
                self._active_service = None
                self.ui_queue.put({"type": "busy", "value": False})

        self.worker = threading.Thread(target=worker, daemon=True, name="update-std-cost-worker")
        self.worker.start()

    def _stop_run(self) -> None:
        if self._active_service is None:
            return
        self._active_service.request_stop()
        self.status_var.set("Menghentikan proses...")

    def shutdown(self) -> None:
        self.pause()
        if self._active_service is not None:
            self._active_service.request_stop()
        self._save_module_settings()
