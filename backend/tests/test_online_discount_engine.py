"""
Tests for the Online Discount-Rule engine (rebuild of BVI DiscountRule; DARK).

Covers (per the build spec):
  A. Rule specificity resolution -- all three levels + priority tie-break + active.
  B. Price compute -- MRP*(1-pct) math + rounding; never-below-cost clamp + flag;
     manual-offer override wins; offer<=MRP always.
  C. recompute_online_price -- writes the online fields (variant discounted_price/
     compare_at_price + ecom.online_*), is FAIL-SOFT (a bad product never raises),
     and NEVER touches the in-store offer_price.
  D. recompute_all across the catalog.
  E. Migration -- map_discount_rule + natural_key + idempotent upsert.
  F. Rule CRUD RBAC -- SALES_STAFF (and other non-allowed roles) are 403.

Real-mongo-safe: the DB-backed tests use the in-memory MockCollection; no global
is monkeypatched (the engine is pure + takes db by argument). No emojis.

Run: JWT_SECRET_KEY=test python -m pytest backend/tests/test_online_discount_engine.py -q
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts"))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

import pytest  # noqa: E402

from database.connection import MockCollection  # noqa: E402
from api.services import online_discount_engine as engine  # noqa: E402
from api.services import rbac_policy as rbac  # noqa: E402


# ===========================================================================
# Helpers
# ===========================================================================


class _EngineDB:
    """A minimal in-memory db the engine can use directly (db["x"] subscript)."""

    def __init__(self):
        self._colls = {}

    def __getitem__(self, name):
        return self._colls.setdefault(name, MockCollection(name))


def _rules():
    return [
        {"id": "cat", "category": "SUNGLASS", "discount_percentage": 10, "active": True},
        {"id": "brand", "category": "SUNGLASS", "brand": "Ray-Ban", "discount_percentage": 20, "active": True},
        {"id": "sub", "category": "SUNGLASS", "brand": "Ray-Ban", "sub_brand": "Aviator", "discount_percentage": 25, "active": True},
    ]


# ===========================================================================
# A. Rule specificity + priority tie-break
# ===========================================================================


def test_specificity_sub_brand_wins():
    r = engine.find_matching_rule(_rules(), "SUNGLASS", "Ray-Ban", "Aviator")
    assert r["id"] == "sub"


def test_specificity_brand_level():
    r = engine.find_matching_rule(_rules(), "SUNGLASS", "Ray-Ban", "Wayfarer")
    assert r["id"] == "brand"


def test_specificity_category_only():
    r = engine.find_matching_rule(_rules(), "SUNGLASS", "Gucci", None)
    assert r["id"] == "cat"


def test_no_match_returns_none():
    assert engine.find_matching_rule(_rules(), "WATCH", "Ray-Ban", None) is None


def test_category_alias_matches():
    # BVI enum SUNGLASSES resolves to IMS SUNGLASS -> still matches.
    r = engine.find_matching_rule(_rules(), "SUNGLASSES", "Ray-Ban", None)
    assert r["id"] == "brand"


def test_priority_tie_break_within_level():
    rules = [
        {"id": "b_lo", "category": "FRAME", "brand": "Boss", "discount_percentage": 10, "active": True, "priority": 1},
        {"id": "b_hi", "category": "FRAME", "brand": "Boss", "discount_percentage": 5, "active": True, "priority": 9},
    ]
    r = engine.find_matching_rule(rules, "FRAME", "Boss", None)
    assert r["id"] == "b_hi"  # higher priority wins even with a lower discount


def test_inactive_rule_ignored():
    rules = [
        {"id": "off", "category": "FRAME", "brand": "Boss", "discount_percentage": 30, "active": False},
        {"id": "on", "category": "FRAME", "discount_percentage": 10, "active": True},
    ]
    r = engine.find_matching_rule(rules, "FRAME", "Boss", None)
    assert r["id"] == "on"  # the disabled brand rule is skipped; category-only wins


def test_brand_rule_with_subbrand_does_not_match_at_brand_level():
    # A rule that carries a sub_brand must NOT match a product without that
    # sub_brand (BVI parity: a rule matches only at its own specificity level).
    rules = [{"id": "sub", "category": "FRAME", "brand": "Boss", "sub_brand": "Orange", "discount_percentage": 20, "active": True}]
    assert engine.find_matching_rule(rules, "FRAME", "Boss", None) is None


# ===========================================================================
# B. Price compute -- math, rounding, clamps, manual override
# ===========================================================================


def test_compute_basic_math_and_rounding():
    r = engine.compute_online_price(999, discount_pct=15)
    # 999 * 0.85 = 849.15 -> 849.15 (2dp half-up)
    assert r["offer"] == 849.15
    assert r["compare_at"] == 999.0
    assert r["source"] == "rule"


def test_compute_below_cost_holds_at_mrp_and_does_not_expose_cost():
    # 40% off 1000 = 600, but cost is 750. cost_price is a MASKED field: we must
    # NEVER publish the exact cost as the online price (that defeats cost-masking).
    # Hold at MRP, flag COST_CONFLICT, and the online price must NOT equal cost.
    r = engine.compute_online_price(1000, discount_pct=40, cost_price=750)
    assert r["offer"] == 1000.0
    assert r["offer"] != 750.0  # the cost number is NOT surfaced as the price
    assert "COST_CONFLICT" in r["flags"]
    assert "COST_CLAMPED" not in r["flags"]  # old leaky flag is gone
    assert r["clamped"] is True


def test_compute_cost_above_mrp_caps_at_mrp():
    # Bad data: cost 1200 > MRP 1000. offer<=MRP is the hard invariant.
    r = engine.compute_online_price(1000, discount_pct=10, cost_price=1200)
    assert r["offer"] == 1000.0
    assert "COST_ABOVE_MRP" in r["flags"]


def test_compute_manual_offer_overrides_rule():
    r = engine.compute_online_price(1000, discount_pct=50, manual_offer=880, cost_price=700)
    assert r["offer"] == 880.0
    assert r["source"] == "manual"


def test_compute_manual_offer_below_cost_holds_at_mrp_not_cost():
    # A manual price below cost is refused too: held at MRP (never a loss, never a
    # cost leak) and flagged COST_CONFLICT -- the cost number is never the price.
    r = engine.compute_online_price(1000, discount_pct=0, manual_offer=500, cost_price=650)
    assert r["offer"] == 1000.0
    assert r["offer"] != 650.0
    assert "COST_CONFLICT" in r["flags"]


def test_compute_offer_never_exceeds_mrp():
    # A manual price above MRP is capped at MRP (offer <= MRP always).
    r = engine.compute_online_price(1000, manual_offer=1300)
    assert r["offer"] == 1000.0
    assert "OFFER_ABOVE_MRP" in r["flags"]


def test_compute_no_rule_no_discount():
    r = engine.compute_online_price(1000, discount_pct=0)
    assert r["offer"] == 1000.0
    assert r["source"] == "none"


def test_compute_invalid_mrp():
    r = engine.compute_online_price(0, discount_pct=20)
    assert r["offer"] == 0.0 and "INVALID_MRP" in r["flags"]


def test_compute_exceeds_instore_cap_is_advisory_not_enforced():
    # 20% off a LUXURY/Gucci product exceeds the 5% in-store cap. The engine
    # still APPLIES the 20% online (owner formula) but FLAGS it advisorily.
    r = engine.compute_online_price(1000, discount_pct=20, discount_category="LUXURY", brand="Gucci")
    assert r["offer"] == 800.0
    assert "EXCEEDS_INSTORE_CAP" in r["flags"]


# ===========================================================================
# C. recompute_online_price -- writes online fields, fail-soft, online-only
# ===========================================================================


def test_recompute_writes_variant_and_ecom_online_fields():
    db = _EngineDB()
    db["ecom_discount_rules"].insert_one(
        {"id": "r1", "category": "SUNGLASS", "brand": "Ray-Ban", "discount_percentage": 20, "active": True}
    )
    product = {
        "id": "P1", "category": "SUNGLASS", "brand": "Ray-Ban",
        "mrp": 1000, "offer_price": 950, "cost_price": 600,
        "attributes": {"brand_name": "Ray-Ban"},
    }
    db["catalog_products"].insert_one(dict(product))
    db["catalog_variants"].insert_one({"sku": "SG-RB-1", "parent_product_id": "P1", "mrp": 1000})

    res = engine.recompute_online_price(product, db=db)
    assert res["ok"] is True
    # Product ecom online fields written (record + no-variant fallback).
    saved = db["catalog_products"].find_one({"id": "P1"})
    assert saved["ecom"]["online_offer_price"] == 800.0
    assert saved["ecom"]["online_compare_at_price"] == 1000.0
    # IN-STORE offer_price is UNTOUCHED (online-only scope).
    assert saved["offer_price"] == 950
    # Variant online price fields written (what the push reads FIRST).
    v = db["catalog_variants"].find_one({"sku": "SG-RB-1"})
    assert v["discounted_price"] == 800.0
    assert v["compare_at_price"] == 1000.0


def test_recompute_manual_override_on_ecom_wins():
    db = _EngineDB()
    db["ecom_discount_rules"].insert_one(
        {"id": "r1", "category": "SUNGLASS", "discount_percentage": 30, "active": True}
    )
    product = {
        "id": "P2", "category": "SUNGLASS", "mrp": 1000, "cost_price": 500,
        "ecom": {"manual_online_offer_price": 899},
    }
    db["catalog_products"].insert_one(dict(product))
    res = engine.recompute_online_price(product, db=db)
    assert res["source"] == "manual"
    saved = db["catalog_products"].find_one({"id": "P2"})
    assert saved["ecom"]["online_offer_price"] == 899.0


def test_recompute_is_fail_soft_on_bad_product():
    # A product with a non-dict attributes (garbage) must NOT raise -- the save
    # proceeds. recompute returns ok=False without throwing.
    class _Boom:
        def get(self, *a, **k):
            raise RuntimeError("boom")

    res = engine.recompute_online_price(_Boom())
    assert res["ok"] is False


def test_recompute_no_mrp_is_noop_not_error():
    res = engine.recompute_online_price({"id": "P3", "category": "FRAME"})
    assert res["ok"] is True
    assert "NO_MRP" in res["flags"]


# ===========================================================================
# D. recompute_all across the catalog
# ===========================================================================


def test_recompute_all_scoped_by_category():
    db = _EngineDB()
    db["ecom_discount_rules"].insert_one(
        {"id": "r1", "category": "SUNGLASS", "discount_percentage": 10, "active": True}
    )
    db["catalog_products"].insert_one({"id": "A", "category": "SUNGLASS", "mrp": 2000})
    db["catalog_products"].insert_one({"id": "B", "category": "FRAME", "mrp": 3000})
    out = engine.recompute_all(db, {"category": "SUNGLASS"})
    assert out["ok"] is True and out["products"] == 1
    a = db["catalog_products"].find_one({"id": "A"})
    assert a["ecom"]["online_offer_price"] == 1800.0
    # FRAME product (out of scope) got no online price.
    b = db["catalog_products"].find_one({"id": "B"})
    assert "ecom" not in b or "online_offer_price" not in (b.get("ecom") or {})


# ===========================================================================
# E. Migration -- pure mapper + natural key + idempotent upsert
# ===========================================================================


def test_migration_maps_bvi_category_and_defaults():
    import migrate_bvi_discount_rules as mig

    doc = mig.map_discount_rule(
        {"id": "d1", "category": "SUNGLASSES", "brand": "Ray-Ban", "subBrand": "Aviator", "discountPercentage": 25}
    )
    assert doc["category"] == "SUNGLASS"  # BVI -> IMS canonical
    assert doc["brand"] == "ray-ban"  # normalised (lower) to match natural_key
    assert doc["sub_brand"] == "aviator"
    assert doc["discount_percentage"] == 25.0
    assert doc["active"] is True and doc["priority"] == 0  # BVI had neither col


def test_migration_natural_key_normalises():
    import migrate_bvi_discount_rules as mig

    k = mig.natural_key({"category": "sunglass", "brand": "Ray-Ban", "sub_brand": "Aviator"})
    assert k == {"category": "SUNGLASS", "brand": "ray-ban", "sub_brand": "aviator"}


class _FakeUpsertColl:
    """A tiny collection supporting update_one(filter, update, upsert=True) with
    $set + $setOnInsert -- enough to prove the migration upsert is idempotent."""

    def __init__(self):
        self.docs = []

    def _find(self, flt):
        for d in self.docs:
            if all(d.get(k) == v for k, v in flt.items()):
                return d
        return None

    def update_one(self, flt, update, upsert=False):
        doc = self._find(flt)
        if doc is None:
            if not upsert:
                return
            doc = dict(flt)
            doc.update(update.get("$setOnInsert", {}))
            self.docs.append(doc)
        doc.update(update.get("$set", {}))


def test_migration_upsert_is_idempotent():
    import migrate_bvi_discount_rules as mig

    coll = _FakeUpsertColl()
    row = {"id": "d1", "category": "SUNGLASSES", "brand": "Ray-Ban", "subBrand": None, "discountPercentage": 20}
    for _ in range(3):
        doc = mig.map_discount_rule(row)
        mig._upsert_one(coll, mig.natural_key(doc), doc)
    assert len(coll.docs) == 1  # three runs -> ONE row (idempotent)
    assert coll.docs[0]["discount_percentage"] == 20.0


# ===========================================================================
# F. Rule CRUD RBAC
# ===========================================================================


def test_rule_crud_rbac_allows_manager_roles():
    for role in ("SUPERADMIN", "ADMIN", "CATALOG_MANAGER"):
        for method, path in (
            ("GET", "/api/v1/online-store/discount-rules"),
            ("POST", "/api/v1/online-store/discount-rules"),
            ("PUT", "/api/v1/online-store/discount-rules/{rule_id}"),
            ("DELETE", "/api/v1/online-store/discount-rules/{rule_id}"),
        ):
            assert rbac.check_access(method, path, [role]) is True, (role, method, path)


def test_rule_crud_rbac_denies_sales_staff_and_others():
    for role in ("SALES_STAFF", "DESIGN_MANAGER", "ACCOUNTANT", "CASHIER", "OPTOMETRIST"):
        assert rbac.check_access("POST", "/api/v1/online-store/discount-rules", [role]) is False, role


# ===========================================================================
# G. Adversarial-panel fixes (#8-#13): preserve non-rule prices, cost-leak +
#    zero-price guards, stable migrated rule_id, dup guard, no cutover flip.
# ===========================================================================


# --- #8 preserve-on-first-touch: no-rule / empty-rules window ---------------


def test_recompute_preserves_unstamped_bvi_online_price_in_empty_rules_window():
    # BVI-migrated product: ecom.online_offer_price=4499 (the live storefront price)
    # was written by the migration, NOT the engine (no online_price_source stamp).
    # An edit before rules are migrated recomputes with EMPTY rules -> the engine
    # must PRESERVE 4499 (adopt as manual), never reset it to MRP 8999.
    db = _EngineDB()  # no discount rules loaded
    product = {
        "id": "BV1", "category": "SUNGLASS", "mrp": 8999,
        "ecom": {"online_offer_price": 4499},
    }
    db["catalog_products"].insert_one(dict(product))
    db["catalog_variants"].insert_one(
        {"sku": "V-BV1", "parent_product_id": "BV1", "mrp": 8999, "discounted_price": 4499}
    )
    res = engine.recompute_online_price(product, db=db)
    assert res["ok"] is True
    saved = db["catalog_products"].find_one({"id": "BV1"})
    assert saved["ecom"]["online_offer_price"] == 4499.0  # NOT reset to MRP
    assert saved["ecom"].get("manual_online_offer_price") == 4499.0  # seeded
    assert saved["ecom"]["online_price_source"] == "manual"
    # In-store price never invented/touched.
    assert "offer_price" not in saved or saved.get("offer_price") in (None, 0)
    v = db["catalog_variants"].find_one({"sku": "V-BV1"})
    assert v["discounted_price"] == 4499.0  # variant BVI price preserved too
    assert v.get("manual_online_offer_price") == 4499.0


def test_recompute_no_rule_does_not_price_fresh_product_to_mrp():
    # A product with NO existing online price and no matching rule must NOT get an
    # MRP "online price" stamped (that is the cutover-flip / MRP-reset bug).
    db = _EngineDB()
    product = {"id": "F1", "category": "SUNGLASS", "mrp": 5000, "offer_price": 4200}
    db["catalog_products"].insert_one(dict(product))
    res = engine.recompute_online_price(product, db=db)
    assert res["ok"] is True and res["source"] == "none"
    saved = db["catalog_products"].find_one({"id": "F1"})
    assert "online_offer_price" not in (saved.get("ecom") or {})


def test_recompute_manual_survives_a_later_rule_recompute():
    # An explicit manual online price wins over a rule and survives recompute_all.
    db = _EngineDB()
    db["ecom_discount_rules"].insert_one(
        {"id": "r", "category": "SUNGLASS", "discount_percentage": 30, "active": True}
    )
    product = {
        "id": "M1", "category": "SUNGLASS", "mrp": 1000, "cost_price": 400,
        "ecom": {"manual_online_offer_price": 880},
    }
    db["catalog_products"].insert_one(dict(product))
    res = engine.recompute_online_price(product, db=db)
    assert res["source"] == "manual"
    saved = db["catalog_products"].find_one({"id": "M1"})
    assert saved["ecom"]["online_offer_price"] == 880.0  # rule 30% (=700) did NOT win


def test_recompute_rule_owned_price_still_reverts_when_no_rule():
    # A price the ENGINE previously wrote (has a stamp) is engine-owned and DOES
    # revert to MRP when its rule is later removed -- preserve-on-first-touch only
    # protects UNstamped (hand-set) prices, not engine-written ones.
    db = _EngineDB()
    product = {
        "id": "E1", "category": "SUNGLASS", "mrp": 2000,
        "ecom": {"online_offer_price": 1500, "online_price_source": "rule"},
    }
    db["catalog_products"].insert_one(dict(product))
    res = engine.recompute_online_price(product, db=db)  # no rules now
    saved = db["catalog_products"].find_one({"id": "E1"})
    assert res["source"] == "none"
    assert saved["ecom"]["online_offer_price"] == 2000.0  # reverted to MRP


# --- #11 cost-leak guard through recompute ----------------------------------


def test_recompute_below_cost_never_writes_cost_to_online_field():
    db = _EngineDB()
    db["ecom_discount_rules"].insert_one(
        {"id": "r", "category": "ACCESSORIES", "discount_percentage": 60, "active": True}
    )
    product = {"id": "C1", "category": "ACCESSORIES", "mrp": 1000, "cost_price": 700}
    db["catalog_products"].insert_one(dict(product))
    engine.recompute_online_price(product, db=db)
    saved = db["catalog_products"].find_one({"id": "C1"})
    # 60% off = 400 < cost 700 -> held at MRP, NEVER the cost number.
    assert saved["ecom"]["online_offer_price"] == 1000.0
    assert saved["ecom"]["online_offer_price"] != 700.0
    assert "COST_CONFLICT" in saved["ecom"]["online_price_flags"]


# --- #13 zero-price guard ---------------------------------------------------


def test_compute_100pct_no_cost_refuses_zero_price():
    r = engine.compute_online_price(1000, discount_pct=100)
    assert r["offer"] == 1000.0  # held at MRP, never 0.0
    assert r["offer"] != 0.0
    assert "ZERO_PRICE_REFUSED" in r["flags"]


def test_recompute_100pct_rule_never_persists_zero():
    db = _EngineDB()
    db["ecom_discount_rules"].insert_one(
        {"id": "r", "category": "ACCESSORIES", "discount_percentage": 100, "active": True}
    )
    product = {"id": "Z1", "category": "ACCESSORIES", "mrp": 500}  # no cost data
    db["catalog_products"].insert_one(dict(product))
    db["catalog_variants"].insert_one(
        {"sku": "V-Z1", "parent_product_id": "Z1", "mrp": 500}
    )
    engine.recompute_online_price(product, db=db)
    saved = db["catalog_products"].find_one({"id": "Z1"})
    assert saved["ecom"]["online_offer_price"] == 500.0
    assert saved["ecom"]["online_offer_price"] != 0.0
    v = db["catalog_variants"].find_one({"sku": "V-Z1"})
    assert v["discounted_price"] == 500.0 and v["discounted_price"] != 0.0


# --- #9 migration mints a STABLE rule_id ------------------------------------


def test_migration_mints_stable_rule_id_across_reruns():
    import migrate_bvi_discount_rules as mig

    coll = _FakeUpsertColl()
    row = {"id": "d1", "category": "SUNGLASSES", "brand": "Ray-Ban",
           "subBrand": None, "discountPercentage": 20}
    ids = []
    for _ in range(3):
        doc = mig.map_discount_rule(row)
        mig._upsert_one(coll, mig.natural_key(doc), doc)
        ids.append(coll.docs[0]["rule_id"])
    assert len(coll.docs) == 1
    assert coll.docs[0]["rule_id"], "migrated rule must carry a CRUD rule_id"
    assert coll.docs[0]["id"] == coll.docs[0]["rule_id"]  # id mirrors rule_id
    assert coll.docs[0]["rule_id"].startswith("rule_")
    assert ids[0] == ids[1] == ids[2]  # stable across re-runs ($setOnInsert)


# --- #10 duplicate natural-key rejected via the router ----------------------


def test_router_rejects_duplicate_category_only_rule():
    import asyncio

    from fastapi import HTTPException

    from api.routers import online_store_discount_rules as rules_router

    db = _EngineDB()
    _orig = rules_router._get_db
    rules_router._get_db = lambda: db
    try:
        user = {"user_id": "u1"}
        first = asyncio.run(
            rules_router.create_rule(
                rules_router.RuleCreate(category="SUNGLASS", discount_percentage=10),
                user,
            )
        )
        assert first["rule"]["category"] == "SUNGLASS"
        # Blank brand/sub_brand stored as '' (not None) so the pre-check matches.
        assert first["rule"]["brand"] == ""
        with pytest.raises(HTTPException) as ei:
            asyncio.run(
                rules_router.create_rule(
                    rules_router.RuleCreate(category="SUNGLASS", discount_percentage=25),
                    user,
                )
            )
        assert ei.value.status_code == 409
    finally:
        rules_router._get_db = _orig


# --- #12 _resolve_variant_pricing: no cutover flip for unstamped ecom price --


def test_resolve_variant_pricing_ignores_unstamped_ecom_online_price():
    from api.services import shopify_push

    # No-variant product on Shopify at its in-store offer (450); a post-merge save
    # stored ecom.online_offer_price=600 (MRP) WITHOUT an engine stamp. The push must
    # NOT prefer it (that flips the live price to MRP) -> falls back to in-store 450.
    product = {"mrp": 600, "offer_price": 450, "ecom": {"online_offer_price": 600}}
    price, mrp = shopify_push._resolve_variant_pricing(product, {})
    assert price == 450.0  # in-store offer, NOT raised to MRP 600
    assert mrp == 600.0


def test_resolve_variant_pricing_uses_stamped_engine_online_price():
    from api.services import shopify_push

    product = {
        "mrp": 600, "offer_price": 450,
        "ecom": {"online_offer_price": 540, "online_price_source": "rule"},
    }
    price, _mrp = shopify_push._resolve_variant_pricing(product, {})
    assert price == 540.0  # engine-stamped rule price IS preferred
