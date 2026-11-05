"""
IMS 2.0 - POS-6 (UPI QR) + BVI-1 (Shopify sync) tests
========================================================
Tests for:
  - api.services.upi_qr  (build_upi_link, reconcile_razorpay_event)
  - GET /api/v1/orders/{order_id}/upi-qr endpoint (400 when no VPA)
  - api.services.online_stock_writeback (writeback_after_sale /
    writeback_after_restock -- SIMULATED when writes disabled)
  - Shopify sync error does NOT bubble to the caller
    (sale + return MUST succeed even when Shopify is broken)

No live network calls: online_stock_writeback.writeback_skus (the ONE path
to the Shopify stock writer) and upi_qr.reconcile_razorpay_event are
monkeypatched throughout.
No real DB: a minimal MockCollection / MockDB is defined below.
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Path / env bootstrap (must happen before any IMS import)
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")
# Ensure IMS_SHOPIFY_WRITES is OFF for all tests (the DARK default).
os.environ["IMS_SHOPIFY_WRITES"] = "0"
os.environ["DISPATCH_MODE"] = "off"


# ---------------------------------------------------------------------------
# Minimal in-memory DB helpers
# ---------------------------------------------------------------------------


class MockCollection:
    """Minimal synchronous Mongo collection emulator for tests."""

    def __init__(self, docs: Optional[List[Dict]] = None):
        self._docs: List[Dict] = list(docs or [])

    def find_one(self, query=None, *args, **kwargs):
        if not query:
            return self._docs[0] if self._docs else None
        for doc in self._docs:
            if all(doc.get(k) == v for k, v in query.items()):
                return doc
        return None

    def find(self, query=None, *args, **kwargs):
        if not query:
            return iter(self._docs)
        return iter(
            d for d in self._docs if all(d.get(k) == v for k, v in query.items())
        )

    def insert_one(self, doc):
        self._docs.append(dict(doc))
        return SimpleNamespace(inserted_id="fake_id")

    def update_one(self, filt, update, **kwargs):
        def _ok(actual, want):  # equality, or {"$ne": x} (the reconcile's compare-and-set)
            if isinstance(want, dict) and "$ne" in want:
                return actual != want["$ne"]
            return actual == want

        for doc in self._docs:
            if all(_ok(doc.get(k), v) for k, v in filt.items()):
                set_vals = (update.get("$set") or {})
                push_vals = (update.get("$push") or {})
                doc.update(set_vals)
                for field, val in push_vals.items():
                    doc.setdefault(field, []).append(val)
                return SimpleNamespace(modified_count=1)
        return SimpleNamespace(modified_count=0)


class MockDB:
    """Minimal DB that delegates get_collection to named MockCollections."""

    def __init__(self, collections: Optional[Dict[str, MockCollection]] = None):
        self._colls: Dict[str, MockCollection] = collections or {}
        self.is_connected = True

    def get_collection(self, name: str) -> Optional[MockCollection]:
        return self._colls.get(name)

    def __getitem__(self, name: str):
        return self._colls.get(name)


# ===========================================================================
# 1. UPI link shape tests
# ===========================================================================


def test_build_upi_link_shape():
    """upi:// link must contain all five required NPCI parameters."""
    from api.services.upi_qr import build_upi_link

    link = build_upi_link(
        vpa="bettervision.bok@upi",
        merchant="Better Vision Bokaro",
        amount=1234.56,
        order_ref="ORD-BOK01-2026-AB12CD",
    )
    assert link.startswith("upi://pay?")
    assert "pa=bettervision.bok%40upi" in link
    assert "am=1234.56" in link
    assert "cu=INR" in link
    assert "tn=ORD-BOK01-2026-AB12CD" in link


def test_build_upi_link_zero_amount():
    """Amount 0 should produce am=0.00 (not negative, not absent)."""
    from api.services.upi_qr import build_upi_link

    link = build_upi_link("vpa@upi", "M", 0, "REF")
    assert "am=0.00" in link


def test_build_upi_link_special_chars_encoded():
    """Merchant name with spaces is percent-encoded."""
    from api.services.upi_qr import build_upi_link

    link = build_upi_link("vpa@upi", "Better Vision & Co", 100, "REF")
    assert " " not in link


# ===========================================================================
# 2. UPI QR endpoint -- 400 when no VPA
# ===========================================================================


@pytest.mark.asyncio
async def test_upi_qr_endpoint_400_when_no_vpa():
    """GET /orders/{id}/upi-qr must return 400 (not 500) when the store
    has no upi_vpa configured.  We test the router logic directly (no full
    FastAPI TestClient needed)."""
    # Patch get_order_repository to return a fake repo with one order.
    fake_order = {
        "order_id": "ord-1",
        "order_number": "ORD-TEST-2026-ABC",
        "store_id": "BV-BOK-01",
        "grand_total": 2500.0,
        "status": "CONFIRMED",
    }
    fake_repo = MagicMock()
    fake_repo.find_by_id.return_value = fake_order

    # Patch get_db to return a DB with an empty stores collection (no VPA).
    db_conn = MagicMock()
    db_conn.db = MockDB({"stores": MockCollection()})

    # Patch validate_store_access to a no-op.
    with (
        patch(
            "api.routers.orders.get_order_repository", return_value=fake_repo
        ),
        patch(
            "api.routers.orders.validate_store_access", return_value="BV-BOK-01"
        ),
        patch("api.routers.orders.get_db", return_value=db_conn, create=True),
    ):
        from fastapi import HTTPException
        from api.routers.orders import get_upi_qr

        current_user = {
            "user_id": "u1",
            "roles": ["STORE_MANAGER"],
            "active_store_id": "BV-BOK-01",
        }

        with pytest.raises(HTTPException) as exc_info:
            await get_upi_qr("ord-1", current_user)
        assert exc_info.value.status_code == 400
        assert "upi_vpa" in exc_info.value.detail.lower() or "UPI VPA" in exc_info.value.detail
        # The till names the screen that exists (owner-approved 2026-10-08).
        assert "Organization (left menu)" in exc_info.value.detail


# ===========================================================================
# 3. UPI reconcile -- DARK when no Razorpay creds
# ===========================================================================


def _captured(pay_id, paise, order_ref):
    return {"event": "payment.captured", "payload": {"payment": {"entity": {
        "id": pay_id, "amount": paise, "currency": "INR", "status": "captured",
        "captured": True, "notes": {"order_ref": order_ref}}}}}


def _rzp_db(order_doc, configured=True):
    import mongomock

    db = mongomock.MongoClient()["t"]
    if configured:
        db.integrations.insert_one({"type": "razorpay", "enabled": True,
                                    "config": {"key_id": "rzp_test_xxx", "key_secret": "secret"}})
    db.orders.insert_one(order_doc)
    return db


def _bill(order_id, grand_total, payments=()):
    paid = sum(p["amount"] for p in payments)
    return {"order_id": order_id, "order_number": f"ORD-{order_id}", "status": "CONFIRMED",
            "grand_total": grand_total, "amount_paid": paid, "balance_due": grand_total - paid,
            "payment_status": "PAID" if paid >= grand_total else "UNPAID",
            "payments": list(payments)}


def test_reconcile_upi_payment_dark_when_no_creds():
    """No Razorpay creds -> a no-op outcome, nothing written."""
    from api.services.upi_qr import reconcile_razorpay_event

    db = _rzp_db(_bill("ord-1", 2500.0), configured=False)
    assert reconcile_razorpay_event(db, _captured("pay_abc", 250000, "ORD-ord-1")) == "dark"
    assert db.orders.find_one({"order_id": "ord-1"})["payments"] == []


def test_reconcile_upi_payment_matches_and_records():
    """When Razorpay is configured AND the captured amount fits, the bill is PAID."""
    from api.services.upi_qr import reconcile_razorpay_event

    db = _rzp_db(_bill("ord-1", 1500.0))
    assert reconcile_razorpay_event(db, _captured("pay_xyz", 150000, "ORD-ord-1")) == "credited"

    updated = db.orders.find_one({"order_id": "ord-1"})
    assert updated["payment_status"] == "PAID"
    # Bill type follows the money on the webhook writer too (round-2 panel
    # blocker): a settled QR payment must not leave a stale bill_type.
    assert updated["bill_type"] == "FINAL"
    assert len(updated["payments"]) == 1
    assert updated["payments"][0]["method"] == "UPI"
    assert updated["payments"][0]["auto_reconciled"] is True


def test_reconcile_upi_payment_amount_mismatch():
    """More than the bill owes is refused (raised, so the inbox row reads failed)."""
    from api.services.upi_qr import ReconcileMismatch, reconcile_razorpay_event

    db = _rzp_db(_bill("ord-2", 3000.0))
    with pytest.raises(ReconcileMismatch):
        reconcile_razorpay_event(db, _captured("pay_bad", 400000, "ORD-ord-2"))
    assert db.orders.find_one({"order_id": "ord-2"})["payments"] == []


def test_reconcile_upi_payment_idempotent():
    """Second reconcile with same payment_id must not double-record."""
    from api.services.upi_qr import reconcile_razorpay_event

    existing = {"payment_id": "upi-auto-pay_dup", "method": "UPI",
                "provider_payment_id": "pay_dup", "amount": 1000.0}
    db = _rzp_db(_bill("ord-3", 1000.0, [existing]))
    assert reconcile_razorpay_event(db, _captured("pay_dup", 100000, "ORD-ord-3")) == "already_recorded"
    assert len(db.orders.find_one({"order_id": "ord-3"})["payments"]) == 1


# ===========================================================================
# 4. Shopify sync is SIMULATED (no-op) when writes disabled
# ===========================================================================


def test_shopify_writeback_simulated_when_writes_disabled():
    """writeback_after_sale must NOT call any live Shopify API when
    IMS_SHOPIFY_WRITES=0 (the default).  It must return without raising."""
    os.environ["IMS_SHOPIFY_WRITES"] = "0"

    from api.services.online_stock_writeback import writeback_after_sale

    items = [
        {
            "product_id": "P1",
            "sku": "SKU-001",
            "item_type": "FRAME",
            "quantity": 1,
        }
    ]

    with patch("api.services.online_stock_writeback.writeback_skus") as mock_wbs:
        # writeback_after_sale schedules writeback_skus (the ONE path to the
        # stock writer); patched, nothing can reach the network.
        mock_wbs.return_value = None
        writeback_after_sale(None, items, "BV-BOK-01")
        assert mock_wbs.called


def test_shopify_sync_error_does_not_bubble_in_sale():
    """A Shopify sync failure must NOT propagate to the caller.
    writeback_after_sale is fire-and-forget and NEVER raises."""
    from api.services.online_stock_writeback import writeback_after_sale

    async def broken_writeback(*args, **kwargs):
        raise RuntimeError("Simulated Shopify outage")

    with patch(
        "api.services.online_stock_writeback.writeback_skus",
        side_effect=broken_writeback,
    ):
        # Must not raise -- the sale MUST succeed.
        writeback_after_sale(None, [{"product_id": "P1", "sku": "SKU-001", "item_type": "FRAME"}], "ST1")


def test_shopify_sync_error_does_not_bubble_in_restock():
    """A Shopify sync failure must NOT propagate to the caller.
    writeback_after_restock is fire-and-forget and NEVER raises."""
    from api.services.online_stock_writeback import writeback_after_restock

    async def broken_writeback(*args, **kwargs):
        raise RuntimeError("Simulated Shopify outage")

    with patch(
        "api.services.online_stock_writeback.writeback_skus",
        side_effect=broken_writeback,
    ):
        # Must not raise -- the return MUST succeed.
        writeback_after_restock(None, ["SKU-001"], "ST1")


# ===========================================================================
# 5. skus_from_items filters service / virtual lines correctly
# ===========================================================================


def test_skus_from_items_filters_service_and_virtual():
    """skus_from_items must skip SERVICE / EYE_TEST / custom-* / lens-* lines."""
    from api.services.online_stock_writeback import skus_from_items

    items = [
        {"product_id": "P1", "sku": "FR-001", "item_type": "FRAME"},
        {"product_id": "P2", "sku": "SVC-001", "item_type": "SERVICE"},
        {"product_id": "custom-abc", "sku": "", "item_type": "CUSTOM"},
        {"product_id": "lens-1234", "sku": "LENS", "item_type": "LENS"},
        {"product_id": "P3", "sku": "SG-002", "item_type": "SUNGLASS"},
        {"product_id": "P4", "sku": "", "item_type": "FRAME"},  # blank sku -> skip
    ]
    skus = skus_from_items(items)
    assert "FR-001" in skus
    assert "SG-002" in skus
    assert "SVC-001" not in skus
    assert "LENS" not in skus
    # Blank sku should not appear.
    assert "" not in skus
    assert len(skus) == 2
