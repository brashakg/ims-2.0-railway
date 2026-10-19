#!/usr/bin/env python3
"""
IMS 2.0 - Give every website collection its IMS id (publish prep, 2026-10-19)
=============================================================================
Runbook-only script. NOT in CI. ASCII only (Windows cp1252). ONE-TIME.

WHY: 94 of the 98 collections imported from the old app carry no
`collection_id`, the key every IMS door uses (open, edit, push one, and the
push's write-back that takes a collection off the queue). The push now falls
back to the Shopify id so a clean push leaves the queue anyway; this gives
them the stable id the screens need.

WHAT: `ecom_collections` rows whose collection_id is missing / null / blank
get collection_id = their old `id` when they have one, else a new uuid4.
Nothing else is touched. A re-run changes nothing.

USAGE
    railway run --service MongoDB -- ".venv\\Scripts\\python.exe" scripts/backfill_collection_ids.py
    ... scripts/backfill_collection_ids.py --commit
"""
from __future__ import annotations

import argparse
import os
import sys
import uuid

MISSING = {"$or": [{"collection_id": {"$exists": False}}, {"collection_id": None}, {"collection_id": ""}]}


def run(db, commit: bool) -> dict:
    coll = db["ecom_collections"]
    rows = list(coll.find(MISSING, {"_id": 1, "id": 1, "title": 1, "handle": 1}))
    taken = {d.get("collection_id") for d in coll.find({}, {"collection_id": 1})}
    written = 0
    for d in rows:
        new_id = str(d.get("id") or "") or str(uuid.uuid4())
        if new_id in taken:
            new_id = str(uuid.uuid4())
        taken.add(new_id)
        print(f"  {str(d.get('handle') or d.get('title'))[:40]:40} -> {new_id}")
        if commit:
            written += coll.update_one({"_id": d["_id"], **MISSING}, {"$set": {"collection_id": new_id}}).modified_count
    return {"missing": len(rows), "written": written}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Backfill ecom_collections.collection_id. Dry-run by default.")
    parser.add_argument("--mongo-uri", default=None)
    parser.add_argument("--db", default=os.getenv("MONGO_DATABASE", "ims_2_0"))
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args(argv)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from backfill_reorder_quantity_minus1 import resolve_mongo_uri  # one resolver
    from pymongo import MongoClient

    uri = resolve_mongo_uri(args.mongo_uri)
    if not uri:
        raise SystemExit("No Mongo connection: pass --mongo-uri or use `railway run`.")
    db = MongoClient(uri, serverSelectionTimeoutMS=10000)[args.db]
    mode = "COMMIT" if args.commit else "DRY-RUN"
    print(f"[{mode}] {run(db, args.commit)}")
    if not args.commit:
        print("[DRY-RUN] nothing written; re-run with --commit.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
