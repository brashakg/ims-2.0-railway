"""A paid fitting order always reaches the lab, whoever wins CONFIRMED (#1387).

Codex round 3: the CONFIRMED write landed, its reply was lost, and the worker
that "lost" the claim returned without the workshop safety net. The inbox row
read succeeded, so neither the sweep nor a redelivery ever came back: money in,
bill confirmed, no lab job. The old suite stubbed the safety net, which is how
this hid.

Nothing here stubs it. Every test runs the real receiver, NEXUS drain, order
doors and job creation, on mongomock and -- when a local Mongo is given, which
is every CI run -- on real Mongo too. The real database starts with NO
secondary index, so uniq_workshop_job_per_order (fail-soft, unbuildable while
legacy duplicates exist) cannot be what keeps a job single.

Real Mongo here is only ever this machine's: the host is fixed and no URL is
read, so a shell holding a remote URL cannot reach it. CI's service answers on
27017; elsewhere IMS_TEST_MONGO_PORT opts in. The test owns REAL_DB and drops
it after each test.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import sys
import threading
import time
from datetime import timedelta

import mongomock
import pymongo
import pytest
from fastapi import FastAPI, HTTPException, Response
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

from api.utils.event_time import utc_now  # noqa: E402

SECRET = "rzp_local_test_secret"
REAL_HOST = "127.0.0.1"
REAL_PORT = os.environ.get("IMS_TEST_MONGO_PORT") or ("27017" if os.environ.get("CI") else "")
# IMS_TEST_MONGO_DB: a builder's own scratch name. The test DROPS it.
REAL_DB = os.environ.get("IMS_TEST_MONGO_DB") or f"ims_test_workshop_handoff_{os.getpid()}"
# Contenders meet at Mongo's refusal of a second _id, and mongomock has none to
# meet at: _insert tests `id in store`, then stores it, under separate locks, so
# both threads can "win" and the later overwrites the earlier (measured: 1866
# of 3000 rounds at a fine thread switch). A pass there would prove nothing.
REAL_ONLY = pytest.mark.parametrize("rig", ["real"], indirect=True)
STORE = "BV-BOK-01"
USER = {"user_id": "U1", "username": "U1", "roles": ["CASHIER"], "active_store_id": STORE,
        "store_ids": [STORE]}
LENS = [{"item_id": "L1", "item_type": "LENS", "product_id": "P1",
         "product_name": "Rx lens", "quantity": 1, "unit_price": 1000.0}]


def _real_db():
    client = pymongo.MongoClient(REAL_HOST, int(REAL_PORT), serverSelectionTimeoutMS=2000)
    client.admin.command("ping")
    assert client.address[0] == REAL_HOST
    client.drop_database(REAL_DB)
    return client, client[REAL_DB]


@pytest.fixture(params=["mongomock", "real"])
def rig(request, monkeypatch):
    """The real receiver + NEXUS drain + order doors over one database."""
    from agents.implementations.nexus import NexusAgent
    from api.routers import orders as om
    from api.routers import webhooks as wh
    from database.repositories.order_repository import OrderRepository
    from database.repositories.workshop_repository import WorkshopJobRepository
    import agents.registry as reg

    client = None
    if request.param == "real":
        if not REAL_PORT:
            pytest.skip("no local Mongo given (IMS_TEST_MONGO_PORT)")
        client, db = _real_db()
    else:
        db = mongomock.MongoClient()["ims"]
    db.integrations.insert_one({"type": "razorpay", "enabled": True, "config": {
        "webhook_secret": SECRET, "key_id": "rzp_test_local", "key_secret": "local"}})
    orders = OrderRepository(db.get_collection("orders"))
    jobs = WorkshopJobRepository(db.get_collection("workshop_jobs"))
    monkeypatch.setattr(wh, "_get_db", lambda: db)
    monkeypatch.setattr(wh, "_check_webhook_rate_limit", lambda *a, **k: True)
    monkeypatch.setattr(om, "get_order_repository", lambda: orders)
    monkeypatch.setattr(om, "get_stock_repository", lambda: None)
    monkeypatch.setattr("api.dependencies.get_order_repository", lambda: orders)
    # THE job rule's order-shop read (workshop.helpers._jobs_in_reach, #1376)
    # binds its own name: point it at this rig's orders too.
    monkeypatch.setattr("api.routers.workshop.helpers.get_order_repository", lambda: orders)
    monkeypatch.setattr("api.dependencies.get_workshop_repository", lambda: jobs)
    monkeypatch.setattr("api.dependencies.get_audit_repository", lambda: None, raising=False)

    nexus = NexusAgent(db=db)
    queue = []

    async def dispatch(event, payload, source=""):
        queue.append((event, payload))

    monkeypatch.setattr(reg, "dispatch_event", dispatch)
    app = FastAPI()
    app.include_router(wh.router, prefix="/api/v1/webhooks")

    def drain():
        work = list(queue)
        queue.clear()
        for event, payload in work:
            asyncio.run(nexus.on_event(event, payload))

    yield {"db": db, "client": TestClient(app), "nexus": nexus, "queue": queue, "drain": drain,
           "orders": orders, "jobs": jobs, "collection_type": type(db.orders)}
    _assert_books_reconcile(db)
    if client is not None:
        client.drop_database(db.name)
        client.close()


def _assert_books_reconcile(db):
    """Recomputed from the raw rows, not from the code under test: amount_paid
    is the sum of the tenders, no provider payment twice, no order with two
    live lab jobs, and a stored pointer names a job of that order."""
    seen = set()
    for o in db.orders.find():
        pays = o.get("payments") or []
        # A CREDIT row is a pay-later promise, never money in.
        assert round(float(o["amount_paid"]), 2) == round(
            sum(p["amount"] for p in pays if p.get("method") != "CREDIT"), 2)
        assert o["amount_paid"] <= o["grand_total"]
        if o["status"] != "CANCELLED":  # the cancel door zeroes it; a test may write the status raw
            assert round(o["grand_total"] - o["amount_paid"], 2) == round(o["balance_due"], 2)
        for p in pays:
            pid = p.get("provider_payment_id")
            assert not pid or pid not in seen, f"{pid} tendered twice"
            seen.add(pid)
        jobs = list(db.workshop_jobs.find({"order_id": o["order_id"]}))
        assert len(jobs) <= 1, f"{o['order_id']} has {len(jobs)} lab jobs"
        if o["status"] == "CANCELLED":
            assert not _actionable(jobs), f"cancelled {o['order_id']} left work for the lab"
        if o.get("workshop_job_id"):
            assert [j["job_id"] for j in jobs] == [o["workshop_job_id"]]


def _actionable(jobs):
    """Work the lab would still pick up: every job not finished or cancelled."""
    return [j for j in jobs if j["status"] not in ("CANCELLED", "DELIVERED")]


def _bill(db, order_id, status="DRAFT", items=LENS, **over):
    doc = {"order_id": order_id, "order_number": f"BV-{order_id}", "store_id": STORE,
           "customer_id": "CUST-1", "status": status, "grand_total": 1000.0, "amount_paid": 0.0,
           "balance_due": 1000.0, "payment_status": "UNPAID", "payments": [], "items": items}
    doc.update(over)
    db.orders.insert_one(doc)
    return order_id


def _post(rig, order_id, pay_id, *, event_id=None, delivery=""):
    payload = {"entity": "event", "event": "payment.captured", "created_at": int(time.time()),
               "payload": {"payment": {"entity": {
                   "id": pay_id, "entity": "payment", "amount": 100000, "currency": "INR",
                   "status": "captured", "captured": True, "method": "upi",
                   "notes": {"order_ref": f"BV-{order_id}", "delivery": delivery}}}}}
    body = json.dumps(payload).encode()
    sig = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    res = rig["client"].post(
        "/api/v1/webhooks/razorpay", content=body,
        headers={"X-Razorpay-Signature": sig, "content-type": "application/json",
                 "X-Razorpay-Event-Id": event_id or f"evt_{pay_id}{delivery}"})
    assert res.status_code == 200
    return body


def _row(db, event_id):
    return db.webhook_inbox.find_one({"event_id": event_id})


def _jobs(db, order_id):
    return list(db.workshop_jobs.find({"order_id": order_id}))


def _sweep(rig):
    asyncio.run(rig["nexus"]._retry_failed_webhooks())


def _lose_the_confirm_reply(rig, monkeypatch, order_id):
    """The CONFIRMED write lands; only its reply is lost."""
    cls = rig["collection_type"]
    real = cls.find_one_and_update
    fired = []

    def lost(self, query, update, *a, **k):
        answer = real(self, query, update, *a, **k)
        if (self.name == "orders" and query.get("order_id") == order_id
                and (update.get("$set") or {}).get("status") == "CONFIRMED" and answer is not None):
            fired.append(1)
            raise TimeoutError("reply lost after the write")
        return answer

    monkeypatch.setattr(cls, "find_one_and_update", lost)
    return fired


def _fail_workshop(rig, m, method):
    """Every workshop_jobs call of this kind times out."""
    cls = rig["collection_type"]
    real = getattr(cls, method)

    def boom(self, *a, **k):
        if self.name == "workshop_jobs":
            raise TimeoutError(f"workshop_jobs.{method} down")
        return real(self, *a, **k)

    m.setattr(cls, method, boom)


def _meet_at_the_read(rig, monkeypatch, parties=2):
    """Hold every reader of find_by_order until `parties` have read, so each
    believes no job exists -- the exact window a read-then-insert loses."""
    from database.repositories.workshop_repository import WorkshopJobRepository

    real = WorkshopJobRepository.find_by_order
    gate = threading.Barrier(parties, timeout=5)
    state = {"open": True}

    def met(self, order_id):
        answer = real(self, order_id)
        if state["open"]:
            try:
                gate.wait()
            except threading.BrokenBarrierError:
                pass
            state["open"] = False
        return answer

    monkeypatch.setattr(WorkshopJobRepository, "find_by_order", met)


def _together(*work):
    threads = [threading.Thread(target=w) for w in work]
    for t in threads:
        t.start()
    for t in threads:
        t.join(20)
        assert not t.is_alive()


# --------------------------------------------------------------------------- #
# The Razorpay door
# --------------------------------------------------------------------------- #
def test_an_ordinary_fitting_payment_gets_its_job(rig):
    db = rig["db"]
    _bill(db, "ORD")
    _post(rig, "ORD", "pay_1")
    rig["drain"]()
    bill, jobs = db.orders.find_one({"order_id": "ORD"}), _jobs(db, "ORD")
    assert bill["status"] == "CONFIRMED" and len(bill["payments"]) == 1
    assert len(jobs) == 1 and bill["workshop_job_id"] == jobs[0]["job_id"]
    assert _row(db, "evt_pay_1")["outcome"] == "credited"


def test_a_lost_confirm_reply_still_reaches_the_workshop(rig, monkeypatch):
    db = rig["db"]
    _bill(db, "ORD")
    body = _post(rig, "ORD", "pay_1")
    with monkeypatch.context() as m:
        fired = _lose_the_confirm_reply(rig, m, "ORD")
        rig["drain"]()
    assert fired
    bill, jobs = db.orders.find_one({"order_id": "ORD"}), _jobs(db, "ORD")
    assert bill["status"] == "CONFIRMED" and bill["amount_paid"] == 1000.0
    assert len(bill["payments"]) == 1 and len(bill["status_history"]) == 1
    assert len(jobs) == 1 and bill["workshop_job_id"] == jobs[0]["job_id"]
    row = _row(db, "evt_pay_1")
    assert row["status"] == "succeeded" and row["attempts"] == 1

    # A retry of settled work changes nothing.
    _sweep(rig)
    sig = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    res = rig["client"].post(
        "/api/v1/webhooks/razorpay", content=body,
        headers={"X-Razorpay-Signature": sig, "content-type": "application/json",
                 "X-Razorpay-Event-Id": "evt_pay_1"})
    assert res.json()["status"] == "duplicate"
    rig["drain"]()
    assert len(_jobs(db, "ORD")) == 1
    assert len(db.orders.find_one({"order_id": "ORD"})["payments"]) == 1


@pytest.mark.parametrize("down", ["find", "insert_one"])
def test_an_unknown_handoff_keeps_the_event_retryable(rig, monkeypatch, down):
    """The confirm reply is lost AND the workshop is unreachable: the event
    must not end succeeded, and its retry finishes the handoff, once."""
    db = rig["db"]
    _bill(db, "ORD")
    _post(rig, "ORD", "pay_1")
    with monkeypatch.context() as m:
        _lose_the_confirm_reply(rig, m, "ORD")
        _fail_workshop(rig, m, down)
        rig["drain"]()
    row = _row(db, "evt_pay_1")
    assert row["status"] == "failed" and row["processed"] is False
    assert "workshop" in row["handler_error"]
    assert db.orders.find_one({"order_id": "ORD"})["status"] == "CONFIRMED"
    assert _jobs(db, "ORD") == []

    _sweep(rig)
    bill, row, jobs = db.orders.find_one({"order_id": "ORD"}), _row(db, "evt_pay_1"), _jobs(db, "ORD")
    assert len(jobs) == 1 and bill["workshop_job_id"] == jobs[0]["job_id"]
    assert len(bill["payments"]) == 1 and bill["amount_paid"] == 1000.0
    assert row["status"] == "succeeded" and row["outcome"] == "already_recorded"
    assert row["attempts"] == 2


def test_a_job_written_but_unacknowledged_is_not_made_twice(rig, monkeypatch):
    db = rig["db"]
    _bill(db, "ORD")
    _post(rig, "ORD", "pay_1")
    cls = rig["collection_type"]
    real = cls.insert_one

    def lost(self, doc, *a, **k):
        answer = real(self, doc, *a, **k)
        if self.name == "workshop_jobs":
            raise TimeoutError("reply lost after the job write")
        return answer

    with monkeypatch.context() as m:
        m.setattr(cls, "insert_one", lost)
        rig["drain"]()
    bill, jobs = db.orders.find_one({"order_id": "ORD"}), _jobs(db, "ORD")
    assert len(jobs) == 1 and bill["workshop_job_id"] == jobs[0]["job_id"]
    assert _row(db, "evt_pay_1")["status"] == "succeeded"


def test_a_job_whose_order_pointer_failed_is_finished_by_the_retry(rig, monkeypatch):
    db = rig["db"]
    _bill(db, "ORD")
    _post(rig, "ORD", "pay_1")
    cls = rig["collection_type"]
    real = cls.update_one

    def boom(self, query, update, *a, **k):
        if self.name == "orders" and "workshop_job_id" in (update.get("$set") or {}):
            raise TimeoutError("order pointer write down")
        return real(self, query, update, *a, **k)

    with monkeypatch.context() as m:
        m.setattr(cls, "update_one", boom)
        rig["drain"]()
    assert _row(db, "evt_pay_1")["status"] == "failed"
    assert not db.orders.find_one({"order_id": "ORD"}).get("workshop_job_id")
    _sweep(rig)
    bill, jobs = db.orders.find_one({"order_id": "ORD"}), _jobs(db, "ORD")
    assert len(jobs) == 1 and bill["workshop_job_id"] == jobs[0]["job_id"]
    assert _row(db, "evt_pay_1")["status"] == "succeeded"


@REAL_ONLY
def test_two_contending_events_make_one_tender_and_one_job(rig, monkeypatch):
    """Twin deliveries of one payment: the confirm reply is lost, so both
    workers see CONFIRMED and both reach the handoff at the same moment."""
    db = rig["db"]
    _bill(db, "ORD")
    _post(rig, "ORD", "pay_1", delivery="a")
    _post(rig, "ORD", "pay_1", delivery="b")
    work = list(rig["queue"])
    rig["queue"].clear()
    assert len(work) == 2
    with monkeypatch.context() as m:
        _lose_the_confirm_reply(rig, m, "ORD")
        _meet_at_the_read(rig, m)
        _together(*[lambda w=w: asyncio.run(rig["nexus"].on_event(*w)) for w in work])
    bill, jobs = db.orders.find_one({"order_id": "ORD"}), _jobs(db, "ORD")
    assert len(bill["payments"]) == 1 and bill["amount_paid"] == 1000.0
    assert len(jobs) == 1 and bill["workshop_job_id"] == jobs[0]["job_id"]
    _sweep(rig)
    assert len(_jobs(db, "ORD")) == 1


def test_a_bill_cancelled_before_the_retry_gets_no_job(rig, monkeypatch):
    db = rig["db"]
    _bill(db, "ORD")
    _post(rig, "ORD", "pay_1")
    with monkeypatch.context() as m:
        _lose_the_confirm_reply(rig, m, "ORD")
        _fail_workshop(rig, m, "find")
        rig["drain"]()
    db.orders.update_one({"order_id": "ORD"}, {"$set": {"status": "CANCELLED"}})
    _sweep(rig)
    assert _jobs(db, "ORD") == []
    assert _row(db, "evt_pay_1")["status"] == "succeeded"


def test_a_bill_no_door_confirmed_keeps_its_old_behaviour(rig):
    """An online or migrated order is born CONFIRMED and has its own route to
    the lab: a payment landing on it never spawned a job and still does not."""
    db = rig["db"]
    _bill(db, "ORD", status="CONFIRMED")
    _post(rig, "ORD", "pay_1")
    rig["drain"]()
    assert db.orders.find_one({"order_id": "ORD"})["amount_paid"] == 1000.0
    assert _jobs(db, "ORD") == []


# --------------------------------------------------------------------------- #
# The store rule: one order, one job, across both create doors
# --------------------------------------------------------------------------- #
@REAL_ONLY
def test_the_safety_net_cannot_race_itself_into_two_jobs(rig, monkeypatch):
    from api.routers.orders.workshop import _ensure_workshop_job_for_order

    db = rig["db"]
    _bill(db, "ORD", status="CONFIRMED")
    order = db.orders.find_one({"order_id": "ORD"})
    answers = []
    _meet_at_the_read(rig, monkeypatch)
    _together(*[lambda: answers.append(_ensure_workshop_job_for_order(order, "U1"))] * 2)
    jobs = _jobs(db, "ORD")
    assert len(jobs) == 1 and answers == [jobs[0]["job_id"]] * 2


@REAL_ONLY
def test_the_pos_door_and_the_safety_net_cannot_race_into_two_jobs(rig, monkeypatch):
    from api.routers.orders.workshop import _ensure_workshop_job_for_order
    from api.routers.workshop import jobs as jobs_door
    from api.routers.workshop.models import WorkshopJobCreate

    db = rig["db"]
    monkeypatch.setattr(jobs_door, "get_workshop_repository", lambda: rig["jobs"])
    monkeypatch.setattr(jobs_door, "get_order_repository", lambda: rig["orders"])
    monkeypatch.setattr(jobs_door, "_verify_job_prescription", lambda *a, **k: None)
    monkeypatch.setattr(jobs_door, "_check_dc_hardlock", lambda *a, **k: {})
    monkeypatch.setattr(jobs_door, "get_db", lambda: None)
    _bill(db, "ORD", status="CONFIRMED")
    order = db.orders.find_one({"order_id": "ORD"})
    body = WorkshopJobCreate(order_id="ORD", frame_details={}, lens_details={},
                             prescription_id="RX-1", expected_date="2026-12-01")
    answers = {}

    def pos():
        answers["pos"] = asyncio.run(jobs_door.create_job(body, Response(), USER))

    def net():
        answers["net"] = _ensure_workshop_job_for_order(order, "U1")

    _meet_at_the_read(rig, monkeypatch)
    _together(pos, net)
    jobs = _jobs(db, "ORD")
    assert len(jobs) == 1
    assert answers["pos"]["job_id"] == answers["net"] == jobs[0]["job_id"]


def test_a_pointer_already_stamped_is_not_a_failed_handoff(rig, monkeypatch):
    """A contender's identical stamp in the same millisecond changes nothing, so
    update() answers False. Time is frozen to make that certain, not lucky."""
    from datetime import datetime

    from api.routers.orders.workshop import ensure_workshop_job

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 11, 7, 12, 0, 0)

    db = rig["db"]
    _bill(db, "ORD", status="CONFIRMED")
    seen_before_the_pointer = db.orders.find_one({"order_id": "ORD"})
    # BaseRepository stamps updated_at through the shared UTC helper (RUN-02).
    monkeypatch.setattr("api.utils.event_time.datetime", Frozen)
    first = ensure_workshop_job(seen_before_the_pointer, "U1", rig["jobs"], rig["orders"])
    time.sleep(0.005)  # a real clock would move on here; only the frozen one must not
    assert rig["orders"].update("ORD", {"workshop_job_id": first,
                                        "workshop_job_number": _jobs(db, "ORD")[0]["job_number"]}) is False
    assert ensure_workshop_job(seen_before_the_pointer, "U1", rig["jobs"], rig["orders"]) == first


def test_a_pointer_truly_not_stored_still_raises(rig, monkeypatch):
    from api.routers.orders.workshop import ensure_workshop_job

    db = rig["db"]
    _bill(db, "ORD", status="CONFIRMED")
    monkeypatch.setattr(rig["orders"], "update", lambda *a, **k: False)
    with pytest.raises(RuntimeError, match="pointer not stored"):
        ensure_workshop_job(db.orders.find_one({"order_id": "ORD"}), "U1", rig["jobs"], rig["orders"])


# --------------------------------------------------------------------------- #
# The manual doors share the rule, and the repair reaches what has no inbox row
# --------------------------------------------------------------------------- #
def _pay(order_id="ORD", key="key-1"):
    from api.routers.orders import PaymentCreate, add_payment

    return asyncio.run(add_payment(order_id, PaymentCreate(method="CASH", amount=1000.0), USER, key))


def test_the_till_door_survives_a_lost_confirm_reply(rig, monkeypatch):
    db = rig["db"]
    _bill(db, "ORD")
    with monkeypatch.context() as m:
        fired = _lose_the_confirm_reply(rig, m, "ORD")
        res = _pay()
    bill, jobs = db.orders.find_one({"order_id": "ORD"}), _jobs(db, "ORD")
    assert fired and res["order_status"] == "CONFIRMED"
    assert len(jobs) == 1 and res["workshop_job_id"] == jobs[0]["job_id"]
    assert bill["workshop_job_id"] == jobs[0]["job_id"] and len(bill["payments"]) == 1

    assert _pay()["_idempotent_replay"] is True  # the till's retry adds nothing
    assert len(_jobs(db, "ORD")) == 1
    assert len(db.orders.find_one({"order_id": "ORD"})["payments"]) == 1


def test_the_till_door_keeps_the_payment_and_the_repair_finishes_the_job(rig, monkeypatch):
    from api.routers.orders.workshop import repair_workshop_handoffs

    db = rig["db"]
    _bill(db, "ORD")
    with monkeypatch.context() as m:
        _fail_workshop(rig, m, "find")
        res = _pay()
    assert res["order_status"] == "CONFIRMED" and res["workshop_job_id"] is None
    assert _jobs(db, "ORD") == []

    assert repair_workshop_handoffs(db) == 1
    bill, jobs = db.orders.find_one({"order_id": "ORD"}), _jobs(db, "ORD")
    assert len(jobs) == 1 and bill["workshop_job_id"] == jobs[0]["job_id"]
    assert len(bill["payments"]) == 1
    assert repair_workshop_handoffs(db) == 0
    assert len(_jobs(db, "ORD")) == 1


def test_a_capture_that_beats_the_till_is_a_refusal_not_a_crash(rig, monkeypatch):
    """Found with real processes: the capture lands after the door's balance
    check, the repository refuses the over-tender, and the cashier got a 500."""
    db = rig["db"]
    _bill(db, "ORD")
    real = rig["orders"].add_payment

    def capture_lands_first(order_id, payment, **kw):
        _post(rig, "ORD", "pay_first")
        _together(rig["drain"])  # its own thread: the till door holds this loop
        return real(order_id, payment, **kw)

    with monkeypatch.context() as m:
        m.setattr(rig["orders"], "add_payment", capture_lands_first)
        with pytest.raises(HTTPException) as refused:
            _pay()
    assert refused.value.status_code == 400
    assert "exceeds balance due" in refused.value.detail
    bill, jobs = db.orders.find_one({"order_id": "ORD"}), _jobs(db, "ORD")
    assert [p["method"] for p in bill["payments"]] == ["UPI"]
    assert bill["amount_paid"] == 1000 and bill["status"] == "CONFIRMED" and len(jobs) == 1


def test_the_confirm_door_survives_a_lost_confirm_reply(rig, monkeypatch):
    from api.routers.orders.confirm import confirm_order

    db = rig["db"]
    _bill(db, "ORD")
    with monkeypatch.context() as m:
        fired = _lose_the_confirm_reply(rig, m, "ORD")
        res = asyncio.run(confirm_order("ORD", USER))
    jobs = _jobs(db, "ORD")
    assert fired and res["status"] == "CONFIRMED"
    assert len(jobs) == 1 and res["workshop_job_id"] == jobs[0]["job_id"]


def test_the_confirm_door_still_refuses_a_bill_someone_else_cancelled(rig, monkeypatch):
    from api.routers.orders import release
    from api.routers.orders.confirm import confirm_order

    db = rig["db"]
    _bill(db, "ORD")
    real = release._claim_order_status

    def cancelled_first(repo, order_id, *a, **k):
        db.orders.update_one({"order_id": order_id}, {"$set": {"status": "CANCELLED"}})
        return real(repo, order_id, *a, **k)

    monkeypatch.setattr(release, "_claim_order_status", cancelled_first)
    monkeypatch.setattr("api.routers.orders.confirm._claim_order_status", cancelled_first,
                        raising=False)
    with pytest.raises(HTTPException) as refused:
        asyncio.run(confirm_order("ORD", USER))
    assert refused.value.status_code == 500 and _jobs(db, "ORD") == []


def test_the_repair_reaches_a_dead_event_and_nothing_it_should_not(rig, monkeypatch):
    from api.routers.orders.workshop import repair_workshop_handoffs

    db = rig["db"]
    now = utc_now().replace(tzinfo=None)
    # Dead after five workshop failures: the policy stands, the repair does not need it.
    _bill(db, "DEAD")
    _post(rig, "DEAD", "pay_1")
    with monkeypatch.context() as m:
        _fail_workshop(rig, m, "find")
        rig["drain"]()
        for _ in range(4):
            _sweep(rig)
    row = _row(db, "evt_pay_1")
    assert row["status"] == "dead" and row["attempts"] == 5 and _jobs(db, "DEAD") == []

    door = {"status_updated_at": now, "status_updated_by": "U1"}
    _bill(db, "LINKED", status="CONFIRMED", workshop_job_id="JOB-X", **door)
    _bill(db, "NOFIT", status="CONFIRMED", items=[{"item_type": "ACCESSORY"}], **door)
    _bill(db, "ONLINE", status="CONFIRMED", status_updated_at=now,
          status_updated_by="system:shopify")
    _bill(db, "BORN", status="CONFIRMED")
    _bill(db, "OLD", status="CONFIRMED", status_updated_at=now - timedelta(days=30),
          status_updated_by="U1")
    _bill(db, "GONE", status="CANCELLED", **door)

    assert repair_workshop_handoffs(db) == 1
    assert len(_jobs(db, "DEAD")) == 1
    assert [j["order_id"] for j in db.workshop_jobs.find()] == ["DEAD"]
    assert _row(db, "evt_pay_1")["status"] == "dead"  # the visible dead letter stays
    db.orders.delete_one({"order_id": "LINKED"})  # its job is fictional; books check


def test_the_hourly_tick_runs_the_repair(rig, monkeypatch):
    from agents.implementations.nexus import NexusAgent

    db = rig["db"]
    _bill(db, "ORD", status="CONFIRMED", status_updated_at=utc_now().replace(tzinfo=None),
          status_updated_by="U1")

    async def no_integrations(self):
        return []

    monkeypatch.setattr(NexusAgent, "_get_enabled_integrations", no_integrations)
    asyncio.run(rig["nexus"]._do_background_work())
    assert len(_jobs(db, "ORD")) == 1


def test_the_qc_gate_still_judges_the_pointed_job_when_the_sweep_fails(rig, monkeypatch):
    """find_by_order now raises where it used to answer []; the handover gate
    must go on examining the job the order points at."""
    from api.routers.workshop import gates

    db = rig["db"]
    db.workshop_jobs.insert_one({"_id": "J1", "job_id": "J1", "job_number": "WS-1",
                                 "order_id": "ORD", "status": "IN_PROGRESS"})
    monkeypatch.setattr(gates, "get_workshop_repository", lambda: rig["jobs"])
    def sweep_down(order_id):
        raise TimeoutError("sweep down")

    monkeypatch.setattr(rig["jobs"], "find_by_order", sweep_down)
    with pytest.raises(HTTPException) as blocked:
        gates.assert_linked_job_qc_cleared({"order_id": "ORD", "workshop_job_id": "J1"})
    assert blocked.value.status_code == 400


def test_an_unreadable_workshop_is_an_error_not_an_empty_list(rig, monkeypatch):
    with monkeypatch.context() as m:
        _fail_workshop(rig, m, "find")
        with pytest.raises(TimeoutError):
            rig["jobs"].find_by_order("ORD")


# --------------------------------------------------------------------------- #
# Codex round 4: a job on a cancelled order ends non-actionable, whichever side
# wins, at every door that can create one
# --------------------------------------------------------------------------- #
MANAGER = {**USER, "roles": ["STORE_MANAGER"]}
REASON = "customer changed mind"


def _cancel(order_id="ORD"):
    from api.routers.orders import cancel_order

    return asyncio.run(cancel_order(order_id, reason=REASON, current_user=MANAGER))


def _cancel_before_the_insert(m, order_id="ORD", cancel=_cancel):
    """The door read the bill; the real cancel lands just before its job insert."""
    from database.repositories.workshop_repository import WorkshopJobRepository

    real = WorkshopJobRepository.create
    fired = []

    def create(self, doc, *a, **k):
        if doc.get("order_id") == order_id and not fired:
            fired.append(1)
            _together(lambda: cancel(order_id))  # its own thread: a door may hold this loop
        return real(self, doc, *a, **k)

    m.setattr(WorkshopJobRepository, "create", create)
    return fired


def _door_confirmed_without_a_job(rig, monkeypatch, order_id="ORD"):
    from api.routers.orders.confirm import confirm_order

    _bill(rig["db"], order_id)
    with monkeypatch.context() as m:
        _fail_workshop(rig, m, "insert_one")
        asyncio.run(confirm_order(order_id, USER))
    bill = rig["db"].orders.find_one({"order_id": order_id})
    assert bill["status"] == "CONFIRMED" and _jobs(rig["db"], order_id) == []


def _assert_the_job_ended(db, order_id="ORD"):
    bill, jobs = db.orders.find_one({"order_id": order_id}), _jobs(db, order_id)
    assert bill["status"] == "CANCELLED" and bill["balance_due"] == 0
    assert [j["status"] for j in jobs] == ["CANCELLED"] and jobs[0]["cancelled_with_order"] is True
    assert jobs[0]["cancellation_reason"] == REASON


def test_a_cancel_inside_the_repair_leaves_the_lab_no_work(rig, monkeypatch):
    """Codex's reproduction: the sweep read CONFIRMED, the real cancel lands, the
    insert goes ahead. The job stands as a record, ended, and is not 'repaired'."""
    from api.routers.orders.workshop import repair_workshop_handoffs

    db = rig["db"]
    _door_confirmed_without_a_job(rig, monkeypatch)
    with monkeypatch.context() as m:
        fired = _cancel_before_the_insert(m)
        linked = repair_workshop_handoffs(db)
    assert fired and linked == 0
    _assert_the_job_ended(db)
    assert repair_workshop_handoffs(db) == 0
    _assert_the_job_ended(db)


def test_a_cancel_inside_the_confirm_door_leaves_the_lab_no_work(rig, monkeypatch):
    from api.routers.orders.confirm import confirm_order

    db = rig["db"]
    _bill(db, "ORD")
    with monkeypatch.context() as m:
        fired = _cancel_before_the_insert(m)
        res = asyncio.run(confirm_order("ORD", USER))
    assert fired and res["workshop_job_id"] is None
    _assert_the_job_ended(db)


def test_a_cancel_inside_the_webhook_handoff_leaves_the_lab_no_work(rig, monkeypatch):
    """A paid bill leaves through refund-advance-and-cancel; what matters here is
    the stored CANCELLED it writes after the worker's read."""
    db = rig["db"]
    _bill(db, "ORD")
    _post(rig, "ORD", "pay_1")

    def refunded_and_cancelled(order_id):
        db.orders.update_one({"order_id": order_id}, {"$set": {
            "status": "CANCELLED", "balance_due": 0.0, "cancellation_reason": REASON}})

    with monkeypatch.context() as m:
        fired = _cancel_before_the_insert(m, cancel=refunded_and_cancelled)
        rig["drain"]()
    assert fired and _row(db, "evt_pay_1")["status"] == "succeeded"
    bill, jobs = db.orders.find_one({"order_id": "ORD"}), _jobs(db, "ORD")
    assert [j["status"] for j in jobs] == ["CANCELLED"] and len(bill["payments"]) == 1


def _client_door(rig, monkeypatch):
    from api.routers.workshop import jobs as door
    from api.routers.workshop.models import WorkshopJobCreate

    monkeypatch.setattr(door, "get_workshop_repository", lambda: rig["jobs"])
    monkeypatch.setattr(door, "get_order_repository", lambda: rig["orders"])
    monkeypatch.setattr(door, "_verify_job_prescription", lambda *a, **k: None)
    monkeypatch.setattr(door, "_check_dc_hardlock", lambda *a, **k: {})
    monkeypatch.setattr(door, "get_db", lambda: None)
    body = WorkshopJobCreate(order_id="ORD", frame_details={}, lens_details={},
                             prescription_id="RX-1", expected_date="2026-12-01")
    return lambda: asyncio.run(door.create_job(body, Response(), USER))


def test_a_cancel_inside_the_client_job_door_is_refused_and_leaves_no_work(rig, monkeypatch):
    db = rig["db"]
    _bill(db, "ORD", status="CONFIRMED")
    press = _client_door(rig, monkeypatch)
    with monkeypatch.context() as m:
        fired = _cancel_before_the_insert(m)
        with pytest.raises(HTTPException) as refused:
            press()
    assert fired and refused.value.status_code == 409
    _assert_the_job_ended(db)


def test_the_client_job_door_refuses_a_cancelled_order(rig, monkeypatch):
    db = rig["db"]
    _bill(db, "ORD", status="CONFIRMED")
    press = _client_door(rig, monkeypatch)
    _cancel()
    with pytest.raises(HTTPException) as refused:
        press()
    assert refused.value.status_code == 409 and _jobs(db, "ORD") == []


def test_the_client_job_door_still_answers_a_duplicate_on_a_cancelled_order(rig, monkeypatch):
    """A second call is a duplicate request, not a new decision: it reads the job."""
    db = rig["db"]
    _bill(db, "ORD", status="CONFIRMED")
    press = _client_door(rig, monkeypatch)
    first = press()
    _cancel()
    assert press()["job_id"] == first["job_id"]
    _assert_the_job_ended(db)


@pytest.mark.parametrize("lost", ["read", "end"])
def test_a_failed_re_check_is_finished_by_the_repair(rig, monkeypatch, lost):
    """The insert stood; the re-read or the end write failed. The door answers as
    it would for any failed handoff, and the hourly repair ends the job."""
    from api.routers.orders.workshop import ensure_workshop_job, repair_workshop_handoffs
    from database.repositories.order_repository import OrderRepository

    db = rig["db"]
    _bill(db, "ORD", status="CONFIRMED", status_updated_by="U1", status_updated_at=_recent())
    order = db.orders.find_one({"order_id": "ORD"})
    with monkeypatch.context() as m:
        _cancel_before_the_insert(m)
        if lost == "read":
            real = OrderRepository.find_by_id
            state = []

            def read_fails_after_the_cancel(self, oid):
                # The cancel door's own reads pass; only the door's re-check fails.
                if threading.current_thread() is threading.main_thread():
                    return None
                return real(self, oid)

            m.setattr(OrderRepository, "find_by_id", read_fails_after_the_cancel)
        else:
            _fail_workshop(rig, m, "update_many")
        try:
            ensure_workshop_job(order, "U1", rig["jobs"], rig["orders"])
        except Exception:  # noqa: BLE001 -- a door with a retry raises; either is fine
            pass
    assert [j["status"] for j in _jobs(db, "ORD")] == ["PENDING"]
    repair_workshop_handoffs(db)
    assert [j["status"] for j in _jobs(db, "ORD")] == ["CANCELLED"]


def _recent():
    return utc_now().replace(tzinfo=None)


def test_the_repair_ends_a_job_whose_cancel_sweep_failed(rig, monkeypatch):
    from api.routers.orders.confirm import confirm_order
    from api.routers.orders.workshop import repair_workshop_handoffs

    db = rig["db"]
    _bill(db, "ORD")
    asyncio.run(confirm_order("ORD", USER))
    with monkeypatch.context() as m:
        _fail_workshop(rig, m, "update_many")
        assert _cancel()["workshop_jobs_cancelled"] == 0
    assert [j["status"] for j in _jobs(db, "ORD")] == ["PENDING"]
    assert repair_workshop_handoffs(db) == 0
    _assert_the_job_ended(db)


def test_the_repair_touches_only_a_recent_cancels_open_job(rig):
    from api.routers.orders.workshop import repair_workshop_handoffs

    db = rig["db"]
    old = (utc_now() - timedelta(days=30)).isoformat()
    now = utc_now().isoformat()
    for oid, status, cancelled_at, job in (("LIVE", "CONFIRMED", None, "PENDING"),
                                           ("GONE", "CANCELLED", now, "DELIVERED"),
                                           ("OLD", "CANCELLED", old, "PENDING")):
        _bill(db, oid, status=status, cancelled_at=cancelled_at, grand_total=0.0, balance_due=0.0,
              workshop_job_id="J" + oid, status_updated_by="U1")
        db.workshop_jobs.insert_one({"job_id": "J" + oid, "order_id": oid, "status": job})
    repair_workshop_handoffs(db)
    assert {j["order_id"]: j["status"] for j in db.workshop_jobs.find()} == {
        "LIVE": "PENDING", "GONE": "DELIVERED", "OLD": "PENDING"}
    db.workshop_jobs.delete_many({"order_id": "OLD"})  # historical: a person's, not the books check's
    db.orders.update_one({"order_id": "OLD"}, {"$unset": {"workshop_job_id": ""}})


@REAL_ONLY
def test_cancel_and_handoff_converge_in_every_real_interleaving(rig):
    """Real Mongo, real threads, nothing staged: whichever lands first, a
    cancelled bill ends with no work for the lab, in every round."""
    from api.routers.orders.workshop import ensure_workshop_job

    db = rig["db"]
    for n in range(25):
        oid = f"ORD{n}"
        _bill(db, oid, status="CONFIRMED")
        order = db.orders.find_one({"order_id": oid})
        gate = threading.Barrier(2, timeout=5)

        def hand_off():
            gate.wait()
            ensure_workshop_job(order, "U1", rig["jobs"], rig["orders"])

        def cancel():
            gate.wait()
            _cancel(oid)

        _together(hand_off, cancel)
        assert db.orders.find_one({"order_id": oid})["status"] == "CANCELLED"
        assert _actionable(_jobs(db, oid)) == []


# --------------------------------------------------------------------------- #
# Codex round 4: no status claim ever ends in an unguarded write
# --------------------------------------------------------------------------- #
def _status_writes_fail(rig, m, order_id="ORD", during_guarded=None):
    """The atomic claim and its guarded fallback both time out (nothing landed);
    `during_guarded` runs inside the second fault. Only a write carrying a status
    precondition is failed, so an unguarded write still gets through."""
    cls = rig["collection_type"]
    real_fau, real_one = cls.find_one_and_update, cls.update_one
    fired = []

    def is_claim(self, query, update):
        return (self.name == "orders" and query.get("order_id") == order_id
                and "status" in (update.get("$set") or {}) and "status_history" in (update.get("$push") or {}))

    def atomic(self, query, update, *a, **k):
        if is_claim(self, query, update):
            raise TimeoutError("atomic status claim down")
        return real_fau(self, query, update, *a, **k)

    def guarded(self, query, update, *a, **k):
        if is_claim(self, query, update) and "status" in query:
            fired.append(1)
            if during_guarded and len(fired) == 1:
                _together(during_guarded)
            raise TimeoutError("guarded status claim down")
        return real_one(self, query, update, *a, **k)

    m.setattr(cls, "find_one_and_update", atomic)
    m.setattr(cls, "update_one", guarded)
    return fired


def test_a_double_fault_cannot_resurrect_a_cancelled_order(rig, monkeypatch):
    """Codex's reproduction: atomic write fails, a real cancel lands, the guarded
    write fails, and the last fallback stamped CONFIRMED over it: paid 0, balance
    0, a pending job."""
    from api.routers.orders.confirm import confirm_order

    db = rig["db"]
    _bill(db, "ORD")
    with monkeypatch.context() as m:
        fired = _status_writes_fail(rig, m, during_guarded=_cancel)
        with pytest.raises(HTTPException) as refused:
            asyncio.run(confirm_order("ORD", USER))
    bill = db.orders.find_one({"order_id": "ORD"})
    assert fired and refused.value.status_code == 500
    assert bill["status"] == "CANCELLED" and bill["balance_due"] == 0 and _jobs(db, "ORD") == []
    assert "status_history" not in bill


def test_a_double_fault_leaves_a_draft_a_re_press_confirms_once(rig, monkeypatch):
    from api.routers.orders.confirm import confirm_order

    db = rig["db"]
    _bill(db, "ORD")
    with monkeypatch.context() as m:
        _status_writes_fail(rig, m)
        with pytest.raises(HTTPException) as refused:
            asyncio.run(confirm_order("ORD", USER))
    assert refused.value.status_code == 500
    assert db.orders.find_one({"order_id": "ORD"})["status"] == "DRAFT" and _jobs(db, "ORD") == []
    assert asyncio.run(confirm_order("ORD", USER))["status"] == "CONFIRMED"
    bill, jobs = db.orders.find_one({"order_id": "ORD"}), _jobs(db, "ORD")
    assert [h["status"] for h in bill["status_history"]] == ["CONFIRMED"] and len(jobs) == 1


def test_a_double_fault_at_the_till_keeps_the_money_and_the_repair_finishes(rig, monkeypatch):
    """No staff screen has a Confirm button, a re-press replays, and a settled bill
    takes no new payment: the hourly repair is the door that moves this bill."""
    from api.routers.orders.workshop import repair_workshop_handoffs

    db = rig["db"]
    _bill(db, "ORD")
    with monkeypatch.context() as m:
        _status_writes_fail(rig, m)
        res = _pay()
        assert _pay()["_idempotent_replay"] is True
        assert repair_workshop_handoffs(db) == 0  # still down: the next tick comes back
    bill = db.orders.find_one({"order_id": "ORD"})
    assert res["order_status"] == "DRAFT" and bill["status"] == "DRAFT" and bill["amount_paid"] == 1000

    assert repair_workshop_handoffs(db) == 1
    bill, jobs = db.orders.find_one({"order_id": "ORD"}), _jobs(db, "ORD")
    assert bill["status"] == "CONFIRMED" and bill["workshop_job_id"] == jobs[0]["job_id"]
    assert len(bill["payments"]) == 1 and bill["amount_paid"] == 1000 and bill["balance_due"] == 0

    assert repair_workshop_handoffs(db) == 0
    bill = db.orders.find_one({"order_id": "ORD"})
    assert [h["status"] for h in bill["status_history"]] == ["CONFIRMED"] and len(_jobs(db, "ORD")) == 1


def test_a_stranded_bill_with_no_fitting_is_released_to_the_ready_door(rig, monkeypatch):
    from api.routers.orders.delivery import mark_ready
    from api.routers.orders.workshop import repair_workshop_handoffs

    db = rig["db"]
    _bill(db, "ORD", items=[])
    with monkeypatch.context() as m:
        _status_writes_fail(rig, m)
        _pay()
    with pytest.raises(HTTPException) as refused:
        asyncio.run(mark_ready("ORD", USER))
    assert refused.value.status_code == 400

    assert repair_workshop_handoffs(db) == 0  # no job owed, the bill still moves
    assert db.orders.find_one({"order_id": "ORD"})["status"] == "CONFIRMED" and _jobs(db, "ORD") == []
    assert asyncio.run(mark_ready("ORD", USER))["status"] == "READY"


CREDIT_ROW = {"payment_id": "P1", "method": "CREDIT", "amount": 1000.0}


def _touched(**ago):
    return utc_now().replace(tzinfo=None) - timedelta(**ago)


def test_a_credit_tender_counts_as_the_first_payment(rig):
    """amount_paid stays 0 on a pay-later tender, and the till confirms on it."""
    from api.routers.orders.workshop import repair_workshop_handoffs

    db = rig["db"]
    _bill(db, "ORD", payments=[CREDIT_ROW], payment_status="CREDIT", updated_at=_touched(hours=1))
    assert repair_workshop_handoffs(db) == 1
    assert db.orders.find_one({"order_id": "ORD"})["status"] == "CONFIRMED" and len(_jobs(db, "ORD")) == 1


@pytest.mark.parametrize("over", [
    {"payments": [], "updated_at": _touched(hours=1)},  # an ordinary open draft
    {"payments": [CREDIT_ROW], "updated_at": _touched(days=8)},  # older than the window
    {"payments": [CREDIT_ROW]},  # never touched by the tender writer
    {"payments": [CREDIT_ROW], "updated_at": _touched(hours=1), "advance_refund_id": "RET-1"},
], ids=["no-tender", "old", "no-stamp", "refund-claim"])
def test_the_repair_leaves_a_draft_it_does_not_own(rig, over):
    from api.routers.orders.workshop import repair_workshop_handoffs

    db = rig["db"]
    _bill(db, "ORD", **over)
    assert repair_workshop_handoffs(db) == 0
    assert db.orders.find_one({"order_id": "ORD"})["status"] == "DRAFT" and _jobs(db, "ORD") == []


def test_a_cancel_inside_the_repair_wins_the_draft(rig, monkeypatch):
    from api.routers.orders.workshop import repair_workshop_handoffs

    db = rig["db"]
    _bill(db, "ORD", payments=[CREDIT_ROW], payment_status="CREDIT", updated_at=_touched(hours=1))
    real, fired = rig["collection_type"].find_one_and_update, []

    def cancel_first(self, query, update, *a, **k):
        if self.name == "orders" and (update.get("$set") or {}).get("status") == "CONFIRMED":
            fired.append(1)
            _together(_cancel)
        return real(self, query, update, *a, **k)

    with monkeypatch.context() as m:
        m.setattr(rig["collection_type"], "find_one_and_update", cancel_first)
        assert repair_workshop_handoffs(db) == 0
    bill = db.orders.find_one({"order_id": "ORD"})
    assert fired and bill["status"] == "CANCELLED" and _jobs(db, "ORD") == []
    assert "CONFIRMED" not in [h["status"] for h in bill.get("status_history") or []]


def test_a_double_fault_keeps_the_webhook_retryable(rig, monkeypatch):
    db = rig["db"]
    _bill(db, "ORD")
    _post(rig, "ORD", "pay_1")
    with monkeypatch.context() as m:
        _status_writes_fail(rig, m)
        rig["drain"]()
    bill = db.orders.find_one({"order_id": "ORD"})
    assert _row(db, "evt_pay_1")["status"] == "failed" and bill["status"] == "DRAFT"
    _sweep(rig)
    bill = db.orders.find_one({"order_id": "ORD"})
    assert bill["status"] == "CONFIRMED" and len(bill["payments"]) == 1 and len(_jobs(db, "ORD")) == 1


@pytest.mark.parametrize("to,start,required", [
    ("READY", "CONFIRMED", ("CONFIRMED", "PROCESSING")),
    ("DELIVERED", "READY", "READY"),
    ("SHIPPED", "CONFIRMED", ["CONFIRMED"]),
])
def test_every_door_of_the_helper_stops_at_the_double_fault(rig, monkeypatch, to, start, required):
    """The same helper behind the ready, deliver and online doors: a cancel inside
    the fault stands, and no status or handover field is stamped over it."""
    from api.routers.orders.release import _claim_order_status

    db = rig["db"]
    _bill(db, "ORD", status=start)
    with monkeypatch.context() as m:
        fired = _status_writes_fail(rig, m, during_guarded=_cancel)
        won = _claim_order_status(rig["orders"], "ORD", to, required, "U1", extra={"handover_record": {"by": "U1"}})
    bill = db.orders.find_one({"order_id": "ORD"})
    assert fired and won is False
    assert bill["status"] == "CANCELLED" and "handover_record" not in bill and "delivered_at" not in bill


def test_the_ready_door_answers_a_retry_on_a_double_fault(rig, monkeypatch):
    from api.routers.orders.delivery import mark_ready

    db = rig["db"]
    _bill(db, "ORD", status="CONFIRMED", items=[])
    with monkeypatch.context() as m:
        _status_writes_fail(rig, m)
        with pytest.raises(HTTPException) as refused:
            asyncio.run(mark_ready("ORD", USER))
    assert refused.value.status_code == 500
    assert db.orders.find_one({"order_id": "ORD"})["status"] == "CONFIRMED"
    assert asyncio.run(mark_ready("ORD", USER))["status"] == "READY"
