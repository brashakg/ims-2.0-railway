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
    """POST /returns requires ADMIN, CASHIER, SALES_STAFF, STORE_MANAGER.

    SALES_CASHIER was merged into SALES_STAFF (backlog #12): the route gate
    previously granted SALES_CASHIER (not SALES_STAFF); the access now lives on
    the survivor SALES_STAFF, and a SALES_CASHIER token is normalized to
    SALES_STAFF at decode_token -- so BOTH role strings are allowed here."""

    _RETURNS_ALLOWED = {
        "ADMIN", "CASHIER", "SALES_STAFF", "SALES_CASHIER", "STORE_MANAGER", "SUPERADMIN",
    }
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


class TestSalesCashierAliasBackwardCompat:
    """backlog #12: a JWT still carrying the retired SALES_CASHIER role must be
    treated as the survivor SALES_STAFF everywhere -- it keeps SALES_STAFF access
    and is never locked out. decode_token normalizes the claim at one chokepoint.

    We assert the alias token reaches every SALES_STAFF-allowed endpoint AND is
    denied on the same endpoints a plain SALES_STAFF token is denied on (so the
    merge granted nothing extra)."""

    # (method, path, body) endpoints SALES_STAFF (the survivor) is allowed on.
    _STAFF_ALLOWED = [
        ("POST", "/api/v1/orders", {"items": [], "customer_id": "CUST-001"}),
        ("POST", "/api/v1/returns", {"order_id": "ORD-1", "items": [], "reason": "defect"}),
        ("GET", "/api/v1/reports/day-end-close", None),
    ]

    # Endpoints a SALES_STAFF token is denied on -> the alias must be too.
    _STAFF_DENIED = [
        ("GET", "/api/v1/payroll/config", None),
        ("POST", "/api/v1/marketing/notifications/send", {"message": "x", "customer_ids": []}),
        ("GET", "/api/v1/reports/inventory/valuation", None),
    ]

    @pytest.mark.parametrize("method,path,body", _STAFF_ALLOWED)
    def test_alias_token_allowed_where_survivor_is(self, matrix_client, method, path, body):
        r = matrix_client.request(method, path, headers=ALL_ROLE_HEADERS["SALES_CASHIER"], json=body)
        assert_route_allowed(r, "SALES_CASHIER(alias)", method, path)
        # The survivor token reaches the same endpoint (parity).
        r2 = matrix_client.request(method, path, headers=ALL_ROLE_HEADERS["SALES_STAFF"], json=body)
        assert_route_allowed(r2, "SALES_STAFF", method, path)

    @pytest.mark.parametrize("method,path,body", _STAFF_DENIED)
    def test_alias_token_denied_where_survivor_is(self, client, method, path, body):
        r = client.request(method, path, headers=ALL_ROLE_HEADERS["SALES_CASHIER"], json=body)
        assert_middleware_403(r, method, path)


class TestCrossStoreListIDOR:
    """BUG-062 tail (live-QA 2026-06-06): a store-scoped role passing ANOTHER
    store's ?store_id to a list endpoint must be 403'd, not served that store's
    rows. Covers the routers the #519/#520 sweep did not reach: tasks, hr,
    workshop, vendors, clinical. A Pune store manager must not read Bokaro's
    tasks / attendance-PII / workshop queue / purchase orders / clinical queue.
    """

    # (path, query) — all GET list endpoints that resolve ?store_id.
    _CROSS_STORE_PATHS = [
        "/api/v1/tasks?store_id=BV-BOK-01",
        "/api/v1/hr/attendance?store_id=BV-BOK-01",
        "/api/v1/workshop/jobs?store_id=BV-BOK-01",
        "/api/v1/vendors/purchase-orders?store_id=BV-BOK-01",
        "/api/v1/clinical/queue?store_id=BV-BOK-01",
    ]

    def test_store_manager_denied_other_store(self, matrix_client):
        # STORE_MANAGER scoped to BV-PUN-01 asking for BV-BOK-01.
        token = _mint_token(["STORE_MANAGER"], store_id="BV-PUN-01")
        headers = {"Authorization": f"Bearer {token}"}
        for path in self._CROSS_STORE_PATHS:
            r = matrix_client.get(path, headers=headers)
            assert r.status_code == 403, (
                f"Cross-store IDOR: STORE_MANAGER@BV-PUN-01 on {path} "
                f"should be 403, got {r.status_code}: {r.text[:200]}"
            )

    def test_admin_allowed_cross_store(self, matrix_client):
        # ADMIN is cross-store: the same requests must NOT be authz-blocked
        # (may 500 on missing mock DB — that's allowed through, proving authz).
        token = _mint_token(["ADMIN"], store_id="BV-PUN-01")
        headers = {"Authorization": f"Bearer {token}"}
        for path in self._CROSS_STORE_PATHS:
            r = matrix_client.get(path, headers=headers)
            assert r.status_code not in (401, 403), (
                f"ADMIN should be allowed cross-store on {path}, got {r.status_code}"
            )

    def test_own_store_allowed(self, matrix_client):
        # Same role asking for its OWN store must pass the gate (no over-block).
        token = _mint_token(["STORE_MANAGER"], store_id="BV-BOK-01")
        headers = {"Authorization": f"Bearer {token}"}
        for path in self._CROSS_STORE_PATHS:
            r = matrix_client.get(path, headers=headers)
            assert r.status_code not in (401, 403), (
                f"Own-store request on {path} should pass, got {r.status_code}"
            )


class TestFinanceCrossStoreIDOR:
    """BUG-062 direct-dict tail: live QA proved a Pune store manager read Bokaro's
    revenue / P&L (incl. COGS) / receivables (with customer PII) via the finance
    aggregation endpoints, which trusted ?store_id directly. _scope_store now 403s
    a store-scoped role asking for another store; admins keep cross-store/all.
    """

    _FIN_PATHS = [
        "/api/v1/finance/revenue?store_id=BV-BOK-01",
        "/api/v1/finance/pnl?store_id=BV-BOK-01",
        "/api/v1/finance/outstanding?store_id=BV-BOK-01",
    ]

    def test_store_manager_denied_other_store(self, matrix_client):
        token = _mint_token(["STORE_MANAGER"], store_id="BV-PUN-01")
        headers = {"Authorization": f"Bearer {token}"}
        for path in self._FIN_PATHS:
            r = matrix_client.get(path, headers=headers)
            assert r.status_code == 403, (
                f"Finance IDOR: STORE_MANAGER@BV-PUN-01 on {path} should be 403, "
                f"got {r.status_code}: {r.text[:200]}"
            )

    def test_admin_allowed_cross_store(self, matrix_client):
        token = _mint_token(["ADMIN"], store_id="BV-PUN-01")
        headers = {"Authorization": f"Bearer {token}"}
        for path in self._FIN_PATHS:
            r = matrix_client.get(path, headers=headers)
            assert r.status_code not in (401, 403), (
                f"ADMIN should be allowed cross-store on {path}, got {r.status_code}"
            )

    def test_store_manager_own_store_allowed(self, matrix_client):
        token = _mint_token(["STORE_MANAGER"], store_id="BV-BOK-01")
        headers = {"Authorization": f"Bearer {token}"}
        for path in self._FIN_PATHS:
            r = matrix_client.get(path, headers=headers)
            assert r.status_code not in (401, 403), (
                f"Own-store finance request on {path} should pass, got {r.status_code}"
            )


# ===========================================================================
# CLI-9 - lens-power combos: the malformed-role-gate regression
# ===========================================================================
# The two WRITE endpoints were gated with ``require_roles(_CLINICAL_ROLES)``
# (the tuple passed as ONE positional arg). require_roles does
# ``allowed = set(allowed_roles)``, so the gate's allow-set became
# ``{("ADMIN", "STORE_MANAGER", "OPTOMETRIST")}`` -- a set holding one TUPLE,
# which no role STRING can ever intersect. Every caller was 403'd except
# SUPERADMIN (hardcoded bypass). The bug fails CLOSED, so it is invisible until
# somebody actually needs the endpoint. These tests lock the fixed behaviour
# through the REAL app + the real RBAC middleware.


class _FakeComboCol:
    """In-memory stand-in for the ``lens_power_combos`` Mongo collection.

    Only the three operations the router uses (insert_one / find_one /
    delete_one) -- enough to prove a real 200, not merely "the gate let it by".
    """

    def __init__(self, docs=None):
        self.docs = [dict(d) for d in (docs or [])]
        self.inserted = []

    def insert_one(self, doc):
        self.inserted.append(dict(doc))
        self.docs.append(dict(doc))
        return type("_Ins", (), {"inserted_id": "fake"})()

    def find_one(self, flt, _projection=None):
        for d in self.docs:
            if d.get("combo_id") == flt.get("combo_id"):
                return dict(d)
        return None

    def delete_one(self, flt):
        before = len(self.docs)
        self.docs = [
            d for d in self.docs if d.get("combo_id") != flt.get("combo_id")
        ]
        return type("_Del", (), {"deleted_count": before - len(self.docs)})()

    def find(self, flt, _projection=None):
        """Cursor stand-in supporting the router's .sort(...).limit(...) chain.

        Honours the filter EXACTLY as Mongo would, so a test can tell an
        all-stores read apart from a store-scoped one.
        """
        selected = [
            dict(d)
            for d in self.docs
            if all(d.get(k) == v for k, v in (flt or {}).items())
        ]

        class _Cursor(list):
            def sort(self, *_a, **_k):
                return self

            def limit(self, *_a, **_k):
                return list(self)

        return _Cursor(selected)


_COMBOS_PATH = "/api/v1/clinical/lens-power-combos"
_COMBO_BODY = {
    "name": "Myopia mild SVS",
    "right_eye": {"sph": "-1.00", "cyl": "0"},
    "left_eye": {"sph": "-1.00", "cyl": "0"},
}


def _stub_combo_col(monkeypatch, col):
    """Point the router's fail-soft collection accessor at ``col``."""
    from api.routers import clinical

    monkeypatch.setattr(clinical, "_get_lens_power_combos_col", lambda: col)
    return col


def _combo_doc(combo_id, created_by, store_id="BV-MATRIX-01"):
    return {
        "combo_id": combo_id,
        "store_id": store_id,
        "created_by": created_by,
        "name": "Shared template",
        "right_eye": {"sph": "-1.00", "cyl": "0"},
        "left_eye": {"sph": "-1.00", "cyl": "0"},
    }


class TestLensPowerComboCreateGate:
    """POST /clinical/lens-power-combos -- clinical roles in, everyone else out."""

    @pytest.mark.parametrize("role", ["OPTOMETRIST", "STORE_MANAGER", "ADMIN"])
    def test_clinical_role_can_create(self, matrix_client, monkeypatch, role):
        col = _stub_combo_col(monkeypatch, _FakeComboCol())
        token = _mint_token([role], uid="u-" + role.lower())
        resp = matrix_client.post(
            _COMBOS_PATH,
            headers={"Authorization": "Bearer " + token},
            json=_COMBO_BODY,
        )
        assert resp.status_code == 200, (
            f"{role} must be able to save a lens-power combo, "
            f"got {resp.status_code}: {resp.text[:300]}"
        )
        # Really persisted -- not just "the gate let the request through".
        assert len(col.inserted) == 1
        body = resp.json()
        assert body["name"] == "Myopia mild SVS"
        assert body["created_by"] == "u-" + role.lower()
        assert body["store_id"] == "BV-MATRIX-01"

    # AREA_MANAGER leads the list deliberately: it is the ONE role whose access
    # this PR actually decided, and the only one a future edit is likely to put
    # back. The other four were never in dispute. Without AREA_MANAGER here the
    # complete revert -- AREA_MANAGER re-added to _CLINICAL_ROLES and to both
    # POLICY rows -- ships GREEN (measured: 619 passed, zero red) while a
    # supervisory Area Manager silently regains create/delete on other stores'
    # shared clinical Rx templates. See feedback_hollow_tests.md.
    @pytest.mark.parametrize(
        "role",
        ["AREA_MANAGER", "SALES_STAFF", "CASHIER", "SALES_CASHIER", "WORKSHOP_STAFF"],
    )
    def test_non_clinical_role_is_forbidden(self, matrix_client, monkeypatch, role):
        col = _stub_combo_col(monkeypatch, _FakeComboCol())
        resp = matrix_client.post(
            _COMBOS_PATH, headers=ALL_ROLE_HEADERS[role], json=_COMBO_BODY
        )
        assert_middleware_403(resp, "POST", _COMBOS_PATH)
        # Blocked BEFORE the handler -- nothing reached the collection.
        assert col.inserted == []

    def test_no_token_is_401(self, matrix_client):
        resp = matrix_client.post(_COMBOS_PATH, json=_COMBO_BODY)
        assert resp.status_code == 401, resp.text


class TestLensPowerComboDeleteGate:
    """DELETE is deliberately narrower than POST: a combo is a SHARED store
    template, so the role gate is only the first half -- store scope (404) and
    creator-or-manager ownership (403) are enforced per-object in the handler.
    """

    def test_creator_may_delete_own_combo(self, matrix_client, monkeypatch):
        col = _stub_combo_col(
            monkeypatch, _FakeComboCol([_combo_doc("c-1", "opto-a")])
        )
        token = _mint_token(["OPTOMETRIST"], uid="opto-a")
        resp = matrix_client.delete(
            _COMBOS_PATH + "/c-1", headers={"Authorization": "Bearer " + token}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"deleted": "c-1"}
        assert col.docs == []

    def test_optometrist_may_not_delete_a_colleagues_combo(
        self, matrix_client, monkeypatch
    ):
        col = _stub_combo_col(
            monkeypatch, _FakeComboCol([_combo_doc("c-1", "opto-a")])
        )
        token = _mint_token(["OPTOMETRIST"], uid="opto-b")
        resp = matrix_client.delete(
            _COMBOS_PATH + "/c-1", headers={"Authorization": "Bearer " + token}
        )
        assert resp.status_code == 403, resp.text
        assert "creator or a manager" in resp.json()["detail"]
        assert len(col.docs) == 1, "a colleague's template must survive"

    def test_store_manager_may_delete_any_combo_in_their_store(
        self, matrix_client, monkeypatch
    ):
        col = _stub_combo_col(
            monkeypatch, _FakeComboCol([_combo_doc("c-1", "opto-a")])
        )
        token = _mint_token(["STORE_MANAGER"], uid="mgr-1")
        resp = matrix_client.delete(
            _COMBOS_PATH + "/c-1", headers={"Authorization": "Bearer " + token}
        )
        assert resp.status_code == 200, resp.text
        assert col.docs == []

    def test_other_store_combo_is_404_not_403(self, matrix_client, monkeypatch):
        # Existence-hiding: a store-scoped caller must not be able to probe for
        # another store's combo by guessing its id.
        col = _stub_combo_col(
            monkeypatch,
            _FakeComboCol([_combo_doc("c-1", "opto-x", store_id="BV-OTHER-01")]),
        )
        token = _mint_token(["STORE_MANAGER"], uid="mgr-1", store_id="BV-MATRIX-01")
        resp = matrix_client.delete(
            _COMBOS_PATH + "/c-1", headers={"Authorization": "Bearer " + token}
        )
        assert resp.status_code == 404, resp.text
        assert len(col.docs) == 1, "another store's template must survive"

    def test_missing_combo_is_404(self, matrix_client, monkeypatch):
        _stub_combo_col(monkeypatch, _FakeComboCol())
        token = _mint_token(["OPTOMETRIST"], uid="opto-a")
        resp = matrix_client.delete(
            _COMBOS_PATH + "/nope", headers={"Authorization": "Bearer " + token}
        )
        assert resp.status_code == 404, resp.text

    # AREA_MANAGER first, for the same reason as the create door: it is the only
    # role this PR's decision moved, so it is the only one whose denial protects
    # that decision. A combo is a SHARED clinical template -- an Area Manager
    # regaining delete would reach OTHER stores' templates.
    @pytest.mark.parametrize("role", ["AREA_MANAGER", "SALES_STAFF", "CASHIER"])
    def test_non_clinical_role_is_forbidden(self, matrix_client, monkeypatch, role):
        col = _stub_combo_col(
            monkeypatch, _FakeComboCol([_combo_doc("c-1", "opto-a")])
        )
        resp = matrix_client.delete(
            _COMBOS_PATH + "/c-1", headers=ALL_ROLE_HEADERS[role]
        )
        assert_middleware_403(resp, "DELETE", _COMBOS_PATH + "/c-1")
        assert len(col.docs) == 1


class TestNoMalformedRoleGateAnywhere:
    """Tripwire for the whole BUG CLASS, not just the two routes that had it.

    ``require_roles(*roles)`` collapses its varargs into ``set(allowed_roles)``.
    Hand it a tuple/list/set instead of splatted strings and the allow-set holds
    a COLLECTION, which no role string can ever match -- a silent, fail-CLOSED
    403 for everyone but SUPERADMIN. Grep cannot see it once the roles live in a
    module constant, so this walks every dependency of every route on the REAL
    app and asserts each require_roles allow-set contains only strings.

    (Verified to have teeth: on the pre-fix tree it reported exactly the two
    /clinical/lens-power-combos routes.)

    The scan reads ANY collection in the closure -- set, frozenset, list or
    tuple -- not just a set. An earlier version matched sets only, so the
    entirely plausible refactor ``allowed = list(allowed_roles)`` (production
    still works) made it inspect ZERO allow-sets and pass green while a
    re-broken gate was live. Hence the counting contract below.
    """

    @staticmethod
    def _scan(app):
        """Return ``(gates, inspected, malformed)`` over every route.

        ``gates``     -- require_roles dependencies reached.
        ``inspected`` -- allow-collections actually READ out of those closures.
        ``malformed`` -- allow-collections holding a non-string member.
        """
        gates = 0
        inspected = 0
        malformed = []
        for route in app.routes:
            dependant = getattr(route, "dependant", None)
            if dependant is None:
                continue
            stack = [dependant]
            while stack:
                node = stack.pop()
                call = getattr(node, "call", None)
                qualname = getattr(call, "__qualname__", "") if call else ""
                if qualname.startswith("require_roles.<locals>"):
                    gates += 1
                    for cell in getattr(call, "__closure__", None) or ():
                        try:
                            value = cell.cell_contents
                        except ValueError:  # pragma: no cover - empty cell
                            continue
                        if not isinstance(value, (set, frozenset, list, tuple)):
                            continue
                        inspected += 1
                        if any(not isinstance(item, str) for item in value):
                            malformed.append(
                                (
                                    sorted(getattr(route, "methods", None) or []),
                                    getattr(route, "path", "?"),
                                    value,
                                )
                            )
                stack.extend(getattr(node, "dependencies", None) or [])
        return gates, inspected, malformed

    def test_every_require_roles_gate_holds_only_role_strings(self):
        from api.main import app as _app

        _gates, _inspected, malformed = self._scan(_app)
        assert malformed == [], (
            "require_roles() was handed a collection instead of splatted role "
            "strings -- these routes 403 EVERYONE except SUPERADMIN:\n  "
            + "\n  ".join(f"{m} {p} -> {v!r}" for m, p, v in malformed)
        )

    def test_the_tripwire_reads_an_allow_set_for_every_gate_it_finds(self):
        # THE anti-vacuity contract. The test above can only be trusted if the
        # scan actually read an allow-collection out of EVERY gate it walked
        # past. require_roles' closure holds exactly one cell (``allowed``), so
        # inspected MUST equal gates. If a refactor changes the closure shape,
        # this goes RED and someone updates the scan -- instead of the malformed
        # check silently inspecting nothing and passing forever.
        from api.main import app as _app
        from api.routers.auth import require_roles

        probe = require_roles("ADMIN")
        assert probe.__qualname__.startswith("require_roles.<locals>"), (
            "require_roles' inner dependency was renamed -- update the "
            "malformed-gate tripwire's qualname match."
        )

        gates, inspected, _malformed = self._scan(_app)
        assert gates > 100, f"expected hundreds of require_roles gates, saw {gates}"
        # NOTE on reach: this is a GLOBAL SUM, so it is only equivalent to the
        # per-gate invariant "every gate yielded exactly one allow-collection"
        # while every gate really does hold exactly one. That holds today -- the
        # real-app per-gate histogram is exactly {1: 539} -- so a 2-cell gate
        # paired with a 0-cell one would currently cancel out and hide. What
        # would break the equivalence: a require_roles closure gaining a second
        # collection cell, an app.mount() sub-app (api/main.py has zero), or a
        # second Depends(require_*( gate factory (require_roles is the only one
        # across 401 sites). If any of those lands, switch this to a per-gate
        # assertion. The scan also cannot see a comma-joined or misspelled role
        # STRING -- that is a different class, covered by the role matrices above.
        assert inspected == gates, (
            f"the malformed-gate scan walked {gates} require_roles gates but "
            f"could only read an allow-collection out of {inspected} of them. "
            "Any gate it cannot read is a gate it cannot check, so "
            "test_every_require_roles_gate_holds_only_role_strings is passing "
            "vacuously. Update _scan for require_roles' new closure shape."
        )

    def test_the_scan_detects_a_planted_malformed_gate(self):
        # Positive control: prove the detector fires on the real bug shape and
        # is not merely returning [] because it looks in the wrong place.
        from fastapi import Depends, FastAPI
        from api.routers.auth import require_roles

        roles = ("ADMIN", "STORE_MANAGER", "OPTOMETRIST")
        planted = FastAPI()

        @planted.post("/planted")
        async def _planted(_u: dict = Depends(require_roles(roles))):  # the BUG
            return {}

        @planted.post("/correct")
        async def _correct(_u: dict = Depends(require_roles(*roles))):
            return {}

        gates, inspected, malformed = self._scan(planted)
        assert gates == 2 and inspected == 2, (gates, inspected)
        assert [p for _m, p, _v in malformed] == ["/planted"], malformed


# ---------------------------------------------------------------------------
# Round 2 - the ownership guard must FAIL CLOSED on an ambiguous identity
# ---------------------------------------------------------------------------
# The first cut of the guard was `doc.get("created_by") != user.get("user_id")`.
# A bare `!=` reads None == None as a MATCH, so a combo with no creator, deleted
# by a caller whose token carries no user_id claim, sailed through:
#     no created_by + no user_id claim -> 200 {"deleted": "c-1"}
# Separately the store leg used _store_scope_or_404, which fails OPEN on a doc
# with no store_id (correct for legacy eye tests, wrong for a brand-new
# collection), so an out-of-store STORE_MANAGER deleted an unattributed combo:
#     UNATTRIBUTED + other-store STORE_MANAGER -> 200 remaining=0
# Both are locked below. Every ambiguous identity must DENY.

# Creator shapes that must never satisfy the ownership test. "deleted user" and
# "disabled user" are spelled out even though this endpoint never resolves a
# user record -- from the handler's side they are simply a created_by that does
# not match the caller, and saying so beats implying a lookup that is not there.
_UNOWNED_CREATORS = [
    pytest.param(None, id="creator-null"),
    pytest.param("", id="creator-empty-string"),
    pytest.param("opto-somebody-else", id="creator-different-user"),
    pytest.param("u-deleted-9999", id="creator-deleted-user"),
    pytest.param("u-disabled-4242", id="creator-disabled-user"),
]

# The four roles the route admits, plus SUPERADMIN.
_PERMITTED_ROLES = ["OPTOMETRIST", "STORE_MANAGER", "ADMIN", "SUPERADMIN"]
_MANAGER_ROLES = {"STORE_MANAGER", "ADMIN", "SUPERADMIN"}


def _combo_missing_creator(combo_id="c-1", store_id="BV-MATRIX-01"):
    """A combo doc with NO created_by key at all (not merely a null one)."""
    doc = _combo_doc(combo_id, "placeholder", store_id=store_id)
    doc.pop("created_by")
    return doc


class TestLensPowerComboDeleteOwnershipFailsClosed:
    """DELETE ownership: absent / null / empty / foreign creator must DENY."""

    @pytest.mark.parametrize("creator", _UNOWNED_CREATORS)
    @pytest.mark.parametrize("role", _PERMITTED_ROLES)
    def test_unowned_combo(self, matrix_client, monkeypatch, role, creator):
        col = _stub_combo_col(
            monkeypatch, _FakeComboCol([_combo_doc("c-1", creator)])
        )
        token = _mint_token([role], uid="caller-1")
        resp = matrix_client.delete(
            _COMBOS_PATH + "/c-1", headers={"Authorization": "Bearer " + token}
        )
        if role in _MANAGER_ROLES:
            # A manager owns the store's shared templates -- allowed by design,
            # and this is the control proving the denials below are about
            # OWNERSHIP and not about the request failing for some other reason.
            assert resp.status_code == 200, resp.text
            assert col.docs == []
        else:
            assert resp.status_code == 403, (
                f"{role} must not delete a combo it does not own "
                f"(created_by={creator!r}), got {resp.status_code}: {resp.text[:200]}"
            )
            assert len(col.docs) == 1, "denied delete must leave the collection UNTOUCHED"

    @pytest.mark.parametrize("role", _PERMITTED_ROLES)
    def test_combo_with_no_created_by_key(self, matrix_client, monkeypatch, role):
        col = _stub_combo_col(monkeypatch, _FakeComboCol([_combo_missing_creator()]))
        token = _mint_token([role], uid="caller-1")
        resp = matrix_client.delete(
            _COMBOS_PATH + "/c-1", headers={"Authorization": "Bearer " + token}
        )
        if role in _MANAGER_ROLES:
            assert resp.status_code == 200, resp.text
        else:
            assert resp.status_code == 403, resp.text
            assert len(col.docs) == 1

    @pytest.mark.parametrize("creator", [None, "", "opto-a"])
    def test_caller_without_a_user_id_claim_can_never_own(
        self, matrix_client, monkeypatch, creator
    ):
        # THE regression: a token with no user_id claim yields caller=None, and
        # `None != None` is False -> the old guard read that as ownership.
        # auth.py's refresh path builds user_id with .get(), so a claimless
        # token is reachable, not hypothetical.
        col = _stub_combo_col(
            monkeypatch, _FakeComboCol([_combo_doc("c-1", creator)])
        )
        payload = {
            "sub": "opto-nouid",
            "username": "opto-nouid",
            "roles": ["OPTOMETRIST"],
            "store_ids": ["BV-MATRIX-01"],
            "active_store_id": "BV-MATRIX-01",
            "exp": datetime.utcnow() + timedelta(hours=2),
        }
        assert "user_id" not in payload
        token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
        resp = matrix_client.delete(
            _COMBOS_PATH + "/c-1", headers={"Authorization": "Bearer " + token}
        )
        assert resp.status_code == 403, (
            f"a caller with no user_id claim must never satisfy the ownership "
            f"test (created_by={creator!r}), got {resp.status_code}: {resp.text[:200]}"
        )
        assert len(col.docs) == 1, "denied delete must leave the collection UNTOUCHED"

    def test_creator_still_may_delete_own_combo(self, matrix_client, monkeypatch):
        # The guard must not have been tightened into refusing the real owner.
        col = _stub_combo_col(
            monkeypatch, _FakeComboCol([_combo_doc("c-1", "opto-a")])
        )
        token = _mint_token(["OPTOMETRIST"], uid="opto-a")
        resp = matrix_client.delete(
            _COMBOS_PATH + "/c-1", headers={"Authorization": "Bearer " + token}
        )
        assert resp.status_code == 200, resp.text
        assert col.docs == []


class TestLensPowerComboUnattributedStore:
    """An unattributed combo (store_id absent/null) must be OUT of scope for a
    store-level caller -- 404, never 403, so the two legs of the guard cannot be
    used together to probe which combos exist.
    """

    @pytest.mark.parametrize(
        "stored", [pytest.param(None, id="store-null"), pytest.param("MISSING", id="store-key-absent")]
    )
    @pytest.mark.parametrize("role", ["OPTOMETRIST", "STORE_MANAGER"])
    def test_store_level_caller_gets_404_on_unattributed_combo(
        self, matrix_client, monkeypatch, role, stored
    ):
        doc = _combo_doc("c-1", "opto-a", store_id=None)
        if stored == "MISSING":
            doc.pop("store_id")
        else:
            doc["store_id"] = None
        col = _stub_combo_col(monkeypatch, _FakeComboCol([doc]))
        token = _mint_token([role], uid="caller-1", store_id="BV-MATRIX-01")
        resp = matrix_client.delete(
            _COMBOS_PATH + "/c-1", headers={"Authorization": "Bearer " + token}
        )
        assert resp.status_code == 404, (
            f"{role} must not reach an unattributed combo, got "
            f"{resp.status_code}: {resp.text[:200]}"
        )
        assert len(col.docs) == 1, "denied delete must leave the collection UNTOUCHED"

    def test_other_store_manager_cannot_delete_unattributed_combo(
        self, matrix_client, monkeypatch
    ):
        # The chair's exact reproduction: before the fix this returned
        # 200 remaining=0.
        col = _stub_combo_col(
            monkeypatch, _FakeComboCol([_combo_doc("c-1", "opto-a", store_id=None)])
        )
        token = _mint_token(["STORE_MANAGER"], uid="mgr-b", store_id="BV-OTHER-99")
        resp = matrix_client.delete(
            _COMBOS_PATH + "/c-1", headers={"Authorization": "Bearer " + token}
        )
        assert resp.status_code == 404, resp.text
        assert len(col.docs) == 1

    def test_store_scope_denial_is_404_not_403(self, matrix_client, monkeypatch):
        # Existence-hiding: the store leg and the ownership leg must not return
        # different statuses in a way that reveals whether a combo exists.
        col = _stub_combo_col(
            monkeypatch,
            _FakeComboCol([_combo_doc("c-1", "opto-a", store_id="BV-OTHER-99")]),
        )
        token = _mint_token(["STORE_MANAGER"], uid="mgr-a", store_id="BV-MATRIX-01")
        present = matrix_client.delete(
            _COMBOS_PATH + "/c-1", headers={"Authorization": "Bearer " + token}
        )
        absent = matrix_client.delete(
            _COMBOS_PATH + "/does-not-exist",
            headers={"Authorization": "Bearer " + token},
        )
        assert present.status_code == 404, present.text
        assert absent.status_code == 404, absent.text
        assert present.json() == absent.json(), (
            "an out-of-store combo must be indistinguishable from a missing one"
        )
        assert len(col.docs) == 1

    def test_admin_may_still_delete_an_unattributed_combo(
        self, matrix_client, monkeypatch
    ):
        # ADMIN/SUPERADMIN are cross-store by design -- somebody has to be able
        # to clean up an orphan.
        col = _stub_combo_col(
            monkeypatch, _FakeComboCol([_combo_doc("c-1", "opto-a", store_id=None)])
        )
        token = _mint_token(["ADMIN"], uid="adm-1", store_id="BV-MATRIX-01")
        resp = matrix_client.delete(
            _COMBOS_PATH + "/c-1", headers={"Authorization": "Bearer " + token}
        )
        assert resp.status_code == 200, resp.text
        assert col.docs == []


class TestLensPowerComboCreateStampsAStore:
    """The other side of the unattributed-combo guard: never MINT one.

    The delete guard now refuses unattributed combos, so the create door must
    not manufacture them -- otherwise the feature creates rows only a SUPERADMIN
    can ever remove. auth.py yields active_store_id=None for a store-less
    session, which is a normal ADMIN account.
    """

    def test_post_without_an_active_store_is_rejected(self, matrix_client, monkeypatch):
        col = _stub_combo_col(monkeypatch, _FakeComboCol())
        payload = {
            "sub": "adm-nostore",
            "user_id": "adm-nostore",
            "username": "adm-nostore",
            "roles": ["ADMIN"],
            "store_ids": [],
            "active_store_id": None,
            "exp": datetime.utcnow() + timedelta(hours=2),
        }
        token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
        resp = matrix_client.post(
            _COMBOS_PATH,
            headers={"Authorization": "Bearer " + token},
            json=_COMBO_BODY,
        )
        assert resp.status_code == 400, (
            f"a store-less session must not mint an unattributed combo, got "
            f"{resp.status_code}: {resp.text[:200]}"
        )
        assert col.inserted == [], "nothing may be written without a store"

    def test_every_created_combo_carries_a_store_id(self, matrix_client, monkeypatch):
        col = _stub_combo_col(monkeypatch, _FakeComboCol())
        token = _mint_token(["OPTOMETRIST"], uid="opto-a", store_id="BV-MATRIX-01")
        resp = matrix_client.post(
            _COMBOS_PATH,
            headers={"Authorization": "Bearer " + token},
            json=_COMBO_BODY,
        )
        assert resp.status_code == 200, resp.text
        assert len(col.inserted) == 1
        assert col.inserted[0]["store_id"] == "BV-MATRIX-01"
        assert col.inserted[0]["created_by"] == "opto-a"


class TestLensPowerComboListGate:
    """GET now states its own role gate instead of leaning on the POLICY row,
    and its store filter fails closed.
    """

    @pytest.mark.parametrize(
        "role", ["OPTOMETRIST", "STORE_MANAGER", "ADMIN", "AREA_MANAGER", "SUPERADMIN"]
    )
    def test_read_roles_allowed(self, matrix_client, monkeypatch, role):
        _stub_combo_col(
            monkeypatch, _FakeComboCol([_combo_doc("c-1", "opto-a")])
        )
        token = _mint_token([role], uid="caller-1")
        resp = matrix_client.get(
            _COMBOS_PATH, headers={"Authorization": "Bearer " + token}
        )
        assert resp.status_code == 200, (
            f"{role} must keep its combo read access, got "
            f"{resp.status_code}: {resp.text[:200]}"
        )

    @pytest.mark.parametrize(
        "role", ["SALES_STAFF", "CASHIER", "SALES_CASHIER", "WORKSHOP_STAFF",
                 "ACCOUNTANT", "CATALOG_MANAGER", "DESIGN_MANAGER"]
    )
    def test_non_read_roles_forbidden(self, matrix_client, monkeypatch, role):
        _stub_combo_col(
            monkeypatch, _FakeComboCol([_combo_doc("c-1", "opto-a")])
        )
        resp = matrix_client.get(_COMBOS_PATH, headers=ALL_ROLE_HEADERS[role])
        assert resp.status_code == 403, (
            f"{role} must not read clinical templates, got {resp.status_code}"
        )

    def test_get_gate_is_explicit_not_policy_only(self):
        # Pin the handler-level gate itself: the POLICY row is defence in depth,
        # not the only lock. Reached through the route's dependency tree, so a
        # deleted Depends() fails this even though the middleware still passes.
        from api.main import app as _app
        from api.routers.clinical import _COMBO_READ_ROLES

        for route in _app.routes:
            if getattr(route, "path", None) == _COMBOS_PATH and "GET" in (
                getattr(route, "methods", None) or set()
            ):
                gate_sets = []
                stack = [route.dependant]
                while stack:
                    node = stack.pop()
                    call = getattr(node, "call", None)
                    if call is not None and getattr(
                        call, "__qualname__", ""
                    ).startswith("require_roles.<locals>"):
                        for cell in call.__closure__ or ():
                            # Same widened container check as _scan above. This
                            # sibling was left on (set, frozenset) for a round,
                            # so the production-PRESERVING refactor
                            # `allowed = list(...)` + `roles & set(allowed)` made
                            # this assertion fire and claim the gate was missing
                            # -- a false diagnostic pointing at a file the
                            # developer never touched. Keep the two in step.
                            if isinstance(
                                cell.cell_contents, (set, frozenset, list, tuple)
                            ):
                                gate_sets.append(set(cell.cell_contents))
                    stack.extend(getattr(node, "dependencies", None) or [])
                assert gate_sets, (
                    "GET /lens-power-combos has no require_roles gate -- it is "
                    "relying on its POLICY row alone, the asymmetry that "
                    "produced the malformed-tuple bug on its write twins. "
                    "(If require_roles' closure shape changed, widen the "
                    "container check just above before believing this message.)"
                )
                assert set(_COMBO_READ_ROLES) in gate_sets, gate_sets
                # ...and the constant must AGREE with the POLICY row it claims to
                # mirror. Asserting only `set(_COMBO_READ_ROLES) in gate_sets`
                # imports the very constant under test, so widening the constant
                # moves the expectation with the subject and nothing fails. Pin
                # it to the POLICY literal instead: the handler gate can then
                # never drift from the middleware row while outside access looks
                # unchanged.
                _row = policy_for("GET", _COMBOS_PATH)
                assert set(_COMBO_READ_ROLES) == set(_row["allowed"]), (
                    "clinical._COMBO_READ_ROLES and the GET POLICY row have "
                    "drifted apart",
                    sorted(_COMBO_READ_ROLES),
                    sorted(_row["allowed"]),
                )
                break
        else:  # pragma: no cover - route must exist
            pytest.fail("GET /lens-power-combos route not found")

    def test_store_level_caller_without_a_store_sees_nothing(
        self, matrix_client, monkeypatch
    ):
        # The read-side sibling of the unattributed-combo guard: an empty filter
        # would return EVERY store's templates.
        _stub_combo_col(
            monkeypatch,
            _FakeComboCol(
                [
                    _combo_doc("c-a", "opto-a", store_id="BV-MATRIX-01"),
                    _combo_doc("c-b", "opto-b", store_id="BV-OTHER-99"),
                ]
            ),
        )
        payload = {
            "sub": "mgr-nostore",
            "user_id": "mgr-nostore",
            "username": "mgr-nostore",
            "roles": ["STORE_MANAGER"],
            "store_ids": [],
            "active_store_id": None,
            "exp": datetime.utcnow() + timedelta(hours=2),
        }
        token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
        resp = matrix_client.get(
            _COMBOS_PATH, headers={"Authorization": "Bearer " + token}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"combos": [], "total": 0}, (
            "a store-level caller with no resolvable store must not receive "
            "every store's templates"
        )
