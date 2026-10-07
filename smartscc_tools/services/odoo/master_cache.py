"""Session-scoped master-data cache shared across modules."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import threading
import time
from typing import Any


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_key_part(value: Any) -> str:
    return _normalize_text(value).lower()


def _normalize_product_fields(fields: Iterable[str] | None) -> frozenset[str]:
    normalized = {_normalize_text(field) for field in fields or [] if _normalize_text(field)}
    return frozenset(normalized)


def rpc_cache_scope(rpc: Any) -> tuple[str, str]:
    config = getattr(rpc, "config", None)
    base_url = _normalize_text(getattr(config, "base_url", ""))
    database = _normalize_text(getattr(config, "database", ""))
    return base_url, database


@dataclass(slots=True)
class _CacheEntry:
    payload: Any
    expires_at: float
    fields: frozenset[str]


class SessionMasterCache:
    """Thread-safe cache for repeated Odoo master-data reads within one app session."""

    def __init__(
        self,
        *,
        ttl_seconds: int = 600,
        clock: Any | None = None,
    ) -> None:
        self.ttl_seconds = max(1, int(ttl_seconds or 600))
        self._clock = clock or time.monotonic
        self._lock = threading.RLock()
        self._product_records_by_code: dict[tuple[str, str, str, int, str], _CacheEntry] = {}
        self._partner_ids_by_name: dict[tuple[str, str, int, str], _CacheEntry] = {}
        self._location_ids_by_name: dict[tuple[str, str, int, str], _CacheEntry] = {}

    def get_product_record(
        self,
        *,
        base_url: str,
        database: str,
        model: str,
        company_id: int,
        code: str,
        required_fields: Iterable[str] | None = None,
    ) -> dict[str, Any] | None:
        key = self._product_key(base_url, database, model, company_id, code)
        required = _normalize_product_fields(required_fields)
        with self._lock:
            entry = self._get_live_entry(self._product_records_by_code, key)
            if entry is None:
                return None
            if required and not required.issubset(entry.fields):
                return None
            payload = entry.payload
            return dict(payload) if isinstance(payload, dict) else None

    def set_product_record(
        self,
        *,
        base_url: str,
        database: str,
        model: str,
        company_id: int,
        row: Mapping[str, Any],
        fields: Iterable[str] | None = None,
        code: str | None = None,
    ) -> None:
        row_payload = dict(row or {})
        clean_code = _normalize_text(code if code is not None else row_payload.get("default_code"))
        if not clean_code:
            return
        key = self._product_key(base_url, database, model, company_id, clean_code)
        new_fields = _normalize_product_fields(fields) | _normalize_product_fields(row_payload.keys())
        with self._lock:
            existing = self._get_live_entry(self._product_records_by_code, key)
            merged_payload = dict(existing.payload) if existing is not None and isinstance(existing.payload, dict) else {}
            merged_payload.update(row_payload)
            merged_payload.setdefault("default_code", clean_code)
            merged_fields = new_fields | (existing.fields if existing is not None else frozenset())
            self._product_records_by_code[key] = _CacheEntry(
                payload=merged_payload,
                expires_at=self._expires_at(),
                fields=merged_fields,
            )

    def get_partner_id(
        self,
        *,
        base_url: str,
        database: str,
        company_id: int,
        partner_name: str,
    ) -> int | None:
        key = self._partner_key(base_url, database, company_id, partner_name)
        with self._lock:
            entry = self._get_live_entry(self._partner_ids_by_name, key)
            if entry is None:
                return None
            return int(entry.payload or 0)

    def set_partner_id(
        self,
        *,
        base_url: str,
        database: str,
        company_id: int,
        partner_name: str,
        partner_id: int,
    ) -> None:
        clean_partner_id = int(partner_id or 0)
        if clean_partner_id <= 0:
            return
        key = self._partner_key(base_url, database, company_id, partner_name)
        with self._lock:
            self._partner_ids_by_name[key] = _CacheEntry(
                payload=clean_partner_id,
                expires_at=self._expires_at(),
                fields=frozenset(),
            )

    def get_location_id(
        self,
        *,
        base_url: str,
        database: str,
        company_id: int,
        location_name: str,
    ) -> int | None:
        key = self._location_key(base_url, database, company_id, location_name)
        with self._lock:
            entry = self._get_live_entry(self._location_ids_by_name, key)
            if entry is None:
                return None
            return int(entry.payload or 0)

    def set_location_id(
        self,
        *,
        base_url: str,
        database: str,
        company_id: int,
        location_name: str,
        location_id: int,
    ) -> None:
        clean_location_id = int(location_id or 0)
        if clean_location_id <= 0:
            return
        key = self._location_key(base_url, database, company_id, location_name)
        with self._lock:
            self._location_ids_by_name[key] = _CacheEntry(
                payload=clean_location_id,
                expires_at=self._expires_at(),
                fields=frozenset(),
            )

    def _expires_at(self) -> float:
        return float(self._clock()) + float(self.ttl_seconds)

    def _get_live_entry(
        self,
        store: dict[Any, _CacheEntry],
        key: Any,
    ) -> _CacheEntry | None:
        entry = store.get(key)
        if entry is None:
            return None
        if entry.expires_at <= float(self._clock()):
            store.pop(key, None)
            return None
        return entry

    @staticmethod
    def _scope_key(base_url: str, database: str) -> tuple[str, str]:
        return _normalize_key_part(base_url), _normalize_key_part(database)

    @classmethod
    def _product_key(
        cls,
        base_url: str,
        database: str,
        model: str,
        company_id: int,
        code: str,
    ) -> tuple[str, str, str, int, str]:
        scope_base_url, scope_database = cls._scope_key(base_url, database)
        return scope_base_url, scope_database, _normalize_key_part(model), int(company_id or 0), _normalize_key_part(code)

    @classmethod
    def _partner_key(
        cls,
        base_url: str,
        database: str,
        company_id: int,
        partner_name: str,
    ) -> tuple[str, str, int, str]:
        scope_base_url, scope_database = cls._scope_key(base_url, database)
        return scope_base_url, scope_database, int(company_id or 0), _normalize_key_part(partner_name)

    @classmethod
    def _location_key(
        cls,
        base_url: str,
        database: str,
        company_id: int,
        location_name: str,
    ) -> tuple[str, str, int, str]:
        scope_base_url, scope_database = cls._scope_key(base_url, database)
        return scope_base_url, scope_database, int(company_id or 0), _normalize_key_part(location_name)
