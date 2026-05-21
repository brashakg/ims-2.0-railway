"""
IMS 2.0 — stock-transfer persistence
====================================
Stock transfers used to live in a module-level dict (STOCK_TRANSFERS), so
they were lost on every redeploy and invisible across Railway workers even
though the transfer feature is live in the UI. They now persist to the
`stock_transfers` collection via _save_transfer / _get_transfer / _all_transfers,
with the in-memory dict kept only as a fail-soft fallback.

These tests exercise the persistence helpers with a fake collection (proving
the Mongo wiring) and the in-memory fallback, plus the enum coercion that
keeps the docs BSON-serialisable.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

import pytest  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import transfers  # noqa: E402


class _FakeColl:
    """Minimal stand-in for a pymongo collection."""

    def __init__(self):
        self.docs = {}

    def update_one(self, flt, update, upsert=False):
        _id = flt["id"]
        doc = self.docs.get(_id, {})
        doc.update(update["$set"])
        self.docs[_id] = doc

    def find_one(self, flt, projection=None):
        d = self.docs.get(flt["id"])
        return dict(d) if d else None

    def find(self, flt=None, projection=None):
        return [dict(d) for d in self.docs.values()]

    def count_documents(self, flt=None):
        return len(self.docs)


class TestEnumCoercion:
    def test_coerce_nested_enums_to_strings(self):
        out = transfers._coerce(
            {
                "status": transfers.TransferStatus.IN_TRANSIT,
                "priority": transfers.TransferPriority.URGENT,
                "items": [{"s": transfers.TransferStatus.RECEIVED}],
            }
        )
        assert out["status"] == "in_transit"
        assert out["priority"] == "urgent"
        assert out["items"][0]["s"] == "received"


class TestCollectionPersistence:
    def test_save_then_get_round_trip(self, monkeypatch):
        fake = _FakeColl()
        monkeypatch.setattr(transfers, "_transfers_coll", lambda: fake)

        transfers._save_transfer(
            {"id": "t1", "status": transfers.TransferStatus.APPROVED}
        )
        # persisted to the collection, enum stored as a plain string
        assert "t1" in fake.docs
        assert fake.docs["t1"]["status"] == "approved"

        got = transfers._get_transfer("t1")
        assert got["id"] == "t1"
        assert got["status"] == "approved"

    def test_all_transfers_reads_collection(self, monkeypatch):
        fake = _FakeColl()
        monkeypatch.setattr(transfers, "_transfers_coll", lambda: fake)
        transfers._save_transfer({"id": "a", "status": "draft"})
        transfers._save_transfer({"id": "b", "status": "draft"})
        ids = {t["id"] for t in transfers._all_transfers()}
        assert ids == {"a", "b"}

    def test_transfer_number_uses_db_count(self, monkeypatch):
        fake = _FakeColl()
        fake.docs = {"x": {}, "y": {}}
        monkeypatch.setattr(transfers, "_transfers_coll", lambda: fake)
        num = transfers.generate_transfer_number()
        assert num.startswith("TRF-")
        assert num.endswith("-1003")  # 2 existing + 1001


class TestInMemoryFallback:
    def test_fallback_when_no_db(self, monkeypatch):
        monkeypatch.setattr(transfers, "_transfers_coll", lambda: None)
        transfers.STOCK_TRANSFERS.clear()

        transfers._save_transfer({"id": "mem1", "status": "draft"})
        assert transfers._get_transfer("mem1")["id"] == "mem1"
        assert any(t["id"] == "mem1" for t in transfers._all_transfers())

    def test_get_missing_returns_none(self, monkeypatch):
        monkeypatch.setattr(transfers, "_transfers_coll", lambda: None)
        transfers.STOCK_TRANSFERS.clear()
        assert transfers._get_transfer("nope") is None
