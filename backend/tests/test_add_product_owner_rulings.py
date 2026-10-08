"""Add product - owner rulings of 2026-09-28 (second set) and 2026-09-29.

Audit rows F12, F13, F69 (the backend half) and F73. Each test pins one rule
the owner set; the `guard` tests pin what the fix must not break.

F12 / D6  The brand default ALWAYS decides whether a product goes to the
          website, read LIVE: shopify_push.product_push_refusal (through
          catalog_dictionary.brand_website_refusal) gates the product push,
          the price push, the image push and the push-all queue. No product
          stores a copy (a stored flag went stale after a brand edit or a Brand
          Master change). The Add/Edit form shows that gate's verdict and reason.
F13 / D5  New products get a readable SKU, category-brand-model-colour-size,
          e.g. FR-CARRERA-CA8895-807-54, from product_master.build_sku -- the
          one minter every door and POST /products/sku-preview use. Existing
          SKUs never change.
F69       The FORM create door keeps the weight the form sends (`weight`, the
          key PUT writes and the form reads), so the "same model" chip can copy it.
F73       Reorder levels are per shop (owner ruling D12, main #1179, tested in
          test_per_shop_reorder_levels.py): the create door stamps no
          chain-wide reorder_point, so a new product has no level = not set.

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

import api.dependencies as deps  # noqa: E402
from api.routers import catalog as cat  # noqa: E402
from api.routers import product_master as pm_router  # noqa: E402
from api.routers import products as prod_router  # noqa: E402
from api.services import product_master as pm  # noqa: E402
from api.services import shopify_push as sp  # noqa: E402
from database.repositories.product_repository import (  # noqa: E402
    ProductRepository,
)
from strict_fakes import StrictCollection, StrictDB  # noqa: E402

_ADMIN = {"user_id": "u-admin", "username": "admin", "roles": ["ADMIN"]}

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


def _verdict(monkeypatch, db, brand, locks=None):
    """GET /products/website-verdict -- what the Add/Edit form's line reads."""
    from api.services import policy_engine as pe

    monkeypatch.setattr(deps, "get_db", lambda: db)
    monkeypatch.setattr(pe, "get_policy", lambda key, default=None: (
        locks if key == "ecom.shopify_push_locks" and locks is not None else default))
    return _run(prod_router.get_website_verdict(brand=brand, current_user=_ADMIN))


class _Up(StrictDB):
    is_connected = True


def _up_db():
    db = _Up()
    db.seed("brand_masters", [dict(b) for b in _BRANDS] + [
        {"brand_id": "b3", "name": "Cartier", "is_active": True, "sync_to_shopify_default": True},
        {"brand_id": "b4", "name": "Titan", "is_active": False, "sync_to_shopify_default": True},
    ])
    return db


def test_f12_the_form_line_is_the_push_gate_answer(monkeypatch):
    """The form works nothing out itself: the line is product_push_refusal's
    verdict for the brand, so a push-locked brand with Brand Master ticked
    reads 'no' on the form exactly as the push refuses it."""
    db = _up_db()
    assert _verdict(monkeypatch, db, "Ray-Ban") == {"brand": "Ray-Ban", "online": True, "reason": None}
    assert _verdict(monkeypatch, db, "RAY-BAN")["online"] is True  # the gate's own casefold match
    locked = _verdict(monkeypatch, db, "Cartier", locks={"brands": ["cartier"]})
    assert locked["online"] is False and "push-locked" in locked["reason"]
    assert locked["reason"] == sp.product_push_refusal(db, {"brand": "Cartier"})


def test_f12_every_refusal_says_its_own_reason(monkeypatch):
    """A misspelt or renamed brand, an inactive one and a Brand Master read
    failure are never shown (on the form, the Catalog chip or the Buy Desk) as
    a brand that is switched off."""
    db = _up_db()
    off = _verdict(monkeypatch, db, "Carrera")["reason"]
    misspelt = _verdict(monkeypatch, db, "RayBan")["reason"]
    inactive = _verdict(monkeypatch, db, "Titan")["reason"]
    down = _verdict(monkeypatch, None, "Ray-Ban")["reason"]
    assert "not for the website" in off
    assert "not in Settings > Brand Master" in misspelt and "spelling" in misspelt
    assert "inactive" in inactive
    assert "could not be read" in down
    assert len({off, misspelt, inactive, down}) == 4
    # The Catalog / Buy Desk chip carries the same reason, not a fixed guess.
    from api.services.online_catalog import doc_online_state

    st = doc_online_state(db, {"brand": "RayBan", "images": ["https://cdn.example.com/p.jpg"]})
    assert st["online"] == "NOT_FOR_WEBSITE" and misspelt in st["note"]


def test_f12_the_gate_reads_a_brand_from_vendor_or_attributes(monkeypatch):
    """A CATALOG-door twin carries its brand only in attributes.brand_name and
    a Shopify-shaped doc only in vendor. The gate fails closed on no brand, so
    if it stopped reading either one, every such product would silently leave
    the website ('the product has no brand')."""
    from api.services import policy_engine as pe
    from api.services.online_catalog import doc_online_state

    db = _db()
    photo = {"images": ["https://cdn.example.com/p.jpg"]}
    twin = {"attributes": {"brand_name": "Ray-Ban"}, **photo}
    assert sp.product_push_refusal(db, twin) is None
    assert sp.product_push_refusal(db, {"vendor": "Ray-Ban"}) is None
    assert "not for the website" in sp.product_push_refusal(db, {"attributes": {"brand_name": "Carrera"}})
    assert "not for the website" in sp.product_push_refusal(db, {"vendor": "Carrera"})
    # The spine's own brand comes first, then vendor.
    assert "not for the website" in sp.product_push_refusal(
        db, {"brand": "Carrera", "vendor": "Ray-Ban", "attributes": {"brand_name": "Ray-Ban"}})
    assert "not for the website" in sp.product_push_refusal(
        db, {"vendor": "Carrera", "attributes": {"brand_name": "Ray-Ban"}})
    assert doc_online_state(db, twin)["online"] != "NOT_FOR_WEBSITE"
    assert _run(sp.push_product(db, {"id": "T1", "sku": "T1", "title": "t", **twin},
                                blocked=False)).action == "create"
    # ... and the push lock reads the same brand.
    monkeypatch.setattr(pe, "get_policy", lambda key, default=None: (
        {"brands": ["ray-ban"]} if key == "ecom.shopify_push_locks" else default))
    assert "push-locked" in sp.product_push_refusal(db, twin)
    assert "push-locked" in sp.product_push_refusal(db, {"vendor": "Ray-Ban"})


# ---------------------------------------------------------------------------
# F13 / D5 - readable SKU for new products, previewed by the same function
# ---------------------------------------------------------------------------


def test_f13_new_frame_gets_a_readable_sku(door):
    created = door(_form(brand="Carrera", model="CA8895", color="807",
                         attributes={"lens_size": "54"}))
    assert created["sku"] == "FR-CARRERA-CA8895-807-54"


def test_f13_punctuation_is_cleaned_and_a_colour_keeps_its_slash(door):
    created = door(_form(category="SUNGLASS", brand="Ray-Ban", model="RB 3016",
                         color="001/58"))
    assert created["sku"] == "SG-RAYBAN-RB3016-001/58"


def test_f13_different_products_never_share_a_readable_sku():
    """`-` separates the parts, so it never appears inside one: colour 901/58
    with no size is not colour 901 in size 58, and colour 1109-71 is not
    110971. The bulk door used to reject the second, genuine product as a
    'Duplicate SKU within this batch'."""
    def sku(**attrs):
        return pm.build_sku("SG", {"brand_name": "Ray-Ban", "model_no": "RB2140", **attrs})

    assert sku(colour_code="901/58") == "SG-RAYBAN-RB2140-901/58"
    assert sku(colour_code="901", size="58") == "SG-RAYBAN-RB2140-901-58"
    assert sku(colour_code="1109-71") == "SG-RAYBAN-RB2140-1109/71"
    assert sku(colour_code="110971") == "SG-RAYBAN-RB2140-110971"
    seen: set = set()
    for colour, size in (("901/58", None), ("901", "58"), ("1109-71", None), ("110971", None)):
        row = prod_router.ProductCreate(category="SUNGLASS", brand="Ray-Ban", model="RB2140",
                                        color=colour, size=size, mrp=5000.0, offer_price=4500.0)
        errors, resolved = prod_router._validate_bulk_row(row, seen)
        assert errors == [], (colour, size, errors)
        seen.add(resolved)
    assert len(seen) == 4


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
    """Optical Lens has no model, colour or size: the minter reads its
    sub-brand, coating and index in their place, and with no sub-brand mints
    no filler ('STD' was the form's)."""
    lens = {"brand_name": "Essilor", "index": "1.56", "coating": "HC"}
    assert door(_form_post("LS", dict(lens, subbrand="Crizal")))["sku"] == "LS-ESSILOR-CRIZAL-HC-1.56"
    assert door(_form_post("LS", lens))["sku"] == "LS-ESSILOR-HC-1.56"


_CRIZAL = {"brand_name": "Essilor", "subbrand": "Crizal", "index": "1.56", "coating": "HC"}


def test_f13_the_same_lens_twice_is_a_duplicate(door):
    """The form posts an Optical Lens with a blank model. The duplicate key
    still names it (brand, sub-brand, coating, index), so the exact same lens
    saved twice is the 409 rescue, never a second row LS-...-1001."""
    from fastapi import HTTPException

    first = door(_form_post("LS", _CRIZAL))
    assert first["model"] == "Crizal"
    assert first["identity_key"] == "essilor|crizal|hc|156"
    with pytest.raises(HTTPException) as exc:
        door(_form_post("LS", _CRIZAL))
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "DUPLICATE_PRODUCT"
    assert exc.value.detail["existing"]["sku"] == first["sku"]


def test_f13_a_lens_is_named_for_its_sub_brand(door):
    assert door(_form_post("LS", _CRIZAL))["name"] == "Essilor Crizal Lenses"


def test_f13_a_second_lens_of_a_sub_brand_saves_the_sku_it_previews(door):
    """Crizal 1.67 after Crizal 1.56: a different lens, so it saves -- and
    under the SKU the Review showed, not the first one's plus a counter."""
    door(_form_post("LS", _CRIZAL))
    second = dict(_CRIZAL, index="1.67")
    preview = _preview("LS", second)
    created = door(_form_post("LS", second))
    assert created["sku"] == preview == "LS-ESSILOR-CRIZAL-HC-1.67"


def test_f13_the_size_keeps_its_decimal_point(door):
    """52.5 and 525 are different eye sizes, so different SKUs; a float 54.0
    from a catalog door is 54. A minted SKU always passes the SKU check (a
    clone or an import may send it back)."""
    def sku(size):
        return pm.build_sku("FR", dict(_CARRERA, lens_size=size))

    assert sku("52.5") == "FR-CARRERA-CA8895-807-52.5"
    assert sku("525") == "FR-CARRERA-CA8895-807-525"
    assert sku(54.0) == sku("54") == sku("54.0") == "FR-CARRERA-CA8895-807-54"
    assert sku("52/18") == "FR-CARRERA-CA8895-807-52/18"
    created = door(_form(brand="Carrera", model="CA8895", color="807",
                         attributes={"lens_size": "52.5"}))
    assert created["sku"] == "FR-CARRERA-CA8895-807-52.5"
    assert pm.is_acceptable_sku(created["sku"])
    again = door(_form(brand="Carrera", model="CA8895", color="809", sku=created["sku"] + "B"))
    assert again["sku"] == created["sku"] + "B"


def test_f13_each_frame_eye_size_is_its_own_product(door):
    """Owner 2026-09-28: each frame eye size is its own variant. With the 52
    saved, the Review previewed FR-CARRERA-CA8895-807-54 while the save 409'd
    against the 52 (the SKU read the eye size, the duplicate key did not).
    The 54 now saves under the SKU it previews; the 52 twice is the 409."""
    c52 = dict(_CARRERA, lens_size="52")
    first = door(_form_post("FR", c52))
    assert first["sku"] == "FR-CARRERA-CA8895-807-52"
    c54 = dict(_CARRERA, lens_size="54")
    assert door(_form_post("FR", c54))["sku"] == _preview("FR", c54) == "FR-CARRERA-CA8895-807-54"
    assert _dup(door, _form_post("FR", dict(c52, lens_size="52.0")))["sku"] == first["sku"]
    # The SKU and the key read the same colour spelling too (color_code).
    old = {"brand_name": "Carrera", "model_no": "CA8895", "color_code": "808"}
    assert pm.compute_identity_key(*pm.identity_parts(old, "FR")) == "carrera|ca8895|808"
    assert pm.build_sku("FR", old) == "FR-CARRERA-CA8895-808"


def _dup(door, payload):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        door(payload)
    assert exc.value.status_code == 409
    return exc.value.detail["existing"]


def test_f13_a_lens_with_no_sub_brand_is_still_guarded(door):
    """Sub Brand is optional for a lens: Hoya HC 1.56 saved twice is one lens."""
    hoya = {"brand_name": "Hoya", "index": "1.56", "coating": "HC"}
    first = door(_form_post("LS", hoya))
    assert first["identity_key"] and first["sku"] == "LS-HOYA-HC-1.56"
    assert _dup(door, _form_post("LS", hoya))["sku"] == first["sku"]
    # ... and a different coating is a different lens.
    assert door(_form_post("LS", dict(hoya, coating="ARC")))["sku"] == "LS-HOYA-ARC-1.56"


def test_f13_one_index_two_spellings_is_one_lens(door):
    """The form sends '1.50'; a spreadsheet row drops the zero ('1.5'). The SKU
    calls them the same size, and so does the duplicate key."""
    first = door(_form_post("LS", dict(_CRIZAL, index="1.50")))
    assert first["sku"] == "LS-ESSILOR-CRIZAL-HC-1.5"
    assert _dup(door, _form_post("LS", dict(_CRIZAL, index="1.5")))["sku"] == first["sku"]


def test_f13_an_odd_size_is_written_as_typed():
    """The size is never read through a float: no float noise, no rounding."""
    def seg(size):
        return pm.build_sku("FR", dict(_CARRERA, lens_size=size)).rsplit("-", 1)[-1]

    assert seg("1e300") == "1E300"
    assert seg("54.1234567") == "54.1234567"
    assert seg("0.00000001") == "0.00000001"
    assert seg("nan") == "NAN"


def test_f13_a_non_ascii_digit_never_reaches_the_sku():
    """A Devanagari or fullwidth digit (a CSV import, the catalog import) is
    not a plain number: it never goes into the SKU, so the SKU minted is one
    the SKU check accepts when a clone or a re-import sends it back."""
    for category, attrs in (
        ("FR", dict(_CARRERA, lens_size="५४")),  # Devanagari 54
        ("FR", dict(_CARRERA, lens_size="５４")),  # fullwidth 54
        ("LS", dict(_CRIZAL, index="१.५६")),  # Devanagari 1.56
    ):
        sku = pm.build_sku(category, attrs)
        assert pm.is_acceptable_sku(sku), sku
        assert sku in ("FR-CARRERA-CA8895-807", "LS-ESSILOR-CRIZAL-HC")


def test_f13_the_key_rebuild_tool_writes_the_create_door_key(door):
    """migrate_identity_key_tighten (the mandatory key rebuild) derives the key
    with the create door's own rule: two Crizal lenses are no collision, and a
    lens key it leaves on file still catches the same lens."""
    from scripts import migrate_identity_key_tighten as mig

    door(_form_post("LS", _CRIZAL))
    door(_form_post("LS", dict(_CRIZAL, index="1.67")))
    door(_form_post("LS", {"brand_name": "Hoya", "index": "1.56", "coating": "HC"}))
    stats = mig.run(door.repo.collection, apply=True)
    assert stats["collisions"] == 0 and stats["rewritten"] == 0
    assert stats["unchanged"] == 3
    _dup(door, _form_post("LS", _CRIZAL))


def test_f13_an_old_std_lens_row_is_the_lens_entered_today(door):
    """main's form sent model 'STD' for a lens with no sub-brand, and the door
    folded it into model_no/model_name. The key rebuild and the create door
    both read that filler as no model, so the same Hoya HC 1.56 entered today
    is the 409 rescue onto the old row, never a silent second product."""
    from scripts import migrate_identity_key_tighten as mig

    door.repo.collection.insert_one({
        "product_id": "P-LEGACY", "sku": "LSHOYASTDHC156", "category": "OPTICAL_LENS",
        "brand": "Hoya", "model": "STD", "identity_key": "hoya|std|", "is_active": True,
        "mrp": 1000.0, "offer_price": 900.0,
        "attributes": {"brand_name": "Hoya", "model_no": "STD", "model_name": "STD",
                       "coating": "HC", "index": "1.56"},
    })
    stats = mig.run(door.repo.collection, apply=True)
    assert (stats["rewritten"], stats["collisions"]) == (1, 0)
    assert door.repo.find_by_id("P-LEGACY")["identity_key"] == "hoya|hc||156"
    hoya = {"brand_name": "Hoya", "index": "1.56", "coating": "HC"}
    assert _dup(door, _form_post("LS", hoya))["sku"] == "LSHOYASTDHC156"
    # A lens re-catalogued from that old row (clone) mints today's SKU.
    assert pm.build_sku("LS", dict(hoya, model_no="STD")) == "LS-HOYA-HC-1.56"
    # ... and its sub-brand, if it has one, still names it.
    assert pm.identity_parts(dict(hoya, model_no="STD", subbrand="Nulux"))[1] == "Nulux"
    # Guard: only a lens's filler. A coloured product's model is its model.
    key = pm.compute_identity_key(*pm.identity_parts(
        {"brand_name": "Acme", "model_no": "STD", "colour_code": "01"}))
    assert key == "acme|std|01"


_OASYS = {"brand_name": "Acuvue", "model_name": "Oasys", "power": "-1.25",
          "expiry_date": "2027-01-31"}


def test_cl_power_is_its_own_item(door):
    """Owner 2026-09-28: a contact lens's power is its own item. A second power
    of the same model is a new product (under the SKU the Review previewed),
    not the 409; the same power twice -- however it is spelt -- still is."""
    first = door(_form_post("CL", _OASYS))
    assert first["sku"] == "CL-ACUVUE-OASYS-M125"
    for power in ({"power": "-1.50"}, {"power": "+1.25"}, {"power": "-12.50"},
                  {"power": "0.00"}, {"cl_cyl": "-0.75", "cl_axis": "180"},
                  {"cl_cyl": "-0.75", "cl_axis": "90"}, {"cl_add": "+2.00"}):
        other = dict(_OASYS, **power)
        created = door(_form_post("CL", other))
        assert created["sku"] == _preview("CL", other), power
        assert created["identity_key"] != first["identity_key"], power
    assert _dup(door, _form_post("CL", dict(_OASYS, power="-1.250")))["sku"] == first["sku"]
    toric = dict(_OASYS, cl_cyl="-0.75", cl_axis="180")
    assert _dup(door, _form_post("CL", dict(toric, cl_cyl="-.75")))["sku"] == "CL-ACUVUE-OASYS-M125/CM075/X180"
    # A colour contact lens too.
    hazel = dict(_OASYS, colour_name="Hazel")
    door(_form_post("CCL", hazel))
    door(_form_post("CCL", dict(hazel, power="-2.00")))
    _dup(door, _form_post("CCL", hazel))


def test_cl_power_key_rebuild_matches_the_create_door(door):
    """The key rebuild keys an existing contact lens with its power, a stored
    plano 0 included, so re-entering that power is caught."""
    from scripts import migrate_identity_key_tighten as mig

    for pid, power in (("P-CL1", -1.25), ("P-CL0", 0.0)):
        door.repo.collection.insert_one({
            "product_id": pid, "sku": pid, "category": "CONTACT_LENS", "is_active": True,
            "brand": "Acuvue", "model": "Oasys", "identity_key": "acuvue|oasys|",
            "attributes": dict(_OASYS, power=power),
        })
    stats = mig.run(door.repo.collection, apply=True)
    assert (stats["rewritten"], stats["collisions"]) == (2, 0)
    assert door.repo.find_by_id("P-CL0")["identity_key"] == "acuvue|oasys||pl"
    assert _dup(door, _form_post("CL", _OASYS))["sku"] == "P-CL1"
    assert _dup(door, _form_post("CL", dict(_OASYS, power="0.00")))["sku"] == "P-CL0"


def test_cl_power_keeps_its_sign_however_it_is_written(door):
    """Power is a free-text box: a unit, a space, a comma, or a minus pasted
    from Word or a PDF (Unicode minus, en or em dash) keeps its sign. The
    opposite power is a new product; the same power, however spelt, is the
    409 (text kept as typed lost its sign: +1.25D was the duplicate of -1.25D,
    and an en-dash -1.25 a second product beside -1.25)."""
    minus = door(_form_post("CL", _OASYS))
    plus = door(_form_post("CL", dict(_OASYS, power="+1.25D")))
    assert (minus["sku"], plus["sku"]) == ("CL-ACUVUE-OASYS-M125", "CL-ACUVUE-OASYS-P125")
    for spelt in ("-1.25D", "-1.25 DS", "- 1.25", "\u22121.25", "\u20131.25", "\u20141,25", "-1.25d"):
        assert _dup(door, _form_post("CL", dict(_OASYS, power=spelt)))["sku"] == minus["sku"], spelt
    for spelt in ("+1.25 DS", "+ 1.25", "+1,25", "1.25"):
        assert _dup(door, _form_post("CL", dict(_OASYS, power=spelt)))["sku"] == plus["sku"], spelt
    plano = door(_form_post("CL", dict(_OASYS, power="Plano")))
    assert plano["sku"] == "CL-ACUVUE-OASYS-PL"
    assert _dup(door, _form_post("CL", dict(_OASYS, power="0.00")))["sku"] == plano["sku"]


def test_cl_a_zero_cylinder_or_add_is_none(door):
    """-1.25 with cylinder 0 or add 0 is the -1.25 lens: the same power twice,
    however spelt, is one product (and the SKU the Review shows is its SKU)."""
    first = door(_form_post("CL", _OASYS))
    for zero in ({"cl_cyl": "0"}, {"cl_add": "0.00"}, {"cl_cyl": "-0.00", "cl_add": "+0"}):
        other = dict(_OASYS, **zero)
        assert _preview("CL", other) == first["sku"], zero
        assert _dup(door, _form_post("CL", other))["sku"] == first["sku"], zero


def test_cl_a_power_that_is_not_a_number_is_refused_not_a_crash(door):
    """A power or axis in exponent form ('1E+5000') crashed the preview and the
    save with a 500 (Python's int digit limit) and a bigger one stalled the
    server; '1e400' minted a 400-digit SKU. Anything that is not a power is a
    422 naming the field, at the preview and at the save."""
    from fastapi import HTTPException

    for key, junk in (("power", "1E+5000"), ("cl_axis", "1E+5000"), ("power", "1e400"),
                      ("cl_cyl", "9" * 5000), ("cl_add", "NaN"), ("power", "-"),
                      ("power", "abc"), ("power", "Infinity")):
        with pytest.raises(pm.ProductMasterError) as exc:
            pm.build_sku("CL", dict(_OASYS, **{key: junk}))
        assert (exc.value.status, exc.value.field) == (422, key), (key, junk)
    with pytest.raises(HTTPException) as preview:
        _preview("CL", dict(_OASYS, cl_axis="1E+5000"))
    assert preview.value.status_code == 422
    with pytest.raises(HTTPException) as save:
        door(_form_post("CL", dict(_OASYS, power="1E+5000")))
    assert save.value.status_code == 422
    assert door.repo.collection.count_documents({}) == 0


def test_cl_key_rebuild_skips_a_row_whose_power_is_not_a_number(door):
    """A stored lens whose power is not a number is named for a human to fix;
    the rest of the rebuild goes on."""
    from scripts import migrate_identity_key_tighten as mig

    for pid, power in (("P-JUNK", "ask"), ("P-OK", "-1.25D")):
        door.repo.collection.insert_one({
            "product_id": pid, "sku": pid, "category": "CONTACT_LENS", "is_active": True,
            "brand": "Acuvue", "model": "Oasys", "identity_key": "acuvue|oasys|" + pid,
            "attributes": dict(_OASYS, power=power),
        })
    stats = mig.run(door.repo.collection, apply=True)
    assert (stats["unreadable"], stats["rewritten"], stats["collisions"]) == (1, 1, 0)
    assert door.repo.find_by_id("P-OK")["identity_key"] == "acuvue|oasys||m125"
    assert door.repo.find_by_id("P-JUNK")["identity_key"] == "acuvue|oasys|P-JUNK"


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


def test_f13_guard_existing_sku_never_changes_on_the_edit_form_save(monkeypatch):
    """The Edit form saves through PUT /products/{id}, not update_product: an
    identity edit there keeps the old SKU too."""
    repo = ProductRepository(StrictCollection("products"))
    repo.collection.insert_one({
        "product_id": "P-OLD", "sku": "SGRAYBANRB3016001/58", "category": "SUNGLASS",
        "brand": "Ray-Ban", "model": "RB3016", "color": "001/58", "mrp": 9000.0,
        "offer_price": 9000.0, "hsn_code": "900410", "gst_rate": 18.0,
        "attributes": {"brand_name": "Ray-Ban", "model_no": "RB3016", "colour_code": "001/58"},
        "is_active": True,
    })
    monkeypatch.setattr(prod_router, "get_product_repository", lambda: repo)
    monkeypatch.setattr(deps, "get_db", lambda: _db())
    monkeypatch.setattr(deps, "get_audit_repository", lambda: None)
    _run(prod_router.update_product(
        "P-OLD",
        prod_router.ProductUpdate(model="RB3025", color="901/58", mrp=9500.0,
                                  attributes={"model_no": "RB3025", "colour_code": "901/58"}),
        _ADMIN,
    ))
    after = repo.find_by_id("P-OLD")
    assert (after["mrp"], after["model"]) == (9500.0, "RB3025")  # the edit really landed
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
# F73 - a new product has no chain-wide level (levels are per shop, D12)
# ---------------------------------------------------------------------------


def test_f73_new_product_has_no_chain_wide_level(door):
    created = door(_form())
    assert "reorder_point" not in created and "reorder_levels" not in created


# ---------------------------------------------------------------------------
# Owner 2026-10-08 - the Buy Desk 'Create draft PO' asks the PO create gate
# ---------------------------------------------------------------------------


def test_the_draft_po_roles_are_the_po_create_gate():
    """The screen's PURCHASE_ROLES (Buy Desk 'Create draft PO', the Purchase
    pages) is the server's PO create gate: _VENDOR_ROLES + SUPERADMIN (who
    always passes require_roles), the same as the route's rbac row -- and a
    catalogue manager is in neither (owner 2026-10-08)."""
    import re as _re
    from api.routers.vendors._shared import _VENDOR_ROLES
    from api.services.rbac_policy import POLICY

    src = open(os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "frontend", "src", "pages", "purchase", "purchaseRoles.ts",
    ), encoding="utf-8").read()
    m = _re.search(r"PURCHASE_ROLES[^=]*=\s*\[([^\]]*)\]", src)
    assert m, "PURCHASE_ROLES is not in purchaseRoles.ts"
    screen = set(_re.findall(r"'([A-Z_]+)'", m.group(1)))
    assert screen == set(_VENDOR_ROLES) | {"SUPERADMIN"}
    row = next(r for r in POLICY if r["method"] == "POST"
               and r["path"] == "/api/v1/vendors/purchase-orders")
    assert set(row["allowed"]) | {"SUPERADMIN"} == screen
    assert "CATALOG_MANAGER" not in screen
