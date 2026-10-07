"""Typed models for Update Standard Cost runs."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class UpdateStdCostRunRequest:
    workbook_path: str
    database: str
    mode: str
    dry_run: bool = False
    sheet_name: str = "Preparation Cost"


@dataclass
class UpdateStdCostProgressSnapshot:
    phase: str = ""
    current: str = ""
    processed: int = 0
    total: int = 0
    progress: float = 0.0


@dataclass
class UpdateStdCostRowResult:
    row_number: int
    mode: str
    status: str
    area: str = ""
    product_code: str = ""
    message: str = ""


@dataclass
class UpdateStdCostModeSummary:
    mode: str
    updated_rows: int = 0
    failed_rows: int = 0
    write_ok: int = 0
    write_fail: int = 0
    missing_areas: int = 0
    missing_products: int = 0
    duplicate_codes: int = 0
    modified: bool = False
    fatal: bool = False
    stopped: bool = False
    message: str = ""

    def render(self) -> str:
        lines = [self.message] if self.message else []
        if self.updated_rows or self.failed_rows:
            lines.append(f"Baris berhasil: {self.updated_rows}")
            lines.append(f"Baris gagal: {self.failed_rows}")
        if self.missing_areas:
            lines.append(f"Area tanpa company: {self.missing_areas}")
        if self.missing_products:
            lines.append(f"Kode tidak ditemukan: {self.missing_products}")
        if self.duplicate_codes:
            lines.append(f"Kode duplikat global: {self.duplicate_codes}")
        if self.write_ok or self.write_fail:
            lines.append(f"Write sukses: {self.write_ok}")
            lines.append(f"Write gagal: {self.write_fail}")
        if self.stopped:
            lines.append("Dihentikan oleh user.")
        return "\n".join(lines).strip()


@dataclass
class UpdateStdCostRunSummary:
    requested_mode: str
    database: str
    workbook_path: str
    dry_run: bool = False
    mode_summaries: list[UpdateStdCostModeSummary] = field(default_factory=list)
    results: list[UpdateStdCostRowResult] = field(default_factory=list)
    total_rows: int = 0
    stopped: bool = False

    @property
    def updated_rows(self) -> int:
        return sum(summary.updated_rows for summary in self.mode_summaries)

    @property
    def failed_rows(self) -> int:
        return sum(summary.failed_rows for summary in self.mode_summaries)
