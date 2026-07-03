"""
IMS 2.0 - product images persist through the create/update doors
================================================================
The Add-Product form uploads images BEFORE create and sends their URLs in the
payload's `images` array -- but ProductCreate never modelled the field, so
pydantic silently DROPPED it and every product saved imageless (masked while
the GridFS store was broken; live-confirmed on prod 2026-07-03). These tests
pin the fix: images ride ProductCreate/ProductUpdate + the door's extra_fields
onto the spine doc.

Run: JWT_SECRET_KEY=test python -m pytest backend/tests/test_product_images_persist.py -q
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

import pytest  # noqa: E402
from pydantic import ValidationError  # noqa: E402

from api.routers import products as pr  # noqa: E402


_IMG = "https://ims-20-railway-production.up.railway.app/api/v1/products/image/abc123"


def _create(**over):
    base = dict(
        category="SG", brand="Ray-Ban", model="RB3025",
        attributes={"brand_name": "Ray-Ban", "model_no": "RB3025", "colour_code": "L0205"},
        mrp=8690, offer_price=8690,
    )
    base.update(over)
    return pr.ProductCreate(**base)


class TestCreateModelsImages:
    def test_images_accepted_and_cleaned(self):
        p = _create(images=[_IMG, "  ", "/api/v1/products/image/def456"])
        assert p.images == [_IMG, "/api/v1/products/image/def456"]

    def test_images_flow_into_door_extra_fields(self):
        p = _create(images=[_IMG])
        extra = pr._form_extra_fields(p)
        assert extra["images"] == [_IMG]

    def test_absent_images_stay_absent(self):
        p = _create()
        assert p.images is None
        assert "images" not in pr._form_extra_fields(p)

    def test_junk_urls_rejected(self):
        with pytest.raises(ValidationError):
            _create(images=["javascript:alert(1)"])
        with pytest.raises(ValidationError):
            _create(images="not-a-list")
        with pytest.raises(ValidationError):
            _create(images=[_IMG] * 13)

    def test_registry_field_is_catalogued(self):
        # images must be in the door's pass-through tuple, or the spine drops it
        assert "images" in pr._FORM_EXTRA_FIELDS


class TestUpdateModelsImages:
    def test_update_accepts_images(self):
        u = pr.ProductUpdate(images=[_IMG])
        assert u.model_dump(exclude_unset=True)["images"] == [_IMG]

    def test_update_without_images_excludes_key(self):
        u = pr.ProductUpdate(mrp=100)
        assert "images" not in u.model_dump(exclude_unset=True)
