# New JE + Relink SVL melalui CLI

Tool ini tersedia langsung dalam source Tools Master:

```cmd
call .venv\Scripts\activate.bat
python main.py svl-new-je-relink --help
```

Jalankan dari root clone setelah menyiapkan `.venv` dan akses GAS/Odoo sesuai
[github_handoff.md](github_handoff.md). Tidak memerlukan server MCP atau membuka
GUI. Koneksi memakai database profile bersama dan jalur credential aplikasi.

Gunakan untuk **SVL tanpa JE**, atau **SVL yang terhubung ke header JE kosong**.
Tool memakai `SvlDashboardRepairServiceAsync`, yaitu service repair yang juga
dipakai GUI. JE baru mempunyai dua line debit/kredit, kemudian ditautkan melalui
`stock.valuation.layer.account_move_id`. Header JE lama yang kosong tetap ada.

## 1. Buat preview

Resolve company dan SVL pada database yang benar. Gunakan inspector repo pada
`tools/odoo_inspector/` bila perlu menelusuri picking ke stock move dan SVL;
panduannya ada di
[skill inspector lokal](../.codex/skills/smartscc-odoo-live-inspector/SKILL.md).
ID company/SVL dari mesin atau database lain tidak boleh dianggap sama.

Contoh Command Prompt berikut memakai placeholder yang harus diganti:

```cmd
call .venv\Scripts\activate.bat
set "PCB_DATABASE_PROFILE=db_live"
set "PCB_COMPANY_ID=ID_COMPANY_HASIL_PENCARIAN"
set "PCB_SVL_ID=ID_SVL_HASIL_PENCARIAN"
set "PCB_JOURNAL_DATE=YYYY-MM-DD"
set "PCB_DEBIT_COA=KODE_AKUN_DEBIT"
set "PCB_CREDIT_COA=KODE_AKUN_KREDIT"
python main.py svl-new-je-relink --database-profile-id "%PCB_DATABASE_PROFILE%" --company-id %PCB_COMPANY_ID% --svl-id %PCB_SVL_ID% --date "%PCB_JOURNAL_DATE%" --debit-account-code "%PCB_DEBIT_COA%" --credit-account-code "%PCB_CREDIT_COA%" --output-json "logs\manual_odoo\new_je_preview.json"
```

Tanpa `--apply`, semua panggilan Odoo hanya membaca. Output JSON berstatus
`preview` menyimpan database efektif, company, produk, sumber SVL, nominal,
tanggal, akun debit/kredit beserta nama, journal, reference, dan posting mode.

Pilihan tambahan:

| Argumen | Perilaku |
| --- | --- |
| `--journal-code` | Default `STJ`; di-resolve pada company target |
| `--posting-mode draft` | Default; apply membuat JE draft dan menautkan SVL |
| `--posting-mode post` | Apply membuat, menautkan, lalu mem-posting JE |
| `--reference` / `--line-label` | Override teks yang akan direview pada preview |
| `--log-file` / `--verbose` | Pengaturan log; tidak mengubah rencana jurnal |
| `--output-json` | Path baru untuk bukti; file existing tidak ditimpa |

Reference dan Label Line default mengikuti **Reference Prefix** tersimpan pada
konfigurasi `SVL Fix JE` di komputer tersebut, dengan default repo jika belum ada
konfigurasi. Reference juga memuat sumber SVL dan marker percobaan repair.

Nominal diambil dari **nilai SVL saat ini**, bukan nominal PO/Bill dan bukan input
amount bebas. Debit/kredit memakai nilai absolutnya. Untuk SVL bernilai negatif,
pilih arah akun debit/kredit sesuai transaksi dan periksa signed value pada
preview; tool tidak menukar akun secara otomatis. Verifikasi nilai SVL dan akun
sebelum menyetujui rencana. Tool ini tidak mengubah cost, quantity, atau nilai SVL.

Tanggal journal wajib eksplisit, termasuk bila tanggal receipt berbeda.
`--posting-mode post` harus dipilih saat membuat preview jika hasil akhir yang
diminta adalah posted. Draft Only belum memulihkan saldo ledger posted.

## 2. Apply rencana yang disetujui

Tunjukkan preview ke pengguna dan peroleh persetujuan untuk rencana transaksi
tersebut. Permintaan menyediakan/menguji tool bukan izin menulis transaksi live.
Sesudah disetujui, jalankan:

```cmd
python main.py svl-new-je-relink --apply --preview-json "logs\manual_odoo\new_je_preview.json" --output-json "logs\manual_odoo\new_je_result.json"
```

Apply mengambil parameter dari file preview; jangan menambahkan override company,
SVL, tanggal, akun, atau posting mode. Jangan edit preview secara manual. Untuk
mengubah rencana, buat dan review preview baru dengan path baru.

Sebelum write, service membaca ulang sumber dan konfigurasi akun/journal.
Koneksi Odoo, database efektif, atau data rencana yang berubah membuat apply ditolak. Tanggal
di dalam lock date company juga ditolak, termasuk untuk Draft Only. Tidak ada
opsi melewati lock date.

Tool menolak JE existing yang sudah berisi line serta mendeteksi jurnal lain
melalui stock move dan sibling SVL. Reference marker mendeteksi JE dari percobaan
CLI sebelumnya agar retry tidak otomatis membuat duplikasi. Apply pada SVL yang
sama dikunci antarproses CLI di komputer yang sama; jangan menjalankan repair
SVL yang sama bersamaan melalui GUI atau komputer lain.

Setelah eksekusi, tool membaca ulang link SVL, header JE, dan journal lines untuk
memastikan company, journal, tanggal, state, produk, akun, serta nominal sesuai
preview. Nomor JE resmi dikembalikan melalui `result.results[].move_name`;
`move_id` adalah informasi diagnostik.

## Hasil dan pemulihan kegagalan

- Exit code `0` berarti preview berhasil atau eksekusi selesai sesuai verifikasi.
- Exit code `1` berarti preflight/eksekusi/verifikasi gagal; baca JSON dan log.
- Exit code `2` berarti argumen CLI tidak valid atau output sudah ada.
- Status `applying` ditulis sebelum write. Jika proses terputus atau pembuatan
  JE berhasil tetapi relink/post/verifikasi gagal, sebagian perubahan dapat
  sudah terjadi. Review JE existing dan link SVL sebelum retry.
- Status `executed` dengan `posting_mode = draft` berarti JE masih draft.
  Periksa `result.results[].posted` untuk memastikan status posting.
- Simpan preview, hasil, dan log di `logs/manual_odoo/`; jangan commit data produksi.

Setelah JE posted, jalankan ulang analisis PCB bila perlu memperbarui laporan.
Bill yang belum tersedia tetap memerlukan proses pembelian tersendiri. CLI ini
tidak mengubah aturan klasifikasi atau perbaikan Case 1–9 pada PCB.

## Pemeriksaan pengembangan

Baseline memakai fake clients, tidak menulis Odoo live:

```cmd
python -m unittest tests.test_svl_new_je_relink_cli tests.test_svl_dashboard_repair_service -q
```

Perubahan ini berupa tool CLI dari source. Tidak memerlukan dependency baru,
server MCP, atau build installer untuk menjalankan perintah `python main.py`.
