---
name: smartscc-tools-module-pattern
description: Use when adding a new Smart's CC Tools Master module, embedding an existing Tkinter tool into the shell, refactoring module UI/state wiring, or reviewing whether a change matches the repo's shell-module architecture.
---

# Smart's CC Tools Module Pattern

## Overview

This skill captures the default architecture for Smart's CC Tools Master modules. Use it to keep new tools aligned with the repo's shell, state, theme, lifecycle, and test patterns.

## When To Use

- Adding a new tool under `smartscc_tools/modules`.
- Refactoring a standalone Tkinter window into an embeddable panel.
- Wiring module settings, branding, or lifecycle hooks into the dashboard shell.
- Reviewing whether a module change broke the repo's modular UI contract.

## Core Rules

- Only the dashboard shell creates `tk.Tk()`. Modules receive a parent frame and render into it.
- New tools should implement `ModuleBase` and be registered through the static module registry.
- Prefer lazy UI creation and lifecycle-aware activation or shutdown hooks.
- Keep shared state in global settings and per-module state in `module_settings`.
- When adopting a standalone tool from another repo, port its business logic into a dedicated package first and keep the shell adapter under `smartscc_tools/modules` thin.
- Reuse centralized theme and branding helpers instead of hardcoding colors, fonts, or icon paths in module code.
- Tkinter UI stays synchronous. Long-running or network-backed work runs in background threads and communicates back through queues or scheduled polling.
- Odoo calls belong in service or gateway layers, not directly in widget callbacks.
- Reuse the app's shared auth/runtime path; if the module needs limited DB routing, add a small module-specific selector instead of replacing the app-wide connection model.
- For Odoo-backed modules, use the shared database-profile selector pattern rather than hardcoded database lists or free-text overrides.
- Shared database profiles belong in global settings and should carry `database_value`, `alias`, and `note`; render labels in the form `Alias - Value [Note]` with fallbacks when a field is empty.
- Module selectors should include `Follow Global Default`, and global settings should expose `Use GAS Default` plus editable saved profiles.
- Favor shared UI primitives for logs, scroll behavior, and collapsible sections instead of duplicating per-module implementations.
- For dense master-detail dashboards, a lightweight custom sidebar group is acceptable when the shared collapsible card feels visually too heavy; keep the shared collapsible widget for logs or sections that benefit from the full shared header pattern.
- Filter or search inputs may use ghost-text placeholders when the surrounding card or section title already makes the field purpose obvious.
- Analytical list headers may show a summary of the current filtered view, such as visible count plus total signed value.
- When two summary metrics are tightly coupled, a single KPI card may show a large primary metric with a smaller secondary metric below it.
- Default tests are `unittest` tests that cover registry wiring, state persistence, module adapters, and service-facing behavior.
- For `SVL Fix JE` dashboard work involving snapshot apply, lazy detail, or Tkinter freezes after backend completion, read `references/svl-dashboard-runtime-fixes.md` before changing the selection or prefetch flow.
- For `SVL Fix JE` changes touching Balance Cycle Pembelian logic or dashboard output, read `references/balance-cycle-pembelian.md` first and update `docs/balance_cycle_pembelian.md` in the same change.

## Default Workflow

1. Start from the module contract.
Define `module_id`, `display_name`, `description`, `icon_path`, and `create_ui`, then decide whether `on_activate`, `on_deactivate`, or `on_shutdown` are needed.

2. Separate the panel from the shell.
If the source tool is standalone, extract an embeddable panel that accepts a parent widget. Leave any standalone launcher as a thin wrapper around that panel.

3. Keep state boundaries explicit.
Module-specific inputs belong in `module_settings`. Cross-module or shell-wide settings belong in global settings. Legacy state may be migrated once, then written back only through the new store. Persist browse/output defaults and collapsible-state preferences there instead of creating new ad hoc files.
For Odoo database routing, keep per-module selection as a `database_profile_id` in `module_settings` and keep the editable profile list and global default in global settings.

4. Wire background work safely.
Use worker threads plus queues or scheduled polling for async-backed operations so the Tkinter event loop stays responsive.

5. Fit the UI into the existing shell language.
Use the shared theme, card layout, sidebar/header conventions, and branding helpers. Favor business-oriented, dense desktop UI over playful or web-like redesigns. In this repo, collapsed sections should use the shared `CollapsibleSection` pattern where it fits, collapsed logs should fall back to a one-line latest preview, and analytical dashboards may use lighter grouped sidebars when density matters more than generic card reuse.

6. Add regression tests.
Cover registry inclusion, embedded panel creation, state load or save behavior, collapsed-state behavior where relevant, and any adapter or lifecycle rules introduced by the module.

## Review Checklist

- Does the module avoid creating its own `tk.Tk()` root?
- Is async or network work kept off the UI thread?
- Is state stored through global settings or module settings rather than ad hoc files?
- If the module is Odoo-backed, does it use the shared database-profile dropdown and resolver instead of a custom DB override field?
- Are colors, branding, and icons reused from centralized helpers?
- Is the module added to the static registry in the intended order?
- Are `unittest` regression tests present for the module wiring?

## Avoid

- Do not open raw Odoo RPC calls from widget handlers.
- Do not hardcode duplicate theme or icon paths inside modules.
- Do not store new module state in separate ad hoc JSON files unless there is a one-time migration reason.
- Do not move heavy logic into the shell when it belongs in a service or adapter layer.
- Do not treat a post-analyze Tk freeze as proof that Odoo fetch is still running; rule out Treeview selection echo and post-snapshot event storms first.

## References

- [references/svl-dashboard-runtime-fixes.md](references/svl-dashboard-runtime-fixes.md) - repo-specific runtime lessons for SVL dashboard snapshot apply and lazy detail flow
- [references/balance-cycle-pembelian.md](references/balance-cycle-pembelian.md) - repo-specific logic summary and mandatory doc-sync rule for Balance Cycle Pembelian

