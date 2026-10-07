"""Shared label helpers for PCB repair planned lines."""

from __future__ import annotations

from typing import Any

from smartscc_tools.features.item_journal.utils import normalize_text


_SELISIH_HPP_LABEL = "Selisih HPP"
_SELISIH_HPP_PREFIX = f"{_SELISIH_HPP_LABEL} - "
_LEGACY_SELISIH_HPP_SUFFIX = f" - {_SELISIH_HPP_LABEL}"


def build_pcb_selisih_hpp_label(base_line_label: Any) -> str:
    base_label = normalize_text(base_line_label)
    if not base_label:
        return _SELISIH_HPP_LABEL
    return f"{_SELISIH_HPP_PREFIX}{base_label}"


def normalize_pcb_planned_line_label(
    *,
    role: Any,
    line_label: Any,
    default_line_label: Any = "",
) -> str:
    clean_role = normalize_text(role).lower()
    clean_label = normalize_text(line_label)
    fallback_label = normalize_text(default_line_label)
    if clean_role != "selisih_hpp":
        return clean_label or fallback_label
    if not clean_label:
        return build_pcb_selisih_hpp_label(fallback_label)
    if clean_label == _SELISIH_HPP_LABEL or clean_label.startswith(_SELISIH_HPP_PREFIX):
        return clean_label
    legacy_suffix = f"{fallback_label}{_LEGACY_SELISIH_HPP_SUFFIX}" if fallback_label else ""
    if legacy_suffix and clean_label == legacy_suffix:
        return build_pcb_selisih_hpp_label(fallback_label)
    return clean_label
