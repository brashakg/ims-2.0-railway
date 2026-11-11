"""
Audit batch b03 - marketing dispatch (L1-F20, L1-F21, L1-F22, L1-F27, L1-F11)
=============================================================================
Regression tests for the Codex whole-app audit findings about the last step
before a customer message leaves the building. Each test drives the REAL
function (the MEGAPHONE drain, the scheduler's leader election and trigger
factory, the scheduled campaign fan-out, the consent recorder) against the
strict in-memory Mongo double; only the provider call is a spy.

NOTHING here can send: DISPATCH_MODE is forced off and the provider functions
are replaced by spies that record the call and return SIMULATED.

  L1-F20  a queued marketing message must not reach the provider after the
          customer opts out (consent is re-read at dispatch, fail closed).
  L1-F21  two drains / two scheduler leaders must not dispatch one queued
          message twice; leadership that cannot be established is not held.
  L1-F22  cron triggers carry the India timezone, whatever the host clock.
  L1-F27  a scheduled campaign obeys the same consent + frequency cap + cap
          ledger as a hand-sent one (one shared fan-out loop).
  L1-F11  the template catalog offers built-ins on a fresh database.

L1-F28 (refuse RECURRING / TRIGGERED schedules) is covered in test_campaigns.py.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("DISPATCH_MODE", "off")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agents.scheduler as scheduler_mod  # noqa: E402
from agents import providers  # noqa: E402
from agents.implementations import megaphone as mega_mod  # noqa: E402
from agents.scheduler import AgentScheduler  # noqa: E402
from api.routers import marketing as marketing_mod  # noqa: E402
from api.services import campaign_segments as seg  # noqa: E402
from api.services import reminder_rail as rail  # noqa: E402
from api.services import notification_templates as templates_mod  # noqa: E402
from api.services.notification_service import TEMPLATES  # noqa: E402
from tests.strict_fakes import StrictCollection, StrictDB  # noqa: E402

CUST = "ZZ-B03-CUST"
PHONE = "9000099999"


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


def _row(rid="N1", **over):
    """A queued notification_logs row in the shape queue_notification_row
    writes for a MARKETING message."""
    row = {
        "_id": rid,
        "notification_id": rid,
        "customer_id": CUST,
        "customer_phone": PHONE,
        "customer_name": "Synthetic only",
        "message": "Synthetic marketing only",
        "template_id": "BIRTHDAY_WISH",
        "category": "MARKETING",
        "consent_basis": "marketing_consent",
        "channel": "WHATSAPP",
        "status": "PENDING",
        "scheduled_for": None,
    }
    row.update(over)
    return row


def _db(rows=None, *, consent=True, customer=True):
    db = StrictDB()
    if customer:
        db.seed(
            "customers",
            [
                {
                    "customer_id": CUST,
                    "name": "Synthetic only",
                    "mobile": PHONE,
                    "marketing_consent": consent,
                }
            ],
        )
    db.get_collection("whatsapp_consent_ledger")
    db.seed("notification_logs", rows if rows is not None else [_row()])
    return db


@pytest.fixture
def provider(monkeypatch):
    """Spy on the WhatsApp/SMS provider doors. Records every call; returns
    SIMULATED. No network, no MSG91."""
    calls = []

    async def _wa(phone, message, **kw):
        calls.append({"channel": "whatsapp", "phone": phone, **kw})
        await asyncio.sleep(0)  # a real send yields; let a rival drain run
        return providers.DispatchResult(ok=True, status="SIMULATED", channel="whatsapp")

    async def _sms(phone, message, **kw):
        calls.append({"channel": "sms", "phone": phone, **kw})
        await asyncio.sleep(0)
        return providers.DispatchResult(ok=True, status="SIMULATED", channel="sms")

    monkeypatch.setattr(mega_mod, "send_whatsapp", _wa)
    monkeypatch.setattr(mega_mod, "send_sms", _sms)
    return calls


def _drain(db):
    agent = mega_mod.MegaphoneAgent(db=db)
    return asyncio.run(agent._drain_pending(db.get_collection("notification_logs")))


def _stored(db, rid="N1"):
    return db.get_collection("notification_logs").find_one({"_id": rid})


def _record_consent(db, monkeypatch, event):
    """Run the REAL consent recorder (POST /marketing/whatsapp-consent)."""
    monkeypatch.setattr(marketing_mod, "_get_db", lambda: db)
    req = marketing_mod.ConsentEventRequest(
        customer_id=CUST, event=event, source="INBOUND_STOP" if event == "STOP" else "STAFF_ENTRY"
    )
    return asyncio.run(marketing_mod.record_whatsapp_consent(req, {"user_id": "ZZ-ADMIN"}))


# ===========================================================================
# L1-F20 - opt-out stops an already queued marketing message
# ===========================================================================


def test_stop_after_queueing_cancels_the_message_without_calling_the_provider(
    provider, monkeypatch
):
    """The audit reproduction: queue while consent exists, the customer sends
    STOP, the drain runs. The provider must not be invoked and the row must end
    on an inspectable terminal state."""
    db = _db()
    out = _record_consent(db, monkeypatch, "STOP")
    assert out["marketing_consent"] is False

    stats = _drain(db)

    assert provider == [], "an opted-out customer's marketing reached the provider"
    row = _stored(db)
    assert row["status"] == "CANCELLED"
    assert row["delivery_status"] == "CANCELLED"
    assert "consent" in row["failure_reason"]
    assert "dispatched_at" not in row  # it never went anywhere
    assert stats["cancelled"] == 1 and stats["simulated"] == 0


def test_stop_in_the_ledger_alone_also_cancels(provider):
    """All three opt-out signals count at dispatch, not only the flag: the
    customer flag still says yes but the newest ledger event is STOP."""
    db = _db()
    db.seed(
        "whatsapp_consent_ledger",
        [{"customer_id": CUST, "event": "STOP", "recorded_at": "2026-11-10T00:00:00+00:00"}],
    )

    _drain(db)

    assert provider == []
    assert _stored(db)["status"] == "CANCELLED"


def test_a_later_opt_in_lets_a_new_message_through(provider, monkeypatch):
    """STOP then a genuine OPT_IN: marketing queued afterwards is allowed."""
    db = _db()
    _record_consent(db, monkeypatch, "STOP")
    time.sleep(0.01)  # the ledger orders events by timestamp; keep them distinct
    _record_consent(db, monkeypatch, "OPT_IN")

    stats = _drain(db)

    assert len(provider) == 1 and provider[0]["phone"] == PHONE
    assert _stored(db)["status"] == "SIMULATED"
    assert stats["simulated"] == 1 and stats.get("cancelled", 0) == 0


def test_consent_read_failure_holds_the_message_then_it_recovers(provider):
    """Fail closed: when consent cannot be read the message is NOT sent. It
    goes back to PENDING with the reason, and sends once the read works."""
    db = _db()
    customers = db.get_collection("customers")
    real_find_one = customers.find_one

    def _boom(*_a, **_k):
        raise RuntimeError("customers unreadable")

    customers.find_one = _boom
    stats = _drain(db)

    assert provider == []
    row = _stored(db)
    assert row["status"] == "PENDING"
    assert "consent could not be verified" in row["hold_reason"]
    assert stats["held"] == 1

    customers.find_one = real_find_one
    _drain(db)
    assert len(provider) == 1
    assert _stored(db)["status"] == "SIMULATED"


def test_consent_ledger_read_failure_also_holds(provider):
    """The flag says yes but the STOP ledger cannot be read: that is not a
    verified yes."""
    db = _db()

    def _boom(*_a, **_k):
        raise RuntimeError("ledger unreadable")

    db.get_collection("whatsapp_consent_ledger").find_one = _boom

    _drain(db)

    assert provider == []
    assert _stored(db)["status"] == "PENDING"


def test_no_database_handle_holds_marketing(provider, monkeypatch):
    """An agent with no database cannot verify consent: marketing is held."""
    import database.connection as conn

    monkeypatch.setattr(conn, "get_seeded_db", lambda: None)
    coll = StrictCollection("notification_logs", [_row()])
    agent = mega_mod.MegaphoneAgent(db=None)

    asyncio.run(agent._drain_pending(coll))

    assert provider == []
    assert coll.find_one({"_id": "N1"})["status"] == "PENDING"


def test_marketing_for_a_customer_with_no_record_is_cancelled(provider):
    """No customer record means no consent on file to rely on."""
    db = _db(customer=False)

    _drain(db)

    assert provider == []
    assert _stored(db)["status"] == "CANCELLED"


@pytest.mark.parametrize(
    "over",
    [
        {"category": "SERVICE", "consent_basis": "transactional"},
        {"category": "OTP", "consent_basis": "transactional"},
        {"category": "TRANSACTIONAL", "consent_basis": "transactional"},
        # an older row with no consent_basis is classified by its category
        {"category": "SERVICE", "consent_basis": None},
        {"category": "OTP", "consent_basis": None},
    ],
)
def test_service_and_otp_messages_still_reach_an_opted_out_customer(provider, over):
    """The marketing opt-out never blocks an order update or an OTP."""
    db = _db([_row(template_id="ORDER_DELIVERED", **over)], consent=False)

    _drain(db)

    assert len(provider) == 1
    assert _stored(db)["status"] == "SIMULATED"


def test_a_row_with_no_category_is_treated_as_marketing(provider):
    """Fail closed on classification: the queue writer's own rule says an
    unlabelled row rides marketing consent, so the drain checks it."""
    db = _db([_row(category=None, consent_basis=None)], consent=False)

    _drain(db)

    assert provider == []
    assert _stored(db)["status"] == "CANCELLED"


def test_marketing_with_no_customer_id_still_sends(provider):
    """Unchanged rule, pinned: a row with no customer has no consent record
    to re-read (the queueing doors treat it the same way)."""
    db = _db([_row(customer_id="")])

    _drain(db)

    assert len(provider) == 1


def test_strict_consent_check_is_opt_in_only_for_the_dispatch_boundary():
    """The queueing doors keep the fail-soft answer; only strict=True raises
    or treats a missing record as opted out."""
    assert marketing_mod.is_opted_out(CUST, None) is False
    with pytest.raises(RuntimeError):
        marketing_mod.is_opted_out(CUST, None, strict=True)
    empty = StrictDB()
    assert marketing_mod.is_opted_out(CUST, empty) is False
    assert marketing_mod.is_opted_out(CUST, empty, strict=True) is True


# ===========================================================================
# L1-F21 - one queued message is dispatched by exactly one owner
# ===========================================================================


def test_two_concurrent_drains_send_one_message_once(provider):
    """The audit reproduction: two drains run at the same time over the same
    PENDING row. Exactly one provider call."""
    db = _db()
    coll = db.get_collection("notification_logs")

    async def _both():
        a = mega_mod.MegaphoneAgent(db=db)
        b = mega_mod.MegaphoneAgent(db=db)
        return await asyncio.gather(a._drain_pending(coll), b._drain_pending(coll))

    asyncio.run(_both())

    assert len(provider) == 1, f"one queued message was sent {len(provider)} times"
    assert _stored(db)["status"] == "SIMULATED"


def test_a_drain_holding_a_stale_copy_of_the_row_loses_the_claim(provider):
    """Both workers already READ the row as PENDING (the exact race). The
    per-row claim, not the earlier read, decides who may send."""
    db = _db()
    coll = db.get_collection("notification_logs")
    stale = [dict(coll.find_one({"_id": "N1"}))]

    class _StaleRead:
        """Hands every drain the same PENDING snapshot; writes hit the store."""

        def find(self, *_a, **_k):
            return StrictCollection("snapshot", stale).find({})

        def __getattr__(self, name):
            return getattr(coll, name)

    async def _both():
        a = mega_mod.MegaphoneAgent(db=db)
        b = mega_mod.MegaphoneAgent(db=db)
        return await asyncio.gather(
            a._drain_pending(_StaleRead()), b._drain_pending(_StaleRead())
        )

    stats = asyncio.run(_both())

    assert len(provider) == 1
    assert sorted(s["attempted"] for s in stats) == [0, 1]
    assert sorted(s["skipped"] for s in stats) == [0, 1]


def test_a_row_that_cannot_be_claimed_is_never_sent(provider):
    """A claim that errors is a lost claim: no send on an unowned row."""
    db = _db()
    coll = db.get_collection("notification_logs")
    real_update_one = coll.update_one

    def _boom(flt, update, **kw):
        if (update.get("$set") or {}).get("status") == "DISPATCHING":
            raise RuntimeError("claim write failed")
        return real_update_one(flt, update, **kw)

    coll.update_one = _boom

    _drain(db)

    assert provider == []
    assert _stored(db)["status"] == "PENDING"


def test_an_abandoned_claim_is_parked_as_unknown_and_not_resent(provider):
    """A worker claimed the row and died. Whether the provider accepted the
    message is unknown, so it is parked visibly and never auto-resent; a claim
    still inside its lease is left alone."""
    now = datetime.now(timezone.utc)
    old = (now - timedelta(minutes=mega_mod.DISPATCH_LEASE_MINUTES + 5)).isoformat()
    fresh = (now - timedelta(minutes=1)).isoformat()
    db = _db(
        [
            _row("N-DEAD", status="DISPATCHING", claimed_at=old),
            _row("N-LIVE", status="DISPATCHING", claimed_at=fresh),
        ]
    )

    _drain(db)
    _drain(db)  # a later tick must not pick the parked row up either

    assert provider == []
    dead = _stored(db, "N-DEAD")
    assert dead["status"] == "UNKNOWN"
    assert dead["delivery_status"] == "UNKNOWN"
    assert "not resent" in dead["failure_reason"].lower()
    assert _stored(db, "N-LIVE")["status"] == "DISPATCHING"


def test_scheduled_campaign_is_not_requeued_when_its_completed_write_fails(monkeypatch):
    """The audience is already queued when the final status write fails. The
    tick must not put the campaign back to SCHEDULED: the next tick would
    queue every recipient a second time."""
    db, sent = _campaign_db(monkeypatch)
    coll = db.get_collection("campaigns")
    real_update_one = coll.update_one

    def _boom(flt, update, **kw):
        if (update.get("$set") or {}).get("status") == "COMPLETED":
            raise RuntimeError("status write failed")
        return real_update_one(flt, update, **kw)

    coll.update_one = _boom
    agent = mega_mod.MegaphoneAgent(db=db)

    assert asyncio.run(agent._dispatch_scheduled_campaigns()) == 1
    assert len(sent) == 1
    assert coll.find_one({"campaign_id": "CMP-B03"})["status"] == "ACTIVE"
    # the next tick finds nothing SCHEDULED
    assert asyncio.run(agent._dispatch_scheduled_campaigns()) == 0
    assert len(sent) == 1


# --- scheduler leadership ---------------------------------------------------


class _FakeRedis:
    def __init__(self, kv):
        self.kv = kv

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.kv:
            return None
        self.kv[key] = value
        return True

    def get(self, key):
        return self.kv.get(key)

    def expire(self, key, ttl):
        return key in self.kv

    def delete(self, key):
        self.kv.pop(key, None)

    def close(self):
        pass


@pytest.mark.asyncio
async def test_two_workers_with_unreachable_redis_do_not_both_lead(monkeypatch):
    """The audit reproduction: Redis is configured but down. Before the fix
    both schedulers answered "leader"; now neither does."""
    monkeypatch.setenv("RUN_AGENT_SCHEDULER", "true")
    monkeypatch.setenv("REDIS_URL", "redis://unreachable.invalid:6379/0")
    monkeypatch.setattr(scheduler_mod, "_make_redis_client", lambda: None)

    a, b = AgentScheduler(db=None), AgentScheduler(db=None)
    leadership = [await a._acquire_leadership(), await b._acquire_leadership()]

    assert leadership == [False, False]
    assert a._is_leader is False and b._is_leader is False


@pytest.mark.asyncio
async def test_unreachable_redis_start_schedules_nothing_and_keeps_retrying(monkeypatch):
    """start() with the lock unavailable attaches no jobs and leaves the
    leader-retry loop running, so the worker takes over once Redis answers."""
    monkeypatch.setenv("RUN_AGENT_SCHEDULER", "true")
    monkeypatch.setenv("REDIS_URL", "redis://unreachable.invalid:6379/0")
    monkeypatch.setattr(scheduler_mod, "_make_redis_client", lambda: None)

    sched = AgentScheduler(db=None)
    await sched.start({})
    try:
        assert sched._running is False
        assert sched._leader_wait_task is not None and not sched._leader_wait_task.done()
        if sched._scheduler is not None:
            assert sched._scheduler.get_jobs() == []
    finally:
        await sched.shutdown()


@pytest.mark.asyncio
async def test_no_redis_configured_still_runs(monkeypatch):
    """A deploy with no Redis at all is the single-worker case: it leads."""
    monkeypatch.setenv("RUN_AGENT_SCHEDULER", "true")
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("REDIS_HOST", raising=False)
    monkeypatch.setattr(scheduler_mod, "_make_redis_client", lambda: None)

    sched = AgentScheduler(db=None)
    assert await sched._acquire_leadership() is True
    await sched.shutdown()


async def _leader_with_job(monkeypatch, kv):
    monkeypatch.setenv("RUN_AGENT_SCHEDULER", "true")
    monkeypatch.setattr(scheduler_mod, "SCHEDULER_LEADER_REFRESH", 0.01)
    monkeypatch.setattr(scheduler_mod, "SCHEDULER_LEADER_RETRY", 3600)
    monkeypatch.setattr(scheduler_mod, "_make_redis_client", lambda: _FakeRedis(kv))
    sched = AgentScheduler(db=None)
    assert await sched._acquire_leadership() is True
    sched._running = True

    async def _tick():
        return None

    if sched._scheduler is not None:
        sched._scheduler.add_job(_tick, trigger=sched._make_trigger("interval", "3600"), id="zz")
    return sched


@pytest.mark.asyncio
async def test_a_leader_that_lost_the_lock_to_another_worker_stands_down(monkeypatch):
    """Before the fix the old leader kept its jobs running beside the new
    leader. Now it removes them and goes back to waiting."""
    kv = {}
    sched = await _leader_with_job(monkeypatch, kv)
    try:
        kv[scheduler_mod.SCHEDULER_LEADER_KEY] = "another-worker"
        await asyncio.sleep(0.2)

        assert sched._is_leader is False
        assert sched._running is False
        assert sched._redis is None
        if sched._scheduler is not None:
            assert sched._scheduler.get_jobs() == []
        assert sched._leader_wait_task is not None and not sched._leader_wait_task.done()
        # it never touched the new leader's lock
        assert kv[scheduler_mod.SCHEDULER_LEADER_KEY] == "another-worker"
    finally:
        await sched.shutdown()


@pytest.mark.asyncio
async def test_a_leader_whose_lock_lapsed_unclaimed_retakes_it(monkeypatch):
    """The key vanished (Redis restarted) and nobody took it: the leader
    re-takes it atomically and keeps its jobs."""
    kv = {}
    sched = await _leader_with_job(monkeypatch, kv)
    try:
        kv.clear()
        await asyncio.sleep(0.2)

        assert sched._is_leader is True
        assert kv[scheduler_mod.SCHEDULER_LEADER_KEY] == sched._instance_id
        if sched._scheduler is not None:
            assert [j.id for j in sched._scheduler.get_jobs()] == ["zz"]
    finally:
        await sched.shutdown()


# ===========================================================================
# L1-F22 - cron jobs fire on India time whatever the host timezone
# ===========================================================================


@pytest.mark.skipif(not scheduler_mod.APSCHEDULER_AVAILABLE, reason="APScheduler not installed")
@pytest.mark.parametrize("host_tz_name", ["UTC", "Asia/Kolkata", "America/New_York"])
@pytest.mark.parametrize(
    "cron,ist_hour,ist_minute",
    [("0 2 * * *", 2, 0), ("0 9 * * *", 9, 0), ("0 22 * * *", 22, 0), ("30 0 * * *", 0, 30)],
)
def test_cron_trigger_fires_at_the_ist_wall_clock_time_on_any_host(
    monkeypatch, host_tz_name, cron, ist_hour, ist_minute
):
    """The audit reproduction: on a UTC host "0 2 * * *" fired at 07:30 IST.
    The host timezone is simulated at the exact place APScheduler reads it."""
    from zoneinfo import ZoneInfo

    import apscheduler.triggers.cron as cron_mod

    monkeypatch.setattr(cron_mod, "get_localzone", lambda: ZoneInfo(host_tz_name))
    ist = ZoneInfo("Asia/Kolkata")

    trigger = AgentScheduler(db=None)._make_trigger("cron", cron)

    # 23:50 IST on 12 Nov: every one of these schedules next fires on the 13th.
    now = datetime(2026, 11, 12, 23, 50, tzinfo=ist)
    fire = trigger.get_next_fire_time(None, now)
    expected = datetime(2026, 11, 13, ist_hour, ist_minute, tzinfo=ist)
    assert fire.astimezone(timezone.utc) == expected.astimezone(timezone.utc), (
        f"host {host_tz_name}: {cron} fired at {fire.astimezone(ist)} IST"
    )


# ===========================================================================
# L1-F27 - a scheduled campaign obeys the shared cap, consent and ledger
# ===========================================================================


def _campaign_db(monkeypatch, *, audience=None):
    """A due ONE_TIME campaign plus a recording send_notification. The segment
    resolver is pinned so the test is about the gates, not the segment."""
    db = StrictDB()
    db.seed(
        "customers",
        [
            {"customer_id": "C-OK", "name": "Ok", "mobile": "9000000001"},
            {"customer_id": "C-CAP", "name": "Capped", "mobile": "9000000002"},
            {"customer_id": "C-STOP", "name": "Stopped", "mobile": "9000000003"},
            {"customer_id": "C-FLAG", "name": "Flagged", "mobile": "9000000004", "marketing_consent": False},
        ],
    )
    db.seed(
        "campaigns",
        [
            {
                "campaign_id": "CMP-B03",
                "status": "SCHEDULED",
                "schedule": {"kind": "ONE_TIME", "send_at": "2020-01-01T00:00:00+00:00"},
                "template_id": "BIRTHDAY_WISH",
                "channels": ["WHATSAPP"],
                "store_id": "",
                "segment_key": "by_store",
                "segment_params": {},
            }
        ],
    )
    db.get_collection("whatsapp_consent_ledger")
    db.get_collection("comms_ledger")
    db.get_collection("notification_logs")
    audience = audience or [{"customer_id": "C-OK", "phone": "9000000001", "name": "Ok"}]
    monkeypatch.setattr(seg, "resolve_segment", lambda *_a, **_k: [dict(r) for r in audience])
    monkeypatch.setattr(mega_mod, "_shared_in_quiet_hours", lambda *_a: False)

    sent = []

    async def _fake_send(**kw):
        sent.append(kw)
        return {"notification_id": f"N-{len(sent)}"}

    import api.services.notification_service as ns

    monkeypatch.setattr(ns, "send_notification", _fake_send)
    return db, sent


_MIXED = [
    {"customer_id": "C-OK", "phone": "9000000001", "name": "Ok"},
    {"customer_id": "C-CAP", "phone": "9000000002", "name": "Capped"},
    {"customer_id": "C-STOP", "phone": "9000000003", "name": "Stopped"},
    {"customer_id": "C-FLAG", "phone": "9000000004", "name": "Flagged"},
]


def _seed_cap_and_stop(db):
    now = datetime.now(timezone.utc).isoformat()
    db.seed(
        "comms_ledger",
        [
            {"customer_id": "C-CAP", "category": "MARKETING", "sent_at": now}
            for _ in range(rail._cap_limit())  # exactly at the 30-day cap
        ],
    )
    db.seed(
        "whatsapp_consent_ledger",
        [{"customer_id": "C-STOP", "event": "STOP", "recorded_at": now}],
    )


def test_scheduled_campaign_skips_a_customer_over_the_frequency_cap(monkeypatch):
    """The audit reproduction: the manual door refuses on the cap, the
    scheduled door queued anyway."""
    db, sent = _campaign_db(
        monkeypatch, audience=[{"customer_id": "C-CAP", "phone": "9000000002", "name": "Capped"}]
    )
    _seed_cap_and_stop(db)
    agent = mega_mod.MegaphoneAgent(db=db)

    assert asyncio.run(agent._dispatch_scheduled_campaigns()) == 1

    assert sent == [], "the scheduled campaign ignored the shared frequency cap"
    camp = db.get_collection("campaigns").find_one({"campaign_id": "CMP-B03"})
    assert camp["status"] == "COMPLETED"
    assert camp["sent_count"] == 0 and camp["skipped_count"] == 1
    # and it did not spend the customer's cap budget
    assert (
        db.get_collection("comms_ledger").count_documents({"customer_id": "C-CAP"})
        == rail._cap_limit()
    )


def test_scheduled_campaign_applies_every_shared_gate_and_writes_the_cap_ledger(monkeypatch):
    """Cap, STOP ledger and the consent flag each skip their customer; the one
    eligible customer is queued once and counted against the shared cap."""
    db, sent = _campaign_db(monkeypatch, audience=_MIXED)
    _seed_cap_and_stop(db)
    agent = mega_mod.MegaphoneAgent(db=db)

    asyncio.run(agent._dispatch_scheduled_campaigns())

    assert [c["customer_id"] for c in sent] == ["C-OK"]
    assert sent[0]["category"] == "MARKETING" and sent[0]["triggered_by"] == "MEGAPHONE"
    camp = db.get_collection("campaigns").find_one({"campaign_id": "CMP-B03"})
    assert (camp["sent_count"], camp["skipped_count"], camp["failed_count"]) == (1, 3, 0)
    ledger = list(db.get_collection("comms_ledger").find({"customer_id": "C-OK"}))
    assert len(ledger) == 1
    assert ledger[0]["campaign_id"] == "CMP-B03" and ledger[0]["category"] == "MARKETING"


def test_manual_and_scheduled_doors_reach_the_same_customers(monkeypatch):
    """Parity: the hand-sent door and the scheduled door, given the same
    audience and the same cap / consent state, queue the same customers for
    the same reasons, because they run the same loop."""
    from api.services.campaign_dispatch import fan_out_campaign

    db, sent = _campaign_db(monkeypatch, audience=_MIXED)
    _seed_cap_and_stop(db)
    camp = db.get_collection("campaigns").find_one({"campaign_id": "CMP-B03"})

    async def _manual_send(**kw):
        return {"notification_id": "N-MANUAL"}

    manual = asyncio.run(
        fan_out_campaign(
            db, camp, send_store_id="", triggered_by="ZZ-USER", send_notification=_manual_send
        )
    )
    reasons = {r["customer_id"]: r.get("reason", r["status"]) for r in manual["results"]}
    assert reasons == {
        "C-OK": "queued",
        "C-CAP": "freqcap",
        "C-STOP": "opted_out",
        "C-FLAG": "opted_out",
    }

    # Fresh state for the scheduled door (the manual run spent C-OK's ledger row).
    db2, sent2 = _campaign_db(monkeypatch, audience=_MIXED)
    _seed_cap_and_stop(db2)
    asyncio.run(mega_mod.MegaphoneAgent(db=db2)._dispatch_scheduled_campaigns())

    assert [c["customer_id"] for c in sent2] == [
        cid for cid, why in reasons.items() if why == "queued"
    ]


def test_megaphone_has_no_second_copy_of_the_fan_out_loop():
    """Guard against the drift coming back: the agent must not carry its own
    consent / queue loop. It calls the shared service."""
    import inspect

    src = inspect.getsource(mega_mod.MegaphoneAgent._execute_campaign_send)
    assert "fan_out_campaign" in src
    assert "marketing_consent" not in src
    assert "resolve_segment" not in src


# ===========================================================================
# L1-F11 - the template catalog offers built-ins on a fresh database
# ===========================================================================


def _by_id(catalog):
    return {c["template_id"]: c for c in catalog}


def test_fresh_database_catalog_offers_every_builtin_as_sendable():
    """The audit reproduction: with no saved templates the picker's source was
    empty. The catalog now lists every template the server has wording for."""
    catalog = templates_mod.effective_catalog([])

    assert [c["template_id"] for c in catalog] == list(TEMPLATES)
    assert all(c["builtin"] and c["sendable"] and not c["saved"] for c in catalog)


def test_saved_override_and_saved_only_template_are_in_the_catalog():
    rows = [
        {"template_id": "ORDER_CONFIRMED", "is_enabled": True, "content": "edited"},
        {"template_id": "ZZ_CUSTOM", "is_enabled": True, "content": "Hello {customer_name}"},
    ]
    cat = _by_id(templates_mod.effective_catalog(rows))

    assert cat["ORDER_CONFIRMED"] == {
        "template_id": "ORDER_CONFIRMED",
        "builtin": True,
        "saved": True,
        "is_enabled": True,
        "sendable": True,
    }
    assert cat["ZZ_CUSTOM"]["builtin"] is False and cat["ZZ_CUSTOM"]["sendable"] is True
    assert len(cat) == len(TEMPLATES) + 1  # nothing vanished, nothing doubled


def test_disabled_or_empty_templates_are_listed_but_not_sendable():
    """Explicit outcome instead of silently vanishing: a switched-off template
    and a saved template with no body are in the list, marked not sendable."""
    rows = [
        {"template_id": "BIRTHDAY_WISH", "is_enabled": False, "content": "x"},
        {"template_id": "ZZ_EMPTY", "is_enabled": True, "content": "   "},
        {"template_id": "ZZ_OFF", "is_enabled": False, "content": "body"},
    ]
    cat = _by_id(templates_mod.effective_catalog(rows))

    assert cat["BIRTHDAY_WISH"]["sendable"] is False and cat["BIRTHDAY_WISH"]["is_enabled"] is False
    assert cat["ZZ_EMPTY"]["sendable"] is False and cat["ZZ_EMPTY"]["is_enabled"] is True
    assert cat["ZZ_OFF"]["sendable"] is False


def test_templates_endpoint_returns_saved_rows_and_the_catalog(monkeypatch):
    """The settings GET keeps `templates` (saved rows, as the Notifications
    screen reads them) and adds `catalog` for pickers."""
    from api.routers import settings as settings_mod

    saved = [{"template_id": "ZZ_CUSTOM", "is_enabled": True, "content": "Hi"}]
    monkeypatch.setattr(settings_mod, "_get_notification_templates_from_db", lambda: list(saved))

    body = asyncio.run(settings_mod.get_notification_templates({"user_id": "ZZ", "roles": ["ADMIN"]}))

    assert body["templates"] == saved
    ids = [c["template_id"] for c in body["catalog"]]
    assert ids == list(TEMPLATES) + ["ZZ_CUSTOM"]
