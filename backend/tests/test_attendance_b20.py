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
