"""
IMS 2.0 - TASKMASTER: store-side overdue-PO reminder (procurement Phase 3)
==========================================================================
"PO sent, no delivery logged": a PO still awaiting goods (SENT/ACKNOWLEDGED/
PARTIAL/PARTIALLY_RECEIVED) that is past its expected_date (>= 1 whole day),
or -- when no expected_date was given -- >= 7 whole days past sent_at, must
raise ONE P2 "Purchase" task on the DELIVERY store's worklist (store_id =
delivery_store_id, assigned_to STORE_MANAGER) so it lands on that store's
bell. The dedupe ref is stage-bucketed ("po_overdue:{po_id}:{stage}", stage =
days_overdue // 7) so a fresh reminder fires weekly, never per 5-minute tick.

These tests drive _remind_overdue_pos directly with in-memory fake
collections (no Mongo), same style as test_taskmaster_refund_escalation.py.

No emoji (Windows cp1252). No whole-JSON substring asserts.
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

from agents.implementations.taskmaster import TaskmasterAgent  # noqa: E402
from api.services.po_variance_engine import overdue_po_reminder_specs  # noqa: E402


class _Coll:
    """Minimal in-memory collection: find (eq + $in match), find_one,
    insert_one. Supports the query shapes the reminder sweep uses."""

    def __init__(self, docs=None):
        self.docs = [dict(d) for d in (docs or [])]

    def _match(self, d, q):
        for k, v in q.items():
            dv = d.get(k)
            if isinstance(v, dict) and "$in" in v:
                if dv not in v["$in"]:
                    return False
            elif isinstance(dv, list):
                if v not in dv:
                    return False
            elif dv != v:
                return False
        return True

    def find(self, q=None):
        q = q or {}
        return _Cursor([dict(d) for d in self.docs if self._match(d, q)])

    def find_one(self, q):
        for d in self.docs:
            if self._match(d, q):
                return dict(d)
        return None

    def find_many(self, q):
        return [dict(d) for d in self.docs if self._match(d, q)]

    def insert_one(self, doc):
        self.docs.append(dict(doc))

    def create(self, doc):
        self.docs.append(dict(doc))
        return doc


class _Cursor:
    def __init__(self, rows):
        self._rows = rows

    def limit(self, n):
        return self._rows[:n]

    def __iter__(self):
        return iter(self._rows)


class _DB:
    def __init__(self, colls):
        self._colls = colls

    def get_collection(self, name):
        return self._colls.get(name)


def _iso_days_ago(days: int) -> str:
    return (datetime.now() - timedelta(days=days)).isoformat()


def _po(**over):
    base = {
        "po_id": "PO-1",
        "po_number": "PO-BOK01-2026-0001",
        "vendor_id": "VEN-1",
        "vendor_name": "Essilor India",
        "delivery_store_id": "BV-BOK-01",
        "status": "SENT",
        "sent_at": _iso_days_ago(3),
        "expected_date": _iso_days_ago(2),
    }
    base.update(over)
    return base


def _build_agent(po_docs, *, tasks=None):
    tasks_coll = _Coll(tasks or [])
    colls = {
        "purchase_orders": _Coll(po_docs),
        "tasks": tasks_coll,
        "agent_audit_log": _Coll([]),
    }
    agent = TaskmasterAgent(db=_DB(colls))
    return agent, tasks_coll


def _system_tasks(tasks_coll):
    return [t for t in tasks_coll.docs if t.get("source") == "SYSTEM"]


# ---------------------------------------------------------------------------
# Firing rules
# ---------------------------------------------------------------------------


def test_overdue_by_expected_date_fires_task():
    agent, tasks_coll = _build_agent([_po()])  # expected 2 days ago
    actions = asyncio.run(agent._remind_overdue_pos())

    assert len(actions) == 1
    act = actions[0]
    assert act["action"] == "po_overdue_reminder"
    assert act["po_id"] == "PO-1"
    assert act["store_id"] == "BV-BOK-01"

    created = _system_tasks(tasks_coll)
    assert len(created) == 1
    task = created[0]
    assert task["priority"] == "P2"
    assert task["category"] == "Purchase"
    assert task["store_id"] == "BV-BOK-01"
    assert task["assigned_to"] == "STORE_MANAGER"
    assert task["source_ref"] == "po_overdue:PO-1:0"  # 2d overdue -> stage 0
    assert "PO-BOK01-2026-0001" in task["title"]
    assert "Essilor India" in task["title"]
    assert "expected" in task["title"]


def test_no_expected_date_fires_after_seven_days_sent():
    po = _po(expected_date=None, sent_at=_iso_days_ago(8))
    agent, tasks_coll = _build_agent([po])
    actions = asyncio.run(agent._remind_overdue_pos())

    assert len(actions) == 1
    created = _system_tasks(tasks_coll)
    assert len(created) == 1
    # 8 days since sent -> days_overdue 8 -> stage 1
    assert created[0]["source_ref"] == "po_overdue:PO-1:1"
    assert "no expected date" in created[0]["title"]


def test_no_expected_date_under_seven_days_does_not_fire():
    po = _po(expected_date=None, sent_at=_iso_days_ago(5))
    agent, tasks_coll = _build_agent([po])
    actions = asyncio.run(agent._remind_overdue_pos())
    assert actions == []
    assert _system_tasks(tasks_coll) == []


def test_not_yet_due_expected_date_does_not_fire():
    # Expected date is tomorrow -> nothing to chase yet.
    future = (datetime.now() + timedelta(days=1)).isoformat()
    agent, tasks_coll = _build_agent([_po(expected_date=future)])
    actions = asyncio.run(agent._remind_overdue_pos())
    assert actions == []
    assert _system_tasks(tasks_coll) == []


def test_received_and_cancelled_pos_never_fire():
    pos = [
        _po(po_id="PO-R", status="RECEIVED"),
        _po(po_id="PO-C", status="CANCELLED"),
        _po(po_id="PO-D", status="DRAFT"),
    ]
    agent, tasks_coll = _build_agent(pos)
    actions = asyncio.run(agent._remind_overdue_pos())
    assert actions == []
    assert _system_tasks(tasks_coll) == []


def test_partially_received_po_still_fires():
    # PARTIAL / PARTIALLY_RECEIVED are still awaiting the remaining goods.
    pos = [
        _po(po_id="PO-P1", status="PARTIALLY_RECEIVED"),
        _po(po_id="PO-P2", status="PARTIAL"),
        _po(po_id="PO-A", status="ACKNOWLEDGED"),
    ]
    agent, tasks_coll = _build_agent(pos)
    actions = asyncio.run(agent._remind_overdue_pos())
    assert {a["po_id"] for a in actions} == {"PO-P1", "PO-P2", "PO-A"}
    assert len(_system_tasks(tasks_coll)) == 3


# ---------------------------------------------------------------------------
# Dedupe: stable within a week, advances weekly
# ---------------------------------------------------------------------------


def test_second_tick_same_week_is_deduped():
    agent, tasks_coll = _build_agent([_po()])
    first = asyncio.run(agent._remind_overdue_pos())
    assert len(first) == 1
    second = asyncio.run(agent._remind_overdue_pos())
    assert second == []
    assert len(_system_tasks(tasks_coll)) == 1


def test_completed_task_is_not_refiled_within_same_stage():
    # The manager closed this week's reminder -> no re-file until the stage
    # advances next week (any-state dedupe on source_ref).
    done = {
        "task_id": "TSK-X",
        "source": "SYSTEM",
        "source_ref": "po_overdue:PO-1:0",
        "status": "COMPLETED",
    }
    agent, tasks_coll = _build_agent([_po()], tasks=[done])
    actions = asyncio.run(agent._remind_overdue_pos())
    assert actions == []
    # Only the pre-existing completed task remains.
    assert len(_system_tasks(tasks_coll)) == 1


def test_stage_advances_weekly_pure_spec():
    # Pure-spec check: days 1-6 -> stage 0, 7-13 -> stage 1, 14 -> stage 2.
    now = datetime(2026, 7, 4, 12, 0, 0)

    def spec_for(days):
        po = _po(expected_date=(now - timedelta(days=days)).isoformat())
        out = overdue_po_reminder_specs([po], now=now)
        assert len(out) == 1
        return out[0]

    assert spec_for(1)["source_ref"] == "po_overdue:PO-1:0"
    assert spec_for(6)["source_ref"] == "po_overdue:PO-1:0"
    assert spec_for(7)["source_ref"] == "po_overdue:PO-1:1"
    assert spec_for(13)["source_ref"] == "po_overdue:PO-1:1"
    assert spec_for(14)["source_ref"] == "po_overdue:PO-1:2"


def test_new_stage_fires_even_when_old_stage_task_exists():
    # Week 2: the week-1 task (stage 0) exists but the ref advanced -> fire.
    old = {
        "task_id": "TSK-OLD",
        "source": "SYSTEM",
        "source_ref": "po_overdue:PO-1:0",
        "status": "COMPLETED",
    }
    po = _po(expected_date=_iso_days_ago(8))  # stage 1 now
    agent, tasks_coll = _build_agent([po], tasks=[old])
    actions = asyncio.run(agent._remind_overdue_pos())
    assert len(actions) == 1
    refs = {t["source_ref"] for t in _system_tasks(tasks_coll)}
    assert refs == {"po_overdue:PO-1:0", "po_overdue:PO-1:1"}


# ---------------------------------------------------------------------------
# Task shape: link + payload land on the doc
# ---------------------------------------------------------------------------


def test_task_carries_link_and_payload():
    agent, tasks_coll = _build_agent([_po()])
    asyncio.run(agent._remind_overdue_pos())
    task = _system_tasks(tasks_coll)[0]
    assert task["link"] == "/purchase?tab=purchase-orders"
    payload = task["payload"]
    assert payload["po_id"] == "PO-1"
    assert payload["po_number"] == "PO-BOK01-2026-0001"
    assert payload["vendor_id"] == "VEN-1"
    assert payload["days_overdue"] == 2


# ---------------------------------------------------------------------------
# Fail-soft + cap
# ---------------------------------------------------------------------------


def test_per_po_failure_continues_with_next_po():
    # A PO whose dedupe lookup blows up must not stop the sweep: the healthy
    # PO after it still gets its reminder.
    bad = _po(po_id="PO-BAD")
    good = _po(po_id="PO-GOOD")
    agent, tasks_coll = _build_agent([bad, good])

    original_find_one = tasks_coll.find_one

    def _exploding_find_one(q):
        if "PO-BAD" in str(q.get("source_ref", "")):
            raise RuntimeError("boom")
        return original_find_one(q)

    tasks_coll.find_one = _exploding_find_one
    actions = asyncio.run(agent._remind_overdue_pos())
    # PO-BAD's dedupe error is swallowed (best-effort) and its create still
    # goes through create_system_task's own dedupe; PO-GOOD definitely lands.
    assert "PO-GOOD" in {a["po_id"] for a in actions}
    refs = {t["source_ref"] for t in _system_tasks(tasks_coll)}
    assert "po_overdue:PO-GOOD:0" in refs


def test_task_create_failure_on_one_po_continues():
    # repo.create failing for one PO must not break the others.
    bad = _po(po_id="PO-BAD")
    good = _po(po_id="PO-GOOD")
    agent, tasks_coll = _build_agent([bad, good])

    original_insert = tasks_coll.insert_one
    original_create = tasks_coll.create

    def _selective_create(doc):
        if doc.get("source_ref", "").startswith("po_overdue:PO-BAD"):
            raise RuntimeError("insert failed")
        return original_create(doc)

    def _selective_insert(doc):
        if doc.get("source_ref", "").startswith("po_overdue:PO-BAD"):
            raise RuntimeError("insert failed")
        return original_insert(doc)

    tasks_coll.create = _selective_create
    tasks_coll.insert_one = _selective_insert
    actions = asyncio.run(agent._remind_overdue_pos())
    # The sweep must NOT abort: PO-GOOD is still processed and its task lands.
    # (create_system_task itself is fail-soft on a swallowed repo error, so we
    # assert on what actually reached the collection, not the action list.)
    assert "PO-GOOD" in {a["po_id"] for a in actions}
    refs = {t.get("source_ref") for t in _system_tasks(tasks_coll)}
    assert refs == {"po_overdue:PO-GOOD:0"}


def test_scan_cap_respects_300():
    # 305 overdue POs -> only the first 300 are scanned this tick.
    pos = [_po(po_id=f"PO-{i}") for i in range(305)]
    agent, tasks_coll = _build_agent(pos)
    actions = asyncio.run(agent._remind_overdue_pos())
    assert len(actions) == 300
    assert len(_system_tasks(tasks_coll)) == 300


def test_missing_db_is_silent_noop():
    agent = TaskmasterAgent(db=None)
    actions = asyncio.run(agent._remind_overdue_pos())
    assert actions == []


# ---------------------------------------------------------------------------
# Pure spec edge cases
# ---------------------------------------------------------------------------


def test_spec_skips_po_with_neither_eta_nor_sent_at():
    po = _po(expected_date=None, sent_at=None)
    assert overdue_po_reminder_specs([po]) == []


def test_spec_unparseable_expected_date_falls_back_to_sent_at_rule():
    po = _po(expected_date="not-a-date", sent_at=_iso_days_ago(8))
    specs = overdue_po_reminder_specs([po])
    assert len(specs) == 1
    assert specs[0]["overdue_basis"] == "sent_at"
