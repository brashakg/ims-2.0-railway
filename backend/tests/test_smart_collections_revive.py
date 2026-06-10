"""
Tests for unification step 11 -- revive the BVI-migrated smart collections.

THE BUG: 1,160 BVI collections were migrated into `ecom_collections` with smart
rules in the SHOPIFY shape ({column, relation, condition}, column enum VENDOR /
TYPE / TAG / TITLE / VARIANT_SKU / VARIANT_PRICE / ...), but the IMS rule engine
(services/ecom_smart_rules) read ONLY {field, relation, value} -- so every
migrated SMART collection resolved to ZERO products and the FE editor rendered
blank rule rows. ALSO: the engine's brand resolution missed
`attributes.brand_name` (where BVI-migrated catalog docs store the brand).

THE FIX (read-side, idempotent -- stored docs are NEVER rewritten):
  1. normalize_rule/normalize_rules: Shopify shape -> IMS shape (VENDOR->brand,
     TYPE/PRODUCT_TYPE->category, TAG->tag, TITLE->title, VARIANT_SKU->sku,
     VARIANT_PRICE->price; unknown columns pass through marked, never crash),
     applied inside the evaluator + the collection GET serializers.
  2. attributes.brand_name added to the brand field-path list.
  3. scripts/migrate_bvi_pim.py::map_collection now writes the normalized shape
     under `rules` and keeps the original under `rules_shopify` for fidelity.

Layers (mirrors test_ecom_collections.py style; CI-robust: monkeypatched
accessors + seeded MockCollection docs, no live Mongo needed):
  1. Normalizer unit tests (mapping table, idempotency, passthrough, garbage).
  2. Engine resolution on a seeded fake catalog (migrated shape + price
     relations + brand_name + native-shape regression).
  3. Live router: editor GET returns normalized rules; resolved-products
     resolves a migrated SMART collection; the stored doc stays untouched.
  4. Migration mapper: normalized `rules` + verbatim `rules_shopify`.

Run: JWT_SECRET_KEY=test python -m pytest backend/tests/test_smart_collections_revive.py -q
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

# Make scripts/ importable for the migration mapper (same pattern as
# test_migrate_bvi_pim.py).
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts"))

import pytest  # noqa: E402

from database.connection import MockCollection  # noqa: E402
from database.repositories.ecom_collection_repository import (  # noqa: E402
    EcomCollectionRepository,
)
from api.services import ecom_smart_rules as smart  # noqa: E402
from api.routers import online_store_collections as osc  # noqa: E402
from migrate_bvi_pim import map_collection  # noqa: E402


# ===========================================================================
# Seeded fake catalog (catalog_products-shaped docs)
# ===========================================================================

def _cat(sku, brand=None, brand_name=None, category=None, tags=None,
         title=None, price=None, extra_attrs=None):
    """Build a catalog_products-shaped doc: brand under attributes (and the
    BVI-migrated `attributes.brand_name` variant), category top-level, tags in
    ecom.seo.tags, price under pricing.offer_price -- the real schema."""
    doc = {"sku": sku, "attributes": {}}
    if brand is not None:
        doc["attributes"]["brand"] = brand
    if brand_name is not None:
        doc["attributes"]["brand_name"] = brand_name
    if category is not None:
        doc["category"] = category
    if title is not None:
        doc["title"] = title
    if tags is not None:
        doc["ecom"] = {"seo": {"tags": tags}}
    if price is not None:
        doc["pricing"] = {"mrp": price, "offer_price": price}
    if extra_attrs:
        doc["attributes"].update(extra_attrs)
    return doc


CATALOG = [
    _cat("RB-EXP", brand="Ray-Ban", category="SUNGLASS", price=7999,
         title="Ray-Ban Aviator"),
    _cat("RB-CHEAP", brand="Ray-Ban", category="SUNGLASS", price=3500,
         title="Ray-Ban Round"),
    # BVI-migrated catalog doc: brand lives in attributes.brand_name ONLY.
    _cat("VOGUE-1", brand_name="Vogue", category="FRAME", price=2500,
         title="Vogue Cateye"),
    _cat("BOSS-1", brand="Boss", category="FRAME", price=9000, tags=["new"],
         extra_attrs={"variant_weight": "32"}),
]


def _shopify_rule(column, relation, condition):
    """A rule exactly as the BVI migration stored it (Shopify shape)."""
    return {"column": column, "relation": relation, "condition": condition}


# ===========================================================================
# Layer 1 -- the pure normalizer
# ===========================================================================

@pytest.mark.parametrize(
    "column,expected_field",
    [
        ("VENDOR", "brand"),
        ("vendor", "brand"),          # case-insensitive
        ("TYPE", "category"),
        ("PRODUCT_TYPE", "category"),
        ("TAG", "tag"),
        ("TITLE", "title"),
        ("VARIANT_SKU", "sku"),
        ("VARIANT_PRICE", "price"),
    ],
)
def test_normalizer_column_mapping_table(column, expected_field):
    out = smart.normalize_rule(
        {"column": column, "relation": "EQUALS", "condition": "X"}
    )
    assert out == {"field": expected_field, "relation": "EQUALS", "value": "X"}


def test_normalizer_native_rule_passes_through_untouched():
    """An IMS-shaped rule is returned as the SAME object -- the idempotency
    guarantee that lets every read path normalize unconditionally."""
    rule = {"field": "brand", "relation": "EQUALS", "value": "Ray-Ban"}
    assert smart.normalize_rule(rule) is rule


def test_normalizer_is_idempotent_on_double_apply():
    shopify = _shopify_rule("VENDOR", "EQUALS", "Ray-Ban")
    once = smart.normalize_rule(shopify)
    twice = smart.normalize_rule(once)
    assert twice == once == {
        "field": "brand", "relation": "EQUALS", "value": "Ray-Ban"
    }
    # The original Shopify-shaped dict was NOT mutated (read-side only).
    assert shopify == {"column": "VENDOR", "relation": "EQUALS",
                       "condition": "Ray-Ban"}


def test_normalizer_unknown_column_passes_through_marked():
    """An unmapped Shopify column becomes a loose-lookup field, keeps the
    original column for fidelity, and is flagged passthrough -- never crashes.
    The loose attributes lookup can still match it."""
    out = smart.normalize_rule(
        _shopify_rule("VARIANT_WEIGHT", "EQUALS", "32")
    )
    assert out["field"] == "variant_weight"
    assert out["value"] == "32"
    assert out["passthrough"] is True
    assert out["column"] == "VARIANT_WEIGHT"
    # BOSS-1 carries attributes.variant_weight = "32" -> the rule still works.
    assert smart.resolve_skus(CATALOG, [out]) == ["BOSS-1"]


def test_normalizer_never_crashes_on_garbage():
    for garbage in (None, "rule", 42, [], {}, {"column": None},
                    {"condition": None}, {"column": ""}):
        smart.normalize_rule(garbage)  # must not raise
    # Non-list rule containers fail soft to [].
    assert smart.normalize_rules(None) == []
    assert smart.normalize_rules("nope") == []
    assert smart.normalize_rules({"column": "VENDOR"}) == []
    # Shopify rule without a condition -> empty value -> evaluator skips it.
    out = smart.normalize_rule({"column": "VENDOR", "relation": "EQUALS"})
    assert out["value"] == ""
    assert smart.resolve_skus(CATALOG, [out]) == []


# ===========================================================================
# Layer 2 -- engine resolution with migrated-shape rules
# ===========================================================================

def test_migrated_vendor_rule_now_resolves_products():
    """THE headline revive: a VENDOR rule exactly as migrated (Shopify shape)
    resolves products. Before this fix the engine saw no `field`/`value`,
    skipped the rule as malformed, and returned [] for every migrated row."""
    rules = [_shopify_rule("VENDOR", "EQUALS", "Ray-Ban")]
    assert sorted(smart.resolve_skus(CATALOG, rules)) == ["RB-CHEAP", "RB-EXP"]


def test_migrated_vendor_AND_variant_price_resolves():
    """The audit's exact repro shape: VENDOR equals X AND VARIANT_PRICE
    greater-than N -> only the expensive Ray-Ban matches."""
    rules = [
        _shopify_rule("VENDOR", "EQUALS", "Ray-Ban"),
        _shopify_rule("VARIANT_PRICE", "GREATER_THAN", "5000"),
    ]
    assert smart.resolve_skus(CATALOG, rules, disjunctive=False) == ["RB-EXP"]


def test_migrated_variant_price_less_than():
    rules = [_shopify_rule("VARIANT_PRICE", "LESS_THAN", "3000")]
    assert smart.resolve_skus(CATALOG, rules) == ["VOGUE-1"]


def test_price_rule_non_numeric_bound_fails_soft():
    """A non-numeric price bound never raises and never matches."""
    rules = [_shopify_rule("VARIANT_PRICE", "GREATER_THAN", "abc")]
    assert smart.resolve_skus(CATALOG, rules) == []


def test_brand_matched_via_attributes_brand_name():
    """Catalog docs that store the brand under attributes.brand_name (the BVI
    migration shape) now match -- in BOTH rule shapes."""
    native = [{"field": "brand", "relation": "EQUALS", "value": "Vogue"}]
    assert smart.resolve_skus(CATALOG, native) == ["VOGUE-1"]
    migrated = [_shopify_rule("VENDOR", "EQUALS", "Vogue")]
    assert smart.resolve_skus(CATALOG, migrated) == ["VOGUE-1"]


def test_attributes_brand_still_wins_over_brand_name():
    """Path order: attributes.brand is authoritative when both keys exist."""
    p = _cat("X1", brand="Primary", brand_name="Secondary")
    assert smart.matches_product(
        p, [{"field": "brand", "relation": "EQUALS", "value": "Primary"}]
    ) is True
    assert smart.matches_product(
        p, [{"field": "brand", "relation": "EQUALS", "value": "Secondary"}]
    ) is False


def test_not_relations_are_vacuously_true_on_absent_field():
    """Shopify semantics: a product with no tags does NOT have tag X."""
    bare = {"sku": "NOTAG"}
    rule = [{"field": "tag", "relation": "NOT_EQUALS", "value": "sale"}]
    assert smart.matches_product(bare, rule) is True
    tagged = _cat("T1", tags=["sale"])
    assert smart.matches_product(tagged, rule) is False


def test_native_rules_regression_unchanged():
    """The pre-existing IMS-shape contract is untouched (regression lock vs
    the test_ecom_collections resolver suite)."""
    rules = [
        {"field": "brand", "relation": "EQUALS", "value": "Ray-Ban"},
        {"field": "category", "relation": "EQUALS", "value": "SUNGLASS"},
    ]
    assert sorted(smart.resolve_skus(CATALOG, rules)) == ["RB-CHEAP", "RB-EXP"]
    # CONTAINS on the ecom.seo.tags list field still works.
    assert smart.resolve_skus(
        CATALOG, [{"field": "tag", "relation": "CONTAINS", "value": "ew"}]
    ) == ["BOSS-1"]
    # Empty / malformed rule sets still match NOTHING (never a match-all).
    assert smart.resolve_skus(CATALOG, []) == []
    assert smart.matches_product(CATALOG[0], [{"field": "brand"}]) is False


# ===========================================================================
# Layer 3 -- live router: editor GET + resolved-products (monkeypatched DB)
# ===========================================================================

_MIGRATED_RULES = [
    {"column": "VENDOR", "relation": "EQUALS", "condition": "Ray-Ban"},
    {"column": "VARIANT_PRICE", "relation": "GREATER_THAN", "condition": "5000"},
]


@pytest.fixture
def seeded(monkeypatch):
    """A SMART collection stored EXACTLY as the migration left it (Shopify-shape
    rules), behind monkeypatched router accessors -- no live Mongo needed."""
    mock = MockCollection("ecom_collections")
    repo = EcomCollectionRepository(mock)
    created = repo.create(
        {
            "title": "Ray-Ban Premium",
            "handle": "ray-ban-premium",
            "collection_type": "SMART",
            "disjunctive": False,
            "rules": [dict(r) for r in _MIGRATED_RULES],
            "source": "bvi_migration",
        }
    )
    monkeypatch.setattr(osc, "_repo", lambda: repo)
    monkeypatch.setattr(
        osc, "_catalog_products", lambda: [dict(p) for p in CATALOG]
    )
    return {"mock": mock, "repo": repo, "cid": created["collection_id"]}


def test_editor_get_returns_normalized_rules(client, auth_headers, seeded):
    """GET /{id} (what the FE editor loads) serves IMS-shaped rules -- the
    editor shows real rows instead of blanks."""
    r = client.get(
        f"/api/v1/online-store/collections/{seeded['cid']}",
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    rules = r.json()["collection"]["rules"]
    assert rules == [
        {"field": "brand", "relation": "EQUALS", "value": "Ray-Ban"},
        {"field": "price", "relation": "GREATER_THAN", "value": "5000"},
    ]


def test_list_get_returns_normalized_rules(client, auth_headers, seeded):
    r = client.get("/api/v1/online-store/collections", headers=auth_headers)
    assert r.status_code == 200, r.text
    rows = r.json()["collections"]
    assert len(rows) == 1
    assert rows[0]["rules"][0] == {
        "field": "brand", "relation": "EQUALS", "value": "Ray-Ban"
    }


def test_stored_doc_is_not_rewritten_by_reads(client, auth_headers, seeded):
    """Normalize-on-read only: after editor GET + list GET the doc on 'disk'
    (the mock store) still holds the original Shopify shape (no mass rewrite)."""
    client.get(
        f"/api/v1/online-store/collections/{seeded['cid']}",
        headers=auth_headers,
    )
    client.get("/api/v1/online-store/collections", headers=auth_headers)
    raw = next(iter(seeded["mock"]._data.values()))  # noqa: SLF001
    assert raw["rules"] == _MIGRATED_RULES
    assert "field" not in raw["rules"][0]


def test_resolved_products_resolves_migrated_collection(
    client, auth_headers, seeded
):
    """The resolver endpoint now returns matches for a migrated SMART
    collection (was: zero products for all 1,160 rows) and echoes the rules in
    the normalized shape."""
    r = client.get(
        f"/api/v1/online-store/collections/{seeded['cid']}/resolved-products",
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["collection_type"] == "SMART"
    assert body["source"] == "smart_rules"
    assert body["skus"] == ["RB-EXP"]
    assert body["count"] == 1
    assert body["rules"][0] == {
        "field": "brand", "relation": "EQUALS", "value": "Ray-Ban"
    }


# ===========================================================================
# Layer 4 -- migration mapper writes the normalized shape going forward
# ===========================================================================

def _bvi_collection_row(**overrides):
    base = {
        "id": "cuid_col_1",
        "handle": "ray-ban",
        "title": "Ray-Ban",
        "collectionType": "SMART",
        "published": True,
        "rules": (
            '[{"column":"VENDOR","relation":"EQUALS","condition":"Ray-Ban"}]'
        ),
        "disjunctive": False,
    }
    base.update(overrides)
    return base


def test_map_collection_writes_normalized_rules_plus_shopify_fidelity():
    doc = map_collection(_bvi_collection_row(), [])
    assert doc["rules"] == [
        {"field": "brand", "relation": "EQUALS", "value": "Ray-Ban"}
    ]
    # Original Shopify shape preserved verbatim for push-side fidelity.
    assert doc["rules_shopify"] == [
        {"column": "VENDOR", "relation": "EQUALS", "condition": "Ray-Ban"}
    ]


def test_map_collection_native_rules_pass_through():
    row = _bvi_collection_row(
        rules='[{"field":"brand","relation":"EQUALS","value":"Ray-Ban"}]'
    )
    doc = map_collection(row, [])
    assert doc["rules"] == [
        {"field": "brand", "relation": "EQUALS", "value": "Ray-Ban"}
    ]
    assert doc["rules_shopify"] == doc["rules"]


def test_map_collection_without_rules_stays_empty():
    doc = map_collection(_bvi_collection_row(rules=None), [])
    assert doc["rules"] == []
    assert doc["rules_shopify"] == []


def test_migration_to_resolution_end_to_end():
    """Full pipeline: BVI row (Shopify-shape rules JSON) -> map_collection ->
    engine resolution against the seeded catalog -> the right SKU."""
    row = _bvi_collection_row(
        rules=(
            '[{"column":"VENDOR","relation":"EQUALS","condition":"Ray-Ban"},'
            '{"column":"VARIANT_PRICE","relation":"GREATER_THAN",'
            '"condition":"5000"}]'
        )
    )
    doc = map_collection(row, [])
    skus = smart.resolve_skus(
        CATALOG, doc["rules"], disjunctive=doc["disjunctive"]
    )
    assert skus == ["RB-EXP"]
