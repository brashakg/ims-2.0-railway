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


def test_compute_never_below_cost_clamps_and_flags():
    # 40% off 1000 = 600, but cost is 750 -> clamp UP to cost, flag COST_CLAMPED.
    r = engine.compute_online_price(1000, discount_pct=40, cost_price=750)
    assert r["offer"] == 750.0
    assert "COST_CLAMPED" in r["flags"]
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


def test_compute_manual_offer_still_clamped_below_cost():
    # A manual price below cost is still lifted to cost (never sell at a loss).
    r = engine.compute_online_price(1000, discount_pct=0, manual_offer=500, cost_price=650)
    assert r["offer"] == 650.0
    assert "COST_CLAMPED" in r["flags"]


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
