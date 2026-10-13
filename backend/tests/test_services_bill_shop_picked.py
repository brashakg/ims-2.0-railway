"""
IMS 2.0 -- A BILL NO GOODS RECEIPT PLACES IS BOOKED TO THE SHOP THE ADMIN
PICKS, NEVER HIS TOPBAR SHOP (owner ruling 2026-10-12, R9)
==========================================================================
Services bills (rent, freight, job-work) have no goods receipt, so nothing on
the bill says which shop they are for. Both bill doors -- the Cash Flow
'+ Bill' (POST /vendors/{id}/bills) and the manual purchase invoice (POST
/vendors/purchase-invoices, and its /preview) -- used to book an ADMIN's such
bill to HIS topbar shop: a Pune rent bill keyed at Dhanbad sat in Dhanbad's
books, on Dhanbad's GSTIN (so in Dhanbad's GSTR-3B input credit).

The ruling: an ADMIN / SUPERADMIN must pick the shop (store_id); missing, the
door answers 422 BILL_SHOP_REQUIRED ('Pick the shop this bill is for') and
writes nothing. Picked, the bill and its buyer GSTIN are that shop's (the
shop's own registration, org_validation.shop_gstin). A bill naming a goods
receipt keeps the receipt's shop -- a store_id naming another is a 422. A
legacy receipt with no shop on record is placed like no receipt. Anyone else
keeps their own shop; a store_id naming another shop is 403.

The from-receipt drafts (GET /from-grn, /from-dcs) follow the same rule: a
legacy receipt with no shop drafts NO shop and NO buyer GSTIN for an admin,
so the form asks him. They used to prefill his topbar shop's GSTIN, which the
form sent back beside the shop he picked -- refused as 'not one of our GST
numbers' -- and a superadmin with no topbar shop could not open them at all.

Today is pinned to 1 Oct 2026 (IST).

Run: JWT_SECRET_KEY=test ENVIRONMENT=test python -m pytest
     backend/tests/test_services_bill_shop_picked.py -q
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

DHN = "BV-DHN-01"  # Better Vision, Jharkhand
PUN = "WO-PUN-01"  # WizOpt, Maharashtra
DHN_GSTIN = "20AAAAA0000A1Z5"
PUN_GSTIN = "27BBBBB1111B1Z5"
BLR = "WO-BLR-01"  # WizOpt again, Karnataka: the same company, another registration
BLR_GSTIN = "29BBBBB1111B1Z1"
V = "V-RENT"
TODAY = "2026-10-01"
ON = "2026-09-25"


def _user(role: str, store, stores=()) -> dict:
    return {
        "user_id": f"u-{role.lower()}-{store or 'none'}",
        "username": role.lower(),
        "roles": [role],
        "store_ids": list(stores),
        "active_store_id": store,
    }


ADMIN_DHN = _user("ADMIN", DHN)  # the admin's topbar shop is Dhanbad
SUPER_NONE = _user("SUPERADMIN", None)  # a superadmin with no topbar shop
ACCT_PUN = _user("ACCOUNTANT", PUN, [PUN])

BILLS = "/vendors/" + V + "/bills"
INVOICES = "/vendors/purchase-invoices"


@pytest.fixture(autouse=True)
def _today(monkeypatch):
    monkeypatch.setattr(
        ap_engine, "now_ist_naive", lambda: datetime.fromisoformat(TODAY + "T12:00:00")
    )


def _seed(db) -> None:
    # The company master (#1167): each shop's company holds a registration in
    # the shop's state, so each shop's own GSTIN is told apart.
    db["entities"].insert_many([
        {"entity_id": "E-BV", "name": "Better Vision",
         "gstins": [{"gstin": DHN_GSTIN, "state_code": "20", "is_primary": True}]},
        {"entity_id": "E-WO", "name": "WizOpt",
         "gstins": [{"gstin": PUN_GSTIN, "state_code": "27", "is_primary": True},
                    {"gstin": BLR_GSTIN, "state_code": "29"}]},
    ])
    db["stores"].insert_many([
        {"store_id": DHN, "entity_id": "E-BV", "state_code": "20", "gstin": DHN_GSTIN},
        {"store_id": PUN, "entity_id": "E-WO", "state_code": "27", "gstin": PUN_GSTIN},
        {"store_id": BLR, "entity_id": "E-WO", "state_code": "29", "gstin": BLR_GSTIN},
    ])
    db["vendors"].insert_one(
        {"vendor_id": V, "legal_name": "Landlord", "trade_name": "Landlord",
         "is_active": True, "credit_days": 30}
    )
    accepted = {
        "status": "ACCEPTED",
        "vendor_id": V,
        "items": [{"product_id": "P1", "accepted_qty": 1}],
    }
    db["grns"].insert_many([
        {"grn_id": "GRN-D", "grn_number": "GRN-D", "store_id": DHN, **accepted},
        {"grn_id": "GRN-NOSHOP", "grn_number": "GRN-NOSHOP", **accepted},  # legacy
        # A legacy Delivery Challan with no shop on record.
        {"grn_id": "DC-OLD", "grn_number": "DC-OLD", "grn_subtype": "DELIVERY_CHALLAN",
         "dc_matched": False, "dc_date": ON, **accepted},
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

    def post(self, path, user, body):
        async def _as_user():
            return dict(user)

        self._app.dependency_overrides[get_current_user] = _as_user
        return self._client.post(path, json=body)

    def get(self, path, user):
        async def _as_user():
            return dict(user)

        self._app.dependency_overrides[get_current_user] = _as_user
        return self._client.get(path)

    def bills(self) -> list:
        return list(self.db["vendor_bills"].find({}, {"_id": 0}))


@pytest.fixture
def world(_client, monkeypatch):
    from database.repositories.vendor_repository import GRNRepository, VendorRepository

    db_name = f"ims_test_bill_shop_{uuid.uuid4().hex[:8]}"
    db = _client[db_name]
    _seed(db)
    proxy = _DBProxy(db)
    for mod in (vendors_pkg, finance_pkg, pi_mod):
        monkeypatch.setattr(mod, "_get_db", lambda: proxy)
    for mod in (vendors_pkg, pi_mod):
        monkeypatch.setattr(mod, "get_vendor_repository", lambda: VendorRepository(db["vendors"]))
        monkeypatch.setattr(mod, "get_grn_repository", lambda: GRNRepository(db["grns"]))
    app = FastAPI()
    # The invoice router first: the vendors router ends in a /{vendor_id}
    # catch-all (api.main mounts them in this order).
    app.include_router(pi_mod.router, prefix=INVOICES)
    app.include_router(vendors_pkg.router, prefix="/vendors")
    try:
        yield _World(TestClient(app), app, db)
    finally:
        try:
            _client.drop_database(db_name)
        except Exception:
            pass


def _services_bill(number: str, **extra) -> dict:
    return {
        "bill_number": number,
        "bill_date": ON,
        "taxable_amount": 1000.0,
        "tax_amount": 180.0,
        "total_amount": 1180.0,
        "bill_kind": "SERVICES",
        **extra,
    }


def _services_invoice(number: str, **extra) -> dict:
    return {
        "vendor_id": V,
        "invoice_number": number,
        "invoice_date": ON,
        "lines": [{"description": "Shop rent, September", "qty": 1, "unit_price": 1000, "gst_rate": 18}],
        "bill_kind": "SERVICES",
        **extra,
    }


def _refused(resp, status: int, code: str) -> dict:
    assert resp.status_code == status, resp.text
    detail = resp.json()["detail"]
    assert isinstance(detail, dict) and detail.get("code") == code, detail
    return detail


# ---------------------------------------------------------------------------
# The Cash Flow '+ Bill' door
# ---------------------------------------------------------------------------


class TestCashFlowBillDoor:
    @pytest.mark.parametrize("admin", [ADMIN_DHN, SUPER_NONE], ids=["admin-on-dhanbad", "superadmin-no-shop"])
    def test_an_admin_services_bill_naming_no_shop_is_refused_and_nothing_written(self, world, admin):
        detail = _refused(world.post(BILLS, admin, _services_bill("RENT-1")), 422, "BILL_SHOP_REQUIRED")
        assert detail["message"] == "Pick the shop this bill is for."
        assert world.bills() == []

    def test_an_admin_services_bill_books_to_the_shop_he_picked_with_its_gstin(self, world):
        resp = world.post(BILLS, ADMIN_DHN, _services_bill("RENT-1", store_id=PUN))
        assert resp.status_code == 201, resp.text
        made = resp.json()
        # Pune's bill on Pune's registration -- not the admin's Dhanbad.
        assert made["store_id"] == PUN
        assert made["recipient_gstin"] == PUN_GSTIN
        assert made["recipient_entity_id"] == "E-WO"
        [stored] = world.bills()
        assert (stored["store_id"], stored["recipient_gstin"]) == (PUN, PUN_GSTIN)

    def test_a_superadmin_with_no_topbar_shop_books_to_the_shop_he_picked(self, world):
        resp = world.post(BILLS, SUPER_NONE, _services_bill("RENT-1", store_id=DHN))
        assert resp.status_code == 201, resp.text
        assert (resp.json()["store_id"], resp.json()["recipient_gstin"]) == (DHN, DHN_GSTIN)

    def test_an_admin_naming_a_shop_that_does_not_exist_is_refused(self, world):
        _refused(world.post(BILLS, ADMIN_DHN, _services_bill("RENT-1", store_id="NO-SUCH")), 422, "BILL_SHOP_UNKNOWN")
        assert world.bills() == []

    def test_a_non_admin_keeps_his_own_shop(self, world):
        resp = world.post(BILLS, ACCT_PUN, _services_bill("RENT-1"))
        assert resp.status_code == 201, resp.text
        assert (resp.json()["store_id"], resp.json()["recipient_gstin"]) == (PUN, PUN_GSTIN)

    def test_a_non_admin_naming_another_shop_is_403_and_nothing_written(self, world):
        resp = world.post(BILLS, ACCT_PUN, _services_bill("RENT-1", store_id=DHN))
        assert resp.status_code == 403, resp.text
        assert world.bills() == []

    def test_a_receipt_bill_keeps_the_receipts_shop_and_a_contradicting_shop_is_422(self, world):
        goods = {**_services_bill("GOODS-1"), "bill_kind": "GOODS", "grn_id": "GRN-D"}
        _refused(world.post(BILLS, ADMIN_DHN, {**goods, "store_id": PUN}), 422, "BILL_SHOP_NOT_RECEIPT_SHOP")
        assert world.bills() == []
        resp = world.post(BILLS, ADMIN_DHN, goods)  # no shop named: the receipt's
        assert resp.status_code == 201, resp.text
        assert (resp.json()["store_id"], resp.json()["recipient_gstin"]) == (DHN, DHN_GSTIN)

    def test_a_legacy_receipt_with_no_shop_is_placed_by_the_admins_pick(self, world):
        goods = {**_services_bill("GOODS-2"), "bill_kind": "GOODS", "grn_id": "GRN-NOSHOP"}
        _refused(world.post(BILLS, ADMIN_DHN, goods), 422, "BILL_SHOP_REQUIRED")
        assert world.bills() == []
        resp = world.post(BILLS, ADMIN_DHN, {**goods, "store_id": PUN})
        assert resp.status_code == 201, resp.text
        assert (resp.json()["store_id"], resp.json()["recipient_gstin"]) == (PUN, PUN_GSTIN)


# ---------------------------------------------------------------------------
# The manual purchase-invoice door (and its preview: the same rule)
# ---------------------------------------------------------------------------


class TestPurchaseInvoiceDoor:
    def test_an_admin_manual_services_invoice_naming_no_shop_is_refused_and_nothing_written(self, world):
        detail = _refused(world.post(INVOICES, ADMIN_DHN, _services_invoice("FR-1")), 422, "BILL_SHOP_REQUIRED")
        assert detail["message"] == "Pick the shop this bill is for."
        assert world.bills() == []
        _refused(world.post(INVOICES + "/preview", ADMIN_DHN, _services_invoice("FR-1")), 422, "BILL_SHOP_REQUIRED")

    def test_an_admin_manual_services_invoice_books_to_the_shop_he_picked_with_its_gstin(self, world):
        pv = world.post(INVOICES + "/preview", ADMIN_DHN, _services_invoice("FR-1", store_id=PUN))
        assert pv.status_code == 200, pv.text
        assert pv.json()["recipient_gstin"] == PUN_GSTIN
        resp = world.post(INVOICES, ADMIN_DHN, _services_invoice("FR-1", store_id=PUN))
        assert resp.status_code == 201, resp.text
        [stored] = world.bills()
        assert (stored["store_id"], stored["recipient_gstin"], stored["recipient_entity_id"]) == (
            PUN,
            PUN_GSTIN,
            "E-WO",
        )

    def test_a_typed_gstin_of_another_shop_cannot_move_the_admins_picked_bill(self, world):
        # Bangalore's registration is WizOpt's own, so only the picked shop's
        # rule refuses it: typed onto a Pune bill it would move the credit to
        # the Karnataka return.
        body = _services_invoice("FR-1", store_id=PUN, recipient_gstin=BLR_GSTIN)
        _refused(world.post(INVOICES, ADMIN_DHN, body), 422, "RECIPIENT_GSTIN_NOT_BILL_SHOP")
        # Another company's number (his own Dhanbad's, say, left in the box)
        # is refused naming the shop he picked -- not 'not one of our GST
        # numbers, check the supplier's bill', which blamed the bill.
        other_company = _services_invoice("FR-2", store_id=PUN, recipient_gstin=DHN_GSTIN)
        detail = _refused(world.post(INVOICES, ADMIN_DHN, other_company), 422, "RECIPIENT_GSTIN_NOT_BILL_SHOP")
        assert PUN in detail["message"] and PUN_GSTIN in detail["message"]
        assert "the shop this bill is for" in detail["message"]
        assert world.bills() == []
        # The picked shop's own number, typed, agrees and books.
        same = world.post(INVOICES, ADMIN_DHN, _services_invoice("FR-3", store_id=PUN, recipient_gstin=PUN_GSTIN))
        assert same.status_code == 201, same.text

    def test_a_non_admin_keeps_his_own_shop_and_another_shop_is_403(self, world):
        resp = world.post(INVOICES, ACCT_PUN, _services_invoice("FR-1"))
        assert resp.status_code == 201, resp.text
        [stored] = world.bills()
        assert (stored["store_id"], stored["recipient_gstin"]) == (PUN, PUN_GSTIN)
        other = world.post(INVOICES, ACCT_PUN, _services_invoice("FR-2", store_id=DHN))
        assert other.status_code == 403, other.text
        assert len(world.bills()) == 1


# ---------------------------------------------------------------------------
# The from-receipt drafts: a legacy receipt with no shop drafts NO shop for an
# admin, so the form asks him -- and nothing from his topbar shop rides along
# ---------------------------------------------------------------------------


def _drafted_invoice(draft: dict, number: str, **extra) -> dict:
    """The booking the form sends from a draft: the draft's recipient GSTIN
    and receipt link, the admin's picked shop."""
    return {
        "vendor_id": V,
        "invoice_number": number,
        "invoice_date": ON,
        "recipient_gstin": draft.get("recipient_gstin"),
        "grn_id": draft.get("grn_id"),
        "linked_dc_ids": draft.get("linked_dc_ids") or [],
        "lines": [{"product_id": "P1", "description": "Frame", "qty": 1, "unit_price": 1000, "gst_rate": 5}],
        **extra,
    }


class TestReceiptDraftsLeaveTheShopToTheAdmin:
    def test_an_admins_legacy_dc_draft_names_no_shop_and_books_to_his_pick(self, world):
        resp = world.get(INVOICES + "/from-dcs?dc_ids=DC-OLD", ADMIN_DHN)
        assert resp.status_code == 200, resp.text
        draft = resp.json()
        # Not his topbar Dhanbad, nor Dhanbad's GSTIN: nothing to contradict
        # the shop he picks on the form.
        assert (draft["store_id"], draft["recipient_gstin"], draft["recipient_entity_id"]) == (None, None, None)
        body = _drafted_invoice(draft, "DC-INV-1", store_id=PUN)
        pv = world.post(INVOICES + "/preview", ADMIN_DHN, body)
        assert pv.status_code == 200, pv.text
        assert pv.json()["recipient_gstin"] == PUN_GSTIN
        made = world.post(INVOICES, ADMIN_DHN, body)
        assert made.status_code == 201, made.text
        [stored] = world.bills()
        assert (stored["store_id"], stored["recipient_gstin"]) == (PUN, PUN_GSTIN)

    def test_an_admins_legacy_grn_draft_names_no_shop_and_books_to_his_pick(self, world):
        resp = world.get(INVOICES + "/from-grn/GRN-NOSHOP", ADMIN_DHN)
        assert resp.status_code == 200, resp.text
        draft = resp.json()
        assert (draft["store_id"], draft["recipient_gstin"], draft["recipient_entity_id"]) == (None, None, None)
        made = world.post(INVOICES, ADMIN_DHN, _drafted_invoice(draft, "GRN-INV-1", store_id=PUN))
        assert made.status_code == 201, made.text
        [stored] = world.bills()
        assert (stored["store_id"], stored["recipient_gstin"]) == (PUN, PUN_GSTIN)

    def test_a_superadmin_with_no_topbar_shop_can_open_a_legacy_receipts_draft(self, world):
        # It used to be 422 RECIPIENT_UNRESOLVED 'pick the shop it is for (top
        # bar)': the form's shop pick was never offered.
        for path in ("/from-dcs?dc_ids=DC-OLD", "/from-grn/GRN-NOSHOP"):
            resp = world.get(INVOICES + path, SUPER_NONE)
            assert resp.status_code == 200, resp.text
            assert (resp.json()["store_id"], resp.json()["recipient_gstin"]) == (None, None)

    def test_a_receipt_with_a_shop_still_drafts_that_shop(self, world):
        resp = world.get(INVOICES + "/from-grn/GRN-D", ADMIN_DHN)
        assert resp.status_code == 200, resp.text
        assert (resp.json()["store_id"], resp.json()["recipient_gstin"]) == (DHN, DHN_GSTIN)
