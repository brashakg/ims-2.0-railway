"""
IMS 2.0 - a return gives the points-paid part back ONCE (#1397 round 5).
=========================================================================
Codex (PR-1397-round5-PENDING-OWNER item 2): a Rs 900 cash + 100 points sale,
returned, paid Rs 900 cash + Rs 100 STORE CREDIT and the loyalty reversal ALSO
restored the 100 points -- the points-paid part came back twice. The store
credit pool counted the LOYALTY tender (returns._order_non_refundable_tenders),
the STORE_CREDIT leg drew on it, and reverse_for_return restored the points.

Owner ruling 2026-11-08: the points-paid part comes back once, as points. Cash
caps and partial-return rules unchanged; a partial return settles its
proportional share.

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


def test_two_partial_returns_each_settle_their_share_and_never_more(ctx):
    c = _points_sale(ctx, qty=2)
    half_cash, half_points = CASH / 2, POINTS / 2
    q = _quote(c, items=_line(1))
    assert q["net_refund"] == half_cash and q["points_paid_share"] == half_points
    assert _post(c, [{"method": "CASH", "amount": half_cash}], items=_line(1)).status_code == 201

    q2 = _quote(c, items=_line(1))
    assert q2["net_refund"] == half_cash and q2["points_paid_share"] == half_points
    bad = _post(c, [{"method": "CASH", "amount": half_cash - half_points},
                    {"method": "STORE_CREDIT", "amount": half_points}], items=_line(1))
    assert bad.status_code in (400, 422), bad.text
    assert _post(c, [{"method": "CASH", "amount": half_cash}], items=_line(1)).status_code == 201

    docs = [d for d in c["returns"].docs if d.get("status") == "COMPLETED"]
    assert sum(d["refund_amount"] for d in docs) == CASH
    assert sum(d["points_paid_share"] for d in docs) == POINTS
    assert _drawer(c) == CASH and _issued(c) == []


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
    assert q["points_paid_share"] == POINTS and q["net_refund"] == round(_BILLED_GROSS - 1000 - POINTS, 2)


def test_a_voucher_sale_still_refunds_its_voucher_part_as_store_credit(ctx):
    """Positive control: only the points-paid part changed."""
    c = ctx([{"method": "CASH", "amount": CASH}, {"method": "GIFT_VOUCHER", "amount": POINTS}])
    q = _quote(c)
    assert q["net_refund"] == _BILLED_GROSS and q["points_paid_share"] == 0.0
    assert q["refundable_by_tender"] == {"CASH": CASH, "STORE_CREDIT": POINTS}
    r = _post(c, [{"method": "CASH", "amount": CASH}, {"method": "STORE_CREDIT", "amount": POINTS}])
    assert r.status_code == 201, r.text
    assert r.json()["credit_amount"] == POINTS and r.json()["refund_amount"] == _BILLED_GROSS
