#!/usr/bin/env python3
"""
IMS 2.0 - Close the stuck POOLED stock-parity task (multi-location PR 4)
========================================================================
Runbook-only script. NOT in CI. ASCII only (Windows cp1252).

WHY
---
Before multi-location PR 4 the nightly stock parity compared ONE pooled number
and filed ONE deduped SYSTEM task with source_ref exactly
"shopify-stock-parity-drift". One of those (July) sits ESCALATED and, because
it dedupes on that ref, it would have blocked every later pooled task. PR 4
compares per location and files one task per shop under
"shopify-stock-parity-drift:<store_id>"; nothing files the bare ref any more,
so the stuck one would stay open forever. The owner said yes to closing it
when PR 4 merges.

WHAT IT TOUCHES
---------------
ONE collection, `tasks` (asserted before any read). ONLY rows whose
source_ref EQUALS "shopify-stock-parity-drift" (an exact match: the per-shop
":<store_id>" refs are never touched) and whose status is still active
(OPEN / IN_PROGRESS / ESCALATED). Closing = what TaskRepository.complete_task
writes (status COMPLETED, completed_at, completion_notes) plus a history row.

USAGE
-----
Dry-run (DEFAULT - prints what it would close, writes nothing):
    railway run --service MongoDB -- ".venv\\Scripts\\python.exe" scripts/close_pooled_parity_task.py

Act:
    railway run --service MongoDB -- ".venv\\Scripts\\python.exe" scripts/close_pooled_parity_task.py --commit

Connection resolution: --mongo-uri, else MONGO_PUBLIC_URL, else MONGODB_URI,
else MONGODB_URL/MONGO_URL (the vars `railway run` injects). No secret value
is ever printed.
"""

import argparse
import os
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

POOLED_REF = "shopify-stock-parity-drift"
COLLECTION = "tasks"
ACTIVE = ["OPEN", "IN_PROGRESS", "ESCALATED"]
NOTES = (
    "Closed by multi-location PR 4: the stock parity now compares each shop "
    "with its own Shopify location and files one task per shop "
    "(shopify-stock-parity-drift:<store_id>); this pooled task is retired."
)


def resolve_mongo_uri(explicit: Optional[str]) -> Optional[str]:
    return (
        explicit
        or os.getenv("MONGO_PUBLIC_URL")
        or os.getenv("MONGODB_URI")
        or os.getenv("MONGODB_URL")
        or os.getenv("MONGO_URL")
    )


def close_pooled(coll, *, commit: bool, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Print, and with ``commit`` close, every ACTIVE task whose source_ref is
    exactly POOLED_REF. Returns the rows it matched. Refuses (AssertionError)
    any collection but `tasks`."""
    name = getattr(coll, "name", None)
    assert name == COLLECTION, f"refusing: pointed at {name!r}, only {COLLECTION!r} is allowed"
    flt = {"source_ref": POOLED_REF, "status": {"$in": ACTIVE}}
    rows = list(
        coll.find(flt, {"_id": 0, "task_id": 1, "title": 1, "status": 1, "created_at": 1, "source_ref": 1})
    )
    print(f"{len(rows)} active task(s) with source_ref == {POOLED_REF!r}:")
    for r in rows:
        print(f"  {r.get('task_id')}  {r.get('status'):12}  created {r.get('created_at')}  {r.get('title')}")
    if not commit:
        print("DRY RUN - nothing written. Re-run with --commit to close them.")
        return rows
    now = now or datetime.now()
    closed = 0
    for r in rows:
        assert r.get("source_ref") == POOLED_REF  # belt and braces: exact ref only
        res = coll.update_one(
            {"task_id": r["task_id"], "source_ref": POOLED_REF, "status": r["status"]},
            {
                "$set": {"status": "COMPLETED", "completed_at": now, "completion_notes": NOTES, "updated_at": now},
                "$push": {"history": {"status": "COMPLETED", "timestamp": now, "by": "system", "notes": NOTES}},
            },
        )
        closed += int(getattr(res, "modified_count", 0) or 0)
    print(f"CLOSED {closed} of {len(rows)}.")
    return rows


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Close the stuck pooled stock-parity task. Dry-run by default.")
    parser.add_argument("--mongo-uri", default=None)
    parser.add_argument("--db", default=os.getenv("MONGO_DATABASE", "ims_2_0"))
    parser.add_argument("--commit", action="store_true", help="Close the tasks (default: dry-run).")
    args = parser.parse_args(argv)
    uri = resolve_mongo_uri(args.mongo_uri)
    if not uri:
        print("No Mongo connection. Set MONGO_PUBLIC_URL / MONGODB_URI, pass --mongo-uri, "
              "or run via `railway run` so the vars are injected.")
        return 2
    from pymongo import MongoClient

    client = MongoClient(uri, serverSelectionTimeoutMS=10000)
    close_pooled(client[args.db][COLLECTION], commit=args.commit)
    return 0


if __name__ == "__main__":
    sys.exit(main())
