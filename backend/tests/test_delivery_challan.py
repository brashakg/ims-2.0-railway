"""
IMS 2.0 - Delivery Challan (Rule 55) render tests
=================================================

Covers the spec's three asks without a live Mongo dependency (the endpoint
functions are driven directly with the repo/db accessors monkeypatched -- same
pattern as test_idor_transfers.py):

  1. render well-formed:  render_delivery_challan produces a complete
     self-contained HTML page with the "Delivery Challan - Not a Tax Invoice"
     header, Rule 55 copy markers (CONSIGNEE/TRANSPORTER/CONSIGNOR), the
     challan number, and per-line item + qty + HSN.
  2. order + transfer paths: the order/transfer endpoints assemble the right
     party + line data and return text/html.
  3. RBAC:                the render is denied for roles outside the
     POS-capable + ACCOUNTANT set (OPTOMETRIST / CATALOG_MANAGER /
     WORKSHOP_STAFF / CASHIER).
"""

from __future__ import annotations

import asyncio
import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import print_documents  # noqa: E402
from api.routers import transfers as transfers_router  # noqa: E402
from api.services.print_render import render_delivery_challan  # noqa: E402


# ===========================================================================
# Fakes + helpers
# ===========================================================================


class _FakeOrderRepo:
    def __init__(self, order):
        self._order = order

    def find_by_id(self, oid):
        if self._order and self._order.get("order_id") == oid:
            return dict(self._order)
        return None


def _user(role, stores=("BV-TEST-01",)):
    return {
        "user_id": f"u-{role.lower()}",
        "username": role.lower(),
        "roles": [role],
        "store_ids": list(stores),
        "active_store_id": stores[0] if stores else None,
    }


def _order():
    return {
        "order_id": "ORD-1",
        "order_number": "ORD-BOK01-2026-ABCD12",
        "store_id": "BV-TEST-01",
        "customer_id": None,
        "customer_name": "Ravi Kumar",
        "status": "CONFIRMED",
        "created_at": "2026-06-17T10:00:00",
        "items": [
            {
                "product_name": "Ray-Ban RB1234",
                "hsn_code": "900311",
                "quantity": 2,
                "serial_number": "SN-1",
            },
            {
                "product_name": "Zeiss Lens",
                "hsn_code": "900150",
                "quantity": 1,
            },
        ],
    }


@pytest.fixture()
def order_env(monkeypatch):
    """Repo with one order; no raw db (store/entity resolve fail-soft to {})."""
    repo = _FakeOrderRepo(_order())
    monkeypatch.setattr(print_documents, "get_order_repository", lambda: repo)
    monkeypatch.setattr(print_documents, "_get_db", lambda: None)
    monkeypatch.setattr(print_documents, "get_customer_repository", lambda: None)
    return repo


@pytest.fixture()
def transfer_env(monkeypatch):
    """In-test transfer in the in-memory store + no raw db."""
    tid = "TRF-1"
    transfers_router.STOCK_TRANSFERS.clear()
    transfers_router.STOCK_TRANSFERS[tid] = {
        "id": tid,
        "transfer_number": "TRF-202606-1",
        "transfer_type": "store_to_store",
        "from_location_id": "BV-TEST-01",
        "from_location_name": "BV Bokaro",
        "to_location_id": "BV-TEST-02",
        "to_location_name": "BV Ranchi",
        "items": [
            {
                "product_id": "P1",
                "sku": "SKU-1",
                "product_name": "Frame Classic",
                "hsn_code": "900311",
                "quantity_requested": 3,
            }
        ],
        "notes": "Stock rebalance",
        "created_at": "2026-06-15T09:00:00",
    }
    # The transfer endpoint resolves the source store via print_documents._get_db.
    monkeypatch.setattr(print_documents, "_get_db", lambda: None)
    # Force the in-memory fallback in transfers persistence (no Mongo).
    monkeypatch.setattr(transfers_router, "_transfers_coll", lambda: None)
    return tid


# ===========================================================================
# 1. render well-formed (pure)
# ===========================================================================


def test_render_challan_is_well_formed_html():
    entity = {
        "legal_name": "Better Vision Opticals Pvt Ltd",
        "pan": "AAACB1234C",
        "gstins": [
            {
                "gstin": "20AAACB1234C1Z5",
                "state_code": "20",
                "state_name": "Jharkhand",
                "is_primary": True,
            }
        ],
    }
    store = {
        "name": "BV Bokaro",
        "store_id": "BV-BOK-01",
        "city": "Bokaro",
        "state": "Jharkhand",
        "state_code": "20",
        "pincode": "827004",
    }
    html = render_delivery_challan(
        entity=entity,
        store=store,
        challan_number="DC/ORD/ABCD1234",
        challan_date="2026-06-17",
        consignee_name="Ravi Kumar",
        consignee_address="Sector 4, Bokaro",
        items=[
            {"product_name": "Ray-Ban RB1234", "hsn_code": "900311", "qty": 2, "serial": "SN-1"},
            {"product_name": "Zeiss Lens", "hsn_code": "900150", "qty": 1},
        ],
    )
    assert html.startswith("<!DOCTYPE html>")
    assert "</html>" in html
    # Mandatory "not a tax invoice" wording + doc title.
    assert "DELIVERY CHALLAN" in html.upper()
    assert "not a tax invoice" in html.lower()
    # Rule 55 copy marker is present (CONSIGNEE, not Rule-48 RECIPIENT).
    assert "CONSIGNEE" in html.upper()
    # Challan number + per-line item + qty + HSN.
    assert "DC/ORD/ABCD1234" in html
    assert "Ray-Ban RB1234" in html and "900311" in html
    assert "Zeiss Lens" in html and "900150" in html
    # Total quantity row (2 + 1 = 3).
    assert "Total Quantity" in html


def test_render_challan_empty_items_still_valid():
    html = render_delivery_challan(
        entity={},
        store={},
        challan_number="DC/EMPTY",
        challan_date="2026-06-17",
        items=[],
    )
    assert html.startswith("<!DOCTYPE html>")
    assert "No items" in html


def test_render_challan_copy_markers_switch():
    for copy in ("ORIGINAL", "DUPLICATE", "TRIPLICATE"):
        html = render_delivery_challan(
            entity={}, store={}, challan_number="DC/X", challan_date="2026-06-17",
            items=[{"product_name": "X", "qty": 1}], copy_marker=copy,
        )
        # Rule 55 labels.
        assert "FOR " in html.upper()


# ===========================================================================
# 2. order + transfer endpoint paths
# ===========================================================================


def test_order_challan_endpoint_returns_html(order_env):
    resp = asyncio.run(
        print_documents.delivery_challan_for_order(
            "ORD-1", "ORIGINAL", False, _user("SALES_STAFF")
        )
    )
    body = resp.body.decode("utf-8")
    assert "DELIVERY CHALLAN" in body.upper()
    assert "Ray-Ban RB1234" in body
    assert "Ravi Kumar" in body
    # Derived challan number references the order.
    assert "DC/ORD/" in body


def test_order_challan_missing_order_404(order_env):
    with pytest.raises(HTTPException) as ei:
        asyncio.run(
            print_documents.delivery_challan_for_order(
                "NOPE", "ORIGINAL", False, _user("SALES_STAFF")
            )
        )
    assert ei.value.status_code == 404


def test_transfer_challan_endpoint_returns_html(transfer_env):
    resp = asyncio.run(
        print_documents.delivery_challan_for_transfer(
            transfer_env, "ORIGINAL", False, _user("STORE_MANAGER")
        )
    )
    body = resp.body.decode("utf-8")
    assert "DELIVERY CHALLAN" in body.upper()
    assert "Frame Classic" in body
    assert "BV Bokaro" in body and "BV Ranchi" in body
    assert "DC/TRF/" in body


# ===========================================================================
# 3. RBAC
# ===========================================================================


def test_order_challan_denied_for_non_pos_roles(order_env):
    for role in ("OPTOMETRIST", "CATALOG_MANAGER", "WORKSHOP_STAFF", "CASHIER"):
        with pytest.raises(HTTPException) as ei:
            asyncio.run(
                print_documents.delivery_challan_for_order(
                    "ORD-1", "ORIGINAL", False, _user(role)
                )
            )
        assert ei.value.status_code == 403, role


def test_order_challan_allowed_for_accountant(order_env):
    # ACCOUNTANT is explicitly allowed for the challan render (back-office).
    resp = asyncio.run(
        print_documents.delivery_challan_for_order(
            "ORD-1", "ORIGINAL", False, _user("ACCOUNTANT")
        )
    )
    assert "DELIVERY CHALLAN" in resp.body.decode("utf-8").upper()


def test_transfer_challan_denied_for_non_pos_roles(transfer_env):
    with pytest.raises(HTTPException) as ei:
        asyncio.run(
            print_documents.delivery_challan_for_transfer(
                transfer_env, "ORIGINAL", False, _user("OPTOMETRIST")
            )
        )
    assert ei.value.status_code == 403
