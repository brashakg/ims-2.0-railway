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
            "stores": [{"store_id": "S1", "entity_id": "E1"}],
        }

    def get_collection(self, name):
        return _FakeCollection(self.collections.setdefault(name, []))


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
    pi_router.get_grn_repository = lambda: None
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
        # Make the recipient entity Maharashtra so it matches the MH supplier.
        db.collections["entities"][0]["gstins"][0] = {
            "gstin": BUY_MH,
            "state_code": "27",
            "is_primary": True,
        }
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

    def test_explicit_recipient_gstin_overrides_entity(self):
        db = _FakeDB()
        cli = _app(db)
        # Pass an explicit MH recipient GSTIN -> intra-state with the MH supplier.
        r = cli.post(
            "/api/v1/vendors/purchase-invoices",
            json=_invoice_body(recipient_gstin=BUY_MH, recipient_entity_id=None),
        )
        assert r.status_code == 201, r.text
        doc = r.json()
        # Explicit MH recipient GSTIN -> intra-state with the MH supplier.
        assert doc["place_of_supply"] == "27" and doc["interstate"] is False
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
    def test_list_returns_only_purchase_invoices(self):
        db = _FakeDB()
        # A legacy header-only bill (no doc_type) must NOT appear in the list.
        db.collections["vendor_bills"].append(
            {
                "bill_id": "legacy1",
                "vendor_id": "V1",
                "bill_number": "OLD-1",
                "total_amount": 500,
            }
        )
        cli = _app(db)
        cli.post("/api/v1/vendors/purchase-invoices", json=_invoice_body())
        r = cli.get("/api/v1/vendors/purchase-invoices")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 1
        assert all(
            row.get("doc_type") == "PURCHASE_INVOICE"
            for row in data["purchase_invoices"]
        )

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
    EXISTING build_itc_register (which keys off taxable_amount / tax_amount /
    place_of_supply that we now write), classifies as IGST against the
    recipient entity's primary state -- NOT CGST/SGST."""
    db = _FakeDB()
    cli = _app(db)
    r = cli.post("/api/v1/vendors/purchase-invoices", json=_invoice_body())
    assert r.status_code == 201, r.text

    # The vendor_bills rows exactly as the ITC register reads them.
    bills = [
        {
            "bill_date": d.get("bill_date"),
            "taxable_amount": d.get("taxable_amount"),
            "tax_amount": d.get("tax_amount"),
            "place_of_supply": d.get("place_of_supply"),
        }
        for d in db.collections["vendor_bills"]
    ]
    # Entity primary state is Jharkhand (20); supplier was Maharashtra (27),
    # so place_of_supply written = "20" and the register routes it to IGST.
    reg = build_itc_register(bills, entity_state="20")
    assert reg["total_igst"] == 50.0
    assert reg["total_cgst"] == 0.0 and reg["total_sgst"] == 0.0
    assert reg["total_itc"] == 50.0


def test_regression_without_pos_would_be_intrastate():
    """Documents the OLD behaviour: a bill with NO place_of_supply (the
    header-only path) is treated intra-state by the register -- which is exactly
    the mis-booking the written place_of_supply fixes."""
    bills = [
        {"bill_date": "2026-05-01", "taxable_amount": 1000, "tax_amount": 50}
    ]  # no place_of_supply
    reg = build_itc_register(bills, entity_state="20")
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
