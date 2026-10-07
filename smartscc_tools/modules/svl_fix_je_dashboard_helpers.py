"""Lazy-loaded general/helper methods for SVL Fix JE dashboard page."""

from __future__ import annotations

from smartscc_tools.modules import svl_fix_je_dashboard_page as page_mod

def _sync_page_globals() -> None:
    for _name, _value in page_mod.__dict__.items():
        if _name.startswith("__"):
            continue
        globals()[_name] = _value


_sync_page_globals()


def _on_scrollable_canvas_configure(self, _event: tk.Event | None = None) -> None:
    if self._sync_height_after_id is not None:
        try:
            self.root.after_cancel(self._sync_height_after_id)
        except Exception:  # noqa: BLE001
            pass
    self._sync_height_after_id = self.root.after_idle(self._sync_content_height)


def _sync_content_height(self) -> None:
    self._sync_height_after_id = None
    if not hasattr(self, "content_pane"):
        return
    if self._is_purchase_cycle_mode():
        # In PCB mode, sidebar is compact at top; detail tables expand below it
        target_height = 420
    else:
        canvas_height = int(self._scrollable.canvas.winfo_height() or 0)
        target_height = max(self._content_min_height, canvas_height - 24)
    if self._last_content_height == target_height:
        return
    self._last_content_height = target_height
    self.content_pane.configure(height=target_height)


def _resolve_effective_database_state(self, *, selected_profile_id: str | None = None) -> dict[str, Any]:
    selected_id = normalize_database_profile_id(selected_profile_id or self._selected_database_profile_id())
    profiles = list(self.context.global_settings.database_profiles)
    selected_profile = find_database_profile(profiles, selected_id)
    default_profile = find_database_profile(profiles, self.context.global_settings.default_database_profile_id)

    selected_label = self._db_label_by_profile_id.get(selected_id, FOLLOW_GLOBAL_LABEL if selected_id == FOLLOW_GLOBAL_PROFILE_ID else selected_id)
    effective_profile = selected_profile
    effective_label = render_database_profile_label(selected_profile) if selected_profile is not None else normalize_text(selected_id)
    effective_database = normalize_text(selected_profile.database_value if selected_profile is not None else selected_id)

    if selected_id == FOLLOW_GLOBAL_PROFILE_ID:
        if default_profile is not None:
            effective_profile = default_profile
            effective_label = render_database_profile_label(default_profile)
            effective_database = normalize_text(default_profile.database_value)
        else:
            effective_profile = None
            effective_label = USE_GAS_DEFAULT_LABEL
            effective_database = ""

    alias_text = normalize_text(effective_profile.alias if effective_profile is not None else "")
    note_text = normalize_text(effective_profile.note if effective_profile is not None else "")
    effective_display = f"Effective DB: {selected_label} -> {effective_label}" if selected_id == FOLLOW_GLOBAL_PROFILE_ID else f"Effective DB: {effective_label or effective_database or USE_GAS_DEFAULT_LABEL}"
    source_text = f"{alias_text or effective_label or USE_GAS_DEFAULT_LABEL} / {effective_database}" if effective_database else effective_label or USE_GAS_DEFAULT_LABEL

    normalized_database = effective_database.lower()
    normalized_label = f"{alias_text} {note_text} {effective_label}".lower()
    is_dummy = bool(
        (effective_profile is not None and normalize_text(effective_profile.profile_id) == DEFAULT_DUMMY_PROFILE_ID)
        or normalized_database == "hwgroup_erp_22022026"
        or "dummy" in normalized_label
    )
    warning_text = ""
    if is_dummy:
        warning_text = (
            "Dashboard Control sedang membaca Dummy ERP "
            f"({effective_database or effective_label}). Transaksi live seperti "
            "WCGT/INT/00036, STJ/2026/02/1212, dan STJ/2026/03/0367 mungkin tidak ada di database ini."
        )

    return {
        "selected_profile_id": selected_id,
        "selected_label": selected_label,
        "effective_profile": effective_profile,
        "effective_label": effective_label,
        "effective_database": effective_database,
        "effective_display": effective_display,
        "source_text": source_text,
        "warning_text": warning_text,
        "is_dummy": is_dummy,
    }


def _format_snapshot_source_database(self, database_value: str) -> str:
    clean_database = normalize_text(database_value)
    if not clean_database:
        return "Source DB: -"
    profile = find_database_profile_by_value(self.context.global_settings.database_profiles, clean_database)
    alias_text = normalize_text(profile.alias if profile is not None else "")
    if alias_text:
        return f"Source DB: {alias_text} / {clean_database}"
    return f"Source DB: {clean_database}"


def _refresh_notice_area(self) -> None:
    if normalize_text(self.database_notice_var.get()):
        self.database_notice_label.pack(fill="x", pady=(0, 8))
    else:
        self.database_notice_label.pack_forget()
    if normalize_text(self.warning_var.get()):
        self.warning_label.pack(fill="x")
    else:
        self.warning_label.pack_forget()


def _refresh_effective_database_display(self) -> None:
    state = self._resolve_effective_database_state()
    self.effective_db_var.set(state["effective_display"])
    self.database_notice_var.set(state["warning_text"])
    self._refresh_notice_area()


def set_repair_collection_changed_callback(self, callback: Callable[[], None] | None) -> None:
    self._repair_collection_changed_callback = callback
    self._notify_repair_collection_changed()


def _notify_repair_collection_changed(self) -> None:
    callback = getattr(self, "_repair_collection_changed_callback", None)
    if callback is not None:
        callback()


def _request_analysis_refresh(self, *, preserve_repair_results: bool = False) -> None:
    self._analysis_refresh_pending = True
    if preserve_repair_results:
        self._repair_refresh_pending = True
    self.root.after(50, self.start_analysis)


def _current_repair_collection_scope(self) -> _RepairCollectionScope | None:
    company_id = self._selected_company_id()
    if company_id <= 0:
        return None
    db_state = self._resolve_effective_database_state(selected_profile_id=self._selected_database_profile_id())
    database_profile_id = normalize_text(db_state.get("selected_profile_id"))
    database_value = normalize_text(db_state.get("effective_database"))
    if not database_profile_id and not database_value:
        return None
    company_label = self._label_for_company_id(company_id) or normalize_text(self.company_choice_var.get()) or f"Company #{company_id}"
    return _RepairCollectionScope(
        database_profile_id=database_profile_id,
        database_label=normalize_text(db_state.get("effective_label")) or normalize_text(db_state.get("selected_label")),
        database_value=database_value,
        company_id=company_id,
        company_label=company_label,
    )


def _repair_collection_scope_matches_current(self) -> bool:
    if not getattr(self, "_repair_collection", None) or getattr(self, "_repair_collection_scope", None) is None:
        return False
    return self._repair_collection_scope.matches(self._current_repair_collection_scope())


def _can_modify_repair_collection_from_current_scope(self) -> bool:
    current_scope = self._current_repair_collection_scope()
    if current_scope is None:
        return False
    collection = getattr(self, "_repair_collection", None)
    scope = getattr(self, "_repair_collection_scope", None)
    return scope is None or not collection or scope.matches(current_scope)

def _today_text() -> str:
    return date.today().strftime("%Y-%m-%d")

def _normalize_iso_date(value: Any) -> str:
    text = normalize_text(value)
    if not text:
        return ""
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        return ""


def _pcb_case1_default_date(self) -> str:
    return self._today_text()


def _save_pcb_case1_last_date(self, value: Any) -> None:
    normalized_date = self._normalize_iso_date(value)
    if not normalized_date:
        return
    state_store = getattr(self, "_state_store", None)
    latest = state_store.load() if state_store is not None else getattr(self, "_module_settings", None)
    if latest is None:
        return
    if normalize_text(getattr(latest, "pcb_case1_last_date", "")) == normalized_date:
        self._module_settings = latest
        return
    latest.pcb_case1_last_date = normalized_date
    self._module_settings = latest
    if state_store is not None:
        state_store.save(latest)


def _repair_collection_total_amount(self) -> float:
    collection = getattr(self, "_repair_collection", OrderedDict())
    return round(sum(float(entry.draft.get("amount") or entry.seed.get("amount") or 0.0) for entry in collection.values()), 2)


def _repair_collection_scope_text(self) -> str:
    scope = getattr(self, "_repair_collection_scope", None)
    if scope is None:
        return ""
    parts = [normalize_text(scope.database_label), normalize_text(scope.company_label)]
    return " / ".join(part for part in parts if part)


def _repair_collection_state_counts(self) -> dict[str, int]:
    counts = {key: 0 for key in REPAIR_ROW_STATE_LABELS}
    collection = getattr(self, "_repair_collection", OrderedDict())
    for entry in collection.values():
        state = normalize_text(entry.draft.get("row_status")).lower() or "incomplete"
        if state not in counts:
            state = "incomplete"
        counts[state] += 1
    return counts

def _repair_collection_counts_text(counts: dict[str, int]) -> str:
    parts: list[str] = []
    for state in ("incomplete", "ready", "running", "failed", "repaired"):
        count = int(counts.get(state) or 0)
        if count <= 0:
            continue
        parts.append(f"{REPAIR_ROW_STATE_LABELS[state]} {count}")
    return " | ".join(parts)


def _close_repair_dialog_if_collection_source(self) -> None:
    dialog = self._last_repair_dialog_widgets.get("dialog")
    source_kind = normalize_text(self._last_repair_dialog_widgets.get("source_kind"))
    if source_kind != "repair_collection" or dialog is None:
        return
    try:
        if bool(dialog.winfo_exists()):
            dialog.destroy()
    except Exception:  # noqa: BLE001
        return
    self._last_repair_dialog_widgets = {}


def get_repair_collection_ui_state(self) -> dict[str, Any]:
    collection = getattr(self, "_repair_collection", OrderedDict())
    count = len(collection)
    total_amount = self._repair_collection_total_amount()
    state_counts = self._repair_collection_state_counts()
    counts_text = self._repair_collection_counts_text(state_counts)
    scope_matches = self._repair_collection_scope_matches_current()
    has_rows = count > 0
    if not has_rows:
        summary_text = "0 record | Total 0.00"
    elif scope_matches:
        summary_text = f"{count} record | Total {total_amount:,.2f}"
        if counts_text:
            summary_text = f"{summary_text} | {counts_text}"
    else:
        scope_text = self._repair_collection_scope_text()
        summary_text = f"{count} record | Total {total_amount:,.2f}"
        if counts_text:
            summary_text = f"{summary_text} | {counts_text}"
        if scope_text:
            summary_text = f"{summary_text} | Saved: {scope_text} [inactive]"
        else:
            summary_text = f"{summary_text} | [inactive]"
    return {
        "count": count,
        "total_amount": total_amount,
        "state_counts": state_counts,
        "has_rows": has_rows,
        "scope_matches_current": scope_matches,
        "can_open": has_rows and scope_matches,
        "can_clear": has_rows,
        "can_modify_current_scope": self._can_modify_repair_collection_from_current_scope(),
        "summary_text": summary_text,
    }


def is_repair_row_collected(self, row_key: str) -> bool:
    clean_row_key = normalize_text(row_key)
    collection = getattr(self, "_repair_collection", {})
    return bool(clean_row_key and clean_row_key in collection)


def _reset_repair_collection_scope_if_empty(self) -> None:
    if not getattr(self, "_repair_collection", None):
        self._repair_collection_scope = None


def clear_repair_collection(self) -> None:
    collection = getattr(self, "_repair_collection", None)
    if collection is None:
        self._repair_collection = OrderedDict()
        collection = self._repair_collection
    count = len(collection)
    collection.clear()
    self._repair_collection_scope = None
    self._close_repair_dialog_if_collection_source()
    self._notify_repair_collection_changed()
    if count > 0:
        self.status_var.set(f"Repair Collection dibersihkan. {count} record dihapus.")


def _build_kpi_card(self, parent: tk.Frame, title: str | tk.StringVar, variable: tk.StringVar, column: int) -> None:
    parent.grid_columnconfigure(column, weight=1)
    card = tk.Frame(parent, bg=T.BG_CARD, bd=1, relief="solid", highlightbackground=T.BORDER_LIGHT, highlightthickness=1)
    card.grid(row=0, column=column, sticky="nsew", padx=(0, 8) if column < 3 else 0, pady=(0, 0))
    inner = tk.Frame(card, bg=T.BG_CARD, padx=12, pady=10)
    inner.pack(fill="both", expand=True)
    title_kwargs = {
        "bg": T.BG_CARD,
        "fg": T.TEXT_MUTED,
        "font": T.font(T.FONT_SMALL_SIZE, bold=True),
    }
    if isinstance(title, tk.StringVar):
        title_kwargs["textvariable"] = title
    else:
        title_kwargs["text"] = title
    tk.Label(inner, **title_kwargs).pack(anchor="w")
    tk.Label(inner, textvariable=variable, bg=T.BG_CARD, fg=T.TEXT_ON_LIGHT, font=T.font(T.FONT_HEADING_SIZE, bold=True)).pack(anchor="w", pady=(6, 0))


def _build_dual_kpi_card(
    self,
    parent: tk.Frame,
    title: str | tk.StringVar,
    primary_variable: tk.StringVar,
    secondary_label: str | tk.StringVar,
    secondary_variable: tk.StringVar,
    column: int,
) -> None:
    parent.grid_columnconfigure(column, weight=1)
    card = tk.Frame(parent, bg=T.BG_CARD, bd=1, relief="solid", highlightbackground=T.BORDER_LIGHT, highlightthickness=1)
    card.grid(row=0, column=column, sticky="nsew", padx=(0, 8) if column < 3 else 0, pady=(0, 0))
    inner = tk.Frame(card, bg=T.BG_CARD, padx=12, pady=10)
    inner.pack(fill="both", expand=True)
    title_kwargs = {
        "bg": T.BG_CARD,
        "fg": T.TEXT_MUTED,
        "font": T.font(T.FONT_SMALL_SIZE, bold=True),
    }
    if isinstance(title, tk.StringVar):
        title_kwargs["textvariable"] = title
    else:
        title_kwargs["text"] = title
    tk.Label(inner, **title_kwargs).pack(anchor="w")
    tk.Label(inner, textvariable=primary_variable, bg=T.BG_CARD, fg=T.TEXT_ON_LIGHT, font=T.font(T.FONT_HEADING_SIZE, bold=True)).pack(anchor="w", pady=(6, 0))
    secondary_row = tk.Frame(inner, bg=T.BG_CARD)
    secondary_row.pack(anchor="w", pady=(6, 0))
    secondary_kwargs = {
        "bg": T.BG_CARD,
        "fg": T.TEXT_MUTED,
        "font": T.font(T.FONT_SMALL_SIZE),
        "text": f"{normalize_text(secondary_label.get())}:" if isinstance(secondary_label, tk.StringVar) else f"{secondary_label}:",
    }
    secondary_label_widget = tk.Label(secondary_row, **secondary_kwargs)
    secondary_label_widget.pack(side="left")
    if isinstance(secondary_label, tk.StringVar):
        secondary_label.trace_add(
            "write",
            lambda *_args, widget=secondary_label_widget, variable=secondary_label: widget.configure(
                text=f"{normalize_text(variable.get())}:"
            ),
        )
    tk.Label(
        secondary_row,
        textvariable=secondary_variable,
        bg=T.BG_CARD,
        fg=T.TEXT_ON_LIGHT,
        font=T.font(T.FONT_SMALL_SIZE, bold=True),
    ).pack(side="left", padx=(6, 0))


def _configure_company_coa_tree(self) -> None:
    if not hasattr(self, "company_coa_tree"):
        return
    tree = self.company_coa_tree
    tree.heading("code", text="CODE")
    tree.column("code", width=110, minwidth=110, anchor="w", stretch=False)
    tree.heading("coa_name", text="COA NAME")
    tree.column("coa_name", width=260, minwidth=220, anchor="w", stretch=False)
    tree.heading("debit", text="DEBIT")
    tree.column("debit", width=110, minwidth=100, anchor="e", stretch=False)
    tree.heading("credit", text="KREDIT")
    tree.column("credit", width=110, minwidth=100, anchor="e", stretch=False)
    tree.heading("balance", text="BALANCE")
    tree.column("balance", width=120, minwidth=120, anchor="e", stretch=False)
    tree.configure(displaycolumns=("code", "coa_name", "balance"))


def _configure_pcb_raw_tree(self) -> None:
    if not hasattr(self, "pcb_raw_tree"):
        return
    t = self.pcb_raw_tree
    # Custom style: clean base rowheight; individual rows can be expanded on demand.
    _pcb_style = ttk.Style()
    _pcb_style.configure("PCBRaw.Treeview", rowheight=20)
    _pcb_style.configure("PCBRaw.Treeview.Heading", font=T.font(T.FONT_SMALL_SIZE, bold=True))
    t.configure(style="PCBRaw.Treeview")
    _cols: list[tuple[str, str, int, str]] = [
        # (col_id, heading, width, anchor)
        ("tanggal",         "TGL",              80,  "w"),
        ("kode_transaksi",  "KODE TRANSAKSI",  165,  "w"),
        ("jenis",           "JENIS",            55,  "w"),
        ("tipe_akun",       "TIPE AKUN",       130,  "w"),
        ("akun_code",       "KODE AKUN",        90,  "w"),
        ("akun_name",       "NAMA AKUN",       200,  "w"),
        ("kode_item",       "KODE ITEM",        90,  "w"),
        ("nama_item",       "NAMA ITEM",       200,  "w"),
        ("uom",             "UOM",              60,  "w"),
        ("qty_item",        "QTY",              70,  "e"),
        ("kategori_produk", "KATEGORI",        140,  "w"),
        ("no_po",           "NO PO",           160,  "w"),
        ("komunikasi",      "KOMUNIKASI",      200,  "w"),
        ("debit",           "DEBIT",           110,  "e"),
        ("kredit",          "KREDIT",          110,  "e"),
        ("saldo",           "SALDO",           110,  "e"),
        ("matching",        "MATCHING",        100,  "w"),
    ]
    for col_id, heading, width, anchor in _cols:
        t.heading(col_id, text=heading)
        t.column(col_id, width=width, minwidth=width, anchor=anchor, stretch=False)


def _configured_inventory_coa_codes(self) -> list[str]:
    codes: list[str] = []
    seen: set[str] = set()
    for entry in getattr(self.context.global_settings, "inventory_coa_entries", []) or []:
        code = normalize_text(getattr(entry, "coa_code", "") or (entry.get("coa_code") if isinstance(entry, dict) else "")).strip().upper()
        if not code or code in seen:
            continue
        seen.add(code)
        codes.append(code)
    return codes


def _configured_repair_account_candidates(self) -> list[SvlDashboardRepairAccountCandidate]:
    candidates: list[SvlDashboardRepairAccountCandidate] = []
    seen: set[str] = set()
    context = getattr(self, "context", None)
    global_settings = getattr(context, "global_settings", None)
    for entry in getattr(global_settings, "repair_account_entries", []) or []:
        code = normalize_text(
            getattr(entry, "coa_code", "") or (entry.get("coa_code") if isinstance(entry, dict) else "")
        ).strip().upper()
        if not code or code in seen:
            continue
        seen.add(code)
        label = normalize_text(getattr(entry, "label", "") or (entry.get("label") if isinstance(entry, dict) else ""))
        candidates.append(
            SvlDashboardRepairAccountCandidate(
                code=code,
                name=label or code,
                source="Global Extra",
                role="extra",
            )
        )
    return candidates

def _group_sidebar_items(self, items: list[Any]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, list[Any]]] = {}
    for item in items:
        valuation_title = self._valuation_title_for_item(item)
        category_title = self._category_title_for_item(item)
        grouped.setdefault(valuation_title, {}).setdefault(category_title, []).append(item)
    valuation_groups: list[dict[str, Any]] = []
    valuation_order = {
        "Automated / Track Inventory": 0,
        "Non Product": 1,
        "Manual / Non Inventory": 2,
    }
    for valuation_title, category_map in grouped.items():
        category_groups: list[dict[str, Any]] = []
        flattened_items: list[Any] = []
        for category_title, category_items in category_map.items():
            ordered_items = sorted(category_items, key=lambda entry: abs(self._item_primary_amount(entry)), reverse=True)
            flattened_items.extend(ordered_items)
            category_groups.append(
                {
                    "title": category_title,
                    "state_key": f"{valuation_title}::{category_title}",
                    "items": [self._sidebar_leaf_for_item(entry) for entry in ordered_items],
                    "count": len(ordered_items),
                    "total_diff": sum(self._item_primary_amount(entry) for entry in ordered_items),
                    "total_abs_diff": sum(abs(self._item_primary_amount(entry)) for entry in ordered_items),
                    "children": [],
                }
            )
        category_groups.sort(key=lambda group: (-group["total_abs_diff"], normalize_text(group["title"]).lower()))
        valuation_groups.append(
            {
                "title": valuation_title,
                "children": category_groups,
                "items": [],
                "count": len(flattened_items),
                "total_diff": sum(self._item_primary_amount(entry) for entry in flattened_items),
                "total_abs_diff": sum(abs(self._item_primary_amount(entry)) for entry in flattened_items),
            }
        )
    valuation_groups.sort(
        key=lambda group: (
            valuation_order.get(normalize_text(group["title"]), 99),
            -group["total_abs_diff"],
            normalize_text(group["title"]).lower(),
        )
    )
    return valuation_groups

def _format_category_summary(group: dict[str, Any]) -> str:
    return f"{int(group['count'])} item | Total {float(group['total_diff']):,.2f}"

def _format_valuation_summary(group: dict[str, Any]) -> str:
    return f"{int(group['count'])} item | Total {float(group['total_diff']):,.2f}"

def _merged_group_priority(status: str) -> int:
    order = {
        "Linked JE header kosong": 0,
        "Linked JE tanpa line valuasi": 1,
        "SVL tanpa JE": 2,
        "Journal tanpa SVL": 3,
        "Fallback hint": 4,
        "Linked via move": 5,
    }
    clean_status = normalize_text(status)
    return order.get(clean_status, 99)

def _merged_group_default_open(status: str) -> bool:
    return normalize_text(status) != "Linked via move"


def _merged_group_is_open(self, status: str) -> bool:
    clean_status = normalize_text(status) or "-"
    if clean_status not in self._merged_group_open:
        self._merged_group_open[clean_status] = self._merged_group_default_open(clean_status)
    return bool(self._merged_group_open.get(clean_status, True))


def _group_merged_rows(self, rows: list[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        meta = dict(row.get("meta") or {})
        status = normalize_text(meta.get("group_status")) or normalize_text((row.get("values") or ("",))[0]) or "-"
        meta["group_status"] = status
        row["meta"] = meta
        grouped.setdefault(status, []).append(row)
    return sorted(
        grouped.items(),
        key=lambda item: (self._merged_group_priority(item[0]), normalize_text(item[0]).lower()),
    )

def _format_merged_group_header(status: str, count: int, total_value: float) -> str:
    return f"{status} | {count} row | Total {total_value:,.2f}"


def _normalize_tree_row_payload(row: Any, *, default_row_key: str) -> tuple[tuple[Any, ...], tuple[str, ...], dict[str, Any]]:
    tags: tuple[str, ...] = ()
    meta: dict[str, Any] = {}
    if isinstance(row, dict):
        values = tuple(row.get("values") or ())
        tags = tuple(tag for tag in row.get("tags", ()) if tag)
        meta = dict(row.get("meta") or {})
    else:
        tag = row[-1] if row and isinstance(row[-1], str) and row[-1] in {"svl_orphan", "jnl_orphan"} else ""
        values = tuple(row[:-1] if tag else row)
        tags = (tag,) if tag else ()
    row_key = normalize_text(meta.get("row_key")) or default_row_key
    meta["row_key"] = row_key
    return values, tags, meta


def _copy_text_to_clipboard(self, text: str) -> None:
    self.root.clipboard_clear()
    self.root.clipboard_append(text)


def _selected_tree_leaf_item_ids(self, tree: ttk.Treeview) -> list[str]:
    meta_map = self._tree_row_meta.get(tree, {})
    return [
        item_id
        for item_id in tree.selection()
        if not bool(meta_map.get(item_id, {}).get("group_header")) and not bool(meta_map.get(item_id, {}).get("placeholder"))
    ]


def _has_copyable_tree_active_cell(self, tree: ttk.Treeview) -> bool:
    active_cell = self._tree_active_cell.get(tree)
    if not active_cell:
        return False
    item_id, column_id = active_cell
    if not item_id or not column_id:
        return False
    meta = self._tree_row_meta.get(tree, {}).get(item_id, {})
    if meta.get("group_header") or meta.get("placeholder"):
        return False
    try:
        column_index = int(column_id.replace("#", "")) - 1
    except ValueError:
        return False
    values = tree.item(item_id, "values")
    return 0 <= column_index < len(values)


def _build_account_move_locator(self, move_id: int) -> str:
    clean_move_id = int(move_id or 0)
    if clean_move_id <= 0:
        return ""
    base_url = normalize_text(self._latest_base_url)
    if base_url:
        return f"{base_url}/web#id={clean_move_id}&model=account.move&view_type=form"
    return f"account.move:{clean_move_id}"


def _build_locator_text(self, meta: dict[str, Any]) -> str:
    locators: list[str] = []
    model = normalize_text(meta.get("odoo_model"))
    record_id = int(meta.get("odoo_id") or 0)
    base_url = normalize_text(self._latest_base_url)
    if model and record_id > 0 and base_url:
        locators.append(f"{base_url}/web#id={record_id}&model={model}&view_type=form")
    elif model and record_id > 0:
        locators.append(f"{model}:{record_id}")
    move_id = int(meta.get("move_id") or 0)
    if move_id > 0 and not locators:
        locators.append(self._build_account_move_locator(move_id))
    for extra_locator in meta.get("extra_locators", []) or []:
        clean_locator = normalize_text(extra_locator)
        if clean_locator and clean_locator not in locators:
            locators.append(clean_locator)
    return "\n".join(locators)


def _placeholder_row(self, columns: tuple[str, ...], message: str) -> dict[str, Any]:
    return {"values": self._placeholder_values(columns, message), "meta": {"placeholder": True}}


def _format_sidebar_summary(self, items: list[Any]) -> str:
    total_value = sum(self._item_primary_amount(item) for item in items)
    return f"{len(items)} item | Total {total_value:,.2f}"


def _sidebar_item_wrap_chars(self) -> int:
    tree = getattr(self, "sidebar_tree", None)
    width_px = 0
    if tree is not None:
        try:
            width_px = int(tree.column("#0", option="width") or 0)
        except Exception:  # noqa: BLE001
            width_px = 0
        if width_px <= 0:
            try:
                width_px = int(tree.winfo_width() or 0)
            except Exception:  # noqa: BLE001
                width_px = 0
    if width_px <= 0:
        return SIDEBAR_ITEM_WRAP_FALLBACK_CHARS
    return max(
        SIDEBAR_ITEM_WRAP_MIN_CHARS,
        min(SIDEBAR_ITEM_WRAP_FALLBACK_CHARS + 8, int((width_px - 64) / 7)),
    )

def _wrap_sidebar_text(value: str, *, width: int, max_lines: int) -> list[str]:
    clean_value = normalize_text(value).strip()
    if not clean_value:
        return ["-"]
    wrapper = textwrap.TextWrapper(
        width=max(SIDEBAR_ITEM_WRAP_MIN_CHARS, int(width or SIDEBAR_ITEM_WRAP_FALLBACK_CHARS)),
        max_lines=max(1, int(max_lines or 1)),
        placeholder="...",
        break_long_words=False,
        break_on_hyphens=False,
    )
    lines = [line.rstrip() for line in wrapper.wrap(clean_value) if normalize_text(line)]
    if lines:
        return lines
    return [clean_value]

def _truncate_sidebar_text(value: str, *, max_chars: int = 84) -> str:
    clean_value = normalize_text(value).strip()
    if len(clean_value) <= max_chars:
        return clean_value
    return clean_value[: max_chars - 3].rstrip() + "..."

def _format_sidebar_group_text(title: str, summary: str) -> str:
    clean_title = normalize_text(title) or "Unknown"
    clean_summary = normalize_text(summary)
    if not clean_summary:
        return clean_title
    return f"{clean_title}\n{clean_summary}"


def _format_sidebar_item_text(self, item: Any) -> str:
    source_item = item.get("item") if isinstance(item, dict) else item
    amount = float(item.get("amount", self._item_primary_amount(source_item)) if isinstance(item, dict) else self._item_primary_amount(item))
    code = normalize_text(getattr(source_item, "code", "")) or "-"
    name = normalize_text(getattr(source_item, "name", "")) or "-"
    wrap_chars = self._sidebar_item_wrap_chars()
    title_lines = self._wrap_sidebar_text(
        f"{code} | {name}",
        width=wrap_chars,
        max_lines=SIDEBAR_ITEM_TITLE_MAX_LINES,
    )
    parts = [f"{self._item_primary_amount_label()} {amount:,.2f}"]
    svl_count = int(getattr(source_item, "svl_orphan_count", 0) or 0)
    jnl_count = int(getattr(source_item, "jnl_orphan_count", 0) or 0)
    po_count = self._item_record_count(source_item, "po_line_count", "po_lines")
    if svl_count > 0:
        parts.append(f"{svl_count} SVL")
    if jnl_count > 0:
        parts.append(f"{jnl_count} JNL")
    if po_count > 0:
        parts.append(f"{po_count} PO")
    meta_lines = self._wrap_sidebar_text(" | ".join(parts), width=wrap_chars, max_lines=1)
    return "\n".join([*title_lines, *meta_lines])


