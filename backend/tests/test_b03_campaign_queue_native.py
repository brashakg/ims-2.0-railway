"""PR #1419 round 2 (Codex PR-1419-round1.md + decision D56), on NATIVE Mongo.

In-memory doubles hid these defects (no unique index, no real insert errors),
so every test here runs the real queue writer, the real campaign fan-out, the
real campaign doors, the real MEGAPHONE drain / scheduled tick / reminder-rule
runner and the real inbound STOP door against a throwaway database on a real
mongod (MONGO_HOST / MONGO_PORT, default 127.0.0.1:27017 as in CI). Only the
provider call is a recording spy. Skips when no mongod answers.

  P2-category   a promotional template stamped SERVICE / no category still
                meets the STOP check at the drain (one shared rule, D56)
  P2-ownership  a ONE_TIME campaign reaches a recipient once across the
                scheduled tick, Send now, a retry and a lost reply
  P2-durability a queue row that did not reach storage is never "queued",
                consumes no cap, and the campaign stays open for a retry
  D56           unfilled placeholder never queued; DAILY 09:00 fires once per
                IST day at/after 09:00; manual REMINDER is promotional
  dependency    inbound WhatsApp STOP is recorded on a real database (L1-F30)
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

from agents.implementations import megaphone as mega_mod  # noqa: E402
from api.routers import campaigns as camp_mod  # noqa: E402
from api.routers import marketing as mkt_mod  # noqa: E402
from api.services import campaign_segments as seg  # noqa: E402
from api.services import notification_service as ns  # noqa: E402
from api.services import notification_templates as nt  # noqa: E402
from api.services import reminder_rail as rail  # noqa: E402
from api.services import whatsapp_intents as wi  # noqa: E402

MONGO_HOST = os.getenv("MONGO_HOST", "127.0.0.1")
MONGO_PORT = int(os.getenv("MONGO_PORT", "27017"))
PHONE = "9123400001"
CID = "ZZ-B03-C1"
CAMP = "ZZ-B03-CAMP"
USER = {"user_id": "ZZ-MGR", "roles": ["STORE_MANAGER"], "store_ids": ["ZZ-S1"], "active_store_id": "ZZ-S1"}


@pytest.fixture
def db(monkeypatch):
    try:
        from pymongo import MongoClient

        client = MongoClient(MONGO_HOST, MONGO_PORT, serverSelectionTimeoutMS=1500)
        client.admin.command("ping")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"no mongod at {MONGO_HOST}:{MONGO_PORT} ({exc})")
    name = f"ims_claude_t1419_{uuid.uuid4().hex[:12]}"
    database = client[name]
    assert database.name == name
    database.customers.insert_one(
        {"customer_id": CID, "name": "Zz", "mobile": PHONE, "store_id": "ZZ-S1", "marketing_consent": True}
    )
    database.stores.insert_one({"store_id": "ZZ-S1", "store_name": "ZZ Optics", "phone": "9000000099"})
    monkeypatch.setattr(ns, "_get_db", lambda: database)
    monkeypatch.setattr(nt, "_get_db", lambda: database)
    monkeypatch.setattr(wi, "_get_db", lambda: database)
    monkeypatch.setattr(mkt_mod, "_get_db", lambda: database)
    monkeypatch.setattr(camp_mod, "_marketing_get_db", lambda: database)
    monkeypatch.setattr(camp_mod, "_check_notification_rate", lambda *_a, **_k: None)
    monkeypatch.setattr(camp_mod, "_enforce_promo_window", lambda *_a, **_k: None)
    monkeypatch.setattr(mega_mod.MegaphoneAgent, "_in_dnd_window", lambda *_a, **_k: False)
    monkeypatch.setattr(mega_mod, "_shared_in_quiet_hours", lambda *_a, **_k: False)
    monkeypatch.setattr(
        seg, "resolve_segment", lambda *_a, **_k: [{"customer_id": CID, "phone": PHONE, "name": "Zz"}]
    )
    yield database
    client.drop_database(name)
    client.close()


@pytest.fixture
def provider(monkeypatch):
    calls = []

    async def _spy(phone, *_a, **_k):
        calls.append(phone)
        return SimpleNamespace(ok=True, status="SIMULATED", provider_id="SPY", error=None)

    monkeypatch.setattr(mega_mod, "send_whatsapp", _spy)
    return calls


def _drain(db):
    return asyncio.run(mega_mod.MegaphoneAgent(db=db)._drain_pending(db.notification_logs))


def _stop(db):
    db.customers.update_one({"customer_id": CID}, {"$set": {"marketing_consent": False}})


def _campaign(db, kind="ONE_TIME", status="SCHEDULED"):
    doc = {
        "campaign_id": CAMP,
        "name": "zz",
        "status": status,
        "template_id": "BIRTHDAY_WISH",
        "channels": ["WHATSAPP"],
        "store_id": "ZZ-S1",
        "segment_key": "by_store",
        "segment_params": {},
        "schedule": {"kind": kind, "send_at": "2020-01-01T00:00:00+00:00"},
    }
    db.campaigns.insert_one(dict(doc))
    return doc


def _rows(db):
    return list(db.notification_logs.find({"related_entity_id": CAMP}))


def _caps(db):
    return list(db.comms_ledger.find({"campaign_id": CAMP}))


def _send_now():
    return asyncio.run(camp_mod.send_campaign(CAMP, current_user=dict(USER)))


def _tick(db):
    return asyncio.run(mega_mod.MegaphoneAgent(db=db)._dispatch_scheduled_campaigns())


# --------------------------------------------------------------------------
# P2-category + D56 classification
# --------------------------------------------------------------------------


@pytest.mark.parametrize("category", [None, "SERVICE", "TRANSACTIONAL", "REMINDER"])
def test_promotional_template_with_any_category_is_cancelled_after_stop(db, provider, category):
    kw = {"category": category} if category else {}
    row = asyncio.run(
        ns.send_notification(
            store_id="ZZ-S1", customer_id=CID, customer_phone=PHONE, customer_name="Zz",
            template_id="BIRTHDAY_WISH", **kw,
        )
    )
    assert row["persisted"] is True and row["consent_basis"] == "marketing_consent"
    _stop(db)
    _drain(db)
    assert provider == []
    assert db.notification_logs.find_one({"notification_id": row["notification_id"]})["status"] == "CANCELLED"


def test_a_stored_misclassified_row_still_meets_stop_at_the_drain(db, provider):
    """A row queued before the rule (Codex's exact shape: BIRTHDAY_WISH,
    category SERVICE, consent_basis transactional) is re-derived at the drain."""
    db.notification_logs.insert_one(
        {"notification_id": "ZZ-OLD", "status": "PENDING", "channel": "WHATSAPP", "customer_id": CID,
         "customer_phone": PHONE, "template_id": "BIRTHDAY_WISH", "category": "SERVICE",
         "consent_basis": "transactional", "message": "m", "scheduled_for": None}
    )
    _stop(db)
    _drain(db)
    assert provider == []
    assert db.notification_logs.find_one({"notification_id": "ZZ-OLD"})["status"] == "CANCELLED"


def test_genuine_service_message_still_reaches_an_opted_out_customer(db, provider):
    row = asyncio.run(
        ns.send_notification(
            store_id="ZZ-S1", customer_id=CID, customer_phone=PHONE, customer_name="Zz",
            template_id="ORDER_DELIVERED", variables={"order_number": "BV-1"}, category="SERVICE",
            related_entity_type="order", related_entity_id="O-1",
        )
    )
    _stop(db)
    _drain(db)
    assert provider == [PHONE] and row["consent_basis"] == "transactional"


@pytest.mark.parametrize(
    "args,basis",
    [
        (("SERVICE", "BIRTHDAY_WISH"), "marketing_consent"),
        ((None, "BIRTHDAY_WISH"), "marketing_consent"),
        # D56 literal: a recall reminder tied to ONE Rx / order / appointment
        # is transactional; the same template any other way is promotional.
        (("REMINDER", "PRESCRIPTION_EXPIRY", "prescription", "RX-1"), "transactional"),
        (("REMINDER", "ANNUAL_CHECKUP_REMINDER", "appointment", "A-1"), "transactional"),
        (("SERVICE", "PRESCRIPTION_EXPIRY", None, None), "marketing_consent"),
        ((None, "PRESCRIPTION_EXPIRY", "prescription", "RX-1"), "marketing_consent"),
        (("MARKETING", "PRESCRIPTION_EXPIRY", "prescription", "C-1"), "marketing_consent"),
        (("OTP", "ANNUAL_CHECKUP_REMINDER", None, None), "marketing_consent"),
        (("REMINDER", "CL_REORDER_REMINDER", "cl_subscription", "C-1"), "marketing_consent"),
        (("REMINDER", "PRESCRIPTION_EXPIRY", "reminder_rule", "R-1"), "marketing_consent"),
        (("REMINDER", "BIRTHDAY_WISH", "order", "O-1"), "marketing_consent"),
        (("OTP", "BIRTHDAY_WISH", "reminder_rule", "R-1"), "marketing_consent"),
        (("OTP", "X_RULE_TPL", "reminder_rule", "R-1"), "marketing_consent"),
        (("REMINDER", "APPT_TPL", "appointment", "A-1"), "transactional"),
        (("REMINDER", "APPT_TPL", "appointment", None), "marketing_consent"),
        (("REMINDER", "ANY", None, None), "marketing_consent"),
        (("SERVICE", "ORDER_DELIVERED", "order", "O-1"), "transactional"),
        (("OTP", "POOL_REDEEM_OTP", "pool_otp", "P-1"), "transactional"),
        ((None, None), "marketing_consent"),
    ],
)
def test_one_consent_rule_d56(args, basis):
    assert ns.consent_basis_for(*args) == basis


def test_a_caller_cannot_downgrade_a_promotional_row_with_consent_basis(db):
    row = asyncio.run(
        ns.send_notification(
            store_id="ZZ-S1", customer_id=CID, customer_phone=PHONE, customer_name="Zz",
            template_id="BIRTHDAY_WISH", category="SERVICE", consent_basis="transactional",
        )
    )
    assert row["consent_basis"] == "marketing_consent"


# --------------------------------------------------------------------------
# D56: unfilled placeholder never queued
# --------------------------------------------------------------------------


def test_a_message_with_an_unfilled_placeholder_is_never_queued(db):
    out = asyncio.run(
        ns.send_notification(
            store_id="ZZ-S1", customer_id=CID, customer_phone=PHONE, customer_name="Zz",
            template_id="ORDER_DELIVERED", variables={}, category="SERVICE",
        )
    )
    assert out["status"] == "REFUSED" and out["persisted"] is False
    assert out["missing_variables"] == ["order_number"]
    assert db.notification_logs.count_documents({}) == 0


def test_the_sending_shops_name_is_filled_from_its_store_record(db):
    row = asyncio.run(
        ns.send_notification(
            store_id="ZZ-S1", customer_id=CID, customer_phone=PHONE, customer_name="Zz",
            template_id="BIRTHDAY_WISH", category="MARKETING",
        )
    )
    assert row["persisted"] is True and "ZZ Optics" in row["message"] and "{" not in row["message"]


def test_a_store_that_cannot_be_read_leaves_the_message_unqueued(db, monkeypatch):
    out = asyncio.run(
        ns.send_notification(
            store_id="ZZ-NO-SUCH-SHOP", customer_id=CID, customer_phone=PHONE, customer_name="Zz",
            template_id="BIRTHDAY_WISH", category="MARKETING",
        )
    )
    assert out["status"] == "REFUSED" and db.notification_logs.count_documents({}) == 0


# --------------------------------------------------------------------------
# P2-ownership: one ONE_TIME recipient, one message, whichever door
# --------------------------------------------------------------------------


def test_scheduled_tick_and_send_now_overlapping_queue_one_message(db, provider, monkeypatch):
    """Codex's overlap: the scheduled tick is paused INSIDE the queue writer
    (after its dedupe read found nothing), Send now runs to completion, then
    the tick resumes: the unique index settles it, one row, one cap row."""
    _campaign(db)
    real = ns.send_notification
    gate = {"held": True}

    async def _paused(**kw):
        if kw.get("triggered_by") == "MEGAPHONE" and gate["held"]:
            gate["held"] = False
            # the manual door runs to completion while the tick is mid-queue
            out = await camp_mod.send_campaign(CAMP, current_user=dict(USER))
            assert out["queued"] == 1
        return await real(**kw)

    monkeypatch.setattr(ns, "send_notification", _paused)
    assert _tick(db) == 1
    retry = _send_now()  # identical retry after a lost HTTP reply
    assert retry["queued"] == 0 and retry["already_queued"] == 1
    assert len(_rows(db)) == 1 and len(_caps(db)) == 1
    camp = db.campaigns.find_one({"campaign_id": CAMP})
    assert camp["status"] == "COMPLETED" and camp["sent_count"] == 1
    _drain(db)
    assert provider == [PHONE]


def test_two_doors_racing_past_the_dedupe_read_still_queue_once(db):
    """Both fan-outs pass the 'already queued?' read before either inserts:
    only the unique index can keep it to one row."""
    from api.services.campaign_dispatch import fan_out_campaign

    camp = _campaign(db, status="ACTIVE")
    real = ns.send_notification

    async def _both():
        first_in = asyncio.Event()
        second_in = asyncio.Event()

        async def _a(**kw):
            first_in.set()
            await second_in.wait()
            return await real(**kw)

        async def _b(**kw):
            await first_in.wait()
            second_in.set()
            return await real(**kw)

        return await asyncio.gather(
            fan_out_campaign(db, camp, send_store_id="ZZ-S1", triggered_by="A", send_notification=_a),
            fan_out_campaign(db, camp, send_store_id="ZZ-S1", triggered_by="B", send_notification=_b),
        )

    a, b = asyncio.run(_both())
    assert sorted([a["sent"], b["sent"]]) == [0, 1]
    assert sorted([a["already_queued"], b["already_queued"]]) == [0, 1]
    assert len(_rows(db)) == 1 and len(_caps(db)) == 1


def test_a_legacy_recurring_campaign_can_still_be_resent_by_hand(db):
    _campaign(db, kind="RECURRING", status="ACTIVE")
    assert _send_now()["queued"] == 1
    assert _send_now()["queued"] == 1  # deliberate re-send: no ref, two rows
    assert len(_rows(db)) == 2 and all("dedupe_ref" not in r for r in _rows(db))


def test_missing_unique_index_queues_nothing(db, monkeypatch):
    from pymongo.collection import Collection
    from fastapi import HTTPException

    _campaign(db, status="ACTIVE")
    real = Collection.create_index

    def _boom(self, *a, **k):
        if self.name == "notification_logs":
            raise OSError("index build refused")
        return real(self, *a, **k)

    monkeypatch.setattr(Collection, "create_index", _boom)
    with pytest.raises(HTTPException) as exc:
        _send_now()
    assert exc.value.status_code == 503 and _rows(db) == []


def test_dedupe_read_failure_is_a_failed_recipient_not_a_new_message(db, monkeypatch):
    from pymongo.collection import Collection

    _campaign(db, status="ACTIVE")
    real = Collection.find_one

    def _boom(self, flt=None, *a, **k):
        if self.name == "notification_logs" and isinstance(flt, dict) and "dedupe_ref" in flt:
            raise OSError("read refused")
        return real(self, flt, *a, **k)

    monkeypatch.setattr(Collection, "find_one", _boom)
    out = _send_now()
    assert out["queued"] == 0 and out["failed"] == 1 and out["status"] == "ACTIVE"
    assert _rows(db) == [] and _caps(db) == []


def test_a_stored_ist_offset_send_at_fires_when_due_not_hours_late(db, provider):
    """Hunt (IST vs UTC): a send_at stored as '+05:30' text was compared with a
    UTC 'now' string, so a campaign due 10 minutes ago waited 5.5 hours."""
    from datetime import timedelta, timezone

    doc = _campaign(db)
    ist = timezone(timedelta(hours=5, minutes=30))
    due = (datetime.now(timezone.utc) - timedelta(minutes=10)).astimezone(ist).isoformat()
    db.campaigns.update_one({"campaign_id": doc["campaign_id"]}, {"$set": {"schedule.send_at": due}})
    assert _tick(db) == 1
    assert db.campaigns.find_one({"campaign_id": CAMP})["status"] == "COMPLETED"
    assert len(_rows(db)) == 1


def test_a_send_at_without_a_zone_is_never_fired(db):
    doc = _campaign(db)
    db.campaigns.update_one({"campaign_id": doc["campaign_id"]}, {"$set": {"schedule.send_at": "2020-01-01T10:00:00"}})
    assert _tick(db) == 0
    assert db.campaigns.find_one({"campaign_id": CAMP})["status"] == "SCHEDULED" and _rows(db) == []


# --------------------------------------------------------------------------
# P2-durability: a row that did not reach storage is never "queued"
# --------------------------------------------------------------------------


def _fail_inserts(monkeypatch, *, land: bool):
    from pymongo.collection import Collection

    real = Collection.insert_one

    def _insert(self, doc, *a, **k):
        if self.name == "notification_logs" and doc.get("related_entity_id") == CAMP:
            if land:
                real(self, doc, *a, **k)
            raise OSError("injected: " + ("reply lost" if land else "write failed"))
        return real(self, doc, *a, **k)

    monkeypatch.setattr(Collection, "insert_one", _insert)


def test_send_now_with_a_failed_insert_reports_it_and_spends_no_cap(db, monkeypatch):
    _campaign(db, status="ACTIVE")
    with monkeypatch.context() as m:
        _fail_inserts(m, land=False)
        out = _send_now()
    assert out["queued"] == 0 and out["failed"] == 1 and out["status"] == "ACTIVE"
    assert _rows(db) == [] and _caps(db) == []
    again = _send_now()  # storage back: the retry queues it exactly once
    assert again["queued"] == 1 and again["status"] == "COMPLETED"
    assert _send_now()["already_queued"] == 1
    assert len(_rows(db)) == 1 and len(_caps(db)) == 1


def test_scheduled_tick_with_a_failed_insert_retries_next_tick(db, monkeypatch):
    _campaign(db)
    with monkeypatch.context() as m:
        _fail_inserts(m, land=False)
        _tick(db)
    camp = db.campaigns.find_one({"campaign_id": CAMP})
    assert camp["status"] == "SCHEDULED" and camp.get("sent_count", 0) == 0
    assert _rows(db) == [] and _caps(db) == []
    _tick(db)
    camp = db.campaigns.find_one({"campaign_id": CAMP})
    assert camp["status"] == "COMPLETED" and camp["sent_count"] == 1
    assert len(_rows(db)) == 1 and len(_caps(db)) == 1


def test_a_lost_acknowledgement_is_read_back_and_counted_once(db, monkeypatch):
    _campaign(db, status="ACTIVE")
    with monkeypatch.context() as m:
        _fail_inserts(m, land=True)
        out = _send_now()
    assert out["queued"] == 1 and out["status"] == "COMPLETED"
    assert _send_now()["already_queued"] == 1
    assert len(_rows(db)) == 1 and len(_caps(db)) == 1


def test_the_single_send_door_never_says_queued_for_a_lost_row(db, monkeypatch):
    from pymongo.collection import Collection
    from fastapi import HTTPException

    real = Collection.insert_one

    def _boom(self, doc, *a, **k):
        if self.name == "notification_logs":
            raise OSError("write failed")
        return real(self, doc, *a, **k)

    monkeypatch.setattr(Collection, "insert_one", _boom)
    with pytest.raises(HTTPException) as exc:
        mkt_mod._queued_or_raise(
            asyncio.run(
                ns.send_notification(
                    store_id="ZZ-S1", customer_id=CID, customer_phone=PHONE, customer_name="Zz",
                    template_id="ORDER_DELIVERED", variables={"order_number": "1"},
                )
            )
        )
    assert exc.value.status_code == 503


# --------------------------------------------------------------------------
# D56 cadence: DAILY 09:00 once per IST day at/after 09:00
# --------------------------------------------------------------------------


def _rule(db, cron="DAILY 09:00"):
    db.reminder_rules.insert_one(
        {"rule_id": "ZZ-RULE", "active": True, "store_id": "ZZ-S1", "segment_key": "by_store",
         "template_id": "BIRTHDAY_WISH", "channel": "WHATSAPP", "is_transactional": False,
         "trigger": {"kind": "CRON", "cron": cron, "event_key": None}, "deleted_at": None}
    )


def test_daily_0900_rule_fires_once_per_ist_day_at_or_after_0900(db):
    _rule(db)
    ticks = [
        "2026-11-16T02:30:00+00:00",  # 08:00 IST
        "2026-11-16T03:31:00+00:00",  # 09:01 IST
        "2026-11-16T04:00:00+00:00",  # 09:30 IST
        "2026-11-16T15:00:00+00:00",  # 20:30 IST
        "2026-11-16T19:00:00+00:00",  # 00:30 IST next day, before 09:00
        "2026-11-17T03:30:00+00:00",  # 09:00 IST next day
    ]
    counts = []
    for t in ticks:
        # a fresh agent each tick == a second worker; the claim is on the rule
        asyncio.run(mega_mod.MegaphoneAgent(db=db)._run_reminder_rules(datetime.fromisoformat(t)))
        counts.append(db.notification_logs.count_documents({"customer_id": CID}))
    assert counts == [0, 1, 1, 1, 1, 2]


@pytest.mark.parametrize(
    "cron,now,expected",
    [
        ("DAILY 09:00", "2026-11-16T03:29:00+00:00", None),
        ("DAILY 09:00", "2026-11-16T03:30:00+00:00", "D2026-11-16"),
        ("DAILY 09:00", "2026-11-16T20:00:00+00:00", None),  # 01:30 IST on the 17th
        ("DAILY 09:00", "2026-11-17T04:00:00+00:00", "D2026-11-17"),
        ("WEEKLY 10:00", "2026-11-16T04:00:00+00:00", None),  # Monday 09:30 IST
        ("WEEKLY 10:00", "2026-11-16T04:30:00+00:00", "W2026-47"),
        ("every day 9am", "2026-11-16T04:30:00+00:00", None),
        (None, "2026-11-16T04:30:00+00:00", None),
    ],
)
def test_cron_period_due(cron, now, expected):
    assert rail.cron_period_due(cron, datetime.fromisoformat(now)) == expected


def test_a_rule_schedule_that_would_never_fire_is_refused():
    from pydantic import ValidationError
    from api.routers.reminders import TriggerSpec

    assert TriggerSpec(kind="CRON", cron="daily 09:00").cron == "DAILY 09:00"
    with pytest.raises(ValidationError):
        TriggerSpec(kind="CRON", cron="every day 9am")


# --------------------------------------------------------------------------
# Dependency (L1-F30): inbound STOP is recorded on a real database
# --------------------------------------------------------------------------


def test_inbound_whatsapp_stop_is_recorded_and_cancels_queued_marketing(db, provider, monkeypatch):
    reply = _ReplySpy()
    from agents import providers

    monkeypatch.setattr(providers, "send_whatsapp", reply)  # the STOP confirmation
    row = asyncio.run(
        ns.send_notification(
            store_id="ZZ-S1", customer_id=CID, customer_phone=PHONE, customer_name="Zz",
            template_id="BIRTHDAY_WISH", category="MARKETING",
        )
    )
    asyncio.run(wi.dispatch_intent("91" + PHONE, "STOP", None, "ZZ-S1"))
    assert db.customers.find_one({"customer_id": CID})["marketing_consent"] is False
    assert wi.find_phone_stop(db, PHONE) is not None
    _drain(db)
    assert provider == []
    assert db.notification_logs.find_one({"notification_id": row["notification_id"]})["status"] == "CANCELLED"


class _ReplySpy:
    def __init__(self):
        self.calls = []

    async def __call__(self, phone, *_a, **_k):
        self.calls.append(phone)
        return SimpleNamespace(ok=True, status="SIMULATED", provider_id="SPY", error=None)


# --------------------------------------------------------------------------
# Walkout follow-up door (reviewer must-fix, round 3): the writer's verdict
# decides "queued"; a refused or unsaved message uses no cap.
# --------------------------------------------------------------------------

WALKOUT = {"walkout_id": "ZZ-WO-1", "store_id": "ZZ-S1", "customer_id": CID, "customer_name": "Zz", "mobile": PHONE}


def _walkout_fu(db):
    from api.routers import walkouts as wo

    return asyncio.run(
        wo._maybe_queue_followup_outbound(db, walkout_row=dict(WALKOUT), round_num=1, mode="WHATSAPP", current_user=USER)
    )


def test_walkout_followup_with_the_default_wording_is_refused_and_spends_no_cap(db):
    # Built-in WALKOUT_RECOVERY needs frame_names / discount_percent /
    # validity_date, which the door does not have: D56 never queues it.
    assert _walkout_fu(db) == "refused"
    assert db.notification_logs.count_documents({}) == 0
    assert db.comms_ledger.count_documents({"customer_id": CID}) == 0


def test_walkout_followup_with_a_failed_insert_is_an_error_and_spends_no_cap(db, monkeypatch):
    from pymongo.collection import Collection

    monkeypatch.setitem(ns.TEMPLATES, "WALKOUT_RECOVERY", "Hi {customer_name}, visit {store_name} again.")
    real = Collection.insert_one

    def _insert(self, doc, *a, **k):
        if self.name == "notification_logs":
            raise OSError("injected: write failed")
        return real(self, doc, *a, **k)

    monkeypatch.setattr(Collection, "insert_one", _insert)
    assert _walkout_fu(db) == "error"
    monkeypatch.setattr(Collection, "insert_one", real)
    assert db.comms_ledger.count_documents({"customer_id": CID}) == 0


def test_walkout_followup_with_fillable_wording_is_queued_once_with_one_cap_row(db, monkeypatch):
    monkeypatch.setitem(ns.TEMPLATES, "WALKOUT_RECOVERY", "Hi {customer_name}, visit {store_name} again.")
    assert _walkout_fu(db) == "queued"
    assert db.notification_logs.count_documents({"related_entity_id": "ZZ-WO-1"}) == 1
    assert db.comms_ledger.count_documents({"customer_id": CID}) == 1


# --------------------------------------------------------------------------
# D91(1): referral invite, NPS survey, repair-ready and Rx-portal OTP doors
# say "sent" only for a STORED message. Two faults per door: the writer
# REFUSED it (a placeholder the door cannot fill, D56) and the insert failed.
# --------------------------------------------------------------------------

UNFILLABLE = "Hi {customer_name}, {zz_value_the_door_cannot_fill}"


def _fail_notification_inserts(monkeypatch):
    from pymongo.collection import Collection

    real = Collection.insert_one

    def _insert(self, doc, *a, **k):
        if self.name == "notification_logs":
            raise OSError("injected: write failed")
        return real(self, doc, *a, **k)

    monkeypatch.setattr(Collection, "insert_one", _insert)


def _fault(monkeypatch, fault, template_id):
    if fault == "refused":
        monkeypatch.setitem(ns.TEMPLATES, template_id, UNFILLABLE)
    else:
        _fail_notification_inserts(monkeypatch)


def _referral():
    return asyncio.run(mkt_mod.send_referral_invite(CID, current_user=dict(USER)))


@pytest.mark.parametrize("fault,code", [("refused", 422), ("storage", 503)])
def test_d91_referral_invite_not_stored_is_not_sent(db, monkeypatch, fault, code):
    from fastapi import HTTPException

    _fault(monkeypatch, fault, "REFERRAL_INVITE")
    with pytest.raises(HTTPException) as exc:
        _referral()
    assert exc.value.status_code == code
    assert exc.value.detail.startswith("Message not sent")
    assert db.notification_logs.count_documents({"template_id": "REFERRAL_INVITE"}) == 0


def test_d91_referral_invite_stored_answers_sent_and_a_retry_reuses_the_code(db, monkeypatch):
    from fastapi import HTTPException

    _fault(monkeypatch, "refused", "REFERRAL_INVITE")
    with pytest.raises(HTTPException):
        _referral()
    monkeypatch.setitem(ns.TEMPLATES, "REFERRAL_INVITE", "Hi {customer_name}, code {referral_code}")
    out = _referral()
    assert out["message"] == "Referral invite sent" and out["notification"]["persisted"] is True
    assert db.notification_logs.count_documents({"template_id": "REFERRAL_INVITE"}) == 1
    assert db.referrals.count_documents({"referrer_customer_id": CID}) == 1  # same code reused


def _nps_order(db, oid="ZZ-ORD-1"):
    db.orders.insert_one({"order_id": oid, "store_id": "ZZ-S1", "customer_id": CID, "status": "DELIVERED"})
    return oid


@pytest.mark.parametrize("fault,code", [("refused", 422), ("storage", 503)])
def test_d91_nps_survey_not_stored_is_not_sent(db, monkeypatch, fault, code):
    from fastapi import HTTPException

    oid = _nps_order(db)
    _fault(monkeypatch, fault, "NPS_SURVEY")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(mkt_mod.send_nps_survey(oid, current_user=dict(USER)))
    assert exc.value.status_code == code and exc.value.detail.startswith("Message not sent")
    assert db.notification_logs.count_documents({"template_id": "NPS_SURVEY"}) == 0
    # The survey row is NOT_SENT and the NPS screen does not count it.
    assert [r["status"] for r in db.nps_responses.find({"order_id": oid})] == ["NOT_SENT"]
    stats = asyncio.run(mkt_mod.get_nps_dashboard(store_id="ZZ-S1", current_user=dict(USER)))
    assert stats["total_surveys"] == 0


def test_d91_nps_survey_stored_answers_sent(db):
    oid = _nps_order(db)
    out = asyncio.run(mkt_mod.send_nps_survey(oid, current_user=dict(USER)))
    assert out["message"] == "NPS survey sent"
    assert db.notification_logs.count_documents({"template_id": "NPS_SURVEY"}) == 1
    assert [r["status"] for r in db.nps_responses.find({"order_id": oid})] == ["SENT"]


@pytest.mark.parametrize("fault", ["refused", "storage"])
def test_d91_auto_nps_on_delivery_not_stored_is_not_sent(db, monkeypatch, fault):
    from api.services import nps_trigger

    monkeypatch.setattr(nps_trigger, "_get_db", lambda: db)
    with monkeypatch.context() as m:
        _fault(m, fault, "NPS_SURVEY")
        asyncio.run(nps_trigger.trigger_nps_on_delivery(
            {"order_id": "ZZ-ORD-A", "customer_id": CID, "store_id": "ZZ-S1"}, dict(USER)))
    assert [r["status"] for r in db.nps_responses.find({"order_id": "ZZ-ORD-A"})] == ["NOT_SENT"]
    # A survey never sent does not block the customer's next one for 30 days.
    asyncio.run(nps_trigger.trigger_nps_on_delivery(
        {"order_id": "ZZ-ORD-B", "customer_id": CID, "store_id": "ZZ-S1"}, dict(USER)))
    assert [r["status"] for r in db.nps_responses.find({"order_id": "ZZ-ORD-B"})] == ["SENT"]


def test_d91_auto_nps_30_day_window_compares_utc_on_an_ist_host(db, monkeypatch):
    """survey_sent_at is UTC; a host-clock cutoff on an IST host moved the
    window 5.5 h. A survey sent 30 days minus 2 h ago still blocks."""
    from datetime import timedelta, timezone

    from api.services import nps_trigger

    monkeypatch.setattr(nps_trigger, "_get_db", lambda: db)
    ist = timezone(timedelta(hours=5, minutes=30))
    real = nps_trigger.datetime

    class _ISTHost(real):
        @classmethod
        def now(cls, tz=None):
            return real.now(tz) if tz else real.now(ist).replace(tzinfo=None)

    monkeypatch.setattr(nps_trigger, "datetime", _ISTHost)
    sent = (real.now(timezone.utc) - timedelta(days=30) + timedelta(hours=2)).isoformat()
    db.nps_responses.insert_one({"nps_id": "ZZ-NPS-OLD", "customer_id": CID, "order_id": "ZZ-OLD", "status": "SENT", "survey_sent_at": sent})
    asyncio.run(nps_trigger.trigger_nps_on_delivery(
        {"order_id": "ZZ-ORD-C", "customer_id": CID, "store_id": "ZZ-S1"}, dict(USER)))
    assert db.nps_responses.count_documents({"order_id": "ZZ-ORD-C"}) == 0


def _repair_ready(db, monkeypatch):
    from api.routers import repair_portal as rp
    from api.services import repair_portal as svc

    monkeypatch.setattr(rp, "_get_db", lambda: db)
    monkeypatch.setattr(rp, "validate_store_access", lambda *_a, **_k: True)
    job = svc.open_job(db, "ZZ-S1", {"service_type": "Frame repair", "walkin_name": "Zz", "walkin_mobile": PHONE}, actor=USER)
    svc.transition_job(db, job["job_id"], "ZZ-S1", "IN_PROGRESS", actor=USER)
    out = asyncio.run(rp.transition_job(job["job_id"], rp.TransitionBody(to="READY"), current_user=dict(USER)))
    return job["job_id"], out


@pytest.mark.parametrize("fault", ["refused", "storage"])
def test_d91_repair_ready_message_not_stored_is_not_sent(db, monkeypatch, fault):
    _fault(monkeypatch, fault, "repair_ready")
    jid, out = _repair_ready(db, monkeypatch)
    assert out["status"] == "READY"  # the job moved; only the message failed
    assert out["ready_message"]["sent"] is False
    assert out["ready_message"]["error"].startswith("The ready message was not sent")
    assert db.notification_logs.count_documents({"related_entity_id": jid}) == 0


def test_d91_repair_ready_message_stored_answers_sent(db, monkeypatch):
    monkeypatch.setitem(ns.TEMPLATES, "repair_ready", "Hi {customer_name}, job {job_id} is ready.")
    jid, out = _repair_ready(db, monkeypatch)
    assert out["ready_message"]["sent"] is True
    assert db.notification_logs.count_documents({"related_entity_id": jid}) == 1


def _otp(db, monkeypatch, sms_status="SIMULATED"):
    import agents.providers as providers
    from api import dependencies as deps
    from api.routers import portal

    sent = []

    async def _sms(phone, message, **_k):
        sent.append(message)
        return SimpleNamespace(status=sms_status)

    monkeypatch.setattr(providers, "send_sms", _sms)
    monkeypatch.setattr(portal, "_get_db", lambda: db)
    monkeypatch.setattr(portal, "_check_rate", lambda *_a, **_k: None)
    monkeypatch.setattr(deps, "get_customer_repository", lambda: SimpleNamespace(
        find_by_mobile=lambda m: db.customers.find_one({"mobile": m[-10:]}, {"_id": 0})))
    return asyncio.run(portal.request_rx_otp(portal.RxOtpRequest(phone=PHONE), None)), sent


def test_d91_portal_otp_not_stored_is_not_sent(db, monkeypatch):
    from fastapi import HTTPException

    _fail_notification_inserts(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        _otp(db, monkeypatch)
    assert exc.value.status_code == 503
    assert exc.value.detail == "The code could not be sent. Try again or ask the shop"
    assert db.notification_logs.count_documents({"template_id": "RX_PORTAL_OTP"}) == 0


def test_d91_portal_otp_transport_failure_is_not_sent(db, monkeypatch):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        _otp(db, monkeypatch, sms_status="FAILED")
    assert exc.value.detail == "The code could not be sent. Try again or ask the shop"
    assert [r["status"] for r in db.notification_logs.find({"template_id": "RX_PORTAL_OTP"})] == ["FAILED"]


def test_d91_portal_otp_stored_answers_sent_and_the_drain_never_resends_it(db, monkeypatch, provider):
    out, sent = _otp(db, monkeypatch)
    assert out["ok"] is True and "has been sent" in out["message"] and len(sent) == 1
    row = db.notification_logs.find_one({"template_id": "RX_PORTAL_OTP"})
    assert row["status"] == "SIMULATED" and "code is ******." in row["message"]
    # The stored OTP row is not PENDING: MEGAPHONE never sends it a second time.
    sms_calls = []

    async def _sms_spy(*a, **_k):
        sms_calls.append(a)
        return SimpleNamespace(ok=True, status="SIMULATED", provider_id="SPY", error=None)

    monkeypatch.setattr(mega_mod, "send_sms", _sms_spy)
    _drain(db)
    assert sms_calls == [] and provider == []


def test_d91_portal_otp_unknown_number_stays_generic(db, monkeypatch):
    from api import dependencies as deps
    from api.routers import portal

    monkeypatch.setattr(portal, "_get_db", lambda: db)
    monkeypatch.setattr(portal, "_check_rate", lambda *_a, **_k: None)
    monkeypatch.setattr(deps, "get_customer_repository", lambda: SimpleNamespace(find_by_mobile=lambda m: None))
    out = asyncio.run(portal.request_rx_otp(portal.RxOtpRequest(phone="9988776655"), None))
    assert out["ok"] is True and db.notification_logs.count_documents({}) == 0
