"""F60: counter roles must not read purchase costs, vendor master or payables.

The purchase screens refuse SALES_STAFF / CASHIER (frontend PURCHASE_ROLES =
backend ``_VENDOR_ROLES``), but the API answered 200 to both for the vendor
master (GSTIN, contacts), every purchase order incl. unit cost prices, and the
vendor ledger (billed / paid / owed). The server now refuses those reads
exactly as the screens do:

  * PO list / PO detail / PO timeline / vendor detail / vendor performance +
    purchase history -> the purchase-screen role set ``_VENDOR_ROLES``.
  * Vendor ledger / bills / payments / debit notes -> ``_AP_ROLES``, the same
    gate as ``/ap-aging`` (the aggregate of the same payable data) and the only
    screen that reads it (Finance > Cash flow: ADMIN / ACCOUNTANT).
  * The vendor LIST stays open because the workshop job (WORKSHOP_STAFF), vendor
    returns and the buy desk (CATALOG_MANAGER) pick a vendor by name -- but any
    role outside ``_VENDOR_ROLES`` now gets names only (no GSTIN, contacts,
    bank or terms).

Each gate is asserted three ways so reverting any one layer turns a test red:
the HTTP answer, the route's own ``require_roles`` dependency, and the
``rbac_policy`` row.
"""

from __future__ import annotations

import asyncio
import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from api.routers import vendors as vendors_mod  # noqa: E402
from api.routers.vendors._shared import _AP_ROLES, _VENDOR_ROLES  # noqa: E402
from api.services import rbac_policy as rbac  # noqa: E402

COUNTER_ROLES = (
    "SALES_STAFF",
    "CASHIER",
    "SALES_CASHIER",
    "OPTOMETRIST",
    "WORKSHOP_STAFF",
)

# (concrete path, route template) -> gate
PURCHASE_READS = [
    ("/api/v1/vendors/v1", "/api/v1/vendors/{vendor_id}"),
    ("/api/v1/vendors/purchase-orders", "/api/v1/vendors/purchase-orders"),
    ("/api/v1/vendors/purchase-orders/po1", "/api/v1/vendors/purchase-orders/{po_id}"),
    (
        "/api/v1/vendors/purchase-orders/po1/timeline",
        "/api/v1/vendors/purchase-orders/{po_id}/timeline",
    ),
    ("/api/v1/vendors/v1/performance", "/api/v1/vendors/{vendor_id}/performance"),
    (
        "/api/v1/vendors/v1/purchase-history",
        "/api/v1/vendors/{vendor_id}/purchase-history",
    ),
]
AP_READS = [
    ("/api/v1/vendors/v1/ledger", "/api/v1/vendors/{vendor_id}/ledger"),
    ("/api/v1/vendors/v1/bills", "/api/v1/vendors/{vendor_id}/bills"),
    ("/api/v1/vendors/v1/payments", "/api/v1/vendors/{vendor_id}/payments"),
    ("/api/v1/vendors/v1/debit-notes", "/api/v1/vendors/{vendor_id}/debit-notes"),
]
GATED = [(c, t, _VENDOR_ROLES) for c, t in PURCHASE_READS] + [
    (c, t, _AP_ROLES) for c, t in AP_READS
]


def _headers(role):
    from api.routers.auth import create_access_token

    token = create_access_token(
        {
            "user_id": "t-1",
            "username": "t",
            "roles": [role],
            "store_ids": ["BV-TEST-01"],
            "active_store_id": "BV-TEST-01",
        }
    )
    return {"Authorization": f"Bearer {token}"}


def _route_gate(app, template):
    """The route's own require_roles dependency (``_dep``) for GET template."""
    for route in app.routes:
        if getattr(route, "path", None) == template and "GET" in getattr(
            route, "methods", ()
        ):
            for dep in route.dependant.dependencies:
                if getattr(dep.call, "__name__", "") == "_dep":
                    return dep.call
            return None
    raise AssertionError(f"no GET route {template}")


# ---------------------------------------------------------------------------
# 1. HTTP: the finding itself (counter roles got 200)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("role", COUNTER_ROLES)
@pytest.mark.parametrize("concrete,template,allowed", GATED)
def test_counter_role_refused(client, role, concrete, template, allowed):
    assert client.get(concrete, headers=_headers(role)).status_code == 403


@pytest.mark.parametrize("concrete,template,allowed", GATED)
def test_gate_roles_still_reach(client, concrete, template, allowed):
    for role in allowed:
        assert client.get(concrete, headers=_headers(role)).status_code != 403, role


@pytest.mark.parametrize("concrete,template", AP_READS)
def test_store_manager_refused_payables(client, concrete, template):
    # Same answer as /ap-aging: what the owner owes is an accounts read.
    assert client.get(concrete, headers=_headers("STORE_MANAGER")).status_code == 403


# ---------------------------------------------------------------------------
# 2. The route's own gate (independent of the RBAC middleware)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("concrete,template,allowed", GATED)
def test_route_dependency_gate(app, concrete, template, allowed):
    gate = _route_gate(app, template)
    assert gate is not None, f"{template} has no require_roles gate"
    with pytest.raises(HTTPException) as exc:
        asyncio.run(gate(current_user={"roles": ["SALES_STAFF"]}))
    assert exc.value.status_code == 403
    for role in allowed:
        assert asyncio.run(gate(current_user={"roles": [role]})) == {"roles": [role]}


# ---------------------------------------------------------------------------
# 3. The rbac_policy row says the same
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("concrete,template,allowed", GATED)
def test_policy_row_matches_gate(concrete, template, allowed):
    row = rbac.policy_for("GET", concrete)
    assert row is not None and row["path"] == template
    assert sorted(row["allowed"]) == sorted(allowed)


# ---------------------------------------------------------------------------
# 4. Vendor list: names only outside the purchase roles
# ---------------------------------------------------------------------------
_FULL_VENDOR = {
    "vendor_id": "V1",
    "vendor_code": "VEN-0001",
    "legal_name": "Acme Optics Pvt Ltd",
    "trade_name": "Acme",
    "is_active": True,
    "gstin": "27AAPFU0939F1ZV",
    "mobile": "9000000000",
    "email": "ap@acme.test",
    "address": "1 Main St",
    "bank_account_number": "001122334455",
    "payment_terms_days": 30,
    "credit_limit": 500000,
}
_NAME_KEYS = {"vendor_id", "vendor_code", "legal_name", "trade_name", "is_active"}


class _VendorRepo:
    def find_many(self, _flt, skip=0, limit=50):
        return [dict(_FULL_VENDOR)]

    def search_vendors(self, _q):
        return [dict(_FULL_VENDOR)]


@pytest.fixture
def vendor_repo(monkeypatch):
    monkeypatch.setattr(vendors_mod, "get_vendor_repository", lambda: _VendorRepo())


@pytest.mark.parametrize("role", COUNTER_ROLES + ("CATALOG_MANAGER",))
@pytest.mark.parametrize("params", [{}, {"search": "acme"}])
def test_vendor_list_names_only_outside_purchase_roles(
    client, vendor_repo, role, params
):
    resp = client.get("/api/v1/vendors", params=params, headers=_headers(role))
    assert resp.status_code == 200
    rows = resp.json()["vendors"]
    assert rows and all(set(r) <= _NAME_KEYS for r in rows), rows
    assert rows[0]["legal_name"] == "Acme Optics Pvt Ltd"


@pytest.mark.parametrize("role", _VENDOR_ROLES + ("SUPERADMIN",))
def test_vendor_list_full_for_purchase_roles(client, vendor_repo, role):
    resp = client.get("/api/v1/vendors/", headers=_headers(role))
    assert resp.status_code == 200
    assert resp.json()["vendors"][0]["gstin"] == "27AAPFU0939F1ZV"
