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


@pytest.fixture(autouse=True)
def _handles_refuse_truth_tests_like_pymongo(monkeypatch):
    """A real pymongo Database / Collection raises on `if db:` / `if coll:`.
    mongomock's are quietly truthy, which hid that class of defect in other
    PRs - so the doubles here carry pymongo's own __bool__."""
    from pymongo.collection import Collection
    from pymongo.database import Database

    monkeypatch.setattr(
        mongomock.collection.Collection, "__bool__", Collection.__bool__, raising=False)
    monkeypatch.setattr(
        mongomock.database.Database, "__bool__", Database.__bool__, raising=False)


@pytest.fixture
def db():
    handle = mongomock.MongoClient()["audit_b04_lookup"]
    with pytest.raises(NotImplementedError):
        bool(handle)
    with pytest.raises(NotImplementedError):
        bool(handle.orders)
    return handle


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


_REAL_FIND_ONE = mongomock.collection.Collection.find_one


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


def test_a_claim_whose_finished_read_failed_is_not_acknowledged_as_done(db, monkeypatch):
    """The second read of the same rule: "was that claim FINISHED?". A run
    claimed the refund (returns doc PENDING, no credit note) and died. On the
    redelivery the first read works (a returns doc exists) and the second one
    fails. Before: the failed read answered "finished", the webhook was
    acknowledged as a duplicate and nothing ever finished the refund."""
    monkeypatch.delenv("SHOPIFY_REFUND_AUTO", raising=False)
    _refund_order(db)
    db.returns.insert_one({
        "return_id": "RET-CLAIM", "order_id": "ORD-AUDIT-1", "shopify_refund_id": _REFUND_ID,
        "status": "PENDING", "credit_note_issued": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    before = _money_rows(db)
    _inbox_row(db, "refund-claim", "refunds/create", _refund_payload())

    real = mongomock.collection.Collection.find_one
    reads = []

    def second_read_fails(self, *args, **kwargs):
        if self.name == "returns":
            reads.append(1)
            if len(reads) == 2:
                raise TimeoutError("synthetic storage outage")
        return real(self, *args, **kwargs)

    with patch.object(mongomock.collection.Collection, "find_one", second_read_fails):
        row = _drain(db, "refund-claim")

    assert len(reads) >= 2
    assert row["processed"] is False and row["status"] == "failed", row
    # Main's D118 answer to an unreadable claim (merged): a retried error.
    from api.services.shopify_refund import CLAIM_UNREADABLE_TEXT

    assert CLAIM_UNREADABLE_TEXT in row["handler_error"]
    assert _money_rows(db) == before

    # Reads working again: the fresh claim is still reported unfinished (the
    # row stays retryable), exactly as it is without any outage.
    row = _drain(db, "refund-claim")
    assert row["processed"] is False and row["status"] == "failed", row
    assert "not finalized" in row["handler_error"]
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


# ---------------------------------------------------------------------------
# D55: the RETRY CLAIM of a stale, never-credited refund. A claim write that
# failed, or landed with its answer lost, is unfinished work -- never a
# duplicate acknowledged as done while no credit note exists.
# ---------------------------------------------------------------------------


def _dead_first_run(db):
    """A run claimed the refund 30 minutes ago and died before posting."""
    db.returns.insert_one({
        "return_id": "RET-DEAD", "order_id": "ORD-AUDIT-1", "shopify_refund_id": _REFUND_ID,
        "customer_id": "CUST-1", "status": "PENDING", "credit_note_issued": False,
        "created_at": (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat(),
    })


def _claim_write(mode):
    """The returns collection's find_one_and_update (the retry claim) fails:
    'fails' writes nothing, 'ack_lost' writes and then loses the answer."""
    real = mongomock.collection.Collection.find_one_and_update
    hits = []

    def flaky(self, *args, **kwargs):
        if self.name != "returns" or hits:
            return real(self, *args, **kwargs)
        if mode == "fails":
            hits.append(1)
            raise TimeoutError("synthetic retry-claim write timeout")
        out = real(self, *args, **kwargs)
        if out is not None:
            hits.append(1)
            raise TimeoutError("synthetic lost claim acknowledgement")
        return out

    return patch.object(mongomock.collection.Collection, "find_one_and_update", flaky), hits


@pytest.mark.parametrize("mode", ["fails", "ack_lost"])
def test_a_failed_retry_claim_is_a_retryable_failure_not_a_duplicate(db, monkeypatch, mode):
    """Codex 1412-round1 P2, both shapes of a failed claim write. Before: the
    handler answered "duplicate", NEXUS stored succeeded/processed and the
    refund was never credited by that event again."""
    monkeypatch.setenv("SHOPIFY_REFUND_AUTO", "1")
    _refund_order(db)
    _dead_first_run(db)
    _inbox_row(db, "refund-claim-" + mode, "refunds/create", _refund_payload())
    ledger_before = db.credit_note_ledger.count_documents({})

    fault, hits = _claim_write(mode)
    with fault:
        row = _drain(db, "refund-claim-" + mode)
    assert hits, "the retry claim was not exercised"
    assert row["processed"] is False and row["status"] == "failed", row
    # A claim write that raised is main's D118 retried error (merged).
    from api.services.shopify_refund import CLAIM_UNREADABLE_TEXT

    assert CLAIM_UNREADABLE_TEXT in row["handler_error"]
    assert db.credit_note_ledger.count_documents({}) == ledger_before
    ret = db.returns.find_one({"shopify_refund_id": _REFUND_ID})
    assert ret["credit_note_issued"] is False

    if mode == "ack_lost":
        # The claim it wrote is fresh: another drain must still not post or
        # acknowledge it (single winner) until the claim goes stale.
        row = _drain(db, "refund-claim-" + mode)
        assert row["processed"] is False and row["status"] == "failed", row
        assert "claim held or unverified" in row["handler_error"]  # D55: never "duplicate"
        assert db.credit_note_ledger.count_documents({}) == ledger_before
        db.returns.update_one({"shopify_refund_id": _REFUND_ID}, {"$set": {
            "reprocessing_at": (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()}})

    # Storage recovered: the SAME event re-drains, wins the claim and posts
    # (this in-memory double has no credit ledger, so the post lands on the
    # accountant's queue as CREDIT_FAILED; the native probe shows the credit).
    row = _drain(db, "refund-claim-" + mode)
    ret = db.returns.find_one({"shopify_refund_id": _REFUND_ID})
    assert ret["status"] != "PENDING" and ret.get("reprocessing_at") is None, ret
    assert row["processed"] is True and row["status"] == "succeeded", row
    assert ret["credit_note_issued"] is True or db.shopify_refund_review.find_one(
        {"shopify_refund_id": _REFUND_ID, "status": "CREDIT_FAILED"}), ret


def test_a_claim_check_that_cannot_read_is_a_failure_not_a_duplicate(db, monkeypatch):
    """No claim was won and the "is the credit note there?" read fails: that
    is not a duplicate either."""
    monkeypatch.setenv("SHOPIFY_REFUND_AUTO", "1")
    _refund_order(db)
    _dead_first_run(db)
    db.returns.update_one({"shopify_refund_id": _REFUND_ID}, {"$set": {
        "reprocessing_at": datetime.now(timezone.utc).isoformat()}})
    real = mongomock.collection.Collection.find_one
    calls = []

    def last_read_fails(self, *args, **kwargs):
        if self.name == "returns" and args and args[0] == {"shopify_refund_id": _REFUND_ID}:
            calls.append(1)
            if len(calls) > 2:  # the guard's two reads work; the claim check does not
                raise TimeoutError("synthetic read outage")
        return real(self, *args, **kwargs)

    with patch.object(mongomock.collection.Collection, "find_one", last_read_fails):
        result = handle_shopify_refund(db, _refund_payload(), topic="refunds/create")
    assert result["status"] == "error", result


def test_a_really_credited_refund_is_still_a_duplicate(db, monkeypatch):
    monkeypatch.setenv("SHOPIFY_REFUND_AUTO", "1")
    _refund_order(db)
    db.returns.insert_one({
        "return_id": "RET-DONE", "order_id": "ORD-AUDIT-1", "shopify_refund_id": _REFUND_ID,
        "status": "COMPLETED", "credit_note_issued": True,
        "created_at": (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat(),
    })
    from api.services import shopify_refund as S

    assert S._post_locked(
        db, refund_id=_REFUND_ID, order=db.orders.find_one({"order_id": "ORD-AUDIT-1"}),
        return_lines=[], credit_note={"gross_refund": 1000.0}, restock_store=None,
        settled_externally=False, restock_unverified=False,
        allowance=(None, "", None, False))["status"] == "duplicate"


# ---------------------------------------------------------------------------
# D13(a): a webhook parked dead is retried by nothing - an admin must see it.
# ONE open task per provider on the Tasks board; later dead webhooks join it.
# ---------------------------------------------------------------------------

_DEAD_REF = "webhook_dead_letter:shopify"


def _park_dead(db, webhook_id, topic="orders/delete", payload=None):
    _inbox_row(db, webhook_id, topic, payload or {"id": int(_SHOPIFY_ORDER_ID)})
    with _lookup_outage("orders"):
        for _ in range(NexusAgent.WEBHOOK_MAX_ATTEMPTS):
            row = _drain(db, webhook_id)
    assert row["status"] == "dead" and row["processed"] is False, row
    return row


@pytest.fixture
def an_admin(monkeypatch):
    """The person the task goes to: task_triggers._person_holding asks the
    user repository for an active ADMIN (any shop)."""
    import api.dependencies as deps

    class Users:
        def find_by_role(self, role, store_id=None):
            return [{"user_id": "U-ADMIN-1"}] if role == "ADMIN" else []

    monkeypatch.setattr(deps, "get_user_repository", lambda: Users())


def test_a_dead_webhook_raises_one_task_for_an_admin(db, an_admin):
    _order(db)
    _inbox_row(db, "dead-1", "orders/delete", {"id": int(_SHOPIFY_ORDER_ID)})
    with _lookup_outage("orders"):
        for _ in range(NexusAgent.WEBHOOK_MAX_ATTEMPTS - 1):
            row = _drain(db, "dead-1")
        # Still being retried: nobody is bothered yet.
        assert row["status"] == "failed"
        assert db.tasks.count_documents({}) == 0
        row = _drain(db, "dead-1")
    assert row["status"] == "dead"

    tasks = list(db.tasks.find({}))
    assert len(tasks) == 1, tasks
    task = tasks[0]
    assert task["source"] == "SYSTEM" and task["source_ref"] == _DEAD_REF
    assert task["status"] == "OPEN" and task["priority"] == "P2"
    assert task["assigned_to"] == "U-ADMIN-1" and task["store_id"] is None
    assert task["title"] == (
        f"Shopify notification not processed after {NexusAgent.WEBHOOK_MAX_ATTEMPTS} tries"
    )
    for said in ("orders/delete", "dead-1", "TimeoutError", "NOT been applied"):
        assert said in task["description"], said
    assert task["link"] == "/online-store/shopify"
    assert task["payload"] == {
        "vendor": "shopify", "webhook_id": "dead-1", "topic": "orders/delete"}

    # A dead row is never drained again, so it never raises a second task.
    _drain(db, "dead-1")
    assert db.tasks.count_documents({}) == 1


def test_later_dead_webhooks_join_the_open_task_and_a_closed_task_is_not_reused(db, an_admin):
    _order(db)
    _park_dead(db, "dead-1")
    _park_dead(db, "dead-2", "refunds/create",
               {"id": 82, "order_id": int(_SHOPIFY_ORDER_ID), "refund_line_items": []})

    tasks = list(db.tasks.find({}))
    assert len(tasks) == 1, [t["title"] for t in tasks]
    text = tasks[0]["description"]
    assert "orders/delete, id dead-1" in text and "refunds/create, id dead-2" in text
    assert tasks[0]["status"] == "OPEN"

    # The admin deals with them and closes the task. The next dead webhook is
    # a new item, naming only itself.
    db.tasks.update_one({"task_id": tasks[0]["task_id"]}, {"$set": {"status": "COMPLETED"}})
    _park_dead(db, "dead-3")
    open_tasks = list(db.tasks.find({"status": "OPEN"}))
    assert db.tasks.count_documents({}) == 2 and len(open_tasks) == 1
    assert "dead-3" in open_tasks[0]["description"]
    assert "dead-1" not in open_tasks[0]["description"]


def test_a_webhook_that_recovers_or_is_skipped_raises_no_task(db, an_admin):
    _order(db)
    _inbox_row(db, "ok-1", "orders/delete", {"id": int(_SHOPIFY_ORDER_ID)})
    with _lookup_outage("orders"):
        assert _drain(db, "ok-1")["status"] == "failed"
    assert _drain(db, "ok-1")["status"] == "succeeded"

    # An explicit terminal skip (no handler for the vendor) is not a dead letter.
    db.webhook_inbox.insert_one({
        "webhook_id": "skip-1", "vendor": "nobody", "processed": False,
        "status": "received", "attempts": NexusAgent.WEBHOOK_MAX_ATTEMPTS,
        "received_at": datetime.now(timezone.utc) - timedelta(minutes=2), "payload": {},
    })
    asyncio.run(NexusAgent(db=db)._handle_inbox_webhook("skip-1", "nobody"))
    assert db.webhook_inbox.find_one({"webhook_id": "skip-1"})["status"] == "skipped"
    assert db.tasks.count_documents({}) == 0


def test_a_task_that_cannot_be_written_never_stops_the_drain(db, monkeypatch):
    """Fail-soft: the row is still parked dead when the task door is broken."""
    import api.services.task_triggers as task_triggers

    def boom(*a, **kw):
        raise RuntimeError("tasks unavailable")

    monkeypatch.setattr(task_triggers, "create_system_task", boom)
    _order(db)
    _park_dead(db, "dead-9")
    assert db.tasks.count_documents({}) == 0


# ---------------------------------------------------------------------------
# D55: the dead-letter alert is OWED until an open task names the webhook.
# ---------------------------------------------------------------------------


def _task_insert(mode):
    """The tasks insert fails for as long as the patch holds ('fails':
    nothing written), or lands once with its answer lost ('ack_lost')."""
    real = mongomock.collection.Collection.insert_one
    hits = []

    def flaky(self, doc, *args, **kwargs):
        if self.name != "tasks" or (hits and mode == "ack_lost"):
            return real(self, doc, *args, **kwargs)
        hits.append(1)
        if mode == "ack_lost":
            real(self, doc, *args, **kwargs)
        raise TimeoutError("synthetic task write outage")

    return patch.object(mongomock.collection.Collection, "insert_one", flaky), hits


def _park_dead_with_task_fault(db, webhook_id, mode):
    _inbox_row(db, webhook_id, "orders/delete", {"id": int(_SHOPIFY_ORDER_ID)})
    fault, hits = _task_insert(mode)
    with _lookup_outage("orders"):
        for _ in range(NexusAgent.WEBHOOK_MAX_ATTEMPTS - 1):
            _drain(db, webhook_id)
        with fault:
            row = _drain(db, webhook_id)
    assert hits and row["status"] == "dead", row
    return row


def _open_dead_tasks(db):
    return list(db.tasks.find({"source_ref": _DEAD_REF, "status": "OPEN"}))


@pytest.mark.parametrize("retry", ["drain", "sweep"])
def test_a_failed_alert_task_insert_is_raised_again(db, an_admin, retry):
    """Codex 1412-round1 P2. Before: the row was dead, the task insert failed
    and nothing ever raised the alert again (the dead row is never claimed)."""
    _order(db)
    row = _park_dead_with_task_fault(db, "dead-lost", "fails")
    assert row["alert_pending"] is True
    assert _open_dead_tasks(db) == []

    if retry == "drain":
        row = _drain(db, "dead-lost")
    else:
        asyncio.run(NexusAgent(db=db)._retry_failed_webhooks())
        row = db.webhook_inbox.find_one({"webhook_id": "dead-lost"})
    tasks = _open_dead_tasks(db)
    assert len(tasks) == 1 and "id dead-lost." in tasks[0]["description"], tasks
    assert "alert_pending" not in row and row["status"] == "dead"
    # Owed once: later drains and sweeps add nothing.
    _drain(db, "dead-lost")
    asyncio.run(NexusAgent(db=db)._retry_failed_webhooks())
    assert db.tasks.count_documents({}) == 1


def test_a_task_insert_whose_answer_was_lost_is_not_duplicated(db, an_admin):
    _order(db)
    _park_dead_with_task_fault(db, "dead-ack", "ack_lost")
    asyncio.run(NexusAgent(db=db)._retry_failed_webhooks())
    tasks = _open_dead_tasks(db)
    assert len(tasks) == 1, tasks
    assert tasks[0]["description"].count("id dead-ack.") == 1
    assert "alert_pending" not in db.webhook_inbox.find_one({"webhook_id": "dead-ack"})


def test_two_drains_raising_the_alert_at_once_open_one_task(db, an_admin, monkeypatch):
    """D55 unique key: both drains read "no open task" before either writes.
    Before: two open tasks. Now the second insert is refused (same episode id)
    and it joins the first."""
    import threading

    import api.services.task_triggers as task_triggers

    _order(db)
    for wid in ("race-1", "race-2"):
        _inbox_row(db, wid, "orders/delete", {"id": int(_SHOPIFY_ORDER_ID)})
        with _lookup_outage("orders"):
            for _ in range(NexusAgent.WEBHOOK_MAX_ATTEMPTS - 1):
                _drain(db, wid)
    gate = threading.Barrier(2, timeout=10)
    real_active = task_triggers.active_tasks
    first_reads = []

    def both_read_before_either_writes(repo, ref):
        rows = real_active(repo, ref)
        if len(first_reads) < 2:
            first_reads.append(1)
            gate.wait()
        return rows

    monkeypatch.setattr(task_triggers, "active_tasks", both_read_before_either_writes)

    # ONE patch around both threads: a patch entered per thread restores in
    # the wrong order and leaks the outage into later tests.
    threads = [threading.Thread(target=_drain, args=(db, w)) for w in ("race-1", "race-2")]
    with _lookup_outage("orders"):
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    assert mongomock.collection.Collection.find_one is _REAL_FIND_ONE

    tasks = _open_dead_tasks(db)
    assert len(tasks) == 1, [t["task_id"] for t in tasks]
    for wid in ("race-1", "race-2"):
        assert f"id {wid}." in tasks[0]["description"]
        assert "alert_pending" not in db.webhook_inbox.find_one({"webhook_id": wid})

