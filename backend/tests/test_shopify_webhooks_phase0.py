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

    def delete_many(self, filter_=None):
        keep = [d for d in self.docs if not _match(d, filter_)]
        removed = len(self.docs) - len(keep)
        self.docs = keep
        return type("R", (), {"deleted_count": removed})()

    def delete_one(self, filter_=None):
        for i, d in enumerate(self.docs):
            if _match(d, filter_):
                del self.docs[i]
                return type("R", (), {"deleted_count": 1})()
        return type("R", (), {"deleted_count": 0})()


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
# refunds/create -- adversarial-review hardening (findings #1-#7)
# ===========================================================================


def _refund_payload_with_txn(refund_id=700001, shopify_order_id=5001, *, amount,
                             gateway=None, kind="refund", status="success"):
    """A refund payload carrying a `transactions` block (the amount Shopify
    actually refunded) so the handler can reconcile / detect a card refund."""
    p = _refund_payload(refund_id=refund_id, shopify_order_id=shopify_order_id)
    txn = {"kind": kind, "status": status, "amount": f"{amount}"}
    if gateway is not None:
        txn["gateway"] = gateway
    p["transactions"] = [txn]
    return p


def test_refund_amount_reconciliation_forces_discrepancy(wired, monkeypatch):
    """#2: a Rs 200 goodwill refund whose lines still claim the full Rs 1000 unit
    must NOT auto-post the Rs 1000 credit note -- it is routed to DISCREPANCY."""
    monkeypatch.setenv("SHOPIFY_REFUND_AUTO", "1")  # even with AUTO on
    _seed_online_order(wired)
    wired["customer_repo"].create(
        {"customer_id": "CUST-1", "name": "Ravi", "mobile": "9876543210", "store_credit": 0.0}
    )
    from api.services.shopify_refund import handle_shopify_refund

    # transactions say Rs 200 refunded; the computed credit note is Rs 1000.
    payload = _refund_payload_with_txn(amount=200, gateway="razorpay")
    res = handle_shopify_refund(wired["db"], payload, topic="refunds/create")

    assert res["status"] == "queued"
    assert res["queue_status"] == "DISCREPANCY"
    row = wired["review"].find_one({"shopify_refund_id": "700001"})
    assert row is not None and row["status"] == "DISCREPANCY"
    assert row["shopify_refunded_amount"] == 200.0
    # NOTHING posted on a mismatched amount.
    assert wired["ledger"].count_documents({}) == 0
    assert wired["returns"].count_documents({}) == 0


def test_refund_auto_claim_first_blocks_double_post(wired, monkeypatch):
    """#3: the claim-first returns-doc insert (unique index on shopify_refund_id)
    blocks a second post for the same refund id -> no double credit / restock."""
    monkeypatch.setenv("SHOPIFY_REFUND_AUTO", "1")
    order = _seed_online_order(wired)
    wired["customer_repo"].create(
        {"customer_id": "CUST-1", "name": "Ravi", "mobile": "9876543210", "store_credit": 0.0}
    )
    from api.services import shopify_refund as sr

    first = sr.handle_shopify_refund(wired["db"], _refund_payload(), topic="refunds/create")
    assert first["status"] == "credited"
    assert wired["returns"].count_documents({"shopify_refund_id": "700001"}) == 1
    assert wired["ledger"].count_documents({"customer_id": "CUST-1"}) == 1

    # Call the poster DIRECTLY (bypassing the pre-check) to prove the claim-first
    # unique index is what stops the double -- it returns 'duplicate', no 2nd row.
    credit_note = {
        "gross_refund": 1000.0,
        "net_refund": 1000.0,
        "gst_breakup": {"gross": 1000.0, "taxable": 952.38, "tax": 47.62, "gst_rate": 5.0},
        "lines": [],
    }
    dup = sr._post_credit_and_restock(
        wired["db"], refund_id="700001", order=order, return_lines=[],
        credit_note=credit_note, restock_store="BV-GANGA-01",
    )
    assert dup["status"] == "duplicate"
    assert wired["returns"].count_documents({"shopify_refund_id": "700001"}) == 1
    assert wired["ledger"].count_documents({"customer_id": "CUST-1"}) == 1


def test_refund_card_refund_does_not_bump_store_credit(wired, monkeypatch):
    """#5: a card/gateway refund books the CDNR ledger row (GST reversal) but must
    NOT also mint redeemable store credit (double benefit)."""
    monkeypatch.setenv("SHOPIFY_REFUND_AUTO", "1")
    _seed_online_order(wired)
    wired["customer_repo"].create(
        {"customer_id": "CUST-1", "name": "Ravi", "mobile": "9876543210", "store_credit": 0.0}
    )
    from api.services.shopify_refund import handle_shopify_refund

    # A gateway refund whose amount matches the computed gross (no discrepancy).
    payload = _refund_payload_with_txn(amount="1000.00", gateway="razorpay")
    res = handle_shopify_refund(wired["db"], payload, topic="refunds/create")

    assert res["status"] == "credited"
    assert res["settled_externally"] is True
    # The CDNR ledger row EXISTS (GST reversal still flows into GSTR-1)...
    rows = list(wired["ledger"].find({"customer_id": "CUST-1"}))
    assert len(rows) == 1
    assert rows[0]["type"] == "ISSUED"
    assert rows[0].get("settlement") == "EXTERNAL"
    assert rows[0].get("delta") == 0.0  # no balance movement
    # ...but the customer's redeemable store credit was NOT bumped.
    cust = wired["customers"].find_one({"customer_id": "CUST-1"})
    assert float(cust.get("store_credit") or 0.0) == 0.0


def test_refund_auto_cdnr_tax_and_billing_store(wired, monkeypatch):
    """#4: the AUTO credit-note ledger row carries the REAL tax (not 0) and lands
    under the ORIGINAL order's billing store (GSTIN), not the physical restock
    store."""
    monkeypatch.setenv("SHOPIFY_REFUND_AUTO", "1")
    _seed_online_order(wired)  # store_id=BV-ONLINE-01, fulfils at BV-GANGA-01
    wired["customer_repo"].create(
        {"customer_id": "CUST-1", "name": "Ravi", "mobile": "9876543210", "store_credit": 0.0}
    )
    from api.services.shopify_refund import handle_shopify_refund

    res = handle_shopify_refund(wired["db"], _refund_payload(), topic="refunds/create")
    assert res["status"] == "credited"
    row = wired["ledger"].find_one({"customer_id": "CUST-1"})
    assert row is not None
    assert row["tax"] == 47.62          # real output-tax reversal, not 0
    assert row["taxable"] == 952.38
    assert row["store_id"] == "BV-ONLINE-01"   # billing store, NOT BV-GANGA-01


def test_refund_unmatched_row_is_reprocessable(wired, monkeypatch):
    """#1: a refund that arrived before its order (UNMATCHED) must be reprocessable
    once the order is ingested -- the stale UNMATCHED row is superseded."""
    monkeypatch.delenv("SHOPIFY_REFUND_AUTO", raising=False)
    from api.services.shopify_refund import handle_shopify_refund

    # 1) Refund arrives first -> UNMATCHED (no order yet).
    first = handle_shopify_refund(wired["db"], _refund_payload(), topic="refunds/create")
    assert first["status"] == "order_not_found"
    assert wired["review"].find_one({"shopify_refund_id": "700001"})["status"] == "UNMATCHED"

    # 2) The order is ingested; a redelivery now reprocesses (not 'duplicate').
    _seed_online_order(wired)
    second = handle_shopify_refund(wired["db"], _refund_payload(), topic="refunds/create")
    assert second["status"] == "queued"
    assert second["queue_status"] == "PENDING"
    # The stale UNMATCHED row was superseded -> exactly one row, now PENDING.
    rows = list(wired["review"].find({"shopify_refund_id": "700001"}))
    assert len(rows) == 1 and rows[0]["status"] == "PENDING"


def test_refund_mixed_gst_rate_splits_per_line(wired, monkeypatch):
    """#7: a refund mixing an 18% frame + a 5% lens backs the tax out PER LINE
    (tax 900 + 300 = 1200), not one dominant rate over the whole gross."""
    monkeypatch.delenv("SHOPIFY_REFUND_AUTO", raising=False)
    frame = {
        "item_id": "it-frame", "product_id": "7001", "ims_product_id": "IMS-FRAME",
        "shopify_line_item_id": 9001, "sku": "FR-18", "product_name": "Frame",
        "quantity": 1, "gst_rate": 18.0, "taxable_value": 5000.0, "tax_amount": 900.0,
    }
    lens = {
        "item_id": "it-lens", "product_id": "7002", "ims_product_id": "IMS-LENS",
        "shopify_line_item_id": 9002, "sku": "LN-5", "product_name": "Lens",
        "quantity": 1, "gst_rate": 5.0, "taxable_value": 6000.0, "tax_amount": 300.0,
    }
    order = {
        "order_id": "ord-mix", "_id": "ord-mix", "shopify_order_id": "5002",
        "order_number": "ONL-5002", "channel": "ONLINE", "source": "shopify",
        "customer_id": "CUST-1", "customer_name": "Ravi", "store_id": "BV-ONLINE-01",
        "fulfillment_stores": ["BV-GANGA-01"], "items": [frame, lens],
        "grand_total": 12200.0, "status": "CONFIRMED",
    }
    wired["orders"].insert_one(order)

    payload = {
        "id": 700002, "order_id": 5002, "restock": True,
        "refund_line_items": [
            {"id": 1, "quantity": 1, "line_item_id": 9001, "restock_type": "return",
             "subtotal": 5000.0, "total_tax": 900.0, "line_item": {"id": 9001, "sku": "FR-18"}},
            {"id": 2, "quantity": 1, "line_item_id": 9002, "restock_type": "return",
             "subtotal": 6000.0, "total_tax": 300.0, "line_item": {"id": 9002, "sku": "LN-5"}},
        ],
    }
    from api.services.shopify_refund import handle_shopify_refund

    res = handle_shopify_refund(wired["db"], payload, topic="refunds/create")
    assert res["status"] == "queued"
    assert res["gross_refund"] == 12200.0
    gb = res["gst_breakup"]
    assert gb["tax"] == 1200.0          # 900 + 300, NOT a dominant-rate figure
    assert gb["taxable"] == 11000.0
    # The exact by-rate split is kept for the accountant.
    assert set(gb["by_rate"].keys()) == {"18.0", "5.0"}
    assert gb["by_rate"]["18.0"]["tax"] == 900.0
    assert gb["by_rate"]["5.0"]["tax"] == 300.0


def test_gst_breakup_lines_engine_mixed_rate():
    """#7 (pure engine): gst_breakup_lines backs tax out per line at each rate."""
    from api.services import returns_engine as engine

    lines = [
        {"return_qty": 1, "unit_price": 5900.0, "gst_rate": 18.0},  # taxable 5000, tax 900
        {"return_qty": 1, "unit_price": 6300.0, "gst_rate": 5.0},   # taxable 6000, tax 300
    ]
    view = engine.gst_breakup_lines(lines)
    assert view["gross"] == 12200.0
    assert view["taxable"] == 11000.0
    assert view["tax"] == 1200.0


def test_refund_auto_guest_order_diverts_to_queue_not_completed(wired, monkeypatch):
    """#6: an AUTO refund on a guest / customer-less order must NOT be silently
    marked COMPLETED with no credit note -- it diverts to the queue (NO_CUSTOMER)
    so an accountant can issue the credit note, and the returns doc is CREDIT_FAILED
    (keeps the refund id consumed for idempotency)."""
    monkeypatch.setenv("SHOPIFY_REFUND_AUTO", "1")
    _seed_online_order(wired, customer_id="")  # guest: no customer on the order
    from api.services.shopify_refund import handle_shopify_refund

    res = handle_shopify_refund(wired["db"], _refund_payload(), topic="refunds/create")
    assert res["status"] == "credit_failed"
    assert res["queue_status"] == "NO_CUSTOMER"
    # No credit note was issued (no customer) ...
    assert wired["ledger"].count_documents({}) == 0
    # ... the returns doc is CREDIT_FAILED (NOT COMPLETED), id stays consumed ...
    ret = wired["returns"].find_one({"shopify_refund_id": "700001"})
    assert ret is not None and ret["status"] == "CREDIT_FAILED"
    # ... and the accountant has a surface.
    row = wired["review"].find_one({"shopify_refund_id": "700001"})
    assert row is not None and row["status"] == "NO_CUSTOMER"


def test_refund_confirm_posts_from_stored_review_doc(wired, monkeypatch):
    """#1: the accountant CONFIRM path posts the credit note + restock from the
    STORED review row (post_from_review), rebuilding ReturnLine from the queued
    proposed_restock."""
    monkeypatch.delenv("SHOPIFY_REFUND_AUTO", raising=False)
    _seed_online_order(wired)
    wired["stock_repo"].units.append(
        {"stock_id": "stk-1", "product_id": "IMS-P-1", "store_id": "BV-GANGA-01",
         "order_id": "ord-abc", "status": "SOLD"}
    )
    wired["customer_repo"].create(
        {"customer_id": "CUST-1", "name": "Ravi", "mobile": "9876543210", "store_credit": 0.0}
    )
    from api.services.shopify_refund import handle_shopify_refund, post_from_review

    # Default queue -> a PENDING review row, nothing posted.
    queued = handle_shopify_refund(wired["db"], _refund_payload(), topic="refunds/create")
    assert queued["status"] == "queued" and queued["queue_status"] == "PENDING"
    assert wired["ledger"].count_documents({}) == 0
    assert wired["returns"].count_documents({}) == 0

    # The accountant confirms -> post from the STORED doc.
    review = wired["review"].find_one({"shopify_refund_id": "700001"})
    res = post_from_review(wired["db"], review)
    assert res["status"] == "credited"
    assert res["credit_note_issued"] is True
    assert res["restock_applied"] is True
    # Now a ledger row + a returns doc exist, and the SOLD unit was reactivated.
    assert wired["ledger"].count_documents({"customer_id": "CUST-1"}) == 1
    ret = wired["returns"].find_one({"shopify_refund_id": "700001"})
    assert ret is not None and ret["status"] == "COMPLETED"
    assert wired["stock_repo"].units[0]["status"] == "AVAILABLE"


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
    # Creds resolve via shopify_auth.resolve_shopify_credentials since #916.
    monkeypatch.setattr(
        shopify_push, "resolve_shopify_credentials",
        lambda db: {"shop_url": "x.myshopify.com", "access_token": "shpat_x",
                    "source": "vault"},
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
