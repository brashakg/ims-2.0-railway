#!/usr/bin/env python3
"""
IMS 2.0 - Backfill order.customer_id from order.customer_phone
==============================================================
Runbook-only script. NOT in CI. Idempotent: only touches orders that have a
customer_phone but no customer_id, and only when exactly one customer matches
that phone. Re-running is a clean no-op.

WHY
---
The TechCherry import wrote orders with `customer_phone` + `customer_name` but
no `customer_id`. So `find_by_customer(customer_id)` and customer order-history
returned nothing for the ~322 imported orders. The app already works via a
read-shim (get_customer_orders matches by phone too), but this stamps the
canonical `customer_id` on each order so the data is correct at rest.

USAGE
-----
Dry-run (DEFAULT - reports, writes nothing):
    railway run python scripts/backfill_order_customer_id.py

Apply (writes customer_id):
    railway run python scripts/backfill_order_customer_id.py --apply

Locally with an explicit URI:
    python scripts/backfill_order_customer_id.py --mongo-uri mongodb://... --apply

On Railway the MONGO_* component vars are injected by `railway run`; this
script builds the connection URI from them when MONGODB_URL/MONGO_URL is unset.
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Optional


def resolve_mongo_uri(explicit: Optional[str]) -> Optional[str]:
    """Prefer an explicit/standard URI; otherwise assemble one from the
    MONGO_* component vars Railway injects (no single MONGODB_URL there).

    MONGO_PUBLIC_URL is checked first so this runbook works locally via
    `railway run -s MongoDB ...` (the internal `mongodb.railway.internal` host
    is only resolvable inside Railway's network). Inside the container that var
    is absent, so the internal URL/components are used instead."""
    uri = (
        explicit
        or os.getenv("MONGO_PUBLIC_URL")
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
            "No Mongo connection. Set MONGODB_URL / MONGO_URL, pass --mongo-uri, "
            "or run via `railway run` so the MONGO_* component vars are injected."
        )
    try:
        from pymongo import MongoClient
    except ImportError:
        raise SystemExit("pymongo not installed; run `pip install pymongo`.")

    client = MongoClient(mongo_uri, serverSelectionTimeoutMS=10000)
    db = client[db_name]
    orders = db["orders"]
    customers = db["customers"]

    # Orders needing a backfill: a phone present, customer_id missing/blank.
    order_filter = {
        "$and": [
            {"customer_phone": {"$nin": [None, ""]}},
            {
                "$or": [
                    {"customer_id": {"$exists": False}},
                    {"customer_id": None},
                    {"customer_id": ""},
                ]
            },
        ]
    }

    scanned = 0
    matched_single = 0
    unmatched = 0
    ambiguous = 0
    updated = 0

    cursor = orders.find(
        order_filter, {"order_id": 1, "customer_phone": 1, "_id": 1}
    )
    for order in cursor:
        scanned += 1
        phone = (order.get("customer_phone") or "").strip()
        if not phone:
            unmatched += 1
            continue
        # Imported customers store the number under `phone`; native ones under
        # `mobile` - match either.
        matches = list(
            customers.find(
                {"$or": [{"phone": phone}, {"mobile": phone}]},
                {"customer_id": 1, "_id": 1},
            ).limit(2)
        )
        if not matches:
            unmatched += 1
            continue
        if len(matches) > 1:
            ambiguous += 1
            continue
        cust_id = matches[0].get("customer_id") or str(matches[0].get("_id"))
        if not cust_id:
            unmatched += 1
            continue
        matched_single += 1
        if apply:
            key = (
                {"order_id": order["order_id"]}
                if order.get("order_id")
                else {"_id": order["_id"]}
            )
            res = orders.update_one(key, {"$set": {"customer_id": cust_id}})
            updated += res.modified_count

    summary = {
        "scanned": scanned,
        "matched_single": matched_single,
        "ambiguous_multi_match": ambiguous,
        "unmatched": unmatched,
        "updated": updated,
        "applied": apply,
    }
    mode = "APPLY" if apply else "DRY-RUN"
    print(
        f"[{mode}] scanned={scanned} matchable={matched_single} "
        f"ambiguous={ambiguous} unmatched={unmatched} updated={updated}"
    )
    if not apply and matched_single:
        print(f"[DRY-RUN] re-run with --apply to stamp customer_id on {matched_single} order(s).")
    return summary


def main():
    parser = argparse.ArgumentParser(
        description="Backfill order.customer_id from customer_phone (idempotent)."
    )
    parser.add_argument(
        "--mongo-uri",
        default=None,
        help="Mongo URI; falls back to MONGODB_URL/MONGO_URL then MONGO_* components.",
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
