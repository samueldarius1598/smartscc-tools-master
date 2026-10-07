#!/usr/bin/env python3
"""Guarded manual fix: move STJ journals linked to one picking onto one date."""

from __future__ import annotations

import argparse
import asyncio
from datetime import date
import json

from common import (
    add_manual_common_args,
    batch_method,
    batch_write,
    fetch_related_journals_for_picking,
    open_manual_connection,
    read_company_lock_blockers,
    write_manual_artifact,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Change dates for STJ/account.move rows related to one stock picking.")
    parser.add_argument("--picking", required=True, help="Stock picking number, for example PIKKP/INT/00103.")
    parser.add_argument("--target-date", required=True, help="Target journal date, YYYY-MM-DD.")
    add_manual_common_args(parser)
    return parser.parse_args()


async def amain() -> int:
    args = parse_args()
    try:
        date.fromisoformat(args.target_date)
    except ValueError as exc:
        raise SystemExit("--target-date must be YYYY-MM-DD") from exc

    conn = await open_manual_connection(args)
    try:
        related = await fetch_related_journals_for_picking(conn.client, args.picking)
        journal_rows = related["journal_rows"]
        journal_ids = [int(row["id"]) for row in journal_rows]
        blockers = await read_company_lock_blockers(conn.client, int(related["company_id"]), args.target_date)
        plan = {
            "action": "fix_picking_stj_dates",
            "mode": "apply" if args.apply else "dry_run",
            "database": conn.config.database,
            "picking": args.picking,
            "target_date": args.target_date,
            "company_id": related["company_id"],
            "move_count": len(related["move_ids"]),
            "svl_count": related["svl_count"],
            "journal_count": len(journal_rows),
            "journal_rows": journal_rows,
            "lock_blockers": blockers,
        }
        if args.apply:
            if not journal_ids:
                raise RuntimeError("Tidak ada journal terkait untuk diubah.")
            if blockers:
                raise RuntimeError(f"Target date diblokir lock date: {blockers}")
            posted_ids = [int(row["id"]) for row in journal_rows if str(row.get("state") or "") == "posted"]
            draft_ids = [int(row["id"]) for row in journal_rows if str(row.get("state") or "") != "posted"]
            context = related["context"]
            if posted_ids:
                await batch_method(conn.client, "account.move", "button_draft", posted_ids, context)
                await batch_write(conn.client, "account.move", posted_ids, {"date": args.target_date, "name": "/"}, context)
                await batch_method(conn.client, "account.move", "action_post", posted_ids, context)
            if draft_ids:
                await batch_write(conn.client, "account.move", draft_ids, {"date": args.target_date}, context)
            plan["applied_posted_count"] = len(posted_ids)
            plan["applied_draft_count"] = len(draft_ids)

        print(json.dumps(plan, ensure_ascii=False, indent=2, default=str))
        path = write_manual_artifact(plan, args, f"fix_picking_stj_dates_{args.picking}")
        if path:
            print(f"Artefact: {path}")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(amain()))

