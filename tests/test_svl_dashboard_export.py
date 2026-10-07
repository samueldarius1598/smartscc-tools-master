import tempfile
import unittest
from pathlib import Path

from smartscc_tools.features.svl_fix_je.dashboard_export import export_dashboard_html, snapshot_to_mapping
from smartscc_tools.features.svl_fix_je.models import (
    SvlDashboardCompanySummary,
    SvlDashboardInventoryCoaRow,
    SvlDashboardItem,
    SvlDashboardJournalRecord,
    SvlDashboardPayableLine,
    SvlDashboardSnapshot,
)


class SvlDashboardExportTest(unittest.TestCase):
    def test_snapshot_mapping_keeps_item_kind_for_synthetic_item(self) -> None:
        snapshot = SvlDashboardSnapshot(
            database="hwgroup_erp",
            company_id=1,
            company_name="Alpha Company",
            generated_at="2026-03-17T10:00:00",
            period="2026-01-01 - 2026-01-31",
            company_summary=SvlDashboardCompanySummary(
                total_svl_value=200.0,
                total_svl_qty=5.0,
                inventory_bs_total=180.0,
                difference=20.0,
                problematic_items_total_value=-25.0,
                unmapped_difference=45.0,
                coa_rows=[SvlDashboardInventoryCoaRow(code="114001", name="Persediaan Barang", balance=180.0)],
            ),
            items=[
                SvlDashboardItem(
                    pid=-1,
                    code="UNASSIGNED-JNL",
                    name="Journal Valuation Tanpa Product",
                    categ="VALUATION AML",
                    cost_method="",
                    standard_price=0.0,
                    svl_value=0.0,
                    bs_value=25.0,
                    difference=-25.0,
                    position="DEBIT",
                    svl_orphan_count=0,
                    svl_orphan_value=0.0,
                    jnl_orphan_count=1,
                    jnl_orphan_value=25.0,
                    jnl_records=[
                        SvlDashboardJournalRecord(
                            record_id=1,
                            product_id=0,
                            date="2026-01-09",
                            debit=25.0,
                            credit=0.0,
                            net=25.0,
                            journal_entry="STJ/2026/02/1212",
                            reference="Unassigned Valuation",
                            has_svl=False,
                        )
                    ],
                    item_kind="unassigned_journal",
                )
            ],
        )

        payload = snapshot_to_mapping(snapshot)

        self.assertEqual(payload["items"][0]["item_kind"], "unassigned_journal")
        self.assertEqual(payload["company_summary"]["coa_rows"][0]["code"], "114001")
        self.assertEqual(payload["company_summary"]["unmapped_difference"], 45.0)

    def test_export_html_renders_synthetic_item_notice(self) -> None:
        snapshot = SvlDashboardSnapshot(
            database="hwgroup_erp",
            company_id=1,
            company_name="Alpha Company",
            generated_at="2026-03-17T10:00:00",
            period="2026-01-01 - 2026-01-31",
            company_summary=SvlDashboardCompanySummary(
                total_svl_value=200.0,
                total_svl_qty=5.0,
                inventory_bs_total=180.0,
                difference=20.0,
                problematic_items_total_value=-25.0,
                unmapped_difference=45.0,
                coa_rows=[SvlDashboardInventoryCoaRow(code="114001", name="Persediaan Barang", balance=180.0)],
            ),
            items=[
                SvlDashboardItem(
                    pid=-1,
                    code="UNASSIGNED-JNL",
                    name="Journal Valuation Tanpa Product",
                    categ="VALUATION AML",
                    cost_method="",
                    standard_price=0.0,
                    svl_value=0.0,
                    bs_value=25.0,
                    difference=-25.0,
                    position="DEBIT",
                    svl_orphan_count=0,
                    svl_orphan_value=0.0,
                    jnl_orphan_count=1,
                    jnl_orphan_value=25.0,
                    jnl_records=[],
                    item_kind="unassigned_journal",
                )
            ],
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            path = export_dashboard_html(snapshot, str(Path(tmp_dir) / "dashboard.html"))
            html = path.read_text(encoding="utf-8")

        self.assertIn("UNASSIGNED-JNL", html)
        self.assertIn("Ringkasan Company", html)
        self.assertIn("groupedItems", html)
        self.assertIn("Belum Terpetakan", html)
        self.assertIn("Qty:", html)



if __name__ == "__main__":
    unittest.main()
