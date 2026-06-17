"""
Regression: order-create resolves products by _id / sku, not just product_id
============================================================================
TechCherry-imported products are referenced from the POS by their Mongo `_id`
(a 24-hex ObjectId) or `sku`, not their `product_id`. Before the fix, order
creation 400'd "Product not found: <id>" for those. `_resolve_product_doc`
falls back to sku and _id (string or ObjectId).
"""

import os

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

from bson import ObjectId  # noqa: E402

from api.routers.orders import _resolve_product_doc  # noqa: E402


class _FakeColl:
    def __init__(self, docs):
        self.docs = docs

    def find_one(self, query):
        clauses = query["$or"] if "$or" in query else [query]
        for d in self.docs:
            for c in clauses:
                if all(d.get(k) == v for k, v in c.items()):
                    return d
        return None


class _FakeRepo:
    """Minimal stand-in for ProductRepository (product_id is id_field)."""

    def __init__(self, docs):
        self.collection = _FakeColl(docs)

    def find_by_id(self, pid):
        return self.collection.find_one({"product_id": pid})


def test_resolve_by_product_id():
    repo = _FakeRepo([{"product_id": "P1", "model": "A"}])
    assert _resolve_product_doc(repo, "P1")["model"] == "A"


def test_resolve_by_sku():
    repo = _FakeRepo([{"product_id": "P1", "sku": "SKU-9", "model": "A"}])
    assert _resolve_product_doc(repo, "SKU-9")["model"] == "A"


def test_resolve_by_string_id():
    repo = _FakeRepo([{"_id": "6a0ebf45fbd46b17c0b52ffc", "model": "Imported"}])
    assert _resolve_product_doc(repo, "6a0ebf45fbd46b17c0b52ffc")["model"] == "Imported"


def test_resolve_by_objectid():
    oid = ObjectId()
    repo = _FakeRepo([{"_id": oid, "model": "ObjImported"}])  # no product_id/sku
    assert _resolve_product_doc(repo, str(oid))["model"] == "ObjImported"


def test_missing_returns_none():
    repo = _FakeRepo([{"product_id": "P1"}])
    assert _resolve_product_doc(repo, "NOPE") is None
    assert _resolve_product_doc(repo, "") is None


def test_catalog_only_resolve_surfaces_tier_and_brand(monkeypatch):
    """Products-convergence cap-hole fix: a catalog-only product (absent from
    the spine) must surface its discount_category AND brand so the POS discount
    cap enforces the category / luxury-brand cap. Before the fix the cap
    re-queried the spine only -> None -> the cap silently no-op'd."""
    import api.routers.orders as orders_mod

    cat = _FakeColl([{
        "id": "C1", "sku": "CART-1", "title": "Cartier Frame",
        "category": "FRAME", "brand": "Cartier",
        "pricing": {"mrp": 50000, "offer_price": 50000, "cost_price": 20000,
                    "discount_category": "LUXURY"},
        "is_active": True,
    }])
    monkeypatch.setattr(orders_mod, "_get_catalog_collection", lambda: cat)

    # product_repo=None -> straight to the catalog fallback path.
    doc = _resolve_product_doc(None, "C1")
    assert doc is not None
    assert doc["discount_category"] == "LUXURY"
    assert doc["brand"] == "Cartier"
    assert doc["_resolved_from"] == "catalog_products"

    # The cap engine now computes the lower luxury-brand cap from these fields.
    from api.services.pricing_caps import effective_discount_cap

    cap = effective_discount_cap(doc["discount_category"], doc["brand"])
    assert cap <= 2  # Cartier luxury-brand cap (2%), not the cashier's full cap
