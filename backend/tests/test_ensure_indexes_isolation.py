"""
ensure_indexes per-index isolation
===================================
Regression guard for the documented prod incident: ensure_indexes used to wrap
ALL ~80 create_index calls in one try/except, so the FIRST failing index (a
unique index rejected by legacy duplicate/null keys -- orders.order_id was null
on 322/343 prod rows) aborted every LATER build, silently leaving the whole DB
unindexed (including the GST uniq_invoice_number backstop).

_safe_index now builds each index in isolation: a dirty collection loses only
its OWN constrained index, never the rest. No mongod needed -- a fake db proves
the control flow.
"""

from database.connection import DatabaseConnection


class _FakeColl:
    def __init__(self, name, fail_keys=()):
        self.name = name
        self._fail = set(fail_keys)
        self.built = []

    def create_index(self, keys, **kwargs):
        label = kwargs.get("name") or (keys if isinstance(keys, str) else str(keys))
        if label in self._fail:
            raise Exception(f"E11000 simulated duplicate/null key on {self.name}.{label}")
        self.built.append(label)
        return label


class _FakeDB:
    def __init__(self, fail_map=None):
        self._fail_map = fail_map or {}
        self.colls = {}

    def __getitem__(self, name):
        if name not in self.colls:
            self.colls[name] = _FakeColl(name, self._fail_map.get(name, ()))
        return self.colls[name]


def _fresh_conn(fake_db):
    conn = DatabaseConnection()  # singleton; __init__ resets flags
    conn._connected = True
    conn._db = fake_db
    return conn


def test_clean_db_builds_every_index():
    conn = _fresh_conn(_FakeDB())
    conn.ensure_indexes()
    assert conn._index_skipped == []
    # 70+ indexes across all collections; locks that the conversion kept them all
    assert conn._index_ok >= 70


def test_dirty_collection_does_not_abort_the_rest():
    # The exact prod failure: orders.order_id unique build throws on null rows.
    conn = _fresh_conn(_FakeDB({"orders": {"order_id"}}))
    conn.ensure_indexes()

    # the bad index is recorded as skipped (fail-loud), not swallowed
    assert any("orders.order_id" in s for s in conn._index_skipped)

    # CRITICAL: the GST invoice backstop on the SAME collection still built
    assert "uniq_invoice_number" in conn._db["orders"].built

    # other order indexes after the failing one still built
    assert "order_number" in conn._db["orders"].built

    # and OTHER collections are completely unaffected
    assert "customer_id" in conn._db["customers"].built
    assert "product_id" in conn._db["products"].built
    assert "uniq_employee_date" in conn._db["attendance"].built
    assert conn._index_ok >= 60


def test_multiple_dirty_collections_each_isolated():
    conn = _fresh_conn(
        _FakeDB(
            {
                "customers": {"customer_id"},  # genuine prod dups
                "products": {"product_id"},  # 10805/10820 null in prod
            }
        )
    )
    conn.ensure_indexes()
    assert len(conn._index_skipped) == 2
    assert any("customers.customer_id" in s for s in conn._index_skipped)
    assert any("products.product_id" in s for s in conn._index_skipped)
    # the rest of each dirty collection still built (e.g. products.sku, barcode)
    assert "sku" in conn._db["products"].built
    assert "barcode" in conn._db["products"].built
    assert "mobile" in conn._db["customers"].built


def test_not_connected_is_a_noop():
    conn = DatabaseConnection()
    conn._connected = False
    conn._db = None
    # must not raise
    conn.ensure_indexes()
