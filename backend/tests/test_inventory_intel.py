"""
IMS 2.0 - inventory intelligence
================================
Pure transfer-recommendation + shrinkage-attribution logic, plus endpoint
gating on the new inventory-intel routes.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.services.inventory_intel import (  # noqa: E402
    recommend_transfers,
    shrinkage_by_custodian,
)
from api.routers import inventory  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402


def test_recommend_transfers_picks_surplus_store():
    low = [{"product_id": "P1", "quantity": 1, "product_name": "Ray-Ban X"}]
    levels = {"P1": {"BLR": 1, "PUN": 40, "MUM": 12}}  # PUN has the most excess
    recs = recommend_transfers("BLR", low, levels, threshold=5)  # target=10
    assert len(recs) == 1
    r = recs[0]
    assert r["from_store"] == "PUN" and r["to_store"] == "BLR"
    assert r["quantity"] == 9  # need = 10-1=9; PUN excess = 40-10=30 -> min=9


def test_recommend_transfers_no_surplus_anywhere():
    low = [{"product_id": "P2", "quantity": 0}]
    levels = {"P2": {"BLR": 0, "PUN": 8}}  # PUN excess = 8-10 < 0
    assert recommend_transfers("BLR", low, levels, threshold=5) == []


def test_recommend_transfers_skips_already_sufficient():
    low = [{"product_id": "P3", "quantity": 12}]  # already above target
    levels = {"P3": {"BLR": 12, "PUN": 40}}
    assert recommend_transfers("BLR", low, levels, threshold=5) == []


def test_shrinkage_by_custodian_attributes():
    counts = [
        {"store_id": "BLR", "audit_number": "A1", "shrinkage_percentage": 4.0, "completed_at": "x"},
        {"store_id": "PUN", "audit_number": "A2", "shrinkage_percentage": 1.0, "completed_at": "y"},
    ]
    custodians = {"BLR": {"staff_id": "u9", "staff_name": "Asha"}}
    rows = shrinkage_by_custodian(counts, custodians)
    by_store = {r["store_id"]: r for r in rows}
    assert by_store["BLR"]["custodian_name"] == "Asha"
    assert by_store["PUN"]["custodian_id"] is None  # unassigned


def _client_as(roles):
    app = FastAPI()
    app.include_router(inventory.router, prefix="/inventory")

    async def _user():
        return {"user_id": "u1", "active_store_id": "BLR", "roles": roles}

    app.dependency_overrides[get_current_user] = _user
    return TestClient(app)


class TestIntelGating:
    def test_sales_staff_blocked_recs(self):
        assert _client_as(["SALES_STAFF"]).get("/inventory/transfer-recommendations").status_code == 403

    def test_sales_staff_blocked_assign(self):
        assert _client_as(["SALES_STAFF"]).post(
            "/inventory/accountability", json={"store_id": "BLR", "staff_id": "u2"}
        ).status_code == 403

    def test_manager_allowed(self):
        assert _client_as(["STORE_MANAGER"]).get("/inventory/transfer-recommendations").status_code != 403
        assert _client_as(["STORE_MANAGER"]).get("/inventory/accountability").status_code != 403
