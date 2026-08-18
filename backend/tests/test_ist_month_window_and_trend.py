"""BUG-104 round 2: the IST fixes that go in OPPOSITE directions.

Three subjects, and the whole point of this file is that they are NOT the same
kind of fix:

  1 payout._month_window            -- a BOUND. Compared against stored
    (MONEY)                            naive-UTC `created_at`, so IST midnight
                                       on the 1st IS 18:30 UTC on the last day
                                       of the previous month: the bound moves
                                       BACKWARD (ist_day_start_utc).
  2 reports._daily_trend            -- a VALUE. Derived FROM a stored instant
                                       and read off a chart, so it moves
                                       FORWARD +5:30 (ist_date_str).
  3 reports._credit_note_date_ist   -- a VALUE whose source is a naive-UTC ISO
                                       STRING, which ist_date_str passes
                                       through unshifted by design: parse
                                       first, THEN shift.

Every assertion reads a RETURNED PAYLOAD (rupee totals, chart rows, the CDNR
row), never a log line. Every shifted case is paired with a POSITIVE CONTROL --
an ordinary afternoon whose answer must NOT move -- because without one a
"shift everything" implementation passes every test here.

No emoji (Windows cp1252).
"""

from __future__ import annotations

import os
import sys
from datetime import date, datetime

os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("MONGODB_URI", "")
os.environ.setdefault("DISPATCH_MODE", "off")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import payout as payout_mod  # noqa: E402
from api.routers import reports as reports_mod  # noqa: E402
from api.utils.ist import ist_date_str, ist_day_start_utc  # noqa: E402


# IST midnight is 18:30 UTC the day before. These are the two instants the
# whole file is built from, in the frame `created_at` is actually stored in.
SMALL_HOURS_1ST = datetime(2026, 4, 30, 22, 30, 0)   # = 1-May-2026 04:00 IST
AFTERNOON_30TH = datetime(2026, 4, 30, 11, 0, 0)     # = 30-Apr-2026 16:30 IST
AFTERNOON_2ND = datetime(2026, 5, 2, 11, 0, 0)       # = 2-May-2026 16:30 IST
FY_EVE = datetime(2026, 3, 31, 20, 0, 0)             # = 1-Apr-2026 01:30 IST


# ===========================================================================
# A strict fake. The payout suite's existing FakeCollection silently IGNORES
# `$lt` -- under it every order matches every window and this whole file would
# pass with the defect live. This one implements $gte/$lt/$in/$nin honestly and
# RAISES on any operator it does not understand, so it can never quietly agree.
# ===========================================================================


class _StrictColl:
    def __init__(self, docs=None):
        self.docs = [dict(d) for d in (docs or [])]

    @staticmethod
    def _match(doc, flt):
        for key, expected in (flt or {}).items():
            actual = doc.get(key)
            if isinstance(expected, dict):
                for op, val in expected.items():
                    if op == "$gte":
                        ok = actual is not None and actual >= val
                    elif op == "$lt":
                        ok = actual is not None and actual < val
                    elif op == "$lte":
                        ok = actual is not None and actual <= val
                    elif op == "$in":
                        ok = actual in val
                    elif op == "$nin":
                        ok = actual not in val
                    else:
                        raise AssertionError(
                            "strict fake does not implement %r -- implement it "
                            "rather than let the filter pass by accident" % op
                        )
                    if not ok:
                        return False
            elif actual != expected:
                return False
        return True

    def find(self, flt=None, *_a, **_k):
        return [dict(d) for d in self.docs if self._match(d, flt)]

    def aggregate(self, pipeline):
        rows = list(self.docs)
        for stage in pipeline:
            if "$match" in stage:
                rows = [r for r in rows if self._match(r, stage["$match"])]
            elif "$group" in stage:
                grp = stage["$group"]
                assert grp.get("_id") is None, "strict fake only groups on None"
                out = {"_id": None}
                for field, spec in grp.items():
                    if field == "_id":
                        continue
                    src = spec["$sum"].lstrip("$")
                    out[field] = sum(float(r.get(src) or 0) for r in rows)
                rows = [out]
            else:
                raise AssertionError("strict fake got unknown stage %r" % stage)
        return iter(rows)


class _StrictDB:
    is_connected = True

    def __init__(self, **colls):
        self._c = {k: _StrictColl(v) for k, v in colls.items()}

    def get_collection(self, name):
        return self._c.setdefault(name, _StrictColl([]))

    def __getitem__(self, name):
        return self.get_collection(name)


class _NoOrders:
    def find_many(self, *_a, **_k):
        return []


def _order(created_at, amount, oid):
    return {
        "order_id": oid,
        "store_id": "ZZ-IST-STORE",
        "status": "COMPLETED",
        "created_at": created_at,
        "grand_total": float(amount),
        "total_discount": 0.0,
    }


# ===========================================================================
# 1. THE BOUND -- payout._month_window
# ===========================================================================


def test_month_window_start_is_ist_midnight_expressed_in_the_stored_utc_frame():
    """IST 1-May-2026 00:00 IS 30-Apr-2026 18:30 UTC. The bound moves BACK."""
    start_dt, next_first, _, _ = payout_mod._month_window(2026, 5)
    assert start_dt == datetime(2026, 4, 30, 18, 30, 0)
    assert next_first == datetime(2026, 5, 31, 18, 30, 0)
    assert start_dt.tzinfo is None and next_first.tzinfo is None


def test_month_window_bound_agrees_with_the_shared_ist_helper():
    """The other side of this bound is `created_at`; the helper that puts a
    calendar day into that frame is ist_day_start_utc. Same answer, or the two
    halves of BUG-104 have drifted apart."""
    start_dt, next_first, _, _ = payout_mod._month_window(2026, 5)
    assert start_dt == ist_day_start_utc(date(2026, 5, 1))
    assert next_first == ist_day_start_utc(date(2026, 6, 1))


def test_month_window_bound_is_NOT_naive_local_midnight_the_defect():
    """Pins the exact defect: a bare datetime(year, month, 1)."""
    start_dt, _, _, _ = payout_mod._month_window(2026, 5)
    assert start_dt != datetime(2026, 5, 1, 0, 0, 0)


def test_month_window_december_rolls_the_year_and_still_moves_backward():
    start_dt, next_first, _, _ = payout_mod._month_window(2026, 12)
    assert start_dt == datetime(2026, 11, 30, 18, 30, 0)
    assert next_first == datetime(2026, 12, 31, 18, 30, 0)


def test_consecutive_months_tile_exactly_no_gap_no_overlap():
    """BOTH ends move together or one month gains what the other loses."""
    for year, month in ((2026, 1), (2026, 4), (2026, 11), (2026, 12)):
        nxt_y, nxt_m = (year + 1, 1) if month == 12 else (year, month + 1)
        assert payout_mod._month_window(year, month)[1] == (
            payout_mod._month_window(nxt_y, nxt_m)[0]
        ), "month %s-%s does not tile with the next" % (year, month)


def test_month_window_labels_stay_plain_ist_calendar_labels():
    """df/dt feed points_log.date_str -- an IST business day a human typed.
    They are LABELS, not bounds; shifting them would drop the 1st of every
    month out of MTD scoring."""
    _, _, df, dt = payout_mod._month_window(2026, 5)
    assert (df, dt) == ("2026-05-01", "2026-05-31")
    _, _, df2, dt2 = payout_mod._month_window(2026, 2)
    assert (df2, dt2) == ("2026-02-01", "2026-02-28")
    _, _, df3, dt3 = payout_mod._month_window(2026, 12)
    assert (df3, dt3) == ("2026-12-01", "2026-12-31")


# --- the money itself -------------------------------------------------------


def _sales_for(month_orders, year, month):
    """Drive the REAL _aggregate_sales against the strict fake and return the
    rupee payload the payout pool is computed from."""
    db = _StrictDB(orders=month_orders)
    real = payout_mod.get_db
    payout_mod.get_db = lambda: db
    try:
        return payout_mod._aggregate_sales("ZZ-IST-STORE", year, month)
    finally:
        payout_mod.get_db = real


ORDERS = [
    _order(SMALL_HOURS_1ST, 10000, "ZZ-1"),   # 1-May IST, 30-Apr UTC
    _order(AFTERNOON_30TH, 700, "ZZ-2"),      # 30-Apr in BOTH frames
    _order(AFTERNOON_2ND, 300, "ZZ-3"),       # 2-May in BOTH frames
]


def test_small_hours_order_on_the_1st_counts_into_ITS_OWN_month():
    """The money claim. 10,000 rupees placed 1-May 04:00 IST must size MAY's
    incentive pool, not April's."""
    assert _sales_for(ORDERS, 2026, 5)["sales"] == 10300.0


def test_small_hours_order_on_the_1st_is_NOT_in_the_previous_month():
    assert _sales_for(ORDERS, 2026, 4)["sales"] == 700.0


def test_afternoon_orders_are_untouched_positive_control():
    """Without this, a window shifted the WRONG way (or by any amount) still
    passes the two tests above by dragging everything with it."""
    only_afternoons = [ORDERS[1], ORDERS[2]]
    assert _sales_for(only_afternoons, 2026, 4)["sales"] == 700.0
    assert _sales_for(only_afternoons, 2026, 5)["sales"] == 300.0


def test_every_rupee_lands_in_exactly_one_month_conservation():
    """Sum over the two months == the total. A bound fixed on one end only
    either double-counts the order or drops it."""
    total = sum(o["grand_total"] for o in ORDERS)
    april = _sales_for(ORDERS, 2026, 4)["sales"]
    may = _sales_for(ORDERS, 2026, 5)["sales"]
    assert april + may == total


def test_the_1_april_financial_year_boundary_order():
    """Production holds one order in this shape. 1-Apr 01:30 IST must pay in
    APRIL (new FY), not March."""
    fy = [
        _order(FY_EVE, 5000, "ZZ-FY"),
        _order(datetime(2026, 3, 31, 11, 0), 40, "ZZ-M"),
    ]
    assert _sales_for(fy, 2026, 4)["sales"] == 5000.0
    assert _sales_for(fy, 2026, 3)["sales"] == 40.0


# ===========================================================================
# 2. THE VALUE -- reports._daily_trend
# ===========================================================================


def _trend(orders):
    return {r["date"]: r for r in reports_mod._daily_trend(orders)}


def test_daily_trend_buckets_a_small_hours_order_on_its_IST_day():
    rows = _trend([_order(SMALL_HOURS_1ST, 10000, "ZZ-1")])
    assert "2026-05-01" in rows
    assert "2026-04-30" not in rows
    assert rows["2026-05-01"]["sales"] == 10000.0
    assert rows["2026-05-01"]["orders"] == 1


def test_daily_trend_leaves_an_afternoon_order_alone_positive_control():
    rows = _trend([_order(AFTERNOON_30TH, 700, "ZZ-2")])
    assert list(rows) == ["2026-04-30"]
    assert rows["2026-04-30"]["sales"] == 700.0


def test_daily_trend_boundary_pair_one_second_apart_lands_on_two_days():
    rows = _trend(
        [
            _order(datetime(2026, 4, 30, 18, 29, 59), 11, "ZZ-A"),
            _order(datetime(2026, 4, 30, 18, 30, 0), 22, "ZZ-B"),
        ]
    )
    assert sorted(rows) == ["2026-04-30", "2026-05-01"]
    assert rows["2026-04-30"]["sales"] == 11.0
    assert rows["2026-05-01"]["sales"] == 22.0


def test_daily_trend_still_prefers_an_explicit_date_str_when_a_caller_sets_one():
    """The preference branch is dead in production (0 of 934 orders carry
    date_str) but is KEPT and must keep working -- a supplied date_str is an
    IST business-day label, already right, and must not be re-shifted."""
    o = _order(SMALL_HOURS_1ST, 500, "ZZ-DS")
    o["date_str"] = "2026-01-09"
    rows = _trend([o])
    assert list(rows) == ["2026-01-09"]


def test_daily_trend_legacy_iso_string_created_at_is_not_guessed_at():
    """A bare string carries no frame; ist_date_str passes it through. Pinned
    so nobody 'improves' it into a silent shift of unknown data."""
    o = _order("2026-04-30T22:30:00", 900, "ZZ-STR")
    assert list(_trend([o])) == ["2026-04-30"]


def test_daily_trend_skips_an_order_with_no_usable_date():
    assert reports_mod._daily_trend([_order(None, 100, "ZZ-NONE")]) == []


def test_daily_trend_uses_the_same_helper_as_the_dashboard_in_this_file():
    """/dashboard already answered this question with ist_date_str; the trend
    must not answer it a second, different way."""
    assert reports_mod.ist_date_str is ist_date_str


# ===========================================================================
# 3. THE VALUE ist_date_str CANNOT FIX -- a naive-UTC ISO STRING
# ===========================================================================


def test_ist_date_str_alone_does_NOT_move_the_stored_string_by_design():
    """Establishes WHY the wrapper exists. If this ever starts shifting, the
    helper has begun guessing at frames it cannot know."""
    assert ist_date_str("2026-04-30T22:30:00") == "2026-04-30"


def test_credit_note_date_parses_the_naive_utc_string_then_shifts():
    assert reports_mod._credit_note_date_ist("2026-04-30T22:30:00") == "2026-05-01"


def test_credit_note_date_afternoon_string_is_unchanged_positive_control():
    assert reports_mod._credit_note_date_ist("2026-04-30T11:00:00") == "2026-04-30"


def test_credit_note_date_boundary_pair():
    assert reports_mod._credit_note_date_ist("2026-04-30T18:29:59") == "2026-04-30"
    assert reports_mod._credit_note_date_ist("2026-04-30T18:30:00") == "2026-05-01"


def test_credit_note_date_1_april_does_not_print_a_prior_fy_date():
    assert reports_mod._credit_note_date_ist("2026-03-31T20:00:00") == "2026-04-01"
    # positive control on the same boundary
    assert reports_mod._credit_note_date_ist("2026-03-31T11:00:00") == "2026-03-31"


def test_credit_note_date_handles_the_shapes_the_writers_actually_emit():
    """make_entry writes datetime.now().isoformat() (microseconds, no offset);
    shopify_ingest writes _to_naive_utc(...).isoformat(). Both naive-UTC."""
    got = reports_mod._credit_note_date_ist("2026-04-30T22:30:00.123456")
    assert got == "2026-05-01"
    assert reports_mod._credit_note_date_ist("2026-04-30T22:30:00Z") == "2026-05-01"
    stored_dt = datetime(2026, 4, 30, 22, 30)
    assert reports_mod._credit_note_date_ist(stored_dt) == "2026-05-01"


def test_credit_note_date_fails_soft_on_junk_and_on_missing():
    # Unparseable -> the pre-BUG-104 behaviour, its own first 10 characters.
    assert reports_mod._credit_note_date_ist("not-a-date-at-all") == "not-a-date"
    assert reports_mod._credit_note_date_ist(None) == ""
    assert reports_mod._credit_note_date_ist("") == ""


def test_credit_note_date_leaves_a_bare_date_string_alone():
    """A 'YYYY-MM-DD' carries no instant, so there is nothing to shift."""
    assert reports_mod._credit_note_date_ist("2026-04-30") == "2026-04-30"


def test_gstr1_cdnr_row_carries_the_ist_credit_note_date():
    """End-to-end on the RETURNED PAYLOAD: the CDNR row that goes to the GSTN
    portal export. The row is correctly SELECTED into May (the month window is
    a bound and already moves backward); this pins that it is also correctly
    DATED."""
    ledger = [
        {
            "entry_id": "ZZ-CN-1",
            "customer_id": "ZZ-CUST",
            "store_id": "ZZ-IST-STORE",
            "type": "ISSUED",
            "ref": "RET-ZZ-0001",
            "amount": 1180.0,
            "gst_rate": 18,
            "created_at": SMALL_HOURS_1ST.isoformat(),
        },
        {
            "entry_id": "ZZ-CN-2",
            "customer_id": "ZZ-CUST",
            "store_id": "ZZ-IST-STORE",
            "type": "ISSUED",
            "ref": "RET-ZZ-0002",
            "amount": 590.0,
            "gst_rate": 18,
            "created_at": AFTERNOON_2ND.isoformat(),
        },
    ]
    db = _StrictDB(credit_note_ledger=ledger)
    real_raw = reports_mod._get_raw_db
    real_repo = reports_mod.get_order_repository
    reports_mod._get_raw_db = lambda: db
    reports_mod.get_order_repository = lambda: _NoOrders()
    try:
        out = reports_mod._compute_gstr1("2026-05", "ZZ-IST-STORE")
    finally:
        reports_mod._get_raw_db = real_raw
        reports_mod.get_order_repository = real_repo

    by_ref = {r["refReference"]: r for r in out["cdnr"]}
    assert set(by_ref) == {"RET-ZZ-0001", "RET-ZZ-0002"}
    assert by_ref["RET-ZZ-0001"]["creditNoteDate"] == "2026-05-01"
    assert by_ref["RET-ZZ-0001"]["creditNoteDate"] != "2026-04-30"
    # POSITIVE CONTROL: the afternoon note must not move.
    assert by_ref["RET-ZZ-0002"]["creditNoteDate"] == "2026-05-02"


# ===========================================================================
# 4. SWEEP -- the one site that was trivially safe and clearly a VALUE
# ===========================================================================


def _reorder_notes(last_order):
    """Drive the REAL handle_reorder and return the follow-up note text that
    goes out with the WhatsApp reply."""
    import asyncio

    from api.services import whatsapp_intents as wi

    captured = {}
    real_last = wi._get_last_cl_order
    real_fu = wi._create_follow_up
    wi._get_last_cl_order = lambda _c: last_order
    wi._create_follow_up = lambda _c, _t, notes, _s: captured.update(notes=notes)
    try:
        asyncio.run(
            wi.handle_reorder("919876543210", {"name": "ZZ Customer"}, "ZZ-IST-STORE")
        )
    finally:
        wi._get_last_cl_order = real_last
        wi._create_follow_up = real_fu
    return captured["notes"]


def test_whatsapp_reorder_note_quotes_the_customers_own_ist_day():
    """The date goes out on WhatsApp to the CUSTOMER, so it is an outbound
    VALUE. No bound and no persisted key on this path."""
    notes = _reorder_notes({
        "order_id": "ZZ-CL-1",
        "order_number": "BV/26-27/0042",
        "created_at": SMALL_HOURS_1ST,
    })
    assert "2026-05-01" in notes
    assert "2026-04-30" not in notes


def test_whatsapp_reorder_note_afternoon_order_unchanged_positive_control():
    notes = _reorder_notes({
        "order_number": "BV/26-27/0043",
        "created_at": AFTERNOON_30TH,
    })
    assert "2026-04-30" in notes
