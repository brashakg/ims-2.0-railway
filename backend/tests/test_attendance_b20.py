"""Batch b20 (PR #1433): attendance doors + Footfall names.

R13  ordinary staff record their OWN attendance (check-in / check-out are not
     behind the finance-role gate); other employees' records stay manager-only.
R14  re-saving an attendance day without times keeps the stored times; only an
     explicit null clears; a change writes a before/after audit row; a
     check-out before the stored check-in is refused.
R15  POST /attendance/mark writes the store the caller names (validated), the
     employee must belong to it, and a day recorded at another store is
     refused instead of silently moved.
L2-POS-F02  GET /walkouts/walkins/status names every roster row.

Discriminating power (measured by restoring the base files): R13 cases 403 at
the role gate; R14 resave nulls both times and writes no audit row; R15 writes
store S1 and accepts the foreign store / non-member; F02 has no staff_names.
Native-Mongo evidence for the same cases: C:/ims-wt/b20-probe/probe.py.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api.routers import hr, hr_self_service, walkouts  # noqa: E402
from tests.test_hr_attendance_fix import (  # noqa: E402
    _FakeAttendanceRepo,
    _FakeAuditRepo,
    _headers,
)

FLOOR = ["OPTOMETRIST", "CASHIER", "SALES_STAFF", "WORKSHOP_STAFF"]


class _Users:
    def __init__(self, users):
        self.users = users

    def find_one(self, filt):
        return next((u for u in self.users if u["user_id"] == filt.get("user_id")), None)

    def find_many(self, filt=None, **kw):
        return list(self.users)


USERS = _Users(
    [
        {"user_id": "emp-a", "store_ids": ["S1"]},
        {"user_id": "emp-ab", "store_ids": ["S1", "S2"]},
    ]
)


def _patch_hr(monkeypatch, att, audit=None, users=USERS):
    monkeypatch.setattr(hr, "get_attendance_repository", lambda: att)
    monkeypatch.setattr(hr, "get_user_repository", lambda: users)
    monkeypatch.setattr(hr, "get_audit_repository", lambda: audit)
    monkeypatch.setattr(hr, "_store_coords", lambda sid: {"lat": None, "lng": None, "radius_m": None})
    monkeypatch.setattr(hr, "_resolve_employee_shift", lambda eid, sid: None)


@pytest.fixture
def hr_client(monkeypatch):
    def make(att, audit=None):
        _patch_hr(monkeypatch, att, audit)
        app = FastAPI()
        app.include_router(hr.router, prefix="/hr")
        app.include_router(hr_self_service.router, prefix="/hr")
        return TestClient(app)

    return make


# --------------------------------------------------------------------------- R13


@pytest.mark.parametrize("role", FLOOR)
def test_r13_floor_staff_check_in_and_out_through_the_real_app(monkeypatch, role):
    """Mounted exactly as production (main.py gates + RBAC middleware)."""
    from api.main import app

    att = _FakeAttendanceRepo([])
    _patch_hr(monkeypatch, att)
    c = TestClient(app)
    h = _headers([role], user_id=f"u-{role}", store_id="S1")
    ci = c.post("/api/v1/hr/attendance/check-in", params={"store_id": "S1"}, headers=h)
    assert ci.status_code == 200, ci.text
    co = c.post("/api/v1/hr/attendance/check-out", params={"store_id": "S1"}, headers=h)
    assert co.status_code == 200, co.text
    (row,) = att.find_many({"employee_id": f"u-{role}"})
    assert row["check_in"] and row["check_out"] and row["store_id"] == "S1"


def test_r13_staff_still_cannot_touch_other_records(monkeypatch):
    from api.main import app

    _patch_hr(monkeypatch, _FakeAttendanceRepo([]))
    c = TestClient(app)
    h = _headers(["SALES_STAFF"], user_id="u-s", store_id="S1")
    assert c.post("/api/v1/hr/attendance/mark", json={"employee_id": "emp-a", "date": "2026-11-02", "status": "ABSENT"}, headers=h).status_code == 403
    assert c.get("/api/v1/hr/attendance", headers=h).status_code == 403
    assert c.post("/api/v1/hr/attendance/x/check-out", headers=h).status_code == 403
    # and cannot clock in at a store they do not belong to
    assert c.post("/api/v1/hr/attendance/check-in", params={"store_id": "S2"}, headers=h).status_code == 403


# --------------------------------------------------------------------------- R14


def test_r14_resave_without_times_keeps_them_and_null_clears(hr_client):
    att, audit = _FakeAttendanceRepo([]), _FakeAuditRepo()
    c = hr_client(att, audit)
    base = {"employee_id": "emp-a", "date": "2026-11-03"}
    r = c.post("/hr/attendance/mark", headers=_headers(), json={**base, "status": "PRESENT", "check_in": "2026-11-03T10:00:00", "check_out": "2026-11-03T18:00:00"})
    assert r.status_code == 200, r.text
    r = c.post("/hr/attendance/mark", headers=_headers(), json={**base, "status": "PRESENT"})
    assert r.status_code == 200
    (row,) = att.find_many({})
    assert row["check_in"].startswith("2026-11-03T10:00") and row["check_out"].startswith("2026-11-03T18:00")
    assert audit.rows == []  # nothing changed -> nothing to audit

    r = c.post("/hr/attendance/mark", headers=_headers(), json={**base, "status": "HALF_DAY", "check_out": None})
    assert r.status_code == 200
    (row,) = att.find_many({})
    assert row["check_out"] is None and row["check_in"].startswith("2026-11-03T10:00")
    (a,) = audit.rows
    assert a["action"] == "ATTENDANCE_EDIT"
    assert set(a["detail"]["changed_fields"]) == {"status", "check_out"}
    assert a["before_state"]["check_out"].startswith("2026-11-03T18:00")


def test_r14_checkout_before_stored_checkin_refused(hr_client):
    att = _FakeAttendanceRepo([])
    c = hr_client(att)
    base = {"employee_id": "emp-a", "date": "2026-11-03"}
    c.post("/hr/attendance/mark", headers=_headers(), json={**base, "status": "PRESENT", "check_in": "2026-11-03T10:00:00"})
    r = c.post("/hr/attendance/mark", headers=_headers(), json={**base, "status": "PRESENT", "check_out": "2026-11-03T09:00:00"})
    assert r.status_code == 422
    assert "after check_in" in r.json()["detail"]


# --------------------------------------------------------------------------- R15


def test_r15_mark_writes_the_named_store(hr_client):
    att = _FakeAttendanceRepo([])
    c = hr_client(att)
    r = c.post("/hr/attendance/mark", headers=_headers(["ADMIN"], store_id="S1"), json={"employee_id": "emp-ab", "date": "2026-11-02", "status": "PRESENT", "store_id": "S2"})
    assert r.status_code == 200, r.text
    (row,) = att.find_many({})
    assert row["store_id"] == "S2"


def test_r15_foreign_store_and_non_member_refused(hr_client):
    att = _FakeAttendanceRepo([])
    c = hr_client(att)
    sm = _headers(["STORE_MANAGER"], store_id="S1")
    assert c.post("/hr/attendance/mark", headers=sm, json={"employee_id": "emp-ab", "date": "2026-11-02", "status": "PRESENT", "store_id": "S2"}).status_code == 403
    r = c.post("/hr/attendance/mark", headers=_headers(["ADMIN"]), json={"employee_id": "emp-a", "date": "2026-11-02", "status": "PRESENT", "store_id": "S2"})
    assert r.status_code == 422
    assert att.find_many({}) == []


def test_r15_day_recorded_at_other_store_not_moved(hr_client):
    att = _FakeAttendanceRepo([{"attendance_id": "x1", "employee_id": "emp-ab", "date": "2026-11-02", "store_id": "S1", "status": "PRESENT", "check_in": "2026-11-02T10:00:00+05:30"}])
    c = hr_client(att)
    r = c.post("/hr/attendance/mark", headers=_headers(["ADMIN"]), json={"employee_id": "emp-ab", "date": "2026-11-02", "status": "ABSENT", "store_id": "S2"})
    assert r.status_code == 409
    row = att.find_one({"attendance_id": "x1"})
    assert row["store_id"] == "S1" and row["status"] == "PRESENT"


# --------------------------------------------------------------------- L2-POS-F02


class _Coll:
    def __init__(self, docs):
        self.docs = docs

    def find(self, filt=None, projection=None):
        ids = set()
        for clause in (filt or {}).get("$or", []):
            for k, v in clause.items():
                ids |= {(k, i) for i in v["$in"]}
        return [d for d in self.docs if any((k, d.get(k)) in ids for k in ("user_id", "id"))]


class _DB:
    def __init__(self, users):
        self.users = _Coll(users)

    def get_collection(self, name):
        return self.users if name == "users" else _Coll([])


def test_f02_status_names_zero_activity_staff(monkeypatch):
    new = {"user_id": "5b0f7f0a-new", "full_name": "New Sales Person", "roles": ["SALES_STAFF"], "store_ids": ["S1"], "is_active": True}

    class _Roster:
        def find_by_store(self, store_id, active_only=True):
            return [new]

    monkeypatch.setattr(walkouts, "get_user_repository", lambda: _Roster())
    monkeypatch.setattr(walkouts, "get_walkin_counter_repository", lambda: None)
    monkeypatch.setattr(walkouts, "get_db", lambda: _DB([new]))
    app = FastAPI()
    app.include_router(walkouts.router, prefix="/walkouts")
    r = TestClient(app).get("/walkouts/walkins/status", headers=_headers(["STORE_MANAGER"], store_id="S1"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["staff_missing"] == ["5b0f7f0a-new"]
    assert body["staff_names"] == {"5b0f7f0a-new": "New Sales Person"}


# ------------------------------------------- #1433 Codex round 1 (real repository)
#
# These run the REAL AttendanceRepository over a mongomock collection that
# carries the production unique (employee_id, date) index, through the real
# app (main.py handlers, JWT, RBAC). Faults are injected on the collection the
# way Codex did on native PyMongo: AutoReconnect from insert_one / update_one /
# find_one / find, plus a "landed, acknowledgement lost" variant.
# Discriminating power (measured by restoring the round-0 head's hr.py,
# hr_self_service.py, hr_repository.py, main.py): every 503 case answered 200
# with no row / an unchanged row and a false audit row; the legacy-date today
# read was null; the no-shop day was adopted into S1.

from datetime import datetime as _dt  # noqa: E402

import mongomock  # noqa: E402
from pymongo.errors import AutoReconnect  # noqa: E402

from database.repositories.hr_repository import AttendanceRepository  # noqa: E402

DAY = "2026-11-05"


@pytest.fixture
def real(monkeypatch):
    from api.main import app

    coll = mongomock.MongoClient().db.attendance
    coll.create_index([("employee_id", 1), ("date", 1)], unique=True)
    audit = _FakeAuditRepo()
    _patch_hr(monkeypatch, AttendanceRepository(coll), audit)

    class _Db:
        def get_collection(self, name):
            return coll

    monkeypatch.setattr(hr_self_service, "_get_db", lambda: _Db())
    return TestClient(app, raise_server_exceptions=False), coll, audit


def _boom(*a, **kw):
    raise AutoReconnect("injected attendance failure")


def _lost_ack(method):
    def run(*a, **kw):
        method(*a, **kw)
        raise AutoReconnect("write landed, acknowledgement lost")

    return run


def _admin():
    return _headers(["ADMIN"], user_id="adm", store_id="S1")


GEO = {"store_id": "S1"}


def _seed(c):
    r = c.post("/api/v1/hr/attendance/mark", headers=_admin(), json={"employee_id": "emp-a", "store_id": "S1", "date": DAY, "status": "PRESENT", "check_in": DAY + "T10:00:00+05:30"})
    assert r.status_code == 200, r.text


def test_codex_failed_mark_and_put_are_503_with_no_audit(real, monkeypatch):
    c, coll, audit = real
    _seed(c)
    before = coll.find_one({"employee_id": "emp-a"})
    monkeypatch.setattr(coll, "update_one", _boom)
    r = c.post("/api/v1/hr/attendance/mark", headers=_admin(), json={"employee_id": "emp-a", "store_id": "S1", "date": DAY, "status": "ABSENT"})
    assert r.status_code == 503, r.text
    r2 = c.put(f"/api/v1/hr/attendance/{before['attendance_id']}", headers=_admin(), json={"status": "ABSENT"})
    assert r2.status_code == 503, r2.text
    assert coll.find_one({"employee_id": "emp-a"}) == before
    assert audit.rows == []  # no audit of a change that never landed


def test_codex_mark_lost_ack_is_reconciled_and_audited_once(real, monkeypatch):
    c, coll, audit = real
    _seed(c)
    upd = coll.update_one
    monkeypatch.setattr(coll, "update_one", _lost_ack(upd))
    body = {"employee_id": "emp-a", "store_id": "S1", "date": DAY, "status": "HALF_DAY"}
    assert c.post("/api/v1/hr/attendance/mark", headers=_admin(), json=body).status_code == 200
    monkeypatch.setattr(coll, "update_one", upd)
    assert c.post("/api/v1/hr/attendance/mark", headers=_admin(), json=body).status_code == 200  # identical retry
    assert coll.find_one({"employee_id": "emp-a"})["status"] == "HALF_DAY"
    assert [a["detail"]["changed_fields"] for a in audit.rows] == [["status"]]


def test_codex_self_check_in_and_out_failures_are_503(real, monkeypatch):
    c, coll, _ = real
    h = _headers(["CASHIER"], user_id="u-cash", store_id="S1")
    ins = coll.insert_one
    monkeypatch.setattr(coll, "insert_one", _boom)
    assert c.post("/api/v1/hr/attendance/check-in", params=GEO, headers=h).status_code == 503
    assert coll.count_documents({"employee_id": "u-cash"}) == 0
    # landed but unacknowledged: success, one row, and a retry keeps the time
    monkeypatch.setattr(coll, "insert_one", _lost_ack(ins))
    assert c.post("/api/v1/hr/attendance/check-in", params=GEO, headers=h).status_code == 200
    monkeypatch.setattr(coll, "insert_one", ins)
    first = coll.find_one({"employee_id": "u-cash"})
    r = c.post("/api/v1/hr/attendance/check-in", params=GEO, headers=h)
    assert r.json()["already_checked_in"] is True
    assert coll.count_documents({"employee_id": "u-cash"}) == 1
    assert coll.find_one({"employee_id": "u-cash"})["check_in"] == first["check_in"]
    upd = coll.update_one
    monkeypatch.setattr(coll, "update_one", _boom)
    assert c.post("/api/v1/hr/attendance/check-out", params=GEO, headers=h).status_code == 503
    assert c.post(f"/api/v1/hr/attendance/{first['attendance_id']}/check-out", headers=_admin()).status_code == 503
    assert coll.find_one({"employee_id": "u-cash"}).get("check_out") is None
    monkeypatch.setattr(coll, "update_one", upd)
    assert c.post("/api/v1/hr/attendance/check-out", params=GEO, headers=h).status_code == 200
    assert coll.find_one({"employee_id": "u-cash"})["check_out"]


def test_codex_own_read_failure_is_503_not_an_empty_day(real, monkeypatch):
    c, coll, _ = real
    h = _headers(["SALES_STAFF"], user_id="u-s", store_id="S1")
    assert c.post("/api/v1/hr/attendance/check-in", params=GEO, headers=h).status_code == 200
    monkeypatch.setattr(coll, "find_one", _boom)
    r = c.get("/api/v1/hr/me/attendance", headers=h)
    assert r.status_code == 503, r.text
    monkeypatch.setattr(coll, "find", _boom)
    g = c.get("/api/v1/hr/attendance/grid", params={"month": DAY[:7], "store_id": "S1"}, headers=_admin())
    assert g.status_code == 503, g.text


def test_codex_legacy_datetime_day_is_today(real):
    from api.utils.ist import now_ist

    c, coll, _ = real
    today = now_ist().date().isoformat()
    coll.insert_one({"_id": "L1", "attendance_id": "L1", "employee_id": "u-w", "store_id": "S1", "date": _dt.fromisoformat(today), "status": "PRESENT", "check_in": today + "T04:30:00+05:30"})
    h = _headers(["WORKSHOP_STAFF"], user_id="u-w", store_id="S1")
    body = c.get("/api/v1/hr/me/attendance", headers=h).json()
    assert body["today"]["check_in"] == today + "T04:30:00+05:30"
    assert body["days"][today] == "P" and body["summary"]["present"] == 1
    r = c.post("/api/v1/hr/attendance/check-in", params=GEO, headers=h)
    assert r.json()["already_checked_in"] is True
    assert coll.count_documents({"employee_id": "u-w"}) == 1


def test_d48_store_less_day_is_not_adopted_and_shows_read_only(real):
    c, coll, audit = real
    coll.insert_one({"_id": "N1", "attendance_id": "N1", "employee_id": "emp-a", "date": DAY, "status": "PRESENT"})
    sm = _headers(["STORE_MANAGER"], user_id="sm", store_id="S1")
    g = c.get("/api/v1/hr/attendance/grid", params={"month": DAY[:7], "store_id": "S1"}, headers=sm).json()
    (emp,) = [e for e in g["employees"] if e["employee_id"] == "emp-a"]
    assert emp["no_shop_days"] == ["5"] and "5" not in emp["days"]
    body = {"employee_id": "emp-a", "store_id": "S1", "date": DAY, "status": "ABSENT"}
    r = c.post("/api/v1/hr/attendance/mark", headers=sm, json=body)
    assert r.status_code == 409 and "Shop not recorded" in r.json()["detail"]
    assert coll.find_one({"_id": "N1"}).get("store_id") is None and audit.rows == []
    r = c.post("/api/v1/hr/attendance/mark", headers=sm, json={**body, "assign_store_id": "S1"})
    assert r.status_code == 200, r.text
    assert coll.find_one({"_id": "N1"})["store_id"] == "S1"


# --------------------------------------------------------------------------- no repository
# #1433 internal review: in production an unreachable Mongo at boot leaves the
# seeded connection disconnected, so get_attendance_repository() is None. Every
# attendance writer used to skip the write and still answer 200 "recorded",
# and the own read answered an empty day ("You haven't checked in today").


@pytest.fixture
def prod_db_down(monkeypatch):
    """The real production path: Railway marker set, real Mongo disconnected,
    the real get_attendance_repository / _get_db (no lambda override)."""
    from api.main import app
    from database import connection

    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
    monkeypatch.setattr(connection.db, "_connected", False)
    monkeypatch.setattr(connection.db, "connect", lambda *a, **kw: False)
    assert hr.get_attendance_repository() is None
    assert hr_self_service._get_db() is None
    return TestClient(app, raise_server_exceptions=False)


def test_no_repository_writers_and_own_read_are_503_not_recorded(prod_db_down):
    c = prod_db_down
    staff = _headers(["SALES_STAFF"], user_id="u-s", store_id="S1")
    calls = [
        c.post("/api/v1/hr/attendance/mark", headers=_admin(), json={"employee_id": "emp-a", "store_id": "S1", "date": DAY, "status": "ABSENT"}),
        c.post("/api/v1/hr/attendance/check-in", params=GEO, headers=staff),
        c.post("/api/v1/hr/attendance/check-out", params=GEO, headers=staff),
        c.post("/api/v1/hr/attendance/att-1/check-out", headers=_admin()),
        c.put("/api/v1/hr/attendance/att-1", headers=_admin(), json={"status": "ABSENT"}),
        c.get("/api/v1/hr/me/attendance", headers=staff),
    ]
    assert [r.status_code for r in calls] == [503] * len(calls), [r.text for r in calls]
    assert not any("recorded" in r.text or "marked" in r.text for r in calls)
