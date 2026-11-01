"""
IMS 2.0 - POS money doors, round-17 money review of PR #1264 (head 359aee53)
============================================================================
ONE owner per redemption row (a press never debits a row the reconciler
owns; a VOID is answered truthfully); a till store-credit debit that never
reached its bill raises a task; the counter keeps its hold WITH its facts;
unreadable marks are unknown; each release is exactly-once by its claim
token; the order door's still-saving wiring end to end. No emoji (cp1252).
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
from tests.test_pos_money_doors import (  # noqa: E402,F401
    _advance_client,
    _hdr,
    _ledger,
    _stored,
    returns_router,
    till,
)
from tests.test_pos_money_doors_r8 import _two_unit_bill  # noqa: E402
from tests.test_pos_money_doors_r9 import _ago, _customer  # noqa: E402
from tests.test_pos_money_doors_r12 import _repo, tasks  # noqa: E402,F401
from tests.test_pos_money_doors_r13 import _bill_with_hold, _complete  # noqa: E402
from tests.test_pos_money_doors_r15 import _boom_at_money_out, _refs  # noqa: E402
from tests.test_orders_hardening import hardening_orders  # noqa: E402,F401
from tests.strict_fakes import StrictCollection  # noqa: E402

KEY = "POSPAY:O1:k:leg:0"


def _balance(till):
    return till["db"].get_collection("customers").find_one({"customer_id": "CUST-1"})["store_credit"]


def _redeem(amount=100.0):
    return cust_router.redeem_store_credit_atomic("CUST-1", amount, reason="r", ref="O1", guard_ref=KEY,
                                                  source="POS_PAYMENT")


@pytest.fixture()
def redeem_env(till, monkeypatch):
    _customer(till, store_credit=500.0)
    repo = _repo(till)
    monkeypatch.setattr(cust_router, "get_customer_repository", lambda: repo)
    return repo


def _dead_first_debit(monkeypatch, repo, then=None):
    real = type(repo).try_debit_store_credit
    state = {"n": 0}

    def _debit(self, cid, amt, guard_ref=None):
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("socket closed before send")  # never landed
        if then:
            then()
        return real(self, cid, amt, guard_ref=guard_ref)

    monkeypatch.setattr(type(repo), "try_debit_store_credit", _debit)


# =========================================================================== #
# A: ONE owner per redemption row
# =========================================================================== #
def test_a_re_press_never_debits_a_row_the_reconciler_owns(till, redeem_env, monkeypatch):
    _dead_first_debit(monkeypatch, redeem_env)
    with pytest.raises(HTTPException):
        _redeem()
    _ledger(till).update_one({"entry_id": KEY}, {"$set": {"pending_at": _ago(6)}})  # past half the timeout
    with pytest.raises(HTTPException) as e:
        _redeem()
    assert e.value.status_code == 503 and _balance(till) == 500.0  # left to the reconciler: no debit
    cust_router.reconcile_pending_credits(redeem_env, ledger=_ledger(till), older_than_s=0)
    assert _ledger(till).find_one({"entry_id": KEY})["type"] == "VOID"
    with pytest.raises(HTTPException) as e3:
        _redeem()
    assert e3.value.status_code == 409 and "nothing was taken" in e3.value.detail and _balance(till) == 500.0


def test_a_debit_racing_the_reconciler_heals_the_line_and_answers_done(till, redeem_env, monkeypatch):
    # The reconciler voids the row in the gap between the press's guard read
    # and its debit: the debit lands on a VOID -- the line is healed, the
    # press is answered as done, and the balance and the ledger agree.
    _dead_first_debit(monkeypatch, redeem_env, then=lambda: cust_router.reconcile_pending_credits(
        redeem_env, ledger=_ledger(till), older_than_s=0))
    with pytest.raises(HTTPException):
        _redeem()
    out = _redeem()
    row = _ledger(till).find_one({"entry_id": KEY})
    assert _balance(till) == 400.0 and row["type"] == "REDEEMED" and row["delta"] == -100.0
    assert out["entry"]["type"] == "REDEEMED"
    again = _redeem()  # a later press replays the same line, never a second debit
    assert again["entry"]["type"] == "REDEEMED" and _balance(till) == 400.0


def test_a_same_key_press_on_a_refused_redemption_is_told_nothing_was_taken(till, redeem_env, monkeypatch):
    ledger = _ledger(till)
    # Press A refused (insufficient) left a VOID line; press B read no prior row.
    ledger.insert_one({"_id": KEY, "entry_id": KEY, "customer_id": "CUST-1", "type": "VOID", "amount": 100.0,
                       "delta": 0.0, "void_delta": -100.0, "void_settled": False, "pending_at": _ago(0)})
    real = ledger.find_one
    state = {"n": 0}

    def _find(flt, *a, **k):
        state["n"] += 1
        return None if state["n"] == 1 else real(flt, *a, **k)

    monkeypatch.setattr(ledger, "find_one", _find)
    with pytest.raises(HTTPException) as e:
        _redeem()
    assert e.value.status_code == 409 and "nothing was taken" in e.value.detail and _balance(till) == 500.0


def test_the_replay_door_never_answers_a_void_as_success(till, redeem_env):
    _ledger(till).insert_one({"_id": KEY, "entry_id": KEY, "customer_id": "CUST-1", "type": "VOID",
                              "amount": 100.0, "delta": 0.0})
    with pytest.raises(HTTPException) as e:
        cust_router._redeem_replay(redeem_env, _ledger(till), "CUST-1", 100.0, KEY, "r", "O1", None, None)
    assert e.value.status_code == 409


# =========================================================================== #
# B: a till debit that never reached its bill raises ONE task
# =========================================================================== #
def test_a_pos_redemption_missing_from_its_bill_raises_one_task(till, redeem_env, monkeypatch, tasks):
    from tests.test_pos_money_doors import _order

    till["db"].get_collection("orders").insert_one(_order(order_id="O1", payments=[]))
    monkeypatch.setattr("api.dependencies.get_order_repository", lambda: till["orders"], raising=False)
    _redeem()  # the debit and its line landed; add_payment's answer never came back
    _ledger(till).update_one({"entry_id": KEY}, {"$set": {"pending_at": _ago(30)}})
    for _ in range(2):
        cust_router.reconcile_pending_credits(redeem_env, ledger=_ledger(till))
    assert _refs(tasks) == [f"store_credit_unbilled:{KEY}"]


def test_a_pos_redemption_on_its_bill_raises_nothing(till, redeem_env, monkeypatch, tasks):
    from tests.test_pos_money_doors import _order

    till["db"].get_collection("orders").insert_one(_order(order_id="O1", payments=[
        {"payment_id": "p1", "method": "STORE_CREDIT", "amount": 100.0, "idempotency_key": "k:leg:0"}]))
    monkeypatch.setattr("api.dependencies.get_order_repository", lambda: till["orders"], raising=False)
    _redeem()
    _ledger(till).update_one({"entry_id": KEY}, {"$set": {"pending_at": _ago(30)}})
    cust_router.reconcile_pending_credits(redeem_env, ledger=_ledger(till))
    assert tasks == [] and _ledger(till).find_one({"entry_id": KEY})["bill_checked"] is True


# =========================================================================== #
# C: the counter keeps its hold WITH its facts; no facts is never "done"
# =========================================================================== #
@pytest.mark.usefixtures("gst_numbers")
def test_the_counter_keeps_its_hold_with_the_lines_and_the_number(till, monkeypatch, tasks):
    payload = _two_unit_bill(till, monkeypatch)
    payload["refund_tenders"] = [{"method": "CASH", "amount": 1800.0}]
    monkeypatch.setattr(returns_router, "_release_returnable_qty", lambda *a, **k: False)  # the release is lost
    monkeypatch.setattr(returns_router, "_record_unused_credit_note", lambda *a, **k: True)
    _boom_at_money_out(monkeypatch)
    with pytest.raises(RuntimeError):
        _advance_client().post("/api/v1/returns", json=payload, headers=_hdr("STORE_MANAGER"))
    (rid, hold), = (_stored(till).get("refund_holds") or {}).items()
    assert [(ln["item_id"], ln["qty"], ln["token"]) for ln in hold["lines"]] == [("li1", 1.0, f"{rid}:0")]
    assert hold["note_number"] == "TST01/C2627/0001" and hold["kept"] is True


def test_nothing_paid_never_marks_a_release_done_without_its_lines(till, monkeypatch, tasks):
    released = []
    monkeypatch.setattr(returns_router, "_release_returnable_qty", lambda *a, **k: released.append(a) or True)
    monkeypatch.setattr(returns_router, "_record_unused_credit_note", lambda *a, **k: True)
    _bill_with_hold(till, "RET-NF", lines=None, claims_released=True, claims_released_at=_ago(30))
    _complete("RET-NF", nothing_paid=True)
    hold = (_stored(till).get("refund_holds") or {}).get("RET-NF")
    assert released == [] and hold is not None and not hold.get("claims_released_done")
    assert "return_step_lost:RET-NF:claims_released" in _refs(tasks)


# =========================================================================== #
# D: unreadable marks are UNKNOWN; each release is exactly-once by token
# =========================================================================== #
def test_unreadable_marks_never_finish_a_done_step_nor_raise_a_task(till, monkeypatch, tasks):
    _bill_with_hold(till, "RET-E", claims_released=True, claims_released_done=True,
                    claims_released_at=_ago(1))
    monkeypatch.setattr(returns_router, "_step_marks", lambda store: None)  # a read blip
    ran = []
    for finisher in (True, False):
        done = returns_router._give_back("ORD-1", "RET-E", "BV-PUN-01",
                                         {"claims_released": lambda: ran.append(1) or True},
                                         finisher=finisher, sole_owner=False)
        assert done is False
    assert ran == [] and tasks == []


def test_a_release_re_run_by_its_token_never_takes_another_returns_unit(monkeypatch):
    class _Orders:
        """Positional semantics for one bill line: returned_qty + claim tokens."""

        def __init__(self):
            self.line = {"item_id": "li1", "product_id": "P1", "quantity": 2, "returned_qty": 1.0,
                         "claim_tokens": ["RET-OTHER:0"]}

        def find_one_and_update(self, match, update, **kw):
            elem = match["items"]["$elemMatch"]
            if "claim_tokens" in elem and elem["claim_tokens"] not in self.line["claim_tokens"]:
                return None
            if "returned_qty" in elem and self.line["returned_qty"] < elem["returned_qty"]["$gte"]:
                return None
            self.line["returned_qty"] += update["$inc"]["items.$.returned_qty"]
            for tok in [update.get("$pull", {}).get("items.$.claim_tokens")]:
                if tok:
                    self.line["claim_tokens"].remove(tok)
            if update.get("$addToSet"):
                self.line["claim_tokens"].append(update["$addToSet"]["items.$.claim_tokens"])
            return {"items": [self.line]}

        def find_one(self, *a, **k):
            return {"items": [self.line]}

    orders = _Orders()
    monkeypatch.setattr(returns_router, "_orders_coll", lambda: orders)
    line = {"item_id": "li1", "product_id": "P1", "quantity": 2}
    assert returns_router._claim_returnable_qty("ORD-1", line, 1.0, token="RET-X:0")
    assert orders.line["returned_qty"] == 2.0
    lines = [{"item_id": "li1", "product_id": "P1", "qty": 1.0, "token": "RET-X:0"}]
    assert returns_router._release_lines("ORD-1", lines) is True
    assert returns_router._release_lines("ORD-1", lines) is True  # a re-run (its done stamp was lost)
    assert orders.line["returned_qty"] == 1.0 and orders.line["claim_tokens"] == ["RET-OTHER:0"]


# =========================================================================== #
# F: the order door's still-saving wiring, end to end
# =========================================================================== #
def _stock(monkeypatch, docs):
    units = StrictCollection("stock_units", docs)

    class _Stock:
        collection = units

    monkeypatch.setattr("api.dependencies.get_stock_repository", lambda: _Stock(), raising=False)
    return units


def test_the_stock_gate_answers_still_saving_for_a_same_key_press(client, auth_headers, hardening_orders,
                                                                  monkeypatch):
    from api.routers.orders import create as C
    from tests.test_orders_hardening import _draft_orders, _frame_item, _post_order_idem

    _stock(monkeypatch, [{"stock_id": "U1", "status": "SOLD", "order_id": "O-SAVING", "claim_key": "K-SAVE"}])

    def _refuse(*a, **k):
        raise HTTPException(status_code=409, detail="This unit is no longer available.")

    monkeypatch.setattr(C, "_assert_serialized_stock_available", _refuse)
    r = _post_order_idem(client, auth_headers, [_frame_item(1000.0)], key="K-SAVE")
    assert r.status_code == 409 and "SALE_STILL_SAVING" in r.text
    assert _draft_orders(hardening_orders) == []


def test_the_claim_answers_still_saving_for_a_same_key_press(client, auth_headers, hardening_orders, monkeypatch):
    from api.routers.orders import create as C
    from tests.test_orders_hardening import _draft_orders, _frame_item, _post_order_idem

    _stock(monkeypatch, [{"stock_id": "U1", "status": "SOLD", "order_id": "O-SAVING", "claim_key": "K-CLM"}])
    monkeypatch.setattr(C, "_assert_serialized_stock_available", lambda *a, **k: None)

    def _claim(*a, **k):
        raise HTTPException(status_code=409, detail="This unit is no longer available.")

    monkeypatch.setattr(C, "_claim_units", _claim)
    r = _post_order_idem(client, auth_headers, [_frame_item(1000.0)], key="K-CLM")
    assert r.status_code == 409 and "SALE_STILL_SAVING" in r.text
    assert _draft_orders(hardening_orders) == []


def test_a_press_stamps_its_key_on_the_units_it_claims(client, auth_headers, hardening_orders, monkeypatch):
    from api.routers.orders import create as C
    from tests.test_orders_hardening import _frame_item, _post_order_idem

    units = _stock(monkeypatch, [{"stock_id": "U9", "status": "AVAILABLE"}])
    monkeypatch.setattr(C, "_assert_serialized_stock_available", lambda *a, **k: None)

    def _claim(order_id, items, store_id):
        units.update_one({"stock_id": "U9"}, {"$set": {"status": "SOLD", "order_id": order_id}})
        return {"U9": None}

    monkeypatch.setattr(C, "_claim_units", _claim)
    r = _post_order_idem(client, auth_headers, [_frame_item(1000.0)], key="K-STAMP")
    assert r.status_code in (200, 201), r.text
    assert units.find_one({"stock_id": "U9"})["claim_key"] == "K-STAMP"
