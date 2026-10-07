"""Canonical runtime and business configuration for Item Journal."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from functools import lru_cache
from typing import Any, Callable, Iterable


@dataclass
class RuntimeSettings:
    # Core behavior
    batch_limit: int = 1000
    batch_delay_ms: int = 0
    retry_max: int = 2
    retry_delay_ms: int = 1000
    save_every_rows: int = 5000
    doevents_every: int = 250
    prefetch_chunk_size: int = 200
    read_chunk_size: int = 300
    adaptive_batch_min: int = 50

    # Validation and debug
    validate_json_payload_on_each_call: bool = False
    debug_row_json: bool = True
    debug_row_json_limit: int = 10
    debug_batch_json: bool = True
    debug_batch_json_limit: int = 5
    debug_batch_json_max_chars: int = 2000
    debug_payload_preview_max: int = 1000
    debug_response_preview_max: int = 1000

    # Date sync behavior
    local_tz_offset: int = 7
    use_local_time_as_utc: bool = False
    auto_validate: bool = True
    date_required: bool = False
    move_date_retry_count: int = 3
    move_date_retry_delay_ms: int = 1000
    use_force_date_context: bool = True
    strict_svl_date_sync: bool = False
    stj_required: bool = True
    validate_backorder_policy: str = "fail"
    validate_enforce_done_qty: bool = True
    validate_cleanup_policy: str = "cancel_then_keep"
    journal_expectation_mode: str = "hybrid"
    stj_poll_retry_count: int = 8
    stj_poll_retry_delay_ms: int = 1000
    stj_poll_fast_attempts: int = 3
    stj_poll_max_delay_ms: int = 10000
    stj_recovery_timeout_ms: int = 600000
    stj_recovery_poll_delay_ms: int = 2000
    stj_recovery_fast_attempts: int = 3
    stj_recovery_poll_max_delay_ms: int = 10000
    stj_include_origin_scope: bool = True
    stj_remap_mode: str = "conservative"
    stj_remap_qty_tolerance: float = 1e-6
    stj_row_split_policy: str = "fail"
    stj_result_picking_mode: str = "actual"

    # HTTP transport behavior
    http_timeout_connect: int = 30
    http_timeout_read: int = 120
    max_read_concurrency: int = 8
    transport_max_retries: int = 3
    transport_retry_backoff_seconds: float = 1.0
    create_timeout_extra_retry_max: int = 1
    create_timeout_extra_retry_delay_ms: int = 1000
    create_origin_mode: str = "technical"
    create_origin_human_ref: str = ""

    # GAS behavior
    gas_timeout_connect: int = 30
    gas_timeout_read: int = 30
    gas_max_retries: int = 3
    gas_retry_backoff_seconds: float = 1.0
    audit_flush_every_events: int = 200

    # Perf reporting v2 (1-second-matters baseline)
    perf_warn_rpc_ms: int = 1000
    perf_critical_rpc_ms: int = 5000
    perf_stall_warn_sec: int = 3
    perf_stall_critical_sec: int = 5
    perf_heartbeat_sec: int = 1
    perf_jsonl_flush_every_events: int = 50
    perf_top_causes: int = 5

    @classmethod
    def from_preset(cls, preset: str) -> "RuntimeSettings":
        name = (preset or "safe-fast").strip().lower()
        settings = cls()

        if name == "safe":
            return settings

        if name == "safe-fast":
            settings.batch_limit = 150
            settings.prefetch_chunk_size = 400
            settings.read_chunk_size = 500
            settings.retry_max = 1
            settings.retry_delay_ms = 500
            settings.move_date_retry_count = 2
            settings.move_date_retry_delay_ms = 500
            settings.transport_max_retries = 2
            settings.transport_retry_backoff_seconds = 0.5
            settings.doevents_every = 50
            settings.debug_row_json = False
            settings.debug_batch_json = False
            settings.auto_validate = True
            settings.strict_svl_date_sync = False
            settings.perf_warn_rpc_ms = 1000
            settings.perf_critical_rpc_ms = 5000
            settings.perf_stall_warn_sec = 3
            settings.perf_stall_critical_sec = 5
            settings.perf_heartbeat_sec = 1
            return settings

        if name == "fast":
            settings.batch_limit = 1500
            settings.prefetch_chunk_size = 400
            settings.read_chunk_size = 500
            settings.retry_max = 1
            settings.debug_row_json = False
            settings.debug_batch_json = False
            settings.perf_warn_rpc_ms = 1200
            settings.perf_critical_rpc_ms = 6000
            settings.perf_stall_warn_sec = 4
            settings.perf_stall_critical_sec = 7
            return settings

        if name == "debug":
            settings.batch_limit = 500
            settings.retry_max = 3
            settings.retry_delay_ms = 1500
            settings.prefetch_chunk_size = 100
            settings.read_chunk_size = 150
            settings.debug_row_json = True
            settings.debug_row_json_limit = 50
            settings.debug_batch_json = True
            settings.debug_batch_json_limit = 20
            settings.validate_json_payload_on_each_call = True
            settings.transport_max_retries = 5
            settings.perf_warn_rpc_ms = 500
            settings.perf_critical_rpc_ms = 3000
            settings.perf_stall_warn_sec = 2
            settings.perf_stall_critical_sec = 4
            return settings

        if name == "custom":
            return settings

        raise ValueError(f"Preset tidak dikenal: {preset}")


def _parse_bool(text: str) -> bool:
    value = text.strip().lower()
    if value in {"1", "true", "yes", "y", "on"}:
        return True
    if value in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError(f"Nilai boolean tidak valid: {text}")


def _parse_int(text: str) -> int:
    return int(text.strip())


@dataclass(frozen=True)
class BusinessUploadSettings:
    # Batching and retry
    transaction_volume_per_batch: int = 150
    max_retry_for_network_errors: int = 1
    wait_between_network_retries_ms: int = 500
    max_retry_for_create_timeout: int = 3
    wait_between_create_timeout_retries_ms: int = 2000

    # Journal waiting and recovery
    max_wait_time_for_journal_creation_ms: int = 600_000
    max_journal_verification_attempts: int = 8
    wait_between_journal_verification_ms: int = 1000
    wait_between_journal_recovery_checks_ms: int = 2000
    include_related_transfers_in_journal_check: bool = True

    def to_override_map(self) -> dict[str, object]:
        return {
            "TRANSACTION_VOLUME_PER_BATCH": self.transaction_volume_per_batch,
            "MAX_RETRY_FOR_NETWORK_ERRORS": self.max_retry_for_network_errors,
            "WAIT_BETWEEN_NETWORK_RETRIES_MS": self.wait_between_network_retries_ms,
            "MAX_RETRY_FOR_CREATE_TIMEOUT": self.max_retry_for_create_timeout,
            "WAIT_BETWEEN_CREATE_TIMEOUT_RETRIES_MS": self.wait_between_create_timeout_retries_ms,
            "MAX_WAIT_TIME_FOR_JOURNAL_CREATION_MS": self.max_wait_time_for_journal_creation_ms,
            "MAX_JOURNAL_VERIFICATION_ATTEMPTS": self.max_journal_verification_attempts,
            "WAIT_BETWEEN_JOURNAL_VERIFICATION_MS": self.wait_between_journal_verification_ms,
            "WAIT_BETWEEN_JOURNAL_RECOVERY_CHECKS_MS": self.wait_between_journal_recovery_checks_ms,
            "INCLUDE_RELATED_TRANSFERS_IN_JOURNAL_CHECK": self.include_related_transfers_in_journal_check,
        }

    def apply_to_runtime(self, runtime: RuntimeSettings) -> RuntimeSettings:
        runtime.batch_limit = max(1, int(self.transaction_volume_per_batch))
        runtime.retry_max = max(0, int(self.max_retry_for_network_errors))
        runtime.retry_delay_ms = max(0, int(self.wait_between_network_retries_ms))
        runtime.create_timeout_extra_retry_max = max(0, int(self.max_retry_for_create_timeout))
        runtime.create_timeout_extra_retry_delay_ms = max(
            0, int(self.wait_between_create_timeout_retries_ms)
        )

        runtime.stj_recovery_timeout_ms = max(0, int(self.max_wait_time_for_journal_creation_ms))
        runtime.stj_poll_retry_count = max(1, int(self.max_journal_verification_attempts))
        runtime.stj_poll_retry_delay_ms = max(0, int(self.wait_between_journal_verification_ms))
        runtime.stj_recovery_poll_delay_ms = max(
            0, int(self.wait_between_journal_recovery_checks_ms)
        )
        runtime.stj_include_origin_scope = bool(
            self.include_related_transfers_in_journal_check
        )
        return runtime


BUSINESS_KEY_TO_ATTR: dict[str, tuple[str, Callable[[str], Any]]] = {
    "TRANSACTION_VOLUME_PER_BATCH": ("transaction_volume_per_batch", _parse_int),
    "MAX_RETRY_FOR_NETWORK_ERRORS": ("max_retry_for_network_errors", _parse_int),
    "WAIT_BETWEEN_NETWORK_RETRIES_MS": ("wait_between_network_retries_ms", _parse_int),
    "MAX_RETRY_FOR_CREATE_TIMEOUT": ("max_retry_for_create_timeout", _parse_int),
    "WAIT_BETWEEN_CREATE_TIMEOUT_RETRIES_MS": ("wait_between_create_timeout_retries_ms", _parse_int),
    "MAX_WAIT_TIME_FOR_JOURNAL_CREATION_MS": ("max_wait_time_for_journal_creation_ms", _parse_int),
    "MAX_JOURNAL_VERIFICATION_ATTEMPTS": ("max_journal_verification_attempts", _parse_int),
    "WAIT_BETWEEN_JOURNAL_VERIFICATION_MS": ("wait_between_journal_verification_ms", _parse_int),
    "WAIT_BETWEEN_JOURNAL_RECOVERY_CHECKS_MS": ("wait_between_journal_recovery_checks_ms", _parse_int),
    "INCLUDE_RELATED_TRANSFERS_IN_JOURNAL_CHECK": ("include_related_transfers_in_journal_check", _parse_bool),
}


@lru_cache(maxsize=8)
def defaults_for_preset(preset: str) -> BusinessUploadSettings:
    clean = (preset or "safe-fast").strip().lower()

    if clean == "safe":
        return BusinessUploadSettings(
            transaction_volume_per_batch=1000,
            max_retry_for_network_errors=2,
            wait_between_network_retries_ms=1000,
        )
    if clean == "safe-fast":
        return BusinessUploadSettings(
            transaction_volume_per_batch=150,
            max_retry_for_network_errors=1,
            wait_between_network_retries_ms=500,
        )
    if clean == "fast":
        return BusinessUploadSettings(
            transaction_volume_per_batch=1500,
            max_retry_for_network_errors=1,
            wait_between_network_retries_ms=500,
        )
    if clean == "debug":
        return BusinessUploadSettings(
            transaction_volume_per_batch=500,
            max_retry_for_network_errors=3,
            wait_between_network_retries_ms=1500,
            max_retry_for_create_timeout=5,
            wait_between_create_timeout_retries_ms=1000,
        )
    if clean == "custom":
        return BusinessUploadSettings()

    raise ValueError(f"Preset tidak dikenal: {preset}")


def apply_business_overrides(
    settings: BusinessUploadSettings,
    set_args: Iterable[str],
) -> BusinessUploadSettings:
    values = asdict(settings)

    for raw in set_args:
        if "=" not in raw:
            raise ValueError(f"Format --set tidak valid: {raw}. Gunakan KEY=VALUE.")
        key, value = raw.split("=", 1)
        clean_key = key.strip().upper()
        clean_value = value.strip()

        if clean_key not in BUSINESS_KEY_TO_ATTR:
            raise ValueError(f"Konstanta bisnis tidak dikenal: {clean_key}")

        attr_name, caster = BUSINESS_KEY_TO_ATTR[clean_key]
        values[attr_name] = caster(clean_value)

    return BusinessUploadSettings(**values)


def build_runtime_settings(
    preset: str,
    set_args: Iterable[str],
) -> tuple[RuntimeSettings, BusinessUploadSettings]:
    runtime = RuntimeSettings.from_preset(preset)
    business = defaults_for_preset(preset)
    business = apply_business_overrides(business, set_args)
    business.apply_to_runtime(runtime)
    return runtime, business
