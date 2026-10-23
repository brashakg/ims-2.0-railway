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

from tests.strict_fakes import StrictDB  # noqa: E402

pytestmark = pytest.mark.usefixtures("gst_numbers")


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
    db = StrictDB()
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


def _txn(amount, gateway):
    return {"kind": "refund", "status": "success", "amount": str(amount), "gateway": gateway}


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
    assert "Shopify already" in row["note"]
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
