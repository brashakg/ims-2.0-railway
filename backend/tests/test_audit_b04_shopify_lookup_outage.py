"""
Audit L1-F29: a Shopify notification whose order lookup FAILED is not "the
order does not exist".

Reproduction (audit lane1/shopify-delete-outage-probe.py): with the orders
read raising, handle_shopify_order_delete returned {"status":
"order_not_found"}; NEXUS does not treat that verdict as a failure, so the
webhook_inbox row was written processed=True / succeeded and the deletion was
never applied.

These tests run the real NEXUS inbox drain (NexusAgent._handle_inbox_webhook)
against mongomock, with the outage injected only on the lookup, and assert the
STORED inbox row and the STORED order - not just the handler's return value.
The same shared lookup serves fulfilments, refunds and customer deletions, so
those doors are pinned too.

Run: JWT_SECRET_KEY=test python -m pytest \
       backend/tests/test_audit_b04_shopify_lookup_outage.py -q
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

import mongomock  # noqa: E402
import pytest  # noqa: E402

from agents.implementations.nexus import NexusAgent, _raise_if_failed  # noqa: E402
from api.services.shopify_customer_delete import (  # noqa: E402
    handle_shopify_customer_delete,
)
from api.services.shopify_fulfillment import reconcile_fulfillment  # noqa: E402
from api.services.shopify_order_delete import handle_shopify_order_delete  # noqa: E402
from api.services.shopify_refund import handle_shopify_refund  # noqa: E402

_SHOPIFY_ORDER_ID = "5550001"


@pytest.fixture
def db():
    return mongomock.MongoClient()["audit_b04_lookup"]


def _order(db, **extra):
    db.orders.insert_one({
        "order_id": "ORD-AUDIT-1",
        "shopify_order_id": _SHOPIFY_ORDER_ID,
        "channel": "ONLINE",
        "status": "CONFIRMED",
        **extra,
    })


def _inbox_row(db, webhook_id, topic, payload):
    db.webhook_inbox.insert_one({
        "webhook_id": webhook_id,
        "vendor": "shopify",
        "received_at": datetime.now(timezone.utc) - timedelta(minutes=2),
        "processed": False,
        "processed_at": None,
        "status": "received",
        "attempts": 0,
        "payload": payload,
        "headers": {"x-shopify-topic": topic},
    })


def _drain(db, webhook_id):
    asyncio.run(NexusAgent(db=db)._handle_inbox_webhook(webhook_id, "shopify"))
    return db.webhook_inbox.find_one({"webhook_id": webhook_id})


def _lookup_outage(*collections):
    """The database is up for everything EXCEPT a find_one on `collections`
    (a storage outage / timeout on the lookup the handler depends on)."""
    real = mongomock.collection.Collection.find_one

    def flaky(self, *args, **kwargs):
        if self.name in collections:
            raise TimeoutError("synthetic storage outage")
        return real(self, *args, **kwargs)

    return patch.object(mongomock.collection.Collection, "find_one", flaky)


# ---------------------------------------------------------------------------
# The audit probe, inverted
# ---------------------------------------------------------------------------


def test_audit_probe_lookup_outage_is_a_failure_not_order_not_found():
    class BrokenOrders:
        def find_one(self, *a, **kw):
            raise RuntimeError("AUDIT temporary order lookup outage")

    class DB:
        def get_collection(self, name):
            assert name == "orders"
            return BrokenOrders()

    result = handle_shopify_order_delete(
        DB(), {"id": "AUDIT-SHOPIFY-ORDER"}, topic="orders/delete"
    )
    assert result["status"] != "order_not_found"
    with pytest.raises(RuntimeError):
        _raise_if_failed(result)


def test_disconnected_database_is_a_failure_not_order_not_found():
    """In production a dropped connection makes get_collection return None
    (database/connection.py). That is an outage too, not an unknown order."""

    class Disconnected:
        def get_collection(self, name):
            return None

    result = handle_shopify_order_delete(
        Disconnected(), {"id": _SHOPIFY_ORDER_ID}, topic="orders/delete"
    )
    assert result["status"] != "order_not_found"
    with pytest.raises(RuntimeError):
        _raise_if_failed(result)


# ---------------------------------------------------------------------------
# The inbox row and the order, as stored
# ---------------------------------------------------------------------------


def test_delete_during_a_lookup_outage_stays_retryable_then_applies_exactly_once(db):
    _order(db)
    _inbox_row(db, "del-1", "orders/delete", {"id": int(_SHOPIFY_ORDER_ID)})

    with _lookup_outage("orders"):
        row = _drain(db, "del-1")

    # The notification is NOT acknowledged as handled...
    assert row["processed"] is False
    assert row["status"] == "failed"
    assert row["attempts"] == 1
    assert "exception:TimeoutError" in row["handler_error"]
    # ...and the order is untouched.
    order = db.orders.find_one({"order_id": "ORD-AUDIT-1"})
    assert order["status"] == "CONFIRMED"
    assert "shopify_deleted_at" not in order

    # Recovery: the retry sweep / a redelivery drains the same row again.
    row = _drain(db, "del-1")
    assert row["processed"] is True and row["status"] == "succeeded"
    assert row["attempts"] == 2
    assert "handler_error" not in row
    order = db.orders.find_one({"order_id": "ORD-AUDIT-1"})
    assert order["status"] == "VOID"
    assert order["status_before_void"] == "CONFIRMED"
    stamped = order["shopify_deleted_at"]

    # A second, genuine orders/delete for the same order changes nothing.
    _inbox_row(db, "del-2", "orders/delete", {"id": int(_SHOPIFY_ORDER_ID)})
    row = _drain(db, "del-2")
    assert row["processed"] is True and row["status"] == "succeeded"
    again = db.orders.find_one({"order_id": "ORD-AUDIT-1"})
    assert again["status"] == "VOID"
    assert again["shopify_deleted_at"] == stamped
    assert db.orders.count_documents({}) == 1


def test_five_outages_park_the_row_as_dead_letter_never_as_done(db):
    _order(db)
    _inbox_row(db, "del-dead", "orders/delete", {"id": int(_SHOPIFY_ORDER_ID)})
    with _lookup_outage("orders"):
        for _ in range(NexusAgent.WEBHOOK_MAX_ATTEMPTS):
            row = _drain(db, "del-dead")
    assert row["status"] == "dead" and row["processed"] is False
    assert db.orders.find_one({"order_id": "ORD-AUDIT-1"})["status"] == "CONFIRMED"


def test_a_genuinely_unknown_order_is_still_a_safe_no_op(db):
    _inbox_row(db, "del-unknown", "orders/delete", {"id": 999})
    row = _drain(db, "del-unknown")
    assert row["processed"] is True and row["status"] == "succeeded"
    assert db.orders.count_documents({}) == 0
    assert handle_shopify_order_delete(db, {"id": 999})["status"] == "order_not_found"


# ---------------------------------------------------------------------------
# The same lookup behind the other Shopify doors
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "topic,payload,collection",
    [
        ("fulfillments/create",
         {"id": 71, "order_id": int(_SHOPIFY_ORDER_ID), "status": "success"}, "orders"),
        ("refunds/create",
         {"id": 81, "order_id": int(_SHOPIFY_ORDER_ID), "refund_line_items": []},
         "orders"),
        ("customers/delete", {"id": 91}, "customers"),
    ],
)
def test_sibling_doors_keep_the_row_retryable_on_a_lookup_outage(
    db, topic, payload, collection
):
    _order(db)
    db.customers.insert_one({"customer_id": "C-1", "shopify_customer_id": "91"})
    _inbox_row(db, "sib-1", topic, payload)

    with _lookup_outage(collection):
        row = _drain(db, "sib-1")

    assert row["processed"] is False and row["status"] == "failed", row
    assert "exception:TimeoutError" in row["handler_error"]
    # Nothing was written anywhere on the strength of a failed read: no
    # "unmatched refund" review row, no order or customer change.
    order = db.orders.find_one({"order_id": "ORD-AUDIT-1"})
    assert order["status"] == "CONFIRMED" and "shopify_deleted_at" not in order
    assert "shopify_erasure_requested" not in db.customers.find_one({"customer_id": "C-1"})
    others = set(db.list_collection_names()) - {"orders", "customers", "webhook_inbox"}
    assert {n: db[n].count_documents({}) for n in others if db[n].count_documents({})} == {}


@pytest.mark.parametrize(
    "handler,payload,verdict",
    [
        (reconcile_fulfillment, {"id": 71, "order_id": 999}, "order_not_found"),
        (handle_shopify_refund, {"id": 81, "order_id": 999}, "order_not_found"),
        (handle_shopify_customer_delete, {"id": 999}, "customer_not_found"),
    ],
)
def test_sibling_doors_still_treat_a_genuine_miss_as_not_found(
    db, handler, payload, verdict
):
    result = handler(db, payload)
    assert result["status"] == verdict
    _raise_if_failed(result)  # a business outcome, not a failure
