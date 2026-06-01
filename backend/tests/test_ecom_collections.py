"""
Tests for the BVI Phase 2 Collections module (FLAGSHIP #1).

Three layers, mirroring the repo's established test style (test_bulk_create):
  1. EcomCollectionRepository round-trips via the in-memory MockCollection
     (no live Mongo): create / get_by_handle / list filters / update / delete,
     and the manual-membership ops add / remove / reorder.
  2. The pure SMART rule resolver (services/ecom_smart_rules): brand/category
     matching under AND + OR, CONTAINS, the attributes/ecom.seo.tags read paths,
     and the fail-soft empty-rule contract.
  3. Router wiring: every collections route is catalogued in rbac_policy.POLICY
     with the ecom role set, check_access allow/deny, and the live role gate
     (SALES_STAFF 403) -- none of which need a DB.

Run: JWT_SECRET_KEY=test python -m pytest backend/tests/test_ecom_collections.py -q
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
from api.services import rbac_policy as rbac  # noqa: E402


# ===========================================================================
# Layer 1 -- repository round-trips (MockCollection, no live Mongo)
# ===========================================================================

@pytest.fixture
def repo():
    return EcomCollectionRepository(MockCollection("ecom_collections"))


def test_create_then_get_by_handle_roundtrip(repo):
    """A collection created via the repo is read back by handle with sane
    defaults applied (CUSTOM, published, dirty-from-birth, empty membership)."""
    created = repo.create(
        {
            "title": "Ray-Ban Sunglasses",
            "handle": "ray-ban-sunglasses",
            "auto_source": "brand:Ray-Ban",
            "category_anchor": "SUNGLASS",
        }
    )
    assert created is not None
    assert created["collection_id"]
    assert created["handle"] == "ray-ban-sunglasses"
    # Defaults.
    assert created["collection_type"] == "CUSTOM"
    assert created["published"] is True
    assert created["disjunctive"] is False
    assert created["sort_priority"] == 100
    assert created["products"] == []
    assert created["products_count"] == 0
    # PUSH-DARK: born dirty (nothing pushed to Shopify yet).
    assert created["locally_modified"] is True
    assert "created_at" in created and "updated_at" in created

    fetched = repo.get_by_handle("ray-ban-sunglasses")
    assert fetched is not None
    assert fetched["collection_id"] == created["collection_id"]
    assert fetched["auto_source"] == "brand:Ray-Ban"
    assert fetched["category_anchor"] == "SUNGLASS"


def test_create_requires_handle(repo):
    """handle is the unique slug + idempotent key; a row without it is refused."""
    assert repo.create({"title": "No Handle"}) is None
    assert repo.create({}) is None
    assert repo.count() == 0


def test_get_by_handle_and_id_missing_return_none(repo):
    assert repo.get_by_handle("nope") is None
    assert repo.get_by_handle("") is None
    assert repo.get_by_id("nope") is None
    assert repo.get_by_id("") is None


def test_list_filters_by_published_type_anchor_and_source(repo):
    repo.create({"title": "A", "handle": "a", "collection_type": "SMART",
                 "category_anchor": "FRAME", "auto_source": "brand:Boss",
                 "published": True, "sort_priority": 10})
    repo.create({"title": "B", "handle": "b", "collection_type": "CUSTOM",
                 "category_anchor": "SUNGLASS", "published": False,
                 "sort_priority": 20})

    # Unfiltered -> both, ordered by sort_priority asc (A before B).
    rows = repo.list()
    assert [r["handle"] for r in rows] == ["a", "b"]

    assert [r["handle"] for r in repo.list(published=True)] == ["a"]
    assert [r["handle"] for r in repo.list(published=False)] == ["b"]
    assert [r["handle"] for r in repo.list(collection_type="SMART")] == ["a"]
    assert [r["handle"] for r in repo.list(category_anchor="SUNGLASS")] == ["b"]
    assert [r["handle"] for r in repo.list(auto_source="brand:Boss")] == ["a"]


def test_update_patches_and_marks_dirty(repo):
    created = repo.create({"title": "Old", "handle": "h1", "locally_modified": False})
    cid = created["collection_id"]
    # Sanity: explicit False honoured on create.
    assert repo.get_by_id(cid)["locally_modified"] is False

    assert repo.update(cid, {"title": "New", "seo_title": "SEO"}) is True
    doc = repo.get_by_id(cid)
    assert doc["title"] == "New"
    assert doc["seo_title"] == "SEO"
    # Any update re-marks dirty for the Phase-5 push queue.
    assert doc["locally_modified"] is True
    # Identity / created_at are immutable through update().
    assert repo.update(cid, {"collection_id": "hacked", "created_at": "x"}) is False
    assert repo.get_by_id(cid)["collection_id"] == cid


def test_delete_removes_collection(repo):
    created = repo.create({"title": "Doomed", "handle": "doomed"})
    cid = created["collection_id"]
    assert repo.delete(cid) is True
    assert repo.get_by_id(cid) is None
    # Deleting again is a no-op False (already gone).
    assert repo.delete(cid) is False


# ---- manual membership ----------------------------------------------------

def test_add_product_appends_and_is_idempotent(repo):
    cid = repo.create({"title": "Manual", "handle": "manual"})["collection_id"]

    repo.add_product(cid, "SKU-1")
    repo.add_product(cid, "SKU-2")
    doc = repo.get_by_id(cid)
    members = {p["sku"]: p["position"] for p in doc["products"]}
    assert members == {"SKU-1": 0, "SKU-2": 1}
    assert doc["products_count"] == 2

    # Re-adding an existing SKU does NOT duplicate it; with a position it moves.
    repo.add_product(cid, "SKU-1", position=5)
    doc = repo.get_by_id(cid)
    assert doc["products_count"] == 2  # still 2 rows
    pos = {p["sku"]: p["position"] for p in doc["products"]}
    assert pos["SKU-1"] == 5


def test_remove_product_is_idempotent(repo):
    cid = repo.create({"title": "Manual", "handle": "manual2"})["collection_id"]
    repo.add_product(cid, "SKU-1")
    repo.add_product(cid, "SKU-2")

    repo.remove_product(cid, "SKU-1")
    doc = repo.get_by_id(cid)
    assert [p["sku"] for p in doc["products"]] == ["SKU-2"]
    assert doc["products_count"] == 1
    # Removing a non-member is a no-op success (count unchanged).
    repo.remove_product(cid, "SKU-NOPE")
    assert repo.get_by_id(cid)["products_count"] == 1


def test_reorder_products_sets_positions_and_appends_leftovers(repo):
    cid = repo.create({"title": "Manual", "handle": "manual3"})["collection_id"]
    for sku in ("A", "B", "C"):
        repo.add_product(cid, sku)

    # Reorder a subset; C is not named -> appended after, keeping prior order.
    repo.reorder_products(cid, ["B", "A"])
    doc = repo.get_by_id(cid)
    ordered = sorted(doc["products"], key=lambda p: p["position"])
    assert [p["sku"] for p in ordered] == ["B", "A", "C"]
    assert [p["position"] for p in ordered] == [0, 1, 2]


def test_membership_ops_unknown_collection_return_none(repo):
    assert repo.add_product("no-such", "SKU-1") is None
    assert repo.remove_product("no-such", "SKU-1") is None
    assert repo.reorder_products("no-such", ["SKU-1"]) is None


# ===========================================================================
# Layer 2 -- pure SMART rule resolver
# ===========================================================================

def _product(sku, category=None, brand=None, tags=None, title=None):
    """Build a catalog_products-shaped doc: brand lives in attributes, tags in
    the optional ecom.seo.tags, category top-level (matches the real schema)."""
    doc = {"sku": sku, "attributes": {}}
    if category is not None:
        doc["category"] = category
    if brand is not None:
        doc["attributes"]["brand"] = brand
    if title is not None:
        doc["title"] = title
    if tags is not None:
        doc["ecom"] = {"seo": {"tags": tags}}
    return doc


CATALOG = [
    _product("RB-AVTR", category="SUNGLASS", brand="Ray-Ban", tags=["bestseller"]),
    _product("RB-WAY", category="SUNGLASS", brand="Ray-Ban", tags=["new"]),
    _product("BOSS-OPT", category="FRAME", brand="Boss", tags=["new"]),
    _product("GEN-FRAME", category="FRAME", brand="Generic"),
]


def test_resolver_brand_equals():
    rules = [{"field": "brand", "relation": "EQUALS", "value": "Ray-Ban"}]
    skus = smart.resolve_skus(CATALOG, rules)
    assert sorted(skus) == ["RB-AVTR", "RB-WAY"]


def test_resolver_category_equals_is_case_insensitive():
    rules = [{"field": "category", "relation": "EQUALS", "value": "frame"}]
    skus = smart.resolve_skus(CATALOG, rules)
    assert sorted(skus) == ["BOSS-OPT", "GEN-FRAME"]


def test_resolver_AND_brand_and_category():
    """AND (disjunctive=False default): both rules must hold."""
    rules = [
        {"field": "brand", "relation": "EQUALS", "value": "Ray-Ban"},
        {"field": "category", "relation": "EQUALS", "value": "SUNGLASS"},
    ]
    skus = smart.resolve_skus(CATALOG, rules, disjunctive=False)
    assert sorted(skus) == ["RB-AVTR", "RB-WAY"]

    # AND with a category that Ray-Ban products don't have -> empty.
    rules2 = [
        {"field": "brand", "relation": "EQUALS", "value": "Ray-Ban"},
        {"field": "category", "relation": "EQUALS", "value": "FRAME"},
    ]
    assert smart.resolve_skus(CATALOG, rules2, disjunctive=False) == []


def test_resolver_OR_brand_or_brand():
    """OR (disjunctive=True): any rule matching includes the product."""
    rules = [
        {"field": "brand", "relation": "EQUALS", "value": "Ray-Ban"},
        {"field": "brand", "relation": "EQUALS", "value": "Boss"},
    ]
    skus = smart.resolve_skus(CATALOG, rules, disjunctive=True)
    assert sorted(skus) == ["BOSS-OPT", "RB-AVTR", "RB-WAY"]


def test_resolver_contains_on_tags_list():
    """CONTAINS against the ecom.seo.tags list field; 'new' matches two."""
    rules = [{"field": "tag", "relation": "CONTAINS", "value": "ew"}]
    skus = smart.resolve_skus(CATALOG, rules)
    assert sorted(skus) == ["BOSS-OPT", "RB-WAY"]


def test_resolver_empty_rules_match_nothing():
    """Fail-soft: no rules -> empty set (never an accidental match-all)."""
    assert smart.resolve_skus(CATALOG, []) == []
    assert smart.matches_product(CATALOG[0], []) is False


def test_resolver_malformed_rule_is_skipped():
    """A rule missing value is skipped; with one good rule left, AND still works."""
    rules = [
        {"field": "brand", "value": "Ray-Ban"},          # good
        {"field": "category", "relation": "EQUALS"},     # malformed (no value)
    ]
    skus = smart.resolve_skus(CATALOG, rules, disjunctive=False)
    assert sorted(skus) == ["RB-AVTR", "RB-WAY"]


def test_resolver_limit_caps_results():
    rules = [{"field": "category", "relation": "EQUALS", "value": "SUNGLASS"}]
    assert len(smart.resolve_skus(CATALOG, rules, limit=1)) == 1


def test_resolver_brand_fallback_to_top_level():
    """If brand is top-level (not under attributes) the resolver still finds it."""
    prod = {"sku": "X", "brand": "Oakley", "category": "SUNGLASS"}
    rules = [{"field": "brand", "relation": "EQUALS", "value": "Oakley"}]
    assert smart.resolve_skus([prod], rules) == ["X"]


def test_resolver_supported_fields_introspection():
    fields = smart.supported_fields()
    assert "brand" in fields and "category" in fields and "tag" in fields
    # tag/tags alias collapses to one entry.
    assert fields.count("tag") == 1
    assert "EQUALS" in smart.SUPPORTED_RELATIONS and "CONTAINS" in smart.SUPPORTED_RELATIONS


# ===========================================================================
# Layer 3 -- router RBAC catalogue + role gate (no DB)
# ===========================================================================

_COLLECTION_ROUTES = [
    ("GET", "/api/v1/online-store/collections"),
    ("POST", "/api/v1/online-store/collections"),
    ("GET", "/api/v1/online-store/collections/{collection_id}"),
    ("PUT", "/api/v1/online-store/collections/{collection_id}"),
    ("DELETE", "/api/v1/online-store/collections/{collection_id}"),
    ("POST", "/api/v1/online-store/collections/{collection_id}/products"),
    ("DELETE", "/api/v1/online-store/collections/{collection_id}/products/{sku}"),
    ("PUT", "/api/v1/online-store/collections/{collection_id}/products/reorder"),
    ("GET", "/api/v1/online-store/collections/{collection_id}/resolved-products"),
]

_ECOM_SET = {"ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER", "SUPERADMIN"}


def test_every_collection_route_catalogued_with_ecom_roles():
    for method, path in _COLLECTION_ROUTES:
        entry = rbac.policy_for(method, path)
        assert entry is not None, f"{method} {path} not catalogued in rbac_policy"
        assert set(entry["allowed"]) == _ECOM_SET, f"{method} {path} -> {entry['allowed']}"


def test_reorder_literal_beats_sku_param():
    """PUT .../products/reorder must resolve to the literal route (it's the only
    PUT at that depth) -- and the DELETE .../products/{sku} stays a param route."""
    reorder = rbac.policy_for("PUT", "/api/v1/online-store/collections/C1/products/reorder")
    assert reorder is not None
    assert reorder["path"].endswith("/products/reorder")
    sku_del = rbac.policy_for("DELETE", "/api/v1/online-store/collections/C1/products/SKU-9")
    assert sku_del is not None
    assert sku_del["path"].endswith("/products/{sku}")


def test_check_access_allows_ecom_roles_denies_others():
    path = "/api/v1/online-store/collections"
    for role in ("SUPERADMIN", "ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER"):
        assert rbac.check_access("POST", path, [role]) is True, role
    for role in ("SALES_STAFF", "CASHIER", "OPTOMETRIST", "WORKSHOP_STAFF", "ACCOUNTANT"):
        assert rbac.check_access("POST", path, [role]) is False, role


def test_live_role_gate_forbids_sales_staff(client, staff_headers):
    """SALES_STAFF is outside the ecom set -> 403 before the handler (no DB needed)."""
    r = client.get("/api/v1/online-store/collections", headers=staff_headers)
    assert r.status_code == 403, r.text


def test_live_list_is_failsoft_without_db(client, auth_headers):
    """GET list returns 200 with a well-formed envelope even when no DB is
    connected (db_connected False -> empty list, never a 500)."""
    r = client.get("/api/v1/online-store/collections", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "collections" in body and "count" in body
    assert isinstance(body["collections"], list)
