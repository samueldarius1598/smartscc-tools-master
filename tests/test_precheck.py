import asyncio
import logging
import unittest
from datetime import date

from smartscc_tools.features.item_journal.config import RuntimeSettings
from smartscc_tools.features.item_journal.services.date_ops import LockDateFetchResult
from smartscc_tools.features.item_journal.services.upload import GlobalPrecheckError, ItemJournalServiceAsync
from smartscc_tools.features.item_journal.workbook import ItemJournalRow


class _DummyRpc:
    pass


class _DummyItemJournalServiceAsync(ItemJournalServiceAsync):
    def __init__(self):
        super().__init__(
            rpc=_DummyRpc(),  # type: ignore[arg-type]
            settings=RuntimeSettings(),
            logger=logging.getLogger("test-precheck-async"),
        )

    async def _validate_locations_precheck(self, active_rows, company_id):  # type: ignore[override]
        _ = (active_rows, company_id)
        return [], {}

    async def _validate_products_precheck(self, active_rows, company_id):  # type: ignore[override]
        _ = (active_rows, company_id)
        return [], {}


class PrecheckTest(unittest.TestCase):
    def _build_row(self, row_number: int, company_id: int, date_text: str) -> ItemJournalRow:
        return ItemJournalRow(
            row_number=row_number,
            date_done_raw=date_text,
            company_id=company_id,
            company_name="PT Test",
            op_type="Internal Transfer",
            src_loc="WH/Stock",
            dest_loc="WH/Output",
            prod_id_text="100",
            prod_key="",
            qty=1.0,
            uom="Units",
            result="",
            stj="",
            error="",
        )

    def test_precheck_fail_multi_company(self) -> None:
        service = _DummyItemJournalServiceAsync()

        async def _lock_dates(_ids):
            return LockDateFetchResult(lock_date_map={}, status_map={})

        service.lock_date_service.fetch_lock_dates = _lock_dates  # type: ignore[method-assign]
        rows = [self._build_row(2, 1, "2026-03-01"), self._build_row(3, 2, "2026-03-01")]
        with self.assertRaises(GlobalPrecheckError) as ctx:
            asyncio.run(service._run_global_precheck(rows))
        self.assertIn("harus 1 unik", str(ctx.exception))

    def test_precheck_fail_multi_date(self) -> None:
        service = _DummyItemJournalServiceAsync()

        async def _lock_dates(_ids):
            return LockDateFetchResult(lock_date_map={1: date(2026, 2, 1)}, status_map={1: "2026-02-01"})

        service.lock_date_service.fetch_lock_dates = _lock_dates  # type: ignore[method-assign]
        rows = [self._build_row(2, 1, "2026-03-01"), self._build_row(3, 1, "2026-03-02")]
        with self.assertRaises(GlobalPrecheckError) as ctx:
            asyncio.run(service._run_global_precheck(rows))
        self.assertIn("Tanggal kolom A harus 1 unik", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
