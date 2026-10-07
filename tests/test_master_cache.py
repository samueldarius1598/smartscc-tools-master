import logging
import unittest
from types import SimpleNamespace

from smartscc_tools.features.item_journal.config import RuntimeSettings
from smartscc_tools.features.item_journal.services.upload import ItemJournalServiceAsync
from smartscc_tools.features.item_journal.workbook import ItemJournalRow
from smartscc_tools.features.svl_fix_je.dashboard_repair_service import SvlDashboardRepairServiceAsync
from smartscc_tools.features.update_std_cost.service import UpdateStdCostServiceAsync
from smartscc_tools.services.odoo.master_cache import SessionMasterCache


class SessionMasterCacheTest(unittest.TestCase):
    def test_product_cache_requires_field_coverage_and_expires(self) -> None:
        now = [100.0]
        cache = SessionMasterCache(ttl_seconds=10, clock=lambda: now[0])

        cache.set_product_record(
            base_url="https://odoo.local",
            database="hwgroup_erp",
            model="product.product",
            company_id=7,
            row={"id": 101, "default_code": "SKU-001"},
            fields=["id", "default_code"],
        )

        cached = cache.get_product_record(
            base_url="https://odoo.local",
            database="hwgroup_erp",
            model="product.product",
            company_id=7,
            code="SKU-001",
            required_fields=["id"],
        )
        self.assertEqual(cached, {"id": 101, "default_code": "SKU-001"})
        self.assertIsNone(
            cache.get_product_record(
                base_url="https://odoo.local",
                database="hwgroup_erp",
                model="product.product",
                company_id=7,
                code="SKU-001",
                required_fields=["id", "standard_price"],
            )
        )

        cache.set_product_record(
            base_url="https://odoo.local",
            database="hwgroup_erp",
            model="product.product",
            company_id=7,
            row={"default_code": "SKU-001", "standard_price": 15.5},
            fields=["default_code", "standard_price"],
        )
        merged = cache.get_product_record(
            base_url="https://odoo.local",
            database="hwgroup_erp",
            model="product.product",
            company_id=7,
            code="SKU-001",
            required_fields=["id", "standard_price"],
        )
        self.assertEqual(merged, {"id": 101, "default_code": "SKU-001", "standard_price": 15.5})

        now[0] += 11.0
        self.assertIsNone(
            cache.get_product_record(
                base_url="https://odoo.local",
                database="hwgroup_erp",
                model="product.product",
                company_id=7,
                code="SKU-001",
                required_fields=["id"],
            )
        )


class _StdCostRpc:
    def __init__(self) -> None:
        self.config = SimpleNamespace(base_url="https://odoo.local", database="hwgroup_erp")
        self.variant_lookup_calls = 0

    async def search_read(self, model, domain, fields=None, context=None, stage=""):  # noqa: ANN001, ANN201
        _ = (stage,)
        if model != "product.product":
            return []
        if fields == ["id", "default_code"]:
            self.variant_lookup_calls += 1
            company_id = int((context or {}).get("company_id") or 0)
            return [{"id": company_id * 1000 + 1, "default_code": "SKU-001"}]
        return []


class _ItemJournalRpc:
    def __init__(self) -> None:
        self.config = SimpleNamespace(base_url="https://odoo.local", database="hwgroup_erp")
        self.product_search_calls = 0
        self.location_search_calls = 0

    async def fields_get(self, model, attributes=None, context=None, stage=""):  # noqa: ANN001, ANN201
        _ = (attributes, context, stage)
        if model == "product.product":
            return {"detailed_type": {"type": "selection"}}
        return {}

    async def search_read(self, model, domain, fields=None, context=None, limit=None, stage=""):  # noqa: ANN001, ANN201
        _ = (fields, context, limit, stage)
        if model == "product.product":
            self.product_search_calls += 1
            return [
                {
                    "id": 501,
                    "default_code": "SKU-001",
                    "display_name": "Produk Cache",
                    "name": "Produk Cache",
                    "uom_id": [1, "Units"],
                    "company_id": [1, "Company"],
                    "standard_price": 15.0,
                    "detailed_type": "product",
                }
            ]
        if model == "stock.location":
            self.location_search_calls += 1
            return [{"id": 701, "complete_name": "WH/Stock", "name": "Stock", "company_id": [1, "Company"]}]
        return []

    async def read(self, model, ids, fields=None, context=None, stage=""):  # noqa: ANN001, ANN201
        _ = (model, ids, fields, context, stage)
        return [{"company_id": [1, "Company"], "display_name": "WH/Stock"}]


class _RepairRpc:
    def __init__(self) -> None:
        self.config = SimpleNamespace(base_url="https://odoo.local", database="hwgroup_erp")
        self.partner_search_calls = 0

    async def search_read(self, model, domain, fields=None, limit=None, context=None, stage=""):  # noqa: ANN001, ANN201
        _ = (fields, limit, context, stage)
        if model == "res.partner":
            self.partner_search_calls += 1
            return [{"id": 901, "name": "Vendor Alpha"}]
        return []


class MasterCacheIntegrationAsyncTest(unittest.IsolatedAsyncioTestCase):
    async def test_update_std_cost_reuses_product_lookup_across_service_instances(self) -> None:
        cache = SessionMasterCache()
        rpc = _StdCostRpc()
        service_first = UpdateStdCostServiceAsync(
            rpc=rpc,
            logger=logging.getLogger("test.master_cache.std_cost.1"),
            master_cache=cache,
        )
        service_second = UpdateStdCostServiceAsync(
            rpc=rpc,
            logger=logging.getLogger("test.master_cache.std_cost.2"),
            master_cache=cache,
        )

        first = await service_first._fetch_product_ids_by_default_code_with_context(["SKU-001"], company_id=7)
        second = await service_second._fetch_product_ids_by_default_code_with_context(["SKU-001"], company_id=7)

        self.assertEqual(first, {"sku-001": 7001})
        self.assertEqual(second, {"sku-001": 7001})
        self.assertEqual(rpc.variant_lookup_calls, 1)

    async def test_item_journal_reuses_product_cache_across_service_instances(self) -> None:
        cache = SessionMasterCache()
        rpc = _ItemJournalRpc()
        row = ItemJournalRow(
            row_number=2,
            date_done_raw="2026-04-01",
            company_id=1,
            company_name="PT Test",
            op_type="Internal Transfer",
            src_loc="WH/Stock",
            dest_loc="WH/Output",
            prod_id_text="",
            prod_key="SKU-001",
            qty=1.0,
            uom="Units",
            result="",
            stj="",
            error="",
        )
        service_first = ItemJournalServiceAsync(
            rpc=rpc,
            settings=RuntimeSettings(),
            logger=logging.getLogger("test.master_cache.item_journal.1"),
            master_cache=cache,
        )
        service_second = ItemJournalServiceAsync(
            rpc=rpc,
            settings=RuntimeSettings(),
            logger=logging.getLogger("test.master_cache.item_journal.2"),
            master_cache=cache,
        )

        await service_first._prefetch_products([row], company_id=1)
        await service_second._prefetch_products([row], company_id=1)

        self.assertEqual(rpc.product_search_calls, 1)

    async def test_item_journal_reuses_location_cache_across_service_instances(self) -> None:
        cache = SessionMasterCache()
        rpc = _ItemJournalRpc()
        service_first = ItemJournalServiceAsync(
            rpc=rpc,
            settings=RuntimeSettings(),
            logger=logging.getLogger("test.master_cache.location.1"),
            master_cache=cache,
        )
        service_second = ItemJournalServiceAsync(
            rpc=rpc,
            settings=RuntimeSettings(),
            logger=logging.getLogger("test.master_cache.location.2"),
            master_cache=cache,
        )

        first = await service_first._resolve_location_id_cached("WH/Stock", 1)
        second = await service_second._resolve_location_id_cached("WH/Stock", 1)

        self.assertEqual(first, 701)
        self.assertEqual(second, 701)
        self.assertEqual(rpc.location_search_calls, 1)

    async def test_svl_repair_reuses_partner_cache_across_service_instances(self) -> None:
        cache = SessionMasterCache()
        rpc = _RepairRpc()
        service_first = SvlDashboardRepairServiceAsync(
            rpc=rpc,
            logger=logging.getLogger("test.master_cache.repair.1"),
            master_cache=cache,
        )
        service_second = SvlDashboardRepairServiceAsync(
            rpc=rpc,
            logger=logging.getLogger("test.master_cache.repair.2"),
            master_cache=cache,
        )

        first = await service_first._resolve_partner_id(1, "Vendor Alpha")
        second = await service_second._resolve_partner_id(1, "Vendor Alpha")

        self.assertEqual(first, 901)
        self.assertEqual(second, 901)
        self.assertEqual(rpc.partner_search_calls, 1)


if __name__ == "__main__":
    unittest.main()
