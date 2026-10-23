"""GET /products/website-verdict?product_id= -- the Add/Edit form's "Website
still needs" line. It must be the push gate's own answer (lock / first-publish
readiness / brand, the photo rule, the one price rule) and must NEVER name the
cost price (a catalogue manager cannot fill it; the purchase brings it)."""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

import api.dependencies as deps  # noqa: E402
from api.routers import products as prod_router  # noqa: E402
from strict_fakes import StrictDB  # noqa: E402

_ADMIN = {"user_id": "u-admin", "username": "admin", "roles": ["ADMIN"]}
_PHOTO = "https://cdn.example.com/p.jpg"


class _Up(StrictDB):
    is_connected = True


class _Conn:
    def __init__(self, db):
        self._db = db
        self.is_connected = True

    @property
    def db(self):
        return self._db


def _spine(**over):
    doc = {
        "product_id": "P1", "sku": "FR-RAYBAN-RB2140-901", "pim_product_id": "T1",
        "category": "FRAME", "brand": "Ray-Ban", "model": "RB2140",
        "attributes": {"brand_name": "Ray-Ban", "model_no": "RB2140", "colour_code": "901"},
        "mrp": 5000, "offer_price": 4500, "hsn_code": "900311", "gst_rate": 5,
    }
    doc.update(over)
    return doc


def _needs(monkeypatch, spine, twin=None):
    db = _Up()
    db.seed("brand_masters", [{"brand_id": "b1", "name": "Ray-Ban", "is_active": True,
                               "sync_to_shopify_default": True}])
    db.seed("products", [spine])
    db.seed("catalog_products", [twin] if twin else [])
    db.seed("catalog_variants", [])
    monkeypatch.setattr(deps, "get_db", lambda: _Conn(db))
    return asyncio.run(prod_router.get_website_verdict(brand="Ray-Ban", product_id="P1", current_user=_ADMIN))


def test_a_draft_with_no_photo_and_no_price_lists_each_gate_reason(monkeypatch):
    out = _needs(monkeypatch, _spine(catalog_status="DRAFT", hsn_code="", offer_price=0, mrp=0))
    assert out["online"] is True  # the brand line is unchanged
    needs = out["needs"]
    assert any("catalogue draft" in n for n in needs)
    assert "a photo" in needs
    assert any(n.startswith("a selling price") for n in needs)
    assert "hsn_code" in out["gaps"]
    assert "cost_price" not in out["gaps"] and not any("cost" in n for n in needs)


def test_a_finished_photographed_priced_product_needs_nothing(monkeypatch):
    twin = {"id": "T1", "sku": "FR-RAYBAN-RB2140-901", "brand": "Ray-Ban", "mrp": 5000,
            "offer_price": 4500, "images": [_PHOTO]}
    out = _needs(monkeypatch, _spine(catalog_status="ACTIVE"), twin)
    assert out["needs"] == [] and out["gaps"] == []


def test_only_cost_missing_is_never_listed(monkeypatch):
    """A draft whose ONLY gap is the cost price is ready for the website (the
    first-publish rule is gaps_before_cost) -- the line must not name cost."""
    twin = {"id": "T1", "sku": "FR-RAYBAN-RB2140-901", "brand": "Ray-Ban", "mrp": 5000,
            "offer_price": 4500, "images": [_PHOTO]}
    out = _needs(monkeypatch, _spine(catalog_status="DRAFT"), twin)
    assert out["needs"] == [] and out["gaps"] == []


def test_an_unknown_product_is_not_guessed(monkeypatch):
    out = _needs(monkeypatch, _spine(product_id="OTHER"))
    assert out["needs"] is None and out["gaps"] == []


def test_without_product_id_the_shape_is_unchanged(monkeypatch):
    db = _Up()
    db.seed("brand_masters", [])
    monkeypatch.setattr(deps, "get_db", lambda: _Conn(db))
    out = asyncio.run(prod_router.get_website_verdict(brand="X", current_user=_ADMIN))
    assert "needs" not in out and "gaps" not in out
