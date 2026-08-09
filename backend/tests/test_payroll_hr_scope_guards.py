"""
IMS 2.0 - Payroll payslip IDOR + HR attendance falsy-scope fail-open
====================================================================
Regression locks for two P1 findings raised by the PR #967 security panel,
both in routers OUTSIDE that PR's scope:

  F-A (payroll.py, payslip x3) -- GET /payroll/payslip/{employee_id},
      GET /payroll/payslip/{employee_id}/{month}/{year} and its /print sibling
      took the employee id as a FREE path parameter with Depends(get_current_user)
      only: no route-level authorization at all, so the handler returned another
      employee's NET PAY, full CTC breakdown and BANK ACCOUNT. Now gated by
      payroll._assert_self_or_pay_manager (SELF always; manager tier for anyone),
      mirroring the self-or-manager gate the commission ledger in the same file
      already used.

  F-B (hr.py, attendance x3) -- GET /hr/attendance, /attendance/grid and
      /attendance/summary resolved their store scope with
      ``validate_store_access(...) or active_store_id`` and then applied it under
      ``if active_store:``. A falsy scope meant NO FILTER (org-wide reads) instead
      of NO ACCESS, and an AREA_MANAGER holding several stores was silently
      narrowed to one. Now hr._scope_for_request / hr._store_scope_filter, the
      same canonical ``user_store_scope`` helper users.py adopted in PR #967:
      an empty reach yields {"$in": []} -> an EMPTY list, never org-wide and
      never a 403 lockout.

Harness mirrors test_hr_attendance_grid.py: FastAPI TestClient over the real
routers with monkeypatched repos / db. The fakes implement Mongo semantics for
the operators these routes actually issue ($in, $gte, $lte, array containment),
so ``{"$in": []}`` matches NOTHING here exactly as it does in Mongo.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

import jwt  # noqa: E402
import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import hr as hr_mod  # noqa: E402
from api.routers import payroll as payroll_mod  # noqa: E402
from api.services import rbac_policy  # noqa: E402

SECRET = os.environ["JWT_SECRET_KEY"]

STORE_A = "BV-PUN-01"
STORE_B = "BV-BOK-01"
STORE_C = "BV-RAN-01"

SELF_ID = "u-cashier"
OTHER_ID = "u-colleague"


def _token(roles, user_id="u-test", store_ids=None, active_store=STORE_A):
    """A token in the shape get_current_user yields (it returns the payload)."""
    return jwt.encode(
        {
            "sub": user_id,
            "user_id": user_id,
            "username": user_id,
            "roles": list(roles),
            "store_ids": [] if store_ids is None else list(store_ids),
            "active_store_id": active_store,
            "exp": datetime.utcnow() + timedelta(hours=1),
        },
        SECRET,
        algorithm="HS256",
    )


def _auth(tok):
    return {"Authorization": f"Bearer {tok}"}


# ===========================================================================
# Mongo-ish fakes
# ===========================================================================


def _match_value(doc_value, expected) -> bool:
    """Enough of the Mongo matcher for the queries these routes issue."""
    if isinstance(expected, dict):
        ok = True
        if "$in" in expected:
            if isinstance(doc_value, list):
                ok = ok and any(v in expected["$in"] for v in doc_value)
            else:
                ok = ok and doc_value in expected["$in"]
        if "$gte" in expected:
            ok = ok and doc_value is not None and doc_value >= expected["$gte"]
        if "$lte" in expected:
            ok = ok and doc_value is not None and doc_value <= expected["$lte"]
        if "$ne" in expected:
            ok = ok and doc_value != expected["$ne"]
        return ok
    if isinstance(doc_value, list):
        return expected in doc_value
    return doc_value == expected


def _matches(doc: dict, query: dict) -> bool:
    return all(_match_value(doc.get(k), v) for k, v in (query or {}).items())


class _FakeCursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def sort(self, spec):
        for field, direction in reversed(list(spec)):
            self._docs.sort(key=lambda d: d.get(field) or 0, reverse=direction < 0)
        return self

    def limit(self, n):
        self._docs = self._docs[:n]
        return self

    def __iter__(self):
        return iter(self._docs)


class _FakeColl:
    def __init__(self, docs=None):
        self.docs = [dict(d) for d in (docs or [])]
        self.inserted = []

    def find_one(self, query=None, projection=None):
        for d in self.docs:
            if _matches(d, query or {}):
                return dict(d)
        return None

    def find(self, query=None, projection=None):
        return _FakeCursor([dict(d) for d in self.docs if _matches(d, query or {})])

    def insert_one(self, doc):
        self.inserted.append(dict(doc))
        self.docs.append(dict(doc))
        return type("_R", (), {"inserted_id": "fake"})()


class _FakeDb:
    def __init__(self, collections):
        self._colls = collections

    def get_collection(self, name):
        return self._colls.setdefault(name, _FakeColl([]))


def _payroll_row(employee_id, store_id=STORE_A, month=5, year=2026, net=28618.0):
    return {
        "payroll_id": f"pr-{employee_id}",
        "employee_id": employee_id,
        "employee_name": f"Name {employee_id}",
        "store_id": store_id,
        "entity_id": "ent-1",
        "month": month,
        "year": year,
        "status": "APPROVED",
        "breakdown": {
            "earnings": {
                "basic": 20000.0,
                "hra": 8000.0,
                "conveyance": 1600.0,
                "medical": 1250.0,
                "special_allowance": 0.0,
                "other_allowances": 0.0,
                "full_gross": 30850.0,
                "earned_gross": 30850.0,
                "incentive": 0.0,
                "total_earnings": 30850.0,
            },
            "deductions": {
                "pf_employee": 1800.0,
                "esi_employee": 232.0,
                "professional_tax": 200.0,
                "tds": 0.0,
                "advance_recovery": 0.0,
                "total_deductions": 2232.0,
            },
            "net_pay": net,
        },
        "net_salary": net,
    }


def _payroll_db():
    """Both employees have a run row + a salary config holding a bank account."""
    return _FakeDb(
        {
            "payroll": _FakeColl(
                [
                    _payroll_row(SELF_ID, net=21000.0),
                    _payroll_row(OTHER_ID, net=99000.0),
                ]
            ),
            "payslips": _FakeColl([]),
            "salary_records": _FakeColl([]),
            "salary_config": _FakeColl(
                [
                    {
                        "employee_id": SELF_ID,
                        "store_id": STORE_A,
                        "bank_account": "111",
                    },
                    {
                        "employee_id": OTHER_ID,
                        "store_id": STORE_A,
                        "bank_account": "SECRET-222",
                    },
                ]
            ),
            "users": _FakeColl(
                [
                    {"user_id": SELF_ID, "full_name": "Own Cashier"},
                    {"user_id": OTHER_ID, "full_name": "Colleague"},
                ]
            ),
            "entities": _FakeColl([{"entity_id": "ent-1", "name": "BV Opticals"}]),
        }
    )


@pytest.fixture()
def payroll_client(monkeypatch):
    app = FastAPI()
    app.include_router(payroll_mod.router, prefix="/payroll")
    db = _payroll_db()
    monkeypatch.setattr(payroll_mod, "_get_db", lambda: db)
    return TestClient(app)


# ===========================================================================
# F-A  payslip self-or-manager gate
# ===========================================================================

_PAYSLIP_PATHS = (
    "/payroll/payslip/{eid}",
    "/payroll/payslip/{eid}/5/2026",
    "/payroll/payslip/{eid}/5/2026/print",
)


@pytest.mark.parametrize("template", _PAYSLIP_PATHS)
def test_cashier_cannot_read_a_colleagues_payslip(payroll_client, template):
    """P1: the whole point -- a non-manager is refused someone else's slip."""
    tok = _token(["CASHIER"], user_id=SELF_ID, store_ids=[STORE_A])
    r = payroll_client.get(template.format(eid=OTHER_ID), headers=_auth(tok))
    assert r.status_code == 403, r.text
    # And nothing of the colleague's pay data leaked into the refusal body.
    assert "99000" not in r.text
    assert "SECRET-222" not in r.text


@pytest.mark.parametrize("template", _PAYSLIP_PATHS)
def test_cashier_can_still_read_their_own_payslip(payroll_client, template):
    """Availability half of the fix: self-service must keep working."""
    tok = _token(["CASHIER"], user_id=SELF_ID, store_ids=[STORE_A])
    r = payroll_client.get(template.format(eid=SELF_ID), headers=_auth(tok))
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("template", _PAYSLIP_PATHS)
@pytest.mark.parametrize(
    "role", ["ACCOUNTANT", "ADMIN", "SUPERADMIN", "STORE_MANAGER", "AREA_MANAGER"]
)
def test_manager_tier_reach_is_unchanged(payroll_client, template, role):
    """No false 403 on payroll day: every role the RBAC policy admits still passes."""
    tok = _token([role], user_id="u-mgr", store_ids=[STORE_A])
    r = payroll_client.get(template.format(eid=OTHER_ID), headers=_auth(tok))
    assert r.status_code == 200, r.text


def test_manager_actually_receives_the_pay_data(payroll_client):
    """The 200 above is a real payslip, not an empty shell."""
    tok = _token(["ACCOUNTANT"], user_id="u-acct", store_ids=[STORE_A])
    r = payroll_client.get(f"/payroll/payslip/{OTHER_ID}/5/2026", headers=_auth(tok))
    assert r.status_code == 200
    slip = r.json()["payslip"]
    assert slip["employee_id"] == OTHER_ID
    assert slip["breakdown"]["net_pay"] == 99000.0
    assert slip["bank_account"] == "SECRET-222"


def test_payslip_gate_applies_before_the_db_is_touched(monkeypatch):
    """With Mongo down the routes fail soft (200/None). The gate must still be
    the answer for an unauthorised caller -- authorization cannot depend on the
    database being up."""
    app = FastAPI()
    app.include_router(payroll_mod.router, prefix="/payroll")
    monkeypatch.setattr(payroll_mod, "_get_db", lambda: None)
    c = TestClient(app)
    tok = _token(["CASHIER"], user_id=SELF_ID, store_ids=[STORE_A])
    assert c.get(f"/payroll/payslip/{OTHER_ID}", headers=_auth(tok)).status_code == 403
    # ... and the caller's own read still fails SOFT rather than 403ing.
    own = c.get(f"/payroll/payslip/{SELF_ID}", headers=_auth(tok))
    assert own.status_code == 200 and own.json() == {"payslip": None}


def test_unauthenticated_still_401(payroll_client):
    assert payroll_client.get(f"/payroll/payslip/{OTHER_ID}").status_code == 401


def test_payslip_gate_admits_every_role_the_rbac_policy_admits():
    """Anti-lockout + anti-drift lock.

    The gate must never be STRICTER than the declared policy rows for these
    paths, or the request-time middleware would allow a caller the handler then
    403s -- a false lockout on a live screen. SUPERADMIN is allowed by
    check_access itself, so it is asserted separately.
    """
    for path in (
        "/api/v1/payroll/payslip/emp-1",
        "/api/v1/payroll/payslip/emp-1/5/2026",
        "/api/v1/payroll/payslip/emp-1/5/2026/print",
    ):
        policy = rbac_policy.policy_for("GET", path)
        assert policy is not None, f"{path} must stay catalogued"
        for role in policy["allowed"]:
            assert payroll_mod._is_pay_data_manager({"roles": [role]}), (
                f"{role} is allowed by rbac_policy for {path} but rejected by the "
                "payslip gate -- that is a lockout"
            )
    assert payroll_mod._is_pay_data_manager({"roles": ["SUPERADMIN"]})


def test_commission_gate_uses_the_same_definition():
    """The commission ledger's self-or-manager gate and the payslip gate read
    ONE tuple, so they cannot drift apart (the whole reason this helper exists)."""
    assert payroll_mod._PAY_DATA_MANAGER_ROLES == (
        "SUPERADMIN",
        "ADMIN",
        "AREA_MANAGER",
        "STORE_MANAGER",
        "ACCOUNTANT",
    )
    assert not payroll_mod._is_pay_data_manager({"roles": ["CASHIER", "SALES_STAFF"]})
    assert not payroll_mod._is_pay_data_manager({})


# ===========================================================================
# F-B  HR attendance store scope
# ===========================================================================


class _FakeUserRepo:
    def __init__(self, users):
        self._users = [dict(u) for u in users]

    def find_many(self, filter=None, sort=None, skip=0, limit=100):
        return [dict(u) for u in self._users if _matches(u, filter or {})][:limit]


class _FakeAttendanceRepo:
    def __init__(self, records):
        self._records = [dict(r) for r in records]

    def find_many(self, filter=None, sort=None, skip=0, limit=100):
        return [dict(r) for r in self._records if _matches(r, filter or {})][:limit]


_USERS = [
    {"user_id": "E1", "full_name": "Asha", "store_ids": [STORE_A], "is_active": True},
    {"user_id": "E2", "full_name": "Bina", "store_ids": [STORE_B], "is_active": True},
    {"user_id": "E3", "full_name": "Chan", "store_ids": [STORE_C], "is_active": True},
]
_RECORDS = [
    {
        "attendance_id": "a1",
        "employee_id": "E1",
        "date": "2026-05-01",
        "status": "PRESENT",
        "store_id": STORE_A,
    },
    {
        "attendance_id": "a2",
        "employee_id": "E2",
        "date": "2026-05-02",
        "status": "ABSENT",
        "store_id": STORE_B,
    },
    {
        "attendance_id": "a3",
        "employee_id": "E3",
        "date": "2026-05-03",
        "status": "PRESENT",
        "store_id": STORE_C,
    },
]


@pytest.fixture()
def hr_client(monkeypatch):
    app = FastAPI()
    app.include_router(hr_mod.router, prefix="/hr")
    monkeypatch.setattr(hr_mod, "get_user_repository", lambda: _FakeUserRepo(_USERS))
    monkeypatch.setattr(
        hr_mod, "get_attendance_repository", lambda: _FakeAttendanceRepo(_RECORDS)
    )
    return TestClient(app)


def _stores_in(records_json):
    return {r["storeId"] for r in records_json["records"]}


def test_storeless_manager_gets_empty_attendance_not_org_wide(hr_client):
    """P1: the fail-open. A manager with NO store_ids and NO active store used to
    get every store's attendance; they must now get an empty list -- and a 200,
    not a 403 (a lockout would be its own outage)."""
    tok = _token(["STORE_MANAGER"], user_id="u-mgr", store_ids=[], active_store=None)
    r = hr_client.get("/hr/attendance", headers=_auth(tok))
    assert r.status_code == 200, r.text
    assert r.json() == {"records": [], "total": 0}


def test_storeless_accountant_grid_and_summary_are_empty_not_org_wide(hr_client):
    tok = _token(["ACCOUNTANT"], user_id="u-acct", store_ids=[], active_store=None)
    grid = hr_client.get(
        "/hr/attendance/grid", params={"month": "2026-05"}, headers=_auth(tok)
    )
    assert grid.status_code == 200, grid.text
    assert grid.json()["employees"] == []
    assert grid.json()["totals"]["present"] == 0

    summ = hr_client.get(
        "/hr/attendance/summary", params={"month": "2026-05"}, headers=_auth(tok)
    )
    assert summ.status_code == 200, summ.text
    assert summ.json()["employees"] == []
    assert summ.json()["stores"] == []


def test_area_manager_sees_all_their_stores_not_just_the_active_one(hr_client):
    """The second half of the fail-open: the old single active_store_id filter
    silently narrowed a multi-store manager to ONE store."""
    tok = _token(
        ["AREA_MANAGER"],
        user_id="u-am",
        store_ids=[STORE_A, STORE_B],
        active_store=STORE_A,
    )
    r = hr_client.get("/hr/attendance", headers=_auth(tok))
    assert r.status_code == 200, r.text
    assert _stores_in(r.json()) == {STORE_A, STORE_B}
    assert STORE_C not in _stores_in(r.json())


def test_area_manager_grid_covers_all_their_stores(hr_client):
    tok = _token(
        ["AREA_MANAGER"],
        user_id="u-am",
        store_ids=[STORE_A, STORE_B],
        active_store=STORE_A,
    )
    r = hr_client.get(
        "/hr/attendance/grid", params={"month": "2026-05"}, headers=_auth(tok)
    )
    assert r.status_code == 200, r.text
    ids = {e["employee_id"] for e in r.json()["employees"]}
    assert ids == {"E1", "E2"}  # E3 belongs to a store outside their reach


def test_store_manager_is_confined_to_own_store(hr_client):
    tok = _token(
        ["STORE_MANAGER"], user_id="u-sm", store_ids=[STORE_A], active_store=STORE_A
    )
    r = hr_client.get("/hr/attendance", headers=_auth(tok))
    assert r.status_code == 200
    assert _stores_in(r.json()) == {STORE_A}


def test_explicit_cross_store_request_still_403(hr_client):
    """Unchanged: naming somebody else's store is still an explicit 403."""
    tok = _token(
        ["STORE_MANAGER"], user_id="u-sm", store_ids=[STORE_A], active_store=STORE_A
    )
    for path in ("/hr/attendance", "/hr/attendance/grid", "/hr/attendance/summary"):
        r = hr_client.get(
            path, params={"store_id": STORE_B, "month": "2026-05"}, headers=_auth(tok)
        )
        assert r.status_code == 403, f"{path}: {r.text}"


def test_admin_keeps_org_wide_reach(hr_client):
    """Cross-store roles are cross-store by design -- user_store_scope says so."""
    tok = _token(["ADMIN"], user_id="u-admin", store_ids=[], active_store=None)
    r = hr_client.get("/hr/attendance", headers=_auth(tok))
    assert r.status_code == 200
    assert _stores_in(r.json()) == {STORE_A, STORE_B, STORE_C}

    grid = hr_client.get(
        "/hr/attendance/grid", params={"month": "2026-05"}, headers=_auth(tok)
    )
    assert {e["employee_id"] for e in grid.json()["employees"]} == {"E1", "E2", "E3"}


def test_explicit_own_store_still_pins_the_roster_rows(hr_client):
    """A single named store keeps the scalar path (roster rows pinned to it)."""
    tok = _token(
        ["STORE_MANAGER"], user_id="u-sm", store_ids=[STORE_A], active_store=STORE_A
    )
    r = hr_client.get(
        "/hr/attendance/grid",
        params={"month": "2026-05", "store_id": STORE_A},
        headers=_auth(tok),
    )
    assert r.status_code == 200
    employees = r.json()["employees"]
    assert {e["employee_id"] for e in employees} == {"E1"}
    assert {e["store_id"] for e in employees} == {STORE_A}


def test_store_scope_filter_shape():
    """The helper itself: cross-store -> None; empty reach -> a clause that
    matches NOTHING (never an absent filter)."""
    assert hr_mod._store_scope_filter({"roles": ["ADMIN"]}) is None
    assert hr_mod._store_scope_filter({"roles": ["SUPERADMIN"]}) is None
    assert hr_mod._store_scope_filter(
        {"roles": ["STORE_MANAGER"], "store_ids": [], "active_store_id": None}
    ) == {"$in": []}
    assert hr_mod._store_scope_filter(
        {"roles": ["AREA_MANAGER"], "store_ids": [STORE_B], "active_store_id": STORE_A}
    ) == {"$in": sorted([STORE_A, STORE_B])}


def test_hr_attendance_unauthenticated_still_401(hr_client):
    assert hr_client.get("/hr/attendance").status_code == 401
