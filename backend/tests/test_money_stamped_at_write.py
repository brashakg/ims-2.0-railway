"""
IMS 2.0 -- A SUPPLIER PAYMENT / DEBIT NOTE IS STAMPED WITH ITS SHOP WHEN IT
IS WRITTEN, SO A LATER BILL NEVER MOVES IT (review 2026-10-01 round 3: #2, #8, #7)
==================================================================================
Owner rulings: supplier balances are ADMIN / SUPERADMIN / ACCOUNTANT only;
F63 -- ADMIN / SUPERADMIN reach every shop, everyone else only their own.

  #2 / #8  An ADMIN's on-account payment or debit note (no bill, no receipt,
           no shop named -- the Cash Flow form's default) was saved with NO
           shop, so ap_engine.supplier_rows re-guessed its shop on every read
           from the supplier's latest bill on or before it. When another shop
           later booked a back-dated bill, the payment MOVED there: a closed
           month's per-shop paid / owed changed, and the first shop owed a
           paid bill again (and could pay it twice). Now the same rule is
           worked out at WRITE time and stamped; the response carries it. A
           supplier that has never billed a shop: still unstamped (all stores
           only). A shop the admin names still wins; a non-admin's money is
           still his own shop's.
  #7       An admin's debit note naming a legacy goods receipt with NO shop,
           plus a shop, was refused with a 422 saying the receipt 'is booked
           to another shop's account'. A receipt with no shop on record now
           takes the shop asked for (validated); with none asked, the
           supplier's shop by its bills (as on-account money). The 422s that
           remain say what is true.

Today is pinned to 1 Oct 2026 (IST) for every figure.

Run: JWT_SECRET_KEY=test ENVIRONMENT=test python -m pytest
     backend/tests/test_money_stamped_at_write.py -q
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
V_BOTH = "V-BOTH"  # bills Dhanbad (5 Sep) and Pune (20 Sep)
V_X = "V-X"  # has only ever billed Pune
V_NEW = "V-NEW"  # has never billed
V_MIRROR = "V-MIRROR"  # Pune bill + a Dhanbad transfer-mirror bill
V_LEG = "V-LEG"  # its latest bill has no shop
TODAY = "2026-10-01"


def _user(role: str, store, stores=()) -> dict:
    return {
        "user_id": f"u-{role.lower()}-{store or 'all'}",
        "username": role.lower(),
        "roles": [role],
        "store_ids": list(stores),
        "active_store_id": store,
    }


ADMIN_DHN = _user("ADMIN", DHN)  # the admin's topbar shop is Dhanbad
SUPER_NONE = _user("SUPERADMIN", None)
ACCT_NONE = _user("ACCOUNTANT", None)  # a login with no shop
ACCT_PUNE = _user("ACCOUNTANT", PUN, [PUN])
ACCT_DHN = _user("ACCOUNTANT", DHN, [DHN])

_KINDS = {
    "payments": ("vendor_payments", "payment_id"),
    "debit-notes": ("vendor_debit_notes", "debit_note_id"),
}


@pytest.fixture(autouse=True)
def _today(monkeypatch):
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


def _seed(db) -> None:
    # The company master the bill doors read for the buyer's GSTIN (#1167): each
    # shop belongs to a company with a registration in its state.
    db["entities"].insert_many([
        {"entity_id": "E-BV", "name": "Better Vision",
         "gstins": [{"gstin": "20AAAAA0000A1Z5", "state_code": "20", "is_primary": True}]},
        {"entity_id": "E-WO", "name": "WizOpt",
         "gstins": [{"gstin": "27BBBBB1111B1Z5", "state_code": "27", "is_primary": True}]},
    ])
    db["stores"].insert_many([
        {"store_id": DHN, "entity_id": "E-BV", "state_code": "20", "gstin": "20AAAAA0000A1Z5"},
        {"store_id": PUN, "entity_id": "E-WO", "state_code": "27", "gstin": "27BBBBB1111B1Z5"},
    ])
    db["vendors"].insert_many([
        {"vendor_id": v, "legal_name": v, "trade_name": v, "is_active": True, "credit_days": 30}
        for v in (V_BOTH, V_X, V_NEW, V_MIRROR, V_LEG)
    ])
    db["vendor_bills"].insert_many([
        _bill("BD", V_BOTH, DHN, "2026-09-05", 2000.0, "2026-10-05"),
        _bill("BP", V_BOTH, PUN, "2026-09-20", 3000.0, "2026-10-20"),
        _bill("BX-P", V_X, PUN, "2026-09-07", 5000.0, "2026-10-07"),
        _bill("BM-P", V_MIRROR, PUN, "2026-09-01", 400.0, "2026-10-01"),
        _bill("BM-D", V_MIRROR, DHN, "2026-09-15", 400.0, "2026-10-15", source_transfer_id="TR-1"),
        _bill("BL-P", V_LEG, PUN, "2026-09-01", 100.0, "2026-10-01"),
        _bill("BL-0", V_LEG, None, "2026-09-10", 100.0, "2026-10-10"),
    ])
    accepted = {"status": "ACCEPTED", "vendor_id": V_BOTH, "items": [{"product_id": "P1", "accepted_qty": 1}]}
    db["grns"].insert_many([
        {"grn_id": "GRN-NOSHOP", "grn_number": "GRN-NOSHOP", **accepted},  # legacy: no store_id
        {"grn_id": "GRN-D", "grn_number": "GRN-D", "store_id": DHN, **accepted},
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

    def snapshot(self) -> tuple:
        return tuple(
            sorted(self.db[c].find({}, {"_id": 0}), key=lambda d: str(sorted(d.items())))
            for c in ("vendor_payments", "vendor_debit_notes", "vendor_bills", "grns")
        )


@pytest.fixture
def world(_client, monkeypatch):
    from database.repositories.vendor_repository import GRNRepository, VendorRepository

    db_name = f"ims_test_money_stamp_{uuid.uuid4().hex[:8]}"
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


def _money(kind: str, amount: float, on: str, **extra) -> dict:
    if kind == "payments":
        return {"amount": amount, "payment_date": on, "mode": "BANK", **extra}
    return {"amount": amount, "date": on, "reason": "short supply", **extra}


def _stored(world, kind: str, made: dict) -> dict:
    coll, key = _KINDS[kind]
    return world.db[coll].find_one({key: made[key]}, {"_id": 0})


def _shop_figures(world, vendor: str, shop: str) -> dict:
    """One shop's September figures for `vendor` on every screen an admin
    reads with that shop picked: the Purchases report row, the vendor ledger
    and the Suppliers card (/finance/vendor-payments)."""
    report = _ok(world.get("/vendors/purchases-this-month", ADMIN_DHN, month="2026-09", store_id=shop))
    row = next((v for v in report["vendors"] if v["vendor_id"] == vendor), {})
    ledger = _ok(world.get(f"/vendors/{vendor}/ledger", ADMIN_DHN, store_id=shop))["ledger"]
    card = {r["vendor_id"]: r for r in _ok(world.get("/finance/vendor-payments", ADMIN_DHN, store_id=shop))}
    return {
        "report_billed": round(row.get("billed", 0.0), 2),
        "report_paid": round(row.get("paid", 0.0), 2),
        "report_owed": round(row.get("owed", 0.0), 2),
        "ledger": round(ledger["closing_balance"], 2),
        "card": round(card[vendor]["balance"], 2) if vendor in card else 0.0,
    }


def _services_bill(world, user, vendor: str, number: str, on: str, total: float) -> dict:
    """A bill booked the way the shop's accountant books one (Cash Flow
    'Bill' form, services/expenses): stamped with his own shop."""
    return _ok(world.post(f"/vendors/{vendor}/bills", user, {
        "bill_number": number,
        "bill_date": on,
        "taxable_amount": total,
        "tax_amount": 0.0,
        "total_amount": total,
        "bill_kind": "SERVICES",
    }), 201)


# ----------------------------------------------------------------------------
# #2 / #8 -- stamped when written; a later back-dated bill never moves it
# ----------------------------------------------------------------------------


@pytest.mark.parametrize("admin", [ADMIN_DHN, SUPER_NONE], ids=["admin", "superadmin-no-shop"])
@pytest.mark.parametrize("kind", list(_KINDS))
def test_r3_2_the_reviewers_sequence_the_money_stays_in_pune(world, kind, admin):
    """V-BOTH: Dhanbad bill BD (5 Sep, 2,000), Pune bill BP (20 Sep, 3,000).
    The admin records 3,000 on 25 Sep with no bill and the Shop left on its
    default. On 1 Oct Dhanbad's accountant books BD2 dated 22 Sep -- before
    the money. The money stays Pune's; Pune's September is untouched and
    Dhanbad's only grows by BD2 itself."""
    made = _ok(world.post(f"/vendors/{V_BOTH}/{kind}", admin, _money(kind, 3000.0, "2026-09-25")), 201)

    pune_before = _shop_figures(world, V_BOTH, PUN)
    dhn_before = _shop_figures(world, V_BOTH, DHN)
    assert pune_before["ledger"] == 0.0 and pune_before["report_owed"] == 0.0 and pune_before["card"] == 0.0
    assert dhn_before["ledger"] == 2000.0 and dhn_before["report_owed"] == 2000.0

    bd2 = _services_bill(world, ACCT_DHN, V_BOTH, "BD2", "2026-09-22", 1500.0)
    assert bd2["store_id"] == DHN

    # The figures first: the money must not have moved.
    pune_after = _shop_figures(world, V_BOTH, PUN)
    assert pune_after == pune_before, (
        f"#2: Pune's closed September moved when Dhanbad back-dated a bill: {pune_before} -> {pune_after}"
    )
    dhn_after = _shop_figures(world, V_BOTH, DHN)
    assert dhn_after["report_paid"] == dhn_before["report_paid"], "#2: the money moved into Dhanbad"
    assert dhn_after["ledger"] == pytest.approx(dhn_before["ledger"] + 1500.0)
    assert dhn_after["report_owed"] == pytest.approx(dhn_before["report_owed"] + 1500.0)
    # Every shop together: 2,000 + 3,000 + 1,500 billed, 3,000 settled.
    assert _ok(world.get(f"/vendors/{V_BOTH}/ledger", ADMIN_DHN))["ledger"]["closing_balance"] == pytest.approx(3500.0)

    # Why: the shop was stamped when the money was written, and the response
    # names it (the Cash Flow toast can say where it went).
    assert made["store_id"] == PUN, f"#2: the response does not name the shop: {made['store_id']}"
    assert _stored(world, kind, made)["store_id"] == PUN, "#2: the money was saved with no shop"


def test_r3_8_a_pune_only_supplier_paid_by_an_admin_on_dhanbad_stays_settled_in_pune(world):
    """#8 as written: V-X has billed only Pune (BX-P, 7 Sep, 5,000). The admin
    (topbar Dhanbad) pays 5,000 on 20 Sep from the Cash Flow default. Then
    Dhanbad's accountant books a 3,000 bill dated 10 Sep. Pune must still owe
    nothing, and Dhanbad owes its 3,000 -- not -2,000."""
    made = _ok(world.post(f"/vendors/{V_X}/payments", ADMIN_DHN, _money("payments", 5000.0, "2026-09-20")), 201)
    _services_bill(world, ACCT_DHN, V_X, "BX-D", "2026-09-10", 3000.0)

    pune = _shop_figures(world, V_X, PUN)
    assert pune == {"report_billed": 5000.0, "report_paid": 5000.0, "report_owed": 0.0, "ledger": 0.0, "card": 0.0}, pune
    dhn = _shop_figures(world, V_X, DHN)
    assert (dhn["ledger"], dhn["report_paid"], dhn["report_owed"]) == (3000.0, 0.0, 3000.0), dhn
    # The Pune accountant, on his own screens, still owes nothing.
    assert _ok(world.get(f"/vendors/{V_X}/ledger", ACCT_PUNE))["ledger"]["closing_balance"] == 0.0
    assert [p["payment_id"] for p in _ok(world.get(f"/vendors/{V_X}/payments", ACCT_PUNE))["payments"]] == [made["payment_id"]]
    assert _ok(world.get(f"/vendors/{V_X}/payments", ACCT_DHN))["payments"] == []
    assert made["store_id"] == PUN


@pytest.mark.parametrize("kind", list(_KINDS))
def test_r3_2_money_before_any_bill_takes_the_earliest_bills_shop_and_keeps_it(world, kind):
    """An advance dated 1 Sep, before V-BOTH's first bill (Dhanbad, 5 Sep):
    Dhanbad's, by the rule's 'else the earliest bill'. A Pune bill later
    back-dated to 25 Aug would have pulled it to Pune on every read."""
    made = _ok(world.post(f"/vendors/{V_BOTH}/{kind}", ADMIN_DHN, _money(kind, 100.0, "2026-09-01")), 201)
    _, key = _KINDS[kind]
    list_key = "payments" if kind == "payments" else "debit_notes"
    dhn = {r[key] for r in _ok(world.get(f"/vendors/{V_BOTH}/{kind}", ACCT_DHN))[list_key]}
    assert made[key] in dhn
    _services_bill(world, ACCT_PUNE, V_BOTH, "BP-OLD", "2026-08-25", 50.0)
    dhn = {r[key] for r in _ok(world.get(f"/vendors/{V_BOTH}/{kind}", ACCT_DHN))[list_key]}
    pun = {r[key] for r in _ok(world.get(f"/vendors/{V_BOTH}/{kind}", ACCT_PUNE))[list_key]}
    assert made[key] in dhn and made[key] not in pun, "#2: Pune's back-dated bill pulled Dhanbad's advance"
    assert made["store_id"] == DHN
    assert _stored(world, kind, made)["store_id"] == DHN


@pytest.mark.parametrize("kind", list(_KINDS))
def test_r3_2_a_supplier_that_never_billed_stays_unstamped(world, kind):
    made = _ok(world.post(f"/vendors/{V_NEW}/{kind}", ADMIN_DHN, _money(kind, 100.0, "2026-09-20")), 201)
    assert made["store_id"] is None
    assert _stored(world, kind, made)["store_id"] is None


@pytest.mark.parametrize("user", [ADMIN_DHN, SUPER_NONE, ACCT_NONE], ids=["admin", "superadmin", "accountant-no-shop"])
@pytest.mark.parametrize(
    "vendor,on,shop",
    [
        (V_BOTH, "2026-09-25", PUN),  # latest bill on or before: Pune's BP
        (V_BOTH, "2026-09-10", DHN),  # latest bill on or before: Dhanbad's BD
        (V_BOTH, "2026-08-01", DHN),  # before every bill: the earliest, BD
        (V_X, "2026-09-01", PUN),
        (V_MIRROR, "2026-09-20", PUN),  # the Dhanbad transfer mirror is never a supplier bill
        (V_LEG, "2026-09-15", None),  # the bill the rule picks has no shop
        (V_LEG, "2026-09-05", PUN),
    ],
)
def test_r3_2_the_stamp_is_the_ledgers_own_rule_at_write_time(world, user, vendor, on, shop):
    """The shop stamped is exactly where supplier_rows would have placed the
    same row unstamped at the moment it was written -- so no figure changes
    on the day it is recorded, only later bills can no longer move it. A
    non-admin with no shop (resolve_store_scope gives none) is placed the
    same way."""
    made = _ok(world.post(f"/vendors/{vendor}/payments", user, _money("payments", 10.0, on)), 201)
    assert made["store_id"] == shop
    stored = _stored(world, "payments", made)
    assert stored["store_id"] == shop
    bills = list(world.db["vendor_bills"].find({"vendor_id": vendor}, {"_id": 0}))
    unstamped = dict(stored, store_id=None)
    placed = [s for s in (DHN, PUN) if ap_engine.supplier_rows(bills, [unstamped], [], s)[1]]
    assert placed == ([shop] if shop else [])


def test_r3_2_a_non_admin_and_a_named_shop_are_unchanged(world):
    """The Pune accountant's money is Pune's even where the rule would say
    Dhanbad (10 Sep); an admin naming Dhanbad gets Dhanbad where the rule
    would say Pune (25 Sep)."""
    mine = _ok(world.post(f"/vendors/{V_BOTH}/payments", ACCT_PUNE, _money("payments", 10.0, "2026-09-10")), 201)
    named = _ok(world.post(f"/vendors/{V_BOTH}/payments", ADMIN_DHN, _money("payments", 10.0, "2026-09-25", store_id=DHN)), 201)
    assert (mine["store_id"], named["store_id"]) == (PUN, DHN)


# ----------------------------------------------------------------------------
# #7 -- a receipt with no shop on record takes the shop asked for
# ----------------------------------------------------------------------------


def _note(grn_id=None, bill_id=None, amount=100.0, on="2026-09-25", **extra) -> dict:
    return {"amount": amount, "date": on, "reason": "rejected", "grn_id": grn_id, "bill_id": bill_id, **extra}


@pytest.mark.parametrize("how", ["query", "body"])
def test_r3_7_an_admins_note_on_a_receipt_with_no_shop_takes_the_shop_asked_for(world, how):
    pune_before = _ok(world.get(f"/vendors/{V_BOTH}/ledger", ACCT_PUNE))["ledger"]["closing_balance"]
    dhn_before = _ok(world.get(f"/vendors/{V_BOTH}/ledger", ACCT_DHN))["ledger"]["closing_balance"]
    if how == "query":
        resp = world.post(f"/vendors/{V_BOTH}/debit-notes", ADMIN_DHN, _note("GRN-NOSHOP"), store_id=PUN)
    else:
        resp = world.post(f"/vendors/{V_BOTH}/debit-notes", ADMIN_DHN, _note("GRN-NOSHOP", store_id=PUN))
    made = _ok(resp, 201)
    assert made["store_id"] == PUN, f"#7: {made}"
    stored = world.db["vendor_debit_notes"].find_one({"debit_note_id": made["debit_note_id"]}, {"_id": 0})
    assert (stored["store_id"], stored["grn_id"]) == (PUN, "GRN-NOSHOP")
    assert _ok(world.get(f"/vendors/{V_BOTH}/ledger", ACCT_PUNE))["ledger"]["closing_balance"] == pytest.approx(pune_before - 100.0)
    assert _ok(world.get(f"/vendors/{V_BOTH}/ledger", ACCT_DHN))["ledger"]["closing_balance"] == pytest.approx(dhn_before)


def test_r3_7_with_no_shop_asked_it_is_the_suppliers_shop_by_its_bills(world):
    """No shop on the receipt and none asked: on-account money's rule --
    V-BOTH's latest bill on or before 10 Sep is Dhanbad's -- stamped, never
    the admin's topbar shop and never left to move."""
    made = _ok(world.post(f"/vendors/{V_BOTH}/debit-notes", SUPER_NONE, _note("GRN-NOSHOP", on="2026-09-10")), 201)
    assert made["store_id"] == DHN
    assert world.db["vendor_debit_notes"].find_one({"debit_note_id": made["debit_note_id"]})["store_id"] == DHN


def test_r3_7_a_non_admin_still_cannot_reach_a_receipt_with_no_shop(world):
    before = world.snapshot()
    for params, body in (({}, _note("GRN-NOSHOP")), ({"store_id": PUN}, _note("GRN-NOSHOP"))):
        resp = world.post(f"/vendors/{V_BOTH}/debit-notes", ACCT_PUNE, body, **params)
        assert resp.status_code == 404, resp.text
        assert resp.json()["detail"] == "GRN GRN-NOSHOP not found"
    assert world.snapshot() == before


def test_r3_7_a_bill_of_a_shop_still_wins_over_a_receipt_with_none(world):
    made = _ok(world.post(f"/vendors/{V_BOTH}/debit-notes", ADMIN_DHN, _note("GRN-NOSHOP", bill_id="BP")), 201)
    assert made["store_id"] == PUN
    clash = world.post(f"/vendors/{V_BOTH}/debit-notes", ADMIN_DHN, _note("GRN-NOSHOP", bill_id="BP"), store_id=DHN)
    assert clash.status_code == 422, clash.text
    assert "bill is booked to another shop" in clash.json()["detail"]


def test_r3_7_every_remaining_422_says_what_is_true(world):
    before = world.snapshot()
    # A receipt booked to Dhanbad, Pune asked for: it IS another shop's.
    other = world.post(f"/vendors/{V_BOTH}/debit-notes", ADMIN_DHN, _note("GRN-D"), store_id=PUN)
    assert other.status_code == 422, other.text
    assert other.json()["detail"].startswith("This goods receipt is booked to another shop's account")
    # A bill booked to Pune, Dhanbad asked for: it IS another shop's.
    bill_other = world.post(f"/vendors/{V_BOTH}/payments", ADMIN_DHN, _money("payments", 5.0, "2026-09-25", bill_id="BP"), store_id=DHN)
    assert bill_other.status_code == 422, bill_other.text
    assert bill_other.json()["detail"].startswith("This bill is booked to another shop's account")
    # A bill with NO shop, Pune asked for: not 'another shop' -- no shop at all.
    for kind in _KINDS:
        no_shop = world.post(f"/vendors/{V_LEG}/{kind}", ADMIN_DHN, _money(kind, 5.0, "2026-09-25", bill_id="BL-0"), store_id=PUN)
        assert no_shop.status_code == 422, no_shop.text
        detail = no_shop.json()["detail"]
        assert "no shop on record" in detail and "another shop" not in detail, detail
    assert world.snapshot() == before
    # Left out, the money stays with the shop-less bill, as before.
    made = _ok(world.post(f"/vendors/{V_LEG}/payments", ADMIN_DHN, _money("payments", 5.0, "2026-09-25", bill_id="BL-0")), 201)
    assert made["store_id"] is None
