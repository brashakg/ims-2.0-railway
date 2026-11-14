"""
IMS 2.0 - a return gives the points-paid part back ONCE (#1397 round 5).
=========================================================================
Codex (PR-1397-round5-PENDING-OWNER item 2): a Rs 900 cash + 100 points sale,
returned, paid Rs 900 cash + Rs 100 STORE CREDIT and the loyalty reversal ALSO
restored the 100 points -- the points-paid part came back twice. The store
credit pool counted the LOYALTY tender (returns._order_non_refundable_tenders),
the STORE_CREDIT leg drew on it, and reverse_for_return restored the points.

Owner ruling 2026-11-08: the points-paid part comes back once, as points. Cash
caps and partial-return rules unchanged. Owner ruling 2026-11-09: a partial
return gives back points IN PROPORTION, cumulative per bill, never more than
spent -- the share held back from the money is the share given back as points
(test_return_points_proportional).

Driven through the REAL quote + POST /returns over the fakes of
test_returns_endpoint_tender_capture; the loyalty reversal is a recorder there
(the engine's own exactly-once is tested in test_loyalty_reverse_for_return).

No emoji (Windows cp1252).
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

from api.routers.finance import _cash_sales_for_window  # noqa: E402
from tests.test_returns_endpoint_tender_capture import (  # noqa: E402,F401
    _BILLED_GROSS,
    _HDR,
    _STORE,
    _payload,
    ctx,
)

pytestmark = pytest.mark.usefixtures("gst_numbers")

CASH, POINTS = 5310.00, 590.00  # Rs 5,900 bill: 90% cash, 10% points


def _points_sale(ctx, qty=1):
    return ctx([{"method": "CASH", "amount": CASH}, {"method": "LOYALTY", "amount": POINTS}], qty=qty)


def _line(qty):
    body = _payload()
    body["items"][0]["return_qty"] = qty
    return body["items"]


def _quote(c, **over):
    r = c["client"].post("/api/v1/returns/quote", json=_payload(**over), headers=_HDR)
    assert r.status_code == 200, r.text
    return r.json()


def _post(c, tenders=None, headers=None, **over):
    body = _payload(**over)
    if tenders is not None:
        body["refund_tenders"] = tenders
    return c["client"].post("/api/v1/returns", json=body, headers={**_HDR, **(headers or {})})


def _drawer(c):
    return _cash_sales_for_window(c["db"], _STORE, "2000-01-01T00:00:00", "2999-01-01T00:00:00")[1]


def _issued(c):
    return [d for d in c["ledger"].docs if d.get("type") == "ISSUED"]


def _credit(c):
    return c["customers"].customers["CUST-1"]["store_credit"]


def _nothing_recorded(c, credit_before):
    assert c["returns"].docs == [] and c["loyalty"] == []
    assert _issued(c) == [] and _credit(c) == credit_before
    assert c["order"]["items"][0].get("returned_qty", 0) == 0


def test_the_quote_asks_only_for_the_cash_part_and_offers_no_store_credit(ctx):
    c = _points_sale(ctx)
    q = _quote(c)
    assert q["net_refund"] == CASH  # the split the till must build
    assert q["points_paid_share"] == POINTS and q["return_value"] == _BILLED_GROSS
    assert q["refundable_by_tender"] == {"CASH": CASH}
    assert q["non_refundable_tenders"] == {}
    assert q["cash_in_shortfall"] is False


def test_a_full_return_pays_the_cash_and_gives_the_points_back_once(ctx):
    c = _points_sale(ctx)
    before = _credit(c)
    r = _post(c, [{"method": "CASH", "amount": CASH}])
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["refund_amount"] == CASH and out["points_paid_share"] == POINTS
    assert out["net_refund"] == _BILLED_GROSS  # the value: cap and credit note read it
    assert out["credit_amount"] is None
    assert "590.0 paid with points" in out["message"]
    [doc] = c["returns"].docs
    assert doc["refund_amount"] == CASH and doc["points_paid_share"] == POINTS
    assert _drawer(c) == CASH
    assert _issued(c) == [] and _credit(c) == before  # no store credit at all
    assert [x[0] for x in c["loyalty"]] == [out["return_id"]]  # the points, once


def test_the_points_part_cannot_also_be_taken_as_store_credit(ctx):
    c = _points_sale(ctx)
    before = _credit(c)
    r = _post(c, [{"method": "CASH", "amount": CASH}, {"method": "STORE_CREDIT", "amount": POINTS}])
    assert r.status_code in (400, 422), r.text
    _nothing_recorded(c, before)


def test_the_points_part_cannot_be_taken_as_cash(ctx):
    c = _points_sale(ctx)
    before = _credit(c)
    r = _post(c, [{"method": "CASH", "amount": _BILLED_GROSS}])
    assert r.status_code == 400, r.text
    _nothing_recorded(c, before)


def test_a_loyalty_refund_leg_is_refused(ctx):
    c = _points_sale(ctx)
    before = _credit(c)
    r = _post(c, [{"method": "CASH", "amount": CASH}, {"method": "LOYALTY", "amount": POINTS}])
    assert r.status_code == 400, r.text
    assert "points" in r.json()["detail"].lower()
    _nothing_recorded(c, before)


def test_a_credit_note_return_credits_only_the_cash_part(ctx):
    c = _points_sale(ctx)
    before = _credit(c)
    r = _post(c, return_type="CREDIT_NOTE")
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["credit_amount"] == CASH and out["points_paid_share"] == POINTS
    assert round(_credit(c) - before, 2) == CASH
    [row] = _issued(c)
    # One credit note for the whole return's tax; the balance moves by the cash part.
    assert row["amount"] == _BILLED_GROSS and row["delta"] == CASH
    assert len(c["loyalty"]) == 1


def test_a_return_with_no_breakdown_records_only_the_cash_part(ctx):
    c = _points_sale(ctx)
    r = _post(c)
    assert r.status_code == 201, r.text
    assert r.json()["refund_amount"] == CASH and r.json()["drawer_auto_netted"] is False


def test_two_partial_returns_hold_the_points_part_back_once_and_never_more(ctx):
    c = _points_sale(ctx, qty=2)
    half = _BILLED_GROSS / 2
    share = POINTS / 2  # owner 2026-11-09: in proportion
    q = _quote(c, items=_line(1))
    assert q["net_refund"] == half - share and q["points_paid_share"] == share
    assert _post(c, [{"method": "CASH", "amount": half - share}], items=_line(1)).status_code == 201

    q2 = _quote(c, items=_line(1))
    assert q2["net_refund"] == half - share and q2["points_paid_share"] == share
    bad = _post(c, [{"method": "CASH", "amount": half - share},
                    {"method": "STORE_CREDIT", "amount": share}], items=_line(1))
    assert bad.status_code in (400, 422), bad.text
    assert _post(c, [{"method": "CASH", "amount": half - share}], items=_line(1)).status_code == 201

    docs = [d for d in c["returns"].docs if d.get("status") == "COMPLETED"]
    assert sum(d["refund_amount"] for d in docs) == CASH
    assert sum(d["points_paid_share"] for d in docs) == POINTS
    assert _drawer(c) == CASH and _issued(c) == []


def test_a_small_first_return_holds_its_share_and_the_next_return_the_rest(ctx):
    """A small first return holds back only its own share (owner 2026-11-09);
    the bill's next return holds back the rest -- never twice."""
    c = _points_sale(ctx, qty=20)  # Rs 295 a unit
    q = _quote(c, items=_line(1))
    assert q["points_paid_share"] == 30.0 and q["net_refund"] == 265.0  # 29.5 points, half up
    assert _post(c, [{"method": "CASH", "amount": 265.0}], items=_line(1)).status_code == 201
    q2 = _quote(c, items=_line(19))
    assert q2["points_paid_share"] == POINTS - 30.0
    assert q2["net_refund"] == CASH - 265.0
    assert _post(c, [{"method": "CASH", "amount": q2["net_refund"]}], items=_line(19)).status_code == 201
    docs = [d for d in c["returns"].docs if d.get("status") == "COMPLETED"]
    assert sum(d["points_paid_share"] for d in docs) == POINTS
    assert sum(d["refund_amount"] for d in docs) == CASH


def test_an_identical_retry_replays_and_pays_nothing_more(ctx):
    c = _points_sale(ctx)
    key = {"Idempotency-Key": "points-once-1"}
    first = _post(c, [{"method": "CASH", "amount": CASH}], headers=key)
    again = _post(c, [{"method": "CASH", "amount": CASH}], headers=key)
    assert first.status_code == 201 and again.status_code in (200, 201), again.text
    assert again.json()["_idempotent_replay"] is True
    assert again.json()["refund_amount"] == CASH and again.json()["points_paid_share"] == POINTS
    assert len(c["returns"].docs) == 1 and len(c["loyalty"]) == 1
    assert _drawer(c) == CASH and _issued(c) == []


def test_a_restocking_fee_still_comes_off_the_cash_part(ctx):
    c = _points_sale(ctx)
    q = _quote(c, restocking_fee=1000.0)
    # In proportion of what comes back after the fee (owner 2026-11-09): 590 x 4,900 / 5,900.
    assert q["points_paid_share"] == 490.0 and q["net_refund"] == round(_BILLED_GROSS - 1000 - 490.0, 2)


def test_a_voucher_sale_still_refunds_its_voucher_part_as_store_credit(ctx):
    """Positive control: only the points-paid part changed."""
    c = ctx([{"method": "CASH", "amount": CASH}, {"method": "GIFT_VOUCHER", "amount": POINTS}])
    q = _quote(c)
    assert q["net_refund"] == _BILLED_GROSS and q["points_paid_share"] == 0.0
    assert q["refundable_by_tender"] == {"CASH": CASH, "STORE_CREDIT": POINTS}
    r = _post(c, [{"method": "CASH", "amount": CASH}, {"method": "STORE_CREDIT", "amount": POINTS}])
    assert r.status_code == 201, r.text
    assert r.json()["credit_amount"] == POINTS and r.json()["refund_amount"] == _BILLED_GROSS


# ----------------------------------------------------------------------------
# Round 6 (#1397 review), restated by the owner 2026-11-09: the REAL engine on a
# partial return. The money side holds back exactly what the engine gives back
# as points -- each return its own share, in proportion -- or the points-paid
# part is paid twice (once as points, once as cash) or not at all.
# ----------------------------------------------------------------------------


def _real_engine(c, monkeypatch):
    """The real reversal engine over the real loyalty repositories (mongomock),
    holding the bill's 590-point redemption."""
    import mongomock
    from api.routers import loyalty as L
    from database.repositories.loyalty_repository import (
        LoyaltyAccountRepository,
        LoyaltyTransactionRepository,
    )

    db = mongomock.MongoClient()["points_paid_once"]
    accounts = LoyaltyAccountRepository(db.get_collection("loyalty_accounts"))
    txns = LoyaltyTransactionRepository(db.get_collection("loyalty_transactions"))
    accounts.find_or_create("CUST-1")
    accounts.adjust_balance("CUST-1", delta_points=0, delta_lifetime_redeemed=int(POINTS))
    db.loyalty_transactions.insert_one({"txn_id": "R-1", "customer_id": "CUST-1", "type": "REDEEM",
                                        "points": int(POINTS), "order_id": c["order"]["order_id"]})
    monkeypatch.setattr(L, "get_loyalty_transaction_repository", lambda: txns)
    monkeypatch.setattr(L, "get_loyalty_account_repository", lambda: accounts)
    monkeypatch.setattr(L, "get_order_repository", lambda: None, raising=False)
    monkeypatch.setattr(L, "reverse_for_return", _REAL_REVERSE)
    return lambda: int(db.loyalty_accounts.find_one({"customer_id": "CUST-1"})["balance_points"])


from api.routers.loyalty import reverse_for_return as _REAL_REVERSE  # noqa: E402


def _value_back(c, points):
    docs = [d for d in c["returns"].docs if d.get("status") == "COMPLETED"]
    return round(sum(d["refund_amount"] for d in docs) + points(), 2)


def test_a_first_partial_return_never_pays_more_than_the_goods_handed_back(ctx, monkeypatch):
    c = _points_sale(ctx, qty=2)
    points = _real_engine(c, monkeypatch)
    half_value = _BILLED_GROSS / 2

    q = _quote(c, items=_line(1))
    r = _post(c, [{"method": "CASH", "amount": q["net_refund"]}], items=_line(1))
    assert r.status_code == 201, r.text
    assert points() == POINTS / 2  # what the real engine restored: this return's share
    assert _value_back(c, points) == half_value  # cash + points == the goods returned
    assert q["points_paid_share"] == r.json()["points_paid_share"] == POINTS / 2

    q2 = _quote(c, items=_line(1))
    assert q2["points_paid_share"] == POINTS / 2 and q2["net_refund"] == half_value - POINTS / 2
    assert _post(c, [{"method": "CASH", "amount": q2["net_refund"]}], items=_line(1)).status_code == 201
    assert points() == POINTS  # never restored twice
    assert _value_back(c, points) == _BILLED_GROSS
    assert _drawer(c) == CASH and _issued(c) == []
