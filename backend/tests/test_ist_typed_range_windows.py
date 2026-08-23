"""BUG-104 round 3: operator-typed date ranges must bound at IST day starts.

THE CLASS (one class, one fix shape): an endpoint takes from_date/to_date as
IST calendar days the operator typed, and filters orders/audit rows whose
created_at/timestamp is a stored naive-UTC instant. Bounding at naive UTC
midnight starts the window 5h30m LATE: every 00:00-05:30-IST event on the
first requested day is dropped, and the same band after the last day is
wrongly claimed. The fix is the BOUND rule everywhere:

    from_dt = ist_day_start_utc(from_date)
    to_dt   = ist_day_start_utc(to_date + 1 day) - 1 microsecond

The four verifier-named sites, plus the round-3 sweep's own finds (six more
reports.py windows, conversion analytics, the Activity Log filter and its
today counters, and the MEGAPHONE status counters):

  reports.py  /sales/by-salesperson  /sales/by-category  /finance/gst
              /sales/comparison (both windows + the seam)
              /profit/by-category  /profit/by-store  /discount/analysis
              /staff/ranking  /finance/expense-vs-revenue
              /inventory/brand-sellthrough
  conversion_analytics.py  order-attribution fetch window
  settings.py  _audit_time_filter (shape-tested in test_audit_time_filter.py)
               + /audit-logs/summary today counters
  megaphone.py run() "today" counters (ALIGN verdict; taskmaster's PO dedupe
               key is TABLED -- see the code comments at both sites)

Per fixed window the deciding trio: a first-day 00:30-IST event is IN, a
day-after 00:30-IST event is OUT, and an afternoon event (same day both
frames) is unchanged -- the positive control. Plus the named AGREEMENT tests:
by-salesperson vs staff/ranking vs both leaderboard twins vs the payout month
aggregate (one roster, row 37's defect), and /finance/gst vs
/finance/gst-summary (one June tax total).

The repo/collection fakes TYPE-BRACKET like Mongo (a string bound never
matches a BSON datetime and vice versa) and enforce the actual filter, so no
test can pass by the fake being generous. All clocks frozen; nothing
calendar-dependent. No emoji (Windows cp1252).
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import date, datetime, timedelta, timezone

os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("MONGODB_URI", "")
os.environ.setdefault("DISPATCH_MODE", "off")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import api.dependencies as deps_mod  # noqa: E402
from agents.implementations import megaphone as megaphone_mod  # noqa: E402
from api.routers import analytics_v2 as analytics_v2_mod  # noqa: E402
from api.routers import finance as finance_mod  # noqa: E402
from api.routers import payout as payout_mod  # noqa: E402
from api.routers import payroll as payroll_mod  # noqa: E402
from api.routers import reports as reports_mod  # noqa: E402
from api.routers import settings as settings_mod  # noqa: E402
from api.services import conversion_analytics as conv_mod  # noqa: E402
from api.utils.ist import IST  # noqa: E402

STORE = "ZZ-IST-STORE"

# The June-range cast (typed range 2026-06-01 .. 2026-06-30):
# 2026-05-31 19:00 UTC IS 00:30 IST 1 June -- first-day early, MUST BE IN.
J_EARLY = datetime(2026, 5, 31, 19, 0, 0)
# 2026-06-30 19:00 UTC IS 00:30 IST 1 July -- day-after early, MUST BE OUT.
J_AFTER = datetime(2026, 6, 30, 19, 0, 0)
# 2026-06-15 11:00 UTC IS 16:30 IST 15 June -- afternoon positive control.
J_NOON = datetime(2026, 6, 15, 11, 0, 0)

JUNE_1 = date(2026, 6, 1)
JUNE_30 = date(2026, 6, 30)

ADMIN = {"user_id": "ZZ-U1", "roles": ["ADMIN"], "active_store_id": STORE}
SUPER = {"user_id": "ZZ-U0", "roles": ["SUPERADMIN"], "active_store_id": STORE}


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


# ---------------------------------------------------------------------------
# STRICT fakes: type-bracketing filter enforcement (Mongo never compares a
# string bound against a BSON datetime), real $match/$group aggregation.
# ---------------------------------------------------------------------------


def _dig(doc, dotted):
    cur = doc
    for part in dotted.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


class _Cursor:
    def __init__(self, rows):
        self.rows = rows

    def limit(self, *_a):
        return self

    def sort(self, *_a, **_k):
        return self

    def __iter__(self):
        return iter(self.rows)


class _TypeBracketColl:
    def __init__(self, docs):
        self.docs = [dict(d) for d in docs]

    @staticmethod
    def _cmp_ok(actual, val, op):
        if isinstance(actual, str) != isinstance(val, str):
            return False  # different BSON types never compare in a range query
        if op == "$gte":
            return actual >= val
        if op == "$gt":
            return actual > val
        if op == "$lte":
            return actual <= val
        if op == "$lt":
            return actual < val
        raise AssertionError("fake does not implement %r" % op)

    def _match(self, doc, flt):
        for key, expected in (flt or {}).items():
            if key == "$or":
                if not any(self._match(doc, arm) for arm in expected):
                    return False
                continue
            actual = _dig(doc, key)
            if isinstance(expected, dict) and any(
                str(k).startswith("$") for k in expected
            ):
                for op, val in expected.items():
                    if op == "$in":
                        ok = actual in val
                    elif op == "$nin":
                        ok = actual not in val
                    elif op == "$ne":
                        ok = actual != val
                    else:
                        ok = actual is not None and self._cmp_ok(actual, val, op)
                    if not ok:
                        return False
            elif actual != expected:
                return False
        return True

    def find(self, flt=None, *_a, **_k):
        return _Cursor([dict(d) for d in self.docs if self._match(d, flt)])

    def find_one(self, flt=None, *_a, **_k):
        rows = list(self.find(flt))
        return rows[0] if rows else None

    def count_documents(self, flt=None, *_a, **_k):
        return len(list(self.find(flt)))

    def aggregate(self, pipeline, *_a, **_k):
        rows = [dict(d) for d in self.docs]
        for stage in pipeline:
            (op, spec), = stage.items()
            if op == "$match":
                rows = [r for r in rows if self._match(r, spec)]
            elif op == "$group":
                groups = {}
                for r in rows:
                    gid = spec["_id"]
                    key = _dig(r, gid.lstrip("$")) if isinstance(gid, str) else gid
                    slot = groups.setdefault(key, {"_id": key})
                    for out_key, expr in spec.items():
                        if out_key == "_id":
                            continue
                        arg = expr["$sum"]
                        val = (
                            _dig(r, arg.lstrip("$")) or 0
                            if isinstance(arg, str)
                            else arg
                        )
                        slot[out_key] = slot.get(out_key, 0) + val
                rows = list(groups.values())
            elif op == "$sort":
                for fld, direction in reversed(list(spec.items())):
                    rows.sort(key=lambda r: r.get(fld) or 0, reverse=direction < 0)
            elif op == "$limit":
                rows = rows[:spec]
            else:
                raise AssertionError("fake does not implement %r" % op)
        return rows


class _DB:
    def __init__(self, **colls):
        self._c = {k: _TypeBracketColl(v) for k, v in colls.items()}

    def get_collection(self, name):
        return self._c.setdefault(name, _TypeBracketColl([]))

    def __getitem__(self, name):
        return self.get_collection(name)


class _StrictRepo:
    """Repository fake that ENFORCES the filter (incl. type bracketing)."""

    def __init__(self, docs):
        self.coll = _TypeBracketColl(docs)

    def find_many(self, flt=None, limit=None, **_k):
        return list(self.coll.find(flt))

    def count(self, flt=None):
        return self.coll.count_documents(flt)


# ---------------------------------------------------------------------------
# Order factory
# ---------------------------------------------------------------------------


def _order(created_at, total=1000.0, tax=0.0, staff="ZZ-S1", brand="ZZ-Brand",
           category="FRAMES", discount=0.0, oid="ZZ-O1"):
    return {
        "order_id": oid,
        "order_number": oid,
        "store_id": STORE,
        "status": "COMPLETED",
        "created_at": created_at,
        "grand_total": total,
        "total_amount": total,
        "tax_amount": tax,
        "sales_person_id": staff,
        "sales_person_name": "ZZ %s" % staff,
        "sales_staff_id": staff,
        "sales_staff_name": "ZZ %s" % staff,
        "items": [
            {
                "category": category,
                "brand": brand,
                "item_total": total,
                "quantity": 1,
                "cost_price": 0.0,
                "discount_amount": discount,
            }
        ],
    }


JUNE_CAST = [
    _order(J_EARLY, total=1000.0, tax=180.0, staff="ZZ-EARLY", brand="B-EARLY",
           category="FRAMES", discount=10.0, oid="ZZ-O-EARLY"),
    _order(J_NOON, total=500.0, tax=120.0, staff="ZZ-NOON", brand="B-NOON",
           category="LENSES", discount=5.0, oid="ZZ-O-NOON"),
    _order(J_AFTER, total=999.0, tax=999.0, staff="ZZ-AFTER", brand="B-AFTER",
           category="SUNGLASSES", discount=99.0, oid="ZZ-O-AFTER"),
]


def _june(endpoint, **kwargs):
    """Run a reports.py endpoint over JUNE_CAST for the typed June range."""
    repo = _StrictRepo(JUNE_CAST)
    with _PatchAttrs(
        reports_mod,
        get_order_repository=lambda: repo,
        get_stock_repository=lambda: None,
    ):
        return asyncio.run(
            endpoint(
                from_date=JUNE_1,
                to_date=JUNE_30,
                current_user=dict(ADMIN),
                **kwargs,
            )
        )


# ===========================================================================
# 1. /sales/by-salesperson -- the staff sales report
# ===========================================================================


def test_by_salesperson_june_window_is_the_ist_month():
    """Deciding trio in one response: the 00:30-IST-1-June sale is IN, the
    00:30-IST-1-July sale is OUT, the 16:30-IST-15-June control is IN."""
    out = _june(reports_mod.sales_by_salesperson, store_id=STORE)
    by_id = {r["id"]: r for r in out["data"]}
    assert set(by_id) == {"ZZ-EARLY", "ZZ-NOON"}, by_id
    assert by_id["ZZ-EARLY"]["sales"] == 1000.0
    assert by_id["ZZ-NOON"]["sales"] == 500.0


# ===========================================================================
# 2. /sales/by-category
# ===========================================================================


def test_by_category_june_window_is_the_ist_month():
    out = _june(reports_mod.sales_by_category, store_id=STORE)
    by_cat = {r["category"]: r["sales"] for r in out["data"]}
    assert by_cat == {"FRAMES": 1000.0, "LENSES": 500.0}, by_cat


# ===========================================================================
# 3. /finance/gst -- and it must AGREE with /finance/gst-summary
# ===========================================================================


def test_gst_report_june_window_is_the_ist_month():
    with _PatchAttrs(reports_mod, _get_raw_db=lambda: None):
        out = _june(reports_mod.gst_report, store_id=STORE)
    # tax 180 (early) + 120 (control); the 999 day-after invoice stays out.
    assert out["summary"]["total_tax"] == 300.0, out["summary"]
    dates = {r["date"] for r in out["data"]}
    # And each row's printed date is the IST day (round-1 VALUE fix intact).
    assert dates == {"2026-06-01", "2026-06-15"}, dates


def test_gst_report_agrees_with_finance_gst_summary_for_june():
    """One period, one tax total, two screens. /finance/gst-summary already
    shifts its month bounds through ist_day_start_utc; the /finance/gst
    report must select the SAME planted orders and report the SAME total."""
    with _PatchAttrs(reports_mod, _get_raw_db=lambda: None):
        report = _june(reports_mod.gst_report, store_id=STORE)
    db = _DB(orders=JUNE_CAST, vendor_bills=[], stores=[], customers=[])
    with _PatchAttrs(finance_mod, _get_db=lambda: db):
        summary = asyncio.run(
            finance_mod.get_gst_summary(
                month=6, year=2026, current_user=dict(ADMIN)
            )
        )
    assert summary["gst_collected"] == 300.0, summary
    assert report["summary"]["total_tax"] == summary["gst_collected"]


# ===========================================================================
# 4. /sales/comparison -- both windows, and the seam between them
# ===========================================================================


def _comparison(orders):
    repo = _StrictRepo(orders)
    with _PatchAttrs(reports_mod, get_order_repository=lambda: repo):
        return asyncio.run(
            reports_mod.sales_comparison(
                store_id=STORE,
                from_date=JUNE_1,
                to_date=JUNE_30,
                period_type="daily",
                current_user=dict(ADMIN),
            )
        )


# The comparison cast. The derived previous period for a June request is
# 2026-05-02 .. 2026-05-31 (30 days back-to-back with June).
COMPARISON_CAST = [
    # 00:30 IST 1 June -- CURRENT period, first-day early.
    _order(J_EARLY, total=1000.0, oid="ZZ-C-EARLY"),
    # 16:30 IST 15 June -- CURRENT, afternoon control.
    _order(J_NOON, total=500.0, oid="ZZ-C-NOON"),
    # 00:30 IST 1 July -- AFTER both periods, must be nowhere.
    _order(J_AFTER, total=999.0, oid="ZZ-C-AFTER"),
    # 16:30 IST 31 May (2026-05-31 11:00 UTC) -- PREVIOUS, last-day control.
    _order(datetime(2026, 5, 31, 11, 0), total=700.0, oid="ZZ-P-LAST"),
    # 00:30 IST 2 May (2026-05-01 19:00 UTC) -- PREVIOUS, first-day early.
    _order(datetime(2026, 5, 1, 19, 0), total=300.0, oid="ZZ-P-EARLY"),
]


def test_comparison_current_window_is_the_ist_range():
    out = _comparison(COMPARISON_CAST)
    assert out["current_period"]["sales"] == 1500.0, out["current_period"]
    assert out["current_period"]["orders"] == 2


def test_comparison_previous_window_is_the_ist_range():
    out = _comparison(COMPARISON_CAST)
    assert out["previous_period"]["sales"] == 1000.0, out["previous_period"]
    assert out["previous_period"]["orders"] == 2


def test_comparison_seam_no_order_dropped_or_double_counted():
    """The seam pin: for adjacent ranges every planted order lands in exactly
    one period. The 00:30-IST-1-June sale is the seam case -- its stored UTC
    instant is 31 May, and the old naive bounds put it in the PREVIOUS period
    (double-counting it against May) while dropping it from June."""
    out = _comparison(COMPARISON_CAST)
    total_counted = out["current_period"]["orders"] + out["previous_period"]["orders"]
    assert total_counted == 4  # all except the 1-July order, each exactly once
    assert (
        out["current_period"]["sales"] + out["previous_period"]["sales"] == 2500.0
    )


# ===========================================================================
# Round-3 sweep finds in reports.py: same trio via each endpoint's own shape
# ===========================================================================


def test_profit_by_category_june_window_is_the_ist_month():
    out = _june(reports_mod.profit_by_category, store_id=STORE)
    # cost_price 0 -> profit == revenue: 1000 (early) + 500 (control).
    assert out["total_profit"] == 1500.0, out


def test_profit_by_store_june_window_is_the_ist_month():
    out = _june(reports_mod.profit_by_store)
    assert [s["revenue"] for s in out["data"]] == [1500.0], out
    assert out["data"][0]["orders"] == 2


def test_discount_analysis_june_window_is_the_ist_month():
    out = _june(reports_mod.discount_analysis, store_id=STORE)
    # 10 (early) + 5 (control); the day-after order's 99 stays out.
    assert out["summary"]["total_discount"] == 15.0, out["summary"]


def test_staff_ranking_june_window_is_the_ist_month():
    out = _june(reports_mod.staff_ranking, store_id=STORE)
    assert {r["staff_id"] for r in out["data"]} == {"ZZ-EARLY", "ZZ-NOON"}


def test_expense_vs_revenue_june_window_is_the_ist_month():
    out = _june(reports_mod.expense_vs_revenue, store_id=STORE)
    assert out["revenue"] == 1500.0, out


def test_brand_sellthrough_june_window_is_the_ist_month():
    out = _june(reports_mod.brand_sellthrough, store_id=STORE)
    brands = {b["brand"] for b in out["data"]}
    assert brands == {"B-EARLY", "B-NOON"}, brands
    assert out["summary"]["total_revenue"] == 1500.0


# ===========================================================================
# THE ROSTER AGREEMENT (row 37's defect class): /sales/by-salesperson,
# /staff/ranking, both leaderboard twins and the payout month aggregate must
# see the SAME orders for the same IST month.
# ===========================================================================

# 2026-06-30 20:00 UTC IS 01:30 IST 1 July -- the frozen "now" for month mode.
NOW_EARLY_JULY = datetime(2026, 6, 30, 20, 0, 0)

ROSTER_CAST = [
    # 00:30 IST 1 July -- belongs to JULY everywhere.
    _order(datetime(2026, 6, 30, 19, 0), total=9000.0, staff="ZZ-S-JUL",
           oid="ZZ-R-JUL"),
    # 16:30 IST 30 June -- belongs to JUNE everywhere.
    _order(datetime(2026, 6, 30, 11, 0), total=7000.0, staff="ZZ-S-JUN",
           oid="ZZ-R-JUN"),
]


def test_by_salesperson_and_ranking_agree_with_leaderboards_and_payout():
    """The requirement IS the agreement: four screens, one July roster.
    Two-screens-different-rosters is the defect row 37 closed."""
    ist_now = (NOW_EARLY_JULY + timedelta(hours=5, minutes=30)).replace(tzinfo=IST)

    repo = _StrictRepo(ROSTER_CAST)
    with _PatchAttrs(reports_mod, get_order_repository=lambda: repo):
        by_sp = asyncio.run(
            reports_mod.sales_by_salesperson(
                store_id=STORE,
                from_date=date(2026, 7, 1),
                to_date=date(2026, 7, 31),
                current_user=dict(ADMIN),
            )
        )
        ranking = asyncio.run(
            reports_mod.staff_ranking(
                store_id=STORE,
                from_date=date(2026, 7, 1),
                to_date=date(2026, 7, 31),
                current_user=dict(ADMIN),
            )
        )

    db1 = _DB(orders=ROSTER_CAST)
    with _PatchAttrs(analytics_v2_mod, _get_db=lambda: db1, now_ist=lambda: ist_now):
        lb = asyncio.run(
            analytics_v2_mod.staff_leaderboard(
                store_id=STORE, period="month", current_user=dict(ADMIN)
            )
        )
    db2 = _DB(orders=ROSTER_CAST)
    with _PatchAttrs(payroll_mod, _get_db=lambda: db2, now_ist=lambda: ist_now):
        pl = asyncio.run(
            payroll_mod.get_commission_leaderboard(
                period="month", store_id=STORE, current_user=dict(ADMIN)
            )
        )

    report_ids = {r["id"] for r in by_sp["data"]}
    ranking_ids = {r["staff_id"] for r in ranking["data"]}
    lb_ids = {e["staff_id"] for e in lb["leaderboard"]}
    pl_ids = {e["staff_id"] for e in pl["leaderboard"]}
    assert report_ids == ranking_ids == lb_ids == pl_ids == {"ZZ-S-JUL"}, (
        report_ids, ranking_ids, lb_ids, pl_ids,
    )

    # And the payout month aggregate (the money that actually pays the staff)
    # sums exactly the same orders the report shows.
    db3 = _DB(orders=ROSTER_CAST)
    with _PatchAttrs(payout_mod, get_db=lambda: db3):
        agg = payout_mod._aggregate_sales(STORE, 2026, 7)
    report_total = sum(r["sales"] for r in by_sp["data"])
    assert agg["sales"] == report_total == 9000.0


def test_roster_agreement_afternoon_month_positive_control():
    """At 16:30 IST 10 June every frame agrees: a June order, a June roster."""
    orders = [
        _order(datetime(2026, 6, 10, 11, 0), total=7000.0, staff="ZZ-S-JUN",
               oid="ZZ-R-CTRL")
    ]
    repo = _StrictRepo(orders)
    with _PatchAttrs(reports_mod, get_order_repository=lambda: repo):
        by_sp = asyncio.run(
            reports_mod.sales_by_salesperson(
                store_id=STORE,
                from_date=JUNE_1,
                to_date=JUNE_30,
                current_user=dict(ADMIN),
            )
        )
    assert {r["id"] for r in by_sp["data"]} == {"ZZ-S-JUN"}


# ===========================================================================
# Sweep find: conversion analytics order-attribution window
# ===========================================================================


def _conversion(orders, tests):
    return conv_mod.get_conversion_dashboard(
        _StrictRepo(tests),
        _StrictRepo(orders),
        store_ids=[STORE],
        from_date=JUNE_1,
        to_date=JUNE_30,
        include_revenue=True,
    )


def _eye_test(tid, cid, test_date, completed_at, opto="ZZ-OPT"):
    return {
        "test_id": tid,
        "customer_id": cid,
        "store_id": STORE,
        "status": "COMPLETED",
        "test_date": test_date,
        "completed_at": completed_at,
        "optometrist_id": opto,
        "optometrist_name": "ZZ Optometrist",
    }


def test_conversion_credits_a_first_day_early_ist_order():
    """A test completed 01:00 IST 1 June converts via an order placed 00:30
    later -- the old naive-midnight fetch window never pulled that order and
    the optometrist silently lost the conversion credit."""
    tests = [_eye_test("ZZ-T1", "ZZ-CU1", "2026-06-01", "2026-05-31T19:30:00")]
    orders = [
        {
            "order_id": "ZZ-O-CONV",
            "order_number": "ZZ-O-CONV",
            "customer_id": "ZZ-CU1",
            "store_id": STORE,
            "status": "COMPLETED",
            "created_at": datetime(2026, 5, 31, 20, 0),  # 01:30 IST 1 June
            "grand_total": 4000.0,
        }
    ]
    out = _conversion(orders, tests)
    row = out["rows"][0]
    assert row["converted_count"] == 1, out
    assert row["revenue_attributed"] == 4000.0


def test_conversion_afternoon_order_still_credits_positive_control():
    tests = [_eye_test("ZZ-T2", "ZZ-CU2", "2026-06-15", "2026-06-15T10:00:00")]
    orders = [
        {
            "order_id": "ZZ-O-CTRL",
            "order_number": "ZZ-O-CTRL",
            "customer_id": "ZZ-CU2",
            "store_id": STORE,
            "status": "COMPLETED",
            "created_at": J_NOON,
            "grand_total": 2500.0,
        }
    ]
    out = _conversion(orders, tests)
    assert out["rows"][0]["converted_count"] == 1


# ===========================================================================
# Sweep find: the Activity Log today counters (settings.py). The
# _audit_time_filter SHAPE itself is pinned in test_audit_time_filter.py.
# ===========================================================================


def test_audit_summary_today_is_the_ist_day():
    """Frozen at IST 1 July: the 00:30-IST-1-July order counts as today, the
    16:30-IST-30-June one is yesterday, the 16:30-IST-1-July one is the
    afternoon control -- and the header names the IST day."""
    orders = [
        _order(datetime(2026, 6, 30, 19, 0), oid="ZZ-A-EARLY"),  # 00:30 IST 1 Jul
        _order(datetime(2026, 6, 30, 11, 0), oid="ZZ-A-YDAY"),   # 16:30 IST 30 Jun
        _order(datetime(2026, 7, 1, 11, 0), oid="ZZ-A-NOON"),    # 16:30 IST 1 Jul
    ]
    repo = _StrictRepo(orders)
    with _PatchAttrs(
        settings_mod,
        ist_today=lambda: date(2026, 7, 1),
        get_audit_repository=lambda: None,
    ), _PatchAttrs(deps_mod, get_order_repository=lambda: repo):
        out = asyncio.run(
            settings_mod.get_audit_logs_summary(current_user=dict(SUPER))
        )
    assert out["date"] == "2026-07-01"
    assert out["today"]["orders_created"] == 2, out


# ===========================================================================
# JUDGMENT SITE (ALIGN): megaphone run() today counters
# ===========================================================================


def test_megaphone_sent_today_starts_at_ist_midnight():
    """VERDICT: ALIGN. A send at 01:00 IST on 1 July (sent_at
    2026-06-30T19:30Z) is TODAY when the IST day is 1 July; a 17:30-IST
    30-June send is yesterday (positive control). Round 2 moved the jarvis
    owner brief's sent_today to IST midnight -- the MEGAPHONE status line
    must quote the same number or the two readouts disagree every night
    00:00-05:30 IST."""
    rows = [
        {"agent_id": "megaphone", "status": "SENT",
         "sent_at": "2026-06-30T19:30:00+00:00"},
        {"agent_id": "megaphone", "status": "SENT",
         "sent_at": "2026-06-30T12:00:00+00:00"},
        {"agent_id": "megaphone", "status": "PENDING"},
    ]
    agent = megaphone_mod.MegaphoneAgent(db=_DB(notification_logs=rows))
    with _PatchAttrs(megaphone_mod, ist_today=lambda: date(2026, 7, 1)):
        out = asyncio.run(agent.run("", None))
    assert out.success is True
    assert out.data["sent_today"] == 1, out.data
    assert out.data["pending_now"] == 1
