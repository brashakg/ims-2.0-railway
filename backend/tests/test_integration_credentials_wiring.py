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
import importlib
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

    # ORDER_DELIVERED is a registry-seeded flow: the send door refuses
    # unmapped flow keys outright (Coexistence piece 3), so the wiring probes
    # ride a real flow.
    res = asyncio.run(
        providers.send_whatsapp("+919999999999", "hi", template_id="ORDER_DELIVERED")
    )

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
    asyncio.run(
        providers.send_whatsapp("+919999999999", "hi", template_id="ORDER_DELIVERED")
    )
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

    asyncio.run(
        providers.send_whatsapp("+919999999999", "hi", template_id="ORDER_DELIVERED")
    )
    assert rec.calls[0]["headers"]["authkey"] == "MARKER-ENV-KEY"
    assert rec.calls[0]["json"]["integrated_number"] == "MARKER-ENV-NUMBER"


def test_missing_credentials_fail_honestly_and_never_crash(monkeypatch, rec):
    _no_db(monkeypatch)
    monkeypatch.setattr(providers, "DISPATCH_MODE", "live")
    monkeypatch.setattr(providers.httpx, "AsyncClient", rec.client())

    wa = asyncio.run(
        providers.send_whatsapp("+919999999999", "hi", template_id="ORDER_DELIVERED")
    )
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

    res = asyncio.run(
        providers.send_whatsapp("+919999999999", "hi", template_id="ORDER_DELIVERED")
    )

    assert res.status == "SIMULATED", res
    assert rec.calls == [], "DISPATCH_MODE=off still sent a real message"
    # And the resolver hands the sender credentials ONLY - no send switch.
    # (store_numbers is the Coexistence per-store sender map - a credential-
    # shaped field, still no dispatch switch.)
    # (otp_template_id: the MSG91 OTP-flow template for the loyalty-redemption
    # OTP - a template NAME, still no dispatch switch.)
    assert set(get_msg91_config()) == {
        "api_key",
        "whatsapp_number",
        "sms_template_id",
        "otp_template_id",
        "sender",
        "store_numbers",
    }


class _SingletonColl:
    """The notification_providers singleton, holding whatever row we plant."""

    def __init__(self, doc=None):
        self.doc = doc

    def find_one(self, flt):
        return dict(self.doc) if self.doc is not None else None

    def update_one(self, flt, update, upsert=False):
        self.doc = dict(self.doc or {}, **update.get("$set", {}))


def _arm_gate(monkeypatch, raw: str):
    """Arm the server's send gate the way a deploy does -- through the
    environment -- then RE-IMPORT agents.providers so the module-level
    snapshot is produced by whatever parse the module itself performs.

    Nothing here restates that parse. An earlier version hand-copied the
    line-one expression into this fixture, which meant no test using it could
    ever observe a change to the real line -- the fixture was itself a second
    implementation of the gate.
    """
    # Register the pre-test snapshot for teardown FIRST: reload() mutates the
    # module in place, so a setattr recorded after it would "restore" the
    # armed value and leak the gate into later tests.
    monkeypatch.setattr(providers, "DISPATCH_MODE", providers.DISPATCH_MODE)
    monkeypatch.setenv("DISPATCH_MODE", raw)
    importlib.reload(providers)


def _providers_readout(monkeypatch, stored):
    """GET /settings/notifications/providers with `stored` as the saved row."""
    from api.routers import settings as settings_router

    _no_db(monkeypatch)
    coll = _SingletonColl(stored)
    monkeypatch.setattr(settings_router, "_get_settings_collection", lambda name: coll)
    return asyncio.run(settings_router.get_notification_providers({"roles": ["ADMIN"]}))


def test_sending_mode_shown_is_the_servers_gate_not_the_stored_row(monkeypatch):
    """The Sending-mode card must never say OFF while the server is LIVE.

    The screen prints this value beside "this switch is set on the server, on
    purpose, so it can never be flipped by accident from a screen" -- so a
    stored row winning here tells the owner nothing is reaching customers at
    the exact moment everything is.
    """
    _arm_gate(monkeypatch, "live")

    resp = _providers_readout(
        monkeypatch, {"_id": "notification_providers", "dispatch_mode": "off"}
    )

    assert (
        resp["dispatch_mode"] == "live"
    ), "the screen was told the server is dark while it was live"
    # The same value the send path gates on -- not a second reading of it.
    assert resp["dispatch_mode"] == providers.dispatch_mode()
    assert providers._should_dispatch("+919999999999")[0] is True


def test_sending_mode_shown_is_the_gate_in_the_other_direction_too(monkeypatch):
    """And a stored `live` must not promise sends from a dark server."""
    _arm_gate(monkeypatch, "off")

    resp = _providers_readout(
        monkeypatch, {"_id": "notification_providers", "dispatch_mode": "live"}
    )

    assert resp["dispatch_mode"] == "off"
    assert providers._should_dispatch("+919999999999")[0] is False


def test_a_case_typo_in_the_env_var_is_reported_the_way_the_gate_read_it(monkeypatch):
    """DISPATCH_MODE="LIVE" DOES arm the gate -- agents.providers lowercases it
    at import. A second, raw reading of the env var would print "LIVE", which
    the screen does not recognise as sending and captions "Nothing is sent to
    customers yet": the same lie, one case away."""
    _arm_gate(monkeypatch, "LIVE")

    resp = _providers_readout(monkeypatch, None)

    assert resp["dispatch_mode"] == "live"
    assert providers._should_dispatch("+919999999999")[0] is True


def test_a_padded_live_from_the_environment_still_arms_the_gate(monkeypatch):
    """ABSOLUTE probe of the padding policy -- not relative to the gate.

    `DISPATCH_MODE=" live"` is a Railway variable pasted with a leading space.
    Policy: every dispatch gate STRIPS. A stray space must not silently disarm
    all messaging -- and before this was pinned, the identically-padded
    SHOPIFY_DISPATCH_MODE (which already stripped) fired a REAL Shopify write
    while the global gate armed nothing: same rule, two padding policies,
    opposite answers. This test reloads agents.providers with the padded env
    var and asserts the resulting behaviour outright, so it dies if line one
    ever becomes more permissive OR more strict than "strip then lower".
    """
    _arm_gate(monkeypatch, " live")
    assert providers.DISPATCH_MODE == "live"
    assert providers.dispatch_mode() == "live"
    assert providers._should_dispatch("+919999999999")[0] is True

    # Padding never launders garbage into a mode: strip, then the unknown
    # value still defaults to off.
    _arm_gate(monkeypatch, " banana ")
    assert providers._should_dispatch("+919999999999")[0] is False


def test_both_send_gates_share_one_padding_policy(monkeypatch):
    """The identically-padded value must give the identical answer on the
    global gate and the Shopify override gate."""
    from agents import nexus_providers

    _arm_gate(monkeypatch, " live")
    monkeypatch.setenv("SHOPIFY_DISPATCH_MODE", " live")

    assert providers.dispatch_mode() == "live"
    assert nexus_providers.shopify_dispatch_mode() == "live"
    assert providers._should_dispatch("+919999999999")[0] is True
    assert nexus_providers._is_shopify_write_allowed() is True

    # The discriminating half: global gate OFF, override padded. If the
    # override's own strip is removed, " live" becomes unrecognised and
    # shopify_dispatch_mode() falls back to the (stripped) GLOBAL gate --
    # which is off -- so this assertion dies. The symmetric case above
    # cannot see that: both gates armed the same launders the fallback.
    _arm_gate(monkeypatch, "off")
    monkeypatch.setenv("SHOPIFY_DISPATCH_MODE", " live")
    assert providers.dispatch_mode() == "off"
    assert nexus_providers.shopify_dispatch_mode() == "live"
    assert nexus_providers._is_shopify_write_allowed() is True


def test_the_fallback_parses_match_the_gate_when_the_import_actually_fails(monkeypatch):
    """notification_service and shiprocket each carry an `except Exception`
    fallback that re-parses DISPATCH_MODE from the environment. Those are the
    last two independent parses of this env var in the app, and until now no
    test ever forced the import to fail -- so they could drift from the gate
    (one padding policy away) with every suite green."""
    import api.services.notification_service as notification_service
    import api.services.shiprocket as shiprocket

    monkeypatch.setenv("DISPATCH_MODE", " live")
    real_providers = sys.modules["agents.providers"]

    # notification_service: the import happens inside _dispatch_mode(), so
    # poisoning sys.modules flips it onto the fallback for this call only.
    monkeypatch.setitem(sys.modules, "agents.providers", None)
    assert notification_service._dispatch_mode() == "live"

    # shiprocket: its fallback is chosen at module import, so reload it with
    # the import broken, assert, then reload it back onto the real gate.
    try:
        importlib.reload(shiprocket)
        assert shiprocket.dispatch_mode() == "live"
    finally:
        sys.modules["agents.providers"] = real_providers
        importlib.reload(shiprocket)


def test_the_stored_row_still_loses_if_the_key_ever_survives_the_strip(monkeypatch):
    """Second lock, tested alone: the gate is assigned AFTER the stored
    document is merged in. With the strip disarmed -- a future key spelling it
    misses, or someone shrinking the set -- the merge sees `dispatch_mode` and
    must still not win. Fails the moment the assignment moves back above the
    merge."""
    from api.routers import settings as settings_router

    monkeypatch.setattr(settings_router, "_SERVER_OWNED_FIELDS", set())
    _arm_gate(monkeypatch, "live")

    resp = _providers_readout(
        monkeypatch, {"_id": "notification_providers", "dispatch_mode": "off"}
    )

    assert resp["dispatch_mode"] == "live"


def test_a_stored_row_cannot_dress_a_dead_channel_up_as_connected(monkeypatch):
    """Same class, one field over: `enabled` is the answer to "would a send
    work right now", so the singleton must not be able to overwrite it either.
    Nothing is configured here -- the row claims everything is."""
    _arm_gate(monkeypatch, "live")

    resp = _providers_readout(
        monkeypatch,
        {
            "_id": "notification_providers",
            "whatsapp": {"provider": "MSG91", "enabled": True, "sender": "9999999999"},
            "sms": {"provider": "MSG91", "enabled": True, "sender": "MARKSND"},
        },
    )

    assert resp["whatsapp"]["enabled"] is False, "a saved row faked a live channel"
    assert resp["sms"]["enabled"] is False
    assert providers.provider_ready("whatsapp") is False


def test_the_send_gate_cannot_be_written_or_echoed_by_the_screen(monkeypatch):
    """ADMIN can PUT anything; the send switch must not be part of it."""
    from api.routers import settings as settings_router

    coll = _SingletonColl({})
    monkeypatch.setattr(settings_router, "_get_settings_collection", lambda name: coll)
    _arm_gate(monkeypatch, "off")

    resp = asyncio.run(
        settings_router.update_notification_providers(
            {"dispatch_mode": "live", "sender_id": "MARKSND"}, {"roles": ["ADMIN"]}
        )
    )

    assert "dispatch_mode" not in coll.doc, "a screen stored the send gate"
    assert "dispatch_mode" not in resp
    assert coll.doc["sender_id"] == "MARKSND", "a real preference was lost"
    # ...and the readout that follows still reports the server, not the write.
    assert _providers_readout(monkeypatch, coll.doc)["dispatch_mode"] == "off"


def test_every_screen_reports_the_gate_the_send_path_actually_uses(monkeypatch):
    """A DIFFERENTIAL probe, kept as a test.

    "What is the send gate" is answered by three endpoints that reach a
    screen. Feed all three the same environment and diff the answers against
    the gate itself. `DISPATCH_MODE=" live"` -- a Railway variable pasted with
    a leading space -- is the input that used to split them: agents.providers
    rejected it and sent nothing, while the SUPERADMIN Integrations status
    screen re-parsed the env with a .strip() of its own and reported "live".
    Anything that reintroduces a second parse fails here.
    """
    from api.routers import settings as settings_router
    from api.services.integration_status import build_integration_status

    _no_db(monkeypatch)
    coll = _SingletonColl(None)
    monkeypatch.setattr(settings_router, "_get_settings_collection", lambda name: coll)

    # Every input on which two hand-written parses of this env var can split:
    # padding (a .strip() one side has and the other does not) and an empty
    # value (an `or "off"` default one side applies and the other does not).
    for raw in (
        "off",
        "live",
        "LIVE",
        " live",
        "live ",
        "\tlive",
        "",
        "test",
        " test ",
    ):
        _arm_gate(monkeypatch, raw)
        gate = providers.dispatch_mode()
        readouts = {
            "GET /settings/notifications/providers": asyncio.run(
                settings_router.get_notification_providers({"roles": ["ADMIN"]})
            )["dispatch_mode"],
            "POST /settings/integrations/{type}/test": asyncio.run(
                settings_router.test_integration("whatsapp", {"roles": ["ADMIN"]})
            )["dispatch_mode"],
            "integration status report": build_integration_status(db=None)[
                "dispatch_mode"
            ],
        }

        assert set(readouts.values()) == {gate}, (raw, gate, readouts)
        # ...and the answer they agree on matches what the send path DOES, so
        # the caption every screen prints under it is true.
        assert providers._should_dispatch("+919999999999")[0] is (gate == "live"), raw


# ---------------------------------------------------------------------------
# The credential-echo lock, on the READ side
# ---------------------------------------------------------------------------
# Both tests below plant the secret BEHIND the write path, which is the only
# way a legacy row exists. The guard above them cannot do this: its fake
# find_one hands back what the WRITE strip already cleaned, so the READ strip
# is never asked a question and a mutant that deletes it survives. This is not
# hypothetical -- origin/main's PUT has no strip at all, and the Notification
# screen this branch deletes used to PUT its own `api_key` box into this
# singleton in plain text, so a live row plausibly still holds one.


def test_a_legacy_plaintext_key_in_the_stored_row_never_reaches_the_readout(
    monkeypatch,
):
    """GET must strip what it found, not just what it would have stored."""
    stored = {
        "_id": "notification_providers",
        "api_key": "MARKER-LEGACY",
        "sender_id": "MARKSND",
    }

    resp = _providers_readout(monkeypatch, stored)

    assert "api_key" not in resp
    assert "MARKER-LEGACY" not in repr(resp), resp
    # ...and the strip took the secret only, not the row.
    assert resp["sender_id"] == "MARKSND", "a real preference was lost"


def test_a_later_save_never_echoes_a_legacy_plaintext_key_back(monkeypatch):
    """PUT returns the whole re-read document. An ADMIN saving an unrelated
    preference on top of a legacy row must not get the old secret in the
    response body."""
    from api.routers import settings as settings_router

    coll = _SingletonColl({"_id": "notification_providers", "api_key": "MARKER-LEGACY"})
    monkeypatch.setattr(settings_router, "_get_settings_collection", lambda name: coll)

    resp = asyncio.run(
        settings_router.update_notification_providers(
            {"sender_id": "MARKSND"}, {"roles": ["ADMIN"]}
        )
    )

    # The fixture must still be holding the secret, or this proves nothing:
    # the row is what a legacy document looks like AFTER an unrelated save.
    assert coll.doc["api_key"] == "MARKER-LEGACY", coll.doc
    assert "api_key" not in resp
    assert "MARKER-LEGACY" not in repr(resp), resp
    assert resp["sender_id"] == "MARKSND", "a real preference was lost"


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
    # PIXEL resolves the key ONCE per run and hands it to each call, so this
    # asserts the same boundary: whatever _pagespeed_key() returns is what
    # rides on the wire.
    asyncio.run(pixel._audit_url("https://example.invalid/login", pixel._pagespeed_key()))

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


def test_no_spelling_of_a_secret_or_the_gate_survives_the_strip(monkeypatch):
    """The strip must be spelling-insensitive, not a snake_case string match.

    The frontend's response aliasing (client.ts addCamelAliases) makes
    camelCase the canonical client-side spelling of every snake_case field --
    and a planted `dispatchMode` WINS over the alias of the true one, because
    the aliaser never overwrites an existing key. So `apiKey` used to land, in
    plain text, in the unencrypted notification_providers singleton and be
    echoed back by PUT and GET; `dispatchMode: "live"` rode the same hole into
    the client-side readout. PUT every spelling a client can produce and
    assert none is stored, none is echoed, and none wins the readout.
    """
    from api.routers import settings as settings_router

    _no_db(monkeypatch)
    coll = _SingletonColl({})
    monkeypatch.setattr(settings_router, "_get_settings_collection", lambda name: coll)
    _arm_gate(monkeypatch, "off")

    put_echo = asyncio.run(
        settings_router.update_notification_providers(
            {
                "apiKey": "MARKER-CAMEL-SECRET",
                "API_KEY": "MARKER-UPPER-SECRET",
                "api_key": "MARKER-SNAKE-SECRET",
                "webhookUrl": "https://x.invalid/MARKER-CAMEL-HOOK",
                "dispatchMode": "live",
                "DISPATCH_MODE": "live",
                "dispatch_mode": "live",
                "dispatch_mode ": "live",
                "sender_id": "MARKSND",
            },
            {"roles": ["ADMIN"]},
        )
    )
    get_resp = asyncio.run(
        settings_router.get_notification_providers({"roles": ["ADMIN"]})
    )

    forbidden_canon = {"apikey", "webhookurl", "dispatchmode"}
    for name, keys in (
        ("stored", coll.doc.keys()),
        ("PUT echo", put_echo.keys()),
    ):
        hit = {k for k in keys if cred_crypto.canon_field(k) in forbidden_canon}
        assert not hit, f"{name} carries a forbidden spelling: {sorted(hit)}"
    # GET may (and must) carry exactly ONE dispatch key: the server-computed
    # snake_case one, reporting the gate -- never the planted "live".
    gate_keys = {k for k in get_resp if cred_crypto.canon_field(k) == "dispatchmode"}
    assert gate_keys == {"dispatch_mode"}, gate_keys
    assert get_resp["dispatch_mode"] == "off", "a planted spelling won the readout"
    assert not {
        k for k in get_resp if cred_crypto.canon_field(k) in {"apikey", "webhookurl"}
    }
    # No secret VALUE leaks under any key either.
    assert "MARKER-CAMEL-SECRET" not in repr(get_resp) + repr(put_echo) + repr(coll.doc)
    # The real preference still lands.
    assert coll.doc["sender_id"] == "MARKSND"


def test_a_camel_spelled_secret_is_encrypted_and_masked_at_rest():
    """Same spelling hole, one collection over: cred_crypto keys the at-rest
    encryption and the response mask off the field NAME, so `apiKey` on
    PUT /settings/integrations/{type} used to be stored unencrypted and
    returned unmasked. Membership must be spelling-insensitive there too."""
    for spelling in ("apiKey", "API_KEY", "api_key "):
        enc = cred_crypto.encrypt_config({spelling: "MARKER-SECRET-VALUE"})
        assert enc[spelling].startswith("fernet:"), spelling
        masked = cred_crypto.mask_config({spelling: "MARKER-SECRET-VALUE"})
        assert "MARKER-SECRET-VALUE" not in repr(masked), spelling


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
