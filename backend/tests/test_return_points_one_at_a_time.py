"""
IMS 2.0 - one return at a time on a points-paid bill (#1397, item B, 2026-11-09).
=================================================================================
Two returns of one bill that carries a LOYALTY tender, pressed at once: one
that fails after reserving releases its refund hold, and the other was priced
beside it -- the points-paid part could be paid twice. _reserve_order_refund
now refuses (409 "another return of this bill is being processed - try again")
while another return's hold on such a bill is live, inside the same
compare-and-set that prices the share. Bills without points are unchanged.

Driven through the REAL quote + POST /returns over the fakes of
test_returns_endpoint_tender_capture, the compare-and-set order repository of
test_return_points_paid_race and the REAL reversal engine (mongomock).

No emoji (Windows cp1252).
"""
from __future__ import annotations

import os
import sys

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

from api.routers import returns as returns_router  # noqa: E402
from tests.test_return_points_paid_once import CASH, POINTS, _drawer, _line, _points_sale, _post, _quote  # noqa: E402
from tests.test_return_points_paid_race import _with_compare_and_set  # noqa: E402
from tests.test_return_points_proportional import HALF, _back, _done, _engine  # noqa: E402
from tests.test_returns_endpoint_tender_capture import _BILLED_GROSS, ctx  # noqa: E402,F401

pytestmark = pytest.mark.usefixtures("gst_numbers")




def test_a_second_return_waits_while_another_is_being_processed(ctx, monkeypatch):
    c = _points_sale(ctx, qty=2)
    _with_compare_and_set(c)
    books = _engine(c, monkeypatch)
    q = _quote(c, items=_line(1))
    real_split = returns_router._normalize_refund_tenders
    seen = {}

    def _b_pressed_while_a_runs(tenders, pays):
        if not seen:
            seen["started"] = True
            seen["b"] = _post(c, [{"method": "CASH", "amount": q["net_refund"]}], items=_line(1))
        return real_split(tenders, pays)

    monkeypatch.setattr(returns_router, "_normalize_refund_tenders", _b_pressed_while_a_runs)
    a = _post(c, [{"method": "CASH", "amount": q["net_refund"]}], items=_line(1))
    assert a.status_code == 201, a.text
    assert seen["b"].status_code == 409, seen["b"].text
    assert "another return of this bill is being processed" in seen["b"].json()["detail"].lower()
    assert len(_done(c)) == 1 and books()[0] == 295
    assert c["order"]["items"][0]["returned_qty"] == 1  # B claimed nothing

    q2 = _quote(c, items=_line(1))  # B again, once A is recorded
    assert _post(c, [{"method": "CASH", "amount": q2["net_refund"]}], items=_line(1)).status_code == 201
    assert books()[0] == POINTS and _back(c, books) == _BILLED_GROSS and _drawer(c) == CASH


def test_a_return_failing_after_reserving_never_lets_the_points_part_be_paid_twice(ctx):
    c = _points_sale(ctx, qty=2)
    _with_compare_and_set(c)
    oid = c["order"]["order_id"]
    _p, reserved, share_a = returns_router._reserve_order_refund(oid, HALF, "RET-A")
    assert reserved and share_a == 295.0
    with pytest.raises(HTTPException) as ei:
        returns_router._reserve_order_refund(oid, HALF, "RET-B")
    assert ei.value.status_code == 409
    assert set(c["order"]["refund_holds"]) == {"RET-A"}  # B holds nothing
    assert returns_router._release_order_refund(oid, "RET-A")  # A failed
    _p, _r, share_b = returns_router._reserve_order_refund(oid, HALF, "RET-B")
    assert share_b == 295.0  # B's own share -- never A's as well


def test_two_returns_of_a_bill_without_points_still_run_side_by_side(ctx):
    """Positive control: the serialisation is for points-paid bills only."""
    c = ctx([{"method": "CASH", "amount": _BILLED_GROSS}], qty=2)
    _with_compare_and_set(c)
    oid = c["order"]["order_id"]
    returns_router._reserve_order_refund(oid, HALF, "RET-A")
    prior, reserved, _s = returns_router._reserve_order_refund(oid, HALF, "RET-B")
    assert reserved and prior == HALF
