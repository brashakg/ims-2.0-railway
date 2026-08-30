"""
IMS 2.0 - The free-text goods-bill hole: bill_kind + the receipt rule
=====================================================================
Audit-reproduced twice on main: a bill for goods described only in PROSE
booked with no receipt and no products named, dodging owner ruling 15 ("a
goods bill must link its receipt") entirely --

  (a) Cash Flow -> vendor Record-Bill form (POST /vendors/{id}/bills):
      Rs 52,500 of "20 pcs assorted frames" -> 201, header-only, nothing
      to tally, nothing for the 3-way match or the rejected-goods hold.
  (b) the manual purchase-invoice form: free-text lines carry no product_id,
      so the goods trigger never fired -- Rs 72,450 booked the same way.

THE FIX IS A REQUIRED DECLARATION, PLUS A DOOR THAT ACTUALLY OPENS:
  * every receipt-less bill must say what it is FOR: GOODS or SERVICES;
  * GOODS refuses until a goods receipt is linked, and the refusal names the
    no-PO Delivery-Challan route the Goods Receipt screen now really has;
  * a declared SERVICES/expense bill books exactly as before;
  * the header door accepts a DELIVERY CHALLAN receipt too (that is what the
    no-PO path produces) and CLAIMS it (dc_matched) so the same goods can
    never be billed twice -- by a second header bill or a /from-dcs invoice;
  * stored legacy bills without the field read as-is through every reader.

ACCEPTED RESIDUAL (the owner's floor): goods typed as prose and deliberately
declared SERVICES still book -- software cannot read the carton.

The invoice-door prose gates live beside their siblings in
test_purchase_lifecycle.py (TestTheInvoiceIsTheGate); this file carries the
header-bill door, the DC claim, the cross-door drift probe and the legacy
readers. Rupee numbers are the audit's own.

Run: JWT_SECRET_KEY=test python -m pytest backend/tests/test_goods_bill_kind_gate.py -q
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MONGODB_URI", "")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api.routers import purchase_invoices as pi  # noqa: E402
from api.routers import vendors as vend  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402
from api.services.ap_engine import normalize_bill_kind  # noqa: E402
from strict_fakes import StrictDB  # noqa: E402


SUP_MH = "27ABCDE1234F1Z5"
BUY_MH = "27ZZZZZ9999Z1Z9"

# The audit's own numbers. 20 pcs assorted frames, Rs 2,500 each = Rs 50,000
# taxable + 5% GST = Rs 52,500 -- the exact bill the Cash Flow form booked
# unchecked. The rent bill is a DELIBERATE services case at Rs 8,260.
FRAMES_TAXABLE = 50000.0
FRAMES_TAX = 2500.0
FRAMES_TOTAL = 52500.0
RENT_TAXABLE = 7000.0
RENT_TAX = 1260.0
RENT_TOTAL = 8260.0


def _db() -> StrictDB:
    db = StrictDB()
    db.seed(
        "vendors",
        [
            {
                "vendor_id": "V-G1",
                "trade_name": "Frames Wala",
                "gstin": SUP_MH,
                "credit_days": 30,
            },
            {
                "vendor_id": "V-G2",
                "trade_name": "Other Vendor",
                "gstin": SUP_MH,
                "credit_days": 30,
            },
        ],
    )
    db.seed(
        "entities",
        [
            {
                "entity_id": "E1",
                "name": "Better Vision",
                "gstins": [{"gstin": BUY_MH, "state_code": "27", "is_primary": True}],
            }
        ],
    )
    db.seed("stores", [{"store_id": "S1", "entity_id": "E1"}])
    db.seed("vendor_bills", [])
    db.seed("vendor_payments", [])
    db.seed("vendor_debit_notes", [])
    db.seed("grns", [])
    return db


def _dc_doc(vendor_id="V-G1", status="ACCEPTED", dc_matched=False):
    """The receipt the no-PO Delivery-Challan path posts: no po_id at all."""
    return {
        "grn_id": "DC-OTC-1",
        "grn_number": "RCPT-DC-1",
        "po_id": None,
        "vendor_id": vendor_id,
        "store_id": "S1",
        "status": status,
        "grn_subtype": "DELIVERY_CHALLAN",
        "dc_number": "DC/26/08/9",
        "dc_date": "2026-08-28",
        "dc_matched": dc_matched,
        "linked_bulk_invoice_id": None,
        "items": [
            {
                "product_id": "P-FR1",
                "product_name": "Assorted frame",
                "received_qty": 20,
                "accepted_qty": 20,
                "rejected_qty": 0,
            }
        ],
    }


def _std_grn_doc():
    return {
        "grn_id": "G-STD-1",
        "grn_number": "RCPT-1",
        "po_id": "PO-1",
        "vendor_id": "V-G1",
        "store_id": "S1",
        "status": "ACCEPTED",
        "grn_subtype": "STANDARD",
        "vendor_invoice_no": "FW-881",
        "items": [
            {
                "product_id": "P-FR1",
                "received_qty": 20,
                "accepted_qty": 20,
                "rejected_qty": 0,
            }
        ],
    }


class _VendorRepo:
    def __init__(self, db):
        self._db = db

    def find_by_id(self, vendor_id):
        return self._db.get_collection("vendors").find_one({"vendor_id": vendor_id})


class _GrnRepo:
    def __init__(self, db):
        self._db = db

    def find_by_id(self, grn_id):
        return self._db.get_collection("grns").find_one({"grn_id": grn_id})


def _wire(db):
    vend._get_db = lambda: db
    vend.get_vendor_repository = lambda: _VendorRepo(db)
    vend.get_grn_repository = lambda: _GrnRepo(db)
    pi._get_db = lambda: db
    pi.get_vendor_repository = lambda: _VendorRepo(db)
    pi.get_grn_repository = lambda: _GrnRepo(db)
    pi.get_purchase_order_repository = lambda: None
    pi.get_audit_repository = lambda: None


@pytest.fixture(autouse=True)
def _restore():
    saved_v = (vend._get_db, vend.get_vendor_repository, vend.get_grn_repository)
    saved_pi = (
        pi._get_db,
        pi.get_vendor_repository,
        pi.get_grn_repository,
        pi.get_purchase_order_repository,
        pi.get_audit_repository,
    )
    yield
    (vend._get_db, vend.get_vendor_repository, vend.get_grn_repository) = saved_v
    (
        pi._get_db,
        pi.get_vendor_repository,
        pi.get_grn_repository,
        pi.get_purchase_order_repository,
        pi.get_audit_repository,
    ) = saved_pi


def _client(router, prefix):
    app = FastAPI()
    app.include_router(router, prefix=prefix)

    async def _u():
        return {
            "user_id": "u1",
            "full_name": "T",
            "username": "t",
            "roles": ["ACCOUNTANT"],
            "store_ids": ["S1"],
            "active_store_id": "S1",
            "discount_cap": None,
        }

    app.dependency_overrides[get_current_user] = _u
    return TestClient(app)


def _vcli():
    return _client(vend.router, "/api/v1/vendors")


def _bills(db):
    return db.get_collection("vendor_bills").docs


def _payable(db):
    return round(sum(float(b.get("outstanding") or 0) for b in _bills(db)), 2)


def _frames_payload(**over):
    body = {
        "bill_number": "FW-2081",
        "bill_date": "2026-08-28",
        "taxable_amount": FRAMES_TAXABLE,
        "tax_amount": FRAMES_TAX,
        "total_amount": FRAMES_TOTAL,
        "notes": "20 pcs assorted frames",
    }
    body.update(over)
    return body


# ===========================================================================
# The header-bill door: the declaration is REQUIRED, and GOODS needs a receipt
# ===========================================================================


class TestHeaderBillMustDeclareItsKind:
    def test_the_audit_repro_no_longer_books(self):
        """Rs 52,500 of frames in prose, no receipt, no declaration -- the
        exact request the audit booked 201 -- now refuses and stores NOTHING."""
        db = _db()
        _wire(db)
        r = _vcli().post("/api/v1/vendors/V-G1/bills", json=_frames_payload())
        assert r.status_code == 422, r.text
        assert r.json()["detail"]["code"] == "BILL_KIND_REQUIRED"
        assert _bills(db) == []
        assert _payable(db) == 0.0

    def test_declared_goods_without_a_receipt_refuses_naming_the_dc_route(self):
        db = _db()
        _wire(db)
        r = _vcli().post(
            "/api/v1/vendors/V-G1/bills", json=_frames_payload(bill_kind="GOODS")
        )
        assert r.status_code == 422, r.text
        d = r.json()["detail"]
        assert d["code"] == "GRN_LINK_REQUIRED"
        # The way out must be one the screens can actually walk now.
        assert "Delivery Challan" in d["message"]
        assert "Goods Receipt" in d["message"]
        assert _bills(db) == []

    def test_a_declared_services_bill_books_exactly_as_before(self):
        db = _db()
        _wire(db)
        r = _vcli().post(
            "/api/v1/vendors/V-G1/bills",
            json={
                "bill_number": "RENT-08",
                "bill_date": "2026-08-28",
                "taxable_amount": RENT_TAXABLE,
                "tax_amount": RENT_TAX,
                "total_amount": RENT_TOTAL,
                "bill_kind": "SERVICES",
                "notes": "August shop rent",
            },
        )
        assert r.status_code == 201, r.text
        assert _payable(db) == RENT_TOTAL
        assert _bills(db)[0]["bill_kind"] == "SERVICES"
        assert _bills(db)[0]["grn_id"] is None

    def test_a_goods_bill_linking_a_standard_receipt_books_and_is_stamped_goods(self):
        db = _db()
        db.get_collection("grns").insert_one(_std_grn_doc())
        _wire(db)
        r = _vcli().post(
            "/api/v1/vendors/V-G1/bills",
            json=_frames_payload(bill_kind="GOODS", grn_id="G-STD-1"),
        )
        assert r.status_code == 201, r.text
        assert _payable(db) == FRAMES_TOTAL
        assert _bills(db)[0]["bill_kind"] == "GOODS"
        assert _bills(db)[0]["grn_id"] == "G-STD-1"

    def test_naming_a_po_is_a_goods_signal_even_declared_services(self):
        """The verifier booked Rs 52,500 receipt-less by declaring SERVICES
        while NAMING a purchase order -- a goods signal the software can
        see, and the exact shape the line-detail invoice door refuses.
        The two doors must give one answer to one signal."""
        db = _db()
        _wire(db)
        r = _vcli().post(
            "/api/v1/vendors/V-G1/bills",
            json=_frames_payload(bill_kind="SERVICES", po_id="PO-77"),
        )
        assert r.status_code == 422, r.text
        assert r.json()["detail"]["code"] == "GRN_LINK_REQUIRED"
        assert "purchase order" in r.json()["detail"]["message"].lower()
        assert _bills(db) == []
        assert _payable(db) == 0.0

    def test_a_receipt_linked_bill_is_stamped_goods_whatever_was_declared(self):
        """The stored bill_kind is a FACT derived from the receipt, not the
        caller's word: a bill linking a goods receipt is GOODS even if the
        caller typed SERVICES. Pinned because disabling the stamp survived
        the whole suite (verifier, 2026-08-30)."""
        db = _db()
        db.get_collection("grns").insert_one(_std_grn_doc())
        _wire(db)
        r = _vcli().post(
            "/api/v1/vendors/V-G1/bills",
            json=_frames_payload(bill_kind="SERVICES", grn_id="G-STD-1"),
        )
        assert r.status_code == 201, r.text
        assert _bills(db)[0]["bill_kind"] == "GOODS"

    def test_a_typo_kind_is_refused_with_the_allowed_values(self):
        db = _db()
        _wire(db)
        r = _vcli().post(
            "/api/v1/vendors/V-G1/bills", json=_frames_payload(bill_kind="GOODZ")
        )
        assert r.status_code == 422, r.text
        assert "GOODS or SERVICES" in r.text
        assert _bills(db) == []


# ===========================================================================
# The header-bill door accepts a Delivery Challan -- and CLAIMS it
# ===========================================================================


def _dc_row(db):
    return db.get_collection("grns").find_one({"grn_id": "DC-OTC-1"})


class TestHeaderBillOnADeliveryChallan:
    def test_the_over_the_counter_purchase_bills_end_to_end(self):
        """The launch-week story: 20 frames bought over the counter, received
        as a no-PO DC, billed Rs 52,500 from the Cash Flow form. The bill
        stores the link and the DC is stamped matched to it."""
        db = _db()
        db.get_collection("grns").insert_one(_dc_doc())
        _wire(db)
        r = _vcli().post(
            "/api/v1/vendors/V-G1/bills",
            json=_frames_payload(bill_kind="GOODS", grn_id="DC-OTC-1"),
        )
        assert r.status_code == 201, r.text
        assert _payable(db) == FRAMES_TOTAL
        bill = _bills(db)[0]
        assert bill["grn_id"] == "DC-OTC-1"
        assert bill["bill_kind"] == "GOODS"
        dc = _dc_row(db)
        assert dc["dc_matched"] is True
        assert dc["linked_bulk_invoice_id"] == bill["bill_id"]

    def test_the_same_dc_cannot_take_a_second_header_bill(self):
        db = _db()
        db.get_collection("grns").insert_one(_dc_doc())
        _wire(db)
        cli = _vcli()
        first = cli.post(
            "/api/v1/vendors/V-G1/bills",
            json=_frames_payload(bill_kind="GOODS", grn_id="DC-OTC-1"),
        )
        assert first.status_code == 201, first.text
        second = cli.post(
            "/api/v1/vendors/V-G1/bills",
            json=_frames_payload(
                bill_number="FW-2082", bill_kind="GOODS", grn_id="DC-OTC-1"
            ),
        )
        assert second.status_code == 409, second.text
        assert len(_bills(db)) == 1
        assert _payable(db) == FRAMES_TOTAL

    def test_a_header_billed_dc_cannot_be_consolidated_again(self):
        """The other direction of the double-bill: after a header bill claims
        the DC, a /from-dcs consolidated invoice on the same DC must refuse --
        or the same 20 frames are paid for twice (Rs 105,000 vs Rs 52,500)."""
        db = _db()
        db.get_collection("grns").insert_one(_dc_doc())
        _wire(db)
        hdr = _vcli().post(
            "/api/v1/vendors/V-G1/bills",
            json=_frames_payload(bill_kind="GOODS", grn_id="DC-OTC-1"),
        )
        assert hdr.status_code == 201, hdr.text

        pcli = _client(pi.router, "/api/v1/vendors/purchase-invoices")
        r = pcli.post(
            "/api/v1/vendors/purchase-invoices",
            json={
                "vendor_id": "V-G1",
                "invoice_number": "FW-BULK-1",
                "invoice_date": "2026-08-29",
                "recipient_entity_id": "E1",
                "linked_dc_ids": ["DC-OTC-1"],
                "lines": [
                    {
                        "product_id": "P-FR1",
                        "description": "Assorted frame",
                        "hsn": "9003",
                        "qty": 20,
                        "unit_price": 2500.0,
                        "gst_rate": 5,
                    }
                ],
            },
        )
        assert r.status_code == 409, r.text
        assert "already matched" in r.text.lower()
        assert len(_bills(db)) == 1
        assert _payable(db) == FRAMES_TOTAL

    def test_another_vendors_dc_is_refused_and_left_unclaimed(self):
        db = _db()
        db.get_collection("grns").insert_one(_dc_doc(vendor_id="V-G2"))
        _wire(db)
        r = _vcli().post(
            "/api/v1/vendors/V-G1/bills",
            json=_frames_payload(bill_kind="GOODS", grn_id="DC-OTC-1"),
        )
        assert r.status_code == 409, r.text
        assert r.json()["detail"]["code"] == "mixed_vendors"
        assert _bills(db) == []
        assert _dc_row(db)["dc_matched"] is False

    def test_an_unaccepted_dc_is_refused(self):
        db = _db()
        db.get_collection("grns").insert_one(_dc_doc(status="PENDING"))
        _wire(db)
        r = _vcli().post(
            "/api/v1/vendors/V-G1/bills",
            json=_frames_payload(bill_kind="GOODS", grn_id="DC-OTC-1"),
        )
        assert r.status_code == 400, r.text
        assert "accepted" in r.text.lower()
        assert _bills(db) == []

    def test_a_failed_insert_gives_the_dc_back(self):
        """A claim with no bill behind it would leave the receipt looking
        billed forever; the release must return it, and a retry must book."""
        db = _db()
        db.get_collection("grns").insert_one(_dc_doc())
        _wire(db)
        bills = db.get_collection("vendor_bills")

        def _boom(_doc):
            raise RuntimeError("disk on fire")

        bills.insert_one = _boom
        r = _vcli().post(
            "/api/v1/vendors/V-G1/bills",
            json=_frames_payload(bill_kind="GOODS", grn_id="DC-OTC-1"),
        )
        assert r.status_code == 500, r.text
        assert _bills(db) == []
        dc = _dc_row(db)
        assert dc["dc_matched"] is False, "the DC is stuck claimed with no bill"
        assert dc["linked_bulk_invoice_id"] is None

        del bills.insert_one  # repair -> class implementation
        retry = _vcli().post(
            "/api/v1/vendors/V-G1/bills",
            json=_frames_payload(bill_kind="GOODS", grn_id="DC-OTC-1"),
        )
        assert retry.status_code == 201, retry.text
        assert _payable(db) == FRAMES_TOTAL

    def test_two_racing_header_bills_on_one_dc_book_exactly_once(self):
        """Both requests pass the read-time checks on the same unmatched DC;
        the guarded dc_matched stamp is what must let exactly one through.
        The rival runs to completion inside the in-flight request's DC read."""
        db = _db()
        db.get_collection("grns").insert_one(_dc_doc())
        _wire(db)
        cli = _vcli()
        grns = db.get_collection("grns")
        real_find_one = grns.find_one
        state = {"fired": False, "rival": None}

        def racing_find_one(*args, **kwargs):
            row = real_find_one(*args, **kwargs)  # read BEFORE the rival wins
            if not state["fired"]:
                state["fired"] = True
                state["rival"] = cli.post(
                    "/api/v1/vendors/V-G1/bills",
                    json=_frames_payload(
                        bill_number="FW-RIVAL", bill_kind="GOODS", grn_id="DC-OTC-1"
                    ),
                )
            return row

        grns.find_one = racing_find_one
        try:
            in_flight = cli.post(
                "/api/v1/vendors/V-G1/bills",
                json=_frames_payload(
                    bill_number="FW-INFLIGHT", bill_kind="GOODS", grn_id="DC-OTC-1"
                ),
            )
        finally:
            grns.find_one = real_find_one

        assert state["fired"], "the race never interleaved -- proves nothing"
        assert state["rival"].status_code == 201, state["rival"].text
        assert in_flight.status_code == 409, (
            "both header bills took the same DC: %s %s"
            % (in_flight.status_code, in_flight.text)
        )
        assert len(_bills(db)) == 1
        assert _bills(db)[0]["bill_number"] == "FW-RIVAL"
        assert _payable(db) == FRAMES_TOTAL


# ===========================================================================
# One rule, two doors: the drift tripwire
# ===========================================================================


class TestBothDoorsEnforceTheSameRule:
    def test_goods_with_no_receipt_refuses_identically_on_both_doors(self):
        """The header door and the invoice door each gate a goods bill on its
        receipt. The CODE and the named way out must be the same on both, so
        neither door can quietly become the softer one again."""
        db = _db()
        _wire(db)
        hdr = _vcli().post(
            "/api/v1/vendors/V-G1/bills", json=_frames_payload(bill_kind="GOODS")
        )
        inv = _client(pi.router, "/api/v1/vendors/purchase-invoices").post(
            "/api/v1/vendors/purchase-invoices",
            json={
                "vendor_id": "V-G1",
                "invoice_number": "FW-2083",
                "invoice_date": "2026-08-28",
                "recipient_entity_id": "E1",
                "bill_kind": "GOODS",
                "lines": [
                    {
                        "description": "20 pcs assorted frames",
                        "qty": 20,
                        "unit_price": 2500.0,
                        "gst_rate": 5,
                    }
                ],
            },
        )
        assert hdr.status_code == 422 and inv.status_code == 422
        d1, d2 = hdr.json()["detail"], inv.json()["detail"]
        assert d1["code"] == d2["code"] == "GRN_LINK_REQUIRED"
        for msg in (d1["message"], d2["message"]):
            assert "Delivery Challan" in msg
            assert "Goods Receipt" in msg
        assert _bills(db) == []

    def test_missing_declaration_refuses_identically_on_both_doors(self):
        db = _db()
        _wire(db)
        hdr = _vcli().post("/api/v1/vendors/V-G1/bills", json=_frames_payload())
        inv = _client(pi.router, "/api/v1/vendors/purchase-invoices").post(
            "/api/v1/vendors/purchase-invoices",
            json={
                "vendor_id": "V-G1",
                "invoice_number": "FW-2084",
                "invoice_date": "2026-08-28",
                "recipient_entity_id": "E1",
                "lines": [
                    {
                        "description": "20 pcs assorted frames",
                        "qty": 20,
                        "unit_price": 2500.0,
                        "gst_rate": 5,
                    }
                ],
            },
        )
        assert hdr.status_code == 422 and inv.status_code == 422
        assert (
            hdr.json()["detail"]["code"]
            == inv.json()["detail"]["code"]
            == "BILL_KIND_REQUIRED"
        )
        assert _bills(db) == []

    def test_the_two_doors_share_one_normaliser(self):
        """Both schemas must route through ap_engine.normalize_bill_kind --
        a copy in either router is the one-rule-two-implementations defect."""
        assert (
            vend.VendorBillCreate(
                bill_number="X",
                bill_date="2026-08-28",
                taxable_amount=1.0,
                tax_amount=0.0,
                total_amount=1.0,
                bill_kind="services",
            ).bill_kind
            == "SERVICES"
        )
        assert (
            pi.PurchaseInvoiceCreate(
                vendor_id="V",
                invoice_number="X",
                invoice_date="2026-08-28",
                lines=[{"description": "rent", "qty": 1, "unit_price": 1.0}],
                bill_kind="expense",
            ).bill_kind
            == "SERVICES"
        )
        assert normalize_bill_kind(" goods ") == "GOODS"
        assert normalize_bill_kind("") is None
        assert normalize_bill_kind(None) is None
        with pytest.raises(ValueError):
            normalize_bill_kind("CAPEX")


# ===========================================================================
# Legacy rows: stored bills with no bill_kind read as-is, everywhere
# ===========================================================================

_LEGACY_BILL = {
    # The exact shape create_vendor_bill wrote before this change -- no
    # bill_kind key at all, header-only, no receipt. Values chosen distinct.
    "bill_id": "LEG-1",
    "vendor_id": "V-G1",
    "vendor_name": "Frames Wala",
    "bill_number": "OLD-77",
    "bill_date": "2026-07-11",
    "due_date": "2026-08-10",
    "credit_days": 30,
    "taxable_amount": 11000.0,
    "tax_amount": 550.0,
    "total_amount": 11550.0,
    "outstanding": 11550.0,
    "po_id": None,
    "grn_id": None,
    "notes": "pre-gate row",
    "status": "OUTSTANDING",
    "created_by": "u0",
    "created_at": "2026-07-11T10:00:00",
}


class TestLegacyBillsReadAsIs:
    def _seeded(self):
        db = _db()
        db.get_collection("vendor_bills").insert_one(dict(_LEGACY_BILL))
        _wire(db)
        return db, _vcli()

    def test_the_bills_list_returns_the_legacy_row_unchanged(self):
        _, cli = self._seeded()
        r = cli.get("/api/v1/vendors/V-G1/bills")
        assert r.status_code == 200, r.text
        rows = r.json()["bills"]
        assert len(rows) == 1
        assert rows[0]["bill_number"] == "OLD-77"
        assert rows[0]["total_amount"] == 11550.0
        assert "bill_kind" not in rows[0], "no restatement of stored rows"

    def test_the_ledger_totals_the_legacy_row(self):
        _, cli = self._seeded()
        r = cli.get("/api/v1/vendors/V-G1/ledger")
        assert r.status_code == 200, r.text
        led = r.json()["ledger"]
        assert led["total_billed"] == 11550.0
        assert led["closing_balance"] == 11550.0

    def test_ap_aging_buckets_the_legacy_row(self):
        _, cli = self._seeded()
        r = cli.get("/api/v1/vendors/ap-aging")
        assert r.status_code == 200, r.text
        vendors = r.json()["vendors"]
        assert len(vendors) == 1
        assert vendors[0]["vendor_id"] == "V-G1"
        assert vendors[0]["net_payable"] == 11550.0

    def test_a_payment_still_allocates_against_the_legacy_row(self):
        db, cli = self._seeded()
        r = cli.post(
            "/api/v1/vendors/V-G1/payments",
            json={
                "amount": 11550.0,
                "payment_date": "2026-08-28",
                "mode": "BANK",
                "bill_id": "LEG-1",
            },
        )
        assert r.status_code == 201, r.text
        bill = db.get_collection("vendor_bills").find_one({"bill_id": "LEG-1"})
        assert bill["status"] == "PAID"
        assert bill["outstanding"] == 0.0
