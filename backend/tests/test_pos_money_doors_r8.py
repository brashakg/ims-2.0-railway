"""
IMS 2.0 - POS money doors, round-8 money review of PR #1264 (head 4f80965f)
===========================================================================
Each finding pinned through the real code over strict fakes, asserting what was
stored (orders, returns, ledger, approvals, shipments) -- never a status alone.
No emoji (Windows cp1252).
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
from tests.test_returns_gst_refund import _qa_payload, _staff_token  # noqa: E402
from tests.test_returns_refund_cap import _ctx  # noqa: E402


def _ago(minutes):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()


# =========================================================================== #
# BLOCK 1: a courier booking in flight is a handover, both ways round
# =========================================================================== #
class _Booked:
    status = "BOOKED"; awb = "AWB1"; courier = "X"; label_url = None; sr_order_id = "1"  # noqa: E702
    shipment_id = "2"; tracking_status = None; tracking_url = None; error = None  # noqa: E702


def _shipping(till, monkeypatch):
    from api.routers import shipping

    ships = _ship(till, monkeypatch)
    monkeypatch.setattr(shipping, "_merge_address", lambda req, order: {"name": "Asha", "pincode": "411001"})

    async def _none(*_a, **_k):
        return None

    monkeypatch.setattr(shipping, "_maybe_push_online_fulfillment", _none)
    return shipping, ships


@pytest.mark.asyncio
async def test_a_refund_pressed_while_the_courier_is_called_is_refused(till, monkeypatch):
    from api.services import shiprocket

    _advance_order(till, status="READY")
    shipping, ships = _shipping(till, monkeypatch)
    monkeypatch.setattr(shipping, "_resolve_order", lambda oid: _stored(till, oid))
    seen = {}

    async def _slow_courier(order, address, db=None, pickup_location=None):
        r = _advance_client().post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr("ACCOUNTANT"))
        seen["refund"] = (r.status_code, r.text)
        return _Booked()

    monkeypatch.setattr(shiprocket, "create_shipment", _slow_courier)
    body = shipping.ShipmentCreate(order_id="ORD-1", address=shipping.ShipAddress(payment_method="COD"))
    await shipping.book_shipment(body, _user("ADMIN"))
    assert seen["refund"][0] == 409 and "handed over" in seen["refund"][1]
    assert _stored(till).get("advance_refund_id") is None and _stored(till)["status"] == "READY"
    assert till["db"].get_collection("returns").count_documents({}) == 0
    assert ships.count_documents({"order_id": "ORD-1", "status": "BOOKED"}) == 1


@pytest.mark.asyncio
async def test_a_refund_claimed_before_the_booking_lands_stops_it_before_the_courier(till, monkeypatch):
    from api.services import shiprocket

    _advance_order(till, status="READY")
    shipping, ships = _shipping(till, monkeypatch)
    stale = dict(_stored(till))
    reads = {"n": 0}

    def _read(oid):
        reads["n"] += 1
        return stale if reads["n"] == 1 else _stored(till, oid)

    monkeypatch.setattr(shipping, "_resolve_order", _read)
    # The refund door claims the order between the shipping door's read and its booking.
    till["db"].get_collection("orders").update_one({"order_id": "ORD-1"}, {"$set": {"advance_refund_id": "RET-X", "advance_refund_at": _ago(0)}})
    called = []

    async def _courier(*a, **k):
        called.append(1)
        return _Booked()

    monkeypatch.setattr(shiprocket, "create_shipment", _courier)
    body = shipping.ShipmentCreate(order_id="ORD-1", address=shipping.ShipAddress(payment_method="COD"))
    with pytest.raises(HTTPException) as exc:
        await shipping.book_shipment(body, _user("ADMIN"))
    assert exc.value.status_code == 409
    assert called == []
    assert [d["status"] for d in ships.find({"order_id": "ORD-1"})] == ["FAILED"]


def test_strict_shipment_check_refuses_when_shipments_cannot_be_read(monkeypatch):
    from api.routers import shipping

    monkeypatch.setattr(shipping, "_shipments_coll", lambda: None)
    assert shipping._live_shipment_for_order("ORD-1") is None
    with pytest.raises(Exception):
        shipping._live_shipment_for_order("ORD-1", strict=True)


# =========================================================================== #
# BLOCK 2: a return's credit note never lands without its credit
# =========================================================================== #
def test_a_failed_balance_move_files_no_credit_note_and_a_retry_files_one(till, monkeypatch):
    n = {"i": 0}

    def _num(*a, **k):
        n["i"] += 1
        return f"TST01/C2627/{n['i']:04d}"

    monkeypatch.setattr(returns_router, "next_document_number", _num)
    custs = till["db"].get_collection("customers")
    custs.insert_one({"customer_id": "CUST-1", "store_credit": 0.0})
    _crash_once(monkeypatch, custs, "update_one", lambda flt, upd, *a, **k: "$inc" in upd)
    kw = dict(reason="return", current_user=_user("ADMIN"), store_id=STORE, order_id="ORD-1")
    # Round 10: a raised write with no landed guard may still be in flight --
    # never voided on the spot; the reconciler voids it once it has resolved.
    with pytest.raises(returns_router.CreditNotConfirmed):
        returns_router._issue_store_credit("CUST-1", 200.0, ref="RET-A", **kw)
    from api.routers import customers as cust_router
    from database.repositories.customer_repository import CustomerRepository

    cust_router.reconcile_pending_credits(CustomerRepository(custs), ledger=_ledger(till), older_than_s=0)
    assert returns_router._issue_store_credit("CUST-1", 200.0, ref="RET-B", **kw) is not None
    issued = list(_ledger(till).find({"customer_id": "CUST-1", "type": "ISSUED"}))
    assert [r["ref"] for r in issued] == ["RET-B"] and issued[0]["applied"] is True
    assert [r["type"] for r in _ledger(till).find({"ref": "RET-A"})] == ["VOID"]
    assert custs.find_one({"customer_id": "CUST-1"})["store_credit"] == 200.0


def test_a_walk_in_credit_is_refused_before_anything_is_written(till, monkeypatch):
    monkeypatch.setattr(returns_router, "next_document_number", lambda *a, **k: "TST01/C2627/0009")
    for _ in range(2):
        assert returns_router._issue_store_credit("walkin-77", 200.0, reason="r", ref="RET-W",
                                                  current_user=_user("ADMIN"), store_id=STORE,
                                                  order_id="ORD-W") is None
    assert _ledger(till).count_documents({"customer_id": "walkin-77"}) == 0


def test_a_row_still_issuing_is_never_summed(till):
    from api.services import store_credit_ledger as scl

    rows = [{"delta": 500.0}, {"delta": 300.0, "applied": False, "type": "ISSUING"}, {"delta": 200.0, "applied": True}]
    assert scl.compute_balance(rows) == 700.0


# =========================================================================== #
# BLOCK 3: a refund approval is bound to one bill and one amount
# =========================================================================== #
def _engine(db=None):
    from api.services.approvals import ApprovalEngine

    db = db or StrictDB()
    db.get_collection("orders").insert_one(_order(order_number="BV-0001", customer_name="Asha K"))
    return ApprovalEngine(db=db), db


def test_a_refund_approval_with_no_amount_cannot_be_raised():
    eng, _ = _engine()
    common = dict(action_type="REFUND_APPROVAL_MATRIX", requested_by="U-cash", requested_by_roles=["CASHIER"],
                  store_id=STORE)
    assert eng.request(**common, context={"order_id": "ORD-1"})["error"] == "amount_and_order_required"
    assert eng.request(**common, amount=2500.0)["error"] == "amount_and_order_required"


def test_the_approver_sees_the_bills_own_number_and_customer():
    eng, db = _engine()
    res = eng.request(action_type="REFUND_APPROVAL_MATRIX", requested_by="U-cash", requested_by_roles=["CASHIER"],
                      store_id=STORE, amount=2500.0,
                      context={"order_id": "ORD-1", "order_number": "FAKE-9", "customer_name": "Someone Else"})
    ctx = db.get_collection("approval_requests").find_one({"request_id": res["request_id"]})["context"]
    assert (ctx["order_number"], ctx["customer_name"], ctx["already_refunded"]) == ("BV-0001", "Asha K", 0.0)


def _approved(db, **over):
    doc = {"request_id": "REQ-1", "action_type": "REFUND_APPROVAL_MATRIX", "status": "APPROVED",
           "consumed": False, "expires_at": datetime.now(timezone.utc) + timedelta(minutes=30),
           "store_id": STORE, "context": {"order_id": "ORD-1"}, "amount": None, "required_tier": "auto",
           "approval_token": "TOK-1", "requested_by": "U-cash"}
    doc.update(over)
    db.get_collection("approval_requests").insert_one(doc)


def test_an_amountless_approval_never_clears_a_refund():
    eng, db = _engine()
    _approved(db)
    res = eng.consume_approval(consumed_by="U-cash", action_type="REFUND_APPROVAL_MATRIX", request_id="REQ-1",
                               amount=45000.0, expected_store_id=STORE, expected_context={"order_id": "ORD-1"},
                               min_tier="auto")
    assert res == {"ok": False, "error": "amount_exceeded"}
    assert db.get_collection("approval_requests").find_one({"request_id": "REQ-1"})["consumed"] is False


def test_the_amount_binding_is_compared_in_paise():
    eng, db = _engine()
    _approved(db, amount=2500.0)
    res = eng.consume_approval(consumed_by="U-cash", action_type="REFUND_APPROVAL_MATRIX", request_id="REQ-1",
                               amount=2500.004, expected_store_id=STORE, expected_context={"order_id": "ORD-1"})
    assert res["ok"] is True


# =========================================================================== #
# SHOULD-FIX: the press that completes the refund says so, with the amount
# =========================================================================== #
@pytest.mark.usefixtures("gst_numbers")
def test_the_press_that_completes_a_refund_reports_the_amount(till, monkeypatch):
    _advance_order(till)
    hit = _crash_once(monkeypatch, till["orders"], "update_if",
                      lambda oid, exp, data, *a, **k: "advance_refund_status" in data)
    client = _advance_client()
    assert client.post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr("ACCOUNTANT")).status_code == 503
    assert hit["n"] == 1
    plan = client.get("/api/v1/returns/advance-refund/ORD-1", headers=_hdr("ACCOUNTANT")).json()
    assert plan["action"] == "finish_refund" and plan["total"] == 1000.0
    assert {t["method"]: t["amount"] for t in plan["refund_tenders"]} == {"CASH": 600.0, "UPI": 400.0}
    r2 = client.post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr("ACCOUNTANT"))
    assert r2.status_code == 201, r2.text
    assert r2.json()["refund_amount"] == 1000.0
    assert r2.json()["message"].startswith("Refund of Rs 1,000.00 recorded")
    assert till["db"].get_collection("returns").find_one({"order_id": "ORD-1"})["status"] == "COMPLETED"


# =========================================================================== #
# SHOULD-FIX: a failed points / stock undo inside the cancel is shown and re-pressable
# =========================================================================== #
def test_a_failed_loyalty_restore_is_shown_and_the_next_press_reruns_it(till, monkeypatch):
    from api.routers import loyalty as L

    _advance_order(till)
    monkeypatch.setattr(L, "reverse_for_cancel", lambda *a, **k: {"ok": False, "reason": "account_write_failed"})
    client = _advance_client()
    r = client.post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr("ACCOUNTANT"))
    assert r.status_code == 201 and "loyalty points" in (r.json()["cancel_error"] or ""), r.text
    assert _stored(till)["loyalty_reversal_failed"] is True
    monkeypatch.setattr(L, "reverse_for_cancel", lambda *a, **k: {"ok": True})
    r2 = client.post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr("ACCOUNTANT"))
    assert r2.status_code == 201, r2.text
    assert r2.json()["cancel_error"] is None
    assert _stored(till)["loyalty_reversal_failed"] is False


# =========================================================================== #
# SHOULD-FIX: a superadmin credit note and the advance door exclude each other
# =========================================================================== #
@pytest.mark.usefixtures("gst_numbers")
def test_the_advance_door_refuses_an_order_with_an_admin_credit_note(till):
    till["db"].get_collection("customers").insert_one({"customer_id": "CUST-1", "store_credit": 500.0})
    _advance_order(till, invoice_number="BOK01/2627/00009", items=[dict(_LINE)])
    _ledger(till).insert_one({"entry_id": "E1", "customer_id": "CUST-1", "type": "ISSUED", "delta": 500.0,
                              "amount": 500.0, "order_id": "ORD-1", "note_number": "TST01/C2627/0001"})
    r = _advance_client().post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr("ACCOUNTANT"))
    assert r.status_code == 400 and "credit note" in r.text, r.text
    assert till["db"].get_collection("returns").count_documents({}) == 0
    assert _stored(till).get("advance_refund_id") is None


def test_the_advance_claim_loses_to_an_admin_credit_note_marker(till):
    _advance_order(till, post_invoice_credit_note_at=_ago(1))
    r = _advance_client().post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr("ACCOUNTANT"))
    assert r.status_code == 400 and "credit note" in r.text
    assert _stored(till).get("advance_refund_id") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("over", [{"advance_refund_id": "RET-1", "advance_refund_record": "RET-1"},
                                  {"status": "CANCELLED"}])
async def test_no_admin_credit_note_on_a_refunded_or_cancelled_order(till, monkeypatch, over):
    from api.routers.orders import admin_edit
    from api.routers.orders.models import SuperadminInvoiceChange

    monkeypatch.setattr(admin_edit, "get_order_repository", lambda: till["orders"])
    monkeypatch.setattr(admin_edit, "_get_db", lambda: None, raising=False)
    till["db"].get_collection("orders").insert_one(
        _order(invoice_number="BOK01/2627/00009", items=[dict(_LINE)], amount_paid=1000.0, **over))
    with pytest.raises(HTTPException) as exc:
        await admin_edit.superadmin_invoice_change(
            "ORD-1", SuperadminInvoiceChange(mode="CREDIT_NOTE", reason="price fix"), _user("SUPERADMIN"))
    assert exc.value.status_code == 409
    assert _ledger(till).count_documents({}) == 0


# =========================================================================== #
# SHOULD-FIX: a return that died between its hold and its doc ages out
# =========================================================================== #
def _two_unit_bill(till, monkeypatch, **over):
    line = {"item_id": "li1", "product_id": "P1", "product_name": "Frame", "sku": "F1", "quantity": 2,
            "unit_price": 1525.42, "gst_rate": 18.0, "taxable_value": 3050.85, "tax_amount": 549.15}
    till["db"].get_collection("orders").insert_one(_order(
        status="DELIVERED", grand_total=3600.0, amount_paid=3600.0, balance_due=0.0, payment_status="PAID",
        items=[line], payments=[{"payment_id": "p1", "method": "CASH", "amount": 3600.0}], **over))
    monkeypatch.setattr(returns_router, "get_stock_repository", lambda: None)
    monkeypatch.setattr(returns_router, "get_product_repository", lambda: None)
    monkeypatch.setattr(returns_router, "next_document_number", lambda *a, **k: "TST01/C2627/0001")
    monkeypatch.setattr(returns_router, "_claim_returnable_qty", lambda *a, **k: True)
    monkeypatch.setattr(returns_router, "_release_returnable_qty", lambda *a, **k: True)
    return {"order_id": "ORD-1", "return_type": "RETURN", "items": [{
        "order_item_id": "li1", "product_id": "P1", "product_name": "Frame", "sku": "F1",
        "return_qty": 1, "unit_price": 1525.42, "reason": "DEFECTIVE", "condition": "GOOD"}]}


@pytest.mark.parametrize("minutes_ago, status", [(10, 201), (0, 403)])
def test_a_dead_returns_hold_ages_out_but_a_live_one_counts(till, monkeypatch, minutes_ago, status):
    payload = _two_unit_bill(till, monkeypatch, refund_holds_v=1,
                             refund_holds={"RET-DEAD": {"amount": 1800.0, "at": _ago(minutes_ago)}})
    r = _advance_client().post("/api/v1/returns", json=payload, headers=_hdr("CASHIER"))
    assert r.status_code == status, r.text
    holds = _stored(till).get("refund_holds") or {}
    assert ("RET-DEAD" in holds) == (minutes_ago == 0)


def test_a_failed_return_gives_its_hold_and_its_approval_back(till, monkeypatch):
    payload = _two_unit_bill(till, monkeypatch)
    payload["items"][0]["return_qty"] = 2
    eng_db = till["db"]
    _approved(eng_db, amount=3600.0, request_id="REQ-9", approval_token="TOK-9")
    payload["refund_approval_request_id"] = "REQ-9"

    def _boom(*a, **k):
        raise RuntimeError("worker died after the gate")

    monkeypatch.setattr(returns_router, "_claim_returnable_qty", _boom)
    with pytest.raises(RuntimeError):
        _advance_client().post("/api/v1/returns", json=payload, headers=_hdr("CASHIER"))
    req = eng_db.get_collection("approval_requests").find_one({"request_id": "REQ-9"})
    assert (req["status"], req["consumed"]) == ("APPROVED", False)
    assert not (_stored(till).get("refund_holds") or {})


# =========================================================================== #
# SHOULD-FIX: the GST view never matches cancelled orders by order_id alone
# =========================================================================== #
def test_the_gst_view_excludes_cancelled_orders_by_their_own_id():
    from api.utils.real_sale import real_sale_match

    db = StrictDB()
    orders = db.get_collection("orders")
    for doc in ({"_id": "a", "order_id": "INV1", "status": "DELIVERED", "invoice_number": "INV/1"},
                {"_id": "b", "order_id": None, "status": "CANCELLED", "invoice_number": "OLD/001"},
                {"_id": "c", "status": "CANCELLED", "invoice_number": "OLD/002"},
                {"_id": "d", "order_id": "dup", "status": "DELIVERED", "invoice_number": "INV/9"},
                {"_id": "e", "order_id": "dup", "status": "CANCELLED"}):
        orders.insert_one(doc)
    filed = sorted(o["invoice_number"] for o in orders.find(real_sale_match(db, gst_documents=True)))
    assert filed == ["INV/1", "INV/9"]


def test_books_drop_a_refunded_order_whose_cancel_is_pending():
    from api.utils.real_sale import is_real_sale, real_sale_match

    db = StrictDB()
    db.get_collection("orders").insert_one({"_id": "a", "order_id": "ORD-1", "status": "CONFIRMED",
                                            "invoice_number": "INV/1", "advance_refund_status": "COMPLETED",
                                            "advance_refund_credit_note": "TST01/C2627/0001"})
    order = db.get_collection("orders").find_one({"order_id": "ORD-1"})
    assert is_real_sale(order) is False and is_real_sale(order, gst_documents=True) is True
    assert db.get_collection("orders").count_documents(real_sale_match(db)) == 0
    assert db.get_collection("orders").count_documents(real_sale_match(db, gst_documents=True)) == 1


# =========================================================================== #
# SHOULD-FIX: the bill's closed month is never rewritten; section 34; IRN
# =========================================================================== #
def test_a_locked_month_bill_is_refused(till):
    _advance_order(till, created_at="2026-09-15T10:00:00")
    till["db"].get_collection("period_locks").insert_one({"month": 9, "year": 2026})
    r = _advance_client().post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr("ACCOUNTANT"))
    assert r.status_code == 423, r.text
    assert till["db"].get_collection("returns").count_documents({}) == 0
    assert _stored(till)["status"] == "CONFIRMED"


@pytest.mark.parametrize("over, words", [
    ({"created_at": "2024-05-01T10:00:00"}, "section 34"),
    ({"irn": "IRN-123"}, "e-invoice"),
])
def test_an_invoice_past_the_credit_note_window_or_with_an_irn_is_refused(till, over, words):
    _advance_order(till, invoice_number="BOK01/2627/00009", items=[dict(_LINE)], **over)
    r = _advance_client().post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr("ACCOUNTANT"))
    assert r.status_code == 400 and words in r.text, r.text
    assert till["db"].get_collection("returns").count_documents({}) == 0


# =========================================================================== #
# SHOULD-FIX: the Rs 2,000 refusal names the bill's running total
# =========================================================================== #
@pytest.mark.usefixtures("gst_numbers")
def test_the_refusal_names_the_bills_running_total(monkeypatch):
    client, returns = _ctx(monkeypatch, qty=2)
    returns.insert_one({"return_id": "RET-0", "order_id": "ORD-BOK01", "status": "COMPLETED", "net_refund": 1900.0})
    r = client.post("/api/v1/returns", json=_qa_payload(), headers=_hdr("CASHIER"))
    assert r.status_code == 403, r.text
    d = r.json()["detail"]
    assert (d["prior_refunds"], d["this_refund"], d["running_total"]) == (1900.0, 1404.2, 3304.2)
    assert "Rs 3,304.20" in d["message"] and "Rs 1,900.00 already refunded" in d["message"]


@pytest.mark.usefixtures("gst_numbers")
def test_the_order_of_a_users_roles_makes_no_difference(monkeypatch):
    client, returns = _ctx(monkeypatch, qty=2)
    payload = _qa_payload()
    payload["items"][0]["return_qty"] = 2
    hdr = {"Authorization": f"Bearer {_staff_token(['CASHIER', 'STORE_MANAGER'])}"}
    r = client.post("/api/v1/returns", json=payload, headers=hdr)
    assert r.status_code == 201, r.text


# =========================================================================== #
# SHOULD-FIX: backfills
# =========================================================================== #
def test_store_credit_backfill_leaves_unapplied_rows_out_instead_of_locking_the_customer_out(tmp_path):
    from scripts import backfill_store_credit_field as bf

    db = StrictDB()
    db["customers"].insert_one({"customer_id": "CUST-1", "name": "Asha", "phone": "9999999999"})
    db["customers"].insert_one({"customer_id": "CUST-2"})
    db["credit_note_ledger"].insert_one({"customer_id": "CUST-1", "entry_id": "a", "delta": 300.0})
    db["credit_note_ledger"].insert_one({"customer_id": "CUST-1", "entry_id": "RET-A:credit", "delta": 1000.0,
                                         "applied": False})
    db["credit_note_ledger"].insert_one({"customer_id": "CUST-2", "entry_id": "b", "delta": -50.0})
    out = bf.plan(db)
    assert out["sets"] == [("CUST-1", 300.0)]
    assert out["unapplied_left"] == ["CUST-1"] and out["negative"] == [("CUST-2", -50.0)]
    backup = str(tmp_path / "b.json")
    assert bf.apply(db, out["sets"], backup) == 1
    import json

    assert json.load(open(backup, encoding="utf-8")) == [{"customer_id": "CUST-1"}]
    cust = db["customers"].find_one({"customer_id": "CUST-1"})
    assert (cust["store_credit"], cust["store_credit_backfill_value"]) == (300.0, 300.0)
    assert bf.revert(db, backup) == 1
    assert "store_credit" not in db["customers"].find_one({"customer_id": "CUST-1"})


def test_a_gst_only_ledger_row_does_not_make_a_customer_unready(till):
    from api.routers.customers import store_credit_balance_unready
    from database.repositories.customer_repository import CustomerRepository

    till["db"].get_collection("customers").insert_one({"customer_id": "CUST-1"})
    _ledger(till).insert_one({"customer_id": "CUST-1", "type": "ISSUED", "delta": 0.0, "entry_id": "z"})
    _ledger(till).insert_one({"customer_id": "CUST-1", "note_type": "CREDIT_NOTE", "source": "SUPERADMIN_ORDER_EDIT"})
    repo = CustomerRepository(till["db"].get_collection("customers"))
    assert store_credit_balance_unready(repo, "CUST-1") is False
    _ledger(till).insert_one({"customer_id": "CUST-1", "type": "ISSUED", "delta": 400.0, "entry_id": "y"})
    assert store_credit_balance_unready(repo, "CUST-1") is True


@pytest.mark.asyncio
async def test_a_burn_an_old_loyalty_payment_already_names_cannot_pay_again(till):
    from api.routers.orders import PaymentCreate, add_payment

    till["db"].get_collection("orders").insert_one(_order(grand_total=1000.0, balance_due=1000.0))
    txn = till["db"].get_collection("loyalty_transactions")
    txn.insert_one({"txn_id": "T-OLD", "customer_id": "CUST-1", "order_id": "ORD-1", "type": "REDEEM",
                    "points": -300, "rupee_value": 300.0})
    # The old till recorded the LOYALTY payment with no claim on the burn.
    till["db"].get_collection("orders").update_one({"order_id": "ORD-1"}, {"$push": {"payments": {
        "payment_id": "p-old", "method": "LOYALTY", "amount": 300.0, "reference": "300pts txn T-OLD"}},
        "$set": {"amount_paid": 300.0, "balance_due": 700.0}})
    with pytest.raises(HTTPException) as exc:
        await add_payment("ORD-1", PaymentCreate(method="LOYALTY", amount=300.0), current_user=_user("CASHIER"))
    assert exc.value.status_code == 400
    assert _stored(till)["amount_paid"] == 300.0


def test_the_loyalty_backfill_lists_payments_no_burn_backs():
    from scripts import backfill_loyalty_tender_claims as bf

    db = StrictDB()
    db["orders"].insert_one(_order(payments=[{"payment_id": "p1", "method": "LOYALTY", "amount": 300.0,
                                              "reference": "300pts txn NOPE"}]))
    db["loyalty_transactions"].insert_one({"txn_id": "T-0", "type": "REDEEM", "order_id": None})
    out = bf.plan(db)
    assert out["unbacked"] == [("ORD-1", "p1", 300.0, "300pts txn NOPE")] and out["no_order"] == 1


# =========================================================================== #
# SHOULD-FIX: the referral reward goes through THE balance rule
# =========================================================================== #
def _referral(till, monkeypatch, cust):
    from api.routers import marketing
    from database.repositories.customer_repository import CustomerRepository

    db = till["db"]
    monkeypatch.setattr(marketing, "_get_db", lambda: db)
    monkeypatch.setattr("api.dependencies.get_customer_repository",
                        lambda: CustomerRepository(db.get_collection("customers")))
    db.get_collection("customers").insert_one(cust)
    db.get_collection("referrals").insert_one({"referral_id": "REF-1", "referrer_customer_id": "CUST-1",
                                               "status": "COMPLETED", "reward_amount": 100.0, "store_id": STORE})
    return marketing


@pytest.mark.asyncio
async def test_a_referral_reward_is_refused_for_an_unready_customer_before_the_claim(till, monkeypatch):
    marketing = _referral(till, monkeypatch, {"customer_id": "CUST-1"})
    _ledger(till).insert_one({"customer_id": "CUST-1", "type": "ISSUED", "delta": 500.0, "entry_id": "old"})
    with pytest.raises(HTTPException) as exc:
        await marketing.redeem_referral("REF-1", current_user=_user("ADMIN"))
    assert exc.value.status_code == 503
    assert till["db"].get_collection("referrals").find_one({"referral_id": "REF-1"})["status"] == "COMPLETED"
    assert "store_credit" not in till["db"].get_collection("customers").find_one({"customer_id": "CUST-1"})
    assert [r["entry_id"] for r in _ledger(till).find({"customer_id": "CUST-1"})] == ["old"]  # no new row


@pytest.mark.asyncio
async def test_a_referral_reward_writes_its_ledger_row_and_moves_the_balance_once(till, monkeypatch):
    marketing = _referral(till, monkeypatch, {"customer_id": "CUST-1", "store_credit": 0.0})
    await marketing.redeem_referral("REF-1", current_user=_user("ADMIN"))
    rows = list(_ledger(till).find({"customer_id": "CUST-1"}))
    assert [(r["type"], r["delta"], r["applied"]) for r in rows] == [("ADJUSTED", 100.0, True)]
    assert till["db"].get_collection("customers").find_one({"customer_id": "CUST-1"})["store_credit"] == 100.0


# =========================================================================== #
# NITs
# =========================================================================== #
def test_upi_auto_reconcile_never_pays_an_order_an_advance_refund_holds(till):
    from api.services import upi_qr

    till["db"].get_collection("orders").insert_one(_order(advance_refund_id="RET-1"))
    assert upi_qr._do_reconcile(till["db"], "ORD-1", {"amount": 100000, "id": "pay_1"}) is False
    assert _stored(till)["payments"] == []


def test_a_handover_undo_landing_after_the_cancel_sweep_ends_the_job(till, monkeypatch):
    from api.routers.workshop import gates
    from database.repositories.workshop_repository import WorkshopJobRepository

    jobs = WorkshopJobRepository(till["db"].get_collection("workshop_jobs"))
    jobs.collection.insert_one({"job_id": "J1", "order_id": "ORD-1", "status": "DELIVERED"})
    reads = iter([{"order_id": "ORD-1", "status": "READY", "advance_refund_id": "RET-1"},
                  {"order_id": "ORD-1", "status": "CANCELLED", "advance_refund_id": "RET-1"}])
    monkeypatch.setattr(gates, "_resolve_job_order", lambda job: next(reads))
    assert gates.undo_handover_if_order_refunded(jobs, {"job_id": "J1", "order_id": "ORD-1"}, "READY") is True
    assert jobs.collection.find_one({"job_id": "J1"})["status"] == "CANCELLED"


def test_the_strict_fake_refuses_a_duplicate_id():
    from pymongo.errors import DuplicateKeyError

    coll = StrictDB().get_collection("x")
    coll.insert_one({"_id": "a"})
    with pytest.raises(DuplicateKeyError):
        coll.insert_one({"_id": "a"})


@pytest.mark.usefixtures("gst_numbers")
def test_the_whole_invoice_note_reverses_the_invoices_own_tax(till):
    line = dict(_LINE, taxable_value=4761.91, tax_amount=238.09)
    _advance_order(till, invoice_number="BOK01/2627/00009", items=[line])
    r = _advance_client().post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr("ACCOUNTANT"))
    assert r.status_code == 201, r.text
    gst = till["db"].get_collection("returns").find_one({"order_id": "ORD-1"})["gst_breakup"]
    assert (gst["taxable"], gst["tax"]) == (4761.91, 238.09)


def test_no_drawer_cash_once_the_shops_day_end_is_counted(till):
    from api.utils.ist import ist_today

    _advance_order(till)
    till["db"].get_collection("till_sessions").insert_one(
        {"store_id": STORE, "session_date": ist_today().isoformat(), "status": "LOCKED"})
    client = _advance_client()
    r = client.post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr("ACCOUNTANT"))
    assert r.status_code == 409 and "Day-End" in r.text, r.text
    assert till["db"].get_collection("returns").count_documents({}) == 0
    r2 = client.post("/api/v1/returns/advance-refund", json=dict(_ADV_BODY, cash_paid_from="HQ"),
                     headers=_hdr("ACCOUNTANT"))
    assert r2.status_code == 201, r2.text


@pytest.mark.usefixtures("gst_numbers")
def test_a_stopped_press_number_is_written_off_by_name_once_the_next_press_wins(till, monkeypatch):
    written_off = []
    monkeypatch.setattr(returns_router, "_record_unused_credit_note",
                        lambda number, *a, **k: written_off.append(number))
    _advance_order(till, invoice_number="BOK01/2627/00009", items=[dict(_LINE)])
    _crash_once(monkeypatch, till["orders"], "update_if",
                lambda oid, exp, data, *a, **k: bool(data.get("advance_refund_credit_note")))
    client = _advance_client()
    assert client.post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr("ACCOUNTANT")).status_code == 503
    minted = _stored(till)["advance_refund_cn_minted"]
    till["db"].get_collection("orders").update_one({"order_id": "ORD-1"},
                                                   {"$set": {"advance_refund_cn_minting_at": _ago(10)}})
    r = client.post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr("ACCOUNTANT"))
    assert r.status_code == 201, r.text
    assert written_off == [minted] and r.json()["credit_note_number"] != minted


def test_an_admin_credit_note_landing_before_the_claim_wins(till, monkeypatch):
    # The door's read missed the marker (it landed a moment later): the claim's
    # compare-and-set on it refuses, so exactly one of the two goes through.
    _advance_order(till, post_invoice_credit_note_at=_ago(0))
    monkeypatch.setattr(returns_router, "_order_has_other_credit_note", lambda order: False)
    r = _advance_client().post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr("ACCOUNTANT"))
    assert r.status_code == 409, r.text
    assert _stored(till).get("advance_refund_id") is None


def test_an_admin_credit_note_landing_after_the_plan_is_caught_after_the_claim(till, monkeypatch):
    _advance_order(till)
    real = returns_router._order_has_other_credit_note
    calls = {"n": 0}

    def _late(order):
        calls["n"] += 1
        if calls["n"] == 1:
            return False  # the plan read: nothing yet
        _ledger(till).insert_one({"entry_id": "E9", "customer_id": "CUST-1", "type": "ISSUED", "delta": 100.0,
                                  "order_id": "ORD-1"})
        return real(order)

    monkeypatch.setattr(returns_router, "_order_has_other_credit_note", _late)
    r = _advance_client().post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr("ACCOUNTANT"))
    assert r.status_code == 409 and "credit note" in r.text, r.text
    assert till["db"].get_collection("returns").count_documents({}) == 0
    assert _stored(till).get("advance_refund_id") is None
