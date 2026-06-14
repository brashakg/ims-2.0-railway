"""
Feature #29 fast-follow -- TASKMASTER optometrist-coverage breach sweep.

The roster engine (#29) computes optometrist coverage; this wires the TASKMASTER
5-minute tick to SWEEP every PUBLISHED roster and raise a deduped IN-APP bell to
the store + area managers for any future-dated slot with no optometrist. Comms
are DARK -- in-app notifications only (no WhatsApp/SMS).

Covers: published_coverage_breaches (pure-ish DB-read), the dedupe key + in-app
notification shape, and the agent sweep (one bell per recipient, deduped on a
repeated tick, no-bell when covered, fail-soft with no DB).

Run: JWT_SECRET_KEY=test python -m pytest \
        backend/tests/test_f29_coverage_sweep.py -q
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

import pytest  # noqa: E402

from api.services import roster_engine as re  # noqa: E402
from agents.implementations.taskmaster import TaskmasterAgent  # noqa: E402

# ---------------------------------------------------------------------------
# A fake collection that emulates the Mongo query semantics this feature uses:
# plain equality, $in, AND scalar-in-array (so {roles: "STORE_MANAGER"} matches
# a doc whose roles is ["STORE_MANAGER"], like real Mongo -- MockCollection does
# NOT do array-contains).
# ---------------------------------------------------------------------------


class _FakeColl:
    def __init__(self):
        self.docs = []

    @staticmethod
    def _matches(doc, query):
        for k, v in (query or {}).items():
            dv = doc.get(k)
            if isinstance(v, dict) and "$in" in v:
                opts = v["$in"]
                if isinstance(dv, list):
                    if not any(x in opts for x in dv):
                        return False
                elif dv not in opts:
                    return False
            elif isinstance(dv, list):
                if v not in dv:  # scalar-in-array (roles / store_ids)
                    return False
            elif dv != v:
                return False
        return True

    def find(self, query=None):
        return [d for d in self.docs if self._matches(d, query or {})]

    def find_one(self, query=None):
        for d in self.docs:
            if self._matches(d, query or {}):
                return d
        return None

    def insert_one(self, doc):
        self.docs.append(dict(doc))
        return type("R", (), {"inserted_id": doc.get("notification_id")})()


class _FakeDB:
    def __init__(self):
        self.colls = {}
        self.is_connected = True

    def get_collection(self, name):
        return self.colls.setdefault(name, _FakeColl())


def _seed(db, *, roster_status="PUBLISHED", slot_date="2099-12-31", cover=False):
    """Seed one store with an optometrist + a non-optom, and one roster whose
    MORNING slot is (optionally) uncovered."""
    skills = db.get_collection(re.COLLECTION_SKILLS)
    skills.docs += [
        {"employee_id": "OPT1", "is_optometrist": True, "store_id": "S1"},
        {"employee_id": "STAFF1", "is_optometrist": False, "store_id": "S1"},
    ]
    entries = [
        # MORNING: STAFF1 only -> breach, unless `cover` adds the optometrist.
        {"employee_id": "STAFF1", "date": slot_date, "shift": "MORNING"},
    ]
    if cover:
        entries.append({"employee_id": "OPT1", "date": slot_date, "shift": "MORNING"})
    db.get_collection(re.COLLECTION_ROSTERS).docs.append(
        {
            "roster_id": "RST-1",
            "store_id": "S1",
            "week_start": "2099-12-29",
            "status": roster_status,
            "entries": entries,
        }
    )
    users = db.get_collection("users")
    users.docs += [
        {
            "user_id": "U_SM",
            "roles": ["STORE_MANAGER"],
            "is_active": True,
            "store_ids": ["S1"],
        },
        {
            "user_id": "U_AM",
            "roles": ["AREA_MANAGER"],
            "is_active": True,
            "store_ids": ["S1", "S2"],
        },
        {
            "user_id": "U_OPT",
            "roles": ["OPTOMETRIST"],
            "is_active": True,
            "store_ids": ["S1"],
        },
    ]
    return db


# ===========================================================================
# 1. published_coverage_breaches (pure-ish)
# ===========================================================================


def test_breach_flagged_for_uncovered_future_slot():
    db = _seed(_FakeDB())
    out = re.published_coverage_breaches(db, today="2099-01-01")
    assert len(out) == 1
    b = out[0]
    assert b["store_id"] == "S1"
    assert b["shift"] == "MORNING"
    assert b["status"] == re.COVERAGE_BREACH
    assert b["week_start"] == "2099-12-29"
    assert b["roster_id"] == "RST-1"


def test_no_breach_when_optometrist_rostered():
    db = _seed(_FakeDB(), cover=True)
    assert re.published_coverage_breaches(db, today="2099-01-01") == []


def test_draft_roster_is_not_swept():
    db = _seed(_FakeDB(), roster_status="DRAFT")
    assert re.published_coverage_breaches(db, today="2099-01-01") == []


def test_past_dated_slot_is_filtered_out():
    db = _seed(_FakeDB(), slot_date="2099-01-05")
    # today AFTER the slot -> a past gap can't be fixed -> not alerted.
    assert re.published_coverage_breaches(db, today="2099-06-01") == []


def test_no_db_is_empty():
    assert re.published_coverage_breaches(None, today="2099-01-01") == []


def test_dedupe_key_is_stable_and_per_recipient():
    b = {"store_id": "S1", "week_start": "W", "date": "D", "shift": "MORNING"}
    k1 = re.coverage_breach_dedupe_key(b, "U_SM")
    assert k1 == re.coverage_breach_dedupe_key(b, "U_SM")  # stable
    assert k1 != re.coverage_breach_dedupe_key(b, "U_AM")  # per recipient


def test_notification_is_in_app_only():
    b = {
        "store_id": "S1",
        "week_start": "W",
        "date": "D",
        "shift": "MORNING",
        "roster_id": "RST-1",
    }
    n = re.build_coverage_breach_notification(b, "U_SM", "key1")
    assert n["channels"] == ["IN_APP"]  # comms dark
    assert n["notification_type"] == "roster_coverage_breach"
    assert n["user_id"] == "U_SM"
    assert n["dedupe_key"] == "key1"
    assert n["entity_id"] == "RST-1"


# ===========================================================================
# 2. TASKMASTER sweep
# ===========================================================================


def test_sweep_raises_one_bell_per_recipient():
    db = _seed(_FakeDB())
    tm = TaskmasterAgent(db=db)
    actions = asyncio.new_event_loop().run_until_complete(tm._sweep_coverage_breaches())
    # SM(S1) + AM = 2 recipients, the optometrist user is NOT a recipient.
    assert len(actions) == 2
    recips = {a["user_id"] for a in actions}
    assert recips == {"U_SM", "U_AM"}
    notifs = db.get_collection("notifications").docs
    assert len(notifs) == 2
    assert all(n["channels"] == ["IN_APP"] for n in notifs)


def test_sweep_dedupes_on_repeated_tick():
    db = _seed(_FakeDB())
    tm = TaskmasterAgent(db=db)
    loop = asyncio.new_event_loop()
    first = loop.run_until_complete(tm._sweep_coverage_breaches())
    second = loop.run_until_complete(tm._sweep_coverage_breaches())
    assert len(first) == 2
    assert second == []  # same breach -> no duplicate bells
    assert len(db.get_collection("notifications").docs) == 2


def test_sweep_no_bell_when_covered():
    db = _seed(_FakeDB(), cover=True)
    tm = TaskmasterAgent(db=db)
    actions = asyncio.new_event_loop().run_until_complete(tm._sweep_coverage_breaches())
    assert actions == []
    assert db.get_collection("notifications").docs == []


def test_sweep_failsoft_without_db():
    tm = TaskmasterAgent(db=None)
    actions = asyncio.new_event_loop().run_until_complete(tm._sweep_coverage_breaches())
    assert actions == []
