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

Layers (mirrors test_ecom_collections.py style; CI-robust: monkeypatched
accessors + seeded MockCollection docs, no live Mongo needed):
  1. Normalizer unit tests (mapping table, idempotency, passthrough, garbage).
  2. Engine resolution on a seeded fake catalog (migrated shape + price
     relations + brand_name + native-shape regression).
  3. Live router: editor GET returns normalized rules; resolved-products
     resolves a migrated SMART collection; the stored doc stays untouched.

Run: JWT_SECRET_KEY=test python -m pytest backend/tests/test_smart_collections_revive.py -q
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

import pytest  # noqa: E402

from database.connection import MockCollection  # noqa: E402
from database.repositories.ecom_collection_repository import (  # noqa: E402
    EcomCollectionRepository,
)
from api.services import ecom_smart_rules as smart  # noqa: E402
from api.routers import online_store_collections as osc  # noqa: E402


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


# ===========================================================================
# Layer 2 -- engine resolution with migrated-shape rules
# ===========================================================================

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
    # THE website membership rule over the listings on the website (the facts
    # the push sends: vendor, variant prices).
    from api.services import collection_membership as cm

    facts = [{"sku": p["sku"], "brand": (p.get("attributes") or {}).get("brand") or p.get("brand") or "",
              "tags": set(), "type": "Sunglass",
              "prices": [float((p.get("pricing") or {}).get("offer_price") or p.get("offer_price") or 0)]}
             for p in CATALOG]
    from strict_fakes import StrictDB

    db = StrictDB()  # the catalogue a CUSTOM collection's manual SKUs are read from
    db.seed("catalog_products", [dict(p) for p in CATALOG])
    monkeypatch.setattr(osc, "_get_db", lambda: db)
    monkeypatch.setattr(cm, "online_facts", lambda db, strict=False: facts)
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
    assert body["source"] == "website_rule"
    assert body["skus"] == ["RB-EXP"]
    assert body["count"] == 1
    assert body["rules"][0] == {
        "field": "brand", "relation": "EQUALS", "value": "Ray-Ban"
    }


def test_resolved_products_counts_a_custom_collection_by_the_one_rule(client, auth_headers, seeded):
    """A CUSTOM collection: ``skus`` / ``count`` are the listings ON THE
    WEBSITE (the menu's number); the hand-picked list rides, labelled, as
    ``manual_skus`` -- "count" means one thing for both types."""
    custom = seeded["repo"].create({
        "title": "Picks", "handle": "picks", "collection_type": "CUSTOM",
        "products": [{"sku": "GONE-1", "position": 0}, {"sku": "BOSS-1", "position": 1}],
    })
    r = client.get(
        f"/api/v1/online-store/collections/{custom['collection_id']}/resolved-products",
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["skus"] == ["BOSS-1"] and body["count"] == 1 and body["source"] == "website_rule"
    assert body["manual_skus"] == ["GONE-1", "BOSS-1"]


def test_resolved_products_says_unreadable_when_a_custom_lookup_blips(client, auth_headers, seeded, monkeypatch):
    """A Mongo blip reading a CUSTOM collection's manual SKU: the route says
    unreadable, never a count one member short (review round 6)."""
    custom = seeded["repo"].create({
        "title": "Picks", "handle": "picks", "collection_type": "CUSTOM",
        "products": [{"sku": "BOSS-1", "position": 0}],
    })
    db = osc._get_db()

    def _blip(*_a, **_k):
        raise RuntimeError("mongo blip")

    monkeypatch.setattr(db["catalog_products"], "find_one", _blip)
    r = client.get(
        f"/api/v1/online-store/collections/{custom['collection_id']}/resolved-products",
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["count"] is None and r.json()["unreadable"]


def test_resolved_products_says_unreadable_never_zero(client, auth_headers, seeded, monkeypatch):
    """The listings cannot be read: the route says so (count None,
    ``unreadable``), never "0 products" (review round 3)."""
    from api.services import collection_membership as cm

    def _facts(db, strict=False):
        if strict:
            raise RuntimeError("Mongo down")
        return []

    monkeypatch.setattr(cm, "online_facts", _facts)
    r = client.get(f"/api/v1/online-store/collections/{seeded['cid']}/resolved-products", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["unreadable"] and r.json()["count"] is None
