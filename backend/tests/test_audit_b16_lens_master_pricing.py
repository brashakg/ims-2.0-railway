"""
Audit batch b16 (2026-11-04 audit) - backend halves.

L1-F37: clearing an online discount rule's brand / sub-brand (explicit null in
the PUT) reported 200 but kept the old scope, because the update dropped every
None before normalising. An OMITTED field must still be left alone.
L1-F08: the lens coating / add-on UPDATE models accepted a negative price
(only create had ge=0).
L1-F07 (safeguard): a lens brand / index that pricing rows still point at
cannot be deleted out from under them.

Calls the route functions directly against mongomock (no network).
"""
from __future__ import annotations

import asyncio
import os
import sys

import mongomock
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")

from api.routers import admin_catalog  # noqa: E402
from api.routers import online_store_discount_rules as rules  # noqa: E402

USER = {"user_id": "u-cat", "roles": ["CATALOG_MANAGER"]}


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def rdb(monkeypatch):
    db = mongomock.MongoClient()["b16_rules"]
    recomputed = []
    monkeypatch.setattr(rules, "_get_db", lambda: db)
    monkeypatch.setattr(
        rules, "_trigger_recompute", lambda _db, cat: recomputed.append(cat) or {"ok": True, "products": 1}
    )
    db.recomputed = recomputed
    return db


def _make_rule(brand="audit brand", sub_brand="audit sub", category="FRAME"):
    body = rules.RuleCreate(category=category, brand=brand, sub_brand=sub_brand, discount_percentage=10)
    return run(rules.create_rule(body, current_user=USER))["rule"]


def _put(rule_id, payload):
    return run(rules.update_rule(rule_id, rules.RuleUpdate.model_validate(payload), current_user=USER))["rule"]


def test_clearing_both_brand_and_sub_brand_broadens_to_category(rdb):
    r = _make_rule()
    out = _put(r["rule_id"], {"brand": None, "sub_brand": None, "discount_percentage": 10})
    stored = rdb[rules.RULES_COLLECTION].find_one({"rule_id": r["rule_id"]})
    assert (out["brand"], out["sub_brand"]) == ("", "")
    assert (stored["brand"], stored["sub_brand"]) == ("", "")
    assert rdb.recomputed[-1] == "FRAME"


def test_clearing_only_sub_brand_keeps_brand(rdb):
    r = _make_rule()
    out = _put(r["rule_id"], {"sub_brand": None})
    assert (out["brand"], out["sub_brand"]) == ("audit brand", "")


def test_omitted_scope_is_left_alone(rdb):
    r = _make_rule()
    out = _put(r["rule_id"], {"discount_percentage": 12.5})
    assert (out["brand"], out["sub_brand"], out["discount_percentage"]) == ("audit brand", "audit sub", 12.5)


def test_null_on_a_non_scope_field_changes_nothing(rdb):
    r = _make_rule()
    out = _put(r["rule_id"], {"discount_percentage": None, "active": None, "priority": None, "category": None})
    assert (out["discount_percentage"], out["active"], out["priority"], out["category"]) == (10.0, True, 0, "FRAME")


def test_clear_that_collides_with_an_existing_rule_is_refused_and_keeps_old_scope(rdb):
    _make_rule(brand=None, sub_brand=None)  # the FRAME/all-brands rule already exists
    r = _make_rule()
    with pytest.raises(HTTPException) as exc:
        _put(r["rule_id"], {"brand": None, "sub_brand": None})
    assert exc.value.status_code == 409
    stored = rdb[rules.RULES_COLLECTION].find_one({"rule_id": r["rule_id"]})
    assert (stored["brand"], stored["sub_brand"]) == ("audit brand", "audit sub")


@pytest.mark.parametrize("model", [admin_catalog.LensCoatingUpdate, admin_catalog.LensAddonUpdate])
@pytest.mark.parametrize("bad", [-1, -0.01, float("nan")])
def test_lens_price_update_refuses_negative_or_nan(model, bad):
    with pytest.raises(ValidationError):
        model(price=bad)


@pytest.mark.parametrize("model", [admin_catalog.LensCoatingUpdate, admin_catalog.LensAddonUpdate])
def test_lens_price_update_keeps_deliberate_zero(model):
    assert model(price=0).price == 0


_AC = admin_catalog
_FINITE_DOORS = [
    (_AC.LensCoatingCreate, {"name": "Coat", "code": "CT"}, "price"),
    (_AC.LensCoatingUpdate, {}, "price"),
    (_AC.LensAddonCreate, {"name": "Tint", "code": "TT", "type": "TINT"}, "price"),
    (_AC.LensAddonUpdate, {}, "price"),
    (_AC.LensIndexCreate, {"value": "1.67"}, "multiplier"),
    (_AC.LensIndexUpdate, {}, "multiplier"),
    (_AC.LensPricingCreate, {"brand_id": "B", "index_id": "I", "category": "SINGLE_VISION"}, "base_price"),
    (_AC.LensPricingRangeCreate, {"brand_id": "B", "index_id": "I", "category": "SINGLE_VISION",
                                  "parameter": "sphere", "min_value": 0, "max_value": 2, "base_price": 1}, "base_price"),
    (_AC.LensPricingRangeUpdate, {}, "base_price"),
    (_AC.LensPricingRangeUpdate, {}, "max_value"),
]


@pytest.mark.parametrize("model,base,field", _FINITE_DOORS)
@pytest.mark.parametrize("bad", ["Infinity", float("inf"), "NaN"])
def test_lens_master_numbers_refuse_non_finite(model, base, field, bad):
    """Codex PR-1429 round 1: price "Infinity" passed ge=0, was stored, and
    then GET of the whole coatings / add-ons list returned 500."""
    with pytest.raises(ValidationError):
        model(**{**base, field: bad})


def test_infinite_price_put_is_422_and_the_list_still_loads(monkeypatch):
    """Through the real route: refused before any write, the list stays 200."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    db = mongomock.MongoClient()["b16_inf"]
    db.lens_coating_masters.insert_one({"coating_id": "C1", "name": "Coat", "code": "CT", "price": 500.0})
    monkeypatch.setattr(admin_catalog, "_coll", lambda name: db[name])
    app = FastAPI()
    app.include_router(admin_catalog.router)
    app.dependency_overrides[admin_catalog._require_admin_role] = lambda: USER  # pylint: disable=protected-access
    client = TestClient(app)
    path = next(r.path for r in admin_catalog.router.routes if r.path.endswith("/lens/coatings/{coating_id}"))
    r = client.put(path.replace("{coating_id}", "C1"), json={"price": "Infinity"})
    assert r.status_code == 422
    assert db.lens_coating_masters.find_one({"coating_id": "C1"})["price"] == 500.0
    assert client.get(path.rsplit("/", 1)[0]).status_code == 200


@pytest.fixture
def ldb(monkeypatch):
    db = mongomock.MongoClient()["b16_lens"]
    monkeypatch.setattr(admin_catalog, "_coll", lambda name: db[name])
    return db


@pytest.mark.parametrize(
    "kind,coll,id_field,ref_coll,ref",
    [
        ("brand", "lens_brand_masters", "brand_id", "lens_pricing_ranges", {"brand_id": "X1", "is_active": True}),
        ("index", "lens_index_masters", "index_id", "lens_pricing_ranges", {"index_id": "X1", "is_active": True}),
    ],
)
def test_lens_delete_refused_while_prices_point_at_it(ldb, kind, coll, id_field, ref_coll, ref):
    ldb[coll].insert_one({id_field: "X1", "name": "Ref"})
    ldb[ref_coll].insert_one(dict(ref))
    fn = admin_catalog.delete_lens_brand if kind == "brand" else admin_catalog.delete_lens_index
    with pytest.raises(HTTPException) as exc:
        run(fn("X1"))
    assert exc.value.status_code == 409
    assert "Lens range pricing" in exc.value.detail  # names a screen staff can act on
    assert ldb[coll].count_documents({id_field: "X1"}) == 1


@pytest.mark.parametrize(
    "kind,coll,id_field,ref",
    [
        ("brand", "lens_brand_masters", "brand_id", {"brandId": "X1", "indexId": "I", "basePrice": 900}),
        ("index", "lens_index_masters", "index_id", {"brandId": "B", "indexId": "X1", "basePrice": 900}),
    ],
)
def test_legacy_matrix_row_does_not_block_delete_and_is_kept(ldb, kind, coll, id_field, ref):
    """Review must-fix: no screen or route can remove a legacy matrix row, so it
    must not block the delete (a 409 staff cannot act on). The row itself is
    left untouched - stored price history is never rewritten."""
    ldb[coll].insert_one({id_field: "X1", "name": "Ref"})
    ldb["lens_pricing_masters"].insert_one(dict(ref))
    fn = admin_catalog.delete_lens_brand if kind == "brand" else admin_catalog.delete_lens_index
    run(fn("X1"))
    assert ldb[coll].count_documents({id_field: "X1"}) == 0
    assert ldb["lens_pricing_masters"].count_documents({}) == 1


def test_lens_delete_unreferenced_removes_only_that_row(ldb):
    ldb["lens_brand_masters"].insert_many([{"brand_id": "A"}, {"brand_id": "B"}])
    ldb["lens_pricing_ranges"].insert_one({"brand_id": "B", "is_active": False})  # retired range
    run(admin_catalog.delete_lens_brand("A"))
    assert sorted(d["brand_id"] for d in ldb["lens_brand_masters"].find()) == ["B"]
