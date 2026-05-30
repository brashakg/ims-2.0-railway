"""
IMS 2.0 - Finance / GST reporting-correctness regressions
=========================================================
A cluster of GST + P&L reports read field names that orders / expenses never
persist, or filter datetime columns with ISO strings, so they silently
returned 0. These tests lock in the fixes.

Persisted ORDER docs store `subtotal` (PRE-cart-discount GROSS sum, NOT the
taxable base), `tax_amount` (total GST) and `grand_total` (what the customer
pays). orders._compute_per_category_gst guarantees taxable + tax ==
grand_total, so the correct GST taxable value is grand_total - tax_amount
(NOT subtotal). The per-line items also carry taxable_value + tax_amount.

Coverage:
  1. _order_taxable_and_tax pure helper (taxable == grand_total - tax_amount,
     line fallback, empty).
  2. GSTR-1 + GSTR-3B taxable != 0 for a taxable order, against a REAL Mongo
     (seeded + cleaned up under a unique store id).
  3. /reports/finance/gst returns non-zero taxable / tax (fake order repo).
  4. Date-ranged P&L includes an in-range expense (real Mongo, unique store).
  5. create_expense / request_advance raise (NOT 201) when the repo write
     returns None -- silent data loss must fail LOUDLY (SYSTEM_INTENT).

Mongo is reached via JWT_SECRET_KEY=test MONGO_HOST=127.0.0.1 MONGO_PORT=27017.
The DB-backed tests skip cleanly if Mongo is unreachable; no live network.
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGO_HOST", "127.0.0.1")
os.environ.setdefault("MONGO_PORT", "27017")

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import reports, finance, expenses  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402


# ============================================================================
# Shared helpers
# ============================================================================


def _client_for(router, prefix, roles, store_id="store-gst-test"):
    app = FastAPI()
    app.include_router(router, prefix=prefix)

    async def _fake_user():
        return {
            "user_id": "u1",
            "username": "tester",
            "full_name": "Test User",
            "active_store_id": store_id,
            "store_ids": [store_id],
            "roles": roles,
        }

    app.dependency_overrides[get_current_user] = _fake_user
    return TestClient(app)


def _raw_db_or_skip():
    """Return a connected raw pymongo Database, else skip the test."""
    try:
        from database.connection import get_db

        conn = get_db()
        if conn is None:
            pytest.skip("No DB connection available")
        # `.db` lazily connects; `is_connected` is False until then.
        raw = conn.db
        if raw is None or not conn.is_connected:
            pytest.skip("Mongo not connected")
        # Ping to be sure.
        raw.command("ping")
        return conn, raw
    except Exception as exc:  # pragma: no cover - env dependent
        pytest.skip(f"Mongo unreachable: {exc}")


# ============================================================================
# 1. Pure helper: _order_taxable_and_tax
# ============================================================================


class TestOrderTaxableHelper:
    def test_taxable_is_grand_total_minus_tax(self):
        # grand_total - tax_amount, NOT subtotal (which is the pre-discount gross).
        order = {"subtotal": 1200.0, "grand_total": 1180.0, "tax_amount": 180.0}
        taxable, tax = reports._order_taxable_and_tax(order)
        assert taxable == 1000.0
        assert tax == 180.0
        # Invariant the whole fix rests on.
        assert round(taxable + tax, 2) == order["grand_total"]

    def test_inclusive_order_exact(self):
        order = {"grand_total": 999.0, "tax_amount": 47.57}
        taxable, tax = reports._order_taxable_and_tax(order)
        assert taxable == 951.43
        assert tax == 47.57
        assert round(taxable + tax, 2) == 999.0

    def test_cart_discount_does_not_use_subtotal(self):
        # subtotal (1200) overstates; with a cart discount grand_total is lower.
        order = {"subtotal": 1200.0, "grand_total": 1000.0, "tax_amount": 152.54}
        taxable, _ = reports._order_taxable_and_tax(order)
        assert taxable == round(1000.0 - 152.54, 2)
        assert taxable != 1200.0

    def test_line_fallback_when_no_grand_total(self):
        order = {"items": [{"taxable_value": 500.0, "tax_amount": 90.0}]}
        taxable, tax = reports._order_taxable_and_tax(order)
        assert taxable == 500.0
        assert tax == 90.0

    def test_empty_order_is_zero(self):
        assert reports._order_taxable_and_tax({}) == (0.0, 0.0)


# ============================================================================
# 2. GSTR-1 + GSTR-3B taxable != 0 (real Mongo, unique store, cleaned up)
# ============================================================================


@pytest.fixture
def seeded_gst_store():
    """Seed one store + one taxable order in a real Mongo under a unique store
    id, yield (store_id, month), then delete what we inserted."""
    conn, raw = _raw_db_or_skip()
    store_id = f"GSTTEST-{uuid.uuid4().hex[:8]}"
    # 2026-03: middle-of-period date so the inclusive monthrange filter catches it.
    created = datetime(2026, 3, 15, 10, 0, 0)
    raw["stores"].insert_one(
        {
            "store_id": store_id,
            "store_name": "GST Test Store",
            "state": "Maharashtra",
            "gstin": "27ABCDE1234F1Z5",
        }
    )
    raw["orders"].insert_one(
        {
            "order_id": f"ORD-{store_id}",
            "order_number": "INV-GSTTEST-1",
            "store_id": store_id,
            "customer_id": "",  # walk-in -> intra-state (same as store)
            "customer_name": "Walk-in",
            "status": "COMPLETED",
            "created_at": created,
            "subtotal": 1200.0,  # pre-discount gross (must NOT be the taxable)
            "tax_amount": 180.0,
            "grand_total": 1180.0,
            "items": [
                {
                    "product_name": "Ray-Ban Sunglass",
                    "hsn_code": "9004",
                    "gst_rate": 18,
                    "taxable_value": 1000.0,
                    "tax_amount": 180.0,
                    "item_total": 1180.0,
                }
            ],
        }
    )
    try:
        yield store_id, "2026-03"
    finally:
        raw["stores"].delete_many({"store_id": store_id})
        raw["orders"].delete_many({"store_id": store_id})


def test_gstr1_taxable_not_zero(seeded_gst_store):
    store_id, month = seeded_gst_store
    client = _client_for(reports.router, "/reports", ["ADMIN"], store_id=store_id)
    resp = client.get(f"/reports/gstr1?month={month}&store_id={store_id}")
    assert resp.status_code == 200
    body = resp.json()
    # The bug: taxable was 0. The fix: taxable == grand_total - tax_amount = 1000.
    assert body["totalTaxableValue"] == 1000.0
    assert body["totalTax"] == 180.0
    # Walk-in with no customer state -> intra-state -> B2CS row carrying it.
    assert body["totalInvoices"] >= 1
    b2cs_taxable = sum(r["taxableValue"] for r in body["b2cs"])
    assert b2cs_taxable == 1000.0


def test_gstr3b_taxable_not_zero(seeded_gst_store):
    store_id, month = seeded_gst_store
    client = _client_for(reports.router, "/reports", ["ADMIN"], store_id=store_id)
    resp = client.get(f"/reports/gstr3b?month={month}&store_id={store_id}")
    assert resp.status_code == 200
    body = resp.json()
    # Outward taxable value must equal grand_total - tax_amount = 1000, not 0.
    assert body["outwardTaxableValue"] == 1000.0
    # Intra-state -> CGST + SGST split of the 180 GST.
    sup = body["outwardTaxableSupplies"]
    assert sup["centralTax"] == 90.0
    assert sup["stateTax"] == 90.0
    assert sup["integratedTax"] == 0.0


# ============================================================================
# 3. /reports/finance/gst non-zero (fake order repo + fake raw db)
# ============================================================================


class _FakeOrderRepo:
    def __init__(self, orders):
        self._orders = orders
        self.last_filter = None
        self.last_limit = None

    def find_many(self, filter_dict, *args, limit=100, **kwargs):
        self.last_filter = filter_dict
        self.last_limit = limit
        return list(self._orders)


def test_reports_finance_gst_non_zero(monkeypatch):
    orders = [
        {
            "order_number": "INV-1",
            "order_id": "ORD-1",
            "customer_id": "",
            "created_at": datetime(2026, 3, 10, 9, 0, 0),
            "grand_total": 1180.0,
            "tax_amount": 180.0,
            "subtotal": 1200.0,
            "items": [{"taxable_value": 1000.0, "tax_amount": 180.0}],
        }
    ]
    repo = _FakeOrderRepo(orders)
    monkeypatch.setattr(reports, "get_order_repository", lambda: repo)
    # No raw db -> store/customer state maps stay empty -> intra-state default.
    monkeypatch.setattr(reports, "_get_raw_db", lambda: None)

    client = _client_for(reports.router, "/reports", ["ACCOUNTANT"])
    resp = client.get(
        "/reports/finance/gst?from_date=2026-03-01&to_date=2026-03-31"
    )
    assert resp.status_code == 200
    body = resp.json()
    s = body["summary"]
    # The bug: every total was 0 (legacy field names). Fix derives them.
    assert s["total_taxable"] == 1000.0
    assert s["total_tax"] == 180.0
    # Intra-state -> CGST + SGST, no IGST.
    assert s["total_cgst"] == 90.0
    assert s["total_sgst"] == 90.0
    assert s["total_igst"] == 0.0
    assert body["data"][0]["taxable_amount"] == 1000.0
    # And the date filter now uses real datetimes, not isoformat strings.
    assert isinstance(repo.last_filter["created_at"]["$gte"], datetime)
    # Unbounded so a full GST report includes every invoice.
    assert repo.last_limit == 0


# ============================================================================
# 4. Date-ranged P&L includes an in-range expense (real Mongo, unique store)
# ============================================================================


@pytest.fixture
def seeded_pnl_store():
    conn, raw = _raw_db_or_skip()
    store_id = f"PNLTEST-{uuid.uuid4().hex[:8]}"
    raw["expenses"].insert_one(
        {
            "expense_id": f"EXP-{store_id}",
            "store_id": store_id,
            "category": "RENT",
            "amount": 5000.0,
            "status": "APPROVED",
            # Stored on `expense_date` as a date-only ISO string (the actual
            # shape create_expense writes). The OLD code filtered `date`.
            "expense_date": "2026-03-15",
        }
    )
    try:
        yield store_id
    finally:
        raw["expenses"].delete_many({"store_id": store_id})


def test_pnl_includes_in_range_expense(seeded_pnl_store):
    store_id = seeded_pnl_store
    client = _client_for(finance.router, "/finance", ["ADMIN"], store_id=store_id)
    resp = client.get(
        f"/finance/pnl?store_id={store_id}&from_date=2026-03-01&to_date=2026-03-31"
    )
    assert resp.status_code == 200
    body = resp.json()
    # The bug: filtering expenses on `date` (not `expense_date`) dropped ALL
    # of them whenever a range was supplied -> total_expenses 0.
    assert body["total_expenses"] == 5000.0
    assert body["expenses"].get("RENT") == 5000.0


def test_pnl_excludes_out_of_range_expense(seeded_pnl_store):
    """A correct field-name filter must also EXCLUDE out-of-range expenses."""
    store_id = seeded_pnl_store
    client = _client_for(finance.router, "/finance", ["ADMIN"], store_id=store_id)
    resp = client.get(
        f"/finance/pnl?store_id={store_id}&from_date=2026-01-01&to_date=2026-01-31"
    )
    assert resp.status_code == 200
    # The March expense is outside Jan -> excluded.
    assert resp.json()["total_expenses"] == 0


# ============================================================================
# 5. create_expense / request_advance FAIL LOUDLY on a swallowed write
# ============================================================================


class _NoneCreateRepo:
    """Mimics BaseRepository.create swallowing an exception -> returns None."""

    def create(self, doc):
        return None

    def find_many(self, *args, **kwargs):
        return []


def _expenses_client(monkeypatch, expense_repo=None, advance_repo=None):
    app = FastAPI()
    app.include_router(expenses.router, prefix="/expenses")

    async def _fake_user():
        return {
            "user_id": "u1",
            "full_name": "Test User",
            "active_store_id": "store-001",
            "roles": ["SALES_STAFF"],
        }

    app.dependency_overrides[get_current_user] = _fake_user
    if expense_repo is not None:
        monkeypatch.setattr(expenses, "get_expense_repository", lambda: expense_repo)
    if advance_repo is not None:
        monkeypatch.setattr(expenses, "get_advance_repository", lambda: advance_repo)
    # No outstanding advances (so the new advance guard doesn't trip here).
    monkeypatch.setattr(expenses, "_outstanding_advances", lambda *_a, **_k: [])
    # No period lock.
    monkeypatch.setattr(expenses, "_period_locked", lambda *_a, **_k: False)
    return TestClient(app)


def test_create_expense_raises_on_write_failure(monkeypatch):
    client = _expenses_client(monkeypatch, expense_repo=_NoneCreateRepo())
    resp = client.post(
        "/expenses",
        json={
            "category": "TRAVEL",
            "amount": 100.0,
            "description": "cab fare",
            "expense_date": "2026-05-21",
        },
    )
    # The bug: 201 with a client-minted id even though nothing persisted.
    # The fix: surface the failure (503), never a false success.
    assert resp.status_code == 503
    assert "persist" in resp.json()["detail"].lower()


def test_create_expense_succeeds_when_write_ok(monkeypatch):
    class _OkRepo:
        def create(self, doc):
            return doc  # truthy -> persisted

    client = _expenses_client(monkeypatch, expense_repo=_OkRepo())
    resp = client.post(
        "/expenses",
        json={
            "category": "TRAVEL",
            "amount": 100.0,
            "description": "cab fare",
            "expense_date": "2026-05-21",
        },
    )
    assert resp.status_code == 201
    assert resp.json()["expense_id"]


def test_request_advance_raises_on_write_failure(monkeypatch):
    client = _expenses_client(monkeypatch, advance_repo=_NoneCreateRepo())
    resp = client.post(
        "/expenses/advances",
        json={"advance_type": "CASH", "amount": 5000.0, "purpose": "travel"},
    )
    assert resp.status_code == 503
    assert "persist" in resp.json()["detail"].lower()


def test_request_advance_blocked_when_outstanding(monkeypatch):
    """A second advance is blocked while an unsettled one exists."""
    app = FastAPI()
    app.include_router(expenses.router, prefix="/expenses")

    async def _fake_user():
        return {
            "user_id": "u1",
            "full_name": "Test User",
            "active_store_id": "store-001",
            "roles": ["SALES_STAFF"],
        }

    app.dependency_overrides[get_current_user] = _fake_user

    class _OkRepo:
        def create(self, doc):
            return doc

    monkeypatch.setattr(expenses, "get_advance_repository", lambda: _OkRepo())
    # An outstanding (DISBURSED) advance exists for this employee.
    monkeypatch.setattr(
        expenses,
        "_outstanding_advances",
        lambda *_a, **_k: [{"advance_id": "ADV-OLD", "status": "DISBURSED"}],
    )
    client = TestClient(app)
    resp = client.post(
        "/expenses/advances",
        json={"advance_type": "CASH", "amount": 5000.0, "purpose": "travel"},
    )
    assert resp.status_code == 400
    assert "outstanding" in resp.json()["detail"].lower()


def test_request_advance_allowed_when_none_outstanding(monkeypatch):
    """No outstanding advance -> the request goes through."""
    client = _expenses_client(
        monkeypatch,
        advance_repo=type("_Ok", (), {"create": lambda self, d: d})(),
    )
    resp = client.post(
        "/expenses/advances",
        json={"advance_type": "CASH", "amount": 5000.0, "purpose": "travel"},
    )
    assert resp.status_code == 201
    assert resp.json()["advance_id"]
