"""Read-only: list every BLOCKED SMART collection whose rules IMS cannot read
the way the website does (an IN list, shape, gender ...). Such a block stalls
the pushes of every product it may be about and must be rewritten before
the menu PR's online-block change goes live. Writes nothing.

    railway run --service MongoDB -- ".venv\Scripts\python.exe" backend/scripts/check_blocked_collection_rules.py
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)
for p in (BACKEND, os.path.join(os.path.dirname(BACKEND), "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)


def run(db) -> int:
    from api.services.online_block import unverifiable_blocks

    rows = unverifiable_blocks(db)
    for r in rows:
        print(f"BLOCKED + NOT READABLE: {r['handle'] or r['collection_id']}: {'; '.join(r['rules'])}")
    print(f"{len(rows)} blocked collection(s) with rules IMS cannot read")
    return len(rows)


if __name__ == "__main__":
    from backfill_reorder_quantity_minus1 import resolve_mongo_uri  # one resolver
    from pymongo import MongoClient

    uri = resolve_mongo_uri(None)
    if not uri:
        raise SystemExit("No Mongo connection: use `railway run`.")
    sys.exit(1 if run(MongoClient(uri, serverSelectionTimeoutMS=10000)[os.getenv("MONGO_DATABASE", "ims_2_0")]) else 0)
