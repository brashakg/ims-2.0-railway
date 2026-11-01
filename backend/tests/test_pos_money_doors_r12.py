"""
IMS 2.0 - POS money doors, round-12 money review of PR #1264 (head c6e6a352)
============================================================================
A lost answer on a money write, then a re-press: every money write has a key
a re-press reuses, the server refuses a reused key that carries other
content, and no outcome has two owners. Plus the review's other findings.
No emoji (cp1252).
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import sys
from contextlib import redirect_stdout

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
from tests.test_pos_money_doors_r9 import _ago, _balance, _customer, _issue, numbered  # noqa: E402,F401
from tests.test_pos_money_doors_r10 import _down, _run_main  # noqa: E402
from tests import test_refund_review_unpaid_credit as rr  # noqa: E402
from tests.test_refund_review_unpaid_credit import env  # noqa: E402,F401


def _repo(till):
    return CustomerRepository(till["db"].get_collection("customers"))


def _row(eid="E-1", **over):
    doc = {"_id": eid, "entry_id": eid, "customer_id": "CUST-1", "type": "ISSUING", "final_type": "ISSUED",
           "applied": False, "delta": 200.0, "amount": 200.0, "pending_at": _ago(30)}
    doc.update(over)
    return doc


def _spec(rid="RET-U", **over):
    spec = {"order_id": "ORD-1", "return_id": rid, "net_amount": 1800.0, "return_type": "RETURN",
            "lines": [{"item_id": "li1", "product_id": "P1", "qty": 1.0, "purchased": 2.0}],
            "refund_tenders": [{"method": "CASH", "amount": 600.0}, {"method": "STORE_CREDIT", "amount": 1200.0}]}
    spec.update(over)
    return spec


@pytest.fixture()
def tasks(monkeypatch):
    out = []
    monkeypatch.setattr("api.services.task_triggers.create_system_task", lambda repo, **k: out.append(k))
    monkeypatch.setattr("api.dependencies.get_task_repository", lambda: object(), raising=False)
    return out


# =========================================================================== #
# BLOCK 1: one owner per outcome -- a refusal whose VOID did not stand
# =========================================================================== #
def test_a_refused_move_whose_void_did_not_stand_is_unclear(till, monkeypatch):
    _customer(till)
    monkeypatch.setattr(cust_router, "apply_store_credit_delta", lambda *a, **k: False)
    monkeypatch.setattr(cust_router, "_guard_landed", lambda *a, **k: None)  # the void cannot stand
    entry = {"entry_id": "E-1", "customer_id": "CUST-1", "type": "ISSUED", "delta": 200.0, "amount": 200.0}
    assert cust_router.post_ledger_credit(_repo(till), entry, ledger=_ledger(till)) == cust_router.UNCLEAR
    assert _ledger(till).find_one({"entry_id": "E-1"})["type"] == "ISSUING"  # left for the reconciler


def test_a_lost_insert_with_no_read_back_is_unclear_not_refused(till, monkeypatch):
    _customer(till)
    ledger = _ledger(till)
    real_insert, real_find = ledger.insert_one, ledger.find_one
    state = {"n": 0}

    def _insert(doc, *a, **k):
        real_insert(doc, *a, **k)  # it landed ...
        raise RuntimeError("reply lost")  # ... but the answer did not

    def _find(flt, *a, **k):
        state["n"] += 1
        if state["n"] > 1:
            raise RuntimeError("read failed")
        return real_find(flt, *a, **k)

    monkeypatch.setattr(ledger, "insert_one", _insert)
    monkeypatch.setattr(ledger, "find_one", _find)
    entry = {"entry_id": "E-2", "customer_id": "CUST-1", "type": "ISSUED", "delta": 200.0, "amount": 200.0}
    assert cust_router.post_ledger_credit(_repo(till), entry, ledger=ledger) == cust_router.UNCLEAR
    assert not entry.get(cust_router.NOT_WRITTEN)


@pytest.mark.usefixtures("gst_numbers")
def test_the_counter_keeps_its_claims_when_the_void_did_not_stand(till, monkeypatch):
    payload = _two_unit_bill(till, monkeypatch)
    payload["return_type"] = "CREDIT_NOTE"
    released = []
    monkeypatch.setattr(returns_router, "_release_returnable_qty", lambda *a, **k: released.append(a) or True)
    _customer(till)
    monkeypatch.setattr(cust_router, "apply_store_credit_delta", lambda *a, **k: False)
    monkeypatch.setattr(cust_router, "_guard_landed", lambda *a, **k: None)
    r = _advance_client().post("/api/v1/returns", json=payload, headers=_hdr("STORE_MANAGER"))
    assert r.status_code == 503 and "could not be confirmed" in r.text, r.text
    assert released == []  # the reconciler is the claims' one owner
    holds = _stored(till).get("refund_holds") or {}
    assert holds and all(h.get("kept") for h in holds.values())


# =========================================================================== #
# BLOCK 2: the hold is kept BEFORE the balance can move
# =========================================================================== #
@pytest.mark.usefixtures("gst_numbers")
def test_a_worker_killed_after_the_credit_lands_leaves_the_hold_kept(till, monkeypatch):
    """A kill right after the move runs no handler: what matters is the
    hold's state at the instant the balance moves -- read there."""
    payload = _two_unit_bill(till, monkeypatch)
    payload["return_type"] = "CREDIT_NOTE"
    _customer(till)
    seen = {}
    real = cust_router.apply_store_credit_delta

    def _land_and_look(*a, **k):
        out = real(*a, **k)
        seen["holds"] = dict(_stored(till).get("refund_holds") or {})
        return out

    monkeypatch.setattr(cust_router, "apply_store_credit_delta", _land_and_look)
    r = _advance_client().post("/api/v1/returns", json=payload, headers=_hdr("STORE_MANAGER"))
    assert r.status_code == 201, r.text
    assert seen["holds"] and all(h.get("kept") for h in seen["holds"].values()), seen


def test_the_reconciler_re_keeps_an_aged_out_hold_when_a_return_credit_lands(till, tasks):
    till["db"].get_collection("orders").insert_one(_order(refund_holds={}, refund_holds_v=1))
    _customer(till, store_credit=1200.0, store_credit_refs=["RET-U:credit"])
    _ledger(till).insert_one(_row("RET-U:credit", delta=1200.0, undo_on_void=_spec()))
    cust_router.reconcile_pending_credits(_repo(till), ledger=_ledger(till))
    hold = (_stored(till).get("refund_holds") or {}).get("RET-U")
    assert hold and hold["kept"] is True and hold["amount"] == 1800.0


# =========================================================================== #
# BLOCK 3: the manual credit key is bound to what the form sent
# =========================================================================== #
def _manual(till, monkeypatch):
    monkeypatch.setattr(cust_router, "get_customer_repository", lambda: _repo(till))
    monkeypatch.setattr(cust_router, "_scoped_customer_or_404",
                        lambda *a, **k: till["db"].get_collection("customers").find_one({"customer_id": "CUST-1"}))


def test_a_reused_manual_key_with_another_amount_is_refused(till, monkeypatch):
    _customer(till)
    _manual(till, monkeypatch)
    state = _down(monkeypatch, till["db"].get_collection("customers"))
    with pytest.raises(HTTPException) as e1:
        cust_router._post_credit_entry("CUST-1", "ISSUED", cust_router.StoreCreditEntryRequest(amount=250.0),
                                       _user("ADMIN"), "k1")
    assert e1.value.status_code == 503
    state["broken"] = False
    with pytest.raises(HTTPException) as e2:  # corrected amount, same open form
        cust_router._post_credit_entry("CUST-1", "ISSUED", cust_router.StoreCreditEntryRequest(amount=300.0),
                                       _user("ADMIN"), "k1")
    assert e2.value.status_code == 409 and "already sent Rs 250.00" in e2.value.detail
    out = cust_router._post_credit_entry("CUST-1", "ISSUED", cust_router.StoreCreditEntryRequest(amount=250.0),
                                         _user("ADMIN"), "k1")
    # The answer is the STORED line and the real balance.
    assert out["entry"]["entry_id"] == "MANUAL:CUST-1:k1" and out["entry"]["applied"] is True
    assert out["balance"] == 250.0 == _balance(till)


# =========================================================================== #
# BLOCK 4: a VOIDed superadmin attempt never blocks the correction
# =========================================================================== #
async def _sacn(till, monkeypatch, number):
    from api.routers.orders import admin_edit
    from api.routers.orders.models import SuperadminEditLine, SuperadminInvoiceChange

    monkeypatch.setattr(admin_edit, "get_order_repository", lambda: till["orders"])
    monkeypatch.setattr(admin_edit, "next_document_number", lambda *a, **k: number)
    monkeypatch.setattr(admin_edit, "_write_order_edit_audit", lambda **k: True)
    body = SuperadminInvoiceChange(mode="CREDIT_NOTE", reason="price fix", items=[SuperadminEditLine(
        item_id="li1", item_type="FRAME", product_name="Frame", quantity=1, unit_price=4500.0)])
    return await admin_edit.superadmin_invoice_change("ORD-1", body, _user("SUPERADMIN"))


def _sacn_bill(till):
    line = {"item_id": "li1", "item_type": "FRAME", "product_name": "Frame", "quantity": 1,
            "unit_price": 5000.0, "item_total": 5000.0, "gst_rate": 18.0}
    till["db"].get_collection("orders").insert_one(_order(invoice_number="BOK01/2627/00009", items=[line],
                                                          grand_total=5000.0))


@pytest.mark.asyncio
async def test_a_voided_superadmin_attempt_is_issued_again_under_a_new_attempt(till, monkeypatch):
    written_off = []
    monkeypatch.setattr(returns_router, "_record_unused_credit_note", lambda n, *a, **k: written_off.append(n) or True)
    _customer(till)
    _sacn_bill(till)
    real = cust_router.apply_store_credit_delta
    monkeypatch.setattr(cust_router, "apply_store_credit_delta", lambda *a, **k: False)
    with pytest.raises(HTTPException) as e1:
        await _sacn(till, monkeypatch, "TST01/C2627/0042")
    assert e1.value.status_code == 503 and written_off == ["TST01/C2627/0042"]
    monkeypatch.setattr(cust_router, "apply_store_credit_delta", real)
    out = await _sacn(till, monkeypatch, "TST01/C2627/0043")
    assert out["note_number"] == "TST01/C2627/0043"
    rows = {r["entry_id"]: r for r in _ledger(till).find({"customer_id": "CUST-1"}) if r.get("entry_id")}
    issued = [r for r in rows.values() if r["type"] == scl.CREDIT_NOTE_TYPE]
    assert len(issued) == 1 and issued[0]["entry_id"].endswith(":r1") and _balance(till) == 500.0
    # Real tax on the note: its taxable and tax add up to the delta.
    assert issued[0]["tax"] > 0 and round(issued[0]["taxable"] + issued[0]["tax"], 2) == 500.0


# =========================================================================== #
# SHOULD-FIX 7: two presses on one correction -- the stored row is the answer
# =========================================================================== #
@pytest.mark.asyncio
async def test_a_press_that_settles_another_presss_line_reports_that_line(till, monkeypatch):
    from api.routers.orders import admin_edit

    written_off = []
    monkeypatch.setattr(returns_router, "_record_unused_credit_note", lambda n, *a, **k: written_off.append(n) or True)
    _customer(till)
    _sacn_bill(till)
    sacn = "ORD-1:sacn:50000:450000"

    def _number(*a, **k):
        # Press A wrote its line (number 0042) after this press's checks.
        _ledger(till).insert_one(_row(sacn, delta=500.0, amount=500.0, note_number="TST01/C2627/0042",
                                      order_id="ORD-1", net_refund=500.0, pending_at=_ago(0)))
        return "TST01/C2627/0043"

    monkeypatch.setattr(admin_edit, "get_order_repository", lambda: till["orders"])
    monkeypatch.setattr(admin_edit, "_write_order_edit_audit", lambda **k: True)
    from api.routers.orders.models import SuperadminEditLine, SuperadminInvoiceChange

    monkeypatch.setattr(admin_edit, "next_document_number", _number)
    body = SuperadminInvoiceChange(mode="CREDIT_NOTE", reason="price fix", items=[SuperadminEditLine(
        item_id="li1", item_type="FRAME", product_name="Frame", quantity=1, unit_price=4500.0)])
    out = await admin_edit.superadmin_invoice_change("ORD-1", body, _user("SUPERADMIN"))
    assert out["note_number"] == "TST01/C2627/0042"
    assert written_off == ["TST01/C2627/0043"]
    assert _ledger(till).count_documents({"note_number": "TST01/C2627/0043"}) == 0  # no second note doc
    assert _balance(till) == 500.0


# =========================================================================== #
# SHOULD-FIX 6 / 12: Returns > Complete record; the doc-missing task
# =========================================================================== #
def _complete(rid, paid):
    return asyncio.run(returns_router.complete_return_record(
        rid, returns_router.CompleteRecordBody(cash_leg_paid=paid), _user("ACCOUNTANT")))


def test_complete_record_writes_the_missing_doc_once_and_frees_the_bill(till, tasks):
    till["db"].get_collection("orders").insert_one(_order(
        status="DELIVERED", refund_holds={"RET-U": {"amount": 1800.0, "at": _ago(60), "kept": True}},
        refund_holds_v=1))
    _ledger(till).insert_one(_row("RET-U:credit", type=scl.ISSUED, applied=True, delta=1200.0,
                                  note_number="TST01/C2627/0007", taxable=1016.95, tax=183.05, gst_rate=18.0,
                                  order_id="ORD-1", undo_on_void=_spec()))
    with pytest.raises(HTTPException) as before:
        returns_router._refuse_while_a_return_settles(_stored(till), "ORD-1")
    assert before.value.status_code == 409 and "Needs completing (RET-U)" in before.value.detail
    assert "gave store credit" in before.value.detail
    doc = _complete("RET-U", paid=False)
    assert doc["status"] == "COMPLETED" and doc["credit_note_number"] == "TST01/C2627/0007"
    # Round 13/14: the unpaid cash part stays on the doc as OWED (the note covers
    # the whole return) -- kept OFF the drawer legs, so no Day-End reader nets it.
    assert [t["method"] for t in doc["refund_tenders"]] == ["STORE_CREDIT"]
    assert [(t["method"], t["status"]) for t in doc["owed_tenders"]] == [("CASH", "OWED")]
    assert doc["net_refund"] == 1800.0 and doc["owed_amount"] == 600.0
    again = _complete("RET-U", paid=True)
    assert again.get("replayed") is True and till["db"].get_collection("returns").count_documents({}) == 1
    returns_router._refuse_while_a_return_settles(_stored(till), "ORD-1")  # no longer blocked


@pytest.mark.usefixtures("gst_numbers")
def test_a_doc_that_could_not_be_saved_raises_a_task_and_is_completed_from_the_hold(till, monkeypatch, tasks):
    from tests.test_pos_money_doors_r9 import _crash_once

    payload = _two_unit_bill(till, monkeypatch)
    returns = till["db"].get_collection("returns")
    _crash_once(monkeypatch, returns, "insert_one", lambda doc, *a, **k: doc.get("status") == "COMPLETED")
    r = _advance_client().post("/api/v1/returns", json=payload, headers=_hdr("STORE_MANAGER"))
    assert r.status_code == 201
    rid = r.json()["return_id"]
    assert [t["dedupe_ref"] for t in tasks] == [f"return_doc_missing:{rid}"]
    assert tasks[0]["priority"] == "P1" and tasks[0]["extra"]["payload"]["doc"]["return_id"] == rid
    # Round 14: a hold younger than the reconciler's timeout may be a press
    # still running -- refused; this one is past it.
    with pytest.raises(HTTPException) as young:
        _complete(rid, paid=True)
    assert young.value.status_code == 409
    holds = _stored(till)["refund_holds"]
    holds[rid]["at"] = _ago(30)
    till["db"].get_collection("orders").update_one({"order_id": "ORD-1"}, {"$set": {"refund_holds": holds}})
    doc = _complete(rid, paid=True)
    assert doc["status"] == "COMPLETED" and doc["net_refund"] == 1800.0
    assert returns.count_documents({"return_id": rid}) == 1


def test_the_reconcilers_heal_of_a_return_credit_raises_the_task(till, monkeypatch, tasks):
    till["db"].get_collection("orders").insert_one(_order(refund_holds={}, refund_holds_v=1))
    _customer(till, store_credit=1200.0)
    _ledger(till).insert_one(_row("RET-H:credit", delta=1200.0, undo_on_void=_spec("RET-H")))
    landed = iter([False, True])  # not landed at the read, landed at the void's re-read
    monkeypatch.setattr(cust_router, "_guard_landed", lambda *a, **k: next(landed))
    cust_router.reconcile_pending_credits(_repo(till), ledger=_ledger(till))
    assert _ledger(till).find_one({"entry_id": "RET-H:credit"})["type"] == "ISSUED"
    assert [t["dedupe_ref"] for t in tasks] == ["return_credit_settled:RET-H:credit"]


# =========================================================================== #
# SHOULD-FIX 8: a past-s34 ADJUSTED credit is counted once in "given back"
# =========================================================================== #
def test_a_second_past_window_refund_is_not_under_credited(env, monkeypatch):
    from api.services import shopify_refund as sr

    monkeypatch.setattr(sr, "_raise_s34_task", lambda *a, **k: None)
    rr._order(env, invoice_date="2023-06-10T10:00:00", created_at="2023-06-10T10:00:00", grand_total=2000.0,
              amount_paid=2000.0, items=[{
                  "item_id": "it-1", "product_id": "7001", "ims_product_id": "IMS-P-1",
                  "shopify_line_item_id": 9001, "sku": "RB-1", "product_name": "RB",
                  "quantity": 2, "gst_rate": 5.0, "taxable_value": 1904.76, "tax_amount": 95.24}])
    rr._queue_and_confirm(env, rr._payload(txns=[]))
    second = rr._payload(refund_id="700002", txns=[])
    second["refund_line_items"][0]["id"] = 2
    rr._queue_and_confirm(env, second)
    rows = list(env.get_collection("credit_note_ledger").find({}))
    assert [r["type"] for r in rows] == [scl.ADJUSTED, scl.ADJUSTED]
    assert [r["delta"] for r in rows] == [1000.0, 1000.0], [(r.get("delta"), r.get("settlement")) for r in rows]


# =========================================================================== #
# SHOULD-FIX 9: a ledger read error on the counter path truly refuses
# =========================================================================== #
@pytest.mark.usefixtures("gst_numbers")
def test_a_ledger_read_error_refuses_cleanly_and_writes_the_number_off(till, monkeypatch):
    payload = _two_unit_bill(till, monkeypatch)
    payload["return_type"] = "CREDIT_NOTE"
    released, written_off = [], []
    monkeypatch.setattr(returns_router, "_release_returnable_qty", lambda *a, **k: released.append(a) or True)
    monkeypatch.setattr(returns_router, "_record_unused_credit_note", lambda n, *a, **k: written_off.append(n) or True)
    _customer(till)
    ledger = _ledger(till)
    real_find = ledger.find_one

    def _find(flt, *a, **k):
        if "ref" in (flt or {}):
            raise RuntimeError("ledger down")
        return real_find(flt, *a, **k)

    monkeypatch.setattr(ledger, "find_one", _find)
    r = _advance_client().post("/api/v1/returns", json=payload, headers=_hdr("STORE_MANAGER"))
    assert r.status_code == 503 and "NOTHING was recorded" in r.text, r.text
    assert len(released) == 1 and written_off == ["TST01/C2627/0001"]
    assert not (_stored(till).get("refund_holds") or {})  # the hold went back too


# =========================================================================== #
# SHOULD-FIX 10: a restocking fee lowers the tax filed for every tender
# =========================================================================== #
@pytest.mark.usefixtures("gst_numbers")
def test_a_cash_refund_with_a_fee_files_tax_on_the_net(monkeypatch):
    from tests.test_returns_gst_refund import _qa_payload
    from tests.test_returns_refund_cap import _ctx

    client, returns = _ctx(monkeypatch, amount_paid=2000.0)
    r = client.post("/api/v1/returns", json=_qa_payload(restocking_fee=104.2,
                                                        refund_tenders=[{"method": "UPI", "amount": 1300.0}]),
                    headers=_hdr("ADMIN"))
    assert r.status_code == 201, r.text
    view = returns.docs[0]["gst_breakup"]
    net = returns.docs[0]["net_refund"]
    assert net == 1300.0
    assert round(view["taxable"] + view["tax"] + returns.docs[0]["round_off"] * net / 1404.2, 2) == pytest.approx(net, abs=0.02)
    assert view["tax"] == pytest.approx(214.2 * net / 1404.2, abs=0.02)


# =========================================================================== #
# SHOULD-FIX 11: a superadmin note lowers what the bill can still refund
# =========================================================================== #
@pytest.mark.usefixtures("gst_numbers")
def test_a_bill_and_its_superadmin_note_are_never_both_refunded(till, monkeypatch):
    payload = _two_unit_bill(till, monkeypatch)
    payload["items"][0]["return_qty"] = 2
    _customer(till)
    _ledger(till).insert_one(_row("ORD-1:sacn:50000:310000", type=scl.ISSUED, applied=True, delta=500.0,
                                  order_id="ORD-1", ref="TST01/C2627/0099"))
    r = _advance_client().post("/api/v1/returns", json=payload, headers=_hdr("ADMIN"))
    assert r.status_code == 400 and "exceed" in r.text.lower(), r.text
    assert returns_router.refundable_ceiling(_stored(till)) == 3100.0


def test_an_unreadable_credit_note_read_fails_the_ceiling_closed(till, monkeypatch):
    ledger = _ledger(till)
    monkeypatch.setattr(ledger, "find", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    with pytest.raises(returns_router.CeilingUnreadable):
        returns_router.refundable_ceiling({"order_id": "ORD-1", "amount_paid": 1000.0})


# =========================================================================== #
# SHOULD-FIX 13: the manual REDEEM door debits once per form
# =========================================================================== #
def test_a_manual_redeem_pressed_again_after_a_lost_answer_debits_once(till, monkeypatch):
    _customer(till, store_credit=1000.0)
    _manual(till, monkeypatch)
    ledger = _ledger(till)
    real_insert = ledger.insert_one
    state = {"fail": True}

    def _insert(doc, *a, **k):
        if state["fail"]:
            state["fail"] = False
            raise RuntimeError("ledger down")
        return real_insert(doc, *a, **k)

    monkeypatch.setattr(ledger, "insert_one", _insert)
    body = cust_router.StoreCreditEntryRequest(amount=300.0)
    with pytest.raises(HTTPException) as e1:
        cust_router._post_credit_entry("CUST-1", "REDEEMED", body, _user("ADMIN"), "r1")
    assert e1.value.status_code == 503 and "could not be confirmed" in e1.value.detail
    out = cust_router._post_credit_entry("CUST-1", "REDEEMED", body, _user("ADMIN"), "r1")
    assert out["entry"]["entry_id"] == "MANUAL-REDEEM:CUST-1:r1" and out["balance"] == 700.0 == _balance(till)
    with pytest.raises(HTTPException) as e2:
        cust_router._post_credit_entry("CUST-1", "REDEEMED", cust_router.StoreCreditEntryRequest(amount=50.0),
                                       _user("ADMIN"), "r1")
    assert e2.value.status_code == 409 and _balance(till) == 700.0
    assert ledger.count_documents({"entry_id": "MANUAL-REDEEM:CUST-1:r1"}) == 1


# =========================================================================== #
# BLOCK 5 (server side): a reused return key with other content is refused
# =========================================================================== #
@pytest.mark.usefixtures("gst_numbers")
def test_a_reused_return_key_with_other_lines_is_refused(till, monkeypatch):
    payload = _two_unit_bill(till, monkeypatch)
    client = _advance_client()
    h = {**_hdr("STORE_MANAGER"), "Idempotency-Key": "ret-k1"}
    assert client.post("/api/v1/returns", json=payload, headers=h).status_code == 201
    replay = client.post("/api/v1/returns", json=payload, headers=h)
    assert replay.status_code == 201 and replay.json()["_idempotent_replay"] is True
    other = json.loads(json.dumps(payload))
    other["restocking_fee"] = 100.0
    r = client.post("/api/v1/returns", json=other, headers=h)
    assert r.status_code == 409 and "different items or amounts" in r.text
    assert till["db"].get_collection("returns").count_documents({}) == 1


# =========================================================================== #
# NITs
# =========================================================================== #
def test_a_legacy_row_with_no_applied_key_is_never_paid_again(till):
    _customer(till, store_credit=200.0)
    _ledger(till).insert_one({"_id": "OLD-1", "entry_id": "OLD-1", "customer_id": "CUST-1", "type": "ISSUED",
                              "delta": 200.0, "amount": 200.0})
    entry = {"entry_id": "OLD-1", "customer_id": "CUST-1", "type": "ISSUED", "delta": 200.0, "amount": 200.0}
    assert cust_router.post_ledger_credit(_repo(till), entry, ledger=_ledger(till)) == cust_router.POSTED
    assert _balance(till) == 200.0


def test_a_revive_that_raises_is_a_clean_refusal(till, monkeypatch):
    _customer(till)
    ledger = _ledger(till)
    ledger.insert_one(_row("E-9", type="VOID", delta=0.0, void_delta=200.0))
    real = ledger.update_one

    def _upd(flt, upd, *a, **k):
        if (flt or {}).get("type") == "VOID":
            raise RuntimeError("down")
        return real(flt, upd, *a, **k)

    monkeypatch.setattr(ledger, "update_one", _upd)
    entry = {"entry_id": "E-9", "customer_id": "CUST-1", "type": "ISSUED", "delta": 200.0, "amount": 200.0}
    assert cust_router.post_ledger_credit(_repo(till), entry, ledger=ledger) == cust_router.REFUSED
    assert _balance(till) == 0.0


def test_a_finish_with_no_final_type_is_money_given_not_a_credit_note(till):
    _customer(till, store_credit=200.0, store_credit_refs=["E-5"])
    _ledger(till).insert_one(_row("E-5", final_type=None))
    cust_router.reconcile_pending_credits(_repo(till), ledger=_ledger(till))
    assert _ledger(till).find_one({"entry_id": "E-5"})["type"] == scl.ADJUSTED


def test_a_finish_across_a_financial_year_is_flagged(monkeypatch):
    audited = []
    monkeypatch.setattr(cust_router, "_audit_credit_event", lambda action, row: audited.append(action))
    monkeypatch.setattr(cust_router, "utc_now_iso", lambda: "2026-04-02T06:00:00+00:00")
    out = cust_router._finish_dates({"entry_id": "E-6", "created_at": "2026-03-30T06:00:00+00:00"})
    assert out.get("redated_cross_fy") is True and audited == ["CREDIT_NOTE_REDATED_CROSS_FY"]


def test_a_redrive_whose_conversion_did_not_match_follows_the_rows_real_state(till, monkeypatch, numbered):
    written_off = []
    monkeypatch.setattr(returns_router, "_record_unused_credit_note", lambda n, *a, **k: written_off.append(n) or True)
    _customer(till, store_credit=300.0, store_credit_refs=["RET-S:credit"])
    ledger = _ledger(till)
    ledger.insert_one(_row("RET-S:credit", ref="RET-S", type=scl.ISSUED, applied=True, delta=300.0,
                           note_number="TST01/C2627/0055"))
    real_find = ledger.find_one
    stale = dict(real_find({"entry_id": "RET-S:credit"}), type="ISSUING", applied=False)

    def _find(flt, *a, **k):
        if "ref" in (flt or {}):
            return dict(stale)  # read just before another press finished it
        return real_find(flt, *a, **k)

    monkeypatch.setattr(ledger, "find_one", _find)
    out = returns_router._issue_store_credit("CUST-1", 300.0, reason="r", ref="RET-S", current_user=_user("ADMIN"),
                                             store_id=STORE, order_id="ORD-1", credit_note=False)
    assert out is not None and out["type"] == scl.ISSUED and written_off == []
    assert real_find({"entry_id": "RET-S:credit"})["note_number"] == "TST01/C2627/0055"


def test_an_unreadable_shopify_date_names_its_reason_and_raises_a_task(env, monkeypatch, numbered, tasks):
    rr._order(env, invoice_date="not a date", created_at="also not")
    _, res = rr._queue_and_confirm(env, rr._payload(txns=[]))
    assert res["status"] == "credit_failed" and res["reason"] == returns_router.S34_UNREADABLE
    assert [t["dedupe_ref"] for t in tasks if t["dedupe_ref"].startswith("order_invoice")] == ["order_invoice_date_unreadable:ord-1"]


def test_an_unreadable_shopify_total_on_a_mismatch_fails_closed():
    assert returns_router.refundable_ceiling({"amount_paid": 2000.0, "shopify_totals_check": {
        "status": "MISMATCH", "shopify_total": "n/a"}}) == 0.0


def test_the_shopify_allowance_is_capped_at_shopifys_total_on_a_mismatch(env):
    from api.services import shopify_refund as sr

    rr._order(env, amount_paid=1000.0, shopify_totals_check={"status": "MISMATCH", "shopify_total": 600.0})
    credit, _why, label, _ask = sr._store_credit_allowance(env, "ord-1", "700001", 1000.0, 0.0)
    assert credit == 600.0 and label == "PARTIAL"


def test_untied_hand_credit_excludes_door_rows_and_other_orders(env):
    from api.services import shopify_refund as sr

    order = rr._order(env)
    led = env.get_collection("credit_note_ledger")
    led.insert_one({"entry_id": "A", "customer_id": "CUST-1", "type": "ADJUSTED", "delta": 100.0,
                    "source": "REFERRAL_REWARD"})
    led.insert_one({"entry_id": "B", "customer_id": "CUST-1", "type": "ADJUSTED", "delta": 200.0,
                    "order_id": "other-order"})
    led.insert_one({"entry_id": "C", "customer_id": "CUST-1", "type": "ADJUSTED", "delta": 50.0})
    back = sr._given_back_elsewhere(env, order, "700001")
    assert back["untied_hand"] == 50.0


def test_both_system_jobs_are_registered(monkeypatch):
    from agents import scheduler as S

    monkeypatch.setattr(S, "APSCHEDULER_AVAILABLE", False)
    sch = S.AgentScheduler.__new__(S.AgentScheduler)
    sch._config_manager = type("C", (), {"get_all_configs": lambda self: []})()
    sch._agents, sch._fallback_tasks, sch._scheduler = {}, {}, None
    ids = []
    monkeypatch.setattr(sch, "_add_fallback_loop", lambda job_id, fn, seconds: ids.append(job_id), raising=False)
    sch._schedule_jobs()
    assert "store_credit_reconcile" in ids and "stock_claim_sweep" in ids


def test_backfills_stop_on_non_finite_numbers_and_print_the_database(monkeypatch):
    from scripts import backfill_store_credit_field as bf

    db = StrictDB()
    db["customers"].insert_one({"customer_id": "CUST-1"})
    db["customers"].insert_one({"customer_id": "CUST-2", "store_credit": float("nan")})
    db["credit_note_ledger"].insert_one({"customer_id": "CUST-1", "entry_id": "x", "delta": float("nan")})
    rc, text = _run_main(monkeypatch, bf, db, [])
    assert rc == 2 and "[BAD-DELTA]" in text and "[NON-NUMERIC]" in text and "[DB] " in text


def test_the_loyalty_backfill_stops_on_no_orders_and_never_stamps_a_row_without_an_id(monkeypatch):
    from scripts import backfill_loyalty_tender_claims as bf

    rc, text = _run_main(monkeypatch, bf, StrictDB(), [])
    assert rc == 3 and "[STOPPED]" in text and "[DB] " in text
    db = StrictDB()
    db["orders"].insert_one(_order("ORD-7", balance_due=0.0, payments=[]))
    db["orders"].insert_one(_order("ORD-8", balance_due="nan", payments=[]))
    db["loyalty_transactions"].insert_one({"type": "REDEEM", "order_id": "ORD-7"})  # no txn_id
    db["loyalty_transactions"].insert_one({"txn_id": "T-8", "type": "REDEEM", "order_id": "ORD-8"})
    out = bf.plan(db)
    assert out["no_txn_id"] == ["ORD-7"] and out["legacy"] == []
    assert ("T-8", "ORD-8", None) in out["unmatched_rows"]  # non-finite: listed, never stamped


def test_the_prior_credit_lookup_read_error_is_a_clean_refusal_for_every_caller(till, monkeypatch, numbered):
    _customer(till)
    ledger = _ledger(till)
    monkeypatch.setattr(ledger, "find_one", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    outcome = {}
    out = returns_router._issue_store_credit("CUST-1", 300.0, reason="r", ref="RET-X", current_user=_user("ADMIN"),
                                             store_id=STORE, order_id="ORD-1", outcome=outcome)
    assert out is None and outcome["not_written"] is True and _balance(till) == 0.0


@pytest.mark.asyncio
async def test_a_reused_payment_key_with_another_amount_is_refused(till):
    from api.routers.orders import PaymentCreate, add_payment

    till["db"].get_collection("orders").insert_one(_order())
    first = await add_payment("ORD-1", PaymentCreate(method="CASH", amount=300.0), _user("CASHIER"), "pay-k1")
    again = await add_payment("ORD-1", PaymentCreate(method="CASH", amount=300.0), _user("CASHIER"), "pay-k1")
    assert again["payment_id"] == first["payment_id"] and again.get("_idempotent_replay") is True
    with pytest.raises(HTTPException) as exc:
        await add_payment("ORD-1", PaymentCreate(method="CASH", amount=200.0), _user("CASHIER"), "pay-k1")
    assert exc.value.status_code == 409 and "already sent as CASH Rs 300.00" in exc.value.detail
    assert len(_stored(till)["payments"]) == 1
