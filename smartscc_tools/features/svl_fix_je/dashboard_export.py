"""Export helpers for dashboard snapshot HTML and JSON."""

from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path

from smartscc_tools.core import theme as T

from .models import SvlDashboardSnapshot


def snapshot_to_mapping(snapshot: SvlDashboardSnapshot) -> dict[str, object]:
    payload = asdict(snapshot)
    payload["account_count"] = snapshot.account_count
    return payload


def export_dashboard_json(snapshot: SvlDashboardSnapshot, output_path: str) -> Path:
    path = Path(output_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(snapshot_to_mapping(snapshot), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def export_dashboard_html(snapshot: SvlDashboardSnapshot, output_path: str) -> Path:
    path = Path(output_path).expanduser().resolve()
    payload = json.dumps(snapshot_to_mapping(snapshot), ensure_ascii=False)
    html = _build_html(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    return path


def _build_html(payload: str) -> str:
    template = """<!DOCTYPE html>
<html lang="id">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Fixing Unlink SVL Dashboard</title>
<style>
:root {{
  --brand: __BRAND__;
  --accent: __ACCENT__;
  --bg: __BG__;
  --card: __CARD__;
  --border: __BORDER__;
  --text: __TEXT__;
  --muted: __MUTED__;
  --danger: __DANGER__;
  --success: __SUCCESS__;
  --warning: __WARNING__;
}}
* {{ box-sizing: border-box; }}
body {{ margin: 0; font-family: Segoe UI, Arial, sans-serif; background: var(--bg); color: var(--text); }}
.app {{ display: grid; grid-template-columns: 360px 1fr; grid-template-rows: auto 1fr; min-height: 100vh; }}
.topbar {{ grid-column: 1 / -1; background: var(--brand); color: #fff; padding: 14px 20px; display: flex; justify-content: space-between; gap: 12px; align-items: center; }}
.topbar h1 {{ margin: 0; font-size: 18px; }}
.meta {{ display: flex; flex-wrap: wrap; gap: 8px; font-size: 12px; }}
.pill {{ padding: 4px 10px; border-radius: 999px; background: rgba(255,255,255,.12); }}
.sidebar {{ border-right: 1px solid var(--border); background: #fff; display: flex; flex-direction: column; min-height: 0; }}
.sidebar-head {{ padding: 16px; border-bottom: 1px solid var(--border); }}
.search {{ width: 100%; padding: 10px 12px; border: 1px solid var(--border); border-radius: 8px; }}
.list {{ padding: 8px; overflow: auto; min-height: 0; }}
.group {{ border: 1px solid var(--border); border-radius: 10px; background: #fff; margin-bottom: 10px; overflow: hidden; }}
.group > summary {{ list-style: none; cursor: pointer; padding: 10px 12px; font-size: 12px; font-weight: 700; color: var(--text); display: flex; justify-content: space-between; gap: 8px; }}
.group > summary::-webkit-details-marker {{ display: none; }}
.group-body {{ padding: 0 8px 8px; }}
.item {{ border: 1px solid var(--border); border-radius: 10px; padding: 12px; margin-top: 8px; cursor: pointer; background: #fff; }}
.item.active {{ border-color: var(--brand); box-shadow: inset 3px 0 0 var(--brand); }}
.code {{ color: var(--brand); font-size: 12px; font-weight: 600; }}
.name {{ margin-top: 2px; font-size: 13px; font-weight: 600; }}
.row {{ display: flex; justify-content: space-between; align-items: center; gap: 8px; margin-top: 8px; }}
.badges {{ display: flex; gap: 4px; flex-wrap: wrap; }}
.badge {{ padding: 2px 6px; border-radius: 999px; font-size: 10px; font-weight: 600; }}
.badge.red {{ background: rgba(229,72,62,.12); color: var(--danger); }}
.badge.green {{ background: rgba(33,181,115,.12); color: var(--success); }}
.badge.yellow {{ background: rgba(240,173,78,.15); color: #946200; }}
.main {{ padding: 20px; overflow: auto; min-height: 0; }}
.warning-box {{ background: #fff3cd; color: #7a5a00; border: 1px solid #f6d47a; padding: 10px 12px; border-radius: 10px; margin-bottom: 16px; }}
.card {{ background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 16px; margin-bottom: 16px; }}
.header-grid {{ display: flex; justify-content: space-between; gap: 12px; align-items: start; }}
.meta-row {{ display: flex; flex-wrap: wrap; gap: 16px; margin-top: 8px; color: var(--muted); font-size: 13px; }}
.kpi-grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 16px; }}
.kpi {{ background: #fff; border: 1px solid var(--border); border-top: 3px solid var(--brand); border-radius: 10px; padding: 14px; }}
.kpi:nth-child(2) {{ border-top-color: var(--success); }}
.kpi:nth-child(3) {{ border-top-color: var(--warning); }}
.kpi:nth-child(4) {{ border-top-color: var(--danger); }}
.kpi-label {{ font-size: 11px; color: var(--muted); text-transform: uppercase; }}
.kpi-value {{ margin-top: 6px; font-size: 22px; font-weight: 700; }}
.kpi-subrow {{ margin-top: 8px; display: flex; gap: 6px; align-items: baseline; color: var(--muted); }}
.kpi-subvalue {{ font-size: 13px; font-weight: 700; color: var(--text); }}
.tabs {{ display: flex; gap: 8px; margin-bottom: 16px; flex-wrap: wrap; }}
.tab {{ padding: 10px 12px; border-radius: 8px; border: 1px solid var(--border); background: #fff; cursor: pointer; font-size: 13px; }}
.tab.active {{ background: var(--brand); border-color: var(--brand); color: #fff; }}
.split {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
.placeholder {{ background: #f5f6f8; color: var(--muted); border: 1px dashed var(--border); border-radius: 10px; padding: 14px; }}
table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
th, td {{ padding: 8px 10px; border-bottom: 1px solid var(--border); text-align: left; vertical-align: top; }}
th {{ background: #f5f6f8; font-size: 11px; text-transform: uppercase; color: var(--muted); }}
td.r, th.r {{ text-align: right; }}
tr.svl-orphan {{ background: rgba(229,72,62,.08); }}
tr.jnl-orphan {{ background: rgba(33,181,115,.08); }}
.analysis-row {{ display: grid; grid-template-columns: 220px 1fr 140px; gap: 12px; align-items: center; margin-bottom: 14px; }}
.bar {{ background: #eef0f3; height: 16px; border-radius: 999px; overflow: hidden; }}
.fill {{ height: 100%; border-radius: 999px; }}
.empty {{ display: flex; align-items: center; justify-content: center; height: 100%; color: var(--muted); }}
@media (max-width: 980px) {{
  .app {{ grid-template-columns: 1fr; grid-template-rows: auto auto 1fr; }}
  .kpi-grid, .split {{ grid-template-columns: 1fr; }}
}}
</style>
</head>
<body>
<div id="app"></div>
<script>
const DATA = __PAYLOAD__;
const CURRENT_ASSET_MODE = "account_balance";
const ISSUE_MODE = "issues";
const IS_CURRENT_ASSET = (DATA.dataset_mode || ISSUE_MODE) === CURRENT_ASSET_MODE;
let searchTerm = "";
let selectedPid = DATA.items.length ? DATA.items[0].pid : 0;
let activeTab = IS_CURRENT_ASSET ? "current_asset" : "svl";

function num(v, digits = 2) {{
  const n = Number(v || 0);
  return n.toLocaleString("id-ID", {{ minimumFractionDigits: digits, maximumFractionDigits: digits }});
}}

function count(v) {{
  const n = Number(v || 0);
  return n.toLocaleString("id-ID", {{ minimumFractionDigits: 0, maximumFractionDigits: 0 }});
}}

function primaryAmount(item) {{
  return IS_CURRENT_ASSET ? Number(item.account_balance_balance || item.current_asset_balance || 0) : Number(item.difference || 0);
}}

function itemPosition(item) {{
  return primaryAmount(item) >= 0 ? "DEBIT" : "KREDIT";
}}

function accountCountLabel() {{
  return IS_CURRENT_ASSET ? "akun bersaldo" : "akun valuasi";
}}

function datasetLabel() {{
  return IS_CURRENT_ASSET ? "Saldo Akun per Item" : "SVL vs Balance Sheet";
}}

function filteredItems() {{
  const q = searchTerm.trim().toLowerCase();
  if (!q) return DATA.items;
  return DATA.items.filter(item => (item.code || "").toLowerCase().includes(q) || (item.name || "").toLowerCase().includes(q));
}}

function itemCategory(item) {{
  if ((item.item_kind || "") === "unassigned_journal") return "System / Journal Tanpa Product";
  return item.categ || "Tanpa Kategori";
}}

function groupedItems(items) {{
  if (IS_CURRENT_ASSET) {{
    const grouped = new Map();
    items.forEach(item => {{
      const accountRows = item.account_balance_account_rows || item.current_asset_account_rows || [];
      accountRows.forEach(row => {{
        const balance = Number(row.balance || 0);
        if (Math.abs(balance) < 0.01) return;
        const accountTitle = [row.code || "", row.name || ""].filter(Boolean).join(" - ") || "Tanpa Akun";
        const position = balance >= 0 ? "DEBIT" : "KREDIT";
        const category = itemCategory(item);
        if (!grouped.has(accountTitle)) grouped.set(accountTitle, new Map());
        const positionMap = grouped.get(accountTitle);
        if (!positionMap.has(position)) positionMap.set(position, new Map());
        const categoryMap = positionMap.get(position);
        if (!categoryMap.has(category)) categoryMap.set(category, []);
        categoryMap.get(category).push({{
          pid: item.pid,
          item,
          amount: balance,
        }});
      }});
    }});
    return [...grouped.entries()]
      .map(([accountTitle, positions]) => {{
        const positionGroups = [...positions.entries()]
          .map(([positionTitle, categories]) => {{
            const categoryGroups = [...categories.entries()]
              .map(([title, rows]) => {{
                const sortedRows = [...rows].sort((a, b) => Math.abs(Number(b.amount || 0)) - Math.abs(Number(a.amount || 0)));
                return {{
                  title,
                  items: sortedRows,
                  children: [],
                  total: sortedRows.reduce((sum, row) => sum + Number(row.amount || 0), 0),
                  itemCount: sortedRows.length,
                }};
              }})
              .sort((a, b) => Math.abs(b.total) - Math.abs(a.total) || a.title.localeCompare(b.title));
            return {{
              title: positionTitle,
              items: [],
              children: categoryGroups,
              total: categoryGroups.reduce((sum, group) => sum + Number(group.total || 0), 0),
              itemCount: categoryGroups.reduce((sum, group) => sum + Number(group.itemCount || 0), 0),
            }};
          }})
          .sort((a, b) => {{
            const order = {{ DEBIT: 0, KREDIT: 1 }};
            return (order[a.title] ?? 99) - (order[b.title] ?? 99) || Math.abs(b.total) - Math.abs(a.total);
          }});
        return {{
          title: accountTitle,
          items: [],
          children: positionGroups,
          total: positionGroups.reduce((sum, group) => sum + Number(group.total || 0), 0),
          itemCount: positionGroups.reduce((sum, group) => sum + Number(group.itemCount || 0), 0),
        }};
      }})
      .sort((a, b) => Math.abs(b.total) - Math.abs(a.total) || a.title.localeCompare(b.title));
  }}
  const grouped = new Map();
  items.forEach(item => {{
    const title = itemCategory(item);
    if (!grouped.has(title)) grouped.set(title, []);
    grouped.get(title).push(item);
  }});
  return [...grouped.entries()]
    .map(([title, rows]) => {{
      const sortedRows = [...rows].sort((a, b) => Math.abs(primaryAmount(b)) - Math.abs(primaryAmount(a)));
      return {{
        title,
        items: [],
        children: [{{
          title,
          items: sortedRows.map(item => ({{
            pid: item.pid,
            item,
            amount: primaryAmount(item),
          }})),
          children: [],
          total: sortedRows.reduce((sum, row) => sum + primaryAmount(row), 0),
          itemCount: sortedRows.length,
        }}],
        total: sortedRows.reduce((sum, row) => sum + primaryAmount(row), 0),
        itemCount: sortedRows.length,
      }};
    }})
    .sort((a, b) => Math.abs(b.total) - Math.abs(a.total) || a.title.localeCompare(b.title));
}}

function renderLeaf(entry) {{
  const item = entry.item || entry;
  const amount = Number(entry.amount != null ? entry.amount : primaryAmount(item));
  return `
    <div class="item ${item.pid === selectedPid ? "active" : ""}" onclick="selectItem(${item.pid})">
      <div class="code">${item.code || "-"}</div>
      <div class="name">${item.name}</div>
      <div class="row">
        <div>${num(amount)}</div>
        <div class="badges">
          ${IS_CURRENT_ASSET
            ? `${Number(item.po_line_count || 0) ? `<span class="badge yellow">${count(item.po_line_count || 0)} PO</span>` : ""}${Number(item.payable_line_count || 0) ? `<span class="badge green">${count(item.payable_line_count || 0)} AP</span>` : ""}`
            : `${Number(item.svl_orphan_count || 0) ? `<span class="badge red">${count(item.svl_orphan_count || 0)} SVL</span>` : ""}${Number(item.jnl_orphan_count || 0) ? `<span class="badge green">${count(item.jnl_orphan_count || 0)} JNL</span>` : ""}${Number(item.po_line_count || 0) ? `<span class="badge yellow">${count(item.po_line_count || 0)} PO</span>` : ""}`
          }
        </div>
      </div>
    </div>`;
}}

function renderSidebarGroup(group) {{
  const children = group.children || [];
  const items = group.items || [];
  return `
    <details class="group" open>
      <summary><span>${group.title}</span><span>${count(group.itemCount || 0)} item | Total ${num(group.total || 0)}</span></summary>
      <div class="group-body">
        ${children.map(child => renderSidebarGroup(child)).join("")}
        ${items.map(entry => renderLeaf(entry)).join("")}
      </div>
    </details>`;
}}

function selectedItem() {{
  const items = filteredItems();
  return items.find(item => item.pid === selectedPid) || items[0] || null;
}}

function switchTab(tab) {{
  activeTab = tab;
  render();
}}

function selectItem(pid) {{
  selectedPid = pid;
  activeTab = IS_CURRENT_ASSET ? "current_asset" : "svl";
  render();
}}

function onSearch(value) {{
  searchTerm = value;
  const items = filteredItems();
  if (!items.some(item => item.pid === selectedPid)) {{
    selectedPid = items.length ? items[0].pid : 0;
  }}
  render();
}}

function renderCompanySummary() {{
  const summary = DATA.company_summary || {{}};
  if (IS_CURRENT_ASSET) {{
    const accountRows = summary.account_balance_rows || [];
    return `
      <div class="card">
        <h3 style="margin:0 0 12px;">Ringkasan Company</h3>
        <div class="kpi-grid">
          <div class="kpi">
            <div class="kpi-label">Total Debit Akun</div>
            <div class="kpi-value">${num(summary.account_balance_total_debit || 0)}</div>
            <div class="kpi-subrow"><span>Item:</span><span class="kpi-subvalue">${count(summary.account_balance_item_count || 0)}</span></div>
          </div>
          <div class="kpi"><div class="kpi-label">Total Kredit Akun</div><div class="kpi-value">${num(summary.account_balance_total_credit || 0)}</div></div>
          <div class="kpi"><div class="kpi-label">Net Saldo Akun</div><div class="kpi-value">${num(summary.account_balance_total_balance || 0)}</div></div>
          <div class="kpi"><div class="kpi-label">Akun Bersaldo</div><div class="kpi-value">${count(accountRows.length)}</div></div>
        </div>
        <div class="meta-row" style="margin-top:0;">
          <span>Source DB: <b>${DATA.database || "-"}</b></span>
          <span>Company: <b>${DATA.company_name} (#${DATA.company_id})</b></span>
          <span>Dataset: <b>Saldo Akun per Item</b></span>
        </div>
        <table style="margin-top:12px;">
          <thead><tr><th>Code</th><th>Akun</th><th class="r">Debit</th><th class="r">Kredit</th><th class="r">Saldo</th></tr></thead>
          <tbody>
            ${accountRows.map(row => `<tr><td>${row.code || "-"}</td><td>${row.name || "-"}</td><td class="r">${num(row.debit || 0)}</td><td class="r">${num(row.credit || 0)}</td><td class="r">${num(row.balance || 0)}</td></tr>`).join("") || `<tr><td colspan="5">Tidak ada detail akun company.</td></tr>`}
          </tbody>
        </table>
        <div class="meta-row"><span>Net Saldo Akun: <b>${num(summary.account_balance_total_balance || 0)}</b></span></div>
      </div>
    `;
  }}
  const coaRows = summary.coa_rows || [];
  const missingCodes = summary.missing_codes || [];
  return `
    <div class="card">
      <h3 style="margin:0 0 12px;">Ringkasan Company</h3>
      <div class="kpi-grid">
        <div class="kpi">
          <div class="kpi-label">Total SVL</div>
          <div class="kpi-value">${num(summary.total_svl_value || 0)}</div>
          <div class="kpi-subrow"><span>Qty:</span><span class="kpi-subvalue">${num(summary.total_svl_qty || 0)}</span></div>
        </div>
        <div class="kpi"><div class="kpi-label">Total BS Persediaan</div><div class="kpi-value">${num(summary.inventory_bs_total || 0)}</div></div>
        <div class="kpi"><div class="kpi-label">Selisih Total</div><div class="kpi-value">${num(summary.difference || 0)}</div></div>
        <div class="kpi"><div class="kpi-label">Belum Terpetakan</div><div class="kpi-value">${num(summary.unmapped_difference || 0)}</div></div>
      </div>
      <div class="meta-row" style="margin-top:0;">
        <span>Source DB: <b>${DATA.database || "-"}</b></span>
        <span>Company: <b>${DATA.company_name} (#${DATA.company_id})</b></span>
        ${missingCodes.length ? `<span>Missing COA: <b>${missingCodes.join(", ")}</b></span>` : ""}
      </div>
      <table style="margin-top:12px;">
        <thead><tr><th>Code</th><th>COA Name</th><th class="r">Balance</th></tr></thead>
        <tbody>
          ${coaRows.map(row => `<tr><td>${row.code}</td><td>${row.name || "-"}</td><td class="r">${num(row.balance || 0)}</td></tr>`).join("") || `<tr><td colspan="3">Tidak ada detail COA persediaan.</td></tr>`}
        </tbody>
      </table>
      <div class="meta-row"><span>Total Balance Persediaan: <b>${num(summary.inventory_bs_total || 0)}</b></span></div>
    </div>
  `;
}}

function renderIssueDetail(item) {{
  const isSyntheticJournal = item.item_kind === "unassigned_journal";
  const diffPoBill = (item.total_po_value || 0) - (item.total_bill_value || 0);
  const svlOrphan = item.svl_orphan_value || 0;
  const jnlOrphan = item.jnl_orphan_value || 0;
  const otherDiff = (item.difference || 0) - svlOrphan + jnlOrphan;
  const maxBar = Math.max(Math.abs(svlOrphan), Math.abs(jnlOrphan), Math.abs(otherDiff), 1);
  const bar = (value, label, color) => `<div class="analysis-row"><div>${label}</div><div class="bar"><div class="fill" style="width:${Math.min(100, Math.abs(value) / maxBar * 100)}%;background:${color}"></div></div><div>${num(value)}</div></div>`;
  const tabs = `
    <div class="tabs">
      <button class="tab ${activeTab === "svl" ? "active" : ""}" onclick="switchTab('svl')">SVL Detail</button>
      <button class="tab ${activeTab === "jnl" ? "active" : ""}" onclick="switchTab('jnl')">Journal Detail</button>
      <button class="tab ${activeTab === "compare" ? "active" : ""}" onclick="switchTab('compare')">PO vs Bill</button>
      <button class="tab ${activeTab === "analysis" ? "active" : ""}" onclick="switchTab('analysis')">Analisis Selisih</button>
    </div>`;
  let body = "";
  if (activeTab === "svl") {{
    if (isSyntheticJournal) {{
      body = `<div class="placeholder">Item ini berasal dari journal valuation tanpa <code>product_id</code>; tab SVL tidak berlaku.</div>`;
    }} else {{
      const rows = (item.svl_records || []).map(row => `
        <tr class="${row.has_journal ? "" : "svl-orphan"}">
          <td>${row.record_id}</td><td>${row.date || "-"}</td><td class="r">${num(row.quantity)}</td><td class="r">${num(row.unit_cost)}</td><td class="r">${num(row.value)}</td><td>${row.reference || "-"}</td><td>${row.has_journal ? (row.journal_ref || "Ada") : "TIDAK ADA"}</td>
        </tr>`).join("");
      body = `<div class="card"><table><thead><tr><th>ID</th><th>Tanggal</th><th class="r">Qty</th><th class="r">Unit Cost</th><th class="r">Value</th><th>Ref</th><th>Journal</th></tr></thead><tbody>${rows || `<tr><td colspan="7">Tidak ada detail SVL.</td></tr>`}</tbody></table></div>`;
    }}
  }} else if (activeTab === "jnl") {{
    const rows = (item.jnl_records || []).map(row => `
      <tr class="${row.has_svl ? "" : "jnl-orphan"}">
        <td>${row.journal_entry || "-"}</td><td>${row.date || "-"}</td><td class="r">${num(row.debit)}</td><td class="r">${num(row.credit)}</td><td class="r">${num(row.net)}</td><td>${row.has_svl ? "ADA" : "TIDAK ADA"}</td><td>${row.reference || "-"}</td>
      </tr>`).join("");
    body = `<div class="card"><table><thead><tr><th>Journal</th><th>Tanggal</th><th class="r">Debit</th><th class="r">Credit</th><th class="r">Net</th><th>SVL</th><th>Referensi</th></tr></thead><tbody>${rows || `<tr><td colspan="7">Tidak ada detail journal.</td></tr>`}</tbody></table></div>`;
  }} else if (activeTab === "compare") {{
    if (isSyntheticJournal) {{
      body = `<div class="placeholder">Item ini berasal dari journal valuation tanpa <code>product_id</code>; tab PO vs Bill tidak berlaku.</div>`;
    }} else {{
      const poRows = (item.po_lines || []).map(row => `
        <tr><td>${row.po}</td><td class="r">${num(row.price_unit)}</td><td class="r">${num(row.qty_received)}</td><td class="r">${num(row.qty_invoiced)}</td><td class="r">${num(row.value)}</td><td>${row.status}</td></tr>`).join("");
      const billRows = (item.bill_lines || []).map(row => `
        <tr><td>${row.bill}</td><td>${row.po}</td><td>${row.date || "-"}</td><td class="r">${num(row.price_unit)}</td><td class="r">${num(row.quantity)}</td><td class="r">${num(row.value)}</td></tr>`).join("");
      body = `<div class="card"><div class="split">
        <div><h3>Purchase Orders</h3><table><thead><tr><th>PO</th><th class="r">Price</th><th class="r">Qty Rcvd</th><th class="r">Qty Inv</th><th class="r">Value</th><th>Status</th></tr></thead><tbody>${poRows || `<tr><td colspan="6">Tidak ada data PO.</td></tr>`}</tbody></table></div>
        <div><h3>Vendor Bills</h3><table><thead><tr><th>Bill</th><th>PO</th><th>Tanggal</th><th class="r">Price</th><th class="r">Qty</th><th class="r">Value</th></tr></thead><tbody>${billRows || `<tr><td colspan="6">Tidak ada data Bill.</td></tr>`}</tbody></table></div>
      </div></div>`;
    }}
  }} else {{
    if (isSyntheticJournal) {{
      body = `<div class="placeholder">Item ini berasal dari journal valuation tanpa <code>product_id</code>; analisis selisih produk tidak berlaku.</div>`;
    }} else {{
      body = `<div class="card">${bar(svlOrphan, "SVL tanpa Journal", "__DANGER__")}${bar(jnlOrphan, "Journal tanpa SVL", "__SUCCESS__")}${bar(otherDiff, "Selisih lain", "__WARNING__")}<div class="meta-row"><span>Total PO: <b>${num(item.total_po_value || 0)}</b></span><span>Total Bill: <b>${num(item.total_bill_value || 0)}</b></span><span>PO - Bill: <b>${num(diffPoBill)}</b></span></div></div>`;
    }}
  }}
  return `
    <div class="card">
      <div class="header-grid">
        <div>
          <div class="code">${item.code || "NO CODE"}</div>
          <h2 style="margin:4px 0 0">${item.name}</h2>
        </div>
        <div class="badge ${itemPosition(item) === "DEBIT" ? "green" : "red"}">${itemPosition(item)}</div>
      </div>
      <div class="meta-row">
        ${isSyntheticJournal
          ? `<span>Jenis: <b>PSEUDO PRODUCT</b></span><span>Sumber: <b>Journal valuation tanpa product_id</b></span>`
          : `<span>Kategori: <b>${item.categ || "-"}</b></span><span>Cost Method: <b>${(item.cost_method || "-").toUpperCase()}</b></span><span>Standard Price: <b>${num(item.standard_price || 0)}</b></span>`}
      </div>
    </div>
    <div class="kpi-grid">
      <div class="kpi"><div class="kpi-label">Nilai SVL</div><div class="kpi-value">${num(item.svl_value || 0)}</div></div>
      <div class="kpi"><div class="kpi-label">Saldo BS</div><div class="kpi-value">${num(item.bs_value || 0)}</div></div>
      <div class="kpi"><div class="kpi-label">Selisih</div><div class="kpi-value">${num(item.difference || 0)}</div></div>
      <div class="kpi"><div class="kpi-label">PO - Bill</div><div class="kpi-value">${num(diffPoBill)}</div></div>
    </div>
    ${tabs}
    ${body}
  `;
}}

function renderCurrentAssetDetail(item) {{
  const diffPoBill = Number(item.total_po_value || 0) - Number(item.total_bill_value || 0);
  const tabs = `
    <div class="tabs">
      <button class="tab ${activeTab === "current_asset" ? "active" : ""}" onclick="switchTab('current_asset')">Saldo Akun per Item</button>
      <button class="tab ${activeTab === "compare" ? "active" : ""}" onclick="switchTab('compare')">PO vs Bill</button>
    </div>`;
  let body = "";
  if (activeTab === "current_asset") {{
    const assetRows = (item.account_balance_move_rows || []).map(row => `
      <tr><td>${row.date || "-"}</td><td>${row.transaction_no || "-"}</td><td>${row.journal_source || "-"}</td><td>${row.gr_reference || "-"}</td><td>${row.po || "-"}</td><td>${row.partner || row.reference || "-"}</td><td class="r">${num(row.debit || 0)}</td><td class="r">${num(row.credit || 0)}</td><td class="r">${num(row.balance || 0)}</td><td>${row.matching || "-"}</td></tr>`).join("");
    const cycleRows = (item.account_balance_cycle_rows || []).map(row => `
      <tr><td>${row.gr_reference || "-"}</td><td>${row.gr_date || "-"}</td><td>${row.po || "-"}</td><td>${row.bill || "-"}</td><td>${row.payment || "-"}</td><td>${row.bank_move || "-"}</td><td>${row.status || "-"}</td><td>${row.source || "-"}</td></tr>`).join("");
    body = `<div class="card"><div class="meta-row" style="margin-top:0;"><span>Net Saldo Akun: <b>${num(item.account_balance_balance || item.current_asset_balance || 0)}</b></span><span>PO: <b>${num(item.total_po_value || 0)}</b></span><span>Bill: <b>${num(item.total_bill_value || 0)}</b></span><span>Payable: <b>${num(item.total_payable_value || 0)}</b></span><span>Bill Remainder: <b>${num(item.total_unassigned_bill_remainder || 0)}</b></span></div><div class="split" style="margin-top:12px;">
      <div><h3>Account Move</h3><table><thead><tr><th>Tanggal</th><th>Nomor Transaksi</th><th>Journal/Source</th><th>GR</th><th>PO</th><th>Partner/Reference</th><th class="r">Debit</th><th class="r">Kredit</th><th class="r">Saldo</th><th>Matching</th></tr></thead><tbody>${assetRows || `<tr><td colspan="10">Tidak ada account.move cycle.</td></tr>`}</tbody></table></div>
      <div><h3>Cycle Link</h3><table><thead><tr><th>GR</th><th>Tanggal GR</th><th>PO</th><th>Bill</th><th>Payment</th><th>Bank</th><th>Status</th><th>Source</th></tr></thead><tbody>${cycleRows || `<tr><td colspan="8">Tidak ada cycle link.</td></tr>`}</tbody></table></div>
    </div></div>`;
  }} else if (activeTab === "compare") {{
    const poRows = (item.po_lines || []).map(row => `
      <tr><td>${row.po}</td><td class="r">${num(row.price_unit)}</td><td class="r">${num(row.qty_received)}</td><td class="r">${num(row.qty_invoiced)}</td><td class="r">${num(row.value)}</td><td>${row.status}</td></tr>`).join("");
    const billRows = (item.bill_lines || []).map(row => `
      <tr><td>${row.bill}</td><td>${row.po}</td><td>${row.date || "-"}</td><td class="r">${num(row.price_unit)}</td><td class="r">${num(row.quantity)}</td><td class="r">${num(row.value)}</td></tr>`).join("");
    body = `<div class="card"><div class="meta-row" style="margin-top:0;"><span>PO: <b>${num(item.total_po_value || 0)}</b></span><span>Bill: <b>${num(item.total_bill_value || 0)}</b></span><span>PO - Bill: <b>${num(diffPoBill)}</b></span></div><div class="split" style="margin-top:12px;">
      <div><h3>Purchase Orders</h3><table><thead><tr><th>PO</th><th class="r">Price</th><th class="r">Qty Rcvd</th><th class="r">Qty Inv</th><th class="r">Value</th><th>Status</th></tr></thead><tbody>${poRows || `<tr><td colspan="6">Tidak ada data PO.</td></tr>`}</tbody></table></div>
      <div><h3>Vendor Bills</h3><table><thead><tr><th>Bill</th><th>PO</th><th>Tanggal</th><th class="r">Price</th><th class="r">Qty</th><th class="r">Value</th></tr></thead><tbody>${billRows || `<tr><td colspan="6">Tidak ada data Bill.</td></tr>`}</tbody></table></div>
    </div></div>`;
  }}
  return `
    <div class="card">
      <div class="header-grid">
        <div>
          <div class="code">${item.code || "NO CODE"}</div>
          <h2 style="margin:4px 0 0">${item.name}</h2>
        </div>
        <div class="badge ${itemPosition(item) === "DEBIT" ? "green" : "red"}">${itemPosition(item)}</div>
      </div>
      <div class="meta-row">
        <span>Kategori: <b>${item.categ || "-"}</b></span>
        <span>Cost Method: <b>${(item.cost_method || "-").toUpperCase()}</b></span>
        <span>Standard Price: <b>${num(item.standard_price || 0)}</b></span>
      </div>
    </div>
    <div class="kpi-grid">
      <div class="kpi"><div class="kpi-label">Total Debit Akun</div><div class="kpi-value">${num(item.account_balance_debit || item.current_asset_debit || 0)}</div></div>
      <div class="kpi"><div class="kpi-label">Total Kredit Akun</div><div class="kpi-value">${num(item.account_balance_credit || item.current_asset_credit || 0)}</div></div>
      <div class="kpi"><div class="kpi-label">Net Saldo Akun</div><div class="kpi-value">${num(item.account_balance_balance || item.current_asset_balance || 0)}</div></div>
      <div class="kpi"><div class="kpi-label">Payable Dialokasikan</div><div class="kpi-value">${num(item.total_payable_value || 0)}</div></div>
    </div>
    ${tabs}
    ${body}
  `;
}}

function renderDetail(item) {{
  if (!item) {{
    return `<div class="empty">Tidak ada item untuk filter saat ini.</div>`;
  }}
  return `
    ${DATA.warnings.length ? `<div class="warning-box">${DATA.warnings.join("<br>")}</div>` : ""}
    ${IS_CURRENT_ASSET ? renderCurrentAssetDetail(item) : renderIssueDetail(item)}
  `;
}}

function renderSidebar(groups, items) {{
  return groups.map(group => renderSidebarGroup(group)).join("");
}}

function render() {{
  const items = filteredItems();
  const detail = selectedItem();
  const groups = groupedItems(items);
  const totalPrimary = items.reduce((sum, item) => sum + primaryAmount(item), 0);
  document.getElementById("app").innerHTML = `
    <div class="app">
      <div class="topbar">
        <h1>Dashboard Control - Fixing Unlink SVL</h1>
        <div class="meta">
          <span class="pill">${DATA.company_name} (#${DATA.company_id})</span>
          <span class="pill">${DATA.period}</span>
          <span class="pill">${datasetLabel()}</span>
          <span class="pill">${count(DATA.account_count || 0)} ${accountCountLabel()}</span>
          <span class="pill">${DATA.generated_at}</span>
        </div>
      </div>
      <aside class="sidebar">
        <div class="sidebar-head">
          <div style="font-size:12px;color:var(--muted);margin-bottom:8px;">${datasetLabel()}: ${count(items.length)} item | Total ${num(totalPrimary)}</div>
          <input class="search" value="${searchTerm}" placeholder="Cari kode / nama produk..." oninput="onSearch(this.value)">
        </div>
        <div class="list">
          ${renderSidebar(groups, items) || `<div class="empty">Tidak ada item untuk mode ini.</div>`}
        </div>
      </aside>
      <main class="main">${renderCompanySummary()}${renderDetail(detail)}</main>
    </div>
  `;
}}

render();
</script>
</body>
</html>"""
    return (
        template.replace("__PAYLOAD__", payload)
        .replace("__BRAND__", T.BRAND_PRIMARY)
        .replace("__ACCENT__", T.BRAND_SECONDARY)
        .replace("__BG__", T.BG_MAIN)
        .replace("__CARD__", T.BG_CARD)
        .replace("__BORDER__", T.BORDER_LIGHT)
        .replace("__TEXT__", T.TEXT_ON_LIGHT)
        .replace("__MUTED__", T.TEXT_MUTED)
        .replace("__DANGER__", T.STATUS_ERROR)
        .replace("__SUCCESS__", T.STATUS_SUCCESS)
        .replace("__WARNING__", T.STATUS_WARNING)
    )
