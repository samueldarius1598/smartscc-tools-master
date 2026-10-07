---
name: odoo-async-compat-service-pattern
description: Use when implementing a new Odoo-backed async service, fixing model or field incompatibilities, or reviewing whether RPC, schema detection, retries, and tests follow this repo's service-layer pattern.
---

# Odoo Async Compat Service Pattern

## Overview

This skill captures the repo's preferred pattern for Odoo service code: async service classes, gateway-based RPC access, compatibility-first field resolution, and `unittest` coverage with fake clients.

For this repository, also use the repo-local references:

- [references/runtime-facts.md](references/runtime-facts.md) for current project facts and validated field behavior.
- [references/incident-log.md](references/incident-log.md) for historical and runtime hazard notes.

## When To Use

- Adding a new Odoo service under `smartscc_tools/features/item_journal/services` or `smartscc_tools/features/edit_transaksi/services`.
- Fixing field-name mismatches between Odoo instances.
- Moving RPC logic out of a UI or entrypoint layer into a reusable service.
- Reviewing whether retries, logging, and schema fallbacks are handled defensively.

## Core Rules

- Service APIs should be async and domain-oriented. Do not bury business behavior in widget callbacks or raw entrypoint code.
- Use the existing gateway, runtime, and config helpers before inventing new transport or auth plumbing.
- Resolve schema differences defensively through `fields_get`, capability detection, or ordered fallbacks instead of assuming one fixed field name.
- Keep request, result, and state payloads in typed dataclasses with explicit names.
- Include useful logging context such as model, field, stage, or company when surfacing failures.
- Keep UI code unaware of low-level RPC details; UI should call services, not assemble transport calls itself.
- Prefer deriving reconciliation or dashboard summary metrics in the service or typed result model, not ad hoc in widgets.
- When a tool can route between live, dummy, or default databases, make the effective or source database explicit in outputs or logs so missing data is diagnosable.
- For large batch writes, use bounded concurrency driven by request or module settings such as `max_workers`; keep outputs order-preserving and progress-friendly.
- Result payloads intended for user-facing summaries or exports should carry official document numbers such as `name` or `move_name`; numeric IDs are diagnostics-only fallbacks.
- If create/post may assign the final document number late, re-read the final `name` before returning the result payload to UI/export layers.
- When seeding manual config lists for Odoo-backed tools, use one-time migration semantics: backfill legacy missing or empty state once, then preserve intentionally empty user state after initialization.
- Prefer fake clients and `unittest` regression tests over live Odoo access in the baseline test path.

## Default Workflow

1. Start from the existing runtime boundary.
Use the shared config and gateway helpers for connection settings, database override behavior, company context, and retry-capable RPC access.

2. Define a service API around domain work.
Use request and result dataclasses when the operation has multiple inputs, outputs, warnings, or partial-failure paths.

3. Detect schema capabilities early.
Probe model fields or relationships once, cache the result when appropriate, and choose the safest available fallback instead of assuming the newest schema.

4. Keep writes explicit and explain partial failures.
When updating multiple models, surface which model or field failed and keep enough context for UI logs and tests.

5. Test with fakes.
Use fake RPC clients to cover success, fallback selection, blocked writes, partial-write errors, and compatibility branches without needing live Odoo access.

## Known Compatibility Biases For This Repo

- Prefer `stock.move.product_qty` over assuming `stock.move.quantity_done`.
- Use `stock.move.line.qty_done` for done quantities.
- For stock valuation layer dates, support `accounting_date` when present and fall back to `date` when it is not.
- Treat user-confirmed schema facts as hard compatibility requirements for future changes.
- When this repo's environment distinguishes display line qty from done qty, prefer the validated repo-local behavior from `references/runtime-facts.md`.
- For repo-specific journal and incident history, consult the references instead of assuming generic Odoo behavior.

## Review Checklist

- Is the service async and separated from UI code?
- Does it reuse the existing gateway or runtime helpers?
- Does it detect fields or relationships defensively instead of hardcoding a fragile schema?
- Are failures logged with model or field context that helps triage?
- Are request and result shapes explicit and typed?
- Are `unittest` tests present for success, fallback, and error paths?

## Avoid

- Do not query uncertain fields without a fallback strategy.
- Do not let widget code construct raw RPC domains, field lists, or write payloads when that behavior belongs in a service.
- Do not base correctness on live Odoo availability for routine tests.
- Do not hide partial-write behavior; report which model updates succeeded or failed.
