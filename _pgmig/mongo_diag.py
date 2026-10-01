"""One-shot diagnostic queries for the 2026-05-28 owner ask:

1. TASKMASTER post-cutover health  — was stock.below_reorder firing or dead?
2. db.stock orphan-collection cleanup — count, then decide drop/merge.

Run via `railway run` so the MONGO_* env vars get injected.
ASCII-only output (Windows cp1252).
"""
from __future__ import annotations
import os
import sys
from datetime import datetime, timezone, timedelta

from pymongo import MongoClient


def _client() -> MongoClient:
    host = os.environ["MONGO_HOST"]
    port = int(os.environ.get("MONGO_PORT", "27017"))
    user = os.environ["MONGO_USERNAME"]
    pw = os.environ["MONGO_PASSWORD"]
    auth_db = os.environ.get("MONGO_AUTH_SOURCE", "admin")
    return MongoClient(
        host=host, port=port, username=user, password=pw,
        authSource=auth_db, serverSelectionTimeoutMS=10000,
    )


def main() -> None:
    db_name = os.environ.get("MONGO_DATABASE", "ims_2_0")
    print(f"[diag] db={db_name}")
    cli = _client()
    db = cli[db_name]

    # 1) TASKMASTER -----------------------------------------------------------
    print("\n=== 1. TASKMASTER stock.below_reorder events ===")
    cutover = datetime(2026, 2, 27, tzinfo=timezone.utc)
    n_total = db.agent_events.count_documents({"event_type": "stock.below_reorder"})
    n_since = db.agent_events.count_documents({
        "event_type": "stock.below_reorder",
        "created_at": {"$gte": cutover},
    })
    print(f"  ALL TIME       : {n_total}")
    print(f"  since 2026-02-27 (~cutover): {n_since}")

    # If any fired since cutover, sample 5 of them so we know what they hit
    if n_since > 0:
        print("  sample (latest 5):")
        for doc in db.agent_events.find(
            {"event_type": "stock.below_reorder", "created_at": {"$gte": cutover}},
            {"_id": 0, "product_id": 1, "store_id": 1, "on_hand": 1, "created_at": 1},
        ).sort("created_at", -1).limit(5):
            print(f"    {doc}")

    # Bonus: were any auto-drafted POs created since cutover by TASKMASTER?
    n_po = db.purchase_orders.count_documents({
        "auto_drafted_by": "TASKMASTER",
        "created_at": {"$gte": cutover},
    })
    print(f"  TASKMASTER auto-drafted POs since cutover: {n_po}")
    if n_po > 0:
        print("  sample drafts (latest 5):")
        for doc in db.purchase_orders.find(
            {"auto_drafted_by": "TASKMASTER", "created_at": {"$gte": cutover}},
            {"_id": 0, "po_id": 1, "vendor_id": 1, "status": 1, "total": 1, "created_at": 1},
        ).sort("created_at", -1).limit(5):
            print(f"    {doc}")

    # 2) db.stock orphan ------------------------------------------------------
    print("\n=== 2. db.stock orphan collection ===")
    coll_list = db.list_collection_names()
    if "stock" not in coll_list:
        print("  stock collection: NOT PRESENT (already cleaned)")
    else:
        n_stock = db.stock.count_documents({})
        n_stock_units = db.stock_units.count_documents({})
        print(f"  db.stock         : {n_stock} docs")
        print(f"  db.stock_units   : {n_stock_units} docs (canonical)")
        if n_stock == 0:
            print("  -> SAFE TO DROP db.stock (empty)")
        else:
            print("  -> orphan rows present; need to MERGE into stock_units before drop")
            # Show a couple to characterise
            print("  sample (first 3):")
            for doc in db.stock.find({}, {"_id": 1, "product_id": 1, "store_id": 1, "quantity": 1}).limit(3):
                print(f"    {doc}")

    # 3) Bonus: count uncategorized products (drives the GST default fix)
    print("\n=== 3. Uncategorized products (drives PR #138 GST fix) ===")
    n_blank_cat = db.products.count_documents({
        "$or": [
            {"category": {"$exists": False}},
            {"category": None},
            {"category": ""},
        ]
    })
    n_total_products = db.products.count_documents({})
    print(f"  uncategorized: {n_blank_cat} of {n_total_products} total products")
    if n_blank_cat > 0:
        print("  sample (5):")
        for doc in db.products.find(
            {"$or": [{"category": {"$exists": False}}, {"category": None}, {"category": ""}]},
            {"_id": 0, "product_id": 1, "sku": 1, "brand": 1, "model": 1, "gst_rate": 1},
        ).limit(5):
            print(f"    {doc}")


if __name__ == "__main__":
    try:
        main()
    except KeyError as e:
        print(f"[diag] MISSING ENV {e} - run via 'railway run'", file=sys.stderr)
        sys.exit(2)
    except Exception as exc:
        print(f"[diag] FAILED: {exc.__class__.__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
