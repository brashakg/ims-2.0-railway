"""
Store credit from an online refund review never exceeds money actually
collected and not already given back.

THE BUG: confirming a Shopify refund on an order the customer never paid (a
COD order cancelled before shipping) or whose money already went back by hand
(a "manual" refund) minted REDEEMABLE store credit for money IMS never held.
Nothing read the order's amount_paid; "no transactions" meant "not settled
elsewhere"; a manual gateway counted as "not external".

Every test asserts the LEDGER (credit_note_ledger delta + the customer's
balance), not just a status. Fakes: tests/strict_fakes.StrictDB (an unknown
Mongo operator raises instead of silently matching).
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, List

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test_x")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ["GST_PRICING_MODE"] = "inclusive"
os.environ["ONLINE_STORE_ID"] = "BV-ONLINE-01"

from tests.strict_fakes import StrictCollection, StrictDB  # noqa: E402

pytestmark = pytest.mark.usefixtures("gst_numbers")


class DuplicateKeyError(Exception):
    """Named like pymongo's, so shopify_refund._is_dup_key recognises it."""


class _UniqueRefundColl(StrictCollection):
    """The unique partial index on shopify_refund_id (uniq_shopify_refund_id)
    the real collections carry: a second doc with the same string refund id
    is refused, so a double post shows up as a test failure."""

    def insert_one(self, doc):
        rid = doc.get("shopify_refund_id")
        if isinstance(rid, str) and any(d.get("shopify_refund_id") == rid for d in self.docs):
            raise DuplicateKeyError("E11000 duplicate key uniq_shopify_refund_id")
        return super().insert_one(doc)


class _DB(StrictDB):
    def get_collection(self, name):
        if name in ("returns", "shopify_refund_review") and name not in self._collections:
            self._collections[name] = _UniqueRefundColl(name)
        return super().get_collection(name)


class _Conn:
    def __init__(self, db):
        self.db = db
        self.is_connected = True


class _Customers:
    """Customer repo over the strict `customers` collection."""

    def __init__(self, coll):
        self.coll = coll

    def find_by_id(self, cid):
        return self.coll.find_one({"customer_id": cid})

    def update(self, cid, data):
        return self.coll.update_one({"customer_id": cid}, {"$set": data})


@pytest.fixture
def env(monkeypatch):
    monkeypatch.delenv("SHOPIFY_REFUND_AUTO", raising=False)
    db = _DB()
    conn = _Conn(db)
    customers = _Customers(db.get_collection("customers"))

    import api.dependencies as deps
    import api.routers.returns as returns_router

    class _OrderRepo:
        def find_by_id(self, oid):
            return db.get_collection("orders").find_one({"order_id": oid})

    monkeypatch.setattr(returns_router, "get_customer_repository", lambda: customers)
    # No stock repository: these tests are about money. The restock leg stays
    # open (a task) exactly as it does when stock cannot be read.
    monkeypatch.setattr(returns_router, "get_stock_repository", lambda: None)
    monkeypatch.setattr(returns_router, "get_order_repository", lambda: _OrderRepo())
    monkeypatch.setattr(deps, "get_db", lambda: conn, raising=False)
    monkeypatch.setattr(deps, "get_task_repository", lambda: None, raising=False)
    monkeypatch.setattr(deps, "get_audit_repository", lambda: None, raising=False)
    db.get_collection("customers").insert_one(
        {"customer_id": "CUST-1", "name": "Ravi", "store_credit": 0.0})
    return db


def _order(db, **over):
    order = {
        "order_id": "ord-1",
        "shopify_order_id": "5001",
        "order_number": "ONL-5001",
        "channel": "ONLINE",
        "source": "shopify",
        "customer_id": "CUST-1",
        "customer_name": "Ravi",
        "store_id": "BV-ONLINE-01",
        "fulfillment_stores": ["BV-GANGA-01"],
        "items": [{
            "item_id": "it-1", "product_id": "7001", "ims_product_id": "IMS-P-1",
            "shopify_line_item_id": 9001, "sku": "RB-1", "product_name": "RB",
            "quantity": 1, "gst_rate": 5.0, "taxable_value": 952.38, "tax_amount": 47.62,
        }],
        "grand_total": 1000.0,
        "amount_paid": 1000.0,
        "payment_status": "PAID",
        "status": "CONFIRMED",
    }
    order.update(over)
    for k in [k for k, v in over.items() if v is _DROP]:
        order.pop(k)
    db.get_collection("orders").insert_one(order)
    return order


_DROP = object()


def _payload(refund_id="700001", txns: List[Dict[str, Any]] = None, restock_type="return"):
    p = {
        "id": refund_id,
        "order_id": "5001",
        "restock": True,
        "refund_line_items": [{
            "id": 1, "quantity": 1, "line_item_id": 9001, "restock_type": restock_type,
            "subtotal": 952.38, "total_tax": 47.62,
            "line_item": {"id": 9001, "sku": "RB-1", "quantity": 1},
        }],
    }
    if txns is not None:
        p["transactions"] = txns
    return p


def _txn(amount, gateway, status="success"):
    return {"kind": "refund", "status": status, "amount": str(amount), "gateway": gateway}


def _queue_and_confirm(db, payload):
    from api.services.shopify_refund import handle_shopify_refund, post_from_review

    queued = handle_shopify_refund(db, payload, topic="refunds/create")
    assert queued["status"] == "queued", queued
    row = db.get_collection("shopify_refund_review").find_one(
        {"shopify_refund_id": str(payload["id"])})
    return row, post_from_review(db, row)


def _ledger(db):
    return list(db.get_collection("credit_note_ledger").find({"customer_id": "CUST-1"}))


def _balance(db):
    from api.services.store_credit_ledger import compute_balance

    cust = db.get_collection("customers").find_one({"customer_id": "CUST-1"})
    return compute_balance(_ledger(db)), float(cust.get("store_credit") or 0)


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


def test_cod_never_paid_cancel_gives_no_credit(env):
    """A COD order cancelled before shipping: never paid, nothing to give back.
    The GST credit note still posts (the invoice is reversed) with ZERO
    redeemable credit, and the review says why in plain words."""
    _order(env, amount_paid=0.0, payment_status="UNPAID", balance_due=1000.0)
    row, res = _queue_and_confirm(env, _payload(txns=[], restock_type="cancel"))

    assert row["credit_note"]["store_credit"] == 0.0
    assert "never paid" in row["note"]
    assert "Shopify already refunded this money" not in row["note"]
    assert res["status"] == "credited"
    rows = _ledger(env)
    assert len(rows) == 1 and rows[0]["type"] == "ISSUED"
    assert rows[0]["gross_refund"] == 1000.0  # the GST reversal is whole
    assert rows[0]["delta"] == 0.0
    assert _balance(env) == (0.0, 0.0)


def test_cod_never_paid_is_not_auto_posted(env, monkeypatch):
    """With SHOPIFY_REFUND_AUTO on, an unpaid order's refund waits for a person
    -- it is never auto-posted, so no credit can mint with nobody looking."""
    monkeypatch.setenv("SHOPIFY_REFUND_AUTO", "1")
    _order(env, amount_paid=0.0, payment_status="UNPAID")
    from api.services.shopify_refund import handle_shopify_refund

    res = handle_shopify_refund(env, _payload(), topic="refunds/create")
    assert res["status"] == "queued"
    assert _ledger(env) == []
    assert _balance(env) == (0.0, 0.0)


def test_gateway_refund_gives_no_credit(env):
    """Paid, then refunded to the card on Shopify: the money is back on the
    card, so no store credit on top."""
    _order(env)
    row, res = _queue_and_confirm(env, _payload(txns=[_txn(1000, "razorpay")]))
    assert row["credit_note"]["store_credit"] == 0.0
    assert "Shopify records Rs 1000.00 already refunded" in row["note"]
    assert res["status"] == "credited"
    assert [r["delta"] for r in _ledger(env)] == [0.0]
    assert _balance(env) == (0.0, 0.0)


def test_manual_refund_gives_no_credit(env):
    """Paid, then refunded by hand (Shopify gateway "manual"): the money went
    back outside Shopify, so store credit would pay the customer twice."""
    _order(env)
    row, res = _queue_and_confirm(env, _payload(txns=[_txn(1000, "manual")]))
    assert row["credit_note"]["store_credit"] == 0.0
    assert res["status"] == "credited"
    assert [r["delta"] for r in _ledger(env)] == [0.0]
    assert _balance(env) == (0.0, 0.0)


def test_partially_paid_credit_capped_at_collected(env):
    """Only Rs 600 of Rs 1000 was ever collected: credit is Rs 600, not 1000."""
    _order(env, amount_paid=600.0, payment_status="PARTIAL", balance_due=400.0)
    row, res = _queue_and_confirm(env, _payload(txns=[]))
    assert row["credit_note"]["store_credit"] == 600.0
    assert "600.00" in row["note"]
    assert res["status"] == "credited"
    rows = _ledger(env)
    assert [r["delta"] for r in rows] == [600.0]
    assert rows[0]["gross_refund"] == 1000.0
    assert _balance(env) == (600.0, 600.0)


def test_paid_not_refunded_gets_full_credit(env):
    """Paid in full, Shopify sent no money back: the store credit IS the
    refund, the full Rs 1000."""
    _order(env)
    row, res = _queue_and_confirm(env, _payload(txns=[]))
    assert row["credit_note"]["store_credit"] == 1000.0
    assert res["status"] == "credited"
    assert [r["delta"] for r in _ledger(env)] == [1000.0]
    assert _balance(env) == (1000.0, 1000.0)


def test_paid_credit_less_what_was_already_given_back(env):
    """Rs 300 already went back at the counter (a COMPLETED return): this
    refund may credit only the Rs 700 still held."""
    _order(env)
    env.get_collection("returns").insert_one({
        "return_id": "RET-COUNTER", "order_id": "ord-1", "status": "COMPLETED",
        "net_refund": 300.0, "return_type": "RETURN"})
    row, res = _queue_and_confirm(env, _payload(txns=[]))
    assert row["credit_note"]["store_credit"] == 700.0
    assert [r["delta"] for r in _ledger(env)] == [700.0]
    assert _balance(env) == (700.0, 700.0)


def test_second_refund_after_earlier_credit_is_capped(env):
    """An earlier Shopify refund on this order already gave Rs 1000 store
    credit: a second refund of the same order gives nothing more."""
    _order(env)
    _queue_and_confirm(env, _payload(refund_id="700001", txns=[]))
    assert _balance(env) == (1000.0, 1000.0)
    row, res = _queue_and_confirm(env, _payload(refund_id="700002", txns=[]))
    assert row["credit_note"]["store_credit"] == 0.0
    assert [r["delta"] for r in _ledger(env)] == [1000.0, 0.0]
    assert _balance(env) == (1000.0, 1000.0)


def test_shopify_shows_order_fully_refunded_gives_no_credit(env):
    """Shopify's own financial status says the whole order went back."""
    _order(env, payment_status="REFUNDED")
    row, _ = _queue_and_confirm(env, _payload(txns=[]))
    assert row["credit_note"]["store_credit"] == 0.0
    assert [r["delta"] for r in _ledger(env)] == [0.0]


def test_unknown_paid_amount_gives_nothing(env):
    """No amount_paid on the order: IMS cannot tell what was collected, so it
    mints nothing and says so (fail closed)."""
    _order(env, amount_paid=_DROP)
    row, res = _queue_and_confirm(env, _payload(txns=[]))
    assert row["credit_note"]["store_credit"] == 0.0
    assert "cannot tell" in row["note"]
    assert [r["delta"] for r in _ledger(env)] == [0.0]
    assert _balance(env) == (0.0, 0.0)


def test_unreadable_order_at_confirm_mints_nothing(env, monkeypatch):
    """The order cannot be read when the accountant confirms: nothing is
    posted and the row stays open for a retry (no credit on a guess)."""
    _order(env)
    from api.services.shopify_refund import handle_shopify_refund, post_from_review

    handle_shopify_refund(env, _payload(txns=[]), topic="refunds/create")
    row = env.get_collection("shopify_refund_review").find_one({"shopify_refund_id": "700001"})
    orders = env.get_collection("orders")

    def _boom(*a, **k):
        raise RuntimeError("mongo blip")

    monkeypatch.setattr(orders, "find_one", _boom)
    res = post_from_review(env, row)
    assert res["status"] == "credit_failed"
    assert _ledger(env) == []
    assert _balance(env) == (0.0, 0.0)


def test_legacy_review_row_without_money_check_gives_nothing(env):
    """A row queued before this fix carries no record of what Shopify sent
    back (a manual refund looked the same as none): confirm it with zero
    credit rather than guess."""
    _order(env)
    from api.services.shopify_refund import handle_shopify_refund, post_from_review

    handle_shopify_refund(env, _payload(txns=[]), topic="refunds/create")
    row = env.get_collection("shopify_refund_review").find_one({"shopify_refund_id": "700001"})
    row["credit_note"].pop("money_back")
    res = post_from_review(env, row)
    assert res["status"] == "credited"
    assert [r["delta"] for r in _ledger(env)] == [0.0]


def test_goods_out_note_only_claims_shopify_refund_when_it_did(env):
    """The reviewer screen says "Shopify already refunded this money" only
    when a refund transaction proves it."""
    _order(env, amount_paid=0.0, payment_status="UNPAID", status="SHIPPED")
    from api.services.shopify_refund import handle_shopify_refund

    handle_shopify_refund(env, _payload(txns=[]), topic="refunds/create")
    row = env.get_collection("shopify_refund_review").find_one({"shopify_refund_id": "700001"})
    assert "Shopify already refunded this money" not in row["note"]
    assert "never paid" in row["note"]

    env.get_collection("orders").update_one(
        {"order_id": "ord-1"}, {"$set": {"amount_paid": 1000.0, "payment_status": "PAID"}})
    handle_shopify_refund(env, _payload(refund_id="700009", txns=[_txn(1000, "razorpay")]),
                          topic="refunds/create")
    row = env.get_collection("shopify_refund_review").find_one({"shopify_refund_id": "700009"})
    assert "Shopify already refunded this money" in row["note"]


# ---------------------------------------------------------------------------
# The read-only audit script
# ---------------------------------------------------------------------------


def test_audit_script_flags_credit_on_unpaid_and_manual_refunds():
    from scripts.audit_unpaid_refund_credit import find_suspect_credits

    db = StrictDB()
    db.seed("orders", [
        {"order_id": "o-unpaid", "amount_paid": 0.0, "payment_status": "UNPAID"},
        {"order_id": "o-paid", "amount_paid": 1000.0, "payment_status": "PAID"},
        {"order_id": "o-manual", "amount_paid": 1000.0, "payment_status": "REFUNDED"},
    ])
    db.seed("returns", [
        {"return_id": "R1", "order_id": "o-unpaid", "shopify_refund_id": "1"},
        {"return_id": "R2", "order_id": "o-paid", "shopify_refund_id": "2"},
        {"return_id": "R3", "order_id": "o-manual", "shopify_refund_id": "3",
         "shopify_refunded_amount": 1000.0},
        {"return_id": "R4", "order_id": "o-unpaid", "shopify_refund_id": "4"},
    ])
    db.seed("credit_note_ledger", [
        {"ref": "R1", "type": "ISSUED", "delta": 1000.0, "customer_id": "C1"},
        {"ref": "R2", "type": "ISSUED", "delta": 1000.0, "customer_id": "C2"},
        {"ref": "R3", "type": "ISSUED", "delta": 1000.0, "customer_id": "C3"},
        {"ref": "R4", "type": "ISSUED", "delta": 0.0, "customer_id": "C4"},
        {"ref": "COUNTER-1", "type": "ISSUED", "delta": 500.0, "customer_id": "C5"},
    ])
    found = {r["return_id"]: r for r in find_suspect_credits(db)}
    assert set(found) == {"R1", "R3"}
    assert found["R1"]["credit"] == 1000.0
    assert "never paid" in found["R1"]["why"]
    assert "Shopify" in found["R3"]["why"]


# ---------------------------------------------------------------------------
# Review round 1 (#1261)
# ---------------------------------------------------------------------------


def _money_only(refund_id, amount, gateway="razorpay"):
    """A Shopify refund that moves money with no item lines (a goodwill
    custom amount, a shipping-only refund)."""
    return {"id": refund_id, "order_id": "5001", "refund_line_items": [],
            "transactions": [_txn(amount, gateway)]}


def test_money_only_refund_is_recorded_and_counted(env):
    """M1: Rs 400 went back to the card with no items, then an item refund
    with no money: the credit is the Rs 600 still held, not Rs 1000."""
    _order(env)
    from api.services.shopify_refund import handle_shopify_refund

    res = handle_shopify_refund(env, _money_only("700050", 400), topic="refunds/create")
    assert res["status"] == "queued" and res["queue_status"] == "MONEY_ONLY"
    row = env.get_collection("shopify_refund_review").find_one({"shopify_refund_id": "700050"})
    assert row["order_id"] == "ord-1" and row["credit_note"]["money_back"] == 400.0
    assert _ledger(env) == []
    row, _ = _queue_and_confirm(env, _payload(txns=[]))
    assert row["credit_note"]["store_credit"] == 600.0
    assert [r["delta"] for r in _ledger(env)] == [600.0]
    assert _balance(env) == (600.0, 600.0)


def test_money_only_refund_replay_is_a_duplicate(env):
    _order(env)
    from api.services.shopify_refund import handle_shopify_refund

    handle_shopify_refund(env, _money_only("700050", 400), topic="refunds/create")
    again = handle_shopify_refund(env, _money_only("700050", 400), topic="refunds/create")
    assert again["status"] == "duplicate"
    assert env.get_collection("shopify_refund_review").count_documents({}) == 1


def test_imported_history_refund_counts_as_given_back(env):
    """Minor 1: an order-history import's RET-HIST doc (settled_externally,
    no shopify_refunded_amount) counts its net refund as given back."""
    _order(env)
    env.get_collection("returns").insert_one({
        "return_id": "RET-HIST-1", "shopify_refund_id": "1", "order_id": "ord-1",
        "net_refund": 400.0, "settled_externally": True, "status": "COMPLETED"})
    row, _ = _queue_and_confirm(env, _payload(txns=[]))
    assert row["credit_note"]["store_credit"] == 600.0
    assert [r["delta"] for r in _ledger(env)] == [600.0]


def test_partial_money_back_credits_the_rest(env):
    """Minor 2 (decision): Rs 400 back on the card for a Rs 1000 refund ->
    credit is the Rs 600 rest, capped by what is still held."""
    _order(env)
    row, res = _queue_and_confirm(env, _payload(txns=[_txn(400, "razorpay")]))
    assert row["status"] == "DISCREPANCY"
    assert row["credit_note"]["store_credit"] == 600.0
    assert "Shopify records Rs 400.00 already refunded" in row["note"]
    assert res["status"] == "credited"
    assert [r["delta"] for r in _ledger(env)] == [600.0]
    assert _balance(env) == (600.0, 600.0)


def test_partial_money_back_still_capped_by_held(env):
    _order(env, amount_paid=500.0, payment_status="PARTIAL")
    row, _ = _queue_and_confirm(env, _payload(txns=[_txn(400, "razorpay")]))
    assert row["credit_note"]["store_credit"] == 100.0
    assert [r["delta"] for r in _ledger(env)] == [100.0]


def test_pending_refund_blocks_credit_and_is_worded_pending(env):
    """Minor 6: a pending refund transaction is not called refunded, but it
    still blocks credit (the money is on its way back)."""
    _order(env)
    row, _ = _queue_and_confirm(env, _payload(txns=[_txn(1000, "razorpay", status="pending")]))
    assert row["credit_note"]["store_credit"] == 0.0
    assert "Rs 1000.00 pending" in row["note"]
    assert "already refunded" not in row["note"]
    assert [r["delta"] for r in _ledger(env)] == [0.0]


def test_auto_does_not_post_when_allowance_moved(env, monkeypatch):
    """Minor 3: the credit shown at queue time changed by the post (a counter
    return landed between): AUTO books nothing and queues it for a person."""
    monkeypatch.setenv("SHOPIFY_REFUND_AUTO", "1")
    _order(env)
    from api.services import shopify_refund as sr

    real = sr._store_credit_allowance
    calls = []

    def _moving(*a, **k):
        out = real(*a, **k)
        if not calls:
            env.get_collection("returns").insert_one({
                "return_id": "RET-C", "order_id": "ord-1", "status": "COMPLETED",
                "net_refund": 300.0})
        calls.append(out)
        return out

    monkeypatch.setattr(sr, "_store_credit_allowance", _moving)
    res = sr.handle_shopify_refund(env, _payload(txns=[]), topic="refunds/create")
    assert res["status"] == "queued"
    assert _ledger(env) == []
    row = env.get_collection("shopify_refund_review").find_one({"shopify_refund_id": "700001"})
    assert row["status"] == "PENDING"


def test_auto_full_credit_still_auto_posts(env, monkeypatch):
    monkeypatch.setenv("SHOPIFY_REFUND_AUTO", "1")
    _order(env)
    from api.services.shopify_refund import handle_shopify_refund

    res = handle_shopify_refund(env, _payload(txns=[]), topic="refunds/create")
    assert res["status"] == "credited"
    assert [r["delta"] for r in _ledger(env)] == [1000.0]
    assert _balance(env) == (1000.0, 1000.0)


def test_confirm_stamps_booked_credit_on_the_row(env):
    """Minor 4: after Confirm the row shows what was booked, not the queue
    time proposal."""
    import asyncio

    _order(env)
    from api.routers.online_store_refund_reviews import confirm_refund_review
    from api.services.shopify_refund import handle_shopify_refund

    handle_shopify_refund(env, _payload(txns=[]), topic="refunds/create")
    reviews = env.get_collection("shopify_refund_review")
    review_id = reviews.find_one({"shopify_refund_id": "700001"})["review_id"]
    # Rs 300 went back at the counter after the row was queued.
    env.get_collection("returns").insert_one({
        "return_id": "RET-C", "order_id": "ord-1", "status": "COMPLETED", "net_refund": 300.0})
    asyncio.run(confirm_refund_review(review_id, current_user={
        "user_id": "acc-1", "roles": ["ACCOUNTANT"]}))
    row = reviews.find_one({"review_id": review_id})
    assert row["status"] == "POSTED"
    assert row["credit_note"]["store_credit"] == 700.0
    assert "700.00" in row["note"]
    assert [r["delta"] for r in _ledger(env)] == [700.0]


def test_never_paid_credit_is_not_labelled_external(env):
    """Minor 7: zero credit because nothing was collected is not a gateway
    settlement."""
    _order(env, amount_paid=0.0, payment_status="UNPAID")
    _queue_and_confirm(env, _payload(txns=[]))
    assert [r.get("settlement") for r in _ledger(env)] == ["NOT_COLLECTED"]


def test_gateway_credit_is_labelled_external(env):
    _order(env)
    _queue_and_confirm(env, _payload(txns=[_txn(1000, "razorpay")]))
    assert [r.get("settlement") for r in _ledger(env)] == ["EXTERNAL"]


def test_hand_adjusted_credit_counts_as_given_back(env):
    """Minor 8: a store-credit adjustment made by hand for this order counts."""
    _order(env)
    env.get_collection("credit_note_ledger").insert_one({
        "customer_id": "CUST-1", "type": "ADJUSTED", "amount": 200.0, "delta": 200.0,
        "ref": "ONL-5001", "entry_id": "adj-1"})
    row, _ = _queue_and_confirm(env, _payload(txns=[]))
    assert row["credit_note"]["store_credit"] == 800.0
    assert [r["delta"] for r in _ledger(env)] == [200.0, 800.0]


def test_same_refund_posted_twice_books_once(env):
    """The unique claim index (enforced by the fake) stops a second post."""
    _order(env)
    from api.services.shopify_refund import post_from_review

    row, first = _queue_and_confirm(env, _payload(txns=[]))
    again = post_from_review(env, row)
    assert first["status"] == "credited" and again["status"] == "duplicate"
    assert [r["delta"] for r in _ledger(env)] == [1000.0]


def test_two_refunds_confirmed_at_once_never_exceed_paid(env, monkeypatch):
    """Two refunds of one order confirmed at the same moment: the second
    cannot see the credit the first is booking, so it must not book on a
    stale answer. Rs 1000 paid on a Rs 2000 order, two Rs 1000 refunds."""
    _order(env, grand_total=2000.0, amount_paid=1000.0, payment_status="PARTIAL",
           items=[{"item_id": "it-1", "product_id": "7001", "ims_product_id": "IMS-P-1",
                   "shopify_line_item_id": 9001, "sku": "RB-1", "product_name": "RB",
                   "quantity": 2, "gst_rate": 5.0, "taxable_value": 1904.76,
                   "tax_amount": 95.24}])
    from api.services import shopify_refund as sr

    sr.handle_shopify_refund(env, _payload(refund_id="700001", txns=[]), topic="refunds/create")
    sr.handle_shopify_refund(env, _payload(refund_id="700002", txns=[]), topic="refunds/create")
    reviews = env.get_collection("shopify_refund_review")
    row_a = reviews.find_one({"shopify_refund_id": "700001"})
    row_b = reviews.find_one({"shopify_refund_id": "700002"})
    assert row_a["credit_note"]["store_credit"] == row_b["credit_note"]["store_credit"] == 1000.0

    real_allowance = sr._store_credit_allowance
    inner = []
    raced = []

    def _racing_allowance(*a, **k):
        out = real_allowance(*a, **k)
        if not raced:  # A has its answer and has not booked yet: B confirms now
            raced.append(True)
            inner.append(sr.post_from_review(env, row_b))
        return out

    monkeypatch.setattr(sr, "_store_credit_allowance", _racing_allowance)
    res_a = sr.post_from_review(env, row_a)
    # The amounts first: B never booked on A's stale "Rs 1000 still held".
    assert [r["delta"] for r in _ledger(env)] == [1000.0]
    assert res_a["status"] == "credited"
    assert inner[0]["status"] == "credit_failed"  # busy: try again
    monkeypatch.setattr(sr, "_store_credit_allowance", real_allowance)
    res_b = sr.post_from_review(env, row_b)
    assert res_b["status"] == "credited"
    assert sorted(r["delta"] for r in _ledger(env)) == [0.0, 1000.0]
    assert _balance(env) == (1000.0, 1000.0)


def test_audit_script_counts_review_rows_from_before_the_fix():
    from scripts.audit_unpaid_refund_credit import legacy_review_rows

    db = StrictDB()
    db.seed("shopify_refund_review", [
        {"review_id": "a", "status": "PENDING", "credit_note": {"gross_refund": 1.0}},
        {"review_id": "b", "status": "PENDING", "credit_note": {"money_back": 0.0}},
        {"review_id": "c", "status": "POSTED", "credit_note": {"gross_refund": 1.0}},
        {"review_id": "d", "status": "DISCREPANCY", "credit_note": {"gross_refund": 1.0}},
    ])
    assert legacy_review_rows(db) == 2


# ---------------------------------------------------------------------------
# Review round 2 (#1261)
# ---------------------------------------------------------------------------


def _two_unit_order(env, **over):
    _order(env, grand_total=2000.0, amount_paid=1000.0, payment_status="PARTIAL",
           items=[{"item_id": "it-1", "product_id": "7001", "ims_product_id": "IMS-P-1",
                   "shopify_line_item_id": 9001, "sku": "RB-1", "product_name": "RB",
                   "quantity": 2, "gst_rate": 5.0, "taxable_value": 1904.76,
                   "tax_amount": 95.24}], **over)


def test_pre_fix_card_refund_confirmed_later_still_counts(env):
    """Round 2 item 1: a card refund queued BEFORE the fix (no money_back on
    its row) and confirmed after it finalizes with money_back None -- its real
    Rs 1000 card refund must still count, so a second refund gets nothing."""
    _two_unit_order(env)
    from api.services.shopify_refund import handle_shopify_refund, post_from_review

    handle_shopify_refund(env, _payload(refund_id="700001", txns=[_txn(1000, "razorpay")]),
                          topic="refunds/create")
    reviews = env.get_collection("shopify_refund_review")
    old = reviews.find_one({"shopify_refund_id": "700001"})
    old["credit_note"].pop("money_back")  # queued before the fix
    reviews.update_one({"shopify_refund_id": "700001"}, {"$set": {"credit_note": old["credit_note"]}})
    post_from_review(env, old)
    ret = env.get_collection("returns").find_one({"shopify_refund_id": "700001"})
    assert ret["money_back"] is None
    row, _ = _queue_and_confirm(env, _payload(refund_id="700002", txns=[]))
    assert row["credit_note"]["store_credit"] == 0.0
    assert [r["delta"] for r in _ledger(env)] == [0.0, 0.0]


def test_counter_credit_in_flight_is_seen(env, monkeypatch):
    """Round 2 item 2: a counter credit whose returns doc is not written yet
    is seen through its ledger row (stamped with the order)."""
    _order(env)
    import api.routers.returns as returns_router

    returns_router._issue_store_credit(
        "CUST-1", 400.0, reason="counter return", ref="RET-INFLIGHT",
        current_user={"user_id": "u1", "active_store_id": "BV-GANGA-01"},
        order_id="ord-1")
    row, _ = _queue_and_confirm(env, _payload(txns=[]))
    assert row["credit_note"]["store_credit"] == 600.0


def test_counter_return_door_stamps_the_order_on_its_ledger_row():
    """Round 2 item 2: the counter return door passes order_id, so its credit
    is tied to the order before its returns doc lands."""
    import inspect
    import api.routers.returns as returns_router

    src = inspect.getsource(returns_router)
    call = src[src.index("        entry = _issue_store_credit(\n            customer_id,\n            amount,"):]
    call = call[:call.index("\n        )\n")]
    assert "order_id=resolved_order_id" in call


def test_hand_credit_ref_is_normalised(env):
    """Round 2 item 3: a hand credit whose free-text ref names the order
    ('#onl-5001 goodwill') is tied to it."""
    _order(env)
    env.get_collection("credit_note_ledger").insert_one({
        "customer_id": "CUST-1", "type": "ISSUED", "amount": 250.0, "delta": 250.0,
        "ref": " #onl-5001 goodwill ", "entry_id": "hand-1"})
    row, _ = _queue_and_confirm(env, _payload(txns=[]))
    assert row["credit_note"]["store_credit"] == 750.0


def test_untied_hand_credit_is_named_in_the_note(env):
    """Round 2 item 3: a hand credit on the customer that names no order is
    not subtracted, but the reviewer is told it exists."""
    _order(env)
    env.get_collection("credit_note_ledger").insert_one({
        "customer_id": "CUST-1", "type": "ADJUSTED", "amount": 150.0, "delta": 150.0,
        "ref": "sorry for the delay", "entry_id": "hand-2"})
    row, _ = _queue_and_confirm(env, _payload(txns=[]))
    assert row["credit_note"]["store_credit"] == 1000.0
    assert "Rs 150.00" in row["note"] and "by hand" in row["note"]


def test_unexplained_shopify_refund_is_not_auto_posted(env, monkeypatch):
    """Round 2 item 4: Shopify says part of the order was refunded but IMS has
    no record of that money (a money-only refund from before the fix): AUTO
    does not post it, and the reviewer is told why."""
    monkeypatch.setenv("SHOPIFY_REFUND_AUTO", "1")
    _order(env, payment_status="PARTIAL_REFUND")
    from api.services.shopify_refund import handle_shopify_refund

    res = handle_shopify_refund(env, _payload(txns=[]), topic="refunds/create")
    assert res["status"] == "queued"
    assert _ledger(env) == []
    row = env.get_collection("shopify_refund_review").find_one({"shopify_refund_id": "700001"})
    assert "IMS has no record" in row["note"]


def test_audit_script_lists_unexplained_shopify_refunds():
    from scripts.audit_unpaid_refund_credit import unexplained_refund_orders

    db = StrictDB()
    db.seed("orders", [
        {"order_id": "o1", "order_number": "ONL-1", "payment_status": "PARTIAL_REFUND"},
        {"order_id": "o2", "order_number": "ONL-2", "payment_status": "REFUNDED"},
        {"order_id": "o3", "order_number": "ONL-3", "payment_status": "PAID"},
    ])
    db.seed("returns", [{"return_id": "R2", "order_id": "o2", "shopify_refund_id": "9"}])
    assert [o["order_id"] for o in unexplained_refund_orders(db)] == ["o1"]


def test_money_only_after_full_credit_flags_over_refund(env):
    """Round 2 item 5: Rs 1000 credit, then Rs 200 goodwill to the card on a
    Rs 1000 order: the money-only row says more went back than was paid."""
    _order(env)
    _queue_and_confirm(env, _payload(txns=[]))
    from api.services.shopify_refund import handle_shopify_refund

    handle_shopify_refund(env, _money_only("700050", 200), topic="refunds/create")
    row = env.get_collection("shopify_refund_review").find_one({"shopify_refund_id": "700050"})
    assert "exceeds what was paid (Rs 1000.00)" in row["note"]


def test_auto_lock_busy_note_does_not_claim_a_restock(env, monkeypatch):
    """Round 2 item 6: another post holds the order's credit lock: the AUTO
    refund books nothing and its note says so (no 'restock done')."""
    monkeypatch.setenv("SHOPIFY_REFUND_AUTO", "1")
    _order(env)
    from datetime import datetime, timezone

    env.get_collection("orders").update_one(
        {"order_id": "ord-1"},
        {"$set": {"refund_credit_lock": datetime.now(timezone.utc).isoformat() + ":other"}})
    from api.services.shopify_refund import handle_shopify_refund

    res = handle_shopify_refund(env, _payload(txns=[]), topic="refunds/create")
    assert res["status"] == "credit_failed"
    assert _ledger(env) == []
    row = env.get_collection("shopify_refund_review").find_one({"shopify_refund_id": "700001"})
    assert row["status"] == "CREDIT_FAILED"
    assert "restock done" not in row["note"].lower()
    assert "nothing was booked" in row["note"]
