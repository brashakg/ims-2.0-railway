"""
IMS 2.0 — base-repository ObjectId serialization regression
===========================================================
The Customers page "failed to load customers" once a TechCherry store
(BV-PUN-01) was in scope. Root cause: docs inserted directly via
`insert_one` during the May 2026 migration carry a real MongoDB ObjectId
`_id`, and `BaseRepository.find_many` returned them raw. FastAPI then 500'd
trying to JSON-serialise the ObjectId:
    ValueError: [TypeError("'ObjectId' object is not iterable")]

The earlier customers test only exercised the no-DB branch (repo is None),
so it never caught this. These tests drive the repository read paths with a
fake collection whose docs carry a non-string `_id`, asserting it is
stringified on the way out.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

import pytest  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.repositories.base_repository import BaseRepository  # noqa: E402


class _FakeObjectId:
    """Stand-in for bson.ObjectId — not a str, str()s to a hex string,
    and (like the real thing) is NOT JSON-serialisable by default."""

    def __init__(self, hexv: str):
        self._h = hexv

    def __str__(self) -> str:
        return self._h


class _FakeCursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def sort(self, *_args, **_kwargs):
        return self

    def skip(self, n):
        self._docs = self._docs[n:]
        return self

    def limit(self, n):
        self._docs = self._docs[:n]
        return self

    def __iter__(self):
        return iter(self._docs)


class _FakeCollection:
    def __init__(self, docs):
        self._docs = docs

    def find(self, _filter=None):
        return _FakeCursor(self._docs)

    def find_one(self, _filter=None):
        return self._docs[0] if self._docs else None

    def aggregate(self, _pipeline):
        return list(self._docs)


class _TestRepo(BaseRepository):
    @property
    def entity_name(self) -> str:
        return "test"

    @property
    def id_field(self) -> str:
        return "customer_id"


# --------------------------------------------------------------------------
# _clean_id static helper
# --------------------------------------------------------------------------


class TestCleanId:
    def test_stringifies_objectid(self):
        out = BaseRepository._clean_id({"_id": _FakeObjectId("507f1f77bcf86cd799439011")})
        assert out["_id"] == "507f1f77bcf86cd799439011"
        assert isinstance(out["_id"], str)

    def test_leaves_string_id_untouched(self):
        out = BaseRepository._clean_id({"_id": "already-a-string"})
        assert out["_id"] == "already-a-string"

    def test_none_doc_is_safe(self):
        assert BaseRepository._clean_id(None) is None

    def test_doc_without_id_is_safe(self):
        out = BaseRepository._clean_id({"customer_id": "c1", "name": "X"})
        assert "_id" not in out
        assert out["name"] == "X"


# --------------------------------------------------------------------------
# Read paths must stringify ObjectId so FastAPI can serialise
# --------------------------------------------------------------------------


class TestReadPathsStringifyObjectId:
    def _repo(self):
        oid = _FakeObjectId("507f1f77bcf86cd799439011")
        coll = _FakeCollection(
            [
                {
                    "_id": oid,
                    "customer_id": "cust-1",
                    "name": "Pune Customer",
                    "preferred_store_id": "BV-PUN-01",
                }
            ]
        )
        return _TestRepo(coll)

    def test_find_many(self):
        docs = self._repo().find_many({})
        assert len(docs) == 1
        assert isinstance(docs[0]["_id"], str)
        assert docs[0]["_id"] == "507f1f77bcf86cd799439011"
        # business fields preserved
        assert docs[0]["name"] == "Pune Customer"

    def test_find_one(self):
        doc = self._repo().find_one({"customer_id": "cust-1"})
        assert isinstance(doc["_id"], str)

    def test_find_by_id(self):
        doc = self._repo().find_by_id("cust-1")
        assert isinstance(doc["_id"], str)

    def test_aggregate(self):
        docs = self._repo().aggregate([{"$match": {}}])
        assert isinstance(docs[0]["_id"], str)
