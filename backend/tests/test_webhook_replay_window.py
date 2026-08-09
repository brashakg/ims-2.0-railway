"""
IMS 2.0 - Webhook replay-window regression tests (P1 money / data-loss)
=======================================================================
THE BUG: the receiver computed its anti-replay freshness check from the
SHOPIFY ORDER's `created_at` (a business field) instead of from the WEBHOOK
DELIVERY's own timestamp. With WEBHOOK_REPLAY_WINDOW_SECONDS=300 that meant
every later-lifecycle webhook about an order placed more than five minutes
ago - payment update, fulfillment, cancellation, REFUND - and every vendor
retry of a transient failure was classified a "replay" and silently dropped
with 200 {"status":"skipped"}. GST-bearing events were lost and the vendor
never resent them.

What these tests pin down:

  (a) a webhook about an order created 3 DAYS ago, delivered NOW with a
      valid signature, is ACCEPTED, persisted and dispatched  <- the bug
  (b) a genuine replay is REJECTED (same delivery id; same signed bytes with
      a rotated delivery id; a delivery timestamp beyond the staleness cap)
  (c) an invalid HMAC is REJECTED regardless of timing
  (d) a vendor RETRY of the same delivery is idempotent - one inbox row, one
      dispatch, no double-booked order / GST invoice

Fixture style mirrors tests/test_webhooks.py + tests/test_webhook_hardening.py
(self-contained Mongo fakes; the `client` fixture comes from conftest).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# JWT not required (webhook endpoints are unauth) but TestClient still mounts
# the full app, which expects this var.
os.environ.setdefault("JWT_SECRET_KEY", "test_x")

from agents import webhook_verify


# ============================================================================
# Mongo emulator - same shape as tests/test_webhooks.py (self-contained).
# ============================================================================


def _doc_matches(doc, filter_):
    if not filter_:
        return True
    for k, expected in filter_.items():
        actual = doc.get(k)
        if isinstance(expected, dict):
            for op, op_val in expected.items():
                if op == "$exists" and bool(actual is not None) != bool(op_val):
                    return False
                if op == "$ne" and actual == op_val:
                    return False
                if op == "$in" and actual not in op_val:
                    return False
        else:
            if actual != expected:
                return False
    return True


class FakeCollection:
    """Fake with REAL unique-index semantics for the two dedupe keys, so the
    tests exercise the same guarantee prod gets from Mongo."""

    def __init__(self):
        self.docs: List[Dict[str, Any]] = []
        self.indexes: List[Any] = []

    def insert_one(self, doc):
        for key in ("event_id", "body_fingerprint"):
            value = doc.get(key)
            if isinstance(value, str) and value:
                for existing in self.docs:
                    if (
                        existing.get("vendor") == doc.get("vendor")
                        and existing.get(key) == value
                    ):
                        raise type("DuplicateKeyError", (Exception,), {})(
                            f"E11000 duplicate key error: {key}"
                        )
        self.docs.append(dict(doc))
        return type("R", (), {"inserted_id": doc.get("webhook_id")})()

    def find_one(self, filter_=None, projection=None, **kwargs):
        for d in self.docs:
            if _doc_matches(d, filter_):
                return d
        return None

    def update_one(self, filter_, update, upsert=False):
        set_block = (update or {}).get("$set", {}) or {}
        for d in self.docs:
            if _doc_matches(d, filter_):
                for k, v in set_block.items():
                    d[k] = v
                return type("R", (), {"modified_count": 1, "matched_count": 1})()
        return type("R", (), {"modified_count": 0, "matched_count": 0})()

    def create_index(self, *args, **kwargs):
        self.indexes.append((args, kwargs))
        return None


class FakeDB:
    is_connected = True

    def __init__(self):
        self._collections: Dict[str, FakeCollection] = {}

    def get_collection(self, name):
        if name not in self._collections:
            self._collections[name] = FakeCollection()
        return self._collections[name]


# ============================================================================
# Helpers
# ============================================================================

SHOPIFY_SECRET = "shpfy_42"
RAZORPAY_SECRET = "rzp_secret_42"
SHIPROCKET_SECRET = "shrkt_42"


def _hex_sig(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _b64_sig(body: bytes, secret: str) -> str:
    return base64.b64encode(
        hmac.new(secret.encode(), body, hashlib.sha256).digest()
    ).decode("ascii")


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _old_order_body(days_old: int = 3, order_id: int = 5550001) -> bytes:
    """A Shopify order payload whose BUSINESS timestamps are days old - the
    exact shape that used to be misread as a replay."""
    placed = datetime.now(timezone.utc) - timedelta(days=days_old)
    return json.dumps(
        {
            "id": order_id,
            "name": "#BV1001",
            "created_at": _iso(placed),
            "updated_at": _iso(datetime.now(timezone.utc)),
            "financial_status": "paid",
            "total_price": "4999.00",
            "total_tax": "238.05",
            "line_items": [{"id": 1, "price": "4999.00", "quantity": 1}],
        },
        separators=(",", ":"),
    ).encode("utf-8")


def _post_shopify(client, body: bytes, *, topic="orders/paid", webhook_id=None,
                  triggered_at=None, secret=SHOPIFY_SECRET):
    headers = {
        "X-Shopify-Hmac-Sha256": _b64_sig(body, secret),
        "X-Shopify-Topic": topic,
        "X-Shopify-Shop-Domain": "bettervision.myshopify.com",
        "content-type": "application/json",
    }
    if webhook_id:
        headers["X-Shopify-Webhook-Id"] = webhook_id
    if triggered_at:
        headers["X-Shopify-Triggered-At"] = triggered_at
    return client.post("/api/v1/webhooks/shopify", content=body, headers=headers)


@pytest.fixture(autouse=True)
def _fresh_rate_limit_state():
    """The limiter's sliding window lives in the shared cache singleton, which
    outlives any one test. Flush before AND after so buckets never leak."""
    from api.services.cache import cache

    cache.flush()
    yield
    cache.flush()


@pytest.fixture
def patched_webhooks(monkeypatch):
    fake_db = FakeDB()

    integ = fake_db.get_collection("integrations")
    integ.insert_one({"type": "razorpay",
                      "config": {"webhook_secret": RAZORPAY_SECRET},
                      "enabled": True})
    integ.insert_one({"type": "shopify",
                      "config": {"webhook_secret": SHOPIFY_SECRET},
                      "enabled": True})
    integ.insert_one({"type": "shiprocket",
                      "config": {"webhook_secret": SHIPROCKET_SECRET},
                      "enabled": True})

    from api.routers import webhooks as wh_module
    monkeypatch.setattr(wh_module, "_get_db", lambda: fake_db)

    dispatched: List[Dict[str, Any]] = []

    async def fake_dispatch(event, payload, source=""):
        dispatched.append({"event": event, "payload": payload, "source": source})

    import agents.registry as reg
    monkeypatch.setattr(reg, "dispatch_event", fake_dispatch)

    yield {"db": fake_db, "dispatched": dispatched}


# ============================================================================
# (a) THE BUG - lifecycle webhooks about older orders must be ACCEPTED
# ============================================================================


def test_webhook_for_three_day_old_order_is_accepted(client, patched_webhooks):
    """A payment/fulfillment webhook for an order placed 3 days ago, delivered
    NOW with a valid signature, must be INGESTED - not skipped as a replay.

    Before the fix this returned {"status":"skipped",
    "reason":"replay_window_exceeded"} and the GST-bearing event was lost."""
    body = _old_order_body(days_old=3)

    r = _post_shopify(
        client,
        body,
        topic="orders/paid",
        webhook_id="wh-lifecycle-1",
        triggered_at=_iso(datetime.now(timezone.utc)),
    )

    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload["status"] == "received", (
        "a lifecycle webhook about an older order must be ingested, not "
        f"treated as a replay (got {payload})"
    )

    inbox = patched_webhooks["db"].get_collection("webhook_inbox")
    assert len(inbox.docs) == 1
    row = inbox.docs[0]
    assert row["vendor"] == "shopify"
    assert row["payload"]["id"] == 5550001
    assert row["processed"] is False
    assert len(patched_webhooks["dispatched"]) == 1


@pytest.mark.parametrize("topic", [
    "orders/paid",
    "orders/fulfilled",
    "orders/cancelled",
    "refunds/create",
    "orders/updated",
])
def test_every_lifecycle_topic_for_an_old_order_is_accepted(
    client, patched_webhooks, topic
):
    """Payment, fulfillment, cancellation and REFUND events all arrive long
    after the order was created. Every one of them must land."""
    body = _old_order_body(days_old=30, order_id=abs(hash(topic)) % 10**7)

    r = _post_shopify(
        client,
        body,
        topic=topic,
        webhook_id=f"wh-{topic.replace('/', '-')}",
        triggered_at=_iso(datetime.now(timezone.utc)),
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "received", f"{topic} was dropped"
    assert len(patched_webhooks["dispatched"]) == 1


def test_old_order_accepted_even_without_triggered_at_header(
    client, patched_webhooks
):
    """Fail-safe: an older payload with NO delivery-timestamp header must
    still be accepted (dedupe carries the replay defence). Dropping a real
    GST-bearing order because we could not read a clock is the worse
    failure."""
    body = _old_order_body(days_old=10, order_id=777001)

    r = _post_shopify(client, body, topic="orders/paid", webhook_id="wh-no-ts")

    assert r.status_code == 200, r.text
    assert r.json()["status"] == "received"
    assert len(patched_webhooks["db"].get_collection("webhook_inbox").docs) == 1


def test_razorpay_refund_for_an_old_payment_is_accepted(client, patched_webhooks):
    """Razorpay refund.created for a payment captured a week ago. The nested
    entity created_at is ancient; the ENVELOPE's created_at (the event
    emission time) is now. Must be accepted."""
    week_ago = int((datetime.now(timezone.utc) - timedelta(days=7)).timestamp())
    body = json.dumps(
        {
            "entity": "event",
            "event": "refund.created",
            "created_at": int(datetime.now(timezone.utc).timestamp()),
            "payload": {
                "payment": {
                    "entity": {
                        "id": "pay_old_1",
                        "amount": 499900,
                        "created_at": week_ago,
                    }
                }
            },
        },
        separators=(",", ":"),
    ).encode("utf-8")

    r = client.post(
        "/api/v1/webhooks/razorpay",
        content=body,
        headers={
            "X-Razorpay-Signature": _hex_sig(body, RAZORPAY_SECRET),
            "X-Razorpay-Event-Id": "evt_refund_old",
            "content-type": "application/json",
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "received"
    assert len(patched_webhooks["dispatched"]) == 1


# ============================================================================
# (b) genuine replays must still be REJECTED
# ============================================================================


def test_replayed_delivery_same_webhook_id_is_rejected(client, patched_webhooks):
    """Verbatim replay carrying the vendor's own delivery id -> duplicate.
    One inbox row, one dispatch."""
    body = _old_order_body(days_old=1, order_id=931001)
    kwargs = dict(topic="orders/paid", webhook_id="wh-replay-1",
                  triggered_at=_iso(datetime.now(timezone.utc)))

    assert _post_shopify(client, body, **kwargs).json()["status"] == "received"
    r2 = _post_shopify(client, body, **kwargs)

    assert r2.status_code == 200
    assert r2.json()["status"] == "duplicate"
    assert len(patched_webhooks["db"].get_collection("webhook_inbox").docs) == 1
    assert len(patched_webhooks["dispatched"]) == 1


def test_replay_with_rotated_delivery_id_is_still_rejected(client, patched_webhooks):
    """The delivery-id and triggered-at headers are OUTSIDE the HMAC, so an
    attacker replaying a captured delivery can rewrite both. The body
    fingerprint is inside the signature's coverage and cannot be changed
    without invalidating it - so the replay is still rejected."""
    body = _old_order_body(days_old=1, order_id=931002)

    first = _post_shopify(client, body, topic="orders/paid",
                          webhook_id="wh-original",
                          triggered_at=_iso(datetime.now(timezone.utc)))
    assert first.json()["status"] == "received"

    # Attacker resends the captured bytes with a fresh id + a fresh clock.
    replay = _post_shopify(client, body, topic="orders/paid",
                           webhook_id="wh-attacker-rotated",
                           triggered_at=_iso(datetime.now(timezone.utc)))

    assert replay.status_code == 200
    assert replay.json()["status"] == "duplicate", (
        "rotating the (unsigned) delivery-id header must not defeat dedupe"
    )
    assert len(patched_webhooks["db"].get_collection("webhook_inbox").docs) == 1
    assert len(patched_webhooks["dispatched"]) == 1, "replay must not re-dispatch"


def test_delivery_timestamp_beyond_staleness_cap_is_skipped_but_recorded(
    client, patched_webhooks
):
    """A delivery whose OWN clock is older than the staleness cap is not
    processed — but it is NOT silently discarded either.

    The cap is pinned to the 30-day dedupe retention (no env override here:
    this exercises the real default), so it can only bite where dedupe
    genuinely cannot help. And the row is persisted with processed=True +
    skipped_reason so a correctly-signed delivery always leaves a durable,
    greppable record."""
    body = _old_order_body(days_old=40, order_id=931003)

    r = _post_shopify(
        client,
        body,
        topic="orders/paid",
        webhook_id="wh-stale",
        triggered_at=_iso(datetime.now(timezone.utc) - timedelta(days=40)),
    )

    assert r.status_code == 200
    payload = r.json()
    assert payload["status"] == "skipped"
    assert payload["reason"] == "delivery_too_old"
    assert payload["webhook_id"], "the skip must be traceable to a row"

    inbox = patched_webhooks["db"].get_collection("webhook_inbox")
    assert len(inbox.docs) == 1, "a signed delivery must never vanish without a record"
    row = inbox.docs[0]
    assert row["skipped_reason"] == "delivery_too_old"
    assert row["processed"] is True, "must never be drained as real work"
    assert row["payload"]["id"] == 931003
    assert patched_webhooks["dispatched"] == [], "must not dispatch a stale delivery"


def test_retry_of_a_stale_delivery_resolves_as_duplicate_not_a_second_skip_row(
    client, patched_webhooks
):
    """Dedupe runs before the staleness cap, so a vendor retrying a delivery
    we already recorded as too old gets a duplicate — not an unbounded pile
    of skip rows."""
    body = _old_order_body(days_old=40, order_id=931009)
    kwargs = dict(topic="orders/paid", webhook_id="wh-stale-retry",
                  triggered_at=_iso(datetime.now(timezone.utc) - timedelta(days=40)))

    first = _post_shopify(client, body, **kwargs)
    assert first.json()["reason"] == "delivery_too_old"

    retry = _post_shopify(client, body, **kwargs)
    assert retry.json()["status"] == "duplicate"

    inbox = patched_webhooks["db"].get_collection("webhook_inbox")
    assert len(inbox.docs) == 1, "a retry must not write a second skip row"
    assert patched_webhooks["dispatched"] == []


def test_razorpay_resend_after_a_long_outage_is_not_silently_dropped(
    client, patched_webhooks
):
    """REGRESSION (must-fix 2). Razorpay's clock is INSIDE the HMAC, so unlike
    a Shopify header it cannot be omitted — an owner clicking Resend in the
    Razorpay dashboard after a long outage has no escape hatch.

    A 10-day-old envelope (well past the old 7-day cap) must be ingested
    normally. Only beyond the 30-day dedupe retention is it refused, and even
    then it is recorded, never dropped."""
    def _envelope(age_days: int, pay_id: str) -> bytes:
        return json.dumps(
            {
                "entity": "event",
                "event": "payment.captured",
                "created_at": int(
                    (datetime.now(timezone.utc) - timedelta(days=age_days)).timestamp()
                ),
                "payload": {"payment": {"entity": {"id": pay_id, "amount": 499900}}},
            },
            separators=(",", ":"),
        ).encode("utf-8")

    def _post(body: bytes, event_id: str):
        return client.post(
            "/api/v1/webhooks/razorpay",
            content=body,
            headers={
                "X-Razorpay-Signature": _hex_sig(body, RAZORPAY_SECRET),
                "X-Razorpay-Event-Id": event_id,
                "content-type": "application/json",
            },
        )

    ten_days = _post(_envelope(10, "pay_outage_1"), "evt_outage_1")
    assert ten_days.status_code == 200, ten_days.text
    assert ten_days.json()["status"] == "received", (
        "a 10-day-old signed Razorpay envelope must be ingested — the old "
        "7-day cap turned an operator resend into permanent GST loss"
    )
    assert len(patched_webhooks["dispatched"]) == 1

    beyond = _post(_envelope(40, "pay_outage_2"), "evt_outage_2")
    assert beyond.json()["status"] == "skipped"
    assert beyond.json()["reason"] == "delivery_too_old"
    rows = patched_webhooks["db"].get_collection("webhook_inbox").docs
    assert len(rows) == 2, "even the refused delivery must leave a durable row"
    assert rows[1]["skipped_reason"] == "delivery_too_old"
    assert len(patched_webhooks["dispatched"]) == 1, "stale one must not dispatch"


def test_shiprocket_verbatim_replay_is_rejected(client, patched_webhooks):
    """Shiprocket sends no delivery id at all. It previously had NO working
    replay cover; the body fingerprint now gives it one."""
    body = b'{"awb":"AWB12345","current_status":"DELIVERED","scan_at":"2026-08-01T10:00:00Z"}'
    headers = {
        "X-Shiprocket-Signature": _hex_sig(body, SHIPROCKET_SECRET),
        "X-Shiprocket-Event": "shipment.status",
        "content-type": "application/json",
    }

    r1 = client.post("/api/v1/webhooks/shiprocket", content=body, headers=headers)
    r2 = client.post("/api/v1/webhooks/shiprocket", content=body, headers=headers)

    assert r1.json()["status"] == "received"
    assert r2.json()["status"] == "duplicate"
    assert len(patched_webhooks["db"].get_collection("webhook_inbox").docs) == 1
    assert len(patched_webhooks["dispatched"]) == 1


@pytest.mark.parametrize("attacker_topic", [
    "Orders/Paid",
    "ORDERS/PAID",
    "orders/Paid",
    " orders/paid",
    "orders/paid ",
    "\torders/paid",
])
def test_case_or_whitespace_variant_of_topic_cannot_mint_a_new_dedupe_key(
    client, patched_webhooks, attacker_topic
):
    """REGRESSION (must-fix 1). The topic comes from an UNSIGNED header. If it
    were hashed verbatim, an attacker could vary only its case or padding to
    mint a fresh dedupe key for the exact same signed bytes and replay one
    captured delivery thousands of times — while the consumer
    (nexus._handle_shopify_webhook) lower-cases the same header and routes
    every variant identically.

    Both the topic and the delivery id are rotated here; the replay must
    still be a duplicate, with exactly one inbox row and one dispatch."""
    body = _old_order_body(days_old=1, order_id=932001)

    first = _post_shopify(client, body, topic="orders/paid",
                          webhook_id="wh-original",
                          triggered_at=_iso(datetime.now(timezone.utc)))
    assert first.json()["status"] == "received"

    replay = _post_shopify(client, body, topic=attacker_topic,
                           webhook_id="wh-rotated",
                           triggered_at=_iso(datetime.now(timezone.utc)))

    assert replay.status_code == 200
    assert replay.json()["status"] == "duplicate", (
        f"topic variant {attacker_topic!r} minted a fresh dedupe key"
    )
    assert len(patched_webhooks["db"].get_collection("webhook_inbox").docs) == 1
    assert len(patched_webhooks["dispatched"]) == 1


def test_whitespace_padded_delivery_id_cannot_dodge_the_unique_index(
    client, patched_webhooks
):
    """REGRESSION (must-fix 1). event_id is stripped before it is used as a
    dedupe key, so ' evt_x ' and 'evt_x' are the same delivery."""
    body = json.dumps(
        {"entity": "event", "event": "payment.captured",
         "payload": {"payment": {"entity": {"id": "pay_ws", "amount": 100}}}},
        separators=(",", ":"),
    ).encode("utf-8")

    def _post(event_id: str):
        return client.post(
            "/api/v1/webhooks/razorpay",
            content=body,
            headers={
                "X-Razorpay-Signature": _hex_sig(body, RAZORPAY_SECRET),
                "X-Razorpay-Event-Id": event_id,
                "content-type": "application/json",
            },
        )

    assert _post("evt_ws_1").json()["status"] == "received"
    assert _post("  evt_ws_1  ").json()["status"] == "duplicate"

    inbox = patched_webhooks["db"].get_collection("webhook_inbox")
    assert len(inbox.docs) == 1
    assert inbox.docs[0]["event_id"] == "evt_ws_1", "the stored id must be stripped"
    assert len(patched_webhooks["dispatched"]) == 1


def test_retry_redispatches_a_row_whose_dispatch_failed(client, patched_webhooks,
                                                        monkeypatch):
    """REGRESSION (must-fix 3). Nothing sweeps webhook_inbox for
    processed=false — NexusAgent._handle_inbox_webhook is reachable only from
    on_event('webhook.received'), and _do_background_work iterates
    INTEGRATION_SCHEDULES. So a row whose dispatch failed is stranded, and the
    fingerprint dedupe now swallows the vendor retry that used to rescue it by
    accident.

    A retry that matches an UNPROCESSED row must therefore re-dispatch it."""
    import agents.registry as reg

    async def boom(event, payload, source=""):
        raise RuntimeError("nexus registry unavailable")

    monkeypatch.setattr(reg, "dispatch_event", boom)

    body = _old_order_body(days_old=1, order_id=933001)
    kwargs = dict(topic="orders/paid", webhook_id="wh-stranded",
                  triggered_at=_iso(datetime.now(timezone.utc)))

    first = _post_shopify(client, body, **kwargs)
    assert first.status_code == 200, "a dispatch failure must still ACK"
    assert first.json()["status"] == "received"

    inbox = patched_webhooks["db"].get_collection("webhook_inbox")
    assert len(inbox.docs) == 1
    assert inbox.docs[0]["processed"] is False, "row is stranded, unprocessed"
    assert patched_webhooks["dispatched"] == []

    # Age the row past the grace window: below it, an unprocessed row is
    # assumed to be a normal in-flight async dispatch, not a stranded one.
    inbox.docs[0]["received_at"] = datetime.now(timezone.utc) - timedelta(minutes=5)

    # NEXUS comes back; the vendor retries the same delivery.
    async def ok(event, payload, source=""):
        patched_webhooks["dispatched"].append({"event": event, "payload": payload})

    monkeypatch.setattr(reg, "dispatch_event", ok)

    retry = _post_shopify(client, body, **kwargs)
    assert retry.status_code == 200
    out = retry.json()
    assert out["status"] == "duplicate", "still must not create a second row"
    assert out["redispatched"] is True, "the stranded row must be re-dispatched"
    assert out["webhook_id"] == inbox.docs[0]["webhook_id"]

    assert len(inbox.docs) == 1, "re-dispatch must not write a second row"
    assert len(patched_webhooks["dispatched"]) == 1
    assert patched_webhooks["dispatched"][0]["payload"]["webhook_id"] == (
        inbox.docs[0]["webhook_id"]
    )


def test_duplicate_of_a_processed_row_does_not_redispatch(client, patched_webhooks):
    """The mirror of the test above: once a row HAS drained, a replay must
    stay inert — ack only, no re-dispatch, no reprocessing. This also covers
    a row NEXUS drained with a handler_error: it is processed, so a replay
    must not silently re-run a failed money handler."""
    body = _old_order_body(days_old=1, order_id=933002)
    kwargs = dict(topic="orders/paid", webhook_id="wh-drained",
                  triggered_at=_iso(datetime.now(timezone.utc)))

    assert _post_shopify(client, body, **kwargs).json()["status"] == "received"

    inbox = patched_webhooks["db"].get_collection("webhook_inbox")
    inbox.docs[0]["processed"] = True  # NEXUS drained it
    inbox.docs[0]["handler_error"] = "ValueError: boom"
    inbox.docs[0]["received_at"] = datetime.now(timezone.utc) - timedelta(hours=2)

    replay = _post_shopify(client, body, **kwargs)
    out = replay.json()
    assert out["status"] == "duplicate"
    assert "redispatched" not in out
    assert len(patched_webhooks["dispatched"]) == 1, "must not re-dispatch"


def test_fast_retry_of_an_inflight_row_does_not_double_dispatch(
    client, patched_webhooks
):
    """The event bus fans out asynchronously when Redis is configured (prod),
    so processed=false immediately after a 200 is the NORMAL in-flight state.
    A retry arriving inside the grace window must NOT start a second
    concurrent drain of the same row."""
    body = _old_order_body(days_old=1, order_id=933003)
    kwargs = dict(topic="orders/paid", webhook_id="wh-inflight",
                  triggered_at=_iso(datetime.now(timezone.utc)))

    assert _post_shopify(client, body, **kwargs).json()["status"] == "received"

    inbox = patched_webhooks["db"].get_collection("webhook_inbox")
    assert inbox.docs[0]["processed"] is False, "still draining"

    out = _post_shopify(client, body, **kwargs).json()
    assert out["status"] == "duplicate"
    assert out.get("redispatched") is None, (
        "an in-flight row must not be re-dispatched"
    )
    assert len(patched_webhooks["dispatched"]) == 1


def test_duplicate_echoes_the_matched_webhook_id(client, patched_webhooks):
    """Shopify's 'Send test notification' posts a byte-stable canned payload,
    so every send after the first dedupes. Echo the matched webhook_id so an
    operator can see WHICH row matched rather than reading it as a failure."""
    body = b'{"awb":"AWB-TEST","current_status":"TEST"}'
    headers = {
        "X-Shiprocket-Signature": _hex_sig(body, SHIPROCKET_SECRET),
        "content-type": "application/json",
    }
    first = client.post("/api/v1/webhooks/shiprocket", content=body, headers=headers)
    second = client.post("/api/v1/webhooks/shiprocket", content=body, headers=headers)

    assert second.json()["status"] == "duplicate"
    assert second.json()["webhook_id"] == first.json()["webhook_id"]


def test_same_body_under_a_different_topic_is_not_collapsed(
    client, patched_webhooks
):
    """The fingerprint is scoped by event type, so two genuinely different
    topics carrying a byte-identical body stay distinct deliveries."""
    body = _old_order_body(days_old=2, order_id=931004)

    r1 = _post_shopify(client, body, topic="orders/paid", webhook_id="wh-t1")
    r2 = _post_shopify(client, body, topic="orders/fulfilled", webhook_id="wh-t2")

    assert r1.json()["status"] == "received"
    assert r2.json()["status"] == "received"
    assert len(patched_webhooks["db"].get_collection("webhook_inbox").docs) == 2
    assert len(patched_webhooks["dispatched"]) == 2


def test_unique_body_fingerprint_index_is_ensured(client, patched_webhooks):
    """The (vendor, body_fingerprint) unique PARTIAL index - the multi-worker
    race backstop for content-bound dedupe - is created idempotently."""
    _post_shopify(client, _old_order_body(days_old=1, order_id=931005),
                  webhook_id="wh-idx")

    inbox = patched_webhooks["db"].get_collection("webhook_inbox")
    matched = [
        (args, kwargs)
        for (args, kwargs) in inbox.indexes
        if kwargs.get("name") == "uniq_webhook_body_fingerprint"
    ]
    assert matched, "uniq_webhook_body_fingerprint index was not ensured"
    args, kwargs = matched[0]
    assert args[0] == [("vendor", 1), ("body_fingerprint", 1)]
    assert kwargs.get("unique") is True
    assert kwargs.get("partialFilterExpression") == {
        "body_fingerprint": {"$type": "string"}
    }


def test_unique_index_race_backstop_on_fingerprint(client, patched_webhooks,
                                                   monkeypatch):
    """When a concurrent worker wins the insert between our pre-check and our
    insert, the unique index raises - the receiver must ACK 200 duplicate and
    not re-dispatch, even when there is no delivery-id header."""
    body = b'{"awb":"AWB999","current_status":"IN_TRANSIT"}'
    headers = {
        "X-Shiprocket-Signature": _hex_sig(body, SHIPROCKET_SECRET),
        "content-type": "application/json",
    }
    r1 = client.post("/api/v1/webhooks/shiprocket", content=body, headers=headers)
    assert r1.json()["status"] == "received"

    inbox = patched_webhooks["db"].get_collection("webhook_inbox")
    # Pre-check misses (as it would if the winner inserted a microsecond
    # later), but the unique index still rejects the insert.
    monkeypatch.setattr(inbox, "find_one", lambda *a, **k: None)

    r2 = client.post("/api/v1/webhooks/shiprocket", content=body, headers=headers)
    assert r2.status_code == 200, r2.text
    assert r2.json()["status"] == "duplicate"
    assert len(inbox.docs) == 1
    assert len(patched_webhooks["dispatched"]) == 1


# ============================================================================
# (c) HMAC is unchanged - invalid signature is rejected regardless of timing
# ============================================================================


@pytest.mark.parametrize("age_days,triggered_offset_seconds", [
    (0, 0),          # brand-new order, delivered now
    (3, 0),          # old order, delivered now  (the availability fix)
    (3, -86400),     # old order, delivery clock a day old
])
def test_invalid_hmac_rejected_regardless_of_timing(
    client, patched_webhooks, age_days, triggered_offset_seconds
):
    """The signature gate is untouched by this fix: a forged/mutated payload
    is 401 at every point on the timeline, nothing is persisted, nothing is
    dispatched."""
    body = _old_order_body(days_old=age_days, order_id=940000 + age_days)
    triggered = _iso(
        datetime.now(timezone.utc) + timedelta(seconds=triggered_offset_seconds)
    )

    r = client.post(
        "/api/v1/webhooks/shopify",
        content=body,
        headers={
            "X-Shopify-Hmac-Sha256": _b64_sig(body, "attacker_secret"),
            "X-Shopify-Topic": "orders/paid",
            "X-Shopify-Webhook-Id": "wh-forged",
            "X-Shopify-Triggered-At": triggered,
            "content-type": "application/json",
        },
    )

    assert r.status_code == 401
    assert len(patched_webhooks["db"].get_collection("webhook_inbox").docs) == 0
    assert patched_webhooks["dispatched"] == []


def test_tampered_body_with_captured_signature_is_rejected(
    client, patched_webhooks
):
    """An attacker who captured a valid delivery cannot edit the amount and
    reuse the signature - and therefore cannot escape fingerprint dedupe by
    mutating the body either."""
    body = _old_order_body(days_old=1, order_id=941001)
    good_sig = _b64_sig(body, SHOPIFY_SECRET)
    tampered = body.replace(b'"total_price":"4999.00"', b'"total_price":"0.01"')
    assert tampered != body

    r = client.post(
        "/api/v1/webhooks/shopify",
        content=tampered,
        headers={
            "X-Shopify-Hmac-Sha256": good_sig,
            "X-Shopify-Topic": "orders/paid",
            "content-type": "application/json",
        },
    )
    assert r.status_code == 401
    assert len(patched_webhooks["db"].get_collection("webhook_inbox").docs) == 0


def test_missing_signature_still_401_for_an_old_order(client, patched_webhooks):
    body = _old_order_body(days_old=3, order_id=941002)
    r = client.post(
        "/api/v1/webhooks/shopify",
        content=body,
        headers={"X-Shopify-Topic": "orders/paid",
                 "content-type": "application/json"},
    )
    assert r.status_code == 401


# ============================================================================
# (d) vendor retry of the same delivery is idempotent
# ============================================================================


def test_shopify_retry_is_idempotent_no_double_booking(client, patched_webhooks):
    """Shopify retries a failed delivery for ~48 h, reusing the same
    X-Shopify-Webhook-Id and the same signed body. Every retry must be ACKed
    and produce exactly ONE inbox row and ONE dispatch - the order (and its
    GST invoice) is never booked twice."""
    body = _old_order_body(days_old=2, order_id=950001)
    first_triggered = _iso(datetime.now(timezone.utc) - timedelta(hours=6))

    statuses = []
    for _ in range(5):
        r = _post_shopify(client, body, topic="orders/create",
                          webhook_id="wh-retry-77",
                          triggered_at=first_triggered)
        assert r.status_code == 200, r.text
        statuses.append(r.json()["status"])

    assert statuses[0] == "received"
    assert set(statuses[1:]) == {"duplicate"}, statuses

    inbox = patched_webhooks["db"].get_collection("webhook_inbox")
    assert len(inbox.docs) == 1, "a retry must never create a second order row"
    assert inbox.docs[0]["payload"]["id"] == 950001
    assert len(patched_webhooks["dispatched"]) == 1, "retry must not re-dispatch"


def test_razorpay_retry_is_idempotent(client, patched_webhooks):
    body = json.dumps(
        {
            "entity": "event",
            "event": "payment.captured",
            "created_at": int(datetime.now(timezone.utc).timestamp()),
            "payload": {"payment": {"entity": {"id": "pay_r1", "amount": 15000}}},
        },
        separators=(",", ":"),
    ).encode("utf-8")
    headers = {
        "X-Razorpay-Signature": _hex_sig(body, RAZORPAY_SECRET),
        "X-Razorpay-Event-Id": "evt_retry_1",
        "content-type": "application/json",
    }

    r1 = client.post("/api/v1/webhooks/razorpay", content=body, headers=headers)
    r2 = client.post("/api/v1/webhooks/razorpay", content=body, headers=headers)

    assert r1.json()["status"] == "received"
    assert r2.json()["status"] == "duplicate"
    assert len(patched_webhooks["db"].get_collection("webhook_inbox").docs) == 1
    assert len(patched_webhooks["dispatched"]) == 1


# ============================================================================
# Pure-function unit tests for the new webhook_verify surface
# ============================================================================


class TestExtractDeliveryTimestamp:
    def test_shopify_uses_triggered_at_header(self):
        now = _iso(datetime.now(timezone.utc))
        got = webhook_verify.extract_delivery_timestamp(
            "shopify",
            {"X-Shopify-Triggered-At": now, "content-type": "application/json"},
            {"created_at": "2020-01-01T00:00:00Z"},
        )
        assert got == now

    def test_shopify_never_falls_back_to_business_created_at(self):
        """The whole point of the fix: the ORDER's created_at is never a
        delivery timestamp."""
        got = webhook_verify.extract_delivery_timestamp(
            "shopify", {}, {"created_at": "2020-01-01T00:00:00Z"}
        )
        assert got is None

    def test_header_lookup_is_case_insensitive(self):
        got = webhook_verify.extract_delivery_timestamp(
            "shopify", {"x-shopify-triggered-at": "2026-08-09T10:00:00Z"}, None
        )
        assert got == "2026-08-09T10:00:00Z"

    def test_razorpay_uses_signed_envelope_epoch(self):
        got = webhook_verify.extract_delivery_timestamp(
            "razorpay", {}, {"entity": "event", "created_at": 1786000000}
        )
        assert got == "1786000000"

    def test_razorpay_ignores_non_numeric_created_at(self):
        assert webhook_verify.extract_delivery_timestamp(
            "razorpay", {}, {"created_at": "2020-01-01T00:00:00Z"}
        ) is None
        assert webhook_verify.extract_delivery_timestamp(
            "razorpay", {}, {"created_at": True}
        ) is None

    def test_shiprocket_has_no_delivery_clock(self):
        assert webhook_verify.extract_delivery_timestamp(
            "shiprocket", {"x-shiprocket-event": "status"}, {"current_status": "X"}
        ) is None

    def test_unknown_vendor_and_garbage_inputs_return_none(self):
        assert webhook_verify.extract_delivery_timestamp("nope", {}, {}) is None
        assert webhook_verify.extract_delivery_timestamp("shopify", None, None) is None
        assert webhook_verify.extract_delivery_timestamp("shopify", {}, "not-a-dict") is None


class TestIsStaleDelivery:
    def test_fresh_delivery_not_stale(self):
        assert webhook_verify.is_stale_delivery(
            _iso(datetime.now(timezone.utc))
        ) is False

    def test_48h_old_delivery_within_default_cap(self):
        """Shopify's full retry horizon must fit inside the default cap."""
        ts = _iso(datetime.now(timezone.utc) - timedelta(hours=48))
        assert webhook_verify.is_stale_delivery(ts) is False

    def test_ten_day_old_delivery_within_default_cap(self):
        """An operator resending after a long outage must not be dropped."""
        ts = _iso(datetime.now(timezone.utc) - timedelta(days=10))
        assert webhook_verify.is_stale_delivery(ts) is False

    def test_beyond_cap_is_stale(self):
        ts = _iso(datetime.now(timezone.utc) - timedelta(days=40))
        assert webhook_verify.is_stale_delivery(ts) is True

    @pytest.mark.parametrize("fraction", [
        ".320072772",   # what Shopify actually sends: 9 digits
        ".320072",      # 6 digits
        ".320",         # 3 digits
        ".32",          # 2 digits
        "",             # none
    ])
    def test_shopify_fractional_second_precision_is_parsed(self, fraction):
        """X-Shopify-Triggered-At carries NINE fractional digits, which
        datetime.fromisoformat before Python 3.11 (3.10 is a REQUIRED CI
        target) cannot parse. Without normalisation the cap would be a
        permanent no-op on half the deploy matrix and nothing would notice."""
        old = datetime.now(timezone.utc) - timedelta(days=40)
        ts = old.strftime("%Y-%m-%dT%H:%M:%S") + fraction + "Z"
        assert webhook_verify._parse_iso(ts) is not None, f"unparsed: {ts}"
        assert webhook_verify.is_stale_delivery(ts) is True

    def test_fractional_seconds_with_a_numeric_offset(self):
        ts = "2026-08-09T10:15:16.320072772+05:30"
        parsed = webhook_verify._parse_iso(ts)
        assert parsed is not None
        assert parsed.utcoffset().total_seconds() == 5.5 * 3600

    def test_explicit_window_argument_wins(self):
        ts = _iso(datetime.now(timezone.utc) - timedelta(seconds=120))
        assert webhook_verify.is_stale_delivery(ts, window_seconds=60) is True
        assert webhook_verify.is_stale_delivery(ts, window_seconds=600) is False

    def test_missing_or_garbage_timestamp_is_fail_safe(self):
        assert webhook_verify.is_stale_delivery("") is False
        assert webhook_verify.is_stale_delivery(None) is False  # type: ignore[arg-type]
        assert webhook_verify.is_stale_delivery("not-a-date") is False

    def test_epoch_seconds_and_millis_parsed(self):
        old = datetime.now(timezone.utc) - timedelta(days=40)
        assert webhook_verify.is_stale_delivery(str(int(old.timestamp()))) is True
        assert webhook_verify.is_stale_delivery(str(int(old.timestamp() * 1000))) is True
        fresh = datetime.now(timezone.utc)
        assert webhook_verify.is_stale_delivery(str(int(fresh.timestamp()))) is False

    def test_cap_default_is_pinned_to_the_dedupe_retention(self, monkeypatch):
        """The cap may only bite where dedupe genuinely cannot help, i.e.
        once the dedupe row has expired. Anything tighter is a drop path with
        no security benefit."""
        monkeypatch.delenv("WEBHOOK_DELIVERY_MAX_AGE_SECONDS", raising=False)
        assert (
            webhook_verify.delivery_max_age_seconds()
            == webhook_verify.DEDUPE_RETENTION_SECONDS
            == 30 * 24 * 3600
        )

    def test_env_override_and_garbage_fallback(self, monkeypatch):
        monkeypatch.setenv("WEBHOOK_DELIVERY_MAX_AGE_SECONDS", str(10 * 24 * 3600))
        assert webhook_verify.delivery_max_age_seconds() == 10 * 24 * 3600
        monkeypatch.setenv("WEBHOOK_DELIVERY_MAX_AGE_SECONDS", "garbage")
        assert webhook_verify.delivery_max_age_seconds() == 30 * 24 * 3600
        # A zero/negative cap would reject everything - never honour it.
        monkeypatch.setenv("WEBHOOK_DELIVERY_MAX_AGE_SECONDS", "0")
        assert webhook_verify.delivery_max_age_seconds() == 30 * 24 * 3600

    @pytest.mark.parametrize("bad", ["300", "60", "3600", "86399"])
    def test_env_override_below_the_floor_is_clamped(self, monkeypatch, bad):
        """An operator pasting the old WEBHOOK_REPLAY_WINDOW_SECONDS value
        (300) into the new key would reinstate the exact outage this module
        exists to fix, on live GST traffic. Anything under the longest vendor
        retry horizon is clamped to it."""
        monkeypatch.setenv("WEBHOOK_DELIVERY_MAX_AGE_SECONDS", bad)
        assert webhook_verify.delivery_max_age_seconds() == 48 * 3600

    def test_a_clamped_cap_still_accepts_a_full_vendor_retry_horizon(
        self, monkeypatch
    ):
        monkeypatch.setenv("WEBHOOK_DELIVERY_MAX_AGE_SECONDS", "300")
        ts = _iso(datetime.now(timezone.utc) - timedelta(hours=47))
        assert webhook_verify.is_stale_delivery(ts) is False

    def test_narrow_replay_window_env_does_not_affect_delivery_cap(self, monkeypatch):
        """WEBHOOK_REPLAY_WINDOW_SECONDS=300 (the prod default that caused the
        outage) must no longer be able to reject a legitimate delivery."""
        monkeypatch.setenv("WEBHOOK_REPLAY_WINDOW_SECONDS", "300")
        ts = _iso(datetime.now(timezone.utc) - timedelta(hours=12))
        assert webhook_verify.is_stale_delivery(ts) is False


class TestBodyFingerprint:
    BODY = b'{"id":1,"total":"100.00"}'

    def test_identical_bytes_same_fingerprint(self):
        a = webhook_verify.body_fingerprint("shopify", self.BODY, scope="orders/paid")
        b = webhook_verify.body_fingerprint("shopify", self.BODY, scope="orders/paid")
        assert a == b
        assert a.startswith("sha256:")

    def test_one_byte_change_changes_fingerprint(self):
        a = webhook_verify.body_fingerprint("shopify", self.BODY)
        b = webhook_verify.body_fingerprint("shopify", self.BODY + b" ")
        assert a != b

    def test_scope_and_vendor_are_mixed_in(self):
        base = webhook_verify.body_fingerprint("shopify", self.BODY, scope="orders/paid")
        assert base != webhook_verify.body_fingerprint(
            "shopify", self.BODY, scope="orders/fulfilled"
        )
        assert base != webhook_verify.body_fingerprint(
            "razorpay", self.BODY, scope="orders/paid"
        )

    def test_scope_is_normalised_inside_the_primitive(self):
        """The scope arrives on an unsigned header. Case/whitespace variants
        of the SAME topic must collapse to ONE key, or an attacker mints a
        fresh dedupe key per spelling and replays at will. Normalising in the
        primitive means no future caller can get this wrong."""
        variants = [
            "orders/updated", "Orders/Updated", "ORDERS/UPDATED",
            "orders/Updated", " orders/updated", "orders/updated ",
            "\torders/updated\n",
        ]
        prints = {
            webhook_verify.body_fingerprint("shopify", self.BODY, scope=v)
            for v in variants
        }
        assert len(prints) == 1, f"{len(prints)} distinct keys for one topic"

    def test_vendor_is_normalised_too(self):
        assert webhook_verify.body_fingerprint("shopify", self.BODY) == (
            webhook_verify.body_fingerprint("  Shopify ", self.BODY)
        )

    def test_distinct_topics_still_do_not_collide_after_normalisation(self):
        assert webhook_verify.body_fingerprint(
            "shopify", self.BODY, scope="orders/paid"
        ) != webhook_verify.body_fingerprint(
            "shopify", self.BODY, scope="orders/updated"
        )

    def test_empty_body_does_not_crash(self):
        assert webhook_verify.body_fingerprint("shopify", b"").startswith("sha256:")
        assert webhook_verify.body_fingerprint("shopify", None).startswith("sha256:")  # type: ignore[arg-type]


def test_is_replay_still_available_for_legacy_callers():
    """`is_replay` stays a working pure predicate (it is simply no longer fed
    a business-object timestamp by the receiver)."""
    old = _iso(datetime.now(timezone.utc) - timedelta(seconds=900))
    assert webhook_verify.is_replay(old, window_seconds=300) is True
    assert webhook_verify.is_replay(_iso(datetime.now(timezone.utc))) is False
