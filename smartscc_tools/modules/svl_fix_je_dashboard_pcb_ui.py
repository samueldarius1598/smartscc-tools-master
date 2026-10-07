"""Lazy-loaded PCB repair UI helpers for SVL Fix JE dashboard page."""

from __future__ import annotations

from smartscc_tools.modules import svl_fix_je_dashboard_page as page_mod


def _sync_page_globals() -> None:
    for _name, _value in page_mod.__dict__.items():
        if _name.startswith("__"):
            continue
        globals()[_name] = _value


_sync_page_globals()


def _open_pcb_case1_repair_dialog_v2(self) -> None:
    _sync_page_globals()
    latest_snapshot = getattr(self, "_latest_snapshot", None)
    if latest_snapshot is not None:
        self._reconcile_pcb_case1_collection_with_snapshot(latest_snapshot)
    col = getattr(self, "_pcb_repair_collection", {})
    if not col:
        messagebox.showwarning(
            self._display_name,
            "PCB Repair Collection masih kosong.\nTambahkan cycle bermasalah yang actionable terlebih dahulu via klik kanan sidebar.",
        )
        return
    scope = getattr(self, "_pcb_repair_collection_scope", None)
    current = self._current_repair_collection_scope()
    if scope is not None and current is not None and not scope.matches(current):
        messagebox.showwarning(
            self._display_name,
            f"PCB Repair Collection aktif di scope lain: {self._pcb_collection_scope_text() or '-'}.\n"
            "Kembali ke scope asal atau clear collection terlebih dahulu.",
        )
        return

    dialog = tk.Toplevel(self.root)
    dialog.title(f"PCB Repair Collection - {len(col)} row")
    dialog.geometry("1480x760")
    dialog.minsize(1120, 620)
    dialog.transient(self.root)
    dialog.grab_set()
    dialog.configure(bg=T.BG_CARD)
    dialog.rowconfigure(0, weight=1)
    dialog.columnconfigure(0, weight=1)

    default_split_ratio = 0.60
    min_left_width = 560
    min_right_width = 420
    split_state = {"manual_ratio": None, "sync_pending": False}

    main = tk.Frame(dialog, bg=T.BG_CARD)
    main.grid(row=0, column=0, sticky="nsew")
    main.rowconfigure(1, weight=1)
    main.columnconfigure(0, weight=1)

    header = tk.Frame(main, bg=T.BG_CARD, padx=10, pady=8)
    header.grid(row=0, column=0, sticky="ew")
    tk.Label(
        header,
        text="PCB Repair Collection",
        bg=T.BG_CARD,
        fg=T.TEXT_ON_LIGHT,
        font=T.font(T.FONT_BODY_SIZE + 1, bold=True),
    ).pack(side="left")
    scope_text = self._pcb_collection_scope_text()
    if scope_text:
        tk.Label(
            header,
            text=f"[{scope_text}]",
            bg=T.BG_CARD,
            fg=T.TEXT_MUTED,
            font=T.font(T.FONT_SMALL_SIZE),
        ).pack(side="left", padx=(8, 0))

    pane = tk.PanedWindow(
        main,
        orient="horizontal",
        sashrelief="flat",
        sashwidth=6,
        bg=T.BG_CARD,
        bd=0,
        opaqueresize=True,
    )
    pane.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 2))

    left = tk.Frame(pane, bg=T.BG_CARD)
    left.grid_propagate(False)
    left.columnconfigure(0, weight=1)
    left.rowconfigure(0, weight=1)
    left.rowconfigure(1, weight=0)

    tv_cols = ("picking", "item", "source", "po", "bill", "amount", "accounts", "reconcile", "status")
    tv_labels = {
        "picking": "Picking",
        "item": "Item",
        "source": "Source",
        "po": "PO",
        "bill": "Bill",
        "amount": "Amount",
        "accounts": "JE Preview",
        "reconcile": "Guard / Reconcile",
        "status": "Status",
    }
    tv_widths = {
        "picking": 180,
        "item": 260,
        "source": 140,
        "po": 160,
        "bill": 200,
        "amount": 100,
        "accounts": 160,
        "reconcile": 160,
        "status": 110,
    }
    tv_minwidths = {
        "picking": 120,
        "item": 180,
        "source": 100,
        "po": 120,
        "bill": 150,
        "amount": 90,
        "accounts": 120,
        "reconcile": 120,
        "status": 90,
    }
    tv = ttk.Treeview(left, columns=tv_cols, show="tree headings", selectmode="extended")
    tv.heading("#0", text="Case")
    tv.column("#0", width=200, minwidth=160, anchor="w", stretch=True)
    for column_name in tv_cols:
        tv.heading(column_name, text=tv_labels[column_name])
        tv.column(
            column_name,
            width=tv_widths[column_name],
            minwidth=tv_minwidths[column_name],
            anchor="w",
            stretch=column_name in {"picking", "item", "po", "bill", "accounts", "reconcile"},
        )
    tv.tag_configure("group", foreground=T.TEXT_ON_LIGHT)
    tv.tag_configure("ready", foreground=T.TEXT_ON_LIGHT)
    tv.tag_configure("needs_review", foreground="#B26A00")
    tv.tag_configure("repaired", foreground=T.STATUS_SUCCESS)
    tv.tag_configure("error", foreground=T.STATUS_ERROR)

    scrollbar = ttk.Scrollbar(left, orient="vertical", command=tv.yview)
    x_scrollbar = ttk.Scrollbar(left, orient="horizontal", command=tv.xview)
    tv.configure(yscrollcommand=scrollbar.set, xscrollcommand=x_scrollbar.set)
    tv.grid(row=0, column=0, sticky="nsew")
    scrollbar.grid(row=0, column=1, sticky="ns")
    x_scrollbar.grid(row=1, column=0, sticky="ew", pady=(2, 0))

    right_host = tk.Frame(pane, bg=T.BG_CARD)
    right_host.grid_propagate(False)
    right_host.columnconfigure(0, weight=1)
    right_host.rowconfigure(0, weight=1)

    right_scroll = ScrollableFrame(right_host, bg=T.BG_CARD)
    right_scroll.grid(row=0, column=0, sticky="nsew")
    right = tk.Frame(right_scroll.interior, bg=T.BG_CARD, padx=12, pady=10)
    right.pack(fill="both", expand=True)
    right.columnconfigure(1, weight=1)

    pane.add(left, minsize=min_left_width, stretch="always")
    pane.add(right_host, minsize=min_right_width, stretch="never")

    def measured_split_width() -> int:
        current_width = max(int(pane.winfo_width() or 0), int(dialog.winfo_width() or 0))
        if current_width > 1:
            return current_width
        configured_width = int(pane.cget("width") or 0)
        if configured_width > 1:
            return configured_width
        geometry_text = normalize_text(dialog.wm_geometry() or dialog.geometry())
        geometry_match = re.match(r"^(?P<width>\d+)x(?P<height>\d+)", geometry_text)
        if geometry_match is not None:
            geometry_width = int(geometry_match.group("width") or 0)
            if geometry_width > 1:
                return geometry_width
        requested_width = max(int(pane.winfo_reqwidth() or 0), int(dialog.winfo_reqwidth() or 0))
        if requested_width > 1:
            return requested_width
        return min_left_width + min_right_width

    def sync_split_layout() -> None:
        split_state["sync_pending"] = False
        if len(pane.panes()) < 2:
            return
        pane_width = measured_split_width()
        if pane_width <= 0:
            return
        max_left = max(min_left_width, pane_width - min_right_width)
        target_ratio = split_state["manual_ratio"]
        if target_ratio is None:
            target_ratio = default_split_ratio
        target_x = int(round(pane_width * float(target_ratio)))
        target_x = max(min_left_width, min(max_left, target_x))
        right_width = max(min_right_width, pane_width - target_x)
        pane.configure(width=pane_width)
        left.configure(width=target_x)
        right_host.configure(width=right_width)
        try:
            current_x = int(pane.sash_coord(0)[0])
        except Exception:  # noqa: BLE001
            current_x = -1
        if current_x != target_x:
            try:
                pane.sash_place(0, target_x, 0)
            except Exception:  # noqa: BLE001
                return

    def request_split_sync() -> None:
        if split_state["sync_pending"]:
            return
        split_state["sync_pending"] = True
        dialog.after_idle(sync_split_layout)

    def remember_current_split_ratio() -> None:
        if len(pane.panes()) < 2:
            return
        pane_width = measured_split_width()
        if pane_width <= 0:
            return
        try:
            sash_x = int(pane.sash_coord(0)[0])
        except Exception:  # noqa: BLE001
            return
        min_ratio = min_left_width / max(1, pane_width)
        max_ratio = max(min_ratio, (pane_width - min_right_width) / max(1, pane_width))
        split_state["manual_ratio"] = max(min_ratio, min(max_ratio, sash_x / max(1, pane_width)))

    def on_pane_configure(_event: tk.Event | None = None) -> None:
        request_split_sync()

    def on_dialog_configure(event: tk.Event | None = None) -> None:
        if event is not None and event.widget is not dialog:
            return
        request_split_sync()

    def on_pane_button_release(_event: tk.Event | None = None) -> None:
        remember_current_split_ratio()

    pane.bind("<Configure>", on_pane_configure, add="+")
    pane.bind("<ButtonRelease-1>", on_pane_button_release, add="+")
    dialog.bind("<Configure>", on_dialog_configure, add="+")

    row_by_iid: dict[str, dict[str, Any]] = {}
    result_var = tk.StringVar(value="")
    detail_info_var = tk.StringVar(value="Pilih row untuk melihat trace link dan mengedit tanggal/reference/resolve account.")
    edit_date_var = tk.StringVar(value="")
    edit_ref_var = tk.StringVar(value="")
    row_resolve_var = tk.StringVar(value="")
    row_resolve_preview_var = tk.StringVar(value="Belum dipilih")
    active_resolve_candidates: dict[str, list[SvlDashboardRepairAccountCandidate]] = {"value": []}
    execute_scope_var = tk.StringVar(value=PCB_CASE1_EXECUTE_SCOPE_ALL)
    shared_worker_count = max(1, int(getattr(self._module_settings, "max_workers", 1) or 1))
    summary_field_order = [
        ("product_id", "Product ID"),
        ("bill_line_id", "Bill Line ID"),
        ("purchase_line_id", "PO Line ID"),
        ("stock_move_id", "Stock Move ID"),
        ("picking_id", "Picking ID"),
        ("stj_refs", "STJ Refs"),
        ("external_clearing_amount", "External Clearing"),
        ("result_move_name", "Result JE No"),
        ("partner_id", "Partner ID"),
        ("reconcile_status", "Reconcile Status"),
        ("review_status", "Review"),
        ("resolve_account_preview", "Resolve Account"),
        ("coefficient_variance", "Coeff Variance"),
        ("guard_status", "Guard"),
        ("problem_balances_by_code", "Problem Bal"),
        ("hpp_balance", "HPP Balance"),
        ("selisih_hpp_amount", "Selisih HPP"),
        ("planned_line_preview", "JE Preview"),
    ]
    advanced_field_order = [
        ("stock_move_ids", "Stock Move IDs"),
        ("stj_move_ids", "STJ Move IDs"),
        ("stj_link_basis", "STJ Link Basis"),
        ("stj_candidate_count", "STJ Candidate Count"),
        ("payment_move_ids", "Payment Move IDs"),
        ("bank_move_ids", "Bank Move IDs"),
        ("suspend_target_aml_ids", "Suspend Target AML IDs"),
        ("clearing_target_aml_ids", "Clearing Target AML IDs"),
        ("product_uom_id", "UoM ID"),
        ("quantity", "Quantity"),
        ("currency_id", "Currency ID"),
        ("amount_currency", "Amount Currency"),
        ("analytic_distribution", "Analytic Distribution"),
        ("bill_price_unit", "Bill Price Unit"),
        ("gr_price_unit", "GR Price Unit"),
        ("price_gap_value", "Price Gap Value"),
        ("allocated_amount", "Allocated Amount"),
        ("reconcile_message", "Reconcile Message"),
        ("external_clearing_refs", "External Clearing Refs"),
        ("external_clearing_basis", "External Clearing Basis"),
        ("external_clearing_verified", "External Clearing Verified"),
        ("case_evidence", "Case Evidence"),
        ("review_required", "Review Required"),
        ("review_confirmed", "Review Confirmed"),
        ("review_reason", "Review Reason"),
        ("resolve_account_code", "Resolve Account Code"),
        ("resolve_account_preview", "Resolve Account Preview"),
        ("suggested_expense_account_code", "Suggested Resolve Account"),
        ("planned_lines", "Planned Lines"),
        ("problem_balances_by_code", "Problem Balances"),
        ("hpp_balances_by_code", "HPP Balances"),
        ("hpp_balance", "HPP Balance"),
        ("selisih_hpp_amount", "Selisih HPP"),
        ("bank_balances_by_code", "Bank Balances"),
        ("inventory_balance", "Inventory Balance"),
        ("cogs_variance_balance", "COGS Variance"),
        ("guard_messages", "Guard Reasons"),
    ]
    summary_vars = {field_name: tk.StringVar(value="-") for field_name, _label in summary_field_order}
    advanced_vars = {field_name: tk.StringVar(value="-") for field_name, _label in advanced_field_order}

    def _row_status_tag(row: dict[str, Any]) -> str:
        status = normalize_text(row.get("row_status")).lower()
        if status in {"repaired"}:
            return "repaired"
        if status in {"error", "failed"}:
            return "error"
        if status == "needs_review":
            return "needs_review"
        return "ready"

    def _dialog_widgets_alive() -> bool:
        try:
            return bool(dialog.winfo_exists()) and bool(tv.winfo_exists())
        except tk.TclError:
            return False

    def _refresh_tree(*, reset_projected_review_confirmation: bool = True) -> None:
        if not _dialog_widgets_alive():
            return
        try:
            previous_row_keys = {row_by_iid[iid].get("row_key") for iid in tv.selection() if iid in row_by_iid}
            for item_id in tv.get_children():
                tv.delete(item_id)
            row_by_iid.clear()
            rows_by_case: dict[str, list[dict[str, Any]]] = {}
            for row in list(col.values()):
                if self._pcb_row_uses_planned_lines(row):
                    cycle = self._pcb_detail_cycle_for_row(row)
                    item_row = self._pcb_detail_item_row_for_row(row, cycle) if cycle is not None else None
                    self._sync_pcb_case2_row_defaults(
                        row,
                        preserve_terminal=True,
                        cycle=cycle,
                        item_row=item_row,
                        reset_projected_review_confirmation=reset_projected_review_confirmation,
                    )
                rows_by_case.setdefault(normalize_text(row.get("pcb_case")).lower() or "case1", []).append(row)
            ordered_cases = tuple(self._PCB_PROBLEM_CASE_ORDER)
            for pcb_case in ordered_cases:
                group_rows = rows_by_case.get(pcb_case) or []
                if not group_rows:
                    continue
                group_iid = f"group::{pcb_case}"
                tv.insert(
                    "",
                    "end",
                    iid=group_iid,
                    text=self._pcb_case_label(case_value=pcb_case, row=group_rows[0]),
                    open=True,
                    tags=("group",),
                    values=(
                        "",
                        "",
                        "",
                        "",
                        "",
                        f"{sum(abs(float(row.get('amount') or 0.0)) for row in group_rows):,.2f}",
                        "",
                        "",
                        f"{len(group_rows)} row",
                    ),
                )
                for row in group_rows:
                    item_label = " | ".join(
                        part
                        for part in (normalize_text(row.get("item_code")), normalize_text(row.get("item_name")))
                        if part
                    )
                    if not item_label:
                        item_label = normalize_text(row.get("item_name")) or f"Product #{int(row.get('product_id') or 0)}"
                    bill_label = (
                        normalize_text(row.get("bill_name"))
                        or f"Bill #{int(row.get('bill_move_id') or 0)}"
                        if int(row.get("bill_move_id") or 0) > 0
                        else ""
                    )
                    iid = tv.insert(
                        group_iid,
                        "end",
                        text="",
                        tags=(_row_status_tag(row),),
                        values=(
                            normalize_text(row.get("picking_name")),
                            item_label,
                            normalize_text(row.get("source_label")),
                            normalize_text(row.get("po_name")),
                            bill_label,
                            f"{abs(float(row.get('amount') or 0.0)):,.2f}",
                            self._pcb_je_preview_text(row),
                            (
                                self._pcb_guard_summary_text(row)
                                if self._pcb_row_uses_planned_lines(row)
                                else (normalize_text(row.get("reconcile_readiness_label")) or "No Target")
                            ),
                            self._repair_row_status_label(row),
                        ),
                    )
                    row_by_iid[iid] = row
            if previous_row_keys:
                tv.selection_set(tuple(iid for iid, row in row_by_iid.items() if row.get("row_key") in previous_row_keys))
            dialog.title(f"PCB Repair Collection - {len(col)} row")
        except tk.TclError:
            return

    tk.Label(
        right,
        text="Row Detail",
        bg=T.BG_CARD,
        fg=T.TEXT_ON_LIGHT,
        font=T.font(T.FONT_BODY_SIZE, bold=True),
    ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))
    detail_info_label = tk.Label(
        right,
        textvariable=detail_info_var,
        bg=T.BG_CARD,
        fg=T.TEXT_MUTED,
        font=T.font(T.FONT_SMALL_SIZE),
        justify="left",
        wraplength=320,
    )
    detail_info_label.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 10))

    tk.Label(right, text="Tanggal JE", bg=T.BG_CARD, fg=T.TEXT_ON_LIGHT, font=T.font(T.FONT_SMALL_SIZE)).grid(row=2, column=0, columnspan=2, sticky="w")
    tk.Entry(right, textvariable=edit_date_var, font=T.font(T.FONT_SMALL_SIZE), width=16).grid(row=3, column=0, columnspan=2, sticky="ew", pady=(2, 8))
    tk.Label(right, text="Reference", bg=T.BG_CARD, fg=T.TEXT_ON_LIGHT, font=T.font(T.FONT_SMALL_SIZE)).grid(row=4, column=0, columnspan=2, sticky="w")
    tk.Entry(right, textvariable=edit_ref_var, font=T.font(T.FONT_SMALL_SIZE), width=30).grid(row=5, column=0, columnspan=2, sticky="ew", pady=(2, 8))
    tk.Label(right, text="Resolve Account Override", bg=T.BG_CARD, fg=T.TEXT_ON_LIGHT, font=T.font(T.FONT_SMALL_SIZE)).grid(row=6, column=0, columnspan=2, sticky="w")
    row_resolve_picker = _RepairAccountPicker(
        right,
        variable=row_resolve_var,
        filter_fn=self._filter_repair_account_candidates,
        label_fn=lambda candidate: self._repair_candidate_preview_label(candidate, compact=True),
        on_change=lambda: row_resolve_preview_var.set(
            self._lookup_repair_candidate_preview(
                normalize_text(row_resolve_var.get()).upper(),
                active_resolve_candidates["value"],
                compact=True,
            )
        ),
    )
    row_resolve_picker.grid(row=7, column=0, columnspan=2, sticky="ew", pady=(2, 0))
    tk.Label(
        right,
        textvariable=row_resolve_preview_var,
        bg=T.BG_CARD,
        fg=T.TEXT_MUTED,
        font=T.font(T.FONT_SMALL_SIZE - 1),
        justify="left",
        anchor="w",
        wraplength=320,
    ).grid(row=8, column=0, columnspan=2, sticky="ew", pady=(2, 10))

    apply_btn = tk.Button(
        right,
        text="Apply ke Terpilih",
        relief="flat",
        bg=T.BRAND_PRIMARY,
        fg=T.TEXT_ON_DARK,
        font=T.font(T.FONT_SMALL_SIZE, bold=True),
        activebackground=T.BRAND_PRIMARY_DARK,
        activeforeground=T.TEXT_ON_DARK,
        padx=10,
        pady=4,
        cursor="hand2",
        state="disabled",
    )
    apply_btn.grid(row=9, column=0, sticky="w", pady=(0, 12))
    confirm_review_btn = tk.Button(
        right,
        text="Confirm Review",
        relief="flat",
        bg=T.BG_INPUT,
        fg=T.TEXT_ON_LIGHT,
        font=T.font(T.FONT_SMALL_SIZE, bold=True),
        padx=10,
        pady=4,
        cursor="hand2",
        state="disabled",
    )
    confirm_review_btn.grid(row=9, column=1, sticky="e", pady=(0, 12))

    current_cycle_note_var = tk.StringVar(value="Pilih 1 row untuk melihat simulasi.")
    simulated_note_var = tk.StringVar(value="Pilih 1 row untuk melihat simulasi.")
    projected_cycle_note_var = tk.StringVar(value="Pilih 1 row untuk melihat simulasi.")
    simulated_editor_note_var = tk.StringVar(value="Editor simulasi aktif untuk 1 row Needs Review / Incomplete.")
    simulated_line_side_var = tk.StringVar(value="debit")
    simulated_line_account_var = tk.StringVar(value="")
    simulated_line_account_preview_var = tk.StringVar(value="Belum dipilih")
    simulated_line_name_var = tk.StringVar(value="")
    simulated_line_amount_var = tk.StringVar(value="")
    simulated_line_label_var = tk.StringVar(value="")
    active_detail_context: dict[str, Any] = {"row": None, "cycle": None, "item_row": None}
    active_simulation_candidates: dict[str, list[SvlDashboardRepairAccountCandidate]] = {"value": []}
    simulated_line_index_state: dict[str, int | None] = {"value": None}
    simulated_line_by_iid: dict[str, int] = {}
    section_note_labels: list[tk.Label] = []

    projected_cycle_title = tk.Label(
        right,
        text="Proyeksi Jurnal Per Item",
        bg=T.BG_CARD,
        fg=T.TEXT_ON_LIGHT,
        font=T.font(T.FONT_SMALL_SIZE, bold=True),
    )
    projected_cycle_title.grid(row=10, column=0, columnspan=2, sticky="w", pady=(0, 4))
    projected_cycle_note_label = tk.Label(
        right,
        textvariable=projected_cycle_note_var,
        bg=T.BG_CARD,
        fg=T.TEXT_MUTED,
        font=T.font(T.FONT_SMALL_SIZE - 1),
        justify="left",
        anchor="w",
        wraplength=320,
    )
    projected_cycle_note_label.grid(row=11, column=0, columnspan=2, sticky="ew", pady=(0, 4))
    section_note_labels.append(projected_cycle_note_label)
    projected_cycle_frame = tk.Frame(right, bg=T.BG_CARD)
    projected_cycle_frame.grid(row=12, column=0, columnspan=2, sticky="ew", pady=(0, 8))
    projected_cycle_tree = self._build_tree(
        projected_cycle_frame,
        ("status", "code", "name", "current_saldo", "simulasi", "projected_saldo"),
    )
    projected_cycle_tree.configure(height=5)
    projected_cycle_tree.heading("status", text="")
    projected_cycle_tree.heading("code", text="Code")
    projected_cycle_tree.heading("name", text="Name")
    projected_cycle_tree.heading("current_saldo", text="Current Saldo")
    projected_cycle_tree.heading("simulasi", text="Simulasi")
    projected_cycle_tree.heading("projected_saldo", text="Projected Saldo")
    projected_cycle_tree.column("status", width=34, minwidth=34, anchor="center", stretch=False)
    projected_cycle_tree.column("code", width=78, minwidth=72, anchor="w", stretch=False)
    projected_cycle_tree.column("name", width=190, minwidth=130, anchor="w", stretch=True)
    projected_cycle_tree.column("current_saldo", width=96, minwidth=90, anchor="e", stretch=False)
    projected_cycle_tree.column("simulasi", width=90, minwidth=84, anchor="e", stretch=False)
    projected_cycle_tree.column("projected_saldo", width=104, minwidth=96, anchor="e", stretch=False)

    current_cycle_title = tk.Label(
        right,
        text="Detail Saldo Akun Per Cycle (Item Terpilih)",
        bg=T.BG_CARD,
        fg=T.TEXT_ON_LIGHT,
        font=T.font(T.FONT_SMALL_SIZE, bold=True),
    )
    current_cycle_title.grid(row=13, column=0, columnspan=2, sticky="w", pady=(0, 4))
    current_cycle_note_label = tk.Label(
        right,
        textvariable=current_cycle_note_var,
        bg=T.BG_CARD,
        fg=T.TEXT_MUTED,
        font=T.font(T.FONT_SMALL_SIZE - 1),
        justify="left",
        anchor="w",
        wraplength=320,
    )
    current_cycle_note_label.grid(row=14, column=0, columnspan=2, sticky="ew", pady=(0, 4))
    section_note_labels.append(current_cycle_note_label)
    current_cycle_frame = tk.Frame(right, bg=T.BG_CARD)
    current_cycle_frame.grid(row=15, column=0, columnspan=2, sticky="ew", pady=(0, 8))
    current_cycle_tree = self._build_tree(
        current_cycle_frame,
        ("status", "code", "name", "debit", "credit", "saldo"),
    )
    current_cycle_tree.configure(height=5)
    current_cycle_tree.heading("status", text="")
    current_cycle_tree.heading("code", text="Code")
    current_cycle_tree.heading("name", text="Name")
    current_cycle_tree.heading("debit", text="Debit")
    current_cycle_tree.heading("credit", text="Credit")
    current_cycle_tree.heading("saldo", text="Saldo")
    current_cycle_tree.column("status", width=34, minwidth=34, anchor="center", stretch=False)
    current_cycle_tree.column("code", width=78, minwidth=72, anchor="w", stretch=False)
    current_cycle_tree.column("name", width=220, minwidth=140, anchor="w", stretch=True)
    current_cycle_tree.column("debit", width=98, minwidth=90, anchor="e", stretch=False)
    current_cycle_tree.column("credit", width=98, minwidth=90, anchor="e", stretch=False)
    current_cycle_tree.column("saldo", width=98, minwidth=90, anchor="e", stretch=False)

    simulated_title = tk.Label(
        right,
        text="Jurnal Simulasi (Belum Execute)",
        bg=T.BG_CARD,
        fg=T.TEXT_ON_LIGHT,
        font=T.font(T.FONT_SMALL_SIZE, bold=True),
    )
    simulated_title.grid(row=16, column=0, columnspan=2, sticky="w", pady=(0, 4))
    simulated_note_label = tk.Label(
        right,
        textvariable=simulated_note_var,
        bg=T.BG_CARD,
        fg=T.TEXT_MUTED,
        font=T.font(T.FONT_SMALL_SIZE - 1),
        justify="left",
        anchor="w",
        wraplength=320,
    )
    simulated_note_label.grid(row=17, column=0, columnspan=2, sticky="ew", pady=(0, 4))
    section_note_labels.append(simulated_note_label)
    simulated_frame = tk.Frame(right, bg=T.BG_CARD)
    simulated_frame.grid(row=18, column=0, columnspan=2, sticky="ew", pady=(0, 8))
    simulated_tree = self._build_tree(
        simulated_frame,
        ("side", "account_code", "account_name", "amount", "keterangan"),
    )
    simulated_tree.configure(height=4)
    simulated_tree.heading("side", text="Side")
    simulated_tree.heading("account_code", text="Account Code")
    simulated_tree.heading("account_name", text="Account Name")
    simulated_tree.heading("amount", text="Amount")
    simulated_tree.heading("keterangan", text="Keterangan")
    simulated_tree.column("side", width=50, minwidth=46, anchor="center", stretch=False)
    simulated_tree.column("account_code", width=92, minwidth=86, anchor="w", stretch=False)
    simulated_tree.column("account_name", width=180, minwidth=120, anchor="w", stretch=True)
    simulated_tree.column("amount", width=96, minwidth=90, anchor="e", stretch=False)
    simulated_tree.column("keterangan", width=240, minwidth=180, anchor="w", stretch=True)

    for detail_tree in (current_cycle_tree, projected_cycle_tree):
        detail_tree.tag_configure("problem", foreground=T.STATUS_ERROR)
        detail_tree.tag_configure("balanced", foreground=T.STATUS_SUCCESS)
        detail_tree.tag_configure("acceptable", foreground=T.TEXT_MUTED)
        detail_tree.tag_configure("info", foreground=T.BRAND_PRIMARY)
    simulated_tree.tag_configure("debit", foreground=T.TEXT_ON_LIGHT)
    simulated_tree.tag_configure("credit", foreground=T.BRAND_PRIMARY)
    simulated_tree.tag_configure("manual", foreground=T.STATUS_WARNING)

    simulated_editor_frame = tk.Frame(right, bg=T.BG_CARD, bd=1, relief="solid", highlightbackground=T.BORDER_LIGHT, highlightthickness=1)
    simulated_editor_frame.grid(row=19, column=0, columnspan=2, sticky="ew", pady=(0, 8))
    simulated_editor_frame.columnconfigure(1, weight=1)
    simulated_editor_frame.columnconfigure(3, weight=1)
    tk.Label(
        simulated_editor_frame,
        text="Edit / Tambah Line Simulasi",
        bg=T.BG_CARD,
        fg=T.TEXT_ON_LIGHT,
        font=T.font(T.FONT_SMALL_SIZE, bold=True),
    ).grid(row=0, column=0, columnspan=4, sticky="w", padx=8, pady=(6, 4))
    tk.Label(
        simulated_editor_frame,
        text="Side",
        bg=T.BG_CARD,
        fg=T.TEXT_ON_LIGHT,
        font=T.font(T.FONT_SMALL_SIZE),
    ).grid(row=1, column=0, sticky="w", padx=(8, 6), pady=(0, 4))
    simulated_line_side_combo = ttk.Combobox(
        simulated_editor_frame,
        textvariable=simulated_line_side_var,
        values=("debit", "credit"),
        state="disabled",
        width=10,
    )
    simulated_line_side_combo.grid(row=1, column=1, sticky="ew", padx=(0, 8), pady=(0, 4))
    tk.Label(
        simulated_editor_frame,
        text="Account",
        bg=T.BG_CARD,
        fg=T.TEXT_ON_LIGHT,
        font=T.font(T.FONT_SMALL_SIZE),
    ).grid(row=1, column=2, sticky="w", padx=(0, 6), pady=(0, 4))
    simulated_line_picker = _RepairAccountPicker(
        simulated_editor_frame,
        variable=simulated_line_account_var,
        filter_fn=self._filter_repair_account_candidates,
        label_fn=lambda candidate: self._repair_candidate_preview_label(candidate, compact=True),
    )
    simulated_line_picker.grid(row=1, column=3, sticky="ew", padx=(0, 8), pady=(0, 4))
    tk.Label(
        simulated_editor_frame,
        textvariable=simulated_line_account_preview_var,
        bg=T.BG_CARD,
        fg=T.TEXT_MUTED,
        font=T.font(T.FONT_SMALL_SIZE - 1),
        justify="left",
        anchor="w",
        wraplength=320,
    ).grid(row=2, column=0, columnspan=4, sticky="ew", padx=8, pady=(0, 4))
    tk.Label(
        simulated_editor_frame,
        text="Account Name",
        bg=T.BG_CARD,
        fg=T.TEXT_ON_LIGHT,
        font=T.font(T.FONT_SMALL_SIZE),
    ).grid(row=3, column=0, sticky="w", padx=(8, 6), pady=(0, 4))
    simulated_line_name_entry = tk.Entry(
        simulated_editor_frame,
        textvariable=simulated_line_name_var,
        font=T.font(T.FONT_SMALL_SIZE),
        state="disabled",
    )
    simulated_line_name_entry.grid(row=3, column=1, sticky="ew", padx=(0, 8), pady=(0, 4))
    tk.Label(
        simulated_editor_frame,
        text="Amount",
        bg=T.BG_CARD,
        fg=T.TEXT_ON_LIGHT,
        font=T.font(T.FONT_SMALL_SIZE),
    ).grid(row=3, column=2, sticky="w", padx=(0, 6), pady=(0, 4))
    simulated_line_amount_entry = tk.Entry(
        simulated_editor_frame,
        textvariable=simulated_line_amount_var,
        font=T.font(T.FONT_SMALL_SIZE),
        state="disabled",
    )
    simulated_line_amount_entry.grid(row=3, column=3, sticky="ew", padx=(0, 8), pady=(0, 4))
    tk.Label(
        simulated_editor_frame,
        text="Line Label",
        bg=T.BG_CARD,
        fg=T.TEXT_ON_LIGHT,
        font=T.font(T.FONT_SMALL_SIZE),
    ).grid(row=4, column=0, sticky="w", padx=(8, 6), pady=(0, 4))
    simulated_line_label_entry = tk.Entry(
        simulated_editor_frame,
        textvariable=simulated_line_label_var,
        font=T.font(T.FONT_SMALL_SIZE),
        state="disabled",
    )
    simulated_line_label_entry.grid(row=4, column=1, columnspan=3, sticky="ew", padx=(0, 8), pady=(0, 4))
    simulated_editor_button_style = {
        "bg": T.BG_CARD,
        "relief": "flat",
        "font": T.font(T.FONT_BODY_SIZE),
        "cursor": "hand2",
        "padx": 8,
        "pady": 3,
    }
    simulated_line_add_btn = tk.Button(simulated_editor_frame, text="Tambah Line", fg=T.TEXT_MUTED, **simulated_editor_button_style)
    simulated_line_add_btn.grid(row=5, column=0, sticky="w", padx=(8, 4), pady=(0, 6))
    simulated_line_update_btn = tk.Button(simulated_editor_frame, text="Update Line", fg=T.TEXT_MUTED, **simulated_editor_button_style)
    simulated_line_update_btn.grid(row=5, column=1, sticky="w", padx=(0, 4), pady=(0, 6))
    simulated_line_remove_btn = tk.Button(simulated_editor_frame, text="Hapus Line", fg=T.STATUS_ERROR, **simulated_editor_button_style)
    simulated_line_remove_btn.grid(row=5, column=2, sticky="w", padx=(0, 4), pady=(0, 6))
    simulated_line_reset_btn = tk.Button(simulated_editor_frame, text="Reset Default", fg=T.TEXT_MUTED, **simulated_editor_button_style)
    simulated_line_reset_btn.grid(row=5, column=3, sticky="e", padx=(0, 8), pady=(0, 6))
    tk.Label(
        simulated_editor_frame,
        textvariable=simulated_editor_note_var,
        bg=T.BG_CARD,
        fg=T.TEXT_MUTED,
        font=T.font(T.FONT_SMALL_SIZE - 1),
        justify="left",
        anchor="w",
        wraplength=320,
    ).grid(row=6, column=0, columnspan=4, sticky="ew", padx=8, pady=(0, 6))

    summary_section = CollapsibleSection(
        right,
        key="pcb_case1_row_detail_summary",
        title="Summary",
        expanded=False,
        summary_text="Compact row summary",
        body_fill="x",
        body_expand=False,
    )
    summary_section.grid(row=20, column=0, columnspan=2, sticky="ew", pady=(0, 8))

    summary_frame = tk.Frame(summary_section.body, bg=T.BG_CARD)
    summary_frame.grid(row=0, column=0, sticky="ew")
    summary_frame.columnconfigure(1, weight=1)
    summary_value_labels: list[tk.Label] = []
    for index, (field_name, label_text) in enumerate(summary_field_order):
        tk.Label(
            summary_frame,
            text=label_text,
            bg=T.BG_CARD,
            fg=T.TEXT_ON_LIGHT,
            font=T.font(T.FONT_SMALL_SIZE),
            anchor="nw",
            justify="left",
            width=14,
        ).grid(row=index, column=0, sticky="nw", padx=(0, 8), pady=(0, 3))
        value_label = tk.Label(
            summary_frame,
            textvariable=summary_vars[field_name],
            bg=T.BG_CARD,
            fg=T.TEXT_MUTED,
            font=T.font(T.FONT_SMALL_SIZE - 1),
            anchor="w",
            justify="left",
            wraplength=200,
        )
        value_label.grid(row=index, column=1, sticky="ew", pady=(0, 3))
        summary_value_labels.append(value_label)

    advanced_section = CollapsibleSection(
        right,
        key="pcb_case1_row_detail_advanced",
        title="Advanced",
        expanded=False,
        summary_text="Additional link + guard fields",
        body_fill="x",
        body_expand=False,
    )
    advanced_section.grid(row=21, column=0, columnspan=2, sticky="ew", pady=(0, 8))
    advanced_section.body.columnconfigure(1, weight=1)
    advanced_value_labels: list[tk.Label] = []
    for index, (field_name, label_text) in enumerate(advanced_field_order):
        tk.Label(
            advanced_section.body,
            text=label_text,
            bg=T.BG_CARD,
            fg=T.TEXT_ON_LIGHT,
            font=T.font(T.FONT_SMALL_SIZE),
            anchor="nw",
            justify="left",
            width=17,
        ).grid(row=index, column=0, sticky="nw", padx=(0, 8), pady=(0, 3))
        value_label = tk.Label(
            advanced_section.body,
            textvariable=advanced_vars[field_name],
            bg=T.BG_CARD,
            fg=T.TEXT_MUTED,
            font=T.font(T.FONT_SMALL_SIZE - 1),
            anchor="w",
            justify="left",
            wraplength=200,
        )
        value_label.grid(row=index, column=1, sticky="ew", pady=(0, 3))
        advanced_value_labels.append(value_label)

    def _selected_rows() -> list[dict[str, Any]]:
        if not _dialog_widgets_alive():
            return []
        try:
            return [row_by_iid[iid] for iid in tv.selection() if iid in row_by_iid]
        except tk.TclError:
            return []

    def _pcb_case1_same_item_group_key(row: dict[str, Any] | None) -> tuple[str, int] | None:
        if not isinstance(row, dict) or self._pcb_row_uses_planned_lines(row):
            return None
        cycle_key = normalize_text(row.get("cycle_key"))
        product_id = int(row.get("product_id") or 0)
        if not cycle_key or product_id <= 0:
            return None
        return (cycle_key, product_id)

    def _pcb_case1_group_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not rows:
            return []
        group_key = _pcb_case1_same_item_group_key(rows[0])
        if group_key is None:
            return []
        if any(_pcb_case1_same_item_group_key(row) != group_key for row in rows):
            return []
        return [
            candidate
            for candidate in list(col.values())
            if _pcb_case1_same_item_group_key(candidate) == group_key
        ]

    def _pcb_case1_group_note_text(*, selected_count: int, total_count: int) -> str:
        if total_count <= 1:
            return ""
        if selected_count <= 1:
            sibling_count = max(0, total_count - 1)
            return (
                f"Preview ini parsial: baru 1 row source item yang dipilih; "
                f"masih ada {sibling_count} sibling row source pada cycle/item yang sama."
            )
        if selected_count < total_count:
            return (
                f"Preview gabungan row terpilih baru mencakup {selected_count} dari {total_count} "
                "row source item pada cycle/item yang sama."
            )
        return (
            f"Preview gabungan row terpilih sudah mencakup seluruh {total_count} "
            "row source item pada cycle/item yang sama."
        )

    def _build_case1_group_simulated_lines(
        rows: list[dict[str, Any]],
        *,
        cycle: Any = None,
        item_row: Any = None,
    ) -> tuple[list[dict[str, Any]], bool]:
        simulated_lines: list[dict[str, Any]] = []
        has_unresolved_account = False
        for row in rows:
            source_label = normalize_text(row.get("source_label"))
            for line in self._pcb_simulated_journal_lines(row, cycle=cycle, item_row=item_row):
                line_mapping = dict(line)
                keterangan = normalize_text(line_mapping.get("keterangan"))
                if source_label:
                    line_mapping["keterangan"] = (
                        f"{source_label} | {keterangan}"
                        if keterangan
                        else source_label
                    )
                line_mapping["line_index"] = len(simulated_lines)
                simulated_lines.append(line_mapping)
                if normalize_text(line_mapping.get("account_code")).upper() in {"", "?"}:
                    has_unresolved_account = True
        return simulated_lines, has_unresolved_account

    def _simulation_editor_target_row() -> dict[str, Any] | None:
        row = active_detail_context["row"]
        if not isinstance(row, dict):
            return None
        if not self._pcb_row_uses_planned_lines(row):
            return None
        if not self._pcb_is_resolve_account_editable(row):
            return None
        return row

    def _lookup_simulation_candidate_name(account_code: Any) -> str:
        clean_code = normalize_text(account_code).upper()
        if not clean_code:
            return ""
        for candidate in active_simulation_candidates["value"]:
            if normalize_text(candidate.code).upper() == clean_code:
                return normalize_text(candidate.name) or clean_code
        row = active_detail_context["row"]
        if isinstance(row, dict):
            return self._pcb_lookup_account_name(row, clean_code)
        fixed_account = self._PCB_SIMULATION_FIXED_ACCOUNTS.get(clean_code)
        return fixed_account[0] if fixed_account is not None else clean_code

    def _set_simulated_line_candidates(row: dict[str, Any] | None, *, cycle: Any = None, item_row: Any = None) -> None:
        if row is None:
            active_simulation_candidates["value"] = []
            simulated_line_picker.set_candidates([])
            simulated_line_account_preview_var.set("Belum dipilih")
            return
        active_simulation_candidates["value"] = self._pcb_simulation_account_candidates_for_row(row, cycle=cycle, item_row=item_row)
        simulated_line_picker.set_candidates(active_simulation_candidates["value"])
        simulated_line_account_preview_var.set(
            self._lookup_repair_candidate_preview(
                normalize_text(simulated_line_account_var.get()).upper(),
                active_simulation_candidates["value"],
                compact=True,
            )
        )

    def _refresh_simulated_line_button_state() -> None:
        row = _simulation_editor_target_row()
        enabled = row is not None
        has_selected_line = simulated_line_index_state["value"] is not None
        simulated_line_add_btn.configure(state="normal" if enabled else "disabled")
        simulated_line_update_btn.configure(state="normal" if enabled and has_selected_line else "disabled")
        simulated_line_remove_btn.configure(state="normal" if enabled and has_selected_line else "disabled")
        simulated_line_reset_btn.configure(
            state="normal" if enabled and bool((row or {}).get("planned_lines_manual")) else "disabled"
        )

    def _clear_simulated_line_editor(*, clear_tree_selection: bool = True) -> None:
        simulated_line_index_state["value"] = None
        simulated_line_side_var.set("debit")
        simulated_line_account_var.set("")
        simulated_line_account_preview_var.set("Belum dipilih")
        simulated_line_name_var.set("")
        simulated_line_amount_var.set("")
        simulated_line_label_var.set("")
        if clear_tree_selection:
            try:
                simulated_tree.selection_remove(simulated_tree.selection())
            except tk.TclError:
                pass
        _refresh_simulated_line_button_state()

    def _set_simulated_line_editor_enabled(enabled: bool, *, message: str = "") -> None:
        simulated_line_side_combo.configure(state="readonly" if enabled else "disabled")
        text_state = "normal" if enabled else "disabled"
        simulated_line_picker.entry.configure(state=text_state)
        simulated_line_picker.candidate_listbox.configure(state=text_state)
        simulated_line_name_entry.configure(state=text_state)
        simulated_line_amount_entry.configure(state=text_state)
        simulated_line_label_entry.configure(state=text_state)
        if not enabled:
            simulated_line_picker._hide_dropdown()  # noqa: SLF001
            _clear_simulated_line_editor()
        _refresh_simulated_line_button_state()
        if message:
            simulated_editor_note_var.set(message)

    def _load_simulated_line_editor(line_index: int | None) -> None:
        row = _simulation_editor_target_row()
        if row is None:
            return
        planned_lines = list(row.get("planned_lines") or [])
        if line_index is None or line_index < 0 or line_index >= len(planned_lines):
            _clear_simulated_line_editor(clear_tree_selection=False)
            simulated_editor_note_var.set("Pilih line simulasi untuk edit, atau isi form lalu klik Tambah Line.")
            return
        line_mapping = dict(planned_lines[line_index])
        simulated_line_index_state["value"] = line_index
        simulated_line_side_var.set(normalize_text(line_mapping.get("side")).lower() or "debit")
        simulated_line_account_var.set(normalize_text(line_mapping.get("account_code")).upper())
        simulated_line_account_preview_var.set(
            self._lookup_repair_candidate_preview(
                normalize_text(line_mapping.get("account_code")).upper(),
                active_simulation_candidates["value"],
                compact=True,
            )
        )
        simulated_line_name_var.set(
            normalize_text(line_mapping.get("account_name"))
            or _lookup_simulation_candidate_name(line_mapping.get("account_code"))
        )
        simulated_line_amount_var.set(f"{abs(float(line_mapping.get('amount') or 0.0)):,.2f}")
        simulated_line_label_var.set(normalize_text(line_mapping.get("line_label")))
        simulated_editor_note_var.set("Edit line terpilih lalu klik Update Line, atau Hapus Line bila tidak diperlukan.")
        _refresh_simulated_line_button_state()

    def _on_simulated_account_change() -> None:
        clean_code = normalize_text(simulated_line_account_var.get()).upper()
        simulated_line_account_preview_var.set(
            self._lookup_repair_candidate_preview(clean_code, active_simulation_candidates["value"], compact=True)
        )
        resolved_name = _lookup_simulation_candidate_name(clean_code)
        if clean_code and resolved_name:
            simulated_line_name_var.set(resolved_name)

    def _build_simulated_line_payload(*, role: str, source_balance: float = 0.0) -> dict[str, Any] | None:
        row = _simulation_editor_target_row()
        if row is None:
            simulated_editor_note_var.set("Editor simulasi hanya aktif untuk 1 row Needs Review / Incomplete.")
            return None
        clean_side = normalize_text(simulated_line_side_var.get()).lower()
        if clean_side not in {"debit", "credit"}:
            simulated_editor_note_var.set("Side simulasi harus Debit atau Credit.")
            return None
        clean_account_code = normalize_text(simulated_line_account_var.get()).upper()
        if not clean_account_code:
            simulated_editor_note_var.set("Account Code simulasi wajib diisi.")
            return None
        try:
            amount = abs(float(normalize_text(simulated_line_amount_var.get()).replace(",", "")))
        except (TypeError, ValueError):
            amount = 0.0
        if amount <= 0.0:
            simulated_editor_note_var.set("Amount simulasi harus lebih besar dari 0.")
            return None
        account_name = normalize_text(simulated_line_name_var.get()) or _lookup_simulation_candidate_name(clean_account_code)
        return {
            "role": role,
            "account_code": clean_account_code,
            "account_name": account_name or clean_account_code,
            "amount": round(amount, 2),
            "side": clean_side,
            "line_label": normalize_text(simulated_line_label_var.get()) or normalize_text(row.get("line_label")),
            "source_balance": round(float(source_balance or 0.0), 2),
            "manual_account_override": True,
            "manual_line": True,
        }

    def _apply_simulated_line_change(action: str) -> None:
        row = _simulation_editor_target_row()
        if row is None:
            simulated_editor_note_var.set("Editor simulasi hanya aktif untuk 1 row Needs Review / Incomplete.")
            return
        cycle = active_detail_context["cycle"]
        item_row = active_detail_context["item_row"]
        planned_lines = self._map_pcb_case2_planned_lines(
            list(row.get("planned_lines") or []),
            default_line_label=normalize_text(row.get("line_label")),
        )
        selected_index = simulated_line_index_state["value"]
        if action == "reset":
            row["planned_lines"] = self._map_pcb_case2_planned_lines(
                list(row.get("base_planned_lines") or []),
                default_line_label=normalize_text(row.get("line_label")),
            )
            row["planned_lines_manual"] = False
            self._sync_pcb_case2_row_defaults(
                row,
                preserve_terminal=True,
                cycle=cycle,
                item_row=item_row,
                reset_projected_review_confirmation=False,
            )
            _refresh_tree(reset_projected_review_confirmation=False)
            _on_selection_change()
            simulated_editor_note_var.set("Jurnal simulasi dikembalikan ke default hasil builder.")
            return
        if action in {"update", "remove"} and (selected_index is None or selected_index < 0 or selected_index >= len(planned_lines)):
            simulated_editor_note_var.set("Pilih line simulasi dulu sebelum update atau hapus.")
            return
        if action == "remove":
            planned_lines.pop(int(selected_index))
        elif action == "update":
            existing_line = dict(planned_lines[int(selected_index)])
            payload = _build_simulated_line_payload(
                role=normalize_text(existing_line.get("role")) or "manual_simulation",
                source_balance=float(existing_line.get("source_balance") or 0.0),
            )
            if payload is None:
                return
            planned_lines[int(selected_index)] = payload
        else:
            payload = _build_simulated_line_payload(role="manual_simulation")
            if payload is None:
                return
            planned_lines.append(payload)
        row["planned_lines"] = planned_lines
        row["planned_lines_manual"] = True
        self._sync_pcb_case2_row_defaults(
            row,
            preserve_terminal=True,
            cycle=cycle,
            item_row=item_row,
            reset_projected_review_confirmation=False,
        )
        _refresh_tree(reset_projected_review_confirmation=False)
        _on_selection_change()
        if action == "add":
            simulated_editor_note_var.set("Line simulasi manual ditambahkan ke draft row.")
        elif action == "update":
            simulated_editor_note_var.set("Line simulasi terpilih sudah diperbarui.")
        else:
            simulated_editor_note_var.set("Line simulasi terpilih sudah dihapus.")

    def _on_simulated_line_selection_change(_event: Any = None) -> None:
        if _simulation_editor_target_row() is None:
            return
        selection = simulated_tree.selection()
        if not selection:
            _clear_simulated_line_editor(clear_tree_selection=False)
            simulated_editor_note_var.set("Pilih line simulasi untuk edit, atau isi form lalu klik Tambah Line.")
            return
        line_index = simulated_line_by_iid.get(selection[0])
        _load_simulated_line_editor(line_index)

    def _set_resolve_picker_enabled(enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        row_resolve_picker.entry.configure(state=state)
        row_resolve_picker.candidate_listbox.configure(state=state)
        if not enabled:
            row_resolve_picker._hide_dropdown()  # noqa: SLF001

    def _set_resolve_candidates(rows: list[dict[str, Any]]) -> None:
        if len(rows) == 1:
            target_rows = rows
        else:
            target_rows = [row for row in rows if self._pcb_is_resolve_account_editable(row)]
        combined_candidates: list[SvlDashboardRepairAccountCandidate] = []
        for row in target_rows:
            if not self._pcb_row_uses_planned_lines(row):
                continue
            self._sync_pcb_case2_row_defaults(
                row,
                preserve_terminal=True,
                reset_projected_review_confirmation=False,
            )
            combined_candidates.extend(self._pcb_account_candidates_for_row(row))
        active_resolve_candidates["value"] = self._dedupe_repair_account_candidates(combined_candidates)
        row_resolve_picker.set_candidates(active_resolve_candidates["value"])

    def _render_trace_value(
        value: Any,
        *,
        empty_text: str = "-",
        max_chars: int | None = None,
    ) -> str:
        if isinstance(value, list):
            text = ", ".join(str(item) for item in value) if value else ""
        elif isinstance(value, dict):
            text = str(value) if value else ""
        else:
            text = normalize_text(value) or ("0" if value == 0 else "")
        if not text:
            text = empty_text
        if max_chars is not None and text != empty_text:
            return build_compact_preview_text(text, empty_text=empty_text, max_chars=max_chars)
        return text

    def _balances_by_code_text(
        value: Any,
        *,
        empty_text: str = "-",
        max_chars: int | None = None,
    ) -> str:
        if not isinstance(value, dict) or not value:
            return empty_text
        parts = [
            f"{normalize_text(code).upper()} {float(balance or 0.0):+,.2f}"
            for code, balance in sorted(value.items(), key=lambda item: normalize_text(item[0]).upper())
            if normalize_text(code)
        ]
        if not parts:
            return empty_text
        text = " | ".join(parts)
        if max_chars is not None:
            return build_compact_preview_text(text, empty_text=empty_text, max_chars=max_chars)
        return text

    def _stj_refs_summary_text(row: dict[str, Any]) -> str:
        refs = [normalize_text(item) for item in list(row.get("stj_refs") or []) if normalize_text(item)]
        candidate_count = int(row.get("stj_candidate_count") or 0)
        if refs:
            preview = build_compact_preview_text(", ".join(refs), empty_text="-", max_chars=64)
            if candidate_count > 1:
                return f"{preview} ({candidate_count} candidates)"
            return preview
        if int(row.get("stock_move_id") or 0) > 0 or list(row.get("stock_move_ids") or []):
            return "Belum ter-resolve dari source STJ"
        return "-"

    def _reconcile_status_summary_text(row: dict[str, Any]) -> str:
        if self._pcb_row_uses_planned_lines(row):
            return self._pcb_guard_summary_text(row)
        if bool(row.get("reconcile_performed")):
            return normalize_text(row.get("reconcile_readiness_label")) or "Exact Auto OK"
        if bool(row.get("reconcile_skipped")):
            skip_label = normalize_text(row.get("reconcile_readiness_label")) or "Skipped"
            skip_detail = build_compact_preview_text(
                normalize_text(row.get("reconcile_message")),
                empty_text="",
                max_chars=58,
            )
            return f"{skip_label} | {skip_detail}" if skip_detail else skip_label
        readiness = normalize_text(row.get("reconcile_readiness_label")) or "No Target"
        target_count = int(row.get("reconcile_target_count") or 0)
        if target_count > 0:
            return f"{readiness} | {target_count} target AML"
        return readiness

    def _advanced_section_summary_text(row: dict[str, Any] | None = None) -> str:
        if not row:
            return "Additional link + guard fields"
        if self._pcb_row_uses_planned_lines(row):
            summary_parts = [
                self._pcb_guard_summary_text(row),
                (
                    f"ExtClr {self._pcb_external_clearing_amount(row):,.2f}"
                    if self._pcb_external_clearing_amount(row) >= 0.01
                    else ""
                ),
                build_compact_preview_text(self._pcb_je_preview_text(row), empty_text="", max_chars=58),
            ]
            return " | ".join(part for part in summary_parts if part and part != "-") or "Additional link + guard fields"
        summary_parts: list[str] = []
        stj_basis = normalize_text(row.get("stj_link_basis"))
        if stj_basis:
            summary_parts.append(f"STJ basis: {stj_basis}")
        candidate_count = int(row.get("stj_candidate_count") or 0)
        if candidate_count > 1:
            summary_parts.append(f"{candidate_count} STJ candidates")
        reconcile_message = normalize_text(row.get("reconcile_message"))
        if reconcile_message:
            summary_parts.append(build_compact_preview_text(reconcile_message, empty_text="", max_chars=52))
        return " | ".join(part for part in summary_parts if part) or "Additional link + guard fields"

    def _target_aml_field_text(row: dict[str, Any], field_name: str) -> str:
        values = list(row.get(field_name) or [])
        if values:
            return _render_trace_value(values)
        if field_name == "suspend_target_aml_ids" and int(row.get("bill_move_id") or 0) > 0:
            return "Runtime lookup via bill move"
        if field_name == "clearing_target_aml_ids" and list(row.get("stj_move_ids") or []):
            return "Runtime lookup via STJ move(s)"
        return "-"

    detail_section_empty_text = "Pilih 1 row untuk melihat simulasi."
    detail_section_multi_text = "Simulasi hanya tersedia untuk 1 row terpilih."
    detail_status_icon_map = {"problem": "❌", "info": "🔵", "acceptable": "ℹ️", "balanced": "✅"}

    def _clear_tree(tree: ttk.Treeview) -> None:
        for item_id in tree.get_children():
            tree.delete(item_id)

    def _set_simulation_placeholder(message: str) -> None:
        current_cycle_note_var.set(message)
        simulated_note_var.set(message)
        projected_cycle_note_var.set(message)
        _clear_tree(current_cycle_tree)
        _clear_tree(simulated_tree)
        _clear_tree(projected_cycle_tree)
        simulated_line_by_iid.clear()
        _clear_simulated_line_editor()

    def _render_current_cycle_tree(item_row: Any, *, note_text: str | None = None) -> None:
        _clear_tree(current_cycle_tree)
        item_account_rows = list(getattr(item_row, "account_rows", None) or [])
        item_label = " | ".join(
            part
            for part in (
                normalize_text(getattr(item_row, "default_code", "")),
                normalize_text(getattr(item_row, "product_name", "")),
            )
            if part
        ) or normalize_text(getattr(item_row, "product_name", "")) or "item aktif"
        current_cycle_note_var.set(
            note_text
            or f"Snapshot item aktif (saldo item) | {item_label} | {len(item_account_rows)} akun item."
        )
        for account_row in item_account_rows:
            status = normalize_text(getattr(account_row, "status", "")).lower() or "acceptable"
            current_cycle_tree.insert(
                "",
                "end",
                values=(
                    detail_status_icon_map.get(status, "•"),
                    normalize_text(getattr(account_row, "code", "")),
                    normalize_text(getattr(account_row, "name", "")),
                    f"{self._pcb_detail_amount(getattr(account_row, 'debit', 0.0)):,.2f}",
                    f"{self._pcb_detail_amount(getattr(account_row, 'credit', 0.0)):,.2f}",
                    f"{self._pcb_detail_amount(getattr(account_row, 'net_balance', 0.0)):+,.2f}",
                ),
                tags=(status,),
            )

    def _render_item_cycle_tree(_item_row: Any) -> None:
        return
        item_account_rows = list(getattr(item_row, "account_rows", None) or [])
        item_label = " | ".join(
            part
            for part in (
                normalize_text(getattr(item_row, "default_code", "")),
                normalize_text(getattr(item_row, "product_name", "")),
            )
            if part
        ) or normalize_text(getattr(item_row, "product_name", "")) or "item aktif"
        item_cycle_note_var.set(f"Snapshot item aktif | {item_label} | {len(item_account_rows)} akun item.")
        for account_row in item_account_rows:
            status = normalize_text(getattr(account_row, "status", "")).lower() or "acceptable"
            item_cycle_tree.insert(
                "",
                "end",
                values=(
                    detail_status_icon_map.get(status, "â€¢"),
                    normalize_text(getattr(account_row, "code", "")),
                    normalize_text(getattr(account_row, "name", "")),
                    f"{self._pcb_detail_amount(getattr(account_row, 'debit', 0.0)):,.2f}",
                    f"{self._pcb_detail_amount(getattr(account_row, 'credit', 0.0)):,.2f}",
                    f"{self._pcb_detail_amount(getattr(account_row, 'net_balance', 0.0)):+,.2f}",
                ),
                tags=(status,),
            )

    def _render_simulated_tree(
        simulated_lines: list[dict[str, Any]],
        *,
        unresolved_account: bool = False,
        note_text: str | None = None,
    ) -> None:
        _clear_tree(simulated_tree)
        simulated_line_by_iid.clear()
        rendered_note = note_text or "Belum execute - preview JE only."
        if unresolved_account:
            rendered_note = f"{rendered_note} Ada line simulasi tanpa account code final."
        if not simulated_lines:
            simulated_note_var.set(rendered_note)
            return
        simulated_note_var.set(rendered_note)
        for line in simulated_lines:
            side = normalize_text(line.get("side")).lower()
            row_tags = [side]
            if normalize_text(line.get("role")).lower() in {"manual_simulation", "manual_adjustment"}:
                row_tags.append("manual")
            line_iid = simulated_tree.insert(
                "",
                "end",
                values=(
                    normalize_text(line.get("side_label")) or ("DR" if side == "debit" else "CR"),
                    normalize_text(line.get("account_code")) or "?",
                    normalize_text(line.get("account_name")) or "-",
                    f"{abs(self._pcb_detail_amount(line.get('amount'))):,.2f}",
                    normalize_text(line.get("keterangan")) or "-",
                ),
                tags=tuple(row_tags),
            )
            if line.get("line_index") is not None:
                simulated_line_by_iid[line_iid] = int(line.get("line_index") or 0)

    def _render_projected_cycle_tree(
        projected_rows: list[dict[str, Any]],
        *,
        unresolved_account: bool = False,
        note_text: str | None = None,
    ) -> None:
        _clear_tree(projected_cycle_tree)
        rendered_note = note_text or "Saldo item current + jurnal simulasi per akun."
        if unresolved_account:
            rendered_note = f"{rendered_note} Masih ada line simulasi tanpa account code final."
        projected_cycle_note_var.set(rendered_note)
        for projected_row in projected_rows:
            status = normalize_text(projected_row.get("status")).lower() or "acceptable"
            projected_cycle_tree.insert(
                "",
                "end",
                values=(
                    detail_status_icon_map.get(status, "•"),
                    normalize_text(projected_row.get("code")) or "?",
                    normalize_text(projected_row.get("name")) or "-",
                    f"{self._pcb_detail_amount(projected_row.get('current_balance')):+,.2f}",
                    f"{self._pcb_detail_amount(projected_row.get('simulated_delta')):+,.2f}",
                    f"{self._pcb_detail_amount(projected_row.get('projected_balance')):+,.2f}",
                ),
                tags=(status,),
            )

    def _summary_section_summary_text(
        row: dict[str, Any] | None = None,
        *,
        count: int = 0,
    ) -> str:
        if count > 1:
            return f"{count} row selected"
        if not row:
            return "Compact row summary"
        item_label = self._pcb_case1_item_display_label(row) or normalize_text(row.get("picking_name"))
        return " | ".join(
            part
            for part in (
                item_label,
                self._repair_row_status_label(row),
                build_compact_preview_text(self._pcb_je_preview_text(row), empty_text="", max_chars=52),
            )
            if part
        ) or "Compact row summary"

    def _clear_detail_values(*, empty_text: str = "-") -> None:
        for variable in summary_vars.values():
            variable.set(empty_text)
        for variable in advanced_vars.values():
            variable.set(empty_text)
        summary_section.set_summary("Compact row summary")
        advanced_section.set_summary("Additional link + guard fields")

    def _format_signed_amount(value: Any, *, empty_text: str = "-") -> str:
        try:
            number = float(value or 0.0)
        except (TypeError, ValueError):
            return empty_text
        if abs(number) < 0.01:
            return empty_text
        return f"{number:+,.2f}"

    def _planned_lines_text(row: dict[str, Any]) -> str:
        planned_lines = list(row.get("planned_lines") or [])
        if not planned_lines:
            return "-"
        parts: list[str] = []
        for planned_line in planned_lines:
            if isinstance(planned_line, dict):
                line_mapping = planned_line
            else:
                line_mapping = {
                    field_name: getattr(planned_line, field_name)
                    for field_name in getattr(planned_line, "__dataclass_fields__", {})
                }
            amount = abs(float(line_mapping.get("amount") or 0.0))
            side = normalize_text(line_mapping.get("side")).lower()
            if amount <= 0.0 or side not in {"debit", "credit"}:
                continue
            parts.append(
                f"{self._pcb_case2_planned_line_role_text(line_mapping.get('role'))}: "
                f"{'DR' if side == 'debit' else 'CR'} {normalize_text(line_mapping.get('account_code')) or '?'} {amount:,.2f}"
                f" | {normalize_text(line_mapping.get('line_label')) or '-'}"
            )
        return " | ".join(parts) if parts else "-"

    def _apply_detail_values(row: dict[str, Any]) -> None:
        for field_name in summary_vars:
            if field_name == "stj_refs":
                summary_vars[field_name].set(_stj_refs_summary_text(row))
            elif field_name == "reconcile_status":
                summary_vars[field_name].set(_reconcile_status_summary_text(row))
            elif field_name == "coefficient_variance":
                coefficient_variance = float(row.get("coefficient_variance") or 0.0)
                summary_vars[field_name].set(f"{coefficient_variance:,.2f}%" if coefficient_variance > 0.0 else "-")
            elif field_name == "problem_balances_by_code":
                summary_vars[field_name].set(_balances_by_code_text(row.get(field_name), max_chars=84))
            elif field_name == "external_clearing_amount":
                amount = self._pcb_external_clearing_amount(row)
                summary_vars[field_name].set(f"{amount:,.2f}" if amount >= 0.01 else "-")
            elif field_name == "hpp_balance":
                summary_vars[field_name].set(_format_signed_amount(row.get(field_name)))
            elif field_name == "selisih_hpp_amount":
                amount = abs(float(row.get(field_name) or 0.0))
                summary_vars[field_name].set(f"{amount:,.2f}" if amount >= 0.01 else "-")
            elif field_name == "guard_status":
                summary_vars[field_name].set(self._pcb_guard_summary_text(row))
            elif field_name == "review_status":
                summary_vars[field_name].set(self._pcb_review_status_text(row))
            elif field_name == "resolve_account_preview":
                summary_vars[field_name].set(_render_trace_value(row.get(field_name), empty_text="-", max_chars=84))
            elif field_name == "planned_line_preview":
                summary_vars[field_name].set(self._pcb_je_preview_text(row))
            else:
                summary_vars[field_name].set(_render_trace_value(row.get(field_name)))
        for field_name in advanced_vars:
            if field_name == "stj_candidate_count":
                candidate_count = int(row.get(field_name) or 0)
                advanced_vars[field_name].set(str(candidate_count) if candidate_count > 0 else "-")
                continue
            if field_name in {"suspend_target_aml_ids", "clearing_target_aml_ids"}:
                advanced_vars[field_name].set(_target_aml_field_text(row, field_name))
                continue
            if field_name == "planned_lines":
                advanced_vars[field_name].set(_planned_lines_text(row))
                continue
            if field_name == "external_clearing_verified":
                amount = self._pcb_external_clearing_amount(row)
                verified = bool(row.get(field_name)) and amount >= 0.01
                advanced_vars[field_name].set("Yes" if verified else "No")
                continue
            if field_name in {"review_required", "review_confirmed"}:
                advanced_vars[field_name].set("Yes" if bool(row.get(field_name)) else "No")
                continue
            if field_name == "review_reason":
                advanced_vars[field_name].set(_render_trace_value(row.get(field_name), empty_text="-", max_chars=120))
                continue
            if field_name in {"resolve_account_code", "resolve_account_preview", "suggested_expense_account_code"}:
                advanced_vars[field_name].set(_render_trace_value(row.get(field_name), empty_text="-", max_chars=120))
                continue
            if field_name in {"problem_balances_by_code", "hpp_balances_by_code", "bank_balances_by_code"}:
                advanced_vars[field_name].set(_balances_by_code_text(row.get(field_name), empty_text="-", max_chars=120))
                continue
            if field_name in {"hpp_balance", "inventory_balance", "cogs_variance_balance"}:
                advanced_vars[field_name].set(_format_signed_amount(row.get(field_name)))
                continue
            if field_name == "selisih_hpp_amount":
                amount = abs(float(row.get(field_name) or 0.0))
                advanced_vars[field_name].set(f"{amount:,.2f}" if amount >= 0.01 else "-")
                continue
            if field_name == "guard_messages":
                advanced_vars[field_name].set(
                    _render_trace_value(self._pcb_effective_guard_messages(row), empty_text="-", max_chars=120)
                )
                continue
            advanced_vars[field_name].set(_render_trace_value(row.get(field_name)))
        summary_section.set_summary(_summary_section_summary_text(row))
        advanced_section.set_summary(_advanced_section_summary_text(row))

    def _refresh_detail_wrap(_event: Any = None) -> None:
        available_width = max(180, int(right.winfo_width() or 0) - 170)
        detail_info_label.configure(wraplength=max(240, available_width + 24))
        for label in section_note_labels:
            label.configure(wraplength=max(240, available_width + 24))
        for label in summary_value_labels:
            label.configure(wraplength=available_width)
        for label in advanced_value_labels:
            label.configure(wraplength=available_width)

    def _apply_case1_aggregate_detail_summary(rows: list[dict[str, Any]], *, total_group_count: int) -> None:
        _clear_detail_values()
        if not rows:
            return
        preview_label = (
            f"Preview gabungan {len(rows)} row source"
            if len(rows) >= total_group_count
            else f"Preview gabungan {len(rows)} dari {total_group_count} row source"
        )
        preview_text = build_compact_preview_text(
            " | ".join(
                text
                for text in (self._pcb_je_preview_text(row) for row in rows)
                if text and text != "-"
            ),
            empty_text="-",
            max_chars=84,
        )
        summary_vars["product_id"].set(_render_trace_value(rows[0].get("product_id")))
        summary_vars["picking_id"].set(_render_trace_value(rows[0].get("picking_id")))
        summary_vars["reconcile_status"].set(preview_label)
        summary_vars["planned_line_preview"].set(preview_text)
        summary_section.set_summary(preview_label)
        advanced_section.set_summary("Saldo item agregat, bukan saldo per source line")

    def _on_selection_change(_event: Any = None) -> None:
        if not _dialog_widgets_alive():
            return
        rows = _selected_rows()
        count = len(rows)
        if count == 0:
            active_detail_context.update({"row": None, "cycle": None, "item_row": None})
            detail_info_var.set("Pilih row untuk melihat trace link dan mengedit tanggal/reference/resolve account.")
            edit_date_var.set("")
            edit_ref_var.set("")
            row_resolve_var.set("")
            row_resolve_preview_var.set("Belum dipilih")
            active_resolve_candidates["value"] = []
            row_resolve_picker.set_candidates([])
            _set_resolve_picker_enabled(False)
            _set_simulated_line_candidates(None)
            _set_simulated_line_editor_enabled(False, message="Editor simulasi aktif untuk 1 row Needs Review / Incomplete.")
            _set_simulation_placeholder(detail_section_empty_text)
            _clear_detail_values()
            apply_btn.configure(state="disabled", text="Apply ke Terpilih")
            confirm_review_btn.configure(state="disabled", text="Confirm Review")
            return
        if count == 1:
            row = rows[0]
            self._sync_pcb_case1_generated_flags(row)
            if self._refresh_generated_pcb_case1_texts(row):
                _refresh_tree()
            cycle = self._pcb_detail_cycle_for_row(row)
            item_row = self._pcb_detail_item_row_for_row(row, cycle) if cycle is not None else None
            case1_group_rows = _pcb_case1_group_rows([row])
            case1_group_note = _pcb_case1_group_note_text(
                selected_count=1,
                total_count=len(case1_group_rows),
            )
            if self._pcb_row_uses_planned_lines(row):
                self._sync_pcb_case2_row_defaults(
                    row,
                    preserve_terminal=True,
                    cycle=cycle,
                    item_row=item_row,
                    reset_projected_review_confirmation=False,
                )
            external_clearing_amount = self._pcb_external_clearing_amount(row)
            review_line = ""
            if self._pcb_review_required(row):
                review_line = (
                    f"\nReview: {self._pcb_review_status_text(row)}"
                    f"{f' | {self._pcb_review_reason(row)}' if self._pcb_review_reason(row) else ''}"
                )
            external_clearing_line = (
                f"\nExternal Clearing: {external_clearing_amount:,.2f} | "
                f"{'verified' if bool(row.get('external_clearing_verified')) and external_clearing_amount >= 0.01 else 'not verified'}"
                if external_clearing_amount >= 0.01 or normalize_text(row.get("pcb_case")).lower() in {"case3", "case4"}
                else ""
            )
            preview_line = (
                f"\n{case1_group_note} Execute tetap per row."
                if case1_group_note and not self._pcb_row_uses_planned_lines(row)
                else ""
            )
            detail_info_var.set(
                f"{normalize_text(row.get('picking_name'))}\n"
                f"{normalize_text(row.get('source_label'))} | {self._pcb_guard_summary_text(row) if self._pcb_row_uses_planned_lines(row) else normalize_text(row.get('reconcile_readiness_label'))}\n"
                f"{normalize_text(row.get('row_status_message')) or normalize_text(row.get('row_status'))}"
                f"{review_line}"
                f"{external_clearing_line}"
                f"{preview_line}"
            )
            edit_date_var.set(normalize_text(row.get("date")))
            edit_ref_var.set(normalize_text(row.get("reference")))
            _set_resolve_candidates([row])
            row_resolve_var.set(normalize_text(row.get("resolve_account_code")).upper())
            row_resolve_preview_var.set(
                normalize_text(row.get("resolve_account_preview"))
                or self._lookup_repair_candidate_preview(
                    row_resolve_var.get(),
                    active_resolve_candidates["value"],
                    compact=True,
                )
            )
            _set_resolve_picker_enabled(self._pcb_is_resolve_account_editable(row))
            active_detail_context.update({"row": row, "cycle": cycle, "item_row": item_row})
            _set_simulated_line_candidates(row, cycle=cycle, item_row=item_row)
            simulated_lines = self._pcb_simulated_journal_lines(row, cycle=cycle, item_row=item_row)
            has_unresolved_account = any(
                normalize_text(line.get("account_code")).upper() in {"", "?"}
                for line in simulated_lines
            )
            simulated_note_text = "Belum execute - preview JE only."
            if case1_group_note and not self._pcb_row_uses_planned_lines(row):
                simulated_note_text = f"{simulated_note_text} {case1_group_note}"
            _render_simulated_tree(
                simulated_lines,
                unresolved_account=has_unresolved_account,
                note_text=simulated_note_text,
            )
            if self._pcb_is_resolve_account_editable(row):
                _set_simulated_line_editor_enabled(
                    True,
                    message="Pilih line simulasi untuk edit, atau isi form lalu klik Tambah Line.",
                )
            else:
                _set_simulated_line_editor_enabled(
                    False,
                    message="Editor simulasi hanya aktif saat row berstatus Needs Review / Incomplete.",
                )
            if cycle is None:
                current_cycle_note_var.set("Cycle asal tidak ditemukan pada snapshot aktif.")
                projected_cycle_note_var.set("Cycle asal tidak ditemukan pada snapshot aktif.")
                _clear_tree(current_cycle_tree)
                _clear_tree(projected_cycle_tree)
            elif item_row is None:
                current_cycle_note_var.set("Saldo item asal tidak ditemukan pada snapshot aktif.")
                projected_cycle_note_var.set("Saldo item asal tidak ditemukan pada snapshot aktif.")
                _clear_tree(current_cycle_tree)
                _clear_tree(projected_cycle_tree)
            else:
                _render_current_cycle_tree(item_row)
                if case1_group_note and not self._pcb_row_uses_planned_lines(row):
                    current_cycle_note_var.set(
                        f"{current_cycle_note_var.get()} {case1_group_note} "
                        "Ini tetap saldo item agregat, bukan saldo per source line."
                    )
                projected_rows, projected_has_unresolved = self._pcb_projected_cycle_account_rows(
                    row,
                    item_row=item_row,
                    simulated_lines=simulated_lines,
                )
                projected_note_text = None
                if case1_group_note and not self._pcb_row_uses_planned_lines(row):
                    projected_note_text = (
                        "Saldo item current + jurnal simulasi per akun. "
                        f"{case1_group_note} Ini tetap saldo item agregat, bukan saldo per source line."
                    )
                _render_projected_cycle_tree(
                    projected_rows,
                    unresolved_account=has_unresolved_account or projected_has_unresolved,
                    note_text=projected_note_text,
                )
            _apply_detail_values(row)
        else:
            case1_group_rows = _pcb_case1_group_rows(rows)
            if case1_group_rows:
                row = rows[0]
                cycle = self._pcb_detail_cycle_for_row(row)
                item_row = self._pcb_detail_item_row_for_row(row, cycle) if cycle is not None else None
                case1_group_note = _pcb_case1_group_note_text(
                    selected_count=count,
                    total_count=len(case1_group_rows),
                )
                item_label = (
                    self._pcb_case1_item_display_label(row)
                    or normalize_text(row.get("item_name"))
                    or normalize_text(row.get("picking_name"))
                    or f"Product #{int(row.get('product_id') or 0)}"
                )
                active_detail_context.update({"row": None, "cycle": cycle, "item_row": item_row})
                detail_info_var.set(
                    f"{count} row source item terpilih untuk {item_label}. "
                    "Field date/reference yang diisi akan diterapkan ke semua row terpilih.\n"
                    f"{case1_group_note} Saldo item dan proyeksi di bawah memakai basis item agregat; "
                    "ini bukan saldo per source line. Execute tetap per row."
                )
                edit_date_var.set("")
                edit_ref_var.set("")
                row_resolve_var.set("")
                _set_resolve_candidates(rows)
                row_resolve_preview_var.set("Resolve account hanya bisa diedit untuk row Needs Review / Incomplete.")
                _set_resolve_picker_enabled(False)
                _set_simulated_line_candidates(None)
                _set_simulated_line_editor_enabled(
                    False,
                    message="Editor simulasi hanya tersedia untuk 1 row Needs Review / Incomplete.",
                )
                simulated_lines, has_unresolved_account = _build_case1_group_simulated_lines(
                    rows,
                    cycle=cycle,
                    item_row=item_row,
                )
                _render_simulated_tree(
                    simulated_lines,
                    unresolved_account=has_unresolved_account,
                    note_text=f"Belum execute - preview JE gabungan row terpilih. {case1_group_note}",
                )
                if cycle is None:
                    current_cycle_note_var.set("Cycle asal tidak ditemukan pada snapshot aktif.")
                    projected_cycle_note_var.set("Cycle asal tidak ditemukan pada snapshot aktif.")
                    _clear_tree(current_cycle_tree)
                    _clear_tree(projected_cycle_tree)
                elif item_row is None:
                    current_cycle_note_var.set("Saldo item asal tidak ditemukan pada snapshot aktif.")
                    projected_cycle_note_var.set("Saldo item asal tidak ditemukan pada snapshot aktif.")
                    _clear_tree(current_cycle_tree)
                    _clear_tree(projected_cycle_tree)
                else:
                    item_account_rows = list(getattr(item_row, "account_rows", None) or [])
                    item_row_label = " | ".join(
                        part
                        for part in (
                            normalize_text(getattr(item_row, "default_code", "")),
                            normalize_text(getattr(item_row, "product_name", "")),
                        )
                        if part
                    ) or normalize_text(getattr(item_row, "product_name", "")) or item_label
                    _render_current_cycle_tree(
                        item_row,
                        note_text=(
                            f"Snapshot item aktif (saldo item agregat) | {item_row_label} | "
                            f"{len(item_account_rows)} akun item. {case1_group_note} "
                            "Ini bukan saldo per source line."
                        ),
                    )
                    projected_rows, projected_has_unresolved = self._pcb_projected_cycle_account_rows(
                        row,
                        item_row=item_row,
                        simulated_lines=simulated_lines,
                    )
                    _render_projected_cycle_tree(
                        projected_rows,
                        unresolved_account=has_unresolved_account or projected_has_unresolved,
                        note_text=(
                            "Saldo item agregat + jurnal simulasi gabungan row terpilih per akun. "
                            f"{case1_group_note} Ini bukan saldo per source line."
                        ),
                    )
                _apply_case1_aggregate_detail_summary(rows, total_group_count=len(case1_group_rows))
            else:
                editable_resolve_rows = [row for row in rows if self._pcb_is_resolve_account_editable(row)]
                active_detail_context.update({"row": None, "cycle": None, "item_row": None})
                detail_info_var.set(
                    f"{count} row terpilih. Field date/reference yang diisi akan diterapkan ke semua row terpilih."
                    f"{f' Resolve account diterapkan ke {len(editable_resolve_rows)} row eligible.' if editable_resolve_rows else ' Tidak ada row eligible untuk edit resolve account.'}"
                )
                edit_date_var.set("")
                edit_ref_var.set("")
                row_resolve_var.set("")
                _set_resolve_candidates(rows)
                row_resolve_preview_var.set(
                    "Isi akun target residual/final untuk apply ke row eligible."
                    if editable_resolve_rows
                    else "Resolve account hanya bisa diedit untuk row Needs Review / Incomplete."
                )
                _set_resolve_picker_enabled(bool(editable_resolve_rows))
                _set_simulated_line_candidates(None)
                _set_simulated_line_editor_enabled(
                    False,
                    message="Editor simulasi hanya tersedia untuk 1 row Needs Review / Incomplete.",
                )
                _set_simulation_placeholder(detail_section_multi_text)
                _clear_detail_values()
                summary_vars["reconcile_status"].set(f"{count} row selected")
                summary_section.set_summary(_summary_section_summary_text(count=count))
                advanced_section.set_summary(f"{count} row selected")
        apply_btn.configure(state="normal", text=f"Apply ke {count} Terpilih")
        review_pending_count = sum(
            1
            for row in rows
            if self._pcb_review_required(row)
            and not self._pcb_review_confirmed(row)
            and normalize_text(row.get("row_status")).lower() not in {"repaired", "running"}
            and not self._pcb_row_has_blocking_guard(row)
        )
        confirm_review_btn.configure(
            state="normal" if review_pending_count > 0 else "disabled",
            text=f"Confirm Review ({review_pending_count})" if review_pending_count > 0 else "Confirm Review",
        )

    def _apply_edit() -> None:
        rows = _selected_rows()
        if not rows:
            return
        new_date = normalize_text(edit_date_var.get())
        new_ref = normalize_text(edit_ref_var.get())
        new_resolve = normalize_text(row_resolve_var.get()).upper()
        changed = 0
        resolve_updated = 0
        skipped_resolve = 0
        for row in rows:
            row_key = normalize_text(row.get("row_key"))
            if row_key not in col:
                continue
            row_changed = False
            if new_date:
                col[row_key]["date"] = new_date
                row_changed = True
            if new_ref:
                col[row_key]["reference"] = new_ref
                col[row_key]["reference_generated"] = False
                row_changed = True
            if new_resolve:
                if self._pcb_is_resolve_account_editable(col[row_key]):
                    self._sync_pcb_case2_row_defaults(col[row_key], preserve_terminal=True)
                    suggested_expense_code = normalize_text(col[row_key].get("suggested_expense_account_code")).upper()
                    col[row_key]["resolve_account_manual"] = bool(new_resolve and new_resolve != suggested_expense_code)
                    col[row_key]["resolve_account_code"] = new_resolve
                    self._sync_pcb_case2_row_defaults(col[row_key], preserve_terminal=True)
                    resolve_updated += 1
                    row_changed = True
                else:
                    skipped_resolve += 1
            self._sync_pcb_case1_generated_flags(col[row_key])
            if row_changed:
                changed += 1
        if new_date:
            self._save_pcb_case1_last_date(new_date)
        _refresh_tree()
        _on_selection_change()
        message = f"{changed} row diperbarui."
        if new_resolve:
            message += f" Resolve account applied ke {resolve_updated} row."
            if skipped_resolve > 0:
                message += f" {skipped_resolve} row resolve-skip."
        result_var.set(message)

    apply_btn.configure(command=_apply_edit)

    def _confirm_review_selected() -> None:
        rows = _selected_rows()
        if not rows:
            return
        updated = 0
        skipped = 0
        for row in rows:
            row_key = normalize_text(row.get("row_key"))
            if row_key not in col:
                skipped += 1
                continue
            target_row = col[row_key]
            if (
                not self._pcb_review_required(target_row)
                or self._pcb_review_confirmed(target_row)
                or self._pcb_row_has_blocking_guard(target_row)
                or normalize_text(target_row.get("row_status")).lower() in {"repaired", "running"}
            ):
                skipped += 1
                continue
            target_row["review_confirmed"] = True
            self._sync_pcb_case2_row_defaults(
                target_row,
                reset_projected_review_confirmation=False,
            )
            updated += 1
        _refresh_tree(reset_projected_review_confirmation=False)
        _on_selection_change()
        if updated > 0:
            message = f"{updated} row review-confirmed."
            if skipped > 0:
                message += f" {skipped} row dilewati."
            result_var.set(message)
            return
        result_var.set("Tidak ada row review yang bisa dikonfirmasi dari selection saat ini.")

    confirm_review_btn.configure(command=_confirm_review_selected)
    simulated_line_picker._on_change = _on_simulated_account_change  # noqa: SLF001
    simulated_line_add_btn.configure(command=lambda: _apply_simulated_line_change("add"))
    simulated_line_update_btn.configure(command=lambda: _apply_simulated_line_change("update"))
    simulated_line_remove_btn.configure(command=lambda: _apply_simulated_line_change("remove"))
    simulated_line_reset_btn.configure(command=lambda: _apply_simulated_line_change("reset"))
    tv.bind("<<TreeviewSelect>>", _on_selection_change)
    simulated_tree.bind("<<TreeviewSelect>>", _on_simulated_line_selection_change)
    right.bind("<Configure>", _refresh_detail_wrap, add="+")
    _set_simulated_line_editor_enabled(False, message="Editor simulasi aktif untuk 1 row Needs Review / Incomplete.")
    for row in list(col.values()):
        self._sync_pcb_case1_generated_flags(row)
        self._refresh_generated_pcb_case1_texts(row)
        if self._pcb_row_uses_planned_lines(row):
            self._sync_pcb_case2_row_defaults(row, preserve_terminal=True)
    _refresh_tree()
    dialog.update_idletasks()
    _refresh_detail_wrap()
    request_split_sync()

    tk.Label(
        dialog,
        textvariable=result_var,
        bg=T.BG_CARD,
        fg=T.TEXT_MUTED,
        font=T.font(T.FONT_SMALL_SIZE),
        anchor="w",
    ).grid(row=1, column=0, sticky="ew", padx=10)

    button_row = tk.Frame(dialog, bg=T.BG_CARD, padx=8, pady=6)
    button_row.grid(row=2, column=0, sticky="ew")

    scope_frame = tk.Frame(button_row, bg=T.BG_CARD)
    scope_frame.pack(side="left", padx=(0, 12))
    tk.Label(
        scope_frame,
        text="Scope:",
        bg=T.BG_CARD,
        fg=T.TEXT_MUTED,
        font=T.font(T.FONT_SMALL_SIZE),
    ).pack(side="left", padx=(0, 6))
    tk.Radiobutton(
        scope_frame,
        text="Selected Row(s)",
        value=PCB_CASE1_EXECUTE_SCOPE_SELECTED,
        variable=execute_scope_var,
        bg=T.BG_CARD,
        fg=T.TEXT_ON_LIGHT,
        selectcolor=T.BG_CARD,
        font=T.font(T.FONT_SMALL_SIZE),
        activebackground=T.BG_CARD,
        activeforeground=T.TEXT_ON_LIGHT,
        highlightthickness=0,
    ).pack(side="left", padx=(0, 6))
    tk.Radiobutton(
        scope_frame,
        text="All Rows",
        value=PCB_CASE1_EXECUTE_SCOPE_ALL,
        variable=execute_scope_var,
        bg=T.BG_CARD,
        fg=T.TEXT_ON_LIGHT,
        selectcolor=T.BG_CARD,
        font=T.font(T.FONT_SMALL_SIZE),
        activebackground=T.BG_CARD,
        activeforeground=T.TEXT_ON_LIGHT,
        highlightthickness=0,
    ).pack(side="left")

    def _remove_selected() -> None:
        rows = _selected_rows()
        for row in rows:
            col.pop(normalize_text(row.get("row_key")), None)
        if not col:
            self._pcb_repair_collection_scope = None
        self._update_pcb_collection_button()
        _refresh_tree()
        _on_selection_change()
        result_var.set(f"{len(rows)} row dihapus.")

    def _clear_all() -> None:
        if not messagebox.askyesno(self._display_name, f"Hapus semua {len(col)} row dari PCB Repair Collection?"):
            return
        col.clear()
        self._pcb_repair_collection_scope = None
        self._update_pcb_collection_button()
        _refresh_tree()
        _on_selection_change()
        result_var.set("PCB Repair Collection dikosongkan.")

    def _execute_scope_label(scope_value: str) -> str:
        return "All Rows" if scope_value == PCB_CASE1_EXECUTE_SCOPE_ALL else "Selected Row(s)"

    def _rows_for_execute_scope() -> tuple[list[dict[str, Any]], str]:
        scope_value = normalize_text(execute_scope_var.get()) or PCB_CASE1_EXECUTE_SCOPE_ALL
        if scope_value == PCB_CASE1_EXECUTE_SCOPE_ALL:
            return list(col.values()), _execute_scope_label(scope_value)
        return _selected_rows(), _execute_scope_label(scope_value)

    def _execute(*, post: bool) -> None:
        selected_rows, scope_label = _rows_for_execute_scope()
        if not selected_rows and normalize_text(execute_scope_var.get()) == PCB_CASE1_EXECUTE_SCOPE_SELECTED:
            messagebox.showwarning(self._display_name, "Scope `Selected Row(s)` aktif, tetapi belum ada row yang dipilih.")
            return
        if not selected_rows:
            messagebox.showinfo(self._display_name, "Tidak ada row untuk dijalankan.")
            return
        blocked_rows: list[dict[str, Any]] = []
        for row in selected_rows:
            if self._pcb_row_uses_planned_lines(row):
                self._sync_pcb_case2_row_defaults(
                    row,
                    preserve_terminal=True,
                    reset_projected_review_confirmation=False,
                )
            if normalize_text(row.get("row_status")).lower() in {"incomplete", "needs_review"}:
                blocked_rows.append(row)
        if blocked_rows:
            preview_lines = [
                f"- {normalize_text(row.get('item_code')) or normalize_text(row.get('item_name')) or normalize_text(row.get('picking_name'))}: {normalize_text(row.get('row_status_message')) or self._repair_row_status_label(row)}"
                for row in blocked_rows[:5]
            ]
            extra_count = len(blocked_rows) - len(preview_lines)
            if extra_count > 0:
                preview_lines.append(f"- ... dan {extra_count} row lain masih belum ready.")
            messagebox.showwarning(
                self._display_name,
                "Masih ada row PCB yang belum siap dijalankan.\n\n"
                f"{chr(10).join(preview_lines)}",
            )
            return
        rows_changed = False
        for row in selected_rows:
            self._sync_pcb_case1_generated_flags(row)
            if self._refresh_generated_pcb_case1_texts(row):
                rows_changed = True
        if rows_changed:
            _refresh_tree()
            _on_selection_change()
        if not messagebox.askyesno(
            self._display_name,
            f"Execute {scope_label}: {len(selected_rows)} row.\n"
            f"Mode: {'Execute + Post' if post else 'Execute Draft'}.\n"
            f"Shared Max Workers: {shared_worker_count}.\n\n"
            "Pastikan tanggal dan reference sudah benar.",
        ):
            return
        result_var.set(f"{scope_label} | {len(selected_rows)} row | Memproses dengan shared Max Workers {shared_worker_count}...")
        dialog.update_idletasks()
        self._run_pcb_case1_repair_collection_v2(
            selected_rows,
            post=post,
            on_done=lambda msg, scope_label=scope_label, row_count=len(selected_rows): (
                result_var.set(f"{scope_label} | {row_count} row | {msg}"),
                _refresh_tree(),
                _on_selection_change(),
            ),
        )

    button_style = {
        "bg": T.BRAND_PRIMARY,
        "fg": T.TEXT_ON_DARK,
        "relief": "flat",
        "font": T.font(T.FONT_BODY_SIZE),
        "cursor": "hand2",
        "padx": 10,
        "pady": 3,
        "activebackground": T.BRAND_PRIMARY_DARK,
        "activeforeground": T.TEXT_ON_DARK,
    }
    plain_button_style = {
        "bg": T.BG_CARD,
        "relief": "flat",
        "font": T.font(T.FONT_BODY_SIZE),
        "cursor": "hand2",
        "padx": 8,
        "pady": 3,
    }
    def _reload_row_list() -> None:
        if not _dialog_widgets_alive():
            return
        _refresh_tree()
        _on_selection_change()

    def _close_dialog() -> None:
        if getattr(self, "_last_pcb_case1_dialog_widgets", {}).get("dialog") is dialog:
            self._last_pcb_case1_dialog_widgets = {}
        try:
            dialog.grab_release()
        except Exception:  # noqa: BLE001
            pass
        dialog.destroy()

    execute_draft_button = tk.Button(button_row, text="Execute Draft", command=lambda: _execute(post=False), **button_style)
    execute_draft_button.pack(side="left", padx=(0, 6))
    execute_post_button = tk.Button(button_row, text="Execute + Post", command=lambda: _execute(post=True), **button_style)
    execute_post_button.pack(side="left", padx=(0, 10))
    tk.Label(
        button_row,
        text=f"Shared Max Workers: {shared_worker_count}",
        bg=T.BG_CARD,
        fg=T.TEXT_MUTED,
        font=T.font(T.FONT_SMALL_SIZE),
    ).pack(side="left", padx=(0, 10))
    export_excel_button = tk.Button(
        button_row,
        text="Download Excel",
        command=lambda: self._export_pcb_case1_summary_excel(_selected_rows() or list(col.values())),
        fg=T.TEXT_MUTED,
        **plain_button_style,
    )
    export_excel_button.pack(side="left", padx=(0, 10))
    tk.Button(button_row, text="Hapus Terpilih", command=_remove_selected, fg=T.TEXT_MUTED, **plain_button_style).pack(side="left", padx=(0, 4))
    tk.Button(button_row, text="Clear All", command=_clear_all, fg=T.STATUS_ERROR, **plain_button_style).pack(side="left")
    close_button = tk.Button(button_row, text="Tutup", command=_close_dialog, fg=T.TEXT_MUTED, **plain_button_style)
    close_button.pack(side="right")
    dialog.protocol("WM_DELETE_WINDOW", _close_dialog)
    self._last_pcb_case1_dialog_widgets = {
        "dialog": dialog,
        "content_pane": pane,
        "tree": tv,
        "left_panel": left,
        "left_scrollbar": scrollbar,
        "left_x_scrollbar": x_scrollbar,
        "row_by_iid": row_by_iid,
        "right_host": right_host,
        "right_scroll": right_scroll,
        "right_body": right,
        "measure_split_width": measured_split_width,
        "apply_split_layout": sync_split_layout,
        "remember_split_ratio": remember_current_split_ratio,
        "result_var": result_var,
        "detail_info_var": detail_info_var,
        "current_cycle_title": current_cycle_title,
        "current_cycle_note_var": current_cycle_note_var,
        "current_cycle_tree": current_cycle_tree,
        "simulated_title": simulated_title,
        "simulated_note_var": simulated_note_var,
        "simulated_tree": simulated_tree,
        "simulated_editor_note_var": simulated_editor_note_var,
        "simulated_line_side_var": simulated_line_side_var,
        "simulated_line_account_var": simulated_line_account_var,
        "simulated_line_account_preview_var": simulated_line_account_preview_var,
        "simulated_line_name_var": simulated_line_name_var,
        "simulated_line_amount_var": simulated_line_amount_var,
        "simulated_line_label_var": simulated_line_label_var,
        "simulated_line_picker": simulated_line_picker,
        "simulated_line_add_button": simulated_line_add_btn,
        "simulated_line_update_button": simulated_line_update_btn,
        "simulated_line_remove_button": simulated_line_remove_btn,
        "simulated_line_reset_button": simulated_line_reset_btn,
        "projected_cycle_title": projected_cycle_title,
        "projected_cycle_note_var": projected_cycle_note_var,
        "projected_cycle_tree": projected_cycle_tree,
        "summary_vars": summary_vars,
        "summary_section": summary_section,
        "advanced_vars": advanced_vars,
        "advanced_section": advanced_section,
        "edit_date_var": edit_date_var,
        "edit_ref_var": edit_ref_var,
        "resolve_var": row_resolve_var,
        "resolve_preview_var": row_resolve_preview_var,
        "resolve_picker": row_resolve_picker,
        "apply_button": apply_btn,
        "confirm_review_button": confirm_review_btn,
        "scope_var": execute_scope_var,
        "execute_draft_button": execute_draft_button,
        "execute_post_button": execute_post_button,
        "export_excel_button": export_excel_button,
        "close_button": close_button,
        "get_selected_rows": _selected_rows,
        "reload_row_list": _reload_row_list,
    }


def _run_pcb_case1_repair_collection_v2(
    self,
    rows: "list[dict[str, Any]]",
    *,
    post: bool,
    on_done: "Callable[[str], None] | None" = None,
) -> None:
    _sync_page_globals()
    import asyncio
    import threading

    col = self._pcb_repair_collection
    for row in rows:
        self._save_pcb_case1_last_date(row.get("date"))
    profile_id = self._selected_database_profile_id()
    case1_rows = [row for row in rows if not self._pcb_row_uses_planned_lines(row)]
    case2_rows = [row for row in rows if self._pcb_row_uses_planned_lines(row)]

    def _build_repair_row_payload(row: dict[str, Any], row_type: type[Any]) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        for field_name in row_type.__dataclass_fields__:
            if field_name in row:
                payload[field_name] = row.get(field_name)
        return payload

    def worker() -> None:
        case1_summary = None
        case2_summary = None
        refresh_analysis = False
        try:
            settings, config = self._runtime_builder(
                self.context.global_settings,
                self.logger,
                database_profile_id=profile_id,
            )

            async def _run() -> None:
                nonlocal case1_summary, case2_summary
                async with AsyncOdooJsonRpcClient(config=config, settings=settings, logger=self.logger) as rpc:
                    service = SvlDashboardRepairServiceAsync(
                        rpc=rpc,
                        logger=self.logger,
                        master_cache=self.context.master_cache,
                    )
                    max_workers = max(1, int(getattr(self._module_settings, "max_workers", 1) or 1))
                    database = normalize_text(config.database)
                    if case1_rows:
                        case1_summary = await service.execute_pcb_case1(
                            SvlDashboardPcbCase1RepairRequest(
                                database=database,
                                rows=[
                                    SvlDashboardPcbCase1RepairRow(
                                        **_build_repair_row_payload(row, SvlDashboardPcbCase1RepairRow)
                                    )
                                    for row in case1_rows
                                ],
                                posting_mode="post" if post else "draft",
                                max_workers=max_workers,
                            )
                        )
                    if case2_rows:
                        case2_summary = await service.execute_pcb_case2(
                            SvlDashboardPcbCase2RepairRequest(
                                database=database,
                                rows=[
                                    SvlDashboardPcbCase2RepairRow(
                                        **_build_repair_row_payload(row, SvlDashboardPcbCase2RepairRow)
                                    )
                                    for row in case2_rows
                                ],
                                posting_mode="post" if post else "draft",
                                max_workers=max_workers,
                            )
                        )

            asyncio.run(_run())
        except Exception as exc:  # noqa: BLE001
            error_prefix = "PCB Case 1" if case1_rows and not case2_rows else "PCB Repair"
            self.log_queue.put(f"{error_prefix} error: {exc}")
            self.ui_queue.put(
                {
                    "type": "_pcb_repair_done",
                    "msg": f"{error_prefix} gagal: {exc}",
                    "on_done": on_done,
                    "refresh_analysis": False,
                }
            )
            return

        if case1_rows and case1_summary is None:
            self.ui_queue.put(
                {
                    "type": "_pcb_repair_done",
                    "msg": "PCB Case 1 gagal: summary kosong.",
                    "on_done": on_done,
                    "refresh_analysis": False,
                }
            )
            return
        if case2_rows and case2_summary is None:
            self.ui_queue.put(
                {
                    "type": "_pcb_repair_done",
                    "msg": "PCB Repair multi-line gagal: summary kosong.",
                    "on_done": on_done,
                    "refresh_analysis": False,
                }
            )
            return

        if case1_summary is not None:
            for result in case1_summary.results:
                row_key = normalize_text(result.row_key)
                if row_key not in col:
                    continue
                terminal_success = bool(
                    result.posted
                    or int(result.move_id or 0) > 0
                    or normalize_text(result.status).upper() in {"CREATED", "POSTED"}
                )
                refresh_analysis = refresh_analysis or terminal_success
                col[row_key]["row_status"] = "repaired" if terminal_success else "error"
                col[row_key]["row_status_message"] = (
                    normalize_text(result.message)
                    or normalize_text(result.error_kind)
                    or normalize_text(result.status)
                )
                col[row_key]["result_status"] = normalize_text(result.status)
                col[row_key]["result_posted"] = bool(result.posted)
                col[row_key]["result_error_kind"] = normalize_text(result.error_kind)
                col[row_key]["result_move_id"] = int(result.move_id or 0)
                col[row_key]["result_move_name"] = normalize_text(result.move_name)
                col[row_key]["existing_move_detected"] = bool(getattr(result, "existing_move_detected", False))
                col[row_key]["reconcile_attempted"] = bool(result.reconcile_attempted)
                col[row_key]["reconcile_performed"] = bool(result.reconcile_performed)
                col[row_key]["reconcile_skipped"] = bool(result.reconcile_skipped)
                col[row_key]["reconcile_message"] = normalize_text(result.reconcile_message)
                col[row_key]["reconcile_error_kind"] = normalize_text(getattr(result, "reconcile_error_kind", ""))
                col[row_key]["reconcile_ready"] = False
                col[row_key]["reconcile_readiness_label"] = "Disabled"

        if case2_summary is not None:
            for result in case2_summary.results:
                row_key = normalize_text(result.row_key)
                if row_key not in col:
                    continue
                terminal_success = bool(
                    result.posted
                    or int(result.move_id or 0) > 0
                    or normalize_text(result.status).upper() in {"CREATED", "POSTED"}
                )
                refresh_analysis = refresh_analysis or terminal_success
                col[row_key]["row_status"] = "repaired" if terminal_success else "error"
                col[row_key]["row_status_message"] = (
                    normalize_text(result.message)
                    or normalize_text(result.error_kind)
                    or normalize_text(result.status)
                )
                col[row_key]["result_status"] = normalize_text(result.status)
                col[row_key]["result_posted"] = bool(result.posted)
                col[row_key]["result_error_kind"] = normalize_text(result.error_kind)
                col[row_key]["result_move_id"] = int(result.move_id or 0)
                col[row_key]["result_move_name"] = normalize_text(result.move_name)
                col[row_key]["existing_move_detected"] = bool(getattr(result, "existing_move_detected", False))
                col[row_key]["reconcile_attempted"] = False
                col[row_key]["reconcile_performed"] = False
                col[row_key]["reconcile_skipped"] = False
                col[row_key]["reconcile_message"] = ""
                col[row_key]["reconcile_error_kind"] = ""

        total_created = int(getattr(case1_summary, "created_count", 0) or 0) + int(getattr(case2_summary, "created_count", 0) or 0)
        total_existing = int(getattr(case1_summary, "existing_count", 0) or 0) + int(getattr(case2_summary, "existing_count", 0) or 0)
        total_posted = int(getattr(case1_summary, "posted_count", 0) or 0) + int(getattr(case2_summary, "posted_count", 0) or 0)
        total_error = int(getattr(case1_summary, "error_count", 0) or 0) + int(getattr(case2_summary, "error_count", 0) or 0)
        if case1_rows and not case2_rows and case1_summary is not None:
            message = (
                f"Selesai: {case1_summary.created_count} JE dibuat"
                f"{f', {case1_summary.existing_count} existing' if int(getattr(case1_summary, 'existing_count', 0) or 0) > 0 else ''}"
                f"{f', {case1_summary.posted_count} posted' if post else ''}"
                f", {case1_summary.error_count} error."
                " Auto reconcile PCB Case 1 disabled."
            )
        else:
            message = (
                f"Selesai: {total_created} JE dibuat"
                f"{f', {total_existing} existing' if total_existing > 0 else ''}"
                f"{f', {total_posted} posted' if post else ''}"
                f", {total_error} error."
            )
            if case1_rows:
                message += " Auto reconcile PCB Case 1 disabled."
        self.ui_queue.put(
            {
                "type": "_pcb_repair_done",
                "msg": message,
                "on_done": on_done,
                "refresh_analysis": refresh_analysis,
            }
        )

    threading.Thread(target=worker, daemon=True, name="pcb-repair").start()


def _open_pcb_repair_dialog(self) -> None:
    _sync_page_globals()
    self._open_pcb_case1_repair_dialog_v2()
    return

    """Open the PCB Repair Collection dialog."""
    col = getattr(self, "_pcb_repair_collection", {})
    if not col:
        messagebox.showwarning(self._display_name, "PCB Repair Collection masih kosong.\nTambahkan cycle bermasalah terlebih dahulu via klik kanan sidebar.")
        return
    scope = getattr(self, "_pcb_repair_collection_scope", None)
    current = self._current_repair_collection_scope()
    if scope is not None and current is not None and not scope.matches(current):
        messagebox.showwarning(
            self._display_name,
            f"PCB Collection aktif di scope lain: {self._repair_collection_scope_text() or '-'}.\n"
            "Kembali ke scope asal atau clear collection terlebih dahulu.",
        )
        return

    dialog = tk.Toplevel(self.root)
    dialog.title(f"PCB Repair Collection — {len(col)} cycle")
    dialog.geometry("1300x620")
    dialog.minsize(900, 500)
    dialog.transient(self.root)
    dialog.grab_set()
    dialog.configure(bg=T.BG_CARD)
    dialog.rowconfigure(0, weight=1)
    dialog.columnconfigure(0, weight=1)

    # ── Main container ───────────────────────────────────────────────
    main = tk.Frame(dialog, bg=T.BG_CARD)
    main.grid(row=0, column=0, sticky="nsew")
    main.rowconfigure(1, weight=1)
    main.columnconfigure(0, weight=1)

    # ── Header ──────────────────────────────────────────────────────────
    hdr = tk.Frame(main, bg=T.BG_CARD, pady=6, padx=10)
    hdr.grid(row=0, column=0, sticky="ew")
    tk.Label(hdr, text="PCB Repair Collection", bg=T.BG_CARD,
             font=T.font(T.FONT_BODY_SIZE + 1, bold=True)).pack(side="left")
    scope_text = (f"{scope.database_label} / {scope.company_label}") if scope else ""
    if scope_text:
        tk.Label(hdr, text=f"  [{scope_text}]", bg=T.BG_CARD,
                 fg=T.TEXT_MUTED, font=T.font(T.FONT_SMALL_SIZE)).pack(side="left")

    # ── PanedWindow: left=treeview, right=edit panel ─────────────────
    pane = tk.PanedWindow(main, orient="horizontal", sashrelief="flat", sashwidth=6,
                          bg=T.BG_CARD, bd=0, opaqueresize=True)
    pane.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 2))

    # Left: treeview
    left = tk.Frame(pane, bg=T.BG_CARD)
    left.columnconfigure(0, weight=1)
    left.rowconfigure(0, weight=1)

    tv_cols = ("picking_name", "pcb_case", "amount", "debit_akun", "kredit_akun", "tanggal", "referensi", "status")
    col_labels = {
        "picking_name": "Picking",
        "pcb_case":     "Case",
        "amount":       "Nominal",
        "debit_akun":   "Debit Akun",
        "kredit_akun":  "Kredit Akun",
        "tanggal":      "Tanggal JE",
        "referensi":    "Referensi",
        "status":       "Status",
    }
    col_widths = {
        "picking_name": 140, "pcb_case": 160, "amount": 90,
        "debit_akun": 70, "kredit_akun": 70, "tanggal": 80,
        "referensi": 200, "status": 90,
    }

    tv = ttk.Treeview(left, columns=tv_cols, show="headings", selectmode="extended")
    for c in tv_cols:
        tv.heading(c, text=col_labels[c])
        tv.column(c, width=col_widths.get(c, 100), anchor="w", stretch=(c == "referensi"))
    tv.tag_configure("case1", background="#fff0f0")
    tv.tag_configure("case2", background="#fff8e8")
    tv.tag_configure("case3", background="#fffbe8")
    tv.tag_configure("case4", background="#f0f8ff")
    tv.tag_configure("case5", background="#eefbf2")
    tv.tag_configure("case6", background="#eef6ff")
    tv.tag_configure("case8a", background="#fff3f0")
    tv.tag_configure("case8b", background="#fff3f0")
    tv.tag_configure("case9", background="#f2f4ff")
    tv.tag_configure("case10", background="#fce4ec")
    tv.tag_configure("repaired", foreground=T.STATUS_SUCCESS)
    tv.tag_configure("error_row", foreground=T.STATUS_ERROR)

    sb = ttk.Scrollbar(left, orient="vertical", command=tv.yview)
    tv.configure(yscrollcommand=sb.set)
    tv.grid(row=0, column=0, sticky="nsew")
    sb.grid(row=0, column=1, sticky="ns")

    _iid_by_rowkey: dict[str, str] = {}
    _rows_by_iid: dict[str, dict] = {}

    case_label_short = {
        "case1": "Case 1 - Clearing/Suspend",
        "case2": "Case 2 - Suspend/Suspend",
        "case3": "Case 3 - Clearing/Expenses",
        "case4": "Case 4 - Suspend/Expenses",
        "case5": "Case 5 - No STJ Suspend",
        "case6": "Case 6 - No STJ Expenses",
        "case8a": "Case 8A - Full Return",
        "case8b": "Case 8B - Partial Return",
        "case9": "Case 9 - UoM Scale",
        "case10": "Case 10 - Belum Ada Bill Vendor",
        "edge_partial_bill": "Edge - Partial Bill",
        "edge_return_no_credit_memo": "Edge - Return No Credit Memo",
        "edge_stj_corrupt": "Edge - STJ Corrupt",
        "case_lainnya": "Case Lainnya",
    }

    result_var = tk.StringVar(value="")

    def _populate_tree() -> None:
        prev_sel_keys = {
            _rows_by_iid[iid].get("row_key", "") for iid in tv.selection() if iid in _rows_by_iid
        }
        for ch in tv.get_children():
            tv.delete(ch)
        _iid_by_rowkey.clear()
        _rows_by_iid.clear()
        for row in list(col.values()):
            case = row.get("pcb_case", "case_lainnya")
            tag = case if case in ("case1", "case2", "case3", "case4", "case5", "case6", "case8a", "case8b", "case9", "case10") else ""
            st = row.get("row_status", "incomplete")
            if st == "repaired":
                tag = "repaired"
            elif st == "error":
                tag = "error_row"
            iid = tv.insert("", "end", tags=(tag,), values=(
                row.get("picking_name", ""),
                case_label_short.get(case, case),
                f"{abs(float(row.get('amount') or 0)):,.2f}",
                row.get("debit_account_code", ""),
                row.get("credit_account_code", ""),
                row.get("date", ""),
                row.get("reference", ""),
                row.get("row_status", "incomplete"),
            ))
            _iid_by_rowkey[row.get("row_key", "")] = iid
            _rows_by_iid[iid] = row
        to_sel = [iid for iid, r in _rows_by_iid.items() if r.get("row_key", "") in prev_sel_keys]
        if to_sel:
            tv.selection_set(to_sel)
        dialog.title(f"PCB Repair Collection — {len(col)} cycle")

    _populate_tree()

    # Right: edit panel
    right = tk.Frame(pane, bg=T.BG_CARD, padx=12, pady=10)
    right.columnconfigure(1, weight=1)

    pane.add(left, minsize=520, stretch="always")
    pane.add(right, minsize=270, stretch="never")

    tk.Label(right, text="Edit Row", bg=T.BG_CARD, fg=T.TEXT_ON_LIGHT,
             font=T.font(T.FONT_BODY_SIZE, bold=True)).grid(
                 row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))

    edit_info_var = tk.StringVar(value="Pilih row untuk mengedit.")
    tk.Label(right, textvariable=edit_info_var, bg=T.BG_CARD, fg=T.TEXT_MUTED,
             font=T.font(T.FONT_SMALL_SIZE), anchor="w", justify="left",
             wraplength=250).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 10))

    tk.Label(right, text="Tanggal JE", bg=T.BG_CARD, fg=T.TEXT_ON_LIGHT,
             font=T.font(T.FONT_SMALL_SIZE)).grid(row=2, column=0, columnspan=2, sticky="w", pady=(0, 2))
    edit_date_var = tk.StringVar(value="")
    tk.Entry(right, textvariable=edit_date_var,
             font=T.font(T.FONT_SMALL_SIZE), width=14).grid(
                 row=3, column=0, columnspan=2, sticky="ew", pady=(0, 8))

    tk.Label(right, text="Referensi", bg=T.BG_CARD, fg=T.TEXT_ON_LIGHT,
             font=T.font(T.FONT_SMALL_SIZE)).grid(row=4, column=0, columnspan=2, sticky="w", pady=(0, 2))
    edit_ref_var = tk.StringVar(value="")
    tk.Entry(right, textvariable=edit_ref_var,
             font=T.font(T.FONT_SMALL_SIZE), width=30).grid(
                 row=5, column=0, columnspan=2, sticky="ew", pady=(0, 12))

    apply_btn = tk.Button(right, text="Apply ke Terpilih", relief="flat",
                          bg=T.BRAND_PRIMARY, fg=T.TEXT_ON_DARK,
                          font=T.font(T.FONT_SMALL_SIZE, bold=True),
                          activebackground=T.BRAND_PRIMARY_DARK, activeforeground=T.TEXT_ON_DARK,
                          padx=10, pady=4, cursor="hand2", state="disabled")
    apply_btn.grid(row=6, column=0, columnspan=2, sticky="w")

    tk.Label(right, text="Kosongkan field = tidak ubah nilai.", bg=T.BG_CARD,
             fg=T.TEXT_MUTED, font=T.font(T.FONT_SMALL_SIZE - 1)).grid(
                 row=7, column=0, columnspan=2, sticky="w", pady=(4, 0))

    def _get_selected_rows() -> "list[dict]":
        sel = tv.selection()
        return [_rows_by_iid[iid] for iid in sel if iid in _rows_by_iid]

    def _on_selection_change(_event: Any = None) -> None:
        sel_rows = _get_selected_rows()
        n = len(sel_rows)
        if n == 0:
            edit_info_var.set("Pilih row untuk mengedit.")
            edit_date_var.set("")
            edit_ref_var.set("")
            apply_btn.configure(state="disabled", text="Apply ke Terpilih")
        else:
            first = sel_rows[0]
            if n == 1:
                edit_info_var.set(
                    f"{first.get('picking_name', '')}  "
                    f"[{case_label_short.get(first.get('pcb_case', ''), '')}]"
                )
                edit_date_var.set(first.get("date", ""))
                edit_ref_var.set(first.get("reference", ""))
            else:
                edit_info_var.set(f"{n} row terpilih.\nField yang diisi diterapkan ke semua row terpilih.")
                edit_date_var.set("")
                edit_ref_var.set("")
            apply_btn.configure(state="normal", text=f"Apply ke {n} Terpilih")

    def _apply_edit() -> None:
        sel_rows = _get_selected_rows()
        if not sel_rows:
            return
        new_date = edit_date_var.get().strip()
        new_ref = edit_ref_var.get().strip()
        changed = 0
        for row in sel_rows:
            rk = row.get("row_key", "")
            if rk in col:
                if new_date:
                    col[rk]["date"] = new_date
                if new_ref:
                    col[rk]["reference"] = new_ref
                changed += 1
        _populate_tree()
        result_var.set(f"{changed} row diperbarui.")

    apply_btn.configure(command=_apply_edit)
    tv.bind("<<TreeviewSelect>>", _on_selection_change)

    # ── Result label ────────────────────────────────────────────────────
    tk.Label(dialog, textvariable=result_var, bg=T.BG_CARD,
             fg=T.TEXT_MUTED, font=T.font(T.FONT_SMALL_SIZE),
             anchor="w").grid(row=1, column=0, sticky="ew", padx=10)

    # ── Buttons ─────────────────────────────────────────────────────────
    btn_frame = tk.Frame(dialog, bg=T.BG_CARD, pady=6, padx=8)
    btn_frame.grid(row=2, column=0, sticky="ew")

    def _remove_selected() -> None:
        for row in _get_selected_rows():
            col.pop(row.get("row_key", ""), None)
        if not col:
            self._pcb_repair_collection_scope = None
        self._update_pcb_collection_button()
        _on_selection_change()
        _populate_tree()
        result_var.set(f"{len(col)} row tersisa.")

    def _clear_all() -> None:
        if not messagebox.askyesno(self._display_name, f"Hapus semua {len(col)} row dari PCB Collection?"):
            return
        col.clear()
        self._pcb_repair_collection_scope = None
        self._update_pcb_collection_button()
        _on_selection_change()
        _populate_tree()
        result_var.set("PCB Collection dikosongkan.")

    def _execute(*, post: bool) -> None:
        sel_rows = _get_selected_rows() or list(col.values())
        case1_rows = [r for r in sel_rows if r.get("pcb_case") == "case1"]
        if not case1_rows:
            messagebox.showinfo(self._display_name,
                "Tidak ada row Case 1 yang terpilih.\nEksekusi saat ini hanya mendukung Case 1.")
            return
        if not messagebox.askyesno(
            self._display_name,
            f"Buat {len(case1_rows)} Journal Entry koreksi"
            f"{'  + langsung Post' if post else ' (Draft)'}?\n\n"
            "Pastikan tanggal dan referensi sudah benar sebelum melanjutkan.",
        ):
            return
        result_var.set(f"Memproses {len(case1_rows)} row...")
        dialog.update_idletasks()
        self._run_pcb_repair_collection(case1_rows, post=post,
                                        on_done=lambda msg: (result_var.set(msg), _populate_tree()))

    _btn_kw = {"bg": T.BRAND_PRIMARY, "fg": T.TEXT_ON_DARK, "relief": "flat",
               "font": T.font(T.FONT_BODY_SIZE), "cursor": "hand2", "padx": 10, "pady": 3,
               "activebackground": T.BRAND_PRIMARY_DARK, "activeforeground": T.TEXT_ON_DARK}
    _btn_plain = {"bg": T.BG_CARD, "relief": "flat",
                  "font": T.font(T.FONT_BODY_SIZE), "cursor": "hand2", "padx": 8, "pady": 3}
    tk.Button(btn_frame, text="Execute Draft",
              command=lambda: _execute(post=False), **_btn_kw).pack(side="left", padx=(0, 6))
    tk.Button(btn_frame, text="Execute + Post",
              command=lambda: _execute(post=True), **_btn_kw).pack(side="left", padx=(0, 12))
    tk.Button(btn_frame, text="Hapus Terpilih",
              command=_remove_selected, fg=T.TEXT_MUTED, **_btn_plain).pack(side="left", padx=(0, 4))
    tk.Button(btn_frame, text="Clear All",
              command=_clear_all, fg=T.STATUS_ERROR, **_btn_plain).pack(side="left")
    tk.Button(btn_frame, text="Tutup",
              command=dialog.destroy, fg=T.TEXT_MUTED, **_btn_plain).pack(side="right")


def _run_pcb_repair_collection(
    self,
    rows: "list[dict[str, Any]]",
    *,
    post: bool,
    on_done: "Callable[[str], None] | None" = None,
) -> None:
    _sync_page_globals()
    self._run_pcb_case1_repair_collection_v2(rows, post=post, on_done=on_done)
    return

    """Run PCB repair execution in a background thread."""
    import asyncio
    import threading
    from smartscc_tools.features.svl_fix_je.dashboard_repair_service import SvlDashboardRepairServiceAsync
    from smartscc_tools.services.odoo.gateway import AsyncOdooJsonRpcClient

    col = self._pcb_repair_collection
    profile_id = self._selected_database_profile_id()

    def worker() -> None:
        results: list[dict] = []
        try:
            settings, config = self._runtime_builder(
                self.context.global_settings,
                self.logger,
                database_profile_id=profile_id,
            )

            async def _run() -> None:
                async with AsyncOdooJsonRpcClient(config=config, settings=settings, logger=self.logger) as rpc:
                    service = SvlDashboardRepairServiceAsync(
                        rpc=rpc,
                        logger=self.logger,
                        master_cache=self.context.master_cache,
                    )
                    for row in rows:
                        try:
                            result = await service.execute_pcb_repair_row(row, post=post)
                        except Exception as exc:  # noqa: BLE001
                            result = {"status": "ERROR", "message": str(exc)}
                        row_key = row.get("row_key", "")
                        if row_key in col:
                            col[row_key]["row_status"] = "repaired" if result.get("status") not in ("ERROR",) else "error"
                            col[row_key]["row_status_message"] = result.get("message", result.get("status", ""))
                            if result.get("move_id"):
                                col[row_key]["result_move_id"] = result["move_id"]
                        results.append(result)

            asyncio.run(_run())
        except Exception as exc:  # noqa: BLE001
            results.append({"status": "ERROR", "message": str(exc)})
            self.log_queue.put(f"PCB repair error: {exc}")

        ok = sum(1 for r in results if r.get("status") not in ("ERROR",))
        err = len(results) - ok
        msg = f"Selesai: {ok} JE berhasil dibuat"
        if post:
            posted = sum(1 for r in results if r.get("posted"))
            msg += f" ({posted} posted)"
        if err:
            msg += f", {err} error"
        msg += "."
        self.ui_queue.put({"type": "_pcb_repair_done", "msg": msg, "on_done": on_done})

    threading.Thread(target=worker, daemon=True, name="pcb-repair").start()

def _build_pcb_cycle_detail_export_payload(self, cycle: Any) -> dict[str, Any]:
    snapshot = getattr(self, "_latest_snapshot", None)
    database = normalize_text(getattr(snapshot, "database", ""))
    company_id = int(getattr(snapshot, "company_id", 0) or 0)
    company_name = normalize_text(getattr(snapshot, "company_name", ""))
    cycle_name = normalize_text(getattr(cycle, "picking_name", ""))
    cycle_status = normalize_text(getattr(cycle, "cycle_status", ""))
    cycle_partner = normalize_text(getattr(cycle, "partner_name", ""))
    gr_date = normalize_text(getattr(cycle, "gr_date", ""))[:10]
    po_label = ", ".join(list(getattr(cycle, "purchase_orders", None) or []))
    sort_mode = self._pcb_current_raw_sort_mode()
    sorted_raw_lines = self._pcb_sorted_raw_lines(cycle, sort_mode=sort_mode)

    raw_rows: list[dict[str, Any]] = []
    for row_no, line in enumerate(sorted_raw_lines, start=1):
        raw_rows.append(
            {
                "row_no": row_no,
                "company_name": company_name,
                "company_id": company_id,
                "database": database,
                "cycle_name": cycle_name,
                "cycle_status": cycle_status,
                "cycle_partner": cycle_partner,
                "gr_date": gr_date,
                "po_label": po_label,
                "process_group": self._pcb_process_label(line),
                "tanggal": normalize_text(line.get("tanggal"))[:10],
                "kode_transaksi": normalize_text(line.get("kode_transaksi")),
                "jenis": normalize_text(line.get("jenis")),
                "tipe_akun": normalize_text(line.get("tipe_akun")),
                "akun_code": normalize_text(line.get("akun_code")),
                "akun_name": normalize_text(line.get("akun_name")),
                "kode_item": normalize_text(line.get("kode_item")),
                "nama_item": normalize_text(line.get("nama_item")),
                "uom": normalize_text(line.get("uom")),
                "qty_item": float(line.get("qty_item") or 0.0) if line.get("qty_item") not in ("", None) else None,
                "kategori_produk": normalize_text(line.get("kategori_produk")),
                "no_po": normalize_text(line.get("no_po")),
                "komunikasi": normalize_text(line.get("komunikasi")),
                "debit": round(float(line.get("debit") or 0.0), 2),
                "kredit": round(float(line.get("kredit") or 0.0), 2),
                "saldo": round(float(line.get("saldo") or 0.0), 2),
                "matching": normalize_text(line.get("matching")),
                "partner_row": normalize_text(line.get("partner")),
            }
        )

    detail_rows: list[dict[str, Any]] = []
    detail_row_no = 1

    def _add_detail_row(
        *,
        scope_level: str,
        row_kind: str,
        item_code: str = "",
        item_name: str = "",
        date: str = "",
        journal_source: str = "",
        transaction_no: str = "",
        gr_reference: str = "",
        po: str = "",
        partner_reference: str = "",
        akun: str = "",
        akun_name: str = "",
        debit: float | None = None,
        credit: float | None = None,
        balance: float | None = None,
        matching: str = "",
        note_detail: str = "",
    ) -> None:
        nonlocal detail_row_no
        detail_rows.append(
            {
                "row_no": detail_row_no,
                "scope_level": scope_level,
                "row_kind": row_kind,
                "company_name": company_name,
                "company_id": company_id,
                "database": database,
                "cycle_name": cycle_name,
                "cycle_status": cycle_status,
                "cycle_partner": cycle_partner,
                "item_code": item_code,
                "item_name": item_name,
                "date": date,
                "journal_source": journal_source,
                "transaction_no": transaction_no,
                "gr_reference": gr_reference,
                "po": po,
                "partner_reference": partner_reference,
                "akun": akun,
                "akun_name": akun_name,
                "debit": debit,
                "credit": credit,
                "balance": balance,
                "matching": matching,
                "note_detail": note_detail,
            }
        )
        detail_row_no += 1

    def _refs_label(refs: list[str], *, limit: int = 3) -> str:
        clean_refs = [normalize_text(value) for value in list(refs or []) if normalize_text(value)]
        if not clean_refs:
            return "-"
        label = ", ".join(clean_refs[:limit])
        if len(clean_refs) > limit:
            label += f" (+{len(clean_refs) - limit})"
        return label

    cycle_icon = self._PCB_STATUS_ICON.get(cycle_status, "")
    cycle_accounts = list(getattr(cycle, "account_rows", None) or [])
    cycle_total_debit = round(float(getattr(cycle, "total_debit", 0.0) or 0.0), 2)
    cycle_total_credit = round(float(getattr(cycle, "total_credit", 0.0) or 0.0), 2)
    cycle_net_balance = round(cycle_total_debit - cycle_total_credit, 2)
    _add_detail_row(
        scope_level="cycle",
        row_kind="cycle_summary",
        date=gr_date,
        journal_source=f"{cycle_icon} Cycle".strip(),
        transaction_no=_refs_label(list(getattr(cycle, "stj_refs", None) or [])),
        gr_reference=cycle_name,
        po=po_label,
        partner_reference=cycle_partner[:40],
        akun_name=(
            f"Bill: {_refs_label(list(getattr(cycle, 'bill_refs', None) or []))}  "
            f"Pmt: {_refs_label(list(getattr(cycle, 'payment_refs', None) or []))}  "
            f"BK: {_refs_label(list(getattr(cycle, 'bank_refs', None) or []))}"
        ).strip(),
        debit=cycle_total_debit,
        credit=cycle_total_credit,
        balance=cycle_net_balance,
        matching=f"{int(getattr(cycle, 'problem_account_count', 0) or 0)} akun bermasalah",
    )

    adjustment_warning_text = normalize_text(getattr(cycle, "adjustment_warning_text", ""))
    if adjustment_warning_text:
        _add_detail_row(
            scope_level="cycle",
            row_kind="cycle_adjustment_warning",
            journal_source="Audit",
            akun_name=adjustment_warning_text,
            note_detail="Warning adjustment audit cycle",
        )

    for audit_row in list(getattr(cycle, "adjustment_audit_rows", None) or []):
        move_label = normalize_text(getattr(audit_row, "move_name", "")) or normalize_text(getattr(audit_row, "move_ref", ""))
        _add_detail_row(
            scope_level="cycle",
            row_kind="cycle_adjustment_audit",
            date=normalize_text(getattr(audit_row, "move_date", ""))[:10],
            journal_source="Adj Audit",
            transaction_no=move_label,
            gr_reference=normalize_text(getattr(audit_row, "move_ref", "")),
            akun=normalize_text(getattr(audit_row, "clearing_account_code", "")),
            akun_name=self._pcb_adjustment_summary_text(audit_row),
            matching=self._pcb_adjustment_match_text(audit_row),
            note_detail="Adjustment audit row",
        )

    all_picking_names = list(getattr(cycle, "picking_names", None) or [])
    if len(all_picking_names) > 1:
        _add_detail_row(
            scope_level="cycle",
            row_kind="cycle_multi_picking",
            journal_source="Multi-picking",
            gr_reference=", ".join(all_picking_names),
            akun_name=f"Cycle digabung: {len(all_picking_names)} picking share 1 bill",
            note_detail="Cycle merge multi-picking",
        )

    icon_map = {"balanced": "✅", "acceptable": "ℹ️", "info": "🔵", "problem": "❌"}
    for account_row in cycle_accounts:
        status = normalize_text(getattr(account_row, "status", ""))
        _add_detail_row(
            scope_level="cycle",
            row_kind="cycle_account",
            journal_source=normalize_text(getattr(account_row, "account_type", "")),
            akun=normalize_text(getattr(account_row, "code", "")),
            akun_name=normalize_text(getattr(account_row, "name", ""))[:60],
            debit=round(float(getattr(account_row, "debit", 0.0) or 0.0), 2),
            credit=round(float(getattr(account_row, "credit", 0.0) or 0.0), 2),
            balance=round(float(getattr(account_row, "net_balance", 0.0) or 0.0), 2),
            matching=(icon_map.get(status, "") + " " + status).strip(),
            note_detail="Saldo akun level cycle",
        )

    for item_row in list(getattr(cycle, "item_rows", None) or []):
        item_code = normalize_text(getattr(item_row, "default_code", ""))
        item_name = normalize_text(getattr(item_row, "product_name", "")) or f"Product #{int(getattr(item_row, 'product_id', 0) or 0)}"
        item_accounts = list(getattr(item_row, "account_rows", None) or [])
        item_total_debit = round(sum(float(getattr(row, "debit", 0.0) or 0.0) for row in item_accounts), 2)
        item_total_credit = round(sum(float(getattr(row, "credit", 0.0) or 0.0) for row in item_accounts), 2)
        item_net_balance = round(item_total_debit - item_total_credit, 2)
        problem_count = sum(1 for row in item_accounts if normalize_text(getattr(row, "status", "")).lower() == "problem")
        valuation_method = normalize_text(getattr(item_row, "valuation_method", "")).lower()
        valuation_label = "🔄 Automated" if valuation_method != "manual" else "📋 Manual"
        _add_detail_row(
            scope_level="item",
            row_kind="item_summary",
            item_code=item_code,
            item_name=item_name,
            date=gr_date,
            journal_source=f"📦 {valuation_label}",
            gr_reference=cycle_name,
            po=po_label,
            partner_reference=cycle_partner[:40],
            akun_name=item_name[:60],
            debit=item_total_debit,
            credit=item_total_credit,
            balance=item_net_balance,
            matching=f"{cycle_icon} {problem_count} akun bermasalah".strip(),
            note_detail="Ringkasan Detail Transaksi per Akun per item",
        )

        bill_item_refs = ", ".join(list(getattr(item_row, "bill_refs", None) or [])) or "No bill item"
        stj_link_text = self._pcb_item_stj_link_text(item_row)
        verified_clearing_amount = self._pcb_external_clearing_amount(item_row)
        external_clearing_refs = ", ".join(
            [
                normalize_text(value)
                for value in list(getattr(item_row, "external_clearing_refs", None) or [])
                if normalize_text(value)
            ]
        ) or "Not verified"
        external_clearing_basis = normalize_text(getattr(item_row, "external_clearing_basis", "")) or "Not verified"
        external_clearing_verified = bool(getattr(item_row, "external_clearing_verified", False)) and verified_clearing_amount >= 0.01
        eligibility_label = "Eligible Case 3/4" if bool(getattr(item_row, "eligible_case34", False)) else "Not Eligible Case 3/4"
        _add_detail_row(
            scope_level="item",
            row_kind="item_evidence",
            item_code=item_code,
            item_name=item_name,
            journal_source="Item Evidence",
            transaction_no=bill_item_refs,
            gr_reference=stj_link_text,
            akun_name=f"External Clearing: {verified_clearing_amount:,.2f} | {eligibility_label}",
            note_detail="Evidence item-level yang diringkas di panel kanan",
        )
        _add_detail_row(
            scope_level="item",
            row_kind="external_clearing",
            item_code=item_code,
            item_name=item_name,
            journal_source="External Clearing",
            transaction_no=f"{verified_clearing_amount:,.2f}" if verified_clearing_amount >= 0.01 else "-",
            gr_reference=external_clearing_refs,
            akun="1108099" if verified_clearing_amount >= 0.01 else "",
            akun_name=f"{'Verified' if external_clearing_verified else 'Not verified'} | Basis: {external_clearing_basis}",
            note_detail="Trace external clearing / audit evidence",
        )

        for audit_row in list(getattr(item_row, "adjustment_audit_rows", None) or []):
            move_label = normalize_text(getattr(audit_row, "move_name", "")) or normalize_text(getattr(audit_row, "move_ref", ""))
            _add_detail_row(
                scope_level="item",
                row_kind="item_adjustment_audit",
                item_code=item_code,
                item_name=item_name,
                date=normalize_text(getattr(audit_row, "move_date", ""))[:10],
                journal_source="Adj Audit",
                transaction_no=move_label,
                gr_reference=normalize_text(getattr(audit_row, "move_ref", "")),
                akun=normalize_text(getattr(audit_row, "clearing_account_code", "")),
                akun_name=self._pcb_adjustment_summary_text(audit_row),
                matching=self._pcb_adjustment_match_text(audit_row),
                note_detail="Adjustment audit row item-level",
            )

        for account_row in item_accounts:
            status = normalize_text(getattr(account_row, "status", ""))
            _add_detail_row(
                scope_level="item",
                row_kind="item_account",
                item_code=item_code,
                item_name=item_name,
                journal_source=normalize_text(getattr(account_row, "account_type", "")),
                akun=normalize_text(getattr(account_row, "code", "")),
                akun_name=normalize_text(getattr(account_row, "name", ""))[:60],
                debit=round(float(getattr(account_row, "debit", 0.0) or 0.0), 2),
                credit=round(float(getattr(account_row, "credit", 0.0) or 0.0), 2),
                balance=round(float(getattr(account_row, "net_balance", 0.0) or 0.0), 2),
                matching=(icon_map.get(status, "") + " " + status).strip(),
                note_detail="Saldo akun level item",
            )

    hidden_rows: list[dict[str, Any]] = []
    hidden_row_no = 1

    def _add_hidden_row(
        *,
        scope_level: str,
        field_name: str,
        value: Any,
        item_code: str = "",
        item_name: str = "",
        note: str = "",
    ) -> None:
        nonlocal hidden_row_no
        value_text = self._pcb_export_value_text(value)
        if not value_text:
            return
        hidden_rows.append(
            {
                "row_no": hidden_row_no,
                "scope_level": scope_level,
                "cycle_name": cycle_name,
                "item_code": item_code,
                "item_name": item_name,
                "field_name": field_name,
                "value_text": value_text,
                "source_ui": "Detail Journal Entry per Cycle Pembelian",
                "note": note or "Metadata ditarik analyzer tetapi tidak jadi kolom di grid kiri Detail JE.",
            }
        )
        hidden_row_no += 1

    cycle_hidden_fields = [
        ("inventory_types", list(getattr(cycle, "inventory_types", None) or [])),
        ("document_classification", normalize_text(getattr(cycle, "document_classification", ""))),
        ("document_classification_label", normalize_text(getattr(cycle, "document_classification_label", ""))),
        ("document_classification_reasons", list(getattr(cycle, "document_classification_reasons", None) or [])),
        ("picking_ids", list(getattr(cycle, "picking_ids", None) or [])),
        ("picking_names", list(getattr(cycle, "picking_names", None) or [])),
        ("stj_refs", list(getattr(cycle, "stj_refs", None) or [])),
        ("bill_refs", list(getattr(cycle, "bill_refs", None) or [])),
        ("payment_refs", list(getattr(cycle, "payment_refs", None) or [])),
        ("bank_refs", list(getattr(cycle, "bank_refs", None) or [])),
        ("primary_case", normalize_text(getattr(cycle, "primary_case", ""))),
        ("case_counts", getattr(cycle, "case_counts", None)),
        ("edge_flags", list(getattr(cycle, "edge_flags", None) or [])),
        ("mixed_case_summary", normalize_text(getattr(cycle, "mixed_case_summary", ""))),
        ("issue_patterns", list(getattr(cycle, "issue_patterns", None) or [])),
        ("has_return_picking", bool(getattr(cycle, "has_return_picking", False))),
        ("has_refund_bill", bool(getattr(cycle, "has_refund_bill", False))),
        ("adjustment_warning_text", adjustment_warning_text),
    ]
    for field_name, value in cycle_hidden_fields:
        _add_hidden_row(scope_level="cycle", field_name=field_name, value=value)

    for item_row in list(getattr(cycle, "item_rows", None) or []):
        item_code = normalize_text(getattr(item_row, "default_code", ""))
        item_name = normalize_text(getattr(item_row, "product_name", "")) or f"Product #{int(getattr(item_row, 'product_id', 0) or 0)}"
        item_hidden_fields = [
            ("product_id", int(getattr(item_row, "product_id", 0) or 0)),
            ("bill_move_ids", list(getattr(item_row, "bill_move_ids", None) or [])),
            ("bill_refs", list(getattr(item_row, "bill_refs", None) or [])),
            ("purchase_line_ids", list(getattr(item_row, "purchase_line_ids", None) or [])),
            ("stock_move_ids", list(getattr(item_row, "stock_move_ids", None) or [])),
            ("stj_move_ids", list(getattr(item_row, "stj_move_ids", None) or [])),
            ("stj_refs", list(getattr(item_row, "stj_refs", None) or [])),
            ("correction_stj_move_ids", list(getattr(item_row, "correction_stj_move_ids", None) or [])),
            ("correction_stj_refs", list(getattr(item_row, "correction_stj_refs", None) or [])),
            ("has_item_bill", bool(getattr(item_row, "has_item_bill", False))),
            ("has_item_stj", bool(getattr(item_row, "has_item_stj", False))),
            ("has_stj_evidence", bool(getattr(item_row, "has_stj_evidence", False))),
            ("gr_quantity", float(getattr(item_row, "gr_quantity", 0.0) or 0.0)),
            ("bill_quantity", float(getattr(item_row, "bill_quantity", 0.0) or 0.0)),
            ("standard_price", float(getattr(item_row, "standard_price", 0.0) or 0.0)),
            ("primary_case", normalize_text(getattr(item_row, "primary_case", ""))),
            ("secondary_flags", list(getattr(item_row, "secondary_flags", None) or [])),
            ("case_reason", normalize_text(getattr(item_row, "case_reason", ""))),
            ("auto_repairable", bool(getattr(item_row, "auto_repairable", False))),
            ("stj_state", normalize_text(getattr(item_row, "stj_state", ""))),
            ("svl_zero_at_gr", bool(getattr(item_row, "svl_zero_at_gr", False))),
            ("bill_hit_role", normalize_text(getattr(item_row, "bill_hit_role", ""))),
            ("repair_basis_amount", float(getattr(item_row, "repair_basis_amount", 0.0) or 0.0)),
            ("repair_basis_source", normalize_text(getattr(item_row, "repair_basis_source", ""))),
            ("external_clearing_refs", list(getattr(item_row, "external_clearing_refs", None) or [])),
            ("external_clearing_basis", normalize_text(getattr(item_row, "external_clearing_basis", ""))),
            ("external_clearing_verified", bool(getattr(item_row, "external_clearing_verified", False))),
            ("case8_evidence", getattr(item_row, "case8_evidence", None)),
            ("case9_evidence", getattr(item_row, "case9_evidence", None)),
        ]
        for field_name, value in item_hidden_fields:
            _add_hidden_row(
                scope_level="item",
                item_code=item_code,
                item_name=item_name,
                field_name=field_name,
                value=value,
            )

    return {
        "database": database,
        "company_id": company_id,
        "company_name": company_name,
        "cycle_name": cycle_name,
        "cycle_status": cycle_status,
        "partner_name": cycle_partner,
        "gr_date": gr_date,
        "po_label": po_label,
        "sort_mode": sort_mode,
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "raw_row_count": len(raw_rows),
        "detail_row_count": len(detail_rows),
        "hidden_row_count": len(hidden_rows),
        "raw_rows": raw_rows,
        "detail_rows": detail_rows,
        "hidden_rows": hidden_rows,
    }


def _export_pcb_cycle_detail_excel(self, cycle: Any | None = None) -> None:
    target_cycle = cycle or getattr(self, "_pcb_raw_current_cycle", None) or self._resolve_pcb_sidebar_cycle()
    if target_cycle is None:
        messagebox.showwarning(
            self._display_name,
            "Pilih cycle terlebih dahulu sebelum download Excel Detail Journal Entry per Cycle Pembelian.",
        )
        return
    payload = self._build_pcb_cycle_detail_export_payload(target_cycle)
    initial_dir = default_output_browse_dir(self.context.global_settings, getattr(self._module_settings, "last_output_dir", ""))
    filename = self._pcb_cycle_detail_filename(target_cycle)
    selected = filedialog.asksaveasfilename(
        title="Download Excel Detail Journal Entry per Cycle Pembelian",
        defaultextension=".xlsx",
        initialdir=initial_dir or None,
        initialfile=filename,
        filetypes=[("Excel Workbook", "*.xlsx")],
    )
    if not selected:
        return
    path = export_pcb_cycle_detail_excel(payload=payload, output_path=selected)
    self._remember_output_dir(path)
    self.status_var.set(f"Export Detail Journal Entry per Cycle selesai: {path}")


def _pcb_item_has_stj_evidence(item_row: Any) -> bool:
    return bool(
        getattr(item_row, "has_stj_evidence", False)
        or getattr(item_row, "has_item_stj", False)
        or any(int(move_id or 0) > 0 for move_id in list(getattr(item_row, "stj_move_ids", None) or []))
        or any(normalize_text(ref) for ref in list(getattr(item_row, "stj_refs", None) or []))
        or any(int(move_id or 0) > 0 for move_id in list(getattr(item_row, "correction_stj_move_ids", None) or []))
        or any(normalize_text(ref) for ref in list(getattr(item_row, "correction_stj_refs", None) or []))
    )

def _pcb_item_stj_link_text(item_row: Any) -> str:
    direct_refs = [
        normalize_text(value)
        for value in list(getattr(item_row, "stj_refs", None) or [])
        if normalize_text(value)
    ]
    if direct_refs:
        return ", ".join(direct_refs)
    correction_refs = [
        f"{normalize_text(value)} (cycle correction)"
        for value in list(getattr(item_row, "correction_stj_refs", None) or [])
        if normalize_text(value)
    ]
    if correction_refs:
        return ", ".join(correction_refs)
    return "No direct STJ"

def _classify_pcb_cycle_case(cycle: Any) -> str:
    primary_case = normalize_text(getattr(cycle, "primary_case", "")).lower()
    if primary_case in SvlFixJeDashboardPage._PCB_PROBLEM_CASE_LABEL:
        return primary_case
    case_counts = dict(getattr(cycle, "case_counts", {}) or {})
    for case_key in SvlFixJeDashboardPage._PCB_PROBLEM_CASE_ORDER:
        if int(case_counts.get(case_key, 0) or 0) > 0:
            return case_key
    edge_flags = [
        normalize_text(value).lower()
        for value in list(getattr(cycle, "edge_flags", None) or [])
        if normalize_text(value)
    ]
    for case_key in SvlFixJeDashboardPage._PCB_PROBLEM_CASE_ORDER:
        if case_key in edge_flags:
            return case_key
    item_rows = list(getattr(cycle, "item_rows", None) or [])
    ranked_item_cases = sorted(
        {
            normalize_text(getattr(item_row, "primary_case", "")).lower()
            for item_row in item_rows
            if normalize_text(getattr(item_row, "primary_case", "")).lower()
            in SvlFixJeDashboardPage._PCB_PROBLEM_CASE_LABEL
        },
        key=lambda case_key: (
            SvlFixJeDashboardPage._PCB_PROBLEM_CASE_ORDER.index(case_key)
            if case_key in SvlFixJeDashboardPage._PCB_PROBLEM_CASE_ORDER
            else 99,
            case_key,
        ),
    )
    if ranked_item_cases:
        return ranked_item_cases[0]
    case1_rows = list(getattr(cycle, "case1_link_rows", None) or [])
    if case1_rows:
        return "case1"
    repair_rows = list(getattr(cycle, "case2_repair_rows", None) or [])
    ranked_row_cases = sorted(
        {
            normalize_text(getattr(row, "pcb_case", "")).lower()
            for row in repair_rows
            if not bool(getattr(row, "review_required", False))
            if normalize_text(getattr(row, "pcb_case", "")).lower()
            in SvlFixJeDashboardPage._PCB_PROBLEM_CASE_LABEL
        },
        key=lambda case_key: (
            SvlFixJeDashboardPage._PCB_PROBLEM_CASE_ORDER.index(case_key)
            if case_key in SvlFixJeDashboardPage._PCB_PROBLEM_CASE_ORDER
            else 99,
            case_key,
        ),
    )
    if ranked_row_cases:
        return ranked_row_cases[0]
    account_rows = list(getattr(cycle, "account_rows", None) or [])
    by_code = {
        normalize_text(getattr(row, "code", "")).upper(): row
        for row in account_rows
        if normalize_text(getattr(row, "code", ""))
    }
    a2103006 = by_code.get("2103006")
    a1108099 = by_code.get("1108099")
    prob_2103006 = a2103006 is not None and normalize_text(getattr(a2103006, "status", "")).lower() == "problem"
    prob_1108099 = a1108099 is not None and normalize_text(getattr(a1108099, "status", "")).lower() == "problem"
    has_bill = bool(list(getattr(cycle, "bill_refs", None) or []))
    has_eligible_case34_items = any(bool(getattr(item_row, "eligible_case34", False)) for item_row in item_rows)
    if prob_2103006 and prob_1108099:
        net_2103006 = abs(float(getattr(a2103006, "net_balance", 0.0) or 0.0))
        net_1108099 = abs(float(getattr(a1108099, "net_balance", 0.0) or 0.0))
        if abs(net_2103006 - net_1108099) < 1.0:
            return "case1"
    raw_lines = list(getattr(cycle, "raw_lines", None) or [])
    account_status_by_code = {
        normalize_text(getattr(row, "code", "")).upper(): normalize_text(getattr(row, "status", "")).lower()
        for row in account_rows
        if normalize_text(getattr(row, "code", ""))
    }
    for line in raw_lines:
        if normalize_text(line.get("jenis")).upper() != "BILL":
            continue
        if normalize_text(line.get("tipe_akun")).lower() != "expense_direct_cost":
            continue
        account_code = normalize_text(line.get("akun_code")).upper()
        if account_code in {"5101010"}:
            continue
        if account_status_by_code.get(account_code, "acceptable") != "info":
            return "case2"
    if prob_2103006 and prob_1108099 and has_bill and (not item_rows or has_eligible_case34_items):
        return "case3"
    if prob_2103006 and not prob_1108099 and has_bill and (not item_rows or has_eligible_case34_items):
        return "case4"
    return "case_lainnya" if normalize_text(getattr(cycle, "cycle_status", "")).lower() == "problem" else ""

def _pcb_partial_group_key(cls, cycle: Any) -> str:
    key = normalize_text(getattr(cycle, "partial_group_key", "")).lower()
    if key in cls._PCB_PARTIAL_GROUP_LABEL:
        return key
    return "partial_other"

def _pcb_partial_group_label(cls, cycle: Any) -> str:
    stored = normalize_text(getattr(cycle, "partial_group_label", ""))
    if stored:
        return stored
    return cls._PCB_PARTIAL_GROUP_LABEL.get(cls._pcb_partial_group_key(cycle), "Partial Lainnya")


def _render_purchase_cycle_sidebar(
    self,
    *,
    on_complete: "Callable[[], None] | None" = None,
    filtered_cycles: "list[Any] | None" = None,
) -> None:
    """Populate sidebar tree with purchase cycles for the purchase_cycle_balance mode.

    ``filtered_cycles`` is used when a search query is active; the full snapshot
    cycle list is used for the summary counters regardless.
    """
    bench_started = perf_counter()
    self._sidebar_item_frames.clear()
    snapshot = self._latest_snapshot
    all_cycles = list(getattr(snapshot, "purchase_cycles", None) or []) if snapshot else []
    cycles = all_cycles if filtered_cycles is None else filtered_cycles
    self._pcb_visible_cycles = list(cycles)

    def _emit_sidebar_bench() -> None:
        elapsed_ms = (perf_counter() - bench_started) * 1000.0
        suffix = "" if filtered_cycles is None else "[filtered]"
        message = f"[BENCH] sidebar_render{suffix}: {elapsed_ms:.0f}ms | {len(cycles)} cycles"
        logger = getattr(self, "logger", None)
        if logger is not None:
            logger.debug(message)
        log_queue = getattr(self, "log_queue", None)
        if filtered_cycles is None and log_queue is not None:
            log_queue.put(message)

    total_problem = sum(1 for c in all_cycles if c.cycle_status == "problem")
    total_partial = sum(1 for c in all_cycles if c.cycle_status == "partial")
    total_healthy = sum(1 for c in all_cycles if c.cycle_status == "healthy")
    shown_suffix = f"  (tampil {len(cycles)})" if filtered_cycles is not None and len(cycles) != len(all_cycles) else ""
    self.sidebar_summary_var.set(
        f"{len(all_cycles)} cycle | ❌{total_problem} ⚠️{total_partial} ✅{total_healthy}{shown_suffix}"
    )
    self._clear_sidebar_tree()
    if not cycles:
        self._set_empty_detail()
        _emit_sidebar_bench()
        if on_complete is not None:
            on_complete()
        return
    tree = self.sidebar_tree
    # Group cycles by status
    status_groups: dict[str, list[Any]] = {"problem": [], "partial": [], "healthy": []}
    for cycle in cycles:
        status_groups.setdefault(cycle.cycle_status, []).append(cycle)
    self._pcb_cycle_by_iid: dict[str, Any] = {}
    self._pcb_item_by_iid: dict[str, Any] = {}
    self._pcb_cycle_node_ids = set()
    self._pcb_item_node_ids = set()

    _acct_icon = {"problem": "🔴", "info": "🔵", "acceptable": "🟡", "balanced": "🟢"}

    def _item_worst_status(item_row: Any) -> str:
        priority = {"problem": 3, "info": 2, "acceptable": 1, "balanced": 0}
        best = "balanced"
        for ar in (item_row.account_rows or []):
            if priority.get(ar.status, 0) > priority.get(best, 0):
                best = ar.status
        return best

    _PROBLEM_CASE_ORDER = self._PCB_PROBLEM_CASE_ORDER
    _PROBLEM_CASE_LABEL = self._PCB_PROBLEM_CASE_LABEL
    _classify_problem_case = self._classify_pcb_cycle_case

    def _insert_cycle_node(parent_iid: str, cycle: Any, icon: str) -> None:
        prob_count = cycle.problem_account_count
        info_count = getattr(cycle, "info_account_count", 0)
        suffix_parts = []
        if prob_count > 0:
            suffix_parts.append(f"{prob_count} ❌")
        if info_count > 0:
            suffix_parts.append(f"{info_count} 🔵")
        suffix = f"  [{', '.join(suffix_parts)}]" if suffix_parts else ""
        po_short = (", ".join(cycle.purchase_orders) if cycle.purchase_orders else "")[:32]
        cycle_iid = tree.insert(
            parent_iid, "end",
            text=f"{icon} {cycle.picking_name}{suffix}\n   {po_short}",
            open=False,
        )
        self._pcb_cycle_by_iid[cycle_iid] = cycle
        self._pcb_cycle_node_ids.add(cycle_iid)

        item_rows = list(cycle.item_rows or [])
        if not item_rows:
            # No item breakdown: show account rows directly
            for acct_row in (cycle.account_rows or []):
                ai = _acct_icon.get(acct_row.status, "⚪")
                child_iid = tree.insert(cycle_iid, "end",
                    text=f"  {ai} {acct_row.code}  {acct_row.net_balance:+,.0f}")
                self._pcb_cycle_by_iid[child_iid] = cycle
            return

        # Group items by valuation method
        automated = [ir for ir in item_rows if getattr(ir, "valuation_method", "automated") != "manual"]
        manual    = [ir for ir in item_rows if getattr(ir, "valuation_method", "automated") == "manual"]

        for grp_label, grp_items, grp_open in (
            ("🔄 Automated", automated, True),
            ("📋 Manual",    manual,    True),
        ):
            if not grp_items:
                continue
            grp_iid = tree.insert(cycle_iid, "end",
                text=f"  {grp_label} ({len(grp_items)})", open=grp_open)
            # Clicking the group header → show cycle detail
            self._pcb_cycle_by_iid[grp_iid] = cycle

            for item_row in grp_items:
                worst = _item_worst_status(item_row)
                item_icon = _acct_icon.get(worst, "⚪")
                code = getattr(item_row, "default_code", "") or ""
                code_part = f"[{code}] " if code else ""
                name_part = (item_row.product_name or f"#{item_row.product_id}")[:28]
                item_iid = tree.insert(
                    grp_iid, "end",
                    text=f"    {item_icon} {code_part}{name_part}",
                    open=False,
                )
                self._pcb_cycle_by_iid[item_iid] = cycle
                self._pcb_item_by_iid[item_iid] = item_row
                self._pcb_item_node_ids.add(item_iid)

    for status in ("problem", "partial", "healthy"):
        group_cycles = status_groups.get(status, [])
        if not group_cycles:
            continue
        icon = self._PCB_STATUS_ICON.get(status, "")
        label = self._PCB_STATUS_LABEL.get(status, status)
        group_iid = tree.insert("", "end", text=f"{icon} {label} ({len(group_cycles)})", open=(status == "problem"))

        if status == "problem":
            # Sub-group bermasalah cycles by case
            case_buckets: dict[str, list[Any]] = {k: [] for k in _PROBLEM_CASE_ORDER}
            for cycle in group_cycles:
                case_buckets[_classify_problem_case(cycle)].append(cycle)
            for case_key in _PROBLEM_CASE_ORDER:
                case_cycles = case_buckets[case_key]
                if not case_cycles:
                    continue
                case_label = _PROBLEM_CASE_LABEL[case_key]
                case_iid = tree.insert(group_iid, "end",
                    text=f"  {case_label} ({len(case_cycles)})", open=True)
                for cycle in case_cycles:
                    _insert_cycle_node(case_iid, cycle, icon)
        elif status == "partial":
            partial_buckets: dict[str, list[Any]] = {k: [] for k in self._PCB_PARTIAL_GROUP_ORDER}
            for cycle in group_cycles:
                partial_buckets.setdefault(self._pcb_partial_group_key(cycle), []).append(cycle)
            for partial_key in self._PCB_PARTIAL_GROUP_ORDER:
                partial_cycles = partial_buckets.get(partial_key, [])
                if not partial_cycles:
                    continue
                partial_label = self._PCB_PARTIAL_GROUP_LABEL.get(partial_key, "Partial Lainnya")
                partial_iid = tree.insert(
                    group_iid,
                    "end",
                    text=f"  {partial_label} ({len(partial_cycles)})",
                    open=True,
                )
                for cycle in partial_cycles:
                    _insert_cycle_node(partial_iid, cycle, icon)
        else:
            for cycle in group_cycles:
                _insert_cycle_node(group_iid, cycle, icon)

    _emit_sidebar_bench()
    if on_complete is not None:
        on_complete()


def _on_pcb_cycle_selected(self, event: Any = None) -> None:
    """Show cycle or item detail when a row is selected in purchase_cycle_balance mode."""
    if not self._is_purchase_cycle_mode():
        return
    tree = self.sidebar_tree
    sel = tree.selection()
    if not sel:
        return
    iid = sel[0]
    cycle = getattr(self, "_pcb_cycle_by_iid", {}).get(iid)
    if cycle is None:
        return
    item_row = getattr(self, "_pcb_item_by_iid", {}).get(iid)
    if item_row is not None:
        self._render_purchase_cycle_item_detail(cycle, item_row)
    else:
        self._render_purchase_cycle_detail(cycle)

# ── PCB Repair Collection ─────────────────────────────────────────────────


def _pcb_collection_scope_text(self) -> str:
    scope = getattr(self, "_pcb_repair_collection_scope", None)
    if scope is None:
        return ""
    parts = [normalize_text(scope.database_label), normalize_text(scope.company_label)]
    return " / ".join(part for part in parts if part)


def _pcb_visible_case1_cycles(self) -> list[Any]:
    cycles = list(getattr(self, "_pcb_visible_cycles", None) or [])
    if not cycles:
        snapshot = getattr(self, "_latest_snapshot", None)
        cycles = list(getattr(snapshot, "purchase_cycles", None) or []) if snapshot else []
    return [
        cycle
        for cycle in cycles
        if bool(getattr(cycle, "case1_link_rows", None))
    ]


def _pcb_visible_actionable_cycle_buckets(self) -> dict[str, list[Any]]:
    cycles = list(getattr(self, "_pcb_visible_cycles", None) or [])
    if not cycles:
        snapshot = getattr(self, "_latest_snapshot", None)
        cycles = list(getattr(snapshot, "purchase_cycles", None) or []) if snapshot else []
    buckets: dict[str, list[Any]] = {case_key: [] for case_key in self._PCB_PROBLEM_CASE_ORDER}
    for cycle in cycles:
        if normalize_text(getattr(cycle, "cycle_status", "")).lower() != "problem":
            continue
        if not self._build_pcb_seeds_for_cycle(cycle):
            continue
        case_key = self._classify_pcb_cycle_case(cycle)
        buckets.setdefault(case_key, []).append(cycle)
    return buckets


def _pcb_visible_actionable_cycles(self, *, case_filter: str | None = None) -> list[Any]:
    buckets = self._pcb_visible_actionable_cycle_buckets()
    normalized_case = normalize_text(case_filter).lower()
    if normalized_case:
        return list(buckets.get(normalized_case, []))
    cycles: list[Any] = []
    for case_key in self._PCB_PROBLEM_CASE_ORDER:
        cycles.extend(buckets.get(case_key, []))
    return cycles


def _pcb_cycle_non_actionable_reason(self, cycle: Any) -> str:
    pcb_case = self._classify_pcb_cycle_case(cycle)
    if pcb_case == "edge_partial_bill":
        return "Cycle ini masuk edge partial bill. Tunggu bill lengkap atau review manual item partial sebelum execute repair."
    if pcb_case == "edge_return_no_credit_memo":
        return "Cycle ini memiliki return picking tanpa credit memo/refund bill. Auto repair diblokir sampai dokumen retur lengkap."
    if pcb_case == "edge_stj_corrupt":
        return "Cycle ini terindikasi STJ corrupt/header tanpa line valid. Perlu review manual sebelum repair."
    item_rows = list(getattr(cycle, "item_rows", None) or [])
    account_rows = list(getattr(cycle, "account_rows", None) or [])
    by_code = {
        normalize_text(getattr(row, "code", "")).upper(): row
        for row in account_rows
        if normalize_text(getattr(row, "code", ""))
    }
    prob_2103006 = normalize_text(getattr(by_code.get("2103006"), "status", "")).lower() == "problem"
    prob_1108099 = normalize_text(getattr(by_code.get("1108099"), "status", "")).lower() == "problem"
    has_bill = bool(list(getattr(cycle, "bill_refs", None) or []))
    has_case34_pattern = has_bill and prob_2103006 and (prob_1108099 or pcb_case == "case4")
    actionable_item_notes: list[str] = []
    for item_row in item_rows:
        item_label = " | ".join(
            part
            for part in (
                normalize_text(getattr(item_row, "default_code", "")),
                normalize_text(getattr(item_row, "product_name", "")),
            )
            if part
        ) or f"Product #{int(getattr(item_row, 'product_id', 0) or 0)}"
        has_item_bill = bool(getattr(item_row, "has_item_bill", False))
        has_item_stj = self._pcb_item_has_stj_evidence(item_row)
        external_clearing_amount = self._pcb_external_clearing_amount(item_row)
        external_clearing_verified = bool(getattr(item_row, "external_clearing_verified", False)) and external_clearing_amount >= 0.01
        actionable_item_notes.append(
            f"{item_label}: Bill {'Yes' if has_item_bill else 'No'}, "
            f"STJ {'Yes' if has_item_stj else 'No'}, "
            f"External Clearing {external_clearing_amount:,.2f} "
            f"({'verified' if external_clearing_verified else 'not verified'})"
        )
    if pcb_case in {"case2", "case3", "case4", "case5", "case6", "case8a", "case8b", "case9"}:
        if not item_rows:
            return f"Cycle ini belum punya breakdown item, jadi {self._pcb_case_label(case_value=pcb_case)} belum bisa membentuk row repair per item."
        if list(getattr(cycle, "case2_repair_rows", None) or []):
            return "Cycle ini sebenarnya sudah actionable; coba refresh menu atau pilih ulang row cycle."
        if pcb_case in {"case8a", "case8b", "case9"}:
            return (
                f"Cycle ini masuk {self._pcb_case_label(case_value=pcb_case)}, tetapi evidence belum cukup untuk membentuk planned line. "
                "Pastikan trace stock.move/SVL/UoM/return atau downstream clearing sudah tersedia, lalu review ulang item."
            )
        has_case2_bill_line = any(
            normalize_text(line.get("jenis")).upper() == "BILL"
            and normalize_text(line.get("tipe_akun")).lower() == "expense_direct_cost"
            and normalize_text(line.get("akun_code")).upper() not in self._PCB_COGS_VARIANCE_CODES
            for line in list(getattr(cycle, "raw_lines", None) or [])
        )
        if pcb_case == "case2" and not has_case2_bill_line:
            return "Tidak ada line BILL expense_direct_cost non-variance yang bisa dipakai sebagai candidate Case 2."
        if pcb_case in {"case5", "case6"}:
            return (
                f"Cycle ini masuk group {self._pcb_case_label(case_value=pcb_case)}, tetapi basis item belum cukup kuat untuk membentuk row repair otomatis. "
                "Pastikan nilai SVL receipt aktual tersedia, tidak ada jurnal pemulihan existing, dan qty bill/receipt sesuai."
            )
        return (
            f"Cycle ini masuk group {self._pcb_case_label(case_value=pcb_case)} dari pola cycle-level, tetapi belum ada item yang actionable. "
            "Row multi-line hanya bisa di-add bila ada item candidate yang pada Detail Saldo Akun per Item/Cycle "
            "masih punya saldo akun problem 1108099 / 2103006 atau external clearing item yang terverifikasi. "
            + " | ".join(actionable_item_notes[:3])
        )
    if pcb_case == "case1" and not list(getattr(cycle, "case1_link_rows", None) or []):
        return "Cycle ini terdeteksi Case 1, tetapi belum ada source row item/source yang bisa dipakai membentuk repair seed."
    if has_case34_pattern and item_rows:
        suffix = ""
        if len(actionable_item_notes) > 3:
            suffix = f" | (+{len(actionable_item_notes) - 3} item lain)"
        return (
            "Cycle ini belum punya row PCB repair yang actionable untuk Case 3/4. "
            "Item wajib punya bill lalu direct STJ atau external clearing yang terverifikasi. "
            + " | ".join(actionable_item_notes[:3])
            + suffix
        )
    return "Cycle ini belum punya row PCB repair yang actionable."


def _resolve_pcb_sidebar_cycle(
    self,
    row_iid: str = "",
    *,
    fallback_item_ids: list[str] | tuple[str, ...] | None = None,
) -> Any | None:
    cycle_by_iid = getattr(self, "_pcb_cycle_by_iid", {}) or {}
    candidate_iids: list[str] = []
    clean_row_iid = normalize_text(row_iid)
    if clean_row_iid:
        candidate_iids.append(clean_row_iid)
    for fallback_iid in list(fallback_item_ids or []):
        clean_fallback_iid = normalize_text(fallback_iid)
        if clean_fallback_iid and clean_fallback_iid not in candidate_iids:
            candidate_iids.append(clean_fallback_iid)
    tree = getattr(self, "sidebar_tree", None)
    if tree is not None:
        for selected_iid in list(tree.selection() or ()):
            clean_selected_iid = normalize_text(selected_iid)
            if clean_selected_iid and clean_selected_iid not in candidate_iids:
                candidate_iids.append(clean_selected_iid)
        focus_iid = normalize_text(tree.focus())
        if focus_iid and focus_iid not in candidate_iids:
            candidate_iids.append(focus_iid)
    for candidate_iid in candidate_iids:
        cycle = cycle_by_iid.get(candidate_iid)
        if cycle is not None:
            return cycle
    return None

def _pcb_case1_source_label(source_kind: str, source_id: int) -> str:
    label_map = {
        "bill_line": "Bill Line",
        "purchase_line": "PO Line",
        "stock_move": "Stock Move",
    }
    label = label_map.get(normalize_text(source_kind), normalize_text(source_kind) or "Source")
    return f"{label} #{int(source_id or 0)}" if int(source_id or 0) > 0 else label


def _build_pcb_case1_repair_seed(self, cycle: Any, link_row: Any) -> "dict[str, Any] | None":
    amount = round(abs(float(getattr(link_row, "allocated_amount", 0.0) or 0.0)), 2)
    if amount <= 0.0:
        return None
    item_code = normalize_text(getattr(link_row, "item_code", ""))
    item_name = normalize_text(getattr(link_row, "item_name", ""))
    bill_name = normalize_text(getattr(link_row, "bill_name", ""))
    po_name = normalize_text(getattr(link_row, "po_name", ""))
    picking_name = normalize_text(getattr(link_row, "picking_name", "")) or normalize_text(getattr(cycle, "picking_name", ""))
    pcb_case_label = self._pcb_case_label("case1")
    generated_text_row = {
        "pcb_case": "case1",
        "pcb_case_label": pcb_case_label,
        "picking_name": picking_name,
        "bill_name": bill_name,
        "item_code": item_code,
        "item_name": item_name,
    }
    reference, line_label = self._pcb_case1_generated_texts_for_row(generated_text_row)
    suspend_target_ids = list(getattr(link_row, "suspend_target_aml_ids", []) or [])
    clearing_target_ids = list(getattr(link_row, "clearing_target_aml_ids", []) or [])
    reconcile_target_count = len(suspend_target_ids) + len(clearing_target_ids)
    reconcile_ready = False
    reconcile_readiness_label = "Disabled"
    return {
        "row_key": normalize_text(getattr(link_row, "source_key", "")),
        "cycle_key": normalize_text(getattr(link_row, "cycle_key", "")),
        "pcb_case": "case1",
        "company_id": int(self._selected_company_id() or 0),
        "company_name": self._selected_company_name(),
        "amount": amount,
        "date": self._pcb_case1_default_date(),
        "reference": reference,
        "line_label": line_label,
        "reference_generated": True,
        "line_label_generated": True,
        "pcb_case_label": pcb_case_label,
        "journal_code": normalize_text(getattr(link_row, "journal_code", "")),
        "debit_account_code": normalize_text(getattr(link_row, "debit_account_code", "")),
        "credit_account_code": normalize_text(getattr(link_row, "credit_account_code", "")),
        "debit_amount": float(getattr(link_row, "debit_amount", 0.0) or 0.0),
        "credit_amount": float(getattr(link_row, "credit_amount", 0.0) or 0.0),
        "diff_account_code": normalize_text(getattr(link_row, "diff_account_code", "")),
        "diff_account_name": normalize_text(getattr(link_row, "diff_account_name", "")),
        "diff_side": normalize_text(getattr(link_row, "diff_side", "")),
        "diff_amount": float(getattr(link_row, "diff_amount", 0.0) or 0.0),
        "source_kind": normalize_text(getattr(link_row, "source_kind", "")),
        "source_id": int(getattr(link_row, "source_id", 0) or 0),
        "source_label": self._pcb_case1_source_label(getattr(link_row, "source_kind", ""), getattr(link_row, "source_id", 0)),
        "product_id": int(getattr(link_row, "product_id", 0) or 0),
        "item_code": item_code,
        "item_name": item_name,
        "item_category_name": normalize_text(getattr(link_row, "item_category_name", "")),
        "bill_line_id": int(getattr(link_row, "bill_line_id", 0) or 0),
        "bill_move_id": int(getattr(link_row, "bill_move_id", 0) or 0),
        "purchase_line_id": int(getattr(link_row, "purchase_line_id", 0) or 0),
        "stock_move_id": int(getattr(link_row, "stock_move_id", 0) or 0),
        "stock_move_ids": list(getattr(link_row, "stock_move_ids", []) or []),
        "stj_move_ids": list(getattr(link_row, "stj_move_ids", []) or []),
        "stj_refs": list(getattr(link_row, "stj_refs", []) or []),
        "stj_link_basis": normalize_text(getattr(link_row, "stj_link_basis", "")),
        "stj_candidate_count": int(getattr(link_row, "stj_candidate_count", 0) or 0),
        "picking_id": int(getattr(link_row, "picking_id", 0) or 0),
        "picking_name": picking_name,
        "po_name": po_name,
        "bill_name": bill_name,
        "partner_id": int(getattr(link_row, "partner_id", 0) or 0),
        "partner_name": normalize_text(getattr(link_row, "partner_name", "")) or normalize_text(getattr(cycle, "partner_name", "")),
        "payment_move_ids": list(getattr(link_row, "payment_move_ids", []) or []),
        "bank_move_ids": list(getattr(link_row, "bank_move_ids", []) or []),
        "suspend_target_aml_ids": list(getattr(link_row, "suspend_target_aml_ids", []) or []),
        "clearing_target_aml_ids": list(getattr(link_row, "clearing_target_aml_ids", []) or []),
        "product_uom_id": int(getattr(link_row, "product_uom_id", 0) or 0),
        "quantity": float(getattr(link_row, "quantity", 0.0) or 0.0),
        "currency_id": int(getattr(link_row, "currency_id", 0) or 0),
        "amount_currency": float(getattr(link_row, "amount_currency", 0.0) or 0.0),
        "amount_currency_basis": float(getattr(link_row, "amount_currency_basis", 0.0) or 0.0),
        "analytic_distribution": getattr(link_row, "analytic_distribution", False),
        "bill_price_unit": float(getattr(link_row, "bill_price_unit", 0.0) or 0.0),
        "gr_price_unit": float(getattr(link_row, "gr_price_unit", 0.0) or 0.0),
        "price_gap_value": float(getattr(link_row, "price_gap_value", 0.0) or 0.0),
        "allocated_amount": float(getattr(link_row, "allocated_amount", 0.0) or 0.0),
        "reconcile_ready": reconcile_ready,
        "reconcile_readiness_label": reconcile_readiness_label,
        "reconcile_target_count": reconcile_target_count,
        "result_status": "",
        "result_posted": False,
        "result_error_kind": "",
        "result_move_id": 0,
        "result_move_name": "",
        "existing_move_detected": False,
        "reconcile_attempted": False,
        "reconcile_performed": False,
        "reconcile_skipped": False,
        "reconcile_message": "",
        "reconcile_error_kind": "",
        "row_status": "ready",
        "row_status_message": "Ready",
        "latest_snapshot_status": normalize_text(getattr(cycle, "cycle_status", "")),
    }


def _build_pcb_case1_seeds_for_cycle(self, cycle: Any) -> list[dict[str, Any]]:
    seeds: list[dict[str, Any]] = []
    for link_row in list(getattr(cycle, "case1_link_rows", None) or []):
        seed = self._build_pcb_case1_repair_seed(cycle, link_row)
        if seed is not None:
            seeds.append(seed)
    return seeds

def _pcb_case2_guard_trigger_kind(seed: dict[str, Any]) -> str:
    if normalize_text(seed.get("pcb_case")).lower() not in {"case2", "case3", "case4", "case5", "case6", "case8a", "case8b", "case9"}:
        return ""
    guard_flags = {
        normalize_text(value)
        for value in list(seed.get("guard_flags") or [])
        if normalize_text(value)
    }
    if any(flag.startswith("review_required_case8") or flag.startswith("review_required_case9") for flag in guard_flags):
        return "case89_review_required"
    coefficient_variance = abs(float(seed.get("coefficient_variance") or 0.0))
    if "missing_repair_basis" in guard_flags or "review_required_basis_manual" in guard_flags:
        return "missing_repair_basis"
    if "coefficient_variance_auto_warning" in guard_flags:
        return "coefficient_variance_auto_warning"
    if coefficient_variance > 35.0:
        return "coefficient_variance_high"
    return ""

def _pcb_case2_guard_required(seed: dict[str, Any]) -> bool:
    return bool(SvlFixJeDashboardPage._pcb_case2_guard_trigger_kind(seed))

def _pcb_case2_guard_reason_preview(seed: dict[str, Any]) -> str:
    trigger_kind = SvlFixJeDashboardPage._pcb_case2_guard_trigger_kind(seed)
    guard_messages = [normalize_text(value) for value in list(seed.get("guard_messages") or []) if normalize_text(value)]
    if trigger_kind == "missing_repair_basis":
        matched_message = next(
            (
                message
                for message in guard_messages
                if "basis" in normalize_text(message).lower() or "standard cost" in normalize_text(message).lower()
            ),
            "",
        )
        if matched_message:
            return matched_message
        return "Basis Standard Cost x Qty item belum valid, perlu dicek manual."
    if trigger_kind == "coefficient_variance_auto_warning":
        matched_message = next(
            (
                message
                for message in guard_messages
                if "selisih hpp" in normalize_text(message).lower() or "denominator" in normalize_text(message).lower()
            ),
            "",
        )
        if matched_message:
            return matched_message
        return "Selisih HPP tidak nol dengan denominator 0.00, perlu dicek manual."
    coefficient_variance = float(seed.get("coefficient_variance") or 0.0)
    if trigger_kind == "coefficient_variance_high":
        return f"Coefficient Variance {coefficient_variance:,.2f}% melebihi 35.00%."
    if trigger_kind == "case89_review_required":
        return next((message for message in guard_messages if message), "Case 8/9 wajib direview manual sebelum execute.")
    if guard_messages:
        return guard_messages[0]
    if coefficient_variance > 35.0:
        return f"Coefficient Variance {coefficient_variance:,.2f}% melebihi 35.00%."
    return "Perlu dicek sebelum dimasukkan ke collection."


def _map_pcb_case2_planned_lines(
    self,
    planned_lines: list[Any],
    *,
    default_line_label: str,
) -> list[dict[str, Any]]:
    mapped_lines: list[dict[str, Any]] = []
    for planned_line in list(planned_lines or []):
        if isinstance(planned_line, dict):
            line_mapping = dict(planned_line)
        else:
            line_mapping = {
                field_name: getattr(planned_line, field_name)
                for field_name in getattr(planned_line, "__dataclass_fields__", {})
            }
        amount = round(abs(float(line_mapping.get("amount") or 0.0)), 2)
        side = normalize_text(line_mapping.get("side")).lower()
        if amount <= 0.0 or side not in {"debit", "credit"}:
            continue
        line_mapping["amount"] = amount
        line_mapping["side"] = side
        line_mapping["account_code"] = normalize_text(line_mapping.get("account_code")).upper()
        line_mapping["account_name"] = normalize_text(line_mapping.get("account_name"))
        line_mapping["role"] = normalize_text(line_mapping.get("role"))
        line_mapping["line_label"] = normalize_pcb_planned_line_label(
            role=line_mapping.get("role"),
            line_label=line_mapping.get("line_label"),
            default_line_label=default_line_label,
        )
        line_mapping["source_balance"] = round(float(line_mapping.get("source_balance") or 0.0), 2)
        mapped_lines.append(line_mapping)
    return mapped_lines


def _build_pcb_case2_repair_seed(self, cycle: Any, repair_row: Any) -> "dict[str, Any] | None":
    item_code = normalize_text(getattr(repair_row, "item_code", ""))
    item_name = normalize_text(getattr(repair_row, "item_name", ""))
    picking_name = normalize_text(getattr(repair_row, "picking_name", "")) or normalize_text(getattr(cycle, "picking_name", ""))
    bill_name = normalize_text(getattr(repair_row, "bill_name", ""))
    pcb_case = normalize_text(getattr(repair_row, "pcb_case", "")).lower() or self._classify_pcb_cycle_case(cycle)
    if pcb_case not in {"case2", "case3", "case4", "case5", "case6", "case8a", "case8b", "case9"}:
        pcb_case = "case2"
    pcb_case_label = self._pcb_case_label(pcb_case)
    generated_text_row = {
        "pcb_case": pcb_case,
        "pcb_case_label": pcb_case_label,
        "picking_name": picking_name,
        "bill_name": bill_name,
        "item_code": item_code,
        "item_name": item_name,
    }
    reference, line_label = self._pcb_case1_generated_texts_for_row(generated_text_row)
    planned_lines = self._map_pcb_case2_planned_lines(
        list(getattr(repair_row, "planned_lines", []) or []),
        default_line_label=line_label,
    )
    account_name_by_code: dict[str, str] = {}
    for account_row in list(getattr(cycle, "account_rows", []) or []):
        account_code = normalize_text(getattr(account_row, "code", "")).upper()
        if not account_code:
            continue
        account_name = normalize_text(getattr(account_row, "name", ""))
        if account_name:
            account_name_by_code[account_code] = account_name
    for planned_line in planned_lines:
        account_code = normalize_text(planned_line.get("account_code")).upper()
        account_name = normalize_text(planned_line.get("account_name"))
        if account_code and account_name and not account_name_by_code.get(account_code):
            account_name_by_code[account_code] = account_name
    external_clearing_amount = self._pcb_external_clearing_amount(repair_row)
    external_clearing_refs = [
        normalize_text(value)
        for value in list(getattr(repair_row, "external_clearing_refs", []) or [])
        if normalize_text(value)
    ]
    external_clearing_basis = normalize_text(getattr(repair_row, "external_clearing_basis", ""))
    external_clearing_verified = bool(getattr(repair_row, "external_clearing_verified", False)) and external_clearing_amount >= 0.01
    source_label = "Item Balance"
    if external_clearing_amount >= 0.01:
        source_label = f"{source_label} | ExtClr {external_clearing_amount:,.2f}"
    amount = round(
        abs(float(getattr(repair_row, "amount", 0.0) or 0.0))
        or max((float(line.get("amount") or 0.0) for line in planned_lines), default=0.0),
        2,
    )
    guard_flags = [normalize_text(value) for value in list(getattr(repair_row, "guard_flags", []) or []) if normalize_text(value)]
    guard_messages = [normalize_text(value) for value in list(getattr(repair_row, "guard_messages", []) or []) if normalize_text(value)]
    seed = {
        "row_key": normalize_text(getattr(repair_row, "row_key", "")),
        "cycle_key": normalize_text(getattr(repair_row, "cycle_key", "")) or f"{pcb_case}::{int(getattr(cycle, 'picking_id', 0) or 0)}",
        "pcb_case": pcb_case,
        "company_id": int(self._selected_company_id() or 0),
        "company_name": self._selected_company_name(),
        "amount": amount,
        "date": self._pcb_case1_default_date(),
        "reference": reference,
        "line_label": line_label,
        "reference_generated": True,
        "line_label_generated": True,
        "pcb_case_label": pcb_case_label,
        "journal_code": normalize_text(getattr(repair_row, "journal_code", "")) or DEFAULT_JOURNAL_CODE,
        "product_id": int(getattr(repair_row, "product_id", 0) or 0),
        "item_code": item_code,
        "item_name": item_name,
        "item_category_name": normalize_text(getattr(repair_row, "item_category_name", "")),
        "bill_line_id": int(getattr(repair_row, "bill_line_id", 0) or 0),
        "purchase_line_id": int(getattr(repair_row, "purchase_line_id", 0) or 0),
        "stock_move_id": int(getattr(repair_row, "stock_move_id", 0) or 0),
        "stock_move_ids": [
            int(value or 0)
            for value in list(getattr(repair_row, "stock_move_ids", []) or [])
            if int(value or 0) > 0
        ],
        "stj_move_ids": [
            int(value or 0)
            for value in list(getattr(repair_row, "stj_move_ids", []) or [])
            if int(value or 0) > 0
        ],
        "stj_refs": [
            normalize_text(value)
            for value in list(getattr(repair_row, "stj_refs", []) or [])
            if normalize_text(value)
        ],
        "payment_move_ids": [
            int(value or 0)
            for value in list(getattr(repair_row, "payment_move_ids", []) or [])
            if int(value or 0) > 0
        ],
        "bank_move_ids": [
            int(value or 0)
            for value in list(getattr(repair_row, "bank_move_ids", []) or [])
            if int(value or 0) > 0
        ],
        "suspend_target_aml_ids": [
            int(value or 0)
            for value in list(getattr(repair_row, "suspend_target_aml_ids", []) or [])
            if int(value or 0) > 0
        ],
        "clearing_target_aml_ids": [
            int(value or 0)
            for value in list(getattr(repair_row, "clearing_target_aml_ids", []) or [])
            if int(value or 0) > 0
        ],
        "source_kind": "item_balance",
        "source_label": source_label,
        "picking_id": int(getattr(repair_row, "picking_id", 0) or 0),
        "picking_name": picking_name,
        "po_name": normalize_text(getattr(repair_row, "po_name", "")),
        "bill_move_id": int(getattr(repair_row, "bill_move_id", 0) or 0),
        "bill_name": bill_name,
        "partner_id": int(getattr(repair_row, "partner_id", 0) or 0),
        "partner_name": normalize_text(getattr(repair_row, "partner_name", "")) or normalize_text(getattr(cycle, "partner_name", "")),
        "product_uom_id": int(getattr(repair_row, "product_uom_id", 0) or 0),
        "quantity": float(getattr(repair_row, "quantity", 0.0) or 0.0),
        "currency_id": int(getattr(repair_row, "currency_id", 0) or 0),
        "amount_currency": float(getattr(repair_row, "amount_currency", 0.0) or 0.0),
        "amount_currency_basis": float(getattr(repair_row, "amount_currency_basis", 0.0) or 0.0),
        "analytic_distribution": getattr(repair_row, "analytic_distribution", False),
        "bill_price_unit": float(getattr(repair_row, "bill_price_unit", 0.0) or 0.0),
        "gr_price_unit": float(getattr(repair_row, "gr_price_unit", 0.0) or 0.0),
        "price_gap_value": float(getattr(repair_row, "price_gap_value", 0.0) or 0.0),
        "allocated_amount": float(getattr(repair_row, "allocated_amount", 0.0) or 0.0),
        "suspend_account_code": normalize_text(getattr(repair_row, "suspend_account_code", "")).upper() or "2103006",
        "inventory_account_code": normalize_text(getattr(repair_row, "inventory_account_code", "")).upper(),
        "expense_account_code": normalize_text(getattr(repair_row, "expense_account_code", "")).upper(),
        "suggested_expense_account_code": normalize_text(getattr(repair_row, "expense_account_code", "")).upper(),
        "resolve_account_code": normalize_text(getattr(repair_row, "expense_account_code", "")).upper(),
        "resolve_account_preview": "Belum dipilih",
        "resolve_account_manual": False,
        "repair_basis_amount": round(float(getattr(repair_row, "repair_basis_amount", 0.0) or 0.0), 2),
        "repair_basis_source": normalize_text(getattr(repair_row, "repair_basis_source", "")),
        "standard_price": round(float(getattr(repair_row, "standard_price", 0.0) or 0.0), 2),
        "account_name_by_code": account_name_by_code,
        "account_candidates": [],
        "problem_balances_by_code": {
            normalize_text(key).upper(): round(float(value or 0.0), 2)
            for key, value in dict(getattr(repair_row, "problem_balances_by_code", {}) or {}).items()
            if normalize_text(key)
        },
        "hpp_balances_by_code": {
            normalize_text(key).upper(): round(float(value or 0.0), 2)
            for key, value in dict(getattr(repair_row, "hpp_balances_by_code", {}) or {}).items()
            if normalize_text(key)
        },
        "suspend_balance": round(float(getattr(repair_row, "suspend_balance", 0.0) or 0.0), 2),
        "hpp_balance": round(float(getattr(repair_row, "hpp_balance", 0.0) or 0.0), 2),
        "inventory_balance": round(float(getattr(repair_row, "inventory_balance", 0.0) or 0.0), 2),
        "cogs_variance_balance": round(float(getattr(repair_row, "cogs_variance_balance", 0.0) or 0.0), 2),
        "selisih_hpp_amount": round(float(getattr(repair_row, "selisih_hpp_amount", 0.0) or 0.0), 2),
        "external_clearing_amount": external_clearing_amount,
        "external_clearing_refs": external_clearing_refs,
        "external_clearing_basis": external_clearing_basis,
        "external_clearing_verified": external_clearing_verified,
        "coefficient_variance": round(float(getattr(repair_row, "coefficient_variance", 0.0) or 0.0), 2),
        "bank_balances_by_code": {
            normalize_text(key).upper(): round(float(value or 0.0), 2)
            for key, value in dict(getattr(repair_row, "bank_balances_by_code", {}) or {}).items()
            if normalize_text(key)
        },
        "bank_account_codes": [
            normalize_text(value).upper()
            for value in list(getattr(repair_row, "bank_account_codes", []) or [])
            if normalize_text(value)
        ],
        "case_evidence": dict(getattr(repair_row, "case_evidence", {}) or {}),
        "guard_flags": guard_flags,
        "guard_messages": guard_messages,
        "review_required_base": bool(getattr(repair_row, "review_required", False)),
        "review_reason_base": normalize_text(getattr(repair_row, "review_reason", "")),
        "review_required": bool(getattr(repair_row, "review_required", False)),
        "review_confirmed": bool(getattr(repair_row, "review_confirmed", False)),
        "review_reason": normalize_text(getattr(repair_row, "review_reason", "")),
        "review_required_projected": False,
        "projected_review_reason": "",
        "projected_problem_codes": [],
        "base_planned_lines": [dict(line) for line in planned_lines],
        "planned_lines_manual": False,
        "planned_lines": planned_lines,
        "result_status": "",
        "result_posted": False,
        "result_error_kind": "",
        "result_move_id": int(getattr(repair_row, "result_move_id", 0) or 0),
        "result_move_name": normalize_text(getattr(repair_row, "result_move_name", "")),
        "existing_move_detected": False,
        "reconcile_attempted": False,
        "reconcile_performed": False,
        "reconcile_skipped": False,
        "reconcile_message": "",
        "reconcile_error_kind": "",
        "row_status": "",
        "row_status_message": "",
        "latest_snapshot_status": normalize_text(getattr(cycle, "cycle_status", "")),
    }
    item_row = self._pcb_detail_item_row_for_row(seed, cycle)
    self._sync_pcb_case2_row_defaults(seed, cycle=cycle, item_row=item_row)
    return seed


def _build_pcb_case2_seeds_for_cycle(self, cycle: Any) -> list[dict[str, Any]]:
    repair_rows = list(getattr(cycle, "case2_repair_rows", None) or [])
    if not repair_rows:
        return []
    seeds: list[dict[str, Any]] = []
    for repair_row in repair_rows:
        seed = self._build_pcb_case2_repair_seed(cycle, repair_row)
        if seed is not None:
            seeds.append(seed)
    return seeds


def _build_pcb_case_lainnya_manual_seeds_for_cycle(self, cycle: Any) -> list[dict[str, Any]]:
    pcb_case = self._classify_pcb_cycle_case(cycle)
    if pcb_case == "case1":
        return []

    cycle_account_rows = list(getattr(cycle, "account_rows", None) or [])
    cycle_raw_lines = list(getattr(cycle, "raw_lines", None) or [])
    cycle_item_rows = list(getattr(cycle, "item_rows", None) or [])
    if not cycle_raw_lines:
        return []

    account_name_by_code: dict[str, str] = {}
    raw_candidates: list[SvlDashboardRepairAccountCandidate] = []
    for account_row in cycle_account_rows:
        account_code = normalize_text(getattr(account_row, "code", "")).upper()
        account_name = normalize_text(getattr(account_row, "name", ""))
        account_type = normalize_text(getattr(account_row, "account_type", "")).lower()
        if account_code and account_name and not account_name_by_code.get(account_code):
            account_name_by_code[account_code] = account_name
        if account_code:
            raw_candidates.append(
                SvlDashboardRepairAccountCandidate(
                    code=account_code,
                    name=account_name or account_code,
                    source="PCB Cycle Account",
                    role="expense" if account_type == "expense_direct_cost" else "other",
                )
            )
    for raw_line in cycle_raw_lines:
        account_code = normalize_text(raw_line.get("akun_code")).upper()
        account_name = normalize_text(raw_line.get("akun_name"))
        account_type = normalize_text(raw_line.get("tipe_akun")).lower()
        if account_code and account_name and not account_name_by_code.get(account_code):
            account_name_by_code[account_code] = account_name
        if account_code:
            raw_candidates.append(
                SvlDashboardRepairAccountCandidate(
                    code=account_code,
                    name=account_name or account_code,
                    source=f"PCB Raw Line - {normalize_text(raw_line.get('jenis')).upper() or '?'}",
                    role="expense" if account_type == "expense_direct_cost" else "other",
                )
            )
    cycle_account_candidates = self._dedupe_repair_account_candidates(raw_candidates)

    problem_balances_cycle = {
        normalize_text(getattr(account_row, "code", "")).upper(): round(float(getattr(account_row, "net_balance", 0.0) or 0.0), 2)
        for account_row in cycle_account_rows
        if normalize_text(getattr(account_row, "status", "")).lower() == "problem"
        and normalize_text(getattr(account_row, "code", ""))
    }
    hpp_balances_cycle = {
        normalize_text(getattr(account_row, "code", "")).upper(): round(float(getattr(account_row, "net_balance", 0.0) or 0.0), 2)
        for account_row in cycle_account_rows
        if normalize_text(getattr(account_row, "account_type", "")).lower() == "expense_direct_cost"
        and normalize_text(getattr(account_row, "code", ""))
        and abs(float(getattr(account_row, "net_balance", 0.0) or 0.0)) >= 0.01
    }

    seeds: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str, str, str]] = set()
    for raw_line in cycle_raw_lines:
        if normalize_text(raw_line.get("jenis")).upper() != "BILL":
            continue
        if normalize_text(raw_line.get("tipe_akun")).lower() != "expense_direct_cost":
            continue
        expense_account_code = normalize_text(raw_line.get("akun_code")).upper()
        if not expense_account_code or expense_account_code in self._PCB_COGS_VARIANCE_CODES:
            continue
        item_code = normalize_text(raw_line.get("kode_item")).upper()
        item_name = normalize_text(raw_line.get("nama_item"))
        bill_name = normalize_text(raw_line.get("kode_transaksi"))
        manual_key = (expense_account_code, item_code, item_name, bill_name)
        if manual_key in seen_keys:
            continue
        seen_keys.add(manual_key)

        item_row = None
        if cycle_item_rows:
            item_row = self._pcb_detail_item_row_for_row(
                {
                    "product_id": 0,
                    "item_code": item_code,
                    "item_name": item_name,
                },
                cycle,
            )
        amount_basis = self._pcb_detail_amount(raw_line.get("saldo"))
        if abs(amount_basis) < 0.01:
            amount_basis = self._pcb_detail_amount(
                float(raw_line.get("debit") or 0.0) - float(raw_line.get("kredit") or 0.0)
            )
        amount = abs(amount_basis)
        if amount <= 0.0:
            continue

        problem_balances = {
            normalize_text(getattr(account_row, "code", "")).upper(): round(float(getattr(account_row, "net_balance", 0.0) or 0.0), 2)
            for account_row in list(getattr(item_row, "account_rows", None) or [])
            if normalize_text(getattr(account_row, "status", "")).lower() == "problem"
            and normalize_text(getattr(account_row, "code", ""))
        }
        if not problem_balances:
            problem_balances = dict(problem_balances_cycle)

        hpp_balances = {
            normalize_text(getattr(account_row, "code", "")).upper(): round(float(getattr(account_row, "net_balance", 0.0) or 0.0), 2)
            for account_row in list(getattr(item_row, "account_rows", None) or [])
            if normalize_text(getattr(account_row, "account_type", "")).lower() == "expense_direct_cost"
            and normalize_text(getattr(account_row, "code", ""))
            and abs(float(getattr(account_row, "net_balance", 0.0) or 0.0)) >= 0.01
        }
        if not hpp_balances:
            hpp_balances = dict(hpp_balances_cycle)
        if expense_account_code and expense_account_code not in hpp_balances:
            hpp_balances[expense_account_code] = round(amount_basis, 2)

        source_account_name_by_code = dict(account_name_by_code)
        for account_row in list(getattr(item_row, "account_rows", None) or []):
            account_code = normalize_text(getattr(account_row, "code", "")).upper()
            account_name = normalize_text(getattr(account_row, "name", ""))
            if account_code and account_name and not source_account_name_by_code.get(account_code):
                source_account_name_by_code[account_code] = account_name
        if expense_account_code and not source_account_name_by_code.get(expense_account_code):
            source_account_name_by_code[expense_account_code] = normalize_text(raw_line.get("akun_name")) or expense_account_code

        item_candidates = list(cycle_account_candidates)
        for account_row in list(getattr(item_row, "account_rows", None) or []):
            account_code = normalize_text(getattr(account_row, "code", "")).upper()
            if not account_code:
                continue
            item_candidates.append(
                SvlDashboardRepairAccountCandidate(
                    code=account_code,
                    name=normalize_text(getattr(account_row, "name", "")) or account_code,
                    source="PCB Item Account",
                    role=(
                        "expense"
                        if normalize_text(getattr(account_row, "account_type", "")).lower() == "expense_direct_cost"
                        else "other"
                    ),
                )
            )
        item_candidates = self._dedupe_repair_account_candidates(item_candidates)

        manual_case_label = f"{self._pcb_case_label(case_value=pcb_case)} - Manual Reclass"
        generated_text_row = {
            "pcb_case": pcb_case,
            "pcb_case_label": manual_case_label,
            "picking_name": normalize_text(getattr(cycle, "picking_name", "")),
            "bill_name": bill_name,
            "item_code": item_code,
            "item_name": item_name,
        }
        reference, line_label = self._pcb_case1_generated_texts_for_row(generated_text_row)
        expense_account_name = source_account_name_by_code.get(expense_account_code, expense_account_code)
        review_reason = (
            f"Manual reklas diperlukan untuk {expense_account_code} - {expense_account_name}. "
            "Pilih sendiri debit/kredit dan line jurnal pada simulasi, lalu confirm review sebelum execute."
        )
        seed = {
            "row_key": (
                f"{pcb_case}::{int(getattr(cycle, 'picking_id', 0) or 0)}::"
                f"{expense_account_code}::{item_code or item_name or bill_name or len(seeds) + 1}"
            ),
            "cycle_key": f"{pcb_case}::{int(getattr(cycle, 'picking_id', 0) or 0)}",
            "pcb_case": pcb_case,
            "company_id": int(self._selected_company_id() or 0),
            "company_name": self._selected_company_name(),
            "amount": amount,
            "date": self._pcb_case1_default_date(),
            "reference": reference,
            "line_label": line_label,
            "reference_generated": True,
            "line_label_generated": True,
            "pcb_case_label": manual_case_label,
            "journal_code": DEFAULT_JOURNAL_CODE,
            "product_id": int(getattr(item_row, "product_id", 0) or 0),
            "item_code": item_code,
            "item_name": item_name,
            "item_category_name": normalize_text(raw_line.get("kategori_produk")),
            "bill_line_id": 0,
            "purchase_line_id": 0,
            "stock_move_id": 0,
            "stock_move_ids": list(getattr(item_row, "stock_move_ids", []) or []),
            "stj_move_ids": list(getattr(item_row, "stj_move_ids", []) or []),
            "stj_refs": list(getattr(item_row, "stj_refs", []) or []),
            "payment_move_ids": list(getattr(cycle, "payment_move_ids", []) or []),
            "bank_move_ids": list(getattr(cycle, "bank_move_ids", []) or []),
            "suspend_target_aml_ids": [],
            "clearing_target_aml_ids": [],
            "source_kind": "manual_reclass_review",
            "source_label": "Manual Reclass Review",
            "picking_id": int(getattr(cycle, "picking_id", 0) or 0),
            "picking_name": normalize_text(getattr(cycle, "picking_name", "")),
            "po_name": normalize_text(raw_line.get("no_po")),
            "bill_move_id": 0,
            "bill_name": bill_name,
            "partner_id": int(getattr(cycle, "partner_id", 0) or 0),
            "partner_name": normalize_text(getattr(cycle, "partner_name", "")),
            "product_uom_id": 0,
            "quantity": float(raw_line.get("qty_item") or 0.0),
            "currency_id": 0,
            "amount_currency": 0.0,
            "amount_currency_basis": 0.0,
            "analytic_distribution": False,
            "bill_price_unit": 0.0,
            "gr_price_unit": 0.0,
            "price_gap_value": 0.0,
            "allocated_amount": round(amount, 2),
            "suspend_account_code": "2103006",
            "inventory_account_code": "",
            "expense_account_code": expense_account_code,
            "suggested_expense_account_code": expense_account_code,
            "resolve_account_code": expense_account_code,
            "resolve_account_preview": "Belum dipilih",
            "resolve_account_manual": False,
            "account_name_by_code": source_account_name_by_code,
            "account_candidates": item_candidates,
            "problem_balances_by_code": problem_balances,
            "hpp_balances_by_code": hpp_balances,
            "suspend_balance": round(float(problem_balances.get("2103006", 0.0) or 0.0), 2),
            "hpp_balance": round(float(hpp_balances.get(expense_account_code, amount_basis) or 0.0), 2),
            "inventory_balance": 0.0,
            "cogs_variance_balance": 0.0,
            "selisih_hpp_amount": 0.0,
            "external_clearing_amount": 0.0,
            "external_clearing_refs": [],
            "external_clearing_basis": "",
            "external_clearing_verified": False,
            "coefficient_variance": 0.0,
            "bank_balances_by_code": {},
            "bank_account_codes": [],
            "guard_flags": [],
            "guard_messages": [],
            "review_required_base": True,
            "review_reason_base": review_reason,
            "review_required": True,
            "review_confirmed": False,
            "review_reason": review_reason,
            "review_required_projected": False,
            "projected_review_reason": "",
            "projected_problem_codes": [],
            "base_planned_lines": [],
            "planned_lines_manual": False,
            "planned_lines": [],
            "result_status": "",
            "result_posted": False,
            "result_error_kind": "",
            "result_move_id": 0,
            "result_move_name": "",
            "existing_move_detected": False,
            "reconcile_attempted": False,
            "reconcile_performed": False,
            "reconcile_skipped": False,
            "reconcile_message": "",
            "reconcile_error_kind": "",
            "row_status": "",
            "row_status_message": "",
            "latest_snapshot_status": normalize_text(getattr(cycle, "cycle_status", "")),
        }
        self._sync_pcb_case2_row_defaults(seed, cycle=cycle, item_row=item_row)
        seeds.append(seed)
    return seeds


def _build_pcb_seeds_for_cycle(self, cycle: Any) -> list[dict[str, Any]]:
    pcb_case = self._classify_pcb_cycle_case(cycle)
    seeds = [
        *self._build_pcb_case1_seeds_for_cycle(cycle),
        *self._build_pcb_case2_seeds_for_cycle(cycle),
    ]
    if seeds:
        return seeds
    if pcb_case == "case_lainnya":
        return self._build_pcb_case_lainnya_manual_seeds_for_cycle(cycle)
    return []


def _update_pcb_collection_button(self) -> None:
    btn = getattr(self, "_pcb_collection_btn", None)
    n = len(getattr(self, "_pcb_repair_collection", {}))
    if btn is not None:
        btn.config(text=f"PCB Repair Collection ({n})")
    self._notify_repair_collection_changed()
    return

    btn = getattr(self, "_pcb_collection_btn", None)
    n = len(getattr(self, "_pcb_repair_collection", {}))
    if btn is not None:
        btn.config(text=f"📋 ({n})")
    self._notify_repair_collection_changed()


def get_pcb_collection_ui_state(self) -> "dict[str, Any]":
    """Return PCB collection state for module-level controls."""
    col = getattr(self, "_pcb_repair_collection", {})
    scope = getattr(self, "_pcb_repair_collection_scope", None)
    current = self._current_repair_collection_scope()
    is_pcb = self._is_purchase_cycle_mode()
    can_open = bool(col) and is_pcb and (scope is None or current is None or scope.matches(current))
    visible_actionable_buckets = self._pcb_visible_actionable_cycle_buckets() if is_pcb else {
        case_key: []
        for case_key in self._PCB_PROBLEM_CASE_ORDER
    }
    return {
        "has_rows": bool(col),
        "count": len(col),
        "is_pcb_mode": is_pcb,
        "can_open": can_open,
        "has_visible_case1_cycles": bool(self._pcb_visible_case1_cycles()),
        "has_visible_actionable_cycles": any(bool(rows) for rows in visible_actionable_buckets.values()),
        "visible_actionable_cases": {
            case_key: bool(rows)
            for case_key, rows in visible_actionable_buckets.items()
        },
    }

    col = getattr(self, "_pcb_repair_collection", {})
    scope = getattr(self, "_pcb_repair_collection_scope", None)
    current = self._current_repair_collection_scope()
    is_pcb = self._is_purchase_cycle_mode()
    can_open = bool(col) and is_pcb and (scope is None or current is None or scope.matches(current))
    return {
        "has_rows": bool(col),
        "count": len(col),
        "is_pcb_mode": is_pcb,
        "can_open": can_open,
    }


def open_pcb_repair_collection_dialog(self) -> None:
    """Public entry point for module-level button."""
    self._open_pcb_repair_dialog()


def _is_pcb_cycle_in_collection(self, cycle: Any) -> bool:
    cycle_keys = {normalize_text(seed.get("cycle_key")) for seed in self._build_pcb_seeds_for_cycle(cycle) if normalize_text(seed.get("cycle_key"))}
    if not cycle_keys:
        cycle_keys = {f"{self._classify_pcb_cycle_case(cycle)}::{int(getattr(cycle, 'picking_id', 0) or 0)}"}
    return any(normalize_text(row.get("cycle_key")) in cycle_keys for row in getattr(self, "_pcb_repair_collection", {}).values())

    return f"pcb_{cycle.picking_id}" in getattr(self, "_pcb_repair_collection", {})


def _build_pcb_cycle_repair_seed(self, cycle: Any) -> "dict[str, Any] | None":
    """Build a repair seed dict from a PCB cycle. Returns None if not actionable."""
    by_code = {r.code: r for r in (cycle.account_rows or [])}
    a2103006 = by_code.get("2103006")
    a1108099 = by_code.get("1108099")

    # Determine debit/credit direction from remaining balance
    if a2103006 and a2103006.status == "problem" and abs(a2103006.net_balance) > 0.01:
        debit_code, credit_code = ("1108099", "2103006") if a2103006.net_balance > 0 else ("2103006", "1108099")
        amount = abs(a2103006.net_balance)
    elif a1108099 and a1108099.status == "problem" and abs(a1108099.net_balance) > 0.01:
        debit_code, credit_code = ("2103006", "1108099") if a1108099.net_balance > 0 else ("1108099", "2103006")
        amount = abs(a1108099.net_balance)
    else:
        return None

    bill_label = ", ".join(cycle.bill_refs[:2]) if cycle.bill_refs else ""
    ref = f"Koreksi: {cycle.picking_name}"
    if bill_label:
        ref += f" | {bill_label}"

    return {
        "row_key": f"pcb_{cycle.picking_id}",
        "pcb_case": self._classify_pcb_cycle_case(cycle),
        "company_id": int(self._selected_company_id() or 0),
        "company_name": self._selected_company_name(),
        "picking_id": cycle.picking_id,
        "picking_name": cycle.picking_name,
        "gr_date": (cycle.gr_date or "")[:10],
        "partner_name": cycle.partner_name or "",
        "purchase_orders": list(cycle.purchase_orders or []),
        "bill_refs": list(cycle.bill_refs or []),
        "stj_refs": list(cycle.stj_refs or []),
        "problem_accounts": [
            {"code": r.code, "net_balance": r.net_balance,
             "account_id": r.account_id, "status": r.status}
            for r in (cycle.account_rows or []) if r.status == "problem"
        ],
        "amount": amount,
        "debit_account_code": debit_code,
        "credit_account_code": credit_code,
        "date": self._pcb_case1_default_date(),
        "reference": ref,
        "line_label": f"{cycle.picking_name} | {debit_code}↔{credit_code}",
        "row_status": "incomplete",
        "row_status_message": "Belum dikonfirmasi",
        "latest_snapshot_status": cycle.cycle_status,
    }


def _confirm_pcb_case2_guard_rows(
    self,
    seeds: list[dict[str, Any]],
    *,
    source_label: str,
) -> bool:
    flagged_rows = [seed for seed in seeds if self._pcb_case2_guard_required(seed)]
    if not flagged_rows:
        return True
    preview_lines = [
        f"- {normalize_text(seed.get('item_code')) or normalize_text(seed.get('item_name')) or normalize_text(seed.get('picking_name'))}: {self._pcb_case2_guard_reason_preview(seed)}"
        for seed in flagged_rows[:3]
    ]
    extra_count = len(flagged_rows) - len(preview_lines)
    if extra_count > 0:
        preview_lines.append(f"- ... dan {extra_count} row lain ter-flag.")
    message = (
        f"{len(flagged_rows)} row PCB multi-line dari {source_label} ter-flag guard.\n\n"
        "Alasan contoh:\n"
        f"{chr(10).join(preview_lines)}\n\n"
        "Tetap lanjutkan masuk ke PCB Repair Collection?"
    )
    return bool(messagebox.askyesno(self._display_name, message))


def _add_pcb_cycle_to_collection(self, cycle: Any, *, skip_case2_guard_confirm: bool = False) -> bool:
    """Add one cycle to the PCB repair collection. Returns True if added/updated."""
    seeds = self._build_pcb_seeds_for_cycle(cycle)
    if not seeds:
        reason = self._pcb_cycle_non_actionable_reason(cycle)
        self.status_var.set(reason)
        messagebox.showinfo(self._display_name, reason)
        return False
    current_scope = self._current_repair_collection_scope()
    if current_scope is None:
        messagebox.showwarning(self._display_name, "Pilih company terlebih dahulu sebelum menggunakan PCB Repair Collection.")
        return False
    col = self._pcb_repair_collection
    scope = self._pcb_repair_collection_scope
    if col and scope is not None and not scope.matches(current_scope):
        messagebox.showwarning(
            self._display_name,
            f"PCB Repair Collection terkunci di scope lain: {self._pcb_collection_scope_text() or '-'}.\n"
            "Kembali ke scope asal atau clear collection terlebih dahulu.",
        )
        return False
    if not col or scope is None:
        self._pcb_repair_collection_scope = current_scope
    if (
        not skip_case2_guard_confirm
        and not self._confirm_pcb_case2_guard_rows(
            seeds,
            source_label=normalize_text(getattr(cycle, "picking_name", "")) or "cycle terpilih",
        )
    ):
        return False
    added = 0
    updated = 0
    for seed in reversed(seeds):
        self._pcb_repair_collection_sequence += 1
        row_key = normalize_text(seed.get("row_key"))
        existed = row_key in col
        merged_seed = dict(seed)
        if existed:
            existing_row = dict(col[row_key])
            self._sync_pcb_case1_generated_flags(existing_row)
            if normalize_text(existing_row.get("date")):
                merged_seed["date"] = normalize_text(existing_row.get("date"))
            if not bool(existing_row.get("reference_generated")) and normalize_text(existing_row.get("reference")):
                merged_seed["reference"] = normalize_text(existing_row.get("reference"))
                merged_seed["reference_generated"] = False
            if not bool(existing_row.get("line_label_generated")) and normalize_text(existing_row.get("line_label")):
                merged_seed["line_label"] = normalize_text(existing_row.get("line_label"))
                merged_seed["line_label_generated"] = False
            if bool(existing_row.get("review_confirmed")):
                merged_seed["review_confirmed"] = True
            if bool(existing_row.get("resolve_account_manual")) and normalize_text(existing_row.get("resolve_account_code")):
                merged_seed["resolve_account_manual"] = True
                merged_seed["resolve_account_code"] = normalize_text(existing_row.get("resolve_account_code")).upper()
            if bool(existing_row.get("planned_lines_manual")):
                merged_seed["planned_lines_manual"] = True
                merged_seed["planned_lines"] = self._map_pcb_case2_planned_lines(
                    list(existing_row.get("planned_lines") or []),
                    default_line_label=normalize_text(existing_row.get("line_label")) or normalize_text(merged_seed.get("line_label")),
                )
        self._sync_pcb_case1_generated_flags(merged_seed)
        if self._pcb_row_uses_planned_lines(merged_seed):
            self._sync_pcb_case2_row_defaults(merged_seed)
        col[row_key] = merged_seed
        col.move_to_end(row_key, last=False)
        if existed:
            updated += 1
        else:
            added += 1
    self._update_pcb_collection_button()
    case_breakdown: dict[str, int] = {}
    for seed in seeds:
        pcb_case = normalize_text(seed.get("pcb_case")) or "case1"
        case_breakdown[pcb_case] = int(case_breakdown.get(pcb_case, 0)) + 1
    case_summary = ", ".join(
        f"{count} {self._pcb_case_label(case_value=pcb_case)}"
        for pcb_case, count in sorted(case_breakdown.items())
    )
    self.status_var.set(
        f"PCB Repair Collection: {added} row baru, {updated} row diperbarui dari {normalize_text(getattr(cycle, 'picking_name', 'cycle'))}."
        f"{f' ({case_summary})' if case_summary else ''}"
    )
    return bool(added or updated)

    seed = self._build_pcb_cycle_repair_seed(cycle)
    if not seed:
        self.status_var.set("Cycle ini tidak memiliki akun problem yang bisa di-repair.")
        return False
    current_scope = self._current_repair_collection_scope()
    if current_scope is None:
        messagebox.showwarning(self._display_name, "Pilih company terlebih dahulu sebelum menggunakan PCB Repair Collection.")
        return False
    col = self._pcb_repair_collection
    scope = self._pcb_repair_collection_scope
    if col and scope is not None and not scope.matches(current_scope):
        messagebox.showwarning(
            self._display_name,
            f"PCB Collection terkunci di scope lain: {self._repair_collection_scope_text() or '-'}.\n"
            "Kembali ke scope asal atau clear collection terlebih dahulu.",
        )
        return False
    if not col or scope is None:
        self._pcb_repair_collection_scope = current_scope
    self._pcb_repair_collection_sequence += 1
    row_key = seed["row_key"]
    col[row_key] = dict(seed)
    col.move_to_end(row_key, last=False)
    self._update_pcb_collection_button()
    return True


def _remove_pcb_cycle_from_collection(self, cycle: Any) -> bool:
    cycle_keys = {normalize_text(seed.get("cycle_key")) for seed in self._build_pcb_seeds_for_cycle(cycle) if normalize_text(seed.get("cycle_key"))}
    if not cycle_keys:
        cycle_keys = {f"{self._classify_pcb_cycle_case(cycle)}::{int(getattr(cycle, 'picking_id', 0) or 0)}"}
    col = getattr(self, "_pcb_repair_collection", {})
    removed_keys = [row_key for row_key, row in col.items() if normalize_text(row.get("cycle_key")) in cycle_keys]
    for row_key in removed_keys:
        col.pop(row_key, None)
    if removed_keys and not col:
        self._pcb_repair_collection_scope = None
    if removed_keys:
        self._update_pcb_collection_button()
    return bool(removed_keys)

    col = getattr(self, "_pcb_repair_collection", {})
    row_key = f"pcb_{cycle.picking_id}"
    if row_key in col:
        col.pop(row_key)
        if not col:
            self._pcb_repair_collection_scope = None
        self._update_pcb_collection_button()
        return True
    return False


def collect_pcb_visible(self, *, case_filter: str | None = None) -> None:
    self._collect_all_pcb_bermasalah(case_filter=case_filter)


def _collect_all_pcb_bermasalah(self, *, case_filter: str | None = None) -> None:
    """Bulk: add visible / filtered actionable PCB cycles into the collection."""
    actionable_cycles = self._pcb_visible_actionable_cycles(case_filter=case_filter)
    filter_label = (
        self._pcb_case_label(case_value=case_filter)
        if normalize_text(case_filter)
        else "Semua Case Bermasalah"
    )
    if not actionable_cycles:
        messagebox.showinfo(
            self._display_name,
            f"Tidak ada cycle PCB actionable {filter_label} yang visible/filtered pada sidebar saat ini.",
        )
        return
    all_case2_seeds = [
        seed
        for cycle in actionable_cycles
        for seed in self._build_pcb_seeds_for_cycle(cycle)
        if self._pcb_row_uses_planned_lines(seed)
    ]
    if not self._confirm_pcb_case2_guard_rows(
        all_case2_seeds,
        source_label=f"{len(actionable_cycles)} cycle visible ({filter_label})",
    ):
        return
    success_cycles = 0
    for cycle in actionable_cycles:
        success_cycles += 1 if self._add_pcb_cycle_to_collection(cycle, skip_case2_guard_confirm=True) else 0
    self.status_var.set(
        f"PCB Repair Collection: {len(getattr(self, '_pcb_repair_collection', {}))} row aktif dari {success_cycles} cycle visible ({filter_label})."
    )


def _on_pcb_sidebar_right_click(self, event: Any) -> str:
    if not self._is_purchase_cycle_mode():
        return "break"
    tree = self.sidebar_tree
    previous_selection = tuple(tree.selection())
    previous_focus = normalize_text(tree.focus())
    row_iid = tree.identify_row(event.y)
    row_cycle = self._resolve_pcb_sidebar_cycle(row_iid)
    if row_iid and row_cycle is not None and row_iid not in tree.selection():
        tree.selection_set(row_iid)
        tree.focus(row_iid)

    fallback_item_ids = list(previous_selection)
    if previous_focus:
        fallback_item_ids.append(previous_focus)
    cycle = self._resolve_pcb_sidebar_cycle(row_iid, fallback_item_ids=fallback_item_ids)
    can_attempt_add = cycle is not None and normalize_text(getattr(cycle, "cycle_status", "")).lower() == "problem"
    is_actionable = cycle is not None and bool(self._build_pcb_seeds_for_cycle(cycle))
    can_remove = cycle is not None and self._is_pcb_cycle_in_collection(cycle)
    has_collection = bool(getattr(self, "_pcb_repair_collection", None))
    visible_actionable_cases = {
        case_key: bool(rows)
        for case_key, rows in self._pcb_visible_actionable_cycle_buckets().items()
    }
    has_visible_actionable_cycles = any(visible_actionable_cases.values())

    menu = tk.Menu(self.root, tearoff=0)
    menu.add_command(
        label="Add Selected Cycle to PCB Repair Collection",
        command=lambda c=cycle: self._add_pcb_cycle_to_collection(c),
        state="normal" if can_attempt_add else "disabled",
    )
    menu.add_command(
        label="Remove Cycle from PCB Repair Collection",
        command=lambda c=cycle: self._remove_pcb_cycle_from_collection(c),
        state="normal" if can_remove else "disabled",
    )
    menu.add_separator()
    menu.add_command(
        label="Open PCB Repair Collection...",
        command=self._open_pcb_repair_dialog,
        state="normal" if has_collection else "disabled",
    )
    menu.add_separator()
    collect_visible_menu = tk.Menu(menu, tearoff=0)
    collect_visible_menu.add_command(
        label="Semua Case Bermasalah",
        command=lambda: self._collect_all_pcb_bermasalah(),
        state="normal" if has_visible_actionable_cycles else "disabled",
    )
    for case_key in self._PCB_PROBLEM_CASE_ORDER:
        collect_visible_menu.add_command(
            label=f"{self._pcb_case_label(case_value=case_key)} saja",
            command=lambda current_case=case_key: self._collect_all_pcb_bermasalah(case_filter=current_case),
            state="normal" if visible_actionable_cases.get(case_key) else "disabled",
        )
    menu.add_cascade(
        label="Collect PCB Repair (Visible)",
        menu=collect_visible_menu,
        state="normal" if has_visible_actionable_cycles else "disabled",
    )
    try:
        menu.tk_popup(event.x_root, event.y_root)
    finally:
        menu.grab_release()
    return "break"

    if not self._is_purchase_cycle_mode():
        return "break"
    tree = self.sidebar_tree
    row_iid = tree.identify_row(event.y)
    if row_iid and row_iid not in tree.selection():
        tree.selection_set(row_iid)
        tree.focus(row_iid)

    cycle = getattr(self, "_pcb_cycle_by_iid", {}).get(row_iid) if row_iid else None
    can_add = cycle is not None and cycle.cycle_status == "problem"
    can_remove = cycle is not None and self._is_pcb_cycle_in_collection(cycle)
    has_collection = bool(getattr(self, "_pcb_repair_collection", None))
    has_snapshot_cycles = bool(
        getattr(getattr(self, "_latest_snapshot", None), "purchase_cycles", None)
    )

    menu = tk.Menu(self.root, tearoff=0)
    menu.add_command(
        label="Tambah Cycle ke PCB Collection",
        command=lambda c=cycle: self._add_pcb_cycle_to_collection(c),
        state="normal" if can_add else "disabled",
    )
    menu.add_command(
        label="Hapus dari PCB Collection",
        command=lambda c=cycle: self._remove_pcb_cycle_from_collection(c),
        state="normal" if can_remove else "disabled",
    )
    menu.add_separator()
    menu.add_command(
        label="Buka PCB Repair Collection...",
        command=self._open_pcb_repair_dialog,
        state="normal" if has_collection else "disabled",
    )
    menu.add_separator()
    menu.add_command(
        label="Collect Semua Bermasalah (Bulk)",
        command=self._collect_all_pcb_bermasalah,
        state="normal" if has_snapshot_cycles else "disabled",
    )
    try:
        menu.tk_popup(event.x_root, event.y_root)
    finally:
        menu.grab_release()
    return "break"

def _pcb_adjustment_warning_text(adjustment_rows: list[Any] | None) -> str:
    rows = list(adjustment_rows or [])
    if not rows:
        return ""
    labels: list[str] = []
    seen: set[str] = set()
    ambiguous_count = 0
    for row in rows:
        label = normalize_text(getattr(row, "move_name", "")) or normalize_text(getattr(row, "move_ref", ""))
        if not label:
            move_id = int(getattr(row, "move_id", 0) or 0)
            label = f"Move #{move_id}" if move_id > 0 else ""
        if label and label not in seen:
            seen.add(label)
            labels.append(label)
        if bool(getattr(row, "ambiguous", False)):
            ambiguous_count += 1
    if not labels:
        return ""
    text = f"Warning: Clearing adjustment -> {labels[0]}"
    if len(labels) > 1:
        text += f" (+{len(labels) - 1})"
    if ambiguous_count > 0:
        text += " | match ambiguous"
    return text

def _pcb_adjustment_match_text(row: Any) -> str:
    basis = normalize_text(getattr(row, "matched_basis", ""))
    origin_basis = normalize_text(getattr(row, "origin_basis", ""))
    verified = bool(getattr(row, "verified_for_case34", False))
    match_parts = [basis]
    if origin_basis:
        match_parts.append(origin_basis)
    if verified:
        match_parts.append("verified-case34")
    if bool(getattr(row, "ambiguous", False)):
        match_parts.append("ambiguous")
    return " | ".join(part for part in match_parts if part) or ("ambiguous" if bool(getattr(row, "ambiguous", False)) else "")

def _pcb_adjustment_summary_text(row: Any) -> str:
    source_parts = [
        normalize_text(getattr(row, "source_account_code", "")),
        normalize_text(getattr(row, "source_account_name", "")),
    ]
    item_parts = [
        normalize_text(getattr(row, "item_code", "")),
        normalize_text(getattr(row, "item_name", "")),
    ]
    summary_parts = [
        " - ".join(part for part in source_parts if part),
        " - ".join(part for part in item_parts if part),
    ]
    verified_amount = round(float(getattr(row, "repair_clearing_amount", 0.0) or 0.0), 2)
    if abs(verified_amount) >= 0.01:
        summary_parts.append(f"Clr: {verified_amount:,.2f}")
    origin_move_name = normalize_text(getattr(row, "origin_move_name", ""))
    if origin_move_name:
        summary_parts.append(f"Origin: {origin_move_name}")
    ref_text = normalize_text(getattr(row, "move_ref", ""))
    if ref_text:
        summary_parts.append(f"Ref: {ref_text}")
    return "  |  ".join(part for part in summary_parts if part)

def _pcb_external_clearing_amount(value: Any) -> float:
    getter = value.get if isinstance(value, dict) else lambda key, default=None: getattr(value, key, default)
    return round(
        float(getter("external_clearing_amount", 0.0) or getter("verified_audit_clearing_amount", 0.0) or 0.0),
        2,
    )


def _render_purchase_cycle_detail(self, cycle: Any) -> None:
    """Render the account balance summary for a selected purchase cycle.

    Populates pcb_detail_tree (in _pcb_summary_outer, below raw AML table).
    Columns: date | journal_source | transaction_no | gr_reference | po |
             partner_reference | akun | akun_name | debit | credit | balance | matching
    """
    try:
        ca_tree = getattr(self, "pcb_detail_tree", None)
        if ca_tree is None:
            return
        children = ca_tree.get_children()
        if children:
            ca_tree.delete(*children)

        icon_map = {"balanced": "✅", "acceptable": "ℹ️", "info": "🔵", "problem": "❌"}
        # svl_orphan reused here for its red background; not SVL-specific in this context
        tag_map = {"balanced": "", "acceptable": "fallback_hint", "info": "fallback_hint", "problem": "svl_orphan"}

        # Header row: cycle summary
        gr_date = (cycle.gr_date or "")[:10]
        po_label = ", ".join(cycle.purchase_orders) if cycle.purchase_orders else ""
        status_icon = self._PCB_STATUS_ICON.get(cycle.cycle_status, "")

        # Update item header label (cycle-level: show picking + PO)
        if hasattr(self, "_pcb_item_header_var"):
            _hdr = cycle.picking_name or ""
            if po_label:
                _hdr += f" | {po_label}"
            self._pcb_item_header_var.set(_hdr)

        def _refs_label(refs: list, n: int = 3) -> str:
            if not refs:
                return "—"
            label = ", ".join(refs[:n])
            if len(refs) > n:
                label += f" (+{len(refs) - n})"
            return label

        stj_label = _refs_label(cycle.stj_refs) if cycle.stj_refs else ""
        bill_label = _refs_label(cycle.bill_refs)
        payment_label = _refs_label(cycle.payment_refs)
        bank_label = _refs_label(cycle.bank_refs)

        ca_tree.insert(
            "", "end",
            values=(
                gr_date,                        # date
                status_icon + " Cycle",         # journal_source
                stj_label,                      # transaction_no
                cycle.picking_name,             # gr_reference
                po_label,                       # po
                (cycle.partner_name or "")[:40],# partner_reference
                "",                             # akun
                f"Bill: {bill_label}  Pmt: {payment_label}  BK: {bank_label}",  # akun_name
                f"{cycle.total_debit:,.2f}",    # debit
                f"{cycle.total_credit:,.2f}",   # credit
                f"{cycle.total_debit - cycle.total_credit:+,.2f}",  # balance
                f"{cycle.problem_account_count} akun bermasalah",   # matching
            ),
            tags=("cycle_group",),
        )

        adjustment_warning_text = normalize_text(getattr(cycle, "adjustment_warning_text", ""))
        if adjustment_warning_text:
            ca_tree.insert(
                "", "end",
                values=(
                    "",
                    "⚠ Audit",
                    "",
                    "",
                    "",
                    "",
                    "",
                    adjustment_warning_text,
                    "",
                    "",
                    "",
                    "",
                ),
                tags=("fallback_hint",),
            )
        for audit_row in list(getattr(cycle, "adjustment_audit_rows", None) or []):
            move_label = normalize_text(getattr(audit_row, "move_name", "")) or normalize_text(getattr(audit_row, "move_ref", ""))
            ca_tree.insert(
                "", "end",
                values=(
                    normalize_text(getattr(audit_row, "move_date", ""))[:10],
                    "Adj Audit",
                    move_label,
                    normalize_text(getattr(audit_row, "move_ref", "")),
                    "",
                    "",
                    normalize_text(getattr(audit_row, "clearing_account_code", "")),
                    self._pcb_adjustment_summary_text(audit_row),
                    "",
                    "",
                    "",
                    self._pcb_adjustment_match_text(audit_row),
                ),
                tags=("fallback_hint",),
            )

        # If merged multi-picking cycle, show all picking names
        all_picking_names = getattr(cycle, "picking_names", [])
        if len(all_picking_names) > 1:
            ca_tree.insert(
                "", "end",
                values=(
                    "",
                    "🔗 Multi-picking",
                    "",
                    ", ".join(all_picking_names),
                    "",
                    "",
                    "",
                    f"Cycle digabung: {len(all_picking_names)} picking share 1 bill",
                    "",
                    "",
                    "",
                    "",
                ),
                tags=("fallback_hint",),
            )

        # One row per account in the cycle
        for row in (cycle.account_rows or []):
            icon = icon_map.get(row.status, "")
            tag = tag_map.get(row.status, "")
            ca_tree.insert(
                "", "end",
                values=(
                    "",                             # date
                    row.account_type,               # journal_source
                    "",                             # transaction_no
                    "",                             # gr_reference
                    "",                             # po
                    "",                             # partner_reference
                    row.code,                       # akun
                    (row.name or "")[:60],          # akun_name
                    f"{row.debit:,.2f}",            # debit
                    f"{row.credit:,.2f}",           # credit
                    f"{row.net_balance:+,.2f}",     # balance
                    icon + " " + (row.status or ""),# matching
                ),
                tags=(tag,) if tag else (),
            )

        # Populate PCB account summary (replaces KPI cards in PCB mode)
        self._populate_pcb_acct_summary(cycle.account_rows, cycle=cycle)

        # Populate PCB raw AML table
        self._populate_pcb_raw_tree(cycle)
    except Exception:  # noqa: BLE001
        pass


def _populate_pcb_acct_summary(
    self, account_rows: list[Any], *, cycle: Any, item_row: Any | None = None
) -> None:
    """Populate pcb_acct_summary_tree with accounts grouped by status.

    Order: problem → balanced → acceptable → info → (others)
    For each account: status icon | code | name | net saldo (+debit / -credit)
    Also sets header_code_var / header_name_var based on cycle + item context.
    """
    tree = getattr(self, "pcb_acct_summary_tree", None)
    if tree is None:
        return
    ch = tree.get_children()
    if ch:
        tree.delete(*ch)

    icon_map = {"problem": "❌", "balanced": "✅", "acceptable": "ℹ️", "info": "🔵"}
    tag_map = {"problem": "svl_orphan", "balanced": "", "acceptable": "fallback_hint", "info": "fallback_hint"}
    status_order = ["problem", "balanced", "acceptable", "info"]
    rows_by_status: dict[str, list[Any]] = {s: [] for s in status_order}
    rows_other: list[Any] = []
    for r in (account_rows or []):
        if r.status in rows_by_status:
            rows_by_status[r.status].append(r)
        else:
            rows_other.append(r)

    for status in status_order:
        for row in rows_by_status[status]:
            icon = icon_map.get(status, "")
            tag = tag_map.get(status, "")
            saldo = row.net_balance
            saldo_str = f"{saldo:+,.2f}"
            tree.insert(
                "", "end",
                values=(icon, row.code, (row.name or "")[:50], saldo_str),
                tags=(tag,) if tag else (),
            )
    for row in rows_other:
        tree.insert("", "end", values=("", row.code, (row.name or "")[:50], f"{row.net_balance:+,.2f}"))

    # Update header vars with cycle / item context
    po_label = ", ".join(cycle.purchase_orders) if cycle.purchase_orders else ""
    if item_row is not None:
        _code = getattr(item_row, "default_code", "") or ""
        _code_part = f"[{_code}]  " if _code else ""
        self.header_code_var.set(f"{_code_part}{cycle.picking_name or ''}")
        self.header_name_var.set(item_row.product_name or f"Produk #{item_row.product_id}")
    else:
        self.header_code_var.set(cycle.picking_name or "")
        names = [ir.product_name for ir in (cycle.item_rows or []) if ir.product_name]
        if names:
            self.header_name_var.set(", ".join(names[:3]) + (f" (+{len(names)-3})" if len(names) > 3 else ""))
        else:
            self.header_name_var.set("")
    if hasattr(self, "header_meta_var"):
        meta_parts = [f"PO: {po_label}" if po_label else ""]
        item_adjustment_warning = self._pcb_adjustment_warning_text(
            getattr(item_row, "adjustment_audit_rows", None) if item_row is not None else None
        )
        cycle_adjustment_warning = normalize_text(getattr(cycle, "adjustment_warning_text", ""))
        warning_text = item_adjustment_warning or cycle_adjustment_warning
        if warning_text:
            meta_parts.append(warning_text)
        self.header_meta_var.set(" | ".join(part for part in meta_parts if part))

# ── PCB raw sort key helpers ───────────────────────────────────────────────

def _pcb_process_order(line: dict) -> int:
    """Return sort priority for 'Sort by Process' mode.

    Order:
      0 — GR / STJ (asset_current: persediaan + clearing)
      1 — Bill (BILL: liability_payable / liability_current)
      2 — PBK outstanding (PBK: asset_current outstanding)
      3 — Payment / BK (asset_cash / bank, or BK jenis)
      9 — everything else
    """
    jenis = (line.get("jenis") or "").upper()
    tipe = (line.get("tipe_akun") or "").lower()
    akun_code = str(line.get("akun_code") or "")

    if jenis == "STJ":
        return 0
    if jenis == "BILL":
        return 1
    if jenis == "PBK":
        # Outstanding payments accounts typically start with 1112
        if "outstanding" in (line.get("akun_name") or "").lower() or akun_code.startswith("1112"):
            return 2
        return 2
    if jenis == "BK" or tipe == "asset_cash":
        return 3
    return 9

def _pcb_process_label(cls, line: dict[str, Any]) -> str:
    label_map = {
        0: "GR / STJ",
        1: "Bill",
        2: "Payment Outstanding",
        3: "Bank / BK",
        9: "Other",
    }
    return label_map.get(cls._pcb_process_order(line), "Other")


def _pcb_current_raw_sort_mode(self) -> str:
    sort_var = getattr(self, "_pcb_raw_sort_var", None)
    if sort_var is None:
        return "Sort by Account"
    try:
        return sort_var.get()
    except Exception:  # noqa: BLE001
        return "Sort by Account"


def _pcb_sorted_raw_lines(self, cycle: Any, *, sort_mode: str | None = None) -> list[dict[str, Any]]:
    raw_lines = list(getattr(cycle, "raw_lines", None) or [])
    active_sort_mode = normalize_text(sort_mode) or self._pcb_current_raw_sort_mode()
    if active_sort_mode == "Sort by Process":
        return sorted(
            raw_lines,
            key=lambda line: (
                self._pcb_process_order(line),
                line.get("akun_code") or "",
                line.get("kode_item") or "",
                line.get("tanggal") or "",
            ),
        )
    return sorted(
        raw_lines,
        key=lambda line: (line.get("akun_code") or "", line.get("kode_item") or "", line.get("tanggal") or ""),
    )


def _set_pcb_cycle_export_button_state(self, enabled: bool) -> None:
    button = getattr(self, "_pcb_export_excel_button", None)
    if button is None:
        return
    button.configure(state="normal" if enabled else "disabled")


def _on_pcb_raw_sort_changed(self) -> None:
    """Re-populate pcb_raw_tree with updated sort when user changes the sort control."""
    cycle = getattr(self, "_pcb_raw_current_cycle", None)
    if cycle is not None:
        self._populate_pcb_raw_tree(cycle)


def _populate_pcb_raw_tree(self, cycle: Any) -> None:
    """Fill pcb_raw_tree with the raw AML lines for the given cycle.

    Layout:
    - Row 0: partner header (all unique partners separated by  |)
    - Remaining rows sorted by selected sort mode
    """
    if not hasattr(self, "pcb_raw_tree"):
        return
    # Store cycle reference for re-sort on sort change
    self._pcb_raw_current_cycle = cycle
    self._set_pcb_cycle_export_button_state(True)
    rt = self.pcb_raw_tree
    children = rt.get_children()
    if children:
        rt.delete(*children)

    raw_lines = list(getattr(cycle, "raw_lines", None) or [])

    # Notice: all picking names (not abbreviated) + AML count
    all_picking_names: list[str] = list(getattr(cycle, "picking_names", None) or [])
    if not all_picking_names:
        all_picking_names = [cycle.picking_name] if cycle.picking_name else []
    picking_label = "  |  ".join(all_picking_names) if all_picking_names else cycle.picking_name
    if hasattr(self, "_pcb_raw_notice_var"):
        self._pcb_raw_notice_var.set(
            f"{len(raw_lines)} baris AML  —  {picking_label}"
        )

    # Partner header label (full-width, above tree)
    unique_partners = sorted({
        (line.get("partner") or "").strip()
        for line in raw_lines
        if (line.get("partner") or "").strip()
    })
    partner_text = "  |  ".join(unique_partners) if unique_partners else ""
    if hasattr(self, "_pcb_partner_header_var"):
        self._pcb_partner_header_var.set(
            f"Partner: {partner_text}" if partner_text else ""
        )

    sorted_lines = self._pcb_sorted_raw_lines(cycle)

    for line in sorted_lines:
        rt.insert(
            "", "end",
            values=(
                (line.get("tanggal") or "")[:10],
                line.get("kode_transaksi", ""),
                line.get("jenis", ""),
                line.get("tipe_akun", ""),
                line.get("akun_code", ""),
                line.get("akun_name", ""),
                line.get("kode_item", ""),
                line.get("nama_item", ""),
                line.get("uom", ""),
                f"{line.get('qty_item', 0):g}" if line.get("qty_item") else "",
                line.get("kategori_produk", ""),
                line.get("no_po", ""),
                line.get("komunikasi", ""),
                f"{line.get('debit', 0):,.2f}",
                f"{line.get('kredit', 0):,.2f}",
                f"{line.get('saldo', 0):+,.2f}",
                line.get("matching", ""),
            ),
        )

# ── PCB raw tree interaction ───────────────────────────────────────────────


def _pcb_raw_cell_at(self, event: Any) -> tuple[str, str, str]:
    """Return (row_iid, col_id "#N", cell_value) for a click event on pcb_raw_tree."""
    tree = self.pcb_raw_tree
    row_iid = tree.identify_row(event.y)
    col_id = tree.identify_column(event.x)  # "#1", "#2", …
    if not row_iid or not col_id:
        return ("", "", "")
    try:
        col_idx = int(col_id.lstrip("#")) - 1
        vals = tree.item(row_iid, "values")
        cell_val = vals[col_idx] if 0 <= col_idx < len(vals) else ""
    except (ValueError, IndexError):
        cell_val = ""
    return (row_iid, col_id, str(cell_val))


def _on_pcb_raw_cell_click(self, event: Any) -> None:
    """Track selected cell on left-click; let native extended selection (Ctrl/Shift) work."""
    if not hasattr(self, "pcb_raw_tree"):
        return
    row_iid, col_id, _ = self._pcb_raw_cell_at(event)
    if row_iid:
        self._pcb_raw_selected_cell = (row_iid, col_id)


def _pcb_raw_copy_cell(self, _event: Any = None) -> str:
    """Ctrl+C: copy focused cell if single selection, else copy all selected rows."""
    if not hasattr(self, "pcb_raw_tree"):
        return "break"
    tree = self.pcb_raw_tree
    sel = tree.selection()
    row_iid, col_id = getattr(self, "_pcb_raw_selected_cell", ("", ""))

    if len(sel) > 1:
        # Multi-row: copy all selected rows, tab-separated columns, newline between rows
        lines = ["\t".join(str(v) for v in tree.item(iid, "values")) for iid in sel]
        self.root.clipboard_clear()
        self.root.clipboard_append("\n".join(lines))
        return "break"

    # Single row: copy focused cell
    if not row_iid or not col_id:
        if sel:
            row_iid = sel[0]
            col_id = "#1"
        else:
            return "break"
    try:
        col_idx = int(col_id.lstrip("#")) - 1
        vals = tree.item(row_iid, "values")
        cell_val = str(vals[col_idx]) if 0 <= col_idx < len(vals) else ""
    except (ValueError, IndexError):
        cell_val = ""
    self.root.clipboard_clear()
    self.root.clipboard_append(cell_val)
    return "break"


def _on_pcb_raw_right_click(self, event: Any) -> None:
    """Show context menu on right-click in pcb_raw_tree."""
    if not hasattr(self, "pcb_raw_tree"):
        return
    tree = self.pcb_raw_tree
    row_iid, col_id, cell_val = self._pcb_raw_cell_at(event)
    if row_iid:
        self._pcb_raw_selected_cell = (row_iid, col_id)
        # Preserve multi-selection when right-clicking on an already-selected row
        if row_iid not in tree.selection():
            tree.selection_set(row_iid)

    menu = tk.Menu(self.root, tearoff=0)

    def _copy_cell() -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(cell_val)

    def _copy_row() -> None:
        sel = tree.selection()
        targets = list(sel) if len(sel) > 1 else ([row_iid] if row_iid else [])
        if not targets:
            return
        lines = ["\t".join(str(v) for v in tree.item(iid, "values")) for iid in targets]
        self.root.clipboard_clear()
        self.root.clipboard_append("\n".join(lines))

    def _copy_column() -> None:
        if not col_id:
            return
        try:
            col_idx = int(col_id.lstrip("#")) - 1
        except ValueError:
            return
        lines = []
        for iid in tree.get_children():
            vals = tree.item(iid, "values")
            lines.append(str(vals[col_idx]) if 0 <= col_idx < len(vals) else "")
        self.root.clipboard_clear()
        self.root.clipboard_append("\n".join(lines))

    def _select_all() -> None:
        all_iids = tree.get_children()
        if all_iids:
            tree.selection_set(all_iids)

    def _set_height() -> None:
        self._pcb_raw_set_row_height(row_iid)

    menu.add_command(label="Copy Cell", command=_copy_cell)
    menu.add_command(label="Copy Row (tab-separated)", command=_copy_row)
    menu.add_command(label="Copy Column", command=_copy_column)
    menu.add_separator()
    menu.add_command(label="Set Tinggi Baris...", command=_set_height)
    menu.add_command(label="Reset Tinggi Baris", command=lambda: self._pcb_raw_set_row_height(row_iid, reset=True))
    menu.add_separator()
    menu.add_command(label="Select All", command=_select_all)
    menu.tk_popup(event.x_root, event.y_root)


def _pcb_raw_set_row_height(self, row_iid: str, *, reset: bool = False) -> None:
    """Set (or reset) the height of a specific row in pcb_raw_tree.

    Asks the user for the desired number of text lines via a dialog.
    Uses the Tk item -height option which multiplies the base rowheight.
    """
    if not hasattr(self, "pcb_raw_tree") or not row_iid:
        return
    tree = self.pcb_raw_tree
    if reset:
        n = 1
    else:
        n = simpledialog.askinteger(
            "Set Tinggi Baris",
            "Berapa baris teks untuk row ini?\n(1 = default, 2 = 2x tinggi, dst.)",
            initialvalue=1,
            minvalue=1,
            maxvalue=20,
            parent=self.root,
        )
        if n is None:
            return
    try:
        tree.tk.call(str(tree), "item", row_iid, "-height", int(n))
    except Exception:  # noqa: BLE001
        pass
    tree.update_idletasks()


def _render_purchase_cycle_item_detail(self, cycle: Any, item_row: Any) -> None:
    """Render per-item account breakdown for a selected item in a purchase cycle."""
    try:
        ca_tree = getattr(self, "pcb_detail_tree", None)
        if ca_tree is None:
            return
        children = ca_tree.get_children()
        if children:
            ca_tree.delete(*children)

        icon_map = {"balanced": "✅", "acceptable": "ℹ️", "info": "🔵", "problem": "❌"}
        tag_map = {"balanced": "", "acceptable": "fallback_hint", "info": "fallback_hint", "problem": "svl_orphan"}

        # Header row: item + cycle context
        gr_date = (cycle.gr_date or "")[:10]
        po_label = ", ".join(cycle.purchase_orders) if cycle.purchase_orders else ""
        item_name = item_row.product_name or f"Produk #{item_row.product_id}"

        # Update item header label: [code] name — picking | PO
        if hasattr(self, "_pcb_item_header_var"):
            _code = getattr(item_row, "default_code", "") or ""
            _code_part = f"[{_code}] " if _code else ""
            _hdr = f"{_code_part}{item_name} — {cycle.picking_name or ''}"
            if po_label:
                _hdr += f" | {po_label}"
            self._pcb_item_header_var.set(_hdr)
        val_method = getattr(item_row, "valuation_method", "automated")
        val_label = "🔄 Automated" if val_method != "manual" else "📋 Manual"
        cycle_icon = self._PCB_STATUS_ICON.get(cycle.cycle_status, "")
        item_acct_rows = list(item_row.account_rows or [])
        item_total_dr = sum(r.debit for r in item_acct_rows)
        item_total_cr = sum(r.credit for r in item_acct_rows)
        item_net = item_total_dr - item_total_cr
        prob_count = sum(1 for r in item_acct_rows if r.status == "problem")

        ca_tree.insert(
            "", "end",
            values=(
                gr_date,                                            # date
                f"📦 {val_label}",                                  # journal_source
                "",                                                 # transaction_no
                cycle.picking_name,                                 # gr_reference
                po_label,                                           # po
                (cycle.partner_name or "")[:40],                    # partner_reference
                "",                                                 # akun
                item_name[:60],                                     # akun_name
                f"{item_total_dr:,.2f}",                           # debit
                f"{item_total_cr:,.2f}",                           # credit
                f"{item_net:+,.2f}",                               # balance
                f"{cycle_icon} {prob_count} akun bermasalah",      # matching
            ),
            tags=("cycle_group",),
        )

        bill_item_refs = ", ".join(list(getattr(item_row, "bill_refs", None) or [])) or "No bill item"
        stj_link_text = self._pcb_item_stj_link_text(item_row)
        verified_clearing_amount = self._pcb_external_clearing_amount(item_row)
        external_clearing_refs = ", ".join(
            [
                normalize_text(value)
                for value in list(getattr(item_row, "external_clearing_refs", None) or [])
                if normalize_text(value)
            ]
        ) or "Not verified"
        external_clearing_basis = normalize_text(getattr(item_row, "external_clearing_basis", "")) or "Not verified"
        external_clearing_verified = bool(getattr(item_row, "external_clearing_verified", False)) and verified_clearing_amount >= 0.01
        eligibility_label = "Eligible Case 3/4" if bool(getattr(item_row, "eligible_case34", False)) else "Not Eligible Case 3/4"
        ca_tree.insert(
            "", "end",
            values=(
                "",
                "Item Evidence",
                bill_item_refs,
                stj_link_text,
                "",
                "",
                "",
                f"External Clearing: {verified_clearing_amount:,.2f} | {eligibility_label}",
                "",
                "",
                "",
                "",
            ),
            tags=("fallback_hint",),
        )
        ca_tree.insert(
            "", "end",
            values=(
                "",
                "External Clearing",
                f"{verified_clearing_amount:,.2f}" if verified_clearing_amount >= 0.01 else "-",
                external_clearing_refs,
                "",
                "",
                "1108099" if verified_clearing_amount >= 0.01 else "",
                f"{'Verified' if external_clearing_verified else 'Not verified'} | Basis: {external_clearing_basis}",
                "",
                "",
                "",
                "",
            ),
            tags=("fallback_hint",),
        )

        for audit_row in list(getattr(item_row, "adjustment_audit_rows", None) or []):
            move_label = normalize_text(getattr(audit_row, "move_name", "")) or normalize_text(getattr(audit_row, "move_ref", ""))
            ca_tree.insert(
                "", "end",
                values=(
                    normalize_text(getattr(audit_row, "move_date", ""))[:10],
                    "Adj Audit",
                    move_label,
                    normalize_text(getattr(audit_row, "move_ref", "")),
                    "",
                    "",
                    normalize_text(getattr(audit_row, "clearing_account_code", "")),
                    self._pcb_adjustment_summary_text(audit_row),
                    "",
                    "",
                    "",
                    self._pcb_adjustment_match_text(audit_row),
                ),
                tags=("fallback_hint",),
            )

        # One row per account for this item
        for row in item_acct_rows:
            icon = icon_map.get(row.status, "")
            tag = tag_map.get(row.status, "")
            ca_tree.insert(
                "", "end",
                values=(
                    "",
                    row.account_type,
                    "",
                    "",
                    "",
                    "",
                    row.code,
                    (row.name or "")[:60],
                    f"{row.debit:,.2f}",
                    f"{row.credit:,.2f}",
                    f"{row.net_balance:+,.2f}",
                    icon + " " + (row.status or ""),
                ),
                tags=(tag,) if tag else (),
            )

        # Populate PCB account summary (item-level, replaces KPI cards)
        self._populate_pcb_acct_summary(item_row.account_rows, cycle=cycle, item_row=item_row)

        # Populate PCB raw AML table (same cycle-level data regardless of item selection)
        self._populate_pcb_raw_tree(cycle)
    except Exception:  # noqa: BLE001
        pass


def _open_pcb_coa_settings(self) -> None:
    """Open dialog to configure PCB COA classification codes."""
    dialog = tk.Toplevel(self.root)
    dialog.title("Pengaturan Klasifikasi COA - Balance Cycle Pembelian")
    dialog.resizable(False, False)
    dialog.transient(self.root)
    dialog.grab_set()
    pad = {"padx": 12, "pady": 6}
    tk.Label(dialog, text="Kode COA Bermasalah", font=("Segoe UI", 9, "bold"), anchor="w").grid(row=0, column=0, sticky="ew", **pad)
    tk.Label(dialog, text="Jika ada saldo → cycle BERMASALAH. Pisahkan dengan koma.", font=("Segoe UI", 8), fg="#666", anchor="w").grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 2))
    problem_entry = tk.Entry(dialog, textvariable=self._pcb_problem_codes_var, width=50, relief="solid", bd=1)
    problem_entry.grid(row=2, column=0, sticky="ew", **pad)

    tk.Label(dialog, text="Kode COA Informasi/Warning", font=("Segoe UI", 9, "bold"), anchor="w").grid(row=3, column=0, sticky="ew", **pad)
    tk.Label(dialog, text="Jika ada saldo → ditampilkan sebagai warning saja. Pisahkan dengan koma.", font=("Segoe UI", 8), fg="#666", anchor="w").grid(row=4, column=0, sticky="ew", padx=12, pady=(0, 2))
    info_entry = tk.Entry(dialog, textvariable=self._pcb_info_codes_var, width=50, relief="solid", bd=1)
    info_entry.grid(row=5, column=0, sticky="ew", **pad)

    tk.Label(dialog, text="Catatan: Semua COA lain (Persediaan, HPP, Bank, dll.) otomatis dianggap acceptable.", font=("Segoe UI", 8), fg="#888", wraplength=400, justify="left").grid(row=6, column=0, sticky="ew", padx=12, pady=(8, 2))

    def _save():
        self._save_settings()
        dialog.destroy()
        if self._is_purchase_cycle_mode():
            self._apply_pcb_search_filter()

    btn_row = tk.Frame(dialog)
    btn_row.grid(row=7, column=0, sticky="e", pady=8, padx=12)
    tk.Button(btn_row, text="Simpan & Terapkan", command=_save, relief="solid").pack(side="right", padx=(4, 0))
    tk.Button(btn_row, text="Batal", command=dialog.destroy, relief="solid").pack(side="right")
    dialog.columnconfigure(0, weight=1)


