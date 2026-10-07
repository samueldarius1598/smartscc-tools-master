#!/usr/bin/env python3
"""Inspect one Odoo model schema and optional sample rows."""

from __future__ import annotations

import argparse
import asyncio
import json

from common import (
    add_common_connection_args,
    build_base_payload,
    open_tool_connection,
    parse_csv_values,
    parse_json_domain,
    write_json_artifact,
)
from smartscc_tools.services.odoo.schema import filter_schema_fields, snapshot_model_schema


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect an Odoo model with fields_get and optional search_read sample.")
    parser.add_argument("--model", required=True, help="Model name, for example uom.uom.")
    parser.add_argument("--fields", default="", help="Comma-separated fields to include/read.")
    parser.add_argument("--domain", default="[]", help="search_read domain JSON for sample rows.")
    parser.add_argument("--sample-limit", type=int, default=0, help="Optional sample row count. Default: 0.")
    add_common_connection_args(parser)
    return parser.parse_args()


async def amain() -> int:
    args = parse_args()
    requested_fields = parse_csv_values(args.fields)
    domain = parse_json_domain(args.domain)
    conn = await open_tool_connection(
        database_profile_id=args.database_profile,
        max_concurrency=args.max_concurrency,
        verbose=args.verbose,
    )
    try:
        snapshot = await snapshot_model_schema(conn.client, args.model)
        fields = filter_schema_fields(snapshot.fields, include_names=requested_fields)
        sample_fields = requested_fields
        if not sample_fields and args.sample_limit > 0:
            sample_fields = [name for name in ("id", "display_name", "name", "active") if name in snapshot.fields]
        sample_rows = []
        if args.sample_limit > 0:
            sample_rows = await conn.client.search_read(
                args.model,
                domain,
                fields=sample_fields or None,
                limit=max(1, int(args.sample_limit)),
                stage=f"INSPECT_MODEL_SAMPLE_{args.model}",
            )
        payload = build_base_payload(conn, action="inspect_model")
        payload.update(
            {
                "model": args.model,
                "model_row": snapshot.model_row,
                "field_count": len(snapshot.fields),
                "fields": fields,
                "sample_domain": domain,
                "sample_limit": max(0, int(args.sample_limit)),
                "sample_rows": sample_rows,
            }
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        if not args.no_artifact:
            path = write_json_artifact(payload, output_dir=args.output_dir, prefix=f"inspect_{args.model}")
            print(f"Artefact: {path}")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(amain()))

