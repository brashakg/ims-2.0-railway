"""
IMS 2.0 - store-scope + store-config role gate (auth-hardening regression)
==========================================================================
QA role-matrix agents found a cross-store data leak + tampering path:
  - GET /orders (+ sibling list endpoints) took `store_id` from the query with
    NO scope check, so any role could read another store's orders by passing
    ?store_id=.
  - GET /stores/{id}/users and /stats likewise leaked another store's staff PII
    and revenue.
  - PUT /stores/{id} had NO role gate, so a cashier could rewrite/disable any
    store's geo-fence.
These lock the fix: out-of-scope access -> 403 (validate_store_access gates
before any DB call, so these are hermetic), store config -> HQ only, and the
legit paths are not over-restricted.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import orders, stores  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402


def _client(roles, store_ids, active="S1"):
    app = FastAPI()
    app.include_router(orders.router, prefix="/api/v1/orders")
    app.include_router(stores.router, prefix="/api/v1/stores")

    async def _fake_user():
        return {
            "user_id": "u1",
            "full_name": "Test User",
            "roles": roles,
            "store_ids": store_ids,
            "active_store_id": active,
        }

    app.dependency_overrides[get_current_user] = _fake_user
    return TestClient(app)


# --- Cross-store READ leak (the bug) -------------------------------------

def test_area_manager_cannot_list_out_of_region_orders():
    c = _client(["AREA_MANAGER"], ["S1", "S2"], "S1")
    assert c.get("/api/v1/orders", params={"store_id": "S3"}).status_code == 403


def test_store_user_cannot_list_other_store_orders():
    c = _client(["SALES_STAFF"], ["S1"], "S1")
    assert c.get("/api/v1/orders", params={"store_id": "S2"}).status_code == 403


def test_cashier_cannot_read_other_store_users_pii():
    c = _client(["CASHIER"], ["S1"], "S1")
    assert c.get("/api/v1/stores/S2/users").status_code == 403


def test_cashier_cannot_read_other_store_stats():
    c = _client(["CASHIER"], ["S1"], "S1")
    assert c.get("/api/v1/stores/S2/stats").status_code == 403


# --- Cross-store WRITE / config tampering (the bug) ----------------------

def test_cashier_cannot_update_any_store():
    c = _client(["CASHIER"], ["S1"], "S1")
    assert (
        c.put("/api/v1/stores/S2", json={"store_name": "HACKED"}).status_code == 403
    )


def test_store_manager_cannot_update_store_config():
    # Store config is HQ-only (SYSTEM_INTENT s11) — even a STORE_MANAGER is denied.
    c = _client(["STORE_MANAGER"], ["S1"], "S1")
    assert c.put("/api/v1/stores/S1", json={"store_name": "X"}).status_code == 403


# --- NOT over-restricted (legit flows preserved) -------------------------

def test_area_manager_can_list_own_region_orders():
    c = _client(["AREA_MANAGER"], ["S1", "S2"], "S1")
    assert c.get("/api/v1/orders", params={"store_id": "S2"}).status_code == 200


def test_superadmin_can_list_any_store_orders():
    c = _client(["SUPERADMIN"], ["S1"], "S1")
    assert c.get("/api/v1/orders", params={"store_id": "S9"}).status_code == 200


def test_admin_passes_store_config_role_gate():
    # ADMIN must clear the HQ role gate (downstream may 404/empty without a DB;
    # we only assert it's NOT the 403 gate rejection).
    c = _client(["ADMIN"], ["S1"], "S1")
    assert c.put("/api/v1/stores/S1", json={"store_name": "X"}).status_code != 403
