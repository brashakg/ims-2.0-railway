"""
IMS 2.0 - Comprehensive RBAC Access Matrix Test
================================================
Validates the request-time RBAC enforcement middleware
(api/middleware/rbac_enforcement.py) + the declarative policy registry
(api/services/rbac_policy.py) against the REAL app.

DESIGN CONTRACT
---------------
  * Mints real JWTs (same SECRET_KEY/ALGORITHM as auth.py) so decode_token
    accepts them - no mock tricks.
  * Uses TestClient(app) so the middleware IS in the stack.
  * No MongoDB is needed: authz decision runs BEFORE data access, so an
    authorized call returns 200/422/503 (no data) and an unauthorized call
    returns 401/403/404 (blocked before data access).
  * Tests cover ~55 (role, endpoint) pairs across all 11 roles, representative
    sensitive endpoints, PUBLIC paths, self-enforced rows, and no-token behavior.

HOW OUTCOMES ARE ASSERTED
--------------------------
  * policy allows role    -> NOT 401/403 (status in ALLOWED_THROUGH_SET)
  * policy denies role (not self-enforced) -> 403 with middleware "Forbidden:" body
  * policy denies role (self-enforced, jarvis/techcherry) -> 404 (existence-hiding)
  * policy denies role (self-enforced, prescriptions POST) -> 403 with clinical body
  * PUBLIC endpoint + no token -> NOT 401/403
  * AUTHENTICATED/role endpoint + no token -> 401

COVERAGE LIST (method, path, roles_tested)
-------------------------------------------
  GET  /api/v1/health                      -> PUBLIC, no token
  POST /api/v1/auth/login                  -> PUBLIC, no token
  GET  /api/v1/auth/me                     -> AUTHENTICATED; no-token -> 401
  GET  /api/v1/jarvis/agents               -> SUPERADMIN only; others -> 404
  GET  /api/v1/jarvis/agents/activity      -> SUPERADMIN only; others -> 404
  GET  /api/v1/jarvis/status               -> SUPERADMIN only; non-super -> 404
  POST /api/v1/admin/integrations/tally/regenerate -> SUPERADMIN only
  GET  /api/v1/admin/escalations           -> ADMIN only; SUPERADMIN passes; others -> 403
  GET  /api/v1/admin/system-health         -> ADMIN only; others -> 403
  GET  /api/v1/payroll/config              -> finance roles; non-finance -> 403
  POST /api/v1/payroll/run                 -> finance roles; SALES_STAFF -> 403
  GET  /api/v1/finance/cash-flow           -> finance roles; WORKSHOP_STAFF -> 403
  GET  /api/v1/finance/pnl                 -> finance roles; OPTOMETRIST -> 403
  GET  /api/v1/customers/{id}/loyalty/add  -> credit roles; SALES_STAFF -> 403
  POST /api/v1/customers/{id}/store-credit/add -> credit roles; CASHIER -> 403
  POST /api/v1/marketing/notifications/send -> mgmt roles; SALES_STAFF -> 403
  POST /api/v1/marketing/notifications/send-bulk -> mgmt roles; WORKSHOP_STAFF -> 403
  PUT  /api/v1/catalog/products/{id}       -> catalog roles; OPTOMETRIST -> 403
  POST /api/v1/catalog/products            -> catalog roles; CASHIER -> 403
  POST /api/v1/orders                      -> POS/sales roles; ACCOUNTANT -> 403
  POST /api/v1/prescriptions               -> clinical roles (self-enforced); SALES_STAFF -> 403 clinical
  PUT  /api/v1/prescriptions/{id}          -> clinical roles; CASHIER -> 403
  GET  /api/v1/reports/inventory/valuation -> finance roles; SALES_CASHIER -> 403
  GET  /api/v1/reports/gstr1               -> finance roles; SALES_STAFF -> 403
  POST /api/v1/returns                     -> cashier/admin roles; OPTOMETRIST -> 403
  POST /api/v1/users                       -> ADMIN/SUPERADMIN; STORE_MANAGER -> 403
  GET  /api/v1/audit/verify                -> SUPERADMIN; ADMIN -> 403
  GET  /api/v1/analytics-v2/anomaly-detection -> SUPERADMIN; AREA_MANAGER -> 403
  POST /api/v1/loyalty/adjust              -> ADMIN/SUPERADMIN; STORE_MANAGER -> 403
  PUT  /api/v1/loyalty/settings            -> SUPERADMIN; ADMIN -> 403
  GET  /api/v1/admin/techcherry/status     -> SUPERADMIN (self-enforced 404-hiding); ADMIN -> 404
  GET  /api/v1/settings/admin-controls     -> SUPERADMIN; ADMIN -> 403
  GET  /api/v1/hr/attendance               -> HR-mgmt roles; SALES_STAFF -> 403
  GET  /api/v1/hr/leaves                   -> HR-mgmt roles; OPTOMETRIST -> 403
  POST /api/v1/transfers                   -> mgmt+super roles; CASHIER -> 403
  GET  /api/v1/inventory/accountability/shrinkage -> mgmt roles; SALES_STAFF -> 403
  GET  /api/v1/vendors/ap-aging            -> ACCOUNTANT/ADMIN; STORE_MANAGER -> 403
  POST /api/v1/vendors/{vid}/bills         -> ACCOUNTANT/ADMIN; AREA_MANAGER -> 403
  GET  /api/v1/expenses/aging              -> ACCOUNTANT/ADMIN; SALES_CASHIER -> 403

DIVERGENCES DETECTED
--------------------
  See comments inline where discovered. None found = clean.

Run:
  JWT_SECRET_KEY=test-secret-key-for-unit-tests MONGODB_URI="" ENVIRONMENT=test \\
      python -m pytest backend/tests/test_rbac_access_matrix.py -v
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from typing import List

# Must set env before any import of api.main
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")
os.environ.setdefault("ENVIRONMENT", "test")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import jwt  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

# ---------------------------------------------------------------------------
# App + policy imports  (lazy-import to let env vars land first)
# ---------------------------------------------------------------------------
from api.routers.auth import SECRET_KEY, ALGORITHM  # noqa: E402
from api.services.rbac_policy import (  # noqa: E402
    POLICY,
    PUBLIC,
    AUTHENTICATED,
    check_access,
    is_self_enforced,
    policy_for,
    ALL_ROLES,
)

# ---------------------------------------------------------------------------
# TestClient setup
# ---------------------------------------------------------------------------
# We use conftest.py's session-scoped `client` fixture.  However some routes
# hit `MockDatabase.get_collection()` which doesn't exist — this causes an
# unhandled AttributeError that the default TestClient (raise_server_exceptions=
# True) re-raises as a Python exception instead of returning a 500 HTTP response.
#
# For the RBAC matrix tests we care ONLY about the authz outcome (401/403 vs
# anything else), not about DB-level errors after the gate passes.  So we
# provide our own `_matrix_client` fixture with raise_server_exceptions=False
# that converts those crashes into 500 HTTP responses — proving the authz gate
# let the request through.  Tests that assert on _specific_ authz outcomes
# (e.g. 404 existence-hiding) still use the shared `client` fixture so server
# exceptions bubble up if something truly unexpected occurs.


# ---------------------------------------------------------------------------
# Session-scoped "forgiving" client fixture
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def matrix_client():
    """Session-scoped TestClient with raise_server_exceptions=False.

    When MONGODB_URI="" some routes hit MockDatabase.get_collection() which
    doesn't exist — the unhandled AttributeError is a DB-absence crash, NOT
    an authz rejection.  With raise_server_exceptions=False the TestClient
    returns a 500 HTTP response instead of re-raising the Python exception,
    letting us assert "status != 401/403" (authz passed) cleanly.
    """
    from api.main import app as _app
    with TestClient(_app, raise_server_exceptions=False) as c:
        yield c


# ---------------------------------------------------------------------------
# Token minting helpers
# ---------------------------------------------------------------------------

def _mint_token(
    roles: List[str],
    uid: str = "matrix-test-user",
    store_id: str = "BV-MATRIX-01",
) -> str:
    """Sign a real JWT with the same key/algo as auth.py.

    Claim shape mirrors ``get_current_user``/``create_access_token``:
      sub, user_id, username, roles, store_ids, active_store_id, exp
    """
    payload = {
        "sub": uid,
        "user_id": uid,
        "username": "matrix-tester",
        "roles": roles,
        "store_ids": [store_id],
        "active_store_id": store_id,
        "exp": datetime.utcnow() + timedelta(hours=2),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def _auth_header(roles: List[str]) -> dict:
    """Return Authorization header dict for the given roles list."""
    return {"Authorization": f"Bearer {_mint_token(roles)}"}


# Convenience per-role headers (single-role tokens - the realistic case)
ALL_ROLE_HEADERS = {role: _auth_header([role]) for role in ALL_ROLES}

# The middleware's signature 403 body prefix
_MW_FORBIDDEN_PREFIX = "Forbidden:"

# Statuses the middleware/route considers "authorized through" (DB absent ->
# 422/503/404-data-not-found are all acceptable; 400 for bad body is fine too)
_AUTHZ_PASS_STATUSES = {200, 201, 204, 400, 422, 503, 500}
# 404 is included only when we DON'T expect existence-hiding.
# In presence of DB-absent data misses it can also appear - we treat it as
# "through" for non-self-enforced routes.
_AUTHZ_PASS_STATUSES_WITH_404 = _AUTHZ_PASS_STATUSES | {404}


# ---------------------------------------------------------------------------
# Helper assertions
# ---------------------------------------------------------------------------

def assert_middleware_403(response, method: str, path: str) -> None:
    """Assert the middleware returned a proper 403 with its distinctive body."""
    assert response.status_code == 403, (
        f"Expected middleware 403 for {method} {path}, "
        f"got {response.status_code}: {response.text[:200]}"
    )
    detail = response.json().get("detail", "")
    assert detail.startswith(_MW_FORBIDDEN_PREFIX), (
        f"403 body not from middleware for {method} {path}. detail={detail!r}"
    )


def assert_route_allowed(response, role: str, method: str, path: str) -> None:
    """Assert the response is NOT a 401 or 403 (authz blocked).

    500 is also acceptable: some routes throw unhandled AttributeError when
    MONGODB_URI="" and the DB is None/Mock — that is a DB-absence crash, NOT
    an authz rejection. The authz gate ran and passed; the route just crashed
    trying to do DB work. We treat 500 as "authorized through" here.
    """
    assert response.status_code not in (401, 403), (
        f"Role {role!r} should be ALLOWED for {method} {path}, "
        f"got {response.status_code}: {response.text[:300]}"
    )


def assert_existence_hiding(response, role: str, path: str) -> None:
    """Assert 404 (self-enforced existence-hiding jarvis/techcherry pattern)."""
    assert response.status_code == 404, (
        f"Role {role!r} on self-enforced {path!r} "
        f"expected 404 (existence-hiding), got {response.status_code}: {response.text[:200]}"
    )


def assert_clinical_403(response, role: str, path: str) -> None:
    """Assert 403 with body-specific clinical message (prescription POST)."""
    assert response.status_code == 403, (
        f"Role {role!r} on clinical {path!r} expected 403, "
        f"got {response.status_code}: {response.text[:200]}"
    )
    detail = response.json().get("detail", "")
    assert "clinical" in detail.lower() or "role" in detail.lower(), (
        f"Expected clinical 403 body for role {role!r} on {path!r}, "
        f"got detail={detail!r}"
    )


# ===========================================================================
# SECTION 1: PUBLIC endpoints — reachable with NO token
# ===========================================================================

class TestPublicEndpoints:
    """PUBLIC policy rows must be reachable without any token."""

    def test_health_no_token(self, client):
        r = client.get("/api/v1/health")
        assert r.status_code == 200, r.text

    def test_login_no_token(self, client):
        # Reaches the handler — fails on credentials (401/422), never RBAC 403
        r = client.post(
            "/api/v1/auth/login",
            json={"username": "nobody-xyz", "password": "wrongpw123"},
        )
        assert r.status_code in (401, 422), r.text
        detail = r.json().get("detail", "")
        assert not detail.startswith(_MW_FORBIDDEN_PREFIX), (
            f"Login PUBLIC route got middleware 403: {detail!r}"
        )

    def test_webhooks_health_no_token(self, client):
        r = client.get("/api/v1/webhooks/health")
        assert r.status_code == 200, r.text

    def test_clinical_root_no_token(self, client):
        r = client.get("/api/v1/clinical/")
        assert r.status_code in (200, 404), r.text  # 404 = no DB, not RBAC block
        assert r.status_code != 403, r.text

    def test_inventory_root_no_token(self, client):
        r = client.get("/api/v1/inventory/")
        assert r.status_code not in (401, 403), r.text

    def test_seed_database_public(self, client):
        # Needs SEED_SECRET body; reaches handler (422/403-from-handler) -- not middleware 403
        r = client.post("/api/v1/admin/seed-database", json={})
        # May 422 (missing SEED_SECRET), 401 (wrong secret), or 200 -- never middleware 403
        detail = r.json().get("detail", "") if r.headers.get("content-type", "").startswith("application/json") else ""
        assert not detail.startswith(_MW_FORBIDDEN_PREFIX), (
            f"seed-database (PUBLIC) got middleware 403: {detail!r}"
        )


# ===========================================================================
# SECTION 2: AUTHENTICATED routes — any role passes, no-token -> 401
# ===========================================================================

class TestAuthenticatedEndpoints:
    """AUTHENTICATED policy rows: any role can reach them; missing token -> 401.

    Uses matrix_client (raise_server_exceptions=False) because some routes
    hit MockDatabase.get_collection() which is absent in stub mode and causes
    an AttributeError that would otherwise be re-raised as a Python exception.
    """

    def test_me_no_token_401(self, client):
        r = client.get("/api/v1/auth/me")
        assert r.status_code == 401, r.text

    def test_me_with_any_role(self, matrix_client):
        for role in ALL_ROLES:
            r = matrix_client.get("/api/v1/auth/me", headers=ALL_ROLE_HEADERS[role])
            assert r.status_code not in (401, 403), (
                f"GET /auth/me: role {role!r} should be AUTHENTICATED-allowed, "
                f"got {r.status_code}"
            )

    def test_customers_list_all_roles(self, matrix_client):
        """GET /customers is AUTHENTICATED — every role passes."""
        for role in ALL_ROLES:
            r = matrix_client.get("/api/v1/customers", headers=ALL_ROLE_HEADERS[role])
            assert r.status_code not in (401, 403), (
                f"GET /customers: role {role!r} got {r.status_code}"
            )

    def test_orders_list_all_roles(self, matrix_client):
        """GET /orders is AUTHENTICATED — every role passes (store-scoped in handler)."""
        for role in ALL_ROLES:
            r = matrix_client.get("/api/v1/orders", headers=ALL_ROLE_HEADERS[role])
            assert r.status_code not in (401, 403), (
                f"GET /orders: role {role!r} got {r.status_code}"
            )

    def test_prescriptions_list_all_roles(self, matrix_client):
        """GET /prescriptions is CLINICAL-RESTRICTED (require_rx_read).

        Prescriptions carry medical data + PII, so only clinical / POS-fulfilment
        / workshop / management roles may read them; non-clinical roles (CASHIER
        payment-only, ACCOUNTANT, CATALOG_MANAGER, INVENTORY_HQ) get 403. The
        expectation is driven by the source-of-truth _RX_READ_ROLES allow-list so
        the test and the gate can never drift.
        """
        from api.routers.prescriptions import _RX_READ_ROLES

        for role in ALL_ROLES:
            r = matrix_client.get("/api/v1/prescriptions", headers=ALL_ROLE_HEADERS[role])
            if role in _RX_READ_ROLES:
                assert r.status_code not in (401, 403), (
                    f"GET /prescriptions: clinical role {role!r} should be allowed, "
                    f"got {r.status_code}"
                )
            else:
                assert r.status_code == 403, (
                    f"GET /prescriptions: non-clinical role {role!r} should be 403, "
                    f"got {r.status_code}"
                )

    def test_catalog_products_get_all_roles(self, matrix_client):
        """GET /catalog/products is AUTHENTICATED (may 500 with no DB - that's allowed through)."""
        for role in ALL_ROLES:
            r = matrix_client.get("/api/v1/catalog/products", headers=ALL_ROLE_HEADERS[role])
            assert r.status_code not in (401, 403), (
                f"GET /catalog/products: role {role!r} got {r.status_code}"
            )

    def test_notifications_unread_count_all_roles(self, matrix_client):
        """GET /notifications/unread-count is AUTHENTICATED."""
        for role in ALL_ROLES:
            r = matrix_client.get(
                "/api/v1/notifications/unread-count",
                headers=ALL_ROLE_HEADERS[role],
            )
            assert r.status_code not in (401, 403), (
                f"GET /notifications/unread-count: role {role!r} got {r.status_code}"
            )

    def test_workshop_jobs_list_all_roles(self, matrix_client):
        """GET /workshop/jobs is AUTHENTICATED."""
        for role in ALL_ROLES:
            r = matrix_client.get("/api/v1/workshop/jobs", headers=ALL_ROLE_HEADERS[role])
            assert r.status_code not in (401, 403), (
                f"GET /workshop/jobs: role {role!r} got {r.status_code}"
            )


# ===========================================================================
# SECTION 3: No-token on gated routes -> 401 (route's own gate)
# ===========================================================================

class TestNoTokenYields401:
    """Missing token on role-gated routes must yield the route's 401,
    NOT the middleware's 403."""

    @pytest.mark.parametrize("path,method", [
        ("/api/v1/jarvis/agents", "GET"),
        ("/api/v1/admin/escalations", "GET"),
        ("/api/v1/payroll/config", "GET"),
        ("/api/v1/finance/cash-flow", "GET"),
        ("/api/v1/audit/verify", "GET"),
        ("/api/v1/customers/CUST-001/loyalty/add", "POST"),
        ("/api/v1/marketing/notifications/send", "POST"),
        ("/api/v1/reports/inventory/valuation", "GET"),
        ("/api/v1/users/", "GET"),
    ])
    def test_no_token_yields_401(self, client, path, method):
        r = client.request(method, path)
        assert r.status_code == 401, (
            f"No-token on {method} {path}: expected 401, got {r.status_code}: {r.text[:200]}"
        )
        detail = r.json().get("detail", "")
        assert not detail.startswith(_MW_FORBIDDEN_PREFIX), (
            f"No-token on {method} {path} got middleware 403 instead of route 401: {detail!r}"
        )


# ===========================================================================
# SECTION 4: JARVIS (self-enforced, SUPERADMIN-only, 404 existence-hiding)
# ===========================================================================

class TestJarvisSuperadminOnly:
    """Jarvis endpoints: SUPERADMIN -> 200, ALL others -> 404 (not 403!).
    self_enforced = True means the middleware DEFERS to the route gate,
    which deliberately returns 404 to hide existence."""

    _JARVIS_PATHS = [
        "/api/v1/jarvis/agents",
        "/api/v1/jarvis/agents/activity",
        "/api/v1/jarvis/status",
        "/api/v1/jarvis/dashboard",
        "/api/v1/jarvis/agents/diagnostic",
    ]

    def test_superadmin_reaches_jarvis_agents(self, client):
        r = client.get("/api/v1/jarvis/agents", headers=ALL_ROLE_HEADERS["SUPERADMIN"])
        assert r.status_code == 200, (
            f"SUPERADMIN on /jarvis/agents expected 200, got {r.status_code}: {r.text[:300]}"
        )

    @pytest.mark.parametrize("path", _JARVIS_PATHS)
    @pytest.mark.parametrize("role", [
        "ADMIN", "AREA_MANAGER", "STORE_MANAGER", "ACCOUNTANT", "CATALOG_MANAGER",
        "OPTOMETRIST", "SALES_CASHIER", "SALES_STAFF", "CASHIER", "WORKSHOP_STAFF",
    ])
    def test_non_superadmin_gets_404_on_jarvis(self, matrix_client, role, path):
        """Non-SUPERADMIN must get 404 (existence-hiding), NOT 200 or 403."""
        r = matrix_client.get(path, headers=ALL_ROLE_HEADERS[role])
        assert_existence_hiding(r, role, path)

    def test_jarvis_agents_activity_superadmin(self, matrix_client):
        r = matrix_client.get(
            "/api/v1/jarvis/agents/activity",
            headers=ALL_ROLE_HEADERS["SUPERADMIN"],
        )
        assert r.status_code in _AUTHZ_PASS_STATUSES_WITH_404, (
            f"SUPERADMIN on /jarvis/agents/activity got {r.status_code}: {r.text[:200]}"
        )
        assert r.status_code not in (401, 403)


# ===========================================================================
# SECTION 5: admin/techcherry (self-enforced, SUPERADMIN-only, 404-hiding)
# ===========================================================================

class TestTechCherrySuperadminOnly:
    """admin/techcherry/* mirrors jarvis: SUPERADMIN -> pass; non-SA -> 404."""

    @pytest.mark.parametrize("role", [
        "ADMIN", "AREA_MANAGER", "STORE_MANAGER", "ACCOUNTANT",
        "CATALOG_MANAGER", "OPTOMETRIST", "SALES_STAFF", "WORKSHOP_STAFF",
    ])
    def test_non_superadmin_gets_404_on_techcherry(self, matrix_client, role):
        r = matrix_client.get(
            "/api/v1/admin/techcherry/status",
            headers=ALL_ROLE_HEADERS[role],
        )
        assert_existence_hiding(r, role, "/api/v1/admin/techcherry/status")

    def test_superadmin_reaches_techcherry(self, client):
        r = client.get(
            "/api/v1/admin/techcherry/status",
            headers=ALL_ROLE_HEADERS["SUPERADMIN"],
        )
        # Passes authz; may 200/503 depending on DB; must not be 401/403/404
        assert r.status_code not in (401, 403, 404), (
            f"SUPERADMIN on techcherry/status got {r.status_code}: {r.text[:200]}"
        )


# ===========================================================================
# SECTION 6: ADMIN-only routes
# ===========================================================================

class TestAdminOnlyRoutes:
    """Routes that allow only ADMIN (not SUPERADMIN-explicit in the list).
    SUPERADMIN always passes via check_access logic."""

    # Policy: ['ADMIN'] only
    _ADMIN_ONLY_PATHS = [
        ("GET", "/api/v1/admin/escalations"),
        ("GET", "/api/v1/admin/system-health"),
    ]

    @pytest.mark.parametrize("method,path", _ADMIN_ONLY_PATHS)
    def test_admin_allowed(self, matrix_client, method, path):
        r = matrix_client.request(method, path, headers=ALL_ROLE_HEADERS["ADMIN"])
        assert_route_allowed(r, "ADMIN", method, path)

    @pytest.mark.parametrize("method,path", _ADMIN_ONLY_PATHS)
    def test_superadmin_allowed_on_admin_only(self, matrix_client, method, path):
        """SUPERADMIN always passes, even for 'ADMIN'-only rows."""
        r = matrix_client.request(method, path, headers=ALL_ROLE_HEADERS["SUPERADMIN"])
        assert_route_allowed(r, "SUPERADMIN", method, path)

    @pytest.mark.parametrize("method,path", _ADMIN_ONLY_PATHS)
    @pytest.mark.parametrize("role", [
        "AREA_MANAGER", "STORE_MANAGER", "ACCOUNTANT", "CATALOG_MANAGER",
        "OPTOMETRIST", "SALES_CASHIER", "SALES_STAFF", "CASHIER", "WORKSHOP_STAFF",
    ])
    def test_non_admin_403_on_admin_only(self, client, method, path, role):
        r = client.request(method, path, headers=ALL_ROLE_HEADERS[role])
        assert_middleware_403(r, method, path)


# ===========================================================================
# SECTION 7: Finance/HR/Payroll — finance-roles only
# ===========================================================================

class TestFinancePayrollHrRoutes:
    """Routes restricted to ACCOUNTANT, ADMIN, AREA_MANAGER, STORE_MANAGER.

    DIVERGENCE NOTES (two real policy/route mismatches discovered by this suite):
    ---------------------------------------------------------------------------
    * POST /api/v1/payroll/run:
        Policy says allowed: ['ACCOUNTANT', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER']
        Route gate (_RUN_ROLES = ("ADMIN", "ACCOUNTANT")) denies AREA_MANAGER+STORE_MANAGER
        => Live app 403s them; policy is too permissive.

    * GET /api/v1/expenses/aging:
        Policy says allowed: ['ACCOUNTANT', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER']
        Route gate (_ACCOUNTANT_ROLES = ("ADMIN", "ACCOUNTANT")) denies AREA_MANAGER+STORE_MANAGER
        => Live app 403s them (with middleware 'Forbidden:' body!); policy too permissive.

    These are captured as xfail tests below (NOT loosened — kept as policy divergence evidence).
    """

    _FINANCE_ROLES = {"ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"}
    _NON_FINANCE_ROLES = set(ALL_ROLES) - _FINANCE_ROLES

    # Paths where ALL 4 finance roles are genuinely allowed by the route gate
    _FINANCE_PATHS_ALL_4_ALLOWED = [
        ("GET", "/api/v1/finance/cash-flow"),
        ("GET", "/api/v1/finance/pnl"),
        ("GET", "/api/v1/payroll/config"),
        ("GET", "/api/v1/hr/attendance"),
        ("GET", "/api/v1/hr/leaves"),
        ("GET", "/api/v1/reports/gstr1"),
        ("GET", "/api/v1/reports/inventory/valuation"),
        ("GET", "/api/v1/reports/finance/gst"),
    ]

    # All paths (including divergent ones) for the non-finance denial test
    _FINANCE_PATHS = _FINANCE_PATHS_ALL_4_ALLOWED + [
        ("POST", "/api/v1/payroll/run"),
        ("GET", "/api/v1/expenses/aging"),
    ]

    @pytest.mark.parametrize("method,path", _FINANCE_PATHS_ALL_4_ALLOWED)
    @pytest.mark.parametrize("role", ["ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"])
    def test_finance_role_allowed(self, matrix_client, method, path, role):
        r = matrix_client.request(method, path, headers=ALL_ROLE_HEADERS[role])
        assert_route_allowed(r, role, method, path)

    # --- Reconciled rows (formerly xfail divergences) ---------------------
    #
    # These were previously xfail "policy too permissive" markers. The policy
    # rows have now been TIGHTENED to mirror the route gates exactly, so the
    # CORRECT behavior is a middleware 'Forbidden:' 403 for the roles the route
    # does not allow, and an authorized pass for the roles it does. The live app
    # is ground truth here: if a "denied" assert ever fails, the row was
    # over-tightened and must be reverted.

    # GET /expenses/aging  — route gate _ACCOUNTANT_ROLES=(ADMIN,ACCOUNTANT);
    # policy row is ['ACCOUNTANT','ADMIN'] (always was). AREA_MANAGER and
    # STORE_MANAGER are correctly DENIED by the middleware (no bug — the old
    # xfail was a stale-copy misread).
    @pytest.mark.parametrize("role", ["AREA_MANAGER", "STORE_MANAGER"])
    def test_expenses_aging_denied_for_manager_roles(self, client, role):
        r = client.get("/api/v1/expenses/aging", headers=ALL_ROLE_HEADERS[role])
        assert_middleware_403(r, "GET", "/api/v1/expenses/aging")

    @pytest.mark.parametrize("role", ["ACCOUNTANT", "ADMIN"])
    def test_expenses_aging_allowed_for_accountant_admin(self, matrix_client, role):
        r = matrix_client.get("/api/v1/expenses/aging", headers=ALL_ROLE_HEADERS[role])
        assert_route_allowed(r, role, "GET", "/api/v1/expenses/aging")

    # POST /payroll/run  — route gate _RUN_ROLES=(ADMIN,ACCOUNTANT); policy row
    # now TIGHTENED to ['ACCOUNTANT','ADMIN']. AREA_MANAGER + STORE_MANAGER are
    # correctly DENIED by the middleware (the real divergence is now fixed).
    @pytest.mark.parametrize("role", ["AREA_MANAGER", "STORE_MANAGER"])
    def test_payroll_run_denied_for_manager_roles(self, client, role):
        r = client.post(
            "/api/v1/payroll/run",
            headers=ALL_ROLE_HEADERS[role],
            json={"month": 5, "year": 2026},
        )
        assert_middleware_403(r, "POST", "/api/v1/payroll/run")

    @pytest.mark.parametrize("role", ["ACCOUNTANT", "ADMIN"])
    def test_payroll_run_allowed_for_accountant_admin(self, matrix_client, role):
        r = matrix_client.post(
            "/api/v1/payroll/run",
            headers=ALL_ROLE_HEADERS[role],
            json={"month": 5, "year": 2026},
        )
        assert_route_allowed(r, role, "POST", "/api/v1/payroll/run")

    # --- Tightened-payroll-row self-check (live app is ground truth) -------
    #
    # Every payroll policy row that was tightened from the 4-role _FINANCE set
    # down to the route's stricter gate. Each REMOVED role must be denied by the
    # live app (middleware 'Forbidden:' 403, since the route-level require_roles
    # is shadowed one layer up by the policy), and each KEPT role must be
    # authorized through. If a REMOVED role is actually allowed by the live
    # route, the denied-assert fails and the row must be reverted.
    #
    # (method, path, kept_roles, removed_roles, body) — confirmed against the
    # require_roles(...) gate in backend/api/routers/payroll.py:
    #   require_roles("ADMIN")             -> kept {ADMIN}
    #   require_roles(*_RUN_ROLES)         -> kept {ADMIN, ACCOUNTANT}
    _TIGHTENED_PAYROLL_ROWS = [
        # require_roles("ADMIN") rows -> only ADMIN (+SUPERADMIN) kept
        ("POST", "/api/v1/payroll/config", ["ADMIN"],
         ["ACCOUNTANT", "AREA_MANAGER", "STORE_MANAGER"], {"employee_id": "EMP-DUMMY-1"}),
        ("POST", "/api/v1/payroll/config/bulk", ["ADMIN"],
         ["ACCOUNTANT", "AREA_MANAGER", "STORE_MANAGER"], {"configs": []}),
        ("PUT", "/api/v1/payroll/config/EMP-DUMMY-1", ["ADMIN"],
         ["ACCOUNTANT", "AREA_MANAGER", "STORE_MANAGER"], {"employee_id": "EMP-DUMMY-1"}),
        ("POST", "/api/v1/payroll/lock", ["ADMIN"],
         ["ACCOUNTANT", "AREA_MANAGER", "STORE_MANAGER"], {"month": 5, "year": 2026}),
        ("POST", "/api/v1/payroll/pt-slabs/seed", ["ADMIN"],
         ["ACCOUNTANT", "AREA_MANAGER", "STORE_MANAGER"], None),
        ("PUT", "/api/v1/payroll/pt-slabs/JH", ["ADMIN"],
         ["ACCOUNTANT", "AREA_MANAGER", "STORE_MANAGER"], {"slabs": []}),
        # require_roles(*_RUN_ROLES=(ADMIN,ACCOUNTANT)) rows -> ADMIN+ACCOUNTANT kept
        ("POST", "/api/v1/payroll/approve", ["ACCOUNTANT", "ADMIN"],
         ["AREA_MANAGER", "STORE_MANAGER"], {"month": 5, "year": 2026}),
        ("POST", "/api/v1/payroll/run", ["ACCOUNTANT", "ADMIN"],
         ["AREA_MANAGER", "STORE_MANAGER"], {"month": 5, "year": 2026}),
        ("GET", "/api/v1/payroll/tally/salary-jv", ["ACCOUNTANT", "ADMIN"],
         ["AREA_MANAGER", "STORE_MANAGER"], None),
        ("GET", "/api/v1/payroll/registers/pf-ecr", ["ACCOUNTANT", "ADMIN"],
         ["AREA_MANAGER", "STORE_MANAGER"], None),
    ]

    @pytest.mark.parametrize(
        "method,path,kept,removed,body",
        _TIGHTENED_PAYROLL_ROWS,
        ids=[f"{m}:{p}" for (m, p, _k, _r, _b) in _TIGHTENED_PAYROLL_ROWS],
    )
    def test_tightened_payroll_row_removed_roles_denied(
        self, client, method, path, kept, removed, body
    ):
        """Each role REMOVED from a tightened payroll row is denied by the live
        app (middleware 'Forbidden:' 403). A failure here means the row was
        over-tightened relative to the real route gate -> revert that row."""
        for role in removed:
            r = client.request(method, path, headers=ALL_ROLE_HEADERS[role], json=body)
            assert_middleware_403(r, method, path)

    @pytest.mark.parametrize(
        "method,path,kept,removed,body",
        _TIGHTENED_PAYROLL_ROWS,
        ids=[f"{m}:{p}" for (m, p, _k, _r, _b) in _TIGHTENED_PAYROLL_ROWS],
    )
    def test_tightened_payroll_row_kept_roles_allowed(
        self, matrix_client, method, path, kept, removed, body
    ):
        """Each role KEPT on a tightened payroll row is still authorized through
        (passes BOTH the middleware policy and the route's own require_roles)."""
        for role in kept:
            r = matrix_client.request(method, path, headers=ALL_ROLE_HEADERS[role], json=body)
            assert_route_allowed(r, role, method, path)

    @pytest.mark.parametrize("method,path", _FINANCE_PATHS)
    @pytest.mark.parametrize("role", [
        "OPTOMETRIST", "SALES_CASHIER", "SALES_STAFF", "CASHIER", "WORKSHOP_STAFF",
        "CATALOG_MANAGER",
    ])
    def test_non_finance_role_403(self, client, method, path, role):
        r = client.request(method, path, headers=ALL_ROLE_HEADERS[role])
        assert_middleware_403(r, method, path)

    def test_superadmin_passes_finance_routes(self, matrix_client):
        """SUPERADMIN passes even finance-only routes (check_access SUPERADMIN auto-pass)."""
        for method, path in self.__class__._FINANCE_PATHS:
            r = matrix_client.request(method, path, headers=ALL_ROLE_HEADERS["SUPERADMIN"])
            assert r.status_code not in (401, 403), (
                f"SUPERADMIN blocked on {method} {path}: {r.status_code}"
            )


# ===========================================================================
# SECTION 8: Credit/loyalty routes — restricted credit roles
# ===========================================================================

class TestCreditLoyaltyRoutes:
    """POST loyalty/add and store-credit/add are ACCOUNTANT, ADMIN, AREA_MANAGER,
    STORE_MANAGER only."""

    _CREDIT_ALLOWED = {"ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER", "SUPERADMIN"}
    _DUMMY_CUST = "CUST-DUMMY-001"

    def test_loyalty_add_denied_for_sales_staff(self, client):
        r = client.post(
            f"/api/v1/customers/{self._DUMMY_CUST}/loyalty/add",
            headers=ALL_ROLE_HEADERS["SALES_STAFF"],
            json={"points": 10, "reason": "test"},
        )
        assert_middleware_403(r, "POST", f"/api/v1/customers/{self._DUMMY_CUST}/loyalty/add")

    def test_loyalty_add_denied_for_cashier(self, client):
        r = client.post(
            f"/api/v1/customers/{self._DUMMY_CUST}/loyalty/add",
            headers=ALL_ROLE_HEADERS["CASHIER"],
            json={"points": 10, "reason": "test"},
        )
        assert_middleware_403(r, "POST", f"/api/v1/customers/{self._DUMMY_CUST}/loyalty/add")

    def test_loyalty_add_denied_for_workshop_staff(self, client):
        r = client.post(
            f"/api/v1/customers/{self._DUMMY_CUST}/loyalty/add",
            headers=ALL_ROLE_HEADERS["WORKSHOP_STAFF"],
            json={"points": 10, "reason": "test"},
        )
        assert_middleware_403(r, "POST", f"/api/v1/customers/{self._DUMMY_CUST}/loyalty/add")

    def test_loyalty_add_allowed_for_accountant(self, matrix_client):
        r = matrix_client.post(
            f"/api/v1/customers/{self._DUMMY_CUST}/loyalty/add",
            headers=ALL_ROLE_HEADERS["ACCOUNTANT"],
            json={"points": 10, "reason": "test"},
        )
        assert_route_allowed(r, "ACCOUNTANT", "POST", f"/api/v1/customers/{self._DUMMY_CUST}/loyalty/add")

    def test_loyalty_add_allowed_for_store_manager(self, matrix_client):
        r = matrix_client.post(
            f"/api/v1/customers/{self._DUMMY_CUST}/loyalty/add",
            headers=ALL_ROLE_HEADERS["STORE_MANAGER"],
            json={"points": 10, "reason": "test"},
        )
        assert_route_allowed(r, "STORE_MANAGER", "POST", f"/api/v1/customers/{self._DUMMY_CUST}/loyalty/add")

    def test_store_credit_add_denied_for_sales_cashier(self, client):
        r = client.post(
            f"/api/v1/customers/{self._DUMMY_CUST}/store-credit/add",
            headers=ALL_ROLE_HEADERS["SALES_CASHIER"],
            json={"amount": 50, "reason": "test"},
        )
        assert_middleware_403(r, "POST", f"/api/v1/customers/{self._DUMMY_CUST}/store-credit/add")

    def test_store_credit_add_allowed_for_area_manager(self, matrix_client):
        r = matrix_client.post(
            f"/api/v1/customers/{self._DUMMY_CUST}/store-credit/add",
            headers=ALL_ROLE_HEADERS["AREA_MANAGER"],
            json={"amount": 50, "reason": "test"},
        )
        assert_route_allowed(r, "AREA_MANAGER", "POST", f"/api/v1/customers/{self._DUMMY_CUST}/store-credit/add")


# ===========================================================================
# SECTION 9: Marketing bulk-send — mgmt roles only
# ===========================================================================

class TestMarketingRoutes:
    """POST /marketing/notifications/send and send-bulk require ADMIN/AREA_MANAGER/STORE_MANAGER."""

    _MARKETING_PATHS = [
        "/api/v1/marketing/notifications/send",
        "/api/v1/marketing/notifications/send-bulk",
    ]

    @pytest.mark.parametrize("path", _MARKETING_PATHS)
    @pytest.mark.parametrize("role", ["SALES_STAFF", "SALES_CASHIER", "CASHIER", "OPTOMETRIST", "WORKSHOP_STAFF"])
    def test_marketing_send_denied_for_lower_roles(self, client, role, path):
        r = client.post(
            path,
            headers=ALL_ROLE_HEADERS[role],
            json={"message": "test", "customer_ids": []},
        )
        assert_middleware_403(r, "POST", path)

    @pytest.mark.parametrize("path", _MARKETING_PATHS)
    @pytest.mark.parametrize("role", ["ADMIN", "AREA_MANAGER", "STORE_MANAGER"])
    def test_marketing_send_allowed_for_mgmt(self, matrix_client, role, path):
        r = matrix_client.post(
            path,
            headers=ALL_ROLE_HEADERS[role],
            json={"message": "test", "customer_ids": []},
        )
        assert_route_allowed(r, role, "POST", path)


# ===========================================================================
# SECTION 10: Catalog pricing routes — catalog roles only
# ===========================================================================

class TestCatalogRoutes:
    """POST/PUT /catalog/products are ADMIN, CATALOG_MANAGER, SUPERADMIN."""

    _DUMMY_PROD = "PROD-DUMMY-001"

    def test_catalog_products_post_denied_for_optometrist(self, client):
        r = client.post(
            "/api/v1/catalog/products",
            headers=ALL_ROLE_HEADERS["OPTOMETRIST"],
            json={"name": "Test", "sku": "TST001", "price": 100},
        )
        assert_middleware_403(r, "POST", "/api/v1/catalog/products")

    def test_catalog_products_post_denied_for_cashier(self, client):
        r = client.post(
            "/api/v1/catalog/products",
            headers=ALL_ROLE_HEADERS["CASHIER"],
            json={"name": "Test", "sku": "TST001", "price": 100},
        )
        assert_middleware_403(r, "POST", "/api/v1/catalog/products")

    def test_catalog_products_put_denied_for_sales_staff(self, client):
        r = client.put(
            f"/api/v1/catalog/products/{self._DUMMY_PROD}",
            headers=ALL_ROLE_HEADERS["SALES_STAFF"],
            json={"name": "Updated"},
        )
        assert_middleware_403(r, "PUT", f"/api/v1/catalog/products/{self._DUMMY_PROD}")

    def test_catalog_products_post_allowed_for_catalog_manager(self, matrix_client):
        r = matrix_client.post(
            "/api/v1/catalog/products",
            headers=ALL_ROLE_HEADERS["CATALOG_MANAGER"],
            json={"name": "Test", "sku": "TST001", "price": 100},
        )
        assert_route_allowed(r, "CATALOG_MANAGER", "POST", "/api/v1/catalog/products")

    def test_catalog_products_put_allowed_for_admin(self, matrix_client):
        r = matrix_client.put(
            f"/api/v1/catalog/products/{self._DUMMY_PROD}",
            headers=ALL_ROLE_HEADERS["ADMIN"],
            json={"name": "Updated"},
        )
        assert_route_allowed(r, "ADMIN", "PUT", f"/api/v1/catalog/products/{self._DUMMY_PROD}")

    def test_products_bulk_price_denied_for_store_manager(self, client):
        """POST /products/bulk-price is ADMIN/CATALOG_MANAGER only (not STORE_MANAGER)."""
        r = client.post(
            "/api/v1/products/bulk-price",
            headers=ALL_ROLE_HEADERS["STORE_MANAGER"],
            json={"updates": []},
        )
        assert_middleware_403(r, "POST", "/api/v1/products/bulk-price")

    def test_products_bulk_price_allowed_for_catalog_manager(self, matrix_client):
        r = matrix_client.post(
            "/api/v1/products/bulk-price",
            headers=ALL_ROLE_HEADERS["CATALOG_MANAGER"],
            json={"updates": []},
        )
        assert_route_allowed(r, "CATALOG_MANAGER", "POST", "/api/v1/products/bulk-price")


# ===========================================================================
# SECTION 11: Order creation (POS/sales roles)
# ===========================================================================

class TestOrderCreation:
    """POST /orders requires ADMIN, AREA_MANAGER, SALES_CASHIER, SALES_STAFF,
    STORE_MANAGER, SUPERADMIN — ACCOUNTANT, OPTOMETRIST, CASHIER, WORKSHOP_STAFF
    must be denied."""

    _POS_ROLES_ALLOWED = {"ADMIN", "AREA_MANAGER", "SALES_CASHIER", "SALES_STAFF", "STORE_MANAGER", "SUPERADMIN"}
    _POS_ROLES_DENIED = set(ALL_ROLES) - _POS_ROLES_ALLOWED

    @pytest.mark.parametrize("role", sorted(_POS_ROLES_DENIED))
    def test_order_post_denied_for_non_pos_roles(self, client, role):
        r = client.post(
            "/api/v1/orders",
            headers=ALL_ROLE_HEADERS[role],
            json={"items": [], "customer_id": "CUST-001"},
        )
        assert_middleware_403(r, "POST", "/api/v1/orders")

    @pytest.mark.parametrize("role", sorted(_POS_ROLES_ALLOWED))
    def test_order_post_allowed_for_pos_roles(self, matrix_client, role):
        r = matrix_client.post(
            "/api/v1/orders",
            headers=ALL_ROLE_HEADERS[role],
            json={"items": [], "customer_id": "CUST-001"},
        )
        assert_route_allowed(r, role, "POST", "/api/v1/orders")


# ===========================================================================
# SECTION 12: Prescriptions (clinical routes, self-enforced on POST)
# ===========================================================================

class TestPrescriptionRoutes:
    """POST /prescriptions is self_enforced: the middleware DEFERS to the route
    which returns 403 with body 'Your role does not have clinical access.'
    PUT /prescriptions/{id} is ALSO self_enforced (since #366): on a denied role
    the middleware DEFERS to the route, which returns the body-specific clinical
    403 ('Only optometrists and managers can edit prescriptions...'), NOT the
    generic middleware 'Forbidden:' 403 — same pattern as POST /prescriptions."""

    _CLINICAL_ROLES = {"ADMIN", "OPTOMETRIST", "STORE_MANAGER", "SUPERADMIN"}
    _NON_CLINICAL = set(ALL_ROLES) - _CLINICAL_ROLES

    _RX_BODY = {
        "customer_id": "CUST-DUMMY-001",
        "patient_id": "PAT-001",
        "sph_od": -1.0,
        "cyl_od": -0.50,
        "axis_od": 90,
        "sph_os": -1.0,
        "cyl_os": -0.50,
        "axis_os": 90,
    }

    @pytest.mark.parametrize("role", sorted(_NON_CLINICAL))
    def test_prescription_post_clinical_403_for_non_clinical(self, client, role):
        """Non-clinical roles get the route's clinical-specific 403 (self-enforced)."""
        r = client.post(
            "/api/v1/prescriptions",
            headers=ALL_ROLE_HEADERS[role],
            json=self._RX_BODY,
        )
        assert_clinical_403(r, role, "/api/v1/prescriptions")

    @pytest.mark.parametrize("role", sorted(_CLINICAL_ROLES))
    def test_prescription_post_allowed_for_clinical(self, matrix_client, role):
        r = matrix_client.post(
            "/api/v1/prescriptions",
            headers=ALL_ROLE_HEADERS[role],
            json=self._RX_BODY,
        )
        assert_route_allowed(r, role, "POST", "/api/v1/prescriptions")

    def test_prescription_put_cashier_clinical_403_deferred(self, client):
        """PUT /prescriptions/{id} is self_enforced (since #366): middleware DEFERS,
        route returns the body-specific clinical 403 (NOT 'Forbidden:')."""
        dummy_id = "RX-DUMMY-001"
        r = client.put(
            f"/api/v1/prescriptions/{dummy_id}",
            headers=ALL_ROLE_HEADERS["CASHIER"],
            json=self._RX_BODY,
        )
        assert_clinical_403(r, "CASHIER", f"/api/v1/prescriptions/{dummy_id}")
        assert not r.json().get("detail", "").startswith(_MW_FORBIDDEN_PREFIX), (
            "PUT /prescriptions/{id} must defer to the route's clinical 403, "
            "not return the generic middleware 'Forbidden:' 403."
        )

    def test_prescription_put_workshop_staff_clinical_403_deferred(self, client):
        dummy_id = "RX-DUMMY-001"
        r = client.put(
            f"/api/v1/prescriptions/{dummy_id}",
            headers=ALL_ROLE_HEADERS["WORKSHOP_STAFF"],
            json={"sph_od": -1.0},
        )
        assert_clinical_403(r, "WORKSHOP_STAFF", f"/api/v1/prescriptions/{dummy_id}")
        assert not r.json().get("detail", "").startswith(_MW_FORBIDDEN_PREFIX), (
            "PUT /prescriptions/{id} must defer to the route's clinical 403, "
            "not return the generic middleware 'Forbidden:' 403."
        )

    def test_prescription_put_optometrist_allowed(self, matrix_client):
        dummy_id = "RX-DUMMY-001"
        r = matrix_client.put(
            f"/api/v1/prescriptions/{dummy_id}",
            headers=ALL_ROLE_HEADERS["OPTOMETRIST"],
            json={"sph_od": -1.0},
        )
        assert_route_allowed(r, "OPTOMETRIST", "PUT", f"/api/v1/prescriptions/{dummy_id}")


# ===========================================================================
# SECTION 13: User management — ADMIN/SUPERADMIN only
# ===========================================================================

class TestUserManagementRoutes:
    """POST /users, DELETE /users/{id} etc. are ADMIN/SUPERADMIN only."""

    def test_post_users_denied_for_store_manager(self, client):
        r = client.post(
            "/api/v1/users",
            headers=ALL_ROLE_HEADERS["STORE_MANAGER"],
            json={"username": "newuser", "password": "secret123", "roles": ["SALES_STAFF"]},
        )
        assert_middleware_403(r, "POST", "/api/v1/users")

    def test_post_users_denied_for_area_manager(self, client):
        r = client.post(
            "/api/v1/users",
            headers=ALL_ROLE_HEADERS["AREA_MANAGER"],
            json={"username": "newuser", "password": "secret123", "roles": ["SALES_STAFF"]},
        )
        assert_middleware_403(r, "POST", "/api/v1/users")

    def test_post_users_allowed_for_admin(self, matrix_client):
        r = matrix_client.post(
            "/api/v1/users",
            headers=ALL_ROLE_HEADERS["ADMIN"],
            json={"username": "newuser", "password": "secret123", "roles": ["SALES_STAFF"]},
        )
        assert_route_allowed(r, "ADMIN", "POST", "/api/v1/users")

    def test_post_users_allowed_for_superadmin(self, matrix_client):
        r = matrix_client.post(
            "/api/v1/users",
            headers=ALL_ROLE_HEADERS["SUPERADMIN"],
            json={"username": "newuser2", "password": "secret123", "roles": ["SALES_STAFF"]},
        )
        assert_route_allowed(r, "SUPERADMIN", "POST", "/api/v1/users")

    def test_get_users_denied_for_optometrist(self, client):
        r = client.get("/api/v1/users/", headers=ALL_ROLE_HEADERS["OPTOMETRIST"])
        assert_middleware_403(r, "GET", "/api/v1/users/")

    def test_get_users_allowed_for_store_manager(self, matrix_client):
        """GET /users/ allows STORE_MANAGER (unlike POST which is ADMIN-only)."""
        r = matrix_client.get("/api/v1/users/", headers=ALL_ROLE_HEADERS["STORE_MANAGER"])
        assert_route_allowed(r, "STORE_MANAGER", "GET", "/api/v1/users/")


# ===========================================================================
# SECTION 14: Audit verify — SUPERADMIN only
# ===========================================================================

class TestAuditVerify:
    """GET /audit/verify is SUPERADMIN-only (not self-enforced -> middleware 403)."""

    @pytest.mark.parametrize("role", [
        "ADMIN", "AREA_MANAGER", "ACCOUNTANT", "STORE_MANAGER",
        "SALES_STAFF", "OPTOMETRIST", "CASHIER",
    ])
    def test_audit_verify_denied_non_superadmin(self, client, role):
        r = client.get("/api/v1/audit/verify", headers=ALL_ROLE_HEADERS[role])
        assert_middleware_403(r, "GET", "/api/v1/audit/verify")

    def test_audit_verify_allowed_superadmin(self, matrix_client):
        r = matrix_client.get("/api/v1/audit/verify", headers=ALL_ROLE_HEADERS["SUPERADMIN"])
        assert_route_allowed(r, "SUPERADMIN", "GET", "/api/v1/audit/verify")


# ===========================================================================
# SECTION 15: Analytics-v2 SUPERADMIN-only routes
# ===========================================================================

class TestAnalyticsV2SuperadminOnly:
    """anomaly-detection, demand-forecast, vendor-margins are SUPERADMIN-only."""

    _SA_ONLY_PATHS = [
        "/api/v1/analytics-v2/anomaly-detection",
        "/api/v1/analytics-v2/demand-forecast",
        "/api/v1/analytics-v2/vendor-margins",
    ]

    @pytest.mark.parametrize("path", _SA_ONLY_PATHS)
    @pytest.mark.parametrize("role", ["ADMIN", "AREA_MANAGER", "ACCOUNTANT", "STORE_MANAGER"])
    def test_non_superadmin_denied(self, client, role, path):
        r = client.get(path, headers=ALL_ROLE_HEADERS[role])
        assert_middleware_403(r, "GET", path)

    @pytest.mark.parametrize("path", _SA_ONLY_PATHS)
    def test_superadmin_allowed(self, matrix_client, path):
        r = matrix_client.get(path, headers=ALL_ROLE_HEADERS["SUPERADMIN"])
        assert_route_allowed(r, "SUPERADMIN", "GET", path)


# ===========================================================================
# SECTION 16: Loyalty admin routes
# ===========================================================================

class TestLoyaltyAdminRoutes:
    """POST /loyalty/adjust requires ADMIN/SUPERADMIN.
    PUT /loyalty/settings requires SUPERADMIN only."""

    def test_loyalty_adjust_denied_for_store_manager(self, client):
        r = client.post(
            "/api/v1/loyalty/adjust",
            headers=ALL_ROLE_HEADERS["STORE_MANAGER"],
            json={"customer_id": "CUST-001", "delta": 10, "reason": "test"},
        )
        assert_middleware_403(r, "POST", "/api/v1/loyalty/adjust")

    def test_loyalty_adjust_denied_for_accountant(self, client):
        r = client.post(
            "/api/v1/loyalty/adjust",
            headers=ALL_ROLE_HEADERS["ACCOUNTANT"],
            json={"customer_id": "CUST-001", "delta": 10, "reason": "test"},
        )
        assert_middleware_403(r, "POST", "/api/v1/loyalty/adjust")

    def test_loyalty_adjust_allowed_for_admin(self, matrix_client):
        r = matrix_client.post(
            "/api/v1/loyalty/adjust",
            headers=ALL_ROLE_HEADERS["ADMIN"],
            json={"customer_id": "CUST-001", "delta": 10, "reason": "test"},
        )
        assert_route_allowed(r, "ADMIN", "POST", "/api/v1/loyalty/adjust")

    def test_loyalty_settings_put_denied_for_admin(self, client):
        """PUT /loyalty/settings is SUPERADMIN-only; ADMIN must be denied."""
        r = client.put(
            "/api/v1/loyalty/settings",
            headers=ALL_ROLE_HEADERS["ADMIN"],
            json={"points_per_rupee": 1},
        )
        assert_middleware_403(r, "PUT", "/api/v1/loyalty/settings")

    def test_loyalty_settings_put_allowed_for_superadmin(self, matrix_client):
        r = matrix_client.put(
            "/api/v1/loyalty/settings",
            headers=ALL_ROLE_HEADERS["SUPERADMIN"],
            json={"points_per_rupee": 1},
        )
        assert_route_allowed(r, "SUPERADMIN", "PUT", "/api/v1/loyalty/settings")


# ===========================================================================
# SECTION 17: Settings admin-controls (SUPERADMIN only)
# ===========================================================================

class TestSettingsAdminControls:
    """GET/PUT /settings/admin-controls are SUPERADMIN-only."""

    def test_admin_controls_denied_for_admin(self, client):
        r = client.get(
            "/api/v1/settings/admin-controls",
            headers=ALL_ROLE_HEADERS["ADMIN"],
        )
        assert_middleware_403(r, "GET", "/api/v1/settings/admin-controls")

    def test_admin_controls_allowed_for_superadmin(self, matrix_client):
        r = matrix_client.get(
            "/api/v1/settings/admin-controls",
            headers=ALL_ROLE_HEADERS["SUPERADMIN"],
        )
        assert_route_allowed(r, "SUPERADMIN", "GET", "/api/v1/settings/admin-controls")

    def test_settings_system_denied_for_store_manager(self, client):
        """GET /settings/system is ADMIN/SUPERADMIN only."""
        r = client.get(
            "/api/v1/settings/system",
            headers=ALL_ROLE_HEADERS["STORE_MANAGER"],
        )
        assert_middleware_403(r, "GET", "/api/v1/settings/system")

    def test_settings_system_allowed_for_admin(self, matrix_client):
        r = matrix_client.get(
            "/api/v1/settings/system",
            headers=ALL_ROLE_HEADERS["ADMIN"],
        )
        assert_route_allowed(r, "ADMIN", "GET", "/api/v1/settings/system")


# ===========================================================================
# SECTION 18: Inventory management — write routes
# ===========================================================================

class TestInventoryWriteRoutes:
    """Inventory write routes require management roles (not sales/cashier)."""

    def test_inventory_accountability_shrinkage_denied_for_sales_staff(self, client):
        r = client.get(
            "/api/v1/inventory/accountability/shrinkage",
            headers=ALL_ROLE_HEADERS["SALES_STAFF"],
        )
        assert_middleware_403(r, "GET", "/api/v1/inventory/accountability/shrinkage")

    def test_inventory_accountability_shrinkage_allowed_for_store_manager(self, matrix_client):
        r = matrix_client.get(
            "/api/v1/inventory/accountability/shrinkage",
            headers=ALL_ROLE_HEADERS["STORE_MANAGER"],
        )
        assert_route_allowed(r, "STORE_MANAGER", "GET", "/api/v1/inventory/accountability/shrinkage")

    def test_inventory_transfers_post_denied_for_cashier(self, client):
        r = client.post(
            "/api/v1/transfers",
            headers=ALL_ROLE_HEADERS["CASHIER"],
            json={"from_store_id": "BV-01", "to_store_id": "BV-02", "items": []},
        )
        assert_middleware_403(r, "POST", "/api/v1/transfers")

    def test_inventory_transfers_post_allowed_for_area_manager(self, matrix_client):
        r = matrix_client.post(
            "/api/v1/transfers",
            headers=ALL_ROLE_HEADERS["AREA_MANAGER"],
            json={"from_store_id": "BV-01", "to_store_id": "BV-02", "items": []},
        )
        assert_route_allowed(r, "AREA_MANAGER", "POST", "/api/v1/transfers")


# ===========================================================================
# SECTION 19: Vendor finance routes — accountant/admin only
# ===========================================================================

class TestVendorFinanceRoutes:
    """Vendor bills/debit-notes/payments are ACCOUNTANT/ADMIN only."""

    _DUMMY_VENDOR = "VENDOR-DUMMY-001"

    def test_vendor_bills_post_denied_for_area_manager(self, client):
        r = client.post(
            f"/api/v1/vendors/{self._DUMMY_VENDOR}/bills",
            headers=ALL_ROLE_HEADERS["AREA_MANAGER"],
            json={"amount": 1000, "bill_date": "2026-05-01", "bill_number": "INV-001"},
        )
        assert_middleware_403(r, "POST", f"/api/v1/vendors/{self._DUMMY_VENDOR}/bills")

    def test_vendor_bills_post_denied_for_store_manager(self, client):
        r = client.post(
            f"/api/v1/vendors/{self._DUMMY_VENDOR}/bills",
            headers=ALL_ROLE_HEADERS["STORE_MANAGER"],
            json={"amount": 1000, "bill_date": "2026-05-01", "bill_number": "INV-001"},
        )
        assert_middleware_403(r, "POST", f"/api/v1/vendors/{self._DUMMY_VENDOR}/bills")

    def test_vendor_bills_post_allowed_for_accountant(self, matrix_client):
        r = matrix_client.post(
            f"/api/v1/vendors/{self._DUMMY_VENDOR}/bills",
            headers=ALL_ROLE_HEADERS["ACCOUNTANT"],
            json={"amount": 1000, "bill_date": "2026-05-01", "bill_number": "INV-001"},
        )
        assert_route_allowed(r, "ACCOUNTANT", "POST", f"/api/v1/vendors/{self._DUMMY_VENDOR}/bills")

    def test_vendors_ap_aging_denied_for_store_manager(self, client):
        """GET /vendors/ap-aging is ACCOUNTANT/ADMIN only (not STORE_MANAGER/AREA_MANAGER)."""
        r = client.get(
            "/api/v1/vendors/ap-aging",
            headers=ALL_ROLE_HEADERS["STORE_MANAGER"],
        )
        assert_middleware_403(r, "GET", "/api/v1/vendors/ap-aging")

    def test_vendors_ap_aging_allowed_for_admin(self, matrix_client):
        r = matrix_client.get(
            "/api/v1/vendors/ap-aging",
            headers=ALL_ROLE_HEADERS["ADMIN"],
        )
        assert_route_allowed(r, "ADMIN", "GET", "/api/v1/vendors/ap-aging")


# ===========================================================================
# SECTION 20: Returns — cashier + admin (not optometrist/catalog)
# ===========================================================================

class TestReturnsRoutes:
    """POST /returns requires ADMIN, CASHIER, SALES_CASHIER, STORE_MANAGER."""

    _RETURNS_ALLOWED = {"ADMIN", "CASHIER", "SALES_CASHIER", "STORE_MANAGER", "SUPERADMIN"}
    _RETURNS_DENIED = set(ALL_ROLES) - _RETURNS_ALLOWED

    @pytest.mark.parametrize("role", sorted(_RETURNS_DENIED))
    def test_returns_post_denied(self, client, role):
        r = client.post(
            "/api/v1/returns",
            headers=ALL_ROLE_HEADERS[role],
            json={"order_id": "ORD-001", "items": [], "reason": "defect"},
        )
        assert_middleware_403(r, "POST", "/api/v1/returns")

    @pytest.mark.parametrize("role", sorted(_RETURNS_ALLOWED))
    def test_returns_post_allowed(self, matrix_client, role):
        r = matrix_client.post(
            "/api/v1/returns",
            headers=ALL_ROLE_HEADERS[role],
            json={"order_id": "ORD-001", "items": [], "reason": "defect"},
        )
        assert_route_allowed(r, role, "POST", "/api/v1/returns")


# ===========================================================================
# SECTION 21: SUPERADMIN-only payout lock
# ===========================================================================

class TestPayoutLock:
    """POST /payout/lock is SUPERADMIN-only."""

    @pytest.mark.parametrize("role", [
        "ADMIN", "AREA_MANAGER", "ACCOUNTANT", "STORE_MANAGER",
    ])
    def test_payout_lock_denied(self, client, role):
        r = client.post(
            "/api/v1/payout/lock",
            headers=ALL_ROLE_HEADERS[role],
            json={"month": "2026-05", "store_id": "BV-01"},
        )
        assert_middleware_403(r, "POST", "/api/v1/payout/lock")

    def test_payout_lock_allowed_superadmin(self, matrix_client):
        r = matrix_client.post(
            "/api/v1/payout/lock",
            headers=ALL_ROLE_HEADERS["SUPERADMIN"],
            json={"month": "2026-05", "store_id": "BV-01"},
        )
        assert_route_allowed(r, "SUPERADMIN", "POST", "/api/v1/payout/lock")


# ===========================================================================
# SECTION 22: Policy consistency probe — check_access vs live app
# ===========================================================================

class TestPolicyConsistencyWithLiveApp:
    """Drive a representative set of (role, endpoint) combinations through the
    live app and compare the outcome to what check_access predicts.

    DIVERGENCE = policy says role is allowed but live returns 401/403,
                 OR policy says role is denied but live returns 200.
    Any divergence is captured as a distinct assertion failure.
    """

    # Curated probe matrix: (method, concrete_path, role, expect_allowed:bool)
    # concrete_path uses real-looking dummy IDs
    PROBES = [
        # PUBLIC
        ("GET", "/api/v1/health", "WORKSHOP_STAFF", True),
        ("GET", "/api/v1/health", "SUPERADMIN", True),
        # AUTHENTICATED
        ("GET", "/api/v1/auth/me", "SALES_STAFF", True),
        ("GET", "/api/v1/customers", "CASHIER", True),
        ("GET", "/api/v1/prescriptions", "WORKSHOP_STAFF", True),
        # Jarvis - SUPERADMIN only
        ("GET", "/api/v1/jarvis/agents", "SUPERADMIN", True),
        ("GET", "/api/v1/jarvis/agents", "ADMIN", False),
        ("GET", "/api/v1/jarvis/status", "STORE_MANAGER", False),
        # Admin-only
        ("GET", "/api/v1/admin/escalations", "ADMIN", True),
        ("GET", "/api/v1/admin/escalations", "AREA_MANAGER", False),
        ("GET", "/api/v1/admin/system-health", "ADMIN", True),
        ("GET", "/api/v1/admin/system-health", "STORE_MANAGER", False),
        # Finance
        ("GET", "/api/v1/payroll/config", "ACCOUNTANT", True),
        ("GET", "/api/v1/payroll/config", "SALES_STAFF", False),
        ("GET", "/api/v1/finance/pnl", "AREA_MANAGER", True),
        ("GET", "/api/v1/finance/pnl", "OPTOMETRIST", False),
        ("GET", "/api/v1/reports/gstr1", "ACCOUNTANT", True),
        ("GET", "/api/v1/reports/gstr1", "CATALOG_MANAGER", False),
        # Credit
        ("POST", "/api/v1/customers/CUST-001/loyalty/add", "STORE_MANAGER", True),
        ("POST", "/api/v1/customers/CUST-001/loyalty/add", "SALES_STAFF", False),
        ("POST", "/api/v1/customers/CUST-001/store-credit/add", "AREA_MANAGER", True),
        ("POST", "/api/v1/customers/CUST-001/store-credit/add", "CASHIER", False),
        # Marketing
        ("POST", "/api/v1/marketing/notifications/send", "AREA_MANAGER", True),
        ("POST", "/api/v1/marketing/notifications/send", "SALES_CASHIER", False),
        # Catalog
        ("POST", "/api/v1/catalog/products", "CATALOG_MANAGER", True),
        ("POST", "/api/v1/catalog/products", "CASHIER", False),
        ("PUT", "/api/v1/catalog/products/PROD-001", "ADMIN", True),
        ("PUT", "/api/v1/catalog/products/PROD-001", "OPTOMETRIST", False),
        # Orders
        ("POST", "/api/v1/orders", "SALES_CASHIER", True),
        ("POST", "/api/v1/orders", "ACCOUNTANT", False),
        # Prescriptions (self_enforced POST; PUT not)
        ("PUT", "/api/v1/prescriptions/RX-001", "OPTOMETRIST", True),
        ("PUT", "/api/v1/prescriptions/RX-001", "CASHIER", False),
        # Reports
        ("GET", "/api/v1/reports/inventory/valuation", "ACCOUNTANT", True),
        ("GET", "/api/v1/reports/inventory/valuation", "SALES_CASHIER", False),
        # Users
        ("POST", "/api/v1/users", "ADMIN", True),
        ("POST", "/api/v1/users", "STORE_MANAGER", False),
        ("GET", "/api/v1/users/", "STORE_MANAGER", True),
        ("GET", "/api/v1/users/", "OPTOMETRIST", False),
        # Audit
        ("GET", "/api/v1/audit/verify", "SUPERADMIN", True),
        ("GET", "/api/v1/audit/verify", "ADMIN", False),
        # Loyalty
        ("PUT", "/api/v1/loyalty/settings", "SUPERADMIN", True),
        ("PUT", "/api/v1/loyalty/settings", "ADMIN", False),
        ("POST", "/api/v1/loyalty/adjust", "ADMIN", True),
        ("POST", "/api/v1/loyalty/adjust", "STORE_MANAGER", False),
        # Vendor finance
        ("POST", "/api/v1/vendors/VND-001/bills", "ACCOUNTANT", True),
        ("POST", "/api/v1/vendors/VND-001/bills", "AREA_MANAGER", False),
        # Returns
        ("POST", "/api/v1/returns", "SALES_CASHIER", True),
        ("POST", "/api/v1/returns", "OPTOMETRIST", False),
        # Payout
        ("POST", "/api/v1/payout/lock", "SUPERADMIN", True),
        ("POST", "/api/v1/payout/lock", "ADMIN", False),
        # Transfers
        ("POST", "/api/v1/transfers", "AREA_MANAGER", True),
        ("POST", "/api/v1/transfers", "CASHIER", False),
        # Analytics-v2 SUPERADMIN-only
        ("GET", "/api/v1/analytics-v2/anomaly-detection", "SUPERADMIN", True),
        ("GET", "/api/v1/analytics-v2/anomaly-detection", "AREA_MANAGER", False),
        # Settings
        ("GET", "/api/v1/settings/admin-controls", "SUPERADMIN", True),
        ("GET", "/api/v1/settings/admin-controls", "ADMIN", False),
        # Expenses aging
        ("GET", "/api/v1/expenses/aging", "ACCOUNTANT", True),
        ("GET", "/api/v1/expenses/aging", "SALES_CASHIER", False),
    ]

    @pytest.mark.parametrize("method,path,role,expect_allowed", PROBES)
    def test_policy_matches_live_enforcement(self, matrix_client, method, path, role, expect_allowed):
        """For each (role, endpoint), assert live app response matches policy.

        Uses matrix_client (raise_server_exceptions=False) so DB-absent crashes
        return 500 instead of propagating as Python exceptions.

        DIVERGENCE FOUND = this test fails with a clear message.
        """
        entry = policy_for(method, path)
        assert entry is not None, (
            f"No policy entry for {method} {path} — coverage gap"
        )

        # Self-enforced rows: 404-hiding (jarvis/techcherry) or clinical 403.
        # We cannot do a single status assertion for those; skip in this
        # general matrix (they have dedicated tests above).
        self_enforced = bool(entry.get("self_enforced"))

        headers = ALL_ROLE_HEADERS[role]
        # Most probes need a minimal body for POST/PUT.
        body = {}
        if method in ("POST", "PUT", "PATCH"):
            # Minimal bodies that won't cause body-parse errors on most routes.
            # Routes may still return 422 (validation) once authz passes — that's fine.
            body = {"_noop": True}

        r = matrix_client.request(method, path, headers=headers, json=body)
        status = r.status_code

        if expect_allowed:
            # Policy says role should be allowed.
            # For self-enforced paths with an allowed role, route still processes.
            assert status not in (401, 403), (
                f"DIVERGENCE: Policy allows role={role!r} on {method} {path}, "
                f"but live returned {status}. "
                f"Policy entry: {entry}. "
                f"Body: {r.text[:300]}"
            )
        elif not self_enforced:
            # Policy denies role, not self-enforced -> middleware 403 expected.
            assert status == 403, (
                f"DIVERGENCE: Policy denies role={role!r} on {method} {path}, "
                f"expected middleware 403, but live returned {status}. "
                f"Policy entry: {entry}. "
                f"Body: {r.text[:300]}"
            )
            detail = r.json().get("detail", "")
            assert detail.startswith(_MW_FORBIDDEN_PREFIX), (
                f"DIVERGENCE: 403 for role={role!r} on {method} {path} "
                f"not from middleware. detail={detail!r} (expected 'Forbidden:...'). "
                f"This means the middleware is NOT blocking it but something else is."
            )
        else:
            # Self-enforced + denied: 404 (jarvis/techcherry) or clinical 403.
            # Jarvis family -> 404. Prescriptions -> 403 clinical body.
            path_str = str(entry["path"])
            is_jarvis_or_techcherry = (
                path_str == "/api/v1/jarvis"
                or path_str.startswith("/api/v1/jarvis/")
                or path_str.startswith("/api/v1/admin/techcherry/")
            )
            if is_jarvis_or_techcherry:
                assert status == 404, (
                    f"DIVERGENCE: Jarvis/techcherry self-enforced denied role={role!r} "
                    f"on {method} {path}, expected 404 (existence-hiding), got {status}. "
                    f"Body: {r.text[:200]}"
                )
            else:
                # Clinical prescriptions POST -> 403 with clinical body
                assert status == 403, (
                    f"DIVERGENCE: Clinical self-enforced denied role={role!r} "
                    f"on {method} {path}, expected 403, got {status}. "
                    f"Body: {r.text[:200]}"
                )
