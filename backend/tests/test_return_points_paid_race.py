"""
IMS 2.0 - two returns of one bill never both hold the points-paid part back (#1397 round 7).
===========================================================================================
Review (round 7, items 1 and 2): returns._points_paid_share subtracted only what
COMPLETED returns already held back. Two returns of one bill whose docs had not
landed yet -- pressed at the same moment, or one kept on a refund hold for Needs
completing -- each held back the WHOLE points-paid part, while the loyalty
engine restores the redemption once per bill (block_on_any_prior_reversal).
Rs 5,310 cash + Rs 590 points, two units returned at once: Rs 4,720 cash + 590
points came back -- Rs 590 short of owner ruling (2) (2026-11-08).

The rule now counts the live and kept refund holds too, and each hold carries
its share from the moment it is reserved (inside the compare-and-set).

Driven through the REAL quote + POST /returns over the fakes of
test_returns_endpoint_tender_capture, with a compare-and-set order repository
(the production OrderRepository.update_if semantics: match the version, then
$set) and the REAL loyalty reversal engine.

No emoji (Windows cp1252).
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

from api.routers import returns as returns_router  # noqa: E402
from tests.test_return_points_paid_once import (  # noqa: E402,F401
    CASH,
    POINTS,
    _drawer,
    _issued,
    _line,
    _points_sale,
    _post,
    _quote,
    _real_engine,
    _value_back,
)
from tests.test_returns_endpoint_tender_capture import _BILLED_GROSS, ctx  # noqa: E402,F401

pytestmark = pytest.mark.usefixtures("gst_numbers")

HALF = _BILLED_GROSS / 2


def _with_compare_and_set(c):
    """Give the fixture's order repository the production compare-and-set, so
    POST /returns reserves its refund on the order (refund_holds)."""
    repo = returns_router.get_order_repository()
    order = c["order"]

    def update_if(order_id, match, sets):
        if order_id != order["order_id"] or any(order.get(k) != v for k, v in match.items()):
            return False
        order.update(sets)
        return True

    repo.update_if = update_if
    return repo


def _completed(c):
    return [d for d in c["returns"].docs if d.get("status") == "COMPLETED"]


def test_two_returns_pressed_at_once_hold_the_points_part_back_once(ctx, monkeypatch):
    """A reserves and prices its money; before A's doc lands, B (the other unit)
    is quoted, posted and completed. Both read the bill while neither doc was
    COMPLETED."""
    c = _points_sale(ctx, qty=2)
    _with_compare_and_set(c)
    points = _real_engine(c, monkeypatch)

    qa = _quote(c, items=_line(1))
    assert qa["net_refund"] == HALF - POINTS
    real_split = returns_router._normalize_refund_tenders
    seen = {}

    def _b_lands_while_a_is_in_flight(tenders, pays):
        if not seen:
            seen["a_pays"] = pays
            assert c["order"]["refund_holds"]  # A's reservation is on the bill
            qb = _quote(c, items=_line(1))
            seen["b_quote"] = qb
            rb = _post(c, [{"method": "CASH", "amount": qb["net_refund"]}], items=_line(1))
            assert rb.status_code == 201, rb.text
        return real_split(tenders, pays)

    monkeypatch.setattr(returns_router, "_normalize_refund_tenders", _b_lands_while_a_is_in_flight)
    ra = _post(c, [{"method": "CASH", "amount": qa["net_refund"]}], items=_line(1))
    assert ra.status_code == 201, ra.text

    docs = _completed(c)
    assert len(docs) == 2
    assert round(sum(d["refund_amount"] for d in docs), 2) == CASH  # Rs 4,720 before the fix
    assert round(sum(d["points_paid_share"] for d in docs), 2) == POINTS  # held back once
    assert seen["b_quote"]["points_paid_share"] == 0.0 and seen["b_quote"]["net_refund"] == HALF
    assert points() == POINTS  # the engine restored the redemption once
    assert _value_back(c, points) == _BILLED_GROSS  # Rs 5,310 cash + 590 points
    assert _drawer(c) == CASH and _issued(c) == []


def test_a_kept_hold_s_points_share_counts_for_the_next_return_of_the_bill(ctx):
    """Return A is kept on a refund hold (its credit unconfirmed / its record
    unsaved) with its share on the hold and no COMPLETED doc. The rule must not
    hold the same points back again for the bill's next return."""
    c = _points_sale(ctx, qty=2)
    _with_compare_and_set(c)
    c["order"]["refund_holds"] = {"RET-A": {
        "amount": HALF, "at": "2026-11-08T10:00:00", "kept": True, "points_paid_share": POINTS}}
    c["order"]["refund_holds_v"] = 1

    assert returns_router._points_paid_share(c["order"], c["order"]["order_id"], HALF, strict=True) == 0.0
    q = _quote(c, items=_line(1))
    assert q["points_paid_share"] == 0.0 and q["net_refund"] == HALF

    # Positive control preserved: the counter still refuses a new return while A is unfinished.
    r = _post(c, [{"method": "CASH", "amount": HALF}], items=_line(1))
    assert r.status_code == 409, r.text
    assert "RET-A" in r.json()["detail"] and _completed(c) == []

    # Once A's doc lands, its share is read from the doc (the hold no longer counts).
    c["returns"].docs.append({"return_id": "RET-A", "order_id": c["order"]["order_id"],
                              "status": "COMPLETED", "net_refund": HALF, "points_paid_share": POINTS})
    assert returns_router._points_paid_share(c["order"], c["order"]["order_id"], HALF, strict=True) == 0.0


def test_a_hold_carries_its_share_from_the_moment_it_is_reserved(ctx):
    c = _points_sale(ctx, qty=2)
    _with_compare_and_set(c)
    oid = c["order"]["order_id"]
    _p, reserved, share_a = returns_router._reserve_order_refund(oid, HALF, "RET-A")
    _p, _r, share_b = returns_router._reserve_order_refund(oid, HALF, "RET-B")
    assert reserved and (share_a, share_b) == (POINTS, 0.0)
    holds = c["order"]["refund_holds"]
    assert holds["RET-A"]["points_paid_share"] == POINTS and holds["RET-B"]["points_paid_share"] == 0.0
    assert returns_router._points_paid_share(c["order"], oid, HALF, strict=True) == 0.0
    # A released (its return failed): its share goes back to the bill's next return.
    assert returns_router._release_order_refund(oid, "RET-A")
    assert returns_router._points_paid_share(c["order"], oid, HALF, strict=True) == POINTS
