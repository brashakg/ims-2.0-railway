"""
IMS 2.0 -- THE ONE 'WE OWE' RULE ON EVERY FINANCE FIGURE (audit F56, round 3 #1)
================================================================================
Review round 3, finding #1 (finance side). One supplier holding an advance made
'we owe' read four ways: the Cash Flow card said Rs 0 (Rs 5,000 owed less
another supplier's Rs 10,000 advance, floored at 0) with 'Rs 5,000 overdue'
under it, its note read 'Less Rs 10,000 paid ... = Rs 0 owed', AP aging's total
was the same floored figure, and Vendor Payments summed the signed balances to
-Rs 5,000.

A12 supersedes the old automatic-allocation assumption: invoice aging is
gross unsettled bills, while unapplied money remains separate. Supplier
ledger/card balances stay signed, and net_position subtracts only positive
supplier nets. Neither invoices nor net_position cross-net suppliers.

The world, by hand. The clock is frozen at 2026-10-01 12:00 IST. Both
suppliers supply Dhanbad (BV-DHN-01); Pune (WO-PUN-01) has nothing.

  Advance Frames (V-ADV)
    BA   bill 2026-08-01, due 2026-08-31                   1000
    PA   11000 paid on account (no bill), 2026-08-02, stamped Dhanbad
    ledger: 1000 - 11000 = -10000   (Rs 10,000 paid ahead)

  Owed Lens Co (V-OWE)
    BB   bill 2026-08-10, due 2026-09-09 (22 days OVERDUE) 5000, nothing paid
    ledger: 5000

  Gross invoices 6000, unapplied 11000; positive supplier nets 5000 and
  negative supplier nets 10000. Same for all stores/Dhanbad; Pune: all zero.

Run: JWT_SECRET_KEY=test ENVIRONMENT=test python -m pytest
     backend/tests/test_we_owe_one_rule_finance.py -q
No emoji (Windows cp1252).
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MONGODB_URI", "")

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import finance as finance_pkg  # noqa: E402
from api.routers import vendors as vendors_pkg  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402
from api.services import ap_engine  # noqa: E402

DHN = "BV-DHN-01"
PUN = "WO-PUN-01"
V_ADV = "V-ADV"
V_OWE = "V-OWE"

# IST wall clock, the frame now_ist_naive() returns.
NOW = datetime(2026, 10, 1, 12, 0, 0)

OWED = 5000.0
ADVANCES = 10000.0
UNPAID_INVOICES = 6000.0
UNAPPLIED = 11000.0


def _user(role: str, store=None, stores=()) -> dict:
    return {
        "user_id": f"u-{role.lower()}-{store or 'all'}",
        "username": role.lower(),
        "roles": [role],
        "store_ids": list(stores),
        "active_store_id": store,
    }


ADMIN = _user("ADMIN", DHN)
SUPERADMIN = _user("SUPERADMIN")
ACCT_DHN = _user("ACCOUNTANT", DHN, (DHN,))
ACCT_PUN = _user("ACCOUNTANT", PUN, (PUN,))

# The bills / payments / notes as the ledger holds them (engine-level tests).
BILLS = [
    {"bill_id": "BA", "vendor_id": V_ADV, "vendor_name": "Advance Frames", "store_id": DHN,
     "bill_number": "INV-BA", "bill_date": "2026-08-01", "due_date": "2026-08-31",
     "total_amount": 1000.0, "status": "PAID"},
    {"bill_id": "BB", "vendor_id": V_OWE, "vendor_name": "Owed Lens Co", "store_id": DHN,
     "bill_number": "INV-BB", "bill_date": "2026-08-10", "due_date": "2026-09-09",
     "total_amount": 5000.0, "status": "OUTSTANDING"},
]
PAYMENTS = [
    {"payment_id": "PA", "vendor_id": V_ADV, "vendor_name": "Advance Frames", "bill_id": None,
     "store_id": DHN, "amount": 11000.0, "tds_amount": 0.0, "mode": "BANK",
     "payment_date": "2026-08-02"},
]


def _seed(db) -> None:
    def ins(coll, *docs):
        for d in docs:
            db[coll].insert_one(dict(d))

    ins(
        "vendors",
        {"vendor_id": V_ADV, "legal_name": "Advance Frames", "trade_name": "Advance Frames"},
        {"vendor_id": V_OWE, "legal_name": "Owed Lens Co", "trade_name": "Owed Lens Co"},
    )
    ins("vendor_bills", *BILLS)
    ins("vendor_payments", *PAYMENTS)


@pytest.fixture(scope="module")
def mongo_db():
    """Seeded once: every test here only reads."""
    from pymongo import MongoClient

    uri = (
        os.getenv("MONGODB_URL")
        or os.getenv("MONGODB_URI")
        or "mongodb://localhost:27017"
    )
    db_name = f"ims_test_weowe_{uuid.uuid4().hex[:8]}"
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
    db = client[db_name]
    _seed(db)
    try:
        yield db
    finally:
        try:
            client.drop_database(db_name)
        except Exception:
            pass
        client.close()


class _DBProxy:
    def __init__(self, db):
        self._db = db
        self.is_connected = True

    def get_collection(self, name):
        return self._db[name]

    def __getitem__(self, name):
        return self._db[name]

    def __getattr__(self, name):
        return self._db[name]


class _World:
    def __init__(self, app: FastAPI):
        self._app = app
        self._client = TestClient(app)

    def ok(self, path: str, user: dict, **params):
        async def _u():
            return dict(user)

        self._app.dependency_overrides[get_current_user] = _u
        resp = self._client.get(path, params={k: v for k, v in params.items() if v is not None})
        assert resp.status_code == 200, (path, resp.status_code, resp.text)
        return resp.json()


@pytest.fixture
def world(mongo_db, monkeypatch):
    proxy = _DBProxy(mongo_db)
    monkeypatch.setattr(finance_pkg, "_get_db", lambda: proxy)
    monkeypatch.setattr(vendors_pkg, "_get_db", lambda: proxy)
    monkeypatch.setattr(finance_pkg, "now_ist_naive", lambda: NOW)
    monkeypatch.setattr(finance_pkg, "ist_today", lambda: NOW.date())
    monkeypatch.setattr(ap_engine, "now_ist_naive", lambda: NOW)
    app = FastAPI()
    app.include_router(vendors_pkg.router, prefix="/vendors")
    app.include_router(finance_pkg.router, prefix="/finance")
    return _World(app)


# ============================================================================
# The anchor: each supplier's own ledger, signed
# ============================================================================


def test_each_supplier_ledger_is_signed_and_its_own(world):
    """Not the finding -- the fixture proven by the ledger: Advance Frames is
    Rs 10,000 ahead, Owed Lens Co is owed Rs 5,000."""
    for vendor, closing in ((V_ADV, -ADVANCES), (V_OWE, OWED)):
        ledger = world.ok(f"/vendors/{vendor}/ledger", ADMIN)["ledger"]
        assert ledger["closing_balance"] == pytest.approx(closing), (vendor, ledger)


# ============================================================================
# The engine: owed and advances apart, never netted across suppliers
# ============================================================================


def test_engine_has_6000_unpaid_invoices_and_11000_unapplied_never_netted():
    for ag in (
        ap_engine.build_aging(BILLS, PAYMENTS, [], "2026-10-01"),
        ap_engine.build_aging_by_vendor(BILLS, PAYMENTS, [], "2026-10-01")["totals"],
    ):
        assert ag["owed"] == pytest.approx(UNPAID_INVOICES), ag
        assert ag["advances"] == pytest.approx(UNAPPLIED), ag
        # The older keys: net_payable IS owed (no floor hiding a subtraction),
        # unallocated_credits IS advances.
        assert ag["net_payable"] == pytest.approx(UNPAID_INVOICES), ag
        assert ag["total_outstanding"] == pytest.approx(UNPAID_INVOICES), ag
        assert ag["unallocated_credits"] == pytest.approx(UNAPPLIED), ag
        # The bars add up to what we owe.
        assert sum(ag["buckets"].values()) == pytest.approx(UNPAID_INVOICES), ag


def test_engine_one_suppliers_advance_never_settles_anothers_bill():
    ag = ap_engine.build_aging(BILLS, PAYMENTS, [], "2026-10-01")
    assert [(it["bill_id"], it["outstanding"]) for it in ag["items"]] == [("BA", 1000.0), ("BB", OWED)]
    rows = {v["vendor_id"]: v for v in ap_engine.build_aging_by_vendor(BILLS, PAYMENTS, [], "2026-10-01")["vendors"]}
    assert (rows[V_OWE]["owed"], rows[V_OWE]["advances"], rows[V_OWE]["balance"]) == (OWED, 0.0, OWED)
    assert (rows[V_ADV]["owed"], rows[V_ADV]["advances"], rows[V_ADV]["balance"]) == (1000.0, UNAPPLIED, -ADVANCES)
    assert rows[V_OWE]["net_payable"] == OWED and rows[V_ADV]["net_payable"] == 1000.0


# ============================================================================
# Every finance figure, for all stores and for the shop
# ============================================================================

# (who, ?store_id) -> the scope every figure below must be for.
_ALL = {
    "admin, all stores": (ADMIN, None),
    "superadmin, all stores": (SUPERADMIN, None),
    "admin picks Dhanbad": (ADMIN, DHN),
    "Dhanbad accountant (own shop)": (ACCT_DHN, None),
}


@pytest.mark.parametrize("case", list(_ALL))
def test_ap_aging_shows_6000_invoices_and_11000_unapplied_apart(world, case):
    user, store = _ALL[case]
    totals = world.ok("/vendors/ap-aging", user, store_id=store)["totals"]
    assert totals["owed"] == pytest.approx(UNPAID_INVOICES), totals
    assert totals["net_payable"] == pytest.approx(UNPAID_INVOICES), totals
    assert totals["total_outstanding"] == pytest.approx(UNPAID_INVOICES), totals
    assert sum(totals["buckets"].values()) == pytest.approx(UNPAID_INVOICES), totals
    assert totals["advances"] == pytest.approx(UNAPPLIED), totals
    assert totals["unallocated_credits"] == pytest.approx(UNAPPLIED), totals


def _dashboard_cases():
    # The owner dashboard takes no ?store_id: an admin reads every shop, a
    # shop accountant his own shop (resolve_store_scope).
    return {"admin": (ADMIN, None), "superadmin": (SUPERADMIN, None), "Dhanbad accountant": (ACCT_DHN, DHN)}


@pytest.mark.parametrize("case", list(_dashboard_cases()))
def test_cash_flow_card_gross_invoices_and_unapplied_preserve_net_position(world, case):
    user, shop = _dashboard_cases()[case]
    body = world.ok("/finance/owner-dashboard", user)
    assert body["store_id"] == shop
    p = body["payables"]
    assert p["total"] == pytest.approx(UNPAID_INVOICES), p
    assert p["advances"] == pytest.approx(UNAPPLIED), p
    assert p["unallocated_credits"] == pytest.approx(UNAPPLIED), p
    # Both invoices are overdue and remain open until explicitly settled.
    assert sum(p["buckets"].values()) == pytest.approx(UNPAID_INVOICES), p
    assert p["overdue"] == pytest.approx(UNPAID_INVOICES), p
    # Net position still subtracts positive supplier nets, never another's advance.
    assert body["net_position"] == pytest.approx(body["receivables"]["total"] - OWED)
    overdue_alert = [a for a in body["alerts"] if "payables overdue" in a.get("label_template", "")]
    assert [a["amount"] for a in overdue_alert] == [UNPAID_INVOICES]


@pytest.mark.parametrize("case", list(_ALL))
def test_vendor_payments_rows_stay_signed_and_split_into_owed_and_advances(world, case):
    user, store = _ALL[case]
    rows = world.ok("/finance/vendor-payments", user, store_id=store)
    balances = {r["vendor_id"]: r["balance"] for r in rows}
    assert balances == {V_ADV: -ADVANCES, V_OWE: OWED}
    assert sum(max(b, 0.0) for b in balances.values()) == pytest.approx(OWED)
    assert sum(max(-b, 0.0) for b in balances.values()) == pytest.approx(ADVANCES)


def test_invoice_and_supplier_net_views_preserve_their_distinct_meanings(world):
    """Gross invoices/unapplied money reconcile to the signed supplier nets."""
    dash = world.ok("/finance/owner-dashboard", ADMIN)["payables"]
    aging = world.ok("/vendors/ap-aging", ADMIN)["totals"]
    vp = [r["balance"] for r in world.ok("/finance/vendor-payments", ADMIN)]
    ledgers = [world.ok(f"/vendors/{v}/ledger", ADMIN)["ledger"]["closing_balance"] for v in (V_ADV, V_OWE)]
    owed = {
        "cash_flow": dash["total"],
        "ap_aging": aging["owed"],
        "ap_aging_net_payable": aging["net_payable"],
        "vendor_payments": round(sum(max(b, 0.0) for b in vp), 2),
        "ledgers": round(sum(max(b, 0.0) for b in ledgers), 2),
    }
    ahead = {
        "cash_flow": dash["advances"],
        "ap_aging": aging["advances"],
        "vendor_payments": round(sum(max(-b, 0.0) for b in vp), 2),
        "ledgers": round(sum(max(-b, 0.0) for b in ledgers), 2),
    }
    assert owed == {"cash_flow": UNPAID_INVOICES, "ap_aging": UNPAID_INVOICES,
                    "ap_aging_net_payable": UNPAID_INVOICES, "vendor_payments": OWED, "ledgers": OWED}
    assert ahead == {"cash_flow": UNAPPLIED, "ap_aging": UNAPPLIED,
                     "vendor_payments": ADVANCES, "ledgers": ADVANCES}
    assert UNPAID_INVOICES - UNAPPLIED == OWED - ADVANCES


def test_pune_sees_none_of_dhanbads_debt_or_advance(world):
    """The shop rule: a Pune accountant's figures are Pune's (nothing here)."""
    dash = world.ok("/finance/owner-dashboard", ACCT_PUN)
    assert dash["store_id"] == PUN
    assert (dash["payables"]["total"], dash["payables"]["advances"]) == (0.0, 0.0)
    totals = world.ok("/vendors/ap-aging", ACCT_PUN)["totals"]
    assert (totals["owed"], totals["advances"], totals["net_payable"]) == (0.0, 0.0, 0.0)
    rows = world.ok("/finance/vendor-payments", ACCT_PUN)
    assert all(r["balance"] == 0.0 for r in rows), rows
