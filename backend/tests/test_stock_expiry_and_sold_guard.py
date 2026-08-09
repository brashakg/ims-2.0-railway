"""
IMS 2.0 - StockRepository: expiry floor + guarded mark_sold + cancel restock
============================================================================
Three audit findings land on StockRepository, all provable at the repo level:

F2 (P1 PATIENT-SAFETY) -- EXPIRED contact lenses were sellable, and sold FIRST.
    `find_available` counted them, and `claim_one_available`'s FEFO phase sorted
    expiry ASCENDING with NO date floor, so POS dispensed the MOST-expired unit
    on the shelf, marked it SOLD, and said nothing. There is now a floor at IST
    today on BOTH the count and the claim -- and, critically, a unit that carries
    NO expiry_date (every frame / sunglass / accessory) is completely unaffected.
    Expired units are not hidden silently either: `count_expired` reports them.

F7 (P2 STOCK) -- `mark_sold` had NO status guard: the barcode-scan path could
    sell a unit that was already SOLD / TRANSFERRED / QUARANTINED / DAMAGED and
    OVERWRITE its prior sale lineage (order_id / sold_at), destroying the trail
    of the earlier sale. It is now an atomic guarded update (AVAILABLE + in date).

F3 (P1 STOCK) -- `release_sold_units_for_order` is the stock-side UNDO used by
    order cancel / DRAFT-line removal. It must be idempotent (a double cancel
    cannot double-reactivate) and must never resurrect a DAMAGED / TRANSFERRED
    unit onto the sellable shelf.

No Mongo: an isolated fake collection whose matcher models the operators the
repo actually uses, INCLUDING BSON type-bracketing for $gte (a string bound
never matches a datetime field, and vice-versa) -- that bracketing is exactly
why the expiry filters are string-vs-string.
"""

from __future__ import annotations

import os
import re
import sys
from datetime import date, datetime, timedelta

import pytest

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.utils.ist import ist_today  # noqa: E402
from database.repositories.product_repository import StockRepository  # noqa: E402

_MISSING = object()


def _same_bson_type(a, b) -> bool:
    """Mongo only compares values of the same BSON type ('type bracketing')."""
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
    if op == "$lt":
        return val is not _MISSING and _same_bson_type(val, arg) and val < arg
    if op == "$type":
        return isinstance(val, str) if arg == "string" else False
    if op == "$regex":
        # Mongo $regex only ever matches STRING values -- that type-bracketing is
        # exactly what makes the ISO-shape branch of the expiry floor work.
        return isinstance(val, str) and re.search(arg, val) is not None
    raise AssertionError("fake collection: unsupported operator " + op)


def _match(doc, flt) -> bool:
    for key, cond in (flt or {}).items():
        if key == "$or":
            if not any(_match(doc, c) for c in cond):
                return False
            continue
        if isinstance(cond, dict) and any(str(k).startswith("$") for k in cond):
            val = doc.get(key, _MISSING)
            for op, arg in cond.items():
                if op == "$exists":
                    if bool(arg) != (key in doc):
                        return False
                elif op == "$not":
                    if all(_op(val, o2, a2) for o2, a2 in arg.items()):
                        return False
                elif not _op(val, op, arg):
                    return False
            continue
        if doc.get(key) != cond:
            return False
    return True


class _FakeColl:
    """Mutating in-place stand-in for the stock_units pymongo collection."""

    def __init__(self, docs):
        self.docs = docs

    def _matching(self, flt):
        return [d for d in self.docs if _match(d, flt)]

    def find_one(self, flt, projection=None):
        found = self._matching(flt)
        return dict(found[0]) if found else None

    def find(self, flt=None, projection=None):
        return _Cursor([dict(d) for d in self._matching(flt or {})])

    def count_documents(self, flt=None):
        return len(self._matching(flt or {}))

    def find_one_and_update(self, flt, upd, sort=None, **kw):
        candidates = self._matching(flt)
        if sort:
            for key, direction in reversed(list(sort)):
                candidates.sort(
                    key=lambda d, k=key: str(d.get(k)), reverse=direction == -1
                )
        if not candidates:
            return None
        target = candidates[0]
        # candidates hold the SAME dict objects as self.docs -> mutate in place.
        before = dict(target)
        target.update(upd.get("$set", {}))
        return before


class _Cursor(list):
    def sort(self, *a, **k):
        return self

    def skip(self, n):
        return self

    def limit(self, n):
        return self


def _repo(docs):
    return StockRepository(_FakeColl(docs))


def _unit(sid, **over):
    d = {
        "stock_id": sid,
        "_id": sid,
        "product_id": "P1",
        "store_id": "S1",
        "status": "AVAILABLE",
    }
    d.update(over)
    return d


def _iso(delta_days: int) -> str:
    return (ist_today() + timedelta(days=delta_days)).isoformat()


# ===========================================================================
# F2 -- the expiry floor
# ===========================================================================


def test_expired_dated_unit_is_not_claimable():
    docs = [_unit("U-EXPIRED", expiry_date=_iso(-1))]
    repo = _repo(docs)
    assert repo.claim_one_available("P1", "S1", "ORD1") is None
    # ...and it was NOT written to: still AVAILABLE, no order stamped.
    assert docs[0]["status"] == "AVAILABLE"
    assert "order_id" not in docs[0]


def test_expired_dated_unit_is_not_counted_as_available():
    repo = _repo(
        [
            _unit("U-EXP1", expiry_date=_iso(-40)),
            _unit("U-EXP2", expiry_date=_iso(-1)),
            _unit("U-GOOD", expiry_date=_iso(30)),
        ]
    )
    assert repo.find_available("P1", "S1") == 1


def test_expired_units_are_surfaced_as_their_own_bucket_not_hidden():
    repo = _repo(
        [
            _unit("U-EXP1", expiry_date=_iso(-40)),
            _unit("U-EXP2", expiry_date=_iso(-1)),
            _unit("U-GOOD", expiry_date=_iso(30)),
        ]
    )
    # The two buckets reconcile exactly against the raw AVAILABLE total (3).
    assert repo.find_available("P1", "S1") == 1
    assert repo.count_expired("P1", "S1") == 2


def test_most_expired_unit_is_no_longer_dispensed_first():
    """The original defect, stated as a test: ascending expiry sort with no
    floor handed POS the OLDEST (most expired) box first."""
    docs = [
        _unit("U-ROTTEN", expiry_date=_iso(-90)),
        _unit("U-ALSO-OLD", expiry_date=_iso(-5)),
        _unit("U-FRESH", expiry_date=_iso(10)),
        _unit("U-FRESHER", expiry_date=_iso(200)),
    ]
    repo = _repo(docs)
    # FEFO still applies -- but only among IN-DATE units.
    assert repo.claim_one_available("P1", "S1", "O1") == "U-FRESH"
    assert repo.claim_one_available("P1", "S1", "O2") == "U-FRESHER"
    assert repo.claim_one_available("P1", "S1", "O3") is None
    by_id = {d["stock_id"]: d for d in docs}
    assert by_id["U-ROTTEN"]["status"] == "AVAILABLE"
    assert by_id["U-ALSO-OLD"]["status"] == "AVAILABLE"


def test_unit_expiring_today_is_still_sellable():
    """Boundary: the floor is >= IST today, so a lens is sellable ON its
    expiry date and unsellable the day after."""
    repo = _repo([_unit("U-TODAY", expiry_date=ist_today().isoformat())])
    assert repo.find_available("P1", "S1") == 1
    assert repo.count_expired("P1", "S1") == 0
    assert repo.claim_one_available("P1", "S1", "O1") == "U-TODAY"


# --- the non-negotiable half: units WITHOUT an expiry are untouched ---------


def test_undated_units_are_completely_unaffected():
    docs = [_unit("F1"), _unit("F2"), _unit("F3")]
    repo = _repo(docs)
    assert repo.find_available("P1", "S1") == 3
    assert repo.count_expired("P1", "S1") == 0
    # Natural (unsorted) order preserved, exactly like the pre-FEFO fallback.
    assert repo.claim_one_available("P1", "S1", "O1") == "F1"
    assert repo.claim_one_available("P1", "S1", "O2") == "F2"
    assert repo.claim_one_available("P1", "S1", "O3") == "F3"
    assert repo.claim_one_available("P1", "S1", "O4") is None


@pytest.mark.parametrize("blank", [None, ""])
def test_null_or_blank_expiry_is_treated_as_undated(blank):
    repo = _repo([_unit("U-BLANK", expiry_date=blank)])
    assert repo.find_available("P1", "S1") == 1
    assert repo.count_expired("P1", "S1") == 0
    assert repo.claim_one_available("P1", "S1", "O1") == "U-BLANK"


def test_expired_lenses_never_hide_the_frames_beside_them():
    """Mixed shelf: an expired dated unit must not drag an undated unit of the
    same product out of availability."""
    repo = _repo(
        [
            _unit("U-EXPIRED", expiry_date=_iso(-3)),
            _unit("U-FRAME"),  # no expiry_date at all
        ]
    )
    assert repo.find_available("P1", "S1") == 1
    assert repo.count_expired("P1", "S1") == 1
    assert repo.claim_one_available("P1", "S1", "O1") == "U-FRAME"


def test_legacy_non_string_expiry_is_sold_not_hidden():
    """A datetime-typed expiry cannot be compared against an ISO string in
    Mongo. We err toward SELLING (never silently hiding stock we cannot reason
    about) -- the opposite choice would black-hole real inventory."""
    repo = _repo([_unit("U-LEGACY", expiry_date=datetime(2030, 1, 1))])
    assert repo.find_available("P1", "S1") == 1
    assert repo.count_expired("P1", "S1") == 0
    assert repo.claim_one_available("P1", "S1", "O1") == "U-LEGACY"


def test_expiry_floor_does_not_weaken_the_status_exclusion():
    repo = _repo(
        [
            _unit("U-SOLD", status="SOLD", expiry_date=_iso(50)),
            _unit("U-QUAR", status="QUARANTINED", expiry_date=_iso(50)),
            _unit("U-OK", expiry_date=_iso(50)),
        ]
    )
    assert repo.find_available("P1", "S1") == 1
    assert repo.claim_one_available("P1", "S1", "O1") == "U-OK"


# ===========================================================================
# F7 -- mark_sold must refuse a unit that is not AVAILABLE
# ===========================================================================


@pytest.mark.parametrize(
    "status", ["SOLD", "TRANSFERRED", "QUARANTINED", "DAMAGED", "RTV", "VOID"]
)
def test_mark_sold_refuses_non_available_unit(status):
    docs = [_unit("U1", status=status, order_id="ORD-ORIGINAL", sold_at="yesterday")]
    repo = _repo(docs)
    assert repo.mark_sold("U1", "ORD-NEW") is False
    # NOTHING was written: the prior sale's lineage survives intact.
    assert docs[0]["status"] == status
    assert docs[0]["order_id"] == "ORD-ORIGINAL"
    assert docs[0]["sold_at"] == "yesterday"


def test_mark_sold_succeeds_on_an_available_unit():
    docs = [_unit("U1")]
    repo = _repo(docs)
    assert repo.mark_sold("U1", "ORD-1") is True
    assert docs[0]["status"] == "SOLD"
    assert docs[0]["order_id"] == "ORD-1"


def test_mark_sold_is_not_double_sellable():
    docs = [_unit("U1")]
    repo = _repo(docs)
    assert repo.mark_sold("U1", "ORD-1") is True
    assert repo.mark_sold("U1", "ORD-2") is False
    assert docs[0]["order_id"] == "ORD-1"  # first sale keeps the unit


def test_mark_sold_refuses_an_expired_unit():
    docs = [_unit("U1", expiry_date=_iso(-1))]
    repo = _repo(docs)
    assert repo.mark_sold("U1", "ORD-1") is False
    assert docs[0]["status"] == "AVAILABLE"


def test_mark_sold_untouched_for_undated_units():
    docs = [_unit("U1", expiry_date=None)]
    repo = _repo(docs)
    assert repo.mark_sold("U1", "ORD-1") is True
    assert docs[0]["status"] == "SOLD"


def test_mark_sold_unknown_id_is_false_not_a_crash():
    repo = _repo([_unit("U1")])
    assert repo.mark_sold("NO-SUCH-UNIT", "ORD-1") is False


# ===========================================================================
# F3 (repo half) -- release_sold_units_for_order
# ===========================================================================


def test_release_reactivates_only_this_orders_sold_units():
    docs = [
        _unit("U1", status="SOLD", order_id="ORD-1", sold_at="t1"),
        _unit("U2", status="SOLD", order_id="ORD-1", sold_at="t1"),
        _unit("U3", status="SOLD", order_id="ORD-2", sold_at="t2"),
        _unit("U4", status="DAMAGED", order_id="ORD-1"),
    ]
    repo = _repo(docs)
    freed, incomplete = repo.release_sold_units_for_order("ORD-1")
    assert sorted(freed) == ["U1", "U2"]
    assert incomplete is False
    by_id = {d["stock_id"]: d for d in docs}
    for sid in ("U1", "U2"):
        assert by_id[sid]["status"] == "AVAILABLE"
        assert by_id[sid]["order_id"] is None  # stale attribution cleared
        assert by_id[sid]["sold_at"] is None
        assert by_id[sid]["prior_sold_order_id"] == "ORD-1"  # lineage preserved
        assert by_id[sid]["released_from_order_id"] == "ORD-1"
    # Another order's unit and a DAMAGED unit are NOT resurrected.
    assert by_id["U3"]["status"] == "SOLD" and by_id["U3"]["order_id"] == "ORD-2"
    assert by_id["U4"]["status"] == "DAMAGED"


def test_release_is_idempotent_double_cancel_cannot_double_reactivate():
    docs = [
        _unit("U1", status="SOLD", order_id="ORD-1"),
        _unit("U2", status="SOLD", order_id="ORD-1"),
    ]
    repo = _repo(docs)
    assert sorted(repo.release_sold_units_for_order("ORD-1").released) == ["U1", "U2"]
    snapshot = [dict(d) for d in docs]
    # Second (retried) cancel: nothing left to claim, nothing changes.
    again = repo.release_sold_units_for_order("ORD-1")
    assert again.released == [] and again.incomplete is False
    assert docs == snapshot


def test_release_can_be_scoped_to_one_line_by_product_and_quantity():
    docs = [
        _unit("A1", product_id="PA", status="SOLD", order_id="ORD-1"),
        _unit("A2", product_id="PA", status="SOLD", order_id="ORD-1"),
        _unit("B1", product_id="PB", status="SOLD", order_id="ORD-1"),
    ]
    repo = _repo(docs)
    freed, incomplete = repo.release_sold_units_for_order(
        "ORD-1", product_id="PA", limit=1
    )
    assert len(freed) == 1 and freed[0] in {"A1", "A2"} and incomplete is False
    by_id = {d["stock_id"]: d for d in docs}
    assert sum(1 for d in docs if d["status"] == "AVAILABLE") == 1
    assert by_id["B1"]["status"] == "SOLD"  # the other line is untouched


def test_release_returns_empty_for_a_blank_order_id():
    repo = _repo([_unit("U1", status="SOLD", order_id="ORD-1")])
    assert repo.release_sold_units_for_order("") == ([], False)


def test_released_unit_is_immediately_sellable_again():
    docs = [_unit("U1", status="SOLD", order_id="ORD-1")]
    repo = _repo(docs)
    repo.release_sold_units_for_order("ORD-1")
    assert repo.find_available("P1", "S1") == 1
    assert repo.claim_one_available("P1", "S1", "ORD-9") == "U1"
    assert docs[0]["order_id"] == "ORD-9"


# ===========================================================================
# PANEL MUST-FIX 8 -- the floor may ONLY act on a canonical ISO date.
# A raw string $gte is lexicographic, not chronological, so it was wrong in
# BOTH directions on the shapes the GRN door actually accepts.
# ===========================================================================


def test_valid_future_non_iso_date_is_not_hidden():
    """'15-08-2027' is a VALID FUTURE date that sorts BELOW today's ISO string.
    The old raw $gte removed it from sale and told the counter it was 'PAST
    THEIR EXPIRY DATE' -- real in-date stock going dark."""
    docs = [_unit("U-DDMMYYYY", expiry_date="15-08-2027")]
    repo = _repo(docs)
    assert repo.find_available("P1", "S1") == 1
    assert repo.count_expired("P1", "S1") == 0
    assert repo.claim_one_available("P1", "S1", "O1") == "U-DDMMYYYY"


def test_expired_non_iso_date_is_also_not_hidden_but_is_flagged_upstream():
    """'31/12/2025' IS genuinely expired, but we cannot prove that with a string
    compare (it sorts ABOVE today and the old code sold it anyway). We keep
    selling it rather than guess -- the fail-soft direction must be consistent --
    and the durable fix is normalising at the GRN door. This test pins the
    CHOICE so it cannot change silently."""
    docs = [_unit("U-SLASH", expiry_date="31/12/2025")]
    repo = _repo(docs)
    assert repo.find_available("P1", "S1") == 1
    assert repo.claim_one_available("P1", "S1", "O1") == "U-SLASH"
    assert repo.is_iso_expiry("31/12/2025") is False
    assert repo.is_iso_expiry("2025-12-31") is True


def test_only_canonical_iso_dates_can_ever_be_held_back():
    repo = _repo(
        [
            _unit("ISO-PAST", expiry_date=_iso(-2)),      # blocked
            _unit("ISO-FUTURE", expiry_date=_iso(2)),     # sellable
            _unit("JUNK", expiry_date="not-a-date"),      # sellable (unreadable)
            _unit("DDMM", expiry_date="15-08-2027"),      # sellable (unreadable)
            _unit("NONE"),                                # sellable (undated)
        ]
    )
    assert repo.find_available("P1", "S1") == 4
    assert repo.count_expired("P1", "S1") == 1


# ===========================================================================
# PANEL MUST-FIX 4 -- a partial release must NOT look like a clean success.
# ===========================================================================


class _FlakyColl(_FakeColl):
    """Fails the Nth find_one_and_update, mid-loop."""

    def __init__(self, docs, fail_on):
        super().__init__(docs)
        self.calls = 0
        self.fail_on = fail_on

    def find_one_and_update(self, flt, upd, sort=None, **kw):
        self.calls += 1
        if self.calls == self.fail_on:
            raise RuntimeError("mongo write blip")
        return super().find_one_and_update(flt, upd, sort=sort, **kw)


def test_partial_release_reports_incomplete():
    docs = [
        _unit("U1", status="SOLD", order_id="ORD-1"),
        _unit("U2", status="SOLD", order_id="ORD-1"),
        _unit("U3", status="SOLD", order_id="ORD-1"),
    ]
    repo = StockRepository(_FlakyColl(docs, fail_on=2))

    released, incomplete = repo.release_sold_units_for_order("ORD-1")

    assert len(released) == 1        # only the first unit came back
    assert incomplete is True        # ...and we SAY SO
    # Two units are genuinely stranded, and the count proves it.
    assert repo.count_sold_units_for_order("ORD-1") == 2


def test_rerun_after_a_partial_release_finishes_the_job():
    """The release is idempotent by construction, so a retry is safe AND
    completes -- which is what makes the cancel re-run path legitimate."""
    docs = [
        _unit("U1", status="SOLD", order_id="ORD-1"),
        _unit("U2", status="SOLD", order_id="ORD-1"),
    ]
    coll = _FlakyColl(docs, fail_on=2)
    repo = StockRepository(coll)
    first = repo.release_sold_units_for_order("ORD-1")
    assert first.incomplete is True and len(first.released) == 1

    coll.fail_on = -1  # the blip clears
    second = repo.release_sold_units_for_order("ORD-1")

    assert second.incomplete is False and len(second.released) == 1
    assert repo.count_sold_units_for_order("ORD-1") == 0
    assert all(d["status"] == "AVAILABLE" for d in docs)


def test_count_sold_units_for_order_is_scoped_to_the_order():
    repo = _repo(
        [
            _unit("U1", status="SOLD", order_id="ORD-1"),
            _unit("U2", status="SOLD", order_id="ORD-2"),
            _unit("U3", status="AVAILABLE"),
        ]
    )
    assert repo.count_sold_units_for_order("ORD-1") == 1
    assert repo.count_sold_units_for_order("ORD-2") == 1
    assert repo.count_sold_units_for_order("") == 0


# ===========================================================================
# PANEL MUST-FIX 5 -- release the line's OWN serial, not an arbitrary one.
# ===========================================================================


def test_release_targets_the_exact_named_unit():
    docs = [
        _unit("U-SCANNED", status="SOLD", order_id="ORD-1"),
        _unit("U-OTHER", status="SOLD", order_id="ORD-1"),
    ]
    repo = _repo(docs)

    freed, incomplete = repo.release_sold_units_for_order(
        "ORD-1", stock_id="U-OTHER"
    )

    assert freed == ["U-OTHER"] and incomplete is False
    by_id = {d["stock_id"]: d for d in docs}
    assert by_id["U-OTHER"]["status"] == "AVAILABLE"
    assert by_id["U-SCANNED"]["status"] == "SOLD"   # NOT an arbitrary pick


def test_release_by_stock_id_never_touches_another_orders_unit():
    docs = [_unit("U1", status="SOLD", order_id="ORD-OTHER")]
    repo = _repo(docs)
    freed, _ = repo.release_sold_units_for_order("ORD-1", stock_id="U1")
    assert freed == []
    assert docs[0]["status"] == "SOLD" and docs[0]["order_id"] == "ORD-OTHER"


# ===========================================================================
# FOLD-IN -- a legacy lowercase status must not become silently unsellable.
# ===========================================================================


@pytest.mark.parametrize("status", ["AVAILABLE", "available", "Available"])
def test_mark_sold_accepts_case_variants_of_available(status):
    docs = [_unit("U1", status=status)]
    repo = _repo(docs)
    assert repo.mark_sold("U1", "ORD-1") is True
    assert docs[0]["status"] == "SOLD"
