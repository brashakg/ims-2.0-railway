"""
IMS 2.0 — orders/customers serialization + store-filter regression tests
=========================================================================
Locks two bugs surfaced after the May 2026 TechCherry migration:

  1. `order_to_frontend()` was copying MongoDB's raw `_id` (BSON ObjectId)
     into the response dict. Pydantic/FastAPI's default JSON encoder
     can't serialise ObjectId — every GET /orders that touched a
     TechCherry-imported order 500'd with
        ValueError: [TypeError("'ObjectId' object is not iterable")...]
     Fix: order_to_frontend skips the `_id` key entirely (orders carry
     their own `order_id` / `order_number`).

  2. `list_customers` only filtered by `home_store_id`; TechCherry
     imports use `preferred_store_id`. SUPERADMIN switching to
     BV-PUN-01 in the topbar saw zero of the 5,022 imported customers.
     Fix: list_customers now accepts ?store_id and matches both fields.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

import pytest  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ----- order_to_frontend: ObjectId handling -------------------------------


class TestItemAndPaymentToFrontendObjectIdFix:
    """Regression for item/payment/job_to_frontend — same ObjectId leak
    pattern as order_to_frontend. PR-API-fixes May 2026."""

    def test_item_to_frontend_drops_objectid(self):
        from api.routers.orders import item_to_frontend
        item = {
            "_id": "raw-objectid-stand-in",
            "item_id": "item-001",
            "product_name": "Frame",
            "unit_price": 100,
        }
        result = item_to_frontend(item)
        assert "_id" not in result
        assert result["id"] == "item-001"
        assert result["productName"] == "Frame"

    def test_payment_to_frontend_drops_objectid(self):
        from api.routers.orders import payment_to_frontend
        payment = {
            "_id": "raw-objectid-stand-in",
            "payment_id": "pay-001",
            "method": "CASH",
            "amount": 100,
        }
        result = payment_to_frontend(payment)
        assert "_id" not in result
        assert result["id"] == "pay-001"
        assert result["method"] == "CASH"

    def test_workshop_job_to_frontend_drops_objectid(self):
        from api.routers.workshop import job_to_frontend
        job = {
            "_id": "raw-objectid-stand-in",
            "job_id": "job-001",
            "job_number": "WS-001",
            "store_id": "BV-PUN-01",
            "customer_name": "Anita",
        }
        result = job_to_frontend(job)
        assert "_id" not in result
        assert result["id"] == "job-001"
        assert result["jobNumber"] == "WS-001"
        assert result["storeId"] == "BV-PUN-01"


class TestOrderToFrontendObjectIdFix:
    def test_drops_mongodb_objectid(self):
        """Raw `_id` BSON ObjectId must NOT appear in the response —
        Pydantic can't serialise it."""
        from api.routers.orders import order_to_frontend
        try:
            from bson import ObjectId  # type: ignore
            oid = ObjectId()
        except ImportError:
            # Fallback for environments without bson — simulate the
            # un-serialisable behaviour with a custom class
            class _FakeObjectId:
                pass
            oid = _FakeObjectId()

        order = {
            "_id": oid,                              # ← the killer
            "order_id": "ord-001",
            "order_number": "ORD-2026-001",
            "store_id": "BV-PUN-01",
            "grand_total": 1990.0,
            "status": "DELIVERED",
        }
        result = order_to_frontend(order)
        assert "_id" not in result, "BSON ObjectId leaked through"
        # But the proper identifier survives
        assert result.get("id") == "ord-001"
        assert result.get("orderNumber") == "ORD-2026-001"
        assert result.get("storeId") == "BV-PUN-01"

    def test_preserves_other_fields_unchanged(self):
        """The _id drop must not affect other fields."""
        from api.routers.orders import order_to_frontend
        order = {
            "_id": "some-id",
            "order_id": "ord-002",
            "items": [
                {"product_name": "Frame", "quantity": 1, "unit_price": 100},
            ],
            "grand_total": 100,
        }
        result = order_to_frontend(order)
        assert "_id" not in result
        assert result["id"] == "ord-002"
        assert result["grandTotal"] == 100
        assert len(result["items"]) == 1
        assert result["items"][0].get("productName") == "Frame"


# ----- list_customers: store-filter + dual store-id field handling ------


class TestCustomersStoreFilter:
    def test_store_id_query_param_accepted(self, client, auth_headers):
        """The ?store_id query param must be a valid request (200, not 422)."""
        r = client.get(
            "/api/v1/customers?store_id=BV-PUN-01&limit=3",
            headers=auth_headers,
        )
        assert r.status_code == 200

    def test_response_includes_customers_key(self, client, auth_headers):
        """Frontend reads response.customers OR response.data — keep both."""
        r = client.get("/api/v1/customers?limit=3", headers=auth_headers)
        assert r.status_code == 200
        body = r.json()
        assert "customers" in body
        assert "data" in body
        # The two are the same payload, just backward-compat aliases
        assert body["customers"] == body["data"]
