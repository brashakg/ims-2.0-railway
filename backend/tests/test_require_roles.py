"""
IMS 2.0 — require_roles RBAC dependency
=======================================
Most sensitive backend routers historically had NO server-side role check —
only frontend route guards — so any authenticated user could call them
directly (e.g. a SALES_STAFF reading the full company P&L via /finance/*).
`require_roles` is the shared enforcement primitive; finance + payroll are
now mounted with it. These tests exercise the dependency directly.
"""

from __future__ import annotations

import asyncio
import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers.auth import require_roles  # noqa: E402


def _call(dep, roles):
    return asyncio.run(dep(current_user={"roles": roles, "user_id": "u1"}))


class TestRequireRoles:
    def test_allowed_role_passes(self):
        dep = require_roles("ADMIN", "ACCOUNTANT")
        user = _call(dep, ["ACCOUNTANT"])
        assert user["user_id"] == "u1"

    def test_superadmin_always_passes(self):
        dep = require_roles("ACCOUNTANT")  # SUPERADMIN not listed
        user = _call(dep, ["SUPERADMIN"])
        assert user is not None

    def test_one_of_multiple_roles_passes(self):
        dep = require_roles("ADMIN", "AREA_MANAGER", "STORE_MANAGER", "ACCOUNTANT")
        assert _call(dep, ["STORE_MANAGER", "SALES_STAFF"])["user_id"] == "u1"

    def test_disallowed_role_403(self):
        dep = require_roles("ADMIN", "ACCOUNTANT")
        with pytest.raises(HTTPException) as exc:
            _call(dep, ["SALES_STAFF"])
        assert exc.value.status_code == 403

    def test_no_roles_403(self):
        dep = require_roles("ADMIN")
        with pytest.raises(HTTPException) as exc:
            _call(dep, [])
        assert exc.value.status_code == 403
