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
REAL_DB = f"ims_test_workshop_handoff_{os.getpid()}"
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
        assert round(float(o["amount_paid"]), 2) == round(sum(p["amount"] for p in pays), 2)
        assert o["amount_paid"] <= o["grand_total"]
        assert round(o["grand_total"] - o["amount_paid"], 2) == round(o["balance_due"], 2)
        for p in pays:
            pid = p.get("provider_payment_id")
            assert not pid or pid not in seen, f"{pid} tendered twice"
            seen.add(pid)
        jobs = list(db.workshop_jobs.find({"order_id": o["order_id"]}))
        assert len(jobs) <= 1, f"{o['order_id']} has {len(jobs)} lab jobs"
        if o.get("workshop_job_id"):
            assert [j["job_id"] for j in jobs] == [o["workshop_job_id"]]


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
    monkeypatch.setattr("database.repositories.base_repository.datetime", Frozen)
    first = ensure_workshop_job(seen_before_the_pointer, "U1", rig["jobs"], rig["orders"])
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
