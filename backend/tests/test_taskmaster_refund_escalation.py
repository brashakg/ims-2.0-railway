"""
IMS 2.0 - TASKMASTER: expired refund-approval escalation (#10 / F27)
====================================================================
When a REFUND_APPROVAL_MATRIX request expires unactioned (the 60-min TTL
lapsed without a manager PIN-approving it), the 5-minute TASKMASTER tick must
turn it into an accountable P1 task assigned to the next escalation rung from
the requesting cashier -- so a refund that needed sign-off but timed out is
never silently dropped.

These tests drive _escalate_expired_refund_approvals directly with in-memory
fake collections (no Mongo, no real ApprovalEngine). They assert:
  - an EXPIRED REFUND_APPROVAL_MATRIX request -> a P1 task to the cashier's
    store manager, naming the order + amount + cashier,
  - the task is deduped (a second tick does NOT re-file it),
  - a non-expired (REQUESTED) request is NOT escalated,
  - a non-refund EXPIRED request (different action_type) is ignored.

No emoji (Windows cp1252). No whole-JSON substring asserts.
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

from agents.implementations.taskmaster import TaskmasterAgent  # noqa: E402


class _Coll:
    """Minimal in-memory collection: find (eq match), find_one, insert_one,
    create. Supports the few query shapes the escalation sweep uses."""

    def __init__(self, docs=None):
        self.docs = [dict(d) for d in (docs or [])]

    def _match(self, d, q):
        for k, v in q.items():
            dv = d.get(k)
            if isinstance(v, dict) and "$in" in v:
                if dv not in v["$in"]:
                    return False
            elif isinstance(dv, list):
                # Mongo array-membership semantics: {"roles": "X"} matches a doc
                # whose roles list CONTAINS X.
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

    # TaskRepository.create delegates to the underlying collection; here the
    # repo wraps this coll, so create() lands via the repo. We expose create on
    # the coll too for safety.
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


def _expired_refund_req(**over):
    base = {
        "request_id": "REQ-abc123",
        "action_type": "REFUND_APPROVAL_MATRIX",
        "status": "EXPIRED",
        "requested_by": "cashier-1",
        "store_id": "BV-BOK-01",
        "amount": 3500.0,
        "context": {"order_id": "ORD-9", "order_number": "ORD-BOK01-2026-9"},
        "expires_at": datetime.now(timezone.utc) - timedelta(minutes=5),
    }
    base.update(over)
    return base


def _build_agent(approval_docs, *, users=None, tasks=None):
    users = users if users is not None else [
        {"user_id": "cashier-1", "roles": ["SALES_CASHIER"], "store_ids": ["BV-BOK-01"],
         "is_active": True, "full_name": "Asha Cashier"},
        {"user_id": "mgr-1", "roles": ["STORE_MANAGER"], "store_ids": ["BV-BOK-01"],
         "is_active": True, "full_name": "Ravi Manager"},
    ]
    tasks_coll = _Coll(tasks or [])
    colls = {
        "approval_requests": _Coll(approval_docs),
        "tasks": tasks_coll,
        "users": _Coll(users),
        "agent_audit_log": _Coll([]),
    }
    agent = TaskmasterAgent(db=_DB(colls))
    return agent, tasks_coll


def test_expired_refund_approval_creates_p1_task_to_manager():
    agent, tasks_coll = _build_agent([_expired_refund_req()])
    actions = asyncio.run(agent._escalate_expired_refund_approvals())

    assert len(actions) == 1
    act = actions[0]
    assert act["action"] == "refund_approval_escalated"
    assert act["request_id"] == "REQ-abc123"
    assert act["to"] == "mgr-1"  # next rung up from the cashier

    # The task itself: P1, Finance, assigned to the manager, naming the cashier
    # (by NAME) + order + amount, deduped by source_ref.
    created = [t for t in tasks_coll.docs if t.get("source") == "SYSTEM"]
    assert len(created) == 1
    task = created[0]
    assert task["priority"] == "P1"
    assert task["assigned_to"] == "mgr-1"
    assert task["source_ref"] == "refund_approval_expired:REQ-abc123"
    assert "Asha Cashier" in task["description"]
    assert "ORD-BOK01-2026-9" in task["description"]


def test_expired_refund_approval_is_deduped_on_second_tick():
    agent, tasks_coll = _build_agent([_expired_refund_req()])
    first = asyncio.run(agent._escalate_expired_refund_approvals())
    assert len(first) == 1
    # A second tick must NOT re-file the same expired approval.
    second = asyncio.run(agent._escalate_expired_refund_approvals())
    assert second == []
    assert len([t for t in tasks_coll.docs if t.get("source") == "SYSTEM"]) == 1


def test_requested_refund_approval_is_not_escalated():
    # Only EXPIRED requests escalate; a still-live REQUESTED one is ignored.
    agent, tasks_coll = _build_agent([_expired_refund_req(status="REQUESTED")])
    actions = asyncio.run(agent._escalate_expired_refund_approvals())
    assert actions == []
    assert [t for t in tasks_coll.docs if t.get("source") == "SYSTEM"] == []


def test_non_refund_expired_request_is_ignored():
    # An EXPIRED request of a different action_type is not our concern here.
    agent, tasks_coll = _build_agent(
        [_expired_refund_req(action_type="leave_approval")]
    )
    actions = asyncio.run(agent._escalate_expired_refund_approvals())
    assert actions == []
    assert [t for t in tasks_coll.docs if t.get("source") == "SYSTEM"] == []
