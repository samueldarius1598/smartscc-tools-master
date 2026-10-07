# SVL Fix JE Handoff: Saldo Akun per Item

Dokumen ini adalah handoff ringkas untuk sesi baru yang bekerja pada dashboard `SVL Fix JE`, khususnya mode `Saldo Akun per Item`.

## Scope
- Dataset mode baru: `account_balance`
- Label UI: `Saldo Akun per Item`
- Mode ini menggantikan konsep lama `current_asset` untuk kebutuhan saldo akun per item berbasis flow pembelian

## Tujuan Fitur
- Menampilkan saldo akun per item dari flow procurement yang utuh
- Menelusuri transaksi mulai dari receipt sampai bill dan payment
- Memisahkan saldo inventory dan non-inventory tanpa menduplikasi hutang vendor penuh ke setiap item

## Trace Flow
- Sumber utama: `stock.move`
- Lanjut ke:
  - `stock.picking`
  - `purchase.order`
  - `purchase.order.line`
  - `account.move` untuk STJ dan bill
  - `account.move.line` untuk ledger item
  - `account.payment` untuk payment tracing

Urutan trace utama:
1. `stock.move -> account_move_ids`
2. fallback `account.move.stock_move_id`
3. `stock.move/picking -> purchase.order`
4. `purchase.order -> invoice_ids`
5. fallback `account.move.invoice_origin`
6. `bill -> account.payment`

## Aturan Data Penting
- Ledger utama saldo item hanya dari `account.move.line` yang `product_id`-linked
- Inventory classification memakai manual inventory COA settings dulu
- Valuation account kategori produk hanya fallback jika manual inventory COA tidak match
- Sidebar grouping mode ini: `Akun -> Posisi -> Kategori -> Item`
- Item yang sama bisa muncul di beberapa akun; semua leaf tetap membuka detail item yang sama

## Payable Allocation
- Payable/payment adalah companion detail, bukan ledger utama item
- Nilai payable item dialokasikan prorata dari kontribusi bill item
- Bill yang punya nilai tanpa linkage item harus tetap masuk `unassigned bill remainder`
- Jangan pernah assign full AP bill ke semua item di bill yang sama

## Compatibility Notes
- Beberapa environment Odoo 18 dapat gagal pada domain `account.payment.reconciled_bill_ids` saat `search_read`
- Service sudah difallback agar payment tracing tetap lanjut lewat `invoice_ids`
- Jangan asumsikan field relation aman hanya karena ada di `fields_get`; tetap uji `search_read`
- Untuk compatibility triage, pola paling aman:
  - `fields_get()`
  - query kecil `search_read()`
  - baru pakai di service utama

## Request / State / UI
- Dataset mode request: `account_balance`
- Toggle scope akun:
  - `include_inventory_accounts`
  - `include_non_inventory_accounts`
- Legacy `hide_inventory_accounts` masih dinormalisasi untuk compatibility, tetapi bukan source utama lagi

## Detail Tab Semantics
- Tab utama mode ini: `Saldo Akun per Item`
- `PO vs Bill` tetap berlaku
- `SVL Detail`, `Journal Detail`, `Gabungan Detail`, `Analisis Selisih` tetap ada tetapi placeholder saat mode ini aktif

## File Kunci
- Service: [smartscc_tools/features/svl_fix_je/dashboard_service.py](../smartscc_tools/features/svl_fix_je/dashboard_service.py)
- Models: [smartscc_tools/features/svl_fix_je/models.py](../smartscc_tools/features/svl_fix_je/models.py)
- Config normalization: [smartscc_tools/features/svl_fix_je/config.py](../smartscc_tools/features/svl_fix_je/config.py)
- Export: [smartscc_tools/features/svl_fix_je/dashboard_export.py](../smartscc_tools/features/svl_fix_je/dashboard_export.py)
- Dashboard UI: [smartscc_tools/modules/svl_fix_je_dashboard_page.py](../smartscc_tools/modules/svl_fix_je_dashboard_page.py)
- Module shell/state wiring: [smartscc_tools/modules/svl_fix_je_module.py](../smartscc_tools/modules/svl_fix_je_module.py)
- Tests:
  - [tests/test_svl_dashboard_service.py](../tests/test_svl_dashboard_service.py)
  - [tests/test_svl_fix_je_adapter.py](../tests/test_svl_fix_je_adapter.py)
  - [tests/test_svl_dashboard_export.py](../tests/test_svl_dashboard_export.py)

## Verification Baseline
- Target regression:
  - `python -m unittest tests.test_svl_dashboard_service tests.test_svl_fix_je_adapter tests.test_svl_dashboard_export`
- Manual startup smoke:
  - `python main.py`

## Jika Melanjutkan Pekerjaan
- Baca dokumen ini dulu
- Lalu baca service account-balance path di `dashboard_service.py`
- Jangan kembali ke desain `current_asset` lama kecuali memang sedang mengerjakan compatibility alias
