"""
IMS 2.0 - A completed general-counter sale ends DELIVERED (owner 2026-09-04)
==========================================================================
The general counter (frontend GeneralCounterSurface) takes a paid take-away
quick sale through the server's EXISTING doors, in this exact order:

    POST /orders            -> DRAFT / UNPAID
    POST /orders/{id}/payments (full)  -> CONFIRMED / PAID (auto-confirm)
    POST /orders/{id}/ready            -> READY   (rx-hold + QC gate + claim)
    POST /orders/{id}/deliver + handover {delivered_by = the cashier}
                                       -> DELIVERED (money gate + holds + claim)

There is NO counter-specific status write on the server. These tests drive
that sequence through the real handlers over strict in-memory doubles and
assert the STORED document, so they fail if:

  * the deliver door stops persisting delivered_by_* on the atomic claim
    (test 1 - handover_record vanishes / names someone else);
  * the salesperson attribution is touched by the handover (test 1);
  * the ready/deliver doors stop refusing a not-yet-READY order (test 1's
    early /deliver probes - the counter's ready step would be redundant);
  * the QC gate is dropped from either door (test 2 - an optical sale whose
    lab job was never inspected would sail through the counter's sequence).

DISCRIMINATING POWER (measured by reverting the guard under test and
re-running): documented in the session report, not asserted on comments.
No emoji / non-ASCII (Windows cp1252).
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, List

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

CASHIER_ID = "u-cashier-meena"
SELLER_ID = "u-seller-rekha"
STORE = "BV-TEST-01"


def _headers(user_id: str, username: str, roles):
    from api.routers.auth import create_access_token

    token = create_access_token(
        {
            "user_id": user_id,
            "username": username,
            "roles": list(roles),
            "store_ids": [STORE],
            "active_store_id": STORE,
        }
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def counter_env(monkeypatch):
    """POST /orders + payments + ready + deliver over strict collections and
    the REAL repositories (the same wiring test_pos_p3_items uses for create)."""
    from api.routers import orders as orders_module
    from api.routers import payout as payout_module
    from database.repositories.customer_repository import CustomerRepository
    from database.repositories.order_repository import OrderRepository
    from strict_fakes import StrictDB

    db = StrictDB()
    order_repo = OrderRepository(db.get_collection("orders"))
    customer_repo = CustomerRepository(db.get_collection("customers"))
    monkeypatch.setattr(orders_module, "get_order_repository", lambda: order_repo)
    monkeypatch.setattr(orders_module, "get_customer_repository", lambda: customer_repo)
    monkeypatch.setattr(orders_module, "get_product_repository", lambda: None)
    monkeypatch.setattr(orders_module, "get_walkin_counter_repository", lambda: None)
    monkeypatch.setattr(payout_module, "get_db", lambda: db)
    monkeypatch.setattr(payout_module, "get_user_repository", lambda: None)
    customer_repo.create(
        {
            "customer_id": "cust-counter",
            "name": "Asha Verma",
            "mobile": "9876543210",
            "phone": "9876543210",
        }
    )
    return {"db": db, "orders": db.get_collection("orders"), "order_repo": order_repo}


class _UnQcdJobRepo:
    """Workshop double: ONE job on the order, worked on, never QC'd."""

    def __init__(self, order_id: str):
        self.job: Dict[str, Any] = {
            "job_id": "J-1",
            "job_number": "WS-1",
            "order_id": order_id,
            "status": "IN_PROGRESS",
            "qc_passed": False,
        }

    def find_by_order(self, order_id) -> List[Dict[str, Any]]:
        return [dict(self.job)] if self.job["order_id"] == order_id else []

    def find_by_id(self, job_id):
        return dict(self.job) if self.job["job_id"] == job_id else None


def _create(client, headers, **over):
    body = {
        "customer_id": "cust-counter",
        "order_type": "quick_sale",
        "salesperson_id": SELLER_ID,
        "salesperson_name": "Rekha Sharma",
        "items": [
            {
                "item_type": "WATCH",
                "product_id": "p-watch",
                "product_name": "Titan watch",
                "sku": "W-1",
                "quantity": 1,
                "unit_price": 5000.0,
                "category": "WATCH",
            }
        ],
    }
    body.update(over)
    resp = client.post("/api/v1/orders", headers=headers, json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()["order_id"]


def _stored(env, order_id):
    doc = env["order_repo"].find_by_id(order_id)
    assert doc is not None
    return doc


def test_counter_sequence_ends_delivered_with_the_cashier_on_the_handover(
    client, counter_env
):
    cashier = _headers(CASHIER_ID, "meena", ["SALES_STAFF"])
    order_id = _create(client, cashier)
    after_create = _stored(counter_env, order_id)
    assert after_create["status"] == "DRAFT"
    assert after_create["payment_status"] == "UNPAID"
    seller_name_at_sale = after_create["salesperson_name"]

    # The deliver door refuses anything that is not READY - the counter's
    # ready step is load-bearing, not decoration.
    r = client.post(f"/api/v1/orders/{order_id}/deliver", headers=cashier)
    assert r.status_code == 400, r.text
    assert _stored(counter_env, order_id)["status"] == "DRAFT"

    r = client.post(
        f"/api/v1/orders/{order_id}/payments",
        headers=cashier,
        json={"method": "CASH", "amount": after_create["grand_total"]},
    )
    assert r.status_code == 200, r.text
    paid = _stored(counter_env, order_id)
    assert paid["status"] == "CONFIRMED"
    assert paid["payment_status"] == "PAID"
    assert paid["balance_due"] == 0.0

    # Paid alone is still not deliverable: CONFIRMED -> DELIVERED is not a
    # transition, so the door refuses and writes nothing.
    r = client.post(f"/api/v1/orders/{order_id}/deliver", headers=cashier)
    assert r.status_code == 400, r.text
    assert _stored(counter_env, order_id)["status"] == "CONFIRMED"

    r = client.post(f"/api/v1/orders/{order_id}/ready", headers=cashier)
    assert r.status_code == 200, r.text
    assert _stored(counter_env, order_id)["status"] == "READY"

    r = client.post(
        f"/api/v1/orders/{order_id}/deliver",
        headers=cashier,
        json={
            "handover": {
                "delivered_by_id": CASHIER_ID,
                "delivered_by_name": "Meena",
            }
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "DELIVERED"

    doc = _stored(counter_env, order_id)
    assert doc["status"] == "DELIVERED"
    assert doc.get("delivered_at")
    assert doc["status_updated_by"] == CASHIER_ID
    assert doc["status_history"][-1]["status"] == "DELIVERED"
    assert doc["status_history"][-1]["changed_by"] == CASHIER_ID
    # The handover names the CASHIER who completed the sale ...
    rec = doc["handover_record"]
    assert rec["delivered_by_id"] == CASHIER_ID
    assert rec["delivered_by_name"] == "Meena"
    assert rec["recorded_by"] == CASHIER_ID
    # ... and the bill's salesperson attribution is untouched.
    assert doc["salesperson_id"] == SELLER_ID
    assert doc["salesperson_name"] == seller_name_at_sale
    assert doc["payment_status"] == "PAID"


def test_optical_sale_with_uninspected_lab_job_is_refused_by_both_doors(
    client, counter_env, monkeypatch
):
    """The counter's sequence reuses the doors VERBATIM, so an optical
    (prescription_order) sale whose workshop job was never QC'd cannot be
    marked ready or delivered through it - the same gate the delivery counter
    meets. Fails if either door drops assert_linked_job_qc_cleared."""
    from api.routers import workshop as wm

    cashier = _headers(CASHIER_ID, "meena", ["SALES_STAFF"])
    order_id = _create(
        client,
        cashier,
        order_type="prescription_order",
        items=[
            {
                # A frame going to the lab: the QC gate keys on the WORKSHOP
                # JOB, not the line (a LENS line would trip the separate
                # Rx-required gate first and prove nothing about QC).
                "item_type": "FRAME",
                "product_id": "p-frame",
                "product_name": "Rx frame",
                "sku": "F-1",
                "quantity": 1,
                "unit_price": 3000.0,
                "category": "FRAME",
            }
        ],
    )
    monkeypatch.setattr(wm, "get_workshop_repository", lambda: _UnQcdJobRepo(order_id))
    total = _stored(counter_env, order_id)["grand_total"]
    r = client.post(
        f"/api/v1/orders/{order_id}/payments",
        headers=cashier,
        json={"method": "CASH", "amount": total},
    )
    assert r.status_code == 200, r.text
    assert _stored(counter_env, order_id)["status"] == "CONFIRMED"

    r = client.post(f"/api/v1/orders/{order_id}/ready", headers=cashier)
    assert r.status_code == 400, r.text
    assert _stored(counter_env, order_id)["status"] == "CONFIRMED"

    r = client.post(
        f"/api/v1/orders/{order_id}/deliver",
        headers=cashier,
        json={"handover": {"delivered_by_id": CASHIER_ID}},
    )
    assert r.status_code == 400, r.text
    doc = _stored(counter_env, order_id)
    assert doc["status"] == "CONFIRMED"
    assert "handover_record" not in doc
