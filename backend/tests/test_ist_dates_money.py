"""IST business dates on the money paths (IST clock sweep, lane A).

Stored instants are naive UTC (prod runs UTC); business days, months and
financial years are IST. Between 00:00 and 05:30 IST the UTC date is still
the PREVIOUS IST day. Each test pins the NIGHT instant (2026-12-31T20:00Z =
01:30 IST on 1 Jan 2027) and the DAY control (2026-12-31T09:00Z = 14:30 IST
on 31 Dec) and asks for the IST answer at both.

CLK-P02 does not depend on the clock: the night that matters there is the
ORDER's stored instant (an order at 02:00 IST on 1 Nov is stored 31 Oct
20:30 UTC), so its night/day pair is two orders, not two clocks.
"""

import os
import sys
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mongomock  # noqa: E402
import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from freezegun import freeze_time  # noqa: E402

NIGHT = "2026-12-31T20:00:00Z"  # IST 2027-01-01 01:30
DAY = "2026-12-31T09:00:00Z"  # IST 2026-12-31 14:30
IST_TODAY = {NIGHT: date(2027, 1, 1), DAY: date(2026, 12, 31)}


# ---------------------------------------------------------------- CLK-P01
@pytest.mark.parametrize("instant", [NIGHT, DAY])
def test_promo_window_reads_the_ist_day(instant):
    from api.routers import promotions

    today = IST_TODAY[instant]
    yesterday = date.fromordinal(today.toordinal() - 1).isoformat()
    ended_yesterday = {"rule_id": "R-OLD", "active": True, "valid_until": yesterday}
    starts_today = {"rule_id": "R-NEW", "active": True, "valid_from": today.isoformat()}

    db = mongomock.MongoClient()["promo_ist"]
    db["promo_rules"].insert_many([dict(ended_yesterday), dict(starts_today)])

    with freeze_time(instant, tick=True):
        assert promotions._rule_active_now(ended_yesterday) is False
        assert promotions._rule_active_now(starts_today) is True
        got = [r["rule_id"] for r in promotions.get_active_rules_for_store(db, "BV-DHN-01")]
    assert got == ["R-NEW"]


# ---------------------------------------------------------------- CLK-P02
def test_date_only_range_bounds_are_ist_days():
    from api.routers import finance

    assert finance._parse_range_dt("2026-11-01") == datetime(2026, 10, 31, 18, 30)
    assert finance._parse_range_dt("2026-10-31", end=True) == datetime(
        2026, 10, 31, 18, 29, 59, 999999
    )


def test_offset_iso_bound_is_converted_not_truncated():
    from api.routers import finance

    # 10:30 IST is 05:00 UTC, the frame created_at is stored in.
    assert finance._parse_range_dt("2026-03-01T10:30:00+05:30") == datetime(2026, 3, 1, 5, 0)


@pytest.mark.parametrize(
    "stored, month",
    [
        (datetime(2026, 10, 31, 20, 30), "nov"),  # NIGHT order: 02:00 IST 1 Nov
        (datetime(2026, 10, 31, 9, 0), "oct"),  # DAY control: 14:30 IST 31 Oct
    ],
)
def test_monthly_export_window_buckets_by_ist_day(stored, month):
    from api.routers import finance

    db = mongomock.MongoClient()["range_ist"]
    db["orders"].insert_one({"order_id": "O1", "created_at": stored})

    def hits(frm, to):
        m: dict = {}
        finance._apply_created_at_range(m, frm, to)
        return db["orders"].count_documents(m)

    in_oct = hits("2026-10-01", "2026-10-31")
    in_nov = hits("2026-11-01", "2026-11-30")
    assert (in_oct, in_nov) == ((1, 0) if month == "oct" else (0, 1))


def test_journal_jv_export_keeps_calendar_entry_dates():
    """entry_date is a calendar-midnight accounting day, not a stored instant:
    the JV export must bound it by calendar day (_je_cal_day), not by the
    IST-shifted created_at bounds."""
    from api.routers.finance import _shared

    lo, hi = _shared._je_cal_day("2026-10-01"), _shared._je_cal_day("2026-10-31", end=True)
    assert lo <= datetime(2026, 10, 31) <= hi
    assert not (lo <= datetime(2026, 11, 1) <= hi)


# ---------------------------------------------------------------- CLK-P03
class _Proxy:
    def __init__(self, db):
        self._db = db
        self.is_connected = True

    def get_collection(self, n):
        return self._db[n]

    def __getitem__(self, n):
        return self._db[n]

    def __getattr__(self, n):
        return self._db[n]


@pytest.fixture
def finance_get(monkeypatch):
    from api.routers import finance as finance_pkg
    from api.routers.auth import get_current_user
    from api.routers.finance import cash_flow as cf_mod

    db = mongomock.MongoClient()["cash_flow_ist"]
    proxy = _Proxy(db)
    for mod in (finance_pkg, cf_mod):
        if hasattr(mod, "_get_db"):
            monkeypatch.setattr(mod, "_get_db", lambda: proxy)
    app = FastAPI()
    app.include_router(finance_pkg.router, prefix="/finance")
    tc = TestClient(app)
    admin = {"user_id": "u-admin", "username": "admin", "roles": ["ADMIN"],
             "store_ids": [], "active_store_id": None}

    async def _u():
        return dict(admin)

    app.dependency_overrides[get_current_user] = _u

    def _get(path):
        r = tc.get(path)
        assert r.status_code == 200, (path, r.status_code, r.text)
        return r.json()

    return db, _get


@pytest.mark.parametrize("instant", [NIGHT, DAY])
def test_month_to_date_expenses_start_on_the_ist_first(finance_get, instant):
    db, get = finance_get
    first = IST_TODAY[instant].replace(day=1)
    prev_last = date.fromordinal(first.toordinal() - 1)
    db["expenses"].insert_many([
        {"expense_id": "E-PREV", "store_id": "BV-DHN-01", "amount": 1000.0,
         "status": "APPROVED", "category": "RENT", "expense_date": prev_last.isoformat()},
        {"expense_id": "E-THIS", "store_id": "BV-DHN-01", "amount": 250.0,
         "status": "APPROVED", "category": "RENT", "expense_date": first.isoformat()},
    ])
    with freeze_time(instant, tick=True):
        cf = get("/finance/cash-flow")
        dash = get("/finance/owner-dashboard")
    assert cf["expense_outflow"] == 250.0
    assert dash["this_month"]["expenses"] == 250.0


# ---------------------------------------------------------------- CLK-P17
@pytest.mark.parametrize("instant", [NIGHT, DAY])
def test_debit_note_voucher_date_fallback_is_ist(instant):
    from api.services import rtv_debit_note

    note = {"debit_note_number": "DN-1", "vendor": {"name": "V"}, "totals": {},
            "accounting_status": "POSTED", "confirmation_id": "C-1"}
    with freeze_time(instant, tick=True):
        xml = rtv_debit_note._tally_debit_note_voucher(note)
    assert f"<DATE>{IST_TODAY[instant].strftime('%Y%m%d')}</DATE>" in xml
