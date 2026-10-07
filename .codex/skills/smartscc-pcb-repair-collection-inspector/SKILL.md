---
name: smartscc-pcb-repair-collection-inspector
description: Use when Codex needs to inspect SVL Fix JE `PCB Repair Collection`, read collection rows or live dialog state, compare collection output vs sidebar/detail, or design and verify PCB holder or planned-line behavior such as Case 9 routing to `1108099`.
---

# SmartSCC PCB Repair Collection Inspector

## Workflow

Prefer collection-aware inspection instead of guessing from service logic alone.

1. Use the GUI inspector with the same effective company, date range, dataset mode, and database profile as the user flow.
2. For PCB collection work, prefer:
   - `python main.py svl-dashboard-gui-inspect --company-id 755 --dataset-mode purchase_cycle_balance --collect-pcb-visible --open-pcb-repair-dialog --dump-json diagnostics\pcb_repair_gui_dump.json`
3. If one cycle matters, add:
   - `--select-picking PIKKP/IN/01509`
4. Read collection data from:
   - `module_state.shell_state.dashboard_page.pcb_repair_collection`
5. Treat these subtrees as the primary truth surfaces:
   - `rows`
   - `rows_by_case`
   - `ui_state`
   - `live_dialog`
6. For row-level diagnosis, report at minimum:
   - `row_key`, `picking_name`, `bill_name`, `pcb_case`
   - `amount`, `planned_lines`, `row_status`, `row_status_message`
   - `resolve_account_code`, `resolve_account_preview`, `account_candidates`
   - `guard_flags`, `guard_messages`
   - `case_evidence`, especially `holder_basis`, `correction_amount`, `downstream_refs`, `inventory_balance`, `hpp_balance`, `suspend_balance`
7. If `live_dialog` exists, also inspect:
   - `selected_row_keys`
   - `summary`
   - `advanced`
   - `trees.rows`, `trees.current_cycle`, `trees.simulated`, `trees.projected_cycle`

## Interpretation Rules

- `svl-dashboard-analyze --dump-json` proves backend cycle/output state, not `PCB Repair Collection` state.
- `pcb_repair_collection.rows` is the source of truth for what the collection currently holds.
- `live_dialog` is the source of truth for what the user actually sees inside the opened collection dialog.
- If collection row data is correct but dialog data differs, treat Tk/dialog wiring as the likely fault surface.
- If collection row data already carries the wrong holder, amount, or planned lines, treat service/row-builder logic as the fault surface.
- For Case 8/9, do not collapse `review_required` meaning into `row_status`. Read both.
- For Case 9, distinguish:
  - bill evidence from vendor bill/refund only
  - downstream or correction evidence from STJ / journal entry / external clearing

## Headless Fallback

- If GUI can run, use the GUI inspector path above.
- If GUI is blocked, be explicit that full `PCB Repair Collection` widget state is not verified.
- In that fallback, inspect:
  - the latest existing `diagnostics\pcb_repair_gui_dump.json` if available, or
  - backend snapshot from `python main.py svl-dashboard-analyze --dump-json ...`
- When using only backend snapshot fallback, describe collection conclusions as inferred, not confirmed from the live dialog.

## Guardrails

- Do not claim collection access from sidebar/raw/detail tables alone.
- Do not turn one-off row keys, move IDs, bill IDs, or current company/date facts into permanent repo memory.
- When a holder/planned-line design depends on accounting intent, separate:
  - what the current row shows
  - what the current service code does
  - what rule change is being proposed

## References

- Read `docs/balance_cycle_pembelian.md` sections `Diagnostik GUI Aktual` and `PCB Repair Collection` when the task touches collection parity or holder/planned-line rules.
