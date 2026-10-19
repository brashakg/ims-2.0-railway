"""
A frame's / sunglass's Model Name is what staff typed -- never a copy of its
Model Number (owner 2026-10-19: 583 of 603 prod Model Names equalled the
number; staff typed none of them).

The copy came from the server, not the form: the create door folded the
form's flat `model` (= its Model No) into EVERY model field the category
shows (normalise_door_payload), and the clone overlay (_overlay_attributes,
"add another colour" / clone_and_vary) filled both model_no and model_name
from the doc's top-level model. Pinned here: create, edit and clone each keep
a typed Model Name and keep a blank one blank; a category keyed on Model Name
(contact lens) still gets it from a flat model.

Run: JWT_SECRET_KEY=test ENVIRONMENT=test python -m pytest backend/tests/test_model_name_never_copied.py -q
"""

from __future__ import annotations

import os
import sys

import pytest

os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

mongomock = pytest.importorskip("mongomock")

from api.services import product_master as pm  # noqa: E402
from database.repositories.product_repository import ProductRepository  # noqa: E402


@pytest.fixture
def repo():
    return ProductRepository(mongomock.MongoClient()["mname_test"]["products"])


def _form_create(repo, model_name, colour="C2"):
    """What the Add-Product form posts for a sunglass: attributes as typed and
    a flat `model` = its Model No (formModel.buildProductPayload / modelOf)."""
    attrs = {"brand_name": "Idee", "model_no": "S3110", "colour_code": colour, "lens_size": "57"}
    if model_name is not None:
        attrs["model_name"] = model_name
    return pm.create_via_door(
        {
            "category": "SUNGLASS",
            "brand": "Idee",
            "model": "S3110",
            "attributes": attrs,
            "mrp": 2000.0,
            "offer_price": 2000.0,
        },
        source="FORM",
        actor="u-admin",
        product_repo=repo,
    )


@pytest.mark.parametrize("typed", [None, ""])
def test_create_keeps_a_blank_model_name_blank(repo, typed):
    doc = _form_create(repo, typed)
    assert doc["attributes"]["model_no"] == "S3110"
    assert not doc["attributes"].get("model_name")
    assert not repo.find_by_id(doc["product_id"])["attributes"].get("model_name")


def test_create_keeps_a_typed_model_name(repo):
    doc = _form_create(repo, "Aviator Classic")
    assert doc["attributes"]["model_name"] == "Aviator Classic"


def test_edit_keeps_typed_and_blank_model_name(repo):
    doc = _form_create(repo, None)
    pid = doc["product_id"]
    out = pm.update_product(
        product_id=pid, patch={"model": "S3110", "attributes": {"model_name": ""}},
        actor="u-admin", product_repo=repo,
    )
    assert not out["attributes"].get("model_name")
    out = pm.update_product(
        product_id=pid, patch={"model": "S3110", "attributes": {"model_name": "Pilot"}},
        actor="u-admin", product_repo=repo,
    )
    assert out["attributes"]["model_name"] == "Pilot"


@pytest.mark.parametrize("typed", [None, "Aviator Classic"])
def test_clone_another_colour_keeps_the_model_name_as_it_was(repo, typed):
    src = _form_create(repo, typed)
    res = pm.clone_and_vary(
        source_id=src["product_id"], variations=[{"colour_code": "C9"}],
        actor="u-admin", product_repo=repo,
    )
    assert not res["errors"], res["errors"]
    attrs = res["created"][0]["attributes"]
    assert attrs["model_no"] == "S3110"
    assert attrs.get("model_name") == typed


def test_legacy_row_with_only_a_top_level_model_overlays_one_key():
    frame = pm._overlay_attributes({"category": "FRAME", "model": "RB2140", "attributes": {}})
    assert frame["model_no"] == "RB2140" and "model_name" not in frame
    cl = pm._overlay_attributes({"category": "CONTACT_LENS", "model": "Oasys", "attributes": {}})
    assert cl["model_name"] == "Oasys" and "model_no" not in cl


def test_flat_model_still_fills_a_model_name_keyed_category():
    out = pm.normalise_door_payload(
        {"category": "SMARTGLASSES", "brand": "Ray-Ban Meta", "model": "Wayfarer", "attributes": {}},
        source="FORM",
    )
    assert out["attributes"]["model_name"] == "Wayfarer"
    assert "model_no" not in out["attributes"]


# ---------------------------------------------------------------------------
# scripts/clear_copied_model_names.py -- the data repair
# ---------------------------------------------------------------------------
def _script():
    import importlib.util

    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "scripts", "clear_copied_model_names.py",
    )
    spec = importlib.util.spec_from_file_location("mname_clear_script", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _repair_db():
    db = mongomock.MongoClient()["mname_repair"]
    db.products.insert_many([
        # copied (case/space differ): blanked, spine + twin
        {"product_id": "P1", "pim_product_id": "T1", "sku": "SG-IDEE-S3110-C2-57", "category": "SUNGLASSES",
         "attributes": {"model_no": "S3110", "model_name": " s3110"}},
        # a real typed name: untouched
        {"product_id": "P2", "sku": "FR-2", "category": "FRAME",
         "attributes": {"model_no": "RB2140", "model_name": "Wayfarer"}},
        # contact lens: Model Name is its required key -- never touched
        {"product_id": "P3", "sku": "CL-3", "category": "CONTACT_LENS",
         "attributes": {"model_no": "Oasys", "model_name": "Oasys"}},
        # blank already
        {"product_id": "P4", "sku": "FR-4", "category": "FRAME", "attributes": {"model_no": "X1"}},
    ])
    db.catalog_products.insert_one({"id": "T1", "attributes": {"model_no": "S3110", "model_name": " s3110"}})
    return db


def test_repair_dry_run_lists_only_copies_and_writes_nothing():
    mod, db = _script(), _repair_db()
    before = list(db.products.find({}, {"_id": 0}))
    rows = mod.plan(db)
    assert [r["sku"] for r in rows] == ["SG-IDEE-S3110-C2-57"]
    assert list(db.products.find({}, {"_id": 0})) == before
    assert db[mod.BACKUPS].count_documents({}) == 0


def test_repair_apply_blanks_spine_and_twin_and_revert_restores():
    mod, db = _script(), _repair_db()
    assert mod.apply(db, mod.plan(db), "run1") == 1
    assert "model_name" not in db.products.find_one({"product_id": "P1"})["attributes"]
    assert "model_name" not in db.catalog_products.find_one({"id": "T1"})["attributes"]
    assert db.products.find_one({"product_id": "P2"})["attributes"]["model_name"] == "Wayfarer"
    assert db.products.find_one({"product_id": "P3"})["attributes"]["model_name"] == "Oasys"
    assert mod.plan(db) == []
    assert mod.revert(db, "run1") == 1
    assert db.products.find_one({"product_id": "P1"})["attributes"]["model_name"] == " s3110"
    assert db.catalog_products.find_one({"id": "T1"})["attributes"]["model_name"] == " s3110"
