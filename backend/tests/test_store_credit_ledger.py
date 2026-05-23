"""
IMS 2.0 - store-credit / credit-note ledger
============================================
Pure ledger math (issue / redeem / adjust + running balance) and role gating on
the issue/redeem endpoints.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.services import store_credit_ledger as scl  # noqa: E402
from api.routers import customers  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402


def test_issue_adds_credit():
    e = scl.make_entry("c1", "ISSUED", 500, current_balance=0)
    assert e["delta"] == 500 and e["balance_after"] == 500
    assert e["type"] == "ISSUED"


def test_redeem_within_balance():
    e = scl.make_entry("c1", "REDEEMED", 200, current_balance=500)
    assert e["delta"] == -200 and e["balance_after"] == 300


def test_redeem_over_balance_rejected():
    with pytest.raises(ValueError):
        scl.make_entry("c1", "REDEEMED", 600, current_balance=500)


def test_issue_nonpositive_rejected():
    with pytest.raises(ValueError):
        scl.make_entry("c1", "ISSUED", 0, current_balance=0)


def test_adjusted_can_be_negative_but_not_below_zero():
    e = scl.make_entry("c1", "ADJUSTED", -100, current_balance=300)
    assert e["balance_after"] == 200
    with pytest.raises(ValueError):
        scl.make_entry("c1", "ADJUSTED", -400, current_balance=300)


def test_bad_type_rejected():
    with pytest.raises(ValueError):
        scl.make_entry("c1", "BOGUS", 100, current_balance=0)


def test_compute_balance_sums_deltas():
    entries = [{"delta": 500}, {"delta": -200}, {"delta": -50}]
    assert scl.compute_balance(entries) == 250


def _client_as(roles):
    app = FastAPI()
    app.include_router(customers.router, prefix="/customers")

    async def _user():
        return {"user_id": "u1", "active_store_id": "s1", "roles": roles}

    app.dependency_overrides[get_current_user] = _user
    return TestClient(app)


class TestLedgerGating:
    def test_sales_staff_blocked_issue(self):
        r = _client_as(["SALES_STAFF"]).post(
            "/customers/c1/store-credit/issue", json={"amount": 100}
        )
        assert r.status_code == 403

    def test_sales_staff_blocked_redeem(self):
        r = _client_as(["SALES_STAFF"]).post(
            "/customers/c1/store-credit/redeem", json={"amount": 100}
        )
        assert r.status_code == 403

    def test_accountant_allowed(self):
        # Allowed past the gate (no DB -> 503/404, but NOT 403).
        r = _client_as(["ACCOUNTANT"]).post(
            "/customers/c1/store-credit/issue", json={"amount": 100}
        )
        assert r.status_code != 403
