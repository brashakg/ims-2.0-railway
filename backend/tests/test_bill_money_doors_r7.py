"""
IMS 2.0 - every door that gives money back on a bill takes ONE reservation (#1397 round 7).
==========================================================================================
Codex round 6 required every money-issuing door of a bill to take one shared
reservation, with what the bill still owes read and booked under it. Two doors
did not: the admin invoice-change credit note (orders/admin_edit) and the
advance refund. Native-Mongo probe (claude_r7_doors.py) on a Rs 1,000 cash bill:
an admin Rs 100 credit note overlapping a counter return or an online refund,
in either order, gave Rs 1,100 back; so did the note pressed AFTER the counter
had refunded the whole bill (the note had no cap at all). And a counter return
of line A followed by the online refund of the same line A paid line A twice
(each capped only by the bill total).

Now: returns.take_bill_hold is the one reservation (online, admin note,
advance); a counter return refuses another door's live hold on every bill; the
counter reads its ceiling after its reservation; the admin note is capped by
what was paid less everything given back; the online allowance is capped per
order line (a unit the counter already took back is never paid again).

Real code over the strict fakes of test_pos_money_doors (till), the counter
endpoint fakes of test_returns_endpoint_tender_capture (ctx) and the online
refund fakes of test_refund_review_unpaid_credit (env). No emoji (cp1252).
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MONGODB_URI", "")

from fastapi import HTTPException  # noqa: E402

from api.routers import returns as returns_router  # noqa: E402
from tests.test_pos_money_doors import (  # noqa: E402,F401
    _ADV_BODY,
    _LINE,
    _advance_client,
    _advance_order,
    _hdr,
    _ledger,
    _order,
    _stored,
    _user,
    till,
)
from tests.test_refund_review_unpaid_credit import _balance, _payload, env  # noqa: E402,F401
from tests.test_refund_review_unpaid_credit import _order as _online_order  # noqa: E402
from tests.test_return_points_paid_race import _with_compare_and_set  # noqa: E402
from tests.test_return_points_paid_once import _line, _post  # noqa: E402
from tests.test_returns_endpoint_tender_capture import _BILLED_GROSS, ctx  # noqa: E402,F401

pytestmark = pytest.mark.usefixtures("gst_numbers")


def _now():
    return datetime.now(timezone.utc).isoformat()


# --- the admin invoice-change credit note --------------------------------------


def _invoiced(till, **over):
    till["db"].get_collection("customers").insert_one({"customer_id": "CUST-1", "store_credit": 0.0})
    line = dict(_LINE, item_type="FRAME", unit_price=1000.0, item_total=1000.0,
                taxable_value=952.38, tax_amount=47.62)
    till["db"].get_collection("orders").insert_one(_order(
        invoice_number="BOK01/2627/00009", items=[line], grand_total=1000.0, amount_paid=1000.0,
        balance_due=0.0, payment_status="PAID",
        payments=[{"payment_id": "p1", "method": "CASH", "amount": 1000.0}], **over))


async def _admin_note(till, monkeypatch):
    """A Rs 100 price correction (1000 -> 900) as a CREDIT_NOTE."""
    from api.routers.orders import admin_edit
    from api.routers.orders.models import SuperadminEditLine, SuperadminInvoiceChange

    monkeypatch.setattr(admin_edit, "get_order_repository", lambda: till["orders"])
    monkeypatch.setattr(admin_edit, "next_document_number", lambda *a, **k: "TST01/C2627/0071")
    monkeypatch.setattr(admin_edit, "_write_order_edit_audit", lambda **k: True)
    body = SuperadminInvoiceChange(mode="CREDIT_NOTE", reason="price fix", items=[SuperadminEditLine(
        item_id="li1", item_type="FRAME", product_name="Frame", quantity=1, unit_price=900.0)])
    return await admin_edit.superadmin_invoice_change("ORD-1", body, _user("SUPERADMIN"))


def _credit_rows(till):
    return [(r["type"], r["delta"]) for r in _ledger(till).find({"customer_id": "CUST-1"}) if "delta" in r]


@pytest.mark.asyncio
async def test_an_admin_note_waits_while_a_counter_return_is_being_written(till, monkeypatch):
    """S1: the counter reserved the whole bill and is writing its credit."""
    _invoiced(till, refund_holds={"RET-C": {"amount": 1000.0, "at": _now()}}, refund_holds_v=1)
    with pytest.raises(HTTPException) as exc:
        await _admin_note(till, monkeypatch)
    assert exc.value.status_code == 409 and "Nothing was issued" in exc.value.detail
    assert _credit_rows(till) == []
    assert _stored(till).get("post_invoice_credit_note_at") is None
    assert list(_stored(till)["refund_holds"]) == ["RET-C"]


@pytest.mark.asyncio
async def test_an_admin_note_waits_while_an_online_refund_posts(till, monkeypatch):
    """S3: the online post holds the bill (its allowance is being read)."""
    _invoiced(till, refund_holds={"shopify:R1": {"amount": 1000.0, "at": _now(), "door": "online", "ttl": 900}},
              refund_holds_v=1)
    with pytest.raises(HTTPException) as exc:
        await _admin_note(till, monkeypatch)
    assert exc.value.status_code == 409
    assert _credit_rows(till) == []


@pytest.mark.asyncio
async def test_a_counter_return_and_an_online_refund_wait_while_an_admin_note_posts(till, monkeypatch):
    """S2 + S4: pressed while the note's credit is being written."""
    from api.services import shopify_refund as S

    _invoiced(till)
    real, seen = returns_router._issue_store_credit, []

    def _inside(*a, **k):
        if ":sacn:" in str(k.get("entry_id") or "") and not seen:
            try:
                seen.append(returns_router._reserve_order_refund("ORD-1", 1000.0, "RET-C", _stored(till)))
            except HTTPException as exc:
                seen.append(exc.status_code)
            seen.append(S._hold_order_for_online(till["db"], "ORD-1", "R1", 1000.0))
        return real(*a, **k)

    monkeypatch.setattr(returns_router, "_issue_store_credit", _inside)
    out = await _admin_note(till, monkeypatch)
    assert out["note_type"] == "CREDIT_NOTE" and out["amount"] == 100.0
    assert seen == [409, False], seen
    assert _credit_rows(till) == [("ISSUED", 100.0)]
    assert _stored(till)["refund_holds"] == {}  # the note's hold went with it
    assert _stored(till).get("refund_credit_lock") is None


@pytest.mark.asyncio
async def test_an_admin_note_never_gives_back_more_than_was_paid(till, monkeypatch):
    """S5: the counter already refunded the whole Rs 1,000; a Rs 100 note on
    top paid Rs 1,100 for Rs 1,000."""
    _invoiced(till)
    till["db"].get_collection("returns").insert_one({
        "return_id": "RET-C", "order_id": "ORD-1", "status": "COMPLETED", "net_refund": 1000.0,
        "items": [{"order_item_id": "li1", "product_id": "P1", "return_qty": 1}]})
    with pytest.raises(HTTPException) as exc:
        await _admin_note(till, monkeypatch)
    assert exc.value.status_code == 409 and "Only Rs 0.00" in exc.value.detail
    assert _credit_rows(till) == []
    assert _stored(till).get("post_invoice_credit_note_at") is None
    assert _stored(till).get("refund_holds") == {}


@pytest.mark.asyncio
async def test_an_admin_note_within_what_is_left_is_issued(till, monkeypatch):
    """Control: Rs 500 refunded of Rs 1,000 -- a Rs 100 note still goes out."""
    _invoiced(till)
    till["db"].get_collection("returns").insert_one({
        "return_id": "RET-C", "order_id": "ORD-1", "status": "COMPLETED", "net_refund": 500.0, "items": []})
    out = await _admin_note(till, monkeypatch)
    assert out["amount"] == 100.0 and _credit_rows(till) == [("ISSUED", 100.0)]


# --- the advance refund ---------------------------------------------------------


def test_an_advance_refund_waits_while_another_door_holds_the_bill(till):
    _advance_order(till, refund_holds={"credit-note:x": {"amount": 100.0, "at": _now(), "door": "credit_note",
                                                         "ttl": 900}}, refund_holds_v=1)
    r = _advance_client().post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr("ACCOUNTANT"))
    assert r.status_code == 409, r.text
    assert till["db"].get_collection("returns").count_documents({}) == 0
    assert _stored(till).get("advance_refund_id") is None and _stored(till)["status"] == "CONFIRMED"
    # The other door finished: the refund goes through and leaves no hold.
    till["db"].get_collection("orders").update_one({"order_id": "ORD-1"}, {"$set": {"refund_holds": {}}})
    r = _advance_client().post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr("ACCOUNTANT"))
    assert r.status_code == 201, r.text
    assert _stored(till)["refund_holds"] == {} and _stored(till)["status"] == "CANCELLED"


# --- the counter -----------------------------------------------------------------


def test_a_counter_return_counts_a_credit_note_booked_while_it_was_priced(ctx, monkeypatch):
    """S1b: an admin note lands after the counter priced the return, before
    its reservation. The ceiling is read after the reservation."""
    c = ctx([{"method": "CASH", "amount": _BILLED_GROSS}])
    _with_compare_and_set(c)
    real = returns_router._refuse_while_a_return_settles

    def _note_lands(order, order_id):
        c["ledger"].docs.append({"entry_id": "ORD-INC-1:sacn:10000", "order_id": "ORD-INC-1",
                                 "customer_id": "CUST-1", "type": "ISSUED", "delta": 100.0,
                                 "ref": "TST01/C2627/0072"})
        return real(order, order_id)

    monkeypatch.setattr(returns_router, "_refuse_while_a_return_settles", _note_lands)
    r = _post(c, [{"method": "CASH", "amount": _BILLED_GROSS}])
    assert r.status_code == 400 and "exceed" in r.text, r.text
    assert c["returns"].docs == [] and c["order"].get("refund_holds") == {}


def test_a_counter_return_waits_for_an_online_refund_of_the_bill_on_a_cash_bill(ctx):
    """The same line pressed at both doors at once: the online post's hold
    refuses the counter on every bill (the bill cap alone let both pay)."""
    c = ctx([{"method": "CASH", "amount": _BILLED_GROSS}], qty=2)
    _with_compare_and_set(c)
    c["order"]["refund_holds"] = {"shopify:R1": {"amount": _BILLED_GROSS / 2, "at": _now(), "door": "online",
                                                 "ttl": 900}}
    c["order"]["refund_holds_v"] = 1
    r = _post(c, [{"method": "CASH", "amount": _BILLED_GROSS / 2}], items=_line(1))
    assert r.status_code == 409, r.text
    assert c["returns"].docs == []
    assert list(c["order"]["refund_holds"]) == ["shopify:R1"]


# --- the online refund, per line -------------------------------------------------


def _two_line_bill(db):
    _online_order(db, payments=[{"method": "CASH", "amount": 1000.0}], items=[
        {"item_id": "it-1", "product_id": "7001", "ims_product_id": "IMS-P-1", "shopify_line_item_id": 9001,
         "sku": "RB-1", "product_name": "RB", "quantity": 1, "gst_rate": 5.0, "taxable_value": 476.19,
         "tax_amount": 23.81},
        {"item_id": "it-2", "product_id": "7002", "ims_product_id": "IMS-P-2", "shopify_line_item_id": 9002,
         "sku": "RB-2", "product_name": "RB2", "quantity": 1, "gst_rate": 5.0, "taxable_value": 476.19,
         "tax_amount": 23.81}])
    # The counter already took line A back and refunded its Rs 500.
    db.get_collection("returns").insert_one({
        "return_id": "RET-C", "order_id": "ord-1", "status": "COMPLETED", "net_refund": 500.0,
        "items": [{"order_item_id": "it-1", "product_id": "IMS-P-1", "return_qty": 1}]})


def _line_refund(line_id, refund_id):
    p = _payload(refund_id=refund_id, txns=[])
    p["refund_line_items"][0].update(line_item_id=line_id, subtotal=476.19, total_tax=23.81,
                                     line_item={"id": line_id, "sku": "RB", "quantity": 1})
    return p


def test_an_online_refund_never_pays_again_for_a_line_the_counter_took_back(env):
    """S6: line A returned at the counter (Rs 500), then Shopify refunds the
    same line A: no second Rs 500 on a Rs 1,000 bill whose line B the customer
    still holds."""
    from api.services.shopify_refund import handle_shopify_refund, post_from_review

    _two_line_bill(env)
    assert handle_shopify_refund(env, _line_refund(9001, "700001"), topic="refunds/create")["status"] == "queued"
    row = env.get_collection("shopify_refund_review").find_one({"shopify_refund_id": "700001"})
    assert row["credit_note"]["store_credit"] == 0.0
    assert "already took back" in row["credit_note"]["store_credit_reason"]
    res = post_from_review(env, row)
    assert res["status"] == "credited" and res.get("store_credit") in (0.0, None), res
    assert _balance(env) == (0.0, 0.0)


def test_an_online_refund_of_the_other_line_is_still_paid(env):
    """Control: Shopify refunds line B (still with the customer): Rs 500."""
    from api.services.shopify_refund import handle_shopify_refund, post_from_review

    _two_line_bill(env)
    assert handle_shopify_refund(env, _line_refund(9002, "700002"), topic="refunds/create")["status"] == "queued"
    row = env.get_collection("shopify_refund_review").find_one({"shopify_refund_id": "700002"})
    assert row["credit_note"]["store_credit"] == 500.0
    assert post_from_review(env, row)["status"] == "credited"
    assert _balance(env) == (500.0, 500.0)
