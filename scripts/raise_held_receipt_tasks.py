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

Before this deploy a catalogue save released nothing, so a held item may
already be finished. Its units go on the shelf first, through the ONE release
(grn_accept.release_held_receipts) -- never a task to finish a finished item.

A vendor bill's ask for cataloguing raised before this deploy was addressed to
nobody (category 'Catalog'), so no catalogue manager's list shows it and
nothing ever closes it. Each one still waiting is asked again through the
bill's own door (purchase_invoices.ask_catalogue_managers), and the old row is
closed.

WHAT IT TOUCHES
---------------
Reads `grns` (status PARTIALLY_ACCEPTED with unresolved_lines), `products`,
`stores`, `users`, `tasks`. Writes `tasks` through the accept's and the bill's
own doors (a receipt's task once per receipt and person, ever: a second run
raises nothing), closes the old asks, and -- for a held item already finished
-- runs the release, which puts its units on the shelf as "Add to stock" does.

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


def finished_held_items(db, g) -> List[str]:
    """The items `g` holds for the catalogue that are catalogue-complete now."""
    from api.services import product_master as pm

    out = []
    for ln in g.get("unresolved_lines") or []:
        pid = ln.get("product_id")
        if ln.get("reason") != "incomplete_catalog" or pid in out:
            continue
        prod = db["products"].find_one({"product_id": pid})
        if prod is not None and not pm.compute_catalog_status(prod)[1]:
            out.append(pid)
    return out


def unaddressed_bill_asks(db) -> List[Dict[str, Any]]:
    """Open asks for cataloguing raised before the asks went to people by
    name: category 'Catalog', nobody assigned."""
    return list(
        db["tasks"].find(
            {
                "category": "Catalog",
                "source": "SYSTEM",
                "source_ref": {"$regex": "^catalogue-for-bill:"},
                "assigned_to": None,
                "status": {"$in": ["OPEN", "IN_PROGRESS", "ESCALATED"]},
            }
        )
    )


def run(db, *, commit: bool) -> List[Dict[str, Any]]:
    from api.routers.vendors.grn_accept import asks_still_waiting

    held = untold_held_receipts(db)
    print(f"{len(held)} held receipt(s) with no task yet:")
    for g in held:
        reasons = sorted({str(ln.get("reason")) for ln in g.get("unresolved_lines") or []})
        done = finished_held_items(db, g)
        print(
            f"  {g.get('grn_number') or g.get('grn_id')}  shop {g.get('store_id')}  "
            f"{len(g.get('unresolved_lines') or [])} line(s) held ({', '.join(reasons)})"
            + (f"; {len(done)} item(s) already finished: released first" if done else "")
        )
    asks = unaddressed_bill_asks(db)
    print(f"{len(asks)} vendor-bill ask(s) for cataloguing addressed to nobody:")
    for t in asks:
        ids = str(t.get("source_ref")).split(":", 1)[1].split(",")
        waiting = asks_still_waiting(db, ids)
        print(
            f"  {t.get('task_id')}  shop {t.get('store_id')}  {len(ids)} item(s), "
            + (f"{len(waiting)} still waiting: asked again by name" if waiting else "none waiting: closed")
        )
    if not commit:
        print("DRY RUN - nothing written. Re-run with --commit to act.")
        return held
    _point_backend_at(db)
    from api.dependencies import get_product_repository
    from api.routers.purchase_invoices import _ask_items, ask_catalogue_managers
    from api.routers.vendors.grn_accept import (
        _sync_catalogue_tasks,
        complete_tasks,
        release_held_receipts,
    )

    product_repo = get_product_repository()
    for g in held:
        # The release raises the receipt's tasks for whatever it still holds.
        for pid in finished_held_items(db, g):
            release_held_receipts(pid)
        g = db["grns"].find_one({"grn_id": g["grn_id"]}) or g
        if g.get("status") != "PARTIALLY_ACCEPTED" or db["tasks"].find_one(
            {"grn_id": g["grn_id"], "source": "SYSTEM"}
        ):
            continue
        _sync_catalogue_tasks(
            g["grn_id"], g, list(g.get("unresolved_lines") or []), g["status"], product_repo
        )
    told = sum(1 for g in held if db["tasks"].find_one({"grn_id": g["grn_id"], "source": "SYSTEM"}))
    print(f"RAISED tasks for {told} of {len(held)} receipt(s) (one released in full needs none).")
    for t in asks:
        waiting = asks_still_waiting(db, str(t.get("source_ref")).split(":", 1)[1].split(","))
        if waiting and not t.get("store_id"):
            print(f"  {t.get('task_id')}: no shop on the old ask - left open; ask again from the bill")
            continue
        if waiting and not ask_catalogue_managers(db, t.get("store_id"), _ask_items(db, waiting)):
            print(f"  {t.get('task_id')}: NOT asked again (no task stored) - left open")
            continue
        complete_tasks(
            db,
            {"task_id": t.get("task_id")},
            "Asked again of the catalogue managers by name." if waiting else "The items are catalogued.",
        )
    print(f"Handled {len(asks)} old vendor-bill ask(s).")
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
