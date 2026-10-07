"""Excel export helpers for purchase-cycle detail views."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from smartscc_tools.features.item_journal.utils import normalize_text
from smartscc_tools.features.svl_fix_je.dashboard_service import _PCB_CASE_DESCRIPTIONS

# ── colour palette (company audit export) ────────────────────────────────────
_NAVY    = "1B2A4A"
_ACCENT  = "2E6DB4"
_GRN_HDR = "1E6B42"
_GRN_LT  = "E8F5E9"
_AMB_LT  = "FFF8E1"
_RED_LT  = "FDECEA"
_BLUE_LT = "E3F2FD"
_WHITE   = "FFFFFF"

_STATUS_FILL = {"healthy": _GRN_LT, "partial": _AMB_LT, "problem": _RED_LT}
_STATUS_TXT  = {"healthy": "375623", "partial": "7F4500", "problem": "C00000"}
_ACC_MAIN    = {"2102002", "2103006", "1105003"}

# ── pre-built singleton style objects (reused across all cells) ───────────────
_FILL_CACHE: dict[str, PatternFill] = {}


def _solid(c: str) -> PatternFill:
    """Return cached PatternFill — never allocate a new object for the same colour."""
    if c not in _FILL_CACHE:
        _FILL_CACHE[c] = PatternFill("solid", fgColor=c)
    return _FILL_CACHE[c]


# Single shared border instance for data cells (thin grey all-sides)
_THIN_SIDE    = Side(border_style="thin", color="CCCCCC")
_DATA_BORDER  = Border(left=_THIN_SIDE, right=_THIN_SIDE, top=_THIN_SIDE, bottom=_THIN_SIDE)
_GOLD_SIDE    = Side(border_style="medium", color="C9A84C")
_GREY_SIDE    = Side(border_style="thin",   color="888888")
_HDR_BORDER   = Border(bottom=_GOLD_SIDE, left=_GREY_SIDE, right=_GREY_SIDE)


def _border(color: str = "CCCCCC") -> Border:  # kept for header writes (few cells)
    if color == "CCCCCC":
        return _DATA_BORDER
    s = Side(border_style="thin", color=color)
    return Border(left=s, right=s, top=s, bottom=s)


def _hdr_border() -> Border:
    return _HDR_BORDER


def _write_banner(ws: Worksheet, text: str, last_col: int, fill_color: str = _NAVY) -> None:
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=last_col)
    c = ws.cell(row=1, column=1, value=f"  {text}")
    c.font = Font(name="Calibri", bold=True, size=11, color="FFFFFF")
    c.fill = _solid(fill_color)
    c.alignment = Alignment(vertical="center")
    ws.row_dimensions[1].height = 24


def _write_col_header(ws: Worksheet, row: int, col: int, text: str,
                      fill: str = _ACCENT, width: float | None = None) -> None:
    c = ws.cell(row=row, column=col, value=text)
    c.font = Font(name="Calibri", bold=True, size=9, color="FFFFFF")
    c.fill = _solid(fill)
    c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    c.border = _border()
    if width:
        ws.column_dimensions[get_column_letter(col)].width = width


def _status_cell(ws: Worksheet, row: int, col: int, status_str: str,
                 base_fill: PatternFill) -> None:
    s = str(status_str).lower() if status_str else ""
    if s == "problem":
        f  = _solid(_RED_LT)
        fn = Font(name="Calibri", size=9, color="C00000", bold=True)
    elif s == "balanced":
        f  = _solid(_GRN_LT)
        fn = Font(name="Calibri", size=9, color="375623")
    elif s == "acceptable":
        f  = _solid(_AMB_LT)
        fn = Font(name="Calibri", size=9, color="7F4500")
    else:
        f  = base_fill
        fn = Font(name="Calibri", size=9, color="555555")
    c = ws.cell(row=row, column=col, value=status_str or "-")
    c.font      = fn
    c.fill      = f
    c.alignment = Alignment(horizontal="center", vertical="center")
    c.border    = _DATA_BORDER


def _text_values(values: Any) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        value = normalize_text(values)
        return [value] if value else []
    try:
        iterator = iter(values)
    except TypeError:
        value = normalize_text(values)
        return [value] if value else []
    result: list[str] = []
    for value in iterator:
        text = normalize_text(value)
        if text:
            result.append(text)
    return result


def _join_text_values(values: Any, sep: str = ", ") -> str:
    return sep.join(_text_values(values))


def _po_value_for_raw_line(raw_line: dict[str, Any], cycle_purchase_orders: Any) -> str:
    row_po = normalize_text(raw_line.get("no_po"))
    if row_po:
        return row_po
    return _join_text_values(cycle_purchase_orders, ", ")


def _po_source_for_raw_line(raw_line: dict[str, Any], cycle_purchase_orders: Any) -> str:
    if normalize_text(raw_line.get("no_po")):
        return "row_item_po"
    if _join_text_values(cycle_purchase_orders, ", "):
        return "cycle_bill_po"
    return ""


def export_pcb_company_audit_excel(*, snapshot: Any, output_path: str) -> Path:
    """Export seluruh cycle company ke 5-sheet Excel audit report.

    Sheet 1 — Cycle Overview  : 1 baris = 1 cycle
    Sheet 2 — Item Detail     : 1 baris = 1 item (akun utama di-pivot)
    Sheet 3 — Akun Non-Std    : akun di luar 2102002/2103006/1105003
    Sheet 4 — Raw Ledger      : semua raw_lines flatten; No PO fallback ke cycle PO
    Sheet 5 — Raw Ledger PO Bill: audit view No PO raw vs hasil enrichment
    """
    path = Path(output_path).expanduser().resolve()

    # ── shared style singletons (allocated once, reused across all cells) ─────
    _FONT9     = Font(name="Calibri", size=9)
    _ALIGN_CTR = Alignment(horizontal="center", vertical="center")
    _ALIGN_R   = Alignment(horizontal="right",  vertical="center")
    _ALIGN_L   = Alignment(vertical="center")
    _ALIGN_WRAP= Alignment(vertical="center", wrap_text=True)

    # bold font cache keyed by hex colour (cycle_status has 3 colours max)
    _bold9_font_cache: dict[str, Font] = {}
    def _bold9_cache(color: str) -> Font:
        if color not in _bold9_font_cache:
            _bold9_font_cache[color] = Font(name="Calibri", size=9, bold=True, color=color)
        return _bold9_font_cache[color]

    # status cell pre-built fonts
    _STATUS_FONT = {
        "problem":    Font(name="Calibri", size=9, bold=True, color="C00000"),
        "balanced":   Font(name="Calibri", size=9, color="375623"),
        "acceptable": Font(name="Calibri", size=9, color="7F4500"),
    }

    company_name = normalize_text(getattr(snapshot, "company_name", "")) or "-"
    company_id   = int(getattr(snapshot, "company_id", 0) or 0)
    database     = normalize_text(getattr(snapshot, "database", "")) or "-"
    generated_at = normalize_text(getattr(snapshot, "generated_at", "")) or datetime.now().strftime("%Y-%m-%d")
    cycles       = list(getattr(snapshot, "purchase_cycles", None) or [])

    # ── pre-compute flat datasets ─────────────────────────────────────────────
    item_flat:  list[dict] = []
    acc_nonst:  list[dict] = []
    raw_all:    list[dict] = []

    def _join(lst: Any, sep: str = " | ") -> str:
        return sep.join(str(x) for x in (lst or [])) if lst else ""

    def _status_label(cyc: Any) -> str:
        """Gabungkan cycle_status + UoM Mismatch suffix jika berlaku."""
        base = normalize_text(getattr(cyc, "cycle_status", "")) or ""
        if normalize_text(getattr(cyc, "uom_flag", "")).lower() == "mismatch":
            return f"{base} UoM Mismatch"
        return base

    def _av(acc_map: dict, code: str, field: str, default: Any = 0) -> Any:
        # account_rows are dataclass objects — use getattr, not dict .get()
        row = acc_map.get(code)
        if row is None:
            return default
        return getattr(row, field, default)

    for cyc in cycles:
        pk_name  = normalize_text(getattr(cyc, "picking_name", "")) or ""
        pk_name  = pk_name.split(" ")[0]
        pk_names = _join(getattr(cyc, "picking_names", None) or [pk_name], ", ")
        gr_date  = (normalize_text(getattr(cyc, "gr_date", "")) or "")[:10]
        vendor   = normalize_text(getattr(cyc, "partner_name", "")) or ""
        pos      = _join(getattr(cyc, "purchase_orders", None), ", ")
        status   = _status_label(cyc)
        case     = normalize_text(getattr(cyc, "primary_case", "")) or "-"
        issues   = _join(getattr(cyc, "issue_patterns", None), "; ")

        for item in (getattr(cyc, "item_rows", None) or []):
            # account_rows are dataclass objects — use getattr, not .get()
            item_acc = {getattr(r, "code", ""): r for r in (getattr(item, "account_rows", None) or [])}
            gr_qty  = float(getattr(item, "gr_quantity", 0) or 0)
            std_px  = float(getattr(item, "standard_price", 0) or 0)
            gr_val  = round(gr_qty * std_px, 2)

            item_flat.append({
                "picking_names":  pk_names,
                "picking_name":   pk_name,
                "gr_date":        gr_date,
                "vendor":         vendor,
                "pos":            pos,
                "cycle_status":   status,
                "uom_flag":       normalize_text(getattr(cyc, "uom_flag", "inline")) or "inline",
                "case":           case,
                "item_case":      normalize_text(getattr(item, "primary_case", "")) or "-",
                "default_code":   normalize_text(getattr(item, "default_code", "")) or "",
                "product_name":   normalize_text(getattr(item, "product_name", "")) or "",
                "gr_qty":         gr_qty,
                "bill_qty":       float(getattr(item, "bill_quantity", 0) or 0),
                "std_price":      std_px,
                "gr_value":       gr_val,
                "stj_refs_item":  _join(getattr(item, "stj_refs", None), ", "),
                "bill_refs_item": _join(getattr(item, "bill_refs", None), ", ") or "-",
                "has_bill":       "Y" if getattr(item, "has_item_bill", False) else "N",
                "stj_state":      normalize_text(getattr(item, "stj_state", "")) or "-",
                "bill_hit_role":  normalize_text(getattr(item, "bill_hit_role", "")) or "-",
                "a2102002_d":     _av(item_acc, "2102002", "debit"),
                "a2102002_c":     _av(item_acc, "2102002", "credit"),
                "a2102002_n":     _av(item_acc, "2102002", "net_balance"),
                "a2102002_s":     _av(item_acc, "2102002", "status", ""),
                "a2103006_d":     _av(item_acc, "2103006", "debit"),
                "a2103006_c":     _av(item_acc, "2103006", "credit"),
                "a2103006_n":     _av(item_acc, "2103006", "net_balance"),
                "a2103006_s":     _av(item_acc, "2103006", "status", ""),
                "a1105003_d":     _av(item_acc, "1105003", "debit"),
                "a1105003_c":     _av(item_acc, "1105003", "credit"),
                "a1105003_n":     _av(item_acc, "1105003", "net_balance"),
                "a1105003_s":     _av(item_acc, "1105003", "status", ""),
            })

            for ar in (getattr(item, "account_rows", None) or []):
                ar_code = getattr(ar, "code", "")
                if ar_code not in _ACC_MAIN:
                    acc_nonst.append({
                        "picking_name":  pk_name,
                        "gr_date":       gr_date,
                        "vendor":        vendor,
                        "cycle_status":  status,
                        "default_code":  normalize_text(getattr(item, "default_code", "")) or "",
                        "product_name":  normalize_text(getattr(item, "product_name", "")) or "",
                        "gr_qty":        gr_qty,
                        "gr_value":      gr_val,
                        "akun_code":     ar_code,
                        "akun_name":     getattr(ar, "name", ""),
                        "akun_type":     getattr(ar, "account_type", ""),
                        "debit":         float(getattr(ar, "debit", 0) or 0),
                        "credit":        float(getattr(ar, "credit", 0) or 0),
                        "net_balance":   float(getattr(ar, "net_balance", 0) or 0),
                        "status":        getattr(ar, "status", ""),
                    })

        _cyc_case      = normalize_text(getattr(cyc, "primary_case", "")) or "-"
        _cyc_ket_case  = _PCB_CASE_DESCRIPTIONS.get(_cyc_case, "")
        _cyc_interco   = "Y" if getattr(cyc, "partner_is_intercompany", False) else "N"
        _cyc_issues    = _join(getattr(cyc, "issue_patterns", None), "; ")
        _cyc_uom_flag  = normalize_text(getattr(cyc, "uom_flag", "inline")) or "inline"
        _cyc_po_refs    = _text_values(getattr(cyc, "purchase_orders", None))
        _cyc_po_label   = _join(_cyc_po_refs, ", ")
        _cyc_bill_refs  = _join(getattr(cyc, "bill_refs", None), ", ")
        for rl in (getattr(cyc, "raw_lines", None) or []):
            _raw_po = normalize_text(rl.get("no_po"))
            _po_value = _po_value_for_raw_line(rl, _cyc_po_refs)
            raw_all.append({
                "picking_name":    pk_name,
                "all_pickings":    pk_names,
                "cycle_status":    status,
                "uom_flag":        _cyc_uom_flag,
                "case":            _cyc_case,
                "keterangan_case": _cyc_ket_case,
                "is_intercompany": _cyc_interco,
                "issues":          _cyc_issues,
                "kode_transaksi":  rl.get("kode_transaksi", ""),
                "tanggal":         rl.get("tanggal", ""),
                "jenis":           rl.get("jenis", ""),
                "akun_code":       rl.get("akun_code", ""),
                "akun_name":       rl.get("akun_name", ""),
                "kode_item":       rl.get("kode_item", ""),
                "nama_item":       rl.get("nama_item", ""),
                "uom":             rl.get("uom", ""),
                "qty_item":        rl.get("qty_item"),
                "kategori_produk": rl.get("kategori_produk", ""),
                "no_po":           _po_value,
                "no_po_raw":       _raw_po,
                "no_po_source":    _po_source_for_raw_line(rl, _cyc_po_refs),
                "cycle_po_refs":   _cyc_po_label,
                "cycle_bill_refs": _cyc_bill_refs,
                "partner":         rl.get("partner", ""),
                "debit":           rl.get("debit"),
                "kredit":          rl.get("kredit"),
                "saldo":           rl.get("saldo"),
                "komunikasi":      rl.get("komunikasi", ""),
                "matching":        rl.get("matching", ""),
            })

    # ── build workbook ────────────────────────────────────────────────────────
    wb = Workbook()

    # ── Sheet 1: Cycle Overview ───────────────────────────────────────────────
    ws1 = wb.active
    ws1.title = "1. Cycle Overview"
    ws1.sheet_view.showGridLines = False
    ws1.sheet_properties.tabColor = _NAVY
    ws1.freeze_panes = "A4"

    COLS1 = [
        ("picking_name",  "Picking Name",      22),
        ("picking_names", "All Pickings",       32),
        ("gr_date",       "GR Date",            12),
        ("vendor",        "Vendor",             22),
        ("pos",           "Purchase Order(s)",  28),
        ("cycle_status",     "Status",             10),
        ("uom_flag",         "UoM Flag",           12),
        ("case",             "Case",               14),
        ("keterangan_case",  "Keterangan Case",    60),
        ("is_intercompany",  "Intercompany?",       13),
        ("issues",           "Issue Patterns",     48),
        ("n_items",       "# Items",             7),
        ("gr_value",      "GR Value",           16),
        ("stj_cyc",       "STJ Refs",           36),
        ("bill_cyc",      "Bill Refs",          24),
        ("pay_cyc",       "Payment Refs",       20),
        ("2102002_d",     "2102002 Debit",      14),
        ("2102002_c",     "2102002 Credit",     14),
        ("2102002_n",     "2102002 Net",        14),
        ("2102002_s",     "2102002 Status",     12),
        ("2103006_d",     "2103006 Debit",      14),
        ("2103006_c",     "2103006 Credit",     14),
        ("2103006_n",     "2103006 Net",        14),
        ("2103006_s",     "2103006 Status",     12),
        ("1105003_d",     "1105003 Debit",      14),
        ("1105003_c",     "1105003 Credit",     14),
        ("1105003_n",     "1105003 Net",        14),
        ("1105003_s",     "1105003 Status",     12),
        ("total_d",       "Total Debit",        14),
        ("total_c",       "Total Credit",       14),
        ("prob_accs",     "Problem Accs",       11),
    ]
    NC1 = len(COLS1)
    _write_banner(ws1, f"CYCLE OVERVIEW — {company_name}  |  {generated_at[:10]}", NC1)

    grp1 = [
        ("CYCLE IDENTITY",      1, 10, _NAVY),
        ("QTY / NILAI",        11, 12, "174A7E"),
        ("DOKUMEN REFERENSI",  13, 15, "1F5FAD"),
        ("AKUN 2102002",       16, 19, "145A32"),
        ("AKUN 2103006",       20, 23, "1A5276"),
        ("AKUN 1105003",       24, 27, "0B4C5F"),
        ("CYCLE TOTAL",        28, 30, "4A235A"),
    ]
    for label, sc, ec, fc in grp1:
        if sc < ec:
            ws1.merge_cells(start_row=2, start_column=sc, end_row=2, end_column=ec)
        c = ws1.cell(row=2, column=sc, value=label)
        c.font = Font(name="Calibri", bold=True, size=9, color="FFFFFF")
        c.fill = _solid(fc)
        c.alignment = Alignment(horizontal="center", vertical="center")
        c.border = _hdr_border()
    ws1.row_dimensions[2].height = 20

    for ci, (_, label, w) in enumerate(COLS1, start=1):
        _write_col_header(ws1, 3, ci, label, width=w)
    ws1.row_dimensions[3].height = 30

    MONEY1 = {"gr_value", "2102002_d", "2102002_c", "2102002_n",
               "2103006_d", "2103006_c", "2103006_n",
               "1105003_d", "1105003_c", "1105003_n", "total_d", "total_c"}
    STATUS1 = {"2102002_s", "2103006_s", "1105003_s"}

    for ri, cyc in enumerate(cycles):
        dr = ri + 4
        pk_name    = (normalize_text(getattr(cyc, "picking_name", "")) or "").split(" ")[0]
        pk_names   = _join(getattr(cyc, "picking_names", None) or [pk_name], ", ")
        status_raw = normalize_text(getattr(cyc, "cycle_status", "")) or ""
        status     = _status_label(cyc)
        base_f     = _solid(_STATUS_FILL.get(status_raw, _WHITE))
        txt_c      = _STATUS_TXT.get(status_raw, "000000")
        # account_rows are dataclass objects — key by code via getattr
        cyc_acc  = {getattr(r, "code", ""): r for r in (getattr(cyc, "account_rows", None) or [])}

        def _cav(code: str, field: str, default: Any = 0) -> Any:
            row = cyc_acc.get(code)
            return getattr(row, field, default) if row is not None else default

        vals = {
            "picking_name": pk_name,
            "picking_names": pk_names,
            "gr_date":      (normalize_text(getattr(cyc, "gr_date", "")) or "")[:10],
            "vendor":       normalize_text(getattr(cyc, "partner_name", "")) or "",
            "pos":          _join(getattr(cyc, "purchase_orders", None), ", "),
            "cycle_status": status,
            "uom_flag":     normalize_text(getattr(cyc, "uom_flag", "inline")) or "inline",
            "case":             normalize_text(getattr(cyc, "primary_case", "")) or "-",
            "keterangan_case":  _PCB_CASE_DESCRIPTIONS.get(
                                    normalize_text(getattr(cyc, "primary_case", "")) or "",
                                    "",
                                ),
            "is_intercompany":  "Y" if getattr(cyc, "partner_is_intercompany", False) else "N",
            "issues":           _join(getattr(cyc, "issue_patterns", None), "; "),
            "n_items":      len(list(getattr(cyc, "item_rows", None) or [])),
            "gr_value":     sum(
                float(getattr(it, "gr_quantity", 0) or 0) * float(getattr(it, "standard_price", 0) or 0)
                for it in (getattr(cyc, "item_rows", None) or [])
            ),
            "stj_cyc":      _join(getattr(cyc, "stj_refs", None), ", "),
            "bill_cyc":     _join(getattr(cyc, "bill_refs", None), ", "),
            "pay_cyc":      _join(getattr(cyc, "payment_refs", None), ", "),
            "2102002_d":    _cav("2102002", "debit"),
            "2102002_c":    _cav("2102002", "credit"),
            "2102002_n":    _cav("2102002", "net_balance"),
            "2102002_s":    _cav("2102002", "status", ""),
            "2103006_d":    _cav("2103006", "debit"),
            "2103006_c":    _cav("2103006", "credit"),
            "2103006_n":    _cav("2103006", "net_balance"),
            "2103006_s":    _cav("2103006", "status", ""),
            "1105003_d":    _cav("1105003", "debit"),
            "1105003_c":    _cav("1105003", "credit"),
            "1105003_n":    _cav("1105003", "net_balance"),
            "1105003_s":    _cav("1105003", "status", ""),
            "total_d":      float(getattr(cyc, "total_debit", 0) or 0),
            "total_c":      float(getattr(cyc, "total_credit", 0) or 0),
            "prob_accs":    int(getattr(cyc, "problem_account_count", 0) or 0),
        }

        for ci, (key, _, _w) in enumerate(COLS1, start=1):
            val = vals.get(key, "")
            if key in STATUS1:
                _status_cell(ws1, dr, ci, str(val), base_f)
                continue
            c = ws1.cell(row=dr, column=ci, value=val)
            c.border = _DATA_BORDER
            if key == "cycle_status":
                c.fill = base_f
                c.font = _bold9_cache(txt_c)
                c.alignment = _ALIGN_CTR
            elif key in MONEY1:
                c.fill = base_f
                c.font = _FONT9
                c.number_format = "#,##0"
                c.alignment = _ALIGN_R
            elif key in ("n_items", "gr_date"):
                c.fill = base_f
                c.font = _FONT9
                c.alignment = _ALIGN_CTR
            elif key == "is_intercompany":
                c.fill = _solid(_GRN_LT) if val == "Y" else base_f
                c.font = _bold9_cache("375623") if val == "Y" else _FONT9
                c.alignment = _ALIGN_CTR
            elif key in {"issues", "keterangan_case", "stj_cyc", "bill_cyc"}:
                c.fill = base_f
                c.font = _FONT9
                c.alignment = _ALIGN_WRAP
            else:
                c.fill = base_f
                c.font = _FONT9
                c.alignment = _ALIGN_L

        ws1.row_dimensions[dr].height = 30 if (vals.get("issues") or vals.get("keterangan_case")) else 18

    ws1.auto_filter.ref = f"A3:{get_column_letter(NC1)}3"
    # ── Sheet 2: Item Detail ──────────────────────────────────────────────────
    ws2 = wb.create_sheet("2. Item Detail")
    ws2.sheet_view.showGridLines = False
    ws2.sheet_properties.tabColor = _ACCENT
    ws2.freeze_panes = "C4"

    COLS2 = [
        ("picking_name",   "Picking",           18),
        ("gr_date",        "GR Date",           12),
        ("vendor",         "Vendor",            20),
        ("cycle_status",   "Status",            10),
        ("uom_flag",       "UoM Flag",          12),
        ("case",           "Case",              14),
        ("item_case",      "Item Case",         12),
        ("default_code",   "Kode Item",         16),
        ("product_name",   "Nama Produk",       36),
        ("gr_qty",         "Qty GR",             9),
        ("bill_qty",       "Qty Bill",           9),
        ("std_price",      "Harga Std",         14),
        ("gr_value",       "Nilai GR",          16),
        ("stj_refs_item",  "STJ Item",          26),
        ("bill_refs_item", "Bill Item",         20),
        ("has_bill",       "Ada Bill?",          9),
        ("stj_state",      "STJ State",         11),
        ("bill_hit_role",  "Bill Role",         12),
        ("a2102002_d",     "2102002 D",         14),
        ("a2102002_c",     "2102002 C",         14),
        ("a2102002_n",     "2102002 Net",       14),
        ("a2102002_s",     "2102002 Sts",       11),
        ("a2103006_d",     "2103006 D",         14),
        ("a2103006_c",     "2103006 C",         14),
        ("a2103006_n",     "2103006 Net",       14),
        ("a2103006_s",     "2103006 Sts",       11),
        ("a1105003_d",     "1105003 D",         14),
        ("a1105003_c",     "1105003 C",         14),
        ("a1105003_n",     "1105003 Net",       14),
        ("a1105003_s",     "1105003 Sts",       11),
    ]
    NC2 = len(COLS2)
    _write_banner(ws2, f"ITEM DETAIL — {company_name}  |  {generated_at[:10]}", NC2)

    grp2 = [
        ("CYCLE CONTEXT",                   1,  7, _NAVY),
        ("PRODUK & NILAI",                  8, 13, "174A7E"),
        ("DOKUMEN",                        14, 18, "1F5FAD"),
        ("AKUN 2102002 — Htg Berelasi",   19, 22, "145A32"),
        ("AKUN 2103006 — Htg Suspensed",  23, 26, "1A5276"),
        ("AKUN 1105003 — Persediaan",     27, 30, "0B4C5F"),
    ]
    for label, sc, ec, fc in grp2:
        if sc < ec:
            ws2.merge_cells(start_row=2, start_column=sc, end_row=2, end_column=ec)
        c = ws2.cell(row=2, column=sc, value=label)
        c.font = Font(name="Calibri", bold=True, size=9, color="FFFFFF")
        c.fill = _solid(fc)
        c.alignment = Alignment(horizontal="center", vertical="center")
        c.border = _hdr_border()
    ws2.row_dimensions[2].height = 20

    for ci, (_, label, w) in enumerate(COLS2, start=1):
        _write_col_header(ws2, 3, ci, label, width=w)
    ws2.row_dimensions[3].height = 30

    MONEY2  = {"std_price","gr_value","a2102002_d","a2102002_c","a2102002_n",
               "a2103006_d","a2103006_c","a2103006_n","a1105003_d","a1105003_c","a1105003_n"}
    STATUS2 = {"a2102002_s","a2103006_s","a1105003_s"}
    QTY2    = {"gr_qty", "bill_qty"}

    prev_pick  = None
    shade_idx  = 0
    for ri, row in enumerate(item_flat):
        dr = ri + 4
        if row["picking_names"] != prev_pick:
            shade_idx += 1
            prev_pick = row["picking_names"]
        base_f     = _solid(_BLUE_LT if shade_idx % 2 == 0 else _WHITE)
        status     = row["cycle_status"]
        status_raw = status.split(" ")[0]  # ambil base: healthy/partial/problem
        txt_c      = _STATUS_TXT.get(status_raw, "000000")

        for ci, (key, _, _w) in enumerate(COLS2, start=1):
            val = row.get(key, "")
            if key in STATUS2:
                _status_cell(ws2, dr, ci, str(val), base_f)
                continue
            c = ws2.cell(row=dr, column=ci, value=val)
            c.border = _DATA_BORDER
            if key == "cycle_status":
                c.fill = _solid(_STATUS_FILL.get(status, _WHITE))
                c.font = _bold9_cache(txt_c)
                c.alignment = _ALIGN_CTR
            elif key in MONEY2:
                c.fill = base_f
                c.font = _FONT9
                c.number_format = "#,##0"
                c.alignment = _ALIGN_R
            elif key in QTY2:
                c.fill = base_f
                c.font = _FONT9
                c.number_format = "#,##0.##"
                c.alignment = _ALIGN_CTR
            elif key == "has_bill":
                c.fill = _solid(_GRN_LT) if val == "Y" else _solid(_RED_LT)
                c.font = _bold9_cache("375623" if val == "Y" else "C00000")
                c.alignment = _ALIGN_CTR
            elif key in ("gr_date", "stj_state", "bill_hit_role", "item_case", "case"):
                c.fill = base_f
                c.font = _FONT9
                c.alignment = _ALIGN_CTR
            elif key in {"product_name", "stj_refs_item"}:
                c.fill = base_f
                c.font = _FONT9
                c.alignment = _ALIGN_WRAP
            else:
                c.fill = base_f
                c.font = _FONT9
                c.alignment = _ALIGN_L
        ws2.row_dimensions[dr].height = 18

    # Total row
    total_dr2 = len(item_flat) + 4
    ws2.merge_cells(start_row=total_dr2, start_column=1, end_row=total_dr2, end_column=6)
    tc = ws2.cell(row=total_dr2, column=1, value="TOTAL")
    tc.font = Font(name="Calibri", bold=True, size=10, color="FFFFFF")
    tc.fill = _solid(_NAVY)
    tc.alignment = Alignment(horizontal="right", vertical="center")
    tc.border = _border()
    key_list2 = [c[0] for c in COLS2]
    for key in ["gr_qty","bill_qty","gr_value",
                "a2102002_d","a2102002_c","a2102002_n",
                "a2103006_d","a2103006_c","a2103006_n",
                "a1105003_d","a1105003_c","a1105003_n"]:
        if key in key_list2:
            ci = key_list2.index(key) + 1
            cl = get_column_letter(ci)
            c = ws2.cell(row=total_dr2, column=ci, value=f"=SUM({cl}4:{cl}{total_dr2-1})")
            c.font = Font(name="Calibri", bold=True, size=9, color="FFFFFF")
            c.fill = _solid(_NAVY)
            c.alignment = Alignment(horizontal="right", vertical="center")
            c.number_format = "#,##0.##" if key in ("gr_qty","bill_qty") else "#,##0"
            c.border = _border()
    ws2.row_dimensions[total_dr2].height = 22
    ws2.auto_filter.ref = f"A3:{get_column_letter(NC2)}3"

    # ── Sheet 3: Akun Non-Std ─────────────────────────────────────────────────
    ws3 = wb.create_sheet("3. Akun Non-Std")
    ws3.sheet_view.showGridLines = False
    ws3.sheet_properties.tabColor = _GRN_HDR
    ws3.freeze_panes = "A4"

    COLS3 = [
        ("picking_name",  "Picking",        18),
        ("gr_date",       "GR Date",        12),
        ("vendor",        "Vendor",         20),
        ("cycle_status",  "Status",         10),
        ("default_code",  "Kode Item",      16),
        ("product_name",  "Nama Produk",    34),
        ("gr_qty",        "Qty GR",          9),
        ("gr_value",      "Nilai GR",       16),
        ("akun_code",     "Kode Akun",      12),
        ("akun_name",     "Nama Akun",      44),
        ("akun_type",     "Tipe Akun",      18),
        ("debit",         "Debit",          16),
        ("credit",        "Credit",         16),
        ("net_balance",   "Net Balance",    16),
        ("status",        "Status Akun",    12),
    ]
    NC3 = len(COLS3)
    _write_banner(ws3, f"AKUN NON-STANDAR — {company_name}  |  {generated_at[:10]}", NC3, _GRN_HDR)

    ws3.merge_cells(start_row=2, start_column=1, end_row=2, end_column=NC3)
    ir = ws3.cell(row=2, column=1,
                  value="  Akun di luar 2102002 / 2103006 / 1105003 — termasuk HPP/COGS variance dan akun koreksi lainnya")
    ir.font = Font(name="Calibri", italic=True, size=9, color="375623")
    ir.fill = _solid("E8F5E9")
    ir.alignment = Alignment(vertical="center")
    ws3.row_dimensions[2].height = 18

    for ci, (_, label, w) in enumerate(COLS3, start=1):
        _write_col_header(ws3, 3, ci, label, fill=_GRN_HDR, width=w)
    ws3.row_dimensions[3].height = 24

    MONEY3 = {"gr_value","debit","credit","net_balance"}
    _f9_net_neg = Font(name="Calibri", size=9, color="C00000")
    _f9_net_pos = Font(name="Calibri", size=9, color="1A5276")
    for ri, row in enumerate(acc_nonst):
        dr = ri + 4
        status     = row["cycle_status"]
        status_raw = status.split(" ")[0]
        base_f     = _solid(_STATUS_FILL.get(status_raw, _WHITE))
        for ci, (key, _, _w) in enumerate(COLS3, start=1):
            val = row.get(key, "")
            if key == "status":
                _status_cell(ws3, dr, ci, str(val), base_f)
                continue
            c = ws3.cell(row=dr, column=ci, value=val)
            c.border = _DATA_BORDER
            if key in MONEY3:
                c.fill = base_f
                c.number_format = "#,##0"
                c.alignment = _ALIGN_R
                if key == "net_balance":
                    nv = float(val) if val else 0
                    c.font = _f9_net_neg if nv < 0 else (_f9_net_pos if nv > 0 else _FONT9)
                else:
                    c.font = _FONT9
            elif key == "cycle_status":
                sv = str(val).lower()
                c.fill = _solid(_STATUS_FILL.get(sv, _WHITE))
                c.font = _bold9_cache(_STATUS_TXT.get(sv, "000000"))
                c.alignment = _ALIGN_CTR
            elif key in ("gr_qty", "gr_date"):
                c.fill = base_f
                c.font = _FONT9
                c.alignment = _ALIGN_CTR
            elif key in {"akun_name", "product_name"}:
                c.fill = base_f
                c.font = _FONT9
                c.alignment = _ALIGN_WRAP
            else:
                c.fill = base_f
                c.font = _FONT9
                c.alignment = _ALIGN_L
        ws3.row_dimensions[dr].height = 16

    ws3.auto_filter.ref = f"A3:{get_column_letter(NC3)}3"

    # ── Sheet 4: Raw Ledger ───────────────────────────────────────────────────
    ws4 = wb.create_sheet("4. Raw Ledger")
    ws4.sheet_view.showGridLines = False
    ws4.sheet_properties.tabColor = "4A235A"
    ws4.freeze_panes = "C3"  # freeze Cycle Name + All Pickings, scroll mulai Cycle Status

    COLS4 = [
        # ── Cycle Identity ────────────────────────────────────────────────────
        ("picking_name",    "Cycle Name",      22),
        ("all_pickings",    "All Pickings",    42),
        ("cycle_status",    "Cycle Status",    12),
        ("uom_flag",        "UoM Flag",        11),
        ("case",            "Case",            20),
        ("keterangan_case", "Keterangan Case", 60),
        ("is_intercompany", "Intercompany?",   12),
        ("issues",          "Issue Patterns",  52),
        # ── Transaksi ─────────────────────────────────────────────────────────
        ("kode_transaksi",  "Picking Num",     24),
        ("tanggal",         "Tanggal",         12),
        ("jenis",           "Jenis",            8),
        # ── Akun ─────────────────────────────────────────────────────────────
        ("akun_code",       "Kode Akun",       12),
        ("akun_name",       "Nama Akun",       44),
        # ── Item ─────────────────────────────────────────────────────────────
        ("kode_item",       "Kode Item",       16),
        ("nama_item",       "Nama Item",       28),
        ("uom",             "UOM",             16),
        ("qty_item",        "Qty",              9),
        ("kategori_produk", "Kategori",        18),
        # ── Dokumen ──────────────────────────────────────────────────────────
        ("no_po",           "No PO",           20),
        ("partner",         "Partner",         20),
        # ── Nilai ────────────────────────────────────────────────────────────
        ("debit",           "Debit",           16),
        ("kredit",          "Kredit",          16),
        ("saldo",           "Saldo",           16),
        # ── Catatan ──────────────────────────────────────────────────────────
        ("komunikasi",      "Komunikasi",      36),
        ("matching",        "Matching",        20),
    ]
    NC4 = len(COLS4)
    _write_banner(ws4, f"RAW LEDGER — {company_name}  |  {generated_at[:10]}", NC4, "4A235A")

    for ci, (_, label, w) in enumerate(COLS4, start=1):
        _write_col_header(ws4, 2, ci, label, fill="4A235A", width=w)
    ws4.row_dimensions[2].height = 24

    # Pre-built font/alignment singletons for raw ledger (33k rows — avoid per-cell alloc)
    _f9          = Font(name="Calibri", size=9)
    _f9_r        = Alignment(horizontal="right",  vertical="center")
    _f9_ctr      = Alignment(horizontal="center", vertical="center")
    _f9_l        = Alignment(vertical="center")
    _f9_neg      = Font(name="Calibri", size=9, color="C00000")
    _f9_pos      = Font(name="Calibri", size=9, color="1A5276")
    _JENIS_FONT  = {
        "STJ":     Font(name="Calibri", size=9, bold=True, color="1A5276"),
        "BILL":    Font(name="Calibri", size=9, bold=True, color="7B241C"),
        "PAYMENT": Font(name="Calibri", size=9, bold=True, color="186A3B"),
        "BK":      Font(name="Calibri", size=9, bold=True, color="4A235A"),
        "PBK":     Font(name="Calibri", size=9, bold=True, color="4A235A"),
    }
    _STATUS_FONT4 = {
        "healthy": Font(name="Calibri", size=9, bold=True, color="375623"),
        "partial": Font(name="Calibri", size=9, bold=True, color="7F4500"),
        "problem": Font(name="Calibri", size=9, bold=True, color="C00000"),
    }
    _UOM_FLAG_FONT = {
        "mismatch": Font(name="Calibri", size=9, bold=True, color="C00000"),
        "inline":   Font(name="Calibri", size=9, color="375623"),
    }
    MONEY4  = {"debit", "kredit", "saldo"}
    WRAP4   = {"akun_name", "nama_item", "komunikasi", "keterangan_case", "issues", "all_pickings"}
    _f9_wrap = Alignment(vertical="center", wrap_text=True)

    col4_keys = [key for key, _, _ in COLS4]
    # Stripe fill applied to cycle identity + money columns; pure text cols stay white
    _FILL4_TEXT = {"picking_name", "all_pickings", "kode_transaksi", "case",
                   "keterangan_case", "issues"}

    prev_trx = None
    shade4   = True
    for ri, rl in enumerate(raw_all):
        dr = ri + 3
        kode = rl.get("kode_transaksi", "")
        if kode != prev_trx:
            shade4   = not shade4
            prev_trx = kode
        base_f   = _solid("EDE7F6" if shade4 else _WHITE)
        cyc_stat = str(rl.get("cycle_status", "")).split(" ")[0].lower()  # base: healthy/partial/problem

        for ci, key in enumerate(col4_keys, start=1):
            val = rl.get(key, "")
            c   = ws4.cell(row=dr, column=ci, value=val)
            # No border on raw ledger rows (saves ~13s on 33k rows)
            if key in MONEY4:
                c.fill   = base_f
                c.number_format = "#,##0"
                c.alignment = _f9_r
                if key == "saldo":
                    nv = float(val) if val else 0
                    c.font = _f9_neg if nv < 0 else (_f9_pos if nv > 0 else _f9)
                else:
                    c.font = _f9
            elif key == "jenis":
                c.fill      = base_f
                c.font      = _JENIS_FONT.get(str(val).upper(), _f9)
                c.alignment = _f9_ctr
            elif key == "cycle_status":
                c.fill      = _solid(_STATUS_FILL.get(cyc_stat, "EDE7F6" if shade4 else _WHITE))
                c.font      = _STATUS_FONT4.get(cyc_stat, _f9)
                c.alignment = _f9_ctr
            elif key == "uom_flag":
                c.fill      = base_f
                c.font      = _UOM_FLAG_FONT.get(str(val).lower(), _f9)
                c.alignment = _f9_ctr
            elif key == "is_intercompany":
                c.fill      = _solid(_GRN_LT) if val == "Y" else base_f
                c.font      = _bold9_cache("375623") if val == "Y" else _f9
                c.alignment = _f9_ctr
            elif key in _FILL4_TEXT:
                c.fill      = base_f
                c.font      = _f9
                c.alignment = _f9_wrap if key in WRAP4 else _f9_l
            elif key == "qty_item":
                c.number_format = "#,##0.##"
                c.alignment = _f9_ctr
                c.font      = _f9
            elif key in ("tanggal", "case"):
                c.alignment = _f9_ctr
                c.font      = _f9
            elif key in WRAP4:
                c.alignment = _f9_wrap
                c.font      = _f9
            else:
                c.alignment = _f9_l
                c.font      = _f9

    ws4.auto_filter.ref = f"A2:{get_column_letter(NC4)}2"

    # Same base columns as Raw Ledger. Column S remains "No PO", but this
    # audit view appends the original row-level PO and enrichment source.
    ws5 = wb.create_sheet("5. Raw Ledger PO Bill")
    ws5.sheet_view.showGridLines = False
    ws5.sheet_properties.tabColor = "7030A0"
    ws5.freeze_panes = "C3"

    COLS5 = [
        *COLS4,
        ("no_po_raw",       "No PO Raw",     20),
        ("no_po_source",    "Sumber PO",     18),
        ("cycle_po_refs",   "Cycle PO(s)",   30),
        ("cycle_bill_refs", "Cycle Bill(s)", 30),
    ]
    NC5 = len(COLS5)
    _write_banner(ws5, f"RAW LEDGER PO BILL — {company_name}  |  {generated_at[:10]}", NC5, "7030A0")
    for ci, (_, label, w) in enumerate(COLS5, start=1):
        _write_col_header(ws5, 2, ci, label, fill="7030A0", width=w)
    ws5.row_dimensions[2].height = 24

    col5_keys = [key for key, _, _ in COLS5]
    prev_trx = None
    shade5 = True
    for ri, rl in enumerate(raw_all):
        dr = ri + 3
        kode = rl.get("kode_transaksi", "")
        if kode != prev_trx:
            shade5 = not shade5
            prev_trx = kode
        base_f = _solid("EDE7F6" if shade5 else _WHITE)
        cyc_stat = str(rl.get("cycle_status", "")).split(" ")[0].lower()

        for ci, key in enumerate(col5_keys, start=1):
            val = rl.get(key, "")
            c = ws5.cell(row=dr, column=ci, value=val)
            if key in MONEY4:
                c.fill = base_f
                c.number_format = "#,##0"
                c.alignment = _f9_r
                if key == "saldo":
                    nv = float(val) if val else 0
                    c.font = _f9_neg if nv < 0 else (_f9_pos if nv > 0 else _f9)
                else:
                    c.font = _f9
            elif key == "jenis":
                c.fill = base_f
                c.font = _JENIS_FONT.get(str(val).upper(), _f9)
                c.alignment = _f9_ctr
            elif key == "cycle_status":
                c.fill = _solid(_STATUS_FILL.get(cyc_stat, "EDE7F6" if shade5 else _WHITE))
                c.font = _STATUS_FONT4.get(cyc_stat, _f9)
                c.alignment = _f9_ctr
            elif key == "uom_flag":
                c.fill = base_f
                c.font = _UOM_FLAG_FONT.get(str(val).lower(), _f9)
                c.alignment = _f9_ctr
            elif key == "is_intercompany":
                c.fill = _solid(_GRN_LT) if val == "Y" else base_f
                c.font = _bold9_cache("375623") if val == "Y" else _f9
                c.alignment = _f9_ctr
            elif key == "no_po_source":
                c.fill = _solid(_BLUE_LT if val == "cycle_bill_po" else _WHITE)
                c.font = _bold9_cache("1A5276") if val == "cycle_bill_po" else _f9
                c.alignment = _f9_ctr
            elif key in _FILL4_TEXT:
                c.fill = base_f
                c.font = _f9
                c.alignment = _f9_wrap if key in WRAP4 else _f9_l
            elif key == "qty_item":
                c.number_format = "#,##0.##"
                c.alignment = _f9_ctr
                c.font = _f9
            elif key in ("tanggal", "case"):
                c.alignment = _f9_ctr
                c.font = _f9
            elif key in WRAP4 or key in {"no_po", "no_po_raw", "cycle_po_refs", "cycle_bill_refs"}:
                c.alignment = _f9_wrap
                c.font = _f9
            else:
                c.alignment = _f9_l
                c.font = _f9

    ws5.auto_filter.ref = f"A2:{get_column_letter(NC5)}2"

    # ── save ──────────────────────────────────────────────────────────────────
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(path))
    wb.close()
    return path


def _autosize_worksheet(worksheet: Worksheet, *, max_width: int = 48) -> None:
    widths: dict[int, int] = {}
    for row in worksheet.iter_rows(values_only=True):
        for index, value in enumerate(row, start=1):
            text = normalize_text(value)
            if not text:
                continue
            widths[index] = min(max(widths.get(index, 0), len(text) + 2), max_width)
    for index, width in widths.items():
        worksheet.column_dimensions[chr(64 + index) if index <= 26 else worksheet.cell(row=1, column=index).column_letter].width = width


def export_pcb_cycle_detail_excel(*, payload: dict[str, Any], output_path: str) -> Path:
    path = Path(output_path).expanduser().resolve()
    workbook = Workbook()
    try:
        summary_sheet = workbook.active
        summary_sheet.title = "Summary"
        summary_sheet.append(["Company", normalize_text(payload.get("company_name")) or "-"])
        summary_sheet.append(["Company ID", int(payload.get("company_id") or 0)])
        summary_sheet.append(["Database", normalize_text(payload.get("database")) or "-"])
        summary_sheet.append(["Cycle", normalize_text(payload.get("cycle_name")) or "-"])
        summary_sheet.append(["Cycle Status", normalize_text(payload.get("cycle_status")) or "-"])
        summary_sheet.append(["GR Date", normalize_text(payload.get("gr_date")) or "-"])
        summary_sheet.append(["Cycle Partner", normalize_text(payload.get("partner_name")) or "-"])
        summary_sheet.append(["Purchase Order", normalize_text(payload.get("po_label")) or "-"])
        summary_sheet.append(["Sort Mode", normalize_text(payload.get("sort_mode")) or "-"])
        summary_sheet.append(["Raw AML Rows", int(payload.get("raw_row_count") or 0)])
        summary_sheet.append(["Detail/Notes Rows", int(payload.get("detail_row_count") or 0)])
        summary_sheet.append(["Hidden Field Rows", int(payload.get("hidden_row_count") or 0)])
        summary_sheet.append(["Exported At", normalize_text(payload.get("exported_at")) or "-"])
        summary_sheet.append([])
        summary_sheet.append(["Catatan", "Export ini mengulang kolom company/cycle/partner/transaksi per baris agar aman untuk filter/sort Excel."])
        summary_sheet.append(["Catatan", "Sheet 'Detail Akun & Catatan' memuat row tambahan seperti Item Evidence, External Clearing, adjustment audit, dan ringkasan akun bermasalah."])
        summary_sheet.append(["Catatan", "Sheet 'Data Tidak Tampil' mencatat metadata cycle/item yang dipakai analyzer tetapi tidak tampil sebagai kolom di grid kiri Detail JE."])

        raw_sheet = workbook.create_sheet("Detail JE")
        raw_sheet.append(
            [
                "No",
                "Company",
                "Company ID",
                "Database",
                "Cycle",
                "Cycle Status",
                "Cycle Partner",
                "GR Date",
                "PO",
                "Process Group",
                "TGL",
                "KODE TRANSAKSI",
                "JENIS",
                "TIPE AKUN",
                "KODE AKUN",
                "NAMA AKUN",
                "KODE ITEM",
                "NAMA ITEM",
                "UOM",
                "QTY",
                "KATEGORI",
                "NO PO",
                "KOMUNIKASI",
                "DEBIT",
                "KREDIT",
                "SALDO",
                "MATCHING",
                "PARTNER ROW",
            ]
        )
        for row in list(payload.get("raw_rows") or []):
            raw_sheet.append(
                [
                    int(row.get("row_no") or 0),
                    normalize_text(row.get("company_name")),
                    int(row.get("company_id") or 0),
                    normalize_text(row.get("database")),
                    normalize_text(row.get("cycle_name")),
                    normalize_text(row.get("cycle_status")),
                    normalize_text(row.get("cycle_partner")),
                    normalize_text(row.get("gr_date")),
                    normalize_text(row.get("po_label")),
                    normalize_text(row.get("process_group")),
                    normalize_text(row.get("tanggal")),
                    normalize_text(row.get("kode_transaksi")),
                    normalize_text(row.get("jenis")),
                    normalize_text(row.get("tipe_akun")),
                    normalize_text(row.get("akun_code")),
                    normalize_text(row.get("akun_name")),
                    normalize_text(row.get("kode_item")),
                    normalize_text(row.get("nama_item")),
                    normalize_text(row.get("uom")),
                    row.get("qty_item"),
                    normalize_text(row.get("kategori_produk")),
                    normalize_text(row.get("no_po")),
                    normalize_text(row.get("komunikasi")),
                    row.get("debit"),
                    row.get("kredit"),
                    row.get("saldo"),
                    normalize_text(row.get("matching")),
                    normalize_text(row.get("partner_row")),
                ]
            )

        detail_sheet = workbook.create_sheet("Detail Akun & Catatan")
        detail_sheet.append(
            [
                "No",
                "Scope",
                "Row Kind",
                "Company",
                "Company ID",
                "Database",
                "Cycle",
                "Cycle Status",
                "Cycle Partner",
                "Item Code",
                "Item Name",
                "Date",
                "Journal Source",
                "Transaction No",
                "GR Reference",
                "PO",
                "Partner Reference",
                "Account Code",
                "Account Name / Detail",
                "Debit",
                "Credit",
                "Balance",
                "Matching / Status",
                "Note",
            ]
        )
        for row in list(payload.get("detail_rows") or []):
            detail_sheet.append(
                [
                    int(row.get("row_no") or 0),
                    normalize_text(row.get("scope_level")),
                    normalize_text(row.get("row_kind")),
                    normalize_text(row.get("company_name")),
                    int(row.get("company_id") or 0),
                    normalize_text(row.get("database")),
                    normalize_text(row.get("cycle_name")),
                    normalize_text(row.get("cycle_status")),
                    normalize_text(row.get("cycle_partner")),
                    normalize_text(row.get("item_code")),
                    normalize_text(row.get("item_name")),
                    normalize_text(row.get("date")),
                    normalize_text(row.get("journal_source")),
                    normalize_text(row.get("transaction_no")),
                    normalize_text(row.get("gr_reference")),
                    normalize_text(row.get("po")),
                    normalize_text(row.get("partner_reference")),
                    normalize_text(row.get("akun")),
                    normalize_text(row.get("akun_name")),
                    row.get("debit"),
                    row.get("credit"),
                    row.get("balance"),
                    normalize_text(row.get("matching")),
                    normalize_text(row.get("note_detail")),
                ]
            )

        hidden_sheet = workbook.create_sheet("Data Tidak Tampil")
        hidden_sheet.append(
            [
                "No",
                "Scope",
                "Cycle",
                "Item Code",
                "Item Name",
                "Field",
                "Value",
                "Source UI",
                "Keterangan",
            ]
        )
        for row in list(payload.get("hidden_rows") or []):
            hidden_sheet.append(
                [
                    int(row.get("row_no") or 0),
                    normalize_text(row.get("scope_level")),
                    normalize_text(row.get("cycle_name")),
                    normalize_text(row.get("item_code")),
                    normalize_text(row.get("item_name")),
                    normalize_text(row.get("field_name")),
                    normalize_text(row.get("value_text")),
                    normalize_text(row.get("source_ui")) or "Detail Journal Entry per Cycle Pembelian",
                    normalize_text(row.get("note")),
                ]
            )

        for worksheet in (raw_sheet, detail_sheet, hidden_sheet):
            worksheet.freeze_panes = "A2"
            if worksheet.max_row >= 1 and worksheet.max_column >= 1:
                worksheet.auto_filter.ref = worksheet.dimensions
        for worksheet in workbook.worksheets:
            _autosize_worksheet(worksheet)

        path.parent.mkdir(parents=True, exist_ok=True)
        workbook.save(path)
        return path
    finally:
        workbook.close()
