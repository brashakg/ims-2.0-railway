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


# ----------------------------------------------------------------------------
# Codex round 5, 1397-P1: a credited refund whose points step never ran, or
# failed with its failure note lost, was never found again -- the sweep reads
# only FLAGGED returns and the flag was written after the attempt. The marker
# is now written with the claim, before anything posts, and only cleared once
# the points are settled. Every test below reads the STORED books.
# ----------------------------------------------------------------------------


class _WorkerDeath(BaseException):
    """Not an Exception: nothing in the post may catch it (a killed process)."""


def _sweep_reads(env, monkeypatch, points):
    """The scheduled sweep reads bills + returns through the order repository
    and writes its audit row through the real audit repository (mongomock)."""
    from api.routers import loyalty as L
    from database.repositories.audit_repository import AuditRepository

    class _Orders:
        collection = env.get_collection("orders")

    _Orders.collection.database = env
    audit = AuditRepository(points.db.get_collection("audit_logs"))
    monkeypatch.setattr(L, "get_order_repository", lambda: _Orders())
    monkeypatch.setattr(L, "get_audit_repository", lambda: audit)


def _stored(env, points):
    """Everything a refund leaves behind: points books, the credit ledger, the
    customer's credit balance, the points restore rows and the return doc(s)."""
    from api.routers import loyalty as L

    docs = list(env.get_collection("returns").find({"shopify_refund_id": "700001"}))
    return {
        "points": points(),
        "credit_rows": [r["delta"] for r in _ledger(env)],
        "credit_balance": _balance(env),
        "restore_rows": sorted((t["txn_id"], int(t["points"]))
                               for t in points.db.loyalty_transactions.find({}) if t.get(L._RETURN_RESTORE)),
        "returns": [(d["status"], d.get("credit_note_issued"), d.get("loyalty_reversal_failed")) for d in docs],
        "restock_stock_ids": [sid for d in docs for sid in d.get("restock_stock_ids") or []],
        "sweep_audits": points.db.audit_logs.count_documents({"action": "loyalty.reversal_reconciled"}),
    }


def _post_with_fault(env, monkeypatch, mode):
    """Queue refund 700001 and confirm it under `mode`, then lift the fault.
    worker_death: the process dies right before the points step, after the
    COMPLETED return and its credit are stored. lost_failure_flag: the restore
    fails AND every write that would note the failure on the return fails."""
    from api.routers import loyalty as L
    from api.services import shopify_refund as S

    _order(env, payments=PAYS)
    assert S.handle_shopify_refund(env, _payload(txns=[]), topic="refunds/create")["status"] == "queued"
    review = env.get_collection("shopify_refund_review").find_one({"shopify_refund_id": "700001"})
    coll = env.get_collection("returns")
    real_restore, real_update = L.restore_return_points, coll.update_one
    if mode == "worker_death":
        def die(*_a, **_k):
            raise _WorkerDeath("after the durable COMPLETED, before the points step")

        monkeypatch.setattr(L, "restore_return_points", die)
    elif mode == "lost_failure_flag":
        monkeypatch.setattr(L, "restore_return_points",
                            lambda *a, **k: {"ok": False, "reason": "balance_update_failed"})

        def no_failure_note(query, update, *a, **k):
            if (update.get("$set") or {}).get("loyalty_reversal_failed"):
                raise RuntimeError("failure flag write unavailable")
            return real_update(query, update, *a, **k)

        monkeypatch.setattr(coll, "update_one", no_failure_note)
    try:
        first = S.post_from_review(env, review)
    except _WorkerDeath:
        first = {"died": True}
    monkeypatch.setattr(L, "restore_return_points", real_restore)
    monkeypatch.setattr(coll, "update_one", real_update)
    return review, first


_SETTLED = {
    "points": (150, 0, 50),  # the 100 points back, once; the 50 earned untouched
    "credit_rows": [900.0],  # one credit row, never a second
    "credit_balance": (900.0, 900.0),
    "returns": [("COMPLETED", True, False)],
    "restock_stock_ids": [],  # no stock repository in this rig: nothing minted, ever
}


def _less_rows(stored):
    return {k: v for k, v in stored.items() if k not in ("restore_rows", "sweep_audits")}


@pytest.mark.parametrize("mode", ["healthy", "worker_death", "lost_failure_flag"])
def test_points_owed_by_a_credited_refund_are_never_lost(env, points, monkeypatch, mode):
    """Codex's two schedules + the healthy control, through two sweeps and an
    identical confirmation. Before: both faults left 50 points for good (the
    sweeps answered 0/0/0, the confirmation `duplicate`)."""
    from api.routers import loyalty as L
    from api.services import shopify_refund as S

    review, first = _post_with_fault(env, monkeypatch, mode)
    after_first = _stored(env, points)
    assert after_first["credit_rows"] == [900.0] and after_first["credit_balance"] == (900.0, 900.0)
    if mode == "healthy":
        assert first["status"] == "credited" and first["points_restored"] == 100
        assert _less_rows(after_first) == _SETTLED
    else:
        # The refund is terminal and credited, its points are still owed, and
        # the STORED return says so (the sweep's row).
        assert after_first["points"] == (50, 100, 50) and after_first["restore_rows"] == []
        assert after_first["returns"] == [("COMPLETED", True, True)]

    _sweep_reads(env, monkeypatch, points)
    sweeps = [L.reconcile_loyalty_reversals() for _ in range(2)]
    assert sweeps[0]["reconciled"] == (0 if mode == "healthy" else 1), sweeps
    assert sweeps[1] == {"reconciled": 0, "unresolved": 0, "clean": 0}, sweeps
    after_sweeps = _stored(env, points)
    assert _less_rows(after_sweeps) == _SETTLED
    assert [p for _t, p in after_sweeps["restore_rows"]] == [100]  # ONE restore row
    assert after_sweeps["sweep_audits"] == (0 if mode == "healthy" else 1)

    again = S.post_from_review(env, review)  # the identical confirmation
    assert again["status"] == "duplicate", again
    assert _stored(env, points) == after_sweeps  # nothing twice: points, credit, rows, units
    assert L.reconcile_loyalty_reversals() == {"reconciled": 0, "unresolved": 0, "clean": 0}
    assert _stored(env, points) == after_sweeps


@pytest.mark.parametrize("door", ["confirm", "webhook"])
@pytest.mark.parametrize("mode", ["worker_death", "lost_failure_flag"])
def test_the_refund_sent_again_finishes_its_points_step_without_a_second_credit(
        env, points, monkeypatch, mode, door):
    """No sweep at all (a stopped scheduler): the same refund confirmed again,
    or redelivered by Shopify, is still `duplicate` for its credit and its
    stock -- and finishes the one step left."""
    from api.services import shopify_refund as S

    review, _first = _post_with_fault(env, monkeypatch, mode)
    assert points() == (50, 100, 50)
    _sweep_reads(env, monkeypatch, points)
    for _ in range(2):
        again = (S.post_from_review(env, review) if door == "confirm"
                 else S.handle_shopify_refund(env, _payload(txns=[]), topic="refunds/create"))
        assert again["status"] == "duplicate", again
        stored = _stored(env, points)
        assert _less_rows(stored) == _SETTLED
        assert [p for _t, p in stored["restore_rows"]] == [100]


def test_an_admins_close_stops_the_unfinished_points_step_for_good(env, points, monkeypatch):
    """Owner 2026-11-08: once an Admin closed the recovery exception nothing is
    given back on its own -- not by the sweep, not by the refund sent again."""
    from api.routers import loyalty as L
    from api.services import shopify_refund as S

    review, _first = _post_with_fault(env, monkeypatch, "worker_death")
    env.get_collection("returns").update_one(
        {"shopify_refund_id": "700001"}, {"$set": {"loyalty_exception_closed": True}})
    _sweep_reads(env, monkeypatch, points)
    before = _stored(env, points)
    assert before["points"] == (50, 100, 50)
    assert [L.reconcile_loyalty_reversals() for _ in range(2)] == [
        {"reconciled": 0, "unresolved": 0, "clean": 0}] * 2
    assert S.post_from_review(env, review)["status"] == "duplicate"
    assert S.handle_shopify_refund(env, _payload(txns=[]), topic="refunds/create")["status"] == "duplicate"
    assert _stored(env, points) == before  # nothing moved, nothing rewritten


def test_a_refund_posted_before_the_marker_existed_is_left_alone(env, points, monkeypatch):
    """Owner exclusion: a return recorded before this change carries no marker
    and is never swept or rewritten (its points are not judged again)."""
    from api.routers import loyalty as L
    from api.services import shopify_refund as S

    review, _first = _post_with_fault(env, monkeypatch, "worker_death")
    env.get_collection("returns").update_one(  # as the old code left it
        {"shopify_refund_id": "700001"}, {"$unset": {"loyalty_reversal_failed": ""}})
    _sweep_reads(env, monkeypatch, points)
    before = _stored(env, points)
    assert before["returns"] == [("COMPLETED", True, None)]
    assert [L.reconcile_loyalty_reversals() for _ in range(2)] == [
        {"reconciled": 0, "unresolved": 0, "clean": 0}] * 2
    assert S.post_from_review(env, review)["status"] == "duplicate"
    assert _stored(env, points) == before


def test_a_refund_with_no_points_part_carries_no_marker(env, points):
    """Positive control: nothing owed, nothing flagged (the doc is unchanged)."""
    _order(env)
    _row, res = _queue_and_confirm(env, _payload(txns=[]))
    assert res["status"] == "credited"
    doc = env.get_collection("returns").find_one({"shopify_refund_id": "700001"})
    assert "loyalty_reversal_failed" not in doc


# ----------------------------------------------------------------------------
# D28: a refund on a bill whose points an Admin closed still holds its points
# share back from the store credit, but nothing promises that points go back.
# ----------------------------------------------------------------------------


def test_the_review_note_never_promises_points_on_a_bill_an_admin_closed(env, points, monkeypatch):
    from api.routers.returns import POINTS_WAIVED_NOTE

    _order(env, payments=PAYS, loyalty_exception_closed=True)
    _sweep_reads(env, monkeypatch, points)
    row, res = _queue_and_confirm(env, _payload(txns=[]))
    assert row["credit_note"]["store_credit"] == 900.0  # the share is still held back
    assert POINTS_WAIVED_NOTE in row["note"] and "goes back as points" not in row["note"]
    assert res["status"] == "credited" and res["points_restored"] == 0
    stored = _stored(env, points)
    assert stored["points"] == (50, 100, 50) and stored["restore_rows"] == []
    assert stored["credit_rows"] == [900.0]
    assert stored["returns"] == [("COMPLETED", True, False)]  # settled by the waiver: no retry


# ----------------------------------------------------------------------------
# Round 6 review: the write that marks the return COMPLETED fails (its credit
# is already posted) and the post still answered `credited`. The review row
# closed on a return that still read PENDING; with the points step failing in
# the same run nothing ever came back for it -- the sweep and the resend finish
# COMPLETED returns only, and nobody could confirm a closed row again. The post
# now answers a failure, the row stays open, and the same confirmation finishes
# the SAME return once its claim is stale: one credit, the points once.
# ----------------------------------------------------------------------------


def _confirm(review_id):
    """The real accountant confirm route -> (http status, body or detail)."""
    import asyncio

    from fastapi import HTTPException

    from api.routers.online_store_refund_reviews import confirm_refund_review

    try:
        return 200, asyncio.run(confirm_refund_review(
            review_id, current_user={"user_id": "acc-1", "roles": ["ACCOUNTANT"]}))
    except HTTPException as exc:
        return exc.status_code, exc.detail


@pytest.mark.parametrize("restore", ["fails", "works"])
def test_a_refund_whose_record_was_not_saved_stays_open_and_is_finished_once(
        env, points, monkeypatch, restore):
    from api.routers import loyalty as L
    from api.services import shopify_refund as S

    _order(env, payments=PAYS)
    assert S.handle_shopify_refund(env, _payload(txns=[]), topic="refunds/create")["status"] == "queued"
    reviews = env.get_collection("shopify_refund_review")
    review_id = reviews.find_one({"shopify_refund_id": "700001"})["review_id"]
    coll = env.get_collection("returns")
    real_restore, real_update = L.restore_return_points, coll.update_one

    def no_finalize(query, update, *a, **k):
        if "status" in (update.get("$set") or {}):
            raise RuntimeError("returns write unavailable")
        return real_update(query, update, *a, **k)

    monkeypatch.setattr(coll, "update_one", no_finalize)
    if restore == "fails":
        monkeypatch.setattr(L, "restore_return_points",
                            lambda *a, **k: {"ok": False, "reason": "ledger_read_failed"})
    code, detail = _confirm(review_id)
    monkeypatch.setattr(L, "restore_return_points", real_restore)
    monkeypatch.setattr(coll, "update_one", real_update)

    # The credit is on the books, the return is not finalized: never `credited`.
    owed = (50, 100, 50) if restore == "fails" else (150, 0, 50)
    assert code == 422 and "confirm it again" in str(detail).lower(), (code, detail)
    row = reviews.find_one({"review_id": review_id})
    assert (row["status"], row["resolved"]) == ("CREDIT_FAILED", False)
    first = _stored(env, points)
    assert first["credit_rows"] == [900.0] and first["credit_balance"] == (900.0, 900.0)
    assert first["points"] == owed
    assert first["returns"] == [("PENDING", None, restore == "fails")]

    # Fault lifted. The sweep finishes COMPLETED returns only; a second press
    # while the claim is fresh books nothing and leaves the row open.
    _sweep_reads(env, monkeypatch, points)
    assert [L.reconcile_loyalty_reversals() for _ in range(2)] == [
        {"reconciled": 0, "unresolved": 0, "clean": 0}] * 2
    assert _confirm(review_id)[0] == 422
    assert reviews.find_one({"review_id": review_id})["resolved"] is False
    assert _stored(env, points) == first

    # The claim goes stale (15 minutes): the same confirmation finishes it.
    real_update({"shopify_refund_id": "700001"}, {"$set": {"created_at": "2026-01-01T00:00:00+00:00"}})
    code, body = _confirm(review_id)
    assert code == 200 and body["status"] == "POSTED" and body["result"]["status"] == "credited", (code, body)
    done = _stored(env, points)
    assert _less_rows(done) == _SETTLED  # one credit row, the return COMPLETED
    assert [p for _t, p in done["restore_rows"]] == [100]  # the 100 points, once

    # Nothing twice afterwards: sweeps, the route, the service, a redelivery.
    assert [L.reconcile_loyalty_reversals() for _ in range(2)] == [
        {"reconciled": 0, "unresolved": 0, "clean": 0}] * 2
    assert _confirm(review_id)[0] == 409
    assert S.post_from_review(env, row)["status"] == "duplicate"
    assert S.handle_shopify_refund(env, _payload(txns=[]), topic="refunds/create")["status"] == "duplicate"
    assert _stored(env, points) == done


def test_the_auto_door_answers_a_failure_the_inbox_retries_until_the_return_is_finished(env, monkeypatch):
    """The same rule on the automatic (webhook) door, no points involved: an
    unsaved return is a FAILURE to the inbox drain (it retries hourly), never
    `credited` (inbox row done, return PENDING for good)."""
    from agents.implementations.nexus import _raise_if_failed
    from api.services import shopify_refund as S

    monkeypatch.setenv("SHOPIFY_REFUND_AUTO", "1")
    _order(env)
    coll = env.get_collection("returns")
    real_update = coll.update_one

    def no_finalize(query, update, *a, **k):
        if "status" in (update.get("$set") or {}):
            raise RuntimeError("returns write unavailable")
        return real_update(query, update, *a, **k)

    monkeypatch.setattr(coll, "update_one", no_finalize)
    first = S.handle_shopify_refund(env, _payload(txns=[]), topic="refunds/create")
    monkeypatch.setattr(coll, "update_one", real_update)
    assert (first["status"], first.get("reason")) == ("error", S.NOT_FINALIZED), first
    with pytest.raises(RuntimeError):
        _raise_if_failed(first)  # the drain records the inbox row as failed
    assert [r["delta"] for r in _ledger(env)] == [1000.0]
    assert coll.find_one({"shopify_refund_id": "700001"})["status"] == "PENDING"

    # Retried while the claim is fresh: still a failure, nothing booked.
    fresh = S.handle_shopify_refund(env, _payload(txns=[]), topic="refunds/create")
    assert fresh["status"] == "error", fresh
    # Retried once it is stale (the drain's hourly tick): finished, once.
    real_update({"shopify_refund_id": "700001"}, {"$set": {"created_at": "2026-01-01T00:00:00+00:00"}})
    done = S.handle_shopify_refund(env, _payload(txns=[]), topic="refunds/create")
    assert done["status"] == "credited", done
    doc = coll.find_one({"shopify_refund_id": "700001"})
    assert (doc["status"], doc["credit_note_issued"]) == ("COMPLETED", True)
    assert [r["delta"] for r in _ledger(env)] == [1000.0] and _balance(env) == (1000.0, 1000.0)
    assert S.handle_shopify_refund(env, _payload(txns=[]), topic="refunds/create")["status"] == "duplicate"
    assert [r["delta"] for r in _ledger(env)] == [1000.0]
