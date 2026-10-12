"""Headless, preview-first adapter for New JE + Relink SVL."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime
import hashlib
import json
import logging
from pathlib import Path
import tempfile
from typing import Sequence

from smartscc_tools.core.global_config import GlobalPersistentState
from smartscc_tools.features.item_journal.runtime import configure_logging
from smartscc_tools.features.svl_fix_je.config import DEFAULT_JOURNAL_CODE, DEFAULT_REF_PREFIX
from smartscc_tools.features.svl_fix_je.dashboard_repair_service import (
    ResolvedAccount, ResolvedJournal, SvlDashboardRepairServiceAsync, SvlNewJeRelinkPlan,
)
from smartscc_tools.features.svl_fix_je.models import SvlDashboardRepairRow
from smartscc_tools.modules.svl_fix_je_module import _SvlFixJeStateStore, build_runtime_connection
from smartscc_tools.services.odoo.gateway import AsyncOdooJsonRpcClient


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python main.py svl-new-je-relink", description="Preview atau apply New JE + Relink SVL tanpa GUI.")
    parser.add_argument("--database-profile-id", default="")
    parser.add_argument("--company-id", type=int)
    parser.add_argument("--svl-id", type=int)
    parser.add_argument("--date", help="Tanggal journal YYYY-MM-DD; wajib pada preview.")
    parser.add_argument("--debit-account-code")
    parser.add_argument("--credit-account-code")
    parser.add_argument("--journal-code", default=DEFAULT_JOURNAL_CODE)
    parser.add_argument("--posting-mode", choices=["draft", "post"], default="draft")
    parser.add_argument("--reference")
    parser.add_argument("--line-label")
    parser.add_argument("--apply", action="store_true", help="Eksekusi rencana pada --preview-json yang sudah disetujui.")
    parser.add_argument("--preview-json", help="File preview dari command ini; wajib bersama --apply.")
    parser.add_argument("--output-json", help="Path baru untuk preview/hasil; default logs/manual_odoo/.")
    parser.add_argument("--log-file", default="")
    parser.add_argument("--verbose", action="store_true")
    return parser


@contextmanager
def _apply_lock(plan: SvlNewJeRelinkPlan):
    # ponytail: one-host lock. Writers on other computers must still be serialized externally.
    key = hashlib.sha256(f"{plan.database}:{plan.row.company_id}:{plan.row.svl_id}".encode()).hexdigest()
    directory = Path(tempfile.gettempdir()) / "smartscc_tools" / "svl_relink_locks"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / key).open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        import os
        if os.name == "nt":
            import msvcrt
            lock, unlock = lambda: msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1), lambda: msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            lock, unlock = lambda: fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB), lambda: fcntl.flock(handle, fcntl.LOCK_UN)
        try:
            lock()
        except OSError as exc:
            raise RuntimeError("Apply SVL yang sama sedang berjalan pada komputer ini.") from exc
        try:
            yield
        finally:
            handle.seek(0)
            unlock()


def _read_approved(path: str) -> tuple[str, str, SvlNewJeRelinkPlan]:
    preview_path = Path(path)
    if preview_path.stat().st_size > 1024 * 1024:
        raise ValueError("File terlalu besar untuk preview repair; jangan gunakan snapshot dashboard.")
    data = json.loads(preview_path.read_text(encoding="utf-8"))
    if data.get("schema") != "svl-new-je-relink/v1" or data.get("status") != "preview":
        raise ValueError("File bukan preview New JE + Relink SVL yang valid.")
    if not data.get("connection_fingerprint"):
        raise ValueError("Preview belum menyimpan identitas koneksi; buat preview baru.")
    item = data["plan"]
    plan = SvlNewJeRelinkPlan(database=item["database"], row=SvlDashboardRepairRow(**item["row"]),
                            debit_account=ResolvedAccount(**item["debit_account"]),
                            credit_account=ResolvedAccount(**item["credit_account"]),
                            journal=ResolvedJournal(**item["journal"]), source_state=item["source_state"])
    return data["database_profile_id"], data["connection_fingerprint"], plan


async def _run(args, *, output: Path, logger: logging.Logger) -> dict:
    state = GlobalPersistentState().load()
    if args.apply:
        profile_id, expected_connection, approved = _read_approved(args.preview_json)
    else:
        profile_id = args.database_profile_id
        module = _SvlFixJeStateStore(state).load()
        prefix = module.last_prefix.strip() or DEFAULT_REF_PREFIX
        row = SvlDashboardRepairRow(row_key=f"svl:{args.svl_id}", company_id=args.company_id,
            svl_id=args.svl_id, item_product_id=0, item_code="", item_name="", amount=0,
            date=args.date, debit_account_code=args.debit_account_code, credit_account_code=args.credit_account_code,
            journal_code=args.journal_code, posting_mode=args.posting_mode,
            reference=args.reference or f"{prefix} New JE + Relink SVL",
            line_label=args.line_label or f"{prefix} New JE + Relink SVL")
    settings, config = build_runtime_connection(state, logger, database_profile_id=profile_id)
    connection = hashlib.sha256(config.base_url.rstrip("/").encode("utf-8")).hexdigest()
    if args.apply and (config.database != approved.database or connection != expected_connection):
        raise ValueError("Koneksi/database efektif berubah sejak preview. Buat preview baru.")
    async with AsyncOdooJsonRpcClient(config=config, settings=settings, logger=logger) as rpc:
        service = SvlDashboardRepairServiceAsync(rpc=rpc, logger=logger)
        if args.apply:
            with _apply_lock(approved):
                output.write_text(json.dumps({"schema": "svl-new-je-relink/v1", "status": "applying", "database_profile_id": profile_id, "connection_fingerprint": connection, "plan": asdict(approved)}, indent=2), encoding="utf-8")
                result = await service.execute_new_je_relink(approved)
            return {"schema": "svl-new-je-relink/v1", "status": "error" if result.error_count else "executed",
                    "database_profile_id": profile_id, "connection_fingerprint": connection, "plan": asdict(approved), "result": asdict(result)}
        plan = await service.prepare_new_je_relink(row, database=config.database)
        return {"schema": "svl-new-je-relink/v1", "status": "preview", "database_profile_id": profile_id,
                "connection_fingerprint": connection, "plan": asdict(plan), "message": "Preview saja; belum ada write. Review akun, nominal, tanggal dan posting sebelum --apply."}


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.apply:
        if not args.preview_json:
            parser.error("--apply membutuhkan --preview-json yang sudah direview/disetujui.")
        if any((args.database_profile_id, args.company_id, args.svl_id, args.date,
                args.debit_account_code, args.credit_account_code, args.reference, args.line_label)) or args.journal_code != DEFAULT_JOURNAL_CODE or args.posting_mode != "draft":
            parser.error("Apply mengambil parameter dari preview; jangan kirim override parameter repair.")
    elif args.preview_json or not all((args.database_profile_id, args.company_id and args.company_id > 0,
            args.svl_id and args.svl_id > 0, args.date, args.debit_account_code, args.credit_account_code)):
        parser.error("Preview membutuhkan profile, company-id, svl-id, date, debit-account-code dan credit-account-code.")
    output = Path(args.output_json or f"logs/manual_odoo/svl_new_je_{datetime.now():%Y%m%d_%H%M%S_%f}.json").expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with output.open("x", encoding="utf-8") as handle:
            handle.write("{}")  # verify a writable receipt path before any Odoo mutation
    except FileExistsError:
        parser.error("Output sudah ada; gunakan path baru agar bukti lama tetap tersimpan.")
    logger, _ = configure_logging(verbose=args.verbose, log_file=args.log_file or str(output.with_suffix(".log")))
    try:
        result = asyncio.run(_run(args, output=output, logger=logger))
    except Exception as exc:
        pending = json.loads(output.read_text(encoding="utf-8"))
        result = {**pending, "status": "error", "message": str(exc),
                  "action": "Review receipt/JE existing sebelum retry bila apply sudah dimulai."}
    output.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    print(f"Receipt: {output}")
    return 1 if result["status"] == "error" else 0
