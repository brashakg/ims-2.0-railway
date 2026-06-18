"""
IMS 2.0 - product HSN is required / auto-minted (statutory P3)
=============================================================
A product must never be persisted with a blank HSN code: the invoice and the
GSTR-1 HSN summary (and the Tally export) need a valid HSN on every taxable
line. The fix (routers/products.py::_resolve_hsn_or_400) auto-mints the HSN
from the category via the canonical gst_rates.GST_CATEGORY_TABLE when the
caller omits it, and rejects with HTTP 400 when the category has no canonical
HSN to fall back to.

Two layers:
1. The pure guard _resolve_hsn_or_400 -- auto-mint from category, explicit
   value wins, no-HSN category -> 400. Asserted against the canonical table.
2. The create_product / update_product endpoints via the bare-app +
   get_current_user override pattern (same as test_gstn_export.py): a create
   with no hsn_code succeeds (stub mode), and an explicit blank-HSN clear on
   update is rejected.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MONGODB_URI", "")

import pytest  # noqa: E402
from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import products  # noqa: E402
from api.routers.products import _resolve_hsn_or_400  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402
from api.services.gst_rates import GST_CATEGORY_TABLE, hsn_for_category  # noqa: E402


# ============================================================================
# Pure guard: _resolve_hsn_or_400
# ============================================================================


class TestResolveHsnGuard:
    def test_explicit_hsn_wins(self):
        # A supplied HSN is returned as-is (trimmed), category ignored.
        assert _resolve_hsn_or_400("FRAME", " 90031100 ") == "90031100"

    def test_auto_mint_from_category_frame(self):
        # No HSN supplied -> minted from the canonical table for FRAME (900311).
        assert _resolve_hsn_or_400("FRAME", None) == "900311"
        assert _resolve_hsn_or_400("FRAME", None) == hsn_for_category("FRAME")

    def test_auto_mint_blank_string_treated_as_absent(self):
        assert _resolve_hsn_or_400("SUNGLASS", "") == "900410"
        assert _resolve_hsn_or_400("SUNGLASS", "   ") == "900410"

    @pytest.mark.parametrize(
        "category",
        ["FRAME", "OPTICAL_LENS", "CONTACT_LENS", "SUNGLASS", "WATCH", "SERVICES"],
    )
    def test_auto_mint_matches_canonical_table(self, category):
        # The minted HSN for every canonical category equals the table's value
        # (master HSN == the one POS billing reads).
        expected = GST_CATEGORY_TABLE[category][0]
        assert _resolve_hsn_or_400(category, None) == expected

    def test_category_without_canonical_hsn_rejected_400(self):
        # A category the canonical table does not know (so hsn_for_category is
        # None) and no explicit HSN -> 400, never a silent blank HSN.
        with pytest.raises(HTTPException) as exc:
            _resolve_hsn_or_400("MADE_UP_CATEGORY", None)
        assert exc.value.status_code == 400
        assert "HSN" in str(exc.value.detail)

    def test_no_hsn_product_is_never_blank(self):
        # The whole point: the guard never returns a blank string.
        for category in GST_CATEGORY_TABLE:
            assert _resolve_hsn_or_400(category, None).strip()


# ============================================================================
# Endpoint behaviour: create / update
# ============================================================================


def _products_client_as(roles):
    app = FastAPI()
    app.include_router(products.router, prefix="/products")

    async def _fake_user():
        return {
            "user_id": "u1",
            "username": "tester",
            "full_name": "Test User",
            "active_store_id": "store-001",
            "store_ids": ["store-001"],
            "roles": roles,
        }

    app.dependency_overrides[get_current_user] = _fake_user
    return TestClient(app)


def _frame_payload(**over):
    body = {
        "sku": "FR-HSN-001",
        "category": "FRAME",
        "brand": "Ray-Ban",
        "model": "RB-2140",
        "color": "BLK",
        "mrp": 5000.0,
        "offer_price": 4500.0,
    }
    body.update(over)
    return body


class TestCreateProductHsn:
    def test_create_without_hsn_succeeds(self):
        # No DB (stub mode) -> create returns a {product_id, sku} envelope; the
        # HSN guard auto-mints rather than 400'ing on the missing hsn_code.
        resp = _products_client_as(["ADMIN"]).post(
            "/products", json=_frame_payload(sku="FR-HSN-001", model="RB-2140")
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["sku"] == "FR-HSN-001"

    def test_create_with_explicit_hsn_succeeds(self):
        # Distinct identity so the canonical door's duplicate-detection guard
        # does not flag it against the no-HSN create above.
        resp = _products_client_as(["ADMIN"]).post(
            "/products",
            json=_frame_payload(
                sku="FR-HSN-002", model="RB-3000", hsn_code="90031100"
            ),
        )
        assert resp.status_code == 201, resp.text

    def test_create_blank_category_still_422(self):
        # Category guard runs before the HSN guard -- a blank category is still
        # the 422 it always was (not a 400 from the HSN guard).
        resp = _products_client_as(["ADMIN"]).post(
            "/products", json=_frame_payload(sku="FR-HSN-003", category="")
        )
        assert resp.status_code == 422


class TestUpdateProductHsn:
    def test_update_clearing_hsn_rejected_400(self):
        # Explicitly blanking hsn_code on update is rejected (would leave the
        # invoice / GSTR-1 line with no HSN).
        resp = _products_client_as(["ADMIN"]).put(
            "/products/some-id", json={"hsn_code": ""}
        )
        assert resp.status_code == 400
        assert "HSN" in resp.text

    def test_update_omitting_hsn_not_blocked(self):
        # Omitting hsn_code is the common edit and must not be blocked by the
        # clear-guard. With no DB the update path 404s the missing product --
        # the point is it is NOT a 400 from the HSN clear-guard.
        resp = _products_client_as(["ADMIN"]).put(
            "/products/some-id", json={"offer_price": 4000.0}
        )
        assert resp.status_code != 400
