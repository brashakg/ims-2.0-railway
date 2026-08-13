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
