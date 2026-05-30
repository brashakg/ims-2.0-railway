"""
Tests for the central RBAC policy registry (backend/api/services/rbac_policy.py).

The headline test is COVERAGE: every live ``/api/v1`` route must appear in
``POLICY``. That is the regression lock - it fails the moment someone adds an
endpoint without recording its access gate, which is exactly when an
un-reviewed privilege gap slips in.

The spot-checks below assert the registry matches the ACTUAL router gates
(read from source when this table was built). They are intentionally grounded
in real, sensitive endpoints so the table can't silently drift looser than the
code it mirrors.

Run: ``JWT_SECRET_KEY=test python -m pytest backend/tests/test_rbac_policy.py -q``
"""

import os
import sys

import pytest

# Ensure the backend package root is importable + JWT secret present (auth.py
# raises at import time without it).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

from api.services import rbac_policy as rbac  # noqa: E402

# NOTE: the ``app`` fixture is provided session-scoped by tests/conftest.py.


# ---------------------------------------------------------------------------
# COVERAGE - the regression lock
# ---------------------------------------------------------------------------

def test_no_uncatalogued_routes(app):
    """Every live /api/v1 route must have a POLICY entry. If this fails, a new
    endpoint shipped without an access-gate decision - add it to POLICY (and the
    matrix doc) with the correct ``allowed`` value."""
    missing = rbac.uncatalogued_routes(app)
    assert missing == [], (
        f"{len(missing)} /api/v1 route(s) missing from rbac_policy.POLICY:\n"
        + "\n".join(f"  {m['method']} {m['path']}" for m in missing)
    )


def test_no_stale_policy_entries(app):
    """Every POLICY entry should correspond to a live route - a stale row means
    the table drifted from the app and ``check_access`` could authorize a path
    that no longer exists (or mask a renamed one)."""
    live = set()
    for route in app.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if not path or not methods or not path.startswith("/api/v1"):
            continue
        for method in methods:
            if method in ("HEAD", "OPTIONS"):
                continue
            live.add((method, path))
    stale = [
        (e["method"], e["path"])
        for e in rbac.POLICY
        if (e["method"], e["path"]) not in live
    ]
    assert stale == [], "Stale POLICY entries (route no longer exists): " + repr(stale)


def test_policy_entries_well_formed():
    """Each entry has method+path+allowed; allowed is PUBLIC, AUTHENTICATED, or a
    non-empty list of KNOWN roles (never INVESTOR - read-only via middleware,
    never an allow-list member)."""
    known = set(rbac.ALL_ROLES)
    for e in rbac.POLICY:
        assert {"method", "path"} <= set(e.keys())
        assert e["method"] in ("GET", "POST", "PUT", "PATCH", "DELETE")
        assert str(e["path"]).startswith("/api/v1")
        allowed = e["allowed"]
        if allowed in (rbac.PUBLIC, rbac.AUTHENTICATED):
            continue
        assert isinstance(allowed, list) and allowed, f"bad allowed for {e}"
        assert set(allowed) <= known, f"unknown role(s) in {e}"
        assert "INVESTOR" not in allowed, f"INVESTOR must not be an allow member: {e}"


def test_no_duplicate_policy_keys():
    """(method, path) is a primary key - duplicates would make resolution
    ambiguous and hide a row."""
    keys = [(e["method"], e["path"]) for e in rbac.POLICY]
    dupes = {k for k in keys if keys.count(k) > 1}
    assert not dupes, f"duplicate POLICY keys: {dupes}"


# ---------------------------------------------------------------------------
# SPOT-CHECKS - grounded in the real router gates
# ---------------------------------------------------------------------------

def _allowed(method, path):
    entry = rbac.policy_for(method, path)
    assert entry is not None, f"{method} {path} not catalogued"
    return entry["allowed"]


def test_orders_create_is_pos_roles_not_accountant_or_optometrist():
    """POST /orders is gated to POS-facing roles (POS_WRITE_ROLES). ACCOUNTANT,
    OPTOMETRIST, CATALOG_MANAGER, WORKSHOP_STAFF, CASHIER are excluded."""
    allowed = _allowed("POST", "/api/v1/orders")
    assert isinstance(allowed, list)
    for pos_role in ("SALES_CASHIER", "SALES_STAFF", "STORE_MANAGER", "ADMIN"):
        assert pos_role in allowed
    for excluded in ("ACCOUNTANT", "OPTOMETRIST", "CATALOG_MANAGER", "CASHIER"):
        assert excluded not in allowed


def test_store_write_is_hq_only():
    """Creating / editing / deleting a store is ADMIN/SUPERADMIN only."""
    for method in ("POST",):
        assert sorted(_allowed(method, "/api/v1/stores")) == ["ADMIN", "SUPERADMIN"]
    for method in ("PUT", "DELETE"):
        assert sorted(_allowed(method, "/api/v1/stores/{store_id}")) == [
            "ADMIN",
            "SUPERADMIN",
        ]


def test_all_jarvis_routes_superadmin_only():
    """Non-negotiable: every /api/v1/jarvis/* route is SUPERADMIN-only."""
    jarvis = [e for e in rbac.POLICY if e["path"].startswith("/api/v1/jarvis")]
    assert jarvis, "no jarvis routes catalogued"
    for e in jarvis:
        assert e["allowed"] == ["SUPERADMIN"], f"jarvis route not superadmin: {e}"


def test_reports_inventory_valuation_is_finance_roles():
    """Inventory valuation is owner-financials -> finance role set (router-level
    finance gate); not open to POS/clinical staff."""
    allowed = _allowed("GET", "/api/v1/reports/inventory/valuation")
    assert set(allowed) == {"ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"}
    assert "OPTOMETRIST" not in allowed
    assert "SALES_STAFF" not in allowed


def test_audit_verify_is_superadmin():
    """Audit chain integrity is a CEO-level control surface (inline
    _require_superadmin)."""
    assert _allowed("GET", "/api/v1/audit/verify") == ["SUPERADMIN"]


def test_advanced_ml_analytics_superadmin_only():
    """analytics-v2 ML surfaces (anomaly / churn / forecast / vendor-margins)
    are SUPERADMIN-only (inline gate)."""
    for path in (
        "/api/v1/analytics-v2/anomaly-detection",
        "/api/v1/analytics-v2/churn-prediction",
        "/api/v1/analytics-v2/demand-forecast",
        "/api/v1/analytics-v2/vendor-margins",
    ):
        assert _allowed("GET", path) == ["SUPERADMIN"], path


def test_settings_system_put_superadmin_only():
    assert _allowed("PUT", "/api/v1/settings/system") == ["SUPERADMIN"]


def test_loyalty_settings_superadmin_only():
    assert _allowed("PUT", "/api/v1/loyalty/settings") == ["SUPERADMIN"]


def test_admin_router_is_admin_gated():
    """The admin router (APIRouter-level _require_admin_role) gates its rows to
    ADMIN/SUPERADMIN - including pricing/HSN/role-cap/system-backup writes."""
    for method, path in (
        ("POST", "/api/v1/admin/brands"),
        ("POST", "/api/v1/admin/hsn"),
        ("POST", "/api/v1/admin/discounts/role-caps"),
        ("POST", "/api/v1/admin/system/backups"),
        ("PUT", "/api/v1/admin/system/settings"),
    ):
        assert sorted(_allowed(method, path)) == ["ADMIN", "SUPERADMIN"], (method, path)


def test_techcherry_import_superadmin_only():
    """TechCherry migration sits under /admin but is strictly SUPERADMIN."""
    assert _allowed("POST", "/api/v1/admin/techcherry/import") == ["SUPERADMIN"]


def test_finance_owner_reports_narrowed_to_org_admins():
    """Owner-level finance reports drop AREA/STORE manager via inline
    _require_finance_admin -> SUPERADMIN/ADMIN/ACCOUNTANT."""
    for path in (
        "/api/v1/finance/owner-dashboard",
        "/api/v1/finance/cash-flow-forecast",
        "/api/v1/finance/itc-register",
    ):
        allowed = _allowed("GET", path)
        assert set(allowed) == {"ACCOUNTANT", "ADMIN", "SUPERADMIN"}, path
        assert "STORE_MANAGER" not in allowed


def test_portal_and_webhook_routes_public():
    """Customer-portal (tokenized/OTP) and webhook (HMAC) routes are PUBLIC -
    they carry no IMS auth dependency; their own mechanism protects them."""
    assert _allowed("GET", "/api/v1/portal/track/{token}") == rbac.PUBLIC
    assert _allowed("POST", "/api/v1/portal/rx/request-otp") == rbac.PUBLIC
    assert _allowed("POST", "/api/v1/webhooks/razorpay") == rbac.PUBLIC
    assert _allowed("POST", "/api/v1/webhooks/shopify") == rbac.PUBLIC
    assert _allowed("GET", "/api/v1/vendor-portal/{token_id}/jobs") == rbac.PUBLIC


def test_auth_login_refresh_public():
    assert _allowed("POST", "/api/v1/auth/login") == rbac.PUBLIC
    assert _allowed("POST", "/api/v1/auth/refresh") == rbac.PUBLIC


# ---------------------------------------------------------------------------
# policy_for - path-param resolution + specificity
# ---------------------------------------------------------------------------

def test_policy_for_resolves_path_params():
    """A concrete request path with an id segment matches the templated entry."""
    entry = rbac.policy_for("PUT", "/api/v1/stores/BV-BOK-01")
    assert entry is not None
    assert entry["path"] == "/api/v1/stores/{store_id}"
    assert sorted(entry["allowed"]) == ["ADMIN", "SUPERADMIN"]


def test_policy_for_resolves_multi_param():
    entry = rbac.policy_for(
        "DELETE", "/api/v1/vendors/V123/portal-token/TOK9"
    )
    assert entry is not None
    assert entry["path"] == "/api/v1/vendors/{vendor_id}/portal-token/{token_id}"


def test_policy_for_prefers_most_specific_literal():
    """A literal segment must beat a {param} sibling at the same depth.

    /api/v1/catalog/products/export (literal) and
    /api/v1/catalog/products/{product_id} (param) co-exist at the same depth.
    A request for .../export must resolve to the literal entry, not the param.
    """
    literal = rbac.policy_for("GET", "/api/v1/catalog/products/export")
    assert literal is not None
    assert literal["path"] == "/api/v1/catalog/products/export"
    # ...while a real id resolves to the param entry.
    param = rbac.policy_for("GET", "/api/v1/catalog/products/SKU-123")
    assert param is not None
    assert param["path"] == "/api/v1/catalog/products/{product_id}"
    # and the two gates genuinely differ, proving the specificity tie-break
    # picked the right row (export drops STORE roles; {id} read is open).
    assert literal["allowed"] != param["allowed"]


def test_policy_for_unknown_route_returns_none():
    assert rbac.policy_for("GET", "/api/v1/does/not/exist") is None
    assert rbac.policy_for("DELETE", "/api/v1/orders") is None  # no DELETE on collection


def test_store_scoped_flag_surfaces():
    """Order detail read is store-scoped (validate_store_access)."""
    assert rbac.is_store_scoped("GET", "/api/v1/orders/ORD-1") is True


# ---------------------------------------------------------------------------
# check_access - allow / deny correctness
# ---------------------------------------------------------------------------

def test_check_access_role_gate_allow_and_deny():
    # POS role allowed to create orders; accountant denied.
    assert rbac.check_access("POST", "/api/v1/orders", ["SALES_CASHIER"]) is True
    assert rbac.check_access("POST", "/api/v1/orders", ["ACCOUNTANT"]) is False
    assert rbac.check_access("POST", "/api/v1/orders", ["OPTOMETRIST"]) is False


def test_check_access_superadmin_passes_everything_role_gated():
    """SUPERADMIN passes any role-gated route even when not explicitly listed
    in a (hypothetically) SUPERADMIN-less list - mirrors require_roles."""
    assert rbac.check_access("GET", "/api/v1/jarvis/agents", ["SUPERADMIN"]) is True
    assert rbac.check_access("PUT", "/api/v1/stores/BV-1", ["SUPERADMIN"]) is True
    assert rbac.check_access("GET", "/api/v1/audit/verify", ["SUPERADMIN"]) is True


def test_check_access_jarvis_denied_for_non_superadmin():
    for role in ("ADMIN", "STORE_MANAGER", "SALES_STAFF", "ACCOUNTANT"):
        assert rbac.check_access("GET", "/api/v1/jarvis/agents", [role]) is False, role


def test_check_access_public_route_allows_no_roles():
    assert rbac.check_access("POST", "/api/v1/auth/login", []) is True
    assert rbac.check_access("GET", "/api/v1/portal/track/abc", []) is True


def test_check_access_authenticated_requires_some_role():
    # AUTHENTICATED routes need a logged-in caller (any role); empty -> deny.
    assert rbac.check_access("GET", "/api/v1/customers", ["SALES_STAFF"]) is True
    assert rbac.check_access("GET", "/api/v1/customers", []) is False


def test_check_access_unknown_route_denied_by_default():
    assert rbac.check_access("GET", "/api/v1/nope", ["SUPERADMIN"]) is False


def test_check_access_finance_allow_deny():
    valn = "/api/v1/reports/inventory/valuation"
    assert rbac.check_access("GET", valn, ["ACCOUNTANT"]) is True
    assert rbac.check_access("GET", valn, ["STORE_MANAGER"]) is True
    assert rbac.check_access("GET", valn, ["SALES_STAFF"]) is False
    assert rbac.check_access("GET", valn, ["OPTOMETRIST"]) is False
