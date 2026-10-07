# Curated Live Odoo Schema Notes

This file is for stable, reviewed schema facts that should be reused by future Codex sessions in this repo.

Rules:

- Include the source database/profile, inspection date, model, and scope.
- Keep raw `fields_get` or sample rows in `logs/odoo_inspector/`, not here.
- Do not store one-off transaction numbers, company IDs, temporary mismatch outputs, or active-database facts.
- Mark uncertain observations as inference.

## Entries

### 2026-04-07 - `uom.uom` live schema shape

Source: database `hwgroup_erp`, profile request `Follow Global Default`, user-visible inspection via `tools/odoo_inspector`, read-only scope. Raw artefacts are kept under `logs/odoo_inspector/`.

Confirmed by `fields_get`:

- `uom.uom` is accessible through `fields_get`, but this user cannot read `ir.model` metadata; inspector tools must tolerate `ir.model` access errors and continue from `fields_get`.
- Confirmed field count during this inspection: 20.
- Core fields include `id`, `name`, `display_name`, `active`, `relative_factor`, `relative_uom_id`, `factor`, `rounding`, `parent_path`, `product_uom_ids`, `related_uom_ids`, `package_type_id`, `l10n_id_uom_code`, `route_ids`, `create_uid`, `create_date`, `write_uid`, and `write_date`.
- `relative_uom_id` is a many2one relation to `uom.uom`; `related_uom_ids` is a one2many relation to `uom.uom`.
- `product_uom_ids` is a one2many relation to `product.uom`.
- `factor` is readonly, stored, and depends on `relative_factor`, `relative_uom_id`, and `relative_uom_id.factor`.
- `rounding` is readonly and not stored.
- The fields `category_id`, `uom_type`, and `factor_inv` were not present in this inspected environment; do not assume the classic Odoo UoM category/type schema without probing first.

Sample-derived inference, not a universal hard rule:

- Reference/root UoMs were found with `relative_uom_id = false`, `relative_factor = 1.0`, `factor = 1.0`, and a single-segment `parent_path`.
- Child/package UoMs can point to another UoM through `relative_uom_id`; their `factor` appears to represent an accumulated absolute quantity relative to the root/reference unit.

### 2026-04-07 - `uom.uom` inline conversion concept

Source: database `hwgroup_erp`, profile request `Follow Global Default`, workbook export `logs/odoo_inspector/UoM Master Data - 2026-04-07.xlsx`, read-only scope.

Curated business/schema concept from user teaching plus workbook verification:

- "Inline" or "sejalur" UoMs are UoMs that share the same root in `parent_path`. For example, rows whose `parent_path` starts with `14/` are in the `GRAM (GR)` lineage and can be converted to each other.
- Do not infer inline convertibility from name/package prefix alone. Use `parent_path` root lineage first.
- In this environment, mass/weight UoMs such as `KG`, `ZAK @25 KG`, `JAR @1 KG`, `BAG @1 KG`, `BTL @1000 GRAM`, and `PACK @500 GRAM` share root `GRAM (GR)` and are structurally inline.
- Conversion formula for same-root UoMs: `target_qty = source_qty * source.factor / target.factor`.
- Example verified from workbook: `ZAK @25 KG` has `factor = 25000`, `KG` has `factor = 1000`, and `JAR @1 KG` has `factor = 1000`, all under root `GRAM (GR)`. Therefore `1 ZAK @25 KG = 25 KG = 25 JAR @1 KG = 25000 GRAM (GR)`.
- UoMs with different roots are not structurally inline even if names sound related; compare root id/name from `parent_path` before converting.
- User-approved business equivalence override: `Units` and `PCS (P)` must be treated as inline/equivalent 1:1 even when their `parent_path` roots differ. This is a narrow case khusus outside structural `uom.uom` lineage and must not be generalized to other count UoMs without explicit user teaching.
- User-taught fatal guardrail: never convert non-inline UoMs by parsing packaging text from the name. This can corrupt stock quantity, valuation, and HPP because the semantic dimension may differ.
- Counterexample to remember: `CASE @24 BTL @330 ML` has `parent_path = 11/2238/2704/`, root `MILILITER (ML)`, factor `7920`, and reference `BTL @330 ML`; `BTL @1 (EA)` has `parent_path = 2205/`, root `BTL @1 (EA)`, factor `1`. They are not inline. `1 CASE @24 BTL @330 ML = 24 BTL @330 ML = 7920 MILILITER (ML)`, but it must not be converted to `BTL @1 (EA)` through `uom.uom` inline conversion.
- Dedicated read-only tool: `python tools\odoo_inspector\uom_inline_check.py --source "SOURCE UOM" --target "TARGET UOM" --quantity 1`.
- For SVL/purchase anomaly triage, do not stop at `stock.valuation.layer.uom_id` versus `stock.move.product_uom`. After a receipt has been generated, those fields may already show the same/product UoM. Also compare upstream `purchase.order.line.product_uom_id` and bill `account.move.line.product_uom_id` against `product.product.uom_id`/`stock.valuation.layer.uom_id`.
- In this environment, `purchase.order.line.product_uom_qty` can carry a computed total in product UoM. If `purchase.order.line.product_uom_id` is not inline with `product.product.uom_id`, that computed quantity can reveal a dangerous cross-root conversion.

### 2026-04-07 - `purchase.order.line.invoice_lines` bill filtering

Source: user-taught workflow correction after live read-only inspection in the default Odoo connection path. Scope: upstream PO/Bill inspection and UoM/SVL anomaly reports.

- Do not assume every `account.move.line` id listed in `purchase.order.line.invoice_lines` is a vendor bill line.
- Confirm whether a line is a real bill line by reading `account.move.line.move_type` or its parent `account.move.move_type`.
- Treat only `move_type in ("in_invoice", "in_refund")` as bill/refund content for Bill columns.
- Treat `move_type = "entry"` as a linked journal entry, such as an STJ correction, even when it has `purchase_line_id` and `purchase_order_id`. Keep these lines in separate adjustment/other-linked columns so Bill amount, SVL value, and correction amount do not look duplicated or ambiguous.
- When duplicate-looking rows appear in PO/Bill/SVL reports, first group by `purchase.order.line.id` and separate vendor bill lines from linked journal entries before interpreting nominal differences.

### 2026-04-08 - Purchase/SVL accounting triage and return value mismatch

Source: database `hwgroup_erp`, profile request `Follow Global Default`, user-guided live read-only investigation of Purchase Cycle Balance, SVL valuation journals, vendor bill lines, and return-to-vendor flow. Raw transaction artefacts are kept under `logs/odoo_inspector/`; do not copy one-off transaction numbers or company IDs here.

Curated workflow rules:

- Vendor bill numbers are not safe as globally unique identifiers. When a bill name is provided, narrow by company, PO origin, partner, product, or picking context before reading bill lines.
- Keep these three journal sources separate:
  - Vendor bill content: `account.move.line` / parent `account.move` with `move_type in ("in_invoice", "in_refund")`.
  - SVL valuation JE: `stock.valuation.layer.account_move_id` and, when present, `account.move.stock_move_id`.
  - Other linked STJ/correction/adjustment entries: `move_type = "entry"` discovered through purchase line links, SVL relation, `account.move.stock_move_id`, reference text, or `account.move.line.matching_number`.
- For item-level purchase-cycle triage, pull these models together before deciding the accounting case:
  - `stock.picking`: `id`, `name`, `origin`, `state`, `picking_type_code`, `date_done`, `move_ids`.
  - `stock.move`: `id`, `picking_id`, `product_id`, `product_uom`, `quantity` or `product_qty`, `purchase_line_id`, `origin_returned_move_id`, `returned_move_ids`.
  - `stock.valuation.layer`: `id`, `reference`, `description`, `product_id`, `quantity`, `value`, `unit_cost`, `uom_id`, `stock_move_id`, `account_move_id`.
  - `account.move`: `id`, `name`, `ref`, `move_type`, `journal_id`, `stock_move_id`, `line_ids`, `state`, `date`.
  - `account.move.line`: `id`, `move_id`, `account_id`, `product_id`, `product_uom_id`, `quantity`, `debit`, `credit`, `balance`, `matching_number`, `purchase_line_id`, `purchase_order_id` when present.
  - `purchase.order.line`: `id`, `order_id`, `product_id`, `product_qty`, `product_uom_id`, `product_uom_qty`, `qty_received`, `qty_invoiced`, `invoice_lines`, `move_ids`.
- For return-to-vendor flow, do not require every STJ/adjustment to have direct PO or bill relations. A return valuation/adjustment journal can be direct-linked to the return `stock.move` through `account.move.stock_move_id`, then indirect-linked to picking and PO via `stock.move.picking_id` and `stock.move.purchase_line_id`.
- `matching_number` is evidence for reconciliation clustering, especially on suspense account `2103006`, but it is not enough by itself to assign source item or bill. Verify item/source through `product_id`, `stock_move_id`, PO line, and picking linkage.
- The current Purchase Cycle Balance classifier can mark a cycle `healthy` when problem accounts such as `2103006` and `1108099` are balanced, even if item-level inventory and HPP have equal-and-opposite balances. Treat that as a possible business/data-quality issue, not proof the cycle is economically clean.

Planned classifier concept, documented in `docs/balance_cycle_pembelian.md`:

- `case8a` - Full Return Value Mismatch: full return quantity, bill item value is zero or not billed, and `abs(return_svl_value)` differs from expected return value from the original receipt.
- `case8b` - Partial Return Value Mismatch: partial return quantity and return SVL value differs from the expected return value for the returned quantity.
- Shared calculation:

```python
receipt_unit_cost = receipt_value / receipt_qty
expected_return_value = return_qty * receipt_unit_cost
expected_kept_value = kept_qty * receipt_unit_cost
return_value_gap = abs(return_svl_value) - expected_return_value
```

- Repair direction from `return_value_gap`:
  - If `return_value_gap > 0`, return took too much value from inventory: suggest `Dr Inventory / Cr COGS`.
  - If `return_value_gap < 0`, return took too little value from inventory: suggest `Dr COGS / Cr Inventory`.
- For `case8a`, expected final item-level balances are approximately zero for inventory, HPP/COGS, and suspense if the item was fully returned and not billed.
- For `case8b`, never zero all inventory/HPP for the item; only correct the return valuation gap. Keep separate any bill/price variance for the quantity that remains kept.
- Prefer valuation-aware repair or manual review. A GL-only JE can make financial statement presentation cleaner, but may leave SVL/subledger history inconsistent if no valuation adjustment is made.
