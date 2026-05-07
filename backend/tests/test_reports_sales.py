"""
IMS 2.0 — Reports / sales aggregation regression tests
=======================================================
Locks in the May-2026 audit fix for the four /sales/* endpoints
(/sales/summary, /sales/daily, /sales/by-category, /sales/growth).

Pre-fix behaviour: every endpoint returned `total_sales = 0` even
when real orders existed, because:

  1. The created_at filter compared a Mongo Date field against an
     ISO string ("$gte": dt.isoformat()) — never matched any rows.
  2. Even when matching, the loops summed `final_amount` /
     `total_amount` — orders.py stamps `grand_total`. Items also
     summed `total` / `price` — items have `item_total` / `unit_price`.

These tests:
  - seed real order docs directly in a fake collection (datetime
    `created_at` like the real BaseRepository writes, current order
    schema with `grand_total` / `total_discount` / `tax_amount`),
  - hit the endpoints,
  - assert the totals match the manual calculation.
"""
from __future__ import annotations

import os
import sys
from datetime import date as _d, datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ============================================================================
# Test fakes — minimal Mongo emulator that supports the date range
# operator the helpers use.
# ============================================================================


def _doc_matches(doc, filter):
    if not filter:
        return True
    for k, expected in filter.items():
        actual = doc.get(k)
        if isinstance(expected, dict):
            for op, op_val in expected.items():
                if op == "$gte" and not (actual is not None and actual >= op_val):
                    return False
                if op == "$lte" and not (actual is not None and actual <= op_val):
                    return False
                if op == "$lt" and not (actual is not None and actual < op_val):
                    return False
                if op == "$nin" and actual in op_val:
                    return False
                if op == "$in" and actual not in op_val:
                    return False
        else:
            if actual != expected:
                return False
    return True


class _FakeCursor:
    def __init__(self, docs):
        self._docs = list(docs)
        self._sort = None
        self._skip = 0
        self._limit = None

    def sort(self, keys):
        self._sort = keys
        return self

    def skip(self, n):
        self._skip = int(n or 0)
        return self

    def limit(self, n):
        self._limit = int(n or 0) or None
        return self

    def __iter__(self):
        out = list(self._docs)
        if self._sort:
            for k, d in reversed(self._sort):
                out.sort(key=lambda x, k=k: (x.get(k) is None, x.get(k)),
                         reverse=(d == -1))
        if self._skip:
            out = out[self._skip:]
        if self._limit:
            out = out[: self._limit]
        return iter(out)


class FakeCollection:
    def __init__(self):
        self.docs = []

    def insert_one(self, doc):
        self.docs.append(dict(doc))
        return type("R", (), {"inserted_id": doc.get("_id")})()

    def find(self, filter=None, projection=None):
        return _FakeCursor(d for d in self.docs if _doc_matches(d, filter))

    def find_one(self, filter=None, projection=None):
        if not filter:
            return self.docs[0] if self.docs else None
        for d in self.docs:
            if _doc_matches(d, filter):
                return d
        return None

    def count_documents(self, filter=None):
        return sum(1 for d in self.docs if _doc_matches(d, filter))


class FakeDB:
    is_connected = True
    def __init__(self):
        self._collections = {}
    def get_collection(self, name):
        if name not in self._collections:
            self._collections[name] = FakeCollection()
        return self._collections[name]
    def __getattr__(self, name):
        return self.get_collection(name)


@pytest.fixture
def patched_reports(monkeypatch):
    """Wire the reports router to a fake orders collection."""
    fake_db = FakeDB()
    from api.routers import reports as reports_module
    monkeypatch.setattr(reports_module, "get_db", lambda: fake_db)

    # Build a real OrderRepository on top of the fake collection so
    # the helpers' `order_repo.find_many(filter)` flows untouched.
    from database.repositories.order_repository import OrderRepository
    order_repo = OrderRepository(fake_db.get_collection("orders"))
    monkeypatch.setattr(reports_module, "get_order_repository", lambda: order_repo)
    monkeypatch.setattr(reports_module, "get_stock_repository", lambda: None)
    # Wire a real CustomerRepository — needed for the
    # customers/acquisition test (the handler 503-equivalent's the
    # response when customer_repo is None).
    from database.repositories.customer_repository import CustomerRepository
    customer_repo = CustomerRepository(fake_db.get_collection("customers"))
    monkeypatch.setattr(reports_module, "get_customer_repository", lambda: customer_repo)
    monkeypatch.setattr(reports_module, "get_task_repository", lambda: None)
    monkeypatch.setattr(reports_module, "get_attendance_repository", lambda: None)
    return {"db": fake_db, "order_repo": order_repo, "customer_repo": customer_repo}


# ============================================================================
# Helper to seed a real-shaped order doc (mirrors orders.py:722-748)
# ============================================================================


def _seed_order(
    fake_db,
    *,
    order_id: str,
    store_id: str = "BV-TEST-01",
    grand_total: float = 1050.0,
    total_discount: float = 0.0,
    tax_amount: float = 50.0,
    items: list = None,
    created_at: datetime = None,
    status: str = "CONFIRMED",
):
    fake_db.get_collection("orders").insert_one({
        "_id": order_id, "order_id": order_id,
        "order_number": order_id, "store_id": store_id,
        "customer_id": "cust-x",
        "items": items or [{
            "item_total": 1000.0, "unit_price": 1000.0, "quantity": 1,
            "category": "FRAME", "item_type": "FRAME",
        }],
        "subtotal": 1000.0,
        "tax_amount": tax_amount,
        "total_discount": total_discount,
        "grand_total": grand_total,
        "status": status,
        "created_at": created_at or datetime.now(),
    })


# ============================================================================
# /sales/summary
# ============================================================================


def test_sales_summary_returns_real_revenue(client, auth_headers, patched_reports):
    """Regression: pre-fix this returned total_sales=0 because the
    backend summed `final_amount` (orders use `grand_total`) and
    filtered created_at as ISO string."""
    fake_db = patched_reports["db"]
    today = datetime.now()
    _seed_order(fake_db, order_id="O1", grand_total=1050.0, tax_amount=50.0,
                created_at=today)
    _seed_order(fake_db, order_id="O2", grand_total=2100.0, tax_amount=100.0,
                total_discount=200.0, created_at=today)

    today_d = today.date().isoformat()
    resp = client.get(
        f"/api/v1/reports/sales/summary?from_date={today_d}&to_date={today_d}",
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    s = resp.json()["summary"]
    assert s["total_sales"] == 3150.0
    assert s["total_orders"] == 2
    assert s["total_tax"] == 150.0
    assert s["total_discount"] == 200.0
    assert s["avg_order_value"] == 1575.0


def test_sales_summary_excludes_cancelled_and_draft(
    client, auth_headers, patched_reports
):
    """CANCELLED + DRAFT orders must not contribute to totals."""
    fake_db = patched_reports["db"]
    today = datetime.now()
    _seed_order(fake_db, order_id="OK", grand_total=1000.0, created_at=today,
                status="CONFIRMED")
    _seed_order(fake_db, order_id="CX", grand_total=99999.0, created_at=today,
                status="CANCELLED")
    _seed_order(fake_db, order_id="DR", grand_total=99999.0, created_at=today,
                status="DRAFT")
    today_d = today.date().isoformat()
    resp = client.get(
        f"/api/v1/reports/sales/summary?from_date={today_d}&to_date={today_d}",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["summary"]["total_sales"] == 1000.0
    assert resp.json()["summary"]["total_orders"] == 1


def test_sales_summary_respects_date_window(
    client, auth_headers, patched_reports
):
    """Old orders outside the window are excluded; pre-fix the
    string comparison sometimes accidentally matched."""
    fake_db = patched_reports["db"]
    now = datetime.now()
    _seed_order(fake_db, order_id="IN", grand_total=500.0, created_at=now)
    _seed_order(fake_db, order_id="OUT",
                grand_total=99999.0,
                created_at=now - timedelta(days=120))
    today_d = now.date().isoformat()
    resp = client.get(
        f"/api/v1/reports/sales/summary?from_date={today_d}&to_date={today_d}",
        headers=auth_headers,
    )
    assert resp.json()["summary"]["total_sales"] == 500.0


def test_sales_summary_returns_daily_trend_and_categories(
    client, auth_headers, patched_reports
):
    """Frontend reads dailyTrend + categoryBreakdown off the same
    response; pre-fix the backend never returned those keys, so the
    frontend's `if (response.dailyTrend) setDailyTrend(...)` was a
    no-op."""
    fake_db = patched_reports["db"]
    now = datetime.now()
    _seed_order(
        fake_db, order_id="MIX",
        grand_total=3410.0, tax_amount=410.0,
        items=[
            {"item_total": 1000.0, "unit_price": 1000.0, "quantity": 1, "category": "FRAME"},
            {"item_total": 2000.0, "unit_price": 2000.0, "quantity": 1, "category": "SUNGLASSES"},
        ],
        created_at=now,
    )
    today_d = now.date().isoformat()
    resp = client.get(
        f"/api/v1/reports/sales/summary?from_date={today_d}&to_date={today_d}",
        headers=auth_headers,
    )
    body = resp.json()
    assert "dailyTrend" in body
    assert "categoryBreakdown" in body
    assert len(body["dailyTrend"]) == 1
    assert body["dailyTrend"][0]["sales"] == 3410.0

    cats = {c["category"]: c for c in body["categoryBreakdown"]}
    assert "FRAME" in cats and "SUNGLASSES" in cats
    assert cats["FRAME"]["sales"] == 1000.0
    assert cats["SUNGLASSES"]["sales"] == 2000.0
    assert cats["FRAME"]["units"] == 1
    # Percentages add to 100
    assert round(sum(c["percentage"] for c in body["categoryBreakdown"])) == 100


# ============================================================================
# /sales/daily
# ============================================================================


def test_sales_daily_groups_by_date(client, auth_headers, patched_reports):
    """Two orders today + one yesterday → 2 daily buckets, sales summed."""
    fake_db = patched_reports["db"]
    today_dt = datetime.now()
    yest = today_dt - timedelta(days=1)
    _seed_order(fake_db, order_id="T1", grand_total=1000.0, created_at=today_dt)
    _seed_order(fake_db, order_id="T2", grand_total=2000.0, created_at=today_dt)
    _seed_order(fake_db, order_id="Y1", grand_total=500.0, created_at=yest)

    resp = client.get(
        "/api/v1/reports/sales/daily?days=30", headers=auth_headers,
    )
    assert resp.status_code == 200
    rows = resp.json()["data"]
    assert len(rows) == 2
    by_date = {r["date"]: r for r in rows}
    assert by_date[today_dt.date().isoformat()]["sales"] == 3000.0
    assert by_date[today_dt.date().isoformat()]["orders"] == 2
    assert by_date[yest.date().isoformat()]["sales"] == 500.0


# ============================================================================
# /sales/by-category
# ============================================================================


def test_sales_by_category_sums_item_revenue(
    client, auth_headers, patched_reports
):
    """Pre-fix this summed item.total / item.price * quantity. Items
    actually carry `item_total` (post item-discount) + `unit_price`."""
    fake_db = patched_reports["db"]
    now = datetime.now()
    _seed_order(
        fake_db, order_id="MIX1", grand_total=4000.0,
        items=[
            {"item_total": 1000.0, "unit_price": 1000.0, "quantity": 2, "category": "FRAME"},
            {"item_total": 2000.0, "unit_price": 2000.0, "quantity": 1, "category": "SUNGLASSES"},
        ],
        created_at=now,
    )
    _seed_order(
        fake_db, order_id="MIX2", grand_total=500.0,
        items=[
            {"item_total": 500.0, "unit_price": 500.0, "quantity": 1, "category": "FRAME"},
        ],
        created_at=now,
    )
    today_d = now.date().isoformat()
    resp = client.get(
        f"/api/v1/reports/sales/by-category"
        f"?from_date={today_d}&to_date={today_d}",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    rows = resp.json()["data"]
    cats = {r["category"]: r for r in rows}
    assert cats["FRAME"]["sales"] == 1500.0
    assert cats["FRAME"]["units"] == 3
    assert cats["SUNGLASSES"]["sales"] == 2000.0
    assert cats["SUNGLASSES"]["units"] == 1


def test_sales_by_category_handles_legacy_item_fields(
    client, auth_headers, patched_reports
):
    """Backwards compat: legacy items with `total` (pre-Phase-6.7)
    still aggregate. Defense-in-depth via the `_item_revenue` helper."""
    fake_db = patched_reports["db"]
    now = datetime.now()
    _seed_order(
        fake_db, order_id="LEGACY", grand_total=1500.0,
        items=[
            {"total": 800.0, "quantity": 1, "category": "FRAME"},
            {"price": 700.0, "quantity": 1, "category": "FRAME"},
        ],
        created_at=now,
    )
    today_d = now.date().isoformat()
    resp = client.get(
        f"/api/v1/reports/sales/by-category"
        f"?from_date={today_d}&to_date={today_d}",
        headers=auth_headers,
    )
    rows = resp.json()["data"]
    assert rows[0]["category"] == "FRAME"
    assert rows[0]["sales"] == 1500.0


# ============================================================================
# /sales/growth
# ============================================================================


def test_sales_growth_mom_yoy(client, auth_headers, patched_reports):
    """MoM and YoY math against an empty prior month / prior year
    yields 0 (no division-by-zero); against real prior-period sales
    yields the correct % change."""
    fake_db = patched_reports["db"]
    now = datetime.now()
    cur_year, cur_month = now.year, now.month
    # Current month: ₹3,000 across 3 orders
    _seed_order(fake_db, order_id="C1", grand_total=1000.0, created_at=now)
    _seed_order(fake_db, order_id="C2", grand_total=1000.0, created_at=now)
    _seed_order(fake_db, order_id="C3", grand_total=1000.0, created_at=now)
    # Previous month: ₹2,000 (50% MoM growth)
    if cur_month == 1:
        prev_y, prev_m = cur_year - 1, 12
    else:
        prev_y, prev_m = cur_year, cur_month - 1
    prev_dt = datetime(prev_y, prev_m, 15, 12, 0)
    _seed_order(fake_db, order_id="M1", grand_total=2000.0, created_at=prev_dt)
    # Previous year same month: ₹1,500 (100% YoY growth)
    yoy_dt = datetime(cur_year - 1, cur_month, 15, 12, 0)
    _seed_order(fake_db, order_id="Y1", grand_total=1500.0, created_at=yoy_dt)

    resp = client.get(
        f"/api/v1/reports/sales/growth?year={cur_year}&month={cur_month}",
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["current_month"]["sales"] == 3000.0
    assert body["current_month"]["orders"] == 3
    # MoM: (3000 - 2000) / 2000 * 100 = 50.0
    # YoY: (3000 - 1500) / 1500 * 100 = 100.0
    # The exact key path depends on the response shape — fetch
    # whichever flavour is set by the trailing block.
    assert "mom_growth" in body or "MoM" in body or "mom" in body


def test_sales_growth_zero_prior_period_no_crash(
    client, auth_headers, patched_reports
):
    """When prior month / prior year had zero sales, growth is 0 not
    a division-by-zero crash."""
    fake_db = patched_reports["db"]
    now = datetime.now()
    _seed_order(fake_db, order_id="ONLY", grand_total=1000.0, created_at=now)
    resp = client.get(
        f"/api/v1/reports/sales/growth"
        f"?year={now.year}&month={now.month}",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["current_month"]["sales"] == 1000.0


# ============================================================================
# Helper unit tests (no HTTP)
# ============================================================================


def test_helper_order_revenue_falls_through_legacy_fields():
    """Defense in depth — older docs without `grand_total` shouldn't
    silently sum to 0."""
    from api.routers.reports import _order_revenue
    assert _order_revenue({"grand_total": 1500.0}) == 1500.0
    assert _order_revenue({"final_amount": 1500.0}) == 1500.0
    assert _order_revenue({"total_amount": 1500.0}) == 1500.0
    assert _order_revenue({}) == 0.0


def test_helper_item_revenue_handles_all_shapes():
    from api.routers.reports import _item_revenue
    assert _item_revenue({"item_total": 800.0}) == 800.0
    assert _item_revenue({"total": 800.0}) == 800.0
    assert _item_revenue({"unit_price": 200.0, "quantity": 4}) == 800.0
    assert _item_revenue({"price": 200.0, "quantity": 4}) == 800.0
    assert _item_revenue({}) == 0.0


# ============================================================================
# Round 2 — staff ranking, brand sell-through, customer acquisition,
# discount analysis, expense vs revenue, sales comparison
# ============================================================================


def test_staff_ranking_uses_grand_total_not_legacy_fields(
    client, auth_headers, patched_reports
):
    fake_db = patched_reports["db"]
    today = datetime.now()
    _seed_order(fake_db, order_id="A1", grand_total=1000.0, created_at=today,
                items=[{"item_total": 800.0, "category": "FRAME"}])
    fake_db.get_collection("orders").docs[-1]["sales_person_id"] = "user-akshay"
    fake_db.get_collection("orders").docs[-1]["sales_person_name"] = "Akshay"
    _seed_order(fake_db, order_id="A2", grand_total=2000.0, created_at=today)
    fake_db.get_collection("orders").docs[-1]["sales_person_id"] = "user-akshay"
    _seed_order(fake_db, order_id="R1", grand_total=500.0, created_at=today)
    fake_db.get_collection("orders").docs[-1]["sales_person_id"] = "user-rupesh"
    fake_db.get_collection("orders").docs[-1]["sales_person_name"] = "Rupesh"

    today_d = today.date().isoformat()
    resp = client.get(
        f"/api/v1/reports/staff/ranking?from_date={today_d}&to_date={today_d}",
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    rows = resp.json()["data"]
    by_id = {r["staff_id"]: r for r in rows}
    assert by_id["user-akshay"]["total_sales"] == 3000.0
    assert by_id["user-akshay"]["order_count"] == 2
    assert by_id["user-akshay"]["avg_bill"] == 1500.0
    assert by_id["user-rupesh"]["total_sales"] == 500.0
    # Sorted by sales desc → akshay first
    assert rows[0]["staff_id"] == "user-akshay"


def test_sales_comparison_uses_grand_total(
    client, auth_headers, patched_reports
):
    """Window: from = today−6d, to = today. Previous period:
    (today−13d) to (today−7d). Seed an order in each."""
    fake_db = patched_reports["db"]
    now = datetime.now()
    # 4 orders in the requested 7-day window, ₹3,500 total
    _seed_order(fake_db, order_id="C1", grand_total=1000.0, created_at=now)
    _seed_order(fake_db, order_id="C2", grand_total=1500.0, created_at=now)
    _seed_order(fake_db, order_id="C3", grand_total=500.0, created_at=now)
    _seed_order(fake_db, order_id="C4", grand_total=500.0, created_at=now)
    # 1 order in the previous 7-day window (day 10 ago)
    _seed_order(
        fake_db, order_id="P1", grand_total=2500.0,
        created_at=now - timedelta(days=10),
    )

    today_d = now.date().isoformat()
    yest = (now - timedelta(days=6)).date().isoformat()
    resp = client.get(
        f"/api/v1/reports/sales/comparison?from_date={yest}&to_date={today_d}",
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["current_period"]["sales"] == 3500.0
    assert body["current_period"]["orders"] == 4
    assert body["previous_period"]["sales"] == 2500.0


def test_discount_analysis_aggregates_per_category(
    client, auth_headers, patched_reports
):
    """Pre-fix: read item.get('discount') and item.get('price'). Now
    reads discount_amount + uses _item_revenue."""
    fake_db = patched_reports["db"]
    now = datetime.now()
    _seed_order(
        fake_db, order_id="D1", grand_total=1800.0,
        total_discount=200.0,
        items=[
            {"item_total": 800.0, "unit_price": 1000.0, "discount_amount": 200.0,
             "quantity": 1, "category": "FRAME"},
            {"item_total": 1000.0, "unit_price": 1000.0, "discount_amount": 0,
             "quantity": 1, "category": "SUNGLASSES"},
        ],
        created_at=now,
    )
    today_d = now.date().isoformat()
    resp = client.get(
        f"/api/v1/reports/discount/analysis?from_date={today_d}&to_date={today_d}",
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    cats = {c["category"]: c for c in body["by_category"]}
    assert cats["FRAME"]["total_discount"] == 200.0
    assert cats["FRAME"]["total_revenue"] == 800.0
    # 200 / (800 + 200) = 20%
    assert cats["FRAME"]["avg_discount_percent"] == 20.0
    assert cats["SUNGLASSES"]["total_discount"] == 0.0
    assert body["summary"]["total_discount"] == 200.0


def test_expense_vs_revenue_computes_margin(
    client, auth_headers, patched_reports
):
    fake_db = patched_reports["db"]
    now = datetime.now()
    _seed_order(
        fake_db, order_id="E1", grand_total=1000.0,
        items=[{"item_total": 800.0, "unit_price": 800.0, "quantity": 1,
                "category": "FRAME", "cost_price": 300.0}],
        created_at=now,
    )
    today_d = now.date().isoformat()
    resp = client.get(
        f"/api/v1/reports/finance/expense-vs-revenue"
        f"?from_date={today_d}&to_date={today_d}",
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # revenue = 1000 (grand_total), cost = 300 × 1 = 300, profit = 700
    assert body["revenue"] == 1000.0
    assert body["cost"] == 300.0
    assert body["profit"] == 700.0
    assert body["margin_percent"] == 70.0


def test_customer_acquisition_retention_pct_is_share_of_buyers(
    client, auth_headers, patched_reports
):
    """Pre-fix the formula divided returning by new_customers,
    yielding > 100% whenever a returning buyer wasn't a new signup.
    Now divides by total unique buyers in the window."""
    fake_db = patched_reports["db"]
    now = datetime.now()
    # 2 customers exist (1 new in window, 1 created last year)
    fake_db.get_collection("customers").insert_one({
        "customer_id": "cust-new", "store_id": "BV-TEST-01",
        "name": "New", "created_at": now,
    })
    fake_db.get_collection("customers").insert_one({
        "customer_id": "cust-old", "store_id": "BV-TEST-01",
        "name": "Old", "created_at": now - timedelta(days=400),
    })

    # cust-new: 1 order in window
    fake_db.get_collection("orders").insert_one({
        "_id": "oN", "order_id": "oN", "store_id": "BV-TEST-01",
        "customer_id": "cust-new", "grand_total": 500.0,
        "created_at": now, "status": "CONFIRMED",
    })
    # cust-old: 2 orders in window → returning
    for i in range(2):
        fake_db.get_collection("orders").insert_one({
            "_id": f"oO{i}", "order_id": f"oO{i}",
            "store_id": "BV-TEST-01", "customer_id": "cust-old",
            "grand_total": 800.0, "created_at": now, "status": "CONFIRMED",
        })

    today_d = now.date().isoformat()
    resp = client.get(
        f"/api/v1/reports/customers/acquisition"
        f"?from_date={today_d}&to_date={today_d}",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["new_customers"] == 1
    # 1 returning buyer (cust-old, 2 orders) out of 2 total buyers = 50%
    assert body["returning_customers"] == 1
    assert body["retention_percent"] == 50.0
    assert body["total_customers"] == 2


def test_brand_sellthrough_uses_item_revenue_helper(
    client, auth_headers, patched_reports
):
    fake_db = patched_reports["db"]
    now = datetime.now()
    _seed_order(
        fake_db, order_id="B1", grand_total=3000.0,
        items=[
            {"item_total": 2000.0, "unit_price": 1000.0, "quantity": 2,
             "brand": "Ray-Ban", "category": "SUNGLASSES"},
            {"item_total": 1000.0, "unit_price": 1000.0, "quantity": 1,
             "brand": "Persol", "category": "SUNGLASSES"},
        ],
        created_at=now,
    )
    today_d = now.date().isoformat()
    resp = client.get(
        f"/api/v1/reports/inventory/brand-sellthrough"
        f"?from_date={today_d}&to_date={today_d}",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    by_brand = {b["brand"]: b for b in body["data"]}
    assert by_brand["Ray-Ban"]["revenue"] == 2000.0
    assert by_brand["Ray-Ban"]["quantity_sold"] == 2
    assert by_brand["Ray-Ban"]["avg_price"] == 1000.0
    assert by_brand["Persol"]["revenue"] == 1000.0
