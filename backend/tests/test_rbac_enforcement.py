"""
IMS 2.0 - Request-time RBAC enforcement middleware
==================================================
Tests the defense-in-depth enforcer in
``api/middleware/rbac_enforcement.py`` against the REAL ``api.main.app`` (so the
middleware is actually in the stack), minting real JWTs the same way the route's
own ``get_current_user`` decodes them.

What we lock here (the middleware's contract):
  * a role-gated route 403s a WRONG role THROUGH the middleware (distinctive
    "Forbidden: <m> <p> requires one of ..." body) and ALLOWS a right role +
    SUPERADMIN;
  * a PUBLIC route is reachable with NO token (the middleware never blocks it);
  * NO token on a gated route still yields the ROUTE's own 401 (not the
    middleware's 403) - so the canonical auth error shape is unchanged;
  * an un-catalogued path FAILS OPEN (the middleware does not 403 it).

Run: ``JWT_SECRET_KEY=test MONGO_HOST=127.0.0.1 python -m pytest \
      backend/tests/test_rbac_enforcement.py -q``
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

import jwt  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import auth as auth_mod  # noqa: E402
from api.services import rbac_policy  # noqa: E402

# NOTE: session-scoped ``app`` and ``client`` fixtures come from tests/conftest.py.

# The middleware's signature 403 body prefix - lets a test prove a 403 came from
# the ENFORCER and not from the route's own gate (which has a different message).
_MW_FORBIDDEN_PREFIX = "Forbidden:"


def _token(roles, uid="rbac-test-1", store_id="BV-TEST-01"):
    """Mint a real JWT exactly as auth.py signs/decodes (same SECRET_KEY +
    ALGORITHM), with the ``roles`` claim get_current_user reads."""
    return jwt.encode(
        {
            "sub": uid,
            "user_id": uid,
            "username": "rbac-tester",
            "roles": roles,
            "store_ids": [store_id],
            "active_store_id": store_id,
            "exp": datetime.utcnow() + timedelta(hours=1),
        },
        auth_mod.SECRET_KEY,
        algorithm=auth_mod.ALGORITHM,
    )


def _auth(roles):
    return {"Authorization": f"Bearer {_token(roles)}"}


# ---------------------------------------------------------------------------
# Role-gated route: 403 (via middleware) for wrong role, allow for right role.
# GET /api/v1/reports/inventory/valuation is finance-roles-only and rejects with
# a plain 403 (NOT self-enforced) -> the enforcer's own 403 is the right answer.
# ---------------------------------------------------------------------------
_GATED = "/api/v1/reports/inventory/valuation"
_GATED_OK_ROLE = "ACCOUNTANT"
_GATED_BAD_ROLE = "SALES_STAFF"


def test_role_gated_wrong_role_403_through_middleware(client):
    r = client.get(_GATED, headers=_auth([_GATED_BAD_ROLE]))
    assert r.status_code == 403
    # Distinctive body proves the ENFORCER produced it, not the route gate.
    detail = r.json().get("detail", "")
    assert detail.startswith(_MW_FORBIDDEN_PREFIX), detail
    assert f"GET {_GATED}" in detail
    # The allow-list roles are named in the message.
    assert "ACCOUNTANT" in detail


def test_role_gated_other_wrong_roles_403(client):
    for role in ("SALES_STAFF", "OPTOMETRIST", "CASHIER", "WORKSHOP_STAFF"):
        r = client.get(_GATED, headers=_auth([role]))
        assert r.status_code == 403, role
        assert r.json().get("detail", "").startswith(_MW_FORBIDDEN_PREFIX), role


def test_role_gated_right_role_allowed(client):
    # A finance role is in the allow-list; must NOT be 403/401 from the enforcer.
    r = client.get(_GATED, headers=_auth([_GATED_OK_ROLE]))
    assert r.status_code != 403, r.text
    assert r.status_code != 401, r.text


def test_jarvis_right_role_superadmin_allowed(client):
    # SUPERADMIN is the explicit allow for jarvis; must reach the handler (200).
    r = client.get("/api/v1/jarvis/agents", headers=_auth(["SUPERADMIN"]))
    assert r.status_code == 200, r.text


def test_superadmin_passes_any_role_gate(client):
    # check_access lets SUPERADMIN through any role-gated route (mirrors
    # require_roles), even one where it is not literally in the allow list.
    r = client.get(
        "/api/v1/reports/inventory/valuation", headers=_auth(["SUPERADMIN"])
    )
    assert r.status_code != 403, r.text


def test_finance_gate_wrong_role_403_right_role_ok(client):
    # Inventory valuation -> finance roles only (router-level finance gate).
    valn = "/api/v1/reports/inventory/valuation"
    bad = client.get(valn, headers=_auth(["SALES_STAFF"]))
    assert bad.status_code == 403
    assert bad.json().get("detail", "").startswith(_MW_FORBIDDEN_PREFIX)
    good = client.get(valn, headers=_auth(["ACCOUNTANT"]))
    assert good.status_code != 403, good.text


# ---------------------------------------------------------------------------
# PUBLIC route: reachable with NO token (middleware must never block it).
# ---------------------------------------------------------------------------

def test_public_route_reachable_without_token(client):
    # Webhook health is PUBLIC -> 200 with no Authorization header.
    r = client.get("/api/v1/webhooks/health")
    assert r.status_code == 200, r.text


def test_public_login_reachable_without_token(client):
    # auth/login is PUBLIC: with no token it reaches the handler and fails on
    # CREDENTIALS (401/422), never the middleware's RBAC 403.
    r = client.post(
        "/api/v1/auth/login", json={"username": "nobody-x", "password": "wrongpw"}
    )
    assert r.status_code in (401, 422)
    assert not r.json().get("detail", "").startswith(_MW_FORBIDDEN_PREFIX)


# ---------------------------------------------------------------------------
# No token on a GATED route -> the ROUTE's own 401, NOT the middleware's 403.
# (Keeps the canonical auth error shape unchanged.)
# ---------------------------------------------------------------------------

def test_no_token_on_gated_route_yields_route_401_not_middleware_403(client):
    r = client.get("/api/v1/jarvis/agents")
    assert r.status_code == 401, r.text
    # The route's get_current_user message, not the middleware's "Forbidden:".
    assert r.json().get("detail") == "Not authenticated"


def test_invalid_token_on_gated_route_yields_route_401(client):
    # A malformed/garbage bearer is not decodable -> middleware passes through ->
    # route's get_current_user returns 401 (never a middleware 403).
    r = client.get(
        "/api/v1/jarvis/agents",
        headers={"Authorization": "Bearer not-a-real-jwt"},
    )
    assert r.status_code == 401
    assert not r.json().get("detail", "").startswith(_MW_FORBIDDEN_PREFIX)


# ---------------------------------------------------------------------------
# Un-catalogued path -> FAIL OPEN (the middleware does not 403 it).
# ---------------------------------------------------------------------------

def test_uncatalogued_path_fails_open(client, monkeypatch):
    """When policy_for returns None (un-catalogued / dynamic route), the
    middleware must NOT 403 - it allows through so the route's own gate decides.
    Proven by: a request the enforcer WOULD normally 403 (wrong role on a gated
    route) no longer carries the middleware's signature 403 once policy_for is
    forced to None."""
    # Baseline: normally this is the middleware's 403.
    baseline = client.get(_GATED, headers=_auth([_GATED_BAD_ROLE]))
    assert baseline.status_code == 403
    assert baseline.json().get("detail", "").startswith(_MW_FORBIDDEN_PREFIX)

    # Force every lookup to "un-catalogued" -> fail open.
    monkeypatch.setattr(rbac_policy, "policy_for", lambda method, path: None)
    failed_open = client.get(_GATED, headers=_auth([_GATED_BAD_ROLE]))
    # The ENFORCER no longer produced its 403; whatever status the route itself
    # returns, it is NOT the middleware's "Forbidden:" body.
    assert not failed_open.json().get("detail", "").startswith(_MW_FORBIDDEN_PREFIX)


def test_uncatalogued_no_token_still_reaches_route(client, monkeypatch):
    """Fail-open + no token -> the route's own auth dependency still runs and
    returns the canonical 401 (the middleware added nothing)."""
    monkeypatch.setattr(rbac_policy, "policy_for", lambda method, path: None)
    r = client.get("/api/v1/jarvis/agents")
    assert r.status_code == 401
    assert r.json().get("detail") == "Not authenticated"


# ---------------------------------------------------------------------------
# self_enforced rows: the enforcer DEFERS on denial so the route's own
# (non-generic) rejection is preserved exactly - 404 existence-hiding for
# jarvis / techcherry, body-specific clinical 403 for prescription create.
# ---------------------------------------------------------------------------

def test_self_enforced_jarvis_defers_to_route_404(client):
    """A wrong role on a jarvis route must get the route's 404 existence-hiding
    response, NOT the middleware's generic 403 (which would leak that the path
    is a real, role-gated endpoint)."""
    r = client.get("/api/v1/jarvis/agents", headers=_auth(["ADMIN"]))
    assert r.status_code == 404, r.text
    assert not r.json().get("detail", "").startswith(_MW_FORBIDDEN_PREFIX)


def test_self_enforced_proposals_defers_to_route_404(client):
    for method, path in (
        ("get", "/api/v1/jarvis/proposals"),
        ("post", "/api/v1/jarvis/proposals/P1/approve"),
        ("post", "/api/v1/jarvis/proposals/P1/reject"),
    ):
        r = getattr(client, method)(path, headers=_auth(["ADMIN"]))
        assert r.status_code == 404, (method, path, r.text)
        assert not r.json().get("detail", "").startswith(_MW_FORBIDDEN_PREFIX)


def test_self_enforced_prescription_create_defers_to_route_clinical_403(client):
    """POST /prescriptions denial keeps the route's body-specific clinical 403,
    not the middleware's generic 'Forbidden:' body."""
    r = client.post(
        "/api/v1/prescriptions",
        headers=_auth(["SALES_STAFF"]),
        json={
            "customer_id": "cust-1",
            "patient_id": "pat-1",
            "source": "TESTED_AT_STORE",
            "right_eye": {"sph": "-2.00", "cyl": "-0.50", "axis": 90},
            "left_eye": {"sph": "-1.50", "cyl": "-0.75", "axis": 85},
        },
    )
    assert r.status_code == 403
    detail = r.json().get("detail", "")
    assert "clinical" in detail.lower(), detail
    assert not detail.startswith(_MW_FORBIDDEN_PREFIX)


def test_self_enforced_flag_present_on_policy_rows():
    """Lock the self_enforced catalogue: jarvis + techcherry families + the two
    prescription-create rows carry the flag; a plain finance route does not."""
    assert rbac_policy.is_self_enforced("GET", "/api/v1/jarvis/agents")
    assert rbac_policy.is_self_enforced("POST", "/api/v1/jarvis/proposals/X/approve")
    assert rbac_policy.is_self_enforced("POST", "/api/v1/admin/techcherry/import")
    assert rbac_policy.is_self_enforced("POST", "/api/v1/prescriptions")
    # A normal finance 403 route must NOT be self-enforced (enforcer handles it).
    assert not rbac_policy.is_self_enforced("GET", _GATED)


# ---------------------------------------------------------------------------
# Corrected policy rows (#356/#360 tightened the routes; policy now matches).
# These assert the ENFORCER blocks the now-stricter roles, mirroring the route.
# ---------------------------------------------------------------------------

def test_corrected_loyalty_add_blocks_sales_staff(client):
    # POST /customers/{id}/loyalty/add is credit-roles only (was AUTHENTICATED).
    r = client.post(
        "/api/v1/customers/CUST-1/loyalty/add?points=5", headers=_auth(["SALES_STAFF"])
    )
    assert r.status_code == 403
    assert r.json().get("detail", "").startswith(_MW_FORBIDDEN_PREFIX)


def test_corrected_marketing_send_blocks_optometrist(client):
    # POST /marketing/notifications/send -> ADMIN/AREA/STORE manager only.
    r = client.post(
        "/api/v1/marketing/notifications/send",
        headers=_auth(["OPTOMETRIST"]),
        json={"customer_phone": "9990001111", "template_id": "t", "channel": "SMS"},
    )
    assert r.status_code == 403
    assert r.json().get("detail", "").startswith(_MW_FORBIDDEN_PREFIX)


def test_corrected_store_category_blocks_store_manager(client):
    # POST /stores/{id}/categories/{cat} is HQ-only (ADMIN); STORE_MANAGER blocked.
    r = client.post(
        "/api/v1/stores/BV-TEST-01/categories/SUNGLASSES",
        headers=_auth(["STORE_MANAGER"]),
    )
    assert r.status_code == 403
    assert r.json().get("detail", "").startswith(_MW_FORBIDDEN_PREFIX)


# ---------------------------------------------------------------------------
# Empty/absent ``roles`` claim on a VALID token: the enforcer must DEFER (never
# substitute a hard 403), so an AUTHENTICATED route still 200s and a role-gated
# route is rejected by its OWN gate -- mirroring the route exactly. (Behavior-
# preservation fix: a zero-role account previously lost AUTHENTICATED reads.)
# ---------------------------------------------------------------------------

_AUTHENTICATED_ROUTE = "/api/v1/notifications/unread-count"


def test_empty_roles_token_defers_on_authenticated_route(client):
    # A valid token whose roles == [] must reach an AUTHENTICATED route, NOT be
    # 403'd by the enforcer. (No DB here -> the route may 200/empty/503, but it
    # must never be the middleware's "Forbidden:" 403, and never 401 since the
    # token is valid.)
    r = client.get(_AUTHENTICATED_ROUTE, headers=_auth([]))
    assert r.status_code != 401, r.text
    assert not r.json().get("detail", "").startswith(_MW_FORBIDDEN_PREFIX), r.text


def test_empty_roles_token_still_blocked_on_gated_route_by_the_route(client):
    # On a ROLE-gated route, an empty-roles token is still rejected -- but by the
    # ROUTE's own require_roles (deferred to), so the body is NOT the enforcer's
    # generic "Forbidden:" prefix. Confirms defer != bypass.
    r = client.get(_GATED, headers=_auth([]))
    assert r.status_code in (401, 403), r.text
    assert not r.json().get("detail", "").startswith(_MW_FORBIDDEN_PREFIX), r.text


# ---------------------------------------------------------------------------
# PUT /prescriptions/{id} is now self_enforced (parity with POST /prescriptions)
# so a wrong role keeps the route's body-specific clinical 403, not the generic
# enforcer 403.
# ---------------------------------------------------------------------------

def test_put_prescription_self_enforced_flag():
    assert rbac_policy.is_self_enforced("PUT", "/api/v1/prescriptions/RX-1")


def test_self_enforced_prescription_update_defers_to_route_clinical_403(client):
    r = client.put(
        "/api/v1/prescriptions/RX-1",
        headers=_auth(["SALES_STAFF"]),
        json={"right_eye": {"sph": "-2.00"}, "validity_months": 12},
    )
    assert r.status_code == 403, r.text
    detail = r.json().get("detail", "")
    assert "clinical" in detail.lower(), detail
    assert not detail.startswith(_MW_FORBIDDEN_PREFIX)
