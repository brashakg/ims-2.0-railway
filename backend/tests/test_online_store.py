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
    # 2026-07-05 truth refresh: phases 1-5 shipped; the summary now reports the
    # REAL push gate (not hardcoded foundation values).
    assert body["status"] == "cutover-ready"
    assert body["phase"] == 5
    # Single-writer safety: the triple gate is unarmed in tests -> writes OFF.
    assert body["shopify_writes_enabled"] is False
    assert body["push_mode"] is None or body["push_mode"].get("is_live") is False
    # Feature list is surfaced so the owner sees the roadmap + status.
    keys = {f["key"] for f in body["planned_features"]}
    assert {"collections", "mega_menu", "image_design_queue", "shopify_push"} <= keys
    # Foundation counts present + fail-soft (all ints, never missing). The
    # summary surfaces a count per PIM tier (expanded from the original two).
    expected_count_keys = {
        "products", "variants", "collections", "menus",
        "images_pending_design", "customers", "orders",
    }
    assert expected_count_keys <= set(body["counts"])
    for _v in body["counts"].values():
        assert isinstance(_v, int)


def test_summary_products_ecom_shape(client, auth_headers):
    """The Phase-1 truth slice adds a products_ecom block (draft / published /
    staged_total / text_only) -- always present + integer, fail-soft to zeros."""
    r = client.get(SUMMARY, headers=auth_headers)
    assert r.status_code == 200, r.text
    pe = r.json().get("products_ecom")
    assert isinstance(pe, dict), "products_ecom block missing from summary"
    assert {"draft", "published", "staged_total", "text_only"} <= set(pe)
    for _v in pe.values():
        assert isinstance(_v, int)


def test_summary_products_ecom_counts(client, auth_headers):
    """DB-backed correctness: DRAFT/PUBLISHED split + text-only (no images) count.
    Uses deltas against a live baseline so pre-existing rows don't matter, and
    skips fail-soft when no DB is connected (local run)."""
    import pytest

    from api.dependencies import get_db

    conn = get_db()
    db = getattr(conn, "db", None) if conn is not None else None
    # Only meaningful against a REAL mongo (CI): the seeded MockDatabase reports
    # "connected" but does not persist inserts, and counting is fail-soft to
    # zeros -- skip there rather than assert against a stub.
    if db is None or "Mock" in type(db).__name__:
        pytest.skip("no real DB connected (counting is fail-soft to zeros)")

    coll = conn.get_collection("catalog_products")
    before = client.get(SUMMARY, headers=auth_headers).json()["products_ecom"]
    docs = [
        # DRAFT with an image -> draft +1, staged +1, NOT text_only.
        {"product_id": "ZZ_ECOM_1", "ecom": {"status": "DRAFT"}, "images": ["http://x/a.jpg"]},
        # DRAFT with an empty images array -> draft +1, staged +1, text_only +1.
        {"product_id": "ZZ_ECOM_2", "ecom": {"status": "DRAFT"}, "images": []},
        # DRAFT with images field missing -> draft +1, staged +1, text_only +1.
        {"product_id": "ZZ_ECOM_3", "ecom": {"status": "DRAFT"}},
        # PUBLISHED with an image -> published +1, staged +1, NOT text_only.
        {"product_id": "ZZ_ECOM_4", "ecom": {"status": "PUBLISHED"}, "images": ["http://x/b.jpg"]},
    ]
    coll.insert_many(docs)
    try:
        after = client.get(SUMMARY, headers=auth_headers).json()["products_ecom"]
        assert after["draft"] - before["draft"] == 3
        assert after["published"] - before["published"] == 1
        assert after["staged_total"] - before["staged_total"] == 4
        assert after["text_only"] - before["text_only"] == 2
    finally:
        coll.delete_many({"product_id": {"$regex": "^ZZ_ECOM_"}})


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
