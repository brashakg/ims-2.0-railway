"""Regression: ensure_indexes() builds each index independently, so one failing
index (e.g. a UNIQUE build blocked by pre-existing duplicate/null data) can no
longer abort every later collection's indexes. This is the exact prod failure
mode that left orders.order_id failing on legacy null order_ids and silently
taking out customers/products/users/... indexes too.
"""
import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.connection import DatabaseConnection  # noqa: E402


class _FakeColl:
    def __init__(self, name, fail=False):
        self.name = name
        self.fail = fail
        self.calls = 0

    def create_index(self, keys, **kw):
        self.calls += 1
        if self.fail:
            raise Exception("E11000 duplicate key error (simulated)")
        return "idx"


class _FakeDB:
    def __init__(self, fail_colls):
        self._colls = {}
        self._fail = set(fail_colls)

    def __getitem__(self, name):
        if name not in self._colls:
            self._colls[name] = _FakeColl(name, fail=(name in self._fail))
        return self._colls[name]


def test_one_failing_index_does_not_abort_the_rest():
    conn = DatabaseConnection()
    saved_db, saved_connected = conn._db, conn._connected
    try:
        conn._connected = True
        db = _FakeDB(fail_colls={"orders"})  # EVERY orders index build raises
        conn._db = db

        # Must NOT raise even though orders.* all fail.
        conn.ensure_indexes()

        # Collections that come AFTER orders still got their indexes built --
        # proves the per-index isolation (pre-fix, the orders failure aborted all).
        assert db["customers"].calls > 0
        assert db["products"].calls > 0
        assert db["users"].calls > 0
        assert db["health_checks"].calls > 0  # the last (TTL) index still attempted
        # orders was still attempted (each index caught independently).
        assert db["orders"].calls >= 1
    finally:
        conn._db, conn._connected = saved_db, saved_connected


def test_all_succeed_path_is_clean():
    conn = DatabaseConnection()
    saved_db, saved_connected = conn._db, conn._connected
    try:
        conn._connected = True
        db = _FakeDB(fail_colls=set())  # nothing fails
        conn._db = db
        conn.ensure_indexes()  # must not raise
        assert db["orders"].calls > 0
        assert db["audit_logs"].calls > 0
    finally:
        conn._db, conn._connected = saved_db, saved_connected
