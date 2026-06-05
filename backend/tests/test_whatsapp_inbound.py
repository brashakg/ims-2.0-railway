"""
IMS 2.0 - WhatsApp inbound webhook tests (CRM-14)
===================================================
Tests for:
  - GET /webhooks/whatsapp -- Meta verify-token challenge (public)
  - POST /webhooks/whatsapp -- inbound message receive + intent routing
  - Intent detection logic in whatsapp_intents.py (unit tests, no HTTP)
  - Opt-out gate: opted-out phone -> no intent processing
  - Fail-soft: DB absent -> 200 (never 5xx to Meta)
  - Signature: absent/bad signature -> 401 when secret configured;
               skip verify when secret not configured (fail-soft)

All tests run without a real Meta webhook or MongoDB (fake DB / monkeypatch).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
from typing import Any, Dict, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_meta_body(phone: str = "919876543210", text: str = "hello") -> Dict[str, Any]:
    """Minimal Meta webhook body shape."""
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "contacts": [{"profile": {"name": "Test User"}}],
                            "messages": [
                                {
                                    "id": "wamid.abc123",
                                    "from": phone,
                                    "type": "text",
                                    "text": {"body": text},
                                    "timestamp": "1717000000",
                                }
                            ],
                        }
                    }
                ]
            }
        ],
    }


def _sign_body(body_bytes: bytes, secret: str) -> str:
    sig = hmac.new(secret.encode(), body_bytes, hashlib.sha256).hexdigest()
    return f"sha256={sig}"


# ---------------------------------------------------------------------------
# Unit tests: intent detection
# ---------------------------------------------------------------------------


class TestDetectIntent:
    def test_book_keyword(self):
        from api.services.whatsapp_intents import detect_intent

        assert detect_intent("I want to book an eye test") == "BOOK"

    def test_reorder_keyword(self):
        from api.services.whatsapp_intents import detect_intent

        assert detect_intent("I need to reorder my contact lens") == "REORDER"

    def test_agent_keyword(self):
        from api.services.whatsapp_intents import detect_intent

        assert detect_intent("Can I talk to an agent please") == "AGENT"

    def test_opt_out_keyword(self):
        from api.services.whatsapp_intents import detect_intent

        assert detect_intent("STOP") == "OPT_OUT"

    def test_opt_out_stop_case_insensitive(self):
        from api.services.whatsapp_intents import detect_intent

        assert detect_intent("stop") == "OPT_OUT"

    def test_unknown(self):
        from api.services.whatsapp_intents import detect_intent

        assert detect_intent("Hi good morning") == "UNKNOWN"

    def test_button_payload_wins_over_text(self):
        from api.services.whatsapp_intents import detect_intent

        # Text says "hello" but button says BOOK_APPT
        result = detect_intent("hello", button_payload="BOOK_APPT")
        assert result == "BOOK"

    def test_button_reorder(self):
        from api.services.whatsapp_intents import detect_intent

        assert detect_intent("", button_payload="REORDER_CL") == "REORDER"

    def test_empty_text_no_button(self):
        from api.services.whatsapp_intents import detect_intent

        assert detect_intent("") == "UNKNOWN"


# ---------------------------------------------------------------------------
# Unit tests: opt-out helpers
# ---------------------------------------------------------------------------


class TestOptOut:
    def test_opted_out_customer_flag(self):
        from api.services.whatsapp_intents import _is_opted_out

        assert _is_opted_out({"marketing_consent": False}) is True
        assert _is_opted_out({"whatsapp_opted_out": True}) is True
        assert _is_opted_out({"marketing_consent": True}) is False
        assert _is_opted_out(None) is False

    def test_opted_out_none_defaults_to_consented(self):
        from api.services.whatsapp_intents import _is_opted_out

        # Missing consent -> defaults to consented (same as campaigns.py)
        assert _is_opted_out({}) is False

    def test_phone_opted_out_returns_false_when_no_db(self):
        from api.services.whatsapp_intents import _phone_is_opted_out

        with patch("api.services.whatsapp_intents._get_db", return_value=None):
            assert _phone_is_opted_out("9876543210") is False


# ---------------------------------------------------------------------------
# HTTP tests: GET /webhooks/whatsapp (challenge endpoint)
# ---------------------------------------------------------------------------


class TestVerifyChallenge:
    def test_challenge_echoed_no_verify_token_set(self, client):
        """When WABA_VERIFY_TOKEN is not set, challenge is echoed regardless."""
        with patch.dict(os.environ, {"WABA_VERIFY_TOKEN": ""}, clear=False):
            # Need to reload module state -- easier to just call with matching token
            resp = client.get(
                "/api/v1/webhooks/whatsapp",
                params={
                    "hub.mode": "subscribe",
                    "hub.verify_token": "anything",
                    "hub.challenge": "challenge_xyz",
                },
            )
        assert resp.status_code == 200
        assert resp.text == "challenge_xyz"

    def test_bad_hub_mode_returns_400(self, client):
        resp = client.get(
            "/api/v1/webhooks/whatsapp",
            params={
                "hub.mode": "garbage",
                "hub.verify_token": "tok",
                "hub.challenge": "xyz",
            },
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# HTTP tests: POST /webhooks/whatsapp (inbound messages)
# ---------------------------------------------------------------------------


class TestReceiveInbound:
    def _post_message(
        self,
        client,
        text: str = "hello",
        phone: str = "919876543210",
        secret: Optional[str] = None,
        extra_env: Optional[Dict[str, str]] = None,
    ):
        body_bytes = json.dumps(_make_meta_body(phone=phone, text=text)).encode()
        headers: Dict[str, str] = {"Content-Type": "application/json"}
        env = dict(extra_env or {})
        if secret:
            env["WABA_APP_SECRET"] = secret
            sig = _sign_body(body_bytes, secret)
            headers["X-Hub-Signature-256"] = sig
        else:
            env.setdefault("WABA_APP_SECRET", "")  # unset => skip verify

        with patch.dict(os.environ, env, clear=False):
            return client.post(
                "/api/v1/webhooks/whatsapp",
                content=body_bytes,
                headers=headers,
            )

    def test_no_secret_configured_returns_200(self, client):
        """Fail-soft: no WABA_APP_SECRET => accept delivery without verification."""
        with patch.dict(os.environ, {"WABA_APP_SECRET": ""}), patch(
            "api.routers.webhooks._upsert_conversation", return_value=None
        ), patch(
            "api.services.whatsapp_intents.dispatch_intent",
            new=AsyncMock(
                return_value={"intent": "UNKNOWN", "reply_sent": False, "customer_id": None, "opted_out": False}
            ),
        ), patch(
            "api.services.whatsapp_intents._lookup_customer_by_phone", return_value=None
        ):
            resp = client.post(
                "/api/v1/webhooks/whatsapp",
                content=json.dumps(_make_meta_body()).encode(),
                headers={"Content-Type": "application/json"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "received"

    def test_bad_signature_returns_401(self, client):
        """When app secret is configured, bad signature => 401."""
        secret = "mysecret"
        body_bytes = json.dumps(_make_meta_body()).encode()
        with patch.dict(os.environ, {"WABA_APP_SECRET": secret}):
            resp = client.post(
                "/api/v1/webhooks/whatsapp",
                content=body_bytes,
                headers={
                    "Content-Type": "application/json",
                    "X-Hub-Signature-256": "sha256=badhex",
                },
            )
        assert resp.status_code == 401

    def test_missing_signature_returns_401_when_secret_set(self, client):
        """No signature header + secret configured => 401."""
        with patch.dict(os.environ, {"WABA_APP_SECRET": "real_secret"}):
            resp = client.post(
                "/api/v1/webhooks/whatsapp",
                content=json.dumps(_make_meta_body()).encode(),
                headers={"Content-Type": "application/json"},
            )
        assert resp.status_code == 401

    def test_good_signature_returns_200(self, client):
        secret = "test_secret_123"
        body_bytes = json.dumps(_make_meta_body(text="book eye test")).encode()
        sig = _sign_body(body_bytes, secret)

        with patch.dict(os.environ, {"WABA_APP_SECRET": secret}), patch(
            "api.routers.webhooks._upsert_conversation", return_value=None
        ), patch(
            "api.services.whatsapp_intents.dispatch_intent",
            new=AsyncMock(
                return_value={"intent": "BOOK", "reply_sent": True, "customer_id": None, "opted_out": False}
            ),
        ), patch(
            "api.services.whatsapp_intents._lookup_customer_by_phone", return_value=None
        ):
            resp = client.post(
                "/api/v1/webhooks/whatsapp",
                content=body_bytes,
                headers={
                    "Content-Type": "application/json",
                    "X-Hub-Signature-256": sig,
                },
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["messages_processed"] == 1

    def test_db_absent_still_returns_200(self, client):
        """Fail-soft: Mongo down => 200 (not 5xx)."""
        with patch.dict(os.environ, {"WABA_APP_SECRET": ""}), patch(
            "api.routers.webhooks._get_wa_conversations_collection", return_value=None
        ), patch(
            "api.services.whatsapp_intents._get_db", return_value=None
        ), patch(
            "api.services.whatsapp_intents.dispatch_intent",
            new=AsyncMock(
                return_value={"intent": "UNKNOWN", "reply_sent": False, "customer_id": None, "opted_out": False}
            ),
        ), patch(
            "api.services.whatsapp_intents._lookup_customer_by_phone", return_value=None
        ):
            resp = client.post(
                "/api/v1/webhooks/whatsapp",
                content=json.dumps(_make_meta_body()).encode(),
                headers={"Content-Type": "application/json"},
            )
        assert resp.status_code == 200

    def test_status_update_no_messages_returns_200(self, client):
        """A status update (no messages[]) still returns 200, messages_processed=0."""
        status_body = {
            "object": "whatsapp_business_account",
            "entry": [{"changes": [{"value": {"statuses": [{"id": "abc", "status": "delivered"}]}}]}],
        }
        with patch.dict(os.environ, {"WABA_APP_SECRET": ""}):
            resp = client.post(
                "/api/v1/webhooks/whatsapp",
                content=json.dumps(status_body).encode(),
                headers={"Content-Type": "application/json"},
            )
        assert resp.status_code == 200
        assert resp.json()["messages_processed"] == 0


# ---------------------------------------------------------------------------
# Unit tests: dispatch_intent with mocked DB
# ---------------------------------------------------------------------------


class TestDispatchIntent:
    @pytest.mark.asyncio
    async def test_book_intent_creates_follow_up(self):
        from api.services.whatsapp_intents import dispatch_intent

        fake_customer = {"customer_id": "cust1", "name": "Rohan", "mobile": "9876543210"}
        mock_send = AsyncMock(
            return_value=MagicMock(ok=True, status="SIMULATED")
        )

        with patch("api.services.whatsapp_intents._lookup_customer_by_phone", return_value=fake_customer), patch(
            "api.services.whatsapp_intents._phone_is_opted_out", return_value=False
        ), patch(
            "api.services.whatsapp_intents._create_follow_up", return_value="fu1"
        ), patch(
            "agents.providers.send_whatsapp", new=mock_send
        ):
            result = await dispatch_intent(
                phone="919876543210",
                text="I want to book an eye test",
                button_payload=None,
                store_id="STORE1",
            )
        assert result["intent"] == "BOOK"

    @pytest.mark.asyncio
    async def test_reorder_intent_looks_up_cl_order(self):
        from api.services.whatsapp_intents import dispatch_intent

        fake_customer = {"customer_id": "cust2", "name": "Priya", "mobile": "9111222333"}
        mock_send = AsyncMock(return_value=MagicMock(ok=True, status="SIMULATED"))

        with patch("api.services.whatsapp_intents._lookup_customer_by_phone", return_value=fake_customer), patch(
            "api.services.whatsapp_intents._phone_is_opted_out", return_value=False
        ), patch(
            "api.services.whatsapp_intents._get_last_cl_order",
            return_value={"order_number": "ORD-999", "created_at": "2026-01-01"},
        ), patch(
            "api.services.whatsapp_intents._create_follow_up", return_value="fu2"
        ), patch(
            "agents.providers.send_whatsapp", new=mock_send
        ):
            result = await dispatch_intent(
                phone="919111222333",
                text="reorder my lens",
                button_payload=None,
                store_id="STORE1",
            )
        assert result["intent"] == "REORDER"

    @pytest.mark.asyncio
    async def test_agent_intent(self):
        from api.services.whatsapp_intents import dispatch_intent

        mock_send = AsyncMock(return_value=MagicMock(ok=True, status="SIMULATED"))

        with patch("api.services.whatsapp_intents._lookup_customer_by_phone", return_value=None), patch(
            "api.services.whatsapp_intents._phone_is_opted_out", return_value=False
        ), patch(
            "api.services.whatsapp_intents._get_db", return_value=None
        ), patch(
            "agents.providers.send_whatsapp", new=mock_send
        ):
            result = await dispatch_intent(
                phone="919999888877",
                text="I need help please",
                button_payload=None,
                store_id="HQ",
            )
        assert result["intent"] == "AGENT"

    @pytest.mark.asyncio
    async def test_opted_out_phone_blocks_all_processing(self):
        from api.services.whatsapp_intents import dispatch_intent

        with patch("api.services.whatsapp_intents._phone_is_opted_out", return_value=True):
            result = await dispatch_intent(
                phone="919000000001",
                text="book eye test",
                button_payload=None,
                store_id="HQ",
            )
        assert result["opted_out"] is True
        assert result["intent"] == "UNKNOWN"  # short-circuited
        assert result["reply_sent"] is False

    @pytest.mark.asyncio
    async def test_opted_out_customer_doc_blocks_processing(self):
        from api.services.whatsapp_intents import dispatch_intent

        opted_out_customer = {"customer_id": "cx99", "marketing_consent": False}
        mock_send = AsyncMock(return_value=MagicMock(ok=True, status="SIMULATED"))

        with patch("api.services.whatsapp_intents._lookup_customer_by_phone", return_value=opted_out_customer), patch(
            "api.services.whatsapp_intents._phone_is_opted_out", return_value=False
        ), patch(
            "agents.providers.send_whatsapp", new=mock_send
        ):
            result = await dispatch_intent(
                phone="919000000002",
                text="book",
                button_payload=None,
                store_id="HQ",
            )
        assert result["opted_out"] is True
        assert result["reply_sent"] is False
