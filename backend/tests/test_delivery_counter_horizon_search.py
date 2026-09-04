"""
IMS 2.0 - Delivery counter: 30-day horizon, name/phone search, staff handover
=============================================================================
Owner, 2026-09-02:
  "let users search through 30 days pending delivery data, except admin and
   superadmin let them have full data. also let users search through customer
   name and phone number too"
  "who is giving delivery to the customer (salesprson) should also be logged"

Discriminating power (each control was deleted and the matching test re-run):
  * drop the drop_rows_before_horizon call -> the negative clamp tests see the
    40-day row and fail
  * drop customer_scoped=...               -> the exemption test loses that row
  * drop the ?q= branch                    -> name / phone search find nothing
  * drop delivered_by_* from HandoverDetails -> Pydantic silently discards them
    and the round-trip test fails

The clamp is on ``created_at``, a real BSON date: BaseRepository._add_timestamps
writes ``datetime.now()``, and OrderRepository.create_unique (the POST /orders
write path) runs it. It is deliberately NOT on ``expected_delivery``, which the
order create stores as an ISO STRING - a datetime bound there matches nothing.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

STORE = "BV-TEST-01"
NOW = datetime.now()
IN_WINDOW = NOW - timedelta(days=3)
OUT_OF_WINDOW = NOW - timedelta(days=40)


@pytest.fixture
def counter(monkeypatch):
    """Order + customer repositories wired into the orders router."""
    from tests.test_walkouts import FakeDB
    from api.routers import orders as orders_module
    from database.repositories.order_repository import OrderRepository
    from database.repositories.customer_repository import CustomerRepository

    fake_db = FakeDB()
    order_repo = OrderRepository(fake_db.get_collection("orders"))
    customer_repo = CustomerRepository(fake_db.get_collection("customers"))

    monkeypatch.setattr(orders_module, "get_order_repository", lambda: order_repo)
    monkeypatch.setattr(
        orders_module, "get_customer_repository", lambda: customer_repo
    )
    return {"orders": order_repo, "customers": customer_repo}


def _seed_order(repo, order_id, created_at, **over):
    doc = {
        "order_id": order_id,
        "order_number": "ORD-BOK01-2026-" + order_id.upper().replace("-", ""),
        "store_id": STORE,
        "customer_id": "cust-1",
        "customer_name": "Rakesh Kumar",
        "customer_phone": "9876543210",
        "status": "READY",
        "items": [],
        "grand_total": 1000.0,
        "amount_paid": 1000.0,
        "balance_due": 0.0,
        "payment_status": "PAID",
    }
    doc.update(over)
    repo.create(doc)
    # create() stamps created_at = now; overwrite it to the age under test.
    repo.collection.update_one(
        {"order_id": order_id}, {"$set": {"created_at": created_at}}
    )
    return doc


def _seed_customer(repo):
    repo.create(
        {
            "customer_id": "cust-1",
            "name": "Rakesh Kumar",
            "mobile": "9876543210",
            "home_store_id": STORE,
        }
    )


def _headers(roles, user_id="u-test", **extra):
    from api.routers.auth import create_access_token

    return {
        "Authorization": "Bearer "
        + create_access_token(
            {
                "user_id": user_id,
                "username": user_id,
                "roles": roles,
                "store_ids": [STORE],
                "active_store_id": STORE,
                **extra,
            }
        )
    }


def _ids(resp):
    return {o["id"] for o in resp.json()["orders"]}


def _get(client, headers, **params):
    return client.get(
        "/api/v1/orders/pending/delivery", headers=headers, params=params
    )


# ---------------------------------------------------------------------------
# TASK 1 - the 30-day window on the pending-delivery queue
# ---------------------------------------------------------------------------


def test_staff_cannot_browse_a_pending_order_older_than_the_window(client, counter):
    """NEGATIVE. The point of the control: a 40-day-old row must not come back
    to a cashier browsing the queue."""
    _seed_order(counter["orders"], "ord-old", OUT_OF_WINDOW)
    r = _get(client, _headers(["SALES_CASHIER"]))
    assert r.status_code == 200, r.text
    assert "ord-old" not in _ids(r)


def test_staff_still_see_an_in_window_pending_order(client, counter):
    """POSITIVE CONTROL. The clamp must narrow the queue, not empty it - an
    empty delivery screen is worse than the problem it fixes."""
    _seed_order(counter["orders"], "ord-new", IN_WINDOW)
    r = _get(client, _headers(["SALES_CASHIER"]))
    assert r.status_code == 200, r.text
    assert "ord-new" in _ids(r)


@pytest.mark.parametrize("role", ["ADMIN", "SUPERADMIN"])
def test_admin_and_superadmin_see_the_whole_queue(client, counter, role):
    """The owner's exemption. Same 40-day row, unrestricted role, still there."""
    _seed_order(counter["orders"], "ord-old", OUT_OF_WINDOW)
    _seed_order(counter["orders"], "ord-new", IN_WINDOW)
    r = _get(client, _headers([role]))
    assert r.status_code == 200, r.text
    assert _ids(r) == {"ord-old", "ord-new"}


def test_a_store_manager_is_still_clamped(client, counter):
    """UNRESTRICTED_ROLES is ADMIN + SUPERADMIN only. A manager runs a shop."""
    _seed_order(counter["orders"], "ord-old", OUT_OF_WINDOW)
    r = _get(client, _headers(["STORE_MANAGER"]))
    assert "ord-old" not in _ids(r)


def test_naming_the_customer_lifts_the_window(client, counter):
    """The named-lookup exemption, decided by the SAME _query_names_one_customer
    GET /orders/search uses. The 40-day pair is exactly the one the customer
    standing at the counter has come for."""
    _seed_customer(counter["customers"])
    _seed_order(counter["orders"], "ord-old", OUT_OF_WINDOW)
    r = _get(client, _headers(["SALES_CASHIER"]), q="Rakesh")
    assert r.status_code == 200, r.text
    assert "ord-old" in _ids(r)


def test_a_fuzzy_search_that_names_nobody_is_still_clamped(client, counter):
    """The exemption must not become the bypass: an order-number fragment
    resolves to no customer, so it is browsing and stays inside the window."""
    _seed_customer(counter["customers"])
    _seed_order(counter["orders"], "ord-old", OUT_OF_WINDOW)
    r = _get(client, _headers(["SALES_CASHIER"]), q="ORD")
    assert r.status_code == 200, r.text
    assert "ord-old" not in _ids(r)


# ---------------------------------------------------------------------------
# TASK 2 - search by customer name and phone
# ---------------------------------------------------------------------------


def _seed_decoy(repo):
    """A second customer's order, also READY and also in-window. Every search
    test asserts it is EXCLUDED -- otherwise a broken ?q= that silently falls
    back to the whole queue would still "find" the target and prove nothing."""
    return _seed_order(
        repo,
        "ord-decoy",
        IN_WINDOW,
        customer_id="cust-2",
        customer_name="Sunita Devi",
        customer_phone="9000000001",
    )


def test_search_by_customer_name_finds_the_order(client, counter):
    _seed_customer(counter["customers"])
    _seed_order(counter["orders"], "ord-new", IN_WINDOW)
    _seed_decoy(counter["orders"])
    r = _get(client, _headers(["SALES_CASHIER"]), q="Rakesh")
    assert r.status_code == 200, r.text
    assert _ids(r) == {"ord-new"}


def test_search_by_phone_number_finds_the_order(client, counter):
    _seed_customer(counter["customers"])
    _seed_order(counter["orders"], "ord-new", IN_WINDOW)
    _seed_decoy(counter["orders"])
    r = _get(client, _headers(["SALES_CASHIER"]), q="9876543210")
    assert r.status_code == 200, r.text
    assert _ids(r) == {"ord-new"}


def test_search_by_order_number_still_works(client, counter):
    _seed_order(counter["orders"], "ord-new", IN_WINDOW)
    _seed_decoy(counter["orders"])
    r = _get(client, _headers(["SALES_CASHIER"]), q="ORD-BOK01-2026-ORDNEW")
    assert r.status_code == 200, r.text
    assert _ids(r) == {"ord-new"}


def test_search_does_not_return_orders_that_are_not_awaiting_collection(
    client, counter
):
    """The queue is still the queue. A DELIVERED order for the same customer
    must not leak into the pending list through the search."""
    _seed_customer(counter["customers"])
    _seed_order(counter["orders"], "ord-done", IN_WINDOW, status="DELIVERED")
    r = _get(client, _headers(["SALES_CASHIER"]), q="Rakesh")
    assert r.status_code == 200, r.text
    assert "ord-done" not in _ids(r)


def test_search_does_not_return_another_customers_order(client, counter):
    _seed_customer(counter["customers"])
    _seed_order(
        counter["orders"],
        "ord-other",
        IN_WINDOW,
        customer_name="Sunita Devi",
        customer_phone="9000000001",
    )
    r = _get(client, _headers(["SALES_CASHIER"]), q="Rakesh")
    assert "ord-other" not in _ids(r)


# ---------------------------------------------------------------------------
# TASK 3 - who handed the goods over
# ---------------------------------------------------------------------------


def _deliver(client, headers, handover):
    return client.post(
        "/api/v1/orders/ord-new/deliver",
        json={"handover": handover},
        headers=headers,
    )


def test_delivered_by_round_trips_and_is_not_the_customer_side(client, counter):
    """delivered_by_* is the STAFF who handed over; picked_up_by_name is the
    person who collected. Both are stored, and they stay distinct."""
    _seed_order(counter["orders"], "ord-new", IN_WINDOW)
    r = _deliver(
        client,
        _headers(["STORE_MANAGER"], user_id="u-mgr"),
        {
            "delivered_by_id": "u-priya",
            "delivered_by_name": "Priya S",
            "picked_up_by_name": "Ravi (brother)",
        },
    )
    assert r.status_code == 200, r.text
    rec = counter["orders"].find_by_id("ord-new")["handover_record"]
    assert rec["delivered_by_id"] == "u-priya"
    assert rec["delivered_by_name"] == "Priya S"
    assert rec["picked_up_by_name"] == "Ravi (brother)"
    assert rec["delivered_by_name"] != rec["picked_up_by_name"]
    # The authenticated actor is recorded separately, so the audit trail never
    # depends on what the client claimed for delivered_by_*.
    assert rec["recorded_by"] == "u-mgr"


def test_delivered_by_falls_back_to_the_signed_in_staff_member(client, counter):
    """A handover recorded without naming the deliverer must still answer
    'who gave this customer their glasses'."""
    _seed_order(counter["orders"], "ord-new", IN_WINDOW)
    r = _deliver(
        client,
        _headers(["STORE_MANAGER"], user_id="u-mgr"),
        {"picked_up_by_name": "Self", "fit_check_done": True},
    )
    assert r.status_code == 200, r.text
    rec = counter["orders"].find_by_id("ord-new")["handover_record"]
    assert rec["delivered_by_id"] == "u-mgr"
    assert rec["delivered_by_name"] == "u-mgr"  # username fallback
    assert rec["picked_up_by_name"] == "Self"


def test_delivered_by_survives_collect_and_deliver(client, counter):
    """The merged counter door delegates to deliver_order, so the staff stamp
    must ride that path too."""
    _seed_order(
        counter["orders"],
        "ord-new",
        IN_WINDOW,
        amount_paid=400.0,
        balance_due=600.0,
        payment_status="PARTIAL",
        payments=[{"payment_id": "p-dep", "method": "CASH", "amount": 400.0}],
    )
    r = client.post(
        "/api/v1/orders/ord-new/deliver-with-payment",
        json={
            "payment": {"method": "CASH", "amount": 600.0},
            "handover": {
                "delivered_by_id": "u-priya",
                "delivered_by_name": "Priya S",
            },
        },
        headers=_headers(["STORE_MANAGER"], user_id="u-mgr"),
    )
    assert r.status_code == 200, r.text
    rec = counter["orders"].find_by_id("ord-new")["handover_record"]
    assert rec["delivered_by_name"] == "Priya S"
