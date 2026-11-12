"""PR #1376 round 2 (Codex 6 Nov): every door that lists or counts workshop jobs
applies THE job rule -- the job's shop AND its order's shop (helpers._job_in_reach)
-- and applies it BEFORE paging or counting.

P2-1 the station queue read the raw saved shop, so a legacy job stamped shop A on
shop B's order (404 on detail) still showed its customer in A's queue.
P2-2 the vendor queue paged first and filtered after: the hidden row used up the
page, so a caller got jobs=[] total=0 with real work on the next page.

Real WorkshopJobRepository / OrderRepository over the strict Mongo double, so the
filters, sort, skip/limit and $nin here are the production code path."""
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
from api.routers.reports import workshop as rw  # noqa: E402
from database.repositories.order_repository import OrderRepository  # noqa: E402
from database.repositories.workshop_repository import WorkshopJobRepository  # noqa: E402
from strict_fakes import StrictDB  # noqa: E402

ORDERS = [
    {"order_id": "ORD-A", "store_id": "SHOP-A"},
    {"order_id": "ORD-A2", "store_id": "SHOP-A"},
    {"order_id": "ORD-B", "store_id": "SHOP-B"},
    {"order_id": "ORD-NOSHOP"},
]


def _job(jid, order, expected, store="SHOP-A", status="PENDING", **extra):
    doc = {
        "job_id": jid,
        "job_number": "WS-" + jid,
        "order_id": order,
        "store_id": store,
        "vendor_id": "V1",
        "technician_id": "T-" + jid,
        "customer_name": "PATIENT " + jid,
        "status": status,
        "expected_date": expected,
        "created_at": expected + "T00:00:00",
        "current_station": "EDGING",
        "station_timestamps": {"EDGING": expected + "T00:00:00"},
        "fitting_details": {"confirmed_by_sales": True},
    }
    doc.update(extra)
    return doc


# The forbidden rows sort FIRST, so they would use up any page taken before the
# rule runs. J-A / J-A2 share a date: only a tiebreak keeps their pages stable.
JOBS = [
    _job("J-MIS", "ORD-B", "2020-01-01"),  # stamped A, B's order
    _job("J-GONE", "ORD-GONE", "2020-01-02"),  # order no longer resolves
    _job("J-NOORD", None, "2020-01-03"),  # never had an order
    _job("J-NOSHOP", "ORD-NOSHOP", "2020-01-04"),  # order without a shop
    _job("J-A", "ORD-A", "2020-02-01"),
    _job("J-A2", "ORD-A2", "2020-02-01"),
    _job("J-B", "ORD-B", "2020-01-01", store="SHOP-B"),
]
FORBIDDEN = {"J-MIS", "J-GONE", "J-NOORD", "J-NOSHOP"}


def _user(roles, active, stores=None):
    return {
        "user_id": "u1",
        "roles": roles,
        "active_store_id": active,
        "store_ids": stores if stores is not None else ([active] if active else []),
    }


STAFF_A = _user(["WORKSHOP_STAFF"], "SHOP-A")
MANAGER_A = _user(["STORE_MANAGER"], "SHOP-A")
AM_AB = _user(["AREA_MANAGER"], "SHOP-A", ["SHOP-A", "SHOP-B"])
ADMIN = _user(["ADMIN"], "HQ")


def _client(monkeypatch, user, jobs=JOBS):
    db = StrictDB()
    db.seed("workshop_jobs", [dict(j) for j in jobs])
    db.seed("orders", [dict(o) for o in ORDERS])
    jrepo = WorkshopJobRepository(db.get_collection("workshop_jobs"))
    orepo = OrderRepository(db.get_collection("orders"))
    monkeypatch.setattr(wm, "get_workshop_repository", lambda: jrepo)
    monkeypatch.setattr(wm, "get_order_repository", lambda: orepo)
    monkeypatch.setattr(wm, "get_db", lambda: db)
    monkeypatch.setattr(deps, "get_db", lambda: db)
    monkeypatch.setattr(rw, "get_db", lambda: db)
    app = FastAPI()
    app.include_router(wm.router, prefix="/workshop")
    app.include_router(rw.router, prefix="/reports")

    async def _u():
        return user

    app.dependency_overrides[get_current_user] = _u
    return TestClient(app)


def _ok(resp):
    assert resp.status_code == 200, resp.text
    return resp.json()


def _queue(client, store="SHOP-A"):
    body = _ok(client.get("/workshop/stations/EDGING/queue", params={"store_id": store}))
    assert body["total"] == len(body["jobs"])
    return body


def _page(client, url, **params):
    body = _ok(client.get(url, params=params))
    return [j["id"] for j in body["jobs"]], body["total"]


# --- P2-1: the station queue --------------------------------------------------


def test_station_queue_hides_every_inconsistent_job_from_shop_staff(monkeypatch):
    body = _queue(_client(monkeypatch, STAFF_A))
    assert sorted(r["job_id"] for r in body["jobs"]) == ["J-A", "J-A2"]
    assert "PATIENT J-MIS" not in str(body)


def test_station_queue_store_manager_same_shop_keeps_own_jobs(monkeypatch):
    body = _queue(_client(monkeypatch, MANAGER_A))
    assert sorted(r["job_id"] for r in body["jobs"]) == ["J-A", "J-A2"]


def test_station_queue_area_manager_of_both_shops_keeps_the_mismatch(monkeypatch):
    body = _queue(_client(monkeypatch, AM_AB))
    assert sorted(r["job_id"] for r in body["jobs"]) == ["J-A", "J-A2", "J-MIS"]


def test_station_queue_hq_keeps_everything_with_row_shape(monkeypatch):
    body = _queue(_client(monkeypatch, ADMIN))
    assert {r["job_id"] for r in body["jobs"]} == FORBIDDEN | {"J-A", "J-A2"}
    row = next(r for r in body["jobs"] if r["job_id"] == "J-MIS")
    assert row["customer_name"] == "PATIENT J-MIS"
    assert row["current_station"] == "EDGING" and row["sla_chip"]


def test_station_queue_shows_nothing_when_orders_cannot_be_read(monkeypatch):
    client = _client(monkeypatch, STAFF_A)
    monkeypatch.setattr(wm, "get_order_repository", lambda: None)
    assert _queue(client)["jobs"] == []


# --- P2-2: paged lists --------------------------------------------------------

VENDOR = "/workshop/jobs/by-vendor/V1"


def test_vendor_first_page_holds_the_first_reachable_job(monkeypatch):
    c = _client(monkeypatch, STAFF_A)
    assert _page(c, VENDOR, limit=1, skip=0) == (["J-A"], 2)
    assert _page(c, VENDOR, limit=1, skip=1) == (["J-A2"], 2)
    assert _page(c, VENDOR, limit=1, skip=2) == ([], 2)
    assert _page(c, VENDOR, limit=100, skip=0) == (["J-A", "J-A2"], 2)


def test_vendor_pages_for_area_manager_and_hq(monkeypatch):
    c = _client(monkeypatch, AM_AB)
    ids, total = _page(c, VENDOR, limit=2, skip=0)
    assert sorted(ids) == ["J-B", "J-MIS"] and total == 4
    assert _page(c, VENDOR, limit=2, skip=2) == (["J-A", "J-A2"], 4)
    c = _client(monkeypatch, ADMIN)
    ids, total = _page(c, VENDOR, limit=1, skip=0)
    assert len(ids) == 1 and total == 7
    seen = [_page(c, VENDOR, limit=1, skip=n)[0][0] for n in range(7)]
    assert sorted(seen) == sorted(j["job_id"] for j in JOBS)


def test_shop_job_list_pages_over_reachable_jobs(monkeypatch):
    # /jobs sorts newest-first, so here the forbidden rows take the newest
    # dates to use up the page.
    jobs = [
        _job("J-MIS", "ORD-B", "2021-01-01"),
        _job("J-GONE", "ORD-GONE", "2021-01-02"),
        _job("J-A", "ORD-A", "2020-02-01"),
        _job("J-A2", "ORD-A2", "2020-02-01"),
    ]
    c = _client(monkeypatch, STAFF_A, jobs)
    assert _page(c, "/workshop/jobs", limit=1, skip=0) == (["J-A"], 2)
    assert _page(c, "/workshop/jobs", limit=1, skip=1) == (["J-A2"], 2)
    assert _page(c, "/workshop/jobs", limit=50, skip=0) == (["J-A", "J-A2"], 2)


@pytest.mark.parametrize("door", ["pending", "overdue"])
def test_board_cap_counts_reachable_jobs_only(monkeypatch, door):
    """The 100-row cap of the board lists must not be used up by hidden rows."""
    jobs = [_job("M%03d" % i, "ORD-B", "2020-01-01") for i in range(100)]
    jobs.append(_job("J-A", "ORD-A", "2020-02-01"))
    c = _client(monkeypatch, STAFF_A, jobs)
    assert _page(c, "/workshop/" + door) == (["J-A"], 1)
    c = _client(monkeypatch, ADMIN, jobs)
    assert _page(c, "/workshop/" + door)[1] == 100


def test_board_ready_cap_counts_reachable_jobs_only(monkeypatch):
    jobs = [
        _job("M%03d" % i, "ORD-B", "2020-01-01", status="READY", completed_at="2020-03-01")
        for i in range(100)
    ]
    jobs.append(_job("J-A", "ORD-A", "2020-02-01", status="READY", completed_at="2020-02-01"))
    assert _page(_client(monkeypatch, STAFF_A, jobs), "/workshop/ready") == (["J-A"], 1)


# --- the same rule on every count --------------------------------------------


def test_dashboard_kpis_count_reachable_jobs(monkeypatch):
    kpis = _ok(_client(monkeypatch, STAFF_A).get("/workshop/dashboard-kpis"))
    assert kpis["pending"] == 2
    kpis = _ok(_client(monkeypatch, AM_AB).get("/workshop/dashboard-kpis"))
    assert kpis["pending"] == 3  # J-A, J-A2, J-MIS (the active shop's jobs)
    kpis = _ok(
        _client(monkeypatch, ADMIN).get(
            "/workshop/dashboard-kpis", params={"store_id": "SHOP-A"}
        )
    )
    assert kpis["pending"] == 6


def test_technician_workload_counts_reachable_jobs(monkeypatch):
    def techs(user):
        rows = _ok(_client(monkeypatch, user).get("/workshop/technician-workload"))
        return sorted(r["_id"] for r in rows["workload"])

    assert techs(STAFF_A) == ["T-J-A", "T-J-A2"]
    assert techs(AM_AB) == ["T-J-A", "T-J-A2", "T-J-MIS"]
    assert len(techs(ADMIN)) == 7


def test_spoilage_counts_reachable_jobs(monkeypatch):
    remake = {
        "remake_count": 1,
        "remake_reasons": [
            {"reason_code": "WRONG_POWER", "at": "2099-01-01T00:00:00", "cost_paise": 100}
        ],
    }
    jobs = [
        _job("J-MIS", "ORD-B", "2099-01-01", **remake),
        _job("J-A", "ORD-A", "2099-01-01", **remake),
    ]
    body = _ok(_client(monkeypatch, MANAGER_A, jobs).get("/workshop/spoilage-analytics"))
    assert body["total_jobs"] == 1, body


def test_pending_jobs_report_lists_reachable_jobs_only(monkeypatch):
    body = _ok(_client(monkeypatch, STAFF_A).get("/reports/workshop/pending-jobs"))
    assert sorted(r["job_id"] for r in body["data"]) == ["J-A", "J-A2"]
    assert body["summary"]["total_pending"] == 2
    body = _ok(_client(monkeypatch, ADMIN).get("/reports/workshop/pending-jobs"))
    assert body["summary"]["total_pending"] == 7


def test_productivity_report_counts_reachable_jobs(monkeypatch):
    done = {"status": "DELIVERED", "completed_at": "2026-01-02T00:00:00"}
    jobs = [
        _job("J-MIS", "ORD-B", "2026-01-01", **done),
        _job("J-A", "ORD-A", "2026-01-01", **done),
    ]
    window = {"from_date": "2026-01-01", "to_date": "2026-01-31"}
    body = _ok(
        _client(monkeypatch, MANAGER_A, jobs).get(
            "/reports/workshop/productivity", params=window
        )
    )
    assert body["totals"]["jobs_completed"] == 1
    body = _ok(
        _client(monkeypatch, ADMIN, jobs).get(
            "/reports/workshop/productivity", params={**window, "store_id": "SHOP-A"}
        )
    )
    assert body["totals"]["jobs_completed"] == 2
