"""The org-wide finance views refuse a login with no shop (owner ruling
2026-10-12, R4: a non-admin login with no shop sees NO shop, never every
shop; an explicit store_id from it is refused).

Before: an ACCOUNTANT with store_ids [] read every shop's Tally sales JV (and
Dhanbad's alone by naming ?store_id=BV-DHN-01), the per-entity GST
reconciliation (both shops' tax) and the GST cross-check -- while the same
login got Rs 0 from the GST summary, ITC register and vendor payments. These
views (with the B2B invoice book, the cross-check sign-off and bank
statements) apply no shop rule, so they now share ONE gate,
finance._require_org_finance: the accounts roles AND a login that holds a
shop. ADMIN / SUPERADMIN pass with or without one; a shop-holding accountant
keeps the org-wide view (an owner question, like R8).
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
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api.dependencies import NO_SHOP_DETAIL  # noqa: E402
from api.routers import finance  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402
from api.routers.finance import bank_statement, gst_crosscheck, tally  # noqa: E402

DHN, PUN = "BV-DHN-01", "WO-PUN-01"
TODAY = datetime.utcnow().strftime("%Y-%m-%d")


def _user(role, shops=()):
    shops = list(shops)
    return {"user_id": f"u-{role.lower()}-{len(shops)}", "username": role.lower(),
            "roles": [role], "store_ids": shops,
            "active_store_id": shops[0] if shops else None}


NO_SHOP_ACCOUNTANT = _user("ACCOUNTANT")

# Every org-wide view: (method, path, json body or None)
ORG_VIEWS = (
    ("GET", f"/finance/tally/sales-jv?from_date={TODAY}&to_date={TODAY}", None),
    ("GET", f"/finance/tally/sales-jv?from_date={TODAY}&to_date={TODAY}&store_id={DHN}", None),
    ("GET", "/finance/gst/reconciliation", None),
    ("GET", "/finance/gst/cross-check", None),
    ("POST", "/finance/gst/cross-check-signoff",
     {"month": datetime.utcnow().month, "year": datetime.utcnow().year}),
    ("GET", "/finance/b2b-invoices", None),
    ("GET", f"/finance/b2b-invoices?store_id={DHN}", None),
    ("GET", "/finance/bank-statement", None),
    ("GET", f"/finance/bank-statement?store_id={DHN}", None),
)


@pytest.fixture
def world(monkeypatch):
    db = mongomock.MongoClient()["org_finance"]
    db["stores"].insert_many([
        {"store_id": DHN, "store_name": "Dhanbad", "state": "Jharkhand", "entity_id": "E1"},
        {"store_id": PUN, "store_name": "Pune", "state": "Maharashtra", "entity_id": "E1"},
    ])
    now = datetime.utcnow()
    for sid, amt in ((DHN, 1000), (PUN, 2000)):
        db["orders"].insert_one({
            "order_id": f"O-{sid}", "order_number": f"INV-{sid}", "store_id": sid,
            "status": "DELIVERED", "created_at": now, "grand_total": amt * 1.05,
            "tax_amount": amt * 0.05,
        })
    for mod in (finance, tally, gst_crosscheck, bank_statement):
        monkeypatch.setattr(mod, "_get_db", lambda: db, raising=False)
    from api.routers.finance import _shared
    monkeypatch.setattr(_shared, "_get_db", lambda: db)
    app = FastAPI()
    app.include_router(finance.router, prefix="/finance")
    who = {"user": NO_SHOP_ACCOUNTANT}

    async def _current():
        return dict(who["user"])

    app.dependency_overrides[get_current_user] = _current
    client = TestClient(app, raise_server_exceptions=False)

    def call(user, method, path, body=None):
        who["user"] = user
        return client.request(method, path, json=body)

    return call


@pytest.mark.parametrize("method,path,body", ORG_VIEWS)
def test_a_login_with_no_shop_is_refused_every_org_wide_view(world, method, path, body):
    r = world(NO_SHOP_ACCOUNTANT, method, path, body)
    assert r.status_code == 403, (path, r.status_code, r.text[:200])
    assert r.json()["detail"] == NO_SHOP_DETAIL
    # Nothing of either shop's books in the answer.
    assert "INV-" not in r.text and DHN not in r.text and PUN not in r.text


def test_the_sales_jv_no_longer_hands_a_login_with_no_shop_every_shops_vouchers(world):
    path = f"/finance/tally/sales-jv?from_date={TODAY}&to_date={TODAY}"
    assert world(NO_SHOP_ACCOUNTANT, "GET", path).status_code == 403
    # The org view is the accounts roles' with a shop, and an admin's.
    for user in (_user("ACCOUNTANT", [PUN]), _user("ADMIN"), _user("SUPERADMIN")):
        r = world(user, "GET", path)
        assert r.status_code == 200, (user["roles"], r.text[:200])
        assert "O-BV-DHN-01" in r.text and "O-WO-PUN-01" in r.text


def test_the_reconciliation_and_cross_check_stay_open_to_a_shop_accountant_and_an_admin(world):
    for user in (_user("ACCOUNTANT", [PUN]), _user("ADMIN")):
        for path in ("/finance/gst/reconciliation", "/finance/gst/cross-check"):
            r = world(user, "GET", path)
            assert r.status_code == 200, (user["roles"], path, r.text[:200])


@pytest.mark.parametrize("role", ("STORE_MANAGER", "AREA_MANAGER", "SALES_STAFF"))
def test_the_role_gate_still_comes_first(world, role):
    r = world(_user(role, [DHN]), "GET", "/finance/gst/reconciliation")
    assert r.status_code == 403
    assert r.json()["detail"] != NO_SHOP_DETAIL


# Every finance handler on the accounts gate either applies a shop rule or
# is an org-wide view on the one gate that refuses a login with no shop.
_SHOP_RULE = re.compile(
    r"resolve_store_scope|_scope_store|_survival_scope|validate_store_access|_money_shop"
)


def test_every_accounts_finance_read_holds_r4():
    import inspect

    loose = []
    for route in finance.router.routes:
        src = inspect.getsource(route.endpoint)
        if "_require_finance_admin(current_user)" in src and not _SHOP_RULE.search(src):
            loose.append(route.path)
    assert loose == [], loose
