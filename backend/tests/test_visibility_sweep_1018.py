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
                        "unit_price": total, "item_total": total,
                        "taxable_value": total}]}  # the shape orders stamp
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
    # The manager's shop, net of GST (the category P&L reads each line's
    # taxable value; it used to read `total`, which only O8/O9 carried):
    #   O8  stamped taxable_value                       111.00
    #   O1  item_total 9999, no rate, FRAME-less -> 5%  9999 / 1.05 = 9522.857
    #   O3  the same line (READY still counts)          9999 / 1.05 = 9522.857
    #   O2  lines under legacy `order_items`, not `items` -> 0
    #   111 + 2 * 9522.857 = 19156.71
    assert own == pytest.approx(19156.71, abs=0.01), own
    assert admin_all - own == pytest.approx(OTHER_REVENUE), (admin_all, own)
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
def test_accounts_still_set_a_first_cost(client, shop, role):
    """A product with no cost yet takes its first one from the editor."""
    shop.products.update_one({"product_id": "P1"}, {"$set": {"cost_price": None}})
    r = client.put("/api/v1/products/P1", json={"cost_price": 1234.0}, headers=_headers(role))
    assert r.status_code == 200, r.text[:300]
    assert shop.products.find_one({"product_id": "P1"})["cost_price"] == 1234.0


# S4 review: once a product has an average, the editor doors refuse a
# different cost (409 pointing to the correction box, which asks a reason and
# writes the cost history); the same figure sent back unchanged passes.
_EDITOR_COST_DOORS = [
    ("/api/v1/products/P1", lambda c: {"cost_price": c}),
    ("/api/v1/catalog/products/P1", lambda c: {"pricing": {"cost_price": c}}),
]


@pytest.mark.parametrize("path,body", _EDITOR_COST_DOORS)
@pytest.mark.parametrize("role", ("ADMIN", "SUPERADMIN"))
def test_the_editor_doors_never_change_an_average(client, shop, path, body, role):
    from api.services.product_cost import EDITOR_COST_REFUSED

    shop.catalog_products.insert_one({"id": "P1", "name": "Aviator", "category": "FR",
                                      "pricing": {"mrp": 9999.0, "cost_price": COST}})
    r = client.put(path, json=body(1234.0), headers=_headers(role))
    assert r.status_code == 409, (path, r.status_code, r.text[:300])
    assert r.json()["detail"] == EDITOR_COST_REFUSED
    assert shop.products.find_one({"product_id": "P1"})["cost_price"] == COST
    assert not shop.products.find_one({"product_id": "P1"}).get("cost_history")
    r = client.put(path, json=body(COST), headers=_headers(role))
    assert r.status_code != 409, (path, r.status_code, r.text[:300])


@pytest.mark.parametrize("role", ("ADMIN", "SUPERADMIN"))
def test_the_catalogue_editor_finds_a_create_door_spine(client, shop, role):
    """A product made through the create door links spine and twin through
    the spine's pim_product_id (a different uuid): the guard reads that
    spine, not a spine sharing the twin's id."""
    from api.services.product_cost import EDITOR_COST_REFUSED

    shop.products.update_one({"product_id": "P1"}, {"$set": {"pim_product_id": "TWIN1"}})
    shop.catalog_products.insert_one({"id": "TWIN1", "name": "Aviator", "category": "FR",
                                      "pricing": {"mrp": 9999.0, "cost_price": COST}})
    path = "/api/v1/catalog/products/TWIN1"
    r = client.put(path, json={"pricing": {"cost_price": 1234.0}}, headers=_headers(role))
    assert r.status_code == 409, (r.status_code, r.text[:300])
    assert r.json()["detail"] == EDITOR_COST_REFUSED
    spine = shop.products.find_one({"product_id": "P1"})
    assert spine["cost_price"] == COST and not spine.get("cost_history")
    r = client.put(path, json={"pricing": {"cost_price": COST}}, headers=_headers(role))
    assert r.status_code == 200, (r.status_code, r.text[:300])
    assert shop.products.find_one({"product_id": "P1"})["cost_price"] == COST


# S4 review round 2: the editor doors never write the cost with a plain update
# (it overwrites a bill landing between the check and the write); a first cost
# is seeded (written only while there is no average, with a history row).
# (door path, twin id, body): both editors, with a twin that shares the spine
# id and a create-door twin linked by the spine's pim_product_id.
_EDITOR_DOORS_BY_TWIN = [
    ("/api/v1/products/P1", "P1", lambda c: {"cost_price": c}),
    ("/api/v1/products/P1", "TWIN1", lambda c: {"cost_price": c}),
    ("/api/v1/catalog/products/P1", "P1", lambda c: {"pricing": {"cost_price": c}}),
    ("/api/v1/catalog/products/TWIN1", "TWIN1", lambda c: {"pricing": {"cost_price": c}}),
]


def _twin(shop, twin_id, cost):
    if twin_id != "P1":
        shop.products.update_one({"product_id": "P1"}, {"$set": {"pim_product_id": twin_id}})
    shop.catalog_products.insert_one({"id": twin_id, "name": "Aviator", "category": "FR",
                                      "pricing": {"mrp": 9999.0, "cost_price": cost}})


@pytest.mark.parametrize("path,twin_id,body", _EDITOR_DOORS_BY_TWIN)
@pytest.mark.parametrize("before,sent", ((COST, COST), (None, 1234.0)))
def test_a_bill_landing_after_the_editor_check_is_kept(
    client, shop, monkeypatch, path, twin_id, body, before, sent
):
    from api.services import product_cost

    shop.products.update_one({"product_id": "P1"}, {"$set": {"cost_price": before}})
    _twin(shop, twin_id, before)
    real = product_cost.refuse_editor_cost

    def check_then_a_bill(product, cost):
        real(product, cost)
        product_cost.correct(shop, "P1", 5000.0, "a bill lands", "u-accounts")

    monkeypatch.setattr(product_cost, "refuse_editor_cost", check_then_a_bill)
    r = client.put(path, json=body(sent), headers=_headers("ADMIN"))
    assert r.status_code == 200, (path, r.status_code, r.text[:300])
    spine = shop.products.find_one({"product_id": "P1"})
    assert spine["cost_price"] == 5000.0 == spine["moving_avg_cost"], (path, twin_id)
    assert [row["new_cost"] for row in spine["cost_history"]] == [5000.0]


@pytest.mark.parametrize("path,twin_id,body", _EDITOR_DOORS_BY_TWIN)
@pytest.mark.parametrize("role", ("ADMIN", "SUPERADMIN"))
def test_a_first_cost_typed_in_an_editor_has_its_history_row(client, shop, path, twin_id, body, role):
    shop.products.update_one({"product_id": "P1"}, {"$set": {"cost_price": None}})
    _twin(shop, twin_id, None)
    r = client.put(path, json=body(1234.0), headers=_headers(role))
    assert r.status_code == 200, (path, r.status_code, r.text[:300])
    spine = shop.products.find_one({"product_id": "P1"})
    assert spine["cost_price"] == 1234.0
    rows = spine.get("cost_history") or []
    assert len(rows) == 1, rows
    assert rows[0]["kind"] == "EDITOR_FIRST_COST"
    assert rows[0]["by"] == _user(role)["user_id"]
    assert rows[0]["new_cost"] == 1234.0 and rows[0]["key"] in spine["cost_keys"]


@pytest.mark.parametrize("path,twin_id,body", _EDITOR_DOORS_BY_TWIN)
def test_a_catalogue_only_price_sent_back_unchanged_is_no_first_cost(client, shop, path, twin_id, body):
    """S4 review round 3: the editor sends the stored price back on every
    save; a price that was only a catalogue price stays one (no average, no
    history row naming someone who never typed a cost)."""
    shop.products.update_one({"product_id": "P1"},
                             {"$set": {"cost_price": 777.0, "cost_price_is_catalogue_only": True}})
    _twin(shop, twin_id, 777.0)
    sent = body(777.0)
    sent["name"] = "Aviator II"
    r = client.put(path, json=sent, headers=_headers("ADMIN"))
    assert r.status_code == 200, (path, r.status_code, r.text[:300])
    spine = shop.products.find_one({"product_id": "P1"})
    assert spine["cost_price_is_catalogue_only"] is True, (path, twin_id)
    assert not spine.get("cost_history"), spine.get("cost_history")


def test_a_first_cost_turns_a_draft_whose_only_gap_is_the_cost_active(client, shop):
    """S4 review round 3: the restamp after an editor's first cost reads the
    product WITH that cost (the cost is never in the plain write)."""
    shop.products.update_one({"product_id": "P1"}, {"$set": {
        "category": "FRAME", "brand": "Ray-Ban",
        "attributes": {"brand_name": "Ray-Ban", "model_no": "RB1", "colour_code": "C1"},
        "mrp": 5000.0, "offer_price": 4000.0, "hsn_code": "9003", "gst_rate": 5.0,
        "cost_price": None, "catalog_status": "DRAFT", "done_gaps": ["cost_price"],
    }})
    r = client.put("/api/v1/products/P1", json={"cost_price": 1234.0}, headers=_headers("ADMIN"))
    assert r.status_code == 200, r.text[:300]
    spine = shop.products.find_one({"product_id": "P1"})
    assert spine["cost_price"] == 1234.0
    assert "cost_price" not in (spine.get("done_gaps") or []), spine.get("done_gaps")
    assert spine["catalog_status"] == "ACTIVE", (spine["catalog_status"], spine.get("done_gaps"))


def test_a_product_created_with_a_cost_has_its_history_row(client, shop):
    """Every create door (the form, clone-vary, promote) goes through
    product_master.create_product: a cost it is born with is its first
    average, with its history row."""
    r = client.post("/api/v1/products", json={**_PROD_CREATE, "offer_price": 5000.0, "color": "C1", "cost_price": 900.0},
                    headers=_headers("ADMIN"))
    assert r.status_code in (200, 201), r.text[:300]
    made = shop.products.find_one({"model": "RB2"}) or shop.products.find_one(
        {"attributes.model_no": "RB2"})
    assert made is not None
    assert made["cost_price"] == 900.0
    rows = made.get("cost_history") or []
    assert len(rows) == 1, rows
    assert rows[0]["kind"] == "FIRST_COST" and rows[0]["by"] == "u-admin"
    assert rows[0]["key"] in made["cost_keys"]


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
    # This file asks about the ORDER of the rows; whose shops they are is
    # test_shop_scope_rules_1018.py's question.
    monkeypatch.setattr(ci, "filter_docs_by_store", lambda rows, user: rows)
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


# ---------------------------------------------------------------------------
# Security review of #1232 (2026-10-18)
# ---------------------------------------------------------------------------

_JE_JV = "/api/v1/finance/tally/journal-jv"


@pytest.mark.parametrize("role", MANAGERS)
def test_managers_cannot_export_the_journal_to_tally(client, voucher, role):
    from api.routers.finance import journal_entries as je
    from api.services.rbac_policy import policy_for

    r = client.get(_JE_JV, headers=_headers(role))
    assert r.status_code == 403, r.text[:300]
    assert sorted(policy_for("GET", _JE_JV)["allowed"]) == ["ACCOUNTANT", "ADMIN"]
    # The handler's own gate, not only the policy row.
    with pytest.raises(HTTPException) as err:
        asyncio.run(je.get_tally_journal_jv(current_user=_user(role)))
    assert err.value.status_code == 403


@pytest.mark.parametrize("role", ACCOUNTS)
def test_accounts_export_the_journal_to_tally(client, voucher, role):
    r = client.get(_JE_JV, headers=_headers(role))
    assert r.status_code == 200 and "xml" in r.headers["content-type"], r.text[:300]


@pytest.fixture
def vouchers(voucher):
    voucher.journal_entries.insert_many([
        {"je_id": "JE2", "store_id": OTHER, "status": "POSTED", "created_at": datetime.now()},
        {"je_id": "JE3", "store_id": None, "status": "POSTED", "created_at": datetime.now()},
    ])
    return voucher


def test_another_shops_voucher_is_not_found(client, vouchers):
    missing = client.get("/api/v1/finance/journal-entries/JE-NONE", headers=_headers("ACCOUNTANT"))
    assert missing.status_code == 404, missing.text[:300]
    for je_id in ("JE2", "JE3"):  # another shop's, and an HQ voucher
        r = client.get(f"/api/v1/finance/journal-entries/{je_id}", headers=_headers("ACCOUNTANT"))
        assert (r.status_code, r.json()) == (404, missing.json()), (je_id, r.text[:300])
    own = client.get("/api/v1/finance/journal-entries/JE1", headers=_headers("ACCOUNTANT"))
    assert own.status_code == 200, own.text[:300]
    for je_id in ("JE2", "JE3"):
        r = client.get(f"/api/v1/finance/journal-entries/{je_id}", headers=_headers("ADMIN"))
        assert r.status_code == 200, (je_id, r.text[:300])


# Opening stock: quantities for every inventory role, a unit cost for the
# accounts roles only.
_OPENING = "/api/v1/inventory/opening-stock/{}"
_NON_COST_INVENTORY = ("AREA_MANAGER", "STORE_MANAGER", "CATALOG_MANAGER", "WORKSHOP_STAFF")


def _opening(rows):
    return {"rows": rows, "skip_if_existing": False}


def _opening_units(shop):
    return list(shop.stock_units.find({"source": "OPENING_STOCK"}))


@pytest.mark.parametrize("role", _NON_COST_INVENTORY)
def test_opening_stock_cost_is_refused_for_non_cost_roles(client, shop, role):
    from api.services.cost_mask import COST_WRITE_REFUSED

    costed = _opening([{"sku": "SKU-P1", "quantity": 2, "unit_cost": 999.0}])
    for step in ("preview", "commit"):
        r = client.post(_OPENING.format(step), json=costed, headers=_headers(role))
        assert r.status_code == 403, (step, r.text[:300])
        assert r.json()["detail"] == COST_WRITE_REFUSED
    assert _opening_units(shop) == []
    # Quantities alone still go on the shelf, with no cost.
    plain = _opening([{"sku": "SKU-P1", "quantity": 2}])
    r = client.post(_OPENING.format("commit"), json=plain, headers=_headers(role))
    assert r.status_code == 200, r.text[:300]
    units = _opening_units(shop)
    assert len(units) == 2 and not any(u.get("cost_price") or u.get("unit_cost") for u in units)


def test_accounts_enter_the_opening_stock_cost(client, shop):
    costed = _opening([{"sku": "SKU-P1", "quantity": 2, "unit_cost": 999.0}])
    r = client.post(_OPENING.format("commit"), json=costed, headers=_headers("ADMIN"))
    assert r.status_code == 200, r.text[:300]
    assert [u["cost_price"] for u in _opening_units(shop)] == [999.0, 999.0]


# The barcode trace and the count scan: another shop's unit is an unknown code.


@pytest.fixture
def traced(two_shops):
    # The own-shop unit was once sold, and returned, at the other shop.
    two_shops.orders.insert_one(
        {"order_id": "O7", "order_number": "BV-O7", "store_id": OTHER, "status": "DELIVERED",
         "created_at": datetime.now(), "items": [{"product_id": "P1", "barcode": OWN_BARCODE}]})
    two_shops.returns.insert_one(
        {"return_number": "R7", "store_id": OTHER, "status": "DONE", "created_at": datetime.now(),
         "items": [{"barcode": OWN_BARCODE}]})
    return two_shops


def _trace(client, role, code):
    r = client.get(f"/api/v1/inventory/barcode/{code}/trace", headers=_headers(role))
    assert r.status_code == 200, r.text[:300]
    return r.json()


@pytest.mark.parametrize("role", ("STORE_MANAGER", "CASHIER"))
def test_trace_of_another_shops_unit_reads_as_unknown(client, traced, role):
    unknown = {**_trace(client, role, "2999999999999"), "barcode": None}
    assert {**_trace(client, role, OTHER_BARCODE), "barcode": None} == unknown
    assert unknown["stock_unit"] is None


@pytest.mark.parametrize("role", ("STORE_MANAGER", "CASHIER"))
def test_trace_lists_the_callers_shops_only(client, traced, role):
    mine = _trace(client, role, OWN_BARCODE)
    assert mine["stock_unit"]["barcode"] == OWN_BARCODE
    assert mine["sales"] == [] and mine["returns"] == [], mine
    admin = _trace(client, "ADMIN", OWN_BARCODE)
    assert [s["order_number"] for s in admin["sales"]] == ["BV-O7"]
    assert [r["return_number"] for r in admin["returns"]] == ["R7"]
    assert _trace(client, "ADMIN", OTHER_BARCODE)["stock_unit"]["barcode"] == OTHER_BARCODE


def _count_scan(client, role, code):
    return client.post("/api/v1/inventory/stock-count-scan",
                       json={"barcode": code, "physical_count": 1}, headers=_headers(role))


@pytest.mark.parametrize("role", ("STORE_MANAGER", "WORKSHOP_STAFF"))
def test_count_scan_of_another_shops_unit_is_not_found(client, two_shops, role):
    missing = _count_scan(client, role, "2999999999999")
    foreign = _count_scan(client, role, OTHER_BARCODE)
    assert missing.status_code == foreign.status_code == 404, foreign.text[:300]
    assert foreign.json() == missing.json()
    own = _count_scan(client, role, OWN_BARCODE)
    assert own.status_code == 200 and own.json()["product_id"] == "P1", own.text[:300]
    assert _count_scan(client, "ADMIN", OTHER_BARCODE).status_code == 200
