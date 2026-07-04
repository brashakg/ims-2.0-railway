#!/usr/bin/env python3
"""
IMS 2.0 - Backfill reorder_quantity = -1 on ALL products (owner decision)
=========================================================================
Runbook-only script. NOT in CI. ASCII only (Windows cp1252).

WHY
---
Owner decision (2026-07-04): `reorder_quantity` defaults to -1, meaning
"no auto-reorder" -- no reorder engine (inventory alerts, TASKMASTER draft
POs, ORACLE predictive proposals, forecast POs, Buy Desk, purchase
recommendations) may suggest or draft an order for a product until someone
explicitly sets a positive quantity. This script resets EVERY existing
product to that disabled default so legacy 10/20 defaults stop driving
suggestions. See backend/api/services/reorder_policy.py.

WHAT IT TOUCHES
---------------
  - `products` (the canonical spine):    top-level `reorder_quantity` = -1
    (the field the create door in product_master.normalise_payload stamps
    and PUT /products/{id} edits)
  - `catalog_products` (catalog shadow): `inventory.reorder_quantity` = -1

USAGE
-----
Dry-run (DEFAULT - prints counts, writes nothing):
    railway run --service MongoDB -- ".venv\\Scripts\\python.exe" scripts/backfill_reorder_quantity_minus1.py

Apply:
    railway run --service MongoDB -- ".venv\\Scripts\\python.exe" scripts/backfill_reorder_quantity_minus1.py --apply

Locally with an explicit URI:
    python scripts/backfill_reorder_quantity_minus1.py --mongo-uri mongodb://... --apply

Connection resolution: --mongo-uri, else MONGO_PUBLIC_URL, else MONGODB_URI,
else MONGODB_URL/MONGO_URL, else the MONGO_* component vars Railway injects.
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Optional


def resolve_mongo_uri(explicit: Optional[str]) -> Optional[str]:
    """Prefer an explicit/standard URI; otherwise assemble one from the
    MONGO_* component vars Railway injects. MONGO_PUBLIC_URL is checked first
    so this runbook works locally via `railway run -s MongoDB ...`."""
    uri = (
        explicit
        or os.getenv("MONGO_PUBLIC_URL")
        or os.getenv("MONGODB_URI")
        or os.getenv("MONGODB_URL")
        or os.getenv("MONGO_URL")
    )
    if uri:
        return uri
    host = os.getenv("MONGO_HOST")
    if not host:
        return None
    user = os.getenv("MONGO_USERNAME") or ""
    pw = os.getenv("MONGO_PASSWORD") or ""
    port = os.getenv("MONGO_PORT", "27017")
    auth_source = os.getenv("MONGO_AUTH_SOURCE", "admin")
    cred = f"{user}:{pw}@" if user and pw else ""
    opts = f"?authSource={auth_source}"
    if (os.getenv("MONGO_SSL", "") or "").lower() in ("true", "1", "yes"):
        opts += "&tls=true"
    return f"mongodb://{cred}{host}:{port}/{opts}"


def run(*, mongo_uri: Optional[str], db_name: str, apply: bool) -> dict:
    if not mongo_uri:
        raise SystemExit(
            "No Mongo connection. Set MONGO_PUBLIC_URL / MONGODB_URI, pass "
            "--mongo-uri, or run via `railway run` so the MONGO_* component "
            "vars are injected."
        )
    try:
        from pymongo import MongoClient
    except ImportError:
        raise SystemExit("pymongo not installed; run `pip install pymongo`.")

    client = MongoClient(mongo_uri, serverSelectionTimeoutMS=10000)
    db = client[db_name]

    # --- products (spine): top-level reorder_quantity -> -1 -----------------
    products = db["products"]
    prod_total = products.count_documents({})
    prod_needing = products.count_documents({"reorder_quantity": {"$ne": -1}})
    prod_updated = 0
    if apply and prod_needing:
        res = products.update_many(
            {"reorder_quantity": {"$ne": -1}},
            {"$set": {"reorder_quantity": -1}},
        )
        prod_updated = res.modified_count

    # --- catalog_products (shadow): inventory.reorder_quantity -> -1 --------
    catalog = db["catalog_products"]
    cat_total = catalog.count_documents({})
    cat_needing = catalog.count_documents(
        {"inventory.reorder_quantity": {"$ne": -1}}
    )
    cat_updated = 0
    if apply and cat_needing:
        res = catalog.update_many(
            {"inventory.reorder_quantity": {"$ne": -1}},
            {"$set": {"inventory.reorder_quantity": -1}},
        )
        cat_updated = res.modified_count

    mode = "APPLY" if apply else "DRY-RUN"
    print(f"[{mode}] products:          total={prod_total} "
          f"needing_backfill={prod_needing} updated={prod_updated}")
    print(f"[{mode}] catalog_products:  total={cat_total} "
          f"needing_backfill={cat_needing} updated={cat_updated}")
    if not apply and (prod_needing or cat_needing):
        print("[DRY-RUN] re-run with --apply to set reorder_quantity=-1 "
              f"on {prod_needing} product(s) + {cat_needing} catalog doc(s).")
    return {
        "products_total": prod_total,
        "products_needing": prod_needing,
        "products_updated": prod_updated,
        "catalog_total": cat_total,
        "catalog_needing": cat_needing,
        "catalog_updated": cat_updated,
        "applied": apply,
    }


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Set reorder_quantity=-1 (auto-reorder disabled) on ALL products "
            "+ catalog_products. Idempotent; dry-run by default."
        )
    )
    parser.add_argument(
        "--mongo-uri",
        default=None,
        help="Mongo URI; falls back to MONGO_PUBLIC_URL / MONGODB_URI / "
             "MONGODB_URL / MONGO_URL then MONGO_* components.",
    )
    parser.add_argument("--db", default=os.getenv("MONGO_DATABASE", "ims_2_0"))
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write changes. Without this flag the script is a dry-run.",
    )
    args = parser.parse_args()
    run(
        mongo_uri=resolve_mongo_uri(args.mongo_uri),
        db_name=args.db,
        apply=args.apply,
    )


if __name__ == "__main__":
    sys.exit(main())
