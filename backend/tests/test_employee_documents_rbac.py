"""Employee-document endpoints are SUPERADMIN/ADMIN ONLY (owner directive).

These endpoints handle SENSITIVE govt-ID PII (Aadhaar/PAN/UAN/ESIC + resume +
photo). The owner restricted the whole feature to SUPERADMIN + ADMIN. This proves
both layers agree: the rbac_enforcement middleware (policy allowed=["ADMIN"]) and
the route-level require_roles("ADMIN") gate. SUPERADMIN auto-passes; every other
role -- including the other HR/finance roles (STORE_MANAGER, ACCOUNTANT,
AREA_MANAGER) -- gets 403.

ASCII only (Windows cp1252).
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from typing import List

import jwt  # noqa: E402
import pytest  # noqa: E402

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402
from api.routers.auth import SECRET_KEY, ALGORITHM  # noqa: E402

_DOC_PATH = "/api/v1/hr/employees/zz-emp-1/documents"
_DOC_ID_PATH = "/api/v1/hr/employees/zz-emp-1/documents/zz-doc-1"


def _mint(roles: List[str]) -> str:
    payload = {
        "sub": "doc-rbac-user",
        "user_id": "doc-rbac-user",
        "username": "doc-rbac-tester",
        "roles": roles,
        "store_ids": ["BV-TEST-01"],
        "active_store_id": "BV-TEST-01",
        "exp": datetime.utcnow() + timedelta(hours=2),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def _hdr(roles: List[str]) -> dict:
    return {"Authorization": f"Bearer {_mint(roles)}"}


@pytest.fixture(scope="module")
def client():
    from api.main import app as _app

    with TestClient(_app, raise_server_exceptions=False) as c:
        yield c


# Roles that MUST be blocked (incl. the other HR/finance roles).
_DENIED = ["STORE_MANAGER", "ACCOUNTANT", "AREA_MANAGER", "SALES_STAFF", "OPTOMETRIST"]
# Roles that MUST pass the auth gate (they then 404/503 with no DB, never 403).
_ALLOWED = ["ADMIN", "SUPERADMIN"]


@pytest.mark.parametrize("role", _DENIED)
def test_denied_roles_get_403_on_list(client, role):
    r = client.get(_DOC_PATH, headers=_hdr([role]))
    assert r.status_code == 403, f"{role} should be 403 on document list, got {r.status_code}"


@pytest.mark.parametrize("role", _DENIED)
def test_denied_roles_get_403_on_download(client, role):
    r = client.get(_DOC_ID_PATH, headers=_hdr([role]))
    assert r.status_code == 403, f"{role} should be 403 on document download, got {r.status_code}"


@pytest.mark.parametrize("role", _DENIED)
def test_denied_roles_get_403_on_delete(client, role):
    r = client.delete(_DOC_ID_PATH, headers=_hdr([role]))
    assert r.status_code == 403, f"{role} should be 403 on document delete, got {r.status_code}"


@pytest.mark.parametrize("role", _ALLOWED)
def test_admin_super_pass_the_role_gate(client, role):
    # Pass the auth gate -> reach the handler -> 404/503 (no DB), but NEVER 401/403.
    r = client.get(_DOC_PATH, headers=_hdr([role]))
    assert r.status_code not in (401, 403), (
        f"{role} must pass the role gate, got {r.status_code}"
    )


def test_no_token_is_401(client):
    r = client.get(_DOC_PATH)
    assert r.status_code == 401
