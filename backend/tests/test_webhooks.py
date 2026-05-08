"""
IMS 2.0 — Webhook receiver + verifier tests (Phase I-2)
==========================================================
20 cases covering inbound webhooks for Razorpay / Shopify / Shiprocket:

  Pure HMAC verifiers
    - each vendor accepts a correct signature
    - each vendor rejects mutated body
    - each vendor rejects wrong secret
    - each vendor rejects missing header / empty inputs
    - timing-safe comparison is in use (string equality could leak)
    - replay detector flags stale timestamps
    - replay detector handles malformed timestamps gracefully

  Endpoint behaviour
    - razorpay: signed body -> 200 + inbox row + dispatched event
    - shopify:  signed body -> 200 + inbox row + dispatched event
    - shiprocket: signed body -> 200 + inbox row + dispatched event
    - any vendor with bad signature -> 401
    - any vendor with no secret in DB -> 200 skipped (vendor must not retry)
    - inbox doc shape preserved (vendor + payload + processed=false)

  NEXUS consume
    - on event the inbox row's processed flag flips to True
    - missing inbox row → no crash
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# JWT not required (webhook endpoints are unauth) but TestClient still mounts
# the full app, which expects this var.
os.environ.setdefault("JWT_SECRET_KEY", "test_x")

from agents import webhook_verify


# ============================================================================
# Mongo emulator — copied from tests/test_handoffs.py for self-contained
# fixtures. Same shape; we don't need array_filters here.
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

    def find_one(self, filter_=None, projection=None):
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
# Verifier unit tests — pure functions, no fixtures needed
# ============================================================================


class TestRazorpayVerifier:
    SECRET = "rzp_secret_42"
    BODY = b'{"event":"payment.captured","payload":{"amount":15000}}'

    def test_correct_signature_accepted(self):
        sig = _hex_sig(self.BODY, self.SECRET)
        assert webhook_verify.verify_razorpay(self.BODY, sig, self.SECRET) is True

    def test_mutated_body_rejected(self):
        sig = _hex_sig(self.BODY, self.SECRET)
        mutated = self.BODY + b" "
        assert webhook_verify.verify_razorpay(mutated, sig, self.SECRET) is False

    def test_wrong_secret_rejected(self):
        sig = _hex_sig(self.BODY, "wrong_secret")
        assert webhook_verify.verify_razorpay(self.BODY, sig, self.SECRET) is False

    def test_missing_header_returns_false(self):
        assert webhook_verify.verify_razorpay(self.BODY, "", self.SECRET) is False
        assert webhook_verify.verify_razorpay(self.BODY, None, self.SECRET) is False  # type: ignore[arg-type]

    def test_empty_body_returns_false(self):
        sig = _hex_sig(b"", self.SECRET)
        assert webhook_verify.verify_razorpay(b"", sig, self.SECRET) is False

    def test_truncated_signature_does_not_match(self):
        sig = _hex_sig(self.BODY, self.SECRET)
        # Drop the last char — must not pass even if first chars match
        assert webhook_verify.verify_razorpay(self.BODY, sig[:-1], self.SECRET) is False

    def test_uses_constant_time_compare(self):
        """We assert on the implementation choice via spying — if anyone
        ever swaps to == we want this test to scream. We patch
        hmac.compare_digest and verify it gets called."""
        called = {"n": 0}
        real_compare = hmac.compare_digest

        def spy(a, b):
            called["n"] += 1
            return real_compare(a, b)

        from unittest.mock import patch
        with patch.object(webhook_verify.hmac, "compare_digest", side_effect=spy):
            sig = _hex_sig(self.BODY, self.SECRET)
            webhook_verify.verify_razorpay(self.BODY, sig, self.SECRET)
        assert called["n"] >= 1


class TestShopifyVerifier:
    SECRET = "shpfy_42"
    BODY = b'{"id":1234,"line_items":[]}'

    def test_correct_b64_signature_accepted(self):
        sig = _b64_sig(self.BODY, self.SECRET)
        assert webhook_verify.verify_shopify(self.BODY, sig, self.SECRET) is True

    def test_hex_signature_does_not_match_shopify(self):
        # Shopify expects base64 — feeding hex must fail even though the
        # underlying digest was computed correctly.
        sig = _hex_sig(self.BODY, self.SECRET)
        assert webhook_verify.verify_shopify(self.BODY, sig, self.SECRET) is False

    def test_mutated_body_rejected(self):
        sig = _b64_sig(self.BODY, self.SECRET)
        assert webhook_verify.verify_shopify(self.BODY + b"!!", sig, self.SECRET) is False

    def test_wrong_secret_rejected(self):
        sig = _b64_sig(self.BODY, "wrong_secret")
        assert webhook_verify.verify_shopify(self.BODY, sig, self.SECRET) is False

    def test_missing_header_returns_false(self):
        assert webhook_verify.verify_shopify(self.BODY, "", self.SECRET) is False


class TestShiprocketVerifier:
    SECRET = "shrkt_42"
    BODY = b'{"awb":"AWB12345","current_status":"DELIVERED"}'

    def test_correct_signature_accepted(self):
        sig = _hex_sig(self.BODY, self.SECRET)
        assert webhook_verify.verify_shiprocket(self.BODY, sig, self.SECRET) is True

    def test_wrong_secret_rejected(self):
        sig = _hex_sig(self.BODY, "wrong_secret")
        assert webhook_verify.verify_shiprocket(self.BODY, sig, self.SECRET) is False

    def test_garbage_secret_returns_false(self):
        # secret has weird chars / not a string-like — must not crash
        assert webhook_verify.verify_shiprocket(self.BODY, "abc", "") is False
        assert webhook_verify.verify_shiprocket(self.BODY, "abc", None) is False  # type: ignore[arg-type]


class TestReplayDetector:
    def test_recent_event_not_flagged(self):
        ts = datetime.now(timezone.utc).isoformat()
        assert webhook_verify.is_replay(ts) is False

    def test_old_event_flagged(self):
        old = (datetime.now(timezone.utc) - timedelta(seconds=900)).isoformat()
        assert webhook_verify.is_replay(old, window_seconds=300) is True

    def test_event_inside_default_window_ok(self):
        recent = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
        assert webhook_verify.is_replay(recent) is False

    def test_event_with_z_suffix_parsed(self):
        old = (datetime.now(timezone.utc) - timedelta(seconds=900))
        # ISO with trailing Z (Shopify-style)
        s = old.strftime("%Y-%m-%dT%H:%M:%SZ")
        assert webhook_verify.is_replay(s, window_seconds=300) is True

    def test_garbage_timestamp_not_flagged(self):
        # Tolerant: bad input -> False (don't reject good webhooks because
        # the timestamp field is missing or malformed).
        assert webhook_verify.is_replay("not-a-date") is False
        assert webhook_verify.is_replay("") is False
        assert webhook_verify.is_replay(None) is False  # type: ignore[arg-type]

    def test_window_env_override(self, monkeypatch):
        # 60s window — 90s old should be flagged
        monkeypatch.setenv("WEBHOOK_REPLAY_WINDOW_SECONDS", "60")
        old = (datetime.now(timezone.utc) - timedelta(seconds=90)).isoformat()
        assert webhook_verify.is_replay(old) is True


# ============================================================================
# Endpoint tests — wire fakes for DB + dispatch_event
# ============================================================================


@pytest.fixture
def patched_webhooks(monkeypatch):
    """Wire DB + event dispatch fakes for the webhooks router."""
    fake_db = FakeDB()

    # Pre-seed the integrations collection with secrets per vendor
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

    # Patch the router's _get_db helper to return our fake
    from api.routers import webhooks as wh_module
    monkeypatch.setattr(wh_module, "_get_db", lambda: fake_db)

    # Capture dispatched events
    dispatched: List[Dict[str, Any]] = []

    async def fake_dispatch(event, payload, source=""):
        dispatched.append({"event": event, "payload": payload, "source": source})

    # The router does: from agents.registry import dispatch_event
    # Patch that import target.
    import agents.registry as reg
    monkeypatch.setattr(reg, "dispatch_event", fake_dispatch)

    yield {"db": fake_db, "dispatched": dispatched}


def _post_signed(client, path: str, body: bytes, headers: Dict[str, str]):
    return client.post(path, content=body, headers=headers)


# ----- razorpay -----------------------------------------------------------


def test_razorpay_signed_body_returns_200_and_writes_inbox(client, patched_webhooks):
    body = b'{"event":"payment.captured","payload":{"amount":15000}}'
    sig = _hex_sig(body, "rzp_secret_42")
    r = _post_signed(client, "/api/v1/webhooks/razorpay", body,
                     {"X-Razorpay-Signature": sig, "content-type": "application/json"})
    assert r.status_code == 200, r.text
    body_json = r.json()
    assert body_json["status"] == "received"
    assert body_json["vendor"] == "razorpay"
    assert body_json["webhook_id"]

    # Inbox row written
    inbox = patched_webhooks["db"].get_collection("webhook_inbox")
    assert len(inbox.docs) == 1
    row = inbox.docs[0]
    assert row["vendor"] == "razorpay"
    assert row["processed"] is False
    assert row["payload"]["event"] == "payment.captured"
    assert row["webhook_id"] == body_json["webhook_id"]
    assert "received_at" in row

    # Event dispatched with correct shape
    dispatched = patched_webhooks["dispatched"]
    assert len(dispatched) == 1
    assert dispatched[0]["event"] == "webhook.received"
    assert dispatched[0]["payload"]["webhook_id"] == body_json["webhook_id"]
    assert dispatched[0]["payload"]["vendor"] == "razorpay"


def test_razorpay_bad_signature_returns_401(client, patched_webhooks):
    body = b'{"event":"payment.captured"}'
    bad_sig = _hex_sig(body, "completely_wrong")
    r = _post_signed(client, "/api/v1/webhooks/razorpay", body,
                     {"X-Razorpay-Signature": bad_sig, "content-type": "application/json"})
    assert r.status_code == 401
    # Nothing written to inbox
    inbox = patched_webhooks["db"].get_collection("webhook_inbox")
    assert len(inbox.docs) == 0
    # No event dispatched
    assert len(patched_webhooks["dispatched"]) == 0


def test_razorpay_missing_signature_returns_401(client, patched_webhooks):
    body = b'{"event":"payment.captured"}'
    r = _post_signed(client, "/api/v1/webhooks/razorpay", body,
                     {"content-type": "application/json"})
    assert r.status_code == 401


# ----- shopify ------------------------------------------------------------


def test_shopify_signed_body_returns_200_and_dispatches(client, patched_webhooks):
    body = b'{"id":12345,"line_items":[{"id":1,"price":"100"}]}'
    sig = _b64_sig(body, "shpfy_42")
    r = _post_signed(client, "/api/v1/webhooks/shopify", body,
                     {"X-Shopify-Hmac-Sha256": sig,
                      "X-Shopify-Topic": "orders/create",
                      "content-type": "application/json"})
    assert r.status_code == 200, r.text
    assert r.json()["vendor"] == "shopify"
    inbox = patched_webhooks["db"].get_collection("webhook_inbox")
    assert len(inbox.docs) == 1
    row = inbox.docs[0]
    assert row["vendor"] == "shopify"
    assert row["payload"]["id"] == 12345
    # Selected headers preserved
    assert row["headers"].get("x-shopify-topic") == "orders/create"
    # Event dispatched
    assert len(patched_webhooks["dispatched"]) == 1


def test_shopify_bad_signature_returns_401(client, patched_webhooks):
    body = b'{"id":99}'
    sig = _b64_sig(body, "wrong")
    r = _post_signed(client, "/api/v1/webhooks/shopify", body,
                     {"X-Shopify-Hmac-Sha256": sig, "content-type": "application/json"})
    assert r.status_code == 401


# ----- shiprocket ---------------------------------------------------------


def test_shiprocket_signed_body_returns_200_and_dispatches(client, patched_webhooks):
    body = b'{"awb":"AWB12345","current_status":"DELIVERED"}'
    sig = _hex_sig(body, "shrkt_42")
    r = _post_signed(client, "/api/v1/webhooks/shiprocket", body,
                     {"X-Shiprocket-Signature": sig, "content-type": "application/json"})
    assert r.status_code == 200, r.text
    inbox = patched_webhooks["db"].get_collection("webhook_inbox")
    assert len(inbox.docs) == 1
    assert inbox.docs[0]["vendor"] == "shiprocket"
    assert inbox.docs[0]["payload"]["awb"] == "AWB12345"


def test_shiprocket_bad_signature_returns_401(client, patched_webhooks):
    body = b'{"awb":"AWB12345"}'
    sig = _hex_sig(body, "wrong")
    r = _post_signed(client, "/api/v1/webhooks/shiprocket", body,
                     {"X-Shiprocket-Signature": sig, "content-type": "application/json"})
    assert r.status_code == 401


# ----- secret missing  ----------------------------------------------------


def test_no_secret_returns_200_skipped(client, monkeypatch):
    """When webhook_secret isn't configured, return 200 + skipped so the
    vendor doesn't pile up retries. Inbox row NOT written (we never had
    enough trust to store it)."""
    fake_db = FakeDB()  # no integrations seeded
    from api.routers import webhooks as wh_module
    monkeypatch.setattr(wh_module, "_get_db", lambda: fake_db)

    body = b'{"event":"payment.captured"}'
    sig = _hex_sig(body, "doesnt_matter")
    r = client.post("/api/v1/webhooks/razorpay", content=body,
                    headers={"X-Razorpay-Signature": sig, "content-type": "application/json"})
    assert r.status_code == 200
    body_json = r.json()
    assert body_json["status"] == "skipped"
    assert body_json["reason"] == "secret_not_configured"
    inbox = fake_db.get_collection("webhook_inbox")
    assert len(inbox.docs) == 0


# ----- inbox doc shape ----------------------------------------------------


def test_inbox_doc_shape_has_required_fields(client, patched_webhooks):
    body = b'{"event":"payment.captured","payload":{"amount":99}}'
    sig = _hex_sig(body, "rzp_secret_42")
    client.post("/api/v1/webhooks/razorpay", content=body,
                headers={"X-Razorpay-Signature": sig, "content-type": "application/json"})

    inbox = patched_webhooks["db"].get_collection("webhook_inbox")
    assert len(inbox.docs) == 1
    row = inbox.docs[0]
    # Required fields
    for f in ("webhook_id", "vendor", "received_at", "headers", "payload",
              "raw_body_size", "processed", "processed_at"):
        assert f in row, f"missing field: {f}"
    assert row["processed"] is False
    assert row["processed_at"] is None
    assert row["raw_body_size"] == len(body)
    # Payload preserved verbatim
    assert row["payload"]["payload"]["amount"] == 99


# ============================================================================
# NEXUS consumer tests — drives the on_event handler directly with a fake DB
# ============================================================================


def test_nexus_marks_inbox_processed_on_event():
    """Hand NEXUS a webhook.received event; it should look up the inbox
    row, run the handler stub, and flip processed=True."""
    fake_db = FakeDB()
    inbox = fake_db.get_collection("webhook_inbox")

    inbox.insert_one({
        "webhook_id": "wh-1",
        "vendor": "razorpay",
        "received_at": datetime.now(timezone.utc),
        "headers": {},
        "payload": {"event": "payment.captured"},
        "raw_body_size": 42,
        "processed": False,
        "processed_at": None,
    })

    from agents.implementations.nexus import NexusAgent
    nx = NexusAgent(db=fake_db)

    asyncio.run(nx.on_event("webhook.received",
                            {"webhook_id": "wh-1", "vendor": "razorpay"}))

    row = inbox.find_one({"webhook_id": "wh-1"})
    assert row is not None
    assert row["processed"] is True
    assert row["processed_at"] is not None


def test_nexus_handles_missing_inbox_row_gracefully():
    """Race: webhook fired for a row that's been swept already. Don't crash."""
    fake_db = FakeDB()
    from agents.implementations.nexus import NexusAgent
    nx = NexusAgent(db=fake_db)
    # Should not raise
    asyncio.run(nx.on_event("webhook.received",
                            {"webhook_id": "ghost", "vendor": "shopify"}))


def test_nexus_dispatches_per_vendor_handler():
    """Each vendor invokes its dedicated handler stub. We patch the handler
    to capture the payload."""
    fake_db = FakeDB()
    inbox = fake_db.get_collection("webhook_inbox")
    inbox.insert_one({
        "webhook_id": "wh-2",
        "vendor": "shopify",
        "received_at": datetime.now(timezone.utc),
        "headers": {},
        "payload": {"id": 7, "topic": "orders/create"},
        "raw_body_size": 0,
        "processed": False,
        "processed_at": None,
    })

    from agents.implementations.nexus import NexusAgent
    nx = NexusAgent(db=fake_db)

    captured: List[Dict[str, Any]] = []

    async def stub(payload):
        captured.append(payload)

    nx._handle_shopify_webhook = stub  # type: ignore[assignment]

    asyncio.run(nx.on_event("webhook.received",
                            {"webhook_id": "wh-2", "vendor": "shopify"}))

    assert len(captured) == 1
    assert captured[0]["id"] == 7
    # And the row is flagged processed
    row = inbox.find_one({"webhook_id": "wh-2"})
    assert row["processed"] is True
