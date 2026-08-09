"""
Concurrency: atomic FIFO stock claim (POS oversell window).

StockRepository.claim_one_available must claim exactly one AVAILABLE unit per
call via find_one_and_update(status="AVAILABLE"), flip it SOLD, and never hand
the SAME unit to two callers. This is the data-integrity guard behind the POS
_mark_units_sold FIFO path (replacing the prior find-then-mark check-then-act).

F2 (2026-08 audit) updated this harness twice over:
  * the FEFO fixtures used HARD-CODED expiry dates that have since gone past,
    so they were silently asserting "the most-expired unit is dispensed first" --
    the very defect F2 fixes. Every expiry is now RELATIVE to IST today, so the
    intent (earliest-expiry-first among IN-DATE units) is what is actually
    tested, forever;
  * the fake matcher now models $in / $gte / $or / $not+$type with Mongo's BSON
    type-bracketing, because the expiry floor relies on it.
"""
from __future__ import annotations

import os
import re
import sys
from datetime import date, datetime, timedelta

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.utils.ist import ist_today  # noqa: E402

_MISSING = object()


def _iso(delta_days: int) -> str:
    """An ISO expiry date relative to IST today (negative == already expired)."""
    return (ist_today() + timedelta(days=delta_days)).isoformat()


def _same_bson_type(a, b) -> bool:
    """Mongo compares only values of the same BSON type ('type bracketing')."""
    if isinstance(a, str) and isinstance(b, str):
        return True
    if isinstance(a, (datetime, date)) and isinstance(b, (datetime, date)):
        return True
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool)
    return isinstance(a, (int, float)) and isinstance(b, (int, float))


def _op(val, op, arg) -> bool:
    if op == "$in":
        return (None if val is _MISSING else val) in arg
    if op == "$nin":
        return (None if val is _MISSING else val) not in arg
    if op == "$ne":
        return not (val is not _MISSING and val == arg)
    if op == "$gte":
        return val is not _MISSING and _same_bson_type(val, arg) and val >= arg
    if op == "$type":
        if arg == "string":
            return isinstance(val, str)
        if arg == "date":
            # BSON date-typed values -- the shape inventory._parse_expiry reads
            # natively and buckets as EXPIRED, so the floor must reach them too.
            return isinstance(val, (datetime, date)) and not isinstance(val, bool)
        return False
    if op == "$regex":
        # Mongo $regex only ever matches STRING values -- that type-bracketing is
        # exactly what makes the ISO-shape branch of the expiry floor work.
        return isinstance(val, str) and re.search(arg, val) is not None
    raise AssertionError("fake collection: unsupported operator " + op)


def _matches(doc, flt):
    for k, v in (flt or {}).items():
        if k == "$or":
            if not any(_matches(doc, c) for c in v):
                return False
            continue
        if isinstance(v, dict) and any(str(o).startswith("$") for o in v):
            val = doc.get(k, _MISSING)
            for op, arg in v.items():
                if op == "$exists":
                    if bool(arg) != (k in doc):
                        return False
                elif op == "$not":
                    if all(_op(val, o2, a2) for o2, a2 in arg.items()):
                        return False
                elif not _op(val, op, arg):
                    return False
            continue
        if doc.get(k) != v:
            return False
    return True


class _FakeColl:
    """Minimal find_one_and_update with $set + the operators the claim uses,
    mutating in place (mirrors the pymongo surface the FEFO claim relies on)."""

    def __init__(self, docs):
        self.docs = docs

    def find_one_and_update(self, flt, upd, sort=None):
        candidates = [d for d in self.docs if _matches(d, flt)]
        if sort:
            for key, direction in reversed(list(sort)):
                candidates.sort(
                    key=lambda d, k=key: str(d.get(k)), reverse=direction == -1
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
        _unit("U-LATE", _iso(365)),
        _unit("U-EARLY", _iso(20)),
        _unit("U-MID", _iso(120)),
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
        _unit("U-DATED", _iso(60)),
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
        _unit("U-DATED", _iso(60)),
    ]
    repo = _repo(docs)
    assert repo.claim_one_available("P", "S", "O1") == "U-DATED"
    assert repo.claim_one_available("P", "S", "O2") == "U-NULL"


def test_fefo_exclude_ids_applies_to_dated_units():
    # The used-set exclusion must hold in the FEFO phase too: two lines of the
    # same order never grab the same dated unit.
    docs = [
        _unit("U-EARLY", _iso(20)),
        _unit("U-LATE", _iso(365)),
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
         "status": "AVAILABLE", "expiry_date": _iso(365)}
    )
    coll.insert_one(
        {"_id": "D-EARLY", "stock_id": "D-EARLY", "product_id": "P", "store_id": "S",
         "status": "AVAILABLE", "expiry_date": _iso(20)}
    )
    repo = StockRepository(coll)

    assert repo.claim_one_available("P", "S", "O1") == "D-EARLY"
    assert repo.claim_one_available("P", "S", "O2") == "D-LATE"
    assert repo.claim_one_available("P", "S", "O3") == "N1"
    assert repo.claim_one_available("P", "S", "O4") is None


def test_expiry_floor_is_real_over_the_mock_collection_not_a_no_op():
    """MockCollection._matches_filter used to treat UNKNOWN operators as
    MATCHING, so every branch of the expiry $or matched and the floor was a
    complete NO-OP in no-Mongo mode -- a test asserting it over a MockCollection
    would have passed with the floor DELETED. $not / $type / $regex are modelled
    now, so this asserts real behaviour: an expired unit is neither claimable
    nor counted, while an undated one beside it is untouched."""
    from database.connection import MockCollection
    from database.repositories.product_repository import StockRepository

    coll = MockCollection("stock")
    coll.insert_one(
        {"_id": "EXPIRED", "stock_id": "EXPIRED", "product_id": "P", "store_id": "S",
         "status": "AVAILABLE", "expiry_date": _iso(-5)}
    )
    coll.insert_one(
        {"_id": "FRAME", "stock_id": "FRAME", "product_id": "P", "store_id": "S",
         "status": "AVAILABLE"}
    )
    # A DATETIME-valued expiry. Without MockCollection's type guard a raw
    # str-vs-datetime $gte raises TypeError, BaseRepository.count swallows it
    # and returns 0, and EVERY unit for this product vanishes -- including the
    # undated frame above. This row is what makes that regression visible.
    coll.insert_one(
        {"_id": "DTYPED", "stock_id": "DTYPED", "product_id": "P", "store_id": "S",
         "status": "AVAILABLE",
         "expiry_date": datetime(ist_today().year + 2, 1, 1)}
    )
    repo = StockRepository(coll)

    # The floor BITES: the undated frame AND the in-date datetime unit are
    # sellable, the ISO-expired one is held back -- and nothing raised, so the
    # neighbours did not vanish with it.
    assert repo.find_available("P", "S") == 2
    assert repo.count_expired("P", "S") == 1
    # The claim takes the in-date units and never the expired one.
    claimed = {
        repo.claim_one_available("P", "S", "O1"),
        repo.claim_one_available("P", "S", "O2"),
    }
    assert claimed == {"FRAME", "DTYPED"}
    assert coll.find_one({"stock_id": "EXPIRED"})["status"] == "AVAILABLE"
    assert repo.claim_one_available("P", "S", "O3") is None
    # And the guarded scan-sell refuses it too.
    assert repo.mark_sold("EXPIRED", "O3") is False


def test_mock_collection_models_not_and_type_operators():
    """Direct guard on the mock itself: if these regress to 'unknown operator ->
    matches', every expiry assertion above silently stops proving anything."""
    from database.connection import MockCollection

    coll = MockCollection("probe")
    assert coll._matches_filter({"v": "2026-01-01"}, {"v": {"$type": "string"}}) is True
    assert coll._matches_filter({"v": 5}, {"v": {"$type": "string"}}) is False
    assert coll._matches_filter({"v": 5}, {"v": {"$not": {"$type": "string"}}}) is True
    assert (
        coll._matches_filter({"v": "2026-01-01"}, {"v": {"$not": {"$type": "string"}}})
        is False
    )
    assert coll._matches_filter({}, {"v": {"$not": {"$regex": "^[0-9]{4}-"}}}) is True


def test_mock_range_operator_survives_mixed_types_on_an_ungated_query():
    """Pins MockCollection's BSON type bracketing on a query with NO $type gate.

    The expiry floor short-circuits on $type before any comparison, so it can
    never exercise the guard -- which is exactly why reverting the guard did not
    fail any expiry test. find_expiring is the real exposure: it compares
    expiry_date against STRING bounds with no type gate at all, so ONE
    date-typed row raises TypeError, BaseRepository.find_many swallows it, and
    the whole expiry report comes back EMPTY -- hiding the string-dated units
    that were perfectly readable.
    """
    from database.connection import MockCollection
    from database.repositories.product_repository import StockRepository

    coll = MockCollection("stock")
    coll.insert_one(
        {"_id": "STR-SOON", "stock_id": "STR-SOON", "product_id": "P",
         "store_id": "S", "status": "AVAILABLE",
         "expiry_date": (ist_today() + timedelta(days=10)).isoformat()}
    )
    coll.insert_one(
        {"_id": "DATE-TYPED", "stock_id": "DATE-TYPED", "product_id": "P",
         "store_id": "S", "status": "AVAILABLE",
         "expiry_date": datetime(ist_today().year + 2, 1, 1)}
    )
    repo = StockRepository(coll)

    rows = repo.find_expiring("S", days=30)

    # The readable, genuinely-expiring unit must still be reported.
    assert [r["stock_id"] for r in rows] == ["STR-SOON"]
