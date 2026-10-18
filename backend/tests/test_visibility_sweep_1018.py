"""Visibility sweep (owner 2026-10-18), probed over HTTP on the real app.

Rules: what we pay is ADMIN / SUPERADMIN / ACCOUNTANT only; a login sees only
its own shops (a login with no shop sees none); another shop's record answers
like a missing one.

  FU-16  GET /finance/pnl/by-category follows the shop scope (entity_id is an
         admin's narrowing only).
  FU-23  a CATALOG_MANAGER cannot SET a cost price on any door.
  FU-02  the goods-receipt bill read back is the accounts roles' only.
  FU-17  journal vouchers are the accounts roles' only.
  F11    another shop's unit is a 404 at the barcode lookups.
  FU-06  the dead /catalog/products/export handler is gone.
  FU-04/05/19  when a reader cannot see cost, rows are not ordered by cost.
  Roles  a mis-cased role in a token or a user record is read canonically.

One shop (STORE) from test_counter_roles_no_cost_anywhere, plus a second
shop (OTHER) seeded here.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime
from types import SimpleNamespace

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from test_counter_roles_no_cost_anywhere import (  # noqa: E402,F401
    COST,
    OTHER,
    STORE,
    _headers,
    _user,
    shop,
)

MANAGERS = ("AREA_MANAGER", "STORE_MANAGER")
ACCOUNTS = ("SUPERADMIN", "ADMIN", "ACCOUNTANT")
OTHER_REVENUE = 7777.0
OWN_REVENUE = 111.0
OTHER_BARCODE = "2900000000031"
OWN_BARCODE = "2900000000017"  # the shop fixture's U1, in STORE


def _hdr(user):
    from api.routers.auth import create_access_token

    return {"Authorization": f"Bearer {create_access_token(user)}"}


NO_SHOP = {"user_id": "u-noshop", "username": "noshop", "roles": ["STORE_MANAGER"],
           "store_ids": [], "active_store_id": None}


@pytest.fixture
def two_shops(shop):
    """The shop plus a second shop with its own sale and its own unit."""
    shop.stores.insert_one({"store_id": OTHER, "store_name": "Other shop",
                            "entity_id": "entity-other"})
    for oid, store, total in (("O8", STORE, OWN_REVENUE), ("O9", OTHER, OTHER_REVENUE)):
        shop.orders.insert_one(
            {"order_id": oid, "order_number": f"BV-{oid}", "store_id": store,
             "status": "CONFIRMED", "grand_total": total, "created_at": datetime.now(),
             "items": [{"product_id": "P1", "item_type": "FRAME", "quantity": 1,
                        "unit_price": total, "total": total}]}
        )
    shop.stock_units.insert_one(
        {"stock_id": "U9", "product_id": "P1", "store_id": OTHER,
         "barcode": OTHER_BARCODE, "status": "AVAILABLE", "quantity": 1,
         "cost_price": COST}
    )
    return shop


# ---------------------------------------------------------------------------
# FU-16: the category P&L follows the shop scope
# ---------------------------------------------------------------------------

_BY_CAT = "/api/v1/finance/pnl/by-category"


def _revenue(client, headers, query=""):
    r = client.get(_BY_CAT + query, headers=headers)
    assert r.status_code == 200, r.text[:300]
    return r.json()["total_revenue"]


@pytest.mark.parametrize("role", MANAGERS)
def test_category_pnl_reads_only_the_managers_shop(client, two_shops, role):
    admin_all = _revenue(client, _headers("ADMIN"))
    own = _revenue(client, _headers(role))
    assert own == pytest.approx(OWN_REVENUE), own
    assert admin_all == pytest.approx(OWN_REVENUE + OTHER_REVENUE), admin_all
    # entity_id does not widen a manager's reach either.
    assert _revenue(client, _headers(role), "?entity_id=entity-other") == own
    # Naming another shop is a 403.
    r = client.get(f"{_BY_CAT}?store_id={OTHER}", headers=_headers(role))
    assert r.status_code == 403, r.text[:300]


def test_category_pnl_no_shop_login_reads_nothing(client, two_shops):
    r = client.get(_BY_CAT, headers=_hdr(NO_SHOP))
    assert r.status_code == 200, r.text[:300]
    assert r.json()["categories"] == [] and r.json()["total_revenue"] == 0


def test_admin_still_narrows_the_category_pnl_by_entity(client, two_shops):
    assert _revenue(client, _headers("ADMIN"), "?entity_id=entity-other") == pytest.approx(
        OTHER_REVENUE
    )


# ---------------------------------------------------------------------------
# FU-23: a catalogue manager sets no cost price, on any door
# ---------------------------------------------------------------------------

_LENS = {"brand": "Zeiss", "series": "Single", "index": 1.6, "material": "MR8",
         "lens_type": "SV", "coating": "BlueCut", "mrp": 5000.0}
_CAT_CREATE = {"category": "FR", "attributes": {"brand_name": "Ray-Ban", "model_no": "RB1"},
               "pricing": {"mrp": 5000.0, "offer_price": 4000.0}}
_PROD_CREATE = {"category": "FRAME", "brand": "Ray-Ban", "model": "RB2",
                "mrp": 5000.0, "offer_price": 4000.0}

# (method, path, body without cost, the same body with a cost price)
_COST_DOORS = [
    ("POST", "/api/v1/products", _PROD_CREATE, {**_PROD_CREATE, "cost_price": 1.0}),
    ("PUT", "/api/v1/products/P1", {"name": "Aviator II"}, {"cost_price": 1.0}),
    ("POST", "/api/v1/catalog/products", _CAT_CREATE,
     {**_CAT_CREATE, "pricing": {**_CAT_CREATE["pricing"], "cost_price": 1.0}}),
    ("PUT", "/api/v1/catalog/products/P1", {"name": "Aviator II"},
     {"pricing": {"cost_price": 1.0}}),
    ("POST", "/api/v1/lens-catalog", _LENS, {**_LENS, "cost_price": 1.0}),
    ("PATCH", "/api/v1/lens-catalog/LL1", {"notes": "re-checked"}, {"cost_price": 1.0}),
]


@pytest.mark.parametrize("method,path,plain,costed", _COST_DOORS)
def test_catalog_manager_cannot_set_cost_on_any_door(client, shop, method, path, plain, costed):
    from api.services.cost_mask import COST_WRITE_REFUSED

    r = client.request(method, path, json=costed, headers=_headers("CATALOG_MANAGER"))
    assert r.status_code == 403, (path, r.status_code, r.text[:300])
    assert r.json()["detail"] == COST_WRITE_REFUSED
    # The cost already on record is untouched.
    assert shop.products.find_one({"product_id": "P1"})["cost_price"] == COST
    assert shop.lens_catalog.find_one({"lens_line_id": "LL1"})["cost_price"] == COST
    # Without a cost the same door is not refused for the cost.
    r = client.request(method, path, json=plain, headers=_headers("CATALOG_MANAGER"))
    assert r.status_code != 403, (path, r.status_code, r.text[:300])


@pytest.mark.parametrize("role", ("ADMIN", "SUPERADMIN"))
def test_accounts_still_set_cost(client, shop, role):
    r = client.put("/api/v1/products/P1", json={"cost_price": 1234.0}, headers=_headers(role))
    assert r.status_code == 200, r.text[:300]
    assert shop.products.find_one({"product_id": "P1"})["cost_price"] == 1234.0


# ---------------------------------------------------------------------------
# FU-02: the receipt's bill is read back by the accounts roles only
# ---------------------------------------------------------------------------

_GRN_DOC = "/api/v1/vendors/grn/G9/document"


@pytest.fixture
def receipt(shop):
    shop.grns.insert_one({"grn_id": "G9", "grn_number": "GRN-9", "store_id": STORE,
                          "status": "ACCEPTED", "items": []})
    return shop


@pytest.mark.parametrize("role", MANAGERS)
def test_managers_cannot_read_the_receipt_bill(client, receipt, role):
    r = client.get(_GRN_DOC, headers=_headers(role))
    assert r.status_code == 403, r.text[:300]


def test_the_handler_itself_refuses_managers():
    """The route's own gate, not only the policy row (the middleware)."""
    from api.routers.vendors import grn as grn_mod
    from api.services.rbac_policy import policy_for

    assert sorted(policy_for("GET", _GRN_DOC)["allowed"]) == ["ACCOUNTANT", "ADMIN"]
    route = next(r for r in __import__("api.main", fromlist=["app"]).app.routes
                 if getattr(r, "path", "") == "/api/v1/vendors/grn/{grn_id}/document")
    assert route.endpoint is grn_mod.download_grn_doc
    gate = route.dependant.dependencies[0].call  # require_roles(...)._dep
    for role in MANAGERS:
        with pytest.raises(HTTPException) as err:
            asyncio.run(gate(current_user=_user(role)))
        assert err.value.status_code == 403
    asyncio.run(gate(current_user=_user("ACCOUNTANT")))


@pytest.mark.parametrize("role", ACCOUNTS)
def test_accounts_reach_the_receipt_bill(client, receipt, role):
    r = client.get(_GRN_DOC, headers=_headers(role))
    # Past the role gate: this receipt simply has no bill attached.
    assert r.status_code == 404 and "No document" in r.text, r.text[:300]


# ---------------------------------------------------------------------------
# FU-17: journal vouchers are the accounts roles' only
# ---------------------------------------------------------------------------


@pytest.fixture
def voucher(shop):
    shop.journal_entries.insert_one({"je_id": "JE1", "store_id": STORE, "status": "POSTED",
                                     "created_at": datetime.now(), "lines": []})
    return shop


_JE_READS = ("/api/v1/finance/journal-entries", "/api/v1/finance/journal-entries/JE1")


@pytest.mark.parametrize("role", MANAGERS)
@pytest.mark.parametrize("path", _JE_READS)
def test_managers_cannot_read_journal_entries(client, voucher, role, path):
    r = client.get(path, headers=_headers(role))
    assert r.status_code == 403, r.text[:300]


@pytest.mark.parametrize("role", MANAGERS)
def test_the_journal_handlers_refuse_managers_themselves(voucher, role):
    from api.routers.finance import journal_entries as je

    with pytest.raises(HTTPException) as err:
        asyncio.run(je.list_journal_entries(current_user=_user(role)))
    assert err.value.status_code == 403
    with pytest.raises(HTTPException) as err:
        asyncio.run(je.get_journal_entry("JE1", current_user=_user(role)))
    assert err.value.status_code == 403


@pytest.mark.parametrize("role", ACCOUNTS)
def test_accounts_read_journal_entries(client, voucher, role):
    r = client.get(_JE_READS[0], headers=_headers(role))
    assert r.status_code == 200 and r.json()["total"] == 1, r.text[:300]
    r = client.get(_JE_READS[1], headers=_headers(role))
    assert r.status_code == 200 and r.json()["je_id"] == "JE1", r.text[:300]


# ---------------------------------------------------------------------------
# F11: another shop's unit answers like a missing one
# ---------------------------------------------------------------------------

_SCAN_PATHS = ("/api/v1/inventory/barcode/{}", "/api/v1/inventory/stock/barcode/{}")


@pytest.mark.parametrize("role", ("CASHIER", "SALES_STAFF", "STORE_MANAGER"))
@pytest.mark.parametrize("path", _SCAN_PATHS)
def test_other_shops_unit_is_not_found(client, two_shops, role, path):
    missing = client.get(path.format("2999999999999"), headers=_headers(role))
    foreign = client.get(path.format(OTHER_BARCODE), headers=_headers(role))
    assert foreign.status_code == 404, foreign.text[:300]
    assert foreign.json() == missing.json() == {"detail": "Stock item not found"}
    own = client.get(path.format(OWN_BARCODE), headers=_headers(role))
    assert own.status_code == 200 and own.json()["barcode"] == OWN_BARCODE, own.text[:300]


@pytest.mark.parametrize("role", ("CASHIER", "STORE_MANAGER"))
def test_a_scan_scoped_to_another_shop_is_refused(client, two_shops, role):
    r = client.get(f"/api/v1/inventory/barcode/{OWN_BARCODE}?store_id={OTHER}",
                   headers=_headers(role))
    assert r.status_code == 403, r.text[:300]
    # The till's own shop is fine.
    r = client.get(f"/api/v1/inventory/barcode/{OWN_BARCODE}?store_id={STORE}",
                   headers=_headers(role))
    assert r.status_code == 200 and r.json()["cross_store"] is False, r.text[:300]


def test_a_two_shop_login_still_gets_the_other_shop_flag(client, two_shops):
    """An admin or a two-shop area manager reaches the unit; the till then
    refuses it on `cross_store` (productIntake.ts), as before."""
    area = {**_user("AREA_MANAGER"), "store_ids": [STORE, OTHER]}
    for headers in (_headers("ADMIN"), _hdr(area)):
        r = client.get(f"/api/v1/inventory/barcode/{OTHER_BARCODE}?store_id={STORE}",
                       headers=headers)
        assert r.status_code == 200 and r.json()["cross_store"] is True, r.text[:300]


# ---------------------------------------------------------------------------
# FU-06: the dead, shadowed catalogue export is gone
# ---------------------------------------------------------------------------


def test_the_shadowed_catalog_export_handler_is_deleted():
    from api.main import app
    from api.services.rbac_policy import POLICY

    paths = {getattr(r, "path", "") for r in app.routes}
    assert "/api/v1/catalog/products/export" not in paths
    assert not [r for r in POLICY if r["path"] == "/api/v1/catalog/products/export"]


# ---------------------------------------------------------------------------
# FU-04/05/19: no cost ranking for a reader who cannot see cost
# ---------------------------------------------------------------------------


@pytest.fixture
def dead_stock(shop):
    # PA: few units, dear. PB: many units, cheap. Cost order != quantity order.
    shop.products.insert_many([
        {"product_id": "PA", "name": "Dear", "store_id": STORE, "quantity": 1,
         "cost_price": 1000.0},
        {"product_id": "PB", "name": "Cheap", "store_id": STORE, "quantity": 5,
         "cost_price": 10.0},
    ])
    return shop


def _dead_ids(client, role):
    r = client.get("/api/v1/analytics-v2/dead-stock", headers=_headers(role))
    assert r.status_code == 200, r.text[:300]
    return [d["product_id"] for d in r.json()["dead_stock"]]


@pytest.mark.parametrize("role", MANAGERS)
def test_dead_stock_runs_by_quantity_for_managers(client, dead_stock, role):
    assert _dead_ids(client, role) == ["PB", "PA"]


@pytest.mark.parametrize("role", ACCOUNTS)
def test_dead_stock_runs_by_cost_for_accounts(client, dead_stock, role):
    assert _dead_ids(client, role) == ["PA", "PB"]


@pytest.fixture
def collection_rows(shop, monkeypatch):
    """S1 holds the dear stock and sells 1; S2 holds cheap stock and sells 5."""
    from api.routers import collections_insights as ci
    from api.services import collection_insights as svc

    monkeypatch.setattr(ci, "_get_db", lambda: shop)
    monkeypatch.setattr(ci, "_load_collection_or_404",
                        lambda db, cid: {"collection_id": cid, "title": "Aviators"})
    monkeypatch.setattr(ci, "_scope_store", lambda user, store_id: None)
    monkeypatch.setattr(svc, "member_ids", lambda db, cid, collection=None: {
        "product_ids": ["PA", "PB"], "skus": [], "capped": False})
    monkeypatch.setattr(svc, "_stock_rollup", lambda db, pids, store_id=None, by_store=False: {
        ("PA", "S1"): 1, ("PB", "S2"): 10})
    monkeypatch.setattr(svc, "movement_summary", lambda db, m, store_id=None, days=90,
                        by_store=False: {"per_store": {"S1": {"units_d30": 1},
                                                       "S2": {"units_d30": 5}}})
    monkeypatch.setattr(svc, "_product_price_map", lambda db, pids: {
        "PA": {"cost_price": 1000.0}, "PB": {"cost_price": 1.0}})


def _shop_order(client, role):
    r = client.get("/api/v1/collections/C1/insights/stores", headers=_headers(role))
    assert r.status_code == 200, r.text[:300]
    return [s["store_id"] for s in r.json()["stores"]]


@pytest.mark.parametrize("role", (*MANAGERS, "CATALOG_MANAGER"))
def test_collection_shops_run_by_sales_for_managers(client, collection_rows, role):
    assert _shop_order(client, role) == ["S2", "S1"]


def test_collection_shops_run_by_stock_value_for_accounts(client, collection_rows):
    assert _shop_order(client, "ADMIN") == ["S1", "S2"]


def test_collection_summary_ties_never_break_by_cost(monkeypatch):
    from api.services import collection_insights as svc

    monkeypatch.setattr(svc, "members_by_collection", lambda db, cids: {
        "A": {"product_ids": ["PA"], "skus": [], "row_count": 1},
        "B": {"product_ids": ["PB"], "skus": [], "row_count": 1}})
    monkeypatch.setattr(svc, "_stock_rollup", lambda db, pids, store_id=None: {"PA": 1, "PB": 10})
    monkeypatch.setattr(svc, "_product_price_map", lambda db, pids: {
        "PA": {"cost_price": 1000.0}, "PB": {"cost_price": 1.0}})
    monkeypatch.setattr(svc, "movement_summary", lambda db, m, store_id=None, days=30: {
        "per_product": {}})
    docs = [{"collection_id": "A", "title": "a"}, {"collection_id": "B", "title": "b"}]
    db = SimpleNamespace()
    assert [r["collection_id"] for r in svc.summary(db, docs, cost_ok=True)] == ["A", "B"]
    assert [r["collection_id"] for r in svc.summary(db, docs, cost_ok=False)] == ["B", "A"]


# ---------------------------------------------------------------------------
# Role casing: one canonical spelling, at decode and at login
# ---------------------------------------------------------------------------


def test_normalize_roles_canonical_casing():
    from api.services.user_roles import normalize_roles

    assert normalize_roles([" accountant ", "Store_Manager", "sales_cashier"]) == [
        "ACCOUNTANT", "STORE_MANAGER", "SALES_STAFF"]


def test_a_lowercase_role_token_is_read_canonically(client, voucher):
    token_user = {**_user("ACCOUNTANT"), "user_id": "u-lower", "roles": ["accountant"]}
    r = client.get(_JE_READS[0], headers=_hdr(token_user))
    assert r.status_code == 200, r.text[:300]
    from api.routers.auth import create_access_token, decode_token

    assert decode_token(create_access_token(token_user))["roles"] == ["ACCOUNTANT"]


def test_a_lowercase_role_on_the_user_record_logs_in_canonically(client, shop, monkeypatch):
    from api import dependencies as deps
    from api.routers import auth as auth_mod

    shop.users.insert_one({
        "user_id": "u-acc", "username": "lowacc", "is_active": True,
        "password_hash": auth_mod.hash_password("correct-horse-9"),
        "roles": [" accountant"], "store_ids": [STORE],
    })
    monkeypatch.setattr(deps, "get_user_repository",
                        lambda: SimpleNamespace(collection=shop.users))
    monkeypatch.setattr(auth_mod, "_login_limiter", auth_mod.LoginRateLimiter())
    r = client.post("/api/v1/auth/login",
                    json={"username": "lowacc", "password": "correct-horse-9"})
    assert r.status_code == 200, r.text[:300]
    assert r.json()["user"]["roles"] == ["ACCOUNTANT"], r.json()
    # ...and the token it hands out reads journal entries (an ACCOUNTANT read).
    token = r.json()["access_token"]
    r = client.get(_JE_READS[0], headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text[:300]
