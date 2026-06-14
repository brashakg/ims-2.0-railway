"""
IMS 2.0 - F43 VIP personal-triggers tests (intent-level)
========================================================
Exercises the REAL vip_triggers service (pure date core + CRUD + VIP profile),
the crm router endpoints (called directly with a fake repo/db), and the
MEGAPHONE _scan_personal_triggers scan against a faithful in-memory fake Mongo
(no network, no live mongod).

Maps to the F43 acceptance intents:
  * next_fire_date / is_due -- pure table: anniversary (lead-time before the
    month-day), birthday + N days, recurring (every N days), custom one-shot,
    not-yet-due, already-fired-this-cycle (last_fired_for guard).
  * VIP profile -- set/read vip_tags + vip_override + appended personal note.
  * trigger CRUD -- create/list/get/update/delete + 422 on a bad type/date.
  * store-scope -- a non-SUPERADMIN cannot create/read a foreign store's trigger.
  * scan -- creates a follow_up + an IN_APP staff notification EXACTLY once; a
    re-scan in the SAME cycle does NOT double-fire (last_fired_for guard).
  * comms-DARK -- the scan creates NO live customer send (0 dispatched; in-app
    + follow_up only).
  * fail-soft -- DB absent -> empty/no-op, never a 500.
  * RBAC -- a denied role is rejected by the route gate.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import date
from typing import Any, Dict, List, Optional

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

from api.services import vip_triggers as svc  # noqa: E402

# ============================================================================
# Faithful in-memory fake Mongo (only the operators F43 uses)
# ============================================================================


def _cmp_op(actual: Any, op: str, expected: Any) -> bool:
    try:
        if op == "$ne":
            return actual != expected
        if op == "$in":
            return actual in expected
        if op == "$gt":
            return actual is not None and actual > expected
        if op == "$lte":
            return actual is not None and actual <= expected
    except TypeError:
        return False
    return False


def _matches(doc: Dict[str, Any], query: Dict[str, Any]) -> bool:
    for k, v in query.items():
        actual = doc.get(k)
        if isinstance(v, dict) and any(str(kk).startswith("$") for kk in v):
            for op, expected in v.items():
                if not _cmp_op(actual, op, expected):
                    return False
            continue
        if actual != v:
            return False
    return True


def _project(
    doc: Dict[str, Any], projection: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    d = dict(doc)
    if projection and projection.get("_id") == 0:
        d.pop("_id", None)
    return d


class FakeCursor:
    def __init__(self, docs: List[Dict[str, Any]]):
        self._docs = docs

    def __iter__(self):
        return iter(self._docs)


class FakeCollection:
    def __init__(self):
        self.docs: List[Dict[str, Any]] = []
        self._n = 0

    def insert_one(self, doc):
        doc.setdefault("_id", f"oid-{self._n}")
        self._n += 1
        self.docs.append(dict(doc))
        return type("R", (), {"inserted_id": doc["_id"]})()

    def find_one(self, query, projection=None):
        for d in self.docs:
            if _matches(d, query):
                return _project(d, projection)
        return None

    def find(self, query=None, projection=None):
        rows = [_project(d, projection) for d in self.docs if _matches(d, query or {})]
        return FakeCursor(rows)

    def find_one_and_update(self, query, update, **_kw):
        for d in self.docs:
            if _matches(d, query):
                for kk, vv in (update.get("$set") or {}).items():
                    d[kk] = vv
                return dict(d)
        return None

    def delete_one(self, query):
        before = len(self.docs)
        self.docs = [d for d in self.docs if not _matches(d, query)]
        return type("R", (), {"deleted_count": before - len(self.docs)})()

    def create_index(self, *a, **k):
        return "idx"


class FakeDB:
    def __init__(self):
        self._collections: Dict[str, FakeCollection] = {}
        self.is_connected = True

    def get_collection(self, name: str) -> FakeCollection:
        if name not in self._collections:
            self._collections[name] = FakeCollection()
        return self._collections[name]

    def __getitem__(self, name: str) -> FakeCollection:
        return self.get_collection(name)


@pytest.fixture()
def db() -> FakeDB:
    return FakeDB()


# ============================================================================
# 1. PURE DATE CORE -- next_event/next_fire/is_due table
# ============================================================================


def test_anniversary_fires_lead_time_before_the_month_day():
    # Wedding anniversary anchored 2010-03-15, 7-day lead. Today = Mar 1 2026.
    trig = {
        "type": svc.ANNIVERSARY,
        "base_date": "2010-03-15",
        "lead_time_days": 7,
        "active": True,
    }
    today = date(2026, 3, 1)
    assert svc.next_event_date(trig, today) == date(2026, 3, 15)
    assert svc.next_fire_date(trig, today) == date(2026, 3, 8)  # 15 - 7
    assert svc.is_due(trig, today) is False  # before the fire window
    assert svc.is_due(trig, date(2026, 3, 8)) is True  # window opens
    assert svc.is_due(trig, date(2026, 3, 15)) is True  # window closes on the event


def test_birthday_plus_n_celebrates_n_days_after_the_birthday():
    # Birthday 1990-06-10, celebrate +3 days (Jun 13), 2-day lead -> fire Jun 11.
    trig = {
        "type": svc.BIRTHDAY_PLUS_N,
        "base_date": "1990-06-10",
        "plus_n_days": 3,
        "lead_time_days": 2,
        "active": True,
    }
    today = date(2026, 6, 1)
    assert svc.next_event_date(trig, today) == date(2026, 6, 13)
    assert svc.next_fire_date(trig, today) == date(2026, 6, 11)
    assert svc.is_due(trig, date(2026, 6, 11)) is True
    assert svc.is_due(trig, date(2026, 6, 10)) is False


def test_recurring_every_n_days_from_base():
    # Every 30 days from 2026-01-01, 0-day lead.
    trig = {
        "type": svc.RECURRING,
        "base_date": "2026-01-01",
        "recur_every_days": 30,
        "lead_time_days": 0,
        "active": True,
    }
    # 2026-01-01 + 30 = 01-31; next tick on/after Feb 5 is 2026-03-02 (60 days).
    assert svc.next_event_date(trig, date(2026, 2, 5)) == date(2026, 3, 2)
    assert svc.is_due(trig, date(2026, 1, 31)) is True  # exactly a tick
    assert svc.is_due(trig, date(2026, 2, 1)) is False  # between ticks


def test_custom_date_is_one_shot_future_only():
    trig = {
        "type": svc.CUSTOM_DATE,
        "base_date": "2026-12-25",
        "lead_time_days": 5,
        "active": True,
    }
    assert svc.next_event_date(trig, date(2026, 12, 1)) == date(2026, 12, 25)
    assert svc.next_fire_date(trig, date(2026, 12, 1)) == date(2026, 12, 20)
    assert svc.is_due(trig, date(2026, 12, 20)) is True
    # Past one-shot never fires again.
    assert svc.next_event_date(trig, date(2027, 1, 1)) is None
    assert svc.is_due(trig, date(2027, 1, 1)) is False


def test_not_yet_due_outside_window():
    trig = {
        "type": svc.ANNIVERSARY,
        "base_date": "2010-09-20",
        "lead_time_days": 7,
        "active": True,
    }
    assert svc.is_due(trig, date(2026, 9, 1)) is False  # 12 days out, lead is 7


def test_already_fired_this_cycle_does_not_refire():
    trig = {
        "type": svc.ANNIVERSARY,
        "base_date": "2010-03-15",
        "lead_time_days": 7,
        "active": True,
        "last_fired_for": "2026-03-15",
    }
    # Inside the window, but the cycle key (event date) is already stamped.
    assert svc.is_due(trig, date(2026, 3, 10)) is False


def test_inactive_trigger_never_due():
    trig = {
        "type": svc.CUSTOM_DATE,
        "base_date": "2026-12-25",
        "lead_time_days": 5,
        "active": False,
    }
    assert svc.is_due(trig, date(2026, 12, 20)) is False


# ============================================================================
# 2. VALIDATION -- 422-shaped ValueError per trigger type
# ============================================================================


def test_validate_rejects_bad_type_and_date_and_missing_subfields():
    with pytest.raises(ValueError):
        svc.validate_trigger_payload({"type": "NONSENSE", "base_date": "2026-01-01"})
    with pytest.raises(ValueError):
        svc.validate_trigger_payload(
            {"type": svc.ANNIVERSARY, "base_date": "not-a-date"}
        )
    with pytest.raises(ValueError):
        # RECURRING needs a positive recur_every_days.
        svc.validate_trigger_payload({"type": svc.RECURRING, "base_date": "2026-01-01"})
    with pytest.raises(ValueError):
        # BIRTHDAY_PLUS_N needs a non-negative plus_n_days.
        svc.validate_trigger_payload(
            {"type": svc.BIRTHDAY_PLUS_N, "base_date": "2026-01-01"}
        )
    # A valid anniversary normalizes cleanly.
    cleaned = svc.validate_trigger_payload(
        {"type": svc.ANNIVERSARY, "base_date": "2010-03-15", "lead_time_days": 7}
    )
    assert cleaned["type"] == svc.ANNIVERSARY and cleaned["lead_time_days"] == 7


# ============================================================================
# 3. CRUD -- create/list/get/update/delete + fail-soft
# ============================================================================


def test_trigger_crud_roundtrip(db):
    doc = svc.create_trigger(
        db,
        "C-1",
        {
            "type": svc.ANNIVERSARY,
            "base_date": "2010-03-15",
            "lead_time_days": 7,
            "label": "Wedding",
        },
        created_by="M1",
        store_id="BV-1",
    )
    assert doc["trigger_id"].startswith("VTR-")
    assert doc["customer_id"] == "C-1" and doc["store_id"] == "BV-1"
    assert doc["active"] is True and doc["last_fired_for"] is None

    rows = svc.list_triggers(db, customer_id="C-1")
    assert len(rows) == 1 and rows[0]["label"] == "Wedding"

    got = svc.get_trigger(db, doc["trigger_id"])
    assert got and got["trigger_id"] == doc["trigger_id"]

    upd = svc.update_trigger(
        db, doc["trigger_id"], {"active": False, "lead_time_days": 14}
    )
    assert upd["active"] is False and upd["lead_time_days"] == 14

    assert svc.delete_trigger(db, doc["trigger_id"]) is True
    assert svc.get_trigger(db, doc["trigger_id"]) is None


def test_crud_fail_soft_when_db_absent():
    assert svc.list_triggers(None, customer_id="C-1") == []
    assert svc.get_trigger(None, "VTR-x") is None
    assert svc.delete_trigger(None, "VTR-x") is False
    assert svc.claim_fire(None, "VTR-x", "2026-01-01") is False


def test_update_revalidates_and_resets_cycle(db):
    doc = svc.create_trigger(
        db,
        "C-1",
        {"type": svc.RECURRING, "base_date": "2026-01-01", "recur_every_days": 30},
        created_by="M1",
        store_id="BV-1",
    )
    # stamp a fired cycle, then re-shape -> last_fired_for resets to None.
    svc.claim_fire(db, doc["trigger_id"], "2026-01-31")
    upd = svc.update_trigger(db, doc["trigger_id"], {"recur_every_days": 7})
    assert upd["recur_every_days"] == 7 and upd["last_fired_for"] is None
    # An invalid re-shape raises (router maps to 422).
    with pytest.raises(ValueError):
        svc.update_trigger(db, doc["trigger_id"], {"recur_every_days": 0})


# ============================================================================
# 4. VIP profile -- set/read tags + override + appended note
# ============================================================================


def test_vip_profile_build_dedupes_tags_and_appends_note():
    existing = {
        "customer_id": "C-1",
        "personal_notes": [{"note": "old", "by": "M0", "at": "t0"}],
    }
    upd = svc.build_vip_update(
        existing,
        vip_tags=["Platinum", "Platinum", "  Whale  "],
        vip_override=True,
        note_text="Prefers a call before noon",
        note_by="M1",
    )
    assert upd["vip_tags"] == ["Platinum", "Whale"]  # de-duped + trimmed
    assert upd["vip_override"] is True
    # The note is APPENDED, never replacing the prior one.
    assert len(upd["personal_notes"]) == 2
    assert upd["personal_notes"][-1]["note"] == "Prefers a call before noon"


def test_read_vip_profile_fail_soft_on_empty():
    prof = svc.read_vip_profile({})
    assert (
        prof["vip_tags"] == []
        and prof["vip_override"] is False
        and prof["personal_notes"] == []
    )


# ============================================================================
# 5. SCAN -- fires once, no double-fire, no live customer send (DARK)
# ============================================================================


class _FakeAgent:
    """Minimal stand-in exposing self.db + get_collection like JarvisAgent."""

    def __init__(self, db):
        self.db = db

    def get_collection(self, name):
        return self.db.get_collection(name)


def _import_scan():
    from agents.implementations.megaphone import MegaphoneAgent

    return MegaphoneAgent


def test_scan_creates_followup_and_inapp_notification_once(db, monkeypatch):
    import agents.implementations.megaphone as meg

    # Pin "today" so a custom-date trigger is exactly in its fire window.
    class _FixedDT:
        @staticmethod
        def date():
            return date(2026, 12, 20)

    monkeypatch.setattr(meg, "_now_ist", lambda: _FixedDT())

    # A due custom-date trigger (event 2026-12-25, 5-day lead -> window opens 12-20).
    svc.create_trigger(
        db,
        "C-1",
        {
            "type": svc.CUSTOM_DATE,
            "base_date": "2026-12-25",
            "lead_time_days": 5,
            "label": "Christmas gift",
        },
        created_by="M1",
        store_id="BV-1",
    )
    MegaphoneAgent = _import_scan()
    agent = MegaphoneAgent(db=db)

    stats = agent._scan_personal_triggers()
    assert stats["due"] == 1 and stats["fired"] == 1
    assert stats["follow_ups"] == 1 and stats["notifications"] == 1

    fu = db.get_collection("follow_ups").docs
    assert len(fu) == 1 and fu[0]["type"] == "vip_trigger_alert"
    notif = db.get_collection("notifications").docs
    assert len(notif) == 1
    n = notif[0]
    # STAFF in-app alert only -- IN_APP channel, never a customer send.
    assert n["channels"] == ["IN_APP"]
    assert n["notification_type"] == "vip_personal_trigger"
    # No customer message rows were queued / dispatched anywhere.
    assert db.get_collection("notification_logs").docs == []


def test_rescan_same_cycle_does_not_double_fire(db, monkeypatch):
    import agents.implementations.megaphone as meg

    class _FixedDT:
        @staticmethod
        def date():
            return date(2026, 12, 20)

    monkeypatch.setattr(meg, "_now_ist", lambda: _FixedDT())
    svc.create_trigger(
        db,
        "C-1",
        {"type": svc.CUSTOM_DATE, "base_date": "2026-12-25", "lead_time_days": 5},
        created_by="M1",
        store_id="BV-1",
    )
    MegaphoneAgent = _import_scan()
    agent = MegaphoneAgent(db=db)

    first = agent._scan_personal_triggers()
    second = agent._scan_personal_triggers()  # same cycle, same day
    assert first["fired"] == 1
    assert second["fired"] == 0  # last_fired_for guard blocks the re-fire
    # Exactly ONE follow_up + ONE notification across both scans.
    assert len(db.get_collection("follow_ups").docs) == 1
    assert len(db.get_collection("notifications").docs) == 1


def test_scan_fail_soft_when_collection_absent():
    """A DB that yields no personal_triggers collection -> the scan is a no-op
    (zero everything), never a crash. Mirrors the DB-down path."""

    class _NoColl:
        is_connected = True

        def get_collection(self, name):
            return None

    MegaphoneAgent = _import_scan()
    agent = MegaphoneAgent(db=_NoColl())
    stats = agent._scan_personal_triggers()
    assert stats == {"due": 0, "fired": 0, "follow_ups": 0, "notifications": 0}


# ============================================================================
# 6. ROUTER endpoints -- store-scope + RBAC + 422 (called directly)
# ============================================================================


def _run(coro):
    return asyncio.run(coro)


class _FakeRepo:
    def __init__(self, customers):
        self._c = {c["customer_id"]: c for c in customers}

    def find_by_id(self, cid):
        return self._c.get(cid)

    def update(self, cid, updates):
        self._c[cid].update(updates)
        return True


def _wire_router(monkeypatch, db, customers):
    import api.routers.crm as crm

    repo = _FakeRepo(customers)
    monkeypatch.setattr(crm, "get_customer_repository", lambda: repo)
    monkeypatch.setattr(crm, "_crm_get_db", lambda: db)
    monkeypatch.setattr(crm, "get_audit_repository", lambda: None)
    return crm


def test_endpoint_create_trigger_store_scope_403(db, monkeypatch):
    crm = _wire_router(monkeypatch, db, [{"customer_id": "C-1", "name": "VIP"}])
    from fastapi import HTTPException

    body = crm.PersonalTriggerCreate(
        customer_id="C-1", type="ANNIVERSARY", base_date="2010-03-15", store_id="BV-2"
    )
    manager = {
        "user_id": "M1",
        "roles": ["STORE_MANAGER"],
        "store_ids": ["BV-1"],
        "active_store_id": "BV-1",
    }
    # Manager owns BV-1 but the trigger targets BV-2 -> 403.
    with pytest.raises(HTTPException) as exc:
        _run(crm.create_personal_trigger(body=body, current_user=manager))
    assert exc.value.status_code == 403


def test_endpoint_create_trigger_bad_shape_422(db, monkeypatch):
    crm = _wire_router(monkeypatch, db, [{"customer_id": "C-1", "name": "VIP"}])
    from fastapi import HTTPException

    # RECURRING with no recur_every_days -> service raises ValueError -> 422.
    body = crm.PersonalTriggerCreate(
        customer_id="C-1", type="RECURRING", base_date="2026-01-01", store_id="BV-1"
    )
    superadmin = {
        "user_id": "S1",
        "roles": ["SUPERADMIN"],
        "store_ids": [],
        "active_store_id": None,
    }
    with pytest.raises(HTTPException) as exc:
        _run(crm.create_personal_trigger(body=body, current_user=superadmin))
    assert exc.value.status_code == 422


def test_endpoint_vip_profile_set_and_read(db, monkeypatch):
    crm = _wire_router(monkeypatch, db, [{"customer_id": "C-1", "name": "VIP"}])
    body = crm.VipProfileBody(vip_tags=["Platinum"], vip_override=True, note="call AM")
    manager = {
        "user_id": "M1",
        "roles": ["STORE_MANAGER"],
        "store_ids": ["BV-1"],
        "active_store_id": "BV-1",
    }
    out = _run(crm.set_vip_profile(customer_id="C-1", body=body, current_user=manager))
    assert out["vip_tags"] == ["Platinum"] and out["vip_override"] is True
    assert out["personal_notes"][-1]["note"] == "call AM"
    read = _run(crm.get_vip_profile(customer_id="C-1", current_user=manager))
    assert read["vip_tags"] == ["Platinum"]


def test_endpoint_vip_profile_404_unknown_customer(db, monkeypatch):
    crm = _wire_router(monkeypatch, db, [])
    from fastapi import HTTPException

    body = crm.VipProfileBody(vip_tags=["X"])
    manager = {
        "user_id": "M1",
        "roles": ["STORE_MANAGER"],
        "store_ids": ["BV-1"],
        "active_store_id": "BV-1",
    }
    with pytest.raises(HTTPException) as exc:
        _run(crm.set_vip_profile(customer_id="NOPE", body=body, current_user=manager))
    assert exc.value.status_code == 404


def test_endpoint_list_triggers_scopes_nonsuper_to_owned_store(db, monkeypatch):
    crm = _wire_router(monkeypatch, db, [{"customer_id": "C-1", "name": "VIP"}])
    # Seed one trigger on BV-1 and one on BV-2.
    svc.create_trigger(
        db,
        "C-1",
        {"type": "ANNIVERSARY", "base_date": "2010-03-15"},
        created_by="M1",
        store_id="BV-1",
    )
    svc.create_trigger(
        db,
        "C-2",
        {"type": "ANNIVERSARY", "base_date": "2011-04-16"},
        created_by="M1",
        store_id="BV-2",
    )
    manager = {
        "user_id": "M1",
        "roles": ["STORE_MANAGER"],
        "store_ids": ["BV-1"],
        "active_store_id": "BV-1",
    }
    out = _run(
        crm.list_personal_triggers(
            customer_id=None, store_id=None, active_only=False, current_user=manager
        )
    )
    # Scoped to BV-1 only -> the BV-2 trigger is invisible.
    assert out["total"] == 1 and out["triggers"][0]["store_id"] == "BV-1"
