"""READ-ONLY: store credit minted by an online (Shopify) refund for money that
was never collected or was already given back.

Before the fix, confirming an online refund review (or the dark AUTO path)
added redeemable store credit for the whole refund without looking at what
the customer had paid: a COD order cancelled before shipping, or a refund
already paid back by hand ("manual"), still gave the customer a wallet
balance. This lists every such credit-note ledger row that MOVED a balance
(delta > 0) where:

  * the order has no paid amount IMS can read,
  * the order was never paid (amount_paid 0),
  * Shopify also refunded money on the order (payment status REFUNDED or
    PARTIAL_REFUND -- a possible double refund), or
  * the order's Shopify-refund credits add up to more than was paid.

Nothing is written. Each row needs a person: check the order, and if the
credit was not owed, reverse it with a store-credit adjustment.

  railway run --service <backend> -- .venv\\Scripts\\python.exe backend\\scripts\\audit_unpaid_refund_credit.py

Reads MONGO_PUBLIC_URL / MONGODB_URL / MONGO_URL (never printed) and
MONGODB_DATABASE (default ims_2_0).
"""

from __future__ import annotations

import math
import os
import sys
from typing import Any, Dict, List


def _num(value: Any):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def find_suspect_credits(db) -> List[Dict[str, Any]]:
    """[{return_id, shopify_refund_id, order_id, customer_id, credit, why}] -- reads only."""
    refunds = {
        r["return_id"]: r
        for r in db.get_collection("returns").find({"shopify_refund_id": {"$exists": True}})
        if r.get("return_id")
    }
    if not refunds:
        return []
    credited: List[Dict[str, Any]] = []
    for row in db.get_collection("credit_note_ledger").find(
            {"type": "ISSUED", "ref": {"$in": list(refunds)}}):
        credit = _num(row.get("delta")) or 0.0
        if credit > 0:
            credited.append({"row": row, "credit": credit, "ret": refunds[row["ref"]]})
    orders = {
        o["order_id"]: o
        for o in db.get_collection("orders").find(
            {"order_id": {"$in": sorted({c["ret"].get("order_id") for c in credited
                                         if c["ret"].get("order_id")})}})
    }
    per_order: Dict[str, float] = {}
    for c in credited:
        oid = c["ret"].get("order_id")
        per_order[oid] = per_order.get(oid, 0.0) + c["credit"]

    out = []
    for c in credited:
        oid = c["ret"].get("order_id")
        order = orders.get(oid) or {}
        paid = _num(order.get("amount_paid"))
        status = str(order.get("payment_status") or "").upper()
        if not order:
            why = "order not found in IMS"
        elif paid is None:
            why = "IMS has no paid amount for this order"
        elif paid <= 0:
            why = "order was never paid"
        elif status in ("REFUNDED", "PARTIAL_REFUND"):
            why = f"Shopify also refunded money on this order ({status}): check for a double refund"
        elif per_order[oid] > paid + 0.01:
            why = f"Shopify-refund credits Rs {per_order[oid]:.2f} are more than paid Rs {paid:.2f}"
        else:
            continue
        out.append({
            "return_id": c["ret"].get("return_id"),
            "shopify_refund_id": c["ret"].get("shopify_refund_id"),
            "order_id": oid,
            "order_number": order.get("order_number"),
            "customer_id": c["row"].get("customer_id"),
            "credit": round(c["credit"], 2),
            "created_at": c["row"].get("created_at"),
            "why": why,
        })
    return sorted(out, key=lambda r: str(r.get("created_at") or ""))


def main() -> int:
    from pymongo import MongoClient

    url = os.getenv("MONGO_PUBLIC_URL") or os.getenv("MONGODB_URL") or os.getenv("MONGO_URL")
    if not url:
        print("[ERROR] No MongoDB URL in the environment (run via railway run).")
        return 2
    client = MongoClient(url, serverSelectionTimeoutMS=10000)
    try:
        db = client[os.getenv("MONGODB_DATABASE") or "ims_2_0"]
        rows = find_suspect_credits(db)
    finally:
        client.close()
    print(f"store-credit rows from online refunds to check: {len(rows)}")
    total = 0.0
    for r in rows:
        total += r["credit"]
        print(f"  {r['created_at'] or '-'}  return {r['return_id']}  refund {r['shopify_refund_id']}  "
              f"order {r['order_number'] or r['order_id']}  customer {r['customer_id']}  "
              f"credit Rs {r['credit']:.2f}  -- {r['why']}")
    print(f"total credit to check: Rs {total:.2f}")
    print("Nothing was changed (read-only).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
