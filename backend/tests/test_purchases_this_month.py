"""
IMS 2.0 -- PURCHASES THIS MONTH, ONE PAYABLE, ONE SHOP SCOPE (audit F56 + F63)
===============================================================================
Owner audit 2026-09-29, rows F56 and F63, and the owner rulings of 2026-09-28
(second set, "Reports / scope"):

  F56  No screen answers "what did we buy this month, from whom, what is
       unpaid". The same "we owe" figure reads Rs 0, Rs 1,77,896 and
       Rs 1,44,456 on different screens.
       Ruling: build ONE 'Purchases this month' report per vendor -- ordered,
       received, billed, paid, owed, next due -- with totals, a month picker and
       an export, and every figure equal to the supplier ledger.

  F63  Purchase Orders and GRN say 0 for an admin while Purchase Invoices on the
       same page lists another shop's bills; invoices ignore the chosen shop, so
       a Pune accountant would see Dhanbad's bills.
       Ruling: admins open Purchase on ALL STORES with a shop filter, every tab
       obeys it, managers and other roles keep their own shop.

THE ONE PAYABLE RULE is the supplier ledger: ap_engine.build_ledger over a
vendor's bills, payments (cash + TDS) and debit notes, read today by
GET /vendors/{id}/ledger and GET /finance/vendor-payments. The anchor test below
proves the fixture owes exactly what is written here BY HAND; every other
payable figure must then equal it.

The world: two shops, two vendors, written out by hand.

  Jharkhand Optical (V-JHK) supplies Dhanbad (BV-DHN-01)
    PO-A1   sent 2026-09-03, 3 x 1000 @ 12% GST          = 3360  (placed)
    PO-A2   DRAFT, never sent                            = 9999  (not ordered)
    PO-A3   sent, then CANCELLED                         = 4480  (not ordered)
    GRN-A1  accepted 2026-09-05, 2 of PO-A1's 3 frames   = 2 x 1000 x 1.12 = 2240
    B-PAID  bill 2026-08-01, 1500, paid in full by P0 (2026-08-05)
    B0      bill 2026-08-10, 5000, due 2026-09-09, nothing paid
    B1      bill 2026-09-06, 2240 (for GRN-A1), due 2026-10-06
    P1      1000 against B1 on 2026-09-10
    P2      500 on account (no bill) on 2026-09-15
    D1      debit note 200 against B1 on 2026-09-12
    ledger: bills 8740 - payments 3000 - debit notes 200 = OWED 5540

  Pune Lens Co (V-PUN) supplies Pune (WO-PUN-01)
    PO-B1   sent 2026-09-04, 1 x 2000 @ 12%              = 2240
    GRN-B1  accepted 2026-09-07, the 1 frame             = 2240
    B2      bill 2026-09-08, 2240 (for GRN-B1), due 2026-09-23
    ledger: OWED 2240

  All stores together owe 7780.

Contract pinned for the report (the build follows it):
  GET /api/v1/vendors/purchases-this-month?month=YYYY-MM[&store_id=]
  -> {"month", "store_id", "vendors": [{vendor_id, vendor_name, ordered,
      received, billed, paid, owed, next_due_date}], "totals": {ordered,
      received, billed, paid, owed}}
  ordered  = POs SENT in the month (drafts and cancelled orders excluded)
  received = accepted quantity x the PO line's price incl. GST, receipts
             accepted in the month
  billed / paid = the ledger's BILL / PAYMENT rows dated in the month
  owed     = the ledger balance at the month's end
  next_due_date = the earliest due date of a bill that still has money on it
  Roles: the supplier-balance readers (ADMIN, ACCOUNTANT; SUPERADMIN) -- never
  a manager, never the counter. Scope: the ONE server-side shop rule every
  Purchase tab uses (api.dependencies.resolve_store_scope).

These were strict xfails while the findings were open; the fix turned them into
plain tests. A regression raises FindingStillOpen, naming the finding.

Run: JWT_SECRET_KEY=test ENVIRONMENT=test python -m pytest
     backend/tests/test_purchases_this_month.py -q
No emoji (Windows cp1252).
"""

from __future__ import annotations

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

from api.routers import finance as finance_pkg  # noqa: E402
from api.routers import purchase_invoices as pinv_mod  # noqa: E402
from api.routers import vendor_returns as vret_mod  # noqa: E402
from api.routers import vendors as vendors_pkg  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402

DHN = "BV-DHN-01"
PUN = "WO-PUN-01"
ONLINE = "BV-ONLINE-01"
VA = "V-JHK"
VB = "V-PUN"
SHOP_OF_VENDOR = {VA: DHN, VB: PUN}

REPORT = "/vendors/purchases-this-month"


class FindingStillOpen(AssertionError):
    """The audited behaviour is still there (not a broken fixture)."""


def _open(ok: bool, message: str) -> None:
    if not ok:
        raise FindingStillOpen(message)


def _user(role: str, store: str | None, stores=()) -> dict:
    return {
        "user_id": f"u-{role.lower()}-{store or 'all'}",
        "username": role.lower(),
        "roles": [role],
        "store_ids": list(stores),
        "active_store_id": store,
    }


# A first-time admin lands on the ONLINE store (F63) -- the default the owner
# ruled out; the scope rule must still give him every shop.
ADMIN = _user("ADMIN", ONLINE)
ACCT_PUNE = _user("ACCOUNTANT", PUN, [PUN])


# ============================================================================
# Engine: the real Mongo CI runs against; mongomock only as a dev-box fallback
# ============================================================================


@pytest.fixture(scope="module")
def mongo_db():
    from pymongo import MongoClient

    uri = (
        os.getenv("MONGODB_URL")
        or os.getenv("MONGODB_URI")
        or "mongodb://localhost:27017"
    )
    db_name = f"ims_test_purrep_{uuid.uuid4().hex[:8]}"
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
    db = client[db_name]
    _seed(db)
    try:
        yield db
    finally:
        try:
            client.drop_database(db_name)
        except Exception:
            pass
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


def _seed(db) -> None:
    def ins(coll, docs):
        for d in docs:
            key = next(k for k in d if k.endswith("_id"))
            db[coll].insert_one({"_id": d[key], **d})

    ins(
        "vendors",
        [
            {"vendor_id": VA, "legal_name": "Jharkhand Optical", "trade_name": "Jharkhand Optical", "is_active": True},
            {"vendor_id": VB, "legal_name": "Pune Lens Co", "trade_name": "Pune Lens Co", "is_active": True},
        ],
    )

    def po(po_id, vendor, store, status, total, sent_at, items):
        return {
            "po_id": po_id,
            "po_number": po_id,
            "vendor_id": vendor,
            "delivery_store_id": store,
            "status": status,
            "total_amount": total,
            "sent_at": sent_at,
            "created_at": (sent_at or "2026-09-20T09:00:00")[:10] + "T08:00:00",
            "items": items,
        }

    frame_a = {"product_id": "P-FRAME-A", "quantity": 3, "unit_price": 1000.0, "tax_rate": 12.0}
    frame_b = {"product_id": "P-FRAME-B", "quantity": 1, "unit_price": 2000.0, "tax_rate": 12.0}
    ins(
        "purchase_orders",
        [
            po("PO-A1", VA, DHN, "PARTIALLY_RECEIVED", 3360.0, "2026-09-03T10:00:00", [frame_a]),
            po("PO-A2", VA, DHN, "DRAFT", 9999.0, None, [dict(frame_a, quantity=9)]),
            po("PO-A3", VA, DHN, "CANCELLED", 4480.0, "2026-09-10T10:00:00", [dict(frame_a, quantity=4)]),
            po("PO-B1", VB, PUN, "RECEIVED", 2240.0, "2026-09-04T10:00:00", [frame_b]),
        ],
    )

    def grn(grn_id, vendor, store, po_id, pid, qty, accepted_at):
        return {
            "grn_id": grn_id,
            "grn_number": grn_id,
            "vendor_id": vendor,
            "store_id": store,
            "po_id": po_id,
            "status": "ACCEPTED",
            "accepted_at": accepted_at,
            "created_at": accepted_at,
            "items": [
                {"product_id": pid, "received_qty": qty, "accepted_qty": qty, "rejected_qty": 0}
            ],
        }

    ins(
        "grns",
        [
            grn("GRN-A1", VA, DHN, "PO-A1", "P-FRAME-A", 2, "2026-09-05T11:00:00"),
            grn("GRN-B1", VB, PUN, "PO-B1", "P-FRAME-B", 1, "2026-09-07T11:00:00"),
        ],
    )

    def bill(bill_id, vendor, store, bill_date, due, total, status, grn_id=None, po_id=None):
        return {
            "bill_id": bill_id,
            "invoice_id": bill_id,
            "doc_type": "PURCHASE_INVOICE",
            "vendor_id": vendor,
            # Where the goods landed. Bills booked from a receipt must carry
            # the receipt's shop (the build stamps it at booking and back-fills
            # old rows from their GRN) -- F63 filters on it.
            "store_id": store,
            "bill_number": f"INV-{bill_id}",
            "invoice_number": f"INV-{bill_id}",
            "bill_date": bill_date,
            "invoice_date": bill_date,
            "due_date": due,
            "total_amount": total,
            "total": total,
            "status": status,
            "grn_id": grn_id,
            "po_id": po_id,
        }

    ins(
        "vendor_bills",
        [
            bill("B-PAID", VA, DHN, "2026-08-01", "2026-08-31", 1500.0, "PAID"),
            bill("B0", VA, DHN, "2026-08-10", "2026-09-09", 5000.0, "OUTSTANDING"),
            bill("B1", VA, DHN, "2026-09-06", "2026-10-06", 2240.0, "PARTIAL", "GRN-A1", "PO-A1"),
            bill("B2", VB, PUN, "2026-09-08", "2026-09-23", 2240.0, "OUTSTANDING", "GRN-B1", "PO-B1"),
        ],
    )

    def pay(pid, vendor, bill_id, amount, on):
        return {
            "payment_id": pid,
            "vendor_id": vendor,
            "bill_id": bill_id,
            "amount": amount,
            "tds_amount": 0.0,
            "mode": "BANK",
            "payment_date": on,
            "created_at": on + "T12:00:00",
        }

    ins(
        "vendor_payments",
        [
            pay("P0", VA, "B-PAID", 1500.0, "2026-08-05"),
            pay("P1", VA, "B1", 1000.0, "2026-09-10"),
            pay("P2", VA, None, 500.0, "2026-09-15"),
        ],
    )
    ins(
        "vendor_debit_notes",
        [
            {
                "debit_note_id": "D1",
                "debit_note_number": "DN-1",
                "vendor_id": VA,
                "bill_id": "B1",
                "amount": 200.0,
                "date": "2026-09-12",
                "reason": "one frame chipped",
            }
        ],
    )
    ins(
        "vendor_returns",
        [
            {"return_id": "R-A", "vendor_id": VA, "store_id": DHN, "status": "PENDING", "created_at": "2026-09-11T10:00:00"},
            {"return_id": "R-B", "vendor_id": VB, "store_id": PUN, "status": "PENDING", "created_at": "2026-09-12T10:00:00"},
        ],
    )


# ============================================================================
# The app: the REAL vendors / purchase-invoice / vendor-return / finance routers
# ============================================================================


class _World:
    def __init__(self, client: TestClient, app: FastAPI):
        self._client = client
        self._app = app

    def get(self, path: str, user: dict, **params):
        async def _as_user():
            return dict(user)

        self._app.dependency_overrides[get_current_user] = _as_user
        params = {k: v for k, v in params.items() if v is not None}
        return self._client.get(path, params=params)


@pytest.fixture
def world(mongo_db, monkeypatch):
    from database.repositories.vendor_repository import (
        GRNRepository,
        PurchaseOrderRepository,
        VendorRepository,
    )

    proxy = _DBProxy(mongo_db)
    for mod in (vendors_pkg, finance_pkg, pinv_mod, vret_mod):
        monkeypatch.setattr(mod, "_get_db", lambda: proxy)
    monkeypatch.setattr(vendors_pkg, "get_vendor_repository", lambda: VendorRepository(mongo_db["vendors"]))
    monkeypatch.setattr(
        vendors_pkg,
        "get_purchase_order_repository",
        lambda: PurchaseOrderRepository(mongo_db["purchase_orders"]),
    )
    monkeypatch.setattr(vendors_pkg, "get_grn_repository", lambda: GRNRepository(mongo_db["grns"]))

    app = FastAPI()
    # Same order as main.py: the concrete /purchase-invoices paths before the
    # vendors router, whose catch-all GET /{vendor_id} would swallow them.
    app.include_router(pinv_mod.router, prefix="/vendors/purchase-invoices")
    app.include_router(vendors_pkg.router, prefix="/vendors")
    app.include_router(vret_mod.router, prefix="/vendor-returns")
    app.include_router(finance_pkg.router, prefix="/finance")
    return _World(TestClient(app), app)


# ============================================================================
# The anchor: the supplier ledger owes exactly what the header says
# ============================================================================


def test_the_supplier_ledger_owes_5540_and_2240(world):
    """Not a finding -- the fixture proven against the ONE payable rule. Both of
    today's ledger readers agree, so every other screen has a number to meet."""
    for vendor, owed, billed in ((VA, 5540.0, 8740.0), (VB, 2240.0, 2240.0)):
        resp = world.get(f"/vendors/{vendor}/ledger", ADMIN)
        assert resp.status_code == 200, resp.text
        ledger = resp.json()["ledger"]
        assert ledger["closing_balance"] == pytest.approx(owed)
        assert ledger["total_billed"] == pytest.approx(billed)

    resp = world.get("/finance/vendor-payments", ADMIN)
    assert resp.status_code == 200, resp.text
    balances = {r["vendor_id"]: r["balance"] for r in resp.json()}
    assert balances[VA] == pytest.approx(5540.0)
    assert balances[VB] == pytest.approx(2240.0)


# ============================================================================
# F56 part 1: every screen that says what we owe reads the ledger
# ============================================================================


def test_f56_ap_aging_owes_what_the_ledger_owes(world):
    resp = world.get("/vendors/ap-aging", ADMIN)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    per_vendor = {v["vendor_id"]: v["net_payable"] for v in body["vendors"]}
    assert set(per_vendor) == {VA, VB}, per_vendor
    _open(
        per_vendor[VA] == pytest.approx(5540.0),
        f"F56: AP aging says Jharkhand Optical is owed {per_vendor[VA]}, the ledger 5540",
    )
    _open(
        body["totals"]["net_payable"] == pytest.approx(7780.0),
        f"F56: AP aging total {body['totals']['net_payable']}, the ledgers 7780",
    )


def test_f56_cash_flow_payables_headline_owes_what_the_ledger_owes(world):
    resp = world.get("/finance/owner-dashboard", ADMIN)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    total = body["payables"]["total"]
    _open(
        total == pytest.approx(7780.0),
        f"F56: Cash Flow payables headline {total}, the supplier ledgers 7780",
    )
    assert body["net_position"] == pytest.approx(
        body["receivables"]["total"] - 7780.0
    ), "net position must be AR minus the same payable"


# ============================================================================
# F56 part 2: the 'Purchases this month' report
# ============================================================================


def _report(world, user, **params):
    resp = world.get(REPORT, user, **params)
    assert resp.status_code in (200, 403, 404), resp.text  # a crash is never the finding
    _open(resp.status_code == 200, f"F56: no Purchases-this-month report ({resp.status_code})")
    return resp.json()


def _rows(body):
    return {r["vendor_id"]: r for r in body["vendors"]}


def test_f56_report_september_per_vendor_matches_the_ledger(world):
    body = _report(world, ADMIN, month="2026-09")
    rows = _rows(body)
    expected = {
        VA: dict(ordered=3360.0, received=2240.0, billed=2240.0, paid=1500.0, owed=5540.0, next_due_date="2026-09-09"),
        VB: dict(ordered=2240.0, received=2240.0, billed=2240.0, paid=0.0, owed=2240.0, next_due_date="2026-09-23"),
    }
    assert set(rows) == set(expected), rows
    for vid, exp in expected.items():
        row = rows[vid]
        for key in ("ordered", "received", "billed", "paid", "owed"):
            assert row[key] == pytest.approx(exp[key]), (vid, key, row)
        assert row["next_due_date"] == exp["next_due_date"], (vid, row)
    assert rows[VA]["vendor_name"] == "Jharkhand Optical"
    totals = body["totals"]
    for key, value in dict(ordered=5600.0, received=4480.0, billed=4480.0, paid=1500.0, owed=7780.0).items():
        assert totals[key] == pytest.approx(value), (key, totals)

    # "Every figure must equal the supplier ledger": owed IS the ledger balance.
    for vid in (VA, VB):
        ledger = world.get(f"/vendors/{vid}/ledger", ADMIN).json()["ledger"]
        assert rows[vid]["owed"] == pytest.approx(ledger["closing_balance"])


def test_f56_report_month_picker_reads_august(world):
    """The month picker: August holds B-PAID + B0 and the payment P0, nothing
    ordered or received; owed is the ledger balance at 31 August."""
    body = _report(world, ADMIN, month="2026-08")
    row = _rows(body)[VA]
    for key, value in dict(ordered=0.0, received=0.0, billed=6500.0, paid=1500.0, owed=5000.0).items():
        assert row[key] == pytest.approx(value), (key, row)
    for key, value in dict(ordered=0.0, received=0.0, billed=6500.0, paid=1500.0, owed=5000.0).items():
        assert body["totals"][key] == pytest.approx(value), (key, body["totals"])


@pytest.mark.parametrize("role", ["SALES_STAFF", "SALES_CASHIER", "CASHIER", "STORE_MANAGER"])
def test_f56_report_is_for_the_supplier_balance_readers_only(world, role):
    """Counter roles never see what we owe (F60); managers do not read
    supplier balances today (AP aging / ledger are ADMIN + ACCOUNTANT)."""
    resp = world.get(REPORT, _user(role, PUN, [PUN]), month="2026-09")
    assert resp.status_code in (200, 403, 404), resp.text
    _open(resp.status_code == 403, f"F56: {role} got {resp.status_code} from the report, not 403")


# ============================================================================
# F63: one shop scope, server-side, shared by every Purchase tab
# ============================================================================

_TABS = {
    "orders": ("/vendors/purchase-orders", {}, "purchase_orders", lambda r: r.get("delivery_store_id")),
    "receiving": ("/vendors/grn", {}, "grns", lambda r: r.get("store_id")),
    "invoices": ("/vendors/purchase-invoices", {}, "purchase_invoices", lambda r: r.get("store_id")),
    "returns": ("/vendor-returns", {}, "returns", lambda r: r.get("store_id")),
    "report": (REPORT, {"month": "2026-09"}, "vendors", lambda r: SHOP_OF_VENDOR.get(r.get("vendor_id"))),
}

# (user, ?store_id, the shops the tab must show -- or 403)
_CASES = {
    "admin opens Purchase (all stores)": (ADMIN, None, {DHN, PUN}),
    "admin picks Dhanbad": (ADMIN, DHN, {DHN}),
    "Pune accountant (own shop)": (ACCT_PUNE, None, {PUN}),
    "Pune accountant asks for Dhanbad": (ACCT_PUNE, DHN, 403),
}


_MATRIX = [
    pytest.param(_tab, _case, id=f"{_tab}: {_case}") for _tab in _TABS for _case in _CASES
]


@pytest.mark.parametrize("tab,case", _MATRIX)
def test_f63_every_purchase_tab_obeys_one_shop_scope(world, tab, case):
    path, extra, key, shop_of = _TABS[tab]
    user, store, expected = _CASES[case]
    resp = world.get(path, user, store_id=store, **extra)
    assert resp.status_code in (200, 403, 404), resp.text  # a crash is never the finding
    if expected == 403:
        _open(
            resp.status_code == 403,
            f"F63: {tab} answered {resp.status_code} to a Pune login asking for Dhanbad",
        )
        return
    _open(resp.status_code == 200, f"F63: {tab} answered {resp.status_code}")
    shops = {shop_of(r) for r in resp.json()[key]}
    _open(shops == expected, f"F63: {tab} shows shops {sorted(map(str, shops))}, expected {sorted(expected)}")


# ============================================================================
# F63: a first-time admin is not parked on the online store
# ============================================================================


def test_f63_a_first_time_admin_is_not_parked_on_the_online_store(monkeypatch):
    import database.connection as conn
    from api.routers import auth as auth_mod
    from strict_fakes import StrictDB

    db = StrictDB()
    db.seed(
        "stores",
        [
            {"store_id": ONLINE, "store_type": "ONLINE", "is_active": True},
            {"store_id": DHN, "store_type": "RETAIL", "is_active": True},
            {"store_id": PUN, "store_type": "RETAIL", "is_active": True},
        ],
    )
    monkeypatch.setattr(conn, "get_db", lambda: type("H", (), {"db": db})())
    picked = auth_mod._default_active_store({"roles": ["ADMIN"]})
    assert picked in (ONLINE, DHN, PUN), picked
    _open(picked != ONLINE, f"F63: a first-time admin defaults to {picked}, the online store")
