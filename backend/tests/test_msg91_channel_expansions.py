"""
MSG91 channel expansions - SMS fallback, email transport + validation, voice
escalation (phase 2 items 2 + 4 + 5, 2026-08-31). Builds on the messaging
data spine (message_events). Each test dies on its REQUIREMENT:

  A. SMS fallback on failed WhatsApp - a FAILED delivery report for a
     utility-class flow (order-ready / recall / workshop-ready) queues ONE
     DLT SMS via the existing send door; the per-flow DLT template id lives
     in the template registry (sms_* sibling of the wa_* fields), never env;
     dedupe_ref per provider_message_id means one failure can never fan out;
     marketing/auth flows and unmapped flows refuse honestly; dark = nothing.
  B. Email as a TRANSPORT - send_email speaks the MSG91 email API shape and
     is SIMULATED-provable dark; validation is validate-on-capture in the
     customer door (FLAG, NEVER BLOCK) plus a batch job over the existing
     list, both spending NOTHING until DISPATCH_MODE arms.
  C. Voice escalation rung - a P1 SYSTEM task past its ack window unacked
     gets ONE TTS call to the store manager (policy msg.voice_escalation,
     registered, default off; atomic one-call-per-task claim; SIMULATED
     dark); IVR press-1 acknowledges via the spine voice webhook with an
     audit row naming the channel.

Markers are invented, distinguishable, non-secret strings (ZZ_ prefixes).
No emoji (Windows cp1252).
"""

import asyncio
import json
import os
import sys
from typing import Any

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("JWT_SECRET_KEY", "test_x")

import database.connection as db_connection  # noqa: E402
from agents import providers  # noqa: E402
from api.services.message_events import apply_delivery_report  # noqa: E402
from api.services.notification_service import queue_sms_fallback  # noqa: E402
from api.services.voice_escalation import (  # noqa: E402
    apply_voice_acks,
    maybe_voice_escalate,
)
from tests.test_message_events_spine import (  # noqa: E402
    SECRET,
    FakeCollection,
    FakeDB,
    _post,
)

# ---------------------------------------------------------------------------
# Shared plumbing
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _fresh_rate_limit_state():
    from api.services.cache import cache

    cache.flush()
    yield
    cache.flush()


@pytest.fixture
def fake(monkeypatch):
    """ONE fake Mongo planted behind every lookup these features use:
    database.connection.get_db (message_events / voice_escalation /
    notification_templates read the connection; notification_service reads
    .db off it) and the webhook router's _get_db."""
    db = FakeDB()
    db.db = db  # notification_service._get_db does get_db().db
    from api.routers import webhooks as wh_module

    monkeypatch.setattr(db_connection, "get_db", lambda: db)
    monkeypatch.setattr(wh_module, "_get_db", lambda: db)
    monkeypatch.setenv("MSG91_WEBHOOK_TOKEN", SECRET)
    return db


class _Recorder:
    """httpx.AsyncClient stand-in that records every POST (kwargs + url)."""

    def __init__(self):
        self.calls = []

    def client(self, status_code=200, body=None):
        rec = self

        class _Resp:
            def __init__(self):
                self.status_code = status_code
                self.text = ""

            def json(self):
                return body if body is not None else {"request_id": "ZZ-REQ-FAKE"}

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

        return _Client


@pytest.fixture
def rec():
    return _Recorder()


class _PoisonedClient:
    """AsyncClient that FAILS the test if anything touches the network."""

    def __init__(self, *a, **kw):
        raise AssertionError("network call attempted while dark - spend leak")


def _creds(**over):
    base = {
        "api_key": "ZZ-AUTHKEY",
        "whatsapp_number": "917000000009",
        "sms_template_id": "ZZ_DLT_DEFAULT_TPL",
        "sender": "BVOPTL",
    }
    base.update(over)
    return base


def _plant_wa_log(db, **over):
    row = {
        "notification_id": "NTF-ZZ-WA1",
        "store_id": "BV-RAN-01",
        "customer_id": "CUST-ZZ-9",
        "customer_phone": "919876500001",
        "customer_name": "ZZ Grandpa",
        "template_id": "ORDER_DELIVERED",
        "channel": "WHATSAPP",
        "status": "SENT",
        "provider_msg_id": "REQ-ZZ-FAIL-1",
        "provider_id": "REQ-ZZ-FAIL-1",
        "message": "ZZ your order is ready at the store",
        "category": "SERVICE",
    }
    row.update(over)
    db.get_collection("notification_logs").insert_one(row)
    return row


def _plant_sms_mapping(db, flow_key="ORDER_DELIVERED", dlt="ZZ_DLT_ORDER_READY_1"):
    db.get_collection("notification_templates").insert_one(
        {"template_id": flow_key, "sms_template_id": dlt}
    )


def _sms_rows(db):
    return [
        r
        for r in db.get_collection("notification_logs").docs
        if r.get("triggered_by") == "sms_fallback"
    ]


# ===========================================================================
# A. SMS fallback on failed WhatsApp (item 2)
# ===========================================================================


def test_failed_whatsapp_utility_flow_queues_exactly_one_dlt_sms(fake):
    """THE requirement: a FAILED delivery report for an order-ready WhatsApp
    queues ONE SMS row for the SAME flow, carrying the registry's DLT
    template id and a dedupe_ref bound to the provider_message_id."""
    _plant_wa_log(fake)
    _plant_sms_mapping(fake)

    out = apply_delivery_report(
        provider_message_id="REQ-ZZ-FAIL-1",
        raw_status="failed",
        channel="whatsapp",
        db=fake,
    )
    assert out["sms_fallback"] == "queued", out

    rows = _sms_rows(fake)
    assert len(rows) == 1, rows
    sms = rows[0]
    assert sms["dlt_template_id"] == "ZZ_DLT_ORDER_READY_1"
    assert sms["dedupe_ref"] == "sms_fallback:REQ-ZZ-FAIL-1"
    assert sms["status"] == "PENDING"  # queued, honestly NOT sent
    assert sms["template_id"] == "ORDER_DELIVERED"
    assert sms["customer_phone"] == "919876500001"
    assert sms["message"] == "ZZ your order is ready at the store"
    assert sms["triggered_by"] == "sms_fallback"


def test_provider_retry_of_the_same_failure_never_fans_out(fake):
    """MSG91 retries webhooks 4-5 times. The spine's per-event dedupe must
    absorb the retry BEFORE the fallback hook - one failure, one SMS, ever."""
    _plant_wa_log(fake)
    _plant_sms_mapping(fake)

    first = apply_delivery_report(
        provider_message_id="REQ-ZZ-FAIL-1",
        raw_status="failed",
        channel="whatsapp",
        db=fake,
    )
    second = apply_delivery_report(
        provider_message_id="REQ-ZZ-FAIL-1",
        raw_status="failed",
        channel="whatsapp",
        db=fake,
    )
    assert first["sms_fallback"] == "queued"
    assert second["event_result"] == "duplicate"
    assert "sms_fallback" not in second, second
    assert len(_sms_rows(fake)) == 1


def test_direct_double_call_is_deduped_by_dedupe_ref(fake):
    """Belt and braces: even calling the fallback door twice for the same
    provider_message_id (a second webhook route, a manual replay) queues
    nothing the second time."""
    _plant_wa_log(fake)
    _plant_sms_mapping(fake)

    assert queue_sms_fallback("REQ-ZZ-FAIL-1", db=fake) == "queued"
    assert queue_sms_fallback("REQ-ZZ-FAIL-1", db=fake) == "duplicate"
    assert len(_sms_rows(fake)) == 1


def test_marketing_flow_never_falls_back_to_sms(fake):
    """A failed birthday blast stays failed - utility-class flows ONLY."""
    _plant_wa_log(
        fake,
        template_id="BIRTHDAY_WISH",
        provider_msg_id="REQ-ZZ-BDAY-1",
        provider_id="REQ-ZZ-BDAY-1",
    )
    _plant_sms_mapping(fake, flow_key="BIRTHDAY_WISH", dlt="ZZ_DLT_BDAY")

    out = apply_delivery_report(
        provider_message_id="REQ-ZZ-BDAY-1",
        raw_status="failed",
        channel="whatsapp",
        db=fake,
    )
    assert out["sms_fallback"] == "skipped:not_a_fallback_flow"
    assert _sms_rows(fake) == []


def test_unmapped_flow_refuses_honestly_no_guessed_template(fake):
    """No sms_template_id in the registry = no fallback. A DLT id is a real
    operator registration; the code must never send under a guessed one."""
    _plant_wa_log(fake, template_id="WORKSHOP_READY")
    # NO registry mapping planted on purpose.

    out = apply_delivery_report(
        provider_message_id="REQ-ZZ-FAIL-1",
        raw_status="failed",
        channel="whatsapp",
        db=fake,
    )
    assert out["sms_fallback"] == "skipped:no_sms_template"
    assert _sms_rows(fake) == []


def test_failed_sms_never_triggers_a_fallback(fake):
    """Only a failed WHATSAPP falls back - a failed SMS must not re-queue
    itself (infinite fallback loop)."""
    _plant_wa_log(
        fake,
        channel="SMS",
        provider_msg_id="REQ-ZZ-SMS-1",
        provider_id="REQ-ZZ-SMS-1",
    )
    _plant_sms_mapping(fake)

    out = apply_delivery_report(
        provider_message_id="REQ-ZZ-SMS-1",
        raw_status="failed",
        channel="sms",
        db=fake,
    )
    assert out["sms_fallback"] == "skipped:not_whatsapp"
    assert _sms_rows(fake) == []


def test_send_sms_uses_the_per_row_dlt_template_and_default_when_absent(
    monkeypatch, rec
):
    """The send door: a queued fallback row's dlt_template_id must reach the
    MSG91 payload verbatim; a row without one keeps today's single default."""
    monkeypatch.setattr(providers, "_msg91", _creds)
    monkeypatch.setattr(providers, "DISPATCH_MODE", "live")
    monkeypatch.setattr(providers.httpx, "AsyncClient", rec.client())

    asyncio.run(
        providers.send_sms(
            "919812345678", "hello", dlt_template_id="ZZ_DLT_OVERRIDE_TPL"
        )
    )
    asyncio.run(providers.send_sms("919812345678", "hello"))

    assert rec.calls[0]["json"]["template_id"] == "ZZ_DLT_OVERRIDE_TPL"
    assert rec.calls[1]["json"]["template_id"] == "ZZ_DLT_DEFAULT_TPL"


def test_send_sms_dark_simulates_and_proves_the_resolved_template(monkeypatch):
    """Dark deploy: SIMULATED, no network, and meta names the exact DLT
    template a real send WOULD use - the payload shape is provable unarmed."""
    monkeypatch.setattr(providers, "_msg91", _creds)
    monkeypatch.setattr(providers, "DISPATCH_MODE", "off")
    monkeypatch.setattr(providers.httpx, "AsyncClient", _PoisonedClient)

    res = asyncio.run(
        providers.send_sms(
            "919812345678", "hello", dlt_template_id="ZZ_DLT_OVERRIDE_TPL"
        )
    )
    assert res.status == "SIMULATED"
    assert res.meta["dlt_template_id"] == "ZZ_DLT_OVERRIDE_TPL"


def test_megaphone_drain_passes_the_rows_dlt_template_to_the_send_door(
    monkeypatch,
):
    """The drain is the only place queued rows become sends: it must hand the
    fallback row's dlt_template_id to send_sms (None for every legacy row)."""
    import agents.implementations.megaphone as mega_mod

    seen = []

    async def _spy(phone, message, **kw):
        seen.append(kw)
        return providers.DispatchResult(ok=True, status="SIMULATED", channel="sms")

    monkeypatch.setattr(mega_mod, "send_sms", _spy)

    class _NotifColl:
        def __init__(self, rows):
            self.rows = rows

        def find(self, flt):
            class _Cur:
                def __init__(self, r):
                    self._r = r

                def limit(self, n):
                    return self._r[:n]

            return _Cur(self.rows)

        def update_one(self, flt, update):
            return None

    rows = [
        {
            "notification_id": "N-ZZ-1",
            "status": "PENDING",
            "channel": "SMS",
            "customer_phone": "919000000001",
            "message": "m1",
            "template_id": "ORDER_DELIVERED",
            "dlt_template_id": "ZZ_DLT_ORDER_READY_1",
            "scheduled_for": None,
        },
        {
            "notification_id": "N-ZZ-2",
            "status": "PENDING",
            "channel": "SMS",
            "customer_phone": "919000000002",
            "message": "m2",
            "template_id": "ORDER_DELIVERED",
            "scheduled_for": None,
        },
    ]
    agent = mega_mod.MegaphoneAgent(db=None)
    asyncio.run(agent._drain_pending(_NotifColl(rows)))

    assert [k.get("dlt_template_id") for k in seen] == [
        "ZZ_DLT_ORDER_READY_1",
        None,
    ]


def test_template_save_without_sms_field_cannot_wipe_a_stored_mapping():
    """The enable toggle PUTs the base shape only; a $set built from it must
    drop an absent sms_template_id instead of overwriting the stored one with
    None (same drop-None rule the wa_* fields already follow)."""
    from api.routers.settings import NotificationTemplate, _template_payload

    base = dict(
        template_id="ORDER_DELIVERED",
        template_type="WHATSAPP",
        trigger_event="ORDER_DELIVERED",
        is_enabled=True,
        content="x",
    )
    without = _template_payload(NotificationTemplate(**base))
    assert "sms_template_id" not in without

    with_field = _template_payload(
        NotificationTemplate(**base, sms_template_id="ZZ_DLT_ORDER_READY_1")
    )
    assert with_field["sms_template_id"] == "ZZ_DLT_ORDER_READY_1"


# ===========================================================================
# A2. WORKSHOP_READY direct send becomes matchable (fallback needs a row)
# ===========================================================================


def _job():
    return {
        "job_id": "JOB-ZZ-1",
        "job_number": "WJ-ZZ-77",
        "store_id": "BV-RAN-01",
        "customer_id": "CUST-ZZ-9",
        "customer_name": "ZZ Grandpa",
        "customer_phone": "919876500001",
    }


def test_workshop_ready_sent_logs_a_matchable_notification_row(
    fake, monkeypatch
):
    """A REAL workshop-ready send must leave a notification_logs row carrying
    its provider id - without it, a FAILED delivery report matches nothing
    and the workshop-ready class can never fall back to SMS."""
    from api.routers import workshop as workshop_mod

    async def _sent(*a, **kw):
        return providers.DispatchResult(
            ok=True, status="SENT", provider_id="REQ-ZZ-WSH-1", channel="whatsapp"
        )

    monkeypatch.setattr(providers, "send_whatsapp", _sent)

    asyncio.run(workshop_mod._perform_ready_notify(_job(), "USER-ZZ-1"))

    rows = [
        r
        for r in fake.get_collection("notification_logs").docs
        if r.get("template_id") == "WORKSHOP_READY"
    ]
    assert len(rows) == 1, rows
    assert rows[0]["provider_msg_id"] == "REQ-ZZ-WSH-1"
    # Already dispatched - the drain must never pick it up and send twice.
    assert rows[0]["status"] == "SENT"


def test_workshop_ready_dark_simulated_writes_no_new_row(fake, monkeypatch):
    """Dark deploy: SIMULATED send, byte-identical persistence to main today
    (no notification_logs row appears)."""
    from api.routers import workshop as workshop_mod

    async def _sim(*a, **kw):
        return providers.DispatchResult(
            ok=True, status="SIMULATED", provider_id="sim-x", channel="whatsapp"
        )

    monkeypatch.setattr(providers, "send_whatsapp", _sim)

    asyncio.run(workshop_mod._perform_ready_notify(_job(), "USER-ZZ-1"))

    assert fake.get_collection("notification_logs").docs == []


def test_workshop_ready_failure_report_rides_into_sms_fallback(
    fake, monkeypatch
):
    """End to end for the workshop-ready class: SENT row -> FAILED delivery
    report -> ONE SMS queued under the flow's mapped DLT template."""
    from api.routers import workshop as workshop_mod

    async def _sent(*a, **kw):
        return providers.DispatchResult(
            ok=True, status="SENT", provider_id="REQ-ZZ-WSH-2", channel="whatsapp"
        )

    monkeypatch.setattr(providers, "send_whatsapp", _sent)
    _plant_sms_mapping(fake, flow_key="WORKSHOP_READY", dlt="ZZ_DLT_WORKSHOP_1")

    asyncio.run(workshop_mod._perform_ready_notify(_job(), "USER-ZZ-1"))
    out = apply_delivery_report(
        provider_message_id="REQ-ZZ-WSH-2",
        raw_status="failed",
        channel="whatsapp",
        db=fake,
    )

    assert out["sms_fallback"] == "queued"
    rows = _sms_rows(fake)
    assert len(rows) == 1
    assert rows[0]["dlt_template_id"] == "ZZ_DLT_WORKSHOP_1"


# ===========================================================================
# B. Email transport (item 4)
# ===========================================================================


def test_send_email_dark_is_simulated_with_full_payload_shape(monkeypatch):
    """Dark deploy: SIMULATED, zero network, and meta proves the resolved
    domain / from / to / subject - the payload shape is provable unarmed."""
    monkeypatch.setattr(providers, "DISPATCH_MODE", "off")
    monkeypatch.setattr(providers.httpx, "AsyncClient", _PoisonedClient)
    monkeypatch.setenv("MSG91_EMAIL_DOMAIN", "mail.zz-example.in")
    monkeypatch.setenv("MSG91_EMAIL_FROM", "no-reply@mail.zz-example.in")

    res = asyncio.run(
        providers.send_email("zz.customer@example.com", "ZZ Subject", "ZZ body")
    )
    assert res.status == "SIMULATED"
    assert res.meta == {
        "domain": "mail.zz-example.in",
        "from_email": "no-reply@mail.zz-example.in",
        "to": "zz.customer@example.com",
        "subject": "ZZ Subject",
    }


def test_send_email_live_posts_the_msg91_email_shape(monkeypatch, rec):
    monkeypatch.setattr(providers, "_msg91", _creds)
    monkeypatch.setattr(providers, "DISPATCH_MODE", "live")
    monkeypatch.setattr(
        providers.httpx,
        "AsyncClient",
        rec.client(body={"data": {"unique_id": "ZZ-EMAIL-UID-1"}}),
    )
    monkeypatch.setenv("MSG91_EMAIL_DOMAIN", "mail.zz-example.in")
    monkeypatch.setenv("MSG91_EMAIL_FROM", "no-reply@mail.zz-example.in")

    res = asyncio.run(
        providers.send_email(
            "zz.customer@example.com", "ZZ Subject", "ZZ body", to_name="ZZ Cust"
        )
    )
    assert res.status == "SENT"
    assert res.provider_id == "ZZ-EMAIL-UID-1"
    payload = rec.calls[0]["json"]
    assert payload["recipients"][0]["to"][0]["email"] == "zz.customer@example.com"
    assert payload["from"]["email"] == "no-reply@mail.zz-example.in"
    assert payload["domain"] == "mail.zz-example.in"
    assert payload["subject"] == "ZZ Subject"
    assert payload["body"] == {"type": "text/plain", "data": "ZZ body"}
    assert rec.calls[0]["headers"]["authkey"] == "ZZ-AUTHKEY"


def test_send_email_test_mode_only_reaches_test_email(monkeypatch, rec):
    """DISPATCH_MODE=test: only the TEST_EMAIL address ever receives - the
    exact TEST_PHONE discipline, email twin."""
    monkeypatch.setattr(providers, "_msg91", _creds)
    monkeypatch.setattr(providers, "DISPATCH_MODE", "test")
    monkeypatch.setattr(providers.httpx, "AsyncClient", rec.client())
    monkeypatch.setenv("MSG91_EMAIL_DOMAIN", "mail.zz-example.in")
    monkeypatch.setenv("MSG91_EMAIL_FROM", "no-reply@mail.zz-example.in")
    monkeypatch.setenv("TEST_EMAIL", "owner@zz-example.in")

    other = asyncio.run(providers.send_email("someone@else.in", "s", "b"))
    owner = asyncio.run(providers.send_email("owner@zz-example.in", "s", "b"))

    assert other.status == "SIMULATED"
    assert owner.status == "SENT"
    assert len(rec.calls) == 1


def test_send_email_without_domain_config_fails_honestly(monkeypatch):
    monkeypatch.setattr(providers, "_msg91", _creds)
    monkeypatch.setattr(providers, "DISPATCH_MODE", "live")
    monkeypatch.delenv("MSG91_EMAIL_DOMAIN", raising=False)
    monkeypatch.delenv("MSG91_EMAIL_FROM", raising=False)

    res = asyncio.run(providers.send_email("zz@example.com", "s", "b"))
    assert res.status == "FAILED"
    assert "MSG91_EMAIL_DOMAIN" in (res.error or "")


def test_megaphone_drains_an_email_row_through_the_email_transport(monkeypatch):
    import agents.implementations.megaphone as mega_mod

    seen = []

    async def _spy(to_email, subject, body_text, **kw):
        seen.append((to_email, subject, body_text))
        return providers.DispatchResult(ok=True, status="SIMULATED", channel="email")

    monkeypatch.setattr(providers, "send_email", _spy)

    class _NotifColl:
        def __init__(self, rows):
            self.rows = rows
            self.updates = []

        def find(self, flt):
            class _Cur:
                def __init__(self, r):
                    self._r = r

                def limit(self, n):
                    return self._r[:n]

            return _Cur(self.rows)

        def update_one(self, flt, update):
            self.updates.append((flt, update))

    rows = [
        {
            "notification_id": "N-ZZ-EM1",
            "status": "PENDING",
            "channel": "EMAIL",
            "customer_email": "zz.customer@example.com",
            "customer_name": "ZZ Cust",
            "subject": "ZZ Invoice",
            "message": "ZZ invoice body",
            "template_id": "ORDER_DELIVERED",
            "scheduled_for": None,
        },
        {
            # An EMAIL row without an address must FAIL honestly, not hang
            # around PENDING forever.
            "notification_id": "N-ZZ-EM2",
            "status": "PENDING",
            "channel": "EMAIL",
            "message": "orphan body",
            "template_id": "ORDER_DELIVERED",
            "scheduled_for": None,
        },
    ]
    coll = _NotifColl(rows)
    agent = mega_mod.MegaphoneAgent(db=None)
    stats = asyncio.run(agent._drain_pending(coll))

    assert seen == [("zz.customer@example.com", "ZZ Invoice", "ZZ invoice body")]
    assert stats["failed"] == 1  # the address-less row
    assert stats["simulated"] == 1


# ===========================================================================
# B2. Email validation - capture flag + batch job (item 4)
# ===========================================================================


class _FakeCustomerRepo:
    def __init__(self, docs=None):
        self.collection = FakeCollection()
        for d in docs or []:
            self.collection.insert_one(d)
        self.created = None
        self.updated = None

    def find_by_mobile(self, mobile):
        return None

    def find_by_email(self, email):
        return None

    def find_by_id(self, customer_id):
        return self.collection.find_one({"customer_id": customer_id})

    def create(self, data):
        self.created = dict(data)
        out = dict(data)
        out.setdefault("customer_id", "CUST-ZZ-NEW")
        return out

    def update(self, customer_id, data):
        self.updated = (customer_id, dict(data))
        return True


_ADMIN = {
    "user_id": "USER-ZZ-ADMIN",
    "username": "zz_admin",
    "roles": ["SUPERADMIN"],
    "active_store_id": "BV-RAN-01",
}


def _customers_module(monkeypatch, repo):
    from api.routers import customers as customers_mod

    monkeypatch.setattr(
        customers_mod, "get_customer_repository", lambda: repo
    )
    monkeypatch.setattr(customers_mod, "get_audit_repository", lambda: None)
    return customers_mod


def test_customer_create_dark_stores_no_validation_stamp(monkeypatch):
    """DARK = byte-identical: with DISPATCH_MODE off the created doc carries
    no email_validation field at all (and nothing was spent)."""
    monkeypatch.setattr(providers, "DISPATCH_MODE", "off")
    monkeypatch.setattr(providers.httpx, "AsyncClient", _PoisonedClient)
    repo = _FakeCustomerRepo()
    mod = _customers_module(monkeypatch, repo)

    body = mod.CustomerCreate(
        name="ZZ Customer", mobile="9812345678", email="zz@example.com"
    )
    asyncio.run(mod.create_customer(body, _ADMIN))

    assert repo.created is not None
    assert "email_validation" not in repo.created


def test_customer_create_armed_stamps_the_verdict_flag(monkeypatch):
    """Armed: the created doc carries the MSG91 verdict as a FLAG."""

    async def _verdict(email):
        return {"status": "risky", "raw": "catch-all"}

    monkeypatch.setattr(providers, "validate_email", _verdict)
    repo = _FakeCustomerRepo()
    mod = _customers_module(monkeypatch, repo)

    body = mod.CustomerCreate(
        name="ZZ Customer", mobile="9812345678", email="zz@example.com"
    )
    asyncio.run(mod.create_customer(body, _ADMIN))

    stamp = repo.created["email_validation"]
    assert stamp["status"] == "risky"
    assert stamp["source"] == "on_capture"


def test_invalid_verdict_flags_but_never_blocks_the_create(monkeypatch):
    """House pattern is flag-and-hold: an INVALID address still creates the
    customer (the verdict is a flag, never a gate); even a validator crash
    must not block capture."""

    async def _invalid(email):
        return {"status": "invalid", "raw": "undeliverable"}

    monkeypatch.setattr(providers, "validate_email", _invalid)
    repo = _FakeCustomerRepo()
    mod = _customers_module(monkeypatch, repo)
    body = mod.CustomerCreate(
        name="ZZ Customer", mobile="9812345678", email="zz@example.com"
    )
    out = asyncio.run(mod.create_customer(body, _ADMIN))
    assert out["customer_id"]
    assert repo.created["email_validation"]["status"] == "invalid"

    async def _boom(email):
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(providers, "validate_email", _boom)
    repo2 = _FakeCustomerRepo()
    mod2 = _customers_module(monkeypatch, repo2)
    out2 = asyncio.run(mod2.create_customer(body, _ADMIN))
    assert out2["customer_id"]
    assert "email_validation" not in repo2.created


def test_customer_update_revalidates_only_a_changed_email(monkeypatch):
    async def _verdict(email):
        return {"status": "valid", "raw": "deliverable"}

    monkeypatch.setattr(providers, "validate_email", _verdict)
    existing = {
        "customer_id": "CUST-ZZ-9",
        "name": "ZZ Customer",
        "mobile": "9812345678",
        "email": "old@example.com",
        "customer_type": "B2C",
        "home_store_id": "BV-RAN-01",
        "patients": [],
    }
    repo = _FakeCustomerRepo([existing])
    mod = _customers_module(monkeypatch, repo)

    # Changed email -> stamped.
    upd = mod.CustomerUpdate(email="new@example.com")
    asyncio.run(mod.update_customer("CUST-ZZ-9", upd, _ADMIN))
    _cid, data = repo.updated
    assert data["email_validation"]["status"] == "valid"

    # Same email re-saved -> nothing re-spent, no stamp in the $set.
    repo.updated = None
    upd_same = mod.CustomerUpdate(email="old@example.com")
    asyncio.run(mod.update_customer("CUST-ZZ-9", upd_same, _ADMIN))
    _cid, data_same = repo.updated
    assert "email_validation" not in data_same


def test_batch_validation_dark_refuses_honestly_and_spends_nothing(monkeypatch):
    monkeypatch.setattr(providers, "DISPATCH_MODE", "off")
    monkeypatch.setattr(providers.httpx, "AsyncClient", _PoisonedClient)
    repo = _FakeCustomerRepo(
        [{"customer_id": "C1", "email": "a@example.com"}]
    )
    mod = _customers_module(monkeypatch, repo)

    out = asyncio.run(mod.validate_customer_emails(limit=200, current_user=_ADMIN))

    assert out["ran"] is False
    assert out["checked"] == 0
    assert "not spending" in out["reason"]
    doc = repo.collection.find_one({"customer_id": "C1"})
    assert "email_validation" not in doc


def test_batch_validation_stamps_skips_stamped_and_respects_the_limit(
    monkeypatch,
):
    monkeypatch.setattr(providers, "DISPATCH_MODE", "live")
    monkeypatch.setattr(providers, "_msg91", _creds)

    verdicts = {
        "a@example.com": {"status": "valid", "raw": "deliverable"},
        "b@example.com": {"status": "invalid", "raw": "undeliverable"},
    }

    async def _verdict(email):
        return verdicts.get(email)

    monkeypatch.setattr(providers, "validate_email", _verdict)
    repo = _FakeCustomerRepo(
        [
            {"customer_id": "C1", "email": "a@example.com"},
            {"customer_id": "C2", "email": "b@example.com"},
            {
                "customer_id": "C3",
                "email": "c@example.com",
                # Already stamped: a re-run must SKIP it (cost-bounded passes).
                "email_validation": {"status": "valid", "source": "batch"},
            },
            {"customer_id": "C4"},  # no email at all
        ]
    )
    mod = _customers_module(monkeypatch, repo)

    out = asyncio.run(mod.validate_customer_emails(limit=200, current_user=_ADMIN))
    assert out["ran"] is True
    assert out["checked"] == 2
    assert out["counts"]["valid"] == 1
    assert out["counts"]["invalid"] == 1
    assert out["remaining_unchecked"] == 0
    c1 = repo.collection.find_one({"customer_id": "C1"})
    assert c1["email_validation"]["status"] == "valid"
    assert c1["email_validation"]["source"] == "batch"

    # The limit caps spend per pass.
    repo2 = _FakeCustomerRepo(
        [
            {"customer_id": "C1", "email": "a@example.com"},
            {"customer_id": "C2", "email": "b@example.com"},
        ]
    )
    mod2 = _customers_module(monkeypatch, repo2)
    out2 = asyncio.run(mod2.validate_customer_emails(limit=1, current_user=_ADMIN))
    assert out2["checked"] == 1
    assert out2["remaining_unchecked"] == 1


def test_validate_email_dark_makes_no_network_call(monkeypatch):
    """Validations are BILLED per address: dark must mean literally zero
    HTTP, not a call whose result is discarded."""
    monkeypatch.setattr(providers, "DISPATCH_MODE", "off")
    monkeypatch.setattr(providers, "_msg91", _creds)
    monkeypatch.setattr(providers.httpx, "AsyncClient", _PoisonedClient)

    assert asyncio.run(providers.validate_email("zz@example.com")) is None


def test_validate_email_armed_normalises_the_provider_verdict(monkeypatch, rec):
    monkeypatch.setattr(providers, "DISPATCH_MODE", "live")
    monkeypatch.setattr(providers, "_msg91", _creds)
    monkeypatch.setattr(
        providers.httpx,
        "AsyncClient",
        rec.client(body={"data": {"result": "Undeliverable"}}),
    )

    out = asyncio.run(providers.validate_email("zz@example.com"))
    assert out == {"status": "invalid", "raw": "Undeliverable"}
    assert rec.calls[0]["json"] == {"email": "zz@example.com"}


# ===========================================================================
# C. Voice escalation rung (item 5)
# ===========================================================================


def _p1_task(**over):
    task = {
        "task_id": "TASK-ZZ-P1",
        "title": "ZZ till variance Rs 5,000",
        "priority": "P1",
        "source": "SYSTEM",
        "store_id": "BV-RAN-01",
        "status": "OPEN",
    }
    task.update(over)
    return task


_ACK_REASON = "Not acknowledged within SLA (30m)"


def _plant_manager(db):
    db.get_collection("users").insert_one(
        {
            "user_id": "USER-ZZ-MGR",
            "roles": ["STORE_MANAGER"],
            "is_active": True,
            "store_ids": ["BV-RAN-01"],
            "phone": "919700000001",
        }
    )


def test_policy_key_is_registered_with_default_off():
    from api.services.policy_registry import REGISTRY

    spec = REGISTRY["msg.voice_escalation"]
    assert spec.default == "off"
    assert spec.enum == ("off", "on")
    assert spec.env == "MSG_VOICE_ESCALATION"


def test_default_off_means_no_call_at_all(fake, monkeypatch):
    """Fresh deploy, policy untouched: the rung must not even look up the
    manager, let alone dial."""
    monkeypatch.delenv("MSG_VOICE_ESCALATION", raising=False)
    _plant_manager(fake)

    verdict = asyncio.run(maybe_voice_escalate(_p1_task(), _ACK_REASON, db=fake))
    assert verdict == "skipped:policy_off"
    assert fake.get_collection("tasks").docs == []


def test_p1_system_ack_breach_places_one_simulated_call_dark(fake, monkeypatch):
    """Policy on + dark deploy: the call is SIMULATED (rings nobody) and the
    task carries the one-call claim with the provider result."""
    monkeypatch.setenv("MSG_VOICE_ESCALATION", "on")
    monkeypatch.setattr(providers, "DISPATCH_MODE", "off")
    monkeypatch.setattr(providers.httpx, "AsyncClient", _PoisonedClient)
    _plant_manager(fake)
    fake.get_collection("tasks").insert_one(_p1_task())

    verdict = asyncio.run(maybe_voice_escalate(_p1_task(), _ACK_REASON, db=fake))
    assert verdict == "simulated"

    task = fake.get_collection("tasks").find_one({"task_id": "TASK-ZZ-P1"})
    assert task["voice_escalation"]["status"] == "SIMULATED"
    assert task["voice_escalation"]["to_user_id"] == "USER-ZZ-MGR"
    assert task["voice_escalation"]["provider_id"].startswith("sim-voice-")


def test_second_escalation_of_the_same_task_never_redials(fake, monkeypatch):
    monkeypatch.setenv("MSG_VOICE_ESCALATION", "on")
    monkeypatch.setattr(providers, "DISPATCH_MODE", "off")
    _plant_manager(fake)
    fake.get_collection("tasks").insert_one(_p1_task())

    first = asyncio.run(maybe_voice_escalate(_p1_task(), _ACK_REASON, db=fake))
    second = asyncio.run(maybe_voice_escalate(_p1_task(), _ACK_REASON, db=fake))
    assert first == "simulated"
    assert second == "duplicate"


def test_only_p1_system_ack_breaches_qualify(fake, monkeypatch):
    """The rung is FOR till-variance / SLA-breach class tasks only: P2, USER
    tasks and overdue-but-acknowledged escalations never dial."""
    monkeypatch.setenv("MSG_VOICE_ESCALATION", "on")
    _plant_manager(fake)

    p2 = asyncio.run(
        maybe_voice_escalate(_p1_task(priority="P2"), _ACK_REASON, db=fake)
    )
    user_task = asyncio.run(
        maybe_voice_escalate(_p1_task(source="USER"), _ACK_REASON, db=fake)
    )
    overdue = asyncio.run(
        maybe_voice_escalate(
            _p1_task(), "Open past due date (task overdue)", db=fake
        )
    )
    acked = asyncio.run(
        maybe_voice_escalate(
            _p1_task(acknowledged_at="2026-08-31T05:00:00+00:00"),
            _ACK_REASON,
            db=fake,
        )
    )

    assert p2 == "skipped:not_p1"
    assert user_task == "skipped:not_system_task"
    assert overdue == "skipped:not_ack_breach"
    assert acked == "skipped:already_acknowledged"


def test_armed_call_carries_tts_script_and_dtmf_collection(monkeypatch, rec, fake):
    """Live: ONE MSG91 voice call with the TTS script naming the task and
    DTMF collection on (press-1 must be capturable)."""
    monkeypatch.setenv("MSG_VOICE_ESCALATION", "on")
    monkeypatch.setattr(providers, "DISPATCH_MODE", "live")
    monkeypatch.setattr(providers, "_msg91", _creds)
    monkeypatch.setattr(
        providers.httpx, "AsyncClient", rec.client(body={"request_id": "ZZ-CALL-1"})
    )
    _plant_manager(fake)
    fake.get_collection("tasks").insert_one(_p1_task())

    verdict = asyncio.run(maybe_voice_escalate(_p1_task(), _ACK_REASON, db=fake))
    assert verdict == "called"

    payload = rec.calls[0]["json"]
    assert payload["to"] == "919700000001"
    assert payload["type"] == "tts"
    assert payload["collect_dtmf"] is True
    assert "ZZ till variance" in payload["message"]
    assert "Press 1" in payload["message"]

    task = fake.get_collection("tasks").find_one({"task_id": "TASK-ZZ-P1"})
    assert task["voice_escalation"]["provider_id"] == "ZZ-CALL-1"


def test_press_1_acknowledges_the_task_with_a_voice_audit_row(fake):
    fake.get_collection("tasks").insert_one(
        _p1_task(
            voice_escalation={
                "status": "SENT",
                "provider_id": "ZZ-CALL-1",
                "to_user_id": "USER-ZZ-MGR",
            }
        )
    )

    acked = apply_voice_acks(
        {"data": {"request_id": "ZZ-CALL-1", "dtmf": "1"}}, db=fake
    )
    assert acked == 1

    task = fake.get_collection("tasks").find_one({"task_id": "TASK-ZZ-P1"})
    assert task["status"] == "IN_PROGRESS"
    assert task["acknowledged_by"] == "USER-ZZ-MGR"
    assert task["acknowledged_via"] == "voice_ivr"
    history = task["history"]
    assert len(history) == 1
    assert history[0]["channel"] == "voice_ivr"
    assert history[0]["action"] == "acknowledged"
    assert history[0]["provider_message_id"] == "ZZ-CALL-1"


def test_other_digits_and_unknown_calls_acknowledge_nothing(fake):
    fake.get_collection("tasks").insert_one(
        _p1_task(voice_escalation={"provider_id": "ZZ-CALL-1"})
    )

    assert (
        apply_voice_acks({"data": {"request_id": "ZZ-CALL-1", "dtmf": "2"}}, db=fake)
        == 0
    )
    assert (
        apply_voice_acks(
            {"data": {"request_id": "ZZ-CALL-UNKNOWN", "dtmf": "1"}}, db=fake
        )
        == 0
    )
    task = fake.get_collection("tasks").find_one({"task_id": "TASK-ZZ-P1"})
    assert task["status"] == "OPEN"


def test_press_1_is_idempotent_on_an_already_acknowledged_task(fake):
    fake.get_collection("tasks").insert_one(
        _p1_task(
            status="IN_PROGRESS",
            acknowledged_at="2026-08-31T05:00:00+00:00",
            voice_escalation={"provider_id": "ZZ-CALL-1"},
        )
    )

    acked = apply_voice_acks(
        {"data": {"request_id": "ZZ-CALL-1", "dtmf": "1"}}, db=fake
    )
    assert acked == 0


def test_voice_webhook_channel_routes_press_1_to_the_task(client, fake):
    """End to end through the spine's voice receiver: a signed MSG91 voice
    report carrying dtmf=1 acknowledges the matching task."""
    fake.get_collection("tasks").insert_one(
        _p1_task(
            voice_escalation={
                "status": "SENT",
                "provider_id": "ZZ-CALL-9",
                "to_user_id": "USER-ZZ-MGR",
            }
        )
    )

    r = _post(client, "voice", {"data": {"request_id": "ZZ-CALL-9", "dtmf": "1"}})
    assert r.status_code == 200, r.text

    task = fake.get_collection("tasks").find_one({"task_id": "TASK-ZZ-P1"})
    assert task["status"] == "IN_PROGRESS"
    assert task["acknowledged_via"] == "voice_ivr"


def test_notify_escalation_carries_the_voice_rung_verdict(monkeypatch):
    """The rung lives at THE one escalation alert site: notify_escalation's
    result must report what the voice rung decided (fail-closed off here -
    no policy, no db)."""
    monkeypatch.delenv("MSG_VOICE_ESCALATION", raising=False)
    monkeypatch.setattr(db_connection, "get_db", lambda: None)
    from api.services.task_notify import notify_escalation

    result = asyncio.run(
        notify_escalation(None, {"user_id": "USER-ZZ-MGR"}, _p1_task(), _ACK_REASON)
    )
    assert result["voice"] == "skipped:policy_off"
