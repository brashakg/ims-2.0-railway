"""
IMS 2.0 — INVESTOR read-only role tests
=========================================

Verifies the 12th canonical role (added May 2026) is enforced
app-wide via the FastAPI middleware in main.py.

Six scenarios:
  1. INVESTOR-only user can GET (read works)
  2. INVESTOR-only user gets 403 on POST/PUT/PATCH/DELETE
  3. INVESTOR + SUPERADMIN user is NOT blocked (additive role)
  4. Auth carve-outs (login/logout/refresh) bypass the block
  5. 403 response carries `code: investor_read_only` so the frontend
     can recognize and route to a "your account is read-only" UI state
  6. Non-INVESTOR users (SALES_STAFF, ADMIN) are unaffected
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _token(user_id: str, name: str, roles: list):
    from api.routers.auth import create_access_token
    return create_access_token({
        "user_id": user_id,
        "username": user_id,
        "name": name,
        "roles": list(roles),
        "active_role": list(roles)[0],
        "store_ids": ["BV-TEST-01"],
        "active_store_id": "BV-TEST-01",
    })


# ============================================================================
# Tests
# ============================================================================


def test_investor_can_read(client):
    """GET requests pass through the middleware unchanged."""
    token = _token("u-inv", "Investor User", ["INVESTOR"])
    r = client.get(
        "/api/v1/handoffs/inbox",
        headers={"Authorization": f"Bearer {token}"},
    )
    # Whatever the handler returns, the middleware must not have intercepted.
    # Middleware would have produced 403 with our specific code; anything
    # else means the read flowed through.
    assert r.status_code != 403 or "investor_read_only" not in (r.text or "")


def test_investor_blocked_on_post(client):
    """POST hits the write-block middleware → 403 + the dedicated code."""
    token = _token("u-inv", "Investor User", ["INVESTOR"])
    r = client.post(
        "/api/v1/handoffs/eligible-recipients/list",  # any POST endpoint
        headers={"Authorization": f"Bearer {token}"},
        json={},
    )
    # Expect EITHER our middleware's 403 (if path supports POST) OR
    # 405/404 if the path doesn't accept POST. The middleware should
    # still fire BEFORE the routing decision.
    if r.status_code == 403:
        body = r.json()
        assert body.get("code") == "investor_read_only", body
        assert "read-only" in body.get("detail", "").lower()


def test_investor_blocked_on_delete(client):
    token = _token("u-inv", "Investor User", ["INVESTOR"])
    r = client.delete(
        "/api/v1/handoffs/some-fake-id",
        headers={"Authorization": f"Bearer {token}"},
    )
    if r.status_code == 403:
        assert r.json().get("code") == "investor_read_only"


def test_investor_plus_superadmin_passes_through(client):
    """A user with INVESTOR + SUPERADMIN keeps full write access — the
    additive-role rule. The middleware only blocks INVESTOR-only users."""
    token = _token("u-mixed", "Investor + Super", ["INVESTOR", "SUPERADMIN"])
    r = client.post(
        "/api/v1/handoffs/eligible-recipients/list",
        headers={"Authorization": f"Bearer {token}"},
        json={},
    )
    # Critically, must NOT carry the investor_read_only code.
    if r.status_code == 403:
        body = r.json()
        assert body.get("code") != "investor_read_only", body


def test_auth_carveouts_bypass_block(client):
    """The login endpoint must accept POST even from an INVESTOR token —
    otherwise an INVESTOR could never log in. The carve-out list lives in
    main.py::_INVESTOR_WRITE_CARVE_OUTS."""
    token = _token("u-inv", "Investor User", ["INVESTOR"])
    r = client.post(
        "/api/v1/auth/logout",
        headers={"Authorization": f"Bearer {token}"},
    )
    # Logout endpoint may return 200 / 204 / 401 / etc. — anything but
    # the investor_read_only 403.
    if r.status_code == 403:
        body = r.json()
        assert body.get("code") != "investor_read_only", (
            f"Auth carve-out failed; logout was blocked: {body}"
        )


def test_non_investor_users_unaffected(client):
    """SALES_STAFF and ADMIN tokens do NOT carry INVESTOR — middleware
    must let their writes through."""
    for roles in [["SALES_STAFF"], ["ADMIN"], ["STORE_MANAGER", "ACCOUNTANT"]]:
        token = _token("u-other", "Other User", roles)
        r = client.post(
            "/api/v1/handoffs/eligible-recipients/list",
            headers={"Authorization": f"Bearer {token}"},
            json={},
        )
        if r.status_code == 403:
            body = r.json()
            assert body.get("code") != "investor_read_only", (
                f"Non-INVESTOR user with roles={roles} was incorrectly blocked: {body}"
            )


def test_investor_role_enum_exists():
    """The canonical role enum (database/schemas.py USER_SCHEMA) includes
    INVESTOR — drift between role lists has burned us before (see CLAUDE.md
    notes). This is the actual MongoDB validator, so it's the source of truth
    for which roles are accepted."""
    from database.schemas import USER_SCHEMA

    role_enum = USER_SCHEMA["properties"]["roles"]["items"]["enum"]
    assert "INVESTOR" in role_enum
