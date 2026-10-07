# Technical Specification: Purchase Cycle Anomaly Classifier & Auto-Repair Engine

---

## 1. OVERVIEW & OBJECTIVE

Sistem ini dirancang untuk mendeteksi, mengklasifikasikan, dan memperbaiki secara otomatis anomali jurnal akuntansi pada **siklus pembelian (Purchase Cycle)** di Odoo 18. Anomali terjadi akibat kegagalan logika server Odoo dalam membentuk jurnal yang seharusnya, sehingga menghasilkan saldo residu pada akun-akun yang seharusnya bernilai nol setelah siklus pembelian mencapai tahap penagihan (vendor bill).

Sistem bekerja pada **dua level**:
- **Level Cycle** → Mengklasifikasikan kesehatan keseluruhan siklus pembelian
- **Level Item** → Mengklasifikasikan case anomali spesifik per produk/item dalam siklus tersebut

## 1A. BASELINE IMPLEMENTASI AKTIF (2026-03-29)

Implementasi aktif di kode saat ini mengikuti baseline berikut:

- Classifier bekerja **item-first**. Setiap item menyimpan satu `primary_case` untuk repair, plus `secondary_flags` untuk gejala tambahan.
- Case **auto-repairable** adalah `case1` s.d. `case6`.
- `case1` sekarang **strict two-sided residual**: item hanya actionable bila `1108099 != 0` dan `2103006 != 0` masih sama-sama hidup.
- Evidence correction / reclass pasca `case5` / `case6` tetap boleh dipakai untuk audit dan guard, tetapi tidak boleh dipromosikan menjadi STJ direct baru yang membuka ulang `case1`.
- Edge case **review-only** adalah:
  - `edge_partial_bill`
  - `edge_return_no_credit_memo`
  - `edge_stj_corrupt`
- Dashboard cycle tetap **cycle-first**, tetapi memakai model **hybrid cycle**:
  - `primary_case`
  - `case_counts`
  - `edge_flags`
  - `mixed_case_summary`
- Prioritas penentuan `primary_case` cycle:
  - `case5`
  - `case6`
  - `case1`
  - `case2`
  - `case3`
  - `case4`
  - `edge_*`
  - `case_lainnya`
- Container repair di snapshot:
  - `case1_link_rows` khusus **Case 1**
  - `case2_repair_rows` untuk seluruh flow multi-line **Case 2-6**
- Untuk **Case 5 / Case 6**, basis nominal repair selalu:
  - `standard_cost x qty base UoM`
  - jurnal `changed cost` / `RAC` hanya dipakai sebagai evidence atau guard, bukan sumber nominal utama
- Jika setelah `case5` / `case6` yang tersisa hanya residual clearing `1108099` pada item zero-SVL, item itu tidak membuka repair baru ke HPP; pada level cycle residual tersebut dapat turun ke `Cycle Sebagian > Clearing via Jurnal Reclass`.

---

## 2. DEFINISI & GLOSARIUM

### 2.1 Dokumen dalam Satu Purchase Cycle

| Kode Dokumen | Nama | Model Odoo | Keterangan |
|---|---|---|---|
| PR | Purchase Requisition | `purchase.requisition` | Permintaan pembelian internal |
| PO | Purchase Order | `purchase.order` | Order ke vendor |
| GR | Goods Receipt / Penerimaan Barang | `stock.picking` (type: in) | Penerimaan fisik barang |
| SVL | Stock Valuation Layer | `stock.valuation.layer` | Layer valuasi per item per GR |
| STJ | Stock Journal / Journal Entry GR | `account.move` (linked via SVL) | Jurnal akuntansi otomatis dari GR |
| RTN | Return / Retur Barang ke Vendor | `stock.picking` (return) | Pengembalian barang ke vendor |
| RTNJ | Return Journal Entry | `account.move` | Jurnal akuntansi dari retur |
| BILL | Vendor Bill / Tagihan Vendor | `account.move` (type: in_invoice) | Tagihan dari vendor |
| CRDM | Credit Memo / Vendor Refund | `account.move` (type: in_refund) | Nota kredit dari vendor atas retur |
| PAY | Bank Payment / Pembayaran | `account.payment` | Pembayaran ke vendor |
| RCN | Rekonsiliasi | `account.partial.reconcile` | Matching payment vs bill |
| RAC | Reklasifikasi / Reclass Entry | `account.move` | Jurnal koreksi/reclass manual/otomatis |
| PDIFF | Price Difference Entry | `account.move` | Jurnal selisih harga otomatis Odoo |

### 2.2 Akun Kritis

| Kode Akun | Nama Akun | Peran Normal |
|---|---|---|
| `1108099` | Clearing – System Pending Entries | Akun transitoris; harus nol setelah cycle selesai |
| `2103006` | Hutang Suspensed Pengadaan Barang/Jasa | Akun interim GR→Bill; harus nol setelah cycle selesai |
| `2101002` | Hutang Pihak Ketiga - Pengadaan Barang/Jasa | Hutang dagang permanen ke vendor |
| `11120003` | Outstanding Payments | Akun pembayaran yang belum direkonsiliasi |
| `SVA-{cat}` | Stock Valuation Account (per kategori produk) | Akun persediaan; dikonfigurasi di `product.category` |
| `EXP-{cat}` | Expense / COGS Account (per kategori produk) | Akun beban/HPP; dikonfigurasi di `product.category` |

### 2.3 Alur Jurnal Normal (Happy Path)

```
GR Validate
└── SVL value > 0 → STJ terbentuk:
    DB  SVA-{cat}    [Persediaan]
    CR  2103006      [Hutang Suspend]

Vendor Bill Posted:
    DB  2103006      [Hutang Suspend]    ← netting dengan STJ
    CR  2101002      [Hutang Pihak Ketiga]

Jika ada selisih harga (PO price ≠ Bill price):
    DB/CR  2103006   [Selisih]
    DB/CR  EXP-{cat} [Price Difference Expense]

Hasil: Saldo 2103006 = 0 ✓, Saldo 1108099 = 0 ✓
```

---

## 3. KLASIFIKASI KESEHATAN CYCLE

### 3.1 Syarat Minimum Cycle Dapat Dievaluasi

Sebuah cycle dianggap **eligible untuk dievaluasi anomali** apabila memenuhi **semua** kondisi berikut:
1. Terdapat minimal satu GR yang sudah divalidasi (`state = 'done'`)
2. Terdapat minimal satu Vendor Bill yang sudah diposting (`state = 'posted'`, `move_type = 'in_invoice'`)
3. Saldo residu dihitung setelah agregasi seluruh jurnal dalam cycle (STJ + RAC + BILL + CRDM + PAY + PDIFF)

### 3.2 Kategori Kesehatan Cycle

```
┌─────────────────────────────────────────────────────────┐
│                  SEMUA CYCLE PEMBELIAN                  │
└────────────────────────────┬────────────────────────────┘
                             │
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
       CYCLE SEHAT    CYCLE ANOMALI   CYCLE BELUM
                                      TERDEFINISI
```

#### ✅ CYCLE SEHAT

Memenuhi **salah satu** dari kondisi berikut:
- Belum memiliki Vendor Bill sama sekali (cycle masih berjalan), **ATAU**
- Sudah memiliki Vendor Bill, dan setelah agregasi seluruh jurnal dalam cycle:
  - Saldo `1108099` = 0 (debit - kredit = 0)
  - Saldo `2103006` = 0 (debit - kredit = 0)
  - Saldo `11120003` = 0 **ATAU** saldo `2101002` = 0 (hutang sudah lunas/rekonsiliasi)

> Cycle Sehat tidak memerlukan tindakan perbaikan.

#### ⚠️ CYCLE ANOMALI

Memenuhi syarat minimum evaluasi (sudah ada Bill) **dan** setelah agregasi seluruh jurnal dalam cycle masih terdapat saldo residu pada **minimal satu** akun bermasalah:
- Saldo `1108099` ≠ 0, **ATAU**
- Saldo `2103006` ≠ 0

Cycle Anomali selanjutnya diklasifikasikan berdasarkan **case per item** (lihat Bagian 4).

#### ❓ CYCLE BELUM TERDEFINISI

Semua cycle yang tidak masuk ke Cycle Sehat maupun Cycle Anomali. Contoh:
- Cycle Anomali yang memiliki akun bermasalah bersaldo, namun **tidak ada satu pun item** yang match ke Case 1–6 atau edge review yang dikenali
- Cycle dengan kondisi jurnal yang sangat tidak lazim atau kombinasi case yang saling overlap tanpa solusi deterministik
- Cycle yang melibatkan multi-currency dengan conversion loss/gain yang belum ter-handle

> Cycle Belum Terdefinisi memerlukan review manual.

---

## 4. KLASIFIKASI CASE PER ITEM (ITEM-LEVEL CLASSIFIER)

### 4.0 Pra-kondisi & Cara Kerja Classifier

**Input per item:**
- Kumpulan seluruh jurnal line (`account.move.line`) yang berelasi dengan item ini dalam cycle, dikelompokkan berdasarkan:
  - `stock_valuation_layer_ids` → untuk identifikasi line terkait STJ
  - `purchase_line_id` → untuk identifikasi line terkait Bill
- Nilai SVL (`stock.valuation.layer.value`) dan `quantity`
- Standard Cost item pada saat GR (`standard_price` di `stock.valuation.layer` atau `product.product`)
- `qty_done` di `stock.move.line` (qty aktual diterima, dalam satuan base UoM)

**Logika agregasi per item per akun:**
```python
# Saldo akun X untuk item Y dalam cycle ini:
saldo = sum(debit) - sum(kredit)
# untuk semua account.move.line di cycle ini
# yang berelasi dengan item Y dan akun X
```

**Urutan prioritas classifier:**
Classifier harus dijalankan secara **berurutan**. Jika item sudah match ke satu case, **tidak** dievaluasi ke case berikutnya, **kecuali** jika secara eksplisit disebutkan bahwa item dapat memiliki multiple case.

---

### CASE 1 — STJ-Bill Mismatch: Clearing ↔ Suspend (Double Problem Accounts)

**Nama Grup:** `Group 1: STJ-Bill Mismatch (Clearing–Suspend)`

**Kondisi Deteksi (semua harus terpenuhi):**
```
✓ SVL value ≠ 0 (STJ terbentuk)
✓ STJ terbentuk dengan:
    DB  SVA-{cat}   > 0
    CR  1108099     > 0   ← bukan 2103006
✓ Bill terbentuk dengan:
    DB  2103006     > 0   ← bukan 1108099
    CR  2101002     > 0
✓ Saldo residu item: 1108099 ≠ 0 DAN 2103006 ≠ 0
```

Catatan implementasi aktif: residual satu sisi setelah correction / reclass, misalnya tinggal `1108099` saja pada item post-`case5` / post-`case6`, tidak boleh diperlakukan sebagai `case1`.

**Akar Masalah:**
STJ menggunakan akun Clearing (`1108099`) sebagai kredit, sedangkan Bill menggunakan Suspend (`2103006`) sebagai debit. Kedua akun interim tidak saling menghapus.

**Jurnal Perbaikan (Repair Journal):**

*Base amount* = nilai absolut CR `1108099` di STJ item ini

*Jika nilai STJ = nilai Bill (tidak ada selisih):*
```
DB  1108099    [Clearing]              amount = base_amount
CR  2103006    [Suspend]               amount = base_amount
```

*Jika nilai STJ ≠ nilai Bill (ada selisih harga):*
```
DB  1108099    [Clearing]              amount = stj_amount
CR  2103006    [Suspend]               amount = bill_amount
DB/CR  EXP-{cat}  [Price Diff Expense] amount = |stj_amount - bill_amount|
    → DB EXP jika bill > stj (undervalued)
    → CR EXP jika bill < stj (overvalued)
```

---

### CASE 2 — STJ-Bill Price Difference: Suspend ↔ Suspend Residual

**Nama Grup:** `Group 2: STJ-Bill Price Diff (Suspend–Suspend)`

**Kondisi Deteksi (semua harus terpenuhi):**
```
✓ SVL value ≠ 0 (STJ terbentuk)
✓ STJ terbentuk dengan:
    DB  SVA-{cat}   > 0
    CR  2103006     > 0   ← benar, menggunakan Suspend
✓ Bill terbentuk dengan:
    DB  2103006     > 0   ← benar, menggunakan Suspend
    CR  2101002     > 0
✓ Saldo residu item: 2103006 ≠ 0 (CR STJ ≠ DB Bill)
✓ Saldo residu item: 1108099 = 0 (tidak ada masalah di Clearing)
```

**Akar Masalah:**
Alur akun sudah benar (Suspend ke Suspend), namun nominal CR `2103006` di STJ **tidak sama** dengan nominal DB `2103006` di Bill akibat perbedaan harga PO vs harga aktual Bill. Jurnal `PDIFF` (Price Difference) tidak terbentuk oleh sistem.

**Identifikasi Selisih:**
```python
cr_suspend_stj  = sum(CR 2103006 dari semua STJ item ini)
db_suspend_bill = sum(DB 2103006 dari semua Bill item ini)
selisih = cr_suspend_stj - db_suspend_bill
# selisih > 0 → STJ lebih besar → overvalued di persediaan
# selisih < 0 → Bill lebih besar → undervalued di persediaan
```

**Jurnal Perbaikan:**
```
Jika selisih > 0 (STJ > Bill, persediaan overvalued):
    DB  2103006     [Suspend]            amount = |selisih|
    CR  EXP-{cat}   [Price Diff Expense] amount = |selisih|

Jika selisih < 0 (Bill > STJ, persediaan undervalued):
    DB  EXP-{cat}   [Price Diff Expense] amount = |selisih|
    CR  2103006     [Suspend]             amount = |selisih|
```

---

### CASE 3 — STJ Clearing, Bill Hit Expenses (Clearing–Expenses)

**Nama Grup:** `Group 3: STJ-Bill Hit Expenses (Clearing–Expenses)`

**Kondisi Deteksi (semua harus terpenuhi):**
```
✓ SVL value ≠ 0 (STJ terbentuk)
✓ STJ terbentuk dengan:
    DB  SVA-{cat}   > 0
    CR  1108099     > 0   ← menggunakan Clearing
✓ Bill terbentuk dengan:
    DB  EXP-{cat}   > 0   ← bukan 2103006 atau 1108099
    CR  2101002     > 0
✓ Saldo residu item: 1108099 ≠ 0
✓ Saldo residu item: 2103006 = 0
```

**Akar Masalah:**
STJ menggunakan Clearing, dan Bill langsung membebankan ke Expenses tanpa melalui akun interim apapun. Akun Clearing tidak ter-netting.

**Jurnal Perbaikan:**

*Nilai netting* = nilai CR `1108099` di STJ item ini

*Jika nilai STJ = nilai Bill (tidak ada selisih):*
```
DB  1108099     [Clearing]          amount = stj_amount
CR  EXP-{cat}   [Zeroing Expenses]  amount = stj_amount
```

*Jika nilai STJ ≠ nilai Bill (ada selisih):*
```
DB  1108099     [Clearing]          amount = stj_amount
CR  EXP-{cat}   [Zeroing Expenses]  amount = bill_amount
DB/CR  EXP-{cat}  [Price Diff]      amount = |stj_amount - bill_amount|
    → CR EXP jika bill < stj
    → DB EXP jika bill > stj
```

> **Catatan:** Jurnal perbaikan ini secara efektif mereklasifikasi beban dari EXP ke Clearing untuk di-offset, namun secara net EXP tetap mencatat selisih jika ada.

---

### CASE 4 — STJ Suspend, Bill Hit Expenses (Suspend–Expenses)

**Nama Grup:** `Group 4: STJ-Bill Hit Expenses (Suspend–Expenses)`

**Kondisi Deteksi (semua harus terpenuhi):**
```
✓ SVL value ≠ 0 (STJ terbentuk)
✓ STJ terbentuk dengan:
    DB  SVA-{cat}   > 0
    CR  2103006     > 0   ← menggunakan Suspend (benar)
✓ Bill terbentuk dengan:
    DB  EXP-{cat}   > 0   ← bukan 2103006
    CR  2101002     > 0
✓ Saldo residu item: 2103006 ≠ 0
✓ Saldo residu item: 1108099 = 0
```

**Akar Masalah:**
STJ sudah benar menggunakan Suspend, namun Bill membebankan langsung ke Expenses. Akun Suspend tidak ter-netting dengan Bill.

**Jurnal Perbaikan:**

*Jika nilai STJ = nilai Bill (tidak ada selisih):*
```
DB  2103006     [Suspend]           amount = stj_amount
CR  EXP-{cat}   [Zeroing Expenses]  amount = stj_amount
```

*Jika nilai STJ ≠ nilai Bill (ada selisih):*
```
DB  2103006     [Suspend]           amount = stj_amount
CR  EXP-{cat}   [Zeroing Expenses]  amount = bill_exp_amount
DB/CR  EXP-{cat}  [Price Diff]      amount = |stj_amount - bill_exp_amount|
```

---

### CASE 5 — No STJ (SVL=0), Bill via Suspend (Undirect Clearing via Suspend)

**Nama Grup:** `Group 5: No STJ-Bill Mismatch (Undirect Clearing–Suspend)`

**Kondisi Deteksi (semua harus terpenuhi):**
```
✓ SVL value = 0 pada saat GR validate
✓ Tidak ada STJ yang terbentuk:
    - account_move_id di SVL = NULL/False, ATAU
    - JE terbentuk namun seluruh line amount = 0
    ⚠️ Pastikan bukan kasus JE ter-archive atau line hilang
✓ Terdapat jurnal "changed cost from 0.0 to X" (Cost Revaluation):
    DB  SVA-{cat}   [Persediaan]    ← backdate revaluation
    CR  EXP-{cat}   [COGS/Expenses]
✓ Terdapat RAC/Reclass Entry atas revaluation tersebut:
    DB  EXP-{cat}   [Zeroing COGS]
    CR  1108099     [Clearing]      ← transfer ke Clearing
✓ Bill terbentuk dengan:
    DB  2103006     [Suspend]       > 0
    CR  2101002     [Hutang Pihak Ketiga]
✓ Saldo residu item: 2103006 ≠ 0 DAN 1108099 ≠ 0 (keduanya dari sisi berbeda)
```

**Logika Deteksi RAC:**
```python
# Deteksi apakah ada RAC yang mentransfer ke 1108099
rac_entries = account_move.search([
    ('ref', 'ilike', 'This entry transfers'),
    ('ref', 'ilike', '1108099'),
    # filter by item/move relation
])
if rac_entries:
    # gunakan nilai CR 1108099 dari RAC sebagai base_amount
    base_amount = sum(CR 1108099 dari RAC untuk item ini)
else:
    # fallback: gunakan standard_cost × qty_done (base UoM)
    base_amount = standard_cost × qty_done_base_uom
```

**Jurnal Perbaikan:**

*base_amount* = nilai CR `1108099` dari RAC, atau `standard_cost × qty_done` jika RAC tidak terdeteksi

*bill_suspend_amount* = nilai DB `2103006` dari Bill item ini

*Jika base_amount = bill_suspend_amount:*
```
DB  2103006     [Suspend]       amount = base_amount
CR  1108099     [Clearing]      amount = base_amount
```

*Jika base_amount ≠ bill_suspend_amount (selisih):*
```
DB  2103006     [Suspend]       amount = bill_suspend_amount
CR  1108099     [Clearing]      amount = base_amount
DB/CR  EXP-{cat}  [Selisih]    amount = |base_amount - bill_suspend_amount|
```

---

### CASE 6 — No STJ (SVL=0), Bill Hit Expenses (Undirect Clearing via Expenses)

**Nama Grup:** `Group 6: No STJ-Bill Hit Expenses (Undirect Clearing–Expenses)`

**Kondisi Deteksi (semua harus terpenuhi):**
```
✓ SVL value = 0 pada saat GR validate (sama seperti Case 5)
✓ Tidak ada STJ yang terbentuk (validasi sama seperti Case 5)
✓ Terdapat jurnal "changed cost from 0.0 to X" dan RAC
    (sama seperti Case 5, CR 1108099 ada dari RAC)
✓ Bill terbentuk dengan:
    DB  EXP-{cat}   [Expenses/COGS]  > 0   ← bukan Suspend
    CR  2101002     [Hutang Pihak Ketiga]
✓ Saldo residu item: 1108099 ≠ 0
✓ Saldo residu item: 2103006 = 0
```

**Logika Deteksi RAC:** Sama dengan Case 5.

**Jurnal Perbaikan:**

*base_amount* = nilai CR `1108099` dari RAC, atau `standard_cost × qty_done` jika RAC tidak terdeteksi

*bill_exp_amount* = nilai DB `EXP-{cat}` dari Bill item ini

*Jika base_amount = bill_exp_amount:*
```
DB  1108099     [Clearing]          amount = base_amount
CR  EXP-{cat}   [Zeroing Expenses]  amount = base_amount
```

*Jika base_amount ≠ bill_exp_amount (selisih):*
```
DB  1108099     [Clearing]          amount = base_amount
CR  EXP-{cat}   [Zeroing Expenses]  amount = bill_exp_amount
DB/CR  EXP-{cat}  [Selisih Harga]   amount = |base_amount - bill_exp_amount|
```

---

### CASE 7 — Partial Billing: Bill Parsial dengan Saldo Suspend Residual

**Nama Grup:** `Group 7: Partial Bill (Suspend Residual – Unbilled Qty)`

**Kondisi Deteksi:**
```
✓ SVL value ≠ 0 (STJ terbentuk)
✓ STJ terbentuk dengan CR 2103006 (alur normal)
✓ Bill terbentuk dengan DB 2103006 NAMUN:
    qty_billed < qty_received (billing parsial)
    → sehingga CR 2103006 STJ > DB 2103006 Bill
✓ Tidak ada Bill lain yang outstanding untuk sisa qty
✓ Saldo residu 2103006 > 0 proporsional terhadap unbilled qty
```

**Akar Masalah:** Vendor belum menagihkan seluruh quantity yang sudah diterima. Ini bisa **bukan anomali** jika memang masih menunggu tagihan. Classifier harus memvalidasi apakah ada open PO line yang masih bisa di-bill.

**Aksi:**
- Jika masih ada `purchase.order.line` dengan `qty_invoiced < qty_received` → **tandai sebagai "Menunggu Tagihan Parsial"**, bukan anomali untuk di-repair otomatis
- Jika PO sudah fully closed/locked namun masih ada residu → masuk Case 2 dengan qty koreksi

---

### CASE 8 — Return tanpa Credit Memo: Retur Tidak Ter-offset

**Nama Grup:** `Group 8: Return Without Credit Memo (Suspend/Clearing Residual from Return)`

**Kondisi Deteksi:**
```
✓ Terdapat Return/Retur GR (stock.picking return, state='done')
✓ Return Journal terbentuk:
    DB  2103006 atau 1108099   [Reverse dari STJ]
    CR  SVA-{cat}              [Reverse persediaan]
✓ Tidak terdapat Credit Memo (in_refund) yang ter-match ke Return ini
✓ Saldo residu 2103006 atau 1108099 ≠ 0 akibat Return yang tidak di-offset
```

**Jurnal Perbaikan:**
- Jika vendor sudah menagih (Bill posted) → buat Credit Memo manual atau jurnal koreksi yang menutup saldo Return
- Jika vendor belum menagih → tandai sebagai "Return Menunggu Credit Memo"

---

### CASE 9 — STJ Archive / JE Incomplete: Header Ada, Lines Hilang

**Nama Grup:** `Group 9: Incomplete STJ (Header Without Valid Lines)`

**Kondisi Deteksi:**
```
✓ SVL.account_move_id terisi (JE header ada)
✓ Namun account.move.line yang terkait:
    - Jumlah line = 0, ATAU
    - Semua line amount = 0, ATAU
    - Line akun kritis (SVA / 1108099 / 2103006) tidak ditemukan
✓ SVL value ≠ 0
```

**Aksi:** Tidak dapat di-repair otomatis. Tandai sebagai **"STJ Corrupt – Manual Review Required"** dan eskalasi ke tim teknis untuk investigasi di level database.

---

## 5. TABEL RINGKASAN CLASSIFIER

| Case | Grup | STJ Ada? | Akun CR STJ | Akun DB Bill | Akun Bermasalah | Solusi Otomatis |
|---|---|---|---|---|---|---|
| 1 | Group 1 | ✅ | 1108099 | 2103006 | 1108099 & 2103006 | DB 1108099, CR 2103006 + selisih EXP |
| 2 | Group 2 | ✅ | 2103006 | 2103006 | 2103006 (selisih) | DB/CR 2103006 + EXP selisih |
| 3 | Group 3 | ✅ | 1108099 | EXP-{cat} | 1108099 | DB 1108099, CR EXP + selisih |
| 4 | Group 4 | ✅ | 2103006 | EXP-{cat} | 2103006 | DB 2103006, CR EXP + selisih |
| 5 | Group 5 | ❌ (SVL=0) | — | 2103006 | 2103006 & 1108099 | DB 2103006, CR 1108099 + selisih |
| 6 | Group 6 | ❌ (SVL=0) | — | EXP-{cat} | 1108099 | DB 1108099, CR EXP + selisih |
| 7 | Group 7 | ✅ | 2103006 | 2103006 | 2103006 (parsial) | Review manual / tunggu bill |
| 8 | Group 8 | ✅ + RTN | — | — | Residu dari RTN | Credit Memo / jurnal koreksi |
| 9 | Group 9 | ⚠️ Corrupt | — | — | — | Eskalasi manual |

---

## 6. FLOWCHART LOGIKA CLASSIFIER

```
INPUT: Item dalam Purchase Cycle yang sudah ada Bill
│
├─► Apakah SVL value = 0?
│    YES ──► Validasi: JE header kosong/lines hilang?
│    │            YES ──► CASE 9 (Corrupt STJ)
│    │            NO  ──► Cek Bill DB akun:
│    │                      DB 2103006? ──► CASE 5
│    │                      DB EXP?     ──► CASE 6
│    │
│    NO ──► STJ ada dan valid
│           │
│           ├─► CR STJ = 1108099?
│           │    YES ──► Cek Bill DB akun:
│           │              DB 2103006? ──► CASE 1
│           │              DB EXP?     ──► CASE 3
│           │
│           └─► CR STJ = 2103006?
│                YES ──► Cek Bill DB akun:
│                          DB 2103006?
│                          │  └─► Saldo 2103006 = 0? ──► Sehat (no case)
│                          │      Saldo 2103006 ≠ 0?
│                          │        └─► qty parsial? ──► CASE 7
│                          │            selisih harga? ──► CASE 2
│                          DB EXP? ──► CASE 4
│
├─► Terdapat Return tanpa Credit Memo? ──► CASE 8
│
└─► Tidak match ke case manapun namun ada saldo bermasalah?
         ──► CYCLE BELUM TERDEFINISI
```

---

## 7. ATURAN PENGHITUNGAN SELISIH (PRICE DIFFERENCE)

Selisih harga dihitung per item per cycle sebagai berikut:

```python
def hitung_selisih_item(item, cycle):
    # Nilai total dari sisi STJ (nilai persediaan yang masuk)
    nilai_stj = abs(sum(
        line.credit for line in item.stj_lines
        if line.account_id.code in ['1108099', '2103006']
    ))

    # Nilai total dari sisi Bill (nilai yang ditagihkan vendor)
    nilai_bill = abs(sum(
        line.debit for line in item.bill_lines
        if line.account_id.code not in ['2101002']
    ))

    selisih = nilai_stj - nilai_bill
    # selisih > 0 : STJ > Bill → CR EXP (koreksi overvalued)
    # selisih < 0 : Bill > STJ → DB EXP (koreksi undervalued)
    # selisih = 0 : tidak perlu jurnal selisih
    return selisih
```

**Akun EXP yang digunakan** selalu mengacu pada `property_account_expense_categ_id` dari `product.category` item tersebut, bukan akun expense umum.

---

## 8. VALIDASI PRA-REPAIR

Sebelum jurnal perbaikan di-post, sistem harus memvalidasi:

```
[ ] Cycle masih dalam fiscal year yang masih open (tidak locked)
[ ] Akun-akun target dalam jurnal perbaikan masih aktif (active=True)
[ ] Tidak ada jurnal perbaikan sebelumnya untuk item yang sama yang belum di-reverse
[ ] Nilai repair journal balance = 0 (debit total = kredit total)
[ ] Referensi ke cycle, PO, GR, Bill, dan item tersimpan di field `ref` dan `narration`
[ ] User yang melakukan repair memiliki akses accounting manager
```

---

## 9. KONVENSI PENAMAAN JURNAL PERBAIKAN

```
Nama JE    : [REPAIR] {Case Name} - {PO Number} - {Item Code}
Reference  : REPAIR/{YYYY}/{MM}/{sequence}
Narration  : "Auto-repair for Purchase Cycle: {PO}
              Item: {product.code} - {product.name}
              Case: {case_number} - {case_description}
              STJ Ref: {stj_name}
              Bill Ref: {bill_name}
              Generated by: {user} at {datetime}"
Journal    : Jurnal khusus repair (konfigurasi terpisah)
```

---

> Dokumen ini bersifat **living specification** — setiap case baru yang ditemukan di production harus didokumentasikan dan ditambahkan ke classifier sebelum di-deploy ke lingkungan repair otomatis.
