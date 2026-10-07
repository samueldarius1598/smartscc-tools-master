"""CLI interface for Item Journal migration tool."""

from __future__ import annotations

import asyncio
import argparse
from datetime import datetime
import sys
from typing import Any, Sequence

from smartscc_tools.features.item_journal.config import BusinessUploadSettings, RuntimeSettings, build_runtime_settings
from smartscc_tools.features.item_journal.entrypoints.common import build_copy_name_stem_from_summary, log_save_result
from smartscc_tools.features.item_journal.messaging import BusinessNarrator, stage_to_business_message
from smartscc_tools.features.item_journal.observability.audit import AuditSink, migrate_audit_jsonl, migrate_audit_sheet
from smartscc_tools.features.item_journal.observability.perf import PerfReporter, PerfThresholds
from smartscc_tools.services.odoo.gateway import AsyncOdooJsonRpcClient, fetch_odoo_config
from smartscc_tools.features.item_journal.runtime import RunControl, configure_logging, get_technical_logger
from smartscc_tools.features.item_journal.services.date_ops import LockDateServiceAsync
from smartscc_tools.features.item_journal.services.upload import ItemJournalServiceAsync
from smartscc_tools.features.item_journal.workbook import ItemJournalWorkbookRepo


class _CliProgressView:
    def __init__(self) -> None:
        self._rich_enabled = False
        self._last_plain_line = ""
        self._progress = None
        self._task_id = None
        self._console = None
        self._table_class = None
        self._transfer_done_count = 0
        self._slow_warn_count = 0
        self._last_message = ""
        if not sys.stdout.isatty():
            return
        try:
            from rich.console import Console
            from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn, TimeElapsedColumn
            from rich.table import Table

            console = Console()
            self._progress = Progress(
                SpinnerColumn(),
                TextColumn("{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                TimeElapsedColumn(),
                console=console,
                transient=False,
            )
            self._console = console
            self._table_class = Table
            self._rich_enabled = True
        except Exception:
            self._rich_enabled = False
            self._progress = None
            self._console = None
            self._table_class = None

    def start(self) -> None:
        if not self._rich_enabled or self._progress is None:
            return
        self._progress.start()
        self._task_id = self._progress.add_task("Menyiapkan upload...", total=1, completed=0)

    def stop(self) -> None:
        if not self._rich_enabled or self._progress is None:
            return
        self._progress.stop()

    def on_progress(self, payload: dict) -> None:
        stage = str(payload.get("stage", "") or "")
        message = stage_to_business_message(stage, payload)
        current = int(payload.get("current", 0) or 0)
        total = max(1, int(payload.get("total", 0) or 1))
        slow_level = str(payload.get("slow_level", "") or "").lower()
        self._last_message = message
        if stage == "TRANSFER_DONE":
            self._transfer_done_count += 1
        if slow_level == "warn":
            self._slow_warn_count += 1

        if self._rich_enabled and self._progress is not None:
            if self._task_id is None:
                self._task_id = self._progress.add_task("Menyiapkan upload...", total=total, completed=0)
            color = "yellow" if slow_level == "warn" else "cyan"
            description = f"[{color}]{message}[/{color}]"
            self._progress.update(
                self._task_id,
                description=description,
                total=total,
                completed=max(0, min(current, total)),
            )
            return

        plain = f"{message} ({current}/{total})" if current > 0 else message
        if plain == self._last_plain_line:
            return
        self._last_plain_line = plain
        print(plain)

    def show_summary(self, summary: dict[str, Any]) -> None:
        rows_success = int(summary.get("rows_success", 0) or 0)
        rows_error = int(summary.get("rows_error", 0) or 0)
        rows_stopped = int(summary.get("rows_stopped", 0) or 0)
        if self._rich_enabled and self._console is not None and self._table_class is not None:
            table = self._table_class(title="Ringkasan Upload")
            table.add_column("Metric")
            table.add_column("Nilai", justify="right")
            table.add_row("Transfer Selesai", str(self._transfer_done_count))
            table.add_row("Baris Sukses", str(rows_success), style="green")
            table.add_row("Baris Error", str(rows_error), style="red" if rows_error > 0 else "")
            table.add_row("Baris Stopped", str(rows_stopped), style="yellow" if rows_stopped > 0 else "")
            table.add_row("Peringatan Koneksi (>30s)", str(self._slow_warn_count), style="yellow" if self._slow_warn_count > 0 else "")
            if self._last_message:
                table.add_row("Status Terakhir", self._last_message)
            self._console.print(table)
            return
        print(
            "Ringkasan Upload | "
            f"Transfer={self._transfer_done_count} | "
            f"Sukses={rows_success} | "
            f"Error={rows_error} | "
            f"Stopped={rows_stopped} | "
            f"WarnSlow={self._slow_warn_count}"
        )


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="item-journal-odoo",
        description="Migrasi proses VBA Item Journal ke Python.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_common_runtime_flags(target: argparse.ArgumentParser) -> None:
        target.add_argument("--workbook", required=True, help="Path file workbook .xlsm")
        target.add_argument(
            "--save-mode",
            choices=["in-place", "copy", "ask"],
            default="ask",
            help="Mode penyimpanan workbook hasil.",
        )
        target.add_argument("--db-override", default="", help="Override database Odoo (opsional).")
        target.add_argument(
            "--preset",
            choices=["safe", "safe-fast", "fast", "debug", "custom"],
            default="safe-fast",
            help="Preset runtime constants.",
        )
        target.add_argument(
            "--set",
            action="append",
            default=[],
            metavar="KEY=VALUE",
            help="Override Business Keys konfigurasi upload. Bisa diulang.",
        )
        target.add_argument("--log-file", default="", help="Path file log output (opsional, kosong=tanpa file).")
        target.add_argument("--verbose", action="store_true", help="Mode log detail.")
        target.add_argument(
            "--max-concurrency",
            type=int,
            default=8,
            help="Maksimum konkurensi untuk read I/O async.",
        )
        target.add_argument(
            "--stop-mode",
            choices=["run-to-batch-stop"],
            default="run-to-batch-stop",
            help="Mode stop terkontrol.",
        )
        target.add_argument(
            "--audit-level",
            choices=["summary", "item"],
            default="item",
            help="Level audit event.",
        )
        target.add_argument("--audit-file", default="", help="Path file audit JSONL (opsional, kosong=tanpa file).")

    check_lock = subparsers.add_parser("check-lock", help="Ambil fiscalyear_lock_date (read-only, tanpa ubah workbook).")
    add_common_runtime_flags(check_lock)

    upload = subparsers.add_parser("upload", help="Upload Internal Transfer dari sheet Item Journal.")
    add_common_runtime_flags(upload)
    upload.add_argument("--dry-run", action="store_true", help="Simulasi tanpa create/write ke Odoo.")
    upload.add_argument(
        "--perf-profile",
        choices=["aggressive", "balanced", "conservative"],
        default="aggressive",
        help="Profil threshold realtime perf reporting.",
    )
    upload.add_argument(
        "--perf-report-file",
        default="",
        help="Path file JSONL perf report v2 (opsional, kosong=tanpa file).",
    )
    upload.add_argument(
        "--perf-sheet",
        choices=["on", "off"],
        default="on",
        help="Tulis perf summary ke sheet audit workbook.",
    )

    gui = subparsers.add_parser("gui", help="Jalankan aplikasi desktop GUI.")
    gui.add_argument("--log-file", default="", help="Path file log output (opsional, kosong=tanpa file).")
    gui.add_argument("--verbose", action="store_true", help="Mode log detail.")

    migrate = subparsers.add_parser("migrate-audit", help="Migrasi audit schema lama ke schema v2.")
    migrate.add_argument("--input", required=True, help="Path file input audit JSONL lama/baru.")
    migrate.add_argument("--output", required=True, help="Path output file audit JSONL v2.")
    migrate.add_argument("--sheet-input", default="", help="Path workbook sumber untuk migrasi sheet audit.")
    migrate.add_argument("--sheet-output", default="", help="Path workbook target untuk migrasi sheet audit.")

    return parser


def _ask_save_mode_cli() -> str:
    while True:
        answer = input("Pilih save mode (in-place/copy): ").strip().lower()
        if answer in {"in-place", "copy"}:
            return answer
        print("Input tidak valid. Masukkan 'in-place' atau 'copy'.")


def _build_settings(
    preset: str,
    set_values: Sequence[str],
) -> tuple[RuntimeSettings, BusinessUploadSettings]:
    return build_runtime_settings(preset=preset, set_args=set_values)


def run_check_lock(args: argparse.Namespace) -> int:
    settings, _business_settings = _build_settings(args.preset, args.set)
    logger, log_path = configure_logging(verbose=args.verbose, log_file=args.log_file or None)
    logger.info("Log file: %s", str(log_path) if log_path is not None else "(disabled)")

    repo = ItemJournalWorkbookRepo(args.workbook)
    config = fetch_odoo_config(settings=settings)
    if args.db_override.strip():
        config.database = args.db_override.strip()
        logger.info("DB override aktif: %s", config.database)

    async def _run() -> object:
        async with AsyncOdooJsonRpcClient(
            config=config,
            settings=settings,
            logger=logger,
            max_concurrency=max(1, int(args.max_concurrency)),
        ) as rpc_async:
            service_async = LockDateServiceAsync(rpc=rpc_async, logger=logger)
            return await service_async.check_close_acc_date(repo)

    lock_date = asyncio.run(_run())

    logger.info(
        "check-lock berjalan read-only; workbook tidak disimpan. save-mode=%s diabaikan.",
        args.save_mode,
    )
    if lock_date:
        logger.info("Check lock date selesai. lock_date=%s", lock_date)
    else:
        logger.warning("Check lock date selesai. lock_date kosong")
    return 0


def run_upload(args: argparse.Namespace) -> int:
    settings, _business_settings = _build_settings(args.preset, args.set)
    perf_thresholds = PerfThresholds.from_profile(args.perf_profile)
    settings.perf_warn_rpc_ms = perf_thresholds.warn_rpc_ms
    settings.perf_critical_rpc_ms = perf_thresholds.critical_rpc_ms
    settings.perf_stall_warn_sec = perf_thresholds.warn_stall_sec
    settings.perf_stall_critical_sec = perf_thresholds.critical_stall_sec
    settings.perf_heartbeat_sec = perf_thresholds.heartbeat_sec
    settings.perf_jsonl_flush_every_events = perf_thresholds.flush_every_events
    settings.perf_top_causes = perf_thresholds.top_causes

    logger, log_path = configure_logging(verbose=args.verbose, log_file=args.log_file or None)
    technical_logger = get_technical_logger()
    narrator = BusinessNarrator(user_logger=logger, technical_logger=technical_logger)
    logger.info("Log file: %s", str(log_path) if log_path is not None else "(disabled)")
    configured_remap_mode = str(getattr(settings, "stj_remap_mode", "") or "").strip().lower() or "-"
    if not bool(args.dry_run) and configured_remap_mode != "conservative":
        logger.warning(
            "Preflight upload: STJ_REMAP_MODE=%s akan dipaksa ke conservative saat run production (dry_run=false).",
            configured_remap_mode,
        )
    narrator.info("UPLOAD_STARTED")

    repo = ItemJournalWorkbookRepo(args.workbook)
    config = fetch_odoo_config(settings=settings)
    if args.db_override.strip():
        config.database = args.db_override.strip()
        logger.info("DB override aktif: %s", config.database)

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    progress_view = _CliProgressView()
    run_control = RunControl(
        stop_mode=args.stop_mode,
        progress_callback=lambda payload: progress_view.on_progress(dict(payload)),
    )
    perf_reporter = PerfReporter(
        run_id=run_id,
        profile=args.perf_profile,
        events_file=args.perf_report_file or None,
        logger=logger,
        technical_logger=technical_logger,
        perf_sheet_enabled=(args.perf_sheet == "on"),
        persist_files=bool((args.perf_report_file or "").strip()),
    )
    perf_reporter.thresholds.flush_every_events = max(1, int(settings.perf_jsonl_flush_every_events))
    perf_reporter.thresholds.top_causes = max(1, int(settings.perf_top_causes))
    audit_sink = AuditSink(
        run_id=run_id,
        audit_level=args.audit_level,
        audit_file=args.audit_file or None,
        flush_every=settings.audit_flush_every_events,
        persist_file=bool((args.audit_file or "").strip()),
    )

    async def _run() -> dict:
        async with AsyncOdooJsonRpcClient(
            config=config,
            settings=settings,
            logger=logger,
            max_concurrency=max(1, int(args.max_concurrency)),
            rpc_telemetry_callback=perf_reporter.handle_rpc_telemetry,
        ) as rpc_async:
            service_async = ItemJournalServiceAsync(
                rpc=rpc_async,
                settings=settings,
                logger=logger,
                narrator=narrator,
                run_control=run_control,
                audit_sink=audit_sink,
                run_id=run_id,
                perf_reporter=perf_reporter,
            )
            return await service_async.run_upload(repo=repo, dry_run=bool(args.dry_run))

    summary: dict[str, Any] | None = None
    progress_view.start()
    try:
        summary = asyncio.run(_run())
    finally:
        progress_view.stop()
        perf_summary = perf_reporter.close()
        logger.info(
            "Perf events file: %s",
            str(perf_reporter.events_path) if perf_reporter.events_path is not None else "(disabled)",
        )
        logger.info(
            "Perf summary file: %s",
            str(perf_reporter.summary_path) if perf_reporter.summary_path is not None else "(disabled)",
        )
        audit_sink.close()
        logger.info("Audit file: %s", str(audit_sink.path) if audit_sink.path is not None else "(disabled)")
        if args.perf_sheet == "on":
            repo.append_perf_summary(perf_summary)

    if summary is None:
        raise RuntimeError("Upload tidak menghasilkan summary.")

    progress_view.show_summary(summary)
    copy_name_stem = build_copy_name_stem_from_summary(summary)
    save_result = repo.save(
        mode=args.save_mode,
        ask_decision=_ask_save_mode_cli,
        copy_name_stem=copy_name_stem,
    )
    log_save_result(logger, args.save_mode, save_result)
    narrator.info("UPLOAD_COMPLETED")
    logger.info("File hasil upload tersimpan: %s", save_result.path)
    return 0


def run_gui(args: argparse.Namespace) -> int:
    from smartscc_tools.features.item_journal.entrypoints.gui import launch_gui

    return launch_gui(verbose=bool(args.verbose), log_file=args.log_file or None)


def run_migrate_audit(args: argparse.Namespace) -> int:
    result = migrate_audit_jsonl(args.input, args.output)
    print(
        f"Migrasi JSONL selesai. input={result['input']} output={result['output']} "
        f"total={result['total']} migrated={result['migrated']}"
    )
    if args.sheet_input.strip() and args.sheet_output.strip():
        sheet_result = migrate_audit_sheet(args.sheet_input.strip(), args.sheet_output.strip())
        print(
            f"Migrasi sheet selesai. input={sheet_result['input']} output={sheet_result['output']} "
            f"total={sheet_result['total']} migrated={sheet_result['migrated']}"
        )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    if args.command == "check-lock":
        return run_check_lock(args)
    if args.command == "upload":
        return run_upload(args)
    if args.command == "gui":
        return run_gui(args)
    if args.command == "migrate-audit":
        return run_migrate_audit(args)
    parser.error("Command tidak dikenal.")
    return 2
