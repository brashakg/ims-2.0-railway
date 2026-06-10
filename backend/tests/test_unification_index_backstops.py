"""Unification step 1 -- index + dedupe backstops (no behavior change).

Grounded by docs/reference/UNIFICATION_AUDIT_2026-06-10:
  1. `shopify_ingest.ensure_shopify_order_index` was DEAD CODE (never called),
     so a Shopify webhook retry could double-book an online order. It is now
     wired into main.py's lifespan beside the other startup index ensures.
  2. `catalog_products` had ZERO DB indexes -> duplicate PIM docs were
     physically possible. connection.py ensure_indexes now builds UNIQUE
     sparse indexes on `id` and `sku` (warn-only on pre-existing prod dupes,
     mirroring the grns dc_number precedent).
  3. `lens_catalog`'s unique `lens_line_id` + 6-field identity indexes were
     declared in schemas.py (documentation-only) but never built at startup.
     ensure_indexes now builds them (plus the two non-unique filters).
  4. The ecom_collections `handle` create race (check-then-insert) surfaced as
     a 500; it now maps to the same 409 as the pre-check.
  5. schemas.py declares catalog_products + orders.shopify_order_id for parity.

CI-robust: every DB accessor is monkeypatched / faked -- no live Mongo needed,
no whole-JSON substring assertions.
"""
import asyncio
import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from database.connection import DatabaseConnection  # noqa: E402


# ===========================================================================
# Fakes (capture create_index calls; optionally fail per-collection)
# ===========================================================================


class _RecordingColl:
    def __init__(self, name, fail=False):
        self.name = name
        self.fail = fail
        self.calls = []  # list of (keys, kwargs)

    def create_index(self, keys, **kw):
        self.calls.append((keys, dict(kw)))
        if self.fail:
            raise Exception("E11000 duplicate key error (simulated)")
        return "idx"


class _RecordingDB:
    def __init__(self, fail_colls=()):
        self._colls = {}
        self._fail = set(fail_colls)

    def __getitem__(self, name):
        if name not in self._colls:
            self._colls[name] = _RecordingColl(name, fail=(name in self._fail))
        return self._colls[name]

    def get_collection(self, name):
        return self[name]


def _run_ensure_indexes(fake_db):
    """Run DatabaseConnection.ensure_indexes against a fake db, restoring the
    singleton's real state afterwards (DatabaseConnection is a singleton)."""
    conn = DatabaseConnection()
    saved_db, saved_connected = conn._db, conn._connected
    try:
        conn._connected = True
        conn._db = fake_db
        conn.ensure_indexes()
    finally:
        conn._db, conn._connected = saved_db, saved_connected


def _find_call(coll, keys):
    """Return the kwargs of the create_index call whose keys match, else None."""
    for got_keys, kw in coll.calls:
        if got_keys == keys:
            return kw
    return None


# ===========================================================================
# 1. ensure_indexes builds the new catalog_products / lens_catalog indexes
# ===========================================================================


def test_ensure_indexes_builds_catalog_products_unique_sparse():
    db = _RecordingDB()
    _run_ensure_indexes(db)

    coll = db["catalog_products"]
    for field in ("id", "sku"):
        kw = _find_call(coll, field)
        assert kw is not None, f"catalog_products.{field} index not built"
        assert kw.get("unique") is True
        assert kw.get("sparse") is True


def test_ensure_indexes_builds_lens_catalog_identity_indexes():
    db = _RecordingDB()
    _run_ensure_indexes(db)

    coll = db["lens_catalog"]
    # UNIQUE slug.
    kw = _find_call(coll, "lens_line_id")
    assert kw is not None, "lens_catalog.lens_line_id index not built"
    assert kw.get("unique") is True

    # UNIQUE 6-field identity grid (matches schemas.py declaration exactly).
    identity_keys = [
        ("brand", 1),
        ("series", 1),
        ("index", 1),
        ("material", 1),
        ("lens_type", 1),
        ("coating", 1),
    ]
    kw = _find_call(coll, identity_keys)
    assert kw is not None, "lens_catalog 6-field identity index not built"
    assert kw.get("unique") is True

    # The two non-unique filters declared in schemas.py.
    assert _find_call(coll, [("brand", 1), ("is_active", 1)]) is not None
    assert _find_call(coll, [("is_active", 1)]) is not None


def test_new_index_builds_are_failsoft():
    """A pre-existing prod duplicate makes the UNIQUE build raise E11000; the
    _idx wrapper must only WARN -- every other collection still gets indexed
    and ensure_indexes never raises (the dc_number precedent)."""
    db = _RecordingDB(fail_colls={"catalog_products", "lens_catalog"})
    _run_ensure_indexes(db)  # must not raise

    # The failing collections were still ATTEMPTED (each isolated).
    assert len(db["catalog_products"].calls) >= 2
    assert len(db["lens_catalog"].calls) >= 4
    # Collections after them in the build order are unaffected.
    assert len(db["ecom_collections"].calls) > 0
    assert len(db["vendor_bills"].calls) > 0
    assert len(db["health_checks"].calls) > 0


# ===========================================================================
# 2. ensure_shopify_order_index -- unit contract + startup wiring
# ===========================================================================


def test_ensure_shopify_order_index_creates_unique_partial_index():
    from api.services.shopify_ingest import ensure_shopify_order_index

    db = _RecordingDB()
    ensure_shopify_order_index(db)

    kw = _find_call(db["orders"], "shopify_order_id")
    assert kw is not None, "orders.shopify_order_id index not built"
    assert kw.get("unique") is True
    assert kw.get("partialFilterExpression") == {
        "shopify_order_id": {"$type": "string"}
    }
    assert kw.get("name") == "uniq_shopify_order_id"


def test_ensure_shopify_order_index_is_failsoft():
    from api.services.shopify_ingest import ensure_shopify_order_index

    # No DB -> silent no-op.
    ensure_shopify_order_index(None)

    # A failing build (e.g. pre-existing duplicate shopify_order_id rows) must
    # never raise to the caller.
    db = _RecordingDB(fail_colls={"orders"})
    ensure_shopify_order_index(db)  # must not raise
    assert len(db["orders"].calls) == 1


def test_startup_lifespan_invokes_ensure_shopify_order_index(monkeypatch):
    """The audit finding: the helper existed but was never called. Run the app
    lifespan with every DB accessor monkeypatched and assert the wiring fires
    with the live db handle."""
    import api.main as main_mod
    from api.services import shopify_ingest

    sentinel_db = object()
    calls = []

    class _StubConn:
        db = sentinel_db

        def ensure_indexes(self):
            calls.append("ensure_indexes")

    stub = _StubConn()
    monkeypatch.setattr(main_mod, "init_db", lambda config=None: True)
    monkeypatch.setattr(main_mod, "get_db", lambda: stub)
    monkeypatch.setattr(
        shopify_ingest,
        "ensure_shopify_order_index",
        lambda db: calls.append(("shopify_order_index", db)),
    )

    from fastapi.testclient import TestClient

    with TestClient(main_mod.app):
        pass

    assert ("shopify_order_index", sentinel_db) in calls, (
        "main.py lifespan no longer invokes shopify_ingest."
        "ensure_shopify_order_index -- the webhook double-book backstop is dead "
        "code again"
    )
    # Sanity: the generic ensure_indexes path also ran on the same stub.
    assert "ensure_indexes" in calls


# ===========================================================================
# 3. ecom_collections handle create race -> 409 (not 500)
# ===========================================================================

from api.routers import online_store_collections as osc  # noqa: E402


def _create(payload_kwargs, repo, monkeypatch):
    monkeypatch.setattr(osc, "_repo", lambda: repo)
    payload = osc.CollectionCreate(**payload_kwargs)
    return asyncio.run(
        osc.create_collection(payload, current_user={"user_id": "tester"})
    )


def test_create_collection_lost_race_maps_to_409(monkeypatch):
    """Pre-check passes (handle absent), then the insert loses the race: the
    repo swallows the DuplicateKeyError fail-soft and returns None while the
    winner's row now exists. Must be 409, not 500."""
    state = {"lookups": 0}

    class _Repo:
        def get_by_handle(self, handle):
            state["lookups"] += 1
            if state["lookups"] == 1:
                return None  # pre-check: not there yet
            return {"collection_id": "winner", "handle": handle}

        def create(self, data):
            return None  # repository swallowed the E11000 (fail-soft contract)

    with pytest.raises(HTTPException) as ei:
        _create({"title": "Dup", "handle": "dup-handle"}, _Repo(), monkeypatch)
    assert ei.value.status_code == 409
    assert "dup-handle" in str(ei.value.detail)


def test_create_collection_duplicate_key_error_maps_to_409(monkeypatch):
    """If the repository ever propagates the raw DuplicateKeyError instead of
    swallowing it, the route must still answer 409."""
    try:
        from pymongo.errors import DuplicateKeyError
    except ImportError:  # pragma: no cover - pymongo always present in CI
        class DuplicateKeyError(Exception):
            pass

    class _Repo:
        def get_by_handle(self, handle):
            return None

        def create(self, data):
            raise DuplicateKeyError("E11000 duplicate key error: handle dup")

    with pytest.raises(HTTPException) as ei:
        _create({"title": "Dup", "handle": "dup-2"}, _Repo(), monkeypatch)
    assert ei.value.status_code == 409


def test_create_collection_non_race_failure_stays_500(monkeypatch):
    """A create failure with NO competing row is a genuine server error -- the
    409 mapping must not mask it."""

    class _Repo:
        def get_by_handle(self, handle):
            return None  # never exists: not a race

        def create(self, data):
            return None  # some other write failure

    with pytest.raises(HTTPException) as ei:
        _create({"title": "Broken", "handle": "h-broken"}, _Repo(), monkeypatch)
    assert ei.value.status_code == 500


def test_create_collection_success_path_unchanged(monkeypatch):
    """Behavior preservation: the happy path still returns the created doc."""

    class _Repo:
        def get_by_handle(self, handle):
            return None

        def create(self, data):
            doc = dict(data)
            doc["collection_id"] = "c-1"
            return doc

    res = _create({"title": "OK", "handle": "h-ok"}, _Repo(), monkeypatch)
    assert res["collection"]["handle"] == "h-ok"
    assert res["collection"]["id"] == "c-1"


# ===========================================================================
# 4. schemas.py parity declarations (documentation drift reconciled)
# ===========================================================================


def test_schemas_declare_catalog_products_indexes():
    from database.schemas import COLLECTIONS

    entry = COLLECTIONS.get("catalog_products")
    assert entry is not None, "catalog_products missing from COLLECTIONS"
    indexes = entry["indexes"]
    assert {"keys": [("id", 1)], "unique": True, "sparse": True} in indexes
    assert {"keys": [("sku", 1)], "unique": True, "sparse": True} in indexes
    # Permissive doc-only schema (the collection is deliberately schemaless).
    assert entry["schema"] == {"bsonType": "object"}


def test_schemas_declare_orders_shopify_order_id_parity():
    from database.schemas import INDEXES

    spec = next(
        (
            s
            for s in INDEXES["orders"]
            if s.get("name") == "uniq_shopify_order_id"
        ),
        None,
    )
    assert spec is not None, "orders.shopify_order_id parity declaration missing"
    assert spec["keys"] == [("shopify_order_id", 1)]
    assert spec["unique"] is True
    assert spec["partialFilterExpression"] == {
        "shopify_order_id": {"$type": "string"}
    }


def test_ensure_indexes_covers_every_declared_lens_catalog_index():
    """Drift lock: every index declared in schemas.py for lens_catalog is now
    actually built by the live startup path (connection.ensure_indexes)."""
    from database.schemas import COLLECTIONS

    db = _RecordingDB()
    _run_ensure_indexes(db)
    built = [keys for keys, _kw in db["lens_catalog"].calls]

    for spec in COLLECTIONS["lens_catalog"]["indexes"]:
        keys = spec["keys"]
        single = keys[0][0] if len(keys) == 1 and keys[0][1] == 1 else None
        assert keys in built or (single is not None and single in built), (
            f"lens_catalog index {keys} declared in schemas.py but not built "
            "by ensure_indexes"
        )
