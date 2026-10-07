"""Configuration defaults for Update Standard Cost module."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from smartscc_tools.branding import ASSETS_DIR
from smartscc_tools.services.odoo.profiles import (
    FOLLOW_GLOBAL_PROFILE_ID,
    normalize_module_database_profile_id,
    resolve_database_selection,
)
from smartscc_tools.core.global_config import GlobalSettings


MODULE_ID = "update_std_cost"
DISPLAY_NAME = "Update Standard Cost Item - Odoo"
DEFAULT_MODE = "both"
CONFIG_GID = 1746209771
AREA_GID = 348037279
LOG_SHEET_NAME = "LOG"
PREPARATION_SHEET_NAME = "Preparation Cost"
WRITE_CHUNK_SIZE = 1000
CODE_CHUNK_SIZE = 5000

TEMPLATE_FILENAME = "Exc- List Update Standard Cost All Company Odoo.xlsm"
TEMPLATE_PATH = ASSETS_DIR / "update_std_cost" / TEMPLATE_FILENAME


@dataclass
class UpdateStdCostSettings:
    database_profile_id: str = FOLLOW_GLOBAL_PROFILE_ID
    last_workbook_file: str = ""
    mode: str = DEFAULT_MODE
    execute_dry_run: bool = False
    logs_section_open: bool = False


def normalize_mode(value: str | None) -> str:
    clean = str(value or "").strip().lower()
    if clean in {"template", "variant", "both"}:
        return clean
    return DEFAULT_MODE


def normalize_database_profile_id(value: str | None) -> str:
    return normalize_module_database_profile_id(value)


def resolve_effective_database(
    *,
    database_profile_id: str,
    global_settings: GlobalSettings,
    default_database: str,
) -> str:
    return resolve_database_selection(
        profiles=global_settings.database_profiles,
        module_profile_id=database_profile_id,
        default_profile_id=global_settings.default_database_profile_id,
        gas_default_database=default_database,
    )


def default_input_browse_dir(global_settings: GlobalSettings, workbook_path: str) -> str:
    workbook = Path(str(workbook_path or "").strip())
    if workbook.is_file():
        return str(workbook.parent)
    if workbook.parent and str(workbook.parent) not in {".", ""}:
        return str(workbook.parent)
    return str(global_settings.default_input_dir or "").strip()
