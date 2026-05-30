"""
IMS 2.0 - /catalog/products pricing guard
=========================================
Closes a pricing back-door: POST /api/v1/catalog/products used to (a) NOT block
offer_price > mrp and (b) store whatever gst_rate the client sent (hard-coded
18.0 default), unlike the canonical POST /api/v1/products which does both.

These tests pin the two non-negotiable rules from SYSTEM_INTENT section 3/10 on
the catalog surface:
  1. offer_price > mrp  -> HTTP 400 (blocked).
  2. a frame (HSN/category) -> 5% GST is DERIVED when the client omits the rate
     (so the master rate equals what POS bills), instead of the old 18%.

The create handler returns the built product_data in its response, so the HTTP
assertions hold with or without a live Mongo (the save layer is fail-soft to an
in-memory dict). A few direct helper assertions cover the resolver logic too.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

import pytest  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import catalog  # noqa: E402


def _frame_payload(mrp, offer_price, gst_rate=None, hsn_code=None):
    """A minimal VALID frame create payload (category FR -> 5% GST). Pricing is
    parameterised so each test can drive the offer/MRP/GST it cares about."""
    payload = {
        "category": "FR",  # ProductCategory.FRAME -> short code FR -> 5% GST
        "attributes": {
            "brand_name": "Ray-Ban",
            "model_no": "RB-TEST-001",
            "colour_code": "BLK",
        },
        "pricing": {
            "mrp": mrp,
            "offer_price": offer_price,
            "discount_category": "MASS",
        },
    }
    if gst_rate is not None:
        payload["gst_rate"] = gst_rate
    if hsn_code is not None:
        payload["hsn_code"] = hsn_code
    return payload


@pytest.fixture(autouse=True)
def _clear_inmemory_catalog():
    """Keep the in-memory fallback store clean between tests (used when no DB)."""
    catalog.CATALOG_PRODUCTS.clear()
    yield
    catalog.CATALOG_PRODUCTS.clear()


class TestCatalogPricingGuardHTTP:
    def test_offer_above_mrp_blocked_400(self, client, auth_headers):
        """offer_price (5000) > mrp (4000) must be rejected with HTTP 400."""
        resp = client.post(
            "/api/v1/catalog/products",
            json=_frame_payload(mrp=4000, offer_price=5000),
            headers=auth_headers,
        )
        assert resp.status_code == 400, resp.text
        assert "MRP" in resp.json()["detail"]

    def test_frame_derives_5pct_gst_when_omitted(self, client, auth_headers):
        """A frame with no gst_rate supplied derives 5% (not the old 18%)."""
        resp = client.post(
            "/api/v1/catalog/products",
            json=_frame_payload(mrp=4000, offer_price=3600),
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        product = resp.json()["product"]
        assert product["gst_rate"] == 5.0
        # HSN is derived from the FRAME category too.
        assert product["hsn_code"] == "900311"

    def test_explicit_gst_rate_is_honoured(self, client, auth_headers):
        """An explicitly-supplied gst_rate still wins (mirrors products.py)."""
        resp = client.post(
            "/api/v1/catalog/products",
            json=_frame_payload(mrp=4000, offer_price=3600, gst_rate=12.0),
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["product"]["gst_rate"] == 12.0

    def test_offer_equal_mrp_allowed(self, client, auth_headers):
        """offer == mrp is allowed (only offer > mrp is the block)."""
        resp = client.post(
            "/api/v1/catalog/products",
            json=_frame_payload(mrp=4000, offer_price=4000),
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text


class TestCatalogPricingGuardUnit:
    """Direct resolver assertions -- run even without the FastAPI app."""

    def test_guard_blocks_offer_above_mrp(self):
        from fastapi import HTTPException

        payload = catalog.ProductCreateInput(**_frame_payload(mrp=4000, offer_price=5000))
        with pytest.raises(HTTPException) as exc:
            catalog._guard_catalog_pricing(payload)
        assert exc.value.status_code == 400

    def test_guard_derives_frame_gst_and_hsn(self):
        payload = catalog.ProductCreateInput(**_frame_payload(mrp=4000, offer_price=3600))
        gst_rate, hsn_code = catalog._guard_catalog_pricing(payload)
        assert gst_rate == 5.0
        assert hsn_code == "900311"

    def test_guard_honours_explicit_rate(self):
        payload = catalog.ProductCreateInput(
            **_frame_payload(mrp=4000, offer_price=3600, gst_rate=18.0)
        )
        gst_rate, _hsn = catalog._guard_catalog_pricing(payload)
        assert gst_rate == 18.0
