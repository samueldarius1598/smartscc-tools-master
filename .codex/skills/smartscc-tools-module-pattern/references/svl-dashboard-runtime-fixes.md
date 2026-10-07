# SVL Dashboard Runtime Fixes
_Last updated: 2026-03-20_

## Symptom
- User runs `Analyze`
- Backend analysis completes normally
- Dashboard freezes during post-snapshot render, not during Odoo fetch

## Final Root Cause
- Snapshot apply was already asynchronous, but sidebar Treeview selection still
  produced a selection echo loop on the Tk main thread.
- Programmatic `_sync_sidebar_selection()` triggered `<<TreeviewSelect>>`.
- The selection handler re-entered `_select_item()` for the same active product.
- The re-entry path re-rendered summary UI and rescheduled first-item prefetch.
- Repeated selection echo starved the event loop and produced `Not Responding`.

## Stable Fix Pattern
- Make `_select_item()` idempotent for the already-active PID.
- Read the active Treeview row from `selection()` first, not only from `focus()`.
- Ignore `<<TreeviewSelect>>` when it points to the currently active item.
- Use `_sync_sidebar_selection()` only for programmatic sync; do not call it again
  from the Treeview-origin selection path.
- Gate first-item prefetch behind `_snapshot_interactive_ready` so initial detail
  loading starts only after the snapshot is already interactive.

## Diagnostic Logging Pattern
Use temporary UI-stage logs to separate backend completion from UI starvation:
- `UI snapshot received`
- `UI snapshot apply scheduled`
- `UI snapshot apply started`
- `UI company summary applied`
- `UI sidebar batch start`
- `UI sidebar batch complete`
- `UI selected item summary rendered`
- `UI busy cleared`
- `UI snapshot interactive`
- `UI first-item prefetch scheduled`
- `UI first-item detail fetch started`
- `UI first-item detail applied`

## Compatibility Note
- Keep the service-layer summary/detail split intact.
- Do not revert lazy `fetch_item_detail()` just because the freeze appeared near
  snapshot apply; the successful fix is in UI selection-loop control, not in
  restoring eager detail fetch.
