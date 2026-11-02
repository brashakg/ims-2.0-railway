"""
IMS 2.0 - POS money doors, round-20 money review of PR #1264 (head 66c4a8a7)
============================================================================
ONE door gives back an unbilled till payment: it claims the leg first,
refuses a leg on its bill, credits once under a fixed id, then marks it given
back (a re-press resumes, never credits twice); the plain Issue no longer
parses reason text. One definition of an unresolved debit; only the same KEY
resumes; the voucher arm is keyed on the voucher; a voucher replay checks its
amount; leg ages compare as times; the human order number is named. No emoji.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MONGODB_URI", "")

from fastapi import HTTPException  # noqa: E402

from api.routers import customers as cust_router  # noqa: E402
from tests.test_pos_money_doors import _ledger, _order, _user, till  # noqa: E402,F401
from tests.test_pos_money_doors_r9 import _ago  # noqa: E402
from tests.test_pos_money_doors_r12 import _repo, tasks  # noqa: E402,F401
from tests.test_pos_money_doors_r15 import _refs  # noqa: E402
from tests.test_pos_money_doors_r17 import KEY, _balance, _redeem, redeem_env  # noqa: E402,F401

VKEY = "POSPAY:O7:k7:leg:0"


def _unbilled_leg(till):
    till["db"].get_collection("orders").insert_one(_order(order_id="O1", order_number="BV/0101", payments=[]))
    _redeem()  # Rs 100 debited (500 -> 400); its payment never reached O1
    _ledger(till).update_one({"entry_id": KEY}, {"$set": {"pending_at": _ago(30)}})


def _voucher(till, balance=0.0, status="REDEEMED", created=None):
    till["db"].get_collection("orders").insert_one(_order(order_id="O7", order_number="BV/0707", payments=[]))
    till["db"].get_collection("vouchers").insert_one({
        "code": "GV7", "status": status, "balance": balance,
        "money_ledger": [{"idempotency_key": VKEY, "delta": -80.0, "created_at": created or _ago(30)}]})
    return till["db"].get_collection("vouchers")


# =========================================================================== #
# BLOCK 1/2: THE one give-back door
# =========================================================================== #
def test_the_door_refuses_a_leg_that_is_on_its_bill(till, redeem_env):
    _unbilled_leg(till)
    till["db"].get_collection("orders").update_one({"order_id": "O1"}, {"$set": {"payments": [
        {"payment_id": "p1", "method": "STORE_CREDIT", "amount": 100.0, "idempotency_key": "k:leg:0"}]}})
    with pytest.raises(HTTPException) as e:
        cust_router.give_back_unbilled_leg(KEY, _user("ADMIN"))
    assert e.value.status_code == 409 and "BV/0101" in e.value.detail and _balance(till) == 400.0
    row = _ledger(till).find_one({"entry_id": KEY})
    assert not row.get("given_back") and not row.get("given_back_claim")  # the claim was let go


def test_the_door_gives_back_once_and_a_re_press_replays(till, redeem_env, tasks):
    _unbilled_leg(till)
    out = cust_router.give_back_unbilled_leg(KEY, _user("ADMIN"))
    assert out["given_back"] is True and _balance(till) == 500.0
    again = cust_router.give_back_unbilled_leg(KEY, _user("ADMIN"))
    assert again["replayed"] is True and _balance(till) == 500.0
    adj = _ledger(till).find_one({"entry_id": f"GIVEBACK:{KEY}"})
    assert adj["type"] == "ADJUSTED" and adj["applied"] is True


def test_a_lost_mark_resumes_from_the_claim_never_credits_twice(till, redeem_env, monkeypatch):
    _unbilled_leg(till)
    ledger = _ledger(till)
    real = ledger.update_one
    state = {"n": 0}

    def _mark(flt, upd, *a, **k):
        if (upd.get("$set") or {}).get("given_back") is True and state["n"] == 0:
            state["n"] += 1
            raise RuntimeError("the mark write was lost")
        return real(flt, upd, *a, **k)

    monkeypatch.setattr(ledger, "update_one", _mark)
    with pytest.raises(RuntimeError):
        cust_router.give_back_unbilled_leg(KEY, _user("ADMIN"))
    assert _balance(till) == 500.0  # credited once
    with pytest.raises(HTTPException) as e:  # the till cannot bill a claimed leg meanwhile
        _redeem()
    assert e.value.status_code == 409
    cust_router.give_back_unbilled_leg(KEY, _user("ADMIN"))  # the re-press resumes
    assert _balance(till) == 500.0 and ledger.find_one({"entry_id": KEY})["given_back"] is True


def test_the_plain_issue_door_no_longer_parses_the_reason(till, redeem_env, monkeypatch):
    _unbilled_leg(till)
    monkeypatch.setattr(cust_router, "_scoped_customer_or_404",
                        lambda *a, **k: till["db"].get_collection("customers").find_one({"customer_id": "CUST-1"}))
    cust_router._post_credit_entry("CUST-1", "ISSUED", cust_router.StoreCreditEntryRequest(
        amount=100.0, reason=f"give back {KEY}"), _user("ADMIN"))
    assert not _ledger(till).find_one({"entry_id": KEY}).get("given_back")


def test_a_voucher_leg_is_given_back_to_the_voucher_once(till, monkeypatch, tasks):
    import mongomock  # the voucher write uses the positional $ (StrictCollection has none)

    seed = _voucher(till).find_one({"code": "GV7"})
    vouchers = mongomock.MongoClient().db.vouchers
    vouchers.insert_one(dict(seed))
    monkeypatch.setattr(cust_router, "_vouchers_coll", lambda: vouchers)
    out = cust_router.give_back_unbilled_leg(VKEY, _user("ADMIN"))
    v = vouchers.find_one({"code": "GV7"})
    assert out["given_back"] is True and v["balance"] == 80.0 and v["status"] == "ACTIVE"
    assert VKEY in v["pos_given_back"]
    cust_router.give_back_unbilled_leg(VKEY, _user("ADMIN"))
    assert vouchers.find_one({"code": "GV7"})["balance"] == 80.0
    with pytest.raises(HTTPException) as e:  # the till's same-key re-press
        cust_router.refuse_given_back_voucher_leg("GV7", VKEY)
    assert e.value.status_code == 409


# =========================================================================== #
# BLOCK 3: the voucher check runs on a real collection (no truth test)
# =========================================================================== #
class _NoTruth:
    """A pymongo Collection refuses bool(): the code must use `is not None`."""

    def __init__(self, coll):
        self.coll = coll

    def __bool__(self):
        raise NotImplementedError("Collection objects do not implement truth value testing")

    def __getattr__(self, name):
        return getattr(self.coll, name)


def test_the_voucher_scan_runs_on_a_collection_that_refuses_truth(till, monkeypatch, tasks):
    vouchers = _voucher(till)
    monkeypatch.setattr(cust_router, "_vouchers_coll", lambda: _NoTruth(vouchers))
    cust_router.reconcile_pending_credits(_repo(till), ledger=_ledger(till))
    assert _refs(tasks) == [f"voucher_unbilled:{VKEY}"]
    assert "BV/0707" in tasks[0]["title"] and "Give back this unbilled payment" in tasks[0]["description"]


# =========================================================================== #
# 5 / 6: only the same KEY resumes; the voucher arm keyed on the voucher
# =========================================================================== #
def test_a_second_key_on_the_same_bill_is_refused_beside_an_unresolved_leg(till, redeem_env):
    _unbilled_leg(till)
    with pytest.raises(HTTPException) as e:
        cust_router.redeem_store_credit_atomic("CUST-1", 50.0, reason="r", ref="O1",
                                               guard_ref="POSPAY:O1:k:leg:1", source="POS_PAYMENT")
    assert e.value.status_code == 409 and "BV/0101" in e.value.detail and _balance(till) == 400.0


def test_the_same_voucher_is_never_debited_twice_for_one_sale_on_a_walk_in_bill(till):
    _voucher(till, balance=500.0, status="ACTIVE")
    till["db"].get_collection("orders").update_one({"order_id": "O7"}, {"$set": {"customer_id": "walkin-1"}})
    with pytest.raises(HTTPException) as e:  # a new key on another bill, the same voucher
        cust_router.refuse_unresolved_debit("walkin-1", "POSPAY:O8:k8:leg:0", "GV7")
    assert e.value.status_code == 409 and "BV/0707" in e.value.detail
    cust_router.refuse_unresolved_debit("walkin-1", VKEY, "GV7")  # the same key resumes: no 409


# =========================================================================== #
# 7: a voucher replay checks the amount
# =========================================================================== #
def test_a_voucher_replay_for_another_amount_is_refused():
    import mongomock

    from api.services import money_guard

    coll = mongomock.MongoClient().db.vouchers
    coll.insert_one({"code": "GV1", "status": "ACTIVE", "balance": 500.0, "money_ledger": []})
    first = money_guard.debit(coll, "GIFT_VOUCHER", "GV1", 100.0, idempotency_key="POSPAY:O1:k:leg:0")
    assert first.ok
    again = money_guard.debit(coll, "GIFT_VOUCHER", "GV1", 200.0, idempotency_key="POSPAY:O1:k:leg:0")
    assert not again.ok and again.reason == "amount_mismatch"
    same = money_guard.debit(coll, "GIFT_VOUCHER", "GV1", 100.0, idempotency_key="POSPAY:O1:k:leg:0")
    assert same.ok and same.reason == "duplicate"


# =========================================================================== #
# 8: a voucher leg's age compares as time, not text
# =========================================================================== #
def test_an_ist_stamped_voucher_leg_is_checked_after_ten_minutes_not_hours(till, monkeypatch, tasks):
    ist = timezone(timedelta(hours=5, minutes=30))
    created = (datetime.now(timezone.utc) - timedelta(minutes=15)).astimezone(ist).isoformat()
    _voucher(till, created=created)
    cust_router.reconcile_pending_credits(_repo(till), ledger=_ledger(till))
    assert _refs(tasks) == [f"voucher_unbilled:{VKEY}"]


# =========================================================================== #
# 9: a manual redemption still being confirmed has its own words
# =========================================================================== #
def test_a_manual_redemption_being_confirmed_has_its_own_words(till, redeem_env):
    _ledger(till).insert_one({"_id": "MANUAL-REDEEM:CUST-1:f1", "entry_id": "MANUAL-REDEEM:CUST-1:f1",
                              "customer_id": "CUST-1", "type": "ISSUING", "final_type": "REDEEMED",
                              "amount": 50.0, "pending_at": _ago(0)})
    with pytest.raises(HTTPException) as e:
        _redeem()
    assert e.value.status_code == 409 and "still being confirmed" in e.value.detail
    assert "Complete Sale" not in e.value.detail


def test_an_unclear_give_back_credit_resumes_once_on_the_re_press(till, redeem_env, monkeypatch):
    _unbilled_leg(till)
    real = cust_router.post_ledger_credit
    state = {"n": 0}

    def _unclear_once(repo, entry):
        got = real(repo, entry)  # the credit lands ...
        state["n"] += 1
        return "UNCLEAR" if state["n"] == 1 else got  # ... but its answer is lost once

    monkeypatch.setattr(cust_router, "post_ledger_credit", _unclear_once)
    with pytest.raises(HTTPException) as e:
        cust_router.give_back_unbilled_leg(KEY, _user("ADMIN"))
    assert e.value.status_code == 503 and _balance(till) == 500.0
    out = cust_router.give_back_unbilled_leg(KEY, _user("ADMIN"))
    assert out["given_back"] is True and _balance(till) == 500.0
