"""
Catalog field-parity (#17) -- the ONE canonical per-category field registry.

Locks the contract that GET /products/categories is THE single source of truth
the three product-entry doors (Quick Add / Guided / Rapid Grid) read to learn,
per category, which attribute fields are REQUIRED vs optional -- and that the
payload it returns is derived directly from product_master CATEGORY_SPECS (the
SAME registry the create gate enforces). This is what removes the FE/BE drift
that previously let the doors disagree with the server on required-ness.

Two layers:
  * PURE: product_master.all_category_specs() shape + values (no Mongo, no HTTP).
  * HTTP: GET /products/categories returns it, is auth-gated to any logged-in
    user (read-only metadata), and routes BEFORE /{product_id} (no collision).

The companion create-enforcement parity (same incomplete payload 422'd at all
three doors, bulk rows rejected-with-reason) is locked in
test_unification_9_canonical_product_create.py; this file locks the registry the
doors consume so the FE can derive required-ness from it instead of a 2nd copy.

Run: JWT_SECRET_KEY=test python -m pytest \
        backend/tests/test_category_registry_endpoint.py -q
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

import pytest  # noqa: E402

from api.services import product_master as pm  # noqa: E402


# ===========================================================================
# 1. PURE -- the registry payload shape + values come straight from CATEGORY_SPECS
# ===========================================================================


def _by_code():
    return {c["code"]: c for c in pm.all_category_specs()}


def test_registry_covers_every_canonical_category():
    specs = _by_code()
    assert set(specs.keys()) == set(pm.canonical_categories())
    assert len(specs) == 13


def test_each_entry_carries_the_render_ready_contract():
    for entry in pm.all_category_specs():
        # Keys the FE doors rely on (productAddShared.getCategoryRegistry).
        for key in (
            "code",
            "sku_prefix",
            "name",
            "required_fields",
            "optional_fields",
            "fields",
            "forced_discount_category",
        ):
            assert key in entry, f"{entry.get('code')} missing {key}"
        assert isinstance(entry["required_fields"], list)
        assert isinstance(entry["optional_fields"], list)
        assert isinstance(entry["fields"], list)


@pytest.mark.parametrize("canonical", sorted(pm.canonical_categories()))
def test_required_optional_lists_match_the_spec_registry(canonical):
    entry = _by_code()[canonical]
    spec = pm.category_spec(canonical)
    assert entry["required_fields"] == list(spec.required)
    assert entry["optional_fields"] == list(spec.optional)
    assert entry["sku_prefix"] == spec.prefix


@pytest.mark.parametrize("canonical", sorted(pm.canonical_categories()))
def test_fields_array_required_flag_equals_required_fields(canonical):
    """The render-ready `fields` array (what the FE marks required + blocks
    submit on) must agree EXACTLY with required_fields -- this is the contract
    that keeps the FE required markers in lockstep with the server gate."""
    entry = _by_code()[canonical]
    required_from_fields = {f["name"] for f in entry["fields"] if f["required"]}
    assert required_from_fields == set(entry["required_fields"])
    # Every field carries a non-empty human label + a name + a bool required.
    for f in entry["fields"]:
        assert f["name"]
        assert isinstance(f["label"], str) and f["label"].strip()
        assert isinstance(f["required"], bool)
    # The fields array enumerates exactly required + optional (no extras, no gaps).
    names = {f["name"] for f in entry["fields"]}
    assert names == set(entry["required_fields"]) | set(entry["optional_fields"])


def test_cost_price_is_never_a_registry_required_field():
    """cost_price is GRN-deferred: required to make a product ACTIVE/sellable,
    NEVER to save it. It must not appear as a required field on any category."""
    for entry in pm.all_category_specs():
        assert "cost_price" not in entry["required_fields"]
        assert "cost_price" not in {f["name"] for f in entry["fields"]}


def test_forced_discount_category_locked_for_ha_and_services():
    specs = _by_code()
    assert specs["HEARING_AID"]["forced_discount_category"] == "NON_DISCOUNTABLE"
    assert specs["SERVICES"]["forced_discount_category"] == "SERVICE"
    # An ordinary category leaves the operator to choose (None).
    assert specs["FRAME"]["forced_discount_category"] is None


def test_reconciled_required_sets_are_frozen():
    """Owner-decided field reconcile (step-9) -- freeze the exact required set so
    a future edit that drifts a door's required fields fails loudly here."""
    specs = _by_code()
    assert set(specs["FRAME"]["required_fields"]) == {
        "brand_name",
        "model_no",
        "colour_code",
    }
    assert set(specs["CONTACT_LENS"]["required_fields"]) == {
        "brand_name",
        "model_name",
        "power",
        "expiry_date",
    }
    assert set(specs["HEARING_AID"]["required_fields"]) == {"brand_name", "model_no"}
    assert set(specs["SERVICES"]["required_fields"]) == {"name"}


# ===========================================================================
# 2. HTTP -- GET /products/categories serves the registry, auth-gated
# ===========================================================================


def test_http_get_categories_returns_the_registry(client, auth_headers):
    resp = client.get("/api/v1/products/categories", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "categories" in body
    codes = {c["code"] for c in body["categories"]}
    assert codes == set(pm.canonical_categories())
    # The HTTP payload equals the pure registry PLUS the Catalog-Dictionary
    # enrichment: per-field `options` may be attached (brand_name from the
    # Brand Master, other fields from Settings -> Catalog Dictionary). Strip
    # the additive key and the payload must equal the pure registry exactly.
    stripped = [
        {
            **cat,
            "fields": [
                {k: v for k, v in fld.items() if k != "options"}
                for fld in cat.get("fields", [])
            ],
        }
        for cat in body["categories"]
    ]
    assert stripped == pm.all_category_specs()
    # Any attached options list is a list of strings (shape contract).
    for cat in body["categories"]:
        for fld in cat.get("fields", []):
            if "options" in fld:
                assert isinstance(fld["options"], list)
                assert all(isinstance(o, str) for o in fld["options"])


def test_http_get_categories_is_readable_by_any_authenticated_user(
    client, staff_headers
):
    # Read-only metadata: a sales-staff token (not a catalog role) can still read
    # the registry -- the create gate is role-protected separately.
    resp = client.get("/api/v1/products/categories", headers=staff_headers)
    assert resp.status_code == 200, resp.text
    assert "categories" in resp.json()


def test_http_get_categories_requires_auth(client):
    resp = client.get("/api/v1/products/categories")
    assert resp.status_code in (401, 403)


def test_categories_route_does_not_collide_with_product_id(client, auth_headers):
    """GET /products/categories must resolve to the registry, NOT be swallowed by
    GET /products/{product_id} treating 'categories' as an id."""
    resp = client.get("/api/v1/products/categories", headers=auth_headers)
    assert resp.status_code == 200
    # The registry shape (a categories list), not a single-product 404/shape.
    assert isinstance(resp.json().get("categories"), list)


def test_new_eyewear_fields_in_registry_and_dictionary_options_stamped(
    client, auth_headers
):
    """2026-07-04 form rework: the SUNGLASS/FRAME registry carries the reworked
    field set (model_name, frame_color, temple_color, ...), and a Catalog
    Dictionary list saved for one of those fields is stamped as `options` on
    the GET /products/categories payload (the Add-Product form renders it as a
    restricted select). Mock-db environments 503 the PATCH -- then only the
    pure registry membership is asserted."""
    # 1) Pure registry membership (works with or without a live db).
    resp = client.get("/api/v1/products/categories", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    cats = {c["code"]: c for c in resp.json()["categories"]}
    sun_fields = {f["name"]: f for f in cats["SUNGLASS"]["fields"]}
    for key in ("model_name", "frame_color", "temple_color", "tint",
                "frame_material", "warranty", "country_of_origin"):
        assert key in sun_fields, f"SUNGLASS registry missing '{key}'"
    assert "full_model_no" not in sun_fields
    assert "gender_label" not in sun_fields

    # 2) Dictionary stamping (needs a live db; PATCH 503s on mock backends).
    field, scope = "frame_color", "SUNGLASS"
    values = ["Matte Black", "Tortoise Shell"]
    r = client.patch(
        f"/api/v1/catalog-field-options/{field}",
        json={"items": values, "category": scope},
        headers=auth_headers,
    )
    assert r.status_code in (200, 503)
    if r.status_code != 200:
        return  # no live db in this environment -- stamping not testable here
    try:
        resp2 = client.get("/api/v1/products/categories", headers=auth_headers)
        assert resp2.status_code == 200, resp2.text
        cats2 = {c["code"]: c for c in resp2.json()["categories"]}
        sun2 = {f["name"]: f for f in cats2["SUNGLASS"]["fields"]}
        assert sun2[field].get("options") == values
        # The category-scoped list must NOT bleed onto FRAME's same-named field.
        fr2 = {f["name"]: f for f in cats2["FRAME"]["fields"]}
        assert fr2[field].get("options") != values
    finally:
        # Clean up: un-configure the scope so other tests see a pristine state.
        client.patch(
            f"/api/v1/catalog-field-options/{field}",
            json={"items": [], "category": scope},
            headers=auth_headers,
        )
