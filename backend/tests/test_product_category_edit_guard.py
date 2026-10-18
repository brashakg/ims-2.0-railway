"""The direct product edit API enforces the existing editor category lock."""

import asyncio
import copy

import pytest
from fastapi import HTTPException

from api.routers import products
from test_catalog_flags_archive import ADMIN, env


def edit(**patch):
    return asyncio.run(products.update_product(
        "P1", products.ProductUpdate(**patch), current_user=ADMIN,
    ))


@pytest.mark.parametrize("state", [
    {},  # Legacy products without a catalogue status are already finished.
    {"catalog_status": "ACTIVE"},
    {"catalog_status": "ACTIVE", "provisional": True},
    {"catalog_status": "DRAFT", "provisional": False},
    {"catalog_status": "ACTIVE", "is_active": False},
])
def test_finished_category_change_refused_before_any_write(env, state):
    db = env["db"]
    db.products.update_one({"product_id": "P1"}, {"$set": state})
    before = {name: copy.deepcopy(list(db[name].find())) for name in (
        "products", "catalog_products", "stock_units", "audit_logs",
    )}
    with pytest.raises(HTTPException) as exc:
        edit(category="FR", mrp=6500)
    assert exc.value.status_code == 409
    assert "category" in str(exc.value.detail).lower()
    for name, rows in before.items():
        assert list(db[name].find()) == rows, name


@pytest.mark.parametrize("patch", [{"category": "SG", "mrp": 6500}, {"mrp": 6500}])
def test_same_canonical_category_and_other_edits_remain_allowed(env, patch):
    edit(**patch)
    stored = env["db"].products.find_one({"product_id": "P1"})
    assert stored["category"] == "SUNGLASS"
    assert stored["mrp"] == 6500


@pytest.mark.parametrize("category", [None, "", "UNKNOWN_CATEGORY"])
def test_explicit_invalid_category_still_rejected_as_validation_error(env, category):
    with pytest.raises(HTTPException) as exc:
        edit(category=category)
    assert exc.value.status_code == 422
    assert env["db"].products.find_one({"product_id": "P1"})["category"] == "SUNGLASS"
