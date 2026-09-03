# ============================================================================
# The 30-day browse horizon on the FOLLOW-UPS + MARKETING doors
# (owner ruling 2026-09-01)
# ============================================================================
# Everyone except ADMIN / SUPERADMIN sees only the last 30 days when BROWSING.
# This module's doors split three ways and the split is the point:
#
#   WORK QUEUE  - never clamped. A follow-up 40 days overdue is work still in
#                 hand; hiding it would delete a live feature and drop exactly
#                 the customers who most need chasing.
#   HISTORY     - clamped. The send log, the referral leaderboard, the NPS
#                 dashboard and the walk-in register are the customer book.
#   NAMED LOOKUP- exempt. One customer's consent history is unbounded.
#
# These tests run against MONGOMOCK, not a hand-rolled matcher: a fake that
# ignored $or / $gte would pass with the clamp deleted. mongomock also
# reproduces BSON TYPE BRACKETING (a string bound never matches a datetime
# field and vice-versa), which is the trap this repo has hit three times and
# which the follow_ups + notification_logs collections both contain live.

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import mongomock  # noqa: E402
import pytest  # noqa: E402

from api.routers import follow_ups as fu_router  # noqa: E402
from api.routers import marketing as mk_router  # noqa: E402

STORE = "ST1"
STAFF = {
    "user_id": "u1",
    "roles": ["SALES_STAFF"],
    "active_store_id": STORE,
    "store_ids": [STORE],
}
MANAGER = {
    "user_id": "u2",
    "roles": ["STORE_MANAGER"],
    "active_store_id": STORE,
    "store_ids": [STORE],
}
ADMIN = {
    "user_id": "u3",
    "roles": ["ADMIN"],
    "active_store_id": STORE,
    "store_ids": [STORE],
}

NOW = datetime.now()
RECENT = NOW - timedelta(days=2)
OLD = NOW - timedelta(days=40)  # outside the 30-day window


def _iso(dt: datetime) -> str:
    return dt.isoformat()


class _Wrapper:
    """What follow_ups._get_db() returns: the bool-safe connection wrapper."""

    def __init__(self, db):
        self._db = db
        self.is_connected = True

    def get_collection(self, name):
        return self._db.get_collection(name)


@pytest.fixture()
def db(monkeypatch):
    """A real (in-memory) Mongo query engine wired into both routers."""
    mdb = mongomock.MongoClient()["ims_test_horizon"]
    monkeypatch.setattr(fu_router, "_get_db", lambda: _Wrapper(mdb))
    monkeypatch.setattr(mk_router, "_get_db", lambda: mdb)
    return mdb


def run(coro):
    return asyncio.run(coro)


def _list_follow_ups(user, **kw):
    return run(
        fu_router.list_follow_ups(
            store_id=STORE,
            type_filter=kw.get("type_filter"),
            status_filter=kw.get("status_filter"),
            date_from=kw.get("date_from"),
            date_to=kw.get("date_to"),
            current_user=user,
        )
    )


def _ids(rows):
    return {r.follow_up_id for r in rows}


def _fu(fid, status, *, created, completed=None, scheduled=None):
    """A follow_ups row in the shape the live writers produce."""
    return {
        "follow_up_id": fid,
        "customer_id": "C1",
        "customer_name": "Ramesh",
        "customer_phone": "9800000000",
        "store_id": STORE,
        "type": "general",
        "scheduled_date": scheduled or (NOW.date().isoformat()),
        "status": status,
        "outcome": "completed" if status == "completed" else None,
        "notes": "",
        "created_at": created,
        "completed_at": completed,
        "completed_by": "u9" if completed else None,
    }


# ---------------------------------------------------------------------------
# GET /follow-ups/  -- the MIXED list: queue stays open, history is clamped
# ---------------------------------------------------------------------------


@pytest.fixture()
def seeded(db):
    db.get_collection("follow_ups").insert_many(
        [
            _fu("FU-CLOSED-OLD", "completed", created=_iso(OLD), completed=_iso(OLD)),
            _fu(
                "FU-CLOSED-NEW",
                "completed",
                created=_iso(RECENT),
                completed=_iso(RECENT),
            ),
            # Closed YESTERDAY on a two-year-old reminder: recently worked, so
            # it must survive even though it was created long before the window.
            _fu(
                "FU-CLOSED-OLDROW-NEWWORK",
                "completed",
                created=_iso(NOW - timedelta(days=400)),
                completed=_iso(RECENT),
            ),
            _fu("FU-SKIPPED-OLD", "skipped", created=_iso(OLD), completed=_iso(OLD)),
            # 40 days OVERDUE and still open: live work, never hidden.
            _fu(
                "FU-OVERDUE",
                "pending",
                created=_iso(OLD),
                scheduled=(NOW - timedelta(days=40)).date().isoformat(),
            ),
            _fu("FU-PENDING-NEW", "pending", created=_iso(RECENT)),
        ]
    )
    return db


@pytest.mark.parametrize("user", [STAFF, MANAGER])
def test_staff_cannot_browse_a_follow_up_closed_40_days_ago(seeded, user):
    """THE negative case. Delete the clamp in list_follow_ups and this passes."""
    got = _ids(_list_follow_ups(user))
    assert "FU-CLOSED-OLD" not in got
    assert "FU-SKIPPED-OLD" not in got


def test_admin_still_sees_the_whole_book(seeded):
    """Positive control. Without it, a clamp that returned NOTHING would pass."""
    got = _ids(_list_follow_ups(ADMIN))
    assert "FU-CLOSED-OLD" in got
    assert "FU-SKIPPED-OLD" in got
    assert len(got) == 6


def test_recent_history_is_still_returned_to_staff(seeded):
    """The clamp NARROWS; it must not empty the screen."""
    got = _ids(_list_follow_ups(STAFF))
    assert "FU-CLOSED-NEW" in got
    assert "FU-PENDING-NEW" in got


def test_a_40_day_overdue_follow_up_is_STILL_WORK_and_stays_visible(seeded):
    """The judgement call. An overdue reminder is work in hand, not browsing -
    clamping it would silently drop the customers who most need chasing."""
    got = _ids(_list_follow_ups(STAFF))
    assert "FU-OVERDUE" in got


def test_work_closed_recently_on_an_old_row_survives(seeded):
    """Clamped on the COMPLETION, not on creation: a two-year-old reminder
    called yesterday is recent work."""
    assert "FU-CLOSED-OLDROW-NEWWORK" in _ids(_list_follow_ups(STAFF))


def test_status_filter_completed_is_not_a_bypass(seeded):
    """Asking explicitly for the closed rows must not lift the window."""
    got = _ids(_list_follow_ups(STAFF, status_filter="completed"))
    assert "FU-CLOSED-OLD" not in got
    assert "FU-CLOSED-NEW" in got
    assert "FU-CLOSED-OLDROW-NEWWORK" in got


def test_date_from_2020_cannot_widen_the_window(seeded):
    """A caller-supplied range is not a bypass. `scheduled_date` is deliberately
    unclamped (it is the queue's due date), so the guard has to sit on the
    closed rows themselves - this proves it does."""
    got = _ids(_list_follow_ups(STAFF, date_from="2020-01-01", date_to="2099-01-01"))
    assert "FU-CLOSED-OLD" not in got
    assert "FU-OVERDUE" in got


# --- the TYPE trap ----------------------------------------------------------
# follow_ups.created_at is an ISO string from every door EXCEPT
# whatsapp_intents.py:288, which stores a real datetime. A string bound
# type-brackets away from those rows, so the clamp must not be hung off
# created_at alone or WhatsApp-inbound follow-ups vanish from the queue.


def test_a_datetime_created_at_pending_row_is_not_silently_dropped(db):
    db.get_collection("follow_ups").insert_many(
        [
            # whatsapp_intents.py shape: real datetime, no scheduled_date.
            {
                "follow_up_id": "FU-WA-NEW",
                "store_id": STORE,
                "customer_id": "C2",
                "customer_name": "Sita",
                "customer_phone": "9800000001",
                "status": "pending",
                "notes": "",
                "created_at": RECENT,
                "source": "whatsapp_inbound",
            },
            {
                "follow_up_id": "FU-WA-OLD",
                "store_id": STORE,
                "customer_id": "C2",
                "customer_name": "Sita",
                "customer_phone": "9800000001",
                "status": "pending",
                "notes": "",
                "created_at": OLD,
                "source": "whatsapp_inbound",
            },
        ]
    )
    got = _ids(_list_follow_ups(STAFF))
    # Both are OPEN work, so both stay - their unusual date type must not
    # decide it. An empty screen here would be worse than the leak.
    assert got == {"FU-WA-NEW", "FU-WA-OLD"}


def test_a_datetime_created_row_closed_recently_is_kept_and_closed_long_ago_is_not(db):
    db.get_collection("follow_ups").insert_many(
        [
            {
                "follow_up_id": "FU-WA-CLOSED-NEW",
                "store_id": STORE,
                "status": "completed",
                "notes": "",
                "created_at": OLD,  # datetime
                "completed_at": _iso(RECENT),  # ISO string, as the doors write
            },
            {
                "follow_up_id": "FU-WA-CLOSED-OLD",
                "store_id": STORE,
                "status": "completed",
                "notes": "",
                "created_at": OLD,  # datetime
                "completed_at": _iso(OLD),
            },
        ]
    )
    got = _ids(_list_follow_ups(STAFF))
    assert got == {"FU-WA-CLOSED-NEW"}


# ---------------------------------------------------------------------------
# GET /follow-ups/due-today  -- WORK QUEUE, deliberately NOT clamped
# ---------------------------------------------------------------------------


def test_due_today_still_surfaces_a_40_day_overdue_call(seeded):
    """Left open on purpose. If this ever starts failing, someone clamped the
    queue and the shop stopped chasing its oldest customers."""
    rows = run(fu_router.get_due_today(store_id=STORE, current_user=STAFF))
    assert "FU-OVERDUE" in _ids(rows)


# ---------------------------------------------------------------------------
# GET /follow-ups/summary  -- the COUNTS
# ---------------------------------------------------------------------------


def test_completed_count_never_reaches_past_the_horizon(seeded):
    """A clamped page beside an unclamped total leaks the size of the book.
    `completed_this_month` is bounded to 30 days == the horizon; this holds that
    equality, so widening that window without clamping it turns this red."""
    s = run(fu_router.get_follow_up_summary(store_id=STORE, current_user=STAFF))
    # FU-CLOSED-NEW + FU-CLOSED-OLDROW-NEWWORK only; the two 40-day-old rows are
    # out (and FU-SKIPPED-OLD is not "completed" anyway).
    assert s.completed_this_month == 2


def test_pending_counts_are_the_work_queue_and_stay_whole(seeded):
    """The open-work counts are the size of the QUEUE, not of the customer book,
    so they are deliberately unclamped - a 40-day-overdue call still counts."""
    s = run(fu_router.get_follow_up_summary(store_id=STORE, current_user=STAFF))
    assert s.overdue == 1
    assert s.pending_total == 2


# ---------------------------------------------------------------------------
# GET /marketing/notifications/logs  -- the SEND LOG (history browse)
# ---------------------------------------------------------------------------


def _logs(user, **kw):
    return run(
        mk_router.get_notification_logs(
            store_id=STORE,
            template_id=kw.get("template_id"),
            status=kw.get("status"),
            limit=kw.get("limit", 50),
            current_user=user,
        )
    )


@pytest.fixture()
def seeded_logs(db):
    db.get_collection("notification_logs").insert_many(
        [
            {
                "notification_id": "NTF-OLD",
                "store_id": STORE,
                "customer_phone": "98000",
                "created_at": _iso(OLD),
            },
            {
                "notification_id": "NTF-NEW",
                "store_id": STORE,
                "customer_phone": "98001",
                "created_at": _iso(RECENT),
            },
            # crm.py:718 shape -- created_at is a real datetime, not a string.
            {
                "notification_id": "NTF-DT-NEW",
                "store_id": STORE,
                "kind": "vip_winback",
                "created_at": RECENT,
            },
            {
                "notification_id": "NTF-DT-OLD",
                "store_id": STORE,
                "kind": "vip_winback",
                "created_at": OLD,
            },
        ]
    )
    return db


def test_staff_cannot_browse_the_send_log_past_30_days(seeded_logs):
    got = {r["notification_id"] for r in _logs(STAFF)["logs"]}
    assert "NTF-OLD" not in got
    assert "NTF-NEW" in got


def test_send_log_datetime_rows_are_clamped_not_erased(seeded_logs):
    """The type trap, live in this collection: a string-only bound would hide
    NTF-DT-NEW for ever (a recent row), a datetime-only bound would hide the
    whole ISO log. Both shapes must narrow, neither may vanish."""
    got = {r["notification_id"] for r in _logs(STAFF)["logs"]}
    assert "NTF-DT-NEW" in got
    assert "NTF-DT-OLD" not in got


def test_admin_sees_the_whole_send_log(seeded_logs):
    res = _logs(ADMIN)
    assert {r["notification_id"] for r in res["logs"]} == {
        "NTF-OLD",
        "NTF-NEW",
        "NTF-DT-NEW",
        "NTF-DT-OLD",
    }
    assert res["total"] == 4


def test_send_log_total_is_clamped_with_the_rows(seeded_logs):
    assert _logs(STAFF)["total"] == 2


# ---------------------------------------------------------------------------
# GET /marketing/referrals
# ---------------------------------------------------------------------------


@pytest.fixture()
def seeded_referrals(db):
    db.get_collection("referrals").insert_many(
        [
            {
                "referral_id": "REF-OLD",
                "store_id": STORE,
                "referrer_name": "Old Referrer",
                "referrer_phone": "9800000010",
                "status": "INVITED",
                "created_at": _iso(OLD),
            },
            {
                "referral_id": "REF-NEW",
                "store_id": STORE,
                "referrer_name": "New Referrer",
                "referrer_phone": "9800000011",
                "status": "INVITED",
                "created_at": _iso(RECENT),
            },
        ]
    )
    return db


def test_staff_cannot_browse_the_referral_book_past_30_days(seeded_referrals):
    got = {r["referral_id"] for r in run(
        mk_router.get_referrals(
            store_id=STORE, status=None, limit=50, current_user=STAFF
        )
    )["referrals"]}
    assert got == {"REF-NEW"}


def test_admin_sees_every_referral(seeded_referrals):
    res = run(
        mk_router.get_referrals(
            store_id=STORE, status=None, limit=50, current_user=ADMIN
        )
    )
    assert {r["referral_id"] for r in res["referrals"]} == {"REF-OLD", "REF-NEW"}
    assert res["total"] == 2


# ---------------------------------------------------------------------------
# GET /marketing/nps-dashboard  -- rows AND aggregates
# ---------------------------------------------------------------------------


@pytest.fixture()
def seeded_nps(db):
    db.get_collection("nps_responses").insert_many(
        [
            {
                "nps_id": "NPS-OLD-1",
                "store_id": STORE,
                "customer_name": "Old A",
                "score": 10,
                "created_at": _iso(OLD),
            },
            {
                "nps_id": "NPS-OLD-2",
                "store_id": STORE,
                "customer_name": "Old B",
                "score": 3,
                "created_at": _iso(OLD),
            },
            {
                "nps_id": "NPS-NEW",
                "store_id": STORE,
                "customer_name": "New C",
                "score": 9,
                "created_at": _iso(RECENT),
            },
        ]
    )
    return db


def test_staff_nps_dashboard_hides_responses_past_the_horizon(seeded_nps):
    res = run(mk_router.get_nps_dashboard(store_id=STORE, current_user=STAFF))
    assert {r["nps_id"] for r in res["responses"]} == {"NPS-NEW"}


def test_staff_nps_TOTALS_are_clamped_too(seeded_nps):
    """Clamping the 20 rows on show but not the counters would have published
    the size of the history they were hiding."""
    res = run(mk_router.get_nps_dashboard(store_id=STORE, current_user=STAFF))
    assert res["total_surveys"] == 1
    assert res["total_responses"] == 1
    assert res["detractors"] == 0  # the old score-3 detractor is out of range


def test_admin_nps_dashboard_is_whole(seeded_nps):
    res = run(mk_router.get_nps_dashboard(store_id=STORE, current_user=ADMIN))
    assert res["total_surveys"] == 3
    assert res["detractors"] == 1


# ---------------------------------------------------------------------------
# GET /marketing/walkins
# ---------------------------------------------------------------------------


@pytest.fixture()
def seeded_walkins(db):
    db.get_collection("walkins").insert_many(
        [
            {
                "walkin_id": "WLK-OLD",
                "store_id": STORE,
                "phone": "9800000020",
                "created_at": _iso(OLD),
            },
            {
                "walkin_id": "WLK-NEW",
                "store_id": STORE,
                "phone": "9800000021",
                "created_at": _iso(RECENT),
            },
        ]
    )
    return db


def _walkins(user, **kw):
    return run(
        mk_router.get_walkins(
            store_id=STORE,
            date_from=kw.get("date_from"),
            date_to=kw.get("date_to"),
            limit=kw.get("limit", 50),
            current_user=user,
        )
    )


def test_staff_cannot_browse_the_walkin_register_past_30_days(seeded_walkins):
    assert {w["walkin_id"] for w in _walkins(STAFF)["walkins"]} == {"WLK-NEW"}


def test_walkin_date_from_2020_cannot_widen_the_window(seeded_walkins):
    """The later bound wins. Without that the clamp is decorative - one query
    param and the whole register is back."""
    got = {w["walkin_id"] for w in _walkins(STAFF, date_from="2020-01-01")["walkins"]}
    assert got == {"WLK-NEW"}


def test_a_caller_may_still_NARROW_below_the_horizon(seeded_walkins):
    """Restricting yourself further is always allowed - the clamp is a floor,
    not a replacement."""
    tomorrow = (NOW + timedelta(days=1)).date().isoformat()
    got = _walkins(STAFF, date_from=tomorrow)["walkins"]
    assert got == []


def test_admin_sees_the_whole_walkin_register(seeded_walkins):
    res = _walkins(ADMIN)
    assert {w["walkin_id"] for w in res["walkins"]} == {"WLK-OLD", "WLK-NEW"}


# ---------------------------------------------------------------------------
# THE EXEMPTION -- a NAMED customer lookup returns the full history
# ---------------------------------------------------------------------------


def test_named_customer_consent_history_is_unbounded_for_staff(db):
    """GET /marketing/whatsapp-consent/{customer_id} names ONE customer, so it
    is a lookup and not a browse: staff get everything about the person in
    front of them. Clamping this would be the rule misapplied."""
    db.get_collection("customers").insert_one(
        {"customer_id": "C7", "name": "Ramesh", "marketing_consent": True}
    )
    db.get_collection("whatsapp_consent_ledger").insert_many(
        [
            {
                "ledger_id": "CLE-OLD",
                "customer_id": "C7",
                "event": "OPT_IN",
                "recorded_at": _iso(NOW - timedelta(days=800)),
            },
            {
                "ledger_id": "CLE-NEW",
                "customer_id": "C7",
                "event": "OPT_OUT",
                "recorded_at": _iso(RECENT),
            },
        ]
    )
    res = run(
        mk_router.get_whatsapp_consent_history(
            customer_id="C7", limit=50, current_user=STAFF
        )
    )
    assert {e["ledger_id"] for e in res["events"]} == {"CLE-OLD", "CLE-NEW"}
