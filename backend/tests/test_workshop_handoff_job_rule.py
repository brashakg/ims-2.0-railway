"""PR #1376 round 4: the workshop handoff applies THE job rule before it returns
or links a job already on the order -- the confirm door, the till's payment
door, the Razorpay reconcile and the hourly repair alike.

Codex repro: an AUDTB order carried one legacy job stamped AUDTA and no
pointer. An AUDTB-only login got 404 on GET of that job and on POST
/workshop/jobs, yet POST /orders/{id}/confirm answered 200 with that job's id
and saved it on the order. The bill still confirms (approved paid-order
behaviour); the forbidden job is neither shown nor linked nor restamped.
A caller reaches the job through workshop.helpers._jobs_in_reach (same shop,
assigned Area Manager, HQ); a system actor only a job of its order's own shop.

Runs the real doors on the #1387 rig (mongomock, and real Mongo when given).
"""
import asyncio

import pytest

from test_workshop_handoff_recovery import (  # noqa: F401 -- the shared rig
    LENS,
    STORE,
    USER,
    _bill,
    _jobs,
    _post,
    _row,
    rig,
)
from api.utils.event_time import utc_now

OTHER = "BV-OTHER-02"
JOB = "LEGACY-JOB"


@pytest.fixture(autouse=True)
def _rule_reads_the_rig(rig, monkeypatch):  # noqa: F811
    # THE job rule's order-shop read (helpers._jobs_in_reach) binds its own name.
    monkeypatch.setattr("api.routers.workshop.helpers.get_order_repository", lambda: rig["orders"])


def _legacy_job(db, order_id, shop=OTHER, job_id=JOB):
    db.workshop_jobs.insert_one({
        "job_id": job_id, "job_number": f"WS-{job_id}", "order_id": order_id,
        "store_id": shop, "status": "PENDING", "created_at": "2026-11-01T00:00:00"})


def _snapshot(db, order_id):
    return [dict(j, _id=str(j["_id"])) for j in db.workshop_jobs.find({"order_id": order_id})]


def _confirm(user, order_id="ORD"):
    from api.routers.orders.confirm import confirm_order

    return asyncio.run(confirm_order(order_id, user))


def _pay(user, order_id="ORD"):
    from api.routers.orders import PaymentCreate, add_payment

    return asyncio.run(add_payment(order_id, PaymentCreate(method="CASH", amount=1000.0), user, "key-r4"))


AM_BOTH = {"user_id": "AM", "username": "AM", "roles": ["AREA_MANAGER"],
           "active_store_id": STORE, "store_ids": [STORE, OTHER]}
HQ = {"user_id": "HQ", "username": "HQ", "roles": ["ADMIN"], "active_store_id": "HQ",
      "store_ids": []}


# --- refused: the caller cannot reach the job --------------------------------


def test_confirm_does_not_show_or_link_an_unreachable_job(rig):
    db = rig["db"]
    _bill(db, "ORD")
    _legacy_job(db, "ORD")
    before = _snapshot(db, "ORD")
    res = _confirm(USER)
    bill = db.orders.find_one({"order_id": "ORD"})
    assert res["status"] == "CONFIRMED" and bill["status"] == "CONFIRMED"
    assert res["workshop_job_id"] is None
    assert not bill.get("workshop_job_id") and not bill.get("workshop_job_number")
    assert _snapshot(db, "ORD") == before  # one job, not restamped


def test_confirm_does_not_return_a_pointer_to_an_unreachable_job(rig):
    db = rig["db"]
    _bill(db, "ORD", workshop_job_id=JOB, workshop_job_number=f"WS-{JOB}")
    _legacy_job(db, "ORD")
    res = _confirm(USER)
    assert res["status"] == "CONFIRMED" and res["workshop_job_id"] is None
    assert db.orders.find_one({"order_id": "ORD"})["workshop_job_id"] == JOB  # untouched


def test_till_payment_keeps_the_money_and_does_not_link_an_unreachable_job(rig):
    db = rig["db"]
    _bill(db, "ORD")
    _legacy_job(db, "ORD")
    before = _snapshot(db, "ORD")
    res = _pay(USER)
    bill = db.orders.find_one({"order_id": "ORD"})
    assert res["order_status"] == "CONFIRMED" and res["workshop_job_id"] is None
    assert len(bill["payments"]) == 1 and bill["amount_paid"] == 1000
    assert not bill.get("workshop_job_id")
    assert _snapshot(db, "ORD") == before
    assert _pay(USER)["_idempotent_replay"] is True  # a retry adds nothing
    assert len(db.orders.find_one({"order_id": "ORD"})["payments"]) == 1


def test_razorpay_reconcile_credits_and_confirms_but_does_not_link(rig):
    db = rig["db"]
    _bill(db, "ORD")
    _legacy_job(db, "ORD")
    before = _snapshot(db, "ORD")
    _post(rig, "ORD", "pay_r4")
    rig["drain"]()
    bill = db.orders.find_one({"order_id": "ORD"})
    assert bill["status"] == "CONFIRMED" and len(bill["payments"]) == 1
    assert _row(db, "evt_pay_r4")["outcome"] == "credited"
    assert not bill.get("workshop_job_id")
    assert _snapshot(db, "ORD") == before


def test_the_repair_does_not_link_another_shops_job(rig):
    from api.routers.orders.workshop import repair_workshop_handoffs

    db = rig["db"]
    _bill(db, "ORD", status="CONFIRMED", status_updated_at=utc_now().replace(tzinfo=None),
          status_updated_by="U1")
    _legacy_job(db, "ORD")
    before = _snapshot(db, "ORD")
    assert repair_workshop_handoffs(db) == 0
    assert not db.orders.find_one({"order_id": "ORD"}).get("workshop_job_id")
    assert _snapshot(db, "ORD") == before
    assert repair_workshop_handoffs(db) == 0  # every later tick stays a no-op
    assert _snapshot(db, "ORD") == before


def test_a_legacy_pair_with_one_unreachable_job_links_neither(rig):
    db = rig["db"]
    _bill(db, "ORD")
    _legacy_job(db, "ORD", shop=STORE, job_id="MINE")
    _legacy_job(db, "ORD")
    res = _confirm(USER)
    assert res["status"] == "CONFIRMED" and res["workshop_job_id"] is None
    assert not db.orders.find_one({"order_id": "ORD"}).get("workshop_job_id")
    db.workshop_jobs.delete_many({"order_id": "ORD"})  # the books check allows one job


# --- allowed: same shop / assigned Area Manager / HQ -------------------------


def test_confirm_links_a_same_shop_job(rig):
    db = rig["db"]
    _bill(db, "ORD")
    _legacy_job(db, "ORD", shop=STORE)
    res = _confirm(USER)
    assert res["workshop_job_id"] == JOB
    assert db.orders.find_one({"order_id": "ORD"})["workshop_job_id"] == JOB


def test_the_repair_links_a_same_shop_job(rig):
    from api.routers.orders.workshop import repair_workshop_handoffs

    db = rig["db"]
    _bill(db, "ORD", status="CONFIRMED", status_updated_at=utc_now().replace(tzinfo=None),
          status_updated_by="U1")
    _legacy_job(db, "ORD", shop=STORE)
    assert repair_workshop_handoffs(db) == 1
    assert db.orders.find_one({"order_id": "ORD"})["workshop_job_id"] == JOB


@pytest.mark.parametrize("who", [AM_BOTH, HQ], ids=["area-manager-of-both", "hq"])
def test_an_authorized_caller_links_the_mismatched_job(rig, who):
    db = rig["db"]
    _bill(db, "ORD")
    _legacy_job(db, "ORD")
    before = _snapshot(db, "ORD")
    res = _confirm(who)
    assert res["workshop_job_id"] == JOB
    assert db.orders.find_one({"order_id": "ORD"})["workshop_job_id"] == JOB
    assert _snapshot(db, "ORD") == before  # linked, never restamped


def test_a_bill_with_no_job_still_gets_one_at_its_shop(rig):
    db = rig["db"]
    _bill(db, "ORD", items=LENS)
    res = _confirm(USER)
    jobs = _jobs(db, "ORD")
    assert len(jobs) == 1 and res["workshop_job_id"] == jobs[0]["job_id"]
    assert jobs[0]["store_id"] == STORE
