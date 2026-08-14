"""IMS 2.0 - the salary rule survives ARITHMETIC, not just field names.

OWNER RULING 2026-08-09, verbatim: "nobody except admin/superadmin should see
anyone elses salary." PR #974 closed the per-EMPLOYEE reads. An audit then found
the rule defeated by subtraction on routers that had never heard of it:

    AN AGGREGATE THAT CONTAINS EXACTLY ONE PERSON IS THAT PERSON'S SALARY.

WHY THESE TESTS LOOK THE WAY THEY DO
------------------------------------
The test that appeared to guard this (test_cost_mask_f35.test_pnl_strip_logic_
mirrors_endpoint) never imported finance at all: it declared its OWN copy of the
strip tuple and popped from it in the test body, so its assertion was true by
construction. The auditor mutated the real guard in finance.py to ``if False:``
and 56 tests stayed green while a STORE_MANAGER received payroll_cost=47777.0.

So every test here CALLS THE ENDPOINT and asserts on the RESPONSE BODY. None of
them re-implements a rule; several of them assert the wage bill cannot be
RECOVERED from what remains, not merely that one key is missing.

The dataset is the auditor's: one store, ZZ-SOLO, with exactly one employee
whose planted CTC is 47777. Expenses 25000 so the two figures cannot be confused
for one another. Hermetic fakes throughout -- no Mongo, so nothing here can flake
on a shared CI database. No emoji (Windows cp1252).
"""

from __future__ import annotations

import copy
import itertools
import os
import sys
from datetime import datetime

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-salary-aggregate")

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import finance, payout, reports  # noqa: E402
from api.routers import users as users_router  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402
from api.services import rbac_policy  # noqa: E402


# ===========================================================================
# The planted dataset (the auditor's numbers)
# ===========================================================================

SOLO_STORE = "ZZ-SOLO"
SOLO_CTC = 47777.0          # the one employee's cost-to-company
SOLO_EXPENSES = 25000.0     # deliberately different, and not a factor of it
SOLO_REVENUE = 300000.0
SOLO_COGS = 120000.0        # 0.4 * revenue via the cost map below

_ORDERS = [
    {
        "order_id": "ZZ-O1",
        "store_id": SOLO_STORE,
        # Mid-window, as a datetime: finance ranges created_at datetime-to-
        # datetime (finance._apply_created_at_range), so a date STRING here
        # would silently match nothing and the fixture would prove nothing.
        "created_at": datetime(2026, 3, 15, 12, 0, 0),
        "status": "COMPLETED",
        "payment_status": "PAID",
        "grand_total": SOLO_REVENUE,
        "tax_amount": 0.0,
        "items": [{"product_id": "ZZ-P1", "quantity": 1, "total": SOLO_REVENUE}],
    }
]
_EXPENSES = [
    {
        "expense_id": "ZZ-E1",
        "store_id": SOLO_STORE,
        "category": "RENT",
        "amount": SOLO_EXPENSES,
        "status": "APPROVED",
        "expense_date": "2026-03-15",
    }
]
_PAYROLL = [
    {
        "employee_id": "ZZ-EMP-SOLO",
        "store_id": SOLO_STORE,
        "year": 2026,
        "month": 3,
        "breakdown": {"ctc_cost": SOLO_CTC},
        "net_salary": 40000.0,
    }
]

_PNL_QS = f"?store_id={SOLO_STORE}&from_date=2026-03-01&to_date=2026-03-31"
_BY_STORE_QS = "?from_date=2026-03-01&to_date=2026-03-31"


# ===========================================================================
# Hermetic Mongo-ish fakes (only what these two handlers actually issue)
# ===========================================================================


def _match(doc, query):
    for key, cond in (query or {}).items():
        if key == "$or":
            if not any(_match(doc, sub) for sub in cond):
                return False
            continue
        val = doc.get(key)
        if isinstance(cond, dict):
            for op, opv in cond.items():
                if op == "$gte" and not (val is not None and val >= opv):
                    return False
                if op == "$lte" and not (val is not None and val <= opv):
                    return False
                if op == "$in" and val not in opv:
                    return False
                if op == "$nin" and val in opv:
                    return False
        elif val != cond:
            return False
    return True


class _Col:
    def __init__(self, docs):
        self._docs = list(docs)

    def find(self, query=None, projection=None, *a, **k):
        return [dict(d) for d in self._docs if _match(d, query)]

    def find_one(self, query=None, *a, **k):
        rows = self.find(query)
        return rows[0] if rows else None

    def aggregate(self, pipeline, *a, **k):
        out = [dict(d) for d in self._docs]
        for stage in pipeline:
            if "$match" in stage:
                out = [d for d in out if _match(d, stage["$match"])]
            elif "$group" in stage:
                grp = stage["$group"]
                key = grp["_id"][1:] if isinstance(grp["_id"], str) else None
                accum = {k2: v for k2, v in grp.items() if k2 != "_id"}
                buckets: dict = {}
                for d in out:
                    bk = d.get(key) if key else None
                    row = buckets.setdefault(bk, {})
                    for name, expr in accum.items():
                        src = expr.get("$sum")
                        if isinstance(src, str):
                            row[name] = row.get(name, 0) + (d.get(src[1:]) or 0)
                        else:
                            # _REVENUE_EXPR is a $ifNull/$cond tree; the fixture
                            # keeps grand_total populated so this is exact.
                            row[name] = row.get(name, 0) + (d.get("grand_total") or 0)
                out = [dict(_id=bk, **vals) for bk, vals in buckets.items()]
        return out


class _FakeDB:
    _MAP = {"orders": _ORDERS, "expenses": _EXPENSES, "payroll": _PAYROLL}

    def get_collection(self, name):
        return _Col(self._MAP.get(name, []))


def _session(*roles, store=SOLO_STORE):
    return {
        "user_id": f"u-{'-'.join(roles).lower()}",
        "username": "tester",
        "full_name": "Test User",
        "active_store_id": store,
        "store_ids": [store],
        "roles": list(roles),
    }


@pytest.fixture
def finance_db(monkeypatch):
    """Point the finance handlers at the planted single-employee store."""
    monkeypatch.setattr(finance, "_get_db", lambda: _FakeDB())
    monkeypatch.setattr(finance, "_cost_by_product", lambda _db: {"ZZ-P1": SOLO_COGS})
    monkeypatch.setattr(finance, "_store_maps", lambda db: ({SOLO_STORE: "ENT1"}, {}))
    monkeypatch.setattr(finance, "_store_name_map", lambda db: {SOLO_STORE: "Solo Store"})


def _finance_client(*roles):
    app = FastAPI()
    app.include_router(finance.router, prefix="/api/v1/finance")

    async def _u():
        return _session(*roles)

    app.dependency_overrides[get_current_user] = _u
    return TestClient(app)


# ===========================================================================
# 1. GET /api/v1/finance/pnl -- the wage bill answers to the SALARY gate,
#    not to the cost gate (defect (a)(i)).
# ===========================================================================

_PAYROLL_KEYS = ("payroll_cost", "net_profit", "net_margin")
_COST_KEYS = ("cogs", "gross_profit", "gross_margin")


@pytest.mark.parametrize("role", ["ADMIN", "SUPERADMIN"])
def test_pnl_gives_the_wage_bill_to_an_admin(finance_db, role):
    """Guard against over-stripping: an ADMIN must still get the real figure."""
    body = _finance_client(role).get("/api/v1/finance/pnl" + _PNL_QS).json()
    assert body["payroll_cost"] == SOLO_CTC
    assert "net_profit" in body and "net_margin" in body


@pytest.mark.parametrize("role", ["ACCOUNTANT", "AREA_MANAGER", "STORE_MANAGER"])
def test_pnl_withholds_every_payroll_derived_figure_below_admin(finance_db, role):
    """THE REQUIREMENT. ACCOUNTANT is the sharp case: they pass can_see_cost, so
    before this fix payroll_cost survived the mask for the same accountant that
    /payroll/registers/summary already 403s."""
    body = _finance_client(role).get("/api/v1/finance/pnl" + _PNL_QS).json()
    for key in _PAYROLL_KEYS:
        assert key not in body, f"{role} received {key}={body.get(key)}"


def test_pnl_accountant_keeps_cost_and_margin(finance_db):
    """The salary fix must NOT quietly take COGS off the accountant: cost is a
    different secret with a different gate, and the books need it. If this test
    fails, somebody 'fixed' the leak by narrowing COST_VISIBLE_ROLES."""
    body = _finance_client("ACCOUNTANT").get("/api/v1/finance/pnl" + _PNL_QS).json()
    for key in _COST_KEYS:
        assert key in body, f"ACCOUNTANT lost {key}"
    assert body["cogs"] == SOLO_COGS


@pytest.mark.parametrize("role", ["AREA_MANAGER", "STORE_MANAGER"])
def test_pnl_below_accountant_still_has_no_cost_figures(finance_db, role):
    """F35 / DECISIONS sec 9, unchanged by this PR -- asserted here because the
    two masks now sit next to each other and one could be lost while editing."""
    body = _finance_client(role).get("/api/v1/finance/pnl" + _PNL_QS).json()
    for key in _COST_KEYS:
        assert key not in body, f"{role} received {key}"


def _numeric_leaves(body):
    """Every number in a /pnl body, including the `expenses` category dict."""
    out = []
    for key, value in body.items():
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            out.append((key, float(value)))
        elif isinstance(value, dict):
            for sub, subv in value.items():
                if isinstance(subv, (int, float)) and not isinstance(subv, bool):
                    out.append((f"{key}.{sub}", float(subv)))
    return out


@pytest.mark.parametrize("role", ["ACCOUNTANT", "AREA_MANAGER", "STORE_MANAGER"])
def test_pnl_wage_bill_is_not_recoverable_by_arithmetic(finance_db, role):
    """The real requirement, stated the way the attacker states it.

    Removing `payroll_cost` alone is the fix that LOOKS right and is not: with

        net_profit = gross_profit - total_expenses - payroll_cost + je_revenue

    a reader holding the other four rearranges to payroll_cost in one line. That
    is a THREE-term recovery, so a test that only compares pairs of figures
    passes while the wage bill is still on the wire -- verified by mutation, see
    the PR body. This searches every signed combination of up to three visible
    numbers instead, which is what "you cannot work it out" actually means.
    """
    numbers = _numeric_leaves(
        _finance_client(role).get("/api/v1/finance/pnl" + _PNL_QS).json()
    )

    for size in (1, 2, 3):
        for combo in itertools.combinations(numbers, size):
            for signs in itertools.product((1, -1), repeat=size):
                total = sum(sign * value for sign, (_, value) in zip(signs, combo))
                if abs(abs(total) - SOLO_CTC) < 0.005:
                    terms = " ".join(
                        f"{'+' if sign > 0 else '-'}{name}({value})"
                        for sign, (name, value) in zip(signs, combo)
                    )
                    pytest.fail(
                        f"{role}: the wage bill {SOLO_CTC} is recoverable as "
                        f"{terms} -- hiding a number while leaving the figures "
                        f"it can be derived from is not hiding it"
                    )


def test_pnl_store_manager_keeps_the_figures_the_owner_left_open(finance_db):
    """Guard against over-stripping in the other direction: the operating-cost
    panel must survive. total_expenses is payroll-EXCLUSIVE and is deliberately
    NOT stripped -- with the payroll keys gone there is nothing left for it to
    be subtracted from."""
    body = _finance_client("STORE_MANAGER").get("/api/v1/finance/pnl" + _PNL_QS).json()
    assert body["revenue"] == SOLO_REVENUE
    assert body["total_expenses"] == SOLO_EXPENSES
    assert body["expenses"]["RENT"] == SOLO_EXPENSES


# ===========================================================================
# 2. GET /api/v1/finance/pnl/by-store -- OWNER DECISION: whole table, admins only
# ===========================================================================


@pytest.mark.parametrize("role", ["ACCOUNTANT", "AREA_MANAGER", "STORE_MANAGER"])
def test_pnl_by_store_is_refused_below_admin(finance_db, role):
    """The auditor drove this endpoint at 200 for all three roles and read
    ZZ-SOLO payroll=47777.0 straight off the row."""
    r = _finance_client(role).get("/api/v1/finance/pnl/by-store" + _BY_STORE_QS)
    assert r.status_code == 403, r.text
    body = r.text
    assert str(int(SOLO_CTC)) not in body, "the refusal still carried the figure"


@pytest.mark.parametrize("role", ["ADMIN", "SUPERADMIN"])
def test_pnl_by_store_still_works_for_an_admin(finance_db, role):
    r = _finance_client(role).get("/api/v1/finance/pnl/by-store" + _BY_STORE_QS)
    assert r.status_code == 200, r.text
    rows = r.json()["stores"]
    assert rows and rows[0]["payroll"] == SOLO_CTC


def test_pnl_by_store_refusal_is_written_in_plain_english(finance_db):
    """The frontend shows `detail` verbatim to a store manager. It must read as
    a permission message a non-technical person understands."""
    detail = (
        _finance_client("STORE_MANAGER")
        .get("/api/v1/finance/pnl/by-store" + _BY_STORE_QS)
        .json()["detail"]
    )
    assert "administrator" in detail.lower()
    for jargon in ("403", "forbidden", "rbac", "payroll_cost", "null"):
        assert jargon not in detail.lower(), f"jargon in user-facing text: {jargon}"


# ===========================================================================
# 3. GET /api/v1/reports/profit/by-store -- the guarded twin. NOT gated.
# ===========================================================================


class _FakeOrderRepo:
    """Two stores; the caller is scoped to ZZ-SOLO only."""

    def find_many(self, query=None, limit=0, **k):
        return [
            {
                "order_id": "ZZ-O1",
                "store_id": SOLO_STORE,
                "grand_total": SOLO_REVENUE,
                "items": [{"cost_price": SOLO_COGS, "quantity": 1}],
            },
            {
                "order_id": "OTHER-O1",
                "store_id": "ZZ-OTHER",
                "grand_total": 999999.0,
                "items": [{"cost_price": 1.0, "quantity": 1}],
            },
        ]


def _reports_client(*roles, store=SOLO_STORE):
    app = FastAPI()
    app.include_router(reports.router, prefix="/api/v1/reports")

    async def _u():
        return _session(*roles, store=store)

    app.dependency_overrides[get_current_user] = _u
    return TestClient(app)


@pytest.fixture
def reports_orders(monkeypatch):
    monkeypatch.setattr(reports, "get_order_repository", lambda: _FakeOrderRepo())


def test_reports_profit_by_store_is_clean_and_stays_open(reports_orders):
    """VERIFIED FOR MYSELF, as instructed, rather than assumed.

    This is the twin of /finance/pnl/by-store and it behaves: it is correctly
    store-scoped via user_store_scope, and profit == revenue - cost with NO
    payroll term anywhere in the row. So a store manager keeps a real view of
    their own store's trading performance after the finance table closes, and
    gating this too would be a loss with no security gain.
    """
    r = _reports_client("STORE_MANAGER").get(
        "/api/v1/reports/profit/by-store?from_date=2026-03-01&to_date=2026-03-31"
    )
    assert r.status_code == 200, r.text
    rows = r.json()["data"]
    assert [row["store_id"] for row in rows] == [SOLO_STORE], "store scope leaked"
    row = rows[0]
    assert set(row) == {"store_id", "revenue", "cost", "profit", "orders"}
    assert "payroll" not in row
    # profit is exactly revenue - cost: no wage term can hide inside it.
    assert round(row["profit"], 2) == round(row["revenue"] - row["cost"], 2)


# ===========================================================================
# 4. /api/v1/payout/* -- per-person incentive rupees (a payslip line)
# ===========================================================================

_SNAPSHOT = {
    "snapshot_id": "SNAP-ZZ-1",
    "store_id": SOLO_STORE,
    "year": 2026,
    "month": 3,
    "status": "LOCKED",
    "total_team_pool": SOLO_CTC,
    "staff_payouts": [
        {
            "user_id": "ZZ-EMP-SOLO",
            "name": "Solo Employee",
            "total_payout": SOLO_CTC,
            "product_incentive": 0.0,
            "payout_by_level": {},
        }
    ],
    "manager_bonuses": [],
    "kicker_only_payouts": [],
    "grand_total": {"all": SOLO_CTC},
    "inputs": {},
}


class _FakeSnapshotRepo:
    def find_by_id(self, snapshot_id):
        return dict(_SNAPSHOT) if snapshot_id == _SNAPSHOT["snapshot_id"] else None

    def list_for_store_year(self, store_id, year):
        return [dict(_SNAPSHOT)]

    def find_locked(self, store_id, year, month):
        return dict(_SNAPSHOT)


def _payout_client(*roles):
    app = FastAPI()
    app.include_router(payout.router, prefix="/api/v1/payout")

    async def _u():
        return _session(*roles)

    app.dependency_overrides[get_current_user] = _u
    return TestClient(app)


@pytest.fixture
def payout_repo(monkeypatch):
    monkeypatch.setattr(payout, "_snapshot_repo", lambda: _FakeSnapshotRepo())


_PAYOUT_READ_PATHS = (
    "/api/v1/payout/snapshot/SNAP-ZZ-1",
    "/api/v1/payout/export/SNAP-ZZ-1.csv",
    f"/api/v1/payout/snapshots?store_id={SOLO_STORE}&year=2026",
    f"/api/v1/payout/payroll-feed?store_id={SOLO_STORE}&year=2026&month=3",
)


@pytest.mark.parametrize("path", _PAYOUT_READ_PATHS)
@pytest.mark.parametrize("role", ["ACCOUNTANT", "AREA_MANAGER", "STORE_MANAGER"])
def test_payout_reads_are_refused_below_admin(payout_repo, path, role):
    """Every one of these bodies names a colleague and states their incentive
    rupees -- the same number PR #974 locked away on
    /payroll/incentive-summary/{employee_id}."""
    r = _payout_client(role).get(path)
    assert r.status_code == 403, f"{role} {path} -> {r.status_code}: {r.text[:200]}"
    assert str(int(SOLO_CTC)) not in r.text
    assert "Solo Employee" not in r.text


@pytest.mark.parametrize("path", _PAYOUT_READ_PATHS)
def test_payout_reads_still_work_for_an_admin(payout_repo, path):
    r = _payout_client("ADMIN").get(path)
    assert r.status_code == 200, r.text


def test_payout_preview_is_refused_below_admin(payout_repo, monkeypatch):
    """/preview computes the same envelope live, so gating only the stored
    snapshots would leave the figures one query away."""
    monkeypatch.setattr(payout, "_compute_payout", lambda **k: dict(_SNAPSHOT))
    r = _payout_client("STORE_MANAGER").get(
        f"/api/v1/payout/preview?store_id={SOLO_STORE}&year=2026&month=3"
    )
    assert r.status_code == 403, r.text
    assert str(int(SOLO_CTC)) not in r.text


def test_payout_refusal_names_where_a_person_finds_their_own_number(payout_repo):
    """A refusal that only refuses is a support ticket. The owner's staff keep
    their OWN incentive on their payslip; the message says so."""
    detail = _payout_client("STORE_MANAGER").get(
        "/api/v1/payout/snapshot/SNAP-ZZ-1"
    ).json()["detail"]
    assert "payslip" in detail.lower()
    assert "administrator" in detail.lower()


# ===========================================================================
# 5. /api/v1/users/* -- sanitize_user is an ALLOW-list now
# ===========================================================================

# The auditor planted these two. NO code in this repo writes either field: they
# exist purely to prove the deny-list shipped unknown fields by default.
_PLANTED_FUTURE_FIELDS = {"salary": 999999.0, "ctc_annual": 1234567.0}

_STATUTORY = {
    "aadhaar_no": "111122223333",
    "pan_no": "ABCDE1234F",
    "uan_no": "100200300400",
    "pf_no": "PF0001",
    "esic_no": "ESIC0001",
    "bank_account_no": "50100123456789",
    "date_of_birth": "1990-04-17",
}

_EMPLOYEE_DOC = {
    "user_id": "zz-emp",
    "username": "zz_emp",
    "email": "zz@example.com",
    "full_name": "Solo Employee",
    "phone": "9876543210",
    "roles": ["SALES_STAFF"],
    "store_ids": [SOLO_STORE],
    "primary_store_id": SOLO_STORE,
    "is_active": True,
    "password_hash": "$2b$12$notarealhashforteststotestwith0000000000000000000",
    "approval_pin_hash": "$2b$12$pinhashplaceholderfortestsonly00000000000000000000",
    **_STATUTORY,
    **_PLANTED_FUTURE_FIELDS,
}


class _FakeUserRepo:
    def find_by_id(self, user_id):
        return dict(_EMPLOYEE_DOC) if user_id == _EMPLOYEE_DOC["user_id"] else None

    def find_by_store(self, store_id):
        return [dict(_EMPLOYEE_DOC)]

    def find_by_role(self, role, store_id=None):
        return [dict(_EMPLOYEE_DOC)]

    def search_users(self, q, store_id=None):
        return [dict(_EMPLOYEE_DOC)]

    def find_many(self, query=None, skip=0, limit=100):
        return [dict(_EMPLOYEE_DOC)]


def _users_client(*roles):
    app = FastAPI()
    app.include_router(users_router.router, prefix="/api/v1/users")

    async def _u():
        return _session(*roles)

    app.dependency_overrides[get_current_user] = _u
    return TestClient(app)


@pytest.fixture
def user_repo(monkeypatch):
    monkeypatch.setattr(users_router, "get_user_repository", lambda: _FakeUserRepo())


_USER_READ_PATHS = (
    "/api/v1/users/zz-emp",
    f"/api/v1/users/store/{SOLO_STORE}",
    "/api/v1/users/role/SALES_STAFF",
    "/api/v1/users/search?q=solo",
    "/api/v1/users",
)


@pytest.mark.parametrize("path", _USER_READ_PATHS)
@pytest.mark.parametrize("role", ["AREA_MANAGER", "STORE_MANAGER"])
def test_statutory_identity_fields_are_withheld_below_admin(user_repo, path, role):
    """Aadhaar carries statutory handling obligations in India; the set as a
    whole is enough to impersonate an employee to a bank or to the EPFO."""
    r = _users_client(role).get(path)
    assert r.status_code == 200, r.text
    for field, value in _STATUTORY.items():
        assert field not in r.text, f"{role} {path}: field name {field} present"
        assert value not in r.text, f"{role} {path}: VALUE of {field} present"


@pytest.mark.parametrize("path", _USER_READ_PATHS)
@pytest.mark.parametrize("role", ["ADMIN", "SUPERADMIN"])
def test_statutory_identity_fields_survive_for_an_admin(user_repo, path, role):
    """Deliberate: HR must type PAN / UAN / ESIC in before payroll can run.
    Breaking the screen that captures them would be worse than the leak."""
    r = _users_client(role).get(path)
    assert r.status_code == 200, r.text
    for field, value in _STATUTORY.items():
        assert value in r.text, f"{role} {path}: admin lost {field}"


@pytest.mark.parametrize("path", _USER_READ_PATHS)
@pytest.mark.parametrize("role", ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"])
def test_a_field_nobody_thought_of_is_hidden_by_construction(user_repo, path, role):
    """THE allow-list requirement, and the one a deny-list cannot pass.

    `salary` and `ctc_annual` are written by NO code in this repo -- the auditor
    invented them to stand in for whatever field gets added next year. Under the
    old pop-list they travelled straight through. Note this holds for ADMIN too:
    an unrecognised field is not on either tier's list.
    """
    r = _users_client(role).get(path)
    assert r.status_code == 200, r.text
    for field, value in _PLANTED_FUTURE_FIELDS.items():
        assert field not in r.text, f"{role} {path}: unknown field {field} shipped"
        assert str(value) not in r.text


@pytest.mark.parametrize("path", _USER_READ_PATHS)
@pytest.mark.parametrize("role", ["ADMIN", "STORE_MANAGER"])
def test_credential_material_never_ships(user_repo, path, role):
    """Round-1's P0 (approval_pin_hash), re-asserted through the allow-list."""
    r = _users_client(role).get(path)
    for field in users_router._CREDENTIAL_FIELDS:
        assert field not in r.text, f"{role} {path}: {field} leaked"


@pytest.mark.parametrize("role", ["ADMIN", "STORE_MANAGER"])
def test_the_user_screens_still_get_what_they_render(user_repo, role):
    """Guard against over-stripping. These are the exact keys the frontend reads
    (SettingsAuth.tsx transformUser + ActivityLogPage). An allow-list that drops
    one of them blanks a real screen."""
    body = _users_client(role).get("/api/v1/users/zz-emp").json()
    for field in (
        "user_id",
        "username",
        "email",
        "full_name",
        "phone",
        "roles",
        "store_ids",
        "is_active",
    ):
        assert field in body, f"{role}: the edit screen lost {field}"


def test_sanitize_user_defaults_to_the_narrow_projection():
    """Fail-closed on the helper itself: a caller we cannot identify never gets
    the identity numbers, even if a future route forgets the viewer argument."""
    out = users_router.sanitize_user(dict(_EMPLOYEE_DOC))
    assert out["user_id"] == "zz-emp"
    for field in _STATUTORY:
        assert field not in out


# ===========================================================================
# 6. The RBAC policy table -- the second, request-time layer
# ===========================================================================


def _allowed(method, path):
    entry = rbac_policy.policy_for(method, path)
    assert entry is not None, f"{method} {path} not catalogued"
    return entry["allowed"]


def test_policy_row_for_pnl_by_store_is_admin_only():
    """Gating the handler alone leaves the middleware advertising the old,
    wider gate -- and the table is what the next reviewer reads."""
    assert set(_allowed("GET", "/api/v1/finance/pnl/by-store")) == {
        "ADMIN",
        "SUPERADMIN",
    }


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/payout/preview",
        "/api/v1/payout/snapshots",
        "/api/v1/payout/snapshot/{snapshot_id}",
        "/api/v1/payout/export/{snapshot_id}.csv",
        "/api/v1/payout/payroll-feed",
    ],
)
def test_policy_rows_for_payout_reads_are_admin_only(path):
    allowed = _allowed("GET", path)
    assert allowed not in (rbac_policy.AUTHENTICATED, rbac_policy.PUBLIC), path
    assert set(allowed) == {"ADMIN", "SUPERADMIN"}, path


def test_the_main_pnl_row_is_deliberately_unchanged():
    """/pnl stays open to the manager tier -- the FIGURES are masked inside the
    handler, not the route. Recorded so a later 'tidy-up' does not close the
    route and quietly delete the store manager's revenue panel."""
    assert set(_allowed("GET", "/api/v1/finance/pnl")) >= {
        "ACCOUNTANT",
        "AREA_MANAGER",
        "STORE_MANAGER",
    }


def test_reports_profit_by_store_row_is_not_narrowed():
    """The guarded twin keeps the manager tier -- it carries no payroll term."""
    allowed = _allowed("GET", "/api/v1/reports/profit/by-store")
    assert "STORE_MANAGER" in allowed and "AREA_MANAGER" in allowed


# ===========================================================================
# 7. ONE definition of the salary tier
# ===========================================================================


def test_every_router_reads_the_same_salary_role_tuple():
    """The leak happened because payroll.py owned the rule privately and three
    other routers never heard of it. If someone re-forks it, this fails."""
    from api.routers import payroll as payroll_router
    from api.services.salary_visibility import SALARY_ADMIN_ROLES, is_salary_admin

    assert SALARY_ADMIN_ROLES == ("SUPERADMIN", "ADMIN")
    assert payroll_router._SALARY_CROSS_EMPLOYEE_ROLES is SALARY_ADMIN_ROLES
    assert finance.is_salary_admin is is_salary_admin
    assert users_router.is_salary_admin is is_salary_admin
    # Fails closed on every shape of "not an admin".
    for session in (None, {}, {"roles": []}, {"roles": ["ACCOUNTANT"]}):
        assert is_salary_admin(session) is False
    assert is_salary_admin({"activeRole": "ADMIN"}) is True


# ===========================================================================
# 8. The residual: `expenses` category is FREE TEXT (owner ruling extension,
#    2026-08-13). #984 reasoned that total_expenses is payroll-EXCLUSIVE, and
#    that is correct for the AUTOMATED payroll run -- it writes the `payroll`
#    collection and never `expenses`. But a PERSON can type anything into the
#    category box, and services/survival_cashflow.py already lists
#    "salary"/"payroll" among its expense heads, so the shape is anticipated in
#    this codebase rather than hypothetical. Booked that way, the wage bill
#    reached a store manager verbatim, by head and by amount.
# ===========================================================================

SALARY_HEAD_AMOUNT = 33333.0     # distinct from SOLO_CTC and SOLO_EXPENSES
INNOCENT_HEAD_AMOUNT = 4100.0

_SALARY_SHAPED_EXPENSES = [
    {
        "expense_id": "ZZ-E-RENT",
        "store_id": SOLO_STORE,
        "category": "RENT",
        "amount": SOLO_EXPENSES,
        "status": "APPROVED",
        "expense_date": "2026-03-15",
    },
    {
        "expense_id": "ZZ-E-SAL",
        "store_id": SOLO_STORE,
        # Deliberately awkward casing/punctuation/whitespace: the match is on a
        # NORMALISED head, so this must be caught exactly like a plain "Salary".
        "category": "  Salaries & Wages ",
        "amount": SALARY_HEAD_AMOUNT,
        "status": "APPROVED",
        "expense_date": "2026-03-20",
    },
    {
        "expense_id": "ZZ-E-INNOCENT",
        "store_id": SOLO_STORE,
        # NOT pay: money recovered FROM staff. A substring or fuzzy match would
        # eat this and quietly corrupt the manager's operating-cost panel -- a
        # worse failure than the one the deny-set prevents.
        "category": "Salary advance recovery",
        "amount": INNOCENT_HEAD_AMOUNT,
        "status": "APPROVED",
        "expense_date": "2026-03-21",
    },
]


class _SalaryExpenseDB(_FakeDB):
    _MAP = {
        "orders": _ORDERS,
        "expenses": _SALARY_SHAPED_EXPENSES,
        "payroll": [],          # nobody has run payroll; this is the mis-booking
    }


@pytest.fixture
def finance_db_with_salary_expense(monkeypatch):
    monkeypatch.setattr(finance, "_get_db", lambda: _SalaryExpenseDB())
    monkeypatch.setattr(finance, "_cost_by_product", lambda _db: {"ZZ-P1": SOLO_COGS})
    monkeypatch.setattr(finance, "_store_maps", lambda db: ({SOLO_STORE: "ENT1"}, {}))
    monkeypatch.setattr(finance, "_store_name_map", lambda db: {SOLO_STORE: "Solo Store"})


@pytest.mark.parametrize("role", ["ADMIN", "SUPERADMIN"])
def test_an_admin_still_sees_a_salary_expense_broken_out(
    finance_db_with_salary_expense, role
):
    """POSITIVE CONTROL. The books must still show what was actually booked --
    an admin who cannot see the head cannot correct the mis-posting."""
    body = _finance_client(role).get("/api/v1/finance/pnl" + _PNL_QS).json()
    assert body["expenses"]["  Salaries & Wages "] == SALARY_HEAD_AMOUNT
    assert body["total_expenses"] == (
        SOLO_EXPENSES + SALARY_HEAD_AMOUNT + INNOCENT_HEAD_AMOUNT
    )
    assert "expenses_partially_restricted" not in body


@pytest.mark.parametrize("role", ["ACCOUNTANT", "AREA_MANAGER", "STORE_MANAGER"])
def test_a_salary_shaped_expense_head_is_withheld_below_admin(
    finance_db_with_salary_expense, role
):
    """THE REQUIREMENT. Somebody typed "Salaries & Wages" into the category box;
    that is pay, whatever collection it landed in."""
    r = _finance_client(role).get("/api/v1/finance/pnl" + _PNL_QS)
    assert r.status_code == 200, r.text
    assert "Salaries" not in r.text, f"{role} received the salary expense head"
    assert str(SALARY_HEAD_AMOUNT) not in r.text
    assert str(int(SALARY_HEAD_AMOUNT)) not in r.text


@pytest.mark.parametrize("role", ["ACCOUNTANT", "AREA_MANAGER", "STORE_MANAGER"])
def test_the_withheld_head_is_also_out_of_total_expenses(
    finance_db_with_salary_expense, role
):
    """Dropping the LINE while leaving the TOTAL is the fix that looks right and
    is not: total_expenses minus the heads still shown is the head removed. That
    is the aggregate-of-one arithmetic this whole file exists to close, and it is
    why the fix does not merely re-label the head as "Other"."""
    body = _finance_client(role).get("/api/v1/finance/pnl" + _PNL_QS).json()
    assert body["total_expenses"] == SOLO_EXPENSES + INNOCENT_HEAD_AMOUNT
    # The panel still adds up, so it reads as a smaller pay-free panel rather
    # than a broken one.
    assert round(sum(body["expenses"].values()), 2) == body["total_expenses"]


@pytest.mark.parametrize("role", ["ACCOUNTANT", "AREA_MANAGER", "STORE_MANAGER"])
def test_the_reader_is_told_their_expense_panel_is_incomplete(
    finance_db_with_salary_expense, role
):
    """A short total that pretends to be the whole truth is its own defect
    (the "honest error states" rule). A flag, never a figure."""
    body = _finance_client(role).get("/api/v1/finance/pnl" + _PNL_QS).json()
    assert body["expenses_partially_restricted"] is True


@pytest.mark.parametrize("role", ["ACCOUNTANT", "AREA_MANAGER", "STORE_MANAGER"])
def test_an_innocent_head_that_merely_mentions_salary_survives(
    finance_db_with_salary_expense, role
):
    """GUARD AGAINST OVER-REACH, which is the failure mode of a deny-list.
    "Salary advance recovery" is money coming back FROM staff, not pay going
    out. If this fails, somebody widened the match to a substring and started
    silently deleting real operating costs from the manager's panel."""
    body = _finance_client(role).get("/api/v1/finance/pnl" + _PNL_QS).json()
    assert body["expenses"]["Salary advance recovery"] == INNOCENT_HEAD_AMOUNT
    assert body["expenses"]["RENT"] == SOLO_EXPENSES


@pytest.mark.parametrize("role", ["ACCOUNTANT", "AREA_MANAGER", "STORE_MANAGER"])
def test_the_salary_expense_is_not_recoverable_by_arithmetic(
    finance_db_with_salary_expense, role
):
    """Same attack as the wage-bill test above, aimed at the mis-booked head."""
    numbers = _numeric_leaves(
        _finance_client(role).get("/api/v1/finance/pnl" + _PNL_QS).json()
    )
    for size in (1, 2, 3):
        for combo in itertools.combinations(numbers, size):
            for signs in itertools.product((1, -1), repeat=size):
                total = sum(sign * value for sign, (_, value) in zip(signs, combo))
                if abs(abs(total) - SALARY_HEAD_AMOUNT) < 0.005:
                    terms = " ".join(
                        f"{'+' if sign > 0 else '-'}{name}({value})"
                        for sign, (name, value) in zip(signs, combo)
                    )
                    pytest.fail(
                        f"{role}: the mis-booked salary {SALARY_HEAD_AMOUNT} is "
                        f"recoverable as {terms}"
                    )


def test_nothing_changes_when_no_expense_is_payroll_shaped(finance_db):
    """The ordinary case -- RENT only -- must be exactly what it was, or this
    fix has quietly narrowed every store manager's cost panel."""
    body = _finance_client("STORE_MANAGER").get("/api/v1/finance/pnl" + _PNL_QS).json()
    assert body["expenses"] == {"RENT": SOLO_EXPENSES}
    assert body["total_expenses"] == SOLO_EXPENSES
    assert "expenses_partially_restricted" not in body


@pytest.mark.parametrize(
    "head",
    [
        "Salary",
        "SALARY",
        "  salary  ",
        "Staff Wages",
        "staff-wages",
        "Payroll",
        "Salaries and Wages",
        "EPF Contribution",
        "ESIC",
        "Gratuity",
        "Staff Incentives",
    ],
)
def test_the_heads_a_person_actually_reaches_for_are_matched(head):
    """Unit-level, on the real helper -- the endpoint tests above prove the
    helper is WIRED IN, this proves the list is worth having."""
    assert finance._is_payroll_shaped_expense(head) is True


@pytest.mark.parametrize(
    "head",
    [
        "RENT",
        "Electricity",
        "Salary advance recovery",
        "Commission to broker",
        "Marketing",
        "",
        None,
        123,
    ],
)
def test_ordinary_heads_are_left_alone(head):
    """The limit of the deny-set, asserted rather than only claimed: this is an
    EXACT match on a normalised head, so "Sal Mar-26" or "Ramesh payment" would
    sail through. That limit is documented at the deny-set itself."""
    assert finance._is_payroll_shaped_expense(head) is False


# ===========================================================================
# 9. THE TWIN. GET /api/v1/finance/budget feeds the Budgets tab of the same
#    FinanceDashboard whose P&L tab section 8 just closed, and it carries the
#    salaries head from the SAME free-text expenses collection -- plus the
#    PLANNED wage bill, which nothing before this PR touched. Fixing one tab
#    and not the one next to it is the failure this repo keeps repeating.
# ===========================================================================

PLANNED_SALARIES = 41000.0
ACTUAL_SALARIES = 37500.0
PLANNED_RENT = 26000.0

_BUDGET_DOC = {
    "month": 3,
    "year": 2026,
    "mode": "full",
    "categories": {
        "rent": {"budget": PLANNED_RENT, "actual": 0},
        "salaries": {"budget": PLANNED_SALARIES, "actual": 0},
        "utilities": {"budget": 9000.0, "actual": 0},
    },
}

_BUDGET_EXPENSES = [
    {
        "expense_id": "ZZ-B-RENT",
        "store_id": SOLO_STORE,
        "category": "rent",
        "amount": PLANNED_RENT,
        "status": "APPROVED",
        "expense_date": "2026-03-05",
    },
    {
        "expense_id": "ZZ-B-SAL",
        "store_id": SOLO_STORE,
        "category": "Salaries",
        "amount": ACTUAL_SALARIES,
        "status": "APPROVED",
        "expense_date": "2026-03-06",
    },
]


class _BudgetCol(_Col):
    def find_one(self, query=None, projection=None, *a, **k):
        # DEEP copy, deliberately. A shallow dict() shares the nested
        # `categories` object between calls, so the first request's strip
        # mutates the fixture and every later request sees a head that was
        # never there -- the tests then pass for the wrong reason. Caught by
        # the flag assertion below failing while the head assertions "passed".
        return copy.deepcopy(_BUDGET_DOC)


class _BudgetDB(_FakeDB):
    def get_collection(self, name):
        if name == "budgets":
            return _BudgetCol([])
        if name == "expenses":
            return _Col(_BUDGET_EXPENSES)
        return _Col([])


@pytest.fixture
def budget_db(monkeypatch):
    monkeypatch.setattr(finance, "_get_db", lambda: _BudgetDB())


def _budget(role):
    return _finance_client(role).get("/api/v1/finance/budget?month=3&year=2026")


@pytest.mark.parametrize("role", ["ADMIN", "SUPERADMIN"])
def test_an_admin_still_sees_the_salaries_budget_line(budget_db, role):
    """POSITIVE CONTROL. Planning the wage bill is an admin job and the screen
    that does it must keep working."""
    cats = _budget(role).json()["categories"]
    assert cats["salaries"]["budget"] == PLANNED_SALARIES
    assert cats["salaries"]["actual"] == ACTUAL_SALARIES
    assert cats["rent"]["budget"] == PLANNED_RENT


@pytest.mark.parametrize("role", ["ACCOUNTANT", "AREA_MANAGER", "STORE_MANAGER"])
def test_the_salaries_budget_line_is_withheld_below_admin(budget_db, role):
    """THE REQUIREMENT. BOTH numbers go: `actual` is the same expense figure
    /pnl now withholds, and `budget` is the PLANNED wage bill, which in a 1-5
    person store is an individual's pay to within a rounding."""
    r = _budget(role)
    assert r.status_code == 200, r.text
    assert "salaries" not in r.json()["categories"], f"{role} kept the head"
    assert str(PLANNED_SALARIES) not in r.text
    assert str(int(PLANNED_SALARIES)) not in r.text
    assert str(ACTUAL_SALARIES) not in r.text
    assert str(int(ACTUAL_SALARIES)) not in r.text


@pytest.mark.parametrize("role", ["ACCOUNTANT", "AREA_MANAGER", "STORE_MANAGER"])
def test_the_rest_of_the_budget_table_survives(budget_db, role):
    """Guard against over-stripping: the manager keeps every non-pay head, so
    the Budgets tab is narrower, not blank."""
    cats = _budget(role).json()["categories"]
    assert cats["rent"]["budget"] == PLANNED_RENT
    assert cats["rent"]["actual"] == PLANNED_RENT
    assert "utilities" in cats


@pytest.mark.parametrize("role", ["ACCOUNTANT", "AREA_MANAGER", "STORE_MANAGER"])
def test_the_budget_reader_is_told_a_head_was_withheld(budget_db, role):
    body = _budget(role).json()
    assert body["categories_partially_restricted"] is True


class _NoBudgetDB(_FakeDB):
    """No budget document for the period -- so /finance/budget falls through to
    the hard-coded skeleton at finance.py:2374, whose `salaries` head is
    {"budget": 0, "actual": 0}. This is the LIVE state of the business today:
    payroll has never been run and no expense has been booked."""

    def get_collection(self, name):
        if name == "budgets":
            return _Col([])
        return _Col([])


@pytest.fixture
def no_budget_db(monkeypatch):
    monkeypatch.setattr(finance, "_get_db", lambda: _NoBudgetDB())


@pytest.mark.parametrize("role", ["ACCOUNTANT", "AREA_MANAGER", "STORE_MANAGER"])
def test_no_restriction_notice_when_the_withheld_head_carried_nothing(
    no_budget_db, role
):
    """THE OTHER DIRECTION, and the reason this test exists.

    The strip pops the pay head unconditionally, which is right -- but CLAIMING
    something was withheld when the head was 0/0 lit the amber "this is not the
    full operating cost" notice permanently, on the live no-budget-set state,
    for every store and area manager. A warning that is always on is exactly as
    useless as one that never appears: staff tune it out, and then it is not
    believed on the day a real wage bill IS withheld.

    Paired with test_the_budget_reader_is_told_a_head_was_withheld above, which
    covers the true case -- delete the flag entirely and THAT one dies, flag
    unconditionally and THIS one dies. Neither alone discriminates.
    """
    body = _budget(role).json()

    # THE REQUIREMENT, asserted first.
    assert "categories_partially_restricted" not in body, (
        f"{role} was told a head was withheld when the pay head was 0/0"
    )
    # ...and the head is still gone, because popping it is unconditional and
    # correct. A test that let the head survive here would pass for the wrong
    # reason.
    assert "salaries" not in body["categories"]
    # Non-pay heads still present, so this is not simply an empty response.
    assert "rent" in body["categories"]


def test_the_budget_policy_row_still_admits_the_manager_tier():
    """The FIGURE is gated, not the route -- a store manager still plans rent,
    utilities and marketing. Recorded so a later tidy-up does not close the
    screen instead."""
    allowed = _allowed("GET", "/api/v1/finance/budget")
    assert {"STORE_MANAGER", "AREA_MANAGER", "ACCOUNTANT"} <= set(allowed)


# ===========================================================================
# 10. THE CROSS-ROUTE SUBTRACTION (round 2, owner ruling 2026-08-14).
#
#     Round 1 of this PR made GET /finance/pnl payroll-EXCLUSIVE below
#     salary-admin and left GET /finance/cash-flow payroll-INCLUSIVE over the
#     SAME store and the SAME month. Two requests, one subtraction, and the
#     wage bill was back. Driven live against the running app:
#
#         pnl.total_expenses        = 24450.00   (pay-free, after round 1)
#         cash-flow.expense_outflow = 88360.65   (pay-INCLUSIVE)
#         88360.65 - 24450.00       = 63910.65   = the planted pay head
#
#     The round-1 sibling sweep DID look at /finance/cash-flow and cleared it
#     because its body is "a grand total, no head names". THE HEAD NAME WAS
#     NEVER THE LEAK. Two figures over the same scope that differ only by
#     payroll ARE the payroll, whatever they are called.
#
#     Per-route tests are exactly what missed this, so the test below is
#     CROSS-ROUTE by construction: one caller, several endpoints, one pool of
#     numbers, one search.
# ===========================================================================

XPAY_HEAD = "Staff Salaries"      # normalises to "staff salaries" -> denied
XPAY_AMT = 63910.65
XRENT_AMT = 21000.00
XPOWER_AMT = 3450.00
XCLEAN = round(XRENT_AMT + XPOWER_AMT, 2)          # 24450.00, the pay-free total
XDIRTY = round(XCLEAN + XPAY_AMT, 2)               # 88360.65, the real total


def _cross_dates():
    """The window BOTH routes agree on.

    /finance/cash-flow has no date parameters at all -- it is hardcoded to the
    1st of the current IST month. That is the whole point: it is a window a
    store manager can also ask /finance/pnl for. So this fixture is dated on the
    real current month rather than the frozen March the sections above use.
    """
    from api.utils.ist import ist_today

    today = ist_today()
    return today.replace(day=1).isoformat(), today.isoformat()


def _cross_expense(category, amount, eid, day_iso):
    return {
        "expense_id": eid,
        "store_id": SOLO_STORE,
        "category": category,
        "amount": amount,
        "status": "APPROVED",
        "expense_date": day_iso,
    }


class _CrossRouteDB(_FakeDB):
    """One store, one month, three expense heads -- one of them somebody's pay.

    `payroll` is EMPTY on purpose: nobody has run a payroll month. The wage bill
    is here only because a person typed it into the expense category box, which
    is the shape production is actually exposed to today.
    """

    def __init__(self):
        first, today = _cross_dates()
        self._MAP = {
            "orders": [
                {
                    "order_id": "ZZ-XO1",
                    "store_id": SOLO_STORE,
                    "created_at": datetime.now(),
                    "status": "COMPLETED",
                    "payment_status": "PAID",
                    "grand_total": SOLO_REVENUE,
                    "tax_amount": 0.0,
                    "amount_paid": SOLO_REVENUE,
                    "items": [
                        {"product_id": "ZZ-P1", "quantity": 1, "total": SOLO_REVENUE}
                    ],
                }
            ],
            "expenses": [
                _cross_expense(XPAY_HEAD, XPAY_AMT, "ZZ-XE1", today),
                _cross_expense("Rent", XRENT_AMT, "ZZ-XE2", first),
                _cross_expense("Electricity", XPOWER_AMT, "ZZ-XE3", today),
            ],
            "payroll": [],
        }

    def get_collection(self, name):
        return _Col(self._MAP.get(name, []))


@pytest.fixture
def cross_route_db(monkeypatch):
    monkeypatch.setattr(finance, "_get_db", lambda: _CrossRouteDB())
    monkeypatch.setattr(finance, "_cost_by_product", lambda _db: {"ZZ-P1": SOLO_COGS})
    monkeypatch.setattr(finance, "_store_maps", lambda db: ({SOLO_STORE: "ENT1"}, {}))
    monkeypatch.setattr(
        finance, "_store_name_map", lambda db: {SOLO_STORE: "Solo Store"}
    )


def _harvest(node, out=None):
    """EVERY numeric leaf anywhere in a response, at any depth, including
    numbers that arrive as strings and numbers used as dict KEYS.

    Deliberately blunt. The round-1 miss was a number that looked innocent
    (`expense_outflow`) in a body with no salary-sounding key in it, so a
    harvester that only looked at the keys it expected would have missed it
    again.
    """
    if out is None:
        out = []
    if isinstance(node, bool) or node is None:
        return out
    if isinstance(node, (int, float)):
        out.append(float(node))
    elif isinstance(node, str):
        try:
            out.append(float(node))
        except ValueError:
            pass
    elif isinstance(node, dict):
        for key, value in node.items():
            _harvest(key, out)
            _harvest(value, out)
    elif isinstance(node, (list, tuple)):
        for value in node:
            _harvest(value, out)
    return out


def _recoverable(target, numbers, max_terms=3, tol=0.005):
    """Is `target` reachable as a SIGNED sum of 1..max_terms of `numbers`?

    Returns the winning combination, or None. Signed, because subtraction is
    the entire attack.
    """
    vals = list(numbers)
    for size in range(1, max_terms + 1):
        for combo in itertools.combinations(range(len(vals)), size):
            for signs in itertools.product((1, -1), repeat=size):
                total = sum(s * vals[i] for s, i in zip(signs, combo))
                if abs(total - target) <= tol:
                    return [(s, vals[i]) for s, i in zip(signs, combo)]
    return None


def test_the_cross_route_searcher_is_not_vacuous():
    """VACUITY GUARD for the tool itself, before anything is proven with it.

    A searcher that cannot find a three-term signed combination would make
    every "not recoverable" verdict below meaningless -- and a two-term-only
    searcher is exactly what would have passed while round 1 was broken.
    """
    assert _recoverable(XPAY_AMT, [17.0, 4.0], max_terms=3) is None
    assert _recoverable(XPAY_AMT, [XDIRTY, XCLEAN], max_terms=2) == [
        (1, XDIRTY),
        (-1, XCLEAN),
    ]
    three = [XDIRTY, XRENT_AMT, XPOWER_AMT]
    assert _recoverable(XPAY_AMT, three, max_terms=2) is None
    assert _recoverable(XPAY_AMT, three, max_terms=3) is not None


def _qs_for(path):
    first, today = _cross_dates()
    return {
        "/api/v1/finance/pnl": (
            f"?store_id={SOLO_STORE}&from_date={first}&to_date={today}"
        ),
        "/api/v1/finance/cash-flow": f"?store_id={SOLO_STORE}",
        "/api/v1/finance/cash-flow-forecast": "?days=90",
        "/api/v1/finance/survival-cashflow": f"?store_id={SOLO_STORE}",
    }[path]


# Every finance route that totals expenses and is reachable below SUPERADMIN.
# The manager tier is 403'd from the last two at both layers, which is exactly
# what test_the_owner_cash_views_are_out_of_reach... below pins.
_ALL_EXPENSE_ROUTES = (
    "/api/v1/finance/pnl",
    "/api/v1/finance/cash-flow",
    "/api/v1/finance/cash-flow-forecast",
    "/api/v1/finance/survival-cashflow",
)

# The two routes PR #985 actually closes. Named separately because the owner
# has deliberately left the other two open to the ACCOUNTANT -- see section 11.
_ROUTES_THIS_PR_CLOSES = (
    "/api/v1/finance/pnl",
    "/api/v1/finance/cash-flow",
)


def _cross_pool(role, paths=_ALL_EXPENSE_ROUTES):
    """Drive each route as `role` and pool every number out of every 200.

    A 403 contributes nothing, which is itself the finding for the roles that
    cannot reach the owner cash views at all.
    """
    client = _finance_client(role)
    bodies = {}
    for path in paths:
        resp = client.get(path + _qs_for(path))
        bodies[path] = (
            resp.status_code,
            resp.json() if resp.status_code == 200 else {},
        )
    pool = []
    for _status, body in bodies.values():
        _harvest(body, pool)
    return bodies, pool


def _fail_text(role, hit, bodies):
    pnl = bodies["/api/v1/finance/pnl"][1]
    cf = bodies["/api/v1/finance/cash-flow"][1]
    return (
        f"{role} can recover the pay head ({XPAY_AMT}) by arithmetic across the "
        f"finance routes: {hit}\n"
        f"  pnl.total_expenses        = {pnl.get('total_expenses')}\n"
        f"  cash-flow.expense_outflow = {cf.get('expense_outflow')}\n"
        f"  cash-flow.outflows        = {cf.get('outflows')}"
    )


@pytest.mark.parametrize("role", ["AREA_MANAGER", "STORE_MANAGER"])
def test_the_pay_head_is_not_recoverable_across_the_finance_routes(
    cross_route_db, role
):
    """THE REQUIREMENT, stated the way the attacker states it.

    One caller. EVERY expense-totalling finance route in the router. Every
    number out of every response in one pool. No signed combination of up to
    three of them may equal the pay head.

    AREA_MANAGER and STORE_MANAGER are the roles the payroll strips genuinely
    protect: they are 403'd from the two owner cash views, so this pool is
    their entire expense-totalling surface and the verdict is a real seal.
    """
    bodies, pool = _cross_pool(role)
    assert bodies["/api/v1/finance/pnl"][0] == 200
    assert bodies["/api/v1/finance/cash-flow"][0] == 200

    hit = _recoverable(XPAY_AMT, pool, max_terms=3)
    assert hit is None, _fail_text(role, hit, bodies)


def test_the_accountant_cannot_recover_it_from_the_routes_this_pr_closes(
    cross_route_db,
):
    """THE ACCOUNTANT, scoped honestly -- and the scope is stated, not implied.

    /finance/pnl and /finance/cash-flow must not give the accountant the pay
    head, and after this round they do not. This is NOT a seal for that role:
    the same accountant reads the wage bill by name on /finance/survival-
    cashflow and can pull it out of /finance/cash-flow-forecast's 90-day totals,
    both by the owner's deliberate 2026-08-14 exception. The test immediately
    below asserts that residual exists rather than letting this one imply it is
    gone. See section 11 and the PR body.
    """
    bodies, pool = _cross_pool("ACCOUNTANT", _ROUTES_THIS_PR_CLOSES)
    hit = _recoverable(XPAY_AMT, pool, max_terms=3)
    assert hit is None, _fail_text("ACCOUNTANT", hit, bodies)


def test_an_admin_can_recover_it_immediately(cross_route_db):
    """THE POSITIVE CONTROL, and the only thing that stops the tests above from
    being a suite which merely proves we can refuse everything.

    For an ADMIN the pay head is supposed to be right there. If the searcher
    cannot find it for an ADMIN then it could not have found it for anybody,
    and every "not recoverable" verdict above is worthless.
    """
    _bodies, pool = _cross_pool("ADMIN")
    hit = _recoverable(XPAY_AMT, pool, max_terms=3)
    assert hit is not None, (
        "the searcher could not find the pay head even for an ADMIN, who is "
        "served it in full -- the search is vacuous"
    )


def _cash_flow(role):
    return (
        _finance_client(role)
        .get(f"/api/v1/finance/cash-flow?store_id={SOLO_STORE}")
        .json()
    )


@pytest.mark.parametrize("role", ["ACCOUNTANT", "AREA_MANAGER", "STORE_MANAGER"])
def test_cash_flow_expense_outflow_is_payroll_free(cross_route_db, role):
    """The single figure that carried the leak, asserted on the RESPONSE BODY."""
    body = _cash_flow(role)
    assert body["expense_outflow"] == XCLEAN
    assert body["expenses_partially_restricted"] is True


@pytest.mark.parametrize("role", ["ACCOUNTANT", "AREA_MANAGER", "STORE_MANAGER"])
def test_cash_flow_moves_all_three_figures_together(cross_route_db, role):
    """THE TRAP. `outflows` and `net_cash_flow` are BUILT from expense_outflow.
    Reduce expense_outflow alone and the reader gets the pay straight back as

        outflows - expense_outflow - purchase_outflow - vendor_payment_outflow

    so this asserts the whole body still adds up -- to the smaller, pay-free
    month, not to the real one.
    """
    body = _cash_flow(role)
    residue = (
        body["outflows"]
        - body["expense_outflow"]
        - body["purchase_outflow"]
        - body["vendor_payment_outflow"]
    )
    assert abs(residue) < 0.005, f"{residue} of payroll left inside `outflows`"
    assert abs(body["net_cash_flow"] - (body["inflows"] - body["outflows"])) < 0.005


@pytest.mark.parametrize("role", ["ADMIN", "SUPERADMIN"])
def test_cash_flow_still_shows_an_admin_the_real_month(cross_route_db, role):
    """Guard against over-stripping: the owner's cash view must stay true."""
    body = _cash_flow(role)
    assert body["expense_outflow"] == XDIRTY
    assert body["outflows"] == XDIRTY
    assert "expenses_partially_restricted" not in body


def test_cash_flow_leaves_an_innocent_month_completely_alone(monkeypatch):
    """No pay-shaped head booked -> identical bodies for a manager and an admin.
    The strip must cost nothing when there is nothing to strip."""
    first, today = _cross_dates()

    class _CleanDB(_CrossRouteDB):
        def __init__(self):
            super().__init__()
            self._MAP["expenses"] = [
                _cross_expense("Rent", XRENT_AMT, "ZZ-XE2", first),
                _cross_expense("Electricity", XPOWER_AMT, "ZZ-XE3", today),
            ]

    monkeypatch.setattr(finance, "_get_db", lambda: _CleanDB())
    manager = _cash_flow("STORE_MANAGER")
    admin = _cash_flow("ADMIN")
    assert manager == admin
    assert manager["expense_outflow"] == XCLEAN


# ===========================================================================
# 11. WHY LEAVING /finance/cash-flow-forecast PAYROLL-INCLUSIVE IS ACCEPTABLE.
#
#     `assumptions.monthly_expense_estimate` is a 90-day, org-wide, all-heads
#     expense total divided by 3, so it carries any pay booked as an expense.
#     It is NOT stripped, and the reason is the ROLE SET rather than the
#     blending: take the salary admins out of its gate and exactly one role is
#     left -- ACCOUNTANT, the role the owner ruled on 2026-08-14 may read the
#     pay heads BY NAME on /finance/survival-cashflow, which sits behind the
#     identical gate. Stripping here would withhold in blended form a figure the
#     owner has just decided that same role may read unblended, and would hand
#     the accountant a cash runway that understates the largest outgoing of the
#     month.
#
#     These two tests are that argument, made checkable.
# ===========================================================================


@pytest.mark.parametrize("role", ["AREA_MANAGER", "STORE_MANAGER"])
def test_the_owner_cash_views_are_out_of_reach_for_the_roles_the_strip_protects(
    cross_route_db, role
):
    """THE LOAD-BEARING CLAIM. The roles /pnl and /cash-flow are stripped for
    cannot reach the owner cash views at all, so section 10's pool really is
    their entire expense-totalling surface. If a later change opens either of
    these routes to the manager tier, this test fails and the payroll strip has
    to be extended the same day."""
    client = _finance_client(role)
    assert client.get("/api/v1/finance/cash-flow-forecast?days=90").status_code == 403
    assert client.get("/api/v1/finance/survival-cashflow").status_code == 403
    assert client.get("/api/v1/finance/owner-dashboard").status_code == 403


def test_the_accountant_forecast_estimate_is_deliberately_payroll_inclusive(
    cross_route_db,
):
    """PINS THE DECISION rather than the absence of one.

    The estimate is the trailing-90-day expense total over 3, pay included. An
    ACCOUNTANT is served it on purpose (owner ruling 2026-08-14, same reasoning
    as survival-cashflow). If somebody later "fixes" this by stripping it, this
    test fails and they have to go and read why -- instead of quietly handing
    the accountant a cash runway with the wage bill missing from it.
    """
    body = (
        _finance_client("ACCOUNTANT")
        .get("/api/v1/finance/cash-flow-forecast?days=90")
        .json()
    )
    est = body["assumptions"]["monthly_expense_estimate"]
    assert est == round(XDIRTY / 3.0, 2), (
        "the recurring estimate is no longer the pay-INCLUSIVE 90-day average; "
        "if that was intentional, see the comment at finance.py's "
        "monthly_expense_est"
    )


# ===========================================================================
# 12. THE OWNER'S DELIBERATE EXCEPTION -- GET /finance/survival-cashflow.
#
#     *** THESE TESTS EXIST TO STOP A FIX, NOT TO PROVE ONE. ***
#
#     OWNER RULING 2026-08-14: this route STAYS OPEN to the ACCOUNTANT, pay
#     heads and all. He was shown the exact consequence -- that it makes the
#     accountant a deliberate exception to his own 2026-08-09 ruling -- and
#     chose it anyway, because knowing what pay is due IS the point of a
#     survival-cash view: an accountant who cannot see the largest committed
#     outgoing cannot answer "can we make payroll this month".
#
#     So the ACCOUNTANT cannot see the wage bill on /finance/pnl or
#     /finance/cash-flow after this PR, but CAN read it here by name. That
#     inconsistency is the owner's to resolve; it is NOT a fresh audit finding
#     and it is NOT a bug. Without the tests below, the next well-meaning
#     security pass deletes the accountant's cash-survival tool in one line and
#     the audit after that re-raises the whole thing from scratch.
# ===========================================================================


def _survival(role):
    resp = _finance_client(role).get(
        f"/api/v1/finance/survival-cashflow?store_id={SOLO_STORE}"
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["survival"]


def _essential_heads(payload):
    return [row.get("head") for row in (payload.get("essential_detail") or [])]


def test_the_accountant_still_reads_the_pay_head_by_name_on_survival_cashflow(
    cross_route_db,
):
    """THE PIN. If this fails, somebody has "closed a leak" the owner ruled open.

    Read the block comment above and finance.get_survival_cashflow's docstring
    BEFORE changing anything here. If the owner really has reversed the ruling,
    the fix is the ROUTE GATE plus these tests plus both comments -- not a quiet
    edit to ESSENTIAL_DEFAULT_HEADS, which would merely reclassify the wage bill
    as DEFERRABLE and tell the owner he can skip paying his staff.
    """
    payload = _survival("ACCOUNTANT")
    assert XPAY_HEAD in _essential_heads(payload), (
        "an ACCOUNTANT no longer sees the pay head on the survival-cash view: "
        f"{payload.get('essential_detail')}"
    )
    row = next(
        r for r in payload["essential_detail"] if r.get("head") == XPAY_HEAD
    )
    assert row["amount_paise"] == int(round(XPAY_AMT * 100))


def test_an_admin_still_gets_the_whole_survival_detail(cross_route_db):
    """POSITIVE CONTROL for the pin: the owner's own view is unchanged, so a
    failure above is about the ACCOUNTANT specifically and not about the
    survival view having broken for everybody."""
    payload = _survival("ADMIN")
    heads = _essential_heads(payload)
    assert XPAY_HEAD in heads
    assert "Rent" in heads
    # rent + electricity + the pay head are all ESSENTIAL heads, so the owner's
    # fixed-cost figure is the whole month, wage bill included.
    assert payload["fixed_costs_paise"] == int(round(XDIRTY * 100))


def test_the_pay_heads_are_still_in_the_essential_seed_list():
    """The other half of the same ruling, pinned at the source.

    services/survival_cashflow.ESSENTIAL_DEFAULT_HEADS is what puts the wage
    bill into `essential_detail` at all. Deleting these entries would look like
    a salary-visibility fix and would in fact reclassify payroll as DEFERRABLE
    spend on the owner's own screen.
    """
    from api.services import survival_cashflow as sc

    for head in ("salary", "salaries", "payroll", "pf", "esi"):
        assert head in sc.ESSENTIAL_DEFAULT_HEADS, (
            f"'{head}' was removed from ESSENTIAL_DEFAULT_HEADS -- see the "
            "owner ruling 2026-08-14 recorded at that constant"
        )


# ===========================================================================
# 13. THE SIBLING THE ROUND-1 SWEEP MISSED -- GET /api/v1/budgets/variance.
#
#     Round 1 closed the pay heads on GET /finance/budget because the Budgets
#     tab renders beside the P&L tab. It never asked who ELSE serves a budget.
#     routers/budgets.py does, to the SAME four roles, store-scoped and
#     month-scoped, and it returns the head BY NAME with its `actual` -- the
#     same expense figure /finance/pnl now withholds. That is the standing
#     question ("where are this guard's sibling call sites?") catching a live
#     leak that names the figure outright, not one that has to be subtracted
#     for.
# ===========================================================================

# Deliberately not a round multiple of anything else in this fixture: an
# arithmetic-recovery searcher reports a coincidence as a leak, and 60000 is
# 3 x the planned rent.
BUDGET_PLANNED_PAY = 58317.0
BUDGET_PLANNED_RENT2 = 20000.0


class _BudgetVarianceColl:
    """The `budgets` collection: one planned pay line, one planned rent line."""

    def find(self, query=None, *a, **k):
        return [
            {
                "budget_id": "ZZ-B1",
                "store_id": SOLO_STORE,
                "period": "2026-03",
                "head": "Staff Salaries",
                "planned_amount": BUDGET_PLANNED_PAY,
            },
            {
                "budget_id": "ZZ-B2",
                "store_id": SOLO_STORE,
                "period": "2026-03",
                "head": "Rent",
                "planned_amount": BUDGET_PLANNED_RENT2,
            },
        ]


@pytest.fixture
def budget_variance_stub(monkeypatch):
    from api.routers import budgets as budgets_router

    monkeypatch.setattr(
        budgets_router, "_budgets_collection", lambda: _BudgetVarianceColl()
    )
    monkeypatch.setattr(budgets_router, "_revenue_actual", lambda s, p: SOLO_REVENUE)
    monkeypatch.setattr(
        budgets_router,
        "_expense_actuals_by_category",
        lambda s, p: {"Staff Salaries": XPAY_AMT, "Rent": XRENT_AMT},
    )
    return budgets_router


def _budgets_client(*roles):
    from api.routers import budgets as budgets_router

    app = FastAPI()
    app.include_router(budgets_router.router, prefix="/api/v1/budgets")

    async def _u():
        return _session(*roles)

    app.dependency_overrides[get_current_user] = _u
    return TestClient(app)


_VARIANCE_QS = f"?store_id={SOLO_STORE}&period=2026-03"


@pytest.mark.parametrize("role", ["ADMIN", "SUPERADMIN"])
def test_an_admin_still_sees_the_pay_line_in_the_budget_variance(
    budget_variance_stub, role
):
    """Guard against over-stripping, and the positive control for the strip:
    the owner plans and reviews the wage bill here."""
    body = _budgets_client(role).get("/api/v1/budgets/variance" + _VARIANCE_QS).json()
    heads = {line["head"]: line for line in body["lines"]}
    assert heads["Staff Salaries"]["planned"] == BUDGET_PLANNED_PAY
    assert heads["Staff Salaries"]["actual"] == XPAY_AMT
    assert "heads_partially_restricted" not in body


@pytest.mark.parametrize("role", ["ACCOUNTANT", "AREA_MANAGER", "STORE_MANAGER"])
def test_the_pay_line_is_withheld_from_the_budget_variance_below_admin(
    budget_variance_stub, role
):
    """THE REQUIREMENT, asserted on the response body. Before this change the
    head arrived by name with both the planned and the booked wage bill."""
    body = _budgets_client(role).get("/api/v1/budgets/variance" + _VARIANCE_QS).json()
    blob = repr(body)
    assert "Staff Salaries" not in blob, f"{role} received the pay head by name"
    assert str(XPAY_AMT) not in blob, f"{role} received the booked wage bill"
    assert str(BUDGET_PLANNED_PAY) not in blob, f"{role} received the planned one"
    assert body["heads_partially_restricted"] is True


@pytest.mark.parametrize("role", ["ACCOUNTANT", "AREA_MANAGER", "STORE_MANAGER"])
def test_the_budget_variance_totals_shrink_with_the_line(budget_variance_stub, role):
    """THE TRAP AGAIN. Filtering `lines` alone leaves the wage bill recoverable
    as totals.expense_actual - sum(line.actual), so the totals block must be
    built from the SAME stripped maps. Asserted as an identity over the body."""
    body = _budgets_client(role).get("/api/v1/budgets/variance" + _VARIANCE_QS).json()
    totals = body["totals"]
    expense_lines = [ln for ln in body["lines"] if not ln["is_revenue"]]

    assert totals["expense_actual"] == XRENT_AMT
    assert totals["expense_planned"] == BUDGET_PLANNED_RENT2
    assert abs(sum(ln["actual"] for ln in expense_lines) - totals["expense_actual"]) < 0.005
    assert abs(sum(ln["planned"] for ln in expense_lines) - totals["expense_planned"]) < 0.005
    assert abs(
        totals["net_actual"] - (totals["revenue_actual"] - totals["expense_actual"])
    ) < 0.005


@pytest.mark.parametrize("role", ["ACCOUNTANT", "AREA_MANAGER", "STORE_MANAGER"])
def test_the_budget_variance_is_not_recoverable_by_arithmetic(
    budget_variance_stub, role
):
    """Same searcher, same standard, applied to this route's whole body."""
    body = _budgets_client(role).get("/api/v1/budgets/variance" + _VARIANCE_QS).json()
    pool = _harvest(body)
    for target in (XPAY_AMT, BUDGET_PLANNED_PAY):
        hit = _recoverable(target, pool, max_terms=3)
        assert hit is None, f"{role} recovers {target} from /budgets/variance: {hit}"


@pytest.mark.parametrize("role", ["ACCOUNTANT", "AREA_MANAGER", "STORE_MANAGER"])
def test_the_rest_of_the_budget_variance_survives(budget_variance_stub, role):
    """Guard against over-stripping in the other direction: a store manager
    still plans and reviews rent, and still sees the revenue target."""
    body = _budgets_client(role).get("/api/v1/budgets/variance" + _VARIANCE_QS).json()
    heads = {line["head"]: line for line in body["lines"]}
    assert heads["Rent"]["planned"] == BUDGET_PLANNED_RENT2
    assert heads["Rent"]["actual"] == XRENT_AMT
    assert heads["REVENUE"]["actual"] == SOLO_REVENUE


@pytest.mark.parametrize("role", ["ACCOUNTANT", "AREA_MANAGER", "STORE_MANAGER"])
def test_the_planned_pay_line_is_withheld_from_the_budget_list(
    budget_variance_stub, role
):
    """The other read on the same router. `planned_amount` for a "Staff
    Salaries" head is the PLANNED wage bill, which in a 1-5 person store is an
    individual's pay to within a rounding."""
    body = _budgets_client(role).get("/api/v1/budgets" + _VARIANCE_QS).json()
    blob = repr(body)
    assert "Staff Salaries" not in blob
    assert str(BUDGET_PLANNED_PAY) not in blob
    assert body["heads_partially_restricted"] is True
    assert [b["head"] for b in body["budgets"]] == ["Rent"]


@pytest.mark.parametrize("role", ["ADMIN", "SUPERADMIN"])
def test_an_admin_still_lists_the_planned_pay_line(budget_variance_stub, role):
    body = _budgets_client(role).get("/api/v1/budgets" + _VARIANCE_QS).json()
    heads = [b["head"] for b in body["budgets"]]
    assert "Staff Salaries" in heads and "Rent" in heads
    assert "heads_partially_restricted" not in body


def test_the_budgets_policy_rows_still_admit_the_manager_tier():
    """The FIGURE is gated, not the route -- a store manager still plans rent
    and marketing. Recorded so a later tidy-up closes the head and not the
    screen."""
    for path in ("/api/v1/budgets", "/api/v1/budgets/variance"):
        allowed = _allowed("GET", path)
        assert {"STORE_MANAGER", "AREA_MANAGER", "ACCOUNTANT"} <= set(allowed), path


# ===========================================================================
# 14. ONE MATCHER, NOT TWO. The rule that produced section 13 in the first
#     place is that a matcher living in one router's private namespace gets
#     forked or forgotten by the next router -- exactly what happened between
#     finance.py and budgets.py inside a single round.
# ===========================================================================


def test_every_router_reads_the_same_payroll_head_matcher():
    from api.routers import budgets as budgets_router
    from api.services import salary_visibility

    assert (
        finance._is_payroll_shaped_expense
        is salary_visibility.is_payroll_shaped_expense
    )
    assert (
        budgets_router.is_payroll_shaped_expense
        is salary_visibility.is_payroll_shaped_expense
    )


# ===========================================================================
# 15. THE CROSS-ROUTER SEARCH -- ONE CALLER, EVERY EXPENSE-TOTALLING SCREEN,
#     ONE POOL OF NUMBERS.
#
#     *** THIS IS THE TEST THE LAST TWO ROUNDS DID NOT HAVE. ***
#
#     Sections 10 and 13 are each per-ROUTER: section 10 pools the finance
#     routes, section 13 pools the budgets routes, and neither can see a
#     subtraction that crosses from one router to the other. That is the exact
#     shape of both misses this PR has now had:
#
#       round 1 stripped /finance/pnl and left /finance/cash-flow inclusive
#               -> one subtraction, two responses, SAME router.
#       round 2 stripped /finance/* and left /budgets/variance naming the head
#               -> no subtraction needed at all, DIFFERENT router.
#
#     A per-route test passes in both worlds. So this section mounts the
#     finance router and the budgets router on ONE app, points them at ONE
#     store and ONE month, drives every expense-totalling screen as ONE caller,
#     and pools every numeric leaf and every string from all of it. The
#     question it asks is the attacker's question and not the reviewer's:
#     given everything this person can see today, can they arrive at the
#     wage bill -- by reading it, or by any signed sum of up to three figures?
#
#     THE ADMIN CONTROL IS LOAD-BEARING. An ADMIN is served the pay head in
#     full, so the searcher MUST find it immediately for that role. If it does
#     not, the searcher is broken and every "sealed" verdict below is worthless
#     -- which is precisely the failure mode catalogued in
#     memory/feedback_hollow_tests.md as "assertions true by construction".
# ===========================================================================

UNI_PAY_HEAD = "Staff Salaries"     # normalises to "staff salaries" -> denied
UNI_PAY_ACTUAL = 63910.65           # the BOOKED wage bill
UNI_PAY_PLANNED = 58317.35          # the PLANNED wage bill (also restricted)
UNI_RENT = 21000.00
UNI_POWER = 3450.00
UNI_RENT_PLANNED = 20000.00
UNI_CLEAN = round(UNI_RENT + UNI_POWER, 2)          # 24450.00
UNI_DIRTY = round(UNI_CLEAN + UNI_PAY_ACTUAL, 2)    # 88360.65


def _uni_period():
    """The month all seven screens describe.

    /finance/cash-flow takes no date parameters -- it is hardcoded to the 1st
    of the current IST month. So the CURRENT month is the only window in which
    all seven screens genuinely describe the same scope, which is the whole
    premise of a cross-route subtraction. A frozen month would make the pnl and
    budgets figures describe a different period from the cash-flow one and the
    test would prove nothing.
    """
    from api.utils.ist import ist_today

    today = ist_today()
    return today.replace(day=1).isoformat(), today.isoformat(), today.strftime("%Y-%m")


class _SessionCursor(list):
    """`find(...).sort(...).limit(...)` -- the chain the sessions route uses.

    Without this, ``list_cash_register_sessions`` raises inside its own
    try/except and returns an EMPTY session list, so the till screens would
    contribute nothing to the pool and every "sealed" verdict below would be
    sealed only because the fixture was broken. That is exactly the
    "assertions true by construction" failure in feedback_hollow_tests.md, so
    ``test_the_till_screens_really_are_in_the_pool`` asserts the fixture is
    live before any of the search tests are believed.
    """

    def sort(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self


class _SessionCol(_Col):
    def find(self, query=None, projection=None, *a, **k):
        return _SessionCursor(super().find(query, projection))


# The open till, in the SAME store and the SAME month as everything else, so
# a figure on the close screen is a figure about the month the /pnl and
# /budgets screens describe. Numbers chosen not to collide with any other.
UNI_OPENING_FLOAT = 5000.00
UNI_CASH_SALES = 137500.00
UNI_TILL_OPEN_AT = None  # filled per-run from _uni_period()


class _UnifiedDB(_FakeDB):
    """One store, one month, three expense heads -- one of them somebody's pay.

    `payroll` is EMPTY on purpose: nobody has run a payroll month, which is
    production's state today (0 payroll docs). The wage bill is here purely
    because a person typed it into the free-text expense category box.

    It also carries an OPEN cash-register session for that store, because the
    till family (/finance/cash-register/sessions, and the reconciliation
    console it feeds) totals THE SAME expenses over THE SAME store for THE
    SAME four roles as /finance/pnl. Round 1 made /pnl payroll-exclusive and
    left this family inclusive; three rounds of per-route tests never saw it,
    and only a pooled search across BOTH families does.
    """

    def __init__(self):
        first, today, _period = _uni_period()
        self._MAP = {
            "orders": [
                {
                    "order_id": "ZZ-UO1",
                    "store_id": SOLO_STORE,
                    "created_at": datetime.now(),
                    "status": "COMPLETED",
                    "payment_status": "PAID",
                    "grand_total": SOLO_REVENUE,
                    "tax_amount": 0.0,
                    "amount_paid": SOLO_REVENUE,
                    "items": [
                        {"product_id": "ZZ-P1", "quantity": 1, "total": SOLO_REVENUE}
                    ],
                }
            ],
            "expenses": [
                _cross_expense(UNI_PAY_HEAD, UNI_PAY_ACTUAL, "ZZ-UE1", today),
                _cross_expense("Rent", UNI_RENT, "ZZ-UE2", first),
                _cross_expense("Electricity", UNI_POWER, "ZZ-UE3", today),
            ],
            "payroll": [],
            "cash_register_sessions": [
                {
                    "session_id": "ZZ-UCR1",
                    "store_id": SOLO_STORE,
                    "status": "OPEN",
                    "opened_at": f"{first}T09:00:00+05:30",
                    "opening_float": UNI_OPENING_FLOAT,
                }
            ],
        }

    def get_collection(self, name):
        docs = self._MAP.get(name, [])
        if name == "cash_register_sessions":
            return _SessionCol(docs)
        return _Col(docs)


class _UnifiedBudgetsColl:
    """The `budgets` collection for the same store and the same month."""

    def find(self, query=None, *a, **k):
        _first, _today, period = _uni_period()
        return [
            {
                "budget_id": "ZZ-UB1",
                "store_id": SOLO_STORE,
                "period": period,
                "head": UNI_PAY_HEAD,
                "planned_amount": UNI_PAY_PLANNED,
            },
            {
                "budget_id": "ZZ-UB2",
                "store_id": SOLO_STORE,
                "period": period,
                "head": "Rent",
                "planned_amount": UNI_RENT_PLANNED,
            },
        ]


@pytest.fixture
def unified_db(monkeypatch):
    """Point BOTH routers at the SAME store, the SAME month, the SAME expenses.

    This is what makes the pool an honest attack surface rather than five
    unrelated fixtures: every figure below describes one real month in one real
    store, so any difference between two of them is information about that
    month and nothing else.
    """
    from api.routers import budgets as budgets_router

    monkeypatch.setattr(finance, "_get_db", lambda: _UnifiedDB())
    monkeypatch.setattr(finance, "_cost_by_product", lambda _db: {"ZZ-P1": SOLO_COGS})
    monkeypatch.setattr(finance, "_store_maps", lambda db: ({SOLO_STORE: "ENT1"}, {}))
    monkeypatch.setattr(
        finance, "_store_name_map", lambda db: {SOLO_STORE: "Solo Store"}
    )
    # The till's cash-IN side. Stubbed because this section is about the
    # EXPENSE side: a hand-built orders fixture for the drawer would only add
    # a way for the pool to be wrong for reasons unrelated to the wage bill.
    monkeypatch.setattr(
        finance, "_cash_sales_for_window", lambda *a, **k: (UNI_CASH_SALES, 0.0)
    )
    monkeypatch.setattr(finance, "_refund_double_entry_advisory", lambda *a, **k: None)

    monkeypatch.setattr(
        budgets_router, "_budgets_collection", lambda: _UnifiedBudgetsColl()
    )
    monkeypatch.setattr(budgets_router, "_revenue_actual", lambda s, p: SOLO_REVENUE)
    monkeypatch.setattr(
        budgets_router,
        "_expense_actuals_by_category",
        lambda s, p: {
            UNI_PAY_HEAD: UNI_PAY_ACTUAL,
            "Rent": UNI_RENT,
            "Electricity": UNI_POWER,
        },
    )
    return budgets_router


def _unified_client(*roles):
    """ONE app, BOTH routers, ONE session. The point of the whole section."""
    from api.routers import budgets as budgets_router

    app = FastAPI()
    app.include_router(finance.router, prefix="/api/v1/finance")
    app.include_router(budgets_router.router, prefix="/api/v1/budgets")

    async def _u():
        return _session(*roles)

    app.dependency_overrides[get_current_user] = _u
    return TestClient(app)


def _harvest_strings(node, out=None):
    """Every string anywhere in a response, at any depth, KEYS INCLUDED.

    Head names are data here, not schema: /budgets/variance puts the expense
    head in a `head` VALUE while other shapes put it in a dict KEY. Harvesting
    both means a future refactor that moves the head from one to the other
    cannot quietly slip past this test.
    """
    if out is None:
        out = []
    if isinstance(node, str):
        out.append(node)
    elif isinstance(node, dict):
        for key, value in node.items():
            _harvest_strings(key, out)
            _harvest_strings(value, out)
    elif isinstance(node, (list, tuple)):
        for value in node:
            _harvest_strings(value, out)
    return out


def _unified_paths():
    first, today, period = _uni_period()
    return (
        f"/api/v1/finance/pnl?store_id={SOLO_STORE}"
        f"&from_date={first}&to_date={today}",
        f"/api/v1/finance/cash-flow?store_id={SOLO_STORE}",
        "/api/v1/finance/cash-flow-forecast?days=90",
        f"/api/v1/budgets?store_id={SOLO_STORE}&period={period}",
        f"/api/v1/budgets/variance?store_id={SOLO_STORE}&period={period}",
        # ADDED ROUND 4. The till family totals the same `expenses` collection
        # over the same store for the same four roles as /finance/pnl, and
        # three rounds of per-route tests never noticed. It is in the POOL now,
        # not in a test of its own, because the pool is the only thing that has
        # reliably caught this class.
        f"/api/v1/finance/cash-register/sessions?store_id={SOLO_STORE}",
        f"/api/v1/finance/cash-reconciliation-summary?from={first}&to={today}"
        f"&store_id={SOLO_STORE}",
    )


def _unified_pool(role):
    """Drive all seven screens as one caller; return (bodies, numbers, strings).

    A 403 contributes nothing to the pool, which is the correct treatment: a
    screen the caller cannot open is not part of their attack surface. It is
    also why the manager tier's pool is genuinely their whole surface -- see
    test_the_owner_cash_views_are_out_of_reach... in section 11.
    """
    client = _unified_client(role)
    bodies = {}
    for path in _unified_paths():
        resp = client.get(path)
        bodies[path.split("?")[0]] = (
            resp.status_code,
            resp.json() if resp.status_code == 200 else {},
        )
    numbers, strings = [], []
    for _status, body in bodies.values():
        _harvest(body, numbers)
        _harvest_strings(body, strings)
    return bodies, numbers, strings


@pytest.mark.parametrize("role", ["AREA_MANAGER", "STORE_MANAGER", "ACCOUNTANT"])
def test_the_pay_head_is_not_named_on_any_screen_this_caller_can_open(
    unified_db, role
):
    """HALF ONE: it must not be READABLE. No subtraction, no cleverness --
    round 2 found /budgets/variance simply printing head="Staff Salaries" with
    its amount, which is why this half exists separately from the arithmetic
    half below and fails with its own message."""
    bodies, _numbers, strings = _unified_pool(role)
    assert bodies["/api/v1/finance/pnl"][0] == 200
    assert bodies["/api/v1/budgets/variance"][0] == 200

    named = [s for s in strings if "salar" in s.lower() or "payroll" in s.lower()]
    assert not named, (
        f"{role} is shown a pay head by name across the finance/budgets "
        f"screens: {named}"
    )


@pytest.mark.parametrize("role", ["AREA_MANAGER", "STORE_MANAGER", "ACCOUNTANT"])
def test_the_till_screens_really_are_in_the_pool(unified_db, role):
    """*** BELIEVE NOTHING BELOW UNTIL THIS PASSES. ***

    The two till paths were added to `_unified_paths` in round 4. Both handlers
    wrap their reads in try/except and fall back to an empty body, so a fixture
    that does not satisfy them returns 200 with nothing in it -- and every
    "not recoverable" verdict in this section would then be true only because
    the till contributed no numbers. That is the hollow-test failure this repo
    has documented 25+ times.

    So: assert the till screens are LIVE and carrying real figures, before any
    search result from them is trusted.
    """
    bodies, numbers, _strings = _unified_pool(role)

    sessions = bodies["/api/v1/finance/cash-register/sessions"]
    assert sessions[0] == 200, sessions
    preview = sessions[1].get("expected_preview")
    assert preview is not None, (
        "the open till session did not reach the handler -- the fixture is "
        "dead and this whole section proves nothing"
    )
    assert preview["cash_sales"] == UNI_CASH_SALES
    assert preview["opening_float"] == UNI_OPENING_FLOAT
    assert UNI_CASH_SALES in numbers, "the till's figures are not in the pool"

    recon = bodies["/api/v1/finance/cash-reconciliation-summary"]
    assert recon[0] == 200, recon
    assert "totals" in recon[1]


@pytest.mark.parametrize("role", ["AREA_MANAGER", "STORE_MANAGER", "ACCOUNTANT"])
def test_the_till_drawer_is_payroll_free_on_the_shared_screen(unified_db, role):
    """The round-3 finding, stated as the figure rather than as a difference.

    The verifier reproduced expected_preview.cash_expenses = payroll-INCLUSIVE
    while /finance/pnl.total_expenses was payroll-EXCLUSIVE over the same store
    and month. Both are now the pay-free operating total, so the subtraction
    that recovered the wage bill returns zero.
    """
    bodies, _numbers, _strings = _unified_pool(role)
    preview = bodies["/api/v1/finance/cash-register/sessions"][1]["expected_preview"]
    assert preview["cash_expenses"] == UNI_CLEAN, (
        f"{role} sees a till total of {preview['cash_expenses']}; the pay-free "
        f"operating total for this month is {UNI_CLEAN}"
    )
    assert bodies["/api/v1/finance/pnl"][1]["total_expenses"] == UNI_CLEAN
    assert (
        round(preview["cash_expenses"]
              - bodies["/api/v1/finance/pnl"][1]["total_expenses"], 2) == 0.0
    )


@pytest.mark.parametrize("role", ["AREA_MANAGER", "STORE_MANAGER"])
def test_the_pay_head_is_not_recoverable_across_finance_and_budgets(
    unified_db, role
):
    """HALF TWO: it must not be DERIVABLE. Both the booked wage bill and the
    planned one, against every number on every screen this caller can open, by
    any signed sum of up to three terms.

    THE MANAGER TIER ONLY, and that is the honest scope rather than a
    convenient one. These two roles are 403'd from the forecast, the owner
    dashboard and the survival view, so the seven screens below really are their
    entire expense-totalling surface and "not recoverable" here really does
    mean sealed. The ACCOUNTANT is a different question with a different answer
    -- see the two tests immediately below, which state it rather than quietly
    excluding the role.
    """
    bodies, numbers, _strings = _unified_pool(role)
    assert bodies["/api/v1/finance/cash-flow-forecast"][0] == 403, (
        f"{role} can now open the payroll-INCLUSIVE forecast; the strip in "
        "get_cash_flow has to be extended to it the same day"
    )
    for target, label in (
        (UNI_PAY_ACTUAL, "the BOOKED wage bill"),
        (UNI_PAY_PLANNED, "the PLANNED wage bill"),
    ):
        hit = _recoverable(target, numbers, max_terms=3)
        assert hit is None, (
            f"{role} recovers {label} ({target}) by arithmetic across the "
            f"finance and budgets screens: {hit}\n"
            f"  pnl.total_expenses        = "
            f"{bodies['/api/v1/finance/pnl'][1].get('total_expenses')}\n"
            f"  cash-flow.expense_outflow = "
            f"{bodies['/api/v1/finance/cash-flow'][1].get('expense_outflow')}\n"
            f"  variance.totals           = "
            f"{bodies['/api/v1/budgets/variance'][1].get('totals')}"
        )


def test_the_accountant_is_sealed_on_the_routes_this_pr_actually_closes(
    unified_db,
):
    """THE ACCOUNTANT, part one: the four screens this PR closes really are
    closed for them too. /finance/pnl, /finance/cash-flow, /budgets and
    /budgets/variance give up neither wage bill, by name or by arithmetic."""
    client = _unified_client("ACCOUNTANT")
    first, today, period = _uni_period()
    closed = (
        f"/api/v1/finance/pnl?store_id={SOLO_STORE}"
        f"&from_date={first}&to_date={today}",
        f"/api/v1/finance/cash-flow?store_id={SOLO_STORE}",
        f"/api/v1/budgets?store_id={SOLO_STORE}&period={period}",
        f"/api/v1/budgets/variance?store_id={SOLO_STORE}&period={period}",
    )
    numbers, strings = [], []
    for path in closed:
        resp = client.get(path)
        assert resp.status_code == 200, resp.text
        _harvest(resp.json(), numbers)
        _harvest_strings(resp.json(), strings)

    assert not [s for s in strings if "salar" in s.lower()]
    for target in (UNI_PAY_ACTUAL, UNI_PAY_PLANNED):
        assert _recoverable(target, numbers, max_terms=3) is None


def test_the_accountants_seal_is_deliberately_incomplete_and_here_is_where(
    unified_db,
):
    """THE ACCOUNTANT, part two -- *** THIS TEST PINS A GAP ON PURPOSE. ***

    Add /finance/cash-flow-forecast, which the ACCOUNTANT may open and the
    manager tier may not, and the wage bill comes straight back:

        forecast's payroll-INCLUSIVE 90-day expense total
          MINUS /finance/pnl's payroll-EXCLUSIVE total
          = the wage bill

    That is NOT an oversight and it is NOT a fresh finding to go and fix. The
    owner ruled on 2026-08-14 that the ACCOUNTANT may read the aggregate wage
    bill BY NAME on /finance/survival-cashflow, which sits behind the identical
    gate. Closing the forecast against that role would protect nothing they can
    not already read one screen over, while handing them a cash runway with the
    largest outgoing of the month missing from it.

    So the honest statement of what PR #985 achieves is:

        STORE_MANAGER / AREA_MANAGER -- SEALED.
        ACCOUNTANT                   -- closed on the four screens above,
                                        still open by the owner's exception.

    This test asserts that gap EXISTS so nobody can claim the accountant is
    sealed when they are not, and so that anybody who does close the forecast
    has to come here, read the ruling, and decide deliberately. If the owner
    ever narrows the survival-screen exception, this is the test to delete and
    the strip in get_cash_flow is the shape to copy.
    """
    _bodies, numbers, _strings = _unified_pool("ACCOUNTANT")
    hit = _recoverable(UNI_PAY_ACTUAL, numbers, max_terms=3)
    assert hit is not None, (
        "the ACCOUNTANT can no longer recover the wage bill across the finance "
        "screens. If that was deliberate -- i.e. the owner narrowed his "
        "2026-08-14 survival-cashflow exception -- then delete this test and "
        "say so in the PR. If it was accidental, the accountant has just lost "
        "an accurate cash runway and nobody decided that."
    )


@pytest.mark.parametrize("role", ["ADMIN", "SUPERADMIN"])
def test_the_cross_router_search_finds_it_instantly_for_an_admin(unified_db, role):
    """THE POSITIVE CONTROL, and the only reason the two tests above mean
    anything. An ADMIN is served the wage bill in full and on purpose, so the
    searcher must find it BY NAME and BY ARITHMETIC. A suite that can only ever
    say "not found" is a suite that proves nothing."""
    _bodies, numbers, strings = _unified_pool(role)

    assert UNI_PAY_HEAD in strings, (
        "an ADMIN is no longer shown the pay head by name -- either the strip "
        "is over-reaching or the string harvester is broken; either way the "
        "'not named' verdicts above are worthless"
    )
    for target in (UNI_PAY_ACTUAL, UNI_PAY_PLANNED):
        assert _recoverable(target, numbers, max_terms=3) is not None, (
            f"the searcher could not find {target} even for an {role}, who is "
            "served it outright -- the search is vacuous"
        )


@pytest.mark.parametrize("role", ["AREA_MANAGER", "STORE_MANAGER", "ACCOUNTANT"])
def test_the_ordinary_heads_survive_on_every_screen(unified_db, role):
    """GUARD AGAINST OVER-STRIPPING, cross-router too. A store manager still
    runs a store: rent and electricity must still be there, on both routers,
    or this PR has broken the operating-cost screens it was meant to protect.
    """
    bodies, _numbers, strings = _unified_pool(role)
    assert "Rent" in strings and "Electricity" in strings

    variance = bodies["/api/v1/budgets/variance"][1]
    heads = {ln["head"]: ln for ln in variance["lines"]}
    assert heads["Rent"]["planned"] == UNI_RENT_PLANNED
    assert heads["Rent"]["actual"] == UNI_RENT
    assert heads["Electricity"]["actual"] == UNI_POWER
    assert heads["REVENUE"]["actual"] == SOLO_REVENUE

    # And the two routers still agree on the pay-free month, which is what
    # makes the subtraction return zero instead of the wage bill.
    assert variance["totals"]["expense_actual"] == UNI_CLEAN
    assert bodies["/api/v1/finance/cash-flow"][1]["expense_outflow"] == UNI_CLEAN
    assert bodies["/api/v1/finance/pnl"][1]["total_expenses"] == UNI_CLEAN


def test_salary_advance_recovery_is_not_swept_up_as_a_pay_head(unified_db):
    """THE FALSE-POSITIVE GUARD, named in the brief because getting this wrong
    is the expensive half. "Salary advance recovery" is money coming BACK from
    a customer or an employee; it is an ordinary operating line and a store
    manager must keep it. The matcher is exact-match on the normalised head
    precisely so it cannot swallow this one."""
    from api.services.salary_visibility import is_payroll_shaped_expense as _m

    assert _m("Salary advance recovery") is False
    assert _m("Commission to broker") is False
    assert _m("Rent") is False
    # ...while still catching the heads a person actually reaches for.
    assert _m("Staff Salaries") is True
    assert _m("  SALARIES & WAGES ") is True


# ===========================================================================
# 16. THE THREE ROUTES LEFT PAYROLL-INCLUSIVE ON PURPOSE, PINNED AS A GATE.
#
#     /finance/owner-dashboard, /finance/cash-flow-forecast and
#     /expenses/aging all total or name expenses without a payroll filter.
#     They are NOT stripped, and the justification is entirely the ROLE SET:
#     every one of them is ACCOUNTANT / ADMIN / SUPERADMIN, and the ACCOUNTANT
#     is the role the owner ruled on 2026-08-14 may read the pay heads BY NAME
#     on /finance/survival-cashflow. Closing them against that role would be
#     theatre; it protects nothing they cannot already read one screen over.
#
#     THAT JUSTIFICATION IS ONLY TRUE WHILE THE GATES STAY WHERE THEY ARE.
#     This test is the tripwire: widen any of them to STORE_MANAGER or
#     AREA_MANAGER and it fails, because those roles have no authorisation to
#     see other people's pay in any form and the strip would have to follow
#     the same day.
# ===========================================================================


def test_the_payroll_inclusive_routes_are_still_accountant_and_admin_only():
    """The tripwire under the 2026-08-14 exception. If this fails, read the
    comments at finance.py::owner_dashboard, finance.py::cash_flow_forecast and
    expenses.py::get_reimbursement_aging BEFORE relaxing the assertion -- the
    correct response is to add the payroll strip, not to widen this list."""
    manager_tier = {"STORE_MANAGER", "AREA_MANAGER"}
    for path in (
        "/api/v1/finance/owner-dashboard",
        "/api/v1/finance/cash-flow-forecast",
        "/api/v1/expenses/aging",
    ):
        allowed = set(_allowed("GET", path))
        assert not (allowed & manager_tier), (
            f"{path} is payroll-INCLUSIVE and has been opened to "
            f"{sorted(allowed & manager_tier)}, who may not see other people's "
            "pay in any form -- it now needs the strip from get_cash_flow"
        )
        assert allowed <= {"ACCOUNTANT", "ADMIN", "SUPERADMIN"}, sorted(allowed)
