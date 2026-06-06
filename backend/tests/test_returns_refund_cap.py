"""BUG-096: returns must (1) only run on a returnable order status (reject
CANCELLED/DRAFT/REFUNDED) and (2) cap the cumulative net refund across all
COMPLETED returns at the order's amount_paid. Reuses the GST-refund fakes."""
from __future__ import annotations

import os
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

from api.routers import returns as returns_router  # noqa: E402
from tests.test_returns_gst_refund import (  # noqa: E402
    _FakeOrderRepo,
    _FakeCustomerRepo,
    _FakeColl,
    _qa_order,
    _qa_payload,
    _staff_token,
)

_HDR = {"Authorization": f"Bearer {_staff_token(['ADMIN'])}"}


def _ctx(monkeypatch, *, amount_paid=1_000_000.0, status="DELIVERED", qty=1):
    order = _qa_order()
    order["status"] = status
    order["amount_paid"] = amount_paid
    order["items"][0]["quantity"] = qty
    app = FastAPI()
    app.include_router(returns_router.router, prefix="/api/v1/returns")
    order_repo = _FakeOrderRepo(order)
    returns_coll = _FakeColl()
    ledger_coll = _FakeColl()

    class _FakeDB:
        is_connected = True

        def __init__(self):
            self.db = self

        def get_collection(self, name):
            return {"returns": returns_coll, "credit_note_ledger": ledger_coll}.get(name, _FakeColl())

    fake_db = _FakeDB()
    monkeypatch.setattr(returns_router, "get_order_repository", lambda: order_repo)
    monkeypatch.setattr(returns_router, "get_customer_repository", lambda: _FakeCustomerRepo())
    monkeypatch.setattr(returns_router, "get_product_repository", lambda: None)
    monkeypatch.setattr(returns_router, "get_stock_repository", lambda: None)
    monkeypatch.setattr("api.dependencies.get_db", lambda: fake_db, raising=False)
    monkeypatch.setattr("api.dependencies.get_audit_repository", lambda: None, raising=False)
    return TestClient(app), returns_coll


def test_reject_cancelled_order(monkeypatch):
    client, _ = _ctx(monkeypatch, status="CANCELLED")
    r = client.post("/api/v1/returns", json=_qa_payload(), headers=_HDR)
    assert r.status_code == 400, r.text
    assert "cancelled" in r.text.lower() or "status" in r.text.lower()


def test_reject_draft_order(monkeypatch):
    client, _ = _ctx(monkeypatch, status="DRAFT")
    r = client.post("/api/v1/returns", json=_qa_payload(), headers=_HDR)
    assert r.status_code == 400, r.text


def test_reject_refund_above_amount_paid(monkeypatch):
    # Refund gross ~1404 but only Rs 500 was paid -> blocked.
    client, _ = _ctx(monkeypatch, amount_paid=500.0)
    r = client.post("/api/v1/returns", json=_qa_payload(), headers=_HDR)
    assert r.status_code == 400, r.text
    assert "exceed" in r.text.lower()


def test_reject_refund_on_never_paid_order(monkeypatch):
    client, _ = _ctx(monkeypatch, amount_paid=0.0)
    r = client.post("/api/v1/returns", json=_qa_payload(), headers=_HDR)
    assert r.status_code == 400, r.text


def test_accept_refund_within_amount_paid(monkeypatch):
    client, _ = _ctx(monkeypatch, amount_paid=2000.0)
    r = client.post("/api/v1/returns", json=_qa_payload(), headers=_HDR)
    assert r.status_code in (200, 201), r.text


def test_cumulative_refund_capped(monkeypatch):
    # qty 2 line (gross ~2808), amount_paid 2000. First return of 1 (1404) ok;
    # second return of 1 (cumulative 2808 > 2000) blocked by the monetary cap.
    client, _ = _ctx(monkeypatch, amount_paid=2000.0, qty=2)
    r1 = client.post("/api/v1/returns", json=_qa_payload(), headers=_HDR)
    assert r1.status_code in (200, 201), r1.text
    r2 = client.post("/api/v1/returns", json=_qa_payload(), headers=_HDR)
    assert r2.status_code == 400, r2.text
    assert "exceed" in r2.text.lower()
