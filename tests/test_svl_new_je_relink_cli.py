"""Offline coverage of headless preview/apply using the real repair service."""
import asyncio
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from dataclasses import asdict
import io
import json
import logging
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from smartscc_tools.features.svl_fix_je import new_je_relink_cli as cli
from smartscc_tools.features.svl_fix_je.dashboard_repair_service import (
    RepairOperationError, SvlDashboardRepairServiceAsync,
)
from tests.test_svl_dashboard_repair_service import _FakeRepairRpc, _build_repair_row


class _Rpc(_FakeRepairRpc):
    def __init__(self):
        super().__init__()
        self.mutations = []
        self.fields_map.update({
            "stock.valuation.layer": {key: {} for key in ("id", "company_id", "product_id", "value", "account_move_id", "stock_move_id", "quantity", "description", "write_date")},
            "stock.move": {key: {} for key in ("id", "company_id", "account_move_ids")},
            "res.company": {"fiscalyear_lock_date": {"type": "date"}},
        })
        self.fields_map["account.move"]["line_ids"] = {"type": "one2many"}
        self.fields_map["account.move"]["stock_move_id"] = {"type": "many2one"}
        self.svls[1002].update(company_id=[1, "Company A"], product_id=[25, "Product"], value=1200.0,
                              stock_move_id=[70, "GR"], quantity=8.0, description="WH/IN/00001 - Product", write_date="2026-07-01 00:00:00")
        self.products = {25: {"id": 25, "name": "Product", "default_code": "P-01"}}
        self.stock_moves = {70: {"id": 70, "company_id": [1, "Company A"], "account_move_ids": []}}

    async def read(self, model, ids, fields=None, **kwargs):
        records = {"stock.valuation.layer": self.svls, "stock.move": self.stock_moves, "product.product": self.products}.get(model)
        if records is not None:
            return [{key: row.get(key, False) for key in fields} if fields else row.copy()
                    for record_id in ids if (row := records.get(record_id)) is not None]
        return await super().read(model, ids, fields=fields, **kwargs)

    async def search_read(self, model, domain, fields=None, **kwargs):
        if model == "stock.valuation.layer":
            return [{key: row.get(key, False) for key in fields} for row in self.svls.values() if self._matches(row, domain)]
        return await super().search_read(model, domain, fields=fields, **kwargs)

    async def create(self, model, values, **kwargs):
        self.mutations.append(("create", model))
        return await super().create(model, values, **kwargs)

    async def write(self, model, ids, values, **kwargs):
        self.mutations.append(("write", model))
        return await super().write(model, ids, values, **kwargs)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


def _row(**overrides):
    overrides.setdefault("posting_mode", "draft")
    return _build_repair_row(svl_id=1002, date="2026-07-02", company_id=1,
                            debit_account_code="114001", credit_account_code="2103006",
                            reference="PREFIX New JE", line_label="PREFIX New JE", **overrides)


class NewJeServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.rpc = _Rpc()
        self.service = SvlDashboardRepairServiceAsync(rpc=self.rpc, logger=logging.getLogger("test-new-je"))

    async def preview(self, **overrides):
        return await self.service.prepare_new_je_relink(_row(**overrides), database="dummy")

    async def test_preview_is_read_only_and_uses_live_svl_value(self):
        plan = await self.preview()
        self.assertEqual(plan.row.amount, 1200.0)
        self.assertEqual(plan.row.item_product_id, 25)
        self.assertIn("WH/IN/00001", plan.row.reference)
        self.assertEqual(self.rpc.mutations, [])

    async def test_apply_creates_balanced_lines_and_links_svl(self):
        result = await self.service.execute_new_je_relink(await self.preview())
        self.assertEqual(result.created_count, 1)
        self.assertEqual(result.error_count, 0)
        move_id = result.results[0].move_id
        self.assertEqual(self.rpc.svls[1002]["account_move_id"][0], move_id)
        lines = self.rpc.moves[move_id]["line_ids"]
        self.assertEqual(sum(line["debit"] - line["credit"] for line in lines), 0)
        self.assertEqual(self.rpc.moves[move_id]["state"], "draft")
        self.assertFalse(result.results[0].posted)

    async def test_post_returns_final_official_number(self):
        self.rpc.assign_name_on_post_only = True
        result = await self.service.execute_new_je_relink(await self.preview(posting_mode="post"))
        self.assertTrue(result.results[0].posted)
        self.assertNotEqual(result.results[0].move_name, "/")

    async def test_linked_empty_header_is_preserved(self):
        self.rpc.svls[1002]["account_move_id"] = [500, "STJ/2026/0500"]
        result = await self.service.execute_new_je_relink(await self.preview())
        self.assertEqual(result.error_count, 0)
        self.assertEqual(self.rpc.moves[500]["line_ids"], [])
        self.assertEqual(self.rpc.moves[500]["state"], "draft")
        self.assertNotEqual(self.rpc.svls[1002]["account_move_id"][0], 500)

    async def test_populated_existing_je_is_rejected(self):
        self.rpc.svls[1002]["account_move_id"] = [501, "STJ/2026/0501"]
        self.rpc.moves[501]["line_ids"] = [88]
        with self.assertRaisesRegex(RepairOperationError, "JE berisi line"):
            await self.preview()
        self.assertEqual(self.rpc.mutations, [])

    async def test_missing_svl_link_does_not_duplicate_stock_je(self):
        self.rpc.stock_moves[70]["account_move_ids"] = [501]
        self.rpc.moves[501]["line_ids"] = [88]
        with self.assertRaisesRegex(RepairOperationError, "JE lain"):
            await self.preview()

    async def test_wrong_company_zero_and_nonfinite_values_are_rejected(self):
        self.rpc.svls[1002]["company_id"] = [2, "Other"]
        with self.assertRaisesRegex(RepairOperationError, "company"):
            await self.preview()
        self.rpc.svls[1002]["company_id"] = [1, "Company A"]
        for value in (0, float("nan"), float("inf")):
            with self.subTest(value=value):
                self.rpc.svls[1002]["value"] = value
                with self.assertRaisesRegex(RepairOperationError, "Nilai SVL"):
                    await self.preview()
        self.assertEqual(self.rpc.mutations, [])

    async def test_source_changed_after_preview_blocks_apply(self):
        approved = await self.preview()
        self.rpc.svls[1002]["value"] = 1500
        with self.assertRaisesRegex(RepairOperationError, "berubah"):
            await self.service.execute_new_je_relink(approved)
        self.assertEqual(self.rpc.mutations, [])

    async def test_lock_date_blocks_both_draft_and_post(self):
        self.rpc.lock_date = "2026-07-31"
        for mode in ("draft", "post"):
            with self.subTest(mode=mode), self.assertRaisesRegex(RepairOperationError, "lock date"):
                await self.preview(posting_mode=mode)
        self.assertEqual(self.rpc.mutations, [])

    async def test_failed_relink_retains_marker_and_blocks_retry(self):
        approved = await self.preview()
        self.rpc.fail_relink = True
        result = await self.service.execute_new_je_relink(approved)
        self.assertEqual(result.error_count, 1)
        self.assertFalse(self.rpc.svls[1002]["account_move_id"])
        with self.assertRaisesRegex(RepairOperationError, "percobaan sebelumnya"):
            await self.service.execute_new_je_relink(approved)
        self.assertEqual(sum(op == "create" for op, _ in self.rpc.mutations), 1)

    async def test_second_apply_cannot_create_duplicate(self):
        approved = await self.preview()
        await self.service.execute_new_je_relink(approved)
        with self.assertRaises(RepairOperationError):
            await self.service.execute_new_je_relink(approved)
        self.assertEqual(sum(op == "create" for op, _ in self.rpc.mutations), 1)

    async def test_zeroed_lines_after_create_are_reported_as_partial_failure(self):
        self.rpc.zero_move_line_amounts_on_create = True
        result = await self.service.execute_new_je_relink(await self.preview())
        self.assertEqual(result.error_count, 1)
        self.assertEqual(result.results[0].error_kind, "verify_failed")
        self.assertGreater(result.results[0].move_id, 0)

    async def test_success_response_without_actual_svl_link_is_rejected(self):
        with patch.object(self.rpc, "write", new=AsyncMock(return_value=True)):
            result = await self.service.execute_new_je_relink(await self.preview())
        self.assertEqual(result.error_count, 1)
        self.assertEqual(result.results[0].error_kind, "verify_failed")


class NewJeCliTests(unittest.TestCase):
    @contextmanager
    def environment(self, rpc, directory, database="dummy", base_url="https://odoo.invalid"):
        store = SimpleNamespace(load=lambda: SimpleNamespace(last_prefix="PREFIX"))
        with patch.object(cli, "GlobalPersistentState", return_value=SimpleNamespace(load=lambda: object())), \
             patch.object(cli, "_SvlFixJeStateStore", return_value=store), \
             patch.object(cli, "build_runtime_connection", return_value=(object(), SimpleNamespace(database=database, base_url=base_url))), \
             patch.object(cli, "AsyncOdooJsonRpcClient", return_value=rpc), \
             patch.object(cli, "configure_logging", return_value=(logging.getLogger("test-cli"), None)), \
             patch.object(cli.tempfile, "gettempdir", return_value=directory), \
             redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            yield

    def test_preview_file_then_apply_without_gui_or_network(self):
        with tempfile.TemporaryDirectory() as directory:
            preview, receipt = Path(directory) / "preview.json", Path(directory) / "result.json"
            rpc = _Rpc()
            with self.environment(rpc, directory):
                code = cli.main(["--database-profile-id", "db_dummy", "--company-id", "1", "--svl-id", "1002",
                                 "--date", "2026-07-02", "--debit-account-code", "114001", "--credit-account-code", "2103006",
                                 "--output-json", str(preview)])
                self.assertEqual(code, 0)
                self.assertEqual(rpc.mutations, [])
                data = json.loads(preview.read_text(encoding="utf-8"))
                self.assertTrue(data["plan"]["row"]["reference"].startswith("PREFIX"))
                self.assertTrue(data["plan"]["row"]["line_label"].startswith("PREFIX"))
                self.assertEqual(cli.main(["--apply", "--preview-json", str(preview), "--output-json", str(receipt)]), 0)
            result = json.loads(receipt.read_text(encoding="utf-8"))
            self.assertEqual(result["status"], "executed")
            self.assertEqual(result["result"]["created_count"], 1)

    def test_apply_rejects_database_drift_and_preserves_existing_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            rpc = _Rpc()
            service = SvlDashboardRepairServiceAsync(rpc=rpc, logger=logging.getLogger("test-cli"))
            plan = asyncio.run(service.prepare_new_je_relink(_row(), database="dummy"))
            preview, receipt = Path(directory) / "preview.json", Path(directory) / "result.json"
            preview.write_text(json.dumps({"schema": "svl-new-je-relink/v1", "status": "preview", "database_profile_id": "db_dummy", "connection_fingerprint": cli.hashlib.sha256(b"https://odoo.invalid").hexdigest(), "plan": asdict(plan)}))
            with self.environment(rpc, directory, database="other"):
                self.assertEqual(cli.main(["--apply", "--preview-json", str(preview), "--output-json", str(receipt)]), 1)
                with self.assertRaises(SystemExit):
                    cli.main(["--apply", "--preview-json", str(preview), "--output-json", str(receipt)])
            self.assertEqual(rpc.mutations, [])

    def test_apply_rejects_other_host_with_same_database_name(self):
        with tempfile.TemporaryDirectory() as directory:
            rpc = _Rpc()
            service = SvlDashboardRepairServiceAsync(rpc=rpc, logger=logging.getLogger("test-cli"))
            plan = asyncio.run(service.prepare_new_je_relink(_row(), database="dummy"))
            preview, receipt = Path(directory) / "preview.json", Path(directory) / "result.json"
            preview.write_text(json.dumps({"schema": "svl-new-je-relink/v1", "status": "preview", "database_profile_id": "db_dummy", "connection_fingerprint": cli.hashlib.sha256(b"https://odoo.invalid").hexdigest(), "plan": asdict(plan)}))
            with self.environment(rpc, directory, base_url="https://other.invalid"):
                self.assertEqual(cli.main(["--apply", "--preview-json", str(preview), "--output-json", str(receipt)]), 1)
            self.assertIn("Koneksi/database", json.loads(receipt.read_text())["message"])
            self.assertEqual(rpc.mutations, [])

    def test_apply_requires_preview_and_rejects_overrides(self):
        for args in (["--apply"], ["--apply", "--preview-json", "unused.json", "--company-id", "1"]):
            with self.subTest(args=args), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                cli.main(args)

    def test_local_lock_rejects_parallel_apply(self):
        with tempfile.TemporaryDirectory() as directory:
            rpc = _Rpc()
            service = SvlDashboardRepairServiceAsync(rpc=rpc, logger=logging.getLogger("test-cli"))
            plan = asyncio.run(service.prepare_new_je_relink(_row(), database="dummy"))
            with patch.object(cli.tempfile, "gettempdir", return_value=directory):
                with cli._apply_lock(plan):
                    with self.assertRaisesRegex(RuntimeError, "sedang berjalan"):
                        with cli._apply_lock(plan):
                            self.fail("Second lock acquired")


if __name__ == "__main__":
    unittest.main()
