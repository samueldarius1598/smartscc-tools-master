"""Global settings and persistent state for the platform."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from smartscc_tools.features.item_journal.utils import normalize_text
from smartscc_tools.services.odoo.profiles import (
    DatabaseProfile,
    default_database_profiles,
    migrate_legacy_global_db_override,
    normalize_database_profiles,
    normalize_global_default_profile_id,
)


@dataclass
class InventoryCoaEntry:
    entry_id: str = ""
    coa_code: str = ""
    label: str = ""


@dataclass
class RepairAccountEntry:
    entry_id: str = ""
    coa_code: str = ""
    label: str = ""


def default_inventory_coa_entries() -> list[InventoryCoaEntry]:
    return [
        InventoryCoaEntry(
            entry_id="inv_coa_default_1",
            coa_code="1105001",
            label="Persediaan Alkohol / Alcohol Inventory",
        ),
        InventoryCoaEntry(
            entry_id="inv_coa_default_2",
            coa_code="1105002",
            label="Persediaan Minuman / Beverage Inventory",
        ),
        InventoryCoaEntry(
            entry_id="inv_coa_default_3",
            coa_code="1105003",
            label="Persediaan Makanan / Food Inventory",
        ),
        InventoryCoaEntry(
            entry_id="inv_coa_default_4",
            coa_code="1105004",
            label="Persediaan Rokok / Cigarette Inventory",
        ),
        InventoryCoaEntry(
            entry_id="inv_coa_default_5",
            coa_code="1105005",
            label="Persediaan Merchandise / Merchandise Inventory",
        ),
        InventoryCoaEntry(
            entry_id="inv_coa_default_6",
            coa_code="1105006",
            label="Persediaan Habis Pakai / Consumables Inventory",
        ),
        InventoryCoaEntry(
            entry_id="inv_coa_default_7",
            coa_code="1105007",
            label="Persediaan dalam Perjalanan / Inventory in Transit",
        ),
        InventoryCoaEntry(
            entry_id="inv_coa_default_8",
            coa_code="1105099",
            label="Persediaan lain-lain / Other Inventory",
        ),
    ]


def default_repair_account_entries() -> list[RepairAccountEntry]:
    return [
        RepairAccountEntry(
            entry_id="repair_acc_default_1",
            coa_code="1108099",
            label="Akun koreksi default / Default correction account",
        )
    ]


def normalize_inventory_coa_entries(value: Any) -> list[InventoryCoaEntry]:
    rows = value if isinstance(value, list) else []
    normalized: list[InventoryCoaEntry] = []
    seen_codes: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        coa_code = normalize_text(row.get("coa_code")).strip()
        if not coa_code:
            continue
        normalized_code = coa_code.upper()
        if normalized_code in seen_codes:
            continue
        seen_codes.add(normalized_code)
        entry_id = normalize_text(row.get("entry_id")) or f"inv_coa_{index + 1}"
        normalized.append(
            InventoryCoaEntry(
                entry_id=entry_id,
                coa_code=normalized_code,
                label=normalize_text(row.get("label")),
            )
        )
    return normalized


def normalize_repair_account_entries(value: Any) -> list[RepairAccountEntry]:
    rows = value if isinstance(value, list) else []
    normalized: list[RepairAccountEntry] = []
    seen_codes: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        coa_code = normalize_text(row.get("coa_code")).strip()
        if not coa_code:
            continue
        normalized_code = coa_code.upper()
        if normalized_code in seen_codes:
            continue
        seen_codes.add(normalized_code)
        entry_id = normalize_text(row.get("entry_id")) or f"repair_acc_{index + 1}"
        normalized.append(
            RepairAccountEntry(
                entry_id=entry_id,
                coa_code=normalized_code,
                label=normalize_text(row.get("label")),
            )
        )
    return normalized


@dataclass
class GlobalSettings:
    # Legacy compatibility only. New writes should use database_profiles.
    db_override: str = ""
    database_profiles: list[DatabaseProfile] = field(default_factory=default_database_profiles)
    default_database_profile_id: str = ""
    default_output_dir: str = ""
    default_input_dir: str = ""
    active_module_id: str = "item_journal"
    window_geometry: str = "1280x820"
    auto_check_updates: bool = True
    auto_download_updates: bool = True
    last_update_check_utc: str = ""
    ignored_update_version: str = ""
    inventory_coa_defaults_initialized: bool = True
    inventory_coa_entries: list[InventoryCoaEntry] = field(default_factory=default_inventory_coa_entries)
    repair_account_defaults_initialized: bool = True
    repair_account_entries: list[RepairAccountEntry] = field(default_factory=default_repair_account_entries)
    module_settings: dict[str, dict[str, Any]] = field(default_factory=dict)


class GlobalPersistentState:
    """Read / write global settings JSON."""

    def __init__(self, path: Path | None = None) -> None:
        if path is None:
            base_dir = Path(os.getenv("APPDATA") or (Path.home() / ".config")) / "smartscc_tools"
            path = base_dir / "global_state.json"
        self.path = Path(path)

    def load(self) -> GlobalSettings:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return GlobalSettings()

        has_database_profiles = "database_profiles" in payload
        database_profiles = normalize_database_profiles(
            payload.get("database_profiles"),
            seed_defaults=not has_database_profiles,
        )
        default_database_profile_id = normalize_global_default_profile_id(
            payload.get("default_database_profile_id"),
            database_profiles,
        )
        legacy_db_override = str(payload.get("db_override") or "")
        default_database_profile_id = migrate_legacy_global_db_override(
            profiles=database_profiles,
            default_profile_id=default_database_profile_id,
            legacy_db_override=legacy_db_override,
        )

        has_inventory_coa_entries = "inventory_coa_entries" in payload
        inventory_coa_entries = normalize_inventory_coa_entries(payload.get("inventory_coa_entries"))
        inventory_coa_defaults_initialized = bool(payload.get("inventory_coa_defaults_initialized", False))
        if inventory_coa_entries:
            inventory_coa_defaults_initialized = True
        elif (not has_inventory_coa_entries) or (not inventory_coa_defaults_initialized):
            inventory_coa_entries = default_inventory_coa_entries()
            inventory_coa_defaults_initialized = True

        has_repair_account_entries = "repair_account_entries" in payload
        repair_account_entries = normalize_repair_account_entries(payload.get("repair_account_entries"))
        repair_account_defaults_initialized = bool(payload.get("repair_account_defaults_initialized", False))
        if repair_account_entries:
            repair_account_defaults_initialized = True
        elif (not has_repair_account_entries) or (not repair_account_defaults_initialized):
            repair_account_entries = default_repair_account_entries()
            repair_account_defaults_initialized = True

        return GlobalSettings(
            db_override=legacy_db_override,
            database_profiles=database_profiles,
            default_database_profile_id=default_database_profile_id,
            default_output_dir=str(payload.get("default_output_dir") or ""),
            default_input_dir=str(payload.get("default_input_dir") or ""),
            active_module_id=str(payload.get("active_module_id") or "item_journal"),
            window_geometry=str(payload.get("window_geometry") or "1280x820"),
            auto_check_updates=bool(payload.get("auto_check_updates", True)),
            auto_download_updates=bool(payload.get("auto_download_updates", True)),
            last_update_check_utc=str(payload.get("last_update_check_utc") or ""),
            ignored_update_version=str(payload.get("ignored_update_version") or ""),
            inventory_coa_defaults_initialized=inventory_coa_defaults_initialized,
            inventory_coa_entries=inventory_coa_entries,
            repair_account_defaults_initialized=repair_account_defaults_initialized,
            repair_account_entries=repair_account_entries,
            module_settings=dict(payload.get("module_settings") or {}),
        )

    def save(self, settings: GlobalSettings) -> None:
        payload = asdict(settings)
        payload.pop("db_override", None)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
