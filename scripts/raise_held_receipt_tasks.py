#!/usr/bin/env python3
"""
IMS 2.0 - Raise the tasks for receipts held before audit C1 shipped (R1-34)
==========================================================================
Runbook-only script. NOT in CI. ASCII only (Windows cp1252).

WHY
---
Since PR #1171 a receipt that holds lines gives each catalogue manager of the
shop's legal entity a task by name (lines waiting to be catalogued), and the
shop's store manager one per item held beyond the order. A receipt that was
already held BEFORE that deploy raised nothing: its tasks are raised only when
someone presses "Add to stock" on it again. This script raises them now,
through the SAME door the accept runs (grn_accept._sync_catalogue_tasks), so
nothing here is a second copy of who is told or what the task says.

WHAT IT TOUCHES
---------------
Reads `grns` (status PARTIALLY_ACCEPTED with unresolved_lines), `products`,
`stores`, `users`. Writes ONLY `tasks`, and only through the accept's own
door, which raises a task once per receipt and person, ever: a task a person
has already closed is never raised again, so a second run raises nothing.

USAGE
-----
Dry-run (DEFAULT - lists the held receipts no task names yet, writes nothing):
    railway run --service MongoDB -- ".venv\\Scripts\\python.exe" scripts/raise_held_receipt_tasks.py

Act:
    railway run --service MongoDB -- ".venv\\Scripts\\python.exe" scripts/raise_held_receipt_tasks.py --commit

Connection resolution: --mongo-uri, else MONGO_PUBLIC_URL, else MONGODB_URI,
else MONGODB_URL/MONGO_URL (the vars `railway run` injects). No secret value
is ever printed.
"""

import argparse
import os
import sys
from typing import Any, Dict, List, Optional

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
)
# The backend modules refuse to import without these; this script signs no
# token and serves no request.
os.environ.setdefault("JWT_SECRET_KEY", "raise-held-receipt-tasks")
os.environ.setdefault("ENVIRONMENT", "script")


def resolve_mongo_uri(explicit: Optional[str]) -> Optional[str]:
    return (
        explicit
        or os.getenv("MONGO_PUBLIC_URL")
        or os.getenv("MONGODB_URI")
        or os.getenv("MONGODB_URL")
        or os.getenv("MONGO_URL")
    )


class _Conn:
    """The backend's DatabaseConnection shape over one pymongo database, so
    the accept's door (task repository, product repository, _get_db) writes
    where this script points it and nowhere else."""

    is_connected = True

    def __init__(self, db):
        self.db = db

    def get_collection(self, name):
        return self.db[name]

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return self.db[name]


def _point_backend_at(db) -> None:
    import database.connection as dbc
    from api import dependencies as deps
    from api.routers.vendors import grn_accept

    conn = _Conn(db)
    dbc.get_db = lambda: conn
    deps.get_db = lambda: conn
    grn_accept._get_db = lambda: db


def untold_held_receipts(db) -> List[Dict[str, Any]]:
    """Held receipts (PARTIALLY_ACCEPTED, lines in unresolved_lines) that no
    task names yet, oldest first."""
    out = []
    for g in db["grns"].find({"status": "PARTIALLY_ACCEPTED"}).sort("created_at", 1):
        if not g.get("unresolved_lines"):
            continue
        if db["tasks"].find_one({"grn_id": g.get("grn_id"), "source": "SYSTEM"}):
            continue
        out.append(g)
    return out


def run(db, *, commit: bool) -> List[Dict[str, Any]]:
    held = untold_held_receipts(db)
    print(f"{len(held)} held receipt(s) with no task yet:")
    for g in held:
        reasons = sorted({str(ln.get("reason")) for ln in g.get("unresolved_lines") or []})
        print(
            f"  {g.get('grn_number') or g.get('grn_id')}  shop {g.get('store_id')}  "
            f"{len(g.get('unresolved_lines') or [])} line(s) held ({', '.join(reasons)})"
        )
    if not commit:
        print("DRY RUN - nothing written. Re-run with --commit to raise the tasks.")
        return held
    _point_backend_at(db)
    from api.dependencies import get_product_repository
    from api.routers.vendors.grn_accept import _sync_catalogue_tasks

    product_repo = get_product_repository()
    for g in held:
        _sync_catalogue_tasks(
            g["grn_id"], g, list(g.get("unresolved_lines") or []), g["status"], product_repo
        )
    told = sum(1 for g in held if db["tasks"].find_one({"grn_id": g["grn_id"], "source": "SYSTEM"}))
    print(f"RAISED tasks for {told} of {len(held)} receipt(s).")
    return held


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Raise the tasks for receipts held before audit C1 shipped. Dry-run by default."
    )
    parser.add_argument("--mongo-uri", default=None)
    parser.add_argument("--db", default=os.getenv("MONGO_DATABASE", "ims_2_0"))
    parser.add_argument("--commit", action="store_true", help="Raise the tasks (default: dry-run).")
    args = parser.parse_args(argv)
    uri = resolve_mongo_uri(args.mongo_uri)
    if not uri:
        print("No Mongo connection. Set MONGO_PUBLIC_URL / MONGODB_URI, pass --mongo-uri, "
              "or run via `railway run` so the vars are injected.")
        return 2
    from pymongo import MongoClient

    client = MongoClient(uri, serverSelectionTimeoutMS=10000)
    run(client[args.db], commit=args.commit)
    return 0


if __name__ == "__main__":
    sys.exit(main())
