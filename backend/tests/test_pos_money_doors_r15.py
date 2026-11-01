"""
IMS 2.0 - POS money doors, round-15 money review of PR #1264 (head ab1c2067)
============================================================================
The counter's give-backs are claimed on the bill's hold before they act (so
"Nothing was paid" never repeats one); the money-out mark comes before the
goods come back; a heal acts once; a lost give-back is a task and keeps the
VOID unsettled; "Cash owed - now paid" follows the advance-refund cash rule;
a same-key press while the first is saving replays it. No emoji (cp1252).
"""

from __future__ import annotations

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MONGODB_URI", "")

from fastapi import HTTPException  # noqa: E402

from api.routers import customers as cust_router  # noqa: E402
from tests.test_pos_money_doors import (  # noqa: E402,F401
    STORE,
    _advance_client,
    _hdr,
    _ledger,
    _stored,
    _user,
    returns_router,
    till,
)
from tests.test_pos_money_doors_r8 import _two_unit_bill  # noqa: E402
from tests.test_pos_money_doors_r9 import _ago, _customer  # noqa: E402
from tests.test_pos_money_doors_r12 import _repo, _row, _spec, tasks  # noqa: E402,F401
from tests.test_pos_money_doors_r13 import _bill_with_hold, _complete  # noqa: E402
from tests.test_pos_money_doors_r14 import _void  # noqa: E402
from tests.test_orders_hardening import hardening_orders  # noqa: E402,F401


def _refs(tasks):
    return [t.get("dedupe_ref") for t in tasks]


def _boom_at_money_out(monkeypatch):
    """An error right AT the money-out mark (before the goods come back)."""
    real = returns_router._keep_order_refund_hold

    def _keep(order_id, return_id, **kw):
        if (kw.get("extra") or {}).get("money_out"):
            raise RuntimeError("died before the money-out mark")
        return real(order_id, return_id, **kw)

    monkeypatch.setattr(returns_router, "_keep_order_refund_hold", _keep)


# =========================================================================== #
# BLOCK 1: the counter's give-backs are claimed on the hold; the release floor
# =========================================================================== #
@pytest.mark.usefixtures("gst_numbers")
def test_the_counter_marks_its_give_backs_on_the_hold_before_it_acts(till, monkeypatch, tasks):
    payload = _two_unit_bill(till, monkeypatch)
    payload["refund_tenders"] = [{"method": "CASH", "amount": 1800.0}]
    released, written_off = [], []
    monkeypatch.setattr(returns_router, "_release_returnable_qty", lambda *a, **k: released.append(a) or True)
    monkeypatch.setattr(returns_router, "_record_unused_credit_note", lambda n, *a, **k: written_off.append(n))
    monkeypatch.setattr(returns_router, "_release_order_refund", lambda *a, **k: False)  # the release is lost
    _boom_at_money_out(monkeypatch)
    with pytest.raises(RuntimeError):
        _advance_client().post("/api/v1/returns", json=payload, headers=_hdr("STORE_MANAGER"))
    assert len(released) == 1 and written_off == ["TST01/C2627/0001"]
    (hold,) = (_stored(till).get("refund_holds") or {}).values()
    assert hold.get("claims_released") is True and hold.get("number_written_off") is True


def test_nothing_paid_honours_the_counters_marks(till, monkeypatch, tasks):
    released, written_off = [], []
    monkeypatch.setattr(returns_router, "_release_returnable_qty", lambda *a, **k: released.append(a) or True)
    monkeypatch.setattr(returns_router, "_record_unused_credit_note", lambda n, *a, **k: written_off.append(n))
    _bill_with_hold(till, "RET-K", claims_released=True, number_written_off=True)
    _complete("RET-K", nothing_paid=True)
    assert released == [] and written_off == []  # the counter already gave both back
    assert "RET-K" not in (_stored(till).get("refund_holds") or {})


def test_a_lost_counter_release_raises_a_task(till, monkeypatch, tasks):
    payload = _two_unit_bill(till, monkeypatch)
    payload["refund_tenders"] = [{"method": "CASH", "amount": 1800.0}]
    monkeypatch.setattr(returns_router, "_release_returnable_qty", lambda *a, **k: False)
    monkeypatch.setattr(returns_router, "_record_unused_credit_note", lambda *a, **k: None)
    monkeypatch.setattr(returns_router, "next_document_number", lambda *a, **k: "TST01/C2627/0001")
    _boom_at_money_out(monkeypatch)
    with pytest.raises(RuntimeError):
        _advance_client().post("/api/v1/returns", json=payload, headers=_hdr("STORE_MANAGER"))
    assert [r for r in _refs(tasks) if r.startswith("return_step_lost:")][0].endswith(":claims_released")


def test_the_units_release_is_floored_at_zero(monkeypatch):
    seen = []

    class _Coll:
        def find_one_and_update(self, match, update, **kw):
            seen.append((match, update))

    monkeypatch.setattr(returns_router, "_orders_coll", lambda: _Coll())
    assert returns_router._release_returnable_qty("ORD-1", {"item_id": "li1"}, 2.0) is True
    match, update = seen[0]
    assert match["items"]["$elemMatch"] == {"item_id": "li1", "returned_qty": {"$gte": 2.0}}
    assert update["$inc"] == {"items.$.returned_qty": -2.0}


# =========================================================================== #
# BLOCK 2: the money-out mark comes before the goods come back
# =========================================================================== #
@pytest.mark.usefixtures("gst_numbers")
def test_an_error_after_the_mark_keeps_the_claims_and_the_hold(till, monkeypatch):
    payload = _two_unit_bill(till, monkeypatch)
    payload["refund_tenders"] = [{"method": "CASH", "amount": 1800.0}]
    released, written_off = [], []
    monkeypatch.setattr(returns_router, "_release_returnable_qty", lambda *a, **k: released.append(a) or True)
    monkeypatch.setattr(returns_router, "_record_unused_credit_note", lambda n, *a, **k: written_off.append(n))

    def _boom(*a, **k):
        raise RuntimeError("loyalty store down")

    monkeypatch.setattr("api.routers.loyalty.reverse_for_return", _boom)
    monkeypatch.setattr(returns_router, "_quarantine_damaged_units", _boom)
    with pytest.raises(RuntimeError):
        _advance_client().post("/api/v1/returns", json=payload, headers=_hdr("STORE_MANAGER"))
    assert released == [] and written_off == []
    (hold,) = (_stored(till).get("refund_holds") or {}).values()
    assert hold.get("money_out") is True and hold.get("kept") is True


# =========================================================================== #
# BLOCK 3: a heal acts once; the press reasserts only released claims
# =========================================================================== #
def test_two_heals_of_one_void_take_the_units_back_once(till, monkeypatch, tasks):
    reclaimed = []
    monkeypatch.setattr(returns_router, "_reassert_return_claims", lambda row: reclaimed.append(row["entry_id"]))
    row = _void(till, "RET-2H", claims_released=True)
    row["delta"] = 200.0
    assert cust_router._heal_voided_row(_ledger(till), dict(row), "x", unrecorded=True) is True
    assert cust_router._heal_voided_row(_ledger(till), dict(row), "x", unrecorded=True) is False
    assert reclaimed == ["RET-2H:credit"]
    assert _refs(tasks).count(_refs(tasks)[0]) == 1 and len(tasks) == 1


@pytest.mark.parametrize("released, expect", [(False, []), (True, ["RET-P:credit"])])
def test_a_late_press_reasserts_only_claims_the_void_gave_back(till, monkeypatch, released, expect):
    reclaimed = []
    monkeypatch.setattr(returns_router, "_reassert_return_claims", lambda row: reclaimed.append(row["entry_id"]))
    monkeypatch.setattr(returns_router, "_keep_order_refund_hold", lambda *a, **k: None)
    monkeypatch.setattr(cust_router, "apply_store_credit_delta", lambda *a, **k: True)
    monkeypatch.setattr(cust_router, "_finish_credit_row", lambda *a, **k: False)
    _customer(till)
    _void(till, "RET-P", claims_released=released)
    press = _row("RET-P:credit", pending_at=_ago(0), undo_on_void=_spec("RET-P"))
    cust_router._settle_credit_row(_repo(till), _ledger(till), press)
    assert reclaimed == expect


# =========================================================================== #
# SHOULD-FIX 4: the drawer day of a recovered return
# =========================================================================== #
def test_the_money_moved_on_the_unsaved_docs_day_first():
    mm = returns_router._money_moved_at
    hold = {"at": "D-hold", "doc": {"created_at": "D-doc"}}
    line = {"created_at": "D-line", "redated_from": "D-real"}
    assert mm({"line": line, "hold": hold}) == "D-doc"
    assert mm({"line": line, "hold": {"at": "D-hold"}}) == "D-real"
    assert mm({"line": {"created_at": "D-line"}, "hold": {"at": "D-hold"}}) == "D-line"
    assert mm({"line": None, "hold": {"at": "D-hold"}}) == "D-hold"


# =========================================================================== #
# SHOULD-FIX 5: Cash owed - now paid follows the advance-refund cash rule
# =========================================================================== #
def _owed(till, rid="RET-O"):
    till["db"].get_collection("returns").insert_one(
        {"return_id": rid, "order_id": "ORD-1", "store_id": STORE, "status": "COMPLETED",
         "owed_amount": 600.0, "owed_tenders": [{"method": "CASH", "amount": 600.0}]})


def _pay(rid="RET-O", **body):
    return asyncio.run(returns_router.mark_owed_paid(rid, returns_router.OwedPaidBody(**body), _user("ACCOUNTANT")))


def test_owed_cash_must_say_where_it_came_from(till, monkeypatch):
    monkeypatch.setattr(returns_router, "_close_task", lambda *a, **k: None)
    _owed(till)
    with pytest.raises(HTTPException) as e:
        _pay(method="CASH")
    assert e.value.status_code == 400
    monkeypatch.setattr(returns_router, "_drawer_closed_today", lambda store_id: True)
    with pytest.raises(HTTPException) as e:
        _pay(method="CASH", cash_paid_from="STORE_DRAWER")
    assert e.value.status_code == 409
    assert till["db"].get_collection("returns").find_one({"payout_of": "RET-O"}) is None


def test_owed_cash_paid_by_hq_is_never_on_the_drawer_and_is_audited_once(till, monkeypatch):
    audits = []
    monkeypatch.setattr(returns_router, "_close_task", lambda *a, **k: None)
    monkeypatch.setattr(returns_router, "_audit_complete", lambda rid, *a, **k: audits.append(rid))
    _owed(till)
    _pay(method="CASH", cash_paid_from="HQ")
    _pay(method="CASH", cash_paid_from="HQ")
    payout = till["db"].get_collection("returns").find_one({"payout_of": "RET-O"})
    assert payout["refund_tenders"] == [] and payout["drawer_auto_netted"] is False
    assert payout["paid_outside_drawer"] == [{"method": "CASH", "amount": 600.0, "paid_from": "HQ"}]
    assert audits == ["RET-O"]


def test_owed_cash_from_the_drawer_carries_its_count_block(till, monkeypatch):
    monkeypatch.setattr(returns_router, "_close_task", lambda *a, **k: None)
    monkeypatch.setattr(returns_router, "_drawer_closed_today", lambda store_id: False)
    _owed(till)
    _pay(method="CASH", cash_paid_from="STORE_DRAWER")
    (leg,) = till["db"].get_collection("returns").find_one({"payout_of": "RET-O"})["refund_tenders"]
    assert (leg["method"], leg["amount"]) == ("CASH", 600.0) and "cash_count" in leg


def test_the_owed_list_and_the_history(till, monkeypatch):
    _owed(till)
    till["db"].get_collection("returns").insert_one({"return_id": "RET-O-PAYOUT", "payout_of": "RET-O",
                                                     "store_id": STORE, "status": "COMPLETED"})
    out = asyncio.run(returns_router.needs_completing(_user("ACCOUNTANT")))
    assert [o["return_id"] for o in out["owed"]] == ["RET-O"] and out["owed"][0]["owed_amount"] == 600.0
    hist = asyncio.run(returns_router.list_returns(store_id=STORE, return_type=None, skip=0, limit=50,
                                                   current_user=_user("ADMIN")))
    assert [r["return_id"] for r in hist["returns"]] == ["RET-O"]


# =========================================================================== #
# SHOULD-FIX 6: recovered items in the return-line shape
# =========================================================================== #
def test_a_recovered_record_keeps_the_return_line_shape(till, tasks):
    view = {"taxable": 1525.42, "tax": 274.58, "gross": 1800.0, "gst_rate": 18.0,
            "by_rate": {"18.0": {"taxable": 1525.42, "tax": 274.58, "gross": 1800.0}}}
    _bill_with_hold(till, "RET-S", money_out=True, tenders=[{"method": "CASH", "amount": 1800.0}],
                    net_amount=1800.0, gross_refund=1800.0, restocking_fee=0.0, round_off=0.0, gst_view=view,
                    return_type="RETURN")
    doc = _complete("RET-S", cash_leg_paid=True)
    assert doc["items"] == [{"order_item_id": "li1", "item_id": "li1", "product_id": "P1", "return_qty": 1.0}]


# =========================================================================== #
# SHOULD-FIX 9: a same-key press while the first is saving replays it
# =========================================================================== #
def test_a_same_key_press_refused_on_stock_replays_the_first_order(client, auth_headers, hardening_orders,
                                                                   monkeypatch):
    from api.routers.orders import create as C
    from tests.test_orders_hardening import _draft_orders, _frame_item, _post_order_idem

    r1 = _post_order_idem(client, auth_headers, [_frame_item(1000.0)], key="K-SAVE")
    assert r1.status_code in (200, 201), r1.text
    repo = hardening_orders["order_repo"]
    real_find_one = repo.find_one
    state = {"n": 0}

    def _find_one(q, *a, **k):
        state["n"] += 1
        if state["n"] == 1:
            return None  # the first press is still saving
        return real_find_one(q, *a, **k)

    def _stock(*a, **k):
        raise HTTPException(status_code=409, detail="This unit is no longer available.")

    monkeypatch.setattr(repo, "find_one", _find_one)
    monkeypatch.setattr(C, "_assert_serialized_stock_available", _stock)
    r2 = _post_order_idem(client, auth_headers, [_frame_item(1000.0)], key="K-SAVE")
    assert r2.status_code in (200, 201), r2.text
    assert r2.json()["order_id"] == r1.json()["order_id"]
    assert len(_draft_orders(hardening_orders)) == 1


def test_a_same_key_press_refused_at_the_claim_replays_the_first_order(client, auth_headers, hardening_orders,
                                                                      monkeypatch):
    from tests.test_orders_hardening import _draft_orders, _frame_item, _post_order_idem

    r1 = _post_order_idem(client, auth_headers, [_frame_item(1000.0)], key="K-CLAIM")
    assert r1.status_code in (200, 201), r1.text
    repo = hardening_orders["order_repo"]
    coll = repo.collection
    real_find_one, real_insert = repo.find_one, coll.insert_one
    state = {"n": 0}

    def _find_one(q, *a, **k):
        state["n"] += 1
        return None if state["n"] == 1 else real_find_one(q, *a, **k)

    def _insert(doc, *a, **k):
        if doc.get("idempotency_key") == "K-CLAIM":
            raise HTTPException(status_code=409, detail="This unit is held by another bill.")
        return real_insert(doc, *a, **k)

    monkeypatch.setattr(repo, "find_one", _find_one)
    monkeypatch.setattr(coll, "insert_one", _insert)
    r2 = _post_order_idem(client, auth_headers, [_frame_item(1000.0)], key="K-CLAIM")
    assert r2.status_code in (200, 201), r2.text
    assert r2.json()["order_id"] == r1.json()["order_id"]
    assert len(_draft_orders(hardening_orders)) == 1


# =========================================================================== #
# SHOULD-FIX 12: a lost give-back is a task; the VOID stays unsettled
# =========================================================================== #
def test_a_void_whose_hold_release_is_lost_is_not_settled(till, monkeypatch, tasks):
    monkeypatch.setattr(returns_router, "_release_returnable_qty", lambda *a, **k: True)
    monkeypatch.setattr(returns_router, "_record_unused_credit_note", lambda *a, **k: None)
    monkeypatch.setattr(returns_router, "_release_order_refund", lambda *a, **k: False)
    row = _void(till, "RET-L")
    cust_router._finish_void(_ledger(till), dict(row), "x", undo=True)
    now = _ledger(till).find_one({"entry_id": "RET-L:credit"})
    assert now["void_settled"] is False and now.get("claims_released_done") is True
    assert "return_step_lost:RET-L:hold_released" in _refs(tasks)


def test_a_claimed_step_without_its_done_stamp_never_settles(till, monkeypatch, tasks):
    monkeypatch.setattr(returns_router, "_record_unused_credit_note", lambda *a, **k: None)
    monkeypatch.setattr(returns_router, "_release_order_refund", lambda *a, **k: True)
    row = _void(till, "RET-D", claims_released=True)  # an earlier pass died after the mark
    cust_router._finish_void(_ledger(till), dict(row), "x", undo=True)
    assert _ledger(till).find_one({"entry_id": "RET-D:credit"})["void_settled"] is False
    assert "return_step_lost:RET-D:claims_released" in _refs(tasks)


def test_nothing_paid_leaves_a_void_with_a_lost_release_unsettled(till, monkeypatch, tasks):
    monkeypatch.setattr(returns_router, "_release_returnable_qty", lambda *a, **k: False)
    monkeypatch.setattr(returns_router, "_record_unused_credit_note", lambda *a, **k: None)
    _bill_with_hold(till, "RET-W")
    _void(till, "RET-W")
    _complete("RET-W", nothing_paid=True)
    assert _ledger(till).find_one({"entry_id": "RET-W:credit"})["void_settled"] is False
    assert "return_step_lost:RET-W:claims_released" in _refs(tasks)


# =========================================================================== #
# 13: the drawer day in the face ledger and the refund legs; the redeem bound
# =========================================================================== #
def _drawer_day_returns(till):
    returns = till["db"].get_collection("returns")
    block = {"status": "NOT_CAPTURED"}
    returns.insert_one({"return_id": "RET-DD", "store_id": STORE, "status": "COMPLETED", "return_type": "RETURN",
                        "created_at": "2026-10-20T06:00:00+00:00", "drawer_at": "2026-10-01T06:00:00+00:00",
                        "refund_tenders": [{"method": "CASH", "amount": 600.0, "cash_count": block}]})


def test_the_refund_legs_go_by_the_drawer_day(till):
    from api.routers.finance.cash_drawer_window import _cash_refund_legs_for_window

    _drawer_day_returns(till)
    assert _cash_refund_legs_for_window(till["db"], STORE, "2026-10-20T00:00:00", "2026-10-20T23:59:59") == []
    assert _cash_refund_legs_for_window(till["db"], STORE, "2026-10-01T00:00:00", "2026-10-01T23:59:59") == [600.0]


def test_the_face_ledger_counts_a_refund_leg_on_its_drawer_day(till):
    from api.services.eod_tally import compute_face_ledger

    _drawer_day_returns(till)
    on = compute_face_ledger(till["db"], STORE, "2026-10-01T00:00:00", "2026-10-01T23:59:59")
    off = compute_face_ledger(till["db"], STORE, "2026-10-20T00:00:00", "2026-10-20T23:59:59")
    assert on["coverage"]["refund_legs"] == 1 and off["coverage"]["refund_legs"] == 0


def test_the_redeem_door_bounds_its_own_writes(till, monkeypatch):
    import pymongo

    seen = []
    real = pymongo.timeout

    def _timeout(s):
        seen.append(s)
        return real(s)

    monkeypatch.setattr(pymongo, "timeout", _timeout)
    monkeypatch.setattr(cust_router, "_redeem_store_credit_atomic", lambda *a, **k: {"entry": {}, "balance": 0.0})
    cust_router.redeem_store_credit_atomic("CUST-1", 10.0, reason="r")
    assert seen == [cust_router.MONEY_WRITE_TIMEOUT_S]


def test_a_stale_read_never_settles_a_void_another_pass_is_still_undoing(till, monkeypatch, tasks):
    monkeypatch.setattr(returns_router, "_record_unused_credit_note", lambda *a, **k: None)
    monkeypatch.setattr(returns_router, "_release_order_refund", lambda *a, **k: True)
    row = _void(till, "RET-R")  # this pass read the row before another pass claimed the units...
    _ledger(till).update_one({"entry_id": "RET-R:credit"}, {"$set": {"claims_released": True}})  # ...mid-act
    cust_router._finish_void(_ledger(till), dict(row), "x", undo=True)
    assert _ledger(till).find_one({"entry_id": "RET-R:credit"})["void_settled"] is False
