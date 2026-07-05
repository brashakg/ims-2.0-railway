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
        if isinstance(v, dict):
            if "$nin" in v and doc.get(k) in v["$nin"]:
                return False
            if "$exists" in v and bool(v["$exists"]) != (k in doc):
                return False
            if "$ne" in v and k in doc and doc.get(k) == v["$ne"]:
                return False
        elif doc.get(k) != v:
            return False
    return True


class _FakeColl:
    """Minimal find_one_and_update with $set + $nin/$exists/$ne + sort,
    mutating in place (mirrors the pymongo surface the FEFO claim uses)."""

    def __init__(self, docs):
        self.docs = docs

    def find_one_and_update(self, flt, upd, sort=None):
        candidates = [d for d in self.docs if _matches(d, flt)]
        if sort:
            for key, direction in reversed(list(sort)):
                candidates.sort(
                    key=lambda d, k=key: d.get(k), reverse=direction == -1
                )
        if not candidates:
            return None
        doc = candidates[0]
        doc.update(upd.get("$set", {}))
        return dict(doc)


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


# ---------------------------------------------------------------------------
# FEFO (First-Expiry-First-Out): dated units are dispensed earliest-expiry
# first; undated units are only claimed once every dated unit is gone.
# ---------------------------------------------------------------------------


def _unit(sid, expiry=..., status="AVAILABLE"):
    d = {"stock_id": sid, "product_id": "P", "store_id": "S", "status": status}
    if expiry is not ...:
        d["expiry_date"] = expiry
    return d


def test_fefo_dated_units_claimed_earliest_expiry_first():
    # Natural (insertion) order deliberately NOT the expiry order.
    docs = [
        _unit("U-LATE", "2027-01-01"),
        _unit("U-EARLY", "2026-08-01"),
        _unit("U-MID", "2026-12-15"),
    ]
    repo = _repo(docs)
    assert repo.claim_one_available("P", "S", "O1") == "U-EARLY"
    assert repo.claim_one_available("P", "S", "O2") == "U-MID"
    assert repo.claim_one_available("P", "S", "O3") == "U-LATE"
    assert repo.claim_one_available("P", "S", "O4") is None


def test_fefo_undated_claimed_only_after_dated_exhausted():
    # Undated unit sits FIRST in natural order; the dated one must still win.
    docs = [
        _unit("U-UNDATED"),
        _unit("U-DATED", "2026-09-30"),
    ]
    repo = _repo(docs)
    assert repo.claim_one_available("P", "S", "O1") == "U-DATED"
    assert repo.claim_one_available("P", "S", "O2") == "U-UNDATED"


def test_fefo_null_expiry_treated_as_undated():
    # expiry_date explicitly null (not just absent) must NOT be picked first --
    # BSON orders null before dates, which is exactly the trap the two-phase
    # claim avoids.
    docs = [
        _unit("U-NULL", None),
        _unit("U-DATED", "2026-09-30"),
    ]
    repo = _repo(docs)
    assert repo.claim_one_available("P", "S", "O1") == "U-DATED"
    assert repo.claim_one_available("P", "S", "O2") == "U-NULL"


def test_fefo_exclude_ids_applies_to_dated_units():
    # The used-set exclusion must hold in the FEFO phase too: two lines of the
    # same order never grab the same dated unit.
    docs = [
        _unit("U-EARLY", "2026-08-01"),
        _unit("U-LATE", "2027-01-01"),
    ]
    repo = _repo(docs)
    sid = repo.claim_one_available("P", "S", "O1", exclude_ids={"U-EARLY"})
    assert sid == "U-LATE"


def test_fefo_plain_products_without_expiry_unchanged():
    # No unit carries expiry_date at all -> phase 1 matches nothing and the
    # fallback claims in natural order, exactly like the pre-FEFO behaviour.
    docs = [
        _unit("U1"),
        _unit("U2"),
    ]
    repo = _repo(docs)
    assert repo.claim_one_available("P", "S", "O1") == "U1"
    assert docs[0]["status"] == "SOLD"
    assert docs[0]["order_id"] == "O1"
    assert repo.claim_one_available("P", "S", "O2") == "U2"
    assert repo.claim_one_available("P", "S", "O3") is None


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


def test_fefo_works_over_real_mock_collection_no_mongo():
    """MockCollection now supports $exists and find_one_and_update(sort=...),
    so the FEFO expiry-first claim behaves correctly in local no-Mongo mode
    too (dated earliest first, undated last)."""
    from database.connection import MockCollection
    from database.repositories.product_repository import StockRepository

    coll = MockCollection("stock")
    coll.insert_one(
        {"_id": "N1", "stock_id": "N1", "product_id": "P", "store_id": "S",
         "status": "AVAILABLE"}
    )
    coll.insert_one(
        {"_id": "D-LATE", "stock_id": "D-LATE", "product_id": "P", "store_id": "S",
         "status": "AVAILABLE", "expiry_date": "2027-03-01"}
    )
    coll.insert_one(
        {"_id": "D-EARLY", "stock_id": "D-EARLY", "product_id": "P", "store_id": "S",
         "status": "AVAILABLE", "expiry_date": "2026-08-01"}
    )
    repo = StockRepository(coll)

    assert repo.claim_one_available("P", "S", "O1") == "D-EARLY"
    assert repo.claim_one_available("P", "S", "O2") == "D-LATE"
    assert repo.claim_one_available("P", "S", "O3") == "N1"
    assert repo.claim_one_available("P", "S", "O4") is None
