"""
IMS 2.0 - Shopify inbound webhook handlers (BVI-retirement phase 0)
====================================================================
Pins the phase-0 handlers that let IMS take over the Shopify topics BVI handles
today, so nothing is lost when BVI is retired:

  refunds/create  (api.services.shopify_refund.handle_shopify_refund)
    - DEFAULT (SHOPIFY_REFUND_AUTO off) -> accountant review QUEUE; NO ledger,
      NO stock movement.
    - AUTO (opt-in) -> GST credit note (credit_note_ledger, REUSING the in-store
      _issue_store_credit) + restock (REUSING _restock_good_items).
    - the credit-note tax equals the ORIGINAL line tax (a true reversal; REUSES
      returns_engine, no new GST math).
    - idempotent on the Shopify refund id (no double-credit / double-restock).
    - an unknown order fails soft (queued UNMATCHED, never crashes).

  fulfillments/update (api.services.shopify_fulfillment.reconcile_fulfillment)
    - marks the matching IMS online order SHIPPED + stamps the AWB; unknown
      order fails soft.

  customers/create+update (api.services.shopify_customer_sync.upsert_shopify_customer)
    - dedupes on mobile (IMS canonical dedupe) and does NOT clobber a non-empty
      IMS field with sparser Shopify data (merge, don't overwrite).

  NEXUS dispatch table (agents.implementations.nexus)
    - routes each topic to the right handler AND still handles every ORDER topic
      via the unchanged mapper path.

  shopify_push registrar
    - webhookSubscriptionDelete builds the right mutation.
    - register_webhooks defaults to the FULL cutover topic set.

Pure: no network, no real Mongo. An in-memory FakeDB + real OrderRepository /
CustomerRepository over fake collections + a fake stock repo exercise the real
reused return machinery.
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any, Dict, List, Optional

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test_x")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ["GST_PRICING_MODE"] = "inclusive"
os.environ["ONLINE_STORE_ID"] = "BV-ONLINE-01"


# ---------------------------------------------------------------------------
# Minimal in-memory Mongo emulator (subset the handlers + reused code touch).
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

    def sort(self, *args, **kwargs):
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
        self.database = database
        self.docs: list = []
        self._unique_fields: set = set()

    def create_index(self, keys, **kwargs):
        if kwargs.get("unique") and isinstance(keys, str):
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
            return type("R", (), {"modified_count": 0, "upserted_id": 1})()
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


class _FakeConn:
    """Stand-in for the DatabaseConnection the returns router's _get_db() /
    audit path resolve via api.dependencies.get_db (needs .is_connected + .db)."""

    def __init__(self, db: FakeDB):
        self.db = db
        self.is_connected = True


class _FakeStockRepo:
    """Serialized-stock repo the reused _restock_good_items drives."""

    def __init__(self):
        self.units: List[Dict[str, Any]] = []
        self._seq = 0

    def find_many(self, query: Dict[str, Any]):
        return [
            dict(u)
            for u in self.units
            if all(u.get(k) == v for k, v in query.items())
        ]

    def update(self, sid, data: Dict[str, Any]) -> bool:
        for u in self.units:
            if u.get("stock_id") == sid:
                u.update(data)
                return True
        return False

    def create(self, data: Dict[str, Any]):
        self._seq += 1
        d = dict(data)
        d.setdefault("stock_id", f"stk-mint-{self._seq}")
        self.units.append(d)
        return d


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Fixtures / wiring
# ---------------------------------------------------------------------------


@pytest.fixture
def wired(monkeypatch):
    db = FakeDB()

    from database.repositories.customer_repository import CustomerRepository

    customer_repo = CustomerRepository(db.get_collection("customers"))
    stock_repo = _FakeStockRepo()
    conn = _FakeConn(db)

    class _StoreRepo:
        def find_by_id(self, _sid):
            return {"gstin": "", "state_code": "20"}

        def find_active(self, filter=None):
            return [{"store_id": "BV-ONLINE-01", "state_code": "20"}]

    import api.dependencies as deps
    import api.routers.returns as returns_router

    # Reused return helpers resolve repos from THEIR module namespace.
    monkeypatch.setattr(returns_router, "get_customer_repository", lambda: customer_repo)
    monkeypatch.setattr(returns_router, "get_stock_repository", lambda: stock_repo)
    # _ledger_coll / stock_audit resolve the DB via dependencies.get_db (lazy).
    monkeypatch.setattr(deps, "get_db", lambda: conn, raising=False)
    # Customer dedupe/create + store resolution.
    monkeypatch.setattr(deps, "get_customer_repository", lambda: customer_repo)
    monkeypatch.setattr(deps, "get_store_repository", lambda: _StoreRepo())

    return {
        "db": db,
        "customer_repo": customer_repo,
        "stock_repo": stock_repo,
        "customers": db.get_collection("customers"),
        "returns": db.get_collection("returns"),
        "review": db.get_collection("shopify_refund_review"),
        "ledger": db.get_collection("credit_note_ledger"),
        "orders": db.get_collection("orders"),
    }


def _seed_online_order(wired, *, shopify_order_id="5001", customer_id="CUST-1"):
    item = {
        "item_id": "it-1",
        "product_id": "7001",           # Shopify product id
        "ims_product_id": "IMS-P-1",    # IMS product id (restock key)
        "shopify_line_item_id": 9001,
        "shopify_variant_id": 888,
        "sku": "RB-1234",
        "product_name": "Ray-Ban RB1234",
        "quantity": 1,
        "gst_rate": 5.0,
        "taxable_value": 952.38,
        "tax_amount": 47.62,
    }
    order = {
        "order_id": "ord-abc",
        "_id": "ord-abc",
        "shopify_order_id": shopify_order_id,
        "order_number": "ONL-5001",
        "invoice_number": "BV/26-27/0001",
        "channel": "ONLINE",
        "source": "shopify",
        "customer_id": customer_id,
        "customer_name": "Ravi Kumar",
        "store_id": "BV-ONLINE-01",
        "fulfillment_stores": ["BV-GANGA-01"],
        "items": [item],
        "grand_total": 1000.0,
        "status": "CONFIRMED",
    }
    wired["orders"].insert_one(order)
    return order


def _refund_payload(refund_id=700001, shopify_order_id=5001, qty=1):
    return {
        "id": refund_id,
        "order_id": shopify_order_id,
        "restock": True,
        "refund_line_items": [
            {
                "id": 1,
                "quantity": qty,
                "line_item_id": 9001,
                "restock_type": "return",
                "subtotal": 952.38,
                "total_tax": 47.62,
                "line_item": {
                    "id": 9001,
                    "variant_id": 888,
                    "product_id": 7001,
                    "sku": "RB-1234",
                    "quantity": 1,
                    "price": "1000.00",
                },
            }
        ],
    }


# ===========================================================================
# refunds/create -- DEFAULT accountant queue
# ===========================================================================


def test_refund_default_routes_to_accountant_queue(wired, monkeypatch):
    monkeypatch.delenv("SHOPIFY_REFUND_AUTO", raising=False)
    _seed_online_order(wired)
    from api.services.shopify_refund import handle_shopify_refund

    res = handle_shopify_refund(wired["db"], _refund_payload(), topic="refunds/create")

    assert res["status"] == "queued"
    assert res["queue_status"] == "PENDING"
    # The proposed credit note carries the correct GST reversal.
    assert res["gross_refund"] == 1000.0
    assert res["gst_breakup"]["tax"] == 47.62
    # A review row was written; NO ledger entry, NO returns doc (nothing posted).
    review = wired["review"].find_one({"shopify_refund_id": "700001"})
    assert review is not None and review["status"] == "PENDING"
    assert wired["ledger"].count_documents({}) == 0
    assert wired["returns"].count_documents({}) == 0


# ===========================================================================
# refunds/create -- AUTO credit note + restock
# ===========================================================================


def test_refund_auto_posts_credit_note_and_restock(wired, monkeypatch):
    monkeypatch.setenv("SHOPIFY_REFUND_AUTO", "1")
    _seed_online_order(wired)
    # Seed the SOLD serialized unit at the fulfilment store so restock REACTIVATES
    # the original unit (rather than minting).
    wired["stock_repo"].units.append(
        {
            "stock_id": "stk-1",
            "product_id": "IMS-P-1",
            "store_id": "BV-GANGA-01",
            "order_id": "ord-abc",
            "status": "SOLD",
        }
    )
    # A customer must exist for the credit-note ledger entry.
    wired["customer_repo"].create(
        {"customer_id": "CUST-1", "name": "Ravi Kumar", "mobile": "9876543210", "store_credit": 0.0}
    )

    from api.services.shopify_refund import handle_shopify_refund

    res = handle_shopify_refund(wired["db"], _refund_payload(), topic="refunds/create")

    assert res["status"] == "credited"
    assert res["credit_note_issued"] is True
    assert res["restock_applied"] is True
    # GST reversal: the credit-note tax equals the ORIGINAL line tax.
    assert res["gst_breakup"]["tax"] == 47.62

    # A credit_note_ledger ISSUED entry was written (the GSTR-1 CDNR source).
    ledger_rows = list(wired["ledger"].find({"customer_id": "CUST-1"}))
    assert len(ledger_rows) == 1
    assert ledger_rows[0].get("gross_refund") == 1000.0
    # The credit-note returns doc is stamped with the Shopify refund id.
    ret = wired["returns"].find_one({"shopify_refund_id": "700001"})
    assert ret is not None and ret["return_type"] == "CREDIT_NOTE"
    # The original SOLD unit was reactivated to AVAILABLE.
    assert wired["stock_repo"].units[0]["status"] == "AVAILABLE"


def test_refund_auto_is_idempotent_on_refund_id(wired, monkeypatch):
    monkeypatch.setenv("SHOPIFY_REFUND_AUTO", "1")
    _seed_online_order(wired)
    wired["customer_repo"].create(
        {"customer_id": "CUST-1", "name": "Ravi", "mobile": "9876543210", "store_credit": 0.0}
    )
    from api.services.shopify_refund import handle_shopify_refund

    first = handle_shopify_refund(wired["db"], _refund_payload(), topic="refunds/create")
    second = handle_shopify_refund(wired["db"], _refund_payload(), topic="refunds/create")

    assert first["status"] == "credited"
    assert second["status"] == "duplicate"
    # No double-credit / double-return doc.
    assert wired["returns"].count_documents({"shopify_refund_id": "700001"}) == 1
    assert wired["ledger"].count_documents({"customer_id": "CUST-1"}) == 1


def test_refund_unknown_order_fails_soft(wired, monkeypatch):
    monkeypatch.delenv("SHOPIFY_REFUND_AUTO", raising=False)
    # No order seeded.
    from api.services.shopify_refund import handle_shopify_refund

    res = handle_shopify_refund(
        wired["db"], _refund_payload(refund_id=999, shopify_order_id=404040),
        topic="refunds/create",
    )
    assert res["status"] == "order_not_found"
    # Recorded as UNMATCHED for an accountant; nothing posted.
    row = wired["review"].find_one({"shopify_refund_id": "999"})
    assert row is not None and row["status"] == "UNMATCHED"
    assert wired["returns"].count_documents({}) == 0


def test_refund_no_db_simulates():
    from api.services.shopify_refund import handle_shopify_refund

    res = handle_shopify_refund(None, _refund_payload(), topic="refunds/create")
    assert res["status"] == "simulated"


# ===========================================================================
# fulfillments/update
# ===========================================================================


def test_fulfillment_marks_order_shipped_with_tracking(wired):
    _seed_online_order(wired)
    from api.services.shopify_fulfillment import reconcile_fulfillment

    payload = {
        "id": 88881,
        "order_id": 5001,
        "status": "success",
        "tracking_number": "AWB123456",
        "tracking_company": "Delhivery",
        "tracking_url": "https://track/AWB123456",
    }
    res = reconcile_fulfillment(wired["db"], payload, topic="fulfillments/update")
    assert res["status"] == "reconciled"
    order = wired["orders"].find_one({"shopify_order_id": "5001"})
    assert order["fulfillment_status"] == "FULFILLED"
    assert order["status"] == "SHIPPED"
    assert order["awb"] == "AWB123456"


def test_fulfillment_delivered_sets_delivered(wired):
    _seed_online_order(wired)
    from api.services.shopify_fulfillment import reconcile_fulfillment

    payload = {
        "id": 88882,
        "order_id": 5001,
        "status": "success",
        "shipment_status": "delivered",
        "tracking_number": "AWB999",
    }
    res = reconcile_fulfillment(wired["db"], payload, topic="fulfillments/update")
    assert res["order_status"] == "DELIVERED"
    order = wired["orders"].find_one({"shopify_order_id": "5001"})
    assert order["status"] == "DELIVERED"


def test_fulfillment_unknown_order_fails_soft(wired):
    from api.services.shopify_fulfillment import reconcile_fulfillment

    res = reconcile_fulfillment(
        wired["db"], {"id": 1, "order_id": 424242, "status": "success"},
        topic="fulfillments/create",
    )
    assert res["status"] == "order_not_found"  # no crash


# ===========================================================================
# customers/create + customers/update
# ===========================================================================


def _customer_payload(cid=555, phone="+91 98765 43210", email="ravi@example.com",
                      first="Ravi", last="Kumar"):
    return {
        "id": cid,
        "first_name": first,
        "last_name": last,
        "email": email,
        "phone": phone,
    }


def test_customer_create_upserts_and_dedupes(wired):
    from api.services.shopify_customer_sync import upsert_shopify_customer

    res1 = upsert_shopify_customer(wired["db"], _customer_payload(), topic="customers/create")
    assert res1["status"] == "created"
    cid = res1["customer_id"]
    assert cid
    # Same buyer (same mobile) again -> matched, NOT duplicated.
    before = wired["customers"].count_documents({})
    res2 = upsert_shopify_customer(wired["db"], _customer_payload(), topic="customers/update")
    assert res2["status"] == "updated"
    assert res2["customer_id"] == cid
    assert wired["customers"].count_documents({}) == before


def test_customer_update_does_not_clobber_richer_ims_data(wired):
    # A rich IMS customer already exists (real name + email).
    wired["customer_repo"].create(
        {
            "customer_id": "CUST-RICH",
            "name": "Dr Ravi Kumar Sr",
            "mobile": "9876543210",
            "phone": "9876543210",
            "email": "rich@ims.example",
        }
    )
    from api.services.shopify_customer_sync import upsert_shopify_customer

    # Shopify sends SPARSER data (placeholder-ish name + a different email) for the
    # same mobile. The non-empty IMS name + email MUST be preserved.
    payload = _customer_payload(
        phone="+91 98765 43210", email="sparse@shopify.example",
        first="Ravi", last="",
    )
    res = upsert_shopify_customer(wired["db"], payload, topic="customers/update")
    assert res["status"] == "updated"
    assert res["customer_id"] == "CUST-RICH"

    cust = wired["customers"].find_one({"customer_id": "CUST-RICH"})
    assert cust["name"] == "Dr Ravi Kumar Sr"      # not clobbered
    assert cust["email"] == "rich@ims.example"     # not clobbered
    # shopify_customer_id IS stamped (was absent).
    assert cust.get("shopify_customer_id") == "555"


# ===========================================================================
# NEXUS dispatch table
# ===========================================================================


def _make_nexus():
    from agents.implementations.nexus import NexusAgent

    return NexusAgent(db=FakeDB())


def test_dispatch_table_routes_each_topic(monkeypatch):
    from agents.implementations import nexus as nexus_mod

    calls: List[str] = []

    monkeypatch.setattr(
        "api.services.shopify_refund.handle_shopify_refund",
        lambda db, payload, **kw: calls.append("refund") or {"status": "queued"},
    )
    monkeypatch.setattr(
        "api.services.shopify_fulfillment.reconcile_fulfillment",
        lambda db, payload, **kw: calls.append("fulfillment") or {"status": "reconciled"},
    )
    monkeypatch.setattr(
        "api.services.shopify_customer_sync.upsert_shopify_customer",
        lambda db, payload, **kw: calls.append("customer") or {"status": "created"},
    )
    monkeypatch.setattr(
        "api.services.online_order_mapper.map_shopify_order",
        lambda payload, db, **kw: calls.append("order") or {"status": "created"},
    )

    agent = _make_nexus()

    for topic, expected in [
        ("orders/create", "order"),
        ("orders/paid", "order"),
        ("orders/cancelled", "order"),
        ("refunds/create", "refund"),
        ("fulfillments/create", "fulfillment"),
        ("fulfillments/update", "fulfillment"),
        ("customers/create", "customer"),
        ("customers/update", "customer"),
    ]:
        calls.clear()
        agent._current_webhook_headers = {"x-shopify-topic": topic}
        _run(agent._handle_shopify_webhook({"id": 1, "topic": topic}))
        assert calls == [expected], f"topic {topic} routed to {calls}, expected {expected}"


def test_dispatch_ignores_catalog_topic_without_crash(monkeypatch, caplog):
    agent = _make_nexus()
    agent._current_webhook_headers = {"x-shopify-topic": "products/update"}
    # Must not raise and must not touch any handler -- consciously unbuilt.
    _run(agent._handle_shopify_webhook({"id": 1, "topic": "products/update"}))


# ===========================================================================
# shopify_push registrar + webhookSubscriptionDelete
# ===========================================================================


def test_registrar_default_topics_are_full_cutover_set(monkeypatch):
    from api.services import shopify_push

    # DARK -> no network; the plan lists every DEFAULT topic as missing.
    monkeypatch.setattr(shopify_push, "ims_shopify_writes_enabled", lambda: False)
    res = _run(shopify_push.register_webhooks(object(), "https://api.example.com"))
    assert res["mode"] == "SIMULATED"
    expected = {shopify_push._topic_enum(t) for t in shopify_push.CUTOVER_WEBHOOK_TOPICS}
    assert set(res["topics"]) == expected
    assert "REFUNDS_CREATE" in res["topics"]
    assert "FULFILLMENTS_CREATE" in res["topics"]
    assert "CUSTOMERS_UPDATE" in res["topics"]


def test_webhook_subscription_delete_builds_right_mutation(monkeypatch):
    from api.services import shopify_push

    monkeypatch.setattr(shopify_push, "ims_shopify_writes_enabled", lambda: True)
    monkeypatch.setattr(shopify_push, "shopify_dispatch_mode", lambda: "live")
    monkeypatch.setattr(
        shopify_push, "_load_integration_config",
        lambda db, t: {"shop_url": "x.myshopify.com", "access_token": "shpat_x"},
    )

    captured: Dict[str, Any] = {}

    async def fake_graphql(db, query, variables):
        captured["query"] = query
        captured["variables"] = variables
        return {
            "data": {
                "webhookSubscriptionDelete": {
                    "deletedWebhookSubscriptionId": variables["id"],
                    "userErrors": [],
                }
            }
        }

    monkeypatch.setattr(shopify_push, "_graphql", fake_graphql)

    gid = "gid://shopify/WebhookSubscription/42"
    res = _run(shopify_push.delete_webhook_subscription(object(), gid))
    assert res["ok"] is True
    assert res["deleted"] == gid
    assert "webhookSubscriptionDelete" in captured["query"]
    assert captured["variables"] == {"id": gid}


def test_webhook_subscription_delete_dark_no_network(monkeypatch):
    from api.services import shopify_push

    monkeypatch.setattr(shopify_push, "ims_shopify_writes_enabled", lambda: False)

    async def boom(db, query, variables):  # pragma: no cover
        raise AssertionError("DARK delete must not hit the network")

    monkeypatch.setattr(shopify_push, "_graphql", boom)
    res = _run(shopify_push.delete_webhook_subscription(object(), "gid://x/1"))
    assert res["mode"] == "SIMULATED" and res["ok"] is True and res["deleted"] is None
