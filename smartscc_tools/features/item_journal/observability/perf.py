"""Realtime performance reporting utilities (schema v2)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
from threading import Lock
import uuid
from typing import Any, Dict, List

from smartscc_tools.features.item_journal.utils import normalize_text, utc_now_iso


SCHEMA_VERSION = "v2"
DEFAULT_EVENTS_FILE = "logs/perf_events_v2.jsonl"
DEFAULT_SUMMARY_FILE = "logs/perf_summary_v2.json"
DEFAULT_SCHEMA_FILE = "logs/perf_schema_v2.json"

MANDATORY_KEYS = [
    "schema_version",
    "run_id",
    "event_id",
    "timestamp_utc",
    "event_type",
    "stage",
    "severity",
    "cause_code",
    "rpc_model",
    "rpc_method",
    "duration_ms",
    "batch_seq",
    "row_number",
]


def _severity_rank(severity: str) -> int:
    clean = normalize_text(severity).lower()
    if clean == "critical":
        return 3
    if clean == "error":
        return 2
    if clean == "warn":
        return 1
    return 0


def _contains_any(text: str, tokens: List[str]) -> bool:
    lower = normalize_text(text).lower()
    return any(item in lower for item in tokens)


def classify_cause(
    message: str,
    stage: str,
    severity: str,
    event_type: str,
    duration_ms: int,
) -> str:
    clean_msg = normalize_text(message).lower()
    clean_stage = normalize_text(stage).upper()
    clean_type = normalize_text(event_type).lower()
    if clean_stage in {"CREATE_RECONCILE_HIT", "CREATE_RECONCILE_AMBIGUOUS"}:
        return "create_reconciled"
    if clean_stage == "STJ_REQUIRED_FAIL" or _contains_any(
        clean_msg,
        [
            "stj wajib",
            "rows_stj_empty",
            "stj tidak ditemukan",
            "stj mandatory",
        ],
    ):
        return "stj_missing"
    if _contains_any(clean_msg, ["timeout", "timed out", "http status 50", "gateway timeout", "connection reset"]):
        return "transport_timeout"
    if clean_type in {"retry", "split"}:
        return "retry_storm"
    if clean_stage in {"PICKING_VALIDATE", "VALIDATE", "POST_VALIDATE_CORE_SYNC", "STJ"}:
        return "post_validate_hotspot"
    if _severity_rank(severity) >= 1 and duration_ms >= 1000:
        return "odoo_server_slow"
    return "unknown"


@dataclass
class PerfThresholds:
    warn_rpc_ms: int
    critical_rpc_ms: int
    warn_stall_sec: int
    critical_stall_sec: int
    heartbeat_sec: int
    top_causes: int
    flush_every_events: int

    @classmethod
    def from_profile(cls, profile: str) -> "PerfThresholds":
        clean = normalize_text(profile).lower() or "aggressive"
        if clean == "balanced":
            return cls(
                warn_rpc_ms=2000,
                critical_rpc_ms=10000,
                warn_stall_sec=5,
                critical_stall_sec=10,
                heartbeat_sec=1,
                top_causes=5,
                flush_every_events=50,
            )
        if clean == "conservative":
            return cls(
                warn_rpc_ms=5000,
                critical_rpc_ms=20000,
                warn_stall_sec=10,
                critical_stall_sec=20,
                heartbeat_sec=1,
                top_causes=5,
                flush_every_events=50,
            )
        return cls(
            warn_rpc_ms=1000,
            critical_rpc_ms=5000,
            warn_stall_sec=3,
            critical_stall_sec=5,
            heartbeat_sec=1,
            top_causes=5,
            flush_every_events=50,
        )


@dataclass
class PerfEventV2:
    run_id: str
    event_type: str
    stage: str
    severity: str
    cause_code: str
    rpc_model: str
    rpc_method: str
    duration_ms: int
    batch_seq: int
    row_number: int
    message: str = ""
    http_status: int = 0
    attempt: int = 0
    details: Dict[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp_utc: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        for key in MANDATORY_KEYS:
            if key not in payload:
                raise RuntimeError(f"Perf event missing mandatory key: {key}")
        return payload


@dataclass
class RpcTelemetry:
    started_at_utc: str
    ended_at_utc: str
    duration_ms: int
    status: str
    attempt: int
    http_status: int
    error_class: str
    error_message: str
    model: str
    method: str
    stage: str
    excel_row: int = 0


class SlowDetector:
    def __init__(self, thresholds: PerfThresholds) -> None:
        self.thresholds = thresholds
        self.last_progress_ts = datetime.now(timezone.utc)
        self.last_emitted_level = ""

    def on_progress(self) -> None:
        self.last_progress_ts = datetime.now(timezone.utc)
        self.last_emitted_level = ""

    def check(self) -> tuple[str, int]:
        now = datetime.now(timezone.utc)
        elapsed_sec = int((now - self.last_progress_ts).total_seconds())
        if elapsed_sec >= self.thresholds.critical_stall_sec:
            if self.last_emitted_level != "critical":
                self.last_emitted_level = "critical"
                return "critical", elapsed_sec
            return "", elapsed_sec
        if elapsed_sec >= self.thresholds.warn_stall_sec:
            if self.last_emitted_level not in {"warn", "critical"}:
                self.last_emitted_level = "warn"
                return "warn", elapsed_sec
        return "", elapsed_sec

    def elapsed_seconds(self) -> int:
        now = datetime.now(timezone.utc)
        return max(0, int((now - self.last_progress_ts).total_seconds()))


class PerfReporter:
    """Machine-readable perf report writer for realtime + final summary."""

    def __init__(
        self,
        run_id: str,
        profile: str = "aggressive",
        events_file: str | None = None,
        logger: Any = None,
        technical_logger: Any = None,
        perf_sheet_enabled: bool = True,
        persist_files: bool = True,
    ) -> None:
        self.run_id = run_id
        self.profile = normalize_text(profile).lower() or "aggressive"
        self.thresholds = PerfThresholds.from_profile(self.profile)
        self.persist_files = bool(persist_files)
        self.events_path: Path | None = None
        self.summary_path: Path | None = None
        self.schema_path: Path | None = None
        if self.persist_files:
            self.events_path = Path(events_file or DEFAULT_EVENTS_FILE)
            self.summary_path = Path(DEFAULT_SUMMARY_FILE)
            self.schema_path = Path(DEFAULT_SCHEMA_FILE)
            self.events_path.parent.mkdir(parents=True, exist_ok=True)
            self.summary_path.parent.mkdir(parents=True, exist_ok=True)
            self.schema_path.parent.mkdir(parents=True, exist_ok=True)
        self.logger = logger
        self.technical_logger = technical_logger
        self.perf_sheet_enabled = bool(perf_sheet_enabled)

        self.lock = Lock()
        self.buffer: List[PerfEventV2] = []
        self.event_count = 0
        self.breach_count = 0
        self.events_by_cause: Dict[str, int] = {}
        self.duration_by_rpc: Dict[str, int] = {}
        self.count_by_rpc: Dict[str, int] = {}
        self.alerts: List[Dict[str, Any]] = []
        self.timeline: List[Dict[str, Any]] = []
        self.slow_detector = SlowDetector(self.thresholds)
        self.latest_stage = ""
        self.latest_batch_seq = 0
        self.latest_row_number = 0
        self.closed = False
        if self.persist_files:
            self._write_schema_file()

    def _write_schema_file(self) -> None:
        schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": "PerfEventV2",
            "type": "object",
            "required": MANDATORY_KEYS,
            "properties": {
                "schema_version": {"type": "string", "const": SCHEMA_VERSION},
                "run_id": {"type": "string"},
                "event_id": {"type": "string"},
                "timestamp_utc": {"type": "string"},
                "event_type": {"type": "string"},
                "stage": {"type": "string"},
                "severity": {"type": "string"},
                "cause_code": {"type": "string"},
                "rpc_model": {"type": "string"},
                "rpc_method": {"type": "string"},
                "duration_ms": {"type": "integer"},
                "batch_seq": {"type": "integer"},
                "row_number": {"type": "integer"},
                "message": {"type": "string"},
                "http_status": {"type": "integer"},
                "attempt": {"type": "integer"},
                "details": {"type": "object"},
            },
        }
        if self.schema_path is not None:
            self.schema_path.write_text(json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8")

    def notify_progress(self, stage: str, batch_seq: int = 0, row_number: int = 0) -> None:
        self.latest_stage = normalize_text(stage)
        self.latest_batch_seq = int(batch_seq or 0)
        self.latest_row_number = int(row_number or 0)
        self.slow_detector.on_progress()

    def emit(
        self,
        event_type: str,
        stage: str,
        severity: str = "info",
        cause_code: str = "unknown",
        rpc_model: str = "",
        rpc_method: str = "",
        duration_ms: int = 0,
        batch_seq: int = 0,
        row_number: int = 0,
        message: str = "",
        http_status: int = 0,
        attempt: int = 0,
        details: Dict[str, Any] | None = None,
    ) -> None:
        ev = PerfEventV2(
            run_id=self.run_id,
            event_type=normalize_text(event_type),
            stage=normalize_text(stage),
            severity=normalize_text(severity).lower() or "info",
            cause_code=normalize_text(cause_code) or "unknown",
            rpc_model=normalize_text(rpc_model),
            rpc_method=normalize_text(rpc_method),
            duration_ms=max(0, int(duration_ms or 0)),
            batch_seq=int(batch_seq or 0),
            row_number=int(row_number or 0),
            message=normalize_text(message),
            http_status=int(http_status or 0),
            attempt=int(attempt or 0),
            details=details or {},
        )
        self._emit_event(ev)

    def handle_rpc_telemetry(self, telemetry: RpcTelemetry) -> None:
        severity = "info"
        if telemetry.duration_ms >= self.thresholds.critical_rpc_ms:
            severity = "critical"
        elif telemetry.duration_ms >= self.thresholds.warn_rpc_ms:
            severity = "warn"
        if normalize_text(telemetry.status).lower() in {"error", "exception"}:
            severity = "error" if severity == "info" else severity
        cause_code = classify_cause(
            message=telemetry.error_message,
            stage=telemetry.stage,
            severity=severity,
            event_type="rpc",
            duration_ms=telemetry.duration_ms,
        )
        self.emit(
            event_type="rpc",
            stage=telemetry.stage,
            severity=severity,
            cause_code=cause_code,
            rpc_model=telemetry.model,
            rpc_method=telemetry.method,
            duration_ms=telemetry.duration_ms,
            batch_seq=self.latest_batch_seq,
            row_number=telemetry.excel_row or self.latest_row_number,
            message=telemetry.error_message or telemetry.status,
            http_status=telemetry.http_status,
            attempt=telemetry.attempt,
            details={
                "started_at_utc": telemetry.started_at_utc,
                "ended_at_utc": telemetry.ended_at_utc,
                "status": telemetry.status,
                "error_class": telemetry.error_class,
            },
        )

    def check_stall(self) -> Dict[str, Any] | None:
        level, elapsed_sec = self.slow_detector.check()
        if not level:
            return None
        message = f"No progress for {elapsed_sec}s on stage={self.latest_stage or '-'}"
        self.emit(
            event_type="slow_alert",
            stage=self.latest_stage or "ROW_LOOP",
            severity=level,
            cause_code="odoo_server_slow",
            rpc_model="",
            rpc_method="",
            duration_ms=elapsed_sec * 1000,
            batch_seq=self.latest_batch_seq,
            row_number=self.latest_row_number,
            message=message,
        )
        payload = {
            "severity": level,
            "elapsed_sec": elapsed_sec,
            "message": message,
            "stage": self.latest_stage or "ROW_LOOP",
            "batch_seq": self.latest_batch_seq,
            "row_number": self.latest_row_number,
        }
        self.alerts.append(payload)
        return payload

    def get_stall_elapsed_sec(self) -> int:
        return self.slow_detector.elapsed_seconds()

    def record_stage_span(
        self,
        stage: str,
        started_perf: float,
        ended_perf: float,
        message: str = "",
        batch_seq: int = 0,
        row_number: int = 0,
    ) -> None:
        duration_ms = int(max(0.0, ended_perf - started_perf) * 1000)
        severity = "info"
        if duration_ms >= self.thresholds.critical_rpc_ms:
            severity = "critical"
        elif duration_ms >= self.thresholds.warn_rpc_ms:
            severity = "warn"
        self.emit(
            event_type="stage_span",
            stage=stage,
            severity=severity,
            cause_code=classify_cause(message=message, stage=stage, severity=severity, event_type="stage_span", duration_ms=duration_ms),
            duration_ms=duration_ms,
            batch_seq=batch_seq,
            row_number=row_number,
            message=message,
        )

    def _emit_event(self, event: PerfEventV2) -> None:
        if self.closed:
            return
        payload = event.to_dict()
        key = f"{event.rpc_model}.{event.rpc_method}".strip(".")
        need_flush = False
        with self.lock:
            self.buffer.append(event)
            self.event_count += 1
            self.events_by_cause[event.cause_code] = self.events_by_cause.get(event.cause_code, 0) + 1
            if key:
                self.duration_by_rpc[key] = self.duration_by_rpc.get(key, 0) + event.duration_ms
                self.count_by_rpc[key] = self.count_by_rpc.get(key, 0) + 1
            if _severity_rank(event.severity) >= 1:
                self.breach_count += 1
            self.timeline.append(
                {
                    "timestamp_utc": payload["timestamp_utc"],
                    "event_type": payload["event_type"],
                    "stage": payload["stage"],
                    "severity": payload["severity"],
                    "cause_code": payload["cause_code"],
                    "message": payload.get("message", ""),
                }
            )
            if len(self.buffer) >= max(1, self.thresholds.flush_every_events):
                need_flush = True
        if need_flush:
            self.flush()
        if self.technical_logger and _severity_rank(event.severity) >= 1:
            self.technical_logger.warning(
                "PERF_ALERT severity=%s stage=%s cause=%s duration_ms=%s msg=%s",
                event.severity,
                event.stage,
                event.cause_code,
                event.duration_ms,
                event.message or "-",
            )

    def flush(self) -> None:
        with self.lock:
            if not self.buffer:
                return
            events = self.buffer[:]
            self.buffer.clear()
        if not self.persist_files or self.events_path is None:
            return
        with self.events_path.open("a", encoding="utf-8") as fp:
            for event in events:
                fp.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")

    def build_summary(self) -> Dict[str, Any]:
        top_causes = sorted(self.events_by_cause.items(), key=lambda item: item[1], reverse=True)
        top_rpc = sorted(self.duration_by_rpc.items(), key=lambda item: item[1], reverse=True)
        top_causes_trim = [
            {"cause_code": cause, "count": count}
            for cause, count in top_causes[: max(1, self.thresholds.top_causes)]
        ]
        top_rpc_trim = []
        for rpc_key, total_ms in top_rpc[:10]:
            count = max(1, self.count_by_rpc.get(rpc_key, 1))
            top_rpc_trim.append(
                {
                    "rpc": rpc_key,
                    "total_duration_ms": total_ms,
                    "count": count,
                    "avg_duration_ms": round(total_ms / count, 2),
                }
            )
        return {
            "schema_version": SCHEMA_VERSION,
            "run_id": self.run_id,
            "profile": self.profile,
            "event_count": self.event_count,
            "threshold_breach_count": self.breach_count,
            "thresholds": asdict(self.thresholds),
            "top_causes": top_causes_trim,
            "top_rpc_by_duration": top_rpc_trim,
            "slow_alerts": self.alerts[-50:],
            "timeline": self.timeline[-200:],
        }

    def write_summary(self) -> Dict[str, Any]:
        summary = self.build_summary()
        if self.persist_files and self.summary_path is not None:
            self.summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        return summary

    def close(self) -> Dict[str, Any]:
        if self.closed:
            return self.build_summary()
        self.flush()
        summary = self.write_summary()
        self.closed = True
        return summary
