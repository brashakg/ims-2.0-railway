"""
IMS 2.0 - orders.customer_id index -- PR #947 follow-up 3 (verification)
=========================================================================
PR #947's tracked follow-up asked for a best-effort `orders.customer_id` index
(mirroring `ensure_shopify_order_index`) so `customers._attach_order_stats`'
`{"customer_id": {"$in": ids}}` scan (OS-026 lifetime-spend enrichment) does not
collscan as order volume grows.

Audit result: this index is ALREADY built on the live startup path --
`database/connection.py::DatabaseConnection.ensure_indexes` (invoked
unconditionally from `api/main.py`'s lifespan via `get_db().ensure_indexes()`
right after `init_db`) creates BOTH a single-field `orders.customer_id` index
and a compound `[customer_id, created_at]` index, and `database/schemas.py`'s
`INDEXES["orders"]` declares the single-field index for documentation parity.
No new index-creation code was needed (unlike `shopify_order_id`, which
`ensure_shopify_order_index` existed for but was never wired into startup --
see test_unification_index_backstops.py).

This test is a DRIFT LOCK: if the customer_id index (or its schemas.py
declaration) is ever removed, this test fails loudly instead of the regression
silently reintroducing the collscan.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.connection import DatabaseConnection  # noqa: E402


class _RecordingColl:
    def __init__(self, name):
        self.name = name
        self.calls = []  # list of (keys, kwargs)

    def create_index(self, keys, **kw):
        self.calls.append((keys, dict(kw)))
        return "idx"


class _RecordingDB:
    def __init__(self):
        self._colls = {}

    def __getitem__(self, name):
        if name not in self._colls:
            self._colls[name] = _RecordingColl(name)
        return self._colls[name]

    def get_collection(self, name):
        return self[name]


def _run_ensure_indexes(fake_db):
    """Run DatabaseConnection.ensure_indexes against a fake db, restoring the
    singleton's real state afterwards (DatabaseConnection is a singleton --
    mirrors test_unification_index_backstops._run_ensure_indexes)."""
    conn = DatabaseConnection()
    saved_db, saved_connected = conn._db, conn._connected
    try:
        conn._connected = True
        conn._db = fake_db
        conn.ensure_indexes()
    finally:
        conn._db, conn._connected = saved_db, saved_connected


def test_ensure_indexes_builds_orders_customer_id_index():
    db = _RecordingDB()
    _run_ensure_indexes(db)

    built = [keys for keys, _kw in db["orders"].calls]
    assert "customer_id" in built, (
        "orders.customer_id index not built by the live startup path -- "
        "customers._attach_order_stats' $in scan would collscan"
    )
    # The compound (customer_id, created_at) index also covers a date-bounded
    # customer-history lookup.
    assert [("customer_id", 1), ("created_at", -1)] in built


def test_schemas_declare_orders_customer_id_index():
    """Documentation parity: schemas.py's INDEXES["orders"] lists customer_id
    too (mirrors the shopify_order_id parity test in
    test_unification_index_backstops.py)."""
    from database.schemas import INDEXES

    keys_list = [spec["keys"] for spec in INDEXES["orders"]]
    assert [("customer_id", 1)] in keys_list
