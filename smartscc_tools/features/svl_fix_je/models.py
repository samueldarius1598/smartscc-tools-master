"""Typed models for SVL Fix JE flows."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class SvlFixJeExcelRow:
    row_number: int
    svl_id: int
    svl_ref: str = ""
    default_code: str = ""
    qty: float | None = None
    uom: str = ""
    unit_cost: float | None = None
    total_value: float | None = None
    coa_credit: str = ""
    coa_debit: str = ""
    journal_code: str = ""
    je_date: str = ""
    note: str = ""


@dataclass
class SvlFixJeValidatedRow:
    row: SvlFixJeExcelRow
    svl_record: dict[str, Any]
    company_id: int
    company_name: str
    product_id: int
    credit_account_id: int
    debit_account_id: int
    journal_id: int


@dataclass
class SvlFixJeRowResult:
    row_number: int
    svl_id: int | None = None
    svl_ref: str = ""
    total_value: float | None = None
    status: str = ""
    move_id: int | None = None
    error_message: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def to_mapping(self) -> dict[str, Any]:
        return {
            "row_number": self.row_number,
            "svl_id": self.svl_id,
            "svl_ref": self.svl_ref,
            "total_value": self.total_value,
            "status": self.status,
            "move_id": self.move_id,
            "error_message": self.error_message,
            "timestamp": self.timestamp,
        }


@dataclass
class SvlFixJeProgressSnapshot:
    phase: str
    processed: int
    total: int
    current: str
    eta_seconds: int | None
    counts: dict[str, int]
    progress: float


@dataclass
class SvlFixJeRunRequest:
    excel_path: str
    database: str
    ref_prefix: str
    auto_post: bool = False
    max_workers: int = 1
    default_journal_code: str = "STJ"


@dataclass
class SvlFixJeRunSummary:
    mode: str
    database: str
    results: list[SvlFixJeRowResult] = field(default_factory=list)
    validated_rows: list[SvlFixJeValidatedRow] = field(default_factory=list)
    total_rows: int = 0
    total_value: float = 0.0
    created_count: int = 0
    posted_count: int = 0
    execution_errors: int = 0
    validation_errors: int = 0
    canceled_count: int = 0
    stopped: bool = False
    auto_post: bool = False
    max_workers: int = 1
    excel_path: str = ""
    ref_prefix: str = ""

    def sorted_results(self) -> list[SvlFixJeRowResult]:
        return sorted(self.results, key=lambda item: item.row_number)


@dataclass
class SvlDashboardProgressSnapshot:
    phase: str
    processed: int
    total: int
    current: str
    progress: float


@dataclass
class SvlDashboardRepairProgressSnapshot:
    phase: str
    processed: int
    total: int
    current: str
    progress: float
    success_count: int = 0
    error_count: int = 0


@dataclass
class SvlDashboardRequest:
    database: str
    company_id: int
    date_from: str = ""
    date_to: str = ""
    inventory_coa_codes: list[str] = field(default_factory=list)
    dataset_mode: str = "issues"
    include_inventory_accounts: bool = True
    include_non_inventory_accounts: bool = True
    hide_inventory_accounts: bool = False
    pcb_problem_codes: frozenset[str] = field(default_factory=frozenset)
    pcb_info_codes: frozenset[str] = field(default_factory=frozenset)


@dataclass
class SvlDashboardCompany:
    company_id: int
    name: str

    @property
    def label(self) -> str:
        return f"{self.name} (#{self.company_id})"


@dataclass
class SvlDashboardSvlRecord:
    record_id: int
    product_id: int
    date: str
    quantity: float
    unit_cost: float
    value: float
    reference: str
    description: str
    has_journal: bool
    move_id: int = 0
    move_state: str = ""
    journal_ref: str = ""


@dataclass
class SvlDashboardJournalRecord:
    record_id: int
    product_id: int
    date: str
    debit: float
    credit: float
    net: float
    journal_entry: str
    reference: str
    has_svl: bool
    move_id: int = 0
    move_state: str = ""
    account_id: int = 0
    account_code: str = ""
    account_name: str = ""
    line_label: str = ""


@dataclass
class SvlDashboardMergedRecord:
    row_key: str
    row_type: str
    status: str
    match_basis: str
    note: str = ""
    repair_candidate: bool = False
    product_id: int = 0
    svl_id: int = 0
    svl_date: str = ""
    svl_qty: float = 0.0
    svl_unit_cost: float = 0.0
    svl_value: float = 0.0
    svl_reference: str = ""
    move_id: int = 0
    move_name: str = ""
    move_date: str = ""
    move_state: str = ""
    aml_id: int = 0
    aml_date: str = ""
    account_id: int = 0
    account_code: str = ""
    account_name: str = ""
    debit: float = 0.0
    credit: float = 0.0
    net: float = 0.0
    reference: str = ""
    line_label: str = ""


@dataclass
class SvlDashboardPoLine:
    po: str
    price_unit: float
    qty_received: float
    qty_invoiced: float
    value: float
    status: str


@dataclass
class SvlDashboardBillLine:
    bill: str
    po: str
    date: str
    price_unit: float
    quantity: float
    value: float
    move_id: int = 0


@dataclass
class SvlDashboardPayableLine:
    bill: str
    date: str
    account_code: str
    account_name: str
    debit: float
    credit: float
    balance: float
    reference: str
    move_id: int = 0
    payment_state: str = ""
    billed_value: float = 0.0
    residual_value: float = 0.0
    paid_value: float = 0.0
    allocation_ratio: float = 0.0
    source: str = ""
    payment_name: str = ""


# ── Purchase Cycle Balance ─────────────────────────────────────────────────────

@dataclass
class SvlDashboardCycleAccountRow:
    """Net debit/credit of one account within a purchase cycle."""
    account_id: int
    code: str
    name: str
    account_type: str   # e.g. 'asset_current', 'liability_payable'
    account_group: str  # prefix before first '_': 'asset', 'liability', 'expense'
    debit: float
    credit: float
    net_balance: float  # debit - credit
    status: str = ""    # 'balanced', 'acceptable', 'problem'


@dataclass
class SvlDashboardPcbAdjustmentAuditRow:
    move_id: int
    move_name: str = ""
    move_date: str = ""
    move_ref: str = ""
    product_id: int = 0
    item_code: str = ""
    item_name: str = ""
    source_account_code: str = ""
    source_account_name: str = ""
    clearing_account_code: str = ""
    repair_clearing_amount: float = 0.0
    origin_move_id: int = 0
    origin_move_name: str = ""
    origin_basis: str = ""
    origin_product_id: int = 0
    origin_purchase_line_id: int = 0
    origin_stock_move_id: int = 0
    verified_for_case34: bool = False
    matched_basis: str = ""
    ambiguous: bool = False


@dataclass
class SvlDashboardCycleItemRow:
    """Per-item account breakdown within a purchase cycle.

    Direct lines (STJ/bill product lines) are assigned to the matching product.
    Shared lines (payment, bank JE, outstanding clearing) are split evenly across
    all items in the GR, after the cycle-level proportional payment allocation.
    """
    product_id: int
    product_name: str
    default_code: str = ""
    valuation_method: str = "automated"  # "automated" or "manual"
    account_rows: list[SvlDashboardCycleAccountRow] = field(default_factory=list)
    bill_move_ids: list[int] = field(default_factory=list)
    bill_refs: list[str] = field(default_factory=list)
    purchase_line_ids: list[int] = field(default_factory=list)
    has_item_bill: bool = False
    stock_move_ids: list[int] = field(default_factory=list)
    stj_move_ids: list[int] = field(default_factory=list)
    stj_refs: list[str] = field(default_factory=list)
    has_item_stj: bool = False
    correction_stj_move_ids: list[int] = field(default_factory=list)
    correction_stj_refs: list[str] = field(default_factory=list)
    has_stj_evidence: bool = False
    gr_quantity: float = 0.0
    bill_quantity: float = 0.0
    standard_price: float = 0.0
    primary_case: str = ""
    primary_group: str = ""
    secondary_flags: list[str] = field(default_factory=list)
    case_reason: str = ""
    auto_repairable: bool = False
    stj_state: str = ""
    svl_zero_at_gr: bool = False
    bill_hit_role: str = ""
    repair_basis_amount: float = 0.0
    repair_basis_source: str = ""
    external_clearing_amount: float = 0.0
    external_clearing_refs: list[str] = field(default_factory=list)
    external_clearing_basis: str = ""
    external_clearing_verified: bool = False
    verified_audit_clearing_amount: float = 0.0
    eligible_case34: bool = False
    adjustment_audit_rows: list[SvlDashboardPcbAdjustmentAuditRow] = field(default_factory=list)
    case8_evidence: dict[str, Any] = field(default_factory=dict)
    case9_evidence: dict[str, Any] = field(default_factory=dict)
    receipt_recovery_evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class SvlDashboardPcbCase1LinkRow:
    cycle_key: str
    source_key: str
    source_kind: str
    source_id: int
    product_id: int
    item_code: str = ""
    item_name: str = ""
    item_category_name: str = ""
    bill_line_id: int = 0
    bill_move_id: int = 0
    purchase_line_id: int = 0
    stock_move_id: int = 0
    stock_move_ids: list[int] = field(default_factory=list)
    stj_move_ids: list[int] = field(default_factory=list)
    stj_refs: list[str] = field(default_factory=list)
    stj_link_basis: str = ""
    stj_candidate_count: int = 0
    picking_id: int = 0
    picking_name: str = ""
    po_name: str = ""
    bill_name: str = ""
    partner_id: int = 0
    partner_name: str = ""
    payment_move_ids: list[int] = field(default_factory=list)
    bank_move_ids: list[int] = field(default_factory=list)
    suspend_target_aml_ids: list[int] = field(default_factory=list)
    clearing_target_aml_ids: list[int] = field(default_factory=list)
    product_uom_id: int = 0
    quantity: float = 0.0
    currency_id: int = 0
    amount_currency: float = 0.0
    amount_currency_basis: float = 0.0
    analytic_distribution: Any = False
    journal_code: str = ""
    debit_account_code: str = ""
    credit_account_code: str = ""
    debit_amount: float = 0.0
    credit_amount: float = 0.0
    diff_account_code: str = ""
    diff_account_name: str = ""
    diff_side: str = ""
    diff_amount: float = 0.0
    stj_amount: float = 0.0
    bill_amount: float = 0.0
    bill_price_unit: float = 0.0
    gr_price_unit: float = 0.0
    price_gap_value: float = 0.0
    allocated_amount: float = 0.0
    bill_date: str = ""
    gr_date: str = ""


@dataclass
class SvlDashboardPcbRepairPlannedLine:
    role: str
    account_code: str
    amount: float
    side: str
    account_name: str = ""
    line_label: str = ""
    source_balance: float = 0.0


@dataclass
class SvlDashboardPurchaseCycle:
    """One complete purchase cycle, anchored to a GR (stock.picking).

    When multiple pickings share the same vendor bill, they are merged into
    one cycle to prevent bill double/triple-counting.  In that case:
    - picking_id / picking_name  → primary picking (first alphabetically)
    - picking_ids / picking_names → all pickings in the merged group
    """
    picking_id: int
    picking_name: str   # e.g. LHPK/IN/01185, or "LHPK/IN/00460 [+2]" if merged
    gr_date: str
    partner_name: str
    partner_id: int = 0
    purchase_orders: list[str] = field(default_factory=list)
    correction_stj_move_ids: list[int] = field(default_factory=list)
    correction_stj_refs: list[str] = field(default_factory=list)
    stj_refs: list[str] = field(default_factory=list)
    bill_move_ids: list[int] = field(default_factory=list)
    bill_refs: list[str] = field(default_factory=list)
    payment_move_ids: list[int] = field(default_factory=list)
    payment_refs: list[str] = field(default_factory=list)
    bank_move_ids: list[int] = field(default_factory=list)
    bank_refs: list[str] = field(default_factory=list)
    product_ids: list[int] = field(default_factory=list)
    inventory_types: list[str] = field(default_factory=list)
    cycle_status: str = ""          # 'problem', 'partial', 'healthy'
    uom_flag: str = "inline"        # 'mismatch' or 'inline'
    document_classification: str = ""
    document_classification_label: str = ""
    document_classification_reasons: list[str] = field(default_factory=list)
    primary_case: str = ""
    case_counts: dict[str, int] = field(default_factory=dict)
    edge_flags: list[str] = field(default_factory=list)
    mixed_case_summary: str = ""
    has_return_picking: bool = False
    has_refund_bill: bool = False
    partner_is_intercompany: bool = False   # True jika partner.ref_company_ids != [] (internal HW Group)
    partial_group_key: str = ""
    partial_group_label: str = ""
    issue_patterns: list[str] = field(default_factory=list)
    adjustment_warning_text: str = ""
    account_rows: list[SvlDashboardCycleAccountRow] = field(default_factory=list)
    item_rows: list[SvlDashboardCycleItemRow] = field(default_factory=list)
    adjustment_audit_rows: list[SvlDashboardPcbAdjustmentAuditRow] = field(default_factory=list)
    total_debit: float = 0.0
    total_credit: float = 0.0
    problem_account_count: int = 0
    has_info_accounts: bool = False
    info_account_count: int = 0
    picking_ids: list[int] = field(default_factory=list)    # all picking IDs in merged group
    picking_names: list[str] = field(default_factory=list)  # all picking names in merged group
    case1_link_rows: list[SvlDashboardPcbCase1LinkRow] = field(default_factory=list)
    case2_repair_rows: list["SvlDashboardPcbCase2RepairRow"] = field(default_factory=list)
    # Raw AML lines (data mentah before aggregation) — for the per-cycle list view in dashboard.
    # Each entry: {kode_transaksi, tanggal, jenis, akun_code, akun_name, komunikasi, debit, kredit, saldo, matching}
    raw_lines: list[dict] = field(default_factory=list)


@dataclass
class SvlDashboardInventoryCoaRow:
    code: str
    name: str
    balance: float


@dataclass
class SvlDashboardCompanySummary:
    total_svl_value: float = 0.0
    total_svl_qty: float = 0.0
    inventory_bs_total: float = 0.0
    difference: float = 0.0
    problematic_items_total_value: float = 0.0
    unmapped_difference: float = 0.0
    coa_rows: list[SvlDashboardInventoryCoaRow] = field(default_factory=list)
    missing_codes: list[str] = field(default_factory=list)


@dataclass
class SvlDashboardRepairAccountCandidate:
    code: str
    name: str
    source: str
    role: str = ""
    account_id: int = 0
    field_name: str = ""

    @property
    def display_label(self) -> str:
        base_name = self.name or self.code
        source_text = f" [{self.source}]" if self.source else ""
        return f"{self.code} - {base_name}{source_text}"


@dataclass
class SvlDashboardItem:
    pid: int
    code: str
    name: str
    categ: str
    cost_method: str
    standard_price: float
    svl_value: float
    bs_value: float
    difference: float
    position: str
    svl_orphan_count: int
    svl_orphan_value: float
    jnl_orphan_count: int
    jnl_orphan_value: float
    linked_empty_journal_count: int = 0
    linked_empty_journal_value: float = 0.0
    svl_record_count: int = 0
    jnl_record_count: int = 0
    svl_records: list[SvlDashboardSvlRecord] = field(default_factory=list)
    jnl_records: list[SvlDashboardJournalRecord] = field(default_factory=list)
    merged_records: list[SvlDashboardMergedRecord] = field(default_factory=list)
    repair_account_candidates: list[SvlDashboardRepairAccountCandidate] = field(default_factory=list)
    po_line_count: int = 0
    bill_line_count: int = 0
    po_lines: list[SvlDashboardPoLine] = field(default_factory=list)
    bill_lines: list[SvlDashboardBillLine] = field(default_factory=list)
    total_po_value: float = 0.0
    total_bill_value: float = 0.0
    payable_line_count: int = 0
    total_payable_value: float = 0.0
    payable_lines: list[SvlDashboardPayableLine] = field(default_factory=list)
    total_paid_value: float = 0.0
    total_unassigned_bill_remainder: float = 0.0
    payment_status: str = ""
    unassigned_bill_lines: list[SvlDashboardPayableLine] = field(default_factory=list)
    item_kind: str = "product"
    automated_valuation: bool = True
    warnings: list[str] = field(default_factory=list)


@dataclass
class SvlDashboardItemDetail:
    pid: int
    item_kind: str = "product"
    po_line_count: int = 0
    bill_line_count: int = 0
    total_po_value: float = 0.0
    total_bill_value: float = 0.0
    svl_records: list[SvlDashboardSvlRecord] = field(default_factory=list)
    jnl_records: list[SvlDashboardJournalRecord] = field(default_factory=list)
    po_lines: list[SvlDashboardPoLine] = field(default_factory=list)
    bill_lines: list[SvlDashboardBillLine] = field(default_factory=list)
    payable_line_count: int = 0
    total_payable_value: float = 0.0
    payable_lines: list[SvlDashboardPayableLine] = field(default_factory=list)
    total_paid_value: float = 0.0
    total_unassigned_bill_remainder: float = 0.0
    payment_status: str = ""
    unassigned_bill_lines: list[SvlDashboardPayableLine] = field(default_factory=list)


@dataclass
class SvlDashboardRepairRow:
    row_key: str
    company_id: int
    item_product_id: int
    item_code: str
    item_name: str
    amount: float
    date: str
    reference: str
    line_label: str
    company_name: str = ""
    journal_code: str = ""
    debit_account_code: str = ""
    credit_account_code: str = ""
    svl_id: int = 0
    move_id: int = 0
    move_name: str = ""
    move_state: str = ""
    target_mode: str = "new_and_relink"
    posting_mode: str = "draft"
    signed_amount: float = 0.0
    base_reference: str = ""
    base_line_label: str = ""
    svl_reference: str = ""
    repair_source_kind: str = ""
    repair_source_label: str = ""
    account_candidates: list[SvlDashboardRepairAccountCandidate] = field(default_factory=list)


@dataclass
class SvlDashboardPcbCase1RepairRow:
    row_key: str
    company_id: int
    amount: float
    date: str
    reference: str
    line_label: str
    cycle_key: str = ""
    company_name: str = ""
    journal_code: str = ""
    debit_account_code: str = ""
    credit_account_code: str = ""
    debit_amount: float = 0.0
    credit_amount: float = 0.0
    diff_account_code: str = ""
    diff_account_name: str = ""
    diff_side: str = ""
    diff_amount: float = 0.0
    source_kind: str = ""
    source_id: int = 0
    source_label: str = ""
    product_id: int = 0
    item_code: str = ""
    item_name: str = ""
    item_category_name: str = ""
    bill_line_id: int = 0
    bill_move_id: int = 0
    purchase_line_id: int = 0
    stock_move_id: int = 0
    stock_move_ids: list[int] = field(default_factory=list)
    stj_move_ids: list[int] = field(default_factory=list)
    stj_refs: list[str] = field(default_factory=list)
    stj_link_basis: str = ""
    stj_candidate_count: int = 0
    picking_id: int = 0
    picking_name: str = ""
    po_name: str = ""
    bill_name: str = ""
    partner_id: int = 0
    partner_name: str = ""
    payment_move_ids: list[int] = field(default_factory=list)
    bank_move_ids: list[int] = field(default_factory=list)
    suspend_target_aml_ids: list[int] = field(default_factory=list)
    clearing_target_aml_ids: list[int] = field(default_factory=list)
    product_uom_id: int = 0
    quantity: float = 0.0
    currency_id: int = 0
    amount_currency: float = 0.0
    amount_currency_basis: float = 0.0
    analytic_distribution: Any = False
    bill_price_unit: float = 0.0
    gr_price_unit: float = 0.0
    price_gap_value: float = 0.0
    allocated_amount: float = 0.0
    result_move_id: int = 0
    result_move_name: str = ""
    result_posted: bool = False


@dataclass
class SvlDashboardPcbCase1RepairRequest:
    database: str
    rows: list[SvlDashboardPcbCase1RepairRow] = field(default_factory=list)
    posting_mode: str = "draft"
    max_workers: int = 1


@dataclass
class SvlDashboardPcbCase1RepairRowResult:
    row_key: str
    status: str
    message: str = ""
    move_id: int = 0
    move_name: str = ""
    posted: bool = False
    existing_move_detected: bool = False
    reconcile_attempted: bool = False
    reconcile_performed: bool = False
    reconcile_skipped: bool = False
    reconcile_message: str = ""
    reconcile_error_kind: str = ""
    error_kind: str = ""


@dataclass
class SvlDashboardPcbCase1RepairBatchResult:
    database: str
    results: list[SvlDashboardPcbCase1RepairRowResult] = field(default_factory=list)
    created_count: int = 0
    posted_count: int = 0
    existing_count: int = 0
    reconciled_count: int = 0
    reconcile_skipped_count: int = 0
    error_count: int = 0
    warnings: list[str] = field(default_factory=list)


@dataclass
class SvlDashboardPcbCase2RepairRow:
    row_key: str
    company_id: int
    amount: float
    date: str
    reference: str
    line_label: str
    cycle_key: str = ""
    company_name: str = ""
    journal_code: str = ""
    pcb_case: str = "case2"
    pcb_case_label: str = ""
    product_id: int = 0
    item_code: str = ""
    item_name: str = ""
    item_category_name: str = ""
    bill_line_id: int = 0
    purchase_line_id: int = 0
    stock_move_id: int = 0
    stock_move_ids: list[int] = field(default_factory=list)
    stj_move_ids: list[int] = field(default_factory=list)
    stj_refs: list[str] = field(default_factory=list)
    picking_id: int = 0
    picking_name: str = ""
    po_name: str = ""
    bill_move_id: int = 0
    bill_name: str = ""
    partner_id: int = 0
    partner_name: str = ""
    payment_move_ids: list[int] = field(default_factory=list)
    bank_move_ids: list[int] = field(default_factory=list)
    suspend_target_aml_ids: list[int] = field(default_factory=list)
    clearing_target_aml_ids: list[int] = field(default_factory=list)
    product_uom_id: int = 0
    quantity: float = 0.0
    currency_id: int = 0
    amount_currency: float = 0.0
    amount_currency_basis: float = 0.0
    analytic_distribution: Any = False
    bill_price_unit: float = 0.0
    gr_price_unit: float = 0.0
    price_gap_value: float = 0.0
    allocated_amount: float = 0.0
    suspend_account_code: str = ""
    inventory_account_code: str = ""
    expense_account_code: str = ""
    resolve_account_code: str = ""
    resolve_account_preview: str = ""
    repair_basis_amount: float = 0.0
    repair_basis_source: str = ""
    standard_price: float = 0.0
    problem_balances_by_code: dict[str, float] = field(default_factory=dict)
    hpp_balances_by_code: dict[str, float] = field(default_factory=dict)
    suspend_balance: float = 0.0
    hpp_balance: float = 0.0
    inventory_balance: float = 0.0
    cogs_variance_balance: float = 0.0
    selisih_hpp_amount: float = 0.0
    external_clearing_amount: float = 0.0
    external_clearing_refs: list[str] = field(default_factory=list)
    external_clearing_basis: str = ""
    external_clearing_verified: bool = False
    coefficient_variance: float = 0.0
    bank_balances_by_code: dict[str, float] = field(default_factory=dict)
    bank_account_codes: list[str] = field(default_factory=list)
    guard_flags: list[str] = field(default_factory=list)
    guard_messages: list[str] = field(default_factory=list)
    review_required: bool = False
    review_confirmed: bool = False
    review_reason: str = ""
    planned_lines: list[SvlDashboardPcbRepairPlannedLine] = field(default_factory=list)
    case_evidence: dict[str, Any] = field(default_factory=dict)
    result_move_id: int = 0
    result_move_name: str = ""
    result_posted: bool = False


@dataclass
class SvlDashboardPcbCase2RepairRequest:
    database: str
    rows: list[SvlDashboardPcbCase2RepairRow] = field(default_factory=list)
    posting_mode: str = "draft"
    max_workers: int = 1


@dataclass
class SvlDashboardPcbCase2RepairRowResult:
    row_key: str
    status: str
    message: str = ""
    move_id: int = 0
    move_name: str = ""
    posted: bool = False
    existing_move_detected: bool = False
    error_kind: str = ""


@dataclass
class SvlDashboardPcbCase2RepairBatchResult:
    database: str
    results: list[SvlDashboardPcbCase2RepairRowResult] = field(default_factory=list)
    created_count: int = 0
    posted_count: int = 0
    existing_count: int = 0
    error_count: int = 0
    warnings: list[str] = field(default_factory=list)


@dataclass
class SvlDashboardRepairRequest:
    database: str
    max_workers: int = 1
    rows: list[SvlDashboardRepairRow] = field(default_factory=list)
    on_progress: Callable[["SvlDashboardRepairProgressSnapshot"], Any] | None = None


@dataclass
class SvlDashboardRepairRowResult:
    row_key: str
    status: str
    company_id: int = 0
    company_name: str = ""
    item_code: str = ""
    item_name: str = ""
    amount: float = 0.0
    reference: str = ""
    message: str = ""
    selected_target_mode: str = ""
    effective_date: str = ""
    error_kind: str = ""
    move_id: int = 0
    move_name: str = ""
    posted: bool = False
    relinked_svl_id: int = 0
    old_move_action: str = ""
    old_move_id: int = 0
    old_move_name: str = ""
    svl_reference: str = ""
    repair_source_kind: str = ""
    repair_source_label: str = ""
    debit_account_code: str = ""
    debit_account_name: str = ""
    credit_account_code: str = ""
    credit_account_name: str = ""


@dataclass
class SvlDashboardRepairBatchResult:
    database: str
    results: list[SvlDashboardRepairRowResult] = field(default_factory=list)
    created_count: int = 0
    posted_count: int = 0
    error_count: int = 0
    warnings: list[str] = field(default_factory=list)


@dataclass
class SvlDashboardSnapshot:
    database: str
    company_id: int
    company_name: str
    generated_at: str
    period: str
    dataset_mode: str = "issues"
    valuation_account_ids: list[int] = field(default_factory=list)
    company_summary: SvlDashboardCompanySummary = field(default_factory=SvlDashboardCompanySummary)
    warnings: list[str] = field(default_factory=list)
    items: list[SvlDashboardItem] = field(default_factory=list)
    purchase_cycles: list[SvlDashboardPurchaseCycle] = field(default_factory=list)

    @property
    def account_count(self) -> int:
        return len(self.valuation_account_ids)
