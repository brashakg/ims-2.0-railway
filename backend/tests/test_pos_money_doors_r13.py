"""
IMS 2.0 - POS money doors, round-13 money review of PR #1264 (head 99ec2fee)
============================================================================
Money that moved is never released by a later error; a held refund nobody
owns raises a task and can be finished (nothing paid included); a till leg
that debits an account is keyed at the debit and never auto-re-sent; the
recovered record is dated when the money moved. No emoji (cp1252).
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
from api.services import store_credit_ledger as scl  # noqa: E402
from tests.strict_fakes import StrictDB  # noqa: E402
from tests.test_pos_money_doors import (  # noqa: E402,F401
    STORE,
    _advance_client,
    _hdr,
    _ledger,
    _order,
    _stored,
    _user,
    returns_router,
    till,
)
from tests.test_pos_money_doors_r8 import _two_unit_bill  # noqa: E402
from tests.test_pos_money_doors_r9 import _ago, _balance, _customer, numbered  # noqa: E402,F401
from tests.test_pos_money_doors_r10 import _run_main  # noqa: E402
from tests.test_pos_money_doors_r12 import _repo, _row, _spec, tasks  # noqa: E402,F401
from tests import test_refund_review_unpaid_credit as rr  # noqa: E402
from tests.test_refund_review_unpaid_credit import env  # noqa: E402,F401


def _bill_with_hold(till, rid="RET-H", **hold):
    h = {"amount": 1800.0, "at": _ago(30), "kept": True, "store_id": STORE,
         "lines": [{"item_id": "li1", "product_id": "P1", "qty": 1.0}], "note_number": "TST01/C2627/0031"}
    h.update(hold)
    till["db"].get_collection("orders").insert_one(_order(status="DELIVERED", refund_holds={rid: h},
                                                          refund_holds_v=1))


def _complete(rid, user=None, **answer):
    return asyncio.run(returns_router.complete_return_record(
        rid, returns_router.CompleteRecordBody(**answer), user or _user("ACCOUNTANT")))


# =========================================================================== #
# BLOCK 1: money out -> a later error releases neither the hold nor the approval
# =========================================================================== #
@pytest.mark.usefixtures("gst_numbers")
def test_an_error_after_the_credit_lands_keeps_the_hold_and_the_approval(till, monkeypatch):
    payload = _two_unit_bill(till, monkeypatch)
    payload["return_type"] = "CREDIT_NOTE"
    _customer(till)
    released = []
    monkeypatch.setattr(returns_router, "_gate_refund_approval_matrix",
                        lambda *a, **k: {"approval_request_id": "REQ-9", "approval_by": "M1",
                                         "approval_status": "APPROVED"})
    from api.services import approvals

    monkeypatch.setattr(approvals.ApprovalEngine, "release_consumption",
                        lambda self, rid, **k: released.append(rid), raising=False)

    def _boom(*a, **k):
        raise RuntimeError("quarantine store down")

    monkeypatch.setattr(returns_router, "_quarantine_damaged_units", _boom)
    with pytest.raises(RuntimeError):
        _advance_client().post("/api/v1/returns", json=payload, headers=_hdr("STORE_MANAGER"))
    assert _balance(till) == 1800.0  # the credit landed
    holds = _stored(till).get("refund_holds") or {}
    assert holds and all(h.get("kept") for h in holds.values()), holds
    assert released == []  # the spent approval is not usable again


# =========================================================================== #
# BLOCK 2: a held refund no one owns
# =========================================================================== #
def test_a_stuck_hold_with_no_credit_line_raises_one_p1_task(till, tasks):
    _bill_with_hold(till, "RET-S")
    assert returns_router.sweep_stuck_return_holds() == 1
    assert [t["dedupe_ref"] for t in tasks] == ["return_hold_stuck:RET-S"]
    assert tasks[0]["priority"] == "P1" and tasks[0]["extra"]["link"].endswith("return=RET-S")
    # Nothing released by itself: the hold stays.
    assert "RET-S" in (_stored(till).get("refund_holds") or {})


def test_the_sweep_skips_young_holds_and_holds_another_owner_has(till, tasks):
    till["db"].get_collection("orders").insert_one(_order(order_id="ORD-2", refund_holds={
        "RET-Y": {"amount": 100.0, "at": _ago(1), "kept": True},                    # too young
        "RET-I": {"amount": 100.0, "at": _ago(30), "kept": True},                   # its credit landed
        "RET-D": {"amount": 100.0, "at": _ago(30), "kept": True, "doc": {"x": 1}},  # its own task
        "RET-V": {"amount": 100.0, "at": _ago(30), "kept": True},                   # a VOID line
    }, refund_holds_v=1))
    _ledger(till).insert_one(_row("RET-I:credit", type=scl.ISSUED, applied=True))
    _ledger(till).insert_one(_row("RET-V:credit", type="VOID", delta=0.0, void_delta=200.0))
    assert returns_router.sweep_stuck_return_holds() == 1
    assert [t["dedupe_ref"] for t in tasks] == ["return_hold_stuck:RET-V"]


def test_nothing_paid_gives_the_claims_hold_and_number_back_once(till, monkeypatch, tasks):
    released, written_off = [], []
    monkeypatch.setattr(returns_router, "_release_returnable_qty", lambda *a, **k: released.append(a) or True)
    monkeypatch.setattr(returns_router, "_record_unused_credit_note", lambda n, *a, **k: written_off.append(n))
    _bill_with_hold(till, "RET-N")
    with pytest.raises(HTTPException) as e:
        returns_router._refuse_while_a_return_settles(_stored(till), "ORD-1")
    assert "never finished" in e.value.detail and "paid out" not in e.value.detail
    with pytest.raises(HTTPException) as cash:  # nothing moved: no cash answer
        _complete("RET-N", cash_leg_paid=True)
    assert cash.value.status_code == 409
    out = _complete("RET-N", nothing_paid=True)
    assert out["status"] == "NOTHING_PAID"
    assert len(released) == 1 and written_off == ["TST01/C2627/0031"]
    assert "RET-N" not in (_stored(till).get("refund_holds") or {})
    again = _complete("RET-N", nothing_paid=True)
    assert again.get("replayed") is True and len(released) == 1 and written_off == ["TST01/C2627/0031"]
    returns_router._refuse_while_a_return_settles(_stored(till), "ORD-1")  # the bill is free


def test_nothing_paid_is_refused_once_store_credit_landed(till):
    _bill_with_hold(till, "RET-L")
    _ledger(till).insert_one(_row("RET-L:credit", type=scl.ISSUED, applied=True, order_id="ORD-1",
                                  undo_on_void=_spec("RET-L")))
    with pytest.raises(HTTPException) as e:
        _complete("RET-L", nothing_paid=True)
    assert e.value.status_code == 409 and "Money moved on this return" in e.value.detail


def test_a_void_line_held_bill_is_finished_without_releasing_twice(till, monkeypatch):
    released = []
    monkeypatch.setattr(returns_router, "_release_returnable_qty", lambda *a, **k: released.append(a) or True)
    monkeypatch.setattr(returns_router, "_record_unused_credit_note", lambda *a, **k: None)
    _bill_with_hold(till, "RET-W")
    _ledger(till).insert_one(_row("RET-W:credit", type="VOID", delta=0.0, void_delta=200.0, claims_released=True,
                                  claims_released_done=True,  # round 15: the void's act stamped done
                                  number_written_off=True, void_settled=False, undo_on_void=_spec("RET-W")))
    with pytest.raises(HTTPException) as e:
        returns_router._refuse_while_a_return_settles(_stored(till), "ORD-1")
    assert "never finished" in e.value.detail
    _complete("RET-W", nothing_paid=True)
    assert released == []  # the void already gave the units back
    assert _ledger(till).find_one({"entry_id": "RET-W:credit"})["void_settled"] is True


# =========================================================================== #
# BLOCK 3: a till leg that debits an account is keyed at the debit
# =========================================================================== #
def _lost_record_once(monkeypatch, repo):
    real = repo.add_payment
    state = {"n": 0}

    def _add(*a, **k):
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("reply lost before the payment row")
        return real(*a, **k)

    monkeypatch.setattr(repo, "add_payment", _add)


@pytest.mark.asyncio
async def test_a_re_sent_store_credit_leg_debits_once(till, monkeypatch):
    from api.routers.orders import PaymentCreate, add_payment

    till["db"].get_collection("orders").insert_one(_order())
    _customer(till, store_credit=1000.0)
    monkeypatch.setattr("api.routers.customers.get_customer_repository", lambda: _repo(till))
    _lost_record_once(monkeypatch, till["orders"])
    with pytest.raises(Exception):
        await add_payment("ORD-1", PaymentCreate(method="STORE_CREDIT", amount=300.0), _user("CASHIER"), "leg-k")
    assert _balance(till) == 700.0
    await add_payment("ORD-1", PaymentCreate(method="STORE_CREDIT", amount=300.0), _user("CASHIER"), "leg-k")
    assert _balance(till) == 700.0  # never twice
    assert len(_stored(till)["payments"]) == 1
    assert _ledger(till).count_documents({"entry_id": "POSPAY:ORD-1:leg-k"}) == 1


@pytest.mark.asyncio
async def test_a_re_sent_voucher_leg_takes_the_card_once(till, monkeypatch):
    from api.routers.orders import PaymentCreate, add_payment

    till["db"].get_collection("orders").insert_one(_order())
    vouchers = till["db"].get_collection("gift_vouchers")
    vouchers.insert_one({"code": "GV1", "balance": 1000.0, "status": "ACTIVE", "expiry_date": None})
    monkeypatch.setattr("api.dependencies.get_seeded_db", lambda: till["db"], raising=False)
    from api.routers import vouchers as V
    from api.services import money_guard

    monkeypatch.setattr(V, "_coll", lambda db: vouchers)
    # StrictDB cannot apply a positional "$" update (the cosmetic balance_after
    # stamp on the voucher's ledger entry); real Mongo does. The guard itself
    # (the key in the filter and in the same write) is the real code.
    monkeypatch.setattr(money_guard, "_stamp_balance_after", lambda *a, **k: None)
    _lost_record_once(monkeypatch, till["orders"])
    body = PaymentCreate(method="GIFT_VOUCHER", amount=300.0, voucher_code="GV1")
    with pytest.raises(Exception):
        await add_payment("ORD-1", body, _user("CASHIER"), "leg-v")
    await add_payment("ORD-1", body, _user("CASHIER"), "leg-v")
    assert vouchers.find_one({"code": "GV1"})["balance"] == 700.0
    assert len(_stored(till)["payments"]) == 1


@pytest.mark.asyncio
async def test_a_loyalty_twin_that_recorded_is_replayed(till):
    from api.routers import loyalty as L
    from api.routers.orders import PaymentCreate, add_payment

    till["db"].get_collection("orders").insert_one(_order())
    till["db"].get_collection("loyalty_accounts").insert_one(
        {"customer_id": "CUST-1", "balance_points": 500, "lifetime_earned": 500, "tier": "BRONZE"})
    burned = await L.redeem(L.RedeemRequest(customer_id="CUST-1", order_id="ORD-1", points=300), _user("CASHIER"))
    # The twin claimed the burn under this key AND its payment row is on the
    # order (recorded without the key, so the early replay misses it).
    till["db"].get_collection("loyalty_transactions").update_one(
        {"txn_id": burned["txn_id"]}, {"$set": {"tender_payment_id": "pay-1", "tender_idempotency_key": "loy-k"}})
    till["db"].get_collection("orders").update_one({"order_id": "ORD-1"}, {"$set": {"payments": [
        {"payment_id": "pay-1", "method": "LOYALTY", "amount": 300.0}]}})
    again = await add_payment("ORD-1", PaymentCreate(method="LOYALTY", amount=300.0), _user("CASHIER"), "loy-k")
    assert again["payment_id"] == "pay-1" and again["_idempotent_replay"] is True
    assert len(_stored(till)["payments"]) == 1


# =========================================================================== #
# BLOCK 4: the recovered record is dated when the money moved
# =========================================================================== #
def test_a_recovered_record_is_dated_when_its_credit_landed(till, tasks):
    moved = cust_router.utc_now_iso()
    _bill_with_hold(till, "RET-T")
    _ledger(till).insert_one(_row("RET-T:credit", type=scl.ISSUED, applied=True, created_at=moved, order_id="ORD-1",
                                  undo_on_void=_spec("RET-T"), store_id=STORE))
    doc = _complete("RET-T", cash_leg_paid=True)
    assert doc["created_at"] == moved and not doc["recovered"].get("redated_from")


def test_a_recovered_record_in_a_locked_month_is_dated_the_first_open_day_and_flagged(till, monkeypatch, tasks):
    moved = cust_router.utc_now_iso()
    month = cust_router._month_of(moved)
    till["db"].get_collection("period_locks").insert_one({"month": int(month[5:7]), "year": int(month[:4])})
    _bill_with_hold(till, "RET-K")
    _ledger(till).insert_one(_row("RET-K:credit", type=scl.ISSUED, applied=True, created_at=moved, order_id="ORD-1",
                                  undo_on_void=_spec("RET-K"), store_id=STORE))
    doc = _complete("RET-K", cash_leg_paid=True)
    assert doc["recovered"]["redated_from"] == moved and doc["created_at"] != moved


# =========================================================================== #
# SHOULD-FIX 6 / 7: store scope; an unpaid cash part is OWED, with a task
# =========================================================================== #
def test_complete_record_refuses_a_caller_from_another_store(till):
    _bill_with_hold(till, "RET-O", store_id="BV-OTHER-09")
    with pytest.raises(HTTPException) as e:
        _complete("RET-O", nothing_paid=True)
    assert e.value.status_code == 404  # round 14: in_reach_or_404 (no leak of another shop's return)


def test_an_unpaid_cash_part_is_owed_and_raises_a_pay_task(till, tasks):
    _bill_with_hold(till, "RET-P")
    _ledger(till).insert_one(_row("RET-P:credit", type=scl.ISSUED, applied=True, delta=1200.0, order_id="ORD-1",
                                  note_number="TST01/C2627/0040", taxable=1525.42, tax=274.58, gst_rate=18.0,
                                  undo_on_void=_spec("RET-P"), store_id=STORE))
    doc = _complete("RET-P", cash_leg_paid=False)
    assert doc["owed_amount"] == 600.0 and doc["net_refund"] == 1800.0
    assert [t["method"] for t in doc["refund_tenders"]] == ["STORE_CREDIT"]
    assert [(t["method"], t["status"]) for t in doc["owed_tenders"]] == [("CASH", "OWED")]
    assert round(doc["gst_breakup"]["taxable"] + doc["gst_breakup"]["tax"], 2) == doc["net_refund"]
    assert [t["dedupe_ref"] for t in tasks] == ["return_cash_owed:RET-P"]
    assert "Pay customer Rs 600.00 for return RET-P" in tasks[0]["title"]


# =========================================================================== #
# SHOULD-FIX 8 / 9 / 10 / 11 / 12
# =========================================================================== #
def test_a_credit_whose_doc_is_not_completed_still_lowers_the_ceiling(till):
    till["db"].get_collection("orders").insert_one(_order(amount_paid=3600.0))
    till["db"].get_collection("returns").insert_one({"return_id": "RET-PD", "order_id": "ORD-1", "status": "PENDING"})
    _ledger(till).insert_one(_row("X", type=scl.ISSUED, applied=True, delta=500.0, order_id="ORD-1", ref="RET-PD"))
    assert returns_router.refundable_ceiling(_stored(till)) == 3100.0


def test_an_unsettled_void_is_finished_by_the_sweep_without_repeating_a_step(till, monkeypatch):
    written_off, undone = [], []
    monkeypatch.setattr(returns_router, "_record_unused_credit_note", lambda n, *a, **k: written_off.append(n))
    state = {"n": 0}

    def _undo(spec, **k):
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("worker died mid-undo")
        if k["step"]("claims_released") and k["step"]("hold_released"):
            undone.append(spec["return_id"])
            for name in ("claims_released", "hold_released"):  # round 15: each act stamped done
                k["done"](name)
        return True

    monkeypatch.setattr(returns_router, "_undo_return_claims", _undo)
    _customer(till)
    _ledger(till).insert_one(_row("RET-U:credit", note_number="TST01/C2627/0050", undo_on_void=_spec()))
    # The void's undo dies; the same pass's sweep of unsettled VOIDs finishes
    # it -- the undo once more, the write-off NOT again.
    cust_router.reconcile_pending_credits(_repo(till), ledger=_ledger(till))
    row = _ledger(till).find_one({"entry_id": "RET-U:credit"})
    assert row["type"] == "VOID" and row["void_settled"] is True and row["number_written_off"] is True
    assert state["n"] == 2 and undone == ["RET-U"] and written_off == ["TST01/C2627/0050"]
    cust_router.reconcile_pending_credits(_repo(till), ledger=_ledger(till))  # a settled VOID is left alone
    assert state["n"] == 2


def test_a_press_never_moves_a_line_older_than_half_the_timeout(till):
    _customer(till)
    row = _row("E-OLD", pending_at=_ago(6))  # 6 minutes: the reconciler's
    _ledger(till).insert_one(row)
    assert cust_router._settle_credit_row(_repo(till), _ledger(till), dict(row)) == cust_router.UNCLEAR
    assert _balance(till) == 0.0


def test_the_mongo_client_has_bounded_timeouts(monkeypatch):
    from database import connection as C

    seen = {}

    class _Client:
        def __init__(self, uri, **kw):
            seen.update(kw)
            self.admin = type("A", (), {"command": lambda self, *a: {"ok": 1}})()

        def __getitem__(self, name):
            return None

    monkeypatch.setattr(C, "MongoClient", _Client)
    monkeypatch.setattr(C, "MONGO_AVAILABLE", True)
    conn = C.DatabaseConnection.__new__(C.DatabaseConnection)
    monkeypatch.setattr(conn, "_connected", False, raising=False)
    monkeypatch.setattr(conn, "_client", None, raising=False)
    monkeypatch.setattr(conn, "_db", None, raising=False)
    monkeypatch.setattr(conn, "_config", type("Cfg", (), {"get_uri": lambda self: "mongodb://x",
                                                          "database": "ims_2_0"})(), raising=False)
    conn.connect()
    # Round 14: no app-wide socket timeout (the money doors bound their own
    # writes); only the pool wait is bounded.
    assert "socketTimeoutMS" not in seen and seen.get("waitQueueTimeoutMS") == 30_000


def test_a_double_clicked_redeem_whose_row_exists_returns_it(till, monkeypatch):
    _customer(till, store_credit=700.0, store_credit_refs=["MANUAL-REDEEM:CUST-1:d1"])
    ledger = _ledger(till)
    ledger.insert_one({"_id": "MANUAL-REDEEM:CUST-1:d1", "entry_id": "MANUAL-REDEEM:CUST-1:d1",
                       "customer_id": "CUST-1", "type": "REDEEMED", "amount": 300.0, "delta": -300.0})
    real_find = ledger.find_one
    state = {"n": 0}

    def _find(flt, *a, **k):
        state["n"] += 1
        if state["n"] == 1:
            return None  # read just before the other click wrote its row
        return real_find(flt, *a, **k)

    monkeypatch.setattr(ledger, "find_one", _find)
    out = cust_router._redeem_replay(_repo(till), ledger, "CUST-1", 300.0, "MANUAL-REDEEM:CUST-1:d1",
                                     "", None, STORE, "U1")
    assert out["replayed"] is True and out["entry"]["entry_id"] == "MANUAL-REDEEM:CUST-1:d1"
    assert ledger.count_documents({"entry_id": "MANUAL-REDEEM:CUST-1:d1"}) == 1


def test_a_shopify_refund_counts_a_kept_hold_without_its_record(env):
    from api.services import shopify_refund as sr

    # A Rs 1,000 return: Rs 600 cash + Rs 400 store credit; its record never landed.
    order = rr._order(env, refund_holds={"RET-K": {"amount": 1000.0, "kept": True, "at": _ago(30)}},
                      refund_holds_v=1)
    env.get_collection("credit_note_ledger").insert_one({"entry_id": "RET-K:credit", "customer_id": "CUST-1",
                                                         "type": "ISSUED", "delta": 400.0, "order_id": "ord-1",
                                                         "ref": "RET-K", "applied": True})
    back = sr._given_back_elsewhere(env, order, "700001")
    assert back["given"] == 1000.0  # the whole hold, and its own credit line not again


# =========================================================================== #
# NITs
# =========================================================================== #
def test_a_press_refused_on_another_presss_row_writes_its_own_number_off(till, monkeypatch):
    written_off = []
    monkeypatch.setattr(returns_router, "_record_unused_credit_note", lambda n, *a, **k: written_off.append(n))
    _customer(till)
    _ledger(till).insert_one(_row("ORD-1:sacn:x", type="VOID", delta=0.0, void_delta=500.0,
                                  note_number="TST01/C2627/0060"))
    out = returns_router._issue_store_credit("CUST-1", 500.0, reason="r", ref="TST01/C2627/0061",
                                             current_user=_user("SUPERADMIN"), store_id=STORE, order_id="ORD-1",
                                             note_number="TST01/C2627/0061", entry_id="ORD-1:sacn:x")
    assert out is None and written_off == ["TST01/C2627/0061"]


def test_a_resumed_press_that_cannot_read_the_row_claims_no_number(till, monkeypatch):
    written_off = []
    monkeypatch.setattr(returns_router, "_record_unused_credit_note", lambda n, *a, **k: written_off.append(n))
    _customer(till)
    ledger = _ledger(till)
    ledger.insert_one(_row("ORD-1:sacn:y", delta=500.0, amount=500.0, note_number="TST01/C2627/0070",
                           pending_at=_ago(0)))
    real_apply, real_find = cust_router.apply_store_credit_delta, ledger.find_one
    state = {"down": False}

    def _apply(*a, **k):
        out = real_apply(*a, **k)
        state["down"] = True
        return out

    def _find(flt, *a, **k):
        if state["down"]:
            raise RuntimeError("down")
        return real_find(flt, *a, **k)

    monkeypatch.setattr(cust_router, "apply_store_credit_delta", _apply)
    monkeypatch.setattr(ledger, "find_one", _find)
    out = returns_router._issue_store_credit("CUST-1", 500.0, reason="r", ref="TST01/C2627/0071",
                                             current_user=_user("SUPERADMIN"), store_id=STORE, order_id="ORD-1",
                                             note_number="TST01/C2627/0071", entry_id="ORD-1:sacn:y")
    assert out["resumed"] is True and out["note_number"] is None and written_off == ["TST01/C2627/0071"]


@pytest.mark.usefixtures("gst_numbers")
def test_the_round_off_refunded_is_stored_scaled_for_a_fee(monkeypatch):
    from tests.test_returns_gst_refund import _qa_payload
    from tests.test_returns_refund_cap import _ctx

    client, returns = _ctx(monkeypatch, amount_paid=2000.0, grand_total=1405.0, round_off=0.8)
    r = client.post("/api/v1/returns", json=_qa_payload(restocking_fee=105.0,
                                                        refund_tenders=[{"method": "UPI", "amount": 1300.0}]),
                    headers=_hdr("ADMIN"))
    assert r.status_code == 201, r.text
    doc = returns.docs[0]
    assert doc["net_refund"] == 1300.0 and doc["round_off"] == round(0.8 * 1300.0 / 1405.0, 2)


@pytest.mark.usefixtures("gst_numbers")
def test_the_quote_refuses_a_shipping_line_like_the_create(monkeypatch):
    from tests.test_returns_gst_refund import _qa_payload
    from tests.test_returns_refund_cap import _ctx

    client, _ = _ctx(monkeypatch, amount_paid=2000.0, items=[{
        "item_id": "li1", "product_id": "PRD-1", "product_name": "Shipping", "sku": "SHIP", "quantity": 1,
        "unit_price": 1190, "gst_rate": 18.0, "item_total": 1190, "item_type": "SHIPPING"}])
    r = client.post("/api/v1/returns/quote", json=_qa_payload(), headers=_hdr("ADMIN"))
    assert r.status_code == 400 and "Shipping charges are not returned at the counter" in r.text


def test_the_scheduler_registers_both_jobs_on_apscheduler(monkeypatch):
    from agents import scheduler as S

    monkeypatch.setattr(S, "APSCHEDULER_AVAILABLE", True)
    added = []

    class _Sched:
        running = True

        def add_job(self, fn, trigger=None, id=None, **k):
            added.append(id)

        def get_jobs(self):
            return added

    if not hasattr(S, "IntervalTrigger"):
        monkeypatch.setattr(S, "IntervalTrigger", lambda **k: None, raising=False)
    sch = S.AgentScheduler.__new__(S.AgentScheduler)
    sch._config_manager = type("C", (), {"get_all_configs": lambda self: []})()
    sch._agents, sch._fallback_tasks, sch._scheduler = {}, {}, _Sched()
    sch._schedule_jobs()
    assert "store_credit_reconcile" in added and "stock_claim_sweep" in added


def test_the_backfill_reads_only_finite_numbers(monkeypatch):
    from scripts import backfill_store_credit_field as bf

    for bad in ("100", True, float("inf")):
        with pytest.raises(ValueError):
            bf._num(bad)
    assert bf._num(None) == 0.0 and bf._num(12) == 12.0
    db = StrictDB()
    db["customers"].insert_one({"customer_id": "CUST-1"})
    db["credit_note_ledger"].insert_one({"customer_id": "CUST-1", "entry_id": "s", "delta": "100"})
    rc, text = _run_main(monkeypatch, bf, db, [])
    assert rc == 2 and "[BAD-DELTA]" in text


def test_the_hand_fix_sets_only_a_missing_or_broken_balance(monkeypatch, tmp_path):
    from scripts import backfill_store_credit_field as bf

    db = StrictDB()
    db["customers"].insert_one({"customer_id": "CUST-1", "store_credit": "abc"})
    db["customers"].insert_one({"customer_id": "CUST-2", "store_credit": 50.0})
    rc, text = _run_main(monkeypatch, bf, db, ["--set", "CUST-1=120", "--set", "CUST-2=999",
                                               "--backup", str(tmp_path / "h.json")])
    assert rc == 0 and "[SET] 1 customer(s)" in text and "[SET-REFUSED] CUST-2" in text
    assert db["customers"].find_one({"customer_id": "CUST-1"})["store_credit"] == 120.0
    assert db["customers"].find_one({"customer_id": "CUST-2"})["store_credit"] == 50.0


def test_two_nothing_paid_presses_release_once(till, monkeypatch):
    released = []
    monkeypatch.setattr(returns_router, "_release_returnable_qty", lambda *a, **k: released.append(a) or True)
    monkeypatch.setattr(returns_router, "_record_unused_credit_note", lambda *a, **k: None)
    _bill_with_hold(till, "RET-2")
    _complete("RET-2", nothing_paid=True)
    returns = till["db"].get_collection("returns")
    real = returns.find_one
    state = {"n": 0}

    def _find(flt, *a, **k):
        state["n"] += 1
        return None if state["n"] == 1 else real(flt, *a, **k)  # passed the first check together

    monkeypatch.setattr(returns, "find_one", _find)
    till["db"].get_collection("orders").update_one({"order_id": "ORD-1"}, {"$set": {"refund_holds": {
        "RET-2": {"amount": 1800.0, "at": _ago(30), "kept": True, "store_id": STORE,
                  "lines": [{"item_id": "li1", "qty": 1.0}]}}}})
    out = _complete("RET-2", nothing_paid=True)
    assert out.get("replayed") is True and len(released) == 1


def test_a_resumed_undo_never_gives_claims_back_twice(till, monkeypatch):
    released, holds = [], []
    monkeypatch.setattr(returns_router, "_release_returnable_qty", lambda *a, **k: released.append(a) or True)
    monkeypatch.setattr(returns_router, "_release_order_refund", lambda *a, **k: holds.append(a) or True)
    # Round 14: each step acts only when its claim was won (claims already done).
    claimed = {"claims_released": False, "hold_released": True}
    returns_router._undo_return_claims(_spec("RET-Z"), step=lambda name: claimed[name])
    assert released == [] and len(holds) == 1


@pytest.mark.asyncio
async def test_a_dead_loyalty_twin_is_freed_and_this_press_records_once(till):
    from api.routers import loyalty as L
    from api.routers.orders import PaymentCreate, add_payment

    till["db"].get_collection("orders").insert_one(_order())
    till["db"].get_collection("loyalty_accounts").insert_one(
        {"customer_id": "CUST-1", "balance_points": 500, "lifetime_earned": 500, "tier": "BRONZE"})
    burned = await L.redeem(L.RedeemRequest(customer_id="CUST-1", order_id="ORD-1", points=300), _user("CASHIER"))
    till["db"].get_collection("loyalty_transactions").update_one(
        {"txn_id": burned["txn_id"]}, {"$set": {"tender_payment_id": "pay-dead", "tender_idempotency_key": "loy-d"}})
    out = await add_payment("ORD-1", PaymentCreate(method="LOYALTY", amount=300.0), _user("CASHIER"), "loy-d")
    assert out["payment_id"] != "pay-dead" and not out.get("_idempotent_replay")
    assert [p["payment_id"] for p in _stored(till)["payments"]] == [out["payment_id"]]


def test_the_scheduled_tick_also_sweeps_stuck_holds(monkeypatch):
    seen = []

    async def _to_thread(fn, *a, **k):
        seen.append(fn.__name__)

    monkeypatch.setattr(asyncio, "to_thread", _to_thread)
    asyncio.run(cust_router.scheduled_credit_reconcile())
    assert seen == ["reconcile_pending_credits", "sweep_stuck_return_holds"]
