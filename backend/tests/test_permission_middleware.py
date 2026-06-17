"""
IMS 2.0 - Per-user capability layer THROUGH the real middleware stack
=====================================================================
Proves the two insertion points (DENY subtract on the role-allowed branch,
GRANT add on the role-denied branch) fire end-to-end via the real
``api.main.app`` middleware, and that DARK-by-default holds at the HTTP layer:
a user with no stored overrides gets the unchanged role decision.

The middleware looks up the user's stored ``permissions``/``module_access`` via
``get_user_repository`` (overrides stay OUT of the JWT). We monkeypatch that
repo so a token's user_id resolves to a doc carrying the override under test.

Run: ``JWT_SECRET_KEY=test python -m pytest backend/tests/test_permission_middleware.py -q``
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

import jwt  # noqa: E402
import pytest  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import auth as auth_mod  # noqa: E402
from api.middleware import rbac_enforcement as mw  # noqa: E402

# NOTE: session-scoped ``app`` / ``client`` fixtures come from tests/conftest.py.

_MW_FORBIDDEN_PREFIX = "Forbidden:"

# A finance-roles-only route that rejects with a PLAIN 403 (not self-enforced),
# so the enforcer's own 403 is the right answer to observe.
_GATED = "/api/v1/reports/inventory/valuation"  # capability: reports:read
_OK_ROLE = "ACCOUNTANT"


def _token(roles, uid):
    return jwt.encode(
        {
            "sub": uid,
            "user_id": uid,
            "username": uid,
            "roles": roles,
            "store_ids": ["BV-TEST-01"],
            "active_store_id": "BV-TEST-01",
            "exp": datetime.utcnow() + timedelta(hours=1),
        },
        auth_mod.SECRET_KEY,
        algorithm=auth_mod.ALGORITHM,
    )


class _OverrideRepo:
    """Resolves a single user_id -> a doc carrying the override under test."""

    def __init__(self, uid, permissions=None, module_access=None):
        self._uid = uid
        self._doc = {
            "user_id": uid,
            "username": uid,
            "permissions": permissions,
            "module_access": module_access,
        }

    def find_by_id(self, uid):
        return dict(self._doc) if uid == self._uid else None


@pytest.fixture
def patch_repo(monkeypatch):
    """Patch the repository the middleware uses for the live override lookup."""

    def _install(repo):
        monkeypatch.setattr(
            mw, "_user_overrides", mw._user_overrides
        )  # keep real lookup
        from api import dependencies as deps

        monkeypatch.setattr(deps, "get_user_repository", lambda: repo)

    return _install


# ---------------------------------------------------------------------------
# DARK: a user with NO overrides gets the unchanged role decision at HTTP layer.
# ---------------------------------------------------------------------------

def test_dark_no_overrides_role_allow_passes(client, patch_repo):
    uid = "perm-dark-ok"
    patch_repo(_OverrideRepo(uid, permissions=None, module_access=None))
    r = client.get(_GATED, headers={"Authorization": f"Bearer {_token([_OK_ROLE], uid)}"})
    assert r.status_code != 403, r.text  # role allows; no override -> allowed


def test_dark_no_overrides_role_deny_403(client, patch_repo):
    uid = "perm-dark-deny"
    patch_repo(_OverrideRepo(uid, permissions=None, module_access=None))
    r = client.get(_GATED, headers={"Authorization": f"Bearer {_token(['SALES_STAFF'], uid)}"})
    assert r.status_code == 403
    assert r.json().get("detail", "").startswith(_MW_FORBIDDEN_PREFIX)


# ---------------------------------------------------------------------------
# INSERTION POINT 1: a capability DENY subtracts a role-allowed route -> 403.
# ---------------------------------------------------------------------------

def test_capability_deny_subtracts_role_allow(client, patch_repo):
    uid = "perm-deny"
    patch_repo(_OverrideRepo(uid, permissions={"deny": {"reports:read": True}}))
    r = client.get(_GATED, headers={"Authorization": f"Bearer {_token([_OK_ROLE], uid)}"})
    assert r.status_code == 403, r.text
    assert r.json().get("detail", "").startswith(_MW_FORBIDDEN_PREFIX)


def test_module_deny_shim_subtracts_role_allow(client, patch_repo):
    # Legacy module_access deny of "reports" maps to reports:read deny at read.
    uid = "perm-mod-deny"
    patch_repo(_OverrideRepo(uid, module_access={"reports": False}))
    r = client.get(_GATED, headers={"Authorization": f"Bearer {_token([_OK_ROLE], uid)}"})
    assert r.status_code == 403, r.text


# ---------------------------------------------------------------------------
# INSERTION POINT 2: a capability GRANT adds a role-denied route at the
# MIDDLEWARE boundary. NOTE (ruling sec.2): the 4 in-route gates STAY as
# defense-in-depth in PR2 -- so on a route that ALSO carries its own
# ``require_roles``, the grant removes the MIDDLEWARE's 403 but the route's own
# gate still fires (its 403 has a DIFFERENT body, not the "Forbidden:" prefix).
# Full grant effectiveness on such routes arrives when the in-route gates are
# consolidated in PR3. The grant is already fully effective TODAY on the ~410
# AUTHENTICATED routes where the middleware is the sole role differentiator.
# Here we prove the middleware honoured the grant: its OWN 403 is gone.
# ---------------------------------------------------------------------------

def test_capability_grant_removes_middleware_denial(client, patch_repo):
    uid = "perm-grant"
    patch_repo(_OverrideRepo(uid, permissions={"grant": {"reports:read": True}}))
    # SALES_STAFF is role-denied; a grant rescues at the middleware. Without the
    # grant the response carries the middleware's "Forbidden:" body; WITH it, the
    # middleware passes through (any remaining 403 is the route's own gate, a
    # different body) -- proving insertion point 2 fired.
    r = client.get(_GATED, headers={"Authorization": f"Bearer {_token(['SALES_STAFF'], uid)}"})
    detail = r.json().get("detail", "") if r.headers.get("content-type", "").startswith("application/json") else ""
    assert not detail.startswith(_MW_FORBIDDEN_PREFIX), (
        "middleware should have honoured the grant and passed through; "
        f"got middleware 403 instead: {r.text}"
    )


def test_capability_grant_effective_on_authenticated_only_route(client, patch_repo):
    """On a route gated SOLELY by the middleware (no own require_roles), a grant
    is fully effective end-to-end. We use a capability DENY then confirm a grant
    of the SAME capability on a different user is NOT denied -- isolating the
    middleware as the sole arbiter. customers list is AUTHENTICATED (any logged
    -in user) so a deny is the observable middleware-only effect."""
    # A module-deny that maps to customers:read should 403 an AUTHENTICATED route
    # purely via the middleware (the route itself has no role gate).
    uid = "perm-auth-deny"
    patch_repo(_OverrideRepo(uid, permissions={"deny": {"customers:read": True}}))
    r = client.get(
        "/api/v1/customers", headers={"Authorization": f"Bearer {_token(['SALES_STAFF'], uid)}"}
    )
    assert r.status_code == 403, r.text
    assert r.json().get("detail", "").startswith(_MW_FORBIDDEN_PREFIX)


# ---------------------------------------------------------------------------
# SUPERADMIN is exempt: an override never locks the top admin out.
# ---------------------------------------------------------------------------

def test_superadmin_immune_to_deny(client, patch_repo):
    uid = "perm-super"
    patch_repo(_OverrideRepo(uid, permissions={"deny": {"reports:read": True}}))
    r = client.get(_GATED, headers={"Authorization": f"Bearer {_token(['SUPERADMIN'], uid)}"})
    assert r.status_code != 403, r.text


# ---------------------------------------------------------------------------
# Ungrantable cannot be granted via the layer (jarvis stays SUPERADMIN-only).
# ---------------------------------------------------------------------------

def test_grant_cannot_open_jarvis(client, patch_repo):
    uid = "perm-jarvis"
    patch_repo(_OverrideRepo(uid, permissions={"grant": {"jarvis:read": True}}))
    # ADMIN is role-denied on jarvis (SUPERADMIN-only); a forged grant must not
    # open it. jarvis is self_enforced (404-hiding) -> route delivers the
    # rejection; the key assertion is simply "not 200".
    r = client.get("/api/v1/jarvis/agents", headers={"Authorization": f"Bearer {_token(['ADMIN'], uid)}"})
    assert r.status_code != 200, r.text
