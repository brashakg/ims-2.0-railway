"""BUG-104 final round: chart keys, and the date a PATIENT reads.

Same two rules as test_ist_fy_and_gstr1_dates.py:

  VALUE  a day derived FROM a stored instant and then displayed / sent out
         moves FORWARD (+5:30) -> ist_date_str.
  BOUND  a bound COMPARED AGAINST stored instants moves BACKWARD
         -> ist_day_start_utc.

The subject here is the third case the earlier rounds kept half-doing: a JOIN,
where the bucket keys come from stored instants and the axis labels come from
the clock. Both sides have to end up in the same frame -- move the buckets and
leave the labels and the revenue simply disappears off the chart.

The clock is FROZEN at an instant inside the 00:00-05:30 IST window
(2026-06-30 20:00 UTC == 1-Jul-2026 01:30 IST), so these tests do not depend
on the day or the hour they run.

Every assertion reads a RETURNED PAYLOAD. Every shifted case is paired with a
POSITIVE CONTROL. No emoji (Windows cp1252).
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import date, datetime, timedelta

os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("MONGODB_URI", "")
os.environ.setdefault("DISPATCH_MODE", "off")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import analytics as analytics_mod  # noqa: E402
from api.routers import analytics_v2 as v2_mod  # noqa: E402
from api.routers import marketing as marketing_mod  # noqa: E402

STORE = "ZZ-IST-STORE"

# 2026-06-30 20:00 UTC IS 1-Jul-2026 01:30 IST. Frozen "now" for every
# endpoint below, so the UTC day and the IST day of "today" differ and the
# defect is reachable at any wall-clock time the suite happens to run.
FROZEN_NOW = datetime(2026, 6, 30, 20, 0, 0)


class _AnyDatetimeIsMine(type):
    """Metaclass so ``isinstance(a_real_datetime, _FrozenDatetime)`` is True.

    The routers shadow the module-global name ``datetime`` for BOTH
    ``datetime.now()`` and ``isinstance(value, datetime)`` type checks. Freezing
    the clock by swapping in a subclass would otherwise make every real stored
    datetime in the fixtures fail those checks and be silently skipped -- a
    fixture that makes the test agree with anything."""

    def __instancecheck__(cls, obj):
        return isinstance(obj, datetime)


def _frozen(base=FROZEN_NOW):
    class _FrozenDatetime(datetime, metaclass=_AnyDatetimeIsMine):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return base
            return base.replace(tzinfo=tz)

        @classmethod
        def utcnow(cls):
            return base

    return _FrozenDatetime


class _PatchAttrs:
    def __init__(self, mod, **attrs):
        self.mod = mod
        self.attrs = attrs
        self._saved = {}

    def __enter__(self):
        for k, v in self.attrs.items():
            self._saved[k] = getattr(self.mod, k)
            setattr(self.mod, k, v)
        return self

    def __exit__(self, *_a):
        for k, v in self._saved.items():
            setattr(self.mod, k, v)
        return False


# ===========================================================================
# C. THE DATE A CUSTOMER READS -- the Rx expiry alert
# ===========================================================================


class _RxColl:
    def __init__(self, docs):
        self.docs = [dict(d) for d in docs]

    def find(self, *_a, **_k):
        return self

    def limit(self, *_a):
        return [dict(d) for d in self.docs]

    def find_one(self, *_a, **_k):
        return None


class _RxDB:
    def __init__(self, rx):
        self._rx = _RxColl(rx)

    def get_collection(self, name):
        if name == "prescriptions":
            return self._rx
        return _RxColl([])


# The eye test was recorded 700 days before "now", at 20:00 UTC -- i.e. at
# 01:30 IST the NEXT morning. Two years' validity puts the expiry 30 days out,
# so the alert is in the 90-day window whatever the run date.
_TEST_DAY_UTC = (FROZEN_NOW - timedelta(days=700)).replace(
    hour=20, minute=0, second=0, microsecond=0
)
_TEST_DAY_AFTERNOON = _TEST_DAY_UTC.replace(hour=11)


def _rx_alerts(created_at):
    db = _RxDB(
        [
            {
                "prescription_id": "ZZ-RX-1",
                "customer_id": "ZZ-CUST",
                "store_id": STORE,
                "created_at": created_at,
                "patient_name": "ZZ Patient",
            }
        ]
    )
    with _PatchAttrs(
        marketing_mod,
        datetime=_frozen(),
        _get_db=lambda: db,
        validate_store_access=lambda s, u: STORE,
    ):
        return asyncio.run(
            marketing_mod.get_rx_expiry_alerts(store_id=STORE, current_user={})
        )


def _only_alert(out):
    rows = (out["urgent"] or []) + (out["soon"] or []) + (out["upcoming"] or [])
    assert len(rows) == 1, rows
    return rows[0]


def test_rx_alert_quotes_the_patients_own_ist_prescription_date():
    """VALUE rule, and the one on this list a real person acts on. An eye test
    recorded at 01:30 IST was quoted back to the patient with YESTERDAY's date
    -- 'your prescription dated 30 June' for a test taken on 1 July."""
    row = _only_alert(_rx_alerts(_TEST_DAY_UTC))
    ist_day = (_TEST_DAY_UTC + timedelta(hours=5, minutes=30)).date().isoformat()
    utc_day = _TEST_DAY_UTC.date().isoformat()
    assert ist_day != utc_day, "the fixture no longer straddles the IST boundary"
    assert row["prescription_date"] == ist_day
    assert row["prescription_date"] != utc_day


def test_rx_alert_expiry_date_moves_with_it():
    """The expiry is that same instant plus 730 days, so it was a day early
    too -- the patient was told to come back before a date that had not
    arrived."""
    row = _only_alert(_rx_alerts(_TEST_DAY_UTC))
    expiry_ist = (
        _TEST_DAY_UTC + timedelta(days=730, hours=5, minutes=30)
    ).date().isoformat()
    assert row["expiry_date"] == expiry_ist


def test_rx_alert_afternoon_test_keeps_its_own_date_positive_control():
    """A 16:30 IST eye test is the same calendar day in both frames and must
    not move. Without this, a 'shift everything' implementation passes."""
    row = _only_alert(_rx_alerts(_TEST_DAY_AFTERNOON))
    assert row["prescription_date"] == _TEST_DAY_AFTERNOON.date().isoformat()


def test_rx_alert_days_until_expiry_is_still_elapsed_time_not_a_calendar_day():
    """Deliberately NOT shifted: it is the gap between two values in the same
    frame, and it is what sorts the call list."""
    row = _only_alert(_rx_alerts(_TEST_DAY_UTC))
    assert row["days_until_expiry"] == 30


# ===========================================================================
# D. THE JOIN -- /analytics/revenue-trends
# ===========================================================================


def _order(created_at, amount, oid):
    return {
        "order_id": oid,
        "store_id": STORE,
        "status": "COMPLETED",
        "created_at": created_at,
        "total_amount": float(amount),
        "grand_total": float(amount),
    }


def _trends(orders, period="daily", days=30):
    """Drive the REAL endpoint with the order window stubbed out, so the
    assertions are about the KEYS and the LABELS, not about the fetch."""
    def _fetch(_repo, store_id=None, start=None, end=None):
        return [dict(o) for o in orders]

    with _PatchAttrs(
        analytics_mod,
        datetime=_frozen(),
        validate_store_access=lambda s, u: STORE,
        get_order_repository=lambda: object(),
        _fetch_orders_in_window=_fetch,
    ):
        return asyncio.run(
            analytics_mod.get_revenue_trends(
                current_user={"active_store_id": STORE},
                period=period,
                days=days,
                store_id=STORE,
            )
        )


# 2026-06-30 19:00 UTC == 1-Jul-2026 00:30 IST: the sale happened TODAY in IST
# and YESTERDAY in UTC, on the last day of the window.
LATE_SALE = FROZEN_NOW - timedelta(hours=1)
MID_WINDOW_AFTERNOON = (FROZEN_NOW - timedelta(days=10)).replace(hour=10)


def test_revenue_trend_bar_is_labelled_with_the_ist_day_of_the_sale():
    """VALUE rule on the bucket key."""
    out = _trends([_order(LATE_SALE, 5000, "ZZ-T1")])
    by_label = {row["label"]: row["value"] for row in out["data"]}
    assert by_label.get("2026-07-01") == 5000.0
    assert by_label.get("2026-06-30", 0.0) == 0.0


def test_revenue_trend_timeline_carries_a_label_for_that_ist_day():
    """The OTHER side of the join. This is the round-2 defect in miniature:
    move the bucket to the IST day and leave the axis on UTC days and the
    2026-07-01 bucket has no bar to sit on."""
    out = _trends([_order(LATE_SALE, 5000, "ZZ-T2")])
    labels = [row["label"] for row in out["data"]]
    assert "2026-07-01" in labels
    assert labels[-1] == "2026-07-01"


def test_revenue_trend_loses_no_money_off_the_end_of_the_chart():
    """Conservation. Every rupee handed in must appear somewhere on the
    returned series -- the property that fails the moment the two sides of the
    join stop sharing a frame."""
    orders = [
        _order(LATE_SALE, 5000, "ZZ-T3"),
        _order(MID_WINDOW_AFTERNOON, 700, "ZZ-T4"),
    ]
    out = _trends(orders)
    assert sum(row["value"] for row in out["data"]) == 5700.0


def test_revenue_trend_afternoon_sale_stays_on_its_own_bar_positive_control():
    out = _trends([_order(MID_WINDOW_AFTERNOON, 700, "ZZ-T5")])
    by_label = {row["label"]: row["value"] for row in out["data"]}
    assert by_label.get("2026-06-20") == 700.0
    assert by_label.get("2026-06-21", 0.0) == 0.0


def test_revenue_trend_monthly_mode_charts_the_sale_in_the_ist_month():
    out = _trends([_order(LATE_SALE, 5000, "ZZ-T6")], period="monthly")
    by_label = {row["label"]: row["value"] for row in out["data"]}
    assert by_label.get("2026-07") == 5000.0
    assert "2026-07" in [row["label"] for row in out["data"]]
    assert by_label.get("2026-06", 0.0) == 0.0


def test_revenue_trend_weekly_mode_uses_the_ist_week():
    """1-Jul-2026 is a Wednesday, so its IST week starts Monday 29-Jun. On the
    UTC day (Tue 30-Jun) the week start is the same Monday, so this test also
    proves the weekly branch did not get shifted by a whole week."""
    out = _trends([_order(LATE_SALE, 5000, "ZZ-T7")], period="weekly")
    by_label = {row["label"]: row["value"] for row in out["data"]}
    assert by_label.get("2026-06-29") == 5000.0


# ===========================================================================
# E. THE THIRD PRIVATE TWIN -- analytics_v2._day_key, triaged per call site
# ===========================================================================


def test_day_key_is_DELIBERATELY_still_the_raw_utc_day():
    """TRIPWIRE, the mirror of test_to_date_str_is_deliberately_NOT_ist_aware.

    _day_key keeps ONE caller: the demand-forecast trend split, whose keys are
    compared against `(now - 45 days).strftime(...)` off the same naive box
    clock and never leave the function. Both sides of THAT comparison are in
    the raw frame; converting one alone would move the boundary error instead
    of removing it. If this test goes red, someone has 'finished the job' and
    silently re-broken the one place the raw frame is correct."""
    assert v2_mod._day_key(datetime(2026, 6, 30, 20, 0)) == "2026-06-30"
    assert v2_mod._day_key(datetime(2026, 6, 30, 11, 0)) == "2026-06-30"
    assert v2_mod._day_key("2026-06-30T20:00:00") == "2026-06-30"
    assert v2_mod._day_key(None) == ""


def test_day_key_has_exactly_one_caller_left():
    """The other three call sites moved to ist_date_str. Pinned by count so a
    new caller has to make a conscious choice of frame."""
    import inspect

    src = inspect.getsource(v2_mod)
    body = src.split("def _day_key", 1)[1].split("\n\n\n", 1)[1]
    assert body.count("_day_key(") == 1, (
        "expected exactly one remaining _day_key call site; found %d -- decide "
        "whether the new one is a raw-frame comparison or a business day"
        % body.count("_day_key(")
    )


# --- dead stock: last_sold_date is read by a buyer -------------------------


class _V2Coll:
    def __init__(self, docs):
        self.docs = [dict(d) for d in docs]

    def find(self, flt=None, *_a, **_k):
        rng = ((flt or {}).get("created_at")) or {}
        rows = []
        for d in self.docs:
            ca = d.get("created_at")
            if "$gte" in rng and not (ca is not None and ca >= rng["$gte"]):
                continue
            if "$lte" in rng and not (ca is not None and ca <= rng["$lte"]):
                continue
            rows.append(dict(d))
        return _V2Cursor(rows)


class _V2Cursor:
    def __init__(self, rows):
        self.rows = rows

    def limit(self, *_a):
        return self.rows

    def sort(self, *_a):
        return self

    def __iter__(self):
        return iter(self.rows)


class _V2DB:
    def __init__(self, **colls):
        self._c = {k: _V2Coll(v) for k, v in colls.items()}

    def get_collection(self, name):
        return self._c.setdefault(name, _V2Coll([]))


def _dead_stock(order_created_at):
    db = _V2DB(
        products=[
            {
                "product_id": "ZZ-P1",
                "store_id": STORE,
                "name": "ZZ Frame",
                "quantity": 4,
                "cost_price": 500.0,
            }
        ],
        orders=[
            {
                "order_id": "ZZ-O1",
                "store_id": STORE,
                "created_at": order_created_at,
                "items": [{"product_id": "ZZ-P0"}],
            }
        ],
    )
    # The dead-stock fallback leg looks for orders touching the UNSOLD product,
    # which is how ZZ-P1 gets a last_sold_date at all.
    db._c["orders"].docs[0]["items"] = [{"product_id": "ZZ-P1"}]
    db._c["orders"].docs.append(
        {
            "order_id": "ZZ-O0",
            "store_id": STORE,
            "created_at": order_created_at,
            "items": [{"product_id": "ZZ-OTHER"}],
        }
    )
    with _PatchAttrs(
        v2_mod,
        datetime=_frozen(),
        ist_today=lambda: (FROZEN_NOW + timedelta(hours=5, minutes=30)).date(),
        _get_db=lambda: db,
        validate_store_access=lambda s, u: STORE,
    ):
        return asyncio.run(
            v2_mod.dead_stock(
                store_id=STORE,
                days_threshold=90,
                current_user={"roles": ["SUPERADMIN"], "active_store_id": STORE},
            )
        )


# 400 days before "now", at 20:00 UTC == 01:30 IST the next morning. Old enough
# that the product is genuinely dead stock.
OLD_SALE = (FROZEN_NOW - timedelta(days=400)).replace(
    hour=20, minute=0, second=0, microsecond=0
)
OLD_SALE_AFTERNOON = OLD_SALE.replace(hour=11)


def test_dead_stock_last_sold_date_is_the_ist_business_day():
    """VALUE rule. This date is what a buyer reads before choosing
    return-to-vendor over a clearance sale."""
    out = _dead_stock(OLD_SALE)
    rows = out["dead_stock"]
    assert rows, out
    ist_day = (OLD_SALE + timedelta(hours=5, minutes=30)).date().isoformat()
    assert ist_day != OLD_SALE.date().isoformat()
    assert rows[0]["last_sold_date"] == ist_day


def test_dead_stock_afternoon_sale_keeps_its_own_date_positive_control():
    out = _dead_stock(OLD_SALE_AFTERNOON)
    rows = out["dead_stock"]
    assert rows and rows[0]["last_sold_date"] == OLD_SALE_AFTERNOON.date().isoformat()


def test_dead_stock_days_since_last_sale_reads_the_same_clock_as_the_date():
    """The age is now IST-today minus the IST day of the sale. Leaving `now`
    on the UTC box clock while the day moved would put the two a day out of
    step -- and 365 / 180 are the thresholds that pick the suggestion."""
    out = _dead_stock(OLD_SALE)
    row = out["dead_stock"][0]
    last = date.fromisoformat(row["last_sold_date"])
    ist_today = (FROZEN_NOW + timedelta(hours=5, minutes=30)).date()
    assert row["days_since_last_sale"] == (ist_today - last).days


# --- staff anomalies: 'same day' must mean the same BUSINESS day -----------


def _anomalies(orders):
    db = _V2DB(orders=orders, customers=[])
    with _PatchAttrs(
        v2_mod,
        datetime=_frozen(),
        _get_db=lambda: db,
        validate_store_access=lambda s, u: STORE,
    ):
        return asyncio.run(
            v2_mod.anomaly_detection(
                store_id=STORE,
                date_from=None,
                date_to=None,
                current_user={"roles": ["SUPERADMIN"], "active_store_id": STORE},
            )
        )


def _staff_order(created_at, status, oid):
    return {
        "order_id": oid,
        "store_id": STORE,
        "created_at": created_at,
        "status": status,
        "sales_staff_id": "ZZ-EMP-1",
        "sales_staff_name": "ZZ Cashier",
        "total_amount": 1000.0,
        "discount_amount": 0.0,
    }


def test_staff_anomaly_groups_an_evening_and_a_small_hours_order_by_ist_day():
    """VALUE rule on a key that is ALSO a display value. The void-and-recreate
    detector asks 'same day'; on the UTC day the shop's late evening and the
    next morning's 00:00-05:30 IST trade sat in two different buckets, so a
    real pair could slip the >= 3 threshold -- and the date printed against a
    named member of staff could be the wrong one."""
    day = FROZEN_NOW.replace(hour=19, minute=0)  # 1-Jul 00:30 IST
    orders = [
        _staff_order(day, "voided", "ZZ-A1"),
        _staff_order(day + timedelta(minutes=10), "COMPLETED", "ZZ-A2"),
        _staff_order(day + timedelta(minutes=20), "COMPLETED", "ZZ-A3"),
    ]
    out = _anomalies(orders)
    rows = [a for a in out["anomalies"] if a["type"] == "void_and_recreate"]
    assert rows, out["anomalies"]
    assert rows[0]["date"] == "2026-07-01"
    assert rows[0]["date"] != "2026-06-30"


def test_staff_anomaly_afternoon_orders_keep_their_own_day_positive_control():
    day = (FROZEN_NOW - timedelta(days=5)).replace(hour=11, minute=0)
    orders = [
        _staff_order(day, "voided", "ZZ-A4"),
        _staff_order(day + timedelta(minutes=10), "COMPLETED", "ZZ-A5"),
        _staff_order(day + timedelta(minutes=20), "COMPLETED", "ZZ-A6"),
    ]
    out = _anomalies(orders)
    rows = [a for a in out["anomalies"] if a["type"] == "void_and_recreate"]
    assert rows and rows[0]["date"] == day.date().isoformat()
