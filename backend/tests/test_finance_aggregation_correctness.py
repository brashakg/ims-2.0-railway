"""
IMS 2.0 - Finance aggregation correctness regressions
======================================================
Locks in the Phase "harden-finance" fixes to backend/api/routers/finance.py:

  1. Order aggregations EXCLUDE DRAFT/CANCELLED orders (status $nin filter).
     Orders have a lifecycle status (orders.OrderStatus); a DRAFT was never
     booked and a CANCELLED was reversed -- neither is real revenue / tax /
     GST liability / cash inflow. The finance router previously had NO status
     filter, so those polluted every figure. reports.py already filters them;
     finance.py now matches that convention.

  2. Date-range filters on `created_at` compare datetime-to-datetime.
     Orders persist `created_at` as a BSON datetime (BaseRepository
     _add_timestamps -> datetime.now()), so the old '.isoformat()' / bare
     'YYYY-MM-DD' STRING bounds matched NOTHING -> every date-ranged revenue /
     P&L / GST / Tally figure read zero. _parse_range_dt / _apply_created_at_range
     convert the bounds to datetimes (end bound -> end-of-day, inclusive).

  3. GSTR / GST-summary December due dates roll into the NEXT year. The old
     `datetime(y, 1, 11)` for December kept the SAME year, putting the due date
     in the past.

  4. gst_reconciliation classifies inter-state sales as IGST (seller state !=
     buyer state) instead of always splitting CGST/SGST 50/50 -- the GSTR-1/3B
     rule. Empty state maps fall back to intra-state (prior behaviour).

These are deterministic: a tiny fake DB captures the Mongo match dicts so the
endpoint-level assertions need no live Mongo. Pure-function checks need none.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import finance  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402


# ============================================================================
# Fake Mongo: records every match dict passed to find()/aggregate() so we can
# assert the status filter + datetime bounds without a live database.
# ============================================================================


class _FakeCollection:
    def __init__(self, name, recorder, docs=None):
        self._name = name
        self._recorder = recorder
        self._docs = docs or []

    # find(filter, projection) -> iterable of docs
    def find(self, filter=None, projection=None, *args, **kwargs):
        self._recorder.setdefault(self._name, []).append(dict(filter or {}))
        return list(self._docs)

    def find_one(self, filter=None, projection=None, *args, **kwargs):
        self._recorder.setdefault(self._name + "#find_one", []).append(
            dict(filter or {})
        )
        return None

    def aggregate(self, pipeline, *args, **kwargs):
        match = {}
        for stage in pipeline or []:
            if "$match" in stage:
                match = stage["$match"]
                break
        self._recorder.setdefault(self._name, []).append(dict(match))
        return []  # empty -> endpoints fall back to zeros, which is fine


class _FakeDB:
    def __init__(self, recorder, docs_by_coll=None):
        self._recorder = recorder
        self._docs = docs_by_coll or {}

    def get_collection(self, name):
        return _FakeCollection(name, self._recorder, self._docs.get(name))

    # Some helpers use db["coll"] subscripting; finance.py uses get_collection.
    def __getitem__(self, name):
        return self.get_collection(name)


@pytest.fixture(autouse=True)
def _isolate_get_db():
    """Snapshot + restore finance._get_db so tests don't leak the fake DB."""
    original = finance._get_db
    yield
    finance._get_db = original


def _finance_client(recorder, roles=("ADMIN",), store_id="store-fin-test"):
    app = FastAPI()
    app.include_router(finance.router, prefix="/finance")

    async def _fake_user():
        return {
            "user_id": "u1",
            "full_name": "Test User",
            "active_store_id": store_id,
            "store_ids": [store_id],
            "roles": list(roles),
        }

    app.dependency_overrides[get_current_user] = _fake_user
    finance._get_db = lambda: _FakeDB(recorder)  # type: ignore[assignment]
    return TestClient(app)


def _all_order_matches(recorder):
    """Every match dict recorded against the `orders` collection."""
    return recorder.get("orders", [])


def _all_expense_matches(recorder):
    """Every match dict recorded against the `expenses` collection."""
    return recorder.get("expenses", [])


def _excludes_draft_cancelled(match):
    st = match.get("status")
    return isinstance(st, dict) and set(st.get("$nin", [])) >= {"CANCELLED", "DRAFT"}


# ============================================================================
# 1. Pure helpers: _parse_range_dt / _apply_created_at_range
# ============================================================================


class TestParseRangeDt:
    def test_date_only_start_is_midnight(self):
        dt = finance._parse_range_dt("2026-03-01", end=False)
        assert dt == datetime(2026, 3, 1, 0, 0, 0, 0)

    def test_date_only_end_is_end_of_day(self):
        dt = finance._parse_range_dt("2026-03-31", end=True)
        assert dt == datetime(2026, 3, 31, 23, 59, 59, 999999)

    def test_returns_datetime_not_string(self):
        # The whole bug: a string bound never matches a BSON datetime field.
        assert isinstance(finance._parse_range_dt("2026-03-01"), datetime)

    def test_empty_and_none_are_none(self):
        assert finance._parse_range_dt(None) is None
        assert finance._parse_range_dt("") is None
        assert finance._parse_range_dt("   ") is None

    def test_full_iso_with_tz_is_made_naive(self):
        dt = finance._parse_range_dt("2026-03-01T10:30:00+05:30")
        assert dt.tzinfo is None
        assert (dt.year, dt.month, dt.day, dt.hour) == (2026, 3, 1, 10)

    def test_apply_range_uses_datetimes(self):
        match: dict = {}
        finance._apply_created_at_range(match, "2026-03-01", "2026-03-31")
        ca = match["created_at"]
        assert isinstance(ca["$gte"], datetime)
        assert isinstance(ca["$lte"], datetime)
        assert ca["$gte"] < ca["$lte"]

    def test_apply_range_omits_missing_bound(self):
        match: dict = {}
        finance._apply_created_at_range(match, "2026-03-01", None)
        assert "$gte" in match["created_at"]
        assert "$lte" not in match["created_at"]


# ============================================================================
# 2. gst_reconciliation intra/inter-state classification (IGST)
# ============================================================================


class TestGstReconciliationStateSplit:
    def test_inter_state_sale_is_igst(self):
        # Seller Maharashtra, buyer Karnataka -> inter-state -> IGST, no CGST/SGST.
        orders = [{"store_id": "S1", "customer_id": "C1", "tax_amount": 100.0}]
        r = finance.gst_reconciliation(
            orders,
            [],
            {"S1": "E1"},
            {"E1": "Entity One"},
            store_state_by_id={"S1": "Maharashtra"},
            customer_state_by_id={"C1": "Karnataka"},
        )
        e1 = r["entities"][0]
        assert e1["igst"] == 100.0
        assert e1["cgst"] == 0.0 and e1["sgst"] == 0.0
        assert e1["gst_collected"] == 100.0
        assert r["total_collected"] == 100.0

    def test_intra_state_sale_is_cgst_sgst(self):
        orders = [{"store_id": "S1", "customer_id": "C1", "tax_amount": 100.0}]
        r = finance.gst_reconciliation(
            orders,
            [],
            {"S1": "E1"},
            {},
            store_state_by_id={"S1": "Maharashtra"},
            customer_state_by_id={"C1": "Maharashtra"},
        )
        e1 = r["entities"][0]
        assert e1["cgst"] == 50.0 and e1["sgst"] == 50.0
        assert e1["igst"] == 0.0
        assert e1["gst_collected"] == 100.0

    def test_mixed_intra_and_inter_in_one_entity(self):
        orders = [
            {"store_id": "S1", "customer_id": "C_local", "tax_amount": 80.0},
            {"store_id": "S1", "customer_id": "C_far", "tax_amount": 60.0},
        ]
        r = finance.gst_reconciliation(
            orders,
            [],
            {"S1": "E1"},
            {},
            store_state_by_id={"S1": "Maharashtra"},
            customer_state_by_id={
                "C_local": "Maharashtra",
                "C_far": "Gujarat",
            },
        )
        e1 = r["entities"][0]
        assert e1["cgst"] == 40.0 and e1["sgst"] == 40.0  # 80 intra split
        assert e1["igst"] == 60.0  # 60 inter
        assert e1["gst_collected"] == 140.0
        # net_payable = collected - input_credit (0 here)
        assert e1["net_payable"] == 140.0

    def test_empty_state_maps_fall_back_to_intra(self):
        # No state info anywhere -> every sale intra-state (prior behaviour),
        # so cgst + sgst == gst_collected and igst == 0.
        orders = [{"store_id": "S1", "customer_id": "C1", "tax_amount": 100.0}]
        r = finance.gst_reconciliation(orders, [], {"S1": "E1"}, {})
        e1 = r["entities"][0]
        assert e1["igst"] == 0.0
        assert e1["cgst"] == 50.0 and e1["sgst"] == 50.0
        assert round(e1["cgst"] + e1["sgst"], 2) == e1["gst_collected"]

    def test_unknown_buyer_state_is_intra(self):
        # Buyer with no state on file (walk-in) -> treated as intra-state.
        orders = [{"store_id": "S1", "customer_id": "", "tax_amount": 100.0}]
        r = finance.gst_reconciliation(
            orders,
            [],
            {"S1": "E1"},
            {},
            store_state_by_id={"S1": "Maharashtra"},
            customer_state_by_id={},
        )
        e1 = r["entities"][0]
        assert e1["igst"] == 0.0
        assert e1["cgst"] == 50.0 and e1["sgst"] == 50.0


# ============================================================================
# 3. Endpoint status-filter + datetime-bound regressions (fake DB)
# ============================================================================


def test_revenue_excludes_draft_cancelled_and_uses_datetime():
    rec: dict = {}
    client = _finance_client(rec)
    resp = client.get("/finance/revenue?period=month")
    assert resp.status_code == 200
    matches = _all_order_matches(rec)
    assert matches, "expected at least one orders query"
    assert all(_excludes_draft_cancelled(m) for m in matches)
    # The current-period bound must be a datetime, not an ISO string.
    cur = matches[0]["created_at"]["$gte"]
    assert isinstance(cur, datetime)


def test_pnl_excludes_draft_cancelled_and_uses_datetime_range():
    rec: dict = {}
    client = _finance_client(rec)
    resp = client.get(
        "/finance/pnl?store_id=store-fin-test"
        "&from_date=2026-03-01&to_date=2026-03-31"
    )
    assert resp.status_code == 200
    matches = _all_order_matches(rec)
    assert matches
    for m in matches:
        assert _excludes_draft_cancelled(m)
        ca = m.get("created_at", {})
        assert isinstance(ca.get("$gte"), datetime)
        assert isinstance(ca.get("$lte"), datetime)


def test_cash_flow_inflow_excludes_draft_cancelled():
    rec: dict = {}
    client = _finance_client(rec)
    resp = client.get("/finance/cash-flow?period=month")
    assert resp.status_code == 200
    # The orders (inflow) query must carry both the PAID payment_status AND the
    # status $nin -- a cancelled order is not cash in.
    order_matches = _all_order_matches(rec)
    inflow = [m for m in order_matches if "payment_status" in m]
    assert inflow, "expected an inflow query keyed on payment_status"
    assert all(_excludes_draft_cancelled(m) for m in inflow)


def test_outstanding_ar_excludes_cancelled():
    rec: dict = {}
    client = _finance_client(rec)
    resp = client.get("/finance/outstanding")
    assert resp.status_code == 200
    matches = _all_order_matches(rec)
    assert matches
    assert all(_excludes_draft_cancelled(m) for m in matches)


def test_pnl_by_category_excludes_draft_cancelled_and_uses_datetime():
    rec: dict = {}
    client = _finance_client(rec)
    resp = client.get(
        "/finance/pnl/by-category?from_date=2026-03-01&to_date=2026-03-31"
    )
    assert resp.status_code == 200
    matches = _all_order_matches(rec)
    assert matches
    for m in matches:
        assert _excludes_draft_cancelled(m)
        assert isinstance(m["created_at"]["$gte"], datetime)


def test_tally_sales_jv_excludes_draft_cancelled():
    rec: dict = {}
    client = _finance_client(rec)
    resp = client.get(
        "/finance/tally/sales-jv?from_date=2026-03-01&to_date=2026-03-31"
    )
    assert resp.status_code == 200
    matches = _all_order_matches(rec)
    assert matches
    assert all(_excludes_draft_cancelled(m) for m in matches)
    # And a real datetime range so the export isn't silently empty.
    assert isinstance(matches[0]["created_at"]["$gte"], datetime)


def test_gst_reconciliation_endpoint_excludes_draft_cancelled():
    rec: dict = {}
    client = _finance_client(rec)
    resp = client.get("/finance/gst/reconciliation?month=3&year=2026")
    assert resp.status_code == 200
    matches = _all_order_matches(rec)
    assert matches
    # Orders query (keyed on created_at range) must exclude draft/cancelled and
    # use datetime bounds.
    order_q = next(m for m in matches if "created_at" in m and "status" in m)
    assert _excludes_draft_cancelled(order_q)
    assert isinstance(order_q["created_at"]["$gte"], datetime)


# ============================================================================
# 4. GST summary due-date roll-over (December -> next year)
# ============================================================================


def test_gst_summary_december_due_dates_roll_into_next_year():
    rec: dict = {}
    client = _finance_client(rec)
    resp = client.get("/finance/gst/summary?month=12&year=2025")
    assert resp.status_code == 200
    body = resp.json()
    # Dec-2025 GSTR-1 due 2026-01-11, GSTR-3B due 2026-01-20 -- NOT 2025-01-*.
    assert body["gstr1_due_date"].startswith("2026-01-11")
    assert body["gstr3b_due_date"].startswith("2026-01-20")


def test_gst_summary_midyear_due_dates_next_month_same_year():
    rec: dict = {}
    client = _finance_client(rec)
    resp = client.get("/finance/gst/summary?month=3&year=2026")
    assert resp.status_code == 200
    body = resp.json()
    assert body["gstr1_due_date"].startswith("2026-04-11")
    assert body["gstr3b_due_date"].startswith("2026-04-20")


def test_gst_summary_orders_query_excludes_draft_cancelled():
    rec: dict = {}
    client = _finance_client(rec)
    resp = client.get("/finance/gst/summary?month=3&year=2026")
    assert resp.status_code == 200
    matches = _all_order_matches(rec)
    assert matches
    assert all(_excludes_draft_cancelled(m) for m in matches)


# ============================================================================
# 5. Expenses are dated on `expense_date`, not `date` (owner dashboard +
#    cash-flow forecast were querying the wrong field -> expenses read 0).
# ============================================================================


def test_owner_dashboard_expenses_use_expense_date_field():
    rec: dict = {}
    client = _finance_client(rec, roles=("ADMIN",))
    resp = client.get("/finance/owner-dashboard")
    assert resp.status_code == 200
    exp_matches = _all_expense_matches(rec)
    assert exp_matches, "expected an expenses query"
    for m in exp_matches:
        # The bug: filtering on `date` (which expenses never carry) zeroed the
        # month's expenses and overstated net cash flow.
        assert "date" not in m
        assert "expense_date" in m


def test_cash_flow_forecast_recurring_estimate_uses_expense_date():
    rec: dict = {}
    client = _finance_client(rec, roles=("ADMIN",))
    resp = client.get("/finance/cash-flow-forecast?days=90")
    assert resp.status_code == 200
    exp_matches = _all_expense_matches(rec)
    assert exp_matches, "expected an expenses query for the recurring estimate"
    for m in exp_matches:
        assert "date" not in m
        assert "expense_date" in m


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
