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
customer's store_credit_refs. Such a row is applied exactly once later by
its own door (the store_credit_refs guard), so nobody is locked out and
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
import math
import os
import sys
from datetime import datetime, timezone


def _num(value) -> float:
    """A finite int or float (a missing delta is 0), else ValueError: a
    string, a bool, NaN or inf is not an amount -- it stops the run."""
    if value is None:
        return 0.0
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("not a finite number")
    return float(value)


def plan(db) -> dict:
    """{"sets": [(customer_id, balance)], "unapplied_left": [customer_id],
    "negative": [(customer_id, total)], "non_numeric": [(customer_id, value)]}
    -- reads only. ``non_numeric``: a store_credit that is not a number (a
    string, say) -- the $inc refuses it, so it is a stop before deploy."""
    sets, unapplied, negative, bad_delta = [], [], [], []
    void_numbered = []
    scanned = {"customers": db["customers"].count_documents({}),
               "ledger_rows": db["credit_note_ledger"].count_documents({})}
    non_numeric = [
        (c.get("customer_id"), c.get("store_credit"))
        for c in db["customers"].find({})  # ponytail: one pass; fine at shop scale
        if c.get("store_credit") is not None
        and (isinstance(c.get("store_credit"), bool) or not isinstance(c.get("store_credit"), (int, float))
             or not math.isfinite(c.get("store_credit")))
    ]
    # The ledger read ONCE (not one query per customer), grouped by customer.
    by_customer: dict = {}
    for r in db["credit_note_ledger"].find({}):
        by_customer.setdefault(r.get("customer_id"), []).append(r)

    def _applied_sum(cust):
        refs = set(cust.get("store_credit_refs") or [])
        total = 0.0
        for r in by_customer.get(cust.get("customer_id"), []):
            if r.get("applied") is False and r.get("entry_id") not in refs:
                continue
            try:
                total += _num(r.get("delta"))
            except (TypeError, ValueError):
                return None
        return round(total, 2)

    # Read-only: customers whose number differs from their applied ledger
    # (a legacy pre-ledger balance, or a real drift) -- shown, never changed.
    drift = []
    for cust in db["customers"].find({"store_credit": {"$ne": None}}):
        value, total = cust.get("store_credit"), _applied_sum(cust)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) \
                and total is not None and abs(float(value) - total) > 0.01:
            drift.append((cust.get("customer_id"), round(float(value), 2), total))

    for cust in db["customers"].find({"store_credit": None}):
        cid = cust.get("customer_id")
        if not cid:
            continue
        refs = set(cust.get("store_credit_refs") or [])
        balance = 0.0
        broken = False
        for r in by_customer.get(cid, []):
            if r.get("applied") is False and r.get("entry_id") not in refs:
                if r.get("type") == "VOID" and r.get("note_number"):
                    void_numbered.append(cid)  # never finishes: a new return is needed
                else:
                    unapplied.append(cid)  # ISSUING / numberless: its door finishes it, once
                continue
            try:
                balance += _num(r.get("delta"))
            except (TypeError, ValueError):
                bad_delta.append((cid, r.get("entry_id"), r.get("delta")))
                broken = True
        if broken:
            continue
        balance = round(balance, 2)
        if balance < 0:
            negative.append((cid, balance))
            continue
        sets.append((cid, balance))
    return {"sets": sets, "unapplied_left": sorted(set(unapplied)), "negative": negative,
            "non_numeric": non_numeric, "non_numeric_delta": bad_delta,
            "void_numbered": sorted(set(void_numbered)), "scanned": scanned, "drift": drift}


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


def hand_set(db, sets, backup_path: str) -> int:
    """The exit-2 hand fix (``--set CUSTOMER_ID=AMOUNT``): the owner decided a
    customer's balance. Written ONLY where the field is missing or not a
    number (a numeric balance is never overwritten here), backed up first,
    and stamped like a backfill so ``--revert`` undoes it."""
    ids = [c for c, _ in sets]
    rows = list(db["customers"].find({"customer_id": {"$in": ids}}, {"_id": 0, "customer_id": 1, "store_credit": 1}))
    os.makedirs(os.path.dirname(backup_path) or ".", exist_ok=True)
    with open(backup_path, "w", encoding="utf-8") as f:
        json.dump(rows, f, default=str, indent=1)
    by_id = {r.get("customer_id"): r.get("store_credit") for r in rows}
    done = 0
    for cid, amount in sets:
        old = by_id.get(cid, "absent")
        if old == "absent":
            print(f"  [SET-REFUSED] {cid}: no such customer")
            continue
        if isinstance(old, (int, float)) and not isinstance(old, bool) and math.isfinite(old):
            print(f"  [SET-REFUSED] {cid}: already has a numeric balance Rs {float(old):.2f} - never overwritten")
            continue
        res = db["customers"].update_one(
            {"customer_id": cid, "store_credit": old},
            {"$set": {"store_credit": amount, "store_credit_backfill_value": amount,
                      "store_credit_backfilled_at": datetime.now(tz=timezone.utc), "store_credit_hand_set": True}})
        done += int(getattr(res, "modified_count", 0) or 0)
    return done


def revert(db, backup_path: str) -> int:
    """Undo --apply for the customers in the backup, only where the balance
    still equals what the script set (a live move since means fix forward)."""
    with open(backup_path, encoding="utf-8") as f:
        ids = [r.get("customer_id") for r in json.load(f) if r.get("customer_id")]
    done = 0
    for cust in db["customers"].find({"customer_id": {"$in": ids}, "store_credit_backfilled_at": {"$ne": None}}):
        if cust.get("store_credit_refs"):
            continue  # a live credit moved this balance since: fix forward, never revert
        res = db["customers"].update_one(
            {"customer_id": cust["customer_id"], "store_credit": cust.get("store_credit_backfill_value")},
            {"$unset": {"store_credit": "", "store_credit_backfill_value": "", "store_credit_backfilled_at": ""}})
        done += int(getattr(res, "modified_count", 0) or 0)
    if done < len(ids):
        print(f"[SKIPPED] {len(ids) - done} (balance moved since the backfill, or never set by it - fix forward)")
    return done


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--apply", action="store_true", help="write (default: dry run)")
    ap.add_argument("--backup",
                    default=os.path.join("backups", f"store_credit_field_backup_{datetime.now():%Y%m%d_%H%M%S}.json"))
    ap.add_argument("--revert", metavar="BACKUP", help="undo an --apply run from its backup file")
    ap.add_argument("--set", action="append", default=[], metavar="CUSTOMER_ID=AMOUNT",
                    help="the exit-2 hand fix: set a decided balance where the field is missing or not a number")
    args = ap.parse_args()
    try:
        hand = [(c.strip(), round(_num(float(a)), 2)) for c, a in (x.split("=", 1) for x in args.set)]
    except (ValueError, TypeError):
        print("[USAGE] --set takes CUSTOMER_ID=AMOUNT with a number")
        return 1
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
    print(f"[DB] {getattr(db, 'name', '?')}")
    if args.revert:
        print(f"[REVERTED] {revert(db, args.revert)} customers")
        return 0
    if hand:
        n = hand_set(db, hand, args.backup)
        print(f"[SET] {n} customer(s) set by hand; backup of the rows before: {args.backup}")
        return 0
    out = plan(db)
    print(f"[SCANNED] customers: {out['scanned']['customers']}; credit ledger rows: "
          f"{out['scanned']['ledger_rows']} (0 and 0 means the wrong or an empty database)")
    if not out["scanned"]["customers"]:
        print("[STOPPED] no customers scanned - wrong or empty database. Nothing written. (exit 3)")
        return 3
    for cid, value, total in out["drift"]:
        print(f"  [DRIFT] {cid}: balance Rs {value:.2f}, ledger Rs {total:.2f} (read-only - not changed)")
    nonzero = [(c, b) for c, b in out["sets"] if b]
    print(f"[PLAN] customers without a store_credit field: {len(out['sets'])} ({len(nonzero)} with a ledger balance)")
    for cid, bal in nonzero:
        print(f"  {cid}: Rs {bal:.2f}")
    if out["unapplied_left"]:
        print(f"[UNAPPLIED] {len(out['unapplied_left'])} customer(s) hold an unfinished credit row left out of "
              "the sum (its own door finishes it on the next press, once): " + ", ".join(out["unapplied_left"]))
    if out["void_numbered"]:
        print(f"[VOIDED] {len(out['void_numbered'])} customer(s) hold a voided credit note that never moved "
              "money (a new return is needed if credit is owed): " + ", ".join(out["void_numbered"]))
    if out["non_numeric_delta"]:
        print(f"[BAD-DELTA] {len(out['non_numeric_delta'])} ledger row(s) with a non-numeric delta - those "
              "customers were left out; fix by hand: "
              + ", ".join(f"{c} row {e} ({d!r})" for c, e, d in out["non_numeric_delta"]))
    if out["non_numeric"]:
        print(f"[NON-NUMERIC] {len(out['non_numeric'])} customer(s) whose store_credit is not a number - STOP, "
              "fix each by hand before the deploy: " + ", ".join(f"{c} ({v!r})" for c, v in out["non_numeric"]))
    if out["negative"]:
        print(f"[REFUSED] {len(out['negative'])} customer(s) with a NEGATIVE ledger total, not set - check by hand: "
              + ", ".join(f"{c} (Rs {b:.2f})" for c, b in out["negative"]))
    stop = bool(out["non_numeric"] or out["negative"] or out["non_numeric_delta"])
    if not args.apply:
        print("[DRY RUN] nothing written. Re-run with --apply.")
        return 2 if stop else 0
    if stop:
        print("[STOPPED] fix the customers listed above first - nothing was written. (exit 2)")
        return 2
    n = apply(db, out["sets"], args.backup)
    print(f"[APPLIED] set {n} customers; backup of the rows before: {args.backup}")
    if n < len(out["sets"]):
        print(f"[SKIPPED] {len(out['sets']) - n} (the field was set by a live door between plan and apply - "
              "re-run the dry run: it must show 0 customers)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
