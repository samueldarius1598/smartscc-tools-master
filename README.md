# Smart's CC Tools Master

Platform tools modular untuk workflow Odoo, dengan module `Internal Transfer - Odoo`, `Edit Transaksi Item Movement - Odoo`, `Fixing Unlink SVL - Odoo`, dan `Update Standard Cost Item - Odoo`.

Source tersedia di repo publik [samueldarius1598/smartscc-tools-master](https://github.com/samueldarius1598/smartscc-tools-master).
Untuk menggunakan atau memodifikasi dari komputer lain, baca
[panduan GitHub dan AI Agent](docs/github_handoff.md). Akses GitHub dan API key
GAS/Odoo disiapkan terpisah; credential tidak disertakan dalam source.

## Fitur
- `check-lock`: baca company aktif di sheet `Item Journal`, fetch `fiscalyear_lock_date` (read-only, tanpa ubah workbook).
- `upload`: precheck global, create internal transfer per batch (tanpa append ke picking lama), retry + adaptive split (non-timeout create), validate + date sync, mapping STJ, writeback hasil.
- Engine runtime: `async` only.
- Satu group data dapat menghasilkan beberapa `stock.picking`; kolom `L` diisi nomor picking aktual per baris.
- Preset produksi `safe-fast` default `TRANSACTION_VOLUME_PER_BATCH=150` (blast radius timeout lebih kecil).
- Guard produksi: upload non-dry-run otomatis menjaga mode remap konservatif internal.
- Stop aman `run-to-batch-stop` untuk upload.
- Audit item-level JSONL + sheet `Item Journal Audit`.
- Realtime perf debug report v2 (`1 second matters`) untuk engine async.
- Default output difokuskan ke workbook Excel (sheet audit + perf summary), tanpa file log tambahan.
- Detail teknis `PERF_ALERT` dipisah ke channel teknis (hanya aktif bila `--log-file` diisi).
- Proteksi save copy untuk batas path Excel (auto short-name + fallback temp folder bila path terlalu panjang).
- Migrasi audit lama ke schema v2 via command `migrate-audit`.
- GUI: pilih workbook, profil database shared per module, save mode, preset, perf profile, business constants panel, log realtime, progress detail, tombol stop.
- Settings menyediakan `Database Profiles` untuk tambah/edit/hapus daftar database Odoo bersama dan menentukan global default yang dipakai semua module Odoo.
- Progress upload menampilkan event transfer-level (`TRANSFER_START/PROGRESS/DONE`) dengan konteks bisnis company + jumlah item.
- CLI interaktif memakai progress bar modern (`rich`) dengan fallback plain text untuk non-TTY.

## Lokasi workspace

Repo berada langsung di `! Asset Development Template HPP/Smarts CC Tools Master`,
sejajar dengan repo `Smarts CC`. Buka folder baru ini di editor. Jika repo
dipindah lagi, buat ulang `.venv` dan jalankan konfigurasi cache; jangan memakai
launcher environment dari lokasi lama.

## Install
```cmd
python -m venv .venv
call .venv\Scripts\activate.bat
python tools\configure_dev_env.py
python -m pip install -r build_tools\requirements.txt
```

Gunakan satu environment lokal `.venv/`; jangan memindahkan environment dari
folder proyek lain. Cache pengembangan berada di
`%LOCALAPPDATA%/SmartsCCTools/cache/{python,pip,pytest}`, di luar OneDrive. Konfigurasi ini berlaku untuk interpreter
`.venv`, tanpa mengubah pengaturan Python global. Setelah membuat ulang
environment, jalankan kembali `python tools\configure_dev_env.py`.

Hasil ekspor disimpan di `output/`, arsip investigasi di `output/archive/`,
dan bukti koreksi Odoo di `logs/manual_odoo/`. `scratch/` hanya untuk pekerjaan
sementara. Cache eksternal boleh dibersihkan saat proses development sudah berhenti;
`.venv/` berisi dependency aplikasi dan bukan folder cache.

Folder tool standalone lama sudah dikonsolidasikan ke repo ini. Lihat
[peta konsolidasi](docs/repo_consolidation.md) untuk lokasi modul aktif dan arsip
unik yang dipertahankan. Aplikasi tidak memerlukan folder `itemJournalOdoo`
atau junction ke nama lama.

## Build Installer Windows
Target distribusi untuk user non-teknis:
- hasil akhir berupa satu file installer `.exe`
- nama shortcut desktop: `Smart's CC Tools Master`

Prasyarat builder:
- Windows
- Python aktif di `.venv` atau `PATH`
- build akan memakai folder temp yang path-nya pendek agar aman dari masalah Windows long path
- installer satu file dibuat memakai `IExpress` bawaan Windows

Build:
```powershell
.\build_tools\build_installer.ps1 -Version 1.0.0 -GitHubRepo your-org/your-repo
```

Opsional, bila ingin skip unit test saat build:
```powershell
.\build_tools\build_installer.ps1 -Version 1.0.0 -GitHubRepo your-org/your-repo -SkipTests
```

Output installer:
```text
build\release\installer\SmartsCC-ToolsMaster-Setup-1.0.0.exe
```

Output manifest update:
```text
build\release\installer\latest.json
```

Installer akan:
- install aplikasi GUI ke `%LOCALAPPDATA%\Programs\Smart's CC Tools Master`
- membuat shortcut desktop `Smart's CC Tools Master`
- membuat shortcut Start Menu dengan nama yang sama
- dapat dipakai lagi untuk upgrade versi berikutnya di lokasi install yang sama

## Jalankan
### GUI
Mode developer saja:
```cmd
python main.py
```

Alias backward-compatible:
```cmd
python main.py gui
```

Untuk user akhir, jalankan aplikasi dari shortcut hasil installer agar icon taskbar dan alur update memakai mode distribusi yang didukung.

### Check Lock Date
```cmd
python main.py check-lock --workbook \"C:\\path\\file.xlsm\"
```
Catatan: command `check-lock` bersifat read-only; `--save-mode` diabaikan.

### Upload Internal Transfer
```cmd
python main.py upload --workbook \"C:\\path\\file.xlsm\" --save-mode ask --preset safe-fast --perf-profile aggressive --perf-sheet on
```

### Migrasi Audit ke v2
```cmd
python main.py migrate-audit --input \"logs\\audit_legacy.jsonl\" --output \"logs\\audit_v2.jsonl\"
```

## Database Profiles
- Semua module Odoo memakai dropdown database shared, bukan field `DB Override` bebas.
- Pilihan per module selalu menyertakan `Follow Global Default`.
- Settings menyediakan `Use GAS Default` sebagai global default bawaan, plus daftar profile editable dengan format label `Alias - Database [Note]`.

## Handoff Notes
- Untuk sesi baru yang melanjutkan mode dashboard `Saldo Akun per Item`, baca dulu [docs/svl_fix_je_account_balance_handoff.md](docs/svl_fix_je_account_balance_handoff.md).

## App Update
- Aplikasi GUI punya panel `Settings > Application Update`.
- Default: check update otomatis saat startup maksimal 1x per 24 jam.
- Jika ada versi baru, app bisa auto-download installer lalu menyiapkan tombol `Install Update & Restart`.
- Source update dibaca dari manifest `latest.json` GitHub Releases yang URL-nya diambil dari constant updater atau env `SMARTSCC_UPDATE_MANIFEST_URL`.

## Opsi Penting
- `--db-override <db_name>`
- `--save-mode in-place|copy|ask`
  - Mode `copy` otomatis pakai nama: `[Company Name] - [DD-MM-YY HH.MM.SS] - [Jumlah baris yang diproses]`.
- `--preset safe-fast|safe|fast|debug|custom`
- `--set KEY=VALUE` (Business Keys only, bisa diulang)
- `--max-concurrency <int>`
- `--stop-mode run-to-batch-stop`
- `--audit-level item|summary`
- `--audit-file <path>` (opsional, jika ingin simpan JSONL)
- `--perf-profile aggressive|balanced|conservative` (upload async)
- `--perf-report-file <path>` (opsional, jika ingin simpan file perf JSONL/summary/schema)
- `--perf-sheet on|off` (upload async)
- `--dry-run` (khusus upload)
- `--log-file <path>`
- `--verbose`

## STJ Gate (Async)
- Default: `STJ_REQUIRED=true`.
- Jika STJ masih kosong setelah recovery timeout, baris sukses **tetap** sukses (kolom `L` tidak diubah), kolom `N` ditambah tag `STJ_TIMEOUT_WARNING[...]`.
- Polling STJ menggunakan hybrid quick + exponential backoff.
- Tuning business keys untuk jurnal:
  - `MAX_JOURNAL_VERIFICATION_ATTEMPTS` (default `8`)
  - `WAIT_BETWEEN_JOURNAL_VERIFICATION_MS` (default `1000`)
  - `MAX_WAIT_TIME_FOR_JOURNAL_CREATION_MS` (default `600000`)
  - `WAIT_BETWEEN_JOURNAL_RECOVERY_CHECKS_MS` (default `2000`)
  - `INCLUDE_RELATED_TRANSFERS_IN_JOURNAL_CHECK` (default `true`)
- Technical remap tetap dijalankan internal (tidak diekspos sebagai business key).
- Guard validasi + cleanup:
  - `VALIDATE_BACKORDER_POLICY` (default `fail`; opsi `create_backorder|cancel_backorder|fail`)
  - `VALIDATE_ENFORCE_DONE_QTY` (default `true`)
  - `VALIDATE_CLEANUP_POLICY` (default `cancel_then_keep`)
- Rule ekspektasi jurnal:
  - `JOURNAL_EXPECTATION_MODE` (default `hybrid`)
- Guard konfigurasi production:
  - Jika upload dijalankan dengan `dry_run=false`, sistem otomatis menjaga remap konservatif internal.
  - Event audit: `CONFIG_GUARD_REMAP_FORCED` (message memuat `old_mode`, `new_mode`, `dry_run`, `reason=production_guard`).
- Event audit/perf tambahan:
  - `STJ_BACKORDER_SCOPE_RESOLVED`
  - `STJ_REMAP_APPLIED`
  - `STJ_REMAP_FAILED`
  - `STJ_REQUIRED_WARN`
  - `ROW_WARN`
  - `BATCH_SIGNATURE_DEFERRED`

Contoh:
```bash
python main.py upload --workbook "C:\path\file.xlsm" --set MAX_JOURNAL_VERIFICATION_ATTEMPTS=8 --set WAIT_BETWEEN_JOURNAL_VERIFICATION_MS=1500
```

## Create Timeout Retry-Only + No-Split Signature (Async)
- Untuk `stock.picking.create timeout`, sistem jalankan reconcile idempotensi lalu retry payload yang sama (`retry-only`) sebanyak 3x dengan delay 2000ms.
- Timeout create tidak memicu split batch.
- Adaptive split tetap dipakai hanya untuk error non-timeout create.
- Batching memakai soft-boundary signature internal (`product + company + date + src + dest`):
  - jika `BATCH_LIMIT` tercapai tapi baris berikutnya masih signature sama, submit ditunda;
  - flush dilakukan saat signature berubah atau group selesai.
- Saat timeout create, sistem melakukan reconcile `stock.picking` berdasarkan key idempotensi di `origin` untuk mencegah duplicate create.
- Mode `origin`:
  - `CREATE_ORIGIN_MODE=technical` (default): `origin` berisi key teknis.
  - `CREATE_ORIGIN_MODE=human_suffix`: `origin` menjadi `{CREATE_ORIGIN_HUMAN_REF} [ID:{key}]`.
  - Jika `CREATE_ORIGIN_HUMAN_REF` kosong, mode `human_suffix` fallback otomatis ke `technical`.

Contoh untuk company lambat:
```bash
python main.py upload --workbook "C:\path\file.xlsm" --set TRANSACTION_VOLUME_PER_BATCH=150 --set MAX_RETRY_FOR_CREATE_TIMEOUT=3 --set WAIT_BETWEEN_CREATE_TIMEOUT_RETRIES_MS=2000
```

## Format Report v2
Catatan: file report v2 hanya dibuat jika `--perf-report-file` diisi.
- `logs/perf_events_v2.jsonl`: satu event JSON per baris.
- Mandatory key event:
  - `schema_version`
  - `run_id`
  - `event_id`
  - `timestamp_utc`
  - `event_type`
  - `stage`
  - `severity`
  - `cause_code`
  - `rpc_model`
  - `rpc_method`
  - `duration_ms`
  - `batch_seq`
  - `row_number`
- `logs/perf_summary_v2.json`: ringkasan bottleneck, top RPC latency, retry/split timeline.
- `logs/perf_schema_v2.json`: JSON Schema resmi untuk validasi parser.

## Testing
```bash
python -m unittest discover -s tests -v
```
