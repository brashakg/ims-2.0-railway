"""
Intent-level tests for the Unified Product Master (PM / N5).

These run against the in-memory MockCollection (no live Mongo) -- the repos use
the 2-arg insert_one/update_one signatures that work on both backends, mirroring
test_catalog_variants.py.

The assertions are INTENT-level (PROTOCOL sec 5): a hollow shell that passes
attributes through without validation, that only writes the spine, that fires an
external write on a fresh deploy, or that corrupts the spine on a mirror failure
must FAIL here.

Run: JWT_SECRET_KEY=test python -m pytest backend/tests/test_pm_product_master.py -q
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
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _mirror_off(monkeypatch):
    """Default to mirror OFF (a fresh deploy). Individual tests opt in."""
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


def _frame_attrs(**over):
    a = {"brand_name": "Burberry", "model_no": "B 3142", "colour_code": "1109/71"}
    a.update(over)
    return a


# ---------------------------------------------------------------------------
# T1 -- Category-conditional required fields (server-side validation)
# ---------------------------------------------------------------------------


def test_contact_lens_missing_power_or_expiry_rejected():
    # Step-9 reconcile: a contact lens needs BOTH power AND expiry_date.
    # Missing power (validated first) is rejected.
    with pytest.raises(pm.ProductMasterError) as ei:
        pm.validate_attributes("CONTACT_LENS", {"brand_name": "Acuvue", "model_name": "Oasys"})
    assert ei.value.status == 422
    assert ei.value.field == "power"
    # With power present but expiry missing, expiry_date is the rejected field.
    with pytest.raises(pm.ProductMasterError) as ei2:
        pm.validate_attributes(
            "CONTACT_LENS",
            {"brand_name": "Acuvue", "model_name": "Oasys", "power": "-2.00"},
        )
    assert ei2.value.status == 422
    assert ei2.value.field == "expiry_date"


def test_hearing_aid_catalogue_serial_not_required():
    # Step-9 reconcile: a HEARING_AID catalogue entry needs only brand+model;
    # serial_no is per-unit at stock-in, so brand+model alone is accepted.
    pm.validate_attributes("HEARING_AID", {"brand_name": "Phonak", "model_no": "Audeo"})
    # Missing model_no is still rejected (it stays required).
    with pytest.raises(pm.ProductMasterError) as ei:
        pm.validate_attributes("HEARING_AID", {"brand_name": "Phonak"})
    assert ei.value.status == 422
    assert ei.value.field == "model_no"


def test_frame_missing_colour_code_rejected():
    with pytest.raises(pm.ProductMasterError) as ei:
        pm.validate_attributes("FRAME", {"brand_name": "Ray-Ban", "model_no": "RB123"})
    assert ei.value.status == 422
    assert ei.value.field == "colour_code"


def test_valid_frame_accepted():
    # Does not raise.
    pm.validate_attributes("FRAME", _frame_attrs())


def test_hearing_aid_type_accepted_with_serial():
    # The HEARING_AID type itself is accepted (was referenced before it existed).
    spec = pm.category_spec("HEARING_AID")
    assert spec is not None and spec.canonical == "HEARING_AID"
    pm.validate_attributes("HEARING_AID", {"brand_name": "Phonak", "model_no": "Audeo", "serial_no": "SN-001"})


# ---------------------------------------------------------------------------
# T2 -- SKU rule (deterministic, format-permissive, unique)
# ---------------------------------------------------------------------------


def test_build_sku_canonical_excel_rule():
    # Burberry sunglass model B 3142 colour 1109/71 -> SG + BURBERRY + B3142 + 1109/71
    sku = pm.build_sku("SUNGLASS", {"brand_name": "Burberry", "model_no": "B 3142", "colour_code": "1109/71"})
    assert sku.startswith("SG")
    assert "BURBERRY" in sku
    # The '/' in the colour code is PRESERVED verbatim (the legacy generate_sku
    # truncated + stripped it; a hollow re-call of generate_sku fails here).
    assert "1109/71" in sku


def test_build_sku_no_truncation():
    # Verbatim concat, not 2/4/3-char truncation.
    sku = pm.build_sku("FRAME", _frame_attrs())
    assert sku == "FRBURBERRYB31421109/71"


def test_mint_unique_sku_collision_appends_counter(product_repo):
    attrs = _frame_attrs()
    first = pm.mint_unique_sku("FRAME", attrs, product_repo=product_repo)
    product_repo.create({"sku": first, "category": "FRAME", "brand": "Burberry", "model": "B 3142", "mrp": 1000, "offer_price": 900, "is_active": True})
    second = pm.mint_unique_sku("FRAME", attrs, product_repo=product_repo)
    assert second != first  # collision resolved with a distinct SKU


def test_legacy_sku_with_slash_is_accepted():
    # A legacy Shopify-style SKU with a '/' must NOT be rejected.
    assert pm.is_acceptable_sku("FRBURBERRYB31421109/7155") is True
    assert pm.is_acceptable_sku("RB-AVTR-BLK-52") is True
    assert pm.is_acceptable_sku("") is False
    assert pm.is_acceptable_sku("bad sku with spaces") is False


def test_create_with_legacy_sku_not_remined(product_repo, audit_repo):
    created = pm.create_product(
        category="FRAME", attributes=_frame_attrs(), mrp=1000, offer_price=900,
        sku="FRBURBERRYB31421109/7155", actor="u1",
        product_repo=product_repo, audit_repo=audit_repo,
    )
    # Legacy SKU preserved exactly, not re-minted.
    assert created["sku"] == "FRBURBERRYB31421109/7155"


# ---------------------------------------------------------------------------
# T3 -- Spine-first write + GATED compensation mirror
# ---------------------------------------------------------------------------


def test_spine_write_succeeds_with_mirror_off(product_repo, variant_repo, audit_repo):
    """Mirror OFF (fresh deploy): the Mongo spine is STILL written + persisted."""
    created = pm.create_product(
        category="FRAME", attributes=_frame_attrs(), mrp=2000, offer_price=1800, actor="u1",
        product_repo=product_repo, variant_repo=variant_repo, audit_repo=audit_repo,
    )
    pid = created["product_id"]
    # Spine persisted.
    assert product_repo.find_by_id(pid) is not None
    # Mirror NOT attempted -- variant tier untouched, status SKIPPED.
    assert variant_repo.get_by_sku(created["sku"]) is None
    targets = created["sync_status"]["targets"]
    assert targets["catalog_products"]["status"] == "SKIPPED"
    assert targets["external"]["status"] == "SKIPPED"


def test_no_external_write_attempted_when_dispatch_off(product_repo, audit_repo, monkeypatch):
    """Even with the PM flag ON, no LIVE external write fires unless
    DISPATCH_MODE=live. On a fresh deploy DISPATCH_MODE is off."""
    monkeypatch.setenv("PM_MIRROR_ENABLED", "1")
    monkeypatch.delenv("DISPATCH_MODE", raising=False)
    # external_mirror_enabled must be False (no live external write).
    assert pm.external_mirror_enabled() is False
    created = pm.create_product(
        category="FRAME", attributes=_frame_attrs(), mrp=1000, offer_price=900, actor="u1",
        product_repo=product_repo, audit_repo=audit_repo,
    )
    assert created["sync_status"]["targets"]["external"]["status"] == "SKIPPED"


def test_internal_mirror_runs_when_flag_on(product_repo, variant_repo, audit_repo, monkeypatch):
    """Flag ON: the INTERNAL (Mongo PIM/variant) mirror runs; the variant row is
    written + linked to the spine's pim_product_id."""
    monkeypatch.setenv("PM_MIRROR_ENABLED", "1")
    created = pm.create_product(
        category="FRAME", attributes=_frame_attrs(), mrp=1000, offer_price=900, actor="u1",
        product_repo=product_repo, variant_repo=variant_repo, audit_repo=audit_repo,
    )
    variant = variant_repo.get_by_sku(created["sku"])
    assert variant is not None
    assert variant["parent_product_id"] == created["pim_product_id"]
    assert created["sync_status"]["targets"]["catalog_variants"]["status"] == "OK"


def test_mirror_failure_never_corrupts_spine(product_repo, audit_repo, monkeypatch):
    """A mirror write that raises must NOT roll back / corrupt the spine."""
    monkeypatch.setenv("PM_MIRROR_ENABLED", "1")

    class _BoomVariantRepo:
        def upsert(self, *_a, **_k):
            raise RuntimeError("mirror target down")

    created = pm.create_product(
        category="FRAME", attributes=_frame_attrs(), mrp=1000, offer_price=900, actor="u1",
        product_repo=product_repo, variant_repo=_BoomVariantRepo(), audit_repo=audit_repo,
    )
    pid = created["product_id"]
    # Spine intact + persisted despite the mirror blowing up.
    spine = product_repo.find_by_id(pid)
    assert spine is not None
    assert spine["mrp"] == 1000 and spine["offer_price"] == 900
    # Compensation recorded the FAILED target.
    assert created["sync_status"]["targets"]["catalog_variants"]["status"] == "FAILED"


# ---------------------------------------------------------------------------
# T4 -- GST is derived server-side, never guessed
# ---------------------------------------------------------------------------


def test_gst_derived_for_frame(product_repo, audit_repo):
    created = pm.create_product(
        category="FRAME", attributes=_frame_attrs(), mrp=1000, offer_price=900, actor="u1",
        product_repo=product_repo, audit_repo=audit_repo,
    )
    assert created["gst_rate"] == 5.0
    assert str(created["hsn_code"]).startswith("9003")


def test_gst_derived_for_sunglass_and_hearing_aid(product_repo, audit_repo):
    sg = pm.create_product(
        category="SUNGLASS",
        attributes={"brand_name": "RayBan", "model_no": "RB1", "colour_code": "BLK"},
        mrp=5000, offer_price=4500, actor="u1", product_repo=product_repo, audit_repo=audit_repo,
    )
    assert sg["gst_rate"] == 18.0
    ha = pm.create_product(
        category="HEARING_AID",
        attributes={"brand_name": "Phonak", "model_no": "Audeo", "serial_no": "SN-1"},
        mrp=80000, offer_price=80000, actor="u1", product_repo=product_repo, audit_repo=audit_repo,
    )
    assert ha["gst_rate"] == 0.0
    # HEARING_AID is forced NON_DISCOUNTABLE.
    assert ha["discount_category"] == "NON_DISCOUNTABLE"


def test_contact_lens_gst_is_5(product_repo, audit_repo):
    cl = pm.create_product(
        category="CONTACT_LENS",
        attributes={
            "brand_name": "Acuvue",
            "model_name": "Oasys",
            "power": "-2.00",
            "expiry_date": "2027-01-01",
        },
        mrp=1200, offer_price=1100, actor="u1", product_repo=product_repo, audit_repo=audit_repo,
    )
    assert cl["gst_rate"] == 5.0


# ---------------------------------------------------------------------------
# T5 -- Pricing invariant on create AND partial update (both directions)
# ---------------------------------------------------------------------------


def test_offer_above_mrp_blocked_on_create(product_repo, audit_repo):
    with pytest.raises(pm.ProductMasterError) as ei:
        pm.create_product(
            category="FRAME", attributes=_frame_attrs(), mrp=1000, offer_price=1500, actor="u1",
            product_repo=product_repo, audit_repo=audit_repo,
        )
    assert ei.value.status == 400


def test_update_raise_offer_above_existing_mrp_blocked(product_repo, audit_repo):
    created = pm.create_product(
        category="FRAME", attributes=_frame_attrs(), mrp=1000, offer_price=900, actor="u1",
        product_repo=product_repo, audit_repo=audit_repo,
    )
    with pytest.raises(pm.ProductMasterError) as ei:
        pm.update_product(
            product_id=created["product_id"], patch={"offer_price": 1500}, actor="u1",
            product_repo=product_repo, audit_repo=audit_repo,
        )
    assert ei.value.status == 400


def test_update_lower_mrp_below_existing_offer_blocked(product_repo, audit_repo):
    created = pm.create_product(
        category="FRAME", attributes=_frame_attrs(), mrp=1000, offer_price=900, actor="u1",
        product_repo=product_repo, audit_repo=audit_repo,
    )
    with pytest.raises(pm.ProductMasterError) as ei:
        pm.update_product(
            product_id=created["product_id"], patch={"mrp": 500}, actor="u1",
            product_repo=product_repo, audit_repo=audit_repo,
        )
    assert ei.value.status == 400


def test_update_luxury_tier_cannot_be_loosened(product_repo, audit_repo):
    created = pm.create_product(
        category="SUNGLASS",
        attributes={"brand_name": "Gucci", "model_no": "G1", "colour_code": "BLK"},
        mrp=20000, offer_price=19500, discount_category="LUXURY", actor="u1",
        product_repo=product_repo, audit_repo=audit_repo,
    )
    assert created["discount_category"] == "LUXURY"
    with pytest.raises(pm.ProductMasterError) as ei:
        pm.update_product(
            product_id=created["product_id"], patch={"discount_category": "MASS"}, actor="u1",
            product_repo=product_repo, audit_repo=audit_repo,
        )
    assert ei.value.status == 400


# ---------------------------------------------------------------------------
# T6 -- HEARING_AID + SERVICE accepted by the Mongo $jsonSchema validator
# ---------------------------------------------------------------------------


def test_schema_includes_hearing_aid_and_service():
    from database.schemas import PRODUCT_SCHEMA

    cat_enum = PRODUCT_SCHEMA["properties"]["category"]["enum"]
    assert "HEARING_AID" in cat_enum
    dc_enum = PRODUCT_SCHEMA["properties"]["discount_category"]["enum"]
    assert "SERVICE" in dc_enum


def test_products_indexes_include_pm_keys():
    from database.schemas import get_all_indexes

    prod_indexes = get_all_indexes()["products"]
    keyed = [tuple(k[0] for k in ix["keys"]) for ix in prod_indexes]
    assert ("pim_product_id",) in keyed
    assert ("sku_prefix",) in keyed
    assert ("category", "brand", "model", "color", "size") in keyed


# ---------------------------------------------------------------------------
# T7 -- PIM superset round-trip (Shopify attributes survive)
# ---------------------------------------------------------------------------


def test_pim_superset_attributes_preserved(product_repo, audit_repo):
    attrs = _frame_attrs(shape="AVIATOR", polarization=True, uv_protection="UV400", tags=["summer", "bestseller"])
    created = pm.create_product(
        category="SUNGLASS",
        attributes={**attrs, "model_no": "RB1", "colour_code": "G15"},
        mrp=5000, offer_price=4500, actor="u1",
        product_repo=product_repo, audit_repo=audit_repo,
    )
    stored = product_repo.find_by_id(created["product_id"])
    a = stored["attributes"]
    assert a["shape"] == "AVIATOR"
    assert a["polarization"] is True
    assert a["uv_protection"] == "UV400"
    assert a["tags"] == ["summer", "bestseller"]


# ---------------------------------------------------------------------------
# T9 -- Audit on every mutation
# ---------------------------------------------------------------------------


def test_audit_on_create(product_repo, audit_repo):
    created = pm.create_product(
        category="FRAME", attributes=_frame_attrs(), mrp=1000, offer_price=900, actor="actor-1",
        product_repo=product_repo, audit_repo=audit_repo,
    )
    rows = audit_repo.find_by_action("product.created")
    assert len(rows) == 1
    assert rows[0]["actor"] == "actor-1"
    assert rows[0]["after"]["product_id"] == created["product_id"]


def test_audit_on_update_and_delete(product_repo, audit_repo):
    created = pm.create_product(
        category="FRAME", attributes=_frame_attrs(), mrp=1000, offer_price=900, actor="u1",
        product_repo=product_repo, audit_repo=audit_repo,
    )
    pm.update_product(
        product_id=created["product_id"], patch={"mrp": 1200}, actor="u1",
        product_repo=product_repo, audit_repo=audit_repo,
    )
    upd_rows = audit_repo.find_by_action("product.updated")
    assert len(upd_rows) == 1
    assert upd_rows[0]["before"]["mrp"] != upd_rows[0]["after"]["mrp"]

    pm.soft_delete_product(
        product_id=created["product_id"], actor="u1",
        product_repo=product_repo, audit_repo=audit_repo,
    )
    del_rows = audit_repo.find_by_action("product.deleted")
    assert len(del_rows) == 1


# ---------------------------------------------------------------------------
# Read paths
# ---------------------------------------------------------------------------


def test_get_and_list_products(product_repo, audit_repo):
    created = pm.create_product(
        category="FRAME", attributes=_frame_attrs(), mrp=1000, offer_price=900, actor="u1",
        product_repo=product_repo, audit_repo=audit_repo,
    )
    assert pm.get_product(created["product_id"], product_repo=product_repo) is not None
    assert pm.get_product(created["sku"], product_repo=product_repo) is not None
    listed = pm.list_products(product_repo=product_repo, category="FRAME")
    assert listed["total"] == 1


# ---------------------------------------------------------------------------
# Role gating -- the new write routes are catalogued to the catalog-mutation
# roles only; the read routes are AUTHENTICATED. (Request-time enforcement is
# the rbac_enforcement middleware reading exactly these POLICY rows.)
# ---------------------------------------------------------------------------


def test_new_routes_role_gated_in_policy():
    from api.services import rbac_policy as rbac

    by_key = {(e["method"], e["path"]): e["allowed"] for e in rbac.POLICY}
    # Writes -> catalog-mutation roles (SUPERADMIN auto-passes via require_roles).
    for method, path in (
        ("POST", "/api/v1/products/sku-preview"),
        ("POST", "/api/v1/products/master"),
        ("PUT", "/api/v1/products/master/{product_id}"),
    ):
        allowed = by_key[(method, path)]
        assert allowed == ["ADMIN", "CATALOG_MANAGER"], (method, path, allowed)
    # Reads -> any authenticated user. (Mounted under /master/ to escape the legacy
    # GET /products/{product_id} shadow.)
    for method, path in (
        ("GET", "/api/v1/products/master/categories"),
        ("GET", "/api/v1/products/master/categories/{category}/fields"),
    ):
        assert by_key[(method, path)] == "AUTHENTICATED", (method, path)


def test_categories_route_not_shadowed_over_http():
    """Adversarial P2 regression: a bare GET /products/categories was a dead 404
    (shadowed by legacy GET /products/{product_id}). The list route is now under
    /master/ -- assert it actually resolves to the category list over HTTP, not a
    404 'Product not found'."""
    import sys
    sys.path.insert(0, "backend")
    from fastapi.testclient import TestClient
    from api.routers.auth import create_access_token
    from api.main import app

    client = TestClient(app)
    token = create_access_token({"user_id": "u1", "username": "u1", "roles": ["ADMIN"]})
    r = client.get(
        "/api/v1/products/master/categories",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "categories" in body and isinstance(body["categories"], list) and body["categories"]
