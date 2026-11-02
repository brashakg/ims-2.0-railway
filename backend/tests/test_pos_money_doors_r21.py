"""
IMS 2.0 - POS money doors, round-21 money review of PR #1264 (head 1524106a)
============================================================================
ONE owner per till debit leg: the bill (a lease stamped at each till press)
or the give-back, by compare-and-set. Every interleaving of a till re-press
and the give-back door ends with the money out exactly once. Plus the door's
age floor, the real running balance, a dead voucher given back as store
credit, the Shopify refund check, the tender inside the leg rule and the
audit's real user. No emoji.
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
from tests.test_pos_money_doors_r9 import _ago, _customer  # noqa: E402
from tests.test_pos_money_doors_r12 import _repo, tasks  # noqa: E402,F401
from tests.test_pos_money_doors_r13 import _lost_record_once, _stored  # noqa: E402
from tests.test_pos_money_doors_r17 import KEY, _balance, _redeem, redeem_env  # noqa: E402,F401
from tests.test_pos_money_doors_r20 import VKEY, _unbilled_leg, _voucher  # noqa: E402

LEG = "POSPAY:ORD-1:leg-k"


def _age(till, minutes=30, key=LEG):
    _ledger(till).update_one({"entry_id": key}, {"$set": {"pending_at": _ago(minutes), "leg_owner_at": _ago(minutes)}})


async def _press(amount=300.0):
    from api.routers.orders import PaymentCreate, add_payment

    return await add_payment("ORD-1", PaymentCreate(method="STORE_CREDIT", amount=amount), _user("CASHIER"), "leg-k")


@pytest.fixture()
def lost_leg(till, monkeypatch):
    """A real till press whose payment write was lost: Rs 300 taken (1000 ->
    700), the leg owned by its bill, nothing on the bill."""
    till["db"].get_collection("orders").insert_one(_order(order_number="BV/0001"))
    _customer(till, store_credit=1000.0)
    monkeypatch.setattr("api.routers.customers.get_customer_repository", lambda: _repo(till))
    _lost_record_once(monkeypatch, till["orders"])
    return till


async def _lose_first_press(till):
    with pytest.raises(Exception):
        await _press()
    assert _balance(till) == 700.0 and _ledger(till).find_one({"entry_id": LEG})["leg_owner"] == "BILL"


def _paid(till):
    return [p for p in _stored(till)["payments"] if p.get("idempotency_key") == "leg-k"]


# =========================================================================== #
# BLOCK: every interleaving -- the money goes out once
# =========================================================================== #
@pytest.mark.asyncio
async def test_re_press_first_then_the_door_refuses(lost_leg):
    await _lose_first_press(lost_leg)
    _age(lost_leg)
    await _press()  # the bill finishes the leg
    with pytest.raises(HTTPException) as e:
        cust_router.give_back_unbilled_leg(LEG, _user("ADMIN"))
    assert e.value.status_code == 409 and "BV/0001" in e.value.detail
    assert _balance(lost_leg) == 700.0 and len(_paid(lost_leg)) == 1


@pytest.mark.asyncio
async def test_give_back_first_then_the_till_refuses(lost_leg):
    await _lose_first_press(lost_leg)
    _age(lost_leg)  # the bill's lease is stale: the door takes the leg over
    cust_router.give_back_unbilled_leg(LEG, _user("ADMIN"))
    assert _balance(lost_leg) == 1000.0
    with pytest.raises(HTTPException) as e:
        await _press()
    assert e.value.status_code == 409 and "given back" in e.value.detail
    assert _balance(lost_leg) == 1000.0 and _paid(lost_leg) == []


@pytest.mark.asyncio
async def test_the_door_inside_the_tills_press_after_its_debit_check(lost_leg, monkeypatch):
    """The reviewers' probe: the till passed its plain read, then the door
    gives back, then the till would push -- its claim on the leg loses."""
    await _lose_first_press(lost_leg)
    _age(lost_leg)
    real = cust_router.claim_leg_for_bill

    def _door_first(*a, **k):
        cust_router.give_back_unbilled_leg(LEG, _user("ADMIN"))
        return real(*a, **k)

    monkeypatch.setattr(cust_router, "claim_leg_for_bill", _door_first)
    with pytest.raises(HTTPException) as e:
        await _press()
    assert e.value.status_code == 409
    assert _balance(lost_leg) == 1000.0 and _paid(lost_leg) == []


@pytest.mark.asyncio
async def test_the_door_inside_the_tills_order_write_loses(lost_leg, monkeypatch):
    """Both at once the other way: the till won the leg, the door fires while
    the payment is being written -- the door refuses."""
    await _lose_first_press(lost_leg)
    _age(lost_leg)
    repo = lost_leg["orders"]
    real = repo.add_payment
    seen = {}

    def _add(*a, **k):
        try:
            cust_router.give_back_unbilled_leg(LEG, _user("ADMIN"))
        except HTTPException as exc:
            seen["door"] = exc.status_code
        return real(*a, **k)

    monkeypatch.setattr(repo, "add_payment", _add)
    await _press()
    assert seen["door"] == 409 and _balance(lost_leg) == 700.0 and len(_paid(lost_leg)) == 1


@pytest.mark.asyncio
async def test_a_fresh_bill_lease_is_never_taken_over(lost_leg):
    """A crash after the till's claim: while its press is recent the door
    refuses; the re-press finishes it."""
    await _lose_first_press(lost_leg)
    _ledger(lost_leg).update_one({"entry_id": LEG}, {"$set": {"pending_at": _ago(30)}})  # leg old, lease fresh
    with pytest.raises(HTTPException) as e:
        cust_router.give_back_unbilled_leg(LEG, _user("ADMIN"))
    assert e.value.status_code == 409 and "Complete Sale" in e.value.detail
    assert _balance(lost_leg) == 700.0
    await _press()
    assert len(_paid(lost_leg)) == 1


@pytest.mark.asyncio
async def test_a_till_press_between_the_doors_read_and_its_takeover_wins(lost_leg, monkeypatch):
    """The door read a stale lease; a till press renews it before the door's
    takeover lands -- the takeover (keyed on the stamp it read) misses."""
    await _lose_first_press(lost_leg)
    _age(lost_leg)
    ledger = _ledger(lost_leg)
    real = ledger.update_one

    def _renew_first(flt, upd, *a, **k):
        if flt.get("leg_owner") == "BILL" and (upd.get("$set") or {}).get("leg_owner") == "GIVEBACK":
            cust_router.claim_leg_for_bill("STORE_CREDIT", LEG)  # the till's press renews the lease
        return real(flt, upd, *a, **k)

    monkeypatch.setattr(ledger, "update_one", _renew_first)
    with pytest.raises(HTTPException) as e:
        cust_router.give_back_unbilled_leg(LEG, _user("ADMIN"))
    assert e.value.status_code == 409 and _balance(lost_leg) == 700.0
    assert ledger.find_one({"entry_id": LEG})["leg_owner"] == "BILL"


def test_a_crash_after_the_doors_claim_resumes_and_the_till_stays_out(till, redeem_env, monkeypatch):
    _unbilled_leg(till)
    real = cust_router.post_ledger_credit

    def _crash(*a, **k):
        raise RuntimeError("worker died after the claim")

    monkeypatch.setattr(cust_router, "post_ledger_credit", _crash)
    with pytest.raises(RuntimeError):
        cust_router.give_back_unbilled_leg(KEY, _user("ADMIN"))
    assert _ledger(till).find_one({"entry_id": KEY})["leg_owner"] == "GIVEBACK"
    with pytest.raises(HTTPException) as e:  # the till's claim loses
        cust_router.claim_leg_for_bill("STORE_CREDIT", KEY)
    assert e.value.status_code == 409
    monkeypatch.setattr(cust_router, "post_ledger_credit", real)
    cust_router.give_back_unbilled_leg(KEY, _user("ADMIN"))
    assert _balance(till) == 500.0


def test_a_voucher_leg_has_one_owner_too(till):
    vouchers = _voucher(till, balance=500.0, status="ACTIVE")
    cust_router.claim_leg_for_bill("GIFT_VOUCHER", VKEY, "GV7")  # the till won it first
    with pytest.raises(HTTPException) as e:
        cust_router.give_back_unbilled_leg(VKEY, _user("ADMIN"))  # its lease is fresh
    assert e.value.status_code == 409 and vouchers.find_one({"code": "GV7"})["balance"] == 500.0


# =========================================================================== #
# 1: an age floor at the door
# =========================================================================== #
def test_the_door_refuses_a_leg_still_in_flight(till, redeem_env):
    _unbilled_leg(till)
    _ledger(till).update_one({"entry_id": KEY}, {"$set": {"pending_at": _ago(2)}})
    with pytest.raises(HTTPException) as e:
        cust_router.give_back_unbilled_leg(KEY, _user("ADMIN"))
    assert e.value.status_code == 409 and "still being processed" in e.value.detail
    assert _balance(till) == 400.0


# =========================================================================== #
# 2: the give-back line carries the real running balance
# =========================================================================== #
def test_the_give_back_line_carries_the_real_balance(till, redeem_env):
    _unbilled_leg(till)
    cust_router.give_back_unbilled_leg(KEY, _user("ADMIN"))
    assert _ledger(till).find_one({"entry_id": f"GIVEBACK:{KEY}"})["balance_after"] == 500.0


# =========================================================================== #
# 3: a give-back is not a refund of that order (Shopify refund check)
# =========================================================================== #
def test_the_shopify_refund_check_does_not_count_a_give_back():
    from api.services import shopify_refund as sr
    from tests.strict_fakes import StrictDB

    db = StrictDB()
    db.get_collection("credit_note_ledger").insert_one({
        "entry_id": "GIVEBACK:POSPAY:ord-1:k:leg:0", "customer_id": "C1", "type": "ADJUSTED", "delta": 300.0,
        "ref": "ord-1", "source": "UNBILLED_GIVE_BACK", "reason": "Unbilled till payment given back"})
    got = sr._given_back_elsewhere(db, {"order_id": "ord-1", "customer_id": "C1", "order_number": "BV/1"}, "ord-1")
    assert got is not None and got["given"] == 0 and got["untied_hand"] == 0


# =========================================================================== #
# 4: a dead voucher is never restored -- store credit instead
# =========================================================================== #
def _dead_voucher(till, monkeypatch, **over):
    import mongomock

    _customer(till, store_credit=0.0)
    monkeypatch.setattr("api.routers.customers.get_customer_repository", lambda: _repo(till))
    seed = _voucher(till).find_one({"code": "GV7"})
    vouchers = mongomock.MongoClient().db.vouchers
    vouchers.insert_one({**seed, **over})
    monkeypatch.setattr(cust_router, "_vouchers_coll", lambda: vouchers)
    return vouchers


def test_an_expired_voucher_is_given_back_as_store_credit(till, monkeypatch, tasks):
    vouchers = _dead_voucher(till, monkeypatch, expiry_date="2026-01-01")
    out = cust_router.give_back_unbilled_leg(VKEY, _user("ADMIN"))
    assert vouchers.find_one({"code": "GV7"})["balance"] == 0.0
    assert _balance(till) == 80.0 and "store credit" in out["message"] and "GV7" in out["message"]
    cust_router.give_back_unbilled_leg(VKEY, _user("ADMIN"))  # a re-press: once
    assert _balance(till) == 80.0


def test_a_cancelled_voucher_on_a_walk_in_bill_asks_where_the_cash_comes_from(till, monkeypatch):
    """Money review 22 replaced "refuse and let go": the cash source is
    required, and the leg is never let go (the till can never bill it)."""
    vouchers = _dead_voucher(till, monkeypatch, status="CANCELLED", balance=20.0)
    till["db"].get_collection("orders").update_one({"order_id": "O7"}, {"$set": {"customer_id": "walkin-1"}})
    with pytest.raises(HTTPException) as e:
        cust_router.give_back_unbilled_leg(VKEY, _user("ADMIN"))
    assert e.value.status_code == 400 and "drawer" in e.value.detail
    v = vouchers.find_one({"code": "GV7"})
    assert v["balance"] == 20.0 and (v.get("pos_leg_owner") or {}).get(VKEY) == "GIVEBACK"


# =========================================================================== #
# 5: another tender on the same leg is a new debit (the rule applies)
# =========================================================================== #
def test_a_voucher_on_a_store_credit_legs_key_is_refused(till, redeem_env):
    _unbilled_leg(till)
    with pytest.raises(HTTPException) as e:
        cust_router.refuse_unresolved_debit("CUST-1", KEY, "GV9")
    assert e.value.status_code == 409


def test_store_credit_on_a_voucher_legs_key_is_refused(till):
    _voucher(till, balance=500.0, status="ACTIVE")
    till["db"].get_collection("orders").update_one({"order_id": "O7"}, {"$set": {"customer_id": "CUST-1"}})
    with pytest.raises(HTTPException) as e:
        cust_router.refuse_unresolved_debit("CUST-1", VKEY)
    assert e.value.status_code == 409


# =========================================================================== #
# 6: the audit names the person who gave the money back
# =========================================================================== #
def test_the_give_back_audit_names_the_user(till, redeem_env, monkeypatch):
    rows = []
    monkeypatch.setattr("api.dependencies.get_audit_repository",
                        lambda: type("A", (), {"create": lambda self, d: rows.append(d)})())
    _unbilled_leg(till)
    cust_router.give_back_unbilled_leg(KEY, _user("ADMIN"))
    audit = [r for r in rows if r["action"] == "UNBILLED_PAYMENT_GIVEN_BACK"]
    assert audit and audit[0]["user_id"] == _user("ADMIN")["user_id"] != "system"
