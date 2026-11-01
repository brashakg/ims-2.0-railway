"""A login with no shop sees no shop OUTSIDE Purchase and Finance too
(owner ruling 2026-10-12, R4).

"A non-admin login with no shop assigned sees NO shop (empty lists + 'No shop
is assigned to this login' message), never every shop; an explicit store_id
from it is refused."

Before, R4 held only inside Purchase, Finance and GST. A STORE_MANAGER,
ACCOUNTANT, SALES_STAFF or AREA_MANAGER with store_ids [] read every shop's
customer returns, workshop jobs, pending expenses / advances, customers and
stock; /returns?store_id=BV-DHN-01 from a no-shop AREA_MANAGER (HQ there) was
honoured. The cause: ~60 routes kept their own copy of the shop rule,
``validate_store_access(store_id, user) or user.get("active_store_id")``,
which is None -- every shop -- for such a login; the returns, customers and
transfers lists kept an HQ rule of their own.

Now: ONE validate-or-active rule, api.dependencies.store_in_view (no-shop ->
NO_SHOP_SCOPE; writing=True refuses it), and api.dependencies.no_shop_scope_for
for the lists whose HQ rule lets an AREA_MANAGER past his shops. /orders is a
POS router (not edited here: raised with the owner).
"""
import os
import re
import sys
from datetime import datetime

os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import mongomock  # noqa: E402
import pytest  # noqa: E402
from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api.dependencies import (  # noqa: E402
    NO_SHOP_DETAIL,
    NO_SHOP_SCOPE,
    no_shop_scope_for,
    store_in_view,
)
from api.routers.auth import get_current_user  # noqa: E402

DHN, PUN = "BV-DHN-01", "WO-PUN-01"
NON_ADMIN = ("STORE_MANAGER", "AREA_MANAGER", "ACCOUNTANT", "SALES_STAFF",
             "CASHIER", "OPTOMETRIST", "WORKSHOP_STAFF", "CATALOG_MANAGER")


def _user(role, shops=(), active="first"):
    shops = list(shops)
    act = (shops[0] if shops else None) if active == "first" else active
    return {"user_id": f"u-{role.lower()}-{act}", "username": role.lower(),
            "roles": [role], "store_ids": shops, "active_store_id": act}


# ============================================================================
# The rule itself
# ============================================================================


@pytest.mark.parametrize("role", NON_ADMIN)
def test_store_in_view_gives_a_login_with_no_shop_no_shop(role):
    user = _user(role)
    assert store_in_view(None, user) == NO_SHOP_SCOPE
    with pytest.raises(HTTPException) as named:
        store_in_view(DHN, user)
    assert (named.value.status_code, named.value.detail) == (403, NO_SHOP_DETAIL)
    with pytest.raises(HTTPException) as write:
        store_in_view(None, user, writing=True)
    assert (write.value.status_code, write.value.detail) == (403, NO_SHOP_DETAIL)


def test_store_in_view_keeps_every_login_that_holds_a_shop():
    # The active shop for anyone else holding one.
    assert store_in_view(None, _user("STORE_MANAGER", [DHN])) == DHN
    # An admin with no shop named reads EVERY shop (None) -- his top-bar shop
    # no longer narrows it (owner 2026-10-18) -- except on a write or a screen
    # of one shop's own, where it is still his top-bar shop.
    assert store_in_view(None, _user("ADMIN", [DHN])) is None
    assert store_in_view(None, _user("SUPERADMIN", [DHN])) is None
    assert store_in_view(None, _user("ADMIN", [DHN]), one_shop=True) == DHN
    assert store_in_view(None, _user("ADMIN", [DHN]), writing=True) == DHN
    assert store_in_view(None, _user("ADMIN")) is None
    assert store_in_view(None, _user("SUPERADMIN")) is None
    assert store_in_view(PUN, _user("ADMIN")) == PUN
    assert store_in_view(PUN, _user("ADMIN", [DHN])) == PUN
    # one_shop changes nothing for anyone else.
    assert store_in_view(None, _user("STORE_MANAGER", [DHN]), one_shop=True) == DHN
    assert store_in_view(None, _user("ACCOUNTANT"), one_shop=True) == NO_SHOP_SCOPE
    # A multi-store login without an active shop: its first own shop, never
    # every shop (the copies gave it None).
    assert store_in_view(None, _user("AREA_MANAGER", [PUN, DHN], active=None)) == PUN
    with pytest.raises(HTTPException):
        store_in_view(DHN, _user("STORE_MANAGER", [PUN]))


def test_no_shop_scope_for_only_answers_a_login_with_no_shop():
    assert no_shop_scope_for(None, _user("AREA_MANAGER")) == NO_SHOP_SCOPE
    with pytest.raises(HTTPException) as named:
        no_shop_scope_for(DHN, _user("AREA_MANAGER"))
    assert named.value.detail == NO_SHOP_DETAIL
    for user in (_user("AREA_MANAGER", [PUN]), _user("ADMIN"), _user("SUPERADMIN"),
                 _user("STORE_MANAGER", [DHN])):
        assert no_shop_scope_for(DHN, user) is None


def test_no_router_keeps_a_copy_of_the_validate_or_active_rule():
    api_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "api")
    copy = re.compile(
        r"validate_store_access\(\s*store_id,\s*current_user\s*\)\s*or\s*"
        r"current_user\.get\(\s*[\"']active_store_id[\"']\s*\)"
    )
    found = []
    for root, _dirs, files in os.walk(api_dir):
        for name in files:
            if name.endswith(".py"):
                path = os.path.join(root, name)
                with open(path, encoding="utf-8") as fh:
                    if copy.search(fh.read()):
                        found.append(os.path.relpath(path, api_dir))
    assert found == [], found


# ============================================================================
# The lists, through FastAPI
# ============================================================================


class _Repo:
    """A repository over one mongomock collection: the find_many / count the
    routes call."""

    def __init__(self, coll):
        self.coll = coll

    def find_many(self, flt=None, skip=0, limit=0, sort=None, **_kw):
        cur = self.coll.find(flt or {}, {"_id": 0})
        return list(cur.skip(skip).limit(limit) if limit else cur.skip(skip))

    def count(self, flt=None):
        return self.coll.count_documents(flt or {})


@pytest.fixture
def world(monkeypatch):
    from api.routers import customers, expenses, inventory_router, returns, transfers
    from api.routers.inventory import stock as stock_mod
    from api.routers.workshop import jobs as jobs_mod
    from database.repositories.customer_repository import CustomerRepository

    db = mongomock.MongoClient()["no_shop_outside"]
    now = datetime.utcnow()
    for sid in (DHN, PUN):
        db["returns"].insert_one({"return_id": f"R-{sid}", "store_id": sid, "created_at": now})
        db["workshop_jobs"].insert_one({"job_id": f"J-{sid}", "store_id": sid,
                                        "status": "PENDING", "created_at": now})
        db["expenses"].insert_one({"expense_id": f"E-{sid}", "store_id": sid,
                                   "status": "PENDING", "amount": 123})
        db["advances"].insert_one({"advance_id": f"A-{sid}", "store_id": sid,
                                   "status": "PENDING", "amount": 50})
        db["customers"].insert_one({"customer_id": f"C-{sid}", "name": f"Cust {sid}",
                                    "home_store_id": sid, "is_active": True})
        db["stock_transfers"].insert_one({"transfer_id": f"T-{sid}", "from_location_id": sid,
                                          "to_location_id": "WH-1", "created_at": now.isoformat()})
        db["stock_units"].insert_one({"stock_id": f"S-{sid}", "product_id": "P1",
                                      "store_id": sid, "status": "AVAILABLE"})
    db["products"].insert_one({"product_id": "P1", "sku": "SKU1", "name": "Frame",
                               "is_active": True, "category": "FRAME"})

    monkeypatch.setattr(returns, "_get_db", lambda: db)
    monkeypatch.setattr(jobs_mod, "get_workshop_repository", lambda: _Repo(db["workshop_jobs"]))
    monkeypatch.setattr(jobs_mod, "_stamp_job_actor_names", lambda jobs: None)
    monkeypatch.setattr(jobs_mod, "job_to_frontend", lambda j, _user=None: j)
    monkeypatch.setattr(expenses, "get_expense_repository", lambda: _Repo(db["expenses"]))
    monkeypatch.setattr(expenses, "get_advance_repository", lambda: _Repo(db["advances"]))
    monkeypatch.setattr(customers, "get_customer_repository",
                        lambda: CustomerRepository(db["customers"]))
    monkeypatch.setattr(transfers, "_all_transfers",
                        lambda: list(db["stock_transfers"].find({}, {"_id": 0})))
    monkeypatch.setattr(stock_mod, "get_stock_repository", lambda: None)  # ledger: see below

    app = FastAPI()
    app.include_router(returns.router, prefix="/returns")
    app.include_router(jobs_mod.router, prefix="/workshop")
    app.include_router(expenses.router, prefix="/expenses")
    app.include_router(customers.router, prefix="/customers")
    app.include_router(transfers.router, prefix="/transfers")
    app.include_router(inventory_router, prefix="/inventory")
    who = {}

    async def _current():
        return dict(who["user"])

    app.dependency_overrides[get_current_user] = _current
    client = TestClient(app)

    def get(user, path, **params):
        who["user"] = user
        return client.get(path, params=params)

    return get


def _ids(body):
    text = str(body)
    return sorted(s for s in (DHN, PUN) if s in text)


LISTS = (
    ("/returns", ("STORE_MANAGER", "ACCOUNTANT", "SALES_STAFF", "AREA_MANAGER")),
    ("/workshop/jobs", ("STORE_MANAGER", "ACCOUNTANT", "SALES_STAFF", "AREA_MANAGER")),
    ("/expenses/pending-approval", ("STORE_MANAGER", "ACCOUNTANT", "AREA_MANAGER")),
    ("/customers", ("STORE_MANAGER", "ACCOUNTANT", "SALES_STAFF", "AREA_MANAGER")),
    ("/transfers", ("AREA_MANAGER",)),
)


@pytest.mark.parametrize("path,roles", LISTS, ids=[p for p, _ in LISTS])
def test_a_login_with_no_shop_reads_no_shops_list(world, path, roles):
    # The world is not empty: an admin reads both shops on the same request.
    assert _ids(world(_user("ADMIN"), path).json()) == [DHN, PUN]
    for role in roles:
        r = world(_user(role), path)
        assert r.status_code == 200, (role, r.text)
        assert _ids(r.json()) == [], (role, path, r.text[:300])


@pytest.mark.parametrize("path,roles", LISTS, ids=[p for p, _ in LISTS])
def test_a_shop_named_by_a_login_with_no_shop_is_refused(world, path, roles):
    for role in roles:
        r = world(_user(role), path, store_id=DHN)
        assert r.status_code == 403, (role, path, r.status_code)
        assert r.json()["detail"] == NO_SHOP_DETAIL


def test_a_login_that_holds_a_shop_keeps_its_list(world):
    sm = _user("STORE_MANAGER", [DHN])
    for path in ("/returns", "/workshop/jobs", "/expenses/pending-approval", "/customers"):
        assert _ids(world(sm, path).json()) == [DHN], path
    # An AREA_MANAGER sees assigned shops in returns and stock transfers.
    # Check the status explicitly: searching the error text for a shop ID
    # would mistake "No access to store ..." for a returned shop record.
    am = _user("AREA_MANAGER", [PUN])
    refused = world(am, "/returns", store_id=DHN)
    assert refused.status_code == 403
    assert refused.json() == {"detail": f"No access to store {DHN}"}
    allowed = world(am, "/returns")
    assert allowed.status_code == 200
    assert _ids(allowed.json()) == [PUN]
    assert _ids(world(am, "/transfers").json()) == [PUN]


def test_the_reorder_list_is_empty_for_a_login_with_no_shop(world):
    # The Buy Desk's place (stock redesign S5): the one Reorder list.
    r = world(_user("STORE_MANAGER"), "/inventory/reorder-list")
    assert r.status_code == 200, r.text
    assert r.json() == {"store_id": None, "rows": [], "skipped": [], "auto_reorder_off": 0}


# ============================================================================
# The Hub widgets (panel probe 2026-10-12): /finance/summary-month and the other
# widget reads kept their own copy of the shop rule (dashboard_widgets._store).
# A STORE_MANAGER or ACCOUNTANT with no shop read every shop's month (Rs 2,240,
# 2 orders), and ?store_id=WO-PUN-01 was honoured (Pune's Rs 1,120) instead of
# the 403 every other read gives. They now ask store_in_view.
# ============================================================================


@pytest.fixture
def widgets(monkeypatch):
    from api.routers import dashboard_widgets as dw

    db = mongomock.MongoClient()["no_shop_widgets"]
    now = datetime.utcnow()
    for sid in (DHN, PUN):
        db["orders"].insert_one({"order_id": f"O-{sid}", "store_id": sid, "status": "DELIVERED",
                                 "grand_total": 1120.0, "created_at": now})
    monkeypatch.setattr(dw, "_coll", lambda name: db[name])
    app = FastAPI()
    app.include_router(dw.router)
    who = {}

    async def _current():
        return dict(who["user"])

    app.dependency_overrides[get_current_user] = _current
    client = TestClient(app)

    def get(user, **params):
        who["user"] = user
        return client.get("/finance/summary-month", params=params)

    return get


def test_the_month_widget_gives_a_login_with_no_shop_no_shop(widgets):
    # The world is not empty: an admin with no shop reads both shops.
    assert widgets(_user("ADMIN")).json() == {"revenue_month": 2240.0, "orders_month": 2}
    for role in ("STORE_MANAGER", "ACCOUNTANT", "AREA_MANAGER"):
        r = widgets(_user(role))
        assert r.status_code == 200, (role, r.text)
        assert r.json() == {"revenue_month": 0.0, "orders_month": 0}, role
        named = widgets(_user(role), store_id=PUN)
        assert named.status_code == 403, (role, named.text)
        assert named.json()["detail"] == NO_SHOP_DETAIL


@pytest.mark.parametrize("role", ("STORE_MANAGER", "AREA_MANAGER", "ACCOUNTANT"))
def test_the_month_widget_keeps_a_shop_to_its_own(widgets, role):
    """A login that holds Dhanbad reads Dhanbad's month; naming Pune is the 403
    every other read gives (the old copy ignored it for a store manager and
    honoured it for an area manager)."""
    user = _user(role, [DHN])
    assert widgets(user).json() == {"revenue_month": 1120.0, "orders_month": 1}
    r = widgets(user, store_id=PUN)
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == f"No access to store {PUN}"
