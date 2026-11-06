"""Razorpay webhook -> UPI auto-reconcile correctness (audit L2-PAYMENT-F01/F02).

F01: the receiver reconciled only AFTER the shared dispatch, by reading the
newest *unprocessed* razorpay inbox row. With inline dispatch NEXUS had already
marked that row succeeded, so a real captured payment left its bill UNPAID;
with queued dispatch the "newest row" could be a different event.

F02: the reconcile credited payment.authorized (captured=false), any currency,
and compared the amount to grand_total instead of the outstanding balance,
adding it on top of whatever was already paid (Rs 1400 on a Rs 1000 bill).

Every payload here is invented and signed with a synthetic local secret
through the real receiver; no Razorpay call, no real key.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import sys
import time

import mongomock
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mongomock.collection import Collection

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

SECRET = "rzp_local_test_secret"


@pytest.fixture(params=["inline", "queued"])
def rig(request, monkeypatch):
    """The real receiver + the real NEXUS drain over one mongomock DB.

    inline: dispatch_event runs the drain inside the request (local event bus).
    queued: dispatch_event only queues; the test drains later (Redis bus)."""
    from agents.implementations.nexus import NexusAgent
    from api.routers import webhooks as wh

    db = mongomock.MongoClient()["ims"]
    db.integrations.insert_one({"type": "razorpay", "enabled": True, "config": {
        "webhook_secret": SECRET, "key_id": "rzp_test_local", "key_secret": "local"}})
    monkeypatch.setattr(wh, "_get_db", lambda: db)
    monkeypatch.setattr(wh, "_check_webhook_rate_limit", lambda *a, **k: True)
    # The workshop safety net reads the global repositories; not under test.
    from api.routers.orders import workshop
    monkeypatch.setattr(workshop, "_ensure_workshop_job_for_order", lambda *a, **k: None)

    nexus = NexusAgent(db=db)
    queue = []

    async def dispatch(event, payload, source=""):
        if request.param == "inline":
            await nexus.on_event(event, payload)
        else:
            queue.append((event, payload))

    import agents.registry as reg
    monkeypatch.setattr(reg, "dispatch_event", dispatch)

    app = FastAPI()
    app.include_router(wh.router, prefix="/api/v1/webhooks")
    client = TestClient(app)

    def drain(order=None):
        items = list(queue) if order is None else [queue[i] for i in order]
        queue.clear()
        for event, payload in items:
            asyncio.run(nexus.on_event(event, payload))

    rig = {"db": db, "client": client, "nexus": nexus, "queue": queue,
           "drain": drain, "mode": request.param}
    yield rig
    _assert_books_reconcile(db)


def _order(db, order_id, grand_total=1000.0, status="CONFIRMED", payments=()):
    paid = round(sum(p["amount"] for p in payments), 2)
    db.orders.insert_one({
        "order_id": order_id, "order_number": f"BV-{order_id}", "store_id": "BV-BOK-01",
        "customer_id": "CUST-1", "status": status, "grand_total": grand_total,
        "amount_paid": paid, "balance_due": round(grand_total - paid, 2),
        "payment_status": "PARTIAL" if paid else "UNPAID", "payments": list(payments),
    })


def _event(order_id, pay_id, paise, *, event="payment.captured", status="captured",
           captured=True, currency="INR", nonce=0):
    entity = {"id": pay_id, "entity": "payment", "amount": paise, "currency": currency,
              "status": status, "captured": captured, "method": "upi",
              "notes": {"order_ref": f"BV-{order_id}"}}
    if pay_id is None:
        entity.pop("id")
    return {"entity": "event", "event": event, "created_at": int(time.time()) + nonce,
            "payload": {"payment": {"entity": entity}}}


def _post(rig, payload, *, event_id=None, secret=SECRET):
    body = json.dumps(payload).encode()
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    headers = {"X-Razorpay-Signature": sig, "content-type": "application/json"}
    if event_id:
        headers["X-Razorpay-Event-Id"] = event_id
    return rig["client"].post("/api/v1/webhooks/razorpay", content=body, headers=headers)


def _bill(db, order_id):
    return db.orders.find_one({"order_id": order_id})


def _row(db, pay_id):
    return db.webhook_inbox.find_one({"payload.payload.payment.entity.id": pay_id})


def _assert_books_reconcile(db):
    """Independent check, recomputed from the raw rows (not from the code
    under test): every bill's amount_paid is the sum of its tenders, never above
    its total, each Razorpay payment id is on at most one tender anywhere, and
    no captured-payment event for an IMS bill sits 'succeeded' unless its
    payment id is on that bill and the bill left DRAFT. Only a verified dark
    integration may finish without one."""
    seen = {}
    for o in db.orders.find():
        pays = o.get("payments") or []
        total = round(sum(float(p.get("amount") or 0) for p in pays), 2)
        assert round(float(o.get("amount_paid") or 0), 2) == total, o["order_id"]
        assert total <= float(o["grand_total"]) + 0.01, o["order_id"]
        assert round(float(o.get("balance_due") or 0), 2) == round(o["grand_total"] - total, 2)
        for p in pays:
            pid = p.get("provider_payment_id")
            if pid:
                assert pid not in seen, f"{pid} credited twice"
                seen[pid] = o["order_id"]
    for row in db.webhook_inbox.find({"vendor": "razorpay", "status": "succeeded"}):
        payload = row.get("payload") or {}
        ent = ((payload.get("payload") or {}).get("payment") or {}).get("entity") or {}
        ref = (ent.get("notes") or {}).get("order_ref")
        bill = db.orders.find_one({"order_number": ref}) if ref else None
        if (payload.get("event") == "payment.captured" and bill is not None
                and row.get("outcome") != "dark"):
            assert seen.get(ent.get("id")) == bill["order_id"], (
                f"event {ent.get('id')} marked succeeded with no tender on {ref}")
            assert bill["status"] != "DRAFT", f"event {ent.get('id')} succeeded on a DRAFT bill"


# --------------------------------------------------------------------------- #
# F01 -- the accepted event itself is what gets reconciled
# --------------------------------------------------------------------------- #
def test_a_captured_payment_pays_its_bill_in_either_delivery_mode(rig):
    db = rig["db"]
    _order(db, "O1")
    r = _post(rig, _event("O1", "pay_O1", 100000), event_id="evt_1")
    assert r.status_code == 200 and r.json()["status"] == "received"
    rig["drain"]()

    bill = _bill(db, "O1")
    assert bill["payment_status"] == "PAID" and bill["amount_paid"] == 1000.0
    assert [p["provider_payment_id"] for p in bill["payments"]] == ["pay_O1"]
    row = _row(db, "pay_O1")
    assert row["status"] == "succeeded" and row["processed"] is True


def test_two_interleaved_events_each_settle_their_own_bill(rig):
    """Order A's event is accepted; before its reconcile runs another worker
    persists B's event. Each bill must end with its own payment only."""
    db = rig["db"]
    _order(db, "A", grand_total=1000.0)
    _order(db, "B", grand_total=700.0)
    from api.routers import webhooks as wh

    real_dispatch = wh._dispatch_webhook_received
    injected = []

    async def dispatch_then_b_lands(webhook_id, vendor):
        ok = await real_dispatch(webhook_id, vendor)
        if not injected:
            injected.append(1)
            assert _post(rig, _event("B", "pay_B", 70000), event_id="evt_b").status_code == 200
        return ok

    rig_mp = pytest.MonkeyPatch()
    rig_mp.setattr(wh, "_dispatch_webhook_received", dispatch_then_b_lands)
    try:
        assert _post(rig, _event("A", "pay_A", 100000), event_id="evt_a").status_code == 200
    finally:
        rig_mp.undo()
    rig["drain"]()

    a, b = _bill(db, "A"), _bill(db, "B")
    assert [p["provider_payment_id"] for p in a["payments"]] == ["pay_A"]
    assert [p["provider_payment_id"] for p in b["payments"]] == ["pay_B"]
    assert a["amount_paid"] == 1000.0 and b["amount_paid"] == 700.0


def test_a_failed_reconcile_is_recorded_failed_and_the_retry_sweep_credits_it(rig, monkeypatch):
    db = rig["db"]
    _order(db, "O2")
    from database.repositories.order_repository import OrderRepository

    real = OrderRepository.add_payment
    calls = []

    def flaky(self, *a, **k):
        calls.append(1)
        if len(calls) == 1:
            return False  # a write that lost (the repository's own fail-soft answer)
        return real(self, *a, **k)

    monkeypatch.setattr(OrderRepository, "add_payment", flaky)
    _post(rig, _event("O2", "pay_O2", 100000), event_id="evt_2")
    rig["drain"]()
    row = _row(db, "pay_O2")
    assert row["status"] == "failed" and row["processed"] is False
    assert _bill(db, "O2")["payments"] == []

    asyncio.run(rig["nexus"]._retry_failed_webhooks())
    assert _row(db, "pay_O2")["status"] == "succeeded"
    assert _bill(db, "O2")["amount_paid"] == 1000.0


def test_a_lost_acknowledgement_rerun_never_credits_twice(rig):
    """The tender landed but the drain died before writing the row's outcome
    (status processing, lease expired). The sweep re-runs it: still one tender."""
    db = rig["db"]
    _order(db, "O3")
    _post(rig, _event("O3", "pay_O3", 100000), event_id="evt_3")
    rig["drain"]()
    assert len(_bill(db, "O3")["payments"]) == 1
    from datetime import datetime, timedelta, timezone

    db.webhook_inbox.update_one({"_id": _row(db, "pay_O3")["_id"]}, {"$set": {
        "processed": False, "status": "processing",
        "processing_at": datetime.now(timezone.utc) - timedelta(hours=1)}})
    asyncio.run(rig["nexus"]._retry_failed_webhooks())
    bill = _bill(db, "O3")
    assert len(bill["payments"]) == 1 and bill["amount_paid"] == 1000.0
    assert _row(db, "pay_O3")["status"] == "succeeded"


def test_bad_signature_and_verbatim_replay_are_still_refused(rig):
    db = rig["db"]
    _order(db, "O4")
    assert _post(rig, _event("O4", "pay_O4", 100000), secret="wrong").status_code == 401
    payload = _event("O4", "pay_O4", 100000)
    assert _post(rig, payload, event_id="evt_4").json()["status"] == "received"
    assert _post(rig, payload, event_id="evt_4").json()["status"] == "duplicate"
    rig["drain"]()
    assert len(_bill(db, "O4")["payments"]) == 1


# --------------------------------------------------------------------------- #
# F02 -- only a captured INR amount within the balance credits, once
# --------------------------------------------------------------------------- #
def test_an_authorized_payment_credits_nothing_until_it_is_captured(rig):
    db = rig["db"]
    _order(db, "O5", status="DRAFT")
    _post(rig, _event("O5", "pay_O5", 100000, event="payment.authorized",
                      status="authorized", captured=False), event_id="evt_5a")
    rig["drain"]()
    bill = _bill(db, "O5")
    assert bill["payments"] == [] and bill["payment_status"] == "UNPAID"

    _post(rig, _event("O5", "pay_O5", 100000), event_id="evt_5c")
    rig["drain"]()
    bill = _bill(db, "O5")
    assert bill["payment_status"] == "PAID" and bill["amount_paid"] == 1000.0
    # Same lifecycle as a manual first payment: the draft bill is confirmed.
    assert bill["status"] == "CONFIRMED"


def test_a_captured_event_whose_entity_is_not_captured_is_refused(rig):
    db = rig["db"]
    _order(db, "O6")
    _post(rig, _event("O6", "pay_O6", 100000, status="authorized", captured=False),
          event_id="evt_6")
    rig["drain"]()
    assert _bill(db, "O6")["payments"] == []
    assert _row(db, "pay_O6")["status"] in ("failed", "dead")


def test_a_foreign_currency_payment_is_never_credited_as_rupees(rig):
    db = rig["db"]
    _order(db, "O7")
    _post(rig, _event("O7", "pay_O7", 100000, currency="USD"), event_id="evt_7")
    rig["drain"]()
    assert _bill(db, "O7")["payments"] == []
    row = _row(db, "pay_O7")
    assert row["status"] in ("failed", "dead") and "USD" in row["handler_error"]


def test_a_partly_paid_bill_takes_only_its_balance_never_an_overpayment(rig):
    db = rig["db"]
    _order(db, "O8", payments=[{"payment_id": "cash-1", "method": "CASH", "amount": 400.0}])

    _post(rig, _event("O8", "pay_O8_full", 100000), event_id="evt_8a")
    rig["drain"]()
    bill = _bill(db, "O8")
    assert bill["amount_paid"] == 400.0 and len(bill["payments"]) == 1
    assert _row(db, "pay_O8_full")["status"] in ("failed", "dead")

    _post(rig, _event("O8", "pay_O8_rest", 60000), event_id="evt_8b")
    rig["drain"]()
    bill = _bill(db, "O8")
    assert bill["amount_paid"] == 1000.0 and bill["payment_status"] == "PAID"
    assert [p["method"] for p in bill["payments"]] == ["CASH", "UPI"]


def test_two_partial_captures_settle_exactly_their_own_amounts(rig):
    db = rig["db"]
    _order(db, "O9")
    _post(rig, _event("O9", "pay_O9_a", 30000), event_id="evt_9a")
    _post(rig, _event("O9", "pay_O9_b", 70000), event_id="evt_9b")
    rig["drain"]()
    bill = _bill(db, "O9")
    assert sorted(p["amount"] for p in bill["payments"]) == [300.0, 700.0]
    assert bill["payment_status"] == "PAID"


def test_the_same_payment_id_in_two_deliveries_credits_once(rig):
    db = rig["db"]
    _order(db, "O10")
    _post(rig, _event("O10", "pay_O10", 100000), event_id="evt_10a")
    _post(rig, _event("O10", "pay_O10", 100000, nonce=5), event_id="evt_10b")
    rig["drain"]()
    bill = _bill(db, "O10")
    assert len(bill["payments"]) == 1 and bill["amount_paid"] == 1000.0


def test_a_payment_with_no_id_is_refused(rig):
    db = rig["db"]
    _order(db, "O11")
    _post(rig, _event("O11", None, 100000), event_id="evt_11")
    rig["drain"]()
    assert _bill(db, "O11")["payments"] == []
    assert db.webhook_inbox.find_one({"event_id": "evt_11"})["status"] in ("failed", "dead")


def test_a_cancelled_bill_takes_no_payment_and_the_event_stays_actionable(rig):
    db = rig["db"]
    _order(db, "O12", status="CANCELLED")
    _post(rig, _event("O12", "pay_O12", 100000), event_id="evt_12")
    rig["drain"]()
    assert _bill(db, "O12")["payments"] == []
    assert _row(db, "pay_O12")["status"] in ("failed", "dead")


def test_a_refund_event_changes_no_bill(rig):
    db = rig["db"]
    _order(db, "O13")
    _post(rig, _event("O13", "pay_O13", 100000), event_id="evt_13")
    _post(rig, _event("O13", "pay_O13", 100000, event="refund.processed", status="refunded"),
          event_id="evt_13r")
    rig["drain"]()
    bill = _bill(db, "O13")
    assert len(bill["payments"]) == 1 and bill["amount_paid"] == 1000.0


# --------------------------------------------------------------------------- #
# Codex 11-06 (PR #1387): an event finishes only on a verified outcome. An
# outage must leave it failed, and retry or redelivery must finish it once.

def _fail(monkeypatch, method, when):
    """Raise a timeout from mongomock's real collection op when `when` matches."""
    real = getattr(Collection, method)

    def faulty(self, *a, **k):
        if when(self, *a):
            raise TimeoutError(f"transient {method} failure")
        return real(self, *a, **k)

    monkeypatch.setattr(Collection, method, faulty)


def _is_config_read(coll, query=None, *_):
    """The drain's config read; the receiver's secret read has no `enabled`."""
    return coll.name == "integrations" and "enabled" in (query or {})


def _is_confirm_write(coll, _query, update, *_):
    return coll.name == "orders" and (update.get("$set") or {}).get("status") == "CONFIRMED"


def _recover(rig, how, payload, event_id):
    if how == "sweep":
        asyncio.run(rig["nexus"]._retry_failed_webhooks())
    else:  # the provider resends the identical signed delivery
        assert _post(rig, payload, event_id=event_id).json()["status"] == "duplicate"
        rig["drain"]()


@pytest.mark.parametrize("how", ["sweep", "redelivery"])
def test_a_config_read_outage_is_not_dark_and_recovers_with_one_tender(rig, monkeypatch, how):
    db = rig["db"]
    _order(db, "F1")
    payload = _event("F1", "pay_F1", 100000)
    with monkeypatch.context() as m:
        _fail(m, "find_one", _is_config_read)
        assert _post(rig, payload, event_id="evt_F1").status_code == 200
        rig["drain"]()
    row = _row(db, "pay_F1")
    assert row["status"] == "failed" and row["processed"] is False
    assert "TimeoutError" in row["handler_error"] and "outcome" not in row
    assert _bill(db, "F1")["payments"] == []

    _recover(rig, how, payload, "evt_F1")
    bill, row = _bill(db, "F1"), _row(db, "pay_F1")
    assert len(bill["payments"]) == 1 and bill["amount_paid"] == 1000.0
    assert row["status"] == "succeeded" and row["outcome"] == "credited"

    _recover(rig, "redelivery", payload, "evt_F1")
    assert len(_bill(db, "F1")["payments"]) == 1


@pytest.mark.parametrize("config_change", [
    {"$unset": {"config.key_secret": ""}}, {"$set": {"enabled": False}}])
def test_a_config_read_that_worked_still_ends_dark(rig, config_change):
    """Intentional dark mode: the read answered, Razorpay is not set up here."""
    db = rig["db"]
    db.integrations.update_one({"type": "razorpay"}, config_change)
    _order(db, "F2")
    _post(rig, _event("F2", "pay_F2", 100000), event_id="evt_F2")
    rig["drain"]()
    row = _row(db, "pay_F2")
    assert row["status"] == "succeeded" and row["outcome"] == "dark"
    assert _bill(db, "F2")["payments"] == []
