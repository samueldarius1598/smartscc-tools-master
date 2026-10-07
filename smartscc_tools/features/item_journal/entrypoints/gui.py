"""Tkinter GUI for Item Journal uploader."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
import hashlib
import json
import logging
import os
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText
from typing import Any, Callable, Dict, Mapping, Optional, Sequence

from smartscc_tools.features.item_journal.config import BusinessUploadSettings, RuntimeSettings, build_runtime_settings, defaults_for_preset
from smartscc_tools.features.item_journal.entrypoints.common import build_copy_name_stem_from_summary, log_save_result
from smartscc_tools.features.item_journal.messaging import BusinessNarrator, stage_to_business_message
from smartscc_tools.features.item_journal.observability.audit import AuditSink
from smartscc_tools.features.item_journal.observability.perf import PerfReporter, PerfThresholds
from smartscc_tools.services.odoo.gateway import AsyncOdooJsonRpcClient, fetch_odoo_config
from smartscc_tools.services.odoo.master_cache import SessionMasterCache
from smartscc_tools.features.item_journal.runtime import RunControl, configure_logging, get_technical_logger
from smartscc_tools.features.item_journal.services.date_ops import LockDateServiceAsync
from smartscc_tools.features.item_journal.services.upload import ItemJournalServiceAsync
from smartscc_tools.features.item_journal.workbook import ItemJournalWorkbookRepo, SaveResult, summarize_unique_dates
from smartscc_tools.branding import apply_window_branding
from smartscc_tools.core import theme as T
from smartscc_tools.services.odoo.profiles import (
    FOLLOW_GLOBAL_PROFILE_ID,
    build_global_default_database_options,
    build_module_database_options,
    build_option_maps,
    default_database_profiles,
    ensure_database_profile,
    find_database_profile,
    normalize_module_database_profile_id,
    render_database_profile_label,
    resolve_database_selection,
)
from smartscc_tools.core.global_config import GlobalSettings
from smartscc_tools.widgets.collapsible_section import CollapsibleSection, build_compact_preview_text
from smartscc_tools.widgets.log_text import append_bounded_text_lines
from smartscc_tools.widgets.scrollable_frame import ScrollableFrame


AUTO_GROUP_BATCH_LIMIT = 2_147_483_647
DEFAULT_PRESET = "safe-fast"
DEFAULT_AUDIT_LEVEL = "item"
DEFAULT_PERF_PROFILE = "aggressive"
DEFAULT_PERF_SHEET = True
DEFAULT_MAX_CONCURRENCY = 8
SLOW_WAIT_PREVIEW_INTERVAL_MS = 120

SECTION_KEYS = (
    "workbook",
    "output",
    "connection",
    "grouping",
    "actions",
    "dashboard",
    "logs",
)

NEUTRAL_COLOR = "#666666"
WARNING_COLOR = "#a36a00"
SUCCESS_COLOR = "#1f7a1f"
ERROR_COLOR = "#b00020"

DEFAULT_BUSINESS_SETTINGS = defaults_for_preset(DEFAULT_PRESET)


def default_section_open() -> dict[str, bool]:
    return {
        "workbook": False,
        "output": False,
        "connection": False,
        "grouping": False,
        "actions": True,
        "dashboard": False,
        "logs": False,
    }


def _safe_int(value: Any, default: int, minimum: int = 0) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return max(minimum, parsed)


def default_output_dir(home_dir: Path | None = None) -> Path:
    home = Path(home_dir) if home_dir is not None else Path.home()
    for candidate in (home / "Documents", home / "Desktop", home):
        try:
            if candidate.exists() and candidate.is_dir():
                return candidate
        except OSError:
            continue
    return home


def normalize_output_dir(path_text: str, home_dir: Path | None = None) -> str:
    clean = str(path_text or "").strip()
    if clean:
        try:
            candidate = Path(clean).expanduser()
            if candidate.exists() and candidate.is_dir():
                return str(candidate)
        except (OSError, RuntimeError, ValueError):
            pass
    return str(default_output_dir(home_dir=home_dir))


def merge_section_open(value: Any) -> dict[str, bool]:
    merged = default_section_open()
    if isinstance(value, Mapping):
        for key in SECTION_KEYS:
            if key in value:
                merged[key] = bool(value[key])
    return merged


@dataclass
class GuiState:
    workbook_path: str = ""
    output_dir: str = field(default_factory=lambda: str(default_output_dir()))
    database_profile_id: str = FOLLOW_GLOBAL_PROFILE_ID
    grouping_mode: str = "auto"
    custom_limit: int = 150
    network_retry: int = DEFAULT_BUSINESS_SETTINGS.max_retry_for_network_errors
    network_retry_delay_ms: int = DEFAULT_BUSINESS_SETTINGS.wait_between_network_retries_ms
    journal_wait_timeout_ms: int = DEFAULT_BUSINESS_SETTINGS.max_wait_time_for_journal_creation_ms
    dry_run: bool = False
    advanced_open: bool = False
    section_open: dict[str, bool] = field(default_factory=default_section_open)


def gui_state_from_mapping(payload: Mapping[str, Any]) -> GuiState:
    database_profile_id = normalize_module_database_profile_id(payload.get("database_profile_id"))
    legacy_db_override = str(payload.get("db_override") or "").strip()
    if database_profile_id == FOLLOW_GLOBAL_PROFILE_ID and legacy_db_override:
        database_profile_id = legacy_db_override
    return GuiState(
        workbook_path=str(payload.get("workbook_path") or ""),
        output_dir=normalize_output_dir(str(payload.get("output_dir") or "")),
        database_profile_id=database_profile_id,
        grouping_mode="custom" if str(payload.get("grouping_mode") or "").strip().lower() == "custom" else "auto",
        custom_limit=_safe_int(payload.get("custom_limit"), 150, minimum=1),
        network_retry=_safe_int(
            payload.get("network_retry"),
            DEFAULT_BUSINESS_SETTINGS.max_retry_for_network_errors,
            minimum=0,
        ),
        network_retry_delay_ms=_safe_int(
            payload.get("network_retry_delay_ms"),
            DEFAULT_BUSINESS_SETTINGS.wait_between_network_retries_ms,
            minimum=0,
        ),
        journal_wait_timeout_ms=_safe_int(
            payload.get("journal_wait_timeout_ms"),
            DEFAULT_BUSINESS_SETTINGS.max_wait_time_for_journal_creation_ms,
            minimum=0,
        ),
        dry_run=bool(payload.get("dry_run", False)),
        advanced_open=bool(payload.get("advanced_open", False)),
        section_open=merge_section_open(payload.get("section_open")),
    )


def gui_state_to_mapping(state: GuiState) -> dict[str, Any]:
    payload = asdict(state)
    payload["output_dir"] = normalize_output_dir(payload.get("output_dir", ""))
    payload.pop("db_override", None)
    payload["section_open"] = merge_section_open(payload.get("section_open"))
    return payload


class PersistentState:
    def __init__(self, path: Path | None = None, app_name: str = "item_journal") -> None:
        if path is None:
            base_dir = Path(os.getenv("APPDATA") or (Path.home() / ".config")) / app_name
            path = base_dir / "gui_state.json"
        self.path = Path(path)

    def load(self) -> GuiState:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return GuiState()
        if not isinstance(payload, Mapping):
            return GuiState()
        return gui_state_from_mapping(payload)

    def save(self, state: GuiState) -> None:
        payload = gui_state_to_mapping(state)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


@dataclass(frozen=True)
class LockDashboardState:
    lock_date_text: str
    journal_dates_text: str
    status_text: str
    status_color: str


@dataclass(frozen=True)
class ProgressSnapshot:
    value: float
    percent_text: str
    count_text: str


@dataclass(frozen=True)
class ActionProgressLayout:
    show_compact_progress: bool
    show_buttons: bool
    show_detail: bool


@dataclass(frozen=True)
class SlowWaitPreviewState:
    fingerprint: str
    items: tuple[str, ...]
    company_name: str
    src: str
    dest: str
    item_count: int
    current_index: int = 0
    shown_once_count: int = 0
    loop_count: int = 0


def build_lock_dashboard_state(
    unique_dates: list[date],
    lock_date: date | None,
    error_text: str = "",
) -> LockDashboardState:
    ordered_dates = sorted({item for item in unique_dates})
    journal_dates_text = ", ".join(item.strftime("%Y-%m-%d") for item in ordered_dates) or "-"
    lock_date_text = lock_date.strftime("%Y-%m-%d") if lock_date is not None else "(kosong)"
    clean_error = str(error_text or "").strip()

    if clean_error:
        return LockDashboardState(
            lock_date_text=lock_date_text,
            journal_dates_text=journal_dates_text,
            status_text=clean_error,
            status_color=WARNING_COLOR,
        )
    if not ordered_dates:
        return LockDashboardState(
            lock_date_text=lock_date_text,
            journal_dates_text=journal_dates_text,
            status_text="Tidak ada tanggal jurnal aktif di workbook.",
            status_color=WARNING_COLOR,
        )
    if lock_date is None:
        return LockDashboardState(
            lock_date_text=lock_date_text,
            journal_dates_text=journal_dates_text,
            status_text="Lock date kosong / gagal dibaca dari Odoo.",
            status_color=WARNING_COLOR,
        )
    if any(item <= lock_date for item in ordered_dates):
        return LockDashboardState(
            lock_date_text=lock_date_text,
            journal_dates_text=journal_dates_text,
            status_text="Minta Accounting Buka Lock Period Terlebih Dahulu",
            status_color=ERROR_COLOR,
        )
    return LockDashboardState(
        lock_date_text=lock_date_text,
        journal_dates_text=journal_dates_text,
        status_text="Aman / Transfer Bisa Dijalankan",
        status_color=SUCCESS_COLOR,
    )


def build_progress_snapshot(current: int, total: int) -> ProgressSnapshot:
    if total <= 0:
        return ProgressSnapshot(value=0.0, percent_text="0%", count_text="0/0 item")
    bounded_current = max(0, min(current, total))
    percent = max(0.0, min(100.0, (float(bounded_current) / float(total)) * 100.0))
    return ProgressSnapshot(
        value=percent,
        percent_text=f"{int(round(percent))}%",
        count_text=f"{bounded_current}/{total} item",
    )


def build_action_progress_layout(is_open: bool) -> ActionProgressLayout:
    return ActionProgressLayout(
        show_compact_progress=True,
        show_buttons=bool(is_open),
        show_detail=bool(is_open),
    )


def build_workbook_section_summary(path_text: str) -> str:
    clean = str(path_text or "").strip()
    if not clean:
        return "Belum pilih workbook"
    try:
        candidate = Path(clean)
        name = candidate.name.strip()
        parent_name = candidate.parent.name.strip()
    except (OSError, RuntimeError, ValueError):
        return clean
    if parent_name and name:
        return f"{parent_name} - {name}"
    return name or clean


def build_output_dir_section_summary(path_text: str) -> str:
    clean = str(path_text or "").strip()
    if not clean:
        return "Belum pilih output folder"
    try:
        candidate = Path(clean)
        name = candidate.name.strip()
    except (OSError, RuntimeError, ValueError):
        return clean
    return name or clean


def build_connection_mode_section_summary(database_label: str, dry_run: bool) -> str:
    database_name = str(database_label or "").strip() or "Use GAS Default"
    mode_text = "Dry Run" if bool(dry_run) else "No Dry Run"
    return f"{database_name} - {mode_text}"


def build_grouping_section_summary(
    grouping_mode: str,
    custom_limit: int | str,
    network_retry: int | str,
    network_retry_delay_ms: int | str,
    journal_wait_timeout_ms: int | str,
) -> str:
    mode = str(grouping_mode or "").strip().lower()
    if mode == "custom":
        mode_text = f"Custom Limit {_safe_int(custom_limit, 150, minimum=1)}"
    else:
        mode_text = "Sesuai COA (Auto)"

    changes: list[str] = []
    retry_value = _safe_int(network_retry, DEFAULT_BUSINESS_SETTINGS.max_retry_for_network_errors, minimum=0)
    if retry_value != DEFAULT_BUSINESS_SETTINGS.max_retry_for_network_errors:
        changes.append(f"Retry {retry_value}x")

    delay_value = _safe_int(
        network_retry_delay_ms,
        DEFAULT_BUSINESS_SETTINGS.wait_between_network_retries_ms,
        minimum=0,
    )
    if delay_value != DEFAULT_BUSINESS_SETTINGS.wait_between_network_retries_ms:
        changes.append(f"Delay {delay_value} ms")

    wait_value = _safe_int(
        journal_wait_timeout_ms,
        DEFAULT_BUSINESS_SETTINGS.max_wait_time_for_journal_creation_ms,
        minimum=0,
    )
    if wait_value != DEFAULT_BUSINESS_SETTINGS.max_wait_time_for_journal_creation_ms:
        changes.append(f"Journal Wait {wait_value} ms")

    advanced_text = ", ".join(changes) if changes else "No Advanced Setting"
    return f"{mode_text} - {advanced_text}"


def build_lock_dashboard_section_summary(status_text: str) -> str:
    clean = str(status_text or "").strip()
    return clean or "Belum diperiksa"


def _normalize_preview_items(items: Any) -> list[str]:
    if isinstance(items, (str, bytes)) or not isinstance(items, Sequence):
        return []
    normalized: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if text:
            normalized.append(text)
    return normalized


def build_slow_wait_fingerprint(payload: Mapping[str, Any]) -> str:
    fingerprint_payload = {
        "company_name": str(payload.get("company_name") or "").strip(),
        "src": str(payload.get("src") or "").strip(),
        "dest": str(payload.get("dest") or "").strip(),
        "item_count": max(0, _safe_int(payload.get("item_count"), 0, minimum=0)),
        "preview_items": _normalize_preview_items(payload.get("preview_items")),
    }
    return hashlib.md5(
        json.dumps(fingerprint_payload, ensure_ascii=True, sort_keys=True).encode("utf-8")
    ).hexdigest()


def build_slow_wait_preview_state(payload: Mapping[str, Any]) -> SlowWaitPreviewState | None:
    items = tuple(_normalize_preview_items(payload.get("preview_items")))
    if not items:
        return None
    item_count = max(len(items), _safe_int(payload.get("item_count"), len(items), minimum=0))
    return SlowWaitPreviewState(
        fingerprint=build_slow_wait_fingerprint(payload),
        items=items,
        company_name=str(payload.get("company_name") or "-").strip() or "-",
        src=str(payload.get("src") or "-").strip() or "-",
        dest=str(payload.get("dest") or "-").strip() or "-",
        item_count=item_count,
        current_index=0,
        shown_once_count=1,
        loop_count=0,
    )


def render_slow_wait_preview(state: SlowWaitPreviewState) -> str:
    if not state.items:
        return ""
    position = min(state.current_index + 1, len(state.items))
    loop_text = f" | loop {state.loop_count + 1}" if state.loop_count > 0 else ""
    return (
        f"Live Preview | Company={state.company_name} | Rute={state.src} -> {state.dest} | "
        f"Item {position}/{state.item_count}{loop_text}: {state.items[state.current_index]}"
    )


def advance_slow_wait_preview(state: SlowWaitPreviewState) -> SlowWaitPreviewState:
    if not state.items:
        return state
    next_index = state.current_index + 1
    loop_count = state.loop_count
    if next_index >= len(state.items):
        next_index = 0
        loop_count += 1
    return SlowWaitPreviewState(
        fingerprint=state.fingerprint,
        items=state.items,
        company_name=state.company_name,
        src=state.src,
        dest=state.dest,
        item_count=state.item_count,
        current_index=next_index,
        shown_once_count=min(len(state.items), state.shown_once_count + 1),
        loop_count=loop_count,
    )


def build_slow_wait_stop_message(
    state: SlowWaitPreviewState,
    *,
    completed_item_count: int | None = None,
    transfer_ref: str = "",
) -> str:
    uploaded = max(0, int(completed_item_count or state.item_count or len(state.items)))
    previewed = min(max(0, int(state.shown_once_count or 0)), len(state.items))
    transfer_text = f" ({transfer_ref})" if str(transfer_ref or "").strip() else ""
    if previewed < len(state.items):
        return (
            f"Preview animasi dihentikan di {previewed}/{len(state.items)} item. "
            f"Upload batch {uploaded} item selesai{transfer_text}."
        )
    return f"Preview animasi batch selesai. Upload batch {uploaded} item selesai{transfer_text}."


def build_business_overrides(
    grouping_mode: str,
    custom_limit: int | str,
    network_retry: int | str,
    network_retry_delay_ms: int | str,
    journal_wait_timeout_ms: int | str,
) -> list[str]:
    mode = str(grouping_mode or "").strip().lower()
    batch_limit = AUTO_GROUP_BATCH_LIMIT if mode != "custom" else _safe_int(custom_limit, 150, minimum=1)
    return [
        f"TRANSACTION_VOLUME_PER_BATCH={batch_limit}",
        f"MAX_RETRY_FOR_NETWORK_ERRORS={_safe_int(network_retry, DEFAULT_BUSINESS_SETTINGS.max_retry_for_network_errors, minimum=0)}",
        f"WAIT_BETWEEN_NETWORK_RETRIES_MS={_safe_int(network_retry_delay_ms, DEFAULT_BUSINESS_SETTINGS.wait_between_network_retries_ms, minimum=0)}",
        f"MAX_WAIT_TIME_FOR_JOURNAL_CREATION_MS={_safe_int(journal_wait_timeout_ms, DEFAULT_BUSINESS_SETTINGS.max_wait_time_for_journal_creation_ms, minimum=0)}",
    ]


class TkQueueHandler(logging.Handler):
    def __init__(self, sink: "queue.Queue[str]") -> None:
        super().__init__()
        self.sink = sink

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if str(record.name).startswith("item_journal.technical"):
                return
            text = self.format(record)
            self.sink.put_nowait(text)
        except Exception:  # noqa: BLE001
            return


class ItemJournalPanel:
    """Embeddable Item Journal panel rendered inside an existing container."""

    def __init__(
        self,
        parent: tk.Frame,
        verbose: bool = False,
        log_file: Optional[str] = None,
        state_store: Any = None,
        global_settings: GlobalSettings | None = None,
        master_cache: SessionMasterCache | None = None,
    ) -> None:
        self.root = parent.winfo_toplevel()  # type: ignore[assignment]
        self.container = parent

        self.log_queue: "queue.Queue[str]" = queue.Queue()
        self.progress_queue: "queue.Queue[Dict[str, Any]]" = queue.Queue()
        self.ui_queue: "queue.Queue[Dict[str, Any]]" = queue.Queue()
        self.worker: Optional[threading.Thread] = None
        self.run_control: Optional[RunControl] = None
        self._progress_total_rows = 0
        self._progress_style_name = "ItemJournalCompact.Horizontal.TProgressbar"

        self.logger, self.log_path = configure_logging(verbose=verbose, log_file=log_file)
        self.technical_logger = get_technical_logger()
        self._attach_queue_logging_handler()
        self.narrator = BusinessNarrator(user_logger=self.logger, technical_logger=self.technical_logger)

        self.state_store = state_store or PersistentState()
        self.state = self.state_store.load()
        self.global_settings = global_settings or GlobalSettings(database_profiles=default_database_profiles())
        self.master_cache = master_cache

        self.workbook_var = tk.StringVar(value=self.state.workbook_path)
        self.output_dir_var = tk.StringVar(value=self.state.output_dir)
        self.database_choice_var = tk.StringVar(value="")
        self.grouping_mode_var = tk.StringVar(value=self.state.grouping_mode)
        self.custom_limit_var = tk.StringVar(value=str(self.state.custom_limit))
        self.network_retry_var = tk.StringVar(value=str(self.state.network_retry))
        self.network_retry_delay_ms_var = tk.StringVar(value=str(self.state.network_retry_delay_ms))
        self.journal_wait_timeout_ms_var = tk.StringVar(value=str(self.state.journal_wait_timeout_ms))
        self.dry_run_var = tk.BooleanVar(value=self.state.dry_run)
        self.advanced_open_var = tk.BooleanVar(value=self.state.advanced_open)

        self.status_var = tk.StringVar(
            value=f"Siap. Log file: {self.log_path if self.log_path is not None else '(disabled)'}"
        )
        self.progress_detail_var = tk.StringVar(value="Idle")
        self.progress_percent_var = tk.StringVar(value="0%")
        self.progress_count_var = tk.StringVar(value="0/0 item")
        self.lock_date_value_var = tk.StringVar(value="-")
        self.journal_dates_value_var = tk.StringVar(value="-")
        self.lock_status_var = tk.StringVar(value="Belum diperiksa")
        self.log_preview_var = tk.StringVar(value="")
        self.latest_log_line_var = tk.StringVar(value="Belum ada log.")

        self.advanced_button_text = tk.StringVar()
        self.sections: dict[str, CollapsibleSection] = {}
        self._db_label_by_profile_id: dict[str, str] = {}
        self._db_profile_id_by_label: dict[str, str] = {}
        self.advanced_body: tk.Frame | None = None
        self.custom_limit_row: tk.Frame | None = None
        self.lock_status_label: tk.Label | None = None
        self.action_button_row: tk.Frame | None = None
        self.progress_detail_label: tk.Label | None = None
        self.log_preview_frame: tk.Frame | None = None
        self._slow_wait_preview: SlowWaitPreviewState | None = None
        self._slow_wait_after_id: str | None = None
        self._poll_after_id: str | None = None
        self._poll_active = False
        self._busy = False

        self._build_panel()
        self._attach_section_summary_traces()
        self._apply_advanced_visibility()
        self._on_grouping_mode_changed()
        self._refresh_section_summaries()
        self._apply_action_progress_layout()
        self.resume()

    def _attach_queue_logging_handler(self) -> None:
        handler = TkQueueHandler(self.log_queue)
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", "%H:%M:%S"))
        self.logger.addHandler(handler)

    def _attach_section_summary_traces(self) -> None:
        for variable in (
            self.workbook_var,
            self.output_dir_var,
            self.database_choice_var,
            self.dry_run_var,
            self.grouping_mode_var,
            self.custom_limit_var,
            self.network_retry_var,
            self.network_retry_delay_ms_var,
            self.journal_wait_timeout_ms_var,
        ):
            variable.trace_add("write", self._on_section_summary_var_changed)

    def _on_section_summary_var_changed(self, *_args: str) -> None:
        self._refresh_section_summaries()

    def refresh_database_options(self) -> None:
        self._refresh_database_options()
        self._refresh_section_summaries()

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
            except Exception:
                pass
            self._poll_after_id = None

    def _refresh_database_options(self) -> None:
        options = build_module_database_options(self.global_settings.database_profiles)
        self._db_label_by_profile_id, self._db_profile_id_by_label = build_option_maps(options)
        selected_id = normalize_module_database_profile_id(self.state.database_profile_id)
        if selected_id != FOLLOW_GLOBAL_PROFILE_ID and find_database_profile(self.global_settings.database_profiles, selected_id) is None:
            selected_id = ensure_database_profile(
                self.global_settings.database_profiles,
                selected_id,
                alias=selected_id,
            )
            self.state.database_profile_id = selected_id
        if selected_id not in self._db_label_by_profile_id:
            selected_id = FOLLOW_GLOBAL_PROFILE_ID
            self.state.database_profile_id = selected_id
        labels = [label for _profile_id, label in options]
        if hasattr(self, "database_combo"):
            self.database_combo.configure(values=labels)
        self.database_choice_var.set(self._db_label_by_profile_id.get(selected_id, ""))

    def _selected_database_profile_id(self) -> str:
        label = str(self.database_choice_var.get() or "").strip()
        return self._db_profile_id_by_label.get(label, FOLLOW_GLOBAL_PROFILE_ID)

    def _current_database_summary_label(self) -> str:
        selected_profile_id = self._selected_database_profile_id()
        if selected_profile_id != FOLLOW_GLOBAL_PROFILE_ID:
            return self._db_label_by_profile_id.get(selected_profile_id, "Use GAS Default")

        default_profile = find_database_profile(
            self.global_settings.database_profiles,
            self.global_settings.default_database_profile_id,
        )
        if default_profile is not None:
            return render_database_profile_label(default_profile)
        return "Use GAS Default"

    def _resolve_database(self, gas_default_database: str) -> str:
        return resolve_database_selection(
            profiles=self.global_settings.database_profiles,
            module_profile_id=self._selected_database_profile_id(),
            default_profile_id=self.global_settings.default_database_profile_id,
            gas_default_database=gas_default_database,
        )

    def _refresh_section_summaries(self) -> None:
        self._set_section_summary("workbook", build_workbook_section_summary(self.workbook_var.get()))
        self._set_section_summary("output", build_output_dir_section_summary(self.output_dir_var.get()))
        self._set_section_summary(
            "connection",
            build_connection_mode_section_summary(
                self._current_database_summary_label(),
                bool(self.dry_run_var.get()),
            ),
        )
        self._set_section_summary(
            "grouping",
            build_grouping_section_summary(
                self.grouping_mode_var.get(),
                self.custom_limit_var.get(),
                self.network_retry_var.get(),
                self.network_retry_delay_ms_var.get(),
                self.journal_wait_timeout_ms_var.get(),
            ),
        )
        self._set_section_summary(
            "dashboard",
            build_lock_dashboard_section_summary(self.lock_status_var.get()),
            fg=self.lock_status_label.cget("fg") if self.lock_status_label is not None else NEUTRAL_COLOR,
        )

    def _set_section_summary(self, key: str, text: str, *, fg: str | None = None) -> None:
        section = self.sections.get(key)
        if section is None:
            return
        section.set_summary(text, fg=fg)

    @staticmethod
    def _card_label(
        parent: tk.Misc,
        *,
        text: str = "",
        textvariable: tk.Variable | None = None,
        fg: str = T.TEXT_ON_LIGHT,
        bold: bool = False,
        wraplength: int = 0,
        justify: str = "left",
        anchor: str = "w",
    ) -> tk.Label:
        kwargs: dict[str, Any] = {
            "bg": T.BG_CARD,
            "fg": fg,
            "font": T.font(T.FONT_BODY_SIZE, bold=bold),
            "justify": justify,
            "anchor": anchor,
        }
        if wraplength > 0:
            kwargs["wraplength"] = wraplength
        if textvariable is not None:
            kwargs["textvariable"] = textvariable
        else:
            kwargs["text"] = text
        return tk.Label(parent, **kwargs)

    @staticmethod
    def _card_entry(parent: tk.Misc, *, textvariable: tk.Variable, width: int | None = None) -> tk.Entry:
        kwargs: dict[str, Any] = {
            "textvariable": textvariable,
            "font": T.font(T.FONT_BODY_SIZE),
            "bg": T.BG_INPUT,
            "fg": T.TEXT_ON_LIGHT,
            "insertbackground": T.TEXT_ON_LIGHT,
            "relief": "solid",
            "bd": 1,
            "disabledbackground": "#F1F3F5",
            "disabledforeground": T.TEXT_MUTED,
        }
        if width is not None:
            kwargs["width"] = width
        return tk.Entry(parent, **kwargs)

    @staticmethod
    def _card_button(
        parent: tk.Misc,
        *,
        text: str = "",
        textvariable: tk.Variable | None = None,
        command: Callable[[], None] | None = None,
        bg: str = T.BRAND_PRIMARY,
        fg: str = T.TEXT_ON_DARK,
        activebackground: str | None = None,
        activeforeground: str = T.TEXT_ON_DARK,
        disabledforeground: str = T.TEXT_MUTED,
    ) -> tk.Button:
        kwargs: dict[str, Any] = {
            "command": command,
            "bg": bg,
            "fg": fg,
            "activebackground": (
                activebackground
                if activebackground is not None
                else (T.BRAND_PRIMARY_DARK if bg == T.BRAND_PRIMARY else T.BRAND_ACCENT)
            ),
            "activeforeground": activeforeground,
            "disabledforeground": disabledforeground,
            "relief": "flat",
            "bd": 0,
            "highlightthickness": 0,
            "font": T.font(T.FONT_BODY_SIZE, bold=True),
            "padx": 12,
            "pady": 4,
            "cursor": "hand2",
        }
        if textvariable is not None:
            kwargs["textvariable"] = textvariable
        else:
            kwargs["text"] = text
        return tk.Button(parent, **kwargs)

    @staticmethod
    def _card_checkbutton(parent: tk.Misc, *, text: str, variable: tk.Variable) -> tk.Checkbutton:
        return tk.Checkbutton(
            parent,
            text=text,
            variable=variable,
            bg=T.BG_CARD,
            fg=T.TEXT_ON_LIGHT,
            activebackground=T.BG_CARD,
            activeforeground=T.TEXT_ON_LIGHT,
            selectcolor=T.BG_CARD,
            highlightthickness=0,
            font=T.font(T.FONT_BODY_SIZE),
        )

    @staticmethod
    def _card_radiobutton(parent: tk.Misc, *, text: str, value: str, variable: tk.Variable, command=None) -> tk.Radiobutton:
        return tk.Radiobutton(
            parent,
            text=text,
            value=value,
            variable=variable,
            command=command,
            bg=T.BG_CARD,
            fg=T.TEXT_ON_LIGHT,
            activebackground=T.BG_CARD,
            activeforeground=T.TEXT_ON_LIGHT,
            selectcolor=T.BG_CARD,
            highlightthickness=0,
            font=T.font(T.FONT_BODY_SIZE),
        )

    @staticmethod
    def _card_spinbox(
        parent: tk.Misc,
        *,
        textvariable: tk.Variable,
        from_: int,
        to: int,
        width: int,
        increment: int = 1,
    ) -> tk.Spinbox:
        return tk.Spinbox(
            parent,
            from_=from_,
            to=to,
            increment=increment,
            width=width,
            textvariable=textvariable,
            font=T.font(T.FONT_BODY_SIZE),
            bg=T.BG_INPUT,
            fg=T.TEXT_ON_LIGHT,
            relief="solid",
            bd=1,
            buttonbackground=T.BG_CARD,
            disabledbackground="#F1F3F5",
            disabledforeground=T.TEXT_MUTED,
        )

    def _create_section(
        self,
        parent: tk.Misc,
        *,
        key: str,
        title: str,
        body_fill: str = "x",
        body_expand: bool = False,
        compact_fill: str = "x",
        compact_expand: bool = False,
        show_compact_when_open: bool = False,
    ) -> CollapsibleSection:
        section = CollapsibleSection(
            parent,
            key=key,
            title=title,
            expanded=bool(self.state.section_open.get(key, default_section_open().get(key, False))),
            on_toggle=self._on_section_toggled,
            body_fill=body_fill,
            body_expand=body_expand,
            compact_fill=compact_fill,
            compact_expand=compact_expand,
            show_compact_when_open=show_compact_when_open,
        )
        self.sections[key] = section
        return section

    def _build_panel(self) -> None:
        self._scrollable = ScrollableFrame(self.container, bg=T.BG_MAIN)
        self._scrollable.pack(fill="both", expand=True)

        main = tk.Frame(self._scrollable.interior, bg=T.BG_MAIN)
        main.pack(fill="x", padx=12, pady=12)

        file_section = self._create_section(main, key="workbook", title="Workbook")
        file_section.pack(fill="x", pady=(0, 8))
        self._card_label(file_section.body, text="Workbook (.xlsm/.xlsx):", bold=True).grid(row=0, column=0, sticky="w")
        self._card_entry(file_section.body, textvariable=self.workbook_var).grid(row=0, column=1, sticky="ew", padx=8)
        self._card_button(file_section.body, text="Browse...", command=self._choose_workbook).grid(
            row=0,
            column=2,
            sticky="e",
        )
        file_section.body.columnconfigure(1, weight=1)
        file_section.refresh_layout()

        output_section = self._create_section(main, key="output", title="Output Folder")
        output_section.pack(fill="x", pady=(0, 8))
        self._card_label(
            output_section.body,
            text="Hasil upload selalu disimpan sebagai copy di folder berikut.",
            bold=True,
        ).grid(row=0, column=0, sticky="w")
        self._card_button(output_section.body, text="Browse Output Folder...", command=self._choose_output_dir).grid(
            row=0,
            column=1,
            sticky="e",
        )
        self._card_label(
            output_section.body,
            textvariable=self.output_dir_var,
            wraplength=920,
            justify="left",
        ).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        output_section.body.columnconfigure(0, weight=1)
        output_section.refresh_layout()

        connection_section = self._create_section(main, key="connection", title="Connection & Mode")
        connection_section.pack(fill="x", pady=(0, 8))
        self._card_label(connection_section.body, text="Database:", bold=True).grid(row=0, column=0, sticky="w")
        self.database_combo = ttk.Combobox(
            connection_section.body,
            state="readonly",
            textvariable=self.database_choice_var,
            width=42,
        )
        self.database_combo.grid(
            row=0,
            column=1,
            sticky="w",
            padx=(8, 24),
        )
        self._card_checkbutton(connection_section.body, text="Dry-run (upload only)", variable=self.dry_run_var).grid(
            row=0,
            column=2,
            sticky="w",
        )
        self._refresh_database_options()
        connection_section.refresh_layout()

        grouping_section = self._create_section(main, key="grouping", title="Grouping")
        grouping_section.pack(fill="x", pady=(0, 8))
        self._card_label(grouping_section.body, text="Grouping Mode:", bold=True).grid(row=0, column=0, sticky="w")
        self._card_radiobutton(
            grouping_section.body,
            text="Sesuai COA (Auto)",
            value="auto",
            variable=self.grouping_mode_var,
            command=self._on_grouping_mode_changed,
        ).grid(row=0, column=1, sticky="w", padx=(8, 16))
        self._card_radiobutton(
            grouping_section.body,
            text="Custom Limit",
            value="custom",
            variable=self.grouping_mode_var,
            command=self._on_grouping_mode_changed,
        ).grid(row=0, column=2, sticky="w")

        self.custom_limit_row = tk.Frame(grouping_section.body, bg=T.BG_CARD)
        self.custom_limit_row.grid(row=1, column=1, columnspan=2, sticky="w", pady=(8, 0))
        self._card_label(self.custom_limit_row, text="Max item per transfer:", bold=True).pack(side="left")
        self._card_spinbox(
            self.custom_limit_row,
            from_=1,
            to=5000,
            width=8,
            textvariable=self.custom_limit_var,
        ).pack(side="left", padx=(8, 0))

        self._card_button(
            grouping_section.body,
            textvariable=self.advanced_button_text,
            command=self._toggle_advanced,
        ).grid(
            row=2,
            column=0,
            sticky="w",
            pady=(10, 0),
        )
        self.advanced_body = tk.Frame(grouping_section.body, bg=T.BG_CARD)
        self.advanced_body.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        self._card_label(self.advanced_body, text="Network Retry:", bold=True).grid(row=0, column=0, sticky="w")
        self._card_spinbox(
            self.advanced_body,
            from_=0,
            to=10,
            width=8,
            textvariable=self.network_retry_var,
        ).grid(row=0, column=1, sticky="w", padx=(8, 24))
        self._card_label(self.advanced_body, text="Retry Delay (ms):", bold=True).grid(row=0, column=2, sticky="w")
        self._card_spinbox(
            self.advanced_body,
            from_=0,
            to=60000,
            increment=100,
            width=10,
            textvariable=self.network_retry_delay_ms_var,
        ).grid(row=0, column=3, sticky="w", padx=(8, 24))
        self._card_label(self.advanced_body, text="Journal Wait Timeout (ms):", bold=True).grid(
            row=1,
            column=0,
            sticky="w",
            pady=(8, 0),
        )
        self._card_spinbox(
            self.advanced_body,
            from_=1000,
            to=3600000,
            increment=1000,
            width=12,
            textvariable=self.journal_wait_timeout_ms_var,
        ).grid(row=1, column=1, sticky="w", padx=(8, 0), pady=(8, 0))
        grouping_section.refresh_layout()

        actions_section = self._create_section(
            main,
            key="actions",
            title="Action & Progress",
            compact_fill="x",
            show_compact_when_open=True,
        )
        actions_section.pack(fill="x", pady=(0, 8))
        ttk.Style(self.root).configure(self._progress_style_name, thickness=10)
        progress_row = tk.Frame(actions_section.compact_body, bg=T.BG_CARD)
        progress_row.pack(fill="x")
        self.progress = ttk.Progressbar(
            progress_row,
            mode="determinate",
            maximum=100,
            style=self._progress_style_name,
        )
        self.progress.configure(value=0)
        self.progress.grid(row=0, column=0, sticky="ew")
        self._card_label(progress_row, textvariable=self.progress_percent_var, bold=True, anchor="e").grid(
            row=0,
            column=1,
            sticky="e",
            padx=(8, 0),
        )
        self._card_label(progress_row, textvariable=self.progress_count_var, bold=True, anchor="e").grid(
            row=0,
            column=2,
            sticky="e",
            padx=(8, 0),
        )
        progress_row.columnconfigure(0, weight=1)

        self.action_button_row = tk.Frame(actions_section.body, bg=T.BG_CARD)
        self.action_button_row.pack(fill="x")
        self.btn_check = self._card_button(
            self.action_button_row,
            text="Check Lock Date",
            command=self._run_check_lock,
            bg=T.BRAND_SECONDARY,
        )
        self.btn_upload = self._card_button(self.action_button_row, text="Upload Internal Transfer", command=self._run_upload)
        self.btn_stop = self._card_button(
            self.action_button_row,
            text="Stop",
            command=self._request_stop,
            bg=T.STATUS_ERROR,
            fg=T.TEXT_ON_DARK,
            activebackground="#C93C33",
            activeforeground=T.TEXT_ON_DARK,
            disabledforeground=T.TEXT_ON_DARK,
        )
        self.btn_stop.configure(state="disabled")
        self.btn_check.pack(side="left")
        self.btn_upload.pack(side="left", padx=8)
        self.btn_stop.pack(side="left")
        self.progress_detail_label = self._card_label(actions_section.body, textvariable=self.progress_detail_var)
        self.progress_detail_label.pack(fill="x", pady=(8, 0))
        actions_section.refresh_layout()

        dashboard_section = self._create_section(main, key="dashboard", title="Lock Date Dashboard")
        dashboard_section.pack(fill="x", pady=(0, 8))
        self._card_label(dashboard_section.body, text="Tanggal Closing Odoo", bold=True).grid(row=0, column=0, sticky="w")
        self._card_label(dashboard_section.body, textvariable=self.lock_date_value_var).grid(
            row=0,
            column=1,
            sticky="w",
            padx=(12, 0),
        )
        self._card_label(dashboard_section.body, text="Tanggal Jurnal di File", bold=True).grid(
            row=1,
            column=0,
            sticky="nw",
            pady=(8, 0),
        )
        self._card_label(
            dashboard_section.body,
            textvariable=self.journal_dates_value_var,
            wraplength=900,
            justify="left",
        ).grid(row=1, column=1, sticky="w", padx=(12, 0), pady=(8, 0))
        self._card_label(dashboard_section.body, text="Status Validasi", bold=True).grid(row=2, column=0, sticky="w", pady=(8, 0))
        self.lock_status_label = self._card_label(
            dashboard_section.body,
            textvariable=self.lock_status_var,
            fg=NEUTRAL_COLOR,
        )
        self.lock_status_label.grid(row=2, column=1, sticky="w", padx=(12, 0), pady=(8, 0))
        dashboard_section.refresh_layout()

        logs_section = self._create_section(
            main,
            key="logs",
            title="Logs",
            body_fill="x",
            body_expand=False,
            show_compact_when_open=False,
        )
        logs_section.pack(fill="x")
        self._card_label(
            logs_section.compact_body,
            textvariable=self.latest_log_line_var,
            fg=T.TEXT_MUTED,
            wraplength=0,
        ).pack(fill="x")
        self.log_preview_frame = tk.Frame(logs_section.body, bg=T.BG_CARD)
        self.log_preview_frame.pack(fill="x", pady=(0, 8))
        self._card_label(self.log_preview_frame, text="Live Preview Batch:", bold=True).pack(anchor="w")
        self._card_label(
            self.log_preview_frame,
            textvariable=self.log_preview_var,
            wraplength=920,
            justify="left",
        ).pack(fill="x", pady=(4, 0))
        self.log_text = ScrolledText(
            logs_section.body,
            height=max(14, T.MIN_LOG_VISIBLE_LINES),
            state="disabled",
            wrap="word",
            font=T.font(T.FONT_SMALL_SIZE),
            bg=T.BG_INPUT,
            relief="solid",
            bd=1,
        )
        self.log_text.pack(fill="x")
        logs_section.refresh_layout()
        self._set_log_preview_visible(False)

        footer = tk.Frame(main, bg=T.BG_MAIN)
        footer.pack(fill="x")
        tk.Label(
            footer,
            textvariable=self.status_var,
            bg=T.BG_MAIN,
            fg=T.TEXT_MUTED,
            font=T.font(T.FONT_SMALL_SIZE),
            anchor="w",
        ).pack(side="left", pady=(8, 0))

    def _on_section_toggled(self, key: str, is_open: bool) -> None:
        self.state.section_open[key] = bool(is_open)
        if key == "actions":
            self._apply_action_progress_layout()
        self._save_state()

    def _apply_action_progress_layout(self) -> None:
        section = self.sections.get("actions")
        if section is None:
            return
        layout = build_action_progress_layout(section.is_open)
        if self.action_button_row is not None:
            if layout.show_buttons:
                self.action_button_row.pack(fill="x")
            else:
                self.action_button_row.pack_forget()
        if self.progress_detail_label is not None:
            if layout.show_detail:
                self.progress_detail_label.pack(fill="x", pady=(8, 0))
            else:
                self.progress_detail_label.pack_forget()
        section.refresh_layout()

    def _set_log_preview_visible(self, visible: bool) -> None:
        if self.log_preview_frame is None:
            return
        if visible:
            pack_kwargs: dict[str, Any] = {"fill": "x", "pady": (0, 8)}
            if hasattr(self, "log_text"):
                pack_kwargs["before"] = self.log_text
            self.log_preview_frame.pack(**pack_kwargs)
        else:
            self.log_preview_frame.pack_forget()

    def _cancel_slow_wait_timer(self) -> None:
        if self._slow_wait_after_id is None:
            return
        try:
            self.root.after_cancel(self._slow_wait_after_id)
        except Exception:
            pass
        self._slow_wait_after_id = None

    def _schedule_slow_wait_tick(self) -> None:
        self._cancel_slow_wait_timer()
        if self._slow_wait_preview is None or len(self._slow_wait_preview.items) <= 1:
            return
        self._slow_wait_after_id = self.root.after(SLOW_WAIT_PREVIEW_INTERVAL_MS, self._tick_slow_wait_preview)

    def _start_or_refresh_slow_wait_preview(self, payload: Mapping[str, Any]) -> None:
        state = build_slow_wait_preview_state(payload)
        if state is None:
            return
        if self._slow_wait_preview is not None and self._slow_wait_preview.fingerprint != state.fingerprint:
            self._stop_slow_wait_preview(log_summary=False)
        if self._slow_wait_preview is None:
            self._slow_wait_preview = state
            self.log_preview_var.set(render_slow_wait_preview(state))
            self._set_log_preview_visible(True)
        self._schedule_slow_wait_tick()

    def _tick_slow_wait_preview(self) -> None:
        self._slow_wait_after_id = None
        if self._slow_wait_preview is None:
            return
        self._slow_wait_preview = advance_slow_wait_preview(self._slow_wait_preview)
        self.log_preview_var.set(render_slow_wait_preview(self._slow_wait_preview))
        self._schedule_slow_wait_tick()

    def _stop_slow_wait_preview(
        self,
        *,
        log_summary: bool,
        completed_item_count: int | None = None,
        transfer_ref: str = "",
    ) -> None:
        state = self._slow_wait_preview
        self._cancel_slow_wait_timer()
        self._slow_wait_preview = None
        self.log_preview_var.set("")
        self._set_log_preview_visible(False)
        if log_summary and state is not None:
            self.logger.info(
                build_slow_wait_stop_message(
                    state,
                    completed_item_count=completed_item_count,
                    transfer_ref=transfer_ref,
                )
            )

    def _apply_advanced_visibility(self) -> None:
        if self.advanced_body is None:
            return
        if self.advanced_open_var.get():
            self.advanced_body.grid()
            self.advanced_button_text.set("Hide Advanced Settings")
        else:
            self.advanced_body.grid_remove()
            self.advanced_button_text.set("Show Advanced Settings")

    def _toggle_advanced(self) -> None:
        self.advanced_open_var.set(not self.advanced_open_var.get())
        self._apply_advanced_visibility()

    def _on_grouping_mode_changed(self) -> None:
        if self.custom_limit_row is None:
            return
        if self.grouping_mode_var.get().strip().lower() == "custom":
            self.custom_limit_row.grid()
        else:
            self.custom_limit_row.grid_remove()

    def _current_workbook_dir(self) -> str:
        workbook_path = self.workbook_var.get().strip()
        if workbook_path:
            try:
                candidate = Path(workbook_path).expanduser()
                if candidate.parent.exists():
                    return str(candidate.parent)
            except (OSError, RuntimeError, ValueError):
                pass
        return normalize_output_dir(self.output_dir_var.get())

    def _choose_workbook(self) -> None:
        selected = filedialog.askopenfilename(
            title="Pilih workbook Item Journal",
            initialdir=self._current_workbook_dir(),
            filetypes=[("Excel Macro Workbook", "*.xlsm"), ("Excel Workbook", "*.xlsx"), ("All files", "*.*")],
        )
        if selected:
            self.workbook_var.set(selected)

    def _choose_output_dir(self) -> None:
        selected = filedialog.askdirectory(
            title="Pilih folder hasil upload",
            initialdir=normalize_output_dir(self.output_dir_var.get()),
            mustexist=True,
        )
        if selected:
            self.output_dir_var.set(normalize_output_dir(selected))

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        state = "disabled" if busy else "normal"
        self.btn_check.configure(state=state)
        self.btn_upload.configure(state=state)
        self.btn_stop.configure(state="normal" if busy else "disabled")
        if busy:
            self._stop_slow_wait_preview(log_summary=False)
            self._progress_total_rows = 0
            snapshot = build_progress_snapshot(current=0, total=0)
            self.progress.configure(value=snapshot.value)
            self.progress_percent_var.set(snapshot.percent_text)
            self.progress_count_var.set(snapshot.count_text)
            self.progress_detail_var.set("Running...")
        else:
            self._stop_slow_wait_preview(log_summary=False)
            self.run_control = None

    def _append_log_batch(self, lines: Sequence[str]) -> None:
        if not lines:
            return
        self.latest_log_line_var.set(
            build_compact_preview_text(lines[-1], empty_text="Belum ada log.", max_chars=120)
        )
        append_bounded_text_lines(self.log_text, lines)

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
        self._poll_progress()
        self._poll_ui_events()
        self._poll_after_id = self.root.after(33 if self._busy else 250, self._poll_queues)

    def _poll_progress(self) -> None:
        while True:
            try:
                payload = self.progress_queue.get_nowait()
            except queue.Empty:
                break
            current = _safe_int(payload.get("current"), 0, minimum=0)
            total = _safe_int(payload.get("total"), 0, minimum=0)
            stage = str(payload.get("stage", ""))
            message = stage_to_business_message(stage, payload)
            rpm = payload.get("rows_per_min")
            eta_sec = payload.get("eta_sec")
            detail = message
            if rpm is not None and eta_sec is not None:
                detail = f"{detail} | {rpm} row/min | ETA {eta_sec}s"
            self.progress_detail_var.set(detail)
            if total > 0:
                if self._progress_total_rows <= 0 or total > self._progress_total_rows:
                    self._progress_total_rows = total
                if total == self._progress_total_rows:
                    snapshot = build_progress_snapshot(current=current, total=total)
                    self.progress.configure(value=snapshot.value)
                    self.progress_percent_var.set(snapshot.percent_text)
                    self.progress_count_var.set(snapshot.count_text)
            if stage == "SYNC_WAIT":
                self.status_var.set(message)
                self._start_or_refresh_slow_wait_preview(payload)
            elif stage == "TRANSFER_DONE":
                self._stop_slow_wait_preview(
                    log_summary=True,
                    completed_item_count=_safe_int(payload.get("item_count"), 0, minimum=0),
                    transfer_ref=str(payload.get("transfer_ref") or "").strip(),
                )
            elif stage in {"TRANSFER_START", "TRANSFER_ERROR", "RUN_DONE"}:
                self._stop_slow_wait_preview(log_summary=False)

    def _poll_ui_events(self) -> None:
        while True:
            try:
                event = self.ui_queue.get_nowait()
            except queue.Empty:
                break
            event_type = str(event.get("type", ""))
            if event_type == "status":
                self.status_var.set(str(event.get("value", "")))
            elif event_type == "error":
                messagebox.showerror(str(event.get("title", "Error")), str(event.get("message", "")))
            elif event_type == "warning":
                messagebox.showwarning(str(event.get("title", "Warning")), str(event.get("message", "")))
            elif event_type == "busy":
                self._set_busy(bool(event.get("value", False)))
            elif event_type == "lock_dashboard":
                dashboard = event.get("dashboard")
                if isinstance(dashboard, LockDashboardState):
                    self._apply_lock_dashboard(dashboard)

    def _apply_lock_dashboard(self, dashboard: LockDashboardState) -> None:
        self.lock_date_value_var.set(dashboard.lock_date_text)
        self.journal_dates_value_var.set(dashboard.journal_dates_text)
        self.lock_status_var.set(dashboard.status_text)
        if self.lock_status_label is not None:
            self.lock_status_label.configure(fg=dashboard.status_color)
        self._set_section_summary("dashboard", build_lock_dashboard_section_summary(dashboard.status_text), fg=dashboard.status_color)

    def _enqueue_ui_event(self, event_type: str, **payload: Any) -> None:
        self.ui_queue.put({"type": event_type, **payload})

    def _request_stop(self) -> None:
        if self.run_control is None:
            return
        self.run_control.request_stop()
        self.status_var.set("Stop diminta. Menunggu batch aktif selesai...")
        self.logger.warning("STOP requested by user (run-to-batch-stop).")

    def _build_state_snapshot(self) -> GuiState:
        return GuiState(
            workbook_path=self.workbook_var.get().strip(),
            output_dir=normalize_output_dir(self.output_dir_var.get()),
            database_profile_id=self._selected_database_profile_id(),
            grouping_mode="custom" if self.grouping_mode_var.get().strip().lower() == "custom" else "auto",
            custom_limit=_safe_int(self.custom_limit_var.get(), 150, minimum=1),
            network_retry=_safe_int(
                self.network_retry_var.get(),
                DEFAULT_BUSINESS_SETTINGS.max_retry_for_network_errors,
                minimum=0,
            ),
            network_retry_delay_ms=_safe_int(
                self.network_retry_delay_ms_var.get(),
                DEFAULT_BUSINESS_SETTINGS.wait_between_network_retries_ms,
                minimum=0,
            ),
            journal_wait_timeout_ms=_safe_int(
                self.journal_wait_timeout_ms_var.get(),
                DEFAULT_BUSINESS_SETTINGS.max_wait_time_for_journal_creation_ms,
                minimum=0,
            ),
            dry_run=bool(self.dry_run_var.get()),
            advanced_open=bool(self.advanced_open_var.get()),
            section_open=merge_section_open(self.state.section_open),
        )

    def _save_state(self) -> None:
        self.state_store.save(self._build_state_snapshot())

    def _collect_runtime_settings(self) -> tuple[RuntimeSettings, BusinessUploadSettings]:
        settings, business = build_runtime_settings(
            preset=DEFAULT_PRESET,
            set_args=build_business_overrides(
                grouping_mode=self.grouping_mode_var.get(),
                custom_limit=self.custom_limit_var.get(),
                network_retry=self.network_retry_var.get(),
                network_retry_delay_ms=self.network_retry_delay_ms_var.get(),
                journal_wait_timeout_ms=self.journal_wait_timeout_ms_var.get(),
            ),
        )
        thresholds = PerfThresholds.from_profile(DEFAULT_PERF_PROFILE)
        settings.perf_warn_rpc_ms = thresholds.warn_rpc_ms
        settings.perf_critical_rpc_ms = thresholds.critical_rpc_ms
        settings.perf_stall_warn_sec = thresholds.warn_stall_sec
        settings.perf_stall_critical_sec = thresholds.critical_stall_sec
        settings.perf_heartbeat_sec = thresholds.heartbeat_sec
        settings.perf_jsonl_flush_every_events = thresholds.flush_every_events
        settings.perf_top_causes = thresholds.top_causes
        return settings, business

    def _validate_workbook_input(self) -> str:
        workbook_path = self.workbook_var.get().strip()
        if not workbook_path:
            raise RuntimeError("Pilih file workbook terlebih dulu.")
        path = Path(workbook_path).expanduser()
        if not path.exists():
            raise RuntimeError(f"Workbook tidak ditemukan: {path}")
        if not path.is_file():
            raise RuntimeError(f"Path workbook bukan file: {path}")
        return str(path)

    def _validate_output_dir(self) -> str:
        clean = normalize_output_dir(self.output_dir_var.get())
        self.output_dir_var.set(clean)
        return clean

    def _start_worker(self, target: Callable[[], None]) -> None:
        if self.worker is not None and self.worker.is_alive():
            messagebox.showwarning("Busy", "Masih ada proses berjalan.")
            return
        self._save_state()
        self.run_control = RunControl(
            stop_mode="run-to-batch-stop",
            progress_callback=lambda payload: self.progress_queue.put(payload),
        )
        self._set_busy(True)
        self.worker = threading.Thread(target=target, daemon=True)
        self.worker.start()

    def _run_check_lock(self) -> None:
        try:
            workbook_path = self._validate_workbook_input()
            settings, _business_settings = self._collect_runtime_settings()
        except Exception as exc:  # noqa: BLE001
            self._apply_lock_dashboard(build_lock_dashboard_state([], None, error_text=str(exc)))
            self.status_var.set("Gagal check lock date.")
            return

        def worker() -> None:
            unique_dates: list[date] = []
            try:
                self._enqueue_ui_event("status", value="Menjalankan check lock date...")
                repo = ItemJournalWorkbookRepo(workbook_path)
                unique_dates = summarize_unique_dates(repo.read_rows())
                config = fetch_odoo_config(settings=settings)
                config.database = self._resolve_database(config.database)
                self.logger.info("DB efektif check lock: %s", config.database)

                async def _run_async() -> object:
                    async with AsyncOdooJsonRpcClient(
                        config=config,
                        settings=settings,
                        logger=self.logger,
                        max_concurrency=DEFAULT_MAX_CONCURRENCY,
                    ) as rpc_async:
                        service_async = LockDateServiceAsync(rpc=rpc_async, logger=self.logger)
                        return await service_async.check_close_acc_date(repo)

                lock_date = asyncio.run(_run_async())
                self._enqueue_ui_event(
                    "lock_dashboard",
                    dashboard=build_lock_dashboard_state(unique_dates, lock_date),
                )
                self._enqueue_ui_event("status", value="Selesai check lock date (read-only, workbook tidak diubah).")
                if lock_date:
                    self.logger.info("fiscalyear_lock_date=%s", lock_date)
                else:
                    self.logger.warning("fiscalyear_lock_date kosong.")
            except Exception as exc:  # noqa: BLE001
                self.logger.exception("check_close_acc_date gagal")
                self._enqueue_ui_event(
                    "lock_dashboard",
                    dashboard=build_lock_dashboard_state(unique_dates, None, error_text=str(exc)),
                )
                self._enqueue_ui_event("status", value="Gagal check lock date.")
            finally:
                self._enqueue_ui_event("busy", value=False)

        self._start_worker(worker)

    def _log_save_result(self, requested_mode: str, result: SaveResult, show_warning_popup: bool = False) -> None:
        log_save_result(self.logger, requested_mode, result)
        if result.warning and show_warning_popup and result.used_temp_fallback:
            self._enqueue_ui_event("warning", title="Warning", message=result.warning)

    def _run_upload(self) -> None:
        try:
            workbook_path = self._validate_workbook_input()
            output_dir = self._validate_output_dir()
            settings, _business_settings = self._collect_runtime_settings()
            dry_run = bool(self.dry_run_var.get())
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Error", str(exc))
            return

        def worker() -> None:
            try:
                configured_remap_mode = str(getattr(settings, "stj_remap_mode", "") or "").strip().lower() or "-"
                if not dry_run and configured_remap_mode != "conservative":
                    self.logger.warning(
                        "Preflight upload: STJ_REMAP_MODE=%s akan dipaksa ke conservative saat run production (dry_run=false).",
                        configured_remap_mode,
                    )
                self._enqueue_ui_event("status", value="Menjalankan upload internal transfer...")
                repo = ItemJournalWorkbookRepo(workbook_path)
                config = fetch_odoo_config(settings=settings)
                config.database = self._resolve_database(config.database)
                self.logger.info("DB efektif upload: %s", config.database)
                run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
                perf_reporter = PerfReporter(
                    run_id=run_id,
                    profile=DEFAULT_PERF_PROFILE,
                    logger=self.logger,
                    technical_logger=self.technical_logger,
                    perf_sheet_enabled=DEFAULT_PERF_SHEET,
                    persist_files=False,
                )
                perf_reporter.thresholds.flush_every_events = max(1, int(settings.perf_jsonl_flush_every_events))
                perf_reporter.thresholds.top_causes = max(1, int(settings.perf_top_causes))
                audit_sink = AuditSink(
                    run_id=run_id,
                    audit_level=DEFAULT_AUDIT_LEVEL,
                    flush_every=settings.audit_flush_every_events,
                    persist_file=False,
                )

                async def _run_async() -> dict:
                    async with AsyncOdooJsonRpcClient(
                        config=config,
                        settings=settings,
                        logger=self.logger,
                        max_concurrency=DEFAULT_MAX_CONCURRENCY,
                        rpc_telemetry_callback=perf_reporter.handle_rpc_telemetry,
                    ) as rpc_async:
                        service_async = ItemJournalServiceAsync(
                            rpc=rpc_async,
                            settings=settings,
                            logger=self.logger,
                            narrator=self.narrator,
                            run_control=self.run_control or RunControl(),
                            audit_sink=audit_sink,
                            run_id=run_id,
                            perf_reporter=perf_reporter,
                            master_cache=self.master_cache,
                        )
                        return await service_async.run_upload(repo=repo, dry_run=dry_run)

                self.narrator.info("UPLOAD_STARTED")
                try:
                    summary = asyncio.run(_run_async())
                finally:
                    perf_summary = perf_reporter.close()
                    self.logger.info(
                        "Perf events file: %s",
                        str(perf_reporter.events_path) if perf_reporter.events_path is not None else "(disabled)",
                    )
                    self.logger.info(
                        "Perf summary file: %s",
                        str(perf_reporter.summary_path) if perf_reporter.summary_path is not None else "(disabled)",
                    )
                    audit_sink.close()
                    self.logger.info("Audit file: %s", str(audit_sink.path) if audit_sink.path is not None else "(disabled)")
                    if DEFAULT_PERF_SHEET:
                        repo.append_perf_summary(perf_summary)

                final_mode = "copy"
                save_result = repo.save(
                    mode=final_mode,
                    copy_name_stem=build_copy_name_stem_from_summary(summary),
                    output_dir=output_dir,
                )
                self._log_save_result(final_mode, save_result, show_warning_popup=True)
                self.narrator.info("UPLOAD_COMPLETED")
                self.logger.info("File hasil upload tersimpan: %s", save_result.path)
                if summary.get("stopped"):
                    self._enqueue_ui_event("status", value=f"Upload stopped aman. Saved: {save_result.path}")
                else:
                    self._enqueue_ui_event("status", value=f"Upload selesai. Saved: {save_result.path}")
            except Exception as exc:  # noqa: BLE001
                self.logger.exception("UnggahInternalTransfer gagal")
                self._enqueue_ui_event("error", title="Error", message=str(exc))
                self._enqueue_ui_event("status", value="Gagal upload.")
            finally:
                self._enqueue_ui_event("busy", value=False)

        self._start_worker(worker)

    def can_shutdown(self) -> bool:
        if self.worker is not None and self.worker.is_alive():
            messagebox.showwarning("Busy", "Masih ada proses berjalan.")
            return False
        return True

    def shutdown(self) -> None:
        self.pause()
        self._stop_slow_wait_preview(log_summary=False)
        self._save_state()

    def _on_close(self) -> None:
        if not self.can_shutdown():
            return
        self.shutdown()


class ItemJournalGui:
    """Standalone wrapper that hosts ItemJournalPanel in its own Tk root."""

    def __init__(
        self,
        verbose: bool = False,
        log_file: Optional[str] = None,
        state_store: Any = None,
    ) -> None:
        self.root = tk.Tk()
        self.root.title("Internal Transfer - Odoo")
        self.root.geometry("1180x780")
        self.root.minsize(980, 720)
        apply_window_branding(self.root)
        self.panel = ItemJournalPanel(
            parent=self.root,
            verbose=verbose,
            log_file=log_file,
            state_store=state_store,
        )
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.panel, name)

    def _on_close(self) -> None:
        if not self.panel.can_shutdown():
            return
        self.panel.shutdown()
        self.root.destroy()

    def shutdown(self) -> None:
        self.panel.shutdown()

    def run(self) -> int:
        self.root.mainloop()
        return 0


def launch_gui(verbose: bool = False, log_file: Optional[str] = None) -> int:
    app = ItemJournalGui(verbose=verbose, log_file=log_file)
    return app.run()
