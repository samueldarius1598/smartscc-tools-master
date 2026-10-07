import asyncio
import logging
import unittest
from typing import Any, Dict, List

from smartscc_tools.features.item_journal.config import RuntimeSettings
from smartscc_tools.features.item_journal.services.date_ops import DateFieldCapability, DateSyncCapabilities, DateSyncServiceAsync


class _ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: List[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


class _RpcDateSyncStub:
    def __init__(self) -> None:
        self.meta: Dict[str, Dict[str, Dict[str, Any]]] = {
            "stock.picking": {
                "date_done": {"type": "datetime", "readonly": False},
                "scheduled_date": {"type": "datetime", "readonly": False},
                "name": {"type": "char", "readonly": False},
                "state": {"type": "selection", "readonly": False},
            },
            "stock.move": {
                "date": {"type": "datetime", "readonly": False},
                "product_uom_qty": {"type": "float", "readonly": False},
            },
            "stock.move.line": {
                "date": {"type": "datetime", "readonly": False},
                "quantity": {"type": "float", "readonly": False},
                "product_uom_qty": {"type": "float", "readonly": False},
            },
            "account.move": {
                "date": {"type": "date", "readonly": False},
            },
        }
        self.records: Dict[str, Dict[int, Dict[str, Any]]] = {
            "account.change.lock.date": {
                601: {
                    "id": 601,
                    "fiscalyear_lock_date": "",
                }
            },
            "res.company": {
                796: {
                    "id": 796,
                    "fiscalyear_lock_date": "",
                }
            },
            "stock.picking": {
                1: {
                    "id": 1,
                    "name": "INT/0001",
                    "state": "assigned",
                    "date_done": "2026-03-01 10:00:00",
                    "scheduled_date": "2026-03-01 10:00:00",
                }
            },
            "stock.move": {
                11: {
                    "id": 11,
                    "picking_id": 1,
                    "product_uom_qty": 5.0,
                    "date": "2026-03-01 10:00:00",
                }
            },
            "stock.move.line": {
                21: {
                    "id": 21,
                    "picking_id": 1,
                    "move_id": [11, "MOVE/11"],
                    "quantity": 0.0,
                    "product_uom_qty": 5.0,
                    "date": "2026-03-01 10:00:00",
                }
            },
            "account.move": {
                401: {
                    "id": 401,
                    "name": "STJ/2026/0001",
                    "ref": "INT/0001",
                    "date": "2026-03-01",
                    "state": "posted",
                }
            },
        }
        self.button_validate_result: Any = True
        self.create_calls: List[Dict[str, Any]] = []
        self.execute_calls: List[Dict[str, Any]] = []
        self.search_calls: List[Dict[str, Any]] = []
        self.write_calls: List[Dict[str, Any]] = []
        self._wizard_pick_by_id: Dict[int, int] = {}
        self._next_wizard_id = 900

    async def fields_get(
        self,
        model: str,
        attributes=None,  # noqa: ANN001
        context=None,  # noqa: ANN001
        stage: str = "",
    ) -> Dict[str, Any]:
        _ = (attributes, context, stage)
        return dict(self.meta.get(model, {}))

    async def execute_kw(
        self,
        model: str,
        method: str,
        args: List[Any] | None = None,
        kwargs: Dict[str, Any] | None = None,
        stage: str = "",
        excel_row: int = 0,
        mutating: bool = False,
    ) -> Any:
        _ = (excel_row, mutating)
        args = args or []
        kwargs = kwargs or {}
        self.execute_calls.append({"model": model, "method": method, "args": list(args), "kwargs": dict(kwargs), "stage": stage})
        if model == "stock.picking" and method == "action_assign":
            return True
        if model == "stock.picking" and method == "button_validate":
            if not isinstance(self.button_validate_result, dict):
                self.records["stock.picking"][1]["state"] = "done"
            return self.button_validate_result
        if model in {"stock.immediate.transfer", "stock.backorder.confirmation"} and method in {
            "process",
            "process_cancel_backorder",
        }:
            wizard_ids = args[0] if args else []
            wiz_id = int(wizard_ids[0]) if wizard_ids else 0
            pick_id = int(self._wizard_pick_by_id.get(wiz_id, 0))
            if pick_id > 0:
                self.records["stock.picking"][pick_id]["state"] = "done"
            return True
        if model == "account.move" and method in {"button_draft", "action_draft"}:
            for record_id in self._coerce_ids(args[0] if args else []):
                if record_id in self.records["account.move"]:
                    self.records["account.move"][record_id]["state"] = "draft"
            return True
        if model == "account.move" and method == "action_post":
            for record_id in self._coerce_ids(args[0] if args else []):
                if record_id in self.records["account.move"]:
                    self.records["account.move"][record_id]["state"] = "posted"
            return True
        return True

    async def create(
        self,
        model: str,
        values: Dict[str, Any],
        context: Dict[str, Any] | None = None,
        stage: str = "",
    ) -> int:
        _ = (context, stage)
        self.create_calls.append({"model": model, "values": dict(values)})
        if model not in {"stock.immediate.transfer", "stock.backorder.confirmation"}:
            return 0
        pick_ids_cmd = values.get("pick_ids", [])
        pick_id = 0
        if isinstance(pick_ids_cmd, list) and pick_ids_cmd:
            cmd = pick_ids_cmd[0]
            if isinstance(cmd, list) and len(cmd) >= 3 and isinstance(cmd[2], list) and cmd[2]:
                pick_id = int(cmd[2][0] or 0)
        self._next_wizard_id += 1
        self._wizard_pick_by_id[self._next_wizard_id] = pick_id
        return self._next_wizard_id

    async def search(
        self,
        model: str,
        domain: List[Any],
        context=None,  # noqa: ANN001
        stage: str = "",
    ) -> List[int]:
        self.search_calls.append({"model": model, "domain": domain, "context": dict(context or {}), "stage": stage})
        rows = await self.search_read(model=model, domain=domain, fields=["id"], context=context, stage=stage)
        return [int(row["id"]) for row in rows]

    async def search_read(
        self,
        model: str,
        domain: List[Any],
        fields=None,  # noqa: ANN001
        limit=None,  # noqa: ANN001
        order=None,  # noqa: ANN001
        context=None,  # noqa: ANN001
        stage: str = "",
        excel_row: int = 0,
    ) -> List[Dict[str, Any]]:
        _ = (order, context, stage, excel_row)
        rows = [record for record in self.records.get(model, {}).values() if self._matches(record, domain)]
        if limit:
            rows = rows[: int(limit)]
        return [self._project(record, fields or []) for record in rows]

    async def read(
        self,
        model: str,
        ids: List[int],
        fields=None,  # noqa: ANN001
        context=None,  # noqa: ANN001
        stage: str = "",
    ) -> List[Dict[str, Any]]:
        _ = (context, stage)
        return [
            self._project(self.records[model][record_id], fields or [])
            for record_id in ids
            if record_id in self.records.get(model, {})
        ]

    async def write(
        self,
        model: str,
        ids: List[int],
        values: Dict[str, Any],
        context: Dict[str, Any] | None = None,
        stage: str = "",
    ) -> bool:
        self.write_calls.append({"model": model, "ids": list(ids), "values": dict(values), "context": dict(context or {}), "stage": stage})
        if model == "account.move" and "date" in values:
            for record_id in ids:
                state = str(self.records[model][record_id].get("state") or "").strip().lower()
                if state == "posted":
                    raise RuntimeError("You cannot modify the following readonly fields on a posted move: date")
        for record_id in ids:
            if record_id in self.records.get(model, {}):
                self.records[model][record_id].update(values)
        return True

    @staticmethod
    def _coerce_ids(raw_ids: Any) -> List[int]:
        if not isinstance(raw_ids, list):
            return []
        return [int(item or 0) for item in raw_ids if int(item or 0) > 0]

    @staticmethod
    def _extract_relation_id(value: Any) -> Any:
        if isinstance(value, (list, tuple)) and value:
            return value[0]
        return value

    def _matches(self, record: Dict[str, Any], domain: List[Any]) -> bool:
        for field_name, operator, expected in domain:
            actual = self._extract_relation_id(record.get(field_name))
            if operator == "=" and actual != expected:
                return False
            if operator == "in" and actual not in expected:
                return False
        return True

    @staticmethod
    def _project(record: Dict[str, Any], fields: List[str]) -> Dict[str, Any]:
        return {field_name: record.get(field_name) for field_name in fields}


class DateSyncServiceAsyncTest(unittest.TestCase):
    @staticmethod
    def _basic_caps() -> DateSyncCapabilities:
        return DateSyncCapabilities(
            stock_picking_done=DateFieldCapability("date_done", False),
            stock_picking_scheduled=DateFieldCapability("scheduled_date", False),
            stock_move=DateFieldCapability("date", False),
            stock_move_line=DateFieldCapability("date", False),
            stock_valuation_layer=None,
            account_move_due_primary=None,
            account_move_due_fallback=None,
        )

    @staticmethod
    def _set_lock_date(rpc: _RpcDateSyncStub, lock_date_text: str) -> None:
        rpc.records["account.change.lock.date"][601]["fiscalyear_lock_date"] = lock_date_text
        rpc.records["res.company"][796]["fiscalyear_lock_date"] = lock_date_text

    @staticmethod
    def _enable_account_move_stock_move_id(rpc: _RpcDateSyncStub) -> None:
        rpc.meta["account.move"]["stock_move_id"] = {
            "type": "many2one",
            "relation": "stock.move",
        }
        rpc.records["account.move"][401]["stock_move_id"] = 11
        rpc.records["account.move"][401]["ref"] = "UNRELATED/REF"

    def _build_service(
        self,
        rpc: _RpcDateSyncStub,
        settings: RuntimeSettings | None = None,
        capabilities: DateSyncCapabilities | None = None,
    ) -> DateSyncServiceAsync:
        runtime = settings or RuntimeSettings.from_preset("safe-fast")
        service = DateSyncServiceAsync(rpc=rpc, settings=runtime, logger=logging.getLogger("test-date-sync"))  # type: ignore[arg-type]

        async def _init_caps():
            return capabilities

        service.initialize_capabilities = _init_caps  # type: ignore[method-assign]
        return service

    def test_safe_validate_handles_immediate_transfer_wizard(self) -> None:
        rpc = _RpcDateSyncStub()
        rpc.button_validate_result = {"res_model": "stock.immediate.transfer"}
        settings = RuntimeSettings.from_preset("safe-fast")
        settings.validate_enforce_done_qty = False
        service = self._build_service(rpc, settings)

        ok, err, warn = asyncio.run(service.force_date_and_validate(pick_id=1, company_id=796, date_done_utc=""))

        self.assertTrue(ok)
        self.assertEqual(err, "")
        self.assertEqual(warn, "")
        self.assertEqual(rpc.records["stock.picking"][1]["state"], "done")
        self.assertTrue(any(call["model"] == "stock.immediate.transfer" for call in rpc.create_calls))
        self.assertTrue(
            any(
                call["model"] == "stock.immediate.transfer" and call["method"] == "process"
                for call in rpc.execute_calls
            )
        )

    def test_safe_validate_rejects_backorder_when_policy_fail(self) -> None:
        rpc = _RpcDateSyncStub()
        rpc.button_validate_result = {"res_model": "stock.backorder.confirmation"}
        settings = RuntimeSettings.from_preset("safe-fast")
        settings.validate_backorder_policy = "fail"
        settings.validate_enforce_done_qty = False
        service = self._build_service(rpc, settings)

        ok, err, _warn = asyncio.run(service.force_date_and_validate(pick_id=1, company_id=796, date_done_utc=""))

        self.assertFalse(ok)
        self.assertIn("Backorder terdeteksi", err)
        self.assertFalse(any(call["model"] == "stock.backorder.confirmation" for call in rpc.create_calls))

    def test_enforce_done_qty_syncs_quantity_before_validate(self) -> None:
        rpc = _RpcDateSyncStub()
        rpc.button_validate_result = True
        settings = RuntimeSettings.from_preset("safe-fast")
        settings.validate_enforce_done_qty = True
        service = self._build_service(rpc, settings)

        ok, err, _warn = asyncio.run(service.force_date_and_validate(pick_id=1, company_id=796, date_done_utc=""))

        self.assertTrue(ok)
        self.assertEqual(err, "")
        line_write_calls = [call for call in rpc.write_calls if call["model"] == "stock.move.line"]
        self.assertGreaterEqual(len(line_write_calls), 1)
        self.assertAlmostEqual(float(line_write_calls[0]["values"].get("quantity", 0.0)), 5.0)

    def test_preflight_readonly_field_logs_humanized_info_only(self) -> None:
        rpc = _RpcDateSyncStub()

        async def _fields_get(model: str, attributes=None, context=None, stage: str = "") -> Dict[str, Any]:  # noqa: ANN001
            _ = (attributes, context, stage)
            if model == "stock.picking":
                return {"date_done": {"type": "datetime", "readonly": True}}
            return {}

        rpc.fields_get = _fields_get  # type: ignore[method-assign]

        logger = logging.getLogger("test-date-sync-humanized")
        logger.setLevel(logging.INFO)
        logger.handlers = []
        logger.propagate = False
        handler = _ListHandler()
        logger.addHandler(handler)

        settings = RuntimeSettings.from_preset("safe-fast")
        service = DateSyncServiceAsync(rpc=rpc, settings=settings, logger=logger)  # type: ignore[arg-type]

        capability = asyncio.run(
            service._resolve_date_field(
                model="stock.picking",
                candidates=["date_done"],
                require_writable=True,
                allow_missing=False,
            )
        )

        self.assertIsNotNone(capability)
        messages = [record.getMessage() for record in handler.records]
        self.assertTrue(any("mode kompatibel odoo" in message.lower() for message in messages))
        self.assertFalse(any("readonly via fields_get" in message.lower() for message in messages))
        self.assertFalse(any(record.levelno >= logging.WARNING for record in handler.records))

    def test_force_date_and_validate_blocks_before_stock_writes_when_journal_hits_lock_date(self) -> None:
        rpc = _RpcDateSyncStub()
        self._enable_account_move_stock_move_id(rpc)
        self._set_lock_date(rpc, "2026-02-22")
        settings = RuntimeSettings.from_preset("safe-fast")
        settings.validate_enforce_done_qty = False
        service = self._build_service(rpc, settings, self._basic_caps())

        ok, err, warn = asyncio.run(
            service.force_date_and_validate(
                pick_id=1,
                company_id=796,
                date_done_utc="2026-01-20 00:31:06",
            )
        )

        self.assertFalse(ok)
        self.assertIn("accounting lock date company adalah 2026-02-22", err)
        self.assertEqual(warn, "")
        self.assertEqual(rpc.records["stock.picking"][1]["date_done"], "2026-03-01 10:00:00")
        self.assertEqual(rpc.records["stock.move"][11]["date"], "2026-03-01 10:00:00")
        self.assertEqual(rpc.records["stock.move.line"][21]["date"], "2026-03-01 10:00:00")
        self.assertEqual(rpc.records["account.move"][401]["date"], "2026-03-01")
        self.assertFalse(rpc.write_calls)

    def test_force_date_and_validate_uses_relation_resolver_not_legacy_ref(self) -> None:
        rpc = _RpcDateSyncStub()
        self._enable_account_move_stock_move_id(rpc)
        settings = RuntimeSettings.from_preset("safe-fast")
        settings.validate_enforce_done_qty = False
        service = self._build_service(rpc, settings, self._basic_caps())

        ok, err, warn = asyncio.run(
            service.force_date_and_validate(
                pick_id=1,
                company_id=796,
                date_done_utc="2026-03-13 10:20:30",
            )
        )

        self.assertTrue(ok)
        self.assertEqual(err, "")
        self.assertEqual(rpc.records["account.move"][401]["date"], "2026-03-13")
        self.assertFalse(
            any(
                call["model"] == "account.move" and call["domain"] == [["ref", "=", "INT/0001"]]
                for call in rpc.search_calls
            )
        )
        self.assertEqual(rpc.records["stock.picking"][1]["date_done"], "2026-03-13 10:20:30")
        self.assertEqual(rpc.records["stock.move"][11]["date"], "2026-03-13 10:20:30")
        self.assertEqual(rpc.records["stock.move.line"][21]["date"], "2026-03-13 10:20:30")
        stock_picking_contexts = [call["context"] for call in rpc.write_calls if call["model"] == "stock.picking"]
        self.assertTrue(any("force_date" in context for context in stock_picking_contexts))
        self.assertIn("draft", warn.lower())

    def test_force_date_and_validate_updates_split_stj_and_reposts_posted_moves(self) -> None:
        rpc = _RpcDateSyncStub()
        self._enable_account_move_stock_move_id(rpc)
        rpc.records["stock.move"][12] = {
            "id": 12,
            "picking_id": 1,
            "product_uom_qty": 1.0,
            "date": "2026-03-01 10:00:00",
        }
        rpc.records["account.move"][402] = {
            "id": 402,
            "name": "STJ/2026/0002",
            "ref": "UNRELATED/REF",
            "date": "2026-03-01",
            "state": "posted",
            "stock_move_id": 12,
        }
        settings = RuntimeSettings.from_preset("safe-fast")
        settings.validate_enforce_done_qty = False
        service = self._build_service(rpc, settings, self._basic_caps())

        ok, err, _warn = asyncio.run(
            service.force_date_and_validate(
                pick_id=1,
                company_id=796,
                date_done_utc="2026-03-13 10:20:30",
            )
        )

        self.assertTrue(ok)
        self.assertEqual(err, "")
        self.assertEqual(rpc.records["account.move"][401]["date"], "2026-03-13")
        self.assertEqual(rpc.records["account.move"][402]["date"], "2026-03-13")
        self.assertEqual(rpc.records["account.move"][401]["state"], "posted")
        self.assertEqual(rpc.records["account.move"][402]["state"], "posted")
        draft_calls = [call for call in rpc.execute_calls if call["model"] == "account.move" and call["method"] in {"button_draft", "action_draft"}]
        self.assertTrue(draft_calls)
        self.assertEqual(draft_calls[0]["args"][0], [401, 402])
        self.assertTrue(
            any(call["model"] == "account.move" and call["method"] == "action_post" for call in rpc.execute_calls)
        )


if __name__ == "__main__":
    unittest.main()
