"""F63 on ONE purchase object: another shop's object by id is the same 404 a
missing one gets, never a body naming the other shop or its money.

Owner ruling (F63): ADMIN / SUPERADMIN see every shop; every other role sees
only its own shop, even when the request is edited -- a dropped or changed
store_id, or another shop's object typed by id. The one shop rule for one
object is dependencies.can_access_store_scoped (purchase_invoices.
_bill_in_scope_or_404 / _grn_in_scope_or_404); the one supplier-ledger row
rule is ap_engine.supplier_ledger_rows.

Review round 1 findings covered here:
  #15  purchase-invoice side: a Pune accountant drafted, previewed and booked
       bills on Dhanbad's goods receipt / Delivery Challans, and a 409 named
       the other bill.
  #10 / #17  the Recon console's scheme / rebate credit notes ignored the shop,
       and any accountant could tick another shop's note.
  #21  vendor return, goods receipt, PO variance dismissal and approval
       request read (or written) by id across shops.

World: Dhanbad (BV-DHN-01, company E-BV) and Pune (WO-PUN-01, company E-WO);
one supplier V-BOTH serves both. The caller is a Pune accountant / manager
unless a test says otherwise. No emoji (Windows cp1252).
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MONGODB_URI", "")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mongomock  # noqa: E402
import pytest  # noqa: E402
from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api.routers import approvals as appr_router  # noqa: E402
from api.routers import purchase_invoices as pi  # noqa: E402
from api.routers import purchase_recon as recon  # noqa: E402
from api.routers import vendor_returns as vr  # noqa: E402
from api.routers import vendors  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402
from api.services.approvals import ApprovalEngine  # noqa: E402

DHN = "BV-DHN-01"
PUN = "WO-PUN-01"
SUP_MH = "27ABCDE1234F1Z5"
BUY_JH = "20ZZZZZ9999Z1Z9"
BUY_MH = "27ZZZZZ9999Z1Z9"

# The Dhanbad bill's price: the figure that must never reach Pune.
DHN_PRICE = 4591.37


def _user(role, store=PUN, uid=None):
    return {
        "user_id": uid or f"u-{role.lower()}-{store}",
        "roles": [role],
        "store_ids": [store],
        "active_store_id": store,
    }


PUNE_ACCOUNTANT = _user("ACCOUNTANT")
PUNE_MANAGER = _user("STORE_MANAGER")
ADMIN_AT_PUNE = _user("ADMIN")


class _Repo:
    """find_by_id over one mongomock collection (the repository surface the
    routers call), plus `.collection` for the PO dismissal's guarded write."""

    def __init__(self, coll, key):
        self.collection = coll
        self._key = key

    def find_by_id(self, _id):
        return self.collection.find_one({self._key: _id}, {"_id": 0})

    def find_by_po(self, po_id):
        return list(self.collection.find({"po_id": po_id}, {"_id": 0}))


def _grn(grn_id, store, **over):
    doc = {
        "grn_id": grn_id,
        "grn_number": f"N-{grn_id}",
        "vendor_id": "V-BOTH",
        "store_id": store,
        "status": "ACCEPTED",
        "grn_subtype": "STANDARD",
        "vendor_invoice_no": f"SUP-{grn_id}",
        "vendor_invoice_date": "2026-09-10",
        "items": [{"product_id": "P1", "product_name": "Frame X", "accepted_qty": 2}],
    }
    doc.update(over)
    return doc


def _world():
    db = mongomock.MongoClient().db
    db["entities"].insert_many(
        [
            {"entity_id": "E-BV", "name": "Better Vision",
             "gstins": [{"gstin": BUY_JH, "state_code": "20", "is_primary": True}]},
            {"entity_id": "E-WO", "name": "WizOpt",
             "gstins": [{"gstin": BUY_MH, "state_code": "27", "is_primary": True}]},
        ]
    )
    db["stores"].insert_many(
        [
            {"store_id": DHN, "entity_id": "E-BV", "state_code": "20", "gstin": BUY_JH},
            {"store_id": PUN, "entity_id": "E-WO", "state_code": "27", "gstin": BUY_MH},
        ]
    )
    db["vendors"].insert_one(
        {"vendor_id": "V-BOTH", "trade_name": "Mumbai Lens House", "gstin": SUP_MH,
         "credit_days": 30}
    )
    db["grns"].insert_many(
        [
            _grn("GRN-D1", DHN, po_id="PO-D1"),
            _grn("GRN-P1", PUN, po_id="PO-P1"),
            _grn("DC-D1", DHN, grn_subtype="DELIVERY_CHALLAN"),
            _grn("DC-P1", PUN, grn_subtype="DELIVERY_CHALLAN"),
        ]
    )
    db["purchase_orders"].insert_many(
        [
            {"po_id": "PO-D1", "vendor_id": "V-BOTH", "delivery_store_id": DHN,
             "status": "PARTIALLY_RECEIVED",
             "items": [{"product_id": "P1", "ordered_qty": 3, "quantity": 3,
                        "unit_price": 4669.21, "tax_rate": 5.0}]},
            {"po_id": "PO-P1", "vendor_id": "V-BOTH", "delivery_store_id": PUN,
             "status": "PARTIALLY_RECEIVED",
             "items": [{"product_id": "P1", "ordered_qty": 3, "quantity": 3,
                        "unit_price": 1000.0, "tax_rate": 5.0}]},
        ]
    )
    db["vendor_bills"].insert_one(
        {"bill_id": "B-D1", "bill_number": "DHN-BILL-7", "doc_type": "PURCHASE_INVOICE",
         "vendor_id": "V-BOTH", "store_id": DHN, "grn_id": "GRN-D1", "po_id": "PO-D1",
         "bill_date": "2026-09-12", "total_amount": 9641.88, "outstanding": 9641.88,
         "lines": [{"product_id": "P1", "qty": 3, "unit_price": DHN_PRICE}]}
    )
    db["vendor_returns"].insert_one(
        {"return_id": "VR-D1", "vendor_id": "V-BOTH", "store_id": DHN,
         "return_type": "credit_note", "status": "created", "total_value": 4669.21,
         "items": [{"product_id": "P1", "quantity": 1, "unit_price": 4669.21}]}
    )
    return db


@pytest.fixture
def world(monkeypatch):
    """One app over every router under test, on one mongomock world; the
    caller is whoever `world.as_(user)` last named."""
    db = _world()
    grns = _Repo(db["grns"], "grn_id")
    pos = _Repo(db["purchase_orders"], "po_id")
    vend = _Repo(db["vendors"], "vendor_id")

    for name, value in (
        ("_get_db", lambda: db),
        ("get_vendor_repository", lambda: vend),
        ("get_purchase_order_repository", lambda: pos),
        ("get_grn_repository", lambda: grns),
        ("get_audit_repository", lambda: None),
    ):
        monkeypatch.setattr(pi, name, value)
        monkeypatch.setattr(vendors, name, value)
    monkeypatch.setattr(recon, "_get_db", lambda: db)
    monkeypatch.setattr(vr, "_get_db", lambda: db)
    monkeypatch.setattr(appr_router, "_get_db", lambda: db)

    app = FastAPI()
    app.include_router(pi.router, prefix="/api/v1/vendors/purchase-invoices")
    app.include_router(recon.router, prefix="/api/v1/vendors")
    app.include_router(vendors.router, prefix="/api/v1/vendors")
    app.include_router(vr.router, prefix="/api/v1/vendor-returns")
    app.include_router(appr_router.router, prefix="/api/v1/approvals")
    who = {"user": PUNE_ACCOUNTANT}

    async def _current():
        return who["user"]

    app.dependency_overrides[get_current_user] = _current
    cli = TestClient(app)

    class _W:
        pass

    w = _W()
    w.db = db
    w.cli = cli

    def as_(user):
        who["user"] = user
        return cli

    w.as_ = as_
    return w


_PI = "/api/v1/vendors/purchase-invoices"


def _bill_body(**over):
    body = {
        "vendor_id": "V-BOTH",
        "invoice_number": "PUN-TRY-1",
        "invoice_date": "2026-09-20",
        "lines": [{"product_id": "P1", "description": "Frame X", "qty": 2,
                   "unit_price": 1000, "gst_rate": 5}],
    }
    body.update(over)
    return body


def _same_404(a, b):
    """Two answers a caller cannot tell apart: both 404, same words up to the
    id typed (the route's own 'not found' for a missing object)."""
    assert a.status_code == b.status_code == 404, (a.text, b.text)
    return a.json()["detail"], b.json()["detail"]


# ===========================================================================
# #15 -- purchase invoices: another shop's goods receipt or DC is not there
# ===========================================================================


class TestAnotherShopsReceiptIsNotThere:
    def test_booking_on_dhanbads_receipt_is_the_missing_receipts_404(self, world):
        cli = world.as_(PUNE_ACCOUNTANT)
        other = cli.post(_PI, json=_bill_body(grn_id="GRN-D1"))
        ghost = cli.post(_PI, json=_bill_body(grn_id="GRN-NOPE"))
        assert _same_404(other, ghost) == ("GRN GRN-D1 not found", "GRN GRN-NOPE not found")
        # Nothing landed in Dhanbad's books.
        assert world.db["vendor_bills"].count_documents({}) == 1

    def test_the_draft_from_dhanbads_receipt_is_the_missing_receipts_404(self, world):
        cli = world.as_(PUNE_ACCOUNTANT)
        other = cli.get(f"{_PI}/from-grn/GRN-D1")
        ghost = cli.get(f"{_PI}/from-grn/GRN-NOPE")
        assert _same_404(other, ghost) == ("GRN not found", "GRN not found")
        assert "E-BV" not in other.text and BUY_JH not in other.text

    def test_the_dc_draft_from_dhanbads_challan_is_the_missing_dcs_404(self, world):
        cli = world.as_(PUNE_ACCOUNTANT)
        other = cli.get(f"{_PI}/from-dcs", params={"dc_ids": "DC-D1", "vendor_id": "V-BOTH"})
        ghost = cli.get(f"{_PI}/from-dcs", params={"dc_ids": "DC-NOPE", "vendor_id": "V-BOTH"})
        assert _same_404(other, ghost) == ("DC DC-D1 not found", "DC DC-NOPE not found")
        # Mixed with Pune's own DC it is still not there (never a 409
        # 'received at more than one store' that confirms it exists).
        mixed = cli.get(f"{_PI}/from-dcs", params={"dc_ids": "DC-P1,DC-D1", "vendor_id": "V-BOTH"})
        assert mixed.status_code == 404 and mixed.json()["detail"] == "DC DC-D1 not found"

    def test_the_preview_of_dhanbads_receipt_is_the_missing_receipts_404(self, world):
        cli = world.as_(PUNE_ACCOUNTANT)
        body = {"vendor_id": "V-BOTH", "lines": _bill_body()["lines"]}
        other = cli.post(f"{_PI}/preview", json={**body, "grn_id": "GRN-D1"})
        ghost = cli.post(f"{_PI}/preview", json={**body, "grn_id": "GRN-NOPE"})
        assert _same_404(other, ghost) == ("GRN GRN-D1 not found", "GRN GRN-NOPE not found")
        assert "E-BV" not in other.text and BUY_JH not in other.text

    def test_the_preview_of_dhanbads_challan_is_the_missing_dcs_404(self, world):
        cli = world.as_(PUNE_ACCOUNTANT)
        body = {"vendor_id": "V-BOTH", "lines": _bill_body()["lines"]}
        other = cli.post(f"{_PI}/preview", json={**body, "linked_dc_ids": ["DC-D1"]})
        ghost = cli.post(f"{_PI}/preview", json={**body, "linked_dc_ids": ["DC-NOPE"]})
        assert _same_404(other, ghost) == ("DC DC-D1 not found", "DC DC-NOPE not found")

    def test_booking_on_dhanbads_challan_is_the_missing_dcs_404(self, world):
        cli = world.as_(PUNE_ACCOUNTANT)
        other = cli.post(_PI, json=_bill_body(linked_dc_ids=["DC-D1"]))
        ghost = cli.post(_PI, json=_bill_body(linked_dc_ids=["DC-NOPE"]))
        assert _same_404(other, ghost) == ("DC DC-D1 not found", "DC DC-NOPE not found")
        assert world.db["vendor_bills"].count_documents({}) == 1
        dc = world.db["grns"].find_one({"grn_id": "DC-D1"})
        assert not dc.get("dc_matched")

    def test_pune_drafts_its_own_receipt_and_an_admin_reaches_every_shop(self, world):
        """The control: the rule narrows the reach, it does not close the door."""
        own = world.as_(PUNE_ACCOUNTANT).get(f"{_PI}/from-grn/GRN-P1")
        assert own.status_code == 200, own.text
        assert own.json()["recipient_entity_id"] == "E-WO"
        far = world.as_(ADMIN_AT_PUNE).get(f"{_PI}/from-grn/GRN-D1")
        assert far.status_code == 200, far.text
        assert far.json()["recipient_entity_id"] == "E-BV"
        pv = world.as_(ADMIN_AT_PUNE).post(
            f"{_PI}/preview", json={"vendor_id": "V-BOTH", "grn_id": "GRN-D1",
                                    "lines": _bill_body()["lines"]})
        assert pv.status_code == 200 and pv.json()["recipient_entity_id"] == "E-BV"

    def test_dhanbads_purchase_order_is_matched_as_a_missing_one(self, world):
        """A Pune bill on Pune's receipt that names Dhanbad's PO: the stored
        and returned 3-way match carries no Dhanbad price -- it is the match a
        PO id that does not exist gets."""
        cli = world.as_(PUNE_ACCOUNTANT)
        other = cli.post(_PI, json=_bill_body(grn_id="GRN-P1", po_id="PO-D1",
                                              invoice_number="PUN-A"))
        assert other.status_code == 201, other.text
        world.db["vendor_bills"].delete_one({"bill_number": "PUN-A"})
        world.db["grns"].update_one({"grn_id": "GRN-P1"},
                                    {"$unset": {"billed_qty": "", "billed_claim_ids": ""}})
        ghost = cli.post(_PI, json=_bill_body(grn_id="GRN-P1", po_id="PO-NOPE",
                                              invoice_number="PUN-B"))
        assert ghost.status_code == 201, ghost.text
        assert other.json()["match_detail"] == ghost.json()["match_detail"]
        assert "4669.21" not in other.text


class TestARefusalNamesOnlyBillsTheCallerSees:
    def _pune_receipt_with_a_shopless_bill(self, world):
        # A legacy header-only bill (booked before bills carried a shop) on
        # Pune's own receipt: a non-admin may not open it, so may not be told
        # its number either.
        world.db["vendor_bills"].insert_one(
            {"bill_id": "B-OLD", "bill_number": "LEGACY-55", "vendor_id": "V-BOTH",
             "grn_id": "GRN-P1", "bill_date": "2026-09-01", "total_amount": 2100.0}
        )

    def test_the_over_bill_refusal_does_not_name_a_bill_out_of_reach(self, world):
        self._pune_receipt_with_a_shopless_bill(world)
        r = world.as_(PUNE_ACCOUNTANT).post(_PI, json=_bill_body(grn_id="GRN-P1"))
        assert r.status_code == 409, r.text
        detail = r.json()["detail"]
        assert detail["code"] == "grn_billed_without_lines"
        assert detail["billed_by"] == []
        assert "LEGACY-55" not in r.text and "B-OLD" not in r.text
        # An admin may open that bill, so is told which one it is.
        r = world.as_(ADMIN_AT_PUNE).post(_PI, json=_bill_body(grn_id="GRN-P1"))
        assert r.status_code == 409 and r.json()["detail"]["billed_by"] == ["LEGACY-55"]

    def test_the_header_bill_guard_keeps_the_same_rule(self, world):
        """assert_grn_billable_header_only, given the caller: another shop's
        receipt is the missing receipt's 404, and its 409 names no bill out of
        reach (the header-only door can pass its caller in)."""
        with pytest.raises(HTTPException) as other:
            pi.assert_grn_billable_header_only(world.db, "GRN-D1", "V-BOTH", PUNE_ACCOUNTANT)
        with pytest.raises(HTTPException) as ghost:
            pi.assert_grn_billable_header_only(world.db, "GRN-NOPE", "V-BOTH", PUNE_ACCOUNTANT)
        assert (other.value.status_code, other.value.detail) == (404, "GRN GRN-D1 not found")
        assert (ghost.value.status_code, ghost.value.detail) == (404, "GRN GRN-NOPE not found")

        self._pune_receipt_with_a_shopless_bill(world)
        with pytest.raises(HTTPException) as billed:
            pi.assert_grn_billable_header_only(world.db, "GRN-P1", "V-BOTH", PUNE_ACCOUNTANT)
        assert billed.value.status_code == 409
        assert billed.value.detail["billed_by"] is None
        assert "LEGACY-55" not in billed.value.detail["message"]


# ===========================================================================
# #10 / #17 -- the Recon console's scheme / rebate credit notes
# ===========================================================================


def _rebate_notes(db):
    """Two volume-rebate credit notes as rebate_engine.post writes them (no
    bill, no shop, an offset-aware created_at): V-DHN bills only Dhanbad,
    V-PUN only Pune -- so the ledger puts each note in that shop."""
    db["vendor_bills"].insert_many(
        [
            {"bill_id": "B-VD", "vendor_id": "V-DHN", "store_id": DHN, "bill_date": "2026-09-01",
             "total_amount": 5000.0},
            {"bill_id": "B-VP", "vendor_id": "V-PUN", "store_id": PUN, "bill_date": "2026-09-01",
             "total_amount": 5000.0},
        ]
    )
    db["vendor_debit_notes"].insert_many(
        [
            {"_id": "CN-REB-D", "credit_note_number": "CN-REB-D", "vendor_id": "V-DHN",
             "amount": 555.55, "bill_id": None, "source": "VOLUME_REBATE",
             "created_at": "2026-09-20T10:00:00.123456+00:00"},
            {"_id": "CN-REB-P", "credit_note_number": "CN-REB-P", "vendor_id": "V-PUN",
             "amount": 222.22, "bill_id": None, "source": "VOLUME_REBATE",
             "created_at": "2026-09-20T10:00:00.123456+00:00"},
        ]
    )


def _scheme_numbers(resp):
    assert resp.status_code == 200, resp.text
    return sorted(c["credit_note_number"] for c in resp.json()["pending_credit_notes_scheme"])


class TestSchemeCreditNotesFollowTheShop:
    _URL = "/api/v1/vendors/recon/worklists"

    def test_a_pune_accountant_sees_pune_notes_only(self, world):
        _rebate_notes(world.db)
        assert _scheme_numbers(world.as_(PUNE_ACCOUNTANT).get(self._URL)) == ["CN-REB-P"]

    def test_an_admin_sees_every_shop_and_the_filter_narrows(self, world):
        _rebate_notes(world.db)
        cli = world.as_(ADMIN_AT_PUNE)
        assert _scheme_numbers(cli.get(self._URL)) == ["CN-REB-D", "CN-REB-P"]
        assert _scheme_numbers(cli.get(self._URL, params={"store_id": PUN})) == ["CN-REB-P"]
        assert _scheme_numbers(cli.get(self._URL, params={"store_id": DHN})) == ["CN-REB-D"]

    def test_a_pune_accountant_cannot_tick_dhanbads_note(self, world):
        _rebate_notes(world.db)
        cli = world.as_(PUNE_ACCOUNTANT)
        other = cli.post("/api/v1/vendors/recon/credit-notes/CN-REB-D/mark-received")
        ghost = cli.post("/api/v1/vendors/recon/credit-notes/CN-NOPE/mark-received")
        assert _same_404(other, ghost) == ("Scheme credit note not found",) * 2
        assert "cn_received_at" not in world.db["vendor_debit_notes"].find_one({"_id": "CN-REB-D"})
        own = cli.post("/api/v1/vendors/recon/credit-notes/CN-REB-P/mark-received")
        assert own.status_code == 200, own.text
        assert world.db["vendor_debit_notes"].find_one({"_id": "CN-REB-P"})["cn_received_at"]

    def test_an_admin_ticks_any_shops_note(self, world):
        _rebate_notes(world.db)
        r = world.as_(ADMIN_AT_PUNE).post("/api/v1/vendors/recon/credit-notes/CN-REB-D/mark-received")
        assert r.status_code == 200, r.text


# ===========================================================================
# #21 -- vendor return, goods receipt, PO variance, approval request by id
# ===========================================================================


class TestOtherShopsObjectsById:
    @pytest.mark.parametrize("user", [PUNE_ACCOUNTANT, PUNE_MANAGER], ids=lambda u: u["roles"][0])
    def test_dhanbads_vendor_return_is_the_missing_returns_404(self, world, user):
        cli = world.as_(user)
        other = cli.get("/api/v1/vendor-returns/VR-D1")
        ghost = cli.get("/api/v1/vendor-returns/VR-NOPE")
        assert _same_404(other, ghost) == ("Return not found",) * 2
        assert "4669.21" not in other.text

    def test_an_admin_reads_dhanbads_vendor_return(self, world):
        r = world.as_(ADMIN_AT_PUNE).get("/api/v1/vendor-returns/VR-D1")
        assert r.status_code == 200 and r.json()["return_id"] == "VR-D1"

    @pytest.mark.parametrize("user", [PUNE_ACCOUNTANT, PUNE_MANAGER], ids=lambda u: u["roles"][0])
    def test_dhanbads_goods_receipt_is_the_missing_receipts_404(self, world, user):
        cli = world.as_(user)
        other = cli.get("/api/v1/vendors/grn/GRN-D1")
        ghost = cli.get("/api/v1/vendors/grn/GRN-NOPE")
        assert _same_404(other, ghost) == ("GRN not found",) * 2
        own = cli.get("/api/v1/vendors/grn/GRN-P1")
        assert own.status_code == 200 and own.json()["grn_id"] == "GRN-P1"

    _DISMISS = {"product_id": "P1", "reason": "supplier short-closed the line"}

    def test_dismissing_dhanbads_variance_is_the_missing_pos_404(self, world):
        cli = world.as_(PUNE_ACCOUNTANT)
        other = cli.post("/api/v1/vendors/purchase-orders/PO-D1/dismiss-variance", json=self._DISMISS)
        ghost = cli.post("/api/v1/vendors/purchase-orders/PO-NOPE/dismiss-variance", json=self._DISMISS)
        assert _same_404(other, ghost) == ("Purchase order not found",) * 2
        assert not world.db["purchase_orders"].find_one({"po_id": "PO-D1"}).get("dismissed_variances")

    def test_a_dismissal_never_prices_off_dhanbads_bill_or_receipt(self, world):
        """Pune's own PO, naming Dhanbad's bill (or receipt): the same 404 as
        a bill (receipt) that does not exist -- never a suggested debit note
        priced off Dhanbad's bill, and nothing written."""
        cli = world.as_(PUNE_ACCOUNTANT)
        url = "/api/v1/vendors/purchase-orders/PO-P1/dismiss-variance"
        other = cli.post(url, json={**self._DISMISS, "grn_id": "GRN-P1", "bill_id": "B-D1"})
        ghost = cli.post(url, json={**self._DISMISS, "grn_id": "GRN-P1", "bill_id": "B-NOPE"})
        assert _same_404(other, ghost) == ("Purchase invoice not found",) * 2
        assert str(DHN_PRICE) not in other.text
        other = cli.post(url, json={**self._DISMISS, "grn_id": "GRN-D1", "bill_id": "B-D1"})
        ghost = cli.post(url, json={**self._DISMISS, "grn_id": "GRN-NOPE", "bill_id": "B-D1"})
        assert _same_404(other, ghost) == ("GRN not found",) * 2
        assert not world.db["purchase_orders"].find_one({"po_id": "PO-P1"}).get("dismissed_variances")

    def test_pune_dismisses_its_own_line_and_an_admin_any(self, world):
        r = world.as_(PUNE_ACCOUNTANT).post(
            "/api/v1/vendors/purchase-orders/PO-P1/dismiss-variance", json=self._DISMISS)
        assert r.status_code == 200 and r.json()["dismissed"] is True
        r = world.as_(ADMIN_AT_PUNE).post(
            "/api/v1/vendors/purchase-orders/PO-D1/dismiss-variance",
            json={**self._DISMISS, "grn_id": "GRN-D1", "bill_id": "B-D1"})
        assert r.status_code == 200, r.text
        assert r.json()["suggested_amount"] == round(1 * DHN_PRICE, 2)


class TestApprovalRequestsById:
    def _requests(self, world):
        eng = ApprovalEngine(db=world.db)
        out = {}
        for key, store, maker in (
            ("dhn", DHN, "u-maker-dhn"),
            ("pun", PUN, "u-maker-pun"),
            ("org", None, "u-maker-hq"),
        ):
            res = eng.request(action_type="rtv", requested_by=maker,
                              requested_by_roles=["STORE_MANAGER"], store_id=store,
                              amount=1500.0, context={"rma_id": f"RMA-{key}"})
            assert res.get("ok"), res
            out[key] = res["request_id"]
        return out

    def test_a_pune_manager_is_told_dhanbads_request_is_not_there(self, world):
        ids = self._requests(world)
        cli = world.as_(PUNE_MANAGER)
        other = cli.get(f"/api/v1/approvals/requests/{ids['dhn']}")
        ghost = cli.get("/api/v1/approvals/requests/REQ-NOPE")
        assert _same_404(other, ghost) == ("Request not found",) * 2
        assert "RMA-dhn" not in other.text

    def test_own_shop_shopless_own_request_and_admin_still_read(self, world):
        ids = self._requests(world)
        cli = world.as_(PUNE_MANAGER)
        assert cli.get(f"/api/v1/approvals/requests/{ids['pun']}").status_code == 200
        # A request with no shop is org-wide (the engine's own rule: every
        # scoped approver's inbox lists it, so its detail opens too).
        assert cli.get(f"/api/v1/approvals/requests/{ids['org']}").status_code == 200
        # Its maker reads it wherever it was raised for.
        maker = _user("STORE_MANAGER", store=PUN, uid="u-maker-dhn")
        assert world.as_(maker).get(f"/api/v1/approvals/requests/{ids['dhn']}").status_code == 200
        assert world.as_(ADMIN_AT_PUNE).get(f"/api/v1/approvals/requests/{ids['dhn']}").status_code == 200
