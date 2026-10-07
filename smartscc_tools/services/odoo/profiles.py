"""Shared database profile models and helpers for Odoo-backed modules."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable


FOLLOW_GLOBAL_PROFILE_ID = "__follow_global__"
FOLLOW_GLOBAL_LABEL = "Follow Global Default"
USE_GAS_DEFAULT_LABEL = "Use GAS Default"

DEFAULT_LIVE_PROFILE_ID = "db_live"
DEFAULT_DUMMY_PROFILE_ID = "db_dummy"


@dataclass(frozen=True)
class DatabaseProfile:
    profile_id: str
    database_value: str
    alias: str = ""
    note: str = ""


def default_database_profiles() -> list[DatabaseProfile]:
    return [
        DatabaseProfile(
            profile_id=DEFAULT_LIVE_PROFILE_ID,
            database_value="hwgroup_erp",
            alias="Live ERP",
            note="Database Live",
        ),
        DatabaseProfile(
            profile_id=DEFAULT_DUMMY_PROFILE_ID,
            database_value="hwgroup_erp_22022026",
            alias="Dummy ERP",
            note="Database Dummy",
        ),
    ]


def normalize_database_profile_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("\xa0", " ").strip()


def render_database_profile_label(profile: DatabaseProfile) -> str:
    alias = normalize_database_profile_text(profile.alias)
    database_value = normalize_database_profile_text(profile.database_value)
    note = normalize_database_profile_text(profile.note)

    if alias and database_value and alias.lower() != database_value.lower():
        label = f"{alias} - {database_value}"
    else:
        label = database_value or alias or "Unnamed Database"

    if note:
        label = f"{label} [{note}]"
    return label


def build_module_database_options(profiles: Iterable[DatabaseProfile]) -> list[tuple[str, str]]:
    options: list[tuple[str, str]] = [(FOLLOW_GLOBAL_PROFILE_ID, FOLLOW_GLOBAL_LABEL)]
    for profile in profiles:
        clean_id = normalize_database_profile_text(profile.profile_id)
        if not clean_id:
            continue
        options.append((clean_id, render_database_profile_label(profile)))
    return options


def build_global_default_database_options(profiles: Iterable[DatabaseProfile]) -> list[tuple[str, str]]:
    options: list[tuple[str, str]] = [("", USE_GAS_DEFAULT_LABEL)]
    for profile in profiles:
        clean_id = normalize_database_profile_text(profile.profile_id)
        if not clean_id:
            continue
        options.append((clean_id, render_database_profile_label(profile)))
    return options


def build_option_maps(options: Iterable[tuple[str, str]]) -> tuple[dict[str, str], dict[str, str]]:
    label_by_id: dict[str, str] = {}
    id_by_label: dict[str, str] = {}
    for option_id, label in options:
        clean_id = normalize_database_profile_text(option_id)
        clean_label = normalize_database_profile_text(label)
        if not clean_label:
            continue
        label_by_id[clean_id] = clean_label
        id_by_label[clean_label] = clean_id
    return label_by_id, id_by_label


def normalize_database_profiles(
    value: Any,
    *,
    seed_defaults: bool,
) -> list[DatabaseProfile]:
    raw_items: list[Any]
    if isinstance(value, list):
        raw_items = list(value)
    else:
        raw_items = []

    profiles: list[DatabaseProfile] = []
    seen_ids: set[str] = set()
    seen_values: set[str] = set()
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        profile_id = normalize_database_profile_text(raw.get("profile_id"))
        database_value = normalize_database_profile_text(raw.get("database_value"))
        alias = normalize_database_profile_text(raw.get("alias"))
        note = normalize_database_profile_text(raw.get("note"))
        if not profile_id or not database_value:
            continue
        if profile_id in seen_ids or database_value.lower() in seen_values:
            continue
        seen_ids.add(profile_id)
        seen_values.add(database_value.lower())
        profiles.append(
            DatabaseProfile(
                profile_id=profile_id,
                database_value=database_value,
                alias=alias,
                note=note,
            )
        )

    if profiles or not seed_defaults:
        return profiles
    return default_database_profiles()


def find_database_profile(profiles: Iterable[DatabaseProfile], profile_id: str) -> DatabaseProfile | None:
    clean_id = normalize_database_profile_text(profile_id)
    if not clean_id:
        return None
    for profile in profiles:
        if normalize_database_profile_text(profile.profile_id) == clean_id:
            return profile
    return None


def find_database_profile_by_value(
    profiles: Iterable[DatabaseProfile],
    database_value: str,
) -> DatabaseProfile | None:
    clean_value = normalize_database_profile_text(database_value).lower()
    if not clean_value:
        return None
    for profile in profiles:
        if normalize_database_profile_text(profile.database_value).lower() == clean_value:
            return profile
    return None


def ensure_database_profile(
    profiles: list[DatabaseProfile],
    database_value: str,
    *,
    alias: str = "",
    note: str = "",
) -> str:
    clean_value = normalize_database_profile_text(database_value)
    if not clean_value:
        return ""

    existing = find_database_profile_by_value(profiles, clean_value)
    if existing is not None:
        return existing.profile_id

    profile_id = _build_unique_profile_id(
        existing_ids={profile.profile_id for profile in profiles},
        preferred_source=alias or clean_value,
    )
    profiles.append(
        DatabaseProfile(
            profile_id=profile_id,
            database_value=clean_value,
            alias=normalize_database_profile_text(alias),
            note=normalize_database_profile_text(note),
        )
    )
    return profile_id


def build_new_database_profile_id(
    profiles: Iterable[DatabaseProfile],
    *,
    preferred_source: str,
) -> str:
    return _build_unique_profile_id(
        existing_ids={profile.profile_id for profile in profiles},
        preferred_source=preferred_source,
    )


def normalize_module_database_profile_id(value: Any) -> str:
    clean = normalize_database_profile_text(value)
    if not clean:
        return FOLLOW_GLOBAL_PROFILE_ID
    return clean


def normalize_database_profile_id(value: Any) -> str:
    return normalize_module_database_profile_id(value)


def normalize_global_default_profile_id(value: Any, profiles: Iterable[DatabaseProfile]) -> str:
    clean = normalize_database_profile_text(value)
    if not clean:
        return ""
    if find_database_profile(profiles, clean) is not None:
        return clean
    return ""


def resolve_database_selection(
    *,
    profiles: Iterable[DatabaseProfile],
    module_profile_id: str,
    default_profile_id: str,
    gas_default_database: str,
) -> str:
    clean_module_profile_id = normalize_module_database_profile_id(module_profile_id)
    if clean_module_profile_id != FOLLOW_GLOBAL_PROFILE_ID:
        profile = find_database_profile(profiles, clean_module_profile_id)
        if profile is not None:
            return normalize_database_profile_text(profile.database_value)
        if clean_module_profile_id:
            return clean_module_profile_id

    profile = find_database_profile(profiles, default_profile_id)
    if profile is not None:
        return normalize_database_profile_text(profile.database_value)
    return normalize_database_profile_text(gas_default_database)


def migrate_legacy_global_db_override(
    *,
    profiles: list[DatabaseProfile],
    default_profile_id: str,
    legacy_db_override: str,
) -> str:
    clean_default = normalize_database_profile_text(default_profile_id)
    if clean_default:
        return clean_default

    clean_legacy = normalize_database_profile_text(legacy_db_override)
    if not clean_legacy:
        return ""
    return ensure_database_profile(profiles, clean_legacy, alias=clean_legacy)


def _build_unique_profile_id(existing_ids: set[str], preferred_source: str) -> str:
    slug = _slugify(preferred_source) or "database"
    candidate = f"db_{slug}"
    if candidate not in existing_ids:
        return candidate
    index = 2
    while True:
        candidate = f"db_{slug}_{index}"
        if candidate not in existing_ids:
            return candidate
        index += 1


def _slugify(value: str) -> str:
    clean = normalize_database_profile_text(value).lower()
    clean = re.sub(r"[^a-z0-9]+", "_", clean)
    return clean.strip("_")
