"""Global settings panel for credentials, paths, and application update config."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Callable
import webbrowser

from smartscc_tools import APP_VERSION
from smartscc_tools.core import theme as T
from smartscc_tools.services.odoo.profiles import (
    DatabaseProfile,
    build_global_default_database_options,
    build_new_database_profile_id,
    build_option_maps,
    normalize_database_profile_text,
    render_database_profile_label,
)
from smartscc_tools.core.global_config import GlobalSettings, GlobalPersistentState
from smartscc_tools.core.global_config import InventoryCoaEntry, RepairAccountEntry
from smartscc_tools.update_service import (
    UpdateCheckResult,
    UpdateDownloadProgress,
    UpdateManifest,
    UpdateService,
    format_utc_timestamp,
    should_auto_check,
    utc_now_iso,
)
from smartscc_tools.widgets.scrollable_frame import ScrollableFrame


AUTO_UPDATE_CHECK_DELAY_MS = 2000


@dataclass(frozen=True)
class UpdatePanelViewState:
    status_text: str
    check_enabled: bool
    download_enabled: bool
    install_enabled: bool
    release_notes_enabled: bool


def build_update_panel_view_state(
    phase: str,
    *,
    latest_version: str = "",
    has_manifest: bool = False,
    has_download: bool = False,
    has_release_notes: bool = False,
    error_text: str = "",
) -> UpdatePanelViewState:
    clean_version = latest_version.strip() or "-"
    clean_error = error_text.strip()

    if phase == "checking":
        return UpdatePanelViewState(
            status_text="Sedang cek update...",
            check_enabled=False,
            download_enabled=False,
            install_enabled=False,
            release_notes_enabled=has_release_notes,
        )
    if phase == "downloading":
        return UpdatePanelViewState(
            status_text=f"Sedang download update {clean_version}...",
            check_enabled=False,
            download_enabled=False,
            install_enabled=False,
            release_notes_enabled=has_release_notes,
        )
    if phase == "ready":
        return UpdatePanelViewState(
            status_text=f"Update {clean_version} siap diinstall.",
            check_enabled=True,
            download_enabled=True,
            install_enabled=True,
            release_notes_enabled=has_release_notes,
        )
    if phase == "available":
        return UpdatePanelViewState(
            status_text=f"Update {clean_version} tersedia untuk di-download.",
            check_enabled=True,
            download_enabled=has_manifest,
            install_enabled=has_download,
            release_notes_enabled=has_release_notes,
        )
    if phase == "ignored":
        return UpdatePanelViewState(
            status_text=f"Update {clean_version} tersedia tetapi sedang diabaikan.",
            check_enabled=True,
            download_enabled=has_manifest,
            install_enabled=has_download,
            release_notes_enabled=has_release_notes,
        )
    if phase == "no_update":
        return UpdatePanelViewState(
            status_text="Aplikasi sudah memakai versi terbaru.",
            check_enabled=True,
            download_enabled=False,
            install_enabled=False,
            release_notes_enabled=has_release_notes,
        )
    if phase == "failed":
        return UpdatePanelViewState(
            status_text=clean_error or "Pengecekan update gagal.",
            check_enabled=True,
            download_enabled=has_manifest,
            install_enabled=has_download,
            release_notes_enabled=has_release_notes,
        )
    return UpdatePanelViewState(
        status_text="Belum cek update.",
        check_enabled=True,
        download_enabled=has_manifest,
        install_enabled=has_download,
        release_notes_enabled=has_release_notes,
    )


def format_download_progress(progress: UpdateDownloadProgress) -> str:
    if progress.total_bytes > 0:
        percent = max(0, min(100, int((progress.bytes_received / progress.total_bytes) * 100)))
        return f"Sedang download update... {percent}% ({_format_bytes(progress.bytes_received)} / {_format_bytes(progress.total_bytes)})"
    return f"Sedang download update... {_format_bytes(progress.bytes_received)}"


def _format_bytes(value: int) -> str:
    size = float(max(0, value))
    units = ("B", "KB", "MB", "GB")
    index = 0
    while size >= 1024.0 and index < len(units) - 1:
        size /= 1024.0
        index += 1
    if index == 0:
        return f"{int(size)} {units[index]}"
    return f"{size:.1f} {units[index]}"


def build_database_profile_preview(alias: str, database_value: str, note: str) -> str:
    profile = DatabaseProfile(
        profile_id="preview",
        database_value=normalize_database_profile_text(database_value),
        alias=normalize_database_profile_text(alias),
        note=normalize_database_profile_text(note),
    )
    return render_database_profile_label(profile)


def build_inventory_coa_preview(coa_code: str, label: str) -> str:
    code = normalize_database_profile_text(coa_code).upper()
    hint = normalize_database_profile_text(label)
    if hint:
        return f"{code} - {hint}"
    return code or "Unnamed COA"


def build_repair_account_preview(coa_code: str, label: str) -> str:
    code = normalize_database_profile_text(coa_code).upper()
    hint = normalize_database_profile_text(label)
    if hint:
        return f"{code} - {hint}"
    return code or "Unnamed Repair Account"


class GlobalSettingsPanel(tk.Frame):
    """Settings UI shown when user clicks Settings in sidebar."""

    def __init__(
        self,
        parent: tk.Widget,
        settings: GlobalSettings,
        state_store: GlobalPersistentState,
        *,
        root: tk.Misc | None = None,
        status_callback: Callable[[str], None] | None = None,
        shutdown_callback: Callable[[], None] | None = None,
        update_service: UpdateService | None = None,
    ) -> None:
        super().__init__(parent, bg=T.BG_MAIN)
        self._settings = settings
        self._state_store = state_store
        self._root = root
        self._status_callback = status_callback or (lambda _message: None)
        self._shutdown_callback = shutdown_callback or self._default_shutdown
        self._update_service = update_service or UpdateService(current_version=APP_VERSION)
        self._update_queue: "queue.Queue[dict[str, object]]" = queue.Queue()
        self._update_thread: threading.Thread | None = None
        self._update_phase = "idle"
        self._current_manifest: UpdateManifest | None = None
        self._cached_installer_path: Path | None = None
        self._release_notes_url = ""
        self._auto_check_after_id: str | None = None
        self._poll_after_id: str | None = None
        self._poll_active = False

        self._database_profiles: list[DatabaseProfile] = list(self._settings.database_profiles)
        self._selected_database_profile_id = ""
        self._inventory_coa_entries: list[InventoryCoaEntry] = list(self._settings.inventory_coa_entries)
        self._selected_inventory_coa_entry_id = ""
        self._repair_account_entries: list[RepairAccountEntry] = list(self._settings.repair_account_entries)
        self._selected_repair_account_entry_id = ""
        self._global_default_label_by_id: dict[str, str] = {}
        self._global_default_id_by_label: dict[str, str] = {}
        self.default_database_profile_var = tk.StringVar(value="")
        self.profile_value_var = tk.StringVar(value="")
        self.profile_alias_var = tk.StringVar(value="")
        self.profile_note_var = tk.StringVar(value="")
        self.profile_preview_var = tk.StringVar(value="Unnamed Database")
        self.inventory_coa_code_var = tk.StringVar(value="")
        self.inventory_coa_label_var = tk.StringVar(value="")
        self.inventory_coa_preview_var = tk.StringVar(value="Unnamed COA")
        self.repair_account_code_var = tk.StringVar(value="")
        self.repair_account_label_var = tk.StringVar(value="")
        self.repair_account_preview_var = tk.StringVar(value="Unnamed Repair Account")
        self.output_dir_var = tk.StringVar(value=self._settings.default_output_dir)
        self.input_dir_var = tk.StringVar(value=self._settings.default_input_dir)
        self.auto_check_updates_var = tk.BooleanVar(value=self._settings.auto_check_updates)
        self.auto_download_updates_var = tk.BooleanVar(value=self._settings.auto_download_updates)
        self.current_version_var = tk.StringVar(value=APP_VERSION)
        self.latest_version_var = tk.StringVar(value="-")
        self.last_checked_var = tk.StringVar(value=format_utc_timestamp(self._settings.last_update_check_utc))
        self.update_status_var = tk.StringVar(value="Belum cek update.")

        for variable in (self.profile_value_var, self.profile_alias_var, self.profile_note_var):
            variable.trace_add("write", self._refresh_database_profile_preview)
        for variable in (self.inventory_coa_code_var, self.inventory_coa_label_var):
            variable.trace_add("write", self._refresh_inventory_coa_preview)
        for variable in (self.repair_account_code_var, self.repair_account_label_var):
            variable.trace_add("write", self._refresh_repair_account_preview)

        self._build_ui()
        self._refresh_database_profile_preview()
        self._refresh_database_profile_ui()
        self._refresh_inventory_coa_preview()
        self._refresh_inventory_coa_ui()
        self._refresh_repair_account_preview()
        self._refresh_repair_account_ui()
        self._apply_update_view_state("idle")
        self.on_show()

    def _build_ui(self) -> None:
        self._scrollable = ScrollableFrame(self, bg=T.BG_MAIN)
        self._scrollable.pack(fill="both", expand=True)
        main = self._scrollable.interior

        tk.Label(
            main,
            text="Global Settings",
            bg=T.BG_MAIN,
            fg=T.TEXT_ON_LIGHT,
            font=T.font(T.FONT_HEADING_SIZE, bold=True),
        ).pack(anchor="w", padx=24, pady=(24, 16))

        card = tk.Frame(
            main,
            bg=T.BG_CARD,
            bd=1,
            relief="solid",
            highlightbackground=T.BORDER_LIGHT,
            highlightthickness=1,
        )
        card.pack(fill="x", padx=24, pady=(0, 16))

        inner = tk.Frame(card, bg=T.BG_CARD, padx=20, pady=16)
        inner.pack(fill="x")

        tk.Label(
            inner,
            text="Database Profiles",
            bg=T.BG_CARD,
            fg=T.BRAND_PRIMARY,
            font=T.font(12, bold=True),
        ).grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 8))

        tk.Label(
            inner,
            text="Credentials tetap diambil otomatis dari GAS. Daftar database di bawah dipakai sebagai pilihan shared untuk semua module Odoo.",
            bg=T.BG_CARD,
            fg=T.TEXT_MUTED,
            font=T.font(T.FONT_SMALL_SIZE),
        ).grid(row=1, column=0, columnspan=4, sticky="w", pady=(0, 8))

        tk.Label(
            inner,
            text="Global Default:",
            bg=T.BG_CARD,
            fg=T.TEXT_ON_LIGHT,
            font=T.font(T.FONT_BODY_SIZE),
        ).grid(row=2, column=0, sticky="w")
        self.default_database_profile_combo = ttk.Combobox(
            inner,
            textvariable=self.default_database_profile_var,
            state="readonly",
            width=48,
            font=T.font(T.FONT_BODY_SIZE),
        )
        self.default_database_profile_combo.grid(row=2, column=1, sticky="w", padx=(8, 0))

        profiles_frame = tk.Frame(inner, bg=T.BG_CARD)
        profiles_frame.grid(row=3, column=0, columnspan=4, sticky="ew", pady=(12, 0))
        profiles_frame.grid_columnconfigure(1, weight=1)

        left_profiles = tk.Frame(profiles_frame, bg=T.BG_CARD)
        left_profiles.grid(row=0, column=0, sticky="nsw", padx=(0, 16))
        tk.Label(
            left_profiles,
            text="Saved Profiles",
            bg=T.BG_CARD,
            fg=T.TEXT_ON_LIGHT,
            font=T.font(T.FONT_BODY_SIZE, bold=True),
        ).pack(anchor="w")
        self.database_profile_listbox = tk.Listbox(
            left_profiles,
            height=6,
            width=42,
            exportselection=False,
            font=T.font(T.FONT_SMALL_SIZE),
        )
        self.database_profile_listbox.pack(fill="both", expand=True, pady=(6, 0))
        self.database_profile_listbox.bind("<<ListboxSelect>>", self._on_database_profile_selected)

        left_button_row = tk.Frame(left_profiles, bg=T.BG_CARD)
        left_button_row.pack(fill="x", pady=(8, 0))
        tk.Button(
            left_button_row,
            text="Add New",
            command=self._start_new_database_profile,
            bg=T.BRAND_PRIMARY,
            fg=T.TEXT_ON_DARK,
            relief="flat",
            font=T.font(T.FONT_SMALL_SIZE, bold=True),
            padx=12,
            pady=4,
        ).pack(side="left")
        tk.Button(
            left_button_row,
            text="Delete",
            command=self._delete_selected_database_profile,
            bg=T.STATUS_ERROR,
            fg=T.TEXT_ON_DARK,
            relief="flat",
            font=T.font(T.FONT_SMALL_SIZE, bold=True),
            padx=12,
            pady=4,
        ).pack(side="left", padx=(8, 0))

        right_profiles = tk.Frame(profiles_frame, bg=T.BG_CARD)
        right_profiles.grid(row=0, column=1, sticky="nsew")
        right_profiles.grid_columnconfigure(1, weight=1)
        tk.Label(
            right_profiles,
            text="Database Value:",
            bg=T.BG_CARD,
            fg=T.TEXT_ON_LIGHT,
            font=T.font(T.FONT_BODY_SIZE),
        ).grid(row=0, column=0, sticky="w")
        tk.Entry(
            right_profiles,
            textvariable=self.profile_value_var,
            font=T.font(T.FONT_BODY_SIZE),
            bg=T.BG_INPUT,
            relief="solid",
            bd=1,
        ).grid(row=0, column=1, sticky="ew", padx=(8, 0))

        tk.Label(
            right_profiles,
            text="Alias:",
            bg=T.BG_CARD,
            fg=T.TEXT_ON_LIGHT,
            font=T.font(T.FONT_BODY_SIZE),
        ).grid(row=1, column=0, sticky="w", pady=(8, 0))
        tk.Entry(
            right_profiles,
            textvariable=self.profile_alias_var,
            font=T.font(T.FONT_BODY_SIZE),
            bg=T.BG_INPUT,
            relief="solid",
            bd=1,
        ).grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=(8, 0))

        tk.Label(
            right_profiles,
            text="Note:",
            bg=T.BG_CARD,
            fg=T.TEXT_ON_LIGHT,
            font=T.font(T.FONT_BODY_SIZE),
        ).grid(row=2, column=0, sticky="w", pady=(8, 0))
        tk.Entry(
            right_profiles,
            textvariable=self.profile_note_var,
            font=T.font(T.FONT_BODY_SIZE),
            bg=T.BG_INPUT,
            relief="solid",
            bd=1,
        ).grid(row=2, column=1, sticky="ew", padx=(8, 0), pady=(8, 0))

        tk.Label(
            right_profiles,
            text="Preview:",
            bg=T.BG_CARD,
            fg=T.TEXT_ON_LIGHT,
            font=T.font(T.FONT_BODY_SIZE),
        ).grid(row=3, column=0, sticky="nw", pady=(8, 0))
        tk.Label(
            right_profiles,
            textvariable=self.profile_preview_var,
            bg=T.BG_CARD,
            fg=T.TEXT_MUTED,
            font=T.font(T.FONT_SMALL_SIZE, bold=True),
            justify="left",
            wraplength=380,
        ).grid(row=3, column=1, sticky="w", padx=(8, 0), pady=(8, 0))

        tk.Button(
            right_profiles,
            text="Save Profile",
            command=self._save_database_profile_edit,
            bg=T.BRAND_SECONDARY,
            fg=T.TEXT_ON_DARK,
            relief="flat",
            font=T.font(T.FONT_SMALL_SIZE, bold=True),
            padx=14,
            pady=4,
        ).grid(row=4, column=1, sticky="w", pady=(12, 0), padx=(8, 0))

        tk.Frame(inner, bg=T.BORDER_LIGHT, height=1).grid(
            row=4, column=0, columnspan=4, sticky="ew", pady=12
        )

        tk.Label(
            inner,
            text="Inventory COA Dashboard",
            bg=T.BG_CARD,
            fg=T.BRAND_PRIMARY,
            font=T.font(12, bold=True),
        ).grid(row=5, column=0, columnspan=4, sticky="w", pady=(0, 8))

        tk.Label(
            inner,
            text="Daftar manual COA persediaan untuk summary Dashboard Control. Matching dilakukan exact ke account.account.code pada database aktif.",
            bg=T.BG_CARD,
            fg=T.TEXT_MUTED,
            font=T.font(T.FONT_SMALL_SIZE),
            justify="left",
            wraplength=760,
        ).grid(row=6, column=0, columnspan=4, sticky="w", pady=(0, 8))

        inventory_frame = tk.Frame(inner, bg=T.BG_CARD)
        inventory_frame.grid(row=7, column=0, columnspan=4, sticky="ew", pady=(0, 4))
        inventory_frame.grid_columnconfigure(1, weight=1)

        left_inventory = tk.Frame(inventory_frame, bg=T.BG_CARD)
        left_inventory.grid(row=0, column=0, sticky="nsw", padx=(0, 16))
        tk.Label(
            left_inventory,
            text="Saved COA",
            bg=T.BG_CARD,
            fg=T.TEXT_ON_LIGHT,
            font=T.font(T.FONT_BODY_SIZE, bold=True),
        ).pack(anchor="w")
        self.inventory_coa_listbox = tk.Listbox(
            left_inventory,
            height=7,
            width=42,
            exportselection=False,
            font=T.font(T.FONT_SMALL_SIZE),
        )
        self.inventory_coa_listbox.pack(fill="both", expand=True, pady=(6, 0))
        self.inventory_coa_listbox.bind("<<ListboxSelect>>", self._on_inventory_coa_selected)

        left_inventory_button_row = tk.Frame(left_inventory, bg=T.BG_CARD)
        left_inventory_button_row.pack(fill="x", pady=(8, 0))
        tk.Button(
            left_inventory_button_row,
            text="Add New",
            command=self._start_new_inventory_coa_entry,
            bg=T.BRAND_PRIMARY,
            fg=T.TEXT_ON_DARK,
            relief="flat",
            font=T.font(T.FONT_SMALL_SIZE, bold=True),
            padx=12,
            pady=4,
        ).pack(side="left")
        tk.Button(
            left_inventory_button_row,
            text="Delete",
            command=self._delete_selected_inventory_coa_entry,
            bg=T.STATUS_ERROR,
            fg=T.TEXT_ON_DARK,
            relief="flat",
            font=T.font(T.FONT_SMALL_SIZE, bold=True),
            padx=12,
            pady=4,
        ).pack(side="left", padx=(8, 0))

        right_inventory = tk.Frame(inventory_frame, bg=T.BG_CARD)
        right_inventory.grid(row=0, column=1, sticky="nsew")
        right_inventory.grid_columnconfigure(1, weight=1)
        tk.Label(
            right_inventory,
            text="COA Code:",
            bg=T.BG_CARD,
            fg=T.TEXT_ON_LIGHT,
            font=T.font(T.FONT_BODY_SIZE),
        ).grid(row=0, column=0, sticky="w")
        tk.Entry(
            right_inventory,
            textvariable=self.inventory_coa_code_var,
            font=T.font(T.FONT_BODY_SIZE),
            bg=T.BG_INPUT,
            relief="solid",
            bd=1,
        ).grid(row=0, column=1, sticky="ew", padx=(8, 0))

        tk.Label(
            right_inventory,
            text="Label / Hint:",
            bg=T.BG_CARD,
            fg=T.TEXT_ON_LIGHT,
            font=T.font(T.FONT_BODY_SIZE),
        ).grid(row=1, column=0, sticky="w", pady=(8, 0))
        tk.Entry(
            right_inventory,
            textvariable=self.inventory_coa_label_var,
            font=T.font(T.FONT_BODY_SIZE),
            bg=T.BG_INPUT,
            relief="solid",
            bd=1,
        ).grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=(8, 0))

        tk.Label(
            right_inventory,
            text="Preview:",
            bg=T.BG_CARD,
            fg=T.TEXT_ON_LIGHT,
            font=T.font(T.FONT_BODY_SIZE),
        ).grid(row=2, column=0, sticky="nw", pady=(8, 0))
        tk.Label(
            right_inventory,
            textvariable=self.inventory_coa_preview_var,
            bg=T.BG_CARD,
            fg=T.TEXT_MUTED,
            font=T.font(T.FONT_SMALL_SIZE, bold=True),
            justify="left",
            wraplength=380,
        ).grid(row=2, column=1, sticky="w", padx=(8, 0), pady=(8, 0))

        tk.Button(
            right_inventory,
            text="Save Entry",
            command=self._save_inventory_coa_entry,
            bg=T.BRAND_SECONDARY,
            fg=T.TEXT_ON_DARK,
            relief="flat",
            font=T.font(T.FONT_SMALL_SIZE, bold=True),
            padx=14,
            pady=4,
        ).grid(row=3, column=1, sticky="w", pady=(12, 0), padx=(8, 0))

        tk.Frame(inner, bg=T.BORDER_LIGHT, height=1).grid(
            row=8, column=0, columnspan=4, sticky="ew", pady=12
        )

        tk.Label(
            inner,
            text="Repair Extra Accounts",
            bg=T.BG_CARD,
            fg=T.BRAND_PRIMARY,
            font=T.font(12, bold=True),
        ).grid(row=9, column=0, columnspan=4, sticky="w", pady=(0, 8))

        tk.Label(
            inner,
            text="Daftar akun tambahan untuk repair jurnal dashboard. Kandidat dialog repair akan menggabungkan akun kategori item dengan daftar global ini.",
            bg=T.BG_CARD,
            fg=T.TEXT_MUTED,
            font=T.font(T.FONT_SMALL_SIZE),
            justify="left",
            wraplength=760,
        ).grid(row=10, column=0, columnspan=4, sticky="w", pady=(0, 8))

        repair_account_frame = tk.Frame(inner, bg=T.BG_CARD)
        repair_account_frame.grid(row=11, column=0, columnspan=4, sticky="ew", pady=(0, 4))
        repair_account_frame.grid_columnconfigure(1, weight=1)

        left_repair = tk.Frame(repair_account_frame, bg=T.BG_CARD)
        left_repair.grid(row=0, column=0, sticky="nsw", padx=(0, 16))
        tk.Label(
            left_repair,
            text="Saved Accounts",
            bg=T.BG_CARD,
            fg=T.TEXT_ON_LIGHT,
            font=T.font(T.FONT_BODY_SIZE, bold=True),
        ).pack(anchor="w")
        self.repair_account_listbox = tk.Listbox(
            left_repair,
            height=5,
            width=42,
            exportselection=False,
            font=T.font(T.FONT_SMALL_SIZE),
        )
        self.repair_account_listbox.pack(fill="both", expand=True, pady=(6, 0))
        self.repair_account_listbox.bind("<<ListboxSelect>>", self._on_repair_account_selected)

        left_repair_button_row = tk.Frame(left_repair, bg=T.BG_CARD)
        left_repair_button_row.pack(fill="x", pady=(8, 0))
        tk.Button(
            left_repair_button_row,
            text="Add New",
            command=self._start_new_repair_account_entry,
            bg=T.BRAND_PRIMARY,
            fg=T.TEXT_ON_DARK,
            relief="flat",
            font=T.font(T.FONT_SMALL_SIZE, bold=True),
            padx=12,
            pady=4,
        ).pack(side="left")
        tk.Button(
            left_repair_button_row,
            text="Delete",
            command=self._delete_selected_repair_account_entry,
            bg=T.STATUS_ERROR,
            fg=T.TEXT_ON_DARK,
            relief="flat",
            font=T.font(T.FONT_SMALL_SIZE, bold=True),
            padx=12,
            pady=4,
        ).pack(side="left", padx=(8, 0))

        right_repair = tk.Frame(repair_account_frame, bg=T.BG_CARD)
        right_repair.grid(row=0, column=1, sticky="nsew")
        right_repair.grid_columnconfigure(1, weight=1)
        tk.Label(
            right_repair,
            text="COA Code:",
            bg=T.BG_CARD,
            fg=T.TEXT_ON_LIGHT,
            font=T.font(T.FONT_BODY_SIZE),
        ).grid(row=0, column=0, sticky="w")
        tk.Entry(
            right_repair,
            textvariable=self.repair_account_code_var,
            font=T.font(T.FONT_BODY_SIZE),
            bg=T.BG_INPUT,
            relief="solid",
            bd=1,
        ).grid(row=0, column=1, sticky="ew", padx=(8, 0))

        tk.Label(
            right_repair,
            text="Label / Hint:",
            bg=T.BG_CARD,
            fg=T.TEXT_ON_LIGHT,
            font=T.font(T.FONT_BODY_SIZE),
        ).grid(row=1, column=0, sticky="w", pady=(8, 0))
        tk.Entry(
            right_repair,
            textvariable=self.repair_account_label_var,
            font=T.font(T.FONT_BODY_SIZE),
            bg=T.BG_INPUT,
            relief="solid",
            bd=1,
        ).grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=(8, 0))

        tk.Label(
            right_repair,
            text="Preview:",
            bg=T.BG_CARD,
            fg=T.TEXT_ON_LIGHT,
            font=T.font(T.FONT_BODY_SIZE),
        ).grid(row=2, column=0, sticky="nw", pady=(8, 0))
        tk.Label(
            right_repair,
            textvariable=self.repair_account_preview_var,
            bg=T.BG_CARD,
            fg=T.TEXT_MUTED,
            font=T.font(T.FONT_SMALL_SIZE, bold=True),
            justify="left",
            wraplength=380,
        ).grid(row=2, column=1, sticky="w", padx=(8, 0), pady=(8, 0))

        tk.Button(
            right_repair,
            text="Save Entry",
            command=self._save_repair_account_entry,
            bg=T.BRAND_SECONDARY,
            fg=T.TEXT_ON_DARK,
            relief="flat",
            font=T.font(T.FONT_SMALL_SIZE, bold=True),
            padx=14,
            pady=4,
        ).grid(row=3, column=1, sticky="w", pady=(12, 0), padx=(8, 0))

        tk.Frame(inner, bg=T.BORDER_LIGHT, height=1).grid(
            row=12, column=0, columnspan=4, sticky="ew", pady=12
        )

        tk.Label(
            inner,
            text="Default Paths",
            bg=T.BG_CARD,
            fg=T.BRAND_PRIMARY,
            font=T.font(12, bold=True),
        ).grid(row=13, column=0, columnspan=4, sticky="w", pady=(0, 8))

        tk.Label(
            inner,
            text="Output Folder:",
            bg=T.BG_CARD,
            fg=T.TEXT_ON_LIGHT,
            font=T.font(T.FONT_BODY_SIZE),
        ).grid(row=14, column=0, sticky="w")
        tk.Entry(
            inner,
            textvariable=self.output_dir_var,
            font=T.font(T.FONT_BODY_SIZE),
            width=50,
            bg=T.BG_INPUT,
            relief="solid",
            bd=1,
        ).grid(row=14, column=1, columnspan=2, sticky="ew", padx=(8, 8))
        tk.Button(
            inner,
            text="Browse...",
            command=self._browse_output,
            bg=T.BG_CARD,
            fg=T.TEXT_ON_LIGHT,
            relief="solid",
            bd=1,
            font=T.font(T.FONT_SMALL_SIZE),
        ).grid(row=14, column=3)

        tk.Label(
            inner,
            text="Input Folder:",
            bg=T.BG_CARD,
            fg=T.TEXT_ON_LIGHT,
            font=T.font(T.FONT_BODY_SIZE),
        ).grid(row=15, column=0, sticky="w", pady=(8, 0))
        tk.Entry(
            inner,
            textvariable=self.input_dir_var,
            font=T.font(T.FONT_BODY_SIZE),
            width=50,
            bg=T.BG_INPUT,
            relief="solid",
            bd=1,
        ).grid(row=15, column=1, columnspan=2, sticky="ew", padx=(8, 8), pady=(8, 0))
        tk.Button(
            inner,
            text="Browse...",
            command=self._browse_input,
            bg=T.BG_CARD,
            fg=T.TEXT_ON_LIGHT,
            relief="solid",
            bd=1,
            font=T.font(T.FONT_SMALL_SIZE),
        ).grid(row=15, column=3, pady=(8, 0))

        tk.Frame(inner, bg=T.BORDER_LIGHT, height=1).grid(
            row=16, column=0, columnspan=4, sticky="ew", pady=12
        )

        tk.Label(
            inner,
            text="Application Update",
            bg=T.BG_CARD,
            fg=T.BRAND_PRIMARY,
            font=T.font(12, bold=True),
        ).grid(row=17, column=0, columnspan=4, sticky="w", pady=(0, 8))

        tk.Label(
            inner,
            text="Current Version:",
            bg=T.BG_CARD,
            fg=T.TEXT_ON_LIGHT,
            font=T.font(T.FONT_BODY_SIZE),
        ).grid(row=18, column=0, sticky="w")
        tk.Label(
            inner,
            textvariable=self.current_version_var,
            bg=T.BG_CARD,
            fg=T.TEXT_ON_LIGHT,
            font=T.font(T.FONT_BODY_SIZE, bold=True),
        ).grid(row=18, column=1, sticky="w", padx=(8, 0))

        tk.Label(
            inner,
            text="Latest Version:",
            bg=T.BG_CARD,
            fg=T.TEXT_ON_LIGHT,
            font=T.font(T.FONT_BODY_SIZE),
        ).grid(row=19, column=0, sticky="w", pady=(8, 0))
        tk.Label(
            inner,
            textvariable=self.latest_version_var,
            bg=T.BG_CARD,
            fg=T.TEXT_ON_LIGHT,
            font=T.font(T.FONT_BODY_SIZE, bold=True),
        ).grid(row=19, column=1, sticky="w", padx=(8, 0), pady=(8, 0))

        tk.Label(
            inner,
            text="Last Checked:",
            bg=T.BG_CARD,
            fg=T.TEXT_ON_LIGHT,
            font=T.font(T.FONT_BODY_SIZE),
        ).grid(row=20, column=0, sticky="w", pady=(8, 0))
        tk.Label(
            inner,
            textvariable=self.last_checked_var,
            bg=T.BG_CARD,
            fg=T.TEXT_ON_LIGHT,
            font=T.font(T.FONT_BODY_SIZE),
        ).grid(row=20, column=1, sticky="w", padx=(8, 0), pady=(8, 0))

        tk.Label(
            inner,
            text="Status:",
            bg=T.BG_CARD,
            fg=T.TEXT_ON_LIGHT,
            font=T.font(T.FONT_BODY_SIZE),
        ).grid(row=21, column=0, sticky="nw", pady=(8, 0))
        self.update_status_label = tk.Label(
            inner,
            textvariable=self.update_status_var,
            bg=T.BG_CARD,
            fg=T.TEXT_MUTED,
            font=T.font(T.FONT_BODY_SIZE),
            justify="left",
            wraplength=680,
        )
        self.update_status_label.grid(row=21, column=1, columnspan=3, sticky="w", padx=(8, 0), pady=(8, 0))

        self.auto_check_updates_check = tk.Checkbutton(
            inner,
            text="Cek update otomatis saat startup",
            variable=self.auto_check_updates_var,
            bg=T.BG_CARD,
            fg=T.TEXT_ON_LIGHT,
            selectcolor=T.BG_CARD,
            activebackground=T.BG_CARD,
            activeforeground=T.TEXT_ON_LIGHT,
            font=T.font(T.FONT_BODY_SIZE),
        )
        self.auto_check_updates_check.grid(row=22, column=0, columnspan=2, sticky="w", pady=(12, 0))

        self.auto_download_updates_check = tk.Checkbutton(
            inner,
            text="Download update otomatis jika versi baru ditemukan",
            variable=self.auto_download_updates_var,
            bg=T.BG_CARD,
            fg=T.TEXT_ON_LIGHT,
            selectcolor=T.BG_CARD,
            activebackground=T.BG_CARD,
            activeforeground=T.TEXT_ON_LIGHT,
            font=T.font(T.FONT_BODY_SIZE),
        )
        self.auto_download_updates_check.grid(row=23, column=0, columnspan=3, sticky="w", pady=(8, 0))

        button_row = tk.Frame(inner, bg=T.BG_CARD)
        button_row.grid(row=24, column=0, columnspan=4, sticky="w", pady=(14, 0))

        self.check_update_button = tk.Button(
            button_row,
            text="Check for Updates",
            bg=T.BRAND_PRIMARY,
            fg=T.TEXT_ON_DARK,
            font=T.font(T.FONT_BODY_SIZE, bold=True),
            activebackground=T.BRAND_PRIMARY_DARK,
            activeforeground=T.TEXT_ON_DARK,
            relief="flat",
            padx=16,
            pady=6,
            command=self._start_update_check,
        )
        self.check_update_button.pack(side="left")

        self.download_update_button = tk.Button(
            button_row,
            text="Download Update",
            bg=T.BRAND_SECONDARY,
            fg=T.TEXT_ON_DARK,
            font=T.font(T.FONT_BODY_SIZE, bold=True),
            activebackground=T.BRAND_ACCENT,
            activeforeground=T.TEXT_ON_DARK,
            relief="flat",
            padx=16,
            pady=6,
            command=self._start_download_update,
        )
        self.download_update_button.pack(side="left", padx=(8, 0))

        self.install_update_button = tk.Button(
            button_row,
            text="Install Update & Restart",
            bg=T.STATUS_SUCCESS,
            fg=T.TEXT_ON_DARK,
            font=T.font(T.FONT_BODY_SIZE, bold=True),
            activebackground=T.STATUS_SUCCESS,
            activeforeground=T.TEXT_ON_DARK,
            relief="flat",
            padx=16,
            pady=6,
            command=self._install_update_and_restart,
        )
        self.install_update_button.pack(side="left", padx=(8, 0))

        self.release_notes_button = tk.Button(
            button_row,
            text="Release Notes",
            bg=T.BG_CARD,
            fg=T.TEXT_ON_LIGHT,
            font=T.font(T.FONT_BODY_SIZE),
            relief="solid",
            bd=1,
            padx=16,
            pady=6,
            command=self._open_release_notes,
        )
        self.release_notes_button.pack(side="left", padx=(8, 0))

        inner.columnconfigure(1, weight=1)
        inner.columnconfigure(2, weight=1)

        btn_frame = tk.Frame(main, bg=T.BG_MAIN)
        btn_frame.pack(fill="x", padx=24, pady=(0, 16))
        tk.Button(
            btn_frame,
            text="Save Settings",
            bg=T.BRAND_PRIMARY,
            fg=T.TEXT_ON_DARK,
            font=T.font(T.FONT_BODY_SIZE, bold=True),
            activebackground=T.BRAND_PRIMARY_DARK,
            activeforeground=T.TEXT_ON_DARK,
            relief="flat",
            padx=20,
            pady=6,
            command=self._save,
        ).pack(side="left")

        self.save_status = tk.Label(
            btn_frame,
            text="",
            bg=T.BG_MAIN,
            fg=T.STATUS_SUCCESS,
            font=T.font(T.FONT_SMALL_SIZE),
        )
        self.save_status.pack(side="left", padx=(12, 0))

    def _browse_output(self) -> None:
        path = filedialog.askdirectory(title="Pilih folder output", mustexist=True)
        if path:
            self.output_dir_var.set(path)

    def _browse_input(self) -> None:
        path = filedialog.askdirectory(title="Pilih folder input", mustexist=True)
        if path:
            self.input_dir_var.set(path)

    def _refresh_database_profile_preview(self, *_args) -> None:
        self.profile_preview_var.set(
            build_database_profile_preview(
                self.profile_alias_var.get(),
                self.profile_value_var.get(),
                self.profile_note_var.get(),
            )
        )

    def _refresh_inventory_coa_preview(self, *_args) -> None:
        self.inventory_coa_preview_var.set(
            build_inventory_coa_preview(
                self.inventory_coa_code_var.get(),
                self.inventory_coa_label_var.get(),
            )
        )

    def _refresh_repair_account_preview(self, *_args) -> None:
        self.repair_account_preview_var.set(
            build_repair_account_preview(
                self.repair_account_code_var.get(),
                self.repair_account_label_var.get(),
            )
        )

    def _refresh_database_profile_ui(self) -> None:
        self.database_profile_listbox.delete(0, "end")
        for profile in self._database_profiles:
            self.database_profile_listbox.insert("end", render_database_profile_label(profile))

        options = build_global_default_database_options(self._database_profiles)
        self._global_default_label_by_id, self._global_default_id_by_label = build_option_maps(options)
        labels = [label for _profile_id, label in options]
        current_default_label = normalize_database_profile_text(self.default_database_profile_var.get())
        selected_default_id = self._global_default_id_by_label.get(current_default_label, "")
        if not selected_default_id:
            selected_default_id = normalize_database_profile_text(self._settings.default_database_profile_id)
        if selected_default_id not in self._global_default_label_by_id:
            selected_default_id = ""
        self.default_database_profile_combo.configure(values=labels)
        self.default_database_profile_var.set(self._global_default_label_by_id.get(selected_default_id, labels[0]))

    def _refresh_inventory_coa_ui(self) -> None:
        self.inventory_coa_listbox.delete(0, "end")
        for entry in self._inventory_coa_entries:
            self.inventory_coa_listbox.insert("end", build_inventory_coa_preview(entry.coa_code, entry.label))

    def _refresh_repair_account_ui(self) -> None:
        self.repair_account_listbox.delete(0, "end")
        for entry in self._repair_account_entries:
            self.repair_account_listbox.insert("end", build_repair_account_preview(entry.coa_code, entry.label))

    def _start_new_inventory_coa_entry(self) -> None:
        self._selected_inventory_coa_entry_id = ""
        self.inventory_coa_listbox.selection_clear(0, "end")
        self.inventory_coa_code_var.set("")
        self.inventory_coa_label_var.set("")

    def _start_new_repair_account_entry(self) -> None:
        self._selected_repair_account_entry_id = ""
        self.repair_account_listbox.selection_clear(0, "end")
        self.repair_account_code_var.set("")
        self.repair_account_label_var.set("")

    def _on_inventory_coa_selected(self, _event=None) -> None:
        selection = self.inventory_coa_listbox.curselection()
        if not selection:
            return
        index = int(selection[0])
        if index < 0 or index >= len(self._inventory_coa_entries):
            return
        entry = self._inventory_coa_entries[index]
        self._selected_inventory_coa_entry_id = entry.entry_id
        self.inventory_coa_code_var.set(entry.coa_code)
        self.inventory_coa_label_var.set(entry.label)

    def _on_repair_account_selected(self, _event=None) -> None:
        selection = self.repair_account_listbox.curselection()
        if not selection:
            return
        index = int(selection[0])
        if index < 0 or index >= len(self._repair_account_entries):
            return
        entry = self._repair_account_entries[index]
        self._selected_repair_account_entry_id = entry.entry_id
        self.repair_account_code_var.set(entry.coa_code)
        self.repair_account_label_var.set(entry.label)

    def _save_inventory_coa_entry(self) -> None:
        coa_code = normalize_database_profile_text(self.inventory_coa_code_var.get()).upper()
        label = normalize_database_profile_text(self.inventory_coa_label_var.get())
        if not coa_code:
            messagebox.showwarning("Inventory COA Dashboard", "COA Code wajib diisi.")
            return
        for entry in self._inventory_coa_entries:
            if entry.entry_id == self._selected_inventory_coa_entry_id:
                continue
            if normalize_database_profile_text(entry.coa_code).upper() == coa_code:
                messagebox.showwarning("Inventory COA Dashboard", "COA Code sudah ada di daftar.")
                return
        entry_id = self._selected_inventory_coa_entry_id or f"inv_coa_{len(self._inventory_coa_entries) + 1}"
        updated_entry = InventoryCoaEntry(
            entry_id=entry_id,
            coa_code=coa_code,
            label=label,
        )
        replaced = False
        for index, entry in enumerate(self._inventory_coa_entries):
            if entry.entry_id == entry_id:
                self._inventory_coa_entries[index] = updated_entry
                replaced = True
                break
        if not replaced:
            self._inventory_coa_entries.append(updated_entry)
        self._inventory_coa_entries.sort(key=lambda item: item.coa_code)
        self._selected_inventory_coa_entry_id = entry_id
        self._refresh_inventory_coa_ui()
        for index, entry in enumerate(self._inventory_coa_entries):
            if entry.entry_id == entry_id:
                self.inventory_coa_listbox.selection_clear(0, "end")
                self.inventory_coa_listbox.selection_set(index)
                self.inventory_coa_listbox.activate(index)
                break

    def _save_repair_account_entry(self) -> None:
        coa_code = normalize_database_profile_text(self.repair_account_code_var.get()).upper()
        label = normalize_database_profile_text(self.repair_account_label_var.get())
        if not coa_code:
            messagebox.showwarning("Repair Extra Accounts", "COA Code wajib diisi.")
            return
        for entry in self._repair_account_entries:
            if entry.entry_id == self._selected_repair_account_entry_id:
                continue
            if normalize_database_profile_text(entry.coa_code).upper() == coa_code:
                messagebox.showwarning("Repair Extra Accounts", "COA Code sudah ada di daftar.")
                return
        entry_id = self._selected_repair_account_entry_id or f"repair_acc_{len(self._repair_account_entries) + 1}"
        updated_entry = RepairAccountEntry(
            entry_id=entry_id,
            coa_code=coa_code,
            label=label,
        )
        replaced = False
        for index, entry in enumerate(self._repair_account_entries):
            if entry.entry_id == entry_id:
                self._repair_account_entries[index] = updated_entry
                replaced = True
                break
        if not replaced:
            self._repair_account_entries.append(updated_entry)
        self._repair_account_entries.sort(key=lambda item: item.coa_code)
        self._selected_repair_account_entry_id = entry_id
        self._refresh_repair_account_ui()
        for index, entry in enumerate(self._repair_account_entries):
            if entry.entry_id == entry_id:
                self.repair_account_listbox.selection_clear(0, "end")
                self.repair_account_listbox.selection_set(index)
                self.repair_account_listbox.activate(index)
                break

    def _delete_selected_inventory_coa_entry(self) -> None:
        if not self._selected_inventory_coa_entry_id:
            messagebox.showwarning("Inventory COA Dashboard", "Pilih entry COA yang ingin dihapus.")
            return
        entry_id = self._selected_inventory_coa_entry_id
        self._inventory_coa_entries = [
            entry for entry in self._inventory_coa_entries if entry.entry_id != entry_id
        ]
        self._start_new_inventory_coa_entry()
        self._refresh_inventory_coa_ui()

    def _delete_selected_repair_account_entry(self) -> None:
        if not self._selected_repair_account_entry_id:
            messagebox.showwarning("Repair Extra Accounts", "Pilih entry akun yang ingin dihapus.")
            return
        entry_id = self._selected_repair_account_entry_id
        self._repair_account_entries = [
            entry for entry in self._repair_account_entries if entry.entry_id != entry_id
        ]
        self._start_new_repair_account_entry()
        self._refresh_repair_account_ui()

    def _start_new_database_profile(self) -> None:
        self._selected_database_profile_id = ""
        self.database_profile_listbox.selection_clear(0, "end")
        self.profile_value_var.set("")
        self.profile_alias_var.set("")
        self.profile_note_var.set("")

    def _on_database_profile_selected(self, _event=None) -> None:
        selection = self.database_profile_listbox.curselection()
        if not selection:
            return
        index = int(selection[0])
        if index < 0 or index >= len(self._database_profiles):
            return
        profile = self._database_profiles[index]
        self._selected_database_profile_id = profile.profile_id
        self.profile_value_var.set(profile.database_value)
        self.profile_alias_var.set(profile.alias)
        self.profile_note_var.set(profile.note)

    def _save_database_profile_edit(self) -> None:
        database_value = normalize_database_profile_text(self.profile_value_var.get())
        alias = normalize_database_profile_text(self.profile_alias_var.get())
        note = normalize_database_profile_text(self.profile_note_var.get())
        if not database_value:
            messagebox.showwarning("Database Profiles", "Database value wajib diisi.")
            return
        for profile in self._database_profiles:
            if profile.profile_id == self._selected_database_profile_id:
                continue
            if normalize_database_profile_text(profile.database_value).lower() == database_value.lower():
                messagebox.showwarning("Database Profiles", "Database value sudah ada di daftar.")
                return

        profile_id = self._selected_database_profile_id
        if not profile_id:
            profile_id = build_new_database_profile_id(
                self._database_profiles,
                preferred_source=alias or database_value,
            )

        updated_profile = DatabaseProfile(
            profile_id=profile_id,
            database_value=database_value,
            alias=alias,
            note=note,
        )

        replaced = False
        for index, profile in enumerate(self._database_profiles):
            if profile.profile_id == profile_id:
                self._database_profiles[index] = updated_profile
                replaced = True
                break
        if not replaced:
            self._database_profiles.append(updated_profile)

        self._selected_database_profile_id = profile_id
        self._refresh_database_profile_ui()
        for index, profile in enumerate(self._database_profiles):
            if profile.profile_id == profile_id:
                self.database_profile_listbox.selection_clear(0, "end")
                self.database_profile_listbox.selection_set(index)
                self.database_profile_listbox.activate(index)
                break

    def _delete_selected_database_profile(self) -> None:
        if not self._selected_database_profile_id:
            messagebox.showwarning("Database Profiles", "Pilih profile yang ingin dihapus.")
            return
        profile_id = self._selected_database_profile_id
        self._database_profiles = [
            profile for profile in self._database_profiles if profile.profile_id != profile_id
        ]
        if normalize_database_profile_text(self._settings.default_database_profile_id) == profile_id:
            self._settings.default_database_profile_id = ""
        self._start_new_database_profile()
        self._refresh_database_profile_ui()

    def _save(self) -> None:
        self._settings.database_profiles = list(self._database_profiles)
        self._settings.inventory_coa_entries = list(self._inventory_coa_entries)
        self._settings.repair_account_entries = list(self._repair_account_entries)
        self._settings.default_database_profile_id = self._global_default_id_by_label.get(
            normalize_database_profile_text(self.default_database_profile_var.get()),
            "",
        )
        self._settings.db_override = ""
        self._settings.default_output_dir = self.output_dir_var.get().strip()
        self._settings.default_input_dir = self.input_dir_var.get().strip()
        self._settings.auto_check_updates = bool(self.auto_check_updates_var.get())
        self._settings.auto_download_updates = bool(self.auto_download_updates_var.get())
        self._state_store.save(self._settings)
        self.save_status.configure(text="Saved!", fg=T.STATUS_SUCCESS)
        self.after(3000, lambda: self.save_status.configure(text=""))

    def _apply_update_view_state(self, phase: str, *, message: str = "", error_text: str = "") -> None:
        self._update_phase = phase
        view_state = build_update_panel_view_state(
            phase,
            latest_version=self.latest_version_var.get(),
            has_manifest=self._current_manifest is not None,
            has_download=self._cached_installer_path is not None,
            has_release_notes=bool(self._release_notes_url),
            error_text=error_text,
        )
        self.update_status_var.set(message or view_state.status_text)
        status_color = T.TEXT_MUTED
        if phase == "failed":
            status_color = T.STATUS_ERROR
        elif phase in {"ready", "no_update"}:
            status_color = T.STATUS_SUCCESS
        elif phase in {"available", "ignored"}:
            status_color = T.BRAND_PRIMARY
        self.update_status_label.configure(fg=status_color)
        self.check_update_button.configure(state="normal" if view_state.check_enabled else "disabled")
        self.download_update_button.configure(state="normal" if view_state.download_enabled else "disabled")
        self.install_update_button.configure(state="normal" if view_state.install_enabled else "disabled")
        self.release_notes_button.configure(state="normal" if view_state.release_notes_enabled else "disabled")
        self._status_callback(self.update_status_var.get())

    def _schedule_auto_update_check(self) -> None:
        if self._auto_check_after_id is not None:
            return
        if not self._settings.auto_check_updates:
            return
        if not should_auto_check(self._settings.last_update_check_utc):
            return
        self._auto_check_after_id = self.after(AUTO_UPDATE_CHECK_DELAY_MS, self._auto_check_for_updates)

    def _auto_check_for_updates(self) -> None:
        self._auto_check_after_id = None
        self._start_update_check(manual=False)

    def _start_update_check(self, manual: bool = True) -> None:
        if self._auto_check_after_id is not None:
            try:
                self.after_cancel(self._auto_check_after_id)
            except Exception:
                pass
            self._auto_check_after_id = None
        if self._update_thread is not None and self._update_thread.is_alive():
            return
        self._apply_update_view_state("checking")
        checked_at_utc = utc_now_iso()

        def worker() -> None:
            try:
                result = asyncio.run(
                    self._update_service.check_for_update(
                        ignored_version=self._settings.ignored_update_version,
                    )
                )
                self._update_queue.put({"type": "check_complete", "result": result, "manual": manual})
            except Exception as exc:
                self._update_queue.put(
                    {
                        "type": "check_failed",
                        "error": str(exc),
                        "manual": manual,
                        "checked_at_utc": checked_at_utc,
                    }
                )

        self._update_thread = threading.Thread(target=worker, daemon=True)
        self._update_thread.start()

    def _start_download_update(self) -> None:
        if self._current_manifest is None:
            messagebox.showwarning("Update", "Belum ada manifest update yang siap di-download.")
            return
        if self._update_thread is not None and self._update_thread.is_alive():
            return
        self._apply_update_view_state("downloading")
        manifest = self._current_manifest

        def worker() -> None:
            try:
                installer_path = asyncio.run(
                    self._update_service.download_update(
                        manifest,
                        progress_callback=lambda progress: self._update_queue.put(
                            {"type": "download_progress", "progress": progress}
                        ),
                    )
                )
                self._update_queue.put({"type": "download_complete", "installer_path": installer_path})
            except Exception as exc:
                self._update_queue.put({"type": "download_failed", "error": str(exc)})

        self._update_thread = threading.Thread(target=worker, daemon=True)
        self._update_thread.start()

    def _handle_update_check_complete(self, result: UpdateCheckResult) -> None:
        self._update_thread = None
        self._settings.last_update_check_utc = result.checked_at_utc
        self.last_checked_var.set(format_utc_timestamp(result.checked_at_utc))
        self._current_manifest = result.manifest
        self._cached_installer_path = result.cached_installer_path
        self._release_notes_url = ""

        if result.manifest is not None:
            self.latest_version_var.set(result.manifest.version)
            self._release_notes_url = result.manifest.release_notes_url
        else:
            self.latest_version_var.set("-")

        self._state_store.save(self._settings)

        if not result.is_update_available:
            self._apply_update_view_state("no_update", message=result.message)
            return
        if result.is_ignored:
            self._apply_update_view_state("ignored", message=result.message)
            return
        if result.cached_installer_path is not None:
            self._apply_update_view_state("ready", message=result.message)
            return

        self._apply_update_view_state("available", message=result.message)
        if self.auto_download_updates_var.get():
            self._start_download_update()

    def _install_update_and_restart(self) -> None:
        if self._current_manifest is None:
            messagebox.showwarning("Update", "Belum ada update yang siap diinstall.")
            return
        try:
            installer_path = self._cached_installer_path or self._update_service.prepare_install(self._current_manifest)
            self._cached_installer_path = installer_path
        except Exception as exc:
            messagebox.showerror("Update", str(exc))
            self._apply_update_view_state("failed", error_text=str(exc))
            return

        self._save()
        try:
            self._update_service.launch_installer(installer_path)
        except Exception as exc:
            messagebox.showerror("Update", str(exc))
            self._apply_update_view_state("failed", error_text=str(exc))
            return

        self._apply_update_view_state("ready", message="Installer update dibuka. Aplikasi akan ditutup untuk upgrade.")
        self.after(250, self._shutdown_callback)

    def _open_release_notes(self) -> None:
        if not self._release_notes_url:
            messagebox.showwarning("Update", "Release notes belum tersedia untuk update ini.")
            return
        webbrowser.open(self._release_notes_url)

    def on_show(self) -> None:
        if self._poll_active:
            return
        self._poll_active = True
        self._poll_update_queue()
        self._schedule_auto_update_check()

    def on_hide(self) -> None:
        self._poll_active = False
        if self._poll_after_id is not None:
            try:
                self.after_cancel(self._poll_after_id)
            except Exception:
                pass
            self._poll_after_id = None
        if self._auto_check_after_id is not None:
            try:
                self.after_cancel(self._auto_check_after_id)
            except Exception:
                pass
            self._auto_check_after_id = None

    def _poll_update_queue(self) -> None:
        if not self._poll_active:
            return
        while True:
            try:
                event = self._update_queue.get_nowait()
            except queue.Empty:
                break

            event_type = str(event.get("type") or "")
            if event_type == "check_complete":
                result = event.get("result")
                if isinstance(result, UpdateCheckResult):
                    self._handle_update_check_complete(result)
            elif event_type == "check_failed":
                self._update_thread = None
                error = str(event.get("error") or "Pengecekan update gagal.")
                checked_at_utc = str(event.get("checked_at_utc") or "").strip()
                if checked_at_utc:
                    self._settings.last_update_check_utc = checked_at_utc
                    self.last_checked_var.set(format_utc_timestamp(checked_at_utc))
                    self._state_store.save(self._settings)
                self._apply_update_view_state("failed", error_text=error)
            elif event_type == "download_progress":
                progress = event.get("progress")
                if isinstance(progress, UpdateDownloadProgress):
                    self._apply_update_view_state("downloading", message=format_download_progress(progress))
            elif event_type == "download_complete":
                self._update_thread = None
                installer_path = event.get("installer_path")
                if isinstance(installer_path, Path):
                    self._cached_installer_path = installer_path
                self._apply_update_view_state("ready")
            elif event_type == "download_failed":
                self._update_thread = None
                error = str(event.get("error") or "Download update gagal.")
                self._apply_update_view_state("failed", error_text=error)

        delay_ms = 33 if self._update_thread is not None and self._update_thread.is_alive() else 250
        self._poll_after_id = self.after(delay_ms, self._poll_update_queue)

    def _default_shutdown(self) -> None:
        toplevel = self._root or self.winfo_toplevel()
        if hasattr(toplevel, "destroy"):
            toplevel.destroy()
