"""Headless CLI for SVL dashboard analysis."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Sequence

from smartscc_tools.core.global_config import GlobalPersistentState
from smartscc_tools.features.item_journal.runtime import configure_logging
from smartscc_tools.features.item_journal.utils import normalize_text
from smartscc_tools.features.svl_fix_je.config import (
    PURCHASE_CYCLE_BALANCE_DATASET_MODE,
    SvlFixJeSettings,
    normalize_dashboard_dataset_mode,
)
from smartscc_tools.features.svl_fix_je.dashboard_service import SvlDashboardServiceAsync
from smartscc_tools.features.svl_fix_je.models import SvlDashboardRequest, SvlDashboardSnapshot
from smartscc_tools.modules.svl_fix_je_module import _SvlFixJeStateStore, build_runtime_connection
from smartscc_tools.services.odoo.gateway import AsyncOdooJsonRpcClient


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python main.py svl-dashboard-analyze",
        description="Jalankan SVL Dashboard analyze tanpa GUI.",
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
    parser.add_argument("--db-override", default="", help="Override database akhir setelah profile/GAS di-resolve.")
    parser.add_argument("--pcb-problem-codes", default="", help="Comma-separated problem account codes.")
    parser.add_argument("--pcb-info-codes", default="", help="Comma-separated info account codes.")
    parser.add_argument(
        "--include-inventory-accounts",
        choices=["on", "off"],
        default="",
        help="Override include inventory accounts.",
    )
    parser.add_argument(
        "--include-non-inventory-accounts",
        choices=["on", "off"],
        default="",
        help="Override include non-inventory accounts.",
    )
    parser.add_argument("--log-file", default="", help="Path file log output.")
    parser.add_argument("--dump-json", default="", help="Simpan full snapshot JSON ke file.")
    parser.add_argument(
        "--pcb-preview-limit",
        type=int,
        default=0,
        help="Tampilkan preview N cycle teratas untuk mode Balance Cycle Pembelian.",
    )
    parser.add_argument("--verbose", action="store_true", help="Mode log detail.")
    return parser


def _configured_inventory_coa_codes(global_settings) -> list[str]:
    codes: list[str] = []
    seen: set[str] = set()
    for entry in getattr(global_settings, "inventory_coa_entries", []) or []:
        code = normalize_text(getattr(entry, "coa_code", "")).strip().upper()
        if not code or code in seen:
            continue
        seen.add(code)
        codes.append(code)
    return codes


def _parse_codes(value: str, fallback: str) -> frozenset[str]:
    source = normalize_text(value) or normalize_text(fallback)
    return frozenset(code.strip() for code in source.split(",") if code.strip())


def _resolve_request(args: argparse.Namespace) -> tuple[SvlDashboardRequest, object, SvlFixJeSettings]:
    global_settings = GlobalPersistentState().load()
    module_settings = _SvlFixJeStateStore(global_settings).load()
    company_id = int(args.company_id or module_settings.dashboard_company_id or 0)
    if company_id <= 0:
        raise RuntimeError("Company ID belum tersedia. Set di GUI dulu atau kirim --company-id.")
    dataset_mode = normalize_dashboard_dataset_mode(
        normalize_text(args.dataset_mode) or normalize_text(module_settings.dashboard_dataset_mode)
    )
    include_inventory_accounts = (
        module_settings.dashboard_include_inventory_accounts
        if not normalize_text(args.include_inventory_accounts)
        else normalize_text(args.include_inventory_accounts).lower() == "on"
    )
    include_non_inventory_accounts = (
        module_settings.dashboard_include_non_inventory_accounts
        if not normalize_text(args.include_non_inventory_accounts)
        else normalize_text(args.include_non_inventory_accounts).lower() == "on"
    )
    request = SvlDashboardRequest(
        database="",
        company_id=company_id,
        date_from=normalize_text(args.date_from) or normalize_text(module_settings.dashboard_date_from),
        date_to=normalize_text(args.date_to) or normalize_text(module_settings.dashboard_date_to),
        inventory_coa_codes=_configured_inventory_coa_codes(global_settings),
        dataset_mode=dataset_mode,
        include_inventory_accounts=bool(include_inventory_accounts),
        include_non_inventory_accounts=bool(include_non_inventory_accounts),
        pcb_problem_codes=_parse_codes(args.pcb_problem_codes, module_settings.pcb_problem_codes),
        pcb_info_codes=_parse_codes(args.pcb_info_codes, module_settings.pcb_info_codes),
    )
    return request, global_settings, module_settings


def _log_snapshot_summary(logger: logging.Logger, snapshot: SvlDashboardSnapshot) -> None:
    if normalize_text(snapshot.dataset_mode) == PURCHASE_CYCLE_BALANCE_DATASET_MODE:
        cycles = list(snapshot.purchase_cycles or [])
        problem_count = sum(1 for cycle in cycles if normalize_text(cycle.cycle_status) == "problem")
        partial_count = sum(1 for cycle in cycles if normalize_text(cycle.cycle_status) == "partial")
        healthy_count = sum(1 for cycle in cycles if normalize_text(cycle.cycle_status) == "healthy")
        document_counts: dict[str, int] = {}
        for cycle in cycles:
            key = (
                normalize_text(getattr(cycle, "document_classification_label", ""))
                or normalize_text(getattr(cycle, "document_classification", ""))
                or "Unclassified"
            )
            document_counts[key] = document_counts.get(key, 0) + 1
        document_order = (
            "Purchase-Backed",
            "Purchase-Likely",
            "Non-Purchase / Intercompany",
            "Unclassified",
        )
        logger.info(
            "CLI snapshot summary: %s cycles | %s problem | %s partial | %s healthy",
            len(cycles),
            problem_count,
            partial_count,
            healthy_count,
        )
        logger.info(
            "CLI document flow: %s",
            ", ".join(
                f"{label} {count}"
                for label in document_order
                if (count := document_counts.get(label, 0)) > 0
            )
            or "none",
        )
        return
    logger.info(
        "CLI snapshot summary: %s items | %s warnings",
        len(list(snapshot.items or [])),
        len(list(snapshot.warnings or [])),
    )


def _json_ready(value):
    if is_dataclass(value):
        return _json_ready(asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_ready(val) for key, val in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return [_json_ready(item) for item in sorted(value, key=lambda item: repr(item))]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _write_snapshot_dump(path_value: str, snapshot: SvlDashboardSnapshot, logger: logging.Logger) -> None:
    dump_path = Path(path_value).expanduser()
    dump_path.parent.mkdir(parents=True, exist_ok=True)
    dump_path.write_text(
        json.dumps(_json_ready(snapshot), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("CLI snapshot dump written: %s", dump_path)


def _log_pcb_preview(logger: logging.Logger, snapshot: SvlDashboardSnapshot, limit: int) -> None:
    if limit <= 0 or normalize_text(snapshot.dataset_mode) != PURCHASE_CYCLE_BALANCE_DATASET_MODE:
        return
    cycles = list(snapshot.purchase_cycles or [])
    if not cycles:
        logger.info("CLI PCB preview: tidak ada cycle.")
        return
    for index, cycle in enumerate(cycles[: max(0, int(limit or 0))], start=1):
        logger.info(
            "CLI PCB preview #%s: [%s] [%s] %s | partner=%s | items=%s | audit=%s | case=%s | patterns=%s",
            index,
            normalize_text(getattr(cycle, "cycle_status", "")) or "-",
            normalize_text(getattr(cycle, "document_classification_label", "")) or normalize_text(getattr(cycle, "document_classification", "")) or "-",
            normalize_text(getattr(cycle, "picking_name", "")) or f"Picking #{int(getattr(cycle, 'picking_id', 0) or 0)}",
            normalize_text(getattr(cycle, "partner_name", "")) or "-",
            len(list(getattr(cycle, "item_rows", None) or [])),
            len(list(getattr(cycle, "adjustment_audit_rows", None) or [])),
            normalize_text(getattr(cycle, "primary_case", "")) or "-",
            len(list(getattr(cycle, "issue_patterns", None) or [])),
        )


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    logger, log_path = configure_logging(verbose=bool(args.verbose), log_file=args.log_file or None)
    logger.info("Log file: %s", str(log_path) if log_path is not None else "(disabled)")
    request, global_settings, module_settings = _resolve_request(args)
    profile_id = normalize_text(args.database_profile_id) or normalize_text(module_settings.database_profile_id)
    settings, config = build_runtime_connection(
        global_settings,
        logger,
        database_profile_id=profile_id,
    )
    if normalize_text(args.db_override):
        config.database = normalize_text(args.db_override)
        logger.info("DB override aktif: %s", config.database)
    request.database = config.database
    logger.info(
        "CLI analyze request: db=%s | company_id=%s | dataset_mode=%s | date_from=%s | date_to=%s",
        request.database,
        request.company_id,
        request.dataset_mode,
        request.date_from or "-",
        request.date_to or "-",
    )

    async def _run() -> SvlDashboardSnapshot:
        async with AsyncOdooJsonRpcClient(config=config, settings=settings, logger=logger) as rpc:
            service = SvlDashboardServiceAsync(rpc=rpc, logger=logger)
            return await service.analyze(request)

    snapshot = asyncio.run(_run())
    _log_snapshot_summary(logger, snapshot)
    _log_pcb_preview(logger, snapshot, int(args.pcb_preview_limit or 0))
    if normalize_text(args.dump_json):
        _write_snapshot_dump(str(args.dump_json), snapshot, logger)
    return 0
