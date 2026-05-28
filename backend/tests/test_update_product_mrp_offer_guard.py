"""
IMS 2.0 — update_product MRP >= offer_price guard (single-field edits)
=====================================================================
Non-negotiable rule: MRP >= offer_price, enforced at the DB layer.

The old update_product check only fired when BOTH mrp and offer_price were in
the payload, so a single-field edit could slip a product into MRP < offer:
  - lowering mrp alone below the existing offer_price
  - raising offer_price alone above the existing mrp

These tests create a real product (mrp 1000 / offer 900) and assert the
guard now rejects either one-sided violation while a valid update succeeds.

Runs end-to-end via the conftest TestClient against CI's mongo:7.0. When no
DB is available locally the create returns no id and the test skips.
"""

from __future__ import annotations

import pytest


def _headers(roles):
    from api.routers.auth import create_access_token

    token = create_access_token(
        {
            "user_id": "mrp-guard-1",
            "username": "mrpguard",
            "roles": roles,
            "store_ids": ["BV-TEST-01"],
            "active_store_id": "BV-TEST-01",
        }
    )
    return {"Authorization": f"Bearer {token}"}


_CATALOG = ["CATALOG_MANAGER"]


def _create_product(client, sku):
    """Create a 1000/900 product; return its id or None when no DB (skip)."""
    body = {
        "sku": sku,
        "category": "FRAME",
        "brand": "GuardBrand",
        "model": "G1",
        "mrp": 1000.0,
        "offer_price": 900.0,
    }
    resp = client.post("/api/v1/products", headers=_headers(_CATALOG), json=body)
    if resp.status_code not in (200, 201):
        return None
    data = resp.json()
    return (
        data.get("product_id")
        or data.get("id")
        or (data.get("product") or {}).get("product_id")
    )


class TestUpdateProductMrpOfferGuard:
    def test_lowering_mrp_below_existing_offer_is_rejected(self, client):
        pid = _create_product(client, "MRPGUARD-LOWER-MRP")
        if not pid:
            pytest.skip("no DB available — create did not return a product id")
        # existing offer_price is 900; drop mrp to 800 -> 800 < 900 -> reject
        resp = client.put(
            f"/api/v1/products/{pid}", headers=_headers(_CATALOG), json={"mrp": 800.0}
        )
        assert resp.status_code == 400
        assert "MRP" in resp.json().get("detail", "")

    def test_raising_offer_above_existing_mrp_is_rejected(self, client):
        pid = _create_product(client, "MRPGUARD-RAISE-OFFER")
        if not pid:
            pytest.skip("no DB available — create did not return a product id")
        # existing mrp is 1000; raise offer to 1500 -> 1500 > 1000 -> reject
        resp = client.put(
            f"/api/v1/products/{pid}",
            headers=_headers(_CATALOG),
            json={"offer_price": 1500.0},
        )
        assert resp.status_code == 400
        assert "MRP" in resp.json().get("detail", "")

    def test_valid_single_field_update_succeeds(self, client):
        pid = _create_product(client, "MRPGUARD-VALID")
        if not pid:
            pytest.skip("no DB available — create did not return a product id")
        # raise mrp to 1100 (offer 900 still <= 1100) -> allowed
        resp = client.put(
            f"/api/v1/products/{pid}", headers=_headers(_CATALOG), json={"mrp": 1100.0}
        )
        assert resp.status_code == 200

    def test_valid_both_fields_update_still_succeeds(self, client):
        pid = _create_product(client, "MRPGUARD-BOTH")
        if not pid:
            pytest.skip("no DB available — create did not return a product id")
        resp = client.put(
            f"/api/v1/products/{pid}",
            headers=_headers(_CATALOG),
            json={"mrp": 2000.0, "offer_price": 1800.0},
        )
        assert resp.status_code == 200

    def test_both_fields_violation_still_rejected(self, client):
        pid = _create_product(client, "MRPGUARD-BOTH-BAD")
        if not pid:
            pytest.skip("no DB available — create did not return a product id")
        # regression: the original both-present check must still reject
        resp = client.put(
            f"/api/v1/products/{pid}",
            headers=_headers(_CATALOG),
            json={"mrp": 500.0, "offer_price": 600.0},
        )
        assert resp.status_code == 400
