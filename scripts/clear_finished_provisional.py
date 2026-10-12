#!/usr/bin/env python3
"""
IMS 2.0 - Clear the ordered-draft mark on products already finished (PR #1171)
==============================================================================
Runbook-only script. NOT in CI. ASCII only (Windows cp1252). ONE-TIME.

WHY
---
An item typed on a purchase order through 'Not in the catalogue?' is born
`provisional` (an ordered draft). Before PR #1171 the save that finished it
never cleared the mark, so a product finished then still carries
provisional:true with catalog_status ACTIVE. Since #1171 every screen reads
product_master.is_ordered_draft (marked AND not finished), so these rows
already read as the finished products they are; the mark is only stale data,
cleared lazily by the product's next editor save. This script clears it now,
with the SAME fields that save writes (product_master.restamp_on_update:
provisional False, bought_before_catalogued True -- reorder_policy still reads
the row as a PO-door buy, never 'Discontinued').

RUN ORDER: scripts/backfill_product_switched_on.py selects provisional rows.
If it has not been run with --commit yet, run it FIRST.

WHAT IT TOUCHES
---------------
Writes ONE collection, `products` (the spine), and only rows that are
provisional:true AND finished (is_ordered_draft False). A real ordered draft
(catalog_status DRAFT) is never touched. Every write re-asserts
provisional:true, so a re-run changes nothing.

USAGE
-----
Dry-run (DEFAULT - lists the rows, writes nothing):
    railway run --service MongoDB -- ".venv\\Scripts\\python.exe" scripts/clear_finished_provisional.py

Act (only after the owner approves the dry-run list):
    railway run --service MongoDB -- ".venv\\Scripts\\python.exe" scripts/clear_finished_provisional.py --commit

Connection resolution: --mongo-uri, else MONGO_PUBLIC_URL, else MONGODB_URI,
else MONGODB_URL/MONGO_URL (the vars `railway run` injects). No secret value
is ever printed.
"""

import argparse
import os
import sys
from typing import List, Optional

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
)
# The backend modules refuse to import without these; this script signs no
# token and serves no request.
os.environ.setdefault("JWT_SECRET_KEY", "clear-finished-provisional")
os.environ.setdefault("ENVIRONMENT", "script")

from api.services.product_master import is_ordered_draft, restamp_on_update  # noqa: E402

PRODUCTS = "products"


def resolve_mongo_uri(explicit: Optional[str]) -> Optional[str]:
    return (
        explicit
        or os.getenv("MONGO_PUBLIC_URL")
        or os.getenv("MONGODB_URI")
        or os.getenv("MONGODB_URL")
        or os.getenv("MONGO_URL")
    )


def clear(products, *, commit: bool) -> List[str]:
    """Print, and with ``commit`` write, the finishing save's fields on every
    finished product still marked provisional. Returns the matched ids."""
    name = getattr(products, "name", None)
    if name != PRODUCTS:
        raise SystemExit(f"refusing: pointed at {name!r} where {PRODUCTS!r} is required")
    found = []
    for row in products.find({"provisional": True}):
        if is_ordered_draft(row):
            continue  # a real ordered draft: the cataloguer's, untouched
        if row.get("product_id"):
            found.append((row["product_id"], row.get("sku"), restamp_on_update(row, {})))
    print(f"{len(found)} finished product(s) still marked as an ordered draft:")
    for pid, sku, _ in found:
        print(f"  {pid}  {sku}")
    if not commit:
        print("DRY RUN - nothing written. Re-run with --commit to clear the mark.")
        return [pid for pid, _, _ in found]
    written = 0
    for pid, _, fields in found:
        res = products.update_one({"product_id": pid, "provisional": True}, {"$set": fields})
        written += int(getattr(res, "modified_count", 0) or 0)
    print(f"WROTE {written} of {len(found)}.")
    return [pid for pid, _, _ in found]


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Clear provisional on finished products. Dry-run by default."
    )
    parser.add_argument("--mongo-uri", default=None)
    parser.add_argument("--db", default=os.getenv("MONGO_DATABASE", "ims_2_0"))
    parser.add_argument("--commit", action="store_true", help="Write (default: dry-run).")
    args = parser.parse_args(argv)
    uri = resolve_mongo_uri(args.mongo_uri)
    if not uri:
        print("No Mongo connection. Set MONGO_PUBLIC_URL / MONGODB_URI, pass --mongo-uri, "
              "or run via `railway run` so the vars are injected.")
        return 2
    from pymongo import MongoClient

    db = MongoClient(uri, serverSelectionTimeoutMS=10000)[args.db]
    clear(db[PRODUCTS], commit=args.commit)
    return 0


if __name__ == "__main__":
    sys.exit(main())
