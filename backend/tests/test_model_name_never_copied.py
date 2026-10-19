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



# Review probes (#1243): the home field is filled when IT is blank, whatever
# else the row carries -- main's behaviour for these, which a stricter
# "every model field blank" guard broke (a 422 on create, false DRAFT gaps).
def test_frame_with_only_a_model_name_gets_its_model_no_from_the_flat_model():
    out = pm.normalise_door_payload(
        {"category": "FRAME", "model": "RB2140", "attributes": {"model_name": "Wayfarer"}},
        source="FORM",
    )
    assert (out["attributes"]["model_no"], out["attributes"]["model_name"]) == ("RB2140", "Wayfarer")


def test_smartglass_with_only_a_model_no_gets_its_model_name_from_the_flat_model():
    out = pm.normalise_door_payload(
        {"category": "SMARTGLASSES", "model": "Wayfarer", "attributes": {"model_no": "RW4006"}},
        source="FORM",
    )
    assert (out["attributes"]["model_no"], out["attributes"]["model_name"]) == ("RW4006", "Wayfarer")


@pytest.mark.parametrize(
    "category,attrs,home",
    [
        ("SMARTGLASSES", {"brand_name": "Ray-Ban Meta", "model_no": "RW4006", "colour_code": "601"}, "model_name"),
        ("FRAME", {"brand_name": "Ray-Ban", "model_name": "Wayfarer", "colour_code": "901"}, "model_no"),
        ("CONTACT_LENS", {"brand_name": "Acuvue", "model_no": "Oasys", "power": "-2.00",
                          "expiry_date": "2027-01-01"}, "model_name"),
    ],
)
def test_legacy_rows_with_a_top_level_model_read_complete(category, attrs, home):
    doc = {"category": category, "model": "M1", "attributes": attrs, "mrp": 1000.0,
           "offer_price": 1000.0, "cost_price": 400.0, "hsn_code": "900410", "gst_rate": 5.0}
    assert pm._overlay_attributes(doc)[home] == "M1"
    _status, gaps = pm.compute_catalog_status(doc)
    assert not {"model_no", "model_name"} & set(gaps), gaps

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
    db.catalog_products.insert_many([
        # P1's twin, live on Shopify: blanked and queued for the next sync
        {"id": "T1", "attributes": {"model_no": "S3110", "model_name": " s3110"},
         "ecom": {"shopify_product_id": "gid://shopify/Product/1"}},
        # P2's twin holds a copy its product does not: blanked, not queued (never published)
        {"id": "P2", "sku": "FR-2", "category": "FRAME",
         "attributes": {"model_no": "RB2140", "model_name": "RB2140"}, "ecom": {}},
        # a twin with no product row at all
        {"id": "T9", "sku": "FR-9", "category": "FRAME", "attributes": {"model_no": "Z9", "model_name": "z9"}},
    ])
    return db


def test_repair_dry_run_lists_only_copies_and_writes_nothing():
    mod, db = _script(), _repair_db()
    before = list(db.products.find({}, {"_id": 0}))
    rows = mod.plan(db)
    assert [(r["kind"], r["sku"]) for r in rows] == [
        ("product", "SG-IDEE-S3110-C2-57"), ("twin", "FR-2"), ("twin", "FR-9")]
    assert list(db.products.find({}, {"_id": 0})) == before
    assert db[mod.BACKUPS].count_documents({}) == 0


def test_repair_apply_blanks_spine_and_twin_and_revert_restores():
    mod, db = _script(), _repair_db()
    assert mod.apply(db, mod.plan(db), "run1") == 3
    assert "model_name" not in db.products.find_one({"product_id": "P1"})["attributes"]
    t1 = db.catalog_products.find_one({"id": "T1"})
    assert "model_name" not in t1["attributes"] and t1["ecom"]["locally_modified"] is True
    p2twin = db.catalog_products.find_one({"id": "P2"})
    assert "model_name" not in p2twin["attributes"] and "locally_modified" not in p2twin["ecom"]
    assert "model_name" not in db.catalog_products.find_one({"id": "T9"})["attributes"]
    assert db.products.find_one({"product_id": "P2"})["attributes"]["model_name"] == "Wayfarer"
    assert db.products.find_one({"product_id": "P3"})["attributes"]["model_name"] == "Oasys"
    assert mod.plan(db) == []
    assert mod.revert(db, "run1") == 3
    assert db.products.find_one({"product_id": "P1"})["attributes"]["model_name"] == " s3110"
    assert db.catalog_products.find_one({"id": "T1"})["attributes"]["model_name"] == " s3110"
    assert db.catalog_products.find_one({"id": "T9"})["attributes"]["model_name"] == "z9"
    assert db.products.find_one({"product_id": "P2"})["attributes"]["model_name"] == "Wayfarer"


def test_repair_keeps_no_backup_for_a_row_it_did_not_write():
    mod, db = _script(), _repair_db()
    rows = mod.plan(db)
    # staff typed a real name on P1 (and its twin) between the dry run and --apply
    db.products.update_one({"product_id": "P1"}, {"$set": {"attributes.model_name": "Pilot"}})
    db.catalog_products.update_one({"id": "T1"}, {"$set": {"attributes.model_name": "Pilot"}})
    assert mod.apply(db, rows, "run2") == 2
    assert db[mod.BACKUPS].count_documents({"run_id": "run2", "sku": "SG-IDEE-S3110-C2-57"}) == 0
    db.products.update_one({"product_id": "P1"}, {"$unset": {"attributes.model_name": ""}})
    mod.revert(db, "run2")
    assert "model_name" not in db.products.find_one({"product_id": "P1"})["attributes"]
