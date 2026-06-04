"""
IMS 2.0 - Online-order MAPPER tests  (BVI Phase 3b)
====================================================
api.services.online_order_mapper.map_shopify_order is the SINGLE authoritative
Shopify-order -> canonical IMS-order mapper. It REUSES shopify_ingest's create path
(GST + invoice serial + hard idempotency) and ADDS variant->sku resolution, customer
match/create, and status-sync on re-ingest. These tests pin those guarantees:

  Create + channel + GST-inclusive total
    - a sample Shopify order -> ONE IMS order tagged channel='ONLINE' with mapped
      lines + a matched/created customer + a GST-inclusive grand_total that equals
      the all-in price the buyer paid (taxable + tax == gross).

  Count-once (revenue must never double-count)
    - re-ingesting the SAME shopify_order_id does NOT create a 2nd order; the mapper
      returns 'duplicate' (same IMS order) and SYNCS the status (e.g. a later
      orders/cancelled flips status -> CANCELLED) in place.

  Variant resolution + graceful fallback
    - a Shopify line with a variant_id mapped in catalog_variants resolves to the
      IMS sku (-> the product master's HSN/GST).
    - an UNMAPPED variant falls back gracefully (sku-on-line, then the product_type
      category hint) and still books the order.

  Customer match (no duplicate buyer)
    - a buyer whose phone already exists as an IMS customer is MATCHED, not
      duplicated; the order carries that existing customer_id.

These run pure (no network, no real Mongo): an in-memory fake DB + a real
OrderRepository over a fake `orders` collection exercises the real invoice serial
allocator + the real _build_invoice_gst_split.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test_x")
# Pin inclusive pricing so the GST assertions are deterministic regardless of env.
os.environ["GST_PRICING_MODE"] = "inclusive"
# Pin the online store bucket so the resolver is deterministic in the test.
os.environ["ONLINE_STORE_ID"] = "BV-ONLINE-01"

from api.services import online_order_mapper


# ---------------------------------------------------------------------------
# Minimal in-memory Mongo emulator (only what the mapper + ingest touch).
# ---------------------------------------------------------------------------


class _DuplicateKeyError(Exception):
    pass


def _match(doc, filter_) -> bool:
    if not filter_:
        return True
    for k, expected in filter_.items():
        if k == "$or":
            if not any(_match(doc, sub) for sub in expected):
                return False
            continue
        actual = doc.get(k)
        if isinstance(expected, dict):
            for op, op_val in expected.items():
                if op == "$type":
                    if actual is None:
                        return False
                elif op == "$gte":
                    if actual is None or actual < op_val:
                        return False
                elif op == "$lte":
                    if actual is None or actual > op_val:
                        return False
                else:
                    return False
        else:
            if actual != expected:
                return False
    return True


class _Cursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, key, direction=-1):
        self._docs.sort(key=lambda d: d.get(key) or "", reverse=(direction == -1))
        return self

    def skip(self, n):
        self._docs = self._docs[n:]
        return self

    def limit(self, n):
        self._docs = self._docs[:n]
        return self

    def __iter__(self):
        return iter([dict(d) for d in self._docs])


class FakeCollection:
    def __init__(self, name: str, database: "FakeDB"):
        self._name = name
        self.database = database  # next_invoice_number reads .database["counters"]
        self.docs: list = []
        self._unique_fields: set = set()

    def create_index(self, keys, **kwargs):
        if kwargs.get("unique"):
            if isinstance(keys, str):
                self._unique_fields.add(keys)
        return None

    def _violates_unique(self, doc) -> bool:
        for f in self._unique_fields:
            val = doc.get(f)
            if val is None:
                continue
            for d in self.docs:
                if d.get(f) == val:
                    return True
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

    def find(self, filter_=None, projection=None):
        return _Cursor([d for d in self.docs if _match(d, filter_)])

    def count_documents(self, filter_=None):
        return len([d for d in self.docs if _match(d, filter_)])

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
        if upsert:
            doc = dict(filter_)
            for k, v in (update.get("$set") or {}).items():
                doc[k] = v
            self.docs.append(doc)
            return type("R", (), {"modified_count": 0, "matched_count": 0, "upserted_id": 1})()
        return type("R", (), {"modified_count": 0, "matched_count": 0})()


class FakeDB:
    is_connected = True

    def __init__(self):
        self._collections: dict = {}

    def __getitem__(self, name):
        return self.get_collection(name)

    def get_collection(self, name):
        if name not in self._collections:
            self._collections[name] = FakeCollection(name, self)
        return self._collections[name]


# ---------------------------------------------------------------------------
# Wiring: patch the dependency getters the mapper + ingest resolve lazily.
# ---------------------------------------------------------------------------


@pytest.fixture
def wired(monkeypatch):
    db = FakeDB()

    from database.repositories.order_repository import OrderRepository
    from database.repositories.customer_repository import CustomerRepository

    order_repo = OrderRepository(db.get_collection("orders"))
    customer_repo = CustomerRepository(db.get_collection("customers"))

    store_state = {"code": "20"}  # Jharkhand by default

    class _StoreRepo:
        def find_by_id(self, _store_id):
            return {"gstin": "", "state_code": store_state["code"]}

        def find_active(self, filter=None):
            return [{"store_id": "BV-ONLINE-01", "state_code": store_state["code"]}]

    import api.dependencies as deps

    monkeypatch.setattr(deps, "get_order_repository", lambda: order_repo)
    monkeypatch.setattr(deps, "get_product_repository", lambda: None)
    monkeypatch.setattr(deps, "get_store_repository", lambda: _StoreRepo())
    monkeypatch.setattr(deps, "get_customer_repository", lambda: customer_repo)

    return {
        "db": db,
        "orders": db.get_collection("orders"),
        "customers": db.get_collection("customers"),
        "variants": db.get_collection("catalog_variants"),
        "store_state": store_state,
        "customer_repo": customer_repo,
    }


def _frame_order(order_id, buyer_state="20", price="999.00", variant_id=999001, sku="RB-1234"):
    """A Shopify orders/create payload: one 5%-GST frame line."""
    return {
        "id": order_id,
        "name": f"#{order_id}",
        "financial_status": "paid",
        "fulfillment_status": None,
        "email": "buyer@example.com",
        "phone": "+91 98765 43210",
        "customer": {"id": 555, "first_name": "Ravi", "last_name": "Kumar", "phone": "+91 98765 43210"},
        "shipping_address": {"province": buyer_state, "province_code": buyer_state},
        "line_items": [
            {
                "id": 9001,
                "product_id": 7001,
                "variant_id": variant_id,
                "title": "Ray-Ban Frame RB1234",
                "product_type": "Frames",
                "sku": sku,
                "quantity": 1,
                "price": price,
                "total_discount": "0.00",
            }
        ],
    }


# ---------------------------------------------------------------------------
# Create + channel + GST-inclusive total + customer
# ---------------------------------------------------------------------------


def test_maps_to_one_online_order_with_customer_and_inclusive_gst(wired):
    payload = _frame_order(10001, buyer_state="20", price="999.00")

    res = online_order_mapper.map_shopify_order(payload, wired["db"], topic="orders/create")

    assert res["status"] == "created"
    assert res["order_id"]
    assert res["invoice_number"]
    assert res["store_id"] == "BV-ONLINE-01"
    assert res["customer_id"], "a customer must be matched/created"

    online = [d for d in wired["orders"].docs if d.get("shopify_order_id") == "10001"]
    assert len(online) == 1
    order = online[0]
    assert order["channel"] == "ONLINE"
    assert order["source"] == "shopify"
    assert order["customer_id"] == res["customer_id"]
    # Online price is GST-inclusive: the all-in price the buyer paid.
    assert order["grand_total"] == 999.0
    # taxable + tax reconciles to the gross.
    assert round(order["tax_totals"]["taxable"] + order["tax_totals"]["tax"], 2) == 999.0
    # Mapped line carried through.
    assert len(order["items"]) == 1
    assert order["items"][0]["sku"] == "RB-1234"

    # The customer row was actually created.
    cust = wired["customers"].find_one({"customer_id": res["customer_id"]})
    assert cust is not None
    assert cust["channel"] == "ONLINE"
    assert cust["mobile"] == "9876543210"  # normalized Indian mobile


# ---------------------------------------------------------------------------
# Count-once + status sync on re-ingest
# ---------------------------------------------------------------------------


def test_reingest_does_not_duplicate_and_syncs_status(wired):
    payload = _frame_order(10002, buyer_state="20")

    first = online_order_mapper.map_shopify_order(payload, wired["db"], topic="orders/create")
    assert first["status"] == "created"

    # Re-ingest the SAME shopify order id, now CANCELLED -> no 2nd order, status synced.
    cancelled = _frame_order(10002, buyer_state="20")
    cancelled["financial_status"] = "refunded"
    cancelled["cancelled_at"] = "2026-06-01T10:00:00Z"
    second = online_order_mapper.map_shopify_order(cancelled, wired["db"], topic="orders/cancelled")

    assert second["status"] == "duplicate"
    assert second["order_id"] == first["order_id"]
    assert second["status_synced"] is True

    online = [d for d in wired["orders"].docs if d.get("shopify_order_id") == "10002"]
    assert len(online) == 1, "count-once: still exactly one IMS order"
    assert online[0]["status"] == "CANCELLED"
    assert online[0]["cancelled_at"] == "2026-06-01T10:00:00Z"


def test_status_only_update_without_line_items_syncs_existing(wired):
    # First create the order.
    online_order_mapper.map_shopify_order(_frame_order(10003), wired["db"], topic="orders/create")

    # An orders/updated that carries NO line_items (Shopify partial payload) must
    # still sync status of the order we already have, not create a new one.
    update = {"id": 10003, "financial_status": "paid", "fulfillment_status": "fulfilled"}
    res = online_order_mapper.map_shopify_order(update, wired["db"], topic="orders/updated")

    assert res["status"] == "status_synced"
    assert res["status_synced"] is True
    order = wired["orders"].find_one({"shopify_order_id": "10003"})
    assert order["fulfillment_status"] == "FULFILLED"
    assert order["status"] == "DELIVERED"


# ---------------------------------------------------------------------------
# Variant resolution + graceful fallback
# ---------------------------------------------------------------------------


def test_variant_id_resolves_to_ims_sku(wired):
    # A catalog_variants row maps the Shopify variant -> a DIFFERENT IMS sku.
    wired["variants"].insert_one(
        {"variant_id": "v1", "sku": "IMS-SKU-77", "shopify_variant_id": "888777"}
    )
    payload = _frame_order(10004, variant_id=888777, sku="")  # no sku on the line

    res = online_order_mapper.map_shopify_order(payload, wired["db"], topic="orders/create")
    assert res["status"] == "created"
    order = wired["orders"].find_one({"shopify_order_id": "10004"})
    assert order["items"][0]["sku"] == "IMS-SKU-77", "variant_id -> catalog_variants -> IMS sku"


def test_unmapped_variant_falls_back_gracefully(wired):
    # No catalog_variants row for this variant; the line carries its own sku, and
    # the product_type 'Sunglasses' resolves the 18% category hint. Order still books.
    payload = _frame_order(10005, variant_id=123123, sku="WALKIN-SG-1", price="1180.00")
    payload["line_items"][0]["product_type"] = "Sunglasses"
    payload["line_items"][0]["title"] = "Polarized Sunglasses"

    res = online_order_mapper.map_shopify_order(payload, wired["db"], topic="orders/create")
    assert res["status"] == "created"
    order = wired["orders"].find_one({"shopify_order_id": "10005"})
    assert order["items"][0]["sku"] == "WALKIN-SG-1"  # fell back to the line sku
    rates = {round(i["gst_rate"], 2) for i in order["items"]}
    assert rates == {18.0}  # sunglass -> 18% via the category hint


# ---------------------------------------------------------------------------
# Customer match (no duplicate buyer)
# ---------------------------------------------------------------------------


def test_existing_customer_is_matched_not_duplicated(wired):
    # Seed an existing IMS customer with the buyer's mobile.
    wired["customer_repo"].create(
        {"customer_id": "CUST-EXISTING", "name": "Ravi K", "mobile": "9876543210"}
    )
    before = len(wired["customers"].docs)

    payload = _frame_order(10006)
    res = online_order_mapper.map_shopify_order(payload, wired["db"], topic="orders/create")

    assert res["customer_id"] == "CUST-EXISTING", "matched the existing customer by phone"
    order = wired["orders"].find_one({"shopify_order_id": "10006"})
    assert order["customer_id"] == "CUST-EXISTING"
    assert len(wired["customers"].docs) == before, "no duplicate customer minted"


# ---------------------------------------------------------------------------
# Fail-soft
# ---------------------------------------------------------------------------


def test_no_db_simulates_without_crashing():
    res = online_order_mapper.map_shopify_order(_frame_order(10007), None, topic="orders/create")
    assert res["status"] == "simulated"
    assert res["shopify_order_id"] == "10007"


def test_bad_payload_is_skipped_not_raised(wired):
    res = online_order_mapper.map_shopify_order({"junk": True}, wired["db"], topic="orders/create")
    assert res["status"] == "skipped"
