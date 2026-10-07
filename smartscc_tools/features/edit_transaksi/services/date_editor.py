"""Service to edit dates and qty on stock.picking and related records."""

from __future__ import annotations

import logging
from typing import Any

from smartscc_tools.features.edit_transaksi.models import TransactionEditRequest, TransactionEditResult
from smartscc_tools.features.edit_transaksi.services.journal_resolver import JournalResolverAsync
from smartscc_tools.features.item_journal.config import RuntimeSettings
from smartscc_tools.services.odoo.gateway import AsyncOdooJsonRpcClient, build_company_context
from smartscc_tools.features.item_journal.services.date_ops import AccountMoveDateSyncServiceAsync, DateSyncServiceAsync


class TransactionDateEditorServiceAsync:
    """Update dates and qty across stock transaction records."""

    def __init__(
        self,
        rpc: AsyncOdooJsonRpcClient,
        settings: RuntimeSettings,
        logger: logging.Logger,
    ) -> None:
        self.rpc = rpc
        self.settings = settings
        self.logger = logger

    async def update_dates(self, request: TransactionEditRequest) -> TransactionEditResult:
        messages: list[str] = []
        errors: list[str] = []
        sync_service = DateSyncServiceAsync(rpc=self.rpc, settings=self.settings, logger=self.logger)
        journal_sync_service = AccountMoveDateSyncServiceAsync(rpc=self.rpc, settings=self.settings, logger=self.logger)
        capabilities = await sync_service.initialize_capabilities()
        stock_context = build_company_context(request.company_id)
        stock_context["check_move_validity"] = False

        move_ids = await self.rpc.search(
            model="stock.move",
            domain=[["picking_id", "=", request.picking_id]],
            context=stock_context,
            stage="FETCH_MOVE_IDS",
        )
        journal_ids: list[int] = []
        if request.new_journal_date:
            resolver = JournalResolverAsync(rpc=self.rpc, logger=self.logger)
            journal_ids, journal_source = await resolver.resolve_account_move_ids_for_picking(
                picking_id=request.picking_id,
                move_ids=move_ids,
                company_id=request.company_id,
            )
            if not journal_ids:
                errors.append("Journal Date dipilih tetapi account.move terkait tidak ditemukan untuk picking ini.")
                self.logger.warning("Journal resolution empty for picking=%s source=%s", request.picking_id, journal_source)
                return TransactionEditResult(success=False, messages=messages, errors=errors)
            precheck_ok, precheck_err, _posted_ids = await journal_sync_service.precheck_account_move_dates(
                journal_ids=journal_ids,
                company_id=request.company_id,
                target_date_utc=request.new_journal_date,
            )
            if not precheck_ok:
                errors.append(precheck_err)
                self.logger.warning(
                    "Journal date precheck failed for picking=%s source=%s err=%s",
                    request.picking_id,
                    journal_source,
                    precheck_err,
                )
                return TransactionEditResult(success=False, messages=messages, errors=errors)
            self.logger.info(
                "Journal resolution for picking=%s source=%s count=%s",
                request.picking_id,
                journal_source,
                len(journal_ids),
            )
        state_info = await self._prepare_edit_state(
            picking_id=request.picking_id,
            move_ids=move_ids,
            stock_context=stock_context,
            allow_continue_without_unlock=True,
        )
        if state_info["error"]:
            errors.append(str(state_info["error"]))
            return TransactionEditResult(success=False, messages=messages, errors=errors)
        if state_info["warning"]:
            messages.append(f"warning: {state_info['warning']}")

        async def edit_dates() -> None:
            if request.new_stock_date:
                await self._sync_field(
                    sync_service=sync_service,
                    model="stock.picking",
                    ids=[request.picking_id],
                    field=capabilities.stock_picking_done,
                    company_id=request.company_id,
                    date_value=request.new_stock_date,
                    messages=messages,
                    errors=errors,
                    label="stock.picking done date",
                )
                if self._can_write_scheduled_date(state_info):
                    await self._sync_field(
                        sync_service=sync_service,
                        model="stock.picking",
                        ids=[request.picking_id],
                        field=capabilities.stock_picking_scheduled,
                        company_id=request.company_id,
                        date_value=request.new_stock_date,
                        messages=messages,
                        errors=errors,
                        label="stock.picking scheduled_date",
                        context_override=stock_context,
                    )
                else:
                    messages.append(
                        "warning: stock.picking scheduled_date dilewati karena picking done dan unlock state server "
                        "tidak kompatibel di instance ini."
                    )
                await self._sync_field(
                    sync_service=sync_service,
                    model="stock.move",
                    ids=move_ids,
                    field=capabilities.stock_move,
                    company_id=request.company_id,
                    date_value=request.new_stock_date,
                    messages=messages,
                    errors=errors,
                    label="stock.move date",
                    context_override=stock_context,
                )
                move_line_ids = await self.rpc.search(
                    model="stock.move.line",
                    domain=[["move_id", "in", move_ids]],
                    context=stock_context,
                    stage="FETCH_MOVE_LINE_IDS",
                )
                await self._sync_field(
                    sync_service=sync_service,
                    model="stock.move.line",
                    ids=move_line_ids,
                    field=capabilities.stock_move_line,
                    company_id=request.company_id,
                    date_value=request.new_stock_date,
                    messages=messages,
                    errors=errors,
                    label="stock.move.line date",
                    context_override=stock_context,
                )

            if request.new_svl_date and capabilities.stock_valuation_layer is not None:
                svl_ids = await self.rpc.search(
                    model="stock.valuation.layer",
                    domain=[["stock_move_id", "in", move_ids]],
                    context=stock_context,
                    stage="FETCH_SVL_IDS",
                )
                await self._sync_field(
                    sync_service=sync_service,
                    model="stock.valuation.layer",
                    ids=svl_ids,
                    field=capabilities.stock_valuation_layer,
                    company_id=request.company_id,
                    date_value=request.new_svl_date,
                    messages=messages,
                    errors=errors,
                    label="stock.valuation.layer date",
                )

            if request.new_journal_date:
                ok, err, warn = await journal_sync_service.sync_account_move_dates(
                    journal_ids=journal_ids,
                    company_id=request.company_id,
                    target_date_utc=request.new_journal_date,
                )
                if ok:
                    messages.append(f"account.move date ({len(journal_ids)} records) -> {request.new_journal_date}")
                    if warn:
                        messages.append(f"account.move warning: {warn}")
                else:
                    errors.append(f"account.move date: {err}")

        try:
            await edit_dates()
        finally:
            await self._restore_edit_state(state_info=state_info, errors=errors)

        self.logger.info("Date edit result: %d success, %d errors", len(messages), len(errors))
        return TransactionEditResult(
            success=len(errors) == 0,
            messages=messages,
            errors=errors,
        )

    async def update_qty(self, request: TransactionEditRequest) -> TransactionEditResult:
        messages: list[str] = []
        errors: list[str] = []
        if request.new_qty is None:
            return TransactionEditResult(success=False, errors=["Qty baru belum diisi."])
        if request.target_move_line_id <= 0 or request.target_move_id <= 0:
            return TransactionEditResult(success=False, errors=["Move line atau move target belum valid."])
        if request.target_svl_id <= 0:
            return TransactionEditResult(
                success=False,
                errors=["Qty tidak dapat diubah karena mapping valuation layer tidak tunggal atau tidak tersedia."],
            )

        stock_context = build_company_context(request.company_id)
        stock_context["check_move_validity"] = False
        move_ids = await self.rpc.search(
            model="stock.move",
            domain=[["picking_id", "=", request.picking_id]],
            context=stock_context,
            stage="FETCH_MOVE_IDS",
        )
        state_info = await self._prepare_edit_state(
            picking_id=request.picking_id,
            move_ids=move_ids,
            stock_context=stock_context,
            allow_continue_without_unlock=True,
        )
        if state_info["error"]:
            errors.append(str(state_info["error"]))
            return TransactionEditResult(success=False, messages=messages, errors=errors)
        if state_info["warning"]:
            messages.append(f"warning: {state_info['warning']}")

        move_line_field = await self._resolve_numeric_field(
            model="stock.move.line",
            candidates=["qty_done", "quantity"],
            stage="FETCH_MOVE_LINE_QTY_META",
        )
        move_field = await self._resolve_numeric_field(
            model="stock.move",
            candidates=["product_qty", "product_uom_qty"],
            stage="FETCH_MOVE_QTY_META",
        )
        svl_field = await self._resolve_numeric_field(
            model="stock.valuation.layer",
            candidates=["quantity"],
            stage="FETCH_SVL_QTY_META",
        )
        qty_value = float(request.new_qty)

        async def edit_qty() -> None:
            await self._write_numeric_field(
                model="stock.move.line",
                ids=[request.target_move_line_id],
                field_name=move_line_field,
                value=qty_value,
                context=stock_context,
                label="stock.move.line qty",
                messages=messages,
                errors=errors,
            )
            await self._write_numeric_field(
                model="stock.move",
                ids=[request.target_move_id],
                field_name=move_field,
                value=qty_value,
                context=stock_context,
                label="stock.move qty",
                messages=messages,
                errors=errors,
            )
            await self._write_numeric_field(
                model="stock.valuation.layer",
                ids=[request.target_svl_id],
                field_name=svl_field,
                value=qty_value,
                context=stock_context,
                label="stock.valuation.layer qty",
                messages=messages,
                errors=errors,
            )

        try:
            await edit_qty()
        finally:
            await self._restore_edit_state(state_info=state_info, errors=errors)

        self.logger.info("Qty edit result: %d success, %d errors", len(messages), len(errors))
        return TransactionEditResult(
            success=len(errors) == 0,
            messages=messages,
            errors=errors,
        )

    async def _prepare_edit_state(
        self,
        *,
        picking_id: int,
        move_ids: list[int],
        stock_context: dict[str, Any],
        allow_continue_without_unlock: bool,
    ) -> dict[str, Any]:
        rows = await self.rpc.read(
            model="stock.picking",
            ids=[picking_id],
            fields=["state"],
            context=stock_context,
            stage="READ_PICKING_STATE",
        )
        state = str((rows[0] if rows else {}).get("state") or "").strip().lower()
        state_info: dict[str, Any] = {
            "state": state,
            "move_ids": move_ids[:],
            "unlocked": False,
            "unlock_context": {**stock_context, "tracking_disable": True},
            "warning": "",
            "error": "",
        }
        if state == "cancel":
            state_info["error"] = "Transaksi yang sudah dibatalkan (cancelled) tidak dapat diedit."
            return state_info

        if state == "done":
            if not move_ids:
                state_info["error"] = "Transaksi done tidak dapat diedit karena stock.move terkait tidak ditemukan."
                return state_info
            try:
                ok = await self.rpc.write(
                    model="stock.move",
                    ids=move_ids,
                    values={"state": "assigned"},
                    context=state_info["unlock_context"],
                    stage="EDIT_STATE_UNLOCK",
                )
            except Exception as exc:  # noqa: BLE001
                if allow_continue_without_unlock and self._is_server_schema_error(exc):
                    state_info["warning"] = (
                        "Unlock state picking done dilewati karena server Odoo mengembalikan error schema saat "
                        "write stock.move. Operasi akan mencoba write langsung untuk field yang masih kompatibel."
                    )
                    self.logger.warning("Skip done unlock for picking=%s due schema error: %s", picking_id, exc)
                    return state_info
                state_info["error"] = f"Gagal unlock state picking done: {exc}"
                return state_info
            if not ok:
                state_info["error"] = "Gagal unlock state picking done: RPC stock.move.write mengembalikan False."
                return state_info
            state_info["unlocked"] = True
        return state_info

    async def _restore_edit_state(
        self,
        *,
        state_info: dict[str, Any],
        errors: list[str],
    ) -> None:
        if not bool(state_info.get("unlocked")):
            return
        try:
            ok = await self.rpc.write(
                model="stock.move",
                ids=list(state_info.get("move_ids") or []),
                values={"state": "done"},
                context=dict(state_info.get("unlock_context") or {}),
                stage="EDIT_STATE_RESTORE",
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"Gagal restore state picking ke done: {exc}")
        else:
            if not ok:
                errors.append("Gagal restore state picking ke done: RPC stock.move.write mengembalikan False.")

    @staticmethod
    def _can_write_scheduled_date(state_info: dict[str, Any]) -> bool:
        state = str(state_info.get("state") or "").strip().lower()
        if state != "done":
            return True
        return bool(state_info.get("unlocked"))

    @staticmethod
    def _is_server_schema_error(exc: Exception) -> bool:
        text = str(exc or "").strip().lower()
        if "undefinedcolumn" in text:
            return True
        return "column" in text and "does not exist" in text

    async def _resolve_numeric_field(
        self,
        *,
        model: str,
        candidates: list[str],
        stage: str,
    ) -> str:
        meta = await self.rpc.fields_get(
            model=model,
            attributes=["type", "readonly"],
            stage=stage,
        )
        for candidate in candidates:
            field_meta = meta.get(candidate)
            if not isinstance(field_meta, dict):
                continue
            field_type = str(field_meta.get("type") or "").strip().lower()
            is_readonly = self._to_bool(field_meta.get("readonly"))
            if field_type in {"float", "integer", "monetary"} and not is_readonly:
                return candidate
        raise RuntimeError(f"{model}: field numerik writable tidak ditemukan. kandidat={candidates}")

    async def _write_numeric_field(
        self,
        *,
        model: str,
        ids: list[int],
        field_name: str,
        value: float,
        context: dict[str, Any],
        label: str,
        messages: list[str],
        errors: list[str],
    ) -> None:
        if not ids:
            return
        try:
            ok = await self.rpc.write(
                model=model,
                ids=ids,
                values={field_name: value},
                context=context,
                stage="QTY_WRITE",
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{label}: {exc}")
            return
        if ok:
            messages.append(f"{label} ({len(ids)} records) -> {value}")
            return
        errors.append(f"{label}: RPC {model}.write mengembalikan False.")

    async def _sync_field(
        self,
        *,
        sync_service: DateSyncServiceAsync,
        model: str,
        ids: list[int],
        field,
        company_id: int,
        date_value: str,
        messages: list[str],
        errors: list[str],
        label: str,
        context_override: dict | None = None,
    ) -> None:
        if not ids:
            return
        try:
            ok, err = await sync_service._write_date_for_ids(  # noqa: SLF001
                model=model,
                ids=ids,
                field=field,
                company_id=company_id,
                date_done_utc=date_value,
                verify_only=False,
                context_override=context_override,
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{label}: {exc}")
            return
        if ok:
            messages.append(f"{label} ({len(ids)} records) -> {date_value}")
            return
        errors.append(f"{label}: {err}")

    @staticmethod
    def _to_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value != 0
        return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}
