# ============================================================================
# Audit R03: ONE month-window rule -- the half-open IST month [start, next_start)
# ============================================================================
# ist_month_window_utc used to return an INCLUSIVE end (next month's start minus
# one microsecond) while GSTR-3B's credit-note leg compared it with `$lt` / `<`.
# A credit note stamped at the month's very last microsecond belonged to no
# month at all on GSTR-3B, while GSTR-1 (its own true half-open window) filed it.
#
# Every month window now reads [start, next_start) and compares with `<`/`$lt`.
# Rows sit at the month's first instant, its last microsecond and the exact
# start of the next month, in every stored shape a row can carry: BSON naive
# UTC, BSON aware, ISO strings naive / '+00:00' / 'Z'. Each row carries a
# distinct power-of-ten amount, so a month total names exactly which rows it
# counted; consecutive months must partition the rows with none in two months
# and none in neither.

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import mongomock  # noqa: E402
import pytest  # noqa: E402

from api.routers import budgets as budgets_mod  # noqa: E402
from api.routers import hr_self_service as hr_mod  # noqa: E402
from api.routers import payroll as payroll_mod  # noqa: E402
from api.routers.reports import growth as growth_mod  # noqa: E402
from api.routers.reports import gstr1, gstr3b  # noqa: E402
from api.utils.ist import ist_month_window_utc  # noqa: E402

STORE = "S1"
USER = "U1"

# Naive-UTC instants around April and May 2026 (IST months).
MAR_LAST = datetime(2026, 3, 31, 18, 29, 59, 999999)
APR_START = datetime(2026, 3, 31, 18, 30)
APR_LAST = datetime(2026, 4, 30, 18, 29, 59, 999999)
MAY_START = datetime(2026, 4, 30, 18, 30)
MAY_LAST = datetime(2026, 5, 31, 18, 29, 59, 999999)
JUN_START = datetime(2026, 5, 31, 18, 30)

# instant -> distinct amount (a month total identifies its exact members)
ROWS = [
    (MAR_LAST, 1),
    (APR_START, 10),
    (APR_LAST, 100),
    (MAY_START, 1000),
    (MAY_LAST, 10000),
    (JUN_START, 100000),
]
APRIL = 10 + 100
MAY = 1000 + 10000
EVERYTHING = sum(v for _, v in ROWS)


def _shape(instant: datetime, shape: str):
    if shape == "bson_naive":
        return instant
    if shape == "bson_aware":
        return instant.replace(tzinfo=timezone.utc)
    if shape == "str_naive":
        return instant.isoformat()
    if shape == "str_utc":
        return instant.replace(tzinfo=timezone.utc).isoformat()
    if shape == "str_z":
        return instant.isoformat() + "Z"
    raise AssertionError(shape)


ALL_SHAPES = ["bson_naive", "bson_aware", "str_naive", "str_utc", "str_z"]
BSON_SHAPES = ["bson_naive", "bson_aware"]


def _db():
    db = mongomock.MongoClient().get_database("r03")
    db.stores.insert_one({"store_id": STORE, "gstin": "20AAAAA0000A1Z5", "state": "Jharkhand"})
    return db


def _month_total(fn, year_month_pairs=((2026, 3), (2026, 4), (2026, 5), (2026, 6))):
    return {ym: fn(*ym) for ym in year_month_pairs}


def _assert_partition(totals):
    """April and May hold exactly their own rows; Mar+Apr+May+Jun count every
    row exactly once (a power-of-ten double count or drop changes the sum)."""
    assert totals[(2026, 4)] == APRIL, totals
    assert totals[(2026, 5)] == MAY, totals
    assert sum(totals.values()) == EVERYTHING, totals


# ---------------------------------------------------------------------------
# The helper itself
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("year,month", [(2026, 1), (2026, 4), (2026, 12), (2027, 2)])
def test_window_is_half_open_and_consecutive_months_share_one_bound(year, month):
    start, end = ist_month_window_utc(year, month)
    ny, nm = (year + 1, 1) if month == 12 else (year, month + 1)
    assert end == ist_month_window_utc(ny, nm)[0]
    assert (start.hour, start.minute, start.microsecond) == (18, 30, 0)
    assert (end.hour, end.minute, end.microsecond) == (18, 30, 0)


# ---------------------------------------------------------------------------
# GSTR-3B credit notes -- both legs, every shape
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("shape", ALL_SHAPES)
def test_gstr3b_credit_note_ledger_leg_partitions_the_months(shape):
    db = _db()
    for instant, amount in ROWS:
        db.credit_note_ledger.insert_one({
            "store_id": STORE, "type": "ISSUED", "created_at": _shape(instant, shape),
            "ref": f"SHOPIFY-HIST-REFUND-{amount}", "amount": amount,
            "taxable": amount, "tax": 0, "interstate": False,
        })
    _assert_partition(_month_total(
        lambda y, m: gstr3b._credit_note_totals(db, STORE, y, m, 30)[3]))


@pytest.mark.parametrize("shape", ALL_SHAPES)
def test_gstr3b_returns_leg_partitions_the_months(shape):
    db = _db()
    for instant, amount in ROWS:
        db.returns.insert_one({
            "store_id": STORE, "status": "COMPLETED", "return_id": f"RET-{amount}",
            "created_at": _shape(instant, shape), "interstate": False,
            "gst_breakup": {"taxable": amount, "tax": amount / 100},
        })
    _assert_partition(_month_total(
        lambda y, m: gstr3b._credit_note_totals(db, STORE, y, m, 30)[3]))


# ---------------------------------------------------------------------------
# GSTR-1 CDNR -- the same match, both legs
# ---------------------------------------------------------------------------


def _gstr1_cdnr_total(db, y, m, monkeypatch):
    monkeypatch.setattr(gstr1, "_get_raw_db", lambda: db)
    report = gstr1._compute_gstr1(f"{y:04d}-{m:02d}", STORE)
    return sum(int(round(float(n.get("taxable_value") or n.get("taxableValue") or 0)))
               for n in report.get("cdnr", []))


@pytest.mark.parametrize("shape", ALL_SHAPES)
def test_gstr1_credit_note_ledger_leg_partitions_the_months(shape, monkeypatch):
    db = _db()
    for instant, amount in ROWS:
        db.credit_note_ledger.insert_one({
            "store_id": STORE, "type": "ISSUED", "created_at": _shape(instant, shape),
            "ref": f"SHOPIFY-HIST-REFUND-{amount}", "note_number": f"CN-{amount}",
            "amount": amount, "taxable": amount, "tax": 0, "interstate": False,
        })
    _assert_partition(_month_total(lambda y, m: _gstr1_cdnr_total(db, y, m, monkeypatch)))


@pytest.mark.parametrize("shape", ALL_SHAPES)
def test_gstr1_returns_leg_partitions_the_months(shape, monkeypatch):
    db = _db()
    for instant, amount in ROWS:
        db.returns.insert_one({
            "store_id": STORE, "status": "COMPLETED", "return_id": f"RET-{amount}",
            "created_at": _shape(instant, shape), "interstate": False,
            "gst_breakup": {"taxable": amount, "tax": amount / 100},
        })
    _assert_partition(_month_total(lambda y, m: _gstr1_cdnr_total(db, y, m, monkeypatch)))


# ---------------------------------------------------------------------------
# Order-month callers: budgets, /sales/growth, payroll + self commission
# ---------------------------------------------------------------------------
# orders.created_at is a BSON date (BaseRepository); a Mongo date range never
# matches a string row, on main or here, so these run the BSON shapes.


class _Repo:
    def __init__(self, col):
        self.col = col

    def find_many(self, flt, limit=0, **_kw):
        return list(self.col.find(flt))


def _orders_db(shape):
    db = _db()
    for instant, amount in ROWS:
        db.orders.insert_one({
            "order_id": f"O-{amount}", "store_id": STORE, "status": "COMPLETED",
            "created_at": _shape(instant, shape), "salesperson_id": USER,
            "grand_total": amount, "total_amount": amount,
        })
    db.salary_config.insert_one({"employee_id": USER, "commission_rate_percent": 0})
    return db


@pytest.mark.parametrize("shape", BSON_SHAPES)
def test_budgets_revenue_actual_partitions_the_months(shape, monkeypatch):
    db = _orders_db(shape)
    monkeypatch.setattr(budgets_mod, "get_order_repository", lambda: _Repo(db.orders))
    _assert_partition(_month_total(
        lambda y, m: budgets_mod._revenue_actual(STORE, f"{y:04d}-{m:02d}")))


@pytest.mark.parametrize("shape", BSON_SHAPES)
def test_sales_growth_current_month_partitions_the_months(shape, monkeypatch):
    db = _orders_db(shape)
    monkeypatch.setattr(growth_mod, "get_order_repository", lambda: _Repo(db.orders))
    user = {"user_id": USER, "roles": ["SUPERADMIN"], "active_store_id": STORE}
    _assert_partition(_month_total(lambda y, m: asyncio.run(growth_mod.sales_growth(
        store_id=STORE, year=y, month=m, current_user=user))["current_month"]["sales"]))


@pytest.mark.parametrize("shape", BSON_SHAPES)
def test_payroll_commission_summary_partitions_the_months(shape, monkeypatch):
    db = _orders_db(shape)
    monkeypatch.setattr(payroll_mod, "_get_db", lambda: db)
    user = {"user_id": "ADMIN1", "roles": ["SUPERADMIN"], "active_store_id": STORE}

    def total(y, m):
        out = asyncio.run(payroll_mod.get_commission_summary(
            month=m, year=y, store_id=STORE, employee_id=None, current_user=user))
        return sum(i["revenue"] for i in out["items"])

    _assert_partition(_month_total(total))


@pytest.mark.parametrize("shape", BSON_SHAPES)
def test_my_commission_partitions_the_months(shape, monkeypatch):
    db = _orders_db(shape)
    monkeypatch.setattr(hr_mod, "_get_db", lambda: db)
    user = {"user_id": USER, "roles": ["SALES_STAFF"], "active_store_id": STORE}
    _assert_partition(_month_total(lambda y, m: asyncio.run(
        hr_mod.my_commission(month=m, year=y, current_user=user))["revenue"]))


# ---------------------------------------------------------------------------
# The audit probe (a13-shopify-credit-boundary-probe.py) as a regression test
# ---------------------------------------------------------------------------
# The real Shopify refund writer feeds both GST readers; GSTR-1 and GSTR-3B
# must select the SAME credit notes for April, in the legacy naive-UTC string
# and the explicit '+00:00' string. On main the final microsecond
# (2026-04-30T18:29:59.999999Z) was in GSTR-1 and missing from GSTR-3B.

PROBE = [
    ("2026-03-31T18:29:59.999999Z", False),
    ("2026-03-31T18:30:00Z", True),
    ("2026-04-30T18:29:59.999998Z", True),
    ("2026-04-30T18:29:59.999999Z", True),
    ("2026-04-30T18:30:00Z", False),
]


@pytest.mark.parametrize("raw,in_april", PROBE)
@pytest.mark.parametrize("representation", ["legacy", "explicit"])
def test_audit_probe_gstr1_and_gstr3b_select_the_same_april_notes(
    raw, in_april, representation, monkeypatch
):
    from api.services.shopify_ingest import _refund_credit_note_date_iso

    encoded = _refund_credit_note_date_iso({"processed_at": raw}, {})
    if representation == "legacy":
        stamp = (datetime.fromisoformat(encoded).astimezone(timezone.utc)
                 .replace(tzinfo=None).isoformat())
    else:
        stamp = encoded
    db = _db()
    db.credit_note_ledger.insert_one({
        "store_id": STORE, "type": "ISSUED", "created_at": stamp,
        "ref": "SHOPIFY-HIST-REFUND-123", "note_number": "CNH-123", "amount": 105,
        "taxable": 100, "tax": 5, "interstate": False,
    })
    monkeypatch.setattr(gstr1, "_get_raw_db", lambda: db)
    gstr1_notes = len(gstr1._compute_gstr1("2026-04", STORE).get("cdnr", []))
    gstr3b_taxable = gstr3b._credit_note_totals(db, STORE, 2026, 4, 30)[3]
    assert gstr1_notes == (1 if in_april else 0)
    assert gstr3b_taxable == (100.0 if in_april else 0.0)
