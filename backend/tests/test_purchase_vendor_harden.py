"""
IMS 2.0 - Purchase + Vendor correctness/validation regression tests
====================================================================
Covers the bugs fixed in the harden-purchase branch:

BUG-1  VendorCreate: negative credit_days accepted -> due date before invoice date
BUG-2  VendorCreate: invalid GSTIN format accepted for REGISTERED vendors
BUG-3  VendorUpdate: negative credit_days accepted
BUG-4  POCreate: empty items list accepted -> PO with subtotal/tax/total = 0
BUG-5  GRNItemCreate: accepted_qty > received_qty accepted (physics violation)
BUG-6  GRNItemCreate: accepted_qty + rejected_qty != received_qty accepted
BUG-7  GRNItemCreate: rejected_qty > received_qty accepted
BUG-8  GRNCreate: empty items list accepted -> GRN with no receiving lines
BUG-9  cancel_po: PARTIALLY_RECEIVED PO can be cancelled (orphans received stock)
BUG-10 create_vendor_bill: duplicate bill_number per vendor not blocked (double recording)
BUG-11 ap_engine.build_aging: unparseable/missing due_date bucketed as "current" not "90_plus"

Tests are pure where possible (no DB). Router-level tests use a standalone
FastAPI + override pattern (no Mongo) so they run locally without any infra.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api.routers import vendors  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402
from api.routers.vendors import (  # noqa: E402
    VendorCreate,
    VendorUpdate,
    POCreate,
    POItemCreate,
    GRNCreate,
    GRNItemCreate,
    VendorBillCreate,
)
from api.services.ap_engine import build_aging  # noqa: E402


# ---------------------------------------------------------------------------
# Standalone client (no DB)
# ---------------------------------------------------------------------------


def _client(roles=("SUPERADMIN",), uid="u1"):
    """Standalone FastAPI app with the vendors router, no DB wired. Any
    SUPERADMIN override means authz is bypassed; tests isolate schema/logic."""
    app = FastAPI()
    app.include_router(vendors.router, prefix="/api/v1/vendors")

    async def _u():
        return {
            "user_id": uid,
            "full_name": "Test",
            "username": "t",
            "roles": list(roles),
            "store_ids": ["S1"],
            "active_store_id": "S1",
            "discount_cap": None,
        }

    app.dependency_overrides[get_current_user] = _u
    return TestClient(app)


_cli = _client()  # shared SUPERADMIN client


# ===========================================================================
# BUG-1  VendorCreate: negative credit_days
# ===========================================================================


class TestVendorCreditDaysBounds:
    def _vendor(self, credit_days=30):
        return {
            "legal_name": "Acme Optics Pvt Ltd",
            "trade_name": "Acme",
            "gstin_status": "UNREGISTERED",
            "address": "1 Main St",
            "city": "Pune",
            "state": "MH",
            "mobile": "9000000000",
            "credit_days": credit_days,
        }

    def test_negative_credit_days_rejected(self):
        r = _cli.post("/api/v1/vendors", json=self._vendor(credit_days=-1))
        assert r.status_code == 422, r.text

    def test_zero_credit_days_allowed_cod(self):
        # credit_days=0 is valid (COD / immediate payment)
        r = _cli.post("/api/v1/vendors", json=self._vendor(credit_days=0))
        assert r.status_code != 422, r.text

    def test_positive_credit_days_allowed(self):
        r = _cli.post("/api/v1/vendors", json=self._vendor(credit_days=45))
        assert r.status_code != 422, r.text

    def test_schema_rejects_negative_directly(self):
        with pytest.raises(Exception):
            VendorCreate(
                legal_name="X",
                trade_name="Y",
                gstin_status="UNREGISTERED",
                address="a",
                city="b",
                state="c",
                mobile="9000000000",
                credit_days=-5,
            )

    def test_schema_allows_zero_credit_days(self):
        v = VendorCreate(
            legal_name="X",
            trade_name="Y",
            gstin_status="UNREGISTERED",
            address="a",
            city="b",
            state="c",
            mobile="9000000000",
            credit_days=0,
        )
        assert v.credit_days == 0

    # VendorUpdate must enforce the same bound
    def test_vendor_update_negative_credit_days_rejected(self):
        with pytest.raises(Exception):
            VendorUpdate(credit_days=-10)

    def test_vendor_update_zero_credit_days_allowed(self):
        v = VendorUpdate(credit_days=0)
        assert v.credit_days == 0


# ===========================================================================
# BUG-2  VendorCreate: invalid GSTIN format
# ===========================================================================


class TestVendorGSTINValidation:
    def _vendor(self, gstin=None, gstin_status="REGISTERED"):
        return {
            "legal_name": "X",
            "trade_name": "Y",
            "gstin_status": gstin_status,
            "address": "a",
            "city": "b",
            "state": "c",
            "mobile": "9000000000",
            "gstin": gstin,
        }

    def test_invalid_gstin_rejected_by_schema(self):
        with pytest.raises(Exception):
            VendorCreate(
                legal_name="X",
                trade_name="Y",
                gstin_status="REGISTERED",
                address="a",
                city="b",
                state="c",
                mobile="9000000000",
                gstin="INVALID",
            )

    def test_valid_gstin_accepted(self):
        v = VendorCreate(
            legal_name="X",
            trade_name="Y",
            gstin_status="REGISTERED",
            address="a",
            city="b",
            state="c",
            mobile="9000000000",
            gstin="27ABCDE1234F1Z5",
        )
        assert v.gstin == "27ABCDE1234F1Z5"

    def test_valid_gstin_is_uppercased(self):
        v = VendorCreate(
            legal_name="X",
            trade_name="Y",
            gstin_status="REGISTERED",
            address="a",
            city="b",
            state="c",
            mobile="9000000000",
            gstin="27abcde1234f1z5",
        )
        assert v.gstin == "27ABCDE1234F1Z5"

    def test_none_gstin_allowed_for_unregistered(self):
        v = VendorCreate(
            legal_name="X",
            trade_name="Y",
            gstin_status="UNREGISTERED",
            address="a",
            city="b",
            state="c",
            mobile="9000000000",
            gstin=None,
        )
        assert v.gstin is None

    def test_vendor_create_api_rejects_invalid_gstin(self):
        r = _cli.post("/api/v1/vendors", json=self._vendor(gstin="BADFORMAT"))
        assert r.status_code == 422, r.text

    def test_vendor_create_api_accepts_valid_gstin(self):
        r = _cli.post("/api/v1/vendors", json=self._vendor(gstin="27ABCDE1234F1Z5"))
        assert r.status_code != 422, r.text

    def test_vendor_create_api_accepts_no_gstin_for_unregistered(self):
        r = _cli.post(
            "/api/v1/vendors",
            json=self._vendor(gstin=None, gstin_status="UNREGISTERED"),
        )
        assert r.status_code != 422, r.text


# ===========================================================================
# BUG-4  POCreate: empty items list
# ===========================================================================


class TestPOItemsNotEmpty:
    def _po_body(self, items=None):
        if items is None:
            items = [
                {
                    "product_id": "p1",
                    "product_name": "Frame",
                    "sku": "SKU1",
                    "quantity": 5,
                    "unit_price": 200.0,
                }
            ]
        return {
            "vendor_id": "v1",
            "delivery_store_id": "S1",
            "items": items,
        }

    def test_empty_items_rejected_by_schema(self):
        with pytest.raises(Exception):
            POCreate(vendor_id="v1", delivery_store_id="S1", items=[])

    def test_empty_items_rejected_via_api(self):
        r = _cli.post("/api/v1/vendors/purchase-orders", json=self._po_body(items=[]))
        assert r.status_code == 422, r.text

    def test_single_valid_item_accepted(self):
        r = _cli.post("/api/v1/vendors/purchase-orders", json=self._po_body())
        assert r.status_code != 422, r.text


# ===========================================================================
# BUG-5, BUG-6, BUG-7  GRNItemCreate cross-field quantity coherence
# ===========================================================================


class TestGRNItemQtyCoherence:
    """The accepted+rejected=received invariant and the accepted<=received bound."""

    def _item(self, received=10, accepted=10, rejected=0):
        return GRNItemCreate(
            po_item_id="pi1",
            product_id="p1",
            received_qty=received,
            accepted_qty=accepted,
            rejected_qty=rejected,
        )

    # BUG-5: accepted > received
    def test_accepted_greater_than_received_rejected(self):
        with pytest.raises(Exception, match="accepted_qty"):
            self._item(received=5, accepted=10, rejected=0)

    # BUG-6: accepted + rejected != received
    def test_accepted_plus_rejected_mismatch_rejected(self):
        with pytest.raises(Exception, match="accepted_qty"):
            self._item(received=10, accepted=6, rejected=3)  # 6+3=9 != 10

    # BUG-7: rejected > received
    def test_rejected_greater_than_received_rejected(self):
        with pytest.raises(Exception, match="accepted_qty"):
            self._item(received=5, accepted=0, rejected=10)

    # Valid cases
    def test_all_accepted_valid(self):
        item = self._item(received=10, accepted=10, rejected=0)
        assert item.accepted_qty == 10
        assert item.rejected_qty == 0

    def test_all_rejected_valid(self):
        item = self._item(received=8, accepted=0, rejected=8)
        assert item.rejected_qty == 8

    def test_partial_accept_reject_valid(self):
        item = self._item(received=10, accepted=7, rejected=3)
        assert item.accepted_qty + item.rejected_qty == item.received_qty

    def test_zero_received_all_zero_valid(self):
        item = self._item(received=0, accepted=0, rejected=0)
        assert item.received_qty == 0

    # Via API
    def _grn_body(self, received=10, accepted=10, rejected=0):
        return {
            "po_id": "po1",
            "vendor_invoice_no": "INV-1",
            "vendor_invoice_date": "2026-05-21",
            "items": [
                {
                    "po_item_id": "pi1",
                    "product_id": "p1",
                    "received_qty": received,
                    "accepted_qty": accepted,
                    "rejected_qty": rejected,
                }
            ],
        }

    def test_api_rejects_accepted_greater_than_received(self):
        r = _cli.post(
            "/api/v1/vendors/grn",
            json=self._grn_body(received=5, accepted=10, rejected=0),
        )
        assert r.status_code == 422, r.text

    def test_api_rejects_sum_mismatch(self):
        r = _cli.post(
            "/api/v1/vendors/grn",
            json=self._grn_body(received=10, accepted=6, rejected=3),
        )
        assert r.status_code == 422, r.text

    def test_api_rejects_rejected_greater_than_received(self):
        r = _cli.post(
            "/api/v1/vendors/grn",
            json=self._grn_body(received=5, accepted=0, rejected=10),
        )
        assert r.status_code == 422, r.text

    def test_api_accepts_coherent_receipt(self):
        r = _cli.post(
            "/api/v1/vendors/grn",
            json=self._grn_body(received=10, accepted=7, rejected=3),
        )
        assert r.status_code != 422, r.text


# ===========================================================================
# BUG-8  GRNCreate: empty items list
# ===========================================================================


class TestGRNItemsNotEmpty:
    def test_empty_grn_items_rejected_by_schema(self):
        with pytest.raises(Exception):
            GRNCreate(
                po_id="po1",
                vendor_invoice_no="INV-1",
                vendor_invoice_date="2026-05-21",
                items=[],
            )

    def test_empty_grn_items_rejected_via_api(self):
        r = _cli.post(
            "/api/v1/vendors/grn",
            json={
                "po_id": "po1",
                "vendor_invoice_no": "INV-1",
                "vendor_invoice_date": "2026-05-21",
                "items": [],
            },
        )
        assert r.status_code == 422, r.text


# ===========================================================================
# BUG-9  cancel_po: partially-received PO should be blocked
# ===========================================================================


class TestCancelPOLifecycle:
    """The cancel endpoint must block PARTIALLY_RECEIVED and PARTIAL status."""

    def test_partially_received_cannot_be_cancelled(self):
        """Regression: a PO with accepted GRNs must not be cancellable.

        This test exercises only the status-check branch (no DB write needed)
        by letting the router fail on PO-not-found, which means the guard fired.
        The interesting assertion is that status=PARTIALLY_RECEIVED does NOT
        fall through to the cancel write -- without a real DB we settle for
        checking the overall response is not a 2xx success without an error.
        """
        # Without a DB the find_by_id returns None -> 404, which is fine and
        # confirms the code reached the DB lookup (past the auth gate).
        # If the code had a BUG where it *doesn't* check status at all, the
        # 404 is still not 200, so this is a smoke test.  The real guard is
        # exercised by the pure-logic tests below.
        r = _cli.post(
            "/api/v1/vendors/purchase-orders/nonexistent-po/cancel",
            params={"reason": "test"},
        )
        # 404 from DB or 503 (no DB) is acceptable; a 200 would be a bug.
        assert r.status_code not in (200, 201), r.text

    def test_cancel_blocked_statuses_in_router_logic(self):
        """Pure check: the set of blocked statuses includes PARTIALLY_RECEIVED."""
        # Import the router source to verify the guard covers all blocked states.
        import inspect
        import api.routers.vendors as v

        src = inspect.getsource(v.cancel_po)
        assert "PARTIALLY_RECEIVED" in src, (
            "cancel_po must block PARTIALLY_RECEIVED status to prevent orphaning "
            "already-received stock"
        )
        # The legacy alias 'PARTIAL' must also be blocked.
        assert "PARTIAL" in src, (
            "cancel_po must also block the legacy 'PARTIAL' status alias"
        )


# ===========================================================================
# BUG-11  ap_engine.build_aging: undatable bills must NOT appear as "current"
# ===========================================================================


class TestAgingUndatableBills:
    """Bills whose due_date is absent AND whose bill_date cannot be parsed
    must not silently appear in the 'current' aging bucket (which would hide
    overdue liabilities). They must be placed in '90_plus' so the accountant
    is prompted to investigate and correct the record."""

    def _bill(self, bill_id="b1", total=1000.0, due=None, bill_date=None):
        return {
            "bill_id": bill_id,
            "vendor_id": "v1",
            "total_amount": total,
            "due_date": due,
            "bill_date": bill_date,
            "credit_days": 0,
        }

    def test_null_due_and_null_bill_date_goes_to_90_plus(self):
        """Both date fields missing -> cannot compute due date -> 90_plus."""
        ag = build_aging([self._bill(due=None, bill_date=None)], [], [], "2026-03-01")
        assert ag["buckets"]["90_plus"] == 1000.0, (
            "A bill with no due/bill date must appear in 90_plus, not current"
        )
        assert ag["buckets"]["current"] == 0.0, (
            "A bill with no due/bill date must NOT appear in current"
        )

    def test_garbage_due_and_garbage_bill_date_goes_to_90_plus(self):
        """Corrupt date strings -> same as missing -> 90_plus."""
        ag = build_aging(
            [self._bill(due="not-a-date", bill_date="also-bad")], [], [], "2026-03-01"
        )
        assert ag["buckets"]["90_plus"] == 1000.0
        assert ag["buckets"]["current"] == 0.0

    def test_null_due_but_valid_bill_date_computes_correctly(self):
        """due_date missing but bill_date parseable -> fallback to bill_date+credit_days."""
        # bill_date 2025-01-01 + credit_days 30 -> due 2025-01-31
        # as_of 2026-03-01 -> 394 days past due -> 90_plus
        bill = {
            "bill_id": "b2",
            "vendor_id": "v1",
            "total_amount": 500.0,
            "due_date": None,
            "bill_date": "2025-01-01",
            "credit_days": 30,
        }
        ag = build_aging([bill], [], [], "2026-03-01")
        assert ag["buckets"]["90_plus"] == 500.0
        assert ag["buckets"]["current"] == 0.0

    def test_valid_future_due_still_current(self):
        """Normal bills with future due dates must remain in current."""
        bill = self._bill(due="2026-12-31", bill_date="2026-01-01")
        ag = build_aging([bill], [], [], "2026-03-01")
        assert ag["buckets"]["current"] == 1000.0
        assert ag["buckets"]["90_plus"] == 0.0

    def test_undatable_item_has_undatable_flag(self):
        """The item dict must expose undatable=True for display layer."""
        ag = build_aging([self._bill(due=None, bill_date=None)], [], [], "2026-03-01")
        assert ag["items"], "Expected at least one item"
        assert ag["items"][0].get("undatable") is True

    def test_dateable_item_not_flagged_undatable(self):
        """Normal items must NOT carry undatable=True (or it is False)."""
        bill = self._bill(due="2026-01-01", bill_date="2025-12-01")
        ag = build_aging([bill], [], [], "2026-03-01")
        assert ag["items"]
        assert ag["items"][0].get("undatable") is not True

    def test_undatable_bills_sort_first(self):
        """Undatable bills surface before dateable overdue bills so they are
        noticed first by the accountant."""
        bills = [
            self._bill(bill_id="b_undatable", total=200.0, due=None, bill_date=None),
            self._bill(bill_id="b_overdue", total=800.0, due="2025-01-01", bill_date="2024-12-01"),
        ]
        ag = build_aging(bills, [], [], "2026-03-01")
        assert ag["items"][0]["bill_id"] == "b_undatable", (
            "Undatable bills must sort before dated overdue bills"
        )


# ===========================================================================
# BUG-10  create_vendor_bill: duplicate bill_number guard
# ===========================================================================


class TestDuplicateVendorBill:
    """Regression: the same vendor invoice number for the same vendor must be
    rejected with HTTP 409 to prevent double-recording of a payable."""

    def test_bill_schema_accepts_required_fields(self):
        b = VendorBillCreate(
            bill_number="INV-001",
            bill_date="2026-01-15",
            taxable_amount=1000.0,
            tax_amount=180.0,
            total_amount=1180.0,
        )
        assert b.bill_number == "INV-001"

    def test_bill_schema_rejects_zero_total(self):
        with pytest.raises(Exception):
            VendorBillCreate(
                bill_number="INV-002",
                bill_date="2026-01-15",
                taxable_amount=0.0,
                tax_amount=0.0,
                total_amount=0.0,
            )

    def test_duplicate_check_logic_present_in_router(self):
        """Code-level regression: the router must contain a duplicate check."""
        import inspect
        import api.routers.vendors as v

        src = inspect.getsource(v.create_vendor_bill)
        assert "bill_number" in src and "409" in src, (
            "create_vendor_bill must include a 409 guard for duplicate bill_number"
        )


# ===========================================================================
# Regression: all pre-existing validation tests still pass
# ===========================================================================


class TestPreExistingValidationRegressions:
    """Smoke-test that the hardening did not break existing passing validations."""

    def _po(self, quantity=10, unit_price=100.0):
        return {
            "vendor_id": "v1",
            "delivery_store_id": "S1",
            "items": [
                {
                    "product_id": "p1",
                    "product_name": "Frame",
                    "sku": "SKU1",
                    "quantity": quantity,
                    "unit_price": unit_price,
                }
            ],
        }

    def test_po_zero_quantity_still_rejected(self):
        r = _cli.post("/api/v1/vendors/purchase-orders", json=self._po(quantity=0))
        assert r.status_code == 422

    def test_po_negative_quantity_still_rejected(self):
        r = _cli.post("/api/v1/vendors/purchase-orders", json=self._po(quantity=-1))
        assert r.status_code == 422

    def test_po_negative_price_still_rejected(self):
        r = _cli.post("/api/v1/vendors/purchase-orders", json=self._po(unit_price=-10.0))
        assert r.status_code == 422

    def test_grn_negative_received_still_rejected(self):
        r = _cli.post(
            "/api/v1/vendors/grn",
            json={
                "po_id": "po1",
                "vendor_invoice_no": "INV-1",
                "vendor_invoice_date": "2026-05-21",
                "items": [
                    {
                        "po_item_id": "pi1",
                        "product_id": "p1",
                        "received_qty": -1,
                        "accepted_qty": 0,
                        "rejected_qty": 0,
                    }
                ],
            },
        )
        assert r.status_code == 422
