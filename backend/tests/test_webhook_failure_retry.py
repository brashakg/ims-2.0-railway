"""SF03 (audit 2026-10-18): a failed Shopify notification is retried, never
marked done.

Before: NexusAgent._handle_inbox_webhook set processed=True whatever the
handler did, and the customer wrapper swallowed its own exception, so a failed
customers/update was stored as done with no error, and a redelivery answered
"duplicate" without re-running it.

Now the inbox row records received -> processing -> succeeded | failed | dead;
`processed` keeps meaning "done" for old readers.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import mongomock
import pytest

from agents.implementations.nexus import NexusAgent
from api.routers import webhooks
from api.services import shopify_customer_sync
from api.services.online_sync_health import failed_webhook_summary


def _inbox_row(db, webhook_id, topic, payload=None, vendor="shopify", age=timedelta(minutes=2),
               **extra):
    db.webhook_inbox.insert_one({
        "webhook_id": webhook_id,
        "vendor": vendor,
        "received_at": datetime.now(timezone.utc) - age,
        "processed": False,
        "processed_at": None,
        "status": "received",
        "attempts": 0,
        "payload": payload if payload is not None else {"id": 123},
        "headers": {"x-shopify-topic": topic},
        **extra,
    })


def _drain(db, webhook_id, vendor="shopify"):
    asyncio.run(NexusAgent(db=db)._handle_inbox_webhook(webhook_id, vendor))
    return db.webhook_inbox.find_one({"webhook_id": webhook_id})


def _redelivery_dispatches(row) -> bool:
    with patch.object(webhooks, "_dispatch_webhook_received") as dispatch:
        reply = asyncio.run(webhooks._duplicate_response(row, "shopify", "same-delivery"))
    assert reply["status"] == "duplicate"
    return dispatch.called


@pytest.fixture
def db():
    return mongomock.MongoClient()["sf03"]


_CUSTOMER = {"id": 123, "email": "buyer@example.com", "first_name": "Ravi",
             "last_name": "Kumar", "phone": "+91 98765 43210"}


def test_audit_probe_failed_customer_update_is_failed_and_redelivery_reruns(db):
    """The audit probe, inverted: it asserted processed=True, no handler_error
    and no re-dispatch on main. Fails on main, passes here."""
    # Received just now (inside the 60 s in-flight grace): only the FAILED
    # state can make the redelivery re-dispatch it, not the row's age.
    _inbox_row(db, "audit-delivery", "customers/update", _CUSTOMER, age=timedelta(0))
    with patch.object(shopify_customer_sync, "upsert_shopify_customer",
                      side_effect=RuntimeError("synthetic database failure")):
        row = _drain(db, "audit-delivery")
    assert row["processed"] is False
    assert row["status"] == "failed"
    assert "synthetic database failure" in row["handler_error"]
    assert row["attempts"] == 1
    assert _redelivery_dispatches(row), "a redelivery must re-run a failed handler"

    # The redelivery's drain re-runs the handler and it succeeds.
    with patch.object(shopify_customer_sync, "upsert_shopify_customer",
                      return_value={"status": "created", "customer_id": "C-1"}) as up:
        row = _drain(db, "audit-delivery")
    up.assert_called_once()
    assert row["processed"] is True
    assert row["status"] == "succeeded"
    assert "handler_error" not in row
    assert row["attempts"] == 2
    assert not _redelivery_dispatches(row)


def test_a_failure_the_service_swallows_is_still_a_failure(db):
    """upsert_shopify_customer never raises: it RETURNS
    {"status": "skipped", "reason": "exception:<Type>"}. That must not read as done."""
    _inbox_row(db, "swallowed", "customers/update", _CUSTOMER)
    with patch.object(shopify_customer_sync, "_extract_customer",
                      side_effect=RuntimeError("mongo blip")):
        row = _drain(db, "swallowed")
    assert row["status"] == "failed" and row["processed"] is False
    assert "exception:RuntimeError" in row["handler_error"]


def test_success_path_unchanged(db):
    _inbox_row(db, "ok", "customers/create", _CUSTOMER)
    with patch.object(shopify_customer_sync, "upsert_shopify_customer",
                      return_value={"status": "created", "customer_id": "C-1"}):
        row = _drain(db, "ok")
    assert row["processed"] is True and row["processed_at"]
    assert row["status"] == "succeeded"
    assert "handler_error" not in row
    # A replay of a finished row is inert: no re-dispatch, no re-run.
    assert not _redelivery_dispatches(row)
    with patch.object(shopify_customer_sync, "upsert_shopify_customer") as up:
        _drain(db, "ok")
    up.assert_not_called()


def test_business_skip_is_success_not_failure(db):
    """A real business skip (no phone or email) is a finished outcome."""
    _inbox_row(db, "nobody", "customers/create", {"id": 9})
    row = _drain(db, "nobody")
    assert row["status"] == "succeeded" and row["processed"] is True


def test_terminal_skips_stay_terminal(db):
    # Unknown vendor: recorded, done, never retried.
    _inbox_row(db, "odd", "x", vendor="mystery")
    row = _drain(db, "odd", vendor="mystery")
    assert row["processed"] is True and row["status"] == "skipped"
    assert row["handler_error"] == "unknown_vendor:mystery"
    assert not _redelivery_dispatches(row)

    # Receiver-written skips (missing secret / too old) carry processed=True:
    # the drain never claims them and a redelivery never re-dispatches them.
    _inbox_row(db, "nosecret", "customers/update", processed=True,
               status="skipped", skipped_reason="secret_not_configured")
    with patch.object(shopify_customer_sync, "upsert_shopify_customer") as up:
        row = _drain(db, "nosecret")
    up.assert_not_called()
    assert row["status"] == "skipped" and row["processed"] is True
    assert not _redelivery_dispatches(row)


def test_bounded_retries_then_dead_letter_visible_to_superadmin(db):
    _inbox_row(db, "doomed", "customers/update", _CUSTOMER)
    boom = patch.object(shopify_customer_sync, "upsert_shopify_customer",
                        side_effect=RuntimeError("still broken"))
    with boom:
        for _ in range(NexusAgent.WEBHOOK_MAX_ATTEMPTS):
            row = _drain(db, "doomed")
    assert row["status"] == "dead" and row["processed"] is False
    assert row["attempts"] == NexusAgent.WEBHOOK_MAX_ATTEMPTS

    # Dead is final: no further run, no re-dispatch on redelivery.
    with patch.object(shopify_customer_sync, "upsert_shopify_customer") as up:
        _drain(db, "doomed")
        asyncio.run(NexusAgent(db=db)._retry_failed_webhooks())
    up.assert_not_called()
    assert not _redelivery_dispatches(row)

    # Counted on the SUPERADMIN sync-health card and listed in its API
    # (GET /admin/online-store/sync-health -> webhooks.dead_letters).
    summary = failed_webhook_summary(db)
    assert summary["checked"] is True, "the page tile shows '-' unless checked"
    assert summary["failed"] == 1, "the tile's failed count includes the dead row"
    assert summary["dead"] == 1
    assert summary["pending"] == 0, "a dead row is not 'waiting for a drain'"
    assert summary["dead_letters"][0]["webhook_id"] == "doomed"
    assert summary["dead_letters"][0]["topic"] == "customers/update"
    assert "still broken" in summary["dead_letters"][0]["error"]


def test_hourly_tick_retries_failed_rows(db):
    _inbox_row(db, "retry-me", "customers/update", _CUSTOMER)
    with patch.object(shopify_customer_sync, "upsert_shopify_customer",
                      side_effect=RuntimeError("transient")):
        _drain(db, "retry-me")
    with patch.object(shopify_customer_sync, "upsert_shopify_customer",
                      return_value={"status": "updated"}):
        asyncio.run(NexusAgent(db=db)._do_background_work())
    row = db.webhook_inbox.find_one({"webhook_id": "retry-me"})
    assert row["status"] == "succeeded" and row["processed"] is True


def test_in_flight_row_is_not_drained_twice(db):
    """A fresh processing lease blocks a second drain and a redelivery;
    an expired one (worker died) is rescued."""
    now = datetime.now(timezone.utc)
    _inbox_row(db, "busy", "customers/update", _CUSTOMER, status="processing",
               processing_at=now, attempts=1)
    with patch.object(shopify_customer_sync, "upsert_shopify_customer") as up:
        _drain(db, "busy")
    up.assert_not_called()
    assert not _redelivery_dispatches(db.webhook_inbox.find_one({"webhook_id": "busy"}))

    stale = now - NexusAgent.WEBHOOK_PROCESSING_LEASE - timedelta(minutes=1)
    db.webhook_inbox.update_one({"webhook_id": "busy"}, {"$set": {"processing_at": stale}})
    assert _redelivery_dispatches(db.webhook_inbox.find_one({"webhook_id": "busy"}))
    with patch.object(shopify_customer_sync, "upsert_shopify_customer",
                      return_value={"status": "updated"}):
        row = _drain(db, "busy")
    assert row["status"] == "succeeded" and row["attempts"] == 2


def test_failed_shopify_order_row_shows_failed_not_pending(db):
    """The online-orders FAILED queue reads the new state: a failed order
    drain is FAILED with its error, not PENDING."""
    from api.routers.online_store_orders import _unbooked_webhook_rows

    _inbox_row(db, "ord", "orders/create", {"id": 777, "line_items": []},
               status="failed", handler_error="RuntimeError: mapper down")
    rows = _unbooked_webhook_rows(db, search=None)
    assert rows[0]["map_status"] == "FAILED"
    assert "mapper down" in rows[0]["map_error"]


def test_a_late_drain_never_overwrites_a_newer_outcome(db):
    """F4: the outcome write is fenced on this drain's claim. A drain that
    outlived its lease (another drain took the row over and finished it)
    must not stamp its stale failure over the newer success."""
    _inbox_row(db, "fenced", "customers/update", _CUSTOMER)

    def taken_over_then_fail(*a, **k):
        db.webhook_inbox.update_one(
            {"webhook_id": "fenced"},
            {"$set": {"claim": "newer-drain", "status": "succeeded", "processed": True}},
        )
        raise RuntimeError("late failure")

    with patch.object(shopify_customer_sync, "upsert_shopify_customer",
                      side_effect=taken_over_then_fail):
        row = _drain(db, "fenced")
    assert row["status"] == "succeeded" and row["processed"] is True
    assert "handler_error" not in row


def test_app_uninstall_that_stopped_before_its_alert_alerts_on_retry(db, monkeypatch):
    """F2c: the alert row was written, then the run stopped before the HIGH
    alert. The retry used to answer 'duplicate' and never alert."""
    from api.services import shopify_app_uninstall as un

    sent = []

    async def emit(**kwargs):
        sent.append(kwargs)
        return "audit-1"

    monkeypatch.setattr("api.services.audit_alerts.emit_audit_alert", emit)
    real = un._raise_high_alert
    calls = []

    async def stop_once(*a, **k):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("stopped before the alert")
        return await real(*a, **k)

    monkeypatch.setattr(un, "_raise_high_alert", stop_once)
    _inbox_row(db, "gone", "app/uninstalled", {"id": 1, "myshopify_domain": "bv.myshopify.com"})
    row = _drain(db, "gone")
    assert row["status"] == "failed" and sent == []
    assert db.integration_alerts.find_one({"shop": "bv.myshopify.com"})["alert_pending"] is True

    row = _drain(db, "gone")
    assert row["status"] == "succeeded"
    assert len(sent) == 1, "the retry raises the alert the stopped run never sent"
    assert db.integration_alerts.count_documents({"shop": "bv.myshopify.com"}) == 1

    _inbox_row(db, "gone-again", "app/uninstalled", {"id": 1, "myshopify_domain": "bv.myshopify.com"})
    _drain(db, "gone-again")
    assert len(sent) == 1, "once alerted, a redelivery stays a duplicate"


def test_order_answer_already_booked_with_no_order_is_a_failure(db):
    """F1 backstop: 'replayed'/'duplicate' naming no IMS order is not done."""
    from api.services import online_fulfillment_route

    async def phantom(*a, **k):
        return {"status": "replayed", "order_id": None, "shopify_order_id": "9"}

    _inbox_row(db, "phantom", "orders/create", {"id": 9, "line_items": [{"id": 1}]})
    with patch.object(online_fulfillment_route, "map_routed_order", side_effect=phantom):
        row = _drain(db, "phantom")
    assert row["status"] == "failed" and "no IMS order" in row["handler_error"]
