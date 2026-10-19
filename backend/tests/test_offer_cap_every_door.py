"""
An HQ offer deeper than the item's cap is refused at EVERY door that writes one
================================================================================
Money review of #1236, round 2 (2026-10-19). The till sells an HQ offer as-is,
without counting it against anyone's limit (orders/pricing measures the store
discount from the offer). So the category / luxury-brand cap has to hold where
the offer is SET -- and only the bulk tools enforced it: a single-product edit
or a catalogue PUT could save a 30% offer on a 2%-cap Cartier frame, which then
sold at Rs 70,000 on an MRP of Rs 1,00,000.

THE check is services/pricing_caps.offer_write_problem (the bulk tools' own
evaluate_offer_price verdict, refused in words). Every door below runs it:
  * the canonical create door (product_master.normalise_payload -- every
    create, catalogue create / promote and import goes through it)
  * POST /products (pre-check) and PUT /products/{id}
  * POST /catalog/products (pre-check) and PUT /catalog/products/{id}
  * the product-master edit (product_master.update_product)
  * POST /products/bulk-price and /bulk-offer -- already did
    (test_bulk_pricing.py::test_cap_violation_rejected_with_reason).
An edit that changes no price, tier or brand keeps the old MRP-only rule, so a
legacy row's standing offer never blocks an unrelated edit.

No emoji in this file (Windows cp1252).
"""
from __future__ import annotations

import os
import sys

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

from test_add_product_owner_rulings import (  # noqa: E402
    _form,
    _put as _products_put,
    door,  # noqa: F401  (pytest fixture)
)
from test_catalog_review_put import (  # noqa: E402
    _bvi_doc,
    _put as _catalog_put,
    catalog_mod,
    env,  # noqa: F401  (pytest fixture)
)
from api.services import pricing_caps  # noqa: E402
from api.services import product_master as pm  # noqa: E402

CARTIER = {"brand_name": "Cartier", "model_no": "CT0001", "colour_code": "GLD"}
REFUSED = "deeper than the item's discount cap"


@pytest.fixture
def cartier_door(door):  # noqa: F811
    """The FORM create door with Cartier in its Brand Master (LUXURY, frames)."""
    door.db.get_collection("brand_masters").insert_one(
        {"brand_id": "b-ct", "name": "Cartier", "categories": ["FR"], "tier": "LUXURY",
         "is_active": True, "sync_to_shopify_default": False})
    return door


def test_the_one_check():
    f = pricing_caps.offer_write_problem
    assert REFUSED in f(100000, 70000, "LUXURY", "Cartier")  # 30% on a 2% cap
    assert f(100000, 98000, "LUXURY", "Cartier") is None  # exactly 2%
    assert f(1000, 1200) == "Offer price cannot exceed MRP"
    assert f(None, 900) is None and f(1000, None) is None
    # An unchanged legacy offer on an unrelated edit: the MRP rule only.
    assert f(100000, 70000, "LUXURY", "Cartier", judge_cap=False) is None
    assert pricing_caps.offer_terms_changed({"offer_price": 900.0}, {"offer_price": 900}) is False
    assert pricing_caps.offer_terms_changed({"mrp": 1100}, {"mrp": 1000}) is True
    assert pricing_caps.offer_terms_changed({"discount_category": "luxury"},
                                            {"discount_category": "LUXURY"}) is False


def test_the_canonical_create_door_refuses_it():
    """Every create, catalogue create / promote and import runs this door."""
    with pytest.raises(pm.ProductMasterError) as exc:
        pm.normalise_payload(category="FRAME", attributes=dict(CARTIER),
                             mrp=100000, offer_price=70000, sku="CT-DOOR-1")
    assert exc.value.status == 400 and REFUSED in exc.value.message
    doc = pm.normalise_payload(category="FRAME", attributes=dict(CARTIER),
                               mrp=100000, offer_price=98000, sku="CT-DOOR-2")
    assert doc["offer_price"] == 98000


def test_single_product_create_refuses_it(cartier_door):
    door = cartier_door
    with pytest.raises(HTTPException) as exc:
        door(_form(brand="Cartier", model="CT0002", color="GLD", mrp=100000.0,
                   offer_price=70000.0))
    assert exc.value.status_code == 400 and REFUSED in str(exc.value.detail)


def test_single_product_edit_refuses_it_and_leaves_an_untouched_offer_alone(cartier_door):
    door = cartier_door
    created = door(_form(brand="Cartier", model="CT0003", color="GLD", mrp=100000.0,
                         offer_price=100000.0))
    pid = created["product_id"]
    with pytest.raises(HTTPException) as exc:
        _products_put(pid, offer_price=70000.0)
    assert exc.value.status_code == 400 and REFUSED in str(exc.value.detail)
    assert door.repo.find_by_id(pid)["offer_price"] == 100000.0

    # A legacy row already holding a too-deep offer still takes an edit that
    # moves no price, tier or brand (the edit form re-sends the old price).
    door.repo.collection.update_one({"product_id": pid}, {"$set": {"offer_price": 70000.0}})
    _products_put(pid, offer_price=70000.0, mrp=100000.0, name="Cartier CT0003 Gold")
    assert door.repo.find_by_id(pid)["name"] == "Cartier CT0003 Gold"


def test_catalog_create_refuses_it(client, auth_headers):
    catalog_mod.CATALOG_PRODUCTS.clear()
    r = client.post("/api/v1/catalog/products", headers=auth_headers, json={
        "category": "FR",
        "attributes": dict(CARTIER, model_no="CT0004"),
        "pricing": {"mrp": 100000, "offer_price": 70000, "discount_category": "LUXURY"},
    })
    assert r.status_code == 400, r.text
    assert REFUSED in r.text
    catalog_mod.CATALOG_PRODUCTS.clear()


def test_catalog_edit_refuses_it(env):  # noqa: F811
    doc = _bvi_doc(doc_id="clx0capput1")
    catalog_mod.CATALOG_PRODUCTS[doc["id"]] = doc  # LUXURY 5%, offer 4% off
    with pytest.raises(HTTPException) as exc:
        _catalog_put(doc["id"], {"pricing": {"offer_price": 3500.0}})  # 30% off
    assert exc.value.status_code == 400 and REFUSED in str(exc.value.detail)
    # Lowering the MRP deepens the standing offer: judged too.
    with pytest.raises(HTTPException):
        _catalog_put(doc["id"], {"pricing": {"mrp": 9000.0, "offer_price": 4800.0}})
    assert catalog_mod.CATALOG_PRODUCTS[doc["id"]]["pricing"]["offer_price"] == 4800.0


def test_the_product_master_edit_refuses_it():
    from database.connection import MockCollection
    from database.repositories.product_repository import ProductRepository

    repo = ProductRepository(MockCollection("products"))
    created = pm.create_product(category="FRAME", attributes=dict(CARTIER, model_no="CT0005"),
                                mrp=100000, offer_price=100000, actor="u1", product_repo=repo)
    with pytest.raises(pm.ProductMasterError) as exc:
        pm.update_product(product_id=created["product_id"], patch={"offer_price": 70000},
                          actor="u1", product_repo=repo)
    assert exc.value.status == 400 and REFUSED in exc.value.message
    # A tier change that makes the standing offer too deep is refused as well.
    created2 = pm.create_product(category="FRAME", attributes={"brand_name": "Ray-Ban",
                                 "model_no": "RB0006", "colour_code": "BLK"},
                                 mrp=10000, offer_price=8800, actor="u1", product_repo=repo)
    with pytest.raises(pm.ProductMasterError):
        pm.update_product(product_id=created2["product_id"],
                          patch={"discount_category": "LUXURY"}, actor="u1", product_repo=repo)



# ---------------------------------------------------------------------------
# Round-3 HIGH: a brand change sent only in attributes.brand_name is a brand
# change -- on the product, and on every variant a model edit reaches.
# ---------------------------------------------------------------------------

from test_model_families import (  # noqa: E402
    _Conn,
    _create as _family_create,
    _p,
    _put as _family_put,
    db,  # noqa: F401  (pytest fixture)
)


def _family(db):  # noqa: F811
    """A Ray-Ban model: the lead at MRP, a variant 12% off (inside MASS 15%)."""
    lead = _family_create(db, {"brand_name": "Ray-Ban", "model_no": "RB4165", "colour_code": "601"})
    child = _family_create(db, {"brand_name": "Ray-Ban", "model_no": "RB4165", "colour_code": "710"},
                           offer_price=4400)
    assert child["variant_of"] == lead["product_id"]
    return lead, child


def test_a_model_renamed_to_cartier_is_refused_whole_on_the_master_door(db):  # noqa: F811
    from database.repositories.product_repository import ProductRepository

    lead, child = _family(db)
    repo = ProductRepository(db["products"])
    with pytest.raises(pm.ProductMasterError) as exc:
        pm.update_product(product_id=lead["product_id"],
                          patch={"attributes": {**lead["attributes"], "brand_name": "Cartier"}},
                          actor="u1", product_repo=repo, db=_Conn(db))
    assert exc.value.status == 400
    assert child["sku"] in exc.value.message and "nothing was changed" in exc.value.message
    assert _p(db, lead["product_id"])["brand"] == "Ray-Ban"  # nothing changed
    assert _p(db, child["product_id"])["attributes"]["brand_name"] == "Ray-Ban"


def test_a_model_renamed_to_cartier_is_refused_whole_on_the_edit_form(db):  # noqa: F811
    lead, child = _family(db)
    with pytest.raises(HTTPException) as exc:
        _family_put(lead["product_id"], attributes={**lead["attributes"], "brand_name": "Cartier"})
    assert exc.value.status_code == 400 and child["sku"] in str(exc.value.detail)
    assert _p(db, lead["product_id"])["brand"] == "Ray-Ban"
    assert _p(db, child["product_id"])["attributes"]["brand_name"] == "Ray-Ban"


def test_an_attributes_only_brand_change_on_a_deep_offer_is_refused(db):  # noqa: F811
    """No flat brand in the request -- the server renames from the attribute."""
    from database.repositories.product_repository import ProductRepository

    solo = _family_create(db, {"brand_name": "Ray-Ban", "model_no": "RB3025", "colour_code": "001"},
                          offer_price=4400)
    with pytest.raises(HTTPException) as exc:
        _family_put(solo["product_id"], attributes={**solo["attributes"], "brand_name": "Cartier"})
    assert exc.value.status_code == 400 and REFUSED in str(exc.value.detail)
    with pytest.raises(pm.ProductMasterError):
        pm.update_product(product_id=solo["product_id"],
                          patch={"attributes": {**solo["attributes"], "brand_name": "Cartier"}},
                          actor="u1", product_repo=ProductRepository(db["products"]), db=_Conn(db))
    assert _p(db, solo["product_id"])["brand"] == "Ray-Ban"


def test_an_attributes_only_brand_change_on_the_catalogue_is_refused(env):  # noqa: F811
    doc = _bvi_doc(doc_id="clx0capbrand1")  # LUXURY, offer 4% off -- over Cartier's 2%
    catalog_mod.CATALOG_PRODUCTS[doc["id"]] = doc
    with pytest.raises(HTTPException) as exc:
        _catalog_put(doc["id"], {"attributes": {"brand_name": "Cartier"}})
    assert exc.value.status_code == 400 and REFUSED in str(exc.value.detail)
    assert catalog_mod.CATALOG_PRODUCTS[doc["id"]]["brand"] == "Vogue"


def test_a_blank_brand_name_with_a_legacy_brand_key_is_a_brand_change(db):  # noqa: F811
    """Round-4 review: {brand_name: "", brand: "Cartier"} -- the save derives
    the brand from the legacy `brand` key when brand_name is blank, so the
    check must read the brand the SAME way (_derive_brand_model_color_size)."""
    from database.repositories.product_repository import ProductRepository

    solo = _family_create(db, {"brand_name": "Ray-Ban", "model_no": "RB3447", "colour_code": "001"},
                          offer_price=4400)
    sneaky = {**solo["attributes"], "brand_name": "", "brand": "Cartier"}
    with pytest.raises(HTTPException) as exc:
        _family_put(solo["product_id"], attributes=sneaky)
    assert exc.value.status_code == 400 and REFUSED in str(exc.value.detail)
    with pytest.raises(pm.ProductMasterError):
        pm.update_product(product_id=solo["product_id"], patch={"attributes": sneaky},
                          actor="u1", product_repo=ProductRepository(db["products"]), db=_Conn(db))
    assert _p(db, solo["product_id"])["brand"] == "Ray-Ban"


def test_moving_a_discounted_frame_to_hearing_aid_is_refused(db):  # noqa: F811
    """A hearing aid is NON_DISCOUNTABLE (0%) whatever tier the row stored:
    the category's forced tier is the cap the moved product is judged on."""
    from database.repositories.product_repository import ProductRepository

    frame = _family_create(db, {"brand_name": "Ray-Ban", "model_no": "RB5154", "colour_code": "2000",
                                "lens_size": "49"}, category="FRAME", offer_price=4400)
    with pytest.raises(pm.ProductMasterError) as exc:
        pm.update_product(product_id=frame["product_id"], patch={"category": "HEARING_AID"},
                          actor="u1", product_repo=ProductRepository(db["products"]), db=_Conn(db))
    assert exc.value.status == 400 and "Non_Discountable" in exc.value.message
    assert _p(db, frame["product_id"])["category"] == "FRAME"
