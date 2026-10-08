"""
IMS 2.0 -- WHICH SHOP A SUPPLIER PAYMENT / DEBIT NOTE IS BOOKED TO, ROUND 3
=========================================================================
Review 2026-10-01 (round 2) findings #1 (HIGH), #13, #2, #7.
Owner rulings: supplier balances are ADMIN / SUPERADMIN / ACCOUNTANT only;
F63 -- ADMIN / SUPERADMIN reach every shop, everyone else only their own
shop even when the request is edited, and another shop's object by id is
the same 404 a missing one gets.

  #1 / #13  An ADMIN's on-account money (no bill, no shop named) was stamped
            with HIS topbar shop (HQ / ONLINE). For a Pune-only supplier the
            Pune accountant then saw the bill still owed on every screen, and
            could pay it again. Now an admin's money with no bill, no receipt
            and no shop named takes the supplier's shop by its bills (the
            ledger's legacy rule: its latest bill on or before the money,
            else its earliest), worked out and STAMPED when it is written
            (round 3 review #2 / #8 -- left unstamped, it moved shops when
            another shop later booked a back-dated bill; see
            test_money_stamped_at_write.py). A shop the admin names still
            wins; a non-admin's money is still his own shop's.
  #2 / #7   A debit note naming a goods receipt (grn_id) never checked it: a
            Pune accountant's note on Dhanbad's receipt released Dhanbad's
            rejected-goods payment hold (owner ruling 7) and booked the
            credit to Pune; a receipt that did not exist was accepted too.
            Now the receipt must exist, be this supplier's and be in the
            caller's reach -- else the same 404 a missing receipt gets,
            before anything is written -- and the note takes the receipt's
            shop.

Today is pinned to 1 Oct 2026 (IST) for every figure.

Run: JWT_SECRET_KEY=test ENVIRONMENT=test python -m pytest
     backend/tests/test_money_shop_round3.py -q
No emoji (Windows cp1252).
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MONGODB_URI", "")

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BACKEND)

from api.routers import finance as finance_pkg  # noqa: E402
from api.routers import purchase_invoices as pi_mod  # noqa: E402
from api.routers import vendors as vendors_pkg  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402
from api.services import ap_engine  # noqa: E402

DHN = "BV-DHN-01"
PUN = "WO-PUN-01"
HQ = "BV-HQ-01"
V_PUN = "V-PUN"  # Pune Lens Co: has only ever billed Pune
V_BOTH = "V-BOTH"  # Two Shop Optics: Dhanbad and Pune
V_OTHER = "V-OTHER"
TODAY = "2026-10-01"


def _user(role: str, store, stores=()) -> dict:
    return {
        "user_id": f"u-{role.lower()}-{store or 'all'}",
        "username": role.lower(),
        "roles": [role],
        "store_ids": list(stores),
        "active_store_id": store,
    }


ADMIN_HQ = _user("ADMIN", HQ)
SUPER_HQ = _user("SUPERADMIN", HQ)
ACCT_PUNE = _user("ACCOUNTANT", PUN, [PUN])
ACCT_DHN = _user("ACCOUNTANT", DHN, [DHN])


@pytest.fixture(autouse=True)
def _today(monkeypatch):
    """Every payable figure is struck on 1 Oct 2026 (IST)."""
    monkeypatch.setattr(ap_engine, "now_ist_naive", lambda: datetime.fromisoformat(TODAY + "T12:00:00"))


def _bill(bill_id, vendor, store, on, total, due, **extra) -> dict:
    return {
        "bill_id": bill_id,
        "vendor_id": vendor,
        "bill_number": bill_id,
        "bill_date": on,
        "due_date": due,
        "total_amount": total,
        "outstanding": total,
        "status": "OUTSTANDING",
        "store_id": store,
        **extra,
    }


def _grn(grn_id, vendor, store, rejected=0) -> dict:
    return {
        "grn_id": grn_id,
        "grn_number": grn_id,
        "vendor_id": vendor,
        "store_id": store,
        "status": "ACCEPTED",
        "items": [{"product_id": "P1", "accepted_qty": 2, "rejected_qty": rejected}],
    }


def _seed(db) -> None:
    """V-PUN: bill B-P1 (Pune, 5 Sep, Rs 5,000, due 25 Sep -- overdue).
    V-BOTH: B-D (Dhanbad, 10 Sep, Rs 2,000) on receipt GRN-D, one unit
    rejected and no debit note: HELD; B-P (Pune, 12 Sep, Rs 1,000) on receipt
    GRN-P, one unit rejected: HELD. GRN-X is Pune's receipt from another
    supplier."""
    db["vendors"].insert_many([
        {"vendor_id": V_PUN, "legal_name": "Pune Lens Co", "trade_name": "Pune Lens Co", "is_active": True, "credit_days": 20},
        {"vendor_id": V_BOTH, "legal_name": "Two Shop Optics", "trade_name": "Two Shop Optics", "is_active": True, "credit_days": 30},
        {"vendor_id": V_OTHER, "legal_name": "Other Co", "trade_name": "Other Co", "is_active": True, "credit_days": 30},
    ])
    db["vendor_bills"].insert_many([
        _bill("B-P1", V_PUN, PUN, "2026-09-05", 5000.0, "2026-09-25"),
        _bill("B-D", V_BOTH, DHN, "2026-09-10", 2000.0, "2026-10-10", grn_id="GRN-D"),
        _bill("B-P", V_BOTH, PUN, "2026-09-12", 1000.0, "2026-10-12", grn_id="GRN-P"),
    ])
    db["grns"].insert_many([
        _grn("GRN-D", V_BOTH, DHN, rejected=1),
        _grn("GRN-P", V_BOTH, PUN, rejected=1),
        _grn("GRN-X", V_OTHER, PUN, rejected=1),
    ])


@pytest.fixture(scope="module")
def _client():
    from pymongo import MongoClient

    uri = os.getenv("MONGODB_URL") or os.getenv("MONGODB_URI") or "mongodb://localhost:27017"
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


class _World:
    def __init__(self, client: TestClient, app: FastAPI, db):
        self._client = client
        self._app = app
        self.db = db

    def _as(self, user: dict):
        async def _as_user():
            return dict(user)

        self._app.dependency_overrides[get_current_user] = _as_user

    def get(self, path, user, **params):
        self._as(user)
        return self._client.get(path, params={k: v for k, v in params.items() if v is not None})

    def post(self, path, user, body, **params):
        self._as(user)
        return self._client.post(path, json=body, params={k: v for k, v in params.items() if v is not None})

    def rows(self, coll: str) -> list:
        return sorted(self.db[coll].find({}, {"_id": 0}), key=lambda d: str(sorted(d.items())))

    def snapshot(self) -> tuple:
        return tuple(self.rows(c) for c in ("vendor_payments", "vendor_debit_notes", "vendor_bills", "grns"))


@pytest.fixture
def world(_client, monkeypatch):
    from database.repositories.vendor_repository import GRNRepository, VendorRepository

    db_name = f"ims_test_money_shop_r3_{uuid.uuid4().hex[:8]}"
    db = _client[db_name]
    _seed(db)
    proxy = _DBProxy(db)
    for mod in (vendors_pkg, finance_pkg, pi_mod):
        monkeypatch.setattr(mod, "_get_db", lambda: proxy)
    monkeypatch.setattr(vendors_pkg, "get_vendor_repository", lambda: VendorRepository(db["vendors"]))
    for mod in (vendors_pkg, pi_mod):
        monkeypatch.setattr(mod, "get_grn_repository", lambda: GRNRepository(db["grns"]))
    app = FastAPI()
    app.include_router(vendors_pkg.router, prefix="/vendors")
    app.include_router(finance_pkg.router, prefix="/finance")
    try:
        yield _World(TestClient(app), app, db)
    finally:
        try:
            _client.drop_database(db_name)
        except Exception:
            pass


def _ok(resp, code=200):
    assert resp.status_code == code, resp.text
    return resp.json()


def _pune_owes(world, vendor, user=ACCT_PUNE, shop=None) -> dict:
    """What every payable screen says `user` owes `vendor`, in `shop` (None =
    the caller's own scope, as each screen sends it)."""
    ledger = _ok(world.get(f"/vendors/{vendor}/ledger", user, store_id=shop))["ledger"]
    report = _ok(world.get("/vendors/purchases-this-month", user, month="2026-09", store_id=shop))
    r_row = next((v for v in report["vendors"] if v["vendor_id"] == vendor), None)
    aging = _ok(world.get("/vendors/ap-aging", user, store_id=shop))
    a_row = next((v for v in aging["vendors"] if v["vendor_id"] == vendor), None)
    # The Suppliers card sends the Purchase shop it shows (the caller's own).
    card_shop = shop or user["active_store_id"]
    card = {r["vendor_id"]: r for r in _ok(world.get("/finance/vendor-payments", user, store_id=card_shop))}
    return {
        "ledger": ledger["closing_balance"],
        "report_owed": r_row["owed"] if r_row else 0.0,
        "report_paid": r_row["paid"] if r_row else 0.0,
        "aging": a_row["net_payable"] if a_row else 0.0,
        "card": card[vendor]["balance"] if vendor in card else 0.0,
    }


def _dashboard_payables(world, user) -> float:
    return _ok(world.get("/finance/owner-dashboard", user))["payables"]["total"]


# ----------------------------------------------------------------------------
# #1 (HIGH) / #13 -- an admin's on-account money is the supplier's shop's
# ----------------------------------------------------------------------------


@pytest.mark.parametrize("admin", [ADMIN_HQ, SUPER_HQ], ids=["admin", "superadmin"])
def test_r3_1_an_admin_at_hq_pays_a_pune_supplier_on_account_and_pune_owes_nothing(world, admin):
    """The HIGH probe: an admin sitting on HQ records Rs 5,000 on account for
    V-PUN, which has only ever billed Pune. The payment was stamped BV-HQ-01,
    so Pune's ledger, report, AP aging, owner dashboard and Suppliers card
    all still owed 5,000 -- and the Pune accountant could pay B-P1 again."""
    before = _pune_owes(world, V_PUN)
    assert before == {"ledger": 5000.0, "report_owed": 5000.0, "report_paid": 0.0, "aging": 5000.0, "card": 5000.0}
    pune_dash_before = _dashboard_payables(world, ACCT_PUNE)

    made = _ok(world.post(f"/vendors/{V_PUN}/payments", admin, {
        "amount": 5000.0, "payment_date": "2026-09-20", "mode": "BANK", "reference": "NEFT-1",
    }), 201)

    # Pune's, by V-PUN's own bills -- never the admin's topbar HQ -- and
    # stamped when written (round 3 #2 / #8), so a later bill cannot move it.
    assert made["store_id"] == PUN, f"#1: the admin's money was filed under {made['store_id']}"
    stored = world.db["vendor_payments"].find_one({"payment_id": made["payment_id"]}, {"_id": 0})
    assert stored["store_id"] == PUN, "#1: not stamped with the supplier's shop"

    after = _pune_owes(world, V_PUN)
    assert after == {"ledger": 0.0, "report_owed": 0.0, "report_paid": 5000.0, "aging": 0.0, "card": 0.0}, (
        f"#1: Pune still owes a supplier the admin paid: {after}"
    )
    assert _dashboard_payables(world, ACCT_PUNE) == pytest.approx(pune_dash_before - 5000.0)
    assert _dashboard_payables(world, ACCT_PUNE) == pytest.approx(1000.0)  # only V-BOTH's B-P is left

    # The payment is listed for the Pune accountant -- he sees why it is settled.
    listed = _ok(world.get(f"/vendors/{V_PUN}/payments", ACCT_PUNE))["payments"]
    assert [p["payment_id"] for p in listed] == [made["payment_id"]]

    # And it is not in HQ's books (HQ never bought from V-PUN).
    hq = _pune_owes(world, V_PUN, user=admin, shop=HQ)
    assert hq == {"ledger": 0.0, "report_owed": 0.0, "report_paid": 0.0, "aging": 0.0, "card": 0.0}, hq
    hq_payments = _ok(world.get(f"/vendors/{V_PUN}/payments", admin, store_id=HQ))["payments"]
    assert hq_payments == []
    # Every shop together: settled.
    assert _ok(world.get(f"/vendors/{V_PUN}/ledger", admin))["ledger"]["closing_balance"] == 0.0


@pytest.mark.parametrize("kind", ["payments", "debit-notes"])
def test_r3_1_an_admin_who_names_the_shop_stamps_it(world, kind):
    body = (
        {"amount": 100.0, "payment_date": "2026-09-20", "mode": "BANK"}
        if kind == "payments"
        else {"amount": 100.0, "date": "2026-09-20", "reason": "rate difference"}
    )
    by_query = _ok(world.post(f"/vendors/{V_BOTH}/{kind}", ADMIN_HQ, body, store_id=PUN), 201)
    by_body = _ok(world.post(f"/vendors/{V_BOTH}/{kind}", ADMIN_HQ, {**body, "store_id": DHN}), 201)
    assert (by_query["store_id"], by_body["store_id"]) == (PUN, DHN)
    coll, key = ("vendor_payments", "payment_id") if kind == "payments" else ("vendor_debit_notes", "debit_note_id")
    assert world.db[coll].find_one({key: by_query[key]})["store_id"] == PUN
    assert world.db[coll].find_one({key: by_body[key]})["store_id"] == DHN


def test_r3_1_a_non_admins_on_account_money_is_still_his_own_shops(world):
    made = _ok(world.post(f"/vendors/{V_BOTH}/payments", ACCT_PUNE, {
        "amount": 10.0, "payment_date": "2026-09-20", "mode": "BANK",
    }), 201)
    assert made["store_id"] == PUN


# ----------------------------------------------------------------------------
# #2 / #7 -- a debit note's goods receipt is checked and gives the shop
# ----------------------------------------------------------------------------


def _note(grn_id=None, bill_id=None, amount=300.0, **extra) -> dict:
    return {"amount": amount, "date": "2026-09-20", "reason": "rejected goods", "grn_id": grn_id, "bill_id": bill_id, **extra}


def _pay_b_d(world):
    return world.post(f"/vendors/{V_BOTH}/payments", ADMIN_HQ, {
        "amount": 100.0, "payment_date": "2026-09-25", "bill_id": "B-D", "mode": "BANK",
    })


def test_r3_2_a_pune_accountants_note_on_dhanbads_receipt_is_404_and_the_hold_stays(world):
    held = _pay_b_d(world)
    assert held.status_code == 409 and held.json()["detail"]["code"] == "REJECTED_GOODS_NO_DEBIT_NOTE", held.text
    before = world.snapshot()
    dhn_before = _ok(world.get(f"/vendors/{V_BOTH}/ledger", ACCT_DHN))["ledger"]["closing_balance"]

    resp = world.post(f"/vendors/{V_BOTH}/debit-notes", ACCT_PUNE, _note("GRN-D"))
    missing = world.post(f"/vendors/{V_BOTH}/debit-notes", ACCT_PUNE, _note("NO-SUCH-GRN"))

    assert resp.status_code == 404, f"#2/#7: a Pune note on Dhanbad's receipt was accepted: {resp.text}"
    assert missing.status_code == 404, f"#7: a note on a receipt that does not exist was accepted: {missing.text}"
    # The same answer a missing receipt gets: nothing says Dhanbad's exists.
    assert resp.json()["detail"] == missing.json()["detail"].replace("NO-SUCH-GRN", "GRN-D")
    assert DHN not in resp.text
    assert world.snapshot() == before, "a refused debit note wrote"

    # Dhanbad's hold stays, and Dhanbad's figures are untouched.
    still = _pay_b_d(world)
    assert still.status_code == 409 and still.json()["detail"]["code"] == "REJECTED_GOODS_NO_DEBIT_NOTE", still.text
    assert _ok(world.get(f"/vendors/{V_BOTH}/ledger", ACCT_DHN))["ledger"]["closing_balance"] == dhn_before


def test_r3_7_another_suppliers_receipt_is_the_same_404(world):
    """GRN-X is in Pune's reach but is V-OTHER's: a V-BOTH note naming it
    would release V-OTHER's hold with V-BOTH's credit."""
    before = world.snapshot()
    resp = world.post(f"/vendors/{V_BOTH}/debit-notes", ACCT_PUNE, _note("GRN-X"))
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"] == "GRN GRN-X not found"
    admin = world.post(f"/vendors/{V_BOTH}/debit-notes", ADMIN_HQ, _note("GRN-X"))
    assert admin.status_code == 404, admin.text
    assert world.snapshot() == before


def test_r3_7_a_note_on_the_callers_own_receipt_takes_that_receipts_shop(world):
    pun_before = _ok(world.get(f"/vendors/{V_BOTH}/ledger", ACCT_PUNE))["ledger"]["closing_balance"]
    mine = _ok(world.post(f"/vendors/{V_BOTH}/debit-notes", ACCT_PUNE, _note("GRN-P", amount=50.0)), 201)
    assert mine["store_id"] == PUN
    assert world.db["vendor_debit_notes"].find_one({"debit_note_id": mine["debit_note_id"]})["store_id"] == PUN
    assert _ok(world.get(f"/vendors/{V_BOTH}/ledger", ACCT_PUNE))["ledger"]["closing_balance"] == pytest.approx(pun_before - 50.0)


def test_r3_7_an_admins_note_on_dhanbads_receipt_is_dhanbads_not_his_topbar_shops(world):
    """The admin sits on HQ; V-BOTH's latest bill on or before 20 Sep is
    Pune's B-P. The note credits Dhanbad's rejected goods, so it is
    Dhanbad's -- it releases Dhanbad's hold and comes off Dhanbad's ledger."""
    dhn_before = _ok(world.get(f"/vendors/{V_BOTH}/ledger", ACCT_DHN))["ledger"]["closing_balance"]
    pun_before = _ok(world.get(f"/vendors/{V_BOTH}/ledger", ACCT_PUNE))["ledger"]["closing_balance"]
    made = _ok(world.post(f"/vendors/{V_BOTH}/debit-notes", ADMIN_HQ, _note("GRN-D")), 201)
    assert made["store_id"] == DHN
    assert world.db["vendor_debit_notes"].find_one({"debit_note_id": made["debit_note_id"]})["store_id"] == DHN
    assert _ok(world.get(f"/vendors/{V_BOTH}/ledger", ACCT_DHN))["ledger"]["closing_balance"] == pytest.approx(dhn_before - 300.0)
    assert _ok(world.get(f"/vendors/{V_BOTH}/ledger", ACCT_PUNE))["ledger"]["closing_balance"] == pytest.approx(pun_before)
    assert _pay_b_d(world).status_code == 201, "the note on Dhanbad's receipt releases Dhanbad's hold"
    # Dhanbad's own accountant may raise it too.
    assert _ok(world.post(f"/vendors/{V_BOTH}/debit-notes", ACCT_DHN, _note("GRN-D", amount=1.0)), 201)["store_id"] == DHN


def test_r3_7_a_receipt_and_a_shop_or_bill_that_disagree_are_refused_not_refiled(world):
    before = world.snapshot()
    other_shop = world.post(f"/vendors/{V_BOTH}/debit-notes", ADMIN_HQ, _note("GRN-D"), store_id=PUN)
    assert other_shop.status_code == 422, other_shop.text
    two_shops = world.post(f"/vendors/{V_BOTH}/debit-notes", ADMIN_HQ, _note("GRN-D", bill_id="B-P"))
    assert two_shops.status_code == 422, two_shops.text
    assert world.snapshot() == before
    # The bill and its own receipt: the bill's shop, as before.
    same = _ok(world.post(f"/vendors/{V_BOTH}/debit-notes", ADMIN_HQ, _note("GRN-D", bill_id="B-D")), 201)
    assert same["store_id"] == DHN
