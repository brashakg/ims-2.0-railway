"""
RUN-01: a valid online (Shopify) phone buyer must become a customer when the
door passes a RAW pymongo Database (online_store_orders._get_db returns
``conn.db``; its Re-map door hands that to the mapper).

A raw pymongo Database answers ANY attribute with a Collection, and a pymongo
Collection's truth test raises NotImplementedError. customer_service._get_repo
truth-tested ``getattr(db, "is_connected")`` -> the online find-or-create threw
internally and the buyer was silently booked as a GUEST order.

Fakes that carry a Boolean ``is_connected`` are blind to this, so the handle
here is mongomock with pymongo's OWN Collection.__bool__ grafted on (plus one
real, offline pymongo Database for the repo-resolution step itself).
"""
from __future__ import annotations

import copy
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test_run01")
os.environ["GST_PRICING_MODE"] = "inclusive"
os.environ["ONLINE_STORE_ID"] = "BV-ONLINE-01"

mongomock = pytest.importorskip("mongomock")
import pymongo  # noqa: E402
from pymongo.collection import Collection as _PyMongoCollection  # noqa: E402

import api.dependencies as deps  # noqa: E402
from api.services import customer_service as cs  # noqa: E402
from api.services import online_order_mapper as mapper  # noqa: E402
from database.repositories.customer_repository import CustomerRepository  # noqa: E402
from database.repositories.order_repository import OrderRepository  # noqa: E402

STORE = "BV-ONLINE-01"
PHONE = "9000000003"


@pytest.fixture
def raw_db(monkeypatch):
    """A raw-Database-shaped handle: attribute access yields a Collection and the
    Collection refuses bool() exactly like pymongo 4."""
    monkeypatch.setattr(
        mongomock.collection.Collection, "__bool__", _PyMongoCollection.__bool__, raising=False
    )
    db = mongomock.MongoClient()["ims_run01"]
    with pytest.raises(NotImplementedError):
        bool(getattr(db, "is_connected", False))  # the defect's trigger is live

    class _StoreRepo:
        def find_by_id(self, _sid):
            return {"gstin": "", "state_code": "20"}

        def find_active(self, filter=None):
            return [{"store_id": STORE, "state_code": "20"}]

    monkeypatch.setattr(deps, "get_order_repository", lambda: OrderRepository(db["orders"]))
    monkeypatch.setattr(deps, "get_product_repository", lambda: None)
    monkeypatch.setattr(deps, "get_store_repository", lambda: _StoreRepo())
    monkeypatch.setattr(
        deps, "get_customer_repository", lambda: CustomerRepository(db["customers"])
    )
    return db


def _order(order_id, *, phone=PHONE, email="buyer3@example.com"):
    customer = {"id": 880000 + order_id, "first_name": "Asha", "last_name": "Rao"}
    if phone:
        customer["phone"] = phone
    if email:
        customer["email"] = email
    payload = {
        "id": order_id,
        "name": f"#{order_id}",
        "financial_status": "paid",
        "fulfillment_status": None,
        "customer": customer,
        "shipping_address": {
            "province": "Jharkhand", "province_code": "JH",
            "address1": "12 Main Road", "city": "Bokaro", "zip": "827001",
        },
        "line_items": [{
            "id": 9001, "product_id": 7001, "variant_id": 999001,
            "title": "Ray-Ban Frame RB1234", "product_type": "Frames",
            "sku": "RB-1234", "quantity": 1, "price": "999.00",
            "total_discount": "0.00",
        }],
    }
    if phone:
        payload["phone"] = phone
    if email:
        payload["email"] = email
    return payload


def _map(db, oid, **kw):
    return mapper.map_shopify_order(_order(oid, **kw), db, topic="orders/create")


def test_real_pymongo_database_resolves_a_customer_repo():
    """The repo step itself, on a REAL (offline) pymongo Database."""
    client = pymongo.MongoClient("mongodb://127.0.0.1:1", connect=False)
    try:
        repo = cs._get_repo(client["ims_run01"])
        assert isinstance(repo, CustomerRepository)
        assert repo.collection.name == "customers"
    finally:
        client.close()


def test_new_phone_buyer_becomes_one_full_customer_not_a_guest(raw_db):
    res = _map(raw_db, 50001)
    assert res["status"] == "created"
    assert res["customer_id"], "a valid phone buyer must link to a customer"
    assert not res.get("customer_link_failed")
    custs = list(raw_db["customers"].find({"mobile": PHONE}))
    assert len(custs) == 1
    assert custs[0]["customer_id"] == res["customer_id"]
    assert custs[0].get("contact_tier", "FULL") != "MARKETING"
    order = raw_db["orders"].find_one({"shopify_order_id": "50001"})
    assert order["customer_id"] == res["customer_id"]
    assert order["is_guest_order"] is False


def test_repeat_phone_and_replay_resolve_the_same_customer(raw_db):
    first = _map(raw_db, 50002)
    again = _map(raw_db, 50003)  # same buyer, new order
    assert again["customer_id"] == first["customer_id"]
    replay = _map(raw_db, 50002)  # same event again
    assert replay["status"] in ("duplicate", "replayed")
    assert replay["customer_id"] == first["customer_id"]
    assert raw_db["customers"].count_documents({"mobile": PHONE}) == 1
    assert raw_db["orders"].count_documents({"shopify_order_id": "50002"}) == 1


def test_existing_offline_customer_phone_links_to_that_record(raw_db):
    cid, created = cs.ensure_customer(
        raw_db, mobile=PHONE, name="Asha (shop)", store_id="BV-BOK-01", source="POS"
    )
    assert created is True
    res = _map(raw_db, 50004)
    assert res["customer_id"] == cid
    assert raw_db["customers"].count_documents({}) == 1


def test_family_member_phone_follows_the_household_rule(raw_db):
    raw_db["customers"].insert_one({
        "customer_id": "cust-holder", "name": "Meena", "mobile": "9000000001",
        "phone": "9000000001", "is_active": True,
        "patients": [{"patient_id": "p-d", "name": "Riya", "mobile": PHONE}],
    })
    res = _map(raw_db, 50005)
    assert res["customer_id"] == "cust-holder"
    assert raw_db["customers"].count_documents({}) == 1


def test_email_only_buyer_stays_a_marketing_contact(raw_db):
    res = _map(raw_db, 50006, phone=None, email="only@example.com")
    cust = raw_db["customers"].find_one({"customer_id": res["customer_id"]})
    assert cust["contact_tier"] == "MARKETING"
    assert "mobile" not in cust
    assert not res.get("customer_link_failed")


def test_unresolvable_phone_buyer_is_flagged_and_replay_repairs(raw_db, monkeypatch):
    """A failure is visible (flagged), never a silent guest; once the door works
    again a replay links the SAME order without a second order or customer."""
    broken = {"on": True}
    real_get_repo = cs._get_repo
    monkeypatch.setattr(
        cs, "_get_repo", lambda db=None: None if broken["on"] else real_get_repo(db)
    )
    res = _map(raw_db, 50007)
    assert res["customer_id"] is None
    assert res["customer_link_failed"] is True
    order = raw_db["orders"].find_one({"shopify_order_id": "50007"})
    assert order["is_guest_order"] is True
    assert order["customer_link_failed"] is True

    broken["on"] = False
    replay = _map(raw_db, 50007)
    assert replay["customer_id"]
    order = raw_db["orders"].find_one({"shopify_order_id": "50007"})
    assert order["customer_id"] == replay["customer_id"]
    assert order["is_guest_order"] is False
    assert order["customer_link_failed"] is False
    assert raw_db["orders"].count_documents({"shopify_order_id": "50007"}) == 1
    assert raw_db["customers"].count_documents({"mobile": PHONE}) == 1


def test_genuine_guest_is_not_flagged_and_replay_leaves_it_alone(raw_db):
    res = _map(raw_db, 50008, phone=None, email=None)
    assert res["customer_id"] is None
    assert not res.get("customer_link_failed")
    order = raw_db["orders"].find_one({"shopify_order_id": "50008"})
    assert order["is_guest_order"] is True
    assert order["customer_link_failed"] is False


def test_connection_wrapper_handle_still_resolves(raw_db):
    """Same-shop doors (walkouts) pass the connection WRAPPER: unchanged."""

    class _Wrapper:
        is_connected = True

        @property
        def db(self):
            return raw_db

    cid, created = cs.ensure_customer(
        _Wrapper(), mobile="9000000009", name="Walk-in", store_id="BV-BOK-01",
        source="WALKOUT",
    )
    assert created is True
    assert raw_db["customers"].find_one({"customer_id": cid})["mobile"] == "9000000009"


def test_online_orders_list_carries_the_link_failed_flag():
    from api.routers import online_store_orders as oso

    assert oso._LIST_PROJECTION.get("customer_link_failed") == 1
