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
