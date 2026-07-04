"""
IMS 2.0 - /catalog/* correctness + validation hardening
=======================================================
Regression tests for the catalog-module audit. They pin bugs that were fixed
without touching the canonical /products path or the shared pricing_caps /
gst_rates services:

  1. PERSISTENCE (mock-mode): the /catalog/products CRUD used
     `db.get_collection(...)` + `update_one(..., upsert=True)` +
     `find_one(filter, projection)`, none of which the seeded in-memory
     MockDatabase / MockCollection supports -> the create/get/update path
     500'd (AttributeError / TypeError) whenever Mongo wasn't connected (a
     fresh deploy before Mongo connects, or local / test mock mode). Now uses
     subscript access + an explicit update-or-insert + Python-side _id strip.

  2. UPDATE MRP >= offer_price guard: the create path blocked offer > MRP, but
     the UPDATE path merged pricing with NO re-validation, so a partial pricing
     edit could push a product into MRP < offer. Now enforced on the EFFECTIVE
     post-merge pricing via the SHARED pricing_caps validator (mirrors the
     equivalent products.update_product fix).

  3. SCHEMA bounds: PricingInput.offer_price / cost_price must be > 0 when
     supplied (a negative offer slipped through because the MRP-rule guard only
     catches offer > MRP and `offer or mrp` is truthy for a negative). And
     discount_category must be one of the canonical cap tiers (an unknown tier
     silently degrades to MASS=15% in the cap resolver).

All assertions hold with OR without a live Mongo: the create/update handlers
return the built product doc, and the save layer is fail-soft to an in-memory
dict, so the HTTP-level checks don't depend on a DB.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

import pytest  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import catalog  # noqa: E402


def _frame_payload(mrp, offer_price=None, gst_rate=None, discount_category="MASS"):
    """Minimal VALID frame create payload (FR -> 5% GST)."""
    pricing = {"mrp": mrp, "discount_category": discount_category}
    if offer_price is not None:
        pricing["offer_price"] = offer_price
    payload = {
        "category": "FR",
        "attributes": {
            "brand_name": "Ray-Ban",
            "model_no": "RB-HARD-001",
            "colour_code": "BLK",
        },
        "pricing": pricing,
    }
    if gst_rate is not None:
        payload["gst_rate"] = gst_rate
    return payload


@pytest.fixture(autouse=True)
def _clear_inmemory_catalog():
    catalog.CATALOG_PRODUCTS.clear()
    yield
    catalog.CATALOG_PRODUCTS.clear()


# ---------------------------------------------------------------------------
# 1. Persistence round-trip (regression for the mock-mode CRUD 500)
# ---------------------------------------------------------------------------


class TestCatalogPersistenceRoundTrip:
    def test_create_then_get_then_update_round_trip(self, client, auth_headers):
        """create -> get -> update must all 2xx (no 500 from the broken
        get_collection / upsert / find_one-projection mock incompatibilities)."""
        created = client.post(
            "/api/v1/catalog/products",
            json=_frame_payload(mrp=4000, offer_price=3600),
            headers=auth_headers,
        )
        assert created.status_code == 200, created.text
        pid = created.json()["product"]["id"]

        got = client.get(f"/api/v1/catalog/products/{pid}", headers=auth_headers)
        assert got.status_code == 200, got.text
        assert got.json()["product"]["id"] == pid
        # The Mongo _id must never leak into the catalog doc shape.
        assert "_id" not in got.json()["product"]

        updated = client.put(
            f"/api/v1/catalog/products/{pid}",
            json={"description": "Updated desc"},
            headers=auth_headers,
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["product"]["description"] == "Updated desc"


# ---------------------------------------------------------------------------
# 2. UPDATE MRP >= offer_price guard
# ---------------------------------------------------------------------------


class TestCatalogUpdateMrpOfferGuard:
    def _create(self, client, headers, mrp, offer):
        resp = client.post(
            "/api/v1/catalog/products",
            json=_frame_payload(mrp=mrp, offer_price=offer),
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        return resp.json()["product"]["id"]

    def test_raising_offer_above_existing_mrp_is_rejected(self, client, auth_headers):
        """Existing mrp 4000; raise offer alone to 5000 -> 400 (was NOT blocked
        before -- the update path skipped the MRP-rule guard)."""
        pid = self._create(client, auth_headers, 4000, 3600)
        resp = client.put(
            f"/api/v1/catalog/products/{pid}",
            json={"pricing": {"mrp": 4000, "offer_price": 5000}},
            headers=auth_headers,
        )
        assert resp.status_code == 400, resp.text
        assert "MRP" in resp.json()["detail"]

    def test_lowering_mrp_below_existing_offer_is_rejected(self, client, auth_headers):
        """Existing offer 3600; drop mrp to 3000 (offer omitted, so the existing
        3600 is retained) -> 3600 > 3000 -> 400."""
        pid = self._create(client, auth_headers, 4000, 3600)
        resp = client.put(
            f"/api/v1/catalog/products/{pid}",
            json={"pricing": {"mrp": 3000}},
            headers=auth_headers,
        )
        assert resp.status_code == 400, resp.text
        assert "MRP" in resp.json()["detail"]

    def test_valid_pricing_update_succeeds(self, client, auth_headers):
        pid = self._create(client, auth_headers, 4000, 3600)
        resp = client.put(
            f"/api/v1/catalog/products/{pid}",
            json={"pricing": {"mrp": 5000, "offer_price": 4500}},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["product"]["pricing"]["offer_price"] == 4500.0

    def test_offer_equal_mrp_update_allowed(self, client, auth_headers):
        pid = self._create(client, auth_headers, 4000, 3600)
        resp = client.put(
            f"/api/v1/catalog/products/{pid}",
            json={"pricing": {"mrp": 4000, "offer_price": 4000}},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# 3. Schema bounds (offer/cost price > 0; discount_category in canonical set)
# ---------------------------------------------------------------------------


class TestCatalogPricingSchema:
    def test_negative_offer_price_rejected_422(self, client, auth_headers):
        resp = client.post(
            "/api/v1/catalog/products",
            json=_frame_payload(mrp=4000, offer_price=-100),
            headers=auth_headers,
        )
        assert resp.status_code == 422, resp.text

    def test_zero_offer_price_rejected_422(self, client, auth_headers):
        resp = client.post(
            "/api/v1/catalog/products",
            json=_frame_payload(mrp=4000, offer_price=0),
            headers=auth_headers,
        )
        assert resp.status_code == 422, resp.text

    def test_negative_cost_price_rejected_422(self, client, auth_headers):
        payload = _frame_payload(mrp=4000, offer_price=3600)
        payload["pricing"]["cost_price"] = -50
        resp = client.post(
            "/api/v1/catalog/products", json=payload, headers=auth_headers
        )
        assert resp.status_code == 422, resp.text

    def test_unknown_discount_category_rejected_422(self, client, auth_headers):
        resp = client.post(
            "/api/v1/catalog/products",
            json=_frame_payload(mrp=4000, offer_price=3600, discount_category="GOLD"),
            headers=auth_headers,
        )
        assert resp.status_code == 422, resp.text

    def test_discount_category_normalized_to_upper(self, client, auth_headers):
        """A lowercase but otherwise valid tier is accepted + persisted UPPER so
        it matches what the cap resolver expects."""
        resp = client.post(
            "/api/v1/catalog/products",
            json=_frame_payload(mrp=4000, offer_price=3600, discount_category="luxury"),
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["product"]["pricing"]["discount_category"] == "LUXURY"

    def test_all_canonical_tiers_accepted(self):
        """Every canonical cap tier validates at the model layer (no DB)."""
        for tier in ("MASS", "PREMIUM", "LUXURY", "SERVICE", "NON_DISCOUNTABLE"):
            model = catalog.PricingInput(
                mrp=1000, offer_price=900, discount_category=tier
            )
            assert model.discount_category == tier


# ---------------------------------------------------------------------------
# 4. Unit-level guard assertions (run even without the FastAPI app / DB)
# ---------------------------------------------------------------------------


class TestCatalogGuardUnit:
    def test_negative_offer_rejected_by_schema(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            catalog.PricingInput(mrp=1000, offer_price=-1)

    def test_unknown_tier_rejected_by_schema(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            catalog.PricingInput(mrp=1000, discount_category="PLATINUM")


# ---------------------------------------------------------------------------
# F6: the products SPINE write is a HARD gate on catalog create. A spine
# failure must abort the create (no hidden catalog-only product), mapping a
# duplicate identity/SKU -> 409 and any other failure -> 500.
# ---------------------------------------------------------------------------


class TestCatalogSpineWriteIsHardGate:
    def _patch_repo(self, monkeypatch, repo):
        # create_catalog_product does `from ..dependencies import
        # get_product_repository` at call time, so patch the source module.
        from api import dependencies as deps

        monkeypatch.setattr(deps, "get_product_repository", lambda: repo)

    def test_spine_write_exception_aborts_with_500_and_no_catalog_doc(
        self, client, auth_headers, monkeypatch
    ):
        class _FailingRepo:
            def create(self, doc, raise_on_duplicate=False):
                raise RuntimeError("spine DB down")

        self._patch_repo(monkeypatch, _FailingRepo())
        resp = client.post(
            "/api/v1/catalog/products",
            json=_frame_payload(mrp=4000, offer_price=3600),
            headers=auth_headers,
        )
        assert resp.status_code == 500, resp.text
        # Hard fail: the spine write raises BEFORE the catalog doc is saved, so
        # the response is an error with no product (no orphan catalog-only doc).
        assert "product" not in resp.json()

    def test_spine_write_returning_none_aborts_with_500(
        self, client, auth_headers, monkeypatch
    ):
        class _NoneRepo:
            def create(self, doc, raise_on_duplicate=False):
                return None  # swallowed DB error

        self._patch_repo(monkeypatch, _NoneRepo())
        resp = client.post(
            "/api/v1/catalog/products",
            json=_frame_payload(mrp=4000, offer_price=3600),
            headers=auth_headers,
        )
        assert resp.status_code == 500, resp.text
        # Hard fail: the spine write raises BEFORE the catalog doc is saved, so
        # the response is an error with no product (no orphan catalog-only doc).
        assert "product" not in resp.json()

    def test_spine_duplicate_maps_to_409(self, client, auth_headers, monkeypatch):
        class DuplicateKeyError(Exception):
            pass

        class _DupRepo:
            def create(self, doc, raise_on_duplicate=False):
                raise DuplicateKeyError("E11000 duplicate key")

        self._patch_repo(monkeypatch, _DupRepo())
        resp = client.post(
            "/api/v1/catalog/products",
            json=_frame_payload(mrp=4000, offer_price=3600),
            headers=auth_headers,
        )
        assert resp.status_code == 409, resp.text
        # Hard fail: the spine write raises BEFORE the catalog doc is saved, so
        # the response is an error with no product (no orphan catalog-only doc).
        assert "product" not in resp.json()

    def test_spine_success_saves_catalog_doc(self, client, auth_headers, monkeypatch):
        saved = []

        class _OkRepo:
            def create(self, doc, raise_on_duplicate=False):
                saved.append(doc)
                return doc

        self._patch_repo(monkeypatch, _OkRepo())
        resp = client.post(
            "/api/v1/catalog/products",
            json=_frame_payload(mrp=4000, offer_price=3600),
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        assert len(saved) == 1  # spine persisted (hard gate passed)
        # Catalog doc persisted + retrievable by the returned id.
        pid = resp.json()["product"]["id"]
        got = client.get(f"/api/v1/catalog/products/{pid}", headers=auth_headers)
        assert got.status_code == 200, got.text
