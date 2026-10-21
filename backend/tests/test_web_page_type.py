"""
Website page type (owner 2026-10-19): Standard | Pre-order | Preview only.

Pinned here (mongomock; no network, no Shopify):
  * the door validates it: a Pre-order needs whole ship days, an unknown type
    is refused -- on create, on update_product and on PUT /products
  * it is MODEL-level: a new colour takes the model's page, a colour cannot
    change it, a change on the model reaches every colour
  * the push payload: templateSuffix 'preorder' / 'preview-only' / '' (a
    hand-set theme_suffix kept on a standard page) and the
    ims.preorder_ship_days metafield
  * product templates are gone: no route, no RBAC row

Run: JWT_SECRET_KEY=test ENVIRONMENT=test python -m pytest backend/tests/test_web_page_type.py -q
"""

from __future__ import annotations

import asyncio
import os
import sys

import pytest

os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

mongomock = pytest.importorskip("mongomock")

from api import dependencies as deps  # noqa: E402
from api.routers import products as products_mod  # noqa: E402
from api.services import product_master as pm  # noqa: E402
from api.services import shopify_push  # noqa: E402
from api.services.web_page_type import clean_page_type  # noqa: E402
from database.repositories.product_repository import ProductRepository  # noqa: E402

ADMIN = {"user_id": "u-admin", "username": "admin", "roles": ["ADMIN"]}


class _Conn:
    is_connected = True

    def __init__(self, db):
        self.db = db

    def get_collection(self, name):
        return self.db[name]

    def __getitem__(self, name):
        return self.db[name]


@pytest.fixture
def db(monkeypatch):
    raw = mongomock.MongoClient()["ims_test_page_type"]
    monkeypatch.setattr(deps, "get_db", lambda: _Conn(raw))
    monkeypatch.setattr(products_mod, "get_product_repository", lambda: ProductRepository(raw["products"]))
    return raw


def _create(db, colour, *, size=None, door=None, **page):
    return pm.create_product(
        category="SUNGLASS",
        attributes={"brand_name": "Ray-Ban", "model_no": "RB4165", "colour_code": colour,
                    **({"lens_size": size} if size else {}), **page},
        **(door or {}),
        mrp=5000,
        offer_price=5000,
        actor="u-admin",
        actor_name="admin",
        product_repo=ProductRepository(db["products"]),
        db=_Conn(db),
    )


def _attrs(db, pid):
    return db["products"].find_one({"product_id": pid})["attributes"]


def test_clean_page_type_canonicalises_and_refuses():
    assert clean_page_type({"web_page_type": "pre-order", "preorder_ship_days": "10"}) == (
        {"web_page_type": "PRE_ORDER", "preorder_ship_days": 10},
        None,
    )
    assert clean_page_type({"web_page_type": "Preview only"})[0]["web_page_type"] == "PREVIEW_ONLY"
    assert clean_page_type({"shape": "Round"}) == ({"shape": "Round"}, None)
    for days in (None, "", "0", "2.5", "366", "abc", True):
        assert clean_page_type({"web_page_type": "PRE_ORDER", "preorder_ship_days": days})[1][0] == (
            "preorder_ship_days"
        ), days
    assert clean_page_type({"web_page_type": "BACKORDER"})[1][0] == "web_page_type"


def test_the_door_refuses_a_pre_order_without_ship_days(db):
    with pytest.raises(pm.ProductMasterError) as e:
        _create(db, "601", web_page_type="PRE_ORDER")
    assert e.value.status == 422 and e.value.field == "preorder_ship_days"
    lead = _create(db, "601", web_page_type="pre-order", preorder_ship_days="10")
    assert (_attrs(db, lead["product_id"])["web_page_type"], _attrs(db, lead["product_id"])["preorder_ship_days"]) == (
        "PRE_ORDER", 10)
    repo = ProductRepository(db["products"])
    with pytest.raises(pm.ProductMasterError) as e:
        pm.update_product(product_id=lead["product_id"],
                          patch={"attributes": {**lead["attributes"], "preorder_ship_days": ""}},
                          actor="u1", product_repo=repo, db=_Conn(db))
    assert e.value.field == "preorder_ship_days"
    # Review P7: a patch carrying only the ship days is judged as the stored
    # Pre-order it edits.
    with pytest.raises(pm.ProductMasterError) as e:
        pm.update_product(product_id=lead["product_id"], patch={"attributes": {"preorder_ship_days": 0}},
                          actor="u1", product_repo=repo, db=_Conn(db))
    assert e.value.field == "preorder_ship_days"
    # PUT /products (the form's edit door) runs the same rule.
    with pytest.raises(products_mod.HTTPException) as e:
        asyncio.run(products_mod.update_product(
            lead["product_id"],
            products_mod.ProductUpdate(attributes={"web_page_type": "PRE_ORDER", "preorder_ship_days": "0"}),
            current_user=ADMIN,
        ))
    assert e.value.status_code == 422
    assert _attrs(db, lead["product_id"])["preorder_ship_days"] == 10


def test_the_page_is_the_models_and_reaches_every_colour(db):
    repo = ProductRepository(db["products"])
    lead = _create(db, "601", web_page_type="PRE_ORDER", preorder_ship_days=10)
    child = _create(db, "710")
    assert child["variant_of"] == lead["product_id"]
    assert (_attrs(db, child["product_id"])["web_page_type"], _attrs(db, child["product_id"])["preorder_ship_days"]) == (
        "PRE_ORDER", 10)
    # A colour cannot change the model's page ...
    with pytest.raises(pm.ProductMasterError) as e:
        pm.update_product(product_id=child["product_id"],
                          patch={"attributes": {**_attrs(db, child["product_id"]), "web_page_type": "STANDARD"}},
                          actor="u1", product_repo=repo, db=_Conn(db))
    assert e.value.status == 422 and e.value.field == "web_page_type"
    assert "Website page" in e.value.message
    # ... the model's change reaches it.
    out = pm.update_product(product_id=lead["product_id"],
                            patch={"attributes": {**_attrs(db, lead["product_id"]), "web_page_type": "PREVIEW_ONLY"}},
                            actor="u1", product_repo=repo, db=_Conn(db))
    assert out["family_not_updated"] == []
    assert _attrs(db, child["product_id"])["web_page_type"] == "PREVIEW_ONLY"


def test_a_runbook_size_on_a_live_listing_takes_the_models_page(db):
    """Review P3: the MASTER runbook (variant_of + live_lead_rows) skipped the
    model-level copy, so a size disagreed with its lead's page."""
    lead = _create(db, "601", size="50", web_page_type="PRE_ORDER", preorder_ship_days=10)
    size = _create(db, "601", size="54", door={"variant_of": lead["product_id"], "live_lead_rows": True})
    assert size["variant_of"] == lead["product_id"]
    assert (_attrs(db, size["product_id"]).get("web_page_type"),
            _attrs(db, size["product_id"]).get("preorder_ship_days")) == ("PRE_ORDER", 10)


def test_the_catalogue_review_editor_never_moves_the_page(db, monkeypatch):
    """Review P3: PUT /catalog/products edits the catalogue copy only, so it
    must not move the website page (the spine's, the model's)."""
    from api.routers import catalog as catalog_mod

    monkeypatch.setattr(catalog_mod, "_get_db", lambda: _Conn(db))
    monkeypatch.setattr(deps, "get_product_repository", lambda: ProductRepository(db["products"]))
    lead = _create(db, "601", web_page_type="PRE_ORDER", preorder_ship_days=10)
    twin_id = lead["pim_product_id"]
    with pytest.raises(catalog_mod.HTTPException) as e:
        asyncio.run(catalog_mod.update_catalog_product(
            twin_id, catalog_mod.ProductUpdateInput(attributes={"web_page_type": "STANDARD"}), ADMIN))
    assert e.value.status_code == 422 and "website page" in str(e.value.detail)
    assert db["catalog_products"].find_one({"id": twin_id})["attributes"]["web_page_type"] == "PRE_ORDER"


def _twin(page=None, days=None, theme_suffix=None):
    attrs = {"brand_name": "Ray-Ban"}
    if page:
        attrs["web_page_type"] = page
    if days is not None:
        attrs["preorder_ship_days"] = days
    ecom = {"shopify_product_id": "gid://shopify/Product/1"}
    if theme_suffix:
        ecom["theme_suffix"] = theme_suffix
    return {"id": "cat-1", "title": "RB4165", "category": "SUNGLASS", "attributes": attrs, "ecom": ecom}


def test_the_push_picks_the_theme_template_and_sends_the_ship_days():
    """templateSuffix: the page type's template; a standard page sends a
    hand-set theme_suffix, else NOTHING (an admin-set template stays) --
    '' only to move a page IMS had made pre-order / preview-only back."""
    def sent(doc, page):
        inp = shopify_push.build_product_input(doc, [], page_type=page)
        return inp.get("templateSuffix", "<not sent>")

    assert sent(_twin("PRE_ORDER", 10), "PRE_ORDER") == "preorder"
    assert sent(_twin("PREVIEW_ONLY"), "PREVIEW_ONLY") == "preview-only"
    assert sent(_twin(), "STANDARD") == "<not sent>"
    assert sent(_twin("STANDARD", theme_suffix="rayban-meta"), "STANDARD") == "rayban-meta"
    assert sent(_twin("PRE_ORDER", 10, theme_suffix="rayban-meta"), "PRE_ORDER") == "preorder"
    back = _twin("STANDARD")
    back["ecom"]["template_suffix_sent"] = "preorder"
    assert sent(back, "STANDARD") == ""
    back["ecom"]["template_suffix_sent"] = ""
    assert sent(back, "STANDARD") == "<not sent>"
    mf = {m["key"]: m["value"] for m in shopify_push.build_product_metafields(_twin("PRE_ORDER", 10))}
    assert mf["preorder_ship_days"] == "10" and mf["web_page_type"] == "PRE_ORDER"


def test_the_gate_keeps_every_page_standard_until_superadmin_switches_it_on(db):
    from api.services.web_page_type import effective_page_type

    twin = _twin("PRE_ORDER", 10)
    assert effective_page_type(_Conn(db), twin) == "STANDARD"
    db["system_settings"].insert_one({"_id": "default", "web_page_types_enabled": True})
    assert effective_page_type(_Conn(db), twin) == "PRE_ORDER"
    out = asyncio.run(products_mod.get_website_verdict(brand="", current_user=ADMIN))
    assert out["web_page_types_enabled"] is True


def test_switching_page_types_off_runs_the_stock_pass_at_once(db, monkeypatch):
    """Review of #1244: on -> off re-sends stock now (THE pass), so a
    Pre-order listing stops selling at 0 immediately; off -> on, or any
    other system setting, does not run it."""
    from api.routers import settings as settings_mod

    ran = []
    the_db = _Conn(db)

    async def _pass(pass_db, **_kw):
        ran.append(pass_db)

    monkeypatch.setattr(deps, "get_db", lambda: the_db)  # never the real one
    monkeypatch.setattr(settings_mod, "_get_settings_collection", lambda name: db[name])
    monkeypatch.setattr(shopify_push, "sync_stock_levels", _pass)
    su = {"user_id": "su", "roles": ["SUPERADMIN"]}

    async def _put(body):
        await settings_mod.update_system_settings(body, current_user=su)
        await asyncio.sleep(0)

    asyncio.run(_put({"web_page_types_enabled": True}))
    asyncio.run(_put({"maintenance_mode": False}))
    assert ran == []
    asyncio.run(_put({"web_page_types_enabled": False}))
    assert ran == [the_db]
    assert db["system_settings"].find_one({"_id": "default"})["maintenance_mode"] is False


def test_the_writeback_records_the_template_it_sent(db):
    from api.services.shopify_push.writeback import _writeback_product

    db["catalog_products"].insert_one({"id": "cat-1", "ecom": {}})
    _writeback_product(_Conn(db), "cat-1", "gid://shopify/Product/1", template_suffix="")
    assert db["catalog_products"].find_one({"id": "cat-1"})["ecom"]["template_suffix_sent"] == ""


def test_product_templates_are_gone():
    from api.main import app
    from api.services.rbac_policy import POLICY

    assert not [r for r in app.routes if "/product-templates" in getattr(r, "path", "")]
    assert not [row for row in POLICY if "/product-templates" in row["path"]]
