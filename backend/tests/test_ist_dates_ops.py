"""IST clock sweep, lane B: operational screens read business dates in IST.

Prod runs UTC and stores naive-UTC instants; the business day / month / year
is IST. Between 00:00 and 05:30 IST the UTC date is still YESTERDAY (and on
1 Jan, last year). Every test here runs the same code at two instants on the
same UTC date:

  NIGHT 2026-12-31T20:00Z == 2027-01-01 01:30 IST  (UTC still says 31 Dec)
  DAY   2026-12-31T09:00Z == 2026-12-31 14:30 IST  (both clocks agree)

The NIGHT case failed before the fix; the DAY case is the control.
No emoji (Windows cp1252).
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import date, datetime

import pytest
from freezegun import freeze_time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

NIGHT = "2026-12-31T20:00:00Z"
DAY = "2026-12-31T09:00:00Z"
# (frozen instant, IST today) for both cases.
CASES = [(NIGHT, "2027-01-01"), (DAY, "2026-12-31")]

ADMIN = {"user_id": "u1", "username": "admin", "roles": ["SUPERADMIN"],
         "active_store_id": "S1", "store_ids": ["S1"]}


def _run(coro):
    return asyncio.run(coro)


class _Coll:
    """Records every filter it is handed; returns the canned docs."""

    def __init__(self, docs=None):
        self.docs = list(docs or [])
        self.queries = []

    def find(self, q=None, *a, **k):
        self.queries.append(q)
        return _Cursor(self.docs)

    def count_documents(self, q, *a, **k):
        self.queries.append(q)
        return 0


class _Cursor(list):
    def sort(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self


class _DB:
    is_connected = True

    def __init__(self, docs=None):
        self.colls = {}
        self._docs = docs or {}

    def get_collection(self, name):
        if name not in self.colls:
            self.colls[name] = _Coll(self._docs.get(name))
        return self.colls[name]


# --- CLK-P04 hr_self_service._month_year -----------------------------------


@pytest.mark.parametrize("instant,today", CASES)
def test_p04_self_service_month_year_is_ist(instant, today):
    from api.routers.hr_self_service import _month_year

    with freeze_time(instant, tick=True):
        assert _month_year(None, None) == (int(today[5:7]), int(today[:4]))


# --- CLK-P05 workshop dashboard KPIs ---------------------------------------


@pytest.mark.parametrize("instant,today", CASES)
def test_p05_workshop_kpis_use_ist_day(monkeypatch, instant, today):
    from api.routers.workshop import board

    yesterday = "2026-12-31" if today == "2027-01-01" else "2026-12-30"
    jobs = [
        # Due IST-yesterday -> overdue now.
        {"job_id": "a", "status": "PENDING", "store_id": "S1",
         "expected_date": yesterday},
        # Completed 23:00 IST on 31 Dec (17:30Z): IST-today only by DAY.
        {"job_id": "b", "status": "READY", "store_id": "S1",
         "completed_at": "2026-12-31T17:30:00"},
        # Completed 01:00 IST on 1 Jan (19:30Z 31 Dec): IST-today only at NIGHT.
        {"job_id": "c", "status": "READY", "store_id": "S1",
         "completed_at": datetime(2026, 12, 31, 19, 30)},
    ]

    class _Repo:
        def find_by_store(self, _sid):
            return jobs

    monkeypatch.setattr(board, "get_workshop_repository", lambda: _Repo())
    with freeze_time(instant, tick=True):
        body = _run(board.get_dashboard_kpis(store_id="S1", current_user=ADMIN))
    assert body["overdue"] == 1
    assert body["completed_today"] == 1


# --- CLK-P06 follow-ups -----------------------------------------------------


@pytest.mark.parametrize("instant,today", CASES)
def test_p06_follow_up_summary_and_due_today_bound_on_ist(monkeypatch, instant, today):
    from api.routers import follow_ups as fu

    db = _DB()
    monkeypatch.setattr(fu, "_get_db", lambda: db)
    with freeze_time(instant, tick=True):
        _run(fu.get_follow_up_summary(store_id="S1", current_user=ADMIN))
        _run(fu.get_due_today(store_id="S1", current_user=ADMIN))
    qs = db.colls["follow_ups"].queries
    overdue = [q for q in qs if "$lt" in (q.get("scheduled_date") or {})]
    assert overdue[0]["scheduled_date"]["$lt"] == today
    due = [q for q in qs if set((q.get("scheduled_date") or {})) == {"$lte"}]
    assert all(q["scheduled_date"]["$lte"] == today for q in due) and due


def test_p06_create_accepts_ist_today_at_night(monkeypatch):
    """At 01:30 IST on 1 Jan a follow-up for 31 Dec is in the past: refused."""
    from fastapi import HTTPException
    from api.routers import follow_ups as fu

    monkeypatch.setattr(fu, "_get_db", lambda: _DB())
    req = fu.CreateFollowUpRequest(
        customer_id="C1", customer_name="A", customer_phone="9000000000",
        store_id="S1", type="eye_test_reminder", scheduled_date="2026-12-31")
    with freeze_time(NIGHT, tick=True):
        with pytest.raises(HTTPException) as exc:
            _run(fu.create_follow_up(request=req, current_user=ADMIN))
    assert exc.value.status_code == 422


# --- CLK-P07 roster coverage-breach "today" ---------------------------------


@pytest.mark.parametrize("instant,today", CASES)
def test_p07_roster_today_is_ist(instant, today):
    from api.services.roster_engine import _today_iso

    with freeze_time(instant, tick=True):
        assert _today_iso() == today


# --- CLK-P08 analytics get_date_range('today') ------------------------------


@pytest.mark.parametrize("instant,today", CASES)
def test_p08_analytics_today_window_starts_at_ist_midnight(instant, today):
    from api.routers.analytics import get_date_range
    from api.utils.ist import ist_day_start_utc

    with freeze_time(instant, tick=True):
        start, _end = get_date_range("today")
    assert start == ist_day_start_utc(date.fromisoformat(today))


# --- CLK-P09 contact-lens days until expiry ---------------------------------


@pytest.mark.parametrize("instant", [NIGHT, DAY])
def test_p09_expiry_today_is_day_zero_all_day(instant):
    """A batch expiring on IST today is 0 days out (near-expiry, still
    sellable, same as the till) at every hour of that IST day."""
    from api.routers.inventory.helpers import (
        compute_days_until_expiry, partition_by_expiry,
    )

    with freeze_time(instant, tick=True):
        from api.utils.ist import ist_today

        today = ist_today().isoformat()
        assert compute_days_until_expiry(today) == 0
        out = partition_by_expiry([{"expiry_date": today}])
    assert len(out["near_expiry"]) == 1 and not out["expired"]


# --- CLK-P10 analytics_v2 discount_analysis bounds --------------------------


def test_p10_discount_analysis_covers_whole_ist_days(monkeypatch):
    from api.routers import analytics_v2

    db = _DB()
    monkeypatch.setattr(analytics_v2, "_get_db", lambda: db)
    _run(analytics_v2.discount_analysis(
        store_id="S1", date_from="2026-06-01", date_to="2026-06-30",
        current_user=ADMIN,
    ))
    rng = db.colls["orders"].queries[0]["created_at"]
    # IST 1 June 00:00 == 31 May 18:30Z; IST 30 June ends 30 June 18:30Z.
    assert rng["$gte"] == datetime(2026, 5, 31, 18, 30)
    assert datetime(2026, 6, 30, 18, 29) < rng["$lte"] < datetime(2026, 6, 30, 18, 30)


# --- CLK-P11 order number year ----------------------------------------------


@pytest.mark.parametrize("instant,today", CASES)
def test_p11_order_number_year_is_ist(instant, today):
    from api.routers.orders.numbering import generate_order_number

    with freeze_time(instant, tick=True):
        assert generate_order_number("BV-BOK-01").split("-")[2] == today[:4]


# --- CLK-P12 transfer number month ------------------------------------------


@pytest.mark.parametrize("instant,today", CASES)
def test_p12_transfer_number_month_is_ist(monkeypatch, instant, today):
    from api.routers import transfers

    monkeypatch.setattr(transfers, "_transfers_coll", lambda: None)
    with freeze_time(instant, tick=True):
        num = transfers.generate_transfer_number()
    assert num.split("-")[1] == today[:4] + today[5:7]


# --- CLK-P13 payout snapshots default year ----------------------------------


@pytest.mark.parametrize("instant,today", CASES)
def test_p13_payout_snapshot_default_year_is_ist(monkeypatch, instant, today):
    from api.routers import payout

    seen = {}

    class _Repo:
        def list_for_store_year(self, store, yr):
            seen["yr"] = yr
            return []

    monkeypatch.setattr(payout, "_check_view_permission", lambda u: None)
    monkeypatch.setattr(payout, "_resolve_store", lambda u, s: "S1")
    monkeypatch.setattr(payout, "_snapshot_repo", lambda: _Repo())
    with freeze_time(instant, tick=True):
        _run(payout.list_snapshots(current_user=ADMIN, store_id=None, year=None))
    assert seen["yr"] == int(today[:4])


# --- CLK-P14 workshop calendar defaults -------------------------------------


@pytest.mark.parametrize("instant,today", CASES)
def test_p14_fitting_details_default_order_date_is_ist(monkeypatch, instant, today):
    from api.routers.workshop import jobs
    from api.routers.workshop.models import FittingDetailsUpdate

    saved = {}

    class _Repo:
        def find_by_id(self, _id):
            return {"job_id": "J1", "store_id": "S1"}

        def update(self, _id, data):
            saved.update(data)
            return True

    monkeypatch.setattr(jobs, "get_workshop_repository", lambda: _Repo())
    payload = FittingDetailsUpdate(fitting_details={})
    with freeze_time(instant, tick=True):
        _run(jobs.update_fitting_details("J1", payload, current_user=ADMIN))
    fd = saved["fitting_details"]
    assert fd["order_date"] == today
    assert fd["order_time"] == ("01:30" if today == "2027-01-01" else "14:30")


@pytest.mark.parametrize("instant,today", CASES)
def test_p14_auto_job_default_expected_date_is_ist(monkeypatch, instant, today):
    import api.dependencies as deps
    import api.routers.orders as orders
    from api.routers.orders import _ensure_workshop_job_for_order

    created = []

    class _WRepo:
        def find_by_order(self, _oid):
            return []

        def create(self, data):
            created.append(data)
            return dict(data, job_id="J1")

    monkeypatch.setattr(deps, "get_workshop_repository", lambda: _WRepo())
    monkeypatch.setattr(orders, "get_order_repository", lambda: None)
    order = {"order_id": "O1", "store_id": "S1", "items": [
        {"item_type": "LENS", "product_id": "L1", "prescription_id": "RX1"}]}
    with freeze_time(instant, tick=True):
        _ensure_workshop_job_for_order(order, "u1")
    want = "2027-01-06" if today == "2027-01-01" else "2027-01-05"
    assert created[0]["expected_date"] == want


# --- CLK-P15 Jarvis live overview -------------------------------------------


@pytest.mark.parametrize("instant,today", CASES)
def test_p15_jarvis_overview_today_is_ist(monkeypatch, instant, today):
    from api.routers import jarvis

    orders = [
        # 00:30 IST 1 Jan (19:00Z 31 Dec): IST-today only at NIGHT.
        {"status": "DELIVERED", "grand_total": 100,
         "created_at": datetime(2026, 12, 31, 19, 0)},
        # 14:00 IST 31 Dec (08:30Z): IST-today only by DAY.
        {"status": "DELIVERED", "grand_total": 7,
         "created_at": datetime(2026, 12, 31, 8, 30)},
    ]
    db = _DB({"orders": orders})
    monkeypatch.setattr(jarvis, "get_db_collection", lambda n: db.get_collection(n) if n == "orders" else None)
    with freeze_time(instant, tick=True):
        out = jarvis.JarvisAnalyticsEngine._compute_overview_live()
    want = 100.0 if today == "2027-01-01" else 7.0
    assert out["revenue"]["today"] == want
