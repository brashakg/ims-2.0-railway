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
    bank or terms), and it searches only those keys (no GSTIN oracle).
  * Vendor returns / RTV debit notes -> writers + WORKSHOP_STAFF (the Vendor
    Returns screen); vendor RMAs -> writers only (no screen).
  * /finance/vendor-payments -> the same accounts set as the vendor ledger:
    one payables rule, and the Finance dashboard hides it from managers.

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
    # A GRN carries the supplier bill number / date and the bill-scan id; every
    # screen that reads GRNs is a purchase / accounts screen.
    ("/api/v1/vendors/grn", "/api/v1/vendors/grn"),
    ("/api/v1/vendors/grn/G1", "/api/v1/vendors/grn/{grn_id}"),
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
    # Equality, not a subset: no hidden key leaks AND no key the pickers need
    # goes missing (WorkshopJobDetail's VendorCaptureBlock, VendorReturns,
    # BuyDeskDraftPOModal and StockReplenishment all key the picker on
    # vendor_id; dropping it would empty them behind a green suite).
    assert rows and all(set(r) == _NAME_KEYS for r in rows), rows
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


# ---------------------------------------------------------------------------
# 7. ONE payables rule: every per-vendor payables read answers the same roles
# ---------------------------------------------------------------------------
# /finance/vendor-payments returns the same per-vendor paid / debit-note /
# balance / PO figures as the vendor ledger, for ALL vendors. The ledger went
# to accounts while this stayed on the finance router's manager set: one rule,
# two answers, and the ledger gate did nothing. The accounts set wins (it is
# already /ap-aging, the purchase-invoice reads and the Cash flow screen).
PAYABLE_READS = AP_READS + [
    ("/api/v1/vendors/ap-aging", "/api/v1/vendors/ap-aging"),
    ("/api/v1/finance/vendor-payments", "/api/v1/finance/vendor-payments"),
]


@pytest.mark.parametrize("concrete,template", PAYABLE_READS)
@pytest.mark.parametrize("role", ("STORE_MANAGER", "AREA_MANAGER"))
def test_payables_one_answer_managers_refused(client, role, concrete, template):
    assert client.get(concrete, headers=_headers(role)).status_code == 403


@pytest.mark.parametrize("concrete,template", PAYABLE_READS)
def test_payables_one_answer_policy_row(concrete, template):
    row = rbac.policy_for("GET", concrete)
    assert row["path"] == template
    assert set(row["allowed"]) - {"SUPERADMIN"} == set(_AP_ROLES)


@pytest.mark.parametrize("role", _AP_ROLES + ("SUPERADMIN",))
def test_vendor_payments_still_reach_accounts(client, role):
    resp = client.get("/api/v1/finance/vendor-payments", headers=_headers(role))
    assert resp.status_code == 200


@pytest.mark.parametrize("role", ("STORE_MANAGER", "AREA_MANAGER"))
def test_vendor_payments_handler_gate(role):
    # The handler's own gate, independent of the RBAC middleware row.
    from api.routers.finance import receivables

    with pytest.raises(HTTPException) as exc:
        asyncio.run(receivables.get_vendor_payments(current_user={"roles": [role]}))
    assert exc.value.status_code == 403


# ---------------------------------------------------------------------------
# 8. The human-readable matrix says the same as the rows this change moved
# ---------------------------------------------------------------------------
# rbac_policy's docstring: "if routes change ... update
# docs/reference/RBAC_MATRIX.md". Every GET row F60 changed must be in the
# doc, with the same roles (SUPERADMIN implied) and the same S column.
_DOC = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..",
    "..",
    "docs",
    "reference",
    "RBAC_MATRIX.md",
)
_DOC_ROW = re.compile(r"^\| `(\w+)` \| `([^`]+)` \| ([^|]*)\| ([^|]*)\|", re.M)
F60_GET_ROWS = sorted(
    {t for _c, t in PURCHASE_READS + PAYABLE_READS}
    | {t for _c, t, _a in SIBLING_READS}
    | {"/api/v1/vendors", "/api/v1/vendors/"}
)


def _doc_cell(row):
    allowed = row["allowed"]
    if allowed == "AUTHENTICATED":
        return "AUTH"
    return allowed if isinstance(allowed, str) else set(allowed) - {"SUPERADMIN"}


@pytest.mark.skipif(not os.path.exists(_DOC), reason="docs/ not shipped here")
def test_rbac_matrix_doc_matches_the_rows_f60_moved():
    with open(_DOC, encoding="utf-8") as fh:
        doc = {
            (m, p): (cell.strip(), s.strip())
            for m, p, cell, s in _DOC_ROW.findall(fh.read())
        }
    drift = []
    for path in F60_GET_ROWS:
        row = rbac.policy_for("GET", path)
        want = (_doc_cell(row), "S" if row.get("store_scoped") else "")
        got = doc.get(("GET", path))
        if got is not None and got[0] not in ("AUTH", "PUBLIC"):
            got = ({r.strip() for r in got[0].split(",")} - {"SUPERADMIN"}, got[1])
        if got != want:
            drift.append((path, got, want))
    assert not drift, drift


# ---------------------------------------------------------------------------
# 9. Vendor returns / RTV debit notes: WORKSHOP_STAFF sees item, qty, reason
# ---------------------------------------------------------------------------
# Owner ruling 2026-09-29. WORKSHOP_STAFF keeps the Vendor Returns screen, but
# the prices paid, totals and the supplier's GSTIN / address are hidden -- else
# the debit note (vendor GSTIN + address) and the return (unit cost) read
# around the names-only vendor list. One rule: services/cost_mask.
import copy  # noqa: E402
import json  # noqa: E402

from api.routers import rtv_debit_notes as dn_router  # noqa: E402
from api.routers import vendor_returns as vr_router  # noqa: E402
from api.services.rtv_debit_note import build_debit_note  # noqa: E402

_GSTIN = "27AAPFU0939F1ZV"
_ADDRESS = "1 Marker Street"
_BILL_NO = "ACME/INV/77"
_RETURN = {
    "return_id": "VR1",
    "vendor_id": "V1",
    "vendor_name": "Acme",
    "store_id": "BV-TEST-01",
    "items": [
        {
            "product_id": "P1",
            "product_name": "RB Frame",
            "quantity": 2,
            "reason": "defective",
            "unit_price": 3173.37,
        }
    ],
    "return_type": "credit_note",
    "status": "credit_issued",
    "total_value": 6346.74,
    "credit_note_number": "CN-1",
    "credit_note_amount": 6346.74,
    "purchase_invoice_number": _BILL_NO,
}
_NOTE = dict(
    build_debit_note(
        _RETURN,
        {"vendor_id": "V1", "name": "Acme", "gstin": _GSTIN, "address": _ADDRESS},
        [dict(_RETURN["items"][0], hsn="9003", gst_rate=5.0)],
        "DN/26-27/0001",
        seller={"name": "Better Vision", "gstin": "20AAACB1234C1Z5"},
    ),
    debit_note_id="DN-1",
    rtv_ref_id="VR1",
)
# Every price / total / supplier identity figure the fixtures carry, in every
# spelling a JSON body or the printed HTML would use.
_SECRETS = (
    _GSTIN,
    _ADDRESS,
    _BILL_NO,
    "3173.37",
    "3,173.37",
    "6346.74",
    "6,346.74",
    "317337",
    "634674",
    "unit_price",
    "total_value",
    "credit_note_amount",
    "rate_paise",
    "totals",
)
_PURCHASE_ROLES = ("ADMIN", "AREA_MANAGER", "STORE_MANAGER", "ACCOUNTANT", "SUPERADMIN")


class _Coll:
    def __init__(self, doc):
        self.doc = doc

    def count_documents(self, _flt):
        return 1

    def find(self, _flt=None):
        return self

    def sort(self, *_a):
        return self

    def skip(self, _n):
        return self

    def limit(self, _n):
        return [copy.deepcopy(self.doc)]

    def find_one(self, _q):
        return copy.deepcopy(self.doc)

    def get_collection(self, _name):
        return self


class _Engine:
    def list(self, **_kw):
        return [copy.deepcopy(_NOTE)]

    def get(self, _id):
        return copy.deepcopy(_NOTE)


@pytest.fixture
def return_docs(monkeypatch):
    monkeypatch.setattr(vr_router, "_get_db", lambda: _Coll(_RETURN))
    monkeypatch.setattr(dn_router, "_engine", lambda: _Engine())


RETURN_DOC_READS = (
    "/api/v1/vendor-returns",
    "/api/v1/vendor-returns/VR1",
    "/api/v1/rtv-debit-notes",
    "/api/v1/rtv-debit-notes/DN-1",
    "/api/v1/rtv-debit-notes/DN-1/print",
)


@pytest.mark.parametrize("path", RETURN_DOC_READS)
def test_workshop_reads_returns_without_prices_or_supplier(client, return_docs, path):
    resp = client.get(path, headers=_headers("WORKSHOP_STAFF"))
    assert resp.status_code == 200
    leaked = [s for s in _SECRETS if s in resp.text]
    assert not leaked, leaked
    # ...but still the item and its quantity (and the reason on a return).
    assert "RB Frame" in resp.text
    # A hidden figure prints as "-", never as a fake zero.
    assert ">0.00<" not in resp.text and ">0%<" not in resp.text
    if "vendor-returns" in path:
        body = resp.json()
        item = (body["returns"][0] if "returns" in body else body)["items"][0]
        assert item == {
            "product_id": "P1",
            "product_name": "RB Frame",
            "quantity": 2,
            "reason": "defective",
        }


@pytest.mark.parametrize("role", _PURCHASE_ROLES)
@pytest.mark.parametrize("path", RETURN_DOC_READS)
def test_purchase_roles_still_read_prices_and_supplier(client, return_docs, role, path):
    resp = client.get(path, headers=_headers(role))
    assert resp.status_code == 200
    want = ("3173.37",) if "vendor-returns" in path else (_GSTIN, _ADDRESS, _BILL_NO)
    for s in want:
        assert s in resp.text, (role, s)


# ---------------------------------------------------------------------------
# 10. Product reads: no cost_price / landed_cost for counter roles
# ---------------------------------------------------------------------------
# cost_price is the PO price written at receipt, landed_cost the purchase-bill
# landed unit cost: a cashier read every product's purchase cost through
# GET /products without touching /vendors (owner ruling 2026-09-29).
from api.routers import products as products_mod  # noqa: E402
from api.services import cache as cache_mod  # noqa: E402

_PRODUCT = {
    "product_id": "P1",
    "sku": "BV-FR-1",
    "name": "RB Frame",
    "mrp": 5000,
    "offer_price": 4500,
    "cost_price": 3173.37,
    "landed_cost": 3301.5,
    "landed_cost_paise": 330150,
    "moving_avg_cost": 3173.37,
}
_PRODUCT_COST_KEYS = {"cost_price", "landed_cost", "landed_cost_paise", "moving_avg_cost"}
# The product master feeds the PO form (buyers) and the product edit form.
_PRODUCT_COST_ROLES = (
    "ADMIN",
    "ACCOUNTANT",
    "AREA_MANAGER",
    "STORE_MANAGER",
    "CATALOG_MANAGER",
    "SUPERADMIN",
)


class _ProductRepo:
    def find_by_sku(self, _sku):
        return dict(_PRODUCT)

    def find_by_id(self, _pid):
        return dict(_PRODUCT)

    def find_many(self, _flt, skip=0, limit=50):
        return [dict(_PRODUCT)]

    def count(self, _flt):
        return 1


class _JsonCache:
    """The real cache stores JSON; so does this one, fresh per test."""

    TTL_MEDIUM = 300

    def __init__(self):
        self.d = {}

    def get(self, key):
        return json.loads(self.d[key]) if key in self.d else None

    def set(self, key, value, ttl=300):
        self.d[key] = json.dumps(value, default=str)


@pytest.fixture
def product_repo(monkeypatch):
    monkeypatch.setattr(products_mod, "get_product_repository", lambda: _ProductRepo())
    monkeypatch.setattr(cache_mod, "cache", _JsonCache())


def _product_rows(client, role, path):
    resp = client.get(path, headers=_headers(role))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    return body["products"] if "products" in body else [body]


PRODUCT_READS = (
    "/api/v1/products",
    "/api/v1/products/sku/BV-FR-1",
    "/api/v1/products/P1",
)


@pytest.mark.parametrize("path", PRODUCT_READS)
@pytest.mark.parametrize("role", COUNTER_ROLES)
def test_counter_roles_read_products_without_cost(client, product_repo, role, path):
    rows = _product_rows(client, role, path)
    assert rows and rows[0]["mrp"] == 5000
    assert not [k for r in rows for k in _PRODUCT_COST_KEYS if k in r], rows


@pytest.mark.parametrize("path", PRODUCT_READS)
@pytest.mark.parametrize("role", _PRODUCT_COST_ROLES)
def test_buyers_and_catalog_still_read_product_cost(client, product_repo, role, path):
    row = _product_rows(client, role, path)[0]
    assert row["cost_price"] == 3173.37 and row["landed_cost"] == 3301.5


# ACCOUNTANT and CASHIER share the attribution tier ("staff") the key already
# carried, so only the cost tier keeps their cached pages apart.
@pytest.mark.parametrize(
    "first,second", [("ACCOUNTANT", "CASHIER"), ("CASHIER", "ACCOUNTANT")]
)
def test_product_list_cache_never_crosses_the_cost_tier(
    client, product_repo, first, second
):
    _product_rows(client, first, "/api/v1/products")
    rows = _product_rows(client, second, "/api/v1/products")
    assert ("cost_price" in rows[0]) is (second == "ACCOUNTANT"), rows


# ---------------------------------------------------------------------------
# 11. GRN detail: the caller's stores only
# ---------------------------------------------------------------------------
# The list validated ?store_id; the detail read any store's GRN by id (supplier
# bill number included). Another store's GRN reads as 404, like its /document.
from api.routers.vendors import grn_create as grn_create_mod  # noqa: E402


def test_grn_detail_is_store_scoped(monkeypatch):
    grn = {
        "grn_id": "GRN2",
        "store_id": "BV-OTHER-02",
        "vendor_invoice_no": "OTHER/INV/77",
    }

    class _Repo:
        def find_by_id(self, _gid):
            return dict(grn)

    monkeypatch.setattr(grn_create_mod, "get_grn_repository", lambda: _Repo())
    mgr = {
        "roles": ["STORE_MANAGER"],
        "store_ids": ["BV-TEST-01"],
        "active_store_id": "BV-TEST-01",
    }
    with pytest.raises(HTTPException) as exc:
        asyncio.run(grn_create_mod.get_grn("GRN2", current_user=mgr))
    assert exc.value.status_code == 404
    own = dict(mgr, store_ids=["BV-OTHER-02"], active_store_id="BV-OTHER-02")
    got = asyncio.run(grn_create_mod.get_grn("GRN2", current_user=own))
    assert got["vendor_invoice_no"] == "OTHER/INV/77"


# ---------------------------------------------------------------------------
# 12. Every rbac row this change touches equals its code gate
# ---------------------------------------------------------------------------
# A row wider than the code told every role it could write a vendor return
# (capability 'vendor-returns:write' was AUTHENTICATED while the POST 403d); a
# row narrower than the code (bank statements) was held only by the middleware.
@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/api/v1/vendor-returns"),
        ("POST", "/api/v1/vendor-returns/"),
        ("PATCH", "/api/v1/vendor-returns/VR1/status"),
    ],
)
def test_vendor_return_write_rows_equal_the_code_gate(method, path):
    row = rbac.policy_for(method, path)
    assert set(row["allowed"]) - {"SUPERADMIN"} == set(vr_router._VENDOR_RETURN_ROLES)


def test_vendor_return_write_capability_is_the_writers():
    from api.services.capabilities import capability_roles

    assert set(capability_roles("vendor-returns:write")) == set(
        vr_router._VENDOR_RETURN_ROLES
    )


def _bank_calls(role):
    from api.routers.finance import bank_statement as bs

    user = {
        "roles": [role],
        "store_ids": ["BV-TEST-01"],
        "active_store_id": "BV-TEST-01",
    }
    return [
        lambda: bs.import_bank_statement(
            file=None, store_id=None, account_name=None, current_user=user
        ),
        lambda: bs.list_bank_statements(store_id=None, limit=20, current_user=user),
        lambda: bs.get_bank_statement("S1", current_user=user),
    ]


@pytest.mark.parametrize("role", ("STORE_MANAGER", "AREA_MANAGER"))
def test_bank_statement_handlers_refuse_managers(role):
    # The finance router admits managers; the row (and now the handler) do not.
    for call in _bank_calls(role):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(call())
        assert exc.value.status_code == 403


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/api/v1/finance/bank-statement/import"),
        ("GET", "/api/v1/finance/bank-statement"),
        ("GET", "/api/v1/finance/bank-statement/S1"),
    ],
)
def test_bank_statement_rows_are_the_handler_gate(method, path):
    from api.routers.finance import _require_finance_admin

    row = set(rbac.policy_for(method, path)["allowed"]) - {"SUPERADMIN"}
    assert row == {"ADMIN", "ACCOUNTANT"}
    for role in row:
        _require_finance_admin({"roles": [role]})  # no raise
