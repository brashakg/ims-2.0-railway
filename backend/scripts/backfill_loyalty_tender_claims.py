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

RUN IT TWICE: dry run then --apply BEFORE the deploy, and dry run then --apply
again STRAIGHT AFTER it -- a loyalty payment the old code takes in between
leaves its burn unclaimed until the second run. Safe to run any number of
times. (Since #1264 the payment door also refuses a burn an existing LOYALTY
payment on the order already names, backfilled or not.)

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
    """{"links": [(txn_id, payment_id)], "unmatched": n, "unbacked": [...],
    "no_order": n} -- reads only. ``unbacked``: LOYALTY payments no burn
    backs (no linked row, and none still claimable)."""
    links, unmatched, used = [], 0, set()
    unmatched_rows, legacy = [], []
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
            due = order.get("balance_due")
            unmatched_rows.append((row["txn_id"], row.get("order_id"), due))
            if due is not None and float(due or 0) <= 0.01:
                legacy.append(row["txn_id"])  # a settled bill: never claimable again
    claimed = {r.get("tender_payment_id") for r in txns.find({"type": "REDEEM", "tender_payment_id": {"$ne": None}})}
    unbacked = []
    for order in db["orders"].find({}):  # ponytail: one pass over all orders; fine at shop scale
        for p in order.get("payments") or []:
            if str(p.get("method") or "").upper() == "LOYALTY" and p.get("payment_id") not in used | claimed:
                unbacked.append((order.get("order_id"), p.get("payment_id"), p.get("amount"), p.get("reference")))
    no_order = txns.count_documents({"type": "REDEEM", "order_id": {"$in": [None, ""]}})
    scanned = {"redeem_rows": txns.count_documents({"type": "REDEEM"}),
               "orders": db["orders"].count_documents({})}
    return {"links": links, "unmatched": unmatched, "unbacked": unbacked, "no_order": no_order,
            "unmatched_rows": unmatched_rows, "legacy": legacy, "scanned": scanned}


LEGACY_CLAIM = "LEGACY-PRE-1088"


def apply(db, links, backup_path: str, legacy=()) -> int:
    """Link each burn to its payment; stamp an unmatched burn on a SETTLED
    bill (balance_due 0) LEGACY-PRE-1088 so no later tender can claim it.
    Both carry tender_backfilled_at, so --revert undoes them exactly."""
    txns = db["loyalty_transactions"]
    links = list(links) + [(t, LEGACY_CLAIM) for t in legacy]
    ids = [t for t, _ in links]
    rows = list(txns.find({"txn_id": {"$in": ids}}, {"_id": 0}))
    os.makedirs(os.path.dirname(backup_path) or ".", exist_ok=True)
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


def revert(db) -> int:
    """Undo every link this script made: exact, because only its own links
    carry tender_backfilled_at (a live payment's claim never does)."""
    txns = db["loyalty_transactions"]
    done = 0
    for row in list(txns.find({"tender_backfilled_at": {"$ne": None}})):
        res = txns.update_one({"txn_id": row["txn_id"], "tender_backfilled_at": row["tender_backfilled_at"]},
                              {"$unset": {"tender_payment_id": "", "tender_backfilled_at": ""}})
        done += int(getattr(res, "modified_count", 0) or 0)
    return done


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--apply", action="store_true", help="write (default: dry run)")
    ap.add_argument("--backup",
                    default=os.path.join("backups", f"loyalty_claims_backup_{datetime.now():%Y%m%d_%H%M%S}.json"))
    ap.add_argument("--revert", action="store_true", help="undo every link this script made")
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
        print(f"[REVERTED] unlinked {revert(db)} redemptions")
        return 0
    out = plan(db)
    print(f"[PLAN] redemptions to link: {len(out['links'])}; with no matching payment (left alone): {out['unmatched']}")
    print(f"[SCANNED] REDEEM rows: {out['scanned']['redeem_rows']}; orders: {out['scanned']['orders']} "
          "(0 and 0 means the wrong or an empty database)")
    print(f"[PLAN] redemptions with no order (inert, never claimable): {out['no_order']}")
    for txn_id, oid, due in out["unmatched_rows"]:
        print(f"  [UNMATCHED] burn {txn_id} on order {oid}, balance due {due}"
              + (" -> stamped LEGACY-PRE-1088 on --apply (settled bill)" if txn_id in out["legacy"] else ""))
    for oid, pid, amt, ref in out["unbacked"]:
        print(f"  [UNBACKED] order {oid} payment {pid} Rs {amt} ref {ref!r}")
    if not args.apply:
        print("[DRY RUN] nothing written. Re-run with --apply.")
        return 0
    n = apply(db, out["links"], args.backup, out["legacy"])
    print(f"[APPLIED] linked {n} redemptions; backup of the rows before: {args.backup}")
    if n < len(out["links"]) + len(out["legacy"]):
        print(f"[SKIPPED] {len(out['links']) + len(out['legacy']) - n} (claimed by a live payment between plan and apply - "
              "re-run the dry run: it must show 0 to link)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
