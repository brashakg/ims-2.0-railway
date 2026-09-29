"""Add product - owner rulings of 2026-09-28 (second set) and 2026-09-29.

Audit rows F12, F13, F69 (the backend half) and F73. Each test pins one rule
the owner set; the `guard` tests pin what the fix must not break.

F12 / D6  The brand default ALWAYS decides whether a product goes to the
          website: the one create door stamps it (product_master.normalise_payload)
          and the Shopify push refuses a brand that is not for the website
          (shopify_push/product.push_product), both through
          catalog_dictionary.load_brand_sync_default.
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


def test_f12_guard_brand_default_stamped_when_nothing_is_sent(door):
    assert door(_form())["sync_to_shopify"] is True
    carrera = door(_form(brand="Carrera", model="CA8895", color="807"))
    assert carrera["sync_to_shopify"] is False


def test_f12_brand_default_beats_an_explicit_false(door):
    created = door(_form(sync_to_shopify=False))
    assert created["sync_to_shopify"] is True  # Ray-Ban: website yes


def test_f12_brand_default_beats_an_explicit_true(door):
    created = door(_form(brand="Carrera", model="CA8895", color="807", sync_to_shopify=True))
    assert created["sync_to_shopify"] is False  # Carrera: website no


def _push(db, brand):
    product = {
        "id": f"P-{brand}",
        "product_id": f"P-{brand}",
        "brand": brand,
        "name": f"{brand} frame",
        "sku": f"SKU-{brand}",
        "attributes": {"brand_name": brand},
        "images": ["https://cdn.example.com/p.jpg"],  # no photo, no publish
    }
    return asyncio.new_event_loop().run_until_complete(
        sp.push_product(db, product, blocked=False)
    )


def test_f12_guard_push_lists_a_brand_that_is_for_the_website():
    res = _push(_db(), "Ray-Ban")
    assert res.mode != sp.MODE_BLOCKED
    assert res.action == "create"


def test_f12_push_refuses_a_brand_that_is_not_for_the_website():
    res = _push(_db(), "Carrera")
    assert res.mode == sp.MODE_BLOCKED
    assert res.action == "skip"
    assert res.ok is False


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


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="F69: ProductCreate has no `weight`, so pydantic drops it and "
                          "the same-model chip has nothing to copy")
def test_f69_create_door_keeps_the_weight(door):
    created = door(prod_router.ProductCreate(
        category="FRAME", brand="Ray-Ban", model="RB2140", color="901",
        mrp=5000.0, offer_price=4500.0, weight=25,
    ))
    assert created.get("weight") == 25


# ---------------------------------------------------------------------------
# F73 - reorder level -1 = not set = no low-stock alert
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="F73: normalise_payload stamps reorder_quantity -1 but no "
                          "reorder_point, so the form's 5 becomes the level")
def test_f73_new_product_is_born_with_reorder_level_not_set(door):
    created = door(_form())
    assert created.get("reorder_point") == -1


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="F73: ProductUpdate.reorder_point is ge=0, so a level can "
                          "never be cleared back to 'not set'")
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


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="F73: find_low_stock uses a fixed threshold of 5 and never "
                          "reads the product's level, so -1 still alerts")
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
