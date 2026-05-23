"""
IMS 2.0 - variance-driven task automation + integrity detectors
===============================================================
Pure threshold/detection logic, create_system_task dedupe, and role gating on
the scan/integrity endpoints.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.services.task_triggers import (  # noqa: E402
    create_system_task,
    is_suspicious_closure,
    payment_anomalies,
    silent_tasks,
    stock_variance_priority,
)
from api.routers import tasks  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402


def test_stock_variance_priority():
    assert stock_variance_priority(12.0, -12.0) == "P1"   # big shrink
    assert stock_variance_priority(3.0, -3.0) == "P2"     # real shrink
    assert stock_variance_priority(0.0, 11.0) == "P2"     # big positive swing
    assert stock_variance_priority(0.5, 0.5) is None      # within tolerance


def test_payment_anomalies():
    orders = [
        {"order_id": "O1", "grand_total": 100, "amount_paid": 150},                       # OVERPAID
        {"order_id": "O2", "grand_total": 100, "amount_paid": 100,
         "payments": [{"amount": 60}, {"amount": 10}]},                                   # PAYMENTS_MISMATCH
        {"order_id": "O3", "grand_total": 100, "amount_paid": 40, "status": "DELIVERED"}, # UNBALANCED_CLOSED
        {"order_id": "O4", "grand_total": 100, "amount_paid": 100,
         "payments": [{"amount": 100}], "status": "DELIVERED"},                            # clean
    ]
    kinds = {a["order_id"]: a["kind"] for a in payment_anomalies(orders)}
    assert kinds == {"O1": "OVERPAID", "O2": "PAYMENTS_MISMATCH", "O3": "UNBALANCED_CLOSED"}


def test_is_suspicious_closure():
    now = datetime.now()
    fast = {"status": "COMPLETED", "created_at": now, "completed_at": now + timedelta(seconds=3)}
    slow = {"status": "COMPLETED", "created_at": now, "completed_at": now + timedelta(minutes=30)}
    opent = {"status": "OPEN", "created_at": now}
    assert is_suspicious_closure(fast) is True
    assert is_suspicious_closure(slow) is False
    assert is_suspicious_closure(opent) is False


def test_silent_tasks():
    now = datetime.now()
    silent = {"task_id": "T1", "status": "OPEN", "priority": "P0",
              "created_at": now - timedelta(minutes=30)}   # P0 ack=15m -> silent
    fresh = {"task_id": "T2", "status": "OPEN", "priority": "P2",
             "created_at": now - timedelta(minutes=5)}     # P2 ack=240m -> not yet
    acked = {"task_id": "T3", "status": "IN_PROGRESS", "priority": "P0",
             "created_at": now - timedelta(hours=5)}        # not OPEN
    ids = {t["task_id"] for t in silent_tasks([silent, fresh, acked], now=now)}
    assert ids == {"T1"}


class _FakeRepo:
    def __init__(self, existing=None):
        self._existing = existing or []
        self.created = None

    def find_many(self, *_a, **_k):
        return self._existing

    def create(self, doc):
        self.created = doc
        return doc


def test_create_system_task_creates_and_dedupes():
    repo = _FakeRepo()
    t = create_system_task(repo, title="x", description="d", priority="P2",
                           category="Inventory", store_id="s1", dedupe_ref="stockcount:c1")
    assert t is not None and repo.created["source"] == "SYSTEM"
    assert repo.created["source_ref"] == "stockcount:c1"

    # An existing ACTIVE task with the same ref -> dedupe (no create).
    repo2 = _FakeRepo(existing=[{"source_ref": "stockcount:c1", "status": "OPEN"}])
    assert create_system_task(repo2, title="x", description="d", priority="P2",
                              category="Inventory", store_id="s1",
                              dedupe_ref="stockcount:c1") is None
    assert repo2.created is None


def _client_as(roles):
    app = FastAPI()
    app.include_router(tasks.router, prefix="/tasks")

    async def _user():
        return {"user_id": "u1", "active_store_id": "s1", "roles": roles}

    app.dependency_overrides[get_current_user] = _user
    return TestClient(app)


class TestScanGating:
    def test_sales_staff_blocked_payment_scan(self):
        assert _client_as(["SALES_STAFF"]).post("/tasks/scan/payment-variance").status_code == 403

    def test_sales_staff_blocked_integrity(self):
        assert _client_as(["SALES_STAFF"]).get("/tasks/integrity/fake-closures").status_code == 403
        assert _client_as(["SALES_STAFF"]).get("/tasks/integrity/silent").status_code == 403

    def test_manager_allowed(self):
        assert _client_as(["STORE_MANAGER"]).get("/tasks/integrity/silent").status_code != 403
        assert _client_as(["STORE_MANAGER"]).post("/tasks/scan/payment-variance").status_code != 403
