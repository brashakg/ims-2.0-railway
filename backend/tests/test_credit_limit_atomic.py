"""Real payment handler + Mongo CAS, with no external database or services."""
import asyncio
import importlib
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
from threading import Barrier, local

import mongomock
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from api.routers.auth import get_current_user
from api.routers.orders import payments
from api.routers.orders.pricing import PaymentCreate
from api.routers import customers, settings
from api.services import credit_limit
from database.repositories.order_repository import OrderRepository


USER = {"user_id": "admin", "roles": ["ADMIN"], "active_store_id": "S1"}


class Customers:
    def __init__(self, collection):
        self.collection = collection

    def find_by_id(self, cid):
        return self.collection.find_one({"customer_id": cid})


@pytest.fixture
def env(monkeypatch):
    db = mongomock.MongoClient().db
    db.customers.insert_one({"customer_id": "C1", "credit_limit": 100, "phone": "9876543210"})
    db.admin_controls.insert_one({"_id": "default", "operational_rules": {"default_credit_limit": 100}})
    repo = OrderRepository(db.orders)
    customer_repo = Customers(db.customers)
    monkeypatch.setattr(payments, "get_order_repository", lambda: repo)
    monkeypatch.setattr(payments, "get_customer_repository", lambda: customer_repo)
    monkeypatch.setattr(customers, "get_customer_repository", lambda: customer_repo)
    # Credential-encryption tests reload settings. Patch both our collected
    # reference and the live module used by the call-time credit-default read.
    for module in {settings, importlib.import_module("api.routers.settings")}:
        monkeypatch.setattr(module, "_get_settings_collection", lambda name: db[name])
    app = FastAPI()
    app.add_api_route("/orders/{order_id}/payments", payments.add_payment, methods=["POST"])
    app.include_router(customers.router, prefix="/customers")
    app.dependency_overrides[get_current_user] = lambda: USER
    return db, repo, TestClient(app)


def sale(db, oid="O1", total=60, customer_id="C1", **extra):
    doc = {"order_id": oid, "customer_id": customer_id, "store_id": "S1", "status": "CONFIRMED",
           "grand_total": total, "balance_due": total, "amount_paid": 0,
           "payments": [], "payment_status": "UNPAID", **extra}
    db.orders.insert_one(deepcopy(doc))
    return doc


def post(client, oid="O1", amount=1, **kwargs):
    return client.post(f"/orders/{oid}/payments", json={"method": "CREDIT", "amount": amount}, **kwargs)


@pytest.mark.parametrize("cid", [None, "", "walk-in", "walkin-123", "C1"])
def test_every_credit_sale_checks_the_entire_unpaid_balance(env, cid):
    db, _, client = env
    sale(db, total=101, customer_id=cid)
    response = post(client, amount=1)
    assert response.status_code == 400
    assert "Credit limit exceeded" in response.json()["detail"]
    assert db.orders.find_one({"order_id": "O1"})["payments"] == []


@pytest.mark.parametrize("cid", [None, "walkin-123", "C1"])
def test_exact_limit_allows_cash_plus_credit_without_counting_cash_twice(env, cid):
    db, _, client = env
    sale(db, total=120, customer_id=cid, amount_paid=20, balance_due=100,
         payments=[{"method": "CASH", "amount": 20}])
    assert post(client, amount=100).status_code == 200
    order = db.orders.find_one({"order_id": "O1"})
    assert (order["amount_paid"], order["balance_due"], order["payment_status"]) == (20, 100, "CREDIT")


def test_walkins_do_not_share_an_unrelated_persons_account(env):
    db, _, client = env
    sale(db, "O1", 100, "walk-in")
    sale(db, "O2", 100, "walk-in")
    assert post(client, "O1").status_code == post(client, "O2").status_code == 200


def test_prior_phone_matched_credit_and_repeated_credit_count_each_sale_once(env):
    db, _, client = env
    sale(db, "OLD", 40, "legacy-id", customer_phone="9876543210",
         payments=[{"method": "CREDIT", "amount": 40}], payment_status="CREDIT")
    sale(db, total=60)
    assert post(client).status_code == 200
    assert post(client).status_code == 200
    sale(db, "O2", 1)
    assert post(client, "O2").status_code == 400


def test_no_500_order_cap_on_outstanding_credit(env):
    db, _, client = env
    db.customers.update_one({"customer_id": "C1"}, {"$set": {"credit_limit": 600}})
    for n in range(501):
        sale(db, f"OLD-{n}", 1, payments=[{"method": "CREDIT", "amount": 1}])
    sale(db, total=100)
    assert post(client).status_code == 400


def test_settlement_frees_capacity_and_cancelled_orders_do_not_count(env):
    db, repo, client = env
    sale(db, "OLD", 100, payments=[{"method": "CREDIT", "amount": 100}])
    sale(db, "CANCEL", 1000, status="CANCELLED", payments=[{"method": "CREDIT", "amount": 1000}])
    sale(db, total=60)
    assert post(client).status_code == 400
    assert repo.add_payment("OLD", {"method": "CASH", "amount": 60})
    assert post(client).status_code == 200


@pytest.mark.parametrize("failure", ["customer", "orders", "settings", "missing_customer", "no_repository"])
def test_unverifiable_credit_never_fails_open(env, monkeypatch, failure):
    db, _, client = env
    sale(db, customer_id="walk-in" if failure == "settings" else "C1")
    def broken(*args, **kwargs):
        raise RuntimeError("offline")
    if failure == "customer":
        monkeypatch.setattr(Customers, "find_by_id", broken)
    elif failure == "orders":
        monkeypatch.setattr(credit_limit, "_exposures", broken)
    elif failure == "settings":
        for module in {settings, importlib.import_module("api.routers.settings")}:
            monkeypatch.setattr(module, "_get_settings_collection", broken)
    elif failure == "missing_customer":
        db.customers.delete_many({})
    else:
        monkeypatch.setattr(payments, "get_order_repository", lambda: None)
    assert post(client).status_code == 503
    assert db.orders.find_one({"order_id": "O1"})["payments"] == []


def test_simultaneous_credit_sales_at_two_shops_cannot_both_spend_capacity(env, monkeypatch):
    db, _, _ = env
    sale(db, "O1", 60)
    sale(db, "O2", 60, store_id="S2")
    barrier = Barrier(2)
    seen = local()
    original = credit_limit._exposures
    def simultaneous(*args):
        result = original(*args)
        if not getattr(seen, "waited", False):
            seen.waited = True
            barrier.wait(timeout=10)
        return result
    monkeypatch.setattr(credit_limit, "_exposures", simultaneous)
    def run(oid):
        try:
            asyncio.run(payments.add_payment(oid, PaymentCreate(method="CREDIT", amount=60), USER))
            return 200
        except HTTPException as exc:
            return exc.status_code
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(run, ["O1", "O2"]))
    assert sorted(results) == [200, 400]
    assert sum(len(d["payments"]) for d in db.orders.find()) == 1


def test_stale_reservation_is_fenced_before_capacity_can_be_reused(env):
    db, repo, client = env
    old = sale(db, "OLD", 80, credit_fence="abandoned")
    db.credit_reservations.insert_one({"_id": "customer:C1", "version": 1, "reservations": [
        {"order_id": "OLD", "amount": 80, "token": "abandoned", "created_at": 0},
    ]})
    sale(db, total=100)
    assert post(client).status_code == 200
    assert not repo.add_payment("OLD", {"method": "CREDIT", "amount": 80}, expected_order=old)
    assert db.orders.find_one({"order_id": "OLD"})["payments"] == []


def test_failed_fencing_keeps_reserved_capacity_and_refuses_new_credit(env, monkeypatch):
    db, _, client = env
    sale(db, "OLD", 80, credit_fence="abandoned")
    db.credit_reservations.insert_one({"_id": "customer:C1", "version": 1, "reservations": [
        {"order_id": "OLD", "amount": 80, "token": "abandoned", "created_at": 0},
    ]})
    sale(db, total=100)
    original = db.orders.update_one
    def failure(query, update, *args, **kwargs):
        if query.get("order_id") == "OLD" and "$unset" in update:
            raise RuntimeError("cannot fence the old writer")
        return original(query, update, *args, **kwargs)
    monkeypatch.setattr(db.orders, "update_one", failure)
    assert post(client).status_code == 503
    assert db.orders.find_one({"order_id": "O1"})["payments"] == []
    assert db.credit_reservations.find_one()["reservations"][0]["token"] == "abandoned"


def test_stale_lower_stored_balance_cannot_hide_the_exposure_to_be_written(env):
    db, _, client = env
    sale(db, total=150, balance_due=50)
    assert post(client).status_code == 400
    assert db.orders.find_one({"order_id": "O1"})["payments"] == []


@pytest.mark.parametrize("amount", ["NaN", "Infinity"])
def test_unverifiable_existing_cash_never_clears_the_credit_balance(env, amount):
    db, _, client = env
    sale(db, total=150, balance_due=60, payments=[{"method": "CASH", "amount": amount}])
    assert post(client).status_code == 503
    assert len(db.orders.find_one({"order_id": "O1"})["payments"]) == 1


def test_lost_write_reply_recovers_the_exact_payment(env, monkeypatch):
    db, _, client = env
    sale(db)
    original = db.orders.update_one
    def lost_reply(query, update, *args, **kwargs):
        result = original(query, update, *args, **kwargs)
        if "$push" in update:
            raise RuntimeError("reply lost after write")
        return result
    monkeypatch.setattr(db.orders, "update_one", lost_reply)
    first = post(client, headers={"Idempotency-Key": "same-sale"})
    assert first.status_code == 200
    replay = post(client, headers={"Idempotency-Key": "same-sale"})
    assert replay.status_code == 200
    assert replay.json()["payment_id"] == first.json()["payment_id"]
    assert len(db.orders.find_one({"order_id": "O1"})["payments"]) == 1


def test_price_changed_after_reserving_cannot_write_an_unapproved_balance(env, monkeypatch):
    db, repo, client = env
    sale(db)
    original = repo.add_payment
    def change(*args, **kwargs):
        db.orders.update_one({"order_id": "O1"}, {"$set": {"grand_total": 200}})
        return original(*args, **kwargs)
    monkeypatch.setattr(repo, "add_payment", change)
    assert post(client).status_code == 409
    assert db.orders.find_one({"order_id": "O1"})["payments"] == []


def test_simultaneous_cash_settlements_keep_the_complete_payment_ladder(env, monkeypatch):
    db, repo, _ = env
    sale(db, total=100)
    original = repo.find_by_id
    barrier = Barrier(2)
    seen = local()
    def read(oid):
        result = original(oid)
        if not getattr(seen, "waited", False):
            seen.waited = True
            barrier.wait(timeout=10)
        return result
    monkeypatch.setattr(repo, "find_by_id", read)
    with ThreadPoolExecutor(2) as pool:
        assert all(pool.map(lambda amount: repo.add_payment("O1", {"method": "CASH", "amount": amount}), [40, 60]))
    order = db.orders.find_one({"order_id": "O1"})
    assert (len(order["payments"]), order["amount_paid"], order["balance_due"], order["payment_status"]) == (2, 100, 0, "PAID")


def test_walkin_summary_uses_the_same_saved_default(env):
    _, _, client = env
    response = client.get("/customers/walk-in/credit-summary")
    assert response.status_code == 200
    assert response.json()["credit_limit"] == 100


def test_named_summary_refuses_missing_customer_repository_but_walkin_still_uses_default(env, monkeypatch):
    _, _, client = env
    monkeypatch.setattr(customers, "get_customer_repository", lambda: None)
    response = client.get("/customers/C1/credit-summary")
    assert response.status_code == 503
    assert "could not be verified" in response.json()["detail"]
    walkin = client.get("/customers/walk-in/credit-summary")
    assert walkin.status_code == 200
    assert walkin.json()["credit_limit"] == 100


@pytest.mark.parametrize("method", ["CREDIT", "CASH"])
def test_concurrent_idempotency_replay_returns_the_stored_payment_id(env, monkeypatch, method):
    db, repo, client = env
    sale(db, total=100)
    original = repo.find_by_id
    reads = 0
    def read(oid):
        nonlocal reads
        reads += 1
        if reads == 2:  # another request wins between replay lookup and main read
            db.orders.update_one({"order_id": oid}, {"$push": {"payments": {
                "payment_id": "winner", "method": method, "amount": 40,
                "idempotency_key": "repeat",
            }}, "$set": {"amount_paid": 40 if method == "CASH" else 0,
                          "balance_due": 60 if method == "CASH" else 100}})
        return original(oid)
    monkeypatch.setattr(repo, "find_by_id", read)
    response = client.post("/orders/O1/payments", json={"method": method, "amount": 40},
                           headers={"Idempotency-Key": "repeat"})
    assert response.status_code == 200, response.text
    assert response.json()["payment_id"] == "winner"
    assert len(db.orders.find_one({"order_id": "O1"})["payments"]) == 1


def test_credit_preview_shows_all_orders_and_pending_capacity(env, monkeypatch):
    db, repo, client = env
    import api.dependencies as deps
    monkeypatch.setattr(deps, "get_order_repository", lambda: repo)
    for n in range(501):
        sale(db, f"OLD-{n}", 0.1, payments=[{"method": "CREDIT", "amount": 0.1}])
    sale(db, "PENDING", 20, credit_fence="in-flight")
    db.credit_reservations.insert_one({"_id": "customer:C1", "version": 1, "reservations": [
        {"order_id": "PENDING", "amount": 20, "token": "in-flight", "created_at": datetime.now(timezone.utc).timestamp()},
    ]})
    response = client.get("/customers/C1/credit-summary")
    assert response.status_code == 200, response.text
    assert response.json()["ar_outstanding"] == 70.1
    assert response.json()["ar_available"] == 29.9
