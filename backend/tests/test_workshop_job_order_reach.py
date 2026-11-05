"""PR #1376 review corrections for the workshop job boundary.

F1 -- GET /workshop/jobs/by-vendor/{vendor_id} returned every shop's jobs for
that vendor to any login (store staff, an A-only area manager, a no-shop
login). It must read only the caller's shops, in the query, so pagination
pages over reachable jobs only.

F3 -- a legacy job whose stored shop disagrees with its ORDER's shop (the
L2-WS-F01 create bug stamped the caller's shop on another shop's order), or
whose order cannot be resolved, must not be shown to / actionable by a
non-HQ login on the list, detail, update and sibling doors. HQ keeps it;
correct same-shop jobs and an area manager's own shops are unchanged."""
import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("MONGODB_URI", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api.routers import workshop as wm  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402


def _match(doc, flt):
    for k, v in flt.items():
        if isinstance(v, dict) and "$in" in v:
            if doc.get(k) not in v["$in"]:
                return False
        elif isinstance(v, dict) and "$nin" in v:
            if doc.get(k) in v["$nin"]:
                return False
        elif doc.get(k) != v:
            return False
    return True


class _JobRepo:
    def __init__(self, jobs):
        self.jobs = {j["job_id"]: dict(j) for j in jobs}
        self.updates = []

    def find_by_id(self, jid):
        j = self.jobs.get(jid)
        return dict(j) if j else None

    def find_many(self, flt, skip=0, limit=100, sort=None):
        rows = [dict(j) for j in self.jobs.values() if _match(j, flt)]
        if sort:
            key, direction = sort[0]
            rows.sort(key=lambda d: d.get(key) or "", reverse=direction == -1)
        return rows[skip: skip + limit] if limit else rows[skip:]

    def find_pending(self, store_id=None):
        flt = {"status": {"$in": ["PENDING", "IN_PROGRESS"]}}
        if store_id:
            flt["store_id"] = store_id
        return self.find_many(flt, limit=0)

    def update(self, jid, data):
        self.updates.append((jid, data))
        return True

    def update_status(self, jid, status, user_id):
        self.updates.append((jid, {"status": status}))
        return True


class _OrderRepo:
    def __init__(self, orders):
        self.orders = {o["order_id"]: o for o in orders}

    def find_by_id(self, oid):
        return self.orders.get(oid)


_ORDERS = [
    {"order_id": "ORD-A", "store_id": "SHOP-A"},
    {"order_id": "ORD-B", "store_id": "SHOP-B"},
]


def _job(jid, store, order, expected, status="PENDING", vendor="V1"):
    return {
        "job_id": jid,
        "job_number": "WS-" + jid,
        "order_id": order,
        "store_id": store,
        "vendor_id": vendor,
        "status": status,
        "expected_date": expected,
        "created_at": expected,
        "fitting_details": {"confirmed_by_sales": True},
    }


_JOBS = [
    # B's job sorts FIRST, so an unscoped page of 1 would be B's.
    _job("J-B", "SHOP-B", "ORD-B", "2026-11-01"),
    _job("J-A", "SHOP-A", "ORD-A", "2026-11-02"),
    _job("J-B-DONE", "SHOP-B", "ORD-B", "2026-11-03", status="DELIVERED"),
    # L2-WS-F01 legacy: stamped A, attached to B's order.
    _job("J-MIS", "SHOP-A", "ORD-B", "2026-11-04", vendor="V2"),
    # Order no longer resolves.
    _job("J-GONE", "SHOP-A", "ORD-GONE", "2026-11-05", vendor="V2"),
]


def _client(monkeypatch, user):
    app = FastAPI()
    app.include_router(wm.router, prefix="/workshop")
    jrepo = _JobRepo(_JOBS)
    # Package-level patch: _WorkshopNamespace forwards it to every sub-module
    # (jobs, board, helpers, status) that binds the name.
    monkeypatch.setattr(wm, "get_workshop_repository", lambda: jrepo)
    monkeypatch.setattr(wm, "get_order_repository", lambda: _OrderRepo(_ORDERS))
    monkeypatch.setattr(wm, "get_db", lambda: None)

    async def _u():
        return user

    app.dependency_overrides[get_current_user] = _u
    return TestClient(app), jrepo


def _user(roles, active, stores=None):
    return {
        "user_id": "u1",
        "roles": roles,
        "active_store_id": active,
        "store_ids": stores if stores is not None else ([active] if active else []),
    }


STAFF_A = _user(["WORKSHOP_STAFF"], "SHOP-A")
STAFF_B = _user(["WORKSHOP_STAFF"], "SHOP-B")
NO_SHOP = _user(["WORKSHOP_STAFF"], None, [])
AM_A = _user(["AREA_MANAGER"], "SHOP-A")
AM_AB = _user(["AREA_MANAGER"], "SHOP-A", ["SHOP-A", "SHOP-B"])
ADMIN = _user(["ADMIN"], "HQ")


def _ids(resp):
    assert resp.status_code == 200, resp.text
    return sorted(j["id"] for j in resp.json()["jobs"])


# --- F1: the vendor queue ----------------------------------------------------


def test_vendor_queue_store_staff_sees_only_own_shop(monkeypatch):
    c, _ = _client(monkeypatch, STAFF_A)
    assert _ids(c.get("/workshop/jobs/by-vendor/V1")) == ["J-A"]
    r = c.get("/workshop/jobs/by-vendor/V1", params={"include_delivered": True})
    assert _ids(r) == ["J-A"]


def test_vendor_queue_pagination_pages_over_reachable_jobs(monkeypatch):
    c, _ = _client(monkeypatch, STAFF_A)
    r = c.get("/workshop/jobs/by-vendor/V1", params={"limit": 1, "skip": 0})
    assert _ids(r) == ["J-A"]


@pytest.mark.parametrize("user", [NO_SHOP, AM_A], ids=["no-shop", "area-manager-A"])
def test_vendor_queue_no_shop_and_single_shop_am(monkeypatch, user):
    c, _ = _client(monkeypatch, user)
    expected = [] if user is NO_SHOP else ["J-A"]
    assert _ids(c.get("/workshop/jobs/by-vendor/V1")) == expected


def test_vendor_queue_area_manager_keeps_assigned_reach(monkeypatch):
    c, _ = _client(monkeypatch, AM_AB)
    assert _ids(c.get("/workshop/jobs/by-vendor/V1")) == ["J-A", "J-B"]


def test_vendor_queue_hq_sees_chain(monkeypatch):
    c, _ = _client(monkeypatch, ADMIN)
    assert _ids(c.get("/workshop/jobs/by-vendor/V1")) == ["J-A", "J-B"]
    r = c.get("/workshop/jobs/by-vendor/V1", params={"include_delivered": True})
    assert _ids(r) == ["J-A", "J-B", "J-B-DONE"]
    assert _ids(c.get("/workshop/jobs/by-vendor/V2")) == ["J-GONE", "J-MIS"]


# --- F3: legacy mismatched / unresolvable jobs --------------------------------


@pytest.mark.parametrize("jid", ["J-MIS", "J-GONE"])
@pytest.mark.parametrize("user", [STAFF_A, STAFF_B], ids=["A", "B"])
def test_inconsistent_job_detail_hidden_from_non_hq(monkeypatch, user, jid):
    c, _ = _client(monkeypatch, user)
    assert c.get("/workshop/jobs/" + jid).status_code == 404


def test_area_manager_holding_both_shops_keeps_mismatched_job(monkeypatch):
    """An area manager assigned BOTH the job's and the order's shop is no
    leak; an unresolvable order still hides the job."""
    c, _ = _client(monkeypatch, AM_AB)
    assert c.get("/workshop/jobs/J-MIS").status_code == 200
    assert c.get("/workshop/jobs/J-GONE").status_code == 404


@pytest.mark.parametrize("jid", ["J-MIS", "J-GONE"])
def test_inconsistent_job_update_and_sibling_refused_write_nothing(monkeypatch, jid):
    c, jrepo = _client(monkeypatch, STAFF_A)
    r = c.put("/workshop/jobs/" + jid, json={"special_notes": "x"})
    assert r.status_code == 404
    r = c.post("/workshop/jobs/%s/start" % jid)
    assert r.status_code == 404
    assert jrepo.updates == []


def test_inconsistent_jobs_not_listed_for_store_staff(monkeypatch):
    c, _ = _client(monkeypatch, STAFF_A)
    assert _ids(c.get("/workshop/jobs")) == ["J-A"]
    assert _ids(c.get("/workshop/pending")) == ["J-A"]
    assert _ids(c.get("/workshop/jobs/by-vendor/V2")) == []


def test_hq_keeps_inconsistent_jobs(monkeypatch):
    c, _ = _client(monkeypatch, ADMIN)
    for jid in ("J-MIS", "J-GONE"):
        assert c.get("/workshop/jobs/" + jid).status_code == 200
    assert {"J-MIS", "J-GONE"} <= set(_ids(c.get("/workshop/jobs")))


def test_correct_jobs_unchanged_same_shop_and_area_manager(monkeypatch):
    c, jrepo = _client(monkeypatch, STAFF_A)
    assert c.get("/workshop/jobs/J-A").status_code == 200
    assert c.put("/workshop/jobs/J-A", json={"special_notes": "x"}).status_code == 200
    assert c.post("/workshop/jobs/J-A/start").status_code == 200
    assert c.get("/workshop/jobs/J-B").status_code == 404
    c, _ = _client(monkeypatch, AM_AB)
    assert c.get("/workshop/jobs/J-A").status_code == 200
    assert c.get("/workshop/jobs/J-B").status_code == 200
