"""
IMS 2.0 - POS money doors, round-14 money review of PR #1264 (head d0909148)
============================================================================
One rule for a young hold; every once-only step claimed before it acts; an
error before the money moves gives the press back; Day-End refunds by the day
the cash moved (an OWED leg never, a payout on its pay day); a re-pressed sale
replays by the database's own key. No emoji (cp1252).
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
from tests.test_pos_money_doors_r13 import _bill_with_hold, _complete  # noqa: E402
from tests.strict_fakes import StrictDB  # noqa: E402
from tests.test_orders_hardening import hardening_orders  # noqa: E402,F401


# =========================================================================== #
# BLOCK 1: one young-hold rule
# =========================================================================== #
def test_a_young_hold_is_refused_everywhere(till, tasks):
    _bill_with_hold(till, "RET-Y", at=_ago(2))
    for answer in ({"nothing_paid": True}, {"cash_leg_paid": True}):
        with pytest.raises(HTTPException) as e:
            _complete("RET-Y", **answer)
        assert e.value.status_code == 409 and e.value.detail == returns_router.HOLD_STILL_RUNNING
    items = asyncio.run(returns_router.needs_completing(_user("ACCOUNTANT")))["items"]
    assert [i["return_id"] for i in items] == []
    assert returns_router.sweep_stuck_return_holds() == 0 and tasks == []


def test_one_return_has_one_doc_by_a_unique_index(monkeypatch):
    from database.connection import DatabaseConnection

    made = []

    class _Coll:
        def __init__(self, name):
            self.name = name

        def create_index(self, keys, **kw):
            made.append((self.name, keys, kw.get("unique"), kw.get("sparse"), kw.get("partialFilterExpression")))

    class _DB:
        def __getitem__(self, name):
            return _Coll(name)

    conn = DatabaseConnection.__new__(DatabaseConnection)
    monkeypatch.setattr(conn, "_connected", True, raising=False)
    monkeypatch.setattr(conn, "_db", _DB(), raising=False)
    conn.ensure_indexes()
    assert ("returns", "return_id", True, True, None) in made
    assert ("orders", [("store_id", 1), ("idempotency_key", 1)], True, None,
            {"idempotency_key": {"$type": "string"}}) in made


# =========================================================================== #
# BLOCK 2: mark-then-act for every VOID step
# =========================================================================== #
def _void(till, rid="RET-V", **over):
    row = _row(f"{rid}:credit", type="VOID", delta=0.0, void_delta=200.0, void_settled=False, void_undo=True,
               note_number="TST01/C2627/0090", undo_on_void=_spec(rid))
    row.update(over)
    _ledger(till).insert_one(row)
    return dict(_ledger(till).find_one({"entry_id": f"{rid}:credit"}))


def test_two_reconcilers_on_one_void_give_back_once(till, monkeypatch):
    released, written_off, holds = [], [], []
    monkeypatch.setattr(returns_router, "_release_returnable_qty", lambda *a, **k: released.append(a))
    monkeypatch.setattr(returns_router, "_record_unused_credit_note", lambda n, *a, **k: written_off.append(n))
    monkeypatch.setattr(returns_router, "_release_order_refund", lambda *a, **k: holds.append(a))
    row = _void(till)
    cust_router._finish_void(_ledger(till), dict(row), "x", undo=True)
    cust_router._finish_void(_ledger(till), dict(row), "x", undo=True)  # the second read the same row
    assert len(released) == 1 and written_off == ["TST01/C2627/0090"] and len(holds) == 1


def test_nothing_paid_racing_the_unsettled_void_sweep_gives_back_once(till, monkeypatch):
    released, written_off = [], []
    monkeypatch.setattr(returns_router, "_release_returnable_qty", lambda *a, **k: released.append(a))
    monkeypatch.setattr(returns_router, "_record_unused_credit_note", lambda n, *a, **k: written_off.append(n))
    _bill_with_hold(till, "RET-V")
    row = _void(till)
    _complete("RET-V", nothing_paid=True)
    cust_router._finish_void(_ledger(till), dict(row), "x", undo=True)  # the sweep read the row before
    assert len(released) == 1 and written_off == ["TST01/C2627/0090"]


def test_a_finished_record_owns_the_claims_the_void_sweep_skips_them(till, monkeypatch):
    released = []
    monkeypatch.setattr(returns_router, "_release_returnable_qty", lambda *a, **k: released.append(a))
    monkeypatch.setattr(returns_router, "_record_unused_credit_note", lambda *a, **k: None)
    till["db"].get_collection("returns").insert_one({"return_id": "RET-V", "status": "NOTHING_PAID"})
    row = _void(till)
    cust_router._finish_void(_ledger(till), dict(row), "x", undo=True)
    assert released == [] and _ledger(till).find_one({"entry_id": "RET-V:credit"})["void_settled"] is True


def test_a_lost_act_after_its_mark_is_never_repeated(till, monkeypatch):
    calls = {"n": 0}

    def _release(*a, **k):
        calls["n"] += 1
        raise RuntimeError("died after the mark")

    monkeypatch.setattr(returns_router, "_release_returnable_qty", _release)
    monkeypatch.setattr(returns_router, "_record_unused_credit_note", lambda *a, **k: None)
    monkeypatch.setattr(returns_router, "_release_order_refund", lambda *a, **k: None)
    row = _void(till)
    cust_router._finish_void(_ledger(till), dict(row), "x", undo=True)
    cust_router._finish_void(_ledger(till), dict(_ledger(till).find_one({"entry_id": "RET-V:credit"})), "x",
                             undo=True)
    assert calls["n"] == 1  # never twice; the units stay held for a person


# =========================================================================== #
# BLOCK 3: an error before the money moves gives the press back
# =========================================================================== #
@pytest.mark.usefixtures("gst_numbers")
def test_an_error_before_the_money_moves_releases_the_claims_and_writes_the_number_off(till, monkeypatch):
    payload = _two_unit_bill(till, monkeypatch)
    payload["refund_tenders"] = [{"method": "CASH", "amount": 1800.0}]
    released, written_off = [], []
    monkeypatch.setattr(returns_router, "_release_returnable_qty", lambda *a, **k: released.append(a))
    monkeypatch.setattr(returns_router, "_record_unused_credit_note", lambda n, *a, **k: written_off.append(n))

    def _boom(*a, **k):
        raise RuntimeError("quarantine store down")

    monkeypatch.setattr(returns_router, "_quarantine_damaged_units", _boom)
    with pytest.raises(RuntimeError):
        _advance_client().post("/api/v1/returns", json=payload, headers=_hdr("STORE_MANAGER"))
    assert len(released) == 1 and written_off == ["TST01/C2627/0001"]
    assert not (_stored(till).get("refund_holds") or {})


# =========================================================================== #
# BLOCK 4: the drawer day
# =========================================================================== #
def _drawer_cash_refunds(till, day_start, day_end):
    from api.routers.finance.cash_drawer_window import _cash_sales_for_window

    return _cash_sales_for_window(till["db"], STORE, day_start, day_end)[1]


def test_day_end_nets_a_refund_on_the_day_its_cash_moved(till):
    returns = till["db"].get_collection("returns")
    today = "2026-10-20"  # a fixed stored-frame day (no clock: the windows are plain strings)
    returns.insert_one({"return_id": "RET-R1", "store_id": STORE, "status": "COMPLETED", "return_type": "RETURN",
                        "created_at": f"{today}T06:00:00+00:00", "drawer_at": "2026-10-01T06:00:00+00:00",
                        "refund_tenders": [{"method": "CASH", "amount": 600.0}],
                        "owed_tenders": [{"method": "CASH", "amount": 900.0, "status": "OWED"}]})
    returns.insert_one({"return_id": "RET-R1-PAYOUT", "store_id": STORE, "status": "COMPLETED",
                        "return_type": "RETURN", "created_at": f"{today}T07:00:00+00:00",
                        "drawer_at": f"{today}T07:00:00+00:00", "net_refund": 0.0,
                        "refund_tenders": [{"method": "CASH", "amount": 250.0}]})
    # Today: only the payout made today; the recovered cash moved on 1 Oct; OWED never.
    assert _drawer_cash_refunds(till, f"{today}T00:00:00", f"{today}T23:59:59") == 250.0
    assert _drawer_cash_refunds(till, "2026-10-01T00:00:00", "2026-10-01T23:59:59") == 600.0


def test_the_tender_reconciliation_reads_the_drawer_day_too():
    from api.services.tender_reconciliation import returns_window_match

    m = returns_window_match(STORE, "2026-11-01T00:00:00", "2026-11-01T23:59:59")
    assert {"drawer_at" in c for c in m["$or"]} == {True}
    assert any(c.get("drawer_at") is None and "created_at" in c for c in m["$or"])


def test_cash_owed_now_paid_books_the_drawer_leg_once_and_closes_the_task(till, monkeypatch):
    closed = []
    monkeypatch.setattr(returns_router, "_close_task", lambda ref, *a, **k: closed.append(ref))
    returns = till["db"].get_collection("returns")
    returns.insert_one({"return_id": "RET-O", "order_id": "ORD-1", "store_id": STORE, "status": "COMPLETED",
                        "owed_amount": 600.0, "owed_tenders": [{"method": "CASH", "amount": 600.0}]})
    body = returns_router.OwedPaidBody(method="CASH")
    out = asyncio.run(returns_router.mark_owed_paid("RET-O", body, _user("ACCOUNTANT")))
    assert out["owed_paid"] == 600.0 and closed == ["return_cash_owed:RET-O"]
    again = asyncio.run(returns_router.mark_owed_paid("RET-O", body, _user("ACCOUNTANT")))
    assert again["replayed"] is True
    payouts = list(returns.find({"payout_of": "RET-O"}))
    assert len(payouts) == 1 and payouts[0]["refund_tenders"] == [{"method": "CASH", "amount": 600.0}]
    assert payouts[0]["net_refund"] == 0.0 and payouts[0]["drawer_at"]


# =========================================================================== #
# BLOCK 5 / should-fix 7: a cash-only hold is recovered from its own figures
# =========================================================================== #
def test_an_unpaid_cash_part_never_sits_on_the_drawer_legs(till, tasks):
    _bill_with_hold(till, "RET-Q")
    _ledger(till).insert_one(_row("RET-Q:credit", type=scl.ISSUED, applied=True, delta=1200.0, order_id="ORD-1",
                                  undo_on_void=_spec("RET-Q"), store_id=STORE))
    doc = _complete("RET-Q", cash_leg_paid=False)
    assert all(t["method"] != "CASH" for t in doc["refund_tenders"])
    assert [t["method"] for t in doc["owed_tenders"]] == ["CASH"]


def test_a_cash_only_hold_is_recovered_from_its_kept_figures(till, tasks):
    view = {"taxable": 1525.42, "tax": 274.58, "gross": 1800.0, "gst_rate": 18.0,
            "by_rate": {"18.0": {"taxable": 1525.42, "tax": 274.58, "gross": 1800.0}}}
    _bill_with_hold(till, "RET-C", money_out=True, tenders=[{"method": "CASH", "amount": 1700.0}],
                    net_amount=1700.0, gross_refund=1800.0, restocking_fee=100.0, round_off=0.4, gst_view=view,
                    return_type="RETURN")
    doc = _complete("RET-C", cash_leg_paid=True)
    assert doc["net_refund"] == 1700.0 and doc["gst_breakup"] == view
    assert doc["credit_note_number"] == "TST01/C2627/0031" and doc["restocking_fee"] == 100.0
    assert doc["round_off"] == 0.4 and doc["gross_refund"] == 1800.0


def test_a_hold_without_its_figures_is_not_rebuilt(till):
    _bill_with_hold(till, "RET-F", money_out=True, tenders=[{"method": "CASH", "amount": 1700.0}])
    with pytest.raises(HTTPException) as e:
        _complete("RET-F", cash_leg_paid=True)
    assert e.value.status_code == 409 and "cannot be rebuilt" in e.value.detail


@pytest.mark.usefixtures("gst_numbers")
def test_a_mixed_return_with_a_fee_stamps_gross_and_fee_on_its_credit_line(monkeypatch):
    from tests.test_returns_gst_refund import _qa_payload
    from tests.test_returns_refund_cap import _ctx

    client, returns = _ctx(monkeypatch, amount_paid=2000.0, created_at="2026-10-20T10:00:00",
                           payments=[{"payment_id": "p1", "method": "UPI", "amount": 700.0},
                                     {"payment_id": "p2", "method": "GIFT_VOUCHER", "amount": 704.2}])
    ledger = returns_router._ledger_coll()
    r = client.post("/api/v1/returns", json=_qa_payload(restocking_fee=100.0, refund_tenders=[
        {"method": "UPI", "amount": 700.0}, {"method": "STORE_CREDIT", "amount": 604.2}]), headers=_hdr("ADMIN"))
    assert r.status_code == 201, r.text
    row = [d for d in ledger.docs if d.get("type") == scl.CREDIT_NOTE_TYPE][0]
    assert row["gross_refund"] == 1404.2 and row["restocking_fee"] == 100.0


# =========================================================================== #
# SHOULD-FIX 6 / 8
# =========================================================================== #
def test_a_nothing_paid_return_no_longer_counts_and_its_replay_finishes_the_hold(till, monkeypatch):
    monkeypatch.setattr(returns_router, "_release_returnable_qty", lambda *a, **k: None)
    monkeypatch.setattr(returns_router, "_record_unused_credit_note", lambda *a, **k: None)
    _bill_with_hold(till, "RET-N")
    _complete("RET-N", nothing_paid=True)
    # The hold came back (a lost release): it does not count, nor block the bill...
    hold = {"amount": 1800.0, "at": _ago(30), "kept": True}
    till["db"].get_collection("orders").update_one({"order_id": "ORD-1"},
                                                   {"$set": {"refund_holds": {"RET-N": hold}}})
    live, _ = returns_router._live_refund_holds("ORD-1", {"RET-N": hold})
    assert live == {}
    returns_router._refuse_while_a_return_settles(_stored(till), "ORD-1")
    # ...and a replay releases it.
    _complete("RET-N", nothing_paid=True)
    assert "RET-N" not in (_stored(till).get("refund_holds") or {})


def test_another_shops_finished_record_is_never_read_back(till):
    till["db"].get_collection("returns").insert_one({"return_id": "RET-X", "store_id": "BV-OTHER-09",
                                                     "status": "COMPLETED", "net_refund": 500.0})
    with pytest.raises(HTTPException) as e:
        _complete("RET-X", cash_leg_paid=True)
    assert e.value.status_code == 404


# =========================================================================== #
# SHOULD-FIX 10 / 11 / 12
# =========================================================================== #
def test_two_creates_with_one_key_replay_the_first_order(client, auth_headers, hardening_orders, monkeypatch):
    from pymongo.errors import DuplicateKeyError

    from tests.test_orders_hardening import _draft_orders, _frame_item, _post_order_idem

    r1 = _post_order_idem(client, auth_headers, [_frame_item(1000.0)], key="K-DUP")
    assert r1.status_code in (200, 201), r1.text
    repo = hardening_orders["order_repo"]
    coll = repo.collection
    real_find_one, real_insert = repo.find_one, coll.insert_one
    state = {"n": 0}

    def _find_one(q, *a, **k):
        state["n"] += 1
        if state["n"] == 1:
            return None  # the second press passed the pre-check while the first was saving
        return real_find_one(q, *a, **k)

    def _insert(doc, *a, **k):
        if doc.get("idempotency_key") == "K-DUP":
            raise DuplicateKeyError("E11000 duplicate key error index: uniq_store_idempotency_key "
                                    "dup key: { store_id, idempotency_key: \"K-DUP\" }")
        return real_insert(doc, *a, **k)

    monkeypatch.setattr(repo, "find_one", _find_one)
    monkeypatch.setattr(coll, "insert_one", _insert)
    r2 = _post_order_idem(client, auth_headers, [_frame_item(1000.0)], key="K-DUP")
    assert r2.status_code in (200, 201), r2.text
    assert r2.json()["order_id"] == r1.json()["order_id"]
    assert len(_draft_orders(hardening_orders)) == 1


def test_an_idempotency_clash_is_never_retried_as_a_number_clash():
    from pymongo.errors import DuplicateKeyError

    from database.repositories.order_repository import OrderRepository

    tries = []

    class _Coll:
        def insert_one(self, doc):
            tries.append(doc.get("order_number"))
            raise DuplicateKeyError("E11000 duplicate key error index: uniq_store_idempotency_key")

    repo = OrderRepository(_Coll())
    with pytest.raises(DuplicateKeyError):
        repo.create_unique({"order_id": "O1", "order_number": "N1"}, number_field="order_number",
                           regenerate=lambda: "N2")
    assert tries == ["N1"]


def test_the_quote_says_whether_a_re_press_replays(monkeypatch):
    from api.routers.orders import create as C
    from database import connection as dbc

    for flag in (True, False):
        monkeypatch.setattr(dbc, "ORDER_REPLAY_GUARANTEED", flag)
        body = C.BillQuoteRequest(items=[{"item_type": "FRAME", "category": "FRAME", "unit_price": 1000.0,
                                          "quantity": 1, "discount_percent": 0}])
        out = asyncio.run(C.quote_bill(body, _user("STORE_MANAGER")))
        assert out["replay_guaranteed"] is flag


def test_the_money_doors_bound_their_own_writes(till, monkeypatch):
    import pymongo

    seen = []
    real = pymongo.timeout

    def _timeout(s):
        seen.append(s)
        return real(s)

    monkeypatch.setattr(pymongo, "timeout", _timeout)
    _customer(till, store_credit=500.0)
    entry = {"entry_id": "E-T", "customer_id": "CUST-1", "type": "ADJUSTED", "delta": 100.0, "amount": 100.0}
    assert cust_router.post_ledger_credit(_repo(till), entry, ledger=_ledger(till)) == cust_router.POSTED
    assert seen == [cust_router.MONEY_WRITE_TIMEOUT_S]


@pytest.mark.asyncio
async def test_workshop_staff_never_record_a_payment(till):
    """Owner ruling 2026-11-01: workshop staff never touch a customer's money."""
    from api.routers.orders import PaymentCreate, add_payment
    from api.routers.orders._shared import HANDOVER_ROLES
    from api.services import rbac_policy

    till["db"].get_collection("orders").insert_one(_order())
    with pytest.raises(HTTPException) as exc:
        await add_payment("ORD-1", PaymentCreate(method="CASH", amount=100.0), _user("WORKSHOP_STAFF"))
    assert exc.value.status_code == 403 and _stored(till)["amount_paid"] == 0.0
    row = next(r for r in rbac_policy.POLICY if r["path"] == "/api/v1/orders/{order_id}/payments")
    assert "WORKSHOP_STAFF" not in row["allowed"]
    assert "WORKSHOP_STAFF" in HANDOVER_ROLES  # the handover roles are unchanged


# =========================================================================== #
# NITs
# =========================================================================== #
def test_nothing_paid_is_refused_once_the_counter_finished_its_money_step(till):
    _bill_with_hold(till, "RET-M", money_out=True)
    with pytest.raises(HTTPException) as e:
        _complete("RET-M", nothing_paid=True)
    assert e.value.status_code == 409


def test_a_heal_after_a_partial_undo_takes_the_units_back(till, monkeypatch, tasks):
    reclaimed = []
    monkeypatch.setattr(returns_router, "_reassert_return_claims", lambda row: reclaimed.append(row["entry_id"]))
    _customer(till, store_credit=200.0, store_credit_refs=["RET-H:credit"])
    _void(till, "RET-H", claims_released=True, pending_at=_ago(30))
    cust_router.reconcile_pending_credits(_repo(till), ledger=_ledger(till))
    assert reclaimed == ["RET-H:credit"]


def test_hand_set_refuses_a_negative_and_revert_restores_the_old_value(monkeypatch, tmp_path):
    from scripts import backfill_store_credit_field as bf

    db = StrictDB()
    db["customers"].insert_one({"customer_id": "CUST-1", "store_credit": "abc"})
    rc, text = _run_main(monkeypatch, bf, db, ["--set", "CUST-1=-5", "--backup", str(tmp_path / "n.json")])
    assert rc == 1 and "refuses a negative" in text
    backup = str(tmp_path / "h.json")
    rc, _ = _run_main(monkeypatch, bf, db, ["--set", "CUST-1=120", "--backup", backup])
    assert rc == 0 and db["customers"].find_one({"customer_id": "CUST-1"})["store_credit"] == 120.0
    rc, _ = _run_main(monkeypatch, bf, db, ["--revert", backup])
    assert db["customers"].find_one({"customer_id": "CUST-1"})["store_credit"] == "abc"
