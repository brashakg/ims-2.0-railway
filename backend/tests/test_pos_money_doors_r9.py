"""
IMS 2.0 - POS money doors, round-9 money review of PR #1264 (head 4e33ae34)
===========================================================================
THE posting door (customers.post_ledger_credit) as a resumable state machine
-- ISSUING -> final | VOID, the balance $inc guarded by the row's entry_id, a
reconciler for rows a dead press left pending -- plus the other round-9
findings, each through the real code over strict fakes. No emoji (cp1252).
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
from api.services import store_credit_ledger as scl  # noqa: E402
from tests.strict_fakes import StrictDB  # noqa: E402
from tests.test_pos_money_doors import (  # noqa: E402,F401
    STORE,
    _ADV_BODY,
    _LINE,
    _advance_client,
    _advance_order,
    _crash_once,
    _hdr,
    _ledger,
    _order,
    _ship,
    _stored,
    _user,
    returns_router,
    till,
)
from tests.test_pos_money_doors_r8 import _approved, _engine, _two_unit_bill  # noqa: E402


class _WorkerDied(BaseException):
    """A process kill: no except-Exception handler sees it."""


def _ago(minutes):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()


def _issue(ref="RET-A", cid="CUST-1", amount=200.0, **kw):
    return returns_router._issue_store_credit(cid, amount, reason="return", ref=ref, current_user=_user("ADMIN"),
                                              store_id=STORE, order_id="ORD-1", **kw)


@pytest.fixture()
def numbered(monkeypatch):
    n = {"i": 0}

    def _num(*a, **k):
        n["i"] += 1
        return f"TST01/C2627/{n['i']:04d}"

    monkeypatch.setattr(returns_router, "next_document_number", _num)
    return n


def _customer(till, **doc):
    till["db"].get_collection("customers").insert_one({"customer_id": "CUST-1", "store_credit": 0.0, **doc})
    return till["db"].get_collection("customers")


def _balance(till):
    return till["db"].get_collection("customers").find_one({"customer_id": "CUST-1"})["store_credit"]


# =========================================================================== #
# BLOCK: the Shopify re-drive after a death between the $inc and the finish
# =========================================================================== #
def test_a_redrive_after_a_death_between_the_move_and_the_finish_credits_once(till, monkeypatch, numbered):
    _customer(till)
    ledger = _ledger(till)
    real = ledger.update_one
    died = {"n": 0}

    def _die_at_finish(flt, upd, *a, **k):
        if not died["n"] and (upd.get("$set") or {}).get("applied") is True:
            died["n"] = 1
            raise _WorkerDied()
        return real(flt, upd, *a, **k)

    monkeypatch.setattr(ledger, "update_one", _die_at_finish)
    with pytest.raises(_WorkerDied):
        _issue(ref="RET-SHOP")
    assert _balance(till) == 200.0 and [r["type"] for r in ledger.find({"ref": "RET-SHOP"})] == ["ISSUING"]
    again = _issue(ref="RET-SHOP")  # the re-drive of the same return
    assert again is not None
    rows = list(ledger.find({"ref": "RET-SHOP"}))
    assert [(r["type"], r["applied"]) for r in rows] == [("ISSUED", True)]
    assert _balance(till) == 200.0  # one $inc, never two


# =========================================================================== #
# SHOULD-FIX: an unclear $inc (landed, then raised) is read from the guard
# =========================================================================== #
def test_an_inc_that_lands_then_raises_is_resolved_by_its_guard(till, monkeypatch, numbered):
    from pymongo.errors import NetworkTimeout

    custs = _customer(till)
    real = custs.update_one
    hit = {"n": 0}

    def _landed_then_raised(flt, upd, *a, **k):
        res = real(flt, upd, *a, **k)
        if not hit["n"] and "$inc" in upd:
            hit["n"] = 1
            raise NetworkTimeout("reply lost")
        return res

    monkeypatch.setattr(custs, "update_one", _landed_then_raised)
    entry = _issue()
    assert entry is not None  # it DID move: the return goes through, once
    assert _balance(till) == 200.0
    assert [(r["type"], r["applied"]) for r in _ledger(till).find({"customer_id": "CUST-1"})] == [("ISSUED", True)]


# =========================================================================== #
# SHOULD-FIX: nothing stays ISSUING forever -- the reconciler settles it
# =========================================================================== #
@pytest.mark.parametrize("landed", [True, False])
def test_the_reconciler_finishes_or_voids_a_stale_pending_row(till, monkeypatch, landed):
    written_off = []
    monkeypatch.setattr(returns_router, "_record_unused_credit_note", lambda number, *a, **k: written_off.append(number) or True)
    _customer(till, store_credit=200.0 if landed else 0.0, store_credit_refs=["E-1"] if landed else [])
    _ledger(till).insert_one({"_id": "E-1", "entry_id": "E-1", "customer_id": "CUST-1", "type": "ISSUING",
                              "final_type": "ISSUED", "applied": False, "delta": 200.0, "note_number": "TST01/C2627/0007",
                              "pending_at": _ago(30)})
    from database.repositories.customer_repository import CustomerRepository

    out = cust_router.reconcile_pending_credits(CustomerRepository(till["db"].get_collection("customers")),
                                                ledger=_ledger(till))
    row = _ledger(till).find_one({"entry_id": "E-1"})
    if landed:
        assert out == {"finished": 1, "voided": 0} and (row["type"], row["applied"]) == ("ISSUED", True)
        assert written_off == []
    else:
        assert out == {"finished": 0, "voided": 1} and row["type"] == "VOID"
        assert written_off == ["TST01/C2627/0007"]


def test_the_reconciler_never_touches_a_row_a_press_is_still_writing(till):
    _customer(till)
    _ledger(till).insert_one({"_id": "E-2", "entry_id": "E-2", "customer_id": "CUST-1", "type": "ISSUING",
                              "final_type": "ISSUED", "applied": False, "delta": 200.0, "pending_at": _ago(1)})
    from database.repositories.customer_repository import CustomerRepository

    repo = CustomerRepository(till["db"].get_collection("customers"))
    assert cust_router.reconcile_pending_credits(repo, ledger=_ledger(till)) == {"finished": 0, "voided": 0}


# =========================================================================== #
# SHOULD-FIX (test gap): a row in flight is never filed in the GST returns
# =========================================================================== #
def test_a_credit_note_in_flight_is_not_in_gstr3b(till, monkeypatch, numbered):
    from api.routers.reports import gstr3b

    custs = _customer(till)
    real = custs.update_one

    def _die(flt, upd, *a, **k):
        if "$inc" in upd:
            raise _WorkerDied()
        return real(flt, upd, *a, **k)

    monkeypatch.setattr(custs, "update_one", _die)
    with pytest.raises(_WorkerDied):
        _issue(ref="RET-FLY")
    now = datetime.now(timezone.utc)
    assert [r["type"] for r in _ledger(till).find({"ref": "RET-FLY"})] == ["ISSUING"]
    totals = gstr3b._credit_note_totals(till["db"], STORE, now.year, now.month, 28)
    assert totals[3] == 0.0


# =========================================================================== #
# BLOCK: the superadmin credit note is only "issued" once its credit landed
# =========================================================================== #
@pytest.mark.asyncio
async def test_a_superadmin_credit_note_with_no_credit_is_refused_and_written_off(till, monkeypatch):
    from api.routers.orders import admin_edit
    from api.routers.orders.models import SuperadminInvoiceChange, SuperadminEditLine

    written_off = []
    monkeypatch.setattr(returns_router, "_record_unused_credit_note", lambda number, *a, **k: written_off.append(number) or True)
    monkeypatch.setattr(admin_edit, "get_order_repository", lambda: till["orders"])
    monkeypatch.setattr(admin_edit, "next_document_number", lambda *a, **k: "TST01/C2627/0042")
    monkeypatch.setattr(admin_edit, "_write_order_edit_audit", lambda **k: True)
    line = dict(_LINE, item_type="FRAME", unit_price=5000.0, item_total=5000.0)
    till["db"].get_collection("orders").insert_one(_order(customer_id="CUST-GONE", invoice_number="BOK01/2627/00009",
                                                          items=[line], grand_total=5000.0))
    body = SuperadminInvoiceChange(mode="CREDIT_NOTE", reason="price fix", items=[SuperadminEditLine(
        item_id="li1", item_type="FRAME", product_name="Frame", quantity=1, unit_price=4500.0)])
    with pytest.raises(HTTPException) as exc:
        await admin_edit.superadmin_invoice_change("ORD-1", body, _user("SUPERADMIN"))
    assert exc.value.status_code == 503
    assert _ledger(till).count_documents({"type": scl.CREDIT_NOTE_TYPE}) == 0
    assert _stored(till).get("post_invoice_credit_note_at") is None
    assert written_off == ["TST01/C2627/0042"]


def test_a_stale_superadmin_marker_with_no_note_heals_itself(till):
    _advance_order(till, post_invoice_credit_note_at=_ago(30))
    r = _advance_client().post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr("ACCOUNTANT"))
    assert r.status_code == 201, r.text
    assert _stored(till)["status"] == "CANCELLED"


@pytest.mark.asyncio
async def test_a_door_claim_landing_before_the_superadmin_cas_wins(till, monkeypatch):
    from api.routers.orders import admin_edit
    from api.routers.orders.models import SuperadminInvoiceChange, SuperadminEditLine

    monkeypatch.setattr(admin_edit, "get_order_repository", lambda: till["orders"])
    monkeypatch.setattr(admin_edit, "_write_order_edit_audit", lambda **k: True)
    minted = []
    monkeypatch.setattr(admin_edit, "next_document_number", lambda *a, **k: minted.append(1) or "TST01/C2627/0050")
    line = dict(_LINE, item_type="FRAME", unit_price=5000.0, item_total=5000.0)
    till["db"].get_collection("orders").insert_one(_order(invoice_number="BOK01/2627/00009", items=[line],
                                                          grand_total=5000.0))
    real = till["orders"].update_if

    def _door_first(oid, exp, data, *a, **k):
        if "post_invoice_credit_note_at" in data and data["post_invoice_credit_note_at"]:
            till["db"].get_collection("orders").update_one({"order_id": oid},
                                                           {"$set": {"advance_refund_id": "RET-DOOR"}})
        return real(oid, exp, data, *a, **k)

    monkeypatch.setattr(till["orders"], "update_if", _door_first)
    body = SuperadminInvoiceChange(mode="CREDIT_NOTE", reason="price fix", items=[SuperadminEditLine(
        item_id="li1", item_type="FRAME", product_name="Frame", quantity=1, unit_price=4500.0)])
    with pytest.raises(HTTPException) as exc:
        await admin_edit.superadmin_invoice_change("ORD-1", body, _user("SUPERADMIN"))
    assert exc.value.status_code == 409
    assert minted == [] and _stored(till).get("post_invoice_credit_note_at") is None
    assert _ledger(till).count_documents({}) == 0


# =========================================================================== #
# SHOULD-FIX: a stale door claim never locks the handover for good
# =========================================================================== #
def test_a_dead_presss_claim_is_freed_by_the_handover_gate(till):
    from api.services.delivery_gate import refuse_handover_of_refunded

    till["db"].get_collection("orders").insert_one(_order(advance_refund_id="RET-ORPHAN", advance_refund_at=_ago(60)))
    refuse_handover_of_refunded(_stored(till))  # no 409
    assert _stored(till).get("advance_refund_id") is None


def test_a_live_claim_still_locks_the_handover(till):
    from api.services.delivery_gate import refuse_handover_of_refunded

    till["db"].get_collection("orders").insert_one(_order(advance_refund_id="RET-LIVE", advance_refund_at=_ago(0)))
    with pytest.raises(HTTPException):
        refuse_handover_of_refunded(_stored(till))
    assert _stored(till)["advance_refund_id"] == "RET-LIVE"


# =========================================================================== #
# SHOULD-FIX: a referral reward is not a GST credit note
# =========================================================================== #
@pytest.mark.asyncio
async def test_a_referral_reward_is_never_a_credit_note(till, monkeypatch):
    from tests.test_pos_money_doors_r8 import _referral

    marketing = _referral(till, monkeypatch, {"customer_id": "CUST-1", "store_credit": 0.0})
    await marketing.redeem_referral("REF-1", current_user=_user("ADMIN"))
    assert [(r["type"], r["applied"]) for r in _ledger(till).find({"customer_id": "CUST-1"})] == [("ADJUSTED", True)]
    assert _ledger(till).count_documents({"type": scl.CREDIT_NOTE_TYPE}) == 0
    assert _balance(till) == 100.0


# =========================================================================== #
# SHOULD-FIX: a hold whose money went out never ages out
# =========================================================================== #
def test_a_return_whose_doc_was_lost_keeps_counting(till, monkeypatch):
    payload = _two_unit_bill(till, monkeypatch)
    returns = till["db"].get_collection("returns")
    _crash_once(monkeypatch, returns, "insert_one", lambda doc, *a, **k: doc.get("status") == "COMPLETED")
    client = _advance_client()
    assert client.post("/api/v1/returns", json=payload, headers=_hdr("CASHIER")).status_code == 201
    assert returns.count_documents({}) == 0
    holds = _stored(till)["refund_holds"]
    rid = next(iter(holds))
    assert holds[rid]["kept"] is True
    holds[rid]["at"] = _ago(10)  # long past the 2-minute window
    till["db"].get_collection("orders").update_one({"order_id": "ORD-1"}, {"$set": {"refund_holds": holds}})
    # The kept hold still counts (it never ages out) ...
    live, _ = returns_router._live_refund_holds("ORD-1", holds)
    assert rid in live
    # ... and since round 14 the bill takes no new return at all until that
    # return is recorded (stricter than the approval gate it used to reach).
    r2 = client.post("/api/v1/returns", json=payload, headers=_hdr("CASHIER"))
    assert r2.status_code == 409 and f"({rid}) was recorded at the counter but its record was not saved" in r2.text, r2.text


# =========================================================================== #
# SHOULD-FIX: a BOOKING the door never confirmed expires; the last write is loud
# =========================================================================== #
def test_an_unconfirmed_booking_expires_and_no_longer_blocks_the_refund(till, monkeypatch):
    _advance_order(till, status="READY")
    ships = _ship(till, monkeypatch, shipment_id="SH-9", order_id="ORD-1", status="BOOKING", created_at=_ago(30))
    r = _advance_client().post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr("ACCOUNTANT"))
    assert r.status_code == 201, r.text
    assert ships.find_one({"shipment_id": "SH-9"})["status"] == "FAILED"


def test_a_fresh_booking_still_counts_as_handed_over(till, monkeypatch):
    _advance_order(till, status="READY")
    _ship(till, monkeypatch, shipment_id="SH-8", order_id="ORD-1", status="BOOKING", created_at=_ago(1))
    r = _advance_client().post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr("ACCOUNTANT"))
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_a_booking_whose_last_write_fails_is_not_a_201(till, monkeypatch):
    from api.services import shiprocket
    from tests.test_pos_money_doors_r8 import _Booked, _shipping

    _advance_order(till, status="READY")
    shipping, ships = _shipping(till, monkeypatch)
    monkeypatch.setattr(shipping, "_resolve_order", lambda oid: _stored(till, oid))

    async def _courier(*a, **k):
        return _Booked()

    monkeypatch.setattr(shiprocket, "create_shipment", _courier)
    real = ships.update_one
    monkeypatch.setattr(ships, "update_one", lambda flt, upd, *a, **k: (_ for _ in ()).throw(RuntimeError("down"))
                        if "sr_shipment_id" in (upd.get("$set") or {}) else real(flt, upd, *a, **k))
    body = shipping.ShipmentCreate(order_id="ORD-1", address=shipping.ShipAddress(payment_method="COD"))
    with pytest.raises(HTTPException) as exc:
        await shipping.book_shipment(body, _user("ADMIN"))
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_the_courier_is_never_called_when_the_order_cannot_be_reread(till, monkeypatch):
    from api.services import shiprocket
    from tests.test_pos_money_doors_r8 import _shipping

    _advance_order(till, status="READY")
    shipping, ships = _shipping(till, monkeypatch)
    reads = iter([_stored(till), None])
    monkeypatch.setattr(shipping, "_resolve_order", lambda oid: next(reads))
    called = []

    async def _courier(*a, **k):
        called.append(1)

    monkeypatch.setattr(shiprocket, "create_shipment", _courier)
    body = shipping.ShipmentCreate(order_id="ORD-1", address=shipping.ShipAddress(payment_method="COD"))
    with pytest.raises(HTTPException) as exc:
        await shipping.book_shipment(body, _user("ADMIN"))
    assert exc.value.status_code == 503 and called == []
    assert [d["status"] for d in ships.find({"order_id": "ORD-1"})] == ["FAILED"]


# =========================================================================== #
# SHOULD-FIX: GST view and cross-check for a refunded order whose cancel is pending
# =========================================================================== #
def test_an_uninvoiced_refunded_order_with_its_cancel_pending_is_not_filed():
    from api.utils.real_sale import is_real_sale, real_sale_match

    db = StrictDB()
    db.get_collection("orders").insert_one({"_id": "a", "order_id": "ORD-1", "status": "CONFIRMED",
                                            "advance_refund_status": "COMPLETED", "grand_total": 11800.0})
    order = db.get_collection("orders").find_one({"order_id": "ORD-1"})
    assert is_real_sale(order, gst_documents=True) is False
    assert db.get_collection("orders").count_documents(real_sale_match(db, gst_documents=True)) == 0


def test_the_crosscheck_adds_back_an_invoiced_order_whose_cancel_is_pending():
    from api.routers.finance import gst_crosscheck as gc

    db = StrictDB()
    start, end = datetime(2026, 10, 1), datetime(2026, 11, 1)
    db.get_collection("orders").insert_one({
        "_id": "a", "order_id": "ORD-1", "status": "CONFIRMED", "invoice_number": "INV/1",
        "advance_refund_status": "COMPLETED", "advance_refund_credit_note": "TST01/C2627/0001",
        "grand_total": 5000.0, "tax_amount": 238.10, "store_id": STORE, "created_at": datetime(2026, 10, 5)})
    out = gc._cancelled_behind_credit_note(db, [STORE], start, end, {}, {})
    assert out["count"] == 1 and out["tax"] == 238.10


# =========================================================================== #
# SHOULD-FIX: section 34 for every credit-note door, failing closed
# =========================================================================== #
def test_a_counter_return_past_the_credit_note_window_is_refused_before_numbering(monkeypatch):
    from tests.test_returns_gst_refund import _qa_payload
    from tests.test_returns_refund_cap import _ctx

    minted = []
    monkeypatch.setattr(returns_router, "next_document_number", lambda *a, **k: minted.append(1) or "X")
    client, returns = _ctx(monkeypatch, amount_paid=2000.0, invoice_number="BOK01/2425/00001",
                           invoice_date="2024-06-10T10:00:00")
    r = client.post("/api/v1/returns", json=_qa_payload(), headers=_hdr("ADMIN"))
    assert r.status_code == 400 and "section 34" in r.text, r.text
    assert minted == [] and returns.count_documents({}) == 0


def test_the_credit_note_window_fails_closed_on_an_unreadable_date():
    assert "cannot be read" in returns_router._credit_note_window_closed({"invoice_date": "garbage"})
    assert returns_router._credit_note_window_closed({"created_at": "2026-10-20T10:00:00"}) is None


# =========================================================================== #
# NITs
# =========================================================================== #
def test_only_a_superadmin_may_switch_the_refund_rule():
    from api.services.policy_registry import REGISTRY

    spec = REGISTRY["refund.matrix_enabled"]
    assert tuple(spec.write_roles) == ("SUPERADMIN",)


def test_finishing_a_refund_rechecks_the_days_count(till, monkeypatch):
    from api.utils.ist import ist_today

    _advance_order(till)
    _crash_once(monkeypatch, till["orders"], "update_if",
                lambda oid, exp, data, *a, **k: "advance_refund_status" in data)
    client = _advance_client()
    assert client.post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr("ACCOUNTANT")).status_code == 503
    till["db"].get_collection("till_sessions").insert_one(
        {"store_id": STORE, "session_date": ist_today().isoformat(), "status": "LOCKED"})
    r = client.post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr("ACCOUNTANT"))
    assert r.status_code == 409 and "Day-End" in r.text
    assert till["db"].get_collection("returns").find_one({"order_id": "ORD-1"})["status"] == "PENDING"


def test_an_einvoice_irn_under_either_name_is_refused(till):
    _advance_order(till, invoice_number="BOK01/2627/00009", items=[dict(_LINE)], einvoice_irn="IRN-9")
    r = _advance_client().post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr("ACCOUNTANT"))
    assert r.status_code == 400 and "e-invoice" in r.text


@pytest.mark.asyncio
async def test_no_einvoice_for_an_order_being_refunded():
    from api.services import einvoice

    out = await einvoice.generate_irn(None, {"order_id": "ORD-1", "advance_refund_id": "RET-1"})
    assert out["status"] == einvoice.STATUS_SKIPPED and out["irn"] is None


def test_an_unreadable_period_lock_refuses_the_refund(till, monkeypatch):
    _advance_order(till)
    locks = till["db"].get_collection("period_locks")
    monkeypatch.setattr(locks, "find_one", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    r = _advance_client().post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr("ACCOUNTANT"))
    assert r.status_code == 503 and till["db"].get_collection("returns").count_documents({}) == 0


def test_an_expired_request_is_never_reused_by_the_dedupe():
    eng, db = _engine()
    common = dict(action_type="REFUND_APPROVAL_MATRIX", requested_by="U-cash", requested_by_roles=["CASHIER"],
                  store_id=STORE, amount=2500.0, context={"order_id": "ORD-1"}, dedupe_key="refundp:ORD-1:250000")
    first = eng.request(**common)
    db.get_collection("approval_requests").update_one(
        {"request_id": first["request_id"]}, {"$set": {"expires_at": datetime.now(timezone.utc) - timedelta(minutes=1)}})
    second = eng.request(**common)
    assert second["request_id"] != first["request_id"] and not second.get("deduped")


@pytest.mark.asyncio
async def test_the_generic_consume_route_refuses_a_refund_approval():
    from api.routers import approvals as ar

    with pytest.raises(HTTPException) as exc:
        await ar.consume_request("REQ-1", ar.ConsumeAction(action_type="REFUND_APPROVAL_MATRIX", amount=1.0),
                                 current_user=_user("CASHIER"))
    assert exc.value.status_code == 400


def test_the_store_credit_backfill_stops_on_a_non_numeric_balance():
    from scripts import backfill_store_credit_field as bf

    db = StrictDB()
    db["customers"].insert_one({"customer_id": "CUST-9", "store_credit": "500"})
    assert bf.plan(db)["non_numeric"] == [("CUST-9", "500")]


def test_the_loyalty_backfill_can_be_undone_exactly():
    from scripts import backfill_loyalty_tender_claims as bf

    db = StrictDB()
    db["loyalty_transactions"].insert_one({"txn_id": "T-1", "tender_payment_id": "p1",
                                           "tender_backfilled_at": datetime.now(timezone.utc)})
    db["loyalty_transactions"].insert_one({"txn_id": "T-2", "tender_payment_id": "p2"})  # a live claim
    assert bf.revert(db) == 1
    rows = {r["txn_id"]: r for r in db["loyalty_transactions"].find({})}
    assert "tender_payment_id" not in rows["T-1"] and rows["T-2"]["tender_payment_id"] == "p2"


def test_a_walk_in_store_credit_return_is_refused_before_a_number_is_taken(monkeypatch):
    from tests.test_returns_gst_refund import _qa_payload
    from tests.test_returns_refund_cap import _ctx

    minted = []
    monkeypatch.setattr(returns_router, "next_document_number", lambda *a, **k: minted.append(1) or "X")
    client, returns = _ctx(monkeypatch, amount_paid=2000.0, customer_id="walkin-77")
    monkeypatch.setattr(returns_router, "get_customer_repository",
                        lambda: type("R", (), {"find_by_id": lambda self, cid: None})())
    r = client.post("/api/v1/returns", json=_qa_payload(return_type="CREDIT_NOTE"), headers=_hdr("ADMIN"))
    assert r.status_code == 400 and "no customer record" in r.text, r.text
    assert minted == []


@pytest.mark.asyncio
async def test_a_superadmin_note_that_cannot_be_numbered_gives_the_order_back(till, monkeypatch):
    from api.routers.orders import admin_edit
    from api.routers.orders.models import SuperadminInvoiceChange, SuperadminEditLine
    from database.repositories.order_repository import InvoiceNumberError

    monkeypatch.setattr(admin_edit, "get_order_repository", lambda: till["orders"])
    monkeypatch.setattr(admin_edit, "_write_order_edit_audit", lambda **k: True)

    def _refuse(*a, **k):
        raise InvoiceNumberError("invoice counter unavailable")

    monkeypatch.setattr(admin_edit, "next_document_number", _refuse)
    line = dict(_LINE, item_type="FRAME", unit_price=5000.0, item_total=5000.0)
    till["db"].get_collection("orders").insert_one(_order(invoice_number="BOK01/2627/00009", items=[line],
                                                          grand_total=5000.0))
    body = SuperadminInvoiceChange(mode="CREDIT_NOTE", reason="price fix", items=[SuperadminEditLine(
        item_id="li1", item_type="FRAME", product_name="Frame", quantity=1, unit_price=4500.0)])
    with pytest.raises(HTTPException) as exc:
        await admin_edit.superadmin_invoice_change("ORD-1", body, _user("SUPERADMIN"))
    assert exc.value.status_code == 400
    assert _stored(till).get("post_invoice_credit_note_at") is None
