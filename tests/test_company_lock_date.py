import logging
import unittest
from datetime import date

from smartscc_tools.features.item_journal.services.date_ops import LockDateServiceAsync
from smartscc_tools.features.svl_fix_je.dashboard_repair_service import (
    RepairOperationError, SvlDashboardRepairServiceAsync,
)


class CompanyLockDateTest(unittest.IsolatedAsyncioTestCase):
    class Rpc:
        def __init__(self):
            self.company = {"id": 1, "fiscalyear_lock_date": "2026-08-31",
                            "user_fiscalyear_lock_date": "2026-08-31",
                            "hard_lock_date": False, "user_hard_lock_date": "1-01-01"}
            self.read_calls = []

        async def fields_get(self, model, **kwargs):
            assert model == "res.company"
            return {f: {"type": "date"} for f in self.company if "lock_date" in f}

        async def read(self, model, ids, **kwargs):
            assert model == "res.company"
            assert kwargs["context"]["allowed_company_ids"] == ids
            self.read_calls.append(ids)
            return [self.company.copy()]

        async def search_read(self, *args, **kwargs):
            raise AssertionError("Transient wizard must never supply an accounting lock date")

    async def test_company_lock_ignores_other_company_wizard_and_allows_september(self):
        rpc = self.Rpc()
        service = SvlDashboardRepairServiceAsync(rpc=rpc, logger=logging.getLogger(__name__))
        await service._ensure_postable_date(company_id=1, target_date=date(2026, 9, 1))
        self.assertEqual(rpc.read_calls, [[1]])
        with self.assertRaises(RepairOperationError) as error:
            await service._ensure_postable_date(company_id=1, target_date=date(2026, 8, 31))
        self.assertEqual(error.exception.error_kind, "lock_date")

    async def test_hard_or_user_lock_is_not_bypassed(self):
        for field in ("hard_lock_date", "user_hard_lock_date", "user_fiscalyear_lock_date"):
            rpc = self.Rpc()
            rpc.company[field] = "2026-09-30"
            result = await LockDateServiceAsync(rpc, logging.getLogger(__name__)).get_close_acc_date_by_company_id(1)
            self.assertEqual(result, (date(2026, 9, 30), ""))

    async def test_unreadable_company_fails_closed(self):
        rpc = self.Rpc()
        async def fail(*args, **kwargs):
            raise RuntimeError("access denied")
        rpc.read = fail
        service = SvlDashboardRepairServiceAsync(rpc=rpc, logger=logging.getLogger(__name__))
        with self.assertRaises(RepairOperationError) as error:
            await service._ensure_postable_date(company_id=1, target_date=date(2026, 9, 1))
        self.assertEqual(error.exception.error_kind, "lock_date_lookup_failed")

    async def test_missing_mismatched_or_invalid_company_lock_fails_closed(self):
        for change in ({"id": 2}, {"fiscalyear_lock_date": "invalid"}):
            rpc = self.Rpc()
            rpc.company.update(change)
            result, error = await LockDateServiceAsync(rpc, logging.getLogger(__name__)).get_close_acc_date_by_company_id(1)
            self.assertIsNone(result)
            self.assertTrue(error)

    async def test_blank_and_year_one_sentinels_mean_no_lock(self):
        rpc = self.Rpc()
        rpc.company["fiscalyear_lock_date"] = False
        rpc.company["user_fiscalyear_lock_date"] = "0001-01-01"
        result = await LockDateServiceAsync(rpc, logging.getLogger(__name__)).get_close_acc_date_by_company_id(1)
        self.assertEqual(result, (None, ""))
