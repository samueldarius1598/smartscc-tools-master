"""Shared helpers for CLI and GUI entrypoints."""

from __future__ import annotations

from typing import Any

from smartscc_tools.features.item_journal.workbook import SaveResult, build_summary_copy_name_stem


def build_copy_name_stem_from_summary(summary: dict[str, Any]) -> str:
    company_name = str(summary.get("company_name", "") or "").strip()
    processed_rows = (
        int(summary.get("rows_success", 0) or 0)
        + int(summary.get("rows_error", 0) or 0)
        + int(summary.get("rows_stopped", 0) or 0)
    )
    if processed_rows <= 0:
        processed_rows = int(summary.get("active_rows", 0) or 0)
    return build_summary_copy_name_stem(company_name=company_name, processed_rows=processed_rows)


def log_save_result(
    logger: Any,
    save_mode_requested: str,
    result: SaveResult,
) -> None:
    logger.info(
        "Save result: save_mode_requested=%s, save_mode_used=%s, saved_path=%s, saved_path_length=%s, used_temp_fallback=%s, warning=%s",
        save_mode_requested,
        result.mode_used,
        result.path,
        len(str(result.path)),
        result.used_temp_fallback,
        result.warning or "-",
    )
    if result.warning:
        logger.warning(result.warning)

