#!/usr/bin/env python3
"""Create a read-only schema snapshot for one or more Odoo models."""

from __future__ import annotations

import argparse
import asyncio
import json

from common import add_common_connection_args, build_base_payload, open_tool_connection, write_json_artifact
from smartscc_tools.services.odoo.schema import normalize_model_names, snapshot_many_model_schemas


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Snapshot Odoo fields_get metadata for selected models.")
    parser.add_argument("--model", action="append", required=True, help="Model name. Repeat or comma-separate.")
    parser.add_argument("--attributes", default="", help="Comma-separated fields_get attributes. Default is curated.")
    add_common_connection_args(parser)
    return parser.parse_args()


async def amain() -> int:
    args = parse_args()
    models = normalize_model_names(part for raw in args.model for part in str(raw).split(","))
    attrs = [part.strip() for part in str(args.attributes or "").split(",") if part.strip()] or None
    conn = await open_tool_connection(
        database_profile_id=args.database_profile,
        max_concurrency=args.max_concurrency,
        verbose=args.verbose,
    )
    try:
        snapshots = await snapshot_many_model_schemas(conn.client, models, attributes=attrs)
        payload = build_base_payload(conn, action="schema_snapshot")
        payload["models"] = [item.to_mapping() for item in snapshots]
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        if not args.no_artifact:
            path = write_json_artifact(payload, output_dir=args.output_dir, prefix="schema_" + "_".join(models))
            print(f"Artefact: {path}")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(amain()))

