"""
IMS 2.0 - Webhook hardening tests (security P2)
================================================
Covers the two receiver-level protections added to api/routers/webhooks.py:

  Per-vendor+IP rate limiting (checked FIRST)
    - over-limit POST returns 429 BEFORE any secret lookup (spy on _load_secret)
    - 429 detail leaks nothing about internals
    - limiter isolates vendors (razorpay spam does not block shopify)
    - every receiver (razorpay/shopify/shiprocket/msg91/whatsapp) is covered
    - WEBHOOK_RATE_LIMIT_PER_MIN env override + garbage-value fallback

  Event-id replay dedupe
    - duplicate x-razorpay-event-id -> 200 {"status":"duplicate"},
      single inbox row, no second dispatch
    - duplicate x-shopify-webhook-id -> same
    - DuplicateKeyError race backstop (concurrent worker won the insert)
      -> 200 duplicate, no second dispatch
    - absent event-id header keeps today's behavior (both deliveries ingested)
    - unique partial index (vendor, event_id) is ensured on the inbox

Fixture style mirrors tests/test_webhooks.py (self-contained fakes).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import sys
from typing import Any, Dict, List

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# JWT not required (webhook endpoints are unauth) but TestClient still mounts
# the full app, which expects this var.
os.environ.setdefault("JWT_SECRET_KEY", "test_x")


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
                if op == "$gte" and not (actual is not None and actual >= op_val):
                    return False
                if op == "$lt" and not (actual is not None and actual < op_val):
                    return False
                if op == "$ne" and actual == op_val:
                    return False
                if op == "$exists" and bool(actual is not None) != bool(op_val):
                    return False
                if op == "$in" and actual not in op_val:
                    return False
        else:
            if actual != expected:
                return False
    return True


class _FakeCursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def sort(self, *a, **kw):
        return self

    def limit(self, n):
        self._docs = self._docs[: int(n or 0) or len(self._docs)]
        return self

    def __iter__(self):
        return iter(self._docs)


class FakeCollection:
    def __init__(self):
        self.docs: List[Dict[str, Any]] = []
        self.indexes: List[Any] = []

    def insert_one(self, doc):
        self.docs.append(dict(doc))
        return type("R", (), {"inserted_id": doc.get("webhook_id")})()

    def find_one(self, filter_=None, projection=None, **kwargs):
        for d in self.docs:
            if _doc_matches(d, filter_):
                return d
        return None

    def find(self, filter_=None, projection=None):
        return _FakeCursor(d for d in self.docs if _doc_matches(d, filter_))

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


def _hex_sig(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _b64_sig(body: bytes, secret: str) -> str:
    return base64.b64encode(
        hmac.new(secret.encode(), body, hashlib.sha256).digest()
    ).decode("ascii")


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture(autouse=True)
def _fresh_rate_limit_state():
    """The limiter's sliding window lives in the shared cache singleton
    (in-memory fallback under tests), which outlives any one test. Flush it
    before AND after each test so (a) buckets never leak between tests here
    and (b) stamps recorded by these tests never bleed into other test files
    on the same process (test_webhooks.py runs right after this file)."""
    from api.services.cache import cache

    cache.flush()
    yield
    cache.flush()


@pytest.fixture
def patched_webhooks(monkeypatch):
    """Wire DB + event dispatch fakes for the webhooks router - same shape as
    tests/test_webhooks.py."""
    fake_db = FakeDB()

    integ = fake_db.get_collection("integrations")
    integ.insert_one({"type": "razorpay",
                      "config": {"webhook_secret": "rzp_secret_42"},
                      "enabled": True})
    integ.insert_one({"type": "shopify",
                      "config": {"webhook_secret": "shpfy_42"},
                      "enabled": True})
    integ.insert_one({"type": "shiprocket",
                      "config": {"webhook_secret": "shrkt_42"},
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
# Rate limiting
# ============================================================================


def test_over_limit_returns_429_before_secret_lookup(client, monkeypatch):
    """The limiter fires BEFORE the secret lookup: the over-limit request
    must never reach _load_secret (that Mongo hit is exactly what unsigned
    garbage spam used to cost us)."""
    monkeypatch.setenv("WEBHOOK_RATE_LIMIT_PER_MIN", "1")

    from api.routers import webhooks as wh_module

    fake_db = FakeDB()  # no integrations seeded -> skipped path
    monkeypatch.setattr(wh_module, "_get_db", lambda: fake_db)

    calls = {"n": 0}
    real_load = wh_module._load_secret

    def spy(vendor):
        calls["n"] += 1
        return real_load(vendor)

    monkeypatch.setattr(wh_module, "_load_secret", spy)

    # Request 1: within budget -> proceeds to the secret lookup (skipped).
    r1 = client.post("/api/v1/webhooks/razorpay", content=b'{"x":1}',
                     headers={"content-type": "application/json"})
    assert r1.status_code == 200
    assert calls["n"] == 1

    # Request 2: over budget -> 429 and _load_secret NOT called again.
    r2 = client.post("/api/v1/webhooks/razorpay", content=b'{"x":1}',
                     headers={"content-type": "application/json"})
    assert r2.status_code == 429
    assert calls["n"] == 1, "secret lookup must not run for a rate-limited request"

    # No internals leaked in the 429 body.
    detail = str(r2.json().get("detail", "")).lower()
    for word in ("mongo", "redis", "cache", "integrations", "secret", "hmac", "60"):
        assert word not in detail, f"429 detail leaks internals: {word}"

    # Nothing persisted either way (garbage never got past the secret gate).
    assert len(fake_db.get_collection("webhook_inbox").docs) == 0


def test_limiter_isolates_vendors(client, monkeypatch):
    """Razorpay spam must not block Shopify - buckets are per vendor+IP."""
    monkeypatch.setenv("WEBHOOK_RATE_LIMIT_PER_MIN", "1")

    from api.routers import webhooks as wh_module

    fake_db = FakeDB()
    monkeypatch.setattr(wh_module, "_get_db", lambda: fake_db)

    assert client.post("/api/v1/webhooks/razorpay", content=b"{}",
                       headers={"content-type": "application/json"}).status_code == 200
    assert client.post("/api/v1/webhooks/razorpay", content=b"{}",
                       headers={"content-type": "application/json"}).status_code == 429
    # Shopify is a separate bucket - still admitted.
    r = client.post("/api/v1/webhooks/shopify", content=b"{}",
                    headers={"content-type": "application/json"})
    assert r.status_code == 200


@pytest.mark.parametrize("path", [
    "/api/v1/webhooks/razorpay",
    "/api/v1/webhooks/shopify",
    "/api/v1/webhooks/shiprocket",
    "/api/v1/webhooks/msg91/delivery",
    "/api/v1/webhooks/whatsapp",
])
def test_every_receiver_is_rate_limited(client, monkeypatch, path):
    """All five POST receivers enforce the endpoint-level limiter."""
    monkeypatch.setenv("WEBHOOK_RATE_LIMIT_PER_MIN", "1")

    from api.routers import webhooks as wh_module

    fake_db = FakeDB()
    monkeypatch.setattr(wh_module, "_get_db", lambda: fake_db)

    first = client.post(path, content=b"{}",
                        headers={"content-type": "application/json"})
    assert first.status_code != 429, f"first request to {path} must be admitted"
    second = client.post(path, content=b"{}",
                         headers={"content-type": "application/json"})
    assert second.status_code == 429, f"second request to {path} must be limited"


def test_rate_limit_env_override_and_garbage_fallback(monkeypatch):
    from api.routers import webhooks as wh_module

    monkeypatch.delenv("WEBHOOK_RATE_LIMIT_PER_MIN", raising=False)
    assert wh_module._webhook_rate_limit_per_min() == 60

    monkeypatch.setenv("WEBHOOK_RATE_LIMIT_PER_MIN", "5")
    assert wh_module._webhook_rate_limit_per_min() == 5

    monkeypatch.setenv("WEBHOOK_RATE_LIMIT_PER_MIN", "not-a-number")
    assert wh_module._webhook_rate_limit_per_min() == 60

    # Non-positive values clamp to 1 (never a zero-budget lockout).
    monkeypatch.setenv("WEBHOOK_RATE_LIMIT_PER_MIN", "0")
    assert wh_module._webhook_rate_limit_per_min() == 1


# ============================================================================
# Event-id replay dedupe
# ============================================================================


def _post_razorpay(client, body: bytes, event_id=None):
    headers = {
        "X-Razorpay-Signature": _hex_sig(body, "rzp_secret_42"),
        "content-type": "application/json",
    }
    if event_id:
        headers["X-Razorpay-Event-Id"] = event_id
    return client.post("/api/v1/webhooks/razorpay", content=body, headers=headers)


def test_duplicate_razorpay_event_id_returns_200_duplicate(client, patched_webhooks):
    """A replayed, correctly-signed Razorpay envelope with the same event id
    is ACKed (200 - vendors must get a 2xx or retry forever) but NOT
    re-ingested and NOT re-dispatched."""
    body = b'{"event":"payment.captured","payload":{"amount":15000}}'

    r1 = _post_razorpay(client, body, event_id="evt_dup_1")
    assert r1.status_code == 200, r1.text
    assert r1.json()["status"] == "received"

    r2 = _post_razorpay(client, body, event_id="evt_dup_1")
    assert r2.status_code == 200, r2.text
    assert r2.json()["status"] == "duplicate"

    inbox = patched_webhooks["db"].get_collection("webhook_inbox")
    assert len(inbox.docs) == 1, "replay must not create a second inbox row"
    assert inbox.docs[0]["event_id"] == "evt_dup_1"
    assert len(patched_webhooks["dispatched"]) == 1, "replay must not re-dispatch"


def test_duplicate_shopify_webhook_id_returns_200_duplicate(client, patched_webhooks):
    body = b'{"id":12345,"line_items":[{"id":1,"price":"100"}]}'
    headers = {
        "X-Shopify-Hmac-Sha256": _b64_sig(body, "shpfy_42"),
        "X-Shopify-Webhook-Id": "wh-abc-123",
        "X-Shopify-Topic": "orders/create",
        "content-type": "application/json",
    }

    r1 = client.post("/api/v1/webhooks/shopify", content=body, headers=headers)
    assert r1.status_code == 200, r1.text
    assert r1.json()["status"] == "received"

    r2 = client.post("/api/v1/webhooks/shopify", content=body, headers=headers)
    assert r2.status_code == 200, r2.text
    assert r2.json()["status"] == "duplicate"

    inbox = patched_webhooks["db"].get_collection("webhook_inbox")
    assert len(inbox.docs) == 1
    assert inbox.docs[0]["event_id"] == "wh-abc-123"
    assert len(patched_webhooks["dispatched"]) == 1


def test_duplicate_key_error_race_backstop(client, patched_webhooks, monkeypatch):
    """When a concurrent worker wins the insert between our pre-check and our
    insert, the unique index raises DuplicateKeyError - the receiver must ACK
    (200 duplicate) and not re-dispatch, never 5xx back to the vendor."""
    body = b'{"event":"payment.captured","payload":{"amount":15000}}'

    r1 = _post_razorpay(client, body, event_id="evt_race")
    assert r1.status_code == 200
    assert len(patched_webhooks["dispatched"]) == 1

    inbox = patched_webhooks["db"].get_collection("webhook_inbox")

    # Simulate the race: the pre-check misses (find_one -> None) but the
    # unique index rejects the insert.
    DuplicateKeyError = type("DuplicateKeyError", (Exception,), {})

    def _raise_dup(doc):
        raise DuplicateKeyError("E11000 duplicate key error")

    monkeypatch.setattr(inbox, "find_one", lambda *a, **k: None)
    monkeypatch.setattr(inbox, "insert_one", _raise_dup)

    r2 = _post_razorpay(client, body, event_id="evt_race")
    assert r2.status_code == 200, r2.text
    assert r2.json()["status"] == "duplicate"
    assert len(patched_webhooks["dispatched"]) == 1, "race loser must not dispatch"


def test_absent_event_id_keeps_todays_behavior(client, patched_webhooks):
    """No delivery-id header (e.g. Shiprocket, or a vendor omitting it) ->
    timestamp-window-only cover, exactly as before: both deliveries ingested
    and dispatched."""
    body = b'{"event":"payment.captured","payload":{"amount":99}}'

    r1 = _post_razorpay(client, body)
    r2 = _post_razorpay(client, body)
    assert r1.status_code == 200 and r1.json()["status"] == "received"
    assert r2.status_code == 200 and r2.json()["status"] == "received"

    inbox = patched_webhooks["db"].get_collection("webhook_inbox")
    assert len(inbox.docs) == 2
    assert all(d.get("event_id") is None for d in inbox.docs)
    assert len(patched_webhooks["dispatched"]) == 2


def test_unique_partial_index_is_ensured_on_inbox(client, patched_webhooks):
    """The (vendor, event_id) unique PARTIAL index - the race backstop - is
    created idempotently on the inbox collection (mirrors the
    uniq_shopify_order_id pattern in shopify_ingest)."""
    body = b'{"event":"payment.captured"}'
    _post_razorpay(client, body, event_id="evt_idx")

    inbox = patched_webhooks["db"].get_collection("webhook_inbox")
    matched = [
        (args, kwargs)
        for (args, kwargs) in inbox.indexes
        if kwargs.get("name") == "uniq_webhook_event_id"
    ]
    assert matched, "uniq_webhook_event_id index was not ensured"
    args, kwargs = matched[0]
    assert args[0] == [("vendor", 1), ("event_id", 1)]
    assert kwargs.get("unique") is True
    assert kwargs.get("partialFilterExpression") == {"event_id": {"$type": "string"}}
