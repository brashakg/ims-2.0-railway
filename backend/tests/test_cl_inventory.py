"""
IMS 2.0 - Contact-lens (CL) inventory
=====================================
Two things under test:

1. The PURE FEFO + near-expiry helpers in api.routers.inventory:
   - compute_days_until_expiry  (handles ISO string / date / datetime / junk)
   - fefo_sort                  (earliest-expiry-first, undated last, stable)
   - partition_by_expiry        (expired / near / safe / undated bucketing)
   - _group_cl_rows             (per-unit rows -> SKU x batch lines with qty)

2. The CL inventory endpoints' SHAPE with the DB join monkeypatched to a fake,
   so no real Mongo is needed. Mirrors the bare-app override pattern in
   test_clinical_rx.py / test_inventory_gating.py.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, date, timedelta

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import inventory  # noqa: E402
from api.routers.inventory import (  # noqa: E402
    compute_days_until_expiry,
    fefo_sort,
    partition_by_expiry,
    _group_cl_rows,
)
from api.routers.auth import get_current_user  # noqa: E402


# "Now" anchored to TODAY (noon, so the exact day-delta assertions below still
# resolve the same) -- NOT a hard-coded calendar date. The endpoint routes under
# test compute expiry against the real current date, so a fixed base date rots:
# once real-today passed the old base + a near-expiry offset, a "near" batch
# flipped to "expired" and broke the counts. Anchoring to today keeps the seed
# and the endpoint in agreement forever. Day-deltas stay deterministic because
# every _iso(...) offset is relative to this same NOW.
NOW = datetime.now().replace(hour=12, minute=0, second=0, microsecond=0)


def _iso(days_from_now: int) -> str:
    return (NOW + timedelta(days=days_from_now)).date().isoformat()


# ============================================================================
# compute_days_until_expiry -- pure
# ============================================================================


class TestComputeDaysUntilExpiry:
    def test_none_and_blank_return_none(self):
        assert compute_days_until_expiry(None, NOW) is None
        assert compute_days_until_expiry("", NOW) is None

    def test_junk_returns_none(self):
        assert compute_days_until_expiry("not-a-date", NOW) is None

    def test_future_iso_string_positive(self):
        # _iso(30) is a midnight date; NOW is noon -> 29.5 truncates to 29.
        assert compute_days_until_expiry(_iso(30), NOW) == 29

    def test_past_iso_string_negative(self):
        # Past date -> negative day count (expired).
        assert compute_days_until_expiry(_iso(-5), NOW) < 0

    def test_accepts_date_object(self):
        d = (NOW + timedelta(days=10)).date()
        # date is midnight; NOW is noon -> 9.5 days truncates to 9.
        assert compute_days_until_expiry(d, NOW) == 9

    def test_accepts_datetime_object(self):
        dt = NOW + timedelta(days=7)
        assert compute_days_until_expiry(dt, NOW) == 7

    def test_handles_trailing_z(self):
        assert compute_days_until_expiry("2026-06-24T00:00:00Z", NOW) is not None


# ============================================================================
# fefo_sort -- pure (earliest expiry first, undated last)
# ============================================================================


class TestFefoSort:
    def test_orders_earliest_expiry_first(self):
        rows = [
            {"id": "c", "expiry_date": _iso(90)},
            {"id": "a", "expiry_date": _iso(10)},
            {"id": "b", "expiry_date": _iso(45)},
        ]
        out = fefo_sort(rows, NOW)
        assert [r["id"] for r in out] == ["a", "b", "c"]

    def test_undated_rows_sort_last(self):
        rows = [
            {"id": "none", "expiry_date": None},
            {"id": "dated", "expiry_date": _iso(5)},
        ]
        out = fefo_sort(rows, NOW)
        assert [r["id"] for r in out] == ["dated", "none"]

    def test_does_not_mutate_input(self):
        rows = [
            {"id": "c", "expiry_date": _iso(90)},
            {"id": "a", "expiry_date": _iso(10)},
        ]
        original = [r["id"] for r in rows]
        fefo_sort(rows, NOW)
        assert [r["id"] for r in rows] == original

    def test_expired_before_future(self):
        rows = [
            {"id": "future", "expiry_date": _iso(30)},
            {"id": "expired", "expiry_date": _iso(-30)},
        ]
        out = fefo_sort(rows, NOW)
        assert [r["id"] for r in out] == ["expired", "future"]


# ============================================================================
# partition_by_expiry -- pure
# ============================================================================


class TestPartitionByExpiry:
    def _rows(self):
        return [
            {"id": "expired", "expiry_date": _iso(-10)},
            {"id": "near", "expiry_date": _iso(30)},
            {"id": "edge", "expiry_date": _iso(90)},
            {"id": "safe", "expiry_date": _iso(200)},
            {"id": "undated", "expiry_date": None},
        ]

    def test_buckets_by_window(self):
        out = partition_by_expiry(self._rows(), near_days=90, now=NOW)
        assert [r["id"] for r in out["expired"]] == ["expired"]
        assert {r["id"] for r in out["near_expiry"]} == {"near", "edge"}
        assert [r["id"] for r in out["safe"]] == ["safe"]
        assert [r["id"] for r in out["undated"]] == ["undated"]

    def test_window_is_configurable(self):
        out = partition_by_expiry(self._rows(), near_days=15, now=NOW)
        # near=30 now falls OUT of a 15-day window -> safe
        assert {r["id"] for r in out["near_expiry"]} == set()
        assert "near" in {r["id"] for r in out["safe"]}

    def test_each_row_annotated_with_days(self):
        out = partition_by_expiry(self._rows(), near_days=90, now=NOW)
        assert out["near_expiry"][0]["days_until_expiry"] is not None

    def test_near_bucket_sorted_soonest_first(self):
        out = partition_by_expiry(self._rows(), near_days=90, now=NOW)
        days = [r["days_until_expiry"] for r in out["near_expiry"]]
        assert days == sorted(days)


# ============================================================================
# _group_cl_rows -- pure (per-unit -> SKU x batch lines)
# ============================================================================


class TestGroupClRows:
    def test_units_collapse_into_one_line_with_qty(self):
        rows = [
            {"product_id": "p1", "sku": "S", "batch_code": "B1", "expiry_date": _iso(30)},
            {"product_id": "p1", "sku": "S", "batch_code": "B1", "expiry_date": _iso(30)},
            {"product_id": "p1", "sku": "S", "batch_code": "B1", "expiry_date": _iso(30)},
        ]
        out = _group_cl_rows(rows, NOW)
        assert len(out) == 1
        assert out[0]["on_hand"] == 3

    def test_distinct_batches_stay_separate(self):
        rows = [
            {"product_id": "p1", "sku": "S", "batch_code": "B1", "expiry_date": _iso(10)},
            {"product_id": "p1", "sku": "S", "batch_code": "B2", "expiry_date": _iso(80)},
        ]
        out = _group_cl_rows(rows, NOW)
        assert len(out) == 2
        # Earliest-expiry batch first (FEFO ordering on grouped lines).
        assert out[0]["batch_code"] == "B1"

    def test_carries_cl_identity_fields(self):
        rows = [
            {
                "product_id": "p1", "sku": "S", "batch_code": "B1",
                "expiry_date": _iso(30), "brand": "Acuvue", "modality": "DAILY",
                "base_curve": 8.6, "cl_power": -2.0, "pack_size": 30,
            }
        ]
        out = _group_cl_rows(rows, NOW)
        assert out[0]["brand"] == "Acuvue"
        assert out[0]["modality"] == "DAILY"
        assert out[0]["base_curve"] == 8.6
        assert out[0]["pack_size"] == 30


# ============================================================================
# Endpoint shape -- DB join monkeypatched to a fake (no Mongo)
# ============================================================================


def _fake_cl_rows():
    """Two SKUs, three batches, varied expiries -> exercises grouping + FEFO."""
    return [
        # SKU A, batch BA1, near expiry, 2 units
        {"stock_id": "1", "product_id": "pA", "store_id": "store-001", "sku": "ACU-D-2.00",
         "brand": "Acuvue", "model": "Oasys", "category": "CONTACT_LENS",
         "cl_series": "Oasys", "modality": "DAILY", "base_curve": 8.6, "diameter": 14.2,
         "cl_power": -2.0, "cl_cyl": None, "cl_axis": None, "cl_add": None, "color": None,
         "pack_size": 30, "batch_code": "BA1", "expiry_date": _iso(40), "location_code": "A1"},
        {"stock_id": "2", "product_id": "pA", "store_id": "store-001", "sku": "ACU-D-2.00",
         "brand": "Acuvue", "model": "Oasys", "category": "CONTACT_LENS",
         "cl_series": "Oasys", "modality": "DAILY", "base_curve": 8.6, "diameter": 14.2,
         "cl_power": -2.0, "cl_cyl": None, "cl_axis": None, "cl_add": None, "color": None,
         "pack_size": 30, "batch_code": "BA1", "expiry_date": _iso(40), "location_code": "A1"},
        # SKU B, batch BB1, expired, 1 unit
        {"stock_id": "3", "product_id": "pB", "store_id": "store-001", "sku": "BAU-M-1.50",
         "brand": "Bausch", "model": "Ultra", "category": "CONTACT_LENS",
         "cl_series": "Ultra", "modality": "MONTHLY", "base_curve": 8.5, "diameter": 14.2,
         "cl_power": -1.5, "cl_cyl": None, "cl_axis": None, "cl_add": None, "color": None,
         "pack_size": 6, "batch_code": "BB1", "expiry_date": _iso(-10), "location_code": "B1"},
        # SKU B, batch BB2, safe, 1 unit
        {"stock_id": "4", "product_id": "pB", "store_id": "store-001", "sku": "BAU-M-1.50",
         "brand": "Bausch", "model": "Ultra", "category": "CONTACT_LENS",
         "cl_series": "Ultra", "modality": "MONTHLY", "base_curve": 8.5, "diameter": 14.2,
         "cl_power": -1.5, "cl_cyl": None, "cl_axis": None, "cl_add": None, "color": None,
         "pack_size": 6, "batch_code": "BB2", "expiry_date": _iso(200), "location_code": "B1"},
    ]


def _client(monkeypatch, rows, roles=("SALES_STAFF",)):
    app = FastAPI()
    app.include_router(inventory.router, prefix="/inventory")

    async def _fake_user():
        return {"user_id": "u1", "username": "t", "active_store_id": "store-001",
                "roles": list(roles)}

    app.dependency_overrides[get_current_user] = _fake_user
    # Bypass the Mongo join entirely with a deterministic row set.
    monkeypatch.setattr(inventory, "_get_db", lambda: object())
    monkeypatch.setattr(inventory, "_load_cl_stock_rows", lambda db, store: list(rows))
    return TestClient(app)


class TestContactLensListEndpoint:
    def test_groups_units_and_totals(self, monkeypatch):
        client = _client(monkeypatch, _fake_cl_rows())
        resp = client.get("/inventory/contact-lenses")
        assert resp.status_code == 200
        data = resp.json()
        # 3 distinct SKU x batch lines, 4 total units.
        assert data["total_lines"] == 3
        assert data["total_units"] == 4
        # FEFO ordering: expired batch (BB1) first.
        assert data["items"][0]["batch_code"] == "BB1"

    def test_brand_filter(self, monkeypatch):
        client = _client(monkeypatch, _fake_cl_rows())
        resp = client.get("/inventory/contact-lenses?brand=Acuvue")
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert items and all(i["brand"] == "Acuvue" for i in items)

    def test_modality_filter_case_insensitive(self, monkeypatch):
        client = _client(monkeypatch, _fake_cl_rows())
        resp = client.get("/inventory/contact-lenses?modality=monthly")
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert items and all(i["modality"] == "MONTHLY" for i in items)

    def test_near_expiry_days_filter(self, monkeypatch):
        client = _client(monkeypatch, _fake_cl_rows())
        resp = client.get("/inventory/contact-lenses?near_expiry_days=90")
        assert resp.status_code == 200
        items = resp.json()["items"]
        # Only the expired BB1 (-10) and near BA1 (40) qualify; safe BB2 (200) drops.
        skus_batches = {(i["sku"], i["batch_code"]) for i in items}
        assert ("BAU-M-1.50", "BB2") not in skus_batches

    def test_fail_soft_empty_when_no_db(self, monkeypatch):
        app = FastAPI()
        app.include_router(inventory.router, prefix="/inventory")

        async def _fake_user():
            return {"user_id": "u1", "active_store_id": "store-001", "roles": ["SALES_STAFF"]}

        app.dependency_overrides[get_current_user] = _fake_user
        monkeypatch.setattr(inventory, "_get_db", lambda: None)
        c = TestClient(app)
        resp = c.get("/inventory/contact-lenses")
        assert resp.status_code == 200
        assert resp.json()["items"] == []


class TestContactLensExpiryStatusEndpoint:
    def test_buckets_and_fefo_pick(self, monkeypatch):
        client = _client(monkeypatch, _fake_cl_rows())
        resp = client.get("/inventory/contact-lenses/expiry-status?expiring_within_days=90")
        assert resp.status_code == 200
        data = resp.json()
        assert data["summary"]["expired_count"] == 1
        assert data["summary"]["expiring_soon_count"] == 1  # BA1 @ 40 days
        assert data["near_expiry_days"] == 90
        # FEFO pick lists dated, in-stock batches earliest-first.
        assert data["fefo_pick"][0]["batch_code"] == "BB1"

    def test_fail_soft_empty_buckets_when_no_db(self, monkeypatch):
        app = FastAPI()
        app.include_router(inventory.router, prefix="/inventory")

        async def _fake_user():
            return {"user_id": "u1", "active_store_id": "store-001", "roles": ["SALES_STAFF"]}

        app.dependency_overrides[get_current_user] = _fake_user
        monkeypatch.setattr(inventory, "_get_db", lambda: None)
        c = TestClient(app)
        resp = c.get("/inventory/contact-lenses/expiry-status")
        assert resp.status_code == 200
        assert resp.json()["summary"]["expired_count"] == 0


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
