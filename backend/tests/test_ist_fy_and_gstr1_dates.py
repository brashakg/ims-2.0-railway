"""BUG-104 final round: the FINANCIAL YEAR and the dates on the GST return.

Railway runs UTC; the business calendar is IST (UTC+5:30); `created_at` is a
naive ``datetime.now()`` == the UTC wall clock. Every instant between 00:00 and
05:30 IST therefore carries the PREVIOUS calendar day. Production holds 76 of
934 orders in that window, ONE of them on 1 April.

Two rules, and every test below says which one it is pinning:

  VALUE  a day derived FROM a stored instant and then displayed / exported
         moves FORWARD  -> ist_date_str (and, for the FY, fy_start_year_ist
         applied to that IST day).
  BOUND  a bound COMPARED AGAINST stored instants moves BACKWARD
         -> ist_day_start_utc.

Every assertion reads a RETURNED PAYLOAD. Every shifted case is paired with a
POSITIVE CONTROL -- an ordinary afternoon instant whose answer must NOT move --
because without one a "shift everything" implementation passes the whole file.

The clock helpers reports.py reads are frozen in every endpoint test, so
nothing here depends on the day it runs (a calendar-dependent test broke main).

No emoji (Windows cp1252).
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import date, datetime

os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("MONGODB_URI", "")
os.environ.setdefault("DISPATCH_MODE", "off")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import reports as reports_mod  # noqa: E402
from api.utils.ist import (  # noqa: E402
    fy_start_year_ist,
    ist_date_str,
    ist_day_start_utc,
)


STORE = "ZZ-IST-STORE"

# The production shape named in the brief: 2026-03-31T20:00 UTC == 1-Apr-2026
# 01:30 IST. Its calendar day, its month and its FINANCIAL YEAR all differ
# between the two frames.
FY_EVE = datetime(2026, 3, 31, 20, 0, 0)
# POSITIVE CONTROL: the same 31 March, but an ordinary working afternoon
# (16:30 IST). Nothing about this instant may move.
MARCH_AFTERNOON = datetime(2026, 3, 31, 11, 0, 0)
APRIL_AFTERNOON = datetime(2026, 4, 2, 11, 0, 0)
# 1-Jul 01:30 IST, for the month-key / weekday cases.
JULY_EVE = datetime(2026, 6, 30, 20, 0, 0)
JUNE_AFTERNOON = datetime(2026, 6, 30, 11, 0, 0)


# ---------------------------------------------------------------------------
# Fakes. The window fake really applies the bounds it is handed, so a test
# about a bound cannot pass by the fake quietly ignoring it.
# ---------------------------------------------------------------------------


class _WindowRepo:
    """An order repo that HONESTLY applies the created_at range it is given."""

    def __init__(self, docs):
        self.docs = [dict(d) for d in docs]
        self.last_filter = None

    def find_many(self, flt, limit=0, **_k):
        self.last_filter = flt
        rng = (flt or {}).get("created_at") or {}
        for op in rng:
            if op not in ("$gte", "$lte", "$lt"):
                raise AssertionError(
                    "strict repo does not implement %r -- implement it rather "
                    "than let the bound pass by accident" % op
                )
        out = []
        for d in self.docs:
            ca = d.get("created_at")
            if "$gte" in rng and not (ca is not None and ca >= rng["$gte"]):
                continue
            if "$lte" in rng and not (ca is not None and ca <= rng["$lte"]):
                continue
            if "$lt" in rng and not (ca is not None and ca < rng["$lt"]):
                continue
            out.append(dict(d))
        return out


class _AllOrdersRepo:
    """Returns every order regardless of window -- used ONLY by the VALUE
    tests, so a label assertion can never be satisfied (or broken) by the
    bound."""

    def __init__(self, docs):
        self.docs = [dict(d) for d in docs]

    def find_many(self, *_a, **_k):
        return [dict(d) for d in self.docs]


def _order(created_at, oid, net=2000.0, tax=0.0):
    return {
        "order_id": oid,
        "store_id": STORE,
        "status": "COMPLETED",
        "created_at": created_at,
        "grand_total": float(net + tax),
        "tax_amount": float(tax),
        "customer_id": "ZZ-CUST-" + oid,
        "items": [],
    }


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

    def find_one(self, flt=None, *_a, **_k):
        rows = self.find(flt)
        return rows[0] if rows else None


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


class _Patched:
    """Freeze the clock helpers reports.py reads."""

    def __init__(self, repo, today=date(2026, 8, 18), fy=2026, db=None):
        self.repo = repo
        self.today = today
        self.fy = fy
        self.db = db if db is not None else _StrictDB()
        self._saved = {}

    def __enter__(self):
        for name in (
            "get_order_repository",
            "validate_store_access",
            "ist_today",
            "now_ist_naive",
            "fy_start_year_ist",
            "get_db",
        ):
            self._saved[name] = getattr(reports_mod, name)
        reports_mod.get_order_repository = lambda: self.repo
        reports_mod.validate_store_access = lambda s, u: STORE
        reports_mod.ist_today = lambda: self.today
        reports_mod.now_ist_naive = lambda: datetime(
            self.today.year, self.today.month, self.today.day, 12, 0, 0
        )
        reports_mod.fy_start_year_ist = lambda dt=None: (
            self.fy if dt is None else fy_start_year_ist(dt)
        )
        reports_mod.get_db = lambda: self.db
        return self

    def __exit__(self, *_a):
        for name, val in self._saved.items():
            setattr(reports_mod, name, val)
        return False


# ===========================================================================
# A. THE FINANCIAL YEAR -- reports._fy_of
# ===========================================================================


def test_fy_of_tags_a_1_april_small_hours_order_into_the_NEW_financial_year():
    """VALUE rule. The exact production shape: 2026-03-31T20:00 UTC is
    1-Apr-2026 01:30 IST, so it belongs to FY26-27. Under the old raw
    ``dt.month >= 4`` test it was tagged FY25-26 -- a statutory mis-file,
    because the GST invoice serial on that same order is FY-scoped (Rule
    46(b)) and is minted off the IST clock."""
    assert reports_mod._fy_of(FY_EVE) == "FY26-27"


def test_fy_of_leaves_a_march_afternoon_in_the_old_fy_positive_control():
    """Without this, a "+5:30 everything" or "always the next FY"
    implementation passes the test above."""
    assert reports_mod._fy_of(MARCH_AFTERNOON) == "FY25-26"


def test_fy_of_boundary_pair_one_second_apart_straddles_the_financial_year():
    assert reports_mod._fy_of(datetime(2026, 3, 31, 18, 29, 59)) == "FY25-26"
    assert reports_mod._fy_of(datetime(2026, 3, 31, 18, 30, 0)) == "FY26-27"


def test_fy_of_agrees_with_the_single_shared_fy_definition():
    """_fy_of must be a LABEL over ist.fy_start_year_ist, not a second copy of
    the April rule. Two copies is how the boundary drifts."""
    for stored in (FY_EVE, MARCH_AFTERNOON, APRIL_AFTERNOON, JULY_EVE):
        yr = fy_start_year_ist(datetime.fromisoformat(ist_date_str(stored)))
        assert reports_mod._fy_of(stored) == "FY%02d-%02d" % (
            yr % 100,
            (yr + 1) % 100,
        )


def test_fy_of_ordinary_days_are_unchanged_by_the_shift():
    assert reports_mod._fy_of(datetime(2026, 4, 2, 11, 0)) == "FY26-27"
    assert reports_mod._fy_of(datetime(2025, 12, 25, 9, 0)) == "FY25-26"


# --- and the payload the FY is actually read off ---------------------------


def _price_bands(repo, **kw):
    with _Patched(repo, **kw):
        return asyncio.run(
            reports_mod.sales_price_bands(
                store_id=STORE, fy_count=3, trend_bands=4, current_user={}
            )
        )


def test_price_bands_payload_files_the_1_april_order_under_the_new_fy():
    """VALUE rule, end-to-end on the RETURNED PAYLOAD. Two invoices of the
    same size, 8h30m apart across the FY boundary, must land in DIFFERENT
    financial years."""
    out = _price_bands(
        _AllOrdersRepo(
            [_order(FY_EVE, "ZZ-FY-EVE"), _order(MARCH_AFTERNOON, "ZZ-MARCH")]
        )
    )
    by_fy = {row["fy"]: row for row in out["by_fy"]}
    assert sorted(by_fy) == ["FY25-26", "FY26-27"]
    band = out["bands"].index("1K-2.5K")
    assert by_fy["FY26-27"]["invoices_by_band"][band] == 1
    # POSITIVE CONTROL: the afternoon invoice stayed in the old FY.
    assert by_fy["FY25-26"]["invoices_by_band"][band] == 1


def test_price_bands_payload_months_are_ist_months():
    """VALUE rule on the same row's ``month`` key: 1-Jul 01:30 IST charts
    under 2026-07, not 2026-06."""
    out = _price_bands(
        _AllOrdersRepo(
            [_order(JULY_EVE, "ZZ-JUL"), _order(JUNE_AFTERNOON, "ZZ-JUN")]
        )
    )
    months = sorted(
        {r["month"] for rows in out["monthly_trend_by_band"].values() for r in rows}
    )
    assert months == ["2026-06", "2026-07"]


def test_price_bands_WINDOW_still_includes_the_1_april_order_it_now_tags():
    """BOUND rule, the other half. The window opens on 1-April of the oldest
    FY; expressed in the stored naive-UTC frame that is 31-Mar 18:30, NOT
    1-Apr 00:00. With the old bare ``datetime(start_year, 4, 1)`` the very
    order _fy_of now tags into that FY was excluded from it -- the label and
    the total would disagree."""
    repo = _WindowRepo(
        [
            _order(datetime(2024, 3, 31, 20, 0), "ZZ-IN"),   # 1-Apr-2024 01:30 IST
            _order(datetime(2024, 3, 31, 11, 0), "ZZ-OUT"),  # 31-Mar-2024 16:30 IST
        ]
    )
    out = _price_bands(repo)
    assert repo.last_filter["created_at"]["$gte"] == ist_day_start_utc(
        date(2024, 4, 1)
    )
    assert repo.last_filter["created_at"]["$gte"] == datetime(2024, 3, 31, 18, 30)
    # Exactly one order survived the bound, and it is the FY24-25 one.
    assert out["total_orders"] == 1
    assert [r["fy"] for r in out["by_fy"]] == ["FY24-25"]


def test_price_bands_window_is_NOT_naive_local_midnight_the_defect():
    repo = _WindowRepo([])
    _price_bands(repo)
    assert repo.last_filter["created_at"]["$gte"] != datetime(2024, 4, 1, 0, 0)


# ===========================================================================
# THE GST RETURN -- the invoiceDate on a filed GSTR-1 row
# ===========================================================================


# B2CS rows are CONSOLIDATED and carry no per-invoice date, so the GSTR-1
# tests use a GSTIN-bearing customer -- the B2B section is where a dated,
# invoice-level row is filed (and where a date outside the tax period is
# rejected by the portal).
_B2B_CUSTOMERS = [
    {
        "customer_id": "ZZ-CUST-B2B",
        "gstin": "20AACCM1234C1ZP",
        "name": "ZZ Registered Buyer",
        "state": "20",
    }
]


def _gstr1(orders, month):
    db = _StrictDB(
        orders=orders,
        customers=_B2B_CUSTOMERS,
        stores=[
            {
                "store_id": STORE,
                "gstin": "20AABCU9603R1ZM",
                "store_name": "ZZ Test Store",
                "state": "20",
            }
        ],
    )
    real_raw = reports_mod._get_raw_db
    real_repo = reports_mod.get_order_repository
    reports_mod._get_raw_db = lambda: db
    reports_mod.get_order_repository = lambda: _NoOrders()
    try:
        return reports_mod._compute_gstr1(month, STORE)
    finally:
        reports_mod._get_raw_db = real_raw
        reports_mod.get_order_repository = real_repo


def _dated_rows(out):
    """Every GSTR-1 row in the payload that carries an invoiceDate."""
    rows = []
    for key in ("b2b", "b2cl", "b2cs"):
        for r in out.get(key) or []:
            if isinstance(r, dict) and r.get("invoiceDate"):
                rows.append(r)
    return rows


def test_gstr1_invoice_date_of_a_1_april_order_is_1_april_not_31_march():
    """VALUE rule, and the worst outcome on the whole list. The month window
    is already IST (a BOUND, moving backward), so this order is correctly
    SELECTED into April's GSTR-1 -- but it used to be DATED 2026-03-31: a date
    outside the tax period it is filed in, in the PRIOR financial year, on a
    row whose Rule 46(b) serial was minted off the IST clock."""
    o = _order(FY_EVE, "ZZ-GST-1", net=10000.0, tax=500.0)
    o["customer_id"] = "ZZ-CUST-B2B"
    o["order_number"] = "BV/26-27/0001"
    rows = _dated_rows(_gstr1([o], "2026-04"))
    assert rows, "the 1-April order was not selected into the April GSTR-1"
    assert rows[0]["invoiceDate"] == "2026-04-01"
    assert rows[0]["invoiceDate"] != "2026-03-31"


def test_gstr1_invoice_date_of_an_afternoon_order_is_unchanged_positive_control():
    o = _order(APRIL_AFTERNOON, "ZZ-GST-2", net=10000.0, tax=500.0)
    o["customer_id"] = "ZZ-CUST-B2B"
    o["order_number"] = "BV/26-27/0002"
    rows = _dated_rows(_gstr1([o], "2026-04"))
    assert rows and rows[0]["invoiceDate"] == "2026-04-02"


def test_gstr1_every_invoice_date_falls_inside_the_month_it_is_filed_in():
    """The property the GSTN portal actually enforces: a row selected into a
    tax period must carry a date inside that period. This assertion fails the
    moment the bound and the value stop sharing a frame."""
    orders = []
    for i, ca in enumerate((FY_EVE, APRIL_AFTERNOON, datetime(2026, 4, 30, 20, 0))):
        o = _order(ca, "ZZ-GST-P%d" % i, net=8000.0, tax=400.0)
        o["customer_id"] = "ZZ-CUST-B2B"
        o["order_number"] = "BV/26-27/010%d" % i
        orders.append(o)
    rows = _dated_rows(_gstr1(orders, "2026-04"))
    # 30-Apr 20:00 UTC is 1-MAY IST, so the April window must not hold it.
    assert len(rows) == 2, [r["invoiceDate"] for r in rows]
    for r in rows:
        assert r["invoiceDate"].startswith("2026-04"), r["invoiceDate"]


def test_gstr1_invoice_date_does_not_guess_at_a_legacy_string_created_at():
    """A bare ISO string carries no reliable frame, so ist_date_str passes it
    through -- exactly the pre-fix behaviour for the 322 migrated orders.

    Asserted on the helper rather than through _compute_gstr1 on purpose: a
    string-typed created_at can never reach that code path at all, because
    Mongo type-brackets and the window is a real BSON Date range. Pinned here
    so nobody 'improves' the helper into a silent shift of unknown data."""
    assert ist_date_str("2026-04-30T22:30:00") == "2026-04-30"
    assert ist_date_str(datetime(2026, 4, 30, 22, 30)) == "2026-05-01"


# ===========================================================================
# B. FOOTFALL AUDIT -- the two sides of one table row
# ===========================================================================


def _footfall(orders, walkins, walkouts, today=date(2026, 7, 15)):
    db = _StrictDB(walk_in_counters=walkins, walkouts=walkouts)
    repo = _WindowRepo(orders)
    with _Patched(repo, today=today, db=db):
        out = asyncio.run(
            reports_mod.footfall_audit(
                store_id=STORE, months_back=3, current_user={}
            )
        )
    return out, repo


def test_footfall_buckets_an_order_by_the_SAME_month_as_its_walkout():
    """VALUE rule. ``date_str`` on walk_in_counters / walkouts is already an
    IST business day; bucketing orders by the raw UTC month put a 1-Jul 01:30
    IST sale in JUNE while its walkout sat in JULY -- one row of the table
    then showed more orders than walk-ins and invented 'hidden sales'."""
    out, _ = _footfall(
        orders=[_order(JULY_EVE, "ZZ-FF-1")],
        walkins=[{"store_id": STORE, "date_str": "2026-07-01", "total": 1}],
        walkouts=[
            {
                "store_id": STORE,
                "date_str": "2026-07-01",
                "result": "CONVERTED",
                "deleted_at": None,
            }
        ],
    )
    by_month = {m["month"]: m for m in out["months"]}
    assert by_month["2026-07"]["orders_total"] == 1
    assert by_month["2026-07"]["hidden_sales"] == 0
    assert by_month["2026-06"]["orders_total"] == 0


def test_footfall_afternoon_order_stays_in_its_own_month_positive_control():
    out, _ = _footfall(
        orders=[_order(JUNE_AFTERNOON, "ZZ-FF-2")],
        walkins=[{"store_id": STORE, "date_str": "2026-06-30", "total": 1}],
        walkouts=[
            {
                "store_id": STORE,
                "date_str": "2026-06-30",
                "result": "CONVERTED",
                "deleted_at": None,
            }
        ],
    )
    by_month = {m["month"]: m for m in out["months"]}
    assert by_month["2026-06"]["orders_total"] == 1
    assert by_month["2026-07"]["orders_total"] == 0


def test_footfall_order_window_opens_at_ist_midnight_in_the_stored_frame():
    """BOUND rule. months_back=3 from 15-Jul-2026 opens the window on 1-April;
    IST-midnight on the 1st IS 31-Mar 18:30 UTC. With the old IST-wall-clock
    bound every order placed 00:00-05:30 IST on the 1st of the opening month
    was dropped from the orders leg while its walkout stayed in the walkout
    leg -- and on this particular month that is also the financial-year
    boundary."""
    out, repo = _footfall(
        orders=[
            _order(datetime(2026, 3, 31, 20, 0), "ZZ-FF-IN"),   # 1-Apr 01:30 IST
            _order(datetime(2026, 3, 31, 11, 0), "ZZ-FF-OUT"),  # 31-Mar 16:30 IST
        ],
        walkins=[],
        walkouts=[],
    )
    assert repo.last_filter["created_at"]["$gte"] == ist_day_start_utc(
        date(2026, 4, 1)
    )
    assert repo.last_filter["created_at"]["$gte"] == datetime(2026, 3, 31, 18, 30)
    assert repo.last_filter["created_at"]["$gte"] != datetime(2026, 4, 1, 0, 0)
    by_month = {m["month"]: m for m in out["months"]}
    assert by_month["2026-04"]["orders_total"] == 1


# ===========================================================================
# SEASONALITY -- day-of-week and month-of-year off a stored instant
# ===========================================================================


def _seasonality(orders, today=date(2026, 7, 15)):
    repo = _WindowRepo(orders)
    with _Patched(repo, today=today):
        out = asyncio.run(
            reports_mod.sales_seasonality(
                store_id=STORE, years_back=2, current_user={}
            )
        )
    return out, repo


def test_seasonality_counts_a_monday_small_hours_sale_on_MONDAY():
    """VALUE rule. 2026-06-28 20:00 UTC is Monday 29-Jun 01:30 IST. Reading
    ``.weekday()`` off the raw instant filed it under SUNDAY -- and which day
    the shop is busy is the entire point of this report."""
    out, _ = _seasonality(
        [_order(datetime(2026, 6, 28, 20, 0), "ZZ-S1", net=5000.0)]
    )
    by_dow = {r["dow"]: r for r in out["day_of_week"]}
    assert by_dow["Mon"]["revenue"] == 5000.0
    assert by_dow["Sun"]["revenue"] == 0.0
    assert out["peak_dow"] == "Mon"


def test_seasonality_afternoon_sale_keeps_its_own_weekday_positive_control():
    """Sunday 28-Jun-2026 16:30 IST really is a Sunday in both frames."""
    out, _ = _seasonality(
        [_order(datetime(2026, 6, 28, 11, 0), "ZZ-S2", net=5000.0)]
    )
    by_dow = {r["dow"]: r for r in out["day_of_week"]}
    assert by_dow["Sun"]["revenue"] == 5000.0
    assert by_dow["Mon"]["revenue"] == 0.0


def test_seasonality_month_of_year_uses_the_ist_month():
    out, _ = _seasonality(
        [
            _order(JULY_EVE, "ZZ-S3", net=3000.0),
            _order(JUNE_AFTERNOON, "ZZ-S4", net=1000.0),
        ]
    )
    by_moy = {r["month"]: r for r in out["month_of_year"]}
    assert by_moy["Jul"]["revenue"] == 3000.0
    assert by_moy["Jun"]["revenue"] == 1000.0


def test_seasonality_window_bound_is_an_ist_midnight_in_the_stored_frame():
    """BOUND rule -- the window and the buckets live in one endpoint, so they
    had to move together (in opposite directions)."""
    today = date(2026, 7, 15)
    repo = _WindowRepo([])
    with _Patched(repo, today=today):
        asyncio.run(
            reports_mod.sales_seasonality(
                store_id=STORE, years_back=2, current_user={}
            )
        )
    expected_day = date.fromordinal(today.toordinal() - 730)
    assert repo.last_filter["created_at"]["$gte"] == ist_day_start_utc(expected_day)
    assert repo.last_filter["created_at"]["$gte"].hour == 18
    assert repo.last_filter["created_at"]["$gte"].minute == 30
