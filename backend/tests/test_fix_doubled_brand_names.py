"""The doubled-brand name backfill: dry run writes nothing, --apply renames
only old-formula names (spine + twin), --revert restores them."""
import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mongomock  # noqa: E402

from scripts.fix_doubled_brand_names import apply, plan, revert  # noqa: E402

OLD = "Carrera Carrera 3044/S Sunglasses - Black"
NEW = "Carrera 3044/S Sunglasses - Black"


def _db():
    db = mongomock.MongoClient().db
    attrs = {"brand_name": "Carrera", "model_no": "Carrera 3044/S", "colour_code": "Black"}
    db.products.insert_many([
        {"product_id": "P1", "sku": "S1", "category": "SUNGLASSES", "brand": "Carrera",
         "model": "Carrera 3044/S", "name": OLD, "attributes": dict(attrs), "identity_key": "k1"},
        # a typed name that is not the old formula: untouched
        {"product_id": "P2", "sku": "S2", "category": "SUNGLASSES", "brand": "Carrera",
         "model": "Carrera 3044/S", "name": "My special Carrera", "attributes": dict(attrs)},
        # already clean: untouched
        {"product_id": "P3", "sku": "S3", "category": "SUNGLASSES", "brand": "Carrera",
         "model": "Carrera 333/S", "name": "Carrera 333/S Sunglasses - Black",
         "attributes": dict(attrs, model_no="Carrera 333/S")},
    ])
    db.catalog_products.insert_one({"id": "P1", "name": OLD, "title": OLD})
    return db


def test_dry_run_lists_only_the_old_formula_rows_and_writes_nothing():
    db = _db()
    out = plan(db)
    assert [(r["sku"], r["old"], r["new"]) for r in out["rows"]] == [("S1", OLD, NEW)]
    assert out["typed"] == 1
    assert db.products.find_one({"product_id": "P1"})["name"] == OLD
    assert db.doubled_brand_name_backups.count_documents({}) == 0


def test_apply_renames_spine_and_twin_then_revert_restores():
    db = _db()
    assert apply(db, plan(db)["rows"], "run1") == 1
    assert db.products.find_one({"product_id": "P1"})["name"] == NEW
    twin = db.catalog_products.find_one({"id": "P1"})
    assert (twin["name"], twin["title"]) == (NEW, NEW)
    assert db.products.find_one({"product_id": "P2"})["name"] == "My special Carrera"
    assert db.products.find_one({"product_id": "P1"})["sku"] == "S1"
    assert plan(db)["rows"] == []  # idempotent
    assert revert(db, "run1") == 1
    assert db.products.find_one({"product_id": "P1"})["name"] == OLD
    assert db.catalog_products.find_one({"id": "P1"})["title"] == OLD


def test_revert_never_overwrites_a_later_edit():
    db = _db()
    apply(db, plan(db)["rows"], "run2")
    db.products.update_one({"product_id": "P1"}, {"$set": {"name": "Renamed later"}})
    assert revert(db, "run2") == 0
    assert db.products.find_one({"product_id": "P1"})["name"] == "Renamed later"
