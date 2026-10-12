"""
IMS 2.0 -- A LOGIN WITH NO SHOP SEES NO SHOP (owner ruling 2026-10-12, R4)
=========================================================================
"A non-admin login with NO shop assigned (no active_store_id) sees NO shop
until an admin assigns one: empty lists and a clear 'No shop is assigned to
this login' message; never every shop." (Control over Convenience.)

THE HOLE. api.dependencies.resolve_store_scope -- the ONE shop rule every
Purchase tab, AP aging, /finance/vendor-payments, the owner dashboard, cash
reconciliation, ITC, inventory analytics and the item ledger read -- returned
None for a non-admin whose token carried no active_store_id. Every caller does
``if scope: flt["store_id"] = scope``, so None meant NO filter: a storeless
ACCOUNTANT read every shop's supplier bills, what the business owes, and every
shop's till closes. (can_access_store_scoped already gave the same login
nothing -- the list doors and the by-id doors disagreed.)

THE RULE NOW. Such a login resolves to NO_SHOP_SCOPE, a reserved id no store
carries: the same caller line matches nothing, so the lists come back EMPTY
(never a crash, never all shops). Naming a shop is a 403 worded
NO_SHOP_DETAIL. A multi-store AREA_MANAGER whose token has store_ids but no
active store keeps its OWN reach: it resolves to its first own store (the one
login makes active), never every shop and never nothing. ADMIN / SUPERADMIN
are unchanged (None = every shop; any shop asked for).

The same rule reaches the doors that keep their own copy of it:
  * finance/_shared._scope_store (revenue, P&L, receivables, journal
    entries, the journal JV export) and GET /finance/cash-flow resolve such a
    login to NO_SHOP_SCOPE too -- they used to give it every shop's revenue,
    P&L, cash and journal entries (HQ vouchers included) while naming a shop
    was a 403;
  * a WRITE never stamps the reserved id: a supplier payment / debit note
    (vendors/ap_payments._money_shop; tests/test_money_stamped_at_write.py)
    and a journal entry are refused, 403 NO_SHOP_DETAIL, nothing written;
  * /items/replenishment refuses it (it used to answer the GLOBAL targets
    against an empty shelf: 'order every cell in full').

The world. Two shops, each with its own supplier, bill, order, receipt,
return, till close, paid sale, unpaid sale and journal entry -- so every
'empty' below is checked against an admin who sees BOTH shops on the same
request (the fixture is not empty).

Run: JWT_SECRET_KEY=test ENVIRONMENT=test python -m pytest
     backend/tests/test_no_shop_login_sees_nothing.py -q
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
from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.dependencies import (  # noqa: E402
    NO_SHOP_DETAIL,
    NO_SHOP_SCOPE,
    can_access_store_scoped,
    is_no_shop_scope,
    resolve_store_scope,
    validate_store_access,
)
from api.routers import finance as finance_pkg  # noqa: E402
from api.routers import item_events as item_events_mod  # noqa: E402
from api.routers import purchase_invoices as pinv_mod  # noqa: E402
from api.routers.finance._shared import _scope_store as finance_scope  # noqa: E402
from api.routers import vendor_returns as vret_mod  # noqa: E402
from api.routers import vendors as vendors_pkg  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402
from api.services import ap_engine, je_service  # noqa: E402

DHN = "BV-DHN-01"
PUN = "WO-PUN-01"
BOK = "BV-BOK-01"  # a shop nobody here holds
V_DHN = "V-DHN"
V_PUN = "V-PUN"

# IST wall clock, the frame now_ist_naive() returns.
NOW = datetime(2026, 10, 12, 12, 0, 0)


def _user(role, active=None, stores=()):
    return {
        "user_id": f"u-{role.lower()}-{active or 'none'}-{len(stores)}",
        "username": role.lower(),
        "roles": [role],
        "store_ids": list(stores),
        "active_store_id": active,
    }


# The logins of the ruling: NO active shop, NO store_ids.
ACCT_NONE = _user("ACCOUNTANT")
MGR_NONE = _user("STORE_MANAGER")
# A multi-store area manager whose token carries its stores but no active one.
AREA_TWO = _user("AREA_MANAGER", None, (PUN, DHN))
# The controls.
ADMIN = _user("ADMIN", DHN)
SUPERADMIN = _user("SUPERADMIN")
ACCT_PUN = _user("ACCOUNTANT", PUN, (PUN,))

# Every non-admin role the app issues (CLAUDE.md, 11 roles).
NON_ADMIN_ROLES = [
    "AREA_MANAGER",
    "STORE_MANAGER",
    "ACCOUNTANT",
    "CATALOG_MANAGER",
    "OPTOMETRIST",
    "SALES_CASHIER",
    "SALES_STAFF",
    "CASHIER",
    "WORKSHOP_STAFF",
]


# ============================================================================
# The rule itself (api.dependencies)
# ============================================================================


@pytest.mark.parametrize("role", NON_ADMIN_ROLES)
def test_a_storeless_non_admin_resolves_to_no_shop_never_every_shop(role):
    for token in (
        _user(role),
        {**_user(role), "store_ids": None},
        # junk a bad import can leave behind is no shop either
        {**_user(role), "store_ids": [None, ""], "active_store_id": ""},
    ):
        scope = resolve_store_scope(None, token)
        assert scope is not None, f"{role} with no shop resolved to every shop"
        assert scope == NO_SHOP_SCOPE and is_no_shop_scope(scope)
        # the scope is a plain string no real store id can be
        assert isinstance(scope, str) and scope not in (DHN, PUN, BOK)


@pytest.mark.parametrize("role", NON_ADMIN_ROLES)
def test_a_storeless_non_admin_naming_any_shop_is_a_403_that_says_why(role):
    for door in (resolve_store_scope, validate_store_access):
        with pytest.raises(HTTPException) as ei:
            door(DHN, _user(role))
        assert ei.value.status_code == 403
        assert ei.value.detail == NO_SHOP_DETAIL
    # the by-id rule already agreed: no object of any shop
    assert can_access_store_scoped(DHN, _user(role)) is False


def test_the_no_shop_message_is_the_owners_words():
    assert NO_SHOP_DETAIL.startswith("No shop is assigned to this login.")


def test_an_area_manager_with_store_ids_but_no_active_shop_keeps_its_own_reach():
    # Dropped store_id: its first own store -- the one login makes active --
    # never every shop (None) and never nothing (NO_SHOP_SCOPE).
    assert resolve_store_scope(None, AREA_TWO) == PUN
    # Either of its stores may be asked for; another region's is a 403 with
    # the ordinary wording (it does hold shops).
    assert resolve_store_scope(DHN, AREA_TWO) == DHN
    assert resolve_store_scope(PUN, AREA_TWO) == PUN
    with pytest.raises(HTTPException) as ei:
        resolve_store_scope(BOK, AREA_TWO)
    assert ei.value.status_code == 403
    assert ei.value.detail == f"No access to store {BOK}"


def test_admins_and_shop_logins_are_unchanged():
    assert resolve_store_scope(None, ADMIN) is None
    assert resolve_store_scope(None, SUPERADMIN) is None
    assert resolve_store_scope(BOK, SUPERADMIN) == BOK
    assert resolve_store_scope(PUN, ADMIN) == PUN
    assert resolve_store_scope(None, ACCT_PUN) == PUN
    area_active = _user("AREA_MANAGER", DHN, (PUN, DHN))
    assert resolve_store_scope(None, area_active) == DHN  # the active one wins
    with pytest.raises(HTTPException) as ei:
        resolve_store_scope(DHN, ACCT_PUN)
    assert ei.value.detail == f"No access to store {DHN}"


# ============================================================================
# The real routers on a seeded two-shop world
# ============================================================================


def _seed(db) -> None:
    def ins(coll, *docs):
        for d in docs:
            db[coll].insert_one(dict(d))

    ins(
        "vendors",
        {"vendor_id": V_DHN, "legal_name": "Dhanbad Frames", "trade_name": "Dhanbad Frames"},
        {"vendor_id": V_PUN, "legal_name": "Pune Lenses", "trade_name": "Pune Lenses"},
    )
    ins(
        "vendor_bills",
        {"bill_id": "B-DHN", "vendor_id": V_DHN, "vendor_name": "Dhanbad Frames",
         "store_id": DHN, "bill_number": "INV-D1", "bill_date": "2026-09-05",
         "due_date": "2026-10-05", "total_amount": 1000.0, "status": "OUTSTANDING"},
        {"bill_id": "B-PUN", "vendor_id": V_PUN, "vendor_name": "Pune Lenses",
         "store_id": PUN, "bill_number": "INV-P1", "bill_date": "2026-09-06",
         "due_date": "2026-10-06", "total_amount": 500.0, "status": "OUTSTANDING"},
    )
    frame = {"product_id": "P-FRAME", "quantity": 1, "ordered_qty": 1,
             "unit_price": 1000.0, "tax_rate": 5.0}
    ins(
        "purchase_orders",
        {"po_id": "PO-DHN", "po_number": "PO-DHN", "vendor_id": V_DHN,
         "delivery_store_id": DHN, "status": "SENT", "total_amount": 1050.0,
         "created_at": "2026-09-01T10:00:00", "sent_at": "2026-09-01T10:05:00",
         "items": [frame]},
        {"po_id": "PO-PUN", "po_number": "PO-PUN", "vendor_id": V_PUN,
         "delivery_store_id": PUN, "status": "SENT", "total_amount": 525.0,
         "created_at": "2026-09-02T10:00:00", "sent_at": "2026-09-02T10:05:00",
         "items": [dict(frame, unit_price=500.0)]},
    )
    ins(
        "vendor_returns",
        {"return_id": "VR-DHN", "return_number": "VR-DHN", "vendor_id": V_DHN,
         "store_id": DHN, "status": "DRAFT", "created_at": "2026-09-10T10:00:00",
         "items": [{"product_id": "P-FRAME", "quantity": 1, "reason": "DEFECT"}]},
        {"return_id": "VR-PUN", "return_number": "VR-PUN", "vendor_id": V_PUN,
         "store_id": PUN, "status": "DRAFT", "created_at": "2026-09-11T10:00:00",
         "items": [{"product_id": "P-FRAME", "quantity": 1, "reason": "DEFECT"}]},
    )
    # One closed till per shop (naive-UTC ISO, as cash_register writes it):
    # 06:00 UTC is 11:30 IST on the 15th.
    ins(
        "cash_register_sessions",
        *[
            {"session_id": f"CS-{shop}", "status": "CLOSED", "store_id": shop,
             "opened_at": "2026-09-15T03:00:00", "closed_at": "2026-09-15T06:00:00",
             "opening_float": 500.0, "cash_sales": 1500.0, "expected": 2000.0,
             "counted": 2000.0}
            for shop in (DHN, PUN)
        ],
    )
    # One paid sale per shop this month (created_at is a BSON datetime).
    ins(
        "orders",
        *[
            {"order_id": f"O-{shop}", "store_id": shop, "status": "DELIVERED",
             "payment_status": "PAID", "grand_total": total,
             "created_at": datetime(2026, 10, 5, 6, 0, 0)}
            for shop, total in ((DHN, 2000.0), (PUN, 1200.0))
        ],
    )
    # One sale per shop still owed by the customer (August: outside this
    # month's revenue, so the paid-sale figures above are untouched).
    ins(
        "orders",
        {"order_id": "O-DHN-DUE", "store_id": DHN, "status": "DELIVERED",
         "payment_status": "PARTIAL", "grand_total": 800.0, "amount_paid": 300.0,
         "customer_id": "C-1", "customer_name": "Dhanbad buyer",
         "created_at": datetime(2026, 8, 20, 6, 0, 0)},
        {"order_id": "O-PUN-DUE", "store_id": PUN, "status": "DELIVERED",
         "payment_status": "UNPAID", "grand_total": 400.0, "amount_paid": 0.0,
         "customer_id": "C-2", "customer_name": "Pune buyer",
         "created_at": datetime(2026, 8, 21, 6, 0, 0)},
    )
    # One journal voucher per shop and one HQ voucher (no shop). DRAFT, so
    # none of them moves a P&L figure (only POSTED ones do).
    ins(
        "journal_entries",
        *[
            {"je_id": je_id, "je_number": je_id, "store_id": shop, "status": "DRAFT",
             "description": je_id, "lines": [], "total_debit": 0, "total_credit": 0,
             "entry_date": datetime(2026, 10, 1), "created_at": datetime(2026, 10, 1, 9, 0, 0)}
            for je_id, shop in (("JE-DHN", DHN), ("JE-PUN", PUN), ("JE-HQ", None))
        ],
    )
    # Readers: one GLOBAL target cell, and Dhanbad already holds it in full.
    ins(
        "base_bank_targets",
        {"scope": "GLOBAL", "grid": "READERS", "cell_key": "+1.50", "base_bank": 4},
    )
    ins(
        "stock_units",
        {"unit_id": "SU-DHN-150", "store_id": DHN, "status": "AVAILABLE",
         "power": "+1.50", "quantity": 4},
    )


@pytest.fixture(scope="module")
def mongo_db():
    """Seeded once: every test here only reads."""
    from pymongo import MongoClient

    uri = (
        os.getenv("MONGODB_URL")
        or os.getenv("MONGODB_URI")
        or "mongodb://localhost:27017"
    )
    db_name = f"ims_test_noshop_{uuid.uuid4().hex[:8]}"
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

    def get(self, path: str, user: dict, **params):
        async def _as_user():
            return dict(user)

        self._app.dependency_overrides[get_current_user] = _as_user
        return self._client.get(
            path, params={k: v for k, v in params.items() if v is not None}
        )

    def ok(self, path: str, user: dict, **params):
        resp = self.get(path, user, **params)
        assert resp.status_code == 200, (path, resp.status_code, resp.text)
        return resp.json()

    def post(self, path: str, user: dict, body: dict):
        async def _as_user():
            return dict(user)

        self._app.dependency_overrides[get_current_user] = _as_user
        return self._client.post(path, json=body)


@pytest.fixture
def world(mongo_db, monkeypatch):
    from database.repositories.vendor_repository import (
        GRNRepository,
        PurchaseOrderRepository,
        VendorRepository,
    )

    proxy = _DBProxy(mongo_db)
    for mod in (vendors_pkg, finance_pkg, pinv_mod, vret_mod, item_events_mod):
        monkeypatch.setattr(mod, "_get_db", lambda: proxy)
    monkeypatch.setattr(
        vendors_pkg, "get_vendor_repository", lambda: VendorRepository(mongo_db["vendors"])
    )
    monkeypatch.setattr(
        vendors_pkg,
        "get_purchase_order_repository",
        lambda: PurchaseOrderRepository(mongo_db["purchase_orders"]),
    )
    monkeypatch.setattr(
        vendors_pkg, "get_grn_repository", lambda: GRNRepository(mongo_db["grns"])
    )
    monkeypatch.setattr(finance_pkg, "now_ist_naive", lambda: NOW)
    monkeypatch.setattr(finance_pkg, "ist_today", lambda: NOW.date())
    monkeypatch.setattr(finance_pkg, "now_ist", lambda: NOW)
    monkeypatch.setattr(ap_engine, "now_ist_naive", lambda: NOW)
    app = FastAPI()
    # main.py's order: the concrete /purchase-invoices paths before the vendors
    # router, whose catch-all GET /{vendor_id} would swallow them.
    app.include_router(pinv_mod.router, prefix="/vendors/purchase-invoices")
    app.include_router(vendors_pkg.router, prefix="/vendors")
    app.include_router(vret_mod.router, prefix="/vendor-returns")
    app.include_router(finance_pkg.router, prefix="/finance")
    app.include_router(item_events_mod.router, prefix="/items")
    return _World(app)


def _report_vendors(body) -> set:
    return {r.get("vendor_id") for r in body["vendors"]}


def test_the_storeless_accountant_gets_an_empty_purchases_report(world):
    admin = world.ok("/vendors/purchases-this-month", ADMIN, month="2026-09")
    assert _report_vendors(admin) == {V_DHN, V_PUN}  # the world is not empty
    body = world.ok("/vendors/purchases-this-month", ACCT_NONE, month="2026-09")
    assert body["vendors"] == [], body["vendors"]
    assert all(v == 0 for v in body["totals"].values()), body["totals"]
    # Never the 'all stores' label (None), and no money placed in no shop.
    assert body["store_id"] is not None
    assert body["unassigned_owed"] is None


def test_the_storeless_accountant_gets_an_empty_purchase_invoice_list(world):
    admin = world.ok("/vendors/purchase-invoices", ADMIN)
    assert {r["bill_id"] for r in admin["purchase_invoices"]} == {"B-DHN", "B-PUN"}
    body = world.ok("/vendors/purchase-invoices", ACCT_NONE)
    assert body == {"purchase_invoices": [], "total": 0}


def test_the_storeless_accountant_reads_no_shops_supplier_money(world):
    """/finance/vendor-payments lists the supplier MASTER (org-wide, the same
    rows any shop's accountant gets) with that login's share of the money --
    for a login with no shop, nothing: no bill, no order, nothing owed."""
    admin = {r["vendor_id"]: r for r in world.ok("/finance/vendor-payments", ADMIN)}
    assert (admin[V_DHN]["balance"], admin[V_PUN]["balance"]) == (1000.0, 500.0)
    rows = world.ok("/finance/vendor-payments", ACCT_NONE)
    money = {
        r["vendor_id"]: (r["balance"], r["total_billed"], r["total_paid"], r["po_total"])
        for r in rows
    }
    assert all(m == (0.0, 0.0, 0.0, 0.0) for m in money.values()), money


def test_the_storeless_accountant_gets_an_empty_ap_aging(world):
    admin = world.ok("/vendors/ap-aging", ADMIN)
    assert {v["vendor_id"] for v in admin["vendors"]} == {V_DHN, V_PUN}
    body = world.ok("/vendors/ap-aging", ACCT_NONE)
    assert body["vendors"] == [], body["vendors"]
    totals = dict(body["totals"])
    assert not any(totals.pop("buckets").values()), body["totals"]
    assert not any(totals.values()), body["totals"]


def test_the_storeless_accountant_gets_no_shops_sales_or_till_closes(world):
    """The sales side: cash reconciliation (each shop's till closes) and the
    owner dashboard's month revenue are every shop's for an admin, nothing for
    a login with no shop."""
    period = {"from": "2026-09-01", "to": "2026-09-30"}
    admin = world.ok("/finance/cash-reconciliation-summary", ADMIN, **period)
    assert {r["store_id"] for r in admin["rows"]} == {DHN, PUN}
    body = world.ok("/finance/cash-reconciliation-summary", ACCT_NONE, **period)
    assert body["rows"] == [], body["rows"]
    assert body["store_id"] is not None

    dash_admin = world.ok("/finance/owner-dashboard", ADMIN)
    assert dash_admin["this_month"]["revenue"] == 3200.0
    dash = world.ok("/finance/owner-dashboard", ACCT_NONE)
    assert dash["this_month"]["revenue"] == 0
    assert dash["payables"]["total"] == 0
    assert dash["store_id"] is not None


@pytest.mark.parametrize(
    "path,params",
    [
        ("/vendors/purchase-invoices", {}),
        ("/vendors/ap-aging", {}),
        ("/vendors/purchases-this-month", {"month": "2026-09"}),
        ("/finance/vendor-payments", {}),
        ("/finance/cash-reconciliation-summary", {}),
    ],
)
def test_the_storeless_accountant_naming_a_shop_is_refused_with_the_reason(world, path, params):
    resp = world.get(path, ACCT_NONE, store_id=DHN, **params)
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == NO_SHOP_DETAIL


def test_a_storeless_store_manager_gets_empty_purchase_lists(world):
    admin_pos = world.ok("/vendors/purchase-orders", ADMIN)
    assert {p["po_id"] for p in admin_pos["purchase_orders"]} == {"PO-DHN", "PO-PUN"}
    assert world.ok("/vendors/purchase-orders", MGR_NONE)["purchase_orders"] == []
    admin_ret = world.ok("/vendor-returns", ADMIN)
    assert {r["return_id"] for r in admin_ret["returns"]} == {"VR-DHN", "VR-PUN"}
    body = world.ok("/vendor-returns", MGR_NONE)
    assert body["returns"] == [] and body["total"] == 0


def test_an_area_manager_with_no_active_shop_keeps_its_own_shops_only(world):
    """Its token holds Pune and Dhanbad but no active shop: dropping store_id
    reads its first own shop (Pune), naming Dhanbad reads Dhanbad -- never
    every shop at once, never nothing, and another region is a 403."""
    rets = world.ok("/vendor-returns", AREA_TWO)
    assert {r["return_id"] for r in rets["returns"]} == {"VR-PUN"}
    rets = world.ok("/vendor-returns", AREA_TWO, store_id=DHN)
    assert {r["return_id"] for r in rets["returns"]} == {"VR-DHN"}
    pos = world.ok("/vendors/purchase-orders", AREA_TWO)
    assert {p["po_id"] for p in pos["purchase_orders"]} == {"PO-PUN"}
    recon = world.ok(
        "/finance/cash-reconciliation-summary", AREA_TWO, **{"from": "2026-09-01", "to": "2026-09-30"}
    )
    assert {r["store_id"] for r in recon["rows"]} == {PUN}
    assert world.get("/vendor-returns", AREA_TWO, store_id=BOK).status_code == 403


# ============================================================================
# The doors that keep their own copy of the rule (second pass)
# ============================================================================


@pytest.mark.parametrize("role", NON_ADMIN_ROLES)
def test_the_finance_scope_gives_a_storeless_login_no_shop(role):
    """finance/_shared._scope_store -- revenue, P&L, receivables, journal
    entries -- used to give such a login None (every shop); an AREA_MANAGER
    with no shop too (it is an HQ role there, but it holds nothing)."""
    assert finance_scope(None, _user(role)) == NO_SHOP_SCOPE
    with pytest.raises(HTTPException) as ei:
        finance_scope(DHN, _user(role))
    assert (ei.value.status_code, ei.value.detail) == (403, NO_SHOP_DETAIL)


def test_the_finance_scope_keeps_each_login_holding_a_shop_to_its_own_reach():
    assert finance_scope(None, ADMIN) is None
    assert finance_scope(None, SUPERADMIN) is None
    assert finance_scope(PUN, ADMIN) == PUN
    # an area manager holding shops keeps the org view this helper gives it
    assert finance_scope(None, AREA_TWO) is None
    assert finance_scope(None, _user("AREA_MANAGER", DHN, (DHN,))) is None
    assert finance_scope(None, ACCT_PUN) == PUN
    # a shop login whose token lost its active shop reads its OWN shop (it
    # used to read None: every shop)
    assert finance_scope(None, _user("STORE_MANAGER", None, (DHN,))) == DHN


def test_the_storeless_accountant_reads_no_shops_revenue_pnl_or_receivables(world):
    month = {"from_date": "2026-10-01", "to_date": "2026-10-31"}
    assert world.ok("/finance/pnl", ADMIN, **month)["revenue"] == 3200.0
    assert world.ok("/finance/pnl", ACCT_PUN, **month)["revenue"] == 1200.0
    pnl = world.ok("/finance/pnl", ACCT_NONE, **month)
    assert (pnl["revenue"], pnl["tax_collected"]) == (0, 0), pnl

    assert world.ok("/finance/revenue", ADMIN, period="month")["total_revenue"] == 3200.0
    rev = world.ok("/finance/revenue", ACCT_NONE, period="month")
    assert (rev["total_revenue"], rev["total_orders"]) == (0, 0), rev

    due = {i["order_id"] for i in world.ok("/finance/outstanding", ADMIN)["items"]}
    assert due == {"O-DHN-DUE", "O-PUN-DUE"}
    due = {i["order_id"] for i in world.ok("/finance/outstanding", ACCT_PUN)["items"]}
    assert due == {"O-PUN-DUE"}
    body = world.ok("/finance/outstanding", ACCT_NONE)
    assert (body["items"], body["total_outstanding"]) == ([], 0), body


def test_the_storeless_accountant_reads_no_shops_cash_flow(world):
    # SUPERADMIN has no topbar shop: the org view, both shops' paid sales.
    assert world.ok("/finance/cash-flow", SUPERADMIN)["inflows"] == 3200.0
    body = world.ok("/finance/cash-flow", ACCT_NONE)
    assert (body["inflows"], body["outflows"], body["net_cash_flow"]) == (0, 0, 0), body
    # a shop login whose token lost its active shop reads its own shop only
    own = world.ok("/finance/cash-flow", _user("STORE_MANAGER", None, (PUN,)))
    assert own["inflows"] == 1200.0
    assert world.get("/finance/cash-flow", ACCT_NONE, store_id=DHN).status_code == 403


def test_the_storeless_accountant_lists_no_journal_entries(world):
    ids = {j["je_id"] for j in world.ok("/finance/journal-entries", ADMIN)["journal_entries"]}
    assert {"JE-DHN", "JE-PUN", "JE-HQ"} <= ids
    ids = {j["je_id"] for j in world.ok("/finance/journal-entries", ACCT_PUN)["journal_entries"]}
    assert "JE-PUN" in ids and not ids & {"JE-DHN", "JE-HQ"}
    body = world.ok("/finance/journal-entries", ACCT_NONE)
    assert (body["journal_entries"], body["total"]) == ([], 0), body


def test_a_storeless_accountant_writes_no_journal_entry(world, mongo_db, monkeypatch):
    """The voucher would have carried the reserved no-shop id (or, before R4,
    been an HQ voucher this login cannot even read back). Refused with the
    reason, nothing written. The same body from a Pune accountant is a Pune
    voucher -- the body is valid, the refusal is about the shop."""
    monkeypatch.setattr(je_service, "is_je_enabled", lambda: True)
    body = {
        "description": "Bank charges",
        "lines": [
            {"account_code": "5004", "debit": 100},
            {"account_code": "2001", "credit": 100},
        ],
    }
    before = mongo_db["journal_entries"].count_documents({})
    resp = world.post("/finance/journal-entries", ACCT_NONE, body)
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == NO_SHOP_DETAIL
    assert mongo_db["journal_entries"].count_documents({}) == before

    made = world.post("/finance/journal-entries", ACCT_PUN, body)
    try:
        assert made.status_code == 200, made.text
        assert made.json()["je"]["store_id"] == PUN
    finally:
        mongo_db["journal_entries"].delete_many(
            {"je_id": {"$nin": ["JE-DHN", "JE-PUN", "JE-HQ"]}}
        )


def test_replenishment_refuses_a_storeless_login(world):
    """It used to answer the GLOBAL targets against no shelf at all (in_hand
    0, required = base_bank): 'buy every cell in full'. Before R4 it was a
    422; now a 403 that says why."""
    mgr_dhn = _user("STORE_MANAGER", DHN, (DHN,))
    cells = world.ok("/items/replenishment", mgr_dhn, grid="READERS")["items"]
    assert [(c["cell_key"], c["in_hand"], c["required"]) for c in cells] == [("+1.50", 4, 0)]
    resp = world.get("/items/replenishment", MGR_NONE, grid="READERS")
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == NO_SHOP_DETAIL
