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


def _create(db, colour, **page):
    return pm.create_product(
        category="SUNGLASS",
        attributes={"brand_name": "Ray-Ban", "model_no": "RB4165", "colour_code": colour, **page},
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
    suffix = lambda doc: shopify_push.build_product_input(doc, [])["templateSuffix"]  # noqa: E731
    assert suffix(_twin("PRE_ORDER", 10)) == "preorder"
    assert suffix(_twin("PREVIEW_ONLY")) == "preview-only"
    assert suffix(_twin()) == ""
    assert suffix(_twin("STANDARD", theme_suffix="rayban-meta")) == "rayban-meta"
    assert suffix(_twin("PRE_ORDER", 10, theme_suffix="rayban-meta")) == "preorder"
    mf = {m["key"]: m["value"] for m in shopify_push.build_product_metafields(_twin("PRE_ORDER", 10))}
    assert mf["preorder_ship_days"] == "10" and mf["web_page_type"] == "PRE_ORDER"


def test_product_templates_are_gone():
    from api.main import app
    from api.services.rbac_policy import POLICY

    assert not [r for r in app.routes if "/product-templates" in getattr(r, "path", "")]
    assert not [row for row in POLICY if "/product-templates" in row["path"]]
