# Incident Log

This file records repo-local hazards and historical runtime incidents. Distinguish stable facts from historical incidents.

## Historical Resolved Incidents

### Holycount `UndefinedColumn` on stock models

- Status: historical, reported resolved on the server side by the user on 2026-03-16.
- Scope observed during triage:
  - `stock.picking.create`
  - `stock.picking.write`
  - some `stock.move.write` paths that triggered picking-side custom logic
  - some relation reads that traversed broken custom stock picking behavior
- Symptom:
  - errors mentioning missing columns such as `holycount_state`, `holycount_response`, or `is_from_holycount`
- Interpretation:
  - this was a server custom-module or schema-drift problem, not a client request to use Holycount behavior
- Triage rule if it reappears:
  - suspect server-side overridden stock models first
  - do not misattribute it to the app intentionally sending Holycount fields

## Historical Compatibility Hazards

### Brittle relation reads on line and valuation models

- Status: historical hazard observed in this environment during March 2026 triage.
- Scope observed:
  - `stock.move.line.read/search_read` when requesting `move_id`
  - `stock.valuation.layer.read/search_read` when requesting `stock_move_id`
- Effect:
  - relation reads could trigger server-side stock-picking custom breakage
- Client pattern retained in this repo:
  - prefer parent-bucketed lookup and safe-field reads when needed
  - attach parent ids client-side rather than relying on the brittle relation field access path

## Current Behavioral Rules

### STJ and accounting lock handling

- Treat `fiscalyear_lock_date` as a hard stop for STJ date edits.
- If the STJ is `posted` and the target date is outside the lock period, preferred handling is:
  - move to draft
  - write date
  - post again
- If the draft or repost path itself is broken by server drift, fail with the exact root cause.
