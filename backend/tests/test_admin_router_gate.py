"""
IMS 2.0 - Admin router role-gate regression test
================================================
Locks SEC-3 (batch-2 launch-hardening): the admin router -- which serves the
integration GET endpoints (Shopify/Shiprocket/Razorpay/WhatsApp/Tally/SMS
config reads) -- must be gated to SUPERADMIN/ADMIN at the router level so that
store-level staff cannot read integration metadata (configured/enabled status,
Shiprocket email, Razorpay key_id prefix), even though secret VALUES are masked.

The gate is the router-level dependency `_require_admin_role` applied via
`APIRouter(dependencies=[Depends(_require_admin_role)])`. These tests assert the
dependency itself rejects non-admin roles and admits SUPERADMIN/ADMIN, and that
every integration GET route is mounted under that gated router (defense against
someone re-adding an un-gated route or dropping the router-level dependency).
"""
from __future__ import annotations

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import HTTPException  # noqa: E402

from api.routers import admin  # noqa: E402


@pytest.mark.parametrize(
    "roles",
    [
        ["WORKSHOP_STAFF"],
        ["SALES_STAFF"],
        ["SALES_CASHIER"],
        ["CASHIER"],
        ["OPTOMETRIST"],
        ["STORE_MANAGER"],
        ["ACCOUNTANT"],
        ["AREA_MANAGER"],
        [],
    ],
)
def test_require_admin_role_rejects_non_admin(roles):
    with pytest.raises(HTTPException) as exc:
        asyncio.run(admin._require_admin_role(current_user={"roles": roles}))
    assert exc.value.status_code == 403


@pytest.mark.parametrize("role", ["SUPERADMIN", "ADMIN"])
def test_require_admin_role_admits_admins(role):
    user = {"roles": [role], "username": "x"}
    result = asyncio.run(admin._require_admin_role(current_user=user))
    assert result is user


def test_admin_router_carries_router_level_admin_gate():
    """The router-level dependency must be present so a newly-added handler that
    forgets its own Depends() is still gated."""
    dep_calls = [getattr(d, "dependency", None) for d in admin.router.dependencies]
    assert admin._require_admin_role in dep_calls


def test_integration_get_routes_are_under_the_gated_router():
    """Every integration GET endpoint (the SEC-3 metadata-exposure surface) is
    served by the gated admin.router, not some un-gated router."""
    get_paths = {
        r.path
        for r in admin.router.routes
        if "GET" in getattr(r, "methods", set())
    }
    # A representative set of the integration metadata GETs flagged in SEC-3.
    for expected in (
        "/integrations/shopify",
        "/integrations/shiprocket",
        "/integrations/razorpay",
    ):
        assert expected in get_paths, f"missing gated GET route: {expected}"
