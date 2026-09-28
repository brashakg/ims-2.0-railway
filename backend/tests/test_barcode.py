"""
IMS 2.0 - Per-unit barcode minter (services/barcode.py)
=======================================================
Pure-function + fake-counter tests; no Mongo required. The doors that call the
minter are covered in tests/test_unit_barcode_one_mint.py.
"""

from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.services.barcode import (  # noqa: E402
    allocate_sequence,
    mint_unit_barcode,
)

# The till's own test for "this is a barcode" (BarcodeScanner.tsx).
TILL = re.compile(r"^[A-Z0-9]{8,}$", re.I)


class _FakeCounter:
    """Mimics find_one_and_update with $inc + upsert + ReturnDocument.AFTER."""

    def __init__(self):
        self.docs = {}

    def find_one_and_update(self, flt, update, upsert=False, return_document=None):
        _id = flt["_id"]
        cur = dict(self.docs.get(_id, {"_id": _id, "seq": 0}))
        cur["seq"] = cur.get("seq", 0) + update["$inc"]["seq"]
        self.docs[_id] = cur
        return cur


class _FakeDB:
    def __init__(self):
        self.counters = _FakeCounter()

    def get_collection(self, name):
        assert name == "counters"
        return self.counters


def test_allocate_sequence_is_monotonic():
    c = _FakeCounter()
    seqs = [allocate_sequence(c) for _ in range(5)]
    assert seqs == [1, 2, 3, 4, 5]


def test_allocate_sequence_fail_soft_without_db():
    assert allocate_sequence(None) is None


def test_mint_is_store_prefix_plus_counter():
    db = _FakeDB()
    assert mint_unit_barcode(db, "BV-DHN-02") == "BV0000000001"
    assert mint_unit_barcode(db, "WO-DHN-01") == "WO0000000002"


def test_two_shops_sharing_a_prefix_never_collide():
    """Every BV shop shares 'BV'; the chain-wide counter keeps codes unique."""
    db = _FakeDB()
    codes = [mint_unit_barcode(db, s) for s in ("BV-DHN-02", "BV-BOK-01") * 50]
    assert len(set(codes)) == 100
    assert all(re.fullmatch(r"BV\d{10}", c) for c in codes)


def test_no_counter_still_mints_a_scannable_code():
    """Fail-soft: no DB counter must never block a receipt or mint a hyphen."""
    codes = {mint_unit_barcode(None, "BV-DHN-02") for _ in range(200)}
    assert len(codes) == 200
    assert all(TILL.match(c) and c.startswith("BV") for c in codes)


def test_a_broken_counter_falls_back_too():
    class _Boom:
        def get_collection(self, name):
            raise RuntimeError("mongo down")

    assert TILL.match(mint_unit_barcode(_Boom(), "BV-DHN-02"))
