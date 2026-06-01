"""
IMS 2.0 - Enforced discount caps endpoint
=========================================
GET /api/v1/admin/discounts/enforced-caps exposes the discount caps the POS
ACTUALLY enforces, sourced from the live code constants (services/role_caps.py +
services/pricing_caps.py). The Settings Discount screen is now read-only and
DISPLAYS these instead of an editable table that wrote to a collection the POS
never read. This guards that the endpoint reflects the real constants.
"""


def test_enforced_caps_match_code_constants(client, auth_headers):
    from api.services.role_caps import ROLE_DISCOUNT_CAPS
    from api.services.pricing_caps import (
        CATEGORY_DISCOUNT_CAPS,
        LUXURY_BRAND_CAPS,
    )

    resp = client.get(
        "/api/v1/admin/discounts/enforced-caps", headers=auth_headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["source"] == "code_constants"
    # The displayed caps must equal what the POS enforces -- no drift.
    assert body["role_caps"] == ROLE_DISCOUNT_CAPS
    assert body["category_caps"] == CATEGORY_DISCOUNT_CAPS
    assert body["luxury_brand_caps"] == LUXURY_BRAND_CAPS
    # Spot-check the headline business rules (SYSTEM_INTENT).
    assert body["role_caps"]["SUPERADMIN"] == 100.0
    assert body["role_caps"]["SALES_STAFF"] == 10.0
    assert body["category_caps"]["LUXURY"] == 5.0
    assert body["luxury_brand_caps"]["CARTIER"] == 2.0


def test_enforced_caps_requires_admin(client, staff_headers):
    """Sales staff must NOT see the admin discount surface (router is gated)."""
    resp = client.get(
        "/api/v1/admin/discounts/enforced-caps", headers=staff_headers
    )
    assert resp.status_code == 403, resp.text
