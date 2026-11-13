"""
IMS 2.0 - a partial return gives back points IN PROPORTION (#1397, owner 2026-11-09).
=====================================================================================
(A) Owner ruling 2026-11-09: a Rs 1,000 bill paid Rs 900 cash + 100 points;
returning one Rs 50 item gives back 5 points and Rs 45 cash. Each later return
gets its own share, cumulative per bill, never more than the points spent.
Before: loyalty.reverse_for_return restored the WHOLE redemption on the bill's
first return and _points_paid_share held the whole points part back from that
return's money (capped at its net) -- a cheap partial return gave back every
point, permanently when nothing else came back (also under a restocking fee).

(B), the serialisation of returns on a points-paid bill, is in
test_return_points_one_at_a_time.

Driven through the REAL quote + POST /returns over the fakes of
test_returns_endpoint_tender_capture with the REAL reversal engine (mongomock),
and the REAL scheduled sweep over the loyalty rig.

No emoji (Windows cp1252).
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

from tests.test_return_points_paid_once import (  # noqa: E402,F401
    _REAL_REVERSE,
    CASH,
    POINTS,
    _drawer,
    _issued,
    _line,
    _points_sale,
    _post,
    _quote,
)
from tests.test_returns_endpoint_tender_capture import _BILLED_GROSS, ctx  # noqa: E402,F401

pytestmark = pytest.mark.usefixtures("gst_numbers")

HALF = _BILLED_GROSS / 2


def _engine(c, monkeypatch, points=int(POINTS), rupees=None, earned=0):
    """The real reversal engine over the real loyalty repositories (mongomock):
    the bill redeemed ``points`` worth ``rupees`` and earned ``earned``."""
    import mongomock
    from api.routers import loyalty as L
    from database.repositories.loyalty_repository import (
        LoyaltyAccountRepository,
        LoyaltyTransactionRepository,
    )

    db = mongomock.MongoClient()["points_proportional"]
    accounts = LoyaltyAccountRepository(db.get_collection("loyalty_accounts"))
    txns = LoyaltyTransactionRepository(db.get_collection("loyalty_transactions"))
    accounts.find_or_create("CUST-1")
    accounts.adjust_balance("CUST-1", delta_points=earned, delta_lifetime_earned=earned,
                            delta_lifetime_redeemed=points)
    oid = c["order"]["order_id"]
    row = {"txn_id": "R-1", "customer_id": "CUST-1", "type": "REDEEM", "points": points, "order_id": oid}
    if rupees is not None:
        row["rupee_value"] = rupees
    db.loyalty_transactions.insert_one(row)
    if earned:
        db.loyalty_transactions.insert_one({"txn_id": "E-1", "customer_id": "CUST-1", "type": "EARN",
                                            "points": earned, "order_id": oid})
    monkeypatch.setattr(L, "get_loyalty_transaction_repository", lambda: txns)
    monkeypatch.setattr(L, "get_loyalty_account_repository", lambda: accounts)
    monkeypatch.setattr(L, "get_order_repository", lambda: None, raising=False)
    monkeypatch.setattr(L, "reverse_for_return", _REAL_REVERSE)

    def books():
        acct = db.loyalty_accounts.find_one({"customer_id": "CUST-1"})
        rows = list(db.loyalty_transactions.find({"type": "ADJUST"}, {"_id": 0}))
        return int(acct["balance_points"]), int(acct.get("lifetime_redeemed") or 0), rows

    return books


def _done(c):
    return [d for d in c["returns"].docs if d.get("status") == "COMPLETED"]


def _back(c, books):
    """Cash refunded + points given back (rate 1) == the value handed back."""
    return round(sum(d["refund_amount"] for d in _done(c)) + books()[0], 2)


# ----------------------------------------------------------------------------
# (A) the share, both sides
# ----------------------------------------------------------------------------


def test_a_partial_return_gives_back_its_share_of_points_and_the_rest_as_cash(ctx, monkeypatch):
    c = _points_sale(ctx, qty=2)
    books = _engine(c, monkeypatch)
    q = _quote(c, items=_line(1))
    assert q["points_paid_share"] == POINTS / 2 and q["net_refund"] == HALF - POINTS / 2
    r = _post(c, [{"method": "CASH", "amount": q["net_refund"]}], items=_line(1))
    assert r.status_code == 201, r.text
    assert r.json()["points_paid_share"] == POINTS / 2
    assert books()[0] == POINTS / 2  # was the whole 590 on the first return
    assert _back(c, books) == HALF

    q2 = _quote(c, items=_line(1))
    assert q2["points_paid_share"] == POINTS / 2
    assert _post(c, [{"method": "CASH", "amount": q2["net_refund"]}], items=_line(1)).status_code == 201
    balance, lifetime_redeemed, rows = books()
    assert balance == POINTS and lifetime_redeemed == 0  # the redemption, back once in all
    assert sorted(int(x.get("reversed_redeem_points", x["points"])) for x in rows) == [295, 295]
    assert _back(c, books) == _BILLED_GROSS and _drawer(c) == CASH and _issued(c) == []


def test_a_cheap_partial_return_alone_gives_back_only_its_share(ctx, monkeypatch):
    """Nothing else comes back: the bill keeps the rest of its points spent."""
    c = _points_sale(ctx, qty=20)  # Rs 295 a unit
    books = _engine(c, monkeypatch)
    q = _quote(c, items=_line(1))
    assert q["points_paid_share"] == 30.0  # 590 x 295 / 5900 = 29.5, half up
    assert q["net_refund"] == 265.0
    assert _post(c, [{"method": "CASH", "amount": 265.0}], items=_line(1)).status_code == 201
    assert books()[0] == 30  # was 590
    assert _back(c, books) == 295.0


def test_a_restocking_fee_comes_off_both_parts_in_proportion(ctx, monkeypatch):
    c = _points_sale(ctx)
    books = _engine(c, monkeypatch)
    q = _quote(c, restocking_fee=1000.0)
    assert q["points_paid_share"] == 490.0  # 590 x 4,900 / 5,900
    assert q["net_refund"] == 4410.0
    r = _post(c, [{"method": "CASH", "amount": 4410.0}], restocking_fee=1000.0)
    assert r.status_code == 201, r.text
    assert books()[0] == 490  # was 590
    assert _back(c, books) == _BILLED_GROSS - 1000.0


def test_three_returns_give_back_exactly_the_points_spent(ctx, monkeypatch):
    c = _points_sale(ctx, qty=3)
    books = _engine(c, monkeypatch)
    shares = []
    for _ in range(3):
        q = _quote(c, items=_line(1))
        shares.append(q["points_paid_share"])
        assert _post(c, [{"method": "CASH", "amount": q["net_refund"]}], items=_line(1)).status_code == 201
    assert sum(shares) == POINTS and all(abs(s - POINTS / 3) < 1 for s in shares)
    assert books()[0] == POINTS and books()[1] == 0
    # Each third is priced at Rs 1,966.67 (the bill's own rounding, 1 paisa over in all):
    # the cash is every return's value less exactly the points held back.
    nets = round(sum(d["net_refund"] for d in _done(c)), 2)
    assert round(sum(d["refund_amount"] for d in _done(c)), 2) == round(nets - POINTS, 2)
    assert round(abs(nets - _BILLED_GROSS), 2) <= 0.01


def test_an_identical_retry_gives_nothing_back_twice(ctx, monkeypatch):
    c = _points_sale(ctx, qty=2)
    books = _engine(c, monkeypatch)
    key = {"Idempotency-Key": "points-prop-1"}
    q = _quote(c, items=_line(1))
    first = _post(c, [{"method": "CASH", "amount": q["net_refund"]}], headers=key, items=_line(1))
    again = _post(c, [{"method": "CASH", "amount": q["net_refund"]}], headers=key, items=_line(1))
    assert first.status_code == 201 and again.json()["_idempotent_replay"] is True
    assert len(c["returns"].docs) == 1 and books()[0] == POINTS / 2


def test_the_points_come_back_at_the_bill_s_own_rate(ctx, monkeypatch):
    """1,180 points bought the Rs 590 points part (Rs 0.50 a point)."""
    c = _points_sale(ctx, qty=2)
    books = _engine(c, monkeypatch, points=1180, rupees=POINTS)
    q = _quote(c, items=_line(1))
    assert q["points_paid_share"] == POINTS / 2
    assert _post(c, [{"method": "CASH", "amount": q["net_refund"]}], items=_line(1)).status_code == 201
    assert books()[0] == 590 and books()[1] == 590


def test_the_earn_claw_stays_order_wide_on_the_first_return(ctx, monkeypatch):
    c = _points_sale(ctx, qty=2)
    books = _engine(c, monkeypatch, earned=500)
    q = _quote(c, items=_line(1))
    assert _post(c, [{"method": "CASH", "amount": q["net_refund"]}], items=_line(1)).status_code == 201
    assert books()[0] == 295  # 500 earned clawed, 295 given back
    q2 = _quote(c, items=_line(1))
    assert _post(c, [{"method": "CASH", "amount": q2["net_refund"]}], items=_line(1)).status_code == 201
    assert books()[0] == 590  # the claw ran once


def test_a_bill_whose_points_paid_no_tender_still_gets_its_redemption_back(ctx, monkeypatch):
    """Positive control: no LOYALTY tender on the bill -- the engine is unchanged."""
    c = ctx([{"method": "CASH", "amount": _BILLED_GROSS}], qty=2)
    books = _engine(c, monkeypatch)
    q = _quote(c, items=_line(1))
    assert q["points_paid_share"] == 0.0
    assert _post(c, [{"method": "CASH", "amount": HALF}], items=_line(1)).status_code == 201
    assert books()[0] == POINTS


# ----------------------------------------------------------------------------
# (A) the engine: a later return's own row, exactly once, and its recovery
# ----------------------------------------------------------------------------


def test_a_later_return_s_points_move_once_and_recover_after_a_failed_write(ctx, monkeypatch):
    from api.routers import loyalty as L

    c = _points_sale(ctx, qty=2)
    books = _engine(c, monkeypatch)
    oid = c["order"]["order_id"]
    assert L.reverse_for_return("RET-1", oid, "CUST-1", points_paid_share=295.0)["redeemed_restored"] == 295

    real = L._apply_reversal_balance
    def _restore_write_fails(accounts, cid, txn_id, *a):
        return ("failed", None) if txn_id.startswith("return-restore:") else real(accounts, cid, txn_id, *a)

    monkeypatch.setattr(L, "_apply_reversal_balance", _restore_write_fails)
    lost = L.reverse_for_return("RET-2", oid, "CUST-1", points_paid_share=295.0)
    assert lost["ok"] is False and books()[0] == 295  # its row stands, its points not moved
    assert len(books()[2]) == 2
    monkeypatch.setattr(L, "_apply_reversal_balance", real)
    healed = L.reverse_for_return("RET-2", oid, "CUST-1", points_paid_share=295.0)
    assert healed["ok"] is True and healed["redeemed_restored"] == 295
    again = L.reverse_for_return("RET-2", oid, "CUST-1", points_paid_share=295.0)
    assert again["ok"] is True and not again.get("redeemed_restored")
    balance, lifetime_redeemed, rows = books()
    assert balance == POINTS and lifetime_redeemed == 0 and len(rows) == 2
    # Never more than spent, whatever a later caller names.
    over = L.reverse_for_return("RET-3", oid, "CUST-1", points_paid_share=590.0)
    assert over["ok"] is True and books()[0] == POINTS and len(books()[2]) == 2


# ----------------------------------------------------------------------------
# (A) recovery: the scheduled sweep finishes a flagged return with ITS share
# ----------------------------------------------------------------------------

from test_loyalty_redeem_exactly_once import CUST, ORDER, START, _account, _redeem, rig  # noqa: E402,F401
from test_loyalty_reversal_reconciliation import _tick, tasks  # noqa: E402,F401


def test_the_sweep_gives_back_the_flagged_return_s_share_not_the_whole_redemption(rig, tasks):
    _redeem(100)
    rig["db"].orders.update_one({"order_id": ORDER}, {"$set": {"payments": [
        {"method": "CASH", "amount": 4900.0}, {"method": "LOYALTY", "amount": 100.0}]}})
    rig["db"].returns.insert_one({"return_id": "RET-P", "order_id": ORDER, "customer_id": CUST,
                                  "store_id": "BV-TEST-01", "status": "COMPLETED", "net_refund": 500.0,
                                  "points_paid_share": 10.0, "loyalty_reversal_failed": True})
    _tick(rig)
    acct = _account(rig)
    assert int(acct["balance_points"]) == START - 90  # was START: all 100 back
    assert int(acct["lifetime_redeemed"]) == 90
    assert rig["db"].returns.find_one({"return_id": "RET-P"})["loyalty_reversal_failed"] is False
    _tick(rig)
    assert int(_account(rig)["balance_points"]) == START - 90


# ----------------------------------------------------------------------------
# (A) a return that loses the reversal-marker race still gets its own share
# ----------------------------------------------------------------------------


def test_a_return_that_loses_the_marker_race_still_gets_its_share_back(ctx, monkeypatch):
    """Review 11-09: R1 (its reversal failed earlier, now re-run by the sweep)
    and R2 both read the ledger before either marker lands. R1 wins the
    uniq_loyalty_order_reversal insert; R2 hits DuplicateKeyError. R2's share
    was held back from its money, so it must still come back as points --
    before, the loser answered ok/raced and its 295 points were lost, unflagged."""
    from api.routers import loyalty as L

    c = _points_sale(ctx, qty=2)
    books = _engine(c, monkeypatch)
    oid = c["order"]["order_id"]
    txns = L.get_loyalty_transaction_repository()
    # The production partial-unique index (database/connection.py).
    txns.collection.create_index(
        [("customer_id", 1), ("reversal_of_order_id", 1)], unique=True,
        partialFilterExpression={"type": "ADJUST", "reversal_of_order_id": {"$type": "string"}},
        name="uniq_loyalty_order_reversal")
    real_create = txns.create
    raced = {}

    def _r1_lands_first(row, **kw):
        if row.get("return_id") == "RET-2" and not raced:
            raced["r1"] = L.reverse_for_return("RET-1", oid, "CUST-1", points_paid_share=295.0)
        return real_create(row, **kw)

    monkeypatch.setattr(txns, "create", _r1_lands_first)
    r2 = L.reverse_for_return("RET-2", oid, "CUST-1", points_paid_share=295.0)
    assert raced["r1"]["ok"] is True and raced["r1"]["redeemed_restored"] == 295
    assert r2["ok"] is True, r2
    balance, lifetime_redeemed, rows = books()
    assert balance == POINTS and lifetime_redeemed == 0  # was 295: R2's share lost
    assert len(rows) == 2
    # And exactly once: a retry of either gives nothing more.
    L.reverse_for_return("RET-2", oid, "CUST-1", points_paid_share=295.0)
    L.reverse_for_return("RET-1", oid, "CUST-1", points_paid_share=295.0)
    assert books()[0] == POINTS and len(books()[2]) == 2


# ----------------------------------------------------------------------------
# D28 (Codex round 5): a later return on a bill whose points recovery an Admin
# closed still goes ahead and still holds its points share back from the money,
# but neither the quote nor the result may promise that points go back.
# ----------------------------------------------------------------------------


def _admin_closed_the_bills_points(c, monkeypatch):
    """The bill carries the admin's close (loyalty_exception_closed), read by
    the one waiver rule every door asks (loyalty._waived_outcome)."""
    import mongomock
    from api.routers import loyalty as L

    bills = mongomock.MongoClient()["waiver"].get_collection("orders")
    bills.insert_one({"order_id": c["order"]["order_id"], "loyalty_exception_closed": True})

    class _Orders:
        collection = bills

    monkeypatch.setattr(L, "get_order_repository", lambda: _Orders())


_HEAD_OFFICE = "points for this bill are settled by head office and are not returned automatically"


def test_a_return_on_a_bill_an_admin_closed_never_promises_points(ctx, monkeypatch):
    c = _points_sale(ctx, qty=2)
    books = _engine(c, monkeypatch)
    _admin_closed_the_bills_points(c, monkeypatch)
    q = _quote(c, items=_line(1))
    # The money is exactly what an unwaived return pays: the share is held back.
    assert q["points_paid_share"] == POINTS / 2 and q["net_refund"] == HALF - POINTS / 2
    assert q["points_waived"] is True
    r = _post(c, [{"method": "CASH", "amount": q["net_refund"]}], items=_line(1))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["points_waived"] is True and body["points_paid_share"] == POINTS / 2
    assert _HEAD_OFFICE in body["message"]
    assert "goes back to the customer's points" not in body["message"]
    balance, lifetime_redeemed, rows = books()
    assert (balance, lifetime_redeemed, rows) == (0, int(POINTS), [])  # no point moved
    [doc] = _done(c)
    assert doc["points_paid_share"] == POINTS / 2 and doc["loyalty_reversal_failed"] is False
    assert _drawer(c) == CASH - q["net_refund"]


def test_an_unwaived_return_says_what_it_always_said(ctx, monkeypatch):
    """Control: the wording and the points are unchanged when no admin closed."""
    c = _points_sale(ctx, qty=2)
    books = _engine(c, monkeypatch)
    q = _quote(c, items=_line(1))
    assert not q.get("points_waived")
    r = _post(c, [{"method": "CASH", "amount": q["net_refund"]}], items=_line(1))
    assert r.status_code == 201, r.text
    body = r.json()
    assert not body.get("points_waived")
    assert f"Rs {POINTS / 2} paid with points goes back to the customer's points" in body["message"]
    assert _HEAD_OFFICE not in body["message"]
    assert books()[0] == POINTS / 2
