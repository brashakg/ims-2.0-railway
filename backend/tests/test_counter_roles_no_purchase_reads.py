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
    Returns screen), who reads them without prices, totals or the supplier's
    GSTIN / address (services/cost_mask, owner ruling 2026-09-29); vendor
    RMAs -> writers only (no screen).
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
from api.services.cost_mask import VENDOR_NAME_KEYS, mask_vendor  # noqa: E402
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
    # ...and it is cost_mask's vendor by name, not a list the route keeps.
    assert rows[0] == mask_vendor(dict(_FULL_VENDOR), {"roles": [role]})


def test_one_vendor_name_projection():
    """The vendor list and the debit note's vendor block are one projection
    (cost_mask.VENDOR_NAME_KEYS): the vendors router keeps no vendor-name key
    list of its own, and the list handler asks cost_mask for the vendor."""
    import ast
    import inspect

    from api.routers.vendors import master

    tree = ast.parse(inspect.getsource(master))
    own = [
        n.lineno
        for n in ast.walk(tree)
        if isinstance(n, (ast.Tuple, ast.List, ast.Set))
        and {"legal_name", "trade_name"}
        <= {c.value for c in n.elts if isinstance(c, ast.Constant)}
    ]
    assert not own, own
    handler = ast.parse(inspect.getsource(master.list_vendors))
    called = {
        getattr(n.func, "id", getattr(n.func, "attr", None))
        for n in ast.walk(handler)
        if isinstance(n, ast.Call)
    }
    assert "mask_vendor" in called
    assert _NAME_KEYS <= set(VENDOR_NAME_KEYS)


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


def _route_allows(app, template, role, method="GET"):
    """Every require_roles gate on the `method` route lets `role` through."""
    for route in app.routes:
        if getattr(route, "path", None) == template and method in getattr(
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
    raise AssertionError(f"no {method} route {template}")


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
    "notes": "Left hinge loose",
    "created_at": "2026-09-01T10:00:00",
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
    "CN-1",
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


# Equality, not a subset (as for the names-only vendor list): no hidden key
# leaks AND no key the Vendor Returns screen reads goes missing -- status (badge
# + Active / History tabs), vendor_name (card heading), return_id (expand +
# keys), rtv_ref_id (return -> note map, else "Issue Debit Note" 403s),
# debit_note_id (Print), debit_note_number (the note label).
_MASKED_RETURN_KEYS = {
    "return_id", "vendor_id", "vendor_name", "store_id", "return_type",
    "status", "notes", "created_at", "items",
}
_MASKED_NOTE_KEYS = {
    "debit_note_id", "debit_note_number", "financial_year", "issue_date",
    "entity_id", "store_id", "seller", "rtv_ref", "rtv_ref_id", "vendor", "lines",
}


@pytest.mark.parametrize("path", RETURN_DOC_READS[:4])
def test_workshop_masked_returns_keep_the_keys_the_screen_reads(
    client, return_docs, path
):
    body = client.get(path, headers=_headers("WORKSHOP_STAFF")).json()
    rows = body.get("returns") or body.get("debit_notes") or [body]
    doc = rows[0]
    if "vendor-returns" in path:
        assert set(doc) == _MASKED_RETURN_KEYS, sorted(doc)
        assert doc["status"] == "credit_issued" and doc["vendor_name"] == "Acme"
    else:
        assert set(doc) == _MASKED_NOTE_KEYS, sorted(doc)
        assert (doc["debit_note_id"], doc["rtv_ref_id"]) == ("DN-1", "VR1")
        assert doc["vendor"] == {"vendor_id": "V1", "name": "Acme"}
        assert {k for ln in doc["lines"] for k in ln} == {
            "sku", "description", "hsn", "qty",
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
import json  # noqa: E402

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
    # The legacy name the PO form still falls back to
    # (routers/vendors/purchase_orders.py), so a legacy product carries it.
    "purchase_price": 3088.88,
}
_PRODUCT_COST_KEYS = {
    "cost_price",
    "landed_cost",
    "landed_cost_paise",
    "moving_avg_cost",
    "purchase_price",
}
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
    assert row["purchase_price"] == 3088.88


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
# The route's own `_dep` is called for every role: comparing the row with a
# module constant stayed green when a route was switched onto the READERS
# tuple, and that gate is the last line once a per-user capability grant lets
# a request past the middleware.
@pytest.mark.parametrize(
    "method,path,template",
    [
        ("POST", "/api/v1/vendor-returns", "/api/v1/vendor-returns"),
        ("POST", "/api/v1/vendor-returns/", "/api/v1/vendor-returns/"),
        (
            "PATCH",
            "/api/v1/vendor-returns/VR1/status",
            "/api/v1/vendor-returns/{return_id}/status",
        ),
        ("POST", "/api/v1/rtv-debit-notes/issue", "/api/v1/rtv-debit-notes/issue"),
        (
            "GET",
            "/api/v1/rtv-debit-notes/DN-1/tally",
            "/api/v1/rtv-debit-notes/{debit_note_id}/tally",
        ),
    ],
)
def test_vendor_return_write_rows_equal_the_code_gate(app, method, path, template):
    row = rbac.policy_for(method, path)
    assert row["path"] == template
    assert set(row["allowed"]) - {"SUPERADMIN"} == set(_RETURN_WRITERS)
    for role in rbac.ALL_ROLES:
        want = role == "SUPERADMIN" or role in row["allowed"]
        assert _route_allows(app, template, role, method) is want, role


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


# ---------------------------------------------------------------------------
# 13. ONE purchase-role rule: the purchase gate and the purchase mask
# ---------------------------------------------------------------------------
# Who may open the purchase reads (vendor detail, POs, GRNs, and the return /
# debit-note / RMA writes) and who is shown what was paid and to whom (the
# full vendor list, prices on returns and debit notes) is one question. Two
# constants let them drift: narrowing the gate alone left the vendor LIST
# handing GSTIN and bank to a role the vendor DETAIL refused. So every gate IS
# cost_mask.PURCHASE_ROLES (identity, not a synced copy), and the mask answers
# exactly its members for every role.
from api.routers import vendor_rma as rma_router  # noqa: E402
from api.services.cost_mask import PURCHASE_ROLES, can_see_cost  # noqa: E402


def test_purchase_gates_are_the_one_purchase_role_constant():
    for name, gate in (
        ("vendors", _VENDOR_ROLES),
        ("vendor returns", vr_router._VENDOR_RETURN_ROLES),
        ("rtv debit notes", dn_router._DEBIT_NOTE_ROLES),
        ("vendor rma", rma_router._VENDOR_RMA_ROLES),
    ):
        assert gate is PURCHASE_ROLES, name


@pytest.mark.parametrize("role", rbac.ALL_ROLES)
def test_purchase_mask_is_membership_of_the_purchase_gate(app, role):
    sees = can_see_cost({"roles": [role]}, "purchase")
    assert sees is (role == "SUPERADMIN" or role in _VENDOR_ROLES), role
    # ...and the route itself agrees, so a handler switched onto another tuple
    # cannot reopen the gap either.
    assert _route_allows(app, "/api/v1/vendors/{vendor_id}", role) is sees, role


# ---------------------------------------------------------------------------
# 14. Product cost: ONE answer on every product route and in the frontend
# ---------------------------------------------------------------------------
# Owner ruling 2026-09-28: the managers (store, area, catalogue) see per-unit
# cost, counter staff never. /products said yes to store and area managers
# while /catalog/products said no (and yes to the catalogue manager only on
# the form) and the frontend CostCell said no to all three: one question,
# three answers. Every product read now asks cost_mask's "product" context,
# and CostCell's PRODUCT_COST_ROLES is that set.
from api.routers import catalog as catalog_mod  # noqa: E402

_CATALOG_DOC = {
    "id": "C1",
    "sku": "BV-FR-1",
    "title": "RB Frame",
    "is_active": True,
    "pricing": {"mrp": 5000, "offer_price": 4500, "cost_price": 3173.37},
}


@pytest.fixture
def catalog_docs(monkeypatch):
    monkeypatch.setattr(
        catalog_mod, "_all_catalog_products", lambda: [copy.deepcopy(_CATALOG_DOC)]
    )
    monkeypatch.setattr(
        catalog_mod, "_get_catalog_product", lambda _pid: copy.deepcopy(_CATALOG_DOC)
    )


def _product_cost_answers(client, role):
    """{product route: did the body carry the per-unit cost} for one role."""
    out = {p: "cost_price" in _product_rows(client, role, p)[0] for p in PRODUCT_READS}
    for path, key in (
        ("/api/v1/catalog/products", "products"),
        ("/api/v1/catalog/products/C1", "product"),
    ):
        resp = client.get(path, headers=_headers(role))
        assert resp.status_code == 200, (role, path, resp.text)
        doc = resp.json()[key]
        doc = doc[0] if isinstance(doc, list) else doc
        assert doc["pricing"]["mrp"] == 5000
        out[path] = "cost_price" in doc["pricing"]
    return out


def _product_cost_roles():
    return {r for r in rbac.ALL_ROLES if can_see_cost({"roles": [r]}, "product")}


@pytest.mark.parametrize("role", rbac.ALL_ROLES)
def test_product_cost_is_one_answer_on_every_product_route(
    client, product_repo, catalog_docs, role
):
    want = role in _product_cost_roles()
    answers = _product_cost_answers(client, role)
    assert answers == dict.fromkeys(answers, want), (role, answers)


def test_product_cost_follows_the_owner_ruling():
    roles = _product_cost_roles()
    assert {"STORE_MANAGER", "AREA_MANAGER", "CATALOG_MANAGER"} <= roles
    assert not roles & set(COUNTER_ROLES), roles


_COST_CELL = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..",
    "..",
    "frontend",
    "src",
    "components",
    "common",
    "CostCell.tsx",
)


@pytest.mark.skipif(not os.path.exists(_COST_CELL), reason="frontend/ not shipped here")
def test_frontend_cost_cell_is_the_product_context():
    with open(_COST_CELL, encoding="utf-8") as fh:
        m = re.search(r"PRODUCT_COST_ROLES[^=]*=\s*\[([^\]]*)\]", fh.read())
    assert m, "CostCell.tsx no longer declares PRODUCT_COST_ROLES"
    assert set(re.findall(r"'([A-Z_]+)'", m.group(1))) == _product_cost_roles()


_FRONTEND_SRC = os.path.dirname(os.path.dirname(os.path.dirname(_COST_CELL)))
# Every frontend check on supplier payments (Finance dashboard tab + schedule,
# booking a supplier bill, approving a match exception) reads the one list.
_PAYABLES_SCREENS = (
    "pages/finance/FinanceDashboard.tsx",
    "components/purchase/POLifecycleDrawer.tsx",
    "pages/purchase/invoices/shared.ts",
    "pages/purchase/ReconConsole.tsx",
)


@pytest.mark.skipif(not os.path.exists(_COST_CELL), reason="frontend/ not shipped here")
def test_frontend_payables_roles_are_the_payables_context():
    with open(_COST_CELL, encoding="utf-8") as fh:
        m = re.search(r"PAYABLES_ROLES[^=]*=\s*\[([^\]]*)\]", fh.read())
    assert m, "CostCell.tsx no longer declares PAYABLES_ROLES"
    want = {r for r in rbac.ALL_ROLES if can_see_cost({"roles": [r]}, "payables")}
    assert set(re.findall(r"'([A-Z_]+)'", m.group(1))) == want
    for rel in _PAYABLES_SCREENS:
        with open(os.path.join(_FRONTEND_SRC, rel), encoding="utf-8") as fh:
            # Code only: a comment naming the list is not a use of it.
            code = re.sub(r"/\*.*?\*/|//[^\n]*", "", fh.read(), flags=re.S)
        assert re.search(r"import\s*\{[^}]*\bPAYABLES_ROLES\b", code), rel
        assert len(re.findall(r"\bPAYABLES_ROLES\b", code)) >= 2, rel


# ---------------------------------------------------------------------------
# 15. Purchase recommendations: the same product-cost rule
# ---------------------------------------------------------------------------
# GET /reports/purchase/recommendations (open to every signed-in role) handed
# each product's cost_price, the margin on it and the buy's estimated cost to
# the counter (panel: 1111.11 / 4444.44 back to CASHIER, SALES_STAFF,
# OPTOMETRIST, WORKSHOP_STAFF) -- the figure /products masks. It now asks the
# same "product" context: velocity and quantities for everyone, cost for the
# managers.
from api.routers.reports import purchase as recs_mod  # noqa: E402

_SALES = {"_id": "P1", "units_sold": 6, "revenue": 27000.0, "avg_price": 4500.0,
          "sample_name": "RB Frame"}
_RECS_PRODUCT = {"_id": "P1", "product_id": "P1", "name": "RB Frame",
                 "offer_price": 4500, "cost_price": 1111.11, "stock_quantity": 0,
                 "reorder_point": 2, "reorder_quantity": 5}
_REC_COST_KEYS = {"cost_price", "unit_margin", "estimated_purchase_cost", "estimated_margin"}


class _RecsColl:
    def __init__(self, rows):
        self.rows = rows

    def aggregate(self, _pipeline):
        return copy.deepcopy(self.rows)

    def find(self, _flt):
        return copy.deepcopy(self.rows)


class _RecsDb:
    def get_collection(self, name):
        return _RecsColl({"orders": [_SALES], "products": [_RECS_PRODUCT]}[name])


@pytest.mark.parametrize("role", rbac.ALL_ROLES)
def test_purchase_recommendations_follow_the_product_cost_rule(
    client, monkeypatch, role
):
    monkeypatch.setattr(recs_mod, "get_db", lambda: _RecsDb())
    resp = client.get(
        "/api/v1/reports/purchase/recommendations",
        params={"store_id": "BV-TEST-01"},
        headers=_headers(role),
    )
    assert resp.status_code == 200, (role, resp.text)
    body = resp.json()
    rec = body["recommendations"][0]
    assert rec["suggested_order_qty"] == 4  # the buying signal stays for all
    if role in _product_cost_roles():
        assert rec["cost_price"] == 1111.11
        assert body["summary"]["estimated_purchase_cost"] == 4444.44
    else:
        assert not _REC_COST_KEYS & (set(rec) | set(body["summary"])), rec
        assert "1111.11" not in resp.text and "4444.44" not in resp.text


# ---------------------------------------------------------------------------
# 16. Cash flow: the supplier-payments TOTAL goes with the per-vendor payments
# ---------------------------------------------------------------------------
# Owner ruling 2026-09-29: supplier payments, per vendor and in total, are
# ADMIN + ACCOUNTANT only. /finance/vendor-payments went to accounts, but
# /finance/cash-flow still gave store and area managers vendor_payment_outflow
# (the total paid to vendors this period). The total now answers to the same
# gate as the per-vendor read, and is left out of `outflows` too -- else
# outflows - expense_outflow - purchase_outflow hands it straight back.
from api.routers.finance import cash_flow as cash_flow_mod  # noqa: E402
from api.routers.finance import receivables as receivables_mod  # noqa: E402

_PAID_TO_VENDORS = 7777.77


class _CashDb:
    def get_collection(self, name):
        rows = [{"_id": None, "total": _PAID_TO_VENDORS}]
        return _RecsColl(rows if name == "vendor_payments" else [])


def _vendor_payments_admits(monkeypatch, role):
    monkeypatch.setattr(receivables_mod, "_get_db", lambda: None)
    try:
        asyncio.run(receivables_mod.get_vendor_payments(current_user={"roles": [role]}))
    except HTTPException as exc:
        assert exc.status_code == 403
        return False
    return True


@pytest.mark.parametrize("role", rbac.ALL_ROLES)
def test_cash_flow_supplier_payments_total_answers_to_the_vendor_payments_gate(
    app, monkeypatch, role
):
    monkeypatch.setattr(cash_flow_mod, "_get_db", lambda: _CashDb())
    # No store on the token or the query: the org view, where AP is folded in.
    body = asyncio.run(
        cash_flow_mod.get_cash_flow(
            period="month", store_id=None, current_user={"roles": [role]}
        )
    )
    # One answer with the vendor ledger's own route gate, not a role literal.
    ledger = _route_allows(app, "/api/v1/vendors/{vendor_id}/ledger", role)
    assert _vendor_payments_admits(monkeypatch, role) is ledger, role
    if ledger:
        assert body["vendor_payment_outflow"] == _PAID_TO_VENDORS
        assert body["outflows"] == _PAID_TO_VENDORS
    else:
        assert "vendor_payment_outflow" not in body, body
        assert body["outflows"] == 0 and body["net_cash_flow"] == 0, body
        assert body["vendor_payments_restricted"] is True
        assert str(_PAID_TO_VENDORS) not in json.dumps(body)


# ---------------------------------------------------------------------------
# 17. ONE supplier-payments rule: every gate IS cost_mask.AP_ROLES
# ---------------------------------------------------------------------------
# Owner ruling 2026-09-29: supplier payments, per vendor AND in total, are
# ADMIN + ACCOUNTANT only. The vendor AP gates kept ("ADMIN", "ACCOUNTANT")
# while /finance/vendor-payments and the cash-flow total asked the finance
# router's own ("SUPERADMIN", "ADMIN", "ACCOUNTANT"): narrowing the vendor copy
# refused an accountant the ledger while cash-flow still handed over the total,
# and no test noticed. Every require_roles AP gate is now that one tuple and
# every in-handler check asks can_see_cost(user, "payables"), which reads the
# same accounts set at request time -- so narrowing it moves them all.
from api.routers import purchase_invoices as pinv_router  # noqa: E402
from api.routers import purchase_recon as recon_router  # noqa: E402
from api.routers import vendor_rebates as rebates_router  # noqa: E402
from api.services import cost_mask as cost_mask_mod  # noqa: E402
from api.services.cost_mask import AP_ROLES  # noqa: E402


def test_supplier_payment_gates_are_the_one_ap_constant():
    for name, gate in (
        ("vendors", _AP_ROLES),
        ("purchase invoices", pinv_router._AP_ROLES),
        ("purchase recon", recon_router._AP_ROLES),
    ):
        assert gate is AP_ROLES, name
    assert cost_mask_mod.COST_VISIBLE_ROLES == {"SUPERADMIN", *AP_ROLES}


def _supplier_payment_answers(monkeypatch, role):
    """{read: does `role` get supplier payments} for every request-time check."""
    monkeypatch.setattr(cash_flow_mod, "_get_db", lambda: _CashDb())
    body = asyncio.run(
        cash_flow_mod.get_cash_flow(
            period="month", store_id=None, current_user={"roles": [role]}
        )
    )
    try:
        rebates_router._require({"roles": [role]}, "read rebates")
        rebates = True
    except HTTPException as exc:
        assert exc.status_code == 403
        rebates = False
    return {
        "cash-flow total": "vendor_payment_outflow" in body,
        "vendor-payments": _vendor_payments_admits(monkeypatch, role),
        "vendor rebates": rebates,
    }


@pytest.mark.parametrize("role", rbac.ALL_ROLES)
def test_supplier_payments_are_one_answer_with_the_ledger(app, monkeypatch, role):
    want = _route_allows(app, "/api/v1/vendors/{vendor_id}/ledger", role)
    answers = _supplier_payment_answers(monkeypatch, role)
    assert answers == dict.fromkeys(answers, want), (role, answers)


def test_narrowing_the_accounts_set_moves_every_supplier_payment_read(monkeypatch):
    # The panel's breaking input: ACCOUNTANT taken out of the accounts set. A
    # read that kept its own role list would still answer the accountant.
    monkeypatch.setattr(cost_mask_mod, "COST_VISIBLE_ROLES", {"SUPERADMIN", "ADMIN"})
    answers = _supplier_payment_answers(monkeypatch, "ACCOUNTANT")
    assert not any(answers.values()), answers
    assert all(_supplier_payment_answers(monkeypatch, "ADMIN").values())
