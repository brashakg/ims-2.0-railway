"""Codex whole-app audit 2026-11-04, batch b02 (WhatsApp / MSG91 / ONDC doors).

  L1-F24  the WhatsApp inbox listed every shop's chats to a shop manager
  L1-F30  the WhatsApp intent DB helper always failed with a real PyMongo
          Database (bool(Database) raises), so no intent ever touched Mongo
  L1-F26  a failed MSG91 handler was stamped processed and deduped for ever
  L1-F31  a national Indian number beginning 91 was sent with no country code
  L1-F35  two ONDC callbacks ignored a failed signature check

Codex round 1 on PR #1416 (2026-11-12):

  1416-P2a  a saved message made an unapplied intent permanently "duplicate"
  1416-P2b  a failed thread write let the intent run again on the retry
  1416-P2c  D20: ONDC off refuses EVERY callback, correctly signed or not

Every test runs behind the REAL routes and services, with signed synthetic
payloads, on an in-memory mongomock database. The inbound state-machine tests
(the `wa` fixture) run on mongomock AND on a native PyMongo Database: CI's
Mongo service on 127.0.0.1:27017, or the port given in IMS_TEST_MONGO_PORT
(the convention of test_workshop_handoff_recovery; the test owns and drops
IMS_TEST_MONGO_DB). No provider is contacted and DISPATCH_MODE is never armed:
the send doors are observed at their dispatch gate, the ONDC publisher is a
stub.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import mongomock
import pymongo
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test_x")

import database.connection as db_connection  # noqa: E402
from agents import providers  # noqa: E402
from agents.implementations.nexus import NexusAgent  # noqa: E402
from api.routers import ondc, portal, webhooks  # noqa: E402
from api.routers.auth import create_access_token  # noqa: E402
from api.services import whatsapp_intents  # noqa: E402


# ---------------------------------------------------------------------------
# Shared harness
# ---------------------------------------------------------------------------


class _Failing:
    """A collection whose named operations fail (a storage outage)."""

    def __init__(self, coll, ops):
        self._coll, self._ops = coll, ops

    def __getattr__(self, name):
        if name in self._ops:

            def boom(*_a, **_k):
                raise RuntimeError("synthetic storage outage")

            return boom
        return getattr(self._coll, name)


class _Db:
    """The mongomock database, with a switch to break chosen collections.

    ``down = {"notification_logs": {"update_many"}}`` makes that one operation
    raise until the entry is removed -- "storage failed after the inbox row
    was written", then recovered. A callable value wraps the collection
    instead (for a failure on one particular call).
    """

    def __init__(self, real):
        self.real = real
        self.down: dict = {}

    def __bool__(self):
        # Exactly what a pymongo Database does: `db or x` / `if db` is an error.
        raise NotImplementedError(
            "Database objects do not implement truth value testing or bool(). "
            "Please compare with None instead: database is not None"
        )

    def get_collection(self, name):
        coll = self.real.get_collection(name)
        ops = self.down.get(name)
        if callable(ops):
            return ops(coll)
        return _Failing(coll, ops) if ops else coll


class _Conn:
    """Shaped like the app's DatabaseConnection: the database is on ``.db``."""

    is_connected = True

    def __init__(self, db):
        self.db = db

    def get_collection(self, name):
        return self.db.get_collection(name)


@pytest.fixture(autouse=True)
def _fresh_rate_limit_state():
    from api.services.cache import cache

    cache.flush()
    yield
    cache.flush()


@pytest.fixture
def mongo(monkeypatch):
    """ONE in-memory database behind every lookup these doors use."""
    db = _Db(mongomock.MongoClient()["zz_b02"])
    monkeypatch.setattr(db_connection, "get_db", lambda: _Conn(db))
    return db


def _headers(roles, store_ids, active=None, user="zz-b02-user"):
    token = create_access_token(
        {
            "user_id": f"{user}-{'-'.join(roles)}-{'-'.join(store_ids) or 'none'}",
            "username": user,
            "roles": roles,
            "store_ids": store_ids,
            "active_store_id": active if active is not None else (store_ids[0] if store_ids else None),
        }
    )
    return {"Authorization": f"Bearer {token}"}


# ===========================================================================
# L1-F30 -- the intent DB helper works with the real PyMongo wrapper
# ===========================================================================


def test_intent_db_helper_returns_a_real_pymongo_database(monkeypatch):
    """The audit's exact probe: a real pymongo Database (connect=False, no
    operation is sent) behind the app's connection wrapper. On main the helper
    did `getattr(d, "db", None) or d`; bool(Database) raises, the helper
    swallowed it and returned None."""
    from pymongo import MongoClient

    client = MongoClient("mongodb://127.0.0.1:1/", connect=False, serverSelectionTimeoutMS=1)
    try:
        real_db = client["zz_b02_never_contacted"]
        with pytest.raises(NotImplementedError):
            bool(real_db)  # the trap itself
        monkeypatch.setattr(db_connection, "get_db", lambda: _Conn(real_db))
        assert whatsapp_intents._get_db() is real_db
        # ...and it is the same helper the delivery spine uses, not a copy.
        from api.services import message_events

        assert whatsapp_intents._get_db is message_events._get_db
    finally:
        client.close()


WA_SECRET = "zz_b02_waba_app_secret"


def _meta_body(text, phone="919876543210", wa_id="wamid.ZZ-1"):
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "contacts": [{"profile": {"name": "Synthetic Sender"}}],
                            "messages": [
                                {
                                    "id": wa_id,
                                    "from": phone,
                                    "type": "text",
                                    "text": {"body": text},
                                    "timestamp": "1700000000",
                                }
                            ],
                        }
                    }
                ]
            }
        ],
    }


def _post_inbound(client, body):
    raw = json.dumps(body).encode()
    sig = "sha256=" + hmac.new(WA_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return client.post(
        "/api/v1/webhooks/whatsapp",
        content=raw,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig},
    )


@pytest.fixture
def inbound(mongo, monkeypatch):
    """A signed-inbound world with one known customer. The database refuses
    truth-testing exactly as PyMongo's does (_Db.__bool__), which is what made
    the old helper return None for every intent."""
    monkeypatch.setenv("WABA_APP_SECRET", WA_SECRET)
    mongo.real.customers.insert_one(
        {
            "customer_id": "CUST-ZZ-A",
            "name": "Asha Synthetic",
            "mobile": "9876543210",
            "home_store_id": "ST-A",
        }
    )
    # The reply door is a stub that must never be reached (auto-reply is off by
    # default; DISPATCH_MODE is off): a call would be a real outbound attempt.
    with patch.object(providers, "send_whatsapp", new=AsyncMock()) as send:
        yield mongo
        send.assert_not_called()


def test_signed_inbound_reaches_the_database_for_every_intent(client, inbound):
    """Lookup, human handoff, STOP and a booking follow-up, through the real
    signed webhook. On main none of these wrote anything."""
    db = inbound.real

    r = _post_inbound(client, _meta_body("I need an agent please", wa_id="wamid.ZZ-AGENT"))
    assert r.status_code == 200, r.text
    assert r.json()["results"][0]["customer_id"] == "CUST-ZZ-A"  # lookup
    thread = db.whatsapp_conversations.find_one({"phone": "9876543210"})
    assert thread["customer_id"] == "CUST-ZZ-A"
    assert thread["customer_name"] == "Asha Synthetic"
    assert thread["needs_human"] is True  # human handoff persisted

    r = _post_inbound(client, _meta_body("book an eye test", wa_id="wamid.ZZ-BOOK"))
    assert r.json()["results"][0]["intent"] == "BOOK"
    follow_ups = list(db.follow_ups.find({"customer_id": "CUST-ZZ-A"}))
    assert len(follow_ups) == 1
    assert follow_ups[0]["source"] == "whatsapp_inbound"

    r = _post_inbound(client, _meta_body("STOP", wa_id="wamid.ZZ-STOP"))
    assert r.json()["results"][0]["intent"] == "OPT_OUT"
    cust = db.customers.find_one({"customer_id": "CUST-ZZ-A"})
    assert cust["whatsapp_opted_out"] is True and cust["marketing_consent"] is False
    assert db.whatsapp_opt_outs.count_documents({"phone": "919876543210"}) == 1

    # The stored STOP is now honoured: the next message is blocked up front.
    r = _post_inbound(client, _meta_body("book again", wa_id="wamid.ZZ-AFTER-STOP"))
    assert r.json()["results"][0]["opted_out"] is True
    assert db.follow_ups.count_documents({}) == 1


def test_meta_redelivery_of_the_same_message_runs_the_intent_once(client, inbound):
    """Now that intents write, a Meta retry of one message id must not write
    twice (a second follow-up, a second thread entry)."""
    db = inbound.real
    body = _meta_body("book an eye test", wa_id="wamid.ZZ-REPLAY")
    assert _post_inbound(client, body).json()["results"][0]["intent"] == "BOOK"
    again = _post_inbound(client, body)
    assert again.status_code == 200
    assert again.json()["results"] == [{"duplicate": True, "phone": "3210"}]
    assert db.follow_ups.count_documents({}) == 1
    thread = db.whatsapp_conversations.find_one({"phone": "9876543210"})
    assert [m["wa_message_id"] for m in thread["messages"]] == ["wamid.ZZ-REPLAY"]
    # A different message on the same thread is still taken.
    _post_inbound(client, _meta_body("book for my son too", wa_id="wamid.ZZ-NEXT"))
    assert db.follow_ups.count_documents({}) == 2
    assert db.whatsapp_conversations.count_documents({}) == 1

# ===========================================================================
# 1416-P2a / 1416-P2b -- one durable inbox row per inbound message
# ===========================================================================
# Codex reproduced both on native Mongo: (a) the message was saved to the
# thread, the intent then failed, and the retry was answered "duplicate" for
# ever; (b) the thread write failed, the intent ran, and the retry ran it
# again. The thread is no longer the idempotency record: each message has its
# own webhook_inbox row (received -> intent applied -> done).

REAL_HOST = "127.0.0.1"
REAL_PORT = os.environ.get("IMS_TEST_MONGO_PORT") or ("27017" if os.environ.get("CI") else "")
# IMS_TEST_MONGO_DB: a builder's own scratch name. The test DROPS it.
REAL_DB = os.environ.get("IMS_TEST_MONGO_DB") or f"ims_test_b02_inbound_{os.getpid()}"
PHONE = "9876543210"

BOOK = ("book an eye test", "BOOK")
AGENT = ("I need an agent please", "AGENT")
STOP = ("STOP", "OPT_OUT")
INTENTS = pytest.mark.parametrize("text,intent", [BOOK, AGENT, STOP], ids=["booking", "handoff", "stop"])


@pytest.fixture(params=["mongomock", "real"])
def wa(request, monkeypatch):
    """The signed Meta door over ONE database, handed to the app the way
    production hands it: a Database on the connection's ``.db`` ("real" = a
    native PyMongo Database, nothing wrapped). A re-dispatch runs NEXUS's real
    drain; the reply door is a stub that must never be reached."""
    mongo_client = None
    if request.param == "real":
        if not REAL_PORT:
            pytest.skip("no local Mongo given (IMS_TEST_MONGO_PORT)")
        mongo_client = pymongo.MongoClient(REAL_HOST, int(REAL_PORT), serverSelectionTimeoutMS=2000)
        mongo_client.admin.command("ping")
        assert mongo_client.address[0] == REAL_HOST
        mongo_client.drop_database(REAL_DB)
        db = mongo_client[REAL_DB]
        assert isinstance(db, pymongo.database.Database) and db.name == REAL_DB
    else:
        db = mongomock.MongoClient()["zz_b02_inbound"]
    monkeypatch.setattr(db_connection, "get_db", lambda: _Conn(db))
    monkeypatch.setenv("WABA_APP_SECRET", WA_SECRET)
    db.customers.insert_one(
        {"customer_id": "CUST-ZZ-A", "name": "Asha Synthetic", "mobile": PHONE,
         "home_store_id": "ST-A"}
    )
    try:
        with patch.object(providers, "send_whatsapp", new=AsyncMock()) as send, \
                _nexus_drains_redeliveries(db):
            yield db
            send.assert_not_called()
    finally:
        if mongo_client is not None:
            mongo_client.drop_database(REAL_DB)
            mongo_client.close()


@contextmanager
def _outage(db, coll_name, op):
    """`op` on ONE collection of this database fails while the block runs
    (patched on the driver's own Collection class, so the app still holds the
    untouched Database)."""
    cls = type(db.get_collection(coll_name))
    real = getattr(cls, op)

    def broken(self, *a, **k):
        if self.name == coll_name and self.database.name == db.name:
            raise RuntimeError("synthetic storage outage")
        return real(self, *a, **k)

    with patch.object(cls, op, broken):
        yield


def _wa_row(db, wa_id):
    rows = list(db.webhook_inbox.find({"vendor": "whatsapp", "event_id": wa_id}))
    assert len(rows) == 1, rows
    return rows[0]


def _effects(db):
    """Everything an intent may have written, as stored."""
    cust = db.customers.find_one({"customer_id": "CUST-ZZ-A"}, {"_id": 0})
    thread = db.whatsapp_conversations.find_one({"phone": PHONE}, {"_id": 0}) or {}
    return {
        "follow_ups": sorted(
            (f["follow_up_id"], f["follow_up_type"], f.get("source_message_id"))
            for f in db.follow_ups.find({})
        ),
        "opted_out": (cust.get("whatsapp_opted_out"), cust.get("whatsapp_opted_out_at"),
                      cust.get("marketing_consent")),
        "opt_out_rows": sorted(
            (o["phone"], o["opted_out_at"]) for o in db.whatsapp_opt_outs.find({})
        ),
        "handoff": (thread.get("needs_human"), thread.get("needs_human_since")),
        "thread_messages": [m["wa_message_id"] for m in thread.get("messages") or []],
    }


def _assert_applied_once(db, intent, wa_id):
    got = _effects(db)
    assert got["thread_messages"] == [wa_id]
    if intent == "BOOK":
        assert [(t, m) for _id, t, m in got["follow_ups"]] == [("eye_test_reminder", wa_id)]
    else:
        assert got["follow_ups"] == []
    if intent == "AGENT":
        assert got["handoff"][0] is True and got["handoff"][1]
    else:
        assert got["handoff"][0] is False
    if intent == "OPT_OUT":
        assert got["opted_out"][0] is True and got["opted_out"][2] is False
        assert len(got["opt_out_rows"]) == 1
    else:
        assert got["opted_out"][0] is None and got["opt_out_rows"] == []
    row = _wa_row(db, wa_id)
    assert row["processed"] is True and row["status"] == "succeeded"
    # (A STOP whose writes had landed before a re-run is met by the opt-out
    # gate, which answers "opted out" without naming the intent again.)
    recorded = row["intent_result"]
    assert row["intent_applied_at"]
    assert recorded["intent"] == intent or (intent == "OPT_OUT" and recorded["opted_out"])
    return got


def _age_the_lease(db, wa_id):
    db.webhook_inbox.update_one(
        {"event_id": wa_id},
        {"$set": {"processing_at": datetime.now(timezone.utc)
                  - NexusAgent.WEBHOOK_PROCESSING_LEASE - timedelta(minutes=1)}},
    )


@INTENTS
def test_healthy_inbound_applies_the_intent_once_and_records_it(client, wa, text, intent):
    wa_id = f"wamid.ZZ-OK-{intent}"
    r = _post_inbound(client, _meta_body(text, wa_id=wa_id))
    assert r.status_code == 200, r.text
    assert r.json()["results"][0]["intent"] == intent
    _assert_applied_once(wa, intent, wa_id)
    row = _wa_row(wa, wa_id)
    assert row["attempts"] == 1 and row["signature_verified"] is True
    assert row["payload"]["wa_message_id"] == wa_id


@INTENTS
def test_duplicate_redelivery_of_an_applied_message_changes_nothing(client, wa, text, intent):
    wa_id = f"wamid.ZZ-DUP-{intent}"
    body = _meta_body(text, wa_id=wa_id)
    _post_inbound(client, body)
    before = _assert_applied_once(wa, intent, wa_id)
    for _ in range(2):
        again = _post_inbound(client, body)
        assert again.status_code == 200
        assert again.json()["results"] == [{"duplicate": True, "phone": "3210"}]
    asyncio.run(NexusAgent(db=wa)._retry_failed_webhooks())  # nothing is owed
    assert _assert_applied_once(wa, intent, wa_id) == before
    assert _wa_row(wa, wa_id)["attempts"] == 1


@INTENTS
def test_crash_after_the_message_is_saved_still_applies_the_intent_on_redelivery(
    client, wa, text, intent
):
    """1416-P2a, Codex's injection: the message is saved, then the run fails
    just before the intent. It used to be acknowledged, and the redelivery
    answered "duplicate" with the intent never applied and nothing pending."""
    wa_id = f"wamid.ZZ-LOST-{intent}"
    body = _meta_body(text, wa_id=wa_id)
    with patch.object(whatsapp_intents, "dispatch_intent",
                      new=AsyncMock(side_effect=RuntimeError("crash before intent"))):
        first = _post_inbound(client, body)
    assert first.status_code == 200
    assert first.json()["results"][0]["error"] == "crash before intent"
    saved = _effects(wa)
    assert saved["thread_messages"] == [wa_id]  # the message WAS saved
    assert saved["follow_ups"] == [] and saved["handoff"][0] is False
    assert saved["opted_out"][0] is None
    pending = _wa_row(wa, wa_id)  # ...and the intent is still owed, on record
    assert pending["processed"] is False and pending["status"] == "failed"
    assert "intent_applied_at" not in pending

    second = _post_inbound(client, body)  # Meta's identical redelivery
    assert second.json()["results"] == [{"duplicate": True, "phone": "3210", "redispatched": True}]
    applied = _assert_applied_once(wa, intent, wa_id)
    assert _wa_row(wa, wa_id)["attempts"] == 2

    third = _post_inbound(client, body)
    assert third.json()["results"] == [{"duplicate": True, "phone": "3210"}]
    assert _assert_applied_once(wa, intent, wa_id) == applied


def test_hourly_sweep_applies_an_unapplied_intent_without_any_redelivery(client, wa):
    wa_id = "wamid.ZZ-SWEEP"
    with patch.object(whatsapp_intents, "dispatch_intent",
                      new=AsyncMock(side_effect=RuntimeError("crash before intent"))):
        _post_inbound(client, _meta_body("book an eye test", wa_id=wa_id))
    assert wa.follow_ups.count_documents({}) == 0
    asyncio.run(NexusAgent(db=wa)._retry_failed_webhooks())
    applied = _assert_applied_once(wa, "BOOK", wa_id)
    asyncio.run(NexusAgent(db=wa)._retry_failed_webhooks())  # second sweep is inert
    assert _assert_applied_once(wa, "BOOK", wa_id) == applied


def test_a_run_that_died_mid_message_is_taken_over_after_the_lease(client, wa):
    """A real crash records nothing at all: the row is left exactly as it was
    claimed (status=processing). A redelivery inside the lease must not start
    a second run beside it; after the lease the sweep finishes the message."""
    wa_id = "wamid.ZZ-DIED"
    body = _meta_body("book an eye test", wa_id=wa_id)
    with patch.object(whatsapp_intents, "dispatch_intent",
                      new=AsyncMock(side_effect=RuntimeError("worker killed"))), \
            patch.object(webhooks, "_write_inline_outcome"):
        _post_inbound(client, body)
    assert _wa_row(wa, wa_id)["status"] == "processing"

    during = _post_inbound(client, body)
    assert during.json()["results"] == [{"duplicate": True, "phone": "3210"}]
    assert wa.follow_ups.count_documents({}) == 0

    _age_the_lease(wa, wa_id)
    asyncio.run(NexusAgent(db=wa)._retry_failed_webhooks())
    _assert_applied_once(wa, "BOOK", wa_id)


@INTENTS
def test_failed_thread_write_then_retries_apply_the_intent_exactly_once(
    client, wa, text, intent
):
    """1416-P2b, Codex's injection: only whatsapp_conversations.update_one
    fails. The intent used to run anyway (nothing recorded the message), and
    the identical retry ran it a second time: follow-ups 1 -> 2."""
    wa_id = f"wamid.ZZ-THREAD-{intent}"
    body = _meta_body(text, wa_id=wa_id)
    with _outage(wa, "whatsapp_conversations", "update_one"):
        first = _post_inbound(client, body)
    assert first.status_code == 200 and "error" in first.json()["results"][0]
    row = _wa_row(wa, wa_id)
    assert row["processed"] is False and row["status"] == "failed"

    second = _post_inbound(client, body)
    assert second.json()["results"][0]["redispatched"] is True
    applied = _assert_applied_once(wa, intent, wa_id)

    for _ in range(2):  # and any number of further identical retries
        _post_inbound(client, body)
    asyncio.run(NexusAgent(db=wa)._retry_failed_webhooks())
    assert _assert_applied_once(wa, intent, wa_id) == applied


@INTENTS
def test_intent_that_landed_but_could_not_be_marked_is_not_applied_twice(
    client, wa, text, intent
):
    """The other side of the same window: the intent's writes land, then the
    inbox row cannot be updated (neither the "applied" mark nor the outcome).
    The re-run meets writes keyed on the message, so nothing is written twice
    and no stored time moves."""
    wa_id = f"wamid.ZZ-MARK-{intent}"
    body = _meta_body(text, wa_id=wa_id)
    with _outage(wa, "webhook_inbox", "update_one"):
        first = _post_inbound(client, body)
    assert first.status_code == 200 and "error" in first.json()["results"][0]
    landed = _effects(wa)  # the business effect is already stored...
    row = _wa_row(wa, wa_id)  # ...but the row could not say so
    assert row["status"] == "processing" and "intent_applied_at" not in row

    _age_the_lease(wa, wa_id)
    asyncio.run(NexusAgent(db=wa)._retry_failed_webhooks())
    assert _assert_applied_once(wa, intent, wa_id) == landed
    assert _post_inbound(client, body).json()["results"] == [{"duplicate": True, "phone": "3210"}]
    assert _effects(wa) == landed


def test_an_applied_intent_is_skipped_when_a_later_step_makes_the_row_run_again(client, wa):
    """Row state "intent applied, not yet done": the re-run must not call the
    intent at all (a reply would otherwise go out twice once replies are on)."""
    wa_id = "wamid.ZZ-APPLIED"
    body = _meta_body("book an eye test", wa_id=wa_id)
    _post_inbound(client, body)
    applied = _assert_applied_once(wa, "BOOK", wa_id)
    wa.webhook_inbox.update_one(
        {"event_id": wa_id}, {"$set": {"processed": False, "status": "failed"}}
    )
    with patch.object(whatsapp_intents, "dispatch_intent", new=AsyncMock()) as intent_door:
        assert _post_inbound(client, body).json()["results"][0]["redispatched"] is True
        intent_door.assert_not_called()
    assert _assert_applied_once(wa, "BOOK", wa_id) == applied


def test_a_message_that_cannot_be_recorded_is_not_acknowledged(client, wa):
    """No inbox row, no 200: Meta is asked to resend, and nothing is done for
    a message nothing would remember."""
    wa_id = "wamid.ZZ-NOROW"
    body = _meta_body("book an eye test", wa_id=wa_id)
    with _outage(wa, "webhook_inbox", "insert_one"):
        assert _post_inbound(client, body).status_code == 503
    assert wa.follow_ups.count_documents({}) == 0
    assert wa.whatsapp_conversations.count_documents({}) == 0
    assert wa.webhook_inbox.count_documents({}) == 0

    assert _post_inbound(client, body).status_code == 200
    _assert_applied_once(wa, "BOOK", wa_id)


def test_two_messages_in_one_delivery_each_keep_their_own_state(client, wa):
    body = _meta_body("book an eye test", wa_id="wamid.ZZ-PAIR-1")
    value = body["entry"][0]["changes"][0]["value"]
    value["messages"].append({**value["messages"][0], "id": "wamid.ZZ-PAIR-2",
                              "text": {"body": "I need an agent please"}})
    calls = {"n": 0}
    real = whatsapp_intents.dispatch_intent

    async def second_fails(**kw):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("only the second message fails")
        return await real(**kw)

    with patch.object(whatsapp_intents, "dispatch_intent", new=second_fails):
        first = _post_inbound(client, body).json()["results"]
    assert first[0]["intent"] == "BOOK" and "error" in first[1]

    again = _post_inbound(client, body).json()["results"]
    assert again == [{"duplicate": True, "phone": "3210"},
                     {"duplicate": True, "phone": "3210", "redispatched": True}]
    assert wa.follow_ups.count_documents({}) == 1
    thread = wa.whatsapp_conversations.find_one({"phone": PHONE})
    assert thread["needs_human"] is True
    assert [m["wa_message_id"] for m in thread["messages"]] == ["wamid.ZZ-PAIR-1", "wamid.ZZ-PAIR-2"]
    assert {r["status"] for r in wa.webhook_inbox.find({"vendor": "whatsapp"})} == {"succeeded"}


def test_reply_goes_out_at_most_once_and_only_after_the_intent_is_marked(client, wa, monkeypatch):
    """With auto-reply allowed (policy stubbed; the provider is still the
    stub), the reply follows the "applied" mark -- so a run that fails before
    the mark sends nothing, and the re-run sends exactly one."""
    monkeypatch.setattr(whatsapp_intents, "auto_reply_allowed", lambda _s: (True, "test"))
    sent = AsyncMock(return_value=MagicMock(ok=True, status="SIMULATED"))
    wa_id = "wamid.ZZ-REPLY"
    body = _meta_body("book an eye test", wa_id=wa_id)
    with patch.object(providers, "send_whatsapp", new=sent):
        with _outage(wa, "webhook_inbox", "update_one"):
            _post_inbound(client, body)
        assert sent.await_count == 0  # effect stored, mark failed: no reply yet
        _age_the_lease(wa, wa_id)
        asyncio.run(NexusAgent(db=wa)._retry_failed_webhooks())
        assert sent.await_count == 1
        _post_inbound(client, body)
        asyncio.run(NexusAgent(db=wa)._retry_failed_webhooks())
        assert sent.await_count == 1
    assert wa.follow_ups.count_documents({}) == 1



# ===========================================================================
# L1-F24 -- the WhatsApp inbox is shop-scoped
# ===========================================================================

INBOX = "/api/v1/webhooks/whatsapp/conversations"


def _thread(db, phone, text, **extra):
    db.whatsapp_conversations.insert_one(
        {
            "phone": phone,
            "customer_name": f"name-{text}",
            "needs_human": False,
            "last_message_at": datetime.now(timezone.utc),
            "messages": [{"wa_message_id": f"wamid.{text}", "text": text, "direction": "inbound"}],
            **extra,
        }
    )


@pytest.fixture
def inbox(mongo):
    db = mongo.real
    db.customers.insert_many(
        [
            {"customer_id": "C-A", "mobile": "9000000001", "home_store_id": "ST-A"},
            {"customer_id": "C-B", "mobile": "9000000002", "home_store_id": "ST-B"},
            # One number, two shops' customer records (a shared family phone).
            {"customer_id": "C-S1", "mobile": "9000000003", "home_store_id": "ST-A"},
            {"customer_id": "C-S2", "mobile": "9000000003", "home_store_id": "ST-B"},
            # Homed only through a key the customer rule also reads.
            {"customer_id": "C-P", "mobile": "9000000005", "preferred_store_id": "ST-A"},
        ]
    )
    _thread(db, "9000000001", "private-A")
    # Threads written before this fix carry NO customer id (L1-F30): the shop
    # is derived from the phone at read time, never from a stored field.
    _thread(db, "9000000002", "private-B", needs_human=True, customer_id=None)
    _thread(db, "9000000003", "private-SHARED")
    _thread(db, "9000000004", "private-UNKNOWN")  # nobody's customer
    _thread(db, "9000000005", "private-PREFERRED")
    return db


def _seen(client, headers, **params):
    r = client.get(INBOX, headers=headers, params=params)
    assert r.status_code == 200, r.text
    body = r.json()
    texts = sorted(m["text"] for c in body["conversations"] for m in c["messages"])
    assert body["total"] == len(texts)
    return texts


def test_shop_manager_sees_only_their_own_shops_chats(client, inbox):
    """The audit reproduction: shop A's manager received shop B's thread."""
    seen = _seen(client, _headers(["STORE_MANAGER"], ["ST-A"]))
    assert seen == ["private-A", "private-PREFERRED", "private-SHARED"]
    assert "private-B" not in json.dumps(
        client.get(INBOX, headers=_headers(["STORE_MANAGER"], ["ST-A"])).json()
    )


def test_other_shop_manager_sees_the_mirror_image(client, inbox):
    assert _seen(client, _headers(["STORE_MANAGER"], ["ST-B"])) == ["private-B", "private-SHARED"]


def test_multi_shop_manager_sees_the_union_but_never_unowned_threads(client, inbox):
    seen = _seen(client, _headers(["STORE_MANAGER"], ["ST-A", "ST-B"]))
    assert seen == ["private-A", "private-B", "private-PREFERRED", "private-SHARED"]


@pytest.mark.parametrize("role", ["ADMIN", "SUPERADMIN"])
def test_head_office_keeps_the_whole_inbox_including_unknown_numbers(client, inbox, role):
    assert _seen(client, _headers([role], ["ST-A"])) == [
        "private-A",
        "private-B",
        "private-PREFERRED",
        "private-SHARED",
        "private-UNKNOWN",
    ]


def test_manager_with_no_shop_sees_nothing(client, inbox):
    assert _seen(client, _headers(["STORE_MANAGER"], [])) == []


def test_scope_composes_with_the_needs_human_filter_and_paging(client, inbox):
    a = _headers(["STORE_MANAGER"], ["ST-A"])
    b = _headers(["STORE_MANAGER"], ["ST-B"])
    # Only shop B's thread is flagged: A's flagged list is empty, not B's.
    assert _seen(client, a, needs_human="true") == []
    assert _seen(client, b, needs_human="true") == ["private-B"]
    # The total is the scoped total on every page, not the chain-wide one.
    page = client.get(INBOX, headers=a, params={"limit": 1, "offset": 0}).json()
    assert page["total"] == 3 and len(page["conversations"]) == 1


def test_inbox_role_gate_is_unchanged(client, inbox):
    assert client.get(INBOX, headers=_headers(["SALES_STAFF"], ["ST-A"])).status_code == 403
    assert client.get(INBOX).status_code == 401


# ===========================================================================
# L1-F26 -- a failed MSG91 handler stays retryable
# ===========================================================================

MSG91_SECRET = "zz_b02_msg91_webhook_secret"
MSG91 = "/api/v1/integrations/msg91/webhooks"


@pytest.fixture
def msg91(mongo, monkeypatch):
    monkeypatch.setenv("MSG91_WEBHOOK_TOKEN", MSG91_SECRET)
    mongo.real.notification_logs.insert_one(
        {
            "notification_id": "NTF-ZZ-1",
            "store_id": "ST-A",
            "customer_phone": "+91 9812345678",
            "template_id": "ORDER_DELIVERED",
            "channel": "WHATSAPP",
            "status": "SENT",
            "provider_msg_id": "REQ-ZZ-1",
            "delivery_status": "SENT",
        }
    )
    return mongo


def _post_msg91(client, channel, payload):
    raw = json.dumps(payload).encode()
    sig = hmac.new(MSG91_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return client.post(
        f"{MSG91}/{channel}",
        content=raw,
        headers={"content-type": "application/json", "x-msg91-signature": sig},
    )


def _row(db):
    rows = list(db.webhook_inbox.find({"vendor": "msg91"}))
    assert len(rows) == 1, rows
    return rows[0]


def _nexus_drains_redeliveries(db):
    """Stand-in for the event bus: a re-dispatch runs NEXUS's real drain."""

    async def _dispatch(webhook_id, vendor):
        await NexusAgent(db=db)._handle_inbox_webhook(webhook_id, vendor)
        return True

    return patch.object(webhooks, "_dispatch_webhook_received", side_effect=_dispatch)


DELIVERED = {"request_id": "REQ-ZZ-1", "status": "delivered"}


def test_failed_handler_is_not_marked_processed_and_a_redelivery_retries_it(client, msg91):
    """The audit reproduction, end to end over signed HTTP: storage fails
    after the inbox row is written; the row must stay unfinished, and the
    same report replayed after recovery must be applied -- exactly once."""
    db = msg91.real
    msg91.down = {"notification_logs": {"update_many"}}
    r = _post_msg91(client, "whatsapp", DELIVERED)
    assert r.status_code == 200 and r.json()["status"] == "received"
    assert r.json()["processed"] is False
    row = _row(db)
    assert row["processed"] is False  # main: True
    assert row["status"] == "failed"
    assert "log_update_failed" in row["handler_error"]
    assert row["attempts"] == 1
    assert db.notification_logs.find_one({})["delivery_status"] == "SENT"

    msg91.down = {}  # storage recovers
    with _nexus_drains_redeliveries(db) as dispatch:
        r = _post_msg91(client, "whatsapp", DELIVERED)
    assert r.status_code == 200 and r.json()["status"] == "duplicate"
    assert r.json()["redispatched"] is True  # main: never retried
    dispatch.assert_called_once()
    row = _row(db)
    assert row["processed"] is True and row["status"] == "succeeded"
    assert row["attempts"] == 2 and "handler_error" not in row
    assert db.notification_logs.find_one({})["delivery_status"] == "DELIVERED"
    assert db.message_events.count_documents({}) == 1

    # A finished row is inert: a further replay neither re-runs nor recounts.
    with _nexus_drains_redeliveries(db) as dispatch:
        r = _post_msg91(client, "whatsapp", DELIVERED)
    assert r.json()["status"] == "duplicate" and "redispatched" not in r.json()
    dispatch.assert_not_called()
    assert db.message_events.count_documents({}) == 1


def test_hourly_sweep_recovers_a_failed_row_without_any_redelivery(client, msg91):
    """Automatic recovery: MSG91 got its 200 and will not resend, so the
    background sweep must finish the row. On main the sweep saw vendor=msg91
    as unknown and parked the row processed."""
    db = msg91.real
    msg91.down = {"message_events": {"insert_one"}}
    assert _post_msg91(client, "whatsapp", DELIVERED).json()["processed"] is False
    assert _row(db)["status"] == "failed"
    assert db.message_events.count_documents({}) == 0

    msg91.down = {}
    asyncio.run(NexusAgent(db=db)._retry_failed_webhooks())
    row = _row(db)
    assert row["processed"] is True and row["status"] == "succeeded"
    assert row.get("handler_error") != "unknown_vendor:msg91"
    assert row["outcome"]["events_recorded"] == 1
    assert db.message_events.count_documents({}) == 1


def test_partial_batch_is_completed_on_retry_without_double_counting(client, msg91):
    """Two reports in one delivery; the second one's write fails. The retry
    finishes the second and leaves the first counted once."""
    db = msg91.real
    db.notification_logs.insert_one(
        {"provider_msg_id": "REQ-ZZ-2", "channel": "WHATSAPP", "delivery_status": "SENT",
         "customer_phone": "9812345679", "store_id": "ST-A"}
    )
    inserts = {"n": 0}

    class _SecondInsertFails(_Failing):
        def __init__(self, coll):
            super().__init__(coll, set())

        def insert_one(self, doc, *a, **k):
            inserts["n"] += 1
            if inserts["n"] == 2:
                raise RuntimeError("synthetic storage outage")
            return self._coll.insert_one(doc, *a, **k)

    batch = [DELIVERED, {"request_id": "REQ-ZZ-2", "status": "read"}]
    msg91.down = {"message_events": _SecondInsertFails}
    assert _post_msg91(client, "whatsapp", batch).json()["processed"] is False
    msg91.down = {}
    assert _row(db)["status"] == "failed"
    assert db.message_events.count_documents({}) == 1  # the first report landed

    asyncio.run(NexusAgent(db=db)._retry_failed_webhooks())
    assert _row(db)["status"] == "succeeded"
    events = sorted((e["provider_message_id"], e["event"]) for e in db.message_events.find({}))
    assert events == [("REQ-ZZ-1", "delivered"), ("REQ-ZZ-2", "read")]


def test_run_that_died_before_its_final_mark_is_picked_up_after_the_lease(client, msg91):
    """The effects landed but the row's outcome write did not (process death):
    the row is held as in flight, then re-run once the lease is over."""
    db = msg91.real
    msg91.down = {"webhook_inbox": {"update_one"}}
    assert _post_msg91(client, "whatsapp", DELIVERED).status_code == 200
    msg91.down = {}
    row = _row(db)
    assert row["processed"] is False and row["status"] == "processing"

    # Inside the lease a redelivery does not start a second run beside it.
    with _nexus_drains_redeliveries(db) as dispatch:
        assert _post_msg91(client, "whatsapp", DELIVERED).json()["status"] == "duplicate"
    dispatch.assert_not_called()

    stale = datetime.now(timezone.utc) - NexusAgent.WEBHOOK_PROCESSING_LEASE - timedelta(minutes=1)
    db.webhook_inbox.update_one({"webhook_id": row["webhook_id"]}, {"$set": {"processing_at": stale}})
    asyncio.run(NexusAgent(db=db)._retry_failed_webhooks())
    assert _row(db)["status"] == "succeeded"
    assert db.message_events.count_documents({}) == 1  # re-run, not re-counted


def test_a_failed_ad_attribution_stamp_is_retried_too(client, msg91):
    db = msg91.real
    db.customers.insert_one({"customer_id": "C-CTWA", "mobile": "9812345678"})
    inbound = {
        "entry": [{"changes": [{"value": {"messages": [
            {"from": "919812345678", "id": "wamid.ZZ-AD",
             "referral": {"source_id": "AD-ZZ-1", "source_type": "ad", "ctwa_clid": "CLID-ZZ"}}
        ]}}]}]
    }
    msg91.down = {"customers": {"update_one"}}
    assert _post_msg91(client, "whatsapp", inbound).json()["processed"] is False
    assert _row(db)["status"] == "failed"

    msg91.down = {}
    asyncio.run(NexusAgent(db=db)._retry_failed_webhooks())
    assert _row(db)["status"] == "succeeded"
    assert db.customers.find_one({})["ads_attribution"]["touches"] == 1


def test_success_path_and_old_rows_are_unchanged(client, msg91):
    db = msg91.real
    r = _post_msg91(client, "whatsapp", DELIVERED)
    assert r.json()["status"] == "received" and r.json()["events_recorded"] == 1
    assert r.json()["processed"] is True
    row = _row(db)
    assert row["processed"] is True and row["processed_at"] and row["status"] == "succeeded"
    assert row["events_recorded"] == 1

    # A row written before this fix (processed, no status) replays as before.
    db.webhook_inbox.update_one({"webhook_id": row["webhook_id"]}, {"$unset": {"status": ""}})
    with _nexus_drains_redeliveries(db) as dispatch:
        assert _post_msg91(client, "whatsapp", DELIVERED).json()["status"] == "duplicate"
    dispatch.assert_not_called()


def test_a_report_that_matches_no_log_row_is_not_a_failure(client, msg91):
    """Unknown message id: nothing to advance, the event is still recorded --
    a finished delivery, never a retry loop."""
    r = _post_msg91(client, "sms", {"request_id": "REQ-ZZ-NOBODY", "status": "delivered"})
    assert r.json()["processed"] is True
    assert _row(msg91.real)["status"] == "succeeded"


# The sibling door, POST /api/v1/webhooks/msg91/delivery. It keeps NO inbox
# row, so nothing in IMS can retry for it: the only recovery is MSG91's own
# resend, which happens only on a non-2xx answer.


def _post_legacy(client, payload):
    raw = json.dumps(payload).encode()
    sig = hmac.new(MSG91_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return client.post(
        "/api/v1/webhooks/msg91/delivery",
        content=raw,
        headers={"content-type": "application/json", "x-msg91-signature": sig},
    )


@pytest.mark.parametrize(
    "outage",
    [
        {"notification_logs": {"update_many"}},  # the log advance fails
        {"message_events": {"insert_one"}},  # the spine event fails
    ],
)
def test_legacy_delivery_door_never_acknowledges_a_report_it_could_not_store(
    client, msg91, outage
):
    db = msg91.real
    msg91.down = outage
    r = _post_legacy(client, DELIVERED)
    assert r.status_code == 503  # was: 200 "received", updated 0 -- lost for good
    assert db.webhook_inbox.count_documents({}) == 0  # this door has no retry row

    msg91.down = {}  # storage recovers; MSG91 resends after the 503
    r = _post_legacy(client, DELIVERED)
    assert r.status_code == 200 and r.json()["delivery_status"] == "DELIVERED"
    assert db.notification_logs.find_one({})["delivery_status"] == "DELIVERED"
    assert db.message_events.count_documents({}) == 1

    # A further resend is harmless: one event, still a 200.
    assert _post_legacy(client, DELIVERED).status_code == 200
    assert db.message_events.count_documents({}) == 1


def test_legacy_delivery_door_without_a_database_asks_for_a_resend(client, msg91, monkeypatch):
    from api.services import message_events

    monkeypatch.setattr(message_events, "_get_db", lambda: None)
    assert _post_legacy(client, DELIVERED).status_code == 503


def test_legacy_delivery_door_healthy_and_unknown_ids_still_answer_200(client, msg91):
    r = _post_legacy(client, DELIVERED)
    assert r.status_code == 200 and r.json()["updated"] == 1
    # Matching no log row is not a storage failure: no retry loop.
    r = _post_legacy(client, {"request_id": "REQ-ZZ-NOBODY", "status": "delivered"})
    assert r.status_code == 200 and r.json()["updated"] == 0


# ===========================================================================
# L1-F31 -- one canonical provider number per Indian mobile
# ===========================================================================


@pytest.mark.parametrize(
    "typed",
    [
        "9123456789",  # the audit case: national number that begins with 91
        "09123456789",  # trunk zero
        "+91 9123456789",
        "919123456789",
        "+91-91234-56789",
        "0919123456789",
    ],
)
def test_every_spelling_of_a_national_91_number_is_one_provider_number(typed):
    assert providers._normalize_phone(typed) == "919123456789"


def test_ordinary_numbers_are_unchanged():
    for typed in ("9876543210", "09876543210", "+919876543210", "919876543210"):
        assert providers._normalize_phone(typed) == "919876543210"


@pytest.mark.parametrize(
    "bad", ["", "   ", "98765", "12345678901234", "5123456789", "1234567890", "91912345678"]
)
def test_an_unusable_number_is_refused_not_passed_through(bad):
    assert providers._normalize_phone(bad) == ""
    res = asyncio.run(providers.send_sms(bad, "hello"))
    assert res.ok is False and res.error == "invalid phone"


def test_every_send_door_hands_the_canonical_number_to_the_dispatch_gate(monkeypatch):
    """WhatsApp, SMS, voice and OTP all consume the one normalizer. Observed
    at the dispatch gate, which stays shut: nothing is sent anywhere."""
    seen = []

    def gate(phone):
        seen.append(phone)
        return False, "test gate (dispatch stays off)"

    monkeypatch.setattr(providers, "_should_dispatch", gate)
    for send in (
        lambda: providers.send_whatsapp("9123456789", "hi", template_id="ORDER_DELIVERED"),
        lambda: providers.send_sms("09123456789", "hi"),
        lambda: providers.send_voice_call("+91 9123456789", "hi"),
        lambda: providers.send_otp("9123456789", "123456"),
    ):
        res = asyncio.run(send())
        assert res.status == "SIMULATED", res
    assert seen == ["919123456789"] * 4


def test_test_recipient_matching_uses_the_same_normalizer(monkeypatch):
    monkeypatch.setattr(providers, "DISPATCH_MODE", "test")
    monkeypatch.setattr(providers, "TEST_PHONE", "9123456789")  # typed bare
    assert providers._should_dispatch("919123456789")[0] is True
    assert providers._should_dispatch("+91 91234 56789")[0] is True
    assert providers._should_dispatch("919876543210")[0] is False
    monkeypatch.setattr(providers, "TEST_PHONE", "not-a-number")
    assert providers._should_dispatch("919123456789")[0] is False


def test_portal_otp_lookup_uses_the_send_normalizer_not_a_copy():
    assert portal._normalize_phone is providers._normalize_phone


# ===========================================================================
# L1-F35 -- every ONDC callback door verifies before it acts or discloses
# ===========================================================================

UKP = "zz_b02_ondc_signing_key"
DOORS = ["on_search", "on_select", "on_init", "on_confirm", "on_status", "on_cancel"]
ONDC_BODY = {
    "context": {"transaction_id": "TXN-ZZ-1"},
    "message": {"order": {"id": "ONDC-ZZ-1"}},
}


@pytest.fixture
def ondc_world(monkeypatch):
    """A configured signing key, one ONDC order, a stub publisher and a spy on
    order ingestion. ONDC itself is OFF, as it is by default."""
    monkeypatch.delenv("IMS_ONDC_ENABLED", raising=False)
    db = mongomock.MongoClient()["zz_b02_ondc"]
    db.integrations.insert_one({"type": "ondc", "enabled": True, "config": {"ukp": UKP}})
    db.orders.insert_one(
        {"order_id": "ORD-ZZ-1", "external_order_id": "ONDC-ZZ-1", "channel": "ONDC",
         "status": "DELIVERED"}
    )
    monkeypatch.setattr(ondc, "_get_db", lambda: db)
    publish = AsyncMock(return_value={"ok": True, "mode": "SIMULATED", "item_count": 0})
    monkeypatch.setattr(ondc.ondc_seller, "publish_catalog", publish)
    ingest = MagicMock(return_value={"ok": True, "order_id": "ORD-ZZ-NEW", "mode": "SIMULATED"})
    monkeypatch.setattr(ondc.ondc_seller, "ingest_ondc_order", ingest)
    return db, publish, ingest


def _switch_ondc_on(monkeypatch):
    """The shared enabled rule answers "on" (a stand-in for the env flag plus
    stored SNP credentials; nothing real is armed and the publisher is a stub)."""
    monkeypatch.setattr(ondc.ondc_seller, "ondc_enabled", lambda _db: True)


def _call(client, door, signature=None):
    raw = json.dumps(ONDC_BODY).encode()
    headers = {"Content-Type": "application/json"}
    if signature == "valid":
        headers["Authorization"] = "signature=" + hmac.new(UKP.encode(), raw, hashlib.sha256).hexdigest()
    elif signature:
        headers["Authorization"] = f"signature={signature}"
    return client.post(f"/api/v1/ondc/{door}", content=raw, headers=headers).json()


def _assert_refused(reply, world, why="Invalid signature"):
    db, publish, ingest = world
    assert reply["message"] == {"ack": {"status": "NACK"}}
    assert reply["error"]["message"] == why
    assert "DELIVERED" not in json.dumps(reply)
    publish.assert_not_called()
    ingest.assert_not_called()
    assert db.orders.find_one({})["status"] == "DELIVERED"
    assert db.orders.count_documents({}) == 1
    assert db.audit_logs.count_documents({}) == 0


@pytest.mark.parametrize("door", DOORS)
@pytest.mark.parametrize("signature", [None, "0" * 64])
@pytest.mark.parametrize("switched_on", [False, True], ids=["ondc_off", "ondc_on"])
def test_unsigned_or_wrongly_signed_callback_is_refused_at_every_door(
    client, ondc_world, monkeypatch, door, signature, switched_on
):
    """The audit reproduction: on_search still published and on_status still
    disclosed the order after the signature check failed. Refused whether
    ONDC is off or on, and never told which."""
    if switched_on:
        _switch_ondc_on(monkeypatch)
    _assert_refused(_call(client, door, signature), ondc_world)


@pytest.mark.parametrize("door", DOORS)
def test_no_signing_key_configured_refuses_every_door(client, ondc_world, monkeypatch, door):
    """Missing configuration fails closed, even for a 'correctly' signed call."""
    _switch_ondc_on(monkeypatch)
    ondc_world[0].integrations.delete_many({})
    _assert_refused(_call(client, door, "valid"), ondc_world)


@pytest.mark.parametrize("door", DOORS)
def test_correctly_signed_callback_is_refused_at_every_door_while_ondc_is_off(
    client, ondc_world, door
):
    """D20 / 1416-P2c, Codex's native-route probe: with the enable flag off a
    signed on_search still called the publisher, on_status answered with an
    order state and on_confirm / on_cancel ACKed. Off means every door NACKs
    and nothing is published, read, ingested, cancelled or audited."""
    _assert_refused(_call(client, door, "valid"), ondc_world, why="ONDC is switched off")


def test_the_env_flag_alone_does_not_open_the_doors(client, ondc_world, monkeypatch):
    """The gate is the SHARED rule (flag AND stored SNP credentials), not a
    second reading of the flag: this config has a signing key but no SNP."""
    monkeypatch.setenv("IMS_ONDC_ENABLED", "1")
    for door in DOORS:
        _assert_refused(_call(client, door, "valid"), ondc_world, why="ONDC is switched off")


def test_signed_callbacks_work_once_ondc_is_switched_on(client, ondc_world, monkeypatch):
    """The gate refuses nothing it should let through."""
    db, publish, ingest = ondc_world
    _switch_ondc_on(monkeypatch)
    assert _call(client, "on_search", "valid")["message"]["ack"]["status"] == "ACK"
    publish.assert_awaited_once()
    status = _call(client, "on_status", "valid")
    assert status["message"]["order"] == {"id": "ONDC-ZZ-1", "state": "DELIVERED"}
    for door in ("on_select", "on_init", "on_confirm"):
        assert _call(client, door, "valid")["message"]["ack"]["status"] == "ACK"
    ingest.assert_called_once()
    assert _call(client, "on_cancel", "valid")["message"]["ack"]["status"] == "ACK"
    assert db.orders.find_one({})["status"] == "CANCELLED"
