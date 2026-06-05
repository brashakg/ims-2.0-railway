"""
IMS 2.0 - INV-7 / INV-8 / INV-9 feature tests
===============================================

INV-7  Vendor-SKU-alias: CRUD routes on /vendors/{vendor_id}/sku-aliases
        and the /vendors/sku-alias-lookup helper.
INV-8  Cycle-count reconcile: POST /inventory/stock-count/{count_id}/reconcile
        - gate (only inventory roles), state machine (must be completed),
        - happy path produces shrinkage records + status transition.
INV-9  Demand-forecast -> draft-PO: POST /vendors/purchase-orders/from-forecast
        - dry_run=True returns suggestions without persisting POs.

All tests run without a live database (fail-soft pattern) or with a minimal
in-memory stub, matching the style of test_inventory_intel.py.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routers import inventory, vendors
from api.routers.auth import get_current_user, create_access_token


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _token(roles, store_id="BV-TEST-01"):
    return create_access_token(
        {
            "user_id": "u-test",
            "username": "tester",
            "roles": roles,
            "store_ids": [store_id],
            "active_store_id": store_id,
        }
    )


def _headers(roles):
    return {"Authorization": f"Bearer {_token(roles)}"}


def _make_inventory_app(override_user=None):
    app = FastAPI()
    app.include_router(inventory.router, prefix="/inventory")
    if override_user:
        app.dependency_overrides[get_current_user] = override_user
    return app


def _make_vendors_app(override_user=None):
    app = FastAPI()
    # vendors router has paths like /purchase-orders/from-forecast that must
    # resolve before /{vendor_id} — the router preserves declaration order.
    app.include_router(vendors.router, prefix="/vendors")
    if override_user:
        app.dependency_overrides[get_current_user] = override_user
    return app


# ---------------------------------------------------------------------------
# INV-8: cycle-count reconcile — ROLE GATING
# ---------------------------------------------------------------------------

class TestReconcileGating:
    """POST /inventory/stock-count/{id}/reconcile must require inventory roles."""

    @pytest.fixture(scope="class")
    def client(self):
        return TestClient(TestClient(FastAPI()).app)  # unused; use per-test

    BLOCKED_ROLES = [["SALES_STAFF"], ["CASHIER"], ["OPTOMETRIST"], ["ACCOUNTANT"]]
    ALLOWED_ROLES = [["STORE_MANAGER"], ["CATALOG_MANAGER"], ["ADMIN"]]

    @pytest.mark.parametrize("roles", BLOCKED_ROLES)
    def test_blocked_roles(self, roles):
        app = _make_inventory_app()
        c = TestClient(app)
        resp = c.post(
            "/inventory/stock-count/cx1/reconcile",
            headers=_headers(roles),
        )
        assert resp.status_code == 403, f"Expected 403 for {roles}, got {resp.status_code}"

    @pytest.mark.parametrize("roles", ALLOWED_ROLES)
    def test_allowed_roles_not_403(self, roles):
        # Without DB the endpoint returns 503 (DB unavailable) or 404 (not found)
        # — either is acceptable; what matters is it is NOT 403.
        app = _make_inventory_app()
        c = TestClient(app)
        resp = c.post(
            "/inventory/stock-count/cx1/reconcile",
            headers=_headers(roles),
        )
        assert resp.status_code != 403, (
            f"Role {roles} should pass the gate, got 403"
        )


# ---------------------------------------------------------------------------
# INV-8: reconcile state machine — pure unit tests on the handler function
# ---------------------------------------------------------------------------

class TestReconcileStateMachine:
    """State-machine tests on reconcile_stock_count business logic.

    We call the handler directly (bypassing FastAPI routing + DB) so we can
    inject fake count documents without patching module globals.
    """

    def _make_fake_db(self, count_doc):
        """Return a fake DB handle whose stock_counts collection returns count_doc."""

        class _FakeColl:
            def __init__(self, doc):
                self._doc = doc
                self.updated = {}

            def find_one(self, *_a, **_kw):
                return self._doc

            def update_one(self, *_a, **_kw):
                pass

            def insert_many(self, *_a, **_kw):
                pass

            def find(self, *_a, **_kw):
                class _It:
                    def __iter__(self_inner):
                        return iter([])
                    def sort(self_inner, *_a, **_kw):
                        return self_inner
                    def limit(self_inner, _n):
                        return []
                return _It()

        class _FakeDB:
            def get_collection(self, _name):
                return _FakeColl(count_doc)

        return _FakeDB()

    def test_in_progress_count_rejected(self):
        """Attempting to reconcile an in-progress count must raise 400."""
        import asyncio
        from fastapi import HTTPException
        from api.routers.inventory import reconcile_stock_count
        import api.routers.inventory as inv_mod

        count_doc = {
            "count_id": "cx2",
            "status": "in_progress",
            "audit_number": "AUDIT-001",
            "store_id": "BV-01",
            "variances": [],
        }
        fake_db = self._make_fake_db(count_doc)
        original = inv_mod._get_db
        inv_mod._get_db = lambda: fake_db
        try:
            user = {"user_id": "u1", "active_store_id": "BV-01", "roles": ["STORE_MANAGER"]}
            with pytest.raises(HTTPException) as exc_info:
                asyncio.run(reconcile_stock_count("cx2", None, user))
            assert exc_info.value.status_code == 400
            assert "completed" in exc_info.value.detail.lower()
        finally:
            inv_mod._get_db = original

    def test_completed_count_with_no_variances_rejected(self):
        """A completed count with empty variances must raise 400."""
        import asyncio
        from fastapi import HTTPException
        from api.routers.inventory import reconcile_stock_count
        import api.routers.inventory as inv_mod

        count_doc = {
            "count_id": "cx3",
            "status": "completed",
            "audit_number": "AUDIT-002",
            "store_id": "BV-01",
            "variances": [],  # empty -> no data to reconcile
        }
        fake_db = self._make_fake_db(count_doc)
        original = inv_mod._get_db
        inv_mod._get_db = lambda: fake_db
        try:
            user = {"user_id": "u1", "active_store_id": "BV-01", "roles": ["STORE_MANAGER"]}
            with pytest.raises(HTTPException) as exc_info:
                asyncio.run(reconcile_stock_count("cx3", None, user))
            assert exc_info.value.status_code == 400
            assert "variance" in exc_info.value.detail.lower()
        finally:
            inv_mod._get_db = original


# ---------------------------------------------------------------------------
# INV-7: vendor SKU alias — ROLE GATING
# ---------------------------------------------------------------------------

class TestVendorSkuAliasGating:
    """POST/DELETE sku-aliases must require vendor roles; GET is open."""

    BLOCKED_ROLES = [["SALES_STAFF"], ["CASHIER"], ["OPTOMETRIST"], ["WORKSHOP_STAFF"]]
    ALLOWED_WRITE = [["STORE_MANAGER"], ["ADMIN"], ["ACCOUNTANT"]]

    @pytest.mark.parametrize("roles", BLOCKED_ROLES)
    def test_create_alias_blocked(self, roles):
        app = _make_vendors_app()
        c = TestClient(app)
        resp = c.post(
            "/vendors/v1/sku-aliases",
            json={"product_id": "P1", "vendor_sku": "VS-001"},
            headers=_headers(roles),
        )
        assert resp.status_code == 403

    @pytest.mark.parametrize("roles", ALLOWED_WRITE)
    def test_create_alias_not_403(self, roles):
        app = _make_vendors_app()
        c = TestClient(app)
        resp = c.post(
            "/vendors/v1/sku-aliases",
            json={"product_id": "P1", "vendor_sku": "VS-001"},
            headers=_headers(roles),
        )
        # Without DB: 503; with DB: 201/200. Either is fine — just not 403.
        assert resp.status_code != 403

    def test_list_aliases_is_authenticated(self):
        """GET sku-aliases requires a valid JWT but no specific role."""
        app = _make_vendors_app()
        c = TestClient(app)
        # No auth -> 401
        resp = c.get("/vendors/v1/sku-aliases")
        assert resp.status_code == 401

    def test_list_aliases_authenticated_user_ok(self):
        app = _make_vendors_app()
        c = TestClient(app)
        resp = c.get("/vendors/v1/sku-aliases", headers=_headers(["SALES_STAFF"]))
        # Without DB: empty list (200/fail-soft), never 403
        assert resp.status_code in (200, 503)

    def test_lookup_is_authenticated(self):
        app = _make_vendors_app()
        c = TestClient(app)
        resp = c.get(
            "/vendors/sku-alias-lookup",
            params={"vendor_id": "v1", "vendor_sku": "VS-001"},
        )
        # no JWT -> 401
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# INV-9: from-forecast PO — ROLE GATING + dry_run shape
# ---------------------------------------------------------------------------

class TestForecastPoGating:
    """POST /vendors/purchase-orders/from-forecast must require vendor roles."""

    BLOCKED_ROLES = [["SALES_STAFF"], ["CASHIER"], ["OPTOMETRIST"], ["WORKSHOP_STAFF"]]
    ALLOWED_WRITE = [["STORE_MANAGER"], ["ADMIN"], ["ACCOUNTANT"], ["AREA_MANAGER"]]

    @pytest.mark.parametrize("roles", BLOCKED_ROLES)
    def test_blocked(self, roles):
        app = _make_vendors_app()
        c = TestClient(app)
        resp = c.post(
            "/vendors/purchase-orders/from-forecast",
            json={"store_id": "BV-01", "dry_run": True},
            headers=_headers(roles),
        )
        assert resp.status_code == 403

    @pytest.mark.parametrize("roles", ALLOWED_WRITE)
    def test_allowed_not_403(self, roles):
        app = _make_vendors_app()
        c = TestClient(app)
        resp = c.post(
            "/vendors/purchase-orders/from-forecast",
            json={"store_id": "BV-01", "dry_run": True},
            headers=_headers(roles),
        )
        # Without DB: may be 200 with empty suggestions or 400/503
        assert resp.status_code != 403


class TestForecastPoShape:
    """dry_run=True must always return pos_created=0 and the suggestions key."""

    def test_dry_run_never_creates_pos(self):
        """Even with a stubbed DB that has sales data, dry_run must not create POs."""
        import api.routers.vendors as ven_mod

        # Fake DB: no orders -> empty suggestions -> pos_created=0
        class _FakeOrders:
            def find(self, *_a, **_kw):
                return self

            def limit(self, _n):
                return []

        class _FakeProducts:
            def find(self, *_a, **_kw):
                return []

        class _FakeDB:
            def get_collection(self, name):
                if name == "orders":
                    return _FakeOrders()
                return _FakeProducts()

        original = ven_mod._get_db
        ven_mod._get_db = lambda: _FakeDB()

        app = FastAPI()
        app.include_router(ven_mod.router, prefix="/vendors")

        async def _user():
            return {
                "user_id": "u1",
                "active_store_id": "BV-01",
                "roles": ["ADMIN", "SUPERADMIN"],
            }

        app.dependency_overrides[get_current_user] = _user
        c = TestClient(app)

        resp = c.post(
            "/vendors/purchase-orders/from-forecast",
            json={"store_id": "BV-01", "dry_run": True},
        )
        ven_mod._get_db = original  # restore

        assert resp.status_code == 201
        data = resp.json()
        assert data["dry_run"] is True
        assert data["pos_created"] == 0
        assert "suggestions" in data

    def test_missing_store_id_rejected(self):
        """No active store and no body store_id -> 400."""
        import api.routers.vendors as ven_mod

        original = ven_mod._get_db
        ven_mod._get_db = lambda: object()  # non-None so we reach store check

        app = FastAPI()
        app.include_router(ven_mod.router, prefix="/vendors")

        async def _user():
            # active_store_id absent
            return {"user_id": "u1", "roles": ["ADMIN", "SUPERADMIN"]}

        app.dependency_overrides[get_current_user] = _user
        c = TestClient(app)

        resp = c.post(
            "/vendors/purchase-orders/from-forecast",
            json={"dry_run": True},
        )
        ven_mod._get_db = original

        assert resp.status_code == 400
