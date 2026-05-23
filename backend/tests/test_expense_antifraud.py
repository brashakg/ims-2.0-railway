"""
IMS 2.0 - expense / GRN anti-fraud controls
===========================================
Two controls are exercised here:

  1. Duplicate-bill detection (expenses): the pure SHA-256 fingerprint helper
     (stable + hex) and the pure find_duplicate matcher (match / no-match),
     plus role gating on the GET /expenses/duplicate-bills watch-list.
  2. GRN-discrepancy detection (vendors): the pure grn_has_discrepancy
     thresholds (rejected lines, short/over shipment, clean receipt).

Endpoint tests reuse the bare-app + get_current_user-override pattern from
test_expenses_gating.py so they run without a database.
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

from api.routers import expenses, vendors  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402


# ---------------------------------------------------------------------------
# Pure: sha256_hex
# ---------------------------------------------------------------------------

class TestSha256Hex:
    def test_known_vector(self):
        # SHA-256 of the empty input is a well-known constant.
        assert expenses.sha256_hex(b"") == (
            "e3b0c44298fc1c149afbf4c8996fb924"
            "27ae41e4649b934ca495991b7852b855"
        )

    def test_stable_and_hex(self):
        h1 = expenses.sha256_hex(b"receipt-bytes")
        h2 = expenses.sha256_hex(b"receipt-bytes")
        assert h1 == h2                      # deterministic across calls
        assert len(h1) == 64                 # 256 bits -> 64 hex chars
        assert h1 == h1.lower()              # lowercase hex
        int(h1, 16)                          # parses as hex (raises if not)

    def test_different_bytes_differ(self):
        assert expenses.sha256_hex(b"a") != expenses.sha256_hex(b"b")

    def test_none_is_treated_as_empty(self):
        # Total: None must not crash the upload path.
        assert expenses.sha256_hex(None) == expenses.sha256_hex(b"")


# ---------------------------------------------------------------------------
# Pure: find_duplicate
# ---------------------------------------------------------------------------

class TestFindDuplicate:
    def test_match_returns_expense_id(self):
        existing = [
            {"expense_id": "E1", "bill_sha256": "aaa"},
            {"expense_id": "E2", "bill_sha256": "bbb"},
        ]
        assert expenses.find_duplicate("bbb", existing) == "E2"

    def test_no_match_returns_none(self):
        existing = [{"expense_id": "E1", "bill_sha256": "aaa"}]
        assert expenses.find_duplicate("zzz", existing) is None

    def test_empty_hash_never_matches(self):
        existing = [{"expense_id": "E1", "bill_sha256": ""}]
        assert expenses.find_duplicate("", existing) is None

    def test_empty_list_returns_none(self):
        assert expenses.find_duplicate("aaa", []) is None

    def test_first_match_wins(self):
        existing = [
            {"expense_id": "E1", "bill_sha256": "dup"},
            {"expense_id": "E2", "bill_sha256": "dup"},
        ]
        assert expenses.find_duplicate("dup", existing) == "E1"

    def test_tolerates_non_dict_rows(self):
        existing = [None, "junk", {"expense_id": "E9", "bill_sha256": "dup"}]
        assert expenses.find_duplicate("dup", existing) == "E9"


# ---------------------------------------------------------------------------
# Pure: grn_has_discrepancy
# ---------------------------------------------------------------------------

class TestGrnHasDiscrepancy:
    def test_clean_receipt_no_discrepancy(self):
        grn = {
            "items": [
                {"product_id": "P1", "ordered_qty": 10, "received_qty": 10,
                 "accepted_qty": 10, "rejected_qty": 0},
            ],
            "total_ordered": 10,
            "total_received": 10,
        }
        assert vendors.grn_has_discrepancy(grn) is False

    def test_rejected_line_is_discrepancy(self):
        grn = {"items": [{"product_id": "P1", "received_qty": 10,
                          "accepted_qty": 8, "rejected_qty": 2}]}
        assert vendors.grn_has_discrepancy(grn) is True

    def test_short_shipment_is_discrepancy(self):
        grn = {"items": [{"product_id": "P1", "ordered_qty": 10,
                          "received_qty": 7, "rejected_qty": 0}]}
        assert vendors.grn_has_discrepancy(grn) is True

    def test_over_shipment_is_discrepancy(self):
        grn = {"items": [{"product_id": "P1", "ordered_qty": 10,
                          "received_qty": 12, "rejected_qty": 0}]}
        assert vendors.grn_has_discrepancy(grn) is True

    def test_within_tolerance_no_discrepancy(self):
        grn = {"items": [{"product_id": "P1", "ordered_qty": 10,
                          "received_qty": 9, "rejected_qty": 0}]}
        assert vendors.grn_has_discrepancy(grn, qty_tolerance=1) is False

    def test_total_backstop_when_no_line_ordered(self):
        # No per-line ordered_qty, but totals disagree -> discrepancy.
        grn = {"items": [{"product_id": "P1", "received_qty": 7}],
               "total_ordered": 10, "total_received": 7}
        assert vendors.grn_has_discrepancy(grn) is True

    def test_no_ordered_info_no_false_positive(self):
        # Nothing to compare against and nothing rejected -> no discrepancy.
        grn = {"items": [{"product_id": "P1", "received_qty": 7}]}
        assert vendors.grn_has_discrepancy(grn) is False

    def test_malformed_input_is_total(self):
        assert vendors.grn_has_discrepancy(None) is False
        assert vendors.grn_has_discrepancy({}) is False
        assert vendors.grn_has_discrepancy({"items": "not-a-list"}) is False
        # garbage qty values coerce to 0, not a crash
        assert vendors.grn_has_discrepancy(
            {"items": [{"received_qty": "x", "ordered_qty": "y"}]}
        ) is False


# ---------------------------------------------------------------------------
# Endpoint gating: GET /expenses/duplicate-bills
# ---------------------------------------------------------------------------

def _expenses_client_as(roles):
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


class TestDuplicateBillsGating:
    def test_sales_staff_blocked(self):
        resp = _expenses_client_as(["SALES_STAFF"]).get("/expenses/duplicate-bills")
        assert resp.status_code == 403

    def test_cashier_blocked(self):
        resp = _expenses_client_as(["CASHIER"]).get("/expenses/duplicate-bills")
        assert resp.status_code == 403

    def test_accountant_allowed(self):
        resp = _expenses_client_as(["ACCOUNTANT"]).get("/expenses/duplicate-bills")
        assert resp.status_code != 403

    def test_store_manager_allowed(self):
        resp = _expenses_client_as(["STORE_MANAGER"]).get("/expenses/duplicate-bills")
        assert resp.status_code != 403

    def test_superadmin_allowed(self):
        resp = _expenses_client_as(["SUPERADMIN"]).get("/expenses/duplicate-bills")
        assert resp.status_code != 403

    def test_returns_empty_envelope_without_db(self):
        # No DB wired -> fail-soft empty list (not a 500).
        resp = _expenses_client_as(["ADMIN"]).get("/expenses/duplicate-bills")
        assert resp.status_code == 200
        body = resp.json()
        assert body == {"expenses": [], "total": 0}
