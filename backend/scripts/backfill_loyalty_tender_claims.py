#!/usr/bin/env python3
"""
IMS 2.0 -- link pre-deploy loyalty redemptions to the payment they paid (PR #1264)
=================================================================================
Since #1264 a LOYALTY payment CLAIMS the REDEEM ledger row behind it
(loyalty_transactions.tender_payment_id), so one redemption pays one bill line
once. Rows written before that deploy carry no claim, so an old burn whose
LOYALTY payment is already on its order would look unused and could back a
SECOND loyalty payment. This links each such row to its payment.

Match rule (exact, never a guess): the till records a LOYALTY payment with
reference "<points>pts txn <txn_id>". A REDEEM row is linked only to a LOYALTY
payment on ITS OWN order whose reference names ITS txn_id, and each payment
backs one row. Rows with no such payment are counted, never touched.

SAFETY: dry run by default; nothing is written without --apply. --apply first
writes every row it will change to a backup JSON file. Idempotent (only rows
with no claim are touched). Prints no secrets. No emoji (Windows cp1252).

  python backend/scripts/backfill_loyalty_tender_claims.py            # dry run
  python backend/scripts/backfill_loyalty_tender_claims.py --apply
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

_TXN_IN_REFERENCE = re.compile(r"\btxn\s+(\S+)")


def plan(db) -> dict:
    """{"links": [(txn_id, payment_id)], "unmatched": n} -- reads only."""
    links, unmatched, used = [], 0, set()
    txns = db["loyalty_transactions"]
    unclaimed = {"type": "REDEEM", "order_id": {"$nin": [None, ""]}, "tender_payment_id": None}
    for row in txns.find(unclaimed):
        order = db["orders"].find_one({"order_id": row["order_id"]}) or {}
        hit = None
        for p in order.get("payments") or []:
            if str(p.get("method") or "").upper() != "LOYALTY":
                continue
            m = _TXN_IN_REFERENCE.search(str(p.get("reference") or ""))
            if m and m.group(1) == row.get("txn_id") and p.get("payment_id") not in used:
                hit = p.get("payment_id")
                break
        if hit:
            used.add(hit)
            links.append((row["txn_id"], hit))
        else:
            unmatched += 1
    return {"links": links, "unmatched": unmatched}


def apply(db, links, backup_path: str) -> int:
    txns = db["loyalty_transactions"]
    ids = [t for t, _ in links]
    rows = list(txns.find({"txn_id": {"$in": ids}}, {"_id": 0}))
    with open(backup_path, "w", encoding="utf-8") as f:
        json.dump(rows, f, default=str, indent=1)
    done = 0
    for txn_id, payment_id in links:
        res = txns.update_one(
            {"txn_id": txn_id, "tender_payment_id": None},
            {"$set": {"tender_payment_id": payment_id, "tender_backfilled_at": datetime.now(tz=timezone.utc)}},
        )
        done += int(getattr(res, "modified_count", 0) or 0)
    return done


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--apply", action="store_true", help="write (default: dry run)")
    ap.add_argument("--backup", default=f"loyalty_claims_backup_{datetime.now():%Y%m%d_%H%M%S}.json")
    args = ap.parse_args()
    url = os.getenv("MONGO_PUBLIC_URL") or os.getenv("MONGO_URL") or os.getenv("MONGODB_URL")
    print(f"[MONGO] connection string: {'SET' if url else 'NOT SET'}")
    if not url:
        return 1
    try:
        from pymongo import MongoClient

        client = MongoClient(url, serverSelectionTimeoutMS=20_000)
        client.admin.command("ping")
    except Exception as exc:  # noqa: BLE001
        print(f"[MONGO] connect failed: {type(exc).__name__}")
        return 1
    db = client[os.getenv("MONGO_DATABASE", "ims_2_0")]
    out = plan(db)
    print(f"[PLAN] redemptions to link: {len(out['links'])}; with no matching payment (left alone): {out['unmatched']}")
    if not args.apply:
        print("[DRY RUN] nothing written. Re-run with --apply.")
        return 0
    n = apply(db, out["links"], args.backup)
    print(f"[APPLIED] linked {n} redemptions; backup of the rows before: {args.backup}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
