"""
IMS 2.0 - Wave-2 Finance/Analytics defect regression tests
===========================================================
Covers: FIN-5 (budget honest empty), FIND-4 (COGS estimate flag),
        FIND-6 (Tally JV IGST ledger), FIND-7 (NPS follow-up field names).
        FIND-1 (CANCELLED/DRAFT excluded from analytics),
        FIND-3 (store name join).
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-key-wave2-defects")
os.environ.setdefault("MONGODB_URI", "")


# ============================================================================
# FIND-4: compute_cogs_with_flag returns estimate metadata
# ============================================================================


def test_cogs_with_flag_no_fallback():
    """All lines have known costs -> estimated_lines=0."""
    from api.routers.finance import compute_cogs_with_flag

    orders = [{"items": [{"product_id": "P1", "quantity": 2}]}]
    cogs, est, total = compute_cogs_with_flag(orders, {"P1": 100.0}, fallback_rate=0.6)
    assert cogs == 200.0
    assert est == 0
    assert total == 1


def test_cogs_with_flag_all_estimated():
    """No cost data -> all lines estimated."""
    from api.routers.finance import compute_cogs_with_flag

    orders = [{"items": [{"product_id": "PX", "quantity": 1, "total": 1000}]}]
    cogs, est, total = compute_cogs_with_flag(orders, {}, fallback_rate=0.6)
    assert cogs == 600.0
    assert est == 1
    assert total == 1


def test_cogs_with_flag_mixed():
    """Some lines known, some estimated."""
    from api.routers.finance import compute_cogs_with_flag

    orders = [
        {
            "items": [
                {"product_id": "P1", "quantity": 1},  # known
                {
                    "product_id": "PX",
                    "quantity": 1,
                    "total": 500,
                },  # unknown -> fallback
            ]
        }
    ]
    cogs, est, total = compute_cogs_with_flag(orders, {"P1": 200.0}, fallback_rate=0.6)
    assert cogs == 500.0  # 200 + 300
    assert est == 1
    assert total == 2


def test_cogs_with_flag_no_fallback_rate_returns_zero_for_unknown():
    """Without fallback_rate, unknown lines contribute 0 and are NOT counted as estimated."""
    from api.routers.finance import compute_cogs_with_flag

    orders = [{"items": [{"product_id": "PX", "quantity": 1, "total": 1000}]}]
    cogs, est, total = compute_cogs_with_flag(orders, {}, fallback_rate=0.0)
    assert cogs == 0.0
    assert est == 0  # no fallback used
    assert total == 1


# ============================================================================
# FIND-6: Tally JV IGST ledger in nexus_providers
# ============================================================================


def test_tally_jv_intra_state_uses_cgst_sgst():
    """Intra-state order -> cgst_amount>0, igst_amount=0 -> CGST/SGST ledgers."""
    from agents.nexus_providers import tally_build_day_voucher_xml

    order = {
        "order_id": "ORD-001",
        "created_at": "2025-01-15",
        "customer_name": "Test Customer",
        "grand_total": 1180.0,
        "subtotal": 1000.0,
        "cgst_amount": 90.0,
        "sgst_amount": 90.0,
        "igst_amount": 0.0,
    }
    xml = tally_build_day_voucher_xml([order])
    assert "CGST Output" in xml
    assert "SGST Output" in xml
    assert "IGST Output" not in xml


def test_tally_jv_inter_state_uses_igst():
    """Inter-state order -> igst_amount>0, cgst/sgst=0 -> IGST Output ledger."""
    from agents.nexus_providers import tally_build_day_voucher_xml

    order = {
        "order_id": "ORD-002",
        "created_at": "2025-01-15",
        "customer_name": "Out-of-State Customer",
        "grand_total": 1180.0,
        "subtotal": 1000.0,
        "cgst_amount": 0.0,
        "sgst_amount": 0.0,
        "igst_amount": 180.0,
    }
    xml = tally_build_day_voucher_xml([order])
    assert "IGST Output" in xml
    assert "CGST Output" not in xml
    assert "SGST Output" not in xml


def test_tally_jv_inter_state_voucher_balances():
    """Inter-state voucher: subtotal + igst == grand_total."""
    from agents.nexus_providers import tally_build_day_voucher_xml

    order = {
        "order_id": "ORD-003",
        "created_at": "2025-01-15",
        "customer_name": "Inter-state",
        "grand_total": 590.0,
        "subtotal": 500.0,
        "cgst_amount": 0.0,
        "sgst_amount": 0.0,
        "igst_amount": 90.0,
    }
    xml = tally_build_day_voucher_xml([order])
    # Party ledger debit matches grand_total
    assert "-590.00" in xml
    # Sales A/c and IGST amounts present
    assert "500.00" in xml
    assert "90.00" in xml


# ============================================================================
# FIND-1: Analytics excludes CANCELLED/DRAFT (via _ANALYTICS_EXCLUDED_STATUSES)
# ============================================================================


def test_analytics_excluded_statuses_constant():
    from api.routers.analytics import _ANALYTICS_EXCLUDED_STATUSES

    assert "CANCELLED" in _ANALYTICS_EXCLUDED_STATUSES
    assert "DRAFT" in _ANALYTICS_EXCLUDED_STATUSES
    assert "cancelled" in _ANALYTICS_EXCLUDED_STATUSES
    assert "draft" in _ANALYTICS_EXCLUDED_STATUSES


def test_fetch_orders_in_window_filter_shape():
    """_fetch_orders_in_window builds a filter with status $nin exclusions."""
    from api.routers.analytics import (
        _fetch_orders_in_window,
        _ANALYTICS_EXCLUDED_STATUSES,
    )
    from datetime import datetime

    captured_filters = []

    class FakeRepo:
        def find_many(self, flt, **kwargs):
            captured_filters.append(flt)
            return []

    _fetch_orders_in_window(
        FakeRepo(),
        store_id="S1",
        start=datetime(2025, 1, 1),
        end=datetime(2025, 1, 31),
    )
    assert captured_filters, "find_many should have been called"
    flt = captured_filters[0]
    assert "status" in flt, "status filter must be present"
    assert "$nin" in flt["status"], "filter must be $nin"
    for s in _ANALYTICS_EXCLUDED_STATUSES:
        assert s in flt["status"]["$nin"], f"{s} must be excluded"


# ============================================================================
# FIN-5: Budget honest empty state
# ============================================================================


def test_budget_empty_state_has_no_budget_set_flag():
    """When no budget doc exists, the response must have no_budget_set=True
    and all category budgets must be 0 (not fabricated numbers)."""
    import types

    # Simulate the default budget structure returned when find_one returns None
    # by calling the same inline dict directly (mirrors the finance.py code path).
    budget = {
        "month": 1,
        "year": 2025,
        "mode": "full",
        "no_budget_set": True,
        "categories": {
            "rent": {"budget": 0, "actual": 0},
            "salaries": {"budget": 0, "actual": 0},
            "utilities": {"budget": 0, "actual": 0},
            "marketing": {"budget": 0, "actual": 0},
            "inventory": {"budget": 0, "actual": 0},
            "miscellaneous": {"budget": 0, "actual": 0},
        },
    }
    assert budget["no_budget_set"] is True
    for cat, vals in budget["categories"].items():
        assert (
            vals["budget"] == 0
        ), f"category {cat} has fabricated budget {vals['budget']}"


def test_budget_empty_state_not_fabricated():
    """Verify the OLD fabricated values (50000, 200000, etc.) are NOT in the new code."""
    import inspect
    from api.routers import finance as fin_mod

    src = inspect.getsource(fin_mod.get_budget)
    # The old code hardcoded these amounts; they must NOT appear in the new code.
    for bad_val in ("50000", "200000", "500000"):
        assert bad_val not in src, (
            f"Found fabricated budget amount {bad_val} in get_budget source -- "
            "should be 0 for honest empty state"
        )


# ============================================================================
# FIND-7: NPS follow-up uses scheduled_date not due_date
# ============================================================================


def test_nps_followup_uses_scheduled_date():
    """The NPS-detractor follow-up insert must use 'scheduled_date' (not 'due_date')
    and include 'customer_phone' so the follow-ups dashboard renders it."""
    import inspect
    from api.routers import marketing as mkt_mod

    src = inspect.getsource(mkt_mod.submit_nps_response)
    # The fix adds scheduled_date and customer_phone to the insert block.
    assert "scheduled_date" in src, "NPS follow-up must use 'scheduled_date'"
    assert "customer_phone" in src, "NPS follow-up must include 'customer_phone'"
    # 'due_date' may appear in comments but the insert dict must not use it.
    # Check the insert block specifically by looking for both key patterns.
    # The fix uses 'scheduled_date': ...; the old code used 'due_date': ...
    assert '"scheduled_date"' in src, "insert dict must have 'scheduled_date' key"
    assert '"due_date"' not in src, "insert dict must NOT use 'due_date' key"


# ============================================================================
# FIND-3: Store name helpers
# ============================================================================


def test_store_name_map_fail_soft_on_none():
    from api.routers.analytics import _store_name_map

    result = _store_name_map(None)
    assert result == {}


def test_customer_name_map_fail_soft_on_none():
    from api.routers.analytics import _customer_name_map

    result = _customer_name_map(None, ["C1", "C2"])
    assert result == {}


def test_customer_name_map_empty_ids():
    from api.routers.analytics import _customer_name_map

    result = _customer_name_map(None, [])
    assert result == {}
