"""Export helpers for SVL Fix JE results."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from openpyxl import Workbook

from .config import RESULT_SHEET_NAME
from .models import SvlFixJeRunSummary


def export_results_to_excel(summary: SvlFixJeRunSummary, output_path: str) -> Path:
    path = Path(output_path).expanduser().resolve()
    workbook = Workbook()
    try:
        worksheet = workbook.active
        worksheet.title = RESULT_SHEET_NAME
        worksheet.append(
            [
                "No",
                "SVL ID",
                "SVL Ref",
                "Total Value",
                "Status",
                "Move ID",
                "Error Message",
                "Timestamp",
            ]
        )

        for index, result in enumerate(summary.sorted_results(), start=1):
            worksheet.append(
                [
                    index,
                    result.svl_id,
                    result.svl_ref,
                    result.total_value,
                    result.status,
                    result.move_id,
                    result.error_message,
                    result.timestamp or datetime.now().isoformat(timespec="seconds"),
                ]
            )

        path.parent.mkdir(parents=True, exist_ok=True)
        workbook.save(path)
        return path
    finally:
        workbook.close()
