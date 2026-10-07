"""
IMS 2.0 - Purchase Invoice (Phase 1) tests
==========================================
Covers the first-class purchase invoice that books AP + ITC from PO + GRN and
FIXES the inter-state classification bug (place_of_supply was read by the ITC
code but written nowhere, so inter-state purchases were mis-booked CGST+SGST).

  ENGINE (pure, no DB):
    * state_code_of / determine_place_of_supply (supplier vs recipient state)
    * per-line CGST/SGST (intra) vs IGST (inter) split, paisa-exact
    * lines_from_grn: accepted GRN qty x PO unit_price/tax_rate, skip rejected
  ROUTER (standalone FastAPI + fake DB, no Mongo):
    * create books AP (due date from credit terms, outstanding, status) and
      WRITES place_of_supply + the split totals
    * INTER-STATE invoice books IGST, intra-state books CGST+SGST  (regression)
    * duplicate vendor invoice -> 409
    * total reconcile guard -> 400
    * create role-gated to ACCOUNTANT/ADMIN; reads AUTHENTICATED
    * from-grn returns a DRAFT (not booked) with prefilled lines + POS
  END-TO-END:
    * a booked inter-state invoice, fed to build_itc_register with the
      recipient entity's state, lands in total_igst (NOT total_cgst/sgst)

Run: JWT_SECRET_KEY=test python -m pytest backend/tests/test_purchase_invoice.py -q
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

from api.services import purchase_invoice_engine as pinv  # noqa: E402
from api.services.itc_reconcile import build_itc_register  # noqa: E402
from api.routers import purchase_invoices as pi_router  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402


# GSTINs whose first two digits are the state code (the only part that matters
# for classification): 27 = Maharashtra, 20 = Jharkhand.
SUP_MH = "27ABCDE1234F1Z5"  # supplier in Maharashtra
SUP_JH = "20ABCDE1234F1Z5"  # supplier in Jharkhand
BUY_JH = "20ZZZZZ9999Z1Z9"  # our entity (recipient) in Jharkhand
BUY_MH = "27ZZZZZ9999Z1Z9"  # our entity (recipient) in Maharashtra


# ===========================================================================
# ENGINE - the ONE invoice-number normaliser (P0-1 launch gate)
# ===========================================================================


class TestNormalizeInvoiceNo:
    """Both duplicate guards -- the GRN create guard (vendors.py) and the
    payable dedupe here -- must compare through THIS one function; two
    spellings of the folding rule is how the payable got booked twice."""

    def test_case_and_punctuation_fold_to_one_identity(self):
        assert (
            pinv.normalize_invoice_no("GO-INV-9007")
            == pinv.normalize_invoice_no("go inv/9007")
            == pinv.normalize_invoice_no(" GO.INV..9007 ")
            == "GOINV9007"
        )

    def test_different_numbers_stay_different(self):
        assert pinv.normalize_invoice_no("INV-9007") != pinv.normalize_invoice_no(
            "INV-9008"
        )

    def test_blank_and_none_are_falsy(self):
        assert pinv.normalize_invoice_no(None) == ""
        assert pinv.normalize_invoice_no("") == ""
        assert pinv.normalize_invoice_no("   ") == ""


# ===========================================================================
# ENGINE - state codes + place-of-supply
# ===========================================================================


class TestStateResolution:
    def test_state_code_of_gstin(self):
        assert pinv.state_code_of(SUP_MH) == "27"
        assert pinv.state_code_of(BUY_JH) == "20"

    def test_state_code_of_abbr_and_name(self):
        assert pinv.state_code_of("MH") == "27"
        assert pinv.state_code_of("Jharkhand") == "20"
        assert pinv.state_code_of("27") == "27"

    def test_state_code_of_empty(self):
        assert pinv.state_code_of(None) == ""
        assert pinv.state_code_of("") == ""

    def test_interstate_when_states_differ(self):
        pos, inter = pinv.determine_place_of_supply(SUP_MH, BUY_JH)
        assert pos == "20" and inter is True

    def test_intrastate_when_states_equal(self):
        pos, inter = pinv.determine_place_of_supply(SUP_MH, BUY_MH)
        assert pos == "27" and inter is False

    def test_missing_supplier_defaults_intrastate(self):
        # Buyer known, supplier unknown -> cannot prove a difference -> intra.
        pos, inter = pinv.determine_place_of_supply(None, BUY_JH)
        assert pos == "20" and inter is False

    def test_missing_recipient_no_pos(self):
        pos, inter = pinv.determine_place_of_supply(SUP_MH, None)
        assert pos is None and inter is False

    def test_explicit_pos_override_wins(self):
        # Recipient GSTIN says JH(20) but explicit POS says MH(27) -> intra w/ MH.
        pos, inter = pinv.determine_place_of_supply(SUP_MH, BUY_JH, "27")
        assert pos == "27" and inter is False


# ===========================================================================
# ENGINE - per-line GST split
# ===========================================================================


class TestLineSplit:
    def test_interstate_line_is_all_igst(self):
        s = pinv.split_line_gst(1000, 5, interstate=True)
        assert s["igst"] == 50.0 and s["cgst"] == 0.0 and s["sgst"] == 0.0
        assert s["line_total"] == 1050.0

    def test_intrastate_line_splits_cgst_sgst(self):
        s = pinv.split_line_gst(1000, 5, interstate=False)
        assert s["cgst"] == 25.0 and s["sgst"] == 25.0 and s["igst"] == 0.0

    def test_odd_paise_sum_is_exact(self):
        # taxable 100.20 @5% -> 5.01 tax -> 2.50 + 2.51 (residual) == 5.01.
        s = pinv.split_line_gst(100.20, 5, interstate=False)
        assert s["cgst"] == 2.50 and s["sgst"] == 2.51
        assert round(s["cgst"] + s["sgst"], 2) == s["gst"] == 5.01


class TestComputeInvoice:
    def test_interstate_invoice_books_igst_not_cgst_sgst(self):
        """THE FIX: an inter-state invoice (MH supplier -> JH buyer) computes
        IGST on every line and ZERO CGST/SGST."""
        inv = pinv.compute_invoice(
            [
                {"product_id": "P1", "qty": 10, "unit_price": 100, "gst_rate": 5},
                {"qty": 2, "unit_price": 500, "gst_rate": 18},
            ],
            SUP_MH,
            BUY_JH,
        )
        assert inv["interstate"] is True
        assert inv["place_of_supply"] == "20"
        assert inv["cgst_total"] == 0.0 and inv["sgst_total"] == 0.0
        # 5% of 1000 + 18% of 1000 = 50 + 180 = 230 -> all IGST.
        assert inv["igst_total"] == 230.0
        assert inv["taxable_total"] == 2000.0
        assert inv["total"] == 2230.0

    def test_intrastate_invoice_books_cgst_sgst(self):
        inv = pinv.compute_invoice([{"taxable": 1000, "gst_rate": 5}], SUP_MH, BUY_MH)
        assert inv["interstate"] is False
        assert inv["igst_total"] == 0.0
        assert inv["cgst_total"] == 25.0 and inv["sgst_total"] == 25.0
        assert inv["total"] == 1050.0

    def test_taxable_defaults_to_qty_times_price(self):
        inv = pinv.compute_invoice(
            [{"qty": 3, "unit_price": 200, "gst_rate": 5}], SUP_MH, BUY_MH
        )
        assert inv["taxable_total"] == 600.0

    def test_header_equals_sum_of_lines(self):
        inv = pinv.compute_invoice(
            [
                {"taxable": 333.33, "gst_rate": 5},
                {"taxable": 666.67, "gst_rate": 18},
            ],
            SUP_MH,
            BUY_JH,
        )
        line_igst = round(sum(l["igst"] for l in inv["lines"]), 2)
        assert inv["igst_total"] == line_igst
        line_taxable = round(sum(l["taxable"] for l in inv["lines"]), 2)
        assert inv["taxable_total"] == line_taxable


# ===========================================================================
# ENGINE - lines_from_grn (PO + GRN -> draft lines)
# ===========================================================================


class TestLinesFromGrn:
    def _po(self):
        return {
            "items": [
                {
                    "product_id": "P1",
                    "sku": "SKU1",
                    "unit_price": 120.0,
                    "tax_rate": 5.0,
                },
                {
                    "product_id": "P2",
                    "sku": "SKU2",
                    "unit_price": 800.0,
                    "tax_rate": 18.0,
                },
            ]
        }

    def _grn(self):
        return {
            "items": [
                {"product_id": "P1", "product_name": "Frame X", "accepted_qty": 5},
                {
                    "product_id": "P2",
                    "product_name": "Sun Y",
                    "accepted_qty": 0,
                    "rejected_qty": 2,
                },  # fully rejected -> skipped
            ]
        }

    def test_builds_lines_from_accepted_qty_and_po_price(self):
        lines = pinv.lines_from_grn(self._grn(), self._po())
        assert len(lines) == 1  # rejected P2 skipped
        ln = lines[0]
        assert ln["product_id"] == "P1"
        assert ln["qty"] == 5
        assert ln["unit_price"] == 120.0
        assert ln["gst_rate"] == 5.0
        assert ln["description"] == "Frame X"

    def test_full_flow_grn_to_computed_invoice(self):
        lines = pinv.lines_from_grn(self._grn(), self._po())
        inv = pinv.compute_invoice(lines, SUP_MH, BUY_JH)
        # 5 x 120 = 600 taxable @5% inter-state -> 30 IGST.
        assert inv["taxable_total"] == 600.0
        assert inv["igst_total"] == 30.0
        assert inv["cgst_total"] == 0.0

    def test_no_po_still_builds_lines_with_zero_price(self):
        lines = pinv.lines_from_grn(self._grn(), None)
        assert len(lines) == 1 and lines[0]["unit_price"] == 0.0


# ===========================================================================
# ROUTER - standalone app + fake DB
# ===========================================================================


class _FakeCollection:
    def __init__(self, store):
        self._store = store  # list of dicts

    def find_one(self, flt, projection=None):
        for d in self._store:
            if all(d.get(k) == v for k, v in flt.items()):
                return dict(d)
        return None

    def find(self, flt=None, projection=None):
        flt = flt or {}
        rows = [
            dict(d) for d in self._store if all(d.get(k) == v for k, v in flt.items())
        ]
        return _FakeCursor(rows)

    def insert_one(self, doc):
        self._store.append(dict(doc))
        return type("R", (), {"inserted_id": doc.get("_id")})()


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def limit(self, n):
        return _FakeCursor(self._rows[:n])

    def __iter__(self):
        return iter(self._rows)


class _FakeDB:
    def __init__(self):
        self.collections = {
            "vendor_bills": [],
            "vendors": [
                {
                    "vendor_id": "V1",
                    "trade_name": "Acme Optics",
                    "gstin": SUP_MH,
                    "credit_days": 30,
                },
            ],
            "entities": [
                {
                    "entity_id": "E1",
                    "name": "Better Vision",
                    "gstins": [
                        {"gstin": BUY_JH, "state_code": "20", "is_primary": True}
                    ],
                },
            ],
            # Every shop is stamped with its state at birth (stores.create_store);
            # its GSTIN is then its company's registration for that state.
            "stores": [{"store_id": "S1", "entity_id": "E1", "state_code": "20"}],
        }

    def get_collection(self, name):
        return _FakeCollection(self.collections.setdefault(name, []))


class _StubRepo:
    """A repository whose find_by_id always yields the one doc it was given."""

    def __init__(self, doc):
        self._doc = doc

    def find_by_id(self, _id):
        return dict(self._doc)


# The goods receipt every GOODS bill in this file is booked against. Ruling 15
# refuses a bill whose lines name a product unless it links the receipt that
# says the quantities were counted in, so the shared body carries grn_id "G1"
# and this is the ACCEPTED STANDARD GRN behind it. The tests below are about
# the GST split / AP + ITC booking / role gating / duplicate handling -- the
# receipt is their premise, not their subject.
_ACCEPTED_GRN = {
    "grn_id": "G1",
    "grn_number": "GRN-1",
    "vendor_id": "V1",
    "store_id": "S1",
    "status": "ACCEPTED",
    "grn_subtype": "STANDARD",
    "items": [{"product_id": "P1", "product_name": "Frame X", "accepted_qty": 10}],
}


def _app(db, roles=("ACCOUNTANT",), uid="u1"):
    """Standalone app with the purchase_invoices router + a fake DB injected."""
    app = FastAPI()
    app.include_router(pi_router.router, prefix="/api/v1/vendors/purchase-invoices")

    async def _u():
        return {
            "user_id": uid,
            "full_name": "T",
            "username": "t",
            "roles": list(roles),
            "store_ids": ["S1"],
            "active_store_id": "S1",
            "discount_cap": None,
        }

    app.dependency_overrides[get_current_user] = _u
    # Point the router's DB handle + repos at our fakes (no Mongo / no repo).
    pi_router._get_db = lambda: db
    pi_router.get_vendor_repository = lambda: None
    pi_router.get_purchase_order_repository = lambda: None
    pi_router.get_grn_repository = lambda: _StubRepo(_ACCEPTED_GRN)
    pi_router.get_audit_repository = lambda: None
    return TestClient(app)


@pytest.fixture(autouse=True)
def _restore_router(monkeypatch):
    """Snapshot + restore the router module globals we monkeypatch in _app so
    one test's fakes don't leak into the next."""
    saved = (
        pi_router._get_db,
        pi_router.get_vendor_repository,
        pi_router.get_purchase_order_repository,
        pi_router.get_grn_repository,
        pi_router.get_audit_repository,
    )
    yield
    (
        pi_router._get_db,
        pi_router.get_vendor_repository,
        pi_router.get_purchase_order_repository,
        pi_router.get_grn_repository,
        pi_router.get_audit_repository,
    ) = saved


def _invoice_body(**over):
    body = {
        "vendor_id": "V1",
        "invoice_number": "INV-001",
        "invoice_date": "2026-05-01",
        "recipient_entity_id": "E1",
        # Ruling 15: a bill whose lines NAME a product must link its goods
        # receipt. _ACCEPTED_GRN is that receipt (see _app).
        "grn_id": "G1",
        "lines": [
            {
                "product_id": "P1",
                "description": "Frame X",
                "hsn": "9003",
                "qty": 10,
                "unit_price": 100,
                "gst_rate": 5,
            },
        ],
    }
    body.update(over)
    return body


class TestCreateBooksApAndItc:
    def test_interstate_create_books_igst_and_writes_pos(self):
        """MH supplier -> JH recipient: the booked doc carries IGST + a written
        place_of_supply (the bug fix) and AP fields (due date, outstanding)."""
        db = _FakeDB()
        cli = _app(db)
        r = cli.post("/api/v1/vendors/purchase-invoices", json=_invoice_body())
        assert r.status_code == 201, r.text
        doc = r.json()
        # IGST classification (the fix), not CGST/SGST.
        # place_of_supply stored = the SUPPLIER state (what the ITC register
        # keys on); the legal recipient-side place of supply is kept separately.
        assert doc["place_of_supply"] == "27"  # supplier (MH) -> register test
        assert doc["supply_place_recipient"] == "20"  # legal recipient (JH)
        assert doc["interstate"] is True
        assert doc["igst_total"] == 50.0
        assert doc["cgst_total"] == 0.0 and doc["sgst_total"] == 0.0
        assert doc["tax_amount"] == 50.0 and doc["taxable_amount"] == 1000.0
        # AP booking.
        assert doc["status"] == "OUTSTANDING"
        assert doc["outstanding"] == 1050.0
        assert doc["due_date"] == "2026-05-31"  # 2026-05-01 + 30 credit days
        assert doc["doc_type"] == "PURCHASE_INVOICE"
        assert doc["vendor_gstin"] == SUP_MH
        assert doc["recipient_gstin"] == BUY_JH
        # Persisted into vendor_bills.
        assert len(db.collections["vendor_bills"]) == 1

    def test_intrastate_create_books_cgst_sgst(self):
        db = _FakeDB()
        # Make the recipient entity (and its shop) Maharashtra so it matches
        # the MH supplier.
        db.collections["entities"][0]["gstins"][0] = {
            "gstin": BUY_MH,
            "state_code": "27",
            "is_primary": True,
        }
        db.collections["stores"][0]["state_code"] = "27"
        cli = _app(db)
        r = cli.post("/api/v1/vendors/purchase-invoices", json=_invoice_body())
        assert r.status_code == 201, r.text
        doc = r.json()
        # Supplier MH(27) == recipient MH(27) -> intra-state.
        assert doc["place_of_supply"] == "27"
        assert doc["interstate"] is False
        assert doc["igst_total"] == 0.0
        assert doc["cgst_total"] == 25.0 and doc["sgst_total"] == 25.0

    def test_duplicate_invoice_number_409(self):
        db = _FakeDB()
        cli = _app(db)
        r1 = cli.post("/api/v1/vendors/purchase-invoices", json=_invoice_body())
        assert r1.status_code == 201
        r2 = cli.post("/api/v1/vendors/purchase-invoices", json=_invoice_body())
        assert r2.status_code == 409, r2.text
        assert "already" in r2.json()["detail"].lower()

    def test_punctuation_variant_invoice_number_still_409(self):
        """P0-1 (launch gate), money side: 'GO-INV-9007' then 'go inv/9007'
        is the SAME vendor bill retyped with different separators -- the old
        exact-string dedupe booked the payable twice (Rs 63,000 x2 in the
        gate's repro). The comparison must be case/punctuation-folded, and
        the 409 must name the variant already on record. Each books half the
        receipt (5 of 10 accepted) so the over-bill cap stays silent and the
        DEDUPE alone must refuse."""
        db = _FakeDB()
        cli = _app(db)
        half = dict(_invoice_body(invoice_number="GO-INV-9007"))
        half["lines"] = [dict(half["lines"][0], qty=5)]
        half["total"] = 525.0
        r1 = cli.post("/api/v1/vendors/purchase-invoices", json=half)
        assert r1.status_code == 201, r1.text
        variant = dict(half, invoice_number="go inv/9007")
        r2 = cli.post("/api/v1/vendors/purchase-invoices", json=variant)
        assert r2.status_code == 409, r2.text
        detail = r2.json()["detail"]
        assert "already" in detail.lower()
        assert "GO-INV-9007" in detail  # names the recorded variant

    def test_genuinely_different_invoice_numbers_both_book(self):
        """The fold must not over-match: INV-9007 and INV-9008 are two real
        bills (part-billing one receipt) and both must book."""
        db = _FakeDB()
        cli = _app(db)
        half = dict(_invoice_body(invoice_number="INV-9007"))
        half["lines"] = [dict(half["lines"][0], qty=5)]
        half["total"] = 525.0
        assert (
            cli.post("/api/v1/vendors/purchase-invoices", json=half).status_code
            == 201
        )
        other = dict(half, invoice_number="INV-9008")
        r2 = cli.post("/api/v1/vendors/purchase-invoices", json=other)
        assert r2.status_code == 201, r2.text

    def test_total_reconcile_guard_400(self):
        db = _FakeDB()
        cli = _app(db)
        # Real total is 1050; claim 9999 -> 400.
        r = cli.post(
            "/api/v1/vendors/purchase-invoices",
            json=_invoice_body(total=9999),
        )
        assert r.status_code == 400, r.text
        assert "reconcile" in r.json()["detail"].lower()

    def test_total_reconcile_within_slack_ok(self):
        db = _FakeDB()
        cli = _app(db)
        r = cli.post(
            "/api/v1/vendors/purchase-invoices",
            json=_invoice_body(total=1050.5),  # within Rs 1
        )
        assert r.status_code == 201, r.text

    def test_typed_recipient_gstin_cannot_override_the_receiving_shop(self):
        """Round 12 item 1: goods received at S1 (Jharkhand registration) are
        S1's, whatever number is typed. A company's OTHER registration typed
        against them is refused, not booked onto the other return."""
        db = _FakeDB()
        db.collections["entities"][0]["gstins"].append(
            {"gstin": BUY_MH, "state_code": "27", "is_primary": False}
        )
        cli = _app(db)
        r = cli.post(
            "/api/v1/vendors/purchase-invoices",
            json=_invoice_body(recipient_gstin=BUY_MH, recipient_entity_id=None),
        )
        assert r.status_code == 422, r.text
        assert r.json()["detail"]["code"] == "RECIPIENT_GSTIN_NOT_RECEIPT_SHOP"
        assert BUY_MH in r.json()["detail"]["message"]
        assert not [x for x in db.collections.get("purchase_invoices", [])]

    def test_receiving_shop_registration_is_the_recipient(self):
        """The same GRN at a Maharashtra shop books intra-state on the MH
        registration with the MH supplier (typed number absent or equal)."""
        for typed in (None, BUY_MH):
            db = _FakeDB()
            db.collections["entities"][0]["gstins"].append(
                {"gstin": BUY_MH, "state_code": "27", "is_primary": False}
            )
            db.collections["stores"][0].update({"state_code": "27", "gstin": BUY_MH})
            cli = _app(db)
            body = _invoice_body(recipient_entity_id=None)
            body.pop("recipient_gstin", None)
            if typed:
                body["recipient_gstin"] = typed
            r = cli.post("/api/v1/vendors/purchase-invoices", json=body)
            assert r.status_code == 201, r.text
            doc = r.json()
            assert doc["recipient_gstin"] == BUY_MH and doc["interstate"] is False
            assert doc["cgst_total"] == 25.0 and doc["igst_total"] == 0.0


class TestRoleGating:
    def test_sales_staff_cannot_create(self):
        db = _FakeDB()
        cli = _app(db, roles=("SALES_STAFF",))
        r = cli.post("/api/v1/vendors/purchase-invoices", json=_invoice_body())
        assert r.status_code == 403

    def test_optometrist_cannot_create(self):
        db = _FakeDB()
        cli = _app(db, roles=("OPTOMETRIST",))
        r = cli.post("/api/v1/vendors/purchase-invoices", json=_invoice_body())
        assert r.status_code == 403

    def test_admin_can_create(self):
        db = _FakeDB()
        cli = _app(db, roles=("ADMIN",))
        r = cli.post("/api/v1/vendors/purchase-invoices", json=_invoice_body())
        assert r.status_code == 201, r.text

    def test_superadmin_can_create(self):
        db = _FakeDB()
        cli = _app(db, roles=("SUPERADMIN",))
        r = cli.post("/api/v1/vendors/purchase-invoices", json=_invoice_body())
        assert r.status_code == 201, r.text


class TestListAndGet:
    def test_list_returns_every_supplier_bill_but_no_transfer_mirror(self):
        """A header-only bill (the Cash Flow '+ bill' door, no doc_type) is a
        supplier bill the ITC register and GSTR-3B count, so the list shows
        it too; a stock-transfer mirror is not a supplier bill."""
        db = _FakeDB()
        db.collections["vendor_bills"] += [
            {"bill_id": "legacy1", "vendor_id": "V1", "bill_number": "OLD-1", "total_amount": 500},
            {"bill_id": "m1", "bill_number": "TRF/T1", "source_transfer_id": "T1", "total_amount": 900},
        ]
        cli = _app(db)
        cli.post("/api/v1/vendors/purchase-invoices", json=_invoice_body())
        r = cli.get("/api/v1/vendors/purchase-invoices")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 2
        assert sorted(row["bill_number"] for row in data["purchase_invoices"]) == ["INV-001", "OLD-1"]

    def test_get_by_id(self):
        db = _FakeDB()
        cli = _app(db)
        created = cli.post(
            "/api/v1/vendors/purchase-invoices", json=_invoice_body()
        ).json()
        inv_id = created["invoice_id"]
        r = cli.get(f"/api/v1/vendors/purchase-invoices/{inv_id}")
        assert r.status_code == 200
        assert r.json()["bill_id"] == inv_id

    def test_get_unknown_404(self):
        db = _FakeDB()
        cli = _app(db)
        r = cli.get("/api/v1/vendors/purchase-invoices/nope")
        assert r.status_code == 404


class TestFromGrnDraft:
    def _wire_grn(self, db):
        grn = {
            "grn_id": "G1",
            "grn_number": "GRN-1",
            "po_id": "PO1",
            "vendor_id": "V1",
            "vendor_name": "Acme Optics",
            "store_id": "S1",
            # A standard GRN can only be drafted/billed once ACCEPTED (F3 guard).
            "status": "ACCEPTED",
            "vendor_invoice_no": "INV-FROM-GRN",
            "vendor_invoice_date": "2026-05-02",
            "items": [
                {"product_id": "P1", "product_name": "Frame X", "accepted_qty": 5},
            ],
        }
        po = {
            "po_id": "PO1",
            "vendor_id": "V1",
            "items": [
                {
                    "product_id": "P1",
                    "sku": "SKU1",
                    "unit_price": 120.0,
                    "tax_rate": 5.0,
                },
            ],
        }

        class _Repo:
            def __init__(self, doc):
                self._doc = doc

            def find_by_id(self, _id):
                return dict(self._doc)

        cli = _app(db, roles=("ACCOUNTANT",))
        pi_router.get_grn_repository = lambda: _Repo(grn)
        pi_router.get_purchase_order_repository = lambda: _Repo(po)
        pi_router.get_vendor_repository = lambda: _Repo(db.collections["vendors"][0])
        return cli

    def test_from_grn_returns_unbooked_draft(self):
        db = _FakeDB()
        cli = self._wire_grn(db)
        r = cli.get("/api/v1/vendors/purchase-invoices/from-grn/G1")
        assert r.status_code == 200, r.text
        draft = r.json()
        assert draft["status"] == "DRAFT"
        assert draft["invoice_number"] == "INV-FROM-GRN"
        assert draft["invoice_date"] == "2026-05-02"
        assert draft["po_id"] == "PO1" and draft["grn_id"] == "G1"
        assert len(draft["lines"]) == 1
        assert draft["lines"][0]["qty"] == 5
        assert draft["lines"][0]["unit_price"] == 120.0
        # Inter-state (MH supplier vs JH recipient entity) -> IGST on the draft.
        # place_of_supply mirrors what POST stores (supplier state, MH=27).
        assert draft["place_of_supply"] == "27"
        assert draft["supply_place_recipient"] == "20"
        assert draft["interstate"] is True
        assert draft["igst_total"] == 30.0  # 600 @5%
        # NOT persisted (draft only).
        assert len(db.collections["vendor_bills"]) == 0

    def test_from_grn_role_gated(self):
        db = _FakeDB()
        cli = _app(db, roles=("SALES_STAFF",))
        r = cli.get("/api/v1/vendors/purchase-invoices/from-grn/G1")
        assert r.status_code == 403


# ===========================================================================
# END-TO-END - booked inter-state invoice flows into the ITC register as IGST
# ===========================================================================


def test_booked_interstate_invoice_lands_in_itc_igst():
    """The whole point: a created inter-state purchase invoice, read by the
    ITC register (which reports each bill's stored heads), lands in IGST --
    NOT CGST/SGST."""
    db = _FakeDB()
    cli = _app(db)
    r = cli.post("/api/v1/vendors/purchase-invoices", json=_invoice_body())
    assert r.status_code == 201, r.text

    # The vendor_bills rows exactly as the ITC register reads them (its
    # projection in finance/itc.py): the bill's own stored heads.
    keys = (
        "bill_date", "taxable_amount", "tax_amount", "cgst_total", "sgst_total",
        "igst_total", "vendor_gstin", "recipient_gstin",
    )
    bills = [{k: d.get(k) for k in keys} for d in db.collections["vendor_bills"]]
    # Supplier Maharashtra (27), our GSTIN Jharkhand (20): stored IGST.
    reg = build_itc_register(bills)
    assert reg["total_igst"] == 50.0
    assert reg["total_cgst"] == 0.0 and reg["total_sgst"] == 0.0
    assert reg["total_itc"] == 50.0


def test_regression_without_gstins_would_be_intrastate():
    """Documents the OLD behaviour: a header-only bill with no heads and no
    GSTINs on file is treated intra-state by the register -- which is exactly
    the mis-booking every door now avoids by storing its heads."""
    bills = [
        {"bill_date": "2026-05-01", "taxable_amount": 1000, "tax_amount": 50}
    ]  # no heads, no GSTINs
    reg = build_itc_register(bills)
    assert reg["total_igst"] == 0.0
    assert reg["total_cgst"] == 25.0 and reg["total_sgst"] == 25.0


# ===========================================================================
# Hardening findings F1 (read RBAC) / F3 (standard GRN must be ACCEPTED) /
# F4 (duplicate-invoice race -> DB unique index -> 409).
# ===========================================================================


class TestReadEndpointsAreAccountingOnly:
    """F1: purchase-invoice READS expose supplier bill / AP / GST-ITC / 3-way-
    match data -> restricted to ACCOUNTANT/ADMIN (SUPERADMIN auto-passes)."""

    _READ_PATHS = [
        "/api/v1/vendors/purchase-invoices",
        "/api/v1/vendors/purchase-invoices/config",
        "/api/v1/vendors/purchase-invoices/INV1/match",
        "/api/v1/vendors/purchase-invoices/INV1",
    ]

    def test_sales_staff_403_on_every_read(self):
        cli = _app(_FakeDB(), roles=("SALES_STAFF",))
        for path in self._READ_PATHS:
            r = cli.get(path)
            assert r.status_code == 403, f"{path} -> {r.status_code} {r.text}"

    def test_cashier_403_on_every_read(self):
        cli = _app(_FakeDB(), roles=("CASHIER",))
        for path in self._READ_PATHS:
            assert cli.get(path).status_code == 403, path

    def test_accountant_not_403_on_reads(self):
        cli = _app(_FakeDB(), roles=("ACCOUNTANT",))
        for path in self._READ_PATHS:
            assert cli.get(path).status_code != 403, path

    def test_superadmin_not_403_on_reads(self):
        cli = _app(_FakeDB(), roles=("SUPERADMIN",))
        for path in self._READ_PATHS:
            assert cli.get(path).status_code != 403, path


class TestStandardGrnMustBeAccepted:
    """F3: a STANDARD PO-backed GRN must be ACCEPTED before it can be billed."""

    def _wire_grn(self, status):
        grn = {
            "grn_id": "G9",
            "po_id": "PO1",
            "vendor_id": "V1",
            "store_id": "S1",
            "status": status,
            "grn_subtype": "STANDARD",
            "vendor_invoice_no": "INV-G9",
            "vendor_invoice_date": "2026-05-02",
            "items": [
                {"product_id": "P1", "product_name": "Frame X", "accepted_qty": 10}
            ],
        }

        class _R:
            def find_by_id(self, _id):
                return dict(grn)

        pi_router.get_grn_repository = lambda: _R()

    def test_create_from_pending_grn_400(self):
        cli = _app(_FakeDB())
        self._wire_grn("PENDING")
        r = cli.post(
            "/api/v1/vendors/purchase-invoices",
            json=_invoice_body(grn_id="G9"),
        )
        assert r.status_code == 400, r.text
        assert "accepted" in r.json()["detail"].lower()

    def test_create_from_partially_accepted_grn_400(self):
        cli = _app(_FakeDB())
        self._wire_grn("PARTIALLY_ACCEPTED")
        r = cli.post(
            "/api/v1/vendors/purchase-invoices",
            json=_invoice_body(grn_id="G9"),
        )
        assert r.status_code == 400, r.text

    def test_draft_from_pending_grn_400(self):
        cli = _app(_FakeDB())
        self._wire_grn("PENDING")
        r = cli.get("/api/v1/vendors/purchase-invoices/from-grn/G9")
        assert r.status_code == 400, r.text
        assert "accepted" in r.json()["detail"].lower()


class TestDuplicateInvoiceRaceMaps409:
    """F4: the app-level pre-check can be raced; the UNIQUE partial index is the
    atomic backstop -> the insert loser's DuplicateKeyError maps to 409."""

    def test_duplicate_key_on_insert_returns_409(self):
        db = _FakeDB()

        class _DupErr(Exception):
            pass

        _DupErr.__name__ = "DuplicateKeyError"  # matched by class name

        class _RaisingColl(_FakeCollection):
            def insert_one(self, doc):
                raise _DupErr("E11000 duplicate key")

        orig_get = db.get_collection

        def _patched(name):
            if name == "vendor_bills":
                return _RaisingColl(db.collections.setdefault("vendor_bills", []))
            return orig_get(name)

        db.get_collection = _patched
        cli = _app(db)
        r = cli.post("/api/v1/vendors/purchase-invoices", json=_invoice_body())
        assert r.status_code == 409, r.text
        assert "already" in r.json()["detail"].lower()


# ===========================================================================
# BILL door - the two GST registrations decide the tax head (F6)
# ===========================================================================
# #1032 made a POST with no place_of_supply follow the delivery shop's DECLARED
# state, while the draft, the form and every screen preview read the two GST
# registrations -- and a POST from the form carried the draft's SUPPLIER state
# as "place of supply", booking an IGST bill as CGST+SGST (procurement audit
# F6). One rule now: the supplier's GSTIN state vs our GSTIN's state. Whether a
# shop whose address and registration are in different states should bill on
# its address is the owner's open bill-follows-store decision; until it lands
# the bill follows the registrations, as the form always showed.


class TestBillTaxHeadIsTheRegistrations:
    def test_the_shops_own_registration_decides_not_its_address(self):
        """MH supplier, goods at a shop whose address says Maharashtra but
        whose record carries our Jharkhand registration: IGST on that number
        -- the registration decides the state (owner, 2026-09-30), not the
        CGST+SGST the address alone would imply."""
        db = _FakeDB()
        db.collections["stores"][0].update({"state_code": "27", "gstin": BUY_JH})
        cli = _app(db)
        r = cli.post("/api/v1/vendors/purchase-invoices", json=_invoice_body())
        assert r.status_code == 201, r.text
        doc = r.json()
        assert doc["supply_place_recipient"] == "20"  # our GSTIN's state
        assert doc["interstate"] is True
        assert doc["igst_total"] == 50.0 and doc["cgst_total"] == 0.0
        assert doc["place_of_supply"] == "27"  # the ITC-register key
        assert doc["recipient_gstin"] == BUY_JH

    def test_receipt_store_reads_the_dc_when_there_is_no_grn(self):
        """The DC branch of _receipt_store_id is live behaviour (prod holds
        DELIVERY_CHALLAN receipts): a bill linked to a DC and no GRN must find
        the receiving shop -- and so the recipient entity -- through the DC.
        An attached GRN doc outranks the DC list (first read wins)."""
        from api.routers.purchase_invoices import _receipt_store_id

        db = _FakeDB()
        db.collections["grns"] = [{"grn_id": "DC1", "store_id": "S2"}]
        assert _receipt_store_id(db, None, ["DC1"]) == "S2"
        assert _receipt_store_id(db, {"store_id": "S3"}, ["DC1"]) == "S3"
        assert _receipt_store_id(db, None, None) is None

    def test_a_shop_with_no_registration_of_its_company_is_refused(self):
        """No GSTIN on the shop's record and its company holds none for the
        shop's state (or it has no state): refused and nothing written --
        never booked on the company's primary, which is another state's
        return. The receipt carries no typed GSTIN (the from-GRN door)."""
        for shop in ({"state_code": "27"}, {"state_code": None}):
            db = _FakeDB()
            db.collections["stores"][0].update(shop)
            body = _invoice_body()
            body.pop("recipient_gstin", None)
            r = _app(db).post("/api/v1/vendors/purchase-invoices", json=body)
            assert r.status_code == 422, r.text
            assert r.json()["detail"]["code"] == "RECIPIENT_SHOP_HAS_NO_GSTIN"
            assert db.collections["vendor_bills"] == []

    def test_a_gstin_less_shop_receives_on_its_states_registration(self):
        """Pune shop, no GSTIN on its record, company holds 20 (primary) and
        27: a Maharashtra vendor's bill is CGST + SGST on the 27 number --
        its PO's verdict -- not IGST on the primary."""
        db = _FakeDB()
        db.collections["entities"][0]["gstins"].append({"gstin": BUY_MH, "state_code": "27"})
        db.collections["stores"][0]["state_code"] = "27"
        body = _invoice_body()
        body.pop("recipient_gstin", None)
        r = _app(db).post("/api/v1/vendors/purchase-invoices", json=body)
        assert r.status_code == 201, r.text
        doc = r.json()
        assert doc["recipient_gstin"] == BUY_MH and doc["interstate"] is False
        assert (doc["cgst_total"], doc["sgst_total"], doc["igst_total"]) == (25.0, 25.0, 0.0)
