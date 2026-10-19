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
    assert out["other"] == 2  # P2 typed, P3 already clean
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


_CL = {"brand_name": "Acuvue", "model_name": "Oasys", "power": "-2.00",
       "base_curve": "8.4", "diameter": "14.0", "pack": "6"}


def _cl(pid, model_name, name):
    return {"product_id": pid, "sku": pid, "category": "CL", "brand": "Acuvue", "model": model_name,
            "name": name, "attributes": dict(_CL, model_name=model_name)}


def test_a_contact_lens_with_no_doubled_brand_is_never_planned():
    from api.services.product_master import minted_name

    db = mongomock.MongoClient().db
    clean = _cl("C1", "Oasys", "")
    clean["name"] = minted_name(clean)  # made today, power and fit in the name
    main_named = _cl("C2", "Oasys", "Acuvue Oasys Contact Lenses")  # main's door: no power yet
    db.products.insert_many([clean, main_named])
    assert plan(db)["rows"] == []


def test_a_contact_lens_whose_model_starts_with_its_brand_loses_only_the_duplicate():
    from api.services.product_master import minted_name

    db = mongomock.MongoClient().db
    doubled = _cl("C3", "Acuvue Oasys", "")
    doubled["name"] = minted_name(doubled, legacy=True)  # power + fit, brand twice
    main_doubled = _cl("C4", "Acuvue Oasys", "Acuvue Acuvue Oasys Contact Lenses")
    db.products.insert_many([doubled, main_doubled])
    assert "Acuvue Acuvue" in doubled["name"]
    got = {r["sku"]: (r["old"], r["new"]) for r in plan(db)["rows"]}
    assert got["C3"] == (doubled["name"], doubled["name"].replace("Acuvue Acuvue", "Acuvue", 1))
    assert "Oasys" in got["C3"][1] and "-2.00" in got["C3"][1]
    assert got["C4"] == ("Acuvue Acuvue Oasys Contact Lenses", "Acuvue Oasys Contact Lenses")
