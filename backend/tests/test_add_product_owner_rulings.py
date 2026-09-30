"""Add product - owner rulings of 2026-09-28 (second set) and 2026-09-29.

Audit rows F12, F13, F69 (the backend half) and F73. Each test pins one rule
the owner set; the `guard` tests pin what the fix must not break.

F12 / D6  The brand default ALWAYS decides whether a product goes to the
          website, read LIVE: shopify_push.product_push_refusal (through
          catalog_dictionary.load_brand_sync_default) gates the product push,
          the price push, the image push and the push-all queue. No product
          stores a copy (a stored flag went stale after a brand edit or a Brand
          Master change).
F13 / D5  New products get a readable SKU, category-brand-model-colour-size,
          e.g. FR-CARRERA-CA8895-807-54, from product_master.build_sku -- the
          one minter every door and POST /products/sku-preview use. Existing
          SKUs never change.
F69       The FORM create door keeps the weight the form sends (`weight`, the
          key PUT writes and the form reads), so the "same model" chip can copy it.
F73       Reorder level: -1 = NOT SET = no low-stock alert. A new product is
          born -1, PUT accepts -1, and GET /inventory/low-stock lists a product
          only at or under its own level (reorder_policy.low_stock_rows).

Run: JWT_SECRET_KEY=test ENVIRONMENT=test python -m pytest backend/tests/test_add_product_owner_rulings.py -q
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

import pytest  # noqa: E402
from pydantic import ValidationError  # noqa: E402

import api.dependencies as deps  # noqa: E402
from api.routers import catalog as cat  # noqa: E402
from api.routers import inventory as inv  # noqa: E402
from api.routers import product_master as pm_router  # noqa: E402
from api.routers import products as prod_router  # noqa: E402
from api.services import product_master as pm  # noqa: E402
from api.services import shopify_push as sp  # noqa: E402
from database.repositories.product_repository import (  # noqa: E402
    ProductRepository,
    StockRepository,
)
from strict_fakes import StrictCollection, StrictDB  # noqa: E402

_ADMIN = {"user_id": "u-admin", "username": "admin", "roles": ["ADMIN"]}
_MGR = {
    "user_id": "m1",
    "roles": ["STORE_MANAGER"],
    "active_store_id": "S1",
    "store_ids": ["S1"],
}

# Ray-Ban goes to the website by default, Carrera does not (the audit's pair).
_BRANDS = [
    {"brand_id": "b1", "name": "Ray-Ban", "categories": ["SG", "FR"], "tier": "PREMIUM",
     "is_active": True, "sync_to_shopify_default": True},
    {"brand_id": "b2", "name": "Carrera", "categories": ["SG", "FR"], "tier": "PREMIUM",
     "is_active": True, "sync_to_shopify_default": False},
]

_CARRERA = {"brand_name": "Carrera", "model_no": "CA8895", "colour_code": "807", "lens_size": "54"}


def _db():
    db = StrictDB()
    db.seed("brand_masters", [dict(b) for b in _BRANDS])
    return db


def _form(**over):
    base = {
        "category": "FRAME",
        "brand": "Ray-Ban",
        "model": "RB2140",
        "color": "901",
        "mrp": 5000.0,
        "offer_price": 4500.0,
    }
    base.update(over)
    return prod_router.ProductCreate(**base)


@pytest.fixture
def door(monkeypatch):
    """The FORM create door (POST /products core) over in-memory fakes."""
    db = _db()
    repo = ProductRepository(StrictCollection("products"))
    monkeypatch.setattr(prod_router, "get_product_repository", lambda: repo)
    monkeypatch.setattr(deps, "get_db", lambda: db)
    monkeypatch.setattr(deps, "get_audit_repository", lambda: None)
    monkeypatch.setattr(pm, "mirror_enabled", lambda: False)

    def _create(product):
        return prod_router._create_via_canonical_door(product, _ADMIN, source="FORM")

    _create.repo = repo
    _create.db = db
    return _create


# ---------------------------------------------------------------------------
# F12 / D6 - the brand default always decides
# ---------------------------------------------------------------------------


def test_f12_no_create_door_stores_a_website_flag(door):
    """The brand default is read LIVE at push time; a stored copy could only go
    stale (brand edit, Brand Master change), so no door writes one -- whatever
    the payload or a door's extra columns send."""
    assert "sync_to_shopify" not in door(_form(sync_to_shopify=True))
    assert "sync_to_shopify" not in door(_form(brand="Carrera", model="CA8895", color="807"))
    via_extra = pm.build_canonical_product(
        {"category": "FR", "attributes": dict(_CARRERA), "mrp": 9000.0, "offer_price": 9000.0},
        source="MASTER", extra_fields={"sync_to_shopify": True}, db=_db(),
    )
    assert "sync_to_shopify" not in via_extra


def test_f12_catalog_door_stores_no_website_flag():
    """POST /catalog/products builds its spine with build_canonical_product and
    no db (catalog.py): with a stored flag that door always stamped False."""
    spine = pm.build_canonical_product(
        {"category": "FR", "attributes": dict(_CARRERA, brand_name="Ray-Ban"),
         "sku": "FR-X", "mrp": 9000.0, "offer_price": 9000.0},
        source="CATALOG",
    )
    assert "sync_to_shopify" not in spine


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _push(db, brand, **extra):
    product = {
        "id": f"P-{brand}",
        "product_id": f"P-{brand}",
        "brand": brand,
        "name": f"{brand} frame",
        "sku": f"SKU-{brand}",
        "attributes": {"brand_name": brand},
        "images": ["https://cdn.example.com/p.jpg"],  # no photo, no publish
        **extra,
    }
    return _run(sp.push_product(db, product, blocked=False))


def _set_brand(db, name, on):
    db["brand_masters"].update_one({"name": name}, {"$set": {"sync_to_shopify_default": on}})


def test_f12_guard_push_lists_a_brand_that_is_for_the_website():
    res = _push(_db(), "Ray-Ban")
    assert res.mode != sp.MODE_BLOCKED
    assert res.action == "create"


def test_f12_push_refuses_a_brand_that_is_not_for_the_website():
    res = _push(_db(), "Carrera")
    assert res.mode == sp.MODE_BLOCKED
    assert res.action == "skip"
    assert res.ok is False


def test_f12_a_stale_stored_flag_never_decides_the_push():
    """Products created before this rule still carry a sync_to_shopify; the
    brand decides, never that copy -- in either direction."""
    assert _push(_db(), "Carrera", sync_to_shopify=True).mode == sp.MODE_BLOCKED
    assert _push(_db(), "Ray-Ban", sync_to_shopify=False).action == "create"


def test_f12_brand_master_change_moves_the_push_at_once():
    db = _db()
    _set_brand(db, "Ray-Ban", False)
    assert _push(db, "Ray-Ban").mode == sp.MODE_BLOCKED
    _set_brand(db, "Carrera", True)
    assert _push(db, "Carrera").action == "create"


def test_f12_a_brand_edit_moves_the_push(door):
    """Created as Ray-Ban (website yes), edited to Carrera (website no)."""
    photo = {"images": ["https://cdn.example.com/p.jpg"]}  # so only the brand can refuse
    created = door(_form())
    before = _run(sp.push_product(door.db, {**created, **photo, "id": created["product_id"]},
                                  blocked=False))
    assert before.action == "create"
    pm.update_product(
        product_id=created["product_id"],
        patch={"brand": "Carrera", "attributes": {**created["attributes"], "brand_name": "Carrera"}},
        actor="u-admin", product_repo=door.repo, db=door.db,
    )
    after = door.repo.find_by_id(created["product_id"])
    assert (after.get("attributes") or {}).get("brand_name") == "Carrera"  # the edit landed
    res = _run(sp.push_product(door.db, {**after, **photo, "id": after["product_id"]},
                               blocked=False))
    assert res.mode == sp.MODE_BLOCKED and "not for the website" in (res.error or "")


def _live_doc(brand):
    return {
        "id": f"P-{brand}", "product_id": f"P-{brand}", "brand": brand, "sku": f"SKU-{brand}",
        "mrp": 5000.0, "offer_price": 4500.0, "online_price": 4500.0,
        "ecom": {"shopify_product_id": "gid://shopify/Product/1",
                 "default_variant_gid": "gid://shopify/ProductVariant/1"},
    }


def test_f12_a_brand_switched_off_stops_its_price_pushes():
    """The price writer asks the same gate as the product push."""
    db = _db()
    assert _run(sp.push_variant_prices(db, _live_doc("Ray-Ban"))).mode != sp.MODE_BLOCKED
    _set_brand(db, "Ray-Ban", False)
    res = _run(sp.push_variant_prices(db, _live_doc("Ray-Ban")))
    assert res.mode == sp.MODE_BLOCKED and res.ok is False


def test_f12_a_brand_switched_off_stops_its_image_pushes():
    db = _db()
    db.seed("catalog_products", [_live_doc("Carrera")])
    res = _run(sp.push_image(db, {"image_id": "IMG1", "product_id": "P-Carrera",
                                  "status": "APPROVED"}))
    assert res.mode == sp.MODE_BLOCKED and res.target_id == "IMG1"


def test_f12_off_brand_products_never_starve_push_all_pending(monkeypatch):
    """25 dirty Carrera twins ahead of 1 Ray-Ban twin: one press must reach the
    Ray-Ban product. The refused ones never take a slot and stay queued."""
    from api.routers.online_store_push import PRODUCT_BATCH_CAP
    from api.services import shopify_live_sync as live_sync

    monkeypatch.setattr(deps, "get_audit_repository", lambda: None)
    db = _db()
    docs = [
        {"id": f"C{i}", "sku": f"C{i}", "brand": "Carrera", "title": "Carrera frame",
         "images": ["https://cdn.example.com/c.jpg"], "ecom": {"locally_modified": True}}
        for i in range(25)
    ] + [
        {"id": "RB", "sku": "RB", "brand": "Ray-Ban", "title": "Ray-Ban frame",
         "images": ["https://cdn.example.com/r.jpg"], "ecom": {"locally_modified": True}}
    ]
    out = _run(live_sync.push_product_docs(
        db, docs, current_user=_ADMIN, max_results=100, max_sent=PRODUCT_BATCH_CAP,
    ))
    assert [r["target_id"] for r in out["results"]] == ["RB"]
    assert out["blocked_skipped"] == 25
    assert out["cap_reached"] is False


# ---------------------------------------------------------------------------
# F13 / D5 - readable SKU for new products, previewed by the same function
# ---------------------------------------------------------------------------


def test_f13_new_frame_gets_a_readable_sku(door):
    created = door(_form(brand="Carrera", model="CA8895", color="807",
                         attributes={"lens_size": "54"}))
    assert created["sku"] == "FR-CARRERA-CA8895-807-54"


def test_f13_slash_becomes_hyphen_and_punctuation_is_cleaned(door):
    created = door(_form(category="SUNGLASS", brand="Ray-Ban", model="RB 3016",
                         color="001/58"))
    assert created["sku"] == "SG-RAYBAN-RB3016-001-58"


def test_f13_preview_endpoint_shows_the_readable_sku():
    out = asyncio.run(pm_router.sku_preview(
        pm_router.SkuPreviewRequest(category="FR", attributes=dict(_CARRERA)),
        current_user=_ADMIN,
    ))
    assert out["sku"] == "FR-CARRERA-CA8895-807-54"


def test_f13_guard_preview_is_what_the_create_door_mints(door):
    """The preview must come from the minting function itself, never a copy."""
    preview = asyncio.run(pm_router.sku_preview(
        pm_router.SkuPreviewRequest(category="FRAME", attributes=dict(_CARRERA)),
        current_user=_ADMIN,
    ))
    created = door(_form(brand="Carrera", model="CA8895", color="807",
                         attributes={"lens_size": "54"}))
    assert created["sku"] == preview["sku"]


def test_f13_guard_a_clash_still_gets_a_unique_sku(door):
    first = door(_form(brand="Carrera", model="CA8895", color="807"))
    # Same identity is a 409 at this door; a different size key is a new row
    # whose base SKU may clash - the existing collision suffix keeps it unique.
    door.repo.collection.insert_one({"product_id": "LEGACY", "sku": pm.build_sku(
        "FRAME", {"brand_name": "Carrera", "model_no": "CA8895", "colour_code": "808"})})
    second = door(_form(brand="Carrera", model="CA8895", color="808"))
    assert second["sku"] != first["sku"]
    assert second["sku"].startswith(pm.build_sku(
        "FRAME", {"brand_name": "Carrera", "model_no": "CA8895", "colour_code": "808"}))


def test_f13_catalog_door_mints_the_same_sku(client, auth_headers):
    """POST /catalog/products (and its /import twin) mint through
    product_master.mint_unique_sku -- the old catalog.generate_sku copy is gone."""
    cat.CATALOG_PRODUCTS.clear()
    resp = client.post(
        "/api/v1/catalog/products",
        json={"category": "FR", "attributes": dict(_CARRERA),
              "pricing": {"mrp": 9000, "discount_category": "PREMIUM"}},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["product"]["sku"] == "FR-CARRERA-CA8895-807-54"
    assert not hasattr(cat, "generate_sku")


def test_f13_guard_existing_sku_never_changes_on_edit():
    """The 121 existing SKUs keep their run-together shape through an edit."""
    repo = ProductRepository(StrictCollection("products"))
    repo.collection.insert_one({
        "product_id": "P-OLD", "sku": "SGRAYBANRB3016001/58", "category": "SUNGLASS",
        "brand": "Ray-Ban", "model": "RB3016", "mrp": 9000.0, "offer_price": 9000.0,
        "attributes": {"brand_name": "Ray-Ban", "model_no": "RB3016", "colour_code": "001/58"},
        "is_active": True,
    })
    pm.update_product(
        product_id="P-OLD",
        patch={"attributes": {"brand_name": "Ray-Ban", "model_no": "RB3016",
                              "colour_code": "901/58"}, "mrp": 9500.0},
        actor="u-admin", product_repo=repo, db=None,
    )
    after = repo.find_by_id("P-OLD")
    assert after["mrp"] == 9500.0  # the edit really landed
    assert after["sku"] == "SGRAYBANRB3016001/58"


# ---------------------------------------------------------------------------
# F69 (backend half) - the create door keeps the weight the form sends
# ---------------------------------------------------------------------------


def test_f69_create_door_keeps_the_weight(door):
    created = door(prod_router.ProductCreate(
        category="FRAME", brand="Ray-Ban", model="RB2140", color="901",
        mrp=5000.0, offer_price=4500.0, weight=25,
    ))
    assert created.get("weight") == 25


# ---------------------------------------------------------------------------
# F73 - reorder level -1 = not set = no low-stock alert
# ---------------------------------------------------------------------------


def test_f73_new_product_is_born_with_reorder_level_not_set(door):
    created = door(_form())
    assert created.get("reorder_point") == -1


def test_f73_edit_can_clear_the_level_back_to_not_set():
    try:
        upd = prod_router.ProductUpdate(reorder_point=-1)
    except ValidationError as exc:
        raise AssertionError(f"-1 refused: {exc}") from exc
    assert upd.reorder_point == -1


def _low_stock(monkeypatch, products, units):
    stock = StrictCollection("stock_units", [dict(u) for u in units])
    prods = StrictCollection("products", [dict(p) for p in products])
    monkeypatch.setattr(inv, "get_stock_repository", lambda: StockRepository(stock))
    monkeypatch.setattr(inv, "get_product_repository", lambda: ProductRepository(prods))
    res = asyncio.run(inv.get_low_stock_alerts(store_id=None, current_user=_MGR))
    return {str(r.get("_id") or r.get("product_id")) for r in res["items"]}


def _one_unit(pid):
    return {"stock_id": f"U-{pid}", "product_id": pid, "store_id": "S1", "status": "AVAILABLE"}


def test_f73_guard_a_typed_level_still_alerts(monkeypatch):
    listed = _low_stock(
        monkeypatch,
        [{"product_id": "P-SET", "sku": "A", "reorder_point": 2}],
        [_one_unit("P-SET")],
    )
    assert listed == {"P-SET"}


def test_f73_low_stock_skips_a_product_whose_level_is_not_set(monkeypatch):
    listed = _low_stock(
        monkeypatch,
        [
            {"product_id": "P-SET", "sku": "A", "reorder_point": 2},
            {"product_id": "P-UNSET", "sku": "B", "reorder_point": -1},
        ],
        [_one_unit("P-SET"), _one_unit("P-UNSET")],
    )
    # P-SET proves the aggregation really ran (a swallowed fake error lists nothing).
    assert listed == {"P-SET"}


def test_f73_the_one_rule_says_not_set_never_minus_one():
    """reorder_policy.reorder_level is what every reader and screen gets: -1
    or garbage is None (the screens print 'not set'), never -1. A product that
    never stored a level is NOT -1: it keeps the old threshold of 5."""
    from api.services.reorder_policy import is_low_stock, reorder_level

    assert reorder_level({"reorder_point": -1}) is None
    assert reorder_level({}) == 5
    assert is_low_stock({}, 5) and not is_low_stock({}, 6)
    assert reorder_level({"reorder_point": "x"}) is None
    assert reorder_level({"reorder_point": 0}) == 0
    assert reorder_level({"inventory": {"reorder_level": 3}}) == 3  # a catalog doc
    assert not is_low_stock({"reorder_point": -1}, -5)  # oversold, still no alert
    assert is_low_stock({"reorder_point": 2}, 2)


def test_f73_a_product_that_never_stored_a_level_still_alerts(monkeypatch):
    """Bulk create, PO walk-in, vendor import and catalog promote never stamped a
    level. Those products must stay on the low-stock list at the old 5 (the
    Mongo suites test_inventory_quantity / test_inventory_correctness assert the
    same against a real database in CI)."""
    listed = _low_stock(
        monkeypatch,
        [
            {"product_id": "P-LEGACY", "sku": "L"},
            {"product_id": "P-LEGACY-FULL", "sku": "F"},
            {"product_id": "P-UNSET", "sku": "U", "reorder_point": -1},
        ],
        [_one_unit("P-LEGACY")]
        + [dict(_one_unit("P-LEGACY-FULL"), stock_id=f"UF{i}") for i in range(6)]
        + [_one_unit("P-UNSET"), _one_unit("P-NO-MASTER")],
    )
    # 1 unit at 5 -> low; 6 units -> not; -1 -> never; a unit whose product
    # row is gone never stored a level either -> listed as before.
    assert listed == {"P-LEGACY", "P-NO-MASTER"}


def test_f73_the_stock_ledger_badge_is_the_products_own_level():
    """GET /inventory/stock builds every row with _ledger_row: its low_stock
    (the ledger's Low Stock badge, InventoryStockPage) and reorder_point come
    from the one rule -- never a fixed 5, never always-off. The audit's
    Bokaro badge."""
    cases = {  # product, on hand -> (low_stock, reorder_point)
        "P-UNSET": ({"reorder_point": -1}, 1, (False, None)),
        "P-AT": ({"reorder_point": 2}, 2, (True, 2)),
        "P-ABOVE": ({"reorder_point": 2}, 3, (False, 2)),
        "P-LEGACY": ({}, 4, (True, 5)),
    }
    for pid, (product, on_hand, want) in cases.items():
        row = inv._ledger_row({"product_id": pid, "sku": pid, **product}, on_hand, 0, {}, "S1")
        assert (row["low_stock"], row["reorder_point"]) == want, pid


def _alert(product):
    from datetime import datetime

    now = datetime(2026, 9, 30)
    return inv._build_stock_alert(
        product, sold_30=30, last_sale=now, now=now, dead_days=90, lead_time_days=7,
    )


def test_f73_no_low_stock_or_reorder_alert_until_a_level_is_typed():
    """Stock Alerts: the velocity branches ask the level too (owner: no alert
    until a level is typed)."""
    for rq in (-1, 4):  # reorder suggestions off, and on
        for stock in (3, 5, 0):
            got = _alert({"stock_quantity": stock, "reorder_point": -1, "reorder_quantity": rq})
            assert (got or {}).get("alertType") not in ("LOW_STOCK", "REORDER_ALERT"), (rq, stock, got)


def test_f73_guard_a_typed_or_legacy_level_still_gets_its_alerts():
    for product in ({"reorder_point": 2}, {}):  # typed; never stored (legacy 5)
        low = _alert({**product, "stock_quantity": 5, "reorder_quantity": -1})
        assert low["alertType"] == "LOW_STOCK"
        reorder = _alert({**product, "stock_quantity": 3, "reorder_quantity": 4})
        assert reorder["alertType"] == "REORDER_ALERT"
