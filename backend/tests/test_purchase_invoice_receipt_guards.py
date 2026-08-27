"""
IMS 2.0 - Purchase invoice: GOODS-RECEIPT guards on the single-GRN path
=======================================================================
Two money leaks lived on the single-GRN branch of create_purchase_invoice --
the branch taken when grn_id is set and linked_dc_ids is empty. The DC branch
next to it had blocked both since F9; the two branches had simply drifted.

LEAK 1 -- A GOODS RECEIPT COULD BE BILLED REPEATEDLY.
  One 20-unit receipt, billed three times: three 201s, every one
  match_status=MATCHED with exceptions=[], Rs 226,800 of payable booked against
  Rs 75,600 of goods. There was no "already invoiced" control of any kind.
  (The DC branch 409s on dc_matched.)

LEAK 2 -- A RECEIPT FROM A DIFFERENT VENDOR WAS ACCEPTED.
  A bill stored under vendor V-RV1 while its linked receipt belonged to V-RV2:
  201, MATCHED, exceptions []. V-RV1's payable rose and V-RV1's GSTIN claimed
  the ITC for goods V-RV2 supplied. (The DC branch has
  _assert_dcs_single_vendor_store -> 409 mixed_vendors.)

THIRD SIBLING -- vendors.create_vendor_bill (POST /vendors/{id}/bills) accepts
  a grn_id and validated NOTHING about it, so both leaks (plus the ACCEPTED
  gate) were reachable there too.

THE RULE CHOSEN IS "CANNOT BILL MORE THAN WAS RECEIVED", NOT "CANNOT BILL
TWICE" -- part-billing one delivery across two invoices keeps working; the
cumulative quantity is what is capped. See over_billed_products' docstring.
This is also the invoiced-vs-RECEIVED comparison audit finding F2 says the
3-way match never makes (it compares invoiced vs ORDERED, with a 5% tolerance);
there is no percentage tolerance here, only float-noise epsilon.

Every test asserts the STORED ROWS and the RUPEE TOTALS, not just the status
code, and uses StrictDB (strict_fakes) rather than a permissive double.

Run: JWT_SECRET_KEY=test python -m pytest backend/tests/test_purchase_invoice_receipt_guards.py -q
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
from api.services import purchase_match as pmatch  # noqa: E402
from strict_fakes import StrictDB  # noqa: E402


SUP_MH = "27ABCDE1234F1Z5"  # both vendors sit in Maharashtra
BUY_MH = "27ZZZZZ9999Z1Z9"  # our entity, Maharashtra -> intra-state, CGST+SGST

UNIT = 3600.0  # Rs per frame
RECEIVED = 20  # units the receipt accepted
# 20 x 3600 = 72,000 taxable; 5% GST = 3,600; Rs 75,600 is the WHOLE receipt.
RECEIPT_TAXABLE = 72000.0
RECEIPT_TOTAL = 75600.0


# ---------------------------------------------------------------------------
# Environment: strict in-memory DB + a GRN/PO repo pair
# ---------------------------------------------------------------------------


def _db() -> StrictDB:
    db = StrictDB()
    db.seed(
        "vendors",
        [
            {
                "vendor_id": "V-RV1",
                "trade_name": "Vendor One",
                "gstin": SUP_MH,
                "credit_days": 30,
            },
            {
                "vendor_id": "V-RV2",
                "trade_name": "Vendor Two",
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
    return db


def _grn_doc(vendor_id="V-RV1", status="ACCEPTED", accepted=RECEIVED):
    return {
        "grn_id": "G-RV1",
        "grn_number": "GRN-1",
        "po_id": "PO-RV1",
        "vendor_id": vendor_id,
        "store_id": "S1",
        "status": status,
        "grn_subtype": "STANDARD",
        "vendor_invoice_no": "INV-RV-1",
        "vendor_invoice_date": "2026-08-01",
        "items": [
            {
                "product_id": "P1",
                "product_name": "Frame X",
                "received_qty": accepted,
                "accepted_qty": accepted,
                "rejected_qty": 0,
            }
        ],
    }


def _wire(db, grn):
    """Point the purchase-invoice router at the strict DB + a GRN/PO repo."""

    class _GrnRepo:
        def find_by_id(self, grn_id):
            return dict(grn) if grn_id == grn["grn_id"] else None

    class _PoRepo:
        def find_by_id(self, po_id):
            if po_id != "PO-RV1":
                return None
            return {
                "po_id": "PO-RV1",
                "vendor_id": "V-RV1",
                "items": [
                    {
                        "product_id": "P1",
                        "product_name": "Frame X",
                        "quantity": RECEIVED,
                        "unit_price": UNIT,
                    }
                ],
            }

    pi._get_db = lambda: db
    pi.get_vendor_repository = lambda: None
    pi.get_purchase_order_repository = lambda: _PoRepo()
    pi.get_grn_repository = lambda: _GrnRepo()
    pi.get_audit_repository = lambda: None


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


@pytest.fixture(autouse=True)
def _restore():
    """Restore every router global these tests monkeypatch."""
    saved_pi = (
        pi._get_db,
        pi.get_vendor_repository,
        pi.get_purchase_order_repository,
        pi.get_grn_repository,
        pi.get_audit_repository,
    )
    saved_v = (vend._get_db, vend.get_vendor_repository)
    yield
    (
        pi._get_db,
        pi.get_vendor_repository,
        pi.get_purchase_order_repository,
        pi.get_grn_repository,
        pi.get_audit_repository,
    ) = saved_pi
    (vend._get_db, vend.get_vendor_repository) = saved_v


def _invoice(vendor_id="V-RV1", invoice_number="INV-1", qty=RECEIVED):
    return {
        "vendor_id": vendor_id,
        "invoice_number": invoice_number,
        "invoice_date": "2026-08-01",
        "recipient_entity_id": "E1",
        "po_id": "PO-RV1",
        "grn_id": "G-RV1",
        "lines": [
            {
                "product_id": "P1",
                "description": "Frame X",
                "hsn": "9003",
                "qty": qty,
                "unit_price": UNIT,
                "gst_rate": 5,
            }
        ],
    }


def _bills(db):
    return db.get_collection("vendor_bills").docs


def _payable(db):
    return round(sum(float(b.get("outstanding") or 0) for b in _bills(db)), 2)


def _billed_units(db):
    return sum(
        float(ln.get("qty") or 0) for b in _bills(db) for ln in (b.get("lines") or [])
    )


# ===========================================================================
# LEAK 1 -- one receipt, billed repeatedly
# ===========================================================================


class TestOneReceiptCannotBeBilledBeyondWhatItReceived:
    """REQUIREMENT: the units billed against a goods receipt, ADDED UP across
    every bill linked to it, may never exceed the units that receipt accepted."""

    def test_billing_the_same_receipt_three_times_books_one_receipt_of_payable(self):
        """The reported reproduction, asserted on the ledger rather than the
        status line: 3 x 20 units against a 20-unit receipt must leave ONE bill
        and Rs 75,600 of payable -- not three bills and Rs 226,800."""
        db = _db()
        _wire(db, _grn_doc())
        cli = _client(pi.router, "/api/v1/vendors/purchase-invoices")

        first = cli.post(
            "/api/v1/vendors/purchase-invoices", json=_invoice(invoice_number="INV-1")
        )
        assert first.status_code == 201, first.text

        for n in (2, 3):
            again = cli.post(
                "/api/v1/vendors/purchase-invoices",
                json=_invoice(invoice_number=f"INV-{n}"),
            )
            assert (
                again.status_code == 409
            ), f"bill {n} re-billed the whole receipt: {again.status_code} {again.text}"
            assert again.json()["detail"]["code"] == "grn_over_billed"

        assert len(_bills(db)) == 1, f"only the first bill may exist: {_bills(db)!r}"
        assert _billed_units(db) == RECEIVED
        assert _payable(db) == RECEIPT_TOTAL, (
            f"payable must equal the one receipt (Rs {RECEIPT_TOTAL:,.2f}), "
            f"got Rs {_payable(db):,.2f}"
        )

    def test_the_blocked_bill_names_the_overage_and_the_bill_that_took_the_units(self):
        """A refusal an accountant can act on: which product, how many units the
        receipt accepted, how many are already billed, and by which invoice."""
        db = _db()
        _wire(db, _grn_doc())
        cli = _client(pi.router, "/api/v1/vendors/purchase-invoices")
        cli.post(
            "/api/v1/vendors/purchase-invoices", json=_invoice(invoice_number="INV-1")
        )

        blocked = cli.post(
            "/api/v1/vendors/purchase-invoices", json=_invoice(invoice_number="INV-2")
        )
        detail = blocked.json()["detail"]
        assert detail["grn_id"] == "G-RV1"
        assert detail["already_billed_by"] == ["INV-1"]
        over = detail["products"][0]
        assert over["product_id"] == "P1"
        assert over["accepted_qty"] == RECEIVED
        assert over["already_billed_qty"] == RECEIVED
        assert over["this_bill_qty"] == RECEIVED
        assert over["over_by"] == RECEIVED

    def test_part_billing_one_delivery_across_two_invoices_is_allowed(self):
        """A vendor may bill 12 units now and the remaining 8 later. Both book;
        together they equal the receipt exactly and no more."""
        db = _db()
        _wire(db, _grn_doc())
        cli = _client(pi.router, "/api/v1/vendors/purchase-invoices")

        part1 = cli.post(
            "/api/v1/vendors/purchase-invoices",
            json=_invoice(invoice_number="INV-A", qty=12),
        )
        assert part1.status_code == 201, part1.text
        part2 = cli.post(
            "/api/v1/vendors/purchase-invoices",
            json=_invoice(invoice_number="INV-B", qty=8),
        )
        assert part2.status_code == 201, part2.text

        assert len(_bills(db)) == 2
        assert _billed_units(db) == RECEIVED
        # 12 x 3600 = 43,200 + 5% = 45,360;  8 x 3600 = 28,800 + 5% = 30,240.
        assert round(part1.json()["total_amount"], 2) == 45360.0
        assert round(part2.json()["total_amount"], 2) == 30240.0
        assert _payable(db) == RECEIPT_TOTAL

    def test_the_balance_is_capped_at_what_is_left_unbilled(self):
        """After 12 of 20 are billed, a 10-unit second bill is 2 units over and
        must be refused -- the cap is the BALANCE, not another whole receipt."""
        db = _db()
        _wire(db, _grn_doc())
        cli = _client(pi.router, "/api/v1/vendors/purchase-invoices")
        cli.post(
            "/api/v1/vendors/purchase-invoices",
            json=_invoice(invoice_number="INV-A", qty=12),
        )

        over = cli.post(
            "/api/v1/vendors/purchase-invoices",
            json=_invoice(invoice_number="INV-B", qty=10),
        )
        assert over.status_code == 409, over.text
        assert over.json()["detail"]["products"][0]["over_by"] == 2.0
        assert len(_bills(db)) == 1
        assert _payable(db) == 45360.0

    def test_rejected_units_are_not_billable(self):
        """The cap is ACCEPTED qty, so units the store rejected cannot be billed
        even on the first invoice (audit F2: the 3-way match compares invoiced
        vs ORDERED and lets rejected goods inside 5% be paid for silently)."""
        db = _db()
        _wire(db, _grn_doc(accepted=18))  # 20 received, 2 rejected -> 18 accepted
        cli = _client(pi.router, "/api/v1/vendors/purchase-invoices")

        r = cli.post(
            "/api/v1/vendors/purchase-invoices",
            json=_invoice(invoice_number="INV-1", qty=20),
        )
        assert r.status_code == 409, r.text
        assert r.json()["detail"]["products"][0]["over_by"] == 2.0
        assert _bills(db) == []

    def test_a_bill_with_no_receipt_link_is_untouched(self):
        """No grn_id -> nothing to cap against; the guard must not fire."""
        db = _db()
        _wire(db, _grn_doc())
        cli = _client(pi.router, "/api/v1/vendors/purchase-invoices")
        body = _invoice(invoice_number="INV-FREE")
        body.pop("grn_id")
        body.pop("po_id")
        r = cli.post("/api/v1/vendors/purchase-invoices", json=body)
        assert r.status_code == 201, r.text
        assert _payable(db) == RECEIPT_TOTAL


# ===========================================================================
# LEAK 2 -- a receipt from a different vendor
# ===========================================================================


class TestReceiptMustBelongToTheVendorBeingBilled:
    """REQUIREMENT: a goods receipt can only be billed by the vendor that
    supplied it -- the single-GRN mirror of the DC path's mixed_vendors 409."""

    def test_billing_another_vendors_receipt_is_refused_and_books_nothing(self):
        db = _db()
        _wire(db, _grn_doc(vendor_id="V-RV2"))  # the receipt is V-RV2's
        cli = _client(pi.router, "/api/v1/vendors/purchase-invoices")

        r = cli.post(
            "/api/v1/vendors/purchase-invoices",
            json=_invoice(vendor_id="V-RV1", invoice_number="INV-XV"),
        )
        assert (
            r.status_code == 409
        ), f"V-RV2's receipt was billed to V-RV1: {r.status_code} {r.text}"
        detail = r.json()["detail"]
        assert detail["code"] == "grn_vendor_mismatch"
        assert detail["grn_vendor_id"] == "V-RV2"
        assert detail["invoice_vendor_id"] == "V-RV1"
        assert _bills(db) == [], "no payable may be booked against the wrong vendor"
        assert _payable(db) == 0.0

    def test_the_supplying_vendor_can_still_bill_its_own_receipt(self):
        """The guard must not block the normal case."""
        db = _db()
        _wire(db, _grn_doc(vendor_id="V-RV2"))
        cli = _client(pi.router, "/api/v1/vendors/purchase-invoices")
        r = cli.post(
            "/api/v1/vendors/purchase-invoices",
            json=_invoice(vendor_id="V-RV2", invoice_number="INV-OK"),
        )
        assert r.status_code == 201, r.text
        assert _bills(db)[0]["vendor_id"] == "V-RV2"
        assert _payable(db) == RECEIPT_TOTAL

    def test_a_legacy_receipt_with_no_vendor_is_not_blocked(self):
        """Mirrors _assert_dcs_single_vendor_store: rows carrying no vendor_id
        are ignored by the check rather than made unbillable."""
        db = _db()
        grn = _grn_doc()
        grn.pop("vendor_id")
        _wire(db, grn)
        cli = _client(pi.router, "/api/v1/vendors/purchase-invoices")
        r = cli.post(
            "/api/v1/vendors/purchase-invoices",
            json=_invoice(vendor_id="V-RV1", invoice_number="INV-LEG"),
        )
        assert r.status_code == 201, r.text


# ===========================================================================
# THIRD SIBLING -- the header-only vendor-bill door
# ===========================================================================


class TestHeaderOnlyBillDoorGuardsItsReceiptLink:
    """POST /vendors/{vendor_id}/bills accepts a grn_id and used to validate
    nothing about it. It carries no lines, so quantities cannot be apportioned:
    the conservative equivalent is that a receipt already carrying a bill may
    not take a second, blind one."""

    def _env(self, grn):
        db = _db()
        _wire(db, grn)
        vend._get_db = lambda: db

        class _VendorRepo:
            def find_by_id(self, vendor_id):
                return db.get_collection("vendors").find_one({"vendor_id": vendor_id})

        vend.get_vendor_repository = lambda: _VendorRepo()
        return db, _client(vend.router, "/api/v1/vendors")

    def _payload(self, bill_number="HB-1", grn_id="G-RV1"):
        body = {
            "bill_number": bill_number,
            "bill_date": "2026-08-01",
            "taxable_amount": RECEIPT_TAXABLE,
            "tax_amount": 3600.0,
            "total_amount": RECEIPT_TOTAL,
        }
        if grn_id:
            body["grn_id"] = grn_id
        return body

    def test_header_bill_cannot_attach_another_vendors_receipt(self):
        db, cli = self._env(_grn_doc(vendor_id="V-RV2"))
        r = cli.post("/api/v1/vendors/V-RV1/bills", json=self._payload())
        assert r.status_code == 409, r.text
        assert r.json()["detail"]["code"] == "grn_vendor_mismatch"
        assert _bills(db) == []

    def test_header_bill_cannot_reuse_a_receipt_that_is_already_billed(self):
        db, cli = self._env(_grn_doc())
        first = cli.post("/api/v1/vendors/V-RV1/bills", json=self._payload("HB-1"))
        assert first.status_code == 201, first.text

        second = cli.post("/api/v1/vendors/V-RV1/bills", json=self._payload("HB-2"))
        assert second.status_code == 409, second.text
        assert second.json()["detail"]["code"] == "grn_already_billed"
        assert second.json()["detail"]["billed_by"] == "HB-1"
        assert len(_bills(db)) == 1
        assert _payable(db) == RECEIPT_TOTAL

    def test_header_bill_cannot_attach_an_unaccepted_receipt(self):
        db, cli = self._env(_grn_doc(status="PENDING"))
        r = cli.post("/api/v1/vendors/V-RV1/bills", json=self._payload())
        assert r.status_code == 400, r.text
        assert "accepted" in r.text.lower()
        assert _bills(db) == []

    def test_a_plain_header_bill_with_no_receipt_link_still_works(self):
        """The only UI caller (Cash Flow -> Record bill) sends no grn_id; that
        path must be exactly as it was."""
        db, cli = self._env(_grn_doc())
        r = cli.post(
            "/api/v1/vendors/V-RV1/bills", json=self._payload("HB-PLAIN", grn_id=None)
        )
        assert r.status_code == 201, r.text
        assert _payable(db) == RECEIPT_TOTAL


# ===========================================================================
# The pure cap math
# ===========================================================================


class TestOverBilledProductsMath:
    def test_no_prior_bills_and_an_exact_bill_is_clean(self):
        assert (
            pmatch.over_billed_products(
                _grn_doc(), [], [{"product_id": "P1", "qty": 20, "unit_price": UNIT}]
            )
            == []
        )

    def test_cumulative_across_prior_bills_is_what_counts(self):
        over = pmatch.over_billed_products(
            _grn_doc(),
            [{"product_id": "P1", "qty": 15, "unit_price": UNIT}],
            [{"product_id": "P1", "qty": 6, "unit_price": UNIT}],
        )
        assert len(over) == 1 and over[0]["over_by"] == 1.0
        assert over[0]["cumulative_qty"] == 21.0

    def test_a_product_the_receipt_never_carried_is_over_billed(self):
        over = pmatch.over_billed_products(
            _grn_doc(), [], [{"product_id": "P9", "qty": 3, "unit_price": UNIT}]
        )
        assert len(over) == 1 and over[0]["accepted_qty"] == 0.0

    def test_lines_with_no_product_id_are_ignored(self):
        """Freight / service / rounding lines carry no receivable quantity."""
        assert (
            pmatch.over_billed_products(
                _grn_doc(),
                [],
                [{"description": "Freight", "qty": 1, "unit_price": 500}],
            )
            == []
        )

    def test_a_receipt_that_cannot_be_keyed_is_not_flagged(self):
        """No product_id anywhere on the receipt -> we cannot prove an overage,
        so we do not invent one."""
        grn = {"grn_id": "G-X", "items": [{"accepted_qty": 5}]}
        assert (
            pmatch.over_billed_products(
                grn, [], [{"product_id": "P1", "qty": 99, "unit_price": UNIT}]
            )
            == []
        )
