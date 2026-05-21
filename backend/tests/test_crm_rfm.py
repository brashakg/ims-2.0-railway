"""
IMS 2.0 — CRM RFM segmentation
==============================
_perform_rfm_segmentation used to return 5 segments with all-zero counts (a
stub), and CustomerSegmentation.tsx rendered a hardcoded SEGMENTS array
(145 Champions, 94% retention, ...). It now computes REAL buckets from the
orders collection (recency/frequency/monetary), matched to customers by
customer_id or normalised phone. These tests drive the bucketing with a fake
orders collection.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

import pytest  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import crm  # noqa: E402


class _Cursor(list):
    def limit(self, _n):
        return self


class _OrdersColl:
    def __init__(self, docs):
        self._docs = docs

    def find(self, _flt=None, _projection=None, projection=None):
        return _Cursor(self._docs)


class _FakeDB:
    def __init__(self, orders):
        self._orders = orders

    def get_collection(self, name):
        return _OrdersColl(self._orders if name == "orders" else [])


def test_rfm_buckets_real_counts(monkeypatch):
    now = datetime.utcnow()
    orders = [
        # c1 -> Champions (3 recent orders)
        {"customer_id": "c1", "grand_total": 10000, "created_at": now - timedelta(days=5)},
        {"customer_id": "c1", "grand_total": 10000, "created_at": now - timedelta(days=10)},
        {"customer_id": "c1", "grand_total": 10000, "created_at": now - timedelta(days=15)},
        # c2 -> Big Spenders (1 order, >= 25k)
        {"customer_id": "c2", "grand_total": 30000, "created_at": now - timedelta(days=20)},
        # c3 -> At Risk (1 small order, 200 days ago)
        {"customer_id": "c3", "grand_total": 2000, "created_at": now - timedelta(days=200)},
        # c4 -> Lost (1 small order, 400 days ago)
        {"customer_id": "c4", "grand_total": 1000, "created_at": now - timedelta(days=400)},
    ]
    customers = [{"customer_id": c} for c in ["c1", "c2", "c3", "c4", "c5"]]  # c5 has no orders

    monkeypatch.setattr(crm, "_crm_get_db", lambda: _FakeDB(orders))
    segments = {s["segment_id"]: s for s in crm._perform_rfm_segmentation(customers)}

    assert segments["champions"]["customer_count"] == 1
    assert segments["champions"]["avg_lifetime_value"] == 30000
    assert segments["big_spenders"]["customer_count"] == 1
    assert segments["at_risk"]["customer_count"] == 1
    assert segments["lost"]["customer_count"] == 1
    assert segments["loyal"]["customer_count"] == 0
    # c5 (no orders) is a prospect, excluded from RFM segments
    total = sum(s["customer_count"] for s in segments.values())
    assert total == 4


def test_rfm_matches_by_phone(monkeypatch):
    now = datetime.utcnow()
    # TechCherry order carries customer_phone, not customer_id
    orders = [
        {"customer_phone": "9876543210", "grand_total": 12000, "created_at": now - timedelta(days=3)},
        {"customer_phone": "9876543210", "grand_total": 12000, "created_at": now - timedelta(days=6)},
        {"customer_phone": "9876543210", "grand_total": 12000, "created_at": now - timedelta(days=9)},
    ]
    customers = [{"customer_id": "cust-x", "mobile": "+91 98765 43210"}]  # matches by normalised phone

    monkeypatch.setattr(crm, "_crm_get_db", lambda: _FakeDB(orders))
    segments = {s["segment_id"]: s for s in crm._perform_rfm_segmentation(customers)}
    assert segments["champions"]["customer_count"] == 1


def test_rfm_empty_without_db(monkeypatch):
    monkeypatch.setattr(crm, "_crm_get_db", lambda: None)
    segments = crm._perform_rfm_segmentation([{"customer_id": "c1"}])
    assert all(s["customer_count"] == 0 for s in segments)
    assert {s["segment_id"] for s in segments} == {
        "champions", "loyal", "big_spenders", "at_risk", "lost",
    }
