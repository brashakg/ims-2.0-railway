"""
Do the credentials typed into Settings -> Integrations actually REACH the
sender?
==========================================================================
The bug these guard: the "WhatsApp Business (MSG91)", "Slack" and "Google
PageSpeed" tiles encrypted what you typed into the `integrations` collection
and reported success -- while the code that sends read an os.getenv() captured
at process start and never looked at that collection. The screen said saved;
nothing sent. A test that only asserts "the save returned 200" cannot see
this, so every test here asserts on what the OUTBOUND CALL carried.

Markers are invented non-secret strings, never real credentials, and are only
ever compared -- never logged.

Also locks the boundary: DISPATCH_MODE -- the switch deciding whether IMS may
send at all -- must stay server-side and must NEVER become settable from the
database or a screen.
"""

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import database.connection as db_connection  # noqa: E402
import observability  # noqa: E402
from agents import providers  # noqa: E402
from agents.implementations import pixel  # noqa: E402
from api.services import cred_crypto  # noqa: E402
from api.services.integration_config import get_msg91_config  # noqa: E402

MSG91_ENV = (
    "MSG91_API_KEY",
    "MSG91_WHATSAPP_INTEGRATED_NUMBER",
    "MSG91_SMS_TEMPLATE_ID",
    "MSG91_SENDER",
)


# ---------------------------------------------------------------------------
# Fakes: a Mongo stand-in holding real ENCRYPTED integration docs, and an
# httpx stand-in that records exactly what the provider tried to send.
# ---------------------------------------------------------------------------


class _FakeColl:
    def __init__(self, docs):
        self._docs = docs

    def find_one(self, flt):
        doc = self._docs.get(flt.get("type"))
        if not doc:
            return None
        if flt.get("enabled") is True and not doc.get("enabled"):
            return None
        return dict(doc)


class _FakeDB:
    is_connected = True

    def __init__(self, docs):
        self._coll = _FakeColl(docs)

    def get_collection(self, name):
        return self._coll if name == "integrations" else None


def _saved(monkeypatch, **by_type):
    """Pretend the owner saved these tiles: writes ENCRYPTED docs exactly the
    way PUT /settings/integrations/{type} does, so the decrypt path is real."""
    docs = {
        t: {
            "type": t,
            "enabled": True,
            "config": cred_crypto.encrypt_config(cfg),
        }
        for t, cfg in by_type.items()
    }
    monkeypatch.setattr(db_connection, "get_db", lambda: _FakeDB(docs))


def _no_db(monkeypatch):
    monkeypatch.setattr(db_connection, "get_db", lambda: None)


class _Recorder:
    """Captures the one outbound request a provider makes."""

    def __init__(self):
        self.calls = []

    def client(self, status_code=200, body=None):
        rec = self

        class _Resp:
            def __init__(self):
                self.status_code = status_code
                self.text = ""

            def json(self):
                return body if body is not None else {"request_id": "req-fake"}

        class _Client:
            def __init__(self, *a, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, url, **kw):
                rec.calls.append(dict(kw, url=url))
                return _Resp()

            async def get(self, url, **kw):
                rec.calls.append(dict(kw, url=url))
                return _Resp()

        return _Client


@pytest.fixture
def rec():
    return _Recorder()


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in MSG91_ENV + ("SLACK_WEBHOOK_URL", "PAGESPEED_API_KEY"):
        monkeypatch.delenv(key, raising=False)


# ---------------------------------------------------------------------------
# MSG91 - the credential must reach the wire
# ---------------------------------------------------------------------------


def test_whatsapp_credentials_saved_on_the_screen_reach_the_sender(monkeypatch, rec):
    _saved(
        monkeypatch,
        whatsapp={
            "api_key": "MARKER-AUTHKEY-WA",
            "whatsapp_number": "MARKER-INTEGRATED-NUMBER",
            "sms_template_id": "MARKER-TEMPLATE",
            "sender": "MARKSND",
        },
    )
    monkeypatch.setattr(providers, "DISPATCH_MODE", "live")
    monkeypatch.setattr(providers.httpx, "AsyncClient", rec.client())

    res = asyncio.run(providers.send_whatsapp("+919999999999", "hi", template_id="t1"))

    assert res.status == "SENT", res
    assert len(rec.calls) == 1, "no outbound call was made"
    sent = rec.calls[0]
    # The auth key the owner typed is what MSG91 is handed.
    assert sent["headers"]["authkey"] == "MARKER-AUTHKEY-WA"
    assert sent["json"]["integrated_number"] == "MARKER-INTEGRATED-NUMBER"


def test_sms_template_and_sender_saved_on_the_screen_reach_the_sender(monkeypatch, rec):
    _saved(
        monkeypatch,
        whatsapp={
            "api_key": "MARKER-AUTHKEY-SMS",
            "whatsapp_number": "MARKER-INTEGRATED-NUMBER",
            "sms_template_id": "MARKER-TEMPLATE",
            "sender": "MARKSND",
        },
    )
    monkeypatch.setattr(providers, "DISPATCH_MODE", "live")
    monkeypatch.setattr(providers.httpx, "AsyncClient", rec.client())

    res = asyncio.run(providers.send_sms("+919999999999", "hi"))

    assert res.status == "SENT", res
    sent = rec.calls[0]
    assert sent["headers"]["authkey"] == "MARKER-AUTHKEY-SMS"
    assert sent["json"]["template_id"] == "MARKER-TEMPLATE"
    assert sent["json"]["sender"] == "MARKSND"


def test_a_credential_saved_after_boot_is_picked_up_without_a_restart(monkeypatch, rec):
    """The whole point: the process starts with nothing, the owner saves, the
    very next send must use it. A value captured at import cannot do this."""
    _no_db(monkeypatch)
    assert providers.provider_ready("whatsapp") is False

    _saved(
        monkeypatch,
        whatsapp={"api_key": "MARKER-LATE-KEY", "whatsapp_number": "MARKER-LATE-NUM"},
    )
    assert providers.provider_ready("whatsapp") is True

    monkeypatch.setattr(providers, "DISPATCH_MODE", "live")
    monkeypatch.setattr(providers.httpx, "AsyncClient", rec.client())
    asyncio.run(providers.send_whatsapp("+919999999999", "hi", template_id="t1"))
    assert rec.calls[0]["headers"]["authkey"] == "MARKER-LATE-KEY"


def test_env_vars_still_work_when_nothing_is_saved_in_the_database(monkeypatch, rec):
    """A deployment that only ever set Railway variables must be unaffected."""
    _no_db(monkeypatch)
    monkeypatch.setenv("MSG91_API_KEY", "MARKER-ENV-KEY")
    monkeypatch.setenv("MSG91_WHATSAPP_INTEGRATED_NUMBER", "MARKER-ENV-NUMBER")
    monkeypatch.setenv("MSG91_SMS_TEMPLATE_ID", "MARKER-ENV-TEMPLATE")
    monkeypatch.setattr(providers, "DISPATCH_MODE", "live")
    monkeypatch.setattr(providers.httpx, "AsyncClient", rec.client())

    assert providers.provider_ready("whatsapp") is True
    assert providers.provider_ready("sms") is True

    asyncio.run(providers.send_whatsapp("+919999999999", "hi", template_id="t1"))
    assert rec.calls[0]["headers"]["authkey"] == "MARKER-ENV-KEY"
    assert rec.calls[0]["json"]["integrated_number"] == "MARKER-ENV-NUMBER"


def test_missing_credentials_fail_honestly_and_never_crash(monkeypatch, rec):
    _no_db(monkeypatch)
    monkeypatch.setattr(providers, "DISPATCH_MODE", "live")
    monkeypatch.setattr(providers.httpx, "AsyncClient", rec.client())

    wa = asyncio.run(providers.send_whatsapp("+919999999999", "hi", template_id="t1"))
    sms = asyncio.run(providers.send_sms("+919999999999", "hi"))

    for res in (wa, sms):
        assert res.ok is False
        assert res.status == "FAILED"
        assert "not configured" in (res.error or "")
    assert rec.calls == [], "a send was attempted with no credentials"


# ---------------------------------------------------------------------------
# BOUNDARY - the send switch stays on the server
# ---------------------------------------------------------------------------


def test_dispatch_mode_can_never_come_from_the_database(monkeypatch, rec):
    """A saved integration row must not be able to arm outbound messaging.
    Credentials move to the screen; permission to send does not."""
    _saved(
        monkeypatch,
        whatsapp={
            "api_key": "MARKER-AUTHKEY-WA",
            "whatsapp_number": "MARKER-INTEGRATED-NUMBER",
            # A hostile / careless row trying to arm sending from data:
            "dispatch_mode": "live",
            "enabled": True,
        },
    )
    monkeypatch.setattr(providers, "DISPATCH_MODE", "off")
    monkeypatch.setattr(providers.httpx, "AsyncClient", rec.client())

    res = asyncio.run(providers.send_whatsapp("+919999999999", "hi", template_id="t1"))

    assert res.status == "SIMULATED", res
    assert rec.calls == [], "DISPATCH_MODE=off still sent a real message"
    # And the resolver hands the sender credentials ONLY - no send switch.
    assert set(get_msg91_config()) == {
        "api_key",
        "whatsapp_number",
        "sms_template_id",
        "sender",
    }


# ---------------------------------------------------------------------------
# Slack + PageSpeed
# ---------------------------------------------------------------------------


def test_slack_webhook_saved_on_the_screen_reaches_the_post(monkeypatch, rec):
    _saved(
        monkeypatch, slack={"webhook_url": "https://hooks.slack.invalid/MARKER-HOOK"}
    )
    monkeypatch.setattr(observability.httpx, "AsyncClient", rec.client())

    assert observability.is_slack_configured() is True
    ok = asyncio.run(observability.notify_slack("CRITICAL", "t", "b"))

    assert ok is True
    assert rec.calls[0]["url"] == "https://hooks.slack.invalid/MARKER-HOOK"


def test_slack_stays_a_silent_no_op_when_nothing_is_configured(monkeypatch, rec):
    _no_db(monkeypatch)
    monkeypatch.setattr(observability.httpx, "AsyncClient", rec.client())

    assert observability.is_slack_configured() is False
    assert asyncio.run(observability.notify_slack("CRITICAL", "t", "b")) is False
    assert rec.calls == []


def test_pagespeed_key_saved_on_the_screen_reaches_the_request(monkeypatch, rec):
    _saved(monkeypatch, pagespeed={"api_key": "MARKER-PAGESPEED-KEY"})
    monkeypatch.setattr(pixel.httpx, "AsyncClient", rec.client(body={}))

    assert pixel._is_pagespeed_available() is True
    asyncio.run(pixel._audit_url("https://example.invalid/login"))

    assert ("key", "MARKER-PAGESPEED-KEY") in rec.calls[0]["params"]


# ---------------------------------------------------------------------------
# The Notifications screen must report the truth, and must not hold secrets
# ---------------------------------------------------------------------------


def test_notifications_readout_follows_the_saved_credentials(monkeypatch):
    from api.routers import settings as settings_router

    _saved(
        monkeypatch,
        whatsapp={
            "api_key": "MARKER-AUTHKEY-WA",
            "whatsapp_number": "MARKER-INTEGRATED-NUMBER",
            "sms_template_id": "MARKER-TEMPLATE",
            "sender": "MARKSND",
        },
    )
    resp = asyncio.run(settings_router.get_notification_providers({"roles": ["ADMIN"]}))

    # No MSG91_* env var is set (see _clean_env) - the old readout said False.
    assert resp["whatsapp"]["enabled"] is True
    assert resp["sms"]["enabled"] is True
    assert "MARKER-AUTHKEY-WA" not in repr(resp), "a credential value leaked"


def test_notifications_readout_is_false_when_nothing_is_configured(monkeypatch):
    from api.routers import settings as settings_router

    _no_db(monkeypatch)
    resp = asyncio.run(settings_router.get_notification_providers({"roles": ["ADMIN"]}))
    assert resp["whatsapp"]["enabled"] is False
    assert resp["sms"]["enabled"] is False


def test_notifications_endpoint_refuses_to_store_a_credential(monkeypatch):
    from api.routers import settings as settings_router

    stored = {}

    class _Singleton:
        def update_one(self, flt, update, upsert=False):
            stored.update(update.get("$set", {}))

        def find_one(self, flt):
            return dict(stored, _id="notification_providers")

    monkeypatch.setattr(
        settings_router, "_get_settings_collection", lambda name: _Singleton()
    )
    resp = asyncio.run(
        settings_router.update_notification_providers(
            {
                "provider": "MSG91",
                "api_key": "MARKER-SHOULD-NOT-PERSIST",
                "webhook_url": "https://x.invalid/MARKER",
                "sender_id": "MARKSND",
            },
            {"roles": ["ADMIN"]},
        )
    )

    assert "api_key" not in stored and "webhook_url" not in stored
    assert stored["sender_id"] == "MARKSND"
    assert "MARKER-SHOULD-NOT-PERSIST" not in repr(resp)


# ---------------------------------------------------------------------------
# The SUPERADMIN status screen must not call a wired integration "dormant"
# ---------------------------------------------------------------------------


def test_status_report_sees_credentials_saved_on_the_screen(monkeypatch):
    """integration_status declares WHERE each credential comes from. Once the
    screen is a real source, a saved-but-env-less integration must read as
    configured, or the status screen tells the owner the opposite of the
    truth."""
    from api.services.integration_status import build_integration_status

    db = _FakeDB(
        {
            t: {"type": t, "enabled": True, "config": cred_crypto.encrypt_config(cfg)}
            for t, cfg in {
                "whatsapp": {
                    "api_key": "MARKER-AUTHKEY-WA",
                    "whatsapp_number": "MARKER-INTEGRATED-NUMBER",
                    "sms_template_id": "MARKER-TEMPLATE",
                },
                "pagespeed": {"api_key": "MARKER-PAGESPEED-KEY"},
                "slack": {"webhook_url": "https://hooks.slack.invalid/MARKER-HOOK"},
            }.items()
        }
    )
    by_id = {i["id"]: i for i in build_integration_status(db=db)["integrations"]}

    for key in ("msg91_whatsapp", "msg91_sms", "pagespeed", "slack"):
        assert by_id[key]["configured"] is True, f"{key} reported dormant"
        assert by_id[key]["source"] == "env_or_collection", key
    assert by_id["msg91_whatsapp"]["state"] != "dormant"
