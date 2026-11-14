"""L2-WS-F02: a real auto-created workshop job (the confirm safety net's shape)
and a POS-created one both show the order's customer, order number, frame, lens
and a usable promised date on every read door (list, pending board, detail) --
the fields the board, the drawer and the printed job card read. Neither create
door stores them flat, so the read door derives them; nothing is written back.

Real WorkshopJobRepository / OrderRepository over the strict Mongo double."""
import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("MONGODB_URI", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import api.dependencies as deps  # noqa: E402
from api.routers import workshop as wm  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402
from api.routers.workshop import helpers as wh  # noqa: E402
from database.repositories.order_repository import OrderRepository  # noqa: E402
from database.repositories.workshop_repository import WorkshopJobRepository  # noqa: E402
from strict_fakes import StrictDB  # noqa: E402

ORDERS = [
    {"order_id": "ORD-AUTO", "store_id": "SHOP-A", "order_number": "BV-0001",
     "customer_id": "C1", "customer_name": "Asha Rao", "customer_phone": "9800000001"},
    {"order_id": "ORD-POS", "store_id": "SHOP-A", "order_number": "BV-0002",
     "customer_id": "C2", "customer_name": "Ravi Kumar", "customer_phone": "9800000002"},
]

# Exactly what orders.workshop.ensure_workshop_job writes (no priority, nested
# frame, catalogue-lens fallback, expected_date as a bare date).
AUTO_JOB = {
    "job_id": "J-AUTO", "job_number": "WS-261104-45039BF8", "order_id": "ORD-AUTO",
    "store_id": "SHOP-A", "status": "PENDING", "auto_created": True,
    "frame_details": {"product_id": "F1", "name": "RB5154 Clubmaster", "sku": "RB1", "brand": "Ray-Ban"},
    "lens_details": {"product_id": "L1", "name": "Zeiss SV 1.6"},
    "prescription_id": "RX-1", "fitting_instructions": None, "special_notes": None,
    "expected_date": "2026-11-09", "created_at": "2026-11-04T10:00:00",
}
# What the POS client's createJob writes (lens_details from the lens modal).
POS_JOB = {
    "job_id": "J-POS", "job_number": "WS-261104-AAAA0001", "order_id": "ORD-POS",
    "store_id": "SHOP-A", "status": "PENDING",
    "frame_details": {"product_id": "F2", "name": "Ray-Ban RB2140", "brand": "Ray-Ban"},
    "lens_details": {"type": "Progressive", "material": "1.67", "index": "167", "coatings": ["BlueCut"]},
    "prescription_id": "RX-2", "expected_date": "2026-11-09", "created_at": "2026-11-04T11:00:00",
}

STAFF_A = {"user_id": "w1", "roles": ["WORKSHOP_STAFF"], "active_store_id": "SHOP-A",
           "store_ids": ["SHOP-A"]}


@pytest.fixture()
def client(monkeypatch):
    db = StrictDB()
    db.seed("workshop_jobs", [dict(AUTO_JOB), dict(POS_JOB)])
    db.seed("orders", [dict(o) for o in ORDERS])
    jrepo = WorkshopJobRepository(db.get_collection("workshop_jobs"))
    orepo = OrderRepository(db.get_collection("orders"))
    monkeypatch.setattr(wm, "get_workshop_repository", lambda: jrepo)
    monkeypatch.setattr(wm, "get_order_repository", lambda: orepo)
    monkeypatch.setattr(wm, "get_db", lambda: db)
    monkeypatch.setattr(wh, "get_db", lambda: db)
    monkeypatch.setattr(deps, "get_db", lambda: db)
    app = FastAPI()
    app.include_router(wm.router, prefix="/workshop")

    async def _u():
        return STAFF_A

    app.dependency_overrides[get_current_user] = _u
    c = TestClient(app)
    c.db = db
    return c


EXPECTED = {
    "J-AUTO": {"customerName": "Asha Rao", "customerPhone": "9800000001",
               "orderNumber": "BV-0001", "frameName": "Ray-Ban RB5154 Clubmaster",
               "lensType": "Zeiss SV 1.6", "promisedDate": "2026-11-09", "priority": "NORMAL"},
    "J-POS": {"customerName": "Ravi Kumar", "customerPhone": "9800000002",
              "orderNumber": "BV-0002", "frameName": "Ray-Ban RB2140",
              "lensType": "Progressive 1.67", "promisedDate": "2026-11-09", "priority": "NORMAL"},
}


def _check(job):
    for k, v in EXPECTED[job["id"]].items():
        assert job.get(k) == v, (job["id"], k, job.get(k))


@pytest.mark.parametrize("url", ["/workshop/jobs", "/workshop/pending"])
def test_every_list_door_shows_the_jobs_identity(client, url):
    resp = client.get(url, params={"store_id": "SHOP-A"})
    assert resp.status_code == 200, resp.text
    jobs = resp.json()["jobs"]
    assert sorted(j["id"] for j in jobs) == ["J-AUTO", "J-POS"]
    for j in jobs:
        _check(j)


@pytest.mark.parametrize("jid", ["J-AUTO", "J-POS"])
def test_detail_door_shows_the_jobs_identity(client, jid):
    resp = client.get(f"/workshop/jobs/{jid}")
    assert resp.status_code == 200, resp.text
    _check(resp.json())


def test_read_doors_write_nothing_back_and_stored_fields_win(client):
    jobs = client.db.get_collection("workshop_jobs")
    jobs.update_one({"job_id": "J-POS"}, {"$set": {"customer_name": "As stored", "priority": "URGENT"}})
    body = client.get("/workshop/jobs/J-POS").json()
    assert body["customerName"] == "As stored"
    assert body["priority"] == "URGENT"
    raw = jobs.find_one({"job_id": "J-AUTO"})
    client.get("/workshop/jobs/J-AUTO")
    assert jobs.find_one({"job_id": "J-AUTO"}) == raw
    assert "customer_name" not in raw and "priority" not in raw


def test_missing_order_details_stay_missing_not_invented(client):
    client.db.get_collection("orders").update_one(
        {"order_id": "ORD-AUTO"}, {"$unset": {"customer_name": "", "customer_phone": ""}})
    client.db.get_collection("workshop_jobs").update_one(
        {"job_id": "J-AUTO"}, {"$set": {"frame_details": {}, "lens_details": {}, "expected_date": None}})
    body = client.get("/workshop/jobs/J-AUTO").json()
    assert body.get("customerName") is None
    assert body.get("customerPhone") is None
    assert body["frameName"] is None
    assert body["lensType"] is None
    assert body["promisedDate"] is None
    assert body["orderNumber"] == "BV-0001"
