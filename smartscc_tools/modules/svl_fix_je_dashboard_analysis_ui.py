"""Lazy-loaded analysis/detail UI helpers for SVL Fix JE dashboard page."""

from __future__ import annotations

from smartscc_tools.modules import svl_fix_je_dashboard_page as page_mod

def _sync_page_globals() -> None:
    for _name, _value in page_mod.__dict__.items():
        if _name.startswith("__"):
            continue
        globals()[_name] = _value


_sync_page_globals()


def _fill_grouped_merged_tree(self, tree: ttk.Treeview, rows: list[dict[str, Any]]) -> None:
    old_meta = self._tree_row_meta.get(tree, {})
    existing_groups: dict[str, str] = {}
    for gid in tree.get_children():
        gm = old_meta.get(gid, {})
        if gm.get("group_header"):
            gs = normalize_text(gm.get("group_status"))
            if gs:
                existing_groups[gs] = gid

    meta_map: dict[str, dict[str, Any]] = {}
    active_group_ids: list[str] = []

    for group_index, (status, grouped_rows) in enumerate(self._group_merged_rows(rows)):
        total_value = round(sum(float((row.get("meta") or {}).get("group_amount") or 0.0) for row in grouped_rows), 2)
        header_text = self._format_merged_group_header(status, len(grouped_rows), total_value)

        group_item_id = existing_groups.pop(status, "")
        if group_item_id:
            tree.item(group_item_id, text=header_text)
            if tree.index(group_item_id) != group_index:
                tree.move(group_item_id, "", group_index)
        else:
            group_item_id = tree.insert(
                "", group_index, text=header_text,
                open=self._merged_group_is_open(status), tags=("merged_group",),
            )
        meta_map[group_item_id] = {
            "group_header": True, "group_status": status,
            "repair_candidate": False, "repair_seed": None,
        }
        active_group_ids.append(group_item_id)

        existing_children: dict[str, str] = {}
        for cid in tree.get_children(group_item_id):
            rk = normalize_text(old_meta.get(cid, {}).get("row_key"))
            if rk and rk not in existing_children:
                existing_children[rk] = cid

        active_child_ids: set[str] = set()
        for child_index, row in enumerate(grouped_rows):
            tags = tuple(tag for tag in row.get("tags", ()) if tag)
            meta = dict(row.get("meta") or {})
            row_key = normalize_text(meta.get("row_key")) or f"group::{status}::idx::{child_index}"
            meta["row_key"] = row_key
            values = tuple(row.get("values") or ())
            child_id = existing_children.pop(row_key, "")
            if child_id:
                tree.item(child_id, text="", values=values, tags=tags)
                if tree.index(child_id) != child_index:
                    tree.move(child_id, group_item_id, child_index)
            else:
                child_id = tree.insert(group_item_id, child_index, text="", values=values, tags=tags)
            meta["group_header"] = False
            meta["group_status"] = status
            meta_map[child_id] = meta
            active_child_ids.add(child_id)

        stale_children = [cid for cid in tree.get_children(group_item_id) if cid not in active_child_ids]
        if stale_children:
            tree.delete(*stale_children)

    for stale_gid in existing_groups.values():
        tree.delete(stale_gid)

    self._tree_row_meta[tree] = meta_map
    self._tree_active_cell[tree] = None


def _toggle_merged_group(self, tree: ttk.Treeview, item_id: str) -> None:
    meta = self._tree_row_meta.get(tree, {}).get(item_id, {})
    if not meta.get("group_header"):
        return
    status = normalize_text(meta.get("group_status")) or "-"
    next_state = not bool(tree.item(item_id, "open"))
    tree.item(item_id, open=next_state)
    self._merged_group_open[status] = next_state


def _build_tree(
    self,
    parent: tk.Frame,
    columns: tuple[str, ...],
    *,
    selectmode: str = "browse",
    role: str | None = None,
    show: str = "headings",
) -> ttk.Treeview:
    parent.columnconfigure(0, weight=1)
    parent.rowconfigure(0, weight=1)
    parent.rowconfigure(1, weight=0)
    tree = ttk.Treeview(parent, columns=columns, show=show, selectmode=selectmode)
    tree.grid(row=0, column=0, sticky="nsew")
    vscrollbar = ttk.Scrollbar(parent, orient="vertical", command=tree.yview)
    vscrollbar.grid(row=0, column=1, sticky="ns")
    hscrollbar = ttk.Scrollbar(parent, orient="horizontal", command=tree.xview)
    hscrollbar.grid(row=1, column=0, sticky="ew")
    tree.configure(yscrollcommand=vscrollbar.set, xscrollcommand=hscrollbar.set)
    for column in columns:
        tree.heading(column, text=column.replace("_", " ").upper())
        anchor = "e" if column in {"qty", "unit_cost", "value", "debit", "credit", "net", "balance"} else "w"
        width = 90 if anchor == "e" else 140
        tree.column(column, width=width, minwidth=width, anchor=anchor, stretch=False)
    tree.tag_configure("svl_orphan", background="#fcebea")
    tree.tag_configure("jnl_orphan", background="#e8f6ef")
    tree.tag_configure("repair_candidate", background="#fff6d9")
    tree.tag_configure("fallback_hint", background="#f8f0ff")
    tree.tag_configure("merged_group", background="#eef1f5")
    tree.tag_configure("cycle_group", background="#dce8f5")
    tree = bind_treeview_scroll_support(tree)
    if role:
        self._register_tree_support(tree, role=role)
    return tree


def _build_detail_tabs(self, parent: tk.Frame, *, row: int, column: int) -> None:
    host = tk.Frame(parent, bg=T.BG_CARD)
    self._detail_tab_host = host
    host.grid(row=row, column=column, sticky="nsew")
    host.columnconfigure(0, weight=1)
    host.rowconfigure(1, weight=1)

    tab_row = tk.Frame(host, bg=T.BG_CARD)
    tab_row.grid(row=0, column=0, sticky="ew", pady=(0, 8))
    content = tk.Frame(host, bg=T.BG_CARD)
    content.grid(row=1, column=0, sticky="nsew")
    content.columnconfigure(0, weight=1)
    content.rowconfigure(0, weight=1)

    tab_specs = [
        ("svl", "SVL Detail", "default"),
        ("jnl", "Journal Detail", "default"),
        ("merged", "Gabungan Detail", "merged"),
        ("current_asset", "Detail Akun", "default"),
        ("compare", "PO vs Bill", "default"),
        ("analysis", "Analisis Selisih", "default"),
    ]
    for index, (tab_id, label, color_key) in enumerate(tab_specs):
        button = tk.Button(
            tab_row,
            text=label,
            relief="flat",
            bd=0,
            highlightthickness=0,
            padx=12,
            pady=6,
            cursor="hand2",
            font=T.font(T.FONT_SMALL_SIZE, bold=True),
            command=lambda current=tab_id: self._switch_detail_tab(current),
        )
        button.grid(row=0, column=index, sticky="w", padx=(0, 6))
        button._tab_color_key = color_key  # type: ignore[attr-defined]
        self._detail_tab_buttons[tab_id] = button

        frame = tk.Frame(content, bg=T.BG_CARD)
        frame.grid(row=0, column=0, sticky="nsew")
        self._detail_tab_frames[tab_id] = frame

    self.svl_tab = self._detail_tab_frames["svl"]
    self.jnl_tab = self._detail_tab_frames["jnl"]
    self.merged_tab = self._detail_tab_frames["merged"]
    self.current_asset_tab = self._detail_tab_frames["current_asset"]
    self.compare_tab = self._detail_tab_frames["compare"]
    self.analysis_tab = self._detail_tab_frames["analysis"]

    self.svl_tree = self._build_tree(
        self.svl_tab,
        ("id", "date", "qty", "unit_cost", "value", "reference", "journal"),
        selectmode="extended",
        role="svl",
    )
    self.jnl_tree = self._build_tree(
        self.jnl_tab,
        ("journal", "date", "account_code", "account_name", "debit", "credit", "net", "has_svl", "reference"),
        selectmode="extended",
        role="journal",
    )
    self.merged_tree = self._build_tree(
        self.merged_tab,
        (
            "match_basis",
            "svl_id",
            "svl_date",
            "svl_value",
            "journal",
            "move_state",
            "aml_id",
            "account_code",
            "account_name",
            "debit",
            "credit",
            "net",
            "reference",
            "note",
        ),
        selectmode="extended",
        role="merged",
        show="tree headings",
    )
    self.merged_tree.heading("#0", text="STATUS")
    self.merged_tree.column("#0", width=240, minwidth=180, anchor="w", stretch=False)
    self._build_current_asset_tab()
    self._build_compare_tab()
    self._build_analysis_tab()
    self._switch_detail_tab(self._active_detail_tab)


def _switch_detail_tab(self, tab_id: str) -> None:
    if tab_id not in self._detail_tab_frames:
        return
    self._active_detail_tab = tab_id
    for current_id, frame in self._detail_tab_frames.items():
        if current_id == tab_id:
            frame.grid()
        else:
            frame.grid_remove()
    for current_id, button in self._detail_tab_buttons.items():
        color_key = getattr(button, "_tab_color_key", "default")
        palette = DETAIL_TAB_COLORS.get(color_key, DETAIL_TAB_COLORS["default"])
        is_active = current_id == tab_id
        button.configure(
            bg=palette["active"] if is_active else palette["inactive"],
            fg=T.TEXT_ON_LIGHT if color_key == "merged" or not is_active else T.TEXT_ON_DARK,
            activebackground=palette["active"],
            activeforeground=T.TEXT_ON_LIGHT if color_key == "merged" or not is_active else T.TEXT_ON_DARK,
        )
    self._schedule_active_detail_render(allow_autoload=True)


def _build_current_asset_tab(self) -> None:
    if not hasattr(self, "current_asset_summary_var"):
        self.current_asset_summary_var = tk.StringVar(value="")
    self.current_asset_tab.columnconfigure(0, weight=1)
    self.current_asset_tab.rowconfigure(1, weight=1)
    # Row 0: summary label
    tk.Label(
        self.current_asset_tab,
        textvariable=self.current_asset_summary_var,
        bg=T.BG_CARD,
        fg=T.TEXT_MUTED,
        font=T.font(T.FONT_SMALL_SIZE),
        justify="left",
        anchor="w",
    ).grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))
    # Row 1: scrollable container with two stacked tables
    # scroll_host expands to fill tab height so canvas shows all rows without clipping
    scroll_host = tk.Frame(self.current_asset_tab, bg=T.BG_CARD)
    scroll_host.grid(row=1, column=0, sticky="nsew", padx=(8, 0), pady=(0, 8))
    scroll_host.columnconfigure(0, weight=1)
    scroll_host.rowconfigure(0, weight=1)
    canvas = tk.Canvas(scroll_host, bg=T.BG_CARD, highlightthickness=0)
    vbar = ttk.Scrollbar(scroll_host, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=vbar.set)
    vbar.grid(row=0, column=1, sticky="ns")
    canvas.grid(row=0, column=0, sticky="nsew")
    inner = tk.Frame(canvas, bg=T.BG_CARD)
    self._ca_scroll_win_id = canvas.create_window((0, 0), window=inner, anchor="nw")

    def _sync_ca_scroll(event: Any = None) -> None:
        canvas.configure(scrollregion=canvas.bbox("all"))
        canvas.itemconfig(self._ca_scroll_win_id, width=canvas.winfo_width())

    inner.bind("<Configure>", _sync_ca_scroll)
    canvas.bind("<Configure>", lambda e: canvas.itemconfig(self._ca_scroll_win_id, width=e.width))
    bind_treeview_scroll_support(canvas)
    # Table 1: Detail Transaksi per Akun (account move lines, per line)
    tk.Label(
        inner,
        text="Detail Transaksi per Akun",
        bg=T.BG_CARD,
        fg=T.TEXT_MUTED,
        font=T.font(T.FONT_SMALL_SIZE, bold=True),
        anchor="w",
    ).pack(fill="x", padx=4, pady=(6, 2))
    aml_frame = tk.Frame(inner, bg=T.BG_CARD)
    aml_frame.pack(fill="x", padx=4, pady=(0, 8))
    self.current_asset_tree = self._build_tree(
        aml_frame,
        ("date", "journal_source", "transaction_no", "gr_reference", "po", "partner_reference", "akun", "akun_name", "debit", "credit", "balance", "matching"),
        selectmode="extended",
        role="current_asset",
    )
    self.current_asset_tree.configure(height=10)
    # Adjust widths for the wider columns
    self.current_asset_tree.column("akun_name", width=200, minwidth=120)
    self.current_asset_tree.column("partner_reference", width=180, minwidth=100)
    self.current_asset_tree.column("gr_reference", width=180, minwidth=100)
    self.current_asset_tree.column("transaction_no", width=160, minwidth=100)
    # Table 2: Cycle Link
    tk.Label(
        inner,
        text="Cycle Link",
        bg=T.BG_CARD,
        fg=T.TEXT_MUTED,
        font=T.font(T.FONT_SMALL_SIZE, bold=True),
        anchor="w",
    ).pack(fill="x", padx=4, pady=(4, 2))
    cycle_frame = tk.Frame(inner, bg=T.BG_CARD)
    cycle_frame.pack(fill="x", padx=4, pady=(0, 8))
    self.cycle_link_tree = self._build_tree(
        cycle_frame,
        ("gr_reference", "gr_date", "po", "bill", "payment", "bank_move", "status", "source"),
        selectmode="extended",
        role="cycle_link",
    )
    self.cycle_link_tree.configure(height=10)


def _build_compare_tab(self) -> None:
    if not hasattr(self, "compare_summary_var"):
        self.compare_summary_var = tk.StringVar(value="")
    self.compare_tab.columnconfigure(0, weight=1)
    self.compare_tab.columnconfigure(1, weight=1)
    self.compare_tab.rowconfigure(1, weight=1)
    tk.Label(
        self.compare_tab,
        textvariable=self.compare_summary_var,
        bg=T.BG_CARD,
        fg=T.TEXT_MUTED,
        font=T.font(T.FONT_SMALL_SIZE),
        justify="left",
        anchor="w",
    ).grid(row=0, column=0, columnspan=2, sticky="ew", padx=8, pady=(8, 4))
    po_frame = tk.Frame(self.compare_tab, bg=T.BG_CARD)
    bill_frame = tk.Frame(self.compare_tab, bg=T.BG_CARD)
    po_frame.grid(row=1, column=0, sticky="nsew", padx=(8, 4), pady=(0, 8))
    bill_frame.grid(row=1, column=1, sticky="nsew", padx=(4, 8), pady=(0, 8))
    self.compare_po_tree = self._build_tree(
        po_frame,
        ("po", "price_unit", "qty_received", "qty_invoiced", "value", "status"),
        selectmode="extended",
        role="compare_po",
    )
    self.compare_bill_tree = self._build_tree(
        bill_frame,
        ("bill", "po", "date", "price_unit", "quantity", "value"),
        selectmode="extended",
        role="compare_bill",
    )


def _build_analysis_tab(self) -> None:
    if not hasattr(self, "analysis_notice_var"):
        self.analysis_notice_var = tk.StringVar(value="")
    if not hasattr(self, "analysis_label_vars") or len(getattr(self, "analysis_label_vars", [])) < 3:
        self.analysis_label_vars = [
            tk.StringVar(value="SVL tanpa Journal Entry"),
            tk.StringVar(value="Journal Entry tanpa SVL"),
            tk.StringVar(value="Selisih lainnya"),
        ]
    self.analysis_tab.columnconfigure(1, weight=1)
    tk.Label(
        self.analysis_tab,
        textvariable=self.analysis_notice_var,
        bg=T.BG_CARD,
        fg=T.TEXT_MUTED,
        justify="left",
        anchor="w",
        wraplength=980,
    ).grid(row=0, column=0, columnspan=3, sticky="ew", padx=12, pady=(12, 0))
    for row_index, color in enumerate(
        (
            T.STATUS_ERROR,
            T.STATUS_SUCCESS,
            T.STATUS_WARNING,
        )
    ):
        tk.Label(
            self.analysis_tab,
            textvariable=self.analysis_label_vars[row_index],
            bg=T.BG_CARD,
            fg=T.TEXT_ON_LIGHT,
            font=T.font(T.FONT_BODY_SIZE, bold=True),
        ).grid(row=row_index + 1, column=0, sticky="w", padx=12, pady=(14 if row_index == 0 else 8, 0))
        canvas = tk.Canvas(self.analysis_tab, width=420, height=20, bg=T.BG_CARD, highlightthickness=0)
        canvas.grid(row=row_index + 1, column=1, sticky="ew", padx=8, pady=(14 if row_index == 0 else 8, 0))
        outline_id = canvas.create_rectangle(0, 2, 420, 18, outline=T.BORDER_LIGHT)
        fill_id = canvas.create_rectangle(0, 2, 4, 18, fill=color, outline=color)
        value_var = tk.StringVar(value="0.00")
        tk.Label(self.analysis_tab, textvariable=value_var, bg=T.BG_CARD, fg=color, font=T.font(T.FONT_BODY_SIZE, bold=True)).grid(
            row=row_index + 1, column=2, sticky="e", padx=12, pady=(14 if row_index == 0 else 8, 0)
        )
        self._analysis_rows.append((canvas, value_var, fill_id, outline_id))


def resume(self) -> None:
    if self._poll_active:
        return
    self._poll_active = True
    self._poll_queues()


def pause(self) -> None:
    self._poll_active = False
    if self._poll_after_id is not None:
        try:
            self.root.after_cancel(self._poll_after_id)
        except Exception:  # noqa: BLE001
            pass
        self._poll_after_id = None
    if self._sidebar_filter_after_id is not None:
        try:
            self.root.after_cancel(self._sidebar_filter_after_id)
        except Exception:  # noqa: BLE001
            pass
        self._sidebar_filter_after_id = None
    self._cancel_scheduled_company_dropdown()
    self._cancel_scheduled_company_commit()
    self._cancel_scheduled_snapshot_apply()
    self._cancel_post_paint_prefetch()
    self._cancel_scheduled_detail_render(invalidate_token=True)

_DETAIL_RENDER_BATCH_SIZE = 120
_DETAIL_RENDER_BUDGET_MS = 8.0


def _cancel_scheduled_detail_render(self, *, invalidate_token: bool = False) -> None:
    if invalidate_token:
        self._detail_render_token = int(getattr(self, "_detail_render_token", 0) or 0) + 1
    current_after_id = getattr(self, "_detail_render_after_id", None)
    if current_after_id is not None and hasattr(self, "root"):
        after_cancel = getattr(self.root, "after_cancel", None)
        if callable(after_cancel):
            try:
                after_cancel(current_after_id)
            except Exception:  # noqa: BLE001
                pass
    self._detail_render_after_id = None


def _schedule_active_detail_render(self, *, allow_autoload: bool = True) -> None:
    if not hasattr(self, "root"):
        return
    self._cancel_scheduled_detail_render(invalidate_token=True)
    self._detail_render_allow_autoload = bool(allow_autoload)
    render_token = int(getattr(self, "_detail_render_token", 0) or 0)
    after_idle = getattr(self.root, "after_idle", None)
    callback = lambda token=render_token: self._render_active_detail_tab(token)
    if callable(after_idle):
        self._detail_render_after_id = after_idle(callback)
        return
    self._detail_render_after_id = self.root.after(0, callback)


def _render_active_detail_tab(self, token: int | None = None) -> None:
    self._detail_render_after_id = None
    self._render_tab_if_needed(
        self._active_detail_tab,
        token=token,
        allow_autoload=bool(getattr(self, "_detail_render_allow_autoload", True)),
    )

def _schedule_detail_render_batch(self, callback: Callable[[], None]) -> None:
    self._detail_render_after_id = self.root.after(1, callback)


def _finish_detail_render(self, tab_id: str, *, token: int) -> None:
    if token != int(getattr(self, "_detail_render_token", 0) or 0):
        return
    self._detail_render_after_id = None
    self._dirty_detail_tabs.discard(tab_id)


def _render_plain_tree_in_batches(
    self,
    tree: ttk.Treeview,
    rows: list[Any],
    *,
    token: int,
    on_complete: "Callable[[], None] | None" = None,
) -> None:
    normalized_rows = [
        self._normalize_tree_row_payload(row, default_row_key=f"index::{index}")
        for index, row in enumerate(rows)
    ]
    children = tree.get_children()
    if children:
        tree.delete(*children)
    meta_map: dict[str, dict[str, Any]] = {}
    self._tree_row_meta[tree] = meta_map
    self._tree_active_cell[tree] = None

    def _insert_batch(offset: int) -> None:
        if token != int(getattr(self, "_detail_render_token", 0) or 0):
            return
        started = perf_counter()
        index = offset
        total_rows = len(normalized_rows)
        while index < total_rows:
            values, tags, meta = normalized_rows[index]
            item_id = tree.insert("", "end", values=values, tags=tags)
            meta_map[item_id] = meta
            index += 1
            elapsed_ms = (perf_counter() - started) * 1000
            if (
                index - offset >= self._DETAIL_RENDER_BATCH_SIZE
                or (elapsed_ms >= self._DETAIL_RENDER_BUDGET_MS and index > offset)
            ):
                break
        if index < total_rows:
            self._schedule_detail_render_batch(lambda next_offset=index: _insert_batch(next_offset))
            return
        self._detail_render_after_id = None
        if on_complete is not None:
            on_complete()

    _insert_batch(0)


def _render_grouped_merged_tree_in_batches(
    self,
    tree: ttk.Treeview,
    rows: list[dict[str, Any]],
    *,
    token: int,
    on_complete: "Callable[[], None] | None" = None,
) -> None:
    grouped_rows = self._group_merged_rows(rows)
    children = tree.get_children()
    if children:
        tree.delete(*children)
    meta_map: dict[str, dict[str, Any]] = {}
    self._tree_row_meta[tree] = meta_map
    self._tree_active_cell[tree] = None

    pending_children: list[tuple[str, tuple[Any, ...], tuple[str, ...], dict[str, Any]]] = []
    for group_index, (status, rows_in_group) in enumerate(grouped_rows):
        total_value = round(sum(float((row.get("meta") or {}).get("group_amount") or 0.0) for row in rows_in_group), 2)
        group_item_id = tree.insert(
            "",
            group_index,
            text=self._format_merged_group_header(status, len(rows_in_group), total_value),
            open=self._merged_group_is_open(status),
            tags=("merged_group",),
        )
        meta_map[group_item_id] = {
            "group_header": True,
            "group_status": status,
            "repair_candidate": False,
            "repair_seed": None,
        }
        for child_index, row in enumerate(rows_in_group):
            values, tags, meta = self._normalize_tree_row_payload(
                row,
                default_row_key=f"group::{status}::idx::{child_index}",
            )
            meta["group_header"] = False
            meta["group_status"] = status
            pending_children.append((group_item_id, values, tags, meta))

    def _insert_batch(offset: int) -> None:
        if token != int(getattr(self, "_detail_render_token", 0) or 0):
            return
        started = perf_counter()
        index = offset
        total_rows = len(pending_children)
        while index < total_rows:
            group_item_id, values, tags, meta = pending_children[index]
            item_id = tree.insert(group_item_id, "end", text="", values=values, tags=tags)
            meta_map[item_id] = meta
            index += 1
            elapsed_ms = (perf_counter() - started) * 1000
            if (
                index - offset >= self._DETAIL_RENDER_BATCH_SIZE
                or (elapsed_ms >= self._DETAIL_RENDER_BUDGET_MS and index > offset)
            ):
                break
        if index < total_rows:
            self._schedule_detail_render_batch(lambda next_offset=index: _insert_batch(next_offset))
            return
        self._detail_render_after_id = None
        if on_complete is not None:
            on_complete()

    _insert_batch(0)


def _on_sidebar_tree_toggled(self, _event: tk.Event | None = None) -> None:
    if not hasattr(self, "sidebar_tree"):
        return
    item_id = normalize_text(self.sidebar_tree.focus())
    meta = self._sidebar_tree_meta.get(item_id, {})
    kind = normalize_text(meta.get("kind"))
    if kind == "valuation" or (kind == "group" and int(meta.get("level") or 0) <= 0):
        state_key = normalize_text(meta.get("state_key"))
        if state_key:
            self._sidebar_valuation_open[state_key] = bool(self.sidebar_tree.item(item_id, "open"))
    elif kind in {"category", "group"}:
        state_key = normalize_text(meta.get("state_key"))
        if state_key:
            self._sidebar_category_open[state_key] = bool(self.sidebar_tree.item(item_id, "open"))


def _on_sidebar_tree_selected(self, _event: tk.Event | None = None) -> None:
    if self._sidebar_tree_syncing_selection or not hasattr(self, "sidebar_tree"):
        return
    if self._is_purchase_cycle_mode():
        return  # PCB mode: handled entirely by _on_pcb_cycle_selected
    selection = tuple(self.sidebar_tree.selection())
    item_id = normalize_text(selection[0] if selection else self.sidebar_tree.focus())
    pid = int(self._sidebar_tree_pid_by_item_id.get(item_id) or 0)
    if item_id in self._sidebar_tree_pid_by_item_id:
        if pid == int(self._selected_product_id or 0):
            return
        self._select_item(pid, source="sidebar_tree")
        return
    selected_item_ids = list(self._sidebar_tree_item_id_by_pid.get(int(self._selected_product_id or 0), []) or [])
    self._sidebar_tree_syncing_selection = True
    try:
        if selected_item_ids:
            self.sidebar_tree.selection_set(selected_item_ids[0])
            self.sidebar_tree.focus(selected_item_ids[0])
        else:
            self.sidebar_tree.selection_remove(self.sidebar_tree.selection())
    finally:
        self._sidebar_tree_syncing_selection = False


def _register_tree_support(self, tree: ttk.Treeview, *, role: str) -> None:
    self._tree_row_meta[tree] = {}
    self._tree_active_cell[tree] = None
    self._tree_role_by_widget[tree] = role
    tree.bind("<Button-1>", lambda event, current=tree: self._on_tree_left_click(current, event), add="+")
    tree.bind("<Button-3>", lambda event, current=tree: self._show_tree_context_menu(current, event), add="+")
    tree.bind("<Control-c>", lambda event, current=tree: self._on_tree_copy_shortcut(current, event), add="+")


def _on_tree_left_click(self, tree: ttk.Treeview, event: tk.Event) -> str | None:
    row_id = tree.identify_row(event.y)
    column_id = tree.identify_column(event.x)
    role = self._tree_role_by_widget.get(tree)
    meta = self._tree_row_meta.get(tree, {}).get(row_id, {})
    if role == "merged" and meta.get("group_header"):
        self._toggle_merged_group(tree, row_id)
        self._tree_active_cell[tree] = None
        return "break"
    self._tree_active_cell[tree] = (row_id, column_id) if row_id and column_id else None
    return None


def _on_tree_copy_shortcut(self, tree: ttk.Treeview, _event: tk.Event | None = None) -> str:
    self._copy_tree_active_cell(tree)
    return "break"


def _copy_tree_active_cell(self, tree: ttk.Treeview) -> None:
    active_cell = self._tree_active_cell.get(tree)
    if not active_cell:
        return
    item_id, column_id = active_cell
    if not item_id or not column_id:
        return
    if self._tree_row_meta.get(tree, {}).get(item_id, {}).get("group_header"):
        return
    try:
        column_index = int(column_id.replace("#", "")) - 1
    except ValueError:
        return
    values = tree.item(item_id, "values")
    if column_index < 0 or column_index >= len(values):
        return
    self._copy_text_to_clipboard(str(values[column_index] or ""))
    self.status_var.set("Cell disalin ke clipboard.")


def _copy_tree_selected_rows(self, tree: ttk.Treeview) -> None:
    selection = self._selected_tree_leaf_item_ids(tree)
    if not selection:
        return
    lines = []
    meta_map = self._tree_row_meta.get(tree, {})
    for item_id in selection:
        meta = meta_map.get(item_id, {})
        values = [str(value or "") for value in tree.item(item_id, "values")]
        if self._tree_role_by_widget.get(tree) == "merged":
            values = [normalize_text(meta.get("group_status")) or "-", *values]
        lines.append("\t".join(values))
    if not lines:
        return
    self._copy_text_to_clipboard("\n".join(lines))
    self.status_var.set(f"{len(lines)} baris disalin ke clipboard.")


def _copy_tree_locator(self, tree: ttk.Treeview) -> None:
    selection = self._selected_tree_leaf_item_ids(tree)
    if not selection:
        return
    lines = []
    meta_map = self._tree_row_meta.get(tree, {})
    for item_id in selection:
        locator = self._build_locator_text(meta_map.get(item_id, {}))
        if locator:
            lines.append(locator)
    if not lines:
        return
    self._copy_text_to_clipboard("\n".join(lines))
    self.status_var.set(f"{len(lines)} locator Odoo disalin.")


def _apply_snapshot(self, snapshot: SvlDashboardSnapshot | None) -> None:
    preferred_product_id = int(self._selected_product_id or 0)
    self._cancel_post_paint_prefetch()
    self._snapshot_interactive_ready = False
    self._detail_warm_state = "idle"
    self._detail_warm_target_keys = set()
    self._latest_snapshot = snapshot
    self._current_detail_payload = None
    self._sidebar_category_open = {}
    self._merged_group_open = {}
    self._sidebar_all_item_ids = None
    if snapshot is not None:
        self._clear_repair_collections_for_scope_change()
    self._reconcile_repair_collection_with_snapshot(snapshot)
    self._reconcile_pcb_case1_collection_with_snapshot(snapshot)
    if snapshot and snapshot.items and any(item.pid == preferred_product_id for item in snapshot.items):
        self._selected_product_id = preferred_product_id
    else:
        self._selected_product_id = snapshot.items[0].pid if snapshot and snapshot.items else 0
    self.warning_var.set("\n".join(snapshot.warnings) if snapshot and snapshot.warnings else "")
    self._refresh_notice_area()
    self._apply_company_summary(snapshot)
    self._log_ui_stage("UI company summary applied")
    on_complete = None
    if snapshot is not None:
        on_complete = lambda current_snapshot=snapshot: self._finalize_snapshot_apply(current_snapshot)
    try:
        self.render_item_cards(on_complete=on_complete, selected_render_mode="summary", log_sidebar_stage=True)
    except TypeError:
        self.render_item_cards()
        if on_complete is not None:
            on_complete()
    self._reset_sidebar_scroll_position()
    self._notify_repair_collection_changed()
    if snapshot is None:
        self.status_var.set("Belum ada hasil.")
        self._refresh_busy_state()


def _finalize_snapshot_apply(self, snapshot: SvlDashboardSnapshot | None) -> None:
    if snapshot is None or snapshot is not self._latest_snapshot:
        return
    if all(hasattr(self, attr_name) for attr_name in ("progress_value", "percent_var", "phase_var")):
        self._apply_progress(
            SvlDashboardProgressSnapshot(
                phase="ready",
                processed=1,
                total=1,
                current="Analisis selesai.",
                progress=1.0,
            )
        )
    self.status_var.set(self._snapshot_ready_status_text(snapshot))
    self._set_busy(False)
    self._snapshot_interactive_ready = True
    self._log_ui_stage("UI busy cleared")
    self._log_ui_stage("UI snapshot interactive")
    # Enable company-level PCB Excel export button when PCB snapshot is ready
    if self._is_purchase_cycle_mode():
        btn = getattr(self, "_pcb_company_export_btn", None)
        if btn is not None:
            has_cycles = bool(list(getattr(snapshot, "purchase_cycles", None) or []))
            btn.configure(state="normal" if has_cycles else "disabled")
    if getattr(self, "_current_detail_item", None) is not None:
        self._schedule_active_detail_render(allow_autoload=False)
        self._schedule_post_paint_prefetch(delay_ms=150)


def _set_empty_detail(self) -> None:
    self._cancel_post_paint_prefetch()
    self._cancel_scheduled_detail_render(invalidate_token=True)
    self._current_detail_item = None
    self._current_detail_payload = None
    self._current_detail_repair_seeds_by_svl_id = {}
    self._dirty_detail_tabs.clear()
    self.header_code_var.set("NO CODE")
    self.header_name_var.set("Belum ada hasil analisis.")
    self.header_meta_var.set("Pilih company dan jalankan Analyze.")
    self.source_db_var.set("Source DB: -")
    self.position_var.set("-")
    self.position_badge.configure(bg=T.BRAND_SECONDARY)
    for variable in (self.kpi_svl_var, self.kpi_bs_var, self.kpi_diff_var, self.kpi_po_bill_var):
        variable.set("0.00")
    self.kpi_sub_var.set("Belum ada item terpilih.")
    self.compare_summary_var.set("Pilih item untuk melihat detail.")
    self.current_asset_summary_var.set("")
    self.analysis_notice_var.set("Ringkasan komponen utama selisih per item.")
    tree_widgets = [self.svl_tree, self.jnl_tree, self.merged_tree, self.compare_po_tree, self.compare_bill_tree]
    if hasattr(self, "current_asset_tree"):
        tree_widgets.extend([self.current_asset_tree, self.cycle_link_tree])
    for tree in tree_widgets:
        children = tree.get_children()
        if children:
            tree.delete(*children)
        self._tree_row_meta[tree] = {}
        self._tree_active_cell[tree] = None
    self._set_empty_company_summary()
    self._render_analysis_bars((0.0, 0.0, 0.0))


def _render_analysis_bars(self, values: tuple[float, float, float]) -> None:
    self._analysis_values = values
    bar_max = max(max(abs(value) for value in values), 1.0)
    for index, value in enumerate(values):
        canvas, value_var, fill_id, outline_id = self._analysis_rows[index]
        max_width = max(40, canvas.winfo_width() or 420)
        width = max(10, int((abs(value) / bar_max) * max_width))
        canvas.coords(fill_id, 0, 2, max(4, width), 18)
        canvas.coords(outline_id, 0, 2, max_width, 18)
        value_var.set(f"{value:,.2f}")


def _clear_snapshot(self, *, reset_company: bool = False) -> None:
    self._cancel_scheduled_snapshot_apply()
    self._cancel_post_paint_prefetch()
    self._latest_snapshot = None
    self._pcb_visible_cycles = []
    self._latest_analysis_request = None
    self._latest_analysis_profile_id = ""
    self._detail_warm_state = "idle"
    self._detail_warm_target_keys = set()
    self._detail_cache.clear()
    self._detail_pending_keys.clear()
    self._detail_error_by_key.clear()
    self._detail_session_token = int(getattr(self, "_detail_session_token", 0) or 0) + 1
    self._snapshot_interactive_ready = False
    self._selected_product_id = 0
    self._sidebar_category_open = {}
    self._sidebar_valuation_open = {}
    self._merged_group_open = {}
    self._sidebar_all_item_ids = None
    self.warning_var.set("")
    self._refresh_notice_area()
    if reset_company:
        self._selected_company_id_value = 0
        self.company_choice_var.set("")
        latest = self._state_store.load()
        latest.dashboard_company_id = 0
        self._module_settings = latest
        self._state_store.save(latest)
    self._set_company_combo_values(self._company_labels)
    self.render_item_cards()
    self._reset_sidebar_scroll_position()
    self._set_empty_detail()
    self._refresh_busy_state()
    self._notify_repair_collection_changed()
    btn = getattr(self, "_pcb_company_export_btn", None)
    if btn is not None:
        btn.configure(state="disabled")


def _filtered_items(self) -> list[Any]:
    snapshot = self._latest_snapshot
    if snapshot is None:
        return []
    items = list(snapshot.items)
    query = self._current_search_query().lower()
    if not query:
        return items
    return [
        item for item in items
        if query in normalize_text(item.code).lower() or query in normalize_text(item.name).lower()
    ]


def _reset_sidebar_scroll_position(self) -> None:
    tree = getattr(self, "sidebar_tree", None)
    if tree is not None:
        try:
            tree.yview_moveto(0)
            return
        except Exception:  # noqa: BLE001
            pass
    canvas = getattr(getattr(self, "sidebar_scroll", None), "canvas", None)
    if canvas is None:
        return
    try:
        canvas.yview_moveto(0)
    except Exception:  # noqa: BLE001
        return


def _configure_sidebar_tree_style(self) -> None:
    style = ttk.Style(self.sidebar_list_host)
    style.configure(
        SIDEBAR_TREE_STYLE,
        rowheight=SIDEBAR_TREE_ROW_HEIGHT,
        font=T.font(T.FONT_SMALL_SIZE),
    )
    style.configure(
        PCB_SIDEBAR_TREE_STYLE,
        rowheight=PCB_SIDEBAR_TREE_ROW_HEIGHT,
        font=T.font(T.FONT_SMALL_SIZE),
    )


def _clear_sidebar_tree(self) -> None:
    if not hasattr(self, "sidebar_tree"):
        return
    children = self.sidebar_tree.get_children()
    if children:
        self.sidebar_tree.delete(*children)
    self._sidebar_tree_meta.clear()
    self._sidebar_tree_item_id_by_pid.clear()
    self._sidebar_tree_pid_by_item_id.clear()

_SIDEBAR_ITEM_BATCH_SIZE = 10
_SIDEBAR_ITEM_BUDGET_MS = 5.0


def _rebuild_sidebar_tree(
    self,
    items: list[Any],
    *,
    on_complete: "Callable[[], None] | None" = None,
    log_stage: bool = False,
) -> None:
    """Rebuild the sidebar Treeview in batches to keep the event loop responsive.

    Phase 1 (synchronous): insert valuation/category group headers — very few of these.
    Phase 2 (batched via after(0)): insert leaf product items _SIDEBAR_ITEM_BATCH_SIZE
    at a time, yielding to the event loop between batches so the UI never freezes.
    """
    self._sidebar_rebuild_token += 1
    my_token = self._sidebar_rebuild_token

    self._clear_sidebar_tree()

    pending_items: list[tuple[str, dict[str, Any]]] = []

    def _insert_group(parent_id: str, group: dict[str, Any], *, level: int) -> None:
        title = normalize_text(group.get("title")) or "Unknown"
        state_key = normalize_text(group.get("state_key")) or title
        summary_text = self._format_valuation_summary(group) if level == 0 else self._format_category_summary(group)
        open_state = (
            bool(self._sidebar_valuation_open.get(state_key, True))
            if level == 0
            else bool(self._sidebar_category_open.get(state_key, True))
        )
        tags = ("valuation_group",) if level == 0 else ("category_group",)
        group_item_id = self.sidebar_tree.insert(
            parent_id,
            "end",
            text=self._format_sidebar_group_text(title, summary_text),
            open=open_state,
            tags=tags,
        )
        self._sidebar_tree_meta[group_item_id] = {
            "kind": "valuation" if level == 0 else "group",
            "state_key": state_key,
            "_parent_id": parent_id,
            "level": level,
        }
        for child_group in list(group.get("children", []) or []):
            _insert_group(group_item_id, child_group, level=level + 1)
        for leaf_item in list(group.get("items", []) or []):
            pending_items.append((group_item_id, leaf_item))

    for valuation_group in self._group_sidebar_items(items):
        _insert_group("", valuation_group, level=0)

    if log_stage:
        self._log_ui_stage("UI sidebar batch start")

    # Phase 2: insert leaf items in batches, yielding to the event loop between each batch.
    def _insert_batch(offset: int) -> None:
        if self._sidebar_rebuild_token != my_token:
            return  # A newer rebuild started; abort this stale batch.
        started = perf_counter()
        next_offset = offset
        inserted = 0
        while next_offset < len(pending_items):
            if inserted >= self._SIDEBAR_ITEM_BATCH_SIZE:
                break
            if inserted > 0 and ((perf_counter() - started) * 1000.0) >= self._SIDEBAR_ITEM_BUDGET_MS:
                break
            category_item_id, item = pending_items[next_offset]
            pid = int((item.get("pid") if isinstance(item, dict) else getattr(item, "pid", 0)) or 0)
            amount = float(item.get("amount", 0.0) if isinstance(item, dict) else getattr(item, "difference", 0.0) or 0.0)
            try:
                item_id = self.sidebar_tree.insert(
                    category_item_id, "end",
                    text=self._format_sidebar_item_text(item),
                    tags=("sidebar_item_positive",) if amount >= 0 else ("sidebar_item_negative",),
                )
            except tk.TclError:
                return  # Parent was deleted by a concurrent _clear_sidebar_tree; abort.
            self._sidebar_tree_meta[item_id] = {"kind": "item", "pid": pid, "_parent_id": category_item_id}
            self._sidebar_tree_item_id_by_pid.setdefault(pid, []).append(item_id)
            self._sidebar_tree_pid_by_item_id[item_id] = pid
            next_offset += 1
            inserted += 1
        if next_offset < len(pending_items):
            self.root.after(0, lambda: _insert_batch(next_offset))
        else:
            if self._sidebar_rebuild_token == my_token and on_complete is not None:
                if log_stage:
                    self._log_ui_stage("UI sidebar batch complete")
                on_complete()

    if pending_items:
        after_idle = getattr(self.root, "after_idle", None)
        if callable(after_idle):
            after_idle(lambda: _insert_batch(0))
        else:
            self.root.after(0, lambda: _insert_batch(0))
    elif on_complete is not None:
        if log_stage:
            self._log_ui_stage("UI sidebar batch complete")
        on_complete()


def _sync_sidebar_selection(self, *, ensure_visible: bool = False) -> None:
    tree = getattr(self, "sidebar_tree", None)
    if tree is None:
        return
    item_ids = list(self._sidebar_tree_item_id_by_pid.get(int(self._selected_product_id or 0), []) or [])
    item_id = item_ids[0] if item_ids else ""
    current_selection = tuple(tree.selection())
    current_focus = normalize_text(tree.focus())
    if item_id and current_selection == (item_id,) and current_focus == item_id:
        if ensure_visible:
            try:
                tree.see(item_id)
            except Exception:  # noqa: BLE001
                pass
        return
    self._sidebar_tree_syncing_selection = True
    try:
        if item_id:
            tree.selection_set(item_id)
            tree.focus(item_id)
            if ensure_visible:
                tree.see(item_id)
        else:
            tree.selection_remove(tree.selection())
    finally:
        self._sidebar_tree_syncing_selection = False


def _render_item_cards_legacy(self) -> None:
    self.render_item_cards()


def _select_item(self, product_id: int, *, source: str = "external", force_render: bool = False) -> None:
    pid = int(product_id or 0)
    if pid == 0:
        return
    current_pid = int(self._selected_product_id or 0)
    if pid == current_pid and not force_render:
        return
    old_frame = self._sidebar_item_frames.get(current_pid)
    if old_frame and old_frame.winfo_exists():
        old_frame.configure(highlightbackground=T.BORDER_LIGHT)
    self._selected_product_id = pid
    new_frame = self._sidebar_item_frames.get(pid)
    if new_frame and new_frame.winfo_exists():
        new_frame.configure(highlightbackground=T.BRAND_PRIMARY)
    if normalize_text(source) != "sidebar_tree":
        self._sync_sidebar_selection(ensure_visible=True)
    try:
        self._render_selected_item(summary_only=False)
    except TypeError:
        self._render_selected_item()
    item = getattr(self, "_current_detail_item", None)
    if (
        bool(getattr(self, "_snapshot_interactive_ready", False))
        and item is not None
        and normalize_text(getattr(self, "_detail_warm_state", "")) == "idle"
    ):
        self._schedule_post_paint_prefetch(delay_ms=150)


def _build_selected_repair_seed_map(self, item: Any) -> dict[int, dict[str, Any]]:
    repair_seeds_by_svl_id: dict[int, dict[str, Any]] = {}
    for record in getattr(item, "merged_records", []):
        svl_id = int(getattr(record, "svl_id", 0) or 0)
        if svl_id <= 0 or not bool(getattr(record, "repair_candidate", False)):
            continue
        repair_seed = self._build_repair_seed_from_merged_row(item, record)
        if repair_seed is None:
            continue
        repair_seeds_by_svl_id[svl_id] = repair_seed
    return repair_seeds_by_svl_id


def _ensure_selected_repair_seed_map(self, item: Any) -> dict[int, dict[str, Any]]:
    current_map = getattr(self, "_current_detail_repair_seeds_by_svl_id", {})
    if current_map:
        return current_map
    rebuilt_map = self._build_selected_repair_seed_map(item)
    self._current_detail_repair_seeds_by_svl_id = rebuilt_map
    return rebuilt_map


def _merged_row_payload(self, item: Any, record: Any) -> dict[str, Any]:
    repair_seed = self._build_repair_seed_from_merged_row(item, record)
    is_repair_candidate = bool(getattr(record, "repair_candidate", False) and repair_seed)
    recent_result = self._recent_repair_results_by_svl_id.get(int(getattr(record, "svl_id", 0) or 0))
    note_text = normalize_text(getattr(record, "note", ""))
    extra_locators: list[str] = []
    group_amount = float(getattr(record, "net", 0.0) or 0.0)
    if int(getattr(record, "aml_id", 0) or 0) <= 0:
        group_amount = float(getattr(record, "svl_value", 0.0) or 0.0)
    if (
        recent_result is not None
        and int(recent_result.move_id or 0) > 0
        and int(getattr(record, "move_id", 0) or 0) == int(recent_result.move_id or 0)
        and normalize_text(recent_result.old_move_action) == "mark_only"
        and int(recent_result.old_move_id or 0) > 0
    ):
        old_move_text = normalize_text(recent_result.old_move_name) or str(int(recent_result.old_move_id))
        repair_note = f"Repair replacement selesai; old move {old_move_text} tetap mark only."
        note_text = f"{note_text} | {repair_note}" if note_text else repair_note
        old_locator = self._build_account_move_locator(int(recent_result.old_move_id or 0))
        if old_locator:
            extra_locators.append(old_locator)
    return {
        "values": (
            record.match_basis,
            record.svl_id or "-",
            record.svl_date or "-",
            f"{record.svl_value:,.2f}" if record.svl_id else "-",
            record.move_name or "-",
            record.move_state or "-",
            record.aml_id or "-",
            record.account_code or "-",
            record.account_name or "-",
            f"{record.debit:,.2f}" if record.aml_id else "-",
            f"{record.credit:,.2f}" if record.aml_id else "-",
            f"{record.net:,.2f}" if record.aml_id else "-",
            record.reference or record.svl_reference or "-",
            note_text or "-",
        ),
        "tags": tuple(
            tag
            for tag in (
                "repair_candidate" if is_repair_candidate else "",
                "fallback_hint" if normalize_text(getattr(record, "row_type", "")) == "svl_reference_hint" else "",
            )
            if tag
        ),
        "meta": {
            "row_key": normalize_text(getattr(record, "row_key", ""))
            or f"merged::{normalize_text(getattr(record, 'row_type', 'row'))}::{int(getattr(record, 'svl_id', 0) or 0)}::{int(getattr(record, 'aml_id', 0) or 0)}",
            "odoo_model": "account.move.line"
            if int(getattr(record, "aml_id", 0) or 0) > 0
            else ("account.move" if int(getattr(record, "move_id", 0) or 0) > 0 else "stock.valuation.layer"),
            "odoo_id": int(getattr(record, "aml_id", 0) or getattr(record, "move_id", 0) or getattr(record, "svl_id", 0) or 0),
            "move_id": int(getattr(record, "move_id", 0) or 0),
            "extra_locators": extra_locators,
            "group_status": normalize_text(getattr(record, "status", "")) or "-",
            "group_amount": group_amount,
            "repair_candidate": is_repair_candidate,
            "repair_seed": repair_seed,
        },
    }


def _ensure_item_detail_loaded(self, item: Any, *, tab_id: str, reason: str = "") -> None:
    if not self._tab_requires_lazy_detail(tab_id):
        return
    if normalize_text(getattr(item, "item_kind", "")) == "unassigned_journal" and tab_id in {"svl", "compare"}:
        return
    request = getattr(self, "_latest_analysis_request", None)
    if request is None:
        return
    cache_key = self._detail_cache_key(request, int(getattr(item, "pid", 0) or 0))
    if cache_key in self._detail_cache or cache_key in self._detail_pending_keys or self._detail_waits_for_warm_cache(item):
        return
    profile_id = normalize_text(getattr(self, "_latest_analysis_profile_id", "")) or self._selected_database_profile_id()
    request_copy = SvlDashboardRequest(
        database=normalize_text(request.database),
        company_id=int(request.company_id or 0),
        date_from=normalize_text(request.date_from),
        date_to=normalize_text(request.date_to),
        inventory_coa_codes=list(request.inventory_coa_codes or []),
        dataset_mode=normalize_dashboard_dataset_mode(getattr(request, "dataset_mode", "")),
        include_inventory_accounts=bool(getattr(request, "include_inventory_accounts", True)),
        include_non_inventory_accounts=bool(getattr(request, "include_non_inventory_accounts", True)),
    )
    self._detail_pending_keys.add(cache_key)
    self._detail_error_by_key.pop(cache_key, None)
    session_token = int(getattr(self, "_detail_session_token", 0) or 0)

    def worker() -> None:
        try:
            if reason == "first_item_prefetch":
                self._log_ui_stage("UI first-item detail fetch started")
            settings, config = self._runtime_builder(
                self.context.global_settings,
                self.logger,
                database_profile_id=profile_id,
            )

            async def _run() -> SvlDashboardItemDetail:
                async with AsyncOdooJsonRpcClient(config=config, settings=settings, logger=self.logger) as rpc:
                    service = SvlDashboardServiceAsync(
                        rpc=rpc,
                        logger=self.logger,
                        on_log=self.log_queue.put,
                    )
                    return await service.fetch_item_detail(request_copy, int(getattr(item, "pid", 0) or 0))

            detail = asyncio.run(_run())
            self.ui_queue.put(
                {
                    "type": "detail_loaded",
                    "cache_key": cache_key,
                    "detail": detail,
                    "session_token": session_token,
                }
            )
        except Exception as exc:  # noqa: BLE001
            self.log_queue.put(f"ERROR: {exc}")
            self.ui_queue.put(
                {
                    "type": "detail_error",
                    "cache_key": cache_key,
                    "message": str(exc),
                    "session_token": session_token,
                }
            )

    threading.Thread(
        target=worker,
        daemon=True,
        name=f"svl-dashboard-detail-{int(getattr(item, 'pid', 0) or 0)}",
    ).start()


def _detail_placeholder_rows(self, tree: ttk.Treeview, message: str) -> list[dict[str, Any]]:
    return [self._placeholder_row(tuple(tree["columns"]), message)]


def _svl_tree_rows(self, item: Any, detail: SvlDashboardItemDetail | None) -> list[dict[str, Any]]:
    repair_seeds_by_svl_id = self._ensure_selected_repair_seed_map(item) if detail is not None else {}
    if normalize_text(item.item_kind) == "unassigned_journal":
        return self._detail_placeholder_rows(self.svl_tree, "Tidak ada detail SVL untuk item journal tanpa product_id.")
    if detail is None:
        error_message = self._detail_error_for_item(item)
        if error_message:
            return self._detail_placeholder_rows(self.svl_tree, f"Gagal memuat detail item: {error_message}")
        return self._detail_placeholder_rows(self.svl_tree, self._detail_pending_message_for_item(item))
    if not detail.svl_records:
        return self._detail_placeholder_rows(self.svl_tree, "Tidak ada detail SVL untuk item ini.")
    return [
        {
            "values": (
                record.record_id,
                record.date,
                f"{record.quantity:,.2f}",
                f"{record.unit_cost:,.2f}",
                f"{record.value:,.2f}",
                record.reference,
                record.journal_ref or ("TIDAK ADA" if not record.has_journal else "ADA"),
            ),
            "tags": ("svl_orphan",) if not record.has_journal else (),
            "meta": {
                "row_key": f"svl::{int(record.record_id or 0)}",
                "odoo_model": "stock.valuation.layer",
                "odoo_id": int(record.record_id or 0),
                "move_id": int(record.move_id or 0),
                "repair_candidate": int(record.record_id or 0) in repair_seeds_by_svl_id,
                "repair_seed": repair_seeds_by_svl_id.get(int(record.record_id or 0)),
            },
        }
        for record in detail.svl_records
    ]


def _journal_tree_rows(self, item: Any, detail: SvlDashboardItemDetail | None) -> list[dict[str, Any]]:
    if detail is None:
        error_message = self._detail_error_for_item(item)
        if error_message:
            return self._detail_placeholder_rows(self.jnl_tree, f"Gagal memuat detail item: {error_message}")
        return self._detail_placeholder_rows(self.jnl_tree, self._detail_pending_message_for_item(item))
    if not detail.jnl_records:
        return self._detail_placeholder_rows(self.jnl_tree, "Tidak ada detail journal untuk item ini.")
    return [
        {
            "values": (
                record.journal_entry,
                record.date,
                record.account_code or "-",
                record.account_name or "-",
                f"{record.debit:,.2f}",
                f"{record.credit:,.2f}",
                f"{record.net:,.2f}",
                "ADA" if record.has_svl else "TIDAK ADA",
                record.reference,
            ),
            "tags": ("jnl_orphan",) if not record.has_svl else (),
            "meta": {
                "row_key": f"journal::{int(record.record_id or 0)}",
                "odoo_model": "account.move.line",
                "odoo_id": int(record.record_id or 0),
                "move_id": int(record.move_id or 0),
                "repair_candidate": False,
                "repair_seed": None,
            },
        }
        for record in detail.jnl_records
    ]


def _compare_po_rows(self, item: Any, detail: SvlDashboardItemDetail | None) -> list[dict[str, Any]]:
    if normalize_text(item.item_kind) == "unassigned_journal":
        return self._detail_placeholder_rows(self.compare_po_tree, "Perbandingan PO tidak berlaku untuk item journal tanpa product_id.")
    if detail is None:
        error_message = self._detail_error_for_item(item)
        if error_message:
            return self._detail_placeholder_rows(self.compare_po_tree, f"Gagal memuat detail item: {error_message}")
        return self._detail_placeholder_rows(self.compare_po_tree, self._detail_pending_message_for_item(item))
    if not detail.po_lines:
        return self._detail_placeholder_rows(self.compare_po_tree, "Tidak ada detail PO untuk item ini.")
    return [
        {
            "values": (
                row.po,
                f"{row.price_unit:,.2f}",
                f"{row.qty_received:,.2f}",
                f"{row.qty_invoiced:,.2f}",
                f"{row.value:,.2f}",
                row.status,
            ),
            "meta": {"row_key": f"po::{index}"},
        }
        for index, row in enumerate(detail.po_lines)
    ]


def _compare_bill_rows(self, item: Any, detail: SvlDashboardItemDetail | None) -> list[dict[str, Any]]:
    if normalize_text(item.item_kind) == "unassigned_journal":
        return self._detail_placeholder_rows(self.compare_bill_tree, "Perbandingan Bill tidak berlaku untuk item journal tanpa product_id.")
    if detail is None:
        error_message = self._detail_error_for_item(item)
        if error_message:
            return self._detail_placeholder_rows(self.compare_bill_tree, f"Gagal memuat detail item: {error_message}")
        return self._detail_placeholder_rows(self.compare_bill_tree, self._detail_pending_message_for_item(item))
    if not detail.bill_lines:
        return self._detail_placeholder_rows(self.compare_bill_tree, "Tidak ada detail bill untuk item ini.")
    return [
        {
            "values": (
                row.bill,
                row.po,
                row.date,
                f"{row.price_unit:,.2f}",
                f"{row.quantity:,.2f}",
                f"{row.value:,.2f}",
            ),
            "meta": {"row_key": f"bill::{index}"},
        }
        for index, row in enumerate(detail.bill_lines)
    ]


def _analysis_values_for_item(self, item: Any) -> tuple[float, float, float]:
    svl_orphan = float(item.svl_orphan_value or 0.0)
    jnl_orphan = float(item.jnl_orphan_value or 0.0)
    other_diff = float(item.difference or 0.0) - svl_orphan + jnl_orphan
    return (svl_orphan, jnl_orphan, other_diff)


def _merged_placeholder_rows(self, message: str) -> list[dict[str, Any]]:
    return [
        {
            "values": self._placeholder_values(tuple(self.merged_tree["columns"]), message),
            "meta": {
                "row_key": "merged::placeholder",
                "placeholder": True,
                "group_status": "Placeholder",
                "group_amount": 0.0,
            },
        }
    ]


def _render_tab_if_needed(self, tab_id: str, *, token: int | None = None, allow_autoload: bool = True) -> None:
    dirty_tabs = getattr(self, "_dirty_detail_tabs", set())
    if tab_id not in dirty_tabs:
        return
    item = getattr(self, "_current_detail_item", None)
    if item is None:
        return
    full_detail = self._full_detail_payload_for_item(item)
    detail = full_detail if tab_id == "compare" else self._detail_payload_for_item(item)
    if (
        self._tab_requires_lazy_detail(tab_id, item)
        and full_detail is None
        and allow_autoload
        and not self._detail_waits_for_warm_cache(item)
    ):
        self._ensure_item_detail_loaded(item, tab_id=tab_id)
    render_token = int(token if token is not None else getattr(self, "_detail_render_token", 0) or 0)
    if tab_id == "svl":
        self._render_plain_tree_in_batches(
            self.svl_tree,
            self._svl_tree_rows(item, detail),
            token=render_token,
            on_complete=lambda current_tab=tab_id, current_token=render_token: self._finish_detail_render(
                current_tab,
                token=current_token,
            ),
        )
        return
    elif tab_id == "jnl":
        self._render_plain_tree_in_batches(
            self.jnl_tree,
            self._journal_tree_rows(item, detail),
            token=render_token,
            on_complete=lambda current_tab=tab_id, current_token=render_token: self._finish_detail_render(
                current_tab,
                token=current_token,
            ),
        )
        return
    elif tab_id == "merged":
        if not allow_autoload:
            merged_rows = self._merged_placeholder_rows("Detail merged dimuat setelah tampilan awal siap.")
        else:
            merged_rows = [self._merged_row_payload(item, record) for record in getattr(item, "merged_records", [])]
        if not merged_rows:
            merged_rows = [
                {
                    "values": self._placeholder_values(tuple(self.merged_tree["columns"]), "Tidak ada detail gabungan untuk item ini."),
                    "meta": {
                        "row_key": "merged::placeholder",
                        "placeholder": True,
                        "group_status": "Tidak ada data",
                        "group_amount": 0.0,
                    },
                }
            ]
        self._render_grouped_merged_tree_in_batches(
            self.merged_tree,
            merged_rows,
            token=render_token,
            on_complete=lambda current_tab=tab_id, current_token=render_token: self._finish_detail_render(
                current_tab,
                token=current_token,
            ),
        )
        return
    elif tab_id == "compare":
        def _render_bill_tree() -> None:
            self._render_plain_tree_in_batches(
                self.compare_bill_tree,
                self._compare_bill_rows(item, detail),
                token=render_token,
                on_complete=lambda current_tab=tab_id, current_token=render_token: self._finish_detail_render(
                    current_tab,
                    token=current_token,
                ),
            )

        self._render_plain_tree_in_batches(
            self.compare_po_tree,
            self._compare_po_rows(item, detail),
            token=render_token,
            on_complete=_render_bill_tree,
        )
        return
    elif tab_id == "analysis":
        self.analysis_notice_var.set("Ringkasan komponen utama selisih per item.")
        self._render_analysis_bars(self._analysis_values_for_item(item))
        self._finish_detail_render(tab_id, token=render_token)


def _render_selected_item_summary(self, item: Any, detail_payload: SvlDashboardItemDetail | None) -> None:
    po_count, bill_count, total_po_value, total_bill_value = self._compare_summary_values(item, detail_payload)
    snapshot = self._latest_snapshot
    self.header_code_var.set(item.code or "NO CODE")
    self.header_name_var.set(item.name)
    if normalize_text(item.item_kind) == "unassigned_journal":
        self.header_meta_var.set("Pseudo product untuk journal valuation di akun persediaan yang tidak memiliki product_id.")
    else:
        self.header_meta_var.set(
            f"Kategori: {item.categ or '-'} | Cost Method: {(item.cost_method or '-').upper()} | Standard Price: {item.standard_price:,.2f}"
        )
    self.source_db_var.set(self._format_snapshot_source_database(snapshot.database if snapshot is not None else ""))
    position = self._item_position_label(item)
    self.position_var.set(position)
    self.position_badge.configure(bg=T.STATUS_SUCCESS if position == "DEBIT" else T.STATUS_ERROR)
    self.kpi_svl_var.set(f"{item.svl_value:,.2f}")
    self.kpi_bs_var.set(f"{item.bs_value:,.2f}")
    self.kpi_diff_var.set(f"{item.difference:,.2f}")
    self.kpi_po_bill_var.set(f"{(total_po_value - total_bill_value):,.2f}")
    if normalize_text(item.item_kind) == "unassigned_journal":
        self.kpi_sub_var.set(
            f"{self._item_record_count(item, 'jnl_record_count', 'jnl_records')} journal valuation tanpa product_id | {item.jnl_orphan_count} JE tanpa SVL"
        )
        self.compare_summary_var.set("Item sintetis ini hanya memiliki detail journal valuation; tab SVL dan PO/Bill tidak berlaku.")
        return
    self.kpi_sub_var.set(
        f"{item.svl_orphan_count} SVL tanpa JE | {item.jnl_orphan_count} JE tanpa SVL | {item.linked_empty_journal_count} JE header kosong | {po_count} PO"
    )
    if detail_payload is None and not self._detail_error_for_item(item):
        if self._detail_waits_for_warm_cache(item):
            self.compare_summary_var.set("Detail PO vs Bill sedang disiapkan di cache lokal.")
        else:
            self.compare_summary_var.set("Detail PO vs Bill dimuat setelah tampilan awal siap.")
        return
    if detail_payload is None:
        self.compare_summary_var.set(
            f"Gagal memuat detail PO vs Bill: {self._detail_error_for_item(item)}"
        )
        return
    self.compare_summary_var.set(
        f"Total PO: {total_po_value:,.2f} ({po_count} row) | "
        f"Total Bill: {total_bill_value:,.2f} ({bill_count} row) | "
        f"PO - Bill: {(total_po_value - total_bill_value):,.2f}"
    )


def _render_selected_item(self, *, summary_only: bool = False) -> None:
    items = self._filtered_items()
    item = next((entry for entry in items if entry.pid == self._selected_product_id), None)
    if item is None:
        self._set_empty_detail()
        return
    full_detail_payload = self._full_detail_payload_for_item(item)
    detail_payload = full_detail_payload or self._snapshot_detail_payload_for_item(item)
    self._selected_item_render_token = int(getattr(self, "_selected_item_render_token", 0) or 0) + 1
    self._render_selected_item_summary(item, full_detail_payload)
    self._current_detail_item = item
    self._current_detail_payload = detail_payload
    self._current_detail_repair_seeds_by_svl_id = {}
    self._dirty_detail_tabs = {"svl", "jnl", "merged", "compare", "analysis"}
    if summary_only:
        self._log_ui_stage("UI selected item summary rendered")
        return
    self._schedule_active_detail_render(allow_autoload=True)


def _fill_tree(self, tree: ttk.Treeview, rows: list[Any]) -> None:
    existing_meta = self._tree_row_meta.get(tree, {})
    existing_by_row_key: dict[str, str] = {}
    for item_id in tree.get_children():
        row_key = normalize_text(existing_meta.get(item_id, {}).get("row_key"))
        if row_key and row_key not in existing_by_row_key:
            existing_by_row_key[row_key] = item_id
    meta_map: dict[str, dict[str, Any]] = {}
    active_ids: list[str] = []
    for index, row in enumerate(rows):
        tags: tuple[str, ...] = ()
        meta: dict[str, Any] = {}
        if isinstance(row, dict):
            values = tuple(row.get("values") or ())
            tags = tuple(tag for tag in row.get("tags", ()) if tag)
            meta = dict(row.get("meta") or {})
        else:
            tag = row[-1] if row and isinstance(row[-1], str) and row[-1] in {"svl_orphan", "jnl_orphan"} else ""
            values = row[:-1] if tag else row
            tags = (tag,) if tag else ()
        row_key = normalize_text(meta.get("row_key")) or f"index::{index}"
        meta["row_key"] = row_key
        item_id = existing_by_row_key.pop(row_key, "")
        if item_id:
            tree.item(item_id, values=values, tags=tags)
            if tree.index(item_id) != index:
                tree.move(item_id, "", index)
        else:
            item_id = tree.insert("", index, values=values, tags=tags)
        meta_map[item_id] = meta
        active_ids.append(item_id)
    stale_ids = [item_id for item_id in tree.get_children() if item_id not in active_ids]
    if stale_ids:
        tree.delete(*stale_ids)
    self._tree_row_meta[tree] = meta_map
    active_cell = self._tree_active_cell.get(tree)
    if not active_cell or active_cell[0] not in meta_map:
        self._tree_active_cell[tree] = None


def _set_empty_company_summary(self) -> None:
    for variable in (
        self.company_total_svl_value_var,
        self.company_total_svl_qty_var,
        self.company_total_bs_var,
        self.company_total_diff_var,
        self.company_total_unmapped_var,
    ):
        variable.set("0.00")
    self.company_coa_total_var.set("Total Balance Persediaan: 0.00")
    if self._configured_inventory_coa_codes():
        self.company_coa_notice_var.set("Belum ada hasil analisis company summary.")
    else:
        self.company_coa_notice_var.set("Isi daftar COA persediaan manual di Settings untuk melihat summary Balance Sheet.")
    self._fill_tree(
        self.company_coa_tree,
        [self._placeholder_values(tuple(self.company_coa_tree["columns"]), "Belum ada detail akun company.")],
    )
    # Clear PCB raw table when no snapshot active
    if hasattr(self, "pcb_raw_tree"):
        _ch = self.pcb_raw_tree.get_children()
        if _ch:
            self.pcb_raw_tree.delete(*_ch)
    self._pcb_raw_current_cycle = None
    self._set_pcb_cycle_export_button_state(False)
    if hasattr(self, "_pcb_raw_notice_var"):
        self._pcb_raw_notice_var.set("Pilih cycle di sidebar untuk melihat data mentah transaksi.")
    if hasattr(self, "_pcb_partner_header_var"):
        self._pcb_partner_header_var.set("")
    if hasattr(self, "pcb_detail_tree"):
        _ch2 = self.pcb_detail_tree.get_children()
        if _ch2:
            self.pcb_detail_tree.delete(*_ch2)
    if hasattr(self, "_pcb_item_header_var"):
        self._pcb_item_header_var.set("")
    if hasattr(self, "pcb_acct_summary_tree"):
        _ch3 = self.pcb_acct_summary_tree.get_children()
        if _ch3:
            self.pcb_acct_summary_tree.delete(*_ch3)
    if hasattr(self, "header_code_var") and self._is_purchase_cycle_mode():
        self.header_code_var.set("")
    if hasattr(self, "header_name_var") and self._is_purchase_cycle_mode():
        self.header_name_var.set("Pilih cycle untuk melihat detail.")


def _apply_company_summary(self, snapshot: SvlDashboardSnapshot | None) -> None:
    if snapshot is None:
        self._set_empty_company_summary()
        return
    summary = snapshot.company_summary
    self.company_total_svl_value_var.set(f"{summary.total_svl_value:,.2f}")
    self.company_total_svl_qty_var.set(f"{summary.total_svl_qty:,.2f}")
    self.company_total_bs_var.set(f"{summary.inventory_bs_total:,.2f}")
    self.company_total_diff_var.set(f"{summary.difference:,.2f}")
    self.company_total_unmapped_var.set(f"{summary.unmapped_difference:,.2f}")
    self.company_coa_total_var.set(f"Total Balance Persediaan: {summary.inventory_bs_total:,.2f}")
    if summary.coa_rows:
        notice = f"{len(summary.coa_rows)} COA persediaan dimasukkan ke summary company."
        if summary.missing_codes:
            notice += f" Missing: {', '.join(summary.missing_codes)}"
        self.company_coa_notice_var.set(notice)
        self._fill_tree(
            self.company_coa_tree,
            [
                (row.code, row.name or "-", "", "", f"{row.balance:,.2f}")
                for row in summary.coa_rows
            ],
        )
        return
    if summary.missing_codes:
        self.company_coa_notice_var.set(
            f"COA persediaan manual tidak ditemukan di database aktif: {', '.join(summary.missing_codes)}"
        )
    elif self._configured_inventory_coa_codes():
        self.company_coa_notice_var.set("COA persediaan tersedia, tetapi tidak ada saldo untuk periode/company ini.")
    else:
        self.company_coa_notice_var.set("Isi daftar COA persediaan manual di Settings untuk melihat summary Balance Sheet.")
    self._fill_tree(
        self.company_coa_tree,
        [self._placeholder_values(tuple(self.company_coa_tree["columns"]), "Tidak ada detail COA persediaan company.")],
    )


def _build_sidebar_item_card(self, parent: tk.Widget, item: Any) -> None:
    selected = item.pid == self._selected_product_id
    frame = tk.Frame(
        parent,
        bg=T.BG_CARD,
        bd=1,
        relief="solid",
        highlightbackground=T.BRAND_PRIMARY if selected else T.BORDER_LIGHT,
        highlightthickness=1,
        padx=10,
        pady=6,
        cursor="hand2",
    )
    frame.pack(fill="x", pady=3)
    self._sidebar_item_frames[item.pid] = frame
    left = tk.Frame(frame, bg=T.BG_CARD)
    left.pack(fill="x")
    code_label = tk.Label(left, text=item.code or "-", bg=T.BG_CARD, fg=T.BRAND_PRIMARY, font=T.font(T.FONT_SMALL_SIZE, bold=True))
    code_label.pack(anchor="w")
    name_label = tk.Label(
        left,
        text=item.name,
        bg=T.BG_CARD,
        fg=T.TEXT_ON_LIGHT,
        font=T.font(T.FONT_BODY_SIZE, bold=True),
        anchor="w",
        justify="left",
        wraplength=260,
    )
    name_label.pack(anchor="w", fill="x", pady=(2, 4))
    bottom = tk.Frame(frame, bg=T.BG_CARD)
    bottom.pack(fill="x")
    primary_amount = self._item_primary_amount(item)
    diff_label = tk.Label(
        bottom,
        text=f"{primary_amount:,.2f}",
        bg=T.BG_CARD,
        fg=T.STATUS_SUCCESS if primary_amount >= 0 else T.STATUS_ERROR,
        font=T.font(T.FONT_BODY_SIZE, bold=True),
    )
    diff_label.pack(side="left")
    badge_frame = tk.Frame(bottom, bg=T.BG_CARD)
    badge_frame.pack(side="right")
    badge_labels: list[tk.Label] = []
    badge_specs = [
        (f"{item.svl_orphan_count} SVL", T.STATUS_ERROR if item.svl_orphan_count else ""),
        (f"{item.jnl_orphan_count} JNL", T.STATUS_SUCCESS if item.jnl_orphan_count else ""),
        (
            f"{self._item_record_count(item, 'po_line_count', 'po_lines')} PO",
            T.STATUS_WARNING if self._item_record_count(item, "po_line_count", "po_lines") else "",
        ),
    ]
    for text, color in badge_specs:
        if not color:
            continue
        lbl = tk.Label(
            badge_frame,
            text=text,
            bg=color,
            fg=T.TEXT_ON_DARK,
            padx=6,
            pady=2,
            font=T.font(T.FONT_SMALL_SIZE, bold=True),
        )
        lbl.pack(side="left", padx=(4, 0))
        badge_labels.append(lbl)
    # Collect explicitly — no winfo_children() query needed
    clickable_widgets = [frame, left, bottom, badge_frame, code_label, name_label, diff_label, *badge_labels]
    for widget in clickable_widgets:
        widget.bind("<Button-1>", lambda _event, pid=item.pid: self._select_item(pid))


def _build_sidebar_category_group(self, parent: tk.Widget, group: dict[str, Any]) -> tk.Frame:
    title = group["title"]
    state_key = normalize_text(group.get("state_key")) or title
    if state_key not in self._sidebar_category_open:
        self._sidebar_category_open[state_key] = True

    section = tk.Frame(
        parent,
        bg=T.BG_CARD,
        bd=1,
        relief="solid",
        highlightbackground=T.BORDER_LIGHT,
        highlightthickness=1,
    )
    section.pack(fill="x", padx=4, pady=3)

    header = tk.Frame(section, bg=T.BG_CARD, padx=8, pady=6)
    header.pack(fill="x")
    header.grid_columnconfigure(1, weight=1)

    summary_var = tk.StringVar(value="")
    toggle_button = tk.Button(
        header,
        bg=T.BRAND_PRIMARY,
        fg=T.TEXT_ON_DARK,
        activebackground=T.BRAND_PRIMARY_DARK,
        activeforeground=T.TEXT_ON_DARK,
        relief="flat",
        bd=0,
        highlightthickness=0,
        cursor="hand2",
        font=T.font(T.FONT_SMALL_SIZE, bold=True),
        padx=10,
        pady=2,
    )
    toggle_button.grid(row=0, column=0, sticky="nw")

    title_container = tk.Frame(header, bg=T.BG_CARD)
    title_container.grid(row=0, column=1, sticky="ew", padx=(8, 0))
    title_container.grid_columnconfigure(0, weight=1)

    title_label = tk.Label(
        title_container,
        text=title,
        bg=T.BG_CARD,
        fg=T.TEXT_ON_LIGHT,
        font=T.font(T.FONT_SMALL_SIZE, bold=True),
        anchor="w",
        justify="left",
        wraplength=220,
    )
    title_label.grid(row=0, column=0, sticky="ew")

    summary_label = tk.Label(
        title_container,
        textvariable=summary_var,
        bg=T.BG_CARD,
        fg=T.TEXT_MUTED,
        font=T.font(T.FONT_SMALL_SIZE),
        anchor="w",
        justify="left",
        wraplength=220,
    )
    summary_label.grid(row=1, column=0, sticky="ew", pady=(2, 0))

    divider = tk.Frame(section, bg=T.BORDER_LIGHT, height=1)
    body = tk.Frame(section, bg=T.BG_CARD, padx=8, pady=6)
    for item in group["items"]:
        self._build_sidebar_item_card(body, item)

    summary_text = self._format_category_summary(group)

    def refresh_header_wrap(_event: tk.Event | None = None) -> None:
        header_width = max(header.winfo_width(), section.winfo_width(), 240)
        button_width = max(toggle_button.winfo_width(), 96)
        available_width = max(120, header_width - button_width - 36)
        title_label.configure(wraplength=available_width)
        summary_label.configure(wraplength=available_width)

    def apply_visibility(is_open: bool) -> None:
        toggle_button.configure(text="Hide" if is_open else "Show More")
        if is_open:
            summary_var.set("")
            summary_label.grid_remove()
            divider.pack(fill="x")
            body.pack(fill="x")
            return
        summary_var.set(summary_text)
        summary_label.grid()
        body.pack_forget()
        divider.pack_forget()

    def toggle_group() -> None:
        next_state = not bool(self._sidebar_category_open.get(state_key, True))
        self._sidebar_category_open[state_key] = next_state
        apply_visibility(next_state)

    toggle_button.configure(command=toggle_group)
    apply_visibility(bool(self._sidebar_category_open.get(state_key, True)))
    header.bind("<Configure>", refresh_header_wrap)
    title_container.bind("<Configure>", refresh_header_wrap)
    section.after(0, refresh_header_wrap)
    section._sidebar_body = body  # type: ignore[attr-defined]
    section._sidebar_toggle_button = toggle_button  # type: ignore[attr-defined]
    section._sidebar_summary_var = summary_var  # type: ignore[attr-defined]
    section._sidebar_title_label = title_label  # type: ignore[attr-defined]
    section._sidebar_summary_label = summary_label  # type: ignore[attr-defined]
    return section


def _build_sidebar_valuation_group(self, parent: tk.Widget, group: dict[str, Any]) -> tk.Frame:
    title = normalize_text(group.get("title")) or "Unknown"
    section = tk.Frame(
        parent,
        bg=T.BG_CARD,
        bd=1,
        relief="solid",
        highlightbackground=T.BORDER_LIGHT,
        highlightthickness=1,
        padx=8,
        pady=8,
    )
    section.pack(fill="x", padx=4, pady=4)

    header = tk.Frame(section, bg=T.BG_CARD)
    header.pack(fill="x", pady=(0, 6))
    tk.Label(
        header,
        text=title,
        bg=T.BG_CARD,
        fg=T.TEXT_ON_LIGHT,
        font=T.font(T.FONT_BODY_SIZE, bold=True),
        anchor="w",
    ).pack(side="left")
    tk.Label(
        header,
        text=self._format_valuation_summary(group),
        bg=T.BG_CARD,
        fg=T.TEXT_MUTED,
        font=T.font(T.FONT_SMALL_SIZE),
        anchor="e",
    ).pack(side="right")

    body = tk.Frame(section, bg=T.BG_CARD)
    body.pack(fill="x")
    for category_group in group.get("categories", []):
        self._build_sidebar_category_group(body, category_group)

    section._sidebar_body = body  # type: ignore[attr-defined]
    return section


def render_item_cards(
    self,
    *,
    on_complete: "Callable[[], None] | None" = None,
    selected_render_mode: str = "full",
    log_sidebar_stage: bool = False,
) -> None:
    if self._is_purchase_cycle_mode():
        self._render_purchase_cycle_sidebar(on_complete=on_complete)
        return
    self._sidebar_item_frames.clear()
    items = self._filtered_items()
    self.sidebar_summary_var.set(self._format_sidebar_summary(items))
    if not items:
        self._clear_sidebar_tree()
        self._sidebar_all_item_ids = None
        self._set_empty_detail()
        if on_complete is not None:
            on_complete()
        return
    if not any(item.pid == self._selected_product_id for item in items):
        self._selected_product_id = items[0].pid

    if self._sidebar_all_item_ids is None or not self.sidebar_tree.get_children():
        self._clear_sidebar_tree()

        def _on_rebuild_complete() -> None:
            self._sidebar_all_item_ids = dict(self._sidebar_tree_item_id_by_pid)
            self._sync_sidebar_selection(ensure_visible=False)
            self._render_selected_item(summary_only=selected_render_mode == "summary")
            if on_complete is not None:
                on_complete()

        self._rebuild_sidebar_tree(items, on_complete=_on_rebuild_complete, log_stage=log_sidebar_stage)
    else:
        self._filter_sidebar_tree_in_place(items)
        self._sync_sidebar_selection(ensure_visible=False)
        self._render_selected_item(summary_only=selected_render_mode == "summary")
        if on_complete is not None:
            on_complete()

# ── Purchase Cycle Balance sidebar ────────────────────────────────────────

_PCB_STATUS_ICON = {"problem": "❌", "partial": "⚠️", "healthy": "✅"}
_PCB_STATUS_LABEL = {"problem": "Cycle Bermasalah", "partial": "Cycle Sebagian", "healthy": "Cycle Sehat"}
_PCB_PARTIAL_GROUP_ORDER = ["clearing_reclass", "bill_unpaid", "bill_unmatched", "payment_unreconciled", "partial_info", "partial_other"]
_PCB_PARTIAL_GROUP_LABEL = {
    "clearing_reclass": "Clearing via Jurnal Reclass",
    "bill_unpaid": "Bill Belum Paid",
    "bill_unmatched": "Bill Belum Matching",
    "payment_unreconciled": "Payment Belum Reconcile",
    "partial_info": "Info Account / Variance",
    "partial_other": "Partial Lainnya",
}

_PCB_PROBLEM_CASE_ORDER = [
    "case1",
    "case2",
    "case3",
    "case4",
    "case5",
    "case6",
    "case8a",
    "case8b",
    "case9",
    "edge_partial_bill",
    "edge_return_no_credit_memo",
    "edge_stj_corrupt",
    "case_lainnya",
]
_PCB_PROBLEM_CASE_LABEL = {
    "case1": "Case 1 - STJ Bill Miss Match (Clearing - Suspend)",
    "case2": "Case 2 - STJ Bill Price Diff (Suspend - Suspend)",
    "case3": "Case 3 - STJ Bill Hit Expenses (Clearing - Expenses)",
    "case4": "Case 4 - STJ Bill Hit Expenses (Suspend - Expenses)",
    "case5": "Case 5 - Pemulihan SVL (Inventory - Suspense)",
    "case6": "Case 6 - Pemulihan SVL (Inventory - Bill Expense)",
    "case8a": "Case 8A - Full Return Value Mismatch",
    "case8b": "Case 8B - Partial Return Value Mismatch",
    "case9": "Case 9 - UoM Scale Mismatch",
    "edge_partial_bill": "Edge - Partial Bill",
    "edge_return_no_credit_memo": "Edge - Return No Credit Memo",
    "edge_stj_corrupt": "Edge - STJ Corrupt",
    "case_lainnya": "Case Lainnya",
}
_PCB_COGS_VARIANCE_CODES: "frozenset[str]" = frozenset({"5101010"})
_PCB_SIMULATION_FIXED_ACCOUNTS: "dict[str, tuple[str, str, str]]" = {
    "1108099": ("Clearing - System Pending Entries", "PCB Clearing Default", "other"),
    "2103006": ("Hutang Suspensed Pengadaan Barang/Jasa / Suspensed", "PCB Suspend Default", "other"),
}

def _filter_sidebar_tree_in_place(self, visible_items: list[Any]) -> None:
    """Detach/reattach sidebar items without destroying them.

    Uses pre-computed attached-children sets to avoid O(n²) Tcl round-trips.
    Processes groups bottom-up (items → categories → valuations) so that empty
    parent detection works correctly after children are detached.
    """
    visible_pids = {item.pid for item in visible_items}
    tree = self.sidebar_tree
    all_item_ids = self._sidebar_all_item_ids or {}

    # Pre-compute set of currently attached children per parent — one Tcl call per parent.
    parent_ids_of_items: set[str] = {
        self._sidebar_tree_meta.get(iid, {}).get("_parent_id", "")
        for item_ids in all_item_ids.values()
        for iid in item_ids
    }
    attached_by_parent: dict[str, set[str]] = {}
    for parent_id in parent_ids_of_items:
        try:
            attached_by_parent[parent_id] = set(tree.get_children(parent_id))
        except tk.TclError:
            attached_by_parent[parent_id] = set()

    # Pass 1: detach/reattach leaf items.
    for pid, item_ids in all_item_ids.items():
        for item_id in item_ids:
            meta = self._sidebar_tree_meta.get(item_id, {})
            parent_id = meta.get("_parent_id", "")
            attached = attached_by_parent.setdefault(parent_id, set())
            if pid not in visible_pids:
                if item_id in attached:
                    try:
                        tree.detach(item_id)
                        attached.discard(item_id)
                    except tk.TclError:
                        pass
            else:
                if item_id not in attached:
                    try:
                        tree.reattach(item_id, parent_id, "end")
                        attached.add(item_id)
                    except tk.TclError:
                        pass

    groups = [
        (gid, meta)
        for gid, meta in self._sidebar_tree_meta.items()
        if normalize_text(meta.get("kind")) in {"valuation", "category", "group"}
    ]
    groups.sort(key=lambda entry: int((entry[1] or {}).get("level") or 0), reverse=True)
    for group_id, meta in groups:
        parent_id = meta.get("_parent_id", "")
        try:
            has_children = bool(tree.get_children(group_id))
        except tk.TclError:
            continue
        parent_attached = attached_by_parent.setdefault(parent_id, set())
        if not has_children:
            if group_id in parent_attached:
                try:
                    tree.detach(group_id)
                    parent_attached.discard(group_id)
                except tk.TclError:
                    pass
        else:
            if group_id not in parent_attached:
                try:
                    tree.reattach(group_id, parent_id, "end")
                    parent_attached.add(group_id)
                except tk.TclError:
                    pass


def _on_database_selected(self, _event: tk.Event | None = None) -> None:
    previous_profile = self._module_settings.database_profile_id
    self._refresh_effective_database_display()
    self._save_settings()
    if self._selected_database_profile_id() != previous_profile:
        self._companies_loaded_for_profile_id = ""
        self._clear_snapshot(reset_company=True)
    self.load_companies(force=False)


def _refresh_dataset_mode_widgets(self) -> None:
    is_pcb = self._is_purchase_cycle_mode()
    if hasattr(self, "_detail_tab_buttons"):
        # PCB reuses the hidden current_asset frame for its detail trees
        # In PCB mode: all tabs hidden — detail akun now lives in _pcb_summary_outer
        allowed_tabs = set() if is_pcb else set(self._detail_tab_buttons)
        for tab_id, button in self._detail_tab_buttons.items():
            if tab_id in allowed_tabs:
                button.grid()
            else:
                button.grid_remove()
    if hasattr(self, "_inventory_filter_frame"):
        if is_pcb:
            self._inventory_filter_frame.pack_forget()
        else:
            self._inventory_filter_frame.pack(
                side="left", padx=(16, 0), after=self.btn_analyze
            )
    if hasattr(self, "_pcb_company_export_btn"):
        if is_pcb:
            self._pcb_company_export_btn.pack(side="left", padx=(8, 0))
        else:
            self._pcb_company_export_btn.pack_forget()
    if hasattr(self, "sidebar_card_title_var"):
        if is_pcb:
            self.sidebar_card_title_var.set("List Item Cycle Pembelian")
        else:
            self.sidebar_card_title_var.set("SVL vs Balance Sheet")
    # Switch sidebar tree row-height style: compact for PCB, normal otherwise
    if hasattr(self, "sidebar_tree"):
        _new_style = PCB_SIDEBAR_TREE_STYLE if is_pcb else SIDEBAR_TREE_STYLE
        try:
            self.sidebar_tree.configure(style=_new_style)
        except Exception:  # noqa: BLE001
            pass
    if hasattr(self, "detail_card_title_var"):
        if is_pcb:
            self.detail_card_title_var.set("Detail Saldo Akun per Cycle")
        else:
            self.detail_card_title_var.set("Detail Produk")
    if hasattr(self, "kpi_svl_title_var"):
        self.kpi_svl_title_var.set("Total Debit Akun" if is_pcb else "Nilai SVL")
    if hasattr(self, "kpi_bs_title_var"):
        self.kpi_bs_title_var.set("Total Kredit Akun" if is_pcb else "Saldo Balance Sheet")
    if hasattr(self, "kpi_diff_title_var"):
        if is_pcb:
            self.kpi_diff_title_var.set("Akun Bermasalah")
        else:
            self.kpi_diff_title_var.set("Selisih (SVL - BS)")
    # Reset per-cycle KPI value to avoid showing stale count from previous selection
    if is_pcb and hasattr(self, "kpi_diff_var"):
        self.kpi_diff_var.set("—")
    if hasattr(self, "kpi_po_bill_title_var"):
        if is_pcb:
            self.kpi_po_bill_title_var.set("Cycle Bermasalah")
        else:
            self.kpi_po_bill_title_var.set("PO - Bill")
    if hasattr(self, "company_total_primary_title_var"):
        self.company_total_primary_title_var.set("Total Debit Akun" if is_pcb else "Total SVL")
    if hasattr(self, "company_total_primary_secondary_label_var"):
        if is_pcb:
            self.company_total_primary_secondary_label_var.set("Cycle")
        else:
            self.company_total_primary_secondary_label_var.set("Qty")
    if hasattr(self, "company_total_secondary_title_var"):
        self.company_total_secondary_title_var.set("Total Kredit Akun" if is_pcb else "Total BS Persediaan")
    if hasattr(self, "company_total_diff_title_var"):
        self.company_total_diff_title_var.set("Net Saldo Akun" if is_pcb else "Selisih Total (SVL - BS)")
    if hasattr(self, "company_total_aux_title_var"):
        if is_pcb:
            self.company_total_aux_title_var.set("Cycle Bermasalah")
        else:
            self.company_total_aux_title_var.set("Belum Terpetakan")
    if hasattr(self, "company_coa_title_var"):
        if is_pcb:
            self.company_coa_title_var.set("Ringkasan Cycle Pembelian")
        else:
            self.company_coa_title_var.set("COA Persediaan Company")
    if hasattr(self, "analysis_notice_var"):
        if is_pcb:
            self.analysis_notice_var.set("Tab ini tidak berlaku untuk mode Balance Cycle Pembelian.")
        else:
            self.analysis_notice_var.set("Ringkasan komponen utama selisih per item.")
    if hasattr(self, "analysis_label_vars") and len(self.analysis_label_vars) >= 3:
        labels = (
            ("Tidak Berlaku", "Tidak Berlaku", "Tidak Berlaku")
            if is_pcb
            else ("SVL tanpa Journal Entry", "Journal Entry tanpa SVL", "Selisih lainnya")
        )
        for variable, label in zip(self.analysis_label_vars, labels):
            variable.set(label)
    # Show/hide PCB filter checkboxes + UoM filter row
    if hasattr(self, "_pcb_filter_frame"):
        if self._is_purchase_cycle_mode():
            self._pcb_filter_frame.grid()
        else:
            self._pcb_filter_frame.grid_remove()
    if hasattr(self, "_pcb_uom_filter_frame"):
        if self._is_purchase_cycle_mode():
            self._pcb_uom_filter_frame.grid()
        else:
            self._pcb_uom_filter_frame.grid_remove()
    # Toggle KPI cards vs PCB account summary; hide/show header elements
    if hasattr(self, "kpi_frame") and hasattr(self, "_pcb_acct_summary_frame"):
        if is_pcb:
            self.kpi_frame.grid_remove()
            self._pcb_acct_summary_frame.grid()
        else:
            self._pcb_acct_summary_frame.grid_remove()
            self.kpi_frame.grid()
    # Hide entire tab host in PCB mode (no tabs needed; detail is in _pcb_summary_outer)
    if hasattr(self, "_detail_tab_host"):
        if is_pcb:
            self._detail_tab_host.grid_remove()
        else:
            self._detail_tab_host.grid()
    if hasattr(self, "position_badge"):
        if is_pcb:
            self.position_badge.pack_forget()
        else:
            self.position_badge.pack(side="right")
    if hasattr(self, "_header_source_db_lbl"):
        if is_pcb:
            self._header_source_db_lbl.pack_forget()
        else:
            self._header_source_db_lbl.pack(anchor="w", pady=(2, 0))
    if hasattr(self, "_header_meta_lbl"):
        if is_pcb:
            self._header_meta_lbl.pack_forget()
        else:
            self._header_meta_lbl.pack(anchor="w")
    if hasattr(self, "_kpi_sub_lbl"):
        if is_pcb:
            self._kpi_sub_lbl.grid_remove()
        else:
            self._kpi_sub_lbl.grid()
    # Reset PCB header to empty when switching into PCB mode
    if is_pcb:
        if hasattr(self, "header_code_var"):
            self.header_code_var.set("")
        if hasattr(self, "header_name_var"):
            self.header_name_var.set("Pilih cycle untuk melihat detail.")
    self._configure_company_coa_tree()
    # Toggle company summary card vs PCB raw summary card
    # PCB mode: sidebar (content_pane) at TOP with compact height, detail tables below.
    # Non-PCB mode: company summary above content_pane, content_pane expands to fill.
    if hasattr(self, "_company_summary_outer") and hasattr(self, "_pcb_summary_outer"):
        if is_pcb:
            self._company_summary_outer.pack_forget()
            # Sidebar compact at top: content_pane no longer expands
            self.content_pane.pack_configure(fill="x", expand=False, pady=(0, 8))
            # Detail tables section below sidebar, above logs, expands to fill remaining space
            if not self._pcb_summary_outer.winfo_ismapped():
                _before_widget = getattr(self, "dashboard_log_section", None)
                if _before_widget is not None:
                    self._pcb_summary_outer.pack(fill="both", expand=True, pady=(0, 8), before=_before_widget)
                else:
                    self._pcb_summary_outer.pack(fill="both", expand=True, pady=(0, 8))
            # Trigger height sync so sidebar gets its compact 280px height
            self._last_content_height = -1
            self.root.after_idle(self._sync_content_height)
        else:
            self._pcb_summary_outer.pack_forget()
            # Restore content_pane to full-expand mode
            self.content_pane.pack_configure(fill="both", expand=True, pady=(0, 8))
            if not self._company_summary_outer.winfo_ismapped():
                self._company_summary_outer.pack(fill="x", pady=(0, 8), before=self.content_pane)
            # Trigger height sync to restore full canvas height
            self._last_content_height = -1
            self.root.after_idle(self._sync_content_height)


def _on_dataset_mode_selected(self, _event: tk.Event | None = None) -> None:
    self._save_settings()
    self._refresh_dataset_mode_widgets()
    self._clear_snapshot(reset_company=False)
    self.status_var.set("Mode dataset berubah. Pilih company lalu jalankan Analyze.")


def _on_company_selected(self, _event: tk.Event | None = None) -> None:
    self._schedule_company_commit()


def _on_logs_section_toggled(self, _key: str, is_open: bool) -> None:
    latest = self._state_store.load()
    latest.dashboard_logs_section_open = bool(is_open)
    self._module_settings = latest
    self._state_store.save(latest)


def load_companies(self, *, force: bool) -> None:
    profile_id = self._selected_database_profile_id()
    if self._busy:
        return
    if not force and self._company_by_label and self._companies_loaded_for_profile_id == profile_id:
        return
    db_state = self._resolve_effective_database_state(selected_profile_id=profile_id)
    self._set_busy(True)
    self.status_var.set(f"Memuat company dari Odoo... ({db_state['source_text']})")
    self.ui_queue.put({"type": "busy", "value": True})

    def worker() -> None:
        try:
            self.log_queue.put(f"Database source: {db_state['effective_display']}")
            settings, config = self._runtime_builder(
                self.context.global_settings,
                self.logger,
                database_profile_id=profile_id,
            )
            self.log_queue.put(f"Connecting to {config.base_url} [{config.database}]...")

            async def _run() -> list[SvlDashboardCompany]:
                async with AsyncOdooJsonRpcClient(config=config, settings=settings, logger=self.logger) as rpc:
                    service = SvlDashboardServiceAsync(
                        rpc=rpc,
                        logger=self.logger,
                        on_log=self.log_queue.put,
                    )
                    return await service.list_companies()

            companies = asyncio.run(_run())
            self._companies_loaded_for_profile_id = profile_id
            self.ui_queue.put({"type": "companies", "companies": companies})
        except Exception as exc:  # noqa: BLE001
            self.log_queue.put(f"ERROR: {exc}")
            self.ui_queue.put({"type": "error", "message": str(exc)})

    self.worker = threading.Thread(target=worker, daemon=True, name="svl-dashboard-companies")
    self.worker.start()


def start_analysis(self) -> None:
    company_id = self._selected_company_id()
    if company_id <= 0:
        messagebox.showwarning("Dashboard Control", "Pilih company terlebih dahulu.")
        return
    if self._busy:
        return
    if not self._repair_refresh_pending:
        self._recent_repair_results_by_svl_id = {}
    self._save_settings()
    self._set_busy(True)
    self._apply_progress(SvlDashboardProgressSnapshot(phase="start", processed=0, total=1, current="Memulai analisis...", progress=0.0))
    db_state = self._resolve_effective_database_state(selected_profile_id=self._selected_database_profile_id())
    self.status_var.set(f"Menjalankan analisis dashboard... ({db_state['source_text']})")
    self._latest_snapshot = None
    self._latest_analysis_request = None
    self._latest_analysis_profile_id = ""
    self._detail_warm_state = "idle"
    self._detail_warm_target_keys = set()
    self._detail_cache.clear()
    self._detail_pending_keys.clear()
    self._detail_error_by_key.clear()
    self._detail_session_token = int(getattr(self, "_detail_session_token", 0) or 0) + 1
    self._current_detail_payload = None
    self._refresh_busy_state()

    profile_id = self._selected_database_profile_id()
    request = SvlDashboardRequest(
        database="",
        company_id=company_id,
        date_from=normalize_text(self.date_from_var.get()),
        date_to=normalize_text(self.date_to_var.get()),
        inventory_coa_codes=self._configured_inventory_coa_codes(),
        dataset_mode=self._selected_dataset_mode(),
        include_inventory_accounts=bool(self.include_inventory_accounts_var.get()),
        include_non_inventory_accounts=bool(self.include_non_inventory_accounts_var.get()),
        pcb_problem_codes=frozenset(
            c.strip() for c in self._pcb_problem_codes_var.get().split(",") if c.strip()
        ) if hasattr(self, "_pcb_problem_codes_var") else frozenset(),
        pcb_info_codes=frozenset(
            c.strip() for c in self._pcb_info_codes_var.get().split(",") if c.strip()
        ) if hasattr(self, "_pcb_info_codes_var") else frozenset(),
    )

    def worker() -> None:
        bench_mode = normalize_text(request.dataset_mode) or "default"
        worker_started = perf_counter()
        try:
            self.log_queue.put(f"Database source: {db_state['effective_display']}")
            settings, config = self._runtime_builder(
                self.context.global_settings,
                self.logger,
                database_profile_id=profile_id,
            )
            request.database = config.database
            self.log_queue.put(f"Connecting to {config.base_url} [{config.database}]...")

            async def _run() -> SvlDashboardSnapshot:
                async with AsyncOdooJsonRpcClient(config=config, settings=settings, logger=self.logger) as rpc:
                    service = SvlDashboardServiceAsync(
                        rpc=rpc,
                        logger=self.logger,
                        on_log=self.log_queue.put,
                        on_progress=lambda snapshot: self.ui_queue.put({"type": "progress", "snapshot": snapshot}),
                    )
                    analyze_started = perf_counter()
                    try:
                        return await service.analyze(request)
                    finally:
                        self.log_queue.put(
                            f"[BENCH] service.analyze[{bench_mode}]: {(perf_counter() - analyze_started) * 1000.0:.0f}ms"
                        )

            snapshot = asyncio.run(_run())
            self.ui_queue.put(
                {
                    "type": "snapshot",
                    "snapshot": snapshot,
                    "base_url": config.base_url,
                    "request": request,
                    "database_profile_id": profile_id,
                }
            )
        except Exception as exc:  # noqa: BLE001
            self.log_queue.put(f"ERROR: {exc}")
            self.ui_queue.put({"type": "error", "message": str(exc)})
        finally:
            self.log_queue.put(
                f"[BENCH] worker total[{bench_mode}]: {(perf_counter() - worker_started) * 1000.0:.0f}ms"
            )

    self.worker = threading.Thread(target=worker, daemon=True, name="svl-dashboard-analyze")
    self.worker.start()


def _export_snapshot(self, kind: str) -> None:
    snapshot = self._latest_snapshot
    request = self._latest_analysis_request
    if snapshot is None or request is None:
        return
    suffix = "html" if kind == "html" else "json"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    initial_dir = normalize_text(self._module_settings.last_output_dir) or normalize_text(self.context.global_settings.default_output_dir)
    filename = f"svl_dashboard_company_{snapshot.company_id}_{timestamp}.{suffix}"
    selected = filedialog.asksaveasfilename(
        title=f"Export {kind.upper()} Dashboard",
        defaultextension=f".{suffix}",
        initialdir=initial_dir or None,
        initialfile=filename,
        filetypes=[(f"{kind.upper()} File", f"*.{suffix}")],
    )
    if not selected:
        return
    self._set_busy(True)
    self.phase_var.set("Menyiapkan export dashboard...")
    self.status_var.set(f"Menyiapkan export {kind.upper()} dashboard...")

    profile_id = normalize_text(self._latest_analysis_profile_id) or self._selected_database_profile_id()
    request_copy = SvlDashboardRequest(
        database=normalize_text(request.database),
        company_id=int(request.company_id or 0),
        date_from=normalize_text(request.date_from),
        date_to=normalize_text(request.date_to),
        inventory_coa_codes=list(request.inventory_coa_codes or []),
        dataset_mode=normalize_dashboard_dataset_mode(getattr(request, "dataset_mode", "")),
        include_inventory_accounts=bool(getattr(request, "include_inventory_accounts", True)),
        include_non_inventory_accounts=bool(getattr(request, "include_non_inventory_accounts", True)),
    )

    def worker() -> None:
        try:
            settings, config = self._runtime_builder(
                self.context.global_settings,
                self.logger,
                database_profile_id=profile_id,
            )

            async def _run() -> SvlDashboardSnapshot:
                async with AsyncOdooJsonRpcClient(config=config, settings=settings, logger=self.logger) as rpc:
                    service = SvlDashboardServiceAsync(
                        rpc=rpc,
                        logger=self.logger,
                        on_log=self.log_queue.put,
                    )
                    return await service.hydrate_snapshot_details(request_copy, snapshot)

            hydrated_snapshot = asyncio.run(_run())
            if kind == "html":
                path = export_dashboard_html(hydrated_snapshot, selected)
            else:
                path = export_dashboard_json(hydrated_snapshot, selected)
            self.ui_queue.put({"type": "export_complete", "path": str(path)})
        except Exception as exc:  # noqa: BLE001
            self.log_queue.put(f"ERROR: {exc}")
            self.ui_queue.put({"type": "export_error", "message": str(exc)})

    threading.Thread(target=worker, daemon=True, name=f"svl-dashboard-export-{kind}").start()

# ── PCB Repair Dialog ─────────────────────────────────────────────────────
