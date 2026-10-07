#!/usr/bin/env python3
"""
IMS 2.0 - Backfill switched_on_at / deleted_at on provisional products (F48)
===========================================================================
Runbook-only script. NOT in CI. ASCII only (Windows cp1252). ONE-TIME.

WHY
---
reorder_policy.discontinued() reads a PROVISIONAL product (ruling 13: bought
on a PO before it was catalogued) as 'still new' -- not discontinued -- while
it has no switched_on_at and no deleted_at. Both stamps are written only by
code from the stock-bugs PR on: ProductRepository.update stamps switched_on_at
on a write of is_active True, and the catalog DELETE stamps deleted_at on the
spine (the old DELETE wrote only is_active False there). A provisional product
switched on before that deploy and switched off after it, or deleted before
it, therefore reads 'new' and gets REORDER_ALERT / FAST_MOVING advice it never
got before. This script writes the two stamps those products would carry.

WHAT IT TOUCHES
---------------
Writes ONE collection, `products` (the spine). Reads `catalog_products` (the
twin) and `orders` (sale lines). Each handle's name is checked before any read;
SystemExit otherwise. Only two classes of row are written:

  1. provisional True, no switched_on_at, AND (is_active True now OR at least
     one sale line, orders.items.product_id == product_id):
       switched_on_at = the earliest sale's created_at (UTC), else now (UTC).
  2. provisional True, is_active False, no deleted_at, AND its catalog twin
     (catalog_products.id == the spine's pim_product_id or product_id, the
     twin key the spine->twin mirror uses) is deleted -- has deleted_at, or a
     status / ecom.status of DELETED or ARCHIVED:
       deleted_at = the twin's deleted_at, copied as stored (now, UTC ISO, when
       the twin is deleted by status only).

Every write re-asserts its class filter, so a re-run changes nothing.

USAGE
-----
Dry-run (DEFAULT - prints counts and ids, writes nothing):
    railway run --service MongoDB -- ".venv\\Scripts\\python.exe" scripts/backfill_product_switched_on.py

Act (only after the owner approves the dry-run counts):
    railway run --service MongoDB -- ".venv\\Scripts\\python.exe" scripts/backfill_product_switched_on.py --commit

Connection resolution: --mongo-uri, else MONGO_PUBLIC_URL, else MONGODB_URI,
else MONGODB_URL/MONGO_URL (the vars `railway run` injects). No secret value
is ever printed.
"""

import argparse
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

PRODUCTS = "products"
CATALOG = "catalog_products"
ORDERS = "orders"

# Class 1 / class 2 filters. `None` matches a missing or null field.
NOT_SWITCHED_ON = {"provisional": True, "switched_on_at": None}
OFF_NOT_DELETED = {"provisional": True, "is_active": False, "deleted_at": None}
_DELETED_STATUS = {"$regex": "^(deleted|archived)$", "$options": "i"}
TWIN_DELETED = {
    "$or": [
        {"deleted_at": {"$nin": [None, ""]}},
        {"status": _DELETED_STATUS},
        {"ecom.status": _DELETED_STATUS},
    ]
}


def resolve_mongo_uri(explicit: Optional[str]) -> Optional[str]:
    return (
        explicit
        or os.getenv("MONGO_PUBLIC_URL")
        or os.getenv("MONGODB_URI")
        or os.getenv("MONGODB_URL")
        or os.getenv("MONGO_URL")
    )


def _check(coll, expected: str) -> None:
    name = getattr(coll, "name", None)
    if name != expected:
        raise SystemExit(f"refusing: pointed at {name!r} where {expected!r} is required")


def _utc(value: Any) -> Optional[datetime]:
    """A sale's created_at as an aware UTC datetime (a naive value is UTC, as
    pymongo hands it back); None when it is not a date."""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _first_sales(orders, pids: List[str]) -> Dict[str, Optional[datetime]]:
    """{product_id: earliest sale (or None when no line carries a date)} for
    every pid with at least one sale line."""
    first: Dict[str, Optional[datetime]] = {}
    if not pids:
        return first
    wanted = set(pids)
    for order in orders.find({"items.product_id": {"$in": pids}}, {"_id": 0, "items": 1, "created_at": 1}):
        at = _utc(order.get("created_at"))
        for line in order.get("items") or []:
            pid = line.get("product_id") if isinstance(line, dict) else None
            if pid not in wanted:
                continue
            seen = first.get(pid)
            first[pid] = at if seen is None else (min(seen, at) if at else seen)
    return first


def backfill(products, catalog, orders, *, commit: bool, now: Optional[datetime] = None) -> Dict[str, List[str]]:
    """Print, and with ``commit`` write, the two stamps. Returns
    {"switched_on": [ids], "deleted": [ids]} -- the rows each class matched.
    Explicit checks, not `assert`: `python -O` strips asserts."""
    _check(products, PRODUCTS)
    _check(catalog, CATALOG)
    _check(orders, ORDERS)
    now = now or datetime.now(timezone.utc)
    fields = {"_id": 0, "product_id": 1, "pim_product_id": 1, "is_active": 1, "sku": 1}

    # Class 1 - switched on at some point: active now, or sold.
    cands = [r for r in products.find(NOT_SWITCHED_ON, fields) if r.get("product_id")]
    sales = _first_sales(orders, [r["product_id"] for r in cands])
    stamp_on = {
        r["product_id"]: sales.get(r["product_id"]) or now
        for r in cands
        if r.get("is_active") is True or r["product_id"] in sales
    }

    # Class 2 - deleted on the catalog twin, never on the spine.
    stamp_del: Dict[str, Any] = {}
    for r in products.find(OFF_NOT_DELETED, fields):
        pid = r.get("product_id")
        keys = [k for k in (r.get("pim_product_id"), pid) if k]
        if not pid or not keys:
            continue
        twin = catalog.find_one({"id": {"$in": keys}, **TWIN_DELETED}, {"_id": 0, "id": 1, "deleted_at": 1})
        if twin is not None:
            stamp_del[pid] = twin.get("deleted_at") or now.isoformat()

    print(f"{len(stamp_on)} provisional product(s) switched on before (active now, or sold) - switched_on_at:")
    for pid, at in stamp_on.items():
        print(f"  {pid}  {at.isoformat()}  {'sold' if pid in sales else 'active, never sold'}")
    print(f"{len(stamp_del)} provisional product(s) deleted on the catalog only - deleted_at:")
    for pid, at in stamp_del.items():
        print(f"  {pid}  {at}")
    out = {"switched_on": list(stamp_on), "deleted": list(stamp_del)}
    if not commit:
        print("DRY RUN - nothing written. Re-run with --commit to write them.")
        return out

    written = 0
    for pid, at in stamp_on.items():
        res = products.update_one({"product_id": pid, **NOT_SWITCHED_ON}, {"$set": {"switched_on_at": at}})
        written += int(getattr(res, "modified_count", 0) or 0)
    for pid, at in stamp_del.items():
        res = products.update_one({"product_id": pid, **OFF_NOT_DELETED}, {"$set": {"deleted_at": at}})
        written += int(getattr(res, "modified_count", 0) or 0)
    print(f"WROTE {written} of {len(stamp_on) + len(stamp_del)}.")
    return out


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Backfill switched_on_at / deleted_at on provisional products. Dry-run by default."
    )
    parser.add_argument("--mongo-uri", default=None)
    parser.add_argument("--db", default=os.getenv("MONGO_DATABASE", "ims_2_0"))
    parser.add_argument("--commit", action="store_true", help="Write the stamps (default: dry-run).")
    args = parser.parse_args(argv)
    uri = resolve_mongo_uri(args.mongo_uri)
    if not uri:
        print("No Mongo connection. Set MONGO_PUBLIC_URL / MONGODB_URI, pass --mongo-uri, "
              "or run via `railway run` so the vars are injected.")
        return 2
    from pymongo import MongoClient

    db = MongoClient(uri, serverSelectionTimeoutMS=10000)[args.db]
    backfill(db[PRODUCTS], db[CATALOG], db[ORDERS], commit=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
