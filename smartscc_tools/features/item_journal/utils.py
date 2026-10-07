"""Shared utility helpers."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, List


def normalize_text(value: Any) -> str:
    return str(value or "").replace("\xa0", " ").strip()


def to_int(value: Any) -> int:
    try:
        if value is None:
            return 0
        if isinstance(value, bool):
            return 1 if value else 0
        text = normalize_text(value)
        if text == "":
            return 0
        return int(float(text))
    except (TypeError, ValueError):
        return 0


def to_float(value: Any) -> float:
    try:
        if value is None:
            return 0.0
        if isinstance(value, bool):
            return float(value)
        text = normalize_text(value)
        if text == "":
            return 0.0
        return float(text)
    except (TypeError, ValueError):
        return 0.0


def parse_iso_date(text: str) -> date | None:
    clean = normalize_text(text)
    if len(clean) >= 10 and clean[4:5] == "-" and clean[7:8] == "-":
        clean = clean[:10]
        try:
            return datetime.strptime(clean, "%Y-%m-%d").date()
        except ValueError:
            return None
    return None


def parse_datetime_text(text: str) -> datetime | None:
    clean = normalize_text(text).replace("T", " ")
    if len(clean) >= 19:
        clean = clean[:19]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(clean, fmt)
        except ValueError:
            continue
    return None


def excel_value_to_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)) and float(value) > 0:
        base = datetime(1899, 12, 30)
        return (base + timedelta(days=float(value))).date()
    clean = normalize_text(value)
    if not clean:
        return None
    parsed = parse_iso_date(clean)
    if parsed:
        return parsed
    for fmt in ("%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(clean, fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(clean).date()
    except ValueError:
        return None


def format_decimal(value: float) -> float:
    return float(f"{value:.12g}")


def append_error(existing: str, addition: str) -> str:
    left = normalize_text(existing)
    right = normalize_text(addition)
    if not right:
        return left
    if not left:
        return right
    return f"{left} | {right}"


def is_retryable_error(text: str) -> bool:
    clean = normalize_text(text).lower()
    if not clean:
        return False
    retryable_tokens = [
        "timeout",
        "timed out",
        "connection reset",
        "response kosong",
        "payload too large",
        "entity too large",
        "bad gateway",
        "gateway timeout",
    ]
    if any(token in clean for token in retryable_tokens):
        return True

    if "http status" in clean:
        status = extract_http_status(clean)
        if status in {408, 429}:
            return True
        if status >= 500:
            return True
    return False


def extract_http_status(text: str) -> int:
    lower = text.lower()
    marker = "http status"
    pos = lower.find(marker)
    if pos < 0:
        return 0
    fragment = lower[pos + len(marker) :]
    digits = []
    started = False
    for ch in fragment:
        if ch.isdigit():
            digits.append(ch)
            started = True
        elif started:
            break
    if not digits:
        return 0
    try:
        return int("".join(digits))
    except ValueError:
        return 0


def utc_string_from_local_date(local_date: date, local_tz_offset_hours: int, use_local_as_utc: bool) -> str:
    local_dt = datetime(local_date.year, local_date.month, local_date.day, 0, 0, 0)
    if use_local_as_utc:
        utc_dt = local_dt
    else:
        utc_dt = local_dt - timedelta(hours=local_tz_offset_hours)
    return utc_dt.strftime("%Y-%m-%d %H:%M:%S")


def local_date_from_utc_text(utc_text: str, local_tz_offset_hours: int, use_local_as_utc: bool) -> date | None:
    dt = parse_datetime_text(utc_text)
    if dt is None:
        parsed = parse_iso_date(utc_text)
        return parsed
    if not use_local_as_utc:
        dt = dt + timedelta(hours=local_tz_offset_hours)
    return dt.date()


def chunked(items: List[Any], chunk_size: int) -> Iterable[List[Any]]:
    if chunk_size <= 0:
        chunk_size = 1
    for idx in range(0, len(items), chunk_size):
        yield items[idx : idx + chunk_size]


def unique_ordered(items: Iterable[Any]) -> List[Any]:
    seen = set()
    out: List[Any] = []
    for item in items:
        key = str(item)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def format_utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def compute_hybrid_delay_ms(
    next_attempt: int,
    base_delay_ms: int,
    fast_attempts: int,
    max_delay_ms: int,
) -> int:
    base = max(0, int(base_delay_ms or 0))
    if base <= 0:
        return 0
    fast = max(0, int(fast_attempts or 0))
    cap = max(base, int(max_delay_ms or 0))
    if next_attempt <= max(1, fast):
        return min(base, cap)
    exponent = max(1, next_attempt - max(1, fast))
    return min(base * (2**exponent), cap)
