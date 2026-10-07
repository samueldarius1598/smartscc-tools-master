"""Automated GUI inspector for SVL dashboard widget state."""

from __future__ import annotations

import argparse
import json
import logging
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter, sleep
from tkinter import messagebox
from typing import Any, Iterator, Sequence

from smartscc_tools.core.auth import AutoAuthService
from smartscc_tools.core.global_config import GlobalPersistentState
from smartscc_tools.features.item_journal.runtime import configure_logging
from smartscc_tools.features.item_journal.utils import normalize_text
from smartscc_tools.features.svl_fix_je.config import (
    DISPLAY_NAME,
    MODULE_ID,
    PURCHASE_CYCLE_BALANCE_DATASET_MODE,
    normalize_dashboard_dataset_mode,
)
from smartscc_tools.modules.svl_fix_je_dashboard_page import DATASET_MODE_LABEL_BY_VALUE
from smartscc_tools.modules.svl_fix_je_module import _SvlFixJeStateStore
from smartscc_tools.shell.dashboard import DashboardWindow


@dataclass(frozen=True)
class _GuiInspectConfig:
    company_id: int
    date_from: str
    date_to: str
    dataset_mode: str
    database_profile_id: str
    dump_json: Path
    tree_row_limit: int
    collect_pcb_visible: bool
    open_pcb_repair_dialog: bool
    select_picking: str
    selection_target: str
    timeout_seconds: float
    include_selected_cycle_payload: bool
    keep_window_open: bool


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python main.py svl-dashboard-gui-inspect",
        description="Launch GUI, jalankan analyze, lalu dump state widget Tkinter aktual ke JSON.",
    )
    parser.add_argument("--company-id", type=int, default=0, help="Company ID target. Default: ambil dari state GUI terakhir.")
    parser.add_argument("--date-from", default="", help="Tanggal awal YYYY-MM-DD. Default: ambil dari state GUI terakhir.")
    parser.add_argument("--date-to", default="", help="Tanggal akhir YYYY-MM-DD. Default: ambil dari state GUI terakhir.")
    parser.add_argument(
        "--dataset-mode",
        choices=["issues", PURCHASE_CYCLE_BALANCE_DATASET_MODE],
        default="",
        help="Mode dataset dashboard. Default: ambil dari state GUI terakhir.",
    )
    parser.add_argument(
        "--database-profile-id",
        default="",
        help="Profile database module. Default: ambil dari state GUI terakhir.",
    )
    parser.add_argument("--dump-json", required=True, help="Path file JSON output untuk state widget GUI.")
    parser.add_argument(
        "--tree-row-limit",
        type=int,
        default=500,
        help="Maksimum node yang disimpan per Treeview. 0 = tanpa batas.",
    )
    parser.add_argument(
        "--collect-pcb-visible",
        action="store_true",
        help="Tambahkan semua cycle PCB actionable yang sedang visible/filtered ke PCB Repair Collection sebelum dump.",
    )
    parser.add_argument(
        "--open-pcb-repair-dialog",
        action="store_true",
        help="Buka dialog PCB Repair Collection sebelum dump agar state widget dialog ikut tersimpan.",
    )
    parser.add_argument(
        "--select-picking",
        default="",
        help="Pilih cycle sidebar berdasarkan picking name setelah render selesai.",
    )
    parser.add_argument(
        "--selection-target",
        choices=["none", "cycle", "item"],
        default="cycle",
        help="Target selection otomatis setelah analyze selesai.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=600.0,
        help="Batas waktu total menunggu load company, analyze, dan render GUI.",
    )
    parser.add_argument(
        "--exclude-selected-cycle-payload",
        action="store_true",
        help="Jangan sertakan payload detail cycle terpilih di JSON output.",
    )
    parser.add_argument(
        "--keep-window-open",
        action="store_true",
        help="Biarkan window GUI tetap terbuka setelah dump selesai.",
    )
    parser.add_argument("--log-file", default="", help="Path file log output.")
    parser.add_argument("--verbose", action="store_true", help="Mode log detail.")
    return parser


def _resolve_config(args: argparse.Namespace) -> _GuiInspectConfig:
    global_settings = GlobalPersistentState().load()
    module_settings = _SvlFixJeStateStore(global_settings).load()
    company_id = int(args.company_id or module_settings.dashboard_company_id or 0)
    if company_id <= 0:
        raise RuntimeError("Company ID belum tersedia. Set di GUI dulu atau kirim --company-id.")
    dataset_mode = normalize_dashboard_dataset_mode(
        normalize_text(args.dataset_mode) or normalize_text(module_settings.dashboard_dataset_mode)
    )
    database_profile_id = normalize_text(args.database_profile_id) or normalize_text(module_settings.database_profile_id)
    return _GuiInspectConfig(
        company_id=company_id,
        date_from=normalize_text(args.date_from) or normalize_text(module_settings.dashboard_date_from),
        date_to=normalize_text(args.date_to) or normalize_text(module_settings.dashboard_date_to),
        dataset_mode=dataset_mode,
        database_profile_id=database_profile_id,
        dump_json=Path(str(args.dump_json)).expanduser(),
        tree_row_limit=max(0, int(args.tree_row_limit or 0)),
        collect_pcb_visible=bool(args.collect_pcb_visible),
        open_pcb_repair_dialog=bool(args.open_pcb_repair_dialog),
        select_picking=normalize_text(args.select_picking),
        selection_target=normalize_text(args.selection_target).lower() or "cycle",
        timeout_seconds=max(30.0, float(args.timeout_seconds or 0.0)),
        include_selected_cycle_payload=not bool(args.exclude_selected_cycle_payload),
        keep_window_open=bool(args.keep_window_open),
    )


@contextmanager
def _suppressed_messageboxes(logger: logging.Logger) -> Iterator[None]:
    originals = {
        "showinfo": messagebox.showinfo,
        "showwarning": messagebox.showwarning,
        "showerror": messagebox.showerror,
        "askyesno": messagebox.askyesno,
    }

    def _make_logger(level: int, label: str):
        def _inner(title: str, message: str, *args: Any, **kwargs: Any) -> str:  # noqa: ARG001
            logger.log(level, "[GUI-MSGBOX:%s] %s | %s", label, title, message)
            return "ok"

        return _inner

    def _askyesno(title: str, message: str, *args: Any, **kwargs: Any) -> bool:  # noqa: ARG001
        logger.info("[GUI-MSGBOX:askyesno] %s | %s | auto-yes", title, message)
        return True

    messagebox.showinfo = _make_logger(logging.INFO, "info")
    messagebox.showwarning = _make_logger(logging.WARNING, "warning")
    messagebox.showerror = _make_logger(logging.ERROR, "error")
    messagebox.askyesno = _askyesno
    try:
        yield
    finally:
        messagebox.showinfo = originals["showinfo"]
        messagebox.showwarning = originals["showwarning"]
        messagebox.showerror = originals["showerror"]
        messagebox.askyesno = originals["askyesno"]


@contextmanager
def _suppressed_background_auth() -> Iterator[None]:
    original = AutoAuthService.start_background_hydration
    AutoAuthService.start_background_hydration = lambda self, on_update: None  # type: ignore[assignment]
    try:
        yield
    finally:
        AutoAuthService.start_background_hydration = original


def _pump_gui(root: Any, *, duration_seconds: float = 0.2) -> None:
    deadline = perf_counter() + max(0.0, duration_seconds)
    while perf_counter() < deadline:
        root.update()
        sleep(0.02)


def _wait_until(root: Any, predicate, *, timeout_seconds: float, description: str) -> None:
    started = perf_counter()
    while True:
        root.update()
        if predicate():
            return
        if (perf_counter() - started) >= timeout_seconds:
            raise TimeoutError(f"Timeout menunggu {description}.")
        sleep(0.03)


def _write_dump(path: Path, payload: dict[str, Any], logger: logging.Logger) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("GUI widget dump written: %s", path)


def _resolve_svl_module(app: DashboardWindow) -> Any:
    module = app.get_module(MODULE_ID)
    if module is None or not callable(getattr(module, "get_shell", None)):
        raise RuntimeError(f"Module {MODULE_ID} tidak tersedia di DashboardWindow.")
    return module


def _apply_dataset_mode(page: Any, dataset_mode: str) -> None:
    label = DATASET_MODE_LABEL_BY_VALUE.get(dataset_mode, "SVL vs Balance Sheet")
    page.dataset_mode_var.set(label)
    on_selected = getattr(page, "_on_dataset_mode_selected", None)
    if callable(on_selected):
        on_selected()


def _select_pcb_repair_dialog_row(page: Any, *, picking_name: str = "") -> bool:
    widgets = dict(getattr(page, "_last_pcb_case1_dialog_widgets", {}) or {})
    tree = widgets.get("tree")
    row_by_iid = dict(widgets.get("row_by_iid", {}) or {})
    if tree is None or not row_by_iid:
        return False

    target_picking = normalize_text(picking_name).lower()

    def _iter_row_ids(parent: str = "") -> Iterator[str]:
        for item_id in tree.get_children(parent):
            if item_id in row_by_iid:
                yield item_id
            yield from _iter_row_ids(item_id)

    ordered_row_ids = list(_iter_row_ids(""))
    if not ordered_row_ids:
        return False
    candidate_row_ids = ordered_row_ids
    if target_picking:
        filtered_row_ids = [
            item_id
            for item_id in ordered_row_ids
            if normalize_text(row_by_iid.get(item_id, {}).get("picking_name", "")).lower() == target_picking
        ]
        if filtered_row_ids:
            candidate_row_ids = filtered_row_ids
    target_row_id = candidate_row_ids[0]
    tree.selection_set(target_row_id)
    tree.focus(target_row_id)
    try:
        tree.see(target_row_id)
    except Exception:  # noqa: BLE001
        pass
    try:
        tree.event_generate("<<TreeviewSelect>>")
    except Exception:  # noqa: BLE001
        pass
    return True


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    logger, log_path = configure_logging(verbose=bool(args.verbose), log_file=args.log_file or None)
    logger.info("Log file: %s", str(log_path) if log_path is not None else "(disabled)")
    config = _resolve_config(args)
    logger.info(
        "GUI inspect request: module=%s | company_id=%s | dataset_mode=%s | date_from=%s | date_to=%s",
        DISPLAY_NAME,
        config.company_id,
        config.dataset_mode,
        config.date_from or "-",
        config.date_to or "-",
    )
    app: DashboardWindow | None = None
    with _suppressed_messageboxes(logger), _suppressed_background_auth():
        try:
            app = DashboardWindow()
            root = app.root
            app.show_module(MODULE_ID)
            module = _resolve_svl_module(app)
            shell = module.get_shell()
            if shell is None:
                raise RuntimeError("Shell SVL Fix JE belum terinisialisasi.")
            page = shell.ensure_dashboard_mode()

            _wait_until(root, lambda: not bool(getattr(page, "_busy", False)), timeout_seconds=120.0, description="idle awal GUI")
            if config.database_profile_id:
                shell.set_database_profile_for_debug(config.database_profile_id)
                _wait_until(root, lambda: not bool(getattr(page, "_busy", False)), timeout_seconds=120.0, description="refresh database profile")

            _apply_dataset_mode(page, config.dataset_mode)
            page.date_from_var.set(config.date_from)
            page.date_to_var.set(config.date_to)
            page.load_companies(force=True)
            _wait_until(
                root,
                lambda: (not bool(getattr(page, "_busy", False))) and bool(getattr(page, "_company_by_label", {})),
                timeout_seconds=config.timeout_seconds,
                description="load company list",
            )
            if not page.debug_select_company(config.company_id):
                raise RuntimeError(f"Company ID {config.company_id} tidak ditemukan di dropdown GUI.")

            page.start_analysis()
            _wait_until(
                root,
                lambda: (
                    not bool(getattr(page, "_busy", False))
                    and bool(getattr(page, "_snapshot_interactive_ready", False))
                    and getattr(page, "_latest_snapshot", None) is not None
                ),
                timeout_seconds=config.timeout_seconds,
                description="analyze + render GUI",
            )
            if config.dataset_mode == PURCHASE_CYCLE_BALANCE_DATASET_MODE and config.selection_target != "none":
                selected = bool(
                    page.debug_select_pcb_sidebar_target(
                        picking_name=config.select_picking,
                        node_kind=config.selection_target,
                    )
                )
                logger.info(
                    "GUI sidebar selection: %s",
                    "ok" if selected else f"not found ({config.selection_target})",
                )
                _pump_gui(root, duration_seconds=0.4)
            if config.dataset_mode == PURCHASE_CYCLE_BALANCE_DATASET_MODE and config.collect_pcb_visible:
                page.collect_pcb_visible()
                logger.info(
                    "GUI PCB collection: collected visible rows, total=%s",
                    len(getattr(page, "_pcb_repair_collection", {}) or {}),
                )
                _pump_gui(root, duration_seconds=0.4)
            if config.dataset_mode == PURCHASE_CYCLE_BALANCE_DATASET_MODE and config.open_pcb_repair_dialog:
                page.open_pcb_repair_collection_dialog()
                _pump_gui(root, duration_seconds=0.4)
                selected_row = _select_pcb_repair_dialog_row(page, picking_name=config.select_picking)
                logger.info(
                    "GUI PCB repair dialog: %s",
                    "row selected" if selected_row else "opened without selectable row",
                )
                _pump_gui(root, duration_seconds=0.2)

            payload = app.build_gui_debug_snapshot(
                module_id=MODULE_ID,
                max_rows_per_tree=config.tree_row_limit,
                include_selected_cycle_payload=config.include_selected_cycle_payload,
            )
            _write_dump(config.dump_json, payload, logger)
        finally:
            if app is not None and not config.keep_window_open:
                try:
                    app.close()
                except Exception:  # noqa: BLE001
                    pass
    return 0
