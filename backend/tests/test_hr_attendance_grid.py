"""
IMS 2.0 - HR monthly attendance grid
====================================
Unit tests for the pure date/grid/mapping helpers, plus endpoint smoke tests
(FastAPI TestClient + monkeypatched fake repos) for GET /hr/attendance/grid:
grid shape, day count for a known month, store scoping, and that a valid staff
token (with an allowed role) is required. No live DB.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

import jwt  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import hr  # noqa: E402

SECRET = os.environ["JWT_SECRET_KEY"]


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_days_in_month_basic():
    assert hr._days_in_month(2026, 1) == 31
    assert hr._days_in_month(2026, 4) == 30
    assert hr._days_in_month(2026, 12) == 31


def test_days_in_month_feb_leap_and_common():
    assert hr._days_in_month(2024, 2) == 29  # leap year
    assert hr._days_in_month(2026, 2) == 28  # common year
    assert hr._days_in_month(2000, 2) == 29  # divisible by 400
    assert hr._days_in_month(1900, 2) == 28  # divisible by 100 but not 400


def test_parse_month_valid():
    assert hr._parse_month("2026-05") == (2026, 5)
    assert hr._parse_month("2024-02") == (2024, 2)


def test_parse_month_falls_back_to_current():
    today = datetime.today()
    for bad in [None, "", "garbage", "2026-13", "2026-00", "not-a-month", "2026"]:
        y, m = hr._parse_month(bad)
        assert (y, m) == (today.year, today.month)


def test_status_to_code_mapping():
    assert hr._status_to_code("PRESENT") == "P"
    assert hr._status_to_code("ABSENT") == "A"
    assert hr._status_to_code("LEAVE") == "L"
    assert hr._status_to_code("HALF_DAY") == "HD"
    assert hr._status_to_code("HOLIDAY") == "WO"
    assert hr._status_to_code("WEEK_OFF") == "WO"
    assert hr._status_to_code("UNPAID") == "LWP"
    assert hr._status_to_code("LWP") == "LWP"
    # case / whitespace insensitive
    assert hr._status_to_code("  present ") == "P"
    # unknown / empty -> placeholder
    assert hr._status_to_code("WHATEVER") == "-"
    assert hr._status_to_code(None) == "-"
    assert hr._status_to_code("") == "-"


def test_day_of_record_handles_string_and_datetime():
    assert hr._day_of_record("2026-05-07") == 7
    assert hr._day_of_record("2026-05-07T09:30:00") == 7
    assert hr._day_of_record(datetime(2026, 5, 18, 9, 0)) == 18
    assert hr._day_of_record(None) is None
    assert hr._day_of_record("nonsense") is None


def test_build_grid_shape_and_counts():
    employees = [
        {"employee_id": "E1", "name": "Asha", "store_id": "S1"},
        {"employee_id": "E2", "name": "Bina", "store_id": "S1"},
    ]
    records = [
        {"employee_id": "E1", "date": "2026-05-01", "status": "PRESENT"},
        {"employee_id": "E1", "date": "2026-05-02", "status": "ABSENT"},
        {"employee_id": "E1", "date": "2026-05-03", "status": "LEAVE"},
        {"employee_id": "E1", "date": "2026-05-04", "status": "HALF_DAY"},
        {"employee_id": "E1", "date": "2026-05-05", "status": "PRESENT", "is_late": True},
        # E2 has a week-off and an out-of-range day that must be ignored.
        {"employee_id": "E2", "date": "2026-05-04", "status": "HOLIDAY"},
        {"employee_id": "E2", "date": "2026-05-99", "status": "PRESENT"},
        # Orphan record for an employee not on the roster -> not surfaced as a row
        {"employee_id": "GHOST", "date": "2026-05-01", "status": "PRESENT"},
    ]
    grid = hr._build_grid(2026, 5, employees, records)

    assert grid["month"] == "2026-05"
    assert grid["days"] == list(range(1, 32))  # May has 31 days
    assert len(grid["employees"]) == 2  # only roster members, GHOST excluded

    e1 = next(e for e in grid["employees"] if e["employee_id"] == "E1")
    assert e1["days"]["1"] == "P"
    assert e1["days"]["2"] == "A"
    assert e1["days"]["3"] == "L"
    assert e1["days"]["4"] == "HD"
    assert e1["days"]["5"] == "P"
    assert e1["summary"]["present"] == 2
    assert e1["summary"]["absent"] == 1
    assert e1["summary"]["leave"] == 1
    assert e1["summary"]["half_day"] == 1
    assert e1["summary"]["late"] == 1

    e2 = next(e for e in grid["employees"] if e["employee_id"] == "E2")
    assert e2["days"].get("4") == "WO"
    assert "99" not in e2["days"]  # out-of-range dropped
    assert e2["summary"]["week_off"] == 1

    # Totals aggregate across employees.
    assert grid["totals"]["present"] == 2
    assert grid["totals"]["absent"] == 1
    assert grid["totals"]["week_off"] == 1
    assert grid["totals"]["late"] == 1


def test_build_grid_empty_roster_still_valid():
    grid = hr._build_grid(2024, 2, [], [])
    assert grid["month"] == "2024-02"
    assert len(grid["days"]) == 29  # leap Feb
    assert grid["employees"] == []
    assert grid["totals"] == hr._empty_summary()


def test_roster_from_users_normalises_and_sorts():
    users = [
        {"user_id": "U2", "full_name": "Zara", "store_ids": ["S1"]},
        {"user_id": "U1", "full_name": "Amit", "store_ids": ["S1", "S2"]},
        {"_id": "U3", "username": "noname", "store_ids": []},
        {"full_name": "skipme"},  # no id -> dropped
    ]
    roster = hr._roster_from_users(users, "S1")
    assert [r["employee_id"] for r in roster] == ["U1", "U3", "U2"]  # sorted by name
    assert roster[0]["name"] == "Amit"
    assert roster[0]["store_id"] == "S1"  # pinned to requested store


# ---------------------------------------------------------------------------
# Endpoint smoke tests
# ---------------------------------------------------------------------------


class _FakeUserRepo:
    def __init__(self, users):
        self._users = users

    def find_many(self, filter=None, sort=None, skip=0, limit=100):
        store = (filter or {}).get("store_ids")
        if store is None:
            return list(self._users)
        return [u for u in self._users if store in (u.get("store_ids") or [])]


class _FakeAttendanceRepo:
    def __init__(self, records):
        self._records = records

    def find_many(self, filter=None, sort=None, skip=0, limit=100):
        store = (filter or {}).get("store_id")
        if store is None:
            return list(self._records)
        return [r for r in self._records if r.get("store_id") == store]


_USERS = [
    {"user_id": "E1", "full_name": "Asha", "store_ids": ["S1"], "is_active": True},
    {"user_id": "E2", "full_name": "Bina", "store_ids": ["S1"], "is_active": True},
    {"user_id": "E3", "full_name": "Other", "store_ids": ["S2"], "is_active": True},
]
_RECORDS = [
    {"employee_id": "E1", "date": "2026-05-01", "status": "PRESENT", "store_id": "S1"},
    {"employee_id": "E1", "date": "2026-05-02", "status": "ABSENT", "store_id": "S1"},
    {"employee_id": "E3", "date": "2026-05-01", "status": "PRESENT", "store_id": "S2"},
]


def _client(monkeypatch):
    app = FastAPI()
    app.include_router(hr.router, prefix="/hr")
    monkeypatch.setattr(hr, "get_user_repository", lambda: _FakeUserRepo(_USERS))
    monkeypatch.setattr(
        hr, "get_attendance_repository", lambda: _FakeAttendanceRepo(_RECORDS)
    )
    return TestClient(app)


def _token(roles, store_ids=None, active_store="S1"):
    return jwt.encode(
        {
            "sub": "u1",
            "roles": roles,
            "store_ids": store_ids if store_ids is not None else ["S1"],
            "active_store_id": active_store,
            "exp": datetime.utcnow() + timedelta(hours=1),
        },
        SECRET,
        algorithm="HS256",
    )


def test_grid_requires_authentication(monkeypatch):
    c = _client(monkeypatch)
    assert c.get("/hr/attendance/grid", params={"month": "2026-05"}).status_code == 401


def test_grid_rejects_disallowed_role(monkeypatch):
    c = _client(monkeypatch)
    tok = _token(["SALES_STAFF"])  # not in _HR_READ_ROLES
    r = c.get(
        "/hr/attendance/grid",
        params={"month": "2026-05"},
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 403


def test_grid_shape_and_day_count_for_known_month(monkeypatch):
    c = _client(monkeypatch)
    tok = _token(["ADMIN"], active_store="S1")
    r = c.get(
        "/hr/attendance/grid",
        params={"month": "2026-05", "store_id": "S1"},
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["month"] == "2026-05"
    assert body["days"] == list(range(1, 32))  # May -> 31 days
    assert {"month", "days", "employees", "totals"} <= set(body.keys())
    # S1 roster only (E1, E2); E3 is in S2.
    ids = {e["employee_id"] for e in body["employees"]}
    assert ids == {"E1", "E2"}
    e1 = next(e for e in body["employees"] if e["employee_id"] == "E1")
    assert e1["days"]["1"] == "P" and e1["days"]["2"] == "A"
    assert e1["summary"]["present"] == 1 and e1["summary"]["absent"] == 1
    assert body["totals"]["present"] == 1 and body["totals"]["absent"] == 1


def test_grid_store_scoping_blocks_cross_store_for_non_hq(monkeypatch):
    c = _client(monkeypatch)
    # STORE_MANAGER pinned to S1 must not read S2.
    tok = _token(["STORE_MANAGER"], store_ids=["S1"], active_store="S1")
    r = c.get(
        "/hr/attendance/grid",
        params={"month": "2026-05", "store_id": "S2"},
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 403


def test_grid_non_hq_scoped_to_own_store(monkeypatch):
    c = _client(monkeypatch)
    tok = _token(["STORE_MANAGER"], store_ids=["S1"], active_store="S1")
    r = c.get(
        "/hr/attendance/grid",
        params={"month": "2026-05", "store_id": "S1"},
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 200
    ids = {e["employee_id"] for e in r.json()["employees"]}
    assert ids == {"E1", "E2"}


def test_grid_failsoft_when_no_repos(monkeypatch):
    app = FastAPI()
    app.include_router(hr.router, prefix="/hr")
    monkeypatch.setattr(hr, "get_user_repository", lambda: None)
    monkeypatch.setattr(hr, "get_attendance_repository", lambda: None)
    c = TestClient(app)
    tok = _token(["ADMIN"])
    r = c.get(
        "/hr/attendance/grid",
        params={"month": "2024-02", "store_id": "S1"},
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body["days"]) == 29  # leap Feb, computed without a DB
    assert body["employees"] == []
    assert body["totals"]["present"] == 0
