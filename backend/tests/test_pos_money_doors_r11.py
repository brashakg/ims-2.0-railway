"""
IMS 2.0 - POS money doors, round-11 money review of PR #1264 (head b5f9702d)
============================================================================
An unconfirmed credit holds the bill (no new return of ANY unit), never
replays (client, superadmin note, manual door), the live Shopify door checks
section 34 on the real order's dates, a landed-but-unrecorded credit raises a
real P1 task -- and the other round-11 findings. No emoji (cp1252).
"""

from __future__ import annotations

import asyncio
import io
import os
import sys
from contextlib import redirect_stdout
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MONGODB_URI", "")

from fastapi import HTTPException  # noqa: E402

from api.routers import customers as cust_router  # noqa: E402
from api.services import store_credit_ledger as scl  # noqa: E402
from database.repositories.customer_repository import CustomerRepository  # noqa: E402
from tests.strict_fakes import StrictDB  # noqa: E402
from tests.test_pos_money_doors import (  # noqa: E402,F401
    STORE,
    _LINE,
    _advance_client,
    _hdr,
    _ledger,
    _order,
    _stored,
    _user,
    returns_router,
    till,
)
from tests.test_pos_money_doors_r9 import _ago, _balance, _customer, _issue, numbered  # noqa: E402,F401
from tests.test_pos_money_doors_r10 import _down, _run_main  # noqa: E402
from tests import test_refund_review_unpaid_credit as rr  # noqa: E402
from tests.test_refund_review_unpaid_credit import env  # noqa: E402,F401


def _repo(till):
    return CustomerRepository(till["db"].get_collection("customers"))


def _multi_qty_bill(till, monkeypatch):
    """A 2-unit line and a FAITHFUL claim: each unit can be claimed once."""
    from tests.test_pos_money_doors_r8 import _two_unit_bill

    payload = _two_unit_bill(till, monkeypatch)
    payload["return_type"] = "CREDIT_NOTE"
    claimed = {"n": 0}

    def _claim(order_id, orig_line, qty, *a, **k):
        if claimed["n"] + qty > 2:
            return False
        claimed["n"] += qty
        return True

    def _release(order_id, orig_line, qty, *a, **k):
        claimed["n"] -= qty

    monkeypatch.setattr(returns_router, "_claim_returnable_qty", _claim)
    monkeypatch.setattr(returns_router, "_release_returnable_qty", _release)
    return payload, claimed


# =========================================================================== #
# BLOCK 1: an unconfirmed credit holds the bill -- any unit, also hours later
# =========================================================================== #
@pytest.mark.usefixtures("gst_numbers")
def test_no_second_unit_is_returned_while_the_first_credit_settles_or_after_it_lands(till, monkeypatch):
    payload, claimed = _multi_qty_bill(till, monkeypatch)
    _customer(till)
    state = _down(monkeypatch, till["db"].get_collection("customers"))
    client = _advance_client()
    r1 = client.post("/api/v1/returns", json=payload, headers=_hdr("STORE_MANAGER"))
    assert r1.status_code == 503 and claimed["n"] == 1
    # The OTHER unit, at once: refused (the first credit is still ISSUING).
    state["broken"] = False
    r2 = client.post("/api/v1/returns", json=payload, headers=_hdr("STORE_MANAGER"))
    assert r2.status_code == 409 and "still being confirmed" in r2.text, r2.text
    # Hours later the reconciler finishes the first credit (it landed): the
    # return was never recorded, so the bill stays held.
    cust_router.reconcile_pending_credits(_repo(till), ledger=_ledger(till), older_than_s=0)
    assert _ledger(till).find_one({"customer_id": "CUST-1"})["type"] == "ISSUED"
    r3 = client.post("/api/v1/returns", json=payload, headers=_hdr("STORE_MANAGER"))
    assert r3.status_code == 409, r3.text
    assert _balance(till) == 1800.0 and _ledger(till).count_documents({"customer_id": "CUST-1"}) == 1


# =========================================================================== #
# BLOCK 3: a superadmin credit note is never issued twice
# =========================================================================== #
async def _sacn(till, monkeypatch, *, number="TST01/C2627/0042"):
    from api.routers.orders import admin_edit
    from api.routers.orders.models import SuperadminEditLine, SuperadminInvoiceChange

    monkeypatch.setattr(admin_edit, "get_order_repository", lambda: till["orders"])
    monkeypatch.setattr(admin_edit, "next_document_number", lambda *a, **k: number)
    monkeypatch.setattr(admin_edit, "_write_order_edit_audit", lambda **k: True)
    body = SuperadminInvoiceChange(mode="CREDIT_NOTE", reason="price fix", items=[SuperadminEditLine(
        item_id="li1", item_type="FRAME", product_name="Frame", quantity=1, unit_price=4500.0)])
    return await admin_edit.superadmin_invoice_change("ORD-1", body, _user("SUPERADMIN"))


@pytest.mark.asyncio
async def test_an_unconfirmed_superadmin_note_is_never_issued_twice(till, monkeypatch):
    monkeypatch.setattr(returns_router, "_record_unused_credit_note", lambda *a, **k: None)
    _customer(till)
    line = dict(_LINE, item_type="FRAME", unit_price=5000.0, item_total=5000.0)
    till["db"].get_collection("orders").insert_one(_order(invoice_number="BOK01/2627/00009", items=[line],
                                                          grand_total=5000.0))
    state = _down(monkeypatch, till["db"].get_collection("customers"))
    with pytest.raises(HTTPException) as e1:
        await _sacn(till, monkeypatch)
    assert e1.value.status_code == 503
    state["broken"] = False
    with pytest.raises(HTTPException) as e2:  # a re-press while it settles
        await _sacn(till, monkeypatch, number="TST01/C2627/0043")
    assert e2.value.status_code == 409 and "still being confirmed" in str(e2.value.detail)
    cust_router.reconcile_pending_credits(_repo(till), ledger=_ledger(till), older_than_s=0)
    with pytest.raises(HTTPException) as e3:  # the same correction again, once settled
        await _sacn(till, monkeypatch, number="TST01/C2627/0044")
    assert e3.value.status_code == 409 and "already issued" in str(e3.value.detail)
    rows = list(_ledger(till).find({"customer_id": "CUST-1", "type": scl.CREDIT_NOTE_TYPE}))
    assert len(rows) == 1 and rows[0]["entry_id"].startswith("ORD-1:sacn:") and _balance(till) == 500.0


# =========================================================================== #
# BLOCK 5: the manual credit door never pays twice for one form
# =========================================================================== #
def test_an_unclear_manual_credit_replayed_with_its_key_credits_once(till, monkeypatch):
    _customer(till)
    monkeypatch.setattr(cust_router, "get_customer_repository", lambda: _repo(till))
    monkeypatch.setattr(cust_router, "_scoped_customer_or_404",
                        lambda *a, **k: till["db"].get_collection("customers").find_one({"customer_id": "CUST-1"}))
    body = cust_router.StoreCreditEntryRequest(amount=250.0)
    state = _down(monkeypatch, till["db"].get_collection("customers"))
    with pytest.raises(HTTPException) as exc:
        cust_router._post_credit_entry("CUST-1", "ISSUED", body, _user("ADMIN"), "form-key-1")
    assert exc.value.status_code == 503 and "may already be credited" in exc.value.detail
    state["broken"] = False
    out = cust_router._post_credit_entry("CUST-1", "ISSUED", body, _user("ADMIN"), "form-key-1")
    assert out["entry"]["entry_id"] == "MANUAL:CUST-1:form-key-1"
    assert _balance(till) == 250.0 and _ledger(till).count_documents({"customer_id": "CUST-1"}) == 1


# =========================================================================== #
# BLOCK 6: the live Shopify door checks section 34 on the REAL order's dates
# =========================================================================== #
def test_a_live_shopify_refund_on_a_2023_invoice_files_no_credit_note(env, monkeypatch):
    from api.services import shopify_refund as sr

    tasks = []
    monkeypatch.setattr(sr, "_raise_s34_task", lambda *a, **k: tasks.append(a))
    rr._order(env, invoice_date="2023-06-10T10:00:00", created_at="2023-06-10T10:00:00")
    rr._queue_and_confirm(env, rr._payload(txns=[]))
    rows = list(env.get_collection("credit_note_ledger").find({}))
    assert [r["type"] for r in rows] == [scl.ADJUSTED] and not rows[0].get("note_number")
    assert len(tasks) == 1


def test_an_unreadable_invoice_date_refuses_the_shopify_credit(env, monkeypatch, numbered):
    rr._order(env, invoice_date="not a date", created_at="also not")
    _, res = rr._queue_and_confirm(env, rr._payload(txns=[]))
    assert env.get_collection("credit_note_ledger").count_documents({}) == 0
    assert res["status"] == "credit_failed", {k: res.get(k) for k in ("status", "reason")}


# =========================================================================== #
# BLOCK 7: a landed-but-unrecorded credit is put in front of a human
# =========================================================================== #
def test_a_settled_but_unrecorded_return_credit_raises_a_p1_task(till, monkeypatch):
    tasks = []
    monkeypatch.setattr("api.services.task_triggers.create_system_task", lambda repo, **k: tasks.append(k))
    monkeypatch.setattr("api.dependencies.get_task_repository", lambda: object(), raising=False)
    _customer(till, store_credit=200.0, store_credit_refs=["RET-U:credit"])
    spec = {"order_id": "ORD-1", "return_id": "RET-U", "lines": [{"item_id": "li1", "qty": 1.0}],
            "refund_tenders": [{"method": "CASH", "amount": 100.0}, {"method": "STORE_CREDIT", "amount": 200.0}]}
    _ledger(till).insert_one({"_id": "RET-U:credit", "entry_id": "RET-U:credit", "customer_id": "CUST-1",
                              "type": "ISSUING", "final_type": "ISSUED", "applied": False, "delta": 200.0,
                              "note_number": "TST01/C2627/0077", "pending_at": _ago(30), "undo_on_void": spec})
    cust_router.reconcile_pending_credits(_repo(till), ledger=_ledger(till))
    assert len(tasks) == 1
    t = tasks[0]
    assert t["priority"] == "P1" and t["dedupe_ref"] == "return_credit_settled:RET-U:credit"
    assert "RET-U" in t["description"] and "TST01/C2627/0077" in t["description"] and "cash leg Rs 100.00" in t["description"]
    # A second pass finds nothing to finish: no second task.
    cust_router.reconcile_pending_credits(_repo(till), ledger=_ledger(till))
    assert len(tasks) == 1
    # A finish that matches nothing (another pass finished it first) raises none.
    stale = {"entry_id": "RET-U:credit", "customer_id": "CUST-1", "final_type": "ISSUED", "delta": 200.0,
             "undo_on_void": spec}
    assert cust_router._finish_credit_row(_ledger(till), stale, redate=True) is False
    assert len(tasks) == 1


# =========================================================================== #
# SHOULD-FIX
# =========================================================================== #
def test_a_refused_press_leaves_the_claims_to_one_owner(till, monkeypatch, numbered):
    undone = []
    monkeypatch.setattr(returns_router, "_undo_return_claims", lambda spec, **k: undone.append(spec))
    monkeypatch.setattr(cust_router, "apply_store_credit_delta", lambda *a, **k: False)  # a clean refusal
    _customer(till)
    out = _issue(ref="RET-R", entry_id="RET-R:credit",
                 undo_on_void={"order_id": "ORD-1", "return_id": "RET-R", "lines": [{"item_id": "li1", "qty": 1}]})
    assert out is None and undone == []  # the caller releases them, once
    assert _ledger(till).find_one({"entry_id": "RET-R:credit"})["type"] == "VOID"


def test_a_ledger_read_error_before_posting_is_a_clean_refusal(till, monkeypatch):
    _customer(till)
    ledger = _ledger(till)
    monkeypatch.setattr(ledger, "find_one", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    entry = {"entry_id": "E-1", "customer_id": "CUST-1", "type": "ISSUED", "delta": 100.0, "amount": 100.0}
    assert cust_router.post_ledger_credit(_repo(till), entry, ledger=ledger) == cust_router.REFUSED
    assert _balance(till) == 0.0


@pytest.mark.asyncio
async def test_a_loyalty_redeem_pressed_again_for_the_same_bill_burns_once(till):
    from api.routers import loyalty as L

    till["db"].get_collection("orders").insert_one(_order())
    till["db"].get_collection("loyalty_accounts").insert_one(
        {"customer_id": "CUST-1", "balance_points": 500, "lifetime_earned": 500, "tier": "BRONZE"})
    a = await L.redeem(L.RedeemRequest(customer_id="CUST-1", order_id="ORD-1", points=300), _user("CASHIER"))
    b = await L.redeem(L.RedeemRequest(customer_id="CUST-1", order_id="ORD-1", points=300), _user("CASHIER"))
    assert a["txn_id"] == b["txn_id"] and b.get("replayed") is True
    assert till["accounts"].find_by_id("CUST-1")["balance_points"] == 200


@pytest.mark.usefixtures("gst_numbers")
def test_a_mismatch_online_order_never_refunds_above_shopifys_total(monkeypatch):
    from tests.test_returns_gst_refund import _qa_payload
    from tests.test_returns_refund_cap import _ctx

    assert returns_router.refundable_ceiling({"amount_paid": 2000.0, "shopify_totals_check": {
        "status": "MISMATCH", "shopify_total": 1000.0}}) == 1000.0
    client, returns = _ctx(monkeypatch, amount_paid=2000.0,
                           shopify_totals_check={"status": "MISMATCH", "shopify_total": 1000.0})
    r = client.post("/api/v1/returns", json=_qa_payload(), headers=_hdr("ADMIN"))
    assert r.status_code == 400 and "exceed" in r.text.lower(), r.text


@pytest.mark.usefixtures("gst_numbers")
def test_the_counter_never_returns_a_shipping_line(monkeypatch):
    from tests.test_returns_gst_refund import _qa_payload
    from tests.test_returns_refund_cap import _ctx

    client, returns = _ctx(monkeypatch, amount_paid=2000.0)
    payload = _qa_payload()
    import tests.test_returns_refund_cap as cap

    order = cap._FakeOrderRepo  # noqa: F841 -- the line below re-types the QA line
    monkeypatch.setattr(returns_router, "_resolve_original_line",
                        lambda ret_line, idx: {"item_id": "li1", "product_id": "PRD-1", "item_type": "SHIPPING",
                                               "category": "SHIPPING", "quantity": 1, "unit_price": 1190})
    r = client.post("/api/v1/returns", json=payload, headers=_hdr("ADMIN"))
    assert r.status_code == 400 and "Shipping charges are not returned at the counter" in r.text
    assert returns.count_documents({}) == 0


@pytest.mark.asyncio
async def test_the_booking_reread_refuses_a_dead_order_with_the_same_400(till, monkeypatch):
    from api.services import shiprocket
    from tests.test_pos_money_doors import _advance_order
    from tests.test_pos_money_doors_r8 import _shipping

    _advance_order(till, status="READY")
    shipping, ships = _shipping(till, monkeypatch)
    first = dict(_stored(till))
    reads = iter([first, {**first, "status": "RETURNED"}])
    monkeypatch.setattr(shipping, "_resolve_order", lambda oid: next(reads))
    called = []

    async def _courier(*a, **k):
        called.append(1)

    monkeypatch.setattr(shiprocket, "create_shipment", _courier)
    body = shipping.ShipmentCreate(order_id="ORD-1", address=shipping.ShipAddress(payment_method="COD"))
    with pytest.raises(HTTPException) as exc:
        await shipping.book_shipment(body, _user("ADMIN"))
    assert exc.value.status_code == 400 and exc.value.detail["code"] == "ORDER_NOT_SHIPPABLE"
    assert called == [] and [d["status"] for d in ships.find({"order_id": "ORD-1"})] == ["FAILED"]


# =========================================================================== #
# NITs
# =========================================================================== #
def test_a_void_with_an_unreadable_guard_goes_back_to_pending(till, monkeypatch):
    _customer(till)
    _ledger(till).insert_one({"_id": "E-6", "entry_id": "E-6", "customer_id": "CUST-1", "type": "ISSUING",
                              "final_type": "ISSUED", "applied": False, "delta": 200.0, "pending_at": _ago(30)})
    row = dict(_ledger(till).find_one({"entry_id": "E-6"}))
    monkeypatch.setattr(cust_router, "_guard_landed", lambda *a, **k: None)
    assert cust_router._void_credit_row(_repo(till), _ledger(till), row, "x") is False
    after = _ledger(till).find_one({"entry_id": "E-6"})
    assert (after["type"], after["delta"], after["pending_at"]) == ("ISSUING", 200.0, row["pending_at"])


def test_a_press_landing_after_a_void_heals_the_line(till, monkeypatch):
    _customer(till)
    # A press still in its window (round 13: an older line is the reconciler's).
    row = {"entry_id": "E-4", "customer_id": "CUST-1", "type": "ISSUING", "final_type": "ISSUED",
           "applied": False, "delta": 200.0, "pending_at": _ago(2)}
    _ledger(till).insert_one({**row, "_id": "E-4"})
    real_apply = cust_router.apply_store_credit_delta

    def _apply_after_a_void(*a, **k):
        _ledger(till).update_one({"entry_id": "E-4"}, {"$set": {"type": "VOID", "delta": 0.0, "void_delta": 200.0}})
        return real_apply(*a, **k)

    monkeypatch.setattr(cust_router, "apply_store_credit_delta", _apply_after_a_void)
    assert cust_router._settle_credit_row(_repo(till), _ledger(till), dict(row)) == cust_router.POSTED
    after = _ledger(till).find_one({"entry_id": "E-4"})
    assert (after["type"], after["applied"], after["delta"], after.get("healed_from_void")) == ("ISSUED", True, 200.0, True)


def test_a_finish_into_a_locked_month_is_dated_the_finish_day(till, monkeypatch):
    class _Conn:
        db = till["db"]

    monkeypatch.setattr("api.dependencies.get_db", lambda: _Conn())
    created = cust_router.utc_now_iso()  # this month: only the lock can redate it
    month = cust_router._month_of(created)

    def _row(eid):
        return {"_id": eid, "entry_id": eid, "customer_id": "CUST-1", "type": "ISSUING", "final_type": "ISSUED",
                "applied": False, "delta": 100.0, "created_at": created, "pending_at": _ago(60)}

    _customer(till, store_credit=200.0, store_credit_refs=["E-OPEN", "E-3"])
    # Control first: an open month keeps its own date.
    _ledger(till).insert_one(_row("E-OPEN"))
    cust_router.reconcile_pending_credits(_repo(till), ledger=_ledger(till))
    assert "redated_from" not in _ledger(till).find_one({"entry_id": "E-OPEN"})
    till["db"].get_collection("period_locks").insert_one({"month": int(month[5:7]), "year": int(month[:4])})
    _ledger(till).insert_one(_row("E-3"))
    cust_router.reconcile_pending_credits(_repo(till), ledger=_ledger(till))
    assert _ledger(till).find_one({"entry_id": "E-3"}).get("redated_from") == created


def test_a_redrive_past_the_window_finishes_its_in_flight_row_as_money_given(till, monkeypatch, numbered):
    written_off = []
    monkeypatch.setattr(returns_router, "_record_unused_credit_note", lambda number, *a, **k: written_off.append(number))
    _customer(till)
    _ledger(till).insert_one({"_id": "RET-S:credit", "entry_id": "RET-S:credit", "customer_id": "CUST-1",
                              "ref": "RET-S", "type": "ISSUING", "final_type": "ISSUED", "applied": False,
                              "delta": 300.0, "note_number": "TST01/C2627/0055", "tax": 14.29,
                              "pending_at": _ago(1)})
    out = returns_router._issue_store_credit("CUST-1", 300.0, reason="r", ref="RET-S", current_user=_user("ADMIN"),
                                             store_id=STORE, order_id="ORD-1", credit_note=False)
    row = _ledger(till).find_one({"entry_id": "RET-S:credit"})
    assert out is not None and row["type"] == scl.ADJUSTED and "note_number" not in row
    assert written_off == ["TST01/C2627/0055"]


def test_the_reconciler_tick_runs_off_the_event_loop(monkeypatch):
    seen = []
    monkeypatch.setattr(asyncio, "to_thread", lambda fn, *a, **k: _done(seen.append(fn)))
    asyncio.run(cust_router.scheduled_credit_reconcile())
    from api.routers.returns import sweep_stuck_return_holds

    assert seen == [cust_router.reconcile_pending_credits, sweep_stuck_return_holds]


async def _done(_):
    return None


def test_the_ledger_indexes_exist(monkeypatch):
    from database.connection import DatabaseConnection

    made = []

    class _Coll:
        def create_index(self, keys, **kw):
            made.append(keys)

    class _DB:
        def __getitem__(self, name):
            return _NamedColl(name)

    class _NamedColl(_Coll):
        def __init__(self, name):
            self.name = name

        def create_index(self, keys, **kw):
            made.append((self.name, keys))

    conn = DatabaseConnection.__new__(DatabaseConnection)  # the process singleton: restored after
    monkeypatch.setattr(conn, "_connected", True, raising=False)
    monkeypatch.setattr(conn, "_db", _DB(), raising=False)
    conn.ensure_indexes()
    assert ("credit_note_ledger", [("type", 1), ("pending_at", 1)]) in made
    assert ("credit_note_ledger", "customer_id") in made


def test_the_store_credit_dry_run_exits_2_on_a_stop_and_3_on_an_empty_database(monkeypatch):
    from scripts import backfill_store_credit_field as bf

    db = StrictDB()
    db["customers"].insert_one({"customer_id": "CUST-2"})
    db["credit_note_ledger"].insert_one({"customer_id": "CUST-2", "entry_id": "y", "delta": -50.0})
    rc, text = _run_main(monkeypatch, bf, db, [])
    assert rc == 2 and "[REFUSED]" in text
    rc, text = _run_main(monkeypatch, bf, StrictDB(), [])
    assert rc == 3 and "no customers scanned" in text


def test_the_store_credit_dry_run_lists_drift_read_only(monkeypatch):
    from scripts import backfill_store_credit_field as bf

    db = StrictDB()
    db["customers"].insert_one({"customer_id": "CUST-1", "store_credit": 500.0})
    db["credit_note_ledger"].insert_one({"customer_id": "CUST-1", "entry_id": "a", "delta": 300.0})
    assert bf.plan(db)["drift"] == [("CUST-1", 500.0, 300.0)]
    assert db["customers"].find_one({"customer_id": "CUST-1"})["store_credit"] == 500.0


def test_revert_never_touches_a_customer_a_live_credit_moved(tmp_path):
    import json

    from scripts import backfill_store_credit_field as bf

    db = StrictDB()
    db["customers"].insert_one({"customer_id": "CUST-1", "store_credit": 300.0, "store_credit_backfill_value": 300.0,
                                "store_credit_backfilled_at": datetime.now(timezone.utc),
                                "store_credit_refs": ["MANUAL:CUST-1:k"]})
    backup = tmp_path / "b.json"
    backup.write_text(json.dumps([{"customer_id": "CUST-1"}]))
    buf = io.StringIO()
    with redirect_stdout(buf):
        assert bf.revert(db, str(backup)) == 0
    assert "[SKIPPED] 1" in buf.getvalue()
    assert db["customers"].find_one({"customer_id": "CUST-1"})["store_credit"] == 300.0


def test_the_loyalty_backfill_survives_a_non_numeric_balance():
    from scripts import backfill_loyalty_tender_claims as bf

    db = StrictDB()
    db["orders"].insert_one(_order("ORD-7", balance_due="abc", payments=[]))
    db["orders"].insert_one({"order_id": "ORD-8", "grand_total": 0.0, "payments": []})  # no balance_due key
    db["loyalty_transactions"].insert_one({"txn_id": "T-7", "type": "REDEEM", "order_id": "ORD-7"})
    db["loyalty_transactions"].insert_one({"txn_id": "T-8", "type": "REDEEM", "order_id": "ORD-8"})
    out = bf.plan(db)
    assert ("T-7", "ORD-7", None) in out["unmatched_rows"] and out["legacy"] == ["T-8"]



@pytest.mark.usefixtures("gst_numbers")
def test_a_mixed_tender_return_with_a_fee_files_one_tax_figure(monkeypatch):
    from tests.test_returns_gst_refund import _qa_payload
    from tests.test_returns_refund_cap import _ctx

    client, returns = _ctx(monkeypatch, amount_paid=2000.0, created_at="2026-10-20T10:00:00",
                           payments=[{"payment_id": "p1", "method": "UPI", "amount": 700.0},
                                     {"payment_id": "p2", "method": "GIFT_VOUCHER", "amount": 704.2}])
    ledger = returns_router._ledger_coll()
    payload = _qa_payload(restocking_fee=100.0,
                          refund_tenders=[{"method": "UPI", "amount": 700.0},
                                          {"method": "STORE_CREDIT", "amount": 604.2}])
    r = client.post("/api/v1/returns", json=payload, headers=_hdr("ADMIN"))
    assert r.status_code == 201, r.text
    doc = returns.docs[0]
    rows = [d for d in ledger.docs if d.get("type") == scl.CREDIT_NOTE_TYPE]
    assert len(rows) == 1
    assert rows[0]["tax"] == doc["gst_breakup"]["tax"] and rows[0]["taxable"] == doc["gst_breakup"]["taxable"]
    assert sum(b["tax"] for b in doc["gst_breakup"]["by_rate"].values()) == pytest.approx(rows[0]["tax"], abs=0.02)


def test_the_loyalty_backup_keeps_only_the_fields_it_writes(tmp_path):
    import json

    from scripts import backfill_loyalty_tender_claims as bf

    db = StrictDB()
    db["loyalty_transactions"].insert_one({"txn_id": "T-1", "type": "REDEEM", "order_id": "ORD-1",
                                           "customer_id": "CUST-1", "customer_phone": "9800000001"})
    backup = str(tmp_path / "b.json")
    assert bf.apply(db, [("T-1", "PAY-1")], backup) == 1
    assert json.load(open(backup, encoding="utf-8")) == [{"txn_id": "T-1"}]
