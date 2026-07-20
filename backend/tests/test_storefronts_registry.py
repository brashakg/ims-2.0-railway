"""
Tests for the storefronts registry (api.services.storefronts) -- WizOpt Phase 0.

Guarantees under test:
  * ensure(dry_run=True) inspects only -- it returns a plan and writes NOTHING
    (the safe default posture on a live system).
  * ensure(dry_run=False) seeds exactly the single BV row and is IDEMPOTENT --
    a second call never clobbers or duplicates it.
  * The seeded row carries the reserved-but-unset keys so the schema is stable.

Uses a tiny in-memory collection (no real Mongo).

Run: JWT_SECRET_KEY=test python -m pytest backend/tests/test_storefronts_registry.py -q
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

import pytest  # noqa: E402

from api.services import storefronts  # noqa: E402


class _FakeColl:
    def __init__(self):
        self.docs = []

    def find_one(self, query, projection=None):
        for d in self.docs:
            if all(d.get(k) == v for k, v in query.items()):
                return {k: v for k, v in d.items() if k != "_id"}
        return None

    def find(self, query=None, projection=None):
        return [dict(d) for d in self.docs]

    def update_one(self, filt, update, upsert=False):
        for d in self.docs:
            if all(d.get(k) == v for k, v in filt.items()):
                return  # exists -> $setOnInsert is a no-op
        if upsert:
            self.docs.append(dict(update.get("$setOnInsert") or {}))


class _FakeDB:
    def __init__(self):
        self._coll = _FakeColl()

    def get_collection(self, name):
        assert name == "storefronts"
        return self._coll


def test_dry_run_writes_nothing_and_returns_plan():
    db = _FakeDB()
    res = storefronts.ensure_default_storefront(db, dry_run=True)
    assert res["ok"] is True
    assert res["dry_run"] is True
    assert res["action"] == "would_create"
    assert res["storefront_id"] == "BV"
    assert res["plan"]["name"] == "Better Vision Online"
    # Nothing persisted.
    assert db._coll.docs == []
    assert storefronts.get_storefront(db, "BV") is None


def test_ensure_seeds_single_bv_row():
    db = _FakeDB()
    res = storefronts.ensure_default_storefront(db, dry_run=False)
    assert res["action"] == "created"
    row = storefronts.get_storefront(db, "BV")
    assert row is not None
    assert row["storefront_id"] == "BV"
    assert row["name"] == "Better Vision Online"
    assert row["is_default"] is True
    assert row["status"] == "ACTIVE"
    assert row["brand"] == "BETTER_VISION"
    # Reserved-but-unset keys are present (stable schema).
    for k in (
        "shop_domain",
        "entity_id",
        "online_store_id",
        "fulfillment_policy",
        "membership_default",
    ):
        assert k in row and row[k] is None


def test_ensure_is_idempotent():
    db = _FakeDB()
    storefronts.ensure_default_storefront(db, dry_run=False)
    second = storefronts.ensure_default_storefront(db, dry_run=False)
    assert second["action"] == "exists"
    # Exactly ONE row -- never duplicated.
    assert len(db._coll.docs) == 1
    assert len(storefronts.list_storefronts(db)) == 1


def test_ensure_failsoft_without_db(monkeypatch):
    # Simulate no DB handle: the ensure must fail soft, never raise.
    monkeypatch.setattr(storefronts, "_get_db", lambda: None)
    res = storefronts.ensure_default_storefront(dry_run=True)
    assert res["ok"] is False
    assert res["action"] == "skipped"
