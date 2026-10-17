#!/usr/bin/env python3
"""
IMS 2.0 -- stamp the shop on old vendor bills (audit F63)
=========================================================
Every Purchase tab now scopes on a bill's `store_id` (the shop the goods landed
in), stamped at booking since this change. Older bills carry none, so a shop
filter would hide them. This fills it the way booking does:

  * a bill linked to a goods receipt / delivery challans -> that receipt's shop
    (purchase_invoices._receipt_store_id, the booking's own rule)
  * an inter-company transfer's mirror bill -> its receiving shop (to_store_id)
  * anything else (a services bill with no receipt) is left alone and counted:
    admins still see it under All stores.

SAFETY: --dry-run is the default; nothing is written without --commit.
Idempotent (only bills with no store_id are touched). Fail-loud on a missing
Mongo (exit 1). Prints no secrets. Each write is audited (BILL_STORE_BACKFILL).
No emoji (Windows cp1252).

  python backend/scripts/backfill_bill_store_id.py            # dry run
  python backend/scripts/backfill_bill_store_id.py --commit
  railway run --service MongoDB -- ".venv\\Scripts\\python.exe" backend/scripts/backfill_bill_store_id.py
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

_BACKEND = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)


def backfill(db, commit: bool = False) -> dict:
    """Stamp store_id on every bill that lacks one and can be placed.
    Returns {"placed": n, "unplaced": n, "samples": [...]}; writes only on commit."""
    from api.routers.purchase_invoices import _receipt_store_id

    bills = db["vendor_bills"]
    placed, unplaced, samples = 0, 0, []
    for b in list(bills.find({"$or": [{"store_id": {"$exists": False}}, {"store_id": None}, {"store_id": ""}]})):
        grn = db["grns"].find_one({"grn_id": b["grn_id"]}) if b.get("grn_id") else None
        store = _receipt_store_id(db, grn, b.get("linked_dc_ids")) or b.get("to_store_id")
        if not store:
            unplaced += 1
            continue
        placed += 1
        if len(samples) < 5:
            samples.append((b.get("bill_number") or b.get("bill_id"), store))
        if commit:
            bills.update_one({"_id": b["_id"]}, {"$set": {"store_id": store}})
            db["audit_logs"].insert_one(
                {
                    "action": "BILL_STORE_BACKFILL",
                    "entity_type": "VENDOR_BILL",
                    "entity_id": b.get("bill_id"),
                    "performed_by": "system:backfill_bill_store_id",
                    "timestamp": datetime.now(tz=timezone.utc),
                    "after_state": {"store_id": store},
                }
            )
    return {"placed": placed, "unplaced": unplaced, "samples": samples}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--commit", action="store_true", help="write (default: dry run)")
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
    out = backfill(db, commit=args.commit)
    mode = "COMMITTED" if args.commit else "DRY RUN (nothing written)"
    print(f"[{mode}] bills placed: {out['placed']}, left without a shop: {out['unplaced']}")
    for number, store in out["samples"]:
        print(f"  {number} -> {store}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
