"""Tests for the lens_stock_hook glue layer (Branch B' sub-PR 4).

The hook bridges POS order-create / workshop-dispatch / order-cancel to the
atomic reserve/commit/release endpoints from B'1. These tests monkey-patch
the lens_stock router seam so no real Mongo is needed for the unit cases.

Async tests run under pytest-asyncio (auto mode is set in pytest.ini).
"""
from __future__ import annotations

import os
import sys
import types
from typing import Any, Dict, Optional

import pytest
from fastapi import HTTPException

pytestmark = pytest.mark.asyncio

# The hook lazily does `from ..routers import lens_stock`, which transitively
# imports modules that require JWT_SECRET_KEY at import time. Set a test value
# before importing anything under api.* so that import path doesn't crash.
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

sys.path.insert(0, "backend")

import api.routers as _routers_pkg  # noqa: E402
from api.services import lens_stock_hook as hook  # noqa: E402


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeAuditColl:
    """Minimal stand-in for the lens_stock_audit collection."""

    def __init__(self, rows: Optional[list] = None) -> None:
        self._rows = rows or []

    def find_one(self, query: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        for r in self._rows:
            if all(r.get(k) == v for k, v in query.items()):
                return r
        return None


def _install_fake_router(monkeypatch, *, reserve=None, commit=None, release=None,
                         audit_rows=None):
    """Build a fake lens_stock router module + audit collection and patch
    both seams the hook uses (`_get_audit_collection` + the lazy router
    import)."""
    fake = types.ModuleType("api.routers.lens_stock")

    class ReserveCommitReleasePayload:  # pylint: disable=too-few-public-methods
        def __init__(self, **kw: Any) -> None:
            self.__dict__.update(kw)

    fake.ReserveCommitReleasePayload = ReserveCommitReleasePayload

    async def _default_ok(*_a, **_k):
        return {"cell": {"line_stock_id": "ls-1", "on_hand": 5, "reserved": 1}}

    fake.reserve_cell = reserve or _default_ok
    fake.commit_cell = commit or _default_ok
    fake.release_cell = release or _default_ok
    fake._get_db = lambda: None  # noqa: SLF001

    # Inject on BOTH sys.modules AND the parent package attribute, because
    # `from ..routers import lens_stock` resolves via the package attribute,
    # not just sys.modules.
    monkeypatch.setitem(sys.modules, "api.routers.lens_stock", fake)
    monkeypatch.setattr(_routers_pkg, "lens_stock", fake, raising=False)
    monkeypatch.setattr(
        hook, "_get_audit_collection", lambda: _FakeAuditColl(audit_rows)
    )
    return fake


_LENS_ITEM = {
    "item_type": "LENS",
    "lens_line_id": "line-abc",
    "sph": -2.0,
    "cyl": -0.5,
    "add": None,
    "quantity": 1,
}
_USER = {"user_id": "u1", "username": "admin", "roles": ["SUPERADMIN"]}


# ---------------------------------------------------------------------------
# reserve
# ---------------------------------------------------------------------------


async def test_non_lens_item_is_noop(monkeypatch):
    _install_fake_router(monkeypatch)
    rec = await hook.reserve_for_order_item(
        order_item={"item_type": "FRAME", "product_id": "p1"},
        order_id="o1", line_index=0, store_id="BV-BOK-01", user=_USER,
    )
    assert rec is None


async def test_lens_missing_cell_coords_is_noop(monkeypatch):
    _install_fake_router(monkeypatch)
    rec = await hook.reserve_for_order_item(
        order_item={"item_type": "LENS", "quantity": 1},
        order_id="o1", line_index=0, store_id="BV-BOK-01", user=_USER,
    )
    assert rec is None


async def test_reserve_happy_path(monkeypatch):
    _install_fake_router(monkeypatch)
    rec = await hook.reserve_for_order_item(
        order_item=dict(_LENS_ITEM),
        order_id="o1", line_index=0, store_id="BV-BOK-01", user=_USER,
    )
    assert rec is not None
    assert rec["status"] == "reserved"
    assert rec["lens_line_id"] == "line-abc"
    assert rec["qty"] == 1


async def test_reserve_insufficient_stock_409_propagates(monkeypatch):
    async def _raise_409(*_a, **_k):
        raise HTTPException(status_code=409, detail="Insufficient: available 0")
    _install_fake_router(monkeypatch, reserve=_raise_409)
    with pytest.raises(HTTPException) as ei:
        await hook.reserve_for_order_item(
            order_item=dict(_LENS_ITEM),
            order_id="o1", line_index=0, store_id="BV-BOK-01", user=_USER,
        )
    assert ei.value.status_code == 409


async def test_reserve_404_cell_not_seeded_upgraded_to_409(monkeypatch):
    async def _raise_404(*_a, **_k):
        raise HTTPException(status_code=404, detail="cell not found")
    _install_fake_router(monkeypatch, reserve=_raise_404)
    with pytest.raises(HTTPException) as ei:
        await hook.reserve_for_order_item(
            order_item=dict(_LENS_ITEM),
            order_id="o1", line_index=0, store_id="BV-BOK-01", user=_USER,
        )
    assert ei.value.status_code == 409


async def test_reserve_transient_error_is_fail_soft(monkeypatch):
    async def _boom(*_a, **_k):
        raise RuntimeError("mongo blip")
    _install_fake_router(monkeypatch, reserve=_boom)
    rec = await hook.reserve_for_order_item(
        order_item=dict(_LENS_ITEM),
        order_id="o1", line_index=0, store_id="BV-BOK-01", user=_USER,
    )
    assert rec is not None
    assert rec["status"] == "failed"
    assert "mongo blip" in rec["error"]


async def test_reserve_idempotent_when_prior_audit_exists(monkeypatch):
    prior = [{"source_id": "o1#0", "action": "reserve", "line_stock_id": "ls-9"}]
    _install_fake_router(monkeypatch, audit_rows=prior)
    rec = await hook.reserve_for_order_item(
        order_item=dict(_LENS_ITEM),
        order_id="o1", line_index=0, store_id="BV-BOK-01", user=_USER,
    )
    assert rec is not None
    assert rec["status"] == "already_reserved"
    assert rec["line_stock_id"] == "ls-9"


# ---------------------------------------------------------------------------
# commit
# ---------------------------------------------------------------------------


async def test_commit_happy_path(monkeypatch):
    _install_fake_router(monkeypatch)
    rec = await hook.commit_for_workshop_dispatch(
        order_item=dict(_LENS_ITEM),
        order_id="o1", line_index=0, store_id="BV-BOK-01", user=_USER,
    )
    assert rec is not None
    assert rec["status"] == "committed"


async def test_commit_409_is_fail_soft(monkeypatch):
    async def _raise_409(*_a, **_k):
        raise HTTPException(status_code=409, detail="reserved < qty")
    _install_fake_router(monkeypatch, commit=_raise_409)
    rec = await hook.commit_for_workshop_dispatch(
        order_item=dict(_LENS_ITEM),
        order_id="o1", line_index=0, store_id="BV-BOK-01", user=_USER,
    )
    assert rec is not None
    assert rec["status"] == "failed"


# ---------------------------------------------------------------------------
# release
# ---------------------------------------------------------------------------


async def test_release_skips_when_no_prior_reserve(monkeypatch):
    _install_fake_router(monkeypatch, audit_rows=[])
    rec = await hook.release_for_cancel(
        order_item=dict(_LENS_ITEM),
        order_id="o1", line_index=0, store_id="BV-BOK-01", user=_USER,
    )
    assert rec is not None
    assert rec["status"] == "no_reservation"


async def test_release_happy_path_after_reserve(monkeypatch):
    rows = [{"source_id": "o1#0", "action": "reserve", "line_stock_id": "ls-1"}]
    _install_fake_router(monkeypatch, audit_rows=rows)
    rec = await hook.release_for_cancel(
        order_item=dict(_LENS_ITEM),
        order_id="o1", line_index=0, store_id="BV-BOK-01", user=_USER,
    )
    assert rec is not None
    assert rec["status"] == "released"


async def test_release_skips_when_already_committed(monkeypatch):
    rows = [
        {"source_id": "o1#0", "action": "reserve", "line_stock_id": "ls-1"},
        {"source_id": "o1#0#commit", "action": "commit"},
    ]
    _install_fake_router(monkeypatch, audit_rows=rows)
    rec = await hook.release_for_cancel(
        order_item=dict(_LENS_ITEM),
        order_id="o1", line_index=0, store_id="BV-BOK-01", user=_USER,
    )
    assert rec is not None
    assert rec["status"] == "already_committed"
