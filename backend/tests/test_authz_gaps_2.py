"""
IMS 2.0 - Privilege-gap fixes round 2 (RBAC matrix audit)
=========================================================
GET /api/v1/admin/escalations and /api/v1/admin/system-health are defined in
dashboard_widgets (mounted at bare /api/v1), so they BYPASS the admin router's
ADMIN/SUPERADMIN gate that every other /admin/* route has -- they were
AUTHENTICATED-only, leaking cross-store escalated tasks + DB/system status to
any logged-in user. Now Admin-only (SUPERADMIN auto-passes). The Hub fetches
widgets fail-soft, so non-admin roles just get an empty card.

(Note: POST /catalog/online-status was flagged too but is actually a READ -- a
SKU online-status lookup with the list in the body -- so it is intentionally
left ungated; gating it would blank the inventory "Online" column.)
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import dashboard_widgets  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402


def _client(roles):
    app = FastAPI()
    app.include_router(dashboard_widgets.router, prefix="/api/v1")

    async def _u():
        return {"user_id": "u1", "roles": roles, "store_ids": ["S1"], "active_store_id": "S1"}

    app.dependency_overrides[get_current_user] = _u
    return TestClient(app)


def test_admin_escalations_denied_for_sales_staff():
    assert _client(["SALES_STAFF"]).get("/api/v1/admin/escalations").status_code == 403


def test_admin_escalations_denied_for_store_manager():
    # cross-store escalations are admin-only (HQ)
    assert _client(["STORE_MANAGER"]).get("/api/v1/admin/escalations").status_code == 403


def test_admin_escalations_allowed_for_admin():
    assert _client(["ADMIN"]).get("/api/v1/admin/escalations").status_code != 403


def test_system_health_denied_for_sales_staff():
    assert _client(["SALES_STAFF"]).get("/api/v1/admin/system-health").status_code == 403


def test_system_health_allowed_for_admin():
    assert _client(["ADMIN"]).get("/api/v1/admin/system-health").status_code != 403
