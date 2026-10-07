# Balance Cycle Pembelian
_Last updated: 2026-03-26_

Use this reference when changing purchase-cycle balance logic or dashboard output in Smart's CC Tools Master.

## Required Doc Sync

If the change affects Balance Cycle Pembelian logic in any of these files, update `docs/balance_cycle_pembelian.md` in the same change:

- `smartscc_tools/features/svl_fix_je/svl_fix_je_balance_cycle.py`
- `smartscc_tools/modules/svl_fix_je_dashboard_page.py`
- `smartscc_tools/features/svl_fix_je/models.py`
- `smartscc_tools/features/svl_fix_je/config.py`

## Summary

### Cycle Definition

- Anchor: one `stock.picking` record such as GR or LHPK
- Merge rule: pickings that share the same vendor bill are merged through union-find

### Transactions Collected Per Cycle

1. STJ via SVL to `stock_move_id`
2. Bill via PO relation or `invoice_origin`
3. Payment via reconciliation to the bill
4. Bank JE via `matching_number` to the payment, or directly to the bill
5. Expansion moves via BFS on `matching_number`

### Processing Pipeline

1. Data collection over the picking -> PO -> bill -> payment -> bank chain
2. Bill totaling by summing credit `payment_term` lines for proportional allocation
3. Cycle merging through union-find
4. Account balance aggregation with proportional weighting for shared payment or bank entries
5. Per-item breakdown using direct product lines plus SVL-weighted distribution for shared lines
6. Account status classification into `balanced`, `acceptable`, `problem`, or `info`
7. Pattern detection for Balance Cycle Pembelian patterns
8. Cycle status classification into `problem`, `partial`, or `healthy`

## Config Defaults

- `pcb_problem_codes = "2103006,1108099"` for Suspensed Payable and Clearing
- `pcb_info_codes = "11120003"` for COGS Variance

## Source Of Truth

This file is a repo-local reminder. The detailed business explanation belongs in `docs/balance_cycle_pembelian.md`.
