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
                # Honour an INCLUSION projection like real Mongo does: a fake
                # that hands back the whole doc cannot tell a field the code
                # projected from one it forgot to (feedback_hollow_tests --
                # doubles weaker than prod hide exactly that bug).
                keep = [k for k, v in (projection or {}).items() if v and k != "_id"]
                if keep:
                    return {k: d[k] for k in keep if k in d}
                return dict(d)
        return None

    def insert_one(self, doc):
        self.rows.append(dict(doc))
        return type("R", (), {"inserted_id": None})()

    def find(self, flt=None, projection=None):
        flt = flt or {}

        def _hit(d):
            for k, v in flt.items():
                if isinstance(v, dict) and "$in" in v:
                    if d.get(k) not in v["$in"]:
                        return False
                elif d.get(k) != v:
                    return False
            return True

        return [dict(d) for d in self.rows if _hit(d)]

    def update_one(self, flt, update, upsert=False):
        for d in self.rows:
            if all(d.get(k) == v for k, v in flt.items()):
                d.update(update.get("$set", {}))
                return type("R", (), {"modified_count": 1, "matched_count": 1})()
        return type("R", (), {"modified_count": 0, "matched_count": 0})()

    def count_documents(self, flt):
        return len(self.find(flt))

    def delete_one(self, flt):
        for i, d in enumerate(self.rows):
            if all(d.get(k) == v for k, v in flt.items()):
                del self.rows[i]
                break
        return type("R", (), {"deleted_count": 1})()


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


def _dc_pay_db(debit_notes=None, rejected=2):
    """The SAME delivery billed the Delivery-Challan way: the bill stores
    grn_id None and carries its receipts in linked_dc_ids instead."""
    return _DB(
        vendor_bills=[
            {
                "bill_id": "B1",
                "bill_number": "INV-9",
                "grn_id": None,
                "linked_dc_ids": ["DC1"],
                "vendor_id": "V1",
            }
        ],
        grns=[
            {
                "grn_id": "DC1",
                "grn_number": "DC-4",
                "grn_subtype": "DELIVERY_CHALLAN",
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

    def test_a_dc_consolidated_bill_with_rejections_is_held_the_same_way(self):
        """Ruling 7 must follow the Delivery-Challan path too. A DC-consolidated
        bill stores grn_id None and its receipts in linked_dc_ids; reading
        bill["grn_id"] alone paid the very delivery a GRN-linked bill would
        have held -- same 2 rejected units, no debit note, payment PAID."""
        db = _dc_pay_db()
        with pytest.raises(HTTPException) as exc:
            _record_payment(db)
        assert exc.value.status_code == 409
        detail = exc.value.detail
        assert detail["code"] == "REJECTED_GOODS_NO_DEBIT_NOTE"
        assert "2 unit(s)" in detail["message"]
        assert "DC-4" in detail["message"]
        # and nothing was written
        assert db.get_collection("vendor_payments").rows == []

    def test_a_debit_note_against_the_dc_releases_the_payment(self):
        """The discriminator for the DC hold: cover the rejection and the same
        payment goes through."""
        db = _dc_pay_db(debit_notes=[{"debit_note_id": "DN1", "grn_id": "DC1"}])
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

# =========================================================================== #
# R14 -- the goods-receipt tally: tick + quantity, ordered vs received
# =========================================================================== #


class _GRNRepo:
    def __init__(self):
        self.created = []

    def create(self, doc):
        self.created.append(doc)
        return doc

    def find_by_id(self, gid):
        for d in self.created:
            if d.get("grn_id") == gid:
                return dict(d)
        return None


class _FileStore:
    def get(self, _fid, **_kw):
        return {"data": b"x", "filename": "inv.pdf"}

    def get_metadata(self, _fid):
        return {"kind": "grn_document", "uploaded_by": "u1", "store_id": "BV-01"}


_RECEIVE_PO = {
    "po_id": "PO-1",
    "po_number": "PO-BV-1",
    "vendor_id": "V1",
    "delivery_store_id": "BV-01",
    "status": "SENT",
    "items": [
        {
            "product_id": "P1",
            "product_name": "Frame",
            "sku": "S1",
            "quantity": 20,
            "unit_price": 3200,
        }
    ],
}


def _receive(lines, po=None, product_repo=None):
    """Create a GRN through the real router with the module's deps stubbed."""
    grn_repo = _GRNRepo()
    po_repo = type(
        "R",
        (),
        {
            "find_by_id": lambda _s, _i: dict(po or _RECEIVE_PO),
            "update": lambda _s, _i, _p: True,
        },
    )()
    saved = {
        k: getattr(vendors_mod, k)
        for k in (
            "get_grn_repository",
            "get_purchase_order_repository",
            "get_product_repository",
            "get_file_store",
            "generate_grn_number",
            "can_access_store_scoped",
            "is_online_store",
            "_get_db",
            "get_audit_repository",
            "get_task_repository",
        )
        if hasattr(vendors_mod, k)
    }
    vendors_mod.get_grn_repository = lambda: grn_repo
    vendors_mod.get_purchase_order_repository = lambda: po_repo
    vendors_mod.get_product_repository = lambda: product_repo
    vendors_mod.get_file_store = lambda: _FileStore()
    vendors_mod.generate_grn_number = lambda _s: "GRN-BV-1"
    vendors_mod.can_access_store_scoped = lambda *a, **k: True
    vendors_mod.is_online_store = lambda *a, **k: False
    vendors_mod._get_db = lambda: None
    vendors_mod.get_audit_repository = lambda: None
    if hasattr(vendors_mod, "get_task_repository"):
        vendors_mod.get_task_repository = lambda: None
    try:
        body = vendors_mod.GRNCreate(
            po_id="PO-1",
            vendor_invoice_no="INV-1",
            attachment_file_id="F1",
            items=[vendors_mod.GRNItemCreate(**ln) for ln in lines],
        )
        out = asyncio.run(
            vendors_mod.create_grn(
                body,
                {
                    "user_id": "u1",
                    "username": "receiver",
                    "roles": ["STORE_MANAGER"],
                    "active_store_id": "BV-01",
                    "store_ids": ["BV-01"],
                },
            )
        )
        return out, grn_repo
    finally:
        for k, v in saved.items():
            setattr(vendors_mod, k, v)


class TestGoodsReceiptTally:
    def test_a_receipt_cannot_post_itself_without_the_tick(self):
        """The received quantity arrives pre-filled with the ordered quantity,
        so an untouched line used to post a perfect receipt. Ruling 14."""
        with pytest.raises(HTTPException) as exc:
            _receive(
                [
                    {
                        "product_id": "P1",
                        "received_qty": 20,
                        "accepted_qty": 20,
                        "rejected_qty": 0,
                    }
                ]
            )
        assert exc.value.status_code == 422
        assert exc.value.detail["code"] == "LINES_NOT_TALLIED"
        assert exc.value.detail["lines"] == [{"product_id": "P1", "received_qty": 20}]

    def test_an_exact_receipt_records_ordered_against_received(self):
        out, repo = _receive(
            [
                {
                    "product_id": "P1",
                    "received_qty": 20,
                    "accepted_qty": 20,
                    "rejected_qty": 0,
                    "tallied": True,
                }
            ]
        )
        line = repo.created[0]["items"][0]
        assert (line["ordered_qty"], line["received_qty"]) == (20, 20)
        assert line["accepted_qty"] == 20
        assert line["variance_status"] == "EXACT"
        assert out["has_discrepancy"] is False

    def test_a_short_receipt_is_flagged_short(self):
        out, repo = _receive(
            [
                {
                    "product_id": "P1",
                    "received_qty": 18,
                    "accepted_qty": 18,
                    "rejected_qty": 0,
                    "tallied": True,
                }
            ]
        )
        line = repo.created[0]["items"][0]
        assert (line["ordered_qty"], line["received_qty"]) == (20, 18)
        assert line["variance_status"] == "SHORT"
        assert out["has_discrepancy"] is True

    def test_an_over_receipt_is_flagged_over(self):
        out, repo = _receive(
            [
                {
                    "product_id": "P1",
                    "received_qty": 22,
                    "accepted_qty": 22,
                    "rejected_qty": 0,
                    "tallied": True,
                }
            ]
        )
        line = repo.created[0]["items"][0]
        assert line["variance_status"] == "OVER"
        assert out["has_discrepancy"] is True

    def test_part_of_a_line_can_be_rejected(self):
        """The owner's own case: 20 ordered, 20 arrive, 2 are damaged. The line
        is no longer all-or-nothing -- 18 enter stock, 2 are rejected."""
        out, repo = _receive(
            [
                {
                    "product_id": "P1",
                    "received_qty": 20,
                    "accepted_qty": 18,
                    "rejected_qty": 2,
                    "rejection_reason": "Damaged in transit",
                    "tallied": True,
                }
            ]
        )
        line = repo.created[0]["items"][0]
        assert (line["received_qty"], line["accepted_qty"], line["rejected_qty"]) == (
            20,
            18,
            2,
        )
        assert line["rejection_reason"] == "Damaged in transit"
        # The full 20 arrived against the 20 ordered, so the QUANTITY tally is
        # exact -- the rejection is a quality fact, and it is what holds the bill.
        assert line["variance_status"] == "EXACT"
        grn = repo.created[0]
        assert grn["total_accepted"] == 18
        assert grn["total_rejected"] == 2
        assert out["total_received"] == 20

    def test_the_arithmetic_of_a_line_must_hold(self):
        with pytest.raises(Exception) as exc:
            vendors_mod.GRNItemCreate(
                product_id="P1", received_qty=20, accepted_qty=18, rejected_qty=0
            )
        assert "must equal" in str(exc.value)

# =========================================================================== #
# R15 / R12 -- the invoice is the gate, and the authority on price
# =========================================================================== #

from api.routers import purchase_invoices as pi_mod  # noqa: E402

_COMPLETE_PRODUCT = {
    "product_id": "P1",
    "sku": "FR-0001",
    "name": "Ray-Ban RB3025",
    "category": "FRAME",
    "brand": "Ray-Ban",
    "model": "RB3025",
    "color": "G-15",
    "mrp": 7990.0,
    "offer_price": 6990.0,
    "cost_price": 3200.0,
    "hsn_code": "9003",
    "gst_rate": 5.0,
}

# The product a PO line typed in: real, but with no selling price yet.
_PROVISIONAL_PRODUCT = {
    k: v for k, v in _COMPLETE_PRODUCT.items() if k != "offer_price"
}
_PROVISIONAL_PRODUCT = {**_PROVISIONAL_PRODUCT, "provisional": True, "is_active": False}

# The shape EVERY live product is actually in (prod, 2026-08-26): all 68 rows
# carry exactly done_gaps ["cost_price"] and nothing else.
_COST_MISSING_PRODUCT = {
    k: v for k, v in _COMPLETE_PRODUCT.items() if k != "cost_price"
}


def _line(**over):
    ln = {"product_id": "P1", "qty": 18, "unit_price": 3400, "gst_rate": 5}
    ln.update(over)
    return pi_mod.PurchaseInvoiceLine(**ln)


def _book(products, po_id="PO1", grn_id="G1", lines=None):
    # copy: the fake collection mutates in place, and these fixtures are
    # module-level dicts shared by every test in the class.
    db = _DB(products=[dict(p) for p in products], vendor_bills=[], vendors=[])
    saved = (pi_mod._get_db, pi_mod.get_vendor_repository, pi_mod.get_audit_repository)
    pi_mod._get_db = lambda: db

    class _V:
        def find_by_id(self, _i):
            return {"vendor_id": "V1", "trade_name": "Acme", "credit_days": 30}

    pi_mod.get_vendor_repository = lambda: _V()
    pi_mod.get_audit_repository = lambda: None
    try:
        body = pi_mod.PurchaseInvoiceCreate(
            vendor_id="V1",
            invoice_number="INV-GATE-1",
            invoice_date="2026-08-26",
            po_id=po_id,
            grn_id=grn_id,
            lines=lines or [_line()],
        )
        out = asyncio.run(
            pi_mod.create_purchase_invoice(
                body, {"user_id": "u1", "roles": ["ACCOUNTANT"]}
            )
        )
        return out, db
    finally:
        (
            pi_mod._get_db,
            pi_mod.get_vendor_repository,
            pi_mod.get_audit_repository,
        ) = saved


@pytest.fixture(autouse=True)
def _no_grn_repo():
    """Keep the ACCEPTED-GRN check out of the way: these tests are about the
    catalogue gate and the price authority, not the GRN status guard."""
    saved = pi_mod.get_grn_repository
    pi_mod.get_grn_repository = lambda: None
    yield
    pi_mod.get_grn_repository = saved


class TestTheInvoiceIsTheGate:
    def test_a_bill_cannot_settle_an_item_that_is_still_incomplete(self):
        with pytest.raises(HTTPException) as exc:
            _book([_PROVISIONAL_PRODUCT])
        assert exc.value.status_code == 422
        d = exc.value.detail
        assert d["code"] == "PRODUCT_NOT_CATALOGUED"
        # It must say WHICH detail is missing, in words the owner reads.
        assert d["lines"][0]["missing"] == ["Selling Price"]
        assert "Ray-Ban RB3025 is still missing Selling Price" in d["message"]

    def test_the_same_bill_books_once_the_item_is_finished(self):
        out, db = _book([_COMPLETE_PRODUCT])
        assert out["invoice_number"] == "INV-GATE-1"
        assert len(db.get_collection("vendor_bills").rows) == 1

    def test_a_bill_against_an_order_must_name_the_goods_receipt(self):
        with pytest.raises(HTTPException) as exc:
            _book([_COMPLETE_PRODUCT], grn_id=None)
        assert exc.value.status_code == 422
        assert exc.value.detail["code"] == "GRN_LINK_REQUIRED"

    def test_a_bill_for_goods_must_name_the_receipt_even_with_no_po(self):
        """The hole ruling 15 left open: the gate only fired when a PO was
        named, so leaving the purchase-order box blank booked a bill for 20
        stocked frames with no tally, no 3-way match and no rejected-goods
        hold. The GOODS are the trigger, not the paperwork."""
        with pytest.raises(HTTPException) as exc:
            _book([_COMPLETE_PRODUCT], po_id=None, grn_id=None)
        assert exc.value.status_code == 422
        assert exc.value.detail["code"] == "GRN_LINK_REQUIRED"
        # ...and it must name a way out the UI can actually walk (the receiving
        # screen still requires a PO even in DC mode, so the message must not
        # send anyone down the Delivery-Challan route it cannot reach).
        msg = exc.value.detail["message"]
        assert "log a purchase order for the delivery" in msg
        assert "Delivery Challan" not in msg

    def test_a_product_id_we_cannot_find_still_needs_the_receipt(self):
        """Naming the id is the trigger, not finding it. The catalogue gate
        skips an id that is not on the products spine, so if the receipt gate
        keyed off the LOOKUP too, a typo'd (or unreadable) product id would be
        the bypass that leaving the PO blank used to be."""
        with pytest.raises(HTTPException) as exc:
            _book(
                [],  # products collection is empty: the id resolves to nothing
                po_id=None,
                grn_id=None,
                lines=[_line(product_id="GHOST")],
            )
        assert exc.value.status_code == 422
        assert exc.value.detail["code"] == "GRN_LINK_REQUIRED"

    def test_a_goods_line_below_a_service_line_still_needs_the_receipt(self):
        """A vendor invoice ordinarily LEADS with a freight/service line and
        puts the goods below it. The gate must read every line, not the first:
        a first-line-only reader waves this bill's 20 frames through with no
        receipt at all."""
        with pytest.raises(HTTPException) as exc:
            _book(
                [_COMPLETE_PRODUCT],
                po_id=None,
                grn_id=None,
                lines=[
                    _line(
                        product_id=None,
                        description="Freight",
                        qty=1,
                        unit_price=910,
                    ),
                    _line(product_id="P1", qty=20, unit_price=3900),
                ],
            )
        assert exc.value.status_code == 422
        assert exc.value.detail["code"] == "GRN_LINK_REQUIRED"

    def test_naming_a_po_alone_still_needs_the_receipt(self):
        """The OTHER half of the trigger: naming a purchase order is itself the
        claim that goods were ordered, even when no typed line names a product.
        po_id alone must demand the receipt."""
        with pytest.raises(HTTPException) as exc:
            _book(
                [_COMPLETE_PRODUCT],
                po_id="PO1",
                grn_id=None,
                lines=[
                    _line(
                        product_id=None,
                        description="Freight",
                        qty=1,
                        unit_price=910,
                    )
                ],
            )
        assert exc.value.status_code == 422
        assert exc.value.detail["code"] == "GRN_LINK_REQUIRED"

    def test_an_invoice_that_names_no_goods_is_untouched(self):
        """Services, freight, rent and expense bills carry no product line at
        all -- they have no receipt to link and must stay bookable."""
        out, _ = _book(
            [_COMPLETE_PRODUCT],
            po_id=None,
            grn_id=None,
            lines=[_line(product_id=None, description="Courier charges")],
        )
        assert out["invoice_number"] == "INV-GATE-1"

    def test_a_missing_cost_price_never_blocks_the_bill_that_carries_it(self):
        """Rulings 11 + 12: the cost arrives LATE, and THIS bill is the
        authority on it. Refusing the bill for the one figure the bill is
        holding would refuse every vendor bill in the system -- all 68 live
        products sit on exactly done_gaps ["cost_price"]."""
        out, db = _book([_COST_MISSING_PRODUCT])
        assert out["invoice_number"] == "INV-GATE-1"
        # and the booked cost must actually land on the product
        p = db.get_collection("products").find_one({"product_id": "P1"})
        assert p["cost_price"] == 3400.0
        assert p["cost_source"] == "PURCHASE_INVOICE"
        assert p["cost_source_id"] == out["invoice_id"]

    def test_subtracting_the_cost_does_not_disarm_the_rest_of_the_gate(self):
        """The discriminator for the fix above: any OTHER gap still stops the
        bill, and the cost gap is never named in the refusal."""
        no_mrp = {k: v for k, v in _COST_MISSING_PRODUCT.items() if k != "mrp"}
        with pytest.raises(HTTPException) as exc:
            _book([no_mrp])
        assert exc.value.status_code == 422
        assert exc.value.detail["lines"][0]["missing"] == ["MRP"]


class TestTheInvoiceIsTheAuthorityOnPrice:
    def test_the_billed_mrp_becomes_the_product_mrp_and_is_recorded(self):
        out, db = _book(
            [_COMPLETE_PRODUCT], lines=[_line(mrp=8490)]
        )
        assert out["invoice_number"] == "INV-GATE-1"
        p = db.get_collection("products").find_one({"product_id": "P1"})
        assert p["mrp"] == 8490.0
        assert p["mrp_source"] == "PURCHASE_INVOICE"
        assert p["mrp_source_id"] == out["invoice_id"]

    def test_a_bill_that_does_not_restate_the_mrp_leaves_it_alone(self):
        _out, db = _book([_COMPLETE_PRODUCT])
        p = db.get_collection("products").find_one({"product_id": "P1"})
        assert p["mrp"] == 7990.0
        assert "mrp_source" not in p

class _TaskRepo:
    def __init__(self):
        self.rows = []

    def find_many(self, _flt):
        return []

    def create(self, doc):
        self.rows.append(doc)
        return doc


class TestAskingForCataloguing:
    """The accountant is stopped by the gate and holds no products:write. They
    must be able to ask the cataloguer, not hunt for a developer."""

    def _ask(self, products, product_ids):
        db = _DB(products=[dict(p) for p in products])
        tasks = _TaskRepo()
        from api import dependencies as deps

        saved = (pi_mod._get_db, deps.get_task_repository)
        pi_mod._get_db = lambda: db
        deps.get_task_repository = lambda: tasks
        try:
            body = pi_mod.CataloguingRequest(product_ids=product_ids)
            out = asyncio.run(
                pi_mod.request_cataloguing(
                    body,
                    {
                        "user_id": "u1",
                        "roles": ["ACCOUNTANT"],
                        "active_store_id": "BV-01",
                    },
                )
            )
            return out, tasks
        finally:
            pi_mod._get_db, deps.get_task_repository = saved

    def test_it_names_the_product_and_exactly_what_is_missing(self):
        out, tasks = self._ask([_PROVISIONAL_PRODUCT], ["P1"])
        assert out["requested"] == [
            {
                "product_id": "P1",
                "product": "Ray-Ban RB3025",
                "missing": ["Selling Price"],
            }
        ]
        assert len(tasks.rows) == 1
        task = tasks.rows[0]
        assert task["category"] == "Catalog"
        assert task["priority"] == "P2"
        assert "Ray-Ban RB3025: needs Selling Price" in task["description"]
        assert task["status"] == "OPEN"
