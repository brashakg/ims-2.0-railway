"""A05: catalogue creation cannot invent a twin after its spine is refused."""

import asyncio
from copy import deepcopy

import mongomock
import pytest
from fastapi import HTTPException

from api import dependencies
from api.routers import catalog
from api.services import product_master as pm
from database.repositories.product_repository import ProductRepository


ACTOR = {"user_id": "cataloguer", "roles": ["CATALOG_MANAGER"]}
ATTRS = {
    "brand_name": "Ray-Ban", "model_no": "RB4350",
    "colour_code": "002/20", "lens_size": "52",
}


@pytest.fixture
def store(monkeypatch):
    db = mongomock.MongoClient().db
    db.products.create_index("sku", unique=True)
    db.products.create_index("identity_key", unique=True, sparse=True)
    repo = ProductRepository(db.products)
    monkeypatch.setattr(catalog, "_get_db", lambda: db)
    monkeypatch.setattr(dependencies, "get_product_repository", lambda: repo)
    monkeypatch.setattr("api.services.online_discount_engine.recompute_online_price", lambda *a, **kw: None)
    return db, repo


def create(**attrs):
    body = catalog.ProductCreateInput(
        category="FR", attributes={**ATTRS, **attrs},
        pricing={"mrp": 4000, "offer_price": 3600},
    )
    return asyncio.run(catalog.create_catalog_product(body, current_user=ACTOR))["product"]


def test_same_identity_without_gtin_is_refused_before_a_second_twin(store):
    db, _repo = store
    first = create()
    with pytest.raises(HTTPException) as raised:
        create(brand_name="RAY BAN", model_no="0RB 4350", colour_code="002-20")
    assert raised.value.status_code == 409
    assert raised.value.detail["existing"]["product_id"] == first["id"]
    assert raised.value.detail["existing"]["sku"] == first["sku"]
    assert db.products.count_documents({}) == 1
    assert db.catalog_products.count_documents({}) == 1
    assert db.products.find_one({"product_id": first["id"]})


def test_form_created_identity_is_also_refused_without_catalogue_orphan(store):
    db, repo = store
    first = pm.create_via_door(
        {"category": "FR", "attributes": ATTRS, "mrp": 4000, "offer_price": 3600},
        source="FORM", actor="cataloguer", product_repo=repo, db=db,
    )
    before = list(db.catalog_products.find({}))
    with pytest.raises(HTTPException) as raised:
        create()
    assert raised.value.status_code == 409
    assert raised.value.detail["existing"]["product_id"] == first["product_id"]
    assert list(db.catalog_products.find({})) == before
    assert db.products.count_documents({}) == 1


def test_unique_index_race_refuses_and_names_actual_winner(store, monkeypatch):
    db, repo = store
    real_create = repo.create

    def concurrent_winner(doc, **kwargs):
        winner = deepcopy(doc)
        winner.update(product_id="winner-id", id="winner-id", sku="OTHER-SKU")
        db.products.insert_one(winner)
        return real_create(doc, **kwargs)

    monkeypatch.setattr(repo, "create", concurrent_winner)
    with pytest.raises(HTTPException) as raised:
        create()
    assert raised.value.status_code == 409
    assert raised.value.detail["existing"]["product_id"] == "winner-id"
    assert db.products.count_documents({}) == 1
    assert db.catalog_products.count_documents({}) == 0


def test_real_colour_and_eye_size_variants_keep_distinct_billable_records(store):
    db, _repo = store
    products = [create(), create(colour_code="003/20"), create(lens_size="54")]
    assert len({p["id"] for p in products}) == 3
    assert db.products.count_documents({}) == 3
    assert db.catalog_products.count_documents({}) == 3
    assert all(db.products.find_one({"product_id": p["id"]}) for p in products)


def test_maker_gtin_remains_one_product_even_with_different_identity(store):
    db, _repo = store
    create(gtin="4006381333931")
    with pytest.raises(HTTPException) as raised:
        create(model_no="RB9999", gtin="4006381333931")
    assert raised.value.status_code == 409
    assert db.products.count_documents({}) == 1
    assert db.catalog_products.count_documents({}) == 1
