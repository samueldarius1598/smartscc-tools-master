"""Read-only Odoo schema inspection helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from smartscc_tools.services.odoo.gateway import OdooRpcError


DEFAULT_FIELD_ATTRIBUTES = [
    "string",
    "type",
    "required",
    "readonly",
    "relation",
    "selection",
    "store",
    "help",
    "related",
    "depends",
]


@dataclass(frozen=True)
class ModelSchemaSnapshot:
    model: str
    model_row: dict[str, Any]
    fields: dict[str, dict[str, Any]]

    def to_mapping(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "model_row": self.model_row,
            "fields": self.fields,
            "field_count": len(self.fields),
        }


def normalize_model_names(values: Iterable[str]) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = str(value or "").strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        names.append(clean)
    return names


def normalize_field_names(values: Iterable[str] | None) -> list[str]:
    if values is None:
        return []
    names: list[str] = []
    seen: set[str] = set()
    for value in values:
        for part in str(value or "").split(","):
            clean = part.strip()
            if not clean or clean in seen:
                continue
            seen.add(clean)
            names.append(clean)
    return names


async def read_model_rows(rpc: Any, model_names: Iterable[str]) -> dict[str, dict[str, Any]]:
    names = normalize_model_names(model_names)
    if not names:
        return {}
    try:
        rows = await rpc.search_read(
            "ir.model",
            [["model", "in", names]],
            fields=["id", "model", "name", "state", "transient"],
            limit=len(names) + 5,
            order="model asc",
            stage="SCHEMA_MODEL_METADATA",
        )
    except OdooRpcError as exc:
        return {
            name: {
                "model": name,
                "metadata_access_error": str(exc),
            }
            for name in names
        }
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        model = str(row.get("model") or "").strip()
        if model:
            result[model] = row
    return result


async def snapshot_model_schema(
    rpc: Any,
    model_name: str,
    *,
    attributes: Iterable[str] | None = None,
) -> ModelSchemaSnapshot:
    model = str(model_name or "").strip()
    if not model:
        raise ValueError("model_name is required")
    attr_list = list(attributes or DEFAULT_FIELD_ATTRIBUTES)
    model_rows = await read_model_rows(rpc, [model])
    fields = await rpc.fields_get(
        model,
        attributes=attr_list,
        stage=f"SCHEMA_FIELDS_{model}",
    )
    return ModelSchemaSnapshot(
        model=model,
        model_row=model_rows.get(model, {}),
        fields=dict(fields or {}),
    )


async def snapshot_many_model_schemas(
    rpc: Any,
    model_names: Iterable[str],
    *,
    attributes: Iterable[str] | None = None,
) -> list[ModelSchemaSnapshot]:
    names = normalize_model_names(model_names)
    snapshots: list[ModelSchemaSnapshot] = []
    for model in names:
        snapshots.append(await snapshot_model_schema(rpc, model, attributes=attributes))
    return snapshots


def filter_schema_fields(
    fields: dict[str, dict[str, Any]],
    *,
    include_names: Iterable[str] | None = None,
) -> dict[str, dict[str, Any]]:
    names = normalize_field_names(include_names)
    if not names:
        return dict(sorted(fields.items()))
    return {name: fields[name] for name in names if name in fields}
