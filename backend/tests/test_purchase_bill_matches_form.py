"""
The bill the server books is the bill the Purchase Invoices form showed.

Procurement-audit findings reproduced here (red first, then fixed):

  F6  A Maharashtra supplier billing the Jharkhand shop showed "Inter-state:
      IGST" on the form, and was STORED as CGST + SGST. The from-GRN draft
      hands back `place_of_supply` = the SUPPLIER's state (the ITC-register
      key), the form sends that value straight back, and the booking read it
      as the BUYER-side place of supply -- supplier 27 vs "place of supply"
      27 = same state. The form's rule is the two GST registrations: the
      supplier's GSTIN vs our GSTIN on the bill. The server now decides by
      exactly that pair, so nothing the client calls "place of supply" can
      flip the tax head.

  F40 Bills booked on this screen stored recipient_entity_id = null (the form
      never sends it), and GSTR-3B's ITC read filters on it, so the month's
      credit read Rs 0 on the GST Cross-Check. The recipient is now decided
      server-side from the receiving shop, and the three readers (GSTR-3B
      ITC, the ITC register, the Purchase Invoices list) report one figure
      from the bills' own tax heads.

Run: JWT_SECRET_KEY=test ENVIRONMENT=test python -m pytest
     backend/tests/test_purchase_bill_matches_form.py -q
"""

from __future__ import annotations

import asyncio
import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MONGODB_URI", "")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mongomock  # noqa: E402

from api.routers import purchase_invoices as pi_router  # noqa: E402
from api.routers.finance import itc as itc_mod  # noqa: E402
from api.routers.reports import _itc_from_vendor_bills  # noqa: E402
from api.services.itc_reconcile import build_itc_register  # noqa: E402
from test_purchase_invoice import (  # noqa: E402,F401
    BUY_JH,
    BUY_MH,
    SUP_MH,
    _FakeDB,
    _StubRepo,
    _app,
    _invoice_body,
    _restore_router,
)

_URL = "/api/v1/vendors/purchase-invoices"


def _jh_shop(db):
    """S1 is the Dhanbad shop: Jharkhand, billing under E1's JH GSTIN."""
    db.collections["stores"][0].update({"state_code": "20", "gstin": BUY_JH})
    return db


def _fe_body(**over):
    """The body the Purchase Invoices form actually POSTs for a from-GRN bill:
    place_of_supply is the draft's value (the SUPPLIER's state, 27), the
    recipient GSTIN is ours, and there is NO recipient_entity_id (the form
    never sends one)."""
    body = _invoice_body(place_of_supply="27", recipient_gstin=BUY_JH)
    body.pop("recipient_entity_id", None)
    body.update(over)
    return body


# ===========================================================================
# F6 - the tax head the form showed is the tax head the server stores
# ===========================================================================


class TestF6TaxHeadMatchesTheForm:
    def test_maharashtra_supplier_to_jharkhand_shop_books_igst(self):
        """The audit's exact booking: MH supplier, JH shop, the form showed
        'Inter-state supply: IGST Rs 50'. The ledger must say the same."""
        cli = _app(_jh_shop(_FakeDB()))
        r = cli.post(_URL, json=_fe_body())
        assert r.status_code == 201, r.text
        doc = r.json()
        assert doc["interstate"] is True
        assert doc["igst_total"] == 50.0
        assert doc["cgst_total"] == 0.0 and doc["sgst_total"] == 0.0
        # The legal (recipient-side) place of supply is OUR state, not the
        # supplier's -- the audit saw '27' stored here.
        assert doc["supply_place_recipient"] == "20"
        assert all(ln["igst"] > 0 and ln["cgst"] == 0 for ln in doc["lines"])

    def test_a_client_place_of_supply_cannot_flip_the_tax_head(self):
        """Whatever a client sends as place_of_supply, the verdict is the two
        registrations: an intra-state pair stays CGST+SGST even when the body
        claims another state."""
        db = _FakeDB()
        db.collections["entities"][0]["gstins"].append(
            {"gstin": BUY_MH, "state_code": "27", "is_primary": False}
        )
        db.collections["stores"][0].update({"state_code": "27", "gstin": BUY_MH})
        cli = _app(db)
        r = cli.post(
            _URL, json=_fe_body(place_of_supply="20", recipient_gstin=BUY_MH)
        )
        assert r.status_code == 201, r.text
        doc = r.json()
        assert doc["interstate"] is False
        assert doc["cgst_total"] == 25.0 and doc["sgst_total"] == 25.0
        assert doc["igst_total"] == 0.0

    def test_draft_then_book_stores_what_the_draft_showed(self):
        """GET the draft, book it the way the form does (every header field
        passed back as-is): the stored verdict and split equal the draft's."""
        db = _jh_shop(_FakeDB())
        grn = {
            "grn_id": "G1",
            "grn_number": "GRN-1",
            "po_id": "PO1",
            "vendor_id": "V1",
            "store_id": "S1",
            "status": "ACCEPTED",
            "vendor_invoice_no": "MLH-77",
            "vendor_invoice_date": "2026-05-02",
            "items": [
                {"product_id": "P1", "product_name": "Frame X", "accepted_qty": 3}
            ],
        }
        po = {
            "po_id": "PO1",
            "vendor_id": "V1",
            "items": [
                {"product_id": "P1", "hsn": "9003", "unit_price": 3100.0, "tax_rate": 5.0}
            ],
        }
        cli = _app(db)
        pi_router.get_grn_repository = lambda: _StubRepo(grn)
        pi_router.get_purchase_order_repository = lambda: _StubRepo(po)
        pi_router.get_vendor_repository = lambda: _StubRepo(db.collections["vendors"][0])

        draft = cli.get(f"{_URL}/from-grn/G1").json()
        assert draft["interstate"] is True
        body = {
            "vendor_id": draft["vendor_id"],
            "invoice_number": draft["invoice_number"],
            "invoice_date": draft["invoice_date"],
            "place_of_supply": draft["place_of_supply"],
            "recipient_gstin": draft["recipient_gstin"],
            "po_id": draft["po_id"],
            "grn_id": draft["grn_id"],
            "lines": draft["lines"],
            "total": draft["total"],
        }
        r = cli.post(_URL, json=body)
        assert r.status_code == 201, r.text
        doc = r.json()
        for key in ("interstate", "igst_total", "cgst_total", "sgst_total", "total"):
            assert doc[key] == draft[key], key
        assert doc["recipient_gstin"] == draft["recipient_gstin"]


# ===========================================================================
# F40 - the recipient is decided server-side, from the receiving shop
# ===========================================================================


class TestF40RecipientIsServerSide:
    def test_booking_stamps_the_receiving_shops_entity(self):
        cli = _app(_jh_shop(_FakeDB()))
        r = cli.post(_URL, json=_fe_body())
        assert r.status_code == 201, r.text
        assert r.json()["recipient_entity_id"] == "E1"

    def test_a_client_entity_id_does_not_override_the_shop(self):
        """Never trust the form alone: the receipt's shop names the entity."""
        db = _jh_shop(_FakeDB())
        db.collections["entities"].append(
            {"entity_id": "E2", "gstins": [{"gstin": BUY_MH, "is_primary": True}]}
        )
        cli = _app(db)
        r = cli.post(_URL, json=_fe_body(recipient_entity_id="E2"))
        assert r.status_code == 201, r.text
        assert r.json()["recipient_entity_id"] == "E1"

    def test_the_receipts_shop_names_the_entity_not_the_users_shop(self):
        """Goods received at S2 (company E2) while the accountant's active
        shop is S1 (company E1): the bill belongs to E2, on E2's registration
        -- and E1's GSTIN typed against it is refused."""
        db = _jh_shop(_FakeDB())
        db.collections["entities"].append(
            {"entity_id": "E2", "name": "WizOpt", "gstins": [{"gstin": BUY_MH, "is_primary": True}]}
        )
        db.collections["stores"].append({"store_id": "S2", "entity_id": "E2", "state_code": "27"})
        grn = {
            "grn_id": "G2",
            "vendor_id": "V1",
            "store_id": "S2",
            "status": "ACCEPTED",
            "grn_subtype": "STANDARD",
            "items": [{"product_id": "P1", "accepted_qty": 10}],
        }
        cli = _app(db)  # active shop S1
        pi_router.get_grn_repository = lambda: _StubRepo(grn)
        body = _fe_body(grn_id="G2")
        body.pop("recipient_gstin")
        r = cli.post(_URL, json=body)
        assert r.status_code == 201, r.text
        doc = r.json()
        assert doc["recipient_entity_id"] == "E2"
        assert doc["recipient_gstin"] == BUY_MH and doc["interstate"] is False
        wrong = cli.post(_URL, json=_fe_body(grn_id="G2", invoice_number="INV-002"))
        assert wrong.status_code == 422, wrong.text
        assert "WizOpt" in wrong.json()["detail"]["message"]

    def test_a_gstin_that_is_not_the_entitys_is_refused(self):
        """A typo'd (or another company's) GSTIN would put the credit and the
        tax head on the wrong registration -- refuse it, say whose it is not."""
        cli = _app(_jh_shop(_FakeDB()))
        r = cli.post(_URL, json=_fe_body(recipient_gstin="20ZZZZZ0000Z1Z0"))
        assert r.status_code == 422, r.text
        assert r.json()["detail"]["code"] == "RECIPIENT_GSTIN_NOT_OURS"
        assert r.json()["detail"]["message"]

    def test_draft_and_booking_resolve_the_same_recipient(self):
        """The draft is resolved by the same helper, with the same arguments,
        as the booking: the shop's company, on the SHOP's own registration
        when its company holds it (panel finding 2: a Pune shop carrying the
        company's Maharashtra number receives on it, as its purchase order
        does). Round 12: typing our OTHER registration against a receipt is
        refused (422) -- the receiving shop decides, a typed number only agrees."""
        db = _FakeDB()
        db.collections["entities"][0]["gstins"].append(
            {"gstin": BUY_MH, "state_code": "27", "is_primary": False}
        )
        db.collections["stores"][0].update({"state_code": "27", "gstin": BUY_MH})
        grn = {
            "grn_id": "G1",
            "vendor_id": "V1",
            "store_id": "S1",
            "status": "ACCEPTED",
            "items": [{"product_id": "P1", "accepted_qty": 10}],
        }
        cli = _app(db)
        pi_router.get_grn_repository = lambda: _StubRepo(grn)
        pi_router.get_vendor_repository = lambda: _StubRepo(db.collections["vendors"][0])
        draft = cli.get(f"{_URL}/from-grn/G1").json()
        assert draft["recipient_entity_id"] == "E1"
        assert draft["recipient_gstin"] == BUY_MH and draft["interstate"] is False
        typed = cli.post(_URL, json=_fe_body(recipient_gstin=BUY_JH))
        assert typed.status_code == 422, typed.text
        assert typed.json()["detail"]["code"] == "RECIPIENT_GSTIN_NOT_RECEIPT_SHOP"


# ===========================================================================
# F40 - the three ITC readers agree, from the bills' own tax heads
# ===========================================================================


def _mongo_world():
    db = mongomock.MongoClient().db
    db["vendors"].insert_one(
        {"vendor_id": "V1", "trade_name": "Mumbai Lens House", "gstin": SUP_MH, "credit_days": 30}
    )
    # An entity doc with NO top-level state (the state lives on its GSTINs),
    # the shape that made the register's re-derivation read intra-state.
    db["entities"].insert_one(
        {"entity_id": "E1", "gstins": [{"gstin": BUY_JH, "state_code": "20", "is_primary": True}]}
    )
    db["stores"].insert_one(
        {"store_id": "S1", "entity_id": "E1", "state_code": "20", "gstin": BUY_JH}
    )
    return db


def _register(db, period):
    itc_mod._get_db = lambda: db
    return asyncio.run(
        itc_mod.itc_register(period=period, entity_id=None, current_user={"roles": ["ADMIN"]})
    )


class TestF40ReadersAgree:
    def test_gstr3b_register_and_list_report_one_itc(self, monkeypatch):
        db = _mongo_world()
        monkeypatch.setattr(itc_mod, "_get_db", itc_mod._get_db)
        cli = _app(db)
        r = cli.post(_URL, json=_fe_body())
        assert r.status_code == 201, r.text

        # 1. GSTR-3B Table 4 (what the GST Cross-Check shows).
        igst, cgst, sgst = _itc_from_vendor_bills(db, "S1", 2026, 5, 31)
        # 2. The ITC register (Finance > GST Input Credit).
        reg = _register(db, "2026-05")
        # 3. The Purchase Invoices list.
        rows = cli.get(_URL).json()["purchase_invoices"]
        listed = round(sum(x["tax_amount"] for x in rows), 2)

        assert (igst, cgst, sgst) == (50.0, 0.0, 0.0)
        assert reg["total_itc"] == listed == 50.0
        assert (reg["total_igst"], reg["total_cgst"], reg["total_sgst"]) == (50.0, 0.0, 0.0)

    def test_a_zero_value_transfer_mirror_is_not_a_bill(self, monkeypatch):
        """Decision: a stock-transfer mirror bill counts toward input credit
        ONLY when it carries value (a deemed supply between two GSTINs is real
        credit, and GSTR-3B already claims it for the receiving GSTIN). A
        cost-less transfer's Rs 0 mirror carries no credit and must not be
        counted as a bill in the register."""
        db = _mongo_world()
        monkeypatch.setattr(itc_mod, "_get_db", itc_mod._get_db)
        cli = _app(db)
        assert cli.post(_URL, json=_fe_body()).status_code == 201
        db["vendor_bills"].insert_one(
            {
                "bill_id": "mbill_zero",
                "bill_date": "2026-05-09",
                "source_transfer_id": "T1",
                "recipient_entity_id": "E1",
                "taxable_amount": 0.0,
                "tax_amount": 0.0,
                "cgst_total": 0.0,
                "sgst_total": 0.0,
                "igst_total": 0.0,
                "status": "OUTSTANDING",
            }
        )
        reg = _register(db, "2026-05")
        assert reg["periods"][0]["bills"] == 1
        assert reg["total_itc"] == 50.0


def test_register_reads_each_bills_own_tax_heads():
    """A bill stored as IGST stays IGST in the register; a stored CGST+SGST
    bill stays CGST+SGST whatever its place_of_supply says. A legacy bill with
    no heads is split by THE engine rule (its two GSTINs), never by the
    register's old place_of_supply-vs-company-state comparison."""
    bills = [
        {"bill_date": "2026-05-01", "taxable_amount": 1000, "tax_amount": 50,
         "place_of_supply": "27", "cgst_total": 0.0, "sgst_total": 0.0, "igst_total": 50.0},
        {"bill_date": "2026-05-02", "taxable_amount": 1000, "tax_amount": 50,
         "place_of_supply": "27", "cgst_total": 25.0, "sgst_total": 25.0, "igst_total": 0.0},
    ]
    reg = build_itc_register(bills)
    assert reg["total_igst"] == 50.0
    assert reg["total_cgst"] == 25.0 and reg["total_sgst"] == 25.0
    legacy = [{"bill_date": "2026-05-03", "taxable_amount": 100, "tax_amount": 10,
               "place_of_supply": "20", "vendor_gstin": SUP_MH, "recipient_gstin": BUY_JH}]
    assert build_itc_register(legacy)["total_igst"] == 10.0
