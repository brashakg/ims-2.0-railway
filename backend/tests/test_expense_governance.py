"""
IMS 2.0 -- expense governance (caps / advance-blocking / aging)
==============================================================
Three controls layered onto the expenses router:

  1. Per-(role, category) daily + monthly spend caps with a global fallback.
  2. Outstanding-advance blocking on new expense claims.
  3. Reimbursement aging buckets (0-7 / 8-15 / 15+).

The cap math, advance-block decision, and aging bucketing live in PURE
functions in api.routers.expenses so they can be unit-tested without a DB.
Endpoint gating (PUT /caps admin-only, GET /aging accountant-gated) is checked
via the bare-app + get_current_user-override pattern from test_expenses_gating.
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

from api.routers import expenses  # noqa: E402
from api.routers.expenses import (  # noqa: E402
    resolve_cap,
    check_cap,
    has_blocking_advance,
    aging_bucket,
    compute_aging,
    _check_caps_for_roles,
)
from api.routers.auth import get_current_user  # noqa: E402


# ===========================================================================
# resolve_cap
# ===========================================================================


SAMPLE_CAPS = {
    "caps": [
        {"role": "SALES_STAFF", "category": "travel", "daily": 500, "monthly": 5000},
        {"role": "SALES_STAFF", "category": "food", "daily": 200, "monthly": None},
    ],
    "global": {"daily": 1000, "monthly": 20000},
}


class TestResolveCap:
    def test_exact_role_category_match(self):
        cap = resolve_cap("travel", "SALES_STAFF", SAMPLE_CAPS)
        assert cap["daily"] == 500
        assert cap["monthly"] == 5000
        assert cap["source"] == "role_category"

    def test_falls_back_to_global_when_no_match(self):
        cap = resolve_cap("rent", "STORE_MANAGER", SAMPLE_CAPS)
        assert cap["daily"] == 1000
        assert cap["monthly"] == 20000
        assert cap["source"] == "global"

    def test_no_config_means_no_limit(self):
        assert resolve_cap("travel", "SALES_STAFF", None) == {}
        assert resolve_cap("travel", "SALES_STAFF", {}) == {}

    def test_null_axis_is_no_limit(self):
        cap = resolve_cap("food", "SALES_STAFF", SAMPLE_CAPS)
        assert cap["daily"] == 200
        assert cap["monthly"] is None

    def test_malformed_entries_skipped(self):
        caps = {"caps": ["junk", None, {"role": "X"}], "global": {"daily": 50}}
        cap = resolve_cap("travel", "X", caps)
        # No exact match (entry has no category) -> global fallback.
        assert cap["daily"] == 50


# ===========================================================================
# check_cap -- daily vs monthly, global fallback, exactly-at-cap
# ===========================================================================


class TestCheckCap:
    def test_under_cap_ok(self):
        ok, reason = check_cap("travel", "SALES_STAFF", 100, 0, 0, SAMPLE_CAPS)
        assert ok is True
        assert reason == ""

    def test_daily_cap_exceeded(self):
        # 450 already spent today, +100 = 550 > 500 daily cap.
        ok, reason = check_cap("travel", "SALES_STAFF", 100, 450, 450, SAMPLE_CAPS)
        assert ok is False
        assert "Daily" in reason
        assert "50" in reason  # remaining headroom

    def test_monthly_cap_exceeded(self):
        # Daily fine (well under 500) but monthly 4950 + 100 = 5050 > 5000.
        ok, reason = check_cap("travel", "SALES_STAFF", 100, 0, 4950, SAMPLE_CAPS)
        assert ok is False
        assert "Monthly" in reason

    def test_exactly_at_daily_cap_allowed(self):
        # 400 + 100 = 500 == daily cap; <= cap passes.
        ok, reason = check_cap("travel", "SALES_STAFF", 100, 400, 400, SAMPLE_CAPS)
        assert ok is True
        assert reason == ""

    def test_exactly_at_monthly_cap_allowed(self):
        ok, _ = check_cap("travel", "SALES_STAFF", 100, 0, 4900, SAMPLE_CAPS)
        assert ok is True

    def test_one_over_daily_cap_rejected(self):
        # 400 + 101 = 501 > 500.
        ok, _ = check_cap("travel", "SALES_STAFF", 101, 400, 400, SAMPLE_CAPS)
        assert ok is False

    def test_daily_checked_before_monthly(self):
        # Both would be exceeded; message must name the daily cap first.
        ok, reason = check_cap("travel", "SALES_STAFF", 100, 500, 5000, SAMPLE_CAPS)
        assert ok is False
        assert "Daily" in reason

    def test_global_fallback_applies(self):
        # Unknown category -> global daily 1000. 950 + 100 = 1050 > 1000.
        ok, reason = check_cap("rent", "STORE_MANAGER", 100, 950, 950, SAMPLE_CAPS)
        assert ok is False
        assert "Daily" in reason

    def test_no_caps_never_blocks(self):
        ok, reason = check_cap("travel", "SALES_STAFF", 999999, 0, 0, {})
        assert ok is True
        assert reason == ""

    def test_null_monthly_axis_only_daily_enforced(self):
        # food: daily 200, monthly None. Huge monthly spend must NOT block.
        ok, _ = check_cap("food", "SALES_STAFF", 50, 100, 999999, SAMPLE_CAPS)
        assert ok is True
        # but daily still bites: 180 + 50 = 230 > 200.
        ok2, reason2 = check_cap("food", "SALES_STAFF", 50, 180, 999999, SAMPLE_CAPS)
        assert ok2 is False
        assert "Daily" in reason2

    def test_non_positive_amount_never_blocks(self):
        ok, _ = check_cap("travel", "SALES_STAFF", 0, 10000, 10000, SAMPLE_CAPS)
        assert ok is True


# ===========================================================================
# _check_caps_for_roles -- most-restrictive across multiple roles
# ===========================================================================


class TestCheckCapsForRoles:
    def test_most_restrictive_role_wins(self):
        caps = {
            "caps": [
                {"role": "SALES_STAFF", "category": "travel", "daily": 500},
                {"role": "STORE_MANAGER", "category": "travel", "daily": 100},
            ],
        }
        # Holding both roles, +200 exceeds the STORE_MANAGER 100 cap.
        ok, reason = _check_caps_for_roles(
            ["SALES_STAFF", "STORE_MANAGER"], "travel", 200, 0, 0, caps
        )
        assert ok is False
        assert "Daily" in reason

    def test_passes_when_all_roles_ok(self):
        ok, _ = _check_caps_for_roles(
            ["SALES_STAFF"], "travel", 100, 0, 0, SAMPLE_CAPS
        )
        assert ok is True

    def test_empty_roles_uses_global_fallback(self):
        ok, reason = _check_caps_for_roles([], "rent", 100, 950, 0, SAMPLE_CAPS)
        assert ok is False
        assert "Daily" in reason


# ===========================================================================
# has_blocking_advance
# ===========================================================================


class TestHasBlockingAdvance:
    def test_no_outstanding_not_blocked(self):
        assert has_blocking_advance([], None) is False
        assert has_blocking_advance(None, None) is False

    def test_outstanding_blocks_unlinked_claim(self):
        outstanding = [{"advance_id": "a1", "status": "DISBURSED"}]
        assert has_blocking_advance(outstanding, None) is True

    def test_linking_the_outstanding_advance_unblocks(self):
        outstanding = [{"advance_id": "a1", "status": "DISBURSED"}]
        assert has_blocking_advance(outstanding, "a1") is False

    def test_linking_a_different_advance_still_blocks(self):
        outstanding = [{"advance_id": "a1", "status": "DISBURSED"}]
        assert has_blocking_advance(outstanding, "a2") is True

    def test_linking_one_of_several_outstanding_unblocks(self):
        outstanding = [
            {"advance_id": "a1", "status": "DISBURSED"},
            {"advance_id": "a2", "status": "PARTIALLY_SETTLED"},
        ]
        assert has_blocking_advance(outstanding, "a2") is False


# ===========================================================================
# aging_bucket + compute_aging
# ===========================================================================


class TestAgingBucket:
    @pytest.mark.parametrize(
        "days,expected",
        [
            (0, "0-7"),
            (7, "0-7"),
            (8, "8-15"),
            (15, "8-15"),
            (16, "15+"),
            (90, "15+"),
            (-3, "0-7"),
        ],
    )
    def test_buckets(self, days, expected):
        assert aging_bucket(days) == expected

    def test_bad_input_defaults_to_first_bucket(self):
        assert aging_bucket("not-a-number") == "0-7"
        assert aging_bucket(None) == "0-7"


class TestComputeAging:
    def test_buckets_and_totals(self):
        now = datetime(2026, 5, 23, 12, 0, 0)
        expenses = [
            # 2 days old -> 0-7
            {
                "expense_id": "e1",
                "status": "APPROVED",
                "amount": 100,
                "approved_at": "2026-05-21T10:00:00",
            },
            # 10 days old -> 8-15
            {
                "expense_id": "e2",
                "status": "SENT_TO_ACCOUNTANT",
                "amount": 200,
                "sent_to_accountant_at": "2026-05-13T10:00:00",
            },
            # 30 days old -> 15+
            {
                "expense_id": "e3",
                "status": "APPROVED",
                "amount": 300,
                "approved_at": "2026-04-23T10:00:00",
            },
        ]
        out = compute_aging(expenses, now)
        assert out["buckets"]["0-7"]["count"] == 1
        assert out["buckets"]["0-7"]["amount"] == 100
        assert out["buckets"]["8-15"]["count"] == 1
        assert out["buckets"]["15+"]["count"] == 1
        assert out["total_count"] == 3
        assert out["total_amount"] == 600

    def test_only_open_states_counted(self):
        now = datetime(2026, 5, 23, 12, 0, 0)
        expenses = [
            {"expense_id": "e1", "status": "ENTERED", "amount": 100,
             "approved_at": "2026-04-01T10:00:00"},
            {"expense_id": "e2", "status": "PENDING", "amount": 100,
             "submitted_at": "2026-04-01T10:00:00"},
            {"expense_id": "e3", "status": "REJECTED", "amount": 100,
             "submitted_at": "2026-04-01T10:00:00"},
            {"expense_id": "e4", "status": "APPROVED", "amount": 50,
             "approved_at": "2026-05-22T10:00:00"},
        ]
        out = compute_aging(expenses, now)
        # Only the APPROVED row qualifies.
        assert out["total_count"] == 1
        assert out["total_amount"] == 50

    def test_rows_sorted_oldest_first(self):
        now = datetime(2026, 5, 23, 12, 0, 0)
        expenses = [
            {"expense_id": "new", "status": "APPROVED", "amount": 1,
             "approved_at": "2026-05-22T10:00:00"},
            {"expense_id": "old", "status": "APPROVED", "amount": 1,
             "approved_at": "2026-04-01T10:00:00"},
        ]
        out = compute_aging(expenses, now)
        assert out["rows"][0]["expense_id"] == "old"

    def test_empty_input(self):
        out = compute_aging([], datetime(2026, 5, 23))
        assert out["total_count"] == 0
        assert out["total_amount"] == 0.0
        assert out["buckets"]["0-7"]["count"] == 0


# ===========================================================================
# Endpoint gating -- PUT /caps admin-only, GET /aging accountant-gated
# ===========================================================================


def _client_as(roles):
    app = FastAPI()
    app.include_router(expenses.router, prefix="/expenses")

    async def _fake_user():
        return {
            "user_id": "u1",
            "full_name": "Test User",
            "active_store_id": "store-001",
            "roles": roles,
        }

    app.dependency_overrides[get_current_user] = _fake_user
    return TestClient(app)


_CAPS_BODY = {
    "caps": [{"role": "SALES_STAFF", "category": "travel", "daily": 500}],
    "global_cap": {"daily": 1000, "monthly": 20000},
}


class TestCapsEndpointGating:
    def test_put_caps_sales_staff_forbidden(self):
        client = _client_as(["SALES_STAFF"])
        resp = client.put("/expenses/caps", json=_CAPS_BODY)
        assert resp.status_code == 403

    def test_put_caps_cashier_forbidden(self):
        client = _client_as(["CASHIER"])
        resp = client.put("/expenses/caps", json=_CAPS_BODY)
        assert resp.status_code == 403

    def test_put_caps_store_manager_forbidden(self):
        # Cap config is ADMIN/SUPERADMIN only -- a store manager cannot edit it.
        client = _client_as(["STORE_MANAGER"])
        resp = client.put("/expenses/caps", json=_CAPS_BODY)
        assert resp.status_code == 403

    def test_put_caps_admin_not_forbidden(self):
        client = _client_as(["ADMIN"])
        resp = client.put("/expenses/caps", json=_CAPS_BODY)
        # No DB in unit context -> 503, but crucially NOT 403 (gate passed).
        assert resp.status_code != 403

    def test_put_caps_superadmin_not_forbidden(self):
        client = _client_as(["SUPERADMIN"])
        resp = client.put("/expenses/caps", json=_CAPS_BODY)
        assert resp.status_code != 403

    def test_get_caps_readable_by_staff(self):
        client = _client_as(["SALES_STAFF"])
        resp = client.get("/expenses/caps")
        assert resp.status_code == 200
        body = resp.json()
        assert "caps" in body
        assert "global" in body


class TestAgingEndpointGating:
    def test_aging_sales_staff_forbidden(self):
        client = _client_as(["SALES_STAFF"])
        resp = client.get("/expenses/aging")
        assert resp.status_code == 403

    def test_aging_accountant_allowed(self):
        client = _client_as(["ACCOUNTANT"])
        resp = client.get("/expenses/aging")
        assert resp.status_code != 403

    def test_aging_admin_allowed(self):
        client = _client_as(["ADMIN"])
        resp = client.get("/expenses/aging")
        assert resp.status_code != 403

    def test_aging_returns_bucket_shape(self):
        client = _client_as(["ADMIN"])
        resp = client.get("/expenses/aging")
        assert resp.status_code == 200
        body = resp.json()
        assert set(body["buckets"].keys()) == {"0-7", "8-15", "15+"}
