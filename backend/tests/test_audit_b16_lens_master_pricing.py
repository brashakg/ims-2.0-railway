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


@pytest.fixture
def ldb(monkeypatch):
    db = mongomock.MongoClient()["b16_lens"]
    monkeypatch.setattr(admin_catalog, "_coll", lambda name: db[name])
    return db


@pytest.mark.parametrize(
    "kind,coll,id_field,ref_coll,ref",
    [
        ("brand", "lens_brand_masters", "brand_id", "lens_pricing_masters", {"brandId": "X1"}),
        ("brand", "lens_brand_masters", "brand_id", "lens_pricing_ranges", {"brand_id": "X1", "is_active": True}),
        ("index", "lens_index_masters", "index_id", "lens_pricing_masters", {"indexId": "X1"}),
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
    assert ldb[coll].count_documents({id_field: "X1"}) == 1


def test_lens_delete_unreferenced_removes_only_that_row(ldb):
    ldb["lens_brand_masters"].insert_many([{"brand_id": "A"}, {"brand_id": "B"}])
    ldb["lens_pricing_ranges"].insert_one({"brand_id": "B", "is_active": False})  # retired range
    run(admin_catalog.delete_lens_brand("A"))
    assert sorted(d["brand_id"] for d in ldb["lens_brand_masters"].find()) == ["B"]
