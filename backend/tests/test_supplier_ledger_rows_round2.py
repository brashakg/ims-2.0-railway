"""
IMS 2.0 -- THE SUPPLIER LEDGER'S ROWS, ROUND 2 (review 2026-10-01: #1 #5 #13 #14 #15)
===================================================================================
Owner ruling 2026-10-01: supplier balances are ADMIN / SUPERADMIN / ACCOUNTANT
only. F63: ADMIN / SUPERADMIN see every shop; every other role its own shop
only, even when the request is edited -- another shop's object answers the
same 404 a missing one does.

The one row rule is ap_engine.supplier_rows / supplier_ledger_rows. Pinned:

  #5 / #14  A row's shop never depends on the as-of day. The transfer-mirror
            set, each bill's shop and the supplier's billing history are built
            from ALL bills; only then is each row cut by its own date. A
            payment naming a bill keyed ahead is that bill's shop's money.
  #13       Money carries its own shop. POST /vendors/{id}/payments and
            /debit-notes stamp store_id: the named bill's shop; else the shop
            asked for (?store_id or body), which a non-admin may only name as
            his own; else a non-admin's active shop. An admin's money naming
            no bill and no shop takes the latest-bill rule's shop, worked out
            and stamped when it is written (round 3 review #2 / #8), so a
            later bill can never move it -- read-time, that rule is only for
            legacy rows with no stamp.
  #1        The as-of cutoff is for FIGURES, not for hiding rows. The payments
            and debit-notes lists show every recorded row (post-dated ones
            flagged); the ledger strikes its balance on today and lists the
            later rows apart (`post_dated`); the bills list owes what the
            ledger owes today, with the post-dated money noted.
  #15       POST /vendors/{id}/bills on another shop's goods receipt: the same
            404 a missing receipt gets, before anything is written; and the
            409 'already billed' never names a bill outside the caller's shop.

Today is pinned to 1 Oct 2026 (IST) for every figure.

Run: JWT_SECRET_KEY=test ENVIRONMENT=test python -m pytest
     backend/tests/test_supplier_ledger_rows_round2.py -q
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
ONLINE = "BV-ONLINE-01"
V_BOTH = "V-BOTH"
V_PDC = "V-PDC"
TODAY = "2026-10-01"


def _user(role: str, store, stores=()) -> dict:
    return {
        "user_id": f"u-{role.lower()}-{store or 'all'}",
        "username": role.lower(),
        "roles": [role],
        "store_ids": list(stores),
        "active_store_id": store,
    }


ADMIN = _user("ADMIN", ONLINE)  # a first-time admin lands on the ONLINE store
SUPERADMIN = _user("SUPERADMIN", None)
ACCT_PUNE = _user("ACCOUNTANT", PUN, [PUN])
ACCT_DHN = _user("ACCOUNTANT", DHN, [DHN])


@pytest.fixture(autouse=True)
def _today(monkeypatch):
    """Every payable figure is struck on 1 Oct 2026 (IST)."""
    monkeypatch.setattr(ap_engine, "now_ist_naive", lambda: datetime.fromisoformat(TODAY + "T12:00:00"))


def _bill(bill_id, vendor, store, on, total, **extra) -> dict:
    doc = {
        "bill_id": bill_id,
        "vendor_id": vendor,
        "bill_number": bill_id,
        "bill_date": on,
        "due_date": "2026-10-30",
        "total_amount": total,
        "outstanding": total,
        "status": "OUTSTANDING",
        **extra,
    }
    if store is not None:
        doc["store_id"] = store
    return doc


def _pay(pid, vendor, bill_id, amount, on, **extra) -> dict:
    return {
        "payment_id": pid,
        "vendor_id": vendor,
        "bill_id": bill_id,
        "amount": amount,
        "tds_amount": 0.0,
        "mode": "BANK",
        "payment_date": on,
        **extra,
    }


def _ids(rows, key) -> list:
    return sorted(r[key] for r in rows)


# ============================================================================
# Engine: #5 / #14 -- a row's shop is decided from ALL bills, then the cutoff
# ============================================================================


def test_r2_5_a_payment_naming_a_bill_keyed_ahead_is_that_bills_shop_in_every_month():
    """Review #5: Dhanbad bill D1 (5 Sep), Pune bill P1 (2 Oct, a proforma),
    and an advance of 3000 on 26 Sep naming P1. September's report put the
    3000 in DHANBAD (P1 was cut before its shop was known, so the payment fell
    back to the supplier's latest earlier bill, D1) -- for ever, since P1 is
    always after September's as-of day."""
    bills = [_bill("D1", "V1", DHN, "2026-09-05", 2000.0), _bill("P1", "V1", PUN, "2026-10-02", 3000.0)]
    pays = [_pay("ADV", "V1", "P1", 3000.0, "2026-09-26")]
    for as_of in ("2026-09-30", TODAY):
        dhn = ap_engine.supplier_ledger_rows(bills, pays, [], DHN, as_of)
        pun = ap_engine.supplier_ledger_rows(bills, pays, [], PUN, as_of)
        assert dhn[1] == [], f"#5: Dhanbad's share as of {as_of} carries Pune's advance"
        assert _ids(pun[1], "payment_id") == ["ADV"], as_of
        assert pun[0] == [], "P1 is dated 2 Oct: not yet counted"
        # The shops still add up to the supplier ledger.
        whole = ap_engine.supplier_ledger_rows(bills, pays, [], None, as_of)
        assert len(whole[1]) == len(dhn[1]) + len(pun[1])
    led = ap_engine.build_ledger(*ap_engine.supplier_ledger_rows(bills, pays, [], DHN, "2026-09-30"))
    assert led["closing_balance"] == 2000.0, "Dhanbad owes its own bill, untouched"


def test_r2_5_a_legacy_advance_before_any_bill_keeps_one_shop_on_every_as_of_day():
    """The billing history used for unstamped money is ALL the supplier's
    bills: an advance paid on 1 Sep to a supplier whose only bill (Pune) is
    dated 5 Oct is Pune's on 30 Sep as on 5 Oct -- it used to have no shop at
    all in September (the bill was cut first) and then move to Pune."""
    bills = [_bill("P-ONLY", "V9", PUN, "2026-10-05", 900.0)]
    pays = [_pay("EARLY", "V9", None, 400.0, "2026-09-01")]
    for as_of in ("2026-09-30", TODAY):
        assert _ids(ap_engine.supplier_ledger_rows(bills, pays, [], PUN, as_of)[1], "payment_id") == ["EARLY"], as_of
        assert ap_engine.supplier_ledger_rows(bills, pays, [], DHN, as_of)[1] == [], as_of


def test_r2_14_a_payment_on_todays_date_naming_a_future_bill_stays_in_the_bills_shop():
    """Review #14: Pune bill B-P2 (28 Sep), Dhanbad bill B-FUT keyed ahead
    (5 Oct); PAY-FUT names B-FUT, dated today. It was Pune's (B-FUT cut, so
    'latest bill on or before' = B-P2): a Pune accountant saw Dhanbad's
    payment and owed 500.01 less than he does."""
    bills = [_bill("B-P2", V_BOTH, PUN, "2026-09-28", 100.0), _bill("B-FUT", V_BOTH, DHN, "2026-10-05", 5000.0)]
    pays = [_pay("PAY-FUT", V_BOTH, "B-FUT", 500.01, TODAY)]
    pun = ap_engine.supplier_ledger_rows(bills, pays, [], PUN)
    dhn = ap_engine.supplier_ledger_rows(bills, pays, [], DHN)
    assert pun[1] == [], "#14: Dhanbad's payment in Pune's share"
    assert _ids(dhn[1], "payment_id") == ["PAY-FUT"]
    assert ap_engine.build_ledger(*pun)["closing_balance"] == 100.0
    assert ap_engine.build_ledger(*dhn)["closing_balance"] == -500.01  # paid ahead of its bill


def test_r2_14_money_naming_a_future_transfer_mirror_bill_is_never_a_supplier_payment():
    """The mirror set too is built from ALL bills: a mirror bill keyed ahead
    still marks the money naming it as an inter-company move, not money paid
    to a supplier (it used to be kept, and filed under a guessed shop)."""
    bills = [
        _bill("EXT", "V1", DHN, "2026-09-01", 1000.0),
        _bill("MIRROR", "ENT-A", PUN, "2026-10-09", 700.0, source_transfer_id="T1"),
    ]
    pays = [_pay("TRF", "ENT-A", "MIRROR", 700.0, "2026-09-20")]
    for shop in (None, DHN, PUN):
        assert ap_engine.supplier_ledger_rows(bills, pays, [], shop)[1] == [], shop


def test_r2_14_a_mirror_bill_with_no_id_does_not_swallow_on_account_money():
    bills = [_bill("EXT", "V1", DHN, "2026-09-01", 1000.0), {"vendor_id": "ENT", "source_transfer_id": "T9", "total_amount": 5}]
    pays = [_pay("ADV", "V1", None, 100.0, "2026-09-20")]
    assert _ids(ap_engine.supplier_ledger_rows(bills, pays, [])[1], "payment_id") == ["ADV"]
    assert [b["bill_id"] for b in ap_engine.supplier_ledger_rows(bills, pays, [])[0]] == ["EXT"]


# ============================================================================
# Engine: #13 -- stamped money goes to its own shop; the guess is legacy-only
# ============================================================================


def test_r2_13_stamped_on_account_money_is_its_own_shops_not_the_latest_bills():
    """Review #13: V-BOTH's latest bill by 30 Sep is Dhanbad's, so Pune's
    on-account 777.77 landed in Dhanbad's ledger. Stamped, it is Pune's. A
    legacy row (no stamp) keeps the latest-bill rule."""
    bills = [_bill("B-P1", V_BOTH, PUN, "2026-09-05", 600.0), _bill("B-HELD-D", V_BOTH, DHN, "2026-09-08", 1000.0)]
    pays = [
        _pay("PUNE-OA", V_BOTH, None, 777.77, "2026-09-30", store_id=PUN),
        _pay("LEGACY-OA", V_BOTH, None, 50.0, "2026-09-30"),
    ]
    notes = [{"debit_note_id": "DN-OA", "vendor_id": V_BOTH, "bill_id": None, "amount": 30.0, "date": "2026-09-30", "store_id": PUN}]
    pun = ap_engine.supplier_ledger_rows(bills, pays, notes, PUN)
    dhn = ap_engine.supplier_ledger_rows(bills, pays, notes, DHN)
    assert _ids(pun[1], "payment_id") == ["PUNE-OA"]
    assert _ids(pun[2], "debit_note_id") == ["DN-OA"]
    assert _ids(dhn[1], "payment_id") == ["LEGACY-OA"]
    assert dhn[2] == []


def test_r2_13_money_naming_a_bill_follows_the_bill_even_if_stamped_otherwise():
    """A payment settles its bill: bill and money always sit in the same
    shop's share, so a shop's per-bill outstanding can never go negative
    because the bill was re-filed (a backfill) after the money was stamped."""
    bills = [_bill("B-D", V_BOTH, DHN, "2026-09-05", 600.0)]
    pays = [_pay("P", V_BOTH, "B-D", 600.0, "2026-09-06", store_id=PUN)]
    assert _ids(ap_engine.supplier_ledger_rows(bills, pays, [], DHN)[1], "payment_id") == ["P"]
    assert ap_engine.supplier_ledger_rows(bills, pays, [], PUN)[1] == []


# ============================================================================
# Engine: #1 -- one bill on the ledger's as-of rule
# ============================================================================


def test_r2_1_bill_as_of_owes_today_and_notes_the_post_dated_money():
    bill = _bill("PD1", V_PDC, PUN, "2026-09-20", 5000.0)
    pays = [_pay("CHQ", V_PDC, "PD1", 4000.0, "2026-11-15"), _pay("CASH", V_PDC, "PD1", 700.0, "2026-09-25")]
    notes = [{"debit_note_id": "DN", "vendor_id": V_PDC, "bill_id": "PD1", "amount": 300.0, "date": "2026-10-20"}]
    got = ap_engine.bill_as_of(bill, pays, notes)
    assert got == {
        "outstanding": 4300.0,
        "post_dated_money": 4300.0,
        "post_dated_until": "2026-11-15",
        "post_dated": False,
    }
    # A later as-of day is clamped to today: the cheque is not counted early.
    assert ap_engine.bill_as_of(bill, pays, notes, "2026-11-20")["outstanding"] == 4300.0
    ahead = ap_engine.bill_as_of(_bill("FUT", V_PDC, PUN, "2026-10-05", 10.0), [], [])
    assert ahead["post_dated"] is True and ahead["post_dated_until"] is None


# ============================================================================
# The REAL vendors + finance routers
# ============================================================================


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


def _seed(db) -> None:
    """Two Shop Optics (V-BOTH) supplies Dhanbad and Pune:
         B-P1      Pune bill      5 Sep   600
         B-HELD-D  Dhanbad bill   8 Sep  1000   (the latest bill by 30 Sep)
       Cheque Lens Co (V-PDC), Pune only: PD1 20 Sep 5000.
       Receipts: GRN-D1 (Dhanbad, unbilled), DC-D1 (a Dhanbad Delivery
       Challan, unbilled), GRN-P1 (Pune, billed by the shop-less legacy bill
       LEG-1), GRN-P2 (Pune, billed by Pune's own bill B-P2-OWN), GRN-P3
       (Pune, unbilled)."""
    # #1167: every bill door books on the receiving shop's own registration
    # (org_validation.shop_gstin) and refuses a bill with no company master.
    db["entities"].insert_one({
        "entity_id": "E1", "legal_name": "Better Vision Opticals Pvt Ltd",
        "gstins": [{"gstin": "20ZZZZZ9999Z1Z9", "state_code": "20", "is_primary": True},
                   {"gstin": "27ZZZZZ9999Z1Z9", "state_code": "27"}],
    })
    db["stores"].insert_many([
        {"store_id": DHN, "entity_id": "E1", "state_code": "20", "gstin": "20ZZZZZ9999Z1Z9"},
        {"store_id": PUN, "entity_id": "E1", "state_code": "27", "gstin": "27ZZZZZ9999Z1Z9"},
    ])
    db["vendors"].insert_many([
        {"vendor_id": V_BOTH, "legal_name": "Two Shop Optics", "trade_name": "Two Shop Optics", "is_active": True, "credit_days": 30},
        {"vendor_id": V_PDC, "legal_name": "Cheque Lens Co", "trade_name": "Cheque Lens Co", "is_active": True, "credit_days": 30},
    ])
    db["vendor_bills"].insert_many([
        _bill("B-P1", V_BOTH, PUN, "2026-09-05", 600.0),
        _bill("B-HELD-D", V_BOTH, DHN, "2026-09-08", 1000.0),
        _bill("PD1", V_PDC, PUN, "2026-09-20", 5000.0),
        _bill("LEG-1", V_BOTH, None, "2026-09-02", 50.0, grn_id="GRN-P1"),
        _bill("B-P2-OWN", V_BOTH, PUN, "2026-09-03", 60.0, grn_id="GRN-P2"),
    ])
    accepted = {"status": "ACCEPTED", "vendor_id": V_BOTH, "items": [{"product_id": "P1", "accepted_qty": 1}]}
    db["grns"].insert_many([
        {"grn_id": "GRN-D1", "grn_number": "GRN-D1", "store_id": DHN, **accepted},
        {"grn_id": "DC-D1", "grn_number": "DC-D1", "store_id": DHN, "grn_subtype": "DELIVERY_CHALLAN", "dc_matched": False, **accepted},
        {"grn_id": "GRN-P1", "grn_number": "GRN-P1", "store_id": PUN, **accepted},
        {"grn_id": "GRN-P2", "grn_number": "GRN-P2", "store_id": PUN, **accepted},
        {"grn_id": "GRN-P3", "grn_number": "GRN-P3", "store_id": PUN, **accepted},
    ])


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

    db_name = f"ims_test_ledger_r2_{uuid.uuid4().hex[:8]}"
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


def _closing(world, user, vendor=V_BOTH, **params) -> float:
    return _ok(world.get(f"/vendors/{vendor}/ledger", user, **params))["ledger"]["closing_balance"]


# ----------------------------------------------------------------------------
# #13 -- money carries its own shop
# ----------------------------------------------------------------------------

_KINDS = {
    "payments": ("vendor_payments", "payment_id", "payments"),
    "debit-notes": ("vendor_debit_notes", "debit_note_id", "debit_notes"),
}


def _money(kind: str, amount: float, bill_id=None, on="2026-09-30", **extra) -> dict:
    if kind == "payments":
        return {"amount": amount, "payment_date": on, "mode": "BANK", "bill_id": bill_id, **extra}
    return {"amount": amount, "date": on, "reason": "short supply", "bill_id": bill_id, **extra}


@pytest.mark.parametrize("kind", list(_KINDS))
def test_r2_13_a_pune_accountants_on_account_money_is_punes(world, kind):
    """Review #13, the probe as written: 201, then the Pune accountant could
    not see what he recorded, and Dhanbad's 'we owe' dropped by 777.77."""
    coll, id_key, list_key = _KINDS[kind]
    dhn_before = _closing(world, ADMIN, store_id=DHN)
    pun_before = _closing(world, ACCT_PUNE)
    made = _ok(world.post(f"/vendors/{V_BOTH}/{kind}", ACCT_PUNE, _money(kind, 777.77)), 201)
    assert made["store_id"] == PUN
    stored = world.db[coll].find_one({id_key: made[id_key]}, {"_id": 0})
    assert stored["store_id"] == PUN, "#13: the money was not stamped with the shop that recorded it"

    mine = _ok(world.get(f"/vendors/{V_BOTH}/{kind}", ACCT_PUNE))[list_key]
    assert made[id_key] in {r[id_key] for r in mine}, "#13: the Pune accountant cannot see what he recorded"
    dhanbad = _ok(world.get(f"/vendors/{V_BOTH}/{kind}", ADMIN, store_id=DHN))[list_key]
    assert made[id_key] not in {r[id_key] for r in dhanbad}, "#13: Pune's money in Dhanbad's list"
    assert _closing(world, ADMIN, store_id=DHN) == pytest.approx(dhn_before)
    assert _closing(world, ACCT_PUNE) == pytest.approx(pun_before - 777.77)
    assert _closing(world, ACCT_DHN) == pytest.approx(dhn_before)


@pytest.mark.parametrize("kind", list(_KINDS))
def test_r2_13_money_naming_a_bill_is_stamped_with_the_bills_shop(world, kind):
    """The admin sits on the ONLINE store; the bill is Dhanbad's: the money
    is Dhanbad's, whatever shop the admin happens to be on."""
    coll, id_key, _ = _KINDS[kind]
    made = _ok(world.post(f"/vendors/{V_BOTH}/{kind}", ADMIN, _money(kind, 100.0, "B-HELD-D")), 201)
    assert made["store_id"] == DHN
    assert world.db[coll].find_one({id_key: made[id_key]})["store_id"] == DHN


@pytest.mark.parametrize("kind", list(_KINDS))
def test_r2_13_an_admin_names_the_shop_or_leaves_it_to_the_suppliers_bills(world, kind):
    """Round 3 (review #1 / #13): an admin's money with no bill and no shop
    named used to be stamped with HIS topbar shop (ONLINE here) -- where he
    sits, not where the supplier's goods went. It now takes the legacy rule's
    shop -- V-BOTH's latest bill on or before 30 Sep is Dhanbad's B-HELD-D --
    STAMPED when written (round 3 review #2 / #8: left unstamped, a later
    back-dated bill moved it). A shop he names still wins."""
    coll, id_key, list_key = _KINDS[kind]
    by_query = _ok(world.post(f"/vendors/{V_BOTH}/{kind}", ADMIN, _money(kind, 10.0), store_id=PUN), 201)
    by_body = _ok(world.post(f"/vendors/{V_BOTH}/{kind}", ADMIN, _money(kind, 11.0, store_id=DHN)), 201)
    by_default = _ok(world.post(f"/vendors/{V_BOTH}/{kind}", ADMIN, _money(kind, 12.0)), 201)
    unplaced = _ok(world.post(f"/vendors/{V_BOTH}/{kind}", SUPERADMIN, _money(kind, 13.0)), 201)
    assert [by_query["store_id"], by_body["store_id"], by_default["store_id"], unplaced["store_id"]] == [
        PUN,
        DHN,
        DHN,
        DHN,
    ]
    assert world.db[coll].find_one({id_key: by_query[id_key]})["store_id"] == PUN
    assert world.db[coll].find_one({id_key: by_default[id_key]})["store_id"] == DHN
    assert world.db[coll].find_one({id_key: unplaced[id_key]})["store_id"] == DHN
    # Never the admin's topbar shop; the supplier's shop by its bills.
    online = {r[id_key] for r in _ok(world.get(f"/vendors/{V_BOTH}/{kind}", ADMIN, store_id=ONLINE))[list_key]}
    dhanbad = {r[id_key] for r in _ok(world.get(f"/vendors/{V_BOTH}/{kind}", ACCT_DHN))[list_key]}
    assert by_default[id_key] not in online
    assert {by_default[id_key], unplaced[id_key], by_body[id_key]} <= dhanbad


@pytest.mark.parametrize("kind", list(_KINDS))
def test_r2_13_a_non_admin_can_never_stamp_another_shop(world, kind):
    before = world.snapshot()
    for params, body in (({"store_id": DHN}, _money(kind, 5.0)), ({}, _money(kind, 5.0, store_id=DHN))):
        resp = world.post(f"/vendors/{V_BOTH}/{kind}", ACCT_PUNE, body, **params)
        assert resp.status_code == 403, resp.text
    assert world.snapshot() == before, "a refused write changed the payable"
    # His own shop, named, is fine.
    assert _ok(world.post(f"/vendors/{V_BOTH}/{kind}", ACCT_PUNE, _money(kind, 5.0), store_id=PUN), 201)["store_id"] == PUN


@pytest.mark.parametrize("kind", list(_KINDS))
def test_r2_13_a_shop_that_contradicts_the_bill_is_refused_not_refiled(world, kind):
    before = world.snapshot()
    resp = world.post(f"/vendors/{V_BOTH}/{kind}", ADMIN, _money(kind, 5.0, "B-HELD-D"), store_id=PUN)
    assert resp.status_code == 422, resp.text
    clash = world.post(f"/vendors/{V_BOTH}/{kind}", ADMIN, _money(kind, 5.0, store_id=PUN), store_id=DHN)
    assert clash.status_code == 422, clash.text
    assert world.snapshot() == before


# ----------------------------------------------------------------------------
# #1 -- a post-dated cheque is listed, flagged and not yet counted
# ----------------------------------------------------------------------------


def _record_post_dated(world, user=ADMIN) -> tuple:
    pay = _ok(world.post(f"/vendors/{V_PDC}/payments", user, {
        "amount": 5000.0, "payment_date": "2026-11-15", "bill_id": "PD1", "mode": "CHEQUE", "reference": "CHQ-777",
    }), 201)
    note = _ok(world.post(f"/vendors/{V_PDC}/debit-notes", user, {
        "amount": 300.0, "date": "2026-10-20", "bill_id": "PD1", "reason": "short supply",
    }), 201)
    return pay, note


@pytest.mark.parametrize("user", [ADMIN, ACCT_PUNE], ids=["admin", "pune-accountant"])
def test_r2_1_the_post_dated_cheque_and_note_are_listed_and_flagged(world, user):
    """Review #1: both POSTs answered 201, then GET /payments and
    /debit-notes returned 0 rows -- the accountant could not see the cheque
    he had just keyed."""
    pay, note = _record_post_dated(world, user)
    payments = _ok(world.get(f"/vendors/{V_PDC}/payments", user))["payments"]
    notes = _ok(world.get(f"/vendors/{V_PDC}/debit-notes", user))["debit_notes"]
    assert [(p["payment_id"], p["post_dated"]) for p in payments] == [(pay["payment_id"], True)], payments
    assert [(n["debit_note_id"], n["post_dated"]) for n in notes] == [(note["debit_note_id"], True)], notes


def test_r2_1_a_row_dated_today_or_earlier_is_not_post_dated(world):
    _ok(world.post(f"/vendors/{V_PDC}/payments", ADMIN, {"amount": 10.0, "payment_date": TODAY, "bill_id": "PD1"}), 201)
    _ok(world.post(f"/vendors/{V_PDC}/payments", ADMIN, {"amount": 20.0, "payment_date": "2026-11-15", "bill_id": "PD1"}), 201)
    rows = _ok(world.get(f"/vendors/{V_PDC}/payments", ADMIN))["payments"]
    assert [(r["amount"], r["post_dated"]) for r in rows] == [(20.0, True), (10.0, False)]  # newest first


def test_r2_1_the_ledger_owes_today_and_lists_the_later_rows_apart(world):
    pay, note = _record_post_dated(world)
    ledger = _ok(world.get(f"/vendors/{V_PDC}/ledger", ADMIN))["ledger"]
    assert [e["type"] for e in ledger["entries"]] == ["BILL"]
    assert ledger["closing_balance"] == pytest.approx(5000.0)
    assert (ledger["total_paid"], ledger["total_debit_notes"]) == (0.0, 0.0)
    later = ledger["post_dated"]
    assert [(e["type"], e["date"], e["debit"]) for e in later] == [
        ("DEBIT_NOTE", "2026-10-20", 300.0),
        ("PAYMENT", "2026-11-15", 5000.0),
    ], later
    assert all("balance" not in e for e in later), "a post-dated row carries no running balance"
    assert later[1]["ref"] == "CHQ-777"
    # The Suppliers card and AP aging owe the same 5000 today.
    vp = {r["vendor_id"]: r["balance"] for r in _ok(world.get("/finance/vendor-payments", ADMIN))}
    assert vp[V_PDC] == pytest.approx(5000.0)
    aging = {v["vendor_id"]: v["net_payable"] for v in _ok(world.get("/vendors/ap-aging", ADMIN))["vendors"]}
    assert aging[V_PDC] == pytest.approx(5000.0)


def test_r2_1_on_the_cheques_day_it_moves_into_the_balance(world, monkeypatch):
    _record_post_dated(world)
    monkeypatch.setattr(ap_engine, "now_ist_naive", lambda: datetime.fromisoformat("2026-11-20T12:00:00"))
    ledger = _ok(world.get(f"/vendors/{V_PDC}/ledger", ADMIN))["ledger"]
    assert ledger["post_dated"] == []
    assert ledger["closing_balance"] == pytest.approx(-300.0)  # 5000 - 5000 - 300: credit with the supplier
    rows = _ok(world.get(f"/vendors/{V_PDC}/payments", ADMIN))["payments"]
    assert [r["post_dated"] for r in rows] == [False]


def test_r2_1_the_bills_list_owes_what_the_ledger_owes_with_the_cheque_noted(world):
    """The stored status counts every recorded payment (a written cheque must
    not be offered for payment again); `outstanding` is the ledger's as-of
    figure, so the bills list and the ledger owe the same today."""
    _record_post_dated(world)
    assert world.db["vendor_bills"].find_one({"bill_id": "PD1"})["status"] == "PAID"
    pd1 = next(b for b in _ok(world.get(f"/vendors/{V_PDC}/bills", ADMIN))["bills"] if b["bill_id"] == "PD1")
    assert pd1["status"] == "PAID"
    assert pd1["outstanding"] == pytest.approx(5000.0), "#1: the bills list says Rs 0 while the ledger owes 5000"
    assert pd1["post_dated_money"] == pytest.approx(5300.0)
    assert pd1["post_dated_until"] == "2026-11-15"
    assert pd1["outstanding"] == pytest.approx(_closing(world, ADMIN, vendor=V_PDC))


# ----------------------------------------------------------------------------
# #15 -- another shop's goods receipt is not there for a shop-bound booker
# ----------------------------------------------------------------------------


def _bill_body(grn_id: str, number: str = "HDR-D") -> dict:
    return {
        "bill_number": number,
        "bill_date": "2026-09-25",
        "taxable_amount": 100.0,
        "tax_amount": 5.0,
        "total_amount": 105.0,
        "grn_id": grn_id,
    }


@pytest.mark.parametrize("grn_id", ["GRN-D1", "DC-D1"], ids=["receipt", "delivery-challan"])
def test_r2_15_a_pune_accountant_cannot_bill_a_dhanbad_receipt(world, grn_id):
    before = world.snapshot()
    resp = world.post(f"/vendors/{V_BOTH}/bills", ACCT_PUNE, _bill_body(grn_id))
    missing = world.post(f"/vendors/{V_BOTH}/bills", ACCT_PUNE, _bill_body("NO-SUCH-GRN"))
    assert missing.status_code == 404, missing.text
    assert resp.status_code == 404, (
        f"F63: a Pune accountant booked a bill on Dhanbad's receipt ({resp.status_code}): {resp.text[:200]}"
    )
    assert resp.json()["detail"] == missing.json()["detail"].replace("NO-SUCH-GRN", grn_id)
    assert DHN not in resp.text
    assert world.snapshot() == before, "a refused bill wrote (or claimed a challan)"


def test_r2_15_the_receipts_own_shop_and_an_admin_still_bill_it(world):
    _ok(world.post(f"/vendors/{V_BOTH}/bills", ACCT_DHN, _bill_body("GRN-D1")), 201)
    made = _ok(world.post(f"/vendors/{V_BOTH}/bills", ADMIN, _bill_body("DC-D1", "HDR-DC")), 201)
    assert made["store_id"] == DHN
    assert _ok(world.post(f"/vendors/{V_BOTH}/bills", ACCT_PUNE, _bill_body("GRN-P3", "HDR-P3")), 201)["store_id"] == PUN


def test_r2_15_already_billed_never_names_a_bill_outside_the_callers_shop(world):
    """GRN-P1 is Pune's receipt, billed by LEG-1, a legacy bill with no shop
    (admin-only everywhere else): the Pune accountant hears the receipt is
    billed, never which bill. His own shop's bill is named as before, and the
    admin sees every bill named."""
    before = world.snapshot()
    resp = world.post(f"/vendors/{V_BOTH}/bills", ACCT_PUNE, _bill_body("GRN-P1"))
    assert resp.status_code == 409, resp.text
    assert "LEG-1" not in resp.text
    assert resp.json()["detail"]["code"] == "grn_already_billed"
    own = world.post(f"/vendors/{V_BOTH}/bills", ACCT_PUNE, _bill_body("GRN-P2"))
    assert own.status_code == 409 and "B-P2-OWN" in own.text, own.text
    admin = world.post(f"/vendors/{V_BOTH}/bills", ADMIN, _bill_body("GRN-P1"))
    assert admin.status_code == 409 and "LEG-1" in admin.text, admin.text
    assert world.snapshot() == before


def test_r2_15_a_billed_challan_never_names_an_invoice_outside_the_callers_shop(world):
    world.db["grns"].insert_one({
        "grn_id": "DC-P9", "grn_number": "DC-P9", "store_id": PUN, "grn_subtype": "DELIVERY_CHALLAN",
        "status": "ACCEPTED", "vendor_id": V_BOTH, "dc_matched": True, "linked_bulk_invoice_id": "INV-DHN-7",
        "items": [{"product_id": "P1", "accepted_qty": 1}],
    })
    world.db["vendor_bills"].insert_one(_bill("INV-DHN-7", V_BOTH, DHN, "2026-09-09", 70.0, linked_dc_ids=["DC-P9"]))
    resp = world.post(f"/vendors/{V_BOTH}/bills", ACCT_PUNE, _bill_body("DC-P9"))
    assert resp.status_code == 409, resp.text
    assert "INV-DHN-7" not in resp.text
    admin = world.post(f"/vendors/{V_BOTH}/bills", ADMIN, _bill_body("DC-P9"))
    assert admin.status_code == 409 and "INV-DHN-7" in admin.text, admin.text


def test_r2_15_a_vendor_mismatch_is_still_a_vendor_mismatch(world):
    """Only the 'already billed' refusal is reworded; any other 409 passes."""
    world.db["vendors"].insert_one({"vendor_id": "V-OTHER", "legal_name": "Other", "trade_name": "Other", "is_active": True})
    resp = world.post("/vendors/V-OTHER/bills", ACCT_PUNE, _bill_body("GRN-P3"))
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "grn_vendor_mismatch"
