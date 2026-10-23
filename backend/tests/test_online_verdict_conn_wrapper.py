"""The website verdict must work whether a caller hands it the raw database or
the get_db() connection wrapper (SeededDatabaseConnection, not subscriptable).

Prod 2026-10-23: the Catalogue list (GET /products, products._stamped) passed
the wrapper, so every not-yet-published row read "Not for the website: its
catalogue row could not be read ('SeededDatabaseConnection' object is not
subscriptable)". Same class as #1248 (the Add/Edit form line). The gate now
unwraps itself, so no caller can re-introduce it.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
sys.path.insert(0, os.path.dirname(__file__))

from api.services import online_catalog  # noqa: E402
from api.services import shopify_push as sp  # noqa: E402
from strict_fakes import StrictDB  # noqa: E402


class _Conn:
    """Shaped like database.connection.SeededDatabaseConnection: NOT subscriptable."""

    def __init__(self, db):
        self._db = db
        self.is_connected = True

    @property
    def db(self):
        return self._db

    def get_collection(self, name):
        return self._db.get_collection(name)


def _db():
    db = StrictDB()
    db.seed("brand_masters", [{"brand_id": "b1", "name": "Ray-Ban", "is_active": True,
                               "sync_to_shopify_default": True}])
    db.seed("products", [{"product_id": "p1", "sku": "SGRB1", "brand": "Ray-Ban", "is_active": True}])
    db.seed("catalog_products", [{"id": "t1", "product_id": "p1", "sku": "SGRB1", "brand": "Ray-Ban",
                                  "parent_sku": "SGRB1", "ecom": {}}])
    return db


_TWIN = {"id": "t1", "product_id": "p1", "sku": "SGRB1", "brand": "Ray-Ban", "parent_sku": "SGRB1", "ecom": {}}


def test_gate_gives_the_same_answer_for_the_wrapper_and_the_database():
    db = _db()
    raw = sp.product_push_refusal(db, dict(_TWIN))
    wrapped = sp.product_push_refusal(_Conn(db), dict(_TWIN))
    assert wrapped == raw
    assert "could not be read" not in str(wrapped)


def test_catalogue_list_stamp_with_the_wrapper_shows_no_read_error():
    rows = [dict(_TWIN)]
    online_catalog.stamp_online_state(_Conn(_db()), rows)
    assert "could not be read" not in str(rows[0].get("online_note") or rows[0])
