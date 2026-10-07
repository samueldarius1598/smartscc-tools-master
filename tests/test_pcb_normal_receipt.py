import logging
import unittest
from unittest.mock import AsyncMock
from tests.test_svl_dashboard_repair_service import _FakeRepairRpc

from smartscc_tools.features.svl_fix_je.dashboard_service import SvlDashboardServiceAsync as Service
from smartscc_tools.features.svl_fix_je.dashboard_repair_service import SvlDashboardRepairServiceAsync as Repair
from smartscc_tools.features.svl_fix_je.models import SvlDashboardCycleItemRow, SvlDashboardCycleAccountRow, SvlDashboardPurchaseCycle


def fixture(*, expense=False, linked=False, bill_qty=30):
    service = Service(rpc=object(), logger=logging.getLogger("test"))
    account = "6601006" if expense else "2103006"
    item = SvlDashboardCycleItemRow(product_id=1, product_name="Supplies", default_code="TEST",
        has_item_bill=True, bill_move_ids=[2], purchase_line_ids=[3], stock_move_ids=[4],
        gr_quantity=30, bill_quantity=bill_qty, standard_price=25300,
        has_item_stj=linked, stj_move_ids=[8] if linked else [],
        account_rows=[SvlDashboardCycleAccountRow(account_id=5, code=account, name="Bill account",
            account_type="expense" if expense else "liability_current", account_group="expense" if expense else "liability",
            debit=760470, credit=0, net_balance=760470, status="acceptable" if expense else "problem")])
    cycle = SvlDashboardPurchaseCycle(picking_id=6, picking_name="TEST/IN/01", gr_date="2025-12-05",
        partner_name="Vendor", cycle_status="healthy" if expense else "problem", item_rows=[item], bill_move_ids=[2],
        raw_lines=[dict(jenis="BILL", akun_code=account, tipe_akun="expense" if expense else "liability_current",
            kode_item="TEST", nama_item="Supplies", product_id=1)])
    move = dict(id=4, picking_id=[6,"TEST/IN/01"], product_id=[1,"Supplies"], purchase_line_id=[3,"PO"], product_qty=30, product_uom=[7,"Units"])
    layer = dict(id=9, company_id=[1,"Company"], product_id=[1,"Supplies"], stock_move_id=[4,"Move"], quantity=30, value=76047, account_move_id=[8,"JE"] if linked else False)
    trace = dict(stock_move_rows_by_id={4:move}, svl_rows_by_stock_move_id={4:[layer]})
    product = {1:dict(default_code="TEST", name="Supplies", valuation_account_code="1105006", valuation_account_name="Inventory",
        expense_account_code="6601006", expense_account_name="Cleaning", standard_price=25300)}
    info = {5:dict(code=account, name="Bill account", account_type="expense" if expense else "liability_current")}
    lines = {2:[dict(id=10, move_id=[2,"BILL"], account_id=[5,"Bill account"], product_id=[1,"Supplies"], purchase_line_id=[3,"PO"], quantity=bill_qty, balance=760470, price_unit=25349, product_uom_id=[7,"Units"])]}
    service._populate_pcb_receipt_recovery_evidence_for_cycle(cycle=cycle, trace=trace)
    service._apply_pcb_item_classifier(cycle=cycle, product_info_map=product, lines_by_move=lines, account_info_map=info, cycle_bill_move_ids=[2])
    kwargs=dict(pcb_case="case6" if expense else "case5", cycle_status=cycle.cycle_status, picking_id=6, picking_name=cycle.picking_name,
        gr_date=cycle.gr_date, partner_name="Vendor", po_names=["PO"], bill_move_ids=[2], item_rows=[item], raw_lines=cycle.raw_lines,
        lines_by_move=lines, account_info_map=info, move_info_map={2:dict(name="BILL", partner_id=[11,"Vendor"])},
        product_info_map=product, bill_rows_by_id={2:dict(name="BILL")}, purchase_line_product_map={3:1}, purchase_line_po_name_map={3:"PO"}, stock_move_rows_by_id={4:move})
    return service, cycle, kwargs, layer


class NormalReceiptTest(unittest.TestCase):
    def test_actual_svl_and_split_expense_without_clearing(self):
        service, cycle, kwargs, _ = fixture()
        row = service._build_pcb_case2_repair_rows(**kwargs)[0]
        self.assertFalse(cycle.item_rows[0].svl_zero_at_gr)
        self.assertEqual(row.repair_basis_amount, 76047)
        self.assertEqual([(l.account_code,l.side,l.amount) for l in row.planned_lines], [
            ("1105006","debit",76047),("2103006","credit",76047),
            ("6601006","debit",682953),("6601006","debit",1470),("2103006","credit",684423)])
        self.assertTrue(row.review_required)
        self.assertFalse(row.review_confirmed)
        self.assertNotIn("1108099",[l.account_code for l in row.planned_lines])

    def test_expense_bill_uses_exact_bill_account_and_promotes_healthy(self):
        service, cycle, kwargs, _ = fixture(expense=True)
        self.assertEqual(cycle.cycle_status,"problem")
        row=service._build_pcb_case2_repair_rows(**kwargs)[0]
        self.assertEqual([(l.account_code,l.side,l.amount) for l in row.planned_lines],[("1105006","debit",76047),("6601006","credit",76047)])

    def test_bill_expense_account_survives_different_category_default(self):
        service, _, kwargs, _=fixture(expense=True)
        kwargs["product_info_map"][1]["expense_account_code"]="6601999"
        row=service._build_pcb_case2_repair_rows(**kwargs)[0]
        self.assertEqual(row.planned_lines[1].account_code,"6601006")

    def test_bank_expense_does_not_override_exact_suspense_bill(self):
        service, cycle, kwargs, _=fixture()
        cycle.item_rows[0].account_rows.append(SvlDashboardCycleAccountRow(account_id=99,code="6109999",name="Admin bank",account_type="expense",account_group="expense",debit=2500,credit=0,net_balance=2500))
        service._apply_pcb_item_classifier(cycle=cycle,product_info_map=kwargs["product_info_map"])
        self.assertEqual(cycle.item_rows[0].primary_case,"case5")
        self.assertEqual(cycle.item_rows[0].bill_hit_role,"suspend")

    def test_partial_bill_is_blocked_and_has_no_executable_plan(self):
        service, cycle, kwargs, _ = fixture(bill_qty=15)
        row=service._build_pcb_case2_repair_rows(**kwargs)[0]
        self.assertEqual(row.planned_lines,[])
        self.assertIn("receipt_recovery_blocked",row.guard_flags)

    def test_completed_receipt_not_offered_again(self):
        service, cycle, kwargs, _ = fixture(linked=True)
        self.assertNotIn(cycle.item_rows[0].primary_case,("case5","case6"))
        self.assertEqual(cycle.item_rows[0].receipt_recovery_evidence["missing_svl_ids"],[])

    def test_unrelated_stj_does_not_replace_missing_receipt_evidence(self):
        service, cycle, kwargs, _=fixture()
        item=cycle.item_rows[0];item.has_item_stj=True;item.stj_move_ids=[80];item.stj_refs=["OTHER/STJ"]
        service._apply_pcb_item_classifier(cycle=cycle,product_info_map=kwargs["product_info_map"])
        self.assertEqual(item.primary_case,"case5")
        self.assertEqual(item.stj_state,"missing_actual_svl")
        row=service._build_pcb_case2_repair_rows(**kwargs)[0]
        self.assertEqual(row.planned_lines[0].amount,76047)

    def test_zero_gap_omitted_and_reverse_cost_deltas_balance(self):
        for bill, value, standard in [(100,100,100),(90,100,110),(110,100,90)]:
            lines=Service._build_pcb_case5_planned_lines(problem_balances_by_code={"2103006":bill},expense_account_code="6601006",expense_account_name="Expense",basis_amount=value,valuation_account_code="1105006",standard_amount=standard)
            self.assertEqual(sum(l.amount*(1 if l.side=="debit" else -1) for l in lines),0)
            self.assertTrue(all(l.amount>0 for l in lines))
            self.assertTrue(all(l.account_code!="1108099" for l in lines))
        self.assertEqual(len(Service._build_pcb_case5_planned_lines(problem_balances_by_code={"2103006":100},expense_account_code="6601006",expense_account_name="Expense",basis_amount=100,valuation_account_code="1105006",standard_amount=100)),2)


class NormalRpc(_FakeRepairRpc):
    def __init__(self, layer, *, expense=False):
        super().__init__()
        self.accounts[0]["code"]="1105006";self.accounts[1]["code"]="6601006"
        self.fields_map["stock.valuation.layer"]={k:{"type":"float"} for k in layer}
        self.fields_map["account.move"]["stock_move_id"]={"type":"many2one"}
        self.svls={9:dict(layer)}
        self.bill=dict(id=10,move_id=[2,"BILL"],company_id=[1,"Company"],product_id=[1,"Supplies"],
            account_id=[202 if expense else 301,"Bill account"],balance=760470,amount_residual=0 if expense else 760470)
        self.link_writes=[]
        self.create_calls=[]

    async def create(self, model, values, **kwargs):
        self.create_calls.append((model,dict(values)))
        return await super().create(model,values,**kwargs)

    async def read(self, model, ids, **kwargs):
        if model=="stock.valuation.layer":
            return [dict(self.svls[i]) for i in ids if i in self.svls]
        if model=="account.move.line":
            return [dict(self.bill)] if ids==[10] else [dict(self.move_line_rows[i]) for i in ids if i in self.move_line_rows]
        return await super().read(model,ids,**kwargs)

    async def write(self, model, ids, values, **kwargs):
        if model=="stock.valuation.layer":
            self.link_writes.append((list(ids),dict(values)))
        return await super().write(model,ids,values,**kwargs)


class NormalReceiptExecuteTest(unittest.IsolatedAsyncioTestCase):
    async def test_post_links_existing_layer_and_retry_reuses_journal(self):
        service, _, kwargs, layer=fixture()
        row=service._build_pcb_case2_repair_rows(**kwargs)[0]
        row.company_id=1;row.partner_id=77;row.review_confirmed=True;row.date="2026-09-01"
        row.reference="Correction: Purchase Cycle Balance: TEST/IN/01 / BILL / TEST"
        rpc=NormalRpc(layer);repair=Repair(rpc=rpc,logger=logging.getLogger("test"))
        result=await repair._execute_pcb_case2_row(row,posting_mode="post")
        self.assertTrue(result.posted)
        self.assertEqual(rpc.svls[9]["account_move_id"][0],result.move_id)
        self.assertEqual(rpc.svls[9]["value"],76047)
        self.assertEqual(rpc.svls[9]["quantity"],30)
        self.assertEqual([l["quantity"] for l in rpc.moves[result.move_id]["line_ids"]],[30,30,0,0,0])
        self.assertFalse(any(model=="stock.valuation.layer" for model,*_ in rpc.create_calls))
        retry=await repair._execute_pcb_case2_row(row,posting_mode="post")
        self.assertEqual(retry.move_id,result.move_id)
        self.assertTrue(retry.existing_move_detected)
        self.assertEqual(sum(model=="account.move" for model,*_ in rpc.create_calls),1)

    async def test_draft_does_not_relink_and_review_gate_prevents_create(self):
        service, _, kwargs, layer=fixture()
        row=service._build_pcb_case2_repair_rows(**kwargs)[0]
        row.company_id=1;row.partner_id=77;row.date="2026-09-01";row.reference="Correction: Purchase Cycle Balance: TEST/IN/01 / BILL / TEST"
        rpc=NormalRpc(layer);repair=Repair(rpc=rpc,logger=logging.getLogger("test"))
        with self.assertRaisesRegex(Exception,"Review|review"):
            await repair._execute_pcb_case2_row(row,posting_mode="draft")
        self.assertEqual(rpc.create_calls,[])
        row.review_confirmed=True
        result=await repair._execute_pcb_case2_row(row,posting_mode="draft")
        self.assertFalse(result.posted)
        self.assertFalse(rpc.svls[9]["account_move_id"])
        self.assertEqual(rpc.link_writes,[])

    async def test_preflight_rejects_already_linked_svl(self):
        service, _, kwargs, layer=fixture()
        row=service._build_pcb_case2_repair_rows(**kwargs)[0];row.company_id=1;row.review_confirmed=True
        layer["account_move_id"]=[99,"Existing JE"]
        repair=Repair(rpc=object(),logger=logging.getLogger("test"))
        repair._fields_get_cached=AsyncMock(return_value={k:{} for k in layer})
        repair.rpc=type("Rpc",(),{"read":AsyncMock(return_value=[layer])})()
        with self.assertRaisesRegex(Exception,"berubah/terhubung"):
            await repair._validate_normal_receipt_recovery(row=row,context={})

    async def test_legacy_clearing_plan_requires_reanalyze(self):
        service, _, kwargs, _=fixture();row=service._build_pcb_case2_repair_rows(**kwargs)[0];row.case_evidence={}
        repair=Repair(rpc=object(),logger=logging.getLogger("test"))
        with self.assertRaisesRegex(Exception,"Re-analyze"):
            await repair._validate_normal_receipt_recovery(row=row,context={})


if __name__=="__main__":
    unittest.main()
