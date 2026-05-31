"""
IMS 2.0 - Reports / Analytics correctness regression tests
==========================================================
Covers the improvement-initiative "reports/analytics correctness" items:

  [S4]  /reports/dashboard returned a hard-coded "change": 12.5 -> now a real
        today-vs-yesterday delta (null when no yesterday baseline).
  [S5]  BSON-datetime-vs-ISO-STRING created_at filters silently zero/under-
        reported, and `str_value[:10]` on a datetime TypeError'd. The window
        helpers must use real datetime bounds and a crash-proof day-key.
  [S6]  analytics _norm_order revenue must prefer grand_total over the
        pre-discount subtotal; window fetch must NOT be capped at 500.
  [ROLE-GATE] enterprise analytics endpoints must 403 a non-manager role.

The helper-level tests run with NO database (pure functions + a fake repo) so
they are deterministic on DB-less local runs AND on CI's mongo:7.0. The
role-gate HTTP tests assert the 403 which fires BEFORE any DB access.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")

from api.routers import analytics as an  # noqa: E402
from api.routers import reports as rep  # noqa: E402


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeOrderRepo:
    """In-memory order repo that applies the SAME created_at $gte/$lte datetime
    bracketing Mongo does -- so a string bound would match nothing (proving the
    bug) and a datetime bound matches datetime-typed rows (proving the fix).
    Also honours `store_id` equality and `limit` (0 => no cap)."""

    def __init__(self, orders):
        self._orders = orders

    def find_many(self, flt=None, sort=None, skip=0, limit=100):
        flt = flt or {}
        out = []
        rng = flt.get("created_at")
        want_store = flt.get("store_id")
        for o in self._orders:
            if want_store is not None and o.get("store_id") != want_store:
                continue
            ca = o.get("created_at")
            if isinstance(rng, dict):
                gte = rng.get("$gte")
                lte = rng.get("$lte")
                # Mongo type-brackets: a datetime bound never matches a str
                # field, and vice-versa. Emulate that so the test is faithful.
                if gte is not None:
                    if type(ca) is not type(gte) or ca < gte:
                        continue
                if lte is not None:
                    if type(ca) is not type(lte) or ca > lte:
                        continue
            out.append(o)
        if limit:  # 0 => no cap
            out = out[:limit]
        return out

    def find_by_store(self, store_id, from_date=None, to_date=None, status=None):
        return [o for o in self._orders if o.get("store_id") == store_id][:500]


# ===========================================================================
# [S5] created_at window helpers
# ===========================================================================


def test_created_range_builds_datetime_bounds_not_strings():
    from api.routers import analytics_v2 as av2

    start = datetime(2026, 5, 1)
    end = datetime(2026, 5, 31, 23, 59, 59)
    out = av2._created_range(start, end)
    assert isinstance(out["$gte"], datetime)
    assert isinstance(out["$lte"], datetime)
    # A datetime is not a str -- the old code passed .isoformat() strings here.
    assert not isinstance(out["$gte"], str)


def test_day_key_handles_datetime_string_and_none():
    from api.routers import analytics_v2 as av2

    assert av2._day_key(datetime(2026, 5, 20, 14, 30)) == "2026-05-20"
    assert av2._day_key("2026-05-20T14:30:00") == "2026-05-20"
    assert av2._day_key(None) == ""
    # Critically, this does NOT raise on a datetime (the old `[:10]` slice did).


def test_fetch_window_returns_nonzero_on_datetime_created_at():
    """The core S5 assertion: a window query returns non-zero rows when
    created_at is a real datetime. A string `.isoformat()` bound (the old code)
    would have matched nothing under Mongo's type bracketing."""
    now = datetime(2026, 5, 15, 12, 0, 0)
    orders = [
        {"store_id": "S1", "created_at": now, "grand_total": 1000},
        {"store_id": "S1", "created_at": now - timedelta(days=2), "grand_total": 500},
        {"store_id": "S1", "created_at": now - timedelta(days=40), "grand_total": 9},
    ]
    repo = FakeOrderRepo(orders)
    got = an._fetch_orders_in_window(
        repo,
        store_id="S1",
        start=now - timedelta(days=7),
        end=now + timedelta(days=1),
    )
    assert len(got) == 2  # the 40-day-old order is outside the window
    assert sum(o["total_amount"] for o in got) == 1500


def test_fetch_window_string_created_at_bound_would_miss():
    """Faithfulness check: with the OLD string bound the same datetime rows
    match nothing -- documenting exactly why the report read zero."""
    now = datetime(2026, 5, 15, 12, 0, 0)
    orders = [{"store_id": "S1", "created_at": now, "grand_total": 1000}]
    repo = FakeOrderRepo(orders)
    # Simulate the pre-fix filter: ISO STRING bounds against datetime rows.
    miss = repo.find_many(
        {
            "store_id": "S1",
            "created_at": {
                "$gte": (now - timedelta(days=7)).isoformat(),
                "$lte": (now + timedelta(days=1)).isoformat(),
            },
        },
        limit=0,
    )
    assert miss == []  # string-vs-datetime never matched -> silent zero


def test_fetch_window_is_not_capped_at_500():
    """[S6] find_by_store hard-capped at 500; the window helper uses limit=0
    (no cap) so a busy store's total is not truncated."""
    now = datetime(2026, 5, 15, 12, 0, 0)
    orders = [
        {"store_id": "S1", "created_at": now, "grand_total": 1} for _ in range(750)
    ]
    repo = FakeOrderRepo(orders)
    got = an._fetch_orders_in_window(
        repo, store_id="S1", start=now - timedelta(days=1), end=now + timedelta(days=1)
    )
    assert len(got) == 750  # not clipped to 500


# ===========================================================================
# [S6] analytics revenue normaliser prefers grand_total
# ===========================================================================


def test_norm_order_prefers_grand_total_over_subtotal():
    """subtotal is the PRE-discount gross and must NOT win over grand_total."""
    o = {"store_id": "S1", "grand_total": 900, "subtotal": 1000}
    assert an._norm_order(o)["total_amount"] == 900


def test_norm_order_falls_back_to_subtotal_only_when_no_total():
    o = {"store_id": "S1", "subtotal": 1000}
    assert an._norm_order(o)["total_amount"] == 1000


def test_norm_order_reads_camel_grand_total():
    o = {"storeId": "S1", "grandTotal": 750}
    assert an._norm_order(o)["total_amount"] == 750


# ===========================================================================
# [S4] reports dashboard change = real today-vs-yesterday delta
# ===========================================================================


def _run_dashboard(monkeypatch, orders):
    """Drive reports.dashboard_stats with a fake order repo and no stock /
    customer / task repos (so only the sales/change path exercises)."""
    repo = FakeOrderRepo(orders)
    # find_by_store on the dashboard ignores date args -> return all store rows.
    monkeypatch.setattr(rep, "get_order_repository", lambda: repo)
    monkeypatch.setattr(rep, "get_stock_repository", lambda: None)
    monkeypatch.setattr(rep, "get_customer_repository", lambda: None)
    monkeypatch.setattr(rep, "get_task_repository", lambda: None)

    import asyncio

    return asyncio.run(
        rep.dashboard_stats(store_id="S1", current_user={"active_store_id": "S1"})
    )


def test_dashboard_change_is_real_today_vs_yesterday(monkeypatch):
    today = datetime.now().replace(hour=10, minute=0, second=0, microsecond=0)
    yesterday = today - timedelta(days=1)
    orders = [
        {"store_id": "S1", "status": "CONFIRMED", "created_at": today, "grand_total": 1200},
        {"store_id": "S1", "status": "CONFIRMED", "created_at": yesterday, "grand_total": 1000},
    ]
    res = _run_dashboard(monkeypatch, orders)
    # (1200 - 1000) / 1000 * 100 = 20.0 -- NOT the old hard-coded 12.5
    assert res["change"] == 20.0
    assert res["totalSales"] == 2200


def test_dashboard_change_null_when_no_yesterday_sales(monkeypatch):
    today = datetime.now().replace(hour=10, minute=0, second=0, microsecond=0)
    orders = [
        {"store_id": "S1", "status": "CONFIRMED", "created_at": today, "grand_total": 1200},
    ]
    res = _run_dashboard(monkeypatch, orders)
    # No yesterday baseline -> null (frontend renders "-"), never a fake number.
    assert res["change"] is None


# ===========================================================================
# [ROLE-GATE] enterprise analytics endpoints reject a non-manager role
# ===========================================================================


def test_enterprise_kpis_forbidden_for_sales_staff(client, staff_headers):
    """A SALES_STAFF token must be 403'd by the new _ANALYTICS_ROLES gate. The
    gate fires before any DB access, so this holds with or without Mongo."""
    resp = client.get("/api/v1/analytics/enterprise-kpis", headers=staff_headers)
    assert resp.status_code == 403


def test_store_performance_forbidden_for_sales_staff(client, staff_headers):
    resp = client.get("/api/v1/analytics/store-performance", headers=staff_headers)
    assert resp.status_code == 403


def test_dashboard_summary_allowed_for_superadmin(client, auth_headers):
    """SUPERADMIN must NOT be 403'd (auto-passes require_roles). Any non-403
    status (200, or 500 in a DB-less stub) proves the gate let them through."""
    resp = client.get(
        "/api/v1/analytics/dashboard-summary", headers=auth_headers
    )
    assert resp.status_code != 403
