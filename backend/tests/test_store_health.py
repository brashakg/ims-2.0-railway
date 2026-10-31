"""
IMS 2.0 - Online-store "Store health" readiness checks
======================================================
Two layers:

  1. PURE service tests (no DB) -- lock the fail-soft shape of
     services.store_health and prove each check computes correctly against a
     tiny in-memory fake catalog. Always run.

  2. HTTP endpoint tests -- prove GET /api/v1/online-store/store-health is
     role-gated (SUPERADMIN 200; SALES_STAFF 403), catalogued in
     rbac_policy.POLICY, and returns the documented fail-soft shape (on CI's
     empty mongo it exercises the zeroed envelope, which is what we assert).

Run: JWT_SECRET_KEY=test python -m pytest backend/tests/test_store_health.py -q
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MONGODB_URI", "")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.services import rbac_policy as rbac  # noqa: E402
from api.services import store_health as sh  # noqa: E402
from strict_fakes import StrictDB  # noqa: E402

STORE_HEALTH = "/api/v1/online-store/store-health"


# ---------------------------------------------------------------------------
# In-memory fakes (subscript collection access, like MockDatabase)
# ---------------------------------------------------------------------------


class _FakeColl:
    """A row holder for _FakeDb."""

    def __init__(self, rows=None):
        self._rows = list(rows or [])


def _FakeDb(colls=None):
    """A StrictDB seeded per collection: the shared online rule
    (online_catalog.skus_on_shopify) reads with real $or/$in semantics."""
    db = StrictDB()
    for name, coll in (colls or {}).items():
        db.seed(name, coll._rows)
    return db


# A registry-style SMART collection: the rule reads the tag the push sends.
_RAYBAN = {
    "collection_id": "c-rb", "handle": "ray-ban", "title": "Ray-Ban", "collection_type": "SMART",
    "rules": [{"field": "tag", "relation": "EQUALS", "value": "brand_rayban"}],
}


def _eligible_product(**overrides):
    """A fully-ready online-eligible catalog_products row (has an ecom sub-doc)."""
    doc = {
        "product_id": "P1",
        "sku": "SKU-1",
        "brand": "Ray-Ban",
        "category": "SUNGLASS",
        "hsn_code": "9004",
        "barcode": "BC-1",
        "images": ["https://cdn/x.jpg"],
        "ecom": {"shopify_product_id": "gid://shopify/Product/1",
                 "shopify_variant_id": "gid://shopify/ProductVariant/1"},
    }
    doc.update(overrides)
    return doc


# ---------------------------------------------------------------------------
# PURE service tests -- fail-soft
# ---------------------------------------------------------------------------


def test_envelope_none_db_is_failsoft():
    """No DB -> a fully-zeroed envelope with the documented keys, never raises."""
    out = sh.store_health_envelope(None)
    assert out["readiness_pct"] == 0.0
    assert out["total_products"] == 0
    assert out["fixes_needed"] == []
    assert out["orphans"]["orphan_count"] == 0
    for k in ("hsn_pct", "category_pct", "brand_pct", "barcode_pct", "image_pct", "overall_pct"):
        assert out["coverage"][k] == 0.0
    assert out["barcode_match"]["match_pct"] == 0.0
    assert out["db_connected"] is False


def test_orphan_none_db_zero():
    assert sh.orphan_skus(None)["orphan_count"] == 0


def test_coverage_none_db_zero():
    cov = sh.attribute_coverage(None)
    assert cov["total"] == 0 and cov["overall_pct"] == 0.0


def test_barcode_none_db_zero():
    assert sh.barcode_match_rate(None)["match_pct"] == 0.0


# ---------------------------------------------------------------------------
# PURE service tests -- populated fake catalog
# ---------------------------------------------------------------------------


def test_all_ready_product_scores_100():
    """One perfect product in a collection + mapped -> 100 readiness, no fixes."""
    db = _FakeDb(
        {
            "catalog_products": _FakeColl([_eligible_product()]),
            # THE website membership rule: a Ray-Ban collection holds the live listing.
            "ecom_collections": _FakeColl([_RAYBAN]),
        }
    )
    out = sh.readiness_score(db)
    assert out["total_products"] == 1
    assert out["coverage"]["overall_pct"] == 100.0
    assert out["barcode_match_pct"] == 100.0
    assert out["orphans"]["orphan_count"] == 0
    assert out["readiness_pct"] == 100.0
    assert out["fixes_needed"] == []


def test_attribute_coverage_partial():
    """Two products, one missing HSN + image -> per-attr coverage reflects it."""
    db = _FakeDb(
        {
            "catalog_products": _FakeColl(
                [
                    _eligible_product(product_id="P1", sku="SKU-1"),
                    _eligible_product(
                        product_id="P2",
                        sku="SKU-2",
                        barcode="BC-2",
                        hsn_code="",  # missing HSN
                        images=[],  # missing image
                        image=None,
                        image_url=None,
                    ),
                ]
            )
        }
    )
    cov = sh.attribute_coverage(db)
    assert cov["total"] == 2
    assert cov["category_pct"] == 100.0
    assert cov["brand_pct"] == 100.0
    assert cov["barcode_pct"] == 100.0
    assert cov["hsn_pct"] == 50.0
    assert cov["image_pct"] == 50.0
    assert cov["missing"]["hsn"] == 1
    assert cov["missing"]["image"] == 1


def test_barcode_duplicate_detected():
    """Two products sharing a barcode -> both flagged duplicate, match_pct 0."""
    db = _FakeDb(
        {
            "catalog_products": _FakeColl(
                [
                    _eligible_product(product_id="P1", sku="SKU-1", barcode="DUP"),
                    _eligible_product(product_id="P2", sku="SKU-2", barcode="DUP"),
                ]
            )
        }
    )
    bc = sh.barcode_match_rate(db)
    assert bc["total"] == 2
    assert bc["with_barcode"] == 2
    assert bc["duplicate_barcode"] == 2
    assert bc["unique_matched"] == 0
    assert bc["match_pct"] == 0.0


def test_orphan_reasons_classified():
    """A product with no mapping, not in any collection, and one with no sku."""
    db = _FakeDb(
        {
            "catalog_products": _FakeColl(
                [
                    # mapped + in collection -> ready
                    _eligible_product(product_id="P1", sku="SKU-1"),
                    # no ecom mapping + a brand no collection holds -> orphan (2 reasons)
                    _eligible_product(product_id="P2", sku="SKU-2", brand="Idee", ecom={}),
                    # no sku -> missing_spine orphan
                    _eligible_product(product_id="P3", sku=""),
                ]
            ),
            # THE website membership rule: a Ray-Ban collection holds the live listing.
            "ecom_collections": _FakeColl([_RAYBAN]),
        }
    )
    orph = sh.orphan_skus(db)
    assert orph["total"] == 3
    assert orph["orphan_count"] == 2  # P2 + P3 (P1 is fully ready)
    assert orph["no_mapping"] == 1  # only P2 has an empty ecom (P3 keeps its gid)
    assert orph["missing_spine"] == 1  # P3 has no sku
    # P2 (Idee) is in no collection by the rule; P1 (Ray-Ban) is.
    # P3 has no sku so it is not double-counted as not_in_collection.
    assert orph["not_in_collection"] == 1


def test_fixes_needed_sorted_desc():
    """fixes_needed is ordered largest count first."""
    rows = [
        _eligible_product(product_id=f"P{i}", sku=f"SKU-{i}", hsn_code="")
        for i in range(3)
    ]
    # Only one of them also missing brand.
    rows[0]["brand"] = ""
    db = _FakeDb({"catalog_products": _FakeColl(rows)})
    out = sh.readiness_score(db)
    counts = [f["count"] for f in out["fixes_needed"]]
    assert counts == sorted(counts, reverse=True)
    # HSN missing on all 3 should be the (or a) largest fix.
    assert out["fixes_needed"][0]["count"] == 3


def test_fallback_to_products_when_no_catalog_spine():
    """No catalog_products with ecom -> falls back to active products master."""
    db = _FakeDb(
        {
            "products": _FakeColl(
                [
                    {
                        "product_id": "P1",
                        "sku": "SKU-1",
                        "brand": "Zeiss",
                        "category": "OPTICAL_LENS",
                        "hsn_code": "9001",
                        "barcode": "BC-9",
                        "images": ["u"],
                        "is_active": True,
                    }
                ]
            )
        }
    )
    cov = sh.attribute_coverage(db)
    assert cov["total"] == 1
    assert cov["overall_pct"] == 100.0


def test_a_colour_mapped_on_its_own_variant_row_is_not_unmapped():
    """Owner 2026-10-29: "No Shopify mapping 162" counted only family leads
    (ecom.shopify_product_id). A colour rides its lead's page; it is mapped
    when its own variant is -- online_catalog.skus_on_shopify, the rule the
    sync screen's colour/size parity reads too."""
    lead = _eligible_product(product_id="P1", sku="L", barcode="BC-1")
    kid = _eligible_product(product_id="P2", sku="K", barcode="BC-2", ecom={"variant_of": {"twin_id": "t1"}})
    new = _eligible_product(product_id="P3", sku="N", barcode="BC-3", ecom={"variant_of": {"twin_id": "t1"}})
    db = _FakeDb({
        "catalog_products": _FakeColl([lead, kid, new]),
        "catalog_variants": _FakeColl([{"sku": "K", "parent_product_id": "t1",
                                        "shopify_variant_id": "gid://shopify/ProductVariant/2"}]),
    })
    orph = sh.orphan_skus(db)
    # Collection membership is its own rule (collection_membership); only the
    # mapping reason is under test here.
    unmapped = [o["sku"] for o in orph["orphans"] if "no_mapping" in o["reasons"]]
    assert orph["no_mapping"] == 1 and unmapped == ["N"]


def test_archived_products_are_left_out_of_every_count():
    """Owner 2026-10-29: the "2 products missing an image" were the 2
    archived ones. Archived on the twin or on its spine
    (online_catalog.is_archived_row) -> in no count at all."""
    live = _eligible_product()
    gone_twin = _eligible_product(product_id="P2", sku="SKU-2", barcode="BC-1", images=[], hsn_code="",
                                  ecom={}, deleted_at="2026-10-01")
    gone_spine = _eligible_product(product_id="P3", sku="SKU-3", barcode="", images=[], ecom={})
    db = _FakeDb({
        "catalog_products": _FakeColl([live, gone_twin, gone_spine]),
        "products": _FakeColl([{"product_id": "S3", "sku": "SKU-3", "deleted_at": "2026-10-02"}]),
        # THE website membership rule: the live Ray-Ban listing is in Ray-Ban.
        "ecom_collections": _FakeColl([_RAYBAN]),
    })
    out = sh.readiness_score(db)
    assert out["total_products"] == 1 and out["coverage"]["missing"]["image"] == 0
    assert out["barcode_match"]["duplicate_barcode"] == 0
    assert (out["orphans"]["total"], out["orphans"]["no_mapping"], out["orphans"]["not_in_collection"]) == (1, 0, 0)
    assert out["readiness_pct"] == 100.0 and out["fixes_needed"] == []


def test_an_unreadable_mapping_is_degraded_never_all_unmapped():
    db = _FakeDb({"catalog_products": _FakeColl([_eligible_product()])})

    def dead(*_a, **_k):
        raise RuntimeError("blip")
    db["catalog_variants"].find = dead
    assert sh.store_health_envelope(db)["degraded"] is True


def test_not_in_collection_judges_online_and_offline_by_the_same_rule():
    """The eligible set holds products not on the website yet: an offline
    Ray-Ban the Ray-Ban collection WILL hold is not "in no collection"; an
    offline Idee (no Idee collection) is (review round 2)."""
    db = _FakeDb({
        "catalog_products": _FakeColl([
            _eligible_product(product_id="P2", sku="SKU-2", ecom={}),
            _eligible_product(product_id="P4", sku="SKU-4", brand="Idee", ecom={}),
        ]),
        "ecom_collections": _FakeColl([_RAYBAN]),
    })
    orph = sh.orphan_skus(db)
    assert [o["sku"] for o in orph["orphans"] if "not_in_collection" in o["reasons"]] == ["SKU-4"]


def test_an_unreadable_membership_is_degraded_never_all_orphans(monkeypatch):
    from api.services import collection_membership as cm

    db = _FakeDb({"catalog_products": _FakeColl([_eligible_product()]), "ecom_collections": _FakeColl([_RAYBAN])})

    def dead(*_a, **_k):
        raise RuntimeError("blip")
    monkeypatch.setattr(cm, "skus_in_any_collection", dead)
    assert sh.store_health_envelope(db)["degraded"] is True


# ---------------------------------------------------------------------------
# RBAC policy catalogue (regression lock)
# ---------------------------------------------------------------------------


def test_store_health_is_catalogued_with_ecom_roles():
    entry = rbac.policy_for("GET", STORE_HEALTH)
    assert entry is not None, "store-health not catalogued in rbac_policy.POLICY"
    assert set(entry["allowed"]) == {
        "ADMIN",
        "CATALOG_MANAGER",
        "DESIGN_MANAGER",
        "SUPERADMIN",
    }


def test_check_access_allows_ecom_roles_denies_others():
    for role in ("SUPERADMIN", "ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER"):
        assert rbac.check_access("GET", STORE_HEALTH, [role]) is True, role
    for role in ("SALES_STAFF", "CASHIER", "OPTOMETRIST", "WORKSHOP_STAFF", "ACCOUNTANT"):
        assert rbac.check_access("GET", STORE_HEALTH, [role]) is False, role


# ---------------------------------------------------------------------------
# HTTP endpoint (live app via TestClient) -- fail-soft shape + role gate
# ---------------------------------------------------------------------------


def test_endpoint_mounts_and_returns_shape(client, auth_headers):
    r = client.get(STORE_HEALTH, headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "readiness_pct" in body
    assert "orphans" in body
    assert "coverage" in body
    assert "barcode_match_pct" in body
    assert "fixes_needed" in body
    assert isinstance(body["fixes_needed"], list)
    for k in ("hsn_pct", "category_pct", "brand_pct", "barcode_pct", "image_pct"):
        assert k in body["coverage"]


def test_endpoint_forbidden_for_sales_staff(client, staff_headers):
    r = client.get(STORE_HEALTH, headers=staff_headers)
    assert r.status_code == 403, r.text


def test_endpoint_requires_auth(client):
    r = client.get(STORE_HEALTH)
    assert r.status_code in (401, 403), r.text
