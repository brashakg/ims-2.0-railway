#!/usr/bin/env python3
"""
IMS 2.0 -- give legacy customers a store_credit field from their ledger (PR #1264)
=================================================================================
Store credit moves by one atomic $inc (customers.apply_store_credit_delta). A
customer with NO store_credit field (or null) starts from 0 there, and a
legacy customer whose balance lives only in credit_note_ledger would then read
short. This sets store_credit = the ledger balance (sum of the entries'
deltas, store_credit_ledger.compute_balance) for every customer whose field
is missing or null. Customers that already carry a number are never touched.

DEPLOY ORDER (PR #1264): run this on prod -- dry run, then --apply -- BEFORE
the PR deploys. After the deploy, a customer with ledger history and no field
is REFUSED store-credit moves (customers.store_credit_balance_unready) until
this has set the field, so a skipped run fails loudly instead of silently
dropping a legacy balance.

SAFETY: dry run by default; nothing is written without --apply. --apply first
writes every customer row it will change to a backup JSON file; each write is
guarded on the field STILL being missing / null. Prints counts only, no
secrets. No emoji (Windows cp1252).

  python backend/scripts/backfill_store_credit_field.py            # dry run
  python backend/scripts/backfill_store_credit_field.py --apply
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime


def plan(db) -> dict:
    """{"sets": [(customer_id, balance)], "skipped_unapplied": [customer_id]}
    -- reads only. A customer holding an advance-refund credit row that was
    written but NOT applied (applied: False -- its balance move is still
    owed, PR #1264) is SKIPPED and reported, never summed: summing it here
    and then applying it on the next press would pay it twice."""
    sets, skipped = [], []
    for cust in db["customers"].find({"store_credit": None}):
        cid = cust.get("customer_id")
        if not cid:
            continue
        rows = list(db["credit_note_ledger"].find({"customer_id": cid}))
        if any(r.get("applied") is False for r in rows):
            skipped.append(cid)
            continue
        balance = round(sum(float(e.get("delta") or 0) for e in rows), 2)
        sets.append((cid, balance))
    return {"sets": sets, "skipped_unapplied": skipped}


def apply(db, sets, backup_path: str) -> int:
    ids = [c for c, _ in sets]
    rows = list(db["customers"].find({"customer_id": {"$in": ids}}, {"_id": 0}))
    with open(backup_path, "w", encoding="utf-8") as f:
        json.dump(rows, f, default=str, indent=1)
    done = 0
    for cid, balance in sets:
        res = db["customers"].update_one({"customer_id": cid, "store_credit": None},
                                         {"$set": {"store_credit": balance}})
        done += int(getattr(res, "modified_count", 0) or 0)
    return done


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--apply", action="store_true", help="write (default: dry run)")
    ap.add_argument("--backup", default=f"store_credit_field_backup_{datetime.now():%Y%m%d_%H%M%S}.json")
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
    nonzero = sum(1 for _, b in out["sets"] if b)
    print(f"[PLAN] customers without a store_credit field: {len(out['sets'])} ({nonzero} with a ledger balance)")
    if out["skipped_unapplied"]:
        print(f"[SKIPPED] {len(out['skipped_unapplied'])} customer(s) with an advance credit not yet applied "
              "(finish their refund first): " + ", ".join(out["skipped_unapplied"]))
    if not args.apply:
        print("[DRY RUN] nothing written. Re-run with --apply.")
        return 0
    n = apply(db, out["sets"], args.backup)
    print(f"[APPLIED] set {n} customers; backup of the rows before: {args.backup}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
