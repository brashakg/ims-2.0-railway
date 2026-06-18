"""
Tests for store-specific print identity (PR: store-specific printouts).

Proves:
  * A document header reflects the PASSED store + entity (legal name, GSTIN-for-
    state, address, brand) -- two different stores -> two different headers.
  * The per-entity logo is read from the NESTED entity.invoice.logo_url (the
    Organization module's location), not only a top-level logo_url, and is
    rendered in the server-side HTML header.
  * The hardcoded brand fallback is gone: an unconfigured store does NOT yield a
    "Better Vision" name -- it yields an empty/neutral header.
  * The delivery-challan render fails loudly (raises) when the issuing store
    cannot be resolved, rather than printing a blank, identity-less document.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.services.print_legal import LegalHeader, _entity_logo  # noqa: E402
from api.services.print_render import (  # noqa: E402
    render_delivery_challan,
    supplier_identity_html,
)
from api.services.print_identity import assert_issuing_identity  # noqa: E402
from fastapi import HTTPException  # noqa: E402


# Two different entities/stores in two different states (Jharkhand 20 / MH 27).
BV_ENTITY = {
    "entity_id": "ent-bv",
    "legal_name": "Better Vision Retail Pvt Ltd",
    "name": "Better Vision",
    "pan": "AABCB1234M",
    "registered_address": "HQ, Bokaro",
    "invoice": {"logo_url": "https://cdn.example.com/bv-logo.png"},
    "gstins": [
        {"gstin": "20AABCB1234M1Z5", "state_code": "20", "state_name": "Jharkhand", "is_primary": True},
        {"gstin": "27AABCB1234M1ZA", "state_code": "27", "state_name": "Maharashtra"},
    ],
}
BV_STORE_JH = {
    "store_id": "s-bok-01",
    "store_name": "Better Vision Bokaro",
    "store_code": "BV-BOK-01",
    "brand": "BETTER_VISION",
    "entity_id": "ent-bv",
    "address": "City Centre",
    "city": "Bokaro",
    "state": "Jharkhand",
    "state_code": "20",
    "pincode": "827004",
    "phone": "06542-000000",
}

WIZ_ENTITY = {
    "entity_id": "ent-wiz",
    "legal_name": "WizOpt Eyewear LLP",
    "name": "WizOpt",
    "llpin": "AAB-1234",
    "registered_address": "Pune Office",
    "invoice": {"logo_url": "https://cdn.example.com/wizopt-logo.png"},
    "gstins": [
        {"gstin": "27AABFW5678N1Z3", "state_code": "27", "state_name": "Maharashtra", "is_primary": True},
    ],
}
WIZ_STORE_MH = {
    "store_id": "s-pune-01",
    "store_name": "WizOpt Pune",
    "store_code": "WO-PUN-01",
    "brand": "WIZOPT",
    "entity_id": "ent-wiz",
    "address": "FC Road",
    "city": "Pune",
    "state": "Maharashtra",
    "state_code": "27",
    "pincode": "411004",
    "phone": "020-0000000",
}


def test_header_reflects_passed_store_and_entity():
    """Two different stores -> two distinct headers (name + GSTIN + brand)."""
    h_bv = LegalHeader(BV_ENTITY, BV_STORE_JH, "tax_invoice", doc_number="BV/1")
    h_wiz = LegalHeader(WIZ_ENTITY, WIZ_STORE_MH, "tax_invoice", doc_number="WO/1")

    assert h_bv["legal_name"] == "Better Vision Retail Pvt Ltd"
    assert h_wiz["legal_name"] == "WizOpt Eyewear LLP"
    assert h_bv["legal_name"] != h_wiz["legal_name"]

    # GSTIN is resolved for the STORE's state (Jharkhand 20 vs Maharashtra 27).
    assert h_bv["gstin"] == "20AABCB1234M1Z5"
    assert h_wiz["gstin"] == "27AABFW5678N1Z3"
    assert h_bv["gstin"] != h_wiz["gstin"]

    # Brand surfaces on the header.
    assert h_bv["brand"] == "BETTER_VISION"
    assert h_bv["brand_label"] == "Better Vision"
    assert h_wiz["brand"] == "WIZOPT"
    assert h_wiz["brand_label"] == "WizOpt"


def test_gstin_picks_store_state_not_primary():
    """A BV store in Maharashtra resolves the MH GSTIN, not the JH primary."""
    bv_store_mh = dict(BV_STORE_JH, state="Maharashtra", state_code="27", city="Mumbai")
    h = LegalHeader(BV_ENTITY, bv_store_mh, "tax_invoice")
    assert h["gstin"] == "27AABCB1234M1ZA"


def test_logo_read_from_nested_invoice_identity():
    """The logo lives at entity.invoice.logo_url, not top-level logo_url."""
    assert _entity_logo(BV_ENTITY) == "https://cdn.example.com/bv-logo.png"
    h = LegalHeader(BV_ENTITY, BV_STORE_JH, "tax_invoice")
    assert h["logo_url"] == "https://cdn.example.com/bv-logo.png"
    # Top-level fallback still works for ad-hoc docs.
    assert _entity_logo({"logo_url": "https://x/y.png"}) == "https://x/y.png"


def test_logo_rendered_in_server_html():
    """The challan HTML actually emits an <img> for the entity logo."""
    html = render_delivery_challan(
        entity=BV_ENTITY,
        store=BV_STORE_JH,
        challan_number="DC/ORD/1",
        challan_date="2026-06-18",
        items=[{"product_name": "Frame", "qty": 1}],
    )
    assert "https://cdn.example.com/bv-logo.png" in html
    assert "<img" in html
    # WizOpt store -> WizOpt logo, not BV.
    html_wiz = render_delivery_challan(
        entity=WIZ_ENTITY,
        store=WIZ_STORE_MH,
        challan_number="DC/ORD/2",
        challan_date="2026-06-18",
        items=[{"product_name": "Frame", "qty": 1}],
    )
    assert "wizopt-logo.png" in html_wiz
    assert "WizOpt Eyewear LLP" in html_wiz
    assert "Better Vision" not in html_wiz


def test_no_hardcoded_brand_fallback_for_empty_store():
    """An empty/unconfigured store yields an empty name -- never 'Better Vision'."""
    h = LegalHeader({}, {}, "tax_invoice")
    assert h["legal_name"] == ""
    assert h["trade_name"] == ""
    assert "Better Vision" not in (h["legal_name"] + h["trade_name"] + h["brand_label"])


def test_supplier_identity_html_carries_gstin_and_logo():
    """The compact Rx-card identity block shows the issuing GSTIN + logo."""
    h = LegalHeader(BV_ENTITY, BV_STORE_JH, "rx_card")
    block = supplier_identity_html(h)
    assert "20AABCB1234M1Z5" in block
    assert "Better Vision Retail Pvt Ltd" in block
    assert "bv-logo.png" in block


def test_assert_issuing_identity_fails_loudly():
    """A statutory doc with no resolvable store raises rather than printing blank."""
    with pytest.raises(HTTPException) as ei:
        assert_issuing_identity({}, entity={})
    assert ei.value.status_code == 404

    # A configured store with no GSTIN for its state raises on a GST doc.
    no_gstin_store = dict(BV_STORE_JH, gstin="")
    with pytest.raises(HTTPException) as ei2:
        assert_issuing_identity(no_gstin_store, require_gstin=True, entity={"gstins": []})
    assert ei2.value.status_code == 400

    # A fully-configured store passes.
    assert_issuing_identity(BV_STORE_JH, require_gstin=True, entity=BV_ENTITY)
