"""Configuration defaults for SVL Fix JE module."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from smartscc_tools.features.item_journal.utils import normalize_text
from smartscc_tools.branding import ASSETS_DIR
from smartscc_tools.services.odoo.profiles import (
    FOLLOW_GLOBAL_PROFILE_ID,
    normalize_module_database_profile_id,
    resolve_database_selection,
)
from smartscc_tools.core.global_config import GlobalSettings


MODULE_ID = "svl_fix_je"
DISPLAY_NAME = "Fixing Unlink SVL - Odoo"
SHEET_NAME = "SVL_Fix"
DEFAULT_JOURNAL_CODE = "STJ"
DEFAULT_REF_PREFIX = "FIX-SVL"
DEFAULT_MAX_WORKERS = 10
RESULT_SHEET_NAME = "SVL Fix Results"
DEFAULT_VIEW_MODE = "upload"
DEFAULT_DASHBOARD_DATASET_MODE = "issues"
PURCHASE_CYCLE_BALANCE_DATASET_MODE = "purchase_cycle_balance"

TEMPLATE_FILENAME = "template_svl_fix_by_svl_id.xlsx"
TEMPLATE_PATH = ASSETS_DIR / "svl_fix_je" / TEMPLATE_FILENAME


@dataclass
class SvlFixJeSettings:
    database_profile_id: str = FOLLOW_GLOBAL_PROFILE_ID
    view_mode: str = DEFAULT_VIEW_MODE
    last_excel_file: str = ""
    last_prefix: str = DEFAULT_REF_PREFIX
    auto_post: bool = False
    max_workers: int = DEFAULT_MAX_WORKERS
    last_output_dir: str = ""
    logs_section_open: bool = False
    dashboard_company_id: int = 0
    dashboard_date_from: str = ""
    dashboard_date_to: str = ""
    dashboard_dataset_mode: str = DEFAULT_DASHBOARD_DATASET_MODE
    dashboard_hide_inventory_accounts: bool = False
    dashboard_include_inventory_accounts: bool = True
    dashboard_include_non_inventory_accounts: bool = True
    dashboard_logs_section_open: bool = False
    dashboard_repair_last_target_mode: str = ""
    dashboard_repair_last_posting_mode: str = ""
    dashboard_repair_last_target_account_role: str = ""
    dashboard_repair_last_resolve_account_code: str = ""
    pcb_case1_last_date: str = ""
    pcb_problem_codes: str = "2103006,1108099"
    pcb_info_codes: str = "11120003"


def normalize_worker_count(value: int | str | None) -> int:
    try:
        return max(1, int(value or DEFAULT_MAX_WORKERS))
    except (TypeError, ValueError):
        return DEFAULT_MAX_WORKERS


def normalize_database_profile_id(value: str | None) -> str:
    return normalize_module_database_profile_id(value)


def normalize_view_mode(value: str | None) -> str:
    clean = normalize_text(value).lower()
    if clean == "dashboard":
        return "dashboard"
    return DEFAULT_VIEW_MODE


def normalize_dashboard_dataset_mode(value: str | None) -> str:
    clean = normalize_text(value).lower()
    if clean == PURCHASE_CYCLE_BALANCE_DATASET_MODE:
        return PURCHASE_CYCLE_BALANCE_DATASET_MODE
    return DEFAULT_DASHBOARD_DATASET_MODE


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


def default_input_browse_dir(global_settings: GlobalSettings, last_excel_file: str) -> str:
    excel_path = Path(normalize_text(last_excel_file))
    if excel_path.is_file():
        return str(excel_path.parent)
    if excel_path.parent and str(excel_path.parent) not in {".", ""}:
        return str(excel_path.parent)
    if normalize_text(global_settings.default_input_dir):
        return normalize_text(global_settings.default_input_dir)
    return ""


def default_output_browse_dir(global_settings: GlobalSettings, last_output_dir: str) -> str:
    if normalize_text(last_output_dir):
        return normalize_text(last_output_dir)
    if normalize_text(global_settings.default_output_dir):
        return normalize_text(global_settings.default_output_dir)
    return ""
