"""
export_pcb_excel.py
Menghasilkan laporan Excel dari tmp_pcb_<company_id>_final.json

Sheet layout:
  1. Cycle Overview   — 1 baris = 1 cycle, ringkasan dokumen & status akun agregat
  2. Item Detail      — 1 baris = 1 item, akun utama (2102002/2103006/1105003) di-pivot
  3. Akun Non-Std     — baris akun di luar 3 kode utama (flat), untuk audit HPP/COGS
  4. Raw Ledger       — semua raw_lines flatten + konteks picking

Usage:
  python export_pcb_excel.py                          # auto-detect JSON di folder yang sama
  python export_pcb_excel.py path/to/file.json        # explicit
"""

import json, sys, os, re
from pathlib import Path
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

# ── colour palette ────────────────────────────────────────────────────────────
NAVY     = "1B2A4A"
ACCENT   = "2E6DB4"
GOLD     = "C9A84C"
GRN_HDR  = "1E6B42"   # sheet3 header
GRN_LT   = "E8F5E9"   # healthy row
AMB_LT   = "FFF8E1"   # partial row
RED_LT   = "FDECEA"   # problem row
BLUE_LT  = "E3F2FD"   # cycle shade A
WHITE    = "FFFFFF"
GRAY_LT  = "F5F5F5"   # cycle shade B

STATUS_FILL = {"healthy": GRN_LT, "partial": AMB_LT, "problem": RED_LT}
STATUS_TXT  = {"healthy": "375623", "partial": "7F4500", "problem": "C00000"}

ACC_MAIN = ["2102002", "2103006", "1105003"]
ACC_LABEL = {
    "2102002": "Htg Berelasi",
    "2103006": "Htg Suspensed",
    "1105003": "Persediaan",
}

# ── helpers ───────────────────────────────────────────────────────────────────
def solid(c): return PatternFill("solid", fgColor=c)

def border(style="thin", color="CCCCCC"):
    s = Side(border_style=style, color=color)
    return Border(left=s, right=s, top=s, bottom=s)

def hdr_border():
    """Medium bottom border only (for group headers)."""
    return Border(bottom=Side(border_style="medium", color=GOLD),
                  left=Side(border_style="thin", color="888888"),
                  right=Side(border_style="thin", color="888888"))

def join(lst, sep=" | "):
    return sep.join(str(x) for x in lst) if lst else ""

def money(ws, row, col, value):
    c = ws.cell(row=row, column=col, value=value if value else 0)
    c.number_format = '#,##0'
    c.alignment = Alignment(horizontal="right", vertical="center")
    return c

def qty(ws, row, col, value):
    c = ws.cell(row=row, column=col, value=value if value else 0)
    c.number_format = '#,##0.##'
    c.alignment = Alignment(horizontal="center", vertical="center")
    return c

def write_banner(ws, text, last_col, fill_color=NAVY, font_color="FFFFFF"):
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=last_col)
    c = ws.cell(row=1, column=1, value=f"  {text}")
    c.font = Font(name="Calibri", bold=True, size=11, color=font_color)
    c.fill = solid(fill_color)
    c.alignment = Alignment(vertical="center")
    ws.row_dimensions[1].height = 24

def write_col_header(ws, row, col, text, fill=ACCENT, font_color="FFFFFF",
                     wrap=False, width=None):
    c = ws.cell(row=row, column=col, value=text)
    c.font = Font(name="Calibri", bold=True, size=9, color=font_color)
    c.fill = solid(fill)
    c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=wrap)
    c.border = border()
    if width:
        ws.column_dimensions[get_column_letter(col)].width = width
    return c

def apply_row_fill(ws, row_num, col_start, col_end, fill_pat):
    for c in range(col_start, col_end + 1):
        ws.cell(row=row_num, column=c).fill = fill_pat

def status_cell(ws, row, col, status_str, fill_base):
    """Write status with colour coding."""
    s = str(status_str).lower() if status_str else ""
    if s == "problem":
        f = solid(RED_LT); fc = "C00000"
    elif s == "balanced":
        f = solid(GRN_LT); fc = "375623"
    elif s == "acceptable":
        f = solid(AMB_LT); fc = "7F4500"
    else:
        f = fill_base; fc = "555555"
    c = ws.cell(row=row, column=col, value=status_str or "-")
    c.font = Font(name="Calibri", size=9, color=fc, bold=(s == "problem"))
    c.fill = f
    c.alignment = Alignment(horizontal="center", vertical="center")
    c.border = border()
    return c

# ── load data ─────────────────────────────────────────────────────────────────
script_dir = Path(__file__).parent
if len(sys.argv) > 1:
    json_path = Path(sys.argv[1])
else:
    candidates = sorted(script_dir.glob("tmp_pcb_*_final.json"))
    if not candidates:
        raise FileNotFoundError("Tidak ada file tmp_pcb_*_final.json di folder ini.")
    json_path = candidates[-1]   # ambil yang terbaru

print(f"[INFO] Membaca: {json_path}")
with open(json_path, encoding="utf-8") as f:
    data = json.load(f)

company_name = data["company_name"]
company_id   = data["company_id"]
generated_at = data["generated_at"][:10]
cycles       = data.get("purchase_cycles", [])
print(f"[INFO] {len(cycles)} cycles dimuat")

def _status_label(cyc: dict) -> str:
    """Gabungkan cycle_status + ' UoM Mismatch' suffix jika uom_flag == mismatch."""
    base = cyc.get("cycle_status", "") or ""
    if (cyc.get("uom_flag") or "").lower() == "mismatch":
        return f"{base} UoM Mismatch"
    return base

# ── pre-compute item flat rows & non-std-account rows ─────────────────────────
item_flat   = []   # sheet 2
acc_nonst   = []   # sheet 3
raw_all     = []   # sheet 4

for cyc in cycles:
    pk_name  = cyc["picking_name"].split(" ")[0]
    pk_names = join(cyc.get("picking_names", [pk_name]), ", ")
    gr_date  = cyc["gr_date"][:10]
    vendor   = cyc["partner_name"]
    pos      = join(cyc.get("purchase_orders", []))
    status   = _status_label(cyc)
    case     = cyc.get("primary_case") or "-"
    issues   = join(cyc.get("issue_patterns", []), "; ")
    stj_cyc  = join(cyc.get("stj_refs", []), ", ")
    bill_cyc = join(cyc.get("bill_refs", []), ", ")
    pay_cyc  = join(cyc.get("payment_refs", []), ", ")

    # ── item rows ─────────────────────────────────────────────────────────
    for item in cyc.get("item_rows", []):
        item_acc = {r["code"]: r for r in item.get("account_rows", [])}
        def av(code, field, default=0):
            return item_acc.get(code, {}).get(field, default)

        gr_val = round(item["gr_quantity"] * item["standard_price"], 2)

        row = {
            "picking_name":   pk_name,
            "picking_names":  pk_names,
            "gr_date":        gr_date,
            "vendor":         vendor,
            "pos":            pos,
            "cycle_status":   status,
            "case":           case,
            "issues":         issues,
            "default_code":   item["default_code"],
            "product_name":   item["product_name"],
            "gr_qty":         item["gr_quantity"],
            "bill_qty":       item["bill_quantity"],
            "std_price":      item["standard_price"],
            "gr_value":       gr_val,
            "stj_refs_item":  join(item.get("stj_refs", []), ", "),
            "bill_refs_item": join(item.get("bill_refs", []), ", ") or "-",
            "has_bill":       "Y" if item.get("has_item_bill") else "N",
            "stj_state":      item.get("stj_state") or "-",
            "bill_hit_role":  item.get("bill_hit_role") or "-",
            "item_case":      item.get("primary_case") or "-",
            # akun main
            "a2102002_d":   av("2102002","debit"),
            "a2102002_c":   av("2102002","credit"),
            "a2102002_n":   av("2102002","net_balance"),
            "a2102002_s":   av("2102002","status",""),
            "a2103006_d":   av("2103006","debit"),
            "a2103006_c":   av("2103006","credit"),
            "a2103006_n":   av("2103006","net_balance"),
            "a2103006_s":   av("2103006","status",""),
            "a1105003_d":   av("1105003","debit"),
            "a1105003_c":   av("1105003","credit"),
            "a1105003_n":   av("1105003","net_balance"),
            "a1105003_s":   av("1105003","status",""),
        }
        item_flat.append(row)

        # non-standard accounts per item
        for ar in item.get("account_rows", []):
            if ar["code"] not in ACC_MAIN:
                acc_nonst.append({
                    "picking_name":  pk_name,
                    "gr_date":       gr_date,
                    "vendor":        vendor,
                    "cycle_status":  status,
                    "default_code":  item["default_code"],
                    "product_name":  item["product_name"],
                    "gr_qty":        item["gr_quantity"],
                    "gr_value":      gr_val,
                    "akun_code":     ar["code"],
                    "akun_name":     ar["name"],
                    "akun_type":     ar["account_type"],
                    "debit":         ar["debit"],
                    "credit":        ar["credit"],
                    "net_balance":   ar["net_balance"],
                    "status":        ar.get("status",""),
                })

    # ── raw lines ─────────────────────────────────────────────────────────
    for rl in cyc.get("raw_lines", []):
        raw_all.append({
            **rl,
            "picking_name":  pk_name,
            "cycle_status":  status,
        })

print(f"[INFO] {len(item_flat)} item rows | {len(acc_nonst)} non-std akun rows | {len(raw_all)} raw lines")

# ══════════════════════════════════════════════════════════════════════════════
# WORKBOOK
# ══════════════════════════════════════════════════════════════════════════════
wb = Workbook()

# ────────────────────────────────────────────────────────────────────────────
# SHEET 1 — Cycle Overview
# ────────────────────────────────────────────────────────────────────────────
ws1 = wb.active
ws1.title = "1. Cycle Overview"
ws1.sheet_view.showGridLines = False
ws1.sheet_properties.tabColor = NAVY
ws1.freeze_panes = "A4"

COLS1 = [
    ("picking_name",  "Picking Name",       22),
    ("picking_names", "All Pickings",        32),
    ("gr_date",       "GR Date",             12),
    ("vendor",        "Vendor",              22),
    ("pos",           "Purchase Order(s)",   28),
    ("cycle_status",  "Status",              10),
    ("case",          "Case",                14),
    ("issues",        "Issue Patterns",      48),
    ("n_items",       "# Items",              7),
    ("gr_value",      "GR Value",            16),
    ("stj_cyc",       "STJ Refs",            36),
    ("bill_cyc",      "Bill Refs",           24),
    ("pay_cyc",       "Payment Refs",        20),
    ("2102002_d",     "2102002 Debit",       14),
    ("2102002_c",     "2102002 Credit",      14),
    ("2102002_n",     "2102002 Net",         14),
    ("2102002_s",     "2102002 Status",      12),
    ("2103006_d",     "2103006 Debit",       14),
    ("2103006_c",     "2103006 Credit",      14),
    ("2103006_n",     "2103006 Net",         14),
    ("2103006_s",     "2103006 Status",      12),
    ("1105003_d",     "1105003 Debit",       14),
    ("1105003_c",     "1105003 Credit",      14),
    ("1105003_n",     "1105003 Net",         14),
    ("1105003_s",     "1105003 Status",      12),
    ("total_d",       "Total Debit",         14),
    ("total_c",       "Total Credit",        14),
    ("prob_accs",     "Problem Accs",        11),
]
NC1 = len(COLS1)

write_banner(ws1, f"CYCLE OVERVIEW — {company_name}  |  {generated_at}", NC1)

# Group header row (row 2)
# Groups: Identity(1-8), Qty/Val(9-10), Docs(11-13), 2102002(14-17), 2103006(18-21), 1105003(22-25), Totals(26-28)
grp1_defs = [
    ("CYCLE IDENTITY",        1,   8, NAVY),
    ("QTY / NILAI",           9,  10, "174A7E"),
    ("DOKUMEN REFERENSI",    11,  13, "1F5FAD"),
    ("AKUN 2102002",         14,  17, "145A32"),
    ("AKUN 2103006",         18,  21, "1A5276"),
    ("AKUN 1105003",         22,  25, "0B4C5F"),
    ("CYCLE TOTAL",          26,  28, "4A235A"),
]
for label, sc, ec, fc in grp1_defs:
    if sc < ec:
        ws1.merge_cells(start_row=2, start_column=sc, end_row=2, end_column=ec)
    c = ws1.cell(row=2, column=sc, value=label)
    c.font = Font(name="Calibri", bold=True, size=9, color="FFFFFF")
    c.fill = solid(fc)
    c.alignment = Alignment(horizontal="center", vertical="center")
    c.border = hdr_border()
ws1.row_dimensions[2].height = 20

# Column headers (row 3)
for ci, (key, label, w) in enumerate(COLS1, start=1):
    write_col_header(ws1, 3, ci, label, width=w, wrap=True)
ws1.row_dimensions[3].height = 30

# Data rows
for ri, cyc in enumerate(cycles):
    dr = ri + 4
    pk_name    = cyc["picking_name"].split(" ")[0]
    pk_names   = join(cyc.get("picking_names", [pk_name]), ", ")
    status_raw = cyc.get("cycle_status", "") or ""
    status     = _status_label(cyc)
    base_f     = solid(STATUS_FILL.get(status_raw, WHITE))
    txt_c      = STATUS_TXT.get(status_raw, "000000")

    cyc_acc  = {r["code"]: r for r in cyc.get("account_rows", [])}
    def cav(code, field, default=0):
        return cyc_acc.get(code, {}).get(field, default)

    vals = {
        "picking_name": pk_name,
        "picking_names": pk_names,
        "gr_date":      cyc["gr_date"][:10],
        "vendor":       cyc["partner_name"],
        "pos":          join(cyc.get("purchase_orders", []), ", "),
        "cycle_status": status,
        "case":         cyc.get("primary_case") or "-",
        "issues":       join(cyc.get("issue_patterns", []), "; "),
        "n_items":      len(cyc.get("item_rows", [])),
        "gr_value":     sum(it["gr_quantity"]*it["standard_price"] for it in cyc.get("item_rows",[])),
        "stj_cyc":      join(cyc.get("stj_refs", []), ", "),
        "bill_cyc":     join(cyc.get("bill_refs", []), ", "),
        "pay_cyc":      join(cyc.get("payment_refs", []), ", "),
        "2102002_d":    cav("2102002","debit"),
        "2102002_c":    cav("2102002","credit"),
        "2102002_n":    cav("2102002","net_balance"),
        "2102002_s":    cav("2102002","status",""),
        "2103006_d":    cav("2103006","debit"),
        "2103006_c":    cav("2103006","credit"),
        "2103006_n":    cav("2103006","net_balance"),
        "2103006_s":    cav("2103006","status",""),
        "1105003_d":    cav("1105003","debit"),
        "1105003_c":    cav("1105003","credit"),
        "1105003_n":    cav("1105003","net_balance"),
        "1105003_s":    cav("1105003","status",""),
        "total_d":      cyc.get("total_debit",0),
        "total_c":      cyc.get("total_credit",0),
        "prob_accs":    cyc.get("problem_account_count",0),
    }

    MONEY_KEYS1 = {"gr_value","2102002_d","2102002_c","2102002_n",
                   "2103006_d","2103006_c","2103006_n",
                   "1105003_d","1105003_c","1105003_n","total_d","total_c"}
    STATUS_KEYS1 = {"2102002_s","2103006_s","1105003_s"}

    for ci, (key, label, w) in enumerate(COLS1, start=1):
        val = vals.get(key, "")
        if key in STATUS_KEYS1:
            status_cell(ws1, dr, ci, val, base_f)
            continue
        c = ws1.cell(row=dr, column=ci, value=val)
        c.font = Font(name="Calibri", size=9)
        c.border = border()
        if key == "cycle_status":
            c.fill = base_f
            c.font = Font(name="Calibri", size=9, bold=True, color=txt_c)
            c.alignment = Alignment(horizontal="center", vertical="center")
        elif key in MONEY_KEYS1:
            c.fill = base_f
            c.number_format = '#,##0'
            c.alignment = Alignment(horizontal="right", vertical="center")
        elif key == "n_items":
            c.fill = base_f
            c.alignment = Alignment(horizontal="center", vertical="center")
        elif key == "gr_date":
            c.fill = base_f
            c.alignment = Alignment(horizontal="center", vertical="center")
        else:
            c.fill = base_f
            c.alignment = Alignment(vertical="center", wrap_text=(key in {"issues","stj_cyc","bill_cyc"}))

    # row height
    has_issues = bool(vals.get("issues"))
    ws1.row_dimensions[dr].height = 30 if has_issues else 18

# Auto-filter
ws1.auto_filter.ref = f"A3:{get_column_letter(NC1)}3"

# ────────────────────────────────────────────────────────────────────────────
# SHEET 2 — Item Detail
# ────────────────────────────────────────────────────────────────────────────
ws2 = wb.create_sheet("2. Item Detail")
ws2.sheet_view.showGridLines = False
ws2.sheet_properties.tabColor = ACCENT
ws2.freeze_panes = "C4"

COLS2 = [
    # cycle context
    ("picking_name",   "Picking",           18),
    ("gr_date",        "GR Date",           12),
    ("vendor",         "Vendor",            20),
    ("cycle_status",   "Status",            10),
    ("case",           "Case",              14),
    ("item_case",      "Item Case",         12),
    # product
    ("default_code",   "Kode Item",         16),
    ("product_name",   "Nama Produk",       36),
    ("gr_qty",         "Qty GR",             9),
    ("bill_qty",       "Qty Bill",           9),
    ("std_price",      "Harga Std",         14),
    ("gr_value",       "Nilai GR",          16),
    # docs
    ("stj_refs_item",  "STJ Item",          26),
    ("bill_refs_item", "Bill Item",         20),
    ("has_bill",       "Ada Bill?",          9),
    ("stj_state",      "STJ State",         11),
    ("bill_hit_role",  "Bill Role",         12),
    # akun 2102002
    ("a2102002_d",     "2102002 D",         14),
    ("a2102002_c",     "2102002 C",         14),
    ("a2102002_n",     "2102002 Net",       14),
    ("a2102002_s",     "2102002 Sts",       11),
    # akun 2103006
    ("a2103006_d",     "2103006 D",         14),
    ("a2103006_c",     "2103006 C",         14),
    ("a2103006_n",     "2103006 Net",       14),
    ("a2103006_s",     "2103006 Sts",       11),
    # akun 1105003
    ("a1105003_d",     "1105003 D",         14),
    ("a1105003_c",     "1105003 C",         14),
    ("a1105003_n",     "1105003 Net",       14),
    ("a1105003_s",     "1105003 Sts",       11),
]
NC2 = len(COLS2)

write_banner(ws2, f"ITEM DETAIL — {company_name}  |  {generated_at}", NC2)

# Group header row
grp2_defs = [
    ("CYCLE CONTEXT",          1,  6, NAVY),
    ("PRODUK & NILAI",         7, 12, "174A7E"),
    ("DOKUMEN",               13, 17, "1F5FAD"),
    ("AKUN 2102002 — Htg Berelasi",    18, 21, "145A32"),
    ("AKUN 2103006 — Htg Suspensed",   22, 25, "1A5276"),
    ("AKUN 1105003 — Persediaan",      26, 29, "0B4C5F"),
]
for label, sc, ec, fc in grp2_defs:
    if sc < ec:
        ws2.merge_cells(start_row=2, start_column=sc, end_row=2, end_column=ec)
    c = ws2.cell(row=2, column=sc, value=label)
    c.font = Font(name="Calibri", bold=True, size=9, color="FFFFFF")
    c.fill = solid(fc)
    c.alignment = Alignment(horizontal="center", vertical="center")
    c.border = hdr_border()
ws2.row_dimensions[2].height = 20

for ci, (key, label, w) in enumerate(COLS2, start=1):
    write_col_header(ws2, 3, ci, label, width=w, wrap=True)
ws2.row_dimensions[3].height = 30

MONEY2 = {"std_price","gr_value","a2102002_d","a2102002_c","a2102002_n",
           "a2103006_d","a2103006_c","a2103006_n","a1105003_d","a1105003_c","a1105003_n"}
STATUS2 = {"a2102002_s","a2103006_s","a1105003_s"}
QTY2    = {"gr_qty","bill_qty"}

prev_pick = None
shade_idx = 0
for ri, row in enumerate(item_flat):
    dr = ri + 4
    if row["picking_names"] != prev_pick:
        shade_idx += 1
        prev_pick = row["picking_names"]
    base_f     = solid(BLUE_LT if shade_idx % 2 == 0 else WHITE)
    status     = row["cycle_status"]
    status_raw = status.split(" ")[0]
    txt_c      = STATUS_TXT.get(status_raw, "000000")

    for ci, (key, label, w) in enumerate(COLS2, start=1):
        val = row.get(key, "")
        if key in STATUS2:
            status_cell(ws2, dr, ci, val, base_f)
            continue
        c = ws2.cell(row=dr, column=ci, value=val)
        c.font = Font(name="Calibri", size=9)
        c.border = border()
        if key == "cycle_status":
            c.fill = solid(STATUS_FILL.get(status, WHITE))
            c.font = Font(name="Calibri", size=9, bold=True, color=txt_c)
            c.alignment = Alignment(horizontal="center", vertical="center")
        elif key in MONEY2:
            c.fill = base_f
            c.number_format = '#,##0'
            c.alignment = Alignment(horizontal="right", vertical="center")
        elif key in QTY2:
            c.fill = base_f
            c.number_format = '#,##0.##'
            c.alignment = Alignment(horizontal="center", vertical="center")
        elif key == "has_bill":
            c.fill = solid(GRN_LT) if val == "Y" else solid(RED_LT)
            c.font = Font(name="Calibri", size=9, bold=True,
                          color="375623" if val == "Y" else "C00000")
            c.alignment = Alignment(horizontal="center", vertical="center")
        elif key == "gr_date":
            c.fill = base_f
            c.alignment = Alignment(horizontal="center", vertical="center")
        else:
            c.fill = base_f
            c.alignment = Alignment(vertical="center",
                                    wrap_text=(key in {"product_name","stj_refs_item"}))

    ws2.row_dimensions[dr].height = 18

# Total row
total_dr2 = len(item_flat) + 4
ws2.merge_cells(start_row=total_dr2, start_column=1, end_row=total_dr2, end_column=6)
tc = ws2.cell(row=total_dr2, column=1, value="TOTAL")
tc.font = Font(name="Calibri", bold=True, size=10, color="FFFFFF")
tc.fill = solid(NAVY)
tc.alignment = Alignment(horizontal="right", vertical="center")
tc.border = border()

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
        c.fill = solid(NAVY)
        c.alignment = Alignment(horizontal="right", vertical="center")
        c.number_format = '#,##0' if key != "gr_qty" else '#,##0.##'
        c.border = border()

ws2.row_dimensions[total_dr2].height = 22
ws2.auto_filter.ref = f"A3:{get_column_letter(NC2)}3"

# ────────────────────────────────────────────────────────────────────────────
# SHEET 3 — Non-Standard Accounts
# ────────────────────────────────────────────────────────────────────────────
ws3 = wb.create_sheet("3. Akun Non-Std")
ws3.sheet_view.showGridLines = False
ws3.sheet_properties.tabColor = GRN_HDR
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

write_banner(ws3, f"AKUN NON-STANDAR — {company_name}  |  {generated_at}", NC3, GRN_HDR)

# info row 2
ws3.merge_cells(start_row=2, start_column=1, end_row=2, end_column=NC3)
ir = ws3.cell(row=2, column=1, value="  Akun di luar 2102002 / 2103006 / 1105003 — termasuk HPP/COGS variance dan akun koreksi lainnya")
ir.font = Font(name="Calibri", italic=True, size=9, color="375623")
ir.fill = solid("E8F5E9")
ir.alignment = Alignment(vertical="center")
ws3.row_dimensions[2].height = 18

for ci, (key, label, w) in enumerate(COLS3, start=1):
    write_col_header(ws3, 3, ci, label, fill=GRN_HDR, width=w)
ws3.row_dimensions[3].height = 24

MONEY3 = {"gr_value","debit","credit","net_balance"}
for ri, row in enumerate(acc_nonst):
    dr = ri + 4
    status     = row["cycle_status"]
    status_raw = status.split(" ")[0]
    base_f     = solid(STATUS_FILL.get(status_raw, WHITE))
    for ci, (key, label, w) in enumerate(COLS3, start=1):
        val = row.get(key, "")
        if key == "status":
            status_cell(ws3, dr, ci, val, base_f)
            continue
        c = ws3.cell(row=dr, column=ci, value=val)
        c.font = Font(name="Calibri", size=9)
        c.border = border()
        if key in MONEY3:
            c.fill = base_f
            c.number_format = '#,##0'
            c.alignment = Alignment(horizontal="right", vertical="center")
            if key == "net_balance":
                nv = float(val) if val else 0
                c.font = Font(name="Calibri", size=9,
                              color=("C00000" if nv < 0 else ("1A5276" if nv > 0 else "555555")))
        elif key == "cycle_status":
            c.fill = solid(STATUS_FILL.get(str(val).lower(), WHITE))
            c.font = Font(name="Calibri", size=9, bold=True,
                          color=STATUS_TXT.get(str(val).lower(), "000000"))
            c.alignment = Alignment(horizontal="center", vertical="center")
        elif key == "gr_qty":
            c.fill = base_f
            c.number_format = '#,##0.##'
            c.alignment = Alignment(horizontal="center", vertical="center")
        elif key == "gr_date":
            c.fill = base_f
            c.alignment = Alignment(horizontal="center", vertical="center")
        else:
            c.fill = base_f
            c.alignment = Alignment(vertical="center",
                                    wrap_text=(key in {"akun_name","product_name"}))
        if ri % 2 == 1:
            # alternate shade within same-status
            pass  # base_f already set per cycle status; keep as-is
    ws3.row_dimensions[dr].height = 16

ws3.auto_filter.ref = f"A3:{get_column_letter(NC3)}3"

# ────────────────────────────────────────────────────────────────────────────
# SHEET 4 — Raw Ledger
# ────────────────────────────────────────────────────────────────────────────
ws4 = wb.create_sheet("4. Raw Ledger")
ws4.sheet_view.showGridLines = False
ws4.sheet_properties.tabColor = "4A235A"
ws4.freeze_panes = "A3"

COLS4 = [
    ("picking_name",   "Picking",         18),
    ("cycle_status",   "Cycle Status",    12),
    ("kode_transaksi", "Kode Transaksi",  24),
    ("tanggal",        "Tanggal",         12),
    ("jenis",          "Jenis",            8),
    ("akun_code",      "Kode Akun",       12),
    ("akun_name",      "Nama Akun",       44),
    ("kode_item",      "Kode Item",       16),
    ("nama_item",      "Nama Item",       28),
    ("uom",            "UOM",             16),
    ("qty_item",       "Qty",              9),
    ("kategori_produk","Kategori",        18),
    ("no_po",          "No PO",           20),
    ("partner",        "Partner",         20),
    ("debit",          "Debit",           16),
    ("kredit",         "Kredit",          16),
    ("saldo",          "Saldo",           16),
    ("komunikasi",     "Komunikasi",      36),
    ("matching",       "Matching",        20),
]
NC4 = len(COLS4)

write_banner(ws4, f"RAW LEDGER — {company_name}  |  {generated_at}", NC4, "4A235A")

for ci, (key, label, w) in enumerate(COLS4, start=1):
    write_col_header(ws4, 2, ci, label, fill="4A235A", width=w)
ws4.row_dimensions[2].height = 24

MONEY4 = {"debit","kredit","saldo"}
JENIS_COLOR = {"STJ": "1A5276", "BILL": "7B241C", "PAYMENT": "186A3B", "BK": "4A235A", "PBK": "4A235A"}

prev_trx = None
shade4 = True
for ri, rl in enumerate(raw_all):
    dr = ri + 3
    if rl["kode_transaksi"] != prev_trx:
        shade4 = not shade4
        prev_trx = rl["kode_transaksi"]
    base_f       = solid("EDE7F6" if shade4 else WHITE)
    cycle_status = rl.get("cycle_status","")
    cycle_status_raw = cycle_status.split(" ")[0]

    for ci, (key, label, w) in enumerate(COLS4, start=1):
        val = rl.get(key, "")
        c = ws4.cell(row=dr, column=ci, value=val)
        c.font = Font(name="Calibri", size=9)
        c.border = border()
        if key in MONEY4:
            c.fill = base_f
            c.number_format = '#,##0'
            c.alignment = Alignment(horizontal="right", vertical="center")
            if key == "saldo":
                nv = float(val) if val else 0
                c.font = Font(name="Calibri", size=9,
                              color=("C00000" if nv < 0 else ("1A5276" if nv > 0 else "555555")))
        elif key == "jenis":
            jenis = str(val).upper()
            c.fill = base_f
            c.font = Font(name="Calibri", size=9, bold=True,
                          color=JENIS_COLOR.get(jenis, "000000"))
            c.alignment = Alignment(horizontal="center", vertical="center")
        elif key == "cycle_status":
            c.fill = solid(STATUS_FILL.get(cycle_status_raw.lower(), base_f.fgColor.rgb if hasattr(base_f.fgColor,'rgb') else WHITE))
            c.font = Font(name="Calibri", size=9, bold=True,
                          color=STATUS_TXT.get(cycle_status_raw.lower(), "000000"))
            c.alignment = Alignment(horizontal="center", vertical="center")
        elif key == "qty_item":
            c.fill = base_f
            c.number_format = '#,##0.##'
            c.alignment = Alignment(horizontal="center", vertical="center")
        elif key == "tanggal":
            c.fill = base_f
            c.alignment = Alignment(horizontal="center", vertical="center")
        else:
            c.fill = base_f
            c.alignment = Alignment(vertical="center",
                                    wrap_text=(key in {"akun_name","nama_item","komunikasi"}))

    ws4.row_dimensions[dr].height = 16

ws4.auto_filter.ref = f"A2:{get_column_letter(NC4)}2"

# ── save ──────────────────────────────────────────────────────────────────────
out_name = f"PCB_Audit_{company_name.replace(' ','_')}_{generated_at}.xlsx"
out_path = script_dir / out_name
wb.save(str(out_path))
print(f"[OK] Saved: {out_path}")
print(f"     Sheet 1: {len(cycles)} cycle rows")
print(f"     Sheet 2: {len(item_flat)} item rows")
print(f"     Sheet 3: {len(acc_nonst)} non-std account rows")
print(f"     Sheet 4: {len(raw_all)} raw ledger rows")
