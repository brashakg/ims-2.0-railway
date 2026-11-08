"""L2-WS-F01: POST /workshop/jobs must authorize the REFERENCED ORDER's shop
before the dedup return, the reverse-pointer backfill or the create.

Audit repro: a WORKSHOP_STAFF login assigned only shop A posted another shop's
(B) order id and got 201 -- a job opened at A and workshop_job_id written onto
B's order. A denied attempt must write nothing and reveal no existing job; the
same-shop door keeps its idempotency; an admin's job lands at the ORDER's shop
(as the confirm safety net already stamps it)."""
import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("MONGODB_URI", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api.routers import workshop as wm  # noqa: E402
from api.routers.workshop import jobs as jobs_mod  # noqa: E402
from api.routers.workshop import helpers as helpers_mod  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402


class _JobRepo:
    def __init__(self, jobs=None):
        self.jobs = list(jobs or [])
        self.created = []

    def find_by_order(self, oid):
        return [j for j in self.jobs if j.get("order_id") == oid]

    def create(self, data):
        doc = dict(data, job_id="NEW-JOB")
        self.created.append(doc)
        self.jobs.append(doc)
        return doc


class _OrderRepo:
    def __init__(self, order):
        self.order = order
        self.updates = []

    def find_by_id(self, oid):
        return self.order if self.order and self.order.get("order_id") == oid else None

    def update(self, oid, data):
        self.updates.append((oid, data))

    def ids_in_stores(self, order_ids, store_ids):
        # THE job rule's order-shop read (helpers._jobs_in_reach).
        o = self.order or {}
        return {o.get("order_id")} & set(order_ids) if o.get("store_id") in store_ids else set()


def _order(store):
    return {"order_id": "ORD-B", "store_id": store, "status": "DELIVERED", "items": []}


_EXISTING = {
    "job_id": "J-B",
    "job_number": "WS-B-1",
    "order_id": "ORD-B",
    "store_id": "SHOP-B",
    "created_at": "2026-11-01T00:00:00",
}

_BODY = {
    "order_id": "ORD-B",
    "frame_details": {},
    "lens_details": {},
    "prescription_id": "",
    "expected_date": "2026-11-10",
}


def _client(monkeypatch, user, order, jobs=None):
    app = FastAPI()
    app.include_router(wm.router, prefix="/workshop")
    jrepo, orepo = _JobRepo(jobs), _OrderRepo(order)
    monkeypatch.setattr(jobs_mod, "get_workshop_repository", lambda: jrepo)
    monkeypatch.setattr(jobs_mod, "get_order_repository", lambda: orepo)
    monkeypatch.setattr(helpers_mod, "get_order_repository", lambda: orepo)
    monkeypatch.setattr(jobs_mod, "get_db", lambda: None)

    async def _u():
        return user

    app.dependency_overrides[get_current_user] = _u
    return TestClient(app), jrepo, orepo


def _user(roles, active, stores=None):
    return {
        "user_id": "u1",
        "roles": roles,
        "active_store_id": active,
        "store_ids": stores or [active],
    }


# --- denied: another shop's order --------------------------------------------


def test_other_shop_order_create_denied_writes_nothing(monkeypatch):
    c, jrepo, orepo = _client(
        monkeypatch, _user(["WORKSHOP_STAFF"], "SHOP-A"), _order("SHOP-B")
    )
    r = c.post("/workshop/jobs", json=_BODY)
    assert r.status_code == 404
    assert jrepo.created == []
    assert orepo.updates == []


def test_other_shop_order_dedup_denied_reveals_nothing(monkeypatch):
    c, jrepo, orepo = _client(
        monkeypatch,
        _user(["WORKSHOP_STAFF"], "SHOP-A"),
        _order("SHOP-B"),
        jobs=[dict(_EXISTING)],
    )
    r = c.post("/workshop/jobs", json=_BODY)
    assert r.status_code == 404
    assert "J-B" not in r.text and "WS-B-1" not in r.text
    assert jrepo.created == []
    assert orepo.updates == []  # no reverse-pointer backfill onto B's order


def test_area_manager_outside_own_shops_denied(monkeypatch):
    c, jrepo, orepo = _client(
        monkeypatch,
        _user(["AREA_MANAGER"], "SHOP-A", ["SHOP-A", "SHOP-C"]),
        _order("SHOP-B"),
    )
    assert c.post("/workshop/jobs", json=_BODY).status_code == 404
    assert jrepo.created == [] and orepo.updates == []


def test_unattributed_order_is_admin_only(monkeypatch):
    c, jrepo, orepo = _client(
        monkeypatch, _user(["WORKSHOP_STAFF"], "SHOP-A"), _order(None)
    )
    assert c.post("/workshop/jobs", json=_BODY).status_code == 404
    assert jrepo.created == [] and orepo.updates == []


# --- allowed: same shop / own shops / HQ -------------------------------------


def test_same_shop_create_ok(monkeypatch):
    c, jrepo, orepo = _client(
        monkeypatch, _user(["WORKSHOP_STAFF"], "SHOP-B"), _order("SHOP-B")
    )
    r = c.post("/workshop/jobs", json=_BODY)
    assert r.status_code == 201
    assert jrepo.created[0]["store_id"] == "SHOP-B"
    assert orepo.updates and orepo.updates[0][1]["workshop_job_id"] == "NEW-JOB"


def test_same_shop_dedup_stays_idempotent(monkeypatch):
    c, jrepo, orepo = _client(
        monkeypatch,
        _user(["WORKSHOP_STAFF"], "SHOP-B"),
        _order("SHOP-B"),
        jobs=[dict(_EXISTING)],
    )
    r = c.post("/workshop/jobs", json=_BODY)
    assert r.status_code == 200
    assert r.json()["job_id"] == "J-B" and r.json()["existing"] is True
    assert jrepo.created == []
    assert orepo.updates[0][1]["workshop_job_id"] == "J-B"


def test_area_manager_own_second_shop_job_lands_at_order_shop(monkeypatch):
    c, jrepo, _ = _client(
        monkeypatch,
        _user(["AREA_MANAGER"], "SHOP-A", ["SHOP-A", "SHOP-B"]),
        _order("SHOP-B"),
    )
    assert c.post("/workshop/jobs", json=_BODY).status_code == 201
    assert jrepo.created[0]["store_id"] == "SHOP-B"


def test_admin_any_shop_job_lands_at_order_shop(monkeypatch):
    c, jrepo, orepo = _client(
        monkeypatch, _user(["ADMIN"], "HQ"), _order("SHOP-B")
    )
    assert c.post("/workshop/jobs", json=_BODY).status_code == 201
    assert jrepo.created[0]["store_id"] == "SHOP-B"
    assert orepo.updates


# --- PR #1376 round 3: the dedup's chosen job passes THE job rule ------------
# Codex repro: a legacy job stamped AUDTA but attached to an AUDTB order; an
# AUDTB-only login posted its own B order and got 200 existing=true with that
# job's id -- and workshop_job_id was backfilled onto the B order -- while GET
# of the same job is 404 to them. The chosen job is now checked first.

import copy  # noqa: E402

_MISMATCH = dict(_EXISTING, job_id="J-A", job_number="WS-A-1", store_id="SHOP-A")


def test_own_order_with_other_shop_job_refused_order_unchanged(monkeypatch):
    order = _order("SHOP-B")
    before = copy.deepcopy(order)
    c, jrepo, orepo = _client(
        monkeypatch, _user(["WORKSHOP_STAFF"], "SHOP-B"), order, jobs=[dict(_MISMATCH)]
    )
    r = c.post("/workshop/jobs", json=_BODY)
    assert r.status_code == 404, r.text
    assert "J-A" not in r.text and "WS-A-1" not in r.text
    assert jrepo.created == []  # no second job
    assert orepo.updates == []  # no reverse-pointer backfill
    assert order == before


def test_pointer_already_at_other_shop_job_refused(monkeypatch):
    order = dict(_order("SHOP-B"), workshop_job_id="J-A", workshop_job_number="WS-A-1")
    before = copy.deepcopy(order)
    c, jrepo, orepo = _client(
        monkeypatch, _user(["WORKSHOP_STAFF"], "SHOP-B"), order, jobs=[dict(_MISMATCH)]
    )
    assert c.post("/workshop/jobs", json=_BODY).status_code == 404
    assert jrepo.created == [] and orepo.updates == [] and order == before


def test_reverse_direction_job_in_my_shop_on_other_shop_order_refused(monkeypatch):
    # Job stamped with MY shop, order belongs to the other shop: refused at the
    # order, nothing written.
    order = _order("SHOP-A")
    before = copy.deepcopy(order)
    c, jrepo, orepo = _client(
        monkeypatch,
        _user(["WORKSHOP_STAFF"], "SHOP-B"),
        order,
        jobs=[dict(_EXISTING, store_id="SHOP-B")],
    )
    r = c.post("/workshop/jobs", json=_BODY)
    assert r.status_code == 404
    assert "J-B" not in r.text
    assert jrepo.created == [] and orepo.updates == [] and order == before


def test_area_manager_of_both_shops_keeps_the_mismatched_job(monkeypatch):
    c, jrepo, orepo = _client(
        monkeypatch,
        _user(["AREA_MANAGER"], "SHOP-A", ["SHOP-A", "SHOP-B"]),
        _order("SHOP-B"),
        jobs=[dict(_MISMATCH)],
    )
    r = c.post("/workshop/jobs", json=_BODY)
    assert r.status_code == 200 and r.json()["job_id"] == "J-A"
    assert jrepo.created == []
    assert orepo.updates[0][1]["workshop_job_id"] == "J-A"


def test_admin_keeps_the_mismatched_job(monkeypatch):
    c, jrepo, _ = _client(
        monkeypatch, _user(["ADMIN"], "HQ"), _order("SHOP-B"), jobs=[dict(_MISMATCH)]
    )
    r = c.post("/workshop/jobs", json=_BODY)
    assert r.status_code == 200 and r.json()["job_id"] == "J-A"
    assert jrepo.created == []


def test_legacy_pair_with_one_other_shop_job_refused_even_if_pointer_is_mine(monkeypatch):
    # Every job already on the order passes the rule: the dedup never links or
    # shows one while another shop's job sits on the same order.
    order = dict(_order("SHOP-B"), workshop_job_id="J-B", workshop_job_number="WS-B-1")
    before = copy.deepcopy(order)
    c, jrepo, orepo = _client(
        monkeypatch,
        _user(["WORKSHOP_STAFF"], "SHOP-B"),
        order,
        jobs=[dict(_EXISTING), dict(_MISMATCH)],
    )
    r = c.post("/workshop/jobs", json=_BODY)
    assert r.status_code == 404 and "J-A" not in r.text
    assert jrepo.created == [] and orepo.updates == [] and order == before
