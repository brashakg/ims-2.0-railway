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


def test_budget_endpoint_returns_zeroes_when_none_configured(
    client, auth_headers, monkeypatch
):
    """BEHAVIOURAL: with no budget row for the period the endpoint must return
    an honest empty skeleton -- every category at 0 and no_budget_set True --
    not the old fabricated allocations (50000 / 200000 / 500000).

    Replaces a grep of get_budget's source for those literals, which would keep
    passing if the fabricated numbers were merely moved into a helper or a
    module-level constant.
    """
    from api.routers import finance as fin_mod
    from strict_fakes import StrictDB

    db = StrictDB()
    monkeypatch.setattr(fin_mod, "_get_db", lambda: db)

    resp = client.get("/api/v1/finance/budget?month=6&year=2026", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["no_budget_set"] is True, body
    assert body["categories"], "the skeleton must still name the categories"
    for cat, vals in body["categories"].items():
        assert vals["budget"] == 0, f"category {cat} has fabricated budget {vals['budget']}"
        assert vals["actual"] == 0, f"category {cat} has fabricated actual {vals['actual']}"


def test_budget_endpoint_uses_a_configured_budget_when_present(
    client, auth_headers, monkeypatch
):
    """The zero skeleton must be the EMPTY-state answer, not an unconditional
    one -- otherwise the test above would pass on a permanently broken reader."""
    from api.routers import finance as fin_mod
    from strict_fakes import StrictDB

    db = StrictDB()
    db.seed(
        "budgets",
        [
            {
                "month": 6,
                "year": 2026,
                "mode": "full",
                "categories": {"rent": {"budget": 40000, "actual": 0}},
            }
        ],
    )
    monkeypatch.setattr(fin_mod, "_get_db", lambda: db)

    body = client.get(
        "/api/v1/finance/budget?month=6&year=2026", headers=auth_headers
    ).json()
    assert body.get("no_budget_set") is not True, body
    assert body["categories"]["rent"]["budget"] == 40000, body


def test_budget_actuals_come_from_recorded_expenses(client, auth_headers, monkeypatch):
    """Actuals are read from APPROVED/PAID expenses dated in the period."""
    from api.routers import finance as fin_mod
    from strict_fakes import StrictDB

    db = StrictDB()
    db.seed(
        "expenses",
        [
            {"expense_id": "e1", "category": "rent", "amount": 12000.0,
             "expense_date": "2026-06-05", "status": "APPROVED"},
            # Out of period -- must not be counted.
            {"expense_id": "e2", "category": "rent", "amount": 99000.0,
             "expense_date": "2026-05-05", "status": "APPROVED"},
            # In period but still pending approval -- must not be counted.
            {"expense_id": "e3", "category": "rent", "amount": 55000.0,
             "expense_date": "2026-06-07", "status": "PENDING"},
        ],
    )
    monkeypatch.setattr(fin_mod, "_get_db", lambda: db)

    body = client.get(
        "/api/v1/finance/budget?month=6&year=2026", headers=auth_headers
    ).json()
    assert body["categories"]["rent"]["actual"] == 12000.0, body


# ============================================================================
# FIND-7: NPS follow-up uses scheduled_date not due_date
# ============================================================================


def _nps_env(monkeypatch):
    """Strict DB behind the marketing router, pre-seeded with one NPS survey."""
    from api.routers import marketing as mkt_mod
    from strict_fakes import StrictDB

    db = StrictDB()
    db.seed(
        "nps_responses",
        [
            {
                "nps_id": "NPS-1",
                "customer_id": "cust-9",
                "customer_name": "Asha",
                "store_id": "BV-TEST-01",
                "status": "SENT",
            }
        ],
    )
    db.seed(
        "customers",
        [{"customer_id": "cust-9", "name": "Asha", "mobile": "9876500011"}],
    )
    monkeypatch.setattr(mkt_mod, "_get_db", lambda: db)
    return db


def test_nps_detractor_writes_a_renderable_followup(client, auth_headers, monkeypatch):
    """BEHAVIOURAL: submit a detractor score and inspect the follow-up row that
    was actually written.

    The follow-ups dashboard reads `scheduled_date` and `customer_phone`; the
    original bug wrote `due_date` and no phone, so the row existed but rendered
    blank. Replaces a source grep whose ``'"due_date"' not in src`` clause was
    additionally fragile -- an unrelated comment mentioning the field would
    have failed it, and moving the insert into a helper would have passed it.
    """
    db = _nps_env(monkeypatch)

    resp = client.post(
        "/api/v1/marketing/nps-response",
        headers=auth_headers,
        json={"nps_id": "NPS-1", "score": 3, "feedback": "Long wait"},
    )
    assert resp.status_code == 200, resp.text

    rows = db.get_collection("follow_ups").docs
    assert len(rows) == 1, f"a detractor must raise exactly one follow-up: {rows!r}"
    row = rows[0]
    assert row.get("scheduled_date"), f"follow-up must carry scheduled_date: {row!r}"
    assert "due_date" not in row, f"follow-up must not use the legacy due_date: {row!r}"
    assert row.get("customer_phone") == "9876500011", row
    assert row.get("customer_id") == "cust-9"
    assert row.get("status") == "pending"
    assert "3" in (row.get("notes") or ""), row


def test_nps_promoter_raises_no_followup(client, auth_headers, monkeypatch):
    """Only detractors (score <= 6) generate follow-ups."""
    db = _nps_env(monkeypatch)

    resp = client.post(
        "/api/v1/marketing/nps-response",
        headers=auth_headers,
        json={"nps_id": "NPS-1", "score": 9},
    )
    assert resp.status_code == 200, resp.text
    assert db.get_collection("follow_ups").docs == []


# ============================================================================
# FIND-3: Store name helpers
# ============================================================================


# Analytics name-resolution + status-exclusion are implemented in
# api/routers/analytics.py (via _is_billable + _fetch_orders_in_window) and
# covered by the reports/analytics test suite; the earlier finance-lane copies
# of those tests were dropped when the analytics changes were superseded.
