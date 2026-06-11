"""
Unification step-9 -- ONE canonical product-create service every door uses.

Locks the step-9 contract from docs/reference/UNIFICATION_AUDIT_2026-06-10.md:
every product-entry door (FORM = POST /products, BULK = /products/bulk-create,
CATALOG = POST /catalog/products) runs the SAME validate+create CORE in
api/services/product_master, so:

  * the SAME incomplete payload is rejected (422 / its door-equivalent) at ALL
    three doors (parity),
  * the SAME complete payload yields an IDENTICAL canonical product at all three
    doors,
  * the owner-decided field reconcile holds: CONTACT_LENS needs BOTH power AND
    expiry_date; HEARING_AID catalogue needs only brand+model (serial is per-unit
    at stock-in),
  * MRP < offer is blocked, and
  * a mirror-write failure NEVER fails the create (fail-soft).

CI-robust: monkeypatches the policy/mirror accessors + builds repos on the
in-memory MockCollection (no live Mongo, no whole-JSON substring matching).

Run: JWT_SECRET_KEY=test python -m pytest \
        backend/tests/test_unification_9_canonical_product_create.py -q
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

import pytest  # noqa: E402

from database.connection import MockCollection  # noqa: E402
from database.repositories.product_repository import ProductRepository  # noqa: E402
from database.repositories.audit_repository import AuditRepository  # noqa: E402
from database.repositories.catalog_variant_repository import (  # noqa: E402
    CatalogVariantRepository,
)
from api.services import product_master as pm  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _mirror_off(monkeypatch):
    """Default the internal mirror OFF for the service-level tests (the mirror
    is asserted explicitly where it matters). Env beats the registry default."""
    monkeypatch.setenv("PM_MIRROR_ENABLED", "")
    monkeypatch.delenv("DISPATCH_MODE", raising=False)
    yield


@pytest.fixture
def product_repo():
    return ProductRepository(MockCollection("products"))


@pytest.fixture
def variant_repo():
    return CatalogVariantRepository(MockCollection("catalog_variants"))


@pytest.fixture
def audit_repo():
    return AuditRepository(MockCollection("audit_logs"))


# A COMPLETE frame, expressed in each door's native payload shape. Same product.
def _form_frame_payload(sku="FR-PARITY-001"):
    """FORM/BULK flat schema: brand/model/color top-level + attributes dict."""
    return {
        "category": "FRAME",
        "sku": sku,
        "brand": "Ray-Ban",
        "model": "RB-2140",
        "color": "BLK",
        "mrp": 5000.0,
        "offer_price": 4500.0,
        "attributes": {},
    }


def _catalog_frame_payload():
    """CATALOG attribute-dict schema: identity lives inside attributes."""
    return {
        "category": "FR",  # short code -> resolves to FRAME
        "attributes": {
            "brand_name": "Ray-Ban",
            "model_no": "RB-2140",
            "colour_code": "BLK",
        },
        "mrp": 5000.0,
        "offer_price": 4500.0,
        "discount_category": "MASS",
    }


def _canonical_fields(doc):
    """The canonical identity/pricing/tax fields a unified door must produce
    identically regardless of which door built it (excludes door-specific
    additive columns + the minted PIM id)."""
    return {
        k: doc.get(k)
        for k in (
            "category",
            "sku_prefix",
            "brand",
            "model",
            "color",
            "mrp",
            "offer_price",
            "hsn_code",
            "gst_rate",
        )
    }


# ---------------------------------------------------------------------------
# 1. PARITY -- same INCOMPLETE payload is rejected at ALL THREE doors
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("source", ["FORM", "BULK", "CATALOG"])
def test_incomplete_frame_rejected_at_every_door(source):
    # A FRAME missing colour_code is incomplete -> rejected at every door's
    # shared core, naming the SAME missing field, with a 422 status.
    if source == "CATALOG":
        payload = {
            "category": "FR",
            "attributes": {"brand_name": "Ray-Ban", "model_no": "RB-2140"},
            "mrp": 5000.0,
            "offer_price": 4500.0,
        }
    else:
        payload = {
            "category": "FRAME",
            "sku": "FR-X",
            "brand": "Ray-Ban",
            "model": "RB-2140",
            "mrp": 5000.0,
            "offer_price": 4500.0,
            "attributes": {},
        }
    with pytest.raises(pm.ProductMasterError) as ei:
        pm.build_canonical_product(payload, source=source)
    assert ei.value.status == 422
    assert ei.value.field == "colour_code"


def test_complete_frame_accepted_at_every_door():
    # The SAME complete frame passes the shared core at every door.
    for payload, source in (
        (_form_frame_payload(), "FORM"),
        (_form_frame_payload(), "BULK"),
        (_catalog_frame_payload(), "CATALOG"),
    ):
        doc = pm.build_canonical_product(payload, source=source)
        assert doc["category"] == "FRAME"


# ---------------------------------------------------------------------------
# 2. PARITY -- same COMPLETE payload yields an IDENTICAL canonical product
# ---------------------------------------------------------------------------


def test_identical_canonical_product_across_all_three_doors():
    form_doc = pm.build_canonical_product(_form_frame_payload(), source="FORM")
    bulk_doc = pm.build_canonical_product(_form_frame_payload(), source="BULK")
    catalog_doc = pm.build_canonical_product(_catalog_frame_payload(), source="CATALOG")

    form_c = _canonical_fields(form_doc)
    bulk_c = _canonical_fields(bulk_doc)
    catalog_c = _canonical_fields(catalog_doc)

    # All three doors derive the SAME canonical identity + GST/HSN + pricing.
    assert form_c == bulk_c == catalog_c
    # And the values are the expected FRAME canonical (5% GST, 9003 HSN).
    assert form_c["category"] == "FRAME"
    assert form_c["sku_prefix"] == "FR"
    assert form_c["brand"] == "Ray-Ban"
    assert form_c["model"] == "RB-2140"
    assert form_c["gst_rate"] == 5.0
    assert str(form_c["hsn_code"]).startswith("9003")


# ---------------------------------------------------------------------------
# 3. Field reconcile (owner decision) -- CONTACT_LENS + HEARING_AID
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("source", ["FORM", "BULK", "CATALOG"])
def test_contact_lens_needs_both_power_and_expiry(source):
    base = {
        "category": "CONTACT_LENS",
        "attributes": {"brand_name": "Acuvue", "model_name": "Oasys"},
        "mrp": 1200.0,
        "offer_price": 1100.0,
    }
    if source != "CATALOG":
        base["sku"] = "CL-X"

    # Missing BOTH -> rejected on power (validated first).
    with pytest.raises(pm.ProductMasterError) as e1:
        pm.build_canonical_product(base, source=source)
    assert e1.value.status == 422
    assert e1.value.field == "power"

    # power present, expiry missing -> rejected on expiry_date.
    with_power = {**base, "attributes": {**base["attributes"], "power": "-2.00"}}
    with pytest.raises(pm.ProductMasterError) as e2:
        pm.build_canonical_product(with_power, source=source)
    assert e2.value.field == "expiry_date"

    # Both present -> accepted.
    complete = {
        **base,
        "attributes": {
            **base["attributes"],
            "power": "-2.00",
            "expiry_date": "2027-01-01",
        },
    }
    doc = pm.build_canonical_product(complete, source=source)
    assert doc["category"] == "CONTACT_LENS"
    assert doc["gst_rate"] == 5.0


@pytest.mark.parametrize("source", ["FORM", "BULK", "CATALOG"])
def test_hearing_aid_catalogue_needs_only_brand_and_model(source):
    # serial_no is per-UNIT at stock-in, NOT a catalogue required field.
    payload = {
        "category": "HEARING_AID",
        "attributes": {"brand_name": "Phonak", "model_no": "Audeo"},
        "mrp": 80000.0,
        "offer_price": 80000.0,
    }
    if source != "CATALOG":
        payload["sku"] = "HA-X"
    doc = pm.build_canonical_product(payload, source=source)
    assert doc["category"] == "HEARING_AID"
    # HEARING_AID is forced NON_DISCOUNTABLE + 0% GST regardless of door.
    assert doc["discount_category"] == "NON_DISCOUNTABLE"
    assert doc["gst_rate"] == 0.0


def test_hearing_aid_missing_model_still_rejected():
    with pytest.raises(pm.ProductMasterError) as ei:
        pm.build_canonical_product(
            {
                "category": "HEARING_AID",
                "attributes": {"brand_name": "Phonak"},
                "mrp": 80000.0,
                "offer_price": 80000.0,
            },
            source="CATALOG",
        )
    assert ei.value.field == "model_no"


# ---------------------------------------------------------------------------
# 4. MRP < offer is blocked at the shared core (every door)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("source", ["FORM", "BULK", "CATALOG"])
def test_offer_above_mrp_blocked_at_every_door(source):
    payload = {
        "category": "FRAME",
        "attributes": {
            "brand_name": "Ray-Ban",
            "model_no": "RB-2140",
            "colour_code": "BLK",
        },
        "mrp": 4000.0,
        "offer_price": 5000.0,  # offer > MRP
    }
    if source != "CATALOG":
        payload["sku"] = "FR-Y"
    with pytest.raises(pm.ProductMasterError) as ei:
        pm.build_canonical_product(payload, source=source)
    assert ei.value.status == 400


# ---------------------------------------------------------------------------
# 5. The full create path (create_via_door) writes the spine + is fail-soft
# ---------------------------------------------------------------------------


def test_create_via_door_writes_spine(product_repo, audit_repo):
    created = pm.create_via_door(
        _form_frame_payload(),
        source="FORM",
        actor="u1",
        product_repo=product_repo,
        audit_repo=audit_repo,
    )
    pid = created["product_id"]
    assert product_repo.find_by_id(pid) is not None
    assert created["sku"] == "FR-PARITY-001"
    assert created["source_door"] == "FORM"


def test_form_extra_fields_ride_along_on_the_spine(product_repo, audit_repo):
    payload = {**_form_frame_payload(), "variant": "Large"}
    created = pm.create_via_door(
        payload,
        source="FORM",
        actor="u1",
        extra_fields={"variant": "Large", "discount_category": None},
        product_repo=product_repo,
        audit_repo=audit_repo,
    )
    stored = product_repo.find_by_id(created["product_id"])
    # The additive door column rode along; a None extra is dropped.
    assert stored.get("variant") == "Large"
    # Canonical keys are never overridden by extra_fields.
    assert stored["category"] == "FRAME"


def test_mirror_failure_never_fails_the_create(product_repo, audit_repo, monkeypatch):
    """A mirror write that raises must NOT fail the create -- the spine is
    written + persisted, and the failed target is recorded fail-soft."""
    monkeypatch.setenv("PM_MIRROR_ENABLED", "1")  # turn the internal mirror ON

    class _BoomVariantRepo:
        def upsert(self, *_a, **_k):
            raise RuntimeError("mirror target down")

    created = pm.create_via_door(
        _form_frame_payload(),
        source="FORM",
        actor="u1",
        product_repo=product_repo,
        variant_repo=_BoomVariantRepo(),
        audit_repo=audit_repo,
    )
    pid = created["product_id"]
    # Create succeeded; the spine is intact despite the mirror blowing up.
    spine = product_repo.find_by_id(pid)
    assert spine is not None
    assert spine["mrp"] == 5000.0 and spine["offer_price"] == 4500.0
    # Compensation recorded the FAILED mirror target.
    assert created["sync_status"]["targets"]["catalog_variants"]["status"] == "FAILED"


# ---------------------------------------------------------------------------
# 6. mirror_enabled defaults ON (step-9 flip) when no env / DB override
# ---------------------------------------------------------------------------


def test_mirror_enabled_defaults_on_when_unset(monkeypatch):
    # With the env override removed, the registry default (flipped ON in step-9)
    # governs. external_mirror_enabled stays False (DISPATCH_MODE not live), so
    # no external write can fire on a fresh deploy.
    monkeypatch.delenv("PM_MIRROR_ENABLED", raising=False)
    monkeypatch.delenv("DISPATCH_MODE", raising=False)
    assert pm.mirror_enabled() is True
    assert pm.external_mirror_enabled() is False


# ---------------------------------------------------------------------------
# 7. HTTP parity -- the SAME incomplete + complete payload at the 3 real doors
# ---------------------------------------------------------------------------


def _su_headers():
    from api.routers.auth import create_access_token

    token = create_access_token(
        {
            "user_id": "u9",
            "username": "u9",
            "roles": ["SUPERADMIN"],
            "store_ids": ["BV-TEST-01"],
            "active_store_id": "BV-TEST-01",
        }
    )
    return {"Authorization": f"Bearer {token}"}


def test_http_incomplete_frame_rejected_at_form_and_catalog(client):
    headers = _su_headers()
    # FORM: a FRAME with no colour_code/color is now incomplete -> 422.
    form_resp = client.post(
        "/api/v1/products",
        headers=headers,
        json={
            "sku": "HTTP-FR-INCOMPLETE",
            "category": "FRAME",
            "brand": "Ray-Ban",
            "model": "RB-9",
            "mrp": 5000.0,
            "offer_price": 4500.0,
        },
    )
    assert form_resp.status_code == 422, form_resp.text

    # CATALOG: the same incomplete frame -> the door's 400 missing-field contract.
    cat_resp = client.post(
        "/api/v1/catalog/products",
        headers=headers,
        json={
            "category": "FR",
            "attributes": {"brand_name": "Ray-Ban", "model_no": "RB-9"},
            "pricing": {"mrp": 5000.0, "offer_price": 4500.0, "discount_category": "MASS"},
        },
    )
    assert cat_resp.status_code == 400, cat_resp.text
    assert "colour_code" in cat_resp.json()["detail"]


def test_http_complete_frame_accepted_at_form_and_catalog(client):
    headers = _su_headers()
    form_resp = client.post(
        "/api/v1/products",
        headers=headers,
        json={
            "sku": "HTTP-FR-COMPLETE-1",
            "category": "FRAME",
            "brand": "Ray-Ban",
            "model": "RB-10",
            "color": "BLK",
            "mrp": 5000.0,
            "offer_price": 4500.0,
        },
    )
    assert form_resp.status_code == 201, form_resp.text
    assert form_resp.json().get("sku") == "HTTP-FR-COMPLETE-1"

    cat_resp = client.post(
        "/api/v1/catalog/products",
        headers=headers,
        json={
            "category": "FR",
            "attributes": {
                "brand_name": "Ray-Ban",
                "model_no": "RB-10",
                "colour_code": "BLK",
            },
            "pricing": {"mrp": 5000.0, "offer_price": 4500.0, "discount_category": "MASS"},
        },
    )
    assert cat_resp.status_code == 200, cat_resp.text
    prod = cat_resp.json()["product"]
    # Same canonical GST/HSN as the FORM door derives for a frame.
    assert prod["gst_rate"] == 5.0


def test_http_bulk_mixed_complete_and_incomplete(client):
    headers = _su_headers()
    resp = client.post(
        "/api/v1/products/bulk-create",
        headers=headers,
        json={
            "products": [
                {
                    "sku": "HTTP-BULK-OK",
                    "category": "FRAME",
                    "brand": "Ray-Ban",
                    "model": "RB-11",
                    "color": "BLK",
                    "mrp": 5000.0,
                    "offer_price": 4500.0,
                },
                {
                    "sku": "HTTP-BULK-BAD",
                    "category": "FRAME",
                    "brand": "Ray-Ban",
                    "model": "RB-12",
                    "mrp": 5000.0,
                    "offer_price": 4500.0,
                },
            ]
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    by_sku = {r["sku"]: r for r in body["results"]}
    assert by_sku["HTTP-BULK-OK"]["ok"] is True
    # The incomplete row (no colour_code) is reported failed, not created.
    assert by_sku["HTTP-BULK-BAD"]["ok"] is False
    assert any("colour_code" in e for e in by_sku["HTTP-BULK-BAD"]["errors"])
