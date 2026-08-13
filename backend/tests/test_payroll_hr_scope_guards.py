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
from api.routers import hr_self_service as hr_self_mod  # noqa: E402
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
        # Every mutation counted, so a gate test can assert "403 AND nothing
        # was written" -- a 403 with a write behind it is not a closed door.
        self.writes = 0

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
        self.writes += 1
        return type("_R", (), {"inserted_id": "fake"})()

    def update_many(self, query=None, update=None):
        """Enough of pymongo's update_many for the approve / lock writes."""
        changed = 0
        for d in self.docs:
            if _matches(d, query or {}):
                d.update((update or {}).get("$set", {}))
                changed += 1
        self.writes += changed
        return type("_R", (), {"modified_count": changed})()

    def update_one(self, query=None, update=None):
        for d in self.docs:
            if _matches(d, query or {}):
                d.update((update or {}).get("$set", {}))
                self.writes += 1
                return type("_R", (), {"modified_count": 1})()
        return type("_R", (), {"modified_count": 0})()


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
            # MF4: commission aggregates from ORDERS. Without these the
            # commission payload test ran against an empty item list and could
            # not fail -- the chair injected net_pay + a bank account into every
            # row and all 135 tests still passed.
            "orders": _FakeColl(
                [
                    {
                        "order_id": "ORD-1",
                        "status": "COMPLETED",
                        "created_at": datetime(2026, 5, 4, 10, 0),
                        "store_id": STORE_A,
                        "sales_staff_id": SELF_ID,
                        "sales_staff_name": "Own Staffer",
                        "total_amount": 12000.0,
                    },
                    {
                        "order_id": "ORD-2",
                        "status": "COMPLETED",
                        "created_at": datetime(2026, 5, 9, 12, 0),
                        "store_id": STORE_A,
                        "sales_staff_id": OTHER_ID,
                        "sales_staff_name": "Colleague",
                        "total_amount": 8000.0,
                    },
                    {
                        "order_id": "ORD-3",
                        "status": "DELIVERED",
                        "created_at": datetime(2026, 5, 21, 16, 30),
                        "store_id": STORE_A,
                        "sales_staff_id": SELF_ID,
                        "sales_staff_name": "Own Staffer",
                        "total_amount": 5000.0,
                    },
                ]
            ),
            "salary_advances": _FakeColl(
                [
                    {
                        "advance_id": "adv-1",
                        "employee_id": OTHER_ID,
                        "amount": 5000.0,
                        "status": "PENDING",
                    },
                    {
                        "advance_id": "adv-other",
                        "employee_id": OTHER_ID,
                        "amount": 7500.0,
                        "status": "pending",
                    },
                ]
            ),
            "incentives": _FakeColl(
                [
                    {
                        "staff_id": OTHER_ID,
                        "month": 5,
                        "year": 2026,
                        "incentive_amount": 4200.0,
                    }
                ]
            ),
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
# A2. THE FULL SALARY FAMILY (owner decision 2026-08-10: close everything)
# ===========================================================================
# Per-employee reads -> SELF or ADMIN. Aggregate reads (a whole store's pay, the
# run register, the statutory exports) have no "self" row -> ADMIN only.

_PER_EMPLOYEE_SALARY_PATHS = (
    "/payroll/payslip/{eid}",
    "/payroll/payslip/{eid}/5/2026",
    "/payroll/payslip/{eid}/5/2026/print",
    "/payroll/config/{eid}",
    "/payroll/advances/{eid}",
    "/payroll/incentive-summary/{eid}/5/2026",
)

_AGGREGATE_SALARY_PATHS = (
    "/payroll/config",
    "/payroll/salary-sheet?month=5&year=2026",
    "/payroll/run/rows?month=5&year=2026",
    "/payroll/registers/summary?month=5&year=2026",
    "/payroll/tally/salary-jv?month=5&year=2026",
    "/payroll/registers/pf-ecr?month=5&year=2026",
)


@pytest.mark.parametrize("template", _PER_EMPLOYEE_SALARY_PATHS)
@pytest.mark.parametrize("role", NON_ADMIN_PAY_ROLES)
def test_per_employee_salary_reads_are_self_or_admin(payroll_client, template, role):
    """Every per-employee salary read refuses a colleague's id for non-admins."""
    tok = _token([role], user_id=SELF_ID, store_ids=[STORE_A])
    r = payroll_client.get(template.format(eid=OTHER_ID), headers=_auth(tok))
    assert r.status_code == 403, f"{template}: {r.text}"
    for leak in ("99000", "SECRET-222", "5000", "4200"):
        assert leak not in r.text, f"{template} leaked {leak}"


@pytest.mark.parametrize("template", _PER_EMPLOYEE_SALARY_PATHS)
@pytest.mark.parametrize("role", NON_ADMIN_PAY_ROLES)
def test_per_employee_salary_reads_still_allow_self(payroll_client, template, role):
    tok = _token([role], user_id=SELF_ID, store_ids=[STORE_A])
    r = payroll_client.get(template.format(eid=SELF_ID), headers=_auth(tok))
    assert r.status_code == 200, f"{template}: {r.text}"


@pytest.mark.parametrize("path", _AGGREGATE_SALARY_PATHS)
@pytest.mark.parametrize("role", NON_ADMIN_PAY_ROLES)
def test_aggregate_salary_reads_are_admin_only(payroll_client, path, role):
    """A store's salary sheet / the run register / the statutory exports have no
    self version -- they are closed outright below ADMIN."""
    tok = _token([role], user_id=SELF_ID, store_ids=[STORE_A])
    r = payroll_client.get(path, headers=_auth(tok))
    assert r.status_code == 403, f"{path}: {r.text}"
    assert "99000" not in r.text and "SECRET-222" not in r.text


@pytest.mark.parametrize("path", _AGGREGATE_SALARY_PATHS)
def test_aggregate_salary_reads_still_work_for_admin(payroll_client, path):
    tok = _token(["ADMIN"], user_id="u-admin", store_ids=[])
    r = payroll_client.get(path, headers=_auth(tok))
    assert r.status_code == 200, f"{path}: {r.text}"


def test_payroll_run_is_admin_only_because_its_response_returns_rows(payroll_client):
    """POST /payroll/run is a WRITE, but its response body carries every
    employee's breakdown -- so the same rule applies to it."""
    body = {"month": 5, "year": 2026, "dry_run": True}
    acct = _token(["ACCOUNTANT"], user_id=SELF_ID, store_ids=[STORE_A])
    r = payroll_client.post("/payroll/run", json=body, headers=_auth(acct))
    assert r.status_code == 403, r.text
    assert "rows" not in r.json()


def test_deleted_salary_route_is_gone(payroll_client):
    """GET /payroll/salary/{employee_id} was REMOVED, not restricted: it served
    raw bank_account_no / pan / ctc_annual. Even an ADMIN gets 404 now."""
    for role in ("ADMIN", "SUPERADMIN", "ACCOUNTANT"):
        tok = _token([role], user_id="u-x", store_ids=[STORE_A])
        r = payroll_client.get(f"/payroll/salary/{OTHER_ID}", headers=_auth(tok))
        assert r.status_code == 404, f"{role}: {r.status_code} {r.text}"


def test_deleted_route_has_no_stale_rbac_policy_row():
    """The POLICY row must die with the route -- tests/test_rbac_policy.py checks
    parity in BOTH directions, so a stale row fails CI."""
    assert rbac_policy.policy_for("GET", "/api/v1/payroll/salary/emp-1") is None


def test_commission_payload_carries_no_salary_figure(payroll_client):
    """For the record: commission is a SALES surface. Its rows expose revenue and
    a commission rate/amount -- never a salary, CTC or net pay -- and the
    salary_config read behind it is a PROJECTION fetching only
    commission_rate_percent."""
    tok = _token(["STORE_MANAGER"], user_id=SELF_ID, store_ids=[STORE_A])
    r = payroll_client.get(
        "/payroll/commission/summary",
        params={"month": 5, "year": 2026},
        headers=_auth(tok),
    )
    assert r.status_code == 200, r.text
    banned = (
        "net_pay",
        "ctc",
        "basic",
        "hra",
        "gross_salary",
        "bank_account",
        "pf_employee",
        "professional_tax",
        "salary",
    )
    body = r.text.lower()
    for field in banned:
        assert field not in body, f"commission payload exposed {field}"
    items = r.json()["items"]
    # MF4: without this the loop below iterates nothing and the test cannot fail.
    assert items, "fixture must produce commission rows or this test is vacuous"
    assert any(i["revenue"] for i in items), "rows must carry real revenue"
    for item in items:
        assert set(item) <= {
            "employee_id",
            "name",
            "store_id",
            "sales_count",
            "revenue",
            "commission_rate_percent",
            "commission_amount",
            "rank",
            "recent_orders",
        }, item


def test_hr_payroll_list_is_admin_only(hr_client):
    """GET /hr/payroll returns base_salary / gross / deductions / net per named
    employee, and reuses the payroll router's gate so the two cannot drift."""
    for role in NON_ADMIN_PAY_ROLES:
        tok = _token([role], user_id=SELF_ID, store_ids=[STORE_A])
        r = hr_client.get(
            "/hr/payroll", params={"year": 2026, "month": 5}, headers=_auth(tok)
        )
        assert r.status_code == 403, f"{role}: {r.text}"
    tok = _token(["ADMIN"], user_id="u-admin", store_ids=[])
    assert (
        hr_client.get(
            "/hr/payroll", params={"year": 2026, "month": 5}, headers=_auth(tok)
        ).status_code
        == 200
    )


def test_hr_salary_slip_stub_is_self_or_admin(hr_client):
    tok = _token(["STORE_MANAGER"], user_id=SELF_ID, store_ids=[STORE_A])
    other = hr_client.get(
        f"/hr/employee/{OTHER_ID}/salary-slip",
        params={"year": 2026, "month": 5},
        headers=_auth(tok),
    )
    assert other.status_code == 403, other.text
    own = hr_client.get(
        f"/hr/employee/{SELF_ID}/salary-slip",
        params={"year": 2026, "month": 5},
        headers=_auth(tok),
    )
    assert own.status_code == 200, own.text


def test_salary_403_detail_is_plain_english_for_the_screen():
    """The frontend shows these strings verbatim (PayrollAccessNotice), so they
    must read as a permission message, not as an error code."""
    with pytest.raises(Exception) as exc:
        payroll_mod._assert_salary_admin({"roles": ["ACCOUNTANT"]})
    detail = exc.value.detail
    assert "administrator" in detail.lower()
    assert detail.endswith(".")
    with pytest.raises(Exception) as exc2:
        payroll_mod._assert_self_or_salary_admin(
            OTHER_ID, {"user_id": SELF_ID, "roles": ["ACCOUNTANT"]}, "salary advances"
        )
    assert "salary advances" in exc2.value.detail


def test_floor_staff_self_service_payslip_is_untouched(monkeypatch):
    """THE PROMISE THE WHOLE RULE RESTS ON: everybody can still see their OWN pay.

    /hr/me/payslip is mounted WITHOUT the finance-roles gate (api/main.py mounts
    hr_self_service_router separately), reads only the caller's own id, and is
    what the /my-work page uses. If closing the payroll routes ever took this
    with it, every employee would lose sight of their own salary -- so it is
    asserted here next to the closures rather than trusted.
    """
    db = _payroll_db()
    monkeypatch.setattr(hr_self_mod, "_get_db", lambda: db)
    app = FastAPI()
    app.include_router(hr_self_mod.router, prefix="/hr")  # no gate, as in main.py
    c = TestClient(app)

    tok = _token(["CASHIER"], user_id=SELF_ID, store_ids=[STORE_A])
    r = c.get("/hr/me/payslip", headers=_auth(tok))
    assert r.status_code == 200, r.text
    slip = r.json()["payslip"]
    assert slip is not None and slip["employee_id"] == SELF_ID
    assert slip["breakdown"]["net_pay"] == 21000.0
    # ... and it is genuinely self-only: the colleague's numbers never appear.
    assert "99000" not in r.text and "SECRET-222" not in r.text


# ===========================================================================
# A3. PAYROLL SIGN-OFF (owner ruling 2026-08-10)
# ===========================================================================
# "Whoever approves payroll should be able to see what they are approving."
# There are TWO approve routes, in different routers, and the HR one had the
# WIDER gate of the pair (_HR_READ_ROLES let a STORE_MANAGER approve).
# POST /payroll/lock was already ADMIN-only and is asserted here so the whole
# sign-off family is pinned in one place.


@pytest.mark.parametrize("role", NON_ADMIN_PAY_ROLES)
def test_payroll_approve_is_admin_only(payroll_client, role):
    """All three manager-tier roles are refused -- but by different layers, and
    the test says which rather than pretending one gate does it all.

    AREA_MANAGER / STORE_MANAGER never passed this route's own
    ``require_roles(*_RUN_ROLES)``, so they get that gate's generic 403. The
    ACCOUNTANT does pass it and is refused by _assert_salary_admin, which is the
    gate this ruling added and the one whose wording reaches the toast.
    """
    r = payroll_client.post(
        "/payroll/approve",
        json={"month": 5, "year": 2026},
        headers=_auth(_token([role], user_id=SELF_ID, store_ids=[STORE_A])),
    )
    assert r.status_code == 403, f"{role}: {r.text}"
    if role == "ACCOUNTANT":
        assert "administrator" in r.text.lower()
        assert "approve a payroll run" in r.text


@pytest.mark.parametrize("role", NON_ADMIN_PAY_ROLES)
def test_payroll_lock_is_admin_only(payroll_client, role):
    """Already ADMIN-only before this round -- asserted so the family cannot
    drift apart later."""
    r = payroll_client.post(
        "/payroll/lock",
        json={"month": 5, "year": 2026},
        headers=_auth(_token([role], user_id=SELF_ID, store_ids=[STORE_A])),
    )
    assert r.status_code == 403, f"{role}: {r.text}"


@pytest.mark.parametrize("role", ADMIN_PAY_ROLES)
def test_payroll_approve_still_works_for_admin(payroll_client, role):
    r = payroll_client.post(
        "/payroll/approve",
        json={"month": 5, "year": 2026},
        headers=_auth(_token([role], user_id="u-admin", store_ids=[])),
    )
    assert r.status_code == 200, f"{role}: {r.text}"


@pytest.mark.parametrize("role", NON_ADMIN_PAY_ROLES)
def test_hr_payroll_approve_is_admin_only(hr_client, role):
    """The SECOND approve route. Its old gate (_HR_READ_ROLES) also admitted
    AREA_MANAGER and STORE_MANAGER, not just the accountant."""
    r = hr_client.post(
        "/hr/payroll/pr-1/approve",
        headers=_auth(_token([role], user_id=SELF_ID, store_ids=[STORE_A])),
    )
    assert r.status_code == 403, f"{role}: {r.text}"
    # _HR_READ_ROLES admitted all three, so here the NEW gate is what refuses
    # every one of them -- and its wording is what the user sees.
    assert "administrator" in r.text.lower()
    assert "approve a payroll record" in r.text


def test_hr_payroll_approve_reaches_the_handler_for_admin(hr_client):
    """An ADMIN gets past the gate. The fake repo has no such record, so the
    handler's own 404 is the proof it was reached -- not a 403."""
    r = hr_client.post(
        "/hr/payroll/pr-does-not-exist/approve",
        headers=_auth(_token(["ADMIN"], user_id="u-admin", store_ids=[])),
    )
    assert r.status_code != 403, r.text


def test_signoff_403_names_the_action_not_just_the_data():
    """The toast must read as a permission message about THIS action."""
    with pytest.raises(Exception) as exc:
        payroll_mod._assert_salary_admin(
            {"roles": ["ACCOUNTANT"]}, "approve a payroll run"
        )
    detail = exc.value.detail
    assert "approve a payroll run" in detail
    assert "administrator" in detail.lower()
    # ... and the no-action form still reads as the data message.
    with pytest.raises(Exception) as exc2:
        payroll_mod._assert_salary_admin({"roles": ["ACCOUNTANT"]})
    assert "restricted to administrators" in exc2.value.detail


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


# ===========================================================================
# D. SIBLING SWEEP (round 5) -- the same two bug classes, one file over
# ===========================================================================
# MF1: the falsy-scope fail-open was fixed on three attendance reads and left
# open on three siblings in the SAME file. MF2: two payroll WRITES were left
# ungated, so a manager could author rows that an ADMIN then blanket-approves
# and that feed the PF ECR and the statutory filing -- the exact inverse of
# this PR's own "an approval on figures you cannot read is a rubber stamp".


class _FakeLeaveRepo:
    def __init__(self, leaves):
        self._leaves = [dict(x) for x in leaves]

    def find_many(self, filter=None, sort=None, skip=0, limit=100):
        return [dict(x) for x in self._leaves if _matches(x, filter or {})][:limit]


_LATE_RECORDS = [
    {
        "attendance_id": "L1",
        "employee_id": "E1",
        "employee_name": "Asha Own-Store",
        "date": "2026-05-06",
        "status": "PRESENT",
        "is_late": True,
        "late_minutes": 20,
        "store_id": STORE_A,
    },
    {
        "attendance_id": "L2",
        "employee_id": "E3",
        "employee_name": "Colleague Person",
        "date": "2026-05-07",
        "status": "PRESENT",
        "is_late": True,
        "late_minutes": 35,
        "store_id": STORE_C,
    },
]

_LEAVES = [
    {
        "leave_id": "LV-1",
        "employee_id": "E1",
        "employee_name": "Asha Own-Store",
        "store_id": STORE_A,
        "leave_type": "CASUAL",
        "status": "APPROVED",
        "from_date": "2026-05-02",
        "to_date": "2026-05-02",
        "reason": "own store errand",
    },
    {
        "leave_id": "LV-2",
        "employee_id": "E3",
        "employee_name": "Colleague Person",
        "store_id": STORE_C,
        "leave_type": "UNPAID",
        "status": "APPROVED",
        "from_date": "2026-05-11",
        "to_date": "2026-05-13",
        "reason": "family matter",
    },
]


@pytest.fixture()
def hr_reports_client(monkeypatch):
    """hr_client plus a leave repo and late-marked attendance."""
    monkeypatch.setattr(hr_mod, "get_user_repository", lambda: _FakeUserRepo(_USERS))
    monkeypatch.setattr(
        hr_mod,
        "get_attendance_repository",
        lambda: _FakeAttendanceRepo(_RECORDS + _LATE_RECORDS),
    )
    monkeypatch.setattr(hr_mod, "get_leave_repository", lambda: _FakeLeaveRepo(_LEAVES))
    return TestClient(_mounted(hr_mod.router, "/hr"))


_STORELESS_ACCOUNTANT = ("ACCOUNTANT", [], None)


def _storeless(role="ACCOUNTANT"):
    """The shape hr._store_scope_filter's own docstring calls ordinary: a
    store-scoped account with an empty store_ids and no active store."""
    return _token([role], user_id="u-storeless", store_ids=[], active_store=None)


def test_storeless_manager_late_marks_report_is_empty_not_org_wide(hr_reports_client):
    """MF1 sibling 1. Pre-fix this returned WO-MUM-01's staff to a manager with
    no store at all -- a different chain, city and legal entity."""
    r = hr_reports_client.get(
        "/hr/attendance/late-marks",
        params={"month": "2026-05"},
        headers=_auth(_storeless()),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["employees"] == []
    assert body["total_late_marks"] == 0
    assert "Colleague Person" not in r.text
    assert STORE_C not in r.text


def test_storeless_manager_lwp_report_is_empty_not_org_wide(hr_reports_client):
    """MF1 sibling 2. Pre-fix this leaked both employees WITH their unpaid-day
    counts -- the number that drives a salary deduction."""
    r = hr_reports_client.get(
        "/hr/reports/lwp",
        params={"year": 2026, "month": 5},
        headers=_auth(_storeless()),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["employees"] == []
    assert body["total_lwp_days"] == 0.0
    assert "Colleague Person" not in r.text


def test_storeless_manager_leaves_list_is_empty_not_org_wide(hr_reports_client):
    """MF1 sibling 3. Pre-fix this leaked full leave rows including store_id,
    the UNPAID type and the free-text reason ("family matter")."""
    r = hr_reports_client.get("/hr/leaves", headers=_auth(_storeless()))
    assert r.status_code == 200, r.text
    assert r.json() == {"leaves": [], "total": 0}
    assert "family matter" not in r.text


def test_reports_still_work_inside_the_callers_reach(hr_reports_client):
    """Availability half: a manager WITH stores still gets their own data, and
    still does not get the store outside their reach."""
    tok = _token(
        ["ACCOUNTANT"], user_id="u-acct", store_ids=[STORE_A], active_store=STORE_A
    )
    late = hr_reports_client.get(
        "/hr/attendance/late-marks", params={"month": "2026-05"}, headers=_auth(tok)
    )
    assert late.status_code == 200
    assert [e["employee_id"] for e in late.json()["employees"]] == ["E1"]
    assert "Colleague Person" not in late.text

    leaves = hr_reports_client.get("/hr/leaves", headers=_auth(tok))
    assert leaves.status_code == 200
    assert [x["leave_id"] for x in leaves.json()["leaves"]] == ["LV-1"]
    assert "family matter" not in leaves.text

    lwp = hr_reports_client.get(
        "/hr/reports/lwp", params={"year": 2026, "month": 5}, headers=_auth(tok)
    )
    assert lwp.status_code == 200
    assert {e["employee_id"] for e in lwp.json()["employees"]} == {"E1", "E4"}


def test_admin_keeps_org_wide_reach_on_the_reports(hr_reports_client):
    tok = _token(["ADMIN"], user_id="u-admin", store_ids=[], active_store=None)
    late = hr_reports_client.get(
        "/hr/attendance/late-marks", params={"month": "2026-05"}, headers=_auth(tok)
    )
    assert {e["employee_id"] for e in late.json()["employees"]} == {"E1", "E3"}
    leaves = hr_reports_client.get("/hr/leaves", headers=_auth(tok))
    assert {x["leave_id"] for x in leaves.json()["leaves"]} == {"LV-1", "LV-2"}


def test_explicit_cross_store_still_403_on_the_reports(hr_reports_client):
    tok = _token(
        ["ACCOUNTANT"], user_id="u-acct", store_ids=[STORE_A], active_store=STORE_A
    )
    for path, params in (
        ("/hr/attendance/late-marks", {"month": "2026-05", "store_id": STORE_C}),
        ("/hr/reports/lwp", {"year": 2026, "month": 5, "store_id": STORE_C}),
        ("/hr/leaves", {"store_id": STORE_C}),
    ):
        r = hr_reports_client.get(path, params=params, headers=_auth(tok))
        assert r.status_code == 403, f"{path}: {r.text}"


# --------------------------------------------------------------------- MF2 --


class _FakePayrollRepo:
    def __init__(self):
        self.created = []

    def find_one(self, flt):
        return None

    def find_many(self, flt=None, sort=None, skip=0, limit=100):
        return []

    def find_by_id(self, pid):
        return None

    def create(self, doc):
        self.created.append(dict(doc))
        return doc


@pytest.fixture()
def hr_write_client(monkeypatch):
    repo = _FakePayrollRepo()
    monkeypatch.setattr(hr_mod, "get_user_repository", lambda: _FakeUserRepo(_USERS))
    monkeypatch.setattr(
        hr_mod, "get_attendance_repository", lambda: _FakeAttendanceRepo(_RECORDS)
    )
    monkeypatch.setattr(hr_mod, "get_payroll_repository", lambda: repo)
    client = TestClient(_mounted(hr_mod.router, "/hr"))
    return client, repo


@pytest.mark.parametrize("role", NON_ADMIN_PAY_ROLES)
def test_hr_payroll_generate_is_admin_only(hr_write_client, role):
    """MF2 write 1. A STORE_MANAGER could author DRAFT payroll rows from a naive
    base_salary/26 * present_days with a flat 10% deduction -- rows they cannot
    then read back, that an ADMIN blanket-approves, and that feed the PF ECR and
    the statutory filing. Authoring salary is now ADMIN-only, matching the read
    sibling GET /hr/payroll."""
    client, repo = hr_write_client
    r = client.post(
        "/hr/payroll/generate",
        params={"year": 2026, "month": 5},
        headers=_auth(_token([role], user_id=SELF_ID, store_ids=[STORE_A])),
    )
    assert r.status_code == 403, f"{role}: {r.text}"
    assert repo.created == [], f"{role} authored {len(repo.created)} payroll rows"


def test_hr_payroll_generate_still_works_for_admin(hr_write_client):
    client, repo = hr_write_client
    r = client.post(
        "/hr/payroll/generate",
        params={"year": 2026, "month": 5},
        headers=_auth(_token(["ADMIN"], user_id="u-admin", store_ids=[])),
    )
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("role", NON_ADMIN_PAY_ROLES)
def test_salary_calculate_is_admin_only(payroll_client, role):
    """MF2 write 2. POST /payroll/salary/calculate returned 201 for ANOTHER
    employee's id, stamping the row with the CALLER's store. The response body
    carries no salary, so this is not a disclosure -- it is authorship of a
    salary_records row that get_salary_sheet then reads back."""
    r = payroll_client.post(
        "/payroll/salary/calculate",
        json={
            "employee_id": OTHER_ID,
            "month": 5,
            "year": 2026,
            "working_days": 26,
            "leave_without_pay_days": 0,
        },
        headers=_auth(_token([role], user_id=SELF_ID, store_ids=[STORE_A])),
    )
    assert r.status_code == 403, f"{role}: {r.text}"


@pytest.mark.parametrize("role", NON_ADMIN_PAY_ROLES)
def test_salary_calculate_refuses_even_your_own_id(payroll_client, role):
    """ADMIN-only, NOT self-or-admin. Every other salary route allows SELF
    because seeing your own pay is harmless -- authoring it is not."""
    r = payroll_client.post(
        "/payroll/salary/calculate",
        json={
            "employee_id": SELF_ID,
            "month": 5,
            "year": 2026,
            "working_days": 26,
            "leave_without_pay_days": 0,
        },
        headers=_auth(_token([role], user_id=SELF_ID, store_ids=[STORE_A])),
    )
    assert r.status_code == 403, f"{role}: {r.text}"


def test_salary_calculate_still_works_for_admin(payroll_client):
    r = payroll_client.post(
        "/payroll/salary/calculate",
        json={
            "employee_id": OTHER_ID,
            "month": 5,
            "year": 2026,
            "working_days": 26,
            "leave_without_pay_days": 0,
        },
        headers=_auth(_token(["ADMIN"], user_id="u-admin", store_ids=[])),
    )
    assert r.status_code in (200, 201), r.text


# ===========================================================================
# E. SALARY-ADVANCE WRITES (round 6)
# ===========================================================================
# The read GET /payroll/advances/{id} was gated in round 3 with the docstring
# "an outstanding advance is a deduction from pay, so it is salary data." Its
# two WRITE twins, twelve lines away, stayed on Depends(get_current_user): any
# manager-tier caller could CREATE an advance against any employee org-wide
# (_get_employee_details does a bare find_one with no store scope, and the row
# is stamped with the CALLER's active_store_id, so it crosses stores AND legal
# entities) and could mark any advance settled -- while being unable to read
# either back. No money moves today (nothing in the payroll run reads
# salary_advances), but settle answers "Advance settled and will be deducted
# from salary", a promise the code does not yet keep. Gating it now is what
# stops that becoming real the day someone wires the deduction.


def _advances_coll(payroll_client):
    """The seeded salary_advances collection behind the client fixture."""
    return payroll_mod._get_db().get_collection("salary_advances")


@pytest.mark.parametrize("role", NON_ADMIN_PAY_ROLES)
def test_advance_create_is_admin_only(payroll_client, role):
    """403 AND zero writes -- a 403 with a row behind it is not a closed door."""
    coll = _advances_coll(payroll_client)
    before = coll.writes
    r = payroll_client.post(
        "/payroll/advances",
        json={
            "employee_id": OTHER_ID,
            "amount": 4000.0,
            "date_requested": "2026-05-04",
            "reason": "fabricated by a non-admin",
        },
        headers=_auth(_token([role], user_id=SELF_ID, store_ids=[STORE_A])),
    )
    assert r.status_code == 403, f"{role}: {r.text}"
    assert coll.writes == before, f"{role} wrote {coll.writes - before} advance row(s)"


@pytest.mark.parametrize("role", NON_ADMIN_PAY_ROLES)
def test_advance_create_refuses_even_your_own_id(payroll_client, role):
    """ADMIN-only, not self-or-admin: authoring a deduction against yourself is
    still authoring pay data (and would be the obvious way to launder one)."""
    coll = _advances_coll(payroll_client)
    before = coll.writes
    r = payroll_client.post(
        "/payroll/advances",
        json={
            "employee_id": SELF_ID,
            "amount": 4000.0,
            "date_requested": "2026-05-04",
            "reason": "own id",
        },
        headers=_auth(_token([role], user_id=SELF_ID, store_ids=[STORE_A])),
    )
    assert r.status_code == 403, f"{role}: {r.text}"
    assert coll.writes == before


@pytest.mark.parametrize("role", NON_ADMIN_PAY_ROLES)
def test_advance_settle_is_admin_only(payroll_client, role):
    coll = _advances_coll(payroll_client)
    before = coll.writes
    r = payroll_client.post(
        "/payroll/advances/adv-other/settle",
        json={"advance_id": "adv-other", "settlement_month": 5, "settlement_year": 2026},
        headers=_auth(_token([role], user_id=SELF_ID, store_ids=[STORE_A])),
    )
    assert r.status_code == 403, f"{role}: {r.text}"
    assert coll.writes == before, f"{role} settled an advance it cannot read"
    # ... and the row is untouched.
    assert coll.find_one({"advance_id": "adv-other"})["status"] == "pending"


@pytest.mark.parametrize("role", ADMIN_PAY_ROLES)
def test_advance_writes_still_work_for_admin(payroll_client, role):
    created = payroll_client.post(
        "/payroll/advances",
        json={
            "employee_id": OTHER_ID,
            "amount": 4000.0,
            "date_requested": "2026-05-04",
            "reason": "legitimate",
        },
        headers=_auth(_token([role], user_id="u-admin", store_ids=[])),
    )
    assert created.status_code == 201, f"{role}: {created.text}"
    settled = payroll_client.post(
        "/payroll/advances/adv-1/settle",
        json={"advance_id": "adv-1", "settlement_month": 5, "settlement_year": 2026},
        headers=_auth(_token([role], user_id="u-admin", store_ids=[])),
    )
    assert settled.status_code == 200, f"{role}: {settled.text}"
