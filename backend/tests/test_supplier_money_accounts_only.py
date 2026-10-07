"""
IMS 2.0 -- SUPPLIER MONEY IS FOR THE ACCOUNTS ROLES, AND EACH SHOP SEES ITS OWN
===============================================================================
Owner ruling 2026-10-01 (verbatim): "supplier balances should not be shown to
anyone apart from admin superadmin and accountant". A supplier balance is what
we owe a supplier, have paid it, or are owed by it: ledger rows and the closing
balance, bills with their outstanding / paid status, payments (TDS included),
and debit / credit notes.

Shop rule (F63, owner rulings 2026-09-28): ADMIN / SUPERADMIN see every shop.
Every other role sees its own shop only, even when the request is edited.

Pinned here on the REAL vendors / purchase-recon / finance routers:

  1. GET /vendors/{id}/ledger, /payments, /debit-notes and /bills answer 403
     to every role except ACCOUNTANT / ADMIN / SUPERADMIN. Before this, any
     logged-in user could read them. Their rbac_policy rows say the same.
  2. Those four reads give a shop-bound accountant only its OWN shop's share,
     by the one row rule (ap_engine.supplier_ledger_rows). That share is the
     same figure /finance/vendor-payments?store_id= and the Purchases report
     give for that shop. Asking for another shop answers 403. AP aging
     (/vendors/ap-aging), the list the ledger is opened from, follows suit.
  3. GET / POST /vendors/purchase-invoices/{id}/recon on another shop's bill
     answer the same 404 as a bill that does not exist. Nothing is ticked.
  4. POST /vendors/{id}/payments and /debit-notes that name another shop's
     bill answer that same 404, and nothing is written.
  5. The accounts-role tuple is defined once (services/cost_mask.AP_ROLES).
     No router here spells it out locally.

The world below is written out by hand. Every date is before 1 Oct 2026.

  Two Shop Optics (V-BOTH) supplies Dhanbad (BV-DHN-01) and Pune (WO-PUN-01)
    B-D1    Dhanbad bill  2026-09-01  1000
    B-P1    Pune bill     2026-09-05   600
    B-P2    Pune bill     2026-09-20   400
    PAY-AD  on account    2026-09-03    50  latest bill by then: B-D1 -> Dhanbad
    PAY-D1  on B-D1       2026-09-10   300
    PAY-P1  on B-P1       2026-09-12   180 cash + 20 TDS
    PAY-AP  on account    2026-09-21   100  latest bill by then: B-P2 -> Pune
    DN-D1   on B-D1       2026-09-11   100
    DN-P2   on B-P2       2026-09-22    50
    CN-REB  rebate credit note, no bill, 2026-09-25, 70  -> Pune
  Pune Lens Co (V-PUN) supplies Pune only: B-PX 2026-09-08, 250.
  Old Frames Co (V-OLD): B-NOSHOP 2026-09-02, 300, carries no shop.

  V-BOTH, Pune's share:    billed 1000, paid 300 (TDS 20), notes 120 -> owes 580
  V-BOTH, Dhanbad's share: billed 1000, paid 350,          notes 100 -> owes 550
  V-BOTH, every shop:      billed 2000, paid 650,          notes 220 -> owes 1130

Run: JWT_SECRET_KEY=test ENVIRONMENT=test python -m pytest
     backend/tests/test_supplier_money_accounts_only.py -q
No emoji (Windows cp1252).
"""

from __future__ import annotations

import ast
import os
import sys
import uuid

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MONGODB_URI", "")

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BACKEND)

from api.routers import finance as finance_pkg  # noqa: E402
from api.routers import purchase_recon as recon_mod  # noqa: E402
from api.routers import vendors as vendors_pkg  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402

DHN = "BV-DHN-01"
PUN = "WO-PUN-01"
ONLINE = "BV-ONLINE-01"
V_BOTH = "V-BOTH"
V_PUN = "V-PUN"
V_OLD = "V-OLD"

RECON = "/vendors/purchase-invoices/{}/recon"
REPORT = "/vendors/purchases-this-month"

# The four supplier-money reads on one supplier.
_READS = ("ledger", "payments", "debit-notes", "bills")
# Every role the ruling leaves out (owner: "anyone apart from admin
# superadmin and accountant").
_OUTSIDERS = (
    "STORE_MANAGER",
    "AREA_MANAGER",
    "SALES_STAFF",
    "CASHIER",
    "WORKSHOP_STAFF",
    "CATALOG_MANAGER",
    "OPTOMETRIST",
    "SALES_CASHIER",
    "DESIGN_MANAGER",
    "INVESTOR",
)


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


def _outsider(role: str) -> dict:
    # An area manager reaches both shops; everyone else is Pune's.
    if role == "AREA_MANAGER":
        return _user(role, PUN, [PUN, DHN])
    return _user(role, PUN, [PUN])


# ============================================================================
# Engine: the real Mongo CI runs against; mongomock only as a dev-box fallback
# ============================================================================


@pytest.fixture(scope="module")
def _client():
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


def _bill(bill_id, vendor, store, on, total, **extra) -> dict:
    doc = {
        "bill_id": bill_id,
        "invoice_id": bill_id,
        "doc_type": "PURCHASE_INVOICE",
        "vendor_id": vendor,
        "bill_number": bill_id,
        "invoice_number": bill_id,
        "bill_date": on,
        "invoice_date": on,
        "due_date": "2026-10-30",
        "total_amount": total,
        "total": total,
        "outstanding": total,
        "status": "OUTSTANDING",
        **extra,
    }
    if store is not None:
        doc["store_id"] = store
    return doc


def _pay(pid, vendor, bill_id, amount, on, tds=0.0) -> dict:
    return {
        "payment_id": pid,
        "vendor_id": vendor,
        "bill_id": bill_id,
        "amount": amount,
        "tds_amount": tds,
        "mode": "BANK",
        "payment_date": on,
    }


def _note(dn_id, vendor, bill_id, amount, on, **extra) -> dict:
    return {
        "debit_note_id": dn_id,
        "debit_note_number": dn_id,
        "vendor_id": vendor,
        "bill_id": bill_id,
        "amount": amount,
        "date": on,
        "reason": "returned goods",
        **extra,
    }


# Dhanbad's bill already carries a recon tick by Dhanbad's accountant.
_DHN_RECON = {
    "reconciled": True,
    "reconciled_by": "acc-dhanbad",
    "reconciled_at": "2026-09-15T10:00:00",
    "entered_tally": False,
}


def _seed(db) -> None:
    for vid, name in (
        (V_BOTH, "Two Shop Optics"),
        (V_PUN, "Pune Lens Co"),
        (V_OLD, "Old Frames Co"),
    ):
        db["vendors"].insert_one(
            {"vendor_id": vid, "legal_name": name, "trade_name": name, "is_active": True}
        )
    for doc in (
        _bill("B-D1", V_BOTH, DHN, "2026-09-01", 1000.0, recon=dict(_DHN_RECON)),
        _bill("B-P1", V_BOTH, PUN, "2026-09-05", 600.0),
        _bill("B-P2", V_BOTH, PUN, "2026-09-20", 400.0),
        _bill("B-PX", V_PUN, PUN, "2026-09-08", 250.0),
        _bill("B-NOSHOP", V_OLD, None, "2026-09-02", 300.0),
    ):
        db["vendor_bills"].insert_one(doc)
    for doc in (
        _pay("PAY-AD", V_BOTH, None, 50.0, "2026-09-03"),
        _pay("PAY-D1", V_BOTH, "B-D1", 300.0, "2026-09-10"),
        _pay("PAY-P1", V_BOTH, "B-P1", 180.0, "2026-09-12", tds=20.0),
        _pay("PAY-AP", V_BOTH, None, 100.0, "2026-09-21"),
    ):
        db["vendor_payments"].insert_one(doc)
    for doc in (
        _note("DN-D1", V_BOTH, "B-D1", 100.0, "2026-09-11"),
        _note("DN-P2", V_BOTH, "B-P2", 50.0, "2026-09-22"),
        # rebate_engine's machine-posted credit note: no bill, source tag only.
        _note("CN-REB", V_BOTH, None, 70.0, "2026-09-25", source="VOLUME_REBATE"),
    ):
        db["vendor_debit_notes"].insert_one(doc)


# ============================================================================
# The app: the REAL vendors + recon + finance routers, in main.py's order
# ============================================================================


class _World:
    def __init__(self, client: TestClient, app: FastAPI, db):
        self._client = client
        self._app = app
        self.db = db

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

    def rows(self, coll: str) -> list:
        return sorted(
            (d for d in self.db[coll].find({}, {"_id": 0})),
            key=lambda d: str(sorted(d.items())),
        )


@pytest.fixture
def world(_client, monkeypatch):
    # Function-scoped: the write probes must each start from the seeded world.
    from database.repositories.vendor_repository import (
        GRNRepository,
        PurchaseOrderRepository,
        VendorRepository,
    )

    db_name = f"ims_test_supmoney_{uuid.uuid4().hex[:8]}"
    db = _client[db_name]
    _seed(db)
    proxy = _DBProxy(db)
    for mod in (vendors_pkg, recon_mod, finance_pkg):
        monkeypatch.setattr(mod, "_get_db", lambda: proxy)
    monkeypatch.setattr(vendors_pkg, "get_vendor_repository", lambda: VendorRepository(db["vendors"]))
    monkeypatch.setattr(
        vendors_pkg,
        "get_purchase_order_repository",
        lambda: PurchaseOrderRepository(db["purchase_orders"]),
    )
    monkeypatch.setattr(vendors_pkg, "get_grn_repository", lambda: GRNRepository(db["grns"]))

    app = FastAPI()
    # Same order as main.py: the concrete recon paths before the vendors
    # router, whose catch-all GET /{vendor_id} would swallow them.
    app.include_router(recon_mod.router, prefix="/vendors")
    app.include_router(vendors_pkg.router, prefix="/vendors")
    app.include_router(finance_pkg.router, prefix="/finance")
    try:
        yield _World(TestClient(app), app, db)
    finally:
        try:
            _client.drop_database(db_name)
        except Exception:
            pass


def _ok(resp):
    assert resp.status_code == 200, resp.text
    return resp.json()


def _ids(resp, key: str, id_key: str) -> set:
    return {r[id_key] for r in _ok(resp)[key]}


# ============================================================================
# 1. Only the accounts roles read a supplier's money
# ============================================================================


@pytest.mark.parametrize("tail", _READS)
@pytest.mark.parametrize("role", _OUTSIDERS)
def test_supplier_money_reads_refuse_every_other_role(world, tail, role):
    resp = world.get(f"/vendors/{V_BOTH}/{tail}", _outsider(role))
    assert resp.status_code == 403, (
        f"owner ruling 2026-10-01: a {role} read /vendors/{{id}}/{tail} "
        f"({resp.status_code}): {resp.text[:200]}"
    )
    for leaked in ("B-P1", "PAY-P1", "DN-P2", "closing_balance", "600"):
        assert leaked not in resp.text, (role, tail, leaked)


@pytest.mark.parametrize("tail", _READS)
@pytest.mark.parametrize(
    "user", [ACCT_PUNE, ADMIN, SUPERADMIN], ids=["accountant", "admin", "superadmin"]
)
def test_supplier_money_reads_answer_the_accounts_roles(world, tail, user):
    assert world.get(f"/vendors/{V_BOTH}/{tail}", user).status_code == 200


@pytest.mark.parametrize("tail", _READS)
def test_the_policy_rows_are_the_accounts_roles(tail):
    """The middleware row is the first gate: it refuses the same roles the
    handler refuses, so the two answers never disagree."""
    from api.services import rbac_policy
    from api.services.rbac_policy._core import ACCOUNTS

    path = f"/api/v1/vendors/{V_BOTH}/{tail}"
    assert rbac_policy.policy_for("GET", path)["allowed"] == ACCOUNTS, tail
    for role in _OUTSIDERS:
        assert not rbac_policy.check_access("GET", path, [role]), (tail, role)
    for role in ("ACCOUNTANT", "ADMIN", "SUPERADMIN"):
        assert rbac_policy.check_access("GET", path, [role]), (tail, role)


# ============================================================================
# 2. A shop-bound accountant reads its own shop's share, and only that
# ============================================================================

_PUNE_SHARE = dict(
    closing_balance=580.0,
    total_billed=1000.0,
    total_paid=300.0,
    total_tds=20.0,
    total_debit_notes=120.0,
)
_DHN_SHARE = dict(
    closing_balance=550.0,
    total_billed=1000.0,
    total_paid=350.0,
    total_tds=0.0,
    total_debit_notes=100.0,
)
_ALL_SHOPS = dict(
    closing_balance=1130.0,
    total_billed=2000.0,
    total_paid=650.0,
    total_tds=20.0,
    total_debit_notes=220.0,
)


def _ledger(world, user, **params) -> dict:
    return _ok(world.get(f"/vendors/{V_BOTH}/ledger", user, **params))["ledger"]


def _assert_figures(ledger: dict, expected: dict, who: str) -> None:
    for key, value in expected.items():
        assert ledger[key] == pytest.approx(value), (who, key, ledger[key], value)


def test_pune_accountant_ledger_is_punes_share(world):
    ledger = _ledger(world, ACCT_PUNE)
    _assert_figures(ledger, _PUNE_SHARE, "Pune accountant, no store_id")
    refs = {e["ref"] for e in ledger["entries"]}
    assert refs == {"B-P1", "B-P2", "PAY-P1", "PAY-AP", "DN-P2", "CN-REB"}, (
        f"F63: a Pune accountant's ledger carries rows of another shop: {sorted(refs)}"
    )
    # Naming its own shop is the same answer.
    _assert_figures(_ledger(world, ACCT_PUNE, store_id=PUN), _PUNE_SHARE, "Pune, store_id=Pune")


def test_pune_ledger_is_what_finance_and_the_report_say_pune_owes(world):
    """One payable: the vendor ledger, the Suppliers card
    (/finance/vendor-payments) and the Purchases report all give a Pune login
    the same figure for the same supplier."""
    ledger = _ledger(world, ACCT_PUNE)
    vp = {r["vendor_id"]: r for r in _ok(world.get("/finance/vendor-payments", ACCT_PUNE, store_id=PUN))}
    card = vp[V_BOTH]
    assert card["balance"] == pytest.approx(ledger["closing_balance"])
    for key in ("total_billed", "total_paid", "total_tds", "total_debit_notes"):
        assert card[key] == pytest.approx(ledger[key]), key
    report = {
        r["vendor_id"]: r
        for r in _ok(world.get(REPORT, ACCT_PUNE, month="2026-09", store_id=PUN))["vendors"]
    }
    assert report[V_BOTH]["owed"] == pytest.approx(ledger["closing_balance"])
    assert report[V_BOTH]["billed"] == pytest.approx(ledger["total_billed"])


def test_dhanbad_accountant_gets_dhanbads_share_and_the_shops_add_up(world):
    _assert_figures(_ledger(world, ACCT_DHN), _DHN_SHARE, "Dhanbad accountant")
    _assert_figures(_ledger(world, ADMIN), _ALL_SHOPS, "admin, every shop")
    _assert_figures(_ledger(world, SUPERADMIN), _ALL_SHOPS, "superadmin, every shop")
    _assert_figures(_ledger(world, ADMIN, store_id=PUN), _PUNE_SHARE, "admin, Pune")
    _assert_figures(_ledger(world, ADMIN, store_id=DHN), _DHN_SHARE, "admin, Dhanbad")


def test_pune_accountant_lists_only_punes_payments_notes_and_bills(world):
    assert _ids(world.get(f"/vendors/{V_BOTH}/payments", ACCT_PUNE), "payments", "payment_id") == {
        "PAY-P1",
        "PAY-AP",
    }
    assert _ids(
        world.get(f"/vendors/{V_BOTH}/debit-notes", ACCT_PUNE), "debit_notes", "debit_note_id"
    ) == {"DN-P2", "CN-REB"}
    assert _ids(world.get(f"/vendors/{V_BOTH}/bills", ACCT_PUNE), "bills", "bill_id") == {
        "B-P1",
        "B-P2",
    }


def test_admin_lists_every_shops_payments_and_notes_and_can_narrow(world):
    assert _ids(world.get(f"/vendors/{V_BOTH}/payments", ADMIN), "payments", "payment_id") == {
        "PAY-AD",
        "PAY-D1",
        "PAY-P1",
        "PAY-AP",
    }
    assert _ids(
        world.get(f"/vendors/{V_BOTH}/debit-notes", ADMIN), "debit_notes", "debit_note_id"
    ) == {"DN-D1", "DN-P2", "CN-REB"}
    assert _ids(
        world.get(f"/vendors/{V_BOTH}/payments", ADMIN, store_id=DHN), "payments", "payment_id"
    ) == {"PAY-AD", "PAY-D1"}
    assert _ids(
        world.get(f"/vendors/{V_BOTH}/debit-notes", ADMIN, store_id=DHN),
        "debit_notes",
        "debit_note_id",
    ) == {"DN-D1"}


def _aging(world, user, **params) -> dict:
    body = _ok(world.get("/vendors/ap-aging", user, **params))
    return {v["vendor_id"]: v["net_payable"] for v in body["vendors"]}, body["totals"]


def test_ap_aging_is_the_callers_shop_and_each_row_is_the_ledger_it_opens(world):
    """The Cash Flow page lists AP aging and opens a supplier's ledger from a
    row: the two must be the same shop's figures. Pune owes V-BOTH 580 and
    V-PUN 250; the no-shop V-OLD bill is an admin's only."""
    rows, totals = _aging(world, ACCT_PUNE)
    assert rows == {V_BOTH: pytest.approx(580.0), V_PUN: pytest.approx(250.0)}, (
        f"F63: a Pune accountant's AP aging carries another shop's money: {rows}"
    )
    assert totals["net_payable"] == pytest.approx(830.0)
    assert rows[V_BOTH] == pytest.approx(_ledger(world, ACCT_PUNE)["closing_balance"])
    assert _aging(world, ACCT_DHN)[0] == {V_BOTH: pytest.approx(550.0)}
    everyone, all_totals = _aging(world, ADMIN)
    assert everyone == {
        V_BOTH: pytest.approx(1130.0),
        V_PUN: pytest.approx(250.0),
        V_OLD: pytest.approx(300.0),
    }
    assert all_totals["net_payable"] == pytest.approx(1680.0)
    assert _aging(world, ADMIN, store_id=PUN)[0] == rows
    resp = world.get("/vendors/ap-aging", ACCT_PUNE, store_id=DHN)
    assert resp.status_code == 403, resp.text


@pytest.mark.parametrize("tail", _READS)
def test_pune_accountant_asking_for_dhanbad_is_refused_not_widened(world, tail):
    resp = world.get(f"/vendors/{V_BOTH}/{tail}", ACCT_PUNE, store_id=DHN)
    assert resp.status_code == 403, (tail, resp.status_code, resp.text[:200])
    assert "B-D1" not in resp.text


# ============================================================================
# 3. Recon ticks: another shop's bill is not there
# ============================================================================


def _missing(world, user, method: str, body=None) -> dict:
    path = RECON.format("NO-SUCH-BILL")
    resp = world.get(path, user) if method == "GET" else world.post(path, user, body or {})
    assert resp.status_code == 404, resp.text
    return resp.json()


_TICKS = {"entered_tally": True, "filed_gst": True, "payment_settled": True}


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_pune_accountant_cannot_read_or_tick_a_dhanbad_bills_recon(world, method):
    before = world.bill("B-D1")
    path = RECON.format("B-D1")
    resp = world.get(path, ACCT_PUNE) if method == "GET" else world.post(path, ACCT_PUNE, _TICKS)
    assert resp.status_code == 404, (
        f"F63: a Pune accountant {method} Dhanbad's recon ({resp.status_code}): "
        f"{resp.text[:200]}"
    )
    # The same body as a bill that does not exist: B-D1 is not confirmed.
    assert resp.json() == _missing(world, ACCT_PUNE, method, _TICKS)
    assert "acc-dhanbad" not in resp.text
    after = world.bill("B-D1")
    assert after == before, "a refused recon request changed the stored bill"
    assert after["recon"] == _DHN_RECON


def test_recon_stays_open_to_the_bills_own_shop_and_the_admin(world):
    assert _ok(world.get(RECON.format("B-D1"), ACCT_DHN))["recon"]["reconciled_by"] == "acc-dhanbad"
    assert _ok(world.get(RECON.format("B-D1"), ADMIN))["recon"]["reconciled"] is True
    _ok(world.post(RECON.format("B-P1"), ACCT_PUNE, {"entered_tally": True}))
    stored = world.bill("B-P1")["recon"]
    assert stored["entered_tally"] is True
    assert stored["entered_tally_by"] == ACCT_PUNE["user_id"]


def test_a_bill_with_no_shop_is_an_admins_only_on_recon(world):
    assert world.get(RECON.format("B-NOSHOP"), ACCT_PUNE).status_code == 404
    assert world.get(RECON.format("B-NOSHOP"), ADMIN).status_code == 200


# ============================================================================
# 4. A payment / debit note cannot name another shop's bill
# ============================================================================

_KINDS = ("payments", "debit-notes")
_COLL = {"payments": "vendor_payments", "debit-notes": "vendor_debit_notes"}


def _body(kind: str, bill_id) -> dict:
    if kind == "payments":
        return {"amount": 100.0, "payment_date": "2026-09-28", "mode": "BANK", "bill_id": bill_id}
    return {"amount": 100.0, "date": "2026-09-28", "reason": "short supply", "bill_id": bill_id}


def _snapshot(world) -> tuple:
    return (world.rows("vendor_payments"), world.rows("vendor_debit_notes"), world.rows("vendor_bills"))


@pytest.mark.parametrize("kind", _KINDS)
def test_pune_accountant_cannot_pay_or_credit_a_dhanbad_bill(world, kind):
    before = _snapshot(world)
    resp = world.post(f"/vendors/{V_BOTH}/{kind}", ACCT_PUNE, _body(kind, "B-D1"))
    assert resp.status_code == 404, (
        f"F63: a Pune accountant recorded a {kind[:-1]} against Dhanbad's bill "
        f"({resp.status_code}): {resp.text[:200]}"
    )
    assert _snapshot(world) == before, "a refused write changed the payable"
    missing = world.post(f"/vendors/{V_BOTH}/{kind}", ACCT_PUNE, _body(kind, "NO-SUCH-BILL"))
    assert missing.status_code == 404, missing.text
    assert resp.json() == missing.json(), "another shop's bill must answer like a missing one"
    assert _snapshot(world) == before


@pytest.mark.parametrize("kind", _KINDS)
def test_a_bill_with_no_shop_is_an_admins_only_to_pay(world, kind):
    before = _snapshot(world)
    resp = world.post(f"/vendors/{V_OLD}/{kind}", ACCT_PUNE, _body(kind, "B-NOSHOP"))
    assert resp.status_code == 404, resp.text
    assert _snapshot(world) == before
    assert world.post(f"/vendors/{V_OLD}/{kind}", ADMIN, _body(kind, "B-NOSHOP")).status_code == 201


@pytest.mark.parametrize("kind", _KINDS)
def test_another_suppliers_bill_is_not_there_either(world, kind):
    """A Pune bill of Pune Lens Co named on Two Shop Optics' payment would
    settle the wrong supplier's bill: same 404, nothing written."""
    before = _snapshot(world)
    resp = world.post(f"/vendors/{V_BOTH}/{kind}", ADMIN, _body(kind, "B-PX"))
    assert resp.status_code == 404, resp.text
    assert _snapshot(world) == before


@pytest.mark.parametrize("kind", _KINDS)
def test_pune_accountant_still_settles_a_pune_bill_and_pays_on_account(world, kind):
    resp = world.post(f"/vendors/{V_BOTH}/{kind}", ACCT_PUNE, _body(kind, "B-P1"))
    assert resp.status_code == 201, resp.text
    assert resp.json()["bill_id"] == "B-P1"
    # 600 billed - 200 already paid (180 + 20 TDS) - this 100.
    assert world.bill("B-P1")["outstanding"] == pytest.approx(300.0)
    assert world.bill("B-P1")["status"] == "PARTIAL"
    on_account = world.post(f"/vendors/{V_BOTH}/{kind}", ACCT_PUNE, _body(kind, None))
    assert on_account.status_code == 201, on_account.text
    assert len(world.rows(_COLL[kind])) == (4 + 2 if kind == "payments" else 3 + 2)


@pytest.mark.parametrize("kind", _KINDS)
def test_admin_settles_a_bill_in_any_shop(world, kind):
    resp = world.post(f"/vendors/{V_BOTH}/{kind}", ADMIN, _body(kind, "B-D1"))
    assert resp.status_code == 201, resp.text
    # 1000 billed - 300 paid - 100 debit note - this 100.
    assert world.bill("B-D1")["outstanding"] == pytest.approx(500.0)


# ============================================================================
# 5. The accounts roles are defined once
# ============================================================================

# Every router this change gates on the accounts roles.
_AP_GATED = (
    "api/routers/vendors/_shared.py",
    "api/routers/vendors/ap_bills.py",
    "api/routers/vendors/ap_payments.py",
    "api/routers/vendors/tds.py",
    "api/routers/purchase_recon.py",
    "api/routers/vendor_rebates.py",
)
_ACCOUNTS_SPELLED = ({"ADMIN", "ACCOUNTANT"}, {"ADMIN", "ACCOUNTANT", "SUPERADMIN"})


def _str_constants(nodes) -> set | None:
    vals = [n.value for n in nodes if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    return set(vals) if vals and len(vals) == len(nodes) else None


def test_no_router_here_spells_the_accounts_roles_itself():
    """Narrowing or widening who reads supplier money is one edit, in
    services/cost_mask.AP_ROLES. A router with its own ("ADMIN", "ACCOUNTANT")
    would silently keep the old set."""
    found = []
    for rel in _AP_GATED:
        with open(os.path.join(_BACKEND, rel), encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=rel)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
                elts = _str_constants(node.elts)
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "require_roles"
            ):
                elts = _str_constants(node.args)
            else:
                continue
            if elts in _ACCOUNTS_SPELLED:
                found.append(f"{rel}:{node.lineno} {sorted(elts)}")
    assert not found, "accounts roles spelled locally instead of cost_mask.AP_ROLES: " + "; ".join(found)


def test_the_gates_are_the_one_tuple():
    from api.routers.vendors import _shared
    from api.services import cost_mask

    assert _shared._AP_ROLES is cost_mask.AP_ROLES
    assert _shared._VENDOR_ROLES is cost_mask.PURCHASE_ROLES
    assert recon_mod._AP_ROLES is cost_mask.AP_ROLES


def test_vendor_rebates_ask_the_payables_rule(monkeypatch):
    """The rebate gate is can_see_cost(user, "payables"): whoever that rule
    admits, the rebate routes admit -- no second list to keep in step."""
    from fastapi import HTTPException

    from api.routers import vendor_rebates
    from api.services import cost_mask

    manager = _user("STORE_MANAGER", PUN, [PUN])
    with pytest.raises(HTTPException) as exc:
        vendor_rebates._require(manager, "view vendor rebates")
    assert exc.value.status_code == 403
    vendor_rebates._require(ACCT_PUNE, "view vendor rebates")
    monkeypatch.setitem(cost_mask._CONTEXT_ROLES, "payables", {"STORE_MANAGER"})
    vendor_rebates._require(manager, "view vendor rebates")  # the one rule moved


# ============================================================================
# The accounts roles are ONE object, and the RBAC matrix says what the rows say
# ============================================================================
# _core.ACCOUNTS is sorted(cost_mask.AP_ROLES): narrowing AP_ROLES moves every
# row and handler that IS it. A row (or a handler tuple) that spells the same
# roles out by hand stayed put under that mutation and every test stayed
# green -- two definitions of 'the accounts roles'.


def test_every_accounts_row_in_the_vendor_rows_is_the_one_list():
    from api.services.rbac_policy import rows_vendors
    from api.services.rbac_policy._core import ACCOUNTS

    spelled = [
        (r["method"], r["path"])
        for r in rows_vendors.ROWS
        if r.get("allowed") == ACCOUNTS and r["allowed"] is not ACCOUNTS
    ]
    assert not spelled, spelled


def test_the_purchase_invoice_gate_is_the_accounts_rule():
    from api.routers import purchase_invoices
    from api.services.cost_mask import AP_ROLES

    assert purchase_invoices._AP_ROLES is AP_ROLES


_MATRIX = os.path.join(_BACKEND, "..", "docs", "reference", "RBAC_MATRIX.md")
# Every row the supplier-money rulings (2026-09-28 .. 10-07) moved.
_MOVED = (
    ("GET", "/api/v1/finance/gst/summary"),
    ("GET", "/api/v1/finance/vendor-payments"),
    ("GET", "/api/v1/reports/gstr3b"),
    ("GET", "/api/v1/reports/gstr3b/gstn-json"),
    ("GET", "/api/v1/rtv-debit-notes/{debit_note_id}/print"),
    ("GET", "/api/v1/rtv-debit-notes/{debit_note_id}/tally"),
    ("GET", "/api/v1/vendors/purchases-this-month"),
    ("GET", "/api/v1/vendors/{vendor_id}/bills"),
    ("GET", "/api/v1/vendors/{vendor_id}/debit-notes"),
    ("GET", "/api/v1/vendors/{vendor_id}/ledger"),
    ("GET", "/api/v1/vendors/{vendor_id}/payments"),
)


@pytest.mark.skipif(not os.path.exists(_MATRIX), reason="docs/ not shipped here")
@pytest.mark.parametrize("method,path", _MOVED)
def test_the_rbac_matrix_says_what_the_row_says(method, path):
    import re

    from api.services import rbac_policy

    with open(_MATRIX, encoding="utf-8") as fh:
        doc = {
            (m, p): (cell.strip(), s.strip())
            for m, p, cell, s in re.findall(
                r"^\| `(\w+)` \| `([^`]+)` \| ([^|]*)\| ([^|]*)\|", fh.read(), re.M
            )
        }
    row = next(r for r in rbac_policy.POLICY if (r["method"], r["path"]) == (method, path))
    assert (method, path) in doc, f"{method} {path} is missing from RBAC_MATRIX.md"
    cell, scoped = doc[(method, path)]
    want = "AUTH" if row["allowed"] == "AUTHENTICATED" else set(row["allowed"]) - {"SUPERADMIN"}
    got = cell if cell in ("AUTH", "PUBLIC") else {r.strip() for r in cell.split(",")} - {"SUPERADMIN"}
    assert (got, scoped) == (want, "S" if row.get("store_scoped") else "")
