"""
IMS 2.0 - POS money doors, round-24 money review of PR #1264 (head 81d8904d)
============================================================================
A press with an existing bill key replays its order only for the SAME bill:
another body is refused (409 IDEMPOTENCY_BODY_MISMATCH) and nothing new is
charged. The give-back's cash source binds only when the payout is booked; a
stored voucher decision follows a voucher that died since. Backfill backups
are never overwritten. No emoji.
"""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MONGODB_URI", "")

from fastapi import HTTPException  # noqa: E402

from api.routers import customers as cust_router  # noqa: E402
from api.routers import returns as returns_router  # noqa: E402
from tests.test_orders_hardening import _draft_orders, _frame_item, _post_order_idem, hardening_orders  # noqa: E402,F401
from tests.test_pos_money_doors import _user, till  # noqa: E402,F401
from tests.test_pos_money_doors_r12 import tasks  # noqa: E402,F401
from tests.test_pos_money_doors_r17 import _balance  # noqa: E402
from tests.test_pos_money_doors_r20 import VKEY  # noqa: E402
from tests.test_pos_money_doors_r21 import _dead_voucher  # noqa: E402
from tests.test_pos_money_doors_r22 import _payouts, _walk_in_dead  # noqa: E402
from tests.test_pos_money_doors_r23 import _credits, _set_customer  # noqa: E402


# =========================================================================== #
# 1: one key replays only the SAME bill
# =========================================================================== #
def test_an_edited_bill_under_the_same_key_is_refused(client, auth_headers, hardening_orders):
    r1 = _post_order_idem(client, auth_headers, [_frame_item(1000.0)], key="K-E")
    assert r1.status_code in (200, 201), r1.text
    r2 = _post_order_idem(client, auth_headers, [_frame_item(1000.0, quantity=2)], key="K-E")
    assert r2.status_code == 409, r2.text
    detail = r2.json()["detail"]
    assert detail["code"] == "IDEMPOTENCY_BODY_MISMATCH"
    assert r1.json()["order_number"] in detail["message"] and "nothing new was charged" in detail["message"]
    assert len(_draft_orders(hardening_orders)) == 1


def test_the_same_bill_pressed_again_replays_even_with_a_new_date_or_note(client, auth_headers, hardening_orders):
    r1 = _post_order_idem(client, auth_headers, [_frame_item(1000.0)], key="K-S", notes="a")
    r2 = _post_order_idem(client, auth_headers, [_frame_item(1000.0)], key="K-S", notes="b",
                          delivery_date=(date.today() + timedelta(days=10)).isoformat())
    assert r2.status_code in (200, 201), r2.text
    assert r2.json()["order_id"] == r1.json()["order_id"]
    assert len(_draft_orders(hardening_orders)) == 1


# =========================================================================== #
# 3: the cash source binds at booking; a dead voucher is re-checked
# =========================================================================== #
def test_after_the_drawer_is_refused_hq_can_be_chosen(till, monkeypatch, tasks):
    _walk_in_dead(till, monkeypatch)
    monkeypatch.setattr(returns_router, "_drawer_closed_today", lambda store_id: True)  # Day-End counted
    with pytest.raises(HTTPException) as e:
        cust_router.give_back_unbilled_leg(VKEY, _user("ADMIN"), "STORE_DRAWER")
    assert e.value.status_code == 409 and _payouts(till) == []
    out = cust_router.give_back_unbilled_leg(VKEY, _user("ADMIN"), "HQ")  # as the 409 says
    [payout] = _payouts(till)
    assert out["given_back"] is True and payout["cash_paid_from"] == "HQ"


def test_a_stored_voucher_decision_follows_a_voucher_that_died_since(till, monkeypatch, tasks):
    from api.services import money_guard

    vouchers = _dead_voucher(till, monkeypatch, status="ACTIVE", balance=0.0, expiry_date=None)
    _set_customer(till, "CUST-1")
    real = money_guard.credit
    state = {"n": 0}

    def _refused_once(*a, **k):
        if state["n"] == 0:
            state["n"] += 1
            return money_guard.GuardResult(ok=False, balance=None, txn_id=None, reason="unavailable")
        return real(*a, **k)

    monkeypatch.setattr(money_guard, "credit", _refused_once)
    with pytest.raises(HTTPException):  # decided VOUCHER, nothing booked
        cust_router.give_back_unbilled_leg(VKEY, _user("ADMIN"))
    assert vouchers.find_one({"code": "GV7"})["pos_give_back_plan"][VKEY]["dest"] == "VOUCHER"
    vouchers.update_one({"code": "GV7"}, {"$set": {"status": "CANCELLED"}})  # the voucher died since
    out = cust_router.give_back_unbilled_leg(VKEY, _user("ADMIN"))
    assert "store credit" in out["message"] and _balance(till) == 80.0 and len(_credits(till)) == 1
    assert vouchers.find_one({"code": "GV7"})["balance"] == 0.0


# =========================================================================== #
# 6: a backfill backup is never overwritten
# =========================================================================== #
@pytest.mark.parametrize("script", ["backfill_store_credit_field", "backfill_loyalty_tender_claims"])
def test_a_backfill_never_overwrites_an_earlier_backup(tmp_path, script):
    import importlib.util

    import mongomock

    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", f"{script}.py")
    spec = importlib.util.spec_from_file_location(script, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    db = mongomock.MongoClient().db
    db["customers"].insert_one({"customer_id": "C1"})
    db["loyalty_transactions"].insert_one({"txn_id": "T1"})
    backup = tmp_path / "backup.json"
    backup.write_text("earlier run", encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        if script == "backfill_store_credit_field":
            mod.apply(db, [("C1", 50.0)], str(backup))
        else:
            mod.apply(db, [("T1", "P1")], str(backup))
    assert "already exists" in str(e.value)
    assert backup.read_text(encoding="utf-8") == "earlier run"
    assert db["customers"].find_one({"customer_id": "C1"}).get("store_credit") is None
    assert db["loyalty_transactions"].find_one({"txn_id": "T1"}).get("tender_payment_id") is None
