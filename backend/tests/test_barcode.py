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
import threading
import time

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


class _AtomicOnlyCounter:
    """A counter store shaped like MongoDB: find_one_and_update is ONE atomic
    step, but a separate read and write are not (the read yields, as a network
    round trip does). A minter that reads the counter and then writes it back
    races here, exactly as it would on the real database."""

    def __init__(self):
        self.docs = {}
        self._lock = threading.Lock()

    def find_one_and_update(self, flt, update, upsert=False, return_document=None):
        with self._lock:
            cur = dict(self.docs.get(flt["_id"], {"_id": flt["_id"], "seq": 0}))
            cur["seq"] += update["$inc"]["seq"]
            self.docs[flt["_id"]] = cur
            return dict(cur)

    def find_one(self, flt, *a, **k):
        doc = self.docs.get(flt["_id"])
        time.sleep(0.001)
        return dict(doc) if doc else None

    def update_one(self, flt, update, upsert=False, **k):
        cur = self.docs.setdefault(flt["_id"], {"_id": flt["_id"], "seq": 0})
        cur.update(update.get("$set") or {})
        for key, n in (update.get("$inc") or {}).items():
            cur[key] = cur.get(key, 0) + n


def test_parallel_receipts_at_two_shops_never_collide():
    """Every BV shop shares 'BV', so uniqueness is the body alone. Receipts at
    two shops run AT THE SAME TIME (8 threads released together); every code
    must still be distinct AND come from the chain-wide counter (1..200 with no
    gap) -- a code from the random fallback would hide a broken counter."""
    counter = _AtomicOnlyCounter()

    class _DB:
        def get_collection(self, name):
            assert name == "counters"
            return counter

    db, per_thread, shops = _DB(), 25, ("BV-DHN-02", "BV-BOK-01") * 4
    start = threading.Barrier(len(shops))
    codes, codes_lock = [], threading.Lock()

    def receive(shop):
        start.wait()
        minted = [mint_unit_barcode(db, shop) for _ in range(per_thread)]
        with codes_lock:
            codes.extend(minted)

    threads = [threading.Thread(target=receive, args=(s,)) for s in shops]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(codes) == len(shops) * per_thread
    assert len(set(codes)) == len(codes), "two parallel receipts minted one code"
    assert all(re.fullmatch(r"BV\d{10}", c) for c in codes)
    assert sorted(int(c[2:]) for c in codes) == list(range(1, len(codes) + 1))


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
