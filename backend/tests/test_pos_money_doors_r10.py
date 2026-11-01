"""
IMS 2.0 - POS money doors, round-10 money review of PR #1264 (head 8c45f210)
============================================================================
An UNCLEAR store-credit posting is its own answer everywhere (never "nothing
was recorded"), the posting door's void is compare-and-set, and the other
round-10 findings -- each through the real code over strict fakes. The
reviewers' probes (P1-P6) are flipped into regression tests. No emoji.
"""

from __future__ import annotations

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
    _hdr,
    _ledger,
    _order,
    _stored,
    _user,
    returns_router,
    till,
)
from tests.test_pos_money_doors_r9 import _ago, _balance, _customer, _issue, numbered  # noqa: E402,F401
from tests import test_refund_review_unpaid_credit as rr  # noqa: E402
from tests.test_refund_review_unpaid_credit import env  # noqa: E402,F401


def _repo(till):
    return CustomerRepository(till["db"].get_collection("customers"))


def _down(monkeypatch, custs, *, inc_raises=True, read_raises=True):
    """The database answers the $inc (it lands) but the reply is lost, and the
    guard read-back fails too: a genuinely UNCLEAR outcome."""
    from pymongo.errors import AutoReconnect

    real_upd, real_find = custs.update_one, custs.find_one
    state = {"broken": True}

    def _upd(flt, upd, *a, **k):
        res = real_upd(flt, upd, *a, **k)
        if state["broken"] and inc_raises and "$inc" in upd:
            raise AutoReconnect("reply lost")
        return res

    def _find(flt, *a, **k):
        if state["broken"] and read_raises and cust_router.STORE_CREDIT_REFS in (flt or {}):
            raise AutoReconnect("still down")
        return real_find(flt, *a, **k)

    monkeypatch.setattr(custs, "update_one", _upd)
    monkeypatch.setattr(custs, "find_one", _find)
    return state


# =========================================================================== #
# BLOCK: an UNCLEAR credit is its own answer, never "nothing was recorded"
# =========================================================================== #
def test_p1_an_unclear_credit_is_never_reported_as_refused(till, monkeypatch, numbered):
    custs = _customer(till)
    state = _down(monkeypatch, custs)
    with pytest.raises(returns_router.CreditNotConfirmed):
        _issue(ref="RET-A")
    assert _balance(till) == 200.0
    assert [r["type"] for r in _ledger(till).find({"ref": "RET-A"})] == ["ISSUING"]  # kept for the reconciler
    state["broken"] = False
    cust_router.reconcile_pending_credits(_repo(till), ledger=_ledger(till), older_than_s=0)
    assert [(r["ref"], r["type"]) for r in _ledger(till).find({"customer_id": "CUST-1"})] == [("RET-A", "ISSUED")]
    assert _balance(till) == 200.0


def test_p2_a_raised_write_with_no_guard_yet_is_left_pending_not_voided(till, monkeypatch, numbered):
    from pymongo.errors import AutoReconnect

    written_off = []
    monkeypatch.setattr(returns_router, "_record_unused_credit_note", lambda number, *a, **k: written_off.append(number))
    custs = _customer(till)
    real_upd = custs.update_one
    pending = []

    def _upd(flt, upd, *a, **k):
        if "$inc" in upd and not pending:
            pending.append((flt, upd))  # still executing on the server
            raise AutoReconnect("connection reset")
        return real_upd(flt, upd, *a, **k)

    monkeypatch.setattr(custs, "update_one", _upd)
    with pytest.raises(returns_router.CreditNotConfirmed):
        _issue(ref="RET-A")
    assert _ledger(till).find_one({"ref": "RET-A"})["type"] == "ISSUING"
    real_upd(*pending[0])  # the in-flight write completes
    cust_router.reconcile_pending_credits(_repo(till), ledger=_ledger(till), older_than_s=0)
    row = _ledger(till).find_one({"ref": "RET-A"})
    assert (row["type"], row["applied"]) == ("ISSUED", True) and written_off == []
    assert _balance(till) == 200.0


def test_p3_a_void_racing_a_landed_move_heals_and_writes_nothing_off(till, monkeypatch):
    written_off = []
    monkeypatch.setattr(returns_router, "_record_unused_credit_note", lambda number, *a, **k: written_off.append(number))
    _customer(till, store_credit=200.0, store_credit_refs=["E-9"])  # the press's $inc landed
    _ledger(till).insert_one({"_id": "E-9", "entry_id": "E-9", "customer_id": "CUST-1", "type": "ISSUING",
                              "final_type": "ISSUED", "applied": False, "delta": 200.0,
                              "note_number": "TST01/C2627/0099", "pending_at": _ago(30), "ref": "RET-Z"})
    row = dict(_ledger(till).find_one({"entry_id": "E-9"}))
    assert cust_router._void_credit_row(_repo(till), _ledger(till), row, "stale") is False
    after = _ledger(till).find_one({"entry_id": "E-9"})
    assert (after["type"], after["applied"], after["delta"]) == ("ISSUED", True, 200.0)
    assert written_off == []


def test_p4_a_second_void_of_the_same_row_writes_nothing_off_again(till, monkeypatch):
    written_off = []
    monkeypatch.setattr(returns_router, "_record_unused_credit_note", lambda number, *a, **k: written_off.append(number))
    _customer(till)
    _ledger(till).insert_one({"_id": "E-8", "entry_id": "E-8", "customer_id": "CUST-1", "type": "ISSUING",
                              "final_type": "ISSUED", "applied": False, "delta": 200.0,
                              "note_number": "TST01/C2627/0088", "pending_at": _ago(30)})
    row = dict(_ledger(till).find_one({"entry_id": "E-8"}))
    assert cust_router._void_credit_row(_repo(till), _ledger(till), row, "a") is True
    assert cust_router._void_credit_row(_repo(till), _ledger(till), row, "b") is False
    assert written_off == ["TST01/C2627/0088"]
    voided = _ledger(till).find_one({"entry_id": "E-8"})
    assert (voided["delta"], voided["void_delta"]) == (0.0, 200.0)  # an older build summing deltas counts 0


def test_a_finish_never_revives_a_voided_row(till):
    _customer(till)
    _ledger(till).insert_one({"_id": "E-7", "entry_id": "E-7", "customer_id": "CUST-1", "type": "VOID",
                              "final_type": "ISSUED", "applied": False, "delta": 0.0, "void_delta": 200.0})
    cust_router._finish_credit_row(_ledger(till), dict(_ledger(till).find_one({"entry_id": "E-7"})))
    assert _ledger(till).find_one({"entry_id": "E-7"})["type"] == "VOID"


def test_p5_an_in_flight_credit_counts_as_given_back(till):
    from api.services import shopify_refund as sr

    _customer(till, store_credit=700.0, store_credit_refs=["E-7"])
    _ledger(till).insert_one({"_id": "E-7", "entry_id": "E-7", "customer_id": "CUST-1", "type": "ISSUING",
                              "final_type": "ISSUED", "applied": False, "delta": 700.0, "order_id": "ORD-1",
                              "ref": "RET-CTR", "pending_at": _ago(3)})
    order = {"order_id": "ORD-1", "customer_id": "CUST-1", "order_number": "BV-1"}
    assert sr._given_back_elsewhere(till["db"], order, "SR-1")["given"] == 700.0


@pytest.mark.usefixtures("gst_numbers")
def test_p6_counter_unclear_keeps_the_claim_and_the_hold_and_refuses_a_redo(till, monkeypatch):
    from tests.test_pos_money_doors_r8 import _two_unit_bill

    payload = _two_unit_bill(till, monkeypatch)
    payload["return_type"] = "CREDIT_NOTE"
    claims = {"n": 0}
    monkeypatch.setattr(returns_router, "_claim_returnable_qty",
                        lambda *a, **k: (claims.__setitem__("n", claims["n"] + 1) or claims["n"] <= 1))
    released = []
    monkeypatch.setattr(returns_router, "_release_returnable_qty", lambda *a, **k: released.append(a))
    _customer(till)
    _down(monkeypatch, till["db"].get_collection("customers"))
    from tests.test_pos_money_doors import _advance_client

    client = _advance_client()
    r1 = client.post("/api/v1/returns", json=payload, headers=_hdr("STORE_MANAGER"))
    assert r1.status_code == 503, r1.text
    assert "NOTHING was recorded" not in r1.text and "Do NOT redo" in r1.text
    assert released == []  # the unit claim stays
    holds = _stored(till)["refund_holds"]
    assert len(holds) == 1 and next(iter(holds.values()))["kept"] is True
    row = _ledger(till).find_one({"customer_id": "CUST-1"})
    assert row["type"] == "ISSUING" and row["entry_id"].endswith(":credit") and row["undo_on_void"]["lines"]
    r2 = client.post("/api/v1/returns", json=payload, headers=_hdr("STORE_MANAGER"))
    assert r2.status_code == 409, r2.text  # the same unit cannot be returned again
    assert _ledger(till).count_documents({"customer_id": "CUST-1"}) == 1


def test_a_voided_unconfirmed_return_gives_its_claims_and_hold_back(till, monkeypatch):
    undone = []
    monkeypatch.setattr(returns_router, "_undo_return_claims", lambda spec, **k: undone.append(spec))
    _customer(till)
    spec = {"order_id": "ORD-1", "return_id": "RET-U", "lines": [{"item_id": "li1", "qty": 1.0}]}
    _ledger(till).insert_one({"_id": "RET-U:credit", "entry_id": "RET-U:credit", "customer_id": "CUST-1",
                              "type": "ISSUING", "final_type": "ISSUED", "applied": False, "delta": 200.0,
                              "pending_at": _ago(30), "undo_on_void": spec})
    cust_router.reconcile_pending_credits(_repo(till), ledger=_ledger(till))
    assert undone == [spec]


@pytest.mark.asyncio
async def test_a_superadmin_note_that_cannot_be_confirmed_keeps_the_order_and_the_number(till, monkeypatch):
    from api.routers.orders import admin_edit
    from api.routers.orders.models import SuperadminEditLine, SuperadminInvoiceChange

    written_off = []
    monkeypatch.setattr(returns_router, "_record_unused_credit_note", lambda number, *a, **k: written_off.append(number))
    monkeypatch.setattr(admin_edit, "get_order_repository", lambda: till["orders"])
    monkeypatch.setattr(admin_edit, "next_document_number", lambda *a, **k: "TST01/C2627/0042")
    monkeypatch.setattr(admin_edit, "_write_order_edit_audit", lambda **k: True)
    _customer(till)
    _down(monkeypatch, till["db"].get_collection("customers"))
    line = dict(_LINE, item_type="FRAME", unit_price=5000.0, item_total=5000.0)
    till["db"].get_collection("orders").insert_one(_order(invoice_number="BOK01/2627/00009", items=[line],
                                                          grand_total=5000.0))
    body = SuperadminInvoiceChange(mode="CREDIT_NOTE", reason="price fix", items=[SuperadminEditLine(
        item_id="li1", item_type="FRAME", product_name="Frame", quantity=1, unit_price=4500.0)])
    with pytest.raises(HTTPException) as exc:
        await admin_edit.superadmin_invoice_change("ORD-1", body, _user("SUPERADMIN"))
    assert exc.value.status_code == 503 and "could not be confirmed" in str(exc.value.detail)
    assert _stored(till).get("post_invoice_credit_note_at") is not None  # the order stays claimed
    assert written_off == []


# =========================================================================== #
# SHOULD-FIX: one credit note for a mixed-tender return
# =========================================================================== #
def test_a_mixed_tender_return_files_one_note_for_the_whole_return(till, monkeypatch, numbered):
    _customer(till)
    # A Rs 1,000 return: Rs 600 back in cash, Rs 400 as store credit.
    returns_router._issue_store_credit(
        "CUST-1", 1000.0, reason="return", ref="RET-MIX", current_user=_user("ADMIN"), store_id=STORE,
        order_id="ORD-1", taxable=952.38, tax=47.62, balance_delta=400.0, settlement="PARTIAL",
        entry_id="RET-MIX:credit")
    row = _ledger(till).find_one({"ref": "RET-MIX"})
    assert (row["type"], row["delta"], row["net_refund"], row["tax"]) == ("ISSUED", 400.0, 1000.0, 47.62)
    assert _balance(till) == 400.0


# =========================================================================== #
# SHOULD-FIX: section 34 on the Shopify door and on un-invoiced counter returns
# =========================================================================== #
def test_a_shopify_refund_past_the_window_files_no_credit_note(env, monkeypatch):
    from api.services import shopify_refund as sr

    monkeypatch.setattr(returns_router, "_credit_note_window_closed", lambda order: "Closed (section 34).")
    tasks = []
    monkeypatch.setattr(sr, "_raise_s34_task", lambda *a, **k: tasks.append(a))
    rr._order(env)
    rr._queue_and_confirm(env, rr._payload(txns=[]))
    rows = list(env.get_collection("credit_note_ledger").find({}))
    assert rows and [r["type"] for r in rows] == [scl.ADJUSTED] and not rows[0].get("note_number")
    doc = env.get_collection("returns").find_one({"order_id": "ord-1"})
    assert float((doc.get("gst_breakup") or {}).get("tax") or 0) == 0.0
    assert len(tasks) == 1


def test_an_uninvoiced_counter_return_past_the_window_is_refused(monkeypatch):
    from tests.test_returns_gst_refund import _qa_payload
    from tests.test_returns_refund_cap import _ctx

    minted = []
    monkeypatch.setattr(returns_router, "next_document_number", lambda *a, **k: minted.append(1) or "X")
    client, returns = _ctx(monkeypatch, amount_paid=2000.0, created_at="2024-05-01T10:00:00")
    r = client.post("/api/v1/returns", json=_qa_payload(), headers=_hdr("ADMIN"))
    assert r.status_code == 400 and "section 34" in r.text, r.text
    assert minted == []


# =========================================================================== #
# SHOULD-FIX: a manual / goodwill credit is not a credit note
# =========================================================================== #
def test_a_manual_credit_is_adjusted_never_a_credit_note(till, monkeypatch):
    _customer(till)
    monkeypatch.setattr(cust_router, "get_customer_repository", lambda: _repo(till))
    monkeypatch.setattr(cust_router, "_scoped_customer_or_404",
                        lambda *a, **k: till["db"].get_collection("customers").find_one({"customer_id": "CUST-1"}))
    out = cust_router._post_credit_entry("CUST-1", "ISSUED", cust_router.StoreCreditEntryRequest(amount=250.0),
                                         _user("ADMIN"))
    assert out["entry"]["type"] == scl.ADJUSTED
    assert _ledger(till).count_documents({"type": scl.CREDIT_NOTE_TYPE}) == 0
    assert _balance(till) == 250.0
    with pytest.raises(HTTPException):
        cust_router._post_credit_entry("CUST-1", "ISSUED", cust_router.StoreCreditEntryRequest(amount=-5.0),
                                       _user("ADMIN"))


# =========================================================================== #
# SHOULD-FIX: an old stored "off" never switches the owner's Rs 2,000 rule off
# =========================================================================== #
def test_a_stored_off_switch_never_loosens_the_owner_rule(monkeypatch):
    from api.services import refund_approval_matrix as ram

    monkeypatch.setattr(ram, "matrix_enabled", lambda scope=None: False)
    assert ram.required_tier_for_refund(250000, "DEFECTIVE", "CASHIER") == "auto"
    assert ram.required_tier_for_refund(150000, "DEFECTIVE", "CASHIER") is None


# =========================================================================== #
# NITs
# =========================================================================== #
@pytest.mark.asyncio
async def test_a_referral_whose_press_died_before_the_credit_is_resumed(till, monkeypatch):
    from tests.test_pos_money_doors_r8 import _referral

    marketing = _referral(till, monkeypatch, {"customer_id": "CUST-1", "store_credit": 0.0})
    till["db"].get_collection("referrals").update_one(
        {"referral_id": "REF-1"}, {"$set": {"status": "REWARD_CREDITED", "reward_ledger_entry": "REFERRAL:REF-1"}})
    out = await marketing.redeem_referral("REF-1", current_user=_user("ADMIN"))
    assert out.get("resumed") is True and _balance(till) == 100.0
    out2 = await marketing.redeem_referral("REF-1", current_user=_user("ADMIN"))
    assert out2.get("already_credited") is True and _balance(till) == 100.0


@pytest.mark.asyncio
async def test_a_legacy_credited_referral_is_never_paid_again(till, monkeypatch):
    from tests.test_pos_money_doors_r8 import _referral

    marketing = _referral(till, monkeypatch, {"customer_id": "CUST-1", "store_credit": 500.0})
    till["db"].get_collection("referrals").update_one({"referral_id": "REF-1"}, {"$set": {"status": "REWARD_CREDITED"}})
    out = await marketing.redeem_referral("REF-1", current_user=_user("ADMIN"))
    assert out.get("already_credited") is True and _balance(till) == 500.0


def test_the_reconciler_dates_a_late_finish_into_the_open_month(till):
    _customer(till, store_credit=200.0, store_credit_refs=["E-5"])
    _ledger(till).insert_one({"_id": "E-5", "entry_id": "E-5", "customer_id": "CUST-1", "type": "ISSUING",
                              "final_type": "ISSUED", "applied": False, "delta": 200.0,
                              "created_at": "2026-01-15T10:00:00+00:00", "pending_at": _ago(60)})
    cust_router.reconcile_pending_credits(_repo(till), ledger=_ledger(till))
    row = _ledger(till).find_one({"entry_id": "E-5"})
    assert row["type"] == "ISSUED" and row["redated_from"] == "2026-01-15T10:00:00+00:00"
    assert row["created_at"][:7] == datetime.now(timezone.utc).isoformat()[:7]


@pytest.mark.asyncio
async def test_the_generic_consume_route_refuses_a_stored_refund_approval_under_another_name(monkeypatch):
    from api.routers import approvals as ar
    from tests.test_pos_money_doors_r8 import _approved, _engine

    eng, db = _engine()
    _approved(db)
    monkeypatch.setattr(ar, "_engine", lambda: eng)
    with pytest.raises(HTTPException) as exc:
        await ar.consume_request("REQ-1", ar.ConsumeAction(action_type="refund", amount=1.0),
                                 current_user=_user("ADMIN"))
    assert exc.value.status_code == 400
    req = db.get_collection("approval_requests").find_one({"request_id": "REQ-1"})
    assert (req["status"], req["consumed"]) == ("APPROVED", False)


def test_the_backfill_stops_with_exit_2_and_names_a_bad_delta(monkeypatch, tmp_path):
    from scripts import backfill_store_credit_field as bf

    db = StrictDB()
    db["customers"].insert_one({"customer_id": "CUST-1"})
    db["customers"].insert_one({"customer_id": "CUST-2"})
    db["credit_note_ledger"].insert_one({"customer_id": "CUST-1", "entry_id": "x", "delta": "abc"})
    db["credit_note_ledger"].insert_one({"customer_id": "CUST-2", "entry_id": "y", "delta": -50.0})
    out = bf.plan(db)
    assert out["non_numeric_delta"] == [("CUST-1", "x", "abc")] and out["negative"] == [("CUST-2", -50.0)]
    assert out["sets"] == [] and out["scanned"] == {"customers": 2, "ledger_rows": 2}
    rc, text = _run_main(monkeypatch, bf, db, ["--apply", "--backup", str(tmp_path / "b.json")])
    assert rc == 2 and "[STOPPED]" in text and "[SCANNED] customers: 2" in text
    assert db["customers"].find_one({"customer_id": "CUST-1"}).get("store_credit") is None


def test_the_backfill_reports_a_customer_a_live_door_set_in_between(monkeypatch, tmp_path):
    from scripts import backfill_store_credit_field as bf

    db = StrictDB()
    db["customers"].insert_one({"customer_id": "CUST-1"})
    real_plan = bf.plan

    def _raced(d):
        out = real_plan(d)
        d["customers"].update_one({"customer_id": "CUST-1"}, {"$set": {"store_credit": 0.0}})  # a live door
        return out

    monkeypatch.setattr(bf, "plan", _raced)
    rc, text = _run_main(monkeypatch, bf, db, ["--apply", "--backup", str(tmp_path / "b.json")])
    assert rc == 0 and "[SKIPPED] 1" in text
    rc, text = _run_main(monkeypatch, bf, db, ["--revert", str(tmp_path / "b.json")])
    assert "[SKIPPED] 1" in text


def test_the_loyalty_backfill_stamps_an_unmatched_burn_on_a_settled_bill(tmp_path):
    from scripts import backfill_loyalty_tender_claims as bf

    db = StrictDB()
    db["orders"].insert_one(_order("ORD-9", balance_due=0.0, payments=[]))
    db["loyalty_transactions"].insert_one({"txn_id": "T-9", "type": "REDEEM", "order_id": "ORD-9"})
    out = bf.plan(db)
    assert out["unmatched_rows"] == [("T-9", "ORD-9", 0.0)] and out["legacy"] == ["T-9"]
    assert bf.apply(db, out["links"], str(tmp_path / "l.json"), out["legacy"]) == 1
    row = db["loyalty_transactions"].find_one({"txn_id": "T-9"})
    assert row["tender_payment_id"] == bf.LEGACY_CLAIM and row["tender_backfilled_at"]
    assert bf.revert(db) == 1


def _run_main(monkeypatch, mod, db, argv):
    class _Client:
        def __init__(self, *a, **k):
            self.admin = type("A", (), {"command": lambda self, *a, **k: {"ok": 1}})()

        def __getitem__(self, name):
            return db

    import pymongo

    monkeypatch.setattr(pymongo, "MongoClient", _Client)
    monkeypatch.setenv("MONGO_URL", "mongodb://test")
    monkeypatch.setattr(sys, "argv", ["prog", *argv])
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = mod.main()
    return rc, buf.getvalue()



@pytest.mark.usefixtures("gst_numbers")
def test_a_mixed_tender_counter_return_reverses_the_whole_returns_tax_once(monkeypatch):
    from tests.test_returns_gst_refund import _qa_payload
    from tests.test_returns_refund_cap import _ctx

    client, returns = _ctx(monkeypatch, amount_paid=2000.0, created_at="2026-10-20T10:00:00",
                           payments=[{"payment_id": "p1", "method": "UPI", "amount": 700.0},
                                     {"payment_id": "p2", "method": "GIFT_VOUCHER", "amount": 704.2}])
    ledger = returns_router._ledger_coll()
    payload = _qa_payload(refund_tenders=[{"method": "UPI", "amount": 700.0},
                                          {"method": "STORE_CREDIT", "amount": 704.2}])
    r = client.post("/api/v1/returns", json=payload, headers=_hdr("ADMIN"))
    assert r.status_code == 201, r.text
    assert r.json().get("credit_amount") == 704.2, r.json()
    doc = returns.docs[0]
    rows = [d for d in ledger.docs if d.get("type") == scl.CREDIT_NOTE_TYPE]
    assert len(rows) == 1, [(d.get("type"), d.get("delta"), d.get("applied")) for d in ledger.docs]
    assert rows[0]["delta"] == 704.2  # the balance moves by the store-credit leg only
    assert rows[0]["tax"] == doc["gst_breakup"]["tax"]  # the note reverses the WHOLE return's tax
