"""IMS 2.0 - F24 Optometrist -> retail conversion dashboard tests (#24).

INTENT-LEVEL: a hollow shell that returns zeros without actually joining
``eye_tests`` to ``orders`` FAILS tests 1, 2, 6, 8, 10. Revenue role-gating
(DECISIONS sec 3 LOCKED) is asserted at the value level: OPTOMETRIST gets
``None`` for revenue, never the rupee figure and never ``0``.

Covers:
  1. 7-day window: order at day 6 converts; day 8 does not (boundary).
  2. status NOT IN [CANCELLED, DRAFT] -> those orders never convert.
  3. most-recent-test attribution (the later test gets credit, not the earlier).
  4. OPTOMETRIST caller -> NO revenue field value (role-strip: None, not 0).
  5. STORE_MANAGER caller -> real revenue + avg order value.
  6. unattributed tests (no customer_id) counted separately.
  7. fail-soft on missing repos.
  8. RBAC: existing /optometrist/{id}/stats route is no longer AUTHENTICATED-open
     (gap closed) + OPTOMETRIST self-scope; conversion-dashboard catalogued.
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta
from typing import Any, Dict, List

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

from api.services import conversion_analytics as conv  # noqa: E402
from api.services import rbac_policy as rbac  # noqa: E402


# ---------------------------------------------------------------------------
# Fake repos (find_many) mirroring the BaseRepository pattern. Each applies the
# real Mongo-ish filter so the service's $in / $gte / $lte / $nin behave.
# ---------------------------------------------------------------------------
class _FakeRepo:
    def __init__(self, docs: List[Dict[str, Any]]):
        self._docs = docs

    def find_many(self, flt: Dict[str, Any], sort=None, skip: int = 0, limit: int = 100):
        out = [d for d in self._docs if self._match(d, flt or {})]
        return out

    @staticmethod
    def _match(doc: Dict[str, Any], flt: Dict[str, Any]) -> bool:
        for key, cond in flt.items():
            val = doc.get(key)
            if isinstance(cond, dict):
                if "$in" in cond and val not in cond["$in"]:
                    return False
                if "$nin" in cond and val in cond["$nin"]:
                    return False
                if "$gte" in cond and not (val is not None and val >= cond["$gte"]):
                    return False
                if "$lte" in cond and not (val is not None and val <= cond["$lte"]):
                    return False
            else:
                if val != cond:
                    return False
        return True


_FROM = date(2026, 6, 1)
_TO = date(2026, 6, 30)


def _test(test_id, optometrist_id, customer_id, completed_at, *, test_date="2026-06-10",
          status="COMPLETED", store_id="BV-1", name=None):
    return {
        "test_id": test_id,
        "optometrist_id": optometrist_id,
        "optometrist_name": name or optometrist_id,
        "customer_id": customer_id,
        "completed_at": completed_at.isoformat() if isinstance(completed_at, datetime) else completed_at,
        "test_date": test_date,
        "status": status,
        "store_id": store_id,
    }


def _order(customer_id, created_at, grand_total, *, status="CONFIRMED",
           order_number="ORD-1", store_id="BV-1"):
    return {
        "customer_id": customer_id,
        "created_at": created_at,
        "grand_total": grand_total,
        "status": status,
        "order_number": order_number,
        "store_id": store_id,
    }


# ---------------------------------------------------------------------------
# 1. 7-day window boundary
# ---------------------------------------------------------------------------
def test_order_at_day_6_converts():
    t = datetime(2026, 6, 10, 12, 0, 0)
    tests = _FakeRepo([_test("T1", "O1", "C1", t)])
    orders = _FakeRepo([_order("C1", t + timedelta(days=6), 5000.0)])
    res = conv.get_conversion_dashboard(
        tests, orders, store_ids=["BV-1"], from_date=_FROM, to_date=_TO,
        conversion_window_days=7, include_revenue=True,
    )
    row = res["rows"][0]
    assert row["converted_count"] == 1
    assert row["conversion_rate_pct"] == 100.0
    assert row["revenue_attributed"] == 5000.0
    assert row["avg_days_to_order"] == 6


def test_order_at_day_8_does_not_convert():
    t = datetime(2026, 6, 10, 12, 0, 0)
    tests = _FakeRepo([_test("T1", "O1", "C1", t)])
    orders = _FakeRepo([_order("C1", t + timedelta(days=8), 5000.0)])
    res = conv.get_conversion_dashboard(
        tests, orders, store_ids=["BV-1"], from_date=_FROM, to_date=_TO,
        conversion_window_days=7, include_revenue=True,
    )
    row = res["rows"][0]
    assert row["converted_count"] == 0
    assert row["conversion_rate_pct"] == 0.0


# ---------------------------------------------------------------------------
# 2. status NOT IN [CANCELLED, DRAFT]
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("bad_status", ["CANCELLED", "DRAFT"])
def test_cancelled_and_draft_orders_do_not_convert(bad_status):
    t = datetime(2026, 6, 10, 12, 0, 0)
    tests = _FakeRepo([_test("T1", "O1", "C1", t)])
    orders = _FakeRepo([_order("C1", t + timedelta(days=2), 5000.0, status=bad_status)])
    res = conv.get_conversion_dashboard(
        tests, orders, store_ids=["BV-1"], from_date=_FROM, to_date=_TO,
        include_revenue=True,
    )
    assert res["rows"][0]["converted_count"] == 0


# ---------------------------------------------------------------------------
# 3. most-recent-test attribution
# ---------------------------------------------------------------------------
def test_most_recent_test_before_order_gets_credit():
    order_dt = datetime(2026, 6, 20, 10, 0, 0)
    # O1 saw the customer 10 days before the order; O2 saw them 3 days before.
    # The most-recent test (O2) gets the conversion credit.
    tests = _FakeRepo([
        _test("T_O1", "O1", "C1", order_dt - timedelta(days=10)),
        _test("T_O2", "O2", "C1", order_dt - timedelta(days=3)),
    ])
    orders = _FakeRepo([_order("C1", order_dt, 7000.0)])
    res = conv.get_conversion_dashboard(
        tests, orders, store_ids=["BV-1"], from_date=_FROM, to_date=_TO,
        include_revenue=True,
    )
    by_id = {r["optometrist_id"]: r for r in res["rows"]}
    assert by_id["O2"]["converted_count"] == 1
    assert by_id["O1"]["converted_count"] == 0
    assert by_id["O2"]["revenue_attributed"] == 7000.0
    assert by_id["O1"]["revenue_attributed"] == 0.0  # manager sees real 0, not None


# ---------------------------------------------------------------------------
# 4. OPTOMETRIST role-strip: revenue is None (present-but-null), never 0/value
# ---------------------------------------------------------------------------
def test_optometrist_caller_gets_no_revenue_value():
    t = datetime(2026, 6, 10, 12, 0, 0)
    tests = _FakeRepo([_test("T1", "O1", "C1", t)])
    orders = _FakeRepo([_order("C1", t + timedelta(days=3), 5000.0)])
    res = conv.get_conversion_dashboard(
        tests, orders, store_ids=["BV-1"], from_date=_FROM, to_date=_TO,
        include_revenue=False, optometrist_id_filter="O1",
    )
    row = res["rows"][0]
    # The non-revenue metrics still compute correctly.
    assert row["converted_count"] == 1
    assert row["conversion_rate_pct"] == 100.0
    # Revenue is STRIPPED: None, not 0, not the rupee figure.
    assert row["revenue_attributed"] is None
    assert row["avg_order_value"] is None
    assert res["store_summary"]["revenue_attributed"] is None
    # The per-order detail amount is also stripped.
    assert row["orders"][0]["amount"] is None
    assert row["orders"][0]["days_after_test"] == 3


def test_optometrist_filter_returns_only_own_row():
    t = datetime(2026, 6, 10, 12, 0, 0)
    tests = _FakeRepo([
        _test("T1", "O1", "C1", t),
        _test("T2", "O2", "C2", t),
    ])
    orders = _FakeRepo([])
    res = conv.get_conversion_dashboard(
        tests, orders, store_ids=["BV-1"], from_date=_FROM, to_date=_TO,
        include_revenue=False, optometrist_id_filter="O1",
    )
    assert len(res["rows"]) == 1
    assert res["rows"][0]["optometrist_id"] == "O1"


# ---------------------------------------------------------------------------
# 5. STORE_MANAGER (include_revenue=True) sees revenue + avg order value
# ---------------------------------------------------------------------------
def test_manager_sees_revenue_and_avg_order_value():
    t = datetime(2026, 6, 5, 12, 0, 0)
    tests = _FakeRepo([
        _test("T1", "O1", "C1", t),
        _test("T2", "O1", "C2", t),
    ])
    orders = _FakeRepo([
        _order("C1", t + timedelta(days=1), 4000.0, order_number="ORD-A"),
        _order("C2", t + timedelta(days=2), 6000.0, order_number="ORD-B"),
    ])
    res = conv.get_conversion_dashboard(
        tests, orders, store_ids=["BV-1"], from_date=_FROM, to_date=_TO,
        include_revenue=True,
    )
    row = res["rows"][0]
    assert row["converted_count"] == 2
    assert row["revenue_attributed"] == 10000.0
    assert row["avg_order_value"] == 5000.0
    assert res["store_summary"]["revenue_attributed"] == 10000.0
    assert res["store_summary"]["converted"] == 2


# ---------------------------------------------------------------------------
# 6. unattributed tests counted separately; rate over total completed
# ---------------------------------------------------------------------------
def test_unattributed_tests_counted_separately():
    t = datetime(2026, 6, 10, 12, 0, 0)
    tests = _FakeRepo([
        _test("T1", "O1", "C1", t),          # attributable + converts
        _test("T2", "O1", None, t),          # no customer -> unattributed
    ])
    orders = _FakeRepo([_order("C1", t + timedelta(days=1), 3000.0)])
    res = conv.get_conversion_dashboard(
        tests, orders, store_ids=["BV-1"], from_date=_FROM, to_date=_TO,
        include_revenue=True,
    )
    row = res["rows"][0]
    assert row["tests_completed"] == 2
    assert row["converted_count"] == 1
    assert row["unattributed_tests"] == 1
    # Rate denominator is total COMPLETED (2), conservative metric: 1/2 = 50%.
    assert row["conversion_rate_pct"] == 50.0


# ---------------------------------------------------------------------------
# 7. fail-soft
# ---------------------------------------------------------------------------
def test_fail_soft_on_missing_repos():
    assert conv.get_conversion_dashboard(
        None, None, store_ids=["BV-1"], from_date=_FROM, to_date=_TO,
    ) == {"store_summary": {}, "rows": []}
    # No store scope -> empty too.
    assert conv.get_conversion_dashboard(
        _FakeRepo([]), _FakeRepo([]), store_ids=[], from_date=_FROM, to_date=_TO,
    ) == {"store_summary": {}, "rows": []}


def test_one_order_credits_one_test_not_repeatedly():
    """A single order converts at most one test (no double-count across two
    tests by the same optometrist for the same customer)."""
    order_dt = datetime(2026, 6, 20, 10, 0, 0)
    tests = _FakeRepo([
        _test("Ta", "O1", "C1", order_dt - timedelta(days=5)),
        _test("Tb", "O1", "C1", order_dt - timedelta(days=2)),
    ])
    orders = _FakeRepo([_order("C1", order_dt, 5000.0)])
    res = conv.get_conversion_dashboard(
        tests, orders, store_ids=["BV-1"], from_date=_FROM, to_date=_TO,
        include_revenue=True,
    )
    row = res["rows"][0]
    # Two completed tests, one order -> exactly one conversion.
    assert row["tests_completed"] == 2
    assert row["converted_count"] == 1
    assert row["revenue_attributed"] == 5000.0


# ---------------------------------------------------------------------------
# 8. RBAC: the latent gap on the stats route is closed + dashboard catalogued
# ---------------------------------------------------------------------------
def test_stats_route_no_longer_authenticated_open():
    """The existing /optometrist/{id}/stats route had NO role guard (any
    authenticated caller incl. SALES_CASHIER). It must now be a role list."""
    entry = rbac.policy_for(
        "GET", "/api/v1/clinical/optometrist/O1/stats"
    )
    assert entry is not None
    allowed = entry["allowed"]
    assert isinstance(allowed, list), "stats route must be role-gated, not AUTHENTICATED"
    assert set(allowed) == {
        "ADMIN", "AREA_MANAGER", "OPTOMETRIST", "STORE_MANAGER", "SUPERADMIN",
    }
    # The gap closed: POS / non-clinical roles are excluded.
    for excluded in ("SALES_CASHIER", "SALES_STAFF", "CASHIER", "ACCOUNTANT",
                     "CATALOG_MANAGER", "WORKSHOP_STAFF"):
        assert excluded not in allowed


def test_conversion_dashboard_route_catalogued_and_gated():
    entry = rbac.policy_for("GET", "/api/v1/clinical/conversion-dashboard")
    assert entry is not None
    allowed = entry["allowed"]
    assert set(allowed) == {
        "ADMIN", "AREA_MANAGER", "OPTOMETRIST", "STORE_MANAGER", "SUPERADMIN",
    }


def test_sales_cashier_denied_both_routes():
    assert rbac.check_access(
        "GET", "/api/v1/clinical/conversion-dashboard", ["SALES_CASHIER"]
    ) is False
    assert rbac.check_access(
        "GET", "/api/v1/clinical/optometrist/O1/stats", ["SALES_CASHIER"]
    ) is False
    # Optometrist + manager allowed (route-level; self-scope enforced in handler).
    assert rbac.check_access(
        "GET", "/api/v1/clinical/conversion-dashboard", ["OPTOMETRIST"]
    ) is True
    assert rbac.check_access(
        "GET", "/api/v1/clinical/conversion-dashboard", ["STORE_MANAGER"]
    ) is True


# ---------------------------------------------------------------------------
# HTTP-level RBAC: the require_roles gate + the OPTOMETRIST self-scope 403 fire
# at the request layer BEFORE any DB access, so these run without a live Mongo.
# ---------------------------------------------------------------------------
def _token(roles, user_id="U1", store="BV-1"):
    from api.routers.auth import create_access_token

    return create_access_token(
        {
            "user_id": user_id,
            "username": user_id,
            "roles": roles,
            "store_ids": [store],
            "active_store_id": store,
        }
    )


def test_http_stats_403_for_non_clinical_role(client):
    headers = {"Authorization": f"Bearer {_token(['SALES_CASHIER'])}"}
    resp = client.get(
        "/api/v1/clinical/optometrist/O1/stats",
        params={"from_date": "2026-06-01", "to_date": "2026-06-30"},
        headers=headers,
    )
    assert resp.status_code == 403


def test_http_stats_403_when_optometrist_reads_another_id(client):
    # O1 (OPTOMETRIST) trying to read O2's stats -> self-scope 403.
    headers = {"Authorization": f"Bearer {_token(['OPTOMETRIST'], user_id='O1')}"}
    resp = client.get(
        "/api/v1/clinical/optometrist/O2/stats",
        params={"from_date": "2026-06-01", "to_date": "2026-06-30"},
        headers=headers,
    )
    assert resp.status_code == 403


def test_http_conversion_dashboard_403_for_non_clinical_role(client):
    headers = {"Authorization": f"Bearer {_token(['SALES_CASHIER'])}"}
    resp = client.get(
        "/api/v1/clinical/conversion-dashboard",
        params={"from_date": "2026-06-01", "to_date": "2026-06-30"},
        headers=headers,
    )
    assert resp.status_code == 403


def test_http_stats_requires_auth(client):
    resp = client.get(
        "/api/v1/clinical/optometrist/O1/stats",
        params={"from_date": "2026-06-01", "to_date": "2026-06-30"},
    )
    assert resp.status_code == 401
