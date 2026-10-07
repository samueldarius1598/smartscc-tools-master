#!/usr/bin/env python3
"""Trace specific Odoo records by id or read-only domain."""

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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read specific Odoo records without writing.")
    parser.add_argument("--model", required=True, help="Model name.")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--id", type=int, action="append", dest="ids", help="Record id. Repeat as needed.")
    target.add_argument("--domain", help="Domain JSON. Example: [[\"name\",\"=\",\"Unit(s)\"]]")
    parser.add_argument("--fields", default="", help="Comma-separated fields. Empty means Odoo default read fields.")
    parser.add_argument("--limit", type=int, default=20, help="Max rows for domain mode.")
    add_common_connection_args(parser)
    return parser.parse_args()


async def amain() -> int:
    args = parse_args()
    fields = parse_csv_values(args.fields)
    conn = await open_tool_connection(
        database_profile_id=args.database_profile,
        max_concurrency=args.max_concurrency,
        verbose=args.verbose,
    )
    try:
        if args.ids:
            domain = [["id", "in", [int(item) for item in args.ids]]]
            rows = await conn.client.read(args.model, [int(item) for item in args.ids], fields=fields or None)
        else:
            domain = parse_json_domain(args.domain)
            rows = await conn.client.search_read(
                args.model,
                domain,
                fields=fields or None,
                limit=max(1, int(args.limit)),
                stage=f"TRACE_RECORD_{args.model}",
            )
        payload = build_base_payload(conn, action="trace_record")
        payload.update({"model": args.model, "domain": domain, "fields": fields, "rows": rows})
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        if not args.no_artifact:
            path = write_json_artifact(payload, output_dir=args.output_dir, prefix=f"trace_{args.model}")
            print(f"Artefact: {path}")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(amain()))

