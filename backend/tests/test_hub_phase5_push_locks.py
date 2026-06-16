"""
Hub Phase 5 -- Shopify push-locks (owner DECISION C). A brand / collection in the
ecom.shopify_push_locks E2 config can NEVER be pushed to Shopify: the lock is the
FIRST statement inside every push fn, BEFORE the dark/live gate (fail-closed). A
read error fails SOFT (does not block every push). Locking does NOT auto-takedown
live items (that's the manual-unpublish flag list, a fast-follow).

Run: JWT_SECRET_KEY=test python -m pytest backend/tests/test_hub_phase5_push_locks.py -q
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

from api.services import shopify_push as sp  # noqa: E402
from api.services import policy_engine  # noqa: E402


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _locks(monkeypatch, cfg):
    monkeypatch.setattr(
        policy_engine,
        "get_policy",
        lambda key, default=None: cfg if key == "ecom.shopify_push_locks" else default,
    )


# ---------------------------------------------------------------------------
# push_lock_reason (pure)
# ---------------------------------------------------------------------------


def test_lock_reason_brand_case_insensitive(monkeypatch):
    _locks(monkeypatch, {"brands": ["Cartier", "Bvlgari"]})
    assert sp.push_lock_reason(None, "product", {"brand": "cartier"})
    assert sp.push_lock_reason(None, "product", {"vendor": "BVLGARI"})
    assert sp.push_lock_reason(None, "product", {"brand": "Ray-Ban"}) is None


def test_lock_reason_brand_from_attributes(monkeypatch):
    _locks(monkeypatch, {"brands": ["cartier"]})
    assert sp.push_lock_reason(
        None, "product", {"attributes": {"brand_name": "Cartier"}}
    )


def test_lock_reason_collection(monkeypatch):
    _locks(monkeypatch, {"collections": ["clearance"]})
    assert sp.push_lock_reason(None, "collection", {"handle": "Clearance"})
    assert sp.push_lock_reason(None, "collection", {"title": "clearance"})
    assert sp.push_lock_reason(None, "collection", {"handle": "new-arrivals"}) is None


def test_lock_reason_empty_config_locks_nothing(monkeypatch):
    _locks(monkeypatch, {})
    assert sp.push_lock_reason(None, "product", {"brand": "Cartier"}) is None


def test_lock_reason_failsoft_on_config_error(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("policy backend down")

    monkeypatch.setattr(policy_engine, "get_policy", _boom)
    # fail-SOFT: a read blip must NOT block every push (the dark/live gate applies)
    assert sp.push_lock_reason(None, "product", {"brand": "Cartier"}) is None


# ---------------------------------------------------------------------------
# push_product / push_collection honour the lock FIRST (before dark/live gate)
# ---------------------------------------------------------------------------


def test_push_product_blocked_when_brand_locked(monkeypatch):
    _locks(monkeypatch, {"brands": ["cartier"]})
    res = _run(sp.push_product(object(), {"product_id": "P1", "brand": "Cartier"}))
    assert res.mode == sp.MODE_BLOCKED
    assert res.ok is False
    assert "push-locked" in (res.error or "")
    assert res.target_id == "P1"


def test_push_product_unlocked_proceeds(monkeypatch):
    # unlocked brand -> NOT blocked; falls through to the dark gate (SIMULATED,
    # since IMS_SHOPIFY_WRITES is off in tests) -- the point is it is not BLOCKED.
    _locks(monkeypatch, {"brands": ["cartier"]})
    res = _run(
        sp.push_product(
            object(),
            {"product_id": "P2", "brand": "Ray-Ban", "name": "RB Frame", "sku": "RB1"},
        )
    )
    assert res.mode != sp.MODE_BLOCKED


def test_push_collection_blocked_when_handle_locked(monkeypatch):
    _locks(monkeypatch, {"collections": ["clearance"]})
    res = _run(
        sp.push_collection(object(), {"collection_id": "C1", "handle": "clearance"})
    )
    assert res.mode == sp.MODE_BLOCKED
    assert res.ok is False
    assert res.target_id == "C1"


def test_push_collection_unlocked_proceeds(monkeypatch):
    _locks(monkeypatch, {"collections": ["clearance"]})
    res = _run(
        sp.push_collection(
            object(), {"collection_id": "C2", "handle": "new-arrivals", "title": "New"}
        )
    )
    assert res.mode != sp.MODE_BLOCKED


# ---------------------------------------------------------------------------
# push_image honours the parent product's brand-lock (defense-in-depth: an image
# of a locked brand must NEVER reach Shopify, even via the media path)
# ---------------------------------------------------------------------------


class _FakeColl:
    def __init__(self, doc):
        self._doc = doc

    def find_one(self, _flt):
        return self._doc


class _FakeDB:
    def __init__(self, product_doc):
        self._p = product_doc

    def __getitem__(self, name):
        return _FakeColl(self._p if name == "catalog_products" else None)


def test_push_image_blocked_when_parent_brand_locked(monkeypatch):
    _locks(monkeypatch, {"brands": ["cartier"]})
    db = _FakeDB({"id": "P1", "brand": "Cartier", "ecom": {"shopify_product_id": "g"}})
    res = _run(
        sp.push_image(
            db, {"image_id": "IMG1", "product_id": "P1", "status": "APPROVED"}
        )
    )
    assert res.mode == sp.MODE_BLOCKED
    assert res.ok is False
    assert res.target_id == "IMG1"


def test_push_image_unlocked_parent_proceeds(monkeypatch):
    _locks(monkeypatch, {"brands": ["cartier"]})
    db = _FakeDB({"id": "P2", "brand": "Ray-Ban", "ecom": {}})
    res = _run(
        sp.push_image(
            db, {"image_id": "IMG2", "product_id": "P2", "status": "APPROVED"}
        )
    )
    assert res.mode != sp.MODE_BLOCKED
