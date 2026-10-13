"""
IMS 2.0 -- ONE BILL, ONE SHOP: the purchase-invoice routes that act on a single
bill obey the same shop scope as the list (audit F63, review round 3 problem 5)
================================================================================
Owner audit 2026-09-29, row F63, and the owner rulings of 2026-09-28: admins
open Purchase on ALL STORES with a shop filter; managers and every other role
keep their own shop -- "a Pune accountant would see Dhanbad's bills" is the
example the row names, and the scope must hold when the request is edited.

The list (GET /vendors/purchase-invoices) already filtered on the one Purchase
shop rule. Every route that acts on ONE bill by id did not:

  GET  /vendors/purchase-invoices/{id}                        full bill
  GET  /vendors/purchase-invoices/{id}/match                  3-way match
  GET  /vendors/purchase-invoices/{id}/dc-match               DC tally
  POST /vendors/purchase-invoices/{id}/approve-exception      override a hold
  POST /vendors/purchase-invoices/{id}/landed-costs           capture costs
  GET  /vendors/purchase-invoices/{id}/landed-costs/preview   cost dry-run
  POST /vendors/purchase-invoices/{id}/allocate-landed-costs  one-way costing

and GET /vendors/{vendor_id}/bills handed out every shop's bill ids to anyone.
The review's probe: a Pune ACCOUNTANT read Dhanbad's B1 in full and approved
Dhanbad's held bill (stored match_status became MATCHED_OVERRIDE, approved by
the Pune accountant).

The rule pinned here: api.dependencies.can_access_store_scoped on the bill's
store_id. Another shop's bill answers 404 with the SAME body as a bill that
does not exist (the GRN / PO convention), so its existence is not confirmed.
A bill with no store_id is an admin's only, as the list already has it.

The world (mongomock fallback when no local Mongo):

  Dhanbad (BV-DHN-01), supplier Jharkhand Optical (V-JHK)
    B1       booked, MATCHED, lines, landed costs captured
    B-HELD   ON_HOLD_EXCEPTION
  Pune (WO-PUN-01), supplier Pune Lens Co (V-PUN)
    B2       booked, MATCHED, lines, landed costs captured
    B-HELD-P ON_HOLD_EXCEPTION
  V-BOTH supplies both shops: B-BOTH-D (Dhanbad), B-BOTH-P (Pune)
  B-NOSHOP a legacy V-JHK bill with no store_id

Run: JWT_SECRET_KEY=test ENVIRONMENT=test python -m pytest
     backend/tests/test_purchase_invoice_shop_scope.py -q
No emoji (Windows cp1252).
"""

from __future__ import annotations

import copy
import os
import sys
import uuid

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MONGODB_URI", "")

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import purchase_invoices as pinv_mod  # noqa: E402
from api.routers import vendors as vendors_pkg  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402

DHN = "BV-DHN-01"
PUN = "WO-PUN-01"
ONLINE = "BV-ONLINE-01"
VA = "V-JHK"
VB = "V-PUN"
V_BOTH = "V-BOTH"

PI = "/vendors/purchase-invoices"


def _user(role: str, store: str | None, stores=()) -> dict:
    return {
        "user_id": f"u-{role.lower()}-{store or 'all'}",
        "username": role.lower(),
        "roles": [role],
        "store_ids": list(stores),
        "active_store_id": store,
    }


# A first-time admin lands on the ONLINE store (F63); he still reaches every shop.
ADMIN = _user("ADMIN", ONLINE)
SUPERADMIN = _user("SUPERADMIN", None)
ACCT_PUNE = _user("ACCOUNTANT", PUN, [PUN])
ACCT_DHN = _user("ACCOUNTANT", DHN, [DHN])


# ============================================================================
# Engine: the real Mongo CI runs against; mongomock only as a dev-box fallback
# ============================================================================


@pytest.fixture(scope="module")
def _client():
    """One client for the module (the 2s server probe runs once); every test
    then gets its own freshly seeded database on it."""
    from pymongo import MongoClient

    uri = (
        os.getenv("MONGODB_URL")
        or os.getenv("MONGODB_URI")
        or "mongodb://localhost:27017"
    )
    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=2000)
        client.server_info()
    except Exception:
        try:
            import mongomock
        except ImportError:
            pytest.skip("no Mongo and no mongomock available")
            return
        client = mongomock.MongoClient()
    yield client
    client.close()


def _fresh_db(client, seed):
    db_name = f"ims_test_pishop_{uuid.uuid4().hex[:8]}"
    db = client[db_name]
    seed(db)
    try:
        yield db
    finally:
        try:
            client.drop_database(db_name)
        except Exception:
            pass


class _DBProxy:
    def __init__(self, db):
        self._db = db
        self.is_connected = True

    def get_collection(self, name):
        return self._db[name]

    def __getitem__(self, name):
        return self._db[name]

    def __getattr__(self, name):
        return self._db[name]


_LINES = [
    {"product_id": "P-A", "qty": 2, "unit_price": 100.0, "taxable": 200.0, "gst_rate": 12.0},
    {"product_id": "P-B", "qty": 1, "unit_price": 300.0, "taxable": 300.0, "gst_rate": 12.0},
]
_COMPONENTS = [{"type": "FREIGHT", "label": "courier", "amount_paise": 5000}]
_HOLD_DETAIL = {
    "match_status": "ON_HOLD_EXCEPTION",
    "exceptions": [{"product_id": "P-A", "reason": "billed qty 3 > accepted 2"}],
}


def _bill(bill_id, vendor, store, **extra) -> dict:
    doc = {
        "bill_id": bill_id,
        "invoice_id": bill_id,
        "doc_type": "PURCHASE_INVOICE",
        "vendor_id": vendor,
        "bill_number": f"INV-{bill_id}",
        "invoice_number": f"INV-{bill_id}",
        "bill_date": "2026-09-06",
        "invoice_date": "2026-09-06",
        "due_date": "2026-10-06",
        "total_amount": 560.0,
        "total": 560.0,
        "status": "OUTSTANDING",
        "lines": copy.deepcopy(_LINES),
        "match_status": "MATCHED",
        "match_detail": {"match_status": "MATCHED", "exceptions": []},
        "dc_match_status": "N_A",
        "landed_cost_components": copy.deepcopy(_COMPONENTS),
        "landed_cost_total_paise": 5000,
        "allocation_method": "BY_VALUE",
        "landed_cost_allocated": False,
        **extra,
    }
    if store is not None:
        doc["store_id"] = store
    return doc


def _held(bill_id, vendor, store) -> dict:
    return _bill(
        bill_id,
        vendor,
        store,
        match_status="ON_HOLD_EXCEPTION",
        match_detail=copy.deepcopy(_HOLD_DETAIL),
    )


def _seed(db) -> None:
    for v_id, name in ((VA, "Jharkhand Optical"), (VB, "Pune Lens Co"), (V_BOTH, "Two Shop Optics")):
        db["vendors"].insert_one({"vendor_id": v_id, "legal_name": name, "trade_name": name})
    for doc in (
        _bill("B1", VA, DHN),
        _held("B-HELD", VA, DHN),
        _bill("B2", VB, PUN),
        _held("B-HELD-P", VB, PUN),
        _bill("B-BOTH-D", V_BOTH, DHN),
        _bill("B-BOTH-P", V_BOTH, PUN),
        _bill("B-NOSHOP", VA, None),
    ):
        db["vendor_bills"].insert_one(dict(doc))


# ============================================================================
# The app: the REAL purchase-invoice + vendors routers
# ============================================================================


class _World:
    def __init__(self, client: TestClient, app: FastAPI, db, audit: list):
        self._client = client
        self._app = app
        self.db = db
        self.audit = audit

    def _as(self, user: dict):
        async def _as_user():
            return dict(user)

        self._app.dependency_overrides[get_current_user] = _as_user

    def get(self, path: str, user: dict, **params):
        self._as(user)
        params = {k: v for k, v in params.items() if v is not None}
        return self._client.get(path, params=params)

    def post(self, path: str, user: dict, body: dict):
        self._as(user)
        return self._client.post(path, json=body)

    def bill(self, bill_id: str) -> dict:
        return self.db["vendor_bills"].find_one({"bill_id": bill_id}, {"_id": 0})


class _Audit:
    def __init__(self, rows: list):
        self._rows = rows

    def create(self, row):
        self._rows.append(row)
        return row


@pytest.fixture
def world(_client, monkeypatch):
    # Function-scoped: the write probes (approve / capture / allocate) must
    # each start from the seeded bills.
    for db in _fresh_db(_client, _seed):
        proxy = _DBProxy(db)
        audit: list = []
        for mod in (vendors_pkg, pinv_mod):
            monkeypatch.setattr(mod, "_get_db", lambda: proxy)
        monkeypatch.setattr(pinv_mod, "get_audit_repository", lambda: _Audit(audit))

        app = FastAPI()
        # Same order as main.py: the concrete /purchase-invoices paths before
        # the vendors router, whose catch-all GET /{vendor_id} would swallow them.
        app.include_router(pinv_mod.router, prefix=PI)
        app.include_router(vendors_pkg.router, prefix="/vendors")
        yield _World(TestClient(app), app, db, audit)


def _not_found_body(world, user, path_tail: str = "", method: str = "GET", body=None):
    """What the same route answers for a bill that does not exist."""
    path = f"{PI}/NO-SUCH-BILL{path_tail}"
    resp = world.get(path, user) if method == "GET" else world.post(path, user, body or {})
    assert resp.status_code == 404, resp.text
    return resp.json()


# ============================================================================
# Reads: another shop's bill is not there
# ============================================================================

_READS = ("", "/match", "/dc-match", "/landed-costs/preview")


@pytest.mark.parametrize("tail", _READS)
def test_pune_accountant_cannot_read_a_dhanbad_bill_by_id(world, tail):
    resp = world.get(f"{PI}/B1{tail}", ACCT_PUNE)
    assert resp.status_code == 404, (
        f"F63: a Pune accountant read Dhanbad's bill B1 at {tail or '/'} "
        f"({resp.status_code}): {resp.text[:200]}"
    )
    # The same answer as a bill that does not exist: nothing confirms B1 is real.
    assert resp.json() == _not_found_body(world, ACCT_PUNE, tail)
    assert "INV-B1" not in resp.text


@pytest.mark.parametrize("tail", _READS)
def test_pune_accountant_still_reads_a_pune_bill(world, tail):
    resp = world.get(f"{PI}/B2{tail}", ACCT_PUNE)
    assert resp.status_code == 200, resp.text


@pytest.mark.parametrize("tail", _READS)
@pytest.mark.parametrize("user", [ADMIN, SUPERADMIN], ids=["admin", "superadmin"])
def test_admin_reads_every_shops_bill(world, tail, user):
    for bill_id in ("B1", "B2"):
        resp = world.get(f"{PI}/{bill_id}{tail}", user)
        assert resp.status_code == 200, (bill_id, tail, resp.text)


def test_the_full_bill_reaches_its_own_shop_and_the_admin(world):
    own = world.get(f"{PI}/B2", ACCT_PUNE).json()
    assert own["bill_id"] == "B2" and own["store_id"] == PUN
    for bill_id, shop in (("B1", DHN), ("B2", PUN)):
        doc = world.get(f"{PI}/{bill_id}", ADMIN).json()
        assert doc["bill_id"] == bill_id and doc["store_id"] == shop


def test_a_dhanbad_accountant_is_held_to_dhanbad_too(world):
    """Not a Pune special case: the rule is the caller's own shop."""
    assert world.get(f"{PI}/B1", ACCT_DHN).status_code == 200
    assert world.get(f"{PI}/B2", ACCT_DHN).status_code == 404


def test_a_bill_with_no_shop_is_an_admins_only(world):
    """The list hides an unattributed bill from every shop-bound role; opening
    it by id must not be the way round that."""
    assert world.get(f"{PI}/B-NOSHOP", ACCT_PUNE).status_code == 404
    assert world.get(f"{PI}/B-NOSHOP", ACCT_DHN).status_code == 404
    assert world.get(f"{PI}/B-NOSHOP", ADMIN).status_code == 200
    listed = world.get(PI, ACCT_PUNE).json()["purchase_invoices"]
    assert "B-NOSHOP" not in {b["bill_id"] for b in listed}


# ============================================================================
# Writes: another shop's bill cannot be approved or costed
# ============================================================================


def test_pune_accountant_cannot_approve_dhanbad_held_bill(world):
    before = world.bill("B-HELD")
    resp = world.post(f"{PI}/B-HELD/approve-exception", ACCT_PUNE, {"reason": "looks fine"})
    assert resp.status_code == 404, (
        f"F63: a Pune accountant approved Dhanbad's held bill ({resp.status_code}): "
        f"{resp.text[:200]}"
    )
    assert resp.json() == _not_found_body(
        world, ACCT_PUNE, "/approve-exception", "POST", {"reason": "looks fine"}
    )
    after = world.bill("B-HELD")
    assert after == before, "the refused approval changed the stored bill"
    assert after["match_status"] == "ON_HOLD_EXCEPTION"
    assert "exception_override" not in after
    assert world.audit == [], "a refused approval wrote an audit row"


def test_the_refusal_does_not_say_whether_the_bill_is_on_hold(world):
    """A Dhanbad bill that is NOT held answers 404 too, not the 400 'only an
    ON_HOLD_EXCEPTION can be approved' that would confirm it exists."""
    resp = world.post(f"{PI}/B1/approve-exception", ACCT_PUNE, {"reason": "x"})
    assert resp.status_code == 404, resp.text


def test_pune_accountant_still_approves_a_pune_held_bill(world):
    resp = world.post(f"{PI}/B-HELD-P/approve-exception", ACCT_PUNE, {"reason": "short-shipped, agreed"})
    assert resp.status_code == 200, resp.text
    stored = world.bill("B-HELD-P")
    assert stored["match_status"] == "MATCHED_OVERRIDE"
    assert stored["exception_override"]["approved_by"] == ACCT_PUNE["user_id"]


def test_admin_approves_a_held_bill_in_any_shop(world):
    resp = world.post(f"{PI}/B-HELD/approve-exception", ADMIN, {"reason": "agreed with vendor"})
    assert resp.status_code == 200, resp.text
    assert world.bill("B-HELD")["match_status"] == "MATCHED_OVERRIDE"


def test_pune_accountant_cannot_cost_a_dhanbad_bill(world):
    before = world.bill("B1")
    capture = {"components": [{"type": "DUTY", "amount_paise": 99900}], "allocation_method": "BY_QTY"}
    resp = world.post(f"{PI}/B1/landed-costs", ACCT_PUNE, capture)
    assert resp.status_code == 404, resp.text
    resp = world.post(f"{PI}/B1/allocate-landed-costs", ACCT_PUNE, {})
    assert resp.status_code == 404, resp.text
    assert world.bill("B1") == before, "a refused landed-cost write changed the bill"
    assert world.audit == []


def test_pune_accountant_still_costs_a_pune_bill(world):
    resp = world.post(f"{PI}/B2/allocate-landed-costs", ACCT_PUNE, {})
    assert resp.status_code == 200, resp.text
    assert world.bill("B2")["landed_cost_allocated"] is True


# ============================================================================
# The supplier's bill list hands out no other shop's bill ids
# ============================================================================


def _bill_ids(resp) -> set:
    assert resp.status_code == 200, resp.text
    return {b["bill_id"] for b in resp.json()["bills"]}


def test_pune_accountant_lists_no_dhanbad_bills_of_a_dhanbad_supplier(world):
    ids = _bill_ids(world.get(f"/vendors/{VA}/bills", ACCT_PUNE))
    assert not ids & {"B1", "B-HELD", "B-NOSHOP"}, (
        f"F63: GET /vendors/{VA}/bills gave a Pune accountant Dhanbad's bills {sorted(ids)}"
    )


def test_a_two_shop_supplier_lists_only_the_callers_shop(world):
    assert _bill_ids(world.get(f"/vendors/{V_BOTH}/bills", ACCT_PUNE)) == {"B-BOTH-P"}
    assert _bill_ids(world.get(f"/vendors/{V_BOTH}/bills", ACCT_DHN)) == {"B-BOTH-D"}


def test_asking_for_another_shop_is_refused_not_widened(world):
    resp = world.get(f"/vendors/{V_BOTH}/bills", ACCT_PUNE, store_id=DHN)
    assert resp.status_code == 403, resp.text


def test_admin_lists_every_shop_and_can_narrow_to_one(world):
    assert _bill_ids(world.get(f"/vendors/{VA}/bills", ADMIN)) == {"B1", "B-HELD", "B-NOSHOP"}
    assert _bill_ids(world.get(f"/vendors/{V_BOTH}/bills", ADMIN)) == {"B-BOTH-D", "B-BOTH-P"}
    assert _bill_ids(world.get(f"/vendors/{V_BOTH}/bills", ADMIN, store_id=PUN)) == {"B-BOTH-P"}


def test_the_bill_list_and_the_invoice_list_agree_for_a_shop(world):
    """One scope rule: what the supplier's list shows a Pune login is exactly
    what the purchase-invoice list shows it for that supplier."""
    for vendor in (VA, VB, V_BOTH):
        bills = _bill_ids(world.get(f"/vendors/{vendor}/bills", ACCT_PUNE))
        resp = world.get(PI, ACCT_PUNE, vendor_id=vendor)
        assert resp.status_code == 200, resp.text
        invoices = {b["bill_id"] for b in resp.json()["purchase_invoices"]}
        assert bills == invoices, (vendor, bills, invoices)
