#!/usr/bin/env python3
"""
IMS 2.0 - Reorder levels become PER SHOP (owner ruling D12, 2026-09-29)
=======================================================================
Runbook-only script. NOT in CI. ASCII only (Windows cp1252).

WHY
---
A product's reorder level used to be ONE chain-wide `reorder_point`. The owner
ruled levels are per shop (audit F73): products.reorder_levels = {store_id: n},
read by api/services/reorder_policy.py, and the old field is ignored. This
script carries a level the owner really TYPED over to every shop that stocks
the product, so no deliberate level is lost.

WHAT COUNTS
-----------
  - an old `reorder_point` of 1 or more, EXCEPT 5: 5 was the add form's
    default, and -1, 0 (the TechCherry import's stamp), garbage or no value
    are "not set" (owner 2026-09-28: not set = no low-stock alert);
  - "stocks the product" = any stock_units row of it at that shop, whatever
    its status, so a shop that has sold out keeps the level;
  - a shop that already has a level of its own (any value) is never touched,
    so a second run plans nothing.
The old `reorder_point` is left in place (nothing reads it), so the change can
be undone by unsetting `reorder_levels`.

USAGE
-----
Dry-run (DEFAULT - prints the plan, writes nothing):
    railway run --service MongoDB -- ".venv\\Scripts\\python.exe" scripts/migrate_reorder_levels_per_shop.py

Apply:
    railway run --service MongoDB -- ".venv\\Scripts\\python.exe" scripts/migrate_reorder_levels_per_shop.py --apply
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from typing import Dict, List

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
)
# The field and the shop-key pattern are the write model's own, not copies.
from api.services.reorder_policy import (  # noqa: E402
    LEVELS_FIELD,
    MAX_LEVEL,
    STORE_KEY_PATTERN,
    whole_number,
)

FORM_DEFAULT = 5
_SHOP_KEY = re.compile(STORE_KEY_PATTERN)


def _typed(value) -> int:
    """The old chain value if the owner typed it, else 0 (= not set). Read through
    the policy's own whole-number reader (bool, 2.9, '7', inf, NaN = garbage);
    never raises."""
    n = whole_number(value)
    if n is None or not 1 <= n <= MAX_LEVEL or n == FORM_DEFAULT:
        return 0
    return n


def plan(db) -> List[Dict]:
    """[{product_id, levels: {store_id: n}}] -- the levels apply() would add."""
    rows = []
    for p in db["products"].find(
        {"reorder_point": {"$exists": True}},
        {"_id": 0, "product_id": 1, "reorder_point": 1, LEVELS_FIELD: 1},
    ):
        level = _typed(p.get("reorder_point"))
        if not level or not p.get("product_id"):
            continue
        have = p.get(LEVELS_FIELD) if isinstance(p.get(LEVELS_FIELD), dict) else {}
        shops = db["stock_units"].distinct("store_id", {"product_id": p["product_id"]})
        levels = {
            str(s): level
            for s in shops
            if s and _SHOP_KEY.fullmatch(str(s)) and str(s) not in have
        }
        if levels:
            rows.append({"product_id": p["product_id"], "levels": levels})
    return rows


def apply(db, rows) -> Dict[str, int]:
    """Write each planned level, never over a level a shop already has."""
    written = 0
    for row in rows:
        # A doc whose reorder_levels is not an object (null, absent, a string, a
        # list) cannot take a dotted $set: set the whole dict instead, guarded
        # so a real dict is never replaced.
        whole = db["products"].update_one(
            {"product_id": row["product_id"], "$or": [
                {LEVELS_FIELD: {"$not": {"$type": "object"}}},
                {LEVELS_FIELD: {"$type": "array"}},  # $type matches any element
            ]},
            {"$set": {LEVELS_FIELD: dict(row["levels"])}},
        )
        if whole.modified_count:
            written += len(row["levels"])
            continue
        for shop, level in row["levels"].items():
            key = f"{LEVELS_FIELD}.{shop}"
            res = db["products"].update_one(
                {"product_id": row["product_id"], key: {"$exists": False}},
                {"$set": {key: level}},
            )
            written += res.modified_count
    return {"products": len(rows), "levels_written": written}


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Copy owner-typed chain reorder levels to every stocking shop. Dry-run by default."
    )
    parser.add_argument("--mongo-uri", default=None)
    parser.add_argument("--db", default=os.getenv("MONGO_DATABASE", "ims_2_0"))
    parser.add_argument("--apply", action="store_true", help="Write. Without it: dry-run.")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from backfill_reorder_quantity_minus1 import resolve_mongo_uri  # one resolver
    from pymongo import MongoClient

    uri = resolve_mongo_uri(args.mongo_uri)
    if not uri:
        raise SystemExit("No Mongo connection: pass --mongo-uri or use `railway run`.")
    db = MongoClient(uri, serverSelectionTimeoutMS=10000)[args.db]
    rows = plan(db)
    mode = "APPLY" if args.apply else "DRY-RUN"
    for row in rows:
        print(f"[{mode}] {row['product_id']}: {row['levels']}")
    print(f"[{mode}] {len(rows)} product(s), "
          f"{sum(len(r['levels']) for r in rows)} shop level(s) planned")
    if args.apply:
        print(f"[APPLY] {apply(db, rows)}")
    else:
        print("[DRY-RUN] nothing written; re-run with --apply.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
