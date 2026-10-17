"""Store-consistency P2 -- default active store for all-stores admins.

An ADMIN/SUPERADMIN whose account has no explicit store assignment
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


def _matches(doc, query):
    return all(
        doc.get(k) != v["$ne"] if isinstance(v, dict) else doc.get(k) == v
        for k, v in query.items()
    )


class _FakeColl:
    def __init__(self, stores):
        self.stores = stores

    def find_one(self, query, projection=None):
        for s in self.stores:
            if _matches(s, query):
                return {"store_id": s["store_id"]}
        return None

    def find(self, query, projection=None):
        # stores_util.physical_stores (the one physical-shop reader) lists here.
        return [dict(s) for s in self.stores if _matches(s, query)]


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


# Owner ruling 2026-10-07 (R3), panel 2026-10-08: an AREA_MANAGER is not an
# admin. With no shop assigned he must get NO shop -- a defaulted one made him
# read a shop he was never given (prod-like: the online store plus two shops,
# none typed HQ), and resolve_store_scope never saw him as shop-less.
_PROD_LIKE = [
    {"store_id": "BV-ONLINE-01", "store_code": "BV-ONLINE-01", "is_active": True, "store_type": "ONLINE"},
    {"store_id": "BV-DHN-02", "store_code": "BV-DHN-02", "is_active": True, "store_type": "RETAIL"},
    {"store_id": "BV-BOK-02", "store_code": "BV-BOK-02", "is_active": True, "store_type": "RETAIL"},
]


def test_r3_a_shopless_area_manager_gets_no_default_shop(monkeypatch):
    _patch_db(monkeypatch, _PROD_LIKE)
    assert auth._default_active_store({"roles": ["AREA_MANAGER"], "store_ids": []}) is None
    # Admins still land on a real shop (they read every shop anyway).
    assert auth._default_active_store({"roles": ["ADMIN"]}) in ("BV-DHN-02", "BV-BOK-02")


def test_r3_refresh_drops_a_shop_the_login_was_never_given():
    """A token minted before the fix (area manager defaulted to Bokaro) must
    not carry that shop past its next refresh: no shop assigned = no shop."""
    claims = auth._resolve_refresh_claims(
        {"user_id": "am1", "roles": ["AREA_MANAGER"], "store_ids": [], "active_store_id": "BV-BOK-02"},
        {"user_id": "am1", "roles": ["AREA_MANAGER"], "store_ids": [], "is_active": True},
    )
    assert claims["active_store_id"] is None
    # His own shops are kept; an admin keeps any shop he switched to.
    own = auth._resolve_refresh_claims(
        {"roles": ["AREA_MANAGER"], "store_ids": ["BV-DHN-02"], "active_store_id": "BV-DHN-02"}, None
    )
    assert own["active_store_id"] == "BV-DHN-02"
    adm = auth._resolve_refresh_claims(
        {"roles": ["ADMIN"], "store_ids": [], "active_store_id": "BV-BOK-02"}, None
    )
    assert adm["active_store_id"] == "BV-BOK-02"
