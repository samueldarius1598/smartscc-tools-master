# Menarik laporan Excel PCB

PCB berarti **Purchase Cycle Balance**, yang disebut **Balance Cycle Pembelian**
di modul `SVL Fix JE`. Analisisnya menelusuri penerimaan/GR, STJ, vendor bill,
payment, jurnal bank, serta adjustment terkait. Menarik laporan adalah operasi
baca; tidak perlu menjalankan repair, membuat journal, atau mem-posting dokumen.

Panduan ini mencatat cara memperoleh laporan agar dapat diikuti pada clone di
komputer lain. ID company, credential, dan hasil suatu penarikan bukan
konfigurasi permanen repo.

## Tool yang digunakan

| Kondisi | Tool |
| --- | --- |
| Agen memiliki koneksi MCP Smart's CC | `purchase_cycle_bill_report`, setelah memastikan database dan company |
| Ada akses app lokal, tanpa MCP | `python main.py` → `SVL Fix JE` → `Balance Cycle Pembelian` → ekspor seluruh cycle |
| Perlu berjalan tanpa GUI atau memantau analisis panjang | Service `SvlDashboardServiceAsync` dan exporter `export_pcb_company_audit_excel`, sesuai contoh di bawah |
| Sudah ada snapshot lengkap dari permintaan yang sama | Gunakan JSON lokal untuk ekspor/verifikasi; tidak perlu membaca Odoo lagi |

MCP merupakan layanan eksternal. Clone repo ini tidak memasang server
`smarts_cc_mcp`, mendaftarkan tool-nya pada agen, atau memberikan akses Odoo.
Gunakan MCP bila tersedia; jika tidak, siapkan konfigurasi lokal sesuai
[github_handoff.md](github_handoff.md). Jangan menyalin environment, path absolut,
atau credential komputer pemilik.

## Jalur utama: MCP resmi

Temukan tool dari server `smarts_cc_mcp` di lingkungan agen. Sebagian client
menampilkan nama seperti `mcp__smarts_cc_mcp__purchase_cycle_bill_report`;
nama fungsionalnya adalah `purchase_cycle_bill_report`. Baca schema tool sebelum
memanggilnya: server dapat berkembang terpisah dari clone ini.

1. Panggil `list_databases({})` untuk mengetahui profile yang tersedia.
2. Panggil `list_companies` dengan profile tersebut dan pencarian nama outlet.
3. Pilih company yang tepat dari hasil pencarian, bedakan outlet dengan nama
   mirip. Untuk Atlas, Beach Club, Night Club, dan Bangkok adalah scope berbeda.
4. Panggil `purchase_cycle_bill_report` dengan `company_id` tersebut dan profile
   yang sama.
5. Baca hasilnya, ambil file melalui mekanisme yang dikembalikan tool, lalu
   periksa scope sebelum menyerahkan Excel. Path file di server belum membuktikan
   bahwa file tersedia di komputer agen.

Contoh argumen, dengan ID dari hasil pencarian sebelumnya:

```javascript
// list_databases
{}

// list_companies
{ database_profile_id: "db_live", query: "atlas" }

// purchase_cycle_bill_report
{
  database_profile_id: "db_live",
  company_id: COMPANY_ID_HASIL_PENCARIAN,
  output_format: "both"
}
```

Gunakan `db_live` untuk penarikan operasional dan `db_dummy` untuk eksperimen,
setelah memastikan profile tersedia. `list_databases` dan `list_companies` tidak
menerima `company_id`; report menerima kedua identifier secara eksplisit.

**Jangan isi `date_from` atau `date_to` jika pengguna tidak meminta periode.**
Pada MCP, tanggal yang tidak diisi berarti semua data. Jika diminta, gunakan
`YYYY-MM-DD`; kedua field ini memfilter tanggal GR. Tanpa `bills`, report mencakup
seluruh company; `bills: ["NOMOR_RESMI_BILL"]` membatasi bill tertentu.
`output_format` menerima `xlsx`, `json`, atau `both`. Gunakan `both` jika juga
membutuhkan hasil terstruktur untuk audit. Jangan menganggap JSON MCP mempunyai
schema yang sama dengan snapshot internal dashboard.

Jika terjadi timeout `tools/call`, catat kegagalannya. Timeout tidak membuktikan
bahwa proses di server berhenti. Jangan terus mengirim ulang penarikan berat yang
sama; periksa apakah server menyediakan hasil/job yang dapat diambil kembali
sebelum memakai fallback lokal. Fallback tidak dijamin lebih cepat.

## Fallback melalui app lokal

Setelah `.venv` dan akses GAS/Odoo dikonfigurasi sesuai handoff, jalankan dari
root clone melalui Command Prompt:

```cmd
call .venv\Scripts\activate.bat
python main.py
```

Buka `SVL Fix JE`, periksa database efektif pada `Konfigurasi`, lalu pilih company
dan mode `Balance Cycle Pembelian`. Periksa tanggal yang terlihat: kosongkan
untuk semua data atau isi hanya periode yang diminta. Periksa juga filter akun
serta pilihan inventory/non-inventory. Jalankan analisis dan tunggu snapshot
akhir. Gunakan ekspor seluruh cycle company, dengan judul dialog
`Download Excel Audit Semua Cycle Pembelian (Company Level)`.
Simpan di `output/`, dalam subfolder khusus penarikan tersebut.

Tombol ini memanggil
[`export_pcb_company_audit_excel`](../smartscc_tools/features/svl_fix_je/dashboard_pcb_export.py)
melalui `_export_pcb_company_excel` di
[`svl_fix_je_dashboard_page.py`](../smartscc_tools/modules/svl_fix_je_dashboard_page.py).
Format native saat ini mempunyai lima sheet:

1. `1. Cycle Overview`
2. `2. Item Detail`
3. `3. Akun Non-Std`
4. `4. Raw Ledger`
5. `5. Raw Ledger PO Bill`

[`export_pcb_excel.py`](../export_pcb_excel.py) di root adalah exporter standalone
lama, dengan layout berbeda dan output di sebelah script. Untuk mengikuti tombol
app saat ini, gunakan `export_pcb_company_audit_excel`.

## Fallback tanpa GUI: analisis sekali, simpan, lalu ekspor

Perintah yang sudah tersedia, `python main.py svl-dashboard-analyze`, menjalankan
analisis dan menerima `--dump-json`, tetapi belum mempunyai opsi langsung untuk
ekspor Excel ini. Periksa `--help` dan source
[`dashboard_cli.py`](../smartscc_tools/features/svl_fix_je/dashboard_cli.py).

**CLI mengambil tanggal tersimpan dari GUI ketika `--date-from`/`--date-to`
tidak dikirim atau kosong.** Jangan menganggapnya sebagai permintaan semua data.
Runner berikut mengisi tanggal langsung pada request di memori, tanpa mengubah
settings tersimpan. Pilihan lainnya tetap mengikuti defaults/settings dashboard;
periksa atau kirim override yang didukung parser untuk menyamakan konfigurasi
antarlingkungan. Contoh ini mencakup seluruh company; tidak menerapkan filter
`bills` milik MCP.

Buat file sementara `scratch/pcb_company_export.py` di root clone dengan isi:

```python
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # file berada di scratch/
sys.path.insert(0, str(ROOT))
from smartscc_tools.features.svl_fix_je import dashboard_cli as cli
from smartscc_tools.features.svl_fix_je.dashboard_pcb_export import (
    export_pcb_company_audit_excel,
)

args = cli._build_parser().parse_args()
if args.company_id <= 0 or not args.database_profile_id or not args.dump_json:
    raise SystemExit("Isi --company-id, --database-profile-id, dan --dump-json.")
dump = Path(args.dump_json).expanduser().resolve()
xlsx = dump.with_suffix(".xlsx")
if dump.suffix.lower() != ".json" or dump.exists() or xlsx.exists():
    raise SystemExit("Pilih path .json baru agar file lama tetap tersimpan.")
dump.parent.mkdir(parents=True, exist_ok=True)
logger, _ = cli.configure_logging(log_file=args.log_file or str(dump.with_suffix(".log")))
request, global_settings, _ = cli._resolve_request(args)
request.dataset_mode = "purchase_cycle_balance"
request.date_from = args.date_from.strip()
request.date_to = args.date_to.strip()
settings, config = cli.build_runtime_connection(
    global_settings, logger, database_profile_id=args.database_profile_id,
)
request.database = config.database
logger.info(
    "PCB: db=%s company=%s dates=%s..%s inventory=%s non_inventory=%s",
    request.database, request.company_id, request.date_from or "ALL",
    request.date_to or "ALL", request.include_inventory_accounts,
    request.include_non_inventory_accounts,
)

async def analyze():
    async with cli.AsyncOdooJsonRpcClient(config=config, settings=settings, logger=logger) as rpc:
        return await cli.SvlDashboardServiceAsync(rpc=rpc, logger=logger).analyze(request)

snapshot = asyncio.run(analyze())
assert snapshot.company_id == args.company_id
assert snapshot.database == request.database
assert snapshot.dataset_mode == "purchase_cycle_balance"
cli._log_snapshot_summary(logger, snapshot)
cli._write_snapshot_dump(str(dump), snapshot, logger)  # simpan sebelum membuat Excel
export_pcb_company_audit_excel(snapshot=snapshot, output_path=str(xlsx))
print(xlsx)
```

Helper dengan awalan `_` merupakan API internal repo: periksa ulang signature
jika source berubah. Contoh ini tidak menerapkan `--db-override`; koneksi dipilih
melalui profile yang diberikan. Ganti nilai berikut dengan ID hasil pencarian
dan profile lokal yang sudah dikonfirmasi; jangan berasumsi ID company atau profile
di instalasi lain sama.

```cmd
call .venv\Scripts\activate.bat
set "PCB_COMPANY_ID=ID_NUMERIK_HASIL_PENCARIAN"
set "PCB_DATABASE_PROFILE=PROFILE_LOKAL_TERKONFIRMASI"
python scratch\pcb_company_export.py --company-id %PCB_COMPANY_ID% --database-profile-id "%PCB_DATABASE_PROFILE%" --dump-json "output\pcb_penarikan_baru\snapshot.json"
```

Tanpa argumen tanggal, runner ini menarik semua data. Tambahkan
`--date-from YYYY-MM-DD --date-to YYYY-MM-DD` hanya jika periode diminta.
Gunakan subfolder baru setiap kali menjalankan analisis. Simpan JSON, log, Excel,
dan bukti pemeriksaan di `output/`; jangan masukkan ke Git.

Jika hanya pembuatan Excel yang gagal, snapshot tersimpan dapat dipakai untuk
mencoba kembali tanpa membaca Odoo lagi. Muat JSON lokal dan ubah snapshot,
cycle, `item_rows`, serta masing-masing `account_rows` menjadi objek beratribut,
misalnya `types.SimpleNamespace`. Pertahankan `raw_lines` sebagai dictionary,
karena exporter memakai `.get()` pada baris tersebut. Periksa company, database,
mode, periode, dan asal snapshot sebelum memberikannya ke
`export_pcb_company_audit_excel`. Jangan terapkan konversi ini pada JSON MCP
tanpa memeriksa schema-nya.

## Pemeriksaan hasil dan waktu proses

Sejak perubahan 2026-10-12, `_build_purchase_cycles` mempertahankan kelompok
penerimaan dengan SVL bernilai meskipun **seluruh** `all_cycle_move_ids` kosong.
**SVL tanpa JE atau belum di-relink tetap bisa masuk PCB**, baik dengan jurnal
lain dalam siklus maupun hanya dengan bukti SVL. Receipt Automated tanpa JE/Bill
dapat masuk Case 5 dan PCB Repair Collection dengan review wajib; lihat aturan
di [balance_cycle_pembelian.md](balance_cycle_pembelian.md).

Cycle dan item receipt tersebut muncul pada dua sheet awal. Kedua sheet Raw
Ledger hanya memuat AML aktual, sehingga receipt tanpa jurnal tidak mempunyai
baris di sana. Jangan menambahkan SVL atau placeholder sebagai debit/kredit
jurnal. Snapshot dan Excel lama tidak diperbarui otomatis oleh perubahan source;
lakukan analyze dan export baru. Jika nomor penerimaan hilang, periksa snapshot,
periode, dan rantai dokumennya sebelum menyimpulkan ada masalah pada ekspor Excel.
New JE/relink bukan prasyarat agar receipt tampil; ekstraksi tidak menulis Odoo.

Untuk menyediakan JE pada SVL tanpa JE melalui source repo, tersedia
[`python main.py svl-new-je-relink`](svl_new_je_relink_cli.md). Tool tersebut
memakai preview/apply dan dapat dijalankan tanpa GUI atau server MCP. Perbaikan
transaksi memerlukan review tersendiri; ekstraksi laporan tidak menjalankannya.

- Pastikan nama/ID company, database efektif, periode, filter, warnings, serta
  file benar-benar tersimpan dan dapat dibuka. Sampaikan warnings analisis.
- Untuk format native, periksa kelima sheet. Cocokkan jumlah cycle, item, dan
  raw line dengan snapshot. `Item Detail` mempunyai baris akhir `TOTAL`, yang
  tidak dihitung sebagai item; kedua sheet raw ledger harus mempertahankan barisnya.
- Gunakan `openpyxl.load_workbook(..., read_only=True)` untuk inspeksi tanpa memuat
  seluruh workbook ke memori. Integritas ZIP dapat diperiksa dengan
  `ZipFile.testzip()`. `openpyxl` tidak menghitung formula: periksa total melalui
  Excel bila perlu memvalidasi nilai hasil perhitungan, dengan menjaga file asli.
- Untuk report besar, periksa header dan sampel sebelum mengimpor/render seluruh
  workbook sebagai preview. Ekspor dan verifikasi menggunakan snapshot yang sama.
- Analisis lokal dapat tetap sibuk membangun ulang cycle setelah pembacaan Odoo
  selesai; pembuatan dan formatting Excel juga merupakan fase terpisah. Pantau
  log dan jelaskan fase yang sedang berjalan, tanpa menjanjikan durasi tetap.
- Jangan mempersempit scope, menambah filter periode, menghapus ledger, atau
  mengubah klasifikasi akuntansi untuk mempercepat tanpa permintaan pengguna.

Untuk aturan bisnis dan interpretasi hasil, baca
[balance_cycle_pembelian.md](balance_cycle_pembelian.md). Panduan ini menjelaskan
ekstraksi; tidak menggantikan aturan dan konfirmasi pada alur repair.
