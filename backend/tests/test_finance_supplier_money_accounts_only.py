"""
IMS 2.0 -- SUPPLIER MONEY ON THE FINANCE ROUTES IS FOR THE ACCOUNTS ROLES ONLY
=============================================================================
Owner ruling 2026-10-01: "supplier balances should not be shown to anyone
apart from admin superadmin and accountant". Supplier balance = what we owe /
have paid / are owed by a supplier.

The finance router is mounted behind _FINANCE_ROLES (ADMIN, AREA_MANAGER,
STORE_MANAGER, ACCOUNTANT), so three of its routes handed supplier money to the
two manager roles:

  GET /finance/vendor-payments     per supplier: billed, paid, TDS, debit
                                   notes, balance owed.
  GET /finance/cash-flow           vendor_payment_outflow (and folded into
                                   outflows / net_cash_flow) on the org view.
  /finance/bank-statement*         bank lines matched to vendor payments; the
                                   rbac row was the only accounts gate, and a
                                   per-user finance:read / finance:write GRANT
                                   rescues a row deny.

Now each asks the ONE payables rule in the handler (services/cost_mask
can_see_cost(user, "payables") = SUPERADMIN / ADMIN / ACCOUNTANT), and the
rbac rows read rbac_policy._core.ACCOUNTS. A grant can lift the middleware
row; it cannot lift the handler.

The world, by hand (every row dated today, so it is this month's):

  Jharkhand Optical (V-JHK) supplies Dhanbad (BV-DHN-01)
    B-DHN  bill 5000              P-DHN  paid 1000      D-DHN  debit note 200
    ledger: 5000 - 1000 - 200 = OWED 3800
  Pune Lens Co (V-PUN) supplies Pune (WO-PUN-01)
    B-PUN  bill 2240              P-PUN  paid 240
    ledger: 2240 - 240 = OWED 2000
  Paid to suppliers this month, every shop: 1000 + 240 = 1240
  One ordinary expense (Rent 700, Dhanbad) so `outflows` is not trivially 0.

Run: JWT_SECRET_KEY=test ENVIRONMENT=test python -m pytest
     backend/tests/test_finance_supplier_money_accounts_only.py -q
No emoji (Windows cp1252).
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timedelta

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MONGODB_URI", "")

import jwt  # noqa: E402
import pytest  # noqa: E402
from fastapi import Depends, FastAPI, HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import auth as auth_mod  # noqa: E402
from api.routers import finance as finance_pkg  # noqa: E402
from api.routers.auth import get_current_user, require_roles  # noqa: E402
from api.services import cost_mask  # noqa: E402
from api.services import rbac_policy  # noqa: E402
from api.utils.ist import ist_today  # noqa: E402

DHN = "BV-DHN-01"
PUN = "WO-PUN-01"
VA = "V-JHK"
VB = "V-PUN"
TODAY = ist_today().isoformat()

OWED = {VA: 3800.0, VB: 2000.0}
PAID_THIS_MONTH = 1240.0
RENT = 700.0

MANAGERS = ("STORE_MANAGER", "AREA_MANAGER")
# main.py mounts the finance router behind exactly this set.
_FINANCE_ROLES = ("ADMIN", "AREA_MANAGER", "STORE_MANAGER", "ACCOUNTANT")


def _user(role: str, store=None, stores=()) -> dict:
    return {
        "user_id": f"u-{role.lower()}-{store or 'none'}",
        "username": role.lower(),
        "roles": [role],
        "store_ids": list(stores),
        "active_store_id": store,
    }


# ============================================================================
# The engine: the real Mongo CI runs against; mongomock as a dev-box fallback
# ============================================================================


def _seed(db) -> None:
    def ins(coll, docs):
        for d in docs:
            key = next(k for k in d if k.endswith("_id"))
            db[coll].insert_one({"_id": d[key], **d})

    ins(
        "vendors",
        [
            {"vendor_id": VA, "legal_name": "Jharkhand Optical"},
            {"vendor_id": VB, "legal_name": "Pune Lens Co"},
        ],
    )
    ins(
        "vendor_bills",
        [
            {"bill_id": "B-DHN", "vendor_id": VA, "store_id": DHN, "bill_number": "JO-1",
             "bill_date": TODAY, "due_date": TODAY, "total_amount": 5000.0,
             "status": "PARTIAL"},
            {"bill_id": "B-PUN", "vendor_id": VB, "store_id": PUN, "bill_number": "PL-1",
             "bill_date": TODAY, "due_date": TODAY, "total_amount": 2240.0,
             "status": "PARTIAL"},
        ],
    )
    ins(
        "vendor_payments",
        [
            {"payment_id": "P-DHN", "vendor_id": VA, "bill_id": "B-DHN", "store_id": DHN,
             "payment_date": TODAY, "amount": 1000.0, "mode": "NEFT", "reference": "UTR-1"},
            {"payment_id": "P-PUN", "vendor_id": VB, "bill_id": "B-PUN", "store_id": PUN,
             "payment_date": TODAY, "amount": 240.0, "mode": "NEFT", "reference": "UTR-2"},
        ],
    )
    ins(
        "vendor_debit_notes",
        [
            {"debit_note_id": "D-DHN", "vendor_id": VA, "bill_id": "B-DHN", "date": TODAY,
             "amount": 200.0, "reason": "short supply"},
        ],
    )
    ins(
        "expenses",
        [
            {"expense_id": "E-RENT", "store_id": DHN, "category": "Rent", "amount": RENT,
             "expense_date": TODAY, "status": "APPROVED"},
        ],
    )
    ins(
        "bank_statements",
        [
            {
                "statement_id": "BS-DHN-1",
                "store_id": DHN,
                "account_name": "HDFC current",
                "filename": "hdfc.csv",
                "uploaded_at": f"{TODAY}T10:00:00",
                "row_count": 1,
                "summary": {"total": 1, "matched_payments": 1, "total_debits": 1000.0},
                "rows": [
                    {"date": TODAY, "description": "NEFT JHARKHAND OPTICAL", "debit": 1000.0,
                     "credit": 0.0, "balance": None, "match_type": "PAYMENT",
                     "match": {"id": "P-DHN", "type": "payment", "amount": 1000.0,
                               "date": TODAY, "reference": VA}},
                ],
                "status": "PENDING_REVIEW",
            }
        ],
    )


class _DBProxy:
    def __init__(self, db):
        self._db = db
        self.is_connected = True

    def get_collection(self, name):
        return self._db[name]

    def __getitem__(self, name):
        return self._db[name]


@pytest.fixture(scope="module")
def mongo_client():
    """One engine per module (the 2s probe for a real Mongo runs once)."""
    from pymongo import MongoClient

    uri = os.getenv("MONGODB_URL") or os.getenv("MONGODB_URI") or "mongodb://localhost:27017"
    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=2000)
        client.server_info()
    except Exception:
        try:
            import mongomock
        except ImportError:
            pytest.skip("no Mongo and no mongomock available")
            return
        client = mongomock.MongoClient()
    try:
        yield client
    finally:
        client.close()


@pytest.fixture
def mongo_db(mongo_client):
    """A fresh, seeded database per test (the import test writes a statement)."""
    db_name = f"ims_test_supmoney_{uuid.uuid4().hex[:8]}"
    db = mongo_client[db_name]
    _seed(db)
    try:
        yield db
    finally:
        try:
            mongo_client.drop_database(db_name)
        except Exception:
            pass


@pytest.fixture
def finance_db(mongo_db, monkeypatch):
    proxy = _DBProxy(mongo_db)
    monkeypatch.setattr(finance_pkg, "_get_db", lambda: proxy)
    return proxy


# ============================================================================
# The finance router as main.py mounts it (the _FINANCE_ROLES gate), the
# caller injected -- the handler is the thing under test here
# ============================================================================


@pytest.fixture
def finance(finance_db):
    app = FastAPI()
    app.include_router(
        finance_pkg.router,
        prefix="/api/v1/finance",
        dependencies=[Depends(require_roles(*_FINANCE_ROLES))],
    )
    client = TestClient(app)

    def get(path, user, **params):
        async def _as_user():
            return dict(user)

        app.dependency_overrides[get_current_user] = _as_user
        params = {k: v for k, v in params.items() if v is not None}
        return client.get(f"/api/v1/finance{path}", params=params)

    return get


def _numbers(node, out=None):
    """Every number anywhere in a JSON body."""
    out = [] if out is None else out
    if isinstance(node, bool):
        return out
    if isinstance(node, (int, float)):
        out.append(float(node))
    elif isinstance(node, dict):
        for v in node.values():
            _numbers(v, out)
    elif isinstance(node, (list, tuple)):
        for v in node:
            _numbers(v, out)
    return out


# ============================================================================
# 1. GET /finance/vendor-payments -- per-supplier balances
# ============================================================================


@pytest.mark.parametrize("role", MANAGERS)
@pytest.mark.parametrize(
    "active, stores, asked",
    [
        (DHN, (DHN,), None),  # store_id dropped -> its own shop
        (DHN, (DHN,), DHN),  # its own shop, asked for by name
        (PUN, (PUN,), PUN),
        (None, (), None),  # no active shop at all (the org view)
    ],
)
def test_managers_are_refused_supplier_balances(finance, role, active, stores, asked):
    """A manager passes the finance router's gate but never reads a supplier's
    billed / paid / owed -- not for its own shop, not with store_id dropped,
    not as the org view."""
    resp = finance("/vendor-payments", _user(role, active, stores), store_id=asked)
    assert resp.status_code == 403, f"{role} read supplier balances: {resp.text}"
    assert resp.json()["detail"] == "Supplier payments are ADMIN / ACCOUNTANT only"
    for figure in (*OWED.values(), 5000.0, 2240.0, 1000.0, 240.0):
        assert str(figure).rstrip("0").rstrip(".") not in resp.text


@pytest.mark.parametrize("role", ["ADMIN", "SUPERADMIN"])
def test_admins_keep_every_suppliers_balance(finance, role):
    resp = finance("/vendor-payments", _user(role, DHN, (DHN,)))
    assert resp.status_code == 200, resp.text
    rows = {r["vendor_id"]: r for r in resp.json()}
    assert {v: r["balance"] for v, r in rows.items()} == OWED
    assert rows[VA]["total_billed"] == 5000.0
    assert rows[VA]["total_paid"] == 1000.0
    assert rows[VA]["total_debit_notes"] == 200.0


def test_an_accountant_with_no_shop_keeps_every_suppliers_balance(finance):
    resp = finance("/vendor-payments", _user("ACCOUNTANT"))
    assert resp.status_code == 200, resp.text
    assert {r["vendor_id"]: r["balance"] for r in resp.json()} == OWED


@pytest.mark.parametrize("asked", [None, PUN])
def test_a_pune_accountant_still_gets_punes_share_only(finance, asked):
    """The payables gate comes first; the shop scope (F63) still applies to the
    accounts role it lets through."""
    resp = finance("/vendor-payments", _user("ACCOUNTANT", PUN, (PUN,)), store_id=asked)
    assert resp.status_code == 200, resp.text
    rows = {r["vendor_id"]: r for r in resp.json()}
    assert {v: r["balance"] for v, r in rows.items()} == {VA: 0.0, VB: 2000.0}
    assert rows[VA]["total_billed"] == 0.0 and rows[VA]["total_paid"] == 0.0


def test_a_pune_accountant_cannot_ask_for_dhanbad(finance):
    resp = finance("/vendor-payments", _user("ACCOUNTANT", PUN, (PUN,)), store_id=DHN)
    assert resp.status_code == 403, resp.text


# ============================================================================
# 2. GET /finance/cash-flow -- money paid to suppliers this month
# ============================================================================


@pytest.mark.parametrize("role", MANAGERS)
def test_a_manager_with_no_shop_gets_no_supplier_payments_in_cash_flow(finance, role):
    """The org view (no active shop) is the only view that ever folded supplier
    payments in. A manager gets it WITHOUT them: no key, not inside outflows /
    net_cash_flow, a flag instead of a figure."""
    resp = finance("/cash-flow", _user(role))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "vendor_payment_outflow" not in body, body
    assert body.get("vendor_payments_restricted") is True, body
    assert body["expense_outflow"] == RENT
    # The trap: the figure must not ride inside the totals either.
    assert body["outflows"] == pytest.approx(body["expense_outflow"] + body["purchase_outflow"])
    assert body["net_cash_flow"] == pytest.approx(body["inflows"] - body["outflows"])
    assert PAID_THIS_MONTH not in _numbers(body), body
    assert RENT + PAID_THIS_MONTH not in _numbers(body), body


@pytest.mark.parametrize("role", MANAGERS)
def test_a_managers_shop_view_carries_no_supplier_payments(finance, role):
    resp = finance("/cash-flow", _user(role, DHN, (DHN,)), store_id=DHN)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "vendor_payment_outflow" not in body, body
    assert "vendor_payments_restricted" not in body, body  # a shop view folds none in
    assert body["outflows"] == RENT


@pytest.mark.parametrize("role", ["ADMIN", "SUPERADMIN", "ACCOUNTANT"])
def test_the_accounts_roles_keep_supplier_payments_on_the_org_view(finance, role):
    body = finance("/cash-flow", _user(role)).json()
    assert body["vendor_payment_outflow"] == PAID_THIS_MONTH
    assert body["outflows"] == pytest.approx(RENT + PAID_THIS_MONTH)
    assert "vendor_payments_restricted" not in body


def test_an_admins_shop_view_still_folds_no_hq_payments_in(finance):
    body = finance("/cash-flow", _user("ADMIN", DHN, (DHN,)), store_id=DHN).json()
    assert body["vendor_payment_outflow"] == 0.0
    assert body["outflows"] == RENT


# ============================================================================
# 3. The owner-financials gate IS the payables rule (one rule, no own list)
# ============================================================================


def test_the_owner_financials_gate_asks_the_payables_rule(monkeypatch):
    """_require_finance_admin (bank statements, owner dashboard, forecast,
    survival, AP aging, ITC, Tally ...) keeps no role list of its own: widen or
    narrow "payables" and it moves with it."""
    gate = finance_pkg._require_finance_admin
    gate(_user("ACCOUNTANT"))
    with pytest.raises(HTTPException):
        gate(_user("AUDITOR_PROBE"))
    monkeypatch.setitem(cost_mask._CONTEXT_ROLES, "payables", {"AUDITOR_PROBE"})
    gate(_user("AUDITOR_PROBE"))  # the payables rule admits it -> so does the gate


@pytest.mark.parametrize(
    "role",
    ["SUPERADMIN", "ADMIN", "ACCOUNTANT", "AREA_MANAGER", "STORE_MANAGER",
     "CATALOG_MANAGER", "OPTOMETRIST", "SALES_STAFF", "CASHIER", "WORKSHOP_STAFF"],
)
def test_the_owner_financials_gate_matches_the_payables_rule_role_by_role(role):
    user = _user(role)
    try:
        finance_pkg._require_finance_admin(user)
        passed = True
    except HTTPException as exc:
        assert exc.status_code == 403
        passed = False
    assert passed is cost_mask.can_see_cost(user, "payables"), role
    assert passed is (role in {"SUPERADMIN", "ADMIN", "ACCOUNTANT"}), role


# ============================================================================
# 4. The rbac rows are the accounts list
# ============================================================================


@pytest.mark.parametrize(
    "method, path",
    [
        ("GET", "/api/v1/finance/vendor-payments"),
        ("POST", "/api/v1/finance/bank-statement/import"),
        ("GET", "/api/v1/finance/bank-statement"),
        ("GET", "/api/v1/finance/bank-statement/{statement_id}"),
    ],
)
def test_the_rbac_rows_are_the_accounts_roles(method, path):
    row = next(
        r for r in rbac_policy.POLICY if r["method"] == method and r["path"] == path
    )
    assert sorted(row["allowed"]) == sorted(cost_mask.AP_ROLES), row
    for role in MANAGERS:
        assert not rbac_policy.check_access(method, path.replace("{statement_id}", "S1"), [role])


# ============================================================================
# 5. Through the REAL app: a capability GRANT lifts the middleware row, never
#    the handler. (This is the only way a manager ever reached bank statements.)
# ============================================================================

_MW_FORBIDDEN = "Forbidden:"
_HANDLER_403 = "Owner financials require ADMIN / ACCOUNTANT"


def _token(role, uid, store=DHN):
    return jwt.encode(
        {
            "sub": uid,
            "user_id": uid,
            "username": uid,
            "roles": [role],
            "store_ids": [store],
            "active_store_id": store,
            "exp": datetime.utcnow() + timedelta(hours=1),
        },
        auth_mod.SECRET_KEY,
        algorithm=auth_mod.ALGORITHM,
    )


class _GrantRepo:
    """The middleware's live override lookup: user_id -> stored permissions."""

    def __init__(self, uid, permissions):
        self._doc = {"user_id": uid, "username": uid, "is_active": True,
                     "permissions": permissions, "module_access": None}

    def find_by_id(self, uid):
        return dict(self._doc) if uid == self._doc["user_id"] else None


@pytest.fixture
def real_app(client, finance_db, monkeypatch):
    from api import dependencies as deps

    def call(method, path, role, grant=None, **kw):
        uid = f"grant-{role.lower()}-{uuid.uuid4().hex[:6]}"
        repo = _GrantRepo(uid, {"grant": {grant: True}} if grant else None)
        monkeypatch.setattr(deps, "get_user_repository", lambda: repo)
        headers = {"Authorization": f"Bearer {_token(role, uid)}"}
        return client.request(method, path, headers=headers, **kw)

    return call


def _csv_upload():
    csv_text = (
        "Date,Description,Debit,Credit,Balance\n"
        f"{TODAY},NEFT JHARKHAND OPTICAL,1000,,50000\n"
    )
    return {"file": ("hdfc.csv", csv_text.encode("utf-8"), "text/csv")}


_BANK_READS = [
    ("GET", "/api/v1/finance/bank-statement", "finance:read"),
    ("GET", "/api/v1/finance/bank-statement/BS-DHN-1", "finance:read"),
    ("POST", "/api/v1/finance/bank-statement/import", "finance:write"),
]


@pytest.mark.parametrize("role", MANAGERS)
@pytest.mark.parametrize("method, path, capability", _BANK_READS)
def test_a_granted_manager_is_still_refused_bank_statements(real_app, role, method, path, capability):
    kw = {"files": _csv_upload()} if method == "POST" else {}
    resp = real_app(method, path, role, grant=capability, **kw)
    assert resp.status_code == 403, f"{role} + {capability} grant read {path}: {resp.text}"
    detail = resp.json().get("detail", "")
    # The grant DID lift the middleware row -- this 403 is the handler's own.
    assert not detail.startswith(_MW_FORBIDDEN), detail
    assert detail == _HANDLER_403, detail
    assert "P-DHN" not in resp.text and "V-JHK" not in resp.text


@pytest.mark.parametrize("role", MANAGERS)
def test_an_ungranted_manager_is_refused_by_the_row(real_app, role):
    resp = real_app("GET", "/api/v1/finance/bank-statement", role)
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"].startswith(_MW_FORBIDDEN)


@pytest.mark.parametrize("role", MANAGERS)
def test_a_granted_manager_is_still_refused_supplier_balances(real_app, role):
    resp = real_app("GET", "/api/v1/finance/vendor-payments", role, grant="finance:read")
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == "Supplier payments are ADMIN / ACCOUNTANT only"


def test_the_accountant_keeps_bank_statements_and_their_supplier_matches(real_app):
    listed = real_app("GET", "/api/v1/finance/bank-statement", "ACCOUNTANT")
    assert listed.status_code == 200, listed.text
    assert [s["statement_id"] for s in listed.json()["statements"]] == ["BS-DHN-1"]

    one = real_app("GET", "/api/v1/finance/bank-statement/BS-DHN-1", "ACCOUNTANT")
    assert one.status_code == 200, one.text
    assert one.json()["rows"][0]["match"]["reference"] == VA

    imported = real_app("POST", "/api/v1/finance/bank-statement/import", "ACCOUNTANT",
                        files=_csv_upload())
    assert imported.status_code == 200, imported.text
    row = imported.json()["rows"][0]
    match = row["match"]
    assert (row["match_type"], match["id"], match["reference"]) == ("PAYMENT", "P-DHN", VA)
