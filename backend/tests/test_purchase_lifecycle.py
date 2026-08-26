"""IMS 2.0 - the buy-first-catalogue-later purchase lifecycle.

Owner rulings 12-15 (2026-08-26) plus the two audit findings they ride on:

  * F2  -- the 3-way match never compared the BILL against the RECEIPT, so a
           delivery partly rejected inside the 5% tolerance was billed in full
           and paid silently.
  * R7  -- a purchase bill may not be passed for payment while goods were
           rejected and no debit note has been raised.
  * R13 -- a PO may carry a product that does not exist in the catalogue yet;
           the buyer types its identity and prices inline. It must NOT become
           sellable until someone finishes cataloguing it.
  * R15 -- the INVOICE is the gate: it may only proceed for catalogued products
           and must be linked to the goods-received record.

Run: JWT_SECRET_KEY=test python -m pytest backend/tests/test_purchase_lifecycle.py -q
"""

from __future__ import annotations

import asyncio
import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MONGODB_URI", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from api.services import purchase_match as pmatch  # noqa: E402
from api.routers import vendors as vendors_mod  # noqa: E402


# =========================================================================== #
# F2 -- the bill against the RECEIPT
# =========================================================================== #

_PO_100 = {
    "po_id": "PO1",
    "items": [
        {
            "product_id": "P1",
            "product_name": "Frame X",
            "quantity": 100,
            "unit_price": 100.0,
        }
    ],
}


def _grn(accepted):
    return {"grn_id": "G1", "items": [{"product_id": "P1", "accepted_qty": accepted}]}


def _inv(qty):
    return [{"product_id": "P1", "qty": qty, "unit_price": 100.0}]


class TestBillAgainstReceipt:
    def test_billed_for_rejected_units_is_held_even_inside_tolerance(self):
        """Ordered 100, 4 rejected so 96 accepted, billed for 100.

        Every comparison against the ORDER is inside 5%, which is exactly how
        this was passed as MATCHED and paid in full. The bill must be held.
        """
        res = pmatch.three_way_match(_PO_100, _grn(96), _inv(100), 5.0)
        assert res["match_status"] == pmatch.MATCH_ON_HOLD
        reasons = res["lines"][0]["reasons"]
        assert len(reasons) == 1, reasons
        assert "exceeds the accepted qty 96" in reasons[0]
        assert "100" in reasons[0]

    def test_billing_only_what_was_accepted_still_matches(self):
        """The discriminator: same short receipt, billed for what arrived.
        Received 96 vs ordered 100 is inside 5%, so this is a clean match."""
        res = pmatch.three_way_match(_PO_100, _grn(96), _inv(96), 5.0)
        assert res["match_status"] == pmatch.MATCH_MATCHED
        assert res["lines"][0]["reasons"] == []

    def test_a_single_rejected_unit_is_not_a_rounding_difference(self):
        """No tolerance applies to over-billing a receipt: 1 unit in 100 is
        0.1% and would sail through any percentage band."""
        res = pmatch.three_way_match(_PO_100, _grn(99), _inv(100), 5.0)
        assert res["match_status"] == pmatch.MATCH_ON_HOLD
        assert any("exceeds the accepted qty" in r for r in res["exceptions"])


# =========================================================================== #
# R7 -- rejected goods hold the bill until a debit note exists
# =========================================================================== #


class _Coll:
    def __init__(self, rows):
        self.rows = rows

    def find_one(self, flt, projection=None):
        for d in self.rows:
            if all(d.get(k) == v for k, v in flt.items()):
                return dict(d)
        return None

    def insert_one(self, doc):
        self.rows.append(dict(doc))
        return type("R", (), {"inserted_id": None})()

    def find(self, flt=None, projection=None):
        flt = flt or {}
        return [
            dict(d) for d in self.rows if all(d.get(k) == v for k, v in flt.items())
        ]


class _DB:
    def __init__(self, **colls):
        self.colls = {k: _Coll(v) for k, v in colls.items()}

    def get_collection(self, name):
        return self.colls.setdefault(name, _Coll([]))


def _pay_db(debit_notes=None, rejected=2):
    return _DB(
        vendor_bills=[
            {"bill_id": "B1", "bill_number": "INV-9", "grn_id": "G1", "vendor_id": "V1"}
        ],
        grns=[
            {
                "grn_id": "G1",
                "grn_number": "GRN-7",
                "items": [
                    {
                        "product_id": "P1",
                        "received_qty": 20,
                        "accepted_qty": 20 - rejected,
                        "rejected_qty": rejected,
                    }
                ],
            }
        ],
        vendor_debit_notes=debit_notes or [],
        vendor_payments=[],
    )


class _VendorRepo:
    def find_by_id(self, _vid):
        return {"vendor_id": "V1", "trade_name": "Acme Optics", "credit_days": 30}


def _record_payment(db):
    """Call the payment route directly, with the module's DB + repos stubbed."""
    saved = (vendors_mod._get_db, vendors_mod.get_vendor_repository)
    vendors_mod._get_db = lambda: db
    vendors_mod.get_vendor_repository = lambda: _VendorRepo()
    try:
        body = vendors_mod.VendorPaymentCreate(
            amount=1000.0, mode="BANK", payment_date="2026-08-26", bill_id="B1"
        )
        return asyncio.run(
            vendors_mod.create_vendor_payment(
                "V1", body, {"user_id": "u1", "roles": ["ACCOUNTANT"]}
            )
        )
    finally:
        vendors_mod._get_db, vendors_mod.get_vendor_repository = saved


class TestRejectedGoodsHoldTheBill:
    def test_payment_refused_while_no_debit_note_covers_the_rejection(self):
        db = _pay_db()
        with pytest.raises(HTTPException) as exc:
            _record_payment(db)
        assert exc.value.status_code == 409
        detail = exc.value.detail
        assert detail["code"] == "REJECTED_GOODS_NO_DEBIT_NOTE"
        assert "2 unit(s)" in detail["message"]
        assert "GRN-7" in detail["message"]
        # and nothing was written
        assert db.get_collection("vendor_payments").rows == []

    def test_payment_allowed_once_the_debit_note_is_raised(self):
        db = _pay_db(debit_notes=[{"debit_note_id": "DN1", "grn_id": "G1"}])
        out = _record_payment(db)
        assert out["amount"] == 1000.0
        assert len(db.get_collection("vendor_payments").rows) == 1

    def test_a_clean_receipt_is_never_held(self):
        db = _pay_db(rejected=0)
        out = _record_payment(db)
        assert out["amount"] == 1000.0
        assert len(db.get_collection("vendor_payments").rows) == 1

# =========================================================================== #
# R13 -- a PO for a product that does not exist yet
# =========================================================================== #


class _ProductRepo:
    """Spine repo good enough for the create door + the duplicate pre-check."""

    def __init__(self):
        self.rows = []

    def create(self, doc, raise_on_duplicate=False):
        d = dict(doc)
        d.setdefault("product_id", f"PID-{len(self.rows) + 1}")
        self.rows.append(d)
        return dict(d)

    def find_by_id(self, pid):
        for d in self.rows:
            if d.get("product_id") == pid:
                return dict(d)
        return None

    def find_by_sku(self, sku):
        for d in self.rows:
            if d.get("sku") == sku:
                return dict(d)
        return None

    def find_by_identity_key(self, key):
        for d in self.rows:
            if d.get("identity_key") == key:
                return dict(d)
        return None


class _POCreateRepo:
    def __init__(self):
        self.created = []

    def create(self, doc):
        self.created.append(doc)
        return doc


def _raise_po(items, product_repo, po_repo):
    saved = (
        vendors_mod.get_product_repository,
        vendors_mod.get_purchase_order_repository,
        vendors_mod.get_vendor_repository,
        vendors_mod.get_audit_repository,
        vendors_mod._get_db,
        vendors_mod.validate_store_access,
        vendors_mod.is_online_store,
        vendors_mod.generate_po_number,
    )
    vendors_mod.get_product_repository = lambda: product_repo
    vendors_mod.get_purchase_order_repository = lambda: po_repo
    vendors_mod.get_vendor_repository = lambda: _VendorRepo()
    vendors_mod.get_audit_repository = lambda: None
    vendors_mod._get_db = lambda: None
    vendors_mod.validate_store_access = lambda *a, **k: None
    vendors_mod.is_online_store = lambda *a, **k: False
    vendors_mod.generate_po_number = lambda _s: "PO-TEST-1"
    try:
        body = vendors_mod.POCreate(
            vendor_id="V1", delivery_store_id="BV-01", items=items
        )
        return asyncio.run(
            vendors_mod.create_po(
                body, {"user_id": "u1", "username": "buyer", "roles": ["ADMIN"]}
            )
        )
    finally:
        (
            vendors_mod.get_product_repository,
            vendors_mod.get_purchase_order_repository,
            vendors_mod.get_vendor_repository,
            vendors_mod.get_audit_repository,
            vendors_mod._get_db,
            vendors_mod.validate_store_access,
            vendors_mod.is_online_store,
            vendors_mod.generate_po_number,
        ) = saved


_NEW_FRAME = {
    "category": "FRAME",
    "brand": "Ray-Ban",
    "model": "RB3025",
    "colour": "G-15",
    "size": "58",
    "mrp": 7990,
}


class TestOrderAnUncataloguedItem:
    def test_po_line_typed_inline_creates_a_real_but_unsellable_product(self):
        repo = _ProductRepo()
        po_repo = _POCreateRepo()
        out = _raise_po(
            [{"new_product": dict(_NEW_FRAME), "quantity": 20, "unit_price": 3200}],
            repo,
            po_repo,
        )
        assert out["po_number"] == "PO-TEST-1"

        # ONE real spine row, carrying exactly what the buyer typed.
        assert len(repo.rows) == 1
        p = repo.rows[0]
        assert p["brand"] == "Ray-Ban"
        assert p["model"] == "RB3025"
        assert p["color"] == "G-15"
        assert p["size"] == "58"
        assert p["mrp"] == 7990
        assert p["cost_price"] == 3200  # the PO rate is the provisional cost
        assert p["sku"]  # a real minted SKU, not a placeholder id

        # ...and it is NOT sellable: inactive, marked provisional, DRAFT, and
        # the ONE thing missing is the selling price.
        assert p["is_active"] is False
        assert p["provisional"] is True
        assert p["catalog_status"] == "DRAFT"
        assert p["done_gaps"] == ["offer_price"]

        # The PO line joins on the REAL product_id -- no placeholder anywhere.
        line = po_repo.created[0]["items"][0]
        assert line["product_id"] == p["product_id"]
        assert line["sku"] == p["sku"]
        assert line["ordered_qty"] == 20
        assert "new_product" not in line

    def test_a_selling_price_can_never_sneak_in_through_this_door(self):
        """The structural invariant: even if a caller supplies offer_price, the
        provisional door drops it, so the row can never stamp ACTIVE."""
        from api.services import product_master as pm

        doc = pm.normalise_payload(
            category="FRAME",
            attributes={
                "brand_name": "Ray-Ban",
                "model_no": "RB3025",
                "colour_code": "G-15",
            },
            mrp=7990,
            offer_price=6990,
            cost_price=3200,
            as_draft=True,
            provisional=True,
        )
        assert doc.get("offer_price") is None
        assert doc["catalog_status"] == "DRAFT"
        assert "offer_price" in doc["done_gaps"]
        assert doc["is_active"] is False

    def test_typing_an_item_we_already_stock_reuses_it_instead_of_twinning(self):
        repo = _ProductRepo()
        po_repo = _POCreateRepo()
        _raise_po(
            [{"new_product": dict(_NEW_FRAME), "quantity": 20, "unit_price": 3200}],
            repo,
            po_repo,
        )
        first_id = repo.rows[0]["product_id"]
        _raise_po(
            [{"new_product": dict(_NEW_FRAME), "quantity": 5, "unit_price": 3300}],
            repo,
            po_repo,
        )
        assert len(repo.rows) == 1, "a second spine row was minted for the same frame"
        assert po_repo.created[1]["items"][0]["product_id"] == first_id

    def test_a_line_must_name_a_product_or_describe_one(self):
        with pytest.raises(Exception) as exc:
            vendors_mod.POItemCreate(quantity=1, unit_price=100)
        assert "brand, model, colour, size and MRP" in str(exc.value)

    def test_a_line_cannot_do_both(self):
        with pytest.raises(Exception) as exc:
            vendors_mod.POItemCreate(
                product_id="P1",
                product_name="Frame",
                sku="S1",
                new_product=dict(_NEW_FRAME),
                quantity=1,
                unit_price=100,
            )
        assert "cannot both reference" in str(exc.value)

    def test_a_provisional_line_does_not_block_sending_the_po(self):
        """Ruling 13: the obstacle moves to the invoice. The send gate must let
        a provisional line through even with the gate switched ON."""
        repo = _ProductRepo()
        po_repo = _POCreateRepo()
        _raise_po(
            [{"new_product": dict(_NEW_FRAME), "quantity": 20, "unit_price": 3200}],
            repo,
            po_repo,
        )
        po_doc = dict(po_repo.created[0])
        po_doc["status"] = "DRAFT"

        class _R:
            def __init__(self, d):
                self.d = d
                self.patched = None

            def find_by_id(self, _i):
                return dict(self.d)

            def update(self, _i, patch):
                self.patched = patch
                return True

        r = _R(po_doc)
        saved = (
            vendors_mod.get_purchase_order_repository,
            vendors_mod.get_product_repository,
            vendors_mod._po_catalog_gate_on,
            vendors_mod.validate_store_access,
        )
        vendors_mod.get_purchase_order_repository = lambda: r
        vendors_mod.get_product_repository = lambda: repo
        vendors_mod._po_catalog_gate_on = lambda: True
        vendors_mod.validate_store_access = lambda *a, **k: None
        try:
            asyncio.run(
                vendors_mod.send_po(
                    po_doc["po_id"], {"user_id": "u1", "roles": ["ADMIN"]}
                )
            )
        finally:
            (
                vendors_mod.get_purchase_order_repository,
                vendors_mod.get_product_repository,
                vendors_mod._po_catalog_gate_on,
                vendors_mod.validate_store_access,
            ) = saved
        assert r.patched["status"] == "SENT"
