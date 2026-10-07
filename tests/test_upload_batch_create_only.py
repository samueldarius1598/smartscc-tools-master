import asyncio
import logging
import unittest
from dataclasses import dataclass
from datetime import date
from types import SimpleNamespace
from typing import Any, Dict, List

from smartscc_tools.features.item_journal.config import RuntimeSettings
from smartscc_tools.features.item_journal.runtime import RunControl
from smartscc_tools.features.item_journal.services.date_ops import LockDateFetchResult
from smartscc_tools.features.item_journal.services.upload import BatchEntry, ItemJournalServiceAsync
from smartscc_tools.features.item_journal.workbook import ItemJournalRow


@dataclass
class _RepoStub:
    rows: List[ItemJournalRow]
    status: str = ""

    def read_rows(self) -> List[ItemJournalRow]:
        return self.rows

    def write_status(self, text: str) -> None:
        self.status = text

    def write_rows(self, rows: List[ItemJournalRow]) -> None:
        self.rows = rows

    def mark_rows_stopped(self, rows: List[ItemJournalRow], message: str) -> None:
        for row in rows:
            if row.result.strip():
                continue
            row.result = "[Stopped]"
            row.stj = ""
            row.append_error(message)

    def append_audit_events(self, _events) -> None:  # pragma: no cover - noop stub
        return


class _AuditSinkStub:
    def __init__(self) -> None:
        self.events: List[Any] = []
        self.buffer: List[Any] = []

    def emit(self, event: Any) -> None:
        self.events.append(event)
        self.buffer.append(event)

    def pop_buffered_events(self) -> List[Any]:
        out = self.buffer[:]
        self.buffer.clear()
        return out

    def should_flush(self) -> bool:
        return False

    def close(self) -> None:
        return


class _ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: List[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


class _RpcSyncStub:
    def __init__(self, company_id: int = 796) -> None:
        self.company_id = company_id
        self.next_pick_id = 9000
        self.pick_names: Dict[int, str] = {}
        self.pick_states: Dict[int, str] = {}
        self.create_calls: List[Dict[str, Any]] = []
        self.write_calls: List[Dict[str, Any]] = []
        self.execute_kw_calls: List[Dict[str, Any]] = []
        self.locations = {"WH/Stock": 1001, "WH/Output": 1002}
        self.location_usage = {1001: "internal", 1002: "transit"}
        self.product_category_by_product: Dict[int, int] = {}
        self.category_valuation_by_id: Dict[int, str] = {500: "real_time"}
        self.call_context = SimpleNamespace(model="", method="", stage="", excel_row=0)

    def ensure_login(self) -> int:
        return 1

    def fields_get(self, *args, **kwargs):  # noqa: ANN002, ANN003
        model = str(kwargs.get("model", args[0] if len(args) > 0 else ""))
        if model == "product.product":
            return {
                "is_storable": {"type": "boolean"},
                "detailed_type": {"type": "selection"},
            }
        return {}

    def search(self, *args, **kwargs):  # noqa: ANN002, ANN003
        return []

    def execute_kw(self, *args, **kwargs):  # noqa: ANN002, ANN003
        model = str(kwargs.get("model", args[0] if len(args) > 0 else ""))
        method = str(kwargs.get("method", args[1] if len(args) > 1 else ""))
        method_args = kwargs.get("args", args[2] if len(args) > 2 else [])
        self.execute_kw_calls.append({"model": model, "method": method, "args": method_args})
        if model == "stock.picking" and method == "action_cancel":
            for pick_id in method_args[0] if method_args else []:
                self.pick_states[int(pick_id)] = "cancel"
            return True
        return None

    def write(self, model: str, ids: List[int], values: Dict[str, Any], **kwargs) -> bool:  # noqa: ANN003
        if model == "stock.picking" and "move_ids" in values:
            raise AssertionError("Append move_ids ke picking lama tidak boleh terjadi lagi.")
        self.write_calls.append({"model": model, "ids": list(ids), "values": dict(values)})
        return True

    def create(self, model: str, values: Dict[str, Any], **kwargs) -> int:  # noqa: ANN003
        if model != "stock.picking":
            return 0
        self.next_pick_id += 1
        pick_id = self.next_pick_id
        pick_name = f"INT/{pick_id}"
        self.pick_names[pick_id] = pick_name
        self.pick_states[pick_id] = "assigned"
        self.create_calls.append({"model": model, "values": dict(values), "pick_id": pick_id, "pick_name": pick_name})
        return pick_id

    def read(self, model: str, ids: List[int], fields=None, **kwargs):  # noqa: ANN001, ANN003
        if model == "stock.picking":
            return [
                {
                    "id": rid,
                    "name": self.pick_names.get(rid, f"INT/{rid}"),
                    "state": self.pick_states.get(rid, "assigned"),
                }
                for rid in ids
            ]
        if model == "stock.location":
            by_id = {v: k for k, v in self.locations.items()}
            return [
                {
                    "id": rid,
                    "display_name": by_id.get(rid, f"LOC/{rid}"),
                    "company_id": [self.company_id, "PT Test"],
                    "usage": self.location_usage.get(rid, ""),
                }
                for rid in ids
            ]
        if model == "product.product":
            rows = []
            for rid in ids:
                categ_id = int(self.product_category_by_product.get(int(rid), 500))
                rows.append(
                    {
                        "id": int(rid),
                        "categ_id": [categ_id, f"CAT/{categ_id}"],
                    }
                )
            return rows
        if model == "product.category":
            rows = []
            for rid in ids:
                rows.append(
                    {
                        "id": int(rid),
                        "property_valuation": self.category_valuation_by_id.get(int(rid), "real_time"),
                    }
                )
            return rows
        return []

    def search_read(
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
        _ = (fields, limit, order, context, stage, excel_row)
        if model == "stock.picking.type":
            return [{"id": 77}]
        if model == "stock.location":
            name = ""
            op = ""
            for cond in domain:
                if isinstance(cond, list) and len(cond) >= 3 and cond[0] in {"name", "complete_name"}:
                    name = str(cond[2])
                    op = str(cond[1])
                    break
            if op == "ilike":
                out = []
                needle = name.lower()
                for loc_name, loc_id in self.locations.items():
                    if needle in loc_name.lower():
                        out.append(
                            {
                                "id": loc_id,
                                "complete_name": loc_name,
                                "name": loc_name.split("/")[-1],
                                "company_id": [self.company_id, "PT Test"],
                            }
                        )
                return out
            loc_id = self.locations.get(name)
            return [{"id": loc_id}] if loc_id else []
        if model == "product.product":
            requested_ids: List[int] = []
            requested_codes: List[str] = []
            for cond in domain:
                if not isinstance(cond, list) or len(cond) < 3:
                    continue
                field_name, operator, value = cond[0], cond[1], cond[2]
                if field_name == "id" and operator == "in":
                    requested_ids.extend(int(x) for x in value)
                if field_name == "default_code" and operator == "in":
                    requested_codes.extend(str(x) for x in value)
                if field_name == "default_code" and operator in {"=", "=ilike"}:
                    requested_codes.append(str(value))
            rows = []
            for product_id in requested_ids:
                rows.append(
                    {
                        "id": product_id,
                        "default_code": f"SKU-{product_id}",
                        "display_name": f"Product {product_id}",
                        "name": f"Product {product_id}",
                        "uom_id": [1, "Units"],
                        "company_id": [self.company_id, "PT Test"],
                        "standard_price": 10.0,
                        "is_storable": True,
                        "detailed_type": "product",
                    }
                )
            for code in requested_codes:
                product_id = int(code.replace("SKU-", "")) if code.startswith("SKU-") and code[4:].isdigit() else 0
                if product_id <= 0:
                    continue
                rows.append(
                    {
                        "id": product_id,
                        "default_code": code,
                        "display_name": f"Product {product_id}",
                        "name": f"Product {product_id}",
                        "uom_id": [1, "Units"],
                        "company_id": [self.company_id, "PT Test"],
                        "standard_price": 10.0,
                        "is_storable": True,
                        "detailed_type": "product",
                    }
                )
            return rows
        if model == "uom.uom":
            return [{"id": 1, "name": "Units"}]
        return []


class _RpcAsyncStub(_RpcSyncStub):
    async def ensure_login(self) -> int:
        return super().ensure_login()

    async def fields_get(self, *args, **kwargs):  # noqa: ANN002, ANN003
        return super().fields_get(*args, **kwargs)

    async def search(self, *args, **kwargs):  # noqa: ANN002, ANN003
        return super().search(*args, **kwargs)

    async def execute_kw(self, *args, **kwargs):  # noqa: ANN002, ANN003
        return super().execute_kw(*args, **kwargs)

    async def write(self, model: str, ids: List[int], values: Dict[str, Any], **kwargs) -> bool:  # noqa: ANN003
        return super().write(model, ids, values, **kwargs)

    async def create(self, model: str, values: Dict[str, Any], **kwargs) -> int:  # noqa: ANN003
        return super().create(model, values, **kwargs)

    async def read(self, model: str, ids: List[int], fields=None, **kwargs):  # noqa: ANN001, ANN003
        return super().read(model, ids, fields=fields, **kwargs)

    async def search_read(  # noqa: ANN001
        self,
        model: str,
        domain: List[Any],
        fields=None,
        limit=None,
        order=None,
        context=None,
        stage: str = "",
        excel_row: int = 0,
    ) -> List[Dict[str, Any]]:
        return super().search_read(
            model=model,
            domain=domain,
            fields=fields,
            limit=limit,
            order=order,
            context=context,
            stage=stage,
            excel_row=excel_row,
        )


def _build_rows(total: int, product_ids: List[int] | None = None) -> List[ItemJournalRow]:
    rows: List[ItemJournalRow] = []
    for idx in range(total):
        row_number = idx + 2
        if product_ids is not None and idx < len(product_ids):
            product_id = int(product_ids[idx])
        else:
            product_id = 1000 + row_number
        rows.append(
            ItemJournalRow(
                row_number=row_number,
                date_done_raw="2026-03-01",
                company_id=796,
                company_name="PT Test",
                op_type="Internal Transfer",
                src_loc="WH/Stock",
                dest_loc="WH/Output",
                prod_id_text=str(product_id),
                prod_key="",
                qty=1.0,
                uom="Units",
                result="",
                stj="",
                error="",
            )
        )
    return rows


class UploadCreateOnlyTest(unittest.TestCase):
    def test_async_create_new_picking_per_batch_and_map_result_per_row(self) -> None:
        rows = _build_rows(4)
        repo = _RepoStub(rows=rows)
        rpc = _RpcAsyncStub()
        settings = RuntimeSettings.from_preset("safe-fast")
        settings.batch_limit = 2
        settings.auto_validate = True
        run_control = RunControl()

        service = ItemJournalServiceAsync(
            rpc=rpc,  # type: ignore[arg-type]
            settings=settings,
            logger=logging.getLogger("test-async"),
            run_control=run_control,
        )

        async def _lock_dates(ids: List[int]) -> LockDateFetchResult:
            _ = ids
            return LockDateFetchResult(lock_date_map={796: date(2026, 1, 31)}, status_map={796: "2026-01-31"})

        async def _init_caps():
            return None

        async def _force_date(pick_id: int, company_id: int, date_done_utc: str):
            _ = (pick_id, company_id, date_done_utc)
            return True, "", ""

        stj_calls: List[int] = []

        async def _fill_stj(pick_id: int, company_id: int, target_rows: List[ItemJournalRow], **kwargs) -> str:
            _ = company_id
            _ = kwargs
            stj_calls.append(pick_id)
            for row in target_rows:
                row.stj = f"STJ/{pick_id}"
            return ""

        service.lock_date_service.fetch_lock_dates = _lock_dates  # type: ignore[method-assign]
        service.date_sync_service.initialize_capabilities = _init_caps  # type: ignore[method-assign]
        service.date_sync_service.force_date_and_validate = _force_date  # type: ignore[method-assign]
        service.stj_service.fill_stj_for_group = _fill_stj  # type: ignore[method-assign]

        summary = asyncio.run(service.run_upload(repo=repo, dry_run=False))

        self.assertEqual(summary["batches"], 2)
        self.assertEqual(len(rpc.create_calls), 2)
        first_pick = rows[0].result
        second_pick = rows[2].result
        self.assertTrue(first_pick.startswith("INT/"))
        self.assertTrue(second_pick.startswith("INT/"))
        self.assertNotEqual(first_pick, second_pick)
        self.assertEqual(rows[0].result, rows[1].result)
        self.assertEqual(rows[2].result, rows[3].result)
        self.assertEqual(sorted(set(stj_calls)), sorted(int(item["pick_id"]) for item in rpc.create_calls))
        self.assertEqual(repo.status, "DONE Upload Internal Transfer")
        self.assertTrue(summary["transfer_refs"])
        self.assertTrue(summary["journal_refs"])
        self.assertEqual(len(summary["transfer_results"]), 4)
        self.assertTrue(all(item["journal_status"] == "ok" for item in summary["transfer_results"]))
        self.assertTrue(all("journal_expected" in item for item in summary["transfer_results"]))
        self.assertTrue(all("journal_reason" in item for item in summary["transfer_results"]))

    def test_async_no_split_when_signature_is_same_after_limit(self) -> None:
        rows = _build_rows(5, product_ids=[2001, 2001, 2001, 2001, 2001])
        repo = _RepoStub(rows=rows)
        rpc = _RpcAsyncStub()
        settings = RuntimeSettings.from_preset("safe-fast")
        settings.batch_limit = 3
        settings.auto_validate = True
        audit_sink = _AuditSinkStub()

        service = ItemJournalServiceAsync(
            rpc=rpc,  # type: ignore[arg-type]
            settings=settings,
            logger=logging.getLogger("test-async-signature-no-split"),
            run_control=RunControl(),
            audit_sink=audit_sink,  # type: ignore[arg-type]
        )

        async def _lock_dates(ids: List[int]) -> LockDateFetchResult:
            _ = ids
            return LockDateFetchResult(lock_date_map={796: date(2026, 1, 31)}, status_map={796: "2026-01-31"})

        async def _init_caps():
            return None

        async def _force_date(pick_id: int, company_id: int, date_done_utc: str):
            _ = (pick_id, company_id, date_done_utc)
            return True, "", ""

        async def _fill_stj(pick_id: int, company_id: int, target_rows: List[ItemJournalRow], **kwargs) -> str:
            _ = (pick_id, company_id, kwargs)
            for row in target_rows:
                row.stj = "STJ/NO-SPLIT"
            return ""

        service.lock_date_service.fetch_lock_dates = _lock_dates  # type: ignore[method-assign]
        service.date_sync_service.initialize_capabilities = _init_caps  # type: ignore[method-assign]
        service.date_sync_service.force_date_and_validate = _force_date  # type: ignore[method-assign]
        service.stj_service.fill_stj_for_group = _fill_stj  # type: ignore[method-assign]

        summary = asyncio.run(service.run_upload(repo=repo, dry_run=False))

        self.assertEqual(summary["rows_error"], 0)
        self.assertEqual(summary["batches"], 1)
        self.assertEqual(len(rpc.create_calls), 1)
        self.assertEqual(len(rpc.create_calls[0]["values"].get("move_ids", [])), 5)
        deferred_events = [event for event in audit_sink.events if getattr(event, "stage", "") == "BATCH_SIGNATURE_DEFERRED"]
        self.assertEqual(len(deferred_events), 1)

    def test_async_qty_non_positive_marks_error_without_create(self) -> None:
        rows = _build_rows(1)
        rows[0].qty = 0.0
        repo = _RepoStub(rows=rows)
        rpc = _RpcAsyncStub()
        settings = RuntimeSettings.from_preset("safe-fast")
        settings.auto_validate = True

        service = ItemJournalServiceAsync(
            rpc=rpc,  # type: ignore[arg-type]
            settings=settings,
            logger=logging.getLogger("test-async-qty-check"),
            run_control=RunControl(),
        )

        async def _lock_dates(ids: List[int]) -> LockDateFetchResult:
            _ = ids
            return LockDateFetchResult(lock_date_map={796: date(2026, 1, 31)}, status_map={796: "2026-01-31"})

        async def _init_caps():
            return None

        service.lock_date_service.fetch_lock_dates = _lock_dates  # type: ignore[method-assign]
        service.date_sync_service.initialize_capabilities = _init_caps  # type: ignore[method-assign]

        summary = asyncio.run(service.run_upload(repo=repo, dry_run=False))

        self.assertEqual(summary["rows_error"], 1)
        self.assertEqual(summary["rows_success"], 0)
        self.assertEqual(len(rpc.create_calls), 0)
        self.assertEqual(rows[0].result, "[Error]")
        self.assertIn("Qty harus > 0", rows[0].error)

    def test_async_missing_operation_type_marks_error_without_create(self) -> None:
        rows = _build_rows(1)
        rows[0].op_type = ""
        repo = _RepoStub(rows=rows)
        rpc = _RpcAsyncStub()
        settings = RuntimeSettings.from_preset("safe-fast")
        settings.auto_validate = True

        service = ItemJournalServiceAsync(
            rpc=rpc,  # type: ignore[arg-type]
            settings=settings,
            logger=logging.getLogger("test-async-op-type-check"),
            run_control=RunControl(),
        )

        async def _lock_dates(ids: List[int]) -> LockDateFetchResult:
            _ = ids
            return LockDateFetchResult(lock_date_map={796: date(2026, 1, 31)}, status_map={796: "2026-01-31"})

        async def _init_caps():
            return None

        service.lock_date_service.fetch_lock_dates = _lock_dates  # type: ignore[method-assign]
        service.date_sync_service.initialize_capabilities = _init_caps  # type: ignore[method-assign]

        summary = asyncio.run(service.run_upload(repo=repo, dry_run=False))

        self.assertEqual(summary["rows_error"], 1)
        self.assertEqual(summary["rows_success"], 0)
        self.assertEqual(len(rpc.create_calls), 0)
        self.assertEqual(rows[0].result, "[Error]")
        self.assertIn("Operation Type", rows[0].error)

    def test_async_flushes_when_signature_changes_after_limit(self) -> None:
        rows = _build_rows(6, product_ids=[2101, 2101, 2101, 2101, 2102, 2102])
        repo = _RepoStub(rows=rows)
        rpc = _RpcAsyncStub()
        settings = RuntimeSettings.from_preset("safe-fast")
        settings.batch_limit = 3
        settings.auto_validate = True
        audit_sink = _AuditSinkStub()

        service = ItemJournalServiceAsync(
            rpc=rpc,  # type: ignore[arg-type]
            settings=settings,
            logger=logging.getLogger("test-async-signature-boundary-flush"),
            run_control=RunControl(),
            audit_sink=audit_sink,  # type: ignore[arg-type]
        )

        async def _lock_dates(ids: List[int]) -> LockDateFetchResult:
            _ = ids
            return LockDateFetchResult(lock_date_map={796: date(2026, 1, 31)}, status_map={796: "2026-01-31"})

        async def _init_caps():
            return None

        async def _force_date(pick_id: int, company_id: int, date_done_utc: str):
            _ = (pick_id, company_id, date_done_utc)
            return True, "", ""

        async def _fill_stj(pick_id: int, company_id: int, target_rows: List[ItemJournalRow], **kwargs) -> str:
            _ = (pick_id, company_id, kwargs)
            for row in target_rows:
                row.stj = "STJ/BOUNDARY"
            return ""

        service.lock_date_service.fetch_lock_dates = _lock_dates  # type: ignore[method-assign]
        service.date_sync_service.initialize_capabilities = _init_caps  # type: ignore[method-assign]
        service.date_sync_service.force_date_and_validate = _force_date  # type: ignore[method-assign]
        service.stj_service.fill_stj_for_group = _fill_stj  # type: ignore[method-assign]

        summary = asyncio.run(service.run_upload(repo=repo, dry_run=False))

        self.assertEqual(summary["rows_error"], 0)
        self.assertEqual(summary["batches"], 2)
        self.assertEqual(len(rpc.create_calls), 2)
        submitted_sizes = [len(call["values"].get("move_ids", [])) for call in rpc.create_calls]
        self.assertEqual(submitted_sizes, [4, 2])
        deferred_events = [event for event in audit_sink.events if getattr(event, "stage", "") == "BATCH_SIGNATURE_DEFERRED"]
        self.assertEqual(len(deferred_events), 1)

    def test_async_stop_marks_remaining_rows_stopped(self) -> None:
        rows = _build_rows(3)
        repo = _RepoStub(rows=rows)
        rpc = _RpcAsyncStub()
        settings = RuntimeSettings.from_preset("safe-fast")
        settings.batch_limit = 2
        run_control = RunControl()
        run_control.request_stop()

        service = ItemJournalServiceAsync(
            rpc=rpc,  # type: ignore[arg-type]
            settings=settings,
            logger=logging.getLogger("test-async-stop"),
            run_control=run_control,
        )

        async def _lock_dates(ids: List[int]) -> LockDateFetchResult:
            _ = ids
            return LockDateFetchResult(lock_date_map={796: date(2026, 1, 31)}, status_map={796: "2026-01-31"})

        async def _init_caps():
            return None

        async def _force_date(pick_id: int, company_id: int, date_done_utc: str):
            _ = (pick_id, company_id, date_done_utc)
            return True, "", ""

        async def _fill_stj(pick_id: int, company_id: int, target_rows: List[ItemJournalRow], **kwargs) -> str:
            _ = (pick_id, company_id, target_rows, kwargs)
            return ""

        service.lock_date_service.fetch_lock_dates = _lock_dates  # type: ignore[method-assign]
        service.date_sync_service.initialize_capabilities = _init_caps  # type: ignore[method-assign]
        service.date_sync_service.force_date_and_validate = _force_date  # type: ignore[method-assign]
        service.stj_service.fill_stj_for_group = _fill_stj  # type: ignore[method-assign]

        summary = asyncio.run(service.run_upload(repo=repo, dry_run=False))

        self.assertTrue(summary["stopped"])
        self.assertEqual(summary["rows_stopped"], 3)
        self.assertEqual(repo.status, "STOPPED (run-to-batch-stop)")
        self.assertTrue(all(row.result == "[Stopped]" for row in rows))

    def test_async_stj_recovery_success_after_queue(self) -> None:
        rows = _build_rows(2)
        repo = _RepoStub(rows=rows)
        rpc = _RpcAsyncStub()
        settings = RuntimeSettings.from_preset("safe-fast")
        settings.batch_limit = 2
        settings.auto_validate = True
        settings.stj_required = True
        settings.stj_recovery_poll_delay_ms = 0
        run_control = RunControl()
        audit_sink = _AuditSinkStub()

        service = ItemJournalServiceAsync(
            rpc=rpc,  # type: ignore[arg-type]
            settings=settings,
            logger=logging.getLogger("test-async-stj-recovery-success"),
            run_control=run_control,
            audit_sink=audit_sink,  # type: ignore[arg-type]
        )

        async def _lock_dates(ids: List[int]) -> LockDateFetchResult:
            _ = ids
            return LockDateFetchResult(lock_date_map={796: date(2026, 1, 31)}, status_map={796: "2026-01-31"})

        async def _init_caps():
            return None

        async def _force_date(pick_id: int, company_id: int, date_done_utc: str):
            _ = (pick_id, company_id, date_done_utc)
            return True, "", ""

        calls = {"count": 0}

        async def _fill_stj(pick_id: int, company_id: int, target_rows: List[ItemJournalRow], **kwargs) -> str:
            _ = (pick_id, company_id, kwargs)
            calls["count"] += 1
            if calls["count"] == 1:
                for row in target_rows:
                    row.stj = ""
                return "STJ summary (picking=1): rows_stj_empty=2"
            for idx, row in enumerate(target_rows, start=1):
                row.stj = f"STJ/REC/{idx}"
            return ""

        service.lock_date_service.fetch_lock_dates = _lock_dates  # type: ignore[method-assign]
        service.date_sync_service.initialize_capabilities = _init_caps  # type: ignore[method-assign]
        service.date_sync_service.force_date_and_validate = _force_date  # type: ignore[method-assign]
        service.stj_service.fill_stj_for_group = _fill_stj  # type: ignore[method-assign]

        summary = asyncio.run(service.run_upload(repo=repo, dry_run=False))

        self.assertEqual(calls["count"], 2)
        self.assertEqual(summary["rows_error"], 0)
        self.assertEqual(summary["rows_success"], 2)
        self.assertTrue(all(row.result.startswith("INT/") for row in rows))
        self.assertEqual([row.stj for row in rows], ["STJ/REC/1", "STJ/REC/2"])
        queued_count = len([event for event in audit_sink.events if getattr(event, "stage", "") == "STJ_RECOVERY_QUEUED"])
        retry_count = len([event for event in audit_sink.events if getattr(event, "stage", "") == "STJ_RECOVERY_RETRY"])
        done_count = len([event for event in audit_sink.events if getattr(event, "stage", "") == "STJ_RECOVERY_DONE"])
        fail_count = len([event for event in audit_sink.events if getattr(event, "stage", "") == "STJ_REQUIRED_FAIL"])
        stj_done_rows = sorted(event.row_number for event in audit_sink.events if getattr(event, "stage", "") == "STJ_DONE")
        self.assertEqual(queued_count, 1)
        self.assertEqual(retry_count, 1)
        self.assertEqual(done_count, 1)
        self.assertEqual(fail_count, 0)
        self.assertEqual(stj_done_rows, [2, 3])

    def test_async_stj_partial_recovery_emits_each_row_done_once(self) -> None:
        rows = _build_rows(2)
        repo = _RepoStub(rows=rows)
        rpc = _RpcAsyncStub()
        settings = RuntimeSettings.from_preset("safe-fast")
        settings.batch_limit = 2
        settings.auto_validate = True
        settings.stj_required = True
        settings.stj_recovery_poll_delay_ms = 0
        audit_sink = _AuditSinkStub()

        service = ItemJournalServiceAsync(
            rpc=rpc,  # type: ignore[arg-type]
            settings=settings,
            logger=logging.getLogger("test-async-stj-partial-recovery"),
            run_control=RunControl(),
            audit_sink=audit_sink,  # type: ignore[arg-type]
        )

        async def _lock_dates(ids: List[int]) -> LockDateFetchResult:
            _ = ids
            return LockDateFetchResult(lock_date_map={796: date(2026, 1, 31)}, status_map={796: "2026-01-31"})

        async def _init_caps():
            return None

        async def _force_date(pick_id: int, company_id: int, date_done_utc: str):
            _ = (pick_id, company_id, date_done_utc)
            return True, "", ""

        calls = {"count": 0}

        async def _fill_stj(pick_id: int, company_id: int, target_rows: List[ItemJournalRow], **kwargs) -> str:
            _ = (pick_id, company_id, kwargs)
            calls["count"] += 1
            if calls["count"] == 1:
                target_rows[0].stj = "STJ/PARTIAL/1"
                target_rows[1].stj = ""
                return "STJ summary (picking=1): rows_stj_empty=1"
            target_rows[0].stj = "STJ/PARTIAL/1"
            target_rows[1].stj = "STJ/PARTIAL/2"
            return ""

        service.lock_date_service.fetch_lock_dates = _lock_dates  # type: ignore[method-assign]
        service.date_sync_service.initialize_capabilities = _init_caps  # type: ignore[method-assign]
        service.date_sync_service.force_date_and_validate = _force_date  # type: ignore[method-assign]
        service.stj_service.fill_stj_for_group = _fill_stj  # type: ignore[method-assign]

        summary = asyncio.run(service.run_upload(repo=repo, dry_run=False))

        self.assertEqual(calls["count"], 2)
        self.assertEqual(summary["rows_error"], 0)
        self.assertEqual(summary["rows_success"], 2)
        self.assertEqual([row.stj for row in rows], ["STJ/PARTIAL/1", "STJ/PARTIAL/2"])
        stj_done_rows = [event.row_number for event in audit_sink.events if getattr(event, "stage", "") == "STJ_DONE"]
        self.assertEqual(sorted(stj_done_rows), [2, 3])
        self.assertEqual(len(stj_done_rows), 2)
        fail_count = len([event for event in audit_sink.events if getattr(event, "stage", "") == "STJ_REQUIRED_FAIL"])
        self.assertEqual(fail_count, 0)

    def test_async_stj_recovery_timeout_marks_error_when_cost_positive(self) -> None:
        rows = _build_rows(2)
        repo = _RepoStub(rows=rows)
        rpc = _RpcAsyncStub()
        settings = RuntimeSettings.from_preset("safe-fast")
        settings.batch_limit = 2
        settings.auto_validate = True
        settings.stj_required = True
        settings.stj_recovery_timeout_ms = 0
        settings.stj_recovery_poll_delay_ms = 0
        run_control = RunControl()
        audit_sink = _AuditSinkStub()

        service = ItemJournalServiceAsync(
            rpc=rpc,  # type: ignore[arg-type]
            settings=settings,
            logger=logging.getLogger("test-async-stj-required-timeout"),
            run_control=run_control,
            audit_sink=audit_sink,  # type: ignore[arg-type]
        )

        async def _lock_dates(ids: List[int]) -> LockDateFetchResult:
            _ = ids
            return LockDateFetchResult(lock_date_map={796: date(2026, 1, 31)}, status_map={796: "2026-01-31"})

        async def _init_caps():
            return None

        async def _force_date(pick_id: int, company_id: int, date_done_utc: str):
            _ = (pick_id, company_id, date_done_utc)
            return True, "", ""

        async def _fill_stj(pick_id: int, company_id: int, target_rows: List[ItemJournalRow], **kwargs) -> str:
            _ = (pick_id, company_id, kwargs)
            for row in target_rows:
                row.stj = ""
            return "STJ summary (picking=1): rows_stj_empty=2"

        service.lock_date_service.fetch_lock_dates = _lock_dates  # type: ignore[method-assign]
        service.date_sync_service.initialize_capabilities = _init_caps  # type: ignore[method-assign]
        service.date_sync_service.force_date_and_validate = _force_date  # type: ignore[method-assign]
        service.stj_service.fill_stj_for_group = _fill_stj  # type: ignore[method-assign]

        summary = asyncio.run(service.run_upload(repo=repo, dry_run=False))

        self.assertEqual(summary["rows_error"], 2)
        self.assertEqual(summary["rows_success"], 0)
        self.assertTrue(all(row.result == "[Error]" for row in rows))
        self.assertTrue(all(not row.stj for row in rows))
        self.assertTrue(all("STJ_TIMEOUT_WARNING[" in row.error for row in rows))
        self.assertTrue(all("cost item > 0" in row.error for row in rows))
        stj_done_count = len([event for event in audit_sink.events if getattr(event, "stage", "") == "STJ_DONE"])
        self.assertEqual(stj_done_count, 0)
        stages = [getattr(event, "stage", "") for event in audit_sink.events]
        self.assertIn("STJ_RECOVERY_QUEUED", stages)
        self.assertIn("STJ_REQUIRED_WARN", stages)
        self.assertIn("STJ_REQUIRED_FAIL", stages)
        self.assertLess(stages.index("STJ_RECOVERY_QUEUED"), stages.index("STJ_REQUIRED_WARN"))
        row_warn_count = len([event for event in audit_sink.events if getattr(event, "stage", "") == "ROW_WARN"])
        self.assertEqual(row_warn_count, 2)

    def test_async_precheck_blocks_cost_zero_and_stops_remaining_rows(self) -> None:
        rows = _build_rows(2)
        repo = _RepoStub(rows=rows)
        rpc = _RpcAsyncStub()
        settings = RuntimeSettings.from_preset("safe-fast")
        settings.batch_limit = 2
        settings.auto_validate = True
        settings.stj_required = True
        settings.stj_recovery_timeout_ms = 0
        settings.stj_recovery_poll_delay_ms = 0
        run_control = RunControl()
        audit_sink = _AuditSinkStub()

        service = ItemJournalServiceAsync(
            rpc=rpc,  # type: ignore[arg-type]
            settings=settings,
            logger=logging.getLogger("test-async-precheck-zero-cost"),
            run_control=run_control,
            audit_sink=audit_sink,  # type: ignore[arg-type]
        )

        async def _lock_dates(ids: List[int]) -> LockDateFetchResult:
            _ = ids
            return LockDateFetchResult(lock_date_map={796: date(2026, 1, 31)}, status_map={796: "2026-01-31"})

        async def _init_caps():
            return None

        original_search_read = rpc.search_read
        zero_cost_product_id = int(rows[0].prod_id_text)

        async def _search_read_with_zero_cost(  # noqa: ANN001
            model: str,
            domain: List[Any],
            fields=None,
            limit=None,
            order=None,
            context=None,
            stage: str = "",
            excel_row: int = 0,
        ) -> List[Dict[str, Any]]:
            rows_out = await original_search_read(
                model=model,
                domain=domain,
                fields=fields,
                limit=limit,
                order=order,
                context=context,
                stage=stage,
                excel_row=excel_row,
            )
            if model == "product.product":
                for rec in rows_out:
                    if int(rec.get("id") or 0) == zero_cost_product_id:
                        rec["standard_price"] = 0.0
            return rows_out

        service.lock_date_service.fetch_lock_dates = _lock_dates  # type: ignore[method-assign]
        service.date_sync_service.initialize_capabilities = _init_caps  # type: ignore[method-assign]
        rpc.search_read = _search_read_with_zero_cost  # type: ignore[method-assign]

        summary = asyncio.run(service.run_upload(repo=repo, dry_run=False))

        self.assertEqual(summary["rows_error"], 1)
        self.assertEqual(summary["rows_stopped"], 1)
        self.assertEqual(summary["rows_success"], 0)
        self.assertEqual(rows[0].result, "[Error]")
        self.assertIn("Item ini Cost nya masih 0, silakah diperbarui terlebih dahulu", rows[0].error)
        self.assertEqual(rows[1].result, "[Stopped]")
        self.assertIn("Precheck upload gagal", rows[1].error)
        self.assertEqual(len(rpc.create_calls), 0)
        self.assertEqual(len(summary["transfer_results"]), 2)
        self.assertIn("PRECHECK_FAIL", [getattr(event, "stage", "") for event in audit_sink.events])

    def test_async_precheck_blocks_negative_cost(self) -> None:
        rows = _build_rows(1)
        repo = _RepoStub(rows=rows)
        rpc = _RpcAsyncStub()
        settings = RuntimeSettings.from_preset("safe-fast")
        settings.batch_limit = 1
        settings.auto_validate = True
        run_control = RunControl()

        service = ItemJournalServiceAsync(
            rpc=rpc,  # type: ignore[arg-type]
            settings=settings,
            logger=logging.getLogger("test-async-precheck-negative-cost"),
            run_control=run_control,
        )

        async def _lock_dates(ids: List[int]) -> LockDateFetchResult:
            _ = ids
            return LockDateFetchResult(lock_date_map={796: date(2026, 1, 31)}, status_map={796: "2026-01-31"})

        async def _init_caps():
            return None

        original_search_read = rpc.search_read

        async def _search_read_with_negative_cost(  # noqa: ANN001
            model: str,
            domain: List[Any],
            fields=None,
            limit=None,
            order=None,
            context=None,
            stage: str = "",
            excel_row: int = 0,
        ) -> List[Dict[str, Any]]:
            rows_out = await original_search_read(
                model=model,
                domain=domain,
                fields=fields,
                limit=limit,
                order=order,
                context=context,
                stage=stage,
                excel_row=excel_row,
            )
            if model == "product.product":
                for rec in rows_out:
                    rec["standard_price"] = -5.0
            return rows_out

        service.lock_date_service.fetch_lock_dates = _lock_dates  # type: ignore[method-assign]
        service.date_sync_service.initialize_capabilities = _init_caps  # type: ignore[method-assign]
        rpc.search_read = _search_read_with_negative_cost  # type: ignore[method-assign]

        summary = asyncio.run(service.run_upload(repo=repo, dry_run=False))

        self.assertEqual(summary["rows_error"], 1)
        self.assertEqual(summary["rows_success"], 0)
        self.assertEqual(summary["rows_stopped"], 0)
        self.assertEqual(rows[0].result, "[Error]")
        self.assertIn("Cost nya bernilai negatif", rows[0].error)
        self.assertEqual(len(rpc.create_calls), 0)

    def test_async_precheck_blocks_non_storeable_product(self) -> None:
        rows = _build_rows(1)
        repo = _RepoStub(rows=rows)
        rpc = _RpcAsyncStub()
        settings = RuntimeSettings.from_preset("safe-fast")
        settings.batch_limit = 1
        settings.auto_validate = True
        run_control = RunControl()

        service = ItemJournalServiceAsync(
            rpc=rpc,  # type: ignore[arg-type]
            settings=settings,
            logger=logging.getLogger("test-async-precheck-non-storeable"),
            run_control=run_control,
        )

        async def _lock_dates(ids: List[int]) -> LockDateFetchResult:
            _ = ids
            return LockDateFetchResult(lock_date_map={796: date(2026, 1, 31)}, status_map={796: "2026-01-31"})

        async def _init_caps():
            return None

        original_search_read = rpc.search_read

        async def _search_read_non_storeable(  # noqa: ANN001
            model: str,
            domain: List[Any],
            fields=None,
            limit=None,
            order=None,
            context=None,
            stage: str = "",
            excel_row: int = 0,
        ) -> List[Dict[str, Any]]:
            rows_out = await original_search_read(
                model=model,
                domain=domain,
                fields=fields,
                limit=limit,
                order=order,
                context=context,
                stage=stage,
                excel_row=excel_row,
            )
            if model == "product.product":
                for rec in rows_out:
                    rec["is_storable"] = False
                    rec["detailed_type"] = "consu"
            return rows_out

        service.lock_date_service.fetch_lock_dates = _lock_dates  # type: ignore[method-assign]
        service.date_sync_service.initialize_capabilities = _init_caps  # type: ignore[method-assign]
        rpc.search_read = _search_read_non_storeable  # type: ignore[method-assign]

        summary = asyncio.run(service.run_upload(repo=repo, dry_run=False))

        self.assertEqual(summary["rows_error"], 1)
        self.assertEqual(summary["rows_success"], 0)
        self.assertEqual(rows[0].result, "[Error]")
        self.assertIn("bukan Track Inventory by Quantity", rows[0].error)
        self.assertEqual(len(rpc.create_calls), 0)

    def test_async_precheck_blocks_when_lock_date_same_or_greater_than_journal_date(self) -> None:
        rows = _build_rows(1)
        repo = _RepoStub(rows=rows)
        rpc = _RpcAsyncStub()
        settings = RuntimeSettings.from_preset("safe-fast")
        settings.batch_limit = 1
        settings.auto_validate = True

        service = ItemJournalServiceAsync(
            rpc=rpc,  # type: ignore[arg-type]
            settings=settings,
            logger=logging.getLogger("test-async-precheck-lock-date"),
            run_control=RunControl(),
        )

        async def _init_caps():
            return None

        service.date_sync_service.initialize_capabilities = _init_caps  # type: ignore[method-assign]

        # Case 1: lock date == journal date.
        async def _lock_dates_equal(ids: List[int]) -> LockDateFetchResult:
            _ = ids
            return LockDateFetchResult(lock_date_map={796: date(2026, 3, 1)}, status_map={796: "2026-03-01"})

        service.lock_date_service.fetch_lock_dates = _lock_dates_equal  # type: ignore[method-assign]
        summary_equal = asyncio.run(service.run_upload(repo=repo, dry_run=False))
        self.assertEqual(summary_equal["rows_error"], 1)
        self.assertIn("Date Accounting sudah ditutup", rows[0].error)
        self.assertEqual(len(rpc.create_calls), 0)

        # Case 2: lock date > journal date.
        rows[0].result = ""
        rows[0].stj = ""
        rows[0].error = ""
        rpc.create_calls.clear()

        async def _lock_dates_greater(ids: List[int]) -> LockDateFetchResult:
            _ = ids
            return LockDateFetchResult(lock_date_map={796: date(2026, 3, 2)}, status_map={796: "2026-03-02"})

        service.lock_date_service.fetch_lock_dates = _lock_dates_greater  # type: ignore[method-assign]
        summary_greater = asyncio.run(service.run_upload(repo=repo, dry_run=False))
        self.assertEqual(summary_greater["rows_error"], 1)
        self.assertIn("Date Accounting sudah ditutup", rows[0].error)
        self.assertEqual(len(rpc.create_calls), 0)

    def test_async_does_not_append_stj_warning_for_rows_already_error(self) -> None:
        rows = _build_rows(1)
        repo = _RepoStub(rows=rows)
        rpc = _RpcAsyncStub()
        settings = RuntimeSettings.from_preset("safe-fast")
        settings.batch_limit = 1
        settings.auto_validate = True
        settings.stj_required = True

        service = ItemJournalServiceAsync(
            rpc=rpc,  # type: ignore[arg-type]
            settings=settings,
            logger=logging.getLogger("test-async-stj-warning-filter"),
            run_control=RunControl(),
        )

        async def _lock_dates(ids: List[int]) -> LockDateFetchResult:
            _ = ids
            return LockDateFetchResult(lock_date_map={796: date(2026, 1, 31)}, status_map={796: "2026-01-31"})

        async def _init_caps():
            return None

        async def _force_date(pick_id: int, company_id: int, date_done_utc: str):
            _ = (pick_id, company_id, date_done_utc)
            return True, "", ""

        async def _fill_stj(pick_id: int, company_id: int, target_rows: List[ItemJournalRow], **kwargs) -> str:
            _ = (pick_id, company_id, kwargs)
            for row in target_rows:
                row.mark_error("forced remap fail")
                row.stj = ""
            return "STJ remap gagal (picking=1): qty mismatch."

        service.lock_date_service.fetch_lock_dates = _lock_dates  # type: ignore[method-assign]
        service.date_sync_service.initialize_capabilities = _init_caps  # type: ignore[method-assign]
        service.date_sync_service.force_date_and_validate = _force_date  # type: ignore[method-assign]
        service.stj_service.fill_stj_for_group = _fill_stj  # type: ignore[method-assign]

        summary = asyncio.run(service.run_upload(repo=repo, dry_run=False))

        self.assertEqual(summary["rows_error"], 1)
        self.assertIn("forced remap fail", rows[0].error)
        self.assertNotIn("STJ warning:", rows[0].error)

    def test_async_create_timeout_extra_retry_success(self) -> None:
        rows = _build_rows(1)
        rpc = _RpcAsyncStub()
        settings = RuntimeSettings.from_preset("safe-fast")
        settings.retry_max = 0
        settings.create_timeout_extra_retry_max = 1
        settings.create_timeout_extra_retry_delay_ms = 0
        service = ItemJournalServiceAsync(
            rpc=rpc,  # type: ignore[arg-type]
            settings=settings,
            logger=logging.getLogger("test-async-extra-retry-ok"),
            run_control=RunControl(),
        )
        batch_entries = [BatchEntry(row=rows[0], move_command=[0, 0, {"name": "move"}])]
        calls = {"count": 0}

        async def _submit(**kwargs):
            pick_acc = kwargs["pick_acc"]
            calls["count"] += 1
            if calls["count"] == 1:
                return False, "stock.picking.create timeout."
            pick_acc.picking_id = 9101
            pick_acc.picking_name = "INT/9101"
            return True, ""

        service._submit_picking_batch = _submit  # type: ignore[method-assign]

        ok, err, split_count = asyncio.run(
            service._process_batch_with_retry(
                batch_entries=batch_entries,
                company_id=796,
                pick_type_id=77,
                src_loc_id=1001,
                dest_loc_id=1002,
                date_done_utc="2026-03-01 00:00:00",
                precheck_error="",
                dry_run=False,
                group_key="g1",
                batch_seq=1,
            )
        )

        self.assertTrue(ok)
        self.assertEqual(err, "")
        self.assertEqual(split_count, 0)
        self.assertEqual(calls["count"], 2)
        self.assertEqual(rows[0].result, "INT/9101")

    def test_async_create_timeout_retry_only_then_fail_without_split(self) -> None:
        rows = _build_rows(2)
        rpc = _RpcAsyncStub()
        settings = RuntimeSettings.from_preset("safe-fast")
        settings.retry_max = 0
        service = ItemJournalServiceAsync(
            rpc=rpc,  # type: ignore[arg-type]
            settings=settings,
            logger=logging.getLogger("test-async-timeout-retry-only-fail"),
            run_control=RunControl(),
        )
        batch_entries = [
            BatchEntry(row=rows[0], move_command=[0, 0, {"name": "move-1"}]),
            BatchEntry(row=rows[1], move_command=[0, 0, {"name": "move-2"}]),
        ]
        calls = {"count": 0}

        async def _submit(**kwargs):
            _ = kwargs
            calls["count"] += 1
            return False, "stock.picking.create timeout."

        service._submit_picking_batch = _submit  # type: ignore[method-assign]

        ok, err, split_count = asyncio.run(
            service._process_batch_with_retry(
                batch_entries=batch_entries,
                company_id=796,
                pick_type_id=77,
                src_loc_id=1001,
                dest_loc_id=1002,
                date_done_utc="2026-03-01 00:00:00",
                precheck_error="",
                dry_run=False,
                group_key="g2",
                batch_seq=2,
            )
        )

        self.assertFalse(ok)
        self.assertIn("stock.picking.create timeout", err)
        self.assertEqual(split_count, 0)
        self.assertEqual(calls["count"], 4)
        self.assertTrue(all(row.result == "[Error]" for row in rows))

    def test_async_create_timeout_retry_only_exhausts_without_split_large_batch(self) -> None:
        rows = _build_rows(75)
        rpc = _RpcAsyncStub()
        settings = RuntimeSettings.from_preset("safe-fast")
        settings.retry_max = 0
        service = ItemJournalServiceAsync(
            rpc=rpc,  # type: ignore[arg-type]
            settings=settings,
            logger=logging.getLogger("test-async-timeout-no-split-large"),
            run_control=RunControl(),
        )
        batch_entries = [
            BatchEntry(row=row, move_command=[0, 0, {"name": f"move-{row.row_number}"}])
            for row in rows
        ]
        calls: List[int] = []
        next_pick_id = {"value": 9400}

        async def _submit(**kwargs):
            size = len(kwargs["move_commands"])
            calls.append(size)
            _ = next_pick_id
            return False, "stock.picking.create timeout."

        service._submit_picking_batch = _submit  # type: ignore[method-assign]

        ok, err, split_count = asyncio.run(
            service._process_batch_with_retry(
                batch_entries=batch_entries,
                company_id=796,
                pick_type_id=77,
                src_loc_id=1001,
                dest_loc_id=1002,
                date_done_utc="2026-03-01 00:00:00",
                precheck_error="",
                dry_run=False,
                group_key="g75",
                batch_seq=3,
            )
        )

        self.assertFalse(ok)
        self.assertIn("stock.picking.create timeout", err)
        self.assertEqual(split_count, 0)
        self.assertEqual(calls[0], 75)
        self.assertEqual(len(calls), 4)
        self.assertTrue(all(size == 75 for size in calls))
        self.assertTrue(all(row.result == "[Error]" for row in rows))

    def test_batch_hash16_stable_for_same_payload_different_order(self) -> None:
        rows = _build_rows(2)
        rpc = _RpcAsyncStub()
        settings = RuntimeSettings.from_preset("safe-fast")
        service = ItemJournalServiceAsync(
            rpc=rpc,  # type: ignore[arg-type]
            settings=settings,
            logger=logging.getLogger("test-async-hash-stable"),
            run_control=RunControl(),
        )
        move_a = [0, 0, {"product_id": 10, "product_uom_qty": 1.0, "product_uom": 1, "location_id": 1001, "location_dest_id": 1002}]
        move_b = [0, 0, {"product_id": 20, "product_uom_qty": 2.0, "product_uom": 1, "location_id": 1001, "location_dest_id": 1002}]
        entries_one = [BatchEntry(row=rows[0], move_command=move_a), BatchEntry(row=rows[1], move_command=move_b)]
        entries_two = [BatchEntry(row=rows[1], move_command=move_b), BatchEntry(row=rows[0], move_command=move_a)]

        hash_one = service._build_batch_hash16(entries_one, 796, 77, 1001, 1002, "2026-03-01 00:00:00")
        hash_two = service._build_batch_hash16(entries_two, 796, 77, 1001, 1002, "2026-03-01 00:00:00")

        self.assertEqual(hash_one, hash_two)

    def test_async_create_timeout_reconcile_hit_marks_success_without_split(self) -> None:
        rows = _build_rows(1)
        rpc = _RpcAsyncStub()
        settings = RuntimeSettings.from_preset("safe-fast")
        settings.retry_max = 0
        settings.create_timeout_extra_retry_max = 0
        service = ItemJournalServiceAsync(
            rpc=rpc,  # type: ignore[arg-type]
            settings=settings,
            logger=logging.getLogger("test-async-reconcile-hit"),
            run_control=RunControl(),
        )
        batch_entries = [BatchEntry(row=rows[0], move_command=[0, 0, {"name": "move-1"}])]
        calls = {"submit": 0}

        async def _submit(**kwargs):
            _ = kwargs
            calls["submit"] += 1
            return False, "stock.picking.create timeout."

        async def _reconcile(**kwargs):
            _ = kwargs
            return "HIT", {"id": 9301, "name": "INT/9301", "state": "done"}

        service._submit_picking_batch = _submit  # type: ignore[method-assign]
        service._reconcile_create_timeout = _reconcile  # type: ignore[method-assign]

        ok, err, split_count = asyncio.run(
            service._process_batch_with_retry(
                batch_entries=batch_entries,
                company_id=796,
                pick_type_id=77,
                src_loc_id=1001,
                dest_loc_id=1002,
                date_done_utc="2026-03-01 00:00:00",
                precheck_error="",
                dry_run=False,
                group_key="g-reconcile-hit",
                batch_seq=9,
            )
        )

        self.assertTrue(ok)
        self.assertEqual(err, "")
        self.assertEqual(split_count, 0)
        self.assertEqual(calls["submit"], 1)
        self.assertEqual(rows[0].result, "INT/9301")

    def test_async_create_timeout_reconcile_miss_then_retry_only_fail(self) -> None:
        rows = _build_rows(2)
        rpc = _RpcAsyncStub()
        settings = RuntimeSettings.from_preset("safe-fast")
        settings.retry_max = 0
        service = ItemJournalServiceAsync(
            rpc=rpc,  # type: ignore[arg-type]
            settings=settings,
            logger=logging.getLogger("test-async-reconcile-miss-retry-only"),
            run_control=RunControl(),
        )
        batch_entries = [
            BatchEntry(row=rows[0], move_command=[0, 0, {"name": "move-1"}]),
            BatchEntry(row=rows[1], move_command=[0, 0, {"name": "move-2"}]),
        ]
        calls = {"submit": 0}

        async def _submit(**kwargs):
            _ = kwargs
            calls["submit"] += 1
            return False, "stock.picking.create timeout."

        async def _reconcile(**kwargs):
            _ = kwargs
            return "MISS", None

        service._submit_picking_batch = _submit  # type: ignore[method-assign]
        service._reconcile_create_timeout = _reconcile  # type: ignore[method-assign]

        ok, err, split_count = asyncio.run(
            service._process_batch_with_retry(
                batch_entries=batch_entries,
                company_id=796,
                pick_type_id=77,
                src_loc_id=1001,
                dest_loc_id=1002,
                date_done_utc="2026-03-01 00:00:00",
                precheck_error="",
                dry_run=False,
                group_key="g-reconcile-miss",
                batch_seq=10,
            )
        )

        self.assertFalse(ok)
        self.assertIn("stock.picking.create timeout", err)
        self.assertEqual(split_count, 0)
        self.assertEqual(calls["submit"], 4)
        self.assertTrue(all(row.result == "[Error]" for row in rows))

    def test_reconcile_ambiguous_selects_highest_state_then_latest(self) -> None:
        service = ItemJournalServiceAsync(
            rpc=_RpcAsyncStub(),  # type: ignore[arg-type]
            settings=RuntimeSettings.from_preset("safe-fast"),
            logger=logging.getLogger("test-async-ambiguous-select"),
            run_control=RunControl(),
        )
        candidates = [
            {"id": 10, "name": "INT/10", "state": "draft", "create_date": "2026-03-01 10:00:00"},
            {"id": 11, "name": "INT/11", "state": "done", "create_date": "2026-03-01 09:00:00"},
            {"id": 12, "name": "INT/12", "state": "assigned", "create_date": "2026-03-01 11:00:00"},
            {"id": 13, "name": "INT/13", "state": "done", "create_date": "2026-03-01 11:00:00"},
        ]
        selected = service._select_reconcile_candidate(candidates)
        self.assertIsNotNone(selected)
        self.assertEqual(int(selected["id"]), 13)

    def test_create_timeout_classifier_covers_variant_messages(self) -> None:
        rpc = _RpcAsyncStub()
        service = ItemJournalServiceAsync(
            rpc=rpc,  # type: ignore[arg-type]
            settings=RuntimeSettings.from_preset("safe-fast"),
            logger=logging.getLogger("test-async-timeout-classifier"),
            run_control=RunControl(),
        )
        rpc.call_context = SimpleNamespace(model="stock.picking", method="create", stage="BATCH_CREATE", excel_row=0)
        self.assertTrue(service._is_stock_picking_create_timeout("Request timed out while waiting response."))
        self.assertTrue(service._is_stock_picking_create_timeout("stock.picking.create failed. HTTP status 504"))
        rpc.call_context = SimpleNamespace(model="product.product", method="create", stage="ROW_LOOP", excel_row=0)
        self.assertFalse(service._is_stock_picking_create_timeout("product.product.create timeout"))

    def test_stj_recovery_hybrid_schedule_respects_cap(self) -> None:
        settings = RuntimeSettings.from_preset("safe-fast")
        settings.stj_recovery_poll_delay_ms = 1000
        settings.stj_recovery_fast_attempts = 2
        settings.stj_recovery_poll_max_delay_ms = 3000
        service = ItemJournalServiceAsync(
            rpc=_RpcAsyncStub(),  # type: ignore[arg-type]
            settings=settings,
            logger=logging.getLogger("test-async-stj-recovery-backoff"),
            run_control=RunControl(),
        )
        delays = [service._stj_recovery_delay_ms(next_attempt=attempt) for attempt in [1, 2, 3, 4, 5]]
        self.assertEqual(delays, [1000, 1000, 2000, 3000, 3000])

    def test_async_stj_required_without_auto_validate_raises(self) -> None:
        rows = _build_rows(1)
        repo = _RepoStub(rows=rows)
        rpc = _RpcAsyncStub()
        settings = RuntimeSettings.from_preset("safe-fast")
        settings.auto_validate = False
        settings.stj_required = True
        service = ItemJournalServiceAsync(
            rpc=rpc,  # type: ignore[arg-type]
            settings=settings,
            logger=logging.getLogger("test-async-config"),
            run_control=RunControl(),
        )

        with self.assertRaises(RuntimeError) as ctx:
            asyncio.run(service.run_upload(repo=repo, dry_run=False))
        self.assertIn("STJ_REQUIRED=true membutuhkan AUTO_VALIDATE=true", str(ctx.exception))

    def test_async_guard_forces_conservative_and_emits_audit(self) -> None:
        rows = _build_rows(2)
        repo = _RepoStub(rows=rows)
        rpc = _RpcAsyncStub()
        settings = RuntimeSettings.from_preset("safe-fast")
        settings.batch_limit = 2
        settings.auto_validate = True
        settings.stj_remap_mode = "legacy"
        run_control = RunControl()
        audit_sink = _AuditSinkStub()

        service = ItemJournalServiceAsync(
            rpc=rpc,  # type: ignore[arg-type]
            settings=settings,
            logger=logging.getLogger("test-async-guard-force"),
            run_control=run_control,
            audit_sink=audit_sink,  # type: ignore[arg-type]
        )

        async def _lock_dates(ids: List[int]) -> LockDateFetchResult:
            _ = ids
            return LockDateFetchResult(lock_date_map={796: date(2026, 1, 31)}, status_map={796: "2026-01-31"})

        async def _init_caps():
            return None

        async def _force_date(pick_id: int, company_id: int, date_done_utc: str):
            _ = (pick_id, company_id, date_done_utc)
            return True, "", ""

        async def _fill_stj(pick_id: int, company_id: int, target_rows: List[ItemJournalRow], **kwargs) -> str:
            _ = (pick_id, company_id, kwargs)
            for row in target_rows:
                row.stj = "STJ/GUARD"
            return ""

        service.lock_date_service.fetch_lock_dates = _lock_dates  # type: ignore[method-assign]
        service.date_sync_service.initialize_capabilities = _init_caps  # type: ignore[method-assign]
        service.date_sync_service.force_date_and_validate = _force_date  # type: ignore[method-assign]
        service.stj_service.fill_stj_for_group = _fill_stj  # type: ignore[method-assign]

        summary = asyncio.run(service.run_upload(repo=repo, dry_run=False))

        self.assertEqual(summary["rows_error"], 0)
        self.assertEqual(settings.stj_remap_mode, "conservative")
        guard_events = [event for event in audit_sink.events if getattr(event, "stage", "") == "CONFIG_GUARD_REMAP_FORCED"]
        self.assertEqual(len(guard_events), 1)
        self.assertIn("old_mode=legacy", guard_events[0].message)
        self.assertIn("new_mode=conservative", guard_events[0].message)
        self.assertIn("dry_run=False", guard_events[0].message)
        self.assertIn("reason=production_guard", guard_events[0].message)

    def test_async_guard_not_emitted_when_already_conservative(self) -> None:
        rows = _build_rows(2)
        repo = _RepoStub(rows=rows)
        rpc = _RpcAsyncStub()
        settings = RuntimeSettings.from_preset("safe-fast")
        settings.batch_limit = 2
        settings.auto_validate = True
        settings.stj_remap_mode = "conservative"
        run_control = RunControl()
        audit_sink = _AuditSinkStub()

        service = ItemJournalServiceAsync(
            rpc=rpc,  # type: ignore[arg-type]
            settings=settings,
            logger=logging.getLogger("test-async-guard-noop"),
            run_control=run_control,
            audit_sink=audit_sink,  # type: ignore[arg-type]
        )

        async def _lock_dates(ids: List[int]) -> LockDateFetchResult:
            _ = ids
            return LockDateFetchResult(lock_date_map={796: date(2026, 1, 31)}, status_map={796: "2026-01-31"})

        async def _init_caps():
            return None

        async def _force_date(pick_id: int, company_id: int, date_done_utc: str):
            _ = (pick_id, company_id, date_done_utc)
            return True, "", ""

        async def _fill_stj(pick_id: int, company_id: int, target_rows: List[ItemJournalRow], **kwargs) -> str:
            _ = (pick_id, company_id, kwargs)
            for row in target_rows:
                row.stj = "STJ/OK"
            return ""

        service.lock_date_service.fetch_lock_dates = _lock_dates  # type: ignore[method-assign]
        service.date_sync_service.initialize_capabilities = _init_caps  # type: ignore[method-assign]
        service.date_sync_service.force_date_and_validate = _force_date  # type: ignore[method-assign]
        service.stj_service.fill_stj_for_group = _fill_stj  # type: ignore[method-assign]

        summary = asyncio.run(service.run_upload(repo=repo, dry_run=False))

        self.assertEqual(summary["rows_error"], 0)
        self.assertEqual(settings.stj_remap_mode, "conservative")
        guard_events = [event for event in audit_sink.events if getattr(event, "stage", "") == "CONFIG_GUARD_REMAP_FORCED"]
        self.assertEqual(len(guard_events), 0)

    def test_async_guard_not_forced_in_dry_run(self) -> None:
        rows = _build_rows(1)
        repo = _RepoStub(rows=rows)
        rpc = _RpcAsyncStub()
        settings = RuntimeSettings.from_preset("safe-fast")
        settings.batch_limit = 1
        settings.stj_remap_mode = "legacy"
        run_control = RunControl()
        audit_sink = _AuditSinkStub()

        service = ItemJournalServiceAsync(
            rpc=rpc,  # type: ignore[arg-type]
            settings=settings,
            logger=logging.getLogger("test-async-guard-dry-run"),
            run_control=run_control,
            audit_sink=audit_sink,  # type: ignore[arg-type]
        )

        async def _lock_dates(ids: List[int]) -> LockDateFetchResult:
            _ = ids
            return LockDateFetchResult(lock_date_map={796: date(2026, 1, 31)}, status_map={796: "2026-01-31"})

        async def _init_caps():
            return None

        service.lock_date_service.fetch_lock_dates = _lock_dates  # type: ignore[method-assign]
        service.date_sync_service.initialize_capabilities = _init_caps  # type: ignore[method-assign]

        summary = asyncio.run(service.run_upload(repo=repo, dry_run=True))

        self.assertEqual(summary["rows_error"], 0)
        self.assertEqual(settings.stj_remap_mode, "legacy")
        guard_events = [event for event in audit_sink.events if getattr(event, "stage", "") == "CONFIG_GUARD_REMAP_FORCED"]
        self.assertEqual(len(guard_events), 0)

    def test_async_transfer_progress_payload_contains_business_context(self) -> None:
        rows = _build_rows(2)
        repo = _RepoStub(rows=rows)
        rpc = _RpcAsyncStub()
        settings = RuntimeSettings.from_preset("safe-fast")
        settings.batch_limit = 2
        settings.auto_validate = True
        progress_events: List[Dict[str, Any]] = []
        run_control = RunControl(progress_callback=lambda payload: progress_events.append(dict(payload)))

        service = ItemJournalServiceAsync(
            rpc=rpc,  # type: ignore[arg-type]
            settings=settings,
            logger=logging.getLogger("test-async-row-item-progress"),
            run_control=run_control,
        )

        async def _lock_dates(ids: List[int]) -> LockDateFetchResult:
            _ = ids
            return LockDateFetchResult(lock_date_map={796: date(2026, 1, 31)}, status_map={796: "2026-01-31"})

        async def _init_caps():
            return None

        async def _force_date(pick_id: int, company_id: int, date_done_utc: str):
            _ = (pick_id, company_id, date_done_utc)
            return True, "", ""

        async def _fill_stj(pick_id: int, company_id: int, target_rows: List[ItemJournalRow], **kwargs) -> str:
            _ = (pick_id, company_id, kwargs)
            for row in target_rows:
                row.stj = "STJ/ROW-ITEM"
            return ""

        service.lock_date_service.fetch_lock_dates = _lock_dates  # type: ignore[method-assign]
        service.date_sync_service.initialize_capabilities = _init_caps  # type: ignore[method-assign]
        service.date_sync_service.force_date_and_validate = _force_date  # type: ignore[method-assign]
        service.stj_service.fill_stj_for_group = _fill_stj  # type: ignore[method-assign]

        summary = asyncio.run(service.run_upload(repo=repo, dry_run=False))

        self.assertEqual(summary["rows_error"], 0)
        transfer_start_events = [payload for payload in progress_events if payload.get("stage") == "TRANSFER_START"]
        transfer_progress_events = [payload for payload in progress_events if payload.get("stage") == "TRANSFER_PROGRESS"]
        transfer_done_events = [payload for payload in progress_events if payload.get("stage") == "TRANSFER_DONE"]

        self.assertGreaterEqual(len(transfer_start_events), 1)
        self.assertGreaterEqual(len(transfer_progress_events), 1)
        self.assertGreaterEqual(len(transfer_done_events), 1)

        first_start = transfer_start_events[0]
        self.assertIn("company_name", first_start)
        self.assertIn("item_count", first_start)
        self.assertIn("src", first_start)
        self.assertIn("dest", first_start)
        self.assertIn("Transfer Internal", str(first_start.get("message", "")))

        first_done = transfer_done_events[0]
        self.assertIn("transfer_ref", first_done)
        self.assertIn("berhasil", str(first_done.get("message", "")).lower())

    def test_async_validate_failure_triggers_cancel_cleanup(self) -> None:
        rows = _build_rows(1)
        repo = _RepoStub(rows=rows)
        rpc = _RpcAsyncStub()
        settings = RuntimeSettings.from_preset("safe-fast")
        settings.batch_limit = 1
        settings.auto_validate = True
        settings.validate_cleanup_policy = "cancel_then_keep"

        service = ItemJournalServiceAsync(
            rpc=rpc,  # type: ignore[arg-type]
            settings=settings,
            logger=logging.getLogger("test-async-cleanup-cancel"),
            run_control=RunControl(),
        )

        async def _lock_dates(ids: List[int]) -> LockDateFetchResult:
            _ = ids
            return LockDateFetchResult(lock_date_map={796: date(2026, 1, 31)}, status_map={796: "2026-01-31"})

        async def _init_caps():
            return None

        async def _force_date(pick_id: int, company_id: int, date_done_utc: str):
            _ = (pick_id, company_id, date_done_utc)
            return False, "Backorder terdeteksi", ""

        service.lock_date_service.fetch_lock_dates = _lock_dates  # type: ignore[method-assign]
        service.date_sync_service.initialize_capabilities = _init_caps  # type: ignore[method-assign]
        service.date_sync_service.force_date_and_validate = _force_date  # type: ignore[method-assign]

        summary = asyncio.run(service.run_upload(repo=repo, dry_run=False))

        self.assertEqual(summary["rows_error"], 1)
        self.assertEqual(rows[0].result, "[Error]")
        self.assertIn("Cleanup selesai", rows[0].error)
        cancel_calls = [
            call
            for call in rpc.execute_kw_calls
            if call.get("model") == "stock.picking" and call.get("method") == "action_cancel"
        ]
        self.assertGreaterEqual(len(cancel_calls), 1)

    def test_async_hybrid_journal_gate_marks_not_expected_for_internal_manual(self) -> None:
        rows = _build_rows(1)
        repo = _RepoStub(rows=rows)
        rpc = _RpcAsyncStub()
        rpc.location_usage[1001] = "internal"
        rpc.location_usage[1002] = "internal"
        rpc.product_category_by_product[int(rows[0].prod_id_text)] = 777
        rpc.category_valuation_by_id[777] = "manual_periodic"

        settings = RuntimeSettings.from_preset("safe-fast")
        settings.batch_limit = 1
        settings.auto_validate = True
        settings.stj_required = True
        settings.journal_expectation_mode = "hybrid"

        service = ItemJournalServiceAsync(
            rpc=rpc,  # type: ignore[arg-type]
            settings=settings,
            logger=logging.getLogger("test-async-hybrid-not-expected"),
            run_control=RunControl(),
        )

        async def _lock_dates(ids: List[int]) -> LockDateFetchResult:
            _ = ids
            return LockDateFetchResult(lock_date_map={796: date(2026, 1, 31)}, status_map={796: "2026-01-31"})

        async def _init_caps():
            return None

        async def _force_date(pick_id: int, company_id: int, date_done_utc: str):
            _ = (pick_id, company_id, date_done_utc)
            return True, "", ""

        async def _fill_stj(pick_id: int, company_id: int, target_rows: List[ItemJournalRow], **kwargs) -> str:
            _ = (pick_id, company_id, kwargs)
            for row in target_rows:
                row.stj = ""
            return ""

        service.lock_date_service.fetch_lock_dates = _lock_dates  # type: ignore[method-assign]
        service.date_sync_service.initialize_capabilities = _init_caps  # type: ignore[method-assign]
        service.date_sync_service.force_date_and_validate = _force_date  # type: ignore[method-assign]
        service.stj_service.fill_stj_for_group = _fill_stj  # type: ignore[method-assign]

        summary = asyncio.run(service.run_upload(repo=repo, dry_run=False))

        self.assertEqual(summary["rows_error"], 0)
        self.assertEqual(summary["rows_success"], 1)
        self.assertIn("STJ tidak diwajibkan", rows[0].error)
        self.assertEqual(summary["transfer_results"][0]["journal_status"], "not_expected")
        self.assertFalse(summary["transfer_results"][0]["journal_expected"])
        self.assertIn("internal", summary["transfer_results"][0]["journal_reason"])

    def test_journal_summary_aggregation_classifies_rows_and_transfers(self) -> None:
        rpc = _RpcAsyncStub()
        settings = RuntimeSettings.from_preset("safe-fast")
        service = ItemJournalServiceAsync(
            rpc=rpc,  # type: ignore[arg-type]
            settings=settings,
            logger=logging.getLogger("test-journal-summary-aggregate"),
            run_control=RunControl(),
        )

        transfer_results = [
            {"row_number": 1, "transfer_ref": "INT/1", "journal_status": "ok", "journal_refs": ["STJ/1"], "error": ""},
            {"row_number": 2, "transfer_ref": "INT/2", "journal_status": "missing", "journal_refs": [], "error": ""},
            {"row_number": 3, "transfer_ref": "INT/3", "journal_status": "not_expected", "journal_refs": [], "error": ""},
            {
                "row_number": 4,
                "transfer_ref": "INT/4",
                "journal_status": "ok",
                "journal_refs": ["STJ/4"],
                "error": "stj_timeout_warning[timeout]",
            },
            {"row_number": 5, "transfer_ref": "", "journal_status": "pending", "journal_refs": [], "error": ""},
        ]

        summary, details = service._aggregate_journal_summary(transfer_results)

        self.assertEqual(summary["row_ok"], 1)
        self.assertEqual(summary["row_failed"], 2)
        self.assertEqual(summary["row_not_required"], 1)
        self.assertEqual(summary["row_other"], 1)
        self.assertEqual(summary["transfer_ok"], 1)
        self.assertEqual(summary["transfer_failed"], 2)
        self.assertEqual(summary["transfer_not_required"], 1)
        self.assertEqual(summary["transfer_other"], 1)
        self.assertEqual(details["row_total"], 5)

    def test_journal_summary_logs_single_business_line_without_per_stj_spam(self) -> None:
        rpc = _RpcAsyncStub()
        settings = RuntimeSettings.from_preset("safe-fast")
        logger = logging.getLogger("test-journal-summary-line")
        logger.setLevel(logging.INFO)
        logger.propagate = False
        logger.handlers = []
        handler = _ListHandler()
        logger.addHandler(handler)

        service = ItemJournalServiceAsync(
            rpc=rpc,  # type: ignore[arg-type]
            settings=settings,
            logger=logger,
            run_control=RunControl(),
        )

        transfer_results = [
            {"row_number": 1, "transfer_ref": "INT/1", "journal_status": "ok", "journal_refs": ["STJ/1"], "error": ""},
            {"row_number": 2, "transfer_ref": "INT/2", "journal_status": "missing", "journal_refs": [], "error": ""},
            {"row_number": 3, "transfer_ref": "INT/3", "journal_status": "not_expected", "journal_refs": [], "error": ""},
        ]
        service._narrate_transfer_results(transfer_results)

        messages = [record.getMessage() for record in handler.records]
        summary_messages = [msg for msg in messages if "Ringkasan Jurnal Akuntansi:" in msg]
        stj_detail_messages = [msg for msg in messages if "No. Jurnal Akuntansi berhasil diterbitkan:" in msg]

        self.assertEqual(len(summary_messages), 1)
        self.assertEqual(len(stj_detail_messages), 0)
        self.assertTrue(any(record.levelno == logging.WARNING for record in handler.records))

    def test_sync_wait_payload_includes_context_and_top_bottom_preview(self) -> None:
        rpc = _RpcAsyncStub()
        settings = RuntimeSettings.from_preset("safe-fast")
        service = ItemJournalServiceAsync(
            rpc=rpc,  # type: ignore[arg-type]
            settings=settings,
            logger=logging.getLogger("test-sync-wait-preview"),
            run_control=RunControl(),
        )

        service._set_active_transfer_context(
            company_name="CECILIA",
            src="WH/Stock",
            dest="WH/Output",
            item_count=0,
            reset_items=True,
        )
        for idx in range(1, 13):
            service._append_active_transfer_item(product_name=f"Item {idx}", qty=idx)

        payload = service._build_sync_wait_payload(
            message="Sedang menyinkronkan data besar, mohon tunggu sebentar...",
            slow_level="info",
            elapsed_sec=12,
            batch_seq=3,
        )
        self.assertEqual(payload["company_name"], "CECILIA")
        self.assertEqual(payload["src"], "WH/Stock")
        self.assertEqual(payload["dest"], "WH/Output")
        self.assertEqual(payload["item_count"], 12)
        self.assertEqual(len(payload["preview_items"]), 12)
        self.assertEqual(len(payload["preview_top"]), 5)
        self.assertEqual(len(payload["preview_bottom"]), 5)
        self.assertTrue(str(payload["preview_items"][0]).startswith("Item 1 x1"))
        self.assertTrue(str(payload["preview_items"][-1]).startswith("Item 12 x12"))
        self.assertTrue(str(payload["preview_top"][0]).startswith("Item 1 x1"))
        self.assertTrue(str(payload["preview_bottom"][-1]).startswith("Item 12 x12"))

    def test_sync_wait_emit_cadence_uses_cooldown_and_context_change(self) -> None:
        rpc = _RpcAsyncStub()
        settings = RuntimeSettings.from_preset("safe-fast")
        service = ItemJournalServiceAsync(
            rpc=rpc,  # type: ignore[arg-type]
            settings=settings,
            logger=logging.getLogger("test-sync-wait-cadence"),
            run_control=RunControl(),
        )

        context_a = "ctx-a"
        context_b = "ctx-b"

        self.assertTrue(service._should_emit_sync_wait(slow_level="info", context_fingerprint=context_a, now_monotonic=10.0))
        self.assertFalse(service._should_emit_sync_wait(slow_level="info", context_fingerprint=context_a, now_monotonic=20.0))
        self.assertTrue(service._should_emit_sync_wait(slow_level="info", context_fingerprint=context_a, now_monotonic=41.0))
        self.assertTrue(service._should_emit_sync_wait(slow_level="warn", context_fingerprint=context_a, now_monotonic=45.0))
        self.assertFalse(service._should_emit_sync_wait(slow_level="warn", context_fingerprint=context_a, now_monotonic=90.0))
        self.assertTrue(service._should_emit_sync_wait(slow_level="warn", context_fingerprint=context_a, now_monotonic=106.0))
        self.assertTrue(service._should_emit_sync_wait(slow_level="warn", context_fingerprint=context_b, now_monotonic=120.0))


if __name__ == "__main__":
    unittest.main()
