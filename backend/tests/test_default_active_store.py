"""Store-consistency P2 -- default active store for all-stores admins.

An ADMIN/SUPERADMIN/AREA_MANAGER whose account has no explicit store assignment
used to get active_store_id=None -> the topbar showed a "No store" pill and POS
dead-ended. _default_active_store picks a sensible store (HQ first, else any
active, else any) so the pill always names a real store. Fail-soft: non-admin
roles and a missing DB both return None (the prior behaviour).
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import auth  # noqa: E402


class _FakeColl:
    def __init__(self, stores):
        self.stores = stores

    def find_one(self, query, projection=None):
        for s in self.stores:
            if all(s.get(k) == v for k, v in query.items()):
                return {"store_id": s["store_id"]}
        return None


class _FakeDB:
    def __init__(self, stores):
        self.stores = stores

    def get_collection(self, _name):
        return _FakeColl(self.stores)


class _Wrap:
    def __init__(self, db):
        self.db = db


def _patch_db(monkeypatch, stores):
    import database.connection as conn

    monkeypatch.setattr(conn, "get_db", lambda: _Wrap(_FakeDB(stores)))


def test_non_admin_role_returns_none():
    # Store-level role never gets an auto-default (it has its own assignment).
    assert auth._default_active_store({"roles": ["SALES_STAFF"]}) is None
    assert auth._default_active_store({"roles": []}) is None


def test_admin_prefers_hq(monkeypatch):
    _patch_db(
        monkeypatch,
        [
            {"store_id": "S1", "is_active": True, "store_type": "RETAIL"},
            {"store_id": "HQ1", "is_active": True, "store_type": "HQ"},
        ],
    )
    assert auth._default_active_store({"roles": ["ADMIN"]}) == "HQ1"


def test_admin_falls_back_to_first_active(monkeypatch):
    _patch_db(
        monkeypatch,
        [
            {"store_id": "S1", "is_active": False, "store_type": "RETAIL"},
            {"store_id": "S2", "is_active": True, "store_type": "RETAIL"},
        ],
    )
    assert auth._default_active_store({"roles": ["SUPERADMIN"]}) == "S2"


def test_admin_no_stores_returns_none(monkeypatch):
    _patch_db(monkeypatch, [])
    assert auth._default_active_store({"roles": ["ADMIN"]}) is None
