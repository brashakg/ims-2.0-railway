"""
IMS 2.0 - Display fixtures + placements CRUD tests (v2-2a)
===========================================================
Async-call the router endpoints directly (no TestClient HTTP layer) against
an in-memory FakeMongo, monkeypatching the routers' _get_db so we never
need a real MongoDB process. The matchers are smaller and faster than the
TestClient path; they also let us assert on internal call shape (which
fields end up on the doc, what audit row was emitted).

Covered:
  - create / list / get / patch / delete happy path
  - UNIQUE constraint on (store_id, code) on fixture create
  - Soft-delete of a fixture WITH active placements -> 409
  - move endpoint atomically updates fixture_id
  - role gates (CASHIER write -> 403)
  - store scoping (Bokaro STORE_MANAGER cannot read Pune fixtures)
  - meta/options returns the enum lists
"""
from __future__ import annotations

import asyncio
import copy
import os
import sys
from typing import Any, Dict, List, Optional

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Set required env vars BEFORE importing anything that imports auth.py (which
# raises if JWT_SECRET_KEY is unset).
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")

from api.routers import display_fixtures as fixtures_router  # noqa: E402
from api.routers import display_placements as placements_router  # noqa: E402


# ---------------------------------------------------------------------------
# Minimal Mongo-collection stand-in. Just enough surface for the CRUD paths
# touched by the two routers: find_one, find (-> cursor with sort/limit),
# insert_one (with E11000 emulation for unique indexes), update_one,
# delete_one, count_documents.
# ---------------------------------------------------------------------------


class DupKeyError(Exception):
    """Stand-in for pymongo.errors.DuplicateKeyError -- str(e) contains the
    'duplicate key' / 'E11000' marker the router branches on."""

    def __str__(self) -> str:  # noqa: D401
        return "E11000 duplicate key error"


class _Cursor:
    """Iterable wrapper that supports the .find(...) -> sort/limit/list chain
    a few of the routers' read paths use. We don't replicate Mongo's order;
    callers sort in Python after .find()."""

    def __init__(self, docs: List[Dict[str, Any]]):
        self._docs = docs

    def __iter__(self):
        return iter(self._docs)


class FakeColl:
    """In-memory document collection."""

    def __init__(self, unique_keys: Optional[List[tuple]] = None):
        # Each unique_keys entry is a tuple of field names that together must
        # be unique. Mirrors the (store_id, code) index on display_fixtures.
        self._docs: List[Dict[str, Any]] = []
        self._unique = unique_keys or []

    # --- writes ---
    def insert_one(self, doc: Dict[str, Any]):
        self._check_unique(doc)
        self._docs.append(copy.deepcopy(doc))
        return type("R", (), {"inserted_id": doc.get("_id", doc.get("placement_id") or doc.get("fixture_id"))})()

    def update_one(self, flt: Dict[str, Any], upd: Dict[str, Any]):
        for d in self._docs:
            if self._match(d, flt):
                if "$set" in upd:
                    candidate = copy.deepcopy(d)
                    candidate.update(upd["$set"])
                    # Re-check unique constraints in case of code rename.
                    for ukey in self._unique:
                        for other in self._docs:
                            if other is d:
                                continue
                            if all(other.get(k) == candidate.get(k) for k in ukey):
                                raise DupKeyError()
                    d.update(upd["$set"])
                return type("R", (), {"matched_count": 1, "modified_count": 1})()
        return type("R", (), {"matched_count": 0, "modified_count": 0})()

    def delete_one(self, flt: Dict[str, Any]):
        for i, d in enumerate(self._docs):
            if self._match(d, flt):
                self._docs.pop(i)
                return type("R", (), {"deleted_count": 1})()
        return type("R", (), {"deleted_count": 0})()

    # --- reads ---
    def find_one(self, flt: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        for d in self._docs:
            if self._match(d, flt):
                return copy.deepcopy(d)
        return None

    def find(self, flt: Optional[Dict[str, Any]] = None) -> _Cursor:
        flt = flt or {}
        return _Cursor([copy.deepcopy(d) for d in self._docs if self._match(d, flt)])

    def count_documents(self, flt: Dict[str, Any]) -> int:
        return sum(1 for d in self._docs if self._match(d, flt))

    # --- internals ---
    def _check_unique(self, doc: Dict[str, Any]) -> None:
        for ukey in self._unique:
            for d in self._docs:
                if all(d.get(k) == doc.get(k) for k in ukey):
                    raise DupKeyError()

    def _match(self, doc: Dict[str, Any], flt: Dict[str, Any]) -> bool:
        for k, v in flt.items():
            if isinstance(v, dict):
                # tiny operator subset: $ne
                if "$ne" in v:
                    if doc.get(k) == v["$ne"]:
                        return False
                else:
                    # not used by these routers -- treat as exact-match
                    if doc.get(k) != v:
                        return False
            else:
                if doc.get(k) != v:
                    return False
        return True


# ---------------------------------------------------------------------------
# Fixture wiring: each test patches both routers' DB helpers to point at a
# shared FakeMongo. The patched `_get_db` returns a tiny shim with
# get_collection(name) so the routers' calls just route into the fake.
# ---------------------------------------------------------------------------


class _DBShim:
    def __init__(self, collections: Dict[str, FakeColl]):
        self._collections = collections

    def get_collection(self, name: str) -> FakeColl:
        if name not in self._collections:
            self._collections[name] = FakeColl()
        return self._collections[name]


@pytest.fixture
def fake_db(monkeypatch):
    """Returns the dict of FakeColls -- tests can introspect contents after
    a call returns. Patches both routers' _get_db + the audit repo into a
    no-op (returns None) so tests don't depend on AuditRepository internals."""
    collections = {
        "display_fixtures": FakeColl(unique_keys=[("store_id", "code")]),
        "display_placements": FakeColl(
            unique_keys=[("store_id", "sku", "fixture_id")]
        ),
    }
    shim = _DBShim(collections)
    monkeypatch.setattr(fixtures_router, "_get_db", lambda: shim)
    monkeypatch.setattr(placements_router, "_get_db", lambda: shim)
    # Audit repo -> None (fail-soft path in both routers).
    monkeypatch.setattr(fixtures_router, "get_audit_repository", lambda: None)
    monkeypatch.setattr(placements_router, "get_audit_repository", lambda: None)
    return collections


def _user(roles, store_id="STR-001", user_id="u1"):
    return {
        "user_id": user_id,
        "username": "tester",
        "roles": list(roles),
        "store_ids": [store_id],
        "active_store_id": store_id,
    }


def _payload_fixture(**overrides):
    base = {
        "store_id": "STR-001",
        "code": "W-01",
        "name": "Wall Designer",
        "type": "wall",
        "floor": "ground",
        "zone": "A",
        "capacity": 80,
        "merch": ["Frame"],
    }
    base.update(overrides)
    return fixtures_router.FixtureCreate(**base)


def _payload_placement(**overrides):
    base = {
        "sku": "BV-RB-AV-5823",
        "store_id": "STR-001",
        "fixture_id": "w-01",
        "qty": 1,
        "position": "shelf-2 . slot-04",
    }
    base.update(overrides)
    return placements_router.PlacementCreate(**base)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_meta_options_returns_enum_lists(fake_db):
    """The dropdown endpoint exposes the four enums the FE filter strip needs."""
    user = _user(["SALES_STAFF"])  # any authenticated user can read meta
    out = asyncio.run(fixtures_router.fixture_meta_options(user))
    assert "wall" in out["types"] and len(out["types"]) == 8
    assert out["floors"] == ["ground", "storage", "clinic"]
    assert out["zones"] == ["A", "B", "C", "-"]
    assert "Frame" in out["catalog_types"]


def test_fixture_create_list_get_update_delete_happy_path(fake_db):
    admin = _user(["SUPERADMIN"])

    # CREATE
    out = asyncio.run(fixtures_router.create_fixture(_payload_fixture(), admin))
    assert out["status"] == "success"
    fixture_id = out["fixture"]["fixture_id"]
    assert fixture_id == "w-01"  # slug derived from W-01

    # LIST
    listed = asyncio.run(
        fixtures_router.list_fixtures(
            store_id="STR-001",
            type=None,
            floor=None,
            zone=None,
            active=True,
            current_user=admin,
        )
    )
    assert listed["total"] == 1
    assert listed["fixtures"][0]["code"] == "W-01"

    # GET
    got = asyncio.run(fixtures_router.get_fixture(fixture_id, admin))
    assert got["fixture"]["fixture_id"] == fixture_id

    # PATCH
    upd = fixtures_router.FixtureUpdate(capacity=120, notes="reorg")
    patched = asyncio.run(fixtures_router.update_fixture(fixture_id, upd, admin))
    assert patched["fixture"]["capacity"] == 120
    assert patched["fixture"]["notes"] == "reorg"
    # Untouched fields preserved.
    assert patched["fixture"]["zone"] == "A"

    # DELETE
    deleted = asyncio.run(fixtures_router.delete_fixture(fixture_id, admin))
    assert deleted["status"] == "success"
    # Soft-delete sets is_active = False; doc still exists.
    raw = fake_db["display_fixtures"].find_one({"fixture_id": fixture_id})
    assert raw is not None
    assert raw["is_active"] is False


def test_fixture_unique_code_per_store_409(fake_db):
    """Two fixtures with code 'W-01' in the same store conflict."""
    admin = _user(["SUPERADMIN"])
    asyncio.run(fixtures_router.create_fixture(_payload_fixture(code="W-01"), admin))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            fixtures_router.create_fixture(_payload_fixture(code="W-01"), admin)
        )
    assert exc.value.status_code == 409
    assert "already exists" in exc.value.detail.lower()


def test_fixture_same_code_in_different_stores_ok(fake_db):
    """Bokaro W-01 and Pune W-01 coexist."""
    admin = _user(["SUPERADMIN"])
    asyncio.run(
        fixtures_router.create_fixture(
            _payload_fixture(store_id="STR-001", code="W-01"), admin
        )
    )
    asyncio.run(
        fixtures_router.create_fixture(
            _payload_fixture(store_id="STR-002", code="W-01"), admin
        )
    )
    assert len(fake_db["display_fixtures"]._docs) == 2


def test_fixture_delete_with_active_placements_409(fake_db):
    """A fixture with any placement row referencing it cannot be deleted."""
    admin = _user(["SUPERADMIN"])
    asyncio.run(fixtures_router.create_fixture(_payload_fixture(), admin))
    asyncio.run(
        placements_router.create_placement(_payload_placement(), admin)
    )
    with pytest.raises(HTTPException) as exc:
        asyncio.run(fixtures_router.delete_fixture("w-01", admin))
    assert exc.value.status_code == 409
    assert "placement" in exc.value.detail.lower()


def test_role_gate_cashier_cannot_create_fixture(fake_db):
    """CASHIER is not in _WRITE_ROLES -- the route's require_roles dep would
    403. We can test the dependency directly."""
    cashier = _user(["CASHIER"])
    dep = fixtures_router.require_roles(*fixtures_router._WRITE_ROLES)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(dep(cashier))
    assert exc.value.status_code == 403


def test_store_scoping_blocks_cross_store_read(fake_db):
    """A STORE_MANAGER for STR-001 cannot read a STR-002 fixture."""
    admin = _user(["SUPERADMIN"])
    # Admin seeds a Pune fixture (STR-002).
    asyncio.run(
        fixtures_router.create_fixture(
            _payload_fixture(store_id="STR-002", code="P-01"), admin
        )
    )
    bokaro_mgr = _user(["STORE_MANAGER"], store_id="STR-001")
    # GET by id 403s -- the fixture is in STR-002 but the manager is in STR-001.
    with pytest.raises(HTTPException) as exc:
        asyncio.run(fixtures_router.get_fixture("p-01", bokaro_mgr))
    assert exc.value.status_code == 403


def test_placement_create_stacks_qty_on_duplicate(fake_db):
    """A second create at the same (sku, fixture) stacks qty into the
    existing row instead of inserting a duplicate."""
    admin = _user(["SUPERADMIN"])
    asyncio.run(fixtures_router.create_fixture(_payload_fixture(), admin))
    first = asyncio.run(
        placements_router.create_placement(_payload_placement(qty=1), admin)
    )
    assert first["stacked"] is False
    second = asyncio.run(
        placements_router.create_placement(_payload_placement(qty=2), admin)
    )
    assert second["stacked"] is True
    assert second["placement"]["qty"] == 3
    # Only one placement row exists.
    assert len(fake_db["display_placements"]._docs) == 1


def test_placement_move_swaps_fixture_atomically(fake_db):
    """POST /move flips fixture_id + updates last_moved_at, preserving qty."""
    admin = _user(["SUPERADMIN"])
    asyncio.run(fixtures_router.create_fixture(_payload_fixture(code="W-01"), admin))
    asyncio.run(fixtures_router.create_fixture(_payload_fixture(code="P-01", type="pillar"), admin))
    created = asyncio.run(
        placements_router.create_placement(
            _payload_placement(fixture_id="w-01", qty=3), admin
        )
    )
    placement_id = created["placement"]["placement_id"]
    moved = asyncio.run(
        placements_router.move_placement(
            placements_router.PlacementMove(
                placement_id=placement_id, target_fixture_id="p-01"
            ),
            admin,
        )
    )
    assert moved["status"] == "success"
    assert moved["placement"]["fixture_id"] == "p-01"
    assert moved["placement"]["qty"] == 3  # qty preserved


def test_placement_move_to_unknown_fixture_400(fake_db):
    admin = _user(["SUPERADMIN"])
    asyncio.run(fixtures_router.create_fixture(_payload_fixture(), admin))
    created = asyncio.run(
        placements_router.create_placement(_payload_placement(), admin)
    )
    placement_id = created["placement"]["placement_id"]
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            placements_router.move_placement(
                placements_router.PlacementMove(
                    placement_id=placement_id, target_fixture_id="ghost-fixture"
                ),
                admin,
            )
        )
    assert exc.value.status_code == 400
    assert "does not exist" in exc.value.detail.lower()


def test_placement_cannot_be_created_on_soft_deleted_fixture(fake_db):
    """Cannot create a placement against a fixture whose is_active = False."""
    admin = _user(["SUPERADMIN"])
    asyncio.run(fixtures_router.create_fixture(_payload_fixture(), admin))
    # Manually flip is_active to False (no placements yet, so the router
    # would happily soft-delete it).
    asyncio.run(fixtures_router.delete_fixture("w-01", admin))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            placements_router.create_placement(_payload_placement(), admin)
        )
    assert exc.value.status_code == 400
    assert "soft-deleted" in exc.value.detail.lower()


def test_placement_list_filters_by_sku(fake_db):
    admin = _user(["SUPERADMIN"])
    asyncio.run(fixtures_router.create_fixture(_payload_fixture(code="W-01"), admin))
    asyncio.run(fixtures_router.create_fixture(_payload_fixture(code="P-01", type="pillar"), admin))
    asyncio.run(
        placements_router.create_placement(
            _payload_placement(sku="SKU-A", fixture_id="w-01"), admin
        )
    )
    asyncio.run(
        placements_router.create_placement(
            _payload_placement(sku="SKU-B", fixture_id="p-01"), admin
        )
    )
    listed = asyncio.run(
        placements_router.list_placements(
            store_id="STR-001", sku="SKU-A", fixture_id=None, current_user=admin
        )
    )
    assert listed["total"] == 1
    assert listed["placements"][0]["sku"] == "SKU-A"
