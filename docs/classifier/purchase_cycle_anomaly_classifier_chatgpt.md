# Prompt Teknis Classifier & Auto-Repair Purchase Cycle Journal Anomaly

## 1. Tujuan Utama Sistem

Bangun sebuah **classifier dan repair engine** untuk menganalisis **purchase cycle** yang telah dikumpulkan dari dokumen pembelian, mulai dari:

- PR
- PO
- GR / STJ
- Return (jika ada)
- Vendor Bill
- Bank Payment
- Reconciliation

Tujuan utama sistem adalah:

1. **Mengidentifikasi kejanggalan jurnal** pada cycle pembelian yang muncul akibat gagalnya logic Odoo di sisi server.
2. **Mengklasifikasikan masalah pada level item**, bukan hanya level cycle.
3. **Menentukan solusi repair jurnal** yang sesuai untuk tiap item bermasalah.
4. **Mengagregasi hasil item-level classification ke level cycle**.
5. Bila memungkinkan, **menjalankan auto-repair** berdasarkan case yang sudah dikenali.
6. Menandai cycle yang tidak jelas/ambigu agar **tidak diperbaiki secara agresif**.

## 1A. Baseline Implementasi Aktif (2026-03-29)

Implementasi aktif saat ini mengikuti baseline berikut:

- Classifier dijalankan **per item** dan menyimpan satu `primary_case` plus `secondary_flags`.
- Case yang dianggap **repairable otomatis**:
  - `case1`
  - `case2`
  - `case3`
  - `case4`
  - `case5`
  - `case6`
- Edge case **non-auto-repair**:
  - `edge_partial_bill`
  - `edge_return_no_credit_memo`
  - `edge_stj_corrupt`
- Cycle dashboard memakai model **hybrid cycle**:
  - satu `primary_case` untuk grouping sidebar
  - `case_counts` untuk breakdown mixed cycle
  - `edge_flags` untuk warning eksplisit
  - `mixed_case_summary` untuk ringkasan cepat
- Prioritas `primary_case` cycle:
  - `case5`, `case6`, `case1`, `case2`, `case3`, `case4`, `edge_*`, lalu `case_lainnya`
- Container repair snapshot:
  - `case1_link_rows` khusus **Case 1**
  - `case2_repair_rows` untuk seluruh row multi-line **Case 2-6**
- Untuk **Case 5 / Case 6**, basis nominal repair wajib `standard_cost x qty base UoM`; jurnal changed-cost / RAC hanya menjadi evidence.

## 2. Prinsip Dasar Analisis

### 2.1 Level Analisis
Classifier wajib bekerja pada **level item**, karena dalam satu cycle:
- dapat terdapat banyak item,
- setiap item bisa memiliki **case berbeda**,
- satu item bahkan bisa memiliki **lebih dari satu gejala**,
- repair yang tepat bergantung pada **kombinasi jurnal STJ dan Bill per item**.

### 2.2 Definisi “Akun Bermasalah”
Akun berikut dianggap sebagai **akun bermasalah** pada purchase cycle ketika seharusnya setelah proses pembelian sampai tahap bill, akun tersebut **tidak menyisakan saldo** pada level item/cycle namun faktanya masih ada saldo:

- **1108099 Clearing – System Pending Entries**
- **2103006 Hutang Suspensed Pengadaan Barang/Jasa / Suspensed Payable for Procurement**

### 2.3 Kapan Cycle Layak Dianalisis Sebagai Bermasalah
Suatu cycle layak masuk proses klasifikasi masalah hanya jika:

- cycle sudah mencapai minimal tahap **barang masuk / SVL**,
- dan/atau sudah mencapai tahap **Vendor Bill**,
- terutama ketika **Vendor Bill sudah ada**,
- lalu setelah agregasi debit-kredit per item/cycle masih tersisa saldo pada akun bermasalah.

### 2.4 Asumsi Umum
Asumsi utama classifier:
- Ketika purchase cycle sudah mencapai **bill vendor**, maka secara normal akun transisi seperti **Clearing** dan/atau **Suspensed Payable** semestinya telah **tertutup** atau telah berpindah sesuai logic akuntansi yang benar.
- Bila masih ada saldo, maka terdapat anomaly yang harus diklasifikasikan.

## 3. Definisi Status Cycle

Sistem harus membagi cycle ke dalam 3 status besar:

### A. Cycle Sehat
Cycle dianggap **Sehat** apabila memenuhi salah satu kondisi berikut:

1. **Belum memiliki Vendor Bill**, sehingga belum layak dinilai mismatch akhir.
2. Sudah memiliki Bill, dan setelah agregasi debit-kredit, akun berikut bernilai **0** atau secara substansi sudah closed:
   - **11120003 Outstanding Payments**
   - **2101002 Hutang Pihak Ketiga - Pengadaan Barang/Jasa / Payable to Third Parties - Goods/Services**
   - **2103006 Hutang Suspensed Pengadaan Barang/Jasa / Suspensed**
3. Tidak ada saldo abnormal di akun bermasalah pada level cycle/item.

> Catatan: “sehat” di sini bukan berarti seluruh jurnal sempurna secara teori, tetapi berarti **tidak ditemukan indikasi mismatch yang menjadi target repair engine**.

### B. Cycle Bermasalah
Cycle dianggap **Bermasalah** bila:
- sudah mencapai tahap **Bill vendor**, dan
- terdapat saldo abnormal pada **1108099** dan/atau **2103006**, dan
- terdapat setidaknya 1 item yang cocok dengan salah satu case classifier.

### C. Cycle Belum Terdefinisi
Cycle masuk **Belum Terdefinisi** bila:
- memiliki akun bermasalah,
- tetapi item-item di dalamnya **tidak cocok dengan seluruh case yang dikenali**,
- atau data pendukung tidak cukup / ambigu,
- atau terdapat kondisi jurnal yang konflik, campuran, atau rusak sehingga repair otomatis berisiko salah.

## 4. Data yang Wajib Tersedia Per Item

Agar classifier valid, sistem idealnya memiliki data berikut untuk setiap item dalam cycle:

1. **Identitas item**
   - item_id / product_id
   - kode item
   - nama item
   - kategori item
   - akun persediaan kategori item
   - akun expense / COGS kategori item

2. **Kuantitas & valuation**
   - qty GR
   - qty return
   - qty net
   - qty dalam base unit
   - standard cost saat GR
   - valuation amount / SVL amount
   - bill amount per item
   - landed / adjustment bila ada

3. **Dokumen sumber**
   - PR
   - PO
   - GR / STJ
   - Return
   - Vendor Bill
   - Payment
   - Reconciliation

4. **Jurnal terkait**
   - semua journal lines yang terkait ke item
   - move_name / move_id
   - account_id
   - debit
   - credit
   - balance
   - source document
   - reference / narration

5. **Status STJ/SVL**
   - apakah SVL terbentuk
   - apakah SVL amount = 0
   - apakah STJ header ada
   - apakah STJ line ada
   - apakah STJ rusak / kosong / missing lines
   - apakah ada JE lanjutan seperti:
     - changed cost from 0.0 to xxx
     - RAC / Reklass ke clearing

## 5. Aturan Umum Klasifikasi

### 5.1 Unit Analisis
Classifier harus mengevaluasi **per item**, lalu menyusun ringkasan di level cycle.

### 5.2 Agregasi
Untuk tiap item, agregasikan seluruh jurnal relevan berdasarkan:
- akun,
- dokumen,
- arah debit/kredit,
- nilai net balance.

### 5.3 Prioritas Sumber Bukti
Urutan prioritas bukti untuk klasifikasi:
1. **Journal lines item-level** yang benar-benar mengandung item / product reference
2. **SVL dan relasinya**
3. **STJ / account move** terkait GR
4. **Bill lines**
5. **Narasi / communication / reference text**
6. **Heuristik fallback**

### 5.4 Prioritas Klasifikasi Case
Bila satu item tampak cocok ke lebih dari satu case, gunakan prioritas berikut:

1. **Case 5 / 6** → No STJ
2. **Case 1 / 2 / 3 / 4** → STJ exists
3. **Price-diff refinement** → Case 2
4. **Expenses-hit refinement** → Case 3 / 4
5. Bila tetap ambigu → **Belum Terdefinisi**

Alasannya sederhana: kalau “tidak ada STJ” salah deteksi menjadi “ada STJ”, repair bisa ngawur. Dan jurnal ngawur itu seperti mantan toxic: sekali masuk, berantakan satu rumah.

## 6. Group Utama Classifier

Gunakan group classifier berikut pada level item, lalu rekap ke cycle:

### Group 1 : STJ - Bill Miss Match (Clearing - Suspend)
- inti masalah: STJ mengenai **Clearing**, Bill mengenai **Suspensed**
- akun bermasalah tersisa di **dua akun sekaligus**
- terkait **Case 1**

### Group 2 : STJ - Bill Price Diff (Suspend - Suspend)
- STJ dan Bill sama-sama mengenai **Suspensed**
- namun nominal berbeda
- tidak terbentuk jurnal selisih harga
- terkait **Case 2**

### Group 3 : STJ - Bill Hit Expenses (Clearing - Expenses)
- STJ ke **Clearing**
- Bill justru debet ke **expense**
- terkait **Case 3**

### Group 4 : STJ - Bill Hit Expenses (Suspend - Expenses)
- STJ ke **Suspensed**
- Bill justru debet ke **expense**
- terkait **Case 4**

### Group 5 : No STJ - Bill Miss Match (Undirect Clearing - Suspend)
- tidak ada STJ valid
- Bill debet ke **Suspensed**
- terkait **Case 5**

### Group 6 : No STJ - Bill Hit Expenses (Undirect Clearing - Expenses)
- tidak ada STJ valid
- Bill debet ke **expense**
- terkait **Case 6**

### Group Lainnya
Jika cycle memiliki akun bermasalah tetapi **tidak ada item** yang cocok dengan case di atas, masukkan ke:
- **Case Lainnya**
- status cycle: **Belum Terdefinisi**

## 7. Case Definition Per Item

## Case 1 — STJ ke Clearing, Bill ke Suspensed

### Kondisi
Item sudah memiliki:
- Bill
- STJ

Dan jurnal yang terbentuk:

### STJ
- **DB** Akun Persediaan / Stock Valuation Account kategori item
- **CR** **1108099 Clearing – System Pending Entries**

### Bill
- **DB** **2103006 Hutang Suspensed Pengadaan Barang/Jasa / Suspensed Payable for Procurement**
- **CR** **2101002 Hutang Pihak Ketiga - Pengadaan Barang/Jasa / Payable to Third Parties**

### Dampak
Akan ada saldo bermasalah di dua akun sekaligus:
- **1108099 Clearing**
- **2103006 Suspensed**

### Klasifikasi
- **Group 1**
- **Case 1**

### Solusi Repair
Tujuan repair adalah **membalikkan / mempertemukan** kedua akun bermasalah tersebut.

Contoh jurnal repair:
- **DB 1108099 Clearing – System Pending Entries**
- **CR 2103006 Hutang Suspensed Pengadaan Barang/Jasa / Suspensed Payable for Procurement**
- jika ada selisih:
  - **DB/CR akun expenses kategori item**

### Catatan Teknis
Nilai repair dasar = nilai overlap yang bisa ditutup antara:
- saldo kredit clearing item, dan
- saldo debit suspensed item

Jika nilai STJ dan Bill berbeda, selisih diarahkan ke akun expense kategori item.

## Case 2 — STJ dan Bill sama-sama ke Suspensed, tapi nominal berbeda

### Kondisi
Item sudah memiliki:
- Bill
- STJ

Dan jurnal yang terbentuk:

### STJ
- **DB** Akun Persediaan / Stock Valuation Account kategori item
- **CR** **2103006 Suspensed Payable for Procurement**

### Bill
- **DB** **2103006 Suspensed Payable for Procurement**
- **CR** **2101002 Hutang Pihak Ketiga**

### Masalah
Akun **2103006 Suspensed** muncul pada STJ dan Bill, tetapi nilainya **berbeda secara agregasi**, biasanya karena:
- perbedaan harga antara GR valuation vs Bill,
- jurnal selisih harga tidak terbentuk.

### Klasifikasi
- **Group 2**
- **Case 2**

### Solusi Repair
Tujuan repair adalah menutup selisih pada akun suspensed dan memindahkan perbedaan ke akun expense kategori item.

Contoh konsep jurnal:
- **DB/CR 2103006 Suspensed**
- pasangan lawan:
- **DB/CR akun expenses kategori item**

### Koreksi Redaksi Teknis
Pada case ini jangan menulis dua baris lawan sama-sama 2103006. Secara implementasi harus:
- satu sisi ke **2103006**
- sisi lawannya ke **akun expense kategori item**

### Formula Dasar
- `price_diff = bill_amount_item - stj_amount_item`
- jika `price_diff > 0`, berarti Bill lebih besar dari STJ
- jika `price_diff < 0`, berarti STJ lebih besar dari Bill

Posting diarahkan agar saldo net akun **2103006** menjadi 0 pada item tersebut.

## Case 3 — STJ ke Clearing, Bill ke Expense

### Kondisi
Item sudah memiliki:
- Bill
- STJ

Dan jurnal yang terbentuk:

### STJ
- **DB** Akun Persediaan / Stock Valuation Account kategori item
- **CR** **1108099 Clearing – System Pending Entries**

### Bill
- **DB** akun expenses kategori item
- **CR** **2101002 Hutang Pihak Ketiga**

### Masalah
Bill seharusnya menyapu akun transisi, tetapi malah langsung ke expense.

### Klasifikasi
- **Group 3**
- **Case 3**

### Solusi Repair
Tujuan repair adalah mereklas debit expense ke akun clearing.

Contoh jurnal:
- **DB 1108099 Clearing – System Pending Entries**
- **CR akun expenses kategori item** *(zeroing expenses)*
- jika ada selisih:
  - **DB/CR akun expenses kategori item**

### Catatan Teknis
Repair dilakukan sebesar nominal yang relevan antara:
- saldo kredit clearing dari STJ
- debit expense dari Bill

Selisih final tetap diarahkan ke akun expense kategori item.

## Case 4 — STJ ke Suspensed, Bill ke Expense

### Kondisi
Item sudah memiliki:
- Bill
- STJ

Dan jurnal yang terbentuk:

### STJ
- **DB** Akun Persediaan / Stock Valuation Account kategori item
- **CR** **2103006 Suspensed Payable for Procurement**

### Bill
- **DB** akun expenses kategori item
- **CR** **2101002 Hutang Pihak Ketiga**

### Masalah
Bill tidak menyapu suspensed, tetapi langsung masuk ke expense.

### Klasifikasi
- **Group 4**
- **Case 4**

### Solusi Repair
Tujuan repair adalah mereklas debit expense ke suspensed.

Contoh jurnal:
- **DB 2103006 Suspensed Payable for Procurement**
- **CR akun expenses kategori item** *(zeroing expenses)*
- jika ada selisih:
  - **DB/CR akun expenses kategori item**

### Catatan Teknis
Nilai yang dipindahkan = bagian debit expense yang secara logika seharusnya dipakai untuk menutup suspensed item tersebut.

## Case 5 — Tidak ada STJ valid, Bill ke Suspensed

### Kondisi
Item sudah memiliki:
- Bill
- **tidak memiliki STJ valid**

Karena saat GR/validate, nilai SVL = 0, sehingga sistem tidak membentuk STJ normal.

Tetapi mungkin belakangan terdapat jurnal seperti:
- `changed cost from 0.0 to xxx - [Kode Item] Nama Item`
- dengan jurnal:
  - **DB** Akun Persediaan kategori item
  - **CR** akun expenses kategori item

Lalu terdapat **RAC / Reklass** misalnya:
- **DB** akun expenses kategori item
- **CR 1108099 Clearing – System Pending Entries**

### Guardrail Wajib
Case 5 hanya boleh dipilih jika sistem memastikan:
1. **SVL benar-benar bernilai 0** saat GR,
2. **tidak ada STJ valid**,
3. tidak ada journal move inventory valid untuk item itu,
4. jika ada STJ header kosong / rusak / tanpa lines, itu tetap diperlakukan sebagai **tidak ada STJ valid**,
5. kasus langka seperti archive/move rusak boleh masuk sini hanya bila bukti kuat menunjukkan STJ substantif memang tidak terbentuk.

### Jurnal Terlihat
#### STJ
- **DB** Tidak ada
- **CR** Tidak ada

#### Bill
- **DB 2103006 Suspensed Payable for Procurement**
- **CR 2101002 Hutang Pihak Ketiga**

### Klasifikasi
- **Group 5**
- **Case 5**

### Solusi Repair
Tujuan repair adalah memindahkan debit suspensed ke clearing secara tidak langsung berdasarkan nilai yang semestinya berasal dari GR.

Contoh jurnal:
- **DB 2103006 Suspensed Payable for Procurement** sebesar `standard_cost x qty_base_unit`
- **CR 1108099 Clearing – System Pending Entries**
- jika ada selisih:
  - **DB/CR akun expenses kategori item**

### Catatan Penting
Jika sistem berhasil mendeteksi jurnal lanjutan:
- changed cost,
- reklas ke clearing,
- RAC final,

maka classifier harus menggunakan **nilai akhir yang benar-benar tersisa** setelah jurnal-jurnal tersebut, bukan membabi buta memakai nilai teoritis.

Kalau tidak bisa mendeteksi secara andal, gunakan fallback:
- `standard_cost x qty_base_unit`

## Case 6 — Tidak ada STJ valid, Bill ke Expense

### Kondisi
Item sudah memiliki:
- Bill
- **tidak memiliki STJ valid**

Dan sama seperti Case 5, penyebab awal biasanya:
- SVL saat GR = 0,
- STJ tidak terbentuk,
- mungkin ada jurnal changed cost belakangan,
- mungkin ada RAC ke clearing.

### Guardrail Wajib
Aturan validasi sama dengan Case 5.

### Jurnal Terlihat
#### STJ
- **DB** Tidak ada
- **CR** Tidak ada

#### Bill
- **DB** akun expenses kategori item
- **CR** **2101002 Hutang Pihak Ketiga**

### Klasifikasi
- **Group 6**
- **Case 6**

### Solusi Repair
Tujuan repair adalah menggantikan debit expense yang tidak tepat menjadi clearing.

Contoh jurnal:
- **DB 1108099 Clearing – System Pending Entries** sebesar `standard_cost x qty_base_unit`
- **CR akun expenses kategori item** *(zeroing expenses)*
- jika ada selisih:
  - **DB/CR akun expenses kategori item**

### Catatan Perbaikan Redaksi
Pada deskripsi awal Anda tertulis:
> “mereklas DB 2103006 dipindahkan ke Clearing”

Tetapi contoh jurnalnya:
- DB Clearing
- CR Expense

Secara substansi **contoh jurnalnya yang lebih konsisten** untuk Case 6.
Jadi implementasi sebaiknya mengikuti **contoh jurnal**, yaitu:
- pindahkan debit yang salah dari **expense** menjadi **clearing**.

## 8. Deteksi Sehat vs Bermasalah vs Undefined

Gunakan urutan keputusan berikut:

### 8.1 Sehat
Tandai **Cycle Sehat** bila:
- belum ada bill, atau
- bill ada tetapi akun transisi dan payable sudah closed secara neto, atau
- tidak ditemukan item yang menyisakan saldo abnormal.

### 8.2 Bermasalah dan Terklasifikasi
Tandai **Cycle Bermasalah** bila:
- bill ada,
- ada saldo abnormal di akun bermasalah,
- dan ada item yang cocok dengan Case 1 s.d. Case 6.

### 8.3 Belum Terdefinisi
Tandai **Cycle Belum Terdefinisi** bila:
- bill ada,
- ada saldo abnormal,
- tetapi item-item tidak cocok dengan seluruh case di atas,
- atau banyak item overlap/konflik,
- atau bukti data tidak cukup,
- atau repair berisiko salah.

## 9. Aturan Penting untuk Auto-Repair

### 9.1 Prinsip Konservatif
Repair otomatis **hanya boleh dilakukan** jika:
- case item teridentifikasi dengan confidence tinggi,
- nilai sumber repair dapat ditelusuri jelas,
- akun kategori item dapat dipastikan,
- tidak ada konflik dengan return/adjustment lain yang belum dipetakan.

### 9.2 Jangan Repair Jika
Jangan auto-repair bila:
1. item cocok ke lebih dari satu case tanpa prioritas yang jelas,
2. terdapat return yang belum teralokasi,
3. terdapat lebih dari satu bill untuk item yang sama dan belum di-split dengan benar,
4. terdapat jurnal manual user yang mengubah posisi akun sehingga pola tidak lagi standar,
5. akun kategori item berubah di tengah cycle,
6. product mapping / item mapping tidak valid,
7. ditemukan STJ header tetapi line hilang dan nilainya tidak dapat direkonstruksi,
8. nominal sumber repair tidak bisa dibuktikan.

Jika salah satu kondisi di atas terjadi:
- tandai sebagai **Belum Terdefinisi**
- atau **Needs Manual Review**

### 9.3 Toleransi Nilai
Gunakan toleransi pembulatan, misalnya:
- `abs(balance) <= tolerance` dianggap 0
- tolerance bisa diset misalnya **1**, **10**, atau **100** sesuai currency precision dan kebijakan perusahaan.

## 10. Edge Cases Tambahan yang Perlu Diakomodasi

Berikut case tambahan yang Anda belum tulis eksplisit, tetapi sangat mungkin muncul dan sebaiknya dimasukkan sebagai guardrail atau future extension.

### Edge Case A — Partial Bill
Satu GR/STJ hanya dibill sebagian.
- Jangan paksa seluruh saldo dianggap mismatch.
- Cocokkan proporsional berdasarkan qty atau value billed.

### Edge Case B — Multiple Bills untuk 1 Item
Satu item dibagi ke beberapa bill.
- Agregasi bill per item harus dijumlahkan dulu sebelum klasifikasi.
- Jika tidak, Case 2 akan sering salah terdeteksi.

### Edge Case C — Return Setelah GR
Ada return terhadap item.
- Qty net dan valuation net harus diperhitungkan.
- Jangan repair atas qty yang sudah direturn.

### Edge Case D — Manual Journal Intervensi
User membuat jurnal manual yang “mirip solusi”.
- Sistem perlu mendeteksi apakah akun bermasalah sebenarnya sudah pernah direklas.
- Jangan membuat repair ganda.

### Edge Case E — STJ Header Ada, Isi Rusak/Kosong
- Perlakukan sebagai **No STJ Valid**, bukan “STJ ada”.
- Ini penting agar tidak salah lempar ke Case 1–4.

### Edge Case F — Akun Expense Kategori Berubah
- Gunakan akun expense yang aktif pada saat transaksi, atau akun expense yang benar-benar dipakai pada jurnal Bill.
- Jika berbeda, tandai ambiguity.

### Edge Case G — Item Non-Inventory tapi Masuk Flow Inventory
- Jika item secara master adalah non-inventory tetapi sudah terlanjur punya SVL/STJ, classifier tetap fokus pada **fakta jurnal**, bukan hanya master data saat ini.

### Edge Case H — Currency / Rate Difference
- Jika bill dalam foreign currency, selisih mungkin bukan murni price diff item.
- Pisahkan selisih kurs dari selisih harga item jika data tersedia.

### Edge Case I — Landed Cost / Additional Cost
- Jika ada landed cost yang sah, jangan salah klasifikasikan sebagai mismatch price diff biasa.

### Edge Case J — Negative Qty / Reversal
- Bila item mengalami reverse posting / cancel / reversal, jangan langsung pakai formula normal.
- Wajib cek arah kuantitas dan arah balance.

## 11. Output yang Harus Dihasilkan Classifier

Untuk setiap **item**, engine harus mengeluarkan minimal:

- `cycle_id`
- `item_id`
- `item_code`
- `item_name`
- `group_case`
- `case_code`
- `classification_confidence`
- `has_stj_valid`
- `has_bill`
- `svl_amount`
- `stj_amount`
- `bill_amount`
- `clearing_balance`
- `suspended_balance`
- `expense_balance`
- `payable_balance`
- `suggested_repair_entry`
- `repair_amount_base`
- `repair_amount_diff`
- `repair_status`
- `needs_manual_review`
- `reasoning_trace`

Untuk level **cycle**, sistem mengeluarkan:
- `cycle_status = Sehat / Bermasalah / Belum Terdefinisi`
- `dominant_group`
- `list_of_item_cases`
- `total_problematic_balance_clearing`
- `total_problematic_balance_suspended`
- `total_items`
- `total_problematic_items`
- `auto_repairable_items`
- `manual_review_items`

## 12. Aturan Penentuan Suggested Repair Entry

Untuk setiap item terklasifikasi, bentuk suggested repair harus memuat:

1. **akun debit**
2. **akun kredit**
3. **nominal dasar**
4. **nominal selisih**
5. **akun offset selisih**
6. **narasi jurnal**
7. **evidence / dasar pengambilan**

Contoh struktur:
- `repair_main_debit_account`
- `repair_main_credit_account`
- `repair_main_amount`
- `repair_diff_account`
- `repair_diff_direction`
- `repair_diff_amount`
- `repair_narration`

Contoh narasi:
- `Auto repair Case 1 - Reclass Clearing vs Suspensed for [Item Code] [Item Name]`
- `Auto repair Case 2 - Price difference adjustment for [Item Code] [Item Name]`

## 13. Decision Tree Ringkas

Gunakan logika berikut pada level item:

1. **Apakah Bill ada?**
   - tidak → bukan target mismatch final, kemungkinan sehat / pending
   - ya → lanjut

2. **Apakah ada saldo abnormal di 1108099 atau 2103006?**
   - tidak → sehat
   - ya → lanjut

3. **Apakah ada STJ valid?**
   - ya → kandidat Case 1–4
   - tidak → kandidat Case 5–6

4. **Jika ada STJ valid:**
   - STJ credit ke Clearing + Bill debit ke Suspensed → **Case 1**
   - STJ credit ke Suspensed + Bill debit ke Suspensed, nominal beda → **Case 2**
   - STJ credit ke Clearing + Bill debit ke Expense → **Case 3**
   - STJ credit ke Suspensed + Bill debit ke Expense → **Case 4**
   - selain itu → undefined

5. **Jika tidak ada STJ valid:**
   - Bill debit ke Suspensed → **Case 5**
   - Bill debit ke Expense → **Case 6**
   - selain itu → undefined

## 14. Prompt Final Siap Pakai

Anda adalah engine **classifier dan auto-repair purchase cycle anomaly** untuk sistem analisis jurnal pembelian berbasis Odoo.

Tugas Anda adalah menganalisis seluruh purchase cycle yang telah dikumpulkan dari dokumen:
- PR
- PO
- GR / STJ
- Return
- Vendor Bill
- Bank Payment
- Reconciliation

Fokus utama Anda adalah menemukan **anomali jurnal pembelian** yang terjadi akibat gagalnya logic server Odoo, khususnya pada akun transisi pembelian, lalu mengklasifikasikannya pada **level item**, bukan hanya level cycle.

### Akun bermasalah utama
Anggap akun berikut sebagai akun bermasalah apabila setelah cycle mencapai tahap bill masih memiliki saldo:
- **1108099 Clearing – System Pending Entries**
- **2103006 Hutang Suspensed Pengadaan Barang/Jasa / Suspensed Payable for Procurement**

### Prinsip klasifikasi
- Analisis harus dilakukan pada **level item**
- Satu cycle dapat memiliki banyak item
- Satu cycle dapat memiliki beberapa case berbeda
- Satu item dapat memiliki lebih dari satu gejala, namun classifier harus memilih **case paling tepat** berdasarkan prioritas dan bukti jurnal
- Jika case tidak dapat dipastikan dengan andal, tandai sebagai **Belum Terdefinisi / Needs Manual Review**

### Status cycle
Klasifikasikan cycle menjadi:
1. **Cycle Sehat**
2. **Cycle Bermasalah**
3. **Cycle Belum Terdefinisi**

### Cycle Sehat
Cycle dianggap sehat bila:
- belum memiliki Vendor Bill, atau
- sudah memiliki Bill tetapi saldo agregat akun berikut sudah 0 / substantively closed:
  - 11120003 Outstanding Payments
  - 2101002 Hutang Pihak Ketiga - Pengadaan Barang/Jasa / Payable to Third Parties
  - 2103006 Hutang Suspensed Pengadaan Barang/Jasa / Suspensed
- dan tidak ada saldo abnormal pada akun bermasalah di level item/cycle

### Group classifier
Gunakan group berikut:
- **Group 1 : STJ - Bill Miss Match (Clearing - Suspend)** → Case 1
- **Group 2 : STJ - Bill Price Diff (Suspend - Suspend)** → Case 2
- **Group 3 : STJ - Bill Hit Expenses (Clearing - Expenses)** → Case 3
- **Group 4 : STJ - Bill Hit Expenses (Suspend - Expenses)** → Case 4
- **Group 5 : No STJ - Bill Miss Match (Undirect Clearing - Suspend)** → Case 5
- **Group 6 : No STJ - Bill Hit Expenses (Undirect Clearing - Expenses)** → Case 6
- jika tidak cocok ke semuanya tetapi akun bermasalah ada → **Case Lainnya / Belum Terdefinisi**

### Case 1
Kondisi:
- item memiliki STJ valid dan Bill
- STJ:
  - DB akun persediaan item
  - CR 1108099 Clearing
- Bill:
  - DB 2103006 Suspensed
  - CR 2101002 Payable to Third Parties

Klasifikasi:
- Group 1 / Case 1

Repair:
- DB 1108099 Clearing
- CR 2103006 Suspensed
- jika ada selisih:
  - arahkan selisih ke akun expenses kategori item

### Case 2
Kondisi:
- item memiliki STJ valid dan Bill
- STJ:
  - DB akun persediaan item
  - CR 2103006 Suspensed
- Bill:
  - DB 2103006 Suspensed
  - CR 2101002 Payable to Third Parties
- nominal STJ vs Bill berbeda

Klasifikasi:
- Group 2 / Case 2

Repair:
- tutup selisih pada 2103006 Suspensed
- lawannya ke akun expenses kategori item
- tujuan akhir: saldo 2103006 pada item = 0

### Case 3
Kondisi:
- item memiliki STJ valid dan Bill
- STJ:
  - DB akun persediaan item
  - CR 1108099 Clearing
- Bill:
  - DB akun expenses kategori item
  - CR 2101002 Payable to Third Parties

Klasifikasi:
- Group 3 / Case 3

Repair:
- DB 1108099 Clearing
- CR akun expenses kategori item
- jika ada selisih:
  - arahkan ke akun expenses kategori item

### Case 4
Kondisi:
- item memiliki STJ valid dan Bill
- STJ:
  - DB akun persediaan item
  - CR 2103006 Suspensed
- Bill:
  - DB akun expenses kategori item
  - CR 2101002 Payable to Third Parties

Klasifikasi:
- Group 4 / Case 4

Repair:
- DB 2103006 Suspensed
- CR akun expenses kategori item
- jika ada selisih:
  - arahkan ke akun expenses kategori item

### Case 5
Kondisi:
- item memiliki Bill
- tidak memiliki STJ valid
- SVL saat GR bernilai 0 atau tidak memunculkan STJ valid
- boleh ada jurnal lanjutan seperti:
  - changed cost from 0.0 to xxx
  - RAC / reklas ke clearing
- tetapi secara substantif tidak ada STJ valid untuk item

Bill:
- DB 2103006 Suspensed
- CR 2101002 Payable to Third Parties

Guardrail:
- pastikan SVL benar-benar 0 saat GR
- tidak ada STJ valid
- jika hanya ada STJ header kosong / rusak / missing line, tetap anggap no STJ valid

Klasifikasi:
- Group 5 / Case 5

Repair:
- DB 2103006 Suspensed sebesar standard_cost x qty_base_unit atau berdasarkan nilai akhir yang dapat dibuktikan
- CR 1108099 Clearing
- jika ada selisih:
  - arahkan ke akun expenses kategori item

### Case 6
Kondisi:
- item memiliki Bill
- tidak memiliki STJ valid
- SVL saat GR bernilai 0 atau tidak memunculkan STJ valid
- Bill:
  - DB akun expenses kategori item
  - CR 2101002 Payable to Third Parties

Klasifikasi:
- Group 6 / Case 6

Repair:
- DB 1108099 Clearing sebesar standard_cost x qty_base_unit atau berdasarkan nilai akhir yang dapat dibuktikan
- CR akun expenses kategori item
- jika ada selisih:
  - arahkan ke akun expenses kategori item

### Prioritas klasifikasi
Jika sebuah item tampak cocok dengan lebih dari satu case, gunakan prioritas:
1. No STJ cases (Case 5–6)
2. STJ exists cases (Case 1–4)
3. Price difference refinement (Case 2)
4. Expenses-hit refinement (Case 3–4)
5. jika tetap ambigu → Belum Terdefinisi

### Jangan lakukan auto-repair jika:
- ada return yang belum teralokasi benar
- ada partial bill yang belum dipetakan
- ada multi-bill tanpa split item yang jelas
- ada jurnal manual yang mengubah pola normal
- akun kategori item berubah dan tidak jelas mana yang valid
- ada STJ rusak yang nilainya tidak dapat direkonstruksi
- nilai repair tidak bisa dibuktikan

### Edge cases yang harus dipertimbangkan
- partial bill
- multiple bills per item
- return setelah GR
- manual JE intervensi
- STJ header ada tapi line kosong
- akun expense kategori berubah
- item non-inventory yang terlanjur masuk flow inventory
- currency difference
- landed cost
- reversal / negative qty

### Output wajib per item
Keluarkan output minimal:
- cycle_id
- item_id
- item_code
- item_name
- group_case
- case_code
- classification_confidence
- has_stj_valid
- has_bill
- svl_amount
- stj_amount
- bill_amount
- clearing_balance
- suspended_balance
- expense_balance
- payable_balance
- suggested_repair_entry
- repair_amount_base
- repair_amount_diff
- repair_status
- needs_manual_review
- reasoning_trace

### Output wajib per cycle
- cycle_status
- dominant_group
- list_of_item_cases
- total_problematic_balance_clearing
- total_problematic_balance_suspended
- total_items
- total_problematic_items
- auto_repairable_items
- manual_review_items

Tujuan akhir Anda adalah:
1. menentukan apakah cycle sehat, bermasalah, atau belum terdefinisi,
2. menentukan case per item secara akurat,
3. menyusun suggested repair entry yang aman, konsisten, dan dapat diaudit,
4. menghindari false repair pada data yang ambigu.
