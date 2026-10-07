# Balance Cycle Pembelian â€” Logic Documentation

**Modul**: `svl_fix_je`
**Mode**: Balance Cycle Pembelian (Purchase Cycle Balance Mode)
**File utama**: `smartscc_tools/features/svl_fix_je/dashboard_service.py`, `smartscc_tools/modules/svl_fix_je_dashboard_page.py`, `smartscc_tools/features/svl_fix_je/models.py`
**Terakhir dianalisis**: 2026-04-12
**Terakhir diupdate**: 2026-10-05 — relokasi, koreksi GRNI/payment, dan aturan lima baris Case 9 yang dikonfirmasi pengguna serta guard projected clearing.

> **Keputusan pengguna 2026-10-05:** downstream Case 9 dengan saldo HPP dan
> Persediaan menggunakan lima baris: clearing nol dan selisih masuk akun HPP
> kategori. Ini menggantikan ekspektasi empat baris pada tes/dokumentasi lama.
> Line selisih nol dihilangkan. Semua row Case 9 tetap wajib review manual.

---

## Normal flow pemulihan penerimaan ? keputusan pengguna 2026-10-05

Aturan ini menggantikan **Synthetic Clearing / Undirect Clearing Case 5 dan Case 6**.
Tidak ada tujuan clearing baru untuk pemulihan penerimaan. Saldo clearing existing
pada case lain tetap merupakan bukti yang harus diselesaikan; aturan downstream
Case 9 lima baris dan review wajib tidak berubah.

- Nilai pemulihan = jumlah **SVL receipt aktual yang belum terhubung JE**, bukan
  `standard_price ? qty` saat ini. `svl_zero_at_gr` dibaca dari layer receipt
  aktual ketika evidence tersedia; JE hilang tidak berarti nilai SVL nol.
- STJ hasil ekspansi pada produk/vendor yang sama tidak membuktikan receipt sudah
  pulih bila SVL receipt tersebut masih unlinked. Evidence SVL aktual mengalahkan
  jejak STJ agregat; inventory existing yang ambigu memblokir default plan.
- Bill hit ditentukan dari AML vendor bill item/purchase line. Akun beban bank
  atau admin pada saldo item tidak boleh mengalahkan bill suspense yang jelas.
- Case 5: Dr valuation kategori / Cr **2103006** sebesar SVL aktual. Sisa suspense
  diselesaikan dengan akun expense kategori setelah review revaluasi/pemakaian.
  Bila standard cost tersedia, tambahan beban diurai menjadi kandidat offset
  perubahan cost `qty missing ? standard ? SVL` dan selisih bill terhadap standard.
  Keduanya bertanda signed; saldo negatif menjadi kredit, baris nol dihilangkan.
  Perubahan standard cost **bukan bukti revaluasi posted atau atribusi ke receipt**.
  Alokasi revaluasi bersama adalah model analitis; finance harus memeriksa seluruh
  jurnal revaluasi/pemakaian di luar rentang receipt sebelum Confirm Review.
- Case 6: Dr valuation kategori / Cr **akun expense bill aktual** sebesar SVL.
  Expense bill tidak di-zero penuh atau diganti otomatis dengan expense kategori.
  Tidak ada tambahan debit beban karena bill sudah mengakui biaya.
- Satu row menghasilkan **satu JE seimbang**: dua receipt legs dan, bila diperlukan,
  tiga value-only cost legs. Efek akun sama dengan dua JE manual receipt + cost,
  tetapi aplikasi mempertahankan granularity satu JE per row. Qty receipt legs
  memakai qty SVL; qty cost legs eksplisit nol.
- Semua pemulihan Case 5/6 wajib review manual. SVL kosong/non-positive, source bill
  ambigu, qty bill tidak sama dengan seluruh receipt terkait, valuation account
  tidak tersedia, saldo inventory existing yang belum dicocokkan, return terkait, atau residual clearing existing menghasilkan row Incomplete
  tanpa plan executable. Return/partial receipt harus ditelusuri terlebih dahulu.
- Draft lama Case 5/6 yang belum membawa `normal_receipt` evidence ditolak saat
  execute dan harus di-analyze ulang. Manual override tidak boleh memasukkan
  clearing ke normal flow atau mengubah net nilai inventory dari SVL aktual.
- Execute membaca ulang SVL dan AML bill exact sebelum create. Layer yang sudah
  ditautkan ke JE lain dan suspense yang sudah direkonsiliasi/berubah memblokir
  create. Existing exact JE digunakan ulang untuk retry.
- Draft **tidak** mengubah link SVL. Setelah posted, existing SVL ditautkan ke JE
  dan dibaca ulang; qty/value tidak diubah dan tidak membuat SVL baru. Reconcile
  suspense tetap manual di luar fitur ini. Preview dan payload execute tetap sama.

## Ringkasan

Mode ini memvisualisasikan keseimbangan akuntansi dari setiap *purchase cycle* secara lengkap â€” mulai dari penerimaan barang (GR/STJ), tagihan vendor (Bill), pembayaran (Payment), hingga rekening bank (Bank JE). Setiap cycle diperiksa apakah semua transaksi sudah balance (debit = credit per akun yang relevan).

### Baseline aktif per 2026-04-11
- Classifier aktif sekarang **item-first** dengan 9 case repairable:
  - `case1` Clearing-Suspend
  - `case2` Suspend-Suspend price diff
  - `case3` Clearing-Expenses
  - `case4` Suspend-Expenses
  - `case5` No STJ + Suspend
  - `case6` No STJ + Expenses
  - `case8a` Full Return Value Mismatch
  - `case8b` Partial Return Value Mismatch
  - `case9` UoM Scale Mismatch
- Edge case review-only:
  - `edge_partial_bill`
  - `edge_return_no_credit_memo`
  - `edge_stj_corrupt`
- Cycle tetap tampil **cycle-first**, tetapi grouping memakai model **hybrid**:
  - `primary_case`
  - `case_counts`
  - `edge_flags`
  - `mixed_case_summary`
- `case1_link_rows` sekarang khusus **Case 1**.
- `case2_repair_rows` sekarang menjadi container row multi-line **Case 2-6, Case 8A/8B, dan Case 9**.
- **GRNI Guard (dikoreksi 2026-10-05)**: Item yang belum ditagih vendor (`has_exact_bill_for_item = False`, yaitu `has_item_bill = False` dan `bill_quantity ≤ 0.01`) di-early-exit dengan `primary_case = ""` dan `auto_repairable = False` bila tidak ada evidence value gap Case 8/9. Saldo proporsional dari sibling saja bukan bukti repair; tetapi return/SVL mismatch dan PO-UoM mismatch dapat terbukti sebelum bill terbit dan harus tetap diklasifikasikan. Evidence kosong atau gap nol tetap terkena guard. Fallback `bill_hit_role = "suspend"` via `has_problem_2103006` juga diblok untuk item tanpa exact bill.
- Untuk **Case 5 / Case 6**, nominal repair memakai SVL receipt aktual yang belum punya JE; standard cost hanya dasar perbandingan cost dan review, bukan pengganti SVL.
- Untuk **Case 8 / Case 9**, row repair default selalu `review_required=True`; execute diblok sampai user melakukan review confirmation.
- Jika evidence **Case 8 / Case 9** valid, cycle dipromosikan ke **Cycle Bermasalah** walaupun saldo akun problem lama sudah balance, supaya case tetap terlihat pada filter default dashboard.
- Evidence **Case 8 / Case 9** memakai pre-index `stock.move` per picking/return picking supaya analyze tidak men-scan ulang seluruh stock move untuk setiap cycle.
- Trace GR -> STJ fallback -> SVL path -> picking sekarang mengeksekusi chunk ID secara paralel, lalu menggabungkan hasilnya dengan urutan chunk tetap stabil.
- Matching correction JE PCB dipersempit dulu dengan index `product_id`, `picking_id`, dan `bill_move_id` sebelum validator cycle-level dijalankan, supaya satu cycle tidak lagi men-scan seluruh kandidat correction move.
- Adjustment audit dan evidence Case 3/4 sekarang membaca `stock.move` dari index per picking yang dibawa di trace, bukan scan penuh `stock_move_rows_by_id` untuk setiap cycle.
- Payment tracing memilih `account.payment.invoice_ids` lebih dulu bila tersedia dan membaca link payment per chunk; `reconciled_bill_ids` hanya fallback untuk schema lama karena live DB dapat mengekspos field ini tetapi menolak domain `search_read`.
- Fetch awal `account.move.line` bill dan tracing `account.payment` dimulai bersamaan setelah `bill_ids` diketahui, lalu hasilnya digabung ke trace yang sama.
- Rebuild repair rows melakukan early-exit hanya di pass akhir: setelah inisialisasi Case 3/4, verifikasi adjustment, evidence Case 8/9, external clearing, classifier item, dan `case1_link_rows` selesai. Cycle yang masih `healthy` pada titik itu melewati `_classify_pcb_cycle_case` dan `_build_pcb_case2_repair_rows`; cycle yang dipromosikan ke `problem` oleh `case8a`/`case8b`/`case9` tetap diproses penuh.
- Trace BFS sekarang memanaskan `matching_move_rows_by_id` dari fetch STJ/Bill yang sudah ada, lalu mencatat bench terpisah untuk `trace/bfs_seed_partners`, `trace/bfs_seed_init`, dan `trace/post_bfs_maps` supaya gap pra-ronde BFS terlihat jelas.
- Adjustment audit sekarang mencatat bench terpisah untuk post-processing sinkron `adj/attach_rows` dan `adj/rebuild_case2`; fetch metadata `account.move`, `account.account`, `product.product`, dan `product.category` yang besar dibaca per chunk secara paralel.
- Build cycle sekarang mencatat bench terpisah untuk `build/product_info`, `build/index_prep`, `build/group_merge`, `build/correction_match`, `build/cycle_assembly`, dan `build/cycle_rows` supaya regresi di fase `cycle_grouping` bisa dipisah antara fetch metadata produk, merge picking, kandidat correction, dan assembly cycle utama.
- Matching correction JE PCB sekarang precompute metadata problem-line per move sekali di awal (`product_id`, `picking_id`, `bill_move_id`, `bill_line_id`, `stock_move_id`, `purchase_line_id`, `ref`) lalu reuse metadata itu saat scan per cycle, sehingga validator cycle tidak perlu parse line correction yang sama berulang-ulang.
- Rebuild adjustment audit sekarang mencatat bench terpisah untuk `adj/rebuild/index_prep`, `adj/rebuild/cycle_prefill`, `adj/rebuild/external_clearing`, `adj/external_index`, `adj/external_match`, `adj/external_finalize`, dan `adj/rebuild/finalize` supaya biaya prefill cycle, matching RAC lintas cycle, dan final classification bisa dibaca terpisah.
- External clearing Case 3/4 sekarang membangun index lintas cycle sekali (`purchase_line_id`, `stock_move_id`, `product_id`, `source_account_code`) lalu reuse untuk explicit-origin dan unique-amount matching, sehingga tidak lagi scan semua cycle untuk setiap AML source adjustment.
- Untuk **Case 9**, nilai benar memakai current `standard_price` bila tersedia; fallback ke bill subtotal lalu PO subtotal bila standard price kosong/nol.
- Cycle sekarang punya metadata klasifikasi dokumen yang terpisah dari `cycle_status`: `inventory_types`, `document_classification`, `document_classification_label`, dan `document_classification_reasons`.

---

## 1. Definisi Satu "Cycle" Pembelian

**Anchor utama**: Satu record `stock.picking` (Goods Receipt / LHPK).

| Field                | Sumber                                            |
|----------------------|---------------------------------------------------|
| `picking_id`         | `stock.picking.id`                                |
| `picking_name`       | e.g. `LHPK/IN/00459`                             |
| `gr_date`            | `stock.picking.scheduled_date` atau `date_done`   |
| `partner_name`       | Dari PO atau picking langsung                     |

### Aturan Penggabungan (Merge)

Jika **beberapa picking berbagi satu vendor bill yang sama**, mereka digabung menjadi **satu cycle** menggunakan algoritma **union-find** (kode ~baris 1785â€“1815). Tujuannya: mencegah triple-counting bill.

Cycle gabungan ditampilkan sebagai: `LHPK/IN/00460 [+2]` â†’ artinya 3 picking berbagi 1 bill.

---

## 2. Transaksi yang Dikumpulkan dalam Satu Cycle

Setiap cycle mengumpulkan semua `account.move` yang berkaitan dengan alur pembelian lengkap:

### 2.1 STJ (Stock Transfer Journal)
- **Sumber**: `stock.valuation.layer` â†’ `stock_move_id` â†’ GR's stock moves
- Merepresentasikan pergerakan inventori/gudang
- **Return picking** (`picking_type_code = outgoing`, misal `WCGT/OUT/xxxxx`): ikut dalam cycle yang sama dengan GR aslinya bila ada relasi `returned_move_ids` (pada GR move) atau `origin_returned_move_id` (pada return move). Lihat Â§2.6.

### 2.2 BILL (Vendor Invoice)
- **Path 1**: PO â†’ `purchase.order.line` â†’ PO name matching
- **Path 2**: `account.move.invoice_origin` field matching PO name
- Fallback: product-based bill lookup jika tidak ada PO, tetapi hanya untuk bill yang partner-compatible dengan partner cycle
- Jika no-PO picking tidak punya partner key yang andal, fallback produk dilewati agar internal transfer / picking non-purchase tidak menarik bill lama hanya karena SKU sama
- Multiple bills bisa masuk dalam satu cycle jika satu PO punya multiple invoices

### 2.3 PBK (Payment)
- **Sumber**: `account.payment` moves yang terhubung ke bills via reconciliation
- Multiple payments bisa mencakup satu cycle's bills
- Dipetakan via `payment_rows_by_bill_id`

### 2.4 BK (Bank JE)
- **Sumber**: `account.move.line` yang punya `matching_number` yang sama dengan payment
- Bisa juga langsung: bill â†’ bank JE (tanpa intermediate payment)

### 2.5 Expansion Moves (BFS)
BFS **iteratif** (max 5 ronde) menelusuri graf rekonsiliasi `matching_number` di `account.move.line`.

**Seed awal (frontier)**:
- Semua **bills** yang sudah diketahui dari PO chain
- Semua **STJs** dari picking yang sedang diproses *(baru â€” sejak 2026-03-24)*

**Eksklusi dari BFS**:
- `matching_number` dari PBK (Payment) â€” sudah dimasukkan ke `_exp_done_mns` di awal, sehingga tidak ditelusuri ulang
- Move yang sudah diketahui (bills, STJs, payments) masuk ke `_exp_known`, tidak bisa jadi kandidat baru

**Per ronde BFS**:
1. **Step A** â€” ambil semua `matching_number` dari lines frontier (exclude yang sudah diproses)
2. **Step B** â€” cari semua `account.move` yang punya matching_number tersebut
3. **Step C** â€” klasifikasi kandidat dengan kombinasi `move_type`, `account_id`, prefix nomor dokumen, dan partner:
   - `in_invoice` / `in_refund` â†’ **Bill baru**
   - `entry` + `stock_move_id`, atau prefix `STJ/` â†’ **STJ baru**
   - `entry` dengan bukti kuat payment/bank-like â†’ **Bank JE / payment-like**
     - prefix `PBK/` atau `BK/`
     - atau line cluster tersebut menyentuh akun payment/bank yang relevan, minimal `2101002`, `11120003`, atau akun cash/liquidity
   - `entry` lainnya â†’ **other_entry**, bukan bank
4. **Step D** â€” daftarkan ke seed linkage:
   - **Bill baru** â†’ `expansion_bills_by_bill[seed_bill]`, tetapi hanya bila partner bill kandidat kompatibel dengan partner seed bill
   - **STJ baru** â†’ `expansion_stjs_by_bill[seed_bill]`, tetapi hanya bila partner STJ kompatibel dengan partner seed bill
   - **Bank JE baru** â†’ `direct_bank_move_ids_by_bill`, hanya untuk candidate `payment/bank-like`
   - **Orphaned STJ seed** (STJ tanpa bill terkait di PO chain) yang menemukan bill baru â†’ `expansion_bills_by_stj[stj_id]`, tetapi hanya bila partner kandidat kompatibel *(baru â€” sejak 2026-03-24)*

**Dedup**: Semua move dikumpulkan dalam `set`, union di `all_cycle_move_ids` â€” tidak ada duplikat meskipun move ditemukan lewat PO chain DAN BFS sekaligus.

**Kondisi trigger BFS**: `"matching_number" in aml_fields AND (bill_ids OR stj_move_ids_by_product)` â€” BFS tetap berjalan meski tidak ada bill sama sekali (Pola 2 / orphaned STJ) *(diubah sejak 2026-03-24)*

**Guard ekspansi aktif (Minimal)**:
- BFS `matching_number` tetap dipakai untuk menangkap payment yang hanya terhubung lewat rekonsiliasi
- Cluster clearing/problem account seperti `2103006` / `1108099` tidak lagi bebas menarik vendor lain hanya karena share `matching_number`
- `MISC` / journal umum yang tidak punya bukti payment/bank-like tidak lagi otomatis dianggap `BK`
- Merge picking via direct BK tetap aktif, tetapi hanya dalam kelompok bill dengan vendor-compatible partner key
- Konsekuensi mode `Minimal`: vendor lain diblok, tetapi same-vendor yang memang tersambung lewat cluster masih boleh ikut

### 2.6 Return Picking (Return-to-Vendor)

Picking outgoing yang merupakan return dari GR (misal `WCGT/OUT/00002` dari `WCGT/IN/00087`) dimasukkan ke cycle yang sama via dua jalur:

1. **`returned_move_ids` pada original GR move** â†’ batch-fetch move IDs yang tidak ada di cache (karena seed phase hanya fetch incoming), resolve ke `picking_id` return
2. **`origin_returned_move_id` pada return move** â†’ berlaku bila return move sudah ada di cache

Return picking dimasukkan ke `return_picking_ids_needed` â†’ picking rows dan stock.moves-nya di-fetch, STJ-nya masuk ke `stj_ids_by_picking[return_picking_id]`.

Union-find Pass 3 merge return picking dengan original picking â†’ keduanya dalam satu cycle.

**BFS guard** (`picking_type_code == incoming`) dikecualikan untuk picking yang ada di `_return_picking_ids`, sehingga STJ dari return picking tidak salah didemotion ke Bank JE.

---

## 3. Pemrosesan Data Sebelum Ditampilkan

### Phase 1 â€” Pengumpulan Data
- Fetch semua STJ/Bill/Payment/Bank move lines untuk periode yang dipilih
- Bangun mapping: picking â†’ PO â†’ bills â†’ payments â†’ bank

### Phase 2 â€” Bill Totaling (~baris 1761â€“1775)
```
per_bill_total = SUM(credit) dari lines dengan display_type = "payment_term"
fallback: SUM semua credit non-zero jika payment_term lines kosong
```
Digunakan untuk **proportional payment allocation** (lihat Phase 4).

### Phase 3 â€” Cycle Merging (~baris 1785â€“1815)
- Union-find mengelompokkan pickings yang berbagi bill apapun
- Output: list of picking groups; tiap group â†’ satu cycle

### Phase 4 â€” Agregasi Saldo Akun (~baris 1933â€“1947)
Untuk setiap akun dalam cycle:
```
debit_total  = SUM(debit)  dari semua relevant moves
credit_total = SUM(credit) dari semua relevant moves
net_balance  = debit_total - credit_total
```
**Proportional weighting** untuk shared moves (payment + bank JE):
```
proportion = cycle_bill_total / semua_bill_yang_dicakup_payment_ini
debit_allocated  = debit  Ã— proportion
credit_allocated = credit Ã— proportion
```

### Phase 5 â€” Per-Item Breakdown (~baris 1990â€“2050)
Untuk setiap produk dalam picking:
- **Direct lines** (punya `product_id`): assign penuh ke produk tersebut
- **Shared lines** (tidak ada product_id, atau payment/bank): distribusi by **inventory weight**
  ```
  weight_item = SVL_value_item / SVL_total_all_items_in_picking
  fallback    = 1 / jumlah_item (equal split jika SVL tidak ada)
  ```

### Phase 6 â€” Status Klasifikasi Akun (~baris 1948â€“1989)
| Status        | Kondisi                                                              |
|---------------|----------------------------------------------------------------------|
| `balanced`    | `\|net_balance\| < 1.0`                                             |
| `problem`     | Kode akun di `pcb_problem_codes` DAN masih ada unmatched balance    |
| `acceptable`  | Semua akun lain (inventori, HPP, bank, dll.)                        |
| `info`        | Kode akun di `pcb_info_codes` â€” info only, tidak trigger "problem"  |

**Default config**:
- `pcb_problem_codes` = `"2103006,1108099"` â†’ Suspensed Payable + Clearing
- `pcb_info_codes` = `"11120003"` â†’ COGS Variance

### Phase 7 â€” Pattern Detection (~baris 2154â€“2206)
| Pola   | Kondisi                                                        | Makna                              |
|--------|----------------------------------------------------------------|------------------------------------|
| Pola 1 | Clearing (1108099) + Suspensed (2103006) keduanya unmatched   | Kemungkinan akun kredit STJ salah  |
| Pola 2 | STJ ada tapi tidak ada bill                                    | Bill belum masuk / tidak link PO   |
| Pola 3A | Bill posted, belum lunas (`not_paid`/`partial`)               | Bill belum dibayar â€” warning, cycle = partial |
| Pola 3B | Bill sudah `paid` / `in_payment`, tetapi belum ter-match ke Payment terkait | Bill belum matching â€” warning, cycle = partial |
| Pola 4 | Payment ada tapi belum ada bank JE                             | Payment belum direkonsiliasi ke bank â€” warning, cycle = partial |
| Pola 5 | COGS Variance (5101010) punya saldo (info only)               | Selisih harga GR vs Bill (normal)  |

### Phase 8 â€” Status Cycle Assignment (~baris 2098â€“2109)
| Status      | Kondisi                                                              |
|-------------|----------------------------------------------------------------------|
| `problem`   | Ada akun dengan status = "problem"                                   |
| `partial`   | Tidak ada problem, TAPI ada warning patterns / bill belum paid / bill belum matching / payment belum reconcile / info accounts |
| `healthy`   | Semua bills terbayar, linkage payment/bank sudah match, tidak ada patterns, tidak ada unmatched problem accounts |

### Metadata Tambahan â€” Klasifikasi Dokumen Cycle

Klasifikasi ini **tidak menggantikan** `cycle_status`. Ia menjawab pertanyaan: cycle ini secara dokumen lebih dekat ke purchase yang kuat, purchase yang masih parsial, atau flow non-purchase/intercompany.

| Field                           | Makna |
|---------------------------------|-------|
| `inventory_types`               | Inventory type yang terdeteksi dari `stock.move.line.inventory_type` untuk picking dalam cycle |
| `document_classification`       | Key mesin: `purchase-backed`, `purchase-likely`, `non-purchase/intercompany` |
| `document_classification_label` | Label UI/diagnostik |
| `document_classification_reasons` | Alasan singkat mengapa cycle masuk klasifikasi tersebut |

Aturan aktif:
- `purchase-backed`
  - Prioritas pertama: `inventory_type` mengandung `purchase` atau `purchase_return`.
  - Jika `inventory_type` tidak tersedia, ada bukti keras purchase: minimal salah satu dari `PO`, `purchase_line_id` di stock move incoming, bill match via `purchase_line`, atau bill `invoice_origin` yang match ke PO cycle.
- `purchase-likely`
  - Tidak ada `inventory_type` purchase dan tidak ada bukti keras purchase, tetapi ada bill/payment/bank chain yang mendukung dan cycle masih terlihat vendor-facing.
- `non-purchase/intercompany`
  - Prioritas pertama: `inventory_type` berada di bucket non-purchase seperti `mutation` atau `internal_transfer`.
  - Jika `inventory_type` tidak tersedia, tidak ada bukti purchase yang cukup, atau nama/origin picking memberi sinyal kuat internal/intercompany seperti `WCGT/INT/...` atau teks mutasi antar company.

Prinsip penting:
- `cycle_status` tetap murni akuntansi (`problem` / `partial` / `healthy`).
- `document_classification` adalah layer tambahan untuk diagnosis jalur dokumen.
- Incoming tanpa PO **tidak** otomatis dibuang di seed; ia baru dibaca sebagai `likely` atau `non-purchase/intercompany` setelah graph dokumen selesai dirakit.

### Gate Status Berdasarkan Klasifikasi Dokumen

Mulai baseline ini, bucket `Cycle Bermasalah` dan `Cycle Sebagian` hanya dipakai untuk cycle yang lolos sebagai `purchase-backed`.

Efeknya:
- `purchase-backed`
  - Tetap boleh menjadi `problem`, `partial`, atau `healthy` sesuai saldo akun dan pattern.
- `purchase-likely`
  - Bersifat audit-only; cycle dipaksa keluar dari bucket `problem/partial`.
- `non-purchase/intercompany`
  - Bersifat audit-only; cycle dipaksa keluar dari bucket `problem/partial`.
- Untuk cycle audit-only, status akun `problem` di level sidebar direlaksasi menjadi `info` agar tidak tampil sebagai candidate repair purchase murni.

### Diagnostik GUI Aktual

Selain replay headless `python main.py svl-dashboard-analyze`, sekarang tersedia jalur inspeksi GUI yang membaca state widget Tkinter yang benar-benar aktif.

Command:

```text
python main.py svl-dashboard-gui-inspect --company-id 755 --dataset-mode purchase_cycle_balance --dump-json diagnostics\pcb_gui_dump.json
```

Untuk inspeksi `PCB Repair Collection`, inspector sekarang bisa sekalian collect row visible lalu membuka dialog sebelum dump:

```text
python main.py svl-dashboard-gui-inspect --company-id 755 --dataset-mode purchase_cycle_balance --select-picking PIKKP/IN/01509 --collect-pcb-visible --open-pcb-repair-dialog --dump-json diagnostics\pcb_repair_gui_dump.json
```

Yang didump:
- `sidebar` Treeview aktual, termasuk hirarki row yang sedang tampil, selection, focus, text, values, dan meta cycle/item PCB.
- Tabel PCB yang sedang dirender: `pcb_raw_tree`, `pcb_detail_tree`, dan `pcb_acct_summary_tree`.
- Header GUI penting seperti status text, phase text, summary sidebar, partner header, item header, dan effective DB text.
- Payload detail cycle terpilih bila mode PCB dan ada selection.
- State `pcb_repair_collection`, termasuk row raw yang sedang aktif, breakdown per case, scope, dan `live_dialog` bila dialog `PCB Repair Collection` sedang dibuka.
- Jika dialog PCB dibuka via CLI, dump juga menyertakan tree row dialog, selected row key, field edit, summary, advanced trace, serta tree `Current Cycle`, `Simulated JE`, dan `Projected Cycle`.

Catatan:
- Jalur ini launch GUI, memaksa page `Dashboard Control`, menjalankan analyze, lalu melakukan selection programatik sebelum dump.
- `--collect-pcb-visible` memakai filter/sidebar yang sedang aktif untuk mengisi collection sebelum snapshot.
- `messagebox` dibungkam ke logger agar automation tidak berhenti pada dialog modal.
- `--select-picking` dapat dipakai untuk memilih cycle tertentu; default selection target adalah node `cycle` pertama yang berhasil dirender.

#### GRNI Suspense Downgrade (2026-04-12)

Ditambahkan bersamaan dengan GRNI guard di item classifier.  Menangani false-positive Case 2 yang muncul ketika satu item di-GR lebih dari satu picking tetapi baru sebagian di-bill.

**Masalah sebelumnya:**
- Cycle dengan item yang diterima via dua picking (mis. PIKKP/IN/02912 + PIKKP/IN/02942) tetapi hanya picking pertama yang sudah di-bill akan menunjukkan residual 2103006 di level cycle.
- Karena cycle punya `has_bills = True` dan residual 2103006 non-zero, logic lama langsung menetapkan `cycle_status = "problem"` dan item classifier mendeteksi pola "STJ suspend + Bill suspend + residual" sebagai **Case 2**.
- Padahal residual itu murni berasal dari qty yang belum di-bill (GRNI state), bukan price mismatch Anglo-Saxon.

**Tiga lapis fix (final, rev 2):**

**Lapis 1 — Item classifier (`_classify_pcb_item_case`)**

Guard ditambahkan sebelum branch Case 2. Sebelum menetapkan `primary_case = "case2"`, fungsi menghitung `stj_qty_2103006` dari raw STJ lines yang punya kredit ke 2103006 untuk item ini, lalu memanggil:

```python
stj_qty_2103006 = sum(qty from raw_stj_lines where akun_code == "2103006" and kredit > 0)
if not cls._is_grni_residual(..., stj_quantity=stj_qty_2103006):
    primary_case = "case2"
```

`_is_grni_residual` returns True (→ skip Case 2) jika:
- `stj_cr / stj_quantity ≈ bill_dr / bill_quantity` (unit price STJ = unit price Bill, toleransi 1 IDR/unit)
- `stj_quantity` = jumlah qty dari STJ lines 2103006; jika tidak tersedia, fallback ke `gr_quantity`
- Alasan pakai `stj_quantity` bukan `gr_quantity`: `gr_quantity` adalah aggregate semua stock moves di merged cycle, termasuk moves yang belum punya STJ — ini mengecilkan implied STJ unit price dan menghasilkan false mismatch.

Case 2 tetap aktif (returns False) jika:
- Unit price berbeda → Anglo-Saxon gagal terbentuk → genuine Case 2
- `bill_quantity = 0` → tidak dapat dihitung
- Tidak ada account_row 2103006 di item, atau STJ/Bill side = 0

**Lapis 2 — Cycle status downgrade (`_apply_pcb_item_classifier`)**

Setelah semua item terklasifikasi, cek:
```
cycle_status == "problem"
AND case_counts kosong (tidak ada item dengan primary_case)
AND edge_flags ⊆ {edge_partial_bill}   ← diperluas dari "kosong" ke "hanya partial_bill"
AND _is_pcb_grni_suspense_downgrade(cycle) == True
```

`_is_pcb_grni_suspense_downgrade` (post-classifier version):
- Problem accounts di level cycle hanya `{"2103006"}` (tidak ada 1108099 problem)
- Tidak ada item yang memiliki `primary_case` non-kosong (artinya lapis 1 sudah menangani semua residual sebagai GRNI)
- Minimal satu item punya residual 2103006 non-zero (ada sesuatu untuk di-downgrade)

Jika semua kondisi terpenuhi:
- `cycle.cycle_status = "partial"` (turun dari "problem")
- `cycle.primary_case = ""`
- `cycle.edge_flags = []` (edge_partial_bill di-clear karena sudah ter-representasikan sebagai partial)
- Account row 2103006 di-cycle diubah dari `"problem"` ke `"acceptable"`

**Lapis 3 — Skip cycle-level re-classifier**

Di rebuild loop (setelah `_apply_pcb_item_classifier`), jika `cycle_status_final == "partial"` dan `cycle.primary_case` kosong, `_classify_pcb_cycle_case` di-skip. Tanpa ini, fungsi tersebut akan fall-through ke `"case_lainnya"` (karena semua problem flags sudah di-clear dan tidak ada named case).

**Invariant yang dijaga:**
- Case 2 genuine (price mismatch nyata, STJ unit price ≠ Bill unit price) tetap terdeteksi
- Cycle dengan 1108099 problem bersamaan tidak di-downgrade
- `edge_return_no_credit_memo` atau edge lain yang menunjukkan masalah nyata tetap memblokir downgrade

#### Clearing-via-Reclass override (Case 5 / Case 6 post-repair)
Sebelum penetapan status final, analyzer menjalankan satu pemeriksaan tambahan:

**Kondisi:**
- `problem_count > 0` dan `has_bills = True`
- Semua akun berstatus "problem" adalah **hanya 1108099** (tidak ada 2103006 problem)
- Hanya item yang **masih menyumbang saldo problem non-zero** yang dicek
- Semua item problem yang tersisa itu harus `svl_zero_at_gr = True`
- Item sibling lain dalam cycle boleh punya histori STJ valid, selama saldo problem itemnya sudah nol
- Jika ada item problem tersisa yang masih menunjukkan residual clearing dari STJ valid / non-zero SVL, override ini **tidak** dipakai

**Efek jika kondisi terpenuhi:**
- Status 1108099 di `account_rows` diubah dari `"problem"` ke `"acceptable"`
- `problem_count` di-reset ke 0
- `cycle_status` ditetapkan `"partial"` (bukan `"problem"`)
- `partial_group_key = "clearing_reclass"`, `partial_group_label = "Clearing via Jurnal Reclass"`

**Rasional:** Ini terjadi setelah Case 5 repair JE (CR 2103006 -> DR 1108099) di-post. Akun 2103006 sudah bersih; saldo 1108099 yang tersisa adalah transit wajar yang akan ditutup via jurnal reclass (RAC) bulk. Cycle tidak perlu lagi tampil sebagai "bermasalah".

**Sidebar:** Subgroup baru `"Clearing via Jurnal Reclass"` muncul di bagian **Cycle Sebagian**, di urutan paling atas sebelum "Bill Belum Paid".

**Post-classifier re-promote (2026-04-16):**
Override `clearing_reclass` berjalan saat *cycle assembly* — sebelum item classifier mengisi `has_stj_evidence` dan `bill_hit_role`. Akibatnya, item **Case 3 genuine** (bill masuk expense + STJ koreksi yang membuat clearing menggantung, yaitu double recognition biaya + persediaan) bisa salah di-downgrade ke `partial`.

Fix: setelah `_apply_pcb_item_classifier` selesai, jika `cycle_status == "partial"` dan `partial_group_key == "clearing_reclass"`, analyzer memeriksa ulang apakah ada item yang memenuhi semua kriteria berikut:
- `primary_case == "case3"`
- `has_stj_evidence == True`
- `bill_hit_role == "expense"`
- saldo 1108099 item-level masih non-zero

Jika ditemukan, cycle di-promote kembali ke `"problem"`, `partial_group_key` di-clear, dan status akun 1108099 di level cycle di-restore ke `"problem"`.

**Invariant yang dijaga:**
- Cycle post-Case-5-repair yang sah (tidak ada item Case 3 dengan STJ+expense clearing) tetap di-downgrade ke `partial / clearing_reclass`
- Hanya Case 3 genuine (double recognition persediaan + biaya dari satu transaksi) yang kembali ke `problem`

### Phase 9 â€” Adjustment Audit Warning (RAC / Clearing Reclass)
- Setelah cycle utama selesai dibentuk, analyzer menjalankan pass tambahan untuk mencari `account.move` posted `entry` dalam company + date range aktif yang **bukan** bagian dari move cycle utama (`relevant_move_ids`).
- Kandidat adjustment hanya dianggap valid bila memiliki:
  - minimal satu AML akun clearing `1108099`
  - minimal satu AML lawan pada akun HPP/expense item (`account_type = expense_direct_cost` atau kode `5101010`)
- Move yang sudah masuk klasifikasi utama STJ / BILL / PBK / BK / PCB correction **tidak** ikut sebagai adjustment audit.
- Matching ke cycle / item memakai prioritas berikut:
  1. exact `bill_move_id`
  2. exact `stock_move_id`
  3. exact `purchase_line_id`
  4. exact `product_id`
  5. exact `picking_id`
- Jika relation field tidak cukup, ada fallback guarded dari token `picking_name` / `bill_ref` yang muncul di `move.name` atau `move.ref`.
- Prefix `RAC/` hanya menjadi **ranking hint**, bukan syarat wajib. Move non-`RAC` tetap bisa match bila relation field exact tersedia.
- Jika kandidat match ke lebih dari satu cycle dengan skor terbaik yang sama, kandidat ditandai **ambiguous** dan hanya ditampilkan sebagai warning cycle-level. Ia **tidak** di-attach ke item tertentu.
- Output audit disimpan terpisah dalam `adjustment_warning_text` dan `adjustment_audit_rows`.
- Audit ini **tidak** mempengaruhi:
  - `raw_lines`
  - `account_rows`
  - `item_rows`
  - `cycle_status`
  - `case1_link_rows`
  - source-of-truth saldo cycle / account aggregation
- Khusus **Case 3 / Case 4**, audit clearing sekarang dipisah menjadi dua lapis:
  - `adjustment_audit_rows` / `adjustment_warning_text` tetap untuk warning cycle-level
  - `external clearing` item-level dipakai sebagai **repair-only evidence** walau akun `1108099` tidak muncul di `Detail Saldo Akun per Cycle`
- Verifikasi lineage adjustment mengikuti urutan:
  1. relation field pada line adjustment itu sendiri (`product_id`, `purchase_line_id`, `stock_move_id`, `bill_move_id`, `picking_id`)
  2. explicit source JE reference dari `move.ref` / `move.name` / label line adjustment
  3. fallback `unique amount + source-account match` lintas item/cycle yang punya bill item-level
- Jika fallback menghasilkan lebih dari satu kandidat kuat, row tetap warning cycle-level dan **tidak** dipakai untuk repair.
- External clearing ini tetap **tidak** menambah saldo ke cycle. Efeknya hanya pada builder `case2_repair_rows` / `planned_lines`:
  - clearing `1108099` dipakai lebih dulu sebagai line `Audit Clearing`
  - bila nominal clearing kurang dari saldo problem, sisanya tetap masuk ke akun HPP target item sebagai `Selisih HPP`
  - bila nominal clearing menutup penuh saldo problem, line residual HPP tidak dibuat
- Dengan kata lain, adjustment audit tetap bukan source-of-truth saldo cycle, tetapi sekarang bisa menjadi **repair-only override** untuk Case 3 / Case 4 yang match secara unambiguous dan terverifikasi walau tidak attach ke detail saldo cycle.

---

### Phase 10 - Runtime Classifier: Case 8 Return Value Mismatch

> **Status**: aktif di runtime per 2026-04-08. Semua row Case 8 tetap `review_required=True` pada tahap awal.

Case 8 ditujukan untuk cycle return-to-vendor ketika barang sudah diterima sebagai persediaan, lalu direturn, tetapi nilai SVL return tidak sebanding dengan nilai SVL receipt yang menjadi asal return. Pola ini bisa tampak "sehat" pada classifier aktif bila akun problem `2103006` / `1108099` sudah balance, padahal secara bisnis masih ada perpindahan nilai tidak wajar antara Persediaan dan HPP.

#### Sumber data dan relasi yang wajib dipakai

Analyzer tidak boleh hanya melihat saldo akun agregat. Case 8 perlu trace item-level dari model dan field berikut:

| Model | Field penting | Kegunaan |
|-------|---------------|----------|
| `stock.picking` | `id`, `name`, `picking_type_code`, `state`, `date_done`, `move_ids` | Menentukan receipt picking dan return picking |
| `stock.move` | `id`, `picking_id`, `product_id`, `product_uom`, `quantity` / `product_qty`, `purchase_line_id`, `origin_returned_move_id`, `returned_move_ids` | Menghubungkan move return ke move receipt dan PO line |
| `stock.valuation.layer` | `id`, `reference`, `description`, `product_id`, `stock_move_id`, `quantity`, `value`, `unit_cost`, `uom_id`, `account_move_id` | Membandingkan nilai receipt SVL vs return SVL |
| `account.move` | `id`, `name`, `ref`, `move_type`, `journal_id`, `stock_move_id`, `line_ids`, `state`, `date` | Menemukan STJ valuation dan adjustment yang direct-linked ke `stock.move` |
| `account.move.line` | `id`, `move_id`, `account_id`, `product_id`, `product_uom_id`, `quantity`, `debit`, `credit`, `balance`, `matching_number`, `purchase_line_id`, `purchase_order_id` | Melihat reclass HPP, suspense, dan matching antar STJ |
| `purchase.order.line` | `id`, `order_id`, `product_id`, `product_qty`, `product_uom_id`, `product_uom_qty`, `qty_received`, `qty_invoiced`, `invoice_lines`, `move_ids` | Menentukan upstream PO line dan bill item link |
| `account.move` / `account.move.line` vendor bill | `move_type`, `invoice_origin`, `payment_state`, `amount_residual`, `invoice_line_ids`, `purchase_line_id`, `product_id`, `price_subtotal`, `quantity` | Menentukan bill value yang benar-benar terkait item |

Catatan relasi:
- STJ return/adjustment dapat direct-linked ke return `stock.move` lewat `account.move.stock_move_id`, lalu indirect-linked ke `stock.picking` via `stock.move.picking_id`.
- STJ return/adjustment tidak harus direct-linked ke PO atau bill. Jika `account.move.line.purchase_line_id` kosong, PO tetap dapat diturunkan secara tidak langsung dari `account.move.stock_move_id -> stock.move.purchase_line_id`.
- Vendor bill item harus tetap difilter ketat: hanya `account.move.line.move_type in ("in_invoice", "in_refund")` yang dianggap bill. `move_type = "entry"` adalah STJ/correction/adjustment, bukan bill line.
- `matching_number` pada akun suspense dapat dipakai sebagai evidence bahwa STJ receipt, STJ return, dan STJ adjustment berada dalam satu cluster rekonsiliasi, tetapi tidak boleh menggantikan relasi stock move / product / PO line untuk menentukan source item.

#### Core calculation yang sama untuk 8A dan 8B

Runtime memakai satu jalur evidence bersama (`case8_evidence`) yang dihitung dari trace receipt/return, lalu hasilnya diklasifikasikan menjadi `case8a` atau `case8b`.

Input item-level minimal:

```python
receipt_qty = sum(qty positif SVL receipt untuk source receipt move)
receipt_value = sum(value positif SVL receipt untuk source receipt move)
return_qty = abs(sum(qty negatif SVL return untuk return move yang origin_returned_move_id/returned_move_ids mengarah ke receipt move))
actual_return_value = abs(sum(value negatif SVL return untuk return move tersebut))
kept_qty = receipt_qty - return_qty
```

Basis expected:

```python
receipt_unit_cost = receipt_value / receipt_qty
expected_return_value = return_qty * receipt_unit_cost
expected_kept_value = kept_qty * receipt_unit_cost
return_value_gap = actual_return_value - expected_return_value
```

Jika satu receipt item punya beberapa receipt SVL layer, FIFO/AVCO source layer perlu dihitung sesuai linkage return SVL bila tersedia. Fallback awal boleh memakai weighted average `receipt_value / receipt_qty`, tetapi harus diberi guard `weighted_average_fallback` agar user tahu basisnya bukan layer-exact.

Bill value untuk item:

```python
bill_value_item = sum(price_subtotal signed dari vendor bill line item)
bill_qty_item = sum(quantity signed dari vendor bill line item)
```

Rule bill line:
- hanya `move_type in ("in_invoice", "in_refund")`
- match prioritas: exact `purchase_line_id`, lalu exact `product_id` dalam bill cycle yang sama
- exclude `move_type = "entry"` walaupun punya product atau purchase line
- bill yang sudah paid pada parent move tidak otomatis berarti item ini dibayar; item line bernilai `0` harus tetap dibaca sebagai `bill_value_item = 0`

#### Case 8A - Full Return Value Mismatch

Klasifikasi:

```python
full_return = abs(return_qty - receipt_qty) <= qty_tolerance
item_not_billed = abs(bill_value_item) <= amount_tolerance
value_mismatch = abs(return_value_gap) > amount_tolerance

case8a = full_return and item_not_billed and value_mismatch
```

Makna bisnis:
- Seluruh qty yang diterima sudah direturn.
- Item tidak ditagihkan/dibayar secara nominal.
- Setelah cycle selesai, item tidak seharusnya menyisakan saldo Persediaan atau HPP.
- Jika return SVL mengambil value lebih besar/kecil dari receipt SVL, selisihnya dapat muncul sebagai saldo silang Persediaan dan HPP walau akun suspense sudah balance.

Expected final item-level untuk full return:

```python
inventory_net ~= 0
cogs_net ~= 0
suspense_net ~= 0
```

Repair suggestion:

```python
if return_value_gap > 0:
    # Return mengambil value terlalu besar dari Persediaan.
    Dr Inventory Account sebesar return_value_gap
    Cr COGS / Expense Account sebesar return_value_gap
elif return_value_gap < 0:
    # Return mengambil value terlalu kecil dari Persediaan.
    Dr COGS / Expense Account sebesar abs(return_value_gap)
    Cr Inventory Account sebesar abs(return_value_gap)
```

Guard:
- `Inventory Account` harus resolve exact dari `product.template.categ_id -> product.category.property_stock_valuation_account_id`.
- `COGS / Expense Account` harus resolve dari expense/HPP kategori item atau akun HPP item yang benar-benar muncul di saldo cycle.
- Jika sudah ada reclass HPP dengan nominal yang sama dengan `abs(return_value_gap)`, suggestion harus diberi label sebagai reverse/rebalance reclass, bukan JE price-diff biasa.
- Repair ideal bersifat **valuation-aware**. JE GL-only boleh menjadi opsi manual review, tetapi harus diberi warning bahwa SVL/subledger masih menyimpan value return historis yang salah jika tidak ada adjustment valuation yang sesuai.

#### Case 8B - Partial Return Value Mismatch

Klasifikasi:

```python
partial_return = return_qty > qty_tolerance and return_qty < receipt_qty - qty_tolerance
value_mismatch = abs(return_value_gap) > amount_tolerance

case8b = partial_return and value_mismatch
```

Makna bisnis:
- Hanya sebagian qty yang direturn.
- Sebagian qty masih disimpan/dikonsumsi, sehingga tidak boleh memaksa Persediaan/HPP menjadi nol.
- Bill dapat bernilai sebagian; bill paid/partial paid harus dievaluasi hanya untuk porsi item yang benar-benar tidak direturn.

Validasi tambahan untuk partial:

```python
expected_kept_value = kept_qty * receipt_unit_cost
kept_bill_gap = expected_kept_value - bill_value_item
```

Interpretasi:
- `return_value_gap` adalah masalah return valuation.
- `kept_bill_gap` adalah masalah price/bill variance untuk qty yang tetap diterima.
- Keduanya tidak boleh dicampur menjadi satu nominal repair.
- Jika `bill_value_item` tidak sama dengan `expected_kept_value`, classifier boleh menambahkan secondary flag seperti `edge_partial_bill` atau meneruskan price variance ke case yang relevan, tetapi Case 8B tetap hanya mengusulkan correction sebesar `return_value_gap`.

Repair suggestion untuk 8B memakai arah yang sama dengan 8A, tetapi hanya sebesar gap return:

```python
if return_value_gap > 0:
    Dr Inventory Account sebesar return_value_gap
    Cr COGS / Expense Account sebesar return_value_gap
elif return_value_gap < 0:
    Dr COGS / Expense Account sebesar abs(return_value_gap)
    Cr Inventory Account sebesar abs(return_value_gap)
```

Guard 8B:
- Jangan zero seluruh akun inventory/HPP item, karena kept qty memang boleh menyisakan nilai.
- Jangan memakai total HPP item sebagai nominal repair jika total itu juga mengandung konsumsi/sale/adjustment lain di luar return mismatch.
- Jika ada reclass HPP, nominal yang boleh direverse hanya bagian yang cocok dengan `abs(return_value_gap)` dan terhubung ke return move / matching cluster / item yang sama.
- Jika evidence reclass HPP ambigu, row harus `Needs Review`, bukan auto-repair.

#### Planned row dan UI status

Case 8A/8B memakai builder row yang sama dengan Case 2-6 (`planned_lines`) agar UI repair collection tetap konsisten:

```python
planned_lines = [
    {"role": "return_value_gap_inventory", "account_code": inventory_account_code, "side": "debit|credit", "amount": abs(return_value_gap)},
    {"role": "return_value_gap_cogs", "account_code": cogs_account_code, "side": "credit|debit", "amount": abs(return_value_gap)},
]
```

Status awal yang disarankan:
- `Ready` hanya bila receipt SVL, return SVL, inventory account, COGS/HPP account, dan evidence reclass HPP item-level semuanya unambiguous.
- `Needs Review` bila ada bill sebagian, HPP reclass sebagian, weighted-average fallback, atau lebih dari satu kandidat return/receipt layer.
- `Incomplete` bila account target tidak resolve atau relation return ke receipt tidak bisa dibuktikan.

Default reference:

```text
{Reference Prefix}: Purchase Cycle Balance: {picking_name} / {return_picking_name} / {bill_name} / {item_code} - {item_name}
```

Default line label:

```text
{item_code} - {item_name} - Case 8A - Full Return Value Mismatch
{item_code} - {item_name} - Case 8B - Partial Return Value Mismatch
```

#### Batas implementasi awal

- Tahap awal dibuat **review-required**: tampilkan metrics, planned JE, dan guard; jangan execute tanpa review confirmation.
- Jika nantinya diizinkan repair valuation-aware, flow harus menjamin GL dan SVL/subledger tetap sinkron. Jika hanya JE manual yang dibuat, UI harus menyebut eksplisit bahwa ini memperbaiki GL presentation tetapi tidak mengubah histori SVL return.
- Case 8A/8B tidak boleh menurunkan/mengubah behavior Case 1-6 sampai ada regression test untuk return full, return partial, bill nol, bill sebagian, dan return value gap dua arah.

---

### Phase 10B - Runtime Classifier: Case 9 UoM Scale Mismatch

> **Status**: aktif di runtime per 2026-04-08. Semua row Case 9 wajib `review_required=True`.

Case 9 menangani item yang salah skala karena UoM PO/Bill tidak inline dengan product/SVL UoM. Trigger tidak dimulai dari saldo akun saja, tetapi dari pintu masuk transaksi:

- UoM source PO/Bill berbeda root dengan product/SVL UoM (`uom.uom.parent_path` atau fallback `category_id`).
- Ratio qty `stock.move.product_qty / source_qty` ekstrem, minimal `>= 10` atau `<= 0.1`.
- Nilai SVL aktual berbeda material dari expected value, minimal `max(1.000, 5% dari expected value)`.

Sumber evidence yang dipakai:

| Evidence | Sumber |
|----------|--------|
| Source UoM dan source qty | Vendor bill line exact `purchase_line_id`, fallback PO line |
| Product/SVL UoM dan system qty | `stock.move.product_uom`, `product_qty` / `quantity` |
| Actual SVL value | `stock.valuation.layer.value` per `stock_move_id`, fallback `stock_move.price_unit x qty` |
| Expected value | `source_qty x current standard_price`; fallback bill subtotal, lalu PO subtotal |
| Holder account | saldo item, external clearing evidence, dan status revaluation/HPP |

Formula utama:

```python
source_qty = bill_qty if exact bill line exists else po_qty
scale_factor = abs(stock_move_qty / source_qty)
expected_value = source_qty * product.standard_price
actual_svl_value = abs(sum(svl.value for stock_move))
value_gap = actual_svl_value - expected_value
```

Fallback bila `standard_price <= 0`:

```python
expected_value = bill_subtotal if exact bill line exists else po_subtotal
```

Decision matrix planned lines:

| Kondisi holder | Planned JE |
|----------------|------------|
| Case 2 sudah menutup suspense ke HPP dan downstream clearing `1108099` terbukti, atau suspense sudah nol tetapi correction STJ item yang sama sudah ada lalu menyisakan HPP minus vs inventory plus | Lima baris: net-off HPP, pasangan clearing sebesar HPP, net-off Persediaan, reversal pasangan clearing sebesar HPP, lalu selisih signed ke HPP kategori; clearing akhirnya nol |
| Suspense masih terbuka dan inventory sudah value-aligned karena product-level revaluation | Dr `2103006` / Cr HPP |
| Suspense masih terbuka dan inventory masih overstated | Dr `2103006` / Cr akun Persediaan kategori |
| Holder ambigu | Row dibuat review-only/incomplete tanpa planned line executable sampai user memilih target |

Catatan baseline MIE RAMEN:

- `PIKKP/IN/01509`, `PIKKP/IN/01680`, `PIKKP/IN/02066` mengikuti holder `case2_downstream_clearing`: Dr HPP / Cr `1108099` per stock picking, nominal memakai Case2 amount yang sudah match picking.
- `PIKKP/IN/02600` mengikuti holder `open_suspense_revalued` bila suspense masih terbuka dan product-level revaluation sudah membuat nilai persediaan berjalan align: Dr `2103006` / Cr HPP.
- Case 9 tidak otomatis menyentuh akun Persediaan bila current inventory value sudah cocok dengan `corrected_qty x standard_price`.

Guardrail:

- Cross-root UoM tidak pernah dianggap konversi normal. Row harus membawa evidence source UoM, product UoM, scale factor, source qty, corrected qty, expected value, actual SVL value, value gap, dan holder basis.
- Jika source UoM dan product UoM masih satu root/kategori, row tidak boleh dipromosikan menjadi Case 9 meskipun ratio qty berbeda.
- Bill evidence Case 9 hanya boleh memakai move vendor bill/refund (`in_invoice` / `in_refund`). Jurnal correction `entry` yang ikut menempel ke `purchase_line_id` atau `PO.invoice_ids` harus tetap dibaca sebagai downstream/correction evidence, bukan bill source.
- Untuk Case 9 dengan `suspend_balance = 0`, `hpp_balance < 0`, `inventory_balance > 0`, dan ada `correction_stj_refs/move_ids` pada item yang sama, holder dipromosikan ke `case2_downstream_clearing` walaupun `external_clearing_amount` masih 0; nominal tetap memakai saldo HPP item yang tersisa, bukan `total_gap`.
- Untuk holder `case2_downstream_clearing` pada Case 9, HPP dan Persediaan yang salah ditutup. Dua line clearing harus saling meniadakan sebesar saldo HPP; selisih akhir diarahkan ke akun HPP kategori, bukan ditinggalkan di clearing.
- Projected-review di Repair Collection wajib menandai residual `1108099` sebagai problem yang perlu ditinjau. Case 9 tidak lagi mendapat pengecualian residual clearing. Rencana lima baris yang benar menghasilkan projected clearing nol, tetapi review Case 9 tetap wajib.
- Formula signed: tutup saldo sumber, nolkan dua line clearing, lalu `price_gap = -sum(signed_planned_lines)` masuk HPP (debit jika positif, credit jika negatif). Saldo akhir HPP mempertahankan nilai ekonomis awal HPP + Persediaan. Debit/kredit harus balance juga pada arah saldo terbalik. Bila gap nol, line kelima dihilangkan. Bila akun HPP atau akun persediaan yang diperlukan belum tersedia, builder tidak menghasilkan rencana parsial executable.
- Case 9 tetap per-item global secara perhitungan, tetapi planned JE dipecah per `stock.picking` agar referensi audit tetap maksimal.
- Execute hanya boleh via Repair Collection setelah review confirmation; classifier sendiri tetap read-only dan tidak menulis ke Odoo.

---

## 4. Tampilan Dashboard

### Sidebar (Tree View)
- Dikelompokkan by status: âŒ **Bermasalah** â†’ âš ï¸ **Sebagian** â†’ âœ… **Sehat**
- **Cycle Bermasalah** di-sub-group lagi by `primary_case` hybrid:
  - **Case 1 - STJ Bill Miss Match (Clearing - Suspend)**
  - **Case 2 - STJ Bill Price Diff (Suspend - Suspend)**
  - **Case 3 - STJ Bill Hit Expenses (Clearing - Expenses)**
  - **Case 4 - STJ Bill Hit Expenses (Suspend - Expenses)**
  - **Case 5 - Pemulihan SVL (Inventory - Suspense)**
  - **Case 6 - Pemulihan SVL (Inventory - Bill Expense)**
  - **Case 8A - Full Return Value Mismatch**
  - **Case 8B - Partial Return Value Mismatch**
  - **Case 9 - UoM Scale Mismatch**
  - **Edge - Partial Bill**
  - **Edge - Return Without Credit Memo**
  - **Edge - STJ Corrupt**
  - **Case Lainnya**
  - Prioritas cycle: `case5 -> case6 -> case1 -> case2 -> case3 -> case4 -> case8a -> case8b -> case9 -> edge_* -> case_lainnya`
- Case 8A/8B/9 tidak bergantung pada residual `2103006` / `1108099`; bila evidence item valid, cycle tetap masuk **Cycle Bermasalah** agar tidak tersembunyi oleh default filter `Cycle Sehat = off`.
- **Cycle Sebagian** sekarang di-sub-group lagi by bill state:
  - **Bill Belum Paid**: ada bill dengan `payment_state = not_paid/partial`
  - **Bill Belum Matching**: bill sudah `paid/in_payment`, tetapi analyzer belum menemukan linkage `payment_move_ids`
  - **Payment Belum Reconcile**: `payment_move_ids` sudah ada, tetapi `bank_move_ids` belum ada
  - **Partial Lainnya**: partial karena info account / variance atau penyebab non-bill lain
- Detail cycle menampilkan breakdown `case_counts` agar mixed cycle tidak tersembunyi walau sidebar hanya memilih satu `primary_case`
- Item eligible `Case 3/4` harus punya **bill item-level**, lalu punya **direct STJ item-level**, atau **cycle-matched correction STJ**, atau **verified external clearing**
  - bila relasi `stock.move.account_move_ids` kosong/tidak terbaca, analyzer boleh fallback ke `Detail Journal Entry per Cycle Pembelian` / `raw_lines` untuk mencari baris `jenis=STJ` yang match ke item (`kode_item` / `nama_item`), lalu resolve `stj_refs` dan `stj_move_ids` dari sana
- Item `Case 5/6` harus lolos guard:
  - `svl_zero_at_gr=True`
  - tidak ada STJ valid
  - pola Bill item-level cocok ke suspend atau expense
  - jika basis `standard_cost x qty` masih nol, row ditandai review-only / incomplete
- Per cycle: nama picking + jumlah problem accounts
- Opsi grouping by valuation method (ðŸ”„ Automated / ðŸ“‹ Manual)
- Summary counter: `N cycle | âŒM âš ï¸P âœ…H  (tampil X)`
- Search sidebar juga memeriksa `adjustment_audit_rows.move_name` dan `adjustment_audit_rows.move_ref`, sehingga cycle bisa dicari langsung dari nomor `RAC` / ref adjustment

### Panel Detail Cycle
Saat cycle dipilih:
- **Header**: GR date | Status icon | STJ refs | Picking name | PO label | Partner | [Bill/Payment/Bank refs]
- **Total row**: Total debit | Total credit | Net balance | Count problem accounts
- **Multi-picking indicator** (jika merged): daftar semua picking yang share bill
- Jika ada adjustment audit, panel detail menampilkan:
  - satu baris warning ringkas, mis. `Warning: Clearing adjustment -> RAC/... (+N)`
  - baris audit trail per move adjustment, termasuk source account, item, ref, basis match, dan origin JE bila terdeteksi
- **Account rows** (1 baris per akun): kode | nama | debit | credit | balance | status icon

### Panel Detail Item
Saat item dipilih dalam cycle:
- Header: nama item | valuation method | picking | PO | tanggal | partner
- Item totals: debit | credit | net balance | count problem accounts
- Item detail sekarang juga menampilkan classifier kanonik:
  - `primary_case`, `primary_group`, `secondary_flags`, `case_reason`
  - `stj_state`, `svl_zero_at_gr`, `bill_hit_role`
  - `repair_basis_amount`, `repair_basis_source`
- Evidence `Case 3/4` tetap ditampilkan sebagai: bill item refs, direct STJ refs, cycle correction STJ refs, atau `No direct STJ`, plus `External Clearing`, basis trace, dan status eligibility
- Evidence `Case 8/9` tampil di Repair Collection sebagai `Case Evidence`, guard message, planned lines, dan review reason.
- Jika evidence STJ berasal dari correction cycle-level, UI menampilkan format `STJ/... (cycle correction)` agar beda dari direct STJ
- `External Clearing` tetap bisa muncul di item detail walau akun `1108099` tidak ada di saldo cycle/item
- Item detail hanya menampilkan `adjustment_audit_rows` yang **unambiguous** dan benar-benar attach ke item tersebut
- Per-akun breakdown filtered ke item tersebut

---

## 5. Data Model Kunci (`models.py`)

### `SvlDashboardPurchaseCycle`
```python
picking_id, picking_name          # GR reference utama
picking_ids, picking_names        # Semua GR jika merged
gr_date, partner_id, partner_name
purchase_orders, stj_refs
bill_move_ids, bill_refs
payment_move_ids, payment_refs
bank_move_ids, bank_refs
product_ids                       # Items dalam GR
cycle_status: "problem" | "partial" | "healthy"
issue_patterns: list[str]         # Masalah terdeteksi
adjustment_warning_text: str      # Warning ringkas audit-only adjustment clearing
adjustment_audit_rows: list[SvlDashboardPcbAdjustmentAuditRow]
account_rows: list[SvlDashboardCycleAccountRow]
item_rows: list[SvlDashboardCycleItemRow]
primary_case: str                  # Case utama untuk grouping sidebar cycle
case_counts: dict[str, int]        # Breakdown jumlah item per case
edge_flags: list[str]              # Edge flags cycle-level
mixed_case_summary: str            # Ringkasan mixed cases
has_return_picking: bool
has_refund_bill: bool
case1_link_rows: list[SvlDashboardPcbCase1LinkRow]  # Strong-linked repair rows khusus Case 1
case2_repair_rows: list[SvlDashboardPcbCase2RepairRow]  # Planned repair rows per item untuk flow multi-line Case 2/3/4/5/6/8A/8B/9
total_debit, total_credit, problem_account_count
```

### `SvlDashboardCycleAccountRow`
```python
account_id, code, name, account_type, account_group
debit, credit, net_balance
status: "balanced" | "acceptable" | "problem" | "info"
```

### `SvlDashboardCycleItemRow`
```python
product_id, product_name, default_code
valuation_method: "automated" | "manual"
gr_quantity, bill_quantity, standard_price
account_rows: list[SvlDashboardCycleAccountRow]
bill_move_ids, bill_refs, purchase_line_ids
has_item_bill
stock_move_ids, stj_move_ids, stj_refs
has_item_stj
correction_stj_move_ids, correction_stj_refs
has_stj_evidence
external_clearing_amount
external_clearing_refs
external_clearing_basis
external_clearing_verified
verified_audit_clearing_amount
eligible_case34
adjustment_audit_rows: list[SvlDashboardPcbAdjustmentAuditRow]
case8_evidence: dict[str, Any]
case9_evidence: dict[str, Any]
primary_case, primary_group
secondary_flags: list[str]
case_reason: str
auto_repairable: bool
stj_state: str
svl_zero_at_gr: bool
bill_hit_role: str
repair_basis_amount: float
repair_basis_source: str
```

### `SvlDashboardPcbAdjustmentAuditRow`
```python
move_id, move_name, move_date, move_ref
product_id, item_code, item_name
source_account_code, source_account_name
clearing_account_code
repair_clearing_amount                # nominal clearing audit yang eligible untuk repair-only Case 3/4
origin_move_id, origin_move_name
origin_basis
origin_product_id, origin_purchase_line_id, origin_stock_move_id
verified_for_case34
matched_basis                       # exact.purchase_line_id / exact.stock_move_id / fallback.*
ambiguous: bool
```

### `SvlDashboardPcbCase1LinkRow`
```python
cycle_key, source_key
source_kind, source_id
product_id, item_code, item_name, item_category_name
bill_line_id, bill_move_id, purchase_line_id
stock_move_id, stock_move_ids
picking_id, picking_name
po_name, bill_name
partner_id, partner_name
payment_move_ids, bank_move_ids
suspend_target_aml_ids, clearing_target_aml_ids
product_uom_id, quantity
currency_id, amount_currency
analytic_distribution
journal_code, debit_account_code, credit_account_code
debit_amount, credit_amount
diff_account_code, diff_account_name
diff_side, diff_amount
stj_amount, bill_amount
bill_price_unit, gr_price_unit, price_gap_value, allocated_amount
bill_date, gr_date
```

### `SvlDashboardPcbRepairPlannedLine`
```python
role, account_code, account_name
amount, side
line_label, source_balance
```

### `SvlDashboardPcbCase2RepairRow`
```python
row_key, cycle_key
product_id, item_code, item_name, item_category_name
bill_line_id, purchase_line_id
stock_move_id, stock_move_ids
stj_move_ids, stj_refs
picking_id, picking_name, po_name
bill_move_id, bill_name
partner_id, partner_name
payment_move_ids, bank_move_ids
suspend_target_aml_ids, clearing_target_aml_ids
product_uom_id, quantity
currency_id, amount_currency, amount_currency_basis
analytic_distribution
bill_price_unit, gr_price_unit, price_gap_value, allocated_amount
suspend_account_code, inventory_account_code, expense_account_code
problem_balances_by_code
suspend_balance, hpp_balance, inventory_balance
cogs_variance_balance, selisih_hpp_amount
coefficient_variance
repair_basis_amount, repair_basis_source
bank_balances_by_code, bank_account_codes
guard_flags, guard_messages
review_required, review_confirmed, review_reason
planned_lines: list[SvlDashboardPcbRepairPlannedLine]
case_evidence: dict[str, Any]
```

---

## 7. Konfigurasi

Tersimpan di `config.py` (lines ~55â€“56), dapat diubah via UI dialog:

| Key                  | Default              | Keterangan                            |
|----------------------|----------------------|---------------------------------------|
| `pcb_problem_codes`  | `"2103006,1108099"`  | Akun yang trigger status "problem"    |
| `pcb_info_codes`     | `"11120003"`         | Akun info-only (tidak trigger problem)|

---

## 6A. PCB Repair Collection (Case 1 + Case 2/3/4/5/6/8A/8B/9 + Manual Review)

### Bentuk umum collection
- Dialog collection sekarang satu pintu untuk Case 1, Case 2, Case 3, Case 4, Case 5, Case 6, Case 8A, Case 8B, Case 9, dan fallback manual review untuk cycle bermasalah yang belum punya auto-row usable
- Label UI utama, context menu klik kanan, tombol header module, dan bulk action memakai nama **PCB Repair Collection**
- Tree kiri di dialog collection digroup **1 level**: parent `Case 1 - ...` sampai `Case 9 - ...`, plus `Case Lainnya - ...`, lalu semua row langsung menjadi leaf di bawah group itu
- Kolom grid utama sekarang fokus ke audit JE: `Picking`, `Item`, `Source`, `PO`, `Bill`, `Amount`, `JE Preview`, `Guard / Reconcile`, `Status`
- Case 1 tetap memakai pasangan debit/kredit utama, tetapi preview/simulasi boleh menampilkan **line selisih tambahan** ke akun expense item bila `debit_amount` dan `credit_amount` item tidak sama
- Case 2/3/4/5/6/8A/8B/9 dan row manual review memakai `planned_lines`, sehingga preview JE bisa berisi 0 sampai 5+ line
- Case 8A/8B/9 selalu masuk sebagai `Needs Review` sampai user confirm review; jika holder/account evidence ambigu, row bisa `Incomplete` dengan planned line kosong.
- Untuk Case 3/4 sekarang ada dua tier row:
  - `Ready` bila item punya evidence `direct STJ`, `cycle correction STJ`, atau `verified external clearing`
  - `Needs Review` bila item hanya punya bill + saldo problem + target HPP resolvable, tetapi belum ada evidence STJ/clearing yang terverifikasi
- Bila auto-builder tidak bisa membentuk row item actionable tetapi cycle masih punya line `BILL` dengan `expense_direct_cost` non-variance, dashboard sekarang membuat **row manual review**:
  - memakai `planned_lines=[]` sehingga awalnya `Incomplete`
  - membawa suggestion akun expense dari line BILL itu sendiri
  - user melengkapi debit/kredit sendiri dari editor `Jurnal Simulasi`
  - fallback ini bisa muncul di `Case 2`, `Case 3`, `Case 4`, maupun `Case Lainnya`, mengikuti klasifikasi cycle asal
- Bulk action PCB sekarang memakai submenu **`Collect PCB Repair (Visible)`** dengan opsi:
  - `Semua Case Bermasalah`
  - `Case 1 saja`
  - `Case 2 saja`
  - `Case 3 saja`
  - `Case 4 saja`
  - `Case 5 saja`
  - `Case 6 saja`
  - `Case 8A saja`
  - `Case 8B saja`
  - `Case 9 saja`
  - `Case Lainnya saja`
- `Case Lainnya saja` mengikuti klasifikasi sidebar `case_lainnya`, dan sekarang juga ikut meng-collect cycle yang actionable lewat fallback manual review meski bukan dari `case2_repair_rows`

### Case 1

### Granularity dan key
- Satu cycle Case 1 diturunkan menjadi beberapa row **`Item+Source`**
- Key row collection mengikuti pola:

```python
case1::{cycle_id}::{product_id}::{source_kind}::{source_id}
```

### `case1_link_rows` pada snapshot
`SvlDashboardPurchaseCycle` sekarang membawa `case1_link_rows` berisi traceability row-level:
- identity: `cycle_key`, `source_key`, `source_kind`, `source_id`, `product_id`, `bill_line_id`, `bill_move_id`, `purchase_line_id`, `stock_move_id`, `picking_id`
- traceability: `picking_name`, `po_name`, `bill_name`, `stj_move_ids`, `stj_refs`, `payment_move_ids`, `bank_move_ids`, `partner_id`, `partner_name`
- JE seeds: `product_uom_id`, `quantity`, `currency_id`, `amount_currency`, `analytic_distribution`, `journal_code`, `debit_account_code`, `credit_account_code`
- allocation inputs: `bill_price_unit`, `gr_price_unit`, `price_gap_value`, `allocated_amount`

Source priority untuk build row:
1. `bill_line_id` **khusus AML bill akun `2103006` (suspend side)**
2. fallback `purchase_line_id`
3. fallback `stock_move_id`

Jika field relasional langsung ke stock/payment/bank tidak ada di schema runtime, ID sumber tetap disimpan di metadata row untuk exact reconcile dan drilldown.
AML bill produk lain seperti `5101010` tidak lagi dipakai sebagai source row PCB Case 1. Jadi kasus seperti `WCGT/IN/01062` tidak lagi membuat row dari COGS variance line; builder hanya mengambil source dari sisi suspend bill.

### Allocation
Nominal PCB Case 1 sekarang dihitung **per item**, bukan langsung dari residual cycle agregat.

Urutan builder:
1. ambil residual item dari `cycle.item_rows[].account_rows` memakai rule yang sama dengan panel `Detail Saldo Akun per Cycle`
2. item hanya actionable bila `2103006` dan `1108099` masih residual dan saling offset dalam tolerance Case 1
3. total `allocated_amount` semua source row untuk item itu harus sama dengan residual item
4. jika satu item punya beberapa source row, nominal dibagi dengan bobot:
   - prioritas `amount_currency_basis` (`abs(balance)` bill-line `2103006`, fallback `abs(price_subtotal)`)
   - fallback `quantity`
   - fallback `price_gap_value`
   - fallback equal split

Rounding tetap 2 desimal dan sisa rounding dikunci ke bobot terbesar agar total row tetap sama dengan residual item.
Kalau item residual sudah nol atau item-level Case 1 tidak valid, source row item itu tidak masuk collection.
Item residual satu sisi, misalnya setelah Case 5 / Case 6 hanya tersisa `1108099`, juga tidak boleh dihidupkan ulang sebagai Case 1 dan harus diarahkan ke flow `Clearing via Jurnal Reclass`.

### UI dan bulk collect
- Klik kanan pada cycle Case 1 menambahkan seluruh `case1_link_rows` cycle itu ke **PCB Repair Collection**
- Bulk action header module dan context menu klik kanan sidebar sekarang memakai submenu yang sama: `Semua Case Bermasalah`, case-specific options, dan `Case Lainnya saja`
- Bulk action PCB tetap hanya meng-collect cycle yang sedang visible / filtered di sidebar
- Dialog collection menampilkan kolom audit: Picking, Item, Source, PO, Bill, Amount, JE Preview, Guard / Reconcile, Status
- Default split dialog PCB sekarang **45 : 55** (list kiri : detail kanan)
- List kiri dialog PCB sekarang punya **horizontal scrollbar** sendiri untuk row/cell yang panjang
- Layout master/detail dialog PCB default sekarang sekitar **60/40** ke kiri agar grid audit lebih lega; kolom `Item` dan `Bill` ikut `stretch`/resize dan tetap bisa digeser via horizontal scrollbar bila melebihi lebar panel
- `reconcile_readiness_label` untuk row Case 1 sekarang:
  - selalu `Disabled`
  - `reconcile_ready=False` untuk semua row Case 1
  - target AML dan STJ scope tetap dibawa ke row detail hanya sebagai audit/debug
- Panel detail kanan sekarang dibagi menjadi:
  - **Proyeksi Jurnal Per Item**: saldo akun item asal setelah dijumlahkan dengan jurnal simulasi per akun
  - **Detail Saldo Akun Per Cycle (Item Terpilih)**: snapshot saldo akun **item row** asal untuk row yang sedang dipilih, bukan total `cycle.account_rows`
- **Jurnal Simulasi (Belum Execute)**: preview AML lokal dari draft row yang aktif; Case 1 dibangun dari `debit_amount`, `credit_amount`, dan optional `diff_amount`, sedangkan row multi-line Case 2/3/4/5/6/8A/8B/9 dibangun dari `planned_lines`
  - line generated role `selisih_hpp` sekarang memakai label `Selisih HPP - <base label>`
  - payload execute untuk row multi-line harus dibuat one-to-one dari `planned_lines` final yang sama dengan preview: urutan line, side, nominal debit/credit, dan `line_label` tidak boleh berbeda
  - **Summary** (shared `Show More/Hide`, default tertutup dengan compact preview di header): `product_id`, `bill_line_id`, `purchase_line_id`, `stock_move_id`, `picking_id`, `stj_refs`, `result_move_name`, `partner_id`, `reconcile_status`
  - **Advanced** (shared `Show More/Hide`, default tertutup): `stock_move_ids`, `stj_move_ids`, `stj_link_basis`, `stj_candidate_count`, `payment_move_ids`, `bank_move_ids`, `suspend_target_aml_ids`, `clearing_target_aml_ids`, `currency_id`, `analytic_distribution`, `reconcile_message`
- Section simulasi hanya aktif saat tepat **1 row** dipilih:
  - jika belum ada selection: panel menampilkan placeholder `Pilih 1 row untuk melihat simulasi`
  - jika multi-select: panel menampilkan placeholder `Simulasi hanya tersedia untuk 1 row terpilih`
- Exception khusus `Case 1`:
  - jika tepat 1 row `Case 1` dipilih dan item yang sama masih punya sibling source row pada `cycle_key + product_id` yang sama, note panel harus menandai preview sebagai **parsial**
  - jika multi-select hanya berisi row `Case 1` dengan `cycle_key + product_id` yang sama, panel kanan masuk mode **preview gabungan read-only**
  - `Jurnal Simulasi` menampilkan concatenated line dari seluruh row terpilih, dengan trace `source_label` per line
  - `Proyeksi Jurnal Per Item` menghitung delta gabungan hanya dari row terpilih terhadap **saldo item agregat**
  - `Detail Saldo Akun Per Cycle (Item Terpilih)` tetap memakai snapshot **saldo item agregat**, bukan saldo per source line; note panel wajib menyatakan ini eksplisit agar tidak menyesatkan user
  - editor `Jurnal Simulasi` tetap nonaktif pada mode gabungan; execute flow juga tetap **per row**, tidak berubah menjadi per-item
- `Jurnal Simulasi` selalu diberi catatan `Belum execute - preview JE only`
- Untuk row `Needs Review` / `Incomplete` pada Case 2/3/4/5/6/8A/8B/9 maupun row manual review fallback, panel **`Jurnal Simulasi (Belum Execute)`** sekarang juga punya editor draft:
  - user bisa memilih line simulasi yang ada lalu mengubah `Side`, `Account Code`, `Account Name`, `Amount`, dan `Line Label`
  - user bisa menambah `manual line`, menghapus line terpilih, atau `Reset Default` kembali ke hasil builder service
  - suggestion akun selalu berupa union deduped dari akun problem item-item pada cycle aktif, akun HPP kategori item, akun fixed `1108099 - Clearing - System Pending Entries`, akun fixed `2103006 - Hutang Suspensed Pengadaan Barang/Jasa / Suspensed`, plus kandidat akun row/global yang sudah ada
  - dropdown/picker simulasi selalu menampilkan format ringkas `kode - nama`
  - perubahan editor simulasi langsung mempengaruhi preview `Proyeksi Jurnal Per Item` dan payload execute row itu
- `Detail Saldo Akun Per Cycle (Item Terpilih)` dan panel `Proyeksi Jurnal Per Item` sekarang **tidak boleh fallback ke total cycle** bila `item_rows` tersedia, supaya preview tidak menyesatkan user
- `Proyeksi Jurnal Per Item` menampilkan **semua akun item** yang relevan untuk row terpilih, bukan akun cycle gabungan:
  - akun item lama tetap tampil walau tidak berubah
  - akun sintetis baru ditambahkan di bawah bila draft JE memakai akun yang belum ada di saldo item saat ini
- Jika row draft masih punya line target tanpa account code final, panel simulasi tetap menampilkan row `? / Belum dipilih` sebagai preview lokal sampai override account diisi
- Jika `cycle_key` row tidak lagi ditemukan di snapshot aktif, panel current/projected menampilkan notice `Cycle asal tidak ditemukan pada snapshot aktif`
- Jika cycle ada tetapi `item_rows` untuk `product_id` row tidak ditemukan, panel current/projected menampilkan notice `Saldo item asal tidak ditemukan pada snapshot aktif`
- Target AML seed boleh tetap tampil di Row Detail, tetapi tidak lagi dipakai sebagai readiness atau flow eksekusi
- Seluruh panel **Row Detail** di sisi kanan sekarang berada di dalam area scroll sendiri, jadi field bagian bawah tetap bisa diakses saat tinggi dialog terbatas
- Field editable sengaja minimal: `date` dan `reference`
- Default `date` untuk seed JE di PCB Repair Collection sekarang selalu otomatis `today` saat row baru dibentuk, baik untuk Case 1 maupun row multi-line
- Dialog sekarang punya selector execute scope explicit:
  - `Selected Row(s)` = hanya row yang sedang dipilih
  - `All Rows` = seluruh row di collection aktif, walau tidak ada selection
- Footer execute menampilkan `Shared Max Workers` yang dipakai dari konfigurasi atas module; PCB Case 1 tidak membuat setting worker terpisah
- Default `reference` sekarang mengikuti shared `Reference Prefix`:
  - `{Reference Prefix}: Purchase Cycle Balance: {picking_name} / {bill_name} / {item_code} - {item_name}`
- Default `line_label` sekarang:
  - `{item_code} - {item_name} - Case 1 - STJ Bill Miss Match (Clearing - Suspend)`
- `reference_generated` / `line_label_generated` disimpan di collection row. Jika text masih auto-generated, perubahan shared `Reference Prefix` akan me-refresh reference PCB saat dialog dibuka, selection berubah, atau sebelum execute
- Footer dialog menyediakan **Download Excel**. User memilih sendiri lokasi file `.xlsx`, dan export membawa nomor JE hasil create/post (`STJ/MISC/...`) plus trace row-level lainnya
- Jika `stj_refs` kosong tetapi `stock_move_id`/`stock_move_ids` ada, Summary menampilkan pesan `Belum ter-resolve dari source STJ` supaya user tahu ini kondisi unresolved, bukan field hilang
- Untuk row multi-line `Case 2/3/4/5/6/8A/8B/9`, dialog aktif sekarang juga punya editor **`Resolve Account Override`**:
  - hanya aktif untuk row status `Needs Review` atau `Incomplete`
  - row `Ready`, `Running`, `Repaired`, dan `Error` tetap read-only untuk field ini
  - override ini adalah **satu target account per row**, bukan editor per planned line
  - kandidat akun diambil dari akun row/cycle yang relevan lalu digabung dengan global `repair_account_entries`
  - preview dan dropdown override sekarang ditampilkan ringkas sebagai `kode - nama`; provenance source kandidat tidak lagi ditumpuk di preview field ini

### Analyzer refresh dan visibility correction JE
- Posted JE correction PCB dengan `ref` mengandung `Purchase Cycle Balance:` sekarang ikut dipertimbangkan oleh analyzer Purchase Cycle Balance
- Prioritas match correction JE:
  1. direct relation field bila schema runtime memang tersedia
  2. fallback `ref` yang cocok ke `picking_name` + `bill_ref` cycle, lalu diverifikasi lagi dengan AML akun problem (`1108099` / `2103006`) dan `product_id`
- Correction JE yang lolos match ikut masuk ke `relevant_move_ids`, sehingga:
  - `Detail Journal Entry per Cycle Pembelian` / `raw_lines` ikut menampilkan JE correction tersebut
  - saldo akun cycle dihitung ulang bersama correction JE
  - `stj_refs` cycle ikut memuat nomor JE correction yang relevan
  - untuk `Case 3/4`, correction STJ yang sudah match ke cycle juga dihitung sebagai bukti STJ untuk semua item dalam cycle tersebut
  - `cycle_status` bisa turun dari `problem` menjadi `partial` atau `healthy` bila akun problem sudah tertutup
- Setelah execute PCB Case 1 selesai dan ada minimal 1 row terminal (`CREATED`, `POSTED`, atau existing move reused), dashboard otomatis menjalankan analyze ulang
- Saat snapshot baru diterapkan, PCB Repair Collection direconcile ke snapshot terbaru:
  - row non-terminal yang sudah hilang dari snapshot dibuang
  - row non-terminal yang masih ada di-refresh seed dan `latest_snapshot_status`-nya
  - row terminal `repaired` tetap dipertahankan
- Saat dialog PCB Repair Collection dibuka atau row list di-reload, row multi-line juga disinkronkan lagi ke snapshot aktif sebelum dirender:
  - panel `Jurnal Simulasi`, `Proyeksi Jurnal Per Item`, dan `Detail Saldo Akun Per Cycle (Item Terpilih)` harus membaca seed/snapshot terbaru yang sama supaya nominal preview dan status `Ready`/`Needs Review` tidak saling bertentangan
  - legacy generated label `... - Selisih HPP` di-upgrade ke bentuk prefix `Selisih HPP - ...` saat row disinkronkan
  - bila row punya draft `planned_lines` manual (`planned_lines_manual=True`), draft manual tetap dipertahankan; hanya `base_planned_lines` yang ikut di-refresh dari snapshot terbaru untuk kebutuhan `Reset Default`
- Bila hasil analyze masuk **company / database scope yang berbeda**, Repair Collection biasa dan PCB Repair Collection sekarang otomatis dikosongkan dulu agar tidak meninggalkan row scope lama yang terkunci

### Target AML seed dan ranking
Snapshot `case1_link_rows` berusaha mengisi `suspend_target_aml_ids` dan `clearing_target_aml_ids` lebih agresif, tetapi tetap deterministic.

Ranking target seed:
1. link langsung `product_id` / `purchase_line_id` / `stock_move_id`
2. partner match
3. fallback move-scope deterministic dalam bill/STJ yang relevan

Artinya AML lama yang tidak punya `product_id`, `purchase_line_id`, atau `stock_move_id` tidak otomatis dianggap unusable; seed tetap boleh dibawa sebagai audit walaupun tidak lagi dipakai untuk auto reconcile Case 1.

### Create JE dan post flow
Setiap row collection membuat **1 `account.move` baru** dengan 2 `account.move.line`.

Field line diisi secara schema-detected bila tersedia di runtime:
- `name`
- `account_id`
- `product_id`
- `product_uom_id`
- `quantity`
- `purchase_line_id`
- `partner_id`
- `currency_id`
- `amount_currency`
- `amount_currency_basis`
- `analytic_distribution`
- `debit` / `credit`
- custom/direct link fields seperti `stock_move_id`, `picking_id`, `bill_line_id`, `bill_move_id` bila memang ada di schema instance

Mode eksekusi:
- **Execute Draft**: create move draft saja
- **Execute + Post**: create, post, re-read final move name, lalu selesai; **tidak ada auto reconcile** untuk Case 1
- Confirmation dialog dan result text selalu menyebut scope (`Selected Row(s)` / `All Rows`) dan jumlah row yang benar
- Jika scope `Selected Row(s)` aktif tetapi belum ada selection, execute ditolak dengan warning yang jelas

Partner JE Case 1 sekarang diselesaikan dengan urutan:
1. `row.partner_id`
2. partner unik dari target bill / target AML
3. fallback resolve by partner name

Hardening pasca-post untuk Case 1:
- Jika row lokal sudah punya `result_move_id` / `result_move_name`, atau Odoo menemukan move exact match berdasarkan header + line pair yang sama, JE **tidak dibuat ulang**; row ditandai memakai existing move tersebut
- Summary / export tetap memisahkan **JE baru** vs **Existing Reused**
- Field `reconcile_*` tetap dipertahankan di row/result/export untuk kompatibilitas, tetapi nilainya non-aktif untuk Case 1
- Matching / `account.partial.reconcile.create` sengaja tidak dijalankan; penutupan target AML dilakukan manual di luar tool bila memang dibutuhkan

Resolver `stj_move_ids` / `stj_refs` pada `case1_link_rows` sekarang memprioritaskan **STJ cycle yang benar-benar punya AML clearing `1108099` yang compatible** untuk source row tersebut.

Urutan resolver:
1. bila `stock.move.account_move_ids` memang juga merupakan kandidat clearing terbaik, direct STJ itu dipakai
2. kalau direct STJ tidak membawa AML clearing yang compatible, fallback ke STJ cycle terbaik berdasarkan `stock_move_id`
3. fallback berikutnya berdasarkan `purchase_line_id`
4. fallback berikutnya berdasarkan `product_id`
5. fallback terakhir berdasarkan `picking_id`

STJ cycle yang dipakai sebagai fallback sudah lebih dulu dikumpulkan dari path upstream seperti `account.move.stock_move_id` dan `stock.valuation.layer.account_move_id`.

Jika kandidat terbaik lebih dari satu, semua kandidat terbaik disimpan di `stj_move_ids` / `stj_refs`, dan metadata `stj_link_basis` + `stj_candidate_count` ikut dibawa ke UI.

### Amount UI vs amount_currency
Untuk PCB Case 1, **kolom `Amount` di UI adalah source of truth nominal JE**.

Konsekuensinya:
- `allocated_amount` tetap menjadi nominal basis row Case 1, tetapi line JE final bisa pecah menjadi `debit_amount`, `credit_amount`, lalu optional `diff_amount`
- tetapi preview/simulasi Case 1 di dialog collection tetap harus membaca nominal final row-level: `debit_amount`, `credit_amount`, lalu optional `diff_amount` ke `diff_account_code`
- `amount_currency` dan `amount_currency_basis` pada row Case 1 sekarang bersifat **diagnostik/export only**; execute Case 1 tidak lagi mengirim `currency_id` / `amount_currency` ke `account.move.line`
- `amount_currency` tidak boleh lagi memakai denominator `price_gap_value` atau `allocated_amount`
- row sekarang membawa `amount_currency_basis`, yaitu basis full bill-line company amount:
  - prioritas `abs(balance)` bila field AML `balance` tersedia
  - fallback `abs(price_subtotal)`
  - fallback `0.0` bila basis memang tidak ada
- `Price Gap Value` **bukan** driver `diff_amount` Case 1; line selisih Case 1 tetap berasal dari residual signed antara akun problem `1108099` dan `2103006`
- service tetap membuat JE memakai nominal company-currency hasil simulasi Case 1 tanpa payload FX line-level

### Reconcile status
Untuk PCB Case 1, tool tidak lagi mencoba menutup AML target secara otomatis.

Konsekuensinya:
- `reconcile_attempted`, `reconcile_performed`, `reconcile_skipped`, `reconcile_message`, dan `reconcile_error_kind` tetap ada untuk kompatibilitas payload/export
- tetapi pada flow normal Case 1 nilainya tetap non-aktif / kosong
- jika penutupan AML masih diperlukan, itu dilakukan manual di luar tool setelah JE berhasil dibuat atau dipost

### Direction guard untuk saldo suspend minus
Untuk Case 1 dengan saldo `2103006` minus (saldo berada di kredit) dan clearing berada di debit, builder row sekarang tetap mengunci arah JE koreksi agar:
- debit = `2103006`
- credit = `1108099`

Guard ini mencegah jurnal koreksi terbalik dan menghindari kasus nilai menjadi double setelah eksekusi.

### Case 2 / Case 3 / Case 4 / Case 5 / Case 6 / Case 8A / Case 8B / Case 9

#### Granularity dan seed
- Satu row multi-line = satu item dalam satu cycle
- Snapshot `SvlDashboardPurchaseCycle` sekarang membawa `case2_repair_rows`
- Builder multi-line tidak mengambil nominal dari residual cycle agregat; sumber saldo dibaca dari `cycle.item_rows[].account_rows`
- Candidate item Case 2 dibentuk dari pola item-level `STJ suspend + Bill suspend + residual 2103006`
- Candidate item Case 3/4 tetap per item, tetapi sekarang ada **item gate**:
  - item wajib punya **bill item-level**
  - lalu item harus punya **direct STJ item-level**, atau **cycle-matched correction STJ**, atau **verified external clearing**
  - fallback `direct STJ item-level` boleh datang dari `raw_lines` bila source STJ sudah tampil di detail cycle tetapi link `stock.move -> account.move` gagal ter-resolve
- Candidate item Case 5/6 dibentuk bila:
  - `stj_state` missing actual SVL / missing zero SVL / correction-only, bukan direct
  - Bill item-level kena `2103006` untuk Case 5 atau akun expense untuk Case 6
  - nilai SVL aktual wajib tersedia untuk plan executable; evidence kosong tetap Incomplete
- Item yang hanya punya STJ tanpa bill **tidak** dibuat row repair Case 3/4
- Jika cycle-level problem masih ada tetapi seluruh item gagal gate di atas, final grouping cycle fallback ke `case_lainnya`
- Khusus cycle `case3/case4-like` yang gagal gate hanya karena belum punya evidence STJ/clearing, builder tetap boleh membuat row **bill-only candidate** dengan metadata:
  - `review_required=True`
  - `review_confirmed=False`
  - `review_reason` yang menjelaskan bahwa JE preview wajib direview manual
- Bill-only candidate ini **tidak** mengubah auto classification sidebar: cycle tetap bisa tampil sebagai `case_lainnya` sampai item benar-benar `eligible_case34`
- Jika akun HPP kategori item belum resolve, row tetap boleh dibentuk tetapi diberi guard `missing_expense_account` dan status `incomplete`
- Item Case 3/4 yang tidak punya STJ langsung tetap boleh actionable bila `external clearing` berhasil diverifikasi sampai ke item melalui jalur `RAC -> source JE -> item`
- Verifikasi `external clearing` bersifat **repair-only evidence**; ia tidak menambah saldo ke `raw_lines`, `account_rows`, `item_rows`, `total balance`, atau `cycle_status`

### Export Excel Detail Journal Entry per Cycle
- Panel `Detail Journal Entry per Cycle Pembelian` sekarang punya tombol **Download Excel**
- Export ini memakai format **flat-repeat**, bukan tree outline Excel:
  - kolom `Company`, `Cycle`, `Cycle Status`, `Cycle Partner`, dan konteks transaksi diulang per baris supaya aman untuk sort/filter/pivot
  - urutan `Detail JE` mengikuti selector aktif (`Sort by Account` atau `Sort by Process`)
- Workbook output sekarang dibagi menjadi:
  - `Summary`
  - `Detail JE` untuk raw AML cycle yang sedang dipilih
  - `Detail Akun & Catatan` untuk row tambahan yang mirip panel kanan, termasuk `Item Evidence`, `External Clearing`, `Adj Audit`, ringkasan `N akun bermasalah`, dan saldo akun per cycle/item
  - `Data Tidak Tampil` untuk metadata cycle/item yang dipakai analyzer tetapi tidak tampil sebagai kolom di grid kiri `Detail Journal Entry per Cycle Pembelian`
- `partner` per AML row ikut diexport walau di grid kiri aslinya hanya tampil sebagai header partner cycle
- Catatan `external clearing` tetap bersifat evidence-only; ia muncul di sheet catatan/metadata, tetapi tidak mengubah angka `raw_lines`

#### Resolusi akun item
- Akun persediaan item harus resolve exact dari `product.category.property_stock_valuation_account_id`
- Akun HPP / expense default diambil dari account expense kategori item
- Fallback ke item-specific expense account dipakai bila akun category expense memang kosong dan field runtime tersedia
- Jika keduanya kosong, builder boleh fallback ke akun expense `BILL` candidate yang memang terdeteksi pada item/cycle itu sendiri. Ini penting untuk kasus item yang realitanya memakai akun generik seperti `51000010 Cost of Goods Sold`; bila akun itu muncul pada saldo item, akun tersebut dipakai untuk line `Zero HPP` dan `Selisih HPP`
- Jika AML `BILL` candidate tidak berhasil membawa `product_id` / `purchase_line_id`, builder tetap boleh membentuk row Case 2 dengan cara mencocokkan kode expense `BILL` cycle-level ke saldo item yang memang punya akun problem (`1108099` / `2103006`). Jadi cycle tidak boleh otomatis gugur hanya karena mapping produk pada invoice line kosong
- Jika akun HPP kategori item memang ada, akun itu tetap menjadi **target HPP akhir**. Akun HPP sumber lain seperti `51000010` diperlakukan sebagai saldo HPP item yang harus di-zero dulu lalu dipindahkan ke akun HPP kategori tersebut
- Jika cycle memang punya kandidat Case 2 tetapi item tidak membawa saldo HPP target di `account_rows`, builder tetap boleh memakai akun HPP kategori item sebagai **target residual akhir**. Jadi item yang hanya punya akun problem + `5101010` tidak otomatis gugur

#### Planned JE
- Case 2/3/4/5/6/8A/8B/9 tidak lagi memakai satu pasangan debit/kredit tetap
- Service membentuk `planned_lines` dengan rule:
  - tutup setiap akun problem item aktif (`1108099` dan/atau `2103006`) sebesar `abs(net_balance)` dengan arah kebalikan saldo
  - untuk **Case 2**:
    - residual suspend ditutup dulu pada `2103006`
    - lawan saldo diarahkan ke akun expense item sebagai line `Selisih HPP`
  - untuk **Case 3 / Case 4**:
    - reversal semua akun problem item aktif dibuat dulu, sehingga `2103006` dan `1108099` otomatis saling offset bila tandanya berlawanan
    - setelah offset problem, service zero semua HPP source **non-target** pada item: prioritas `5101010`, lalu akun COGS/HPP umum non-target lain
    - hanya residual signed terakhir yang masuk ke **akun HPP kategori item** sebagai line `Selisih HPP`
    - bila stage problem + zero source sudah balance, line `Selisih HPP` tidak dibuat
    - flow planned line ini juga dipakai untuk **bill-only candidate**, tetapi row harus berhenti di status `Needs Review` sampai user mengonfirmasi review manual
  - untuk **Case 5**: pemulihan Dr valuation kategori / Cr 2103006 memakai SVL aktual;
    sisa suspense ke expense kategori, dengan kandidat offset cost dan price gap terpisah.
  - untuk **Case 6**: pemulihan Dr valuation kategori / Cr akun expense bill aktual
    memakai SVL aktual; tidak membuat Synthetic Clearing atau tambahan debit beban.
  - untuk **Case 8A / Case 8B**:
    - nominal utama = `abs(return_value_gap)`
    - jika return mengambil value terlalu besar: Dr akun Persediaan kategori / Cr akun HPP item
    - jika return mengambil value terlalu kecil: Dr akun HPP item / Cr akun Persediaan kategori
    - partial return tidak boleh zero seluruh Persediaan/HPP; planned lines hanya sebesar gap return
  - untuk **Case 9**:
    - nominal utama = `correction_amount` dari evidence holder
    - source qty / bill line / bill refs hanya dihitung dari move vendor bill-refund; `STJ` correction yang share `purchase_line_id` tidak boleh mengubah bill evidence
    - holder `case2_downstream_clearing` dengan HPP kredit dan Persediaan debit: Dr HPP / Cr `1108099` sebesar HPP; Cr Persediaan sebesar saldo Persediaan; Dr `1108099` sebesar HPP; Dr/Cr HPP sebesar selisih signed. Dua line clearing saling menutup. Bila tidak ada saldo persediaan untuk ditutup, plan reclass HPP tetap memerlukan review atas projected clearing.
    - holder `open_suspense_revalued`: Dr `2103006` / Cr HPP
    - holder `open_suspense_inventory`: Dr `2103006` / Cr akun Persediaan kategori
    - holder ambigu membuat row review/incomplete tanpa planned line executable
  - untuk **Case 3 / Case 4** dengan external clearing:
    - jika item tidak punya saldo `1108099` di `account_rows`, tetapi punya `external clearing` item-level yang verified ke clearing `1108099`, service menambahkan line `Audit Clearing`
    - line `Audit Clearing` dipakai **lebih dulu** untuk meng-offset saldo problem `2103006`
    - bila nominal audit-linked clearing hanya sebagian, sisa signed tetap masuk ke akun HPP target item sebagai `Selisih HPP`
    - bila nominal audit-linked clearing menutup penuh, `Selisih HPP` tidak dibuat
  - akun persediaan menjadi line JE pemulihan Case 5/6 dan koreksi Case 8/9; Case 2/3/4 mengikuti rule source/residual masing-masing
  - line nol setelah rounding 2 desimal dibuang
- Item tetap boleh actionable walau akun problem `1108099/2103006` sudah nol, selama masih ada akun HPP sumber non-target yang harus dipindah ke akun HPP kategori item
- Execute Case 2/3/4/5/6/8A/8B/9 memakai service multi-line yang sama (`execute_pcb_case2`), jadi flow Case 1 lama tetap stabil

#### Guard dan coefficient variance
- Seed multi-line membawa metadata `problem_balances_by_code`, `hpp_balances_by_code`, `hpp_balance`, `inventory_balance`, `bank_balances_by_code`, `selisih_hpp_amount`, `coefficient_variance`, `guard_flags`, `guard_messages`, dan `planned_lines`
- Akun `Bank` untuk guard diambil dari raw line cycle yang berjenis `BK` / `PBK`, lalu dibaca lagi dari distribusi saldo item
- Rumus coefficient variance:

```python
abs(Selisih HPP) / max(abs(problem_active_total), abs(hpp_balance_existing), abs(inventory_balance)) * 100
```

- `problem_active_total` dihitung dari total absolut akun problem aktif item yang benar-benar non-zero
- Jika denominator `0` tetapi `Selisih HPP` tidak nol, row otomatis diberi warning manual-check

#### Soft confirm dan dialog detail
- Saat add row multi-line ke PCB Repair Collection, tool meminta konfirmasi bila `coefficient_variance > 35%`, row terkena auto-warning denominator-0 tetapi `Selisih HPP` tidak nol, atau Case 8/9 wajib review
- Bulk collect visible menampilkan satu confirm dialog ringkas yang merangkum row multi-line ter-flag beserta contoh alasannya
- Summary panel row detail multi-line menampilkan `Coeff Variance`, `Guard`, dan `JE Preview`
- Seed + detail kanan multi-line juga menampilkan `External Clearing`, `External Clearing Refs`, `External Clearing Basis`, `External Clearing Verified`, dan `Case Evidence`
- Detail kanan multi-line juga menampilkan state review: `Review Required`, `Review Confirmed`, dan `Review Reason`
- Detail kanan multi-line sekarang juga menampilkan `Resolve Account`, `Resolve Account Code`, dan `Suggested Resolve Account`
- Row `Needs Review` tidak boleh execute sampai user menekan aksi konfirmasi review di dialog PCB Repair Collection
- Detail kanan multi-line menampilkan `Problem Balances`, `HPP Balances`, `HPP Balance`, `Inventory Balance`, `Bank Balances`, `Coeff Variance`, `Selisih HPP`, `Case Evidence`, dan `Planned Lines`
- Jika user mengisi `Resolve Account Override`:
  - row menyimpan `resolve_account_code` + `resolve_account_preview`
  - line target residual/final (`hpp_zero` target dan/atau `selisih_hpp`) di `planned_lines` direwrite ke akun baru
  - guard `missing_expense_account` dianggap sudah terpenuhi bila target override valid terisi
  - row `Incomplete` karena `missing_expense_account` bisa naik menjadi `Needs Review` atau `Ready`, tetapi `Confirm Review` tetap langkah terpisah
  - override manual dipertahankan saat collection direconcile ke snapshot baru selama `row_key` masih sama
- Jika user mengubah line lewat editor `Jurnal Simulasi`:
  - row menyimpan draft `planned_lines` hasil edit sebagai state manual per-row dan mempertahankannya saat collection direconcile ke snapshot baru selama `row_key` masih sama
  - line target `hpp_zero` / `selisih_hpp` yang sudah diedit manual tidak lagi otomatis ditimpa oleh `Resolve Account Override` sampai user `Reset Default`
  - guard `missing_expense_account` dianggap selesai bila line target efektif sudah punya akun final, walau `resolve_account_code` row belum diisi
  - bila hasil edit membuat `planned_lines` kosong atau debit/kredit tidak balance, row otomatis turun ke `Incomplete` dengan alasan blocking dari editor simulasi
- Selain bill-only review, tool sekarang juga mengevaluasi hasil **`Proyeksi Jurnal Per Item`**:
  - bila projected rows masih menyisakan akun berstatus `problem`, row otomatis masuk `Needs Review`
  - `Review Reason` menggabungkan alasan base review (mis. bill-only) dengan alasan projected problem yang tersisa
  - saat seed di-refresh, collection direconcile, override diubah, atau dialog di-reload lalu projected problem masih ada, `review_confirmed` di-reset sehingga user harus `Confirm Review` lagi
- Export Excel PCB collection sekarang ikut membawa `Resolve Account Code`, `Resolve Account Preview`, `Review Required`, `Review Confirmed`, dan `Review Reason`
- Repair row Case 3/4 sekarang juga membawa trace relation item-level: `bill_line_id`, `purchase_line_id`, `stock_move_id`, `stock_move_ids`, `stj_move_ids`, dan `stj_refs`
- Repair row multi-line sekarang juga membawa trace `payment_move_ids` dan `bank_move_ids` per item, tetapi tetap audit-only
- Repair row Case 3/4 sekarang juga membawa `suspend_target_aml_ids` dan `clearing_target_aml_ids` sebagai lookup hint/debug, bukan auto-reconcile
- Repair row multi-line sekarang juga membawa blok ekonomi ringan dari relation yang sama: `product_uom_id`, `quantity`, `currency_id`, `amount_currency`, `analytic_distribution`, `bill_price_unit`, `gr_price_unit`, `price_gap_value`, `allocated_amount`, `repair_basis_amount`, dan `repair_basis_source`
- Scalar relation untuk Case 3/4 dipilih deterministik dari evidence picking cycle:
  - pilih `stock_move_id` utama dulu dari `stock_move_ids` item pada picking cycle yang sama
  - `purchase_line_id` mengikuti `stock_move_id` utama bila tersedia
  - bila `stock_move_id` utama tidak punya `purchase_line_id`, fallback ke `purchase_line_ids` item pada cycle yang sama
  - `bill_line_id` hanya diisi bila builder bisa resolve kandidat bill line yang unambiguous dari bill cycle tersebut
  - bila `stj_move_ids` item kosong, builder boleh recover `stj_refs`/`stj_move_ids` dari `raw_lines` item-level lebih dulu sebelum mencari `clearing_target_aml_ids`
- `payment_move_ids` / `bank_move_ids` diisi hanya dari irisan trace per-product dengan trace payment/bank cycle yang sama; tidak ada fallback whole-cycle bila mapping item kosong
- `suspend_target_aml_ids` / `clearing_target_aml_ids` untuk Case 3/4 dihitung ulang dengan matcher deterministic yang sama seperti Case 1, tetapi tetap dipakai sebagai hint export/detail saja
- Blok ekonomi row multi-line diturunkan dari `bill_line_id` dan `stock_move_id` yang sudah ter-resolve:
  - `product_uom_id`, `currency_id`, `amount_currency`, `analytic_distribution`, dan `bill_price_unit` dari bill line bila tersedia
  - `gr_price_unit` dari stock move utama
  - `quantity` pakai `bill_line.quantity`, fallback ke `stock_move.product_qty` / `quantity`
  - `price_gap_value = abs((bill_price_unit - gr_price_unit) * quantity)`
  - `allocated_amount` memakai basis bill line (`balance`/`price_subtotal`) dan fallback ke `price_gap_value`
- Default `line_label` tetap mengikuti label case masing-masing, misalnya:
  - `{item_code} - {item_name} - Case 2 - STJ Bill Price Diff (Suspend - Suspend)`
  - `{item_code} - {item_name} - Case 3 - STJ Bill Hit Expenses (Clearing - Expenses)`
  - `{item_code} - {item_name} - Case 4 - STJ Bill Hit Expenses (Suspend - Expenses)`
  - `{item_code} - {item_name} - Case 5 - Pemulihan SVL (Inventory - Suspense)`
  - `{item_code} - {item_name} - Case 6 - Pemulihan SVL (Inventory - Bill Expense)`
  - `{item_code} - {item_name} - Case 8A - Full Return Value Mismatch`
  - `{item_code} - {item_name} - Case 8B - Partial Return Value Mismatch`
  - `{item_code} - {item_name} - Case 9 - UoM Scale Mismatch`
- Field `reconcile_*` tetap ada untuk kompatibilitas payload/export, tetapi row multi-line tidak menjalankan reconcile

#### Analyzer refresh
- Correction JE Case 2/3/4/5/6/8A/8B/9 ikut dibaca ulang oleh analyzer bila `ref` cocok ke `Purchase Cycle Balance:` dan signature line cocok ke picking / bill / product cycle tersebut
- Untuk schema runtime yang tidak memakai nama field literal `picking_id` / `bill_move_id`, analyzer correction juga menerima alias relation yang umum dipakai service repair, terutama `stock_picking_id` dan `invoice_id` / `bill_id`
- Saat execute Case 3/4 membuat AML baru, service repair juga menulis scalar relation yang aman bila field schema tersedia: `bill_line_id` / alias vendor bill line, `purchase_line_id`, `stock_move_id`, `stock_picking_id` / `picking_id`, dan `bill_move_id` / `invoice_id` / `bill_id`
- Snapshot cycle sekarang juga menyimpan `bill_move_ids`, `payment_move_ids`, `bank_move_ids`, dan `partner_id`, sehingga rebuild row repair multi-line tidak lagi bergantung pada remap nama bill dari `bill_refs`
- Saat rebuild row repair multi-line, trace `payment_move_ids` / `bank_move_ids` sekarang ikut diwariskan dari `payment_move_ids_by_product` / `bank_move_ids_by_product` yang dipotong ke scope cycle tersebut
- Target AML hint `suspend_target_aml_ids` / `clearing_target_aml_ids` hanya diisi untuk Case 3/4; `Case 2` sengaja tetap kosong agar tidak menyesatkan seolah ada flow reconcile aktif
- Saat `bill_refs` alias/tampilan tidak bisa dipetakan balik persis ke `account.move.name`, repair row tetap memakai linkage ID yang tersimpan di snapshot cycle; ini menjaga `bill_move_id`, `partner_id`, `partner_name`, dan relink correction STJ tetap stabil setelah re-analyze
- Setelah execute row multi-line menghasilkan move terminal (`CREATED`, `POSTED`, atau existing move reused), dashboard tetap auto-analyze ulang dan collection direconcile ke snapshot baru seperti Case 1

### Script diagnostik developer-only
Root repo menyediakan script:

```python
python debug_pcb_case1_reconcile_aml.py --account-code 2103006
python debug_pcb_case1_reconcile_aml.py --aml-id 3739161 --heal
python debug_pcb_case1_reconcile_aml.py --account-code 1108099 --database nama_db
```

Fungsi script:
- audit `account.move.line` target yang masih punya `amount_currency`, `amount_residual`, atau `amount_residual_currency` null
- optional `--heal` untuk best-effort write `amount_currency = 0.0`
- re-read AML sesudah heal agar mudah melihat apakah null-field benar-benar hilang atau tetap korup

## 8. Catatan Pemeliharaan

### Pemeriksaan tanggal jurnal

Lock date wajib dibaca dari `res.company` dengan ID company row yang sedang
diperbaiki. Jangan memakai record terbaru `account.change.lock.date`: wizard itu
bersifat sementara, dapat belum diterapkan dan dapat berasal dari company lain.
Service memakai tanggal maksimum fiscal/user-fiscal/hard/user-hard yang tersedia
di schema; nilai tahun satu berarti tidak terkunci. Gagal membaca company atau
format tanggal tidak valid tetap memblokir posting. Tidak ada perubahan lock
period atau bypass pembatasan Odoo.

Jika pengecekan tanggal gagal sebelum create, collection dapat menampilkan
`Incomplete` dengan hasil `ERROR`, `result_move_id = 0`, dan `result_posted = False`.
Itu bukan bukti jurnal telah posted; cocokkan hasil collection dengan Odoo sebelum
retry. Muat ulang collection setelah memperbaiki sumber pengecekan tanggal dan
pertahankan review manual atas planned lines.

> **PENTING**: Setiap kali ada perubahan pada logic cycle collection, merge, aggregasi, pattern detection, atau status assignment â€” **update dokumen ini**. Tanggal terakhir dianalisis ada di header dokumen.
