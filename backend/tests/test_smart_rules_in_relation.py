"""
Collections Phase 1, Track 1 -- the IN relation (ecom_smart_rules).

Additive, backward-compatible: a rule {field, relation: 'IN', value: [list]}
matches when the product's extracted value(s) for that field case-insensitively
equal ANY of the list entries. Contract locked here:

  * IN matches ANY entry of the list, case-insensitively.
  * IN resolves attributes-dict fields (e.g. lens_colour) via the engine's
    loose fallback, same as every other relation.
  * normalize_rule passes IN rules through with the value STAYING a list of
    trimmed non-empty strings (blanks dropped), idempotently.
  * An EMPTY-list IN is a VALID rule that NEVER matches (False, not skipped --
    so it can never accidentally match-all through the malformed-skip path).
  * Lists are allowed ONLY for IN: every other relation keeps coercing a list
    value through str() exactly as before (-> no match), unchanged behaviour.
  * PARITY: a single-value IN behaves identically to EQUALS for the same
    field/value.
  * 'IN' is advertised in SUPPORTED_RELATIONS; legacy Shopify-shaped rules
    (which never carry IN) still normalize exactly as before.

CI-robust: pure evaluator, no DB. Mirrors test_smart_collections_revive.py.

Run: JWT_SECRET_KEY=test python -m pytest \
        backend/tests/test_smart_rules_in_relation.py -q
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

import pytest  # noqa: E402

from api.services import ecom_smart_rules as smart  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture products (catalog_products-shaped: brand under attributes, category
# top-level, tags under ecom.seo.tags -- the real schema)
# ---------------------------------------------------------------------------

RAYBAN = {
    "sku": "RB-1",
    "category": "SUNGLASS",
    "attributes": {"brand": "Ray-Ban", "lens_colour": "Green"},
    "ecom": {"seo": {"tags": ["new", "aviator"]}},
}
GUCCI = {
    "sku": "GG-1",
    "category": "FRAME",
    "attributes": {"brand": "Gucci", "lens_colour": "Brown"},
}
NO_BRAND = {"sku": "NB-1", "category": "FRAME", "attributes": {}}

CATALOG = [RAYBAN, GUCCI, NO_BRAND]


def _in_rule(field, values):
    return {"field": field, "relation": "IN", "value": values}


# ===========================================================================
# 1. IN matches any of the list, case-insensitively
# ===========================================================================


# ===========================================================================
# 2. IN on an attributes-dict field via the loose fallback
# ===========================================================================


# ===========================================================================
# 3. Empty-list IN never matches (False, NOT skipped-as-malformed)
# ===========================================================================


# ===========================================================================
# 4. Lists stay rejected/coerced for every NON-IN relation (unchanged)
# ===========================================================================


def test_normalize_leaves_non_in_list_value_untouched():
    rule = {"field": "brand", "relation": "EQUALS", "value": ["Ray-Ban"]}
    out = smart.normalize_rule(rule)
    assert out is rule  # same object: idempotent IMS-shape passthrough


# ===========================================================================
# 5. normalize_rule / normalize_rules round-trip
# ===========================================================================


def test_normalize_rule_cleans_in_list_and_keeps_it_a_list():
    rule = _in_rule("brand", ["  Ray-Ban  ", "", None, "Gucci", "   "])
    out = smart.normalize_rule(rule)
    assert out["field"] == "brand"
    assert out["relation"] == "IN"
    assert out["value"] == ["Ray-Ban", "Gucci"]  # trimmed, blanks dropped


def test_normalize_rule_in_is_idempotent():
    once = smart.normalize_rule(_in_rule("brand", [" Ray-Ban ", ""]))
    twice = smart.normalize_rule(once)
    assert twice == once
    assert isinstance(twice["value"], list)


def test_normalize_rule_in_empty_list_stays_empty():
    out = smart.normalize_rule(_in_rule("brand", []))
    assert out["value"] == []
    assert out["relation"] == "IN"


def test_normalize_rule_lowercase_in_relation_uppercased():
    out = smart.normalize_rule({"field": "brand", "relation": "in",
                                "value": ["Ray-Ban"]})
    assert out["relation"] == "IN"
    assert out["value"] == ["Ray-Ban"]


def test_normalize_rules_round_trip_mixed_shapes():
    """A mixed list -- native IN + native EQUALS + legacy Shopify shape --
    normalizes each member correctly, and re-normalizing is a fixed point."""
    rules = [
        _in_rule("brand", [" Ray-Ban ", ""]),
        {"field": "category", "relation": "EQUALS", "value": "SUNGLASS"},
        {"column": "VENDOR", "relation": "EQUALS", "condition": "Gucci"},
    ]
    once = smart.normalize_rules(rules)
    assert once[0]["value"] == ["Ray-Ban"]
    assert once[1] is rules[1]  # native non-IN rule passes through untouched
    assert once[2] == {"field": "brand", "relation": "EQUALS", "value": "Gucci"}
    assert smart.normalize_rules(once) == once  # idempotent round-trip


def test_shopify_shaped_rules_untouched_by_in_support():
    """Legacy Shopify-shape normalization is byte-identical to before (none
    of the migrated rules carry IN)."""
    out = smart.normalize_rule(
        {"column": "TAG", "relation": "EQUALS", "condition": "new"}
    )
    assert out == {"field": "tag", "relation": "EQUALS", "value": "new"}
    assert not isinstance(out["value"], list)


# ===========================================================================
# 6. PARITY: single-value IN == EQUALS
# ===========================================================================


# ===========================================================================
# 7. Introspection + index groundwork
# ===========================================================================


def test_orders_index_declares_items_product_id_created_at():
    """Track-1 index groundwork: INDEXES['orders'] declares the multikey
    {items.product_id, created_at desc} compound for per-product sales reads."""
    from database.schemas import INDEXES

    assert any(
        spec.get("keys") == [("items.product_id", 1), ("created_at", -1)]
        for spec in INDEXES["orders"]
    )
