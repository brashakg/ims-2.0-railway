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
F73       Reorder level (owner 2026-09-28 and 2026-10-01): only a level above 0
          is a level. -1, 0, below 0, missing, garbage or no product row = NOT
          SET = no low-stock alert, no top-up, 'not set' on screen. A new
          product is born -1, PUT accepts -1, and ONE helper decides for every
          reader (reorder_policy.reorder_level / is_low_stock / low_stock_rows,
          the only caller of find_low_stock -- guarded structurally below).

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


def test_f12_a_live_listing_whose_brand_is_switched_off_is_named_in_the_run(monkeypatch):
    """A live Carrera listing edited after its brand went off the website is
    not pushed (the one gate), so it keeps selling at its last pushed price:
    the live-sync run names it in its failures, never just a count."""
    from api.services import policy_engine as pe
    from api.services import shopify_live_sync as live_sync

    async def _boom(*_a, **_k):  # pragma: no cover - must never run
        raise AssertionError("no Shopify network in tests")

    async def _no_stock(db):
        return sp.PushResult(ok=True, mode=sp.MODE_SIMULATED, entity="stock",
                             action="sync", target_id="all", payload={})

    monkeypatch.setattr(sp, "_graphql", _boom)
    monkeypatch.setattr(sp, "sync_stock_levels", _no_stock)
    monkeypatch.setattr(deps, "get_audit_repository", lambda: None)
    db = _db()
    monkeypatch.setattr(pe, "_coll", lambda name="policy_settings": db[name])
    doc = _live_doc("Carrera")
    doc["ecom"]["locally_modified"] = True
    doc["offer_price"] = 4900.0  # the raise that never reaches the website
    db.seed("catalog_products", [doc])
    run = _run(live_sync.sync_live_products(db, trigger="manual", actor="u-admin"))
    assert (run["selected"], run["attempted"], run["live_not_pushed"]) == (1, 0, 1)
    assert [(f["sku"], f["code"]) for f in run["failures"]] == [
        ("SKU-Carrera", live_sync.LIVE_NOT_PUSHED)
    ]
    assert "not for the website" in run["failures"][0]["error"]
    assert "still live" in run["failures"][0]["error"]


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


# What the Add-product form collects, per registry key (one value each).
_FORM_VALUES = {
    "brand_name": "Carrera", "model_no": "CA 8895", "model_name": "Acuvue Oasys",
    "colour_code": "807/2", "colour_name": "Hazel Brown", "power": "-1.25",
    "expiry_date": "2027-01-31", "index": "1.56", "coating": "HC",
    "name": "Frame fitting", "subbrand": "Vista", "lens_size": "52.5", "size": "M",
}


def _form_post(category, attrs):
    """The create payload the form posts (formModel.buildProductPayload):
    brand and model straight from the same attributes, nothing invented. The
    frontend test 'previews an Optical Lens (no model) too, and its save
    invents no model' (addProductOwnerRulings.test.tsx) pins the form to this
    shape -- the form used to send model_no || model_name || subbrand || 'STD'."""
    return prod_router.ProductCreate(
        category=category, brand=attrs.get("brand_name", ""),
        model=attrs.get("model_no") or attrs.get("model_name") or "",
        attributes=dict(attrs), mrp=1000.0, offer_price=900.0,
    )


def _preview(category, attrs):
    return asyncio.run(pm_router.sku_preview(
        pm_router.SkuPreviewRequest(category=category, attributes=dict(attrs)),
        current_user=_ADMIN,
    ))["sku"]


@pytest.mark.parametrize("category", pm.canonical_categories())
def test_f13_every_category_saves_the_sku_it_previews(door, category):
    """The Review preview and the save come from the same minter for EVERY
    category -- Optical Lens (no model) and Services (no brand) included. The
    form used to invent a model ('STD', or the sub-brand) only on save."""
    spec = pm.category_spec(category)
    attrs = {k: _FORM_VALUES[k] for k in (*spec.required, *spec.optional) if k in _FORM_VALUES}
    created = door(_form_post(category, attrs))
    assert created["sku"] == _preview(category, attrs)


def test_f13_an_optical_lens_sku_reads_brand_and_sub_brand(door):
    """Optical Lens has no model: the minter puts the sub-brand in its place,
    and with no sub-brand mints no filler ('STD' was the form's)."""
    lens = {"brand_name": "Essilor", "index": "1.56", "coating": "HC"}
    assert door(_form_post("LS", dict(lens, subbrand="Crizal")))["sku"] == "LS-ESSILOR-CRIZAL"
    assert door(_form_post("LS", lens))["sku"] == "LS-ESSILOR"


def test_f13_the_size_keeps_its_decimal_point(door):
    """52.5 and 525 are different eye sizes, so different SKUs; a float 54.0
    from a catalog door is 54. A minted SKU always passes the SKU check (a
    clone or an import may send it back)."""
    def sku(size):
        return pm.build_sku("FR", dict(_CARRERA, lens_size=size))

    assert sku("52.5") == "FR-CARRERA-CA8895-807-52.5"
    assert sku("525") == "FR-CARRERA-CA8895-807-525"
    assert sku(54.0) == sku("54") == sku("54.0") == "FR-CARRERA-CA8895-807-54"
    assert sku("52/18") == "FR-CARRERA-CA8895-807-52-18"
    created = door(_form(brand="Carrera", model="CA8895", color="807",
                         attributes={"lens_size": "52.5"}))
    assert created["sku"] == "FR-CARRERA-CA8895-807-52.5"
    assert pm.is_acceptable_sku(created["sku"])
    again = door(_form(brand="Carrera", model="CA8895", color="809", sku=created["sku"] + "B"))
    assert again["sku"] == created["sku"] + "B"


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
    """reorder_policy.reorder_level is what every reader and screen gets: only
    a level above 0 is a level; -1, 0, below 0, missing, garbage or no product
    doc at all is None (the screens print 'not set'). Owner 2026-10-01: a 0
    can never fire with stock on the shelf, so it is not set too."""
    from api.services.reorder_policy import is_low_stock, reorder_level

    for not_set in ({"reorder_point": -1}, {"reorder_point": 0}, {"reorder_point": "0"},
                    {"reorder_point": -3}, {}, {"reorder_point": None},
                    {"reorder_point": "x"}, {"reorder_point": ""}, None,
                    {"inventory": {"reorder_level": 0}}, {"inventory": {"reorder_level": -1}}):
        assert reorder_level(not_set) is None, not_set
        assert not is_low_stock(not_set, 0) and not is_low_stock(not_set, -5), not_set
    assert reorder_level({"reorder_point": 3}) == 3
    assert reorder_level({"reorder_point": "3"}) == 3
    assert reorder_level({"inventory": {"reorder_level": 3}}) == 3  # a catalog doc
    assert is_low_stock({"reorder_point": 2}, 2)
    assert not is_low_stock({"reorder_point": 2}, 3)


# Every low-stock reader below is fed the SAME shelf: one unit each of a
# product with a typed level (P-SET, 2) and of every NOT-SET shape, plus a unit
# whose product row is gone (the 09-07 wipe). Only P-SET is ever low; P-SET
# also proves each reader really ran (a swallowed error lists nothing).
_NOT_SET_LEVELS = {"P-UNSET": -1, "P-ZERO": 0, "P-NEG": -3, "P-GARBAGE": "x", "P-BLANK": ""}


def _shelf_products(**extra):
    rows = [
        {"product_id": "P-SET", "sku": "S-SET", "name": "Set", "reorder_point": 2, **extra},
        {"product_id": "P-MISSING", "sku": "S-MISSING", "name": "Missing", **extra},
    ]
    return rows + [
        {"product_id": pid, "sku": "S" + pid[1:], "name": pid, "reorder_point": rp, **extra}
        for pid, rp in _NOT_SET_LEVELS.items()
    ]


def _shelf_units():
    return [_one_unit(p["product_id"]) for p in _shelf_products()] + [_one_unit("P-NO-ROW")]


def test_f73_only_a_level_above_0_puts_a_product_on_the_low_stock_list(monkeypatch):
    """GET /inventory/low-stock: a level of 0 or below, a missing or garbage
    level, or a unit whose product row is gone is never listed (it used to be
    listed at the legacy 5, and a 0 level silently never fired)."""
    assert _low_stock(monkeypatch, _shelf_products(), _shelf_units()) == {"P-SET"}


def test_f73_the_stock_ledger_badge_is_the_products_own_level():
    """GET /inventory/stock builds every row with _ledger_row: its low_stock
    (the ledger's Low Stock badge, InventoryStockPage) and reorder_point come
    from the one rule -- never a fixed 5, never always-off. The audit's
    Bokaro badge."""
    cases = {  # product, on hand -> (low_stock, reorder_point)
        "P-UNSET": ({"reorder_point": -1}, 1, (False, None)),
        "P-AT": ({"reorder_point": 2}, 2, (True, 2)),
        "P-ABOVE": ({"reorder_point": 2}, 3, (False, 2)),
        "P-MISSING": ({}, 0, (False, None)),
        "P-ZERO": ({"reorder_point": 0}, 0, (False, None)),
        "P-GARBAGE": ({"reorder_point": "x"}, 0, (False, None)),
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
    until a level is typed; 0 or missing is not typed)."""
    for level in (-1, 0, "x", None):
        for rq in (-1, 4):  # reorder suggestions off, and on
            for stock in (3, 5, 0):
                product = {"stock_quantity": stock, "reorder_quantity": rq}
                if level is not None:
                    product["reorder_point"] = level
                got = _alert(product)
                assert (got or {}).get("alertType") not in ("LOW_STOCK", "REORDER_ALERT"), (
                    level, rq, stock, got)


def test_f73_guard_a_typed_level_still_gets_its_alerts():
    low = _alert({"reorder_point": 2, "stock_quantity": 5, "reorder_quantity": -1})
    assert low["alertType"] == "LOW_STOCK"
    reorder = _alert({"reorder_point": 2, "stock_quantity": 3, "reorder_quantity": 4})
    assert reorder["alertType"] == "REORDER_ALERT"


# -- F73: every reader asks the PRODUCT's level, through the one rule --------


class _NoRows:
    """An order / customer repo with nothing in it."""

    def find_many(self, *args, **kwargs):
        return []


def _analytics(monkeypatch, products, units):
    from api.routers import analytics as an

    stock = StockRepository(StrictCollection("stock_units", [dict(u) for u in units]))
    prods = ProductRepository(StrictCollection("products", [dict(p) for p in products]))
    monkeypatch.setattr(an, "get_stock_repository", lambda: stock)
    monkeypatch.setattr(an, "get_product_repository", lambda: prods)
    monkeypatch.setattr(an, "get_order_repository", lambda: _NoRows())
    monkeypatch.setattr(an, "get_customer_repository", lambda: _NoRows())
    user = {**_ADMIN, "active_store_id": "S1"}
    return (
        asyncio.run(an.get_dashboard_summary(current_user=user, period="month", store_id="S1")),
        asyncio.run(an.get_inventory_intelligence(current_user=user, store_id="S1")),
        asyncio.run(an.get_enterprise_kpis(current_user=user, period="month", store_id="S1")),
    )


def test_f73_analytics_counts_judge_the_product_never_a_stock_unit(monkeypatch):
    """/analytics dashboard-summary, inventory-intelligence and enterprise-kpis
    count THE low-stock list (low_stock_rows). A stock_units row is one unit
    with no level: judged on its own it read a level off nothing, so the 3
    units of a -1 product counted as 3 low items."""
    units = [dict(u, quantity=1, sales_velocity=1) for u in _shelf_units()] + [
        dict(_one_unit("P-UNSET"), stock_id=f"UU{i}", quantity=1, sales_velocity=1)
        for i in range(2)
    ]
    products = [
        dict(p, cost_price=100 if p["product_id"] == "P-SET" else 50) for p in _shelf_products()
    ]
    summary, intel, kpis = _analytics(monkeypatch, products, units)
    assert summary["low_stock_items"] == 1
    assert kpis["inventory"]["low_stock_items"] == 1
    assert intel["low_stock"]["count"] == 1
    assert intel["low_stock"]["items"] == [
        {"sku": "S-SET", "name": "Set", "quantity": 1, "reorder_point": 2}
    ]
    assert intel["low_stock"]["total_value"] == 100
    # Fast-moving reads the product's level too: no level, no fast-mover.
    assert intel["fast_moving"]["count"] == 1


def _repos(monkeypatch, modules, products, units):
    """Point each module's stock + product repositories at one in-memory shelf
    (mongomock: the transfer pass groups on a composite _id)."""
    db = _mongo()
    db.stock_units.insert_many([dict(u) for u in units])
    db.products.insert_many([dict(p) for p in products])
    stock, prods = StockRepository(db.stock_units), ProductRepository(db.products)
    for mod in modules:
        monkeypatch.setattr(mod, "get_stock_repository", lambda: stock)
        monkeypatch.setattr(mod, "get_product_repository", lambda: prods)


def test_f73_stock_low_stock_mode_and_transfer_recommendations_read_the_one_list(monkeypatch):
    """GET /inventory/stock?low_stock=true and GET /inventory/transfer-
    recommendations list only P-SET (they used find_low_stock's fixed 5)."""
    donors = [  # another shop holding plenty of everything, so any low product can be refilled
        dict(_one_unit(p["product_id"]), stock_id=f"D-{p['product_id']}-{i}", store_id="S2")
        for p in _shelf_products()
        for i in range(20)
    ]
    _repos(monkeypatch, [inv], _shelf_products(), _shelf_units() + donors)
    mode = asyncio.run(inv.get_stock(
        store_id=None, product_id=None, category=None, created_by=None,
        low_stock=True, current_user=_MGR,
    ))
    assert {r["_id"] for r in mode["items"]} == {"P-SET"}
    recs = asyncio.run(inv.transfer_recommendations(store_id=None, current_user=_MGR))
    assert {r["product_id"] for r in recs["recommendations"]} == {"P-SET"}, recs


def test_f73_transfer_recommendations_refill_a_low_product_at_any_level(monkeypatch):
    """A low product holding 10 or more is still refilled: the target is twice
    the product's own level, never the old fixed threshold 5 (refill to 10),
    which skipped P20 (level 20, 12 on hand) after the one list called it low."""
    products = [{"product_id": "P20", "sku": "S20", "name": "P20", "reorder_point": 20}]
    units = [dict(_one_unit("P20"), stock_id=f"U-{i}") for i in range(12)] + [
        dict(_one_unit("P20"), stock_id=f"D-{i}", store_id="S2") for i in range(50)
    ]
    _repos(monkeypatch, [inv], products, units)
    recs = asyncio.run(inv.transfer_recommendations(store_id=None, current_user=_MGR))
    assert [(r["product_id"], r["from_store"], r["quantity"]) for r in recs["recommendations"]] == [
        ("P20", "S2", 10)  # need 40-12=28; S2 spares 50-40=10
    ], recs


def test_f73_report_low_stock_counts_read_the_one_list(monkeypatch):
    """GET /reports/dashboard, /reports/inventory and /reports/inventory/summary
    count the one list: 1, never the 7 products on hand under a fixed 5."""
    from api.routers.reports import inventory as rep_inv
    from api.routers.reports import overview

    _repos(monkeypatch, [overview, rep_inv], _shelf_products(), _shelf_units())
    for name in ("get_order_repository", "get_customer_repository", "get_task_repository"):
        monkeypatch.setattr(overview, name, lambda: None)
    dash = asyncio.run(overview.dashboard_stats(store_id=None, current_user=_MGR))
    report = asyncio.run(overview.inventory_report(store_id=None, current_user=_MGR))
    summary = asyncio.run(rep_inv.inventory_summary(store_id=None, current_user=_MGR))
    assert dash["lowStockItems"] == 1
    assert report["lowStock"] == 1
    assert summary["summary"]["low_stock_count"] == 1


def _walk(obj):
    """Every dict/list inside a response, depth first."""
    yield obj
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)


def test_f73_hub_widgets_count_low_stock_by_the_one_rule(monkeypatch):
    """Hub stock-count-status and the owner digest judge each product's own
    level: only P-SET (1 on hand, level 2) is low; a garbage level is not set
    (the old int() copy 500'd the widget on it)."""
    from api.routers import dashboard_widgets as dw

    db = _mongo()
    db.products.insert_many(_shelf_products(stock_quantity=1, is_active=True))
    monkeypatch.setattr(dw, "_coll", lambda name: db[name])
    status = asyncio.run(dw.inventory_stock_count_status(store_id=None, current_user=_ADMIN))
    assert (status["low_stock"], status["out_of_stock"]) == (1, 0)
    digest = asyncio.run(dw.owner_digest(store_id=None, current_user=_ADMIN))
    rows = [r for r in _walk(digest) if isinstance(r, dict) and "reorder_point" in r]
    assert [(r["sku"], r["reorder_point"]) for r in rows] == [("S-SET", 2)], digest


def test_f73_catalog_inventory_needs_reorder_only_at_a_level(monkeypatch):
    """GET /catalog/products/{id}/inventory: needs_reorder is the one rule on
    the catalog doc's inventory.reorder_level; 0 and -1 are not set."""
    docs = {
        pid: {"sku": pid, "title": pid,
              "inventory": {"total_quantity": 0, "locations": {}, "reorder_level": lvl}}
        for pid, lvl in (("C-SET", 2), ("C-ZERO", 0), ("C-UNSET", -1))
    }
    monkeypatch.setattr(cat, "_get_catalog_product", docs.get)
    got = {}
    for pid in docs:
        r = asyncio.run(cat.get_product_inventory(pid, current_user=_ADMIN))
        got[pid] = (r["reorder_level"], r["needs_reorder"])
    assert got == {"C-SET": (2, True), "C-ZERO": (None, False), "C-UNSET": (None, False)}


def _find_low_stock_callers():
    """(file, line) of every `.find_low_stock(` call in the backend, tests excluded."""
    import ast
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1]
    hits = []
    for path in root.rglob("*.py"):
        rel = path.relative_to(root).as_posix()
        if rel.startswith("tests/") or "site-packages" in rel:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        hits += [
            (rel, node.lineno)
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "find_low_stock"
        ]
    return hits


def test_f73_guard_only_the_one_helper_calls_find_low_stock():
    """Structural guard (owner 2026-10-01: ONE helper decides set/low for every
    reader). find_low_stock counts units under a FIXED threshold and never asks
    the product's level, so a reader calling it directly brings the old 5 back
    -- and that reader's own tests can stay green on legacy-shaped data. Only
    reorder_policy.low_stock_rows may call it; every other reader calls that."""
    callers = _find_low_stock_callers()
    assert [rel for rel, _ in callers] == ["api/services/reorder_policy.py"], callers


def _mongo():
    import mongomock

    return mongomock.MongoClient().db


def test_f73_jarvis_counts_and_lists_low_stock_by_the_same_rule(monkeypatch):
    """Jarvis's overview count, its inventory alerts and its 'value at risk'
    list read the same products through the one rule: only a level above 0
    is low; 0, -1, missing or garbage never."""
    from api.routers import jarvis

    db = _mongo()
    db.products.insert_many(_shelf_products(stock_quantity=1, mrp=100, is_active=True))
    db.products.insert_one({"name": "ABOVE", "sku": "S-ABOVE", "stock_quantity": 9,
                            "reorder_point": 4, "mrp": 100})
    monkeypatch.setattr(jarvis, "get_db_collection", lambda name: db[name])
    overview = jarvis.JarvisAnalyticsEngine._compute_overview_live()
    ctx = jarvis.JarvisAnalyticsEngine.get_extended_context()
    live = jarvis.JarvisAnalyticsEngine._compute_inventory_live()
    listed = {r["name"]: r["reorder_point"] for r in ctx["low_stock_value_at_risk"]}
    assert listed == {"Set": 2}
    assert overview["inventory"]["low_stock_items"] == 1
    alerts = [r for r in _walk(live) if isinstance(r, dict) and r.get("type") == "low_stock"]
    assert [(r["sku"], r["reorder_point"]) for r in alerts] == [("S-SET", 2)], live


def _recommendations(monkeypatch, products, sold):
    from datetime import timedelta

    from api.routers.reports import purchase
    from api.utils.ist import now_ist_naive

    db = _mongo()
    if products:
        db.products.insert_many([dict(p) for p in products])
    db.orders.insert_many([
        {"store_id": "S1", "status": "CONFIRMED",
         "created_at": now_ist_naive() - timedelta(days=1),
         "items": [{"product_id": pid, "quantity": qty, "unit_price": 100}]}
        for pid, qty in sold.items()
    ])
    monkeypatch.setattr(purchase, "get_db", lambda: db)
    res = asyncio.run(purchase.purchase_recommendations(
        store_id="S1", lookback_days=90, lead_time_days=7, reorder_cycle_days=14,
        safety_buffer_days=7, min_velocity=2, limit=100,
        current_user={**_ADMIN, "active_store_id": "S1"},
    ))
    return {r["product_id"]: (r["suggested_order_qty"], r["reorder_point"])
            for r in res["recommendations"]}


def test_f73_a_sku_with_no_product_row_is_never_topped_up_to_five(monkeypatch):
    """A SKU sold in the window whose product row is gone (the 09-07 wipe) has
    no level at all: the purchase report suggests what its sales need (1),
    never a top-up to a level it does not have."""
    recs = _recommendations(monkeypatch, [], {"P-GONE": 2})
    assert recs == {"P-GONE": (1, None)}


def test_f73_no_level_buys_only_what_sales_need(monkeypatch):
    """Purchase recommendations: a product with no level (missing, 0, -1,
    garbage) gets no top-up to a level -- just what its sales need (1) -- and
    reports reorder_point None ('not set'). A typed 5 still tops up to 5."""
    products = [
        dict(p, reorder_point=5) if p["product_id"] == "P-SET" else p for p in _shelf_products()
    ]
    sold = {p["product_id"]: 2 for p in products}
    want = {pid: (1, None) for pid in sold}
    want["P-SET"] = (5, 5)
    assert _recommendations(monkeypatch, products, sold) == want


def test_f73_get_product_sends_the_level_the_rule_gives(monkeypatch):
    """GET /products/{id} (the Add/Edit form's read) carries the level the rule
    gives, so an edit that never touches the level round-trips it: a product
    with no level (missing, 0, -1, garbage) reads -1 = not set (the form's
    blank box), a typed level reads itself."""
    repo = ProductRepository(StrictCollection("products", [
        {"product_id": "P-LEGACY", "sku": "L"},
        {"product_id": "P-UNSET", "sku": "U", "reorder_point": -1},
        {"product_id": "P-ZERO", "sku": "Z", "reorder_point": 0},
        {"product_id": "P-SET", "sku": "S", "reorder_point": 3},
        {"product_id": "P-GARBAGE", "sku": "G", "reorder_point": "x"},
    ]))
    monkeypatch.setattr(prod_router, "get_product_repository", lambda: repo)
    got = {
        pid: asyncio.run(prod_router.get_product(pid, current_user=_ADMIN))["reorder_point"]
        for pid in ("P-LEGACY", "P-UNSET", "P-ZERO", "P-SET", "P-GARBAGE")
    }
    assert got == {"P-LEGACY": -1, "P-UNSET": -1, "P-ZERO": -1, "P-SET": 3, "P-GARBAGE": -1}
