# Konsolidasi Tools Master

**Aturan Case 9 telah dikonfirmasi pengguna:** downstream dengan saldo HPP
dan Persediaan memakai lima baris, clearing nol, dan selisih masuk HPP kategori.
Keputusan ini menggantikan perbaikan empat baris yang sempat mengikuti tes lama.
Tes tersebut diperbarui untuk membuktikan aturan bisnis yang dikonfirmasi,
termasuk arah saldo terbalik dan guard bila projected clearing belum nol.
Laporan verifikasi aturan final berada di `output/maintenance/case9-confirmed/`.

`Smarts CC Tools Master` adalah satu lokasi source dan environment aplikasi.
Lokasi canonical sekarang langsung di dalam `! Asset Development Template HPP`,
sejajar dengan repo `Smarts CC`. Folder standalone lama di `Tools OdooXPython`
sudah dikonsolidasikan:

| Folder lama | Lokasi sekarang |
| --- | --- |
| `itemJournalOdoo` | Junction lama ke repo ini dilepas; entrypoint tetap `main.py`. |
| `editDate_stock_move` | Modul aktif `smartscc_tools/features/edit_transaksi/`. |
| `svl_fix_no_STJ` | Modul aktif `smartscc_tools/features/svl_fix_je/`. |
| `update_std_cost` | Modul aktif `smartscc_tools/features/update_std_cost/`. |
| `fix_svl-unmatched_odoo` | Source historis dalam arsip lokal; belum menjadi modul dashboard. |
| `checkaccountingdate` | Folder kosong dihapus. |

Kode lama yang berbeda, referensi VBA, README, dan tes lama dipertahankan dalam
`output/archive/legacy_tools/source_history.zip`. Arsip ini bukan source runtime
dan tidak dikemas ke installer. Khusus `fix_svl-unmatched_odoo`, perilakunya
membuat stock move dan memicu ulang valuasi; jangan menganggapnya sama dengan
repair journal pada modul aktif atau menjalankannya otomatis.

Workbook dan konfigurasi lokal unik berada di
`output/archive/legacy_tools/<folder_asal>/`. Log eksekusi lama berada di
`logs/manual_odoo/archive/svl_fix_no_STJ/`. File konfigurasi lama dapat mengandung
kredensial; seluruh arsip ini tetap lokal dan diabaikan Git. Environment, cache,
hasil build, launcher dan konfigurasi editor lama yang sudah tidak dipakai
dihapus. Aplikasi aktif memakai `.venv/` dan konfigurasi GAS/profil globalnya.

Ikon dan template source ada di `build_tools/icon/` dan `build_tools/assets/`.
Build mengambil source dari root repo dan mengemas aset menjadi `icon/` dan
`assets/` di dalam runtime installer. Jalur source dan frozen runtime ditentukan
oleh `smartscc_tools/branding.py`; tidak diperlukan junction atau import dari
folder standalone lain.

`Smarts CC Approval Master` tetap merupakan proyek terpisah. Referensi Odoo tetap
mengikuti pointer `Repo Odoo Holywings` dalam AGENTS.md.

Manifest pemindahan, verifikasi SHA256 arsip, identitas file data yang dipindahkan,
dan log verifikasi lokal berada di `output/maintenance/consolidation/`.

Verifikasi awal konsolidasi mencakup GUI source, navigasi empat modul, tombol template
(workbook diperiksa read-only, tanpa meluncurkan Excel), serta startup/penutupan
executable hasil build. Suite penuh menjalankan 638 tes: 630 lulus dan 8 gagal
pada logika SVL yang source dan tesnya tidak berubah dari audit sebelumnya.
Build normal berhenti pada gate tersebut. Installer lokal kemudian dibuat dengan
`-SkipTests` untuk memverifikasi packaging secara terpisah; payload installer
dicocokkan terhadap executable dan aset hasil build. Ini bukan pernyataan bahwa
seluruh fungsi SVL sudah lolos validasi. Installer tidak dipasang atau dipublikasikan.

## Tindak lanjut relokasi dan regresi SVL — 2026-10-05

Seluruh 347 file repo dipindahkan ke lokasi baru dengan pemeriksaan inventaris
path relatif dan ukuran. `.venv` dibuat ulang; 30 versi dependency dipertahankan
dan `pip check` lulus. Cache Python/pip/pytest sekarang berada di
`%LOCALAPPDATA%/SmartsCCTools/cache`, di luar OneDrive. Cache Python di dalam repo
sebelumnya mengulang path sumber absolut sehingga melewati batas path OneDrive.
Konfigurasi editor lokal dan path workbook tersimpan sudah mengikuti lokasi baru.

Delapan tes gagal sebelumnya ditangani melalui perbaikan runtime dan
penyesuaian tes lama setelah rekonsiliasi keputusan bisnis lintas AI:

- Guard GRNI tetap menolak saldo proporsional tanpa bill sebagai dasar repair,
  tetapi tidak lagi menutupi evidence value gap Case 8/9 yang independen dari bill.
- Bill belum lunas/belum matching dan payment belum reconcile kembali berstatus
  `partial`, dengan kelompok follow-up yang sesuai.
- Holder downstream Case 9 memakai lima baris: clearing nol dan selisih ke HPP.
  Dua tes lama yang meminta empat baris diperbarui sesuai keputusan pengguna.
  Preview tidak lagi mengabaikan residual clearing pada rencana yang belum benar.

Suite penuh kini **640 tes lulus**, termasuk dua regresi tambahan untuk guard
GRNI tanpa gap dan keseimbangan jurnal Case 9 pada arah saldo berbeda. GUI aktual
`python main.py` diperiksa menggunakan fixture offline: grouping lima cycle,
aksi collect Case 9, pemilihan baris dialog, lima planned lines, projected saldo,
dan kewajiban review. Pemeriksaan ini tidak menjalankan transaksi Odoo live.
Bukti relokasi awal berada di `output/maintenance/relocation/`; bukti tes dan GUI
untuk aturan lima baris final berada di `output/maintenance/case9-confirmed/`.

Build sebelum penetapan aturan final telah lulus gate 640 tes, tetapi artefak
empat baris tersebut telah digantikan installer yang memakai aturan lima baris.
Build normal melalui `build_tools/build_installer.ps1 -Version 1.0.0` lulus
gate seluruh 640 tes tanpa `-SkipTests`. Executable hasil build berhasil dibuka
dan ditutup; ikon, template, serta payload installer cocok dengan hasil build.
Installer akhir berukuran 39.518.208 byte, SHA256
`0dfa1b18b5e596c45a0b53fe1dfcab58cbf38b8d77b209a0d93a4223e90c6d32`.
Manifest `latest.json` cocok dengan hash tersebut. Laporan dan bukti terbaru
berada di `output/maintenance/case9-confirmed/`.
Installer belum dipasang atau dipublikasikan; build lokal ini tidak membuktikan
hasil transaksi Odoo live. Direktori lama kosong dapat masih tertahan handle
sesi editor.

Workspace baru telah dibuka dan Explorer VS Code menampilkan source dari lokasi
baru. Cwd bawaan chat yang dilanjutkan tetap dapat mengarah ke direktori lama;
operasi repo harus menggunakan workdir baru secara eksplisit. Ini terpisah dari
status folder yang terlihat di jendela VS Code.
