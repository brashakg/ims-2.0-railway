"""
IMS 2.0 - product-category read path: bool(collection) regression
=================================================================
Regression for BUGCLASS-3 (launch-hardening batch 1): products._get_categories_from_db
did `categories_collection = db.db.get_collection(...)` then `if categories_collection:`
on a PyMongo Collection whose __bool__ raises NotImplementedError. The raise was
swallowed by the surrounding `try/except Exception: return []`, so DB-backed
categories silently NEVER loaded (the app always fell back to the hardcoded list).
Fix = `is not None`. This test proves DB categories now load.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import products as products_mod  # noqa: E402


class _BoolRaisesColl:
    """Mimics a PyMongo Collection: bool(coll) raises (the original bug)."""

    def __init__(self, docs=None, distinct_values=None):
        self.docs = list(docs or [])
        self._distinct = list(distinct_values or [])

    def __bool__(self):
        raise NotImplementedError(
            "Collection objects do not implement truth value testing"
        )

    def find(self, query=None, projection=None):
        return list(self.docs)

    def distinct(self, field):
        return list(self._distinct)


class _RawDatabase:
    """Mimics the raw PyMongo Database accessed via wrapper.db."""

    def __init__(self, collections):
        self._collections = collections

    def get_collection(self, name):
        return self._collections[name]


class _Wrapper:
    is_connected = True

    def __init__(self, collections):
        self.db = _RawDatabase(collections)


def test_categories_load_from_collection_not_swallowed(monkeypatch):
    """When product_categories has docs, they must be returned (not [] from a
    swallowed bool() crash)."""
    cats_coll = _BoolRaisesColl(docs=[{"name": "FRAME"}, {"name": "SUNGLASS"}])
    products_coll = _BoolRaisesColl()
    wrapper = _Wrapper({"product_categories": cats_coll, "products": products_coll})

    import database.connection as conn

    monkeypatch.setattr(conn, "get_db", lambda: wrapper, raising=False)

    result = products_mod._get_categories_from_db()
    assert result == ["FRAME", "SUNGLASS"], result


def test_categories_fall_back_to_products_distinct(monkeypatch):
    """Empty product_categories -> distinct categories from the products
    collection are returned (the second `is not None` guard)."""
    cats_coll = _BoolRaisesColl(docs=[])  # no category docs
    products_coll = _BoolRaisesColl(distinct_values=["CONTACT_LENS", "WATCH"])
    wrapper = _Wrapper({"product_categories": cats_coll, "products": products_coll})

    import database.connection as conn

    monkeypatch.setattr(conn, "get_db", lambda: wrapper, raising=False)

    result = products_mod._get_categories_from_db()
    assert result == ["CONTACT_LENS", "WATCH"], result
