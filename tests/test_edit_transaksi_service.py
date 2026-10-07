import asyncio
import logging
import unittest
from types import SimpleNamespace
from unittest import mock

from smartscc_tools.features.edit_transaksi.models import TransactionEditRequest
from smartscc_tools.features.edit_transaksi.services.date_editor import TransactionDateEditorServiceAsync
from smartscc_tools.features.edit_transaksi.services.journal_resolver import JournalResolverAsync
from smartscc_tools.features.edit_transaksi.services.picking_reader import PickingReaderServiceAsync
from smartscc_tools.features.item_journal.config import RuntimeSettings
from smartscc_tools.features.item_journal.services.date_ops import AccountMoveDateSyncServiceAsync, DateFieldCapability, DateSyncCapabilities, DateSyncServiceAsync
from smartscc_tools.core.global_config import GlobalSettings
from smartscc_tools.modules.edit_transaksi_module import build_runtime_connection


class _FakeRpc:
    def __init__(self) -> None:
        self.meta = {
            "res.company": {"fiscalyear_lock_date": {"type": "date"}},
            "stock.picking": {
                "date_done": {"type": "datetime", "readonly": False},
                "scheduled_date": {"type": "datetime", "readonly": False},
                "name": {"type": "char", "readonly": False},
            },
            "stock.move": {
                "date": {"type": "datetime", "readonly": False},
                "product_qty": {"type": "float", "readonly": False},
                "product_uom": {"type": "many2one", "readonly": False},
            },
            "stock.move.line": {
                "date": {"type": "datetime", "readonly": False},
                "qty_done": {"type": "float", "readonly": False},
                "quantity": {"type": "float", "readonly": False},
            },
            "stock.valuation.layer": {
                "accounting_date": {"type": "date", "readonly": False},
                "quantity": {"type": "float", "readonly": False},
            },
            "account.move": {"date": {"type": "date", "readonly": False}},
        }
        self.records = {
            "account.change.lock.date": {
                601: {
                    "id": 601,
                    "fiscalyear_lock_date": "",
                }
            },
            "res.company": {
                7: {
                    "id": 7,
                    "fiscalyear_lock_date": "",
                }
            },
            "stock.picking": {
                1: {
                    "id": 1,
                    "name": "WH/INT/00123",
                    "state": "done",
                    "date_done": "2026-03-01 10:00:00",
                    "scheduled_date": "2026-03-01 10:00:00",
                    "origin": "Manual",
                    "location_id": (11, "WH/Stock"),
                    "location_dest_id": (12, "WH/Transit"),
                    "company_id": 7,
                }
            },
            "stock.move": {
                101: {
                    "id": 101,
                    "picking_id": 1,
                    "product_id": (301, "Product A"),
                    "product_qty": 2.0,
                    "product_uom": (401, "Units"),
                    "date": "2026-03-01 10:00:00",
                    "state": "done",
                }
            },
            "stock.move.line": {
                201: {
                    "id": 201,
                    "move_id": 101,
                    "product_id": (301, "Product A"),
                    "qty_done": 2.0,
                    "quantity": 2.0,
                    "date": "2026-03-01 10:00:00",
                    "location_id": (11, "WH/Stock"),
                    "location_dest_id": (12, "WH/Transit"),
                }
            },
            "stock.valuation.layer": {
                301: {
                    "id": 301,
                    "stock_move_id": 101,
                    "accounting_date": "2026-03-01",
                    "create_date": "2026-03-01 10:00:00",
                    "value": 100.0,
                    "quantity": 2.0,
                    "product_id": (301, "Product A"),
                }
            },
            "account.move": {
                401: {
                    "id": 401,
                    "name": "STJ/2026/0001",
                    "ref": "WH/INT/00123",
                    "date": "2026-03-01",
                    "state": "posted",
                    "journal_id": (501, "Stock Journal"),
                }
            },
        }
        self.write_failures: dict[str, Exception] = {}
        self.write_failures_by_stage: dict[tuple[str, str], Exception] = {}
        self.write_contexts: list[tuple[str, dict | None]] = []
        self.write_calls: list[dict[str, object]] = []
        self.search_calls: list[dict[str, object]] = []
        self.execute_kw_calls: list[dict[str, object]] = []
        self.execute_failures_by_stage: dict[tuple[str, str], Exception] = {}
        self.execute_failures_by_method: dict[tuple[str, str], Exception] = {}

    async def fields_get(self, model, attributes=None, context=None, stage=""):
        _ = (attributes, context, stage)
        return self.meta[model]

    async def search(self, model, domain, context=None, stage=""):
        self.search_calls.append({"model": model, "domain": domain, "context": dict(context or {}), "stage": stage})
        rows = await self.search_read(model=model, domain=domain, fields=["id"], context=context, stage=stage)
        return [int(row["id"]) for row in rows]

    async def search_read(self, model, domain, fields, context=None, stage="", limit=0, order=""):
        _ = (context, stage, order)
        rows = [record for record in self.records.get(model, {}).values() if self._matches(record, domain)]
        if limit:
            rows = rows[:limit]
        return [self._project(record, fields) for record in rows]

    async def read(self, model, ids, fields, context=None, stage=""):
        _ = (context, stage)
        return [self._project(self.records[model][record_id], fields) for record_id in ids if record_id in self.records[model]]

    async def write(self, model, ids, values, context=None, stage=""):
        self.write_calls.append(
            {
                "model": model,
                "ids": list(ids),
                "values": dict(values),
                "context": dict(context or {}),
                "stage": stage,
            }
        )
        self.write_contexts.append((model, dict(context or {})))
        failure = self.write_failures_by_stage.get((model, stage))
        if failure is not None:
            raise failure
        failure = self.write_failures.get(model)
        if failure is not None:
            raise failure
        if model == "stock.picking" and "scheduled_date" in values:
            for record_id in ids:
                state = str(self.records[model][record_id].get("state") or "").strip().lower()
                if state in {"done", "cancel"}:
                    raise RuntimeError("You cannot change the Scheduled Date on a done or cancelled transfer.")
        if model == "account.move" and "date" in values:
            for record_id in ids:
                state = str(self.records[model][record_id].get("state") or "").strip().lower()
                if state == "posted":
                    raise RuntimeError("You cannot modify the following readonly fields on a posted move: date")
        for record_id in ids:
            self.records[model][record_id].update(values)
        if model == "stock.move" and "state" in values:
            for record_id in ids:
                picking_id = int(self.records[model][record_id].get("picking_id") or 0)
                if picking_id > 0:
                    self._recompute_picking_state(picking_id)
        return True

    async def execute_kw(
        self,
        model,
        method,
        args=None,
        kwargs=None,
        stage="",
        excel_row=0,
        mutating=False,
    ):
        _ = (excel_row, mutating)
        self.execute_kw_calls.append(
            {
                "model": model,
                "method": method,
                "args": list(args or []),
                "kwargs": dict(kwargs or {}),
                "stage": stage,
            }
        )
        failure = self.execute_failures_by_stage.get((model, stage))
        if failure is not None:
            raise failure
        failure = self.execute_failures_by_method.get((model, method))
        if failure is not None:
            raise failure

        ids = []
        if args:
            raw_ids = args[0]
            if isinstance(raw_ids, list):
                ids = [int(item or 0) for item in raw_ids if int(item or 0) > 0]
        if model == "account.move" and method in {"button_draft", "action_draft"}:
            for record_id in ids:
                if record_id in self.records.get("account.move", {}):
                    self.records["account.move"][record_id]["state"] = "draft"
            return True
        if model == "account.move" and method == "action_post":
            for record_id in ids:
                if record_id in self.records.get("account.move", {}):
                    self.records["account.move"][record_id]["state"] = "posted"
            return True
        return True

    def _recompute_picking_state(self, picking_id: int) -> None:
        move_states = [
            str(record.get("state") or "").strip().lower()
            for record in self.records.get("stock.move", {}).values()
            if int(record.get("picking_id") or 0) == picking_id
        ]
        if not move_states:
            return
        if any(state == "assigned" for state in move_states):
            next_state = "assigned"
        elif any(state == "cancel" for state in move_states):
            next_state = "cancel"
        elif all(state == "done" for state in move_states):
            next_state = "done"
        else:
            next_state = move_states[0]
        self.records["stock.picking"][picking_id]["state"] = next_state

    @staticmethod
    def _matches(record, domain) -> bool:
        for field_name, operator, expected in domain:
            actual = record.get(field_name)
            if operator == "=" and actual != expected:
                return False
            if operator == "in" and actual not in expected:
                return False
        return True

    @staticmethod
    def _project(record, fields):
        payload = {}
        for field_name in fields:
            payload[field_name] = record.get(field_name)
        return payload


class EditTransaksiServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.logger = logging.getLogger("test.edit_transaksi")
        self.settings = RuntimeSettings.from_preset("safe-fast")

    @staticmethod
    def _disable_legacy_ref_link(rpc: _FakeRpc) -> None:
        rpc.records["account.move"][401]["ref"] = "UNRELATED/REF"

    @staticmethod
    def _enable_picking_account_move_ids(rpc: _FakeRpc) -> None:
        rpc.meta["stock.picking"]["account_move_ids"] = {
            "type": "many2many",
            "relation": "account.move",
        }
        rpc.records["stock.picking"][1]["account_move_ids"] = [401]

    @staticmethod
    def _enable_move_account_move_ids(rpc: _FakeRpc) -> None:
        rpc.meta["stock.move"]["account_move_ids"] = {
            "type": "many2many",
            "relation": "account.move",
        }
        rpc.records["stock.move"][101]["account_move_ids"] = [401]

    @staticmethod
    def _enable_account_move_stock_move_id(rpc: _FakeRpc) -> None:
        rpc.meta["account.move"]["stock_move_id"] = {
            "type": "many2one",
            "relation": "stock.move",
        }
        rpc.records["account.move"][401]["stock_move_id"] = 101

    @staticmethod
    def _enable_svl_account_move_id(rpc: _FakeRpc) -> None:
        rpc.meta["stock.valuation.layer"]["account_move_id"] = {
            "type": "many2one",
            "relation": "account.move",
        }
        rpc.records["stock.valuation.layer"][301]["account_move_id"] = 401

    @staticmethod
    def _set_lock_date(rpc: _FakeRpc, lock_date_text: str) -> None:
        rpc.records["account.change.lock.date"][601]["fiscalyear_lock_date"] = lock_date_text
        rpc.records["res.company"][7]["fiscalyear_lock_date"] = lock_date_text

    def test_fetch_picking_by_name_collects_related_records_and_line_items(self) -> None:
        rpc = _FakeRpc()
        service = PickingReaderServiceAsync(rpc=rpc, logger=self.logger)

        detail = asyncio.run(service.fetch_picking_by_name("WH/INT/00123"))

        self.assertEqual(detail.picking["id"], 1)
        self.assertEqual(len(detail.moves), 1)
        self.assertEqual(len(detail.move_lines), 1)
        self.assertEqual(len(detail.svl_records), 1)
        self.assertEqual(len(detail.journal_entries), 1)
        self.assertEqual(detail.moves[0]["_move_qty_field"], "product_qty")
        self.assertEqual(detail.moves[0]["_display_qty_done"], 2.0)
        self.assertEqual(detail.svl_records[0]["_svl_date_field"], "accounting_date")
        self.assertEqual(len(detail.line_items), 1)
        self.assertEqual(detail.line_items[0].move_line_id, 201)
        self.assertEqual(detail.line_items[0].line_qty, 2.0)
        self.assertTrue(detail.line_items[0].svl_qty_editable)
        self.assertEqual(detail.line_items[0].svl_qty, 2.0)

    def test_fetch_picking_by_name_avoids_stock_move_line_search_read_and_move_id_reads(self) -> None:
        rpc = _FakeRpc()
        original_search_read = rpc.search_read
        original_read = rpc.read

        async def _search_read(model, domain, fields, context=None, stage="", limit=0, order=""):
            if model in {"stock.move.line", "stock.valuation.layer"} and fields != ["id"]:
                raise RuntimeError(f"{model}.search_read should only be used for id lookup")
            return await original_search_read(
                model=model,
                domain=domain,
                fields=fields,
                context=context,
                stage=stage,
                limit=limit,
                order=order,
            )

        async def _read(model, ids, fields, context=None, stage=""):
            if model == "stock.move.line" and "move_id" in fields:
                raise RuntimeError("stock.move.line.read should not request move_id")
            if model == "stock.valuation.layer" and "stock_move_id" in fields:
                raise RuntimeError("stock.valuation.layer.read should not request stock_move_id")
            return await original_read(model=model, ids=ids, fields=fields, context=context, stage=stage)

        rpc.search_read = _search_read  # type: ignore[method-assign]
        rpc.read = _read  # type: ignore[method-assign]
        service = PickingReaderServiceAsync(rpc=rpc, logger=self.logger)

        detail = asyncio.run(service.fetch_picking_by_name("WH/INT/00123"))

        self.assertEqual(len(detail.move_lines), 1)
        self.assertEqual(detail.line_items[0].move_id, 101)
        self.assertEqual(detail.line_items[0].line_qty, 2.0)

    def test_fetch_picking_by_name_uses_move_line_quantity_for_split_line_display(self) -> None:
        rpc = _FakeRpc()
        rpc.records["stock.move"][101]["product_qty"] = 100.0
        rpc.records["stock.move.line"][201]["qty_done"] = 0.0
        rpc.records["stock.move.line"][201]["quantity"] = 39.0
        rpc.records["stock.move.line"][201]["date"] = "2026-03-14 12:56:32"
        rpc.records["stock.move.line"][202] = {
            "id": 202,
            "move_id": 101,
            "product_id": (301, "Product A"),
            "qty_done": 0.0,
            "quantity": 61.0,
            "date": "2026-03-14 12:56:51",
            "location_id": (11, "WH/Stock"),
            "location_dest_id": (12, "WH/Transit"),
        }
        service = PickingReaderServiceAsync(rpc=rpc, logger=self.logger)

        detail = asyncio.run(service.fetch_picking_by_name("WH/INT/00123"))

        self.assertEqual(len(detail.line_items), 2)
        self.assertEqual([item.line_qty for item in detail.line_items], [39.0, 61.0])
        self.assertEqual([item.qty_done for item in detail.line_items], [0.0, 0.0])
        self.assertEqual([item.move_qty for item in detail.line_items], [100.0, 100.0])
        self.assertEqual(detail.moves[0]["_display_qty_done"], 100.0)

    def test_fetch_picking_by_name_resolves_journal_entries_via_picking_account_move_ids(self) -> None:
        rpc = _FakeRpc()
        self._disable_legacy_ref_link(rpc)
        self._enable_picking_account_move_ids(rpc)
        service = PickingReaderServiceAsync(rpc=rpc, logger=self.logger)

        detail = asyncio.run(service.fetch_picking_by_name("WH/INT/00123"))

        self.assertEqual(len(detail.journal_entries), 1)
        self.assertEqual(detail.journal_entries[0]["id"], 401)

    def test_fetch_picking_by_name_falls_back_when_svl_accounting_date_missing(self) -> None:
        rpc = _FakeRpc()
        rpc.meta["stock.valuation.layer"] = {
            "date": {"type": "date", "readonly": False},
            "quantity": {"type": "float", "readonly": False},
        }
        rpc.records["stock.valuation.layer"][301]["date"] = "2026-03-01"
        service = PickingReaderServiceAsync(rpc=rpc, logger=self.logger)

        detail = asyncio.run(service.fetch_picking_by_name("WH/INT/00123"))

        self.assertEqual(detail.svl_records[0]["_svl_date_field"], "date")

    def test_fetch_picking_by_name_marks_ambiguous_svl_mapping(self) -> None:
        rpc = _FakeRpc()
        rpc.records["stock.move.line"][202] = {
            "id": 202,
            "move_id": 101,
            "product_id": (301, "Product A"),
            "qty_done": 1.0,
            "date": "2026-03-01 10:00:00",
            "location_id": (11, "WH/Stock"),
            "location_dest_id": (12, "WH/Transit"),
        }
        rpc.records["stock.valuation.layer"][302] = {
            "id": 302,
            "stock_move_id": 101,
            "accounting_date": "2026-03-01",
            "create_date": "2026-03-01 10:00:00",
            "value": 50.0,
            "quantity": 1.0,
            "product_id": (301, "Product A"),
        }
        service = PickingReaderServiceAsync(rpc=rpc, logger=self.logger)

        detail = asyncio.run(service.fetch_picking_by_name("WH/INT/00123"))

        self.assertEqual(len(detail.line_items), 2)
        self.assertFalse(detail.line_items[0].svl_qty_editable)
        self.assertIn("valuation layer", detail.line_items[0].svl_warning)

    def test_journal_resolver_uses_picking_account_move_ids_first(self) -> None:
        rpc = _FakeRpc()
        self._disable_legacy_ref_link(rpc)
        self._enable_picking_account_move_ids(rpc)
        resolver = JournalResolverAsync(rpc=rpc, logger=self.logger)

        journal_ids, source = asyncio.run(
            resolver.resolve_account_move_ids_for_picking(
                picking_id=1,
                move_ids=[101],
                company_id=7,
                picking_name="WH/INT/00123",
            )
        )

        self.assertEqual(journal_ids, [401])
        self.assertEqual(source, "picking_account_move_ids")

    def test_journal_resolver_falls_back_to_move_account_move_ids(self) -> None:
        rpc = _FakeRpc()
        self._disable_legacy_ref_link(rpc)
        self._enable_move_account_move_ids(rpc)
        resolver = JournalResolverAsync(rpc=rpc, logger=self.logger)

        journal_ids, source = asyncio.run(
            resolver.resolve_account_move_ids_for_picking(
                picking_id=1,
                move_ids=[101],
                company_id=7,
                picking_name="WH/INT/00123",
            )
        )

        self.assertEqual(journal_ids, [401])
        self.assertEqual(source, "move_account_move_ids")

    def test_journal_resolver_falls_back_to_account_move_stock_move_id(self) -> None:
        rpc = _FakeRpc()
        self._disable_legacy_ref_link(rpc)
        self._enable_account_move_stock_move_id(rpc)
        resolver = JournalResolverAsync(rpc=rpc, logger=self.logger)

        journal_ids, source = asyncio.run(
            resolver.resolve_account_move_ids_for_picking(
                picking_id=1,
                move_ids=[101],
                company_id=7,
                picking_name="WH/INT/00123",
            )
        )

        self.assertEqual(journal_ids, [401])
        self.assertEqual(source, "account_move_stock_move_id")

    def test_journal_resolver_falls_back_to_svl_account_move_id(self) -> None:
        rpc = _FakeRpc()
        self._disable_legacy_ref_link(rpc)
        self._enable_svl_account_move_id(rpc)
        resolver = JournalResolverAsync(rpc=rpc, logger=self.logger)

        journal_ids, source = asyncio.run(
            resolver.resolve_account_move_ids_for_picking(
                picking_id=1,
                move_ids=[101],
                company_id=7,
                picking_name="WH/INT/00123",
            )
        )

        self.assertEqual(journal_ids, [401])
        self.assertEqual(source, "svl_account_move_id")

    def test_journal_resolver_uses_legacy_ref_when_relations_missing(self) -> None:
        rpc = _FakeRpc()
        resolver = JournalResolverAsync(rpc=rpc, logger=self.logger)

        journal_ids, source = asyncio.run(
            resolver.resolve_account_move_ids_for_picking(
                picking_id=1,
                move_ids=[101],
                company_id=7,
                picking_name="WH/INT/00123",
            )
        )

        self.assertEqual(journal_ids, [401])
        self.assertEqual(source, "ref")

    def test_account_move_date_sync_blocks_when_target_date_hits_lock_date(self) -> None:
        rpc = _FakeRpc()
        self._set_lock_date(rpc, "2026-02-22")
        service = AccountMoveDateSyncServiceAsync(rpc=rpc, settings=self.settings, logger=self.logger)

        ok, err, warn = asyncio.run(
            service.sync_account_move_dates(
                journal_ids=[401],
                company_id=7,
                target_date_utc="2026-01-20",
            )
        )

        self.assertFalse(ok)
        self.assertIn("accounting lock date company adalah 2026-02-22", err)
        self.assertEqual(warn, "")
        self.assertEqual(rpc.records["account.move"][401]["date"], "2026-03-01")
        self.assertFalse(rpc.execute_kw_calls)

    def test_account_move_date_sync_auto_drafts_and_reposts_posted_moves(self) -> None:
        rpc = _FakeRpc()
        service = AccountMoveDateSyncServiceAsync(rpc=rpc, settings=self.settings, logger=self.logger)

        ok, err, warn = asyncio.run(
            service.sync_account_move_dates(
                journal_ids=[401],
                company_id=7,
                target_date_utc="2026-03-13",
            )
        )

        self.assertTrue(ok)
        self.assertEqual(err, "")
        self.assertEqual(rpc.records["account.move"][401]["date"], "2026-03-13")
        self.assertEqual(rpc.records["account.move"][401]["state"], "posted")
        self.assertTrue(any(call["method"] in {"button_draft", "action_draft"} for call in rpc.execute_kw_calls))
        self.assertTrue(any(call["method"] == "action_post" for call in rpc.execute_kw_calls))
        self.assertIn("draft", warn.lower())

    def test_account_move_date_sync_repost_failure_marks_result_failed(self) -> None:
        rpc = _FakeRpc()
        rpc.execute_failures_by_method[("account.move", "action_post")] = RuntimeError("repost blocked")
        service = AccountMoveDateSyncServiceAsync(rpc=rpc, settings=self.settings, logger=self.logger)

        ok, err, _warn = asyncio.run(
            service.sync_account_move_dates(
                journal_ids=[401],
                company_id=7,
                target_date_utc="2026-03-13",
            )
        )

        self.assertFalse(ok)
        self.assertIn("restore stj ke posted", err.lower())

    def test_update_dates_writes_related_models_and_uses_force_date_context(self) -> None:
        rpc = _FakeRpc()
        self._disable_legacy_ref_link(rpc)
        self._enable_account_move_stock_move_id(rpc)
        service = TransactionDateEditorServiceAsync(rpc=rpc, settings=self.settings, logger=self.logger)
        request = TransactionEditRequest(
            picking_id=1,
            company_id=7,
            new_stock_date="2026-03-11 10:20:30",
            new_svl_date="2026-03-12",
            new_journal_date="2026-03-13",
        )

        result = asyncio.run(service.update_dates(request))

        self.assertTrue(result.success)
        self.assertEqual(rpc.records["stock.picking"][1]["date_done"], "2026-03-11 10:20:30")
        self.assertEqual(rpc.records["stock.move"][101]["date"], "2026-03-11 10:20:30")
        self.assertEqual(rpc.records["stock.move.line"][201]["date"], "2026-03-11 10:20:30")
        self.assertEqual(rpc.records["stock.valuation.layer"][301]["accounting_date"], "2026-03-12")
        self.assertEqual(rpc.records["account.move"][401]["date"], "2026-03-13")
        self.assertEqual(rpc.records["stock.move"][101]["state"], "done")
        self.assertEqual(rpc.records["stock.picking"][1]["state"], "done")
        svl_contexts = [context for model, context in rpc.write_contexts if model == "stock.valuation.layer"]
        self.assertTrue(any(context.get("force_date") == "2026-03-12" for context in svl_contexts))
        self.assertTrue(
            any(call["model"] == "stock.move" and call["stage"] == "EDIT_STATE_UNLOCK" for call in rpc.write_calls)
        )
        self.assertTrue(
            any(call["model"] == "stock.move" and call["stage"] == "EDIT_STATE_RESTORE" for call in rpc.write_calls)
        )
        self.assertTrue(any(call["method"] == "action_post" for call in rpc.execute_kw_calls))

    def test_update_dates_collects_partial_write_errors(self) -> None:
        rpc = _FakeRpc()
        rpc.write_failures["stock.move.line"] = RuntimeError("line blocked")
        service = TransactionDateEditorServiceAsync(rpc=rpc, settings=self.settings, logger=self.logger)
        request = TransactionEditRequest(
            picking_id=1,
            company_id=7,
            new_stock_date="2026-03-11 10:20:30",
        )

        result = asyncio.run(service.update_dates(request))

        self.assertFalse(result.success)
        self.assertTrue(any("stock.move.line date" in error for error in result.errors))
        self.assertEqual(rpc.records["stock.picking"][1]["date_done"], "2026-03-11 10:20:30")
        self.assertEqual(rpc.records["stock.move"][101]["state"], "done")
        self.assertEqual(rpc.records["stock.picking"][1]["state"], "done")

    def test_update_dates_skips_unlock_schema_error_and_continues_compatible_writes(self) -> None:
        rpc = _FakeRpc()
        rpc.write_failures_by_stage[("stock.move", "EDIT_STATE_UNLOCK")] = RuntimeError(
            "psycopg2.errors.UndefinedColumn: column stock_picking.is_from_holycount does not exist"
        )
        service = TransactionDateEditorServiceAsync(rpc=rpc, settings=self.settings, logger=self.logger)
        request = TransactionEditRequest(
            picking_id=1,
            company_id=7,
            new_stock_date="2026-03-11 10:20:30",
        )

        result = asyncio.run(service.update_dates(request))

        self.assertTrue(result.success)
        self.assertEqual(rpc.records["stock.picking"][1]["date_done"], "2026-03-11 10:20:30")
        self.assertEqual(rpc.records["stock.picking"][1]["scheduled_date"], "2026-03-01 10:00:00")
        self.assertEqual(rpc.records["stock.move"][101]["date"], "2026-03-11 10:20:30")
        self.assertEqual(rpc.records["stock.move.line"][201]["date"], "2026-03-11 10:20:30")
        self.assertTrue(any("unlock state picking done dilewati" in message.lower() for message in result.messages))
        self.assertTrue(any("scheduled_date dilewati" in message.lower() for message in result.messages))
        self.assertFalse(any(call["stage"] == "EDIT_STATE_RESTORE" for call in rpc.write_calls))

    def test_update_dates_fails_before_writes_when_journal_date_selected_but_no_journal_found(self) -> None:
        rpc = _FakeRpc()
        self._disable_legacy_ref_link(rpc)
        service = TransactionDateEditorServiceAsync(rpc=rpc, settings=self.settings, logger=self.logger)
        request = TransactionEditRequest(
            picking_id=1,
            company_id=7,
            new_stock_date="2026-03-11 10:20:30",
            new_journal_date="2026-03-13",
        )

        result = asyncio.run(service.update_dates(request))

        self.assertFalse(result.success)
        self.assertTrue(any("Journal Date dipilih" in error for error in result.errors))
        self.assertEqual(rpc.records["stock.picking"][1]["date_done"], "2026-03-01 10:00:00")
        self.assertEqual(rpc.records["stock.move"][101]["date"], "2026-03-01 10:00:00")
        self.assertEqual(rpc.records["stock.valuation.layer"][301]["accounting_date"], "2026-03-01")
        self.assertEqual(rpc.records["account.move"][401]["date"], "2026-03-01")
        self.assertFalse(rpc.write_calls)

    def test_update_dates_fails_before_writes_when_journal_date_hits_lock_date(self) -> None:
        rpc = _FakeRpc()
        self._disable_legacy_ref_link(rpc)
        self._enable_account_move_stock_move_id(rpc)
        self._set_lock_date(rpc, "2026-02-22")
        service = TransactionDateEditorServiceAsync(rpc=rpc, settings=self.settings, logger=self.logger)
        request = TransactionEditRequest(
            picking_id=1,
            company_id=7,
            new_stock_date="2026-01-20 00:31:06",
            new_journal_date="2026-01-20",
        )

        result = asyncio.run(service.update_dates(request))

        self.assertFalse(result.success)
        self.assertTrue(any("accounting lock date company adalah 2026-02-22" in error for error in result.errors))
        self.assertEqual(rpc.records["stock.picking"][1]["date_done"], "2026-03-01 10:00:00")
        self.assertEqual(rpc.records["stock.move"][101]["date"], "2026-03-01 10:00:00")
        self.assertEqual(rpc.records["account.move"][401]["date"], "2026-03-01")
        self.assertFalse(rpc.write_calls)

    def test_update_dates_marks_failure_when_journal_repost_fails(self) -> None:
        rpc = _FakeRpc()
        self._disable_legacy_ref_link(rpc)
        self._enable_account_move_stock_move_id(rpc)
        rpc.execute_failures_by_method[("account.move", "action_post")] = RuntimeError("repost blocked")
        service = TransactionDateEditorServiceAsync(rpc=rpc, settings=self.settings, logger=self.logger)
        request = TransactionEditRequest(
            picking_id=1,
            company_id=7,
            new_journal_date="2026-03-13",
        )

        result = asyncio.run(service.update_dates(request))

        self.assertFalse(result.success)
        self.assertTrue(any("restore stj ke posted" in error.lower() for error in result.errors))

    def test_update_dates_processes_split_stj_journals_as_one_set(self) -> None:
        rpc = _FakeRpc()
        self._disable_legacy_ref_link(rpc)
        self._enable_account_move_stock_move_id(rpc)
        rpc.records["stock.move"][102] = {
            "id": 102,
            "picking_id": 1,
            "product_id": (302, "Product B"),
            "product_qty": 1.0,
            "product_uom": (401, "Units"),
            "date": "2026-03-01 10:00:00",
            "state": "done",
        }
        rpc.records["account.move"][402] = {
            "id": 402,
            "name": "STJ/2026/0002",
            "ref": "UNRELATED/REF",
            "date": "2026-03-01",
            "state": "posted",
            "journal_id": (501, "Stock Journal"),
            "stock_move_id": 102,
        }
        service = TransactionDateEditorServiceAsync(rpc=rpc, settings=self.settings, logger=self.logger)
        request = TransactionEditRequest(
            picking_id=1,
            company_id=7,
            new_journal_date="2026-03-13",
        )

        result = asyncio.run(service.update_dates(request))

        self.assertTrue(result.success)
        self.assertEqual(rpc.records["account.move"][401]["date"], "2026-03-13")
        self.assertEqual(rpc.records["account.move"][402]["date"], "2026-03-13")
        draft_calls = [call for call in rpc.execute_kw_calls if call["method"] in {"button_draft", "action_draft"}]
        self.assertTrue(draft_calls)
        self.assertEqual(draft_calls[0]["args"][0], [401, 402])

    def test_update_qty_writes_move_line_move_and_svl(self) -> None:
        rpc = _FakeRpc()
        rpc.records["stock.move"][102] = {
            "id": 102,
            "picking_id": 1,
            "product_id": (302, "Product B"),
            "product_qty": 1.0,
            "product_uom": (401, "Units"),
            "date": "2026-03-01 10:00:00",
            "state": "done",
        }
        service = TransactionDateEditorServiceAsync(rpc=rpc, settings=self.settings, logger=self.logger)
        request = TransactionEditRequest(
            picking_id=1,
            company_id=7,
            target_move_line_id=201,
            target_move_id=101,
            target_svl_id=301,
            new_qty=5.5,
        )

        result = asyncio.run(service.update_qty(request))

        self.assertTrue(result.success)
        self.assertEqual(rpc.records["stock.move.line"][201]["qty_done"], 5.5)
        self.assertEqual(rpc.records["stock.move"][101]["product_qty"], 5.5)
        self.assertEqual(rpc.records["stock.valuation.layer"][301]["quantity"], 5.5)
        self.assertEqual(rpc.records["stock.move"][101]["state"], "done")
        self.assertEqual(rpc.records["stock.move"][102]["state"], "done")
        unlock_calls = [call for call in rpc.write_calls if call["model"] == "stock.move" and call["stage"] == "EDIT_STATE_UNLOCK"]
        self.assertTrue(unlock_calls)
        self.assertEqual(unlock_calls[0]["ids"], [101, 102])
        self.assertTrue(any(call["model"] == "stock.move" and call["stage"] == "FETCH_MOVE_IDS" for call in rpc.search_calls))

    def test_update_qty_skips_unlock_schema_error_and_still_writes_qty(self) -> None:
        rpc = _FakeRpc()
        rpc.write_failures_by_stage[("stock.move", "EDIT_STATE_UNLOCK")] = RuntimeError(
            "psycopg2.errors.UndefinedColumn: column stock_picking.is_from_holycount does not exist"
        )
        service = TransactionDateEditorServiceAsync(rpc=rpc, settings=self.settings, logger=self.logger)
        request = TransactionEditRequest(
            picking_id=1,
            company_id=7,
            target_move_line_id=201,
            target_move_id=101,
            target_svl_id=301,
            new_qty=5.5,
        )

        result = asyncio.run(service.update_qty(request))

        self.assertTrue(result.success)
        self.assertEqual(rpc.records["stock.move.line"][201]["qty_done"], 5.5)
        self.assertEqual(rpc.records["stock.move"][101]["product_qty"], 5.5)
        self.assertEqual(rpc.records["stock.valuation.layer"][301]["quantity"], 5.5)
        self.assertTrue(any("unlock state picking done dilewati" in message.lower() for message in result.messages))
        self.assertFalse(any(call["stage"] == "EDIT_STATE_RESTORE" for call in rpc.write_calls))

    def test_update_qty_blocks_when_svl_mapping_missing(self) -> None:
        rpc = _FakeRpc()
        service = TransactionDateEditorServiceAsync(rpc=rpc, settings=self.settings, logger=self.logger)
        request = TransactionEditRequest(
            picking_id=1,
            company_id=7,
            target_move_line_id=201,
            target_move_id=101,
            target_svl_id=0,
            new_qty=5.5,
        )

        result = asyncio.run(service.update_qty(request))

        self.assertFalse(result.success)
        self.assertTrue(any("mapping valuation layer" in error for error in result.errors))
        self.assertEqual(rpc.records["stock.move.line"][201]["qty_done"], 2.0)

    def test_update_qty_collects_partial_write_errors(self) -> None:
        rpc = _FakeRpc()
        rpc.write_failures["stock.valuation.layer"] = RuntimeError("svl blocked")
        service = TransactionDateEditorServiceAsync(rpc=rpc, settings=self.settings, logger=self.logger)
        request = TransactionEditRequest(
            picking_id=1,
            company_id=7,
            target_move_line_id=201,
            target_move_id=101,
            target_svl_id=301,
            new_qty=5.5,
        )

        result = asyncio.run(service.update_qty(request))

        self.assertFalse(result.success)
        self.assertTrue(any("stock.valuation.layer qty" in error for error in result.errors))
        self.assertEqual(rpc.records["stock.move.line"][201]["qty_done"], 5.5)
        self.assertEqual(rpc.records["stock.move"][101]["product_qty"], 5.5)
        self.assertEqual(rpc.records["stock.move"][101]["state"], "done")

    def test_update_dates_cancelled_picking_is_rejected_without_writes(self) -> None:
        rpc = _FakeRpc()
        rpc.records["stock.picking"][1]["state"] = "cancel"
        rpc.records["stock.move"][101]["state"] = "cancel"
        service = TransactionDateEditorServiceAsync(rpc=rpc, settings=self.settings, logger=self.logger)
        request = TransactionEditRequest(
            picking_id=1,
            company_id=7,
            new_stock_date="2026-03-11 10:20:30",
        )

        result = asyncio.run(service.update_dates(request))

        self.assertFalse(result.success)
        self.assertTrue(any("dibatalkan" in error for error in result.errors))
        self.assertFalse(any(call["stage"] == "EDIT_STATE_UNLOCK" for call in rpc.write_calls))

    def test_update_dates_restore_failure_marks_result_failed(self) -> None:
        rpc = _FakeRpc()
        rpc.write_failures_by_stage[("stock.move", "EDIT_STATE_RESTORE")] = RuntimeError("restore blocked")
        service = TransactionDateEditorServiceAsync(rpc=rpc, settings=self.settings, logger=self.logger)
        request = TransactionEditRequest(
            picking_id=1,
            company_id=7,
            new_stock_date="2026-03-11 10:20:30",
        )

        result = asyncio.run(service.update_dates(request))

        self.assertFalse(result.success)
        self.assertTrue(any("restore state picking" in error.lower() for error in result.errors))

    def test_update_qty_restore_runs_after_inner_write_error(self) -> None:
        rpc = _FakeRpc()
        rpc.write_failures["stock.valuation.layer"] = RuntimeError("svl blocked")
        service = TransactionDateEditorServiceAsync(rpc=rpc, settings=self.settings, logger=self.logger)
        request = TransactionEditRequest(
            picking_id=1,
            company_id=7,
            target_move_line_id=201,
            target_move_id=101,
            target_svl_id=301,
            new_qty=5.5,
        )

        result = asyncio.run(service.update_qty(request))

        self.assertFalse(result.success)
        self.assertEqual(rpc.records["stock.move"][101]["state"], "done")
        self.assertEqual(rpc.records["stock.picking"][1]["state"], "done")
        self.assertTrue(
            any(call["model"] == "stock.move" and call["stage"] == "EDIT_STATE_RESTORE" for call in rpc.write_calls)
        )

    def test_build_runtime_connection_applies_database_profile_resolution(self) -> None:
        global_settings = GlobalSettings(
            database_profiles=[],
            default_database_profile_id="",
        )

        with mock.patch(
            "smartscc_tools.modules.edit_transaksi_module.build_runtime_settings",
            return_value=(self.settings, object()),
        ), mock.patch(
            "smartscc_tools.modules.edit_transaksi_module.fetch_odoo_config",
            return_value=SimpleNamespace(base_url="https://demo.local", database="base-db"),
        ):
            settings, config = build_runtime_connection(
                global_settings,
                self.logger,
                database_profile_id="override-db",
            )

        self.assertIs(settings, self.settings)
        self.assertEqual(config.database, "override-db")


if __name__ == "__main__":
    unittest.main()
