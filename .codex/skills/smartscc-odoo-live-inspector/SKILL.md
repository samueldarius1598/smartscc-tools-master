---
name: smartscc-odoo-live-inspector
description: Use when Codex needs to inspect Smart's CC Tools Master live Odoo schema or records safely, especially for model learning tasks such as `uom.uom`, `stock.move`, `account.move`, or SVL dashboard/service triage; covers read-only inspector tools, guarded manual Odoo write tools, curated schema references, and the required summary of what was learned versus what remains unclear.
---

# SmartSCC Odoo Live Inspector

## Workflow

Use the repo tools instead of guessing live Odoo structure.

1. Clarify only the missing live context that cannot be inferred from the request: database profile, target model/record/domain, sample limit, and whether writes are allowed.
2. Prefer read-only inspector commands:
   - `python tools\odoo_inspector\inspect_model.py --model uom.uom --sample-limit 5`
   - `python tools\odoo_inspector\schema_snapshot.py --model uom.uom`
   - `python tools\odoo_inspector\trace_record.py --model uom.uom --domain "[[\"name\",\"ilike\",\"Unit\"]]" --fields id,name,relative_uom_id,relative_factor,factor`
   - `python tools\odoo_inspector\uom_inline_check.py --source "ZAK @25 KG" --target "KG" --quantity 1`
   - `python tools\odoo_inspector\dashboard_probe.py`
3. Keep raw inspection output in `logs/odoo_inspector/`; do not paste large raw payloads into chat.
4. Report in two short sections: `Yang berhasil dipelajari` and `Yang masih membingungkan/perlu dikonfirmasi`.
5. Write reusable facts only after they are curated and stable enough for future repo work.

## Concept Teaching Protocol

When the user wants to teach a reusable Odoo/business concept, guide them to provide:

- Concept name and affected model(s).
- Scope: which database/profile or module behavior the concept applies to.
- Rule statement: what should be treated as true in future work.
- Positive examples and counterexamples, without one-off transaction IDs unless explicitly needed for debugging.
- Whether the concept is a hard rule, a current-environment fact, or an inference that must be rechecked.
- Permission to write the curated note into `references/curated-schema.md`.

After learning, summarize what was accepted as reusable knowledge, what remains uncertain, and which file was updated.

PowerShell note: JSON domains passed to `--domain` need escaped quotes, for example:

`python tools\odoo_inspector\inspect_model.py --model uom.uom --domain "[[\`"relative_uom_id\`",\`"=\`",false]]" --sample-limit 5`

## Default Connection And Output Naming

- If the user does not specify a database/profile, silently use the repo's default Odoo connection flow: app runtime settings, GAS-backed credentials, and `Follow Global Default` database profile resolution.
- Do not ask for credentials again unless the default connection fails, the user requests a different profile, or a write/manual-fix flow requires explicit confirmation.
- Do not print or store raw API keys/passwords. Reporting host, database, profile, login email, and UID is acceptable when the user asks which connection was used.
- For exported inspection files, use simple, descriptive, intuitive titles and filenames, for example `UoM Master Data - 2026-04-07.xlsx`.
- Prefer putting live DB artefacts in `logs/odoo_inspector/` unless the user asks for another folder.
- For Excel reports generated with `openpyxl`, do not add `Table(...)` objects by default. Prefer styled normal ranges with freeze panes and sheet-level filters, because Excel may repair/remove generated `/xl/tables/table*.xml` parts.
- For exported inspection workbooks, keep ID and transaction-number columns as text even when values are numeric-looking; keep quantity and currency columns as real numeric cells with number formats; group adjacent columns by ID, transaction number, UoM, currency, and quantity where practical, using distinct header colors for each group.
- For upstream PO/Bill inspection reports, do not treat every `purchase.order.line.invoice_lines` entry as a vendor bill. Only `account.move.line` rows with `move_type` in `in_invoice` or `in_refund` belong in Bill columns; linked journal entries such as STJ corrections (`move_type = entry`) must be separated into other-linked/adjustment columns so bill amount, SVL value, and correction amount do not look duplicated or ambiguous.
- Keep SVL valuation journals separate from purchase-line linked adjustments. A receipt valuation journal comes from `stock.valuation.layer.account_move_id`, not from `purchase.order.line.invoice_lines`; show it in dedicated SVL JE columns so it is not mistaken for a missing bill or an other-linked correction entry.
- Validate important `.xlsx` exports by checking the archive contains no unintended `xl/tables/*` parts and, when Excel COM is available, opening the workbook once through a hidden `DispatchEx("Excel.Application")` instance.

## UoM Inline Conversion Guardrail

- For `uom.uom` conversion checks, never rely on name text alone. Use live Odoo or a current export to compare `parent_path` root and `factor`.
- Use `tools\odoo_inspector\uom_inline_check.py` before treating one UoM as convertible to another.
- Same root in `parent_path` means inline/sejalur and conversion may use `target_qty = source_qty * source.factor / target.factor`.
- Different roots mean not inline; block the conversion unless the user explicitly defines a separate business rule outside `uom.uom`.
- User-approved business equivalence override: treat `Units` and `PCS (P)` as inline/equivalent 1:1 even when their `parent_path` roots differ. Keep this exception narrow; do not generalize it to other count UoMs without explicit user teaching.
- Treat non-inline conversion as high-risk/fatal because it can silently corrupt quantities and cost/HPP by converting packaging/count units into weight/volume units incorrectly.
- Remember the taught counterexample: `CASE @24 BTL @330 ML` is inline with `MILILITER (ML)` / `BTL @330 ML`, but not inline with `BTL @1 (EA)`, even though the business name contains `24 BTL`.

## Purchase/SVL Accounting Triage

- When analyzing a named vendor bill, do not assume `account.move.name` is globally unique. Narrow it by company, PO origin, partner, product, or picking context before interpreting bill lines.
- For Purchase Cycle Balance triage, start from upstream and then trace downstream: `purchase.order.line.product_uom_id`, vendor bill `account.move.line.product_uom_id`, product/SVL UoM, `stock.move`, `stock.valuation.layer`, SVL valuation JE, linked adjustments, payment, and bank reconciliation.
- Keep three journal sources distinct:
  - Vendor bill lines: `account.move.line.move_type in ("in_invoice", "in_refund")`.
  - SVL valuation JE: normally from `stock.valuation.layer.account_move_id` or `account.move.stock_move_id`.
  - Other linked STJ/correction/adjustment entries: `move_type = "entry"` discovered through purchase line links, SVL, `stock_move_id`, references, or `matching_number`.
- For return-to-vendor cycles, a STJ adjustment may be direct-linked to `account.move.stock_move_id` rather than to the bill or PO. From there, infer the picking/PO only through `stock.move.picking_id` and `stock.move.purchase_line_id`.
- `matching_number` is useful evidence that STJ receipt, STJ return, and STJ adjustment lines are in the same reconciliation cluster, especially on `2103006`, but it must not replace exact item/source checks through `stock.move`, `product_id`, and PO line relations.
- A cycle can be `healthy` in the current classifier because problem accounts such as `2103006`/`1108099` are balanced, while still being business-problematic because inventory and HPP have equal-and-opposite balances caused by return valuation mismatch.
- Planned future classifier concept: use a shared Return Value Mismatch calculation and label it as `case8a` for full return + zero item bill, or `case8b` for partial return. The repair suggestion is based on `return_value_gap = abs(return_svl_value) - expected_return_value`; if positive, suggest `Dr Inventory / Cr COGS`, and if negative, suggest `Dr COGS / Cr Inventory`, with valuation-aware/manual-review guardrails.

## Guardrails

- Inspector scripts are read-only by design.
- Manual write tools under `tools\manual_odoo` default to dry-run and require `--apply`.
- Never turn one-off transaction numbers, company IDs, temporary mismatch outputs, or today’s active database into permanent skill knowledge.
- For schema facts, record source database/profile, inspection date, model, and scope.
- If a field exists only as an inferred fallback, label it as an inference rather than a confirmed hard requirement.

## References

- Read `references/curated-schema.md` before using previous live-schema conclusions.
- Add only stable, reviewed schema notes there; keep raw model dumps in ignored logs.
