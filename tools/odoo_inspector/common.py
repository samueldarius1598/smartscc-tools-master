#!/usr/bin/env python3
"""Shared helpers for live Odoo inspection scripts."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import sys
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartscc_tools.core.global_config import GlobalPersistentState, GlobalSettings
from smartscc_tools.features.item_journal.config import build_runtime_settings
from smartscc_tools.services.gas.config_client import fetch_odoo_config
from smartscc_tools.services.odoo.gateway import AsyncOdooJsonRpcClient, OdooConfig
from smartscc_tools.services.odoo.profiles import FOLLOW_GLOBAL_PROFILE_ID, resolve_database_selection


DEFAULT_ARTIFACT_DIR = PROJECT_ROOT / "logs" / "odoo_inspector"


@dataclass
class OdooToolConnection:
    client: AsyncOdooJsonRpcClient
    config: OdooConfig
    settings: Any
    global_settings: GlobalSettings
    database_profile_id: str
    logger: logging.Logger

    async def close(self) -> None:
        await self.client.close()


def build_logger(name: str, *, verbose: bool = False) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.handlers.clear()
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", "%H:%M:%S"))
    logger.addHandler(handler)
    logger.propagate = False
    return logger


def add_common_connection_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--database-profile",
        default=FOLLOW_GLOBAL_PROFILE_ID,
        help="Database profile id, explicit database value, or Follow Global Default.",
    )
    parser.add_argument("--max-concurrency", type=int, default=4, help="Read RPC concurrency limit.")
    parser.add_argument("--output-dir", default=str(DEFAULT_ARTIFACT_DIR), help="Directory for JSON artefacts.")
    parser.add_argument("--no-artifact", action="store_true", help="Print only; do not write JSON artefact.")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging.")


def parse_json_domain(raw: str | None) -> list[Any]:
    text = str(raw or "").strip()
    if not text:
        return []
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError(f"Domain must be JSON, got: {exc}") from exc
    if not isinstance(value, list):
        raise argparse.ArgumentTypeError("Domain JSON must be a list.")
    return value


def parse_csv_values(raw: str | None) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for part in str(raw or "").split(","):
        clean = part.strip()
        if clean and clean not in seen:
            seen.add(clean)
            values.append(clean)
    return values


def utc_now_token() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%SZ")


def safe_stem(value: str) -> str:
    out = str(value or "").strip()
    for char in '\\/:*?"<>| ':
        out = out.replace(char, "_")
    return out.strip("_") or "odoo"


def write_json_artifact(payload: dict[str, Any], *, output_dir: str | Path, prefix: str) -> Path:
    target_dir = Path(output_dir).expanduser()
    if not target_dir.is_absolute():
        target_dir = PROJECT_ROOT / target_dir
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{utc_now_token()}_{safe_stem(prefix)}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return path


async def open_tool_connection(
    *,
    database_profile_id: str = FOLLOW_GLOBAL_PROFILE_ID,
    max_concurrency: int = 4,
    verbose: bool = False,
) -> OdooToolConnection:
    logger = build_logger("tools.odoo_inspector", verbose=verbose)
    settings, _ = build_runtime_settings(preset="safe-fast", set_args=[])
    config = fetch_odoo_config(settings=settings)
    global_settings = GlobalPersistentState().load()
    effective_database = resolve_database_selection(
        profiles=global_settings.database_profiles,
        module_profile_id=database_profile_id,
        default_profile_id=global_settings.default_database_profile_id,
        gas_default_database=config.database,
    )
    if effective_database:
        config.database = effective_database
    logger.info("Using Odoo database: %s", config.database)
    client = AsyncOdooJsonRpcClient(
        config=config,
        settings=settings,
        logger=logger,
        max_concurrency=max(1, int(max_concurrency or 1)),
    )
    return OdooToolConnection(
        client=client,
        config=config,
        settings=settings,
        global_settings=global_settings,
        database_profile_id=database_profile_id,
        logger=logger,
    )


def build_base_payload(connection: OdooToolConnection, *, action: str) -> dict[str, Any]:
    return {
        "action": action,
        "generated_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "database": connection.config.database,
        "database_profile_id": connection.database_profile_id,
        "source": "tools/odoo_inspector",
    }

