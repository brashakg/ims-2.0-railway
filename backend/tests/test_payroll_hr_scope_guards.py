"""
IMS 2.0 - Payslip admin-only rule + HR attendance store scoping
===============================================================
Locks two access-control rules and one regression the adversarial panel caught.

  A. PAYSLIP (payroll.py) -- OWNER RULING 2026-08-09, verbatim: "nobody except
     admin/superadmin should see anyone elses salary." The three payslip routes
     (GET /payroll/payslip/{employee_id}, .../{month}/{year}, and .../print) had
     no gate of their own, so every role the router mount admits -- ADMIN,
     AREA_MANAGER, STORE_MANAGER, ACCOUNTANT (+ SUPERADMIN) -- could read
     anyone's NET PAY, CTC and BANK ACCOUNT by typing an id. Now
     payroll._assert_self_or_salary_admin: SELF always, anyone else ADMIN /
     SUPERADMIN only.

  B. HR ATTENDANCE (hr.py) -- GET /hr/attendance, /attendance/grid and
     /attendance/summary resolved scope as "validate_store_access(...) or
     active_store_id" applied under "if active_store:". A falsy scope meant NO
     FILTER (org-wide) instead of NO ACCESS, and a multi-store AREA_MANAGER was
     silently narrowed to one store. Now hr._scope_for_request /
     _store_scope_filter, reusing the canonical user_store_scope (the same
     helper users.py adopted in PR #967): an empty reach yields {"$in": []} ->
     an EMPTY list, never org-wide and never a 403.

  C. ROSTER LABELS (hr.py, PR-introduced regression) -- because (B) returns an
     $in clause for EVERY non-cross-store role, _single_store returned None
     universally and _roster_from_users fell back to store_ids[0]. A single-store
     Ranchi manager saw a summary row labelled WO-MUM-01 (another city, another
     legal entity) and Ranchi's headcount was short by the multi-store employee.
     Now _scope_store_set + the allowed_stores argument pin each row to a store
     the caller can actually see. E4/E5 below are the multi-store employees whose
     store_ids[0] sits OUTSIDE the caller's reach -- they are the whole point of
     these fixtures, and they are what the first cut of this suite lacked.

FIDELITY: both routers are mounted here exactly as api/main.py mounts them --
with dependencies=[Depends(require_roles(*_FINANCE_ROLES))] (main.py:1417 for
hr, main.py:1509 for payroll). Driving a BARE router hid the fact that floor
roles never reach these paths at all, which is why the first cut of this file
asserted a CASHIER self-service flow that cannot happen in the real app.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

import jwt  # noqa: E402
import pytest  # noqa: E402
from fastapi import Depends, FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.main import _FINANCE_ROLES  # noqa: E402  - the REAL mount gate
from api.routers import hr as hr_mod  # noqa: E402
from api.routers import payroll as payroll_mod  # noqa: E402
from api.routers.auth import require_roles  # noqa: E402
from api.services import rbac_policy  # noqa: E402

SECRET = os.environ["JWT_SECRET_KEY"]

STORE_A = "BV-PUN-01"
STORE_B = "BV-RAN-01"
# Deliberately a different chain / city / legal entity: a label that leaking into
# a BV manager's summary is the exact bug the panel reproduced.
STORE_C = "WO-MUM-01"

SELF_ID = "u-self"
OTHER_ID = "u-colleague"

# Roles the router mount admits (main.py _FINANCE_ROLES) that are NOT allowed to
# read someone else's salary under the owner ruling.
NON_ADMIN_PAY_ROLES = ("ACCOUNTANT", "AREA_MANAGER", "STORE_MANAGER")
ADMIN_PAY_ROLES = ("ADMIN", "SUPERADMIN")


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


def _mounted(router, prefix):
    """Mount a router the way api/main.py mounts it -- gate included."""
    app = FastAPI()
    app.include_router(
        router,
        prefix=prefix,
        dependencies=[Depends(require_roles(*_FINANCE_ROLES))],
    )
    return app


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
                    {"user_id": SELF_ID, "full_name": "Own Staffer"},
                    {"user_id": OTHER_ID, "full_name": "Colleague"},
                ]
            ),
            "entities": _FakeColl([{"entity_id": "ent-1", "name": "BV Opticals"}]),
        }
    )


@pytest.fixture()
def payroll_client(monkeypatch):
    db = _payroll_db()
    monkeypatch.setattr(payroll_mod, "_get_db", lambda: db)
    return TestClient(_mounted(payroll_mod.router, "/payroll"))


# ===========================================================================
# A. payslip -- owner ruling: self, or ADMIN/SUPERADMIN
# ===========================================================================

_PAYSLIP_PATHS = (
    "/payroll/payslip/{eid}",
    "/payroll/payslip/{eid}/5/2026",
    "/payroll/payslip/{eid}/5/2026/print",
)


@pytest.mark.parametrize("template", _PAYSLIP_PATHS)
@pytest.mark.parametrize("role", NON_ADMIN_PAY_ROLES)
def test_non_admin_cannot_read_a_colleagues_payslip(payroll_client, template, role):
    """OWNER RULE: ACCOUNTANT / AREA_MANAGER / STORE_MANAGER are refused another
    employee's slip even though the router mount admits them."""
    tok = _token([role], user_id=SELF_ID, store_ids=[STORE_A])
    r = payroll_client.get(template.format(eid=OTHER_ID), headers=_auth(tok))
    assert r.status_code == 403, r.text
    assert "99000" not in r.text
    assert "SECRET-222" not in r.text


@pytest.mark.parametrize("template", _PAYSLIP_PATHS)
@pytest.mark.parametrize("role", NON_ADMIN_PAY_ROLES)
def test_non_admin_can_still_read_their_own_payslip(payroll_client, template, role):
    """Availability half of the owner rule: own slip must keep working."""
    tok = _token([role], user_id=SELF_ID, store_ids=[STORE_A])
    r = payroll_client.get(template.format(eid=SELF_ID), headers=_auth(tok))
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("template", _PAYSLIP_PATHS)
@pytest.mark.parametrize("role", ADMIN_PAY_ROLES)
def test_admin_and_superadmin_keep_full_reach(payroll_client, template, role):
    tok = _token([role], user_id="u-admin", store_ids=[])
    r = payroll_client.get(template.format(eid=OTHER_ID), headers=_auth(tok))
    assert r.status_code == 200, r.text


def test_admin_actually_receives_the_pay_data(payroll_client):
    """The 200 above is a real payslip, not an empty shell."""
    tok = _token(["ADMIN"], user_id="u-admin", store_ids=[])
    r = payroll_client.get(f"/payroll/payslip/{OTHER_ID}/5/2026", headers=_auth(tok))
    assert r.status_code == 200
    slip = r.json()["payslip"]
    assert slip["employee_id"] == OTHER_ID
    assert slip["breakdown"]["net_pay"] == 99000.0
    assert slip["bank_account"] == "SECRET-222"


@pytest.mark.parametrize("template", _PAYSLIP_PATHS)
def test_floor_roles_never_reach_these_routes_at_all(payroll_client, template):
    """Documents WHERE the floor-staff refusal comes from: the router mount
    (require_roles(*_FINANCE_ROLES)), not the payslip gate. A CASHIER is refused
    even for their OWN id here -- their self-service path is /hr/me/payslip."""
    tok = _token(["CASHIER"], user_id=SELF_ID, store_ids=[STORE_A])
    assert payroll_client.get(
        template.format(eid=OTHER_ID), headers=_auth(tok)
    ).status_code == 403
    assert payroll_client.get(
        template.format(eid=SELF_ID), headers=_auth(tok)
    ).status_code == 403


def test_payslip_gate_applies_before_the_db_is_touched(monkeypatch):
    """With Mongo down the routes fail soft (200/None). Authorization must not
    depend on the database being up."""
    monkeypatch.setattr(payroll_mod, "_get_db", lambda: None)
    c = TestClient(_mounted(payroll_mod.router, "/payroll"))
    tok = _token(["ACCOUNTANT"], user_id=SELF_ID, store_ids=[STORE_A])
    assert c.get(f"/payroll/payslip/{OTHER_ID}", headers=_auth(tok)).status_code == 403
    own = c.get(f"/payroll/payslip/{SELF_ID}", headers=_auth(tok))
    assert own.status_code == 200 and own.json() == {"payslip": None}


def test_unauthenticated_still_401(payroll_client):
    assert payroll_client.get(f"/payroll/payslip/{OTHER_ID}").status_code == 401


def test_salary_gate_is_deliberately_stricter_than_the_rbac_policy_rows():
    """The gate is STRICTER than the declared policy, on the owner's ruling.

    The policy rows still list the manager tier, so the middleware admits them
    one layer earlier and this handler refuses them. ADMIN must remain in the
    rows, otherwise the middleware would block the only role that may read.
    """
    for path in (
        "/api/v1/payroll/payslip/emp-1",
        "/api/v1/payroll/payslip/emp-1/5/2026",
        "/api/v1/payroll/payslip/emp-1/5/2026/print",
    ):
        policy = rbac_policy.policy_for("GET", path)
        assert policy is not None, f"{path} must stay catalogued"
        # ADMIN/SUPERADMIN must stay admitted by the middleware, or the only
        # roles that may read would be blocked one layer earlier.
        assert "ADMIN" in policy["allowed"]
        assert rbac_policy.check_access("GET", path, ["SUPERADMIN"])
        # The manager tier is still admitted by the policy AND by the router
        # mount -- and is then refused by the handler. That layering is the
        # deliberate part: if someone "aligns" the policy rows to the handler
        # later, this assertion is where they will notice they are changing the
        # middleware's behaviour too.
        for role in NON_ADMIN_PAY_ROLES:
            assert role in policy["allowed"], f"{role} missing from {path} policy row"
            assert role in _FINANCE_ROLES


def test_salary_and_commission_rules_are_separate_constants():
    """The salary rule moved to admin-only; commission deliberately did NOT."""
    assert payroll_mod._SALARY_CROSS_EMPLOYEE_ROLES == ("SUPERADMIN", "ADMIN")
    assert payroll_mod._COMMISSION_MANAGER_ROLES == (
        "SUPERADMIN",
        "ADMIN",
        "AREA_MANAGER",
        "STORE_MANAGER",
        "ACCOUNTANT",
    )
    # Commission behaviour is unchanged for the manager tier ...
    assert payroll_mod._is_commission_manager({"roles": ["STORE_MANAGER"]})
    assert payroll_mod._is_commission_manager({"roles": ["ACCOUNTANT"]})
    # ... while the same roles are refused someone else's salary.
    for role in NON_ADMIN_PAY_ROLES:
        with pytest.raises(Exception) as exc:
            payroll_mod._assert_self_or_salary_admin(
                OTHER_ID, {"user_id": SELF_ID, "roles": [role]}
            )
        assert getattr(exc.value, "status_code", None) == 403
    assert not payroll_mod._is_commission_manager({"roles": ["CASHIER"]})
    assert not payroll_mod._is_commission_manager({})


# ===========================================================================
# B + C. HR attendance store scope and roster labels
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


# E4 and E5 are MULTI-STORE employees whose store_ids[0] is OUTSIDE a BV
# manager's reach -- the shape that produced the mislabelled WO-MUM-01 summary
# row. Keep them: without a multi-store employee this suite cannot see the bug.
_USERS = [
    {"user_id": "E1", "full_name": "Asha", "store_ids": [STORE_A], "is_active": True},
    {"user_id": "E2", "full_name": "Bina", "store_ids": [STORE_B], "is_active": True},
    {"user_id": "E3", "full_name": "Chan", "store_ids": [STORE_C], "is_active": True},
    {
        "user_id": "E4",
        "full_name": "Dev",
        "store_ids": [STORE_C, STORE_A],
        "is_active": True,
    },
    {
        "user_id": "E5",
        "full_name": "Esha",
        "store_ids": [STORE_C, STORE_B],
        "is_active": True,
    },
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
    {
        "attendance_id": "a4",
        "employee_id": "E4",
        "date": "2026-05-04",
        "status": "PRESENT",
        "store_id": STORE_A,
    },
]


@pytest.fixture()
def hr_client(monkeypatch):
    monkeypatch.setattr(hr_mod, "get_user_repository", lambda: _FakeUserRepo(_USERS))
    monkeypatch.setattr(
        hr_mod, "get_attendance_repository", lambda: _FakeAttendanceRepo(_RECORDS)
    )
    return TestClient(_mounted(hr_mod.router, "/hr"))


def _stores_in(records_json):
    return {r["storeId"] for r in records_json["records"]}


def _roster_pins(grid_json):
    return {e["employee_id"]: e["store_id"] for e in grid_json["employees"]}


def test_storeless_manager_gets_empty_attendance_not_org_wide(hr_client):
    """The fail-open. A manager with NO store_ids and NO active store used to get
    every store's attendance; now an empty list -- and a 200, not a 403."""
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
    # E3 is WizOpt-only and stays out; E4/E5 are in via their second store.
    assert set(_roster_pins(r.json())) == {"E1", "E2", "E4", "E5"}


def test_single_store_manager_roster_is_labelled_with_their_own_store(hr_client):
    """REGRESSION (panel MUST-FIX 1). E4's store_ids[0] is WO-MUM-01, a different
    chain / city / legal entity. A Pune-only STORE_MANAGER must never see that
    label -- pre-fix the roster row said WO-MUM-01 and Pune's headcount was
    short by one."""
    tok = _token(
        ["STORE_MANAGER"], user_id="u-sm", store_ids=[STORE_A], active_store=STORE_A
    )
    r = hr_client.get(
        "/hr/attendance/grid", params={"month": "2026-05"}, headers=_auth(tok)
    )
    assert r.status_code == 200, r.text
    pins = _roster_pins(r.json())
    assert pins == {"E1": STORE_A, "E4": STORE_A}
    assert STORE_C not in set(pins.values())


def test_multi_store_manager_roster_labels_each_row_within_reach(hr_client):
    """Same regression for a multi-store caller: each employee is labelled with
    the store THIS caller shares with them, not store_ids[0]."""
    tok = _token(
        ["AREA_MANAGER"],
        user_id="u-am",
        store_ids=[STORE_A, STORE_B],
        active_store=STORE_A,
    )
    r = hr_client.get(
        "/hr/attendance/grid", params={"month": "2026-05"}, headers=_auth(tok)
    )
    assert _roster_pins(r.json()) == {
        "E1": STORE_A,
        "E2": STORE_B,
        "E4": STORE_A,
        "E5": STORE_B,
    }


def test_summary_store_buckets_carry_no_out_of_reach_store(hr_client):
    """The panel's reproduction, as a rollup assertion: a single-store manager's
    summary must contain exactly ONE store row, with the right headcount."""
    tok = _token(
        ["STORE_MANAGER"], user_id="u-sm", store_ids=[STORE_A], active_store=STORE_A
    )
    r = hr_client.get(
        "/hr/attendance/summary", params={"month": "2026-05"}, headers=_auth(tok)
    )
    assert r.status_code == 200, r.text
    buckets = {s["store_id"]: s["employees"] for s in r.json()["stores"]}
    assert buckets == {STORE_A: 2}


def test_admin_roster_keeps_the_legacy_first_store_label(hr_client):
    """Cross-store callers are unconstrained, so the legacy store_ids[0] label
    is still correct for them -- the fix must not move ADMIN's behaviour."""
    tok = _token(["ADMIN"], user_id="u-admin", store_ids=[], active_store=None)
    r = hr_client.get(
        "/hr/attendance/grid", params={"month": "2026-05"}, headers=_auth(tok)
    )
    assert _roster_pins(r.json()) == {
        "E1": STORE_A,
        "E2": STORE_B,
        "E3": STORE_C,
        "E4": STORE_C,
        "E5": STORE_C,
    }


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
    tok = _token(["ADMIN"], user_id="u-admin", store_ids=[], active_store=None)
    r = hr_client.get("/hr/attendance", headers=_auth(tok))
    assert r.status_code == 200
    assert _stores_in(r.json()) == {STORE_A, STORE_B, STORE_C}


def test_explicit_own_store_still_pins_every_roster_row(hr_client):
    """A single named store keeps the scalar path (all rows pinned to it)."""
    tok = _token(
        ["STORE_MANAGER"], user_id="u-sm", store_ids=[STORE_A], active_store=STORE_A
    )
    r = hr_client.get(
        "/hr/attendance/grid",
        params={"month": "2026-05", "store_id": STORE_A},
        headers=_auth(tok),
    )
    assert r.status_code == 200
    assert _roster_pins(r.json()) == {"E1": STORE_A, "E4": STORE_A}


def test_store_scope_filter_shape():
    """Cross-store -> None; empty reach -> a clause that matches NOTHING."""
    assert hr_mod._store_scope_filter({"roles": ["ADMIN"]}) is None
    assert hr_mod._store_scope_filter({"roles": ["SUPERADMIN"]}) is None
    assert hr_mod._store_scope_filter(
        {"roles": ["STORE_MANAGER"], "store_ids": [], "active_store_id": None}
    ) == {"$in": []}
    assert hr_mod._store_scope_filter(
        {"roles": ["AREA_MANAGER"], "store_ids": [STORE_B], "active_store_id": STORE_A}
    ) == {"$in": sorted([STORE_A, STORE_B])}


@pytest.mark.parametrize(
    "junk",
    [
        [None, STORE_A],
        [STORE_A, 7],
        [STORE_A, ""],
        [STORE_A, STORE_A],
    ],
)
def test_store_scope_filter_survives_junk_store_ids(junk):
    """MUST-FIX 3: store_ids is unvalidated data. A bare ``sorted()`` raises
    TypeError on a mixed-type list and 500s the attendance screen."""
    scope = hr_mod._store_scope_filter(
        {"roles": ["STORE_MANAGER"], "store_ids": junk, "active_store_id": None}
    )
    assert scope == {"$in": [STORE_A]}


@pytest.mark.parametrize("junk", [[{"a": 1}, STORE_A], [["nested"], STORE_A]])
def test_unhashable_store_ids_still_crash_UPSTREAM_not_in_this_router(junk):
    """HONEST LIMIT, recorded rather than implied by a green suite.

    An UNHASHABLE element dies one level up, in ``dependencies.user_store_scope``
    (``set(current_user.get("store_ids") or [])``), before this router's
    isinstance filter can run. That helper is shared -- users._store_scope_filter
    from PR #967 sits behind exactly the same call -- and it is outside this PR's
    file ownership, so it is reported instead of patched here. The filter in this
    router still fixes every shape that reaches it.
    """
    with pytest.raises(TypeError):
        hr_mod._store_scope_filter(
            {"roles": ["STORE_MANAGER"], "store_ids": junk, "active_store_id": None}
        )


def test_junk_store_ids_do_not_500_the_live_route(hr_client):
    tok = _token(
        ["STORE_MANAGER"], user_id="u-sm", store_ids=[None, STORE_A], active_store=None
    )
    r = hr_client.get("/hr/attendance", headers=_auth(tok))
    assert r.status_code == 200, r.text
    assert _stores_in(r.json()) == {STORE_A}


def test_scope_store_set_shape():
    assert hr_mod._scope_store_set({"$in": [STORE_A, STORE_B]}) == {STORE_A, STORE_B}
    assert hr_mod._scope_store_set({"$in": []}) == set()
    assert hr_mod._scope_store_set(STORE_A) is None
    assert hr_mod._scope_store_set(None) is None


def test_hr_attendance_unauthenticated_still_401(hr_client):
    assert hr_client.get("/hr/attendance").status_code == 401
