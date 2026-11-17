"""READ-ONLY: counter returns saved with no product on a returned line
(Codex audit 2026-11-04, L2-RETURN-F01).

Until the fix, the Returns screen sent each line with the bill line's id and
no product, and the server kept it that way: the credit or refund was made,
but a GOOD piece was never put back on sale (it stayed SOLD) and a DAMAGED
piece was never set aside, while the return said its stock was dealt with.
New returns take the product from the bill line.

This lists every completed counter return holding such a line whose bill line
is a STOCK piece (its sale took a stock unit; a lens, a service or a custom
till item never did), so each piece can be checked against stock. Nothing is
written and nothing is corrected: any correction of stored returns or stock
needs the owner's separate approval.

  railway run --service <backend> -- .venv\\Scripts\\python.exe backend\\scripts\\count_returns_missing_product.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def returns_missing_product(db):
    """[(return_id, store_id, order_number, created_at, order_item_id,
    product_id on the bill, qty, condition)] -- reads only."""
    # A service, never a router: a script must run without the app's login
    # secret (test_scripts_need_no_app_secret).
    from api.services.restock_engine import line_stock_product_id

    rows = []
    orders = db.get_collection("orders")
    for doc in db.get_collection("returns").find({"status": "COMPLETED"}):
        if doc.get("shopify_refund_id"):
            continue  # the Shopify refund door always named the IMS product
        lines = [it for it in doc.get("items") or []
                 if isinstance(it, dict) and not it.get("product_id")
                 and str(it.get("condition") or "GOOD").upper() in ("GOOD", "DAMAGED")
                 and it.get("restock", True) and float(it.get("return_qty") or 0) > 0]
        if not lines:
            continue
        order = orders.find_one({"order_id": doc.get("order_id")}) or {}
        by_item = {str(ln.get("item_id") or ln.get("id")): ln
                   for ln in order.get("items") or [] if isinstance(ln, dict)}
        for it in lines:
            # None for a line that never took a stock unit (a lens, a service).
            product = line_stock_product_id(by_item.get(str(it.get("order_item_id") or "")))
            if product:
                rows.append((doc.get("return_id"), doc.get("store_id"),
                             doc.get("order_number") or order.get("order_number"), doc.get("created_at"),
                             it.get("order_item_id"), product, float(it.get("return_qty") or 0),
                             str(it.get("condition") or "GOOD").upper()))
    return sorted(rows, key=lambda r: (str(r[3]), str(r[0])))


def _connect():
    from database.connection import DatabaseConfig, get_db, init_db

    mongo_url = os.getenv("MONGO_PUBLIC_URL") or os.getenv("MONGODB_URL") or os.getenv("MONGO_URL")
    config = (
        DatabaseConfig.from_uri(mongo_url, database="ims_2_0") if mongo_url else DatabaseConfig.from_env()
    )
    return get_db() if init_db(config) else None


def main() -> int:
    conn = _connect()
    if conn is None or not conn.is_connected:
        print("[ERROR] Could not connect to MongoDB (run via railway run).")
        return 2
    rows = returns_missing_product(conn.db)
    print(f"returned lines saved with no product (piece may still read SOLD): {len(rows)}")
    for rid, store, number, at, item, product, qty, condition in rows:
        print(f"  {rid}  shop {store}  bill {number}  {at}  line {item}  product {product}  "
              f"qty {qty:g}  {condition}: should be "
              f"{'back on sale' if condition == 'GOOD' else 'set aside (quarantine)'}")
    print("Nothing was changed. Check each piece in stock; a correction needs the owner's approval.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
