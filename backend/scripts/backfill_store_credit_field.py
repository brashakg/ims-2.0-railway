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

Only rows that MOVED the balance are summed: a row written but not applied
(applied: False -- an advance credit whose balance move is still owed, or an
unfinished / voided write) is left out unless its id is already in the
customer's advance_credit_refs. Such a row is applied exactly once later by
its own door (the advance_credit_refs guard), so nobody is locked out and
nothing is paid twice. A NEGATIVE total is refused and reported, never set.

SAFETY: dry run by default; nothing is written without --apply. The dry run
lists every customer_id with a non-zero total and its amount (ids are not
secret). --apply first writes {customer_id, store_credit} of every customer
it will change to a backup JSON file under backups/ (git-ignored); each write
is guarded on the field STILL being missing / null, and stamps
store_credit_backfilled_at + store_credit_backfill_value so --revert can
undo exactly the customers it set (only where the balance has not moved
since). No secrets printed. No emoji (Windows cp1252).

  python backend/scripts/backfill_store_credit_field.py            # dry run
  python backend/scripts/backfill_store_credit_field.py --apply
  python backend/scripts/backfill_store_credit_field.py --revert backups/<file>.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone


def plan(db) -> dict:
    """{"sets": [(customer_id, balance)], "unapplied_left": [customer_id],
    "negative": [(customer_id, total)]} -- reads only."""
    sets, unapplied, negative = [], [], []
    for cust in db["customers"].find({"store_credit": None}):
        cid = cust.get("customer_id")
        if not cid:
            continue
        refs = set(cust.get("advance_credit_refs") or [])
        balance = 0.0
        for r in db["credit_note_ledger"].find({"customer_id": cid}):
            if r.get("applied") is False and r.get("entry_id") not in refs:
                unapplied.append(cid)  # its own door applies it later, once
                continue
            balance += float(r.get("delta") or 0)
        balance = round(balance, 2)
        if balance < 0:
            negative.append((cid, balance))
            continue
        sets.append((cid, balance))
    return {"sets": sets, "unapplied_left": sorted(set(unapplied)), "negative": negative}


def apply(db, sets, backup_path: str) -> int:
    ids = [c for c, _ in sets]
    rows = list(db["customers"].find({"customer_id": {"$in": ids}}, {"_id": 0, "customer_id": 1, "store_credit": 1}))
    os.makedirs(os.path.dirname(backup_path) or ".", exist_ok=True)
    with open(backup_path, "w", encoding="utf-8") as f:
        json.dump(rows, f, default=str, indent=1)
    done = 0
    for cid, balance in sets:
        res = db["customers"].update_one(
            {"customer_id": cid, "store_credit": None},
            {"$set": {"store_credit": balance, "store_credit_backfill_value": balance,
                      "store_credit_backfilled_at": datetime.now(tz=timezone.utc)}})
        done += int(getattr(res, "modified_count", 0) or 0)
    return done


def revert(db, backup_path: str) -> int:
    """Undo --apply for the customers in the backup, only where the balance
    still equals what the script set (a live move since means fix forward)."""
    with open(backup_path, encoding="utf-8") as f:
        ids = [r.get("customer_id") for r in json.load(f) if r.get("customer_id")]
    done = 0
    for cust in db["customers"].find({"customer_id": {"$in": ids}, "store_credit_backfilled_at": {"$ne": None}}):
        res = db["customers"].update_one(
            {"customer_id": cust["customer_id"], "store_credit": cust.get("store_credit_backfill_value")},
            {"$unset": {"store_credit": "", "store_credit_backfill_value": "", "store_credit_backfilled_at": ""}})
        done += int(getattr(res, "modified_count", 0) or 0)
    return done


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--apply", action="store_true", help="write (default: dry run)")
    ap.add_argument("--backup",
                    default=os.path.join("backups", f"store_credit_field_backup_{datetime.now():%Y%m%d_%H%M%S}.json"))
    ap.add_argument("--revert", metavar="BACKUP", help="undo an --apply run from its backup file")
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
    if args.revert:
        print(f"[REVERTED] {revert(db, args.revert)} customers")
        return 0
    out = plan(db)
    nonzero = [(c, b) for c, b in out["sets"] if b]
    print(f"[PLAN] customers without a store_credit field: {len(out['sets'])} ({len(nonzero)} with a ledger balance)")
    for cid, bal in nonzero:
        print(f"  {cid}: Rs {bal:.2f}")
    if out["unapplied_left"]:
        print(f"[UNAPPLIED] {len(out['unapplied_left'])} customer(s) hold a credit row not yet applied; it is left "
              "out of the sum and applied by its own door later: " + ", ".join(out["unapplied_left"]))
    if out["negative"]:
        print(f"[REFUSED] {len(out['negative'])} customer(s) with a NEGATIVE ledger total, not set - check by hand: "
              + ", ".join(f"{c} (Rs {b:.2f})" for c, b in out["negative"]))
    if not args.apply:
        print("[DRY RUN] nothing written. Re-run with --apply.")
        return 0
    n = apply(db, out["sets"], args.backup)
    print(f"[APPLIED] set {n} customers; backup of the rows before: {args.backup}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
