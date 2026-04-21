"""
IMS 2.0 — Non-moving stock report tests (Phase 6.3)
=====================================================
Verifies the /reports/inventory/non-moving-stock endpoint:

  - Auth guard: unauthenticated calls rejected.
  - DB-absent path: returns empty data with correct envelope.
  - Classification logic: products with no orders are surfaced as
    `never_sold: true`; products with a recent order are excluded;
    products with a stale order are included with correct
    `days_since_sold`.
  - Query-param bounds: `days=0` and `days=9999` are rejected (422).
  - Sort order: never-sold floats to the top, then by staleness desc.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ============================================================================
# HTTP-level tests via TestClient
# ============================================================================


class TestAuthAndEnvelope:
    def test_requires_auth(self, client):
        resp = client.get("/api/v1/reports/inventory/non-moving-stock")
        assert resp.status_code == 401

    def test_returns_empty_envelope_when_db_absent(self, client, auth_headers):
        resp = client.get(
            "/api/v1/reports/inventory/non-moving-stock",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == []
        assert body["count"] == 0
        assert body["days_threshold"] == 90
        assert "as_of" in body
        # store_id should reflect the token's active_store_id
        assert body["store_id"] == "BV-TEST-01"

    def test_custom_days_threshold(self, client, auth_headers):
        resp = client.get(
            "/api/v1/reports/inventory/non-moving-stock?days=180",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["days_threshold"] == 180

    def test_days_below_minimum_rejected(self, client, auth_headers):
        resp = client.get(
            "/api/v1/reports/inventory/non-moving-stock?days=0",
            headers=auth_headers,
        )
        assert resp.status_code == 422

    def test_days_above_maximum_rejected(self, client, auth_headers):
        resp = client.get(
            "/api/v1/reports/inventory/non-moving-stock?days=9999",
            headers=auth_headers,
        )
        assert resp.status_code == 422


# ============================================================================
# Classification logic with mocked DB
# ============================================================================


class FakeCollection:
    """Minimal stand-in for a pymongo collection."""
    def __init__(self, docs: List[Dict[str, Any]]):
        self._docs = docs

    def find(self, _filter=None):
        # The endpoint's product query is {"is_active": True} — honor that
        if _filter and "is_active" in _filter:
            return [d for d in self._docs if d.get("is_active") is True]
        return list(self._docs)

    def aggregate(self, pipeline):
        # For the orders aggregation we're called with a pipeline that
        # unwinds items and groups by product_id. We pre-compute the
        # expected result per test case so the fake can just yield it.
        return self._docs


class FakeDB:
    """MongoDB stub wired for the non_moving_stock endpoint."""
    def __init__(self, products, orders_agg_result):
        self.is_connected = True
        self._products = products
        self._orders_agg_result = orders_agg_result

    def get_collection(self, name):
        if name == "products":
            return FakeCollection(self._products)
        if name == "orders":
            return FakeCollection(self._orders_agg_result)
        return FakeCollection([])


@pytest.fixture
def patched_db(monkeypatch):
    """
    Patch backend.api.routers.reports.get_db so the endpoint sees our
    FakeDB instead of hitting real Mongo.
    """
    from api.routers import reports as reports_module

    installed: List[FakeDB] = []

    def install(products, orders_agg):
        db = FakeDB(products, orders_agg)
        installed.append(db)
        monkeypatch.setattr(reports_module, "get_db", lambda: db)
        return db

    return install


def test_never_sold_floats_to_top(client, auth_headers, patched_db):
    """A never-sold product appears first, with never_sold=True."""
    products = [
        {"product_id": "P1", "sku": "BV-BOK-01", "brand": "Ray-Ban",
         "model": "Wayfarer", "category": "frames", "mrp": 7500,
         "is_active": True},
        {"product_id": "P2", "sku": "BV-BOK-02", "brand": "Oakley",
         "model": "Holbrook", "category": "frames", "mrp": 11500,
         "is_active": True},
    ]
    # P1 sold 200 days ago, P2 never sold
    old_ts = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
    orders_agg = [
        {"_id": "P1", "last_sold_at": old_ts, "total_sold": 3},
        # No entry for P2 — simulates never-sold
    ]
    patched_db(products, orders_agg)

    resp = client.get(
        "/api/v1/reports/inventory/non-moving-stock?days=90",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data) == 2
    # P2 (never_sold) should come first
    assert data[0]["sku"] == "BV-BOK-02"
    assert data[0]["never_sold"] is True
    assert data[0]["days_since_sold"] is None
    # P1 should come second
    assert data[1]["sku"] == "BV-BOK-01"
    assert data[1]["never_sold"] is False
    assert data[1]["days_since_sold"] >= 199  # allow for clock drift
    assert data[1]["total_sold_all_time"] == 3


def test_recent_sale_excluded(client, auth_headers, patched_db):
    """A product sold within the threshold is NOT in the stale list."""
    products = [
        {"product_id": "P1", "sku": "BV-BOK-01", "brand": "Ray-Ban",
         "model": "Wayfarer", "category": "frames", "mrp": 7500,
         "is_active": True},
        {"product_id": "P3", "sku": "BV-BOK-03", "brand": "Cartier",
         "model": "Classic", "category": "luxury", "mrp": 85000,
         "is_active": True},
    ]
    recent_ts = (datetime.now(timezone.utc) - timedelta(days=15)).isoformat()
    old_ts = (datetime.now(timezone.utc) - timedelta(days=120)).isoformat()
    orders_agg = [
        {"_id": "P1", "last_sold_at": recent_ts, "total_sold": 2},
        {"_id": "P3", "last_sold_at": old_ts, "total_sold": 1},
    ]
    patched_db(products, orders_agg)

    resp = client.get(
        "/api/v1/reports/inventory/non-moving-stock?days=90",
        headers=auth_headers,
    )
    data = resp.json()["data"]
    skus = {row["sku"] for row in data}
    assert "BV-BOK-01" not in skus, "recently-sold product should be excluded"
    assert "BV-BOK-03" in skus, "sold-120-days-ago product should be included"


def test_days_since_ordered_descending(client, auth_headers, patched_db):
    """Among stale products, the most stale appears first."""
    products = [
        {"product_id": "P1", "sku": "S1", "brand": "B", "is_active": True},
        {"product_id": "P2", "sku": "S2", "brand": "B", "is_active": True},
        {"product_id": "P3", "sku": "S3", "brand": "B", "is_active": True},
    ]
    now = datetime.now(timezone.utc)
    orders_agg = [
        {"_id": "P1", "last_sold_at": (now - timedelta(days=100)).isoformat(), "total_sold": 1},
        {"_id": "P2", "last_sold_at": (now - timedelta(days=250)).isoformat(), "total_sold": 1},
        {"_id": "P3", "last_sold_at": (now - timedelta(days=150)).isoformat(), "total_sold": 1},
    ]
    patched_db(products, orders_agg)

    resp = client.get(
        "/api/v1/reports/inventory/non-moving-stock?days=90",
        headers=auth_headers,
    )
    data = resp.json()["data"]
    assert [row["sku"] for row in data] == ["S2", "S3", "S1"]


def test_inactive_products_ignored(client, auth_headers, patched_db):
    """is_active=False products don't show up even if they've never sold."""
    products = [
        {"product_id": "P1", "sku": "S1", "brand": "B", "is_active": True},
        {"product_id": "P2", "sku": "S2", "brand": "B", "is_active": False},
    ]
    patched_db(products, [])

    resp = client.get(
        "/api/v1/reports/inventory/non-moving-stock?days=90",
        headers=auth_headers,
    )
    data = resp.json()["data"]
    assert len(data) == 1
    assert data[0]["sku"] == "S1"


def test_limit_parameter(client, auth_headers, patched_db):
    """limit caps the result set."""
    products = [
        {"product_id": f"P{i}", "sku": f"S{i}", "is_active": True}
        for i in range(10)
    ]
    patched_db(products, [])

    resp = client.get(
        "/api/v1/reports/inventory/non-moving-stock?days=90&limit=3",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert len(resp.json()["data"]) == 3
