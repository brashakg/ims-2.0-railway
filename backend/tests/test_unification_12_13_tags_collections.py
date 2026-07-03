"""
Unification step-12 (governed product tags) + step-13 (materialised smart
collections + collection browse).

Locks the step-12/13 contract from docs/reference/UNIFICATION_AUDIT_2026-06-10.md:

  step-12:
   * `normalise_tags` lower-cases / trims / collapses whitespace / de-dupes
     (order-preserving), tolerates a CSV string, and never raises.
   * tags flow through the canonical doors (build_canonical_product /
     create_via_door) so the SAME input yields an IDENTICAL `tags` array.
   * the tag-filter predicate (as used by GET /products?tag=) matches members.

  step-13:
   * a SMART rule (tag=ray-ban OR category=SUNGLASS) materialises the CORRECT
     membership into the collection_products view.
   * a product create / update refreshes SMART membership.
   * browse() pages the materialised membership.
   * a rule change recomputes membership.
   * an empty (no-match / no-rules) collection browses to an EMPTY page, not an
     error.
   * every new route is catalogued in rbac_policy.POLICY.

CI-robust: builds on the in-memory MockDatabase/MockCollection (no live Mongo),
exercises the service + repo layers directly, and asserts on structured values
(no whole-JSON substring matching).

Run: JWT_SECRET_KEY=test python -m pytest \
        backend/tests/test_unification_12_13_tags_collections.py -q
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

import pytest  # noqa: E402

from database.connection import MockDatabase, MockCollection  # noqa: E402
from database.repositories.product_repository import ProductRepository  # noqa: E402
from api.services import product_master as pm  # noqa: E402
from api.services import collection_materializer as mat  # noqa: E402
from api.services import rbac_policy as rbac  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _mirror_off(monkeypatch):
    """Mirror OFF -- step-12/13 are pure spine + materialised-view, no external."""
    monkeypatch.setenv("PM_MIRROR_ENABLED", "")
    monkeypatch.delenv("DISPATCH_MODE", raising=False)
    yield


@pytest.fixture
def product_repo():
    return ProductRepository(MockCollection("products"))


def _seed_product(db, *, sku, category, brand, tags, mrp=5000.0, offer=4500.0):
    """Insert a product spine doc directly into the mock `products` collection,
    normalising tags the same way a canonical create would."""
    db["products"].insert_one(
        {
            "product_id": sku,
            "sku": sku,
            "category": category,
            "brand": brand,
            "attributes": {"brand": brand},
            "mrp": mrp,
            "offer_price": offer,
            "tags": pm.normalise_tags(tags),
            "is_active": True,
        }
    )


def _seed_collection(db, *, handle, ctype="SMART", rules=None, disjunctive=True,
                     products=None):
    cid = f"coll-{handle}"
    db["ecom_collections"].insert_one(
        {
            "collection_id": cid,
            "handle": handle,
            "title": handle.replace("-", " ").title(),
            "collection_type": ctype,
            "rules": rules or [],
            "disjunctive": disjunctive,
            "products": products or [],
            "published": True,
            "sort_priority": 100,
        }
    )
    return cid


# ===========================================================================
# step-12 -- governed tag normalisation
# ===========================================================================


def test_normalise_tags_lowercases_trims_collapses_dedupes_order_preserving():
    out = pm.normalise_tags(["Ray-Ban", "  RAY-BAN ", "Best  Seller", "best seller", ""])
    assert out == ["ray-ban", "best seller"]


def test_normalise_tags_accepts_csv_string():
    assert pm.normalise_tags("New, Bestseller , new") == ["new", "bestseller"]


def test_normalise_tags_failsoft_on_garbage():
    assert pm.normalise_tags(None) == []
    assert pm.normalise_tags(123) == []
    assert pm.normalise_tags({}) == []


def test_tags_normalised_through_canonical_build_door():
    """build_canonical_product folds tags into the spine, normalised + deduped."""
    doc = pm.build_canonical_product(
        {
            "category": "FRAME",
            "sku": "FR-TAG-001",
            "brand": "Ray-Ban",
            "model": "RB-2140",
            "color": "BLK",
            "mrp": 5000.0,
            "offer_price": 4500.0,
            "attributes": {},
            "tags": ["Ray-Ban", "ray-ban", "  NEW  "],
        },
        source="FORM",
    )
    assert doc["tags"] == ["ray-ban", "new"]


def test_tags_identical_across_form_and_catalog_doors():
    """Same tag input -> identical canonical `tags` array regardless of door."""
    form = pm.build_canonical_product(
        {
            "category": "FRAME", "sku": "FR-X", "brand": "B", "model": "M",
            "color": "BLK", "mrp": 100.0, "offer_price": 90.0, "attributes": {},
            "tags": "Sunny, sale ,SUNNY",
        },
        source="FORM",
    )
    catalog = pm.build_canonical_product(
        {
            "category": "FR", "mrp": 100.0, "offer_price": 90.0,
            "attributes": {"brand_name": "B", "model_no": "M", "colour_code": "BLK"},
            "sku": "FR-Y", "tags": ["sunny", "Sale", "sunny"],
        },
        source="CATALOG",
    )
    assert form["tags"] == catalog["tags"] == ["sunny", "sale"]


def test_create_via_door_persists_normalised_tags(product_repo):
    created = pm.create_via_door(
        {
            "category": "FRAME", "sku": "FR-TAG-DOOR", "brand": "Ray-Ban",
            "model": "RB-1", "color": "BLK", "mrp": 5000.0, "offer_price": 4500.0,
            "attributes": {}, "tags": ["RAY-BAN", "ray-ban", "premium"],
        },
        source="FORM",
        actor="tester",
        product_repo=product_repo,
    )
    assert created["tags"] == ["ray-ban", "premium"]
    fetched = product_repo.find_by_sku("FR-TAG-DOOR")
    assert fetched["tags"] == ["ray-ban", "premium"]


def test_tag_filter_predicate_matches_member():
    """The GET /products?tag= filter normalises the requested tag the same way
    and keeps products whose normalised tags contain it."""
    products = [
        {"sku": "A", "tags": ["ray-ban", "new"]},
        {"sku": "B", "tags": ["gucci"]},
        {"sku": "C", "tags": "Ray-Ban,sale"},  # CSV-string tags also match
    ]
    wanted = pm.normalise_tags("Ray-Ban")[0]
    matched = [p["sku"] for p in products if wanted in pm.normalise_tags(p.get("tags"))]
    assert matched == ["A", "C"]


def test_repo_get_tags_builds_unwind_group_pipeline(product_repo, monkeypatch):
    """get_tags builds an unwind+group+sort aggregation and returns the grouped
    `_id`s in order. The MockCollection.aggregate is a no-op stub, so we capture
    the pipeline + feed back the shape real Mongo would (group rows with _id),
    asserting the method's pipeline shape AND its row-projection."""
    captured = {}

    def fake_aggregate(pipeline):
        captured["pipeline"] = pipeline
        # Simulate a $group stage's output (count desc already applied upstream).
        return [{"_id": "ray-ban", "count": 2}, {"_id": "new", "count": 1}]

    monkeypatch.setattr(product_repo, "aggregate", fake_aggregate)
    tags = product_repo.get_tags()
    assert tags == ["ray-ban", "new"]
    stages = [list(s.keys())[0] for s in captured["pipeline"]]
    assert "$unwind" in stages and "$group" in stages and "$limit" in stages

    # A prefix injects a $regex match stage anchored at start-of-string.
    product_repo.get_tags(prefix="Ray")
    regex_stage = next(
        (s for s in captured["pipeline"] if "$match" in s and "tags" in s["$match"]
         and isinstance(s["$match"]["tags"], dict)
         and "$regex" in s["$match"]["tags"]),
        None,
    )
    assert regex_stage is not None
    assert regex_stage["$match"]["tags"]["$regex"] == "^ray"


# ===========================================================================
# step-13 -- materialised smart collections + browse
# ===========================================================================


def _db_with_catalog():
    db = MockDatabase()
    _seed_product(db, sku="RB-1", category="FRAME", brand="Ray-Ban",
                  tags=["ray-ban", "frames"])
    _seed_product(db, sku="SUN-1", category="SUNGLASS", brand="Gucci",
                  tags=["gucci"])
    _seed_product(db, sku="GZ-1", category="FRAME", brand="Gucci",
                  tags=["gucci", "frames"])
    return db


def test_smart_rule_tag_or_category_materialises_correct_membership():
    """Rule: tag EQUALS ray-ban  OR  category EQUALS SUNGLASS (disjunctive).
    RB-1 (tag) + SUN-1 (category) match; GZ-1 (gucci frame) does NOT."""
    db = _db_with_catalog()
    cid = _seed_collection(
        db,
        handle="rayban-or-sunglass",
        rules=[
            {"field": "tag", "relation": "EQUALS", "value": "ray-ban"},
            {"field": "category", "relation": "EQUALS", "value": "SUNGLASS"},
        ],
        disjunctive=True,
    )
    coll = db["ecom_collections"].find_one({"collection_id": cid})
    count = mat.materialize_collection(db, coll)
    assert count == 2
    members = {r["sku"] for r in db["collection_products"].find({"collection_id": cid})}
    assert members == {"RB-1", "SUN-1"}
    # products_count stamped back on the collection.
    assert db["ecom_collections"].find_one({"collection_id": cid})["products_count"] == 2


def test_shopify_shape_rule_normalised_on_materialise():
    """A Shopify-shape rule ({column:VENDOR, condition:Gucci}) resolves via the
    on-the-fly normaliser -> the two Gucci products."""
    db = _db_with_catalog()
    cid = _seed_collection(
        db,
        handle="gucci",
        rules=[{"column": "VENDOR", "relation": "EQUALS", "condition": "Gucci"}],
        disjunctive=False,
    )
    coll = db["ecom_collections"].find_one({"collection_id": cid})
    mat.materialize_collection(db, coll)
    members = {r["sku"] for r in db["collection_products"].find({"collection_id": cid})}
    assert members == {"SUN-1", "GZ-1"}


def test_browse_pages_materialised_membership():
    db = _db_with_catalog()
    cid = _seed_collection(
        db,
        handle="frames",
        rules=[{"field": "tag", "relation": "EQUALS", "value": "frames"}],
        disjunctive=True,
    )
    coll = db["ecom_collections"].find_one({"collection_id": cid})
    mat.materialize_collection(db, coll)

    page1 = mat.browse(db, coll, skip=0, limit=1)
    assert page1["total"] == 2
    assert len(page1["products"]) == 1
    page2 = mat.browse(db, coll, skip=1, limit=1)
    assert len(page2["products"]) == 1
    seen = {page1["products"][0]["sku"], page2["products"][0]["sku"]}
    assert seen == {"RB-1", "GZ-1"}
    # rows are rich (joined to product detail).
    assert page1["products"][0]["brand"] in ("Ray-Ban", "Gucci")


def test_browse_lazy_materialises_on_first_read():
    """A collection never explicitly materialised still browses (lazy first
    compute inside browse())."""
    db = _db_with_catalog()
    cid = _seed_collection(
        db,
        handle="lazy",
        rules=[{"field": "tag", "relation": "EQUALS", "value": "gucci"}],
        disjunctive=True,
    )
    coll = db["ecom_collections"].find_one({"collection_id": cid})
    assert db["collection_products"].count_documents({"collection_id": cid}) == 0
    page = mat.browse(db, coll, skip=0, limit=10)
    # gucci tag is carried by SUN-1 and GZ-1.
    assert page["total"] == 2
    assert {p["sku"] for p in page["products"]} == {"SUN-1", "GZ-1"}


def test_custom_collection_materialises_manual_membership_in_order():
    db = _db_with_catalog()
    cid = _seed_collection(
        db,
        handle="curated",
        ctype="CUSTOM",
        products=[{"sku": "GZ-1", "position": 1}, {"sku": "RB-1", "position": 0}],
    )
    coll = db["ecom_collections"].find_one({"collection_id": cid})
    count = mat.materialize_collection(db, coll)
    assert count == 2
    rows = sorted(
        db["collection_products"].find({"collection_id": cid}),
        key=lambda r: r["position"],
    )
    assert [r["sku"] for r in rows] == ["RB-1", "GZ-1"]  # position order honoured


def test_refresh_for_product_recomputes_smart_membership():
    """A newly-added product carrying the rule tag joins the SMART collection
    after refresh_for_product (the canonical create/update hook)."""
    db = _db_with_catalog()
    cid = _seed_collection(
        db,
        handle="ray-ban",
        rules=[{"field": "tag", "relation": "EQUALS", "value": "ray-ban"}],
        disjunctive=True,
    )
    coll = db["ecom_collections"].find_one({"collection_id": cid})
    mat.materialize_collection(db, coll)
    assert {r["sku"] for r in db["collection_products"].find({"collection_id": cid})} == {"RB-1"}

    # New ray-ban product arrives; the create-hook refreshes SMART collections.
    _seed_product(db, sku="RB-2", category="FRAME", brand="Ray-Ban",
                  tags=["ray-ban"])
    mat.refresh_for_product(db, {"sku": "RB-2"})
    assert {r["sku"] for r in db["collection_products"].find({"collection_id": cid})} == {"RB-1", "RB-2"}


def test_rule_change_recomputes_membership():
    db = _db_with_catalog()
    cid = _seed_collection(
        db,
        handle="switch",
        rules=[{"field": "tag", "relation": "EQUALS", "value": "ray-ban"}],
        disjunctive=True,
    )
    coll = db["ecom_collections"].find_one({"collection_id": cid})
    mat.materialize_collection(db, coll)
    assert {r["sku"] for r in db["collection_products"].find({"collection_id": cid})} == {"RB-1"}

    # Rule edited to target gucci instead; refresh_collection recomputes.
    db["ecom_collections"].update_one(
        {"collection_id": cid},
        {"$set": {"rules": [{"field": "tag", "relation": "EQUALS", "value": "gucci"}]}},
    )
    mat.refresh_collection(db, cid)
    assert {r["sku"] for r in db["collection_products"].find({"collection_id": cid})} == {"SUN-1", "GZ-1"}


def test_empty_collection_browses_empty_not_error():
    db = _db_with_catalog()
    # No-match rule.
    cid = _seed_collection(
        db,
        handle="none",
        rules=[{"field": "tag", "relation": "EQUALS", "value": "does-not-exist"}],
        disjunctive=True,
    )
    coll = db["ecom_collections"].find_one({"collection_id": cid})
    assert mat.materialize_collection(db, coll) == 0
    page = mat.browse(db, coll, skip=0, limit=10)
    assert page == {"products": [], "total": 0, "skip": 0, "limit": 10}

    # No-rules SMART collection -> empty (never match-all).
    cid2 = _seed_collection(db, handle="norules", rules=[], disjunctive=True)
    coll2 = db["ecom_collections"].find_one({"collection_id": cid2})
    assert mat.materialize_collection(db, coll2) == 0


def test_materialize_failsoft_no_db():
    assert mat.materialize_collection(None, {"collection_id": "x"}) == 0
    assert mat.refresh_for_product(None, {}) == {}
    assert mat.refresh_collection(None, "x") == 0
    assert mat.browse(None, {"collection_id": "x"}) == {
        "products": [], "total": 0, "skip": 0, "limit": 24
    }


# ===========================================================================
# Collections Phase 1, Track 1 -- product_id denormalised onto the
# collection_products rows + the new view indexes
# ===========================================================================


def test_materialized_rows_carry_spine_product_id():
    """Every SMART-materialised row carries the `products` spine doc's
    product_id (resolved in one batched find). _seed_product writes
    product_id == sku, so the rows must mirror that."""
    db = _db_with_catalog()
    cid = _seed_collection(
        db,
        handle="frames-pid",
        rules=[{"field": "tag", "relation": "EQUALS", "value": "frames"}],
        disjunctive=True,
    )
    coll = db["ecom_collections"].find_one({"collection_id": cid})
    assert mat.materialize_collection(db, coll) == 2
    rows = list(db["collection_products"].find({"collection_id": cid}))
    assert {r["sku"] for r in rows} == {"RB-1", "GZ-1"}
    for r in rows:
        assert r["product_id"] == r["sku"]


def test_materialized_rows_product_id_none_when_sku_not_on_spine():
    """A catalog_products-only member (no `products` spine doc) still
    materialises -- fail-soft with product_id None, never an error."""
    db = MockDatabase()
    db["catalog_products"].insert_one(
        {
            "sku": "CAT-ONLY",
            "category": "SUNGLASS",
            "attributes": {"brand": "Prada"},
        }
    )
    cid = _seed_collection(
        db,
        handle="cat-only",
        rules=[{"field": "category", "relation": "EQUALS", "value": "SUNGLASS"}],
        disjunctive=True,
    )
    coll = db["ecom_collections"].find_one({"collection_id": cid})
    assert mat.materialize_collection(db, coll) == 1
    row = db["collection_products"].find_one(
        {"collection_id": cid, "sku": "CAT-ONLY"}
    )
    assert row is not None
    assert row["product_id"] is None


def test_custom_collection_rows_also_carry_product_id():
    """The CUSTOM (manual membership) write path denormalises product_id the
    same way; an unresolvable sku writes product_id None (fail-soft)."""
    db = _db_with_catalog()
    cid = _seed_collection(
        db,
        handle="curated-pid",
        ctype="CUSTOM",
        products=[{"sku": "RB-1", "position": 0}, {"sku": "GHOST", "position": 1}],
    )
    coll = db["ecom_collections"].find_one({"collection_id": cid})
    assert mat.materialize_collection(db, coll) == 2
    rows = {r["sku"]: r for r in db["collection_products"].find({"collection_id": cid})}
    assert rows["RB-1"]["product_id"] == "RB-1"
    assert rows["GHOST"]["product_id"] is None


class _RecordingColl:
    """Records create_index calls (keys, kwargs) -- mirrors the fake in
    test_unification_index_backstops.py."""

    def __init__(self, name):
        self.name = name
        self.calls = []  # list of (keys, kwargs)

    def create_index(self, keys, **kw):
        self.calls.append((keys, dict(kw)))
        return "idx"


class _RecordingDB:
    def __init__(self):
        self._colls = {}

    def __getitem__(self, name):
        if name not in self._colls:
            self._colls[name] = _RecordingColl(name)
        return self._colls[name]


def _index_kwargs(coll, keys):
    """Return the kwargs of the create_index call whose keys match, else None."""
    for got_keys, kw in coll.calls:
        if got_keys == keys:
            return kw
    return None


def test_ensure_indexes_builds_product_id_indexes():
    db = _RecordingDB()
    mat.ensure_indexes(db)
    coll = db["collection_products"]

    # New Track-1 indexes: single product_id + {collection_id, product_id}
    # compound, both NON-unique (product_id may be None / repeat).
    kw = _index_kwargs(coll, [("product_id", 1)])
    assert kw is not None, "collection_products.product_id index not built"
    assert kw.get("unique") is not True

    kw = _index_kwargs(coll, [("collection_id", 1), ("product_id", 1)])
    assert kw is not None, "collection_products {collection_id, product_id} index not built"
    assert kw.get("unique") is not True

    # The pre-existing view indexes are still built (additive change).
    assert _index_kwargs(coll, [("collection_id", 1), ("position", 1)]) is not None
    unique_kw = _index_kwargs(coll, [("collection_id", 1), ("sku", 1)])
    assert unique_kw is not None and unique_kw.get("unique") is True
    assert _index_kwargs(coll, [("sku", 1)]) is not None

    # Fail-soft contract: no db -> no raise.
    mat.ensure_indexes(None)


# ===========================================================================
# RBAC cataloguing
# ===========================================================================


def test_new_routes_catalogued_in_policy():
    assert rbac.policy_for("GET", "/api/v1/collections") is not None
    assert rbac.policy_for("GET", "/api/v1/collections/x/products") is not None
    assert rbac.policy_for("POST", "/api/v1/collections/x/refresh") is not None
    assert rbac.policy_for("GET", "/api/v1/products/tags/list") is not None


def test_browse_reads_authenticated_refresh_role_gated():
    # AUTHENTICATED browse: any logged-in role can read.
    assert rbac.check_access("GET", "/api/v1/collections", ["SALES_STAFF"]) is True
    assert rbac.check_access(
        "GET", "/api/v1/collections/x/products", ["SALES_STAFF"]
    ) is True
    # refresh: catalogue roles only.
    assert rbac.check_access(
        "POST", "/api/v1/collections/x/refresh", ["SALES_STAFF"]
    ) is False
    assert rbac.check_access(
        "POST", "/api/v1/collections/x/refresh", ["CATALOG_MANAGER"]
    ) is True
