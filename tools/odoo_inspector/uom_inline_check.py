#!/usr/bin/env python3
"""Check whether two uom.uom records are inline and convertible."""

from __future__ import annotations

import argparse
import asyncio
from decimal import Decimal, InvalidOperation
import json
from typing import Any

from common import add_common_connection_args, build_base_payload, open_tool_connection, write_json_artifact
from smartscc_tools.services.odoo.schema import snapshot_model_schema


MODEL = "uom.uom"
READ_FIELDS = [
    "id",
    "display_name",
    "name",
    "active",
    "relative_factor",
    "relative_uom_id",
    "factor",
    "rounding",
    "parent_path",
]
REQUIRED_FIELDS = ["id", "name", "factor", "parent_path"]
BUSINESS_EQUIVALENT_UOM_PAIRS = {frozenset({"units", "pcs (p)"})}


class UomInlineError(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only check whether two uom.uom records share one parent_path root and can be converted."
    )
    parser.add_argument("--source", required=True, help="Source UoM exact name/display_name or numeric id.")
    parser.add_argument("--target", required=True, help="Target UoM exact name/display_name or numeric id.")
    parser.add_argument("--quantity", default="1", help="Source quantity to convert. Default: 1.")
    parser.add_argument(
        "--allow-ilike",
        action="store_true",
        help="Fallback to ilike search if exact name/display_name does not match. Refuses ambiguous matches.",
    )
    add_common_connection_args(parser)
    return parser.parse_args()


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _to_decimal(value: Any, *, label: str) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise UomInlineError(f"{label} is not numeric: {value!r}") from exc


def _maybe_int(value: Any) -> int | None:
    text = _clean_text(value)
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def parent_path_ids(row: dict[str, Any]) -> list[int]:
    ids: list[int] = []
    for part in _clean_text(row.get("parent_path")).split("/"):
        clean = part.strip()
        if not clean:
            continue
        try:
            ids.append(int(clean))
        except ValueError:
            continue
    return ids


def root_id(row: dict[str, Any]) -> int:
    ids = parent_path_ids(row)
    if ids:
        return ids[0]
    record_id = _maybe_int(row.get("id"))
    return int(record_id or 0)


def display_uom(row: dict[str, Any]) -> str:
    return _clean_text(row.get("display_name")) or _clean_text(row.get("name")) or f"#{row.get('id')}"


def _business_uom_key(row: dict[str, Any]) -> str:
    return display_uom(row).casefold().strip()


def business_equivalence_override(source: dict[str, Any], target: dict[str, Any]) -> str:
    names = frozenset({_business_uom_key(source), _business_uom_key(target)})
    if names in BUSINESS_EQUIVALENT_UOM_PAIRS:
        return "Units and PCS (P) are user-approved business-equivalent UoMs at 1:1."
    return ""


def summarize_uom(row: dict[str, Any], *, root_row: dict[str, Any] | None = None) -> dict[str, Any]:
    root = root_id(row)
    return {
        "id": row.get("id"),
        "name": row.get("name"),
        "display_name": row.get("display_name"),
        "active": row.get("active"),
        "factor": row.get("factor"),
        "relative_factor": row.get("relative_factor"),
        "relative_uom_id": row.get("relative_uom_id"),
        "rounding": row.get("rounding"),
        "parent_path": row.get("parent_path"),
        "root_id": root,
        "root_name": display_uom(root_row or {}) if root_row else "",
    }


def format_decimal(value: Decimal) -> str:
    normalized = value.normalize()
    if normalized == normalized.to_integral():
        return str(normalized.quantize(Decimal(1)))
    return format(normalized, "f")


def conversion_result(source: dict[str, Any], target: dict[str, Any], quantity: Decimal) -> dict[str, Any]:
    source_root = root_id(source)
    target_root = root_id(target)
    if not source_root or not target_root:
        return {
            "inline": False,
            "reason": "Cannot determine parent_path root for one or both UoMs.",
            "converted_quantity": None,
        }
    if source_root != target_root:
        override_reason = business_equivalence_override(source, target)
        if override_reason:
            return {
                "inline": True,
                "reason": override_reason,
                "formula": "target_qty = source_qty (1:1 business-equivalence override)",
                "source_factor": format_decimal(_to_decimal(source.get("factor"), label="source.factor")),
                "target_factor": format_decimal(_to_decimal(target.get("factor"), label="target.factor")),
                "converted_quantity": format_decimal(quantity),
                "override": "units_pcs_p_1_to_1",
            }
        return {
            "inline": False,
            "reason": "Different parent_path roots. Treating this conversion as blocked/fatal.",
            "converted_quantity": None,
        }
    source_factor = _to_decimal(source.get("factor"), label="source.factor")
    target_factor = _to_decimal(target.get("factor"), label="target.factor")
    if target_factor == 0:
        raise UomInlineError("target.factor is zero; conversion is unsafe")
    converted = quantity * source_factor / target_factor
    return {
        "inline": True,
        "reason": "Same parent_path root.",
        "formula": "target_qty = source_qty * source.factor / target.factor",
        "source_factor": format_decimal(source_factor),
        "target_factor": format_decimal(target_factor),
        "converted_quantity": format_decimal(converted),
    }


async def _read_by_ids(conn: Any, ids: list[int], fields: list[str]) -> dict[int, dict[str, Any]]:
    clean_ids = sorted({int(item) for item in ids if int(item) > 0})
    if not clean_ids:
        return {}
    rows = await conn.client.read(MODEL, clean_ids, fields=fields, stage="UOM_INLINE_READ")
    return {int(row["id"]): row for row in rows if row.get("id") is not None}


async def _search_uom(conn: Any, ref: str, *, fields: list[str], allow_ilike: bool) -> dict[str, Any]:
    record_id = _maybe_int(ref)
    if record_id is not None:
        rows_by_id = await _read_by_ids(conn, [record_id], fields)
        row = rows_by_id.get(record_id)
        if row:
            return row
        raise UomInlineError(f"UoM id not found: {record_id}")

    text = _clean_text(ref)
    if not text:
        raise UomInlineError("UoM reference is empty")
    exact_domain = ["|", ["name", "=", text], ["display_name", "=", text]]
    rows = await conn.client.search_read(
        MODEL,
        exact_domain,
        fields=fields,
        limit=5,
        stage="UOM_INLINE_SEARCH_EXACT",
    )
    if len(rows) == 1:
        return rows[0]
    if len(rows) > 1:
        raise UomInlineError(f"Ambiguous exact UoM reference {text!r}: {len(rows)} matches")
    if not allow_ilike:
        raise UomInlineError(f"UoM not found by exact name/display_name: {text!r}")

    fuzzy_domain = ["|", ["name", "ilike", text], ["display_name", "ilike", text]]
    rows = await conn.client.search_read(
        MODEL,
        fuzzy_domain,
        fields=fields,
        limit=10,
        stage="UOM_INLINE_SEARCH_ILIKE",
    )
    if len(rows) == 1:
        return rows[0]
    if not rows:
        raise UomInlineError(f"UoM not found by ilike: {text!r}")
    candidates = [display_uom(row) for row in rows]
    raise UomInlineError(f"Ambiguous ilike UoM reference {text!r}: {candidates}")


async def amain() -> int:
    args = parse_args()
    quantity = _to_decimal(args.quantity, label="quantity")
    conn = await open_tool_connection(
        database_profile_id=args.database_profile,
        max_concurrency=args.max_concurrency,
        verbose=args.verbose,
    )
    try:
        snapshot = await snapshot_model_schema(conn.client, MODEL)
        missing_required = [field for field in REQUIRED_FIELDS if field not in snapshot.fields]
        if missing_required:
            raise UomInlineError(f"Required uom.uom field(s) are missing: {', '.join(missing_required)}")
        fields = [field for field in READ_FIELDS if field in snapshot.fields]

        source = await _search_uom(conn, args.source, fields=fields, allow_ilike=bool(args.allow_ilike))
        target = await _search_uom(conn, args.target, fields=fields, allow_ilike=bool(args.allow_ilike))
        lineage_ids = sorted(set(parent_path_ids(source) + parent_path_ids(target)))
        lineage_rows = await _read_by_ids(conn, lineage_ids, fields)

        result = conversion_result(source, target, quantity)
        source_root = lineage_rows.get(root_id(source))
        target_root = lineage_rows.get(root_id(target))

        payload = build_base_payload(conn, action="uom_inline_check")
        payload.update(
            {
                "model": MODEL,
                "quantity": format_decimal(quantity),
                "source_input": args.source,
                "target_input": args.target,
                "source_uom": summarize_uom(source, root_row=source_root),
                "target_uom": summarize_uom(target, root_row=target_root),
                "source_lineage": [summarize_uom(lineage_rows[item]) for item in parent_path_ids(source) if item in lineage_rows],
                "target_lineage": [summarize_uom(lineage_rows[item]) for item in parent_path_ids(target) if item in lineage_rows],
                "result": result,
                "guardrail": (
                    "Only convert when source and target share the same parent_path root. "
                    "Different roots are blocked unless a narrow user-approved business-equivalence override exists, "
                    "currently Units <-> PCS (P) at 1:1."
                ),
            }
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        if not args.no_artifact:
            path = write_json_artifact(payload, output_dir=args.output_dir, prefix=f"uom_inline_{args.source}_to_{args.target}")
            print(f"Artefact: {path}")
        return 0
    except UomInlineError as exc:
        payload = {"action": "uom_inline_check", "ok": False, "error": str(exc)}
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return 2
    finally:
        await conn.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(amain()))
