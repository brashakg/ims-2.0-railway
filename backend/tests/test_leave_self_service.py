"""
IMS 2.0 - Apply-for-leave via the self-service door (/hr/me/leaves)
====================================================================
The apply rule has always lived in hr.apply_leave (POST /hr/leaves), but that
router is mounted behind the _FINANCE_ROLES gate, so floor staff (sales /
cashier / optometrist / workshop) 403'd on it -- nobody below manager could
request leave, and the whole approve chain sat unused.

The fix is a DELEGATING route on the ungated hr_self_service router:
POST /hr/me/leaves calls hr.apply_leave (one implementation of the apply rule,
per the one-rule-two-implementations feedback), plus a new self-scoped
POST /hr/me/leaves/{leave_id}/cancel.

These tests drive the REAL handlers + the REAL LeaveRepository over the strict
in-memory Mongo double. What each locks:

  * the /me door files the leave pinned to the CALLER (an employee_id smuggled
    into the body is ignored -- the model has no such field);
  * the /me door reaches the REAL overlap rule (409) -- a naive second
    implementation that just inserts would pass everything else and fail here;
  * cancel is self-scoped: a colleague's leave id behaves exactly like a
    missing id (404, nothing written);
  * cancel only works on PENDING (400 once decided) and frees the dates for a
    re-application (the overlap rule counts only APPROVED/PENDING);
  * the /me routes are mounted OUTSIDE the finance gate while POST /hr/leaves
    stays INSIDE it (route-dependency introspection, calibrated against the
    gated door so the assertion cannot rot silently);
  * the rbac_policy rows say AUTHENTICATED, so the request-time middleware
    does not undo the mount (a row saying ["ADMIN"] would kill the screen for
    its entire audience while every handler test stayed green).
"""

from __future__ import annotations

import asyncio
import os
import sys
from tests.ist_business_day import business_day
from datetime import date, timedelta

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

from strict_fakes import StrictDB  # noqa: E402

from api.routers import hr as hr_router  # noqa: E402
from api.routers import hr_self_service as ss_router  # noqa: E402
from api.routers.hr import LeaveCreate  # noqa: E402
from database.repositories.hr_repository import LeaveRepository  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


def _user(user_id: str, roles=("SALES_STAFF",), store_id="BV-BOK-01") -> dict:
    return {
        "user_id": user_id,
        "roles": list(roles),
        "active_store_id": store_id,
        "store_ids": [store_id],
    }


def _payload(days_ahead: int = 10, length_days: int = 1, leave_type: str = "EARNED", **extra):
    """A valid LeaveCreate payload. Defaults to EARNED, 10 days out, so the F26
    CASUAL/SICK short-notice fast-path (covered by test_f26_remote_approval)
    stays out of the way."""
    # IST business day, not the box clock: this date is compared against the
    # leave table's business-day window and would drift between 00:00 and 05:30.
    frm = date.fromisoformat(business_day()) + timedelta(days=days_ahead)
    to = frm + timedelta(days=length_days - 1)
    return {
        "leave_type": leave_type,
        "from_date": frm.isoformat(),
        "to_date": to.isoformat(),
        "reason": "family function",
        **extra,
    }


@pytest.fixture
def db():
    return StrictDB()


@pytest.fixture
def leave_repo(db):
    return LeaveRepository(db.get_collection("leaves"))


@pytest.fixture
def wired(db, leave_repo, monkeypatch):
    """Point every accessor BOTH modules use at one shared strict DB. The
    delegate (hr.apply_leave) resolves names in hr's module globals; the cancel
    handler resolves get_leave_repository in hr_self_service's globals."""
    monkeypatch.setattr(hr_router, "_get_db", lambda: db)
    monkeypatch.setattr(hr_router, "get_leave_repository", lambda: leave_repo)
    monkeypatch.setattr(ss_router, "get_leave_repository", lambda: leave_repo)
    return db


# ---------------------------------------------------------------------------
# Apply through the /me door
# ---------------------------------------------------------------------------


def test_apply_via_me_door_pins_employee_to_caller(wired, leave_repo):
    res = _run(ss_router.apply_my_leave(LeaveCreate(**_payload()), _user("emp-A")))

    assert res["status"] == "PENDING"
    stored = leave_repo.find_by_id(res["leaveId"])
    assert stored is not None
    assert stored["employee_id"] == "emp-A"
    assert stored["store_id"] == "BV-BOK-01"
    assert stored["leave_type"] == "EARNED"


def test_body_cannot_smuggle_a_colleagues_employee_id(wired, leave_repo):
    """LeaveCreate has no employee_id field -- an injected one is dropped by
    pydantic and the door files for the CALLER. If someone ever adds an
    employee_id field to the model AND trusts it, this fails."""
    model = LeaveCreate(**_payload(employee_id="emp-VICTIM"))
    assert not hasattr(model, "employee_id")

    res = _run(ss_router.apply_my_leave(model, _user("emp-A")))
    stored = leave_repo.find_by_id(res["leaveId"])
    assert stored["employee_id"] == "emp-A"
    assert "emp-VICTIM" not in [d.get("employee_id") for d in wired.get_collection("leaves").docs]


def test_me_door_reaches_the_real_overlap_rule(wired):
    """The /me route DELEGATES to hr.apply_leave. A second, naive apply
    implementation (plain insert) would pass the pin test above and silently
    allow double-booking -- this 409 is the differential probe that catches it."""
    _run(ss_router.apply_my_leave(LeaveCreate(**_payload(days_ahead=10, length_days=3)), _user("emp-A")))

    with pytest.raises(HTTPException) as exc:
        # Starts inside the existing PENDING range.
        _run(ss_router.apply_my_leave(LeaveCreate(**_payload(days_ahead=11)), _user("emp-A")))
    assert exc.value.status_code == 409

    # A DIFFERENT employee on the same dates is fine (the rule is per-employee).
    res = _run(ss_router.apply_my_leave(LeaveCreate(**_payload(days_ahead=11)), _user("emp-B")))
    assert res["status"] == "PENDING"


# ---------------------------------------------------------------------------
# Cancel own pending request
# ---------------------------------------------------------------------------


def test_cancel_own_pending_leave(wired, leave_repo):
    leave_id = _run(
        ss_router.apply_my_leave(LeaveCreate(**_payload()), _user("emp-A"))
    )["leaveId"]

    res = _run(ss_router.cancel_my_leave(leave_id, _user("emp-A")))
    assert res["status"] == "CANCELLED"

    stored = leave_repo.find_by_id(leave_id)
    assert stored["status"] == "CANCELLED"
    assert stored["cancelled_by"] == "emp-A"


def test_cannot_cancel_a_colleagues_leave(wired, leave_repo):
    """Self-scope: a colleague's id must behave EXACTLY like a missing id (404)
    and write nothing. Last week's IDOR sweep found thirteen doors trusting an
    id as authority -- this door must not be the fourteenth."""
    leave_id = _run(
        ss_router.apply_my_leave(LeaveCreate(**_payload()), _user("emp-B"))
    )["leaveId"]

    with pytest.raises(HTTPException) as exc:
        _run(ss_router.cancel_my_leave(leave_id, _user("emp-A")))
    assert exc.value.status_code == 404

    with pytest.raises(HTTPException) as exc2:
        _run(ss_router.cancel_my_leave("no-such-leave", _user("emp-A")))
    assert exc2.value.status_code == 404  # indistinguishable from the colleague case

    assert leave_repo.find_by_id(leave_id)["status"] == "PENDING"  # untouched


def test_cannot_cancel_a_decided_leave(wired, leave_repo):
    leave_id = _run(
        ss_router.apply_my_leave(LeaveCreate(**_payload()), _user("emp-A"))
    )["leaveId"]
    leave_repo.update(leave_id, {"status": "APPROVED", "approved_by": "mgr-1"})

    with pytest.raises(HTTPException) as exc:
        _run(ss_router.cancel_my_leave(leave_id, _user("emp-A")))
    assert exc.value.status_code == 400
    assert leave_repo.find_by_id(leave_id)["status"] == "APPROVED"


def test_cancelled_leave_frees_the_dates(wired):
    """The overlap rule counts APPROVED/PENDING only, so cancelling must free
    the range. If cancel ever writes a status the overlap filter still matches
    (or stops writing at all), the re-application here 409s."""
    first = _run(ss_router.apply_my_leave(LeaveCreate(**_payload()), _user("emp-A")))
    _run(ss_router.cancel_my_leave(first["leaveId"], _user("emp-A")))

    again = _run(ss_router.apply_my_leave(LeaveCreate(**_payload()), _user("emp-A")))
    assert again["status"] == "PENDING"


# ---------------------------------------------------------------------------
# Mount + policy: the door must actually be REACHABLE by floor staff
# ---------------------------------------------------------------------------


def _route_dep_names(app, method: str, path: str):
    for r in app.routes:
        if getattr(r, "path", None) == path and method in (getattr(r, "methods", None) or ()):
            return [d.call.__name__ for d in r.dependant.dependencies if d.call]
    raise AssertionError(f"route {method} {path} not found")


def test_me_routes_mounted_outside_finance_gate():
    """The whole point of the /me door: floor staff must REACH it. The finance
    gate arrives as a router-level dependency copied onto every hr_router
    route; we CALIBRATE by reading the gated POST /hr/leaves first, then assert
    the same closure is absent from both /me routes."""
    from api.main import app

    gated = _route_dep_names(app, "POST", "/api/v1/hr/leaves")
    gate_names = [n for n in gated if n not in ("get_current_user",)]
    assert gate_names, "calibration broke: POST /hr/leaves no longer carries a role gate"

    for path in ("/api/v1/hr/me/leaves", "/api/v1/hr/me/leaves/{leave_id}/cancel"):
        deps = _route_dep_names(app, "POST", path)
        for gate in gate_names:
            assert gate not in deps, f"{path} is behind the finance gate: {deps}"


def test_policy_rows_let_floor_staff_through_the_me_doors():
    """The RBAC middleware enforces rbac_policy rows at request time. If the
    new rows said ["ADMIN"] the mount test above would still pass while every
    floor-staff request 403'd -- so check the POLICY, functionally."""
    from api.services.rbac_policy import check_access

    for role in ("SALES_STAFF", "SALES_CASHIER", "OPTOMETRIST", "WORKSHOP_STAFF"):
        assert check_access("POST", "/api/v1/hr/me/leaves", [role])
        assert check_access("POST", "/api/v1/hr/me/leaves/lv-1/cancel", [role])

    # Calibration: the gated door still refuses floor staff at the policy layer.
    assert not check_access("POST", "/api/v1/hr/leaves", ["SALES_STAFF"])
