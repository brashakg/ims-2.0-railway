#!/usr/bin/env python3
"""
IMS 2.0 -- re-queue the LIVE listings so the listing-quality fixes reach them
=============================================================================
The listing-quality fixes (2026-10-22: no doubled brand, a readable model line,
alt text on every photo, no discount promise, mm on measurements, a
non-polarized lens without a 'polar' word, polarized only when every colour
is, the arrival-week tag) change what the push SENDS. A code deploy changes
nothing on Shopify by itself: a live listing is re-sent only when its twin is
queued (ecom.locally_modified). This script queues every live listing once, so
the next push / scheduled sync re-sends it through the same engine and gates.

It queues; it does NOT push. Nothing here talks to Shopify.

LIVE = a lead or single product (not a colour of another listing) with a
Shopify id, not a Shopify Draft, not taken down, archived or deleted -- the
collection registry's own reading (collection_registry.listing_facts).

SAFETY: DRY RUN by default (prints the SKUs and a count, writes nothing).
--apply queues each row the way every queueing write does (the flag plus a
fresh ecom.edit_rev, catalog_ecom.dirty_marks; compare-and-set of the ecom
sub-doc) and records the row and its revision in
`listing_copy_requeue_backups` under a run id. A row someone else queued
meanwhile is left alone and not recorded. --revert --run-id X un-queues only
rows whose ecom.edit_rev is STILL this run's: a row the push already re-sent,
or that a human edit queued again since, is left exactly as it is.
Plain pymongo; init_db is never called. No emoji (Windows cp1252). Prints no
secrets.

  railway run --service ims-2.0-railway '.venv\\Scripts\\python.exe' -I backend/scripts/requeue_live_listing_copy.py
  ... --apply
  ... --revert --run-id <id>
"""
from __future__ import annotations

import argparse
import os
import sys
import uuid
from datetime import datetime, timezone

_BACKEND = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

BACKUPS = "listing_copy_requeue_backups"


def live_listings(db) -> list:
    """Every live listing's twin: {id, sku, queued}. Pure read."""
    from api.services.shopify_push import is_variant_of

    out = []
    for doc in db["catalog_products"].find({"ecom.shopify_product_id": {"$nin": [None, ""]}}):
        ecom = doc.get("ecom") or {}
        if (
            is_variant_of(doc)
            or doc.get("deleted_at")
            or ecom.get("taken_down_at")
            or str(ecom.get("status") or "").upper() in ("ARCHIVED", "DRAFT")
        ):
            continue
        out.append({"id": doc.get("id"), "sku": doc.get("sku"), "queued": bool(ecom.get("locally_modified"))})
    return out


def apply(db, rows: list, run_id: str) -> int:
    """Record, then queue (flag + a fresh edit_rev). A row the write declines
    (queued meanwhile) loses its record again. Returns how many were queued."""
    from api.services.catalog_ecom import EDIT_REV, dirty_marks, update_ecom

    now = datetime.now(tz=timezone.utc)
    done = 0
    for r in rows:
        if r["queued"] or not r["id"]:
            continue
        marks = dirty_marks()
        rec = db[BACKUPS].insert_one({**r, "run_id": run_id, EDIT_REV: marks[EDIT_REV], "at": now})
        wrote = update_ecom(db, r["id"], lambda e: None if e.get("locally_modified") else {**e, **marks})
        if wrote is None:
            db[BACKUPS].delete_one({"_id": rec.inserted_id})
        else:
            done += 1
    return done


def revert(db, run_id: str) -> int:
    """Un-queue a run's rows whose edit_rev is still the run's. Returns how many."""
    from api.services.catalog_ecom import EDIT_REV, update_ecom

    done = 0
    for b in list(db[BACKUPS].find({"run_id": run_id})):
        rev = b.get(EDIT_REV)
        wrote = update_ecom(
            db,
            b["id"],
            lambda e: {**e, "locally_modified": False}
            if rev and e.get("locally_modified") and e.get(EDIT_REV) == rev
            else None,
        )
        done += wrote is not None
    return done


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--apply", action="store_true", help="write (default: dry run)")
    ap.add_argument("--revert", action="store_true", help="undo a run (needs --run-id)")
    ap.add_argument("--run-id", help="the run id --apply printed")
    args = ap.parse_args()
    if args.revert and not args.run_id:
        print("[ERROR] --revert needs --run-id")
        return 2
    url = os.getenv("MONGO_PUBLIC_URL") or os.getenv("MONGO_URL") or os.getenv("MONGODB_URL")
    print(f"[MONGO] connection string: {'SET' if url else 'NOT SET'}")
    if not url:
        return 1
    from pymongo import MongoClient

    db = MongoClient(url, serverSelectionTimeoutMS=20_000)[os.getenv("MONGO_DATABASE", "ims_2_0")]
    if args.revert:
        print(f"[REVERTED] run {args.run_id}: {revert(db, args.run_id)} listing(s) un-queued")
        return 0
    rows = live_listings(db)
    for r in rows:
        print(f"  {r['sku']}: {'already queued' if r['queued'] else 'queue'}")
    todo = sum(1 for r in rows if not r["queued"])
    print(f"live listings {len(rows)}, to queue {todo}, already queued {len(rows) - todo}")
    if not args.apply:
        print("[DRY RUN] nothing written. Re-run with --apply to queue.")
        return 0
    run_id = uuid.uuid4().hex[:12]
    print(f"[APPLY] run id {run_id}")
    print(f"[APPLIED] run {run_id}: {apply(db, rows, run_id)} listing(s) queued")
    return 0


if __name__ == "__main__":
    sys.exit(main())
