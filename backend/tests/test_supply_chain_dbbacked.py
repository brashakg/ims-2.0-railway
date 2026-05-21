"""
IMS 2.0 — supply-chain de-mock + inventory status back-port
===========================================================
supply_chain.py used to serve hardcoded MOCK_POS / MOCK_VENDORS / MOCK_GRNS /
MOCK_STOCK_AUDIT (data that reset on restart; nothing in the frontend
consumed it). It is now DB-backed via the real repositories, fail-soft to
empty. In the no-DB test environment the repos are None, so the endpoints
must return EMPTY envelopes — not the old fabricated rows.

Also locks the back-port of the broad _SOLD_STATUSES set to the inventory
analytics endpoints (/non-moving etc.), which previously matched only
lowercase ["completed","delivered"] and silently missed TechCherry's
uppercase "DELIVERED" sales.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

import pytest  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestSupplyChainDeMocked:
    def test_mock_constants_removed(self):
        import api.routers.supply_chain as m

        for name in ("MOCK_POS", "MOCK_VENDORS", "MOCK_GRNS", "MOCK_STOCK_AUDIT"):
            assert not hasattr(m, name), f"{name} is still defined in supply_chain.py"

    def test_purchase_orders_not_mock(self, client, auth_headers):
        # Was 2 hardcoded POs (PO-2024-001/002). Now DB-backed → empty in no-DB env.
        r = client.get("/api/v1/supply-chain/purchase-orders", headers=auth_headers)
        assert r.status_code == 200
        body = r.json()
        assert body["items"] == []
        assert body["total"] == 0

    def test_vendors_not_mock(self, client, auth_headers):
        r = client.get("/api/v1/supply-chain/vendors", headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["items"] == []

    def test_replenishment_suggestions_not_mock(self, client, auth_headers):
        # Was 2 fabricated suggestions (Frame Model A / Premium Lens Coating).
        r = client.get(
            "/api/v1/supply-chain/replenishment/suggestions", headers=auth_headers
        )
        assert r.status_code == 200
        assert r.json()["items"] == []

    def test_abc_analysis_not_fabricated(self, client, auth_headers):
        # Was hardcoded counts (A:45, B:68, C:187). Now zeroed pending a real pipeline.
        r = client.get(
            "/api/v1/supply-chain/replenishment/abc-analysis", headers=auth_headers
        )
        assert r.status_code == 200
        assert r.json()["analysis"]["A"]["count"] == 0

    def test_audits_not_mock(self, client, auth_headers):
        # Was 1 hardcoded AUDIT-2024-001. Now DB-backed → empty in no-DB env.
        r = client.get("/api/v1/supply-chain/audits", headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["items"] == []


class TestInventoryStatusBackport:
    def test_sold_statuses_covers_uppercase_and_lowercase(self):
        from api.routers.inventory import _SOLD_STATUSES

        # TechCherry imports are stamped uppercase "DELIVERED" — must be matched
        assert "DELIVERED" in _SOLD_STATUSES
        # legacy lowercase variants still matched (no regression vs the old filter)
        assert "delivered" in _SOLD_STATUSES
        assert "completed" in _SOLD_STATUSES
