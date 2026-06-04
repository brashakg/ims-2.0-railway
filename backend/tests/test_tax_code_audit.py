"""
IMS 2.0 — Go-live tax-code audit report tests
==============================================
Verifies GET /reports/inventory/tax-code-audit, the read-only pre-go-live check
that flags products whose stored HSN / GST rate disagrees with the canonical
gst_rates table for their category.

  - Auth + role guard (finance roles only).
  - DB-absent path -> empty envelope.
  - A correctly-coded product is NOT flagged.
  - A wrong GST rate -> CRITICAL flag.
  - A wrong HSN (and a 4-digit prefix that should be ACCEPTED) .
  - A blank/unknown category -> uncategorized flag.
  - Summary counts + CRITICAL-first ordering.
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, List

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ============================================================================
# Mongo stubs (mirror test_non_moving_stock's FakeDB pattern)
# ============================================================================
class FakeCollection:
    def __init__(self, docs: List[Dict[str, Any]]):
        self._docs = docs

    def find(self, _filter=None):
        # The endpoint queries {"is_active": {"$ne": False}} (optionally with a
        # store $or). Honor the is_active $ne filter; ignore store scoping here.
        if _filter and "is_active" in _filter:
            cond = _filter["is_active"]
            if isinstance(cond, dict) and "$ne" in cond:
                bad = cond["$ne"]
                return [d for d in self._docs if d.get("is_active", True) != bad]
        return list(self._docs)


class FakeDB:
    def __init__(self, products):
        self.is_connected = True
        self._products = products

    def get_collection(self, name):
        if name == "products":
            return FakeCollection(self._products)
        return FakeCollection([])


@pytest.fixture
def patched_db(monkeypatch):
    from api.routers import reports as reports_module

    def install(products):
        db = FakeDB(products)
        monkeypatch.setattr(reports_module, "get_db", lambda: db)
        return db

    return install


_URL = "/api/v1/reports/inventory/tax-code-audit"


# ============================================================================
# Auth / envelope
# ============================================================================
def test_requires_auth(client):
    assert client.get(_URL).status_code == 401


def test_empty_envelope_when_db_absent(client, auth_headers, monkeypatch):
    from api.routers import reports as reports_module

    class _NoDB:
        is_connected = False

        def get_collection(self, _name):  # pragma: no cover - never reached
            return None

    monkeypatch.setattr(reports_module, "get_db", lambda: _NoDB())
    resp = client.get(_URL, headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"] == []
    assert body["summary"]["total_products"] == 0
    assert body["summary"]["flagged"] == 0


# ============================================================================
# Classification
# ============================================================================
def test_correct_product_not_flagged(client, auth_headers, patched_db):
    # FRAME canonical = (900311, 5.0).
    patched_db(
        [
            {
                "product_id": "P1",
                "name": "Ray-Ban RB123",
                "category": "FRAME",
                "hsn_code": "900311",
                "gst_rate": 5.0,
                "is_active": True,
            }
        ]
    )
    body = client.get(_URL, headers=auth_headers).json()
    assert body["summary"]["total_products"] == 1
    assert body["summary"]["flagged"] == 0
    assert body["summary"]["ok"] == 1
    assert body["data"] == []


def test_wrong_gst_rate_is_critical(client, auth_headers, patched_db):
    # SUNGLASS canonical = (900410, 18.0); this row says 5.0 -> CRITICAL.
    patched_db(
        [
            {
                "product_id": "P2",
                "name": "Fastrack Sunnies",
                "category": "SUNGLASS",
                "hsn_code": "900410",
                "gst_rate": 5.0,
                "is_active": True,
            }
        ]
    )
    body = client.get(_URL, headers=auth_headers).json()
    assert body["summary"]["flagged"] == 1
    assert body["summary"]["gst_mismatch"] == 1
    row = body["data"][0]
    assert row["severity"] == "CRITICAL"
    assert row["expected_gst"] == 18.0
    assert any("GST" in i for i in row["issues"])


def test_four_digit_hsn_prefix_accepted(client, auth_headers, patched_db):
    # A <=Rs 5 Cr business may use a 4-digit HSN "9003" for a frame; canonical is
    # 900311. The 4-digit prefix must NOT be flagged. GST still correct (5.0).
    patched_db(
        [
            {
                "product_id": "P3",
                "name": "Local Frame",
                "category": "FRAME",
                "hsn_code": "9003",
                "gst_rate": 5.0,
                "is_active": True,
            }
        ]
    )
    body = client.get(_URL, headers=auth_headers).json()
    assert body["summary"]["flagged"] == 0
    assert body["summary"]["hsn_mismatch"] == 0


def test_wrong_hsn_flagged_high(client, auth_headers, patched_db):
    # GST correct (5.0) but HSN is an unrelated code -> HIGH (not CRITICAL).
    patched_db(
        [
            {
                "product_id": "P4",
                "name": "Mislabeled Frame",
                "category": "FRAME",
                "hsn_code": "847130",
                "gst_rate": 5.0,
                "is_active": True,
            }
        ]
    )
    body = client.get(_URL, headers=auth_headers).json()
    assert body["summary"]["flagged"] == 1
    assert body["summary"]["hsn_mismatch"] == 1
    row = body["data"][0]
    assert row["severity"] == "HIGH"
    assert any("HSN" in i for i in row["issues"])


def test_blank_category_is_uncategorized(client, auth_headers, patched_db):
    patched_db(
        [
            {
                "product_id": "P5",
                "name": "Mystery SKU",
                "category": "",
                "hsn_code": "",
                "gst_rate": None,
                "is_active": True,
            }
        ]
    )
    body = client.get(_URL, headers=auth_headers).json()
    assert body["summary"]["flagged"] == 1
    assert body["summary"]["uncategorized"] == 1
    assert body["data"][0]["category"] is None


def test_inactive_products_ignored(client, auth_headers, patched_db):
    patched_db(
        [
            {
                "product_id": "P6",
                "name": "Retired wrong-tax frame",
                "category": "SUNGLASS",
                "hsn_code": "900410",
                "gst_rate": 5.0,  # wrong, but inactive so skipped
                "is_active": False,
            }
        ]
    )
    body = client.get(_URL, headers=auth_headers).json()
    assert body["summary"]["total_products"] == 0
    assert body["summary"]["flagged"] == 0


def test_critical_sorts_above_high(client, auth_headers, patched_db):
    patched_db(
        [
            {
                "product_id": "HSN_ONLY",
                "name": "HSN issue only",
                "category": "FRAME",
                "hsn_code": "847130",
                "gst_rate": 5.0,
                "is_active": True,
            },
            {
                "product_id": "GST_WRONG",
                "name": "GST wrong",
                "category": "SUNGLASS",
                "hsn_code": "900410",
                "gst_rate": 5.0,
                "is_active": True,
            },
        ]
    )
    body = client.get(_URL, headers=auth_headers).json()
    assert body["summary"]["flagged"] == 2
    # CRITICAL (wrong GST) must come first.
    assert body["data"][0]["severity"] == "CRITICAL"
    assert body["data"][0]["product_id"] == "GST_WRONG"
    assert body["data"][1]["severity"] == "HIGH"
