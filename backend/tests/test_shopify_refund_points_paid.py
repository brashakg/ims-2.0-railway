"""
IMS 2.0 - a Shopify refund gives the points-paid part back AS POINTS (#1397, review 11-09 item 2).
====================================================================================================
An online order can carry a LOYALTY tender (POST /orders/{id}/payments does not
refuse one). Owner rulings 2026-11-08 / 2026-11-09: the part of a bill paid with
points comes back as points, never as cash or store credit, in proportion and
cumulative per bill. The counter return door applies that rule
(returns._points_paid_share + loyalty.reverse_for_return); the Shopify refund
door did not: services/shopify_refund._store_credit_allowance capped store
credit at refundable_ceiling (amount_paid, which includes the LOYALTY tender),
so the points-paid part went out as REDEEMABLE store credit and no point came
back.

Rs 1,000 online order paid Rs 900 COD + Rs 100 in points. Driven through the
REAL webhook handler + accountant confirm over the strict fakes of
test_refund_review_unpaid_credit and the REAL loyalty engine (mongomock). The
order-wide earn claw is untouched on this door (restore only).

No emoji (Windows cp1252).
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.test_refund_review_unpaid_credit import (  # noqa: E402,F401
    _balance,
    _ledger,
    _order,
    _payload,
    _queue_and_confirm,
    _txn,
    env,
)

pytestmark = pytest.mark.usefixtures("gst_numbers")

PAYS = [{"method": "CASH", "amount": 900.0}, {"method": "LOYALTY", "amount": 100.0}]


@pytest.fixture
def points(env, monkeypatch):
    """The real loyalty engine over mongomock: the bill redeemed 100 points
    (Rs 100) and earned 50. Returns books() -> (balance, lifetime_redeemed,
    lifetime_earned)."""
    import mongomock
    from api.routers import loyalty as L
    from database.repositories.loyalty_repository import (
        LoyaltyAccountRepository,
        LoyaltyTransactionRepository,
    )

    db = mongomock.MongoClient()["shopify_points"]
    accounts = LoyaltyAccountRepository(db.get_collection("loyalty_accounts"))
    txns = LoyaltyTransactionRepository(db.get_collection("loyalty_transactions"))
    accounts.find_or_create("CUST-1")
    accounts.adjust_balance("CUST-1", delta_points=50, delta_lifetime_earned=50, delta_lifetime_redeemed=100)
    db.loyalty_transactions.insert_one({"txn_id": "R-1", "customer_id": "CUST-1", "type": "REDEEM",
                                        "points": 100, "rupee_value": 100.0, "order_id": "ord-1"})
    db.loyalty_transactions.insert_one({"txn_id": "E-1", "customer_id": "CUST-1", "type": "EARN",
                                        "points": 50, "order_id": "ord-1"})
    monkeypatch.setattr(L, "get_loyalty_transaction_repository", lambda: txns)
    monkeypatch.setattr(L, "get_loyalty_account_repository", lambda: accounts)
    monkeypatch.setattr(L, "get_order_repository", lambda: None, raising=False)

    def books():
        a = db.loyalty_accounts.find_one({"customer_id": "CUST-1"})
        return int(a["balance_points"]), int(a.get("lifetime_redeemed") or 0), int(a.get("lifetime_earned") or 0)

    books.db = db
    return books


def test_the_points_paid_part_comes_back_as_points_not_store_credit(env, points):
    _order(env, payments=PAYS)
    row, res = _queue_and_confirm(env, _payload(txns=[]))
    assert row["credit_note"]["store_credit"] == 900.0  # was 1000: the points part as store credit
    assert "loyalty points" in row["note"]
    assert res["status"] == "credited"
    rows = _ledger(env)
    assert [r["delta"] for r in rows] == [900.0]
    assert rows[0]["gross_refund"] == 1000.0  # the GST reversal stays whole
    assert _balance(env) == (900.0, 900.0)
    # 100 points back (50 earned untouched: restore only), redemption undone.
    assert points() == (150, 0, 50)
    doc = env.get_collection("returns").find_one({"shopify_refund_id": "700001"})
    assert doc["points_paid_share"] == 100.0 and doc["status"] == "COMPLETED"


def test_a_replayed_confirm_gives_nothing_back_twice(env, points):
    from api.services.shopify_refund import post_from_review

    _order(env, payments=PAYS)
    row, _res = _queue_and_confirm(env, _payload(txns=[]))
    again = post_from_review(env, row)
    assert again["status"] != "credited" or again.get("points_restored") == 0, again
    assert [r["delta"] for r in _ledger(env)] == [900.0]
    assert points() == (150, 0, 50)


def test_a_later_counter_return_or_cancel_never_gives_the_points_back_again(env, points):
    """The Shopify door's restore row counts toward the bill's redemption: a
    cancel afterwards restores nothing more (it claws the earn only)."""
    from api.routers import loyalty as L

    _order(env, payments=PAYS)
    _queue_and_confirm(env, _payload(txns=[]))
    assert points()[0] == 150
    out = L.reverse_for_cancel("ord-1", "CUST-1")
    assert out["ok"] is True
    assert points() == (100, 0, 0)  # earn 50 clawed; the 100 points not restored twice


def test_money_shopify_sent_back_covers_the_points_part_too(env, points):
    """Shopify paid the whole Rs 1,000 back by hand: nothing more is owed --
    neither store credit nor points."""
    _order(env, payments=PAYS)
    row, res = _queue_and_confirm(env, _payload(txns=[_txn(1000, "manual")]))
    assert row["credit_note"]["store_credit"] == 0.0
    assert [r["delta"] for r in _ledger(env)] == [0.0]
    assert points() == (50, 100, 50)


def test_a_failed_restore_stays_flagged_and_the_sweep_finishes_it(env, points, monkeypatch):
    from api.routers import loyalty as L

    _order(env, payments=PAYS)
    real = L._apply_reversal_balance
    monkeypatch.setattr(L, "_apply_reversal_balance", lambda *a, **k: ("failed", None))
    row, res = _queue_and_confirm(env, _payload(txns=[]))
    assert res["status"] == "credited" and points()[0] == 50
    doc = env.get_collection("returns").find_one({"shopify_refund_id": "700001"})
    assert doc["loyalty_reversal_failed"] is True
    monkeypatch.setattr(L, "_apply_reversal_balance", real)

    class _Orders:  # the scheduled sweep reads bills + returns through the order repo
        collection = env.get_collection("orders")

    _Orders.collection.database = env
    monkeypatch.setattr(L, "get_order_repository", lambda: _Orders())
    L.reconcile_loyalty_reversals()
    assert points() == (150, 0, 50)  # restore only: the 50 earned is not clawed
    assert env.get_collection("returns").find_one(
        {"shopify_refund_id": "700001"})["loyalty_reversal_failed"] is False
    L.reconcile_loyalty_reversals()
    assert points() == (150, 0, 50)


def test_positive_control_no_points_tender_is_unchanged(env, points):
    _order(env)
    row, res = _queue_and_confirm(env, _payload(txns=[]))
    assert row["credit_note"]["store_credit"] == 1000.0
    assert [r["delta"] for r in _ledger(env)] == [1000.0]
    assert points() == (50, 100, 50)



def test_two_half_refunds_give_back_half_the_points_each(env, points):
    """Cumulative per bill: each half refund gives back 50 points and Rs 450."""
    _order(env, payments=PAYS, items=[{
        "item_id": "it-1", "product_id": "7001", "ims_product_id": "IMS-P-1",
        "shopify_line_item_id": 9001, "sku": "RB-1", "product_name": "RB",
        "quantity": 2, "gst_rate": 5.0, "taxable_value": 952.38, "tax_amount": 47.62}])
    for rid in ("700001", "700002"):
        p = _payload(refund_id=rid, txns=[])
        p["refund_line_items"][0].update({"quantity": 1, "subtotal": 476.19, "total_tax": 23.81})
        row, res = _queue_and_confirm(env, p)
        assert res["status"] == "credited", res
        assert row["credit_note"]["store_credit"] == 450.0, row["credit_note"]
    assert sorted(r["delta"] for r in _ledger(env)) == [450.0, 450.0]
    assert points() == (150, 0, 50)


def test_a_counter_return_after_a_shopify_half_refund_gives_back_its_full_share(env, points):
    """Review 11-09 (round 2): the first counter return after a Shopify refund
    priced its points at the bill's rate on the REDUCED redemption (100 - 50
    already given back, over the full Rs 100), so Rs 50 held back from the cash
    gave back only 25 points. One rule both sides: Rs 50 -> 50 points."""
    from api.routers import loyalty as L

    _order(env, payments=PAYS, items=[{
        "item_id": "it-1", "product_id": "7001", "ims_product_id": "IMS-P-1",
        "shopify_line_item_id": 9001, "sku": "RB-1", "product_name": "RB",
        "quantity": 2, "gst_rate": 5.0, "taxable_value": 952.38, "tax_amount": 47.62}])
    p = _payload(txns=[])
    p["refund_line_items"][0].update({"quantity": 1, "subtotal": 476.19, "total_tax": 23.81})
    _row, res = _queue_and_confirm(env, p)
    assert res["status"] == "credited", res
    assert points() == (100, 50, 50)
    # Counter return of the other unit: _points_paid_share holds back Rs 50.
    assert L.points_for_rupees(50.0, *L.bill_redemption("ord-1")) == 50
    out = L.reverse_for_return("RET-2", "ord-1", "CUST-1", points_paid_share=50.0)
    assert out["ok"] is True, out
    assert points() == (100, 0, 0)  # 50 restored, the 50 earned clawed


@pytest.mark.parametrize("shopify_pts,share,restored", [(30, 20.0, 20), (50, 50.0, 50), (0, 20.0, 20)])
def test_first_counter_return_prices_at_the_bills_rate_not_the_reduced_one(points, shopify_pts, share, restored):
    """Same rule on the ledger directly: a Shopify restore row already gave back
    `shopify_pts`; the counter return's Rs `share` gives back exactly `restored`."""
    from api.routers import loyalty as L

    db = points.db
    if shopify_pts:
        db.loyalty_transactions.insert_one({
            "txn_id": "return-restore:SH-1", "customer_id": "CUST-1", "type": "ADJUST",
            "points": shopify_pts, "order_id": "ord-1", L._RETURN_RESTORE: "SH-1"})
        db.loyalty_accounts.update_one({"customer_id": "CUST-1"}, {"$inc": {
            "balance_points": shopify_pts, "lifetime_redeemed": -shopify_pts}})
    out = L.reverse_for_return("RET-2", "ord-1", "CUST-1", points_paid_share=share)
    assert out["ok"] is True, out
    assert points() == (50 + shopify_pts + restored - 50, 100 - shopify_pts - restored, 0)
