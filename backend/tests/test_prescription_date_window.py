# ============================================================================
# One field, two stored shapes -- and a bound that has to match both
# ============================================================================
# `prescription_date` is written in TWO different string shapes by two
# different doors:
#
#   * POST /prescriptions writes a full ISO datetime WITH a real time
#     ('2026-06-18T12:05:26.552211' -- verified against production), because
#     the pydantic field is a datetime and the door calls .isoformat().
#   * marketing.py writes a bare 'YYYY-MM-DD' via ist_date_str().
#
# ISO-8601 is lexicographically ordered, so a string compare IS a correct date
# compare -- but only if the bounds carry no time component. The two failure
# modes this file pins:
#
#   lower bound 'YYYY-MM-DDT00:00:00'  drops every BARE-DATE row on the from
#                                      day, because a shorter string that is a
#                                      prefix sorts BEFORE the longer one:
#                                      '2026-06-18' < '2026-06-18T00:00:00'.
#   upper bound '$lte YYYY-MM-DD'      drops every FULL-DATETIME row on the to
#                                      day, because '2026-06-18T12:05' is
#                                      greater than '2026-06-18'.
#
# Both edges are silent: the screen simply shows fewer prescriptions than
# exist, on exactly the days the operator asked about.

from __future__ import annotations

import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from database.repositories.prescription_repository import (  # noqa: E402
    PrescriptionRepository,
)

FROM = date(2026, 6, 18)
TO = date(2026, 6, 20)

# One row per shape, on each edge of the window, plus a row just outside it.
ROWS = {
    "bare-on-from": "2026-06-18",
    "stamp-on-from": "2026-06-18T12:05:26.552211",
    "bare-mid": "2026-06-19",
    "stamp-on-to": "2026-06-20T23:14:02.100000",
    "bare-on-to": "2026-06-20",
    "stamp-next-day": "2026-06-21T00:00:01.000000",
    "bare-next-day": "2026-06-21",
}


def _matches(value: str, window: dict) -> bool:
    """Mongo's string comparison for the operators this filter uses.

    Deliberately a plain lexicographic compare -- that is exactly what Mongo
    does for two BSON strings, so a test that models it faithfully is testing
    the real bound rather than a friendlier version of it.
    """
    if "$gte" in window and not value >= window["$gte"]:
        return False
    if "$gt" in window and not value > window["$gt"]:
        return False
    if "$lte" in window and not value <= window["$lte"]:
        return False
    if "$lt" in window and not value < window["$lt"]:
        return False
    return True


def _selected(window: dict) -> set:
    return {name for name, v in ROWS.items() if _matches(v, window)}


def test_the_window_admits_both_stored_shapes_on_both_edges():
    """The whole point: neither shape is dropped at either boundary."""
    got = _selected(PrescriptionRepository._clinical_date_filter(FROM, TO))
    assert got == {
        "bare-on-from",
        "stamp-on-from",
        "bare-mid",
        "stamp-on-to",
        "bare-on-to",
    }, got


def test_the_day_after_the_window_is_excluded():
    """The upper bound is exclusive-at-next-day, not open-ended."""
    got = _selected(PrescriptionRepository._clinical_date_filter(FROM, TO))
    assert "stamp-next-day" not in got
    assert "bare-next-day" not in got


def test_a_midnight_lower_bound_would_have_dropped_the_bare_rows():
    """Pins the exact defect the fix removes, so nobody reintroduces it.

    This is the bound the code carried: datetime.combine(from, min.time()).
    It looks harmless and silently loses a whole class of row.
    """
    broken = {"$gte": FROM.isoformat() + "T00:00:00", "$lt": "2026-06-21"}
    assert "bare-on-from" not in _selected(broken), (
        "a T00:00:00 lower bound is expected to drop bare-date rows -- if this "
        "now passes, the lexicographic premise of this whole filter changed"
    )
    assert "stamp-on-from" in _selected(broken)


def test_an_lte_bare_upper_bound_would_have_dropped_the_stamped_rows():
    """The mirror-image defect, on the other edge."""
    broken = {"$gte": FROM.isoformat(), "$lte": TO.isoformat()}
    assert "stamp-on-to" not in _selected(broken)
    assert "bare-on-to" in _selected(broken)


def test_open_ended_windows_are_still_open():
    """Only the supplied side is bounded -- a caller passing one date must not
    silently acquire the other bound."""
    lower_only = PrescriptionRepository._clinical_date_filter(FROM, None)
    assert set(lower_only) == {"$gte"}
    assert "stamp-next-day" in _selected(lower_only)

    upper_only = PrescriptionRepository._clinical_date_filter(None, TO)
    assert set(upper_only) == {"$lt"}
    assert "bare-on-from" in _selected(upper_only)

    assert PrescriptionRepository._clinical_date_filter(None, None) == {}


def test_every_call_site_uses_the_shared_builder():
    """The rule was implemented THREE times and all three disagreed.

    find_by_optometrist and get_optometrist_stats both passed raw DATETIMES at
    this string field, so one returned an empty list and the other aggregated
    an empty set -- silently, for every optometrist, forever. Guard the
    DELETION of the copies, not just the fix: assert the class builds no date
    bound of its own anywhere.
    """
    import inspect

    src = inspect.getsource(PrescriptionRepository)
    assert "datetime.combine" not in src, (
        "a prescription_date bound is being built by hand again -- route it "
        "through _clinical_date_filter instead"
    )
    for fn in ("find_by_optometrist", "find_by_store", "get_optometrist_stats"):
        body = src[src.index("def " + fn):]
        nxt = body.find(os.linesep.join(["", "    def "]), 1)
        if nxt == -1:
            nxt = body.find("\n    def ", 1)
        body = body[:nxt] if nxt != -1 else body
        assert "_clinical_date_filter" in body, f"{fn} does not use the shared builder"
