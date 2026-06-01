"""
IMS 2.0 - Shopify online-order ingestion tests (Council B10)
============================================================
IMS is the GST invoice system-of-record for online (bettervision.in / Shopify)
orders. These tests pin the load-bearing guarantees of
api.services.shopify_ingest.ingest_shopify_order:

  Idempotency (revenue must never double-count)
    - ingesting the SAME Shopify order payload twice -> exactly ONE IMS order +
      ONE invoice (the second call returns status "duplicate", same order id).
    - a replayed X-Shopify-Webhook-Id short-circuits (status "replayed").

  GST place-of-supply (the whole reason IMS mints the invoice)
    - inter-state delivery (buyer state != store state) -> IGST, CGST/SGST = 0.
    - intra-state delivery (buyer state == store state) -> CGST + SGST, IGST = 0.
    - the per-line rate resolves via resolve_gst_rate (5% optical-dominant
      default; an explicit 18% category -> 18%).

  Channel + fail-soft
    - the IMS order is tagged channel='ONLINE'.
    - no DB -> "simulated", never a crash.

These run pure (no network, no real Mongo): an in-memory fake DB + a real
OrderRepository wrapped around a fake collection exercises the real invoice
serial allocator (counters $inc) and the real _build_invoice_gst_split.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test_x")
# Pin inclusive pricing so the assertions are deterministic regardless of env.
os.environ["GST_PRICING_MODE"] = "inclusive"

from api.services import shopify_ingest


# ---------------------------------------------------------------------------
# Minimal in-memory Mongo emulator (just what the ingest path touches).
# ---------------------------------------------------------------------------


class FakeCollection:
    def __init__(self, name: str, database: "FakeDB"):
        self._name = name
        self.database = database  # next_invoice_number reads .database["counters"]
        self.docs: list = []
        self._unique_fields: set = set()

    # --- index registration (we only honour the unique ones we care about) ---
    def create_index(self, keys, **kwargs):
        if kwargs.get("unique"):
            if isinstance(keys, str):
                self._unique_fields.add(keys)
        return None

    def _violates_unique(self, doc) -> bool:
        for f in self._unique_fields:
            val = doc.get(f) if f != "_id" else doc.get("_id")
            if val is None:
                continue
            for d in self.docs:
                existing = d.get(f) if f != "_id" else d.get("_id")
                if existing == val:
                    return True
        # _id is always unique in Mongo
        if "_id" in doc:
            for d in self.docs:
                if d.get("_id") == doc.get("_id"):
                    return True
        return False

    def insert_one(self, doc):
        if self._violates_unique(doc):
            raise _DuplicateKeyError("E11000 duplicate key error")
        self.docs.append(dict(doc))
        return type("R", (), {"inserted_id": doc.get("_id")})()

    def find_one(self, filter_=None, projection=None):
        for d in self.docs:
            if _match(d, filter_):
                return dict(d)
        return None

    def find_one_and_update(self, filter_, update, upsert=False, return_document=None):
        target = None
        for d in self.docs:
            if _match(d, filter_):
                target = d
                break
        if target is None and upsert:
            target = dict(filter_)
            self.docs.append(target)
        if target is None:
            return None
        for op, fields in (update or {}).items():
            if op == "$inc":
                for k, v in fields.items():
                    target[k] = (target.get(k) or 0) + v
            elif op == "$set":
                for k, v in fields.items():
                    target[k] = v
        return dict(target)

    def update_one(self, filter_, update, upsert=False):
        for d in self.docs:
            if _match(d, filter_):
                for k, v in (update.get("$set") or {}).items():
                    d[k] = v
                return type("R", (), {"modified_count": 1, "matched_count": 1})()
        return type("R", (), {"modified_count": 0, "matched_count": 0})()


class _DuplicateKeyError(Exception):
    pass


def _match(doc, filter_) -> bool:
    if not filter_:
        return True
    for k, expected in filter_.items():
        if isinstance(expected, dict):
            actual = doc.get(k)
            for op, op_val in expected.items():
                if op == "$type":
                    if actual is None:
                        return False
                else:
                    return False
        else:
            if doc.get(k) != expected:
                return False
    return True


class FakeDB:
    is_connected = True

    def __init__(self):
        self._collections: dict = {}

    def __getitem__(self, name):  # counters access: self.collection.database["counters"]
        return self.get_collection(name)

    def get_collection(self, name):
        if name not in self._collections:
            self._collections[name] = FakeCollection(name, self)
        return self._collections[name]


# ---------------------------------------------------------------------------
# Wiring: patch the three dependency getters the service resolves lazily.
# ---------------------------------------------------------------------------


@pytest.fixture
def wired(monkeypatch):
    db = FakeDB()

    # A real OrderRepository on a fake `orders` collection -> exercises the real
    # atomic invoice serial allocator (counters $inc) + ensure_invoice_index.
    from database.repositories.order_repository import OrderRepository

    orders_coll = db.get_collection("orders")
    order_repo = OrderRepository(orders_coll)

    # No product master / no store doc in these tests: SKU lookup misses (so the
    # category hint path is used) and the store state is supplied by the test
    # via a patched store repo when needed.
    store_state = {"code": "20"}  # Jharkhand, by default

    class _StoreRepo:
        def find_by_id(self, _store_id):
            return {"gstin": "", "state_code": store_state["code"]}

    import api.dependencies as deps

    monkeypatch.setattr(deps, "get_order_repository", lambda: order_repo)
    monkeypatch.setattr(deps, "get_product_repository", lambda: None)
    monkeypatch.setattr(deps, "get_store_repository", lambda: _StoreRepo())

    return {"db": db, "orders": orders_coll, "store_state": store_state}


def _frame_order(order_id: int, buyer_state: str, price: str = "999.00"):
    """A Shopify orders/create payload: one 5%-GST frame line."""
    return {
        "id": order_id,
        "name": f"#{order_id}",
        "financial_status": "paid",
        "email": "buyer@example.com",
        "customer": {"id": 555, "first_name": "Ravi", "last_name": "Kumar"},
        "shipping_address": {"province": buyer_state, "province_code": buyer_state},
        "line_items": [
            {
                "id": 9001,
                "product_id": 7001,
                "title": "Ray-Ban Frame RB1234",
                "product_type": "Frames",
                "sku": "RB-1234",
                "quantity": 1,
                "price": price,
                "total_discount": "0.00",
            }
        ],
    }


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_double_ingest_yields_one_order_and_one_invoice(wired):
    payload = _frame_order(1001, buyer_state="20")  # intra-state

    first = shopify_ingest.ingest_shopify_order(wired["db"], payload, topic="orders/create")
    second = shopify_ingest.ingest_shopify_order(wired["db"], payload, topic="orders/create")

    assert first["status"] == "created"
    assert first["order_id"]
    assert first["invoice_number"]

    # Second ingest of the SAME Shopify order id -> duplicate, same IMS order,
    # NO new order doc, NO second invoice.
    assert second["status"] == "duplicate"
    assert second["order_id"] == first["order_id"]
    assert second["invoice_number"] == first["invoice_number"]

    online_orders = [d for d in wired["orders"].docs if d.get("shopify_order_id") == "1001"]
    assert len(online_orders) == 1, "exactly one IMS order for the Shopify order"


def test_replayed_webhook_id_short_circuits(wired):
    payload = _frame_order(1002, buyer_state="20")
    wid = "abcdef-webhook-id-123"

    first = shopify_ingest.ingest_shopify_order(
        wired["db"], payload, webhook_id=wid, topic="orders/create"
    )
    replay = shopify_ingest.ingest_shopify_order(
        wired["db"], payload, webhook_id=wid, topic="orders/create"
    )

    assert first["status"] == "created"
    assert replay["status"] == "replayed"
    assert replay["order_id"] == first["order_id"]
    online_orders = [d for d in wired["orders"].docs if d.get("shopify_order_id") == "1002"]
    assert len(online_orders) == 1


# ---------------------------------------------------------------------------
# GST place-of-supply: IGST vs CGST+SGST
# ---------------------------------------------------------------------------


def test_interstate_delivery_yields_igst(wired):
    wired["store_state"]["code"] = "20"  # supplier: Jharkhand
    payload = _frame_order(2001, buyer_state="27")  # buyer: Maharashtra -> inter-state

    res = shopify_ingest.ingest_shopify_order(wired["db"], payload, topic="orders/create")
    assert res["status"] == "created"
    assert res["interstate"] is True

    order = wired["orders"].find_one({"shopify_order_id": "2001"})
    totals = order["tax_totals"]
    assert totals["igst"] > 0, "inter-state must charge IGST"
    assert totals["cgst"] == 0 and totals["sgst"] == 0
    # taxable + tax reconciles to grand_total.
    assert round(totals["taxable"] + totals["igst"], 2) == order["grand_total"]


def test_intrastate_delivery_yields_cgst_sgst(wired):
    wired["store_state"]["code"] = "20"  # supplier: Jharkhand
    payload = _frame_order(2002, buyer_state="20")  # buyer: Jharkhand -> intra-state

    res = shopify_ingest.ingest_shopify_order(wired["db"], payload, topic="orders/create")
    assert res["status"] == "created"
    assert res["interstate"] is False

    order = wired["orders"].find_one({"shopify_order_id": "2002"})
    totals = order["tax_totals"]
    assert totals["cgst"] > 0 and totals["sgst"] > 0, "intra-state must split CGST+SGST"
    assert totals["igst"] == 0
    # CGST + SGST + taxable reconciles to grand_total.
    assert round(totals["taxable"] + totals["cgst"] + totals["sgst"], 2) == order["grand_total"]


def test_rate_resolves_via_resolve_gst_rate(wired):
    # A frame line (product_type "Frames") resolves to 5% via the category hint.
    payload = _frame_order(3001, buyer_state="20", price="1050.00")
    res = shopify_ingest.ingest_shopify_order(wired["db"], payload, topic="orders/create")
    assert res["status"] == "created"
    order = wired["orders"].find_one({"shopify_order_id": "3001"})
    rates = {round(i["gst_rate"], 2) for i in order["items"]}
    assert rates == {5.0}, "frame -> 5% optical rate via resolve_gst_rate"

    # A sunglass line (product_type "Sunglasses") resolves to 18%.
    sg = _frame_order(3002, buyer_state="20", price="1180.00")
    sg["line_items"][0]["product_type"] = "Sunglasses"
    sg["line_items"][0]["title"] = "Polarized Sunglasses"
    sg["line_items"][0]["sku"] = "SG-9"
    res2 = shopify_ingest.ingest_shopify_order(wired["db"], sg, topic="orders/create")
    order2 = wired["orders"].find_one({"shopify_order_id": "3002"})
    rates2 = {round(i["gst_rate"], 2) for i in order2["items"]}
    assert rates2 == {18.0}, "sunglass -> 18% via resolve_gst_rate"


# ---------------------------------------------------------------------------
# Channel tag + fail-soft
# ---------------------------------------------------------------------------


def test_order_tagged_channel_online(wired):
    payload = _frame_order(4001, buyer_state="20")
    shopify_ingest.ingest_shopify_order(wired["db"], payload, topic="orders/create")
    order = wired["orders"].find_one({"shopify_order_id": "4001"})
    assert order["channel"] == "ONLINE"
    assert order["source"] == "shopify"
    # Shopify "paid" -> the IMS online order is PAID on ingestion.
    assert order["payment_status"] == "PAID"


def test_no_db_simulates_without_crashing():
    payload = _frame_order(5001, buyer_state="20")
    res = shopify_ingest.ingest_shopify_order(None, payload, topic="orders/create")
    assert res["status"] == "simulated"
    assert res["shopify_order_id"] == "5001"


def test_non_order_topic_ignored(wired):
    payload = _frame_order(6001, buyer_state="20")
    res = shopify_ingest.ingest_shopify_order(
        wired["db"], payload, topic="products/update"
    )
    assert res["status"] == "ignored"
    assert not [d for d in wired["orders"].docs if d.get("shopify_order_id") == "6001"]
