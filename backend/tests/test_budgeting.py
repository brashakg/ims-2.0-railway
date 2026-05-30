"""
IMS 2.0 - Dual-mode budgeting (planned vs actual)
=================================================
Exercises backend/api/routers/budgets.py end to end against a throwaway Mongo
database at MONGO_HOST=127.0.0.1 (skip-if-no-mongo, mirroring
test_transfer_stock_movement.py), plus pure-helper and RBAC/store-scope checks
that need no DB.

Coverage:
  * planned upsert + list (one doc per store/period/head; re-upsert overwrites)
  * variance computes correct planned / actual / variance from seeded orders
    (revenue actual) + APPROVED expenses (expense actuals), and surfaces a head
    that has actuals but no plan (planned == 0)
  * a non-manager role -> 403
  * a cross-store store_id -> 403 (store-scope)

If Mongo isn't reachable the DB-backed tests SKIP (they don't fail), so the
suite stays green on machines without a local mongod.
"""

from __future__ import annotations

import os
import sys
import uuid
import asyncio
from datetime import datetime

import pytest

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import HTTPException  # noqa: E402

from api.routers import budgets  # noqa: E402


# ===========================================================================
# Pure helpers (no DB)
# ===========================================================================


def test_period_validation_accepts_valid_and_rejects_garbage():
    assert budgets._validate_period("2026-05") == "2026-05"
    assert budgets._validate_period("2026-12") == "2026-12"
    for bad in ("2026-13", "2026-00", "2026-5", "2026/05", "202605", "", "abc"):
        with pytest.raises(HTTPException) as exc:
            budgets._validate_period(bad)
        assert exc.value.status_code == 400


def test_month_window_spans_whole_month_and_wraps_december():
    start, end = budgets._month_window("2026-02")
    assert start == datetime(2026, 2, 1)
    assert start <= end and end < datetime(2026, 3, 1)
    # December wraps into the next year.
    _, dec_end = budgets._month_window("2026-12")
    assert dec_end < datetime(2027, 1, 1)
    assert dec_end >= datetime(2026, 12, 31)


def test_variance_and_pct_math():
    assert budgets._variance(100.0, 130.0) == 30.0
    assert budgets._variance(100.0, 80.0) == -20.0
    assert budgets._variance_pct(100.0, 130.0) == 30.0
    assert budgets._variance_pct(200.0, 150.0) == -25.0
    # No plan -> pct is None (avoid divide-by-zero / misleading %).
    assert budgets._variance_pct(0.0, 500.0) is None


# ===========================================================================
# RBAC + store-scope (no DB needed)
# ===========================================================================


def test_non_manager_role_is_forbidden():
    """The require_roles dependency must 403 a role outside the manager+ set."""
    dep = budgets.require_roles(*budgets._BUDGET_ROLES)
    cashier = {"user_id": "u-cash", "roles": ["SALES_CASHIER"], "store_ids": ["S1"]}
    with pytest.raises(HTTPException) as exc:
        asyncio.run(dep(current_user=cashier))
    assert exc.value.status_code == 403


def test_cross_store_store_id_is_forbidden():
    """A store manager requesting a store outside their store_ids -> 403."""
    manager = {
        "user_id": "u-mgr",
        "roles": ["STORE_MANAGER"],
        "store_ids": ["S1"],
        "active_store_id": "S1",
    }
    # Listing a foreign store must raise inside validate_store_access.
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            budgets.list_budgets(
                store_id="S2", period="2026-05", current_user=manager
            )
        )
    assert exc.value.status_code == 403

    # Upsert against a foreign store is also blocked.
    payload = budgets.BudgetUpsert(
        store_id="S2", period="2026-05", head="rent", planned_amount=1000
    )
    with pytest.raises(HTTPException) as exc2:
        asyncio.run(budgets.upsert_budget(payload, current_user=manager))
    assert exc2.value.status_code == 403


# ===========================================================================
# DB-backed: persistence + variance against real repos
# ===========================================================================


MONGO_HOST = os.getenv("MONGO_HOST", "127.0.0.1")
MONGO_PORT = int(os.getenv("MONGO_PORT", "27017"))

STORE = "S-BUDGET-1"
OTHER_STORE = "S-BUDGET-2"
PERIOD = "2026-05"

_ADMIN = {
    "user_id": "u-admin",
    "username": "admin",
    "roles": ["SUPERADMIN"],
    "store_ids": [STORE, OTHER_STORE],
    "active_store_id": STORE,
}


def _mongo_db():
    """Connect to a throwaway DB on the local mongo, or skip if unreachable."""
    try:
        from pymongo import MongoClient
    except Exception:  # noqa: BLE001
        pytest.skip("pymongo not installed")
    try:
        client = MongoClient(MONGO_HOST, MONGO_PORT, serverSelectionTimeoutMS=1500)
        client.admin.command("ping")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"no local mongo at {MONGO_HOST}:{MONGO_PORT} ({exc})")
    name = f"ims_test_budget_{uuid.uuid4().hex[:8]}"
    return client, client[name], name


@pytest.fixture
def wired(monkeypatch):
    """Real OrderRepository + ExpenseRepository + budgets collection on a
    throwaway mongo DB, wired into the budgets router."""
    from database.repositories.order_repository import OrderRepository
    from database.repositories.expense_repository import ExpenseRepository

    client, db, name = _mongo_db()
    order_repo = OrderRepository(db.get_collection("orders"))
    expense_repo = ExpenseRepository(db.get_collection("expenses"))

    monkeypatch.setattr(budgets, "get_order_repository", lambda: order_repo)
    monkeypatch.setattr(budgets, "get_expense_repository", lambda: expense_repo)
    monkeypatch.setattr(
        budgets, "_budgets_collection", lambda: db.get_collection("budgets")
    )

    try:
        yield {"db": db, "order_repo": order_repo, "expense_repo": expense_repo}
    finally:
        client.drop_database(name)
        client.close()


def _seed_order(db, store_id, grand_total, *, day=10, status="COMPLETED"):
    """Insert an order with a datetime created_at inside PERIOD, mirroring how
    the real OrderRepository stamps created_at (a datetime, not a string)."""
    db.get_collection("orders").insert_one(
        {
            "order_id": uuid.uuid4().hex,
            "store_id": store_id,
            "grand_total": grand_total,
            "status": status,
            "created_at": datetime(2026, 5, day, 12, 0, 0),
        }
    )


def _seed_expense(db, store_id, category, amount, *, status="APPROVED", day=12):
    """Insert an expense with an ISO-string expense_date inside PERIOD."""
    db.get_collection("expenses").insert_one(
        {
            "expense_id": uuid.uuid4().hex,
            "store_id": store_id,
            "category": category,
            "amount": amount,
            "status": status,
            "expense_date": f"2026-05-{day:02d}",
        }
    )


def test_upsert_then_list_is_idempotent_per_head(wired):
    db = wired["db"]

    p1 = budgets.BudgetUpsert(
        store_id=STORE, period=PERIOD, head="REVENUE", planned_amount=500000
    )
    p2 = budgets.BudgetUpsert(
        store_id=STORE, period=PERIOD, head="rent", planned_amount=40000
    )
    r1 = asyncio.run(budgets.upsert_budget(p1, current_user=_ADMIN))
    asyncio.run(budgets.upsert_budget(p2, current_user=_ADMIN))
    assert r1["persisted"] is True
    assert r1["budget"]["planned_amount"] == 500000

    listed = asyncio.run(
        budgets.list_budgets(store_id=STORE, period=PERIOD, current_user=_ADMIN)
    )
    assert listed["total"] == 2
    heads = {b["head"]: b["planned_amount"] for b in listed["budgets"]}
    assert heads == {"REVENUE": 500000, "rent": 40000}
    # REVENUE sorts first.
    assert listed["budgets"][0]["head"] == "REVENUE"

    # Re-upsert the same (store, period, head) overwrites, not appends.
    p2b = budgets.BudgetUpsert(
        store_id=STORE, period=PERIOD, head="rent", planned_amount=45000
    )
    asyncio.run(budgets.upsert_budget(p2b, current_user=_ADMIN))
    relisted = asyncio.run(
        budgets.list_budgets(store_id=STORE, period=PERIOD, current_user=_ADMIN)
    )
    assert relisted["total"] == 2  # still 2 docs, not 3
    assert db.get_collection("budgets").count_documents(
        {"store_id": STORE, "period": PERIOD, "head": "rent"}
    ) == 1
    rent = next(b for b in relisted["budgets"] if b["head"] == "rent")
    assert rent["planned_amount"] == 45000


def test_delete_removes_a_planned_line(wired):
    p = budgets.BudgetUpsert(
        store_id=STORE, period=PERIOD, head="marketing", planned_amount=10000
    )
    created = asyncio.run(budgets.upsert_budget(p, current_user=_ADMIN))
    bid = created["budget"]["budget_id"]
    assert bid

    res = asyncio.run(budgets.delete_budget(bid, current_user=_ADMIN))
    assert res["deleted"] is True
    after = asyncio.run(
        budgets.list_budgets(store_id=STORE, period=PERIOD, current_user=_ADMIN)
    )
    assert after["total"] == 0


def test_variance_computes_planned_actual_and_unplanned_actuals(wired):
    db = wired["db"]

    # --- plans -----------------------------------------------------------
    asyncio.run(
        budgets.upsert_budget(
            budgets.BudgetUpsert(
                store_id=STORE, period=PERIOD, head="REVENUE", planned_amount=100000
            ),
            current_user=_ADMIN,
        )
    )
    asyncio.run(
        budgets.upsert_budget(
            budgets.BudgetUpsert(
                store_id=STORE, period=PERIOD, head="rent", planned_amount=40000
            ),
            current_user=_ADMIN,
        )
    )

    # --- actuals: orders (revenue) --------------------------------------
    _seed_order(db, STORE, 30000)
    _seed_order(db, STORE, 50000)
    # Excluded: cancelled + draft must NOT count toward revenue actual.
    _seed_order(db, STORE, 99999, status="CANCELLED")
    _seed_order(db, STORE, 88888, status="DRAFT")
    # Excluded: a different store's order must NOT leak in.
    _seed_order(db, OTHER_STORE, 12345)

    # --- actuals: APPROVED expenses -------------------------------------
    _seed_expense(db, STORE, "rent", 42000)  # over its 40000 plan
    _seed_expense(db, STORE, "salaries", 25000)  # UNPLANNED head (no plan)
    # Excluded: a PENDING expense must NOT count toward actuals.
    _seed_expense(db, STORE, "rent", 9999, status="PENDING")

    out = asyncio.run(
        budgets.budget_variance(store_id=STORE, period=PERIOD, current_user=_ADMIN)
    )
    lines = {ln["head"]: ln for ln in out["lines"]}

    # Revenue: planned 100000, actual 30000+50000 = 80000 (cancel/draft/other
    # store excluded) -> variance -20000, -20%.
    assert lines["REVENUE"]["planned"] == 100000
    assert lines["REVENUE"]["actual"] == 80000
    assert lines["REVENUE"]["variance"] == -20000
    assert lines["REVENUE"]["variance_pct"] == -20.0
    assert lines["REVENUE"]["is_revenue"] is True

    # Rent: planned 40000, actual 42000 (PENDING excluded) -> +2000, +5%.
    assert lines["rent"]["planned"] == 40000
    assert lines["rent"]["actual"] == 42000
    assert lines["rent"]["variance"] == 2000
    assert lines["rent"]["variance_pct"] == 5.0

    # Salaries: an actual with NO plan is surfaced with planned == 0.
    assert "salaries" in lines
    assert lines["salaries"]["planned"] == 0
    assert lines["salaries"]["actual"] == 25000
    assert lines["salaries"]["variance"] == 25000
    assert lines["salaries"]["variance_pct"] is None  # no plan -> no pct

    # Totals block.
    totals = out["totals"]
    assert totals["revenue_planned"] == 100000
    assert totals["revenue_actual"] == 80000
    assert totals["expense_planned"] == 40000
    assert totals["expense_actual"] == 42000 + 25000
    assert totals["net_planned"] == 100000 - 40000
    assert totals["net_actual"] == 80000 - (42000 + 25000)


def test_variance_no_data_is_zeroed_not_error(wired):
    """A period with no plans and no actuals returns a clean zeroed envelope."""
    out = asyncio.run(
        budgets.budget_variance(store_id=STORE, period="2026-04", current_user=_ADMIN)
    )
    # REVENUE always present (actual 0); no expense heads.
    assert out["totals"]["revenue_actual"] == 0
    assert out["totals"]["expense_actual"] == 0
    assert out["totals"]["net_actual"] == 0
    rev = next(ln for ln in out["lines"] if ln["head"] == "REVENUE")
    assert rev["planned"] == 0 and rev["actual"] == 0
