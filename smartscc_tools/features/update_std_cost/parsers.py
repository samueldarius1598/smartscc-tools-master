"""Parsing helpers for Update Standard Cost workflow."""

from __future__ import annotations

import locale
import math
import re
from typing import Iterable, List, Sequence, TypeVar


T = TypeVar("T")
_FULL_NUMERIC_RE = re.compile(r"^[+-]?(\d+(\.\d+)?|\.\d+)$")


def normalize_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip().replace("\xa0", "")


def normalize_config_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).replace("\xa0", " ").strip()


def trim_trailing_slash(text: str) -> str:
    out = (text or "").strip()
    while out.endswith("/"):
        out = out[:-1]
    return out


def parse_number_smart_local(value: object, decimal_separator: str | None = None) -> float:
    if isinstance(value, bool):
        raise ValueError("Boolean is not a numeric input")
    if isinstance(value, (int, float)):
        number = float(value)
        if math.isnan(number) or math.isinf(number):
            raise ValueError("Non-finite numeric input")
        return number

    text = str(value).strip()
    if not text:
        raise ValueError("Empty value")

    text = text.replace("\xa0", "").replace(" ", "")
    has_dot = "." in text
    has_comma = "," in text

    if has_dot and has_comma:
        if text.rfind(".") > text.rfind(","):
            dec_guess = "."
            grp_guess = ","
        else:
            dec_guess = ","
            grp_guess = "."
    elif has_comma:
        dec_guess = ","
        grp_guess = ""
    elif has_dot:
        dec_guess = "."
        grp_guess = ""
    else:
        decimal_separator = decimal_separator or (locale.localeconv().get("decimal_point", ".") or ".")
        dec_guess = decimal_separator
        grp_guess = ""

    if grp_guess:
        text = text.replace(grp_guess, "")
    if dec_guess and dec_guess != ".":
        text = text.replace(dec_guess, ".")
    if not _FULL_NUMERIC_RE.match(text):
        raise ValueError(f"Invalid numeric value: {value!r}")
    return float(text)


def json_number(value: float) -> str:
    text = format(float(value), ".15g").strip().replace(",", ".")
    if "e" in text.lower():
        text = format(float(value), ".15f").rstrip("0").rstrip(".")
        if not text:
            text = "0"
    if text == "-0":
        text = "0"
    return text


def chunked(items: Sequence[T] | Iterable[T], size: int) -> List[List[T]]:
    normalized = list(items)
    if size <= 0:
        size = 100
    return [normalized[index : index + size] for index in range(0, len(normalized), size)]


def normalize_company_id_key(value: object) -> str:
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, (int, float)):
        try:
            return str(int(float(value)))
        except (ValueError, OverflowError):
            return ""

    text = str(value).strip().replace("\xa0", "")
    if not text:
        return ""
    if _FULL_NUMERIC_RE.match(text):
        try:
            return str(int(float(text)))
        except (ValueError, OverflowError):
            return ""
    return text
