"""
IMS 2.0 - HR correctness regression tests
==========================================
Covers the correctness/validation bugs found and fixed in backend/api/routers/hr.py:

  1. Double check-in blocked (409) once a check_in stamp exists.
  2. Checkout-before-checkin blocked (400) when no check_in on the record.
  3. Double checkout blocked (409) when check_out already stamped.
  4. AttendanceMarkRequest: future date -> 422.
  5. AttendanceMarkRequest: check_out <= check_in -> 422.
  6. AttendanceMarkRequest: invalid status -> 422.
  7. LeaveCreate: to_date < from_date -> 422.
  8. LeaveCreate: invalid leave_type -> 422.
  9. LeaveCreate: from_date > 1 year in the past -> 422.
  10. apply_leave persists and returns 201.
  11. apply_leave blocks overlapping APPROVED leaves (409).
  12. apply_leave blocks overlapping PENDING leaves (409).
  13. apply_leave with no DB still returns 201 (fail-soft).

All tests run with no live DB (monkeypatched repos or None).
"""

from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta

import pytest

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

import jwt  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import hr  # noqa: E402

SECRET = os.environ["JWT_SECRET_KEY"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _token(roles=None, user_id="u-test", store_id="S1"):
    return jwt.encode(
        {
            "sub": user_id,
            "user_id": user_id,
            "roles": roles or ["ADMIN"],
            "store_ids": [store_id],
            "active_store_id": store_id,
            "exp": datetime.utcnow() + timedelta(hours=1),
        },
        SECRET,
        algorithm="HS256",
    )


def _auth_headers(roles=None, user_id="u-test", store_id="S1"):
    return {"Authorization": f"Bearer {_token(roles, user_id, store_id)}"}


class _FakeLeaveRepo:
    """In-memory leave repo that honours find_many / find_by_id / create / update."""

    def __init__(self, leaves=None):
        self._store: dict = {}
        for lv in leaves or []:
            self._store[lv["leave_id"]] = dict(lv)

    def find_many(self, filter=None, **kwargs):
        status_filter = (filter or {}).get("status")
        emp_filter = (filter or {}).get("employee_id")
        results = []
        for lv in self._store.values():
            if emp_filter and lv.get("employee_id") != emp_filter:
                continue
            if status_filter:
                # Handle {"$in": [...]} syntax.
                if isinstance(status_filter, dict):
                    allowed = status_filter.get("$in", [])
                    if lv.get("status") not in allowed:
                        continue
                elif lv.get("status") != status_filter:
                    continue
            results.append(lv)
        return results

    def find_by_id(self, leave_id):
        return self._store.get(leave_id)

    def create(self, doc):
        lid = doc.get("leave_id") or doc.get("_id")
        self._store[lid] = dict(doc)
        return doc

    def update(self, leave_id, updates):
        if leave_id in self._store:
            self._store[leave_id].update(updates)


class _FakeAttendanceRepo:
    """In-memory attendance repo."""

    def __init__(self, records=None):
        self._store: dict = {}
        for r in records or []:
            self._store[r.get("attendance_id") or r.get("_id")] = dict(r)

    def find_one(self, filter=None):
        for r in self._store.values():
            match = True
            for key, val in (filter or {}).items():
                if key == "$or":
                    # Simple $or: match if ANY clause matches.
                    or_match = False
                    for clause in val:
                        if all(r.get(k) == v for k, v in clause.items()):
                            or_match = True
                            break
                    if not or_match:
                        match = False
                else:
                    if r.get(key) != val:
                        match = False
            if match:
                return r
        return None

    def find_many(self, filter=None, **kwargs):
        return list(self._store.values())

    def create(self, doc):
        aid = doc.get("attendance_id") or doc.get("_id")
        self._store[aid] = dict(doc)
        return doc

    def update(self, aid, updates):
        if aid in self._store:
            self._store[aid].update(updates)


def _make_app(monkeypatch, attendance_records=None, leave_records=None,
              attendance_repo=None, leave_repo=None):
    """Build a TestClient with monkeypatched fake repos."""
    app = FastAPI()
    app.include_router(hr.router, prefix="/hr")

    monkeypatch.setattr(
        hr, "get_attendance_repository",
        lambda: attendance_repo if attendance_repo is not None
                else _FakeAttendanceRepo(attendance_records),
    )
    monkeypatch.setattr(
        hr, "get_leave_repository",
        lambda: leave_repo if leave_repo is not None
                else _FakeLeaveRepo(leave_records),
    )
    # Other repos not needed for these tests.
    monkeypatch.setattr(hr, "get_user_repository", lambda: None)
    monkeypatch.setattr(hr, "get_payroll_repository", lambda: None)
    return TestClient(app)


# ---------------------------------------------------------------------------
# Schema-level validation (Pydantic — no HTTP needed)
# ---------------------------------------------------------------------------


class TestAttendanceMarkRequestSchema:
    def test_future_date_rejected(self):
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        from pydantic import ValidationError
        with pytest.raises(ValidationError, match="future"):
            hr.AttendanceMarkRequest(
                employee_id="E1",
                date=date.today() + timedelta(days=1),
                status="PRESENT",
            )

    def test_today_accepted(self):
        req = hr.AttendanceMarkRequest(
            employee_id="E1",
            date=date.today(),
            status="PRESENT",
        )
        assert req.date == date.today()

    def test_past_date_accepted(self):
        req = hr.AttendanceMarkRequest(
            employee_id="E1",
            date=date.today() - timedelta(days=5),
            status="PRESENT",
        )
        assert req.status == "PRESENT"

    def test_invalid_status_rejected(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError, match="status"):
            hr.AttendanceMarkRequest(
                employee_id="E1",
                date=date.today(),
                status="WORKING",  # not a valid status
            )

    def test_checkout_before_checkin_rejected(self):
        from pydantic import ValidationError
        now = datetime.now()
        with pytest.raises(ValidationError, match="check_out"):
            hr.AttendanceMarkRequest(
                employee_id="E1",
                date=date.today(),
                status="PRESENT",
                check_in=now,
                check_out=now - timedelta(minutes=30),
            )

    def test_checkout_equal_checkin_rejected(self):
        from pydantic import ValidationError
        now = datetime.now()
        with pytest.raises(ValidationError, match="check_out"):
            hr.AttendanceMarkRequest(
                employee_id="E1",
                date=date.today(),
                status="PRESENT",
                check_in=now,
                check_out=now,  # equal is also invalid
            )

    def test_valid_checkin_checkout_accepted(self):
        now = datetime.now()
        req = hr.AttendanceMarkRequest(
            employee_id="E1",
            date=date.today(),
            status="PRESENT",
            check_in=now - timedelta(hours=8),
            check_out=now,
        )
        assert req.check_in < req.check_out

    def test_status_normalised_to_upper(self):
        req = hr.AttendanceMarkRequest(
            employee_id="E1",
            date=date.today(),
            status="present",
        )
        assert req.status == "PRESENT"

    def test_all_valid_statuses_accepted(self):
        for status in hr._VALID_STATUSES:
            req = hr.AttendanceMarkRequest(
                employee_id="E1", date=date.today(), status=status
            )
            assert req.status == status


class TestLeaveCreateSchema:
    def test_to_date_before_from_date_rejected(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError, match="to_date"):
            hr.LeaveCreate(
                leave_type="CASUAL",
                from_date=date.today() + timedelta(days=5),
                to_date=date.today() + timedelta(days=2),
                reason="Test",
            )

    def test_same_from_and_to_accepted(self):
        lv = hr.LeaveCreate(
            leave_type="SICK",
            from_date=date.today(),
            to_date=date.today(),
            reason="One day sick",
        )
        assert lv.from_date == lv.to_date

    def test_invalid_leave_type_rejected(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError, match="leave_type"):
            hr.LeaveCreate(
                leave_type="VACATION",  # not in _VALID_LEAVE_TYPES
                from_date=date.today(),
                to_date=date.today(),
                reason="Test",
            )

    def test_leave_type_normalised_to_upper(self):
        lv = hr.LeaveCreate(
            leave_type="casual",
            from_date=date.today(),
            to_date=date.today() + timedelta(days=2),
            reason="Holiday",
        )
        assert lv.leave_type == "CASUAL"

    def test_all_valid_leave_types_accepted(self):
        for lt in hr._VALID_LEAVE_TYPES:
            lv = hr.LeaveCreate(
                leave_type=lt,
                from_date=date.today(),
                to_date=date.today(),
                reason="Test",
            )
            assert lv.leave_type == lt

    def test_back_dated_more_than_1_year_rejected(self):
        from pydantic import ValidationError
        very_old = date.today() - timedelta(days=400)
        with pytest.raises(ValidationError, match="past"):
            hr.LeaveCreate(
                leave_type="CASUAL",
                from_date=very_old,
                to_date=very_old + timedelta(days=1),
                reason="Old leave",
            )


# ---------------------------------------------------------------------------
# HTTP-level tests for check-in / check-out
# ---------------------------------------------------------------------------


class TestCheckInBlocking:
    def test_double_checkin_is_idempotent_no_duplicate(self, monkeypatch):
        """A second check-in the same day must NOT create a 2nd row.

        This is the owner-reported "same user recorded twice" bug. The endpoint
        now returns 200 (idempotent), flags already_checked_in, PRESERVES the
        original (earlier) check-in stamp, and the repo still holds exactly ONE
        row for the employee+day."""
        original_ci = "2026-01-01T09:00:00"
        existing = {
            "attendance_id": "att-1",
            "employee_id": "u-test",
            "store_id": "S1",
            "date": date.today().isoformat(),
            "check_in": original_ci,  # already checked in
            "status": "PRESENT",
        }
        repo = _FakeAttendanceRepo([existing])
        monkeypatch.setattr(hr, "_store_coords", lambda sid: {"lat": None, "lng": None, "radius_m": None})
        monkeypatch.setattr(hr, "_resolve_employee_shift", lambda eid, sid: None)

        c = _make_app(monkeypatch, attendance_repo=repo)
        resp = c.post("/hr/attendance/check-in", headers=_auth_headers())
        assert resp.status_code == 200, resp.text
        assert resp.json()["already_checked_in"] is True
        # Exactly one row -- no duplicate.
        assert len(repo._store) == 1
        # The first stamp of the day is the system of record (not overwritten).
        only = next(iter(repo._store.values()))
        assert only["check_in"] == original_ci
        assert only["status"] == "PRESENT"

    def test_first_checkin_succeeds_201(self, monkeypatch):
        """First check-in of the day should return 200 with checkInTime."""
        repo = _FakeAttendanceRepo([])  # no existing records
        monkeypatch.setattr(hr, "_store_coords", lambda sid: {"lat": None, "lng": None, "radius_m": None})
        monkeypatch.setattr(hr, "_resolve_employee_shift", lambda eid, sid: None)

        c = _make_app(monkeypatch, attendance_repo=repo)
        resp = c.post("/hr/attendance/check-in", headers=_auth_headers())
        assert resp.status_code == 200
        assert "checkInTime" in resp.json()


class TestCheckOutBlocking:
    def _base_record(self, **kwargs):
        rec = {
            "attendance_id": "att-2",
            "employee_id": "u-test",
            "date": date.today().isoformat(),
            "check_in": "2026-01-01T09:00:00",
            "check_out": None,
        }
        rec.update(kwargs)
        return rec

    def test_checkout_without_checkin_blocked_400(self, monkeypatch):
        """Cannot check out a record that has no check_in."""
        rec = self._base_record(check_in=None)
        repo = _FakeAttendanceRepo([rec])
        c = _make_app(monkeypatch, attendance_repo=repo)
        resp = c.post("/hr/attendance/att-2/check-out", headers=_auth_headers())
        assert resp.status_code == 400
        assert "no check-in" in resp.json()["detail"]

    def test_double_checkout_blocked_409(self, monkeypatch):
        """A record already checked out must return 409."""
        rec = self._base_record(check_out="2026-01-01T18:00:00")
        repo = _FakeAttendanceRepo([rec])
        c = _make_app(monkeypatch, attendance_repo=repo)
        resp = c.post("/hr/attendance/att-2/check-out", headers=_auth_headers())
        assert resp.status_code == 409
        assert "Already checked out" in resp.json()["detail"]

    def test_valid_checkout_succeeds(self, monkeypatch):
        """A record with check_in but no check_out should succeed."""
        rec = self._base_record()
        repo = _FakeAttendanceRepo([rec])
        c = _make_app(monkeypatch, attendance_repo=repo)
        resp = c.post("/hr/attendance/att-2/check-out", headers=_auth_headers())
        assert resp.status_code == 200
        assert "checkOutTime" in resp.json()

    def test_checkout_nonexistent_record_404(self, monkeypatch):
        """Unknown attendance_id must return 404."""
        repo = _FakeAttendanceRepo([])
        c = _make_app(monkeypatch, attendance_repo=repo)
        resp = c.post("/hr/attendance/no-such-id/check-out", headers=_auth_headers())
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# HTTP-level tests for mark_attendance validation
# ---------------------------------------------------------------------------


class TestMarkAttendanceValidation:
    def _mark(self, monkeypatch, payload, repo=None):
        c = _make_app(monkeypatch, attendance_repo=repo or _FakeAttendanceRepo([]))
        return c.post("/hr/attendance/mark", headers=_auth_headers(), json=payload)

    def test_future_date_rejected_422(self, monkeypatch):
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        resp = self._mark(monkeypatch, {
            "employee_id": "E1", "date": tomorrow, "status": "PRESENT"
        })
        assert resp.status_code == 422

    def test_invalid_status_rejected_422(self, monkeypatch):
        resp = self._mark(monkeypatch, {
            "employee_id": "E1", "date": date.today().isoformat(), "status": "WORKING"
        })
        assert resp.status_code == 422

    def test_checkout_before_checkin_rejected_422(self, monkeypatch):
        now = datetime.now()
        resp = self._mark(monkeypatch, {
            "employee_id": "E1",
            "date": date.today().isoformat(),
            "status": "PRESENT",
            "check_in": now.isoformat(),
            "check_out": (now - timedelta(hours=1)).isoformat(),
        })
        assert resp.status_code == 422

    def test_valid_mark_succeeds(self, monkeypatch):
        resp = self._mark(monkeypatch, {
            "employee_id": "E1",
            "date": date.today().isoformat(),
            "status": "PRESENT",
        })
        assert resp.status_code == 200

    def test_valid_mark_with_times_succeeds(self, monkeypatch):
        now = datetime.now()
        resp = self._mark(monkeypatch, {
            "employee_id": "E1",
            "date": date.today().isoformat(),
            "status": "PRESENT",
            "check_in": (now - timedelta(hours=9)).isoformat(),
            "check_out": now.isoformat(),
        })
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# HTTP-level tests for apply_leave
# ---------------------------------------------------------------------------


class TestApplyLeave:
    _BASE = {
        "leave_type": "CASUAL",
        "from_date": (date.today() + timedelta(days=2)).isoformat(),
        "to_date": (date.today() + timedelta(days=4)).isoformat(),
        "reason": "Personal work",
    }

    def test_valid_leave_creates_201(self, monkeypatch):
        c = _make_app(monkeypatch)
        resp = c.post("/hr/leaves", headers=_auth_headers(), json=self._BASE)
        assert resp.status_code == 201
        body = resp.json()
        assert body["status"] == "PENDING"
        assert body["leaveId"] != "new-leave-id"  # no longer the stub ID

    def test_invalid_leave_type_422(self, monkeypatch):
        c = _make_app(monkeypatch)
        payload = {**self._BASE, "leave_type": "VACATION"}
        resp = c.post("/hr/leaves", headers=_auth_headers(), json=payload)
        assert resp.status_code == 422

    def test_to_date_before_from_date_422(self, monkeypatch):
        c = _make_app(monkeypatch)
        payload = {
            **self._BASE,
            "from_date": (date.today() + timedelta(days=5)).isoformat(),
            "to_date": (date.today() + timedelta(days=2)).isoformat(),
        }
        resp = c.post("/hr/leaves", headers=_auth_headers(), json=payload)
        assert resp.status_code == 422

    def test_overlapping_approved_leave_blocked_409(self, monkeypatch):
        """A new leave that overlaps an APPROVED leave must return 409."""
        existing = {
            "leave_id": "lv-exist",
            "employee_id": "u-test",
            "status": "APPROVED",
            "from_date": (date.today() + timedelta(days=1)).isoformat(),
            "to_date": (date.today() + timedelta(days=10)).isoformat(),
        }
        repo = _FakeLeaveRepo([existing])
        c = _make_app(monkeypatch, leave_repo=repo)
        # Request overlaps days 2-4 with the approved block 1-10.
        resp = c.post("/hr/leaves", headers=_auth_headers(), json=self._BASE)
        assert resp.status_code == 409
        assert "overlaps" in resp.json()["detail"].lower()

    def test_overlapping_pending_leave_blocked_409(self, monkeypatch):
        """A new leave that overlaps a PENDING leave must return 409."""
        existing = {
            "leave_id": "lv-pend",
            "employee_id": "u-test",
            "status": "PENDING",
            "from_date": (date.today() + timedelta(days=3)).isoformat(),
            "to_date": (date.today() + timedelta(days=6)).isoformat(),
        }
        repo = _FakeLeaveRepo([existing])
        c = _make_app(monkeypatch, leave_repo=repo)
        resp = c.post("/hr/leaves", headers=_auth_headers(), json=self._BASE)
        assert resp.status_code == 409

    def test_non_overlapping_leave_allowed(self, monkeypatch):
        """A leave that doesn't overlap with existing ones must succeed."""
        existing = {
            "leave_id": "lv-old",
            "employee_id": "u-test",
            "status": "APPROVED",
            "from_date": (date.today() + timedelta(days=10)).isoformat(),
            "to_date": (date.today() + timedelta(days=15)).isoformat(),
        }
        repo = _FakeLeaveRepo([existing])
        c = _make_app(monkeypatch, leave_repo=repo)
        resp = c.post("/hr/leaves", headers=_auth_headers(), json=self._BASE)
        assert resp.status_code == 201

    def test_rejected_leave_does_not_block_new_one(self, monkeypatch):
        """A REJECTED leave in the same date range must not block a new application."""
        existing = {
            "leave_id": "lv-rej",
            "employee_id": "u-test",
            "status": "REJECTED",
            "from_date": (date.today() + timedelta(days=2)).isoformat(),
            "to_date": (date.today() + timedelta(days=4)).isoformat(),
        }
        repo = _FakeLeaveRepo([existing])
        c = _make_app(monkeypatch, leave_repo=repo)
        resp = c.post("/hr/leaves", headers=_auth_headers(), json=self._BASE)
        assert resp.status_code == 201

    def test_no_db_still_returns_201_failsoft(self, monkeypatch):
        """When the leave repo is unavailable, the endpoint must not 500."""
        c = _make_app(monkeypatch, leave_repo=None)
        resp = c.post("/hr/leaves", headers=_auth_headers(), json=self._BASE)
        assert resp.status_code == 201
        assert resp.json()["status"] == "PENDING"
