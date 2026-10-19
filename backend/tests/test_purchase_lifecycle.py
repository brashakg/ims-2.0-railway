"""IMS 2.0 - the buy-first-catalogue-later purchase lifecycle.

Owner rulings 12-15 (2026-08-26) plus the two audit findings they ride on:

  * F2  -- the 3-way match never compared the BILL against the RECEIPT, so a
           delivery partly rejected inside the 5% tolerance was billed in full
           and paid silently.
  * R7 / A01 -- billed rejected goods need actual confirmed supplier credit;
           accepted-only bills need a confirmed internal custody request.
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
from uuid import uuid4

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MONGODB_URI", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
import mongomock  # noqa: E402
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
# R7 / A01 -- rejected goods need verified custody and billed-credit evidence
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


def _receipt_pay_db(receipts, *, dc=False, accepted_only=False, debit_notes=None):
    """Actual booked quantities and immutable rejected custody, in memory only.

    A01 needs $or/$in/$exists and atomic writer claims, so payment evidence uses
    Mongo-compatible semantics rather than the small catalogue fake above.
    """
    db = mongomock.MongoClient().get_database("purchase_lifecycle_payment")
    lines = []
    for gid, number, pid, received, rejected in receipts:
        accepted = received - rejected
        db.grns.insert_one({
            "grn_id": gid, "grn_number": number, "vendor_id": "V1", "store_id": "S1",
            "status": "ACCEPTED", "grn_subtype": "DELIVERY_CHALLAN" if dc else "PO",
            "items": [{"product_id": pid, "received_qty": received,
                       "accepted_qty": accepted, "rejected_qty": rejected}],
        })
        qty = accepted if accepted_only else received
        lines.append({"product_id": pid, "qty": qty, "unit_price": 100,
                      "taxable": qty * 100, "gst_rate": 5, "hsn": "9003",
                      "cgst": qty * 2.5, "sgst": qty * 2.5, "igst": 0,
                      "line_total": qty * 105})
        if rejected:
            rid = f"RETURN-{gid}"
            stock_ids = [f"REJECTED-{gid}-{index}" for index in range(rejected)]
            db.vendor_returns.insert_one({
                "return_id": rid, "vendor_id": "V1", "store_id": "S1",
                "receipt_rejection": True, "grn_id": gid, "grn_line_index": 0,
                "status": "received_by_vendor", "return_type": "credit_note",
                "stock_ids": stock_ids, "total_value": rejected * 100,
                "items": [{"product_id": pid, "quantity": rejected, "unit_price": 100}],
            })
            db.stock_units.insert_many([{
                "stock_id": sid, "product_id": pid, "quantity": 1, "store_id": "S1",
                "grn_id": gid, "grn_line_index": 0, "receipt_disposition": "REJECTED",
                "rtv_vendor_id": rid, "status": "RETURNED_TO_VENDOR",
            } for sid in stock_ids])
    total_qty = sum(line["qty"] for line in lines)
    db.vendor_bills.insert_one({
        "bill_id": "B1", "bill_number": "INV-9", "bill_date": "2026-08-26",
        "grn_id": None if dc else receipts[0][0],
        "linked_dc_ids": [r[0] for r in receipts] if dc else [],
        "vendor_id": "V1", "store_id": "S1", "status": "UNPAID", "received": True,
        "doc_type": "PURCHASE_INVOICE", "recipient_entity_id": "E1",
        "recipient_gstin": "20AAAAA0000A1Z5", "vendor_gstin": "20BBBBB0000B1Z5",
        "lines": lines, "taxable_amount": total_qty * 100, "tax_amount": total_qty * 5,
        "cgst_total": total_qty * 2.5, "sgst_total": total_qty * 2.5, "igst_total": 0,
        "total_amount": total_qty * 105,
    })
    db.entities.insert_one({"entity_id": "E1", "gstins": [{"gstin": "20AAAAA0000A1Z5"}]})
    if debit_notes:
        db.vendor_debit_notes.insert_many(debit_notes)
    return db


def _pay_db(debit_notes=None, rejected=2, *, accepted_only=False):
    return _receipt_pay_db([("G1", "GRN-7", "P1", 20, rejected)],
                           debit_notes=debit_notes, accepted_only=accepted_only)


def _dc_pay_db(debit_notes=None, rejected=2, *, accepted_only=False):
    return _receipt_pay_db([("DC1", "DC-4", "P1", 20, rejected)], dc=True,
                           debit_notes=debit_notes, accepted_only=accepted_only)


def _two_dc_pay_db(*, accepted_only=False):
    return _receipt_pay_db([("DC1", "DC-4", "P1", 20, 2), ("DC2", "DC-5", "P2", 10, 3)],
                           dc=True, accepted_only=accepted_only)


def _confirm_rejection(db, gid):
    """Exercise the real canonical writer: never plant a fabricated POSTED row."""
    from api.services.supplier_credit_confirmation import record_supplier_confirmation
    from api.services.supplier_credit_evidence import resolve_credit_evidence

    request = db.vendor_returns.find_one({"grn_id": gid})
    source, = resolve_credit_evidence(db, request)
    qty = request["items"][0]["quantity"]
    event, repeated = record_supplier_confirmation(db, request, {
        "idempotency_key": str(uuid4()), "credit_note_number": f"SUPPLIER-CN-{gid}",
        "credit_note_date": "2026-08-26", "received_amount": qty * 105,
        "source_selections": [], "allocations": [{"source_key": source["source_key"],
            "taxable": qty * 100, "cgst": qty * 2.5, "sgst": qty * 2.5, "igst": 0}],
    }, actor_id="ACCOUNTS", allowed_store_ids={"S1"})
    assert not repeated and event["posting_status"] == "POSTED"
    assert event["sources"][0]["grn_id"] == gid
    assert event["sources"][0]["receipt_disposition"] == "REJECTED"
    assert event["allocations"][0]["bill_id"] == "B1"
    return event


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
                "V1",
                body,
                # The bill's own shop (F63: a bill elsewhere is a 404).
                {"user_id": "u1", "roles": ["ACCOUNTANT"], "store_ids": ["S1"], "active_store_id": "S1"},
            )
        )
    finally:
        vendors_mod._get_db, vendors_mod.get_vendor_repository = saved


class TestRejectedGoodsHoldTheBill:
    def test_payment_refused_while_no_actual_credit_covers_billed_rejections(self):
        db = _pay_db()
        with pytest.raises(HTTPException) as exc:
            _record_payment(db)
        assert exc.value.status_code == 409
        detail = exc.value.detail
        assert detail["code"] == "REJECTED_GOODS_NO_DEBIT_NOTE"
        assert "supplier's actual credit against this bill" in detail["message"]
        # and nothing was written
        assert db.vendor_payments.count_documents({}) == 0

    def test_internal_debit_note_does_not_release_billed_rejections(self):
        db = _pay_db(debit_notes=[{"debit_note_id": "DN1", "grn_id": "G1"}])
        with pytest.raises(HTTPException) as exc:
            _record_payment(db)
        assert exc.value.status_code == 409
        assert "actual credit" in exc.value.detail["message"]
        assert db.vendor_payments.count_documents({}) == 0
        assert db.supplier_credit_confirmations.count_documents({}) == 0

    def test_payment_allowed_once_actual_supplier_credit_covers_billed_rejections(self):
        db = _pay_db()
        _confirm_rejection(db, "G1")
        out = _record_payment(db)
        assert out["amount"] == 1000.0
        assert db.vendor_payments.count_documents({}) == 1

    def test_a_clean_receipt_is_never_held(self):
        db = _pay_db(rejected=0)
        out = _record_payment(db)
        assert out["amount"] == 1000.0
        assert db.vendor_payments.count_documents({}) == 1

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
        assert "supplier's actual credit against this bill" in detail["message"]
        # and nothing was written
        assert db.vendor_payments.count_documents({}) == 0

    def test_actual_supplier_credit_against_the_dc_releases_the_payment(self):
        """Single linked DC proves all accepted and rejected units were billed."""
        db = _dc_pay_db()
        _confirm_rejection(db, "DC1")
        out = _record_payment(db)
        assert out["amount"] == 1000.0
        assert db.vendor_payments.count_documents({}) == 1

    @pytest.mark.parametrize("pending_gid", ["DC1", "DC2"])
    def test_every_linked_dc_is_walked_not_just_the_first(self, pending_gid):
        """Accepted-only billing still needs confirmed custody for EVERY DC.

        One request is confirmed; the other's draft must hold payment.
        Confirming just that remaining request releases the same bill, without
        pretending a consolidated bill proves a rejected unit's credit origin.
        """
        db = _two_dc_pay_db(accepted_only=True)
        db.vendor_returns.update_one({"grn_id": pending_gid}, {"$set": {"status": "draft"}})
        with pytest.raises(HTTPException) as exc:
            _record_payment(db)
        assert exc.value.status_code == 409
        detail = exc.value.detail
        assert detail["code"] == "REJECTED_GOODS_NO_DEBIT_NOTE"
        assert "Confirm the rejected goods' credit or replacement request" in detail["message"]
        other_gid = "DC1" if pending_gid == "DC2" else "DC2"
        assert db.vendor_returns.find_one({"grn_id": other_gid})["status"] == "received_by_vendor"
        assert db.vendor_payments.count_documents({}) == 0
        # Only the pending request changes. Cover both possible iteration orders.
        db.vendor_returns.update_one({"grn_id": pending_gid}, {"$set": {"status": "created"}})
        out = _record_payment(db)
        assert out["amount"] == 1000.0
        assert db.vendor_payments.count_documents({}) == 1
        assert db.supplier_credit_confirmations.count_documents({}) == 0

    def test_a_legacy_debit_note_allocated_to_the_bill_does_not_release_the_hold(self):
        db = _pay_db()
        with pytest.raises(HTTPException) as exc:
            _record_payment(db)  # rejections held while nothing covers them
        assert exc.value.status_code == 409
        assert exc.value.detail["code"] == "REJECTED_GOODS_NO_DEBIT_NOTE"
        db.get_collection("vendor_debit_notes").insert_one(
            {"debit_note_id": "DN1", "bill_id": "B1"}
        )
        with pytest.raises(HTTPException) as exc:
            _record_payment(db)
        assert exc.value.status_code == 409
        assert "actual credit" in exc.value.detail["message"]
        assert db.vendor_payments.count_documents({}) == 0
        _confirm_rejection(db, "G1")
        out = _record_payment(db)
        assert out["amount"] == 1000.0
        assert db.vendor_payments.count_documents({}) == 1

    @pytest.mark.parametrize("factory", [_pay_db, _dc_pay_db])
    def test_accepted_only_bill_needs_internal_confirmation_but_no_accounting_credit(self, factory):
        db = factory(accepted_only=True)
        db.vendor_returns.update_one({}, {"$set": {"status": "draft"}})
        with pytest.raises(HTTPException) as exc:
            _record_payment(db)
        assert exc.value.status_code == 409
        assert db.vendor_payments.count_documents({}) == 0
        db.vendor_returns.update_one({}, {"$set": {"status": "created"}})
        assert _record_payment(db)["amount"] == 1000.0
        assert db.supplier_credit_confirmations.count_documents({}) == 0
        assert db.vendor_debit_notes.count_documents({}) == 0

    def test_consolidated_billed_rejects_stay_held_without_provable_credit_allocation(self):
        from api.services.supplier_credit_evidence import credit_preview

        db = _two_dc_pay_db()
        for request in db.vendor_returns.find({}):
            assert credit_preview(db, request)["source_status"] == "PENDING_EVIDENCE"
        with pytest.raises(HTTPException) as exc:
            _record_payment(db)
        assert exc.value.status_code == 409
        assert "actual credit" in exc.value.detail["message"]
        assert db.vendor_payments.count_documents({}) == 0
        assert db.supplier_credit_confirmations.count_documents({}) == 0

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

    def find_by_id(self, po_id):
        return next((d for d in self.created if d.get("po_id") == po_id), None)


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
        # A frame's eye size lands where the frame registry keeps it (the
        # Add-Product form's lens_size), so the draft's identity_key is the
        # one a catalogued frame carries (audit C2/C3).
        assert p["attributes"]["lens_size"] == "58"
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

    def test_typing_an_item_we_already_stock_is_answered_with_it_not_twinned(self):
        # Audit C2: the server names the product we already have and creates
        # nothing; the composer asks "use it?" and resends the line with it.
        repo = _ProductRepo()
        po_repo = _POCreateRepo()
        _raise_po(
            [{"new_product": dict(_NEW_FRAME), "quantity": 20, "unit_price": 3200}],
            repo,
            po_repo,
        )
        first_id = repo.rows[0]["product_id"]
        with pytest.raises(HTTPException) as exc:
            _raise_po(
                [{"new_product": dict(_NEW_FRAME), "quantity": 5, "unit_price": 3300}],
                repo,
                po_repo,
            )
        assert exc.value.status_code == 409
        assert exc.value.detail["code"] == "ALREADY_IN_CATALOGUE"
        assert exc.value.detail["matches"][0]["existing"]["product_id"] == first_id
        assert len(repo.rows) == 1, "a second spine row was minted for the same frame"
        assert len(po_repo.created) == 1, "the refused order was saved anyway"

    def test_two_lines_of_one_order_typing_the_same_item_share_one_product(self):
        repo = _ProductRepo()
        po_repo = _POCreateRepo()
        line = {"new_product": dict(_NEW_FRAME), "quantity": 2, "unit_price": 3200}
        _raise_po([dict(line), dict(line)], repo, po_repo)
        assert len(repo.rows) == 1
        items = po_repo.created[0]["items"]
        assert items[0]["product_id"] == items[1]["product_id"] == repo.rows[0]["product_id"]

    def test_a_refused_later_line_leaves_no_product_from_the_first(self):
        """create_po ran the product door line by line, so the door refusing
        line 2 arrived after line 1's product was written: a 422, no order,
        and an orphan provisional Ray-Ban on the spine."""
        repo = _ProductRepo()
        po_repo = _POCreateRepo()
        with pytest.raises(HTTPException) as exc:
            _raise_po(
                [
                    {"new_product": dict(_NEW_FRAME), "quantity": 2, "unit_price": 3200},
                    {"new_product": {**_NEW_FRAME, "category": "NOT_A_CATEGORY",
                                     "brand": "Oakley", "model": "OX8046"},
                     "quantity": 1, "unit_price": 2500},
                ],
                repo,
                po_repo,
            )
        assert exc.value.status_code == 422
        assert repo.rows == [] and po_repo.created == []

    def test_an_order_that_was_not_saved_creates_no_product(self):
        """BaseRepository.create answers None on a failed insert. The door used
        to answer 201 for an order that did not exist -- and the typed-in
        product (and its PO-rate cost) was already written for it."""

        class _FailingPORepo(_POCreateRepo):
            def create(self, doc):
                return None

        repo = _ProductRepo()
        with pytest.raises(HTTPException) as exc:
            _raise_po(
                [{"new_product": dict(_NEW_FRAME), "quantity": 2, "unit_price": 3200}],
                repo,
                _FailingPORepo(),
            )
        assert exc.value.status_code == 500
        assert repo.rows == [], "a product was written for an order never saved"

    def test_the_typed_in_product_is_written_after_the_order(self):
        repo = _ProductRepo()
        seen = []

        class _OrderFirst(_POCreateRepo):
            def create(self, doc):
                seen.append(len(repo.rows))
                return super().create(doc)

        po_repo = _OrderFirst()
        _raise_po(
            [{"new_product": dict(_NEW_FRAME), "quantity": 2, "unit_price": 3200}],
            repo,
            po_repo,
        )
        assert seen == [0], "the product was on the spine before the order"
        assert po_repo.created[0]["items"][0]["product_id"] == repo.rows[0]["product_id"]

    def test_a_lens_typed_on_a_line_is_its_sub_brand_and_is_reused(self):
        """An Optical Lens shows no Model No: the model typed on the line is its
        Sub Brand, so the row is LS-ESSILOR-CRIZAL, 'Essilor Crizal Lenses',
        with a duplicate key; the same line typed again is answered with it
        (audit C2, as for a frame: the composer asks "use it?"), and Varilux
        is another lens goods receipt can tell apart."""
        repo = _ProductRepo()
        po_repo = _POCreateRepo()
        lens = {"category": "LS", "brand": "Essilor", "model": "Crizal", "mrp": 2500}
        _raise_po([{"new_product": dict(lens), "quantity": 2, "unit_price": 900}], repo, po_repo)
        p = repo.rows[0]
        with pytest.raises(HTTPException) as exc:
            _raise_po([{"new_product": dict(lens), "quantity": 3, "unit_price": 900}], repo, po_repo)
        assert exc.value.status_code == 409
        assert exc.value.detail["code"] == "ALREADY_IN_CATALOGUE"
        assert exc.value.detail["matches"][0]["existing"]["product_id"] == p["product_id"]
        assert len(repo.rows) == 1, "a second spine row was minted for the same lens"
        assert (p["sku"], p["model"], p["name"], p["identity_key"]) == (
            "LS-ESSILOR-CRIZAL", "Crizal", "Essilor Crizal Lenses", "essilor|crizal|")
        assert p["attributes"]["subbrand"] == "Crizal"
        assert not {"model_no", "model_name"} & set(p["attributes"])
        _raise_po([{"new_product": dict(lens, model="Varilux"), "quantity": 1, "unit_price": 900}],
                  repo, po_repo)
        assert (repo.rows[1]["sku"], repo.rows[1]["name"]) == ("LS-ESSILOR-VARILUX", "Essilor Varilux Lenses")

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

            def update_if(self, _i, _expected, patch):  # send's guarded write
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

    def update(self, grn_id, fields):
        for d in self.created:
            if d.get("grn_id") == grn_id:
                d.update(fields)
                return True
        return False

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


def _book(products, po_id="PO1", grn_id="G1", lines=None, bill_kind=None):
    # copy: the fake collection mutates in place, and these fixtures are
    # module-level dicts shared by every test in the class.
    db = _DB(
        products=[dict(p) for p in products],
        vendor_bills=[],
        vendors=[],
        # The bill must name a company + GSTIN (a bill with none is a 422).
        entities=[{"entity_id": "E1", "gstins": [{"gstin": "27ZZZZZ9999Z1Z9", "state_code": "27"}]}],
        stores=[{"store_id": "BV-01", "entity_id": "E1", "state_code": "27", "gstin": "27ZZZZZ9999Z1Z9"}],
    )
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
            bill_kind=bill_kind,
        )
        out = asyncio.run(
            pi_mod.create_purchase_invoice(
                body,
                {
                    "user_id": "u1",
                    "roles": ["ACCOUNTANT"],
                    "active_store_id": "BV-01",
                    "store_ids": ["BV-01"],
                },
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
        # ...and it must name a way out the UI can actually walk. The Goods
        # Receipt screen's Delivery-Challan mode now receives WITHOUT a PO
        # (vendor picker + typed product lines), so the message names that
        # route -- it used to be forbidden here precisely because the screen
        # kept that door shut.
        msg = exc.value.detail["message"]
        assert "Delivery Challan" in msg
        assert "Goods Receipt" in msg

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
        all -- they have no receipt to link and must stay bookable. Since the
        free-text hole closed they SAY so (bill_kind=SERVICES, the choice the
        form now requires) -- and then book exactly as before."""
        out, _ = _book(
            [_COMPLETE_PRODUCT],
            po_id=None,
            grn_id=None,
            lines=[_line(product_id=None, description="Courier charges")],
            bill_kind="SERVICES",
        )
        assert out["invoice_number"] == "INV-GATE-1"

    def test_prose_only_lines_must_declare_goods_or_services(self):
        """THE FREE-TEXT HOLE (audit-reproduced): 21 pcs of frames described
        only in prose carry no product_id, so the goods trigger above cannot
        see them -- Rs 72,450 booked with no receipt, no tally, no 3-way
        match. With no receipt linked the bill must now declare its kind."""
        with pytest.raises(HTTPException) as exc:
            _book(
                [_COMPLETE_PRODUCT],
                po_id=None,
                grn_id=None,
                lines=[
                    _line(
                        product_id=None,
                        description="20 pcs assorted frames",
                        qty=21,
                        unit_price=3450,
                    )
                ],
            )
        assert exc.value.status_code == 422
        assert exc.value.detail["code"] == "BILL_KIND_REQUIRED"

    def test_declaring_goods_in_prose_demands_the_receipt(self):
        """The honest half of the choice: prose lines + bill_kind=GOODS is a
        goods bill and refuses without its receipt, message naming the no-PO
        Delivery-Challan route."""
        with pytest.raises(HTTPException) as exc:
            _book(
                [_COMPLETE_PRODUCT],
                po_id=None,
                grn_id=None,
                lines=[
                    _line(
                        product_id=None,
                        description="20 pcs assorted frames",
                        qty=21,
                        unit_price=3450,
                    )
                ],
                bill_kind="GOODS",
            )
        assert exc.value.status_code == 422
        assert exc.value.detail["code"] == "GRN_LINK_REQUIRED"
        assert "Delivery Challan" in exc.value.detail["message"]

    def test_declaring_services_cannot_dodge_a_line_that_names_a_product(self):
        """bill_kind=SERVICES on a bill whose line names a stocked product is
        still a goods bill -- the product trigger outranks the header claim."""
        with pytest.raises(HTTPException) as exc:
            _book(
                [_COMPLETE_PRODUCT],
                po_id=None,
                grn_id=None,
                lines=[_line(product_id="P1", qty=20, unit_price=3900)],
                bill_kind="SERVICES",
            )
        assert exc.value.status_code == 422
        assert exc.value.detail["code"] == "GRN_LINK_REQUIRED"

    def test_a_missing_cost_price_never_blocks_the_bill_that_carries_it(self):
        """Rulings 11 + 12: the cost arrives LATE, and THIS bill is the
        authority on it. Refusing the bill for the one figure the bill is
        holding would refuse every vendor bill in the system -- all 68 live
        products sit on exactly done_gaps ["cost_price"]."""
        out, db = _book([_COST_MISSING_PRODUCT])
        assert out["invoice_number"] == "INV-GATE-1"
        # and the booked cost must actually land on the product
        p = db.get_collection("products").find_one({"product_id": "P1"})
        # Rs 3400 + its 5% GST: this supplier has no GSTIN, so the bill claims
        # no input credit and that GST is part of the cost (F04, owner + CA
        # 2026-10-18, purchase_invoices._cost_lines).
        assert p["cost_price"] == 3570.0
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

    def find_many(self, flt):
        # Reads what was written (the ask reads its task back to know the
        # catalogue manager was really told).
        return [r for r in self.rows if all(r.get(k) == v for k, v in (flt or {}).items())]

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
        assert task["category"] == "Catalogue"  # the catalogue managers' door
        assert task["priority"] == "P3"  # the door's SLA: escalates after a day
        assert "Ray-Ban RB3025: needs Selling Price" in task["description"]
        assert task["status"] == "OPEN"
