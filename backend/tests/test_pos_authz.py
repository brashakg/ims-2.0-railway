"""
IMS 2.0 - POS authorization + separation-of-duties (auth-hardening regression)
==============================================================================
QA role-matrix agents found:
  - POST /orders had NO role gate -> ACCOUNTANT / OPTOMETRIST / CATALOG_MANAGER /
    WORKSHOP_STAFF could all create POS orders.
  - Order-level cart_discount_percent + add-item discount were not cap-checked
    (any role could apply up to 100% off). [cart-cap is verified live against a
    real DB; here we lock the role gate + self-approval, which gate before any DB]
  - Expense / advance approval had no requester != approver check (SYSTEM_INTENT
    s7 separation of duties).
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import orders, expenses  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402


def _orders_client(roles, uid="u1"):
    app = FastAPI()
    app.include_router(orders.router, prefix="/api/v1/orders")

    async def _u():
        return {"user_id": uid, "full_name": "T", "roles": roles,
                "store_ids": ["S1"], "active_store_id": "S1", "discount_cap": None}

    app.dependency_overrides[get_current_user] = _u
    return TestClient(app)


_ORDER_BODY = {
    "customer_id": "walkin-qa",
    "items": [{"item_type": "FRAME", "product_id": "custom-qa", "product_name": "QA",
               "quantity": 1, "unit_price": 1000, "discount_percent": 0}],
}


# --- POST /orders role gate (fires before any DB) ------------------------

def test_accountant_cannot_create_order():
    assert _orders_client(["ACCOUNTANT"]).post("/api/v1/orders", json=_ORDER_BODY).status_code == 403


def test_optometrist_cannot_create_order():
    assert _orders_client(["OPTOMETRIST"]).post("/api/v1/orders", json=_ORDER_BODY).status_code == 403


def test_workshop_staff_cannot_create_order():
    assert _orders_client(["WORKSHOP_STAFF"]).post("/api/v1/orders", json=_ORDER_BODY).status_code == 403


def test_catalog_manager_cannot_create_order():
    assert _orders_client(["CATALOG_MANAGER"]).post("/api/v1/orders", json=_ORDER_BODY).status_code == 403


def test_sales_staff_passes_order_role_gate():
    # SALES_STAFF clears the POS gate (downstream may vary without a DB; assert it's
    # NOT the 403 role rejection).
    assert _orders_client(["SALES_STAFF"]).post("/api/v1/orders", json=_ORDER_BODY).status_code != 403


# --- Expense / advance self-approval (SYSTEM_INTENT s7) ------------------

class _FakeExpenseRepo:
    def __init__(self, doc):
        self._doc = doc
        self.updated = None

    def find_by_id(self, _id):
        return dict(self._doc) if self._doc else None

    def update(self, _id, data):
        self.updated = data
        return True


def _expenses_client(roles, uid, repo, monkeypatch):
    monkeypatch.setattr(expenses, "get_expense_repository", lambda: repo)
    app = FastAPI()
    app.include_router(expenses.router, prefix="/api/v1/expenses")

    async def _u():
        return {"user_id": uid, "full_name": "T", "roles": roles,
                "store_ids": ["S1"], "active_store_id": "S1"}

    app.dependency_overrides[get_current_user] = _u
    return TestClient(app)


def test_manager_cannot_approve_own_expense(monkeypatch):
    repo = _FakeExpenseRepo({"expense_id": "e1", "employee_id": "sm1",
                             "status": "PENDING", "expense_date": "2026-05-29"})
    c = _expenses_client(["STORE_MANAGER"], "sm1", repo, monkeypatch)
    assert c.post("/api/v1/expenses/e1/approve").status_code == 403
    assert repo.updated is None  # never approved


def test_manager_can_approve_others_expense(monkeypatch):
    repo = _FakeExpenseRepo({"expense_id": "e1", "employee_id": "sm1",
                             "status": "PENDING", "expense_date": "2026-05-29"})
    c = _expenses_client(["STORE_MANAGER"], "sm2", repo, monkeypatch)  # different approver
    assert c.post("/api/v1/expenses/e1/approve").status_code != 403


def test_manager_cannot_reject_own_expense(monkeypatch):
    repo = _FakeExpenseRepo({"expense_id": "e1", "employee_id": "sm1",
                             "status": "PENDING", "expense_date": "2026-05-29"})
    c = _expenses_client(["STORE_MANAGER"], "sm1", repo, monkeypatch)
    assert c.post("/api/v1/expenses/e1/reject", params={"reason": "dup"}).status_code == 403
