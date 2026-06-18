"""
IMS 2.0 - dashboard widgets: route-level RBAC gate (RBAC-1)
==========================================================
Regression for RBAC-1 (launch-hardening batch 1): the finance/* and hr/*
Hub-widget handlers in dashboard_widgets.py depended only on get_current_user,
so a floor-staff JWT could read store revenue / staff headcount via
GET /api/v1/finance/summary-month (etc.). The only thing stopping it was the
request-time RBAC middleware + frontend hiding -- frontend-hiding is not
protection.

These tests mount ONLY the dashboard_widgets router in a bare FastAPI app with
NO RBAC middleware, proving the gate now lives on the route handler itself
(defense-in-depth): a floor role gets 403, a finance role passes the gate.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

import pytest

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

import jwt  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import dashboard_widgets as dw_mod  # noqa: E402
from api.routers import auth as auth_mod  # noqa: E402


def _token(roles, store_id="BV-PUN-01", uid="u1"):
    return jwt.encode(
        {
            "sub": uid,
            "user_id": uid,
            "username": "tester",
            "roles": list(roles),
            "store_ids": [store_id],
            "active_store_id": store_id,
            "exp": datetime.utcnow() + timedelta(hours=1),
        },
        auth_mod.SECRET_KEY,
        algorithm=auth_mod.ALGORITHM,
    )


@pytest.fixture
def dw_client():
    # Bare app, NO RBAC middleware -> only the route-level gate is exercised.
    app = FastAPI()
    app.include_router(dw_mod.router, prefix="/api/v1")
    return TestClient(app, raise_server_exceptions=False)


# Endpoints that must be gated to finance/HR management roles.
_GATED = [
    "/api/v1/finance/summary-month",
    "/api/v1/finance/gst-status",
    "/api/v1/finance/pending-reconciliations",
    "/api/v1/hr/summary-today",
    "/api/v1/hr/attendance-compliance",
]

_FLOOR_ROLES = ["SALES_STAFF", "CASHIER", "WORKSHOP_STAFF", "OPTOMETRIST"]
_ALLOWED_ROLES = ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER", "SUPERADMIN"]


@pytest.mark.parametrize("path", _GATED)
def test_floor_role_gets_403_on_finance_hr_widgets(dw_client, path):
    """A SALES_STAFF token must be 403'd at the route gate (no middleware here)."""
    r = dw_client.get(path, headers={"Authorization": f"Bearer {_token(['SALES_STAFF'])}"})
    assert r.status_code == 403, f"{path}: expected 403, got {r.status_code} {r.text}"


@pytest.mark.parametrize("role", _FLOOR_ROLES)
def test_all_floor_roles_blocked_on_finance_summary(dw_client, role):
    r = dw_client.get(
        "/api/v1/finance/summary-month",
        headers={"Authorization": f"Bearer {_token([role])}"},
    )
    assert r.status_code == 403, f"{role}: expected 403, got {r.status_code}"


@pytest.mark.parametrize("role", _ALLOWED_ROLES)
def test_management_roles_pass_the_gate(dw_client, role):
    """Allowed roles (and SUPERADMIN) must NOT be 403'd by the gate. The stub
    endpoints return 200; the live ones may 500 on the absent DB, but never the
    authz 401/403."""
    r = dw_client.get(
        "/api/v1/finance/gst-status",  # stub -> 200 once past the gate
        headers={"Authorization": f"Bearer {_token([role])}"},
    )
    assert r.status_code not in (401, 403), f"{role}: gate wrongly blocked ({r.status_code})"
