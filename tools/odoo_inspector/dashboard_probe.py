#!/usr/bin/env python3
"""Probe SVL dashboard service paths without clicking the Tkinter UI."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
import json

from common import add_common_connection_args, build_base_payload, open_tool_connection, write_json_artifact
from smartscc_tools.features.svl_fix_je.dashboard_service import SvlDashboardServiceAsync
from smartscc_tools.features.svl_fix_je.models import SvlDashboardRequest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only probe for SVL dashboard service flows.")
    parser.add_argument("--company-id", type=int, default=0, help="Company id for optional analyze probe.")
    parser.add_argument("--date-from", default="", help="YYYY-MM-DD filter.")
    parser.add_argument("--date-to", default="", help="YYYY-MM-DD filter.")
    parser.add_argument("--dataset-mode", default="issues", help="SVL dashboard dataset mode.")
    parser.add_argument("--analyze", action="store_true", help="Run dashboard analyze; default only lists companies.")
    add_common_connection_args(parser)
    return parser.parse_args()


async def amain() -> int:
    args = parse_args()
    conn = await open_tool_connection(
        database_profile_id=args.database_profile,
        max_concurrency=args.max_concurrency,
        verbose=args.verbose,
    )
    try:
        service = SvlDashboardServiceAsync(rpc=conn.client, logger=conn.logger)
        payload = build_base_payload(conn, action="dashboard_probe")
        companies = await service.list_companies()
        payload["companies"] = [asdict(item) for item in companies]
        if args.analyze:
            if args.company_id <= 0:
                raise SystemExit("--company-id is required with --analyze")
            snapshot = await service.analyze(
                SvlDashboardRequest(
                    database=conn.config.database,
                    company_id=int(args.company_id),
                    date_from=str(args.date_from or ""),
                    date_to=str(args.date_to or ""),
                    dataset_mode=str(args.dataset_mode or "issues"),
                )
            )
            payload["analyze_summary"] = {
                "company_id": snapshot.company_id,
                "items": len(snapshot.items),
                "companies": len(snapshot.companies),
                "warnings": list(snapshot.warnings),
            }
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        if not args.no_artifact:
            path = write_json_artifact(payload, output_dir=args.output_dir, prefix="dashboard_probe")
            print(f"Artefact: {path}")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(amain()))

