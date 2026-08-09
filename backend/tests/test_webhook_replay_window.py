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


def test_delivery_timestamp_beyond_staleness_cap_is_rejected(
    client, patched_webhooks, monkeypatch
):
    """A delivery whose OWN clock is older than the staleness cap is refused.
    The cap (7 days by default) sits far beyond every vendor retry horizon
    and below the inbox TTL, so nothing legitimate can reach this branch."""
    monkeypatch.setenv("WEBHOOK_DELIVERY_MAX_AGE_SECONDS", "3600")
    body = _old_order_body(days_old=3, order_id=931003)

    r = _post_shopify(
        client,
        body,
        topic="orders/paid",
        webhook_id="wh-stale",
        triggered_at=_iso(datetime.now(timezone.utc) - timedelta(hours=5)),
    )

    assert r.status_code == 200
    assert r.json() == {"status": "skipped", "reason": "delivery_too_old"}
    assert len(patched_webhooks["db"].get_collection("webhook_inbox").docs) == 0
    assert patched_webhooks["dispatched"] == []


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

    def test_beyond_cap_is_stale(self):
        ts = _iso(datetime.now(timezone.utc) - timedelta(days=9))
        assert webhook_verify.is_stale_delivery(ts) is True

    def test_explicit_window_argument_wins(self):
        ts = _iso(datetime.now(timezone.utc) - timedelta(seconds=120))
        assert webhook_verify.is_stale_delivery(ts, window_seconds=60) is True
        assert webhook_verify.is_stale_delivery(ts, window_seconds=600) is False

    def test_missing_or_garbage_timestamp_is_fail_safe(self):
        assert webhook_verify.is_stale_delivery("") is False
        assert webhook_verify.is_stale_delivery(None) is False  # type: ignore[arg-type]
        assert webhook_verify.is_stale_delivery("not-a-date") is False

    def test_epoch_seconds_and_millis_parsed(self):
        old = datetime.now(timezone.utc) - timedelta(days=9)
        assert webhook_verify.is_stale_delivery(str(int(old.timestamp()))) is True
        assert webhook_verify.is_stale_delivery(str(int(old.timestamp() * 1000))) is True
        fresh = datetime.now(timezone.utc)
        assert webhook_verify.is_stale_delivery(str(int(fresh.timestamp()))) is False

    def test_env_override_and_garbage_fallback(self, monkeypatch):
        monkeypatch.delenv("WEBHOOK_DELIVERY_MAX_AGE_SECONDS", raising=False)
        assert webhook_verify.delivery_max_age_seconds() == 7 * 24 * 3600
        monkeypatch.setenv("WEBHOOK_DELIVERY_MAX_AGE_SECONDS", "120")
        assert webhook_verify.delivery_max_age_seconds() == 120
        monkeypatch.setenv("WEBHOOK_DELIVERY_MAX_AGE_SECONDS", "garbage")
        assert webhook_verify.delivery_max_age_seconds() == 7 * 24 * 3600
        # A zero/negative cap would reject everything - never honour it.
        monkeypatch.setenv("WEBHOOK_DELIVERY_MAX_AGE_SECONDS", "0")
        assert webhook_verify.delivery_max_age_seconds() == 7 * 24 * 3600

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

    def test_empty_body_does_not_crash(self):
        assert webhook_verify.body_fingerprint("shopify", b"").startswith("sha256:")
        assert webhook_verify.body_fingerprint("shopify", None).startswith("sha256:")  # type: ignore[arg-type]


def test_is_replay_still_available_for_legacy_callers():
    """`is_replay` stays a working pure predicate (it is simply no longer fed
    a business-object timestamp by the receiver)."""
    old = _iso(datetime.now(timezone.utc) - timedelta(seconds=900))
    assert webhook_verify.is_replay(old, window_seconds=300) is True
    assert webhook_verify.is_replay(_iso(datetime.now(timezone.utc))) is False
