"""Service to read stock.picking detail from Odoo."""

from __future__ import annotations

from collections import defaultdict
import logging
from typing import Any

from smartscc_tools.features.edit_transaksi.models import PickingDetail, PickingLineItem
from smartscc_tools.features.edit_transaksi.services.journal_resolver import JournalResolverAsync
from smartscc_tools.services.odoo.gateway import AsyncOdooJsonRpcClient, extract_many2one_id


class PickingReaderServiceAsync:
    """Fetch a stock.picking by name and load all related records."""

    def __init__(self, rpc: AsyncOdooJsonRpcClient, logger: logging.Logger) -> None:
        self.rpc = rpc
        self.logger = logger

    async def fetch_picking_by_name(self, picking_name: str) -> PickingDetail:
        name = picking_name.strip()
        if not name:
            raise ValueError("Nama picking tidak boleh kosong.")

        self.logger.info("Mencari picking: %s", name)

        pickings = await self.rpc.search_read(
            model="stock.picking",
            domain=[["name", "=", name]],
            fields=[
                "id",
                "name",
                "state",
                "date_done",
                "scheduled_date",
                "origin",
                "picking_type_id",
                "location_id",
                "location_dest_id",
                "company_id",
            ],
            stage="FETCH_PICKING",
        )
        if not pickings:
            raise ValueError(f"Picking '{name}' tidak ditemukan di Odoo.")

        picking = pickings[0]
        picking_id = picking["id"]
        company_id = extract_many2one_id(picking.get("company_id"))
        self.logger.info("Picking ditemukan: id=%s state=%s", picking_id, picking.get("state"))

        move_meta = await self.rpc.fields_get(
            model="stock.move",
            attributes=["type"],
            stage="FETCH_MOVES_META",
        )
        move_qty_field = self._pick_first_field(move_meta, "product_qty", "product_uom_qty", "quantity_done")
        move_uom_field = self._pick_first_field(move_meta, "product_uom", "product_uom_id")
        move_fields = ["id", "product_id", "date", "state"]
        if move_qty_field:
            move_fields.append(move_qty_field)
        if move_uom_field:
            move_fields.append(move_uom_field)

        moves = await self.rpc.search_read(
            model="stock.move",
            domain=[["picking_id", "=", picking_id]],
            fields=move_fields,
            stage="FETCH_MOVES",
        )
        self.logger.info("Stock moves: %d record", len(moves))

        for move in moves:
            if move_qty_field:
                move["_move_qty_field"] = move_qty_field
            if move_uom_field:
                move["_move_uom_field"] = move_uom_field

        move_ids = [int(move["id"]) for move in moves if int(move.get("id") or 0) > 0]
        move_by_id = {int(move["id"]): move for move in moves if int(move.get("id") or 0) > 0}

        move_lines: list[dict[str, Any]] = []
        move_line_done_qty_field = ""
        move_line_display_qty_field = ""
        if move_ids:
            move_line_meta = await self.rpc.fields_get(
                model="stock.move.line",
                attributes=["type"],
                stage="FETCH_MOVE_LINES_META",
            )
            move_line_done_qty_field = self._pick_first_field(move_line_meta, "qty_done", "quantity")
            move_line_display_qty_field = self._pick_first_field(
                move_line_meta,
                "quantity",
                "reserved_uom_qty",
                "reserved_qty",
                "product_uom_qty",
                "qty_done",
            )
            move_line_fields = ["id", "product_id", "date", "location_id", "location_dest_id"]
            for field_name in (move_line_done_qty_field, move_line_display_qty_field):
                if field_name and field_name not in move_line_fields:
                    move_line_fields.append(field_name)
            move_lines = await self._fetch_move_lines_for_moves(move_ids=move_ids, fields=move_line_fields)
        self.logger.info("Stock move lines: %d record", len(move_lines))

        display_qty_by_move_id: dict[int, float] = defaultdict(float)
        move_line_count_by_move_id: dict[int, int] = defaultdict(int)
        move_lines_by_move_id: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for line in move_lines:
            move_id = extract_many2one_id(line.get("move_id"))
            if move_id <= 0:
                continue
            move_line_count_by_move_id[move_id] += 1
            move_lines_by_move_id[move_id].append(line)
            display_qty_by_move_id[move_id] += self._to_float(
                line.get(move_line_display_qty_field or move_line_done_qty_field or "qty_done")
            )
            if move_line_done_qty_field:
                line["_move_line_qty_field"] = move_line_done_qty_field
            if move_line_display_qty_field:
                line["_display_line_qty_field"] = move_line_display_qty_field

        for move in moves:
            move_id = int(move.get("id") or 0)
            move["_display_qty_done"] = display_qty_by_move_id.get(move_id, self._to_float(move.get(move_qty_field or "")))

        svl_records: list[dict[str, Any]] = []
        svls_by_move_id: dict[int, list[dict[str, Any]]] = defaultdict(list)
        if move_ids:
            svl_meta = await self.rpc.fields_get(
                model="stock.valuation.layer",
                attributes=["type"],
                stage="FETCH_SVL_META",
            )
            svl_date_field = self._pick_first_field(svl_meta, "accounting_date", "date", "create_date")
            svl_fields = ["id", "create_date", "value", "quantity", "product_id"]
            if svl_date_field and svl_date_field not in svl_fields:
                svl_fields.append(svl_date_field)
            svl_records = await self._fetch_svl_for_moves(move_ids=move_ids, fields=svl_fields)
            for svl in svl_records:
                if svl_date_field:
                    svl["_svl_date_field"] = svl_date_field
                move_id = extract_many2one_id(svl.get("stock_move_id"))
                if move_id > 0:
                    svls_by_move_id[move_id].append(svl)
        self.logger.info("SVL records: %d record", len(svl_records))

        journal_entries: list[dict[str, Any]] = []
        journal_source = "none"
        if company_id > 0:
            resolver = JournalResolverAsync(rpc=self.rpc, logger=self.logger)
            journal_ids, journal_source = await resolver.resolve_account_move_ids_for_picking(
                picking_id=picking_id,
                move_ids=move_ids,
                company_id=company_id,
                picking_name=str(picking.get("name") or ""),
            )
            if journal_ids:
                journal_entries = await self.rpc.read(
                    model="account.move",
                    ids=journal_ids,
                    fields=["id", "name", "date", "ref", "state", "journal_id"],
                    stage="FETCH_JOURNAL_ENTRIES",
                )
        self.logger.info("Journal entries: %d record (source=%s)", len(journal_entries), journal_source)

        line_items: list[PickingLineItem] = []
        for line in move_lines:
            move_id = extract_many2one_id(line.get("move_id"))
            move = move_by_id.get(move_id, {})
            svl_candidates = svls_by_move_id.get(move_id, [])
            line_count = int(move_line_count_by_move_id.get(move_id, 0))
            single_svl = svl_candidates[0] if len(svl_candidates) == 1 else None
            svl_qty_editable = line_count == 1 and len(svl_candidates) == 1
            svl_warning = ""
            if not svl_qty_editable:
                if len(svl_candidates) == 0:
                    svl_warning = "SVL quantity tidak dapat diedit karena valuation layer belum ditemukan."
                elif len(svl_candidates) > 1:
                    svl_warning = "SVL quantity diblok karena 1 move memiliki lebih dari 1 valuation layer."
                elif line_count > 1:
                    svl_warning = "SVL quantity diblok karena 1 move memiliki lebih dari 1 move line."

            prod_value = line.get("product_id") or move.get("product_id")
            uom_value = move.get(str(move.get("_move_uom_field") or "product_uom"))
            line_items.append(
                PickingLineItem(
                    move_line_id=int(line.get("id") or 0),
                    move_id=move_id,
                    product_name=self._many2one_name(prod_value),
                    uom_name=self._many2one_name(uom_value),
                    line_qty=self._to_float(line.get(move_line_display_qty_field or move_line_done_qty_field or "qty_done")),
                    qty_done=self._to_float(line.get(move_line_done_qty_field or "qty_done")),
                    move_qty=self._to_float(move.get(str(move.get("_move_qty_field") or "product_qty"))),
                    stock_date=str(line.get("date") or move.get("date") or ""),
                    state=str(move.get("state") or ""),
                    svl_id=int(single_svl.get("id") or 0) if single_svl else 0,
                    svl_qty=self._to_float(single_svl.get("quantity")) if single_svl else None,
                    svl_qty_editable=svl_qty_editable,
                    svl_warning=svl_warning,
                )
            )

        return PickingDetail(
            picking=picking,
            moves=moves,
            move_lines=move_lines,
            svl_records=svl_records,
            journal_entries=journal_entries,
            line_items=line_items,
        )

    @staticmethod
    def _pick_first_field(meta: dict[str, Any], *candidates: str) -> str:
        for candidate in candidates:
            if candidate in meta:
                return candidate
        return ""

    async def _fetch_move_lines_for_moves(
        self,
        *,
        move_ids: list[int],
        fields: list[str],
    ) -> list[dict[str, Any]]:
        move_lines: list[dict[str, Any]] = []
        for move_id in move_ids:
            line_ids = await self.rpc.search(
                model="stock.move.line",
                domain=[["move_id", "=", int(move_id)]],
                stage="FETCH_MOVE_LINES",
            )
            if not line_ids:
                continue
            rows = await self.rpc.read(
                model="stock.move.line",
                ids=line_ids,
                fields=fields,
                stage="FETCH_MOVE_LINES",
            )
            for row in rows:
                row["move_id"] = int(move_id)
                move_lines.append(row)
        return move_lines

    async def _fetch_svl_for_moves(
        self,
        *,
        move_ids: list[int],
        fields: list[str],
    ) -> list[dict[str, Any]]:
        svl_records: list[dict[str, Any]] = []
        for move_id in move_ids:
            svl_ids = await self.rpc.search(
                model="stock.valuation.layer",
                domain=[["stock_move_id", "=", int(move_id)]],
                stage="FETCH_SVL",
            )
            if not svl_ids:
                continue
            rows = await self.rpc.read(
                model="stock.valuation.layer",
                ids=svl_ids,
                fields=fields,
                stage="FETCH_SVL",
            )
            for row in rows:
                row["stock_move_id"] = int(move_id)
                svl_records.append(row)
        return svl_records

    @staticmethod
    def _many2one_name(value: Any) -> str:
        if isinstance(value, (list, tuple)) and len(value) > 1:
            return str(value[1])
        return str(value or "")

    @staticmethod
    def _to_float(value: Any) -> float:
        try:
            return float(value or 0)
        except (TypeError, ValueError):
            return 0.0
