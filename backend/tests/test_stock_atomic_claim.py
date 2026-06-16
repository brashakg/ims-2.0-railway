"""
Concurrency: atomic FIFO stock claim (POS oversell window).

StockRepository.claim_one_available must claim exactly one AVAILABLE unit per
call via find_one_and_update(status="AVAILABLE"), flip it SOLD, and never hand
the SAME unit to two callers. This is the data-integrity guard behind the POS
_mark_units_sold FIFO path (replacing the prior find-then-mark check-then-act).
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _matches(doc, flt):
    for k, v in flt.items():
        if isinstance(v, dict) and "$nin" in v:
            if doc.get(k) in v["$nin"]:
                return False
        elif doc.get(k) != v:
            return False
    return True


class _FakeColl:
    """Minimal find_one_and_update with $set + $nin, mutating in place."""

    def __init__(self, docs):
        self.docs = docs

    def find_one_and_update(self, flt, upd):
        for d in self.docs:
            if _matches(d, flt):
                d.update(upd.get("$set", {}))
                return dict(d)
        return None


def _repo(docs):
    from database.repositories.product_repository import StockRepository

    return StockRepository(_FakeColl(docs))


def test_claim_returns_available_and_marks_sold():
    docs = [
        {"stock_id": "U1", "product_id": "P", "store_id": "S", "status": "AVAILABLE"},
    ]
    repo = _repo(docs)
    sid = repo.claim_one_available("P", "S", "ORD1")
    assert sid == "U1"
    assert docs[0]["status"] == "SOLD"
    assert docs[0]["order_id"] == "ORD1"


def test_two_claims_never_return_same_unit():
    docs = [
        {"stock_id": "U1", "product_id": "P", "store_id": "S", "status": "AVAILABLE"},
        {"stock_id": "U2", "product_id": "P", "store_id": "S", "status": "AVAILABLE"},
    ]
    repo = _repo(docs)
    a = repo.claim_one_available("P", "S", "ORD1")
    b = repo.claim_one_available("P", "S", "ORD2")
    assert {a, b} == {"U1", "U2"}  # two distinct units, no double-claim


def test_claim_none_when_no_available():
    docs = [
        {"stock_id": "U1", "product_id": "P", "store_id": "S", "status": "SOLD"},
    ]
    repo = _repo(docs)
    assert repo.claim_one_available("P", "S", "ORD1") is None


def test_exclude_ids_skips_claimed_in_same_order():
    docs = [
        {"stock_id": "U1", "product_id": "P", "store_id": "S", "status": "AVAILABLE"},
        {"stock_id": "U2", "product_id": "P", "store_id": "S", "status": "AVAILABLE"},
    ]
    repo = _repo(docs)
    sid = repo.claim_one_available("P", "S", "ORD1", exclude_ids={"U1"})
    assert sid == "U2"


def test_claims_work_over_real_mock_collection_no_mongo():
    """Regression: in local no-Mongo mode the bound collection is the real
    MockCollection, which lacked find_one_and_update -> the atomic claims
    silently no-opped (POS FIFO never flipped SOLD, transfer ship moved 0).
    MockCollection now implements it, so both claims work in mock mode too."""
    from database.connection import MockCollection
    from database.repositories.product_repository import StockRepository

    coll = MockCollection("stock")
    coll.insert_one(
        {"_id": "U1", "stock_id": "U1", "product_id": "P", "store_id": "S", "status": "AVAILABLE"}
    )
    coll.insert_one(
        {"_id": "U2", "stock_id": "U2", "product_id": "P", "store_id": "S", "status": "AVAILABLE"}
    )
    repo = StockRepository(coll)

    # FIFO sale claim flips exactly one AVAILABLE unit SOLD.
    sid = repo.claim_one_available("P", "S", "ORD1")
    assert sid in {"U1", "U2"}
    assert coll.find_one({"stock_id": sid})["status"] == "SOLD"

    # exclude_ids ($nin) now honored by the mock matcher.
    other = repo.claim_one_available("P", "S", "ORD2", exclude_ids={sid})
    assert other == ("U2" if sid == "U1" else "U1")

    # transfer claim flips AVAILABLE -> TRANSFERRED; a non-AVAILABLE unit fails.
    coll.insert_one(
        {"_id": "U3", "stock_id": "U3", "product_id": "P", "store_id": "S", "status": "AVAILABLE"}
    )
    assert repo.claim_for_transfer("U3", "T1", "S2") is True
    assert coll.find_one({"stock_id": "U3"})["status"] == "TRANSFERRED"
    assert repo.claim_for_transfer("U3", "T1", "S2") is False  # already claimed
