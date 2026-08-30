"""
Messaging data spine - delivery-report ingestion, short-URL clicks, CTWA
attribution (phase 2 items 1 + 3 + 7, 2026-08-31). Each test dies on its
REQUIREMENT:

  A. POST /api/v1/integrations/msg91/webhooks/{channel} - public + verified
     (HMAC or shared token against the msg91 webhook secret with the
     MSG91_WEBHOOK_TOKEN env fallback), closed channel allowlist, durable
     inbox row, fingerprint dedupe, fail-soft processing.
  B. ONE events rule - both MSG91 receivers (the pre-existing
     /webhooks/msg91/delivery and the new per-channel door) interpret a
     delivery report through message_events.apply_delivery_report: same
     notification_logs advance, same message_events row shape, shared
     dedupe on (channel, provider_message_id, event).
  C. Surfaces - Customer 360 carries message_timeline; the messaging
     preflight carries honest failure counts.
  D. Short-URL wrapping - review-request/recall links wrap via MSG91 ONLY
     when armed (DISPATCH_MODE test/live AND creds); dark or failing =
     byte-identical pass-through; clicks land as event=clicked.
  E. CTWA ads attribution - inbound WhatsApp referral metadata stamps the
     customer record (mobile-primary), first touch write-once, last touch
     always, capture only.

Markers are invented, distinguishable, non-secret strings (ZZ_ prefixes).
No emoji (Windows cp1252).
"""

import asyncio
import hashlib
import hmac
import json
import os
import sys
from typing import Any, Dict, List

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("JWT_SECRET_KEY", "test_x")

import database.connection as db_connection  # noqa: E402
from agents import providers  # noqa: E402
from api.services import message_events  # noqa: E402
from api.services.message_events import (  # noqa: E402
    apply_delivery_report,
    customer_message_timeline,
    failure_counts,
    record_message_event,
    stamp_ctwa_attribution,
)

SECRET = "ZZ_msg91_webhook_secret_42"
BASE = "/api/v1/integrations/msg91/webhooks"


# ---------------------------------------------------------------------------
# Mongo emulator (self-contained, same philosophy as test_webhook_hardening)
# with dotted paths, $or, $exists, $gte, $in, $inc, update_many, sort.
# ---------------------------------------------------------------------------


def _get_path(doc, path):
    cur = doc
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _set_path(doc, path, value):
    parts = path.split(".")
    cur = doc
    for part in parts[:-1]:
        if not isinstance(cur.get(part), dict):
            cur[part] = {}
        cur = cur[part]
    cur[parts[-1]] = value


def _doc_matches(doc, flt):
    if not flt:
        return True
    for k, expected in flt.items():
        if k == "$or":
            if not any(_doc_matches(doc, sub) for sub in expected):
                return False
            continue
        actual = _get_path(doc, k)
        if isinstance(expected, dict) and any(
            str(op).startswith("$") for op in expected
        ):
            for op, opv in expected.items():
                if op == "$exists" and (actual is not None) != bool(opv):
                    return False
                if op == "$gte" and not (actual is not None and actual >= opv):
                    return False
                if op == "$in" and actual not in opv:
                    return False
                if op == "$ne" and actual == opv:
                    return False
                if op == "$type" and not isinstance(actual, str):
                    return False
        else:
            if actual != expected:
                return False
    return True


class _Cursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def sort(self, key, direction=1):
        self._docs.sort(
            key=lambda d: (d.get(key) is None, d.get(key)), reverse=direction == -1
        )
        return self

    def limit(self, n):
        if n:
            self._docs = self._docs[: int(n)]
        return self

    def __iter__(self):
        return iter(dict(d) for d in self._docs)


class FakeCollection:
    def __init__(self):
        self.docs: List[Dict[str, Any]] = []

    def insert_one(self, doc):
        self.docs.append(dict(doc))
        return type("R", (), {"inserted_id": doc.get("_id")})()

    def find_one(self, flt=None, projection=None, **kw):
        for d in self.docs:
            if _doc_matches(d, flt):
                return dict(d)
        return None

    def find(self, flt=None, projection=None):
        return _Cursor(d for d in self.docs if _doc_matches(d, flt))

    def _apply(self, d, update):
        for k, v in (update.get("$set") or {}).items():
            _set_path(d, k, v)
        for k, v in (update.get("$inc") or {}).items():
            _set_path(d, k, (_get_path(d, k) or 0) + v)

    def update_one(self, flt, update, upsert=False):
        for d in self.docs:
            if _doc_matches(d, flt):
                self._apply(d, update)
                return type("R", (), {"modified_count": 1, "matched_count": 1})()
        return type("R", (), {"modified_count": 0, "matched_count": 0})()

    def update_many(self, flt, update):
        n = 0
        for d in self.docs:
            if _doc_matches(d, flt):
                self._apply(d, update)
                n += 1
        return type("R", (), {"modified_count": n, "matched_count": n})()

    def count_documents(self, flt=None):
        return sum(1 for d in self.docs if _doc_matches(d, flt))

    def create_index(self, *a, **kw):
        return None


class FakeDB:
    is_connected = True

    def __init__(self):
        self._colls: Dict[str, FakeCollection] = {}

    def get_collection(self, name):
        if name not in self._colls:
            self._colls[name] = FakeCollection()
        return self._colls[name]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _fresh_rate_limit_state():
    from api.services.cache import cache

    cache.flush()
    yield
    cache.flush()


@pytest.fixture
def spine(monkeypatch):
    """ONE fake Mongo planted behind BOTH lookups the spine uses:
    webhooks._get_db (router) and database.connection.get_db (service)."""
    fake = FakeDB()
    from api.routers import webhooks as wh_module

    monkeypatch.setattr(wh_module, "_get_db", lambda: fake)
    monkeypatch.setattr(db_connection, "get_db", lambda: fake)
    monkeypatch.setenv("MSG91_WEBHOOK_TOKEN", SECRET)
    return fake


def _sig(body: bytes, secret: str = SECRET) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _post(client, channel, payload, headers=None, secret=SECRET):
    body = json.dumps(payload).encode()
    hdrs = {"content-type": "application/json"}
    if headers is None:
        hdrs["x-msg91-signature"] = _sig(body, secret)
    else:
        hdrs.update(headers)
    return client.post(f"{BASE}/{channel}", content=body, headers=hdrs)


def _plant_log(fake, **over):
    row = {
        "notification_id": "NTF-ZZ-1",
        "store_id": "STORE-ZZ-7",
        "customer_id": "CUST-ZZ-1",
        "customer_phone": "+91 9812345678",
        "template_id": "GOOGLE_REVIEW_REQUEST",
        "channel": "WHATSAPP",
        "status": "SENT",
        "provider_msg_id": "REQ-ZZ-A1",
        "delivery_status": "SENT",
    }
    row.update(over)
    fake.get_collection("notification_logs").insert_one(row)
    return row


# ===========================================================================
# A. The per-channel endpoint
# ===========================================================================


def test_unknown_channel_is_404(client, spine):
    r = _post(client, "pigeon", {"request_id": "R", "status": "delivered"})
    assert r.status_code == 404


def test_bad_signature_is_401_and_records_nothing(client, spine):
    body = json.dumps({"request_id": "REQ-ZZ-A1", "status": "delivered"}).encode()
    r = client.post(
        f"{BASE}/whatsapp",
        content=body,
        headers={
            "content-type": "application/json",
            "x-msg91-signature": _sig(body, "ZZ_wrong_secret"),
        },
    )
    assert r.status_code == 401
    assert spine.get_collection("message_events").docs == []
    assert spine.get_collection("webhook_inbox").docs == []


def test_hmac_signature_authenticates(client, spine):
    _plant_log(spine)
    r = _post(client, "whatsapp", {"request_id": "REQ-ZZ-A1", "status": "delivered"})
    assert r.status_code == 200
    assert r.json()["status"] == "received"
    assert r.json()["events_recorded"] == 1


def test_shared_token_authenticates(client, spine):
    _plant_log(spine)
    r = _post(
        client,
        "whatsapp",
        {"request_id": "REQ-ZZ-A1", "status": "delivered"},
        headers={"x-msg91-token": SECRET},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "received"


def test_no_secret_configured_acks_but_processes_nothing(client, spine, monkeypatch):
    """An unverifiable delivery is ACKed (no retry storm) and leaves a
    metadata-only inbox row, but its BODY must never reach the spine."""
    monkeypatch.delenv("MSG91_WEBHOOK_TOKEN", raising=False)
    _plant_log(spine)
    body = json.dumps({"request_id": "REQ-ZZ-A1", "status": "delivered"}).encode()
    r = client.post(
        f"{BASE}/whatsapp", content=body, headers={"content-type": "application/json"}
    )
    assert r.status_code == 200
    assert spine.get_collection("message_events").docs == []
    rows = spine.get_collection("webhook_inbox").docs
    assert len(rows) == 1
    assert rows[0]["skipped_reason"] == "secret_not_configured"
    assert rows[0]["payload"] is None
    # The untouched log row proves nothing was interpreted.
    log = spine.get_collection("notification_logs").docs[0]
    assert log["delivery_status"] == "SENT"


def test_delivered_report_lands_one_enriched_spine_row(client, spine):
    """The keystone row: {channel, provider_message_id, flow_key, store_id,
    mobile, event, at} - flow/store/mobile enriched from the outbound
    notification_logs row the send stamped."""
    _plant_log(spine)
    r = _post(client, "whatsapp", {"request_id": "REQ-ZZ-A1", "status": "delivered"})
    assert r.status_code == 200
    rows = spine.get_collection("message_events").docs
    assert len(rows) == 1
    ev = rows[0]
    assert ev["channel"] == "whatsapp"
    assert ev["provider_message_id"] == "REQ-ZZ-A1"
    assert ev["event"] == "delivered"
    assert ev["flow_key"] == "GOOGLE_REVIEW_REQUEST"
    assert ev["store_id"] == "STORE-ZZ-7"
    assert ev["mobile"] == "9812345678"
    assert ev["at"]
    # And the pre-existing contract still holds: the log row advanced.
    log = spine.get_collection("notification_logs").docs[0]
    assert log["delivery_status"] == "DELIVERED"
    assert log["delivered_at"]


@pytest.mark.parametrize(
    "raw,event",
    [("read", "read"), ("failed", "failed"), ("2", "failed"), ("clicked", "clicked")],
)
def test_each_report_status_maps_to_its_spine_event(client, spine, raw, event):
    _plant_log(spine)
    r = _post(client, "whatsapp", {"request_id": "REQ-ZZ-A1", "status": raw})
    assert r.status_code == 200
    rows = spine.get_collection("message_events").docs
    assert [e["event"] for e in rows] == [event]
    assert rows[0]["raw_status"] == raw


def test_sent_class_status_advances_log_but_is_not_an_event(client, spine):
    """SENT/submitted are outbound states, not spine events - the log row
    advances, message_events stays empty."""
    _plant_log(spine, delivery_status="QUEUED")
    r = _post(client, "whatsapp", {"request_id": "REQ-ZZ-A1", "status": "sent"})
    assert r.status_code == 200
    assert spine.get_collection("message_events").docs == []
    assert spine.get_collection("notification_logs").docs[0]["delivery_status"] == "SENT"


def test_retry_of_same_delivery_never_double_counts(client, spine):
    """MSG91 retries 4-5x on flaky networks: the byte-identical retry ACKs as
    duplicate and the spine still holds ONE row."""
    _plant_log(spine)
    payload = {"request_id": "REQ-ZZ-A1", "status": "delivered"}
    assert _post(client, "whatsapp", payload).json()["status"] == "received"
    r2 = _post(client, "whatsapp", payload)
    assert r2.status_code == 200
    assert r2.json()["status"] == "duplicate"
    assert len(spine.get_collection("message_events").docs) == 1


def test_meta_statuses_envelope_is_understood(client, spine):
    """The WhatsApp channel forwards Meta-cloud envelopes:
    entry[].changes[].value.statuses[]."""
    _plant_log(spine, provider_msg_id="wamid.ZZ-X1")
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "statuses": [
                                {
                                    "id": "wamid.ZZ-X1",
                                    "status": "read",
                                    "recipient_id": "919812345678",
                                    "timestamp": "1756600000",
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }
    r = _post(client, "whatsapp", payload)
    assert r.status_code == 200
    rows = spine.get_collection("message_events").docs
    assert len(rows) == 1
    assert rows[0]["event"] == "read"
    assert rows[0]["provider_message_id"] == "wamid.ZZ-X1"
    assert rows[0]["mobile"] == "9812345678"
    assert rows[0]["at"].startswith("2025-08-31")  # epoch 1756600000 UTC


def test_classic_sms_report_block_is_understood(client, spine):
    """Classic SMS DLR: request block carrying a per-number report list; the
    request id is inherited by each report row."""
    _plant_log(spine, provider_msg_id="REQ-ZZ-SMS1", channel="SMS")
    payload = {
        "requestId": "REQ-ZZ-SMS1",
        "report": [{"status": "1", "number": "919812345678"}],
    }
    r = _post(client, "sms", payload)
    assert r.status_code == 200
    rows = spine.get_collection("message_events").docs
    assert len(rows) == 1
    assert rows[0]["channel"] == "sms"
    assert rows[0]["event"] == "delivered"
    assert rows[0]["provider_message_id"] == "REQ-ZZ-SMS1"


def test_shorturl_click_rides_in_as_clicked_with_url(client, spine):
    _plant_log(spine, provider_msg_id="REQ-ZZ-CLK1")
    r = _post(
        client,
        "shorturl",
        {
            "request_id": "REQ-ZZ-CLK1",
            "status": "clicked",
            "url": "https://zz.example/review",
        },
    )
    assert r.status_code == 200
    rows = spine.get_collection("message_events").docs
    assert len(rows) == 1
    assert rows[0]["event"] == "clicked"
    assert rows[0]["channel"] == "shorturl"
    assert rows[0]["url"] == "https://zz.example/review"


def test_unattributable_channel_is_recorded_as_unknown_not_guessed(spine):
    """A report with no channel hint and no matching outbound row stays
    honest: channel 'unknown'."""
    verdict = record_message_event(
        provider_message_id="REQ-ZZ-ORPHAN", event="delivered"
    )
    assert verdict == "recorded"
    rows = spine.get_collection("message_events").docs
    assert rows[0]["channel"] == "unknown"
    assert rows[0]["flow_key"] is None


# ===========================================================================
# B. ONE events rule - both receivers, one interpreter, one shape
# ===========================================================================


def test_old_delivery_receiver_writes_the_same_spine_row(client, spine):
    """The PRE-EXISTING /webhooks/msg91/delivery door must feed the SAME
    collection through the SAME interpreter. Complete writer enumeration:
    receive_msg91_delivery + receive_msg91_channel -> apply_delivery_report
    -> record_message_event (the only insert)."""
    _plant_log(spine)
    body = json.dumps({"request_id": "REQ-ZZ-A1", "status": "delivered"}).encode()
    r = client.post(
        "/api/v1/webhooks/msg91/delivery",
        content=body,
        headers={
            "content-type": "application/json",
            "x-msg91-signature": _sig(body),
        },
    )
    assert r.status_code == 200
    rows = spine.get_collection("message_events").docs
    assert len(rows) == 1
    ev = rows[0]
    # Same shape and same enrichment as the channel door's row.
    assert ev["event"] == "delivered"
    assert ev["channel"] == "whatsapp"  # enriched from the outbound row
    assert ev["flow_key"] == "GOOGLE_REVIEW_REQUEST"
    assert ev["store_id"] == "STORE-ZZ-7"
    assert ev["mobile"] == "9812345678"
    # And the log advance the old door always did still happened.
    assert spine.get_collection("notification_logs").docs[0][
        "delivery_status"
    ] == "DELIVERED"


def test_both_doors_share_one_dedupe(client, spine):
    """The same delivered report arriving on BOTH doors is ONE event - the
    spine dedupes on (channel, provider_message_id, event) across writers."""
    _plant_log(spine)
    assert (
        _post(
            client, "whatsapp", {"request_id": "REQ-ZZ-A1", "status": "delivered"}
        ).status_code
        == 200
    )
    body = json.dumps({"request_id": "REQ-ZZ-A1", "status": "delivered"}).encode()
    r = client.post(
        "/api/v1/webhooks/msg91/delivery",
        content=body,
        headers={
            "content-type": "application/json",
            "x-msg91-signature": _sig(body),
        },
    )
    assert r.status_code == 200
    assert len(spine.get_collection("message_events").docs) == 1


def test_mobile_is_stored_as_last_10_digit_key(spine):
    """The identity key convention: last 10 digits, same as customers /
    whatsapp_conversations."""
    verdict = record_message_event(
        provider_message_id="REQ-ZZ-M1",
        event="delivered",
        channel="sms",
        mobile="+91 98123-45678",
    )
    assert verdict == "recorded"
    assert spine.get_collection("message_events").docs[0]["mobile"] == "9812345678"


def test_spine_is_fail_soft_when_storage_is_down(monkeypatch):
    monkeypatch.setattr(db_connection, "get_db", lambda: None)
    out = apply_delivery_report(provider_message_id="REQ-ZZ-D1", raw_status="delivered")
    assert out["logs_updated"] == 0
    assert out["event_result"] == "skipped:storage_unavailable"
    assert (
        record_message_event(provider_message_id="REQ-ZZ-D1", event="delivered")
        == "skipped:storage_unavailable"
    )
    assert customer_message_timeline("9812345678") == []
    assert failure_counts() is None


# ===========================================================================
# C. Surfaces - Customer 360 timeline + preflight failure counts
# ===========================================================================


def test_customer_360_response_model_declares_message_timeline():
    """FastAPI strips undeclared response fields: without the model field the
    timeline would silently vanish from the API even if the handler built it."""
    from api.routers.crm import Customer360Response

    assert "message_timeline" in set(Customer360Response.model_fields)


def test_customer_360_carries_the_message_timeline(spine, monkeypatch):
    from api.routers import crm

    for pid, event, at in (
        ("REQ-ZZ-T1", "delivered", "2026-08-30T10:00:00+00:00"),
        ("REQ-ZZ-T2", "clicked", "2026-08-31T10:00:00+00:00"),
    ):
        assert (
            record_message_event(
                provider_message_id=pid,
                event=event,
                channel="whatsapp",
                mobile="9812345678",
                at=at,
            )
            == "recorded"
        )

    class _Stub:
        def query_customer(self, cid):
            return {
                "customer_id": cid,
                "name": "ZZ Customer",
                "mobile": "9812345678",
                "created_at": "2026-01-01T00:00:00",
            }

        def query_customer_orders(self, cid):
            return []

        def query_customer_prescriptions(self, cid):
            return []

        def query_customer_interactions(self, cid, limit=100):
            return []

    monkeypatch.setattr(crm, "db", _Stub())
    out = asyncio.run(
        crm.get_customer_360(customer_id="CUST-ZZ-1", current_user={"user_id": "u1"})
    )
    timeline = out["message_timeline"]
    assert [e["provider_message_id"] for e in timeline] == ["REQ-ZZ-T2", "REQ-ZZ-T1"]
    assert timeline[0]["event"] == "clicked"


def test_preflight_reports_failure_counts_honestly(spine):
    from api.services.integration_status import build_messaging_preflight

    for pid, event in (
        ("REQ-ZZ-F1", "failed"),
        ("REQ-ZZ-F2", "failed"),
        ("REQ-ZZ-F3", "delivered"),
    ):
        assert (
            record_message_event(
                provider_message_id=pid, event=event, channel="whatsapp"
            )
            == "recorded"
        )
    rows = {r["id"]: r for r in build_messaging_preflight(db=spine)["rows"]}
    row = rows["delivery_failures"]
    assert row["ok"] is False
    assert "2 failed of 3" in row["detail"]


def test_preflight_failure_row_is_green_on_a_dark_deploy(spine):
    """Zero events (nothing armed yet) must NOT show red - honest, not
    alarmist."""
    from api.services.integration_status import build_messaging_preflight

    rows = {r["id"]: r for r in build_messaging_preflight(db=spine)["rows"]}
    row = rows["delivery_failures"]
    assert row["ok"] is True
    assert "no delivery reports" in row["detail"]


# ===========================================================================
# D. Short-URL wrapping (dark = byte-identical pass-through)
# ===========================================================================

ORIGINAL_LINK = "https://g.page/r/zz-test/review"
SHORT_LINK = "https://msg91.short/ZZ1"


class _ShortUrlRecorder:
    """httpx.AsyncClient stand-in for the shorturl call."""

    def __init__(self, status_code=200, body=None, out=None):
        self._status = status_code
        self._body = body if body is not None else {"data": {"shortUrl": SHORT_LINK}}
        self.calls = out if out is not None else []

    def client_cls(self):
        rec = self

        class _Resp:
            status_code = rec._status
            text = json.dumps(rec._body)

            def json(self):
                return rec._body

        class _Client:
            def __init__(self, *a, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, url, headers=None, json=None):
                rec.calls.append({"url": url, "headers": headers, "json": json})
                return _Resp()

        return _Client


def _queue_review(**over):
    from api.services.notification_service import send_notification

    kwargs = dict(
        store_id="STORE-ZZ-7",
        customer_id="CUST-ZZ-1",
        customer_phone="9812345678",
        customer_name="ZZ Customer",
        template_id="GOOGLE_REVIEW_REQUEST",
        channel="WHATSAPP",
        variables={"store_name": "ZZ Store", "review_link": ORIGINAL_LINK},
        category="SERVICE",
    )
    kwargs.update(over)
    return asyncio.run(send_notification(**kwargs))


def test_dark_mode_passes_review_link_through_byte_identical(monkeypatch):
    """DISPATCH_MODE=off (the default): no MSG91 call is made and the queued
    message carries the ORIGINAL link byte for byte."""
    monkeypatch.setattr(db_connection, "get_db", lambda: None)
    monkeypatch.setattr(providers, "DISPATCH_MODE", "off")

    def _boom(*a, **kw):
        raise AssertionError("dark mode must never touch httpx")

    monkeypatch.setattr(providers.httpx, "AsyncClient", _boom)
    note = _queue_review()
    assert ORIGINAL_LINK in note["message"]
    assert SHORT_LINK not in note["message"]


def test_armed_mode_wraps_review_link_via_msg91(monkeypatch):
    monkeypatch.setattr(db_connection, "get_db", lambda: None)
    monkeypatch.setattr(providers, "DISPATCH_MODE", "live")
    monkeypatch.setattr(providers, "_msg91", lambda: {"api_key": "ZZ_AUTHKEY_1"})
    rec = _ShortUrlRecorder()
    monkeypatch.setattr(providers.httpx, "AsyncClient", rec.client_cls())
    note = _queue_review()
    assert SHORT_LINK in note["message"]
    assert ORIGINAL_LINK not in note["message"]
    assert len(rec.calls) == 1
    assert rec.calls[0]["json"] == {"url": ORIGINAL_LINK}
    assert rec.calls[0]["headers"]["authkey"] == "ZZ_AUTHKEY_1"


def test_armed_recall_flow_links_wrap_too(monkeypatch):
    monkeypatch.setattr(db_connection, "get_db", lambda: None)
    monkeypatch.setattr(providers, "DISPATCH_MODE", "live")
    monkeypatch.setattr(providers, "_msg91", lambda: {"api_key": "ZZ_AUTHKEY_1"})
    rec = _ShortUrlRecorder()
    monkeypatch.setattr(providers.httpx, "AsyncClient", rec.client_cls())
    note = _queue_review(
        template_id="PRESCRIPTION_EXPIRY",
        variables={
            "store_name": "ZZ Store",
            "expiry_date": "2026-09-30",
            "store_phone": "0651-ZZ",
            "booking_link": ORIGINAL_LINK,
        },
    )
    assert note["status"] == "PENDING"
    assert len(rec.calls) == 1
    assert rec.calls[0]["json"] == {"url": ORIGINAL_LINK}


def test_provider_failure_passes_the_original_link_through(monkeypatch):
    monkeypatch.setattr(db_connection, "get_db", lambda: None)
    monkeypatch.setattr(providers, "DISPATCH_MODE", "live")
    monkeypatch.setattr(providers, "_msg91", lambda: {"api_key": "ZZ_AUTHKEY_1"})
    rec = _ShortUrlRecorder(status_code=500, body={"message": "boom"})
    monkeypatch.setattr(providers.httpx, "AsyncClient", rec.client_cls())
    note = _queue_review()
    assert ORIGINAL_LINK in note["message"]


def test_armed_without_creds_still_passes_through(monkeypatch):
    monkeypatch.setattr(db_connection, "get_db", lambda: None)
    monkeypatch.setattr(providers, "DISPATCH_MODE", "live")
    monkeypatch.setattr(providers, "_msg91", lambda: {"api_key": ""})

    def _boom(*a, **kw):
        raise AssertionError("no creds must mean no httpx call")

    monkeypatch.setattr(providers.httpx, "AsyncClient", _boom)
    note = _queue_review()
    assert ORIGINAL_LINK in note["message"]


def test_flows_outside_the_review_recall_set_are_untouched(monkeypatch):
    """Scope: only review-request + recall flows wrap. An ORDER_DELIVERED
    link rides unmodified even when fully armed."""
    monkeypatch.setattr(db_connection, "get_db", lambda: None)
    monkeypatch.setattr(providers, "DISPATCH_MODE", "live")
    monkeypatch.setattr(providers, "_msg91", lambda: {"api_key": "ZZ_AUTHKEY_1"})

    def _boom(*a, **kw):
        raise AssertionError("out-of-scope flow must never touch httpx")

    monkeypatch.setattr(providers.httpx, "AsyncClient", _boom)
    note = _queue_review(
        template_id="ORDER_DELIVERED",
        variables={"order_number": "ORD-ZZ-1", "store_name": ORIGINAL_LINK},
    )
    assert ORIGINAL_LINK in note["message"]


def test_dark_never_shortens_even_with_the_key_already_pasted_in(monkeypatch):
    """THE arming-runbook state: the owner pastes the MSG91 key into
    Settings (creds are DB-first and live without a redeploy) and has NOT
    yet flipped DISPATCH_MODE. Credentials present + gate dark must still
    mean zero network calls and a byte-identical link.

    The existing dark test plants NO key, so the no-credentials guard
    masks the DISPATCH_MODE guard: deleting the dispatch line left all 37
    tests green (verifier mutation M3, 2026-08-31). This one plants a
    real-looking key so only the dispatch check stands between dark and
    a live call to MSG91."""
    monkeypatch.setattr(providers, "_msg91", lambda: {"api_key": "ZZ_AUTHKEY_1"})

    def _boom(*a, **kw):
        raise AssertionError("dark must never reach the shortener")

    monkeypatch.setattr(providers.httpx, "AsyncClient", _boom)
    for mode in ("off", "", "garbage"):
        monkeypatch.setattr(providers, "DISPATCH_MODE", mode)
        assert asyncio.run(providers.shorten_url(ORIGINAL_LINK)) == ORIGINAL_LINK, (
            f"DISPATCH_MODE={mode!r} with a key present must pass the link"
            " through untouched"
        )


def test_shorten_url_refuses_non_http_input(monkeypatch):
    monkeypatch.setattr(providers, "DISPATCH_MODE", "live")
    monkeypatch.setattr(providers, "_msg91", lambda: {"api_key": "ZZ_AUTHKEY_1"})

    def _boom(*a, **kw):
        raise AssertionError("non-http input must never touch httpx")

    monkeypatch.setattr(providers.httpx, "AsyncClient", _boom)
    assert asyncio.run(providers.shorten_url("not a link")) == "not a link"
    assert asyncio.run(providers.shorten_url("")) == ""


# ===========================================================================
# E. Click-to-WhatsApp ads attribution (capture only)
# ===========================================================================

REFERRAL_1 = {
    "source_url": "https://fb.me/zz-ad-1",
    "source_id": "AD-ZZ-1",
    "source_type": "ad",
    "headline": "ZZ Headline 1",
    "ctwa_clid": "CLID-ZZ-1",
}
REFERRAL_2 = {
    "source_url": "https://fb.me/zz-ad-2",
    "source_id": "AD-ZZ-2",
    "source_type": "ad",
    "headline": "ZZ Headline 2",
    "ctwa_clid": "CLID-ZZ-2",
}


def _ctwa_inbound(referral, mobile="919812345678"):
    return {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "from": mobile,
                                    "referral": referral,
                                    "text": {"body": "hi"},
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }


def _plant_customer(fake, mobile="9812345678"):
    fake.get_collection("customers").insert_one(
        {"customer_id": "CUST-ZZ-1", "mobile": mobile, "name": "ZZ Customer"}
    )


def test_ctwa_referral_on_msg91_inbound_stamps_the_customer(client, spine):
    _plant_customer(spine)
    r = _post(client, "whatsapp", _ctwa_inbound(REFERRAL_1))
    assert r.status_code == 200
    assert r.json()["ctwa_stamped"] == 1
    cust = spine.get_collection("customers").docs[0]
    attribution = cust["ads_attribution"]
    assert attribution["first"]["source_id"] == "AD-ZZ-1"
    assert attribution["last"]["source_id"] == "AD-ZZ-1"
    assert attribution["first"]["source"] == "ctwa"
    assert attribution["touches"] == 1


def test_second_referral_updates_last_touch_and_keeps_first(spine):
    _plant_customer(spine)
    assert stamp_ctwa_attribution("919812345678", REFERRAL_1) is True
    assert stamp_ctwa_attribution("919812345678", REFERRAL_2) is True
    attribution = spine.get_collection("customers").docs[0]["ads_attribution"]
    assert attribution["first"]["source_id"] == "AD-ZZ-1"  # write-once
    assert attribution["last"]["source_id"] == "AD-ZZ-2"  # always updates
    assert attribution["touches"] == 2


def test_referral_without_matching_customer_stamps_nothing(spine):
    assert stamp_ctwa_attribution("919899999999", REFERRAL_1) is False
    assert spine.get_collection("customers").docs == []


def test_referral_junk_keys_are_not_stored(spine):
    """Webhook bodies are untrusted: only the documented referral keys are
    captured, never arbitrary blobs."""
    _plant_customer(spine)
    junk = dict(REFERRAL_1)
    junk["evil_blob"] = "x" * 5000
    assert stamp_ctwa_attribution("919812345678", junk) is True
    first = spine.get_collection("customers").docs[0]["ads_attribution"]["first"]
    assert "evil_blob" not in first


def test_meta_direct_inbound_message_parts_carry_the_referral():
    """The OTHER stamping site: the Meta-direct /webhooks/whatsapp door.
    _extract_message_parts must surface msg.referral so the receiver can
    stamp - without it the Meta door drops CTWA metadata on the floor."""
    from api.routers.webhooks import _extract_message_parts

    parts = _extract_message_parts(_ctwa_inbound(REFERRAL_1))
    assert len(parts) == 1
    assert parts[0]["referral"] == REFERRAL_1
    # And a plain organic message carries referral=None, not a KeyError.
    organic = _ctwa_inbound(REFERRAL_1)
    del organic["entry"][0]["changes"][0]["value"]["messages"][0]["referral"]
    parts = _extract_message_parts(organic)
    assert parts[0]["referral"] is None


# ===========================================================================
# Hygiene
# ===========================================================================


def test_endpoint_responses_never_echo_the_secret(client, spine):
    _plant_log(spine)
    ok = _post(client, "whatsapp", {"request_id": "REQ-ZZ-A1", "status": "delivered"})
    bad = _post(
        client,
        "whatsapp",
        {"request_id": "REQ-ZZ-A1", "status": "delivered"},
        headers={"x-msg91-token": "ZZ_wrong"},
    )
    for resp in (ok, bad):
        assert SECRET not in resp.text
