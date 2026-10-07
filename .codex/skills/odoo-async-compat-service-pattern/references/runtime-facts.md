# Runtime Facts

These are stronger project facts for this repository than the broader Odoo 18 defaults.

## Architecture

- This repository is a Windows-first Tkinter desktop shell with reusable `ModuleBase` modules.
- Odoo I/O is async and belongs in service or gateway layers.
- Shared app state belongs in global settings; per-module state belongs in `module_settings`.
- Baseline verification is fake-client `unittest`; live Odoo checks are targeted follow-up only.

## Stock and Quantity Facts

- Prefer `stock.move.product_qty` as the main move quantity field in this repo, with fallback to `product_uom_qty`.
- Treat `stock.move.line.qty_done` as done quantity.
- In this project, `stock.move.line.quantity` can be the display or operative per-line quantity for split move lines even when `qty_done` differs.
- Avoid assuming `stock.move.line.product_uom_qty` exists in the handled environments.

## Date Facts

- Repo date sync logic treats `stock.picking.date_done`, `stock.picking.scheduled_date`, `stock.move.date`, `stock.move.line.date`, `stock.valuation.layer.accounting_date`, and `account.move.date` as the main cross-model date surface.
- For `stock.valuation.layer`, prefer `accounting_date`, then `date`, then `create_date` for read/display fallback when needed.
- For STJ date edits, journal mapping should be resolved before stock writes when the operation explicitly requests journal date changes.

## Journal Facts

- The validated journal resolution order in this codebase is:
  1. `stock.picking.account_move_ids`
  2. `stock.move.account_move_ids`
  3. `account.move.stock_move_id`
  4. `stock.valuation.layer.account_move_id`
  5. legacy `account.move.ref`
- Split STJ handling is expected; do not assume one picking maps to only one journal.
- Accounting lock date is a hard stop in this repo.
- Outside the lock period, posted STJ date edits are expected to use `draft -> write -> post`.

## Preference Lock-ins

- Keep repo Odoo work service-layer first and compatibility-first.
- When the user confirms a runtime fact from this repo or environment, treat it as a hard requirement for future repo work.
- Explain blockers with exact technical names, not generic UI phrasing.
