"""
IMS 2.0 - POS money doors, round-19 money review of PR #1264 (head 4113365f)
============================================================================
The client bill lock is gone; the SERVER enforces one rule: no new store-
credit or gift-voucher debit for a customer while that customer has an
unresolved debit on another bill. Every redemption replay checks customer
and amount; a give-back recorded through Issue blocks the leg for good; the
unbilled task is stamped only once it exists, names the one door that works,
and covers gift vouchers too; a read error on a money door is 503, never a
refusal; the claim gate's modified_count rule is pinned. No emoji (cp1252).
"""

from __future__ import annotations

import os
import sys

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


def _bal(till, cid):
    return till["db"].get_collection("customers").find_one({"customer_id": cid})["store_credit"]


def _finish_lost_once(monkeypatch, ledger):
    """The finish write of the next redemption is lost once (its debit lands)."""
    real = ledger.update_one
    state = {"n": 0}

    def _finish(flt, upd, *a, **k):
        if (upd.get("$set") or {}).get("type") == "REDEEMED" and state["n"] == 0:
            state["n"] += 1
            raise RuntimeError("lost")
        return real(flt, upd, *a, **k)

    monkeypatch.setattr(ledger, "update_one", _finish)


# =========================================================================== #
# THE rule: no new debit while another bill holds an unresolved one
# =========================================================================== #
def test_a_new_debit_is_refused_while_another_bill_has_an_unresolved_one(till, redeem_env, monkeypatch):
    _finish_lost_once(monkeypatch, _ledger(till))
    with pytest.raises(HTTPException):
        _redeem()  # O1's leg: debited, its line still ISSUING
    with pytest.raises(HTTPException) as e:
        cust_router.redeem_store_credit_atomic("CUST-1", 50.0, reason="r", ref="O2",
                                               guard_ref="POSPAY:O2:k2:leg:0", source="POS_PAYMENT")
    assert e.value.status_code == 409 and "order O1" in e.value.detail and _balance(till) == 400.0
    assert _redeem()["entry"]["type"] == "REDEEMED"  # the same key on the same bill still resumes


def test_a_store_credit_leg_not_on_its_bill_blocks_a_new_debit_until_it_is(till, redeem_env):
    till["db"].get_collection("orders").insert_one(_order(order_id="O1", payments=[]))
    _redeem()  # the debit and its line landed; the payment never reached O1
    with pytest.raises(HTTPException) as e:
        cust_router.redeem_store_credit_atomic("CUST-1", 50.0, reason="r", ref="O2",
                                               guard_ref="POSPAY:O2:k2:leg:0", source="POS_PAYMENT")
    assert e.value.status_code == 409 and "order O1" in e.value.detail
    till["db"].get_collection("orders").update_one({"order_id": "O1"}, {"$set": {"payments": [
        {"payment_id": "p1", "method": "STORE_CREDIT", "amount": 100.0, "idempotency_key": "k:leg:0"}]}})
    out = cust_router.redeem_store_credit_atomic("CUST-1", 50.0, reason="r", ref="O2",
                                                 guard_ref="POSPAY:O2:k2:leg:0", source="POS_PAYMENT")
    assert out["entry"]["type"] == "REDEEMED" and _balance(till) == 350.0


def test_a_voucher_debit_not_on_its_bill_blocks_a_new_store_credit_debit(till, redeem_env):
    till["db"].get_collection("orders").insert_one(_order(order_id="O5", payments=[]))  # balance due
    till["db"].get_collection("vouchers").insert_one({"code": "GV1", "balance": 50.0, "money_ledger": [
        {"idempotency_key": "POSPAY:O5:k5:leg:0", "delta": -50.0, "created_at": _ago(1)}]})
    with pytest.raises(HTTPException) as e:
        cust_router.redeem_store_credit_atomic("CUST-1", 50.0, reason="r", ref="O2",
                                               guard_ref="POSPAY:O2:k2:leg:0", source="POS_PAYMENT")
    assert e.value.status_code == 409 and "order O5" in e.value.detail and _balance(till) == 500.0


def test_the_rule_reads_or_answers_503_never_a_guess(till, redeem_env, monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("read blip")

    monkeypatch.setattr(cust_router, "_unresolved_debit", _boom)
    with pytest.raises(HTTPException) as e:
        _redeem()
    assert e.value.status_code == 503 and _balance(till) == 500.0


# =========================================================================== #
# 1: every replay checks customer and amount
# =========================================================================== #
def test_a_pending_line_of_another_customer_is_never_debited_to_this_one(till, redeem_env, monkeypatch):
    till["db"].get_collection("customers").insert_one({"customer_id": "CUST-2", "store_credit": 500.0})
    _finish_lost_once(monkeypatch, _ledger(till))
    with pytest.raises(HTTPException):
        _redeem()  # A's line: debited, still ISSUING
    with pytest.raises(HTTPException) as e:  # the bill moved to customer B; same key
        cust_router.redeem_store_credit_atomic("CUST-2", 100.0, reason="r", ref="O1", guard_ref=KEY,
                                               source="POS_PAYMENT")
    assert e.value.status_code == 409 and _bal(till, "CUST-2") == 500.0


def test_the_same_customer_branch_of_the_replay_check():
    row = {"customer_id": "CUST-1", "amount": 100.0}
    cust_router._refuse_other_redemption(row, "CUST-1", 100.0)  # the same redemption: no refusal
    for cid, amt in (("CUST-1", 150.0), ("CUST-9", 100.0)):
        with pytest.raises(HTTPException) as e:
            cust_router._refuse_other_redemption(row, cid, amt)
        assert e.value.status_code == 409


# =========================================================================== #
# 2: the give-back blocks the leg for good; the task
# =========================================================================== #
def _bill_and_unbilled_leg(till, redeem_env, monkeypatch):
    till["db"].get_collection("orders").insert_one(_order(order_id="O1", payments=[]))
    _redeem()
    _ledger(till).update_one({"entry_id": KEY}, {"$set": {"pending_at": _ago(30)}})
    monkeypatch.setattr(cust_router, "_scoped_customer_or_404",
                        lambda *a, **k: till["db"].get_collection("customers").find_one({"customer_id": "CUST-1"}))


def test_a_recorded_give_back_blocks_the_legs_replay_and_closes_the_task(till, redeem_env, monkeypatch, tasks):
    from api.routers import returns as returns_router

    closed = []
    monkeypatch.setattr(returns_router, "_close_task", lambda ref, *a, **k: closed.append(ref))
    _bill_and_unbilled_leg(till, redeem_env, monkeypatch)
    cust_router.reconcile_pending_credits(redeem_env, ledger=_ledger(till))
    assert _refs(tasks) == [f"store_credit_unbilled:{KEY}"]
    # The customer left: Accounts give it back through the card's Issue, quoting the line.
    cust_router._post_credit_entry("CUST-1", "ISSUED", cust_router.StoreCreditEntryRequest(
        amount=100.0, reason=f"give back {KEY}"), _user("ADMIN"))
    assert _balance(till) == 500.0 and _ledger(till).find_one({"entry_id": KEY})["given_back"] is True
    with pytest.raises(HTTPException) as e:  # a later re-press of the old bill
        _redeem()
    assert e.value.status_code == 409 and "given back" in e.value.detail and _balance(till) == 500.0
    cust_router.reconcile_pending_credits(redeem_env, ledger=_ledger(till))
    assert closed == [f"store_credit_unbilled:{KEY}"]


def test_the_task_is_stamped_only_once_it_exists(till, redeem_env, monkeypatch):
    made = []
    state = {"n": 0}

    def _create(repo, **k):
        state["n"] += 1
        if state["n"] == 1:
            return None  # the insert failed
        made.append(k["dedupe_ref"])
        return k

    monkeypatch.setattr("api.services.task_triggers.create_system_task", _create)
    monkeypatch.setattr("api.services.task_triggers.active_tasks", lambda repo, ref: [])
    monkeypatch.setattr("api.dependencies.get_task_repository", lambda: object(), raising=False)
    _bill_and_unbilled_leg(till, redeem_env, monkeypatch)
    cust_router.reconcile_pending_credits(redeem_env, ledger=_ledger(till))
    assert not _ledger(till).find_one({"entry_id": KEY}).get("bill_tasked")  # retried next pass
    cust_router.reconcile_pending_credits(redeem_env, ledger=_ledger(till))
    cust_router.reconcile_pending_credits(redeem_env, ledger=_ledger(till))
    assert made == [f"store_credit_unbilled:{KEY}"]
    assert _ledger(till).find_one({"entry_id": KEY})["bill_tasked"] is True


# =========================================================================== #
# 3: gift-voucher legs are tracked by the same scan
# =========================================================================== #
def test_a_gift_voucher_leg_not_on_its_bill_raises_one_task_and_closes(till, monkeypatch, tasks):
    from api.routers import returns as returns_router

    closed = []
    monkeypatch.setattr(returns_router, "_close_task", lambda ref, *a, **k: closed.append(ref))
    till["db"].get_collection("orders").insert_one(_order(order_id="O7", payments=[]))
    till["db"].get_collection("vouchers").insert_one({"code": "GV7", "balance": 0.0, "money_ledger": [
        {"idempotency_key": "POSPAY:O7:k7:leg:0", "delta": -80.0, "created_at": _ago(30)}]})
    for _ in range(2):
        cust_router.reconcile_pending_credits(_repo(till), ledger=_ledger(till))
    assert _refs(tasks) == ["voucher_unbilled:POSPAY:O7:k7:leg:0"]
    till["db"].get_collection("orders").update_one({"order_id": "O7"}, {"$set": {"payments": [
        {"payment_id": "p7", "method": "GIFT_VOUCHER", "amount": 80.0, "idempotency_key": "k7:leg:0"}]}})
    cust_router.reconcile_pending_credits(_repo(till), ledger=_ledger(till))
    assert closed == ["voucher_unbilled:POSPAY:O7:k7:leg:0"]


# =========================================================================== #
# 4: a read error on a money door is 503, never a refusal
# =========================================================================== #
def test_a_customer_read_error_is_503_not_404(till, redeem_env, monkeypatch):
    class _Down:
        def find_one(self, *a, **k):
            raise RuntimeError("WaitQueueTimeoutError")

    monkeypatch.setattr(redeem_env, "collection", _Down())
    with pytest.raises(HTTPException) as e:
        _redeem()
    assert e.value.status_code == 503


@pytest.mark.asyncio
async def test_an_order_read_error_on_a_payment_is_503_not_404(till, monkeypatch):
    from api.routers.orders import PaymentCreate, add_payment

    till["db"].get_collection("orders").insert_one(_order())
    coll = till["orders"].collection

    def _down(*a, **k):
        raise RuntimeError("AutoReconnect")

    monkeypatch.setattr(coll, "find_one", _down)
    with pytest.raises(HTTPException) as e:
        await add_payment("ORD-1", PaymentCreate(method="CASH", amount=100.0), _user("CASHIER"))
    assert e.value.status_code == 503


# =========================================================================== #
# 5: the claim gate acts only when THIS call set the mark
# =========================================================================== #
def test_the_claim_gate_acts_only_on_modified_count_one():
    class _Res:
        def __init__(self, n):
            self.modified_count = n

    class _Coll:
        def __init__(self, n):
            self.n = n

        def update_one(self, *a, **k):
            return _Res(self.n)

    assert cust_router.claim_step(_Coll(1), {"entry_id": "E"}, "step") is True
    assert cust_router.claim_step(_Coll(0), {"entry_id": "E"}, "step") is False  # another pass set it


@pytest.mark.asyncio
async def test_the_voucher_door_refuses_a_new_debit_beside_an_unresolved_one(till):
    from api.routers.orders import PaymentCreate, add_payment

    till["db"].get_collection("orders").insert_one(_order())
    _ledger(till).insert_one({"_id": "POSPAY:OX:k:leg:0", "entry_id": "POSPAY:OX:k:leg:0", "customer_id": "CUST-1",
                              "type": "ISSUING", "final_type": "REDEEMED", "amount": 100.0, "ref": "OX",
                              "pending_at": _ago(0)})
    till["db"].get_collection("vouchers").insert_one({"code": "GV9", "status": "ACTIVE", "balance": 500.0})
    with pytest.raises(HTTPException) as e:
        await add_payment("ORD-1", PaymentCreate(method="GIFT_VOUCHER", amount=100.0, voucher_code="GV9"),
                          _user("CASHIER"), "kv:leg:0")
    assert e.value.status_code == 409 and "order OX" in e.value.detail
    assert till["db"].get_collection("vouchers").find_one({"code": "GV9"})["balance"] == 500.0
