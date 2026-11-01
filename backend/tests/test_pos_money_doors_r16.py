"""
IMS 2.0 - POS money doors, round-16 money review of PR #1264 (head 0b2e1f06)
============================================================================
ONE set of give-back marks for a return (its credit line, else its hold),
read by the counter, the reconciler and "Nothing was paid"; a lost step is
tasked once and finished once by a person; the credit-note write-off has its
own done stamp; "Cash owed - now paid" writes its payout first; a same-key
press while the first saves is told so; a redemption writes its row first.
No emoji (cp1252).
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
from tests.test_pos_money_doors_r15 import _boom_at_money_out, _refs  # noqa: E402
from tests.strict_fakes import StrictCollection  # noqa: E402


def _hold(till, rid):
    return (_stored(till).get("refund_holds") or {}).get(rid)


# =========================================================================== #
# 1 / 2: the counter never deletes its trace; it stamps each step done
# =========================================================================== #
@pytest.mark.usefixtures("gst_numbers")
def test_an_unknown_counter_claim_keeps_the_hold_and_raises_a_task(till, monkeypatch, tasks):
    payload = _two_unit_bill(till, monkeypatch)
    payload["refund_tenders"] = [{"method": "CASH", "amount": 1800.0}]
    released = []
    monkeypatch.setattr(returns_router, "_release_returnable_qty", lambda *a, **k: released.append(a) or True)
    monkeypatch.setattr(returns_router, "_record_unused_credit_note", lambda *a, **k: True)
    real = returns_router._step_claim
    monkeypatch.setattr(returns_router, "_step_claim",
                        lambda store, step: returns_router.STEP_ERROR if step == "claims_released"
                        else real(store, step))
    _boom_at_money_out(monkeypatch)
    with pytest.raises(RuntimeError):
        _advance_client().post("/api/v1/returns", json=payload, headers=_hdr("STORE_MANAGER"))
    assert released == []  # unknown: never acted on a guess
    (rid, hold), = (_stored(till).get("refund_holds") or {}).items()
    assert hold.get("kept") is True  # the trace stays: Needs completing lists it
    assert f"return_step_lost:{rid}:claims_released" in _refs(tasks)


@pytest.mark.usefixtures("gst_numbers")
def test_the_counter_stamps_each_give_back_done(till, monkeypatch, tasks):
    payload = _two_unit_bill(till, monkeypatch)
    payload["refund_tenders"] = [{"method": "CASH", "amount": 1800.0}]
    monkeypatch.setattr(returns_router, "_release_returnable_qty", lambda *a, **k: True)
    monkeypatch.setattr(returns_router, "_record_unused_credit_note", lambda *a, **k: False)  # not recorded
    _boom_at_money_out(monkeypatch)
    with pytest.raises(RuntimeError):
        _advance_client().post("/api/v1/returns", json=payload, headers=_hdr("STORE_MANAGER"))
    (rid, hold), = (_stored(till).get("refund_holds") or {}).items()
    assert hold.get("claims_released_done") is True
    assert hold.get("number_written_off") is True and not hold.get("number_written_off_done")
    assert hold.get("kept") is True and _refs(tasks) == [f"return_step_lost:{rid}:number_written_off"]


# =========================================================================== #
# 2 / 4: "Nothing was paid" finishes a lost step, once
# =========================================================================== #
def test_nothing_paid_finishes_a_counter_step_that_was_never_stamped_done(till, monkeypatch, tasks):
    released = []
    monkeypatch.setattr(returns_router, "_release_returnable_qty", lambda *a, **k: released.append(a) or True)
    monkeypatch.setattr(returns_router, "_record_unused_credit_note", lambda *a, **k: True)
    _bill_with_hold(till, "RET-LOST", claims_released=True, claims_released_at=_ago(30))  # died after the mark
    _complete("RET-LOST", nothing_paid=True)
    assert len(released) == 1 and _hold(till, "RET-LOST") is None
    _complete("RET-LOST", nothing_paid=True)  # a replay never finishes it twice
    assert len(released) == 1


def test_a_lost_reconciler_step_is_tasked_once_and_a_person_finishes_it(till, monkeypatch, tasks):
    released = []
    monkeypatch.setattr(returns_router, "_release_returnable_qty", lambda *a, **k: released.append(a) or True)
    monkeypatch.setattr(returns_router, "_record_unused_credit_note", lambda *a, **k: True)
    _bill_with_hold(till, "RET-F")
    _void(till, "RET-F", claims_released=True, claims_released_at=_ago(30), pending_at=_ago(30),
          number_written_off=True, number_written_off_done=True)
    for _ in range(3):  # three reconciler ticks
        cust_router._finish_void(_ledger(till), dict(_ledger(till).find_one({"entry_id": "RET-F:credit"})),
                                 "x", undo=True)
    assert _refs(tasks) == ["return_step_lost:RET-F:claims_released"] and released == []
    assert _hold(till, "RET-F") is not None  # kept: the return stays in Needs completing
    items = asyncio.run(returns_router.needs_completing(_user("ACCOUNTANT")))["items"]
    assert [i["return_id"] for i in items] == ["RET-F"]
    _complete("RET-F", nothing_paid=True)  # the person's finish action
    row = _ledger(till).find_one({"entry_id": "RET-F:credit"})
    assert len(released) == 1 and row["claims_released_done"] is True and row["void_settled"] is True
    assert _hold(till, "RET-F") is None


# =========================================================================== #
# 3: ONE set of marks -- a late row can no longer follow a counter give-back
# =========================================================================== #
def test_an_insert_that_raised_and_cannot_be_read_back_is_unclear(till):
    _customer(till)
    ledger = StrictCollection("credit_note_ledger", [])

    def _boom(*a, **k):
        raise RuntimeError("connection reset after the bytes left")

    ledger.insert_one = _boom
    entry = {"entry_id": "RET-X:credit", "customer_id": "CUST-1", "type": "ISSUED", "delta": 100.0, "amount": 100.0}
    assert cust_router.post_ledger_credit(_repo(till), entry, ledger=ledger) == cust_router.UNCLEAR
    assert not entry.get(cust_router.NOT_WRITTEN)


def test_the_reconciler_reads_the_marks_the_counter_wrote_on_the_line(till, monkeypatch, tasks):
    released, written_off = [], []
    monkeypatch.setattr(returns_router, "_release_returnable_qty", lambda *a, **k: released.append(a) or True)
    monkeypatch.setattr(returns_router, "_record_unused_credit_note",
                        lambda n, *a, **k: written_off.append(n) or True)
    _bill_with_hold(till, "RET-ONE")
    # A refused credit's VOID line: the counter gave the units back ON THE LINE.
    _void(till, "RET-ONE", claims_released=True, claims_released_done=True)
    assert returns_router._give_back("ORD-1", "RET-ONE", STORE,
                                     {"claims_released": lambda: released.append("again") or True},
                                     finisher=True, sole_owner=False) is True
    cust_router._finish_void(_ledger(till), dict(_ledger(till).find_one({"entry_id": "RET-ONE:credit"})),
                             "x", undo=True)
    assert released == [] and written_off == ["TST01/C2627/0090"]


# =========================================================================== #
# 5: Cash owed - now paid writes its payout first and replays the STORED one
# =========================================================================== #
def _owed(till, rid="RET-O"):
    till["db"].get_collection("returns").insert_one(
        {"return_id": rid, "order_id": "ORD-1", "store_id": STORE, "status": "COMPLETED",
         "owed_amount": 600.0, "owed_tenders": [{"method": "CASH", "amount": 600.0}]})


def _pay(rid="RET-O", **body):
    return asyncio.run(returns_router.mark_owed_paid(rid, returns_router.OwedPaidBody(**body), _user("ACCOUNTANT")))


def test_a_lost_flip_keeps_the_return_owed_and_a_re_press_replays_the_stored_payment(till, monkeypatch):
    monkeypatch.setattr(returns_router, "_close_task", lambda *a, **k: None)
    audits = []
    monkeypatch.setattr(returns_router, "_audit_complete", lambda rid, *a, **k: audits.append(rid))
    _owed(till)
    returns = till["db"].get_collection("returns")
    real = returns.update_one
    state = {"n": 0}

    def _flip(flt, upd, *a, **k):
        if "owed_paid_at" in (upd.get("$set") or {}) and state["n"] == 0:
            state["n"] += 1
            raise RuntimeError("lost")
        return real(flt, upd, *a, **k)

    monkeypatch.setattr(returns, "update_one", _flip)
    with pytest.raises(HTTPException) as e:
        _pay(method="UPI")
    assert e.value.status_code == 503
    out = asyncio.run(returns_router.needs_completing(_user("ACCOUNTANT")))
    assert [o["return_id"] for o in out["owed"]] == ["RET-O"]  # still owed: it can be pressed again
    monkeypatch.setattr(returns_router, "_drawer_closed_today", lambda store_id: True)  # never re-checked
    again = _pay(method="CASH", cash_paid_from="STORE_DRAWER")
    assert again["method"] == "UPI" and again["replayed"] is True
    payouts = list(returns.find({"payout_of": "RET-O"}))
    assert len(payouts) == 1 and payouts[0]["refund_tenders"] == [{"method": "UPI", "amount": 600.0}]
    assert returns.find_one({"return_id": "RET-O"})["owed_paid_method"] == "UPI" and audits == ["RET-O"]


def test_a_lost_payout_write_books_nothing_and_stays_owed(till, monkeypatch):
    monkeypatch.setattr(returns_router, "_close_task", lambda *a, **k: None)
    _owed(till)
    returns = till["db"].get_collection("returns")

    def _boom(*a, **k):
        raise RuntimeError("insert lost")

    monkeypatch.setattr(returns, "insert_one", _boom)
    with pytest.raises(HTTPException):
        _pay(method="UPI")
    assert returns.find_one({"return_id": "RET-O"}).get("owed_paid_at") is None


# =========================================================================== #
# 6: the credit-note write-off has its own done stamp and is in the gate
# =========================================================================== #
def test_a_number_write_off_that_was_not_recorded_never_settles_the_void(till, monkeypatch, tasks):
    monkeypatch.setattr(returns_router, "_record_unused_credit_note", lambda *a, **k: False)
    ledger = _ledger(till)
    ledger.insert_one(_row("SA-CN-1", type="VOID", delta=0.0, void_settled=False, note_number="TST01/C2627/0091"))
    cust_router._finish_void(ledger, dict(ledger.find_one({"entry_id": "SA-CN-1"})), "x", undo=False)
    row = ledger.find_one({"entry_id": "SA-CN-1"})
    assert row["number_written_off"] is True and not row.get("number_written_off_done")
    assert row["void_settled"] is False and "return_step_lost:SA-CN-1:number_written_off" in _refs(tasks)


def test_the_write_off_reports_whether_it_was_recorded(monkeypatch):
    class _Audit:
        def __init__(self, out):
            self.out = out

        def create(self, data):
            return self.out

    for out, expect in (({"id": 1}, True), (None, False)):
        monkeypatch.setattr("api.dependencies.get_audit_repository", lambda o=out: _Audit(o), raising=False)
        assert returns_router._record_unused_credit_note("N1", {}, "R1", {"user_id": "u"}, "why") is expect


# =========================================================================== #
# 9: a same-key press while the first still saves is told so
# =========================================================================== #
def test_a_same_key_press_while_the_first_saves_is_told_to_wait(monkeypatch):
    from api.routers.orders import create as C

    units = StrictCollection("stock_units", [{"stock_id": "U1", "status": "SOLD", "order_id": "O-SAVING"}])

    class _Stock:
        collection = units

    monkeypatch.setattr("api.dependencies.get_stock_repository", lambda: _Stock(), raising=False)
    C._stamp_claim_key({"U1": None}, "O-SAVING", "K-1")
    assert units.find_one({"stock_id": "U1"})["claim_key"] == "K-1"

    class _Orders:
        def __init__(self, saved):
            self.saved = saved

        def find_one(self, q):
            return {"order_id": "O-SAVING"} if self.saved else None

    exc = C._still_saving(_Orders(False), "K-1")
    assert exc.status_code == 409 and exc.detail["code"] == C.SALE_STILL_SAVING
    assert C._still_saving(_Orders(True), "K-1") is None  # saved: the twin replay answers
    assert C._still_saving(_Orders(False), "K-OTHER") is None  # another bill's unit: the stock refusal


# =========================================================================== #
# 10: a redemption writes its row FIRST
# =========================================================================== #
def _redeem(key="POSPAY:O1:k:leg:0", amount=100.0):
    return cust_router.redeem_store_credit_atomic("CUST-1", amount, reason="r", guard_ref=key)


def test_a_crash_between_the_debit_and_the_row_finish_is_recovered(till, monkeypatch):
    _customer(till, store_credit=500.0)
    monkeypatch.setattr(cust_router, "get_customer_repository", lambda: _repo(till))
    ledger = _ledger(till)
    real = ledger.update_one
    state = {"n": 0}

    def _finish(flt, upd, *a, **k):
        if (upd.get("$set") or {}).get("type") == "REDEEMED" and state["n"] == 0:
            state["n"] += 1
            raise RuntimeError("operation exceeded time limit")
        return real(flt, upd, *a, **k)

    monkeypatch.setattr(ledger, "update_one", _finish)
    with pytest.raises(HTTPException) as e:
        _redeem()
    assert e.value.status_code == 503
    row = ledger.find_one({"entry_id": "POSPAY:O1:k:leg:0"})
    assert row["type"] == "ISSUING" and till["db"].get_collection("customers").find_one(
        {"customer_id": "CUST-1"})["store_credit"] == 400.0  # debited, its row waiting
    out = cust_router.reconcile_pending_credits(_repo(till), ledger=ledger, older_than_s=0)
    assert out["finished"] == 1 and ledger.find_one({"entry_id": "POSPAY:O1:k:leg:0"})["type"] == "REDEEMED"


def test_a_re_press_after_an_unclear_redeem_debits_once(till, monkeypatch):
    _customer(till, store_credit=500.0)
    monkeypatch.setattr(cust_router, "get_customer_repository", lambda: _repo(till))
    ledger = _ledger(till)
    real = ledger.update_one
    state = {"n": 0}

    def _finish(flt, upd, *a, **k):
        if (upd.get("$set") or {}).get("type") == "REDEEMED" and state["n"] == 0:
            state["n"] += 1
            raise RuntimeError("lost")
        return real(flt, upd, *a, **k)

    monkeypatch.setattr(ledger, "update_one", _finish)
    with pytest.raises(HTTPException):
        _redeem()
    out = _redeem()
    assert out["entry"]["type"] == "REDEEMED" and out["balance"] == 400.0
    assert till["db"].get_collection("customers").find_one({"customer_id": "CUST-1"})["store_credit"] == 400.0


def test_a_debit_that_never_landed_is_voided_by_the_reconciler(till, monkeypatch):
    _customer(till, store_credit=500.0)
    repo = _repo(till)

    def _dead(*a, **k):
        raise RuntimeError("socket closed")

    monkeypatch.setattr(type(repo), "try_debit_store_credit", _dead)
    monkeypatch.setattr(cust_router, "get_customer_repository", lambda: repo)
    with pytest.raises(HTTPException):
        _redeem()
    assert _ledger(till).find_one({"entry_id": "POSPAY:O1:k:leg:0"})["type"] == "ISSUING"
    out = cust_router.reconcile_pending_credits(repo, ledger=_ledger(till), older_than_s=0)
    assert out["voided"] == 1
    assert till["db"].get_collection("customers").find_one({"customer_id": "CUST-1"})["store_credit"] == 500.0


# =========================================================================== #
# NIT 11: the loyalty backfill lists a burn beside an unbacked payment
# =========================================================================== #
def test_the_loyalty_backfill_lists_a_conflict_and_never_stamps_it():
    from scripts import backfill_loyalty_tender_claims as bl
    from tests.strict_fakes import StrictDB

    db = StrictDB()
    db["orders"].insert_one({"order_id": "O-1", "balance_due": 0.0, "payments": [
        {"payment_id": "P-OLD", "method": "LOYALTY", "amount": 300.0, "reference": "300 points"}]})
    db["loyalty_transactions"].insert_one({"txn_id": "T-OLD", "type": "REDEEM", "order_id": "O-1",
                                           "tender_payment_id": None})
    out = bl.plan(db)
    assert out["conflicts"] == [("T-OLD", "O-1", "P-OLD", 300.0)]
    assert "T-OLD" not in out["legacy"]  # never stamped: the owner decides


def test_an_unfinished_nothing_paid_keeps_its_hold_listed_and_never_dropped(till, monkeypatch, tasks):
    monkeypatch.setattr(returns_router, "_release_returnable_qty", lambda *a, **k: False)  # the release is lost
    monkeypatch.setattr(returns_router, "_record_unused_credit_note", lambda *a, **k: True)
    _bill_with_hold(till, "RET-NP")
    _complete("RET-NP", nothing_paid=True)
    assert _hold(till, "RET-NP") is not None and "return_step_lost:RET-NP:claims_released" in _refs(tasks)
    items = asyncio.run(returns_router.needs_completing(_user("ACCOUNTANT")))["items"]
    assert [i["return_id"] for i in items] == ["RET-NP"]  # the person can finish it
    returns_router._reserve_order_refund("ORD-1", 100.0, "RET-NEW")  # a later return on the bill
    assert _hold(till, "RET-NP") is not None  # its marks are the trace: never dropped
