## GitHub clone dan AI Agent di komputer lain

- Baca [docs/github_handoff.md](docs/github_handoff.md) untuk setup clone, credential lokal, dan alur perubahan.
- Untuk clone GitHub, root proyek adalah folder checkout yang berisi file ini. Path absolut pemilik di bawah hanya menjelaskan instalasi pemilik; jangan membuat ulang path tersebut atau junction di komputer lain.
- Source, tests, docs, aset aplikasi, dan skill `.codex/skills/` berada dalam repo. Memori `C:/Users/User/.ai-shared/`, skill global pemilik, dan repo referensi Odoo adalah referensi eksternal opsional pada komputer lain; gunakan dokumentasi repo bila tidak tersedia. Jangan menganggap referensi eksternal itu sudah dibaca.
- API key GAS harus disediakan per komputer melalui `SMARTSCC_GAS_API_KEY` atau `%APPDATA%/smartscc_tools/gas_api_key.txt`. Jangan commit credential, workbook kerja, hasil ekspor, atau log produksi.
- Alamat fallback CSV konfigurasi juga bersifat privat: gunakan `SMARTSCC_GAS_FALLBACK_CONFIG_SSID` atau `%APPDATA%/smartscc_tools/fallback_config_ssid.txt` bila diperlukan. Jangan mengembalikan ID spreadsheet konfigurasi ke source atau history publik.
- Baseline tests menggunakan fake clients. Jangan menjalankan write Odoo live sebagai tes otomatis atau menganggap izin perubahan kode mencakup write transaksi produksi.

## Lokasi instalasi pemilik

- Lokasi canonical: `! Asset Development Template HPP/Smarts CC Tools Master`, sejajar dengan repo `Smarts CC`; tidak lagi di `Tools OdooXPython`.
- Root absolut: `C:/Users/User/OneDrive/!! PT Aneka Bintang Gading (Hollywings Group)/! HPP dan Cost Outlet/! Asset Development Template HPP/Smarts CC Tools Master`.
- Setelah relokasi, verifikasi workspace VS Code dan cwd sesi agen secara terpisah. Chat yang dilanjutkan dapat mewarisi cwd lama; gunakan workdir canonical secara eksplisit. Jangan membuat junction untuk menyamarkan path yang tertinggal.

## Repo Structure

```
root/
├── smartscc_tools/      # source code utama (ModuleBase, services, widgets, features, entrypoints)
├── tests/               # test suite (pytest / unittest)
├── tools/               # helper scripts Odoo (odoo_inspector, manual_odoo)
├── main.py              # entry point aplikasi
├── export_pcb_excel.py  # script standalone PCB export
│
├── build_tools/         # semua artefak build & release
│   ├── build_installer.ps1
│   ├── requirements.txt
│   ├── packaging/       # PyInstaller spec, Inno Setup .iss, install_app.ps1
│   ├── icon/            # ikon branding Windows (mainlogo.ico, mainlogo.png)
│   └── assets/          # aset visual app (logo, gambar branding)
│
├── docs/                # dokumentasi teknis lokal
│   └── *.md             # handoff notes, balance cycle docs, dll
│
├── output/              # file output audit/export (tidak di-commit ke source)
│   ├── PCB_Audit_*.xlsx
│   └── archive/         # arsip workbook dan investigasi lama
│
├── logs/                # log runtime dan bukti koreksi manual Odoo
├── scratch/             # sementara saja; dibuat saat diperlukan
│
├── build/               # output PyInstaller (auto-generated, gitignored)
├── .venv/               # satu virtual environment lokal (gitignored)
├── .claude/             # konfigurasi Claude Code lokal
└── .codex/              # konfigurasi Codex lokal
```

**Catatan penting untuk AI agents:**
- Source code **tidak** ada di subfolder `src/` — semua di root langsung (`smartscc_tools/`, `tests/`, `tools/`, `main.py`).
- `build_tools/` bukan source — jangan import dari sana.
- `scratch/` dan `output/` adalah artefak sementara/runtime — jangan baca sebagai source of truth.
- Gunakan `.venv/` di root sebagai satu environment development. Setelah membuat ulang environment, jalankan `.venv\Scripts\python.exe tools\configure_dev_env.py` untuk memusatkan cache ke `%LOCALAPPDATA%/SmartsCCTools/cache/` di luar OneDrive. Jangan mengarahkan `sys.pycache_prefix` ke dalam repo: Python mengulang path absolut sumber di bawah prefix sehingga melewati batas path OneDrive. Jangan menyalin atau memindahkan venv dari lokasi lain; activation dan console launcher dapat menyimpan path absolut lama.
- Simpan hasil ekspor di `output/` (bukan `outputs/`), arsip lama di `output/archive/`, dan bukti koreksi Odoo di `logs/manual_odoo/`. Jangan menghapus bukti koreksi atau workbook unik sebagai cache.
- Repo ini adalah lokasi canonical aplikasi; jangan membuat ulang junction `itemJournalOdoo` atau import dari folder tool standalone di sebelah repo. Peta konsolidasi ada di `docs/repo_consolidation.md`; arsip source historis bukan kode runtime dan tidak dikemas ke installer.
- PyInstaller spec ada di `build_tools/packaging/windows/tools_master_gui.spec`, bukan di `packaging/`.
- Untuk menjalankan build: `powershell -ExecutionPolicy Bypass -File .\build_tools\build_installer.ps1 -Version 1.0.0`

## Repo Defaults

- Treat this repository as a Windows-first desktop app. When giving launch or install commands for this repo, default to Command Prompt-compatible syntax and use `python`.
- The primary architecture is a modular Tkinter shell in `smartscc_tools` with reusable modules. Only the dashboard shell should create `tk.Tk()`.
- New tools should integrate as `ModuleBase` modules and be registered statically. Do not introduce new standalone roots unless legacy compatibility explicitly requires it.
- Prefer lazy import and lazy UI construction for heavy panels, dashboard pages, and settings surfaces on startup-sensitive paths; move first-load cost to first use rather than app launch when practical.
- Odoo and service I/O should be async. Tkinter UI stays synchronous and should bridge async work through worker threads, queues, and `asyncio.run(...)` outside the main UI thread.
- For large third-party or Odoo batches, prefer bounded concurrency driven by module settings such as `max_workers`; keep results order-preserving, keep progress visible on the same UI surface that launched the action, and keep write orchestration in the service layer rather than widgets.
- Keep RPC and schema logic inside gateway or service layers, not inline in widgets.
- For Odoo logic inspection, use the external repository [Repo Odoo Holywings](<C:/Users/User/OneDrive/!! PT Aneka Bintang Gading (Hollywings Group)/Repo Odoo Holywings>). Read that repository's `AGENTS.md` before inspecting its code. Treat it as a read-only reference for work in Tools Master, outside this app's imports, tests, packaging, and release artifacts. Odoo source copies and junctions under `docs/references/` have been removed; do not recreate them in this repository.
- Persist shared app state in global settings and keep per-module state inside `module_settings`.
- When one configuration block affects multiple pages in the same module, expose it as one shared configuration surface instead of duplicating page-local sources of truth.
- For user-facing Odoo summaries and exports, prefer official document numbers such as `name` or `move_name`; numeric IDs are diagnostics-only fallbacks.
- Reuse centralized theme and branding helpers for colors, fonts, and icons. The `build_tools/icon/` folder is the branding source of truth, and shipped Windows branding should continue to land in `mainlogo.png` and `mainlogo.ico`.
- Keep `mainlogo.ico` valid for Windows shell surfaces; preserve at least `16x16` and `32x32` sizes when regenerating it.
- Keep Windows branding and installer references synchronized across `smartscc_tools/branding.py`, `build_tools/packaging/windows/tools_master_gui.spec`, `build_tools/packaging/windows/installer.iss`, and `build_tools/packaging/windows/install_app.ps1`.
- When shipped branding, icons, packaging, installer scripts, or bundled desktop assets change, run `powershell -ExecutionPolicy Bypass -File .\\build_tools\\build_installer.ps1 -Version 1.0.0`; targeted unit tests alone are not enough.
- User-facing log panes should default to at least 15 visible rows via shared UI constants/helpers, not per-module magic numbers.
- Collapsed sections should show a meaningful header summary or one-line compact preview instead of leaving the header area empty.
- Collapsible sections in this repo should use the shared header-row pattern: filled primary `Hide` / `Show More` button on the left, bold title beside it, and summary text on the right only when collapsed.
- Only make a section collapsible when the collapsed state can show a meaningful summary or compact preview; if no good summary exists, keep the section expanded by default.
- When a log section is collapsed, show a one-line latest-log preview; when expanded, keep the shared minimum log height.
- If an action/progress section is collapsible, keep one shared compact progress row rather than separate progress bars for expanded and collapsed states.
- Standard desktop progress rows should keep a stable bar length and reserve a dedicated detail area for `processed/total`, `%`, and current item or phase text; wrap or compress the detail text inside that area instead of letting the text resize the bar itself.
- Shared scroll tuning belongs in the shared scroll widget, not per-module wheel overrides.
- Search/filter inputs in Indonesian-facing screens may use the default placeholder text `Cari disini...` when the field purpose is already clear from context.
- Autocomplete/search selectors should allow free typing at any length, begin live suggestions once the query reaches at least 3 characters, keep the dropdown synced to the active filter when typing or clicking the arrow button, and keep keyboard navigation plus `Enter` bound to the filtered suggestion order rather than the full list.
- For grouped business tables or `Treeview`s, do not assume a flat or single-level hierarchy; if the requested grouping is not already fixed by the feature, explicitly confirm the desired grouping depth (`tanpa grouping`, `1 level`, `2 level`, or `3+ level`) before implementing.
- Grouped `Treeview` patterns in this repo may use two or more lightweight header levels; row actions and bulk actions should target leaf rows unless the feature explicitly calls for group-level actions.
- Filterable analytical sidebars may show a header summary for the current filtered view, such as count plus total signed value.
- For dense dashboard summaries, strongly related metrics may be combined into one KPI card as a large primary value with a smaller secondary value below it.
- In `Dashboard Control`, the company reconciliation summary should keep the combined `Total SVL` + `Qty` card and the `Belum Terpetakan` card.
- In `Dashboard Control`, `Produk Bermasalah` should keep the header summary based on the current filtered list, not only the full snapshot, and should stay grouped in order as `Automated / Track Inventory`, `Non Product`, then `Manual / Non Inventory`.
- In `Dashboard Control`, `Produk Bermasalah` should stay on a performant `Treeview` for long datasets; prefer multiline wrapped group headers and product rows with row-height tuning over heavier custom sidebar widgets.
- Default inventory COA entries in this repo are seeded manual defaults from settings; do not replace them with runtime auto-detect behavior.
- For `SVL Fix JE`, `Konfigurasi` is the shared source of truth across pages for `Database`, `Reference Prefix`, `Max Workers`, and upload-only auto-post.
- For `SVL Fix JE`, default repair `Reference` and `Label Line` values must derive from the shared `Reference Prefix`; do not hardcode `Correction`.
- For PCB Case 9 `case2_downstream_clearing` with HPP and inventory balances, use the user-confirmed five-line plan: clearing nets to zero and the signed economic gap goes to category HPP/expense. The inventory clearing offset matches the HPP clearing leg, not the inventory amount; omit a zero gap line. Preserve debit/credit balance for reversed signs.
- For PCB Case 9 projected review, do not exempt residual clearing `1108099` from problem detection. Correct projected balances do not remove the mandatory Case 9 review confirmation.
- For `SVL Fix JE`, `Target Account Type = Valuation` must resolve exactly from `product.template.categ_id -> product.category.property_stock_valuation_account_id`, not from a generic inventory-role fallback.
- For `SVL Fix JE`, `svl_no_move` rows with zero value may stay visible in the dashboard but must be excluded from repair entry points.
- For `SVL Fix JE` dashboard snapshots, apply new analysis results quickly, then warm one snapshot-scoped background detail cache; while the snapshot is unchanged, selection and tab changes should reuse local in-memory detail data instead of per-click refetches.
- For `SVL Fix JE` detail panels, `SVL Detail` and `Journal Detail` may render from snapshot-seeded rows before full detail warm completes; `PO vs Bill` may wait for the warmed cache and only fall back to lazy fetch if the batch warm fails.
- For `SVL Fix JE`, synthetic `System / Journal Tanpa Product` / `UNASSIGNED-JNL` (`pid = -1`) is a real selectable sidebar item and must follow the same detail-selection flow as normal products.
- For `SVL Fix JE`, `Repair Collection` should keep persistent per-row state, explicit per-row statuses, grouped source sections for `SVL tanpa JE` versus `Linked JE Header Kosong`, and a resizable master/detail layout around `60/40`.
- For `SVL Fix JE`, large `Repair Collection` scopes should avoid physically selecting every leaf row; prefer virtual select-all with an explicit visible indicator such as `All N rows selected (virtual)`, and make downstream actions read the virtual selection model rather than raw widget selection alone.
- For `SVL Fix JE`, local bulk-fill actions in `Repair Collection` should run in chunks via `after(...)` / `after_idle(...)` so the dialog stays responsive during large batch preparation.
- For `SVL Fix JE`, do not assume that simply increasing `Max Workers` will improve write-heavy repair throughput; prefer caching repeated account, journal, or lock-date lookups and throttling progress/log UI updates before increasing concurrency.
- For `SVL Fix JE`, `Repair All` operates on all draft rows in the currently open repair dialog scope, not all analyzed items; validation is scope-wide and all-or-nothing for that dialog, while `Repair Selected Row(s)` validates only the selected subset.
- For `SVL Fix JE` Balance Cycle Pembelian changes in `smartscc_tools/features/svl_fix_je/svl_fix_je_balance_cycle.py`, `smartscc_tools/modules/svl_fix_je_dashboard_page.py`, `smartscc_tools/features/svl_fix_je/models.py`, or `smartscc_tools/features/svl_fix_je/config.py`, update `docs/balance_cycle_pembelian.md` in the same change.
- When porting a standalone tool from another repo, keep business logic in a dedicated package under `smartscc_tools/features`, keep the shell adapter thin under `smartscc_tools/modules`, and keep source templates/assets under `build_tools/assets/` (packaged as `assets/`).
- Odoo-backed modules should use the shared database-profile dropdown pattern rather than free-text DB overrides or hardcoded per-module database lists.
- Shared database profiles live in global settings and carry `database_value`, `alias`, and `note`; dropdown labels should render as `Alias - Value [Note]` with sensible fallbacks.
- Module-level database selectors should include the synthetic `Follow Global Default` choice, and the global default selector should include `Use GAS Default`.
- If a module needs limited database routing, add a small module-level selector but still reuse the shared GAS-backed credentials/runtime path and the shared profile resolver.
- Prefer Odoo schema compatibility via field detection and fallbacks instead of assuming one fixed model layout.
- Known environment-safe field expectations for this repo include `stock.move.product_qty`, `stock.move.line.qty_done`, and `stock.valuation.layer.date` as a fallback when `accounting_date` is unavailable.
- Default tests use `unittest` with fake clients or service doubles. Do not rely on live Odoo access as the baseline test path.
- When a task changes user-facing UI behavior in this repo, such as layout, scrolling, rendering, selection, widget state, dialog flow, preview, or other visible interaction, run a targeted manual GUI smoke test before reporting completion if the current environment can launch the app.
- For this repo's developer manual GUI smoke test, launch with `python main.py` and verify the changed surface directly; `python main.py gui` is only a backward-compatible alias.
- Manual GUI smoke testing supplements `unittest`; it does not replace automated tests.
- If manual GUI smoke testing is blocked by environment, login, missing dependency, or inability to reach the changed UI path, state that blocker explicitly in the final response instead of implying the manual check was done.
- For this repo, verification of GUI or backend changes that affect user-visible data must not stop at "window opens" or "widget exists". Trigger the relevant buttons/actions, wait for the real workflow to finish, and inspect the actual rendered interface data that the user would see.
- When the changed behavior affects analytical tables, sidebars, detail panes, or other data surfaces, verify the contents of those UI surfaces directly. Use actual Tk widget state, structured dumps, or equivalent repo-native inspection hooks rather than assuming the backend snapshot alone proves the GUI output.
- For dashboard or live-analysis self-diagnose work, do not rely on blind GUI launch as the primary verification path when the user can describe the exact GUI inputs. Prefer replaying the same request headlessly from repo code, using the same resolved database profile and module state as the GUI.
- For `SVL Fix JE` dashboard analyze repro, prefer `python main.py svl-dashboard-analyze` with the same effective inputs as the GUI state. When table parity matters, dump the resulting snapshot to JSON and inspect backend structures such as `purchase_cycles`, `item_rows`, `account_rows`, `raw_lines`, `adjustment_audit_rows`, `case1_link_rows`, and `case2_repair_rows` before claiming what the GUI should show.
- When investigating GUI-vs-backend mismatches in this repo, first confirm whether the service snapshot is correct; only after that should Tkinter rendering or selection logic be treated as the likely fault surface.
- For `SVL Fix JE` GUI validation where parity with the on-screen interface matters, prefer `python main.py svl-dashboard-gui-inspect` or an equivalent widget-state inspection path so the agent can read sidebar rows, selected detail tables, and displayed headers from the actual GUI and discuss their contents with the user.
- For `SVL Fix JE` `PCB Repair Collection` inspection, holder design, or dialog-parity verification, use the repo-local skill `smartscc-pcb-repair-collection-inspector` and prefer `python main.py svl-dashboard-gui-inspect --collect-pcb-visible --open-pcb-repair-dialog ...` so the collection rows and live dialog state are both available.

## Knowledge Hygiene

- Sebelum mengubah aturan bisnis PCB agar tes lulus, cocokkan dokumentasi repo dengan keputusan lintas AI di `C:/Users/User/.ai-shared/memories/projects/SmartsCCToolsMaster/architecture.md` dan sumber historis yang dirujuknya. Tes lama tidak otomatis mengalahkan keputusan bisnis yang lebih baru. Bila konflik belum terselesaikan, nyatakan konflik dan jangan mengabadikan salah satu versi sebagai aturan yang disetujui.
- Angka hasil tes, hash installer, status pemasangan, dan bukti GUI disimpan dalam laporan sesi (`docs/repo_consolidation.md` / `output/maintenance/`), bukan menjadi aturan permanen AGENTS.md. Kelulusan teknis, persetujuan aturan bisnis, dan verifikasi Odoo live adalah bukti yang berbeda.
- Do not turn temporary runtime facts into permanent repo instructions. Examples that must stay out of reusable memory include transaction numbers, company IDs, one-off mismatch outputs, screenshots, and whichever database is active today.

## Repo-local skills

- `smartscc-tools-module-pattern`: use when adding or refactoring modules in Smart's CC Tools Master.
- `odoo-async-compat-service-pattern`: use when implementing or fixing async Odoo services with schema fallbacks.
- `smartscc-windows-release-maintainer`: use when building the Windows installer, updating shipped branding assets, fixing title bar/taskbar/Alt+Tab branding, or troubleshooting the installed Windows app for this repo.
- `smartscc-pcb-repair-collection-inspector`: use when inspecting `PCB Repair Collection`, comparing collection rows vs dialog state, or designing/verifying PCB holder and planned-line behavior from actual collection output.

