"""
Tests for the Online Store module router (BVI Phase 1 foundation).

Asserts the stub GET /online-store/summary:
  - mounts and returns the module status + planned features + counts,
  - is role-gated (SUPERADMIN 200; SALES_STAFF 403),
  - is catalogued in rbac_policy.POLICY with the ecom role set,
  - is fail-soft (counts present even with no live data).

Uses the shared session `client` + `auth_headers` (SUPERADMIN) / `staff_headers`
(SALES_STAFF) fixtures from conftest.py.

Run: JWT_SECRET_KEY=test python -m pytest backend/tests/test_online_store.py -q
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

from api.services import rbac_policy as rbac  # noqa: E402

SUMMARY = "/api/v1/online-store/summary"


# ---------------------------------------------------------------------------
# Endpoint behaviour (live app via TestClient)
# ---------------------------------------------------------------------------

def test_summary_mounts_and_returns_status(client, auth_headers):
    r = client.get(SUMMARY, headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["module"] == "online_store"
    assert body["status"] == "foundation"
    assert body["phase"] == 1
    # Single-writer safety: writes are OFF in Phase 1.
    assert body["shopify_writes_enabled"] is False
    # Planned feature list is surfaced so the owner sees the roadmap.
    keys = {f["key"] for f in body["planned_features"]}
    assert {"collections", "mega_menu", "image_design_queue", "shopify_push"} <= keys
    # Foundation counts present + fail-soft (ints, never missing).
    assert set(body["counts"]) == {"catalog_variants", "products_with_ecom"}
    assert isinstance(body["counts"]["catalog_variants"], int)
    assert isinstance(body["counts"]["products_with_ecom"], int)


def test_summary_forbidden_for_sales_staff(client, staff_headers):
    """SALES_STAFF is outside the ecom role set -> 403."""
    r = client.get(SUMMARY, headers=staff_headers)
    assert r.status_code == 403, r.text


def test_summary_requires_auth(client):
    """No token -> 401 (the route's own get_current_user via require_roles)."""
    r = client.get(SUMMARY)
    assert r.status_code in (401, 403), r.text


# ---------------------------------------------------------------------------
# RBAC policy catalogue (regression lock)
# ---------------------------------------------------------------------------

def test_summary_is_catalogued_with_ecom_roles():
    entry = rbac.policy_for("GET", SUMMARY)
    assert entry is not None, "online-store summary not catalogued in rbac_policy"
    assert set(entry["allowed"]) == {
        "ADMIN",
        "CATALOG_MANAGER",
        "DESIGN_MANAGER",
        "SUPERADMIN",
    }


def test_check_access_allows_ecom_roles_denies_others():
    assert rbac.check_access("GET", SUMMARY, ["SUPERADMIN"]) is True
    assert rbac.check_access("GET", SUMMARY, ["ADMIN"]) is True
    assert rbac.check_access("GET", SUMMARY, ["CATALOG_MANAGER"]) is True
    assert rbac.check_access("GET", SUMMARY, ["DESIGN_MANAGER"]) is True
    for role in ("SALES_STAFF", "CASHIER", "OPTOMETRIST", "WORKSHOP_STAFF", "ACCOUNTANT"):
        assert rbac.check_access("GET", SUMMARY, [role]) is False, role


def test_design_manager_in_role_matrix():
    """The new lowest-privilege ecom role is registered in the canonical matrix."""
    assert "DESIGN_MANAGER" in rbac.ALL_ROLES
