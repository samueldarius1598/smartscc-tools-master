# Technical Specification: Procurement Cycle Journal Classifier & Auto-Repair Engine

## 1. Pendahuluan
Dokumen ini merincikan logika klasifikasi dan mesin perbaikan otomatis (*auto-repair*) untuk siklus pembelian (Procurement Cycle) yang mencakup: **PR -> PO -> GR (STJ) -> Return -> Bill -> Payment -> Reconcile**.

Tujuan utama sistem ini adalah mendeteksi kejanggalan jurnal pada akun penampung akibat kegagalan logika sistem ERP (Odoo) dan melakukan koreksi jurnal secara otomatis pada level **item**.

---

## 1A. Baseline Implementasi Aktif (2026-03-29)

Baseline implementasi yang dipakai aplikasi saat ini:

- Classifier aktif bersifat **item-level** dan menyimpan `primary_case`, `secondary_flags`, `auto_repairable`, dan basis repair per item.
- Case repairable: `case1` sampai `case6`.
- Edge case review-only:
  - `edge_partial_bill`
  - `edge_return_no_credit_memo`
  - `edge_stj_corrupt`
- Cycle dashboard memakai agregasi **hybrid**:
  - `primary_case`
  - `case_counts`
  - `edge_flags`
  - `mixed_case_summary`
- Prioritas group cycle: `case5`, `case6`, `case1`, `case2`, `case3`, `case4`, `edge_*`, `case_lainnya`.
- `case1_link_rows` dipakai hanya untuk **Case 1**.
- `case2_repair_rows` dipakai untuk flow multi-line **Case 2-6**.
- **Case 5 / 6** selalu menggunakan dasar nominal `standard_cost x qty base UoM`; RAC / changed-cost hanya evidence dan guard.

---

## 2. Parameter Akun & Indikator Masalah

### 2.1 Akun Bermasalah (Target Penolkan)
Sebuah cycle atau item dianggap bermasalah jika setelah tahap **Vendor Bill**, saldo agregat (Debit vs Kredit) pada akun berikut **tidak bernilai nol**:
1.  **1108099** - Clearing: System Pending Entries
2.  **2103006** - Hutang Suspensed Pengadaan Barang/Jasa

### 2.2 Akun Terkait Lainnya
- **Stock_Valuation_Account**: Akun Persediaan (berdasarkan kategori item).
- **Expense_Account**: Akun Beban/HPP (berdasarkan kategori item).
- **2101002**: Hutang Pihak Ketiga (Payable).
- **11120003**: Outstanding Payments.

---

## 3. Klasifikasi Case & Logika Perbaikan (Item Level)

Classifier ini bekerja dengan memindai setiap item dalam cycle yang telah memiliki status *Billed*.

### Group 1: STJ - Bill Mismatch (Clearing vs Suspense)
**Case 1: Cross-Account Conflict**
- **Gejala:** STJ mengkredit *Clearing*, tetapi Bill mendebit *Suspense*.
- **Jurnal Eksisting:**
  - STJ: `DB Inventory` | `CR 1108099 (Clearing)`
  - Bill: `DB 2103006 (Suspense)` | `CR 2101002 (Payable)`
- **Solusi Repair:** Menghapus saldo di kedua akun penampung.
  - **Jurnal Koreksi:** `DB 1108099` | `CR 2103006`.
  - *Catatan: Jika ada selisih nominal, masukkan ke DB/CR Expense Account kategori item.*

### Group 2: STJ - Bill Price Difference (Suspense Imbalance)
**Case 2: Price Variance Failure**
- **Gejala:** STJ dan Bill sudah menggunakan akun yang sama (`2103006`), namun saldo tidak nol karena perbedaan harga (Price Difference) tidak terbentuk otomatis.
- **Jurnal Eksisting:**
  - STJ: `DB Inventory` | `CR 2103006` (Harga PO)
  - Bill: `DB 2103006` (Harga Bill) | `CR 2101002`
- **Solusi Repair:** Menolkan sisa saldo Suspense.
  - **Jurnal Koreksi:** `DB/CR 2103006` | `CR/DB Expense Account`.

### Group 3: STJ - Bill Hit Expenses (Clearing vs Expenses)
**Case 3: Direct Expense Leakage (Clearing)**
- **Gejala:** STJ menggantung di *Clearing*, namun Bill langsung menghantam *Expense*.
- **Jurnal Eksisting:**
  - STJ: `DB Inventory` | `CR 1108099`
  - Bill: `DB Expense Account` | `CR 2101002`
- **Solusi Repair:** Reklassifikasi Expense ke Clearing.
  - **Jurnal Koreksi:** `DB 1108099` | `CR Expense Account` (Zero-ing Expense).
  - *Catatan: Tambahkan selisih nominal ke Expense Account jika ada.*

### Group 4: STJ - Bill Hit Expenses (Suspense vs Expenses)
**Case 4: Direct Expense Leakage (Suspense)**
- **Gejala:** STJ menggantung di *Suspense*, namun Bill langsung menghantam *Expense*.
- **Jurnal Eksisting:**
  - STJ: `DB Inventory` | `CR 2103006`
  - Bill: `DB Expense Account` | `CR 2101002`
- **Solusi Repair:** Reklassifikasi Expense ke Suspense.
  - **Jurnal Koreksi:** `DB 2103006` | `CR Expense Account` (Zero-ing Expense).
  - *Catatan: Tambahkan selisih nominal ke Expense Account jika ada.*

### Group 5: No STJ - Bill Mismatch (Zero Valuation Issue)
**Case 5: Missing STJ with Suspense Bill**
- **Kondisi:** SVL bernilai 0 saat Validate GR (STJ tidak terbentuk). Sistem harus memvalidasi apakah ada jurnal "Changed Cost" atau "RAC Reklass" setelahnya.
- **Jurnal Eksisting:**
  - STJ: (Kosong/Tidak Ada)
  - Bill: `DB 2103006` | `CR 2101002`
- **Solusi Repair:** Reklass Suspense ke Clearing menggunakan Standard Cost.
  - **Jurnal Koreksi:** `DB 2103006` (Nilai: Standard Cost x Qty) | `CR 1108099`.
  - *Catatan: Selisih nominal dimasukkan ke Expense Account.*

### Group 6: No STJ - Bill Hit Expenses (Zero Valuation Issue)
**Case 6: Missing STJ with Expense Bill**
- **Kondisi:** Sama dengan Case 5 (SVL 0).
- **Jurnal Eksisting:**
  - STJ: (Kosong/Tidak Ada)
  - Bill: `DB Expense Account` | `CR 2101002`
- **Solusi Repair:**
  - **Jurnal Koreksi:** `DB 1108099` (Nilai: Standard Cost x Qty) | `CR Expense Account`.
  - *Catatan: Selisih nominal tetap dialokasikan ke Expense Account.*

---

## 4. Klasifikasi Status Cycle

Setelah pengecekan level item, sistem memberikan status akhir pada level Cycle:

1.  **Cycle Sehat**:
    - Sudah memiliki Bill.
    - Akun `11120003`, `2101002`, dan `2103006` memiliki saldo agregat **nol**.
    - Tidak ada anomali pada akun `1108099`.
2.  **Cycle Bermasalah**:
    - Terdeteksi satu atau lebih item yang masuk ke dalam Case 1 s/d Case 6.
3.  **Cycle Belum Terdefinisi**:
    - Cycle yang tidak memenuhi kriteria "Sehat" namun polanya tidak sesuai dengan Case 1-6.
4.  **Cycle Lainnya**:
    - Memiliki akun bermasalah tetapi tidak ditemukan kecocokan pola pada level item yang telah didefinisikan.

---

## 5. Logic Guardrails & Otomatisasi

- **Traceability**: Setiap jurnal perbaikan wajib menyertakan referensi `Document Origin` dan `Case ID`.
- **SVL Verification**: Untuk Case 5 & 6, sistem wajib memastikan nomor SVL benar-benar bernilai 0 dan memeriksa keberadaan JE "Changed Cost" sebelum eksekusi.
- **Aggregation Logic**: Perbaikan dilakukan berdasarkan nilai agregat per item untuk memastikan tidak ada *over-correction*.
- **Return Handling**: Jika terdapat dokumen Return, sistem harus menghitung nilai bersih (Net Qty) sebelum menentukan nilai reklassifikasi.

---
