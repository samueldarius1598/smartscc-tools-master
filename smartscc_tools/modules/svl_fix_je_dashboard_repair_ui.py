"""Lazy-loaded repair UI helpers for SVL Fix JE dashboard page."""

from __future__ import annotations

from smartscc_tools.modules import svl_fix_je_dashboard_page as page_mod

def _sync_page_globals() -> None:
    for _name, _value in page_mod.__dict__.items():
        if _name.startswith("__"):
            continue
        globals()[_name] = _value


_sync_page_globals()


def _normalize_repair_target_account_role(value: str) -> str:
    clean_value = normalize_text(value).lower()
    return REPAIR_TARGET_ACCOUNT_ROLE_COMPATIBILITY_MAP.get(clean_value, clean_value)

def _normalized_repair_candidate_role(candidate: SvlDashboardRepairAccountCandidate) -> str:
    field_name = normalize_text(getattr(candidate, "field_name", "")).lower()
    source = normalize_text(getattr(candidate, "source", "")).lower()
    role = SvlFixJeDashboardPage._normalize_repair_target_account_role(
        normalize_text(getattr(candidate, "role", ""))
    )
    if field_name == "property_stock_valuation_account_id":
        return "valuation"
    if "category stock valuation" in source or "category valuation" in source:
        return "valuation"
    if "category input" in source:
        return "input"
    if "category output" in source:
        return "output"
    if "category expense" in source:
        return "expense"
    if "category cost" in source:
        return "cost"
    if "category other" in source or "category inventory" in source:
        return "other"
    return role

def _repair_account_role_priority(role_value: str) -> int:
    clean_role = SvlFixJeDashboardPage._normalize_repair_target_account_role(role_value)
    return REPAIR_ACCOUNT_ROLE_PRIORITY.get(clean_role, 999)

def _dedupe_repair_account_candidates(
    candidates: list[SvlDashboardRepairAccountCandidate] | list[Any],
) -> list[SvlDashboardRepairAccountCandidate]:
    merged: dict[str, dict[str, Any]] = {}
    for raw_candidate in candidates or []:
        if isinstance(raw_candidate, SvlDashboardRepairAccountCandidate):
            candidate = raw_candidate
        else:
            candidate = SvlDashboardRepairAccountCandidate(
                code=normalize_text(getattr(raw_candidate, "code", "") or (raw_candidate.get("code") if isinstance(raw_candidate, dict) else "")).upper(),
                name=normalize_text(getattr(raw_candidate, "name", "") or (raw_candidate.get("name") if isinstance(raw_candidate, dict) else "")),
                source=normalize_text(getattr(raw_candidate, "source", "") or (raw_candidate.get("source") if isinstance(raw_candidate, dict) else "")),
                role=normalize_text(getattr(raw_candidate, "role", "") or (raw_candidate.get("role") if isinstance(raw_candidate, dict) else "")),
                account_id=int(getattr(raw_candidate, "account_id", 0) or (raw_candidate.get("account_id") if isinstance(raw_candidate, dict) else 0) or 0),
                field_name=normalize_text(getattr(raw_candidate, "field_name", "") or (raw_candidate.get("field_name") if isinstance(raw_candidate, dict) else "")),
            )
        code = normalize_text(candidate.code).upper()
        if not code:
            continue
        normalized_role = SvlFixJeDashboardPage._normalized_repair_candidate_role(candidate)
        entry = merged.get(code)
        if entry is None:
            merged[code] = {
                "code": code,
                "name": normalize_text(candidate.name),
                "sources": [normalize_text(candidate.source)] if normalize_text(candidate.source) else [],
                "role": normalized_role,
                "account_id": int(candidate.account_id or 0),
                "field_name": normalize_text(candidate.field_name),
            }
            continue
        if normalize_text(candidate.name) and not normalize_text(entry["name"]):
            entry["name"] = normalize_text(candidate.name)
        source = normalize_text(candidate.source)
        if source and source not in entry["sources"]:
            entry["sources"].append(source)
        if SvlFixJeDashboardPage._repair_account_role_priority(normalized_role) < SvlFixJeDashboardPage._repair_account_role_priority(
            normalize_text(entry["role"])
        ):
            entry["role"] = normalized_role
            entry["account_id"] = int(candidate.account_id or 0)
            entry["field_name"] = normalize_text(candidate.field_name)
        elif normalized_role and not normalize_text(entry["role"]):
            entry["role"] = normalized_role
            entry["account_id"] = int(candidate.account_id or 0)
            entry["field_name"] = normalize_text(candidate.field_name)
    ordered = sorted(
        merged.values(),
        key=lambda item: (
            SvlFixJeDashboardPage._repair_account_role_priority(normalize_text(item["role"])),
            normalize_text(item["code"]),
        ),
    )
    return [
        SvlDashboardRepairAccountCandidate(
            code=normalize_text(item["code"]).upper(),
            name=normalize_text(item["name"]),
            source=" | ".join(item["sources"]),
            role=normalize_text(item["role"]),
            account_id=int(item["account_id"] or 0),
            field_name=normalize_text(item["field_name"]),
        )
        for item in ordered
    ]


def _repair_account_candidates_for_seed(
    self,
    seed: dict[str, Any],
    *,
    global_candidates: list[SvlDashboardRepairAccountCandidate] | None = None,
    category_candidates_cache: dict[tuple[tuple[str, str, str, str, str, int], ...], list[SvlDashboardRepairAccountCandidate]] | None = None,
    combined_candidates_cache: dict[tuple[tuple[str, str, str, str, str, int], ...], list[SvlDashboardRepairAccountCandidate]] | None = None,
) -> list[SvlDashboardRepairAccountCandidate]:
    raw_candidates = list(seed.get("account_candidates") or [])
    cache_key = self._repair_account_candidates_cache_key(raw_candidates)
    if combined_candidates_cache is not None and cache_key in combined_candidates_cache:
        return combined_candidates_cache[cache_key]
    category_candidates = self._category_repair_account_candidates_for_seed(seed, cache=category_candidates_cache)
    active_global_candidates = global_candidates if global_candidates is not None else self._configured_repair_account_candidates()
    combined = self._dedupe_repair_account_candidates([*category_candidates, *active_global_candidates])
    if combined_candidates_cache is not None:
        combined_candidates_cache[cache_key] = combined
    return combined

def _repair_account_candidates_cache_key(candidates: list[Any] | tuple[Any, ...]) -> tuple[tuple[str, str, str, str, str, int], ...]:
    normalized_items: list[tuple[str, str, str, str, str, int]] = []
    for raw_candidate in candidates or []:
        if isinstance(raw_candidate, SvlDashboardRepairAccountCandidate):
            code = normalize_text(raw_candidate.code).upper()
            name = normalize_text(raw_candidate.name)
            source = normalize_text(raw_candidate.source)
            role = normalize_text(raw_candidate.role).lower()
            field_name = normalize_text(raw_candidate.field_name).lower()
            account_id = int(raw_candidate.account_id or 0)
        else:
            code = normalize_text(getattr(raw_candidate, "code", "") or (raw_candidate.get("code") if isinstance(raw_candidate, dict) else "")).upper()
            name = normalize_text(getattr(raw_candidate, "name", "") or (raw_candidate.get("name") if isinstance(raw_candidate, dict) else ""))
            source = normalize_text(getattr(raw_candidate, "source", "") or (raw_candidate.get("source") if isinstance(raw_candidate, dict) else ""))
            role = normalize_text(getattr(raw_candidate, "role", "") or (raw_candidate.get("role") if isinstance(raw_candidate, dict) else "")).lower()
            field_name = normalize_text(getattr(raw_candidate, "field_name", "") or (raw_candidate.get("field_name") if isinstance(raw_candidate, dict) else "")).lower()
            account_id = int(getattr(raw_candidate, "account_id", 0) or (raw_candidate.get("account_id") if isinstance(raw_candidate, dict) else 0) or 0)
        normalized_items.append((code, name, source, role, field_name, account_id))
    return tuple(normalized_items)

def _category_repair_account_candidates_for_seed(
    seed: dict[str, Any],
    *,
    cache: dict[tuple[tuple[str, str, str, str, str, int], ...], list[SvlDashboardRepairAccountCandidate]] | None = None,
) -> list[SvlDashboardRepairAccountCandidate]:
    raw_candidates = list(seed.get("account_candidates") or [])
    if cache is None:
        return SvlFixJeDashboardPage._dedupe_repair_account_candidates(raw_candidates)
    cache_key = SvlFixJeDashboardPage._repair_account_candidates_cache_key(raw_candidates)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    cached = SvlFixJeDashboardPage._dedupe_repair_account_candidates(raw_candidates)
    cache[cache_key] = cached
    return cached

def _repair_target_account_role_label(value: str) -> str:
    normalized_value = SvlFixJeDashboardPage._normalize_repair_target_account_role(value)
    return REPAIR_TARGET_ACCOUNT_ROLE_LABEL_BY_VALUE.get(normalized_value, "Target Account Type")

def _repair_target_account_candidate(
    seed: dict[str, Any],
    role_value: str,
    *,
    category_candidates_cache: dict[tuple[tuple[str, str, str, str, str, int], ...], list[SvlDashboardRepairAccountCandidate]] | None = None,
    target_candidate_cache: dict[tuple[tuple[tuple[str, str, str, str, str, int], ...], str], SvlDashboardRepairAccountCandidate | None] | None = None,
) -> SvlDashboardRepairAccountCandidate | None:
    clean_role = SvlFixJeDashboardPage._normalize_repair_target_account_role(role_value)
    if not clean_role:
        return None
    raw_candidates = list(seed.get("account_candidates") or [])
    cache_key = (
        SvlFixJeDashboardPage._repair_account_candidates_cache_key(raw_candidates),
        clean_role,
    )
    if target_candidate_cache is not None and cache_key in target_candidate_cache:
        return target_candidate_cache[cache_key]
    result = None
    for candidate in SvlFixJeDashboardPage._category_repair_account_candidates_for_seed(
        seed,
        cache=category_candidates_cache,
    ):
        if SvlFixJeDashboardPage._normalized_repair_candidate_role(candidate) == clean_role:
            result = candidate
            break
    if target_candidate_cache is not None:
        target_candidate_cache[cache_key] = result
    return result

def _missing_repair_target_account_message(row: dict[str, Any], role_value: str) -> str:
    clean_role = SvlFixJeDashboardPage._normalize_repair_target_account_role(role_value)
    category_name = normalize_text(row.get("item_category_name")) or "Tanpa kategori item"
    if clean_role == "valuation":
        return (
            "Stock Valuation Account category item belum ada / tidak terbaca "
            f"(property_stock_valuation_account_id, category: {category_name})"
        )
    label = SvlFixJeDashboardPage._repair_target_account_role_label(clean_role)
    return f"{label} account category item tidak tersedia (category: {category_name})"

def _derive_repair_account_codes(
    row: dict[str, Any],
    *,
    target_account_code: str,
    resolve_account_code: str,
) -> tuple[str, str]:
    signed_amount = float(row.get("signed_amount") or row.get("amount") or 0.0)
    clean_target_code = normalize_text(target_account_code).upper()
    clean_resolve_code = normalize_text(resolve_account_code).upper()
    if signed_amount < 0:
        return clean_resolve_code, clean_target_code
    return clean_target_code, clean_resolve_code

def _filter_repair_account_candidates(
    query: str,
    candidates: list[SvlDashboardRepairAccountCandidate],
) -> list[SvlDashboardRepairAccountCandidate]:
    clean_query = normalize_text(query).lower()
    if not clean_query:
        return list(candidates[:8])
    ranked: list[tuple[int, str, SvlDashboardRepairAccountCandidate]] = []
    for candidate in candidates:
        code = normalize_text(candidate.code).lower()
        name = normalize_text(candidate.name).lower()
        source = normalize_text(candidate.source).lower()
        searchable_values = [value for value in (code, name, f"{code} {name}", f"{code} {name} {source}") if value]
        searchable_values.extend(token for value in (name, source) for token in value.split() if token)
        tier = 99
        normalized_query = clean_query.replace(" ", "")
        for value in searchable_values:
            if value.startswith(clean_query):
                tier = min(tier, 0)
                continue
            if clean_query in value:
                tier = min(tier, 1)
                continue
            if any(token.startswith(clean_query) for token in value.split()):
                tier = min(tier, 2)
                continue
            compact_value = value.replace(" ", "")
            if normalized_query and _is_subsequence_match(normalized_query, compact_value):
                tier = min(tier, 3)
                continue
            if any(_bounded_damerau_levenshtein(clean_query, token, max_distance=1) <= 1 for token in value.split()):
                tier = min(tier, 4)
        if tier < 99:
            ranked.append((tier, code, candidate))
    ranked.sort()
    return [candidate for _tier, _code, candidate in ranked[:8]]

def _repair_generated_reference(mode_value: str, row: dict[str, Any], *, prefix: str = "") -> str:
    clean_prefix = normalize_text(prefix).strip() or DEFAULT_REF_PREFIX
    parts = [
        clean_prefix,
        SvlFixJeDashboardPage._repair_target_mode_label(mode_value),
        normalize_text(row.get("base_reference")) or normalize_text(row.get("item_code")),
    ]
    return " ".join(part for part in parts if part).strip()

def _repair_generated_line_label(mode_value: str, row: dict[str, Any], *, prefix: str = "") -> str:
    clean_prefix = normalize_text(prefix).strip() or DEFAULT_REF_PREFIX
    parts = [
        clean_prefix,
        SvlFixJeDashboardPage._repair_target_mode_label(mode_value),
        normalize_text(row.get("base_line_label")) or " ".join(
            part for part in (normalize_text(row.get("item_code")), normalize_text(row.get("item_name"))) if part
        ),
    ]
    return " ".join(part for part in parts if part).strip()

def _pcb_case_label(case_value: str | None = None, *, row: dict[str, Any] | None = None) -> str:
    explicit_label = normalize_text(row.get("pcb_case_label")) if row else ""
    if explicit_label:
        return explicit_label
    normalized_case = normalize_text(case_value or (row.get("pcb_case") if row else "")).lower() or "case1"
    return normalize_text(SvlFixJeDashboardPage._PCB_PROBLEM_CASE_LABEL.get(normalized_case)) or "Purchase Cycle Balance"

def _pcb_case1_item_display_label(row: dict[str, Any]) -> str:
    return " - ".join(
        part
        for part in (
            normalize_text(row.get("item_code")),
            normalize_text(row.get("item_name")),
        )
        if part
    )

def _pcb_case1_generated_reference(row: dict[str, Any], *, prefix: str = "") -> str:
    clean_prefix = normalize_text(prefix).strip() or DEFAULT_REF_PREFIX
    reference_parts = [
        normalize_text(row.get("picking_name")),
        normalize_text(row.get("bill_name")),
        SvlFixJeDashboardPage._pcb_case1_item_display_label(row),
    ]
    reference_body = " / ".join(part for part in reference_parts if part)
    if reference_body:
        return f"{clean_prefix}: Purchase Cycle Balance: {reference_body}"
    return f"{clean_prefix}: Purchase Cycle Balance"

def _pcb_case1_generated_line_label(row: dict[str, Any]) -> str:
    return " - ".join(
        part
        for part in (
            normalize_text(row.get("item_code")),
            normalize_text(row.get("item_name")),
            SvlFixJeDashboardPage._pcb_case_label(row=row),
        )
        if part
    )

def _pcb_row_uses_planned_lines(row: dict[str, Any] | None) -> bool:
    pcb_case = normalize_text((row or {}).get("pcb_case")).lower()
    return pcb_case not in {"", "case1"}

def _pcb_is_resolve_account_editable(row: dict[str, Any] | None) -> bool:
    if not SvlFixJeDashboardPage._pcb_row_uses_planned_lines(row):
        return False
    row_status = normalize_text((row or {}).get("row_status")).lower()
    return row_status in {"needs_review", "incomplete"}

def _pcb_missing_expense_guard_message(message: Any) -> bool:
    clean_message = normalize_text(message).lower()
    return "akun hpp/expense item dari kategori tidak ditemukan" in clean_message

def _pcb_local_account_name_map(row: dict[str, Any]) -> dict[str, str]:
    name_by_code = {
        account_code: account_name
        for account_code, (account_name, _source_name, _role_name) in SvlFixJeDashboardPage._PCB_SIMULATION_FIXED_ACCOUNTS.items()
    }
    name_by_code.update(
        {
        normalize_text(code).upper(): normalize_text(name)
        for code, name in dict(row.get("account_name_by_code") or {}).items()
        if normalize_text(code)
        }
    )
    for planned_line in list(row.get("planned_lines") or []):
        if isinstance(planned_line, dict):
            line_mapping = planned_line
        else:
            line_mapping = {
                field_name: getattr(planned_line, field_name)
                for field_name in getattr(planned_line, "__dataclass_fields__", {})
            }
        account_code = normalize_text(line_mapping.get("account_code")).upper()
        if not account_code:
            continue
        account_name = normalize_text(line_mapping.get("account_name"))
        if account_name and not name_by_code.get(account_code):
            name_by_code[account_code] = account_name
    return name_by_code

def _pcb_fixed_account_candidates(cls) -> list[SvlDashboardRepairAccountCandidate]:
    return [
        SvlDashboardRepairAccountCandidate(
            code=account_code,
            name=account_name,
            source=source_name,
            role=role_name,
        )
        for account_code, (account_name, source_name, role_name) in cls._PCB_SIMULATION_FIXED_ACCOUNTS.items()
    ]

def _pcb_cycle_problem_account_candidates(cls, cycle: Any) -> list[SvlDashboardRepairAccountCandidate]:
    raw_candidates: list[SvlDashboardRepairAccountCandidate] = []
    for cycle_item_row in list(getattr(cycle, "item_rows", None) or []):
        item_label = " / ".join(
            part
            for part in (
                normalize_text(getattr(cycle_item_row, "default_code", "")),
                normalize_text(getattr(cycle_item_row, "product_name", "")),
            )
            if part
        ) or normalize_text(getattr(cycle_item_row, "product_name", "")) or "Item Cycle"
        for account_row in list(getattr(cycle_item_row, "account_rows", None) or []):
            if normalize_text(getattr(account_row, "status", "")).lower() != "problem":
                continue
            account_code = normalize_text(getattr(account_row, "code", "")).upper()
            if not account_code:
                continue
            raw_candidates.append(
                SvlDashboardRepairAccountCandidate(
                    code=account_code,
                    name=normalize_text(getattr(account_row, "name", "")) or account_code,
                    source=f"PCB Cycle Problem - {item_label}",
                    role="other",
                )
            )
    if raw_candidates:
        return cls._dedupe_repair_account_candidates(raw_candidates)
    for account_row in list(getattr(cycle, "account_rows", None) or []):
        if normalize_text(getattr(account_row, "status", "")).lower() != "problem":
            continue
        account_code = normalize_text(getattr(account_row, "code", "")).upper()
        if not account_code:
            continue
        raw_candidates.append(
            SvlDashboardRepairAccountCandidate(
                code=account_code,
                name=normalize_text(getattr(account_row, "name", "")) or account_code,
                source="PCB Cycle Problem",
                role="other",
            )
        )
    return cls._dedupe_repair_account_candidates(raw_candidates)

def _pcb_local_account_candidates_for_row(cls, row: dict[str, Any]) -> list[SvlDashboardRepairAccountCandidate]:
    name_by_code = cls._pcb_local_account_name_map(row)
    raw_candidates: list[SvlDashboardRepairAccountCandidate] = [*cls._pcb_fixed_account_candidates(), *list(row.get("account_candidates") or [])]

    def add_candidate(code: Any, *, source: str, role: str) -> None:
        clean_code = normalize_text(code).upper()
        if not clean_code:
            return
        raw_candidates.append(
            SvlDashboardRepairAccountCandidate(
                code=clean_code,
                name=name_by_code.get(clean_code, clean_code),
                source=source,
                role=role,
            )
        )

    add_candidate(row.get("suggested_expense_account_code") or row.get("expense_account_code"), source="PCB Expense Target", role="expense")
    add_candidate(row.get("inventory_account_code"), source="PCB Inventory", role="valuation")
    add_candidate(row.get("suspend_account_code"), source="PCB Suspend", role="other")
    for account_code in sorted(dict(row.get("problem_balances_by_code") or {})):
        add_candidate(account_code, source="PCB Problem", role="other")
    for account_code in sorted(dict(row.get("hpp_balances_by_code") or {})):
        add_candidate(account_code, source="PCB HPP", role="expense")
    for account_code in sorted(dict(row.get("bank_balances_by_code") or {})):
        add_candidate(account_code, source="PCB Bank", role="other")
    for planned_line in list(row.get("planned_lines") or []):
        if isinstance(planned_line, dict):
            line_mapping = planned_line
        else:
            line_mapping = {
                field_name: getattr(planned_line, field_name)
                for field_name in getattr(planned_line, "__dataclass_fields__", {})
            }
        account_code = normalize_text(line_mapping.get("account_code")).upper()
        if not account_code:
            continue
        role_text = normalize_text(line_mapping.get("role")).lower()
        if role_text in {"hpp_zero", "selisih_hpp", "cost_change_offset"}:
            candidate_role = "expense"
            source_text = "PCB Planned Target"
        elif role_text.startswith("hpp_zero_"):
            candidate_role = "expense"
            source_text = "PCB Planned HPP"
        elif role_text.startswith("variance_zero_"):
            candidate_role = "expense"
            source_text = "PCB Planned Variance"
        elif role_text.startswith("audit_clearing_"):
            candidate_role = "other"
            source_text = "PCB Audit Clearing"
        elif role_text.startswith("problem_"):
            candidate_role = "other"
            source_text = "PCB Planned Problem"
        else:
            candidate_role = "other"
            source_text = "PCB Planned Line"
        add_candidate(account_code, source=source_text, role=candidate_role)
    return cls._dedupe_repair_account_candidates(raw_candidates)


def _pcb_simulation_account_candidates_for_row(
    self,
    row: dict[str, Any],
    *,
    cycle: Any = None,
    item_row: Any = None,
) -> list[SvlDashboardRepairAccountCandidate]:
    raw_candidates: list[SvlDashboardRepairAccountCandidate] = list(self._pcb_account_candidates_for_row(row))
    raw_candidates.extend(self._pcb_fixed_account_candidates())
    if cycle is not None:
        raw_candidates.extend(self._pcb_cycle_problem_account_candidates(cycle))
    candidate_item_rows = list(getattr(cycle, "item_rows", None) or []) if cycle is not None else []
    if item_row is not None:
        candidate_item_rows.append(item_row)
    for active_item_row in candidate_item_rows:
        item_label = " / ".join(
            part
            for part in (
                normalize_text(getattr(active_item_row, "default_code", "")),
                normalize_text(getattr(active_item_row, "product_name", "")),
            )
            if part
        ) or normalize_text(getattr(active_item_row, "product_name", "")) or "Item Aktif"
        for account_row in list(getattr(active_item_row, "account_rows", None) or []):
            if normalize_text(getattr(account_row, "status", "")).lower() != "problem":
                continue
            account_code = normalize_text(getattr(account_row, "code", "")).upper()
            if not account_code:
                continue
            raw_candidates.append(
                SvlDashboardRepairAccountCandidate(
                    code=account_code,
                    name=normalize_text(getattr(account_row, "name", "")) or account_code,
                    source=f"PCB Problem Item - {item_label}",
                    role="other",
                )
            )
    hpp_account_code = normalize_text(row.get("suggested_expense_account_code") or row.get("expense_account_code")).upper()
    if hpp_account_code:
        raw_candidates.append(
            SvlDashboardRepairAccountCandidate(
                code=hpp_account_code,
                name=self._pcb_lookup_account_name(row, hpp_account_code),
                source=f"HPP Kategori - {normalize_text(row.get('item_category_name')) or 'Item'}",
                role="expense",
            )
        )
    return self._dedupe_repair_account_candidates(raw_candidates)

def _pcb_effective_guard_flags(cls, row: dict[str, Any] | Any) -> list[str]:
    guard_values = row.get("guard_flags") if isinstance(row, dict) else getattr(row, "guard_flags", [])
    guard_flags = [normalize_text(value) for value in list(guard_values or []) if normalize_text(value)]
    case = row.get("pcb_case") if isinstance(row, dict) else getattr(row, "pcb_case", "")
    evidence = row.get("case_evidence", {}) if isinstance(row, dict) else getattr(row, "case_evidence", {})
    lines = row.get("planned_lines", []) if isinstance(row, dict) else getattr(row, "planned_lines", [])
    if normalize_text(case).lower() in {"case5", "case6"} and (
        dict(evidence or {}).get("flow") != "normal_receipt"
        or dict(evidence or {}).get("blockers")
        or any(normalize_text(line.get("account_code") if isinstance(line, dict) else getattr(line, "account_code", "")) == "1108099" for line in lines)
    ):
        if "receipt_recovery_blocked" not in guard_flags:
            guard_flags.append("receipt_recovery_blocked")
    expense_account_code = normalize_text(
        row.get("expense_account_code") if isinstance(row, dict) else getattr(row, "expense_account_code", "")
    ).upper()
    target_account_missing = (
        bool(row.get("planned_lines_target_account_missing"))
        if isinstance(row, dict)
        else bool(getattr(row, "planned_lines_target_account_missing", False))
    )
    if expense_account_code or not target_account_missing:
        guard_flags = [value for value in guard_flags if value != "missing_expense_account"]
    zero_guard = bool(row.get("planned_lines_zero_guard")) if isinstance(row, dict) else bool(getattr(row, "planned_lines_zero_guard", False))
    if zero_guard and "zero_planned_lines" not in guard_flags:
        guard_flags.append("zero_planned_lines")
    planned_diff = (
        float(row.get("planned_lines_balance_diff") or 0.0)
        if isinstance(row, dict)
        else float(getattr(row, "planned_lines_balance_diff", 0.0) or 0.0)
    )
    if abs(planned_diff) >= 0.01 and "unbalanced_planned_lines" not in guard_flags:
        guard_flags.append("unbalanced_planned_lines")
    return guard_flags

def _pcb_effective_guard_messages(cls, row: dict[str, Any] | Any) -> list[str]:
    message_values = row.get("guard_messages") if isinstance(row, dict) else getattr(row, "guard_messages", [])
    guard_messages = [normalize_text(value) for value in list(message_values or []) if normalize_text(value)]
    guard_flags = set(cls._pcb_effective_guard_flags(row))
    if "missing_expense_account" not in guard_flags:
        guard_messages = [value for value in guard_messages if not cls._pcb_missing_expense_guard_message(value)]
    projected_review_reason = normalize_text(
        row.get("projected_review_reason") if isinstance(row, dict) else getattr(row, "projected_review_reason", "")
    )
    if projected_review_reason and projected_review_reason not in guard_messages:
        guard_messages.append(projected_review_reason)
    planned_balance_guard = normalize_text(
        row.get("planned_lines_balance_guard") if isinstance(row, dict) else getattr(row, "planned_lines_balance_guard", "")
    )
    if planned_balance_guard and planned_balance_guard not in guard_messages:
        guard_messages.append(planned_balance_guard)
    return guard_messages


def _pcb_account_candidates_for_row(self, row: dict[str, Any]) -> list[SvlDashboardRepairAccountCandidate]:
    row["account_name_by_code"] = self._pcb_local_account_name_map(row)
    row["account_candidates"] = self._pcb_local_account_candidates_for_row(row)
    return self._repair_account_candidates_for_seed(row)


def _pcb_lookup_account_name(self, row: dict[str, Any], account_code: str) -> str:
    clean_code = normalize_text(account_code).upper()
    if not clean_code:
        return ""
    for candidate in self._pcb_account_candidates_for_row(row):
        if normalize_text(candidate.code).upper() == clean_code:
            return normalize_text(candidate.name) or clean_code
    return normalize_text(self._pcb_local_account_name_map(row).get(clean_code, "")) or clean_code


def _sync_pcb_case2_row_defaults(
    self,
    row: dict[str, Any],
    *,
    preserve_terminal: bool = False,
    cycle: Any = None,
    item_row: Any = None,
    reset_projected_review_confirmation: bool = True,
) -> None:
    if not self._pcb_row_uses_planned_lines(row):
        return
    row.setdefault("account_candidates", [])
    row.setdefault("account_name_by_code", {})
    row.setdefault("resolve_account_code", "")
    row.setdefault("resolve_account_preview", "Belum dipilih")
    row.setdefault("resolve_account_manual", False)
    row.setdefault("planned_lines_manual", False)
    row.setdefault("review_required_base", bool(row.get("review_required")))
    row.setdefault("review_reason_base", normalize_text(row.get("review_reason")))
    default_line_label = normalize_text(row.get("line_label"))
    base_planned_lines = self._map_pcb_case2_planned_lines(
        list(row.get("base_planned_lines") or row.get("planned_lines") or []),
        default_line_label=default_line_label,
    )
    current_planned_lines = self._map_pcb_case2_planned_lines(
        list(row.get("planned_lines") or []),
        default_line_label=default_line_label,
    )
    row["base_planned_lines"] = [dict(line) for line in base_planned_lines]
    if not current_planned_lines and base_planned_lines and not bool(row.get("planned_lines_manual")):
        current_planned_lines = [dict(line) for line in base_planned_lines]
    if "suggested_expense_account_code" in row:
        suggested_expense_code = normalize_text(row.get("suggested_expense_account_code")).upper()
    else:
        suggested_expense_code = normalize_text(row.get("expense_account_code")).upper()
    row["suggested_expense_account_code"] = suggested_expense_code
    active_candidates = self._pcb_account_candidates_for_row(row)
    resolve_account_code = normalize_text(row.get("resolve_account_code")).upper()
    resolve_manual = bool(row.get("resolve_account_manual"))
    if resolve_manual and resolve_account_code and suggested_expense_code and resolve_account_code == suggested_expense_code:
        resolve_manual = False
        row["resolve_account_manual"] = False
    if not resolve_manual:
        resolve_account_code = suggested_expense_code
        row["resolve_account_code"] = resolve_account_code
    row["expense_account_code"] = resolve_account_code
    row["resolve_account_preview"] = self._lookup_repair_candidate_preview(
        resolve_account_code,
        active_candidates,
        compact=True,
    )
    resolve_account_name = self._pcb_lookup_account_name(row, resolve_account_code)
    if resolve_account_code and resolve_account_name:
        account_name_by_code = {
            normalize_text(code).upper(): normalize_text(name)
            for code, name in dict(row.get("account_name_by_code") or {}).items()
            if normalize_text(code)
        }
        account_name_by_code[resolve_account_code] = resolve_account_name
        row["account_name_by_code"] = account_name_by_code
    normalized_planned_lines: list[dict[str, Any]] = []
    for planned_line in current_planned_lines:
        if isinstance(planned_line, dict):
            line_mapping = dict(planned_line)
        else:
            line_mapping = {
                field_name: getattr(planned_line, field_name)
                for field_name in getattr(planned_line, "__dataclass_fields__", {})
            }
        role_text = normalize_text(line_mapping.get("role")).lower()
        if role_text in {"hpp_zero", "selisih_hpp", "cost_change_offset"} and not bool(line_mapping.get("manual_account_override")):
            line_mapping["account_code"] = resolve_account_code
            line_mapping["account_name"] = resolve_account_name
        normalized_planned_lines.append(line_mapping)
    row["planned_lines"] = normalized_planned_lines
    row["planned_lines_target_account_missing"] = any(
        normalize_text(line.get("role")).lower() in {"hpp_zero", "selisih_hpp", "cost_change_offset"}
        and not normalize_text(line.get("account_code")).upper()
        for line in normalized_planned_lines
    )
    signed_total = self._pcb_planned_lines_signed_total(normalized_planned_lines)
    row["planned_lines_zero_guard"] = not bool(normalized_planned_lines)
    row["planned_lines_balance_diff"] = signed_total
    row["planned_lines_balance_guard"] = (
        "Jurnal simulasi belum memiliki line."
        if row["planned_lines_zero_guard"]
        else (f"Jurnal simulasi belum balance {signed_total:+,.2f}." if abs(signed_total) >= 0.01 else "")
    )
    self._recompute_pcb_projected_review_state(
        row,
        cycle=cycle,
        item_row=item_row,
        reset_review_confirmation=reset_projected_review_confirmation,
    )
    self._refresh_pcb_case2_collection_row_state(row, preserve_terminal=preserve_terminal)

def _pcb_je_preview_text(cls, row: dict[str, Any]) -> str:
    planned_lines = list(row.get("planned_lines") or [])
    if planned_lines:
        preview_parts: list[str] = []
        for line in planned_lines[:3]:
            if isinstance(line, dict):
                line_mapping = line
            else:
                line_mapping = {
                    field_name: getattr(line, field_name)
                    for field_name in getattr(line, "__dataclass_fields__", {})
                }
            side = normalize_text(line_mapping.get("side")).lower()
            amount = abs(float(line_mapping.get("amount") or 0.0))
            if side not in {"debit", "credit"} or amount <= 0.0:
                continue
            preview_parts.append(
                f"{'DR' if side == 'debit' else 'CR'} {normalize_text(line_mapping.get('account_code')) or '?'} {amount:,.2f}"
            )
        if preview_parts:
            return " | ".join(preview_parts)
    case1_lines = cls._pcb_case1_simulated_journal_lines(row)
    if case1_lines:
        return " | ".join(
            f"{normalize_text(line.get('side_label'))} {normalize_text(line.get('account_code')) or '?'} {abs(float(line.get('amount') or 0.0)):,.2f}"
            for line in case1_lines[:3]
        )
    debit_code = normalize_text(row.get("debit_account_code"))
    credit_code = normalize_text(row.get("credit_account_code"))
    if debit_code or credit_code:
        return f"{debit_code or '?'} / {credit_code or '?'}"
    return "-"

def _pcb_case2_planned_line_role_text(role: Any) -> str:
    normalized_role = normalize_text(role).lower()
    recovery_labels = {"receipt_inventory": "Pemulihan Persediaan", "receipt_counterpart": "Counterpart Penerimaan", "cost_change_offset": "Offset Perubahan Cost", "cost_counterpart": "Sisa Suspense Bill"}
    if normalized_role in recovery_labels:
        return recovery_labels[normalized_role]
    if normalized_role.startswith("audit_clearing_"):
        return "Audit Clearing"
    if normalized_role.startswith("case8_"):
        return "Case 8 Return"
    if normalized_role.startswith("case9_"):
        return "Case 9 UoM"
    if normalized_role.startswith("variance_zero_"):
        return "Zero Selisih HPP"
    if normalized_role in {"manual_simulation", "manual_adjustment"}:
        return "Manual Simulasi"
    if normalized_role == "hpp_zero" or normalized_role.startswith("hpp_zero_"):
        return "Zero HPP"
    if normalized_role == "selisih_hpp":
        return "Selisih HPP"
    if normalized_role.startswith("problem_"):
        account_code = normalized_role.split("_", 1)[1]
        return f"problem_{account_code}"
    return normalize_text(role) or "?"

def _pcb_detail_amount(value: Any) -> float:
    try:
        return round(float(value or 0.0), 2)
    except (TypeError, ValueError):
        return 0.0

def _pcb_planned_lines_signed_total(cls, planned_lines: list[Any]) -> float:
    signed_total = 0.0
    for planned_line in list(planned_lines or []):
        if isinstance(planned_line, dict):
            line_mapping = planned_line
        else:
            line_mapping = {
                field_name: getattr(planned_line, field_name)
                for field_name in getattr(planned_line, "__dataclass_fields__", {})
            }
        amount = abs(cls._pcb_detail_amount(line_mapping.get("amount")))
        side = normalize_text(line_mapping.get("side")).lower()
        if amount <= 0.0 or side not in {"debit", "credit"}:
            continue
        signed_total += amount if side == "debit" else -amount
    return cls._pcb_detail_amount(signed_total)


def _pcb_problem_code_set(self) -> set[str]:
    raw_value = (
        normalize_text(self._pcb_problem_codes_var.get())
        if hasattr(self, "_pcb_problem_codes_var")
        else normalize_text(getattr(getattr(self, "_module_settings", None), "pcb_problem_codes", "2103006,1108099"))
    )
    return {normalize_text(code).upper() for code in raw_value.split(",") if normalize_text(code)}


def _pcb_info_code_set(self) -> set[str]:
    raw_value = (
        normalize_text(self._pcb_info_codes_var.get())
        if hasattr(self, "_pcb_info_codes_var")
        else normalize_text(getattr(getattr(self, "_module_settings", None), "pcb_info_codes", "11120003"))
    )
    return {normalize_text(code).upper() for code in raw_value.split(",") if normalize_text(code)}


def _pcb_detail_account_status(self, account_code: Any, net_balance: Any) -> str:
    balance = self._pcb_detail_amount(net_balance)
    clean_code = normalize_text(account_code).upper()
    if abs(balance) < 1.0:
        return "balanced"
    if clean_code in self._pcb_problem_code_set():
        return "problem"
    if clean_code in self._pcb_info_code_set():
        return "info"
    return "acceptable"

def _pcb_cycle_key_picking_id(cycle_key: Any) -> int:
    clean_key = normalize_text(cycle_key)
    if not clean_key:
        return 0
    parts = clean_key.split("::")
    if len(parts) < 2:
        return 0
    try:
        return int(parts[1] or 0)
    except (TypeError, ValueError):
        return 0


def _pcb_detail_cycle_for_row(self, row: dict[str, Any]) -> Any:
    snapshot = getattr(self, "_latest_snapshot", None)
    cycles = list(getattr(snapshot, "purchase_cycles", None) or []) if snapshot else []
    if not cycles:
        return None
    clean_cycle_key = normalize_text(row.get("cycle_key"))
    target_picking_id = self._pcb_cycle_key_picking_id(clean_cycle_key)
    if target_picking_id > 0:
        for cycle in cycles:
            if int(getattr(cycle, "picking_id", 0) or 0) == target_picking_id:
                return cycle
    if clean_cycle_key:
        for cycle in cycles:
            expected_key = f"{self._classify_pcb_cycle_case(cycle)}::{int(getattr(cycle, 'picking_id', 0) or 0)}"
            if clean_cycle_key == normalize_text(expected_key):
                return cycle
        for cycle in cycles:
            cycle_keys = {
                normalize_text(seed.get("cycle_key"))
                for seed in self._build_pcb_seeds_for_cycle(cycle)
                if normalize_text(seed.get("cycle_key"))
            }
            if clean_cycle_key in cycle_keys:
                return cycle
    return None

def _pcb_detail_item_row_for_row(row: dict[str, Any], cycle: Any) -> Any:
    item_rows = list(getattr(cycle, "item_rows", None) or [])
    if not item_rows:
        return None
    target_product_id = int(row.get("product_id") or 0)
    if target_product_id > 0:
        for item_row in item_rows:
            if int(getattr(item_row, "product_id", 0) or 0) == target_product_id:
                return item_row
    target_item_code = normalize_text(row.get("item_code")).upper()
    if target_item_code:
        for item_row in item_rows:
            if normalize_text(getattr(item_row, "default_code", "")).upper() == target_item_code:
                return item_row
    target_item_name = normalize_text(row.get("item_name"))
    if target_item_name:
        for item_row in item_rows:
            if normalize_text(getattr(item_row, "product_name", "")) == target_item_name:
                return item_row
    if len(item_rows) == 1:
        return item_rows[0]
    return None

def _pcb_detail_account_name_map(cls, row: dict[str, Any], cycle: Any = None, item_row: Any = None) -> dict[str, str]:
    name_by_code: dict[str, str] = {}
    for account_row in list(getattr(item_row, "account_rows", None) or []):
        account_code = normalize_text(getattr(account_row, "code", "")).upper()
        account_name = normalize_text(getattr(account_row, "name", ""))
        if account_code and account_name and not name_by_code.get(account_code):
            name_by_code[account_code] = account_name
    for account_row in list(getattr(cycle, "account_rows", None) or []):
        account_code = normalize_text(getattr(account_row, "code", "")).upper()
        account_name = normalize_text(getattr(account_row, "name", ""))
        if account_code and account_name and not name_by_code.get(account_code):
            name_by_code[account_code] = account_name
    for account_code, account_name in cls._pcb_local_account_name_map(row).items():
        if account_code and account_name and not name_by_code.get(account_code):
            name_by_code[account_code] = account_name
    return name_by_code

def _pcb_case1_simulation_keterangan(cls, row: dict[str, Any]) -> str:
    item_label = cls._pcb_case1_item_display_label(row)
    if not item_label:
        item_label = normalize_text(row.get("item_name"))
    if not item_label:
        product_id = int(row.get("product_id") or 0)
        item_label = f"Product #{product_id}" if product_id > 0 else "Purchase Cycle Balance"
    parts = [f"Correction {item_label}"]
    line_label = normalize_text(row.get("line_label"))
    if line_label:
        parts.append(line_label)
    return " | ".join(part for part in parts if part)

def _pcb_case1_simulated_journal_lines(
    cls,
    row: dict[str, Any],
    *,
    account_name_by_code: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    name_by_code = {
        normalize_text(code).upper(): normalize_text(name)
        for code, name in dict(account_name_by_code or {}).items()
        if normalize_text(code)
    }
    diff_account_code = normalize_text(row.get("diff_account_code")).upper()
    diff_account_name = normalize_text(row.get("diff_account_name"))
    if diff_account_code and diff_account_name and not name_by_code.get(diff_account_code):
        name_by_code[diff_account_code] = diff_account_name
    debit_amount = abs(cls._pcb_detail_amount(row.get("debit_amount") or row.get("amount")))
    credit_amount = abs(cls._pcb_detail_amount(row.get("credit_amount") or row.get("amount")))
    diff_amount = abs(cls._pcb_detail_amount(row.get("diff_amount")))
    diff_side = normalize_text(row.get("diff_side")).lower()
    line_specs: list[tuple[str, str, float]] = []
    if debit_amount > 0.0:
        line_specs.append(("debit", normalize_text(row.get("debit_account_code")).upper() or "?", debit_amount))
    if diff_amount >= 0.01 and diff_side == "debit":
        line_specs.append(("debit", diff_account_code or "?", diff_amount))
    if credit_amount > 0.0:
        line_specs.append(("credit", normalize_text(row.get("credit_account_code")).upper() or "?", credit_amount))
    if diff_amount >= 0.01 and diff_side == "credit":
        line_specs.append(("credit", diff_account_code or "?", diff_amount))
    keterangan = cls._pcb_case1_simulation_keterangan(row)
    simulated_lines: list[dict[str, Any]] = []
    for line_index, (side, account_code, amount) in enumerate(line_specs):
        account_name = name_by_code.get(account_code, "") or ("Belum dipilih" if account_code == "?" else account_code)
        simulated_lines.append(
            {
                "line_index": line_index,
                "side": side,
                "side_label": "DR" if side == "debit" else "CR",
                "account_code": account_code,
                "account_name": account_name,
                "amount": amount,
                "signed_amount": amount if side == "debit" else -amount,
                "keterangan": keterangan,
            }
        )
    return simulated_lines

def _pcb_planned_line_simulation_keterangan(cls, row: dict[str, Any], line_mapping: dict[str, Any]) -> str:
    normalized_role = normalize_text(line_mapping.get("role")).lower()
    line_label = normalize_pcb_planned_line_label(
        role=line_mapping.get("role"),
        line_label=line_mapping.get("line_label"),
        default_line_label=normalize_text(row.get("line_label")),
    )
    if normalized_role == "selisih_hpp" and line_label:
        return line_label
    if normalized_role.startswith("problem_"):
        account_code = normalize_text(line_mapping.get("account_code")).upper() or normalized_role.split("_", 1)[1]
        role_label = f"Saldo Problem {account_code}".strip()
    else:
        role_label = cls._pcb_case2_planned_line_role_text(line_mapping.get("role"))
    return " | ".join(part for part in (role_label, line_label) if part)


def _pcb_simulated_journal_lines(self, row: dict[str, Any], *, cycle: Any = None, item_row: Any = None) -> list[dict[str, Any]]:
    account_name_by_code = self._pcb_detail_account_name_map(row, cycle=cycle, item_row=item_row)
    simulated_lines: list[dict[str, Any]] = []
    if self._pcb_row_uses_planned_lines(row):
        planned_lines = self._map_pcb_case2_planned_lines(
            list(row.get("planned_lines") or []),
            default_line_label=normalize_text(row.get("line_label")),
        )
        for line_mapping in planned_lines:
            side = normalize_text(line_mapping.get("side")).lower()
            amount = abs(self._pcb_detail_amount(line_mapping.get("amount")))
            if side not in {"debit", "credit"} or amount <= 0.0:
                continue
            account_code = normalize_text(line_mapping.get("account_code")).upper() or "?"
            account_name = (
                normalize_text(line_mapping.get("account_name"))
                or account_name_by_code.get(account_code, "")
                or ("Belum dipilih" if account_code == "?" else account_code)
            )
            simulated_lines.append(
                {
                    "line_index": len(simulated_lines),
                    "role": normalize_text(line_mapping.get("role")),
                    "line_label": normalize_text(line_mapping.get("line_label")),
                    "side": side,
                    "side_label": "DR" if side == "debit" else "CR",
                    "account_code": account_code,
                    "account_name": account_name,
                    "amount": amount,
                    "signed_amount": amount if side == "debit" else -amount,
                    "keterangan": self._pcb_planned_line_simulation_keterangan(row, line_mapping),
                }
            )
        return simulated_lines

    return self._pcb_case1_simulated_journal_lines(
        row,
        account_name_by_code=account_name_by_code,
    )


def _pcb_projected_cycle_account_rows(
    self,
    row: dict[str, Any],
    *,
    item_row: Any,
    simulated_lines: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    simulated_lines = list(simulated_lines or [])
    delta_by_code: dict[str, float] = {}
    extra_name_by_code: dict[str, str] = {}
    has_unresolved_account = False
    for line in simulated_lines:
        account_code = normalize_text(line.get("account_code")).upper() or "?"
        signed_amount = self._pcb_detail_amount(line.get("signed_amount"))
        if account_code == "?":
            has_unresolved_account = True
        delta_by_code[account_code] = self._pcb_detail_amount(delta_by_code.get(account_code, 0.0) + signed_amount)
        account_name = normalize_text(line.get("account_name"))
        if account_name and not extra_name_by_code.get(account_code):
            extra_name_by_code[account_code] = account_name

    projected_rows: list[dict[str, Any]] = []
    seen_codes: set[str] = set()
    for account_row in list(getattr(item_row, "account_rows", None) or []):
        account_code = normalize_text(getattr(account_row, "code", "")).upper()
        if not account_code:
            continue
        current_balance = self._pcb_detail_amount(getattr(account_row, "net_balance", 0.0))
        simulated_delta = self._pcb_detail_amount(delta_by_code.get(account_code, 0.0))
        projected_balance = self._pcb_detail_amount(current_balance + simulated_delta)
        projected_rows.append(
            {
                "status": self._pcb_detail_account_status(account_code, projected_balance),
                "code": account_code,
                "name": normalize_text(getattr(account_row, "name", "")) or extra_name_by_code.get(account_code, "") or account_code,
                "current_balance": current_balance,
                "simulated_delta": simulated_delta,
                "projected_balance": projected_balance,
            }
        )
        seen_codes.add(account_code)

    for account_code, simulated_delta in delta_by_code.items():
        if account_code in seen_codes:
            continue
        projected_rows.append(
            {
                "status": self._pcb_detail_account_status(account_code, simulated_delta),
                "code": account_code,
                "name": extra_name_by_code.get(account_code, "") or ("Belum dipilih" if account_code == "?" else account_code),
                "current_balance": 0.0,
                "simulated_delta": self._pcb_detail_amount(simulated_delta),
                "projected_balance": self._pcb_detail_amount(simulated_delta),
            }
        )
    return projected_rows, has_unresolved_account

def _combine_unique_text_parts(*parts: str) -> str:
    ordered_parts: list[str] = []
    for part in parts:
        clean_part = normalize_text(part)
        if clean_part and clean_part not in ordered_parts:
            ordered_parts.append(clean_part)
    return " | ".join(ordered_parts)

def _pcb_projected_review_reason_text(problem_rows: list[dict[str, Any]]) -> str:
    if not problem_rows:
        return ""
    problem_parts = [
        f"{normalize_text(problem_row.get('code')).upper()} {float(problem_row.get('projected_balance') or 0.0):+,.2f}"
        for problem_row in sorted(
            problem_rows,
            key=lambda value: normalize_text(value.get("code")).upper(),
        )
        if normalize_text(problem_row.get("code"))
    ]
    if not problem_parts:
        return ""
    return (
        "Proyeksi Jurnal Per Item masih menyisakan akun problem: "
        + ", ".join(problem_parts)
        + "."
    )

def _recompute_pcb_projected_review_state(
    self,
    row: dict[str, Any],
    *,
    cycle: Any = None,
    item_row: Any = None,
    simulated_lines: list[dict[str, Any]] | None = None,
    reset_review_confirmation: bool = True,
) -> None:
    if not self._pcb_row_uses_planned_lines(row):
        return
    row.setdefault("review_required_base", bool(row.get("review_required")))
    row.setdefault("review_reason_base", normalize_text(row.get("review_reason")))
    projected_required = bool(row.get("review_required_projected"))
    projected_reason = normalize_text(row.get("projected_review_reason"))
    projected_problem_codes = [
        normalize_text(value).upper()
        for value in list(row.get("projected_problem_codes") or [])
        if normalize_text(value)
    ]
    resolved_cycle = cycle if cycle is not None else self._pcb_detail_cycle_for_row(row)
    resolved_item_row = item_row
    if resolved_cycle is not None and resolved_item_row is None:
        resolved_item_row = self._pcb_detail_item_row_for_row(row, resolved_cycle)
    if resolved_cycle is not None and resolved_item_row is not None:
        active_simulated_lines = (
            list(simulated_lines)
            if simulated_lines is not None
            else self._pcb_simulated_journal_lines(row, cycle=resolved_cycle, item_row=resolved_item_row)
        )
        projected_rows, _has_unresolved = self._pcb_projected_cycle_account_rows(
            row,
            item_row=resolved_item_row,
            simulated_lines=active_simulated_lines,
        )
        projected_problem_rows = [
            projected_row
            for projected_row in projected_rows
            if normalize_text(projected_row.get("status")).lower() == "problem"
        ]
        projected_required = bool(projected_problem_rows)
        projected_reason = (
            self._pcb_projected_review_reason_text(projected_problem_rows)
            if projected_required
            else ""
        )
        projected_problem_codes = [
            normalize_text(projected_row.get("code")).upper()
            for projected_row in sorted(
                projected_problem_rows,
                key=lambda value: normalize_text(value.get("code")).upper(),
            )
            if normalize_text(projected_row.get("code"))
        ]
        if projected_required and reset_review_confirmation:
            row["review_confirmed"] = False
    row["review_required_projected"] = projected_required
    row["projected_review_reason"] = projected_reason
    row["projected_problem_codes"] = projected_problem_codes
    base_required = bool(row.get("review_required_base"))
    base_reason = normalize_text(row.get("review_reason_base"))
    row["review_required"] = bool(base_required or projected_required)
    row["review_reason"] = self._combine_unique_text_parts(base_reason, projected_reason)

def _pcb_guard_summary_text(row: dict[str, Any]) -> str:
    guard_messages = SvlFixJeDashboardPage._pcb_effective_guard_messages(row)
    if guard_messages:
        return build_compact_preview_text(" | ".join(guard_messages), empty_text="-", max_chars=68)
    coefficient_variance = float(row.get("coefficient_variance") or 0.0)
    if coefficient_variance > 35.0:
        return f"Coeff {coefficient_variance:,.2f}%"
    return "-"

def _pcb_review_required(row: dict[str, Any] | Any) -> bool:
    if isinstance(row, dict):
        return bool(row.get("review_required"))
    return bool(getattr(row, "review_required", False))

def _pcb_review_confirmed(row: dict[str, Any] | Any) -> bool:
    if isinstance(row, dict):
        return bool(row.get("review_confirmed"))
    return bool(getattr(row, "review_confirmed", False))

def _pcb_review_reason(row: dict[str, Any] | Any) -> str:
    if isinstance(row, dict):
        return normalize_text(row.get("review_reason"))
    return normalize_text(getattr(row, "review_reason", ""))

def _pcb_review_status_text(cls, row: dict[str, Any] | Any) -> str:
    if not cls._pcb_review_required(row):
        return "No"
    if cls._pcb_review_confirmed(row):
        return "Confirmed"
    return "Required"

def _pcb_row_has_blocking_guard(row: dict[str, Any] | Any) -> bool:
    guard_flags = {
        normalize_text(value)
        for value in SvlFixJeDashboardPage._pcb_effective_guard_flags(row)
        if normalize_text(value)
    }
    return bool({"missing_expense_account", "zero_planned_lines", "unbalanced_planned_lines", "receipt_recovery_blocked"} & guard_flags)

def _refresh_pcb_case2_collection_row_state(
    cls,
    row: dict[str, Any],
    *,
    preserve_terminal: bool = False,
) -> None:
    current_state = normalize_text(row.get("row_status")).lower()
    if preserve_terminal and current_state in {"running", "repaired", "error"}:
        return
    guard_messages = cls._pcb_effective_guard_messages(row)
    if cls._pcb_row_has_blocking_guard(row):
        blocking_message = normalize_text(
            row.get("planned_lines_balance_guard") if isinstance(row, dict) else getattr(row, "planned_lines_balance_guard", "")
        )
        row["row_status"] = "incomplete"
        row["row_status_message"] = blocking_message or next((message for message in guard_messages if message), "Belum siap dijalankan.")
        return
    if cls._pcb_review_required(row) and not cls._pcb_review_confirmed(row):
        row["row_status"] = "needs_review"
        row["row_status_message"] = (
            cls._pcb_review_reason(row)
            or next((message for message in guard_messages if message), "")
            or "Row ini wajib direview manual sebelum execute."
        )
        return
    row["row_status"] = "ready"
    row["row_status_message"] = "Review confirmed" if cls._pcb_review_required(row) else "Ready"

def _suggest_repair_account_codes(
    seed: dict[str, Any],
    candidates: list[SvlDashboardRepairAccountCandidate],
) -> tuple[str, str]:
    signed_amount = float(seed.get("signed_amount") or seed.get("amount") or 0.0)
    valuation_candidate = next(
        (
            candidate
            for candidate in candidates
            if SvlFixJeDashboardPage._normalized_repair_candidate_role(candidate) == "valuation"
        ),
        None,
    )
    extra_candidate = next(
        (candidate for candidate in candidates if "global extra" in normalize_text(candidate.source).lower()),
        None,
    )
    valuation_code = normalize_text(valuation_candidate.code if valuation_candidate is not None else "")
    extra_code = normalize_text(extra_candidate.code if extra_candidate is not None else "")
    if signed_amount < 0:
        return extra_code, valuation_code
    return valuation_code, extra_code

def _repair_candidate_preview_label(
    candidate: SvlDashboardRepairAccountCandidate,
    *,
    compact: bool = False,
) -> str:
    clean_code = normalize_text(candidate.code).upper()
    clean_name = normalize_text(candidate.name)
    if compact:
        if clean_code and clean_name and clean_name != clean_code:
            return f"{clean_code} - {clean_name}"
        return clean_code or clean_name or "-"
    return candidate.display_label

def _lookup_repair_candidate_preview(
    cls,
    code: str,
    candidates: list[SvlDashboardRepairAccountCandidate],
    *,
    compact: bool = False,
) -> str:
    clean_code = normalize_text(code).upper()
    if not clean_code:
        return "Belum dipilih"
    for candidate in candidates:
        if normalize_text(candidate.code).upper() == clean_code:
            return cls._repair_candidate_preview_label(candidate, compact=compact)
    if compact:
        return f"{clean_code} - Manual entry"
    return f"{clean_code} - Manual entry (akan resolve exact saat check/run)"

def _build_collection_repair_draft(seed: dict[str, Any]) -> dict[str, Any]:
    draft = dict(seed)
    draft["date"] = SvlFixJeDashboardPage._today_text()
    draft["target_mode"] = ""
    draft["posting_mode"] = ""
    draft["target_account_role"] = ""
    draft["target_account_code"] = ""
    draft["target_account_preview"] = "Belum dipilih"
    draft["resolve_account_code"] = ""
    draft["resolve_account_preview"] = "Belum dipilih"
    draft["debit_account_code"] = ""
    draft["credit_account_code"] = ""
    draft["debit_account_preview"] = "Belum dipilih"
    draft["credit_account_preview"] = "Belum dipilih"
    draft["reference"] = ""
    draft["line_label"] = ""
    draft["reference_generated"] = False
    draft["line_label_generated"] = False
    draft["row_status"] = "incomplete"
    draft["row_status_message"] = ""
    return draft


def _merge_seed_into_repair_draft(self, draft: dict[str, Any], seed: dict[str, Any]) -> None:
    for key in (
        "company_id",
        "company_name",
        "item_product_id",
        "item_code",
        "item_name",
        "item_category_name",
        "amount",
        "signed_amount",
        "base_reference",
        "base_line_label",
        "journal_code",
        "svl_id",
        "svl_date",
        "svl_qty",
        "svl_unit_cost",
        "svl_value",
        "svl_reference",
        "move_id",
        "move_name",
        "move_state",
        "account_candidates",
        "latest_snapshot_status",
        "repair_source_kind",
        "repair_source_label",
    ):
        if key == "journal_code" and normalize_text(draft.get("journal_code")):
            continue
        draft[key] = seed.get(key)
    if not normalize_text(draft.get("date")):
        draft["date"] = self._today_text()


def _repair_default_settings_payload(self) -> dict[str, str]:
    state_store = getattr(self, "_state_store", None)
    latest = state_store.load() if state_store is not None else getattr(self, "_module_settings", None)
    return {
        "target_mode": normalize_text(getattr(latest, "dashboard_repair_last_target_mode", "")),
        "posting_mode": normalize_text(getattr(latest, "dashboard_repair_last_posting_mode", "")),
        "target_account_role": self._normalize_repair_target_account_role(
            normalize_text(getattr(latest, "dashboard_repair_last_target_account_role", ""))
        ),
        "resolve_account_code": normalize_text(getattr(latest, "dashboard_repair_last_resolve_account_code", "")).upper(),
    }


def _save_repair_default_settings(
    self,
    *,
    target_mode: str,
    posting_mode: str,
    target_account_role: str,
    resolve_account_code: str,
) -> None:
    state_store = getattr(self, "_state_store", None)
    if state_store is None:
        return
    latest = state_store.load()
    latest.dashboard_repair_last_target_mode = normalize_text(target_mode)
    latest.dashboard_repair_last_posting_mode = normalize_text(posting_mode)
    latest.dashboard_repair_last_target_account_role = self._normalize_repair_target_account_role(
        normalize_text(target_account_role)
    )
    latest.dashboard_repair_last_resolve_account_code = normalize_text(resolve_account_code).upper()
    self._module_settings = latest
    state_store.save(latest)

def _selected_repairable_meta(self, tree: ttk.Treeview) -> list[dict[str, Any]]:
    meta_map = self._tree_row_meta.get(tree, {})
    return [
        meta
        for meta in (meta_map.get(item_id, {}) for item_id in self._selected_tree_leaf_item_ids(tree))
        if meta.get("repair_candidate") and meta.get("repair_seed")
    ]


def _repair_candidates_for_tree(self, tree: ttk.Treeview) -> list[dict[str, Any]]:
    meta_map = self._tree_row_meta.get(tree, {})
    selected_meta = [meta_map.get(item_id, {}) for item_id in self._selected_tree_leaf_item_ids(tree)]
    if not selected_meta:
        return []
    candidates = [meta for meta in selected_meta if meta.get("repair_candidate") and meta.get("repair_seed")]
    if len(candidates) != len(selected_meta):
        return []
    return candidates


def _can_add_selected_to_repair_collection(self, tree: ttk.Treeview) -> bool:
    if not self._selected_repairable_meta(tree):
        return False
    return self._can_modify_repair_collection_from_current_scope()


def _can_remove_selected_from_repair_collection(self, tree: ttk.Treeview) -> bool:
    if not self._can_modify_repair_collection_from_current_scope():
        return False
    for meta in self._selected_repairable_meta(tree):
        seed = dict(meta.get("repair_seed") or {})
        if self.is_repair_row_collected(normalize_text(seed.get("row_key"))):
            return True
    return False


def _repair_collection_rows(self) -> list[dict[str, Any]]:
    return [entry.draft for entry in self._repair_collection.values()]


def _add_repair_collection_seeds(self, seeds: list[dict[str, Any]]) -> tuple[int, int]:
    valid_seeds = [dict(seed) for seed in seeds if normalize_text(seed.get("row_key"))]
    if not valid_seeds:
        return (0, 0)
    current_scope = self._current_repair_collection_scope()
    if current_scope is None:
        messagebox.showwarning(self._display_name, "Pilih company terlebih dahulu sebelum memakai Repair Collection.")
        return (0, 0)
    if self._repair_collection and self._repair_collection_scope is not None and not self._repair_collection_scope.matches(current_scope):
        current_scope_text = self._repair_collection_scope_text() or "-"
        messagebox.showwarning(
            self._display_name,
            f"Repair Collection terkunci di scope lain: {current_scope_text}.\n"
            "Kembali ke scope asal atau clear collection terlebih dahulu.",
        )
        return (0, 0)
    if not self._repair_collection or self._repair_collection_scope is None:
        self._repair_collection_scope = current_scope

    added = 0
    moved = 0
    for seed in valid_seeds:
        row_key = normalize_text(seed.get("row_key"))
        latest_status = normalize_text(seed.get("latest_snapshot_status"))
        self._repair_collection_sequence += 1
        if row_key in self._repair_collection:
            entry = self._repair_collection[row_key]
            entry.seed = dict(seed)
            entry.latest_snapshot_status = latest_status
            if normalize_text(entry.draft.get("row_status")).lower() == "repaired":
                entry.draft = self._build_collection_repair_draft(seed)
            else:
                self._merge_seed_into_repair_draft(entry.draft, seed)
                self._refresh_repair_row_state(entry.draft)
            entry.collected_at_order = self._repair_collection_sequence
            self._repair_collection.move_to_end(row_key, last=False)
            moved += 1
            continue
        self._repair_collection[row_key] = _RepairCollectionEntry(
            row_key=row_key,
            scope_database_profile_id=current_scope.database_profile_id,
            scope_company_id=current_scope.company_id,
            collected_at_order=self._repair_collection_sequence,
            latest_snapshot_status=latest_status,
            seed=dict(seed),
            draft=self._build_collection_repair_draft(seed),
        )
        self._repair_collection.move_to_end(row_key, last=False)
        added += 1
    self._notify_repair_collection_changed()
    return (added, moved)


def _remove_repair_collection_row_keys(self, row_keys: list[str]) -> int:
    removed = 0
    for row_key in row_keys:
        clean_row_key = normalize_text(row_key)
        if clean_row_key and clean_row_key in self._repair_collection:
            self._repair_collection.pop(clean_row_key, None)
            removed += 1
    self._reset_repair_collection_scope_if_empty()
    if removed:
        self._notify_repair_collection_changed()
    return removed


def add_selected_to_repair_collection(self, tree: ttk.Treeview) -> None:
    selected_meta = self._selected_repairable_meta(tree)
    if not selected_meta:
        self.status_var.set("Tidak ada row repairable yang bisa ditambahkan ke Repair Collection.")
        return
    added, moved = self._add_repair_collection_seeds([dict(meta.get("repair_seed") or {}) for meta in selected_meta])
    if added or moved:
        self.status_var.set(f"Repair Collection diperbarui. {added} row ditambahkan, {moved} row dipindah ke urutan teratas.")


def remove_selected_from_repair_collection(self, tree: ttk.Treeview) -> None:
    selected_meta = self._selected_repairable_meta(tree)
    if not selected_meta:
        self.status_var.set("Tidak ada row repairable yang bisa dikeluarkan dari Repair Collection.")
        return
    if self._repair_collection and not self._can_modify_repair_collection_from_current_scope():
        scope_text = self._repair_collection_scope_text() or "-"
        messagebox.showwarning(
            self._display_name,
            f"Repair Collection terkunci di scope lain: {scope_text}.\n"
            "Kembali ke scope asal atau gunakan Clear Collection dari header.",
        )
        return
    removed = self._remove_repair_collection_row_keys(
        [normalize_text(dict(meta.get("repair_seed") or {}).get("row_key")) for meta in selected_meta]
    )
    if removed:
        self.status_var.set(f"Repair Collection diperbarui. {removed} row dikeluarkan.")
    else:
        self.status_var.set("Row terpilih belum ada di Repair Collection.")


def has_latest_snapshot(self) -> bool:
    return getattr(self, "_latest_snapshot", None) is not None


def _collect_all_snapshot_rows(
    self,
    *,
    row_type: str,
    row_label: str,
    filter_automated_only: bool = False,
) -> tuple[int, int]:
    snapshot = getattr(self, "_latest_snapshot", None)
    if snapshot is None:
        messagebox.showwarning(self._display_name, "Belum ada hasil analisis. Jalankan Analyze terlebih dahulu.")
        return (0, 0)
    seeds: list[dict[str, Any]] = []
    skipped_non_automated = 0
    skipped_ineligible = 0
    for item in snapshot.items:
        if normalize_text(getattr(item, "item_kind", "product")) != "product":
            continue
        if filter_automated_only and not bool(getattr(item, "automated_valuation", True)):
            skipped_non_automated += 1
            continue
        for row in (getattr(item, "merged_records", []) or []):
            if normalize_text(getattr(row, "row_type", "")) != normalize_text(row_type):
                continue
            if not bool(getattr(row, "repair_candidate", False)):
                continue
            seed = self._build_repair_seed_from_merged_row(item, row)
            if seed is None:
                skipped_ineligible += 1
                continue
            seeds.append(seed)
    if not seeds:
        msg = f"Tidak ada transaksi '{row_label}' yang bisa di-collect dari snapshot saat ini."
        if filter_automated_only and skipped_non_automated > 0:
            msg += f"\n\n({skipped_non_automated} item dilewati karena kategori bukan Automated / real_time.)"
        if skipped_ineligible > 0:
            msg += f"\n\n({skipped_ineligible} row dilewati karena nominal repair tidak eligible.)"
        messagebox.showinfo(self._display_name, msg)
        return (0, 0)
    added, moved = self._add_repair_collection_seeds(seeds)
    if added or moved:
        msg = f"Bulk Collect selesai: {added} row baru ditambahkan, {moved} row diperbarui."
        if filter_automated_only and skipped_non_automated > 0:
            msg += f" ({skipped_non_automated} item non-Automated dilewati.)"
        if skipped_ineligible > 0:
            msg += f" ({skipped_ineligible} row tidak eligible dilewati.)"
        self.status_var.set(msg)
    return (added, moved)


def collect_all_linked_empty_je(self, *, filter_automated_only: bool = False) -> tuple[int, int]:
    return self._collect_all_snapshot_rows(
        row_type="svl_linked_empty_move",
        row_label="Linked JE Header Kosong",
        filter_automated_only=filter_automated_only,
    )


def collect_all_svl_without_je(self, *, filter_automated_only: bool = False) -> tuple[int, int]:
    return self._collect_all_snapshot_rows(
        row_type="svl_no_move",
        row_label="SVL tanpa JE",
        filter_automated_only=filter_automated_only,
    )


def open_repair_collection_dialog(self) -> None:
    state = self.get_repair_collection_ui_state()
    if not state["has_rows"]:
        messagebox.showwarning(self._display_name, "Repair Collection masih kosong.")
        return
    if not state["can_open"]:
        scope_text = self._repair_collection_scope_text() or "-"
        if self._current_repair_collection_scope() is None:
            messagebox.showwarning(
                self._display_name,
                f"Repair Collection aktif di scope {scope_text}.\nPilih database dan company yang sama terlebih dahulu.",
            )
            return
        messagebox.showwarning(
            self._display_name,
            f"Repair Collection aktif di scope lain: {scope_text}.\n"
            "Kembali ke scope asal atau clear collection terlebih dahulu.",
        )
        return
    self._open_repair_dialog_for_rows(
        self._repair_collection_rows(),
        source_title="Repair Collection",
        source_note="Repair Collection lintas item. Urutan mengikuti koleksi terbaru di paling atas.",
    )


def _show_tree_context_menu(self, tree: ttk.Treeview, event: tk.Event) -> str:
    row_id = tree.identify_row(event.y)
    column_id = tree.identify_column(event.x)
    if row_id:
        if row_id not in tree.selection():
            tree.selection_set(row_id)
        tree.focus(row_id)
        self._tree_active_cell[tree] = (row_id, column_id) if column_id else None

    has_leaf_selection = bool(self._selected_tree_leaf_item_ids(tree))
    has_copyable_cell = self._has_copyable_tree_active_cell(tree)
    can_add_to_collection = self._can_add_selected_to_repair_collection(tree)
    can_remove_from_collection = self._can_remove_selected_from_repair_collection(tree)
    collection_state = self.get_repair_collection_ui_state()
    menu = tk.Menu(self.root, tearoff=0)
    menu.add_command(
        label="Copy Cell",
        command=lambda current=tree: self._copy_tree_active_cell(current),
        state="normal" if has_copyable_cell else "disabled",
    )
    menu.add_command(
        label="Copy Selected Rows",
        command=lambda current=tree: self._copy_tree_selected_rows(current),
        state="normal" if has_leaf_selection else "disabled",
    )
    menu.add_command(
        label="Copy Odoo URL/Locator",
        command=lambda current=tree: self._copy_tree_locator(current),
        state="normal" if has_leaf_selection else "disabled",
    )
    menu.add_separator()
    menu.add_command(
        label="Add Selected to Repair Collection",
        command=lambda current=tree: self.add_selected_to_repair_collection(current),
        state="normal" if can_add_to_collection else "disabled",
    )
    menu.add_command(
        label="Remove Selected from Repair Collection",
        command=lambda current=tree: self.remove_selected_from_repair_collection(current),
        state="normal" if can_remove_from_collection else "disabled",
    )
    menu.add_command(
        label="Open Repair Collection...",
        command=self.open_repair_collection_dialog,
        state="normal" if collection_state["can_open"] else "disabled",
    )
    menu.add_separator()
    repair_candidates = self._repair_candidates_for_tree(tree)
    menu.add_command(
        label="Repair Selected...",
        command=lambda current=tree: self._open_repair_dialog(current),
        state="normal" if repair_candidates else "disabled",
    )
    try:
        menu.tk_popup(event.x_root, event.y_root)
    finally:
        menu.grab_release()
    return "break"

def _repair_row_is_editable(row: dict[str, Any]) -> bool:
    return normalize_text(row.get("row_status")).lower() in REPAIR_EDITABLE_STATES

def _repair_row_status_label(row: dict[str, Any]) -> str:
    state = normalize_text(row.get("row_status")).lower() or "incomplete"
    return REPAIR_ROW_STATE_LABELS.get(state, REPAIR_ROW_STATE_LABELS["incomplete"])


def _sync_repair_row_account_codes(
    self,
    row: dict[str, Any],
    *,
    global_candidates: list[SvlDashboardRepairAccountCandidate] | None = None,
    category_candidates_cache: dict[tuple[tuple[str, str, str, str, str, int], ...], list[SvlDashboardRepairAccountCandidate]] | None = None,
    combined_candidates_cache: dict[tuple[tuple[str, str, str, str, str, int], ...], list[SvlDashboardRepairAccountCandidate]] | None = None,
    target_candidate_cache: dict[tuple[tuple[tuple[str, str, str, str, str, int], ...], str], SvlDashboardRepairAccountCandidate | None] | None = None,
    preview_map_cache: dict[tuple[tuple[str, str, str, str, str, int], ...], dict[str, str]] | None = None,
) -> None:
    role_value = self._normalize_repair_target_account_role(normalize_text(row.get("target_account_role")))
    row["target_account_role"] = role_value
    candidate_signature = self._repair_account_candidates_cache_key(list(row.get("account_candidates") or []))
    resolve_candidates = self._repair_account_candidates_for_seed(
        row,
        global_candidates=global_candidates,
        category_candidates_cache=category_candidates_cache,
        combined_candidates_cache=combined_candidates_cache,
    )
    target_candidate = self._repair_target_account_candidate(
        row,
        role_value,
        category_candidates_cache=category_candidates_cache,
        target_candidate_cache=target_candidate_cache,
    )
    resolve_code = normalize_text(row.get("resolve_account_code")).upper()
    preview_map: dict[str, str] | None = None
    if preview_map_cache is not None:
        preview_map = preview_map_cache.get(candidate_signature)
        if preview_map is None:
            preview_map = {
                normalize_text(candidate.code).upper(): candidate.display_label
                for candidate in resolve_candidates
                if normalize_text(candidate.code).upper()
            }
            preview_map_cache[candidate_signature] = preview_map

    def resolve_preview(code: str) -> str:
        clean_code = normalize_text(code).upper()
        if not clean_code:
            return "Belum dipilih"
        if preview_map is not None and clean_code in preview_map:
            return preview_map[clean_code]
        return self._lookup_repair_candidate_preview(clean_code, resolve_candidates)

    row["target_account_code"] = normalize_text(target_candidate.code if target_candidate is not None else "").upper()
    row["target_account_preview"] = (
        target_candidate.display_label
        if target_candidate is not None
        else (
            self._missing_repair_target_account_message(row, role_value)
            if role_value
            else "Belum dipilih"
        )
    )
    row["resolve_account_code"] = resolve_code
    row["resolve_account_preview"] = resolve_preview(resolve_code)
    debit_code = ""
    credit_code = ""
    if row["target_account_code"] and resolve_code:
        debit_code, credit_code = self._derive_repair_account_codes(
            row,
            target_account_code=row["target_account_code"],
            resolve_account_code=resolve_code,
        )
    row["debit_account_code"] = debit_code
    row["credit_account_code"] = credit_code
    row["debit_account_preview"] = resolve_preview(debit_code)
    row["credit_account_preview"] = resolve_preview(credit_code)


def _repair_reference_prefix(self) -> str:
    latest = getattr(self, "_module_settings", None)
    if latest is None:
        state_store = getattr(self, "_state_store", None)
        if state_store is not None:
            try:
                latest = state_store.load()
            except Exception:  # noqa: BLE001
                latest = None
    return normalize_text(getattr(latest, "last_prefix", "")).strip() or DEFAULT_REF_PREFIX


def _repair_generated_texts_for_row(self, row: dict[str, Any], *, prefix: str | None = None) -> tuple[str, str]:
    mode_value = normalize_text(row.get("target_mode"))
    if not mode_value:
        return ("", "")
    active_prefix = normalize_text(prefix).strip() or self._repair_reference_prefix()
    return (
        SvlFixJeDashboardPage._repair_generated_reference(mode_value, row, prefix=active_prefix),
        SvlFixJeDashboardPage._repair_generated_line_label(mode_value, row, prefix=active_prefix),
    )

def _set_repair_generated_flags(
    row: dict[str, Any],
    *,
    reference_generated: bool | None = None,
    line_label_generated: bool | None = None,
) -> None:
    if reference_generated is not None:
        row["reference_generated"] = bool(reference_generated)
    if line_label_generated is not None:
        row["line_label_generated"] = bool(line_label_generated)


def _sync_repair_generated_flags(self, row: dict[str, Any]) -> None:
    generated_reference, generated_line_label = self._repair_generated_texts_for_row(row)
    reference_text = normalize_text(row.get("reference"))
    line_label_text = normalize_text(row.get("line_label"))
    if "reference_generated" not in row:
        row["reference_generated"] = bool(reference_text and generated_reference and reference_text == generated_reference)
    if "line_label_generated" not in row:
        row["line_label_generated"] = bool(line_label_text and generated_line_label and line_label_text == generated_line_label)
    if not reference_text and generated_reference:
        row["reference"] = generated_reference
        row["reference_generated"] = True
    elif not generated_reference:
        row["reference_generated"] = False
    if not line_label_text and generated_line_label:
        row["line_label"] = generated_line_label
        row["line_label_generated"] = True
    elif not generated_line_label:
        row["line_label_generated"] = False


def _pcb_case1_generated_texts_for_row(self, row: dict[str, Any], *, prefix: str | None = None) -> tuple[str, str]:
    if not normalize_text(row.get("pcb_case")):
        row["pcb_case"] = "case1"
    active_prefix = normalize_text(prefix).strip() or self._repair_reference_prefix()
    return (
        SvlFixJeDashboardPage._pcb_case1_generated_reference(row, prefix=active_prefix),
        SvlFixJeDashboardPage._pcb_case1_generated_line_label(row),
    )

def _set_pcb_case1_generated_flags(
    row: dict[str, Any],
    *,
    reference_generated: bool | None = None,
    line_label_generated: bool | None = None,
) -> None:
    if reference_generated is not None:
        row["reference_generated"] = bool(reference_generated)
    if line_label_generated is not None:
        row["line_label_generated"] = bool(line_label_generated)


def _sync_pcb_case1_generated_flags(self, row: dict[str, Any]) -> None:
    if not normalize_text(row.get("pcb_case_label")):
        row["pcb_case_label"] = self._pcb_case_label(row=row)
    generated_reference, generated_line_label = self._pcb_case1_generated_texts_for_row(row)
    reference_text = normalize_text(row.get("reference"))
    line_label_text = normalize_text(row.get("line_label"))
    if "reference_generated" not in row:
        row["reference_generated"] = bool(reference_text and generated_reference and reference_text == generated_reference)
    if "line_label_generated" not in row:
        row["line_label_generated"] = bool(line_label_text and generated_line_label and line_label_text == generated_line_label)
    if not reference_text and generated_reference:
        row["reference"] = generated_reference
        row["reference_generated"] = True
    elif not generated_reference:
        row["reference_generated"] = False
    if not line_label_text and generated_line_label:
        row["line_label"] = generated_line_label
        row["line_label_generated"] = True
    elif not generated_line_label:
        row["line_label_generated"] = False


def _refresh_generated_pcb_case1_texts(self, row: dict[str, Any]) -> bool:
    generated_reference, generated_line_label = self._pcb_case1_generated_texts_for_row(row)
    changed = False
    if bool(row.get("reference_generated")) and generated_reference:
        if normalize_text(row.get("reference")) != generated_reference:
            row["reference"] = generated_reference
            changed = True
    if bool(row.get("line_label_generated")) and generated_line_label:
        if normalize_text(row.get("line_label")) != generated_line_label:
            row["line_label"] = generated_line_label
            changed = True
    return changed


def _refresh_pcb_case1_collection_prefix_texts(self) -> None:
    changed = False
    for row in getattr(self, "_pcb_repair_collection", OrderedDict()).values():
        self._sync_pcb_case1_generated_flags(row)
        if self._refresh_generated_pcb_case1_texts(row):
            changed = True
    widgets = getattr(self, "_last_pcb_case1_dialog_widgets", {})
    reload_row_list = widgets.get("reload_row_list")
    if changed and callable(reload_row_list):
        try:
            reload_row_list()
        except Exception:  # noqa: BLE001
            pass


def _refresh_generated_repair_texts(self, row: dict[str, Any]) -> bool:
    generated_reference, generated_line_label = self._repair_generated_texts_for_row(row)
    changed = False
    if bool(row.get("reference_generated")) and generated_reference:
        if normalize_text(row.get("reference")) != generated_reference:
            row["reference"] = generated_reference
            changed = True
    if bool(row.get("line_label_generated")) and generated_line_label:
        if normalize_text(row.get("line_label")) != generated_line_label:
            row["line_label"] = generated_line_label
            changed = True
    return changed


def _refresh_repair_collection_prefix_texts(self) -> None:
    changed = False
    for entry in getattr(self, "_repair_collection", OrderedDict()).values():
        draft = entry.draft
        self._ensure_repair_row_defaults(draft)
        if self._refresh_generated_repair_texts(draft):
            changed = True
    widgets = getattr(self, "_last_repair_dialog_widgets", {})
    reload_row_list = widgets.get("reload_row_list")
    if changed and callable(reload_row_list):
        try:
            reload_row_list(0)
        except Exception:  # noqa: BLE001
            pass


def _refresh_repair_row_state(
    self,
    row: dict[str, Any],
    *,
    preserve_terminal: bool = False,
    accounts_already_synced: bool = False,
    global_candidates: list[SvlDashboardRepairAccountCandidate] | None = None,
    category_candidates_cache: dict[tuple[tuple[str, str, str, str, str, int], ...], list[SvlDashboardRepairAccountCandidate]] | None = None,
    combined_candidates_cache: dict[tuple[tuple[str, str, str, str, str, int], ...], list[SvlDashboardRepairAccountCandidate]] | None = None,
    target_candidate_cache: dict[tuple[tuple[tuple[str, str, str, str, str, int], ...], str], SvlDashboardRepairAccountCandidate | None] | None = None,
    preview_map_cache: dict[tuple[tuple[str, str, str, str, str, int], ...], dict[str, str]] | None = None,
) -> None:
    current_state = normalize_text(row.get("row_status")).lower()
    if preserve_terminal and current_state in {"running", "repaired"}:
        return
    if not accounts_already_synced:
        self._sync_repair_row_account_codes(
            row,
            global_candidates=global_candidates,
            category_candidates_cache=category_candidates_cache,
            combined_candidates_cache=combined_candidates_cache,
            target_candidate_cache=target_candidate_cache,
            preview_map_cache=preview_map_cache,
        )
    issues: list[str] = []
    if not normalize_text(row.get("target_mode")):
        issues.append("Target")
    if not normalize_text(row.get("posting_mode")):
        issues.append("Posting")
    role_value = self._normalize_repair_target_account_role(normalize_text(row.get("target_account_role")))
    row["target_account_role"] = role_value
    if not role_value:
        issues.append("Target Account Type")
    if not normalize_text(row.get("resolve_account_code")):
        issues.append("Resolve Account")
    if role_value and not normalize_text(row.get("target_account_code")):
        issues.append(self._missing_repair_target_account_message(row, role_value))
    elif normalize_text(row.get("resolve_account_code")) and (
        not normalize_text(row.get("debit_account_code")) or not normalize_text(row.get("credit_account_code"))
    ):
        issues.append("Account")
    try:
        amount = float(row.get("amount") or 0.0)
    except (TypeError, ValueError):
        amount = 0.0
    if amount <= 0:
        issues.append("Nominal")
    if not normalize_text(row.get("date")):
        issues.append("Tanggal")
    if issues:
        row["row_status"] = "incomplete"
        row["row_status_message"] = ", ".join(issues)
        return
    row["row_status"] = "ready"
    row["row_status_message"] = "Ready"


def _apply_repair_global_defaults_to_row(
    self,
    row: dict[str, Any],
    *,
    target_mode_value: str,
    posting_mode_value: str,
    role_value: str,
    resolve_code: str,
    reference_prefix: str,
    global_candidates: list[SvlDashboardRepairAccountCandidate] | None = None,
    category_candidates_cache: dict[tuple[tuple[str, str, str, str, str, int], ...], list[SvlDashboardRepairAccountCandidate]] | None = None,
    combined_candidates_cache: dict[tuple[tuple[str, str, str, str, str, int], ...], list[SvlDashboardRepairAccountCandidate]] | None = None,
    target_candidate_cache: dict[tuple[tuple[tuple[str, str, str, str, str, int], ...], str], SvlDashboardRepairAccountCandidate | None] | None = None,
    preview_map_cache: dict[tuple[tuple[str, str, str, str, str, int], ...], dict[str, str]] | None = None,
) -> bool:
    if not self._repair_row_is_editable(row):
        return False
    row["target_mode"] = target_mode_value
    row["posting_mode"] = posting_mode_value
    row["target_account_role"] = role_value
    row["resolve_account_code"] = resolve_code
    if not normalize_text(row.get("date")):
        row["date"] = self._today_text()
    if target_mode_value:
        row["reference"], row["line_label"] = self._repair_generated_texts_for_row(row, prefix=reference_prefix)
        self._set_repair_generated_flags(row, reference_generated=True, line_label_generated=True)
    else:
        row["reference"] = ""
        row["line_label"] = ""
        self._set_repair_generated_flags(row, reference_generated=False, line_label_generated=False)
    self._sync_repair_row_account_codes(
        row,
        global_candidates=global_candidates,
        category_candidates_cache=category_candidates_cache,
        combined_candidates_cache=combined_candidates_cache,
        target_candidate_cache=target_candidate_cache,
        preview_map_cache=preview_map_cache,
    )
    self._refresh_repair_row_state(
        row,
        accounts_already_synced=True,
        global_candidates=global_candidates,
        category_candidates_cache=category_candidates_cache,
        combined_candidates_cache=combined_candidates_cache,
        target_candidate_cache=target_candidate_cache,
        preview_map_cache=preview_map_cache,
    )
    return True

def _repair_source_kind(row: dict[str, Any]) -> str:
    clean_kind = normalize_text(row.get("repair_source_kind")).lower()
    if clean_kind:
        return clean_kind
    move_id = int(row.get("move_id") or 0)
    latest_status = normalize_text(row.get("latest_snapshot_status")).lower()
    if latest_status == "svl tanpa je" or move_id <= 0:
        return "svl_no_move"
    if latest_status == "linked je header kosong":
        return "svl_linked_empty_move"
    return "repair_row"

def _repair_source_label(row: dict[str, Any]) -> str:
    clean_label = normalize_text(row.get("repair_source_label"))
    if clean_label:
        return clean_label
    clean_kind = SvlFixJeDashboardPage._repair_source_kind(row)
    if clean_kind == "svl_no_move":
        return "SVL tanpa JE"
    if clean_kind == "svl_linked_empty_move":
        return "Linked JE Header Kosong"
    return normalize_text(row.get("latest_snapshot_status")) or "Repair Row"

def _repair_source_behavior_text(source_kind: str) -> str:
    clean_kind = normalize_text(source_kind).lower()
    if clean_kind == "svl_no_move":
        return "SVL tanpa JE: create JE baru lalu link ke SVL."
    if clean_kind == "svl_linked_empty_move":
        return "Linked JE Header Kosong: create JE baru, relink SVL, old move mark only."
    return ""


def _repair_dialog_source_note(self, base_note: str, rows: list[dict[str, Any]], *, mixed_only: bool = False) -> str:
    clean_base = normalize_text(base_note)
    ordered_kinds: list[str] = []
    seen_kinds: set[str] = set()
    for row in rows:
        source_kind = self._repair_source_kind(row)
        if source_kind and source_kind not in seen_kinds:
            seen_kinds.add(source_kind)
            ordered_kinds.append(source_kind)
    lines = [clean_base] if clean_base else []
    if ordered_kinds and (not mixed_only or len(ordered_kinds) > 1):
        behavior_lines = [
            self._repair_source_behavior_text(source_kind)
            for source_kind in ordered_kinds
            if self._repair_source_behavior_text(source_kind)
        ]
        if behavior_lines:
            if lines:
                lines.append("")
            lines.extend(behavior_lines)
    return "\n".join(line for line in lines if line)

def _group_repair_rows_by_source(rows: list[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    grouped: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
    for row in rows:
        label = SvlFixJeDashboardPage._repair_source_label(row)
        grouped.setdefault(label, []).append(row)
    return list(grouped.items())

def _repair_group_header_text(source_label: str, rows: list[dict[str, Any]]) -> str:
    total_amount = sum(float(row.get("amount") or 0.0) for row in rows)
    return f"{source_label} | {len(rows)} row | Total {total_amount:,.2f}"

def _repair_linked_move_label(row: dict[str, Any]) -> str:
    move_name = normalize_text(row.get("move_name"))
    if move_name:
        return move_name
    if SvlFixJeDashboardPage._repair_source_kind(row) == "svl_no_move":
        return "Tidak ada JE"
    return "-"

def _repair_selector_row_text(row: dict[str, Any]) -> str:
    item_code = normalize_text(row.get("item_code")) or "-"
    item_name = normalize_text(row.get("item_name")) or "-"
    status_text = SvlFixJeDashboardPage._repair_row_status_label(row)
    return f"[{status_text}] {item_code} | {item_name}"

def _repair_row_state_tag(row: dict[str, Any]) -> str:
    state_tag = f"repair_row_{normalize_text(row.get('row_status')).lower() or 'incomplete'}"
    if state_tag not in {
        "repair_row_incomplete",
        "repair_row_needs_review",
        "repair_row_ready",
        "repair_row_running",
        "repair_row_failed",
        "repair_row_repaired",
    }:
        return "repair_row_incomplete"
    return state_tag

def _repair_selector_row_values(row: dict[str, Any]) -> tuple[str, ...]:
    svl_id = int(row.get("svl_id") or 0)
    svl_date = normalize_text(row.get("svl_date")) or normalize_text(row.get("date")) or "-"
    svl_qty = float(row.get("svl_qty") or 0.0)
    svl_unit_cost = float(row.get("svl_unit_cost") or 0.0)
    svl_value = float(row.get("svl_value") or row.get("signed_amount") or row.get("amount") or 0.0)
    reference = normalize_text(row.get("svl_reference")) or normalize_text(row.get("reference")) or "-"
    return (
        str(svl_id) if svl_id > 0 else "-",
        svl_date,
        f"{svl_qty:,.2f}",
        f"{svl_unit_cost:,.2f}",
        f"{svl_value:,.2f}",
        reference,
        SvlFixJeDashboardPage._repair_linked_move_label(row),
    )

def _repair_row_preview(row: dict[str, Any]) -> str:
    item_code = normalize_text(row.get("item_code")) or "-"
    item_name = normalize_text(row.get("item_name")) or "-"
    amount = float(row.get("amount") or 0.0)
    move_name = normalize_text(row.get("move_name")) or "NEW MOVE"
    status_text = SvlFixJeDashboardPage._repair_row_status_label(row)
    detail_text = normalize_text(row.get("row_status_message"))
    preview = f"[{status_text}] {item_code} | {item_name} | {amount:,.2f} | {move_name}"
    if detail_text and detail_text != status_text:
        preview = f"{preview} | {detail_text}"
    return preview


def _build_dashboard_repair_row(self, row: dict[str, Any], *, fallback_company_id: int) -> SvlDashboardRepairRow:
    return SvlDashboardRepairRow(
        row_key=normalize_text(row.get("row_key")),
        company_id=int(row.get("company_id") or fallback_company_id),
        company_name=normalize_text(row.get("company_name")) or self._selected_company_name(),
        item_product_id=int(row.get("item_product_id") or 0),
        item_code=normalize_text(row.get("item_code")),
        item_name=normalize_text(row.get("item_name")),
        amount=float(row.get("amount") or 0.0),
        date=normalize_text(row.get("date")),
        reference=normalize_text(row.get("reference")),
        line_label=normalize_text(row.get("line_label")),
        journal_code=normalize_text(row.get("journal_code")),
        debit_account_code=normalize_text(row.get("debit_account_code")),
        credit_account_code=normalize_text(row.get("credit_account_code")),
        svl_id=int(row.get("svl_id") or 0),
        move_id=int(row.get("move_id") or 0),
        move_name=normalize_text(row.get("move_name")),
        move_state=normalize_text(row.get("move_state")),
        target_mode=normalize_text(row.get("target_mode")),
        posting_mode=normalize_text(row.get("posting_mode")),
        signed_amount=float(row.get("signed_amount") or 0.0),
        base_reference=normalize_text(row.get("base_reference")),
        base_line_label=normalize_text(row.get("base_line_label")),
        svl_reference=normalize_text(row.get("svl_reference")),
        repair_source_kind=normalize_text(row.get("repair_source_kind")),
        repair_source_label=normalize_text(row.get("repair_source_label")),
        account_candidates=list(row.get("account_candidates") or []),
    )

def _apply_repair_result_to_row(row: dict[str, Any], result: SvlDashboardRepairRowResult) -> None:
    if normalize_text(result.status).upper() == "ERROR":
        row["row_status"] = "failed"
        row["row_status_message"] = normalize_text(result.message) or normalize_text(result.error_kind) or "Repair gagal"
    else:
        row["row_status"] = "repaired"
        row["row_status_message"] = normalize_text(result.message) or "Repaired"
        if int(result.move_id or 0) > 0:
            row["move_id"] = int(result.move_id or 0)
        if normalize_text(result.move_name):
            row["move_name"] = normalize_text(result.move_name)


def _apply_repair_results_to_collection_entries(self, results: list[SvlDashboardRepairRowResult]) -> None:
    if not getattr(self, "_repair_collection", None):
        return
    result_by_row_key = {
        normalize_text(result.row_key): result
        for result in results
        if normalize_text(result.row_key)
    }
    if not result_by_row_key:
        return
    changed = False
    for row_key, entry in self._repair_collection.items():
        result = result_by_row_key.get(normalize_text(row_key))
        if result is None:
            continue
        self._apply_repair_result_to_row(entry.draft, result)
        changed = True
    if changed:
        self._notify_repair_collection_changed()


def _repair_collection_all_repaired(self) -> bool:
    collection = getattr(self, "_repair_collection", OrderedDict())
    return bool(collection) and all(
        normalize_text(entry.draft.get("row_status")).lower() == "repaired"
        for entry in collection.values()
    )


def _mark_active_repair_rows_failed(self, message: str) -> None:
    active_row_keys = {normalize_text(row_key) for row_key in getattr(self, "_active_repair_row_keys", set()) if normalize_text(row_key)}
    if not active_row_keys:
        return
    for entry in getattr(self, "_repair_collection", OrderedDict()).values():
        if normalize_text(entry.row_key) not in active_row_keys:
            continue
        entry.draft["row_status"] = "failed"
        entry.draft["row_status_message"] = normalize_text(message) or "Repair gagal"
    widgets = getattr(self, "_last_repair_dialog_widgets", {})
    for row in widgets.get("draft_rows", []) or []:
        if normalize_text(row.get("row_key")) not in active_row_keys:
            continue
        row["row_status"] = "failed"
        row["row_status_message"] = normalize_text(message) or "Repair gagal"
    reload_dialog = widgets.get("reload_row_list")
    if callable(reload_dialog) and widgets.get("draft_rows"):
        try:
            reload_dialog(0)
        except Exception:  # noqa: BLE001
            pass
    self._notify_repair_collection_changed()


def _ensure_repair_row_defaults(self, row: dict[str, Any]) -> None:
    row.setdefault("date", self._today_text())
    row.setdefault("target_mode", "")
    row.setdefault("posting_mode", "")
    row.setdefault("target_account_role", "")
    row.setdefault("target_account_code", "")
    row.setdefault("target_account_preview", "Belum dipilih")
    row.setdefault("resolve_account_code", "")
    row.setdefault("resolve_account_preview", "Belum dipilih")
    row.setdefault("debit_account_code", "")
    row.setdefault("credit_account_code", "")
    row.setdefault("debit_account_preview", "Belum dipilih")
    row.setdefault("credit_account_preview", "Belum dipilih")
    row.setdefault("reference", "")
    row.setdefault("line_label", "")
    row.setdefault("reference_generated", False)
    row.setdefault("line_label_generated", False)
    row.setdefault("row_status", "incomplete")
    row.setdefault("row_status_message", "")
    row.setdefault("item_category_name", "")
    row.setdefault("svl_date", "")
    row.setdefault("svl_qty", 0.0)
    row.setdefault("svl_unit_cost", 0.0)
    row.setdefault("svl_value", float(row.get("signed_amount") or row.get("amount") or 0.0))
    row.setdefault("svl_reference", "")
    row.setdefault("repair_source_kind", self._repair_source_kind(row))
    row.setdefault("repair_source_label", self._repair_source_label(row))
    row["target_account_role"] = self._normalize_repair_target_account_role(normalize_text(row.get("target_account_role")))
    self._sync_repair_generated_flags(row)
    self._sync_repair_row_account_codes(row)
    self._refresh_repair_row_state(row, preserve_terminal=True)


def _resolve_repair_accounts_async(
    self,
    *,
    company_id: int,
    debit_code: str,
    credit_code: str,
    on_done: Callable[[Any, Any, str], None],
) -> None:
    profile_id = self._selected_database_profile_id()

    def worker() -> None:
        try:
            settings, config = self._runtime_builder(
                self.context.global_settings,
                self.logger,
                database_profile_id=profile_id,
            )

            async def _run() -> tuple[Any, Any]:
                async with AsyncOdooJsonRpcClient(config=config, settings=settings, logger=self.logger) as rpc:
                    service = SvlDashboardRepairServiceAsync(
                        rpc=rpc,
                        logger=self.logger,
                        master_cache=self.context.master_cache,
                    )
                    debit = await service.resolve_account(company_id=company_id, code=debit_code)
                    credit = await service.resolve_account(company_id=company_id, code=credit_code)
                    return debit, credit

            debit_account, credit_account = asyncio.run(_run())
            self.root.after(0, lambda: on_done(debit_account, credit_account, ""))
        except Exception as exc:  # noqa: BLE001
            self.root.after(0, lambda: on_done(None, None, str(exc)))

    threading.Thread(target=worker, daemon=True, name="svl-dashboard-repair-account-lookup").start()


def _start_repair(self, rows: list[SvlDashboardRepairRow]) -> None:
    if not rows or self._busy:
        return
    self._active_repair_row_keys = {
        normalize_text(row.row_key)
        for row in rows
        if normalize_text(row.row_key)
    }
    self._set_busy(True)
    self.status_var.set(f"Menjalankan repair {len(rows)} baris...")
    self._set_repair_dialog_running_state(True)
    self._apply_repair_progress(
        SvlDashboardRepairProgressSnapshot(
            phase="repair",
            processed=0,
            total=len(rows),
            current="Memulai repair...",
            progress=0.0,
            success_count=0,
            error_count=0,
        )
    )
    profile_id = self._selected_database_profile_id()
    request = SvlDashboardRepairRequest(
        database=normalize_text(self._latest_snapshot.database if self._latest_snapshot is not None else ""),
        max_workers=int(getattr(self._module_settings, "max_workers", 1) or 1),
        rows=rows,
    )

    def worker() -> None:
        try:
            settings, config = self._runtime_builder(
                self.context.global_settings,
                self.logger,
                database_profile_id=profile_id,
            )
            request.database = config.database
            self.log_queue.put(f"Repair dashboard connecting to {config.base_url} [{config.database}]...")

            async def _run() -> Any:
                async with AsyncOdooJsonRpcClient(config=config, settings=settings, logger=self.logger) as rpc:
                    service = SvlDashboardRepairServiceAsync(
                        rpc=rpc,
                        logger=self.logger,
                        master_cache=self.context.master_cache,
                    )
                    return await service.execute(request)

            request.on_progress = lambda snapshot: self.ui_queue.put({"type": "repair_progress", "snapshot": snapshot})
            summary = asyncio.run(_run())
            for result in summary.results:
                move_name = normalize_text(result.move_name)
                suffix = f" [{move_name}]" if move_name else ""
                self.log_queue.put(f"Repair {result.status}: {result.row_key}{suffix} - {result.message}")
            for warning in summary.warnings:
                self.log_queue.put(f"WARNING: {warning}")
            self.ui_queue.put(
                {
                    "type": "repair_complete",
                    "results": summary.results,
                    "database": summary.database,
                    "base_url": config.base_url,
                }
            )
        except Exception as exc:  # noqa: BLE001
            self.log_queue.put(f"ERROR: {exc}")
            self.ui_queue.put({"type": "error", "message": str(exc)})

    self.worker = threading.Thread(target=worker, daemon=True, name="svl-dashboard-repair")
    self.worker.start()


def _open_repair_dialog(self, tree: ttk.Treeview) -> None:
    repair_meta = self._repair_candidates_for_tree(tree)
    if not repair_meta:
        return
    self._open_repair_dialog_for_rows(
        [dict(meta["repair_seed"]) for meta in repair_meta],
        source_title="Selected Rows",
        source_note="Selected rows dari detail item yang sedang dibuka.",
    )


def _open_repair_dialog_for_rows(
    self,
    draft_rows: list[dict[str, Any]],
    *,
    source_title: str,
    source_note: str,
) -> None:
    return self._open_repair_dialog_for_rows_v2(
        draft_rows,
        source_title=source_title,
        source_note=source_note,
    )

    company_id = self._selected_company_id()
    if company_id <= 0:
        messagebox.showwarning(self._display_name, "Pilih company terlebih dahulu.")
        return

    if not draft_rows:
        return
    draft_rows = [dict(row) for row in draft_rows]
    target_options = self._repair_target_options(draft_rows)
    posting_options = [("Draft Only", "draft"), ("Auto Post", "post")]

    dialog = tk.Toplevel(self.root)
    dialog.title("Repair Journal Dashboard")
    dialog.transient(self.root)
    dialog.grab_set()
    dialog.configure(bg=T.BG_CARD)
    dialog.geometry("980x560")
    dialog.minsize(900, 520)
    dialog.rowconfigure(0, weight=1)
    dialog.columnconfigure(0, weight=1)

    body = tk.Frame(dialog, bg=T.BG_CARD)
    body.grid(row=0, column=0, sticky="nsew")
    body.columnconfigure(0, weight=0)
    body.columnconfigure(1, weight=1)
    body.rowconfigure(0, weight=1)

    left = tk.Frame(body, bg=T.BG_CARD, padx=12, pady=12)
    left.grid(row=0, column=0, sticky="nsw")
    left.rowconfigure(2, weight=1)
    tk.Label(
        left,
        text=source_title,
        bg=T.BG_CARD,
        fg=T.TEXT_ON_LIGHT,
        font=T.font(T.FONT_BODY_SIZE, bold=True),
    ).grid(row=0, column=0, sticky="w", pady=(0, 8))
    source_var = tk.StringVar(value=normalize_text(source_note))
    tk.Label(
        left,
        textvariable=source_var,
        bg=T.BG_CARD,
        fg=T.TEXT_MUTED,
        font=T.font(T.FONT_SMALL_SIZE),
        justify="left",
        anchor="w",
        wraplength=320,
    ).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 8))

    row_list = tk.Listbox(left, exportselection=False, width=44, height=22, selectmode="extended")
    row_list.grid(row=2, column=0, sticky="nsw")
    list_scroll = ttk.Scrollbar(left, orient="vertical", command=row_list.yview)
    list_scroll.grid(row=2, column=1, sticky="ns")
    row_list.configure(yscrollcommand=list_scroll.set)

    select_btns_frame = tk.Frame(left, bg=T.BG_CARD)
    select_btns_frame.grid(row=3, column=0, columnspan=2, sticky="w", pady=(4, 0))
    tk.Button(
        select_btns_frame,
        text="Select All",
        bg=T.BG_INPUT,
        fg=T.TEXT_ON_LIGHT,
        font=T.font(T.FONT_SMALL_SIZE),
        relief="flat",
        padx=8,
        pady=3,
        command=lambda: row_list.selection_set(0, "end"),
    ).pack(side="left")
    tk.Button(
        select_btns_frame,
        text="Deselect All",
        bg=T.BG_INPUT,
        fg=T.TEXT_ON_LIGHT,
        font=T.font(T.FONT_SMALL_SIZE),
        relief="flat",
        padx=8,
        pady=3,
        command=lambda: row_list.selection_clear(0, "end"),
    ).pack(side="left", padx=(4, 0))

    for row in draft_rows:
        row.setdefault("debit_account_code", "")
        row.setdefault("credit_account_code", "")
        row.setdefault("debit_account_preview", "Belum dipilih")
        row.setdefault("credit_account_preview", "Belum dipilih")
        if not normalize_text(row.get("debit_account_code")) and not normalize_text(row.get("credit_account_code")):
            suggested_debit, suggested_credit = self._suggest_repair_account_codes(
                row,
                self._repair_account_candidates_for_seed(row),
            )
            row["debit_account_code"] = suggested_debit
            row["credit_account_code"] = suggested_credit
    right_host = tk.Frame(body, bg=T.BG_CARD)
    right_host.grid(row=0, column=1, sticky="nsew")
    right_host.columnconfigure(0, weight=1)
    right_host.rowconfigure(0, weight=1)

    right_scroll = ScrollableFrame(right_host, bg=T.BG_CARD)
    right_scroll.grid(row=0, column=0, sticky="nsew")
    right = tk.Frame(right_scroll.interior, bg=T.BG_CARD, padx=12, pady=12)
    right.pack(fill="both", expand=True)
    right.columnconfigure(1, weight=1)

    footer = tk.Frame(right_host, bg=T.BG_CARD, padx=12, pady=0)
    footer.grid(row=1, column=0, sticky="ew", pady=(0, 12))

    tk.Label(
        right,
        text="Repair Mode",
        bg=T.BG_CARD,
        fg=T.TEXT_ON_LIGHT,
        font=T.font(T.FONT_BODY_SIZE, bold=True),
    ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))

    target_mode_label_to_value = {label: value for label, value in target_options}
    target_mode_value_to_label = {value: label for label, value in target_options}
    posting_mode_label_to_value = {label: value for label, value in posting_options}
    posting_mode_value_to_label = {value: label for label, value in posting_options}

    target_mode_var = tk.StringVar(
        value=target_mode_value_to_label.get(normalize_text(draft_rows[0].get("target_mode")), target_options[0][0])
    )
    posting_mode_var = tk.StringVar(
        value=posting_mode_value_to_label.get(normalize_text(draft_rows[0].get("posting_mode")), posting_options[0][0])
    )
    target_warning_var = tk.StringVar(
        value=self._repair_target_warning_text(
            target_mode_label_to_value.get(target_mode_var.get(), target_options[0][1]),
            draft_rows,
        )
    )
    debit_account_var = tk.StringVar(value="")
    credit_account_var = tk.StringVar(value="")
    debit_preview_var = tk.StringVar(value="Belum dipilih")
    credit_preview_var = tk.StringVar(value="Belum dipilih")
    current_index = {"value": 0}
    active_account_candidates: dict[str, list[SvlDashboardRepairAccountCandidate]] = {"value": []}
    selection_guard = {"active": False}

    tk.Label(right, text="Target:", bg=T.BG_CARD, fg=T.TEXT_ON_LIGHT, font=T.font()).grid(
        row=1, column=0, sticky="w", pady=4
    )
    target_combo = ttk.Combobox(
        right,
        state="readonly",
        textvariable=target_mode_var,
        values=[label for label, _value in target_options],
    )
    target_combo.grid(row=1, column=1, sticky="ew", pady=4)

    tk.Label(right, text="Posting:", bg=T.BG_CARD, fg=T.TEXT_ON_LIGHT, font=T.font()).grid(
        row=2, column=0, sticky="w", pady=4
    )
    posting_combo = ttk.Combobox(
        right,
        state="readonly",
        textvariable=posting_mode_var,
        values=[label for label, _value in posting_options],
    )
    posting_combo.grid(row=2, column=1, sticky="ew", pady=4)
    tk.Label(
        right,
        textvariable=target_warning_var,
        bg=T.BG_CARD,
        fg=T.STATUS_WARNING,
        font=T.font(T.FONT_SMALL_SIZE),
        justify="left",
        anchor="w",
        wraplength=520,
    ).grid(row=3, column=0, columnspan=2, sticky="ew", pady=(0, 8))

    account_header = tk.Label(
        right,
        text="Account Resolve",
        bg=T.BG_CARD,
        fg=T.TEXT_ON_LIGHT,
        font=T.font(T.FONT_BODY_SIZE, bold=True),
    )
    account_header.grid(row=4, column=0, columnspan=2, sticky="w", pady=(8, 8))

    tk.Label(right, text="Akun Debit:", bg=T.BG_CARD, fg=T.TEXT_ON_LIGHT, font=T.font()).grid(
        row=5, column=0, sticky="w", pady=4
    )
    debit_picker = _RepairAccountPicker(
        right,
        variable=debit_account_var,
        filter_fn=self._filter_repair_account_candidates,
        on_change=lambda: refresh_account_previews(),
    )
    debit_picker.grid(row=5, column=1, sticky="ew", pady=4)
    tk.Label(right, textvariable=debit_preview_var, bg=T.BG_CARD, fg=T.TEXT_MUTED, font=T.font(T.FONT_SMALL_SIZE)).grid(
        row=6, column=1, sticky="w", pady=(0, 6)
    )

    tk.Label(right, text="Akun Kredit:", bg=T.BG_CARD, fg=T.TEXT_ON_LIGHT, font=T.font()).grid(
        row=7, column=0, sticky="w", pady=4
    )
    credit_picker = _RepairAccountPicker(
        right,
        variable=credit_account_var,
        filter_fn=self._filter_repair_account_candidates,
        on_change=lambda: refresh_account_previews(),
    )
    credit_picker.grid(row=7, column=1, sticky="ew", pady=4)
    tk.Label(
        right,
        textvariable=credit_preview_var,
        bg=T.BG_CARD,
        fg=T.TEXT_MUTED,
        font=T.font(T.FONT_SMALL_SIZE),
    ).grid(row=8, column=1, sticky="w", pady=(0, 6))

    row_header = tk.Label(
        right,
        text="Row Editor",
        bg=T.BG_CARD,
        fg=T.TEXT_ON_LIGHT,
        font=T.font(T.FONT_BODY_SIZE, bold=True),
    )
    row_header.grid(row=9, column=0, columnspan=2, sticky="w", pady=(12, 8))

    amount_var = tk.StringVar(value="")
    date_var = tk.StringVar(value="")
    reference_var = tk.StringVar(value="")
    line_label_var = tk.StringVar(value="")
    journal_code_var = tk.StringVar(value="")

    def labeled_entry(row_index: int, label: str, variable: tk.StringVar) -> None:
        tk.Label(right, text=label, bg=T.BG_CARD, fg=T.TEXT_ON_LIGHT, font=T.font()).grid(
            row=row_index, column=0, sticky="w", pady=4
        )
        tk.Entry(right, textvariable=variable, bg=T.BG_INPUT, relief="solid", bd=1).grid(
            row=row_index, column=1, sticky="ew", pady=4
        )

    labeled_entry(10, "Nominal:", amount_var)
    labeled_entry(11, "Tanggal:", date_var)
    labeled_entry(12, "Reference:", reference_var)
    labeled_entry(13, "Label Line:", line_label_var)
    labeled_entry(14, "Journal Code:", journal_code_var)

    action_row = tk.Frame(footer, bg=T.BG_CARD)
    action_row.pack(fill="x")

    def update_row_list_entry(index: int) -> None:
        if index < 0 or index >= len(draft_rows):
            return
        selected_indices = tuple(int(value) for value in row_list.curselection())
        anchor_index = int(row_list.index("anchor")) if row_list.size() else index
        active_index = int(row_list.index("active")) if row_list.size() else index
        row_list.delete(index)
        row_list.insert(index, self._repair_row_preview(draft_rows[index]))
        selection_guard["active"] = True
        row_list.selection_clear(0, "end")
        for selected_index in selected_indices:
            if 0 <= selected_index < row_list.size():
                row_list.selection_set(selected_index)
        if 0 <= anchor_index < row_list.size():
            row_list.selection_anchor(anchor_index)
        if 0 <= active_index < row_list.size():
            row_list.activate(active_index)
        selection_guard["active"] = False

    def reload_row_list(select_index: int = 0) -> None:
        selection_guard["active"] = True
        row_list.delete(0, "end")
        for row in draft_rows:
            row_list.insert("end", self._repair_row_preview(row))
        if draft_rows:
            safe_index = max(0, min(select_index, len(draft_rows) - 1))
            row_list.selection_clear(0, "end")
            row_list.selection_set(safe_index)
            row_list.selection_anchor(safe_index)
            row_list.activate(safe_index)
        selection_guard["active"] = False
        if draft_rows:
            load_row(safe_index)

    def sync_active_account_preview_vars() -> None:
        active_candidates = active_account_candidates["value"]
        debit_preview_var.set(self._lookup_repair_candidate_preview(debit_account_var.get(), active_candidates))
        credit_preview_var.set(self._lookup_repair_candidate_preview(credit_account_var.get(), active_candidates))

    def refresh_account_previews() -> None:
        sync_active_account_preview_vars()
        index = int(current_index["value"])
        if index < 0 or index >= len(draft_rows):
            return
        row = draft_rows[index]
        row["debit_account_code"] = normalize_text(debit_account_var.get())
        row["credit_account_code"] = normalize_text(credit_account_var.get())
        row["debit_account_preview"] = normalize_text(debit_preview_var.get())
        row["credit_account_preview"] = normalize_text(credit_preview_var.get())
        update_row_list_entry(index)

    def apply_account_candidates_for_row(row: dict[str, Any], *, apply_defaults: bool = False) -> None:
        candidates = self._repair_account_candidates_for_seed(row)
        active_account_candidates["value"] = candidates
        debit_picker.set_candidates(candidates)
        credit_picker.set_candidates(candidates)
        if apply_defaults and (not normalize_text(row.get("debit_account_code")) and not normalize_text(row.get("credit_account_code"))):
            suggested_debit, suggested_credit = self._suggest_repair_account_codes(row, candidates)
            row["debit_account_code"] = suggested_debit
            row["credit_account_code"] = suggested_credit
        debit_picker.set_value(normalize_text(row.get("debit_account_code")))
        credit_picker.set_value(normalize_text(row.get("credit_account_code")))
        sync_active_account_preview_vars()
        row["debit_account_preview"] = normalize_text(debit_preview_var.get())
        row["credit_account_preview"] = normalize_text(credit_preview_var.get())

    def apply_generated_texts_to_row(row: dict[str, Any], mode_value: str) -> None:
        row["target_mode"] = mode_value
        row["reference"], row["line_label"] = self._repair_generated_texts_for_row(row)

    def apply_target_mode_defaults(mode_value: str) -> None:
        for index, row in enumerate(draft_rows):
            apply_generated_texts_to_row(row, mode_value)
            row["posting_mode"] = posting_mode_label_to_value.get(posting_mode_var.get(), posting_options[0][1])
            update_row_list_entry(index)
        current_row = draft_rows[int(current_index["value"])]
        reference_var.set(normalize_text(current_row.get("reference")))
        line_label_var.set(normalize_text(current_row.get("line_label")))
        target_warning_var.set(self._repair_target_warning_text(mode_value, draft_rows))
        row_list.activate(int(current_index["value"]))

    def save_current_row(*_args: Any) -> None:
        index = int(current_index["value"])
        if index < 0 or index >= len(draft_rows):
            return
        row = draft_rows[index]
        row["amount"] = self._parse_amount_text(amount_var.get())
        row["date"] = normalize_text(date_var.get())
        row["reference"] = normalize_text(reference_var.get())
        row["line_label"] = normalize_text(line_label_var.get())
        row["journal_code"] = normalize_text(journal_code_var.get())
        row["target_mode"] = target_mode_label_to_value.get(target_mode_var.get(), target_options[0][1])
        row["posting_mode"] = posting_mode_label_to_value.get(posting_mode_var.get(), posting_options[0][1])
        row["debit_account_code"] = normalize_text(debit_account_var.get())
        row["credit_account_code"] = normalize_text(credit_account_var.get())
        row["debit_account_preview"] = normalize_text(debit_preview_var.get())
        row["credit_account_preview"] = normalize_text(credit_preview_var.get())
        update_row_list_entry(index)

    def load_row(index: int) -> None:
        if index < 0 or index >= len(draft_rows):
            return
        current_index["value"] = index
        row = draft_rows[index]
        amount_var.set(f"{float(row.get('amount') or 0.0):,.2f}")
        date_var.set(normalize_text(row.get("date")))
        reference_var.set(normalize_text(row.get("reference")))
        line_label_var.set(normalize_text(row.get("line_label")))
        journal_code_var.set(normalize_text(row.get("journal_code")))
        target_mode_var.set(target_mode_value_to_label.get(normalize_text(row.get("target_mode")), target_options[0][0]))
        posting_mode_var.set(posting_mode_value_to_label.get(normalize_text(row.get("posting_mode")), posting_options[0][0]))
        target_warning_var.set(
            self._repair_target_warning_text(
                target_mode_label_to_value.get(target_mode_var.get(), target_options[0][1]),
                draft_rows,
            )
        )
        apply_account_candidates_for_row(row, apply_defaults=True)
        row_list.activate(index)

    def select_row(index: int) -> None:
        if index < 0 or index >= len(draft_rows):
            return
        save_current_row()
        selection_guard["active"] = True
        row_list.selection_clear(0, "end")
        row_list.selection_set(index)
        row_list.selection_anchor(index)
        row_list.activate(index)
        selection_guard["active"] = False
        load_row(index)

    def on_row_selected(_event: tk.Event | None = None) -> None:
        if selection_guard["active"]:
            return
        selection = row_list.curselection()
        if not selection:
            return
        anchor_index = int(row_list.index("anchor")) if selection else int(selection[0])
        target_index = anchor_index if anchor_index in selection else int(selection[0])
        if target_index != int(current_index["value"]):
            save_current_row()
            load_row(target_index)
        else:
            row_list.activate(target_index)

    def on_account_lookup_done(debit_account: Any, credit_account: Any, error: str) -> None:
        if not dialog.winfo_exists():
            return
        if error:
            messagebox.showerror(self._display_name, error)
            return
        debit_preview_var.set(
            f"{debit_account.code} - {debit_account.name}" if debit_account is not None else "Tidak ditemukan exact"
        )
        credit_preview_var.set(
            f"{credit_account.code} - {credit_account.name}" if credit_account is not None else "Tidak ditemukan exact"
        )
        index = int(current_index["value"])
        if 0 <= index < len(draft_rows):
            draft_rows[index]["debit_account_preview"] = normalize_text(debit_preview_var.get())
            draft_rows[index]["credit_account_preview"] = normalize_text(credit_preview_var.get())
            update_row_list_entry(index)

    def check_accounts() -> None:
        save_current_row()
        current_row = draft_rows[int(current_index["value"])]
        debit_code = normalize_text(current_row.get("debit_account_code"))
        credit_code = normalize_text(current_row.get("credit_account_code"))
        if not debit_code or not credit_code:
            messagebox.showwarning(self._display_name, "Isi akun debit dan kredit terlebih dahulu.")
            return
        debit_preview_var.set("Checking...")
        credit_preview_var.set("Checking...")
        self._resolve_repair_accounts_async(
            company_id=company_id,
            debit_code=debit_code,
            credit_code=credit_code,
            on_done=on_account_lookup_done,
        )

    def apply_coa_to_selected() -> None:
        save_current_row()
        selection = tuple(int(index) for index in row_list.curselection())
        if not selection:
            messagebox.showwarning(self._display_name, "Pilih row di list kiri terlebih dahulu.")
            return
        debit_code = normalize_text(debit_account_var.get())
        credit_code = normalize_text(credit_account_var.get())
        debit_preview = normalize_text(debit_preview_var.get())
        credit_preview = normalize_text(credit_preview_var.get())
        for index in selection:
            if index < 0 or index >= len(draft_rows):
                continue
            draft_rows[index]["debit_account_code"] = debit_code
            draft_rows[index]["credit_account_code"] = credit_code
            draft_rows[index]["debit_account_preview"] = debit_preview
            draft_rows[index]["credit_account_preview"] = credit_preview
            update_row_list_entry(index)
        self.status_var.set(f"COA diterapkan ke {len(selection)} row terpilih.")

    def apply_all_to_selected() -> None:
        save_current_row()
        selection = tuple(int(index) for index in row_list.curselection())
        if not selection:
            messagebox.showwarning(self._display_name, "Pilih row di list kiri terlebih dahulu.")
            return
        target_mode_value = target_mode_label_to_value.get(target_mode_var.get(), target_options[0][1])
        posting_mode_value = posting_mode_label_to_value.get(posting_mode_var.get(), posting_options[0][1])
        debit_code = normalize_text(debit_account_var.get())
        credit_code = normalize_text(credit_account_var.get())
        debit_preview = normalize_text(debit_preview_var.get())
        credit_preview = normalize_text(credit_preview_var.get())
        journal = normalize_text(journal_code_var.get())
        for index in selection:
            if index < 0 or index >= len(draft_rows):
                continue
            row = draft_rows[index]
            row["target_mode"] = target_mode_value
            row["posting_mode"] = posting_mode_value
            row["debit_account_code"] = debit_code
            row["credit_account_code"] = credit_code
            row["debit_account_preview"] = debit_preview
            row["credit_account_preview"] = credit_preview
            if journal:
                row["journal_code"] = journal
            apply_generated_texts_to_row(row, target_mode_value)
            update_row_list_entry(index)
        self.status_var.set(f"Semua setting diterapkan ke {len(selection)} row terpilih.")

    def execute_repair() -> None:
        if getattr(self, "_busy", False):
            messagebox.showwarning(self._display_name, "Masih ada proses repair lain yang sedang berjalan.")
            return
        save_current_row()
        rows: list[SvlDashboardRepairRow] = []
        incomplete_rows: list[dict[str, Any]] = []
        for row in draft_rows:
            debit_code = normalize_text(row.get("debit_account_code"))
            credit_code = normalize_text(row.get("credit_account_code"))
            if not debit_code or not credit_code:
                incomplete_rows.append(row)
                continue
            try:
                amount = float(row.get("amount") or 0.0)
            except (TypeError, ValueError):
                amount = 0.0
            if amount <= 0:
                messagebox.showwarning(self._display_name, "Nominal repair harus lebih besar dari 0.")
                return
            clean_date = normalize_text(row.get("date"))
            if not clean_date:
                messagebox.showwarning(self._display_name, "Tanggal repair wajib diisi.")
                return
            rows.append(
                SvlDashboardRepairRow(
                    row_key=normalize_text(row.get("row_key")),
                    company_id=int(row.get("company_id") or company_id),
                    company_name=normalize_text(row.get("company_name")) or self._selected_company_name(),
                    item_product_id=int(row.get("item_product_id") or 0),
                    item_code=normalize_text(row.get("item_code")),
                    item_name=normalize_text(row.get("item_name")),
                    amount=amount,
                    date=clean_date,
                    reference=normalize_text(row.get("reference")),
                    line_label=normalize_text(row.get("line_label")),
                    journal_code=normalize_text(row.get("journal_code")),
                    debit_account_code=debit_code,
                    credit_account_code=credit_code,
                    svl_id=int(row.get("svl_id") or 0),
                    move_id=int(row.get("move_id") or 0),
                    move_name=normalize_text(row.get("move_name")),
                    move_state=normalize_text(row.get("move_state")),
                    target_mode=target_mode_label_to_value.get(target_mode_var.get(), target_options[0][1]),
                    posting_mode=posting_mode_label_to_value.get(posting_mode_var.get(), posting_options[0][1]),
                    signed_amount=float(row.get("signed_amount") or 0.0),
                    base_reference=normalize_text(row.get("base_reference")),
                    base_line_label=normalize_text(row.get("base_line_label")),
                    svl_reference=normalize_text(row.get("svl_reference")),
                    repair_source_kind=normalize_text(row.get("repair_source_kind")),
                    repair_source_label=normalize_text(row.get("repair_source_label")),
                    account_candidates=list(row.get("account_candidates") or []),
                )
            )
        if incomplete_rows:
            incomplete_preview = "\n".join(self._repair_row_preview(row) for row in incomplete_rows[:8])
            if len(incomplete_rows) > 8:
                incomplete_preview += f"\n... dan {len(incomplete_rows) - 8} row lainnya."
            proceed = messagebox.askyesno(
                self._display_name,
                f"{len(incomplete_rows)} row belum punya debit/kredit lengkap.\n\n"
                f"{incomplete_preview}\n\n"
                "Lanjutkan hanya row yang sudah valid?",
            )
            if not proceed:
                return
        if not rows:
            messagebox.showwarning(self._display_name, "Belum ada row valid untuk dijalankan.")
            return
        if incomplete_rows:
            draft_rows[:] = list(incomplete_rows)
            self._start_repair(rows)
            reload_row_list(0)
            messagebox.showinfo(
                self._display_name,
                f"{len(rows)} row valid dijalankan.\n"
                f"{len(incomplete_rows)} row masih perlu COA dan tetap ada di dialog ini.",
            )
            return
        dialog.destroy()
        self._start_repair(rows)

    check_accounts_button = tk.Button(
        action_row,
        text="Check Accounts",
        bg=T.BRAND_SECONDARY,
        fg=T.TEXT_ON_DARK,
        activebackground=T.BRAND_ACCENT,
        activeforeground=T.TEXT_ON_DARK,
        relief="flat",
        padx=14,
        pady=6,
        command=check_accounts,
    )
    check_accounts_button.pack(side="left")
    apply_coa_button = tk.Button(
        action_row,
        text="Apply COA to Selected",
        bg=T.BG_INPUT,
        fg=T.TEXT_ON_LIGHT,
        relief="flat",
        padx=14,
        pady=6,
        command=apply_coa_to_selected,
    )
    apply_coa_button.pack(side="left", padx=(8, 0))
    apply_all_button = tk.Button(
        action_row,
        text="Apply All to Selected",
        bg=T.BG_INPUT,
        fg=T.TEXT_ON_LIGHT,
        relief="flat",
        padx=14,
        pady=6,
        command=apply_all_to_selected,
    )
    apply_all_button.pack(side="left", padx=(8, 0))
    run_repair_button = tk.Button(
        action_row,
        text="Run Repair",
        bg=T.BRAND_PRIMARY,
        fg=T.TEXT_ON_DARK,
        activebackground=T.BRAND_PRIMARY_DARK,
        activeforeground=T.TEXT_ON_DARK,
        relief="flat",
        padx=18,
        pady=6,
        command=execute_repair,
    )
    run_repair_button.pack(side="left", padx=(8, 0))
    cancel_button = tk.Button(
        action_row,
        text="Cancel",
        bg=T.BG_INPUT,
        fg=T.TEXT_ON_LIGHT,
        relief="flat",
        padx=14,
        pady=6,
        command=dialog.destroy,
    )
    cancel_button.pack(side="right")

    target_combo.bind(
        "<<ComboboxSelected>>",
        lambda _event: apply_target_mode_defaults(
            target_mode_label_to_value.get(target_mode_var.get(), target_options[0][1])
        ),
    )
    row_list.bind("<<ListboxSelect>>", on_row_selected)
    dialog.bind("<Escape>", lambda _event: dialog.destroy())
    self._last_repair_dialog_widgets = {
        "dialog": dialog,
        "left_list": row_list,
        "left_scrollbar": list_scroll,
        "left_panel": left,
        "source_label_var": source_var,
        "target_combo": target_combo,
        "posting_combo": posting_combo,
        "debit_var": debit_account_var,
        "credit_var": credit_account_var,
        "debit_preview_var": debit_preview_var,
        "credit_preview_var": credit_preview_var,
        "amount_var": amount_var,
        "date_var": date_var,
        "reference_var": reference_var,
        "line_label_var": line_label_var,
        "journal_code_var": journal_code_var,
        "draft_rows": draft_rows,
        "save_current_row": save_current_row,
        "load_row": load_row,
        "select_row": select_row,
        "right_scroll": right_scroll,
        "right_canvas": right_scroll.canvas,
        "right_scrollbar": right_scroll.scrollbar,
        "right_body": right,
        "footer": footer,
        "action_row": action_row,
        "check_accounts_button": check_accounts_button,
        "apply_coa_button": apply_coa_button,
        "apply_all_button": apply_all_button,
        "run_repair_button": run_repair_button,
        "cancel_button": cancel_button,
        "debit_candidates": debit_picker.candidate_listbox,
        "credit_candidates": credit_picker.candidate_listbox,
    }
    reload_row_list(0)


def _open_repair_dialog_for_rows_v2(
    self,
    draft_rows: list[dict[str, Any]],
    *,
    source_title: str,
    source_note: str,
) -> None:
    company_id = self._selected_company_id()
    if company_id <= 0:
        messagebox.showwarning(self._display_name, "Pilih company terlebih dahulu.")
        return
    if not draft_rows:
        return

    is_repair_collection = (
        normalize_text(source_title).lower() == "repair collection"
        and hasattr(self, "_repair_collection")
        and bool(getattr(self, "_repair_collection", None))
    )
    source_kind = "repair_collection" if is_repair_collection else "selected_rows"
    existing_dialog = self._last_repair_dialog_widgets.get("dialog")
    if existing_dialog is not None:
        try:
            if bool(existing_dialog.winfo_exists()):
                existing_dialog.destroy()
        except Exception:  # noqa: BLE001
            pass

    if source_kind == "repair_collection":
        draft_rows = [entry.draft for entry in self._repair_collection.values()]
    else:
        draft_rows = [self._build_collection_repair_draft(dict(row)) for row in draft_rows]
    if not draft_rows:
        return
    for row in draft_rows:
        self._ensure_repair_row_defaults(row)

    target_options = self._repair_target_options(draft_rows)
    target_mode_label_to_value = {"Belum dipilih": "", **{label: value for label, value in target_options}}
    target_mode_value_to_label = {value: label for label, value in target_mode_label_to_value.items()}
    posting_mode_label_to_value = {"Belum dipilih": "", "Draft Only": "draft", "Auto Post": "post"}
    posting_mode_value_to_label = {value: label for label, value in posting_mode_label_to_value.items()}
    role_label_to_value = {"Belum dipilih": "", **{label: value for label, value in REPAIR_TARGET_ACCOUNT_ROLE_OPTIONS}}
    role_value_to_label = {value: label for label, value in role_label_to_value.items()}
    resolve_candidates = self._configured_repair_account_candidates()
    remembered_defaults = self._repair_default_settings_payload()

    dialog = tk.Toplevel(self.root)
    dialog.title("Repair Journal Dashboard")
    dialog.transient(self.root)
    dialog.grab_set()
    dialog.configure(bg=T.BG_CARD)
    dialog.geometry("1120x700")
    dialog.minsize(980, 620)
    dialog.rowconfigure(0, weight=1)
    dialog.columnconfigure(0, weight=1)

    default_split_ratio = 0.6
    min_left_width = 420
    min_right_width = 320
    split_state = {"manual_ratio": None, "sync_pending": False}

    content_pane = tk.PanedWindow(
        dialog,
        orient="horizontal",
        sashrelief="flat",
        sashwidth=8,
        bg=T.BG_CARD,
        bd=0,
        opaqueresize=True,
    )
    content_pane.grid(row=0, column=0, sticky="nsew")

    left = tk.Frame(content_pane, bg=T.BG_CARD, padx=12, pady=12)
    left.grid_propagate(False)
    left.columnconfigure(0, weight=1)
    left.rowconfigure(2, weight=1)
    tk.Label(
        left,
        text=source_title,
        bg=T.BG_CARD,
        fg=T.TEXT_ON_LIGHT,
        font=T.font(T.FONT_BODY_SIZE, bold=True),
    ).grid(row=0, column=0, sticky="w", pady=(0, 8))
    source_var = tk.StringVar(value=self._repair_dialog_source_note(source_note, draft_rows, mixed_only=source_kind == "repair_collection"))
    tk.Label(
        left,
        textvariable=source_var,
        bg=T.BG_CARD,
        fg=T.TEXT_MUTED,
        font=T.font(T.FONT_SMALL_SIZE),
        justify="left",
        anchor="w",
        wraplength=420,
    ).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 8))

    row_tree = ttk.Treeview(
        left,
        columns=("svl_id", "svl_date", "qty", "unit_cost", "value", "reference", "linked_move"),
        show="tree headings",
        selectmode="extended",
        height=24,
    )
    row_tree.grid(row=2, column=0, sticky="nsew")
    row_tree.heading("#0", text="ITEM")
    row_tree.column("#0", width=280, minwidth=220, anchor="w", stretch=True)
    row_tree.heading("svl_id", text="SVL ID")
    row_tree.heading("svl_date", text="SVL DATE")
    row_tree.heading("qty", text="QTY")
    row_tree.heading("unit_cost", text="UNIT COST")
    row_tree.heading("value", text="VALUE")
    row_tree.heading("reference", text="REFERENCE")
    row_tree.heading("linked_move", text="LINKED MOVE")
    row_tree.column("svl_id", width=78, minwidth=70, anchor="w", stretch=False)
    row_tree.column("svl_date", width=96, minwidth=92, anchor="w", stretch=False)
    row_tree.column("qty", width=82, minwidth=78, anchor="e", stretch=False)
    row_tree.column("unit_cost", width=96, minwidth=92, anchor="e", stretch=False)
    row_tree.column("value", width=108, minwidth=100, anchor="e", stretch=False)
    row_tree.column("reference", width=180, minwidth=140, anchor="w", stretch=True)
    row_tree.column("linked_move", width=180, minwidth=140, anchor="w", stretch=True)
    row_tree.tag_configure("repair_group", background="#eef1f5")
    row_tree.tag_configure("repair_row_ready", background="#edf7ee")
    row_tree.tag_configure("repair_row_incomplete", background="#fff6d9")
    row_tree.tag_configure("repair_row_needs_review", background="#fff0d8")
    row_tree.tag_configure("repair_row_running", background="#eaf2fb")
    row_tree.tag_configure("repair_row_failed", background="#fcebea")
    row_tree.tag_configure("repair_row_repaired", background="#e8f6ef")
    row_tree = bind_treeview_scroll_support(row_tree)
    list_scroll = ttk.Scrollbar(left, orient="vertical", command=row_tree.yview)
    list_scroll.grid(row=2, column=1, sticky="ns")
    row_tree.configure(yscrollcommand=list_scroll.set)

    select_btns_frame = tk.Frame(left, bg=T.BG_CARD)
    select_btns_frame.grid(row=3, column=0, columnspan=2, sticky="w", pady=(4, 0))
    row_tree_meta: dict[str, dict[str, Any]] = {}
    group_item_ids: list[str] = []
    group_item_id_by_source_label: dict[str, str] = {}
    leaf_item_by_index: dict[int, str] = {}
    index_by_item_id: dict[str, int] = {}
    selection_state = {"virtual_all": False}

    select_all_button = tk.Button(
        select_btns_frame,
        text="Select All",
        bg=T.BG_INPUT,
        fg=T.TEXT_ON_LIGHT,
        font=T.font(T.FONT_SMALL_SIZE),
        relief="flat",
        padx=8,
        pady=3,
    )
    select_all_button.pack(side="left")
    deselect_all_button = tk.Button(
        select_btns_frame,
        text="Deselect All",
        bg=T.BG_INPUT,
        fg=T.TEXT_ON_LIGHT,
        font=T.font(T.FONT_SMALL_SIZE),
        relief="flat",
        padx=8,
        pady=3,
    )
    deselect_all_button.pack(side="left", padx=(4, 0))
    selection_summary_var = tk.StringVar(value="No rows selected")
    tk.Label(
        select_btns_frame,
        textvariable=selection_summary_var,
        bg=T.BG_CARD,
        fg=T.TEXT_MUTED,
        font=T.font(T.FONT_SMALL_SIZE),
        anchor="w",
        justify="left",
    ).pack(side="left", padx=(10, 0))

    right_host = tk.Frame(content_pane, bg=T.BG_CARD)
    right_host.grid_propagate(False)
    right_host.columnconfigure(0, weight=1)
    right_host.rowconfigure(0, weight=1)

    content_pane.add(left, minsize=min_left_width)
    content_pane.add(right_host, minsize=min_right_width)

    right_scroll = ScrollableFrame(right_host, bg=T.BG_CARD)
    right_scroll.grid(row=0, column=0, sticky="nsew")
    right = tk.Frame(right_scroll.interior, bg=T.BG_CARD, padx=12, pady=12)
    right.pack(fill="both", expand=True)
    right.columnconfigure(1, weight=1)

    footer = tk.Frame(right_host, bg=T.BG_CARD, padx=12, pady=0)
    footer.grid(row=1, column=0, sticky="ew", pady=(0, 12))
    repair_progress_value_var = tk.DoubleVar(value=0.0)
    repair_progress_count_var = tk.StringVar(value="0 / 0")
    repair_progress_percent_var = tk.StringVar(value="0%")
    repair_progress_current_var = tk.StringVar(value="Belum ada proses repair.")
    repair_progress_meta_var = tk.StringVar(value="Berhasil 0 | Gagal 0")
    progress_row = tk.Frame(footer, bg=T.BG_CARD)
    progress_row.pack(fill="x", pady=(0, 6))
    progress_row.grid_columnconfigure(0, weight=13)
    progress_row.grid_columnconfigure(1, weight=7, minsize=REPAIR_PROGRESS_INFO_MIN_WIDTH)
    repair_progress_bar_host = tk.Frame(progress_row, bg=T.BG_CARD)
    repair_progress_bar_host.grid(row=0, column=0, sticky="ew")
    repair_progress_bar = ttk.Progressbar(
        repair_progress_bar_host,
        variable=repair_progress_value_var,
        maximum=100,
        length=REPAIR_PROGRESS_BAR_MIN_WIDTH,
    )
    repair_progress_bar.pack(fill="x")
    repair_progress_info_host = tk.Frame(progress_row, bg=T.BG_CARD)
    repair_progress_info_host.grid(row=0, column=1, sticky="ew", padx=(12, 0))
    repair_progress_info_host.grid_columnconfigure(1, weight=1)
    repair_progress_count_label = tk.Label(
        repair_progress_info_host,
        textvariable=repair_progress_count_var,
        bg=T.BG_CARD,
        fg=T.TEXT_ON_LIGHT,
        font=T.font(),
    )
    repair_progress_count_label.grid(row=0, column=0, sticky="nw")
    repair_progress_percent_label = tk.Label(
        repair_progress_info_host,
        textvariable=repair_progress_percent_var,
        bg=T.BG_CARD,
        fg=T.TEXT_ON_LIGHT,
        font=T.font(),
    )
    repair_progress_percent_label.grid(row=0, column=1, sticky="nw", padx=(8, 0))
    repair_progress_current_label = tk.Label(
        repair_progress_info_host,
        textvariable=repair_progress_current_var,
        bg=T.BG_CARD,
        fg=T.TEXT_MUTED,
        font=T.font(T.FONT_SMALL_SIZE),
        anchor="w",
        justify="left",
        wraplength=PROGRESS_TEXT_WRAP_MIN_WIDTH,
    )
    repair_progress_current_label.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(2, 0))
    self._bind_progress_row_layout(
        progress_row,
        repair_progress_bar_host,
        repair_progress_bar,
        repair_progress_info_host,
        repair_progress_current_label,
        repair_progress_count_label,
        repair_progress_percent_label,
        width_resolver=self._repair_progress_row_widths,
        left_weight=13,
        right_weight=7,
        measure_width=lambda: self._priority_widget_width(progress_row, footer, right_host),
        watch_widgets=(right_host,),
    )
    tk.Label(
        footer,
        textvariable=repair_progress_meta_var,
        bg=T.BG_CARD,
        fg=T.TEXT_MUTED,
        font=T.font(T.FONT_SMALL_SIZE),
        anchor="w",
        justify="left",
    ).pack(fill="x", pady=(0, 8))
    action_row = tk.Frame(footer, bg=T.BG_CARD)
    action_row.pack(fill="x")

    def measured_split_width() -> int:
        current_width = max(int(content_pane.winfo_width() or 0), int(dialog.winfo_width() or 0))
        if current_width > 1:
            return current_width
        configured_width = int(content_pane.cget("width") or 0)
        if configured_width > 1:
            return configured_width
        geometry_text = normalize_text(dialog.wm_geometry() or dialog.geometry())
        geometry_match = re.match(r"^(?P<width>\d+)x(?P<height>\d+)", geometry_text)
        if geometry_match is not None:
            geometry_width = int(geometry_match.group("width") or 0)
            if geometry_width > 1:
                return geometry_width
        requested_width = max(int(content_pane.winfo_reqwidth() or 0), int(dialog.winfo_reqwidth() or 0))
        if requested_width > 1:
            return requested_width
        return min_left_width + min_right_width

    def sync_split_layout() -> None:
        split_state["sync_pending"] = False
        if len(content_pane.panes()) < 2:
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
        content_pane.configure(width=pane_width)
        left.configure(width=target_x)
        right_host.configure(width=right_width)
        try:
            current_x = int(content_pane.sash_coord(0)[0])
        except Exception:  # noqa: BLE001
            current_x = -1
        if current_x != target_x:
            try:
                content_pane.sash_place(0, target_x, 0)
            except Exception:  # noqa: BLE001
                return

    def request_split_sync() -> None:
        if split_state["sync_pending"]:
            return
        split_state["sync_pending"] = True
        dialog.after_idle(sync_split_layout)

    def remember_current_split_ratio() -> None:
        if len(content_pane.panes()) < 2:
            return
        pane_width = measured_split_width()
        if pane_width <= 0:
            return
        try:
            sash_x = int(content_pane.sash_coord(0)[0])
        except Exception:  # noqa: BLE001
            return
        min_ratio = min_left_width / max(1, pane_width)
        max_ratio = max(min_ratio, (pane_width - min_right_width) / max(1, pane_width))
        split_state["manual_ratio"] = max(min_ratio, min(max_ratio, sash_x / max(1, pane_width)))

    def on_content_pane_configure(_event: tk.Event | None = None) -> None:
        request_split_sync()

    def on_dialog_configure(event: tk.Event | None = None) -> None:
        if event is not None and event.widget is not dialog:
            return
        request_split_sync()

    def on_content_pane_button_release(_event: tk.Event | None = None) -> None:
        remember_current_split_ratio()

    content_pane.bind("<Configure>", on_content_pane_configure, add="+")
    content_pane.bind("<ButtonRelease-1>", on_content_pane_button_release, add="+")
    dialog.bind("<Configure>", on_dialog_configure, add="+")

    global_target_var = tk.StringVar(
        value=target_mode_value_to_label.get(normalize_text(remembered_defaults["target_mode"]), "Belum dipilih")
    )
    global_posting_var = tk.StringVar(
        value=posting_mode_value_to_label.get(normalize_text(remembered_defaults["posting_mode"]), "Belum dipilih")
    )
    global_role_var = tk.StringVar(
        value=role_value_to_label.get(normalize_text(remembered_defaults["target_account_role"]), "Belum dipilih")
    )
    global_resolve_var = tk.StringVar(value=normalize_text(remembered_defaults["resolve_account_code"]).upper())
    global_resolve_preview_var = tk.StringVar(value=self._lookup_repair_candidate_preview(global_resolve_var.get(), resolve_candidates))

    row_status_var = tk.StringVar(value="")
    source_status_var = tk.StringVar(value="-")
    source_svl_id_var = tk.StringVar(value="-")
    source_svl_date_var = tk.StringVar(value="-")
    source_svl_qty_var = tk.StringVar(value="0.00")
    source_svl_unit_cost_var = tk.StringVar(value="0.00")
    source_svl_value_var = tk.StringVar(value="0.00")
    source_svl_reference_var = tk.StringVar(value="-")
    source_linked_move_var = tk.StringVar(value="-")
    row_target_var = tk.StringVar(value="Belum dipilih")
    row_posting_var = tk.StringVar(value="Belum dipilih")
    row_role_var = tk.StringVar(value="Belum dipilih")
    row_resolve_var = tk.StringVar(value="")
    row_resolve_preview_var = tk.StringVar(value="Belum dipilih")
    target_account_code_var = tk.StringVar(value="")
    target_account_preview_var = tk.StringVar(value="Belum dipilih")
    debit_code_var = tk.StringVar(value="")
    debit_preview_var = tk.StringVar(value="Belum dipilih")
    credit_code_var = tk.StringVar(value="")
    credit_preview_var = tk.StringVar(value="Belum dipilih")
    amount_var = tk.StringVar(value="")
    date_var = tk.StringVar(value="")
    reference_var = tk.StringVar(value="")
    line_label_var = tk.StringVar(value="")
    journal_code_var = tk.StringVar(value="")
    target_warning_var = tk.StringVar(value="")
    current_index = {"value": 0}
    selection_guard = {"active": False}
    dialog_state = {"repair_running": False, "bulk_apply_running": False}
    bulk_apply_state: dict[str, Any] = {"after_id": None}

    tk.Label(
        right,
        text="Global Repair Defaults",
        bg=T.BG_CARD,
        fg=T.TEXT_ON_LIGHT,
        font=T.font(T.FONT_BODY_SIZE, bold=True),
    ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
    tk.Label(right, text="Target:", bg=T.BG_CARD, fg=T.TEXT_ON_LIGHT, font=T.font()).grid(row=1, column=0, sticky="w", pady=4)
    global_target_combo = ttk.Combobox(
        right,
        state="readonly",
        textvariable=global_target_var,
        values=list(target_mode_label_to_value.keys()),
    )
    global_target_combo.grid(row=1, column=1, sticky="ew", pady=4)
    tk.Label(right, text="Posting:", bg=T.BG_CARD, fg=T.TEXT_ON_LIGHT, font=T.font()).grid(row=2, column=0, sticky="w", pady=4)
    global_posting_combo = ttk.Combobox(
        right,
        state="readonly",
        textvariable=global_posting_var,
        values=list(posting_mode_label_to_value.keys()),
    )
    global_posting_combo.grid(row=2, column=1, sticky="ew", pady=4)
    tk.Label(right, text="Target Account Type:", bg=T.BG_CARD, fg=T.TEXT_ON_LIGHT, font=T.font()).grid(row=3, column=0, sticky="w", pady=4)
    global_role_combo = ttk.Combobox(
        right,
        state="readonly",
        textvariable=global_role_var,
        values=list(role_label_to_value.keys()),
    )
    global_role_combo.grid(row=3, column=1, sticky="ew", pady=4)
    tk.Label(right, text="Resolve Account:", bg=T.BG_CARD, fg=T.TEXT_ON_LIGHT, font=T.font()).grid(row=4, column=0, sticky="w", pady=4)
    global_resolve_picker = _RepairAccountPicker(
        right,
        variable=global_resolve_var,
        filter_fn=self._filter_repair_account_candidates,
        on_change=lambda: global_resolve_preview_var.set(
            self._lookup_repair_candidate_preview(global_resolve_var.get(), resolve_candidates)
        ),
    )
    global_resolve_picker.grid(row=4, column=1, sticky="ew", pady=4)
    global_resolve_picker.set_candidates(resolve_candidates)
    tk.Label(
        right,
        textvariable=global_resolve_preview_var,
        bg=T.BG_CARD,
        fg=T.TEXT_MUTED,
        font=T.font(T.FONT_SMALL_SIZE),
    ).grid(row=5, column=1, sticky="w", pady=(0, 8))

    tk.Label(
        right,
        text="Row Editor",
        bg=T.BG_CARD,
        fg=T.TEXT_ON_LIGHT,
        font=T.font(T.FONT_BODY_SIZE, bold=True),
    ).grid(row=6, column=0, columnspan=2, sticky="w", pady=(12, 8))
    tk.Label(
        right,
        textvariable=row_status_var,
        bg=T.BG_CARD,
        fg=T.TEXT_MUTED,
        font=T.font(T.FONT_SMALL_SIZE, bold=True),
        justify="left",
        anchor="w",
        wraplength=560,
    ).grid(row=7, column=0, columnspan=2, sticky="ew", pady=(0, 8))
    tk.Label(
        right,
        text="Source Detail",
        bg=T.BG_CARD,
        fg=T.TEXT_ON_LIGHT,
        font=T.font(T.FONT_SMALL_SIZE, bold=True),
    ).grid(row=8, column=0, columnspan=2, sticky="w", pady=(0, 6))

    def source_detail_label(row_index: int, label: str, variable: tk.StringVar) -> None:
        tk.Label(right, text=label, bg=T.BG_CARD, fg=T.TEXT_ON_LIGHT, font=T.font()).grid(
            row=row_index, column=0, sticky="w", pady=2
        )
        tk.Label(
            right,
            textvariable=variable,
            bg=T.BG_CARD,
            fg=T.TEXT_MUTED,
            font=T.font(T.FONT_SMALL_SIZE),
            justify="left",
            anchor="w",
            wraplength=560,
        ).grid(row=row_index, column=1, sticky="ew", pady=2)

    source_detail_label(9, "Source Status:", source_status_var)
    source_detail_label(10, "SVL ID:", source_svl_id_var)
    source_detail_label(11, "Tanggal SVL:", source_svl_date_var)
    source_detail_label(12, "Qty:", source_svl_qty_var)
    source_detail_label(13, "Unit Cost:", source_svl_unit_cost_var)
    source_detail_label(14, "Nilai SVL:", source_svl_value_var)
    source_detail_label(15, "Reference SVL:", source_svl_reference_var)
    source_detail_label(16, "Linked Move:", source_linked_move_var)

    tk.Label(right, text="Target:", bg=T.BG_CARD, fg=T.TEXT_ON_LIGHT, font=T.font()).grid(row=17, column=0, sticky="w", pady=4)
    target_combo = ttk.Combobox(
        right,
        state="readonly",
        textvariable=row_target_var,
        values=list(target_mode_label_to_value.keys()),
    )
    target_combo.grid(row=17, column=1, sticky="ew", pady=4)
    tk.Label(right, text="Posting:", bg=T.BG_CARD, fg=T.TEXT_ON_LIGHT, font=T.font()).grid(row=18, column=0, sticky="w", pady=4)
    posting_combo = ttk.Combobox(
        right,
        state="readonly",
        textvariable=row_posting_var,
        values=list(posting_mode_label_to_value.keys()),
    )
    posting_combo.grid(row=18, column=1, sticky="ew", pady=4)
    tk.Label(right, text="Target Account Type:", bg=T.BG_CARD, fg=T.TEXT_ON_LIGHT, font=T.font()).grid(row=19, column=0, sticky="w", pady=4)
    role_combo = ttk.Combobox(
        right,
        state="readonly",
        textvariable=row_role_var,
        values=list(role_label_to_value.keys()),
    )
    role_combo.grid(row=19, column=1, sticky="ew", pady=4)
    tk.Label(right, text="Resolve Account:", bg=T.BG_CARD, fg=T.TEXT_ON_LIGHT, font=T.font()).grid(row=20, column=0, sticky="w", pady=4)
    resolve_picker = _RepairAccountPicker(
        right,
        variable=row_resolve_var,
        filter_fn=self._filter_repair_account_candidates,
        on_change=lambda: row_resolve_preview_var.set(
            self._lookup_repair_candidate_preview(row_resolve_var.get(), resolve_candidates)
        ),
    )
    resolve_picker.grid(row=20, column=1, sticky="ew", pady=4)
    resolve_picker.set_candidates(resolve_candidates)
    tk.Label(
        right,
        textvariable=row_resolve_preview_var,
        bg=T.BG_CARD,
        fg=T.TEXT_MUTED,
        font=T.font(T.FONT_SMALL_SIZE),
    ).grid(row=21, column=1, sticky="w", pady=(0, 6))
    tk.Label(right, text="Target Account:", bg=T.BG_CARD, fg=T.TEXT_ON_LIGHT, font=T.font()).grid(row=22, column=0, sticky="w", pady=4)
    tk.Label(
        right,
        textvariable=target_account_preview_var,
        bg=T.BG_CARD,
        fg=T.TEXT_MUTED,
        font=T.font(T.FONT_SMALL_SIZE),
        justify="left",
        anchor="w",
        wraplength=520,
    ).grid(row=22, column=1, sticky="ew", pady=4)

    def labeled_entry(row_index: int, label: str, variable: tk.StringVar, *, readonly: bool = False) -> tk.Entry:
        tk.Label(right, text=label, bg=T.BG_CARD, fg=T.TEXT_ON_LIGHT, font=T.font()).grid(
            row=row_index, column=0, sticky="w", pady=4
        )
        entry = tk.Entry(right, textvariable=variable, bg=T.BG_INPUT, relief="solid", bd=1)
        if readonly:
            entry.configure(state="readonly")
        entry.grid(row=row_index, column=1, sticky="ew", pady=4)
        return entry

    amount_entry = labeled_entry(27, "Nominal:", amount_var)
    date_entry = labeled_entry(28, "Tanggal:", date_var)
    reference_entry = labeled_entry(29, "Reference:", reference_var)
    line_label_entry = labeled_entry(30, "Label Line:", line_label_var)
    journal_entry = labeled_entry(31, "Journal Code:", journal_code_var)
    debit_entry = labeled_entry(23, "Akun Debit:", debit_code_var, readonly=True)
    tk.Label(
        right,
        textvariable=debit_preview_var,
        bg=T.BG_CARD,
        fg=T.TEXT_MUTED,
        font=T.font(T.FONT_SMALL_SIZE),
    ).grid(row=24, column=1, sticky="w", pady=(0, 6))
    credit_entry = labeled_entry(25, "Akun Kredit:", credit_code_var, readonly=True)
    tk.Label(
        right,
        textvariable=credit_preview_var,
        bg=T.BG_CARD,
        fg=T.TEXT_MUTED,
        font=T.font(T.FONT_SMALL_SIZE),
    ).grid(row=26, column=1, sticky="w", pady=(0, 6))
    tk.Label(
        right,
        textvariable=target_warning_var,
        bg=T.BG_CARD,
        fg=T.STATUS_WARNING,
        font=T.font(T.FONT_SMALL_SIZE),
        justify="left",
        anchor="w",
        wraplength=560,
    ).grid(row=32, column=0, columnspan=2, sticky="ew", pady=(8, 0))

    def persist_global_defaults(*_args: Any) -> None:
        self._save_repair_default_settings(
            target_mode=target_mode_label_to_value.get(global_target_var.get(), ""),
            posting_mode=posting_mode_label_to_value.get(global_posting_var.get(), ""),
            target_account_role=role_label_to_value.get(global_role_var.get(), ""),
            resolve_account_code=normalize_text(global_resolve_var.get()).upper(),
        )
        global_resolve_preview_var.set(self._lookup_repair_candidate_preview(global_resolve_var.get(), resolve_candidates))

    def repair_virtual_select_threshold() -> int:
        return max(
            1,
            int(
                getattr(
                    self,
                    "_repair_virtual_select_all_threshold",
                    REPAIR_VIRTUAL_SELECT_ALL_THRESHOLD,
                )
                or REPAIR_VIRTUAL_SELECT_ALL_THRESHOLD
            ),
        )

    def clear_virtual_selection() -> None:
        selection_state["virtual_all"] = False

    def has_virtual_selection() -> bool:
        return bool(selection_state["virtual_all"] and draft_rows)

    def current_selection_item_ids() -> tuple[str, ...]:
        return tuple(
            item_id
            for item_id in row_tree.selection()
            if not bool(row_tree_meta.get(item_id, {}).get("group_header"))
        )

    def current_physical_selection_indices() -> tuple[int, ...]:
        return tuple(
            sorted(
                index_by_item_id[item_id]
                for item_id in current_selection_item_ids()
                if item_id in index_by_item_id
            )
        )

    def iter_selected_indices() -> range | tuple[int, ...]:
        if has_virtual_selection():
            return range(len(draft_rows))
        return current_physical_selection_indices()

    def current_selection_indices() -> tuple[int, ...]:
        selected = iter_selected_indices()
        return tuple(selected) if isinstance(selected, range) else selected

    def current_selection_count() -> int:
        if has_virtual_selection():
            return len(draft_rows)
        return len(current_physical_selection_indices())

    def current_selection_snapshot() -> tuple[int, ...] | str:
        if has_virtual_selection():
            return REPAIR_DIALOG_SELECTION_ALL
        return current_physical_selection_indices()

    def refresh_selection_summary() -> None:
        selection_count = current_selection_count()
        if has_virtual_selection() and selection_count > 0:
            selection_summary_var.set(f"All {selection_count:,} rows selected (virtual)")
            return
        if selection_count <= 0:
            selection_summary_var.set("No rows selected")
            return
        if selection_count == 1:
            selection_summary_var.set("1 row selected")
            return
        selection_summary_var.set(f"{selection_count:,} rows selected")

    def current_focus_index(default_index: int = 0) -> int:
        focus_item = row_tree.focus()
        if focus_item in index_by_item_id:
            return index_by_item_id[focus_item]
        selection = current_physical_selection_indices()
        if selection:
            return int(selection[0])
        if not draft_rows:
            return 0
        return max(0, min(default_index, len(draft_rows) - 1))

    def refresh_group_header(source_label: str) -> None:
        group_item_id = group_item_id_by_source_label.get(source_label, "")
        if not group_item_id:
            return
        try:
            if not row_tree.exists(group_item_id):
                return
        except Exception:  # noqa: BLE001
            return
        grouped_rows = [row for row in draft_rows if self._repair_source_label(row) == source_label]
        row_tree.item(group_item_id, text=self._repair_group_header_text(source_label, grouped_rows))

    def update_row_list_entry(index: int, *, refresh_group: bool = True, allow_reload: bool = True) -> None:
        if index < 0 or index >= len(draft_rows):
            return
        item_id = leaf_item_by_index.get(index, "")
        if not item_id:
            if allow_reload:
                reload_row_list(current_focus_index(index), preserve_selection=current_selection_snapshot())
            return
        try:
            if not row_tree.exists(item_id):
                if allow_reload:
                    reload_row_list(current_focus_index(index), preserve_selection=current_selection_snapshot())
                return
        except Exception:  # noqa: BLE001
            if allow_reload:
                reload_row_list(current_focus_index(index), preserve_selection=current_selection_snapshot())
            return
        row = draft_rows[index]
        source_label = self._repair_source_label(row)
        existing_source_label = normalize_text(row_tree_meta.get(item_id, {}).get("repair_source_label"))
        if existing_source_label and existing_source_label != source_label:
            if allow_reload:
                reload_row_list(current_focus_index(index), preserve_selection=current_selection_snapshot())
            return
        row_tree.item(
            item_id,
            text=self._repair_selector_row_text(row),
            values=self._repair_selector_row_values(row),
            tags=(self._repair_row_state_tag(row),),
        )
        if refresh_group and source_label:
            row_tree_meta.setdefault(item_id, {})["repair_source_label"] = source_label
            refresh_group_header(source_label)

    def reload_row_list(
        select_index: int = 0,
        *,
        preserve_selection: tuple[int, ...] | str | None = None,
    ) -> None:
        selection_guard["active"] = True
        preserve_virtual_selection = bool(has_virtual_selection() and preserve_selection is None)
        for item_id in row_tree.get_children():
            row_tree.delete(item_id)
        row_tree_meta.clear()
        group_item_ids.clear()
        group_item_id_by_source_label.clear()
        leaf_item_by_index.clear()
        index_by_item_id.clear()
        row_index_by_identity = {id(row): index for index, row in enumerate(draft_rows)}
        for source_label, grouped_rows in self._group_repair_rows_by_source(draft_rows):
            group_item_id = row_tree.insert(
                "",
                "end",
                text=self._repair_group_header_text(source_label, grouped_rows),
                open=True,
                tags=("repair_group",),
            )
            row_tree_meta[group_item_id] = {"group_header": True, "repair_source_label": source_label}
            group_item_ids.append(group_item_id)
            group_item_id_by_source_label[source_label] = group_item_id
            for row in grouped_rows:
                row_index = row_index_by_identity.get(id(row), 0)
                item_id = row_tree.insert(
                    group_item_id,
                    "end",
                    text=self._repair_selector_row_text(row),
                    values=self._repair_selector_row_values(row),
                    tags=(self._repair_row_state_tag(row),),
                )
                row_tree_meta[item_id] = {
                    "group_header": False,
                    "index": row_index,
                    "repair_source_label": source_label,
                }
                leaf_item_by_index[row_index] = item_id
                index_by_item_id[item_id] = row_index
        if draft_rows:
            safe_index = max(0, min(select_index, len(draft_rows) - 1))
            row_tree.selection_remove(row_tree.selection())
            focus_item = leaf_item_by_index.get(safe_index, "")
            if preserve_selection == REPAIR_DIALOG_SELECTION_ALL or preserve_virtual_selection:
                selection_state["virtual_all"] = True
                if not focus_item and leaf_item_by_index:
                    focus_item = leaf_item_by_index.get(min(leaf_item_by_index), "")
                if focus_item:
                    row_tree.selection_set(focus_item)
            else:
                clear_virtual_selection()
                preserved = preserve_selection if preserve_selection is not None else (safe_index,)
                selected_item_ids = tuple(
                    leaf_item_by_index[preserved_index]
                    for preserved_index in preserved
                    if 0 <= preserved_index < len(draft_rows) and preserved_index in leaf_item_by_index
                )
                if selected_item_ids:
                    row_tree.selection_set(selected_item_ids)
                elif preserve_selection is None and safe_index in leaf_item_by_index:
                    row_tree.selection_set(leaf_item_by_index[safe_index])
                if not focus_item and selected_item_ids:
                    focus_item = selected_item_ids[0]
            if focus_item:
                row_tree.focus(focus_item)
                row_tree.see(focus_item)
        selection_guard["active"] = False
        refresh_selection_summary()
        if draft_rows:
            load_row(safe_index)

    def dialog_has_busy_state() -> bool:
        return bool(dialog_state["repair_running"] or dialog_state["bulk_apply_running"])

    def set_row_editor_state(editable: bool) -> None:
        effective_editable = bool(editable and not dialog_has_busy_state())
        combo_state = "readonly" if effective_editable else "disabled"
        entry_state = "normal" if effective_editable else "readonly"
        target_combo.configure(state=combo_state)
        posting_combo.configure(state=combo_state)
        role_combo.configure(state=combo_state)
        resolve_picker.entry.configure(state="normal" if effective_editable else "disabled")
        amount_entry.configure(state=entry_state)
        date_entry.configure(state=entry_state)
        reference_entry.configure(state=entry_state)
        line_label_entry.configure(state=entry_state)
        journal_entry.configure(state=entry_state)
        check_accounts_button.configure(state="normal" if effective_editable else "disabled")

    def refresh_dialog_busy_state() -> None:
        busy = dialog_has_busy_state()
        combo_state = "disabled" if busy else "readonly"
        global_target_combo.configure(state=combo_state)
        global_posting_combo.configure(state=combo_state)
        global_role_combo.configure(state=combo_state)
        global_resolve_picker.entry.configure(state="disabled" if busy else "normal")
        apply_global_button.configure(state="disabled" if busy else "normal")
        repair_selected_button.configure(state="disabled" if busy else "normal")
        repair_all_button.configure(state="disabled" if busy else "normal")
        select_all_button.configure(state="disabled" if busy else "normal")
        deselect_all_button.configure(state="disabled" if busy else "normal")
        cancel_button.configure(state="disabled" if busy else "normal")
        if draft_rows:
            current_row = draft_rows[int(current_index["value"])]
            set_row_editor_state(self._repair_row_is_editable(current_row))

    def set_dialog_busy_state(busy: bool) -> None:
        dialog_state["repair_running"] = bool(busy)
        refresh_dialog_busy_state()

    def set_bulk_apply_running_state(running: bool) -> None:
        dialog_state["bulk_apply_running"] = bool(running)
        refresh_dialog_busy_state()

    def save_current_row(*_args: Any, notify_collection: bool = True) -> None:
        index = int(current_index["value"])
        if index < 0 or index >= len(draft_rows):
            return
        row = draft_rows[index]
        if not self._repair_row_is_editable(row):
            return
        previous_target_mode = normalize_text(row.get("target_mode"))
        row["amount"] = self._parse_amount_text(amount_var.get())
        row["date"] = normalize_text(date_var.get()) or self._today_text()
        row["target_mode"] = target_mode_label_to_value.get(row_target_var.get(), "")
        row["posting_mode"] = posting_mode_label_to_value.get(row_posting_var.get(), "")
        row["target_account_role"] = role_label_to_value.get(row_role_var.get(), "")
        row["resolve_account_code"] = normalize_text(row_resolve_var.get()).upper()
        row["journal_code"] = normalize_text(journal_code_var.get())
        generated_reference = ""
        generated_line_label = ""
        if normalize_text(row.get("target_mode")) != previous_target_mode:
            row["reference"], row["line_label"] = self._repair_generated_texts_for_row(row)
            self._set_repair_generated_flags(row, reference_generated=True, line_label_generated=True)
        else:
            row["reference"] = normalize_text(reference_var.get())
            row["line_label"] = normalize_text(line_label_var.get())
            if normalize_text(row.get("target_mode")):
                generated_reference, generated_line_label = self._repair_generated_texts_for_row(row)
                if not normalize_text(row.get("reference")):
                    row["reference"] = generated_reference
                    row["reference_generated"] = True
                if not normalize_text(row.get("line_label")):
                    row["line_label"] = generated_line_label
                    row["line_label_generated"] = True
                if normalize_text(row.get("reference")):
                    row["reference_generated"] = normalize_text(row.get("reference")) == generated_reference
                if normalize_text(row.get("line_label")):
                    row["line_label_generated"] = normalize_text(row.get("line_label")) == generated_line_label
            else:
                self._set_repair_generated_flags(row, reference_generated=False, line_label_generated=False)
        self._sync_repair_row_account_codes(row)
        self._refresh_repair_row_state(row)
        target_account_code_var.set(normalize_text(row.get("target_account_code")))
        target_account_preview_var.set(normalize_text(row.get("target_account_preview")))
        row_resolve_preview_var.set(normalize_text(row.get("resolve_account_preview")))
        debit_code_var.set(normalize_text(row.get("debit_account_code")))
        debit_preview_var.set(normalize_text(row.get("debit_account_preview")))
        credit_code_var.set(normalize_text(row.get("credit_account_code")))
        credit_preview_var.set(normalize_text(row.get("credit_account_preview")))
        reference_var.set(normalize_text(row.get("reference")))
        line_label_var.set(normalize_text(row.get("line_label")))
        target_warning_var.set(
            self._repair_target_warning_text(normalize_text(row.get("target_mode")), [row])
            if normalize_text(row.get("target_mode"))
            else ""
        )
        row_status_label = self._repair_row_status_label(row)
        row_status_detail = normalize_text(row.get("row_status_message"))
        row_status_var.set(f"Status: {row_status_label}" + (f" | {row_status_detail}" if row_status_detail else ""))
        update_row_list_entry(index)
        if source_kind == "repair_collection" and notify_collection:
            self._notify_repair_collection_changed()

    def load_row(index: int) -> None:
        if index < 0 or index >= len(draft_rows):
            return
        current_index["value"] = index
        row = draft_rows[index]
        amount_var.set(f"{float(row.get('amount') or 0.0):,.2f}")
        date_var.set(normalize_text(row.get("date")) or self._today_text())
        reference_var.set(normalize_text(row.get("reference")))
        line_label_var.set(normalize_text(row.get("line_label")))
        journal_code_var.set(normalize_text(row.get("journal_code")))
        row_target_var.set(target_mode_value_to_label.get(normalize_text(row.get("target_mode")), "Belum dipilih"))
        row_posting_var.set(posting_mode_value_to_label.get(normalize_text(row.get("posting_mode")), "Belum dipilih"))
        row_role_var.set(role_value_to_label.get(normalize_text(row.get("target_account_role")), "Belum dipilih"))
        row_resolve_var.set(normalize_text(row.get("resolve_account_code")).upper())
        row_resolve_preview_var.set(normalize_text(row.get("resolve_account_preview")))
        target_account_code_var.set(normalize_text(row.get("target_account_code")))
        target_account_preview_var.set(normalize_text(row.get("target_account_preview")))
        debit_code_var.set(normalize_text(row.get("debit_account_code")))
        debit_preview_var.set(normalize_text(row.get("debit_account_preview")))
        credit_code_var.set(normalize_text(row.get("credit_account_code")))
        credit_preview_var.set(normalize_text(row.get("credit_account_preview")))
        source_status_var.set(self._repair_source_label(row))
        source_svl_id_var.set(str(int(row.get("svl_id") or 0)) if int(row.get("svl_id") or 0) > 0 else "-")
        source_svl_date_var.set(normalize_text(row.get("svl_date")) or "-")
        source_svl_qty_var.set(f"{float(row.get('svl_qty') or 0.0):,.2f}")
        source_svl_unit_cost_var.set(f"{float(row.get('svl_unit_cost') or 0.0):,.2f}")
        source_svl_value_var.set(f"{float(row.get('svl_value') or row.get('signed_amount') or row.get('amount') or 0.0):,.2f}")
        source_svl_reference_var.set(normalize_text(row.get("svl_reference")) or "-")
        source_linked_move_var.set(self._repair_linked_move_label(row))
        row_status_label = self._repair_row_status_label(row)
        row_status_detail = normalize_text(row.get("row_status_message"))
        row_status_var.set(f"Status: {row_status_label}" + (f" | {row_status_detail}" if row_status_detail else ""))
        target_warning_var.set(
            self._repair_target_warning_text(normalize_text(row.get("target_mode")), [row])
            if normalize_text(row.get("target_mode"))
            else ""
        )
        set_row_editor_state(self._repair_row_is_editable(row))
        if index in leaf_item_by_index:
            row_tree.focus(leaf_item_by_index[index])
            row_tree.see(leaf_item_by_index[index])

    def select_row(index: int) -> None:
        if index < 0 or index >= len(draft_rows):
            return
        save_current_row()
        clear_virtual_selection()
        selection_guard["active"] = True
        row_tree.selection_remove(row_tree.selection())
        if index in leaf_item_by_index:
            row_tree.selection_set(leaf_item_by_index[index])
            row_tree.focus(leaf_item_by_index[index])
            row_tree.see(leaf_item_by_index[index])
        selection_guard["active"] = False
        refresh_selection_summary()
        load_row(index)

    def on_row_selected(_event: tk.Event | None = None) -> None:
        if selection_guard["active"]:
            return
        if has_virtual_selection():
            clear_virtual_selection()
        selection = current_physical_selection_indices()
        if not selection and row_tree.focus() not in index_by_item_id:
            refresh_selection_summary()
            return
        refresh_selection_summary()
        target_index = current_focus_index(int(current_index["value"]))
        if target_index != int(current_index["value"]):
            save_current_row()
            load_row(target_index)
        elif target_index in leaf_item_by_index:
            row_tree.focus(leaf_item_by_index[target_index])
            row_tree.see(leaf_item_by_index[target_index])

    def toggle_row_group(item_id: str) -> None:
        if not item_id or not bool(row_tree_meta.get(item_id, {}).get("group_header")):
            return
        current_selection = current_selection_item_ids()
        current_focus = row_tree.focus()
        try:
            is_open = bool(row_tree.item(item_id, "open"))
            row_tree.item(item_id, open=not is_open)
        except Exception:  # noqa: BLE001
            return
        if current_selection and not has_virtual_selection():
            selection_guard["active"] = True
            row_tree.selection_set(current_selection)
            if current_focus and current_focus in current_selection:
                row_tree.focus(current_focus)
                row_tree.see(current_focus)
            selection_guard["active"] = False

    def on_row_tree_click(event: tk.Event) -> str | None:
        clicked_item = row_tree.identify_row(event.y)
        if clicked_item and bool(row_tree_meta.get(clicked_item, {}).get("group_header")):
            toggle_row_group(clicked_item)
            return "break"
        return None

    def on_account_lookup_done(debit_account: Any, credit_account: Any, error: str) -> None:
        if not dialog.winfo_exists():
            return
        if error:
            messagebox.showerror(self._display_name, error)
            return
        debit_preview_var.set(
            f"{debit_account.code} - {debit_account.name}" if debit_account is not None else "Tidak ditemukan exact"
        )
        credit_preview_var.set(
            f"{credit_account.code} - {credit_account.name}" if credit_account is not None else "Tidak ditemukan exact"
        )
        index = int(current_index["value"])
        if 0 <= index < len(draft_rows):
            draft_rows[index]["debit_account_preview"] = normalize_text(debit_preview_var.get())
            draft_rows[index]["credit_account_preview"] = normalize_text(credit_preview_var.get())
            update_row_list_entry(index)

    def repair_bulk_apply_chunk_size() -> int:
        return max(
            1,
            int(
                getattr(
                    self,
                    "_repair_bulk_apply_chunk_size",
                    REPAIR_BULK_APPLY_CHUNK_SIZE,
                )
                or REPAIR_BULK_APPLY_CHUNK_SIZE
            ),
        )

    def set_repair_progress_fields(
        *,
        processed: int,
        total: int,
        current_text: str,
        meta_text: str,
    ) -> None:
        safe_total = max(0, int(total or 0))
        safe_processed = max(0, min(int(processed or 0), safe_total)) if safe_total > 0 else 0
        progress_pct = round((safe_processed / safe_total) * 100, 2) if safe_total > 0 else 0.0
        repair_progress_value_var.set(progress_pct)
        repair_progress_count_var.set(f"{safe_processed} / {safe_total}" if safe_total > 0 else "0 / 0")
        repair_progress_percent_var.set(f"{int(round(progress_pct))}%")
        repair_progress_current_var.set(normalize_text(current_text) or "-")
        repair_progress_meta_var.set(normalize_text(meta_text) or "Berhasil 0 | Gagal 0")

    def select_all_rows() -> None:
        save_current_row(notify_collection=False)
        if not draft_rows:
            return
        threshold = repair_virtual_select_threshold()
        selection_guard["active"] = True
        row_tree.selection_remove(row_tree.selection())
        if len(draft_rows) > threshold:
            selection_state["virtual_all"] = True
            focus_index = current_focus_index(0)
            focus_item = leaf_item_by_index.get(focus_index, "")
            if not focus_item and leaf_item_by_index:
                focus_item = leaf_item_by_index.get(min(leaf_item_by_index), "")
            if focus_item:
                row_tree.selection_set(focus_item)
                row_tree.focus(focus_item)
                row_tree.see(focus_item)
            status_text = (
                f"Semua {len(draft_rows):,} row dipilih. "
                f"Mode virtual aktif untuk menjaga performa di atas {threshold:,} row."
            )
        else:
            clear_virtual_selection()
            selected_item_ids = tuple(leaf_item_by_index[index] for index in sorted(leaf_item_by_index))
            if selected_item_ids:
                row_tree.selection_set(selected_item_ids)
                row_tree.focus(selected_item_ids[0])
                row_tree.see(selected_item_ids[0])
            status_text = f"{len(selected_item_ids):,} row dipilih."
        selection_guard["active"] = False
        refresh_selection_summary()
        self.status_var.set(status_text)

    def deselect_all_rows() -> None:
        clear_virtual_selection()
        selection_guard["active"] = True
        row_tree.selection_remove(row_tree.selection())
        selection_guard["active"] = False
        refresh_selection_summary()
        self.status_var.set("Selection dibersihkan.")

    def apply_global_to_selected() -> None:
        save_current_row(notify_collection=False)
        selection_count = current_selection_count()
        if selection_count <= 0:
            messagebox.showwarning(self._display_name, "Pilih row di list kiri terlebih dahulu.")
            return
        persist_global_defaults()
        target_mode_value = target_mode_label_to_value.get(global_target_var.get(), "")
        posting_mode_value = posting_mode_label_to_value.get(global_posting_var.get(), "")
        role_value = role_label_to_value.get(global_role_var.get(), "")
        resolve_code = normalize_text(global_resolve_var.get()).upper()
        current_focus = int(current_index["value"])
        selection_snapshot = current_selection_snapshot()
        selection_iterator = iter(iter_selected_indices())
        chunk_size = repair_bulk_apply_chunk_size()
        applied = 0
        skipped = 0
        processed = 0
        bulk_context = {
            "reference_prefix": self._repair_reference_prefix(),
            "global_candidates": list(resolve_candidates),
            "category_candidates_cache": {},
            "combined_candidates_cache": {},
            "target_candidate_cache": {},
            "preview_map_cache": {},
        }
        set_bulk_apply_running_state(True)
        set_repair_progress_fields(
            processed=0,
            total=selection_count,
            current_text="Applying global defaults...",
            meta_text="Applied 0 | Skipped 0",
        )
        self.status_var.set(f"Menerapkan global settings ke {selection_count:,} row...")

        def run_bulk_apply_chunk() -> None:
            nonlocal applied, skipped, processed
            if not bool(dialog.winfo_exists()):
                bulk_apply_state["after_id"] = None
                return
            chunk_indices: list[int] = []
            exhausted = False
            try:
                for _ in range(chunk_size):
                    chunk_indices.append(int(next(selection_iterator)))
            except StopIteration:
                exhausted = True
            if not chunk_indices:
                bulk_apply_state["after_id"] = None
                set_bulk_apply_running_state(False)
                if source_kind == "repair_collection":
                    self._notify_repair_collection_changed()
                load_row(current_focus)
                if selection_snapshot == REPAIR_DIALOG_SELECTION_ALL:
                    selection_state["virtual_all"] = True
                status_text = f"Global settings diterapkan ke {applied:,} row terpilih."
                if skipped > 0:
                    status_text += f" {skipped:,} row dilewati karena sudah locked."
                set_repair_progress_fields(
                    processed=selection_count,
                    total=selection_count,
                    current_text="Apply global selesai.",
                    meta_text=f"Applied {applied:,} | Skipped {skipped:,}",
                )
                self.status_var.set(status_text)
                return
            for index in chunk_indices:
                processed += 1
                if index < 0 or index >= len(draft_rows):
                    continue
                row = draft_rows[index]
                if self._apply_repair_global_defaults_to_row(
                    row,
                    target_mode_value=target_mode_value,
                    posting_mode_value=posting_mode_value,
                    role_value=role_value,
                    resolve_code=resolve_code,
                    reference_prefix=bulk_context["reference_prefix"],
                    global_candidates=bulk_context["global_candidates"],
                    category_candidates_cache=bulk_context["category_candidates_cache"],
                    combined_candidates_cache=bulk_context["combined_candidates_cache"],
                    target_candidate_cache=bulk_context["target_candidate_cache"],
                    preview_map_cache=bulk_context["preview_map_cache"],
                ):
                    applied += 1
                    update_row_list_entry(index, refresh_group=False, allow_reload=False)
                else:
                    skipped += 1
                set_repair_progress_fields(
                    processed=processed,
                    total=selection_count,
                    current_text=f"Applying global defaults... {processed:,}/{selection_count:,}",
                    meta_text=f"Applied {applied:,} | Skipped {skipped:,}",
                )
                self.status_var.set(
                    f"Menerapkan global settings... {processed:,}/{selection_count:,} row | "
                    f"Applied {applied:,} | Skipped {skipped:,}"
                )
            if exhausted or processed >= selection_count:
                bulk_apply_state["after_id"] = None
                set_bulk_apply_running_state(False)
                if source_kind == "repair_collection":
                    self._notify_repair_collection_changed()
                load_row(current_focus)
                if selection_snapshot == REPAIR_DIALOG_SELECTION_ALL:
                    selection_state["virtual_all"] = True
                status_text = f"Global settings diterapkan ke {applied:,} row terpilih."
                if skipped > 0:
                    status_text += f" {skipped:,} row dilewati karena sudah locked."
                set_repair_progress_fields(
                    processed=selection_count,
                    total=selection_count,
                    current_text="Apply global selesai.",
                    meta_text=f"Applied {applied:,} | Skipped {skipped:,}",
                )
                self.status_var.set(status_text)
                return
            bulk_apply_state["after_id"] = dialog.after_idle(run_bulk_apply_chunk)

        bulk_apply_state["after_id"] = dialog.after_idle(run_bulk_apply_chunk)

    def check_accounts() -> None:
        save_current_row()
        current_row = draft_rows[int(current_index["value"])]
        debit_code = normalize_text(current_row.get("debit_account_code"))
        credit_code = normalize_text(current_row.get("credit_account_code"))
        if not debit_code or not credit_code:
            messagebox.showwarning(self._display_name, "Akun debit dan kredit hasil derivasi masih kosong.")
            return
        debit_preview_var.set("Checking...")
        credit_preview_var.set("Checking...")
        self._resolve_repair_accounts_async(
            company_id=company_id,
            debit_code=debit_code,
            credit_code=credit_code,
            on_done=on_account_lookup_done,
        )

    def validation_blockers(rows_to_check: list[dict[str, Any]]) -> list[tuple[dict[str, Any], str]]:
        blockers: list[tuple[dict[str, Any], str]] = []
        for row in rows_to_check:
            state = normalize_text(row.get("row_status")).lower()
            if state == "repaired":
                blockers.append((row, "Already Repaired"))
                continue
            if state == "running":
                blockers.append((row, "Running"))
                continue
            self._refresh_repair_row_state(row)
            if normalize_text(row.get("row_status")).lower() != "ready":
                blockers.append((row, normalize_text(row.get("row_status_message")) or "Incomplete"))
        return blockers

    def show_validation_blockers(blockers: list[tuple[dict[str, Any], str]], *, title_suffix: str) -> None:
        preview_lines = [
            f"{index}. {normalize_text(row.get('item_code')) or normalize_text(row.get('row_key')) or '-'} | {detail}"
            for index, (row, detail) in enumerate(blockers[:10], start=1)
        ]
        if len(blockers) > 10:
            preview_lines.append(f"... dan {len(blockers) - 10} row lainnya.")
        messagebox.showwarning(
            self._display_name,
            f"Repair {title_suffix} dibatalkan karena masih ada row yang belum siap.\n\n" + "\n".join(preview_lines),
        )

    def execute_repair(*, selected_only: bool) -> None:
        if getattr(self, "_busy", False):
            messagebox.showwarning(self._display_name, "Masih ada proses repair lain yang sedang berjalan.")
            return
        save_current_row(notify_collection=False)
        selection_snapshot = current_selection_snapshot()
        scope_rows = (
            list(draft_rows)
            if selection_snapshot == REPAIR_DIALOG_SELECTION_ALL
            else [draft_rows[index] for index in selection_snapshot if 0 <= index < len(draft_rows)]
            if selected_only
            else list(draft_rows)
        )
        if selected_only and not scope_rows:
            messagebox.showwarning(self._display_name, "Pilih row di list kiri terlebih dahulu.")
            return
        if not scope_rows:
            messagebox.showwarning(self._display_name, "Belum ada row untuk dijalankan.")
            return
        blockers = validation_blockers(scope_rows)
        if blockers:
            show_validation_blockers(blockers, title_suffix="selected rows" if selected_only else "all")
            reload_row_list(int(current_index["value"]), preserve_selection=selection_snapshot)
            return
        for row in scope_rows:
            row["row_status"] = "running"
            row["row_status_message"] = "Running"
        reload_row_list(int(current_index["value"]), preserve_selection=selection_snapshot)
        if source_kind == "repair_collection":
            self._notify_repair_collection_changed()
        self._start_repair(
            [self._build_dashboard_repair_row(row, fallback_company_id=company_id) for row in scope_rows]
        )

    def apply_results_to_rows(results: list[SvlDashboardRepairRowResult]) -> None:
        result_by_row_key = {
            normalize_text(result.row_key): result
            for result in results
            if normalize_text(result.row_key)
        }
        if not result_by_row_key:
            return
        current_selection = current_selection_snapshot()
        for row in draft_rows:
            result = result_by_row_key.get(normalize_text(row.get("row_key")))
            if result is not None:
                self._apply_repair_result_to_row(row, result)
        reload_row_list(int(current_index["value"]), preserve_selection=current_selection)
        if source_kind == "repair_collection":
            self._notify_repair_collection_changed()

    check_accounts_button = tk.Button(
        action_row,
        text="Check Accounts",
        bg=T.BRAND_SECONDARY,
        fg=T.TEXT_ON_DARK,
        activebackground=T.BRAND_ACCENT,
        activeforeground=T.TEXT_ON_DARK,
        relief="flat",
        padx=14,
        pady=6,
        command=check_accounts,
    )
    check_accounts_button.pack(side="left")
    apply_global_button = tk.Button(
        action_row,
        text="Apply Global to Selected",
        bg=T.BG_INPUT,
        fg=T.TEXT_ON_LIGHT,
        relief="flat",
        padx=14,
        pady=6,
        command=apply_global_to_selected,
    )
    apply_global_button.pack(side="left", padx=(8, 0))
    repair_selected_button = tk.Button(
        action_row,
        text="Repair Selected Row(s)",
        bg=T.BG_INPUT,
        fg=T.TEXT_ON_LIGHT,
        relief="flat",
        padx=14,
        pady=6,
        command=lambda: execute_repair(selected_only=True),
    )
    repair_selected_button.pack(side="left", padx=(8, 0))
    repair_all_button = tk.Button(
        action_row,
        text="Repair All",
        bg=T.BRAND_PRIMARY,
        fg=T.TEXT_ON_DARK,
        activebackground=T.BRAND_PRIMARY_DARK,
        activeforeground=T.TEXT_ON_DARK,
        relief="flat",
        padx=18,
        pady=6,
        command=lambda: execute_repair(selected_only=False),
    )
    repair_all_button.pack(side="left", padx=(8, 0))

    def close_dialog() -> None:
        if dialog_state["bulk_apply_running"]:
            messagebox.showwarning(self._display_name, "Tunggu Apply Global selesai terlebih dahulu.")
            return
        if self._last_repair_dialog_widgets.get("dialog") is dialog:
            self._last_repair_dialog_widgets = {}
        dialog.destroy()

    cancel_button = tk.Button(
        action_row,
        text="Cancel",
        bg=T.BG_INPUT,
        fg=T.TEXT_ON_LIGHT,
        relief="flat",
        padx=14,
        pady=6,
        command=close_dialog,
    )
    cancel_button.pack(side="right")
    select_all_button.configure(command=select_all_rows)
    deselect_all_button.configure(command=deselect_all_rows)

    global_target_combo.bind("<<ComboboxSelected>>", persist_global_defaults)
    global_posting_combo.bind("<<ComboboxSelected>>", persist_global_defaults)
    global_role_combo.bind("<<ComboboxSelected>>", persist_global_defaults)
    global_resolve_var.trace_add("write", lambda *_args: persist_global_defaults())
    target_combo.bind("<<ComboboxSelected>>", lambda _event: save_current_row())
    posting_combo.bind("<<ComboboxSelected>>", lambda _event: save_current_row())
    role_combo.bind("<<ComboboxSelected>>", lambda _event: save_current_row())
    row_tree.bind("<<TreeviewSelect>>", on_row_selected)
    row_tree.bind("<Button-1>", on_row_tree_click)
    dialog.bind("<Escape>", lambda _event: close_dialog())
    dialog.protocol("WM_DELETE_WINDOW", close_dialog)
    self._last_repair_dialog_widgets = {
        "dialog": dialog,
        "source_kind": source_kind,
        "content_pane": content_pane,
        "left_list": row_tree,
        "left_tree": row_tree,
        "left_scrollbar": list_scroll,
        "left_panel": left,
        "right_host": right_host,
        "source_label_var": source_var,
        "global_target_combo": global_target_combo,
        "global_posting_combo": global_posting_combo,
        "global_role_combo": global_role_combo,
        "global_resolve_var": global_resolve_var,
        "global_resolve_preview_var": global_resolve_preview_var,
        "target_combo": target_combo,
        "posting_combo": posting_combo,
        "role_combo": role_combo,
        "row_resolve_var": row_resolve_var,
        "row_resolve_preview_var": row_resolve_preview_var,
        "target_account_code_var": target_account_code_var,
        "target_account_preview_var": target_account_preview_var,
        "debit_var": debit_code_var,
        "credit_var": credit_code_var,
        "debit_preview_var": debit_preview_var,
        "credit_preview_var": credit_preview_var,
        "amount_var": amount_var,
        "date_var": date_var,
        "reference_var": reference_var,
        "line_label_var": line_label_var,
        "journal_code_var": journal_code_var,
        "row_status_var": row_status_var,
        "target_warning_var": target_warning_var,
        "source_status_var": source_status_var,
        "source_svl_id_var": source_svl_id_var,
        "source_svl_date_var": source_svl_date_var,
        "source_svl_qty_var": source_svl_qty_var,
        "source_svl_unit_cost_var": source_svl_unit_cost_var,
        "source_svl_value_var": source_svl_value_var,
        "source_svl_reference_var": source_svl_reference_var,
        "source_linked_move_var": source_linked_move_var,
        "draft_rows": draft_rows,
        "save_current_row": save_current_row,
        "load_row": load_row,
        "select_row": select_row,
        "apply_results_to_rows": apply_results_to_rows,
        "reload_row_list": reload_row_list,
        "on_row_tree_click": on_row_tree_click,
        "toggle_row_group": toggle_row_group,
        "left_group_item_ids": lambda: tuple(group_item_ids),
        "left_leaf_item_ids": lambda: tuple(leaf_item_by_index[index] for index in sorted(leaf_item_by_index)),
        "left_item_id_for_index": lambda index: leaf_item_by_index.get(index, ""),
        "left_index_for_item_id": lambda item_id: index_by_item_id.get(item_id, -1),
        "right_scroll": right_scroll,
        "right_canvas": right_scroll.canvas,
        "right_scrollbar": right_scroll.scrollbar,
        "right_body": right,
        "split_state": split_state,
        "measure_split_width": measured_split_width,
        "apply_split_layout": sync_split_layout,
        "remember_split_ratio": remember_current_split_ratio,
        "set_dialog_busy_state": set_dialog_busy_state,
        "footer": footer,
        "progress_row": progress_row,
        "repair_progress_bar_host": repair_progress_bar_host,
        "repair_progress_info_host": repair_progress_info_host,
        "repair_progress_bar": repair_progress_bar,
        "repair_progress_count_label": repair_progress_count_label,
        "repair_progress_percent_label": repair_progress_percent_label,
        "repair_progress_current_label": repair_progress_current_label,
        "repair_progress_value_var": repair_progress_value_var,
        "repair_progress_count_var": repair_progress_count_var,
        "repair_progress_percent_var": repair_progress_percent_var,
        "repair_progress_current_var": repair_progress_current_var,
        "repair_progress_meta_var": repair_progress_meta_var,
        "action_row": action_row,
        "select_all_button": select_all_button,
        "deselect_all_button": deselect_all_button,
        "selection_summary_var": selection_summary_var,
        "check_accounts_button": check_accounts_button,
        "apply_global_button": apply_global_button,
        "apply_coa_button": apply_global_button,
        "apply_all_button": apply_global_button,
        "repair_selected_button": repair_selected_button,
        "repair_all_button": repair_all_button,
        "run_repair_button": repair_all_button,
        "cancel_button": cancel_button,
        "debit_candidates": resolve_picker.candidate_listbox,
        "credit_candidates": resolve_picker.candidate_listbox,
        "resolve_candidates": resolve_picker.candidate_listbox,
        "current_selection_indices": current_selection_indices,
        "current_selection_count": current_selection_count,
        "current_selection_snapshot": current_selection_snapshot,
        "is_virtual_select_all": lambda: bool(selection_state["virtual_all"]),
        "is_bulk_apply_running": lambda: bool(dialog_state["bulk_apply_running"]),
    }
    reload_row_list(0)
    refresh_dialog_busy_state()
    request_split_sync()


def _format_repair_result_summary(
    self,
    results: list[SvlDashboardRepairRowResult],
    *,
    database: str = "",
) -> tuple[str, str, str, bool, dict[str, Any]]:
    payload = build_repair_summary_export_payload(database=database, results=results)
    body_lines: list[str] = []
    for row in payload["rows"]:
        body_lines.append(f"{int(row['no'])}. {row['status']} | Transaksi: {row['transaction_no']}")
        body_lines.append(f"   Company : {row['company_label']}")
        body_lines.append(f"   Nominal : {row['amount_label']}")
        body_lines.append(f"   Item    : {normalize_text(row['item_code'])} | {normalize_text(row['item_name'])}".rstrip(" |"))
        body_lines.append(f"   Mode    : {row['effective_mode']} | Tanggal: {row['effective_date']}")
        body_lines.append(f"   SVL     : {row['svl_reference']}")
        if normalize_text(row["error_kind"]):
            body_lines.append(
                f"   Error   : {self._repair_error_label(normalize_text(row['error_kind']))}"
            )
        if normalize_text(row["old_transaction_no"]):
            body_lines.append(f"   Lama    : {row['old_transaction_no']} (mark only)")
        body_lines.append(f"   Detail  : {row['detail_sentence']}")
        body_lines.append("")
    title = "Repair Summary"
    summary_text = f"Repair selesai. {int(payload['success_count'])} berhasil, {int(payload['error_count'])} gagal."
    body_text = "\n".join(body_lines).rstrip()
    return title, summary_text, body_text, int(payload["error_count"]) > 0, payload


def _close_repair_summary_dialog(self) -> None:
    widgets = getattr(self, "_last_repair_summary_widgets", {})
    dialog = widgets.get("dialog")
    if dialog is not None and getattr(dialog, "winfo_exists", lambda: False)():
        try:
            dialog.destroy()
        except Exception:  # noqa: BLE001
            pass
    self._last_repair_summary_widgets = {}


def _show_repair_result_summary(self, results: list[SvlDashboardRepairRowResult], *, database: str = "") -> None:
    if not results:
        return
    title, summary_text, body_text, has_error, payload = self._format_repair_result_summary(results, database=database)
    self._close_repair_summary_dialog()
    root = getattr(self, "root", None)
    if not isinstance(root, tk.Misc):
        self._last_repair_summary_widgets = {
            "title": title,
            "summary_text": summary_text,
            "body_text": body_text,
            "body_message": body_text,
            "has_error": has_error,
            "results": list(results),
            "database": normalize_text(database),
            "payload": payload,
        }
        return
    dialog = tk.Toplevel(root)
    dialog.title(title)
    dialog.transient(root)
    dialog.grab_set()
    dialog.configure(bg=T.BG_MAIN)
    dialog.geometry("980x680")
    dialog.resizable(False, False)

    main = tk.Frame(dialog, bg=T.BG_MAIN)
    main.pack(fill="both", expand=True, padx=18, pady=18)

    banner_color = T.STATUS_WARNING if has_error else T.STATUS_INFO
    banner = tk.Frame(main, bg=banner_color)
    banner.pack(fill="x", pady=(0, 12))
    summary_label = tk.Label(
        banner,
        text=summary_text,
        bg=banner_color,
        fg=T.TEXT_ON_DARK,
        font=T.font(T.FONT_BODY_SIZE, bold=True),
        anchor="w",
        justify="left",
        padx=14,
        pady=10,
    )
    summary_label.pack(fill="x")

    body = ScrolledText(
        main,
        wrap="word",
        bg=T.BG_CARD,
        fg=T.TEXT_ON_LIGHT,
        relief="solid",
        borderwidth=1,
        font=T.font(),
        padx=10,
        pady=10,
        height=26,
    )
    body.pack(fill="both", expand=True)
    body.insert("1.0", body_text)
    body.configure(state="disabled")
    body.yview_moveto(0.0)

    footer = tk.Frame(main, bg=T.BG_MAIN)
    footer.pack(fill="x", pady=(12, 0))

    def close_dialog() -> None:
        if self._last_repair_summary_widgets.get("dialog") is dialog:
            self._last_repair_summary_widgets = {}
        try:
            dialog.grab_release()
        except Exception:  # noqa: BLE001
            pass
        dialog.destroy()

    export_html_button = tk.Button(
        footer,
        text="Export HTML",
        command=lambda: self._export_repair_summary("html"),
        bg=T.BG_INPUT,
        fg=T.TEXT_ON_LIGHT,
        relief="flat",
        padx=16,
        pady=8,
        font=T.font(T.FONT_BODY_SIZE, bold=True),
    )
    export_html_button.pack(side="left")
    export_excel_button = tk.Button(
        footer,
        text="Export Excel",
        command=lambda: self._export_repair_summary("excel"),
        bg=T.BG_INPUT,
        fg=T.TEXT_ON_LIGHT,
        relief="flat",
        padx=16,
        pady=8,
        font=T.font(T.FONT_BODY_SIZE, bold=True),
    )
    export_excel_button.pack(side="left", padx=(8, 0))
    ok_button = tk.Button(
        footer,
        text="OK",
        command=close_dialog,
        bg=T.BRAND_PRIMARY,
        fg=T.TEXT_ON_DARK,
        activebackground=T.BRAND_PRIMARY_DARK,
        activeforeground=T.TEXT_ON_DARK,
        relief="flat",
        padx=20,
        pady=8,
        font=T.font(T.FONT_BODY_SIZE, bold=True),
    )
    ok_button.pack(side="right")

    dialog.bind("<Escape>", lambda _event: close_dialog())
    dialog.bind("<Return>", lambda _event: close_dialog())
    dialog.protocol("WM_DELETE_WINDOW", close_dialog)
    self._last_repair_summary_widgets = {
        "dialog": dialog,
        "summary_label": summary_label,
        "body_text": body,
        "footer": footer,
        "ok_button": ok_button,
        "export_html_button": export_html_button,
        "export_excel_button": export_excel_button,
        "summary_text": summary_text,
        "body_message": body_text,
        "has_error": has_error,
        "results": list(results),
        "database": normalize_text(database),
        "payload": payload,
    }
    ok_button.focus_set()


def _build_repair_seed_from_merged_row(self, item: Any, row: Any) -> dict[str, Any] | None:
    if not bool(getattr(row, "repair_candidate", False)):
        return None
    row_type = normalize_text(getattr(row, "row_type", ""))
    label = " ".join(part for part in (normalize_text(item.code), normalize_text(item.name)) if part)
    signed_amount = float(getattr(row, "svl_value", 0.0) or getattr(row, "net", 0.0) or 0.0)
    if row_type == "svl_no_move" and abs(signed_amount) <= 0:
        return None
    seed: dict[str, Any] = {
        "row_key": normalize_text(getattr(row, "row_key", "")),
        "company_id": int(self._selected_company_id() or 0),
        "company_name": self._selected_company_name(),
        "item_product_id": int(item.pid or 0) if int(item.pid or 0) > 0 else 0,
        "item_code": normalize_text(item.code),
        "item_name": normalize_text(item.name),
        "item_category_name": normalize_text(getattr(item, "categ", "")),
        "amount": abs(signed_amount),
        "signed_amount": signed_amount,
        "date": normalize_text(getattr(row, "move_date", "") or getattr(row, "svl_date", "") or getattr(row, "aml_date", ""))[:10],
        "reference": "",
        "line_label": "",
        "reference_generated": True,
        "line_label_generated": True,
        "base_reference": normalize_text(item.code),
        "base_line_label": label,
        "journal_code": self._parse_journal_code(normalize_text(getattr(row, "move_name", ""))),
        "svl_id": int(getattr(row, "svl_id", 0) or 0),
        "svl_date": normalize_text(getattr(row, "svl_date", ""))[:10],
        "svl_qty": float(getattr(row, "svl_qty", 0.0) or 0.0),
        "svl_unit_cost": float(getattr(row, "svl_unit_cost", 0.0) or 0.0),
        "svl_value": float(getattr(row, "svl_value", 0.0) or 0.0),
        "svl_reference": normalize_text(getattr(row, "svl_reference", "")),
        "move_id": int(getattr(row, "move_id", 0) or 0),
        "move_name": normalize_text(getattr(row, "move_name", "")),
        "move_state": normalize_text(getattr(row, "move_state", "")),
        "target_mode": "new_and_relink",
        "posting_mode": "draft",
        "account_candidates": list(getattr(item, "repair_account_candidates", []) or []),
        "latest_snapshot_status": normalize_text(getattr(row, "status", "")),
        "repair_source_kind": row_type,
        "repair_source_label": (
            "SVL tanpa JE"
            if row_type == "svl_no_move"
            else (
                "Linked JE Header Kosong"
                if row_type == "svl_linked_empty_move"
                else normalize_text(getattr(row, "status", "")) or "Repair Row"
            )
        ),
    }
    seed["date"] = self._default_repair_date_for_seed(seed)
    seed["reference"], seed["line_label"] = self._repair_generated_texts_for_row(seed)
    return seed


def _snapshot_repair_candidates_by_row_key(
    self,
    snapshot: SvlDashboardSnapshot | None,
) -> dict[str, tuple[dict[str, Any], str]]:
    by_row_key: dict[str, tuple[dict[str, Any], str]] = {}
    if snapshot is None:
        return by_row_key
    for item in snapshot.items:
        for row in getattr(item, "merged_records", []) or []:
            seed = self._build_repair_seed_from_merged_row(item, row)
            row_key = normalize_text(seed.get("row_key")) if seed else ""
            if not row_key:
                continue
            by_row_key[row_key] = (dict(seed), normalize_text(getattr(row, "status", "")))
    return by_row_key


def _reconcile_repair_collection_with_snapshot(self, snapshot: SvlDashboardSnapshot | None) -> None:
    collection = getattr(self, "_repair_collection", None)
    scope = getattr(self, "_repair_collection_scope", None)
    if not collection or scope is None or snapshot is None:
        return
    if not scope.matches(self._current_repair_collection_scope()):
        return
    latest_by_row_key = self._snapshot_repair_candidates_by_row_key(snapshot)
    removed = 0
    refreshed = 0
    for row_key in list(collection.keys()):
        latest_entry = latest_by_row_key.get(row_key)
        current_entry = collection[row_key]
        current_state = normalize_text(current_entry.draft.get("row_status")).lower()
        if latest_entry is None:
            if current_state == "repaired":
                continue
            collection.pop(row_key, None)
            removed += 1
            continue
        latest_seed, latest_status = latest_entry
        if current_entry.seed != latest_seed or normalize_text(current_entry.latest_snapshot_status) != latest_status:
            current_entry.seed = dict(latest_seed)
            current_entry.latest_snapshot_status = latest_status
            if current_state != "repaired":
                self._merge_seed_into_repair_draft(current_entry.draft, latest_seed)
                self._refresh_repair_row_state(current_entry.draft, preserve_terminal=True)
            refreshed += 1
    self._reset_repair_collection_scope_if_empty()
    if removed or refreshed:
        self._append_log(
            f"Repair Collection reconciled for current snapshot: {removed} removed, {refreshed} refreshed."
        )


def _snapshot_pcb_case1_candidates_by_row_key(
    self,
    snapshot: SvlDashboardSnapshot | None,
) -> dict[str, tuple[dict[str, Any], str]]:
    by_row_key: dict[str, tuple[dict[str, Any], str]] = {}
    if snapshot is None:
        return by_row_key
    for cycle in list(getattr(snapshot, "purchase_cycles", None) or []):
        cycle_status = normalize_text(getattr(cycle, "cycle_status", ""))
        for seed in self._build_pcb_seeds_for_cycle(cycle):
            row_key = normalize_text(seed.get("row_key"))
            if not row_key:
                continue
            by_row_key[row_key] = (dict(seed), cycle_status)
    return by_row_key


def _merge_pcb_case1_seed_into_row(self, row: dict[str, Any], latest_seed: dict[str, Any]) -> None:
    self._sync_pcb_case1_generated_flags(row)
    existing_date = normalize_text(row.get("date"))
    manual_reference = normalize_text(row.get("reference")) if not bool(row.get("reference_generated")) else ""
    manual_line_label = normalize_text(row.get("line_label")) if not bool(row.get("line_label_generated")) else ""
    existing_resolve_account_code = normalize_text(row.get("resolve_account_code")).upper()
    existing_resolve_account_manual = bool(row.get("resolve_account_manual"))
    existing_planned_lines_manual = bool(row.get("planned_lines_manual"))
    existing_planned_lines = (
        self._map_pcb_case2_planned_lines(
            list(row.get("planned_lines") or []),
            default_line_label=normalize_text(row.get("line_label")) or normalize_text(latest_seed.get("line_label")),
        )
        if existing_planned_lines_manual
        else []
    )
    protected_fields = {
        "row_status",
        "row_status_message",
        "review_confirmed",
        "result_status",
        "result_posted",
        "result_error_kind",
        "result_move_id",
        "result_move_name",
        "existing_move_detected",
        "reconcile_attempted",
        "reconcile_performed",
        "reconcile_skipped",
        "reconcile_message",
        "reconcile_error_kind",
    }
    for field_name, field_value in latest_seed.items():
        if field_name in protected_fields:
            continue
        row[field_name] = field_value
    if existing_date:
        row["date"] = existing_date
    if manual_reference:
        row["reference"] = manual_reference
        row["reference_generated"] = False
    if manual_line_label:
        row["line_label"] = manual_line_label
        row["line_label_generated"] = False
    if existing_resolve_account_manual and existing_resolve_account_code:
        row["resolve_account_manual"] = True
        row["resolve_account_code"] = existing_resolve_account_code
    if existing_planned_lines_manual:
        row["planned_lines_manual"] = True
        row["planned_lines"] = existing_planned_lines
    self._sync_pcb_case1_generated_flags(row)
    self._refresh_generated_pcb_case1_texts(row)
    if self._pcb_row_uses_planned_lines(row):
        self._sync_pcb_case2_row_defaults(row, preserve_terminal=True)


def _reconcile_pcb_case1_collection_with_snapshot(self, snapshot: SvlDashboardSnapshot | None) -> None:
    collection = getattr(self, "_pcb_repair_collection", None)
    scope = getattr(self, "_pcb_repair_collection_scope", None)
    if not collection or scope is None or snapshot is None:
        return
    if not scope.matches(self._current_repair_collection_scope()):
        return
    latest_by_row_key = self._snapshot_pcb_case1_candidates_by_row_key(snapshot)
    removed = 0
    refreshed = 0
    for row_key in list(collection.keys()):
        latest_entry = latest_by_row_key.get(row_key)
        current_row = collection[row_key]
        current_state = normalize_text(current_row.get("row_status")).lower()
        if latest_entry is None:
            if current_state == "repaired":
                continue
            collection.pop(row_key, None)
            removed += 1
            continue
        latest_seed, latest_status = latest_entry
        if current_state == "repaired":
            if normalize_text(current_row.get("latest_snapshot_status")) != latest_status:
                current_row["latest_snapshot_status"] = latest_status
                refreshed += 1
            continue
        before_row = dict(current_row)
        current_row["latest_snapshot_status"] = latest_status
        self._merge_pcb_case1_seed_into_row(current_row, latest_seed)
        if current_row != before_row:
            refreshed += 1
    if not collection:
        self._pcb_repair_collection_scope = None
    if removed or refreshed:
        self._update_pcb_collection_button()
        reload_row_list = getattr(self, "_last_pcb_case1_dialog_widgets", {}).get("reload_row_list")
        if callable(reload_row_list):
            try:
                reload_row_list()
            except Exception:  # noqa: BLE001
                pass
        self._append_log(
            f"PCB Repair Collection reconciled for current snapshot: {removed} removed, {refreshed} refreshed."
        )


def _refresh_open_pcb_collection_dialog(self) -> None:
    reload_row_list = getattr(self, "_last_pcb_case1_dialog_widgets", {}).get("reload_row_list")
    if callable(reload_row_list):
        try:
            reload_row_list()
        except Exception:  # noqa: BLE001
            pass


def _clear_repair_collections_for_scope_change(self) -> None:
    try:
        current_scope = self._current_repair_collection_scope()
    except Exception:  # noqa: BLE001
        current_scope = None
    cleared_parts: list[str] = []
    repair_collection = getattr(self, "_repair_collection", None)
    repair_scope = getattr(self, "_repair_collection_scope", None)
    if repair_collection and repair_scope is not None and not repair_scope.matches(current_scope):
        cleared_parts.append(f"Repair Collection {len(repair_collection)} row")
        repair_collection.clear()
        self._repair_collection_scope = None
        self._close_repair_dialog_if_collection_source()

    pcb_collection = getattr(self, "_pcb_repair_collection", None)
    pcb_scope = getattr(self, "_pcb_repair_collection_scope", None)
    if pcb_collection and pcb_scope is not None and not pcb_scope.matches(current_scope):
        cleared_parts.append(f"PCB Repair Collection {len(pcb_collection)} row")
        pcb_collection.clear()
        self._pcb_repair_collection_scope = None
        self._refresh_open_pcb_collection_dialog()
        self._update_pcb_collection_button()

    if not cleared_parts:
        return

    if pcb_collection is None or not cleared_parts[-1].startswith("PCB Repair Collection"):
        self._notify_repair_collection_changed()
    cleared_text = " dan ".join(cleared_parts)
    if hasattr(self, "status_var"):
        self.status_var.set(f"{cleared_text} dibersihkan karena analyze berpindah company / scope.")
    append_log = getattr(self, "_append_log", None)
    if callable(append_log):
        append_log(f"{cleared_text} cleared because analyze switched to a different company / scope.")
