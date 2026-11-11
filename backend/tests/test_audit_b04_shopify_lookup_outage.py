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


# ---------------------------------------------------------------------------
# D13(b): "is this refund already booked?" fails CLOSED. A refund is money: a
# read that FAILED is not "not booked yet", on any of the three doors that ask
# (the webhook, the hourly status sweep, the order-history import).
# ---------------------------------------------------------------------------

_REFUND_ID = "81"


def _refund_order(db):
    db.orders.insert_one({
        "order_id": "ORD-AUDIT-1",
        "shopify_order_id": _SHOPIFY_ORDER_ID,
        "order_number": "ONL-5550001",
        "channel": "ONLINE",
        "source": "shopify",
        "customer_id": "CUST-1",
        "store_id": "BV-ONLINE-01",
        "status": "CONFIRMED",
        "payment_status": "PAID",
        "grand_total": 1000.0,
        "amount_paid": 1000.0,
        "items": [{
            "item_id": "it-1", "product_id": "7001", "ims_product_id": "IMS-P-1",
            "shopify_line_item_id": 9001, "sku": "RB-1", "product_name": "RB",
            "quantity": 1, "gst_rate": 5.0, "taxable_value": 952.38, "tax_amount": 47.62,
        }],
    })


def _refund_payload():
    return {
        "id": int(_REFUND_ID),
        "order_id": int(_SHOPIFY_ORDER_ID),
        "restock": True,
        "refund_line_items": [{
            "id": 1, "quantity": 1, "line_item_id": 9001, "restock_type": "return",
            "subtotal": 952.38, "total_tax": 47.62,
            "line_item": {"id": 9001, "sku": "RB-1", "quantity": 1},
        }],
    }


def _booked_by_returns_doc(db):
    """Credited through a returns doc (an AUTO post, or a confirmed review
    whose row was since cleared)."""
    db.returns.insert_one({
        "return_id": "RET-1", "order_id": "ORD-AUDIT-1", "shopify_refund_id": _REFUND_ID,
        "status": "COMPLETED", "credit_note_issued": True, "net_refund": 1000.0,
        "credit_entry": {"entry_id": "CN-1", "delta": 1000.0},
    })
    db.credit_note_ledger.insert_one({
        "entry_id": "CN-1", "ref": "RET-1", "type": "ISSUED", "delta": 1000.0,
        "customer_id": "CUST-1", "order_id": "ORD-AUDIT-1",
    })


def _booked_by_history_import(db):
    """Credited by the order-history import: its ledger row is written first,
    and its returns stamp may never land."""
    db.credit_note_ledger.insert_one({
        "entry_id": "CN-H", "ref": f"SHOPIFY-HIST-REFUND-{_REFUND_ID}", "type": "ISSUED",
        "delta": 0.0, "customer_id": "CUST-1", "order_id": "ORD-AUDIT-1",
    })


def _rejected_by_the_accountant(db):
    db.shopify_refund_review.insert_one({
        "review_id": "REV-1", "shopify_refund_id": _REFUND_ID, "order_id": "ORD-AUDIT-1",
        "status": "REJECTED", "resolved": True,
    })


_MONEY_COLLECTIONS = ("returns", "credit_note_ledger", "shopify_refund_review", "tasks")


def _money_rows(db):
    """Every stored row a refund can write, without Mongo's _id."""
    return {
        name: sorted(
            repr(sorted((k, v) for k, v in d.items() if k != "_id"))
            for d in db[name].find({})
        )
        for name in _MONEY_COLLECTIONS
    }


_BOOKED = [
    pytest.param(_booked_by_returns_doc, "returns", id="returns-doc"),
    pytest.param(_booked_by_history_import, "credit_note_ledger", id="history-import-ledger-row"),
    pytest.param(_rejected_by_the_accountant, "shopify_refund_review", id="rejected-review-row"),
]


@pytest.mark.parametrize("seed,outage", _BOOKED)
def test_refund_already_processed_raises_when_its_read_fails(db, seed, outage):
    from api.services.shopify_refund import _refund_already_processed

    seed(db)
    assert _refund_already_processed(db, _REFUND_ID) is True
    with _lookup_outage(outage):
        with pytest.raises(TimeoutError):
            _refund_already_processed(db, _REFUND_ID)


def test_refund_already_processed_raises_when_the_database_is_disconnected():
    from api.services.shopify_refund import _refund_already_processed

    class Disconnected:
        def get_collection(self, name):
            return None

    with pytest.raises(RuntimeError):
        _refund_already_processed(Disconnected(), _REFUND_ID)


def test_refund_already_processed_still_answers_when_the_reads_work(db):
    from api.services.shopify_refund import _refund_already_processed

    assert _refund_already_processed(db, _REFUND_ID) is False
    assert _refund_already_processed(None, _REFUND_ID) is False
    # An UNMATCHED row (the refund arrived before its order) stays reprocessable.
    db.shopify_refund_review.insert_one(
        {"shopify_refund_id": _REFUND_ID, "status": "UNMATCHED"})
    assert _refund_already_processed(db, _REFUND_ID) is False
    db.shopify_refund_review.update_one(
        {"shopify_refund_id": _REFUND_ID}, {"$set": {"status": "PENDING"}})
    assert _refund_already_processed(db, _REFUND_ID) is True


@pytest.mark.parametrize("seed,outage", _BOOKED)
def test_a_booked_refund_redelivered_during_a_read_outage_books_nothing_twice(
    db, monkeypatch, seed, outage
):
    """The webhook door, by its STORED rows. Before: the failed read answered
    "not booked yet", the refund was worked again, a second PENDING review row
    was queued for money already settled and the inbox row was acknowledged."""
    monkeypatch.delenv("SHOPIFY_REFUND_AUTO", raising=False)
    _refund_order(db)
    seed(db)
    before = _money_rows(db)
    order_before = db.orders.find_one({"order_id": "ORD-AUDIT-1"})
    _inbox_row(db, "refund-1", "refunds/create", _refund_payload())

    with _lookup_outage(outage):
        row = _drain(db, "refund-1")

    assert row["processed"] is False and row["status"] == "failed", row
    assert "exception:TimeoutError" in row["handler_error"]
    assert _money_rows(db) == before
    assert db.orders.find_one({"order_id": "ORD-AUDIT-1"}) == order_before

    # The outage ends: the retry finds the refund booked and changes nothing.
    row = _drain(db, "refund-1")
    assert row["processed"] is True and row["status"] == "succeeded", row
    assert _money_rows(db) == before
    assert db.orders.find_one({"order_id": "ORD-AUDIT-1"}) == order_before


@pytest.mark.parametrize("seed,outage", _BOOKED)
def test_the_auto_post_books_nothing_twice_after_a_read_failure(
    db, monkeypatch, seed, outage
):
    """Same, with the automatic credit-note post switched on in the test (it
    is off in production): no second credit note, no second return."""
    monkeypatch.setenv("SHOPIFY_REFUND_AUTO", "1")
    _refund_order(db)
    seed(db)
    before = _money_rows(db)

    with _lookup_outage(outage):
        result = handle_shopify_refund(db, _refund_payload(), topic="refunds/create")
    assert result == {"status": "skipped", "reason": "exception:TimeoutError"}
    with pytest.raises(RuntimeError):
        _raise_if_failed(result)
    assert _money_rows(db) == before

    assert handle_shopify_refund(db, _refund_payload())["status"] == "duplicate"
    assert _money_rows(db) == before


@pytest.mark.parametrize("seed,outage", _BOOKED)
def test_the_hourly_sweep_does_not_feed_a_refund_it_could_not_check(db, seed, outage):
    from agents import nexus_providers

    _refund_order(db)
    seed(db)
    before = _money_rows(db)
    raw = {"id": int(_SHOPIFY_ORDER_ID), "updated_at": "2026-11-10T10:00:00Z",
           "refunds": [_refund_payload()]}
    existing = {"order_id": "ORD-AUDIT-1", "historical": True}

    with _lookup_outage(outage):
        buckets, reason = asyncio.run(nexus_providers._sweep_booked_order(
            db, raw, raw, existing, _SHOPIFY_ORDER_ID, True))

    assert buckets == ["status_failed"] and "TimeoutError" in reason
    assert _money_rows(db) == before
    # Next hour, reads working: nothing to do.
    buckets, reason = asyncio.run(nexus_providers._sweep_booked_order(
        db, raw, raw, existing, _SHOPIFY_ORDER_ID, True))
    assert buckets == [] and reason is None
    assert _money_rows(db) == before


def test_the_history_import_does_not_rebook_a_refund_it_could_not_check(db):
    """The order-history import asks the same rule before it writes a credit
    note. A failed read is a FAILED booking (healed on the next run), never a
    second credit note."""
    from api.services.shopify_ingest import _book_historical_refund_credit_notes

    _refund_order(db)
    order = db.orders.find_one({"order_id": "ORD-AUDIT-1"})
    payload = {"id": int(_SHOPIFY_ORDER_ID), "financial_status": "partially_refunded",
               "refunds": [dict(_refund_payload(), created_at="2024-02-01T10:00:00Z")]}

    first = _book_historical_refund_credit_notes(db, payload, order)
    assert first["credit_notes"] == 1
    booked = db.credit_note_ledger.count_documents({})
    assert booked == 1
    # The returns stamp did not land (it is fail-soft): only the ledger row
    # says this refund is booked.
    db.returns.delete_many({})

    with _lookup_outage("credit_note_ledger"):
        during = _book_historical_refund_credit_notes(db, payload, order)
    assert during["credit_notes"] == 0 and during["cn_failed"] == 1
    assert db.credit_note_ledger.count_documents({}) == booked

    after = _book_historical_refund_credit_notes(db, payload, order)
    assert after["credit_notes"] == 0 and after["duplicate_refunds"] == 1
    assert db.credit_note_ledger.count_documents({}) == booked
