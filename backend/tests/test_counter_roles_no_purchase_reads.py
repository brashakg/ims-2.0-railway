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
import re
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

    def search_vendors(self, _q, fields=None):
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


# ---------------------------------------------------------------------------
# 5. Vendor list SEARCH is not a GSTIN oracle for names-only callers
# ---------------------------------------------------------------------------
# Hiding the gstin key was not enough while ?search still matched the gstin
# field: whether Acme came back answered "does its GSTIN start with 27AAPFU?",
# and a per-character walk recovered the whole number (panel: 217 requests as
# CASHIER). The REAL VendorRepository builds the real prefix query here; only
# the store is swapped for a one-document list the query is run against.
def _mongo_match(doc, query):
    """The subset of Mongo the tokenised prefix search emits: $and of $or of
    {field: {$regex: ^token, $options: i}}. A non-string field never matches."""
    return all(
        any(
            isinstance(doc.get(f), str)
            and re.match(cond[f]["$regex"], doc[f], re.I) is not None
            for cond in clause["$or"]
            for f in cond
        )
        for clause in query["$and"]
    )


@pytest.fixture
def real_vendor_repo(monkeypatch):
    from database.repositories.vendor_repository import VendorRepository

    repo = VendorRepository(None)
    repo.find_many = lambda query, skip=0, limit=100, sort=None: [
        dict(_FULL_VENDOR) for _ in [0] if _mongo_match(_FULL_VENDOR, query)
    ]
    monkeypatch.setattr(vendors_mod, "get_vendor_repository", lambda: repo)


def _search(client, role, q):
    resp = client.get("/api/v1/vendors", params={"search": q}, headers=_headers(role))
    assert resp.status_code == 200
    return resp.json()["vendors"]


@pytest.mark.parametrize("role", COUNTER_ROLES + ("CATALOG_MANAGER",))
def test_names_only_search_is_not_a_gstin_oracle(client, real_vendor_repo, role):
    assert _search(client, role, "acme")  # a name still finds the vendor
    assert _search(client, role, "VEN-0001")  # so does the code it is shown
    for q in ("27", "27AAPFU", "27AAPFU0939F1ZV"):
        assert _search(client, role, q) == [], q


@pytest.mark.parametrize("role", _VENDOR_ROLES + ("SUPERADMIN",))
def test_purchase_roles_still_find_a_vendor_by_gstin(client, real_vendor_repo, role):
    assert _search(client, role, "27AAPFU")[0]["gstin"] == "27AAPFU0939F1ZV"


# ---------------------------------------------------------------------------
# 6. Sibling reads: vendor returns, RMAs, RTV debit notes
# ---------------------------------------------------------------------------
# Same data class as the PO reads (unit cost of a returned frame, expected
# vendor credit, vendor GSTIN + debit-note total). Readers = the roles that
# write them plus the Vendor Returns screen (/purchase/vendor-returns also lets
# WORKSHOP_STAFF in: it logs defective pairs). RMAs have no screen, so only
# their writers read them.
_RETURN_WRITERS = ("ADMIN", "AREA_MANAGER", "STORE_MANAGER", "ACCOUNTANT")
_RETURN_READERS = _RETURN_WRITERS + ("WORKSHOP_STAFF",)

SIBLING_READS = [
    ("/api/v1/vendor-returns", "/api/v1/vendor-returns", _RETURN_READERS),
    ("/api/v1/vendor-returns/", "/api/v1/vendor-returns/", _RETURN_READERS),
    (
        "/api/v1/vendor-returns/VR-1",
        "/api/v1/vendor-returns/{return_id}",
        _RETURN_READERS,
    ),
    ("/api/v1/rtv-debit-notes", "/api/v1/rtv-debit-notes", _RETURN_READERS),
    ("/api/v1/rtv-debit-notes/", "/api/v1/rtv-debit-notes/", _RETURN_READERS),
    (
        "/api/v1/rtv-debit-notes/DN-1",
        "/api/v1/rtv-debit-notes/{debit_note_id}",
        _RETURN_READERS,
    ),
    (
        "/api/v1/rtv-debit-notes/DN-1/print",
        "/api/v1/rtv-debit-notes/{debit_note_id}/print",
        _RETURN_READERS,
    ),
    ("/api/v1/vendor-rma", "/api/v1/vendor-rma", _RETURN_WRITERS),
    ("/api/v1/vendor-rma/", "/api/v1/vendor-rma/", _RETURN_WRITERS),
    ("/api/v1/vendor-rma/RMA-1", "/api/v1/vendor-rma/{rma_id}", _RETURN_WRITERS),
]


def _refused(allowed):
    return [r for r in COUNTER_ROLES + ("CATALOG_MANAGER",) if r not in allowed]


def _route_allows(app, template, role):
    """Every require_roles gate on the GET route lets `role` through."""
    for route in app.routes:
        if getattr(route, "path", None) == template and "GET" in getattr(
            route, "methods", ()
        ):
            try:
                for dep in route.dependant.dependencies:
                    if getattr(dep.call, "__name__", "") == "_dep":
                        asyncio.run(dep.call(current_user={"roles": [role]}))
            except HTTPException as exc:
                assert exc.status_code == 403
                return False
            return True
    raise AssertionError(f"no GET route {template}")


@pytest.mark.parametrize("concrete,template,allowed", SIBLING_READS)
def test_sibling_reads_refuse_roles_without_a_screen(
    client, concrete, template, allowed
):
    # The panel's probe: SALES_STAFF / CASHIER / OPTOMETRIST got 200 on all.
    assert {"SALES_STAFF", "CASHIER", "OPTOMETRIST"} <= set(_refused(allowed))
    for role in _refused(allowed):
        assert client.get(concrete, headers=_headers(role)).status_code == 403, role
    for role in allowed:
        assert client.get(concrete, headers=_headers(role)).status_code != 403, role


@pytest.mark.parametrize("concrete,template,allowed", SIBLING_READS)
def test_sibling_route_gate(app, concrete, template, allowed):
    for role in _refused(allowed):
        assert not _route_allows(app, template, role), role
    for role in allowed + ("SUPERADMIN",):
        assert _route_allows(app, template, role), role


@pytest.mark.parametrize("concrete,template,allowed", SIBLING_READS)
def test_sibling_policy_row(concrete, template, allowed):
    row = rbac.policy_for("GET", concrete)
    assert row is not None and row["path"] == template
    assert set(row["allowed"]) - {"SUPERADMIN"} == set(allowed)
    assert row.get("store_scoped") is True


def test_vendor_return_detail_is_store_scoped(monkeypatch):
    # The row says store_scoped; the detail read now checks the return's store
    # like its RMA / debit-note siblings (the list already did).
    from api.routers import vendor_returns as vr

    doc = {"return_id": "VR-1", "store_id": "BV-OTHER-01", "total_value": 4321.87}

    class _Db:
        def get_collection(self, _name):
            return self

        def find_one(self, _q):
            return dict(doc)

    monkeypatch.setattr(vr, "_get_db", lambda: _Db())
    mgr = {
        "roles": ["STORE_MANAGER"],
        "store_ids": ["BV-TEST-01"],
        "active_store_id": "BV-TEST-01",
    }
    with pytest.raises(HTTPException) as exc:
        asyncio.run(vr.get_vendor_return("VR-1", current_user=mgr))
    assert exc.value.status_code == 403
    own = dict(mgr, store_ids=["BV-OTHER-01"], active_store_id="BV-OTHER-01")
    assert asyncio.run(vr.get_vendor_return("VR-1", current_user=own))["return_id"] == "VR-1"
