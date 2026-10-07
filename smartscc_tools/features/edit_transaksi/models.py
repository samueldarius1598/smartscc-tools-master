"""Data models for Edit Transaksi Item Movement."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PickingLineItem:
    """Line-level display model for one stock.move.line."""

    move_line_id: int
    move_id: int
    product_name: str
    uom_name: str
    line_qty: float
    qty_done: float
    move_qty: float
    stock_date: str
    state: str
    svl_id: int = 0
    svl_qty: float | None = None
    svl_qty_editable: bool = False
    svl_warning: str = ""


@dataclass
class PickingDetail:
    """All details of a stock.picking and its related records."""

    picking: dict[str, Any] = field(default_factory=dict)
    moves: list[dict[str, Any]] = field(default_factory=list)
    move_lines: list[dict[str, Any]] = field(default_factory=list)
    svl_records: list[dict[str, Any]] = field(default_factory=list)
    journal_entries: list[dict[str, Any]] = field(default_factory=list)
    line_items: list[PickingLineItem] = field(default_factory=list)


@dataclass
class TransactionEditRequest:
    """Request to update dates and qty on a picking transaction."""

    picking_id: int
    company_id: int
    new_stock_date: str | None = None
    new_svl_date: str | None = None
    new_journal_date: str | None = None
    target_move_line_id: int = 0
    target_move_id: int = 0
    target_svl_id: int = 0
    new_qty: float | None = None

    def has_date_updates(self) -> bool:
        return any([self.new_stock_date, self.new_svl_date, self.new_journal_date])

    def has_qty_update(self) -> bool:
        return self.new_qty is not None


@dataclass
class TransactionEditResult:
    """Result of a transaction edit operation."""

    success: bool = True
    messages: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


DateEditRequest = TransactionEditRequest
DateEditResult = TransactionEditResult
