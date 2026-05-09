"""
IMS 2.0 — Range-wise lens pricing tests
=========================================

Covers the pure resolver in `api/services/lens_pricing.py` plus the
admin-catalog endpoints for the `lens_pricing_ranges` collection.

Per the YouTube competitor research (Optical CRM ships range pricing),
this lands the same capability: tier brackets like "Sphere ±0.00 to
±2.00 = ₹1,200" instead of per-SKU pricing rows.

Twelve scenarios:
  1. resolver: exact_match wins over range_match
  2. resolver: range_match returns highest base_price across multiple matches
  3. resolver: no pricing → ok=False with hint
  4. resolver: brand tier multiplier applies
  5. resolver: index multiplier applies
  6. resolver: coatings sum onto the lens subtotal
  7. resolver: inactive ranges excluded
  8. detect_overlap: same key + overlapping bracket → conflict
  9. detect_overlap: same key + non-overlapping → no conflict
 10. endpoint: create + list + delete (soft) round-trip
 11. endpoint: bulk create stamps ranges
 12. endpoint: overlap on create returns 409
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ============================================================================
# Resolver — pure-function tests (no fixtures needed)
# ============================================================================


def test_exact_match_wins_over_range():
    from api.services.lens_pricing import resolve_price
    quote = resolve_price(
        rx={"sphere": -1.50, "cylinder": 0, "addition": 0},
        brand_id="B1", index_id="I1", category="SINGLE_VISION",
        exact_pricing=[
            {"brandId": "B1", "indexId": "I1", "category": "SINGLE_VISION", "basePrice": 999.0}
        ],
        ranges=[
            {"brand_id": "B1", "index_id": "I1", "category": "SINGLE_VISION",
             "parameter": "sphere", "min_value": 0, "max_value": 2.00,
             "base_price": 1500.0, "is_active": True},
        ],
    )
    assert quote["ok"] is True
    assert quote["source"] == "exact_match"
    assert quote["base_price"] == 999.0


def test_range_match_picks_highest_base_price():
    """When sphere AND cylinder both match different ranges, the
    higher base_price wins (charge for the harder lens)."""
    from api.services.lens_pricing import resolve_price
    quote = resolve_price(
        rx={"sphere": -2.00, "cylinder": -1.50, "addition": 0},
        brand_id="B1", index_id="I1", category="SINGLE_VISION",
        ranges=[
            {"brand_id": "B1", "index_id": "I1", "category": "SINGLE_VISION",
             "parameter": "sphere", "min_value": 0, "max_value": 2.00,
             "base_price": 1200.0, "is_active": True},
            {"brand_id": "B1", "index_id": "I1", "category": "SINGLE_VISION",
             "parameter": "cylinder", "min_value": 0, "max_value": 2.00,
             "base_price": 1800.0, "is_active": True},
        ],
    )
    assert quote["ok"] is True
    assert quote["source"] == "range_match"
    assert quote["base_price"] == 1800.0  # cylinder bracket wins (higher)
    assert len(quote["matched_ranges"]) == 2


def test_no_pricing_returns_ok_false_with_hint():
    from api.services.lens_pricing import resolve_price
    quote = resolve_price(
        rx={"sphere": -1.0},
        brand_id="B-MISSING", index_id="I1", category="SINGLE_VISION",
        exact_pricing=[],
        ranges=[],
    )
    assert quote["ok"] is False
    assert quote["source"] == "no_pricing"
    assert "hint" in quote
    assert "B-MISSING" in quote["hint"]
    assert quote["total_price"] == 0.0


def test_brand_tier_multiplier_applies():
    from api.services.lens_pricing import resolve_price
    base = 1000.0
    rx = {"sphere": 1.0}
    ranges = [{
        "brand_id": "B1", "index_id": "I1", "category": "SINGLE_VISION",
        "parameter": "sphere", "min_value": 0, "max_value": 2.0,
        "base_price": base, "is_active": True,
    }]
    # PREMIUM = 1.2x
    q = resolve_price(
        rx=rx, brand_id="B1", index_id="I1", category="SINGLE_VISION",
        ranges=ranges, brand={"tier": "PREMIUM"},
    )
    assert q["lens_subtotal"] == 1200.0
    # LUXURY = 1.5x
    q = resolve_price(
        rx=rx, brand_id="B1", index_id="I1", category="SINGLE_VISION",
        ranges=ranges, brand={"tier": "LUXURY"},
    )
    assert q["lens_subtotal"] == 1500.0


def test_index_multiplier_applies():
    from api.services.lens_pricing import resolve_price
    rx = {"sphere": 1.0}
    ranges = [{
        "brand_id": "B1", "index_id": "I160", "category": "SINGLE_VISION",
        "parameter": "sphere", "min_value": 0, "max_value": 2.0,
        "base_price": 1000.0, "is_active": True,
    }]
    q = resolve_price(
        rx=rx, brand_id="B1", index_id="I160", category="SINGLE_VISION",
        ranges=ranges, index_master={"value": "1.60", "multiplier": 1.4},
    )
    assert q["lens_subtotal"] == 1400.0


def test_coatings_sum_onto_subtotal():
    from api.services.lens_pricing import resolve_price
    rx = {"sphere": 0.5}
    q = resolve_price(
        rx=rx, brand_id="B1", index_id="I1", category="SINGLE_VISION",
        coatings=["AR", "BLUE_CUT"],
        ranges=[{
            "brand_id": "B1", "index_id": "I1", "category": "SINGLE_VISION",
            "parameter": "sphere", "min_value": 0, "max_value": 2.0,
            "base_price": 1000.0, "is_active": True,
        }],
        coating_masters=[
            {"code": "AR", "price": 200},
            {"code": "BLUE_CUT", "price": 350},
            {"code": "PHOTOCHROMIC", "price": 800},  # not requested
        ],
    )
    assert q["coating_total"] == 550.0
    assert q["total_price"] == 1550.0


def test_inactive_ranges_excluded():
    from api.services.lens_pricing import resolve_price
    q = resolve_price(
        rx={"sphere": 1.0}, brand_id="B1", index_id="I1", category="SINGLE_VISION",
        ranges=[{
            "brand_id": "B1", "index_id": "I1", "category": "SINGLE_VISION",
            "parameter": "sphere", "min_value": 0, "max_value": 2.0,
            "base_price": 1000.0, "is_active": False,
        }],
    )
    assert q["ok"] is False, "Inactive ranges must not match"


def test_overlap_detection_flags_conflicts():
    from api.services.lens_pricing import detect_overlap
    new = {"brand_id": "B1", "index_id": "I1", "category": "SINGLE_VISION",
           "parameter": "sphere", "min_value": 1.0, "max_value": 3.0}
    existing = [{
        "range_id": "R1", "brand_id": "B1", "index_id": "I1", "category": "SINGLE_VISION",
        "parameter": "sphere", "min_value": 2.0, "max_value": 4.0, "is_active": True,
    }]
    assert detect_overlap(new, existing) is not None


def test_overlap_detection_clean_when_no_conflict():
    from api.services.lens_pricing import detect_overlap
    new = {"brand_id": "B1", "index_id": "I1", "category": "SINGLE_VISION",
           "parameter": "sphere", "min_value": 0, "max_value": 1.0}
    existing = [{
        "range_id": "R1", "brand_id": "B1", "index_id": "I1", "category": "SINGLE_VISION",
        "parameter": "sphere", "min_value": 2.0, "max_value": 4.0, "is_active": True,
    }]
    assert detect_overlap(new, existing) is None


# ============================================================================
# Endpoint tests — admin_catalog router
# ============================================================================


@pytest.fixture
def super_token():
    from api.routers.auth import create_access_token
    return create_access_token({
        "user_id": "test-super", "username": "super",
        "roles": ["SUPERADMIN"], "active_role": "SUPERADMIN",
        "store_ids": [], "active_store_id": None,
    })


@pytest.fixture
def patched_admin_catalog(monkeypatch):
    """Wire the admin_catalog router's `_coll` helper to fake collections."""
    from api.routers import admin_catalog as mod

    # Tiny chainable cursor (supports .sort() — _list_envelope uses it)
    class _Cursor:
        def __init__(self, docs):
            self._docs = list(docs)

        def sort(self, *args, **kwargs):
            return self

        def __iter__(self):
            return iter(self._docs)

    class _Coll:
        def __init__(self):
            self.docs = []

        def insert_one(self, doc):
            self.docs.append(dict(doc))
            return type("R", (), {"inserted_id": doc.get("_id")})()

        def find(self, filter_=None):
            def matches(d):
                if not filter_:
                    return True
                for k, v in filter_.items():
                    if d.get(k) != v:
                        return False
                return True
            return _Cursor([d for d in self.docs if matches(d)])

        def find_one(self, filter_=None):
            for d in self.find(filter_):
                return d
            return None

        def update_one(self, filter_, update, upsert=False):
            for d in self.docs:
                if all(d.get(k) == v for k, v in (filter_ or {}).items()):
                    d.update((update or {}).get("$set", {}))
                    return type("R", (), {"modified_count": 1})()
            return type("R", (), {"modified_count": 0})()

        def delete_one(self, filter_):
            for i, d in enumerate(self.docs):
                if all(d.get(k) == v for k, v in (filter_ or {}).items()):
                    self.docs.pop(i)
                    return type("R", (), {"deleted_count": 1})()
            return type("R", (), {"deleted_count": 0})()

    colls: dict = {}

    def _coll_factory(name):
        if name not in colls:
            colls[name] = _Coll()
        return colls[name]

    monkeypatch.setattr(mod, "_coll", _coll_factory)
    return colls


def test_endpoint_create_list_delete_round_trip(client, patched_admin_catalog, super_token):
    headers = {"Authorization": f"Bearer {super_token}"}

    # Create
    r = client.post(
        "/api/v1/admin/lens/pricing-ranges",
        headers=headers,
        json={
            "brand_id": "B1", "index_id": "I1", "category": "SINGLE_VISION",
            "parameter": "sphere", "min_value": 0, "max_value": 2.0,
            "base_price": 1200,
        },
    )
    assert r.status_code == 201, r.text
    range_id = r.json()["range_id"]

    # List
    r2 = client.get("/api/v1/admin/lens/pricing-ranges", headers=headers)
    assert r2.status_code == 200
    body = r2.json()
    assert body["total"] == 1
    assert body["ranges"][0]["range_id"] == range_id

    # Delete (soft — flips is_active=False)
    r3 = client.delete(f"/api/v1/admin/lens/pricing-ranges/{range_id}", headers=headers)
    assert r3.status_code == 200

    # List again — soft-deleted row excluded
    r4 = client.get("/api/v1/admin/lens/pricing-ranges", headers=headers)
    assert r4.json()["total"] == 0


def test_endpoint_overlap_on_create_returns_409(client, patched_admin_catalog, super_token):
    headers = {"Authorization": f"Bearer {super_token}"}

    base = {
        "brand_id": "B1", "index_id": "I1", "category": "SINGLE_VISION",
        "parameter": "sphere",
    }
    r1 = client.post(
        "/api/v1/admin/lens/pricing-ranges",
        headers=headers,
        json={**base, "min_value": 0, "max_value": 2.0, "base_price": 1200},
    )
    assert r1.status_code == 201

    # Overlapping range — same key + overlapping bracket
    r2 = client.post(
        "/api/v1/admin/lens/pricing-ranges",
        headers=headers,
        json={**base, "min_value": 1.5, "max_value": 3.0, "base_price": 1500},
    )
    assert r2.status_code == 409, r2.text


def test_endpoint_bulk_create(client, patched_admin_catalog, super_token):
    headers = {"Authorization": f"Bearer {super_token}"}
    payload = [
        {"brand_id": "B1", "index_id": "I1", "category": "SINGLE_VISION",
         "parameter": "sphere", "min_value": 0, "max_value": 2, "base_price": 1200},
        {"brand_id": "B1", "index_id": "I1", "category": "SINGLE_VISION",
         "parameter": "sphere", "min_value": 2.25, "max_value": 4, "base_price": 1500},
        {"brand_id": "B1", "index_id": "I1", "category": "SINGLE_VISION",
         "parameter": "sphere", "min_value": 4.25, "max_value": 6, "base_price": 2000},
    ]
    r = client.post(
        "/api/v1/admin/lens/pricing-ranges/bulk", headers=headers, json=payload,
    )
    assert r.status_code == 201
    body = r.json()
    assert body["total"] == 3
    assert all("range_id" in x for x in body["created"])
