"""buy_desk /rows on-hand counts are store-scoped (salvaged hardening 2026-07-03).

A store-level role can no longer read another store's on-hand counts by passing
?store_id=<foreign>. Direct-call unit tests (no TestClient) exercise the
resolve_store_scope guard added at the top of buy_desk_rows.
"""
from __future__ import annotations

import asyncio
import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from api.routers import buy_desk as bd  # noqa: E402


def _user(roles, active, stores=None):
    return {
        "user_id": "u1",
        "username": "t",
        "roles": roles,
        "active_store_id": active,
        "store_ids": stores if stores is not None else ([active] if active else []),
    }


def _rows(**kw):
    return asyncio.run(bd.buy_desk_rows(**kw))


def test_store_manager_foreign_store_403(monkeypatch):
    """A STORE_MANAGER of BV-A asking for BV-A's rival store BV-B is rejected."""
    monkeypatch.setattr(bd, "get_product_repository", lambda: None)
    with pytest.raises(HTTPException) as e:
        _rows(store_id="BV-B", limit=200, skip=0,
              current_user=_user(["STORE_MANAGER"], "BV-A"))
    assert e.value.status_code == 403


def test_store_manager_own_store_ok(monkeypatch):
    monkeypatch.setattr(bd, "get_product_repository", lambda: None)
    out = _rows(store_id="BV-A", limit=200, skip=0,
                current_user=_user(["STORE_MANAGER"], "BV-A"))
    assert out["store_id"] == "BV-A"


def test_store_manager_omit_pins_to_own(monkeypatch):
    """Omitting ?store_id pins a store-level role to their own store (not all)."""
    monkeypatch.setattr(bd, "get_product_repository", lambda: None)
    out = _rows(store_id=None, limit=200, skip=0,
                current_user=_user(["STORE_MANAGER"], "BV-A"))
    assert out["store_id"] == "BV-A"


def test_admin_any_store_ok(monkeypatch):
    """A cross-store role (ADMIN) keeps full reach across stores."""
    monkeypatch.setattr(bd, "get_product_repository", lambda: None)
    out = _rows(store_id="BV-B", limit=200, skip=0,
                current_user=_user(["ADMIN"], "BV-A", stores=[]))
    assert out["store_id"] == "BV-B"
