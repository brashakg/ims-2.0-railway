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
  2. GSTR-1 + GSTR-3B taxable != 0 for a taxable order (hermetic fake raw db).
  3. /reports/finance/gst returns non-zero taxable / tax (fake order repo).
  4. Date-ranged P&L filters expenses on `expense_date` (hermetic mini-pipeline).
  5. create_expense / request_advance raise (NOT 201) when the repo write
     returns None -- silent data loss must fail LOUDLY (SYSTEM_INTENT).

All tests are hermetic (fake repos / fake DB handles) -- no live Mongo, so they
cannot flake on CI's shared database regardless of test order.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")

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
# 2. GSTR-1 + GSTR-3B taxable != 0 (hermetic: fake raw db, no Mongo)
# ============================================================================


class _FakeRawCol:
    """Minimal pymongo-collection stand-in: find() returns all docs, find_one()
    the first. Filters are ignored -- the test controls the data."""

    def __init__(self, docs):
        self._docs = list(docs)

    def find(self, *args, **kwargs):
        return list(self._docs)

    def find_one(self, *args, **kwargs):
        return self._docs[0] if self._docs else None


class _FakeRawDB:
    def __init__(self, **collections):
        self._cols = {k: _FakeRawCol(v) for k, v in collections.items()}

    def __getitem__(self, name):
        return self._cols.get(name) or _FakeRawCol([])


def _gst_fake_order():
    """One walk-in (intra-state) COMPLETED taxable order for hermetic GSTR tests."""
    return {
        "order_number": "INV-GSTTEST-1",
        "order_id": "ORD-GSTTEST-1",
        "customer_id": "",  # walk-in -> no customer state -> intra-state default
        "customer_name": "Walk-in",
        "status": "COMPLETED",
        "created_at": datetime(2026, 3, 15, 10, 0, 0),
        "grand_total": 1180.0,
        "tax_amount": 180.0,
        "subtotal": 1200.0,
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


def _gst_fake_db():
    """Fake raw DB the GSTR endpoints read via reports._get_raw_db()."""
    return _FakeRawDB(
        orders=[_gst_fake_order()],
        stores=[
            {
                "store_id": "GSTTEST",
                "gstin": "27ABCDE1234F1Z5",
                "store_name": "GST Test Store",
                "state": "Maharashtra",
            }
        ],
        customers=[],
    )


def test_gstr1_taxable_not_zero(monkeypatch):
    # Hermetic (fake raw db) so it can't be perturbed by full-suite cross-test DB
    # state -- the real-Mongo version was the documented `finance_reporting` flake
    # (intermittent taxable=0). Same assertions, now deterministic.
    monkeypatch.setattr(reports, "_get_raw_db", _gst_fake_db)
    client = _client_for(reports.router, "/reports", ["ADMIN"], store_id="GSTTEST")
    resp = client.get("/reports/gstr1?month=2026-03&store_id=GSTTEST")
    assert resp.status_code == 200
    body = resp.json()
    # The bug: taxable was 0. The fix: taxable == grand_total - tax_amount = 1000.
    assert body["totalTaxableValue"] == 1000.0
    assert body["totalTax"] == 180.0
    # Walk-in with no customer state -> intra-state -> B2CS row carrying it.
    assert body["totalInvoices"] >= 1
    b2cs_taxable = sum(r["taxableValue"] for r in body["b2cs"])
    assert b2cs_taxable == 1000.0


def test_gstr3b_taxable_not_zero(monkeypatch):
    # Hermetic, same rationale as test_gstr1_taxable_not_zero.
    monkeypatch.setattr(reports, "_get_raw_db", _gst_fake_db)
    client = _client_for(reports.router, "/reports", ["ADMIN"], store_id="GSTTEST")
    resp = client.get("/reports/gstr3b?month=2026-03&store_id=GSTTEST")
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
# 4. Date-ranged P&L filters expenses on `expense_date` (hermetic mini-pipeline)
# ============================================================================
#
# The endpoint reads expenses via db.get_collection("expenses").aggregate([
#   {"$match": {... "expense_date": {"$gte","$lte"}, "status": {"$in"}}},
#   {"$group": {"_id": "$category", "amount": {"$sum": "$amount"}}}]).
# `_ExpensesCol` honours exactly those operators against the seeded docs, so the
# regression still bites if anyone reverts the field name back to `date` (the
# match key would miss the doc -> expense dropped -> total_expenses 0). Hermetic
# (no Mongo) so it can't flake on CI's shared db the way the real-Mongo version
# did (intermittent taxable/total 0 under full-suite cross-test load).


def _match_doc(doc, match):
    """True if `doc` satisfies a Mongo-style $match (eq / $gte / $lte / $in)."""
    for key, cond in match.items():
        val = doc.get(key)
        if isinstance(cond, dict):
            for op, opv in cond.items():
                if op == "$gte" and not (val is not None and val >= opv):
                    return False
                if op == "$lte" and not (val is not None and val <= opv):
                    return False
                if op == "$in" and val not in opv:
                    return False
        elif val != cond:
            return False
    return True


class _ExpensesCol:
    """Honours the 2-stage [$match, $group-by-$category-sum-$amount] pnl pipeline."""

    def __init__(self, docs):
        self._docs = list(docs)

    def aggregate(self, pipeline, *args, **kwargs):
        out = self._docs
        for stage in pipeline:
            if "$match" in stage:
                out = [d for d in out if _match_doc(d, stage["$match"])]
            elif "$group" in stage:
                grp = stage["$group"]
                field = grp["_id"][1:] if isinstance(grp["_id"], str) else None
                buckets: dict = {}
                for d in out:
                    key = d.get(field) if field else None
                    buckets[key] = buckets.get(key, 0.0) + (d.get("amount") or 0)
                out = [{"_id": k, "amount": v} for k, v in buckets.items()]
        return list(out)


class _EmptyCol:
    def aggregate(self, *args, **kwargs):
        return []

    def find(self, *args, **kwargs):
        return []


class _FakePnlDB:
    """Only `expenses` carries data; orders/products are empty (revenue 0)."""

    def __init__(self, expenses):
        self._exp = _ExpensesCol(expenses)

    def get_collection(self, name):
        return self._exp if name == "expenses" else _EmptyCol()


@pytest.fixture
def pnl_expenses():
    # One APPROVED RENT expense dated 2026-03-15 on the `expense_date` field
    # (the shape create_expense actually writes).
    return [
        {
            "expense_id": "EXP-PNLTEST",
            "store_id": "PNLTEST",
            "category": "RENT",
            "amount": 5000.0,
            "status": "APPROVED",
            "expense_date": "2026-03-15",
        }
    ]


def _patch_pnl(monkeypatch, expenses):
    monkeypatch.setattr(finance, "_get_db", lambda: _FakePnlDB(expenses))
    # COGS + payroll have their own tests; zero them so the assertion isolates
    # the expense date filter.
    monkeypatch.setattr(finance, "_cost_by_product", lambda _db: {})
    monkeypatch.setattr(finance, "_payroll_cost", lambda *a, **k: 0.0)


def test_pnl_includes_in_range_expense(monkeypatch, pnl_expenses):
    _patch_pnl(monkeypatch, pnl_expenses)
    client = _client_for(finance.router, "/finance", ["ADMIN"], store_id="PNLTEST")
    resp = client.get(
        "/finance/pnl?store_id=PNLTEST&from_date=2026-03-01&to_date=2026-03-31"
    )
    assert resp.status_code == 200
    body = resp.json()
    # The bug: filtering expenses on `date` (not `expense_date`) dropped ALL
    # of them whenever a range was supplied -> total_expenses 0.
    assert body["total_expenses"] == 5000.0
    assert body["expenses"].get("RENT") == 5000.0


def test_pnl_excludes_out_of_range_expense(monkeypatch, pnl_expenses):
    """A correct field-name filter must also EXCLUDE out-of-range expenses."""
    _patch_pnl(monkeypatch, pnl_expenses)
    client = _client_for(finance.router, "/finance", ["ADMIN"], store_id="PNLTEST")
    resp = client.get(
        "/finance/pnl?store_id=PNLTEST&from_date=2026-01-01&to_date=2026-01-31"
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
