"""
IMS 2.0 - Task scan reliability (audit F13 + F14)
=================================================
Two silent-failure bugs in the task automation scans:

F13  /tasks/scan/payment-variance built an ISO-STRING cutoff and compared it
     against orders.created_at, which is a BSON Date on every live write path
     (BaseRepository._add_timestamps stamps datetime.now(); shopify_ingest
     persists naive-UTC datetimes). Mongo type-bracketing means a string bound
     never compares against a Date field, so the scan matched ZERO orders and
     reported a permanently clean "no variances found". Fixed with the BUG-031
     dual window (datetime window OR string window via $or), the same shape as
     finance._cash_sales_for_window.

F14  The SLA auto-escalation scan read ONE UNORDERED slice (limit=500 in the
     router, .limit(200) in the TASKMASTER tick), so a genuinely breached
     P0/P1 outside that arbitrary slice never escalated. Fixed by filtering
     candidates server-side, sorting most-overdue-first and PAGING through the
     whole candidate set (bounded memory, bounded actions).

Everything here is in-memory: the fake order collection reproduces Mongo's
type-bracketing exactly (a $gte datetime bound skips string values and vice
versa), so the F13 tests genuinely FAIL against the old string-only filter.
An optional real-mongo test (skipped without a local/CI mongod) proves the
fake's type-bracketing matches the real server.

No emoji (Windows cp1252). Notifications are patched out - nothing dispatches.
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from unittest.mock import patch

import pytest

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")
os.environ.setdefault("ENVIRONMENT", "test")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from agents.implementations.taskmaster import TaskmasterAgent  # noqa: E402
from api.routers import tasks as tasks_mod  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402
from api.services.task_sla import MAX_ESCALATION_LEVEL  # noqa: E402


STORE = "S1"


# ===========================================================================
# Mongo-faithful in-memory query helpers
# ===========================================================================


def _gte(value: Any, bound: Any) -> bool:
    """`$gte` with MongoDB TYPE-BRACKETING: values of a different BSON type
    than the bound never compare (a string field is invisible to a Date bound
    and vice versa) -- the exact semantics that made F13 a silent no-op."""
    if isinstance(bound, datetime):
        return isinstance(value, datetime) and value >= bound
    if isinstance(bound, str):
        return isinstance(value, str) and value >= bound
    return False


def _match(doc: Dict[str, Any], query: Dict[str, Any]) -> bool:
    """Support the operators the scans actually use: $or, $in, $gte, $not/$gte
    and plain equality."""
    for key, cond in (query or {}).items():
        if key == "$or":
            if not any(_match(doc, clause) for clause in cond):
                return False
            continue
        value = doc.get(key)
        if isinstance(cond, dict):
            if "$in" in cond and value not in cond["$in"]:
                return False
            if "$gte" in cond and not _gte(value, cond["$gte"]):
                return False
            if "$not" in cond:
                inner = cond["$not"]
                if "$gte" in inner:
                    bound = inner["$gte"]
                    # $not matches when the field is missing or fails $gte.
                    if isinstance(value, (int, float)) and value >= bound:
                        return False
        elif isinstance(value, list):
            # Mongo array-membership: {"roles": "X"} matches a doc whose roles
            # list CONTAINS X (used by the escalation role-ladder lookup).
            if cond not in value:
                return False
        elif value != cond:
            return False
    return True


def _sort_key(doc: Dict[str, Any], spec: List[tuple]):
    """Mongo-ish sort key: missing/None sorts before any value."""
    key = []
    for field, direction in spec:
        value = doc.get(field)
        present = 0 if value is None else 1
        if isinstance(value, datetime):
            ordinal = value.timestamp()
        elif isinstance(value, str):
            ordinal = 0.0
        elif isinstance(value, (int, float)):
            ordinal = float(value)
        else:
            ordinal = 0.0
        key.append(present * direction)
        key.append(ordinal * direction)
    return tuple(key)


# ===========================================================================
# F13 - payment-variance scan: dual date window
# ===========================================================================


class _FakeOrderRepo:
    """Repo whose find_many honours the dual window with real type-bracketing.
    Records the filters it was called with so tests can assert the shape."""

    def __init__(self, orders: List[Dict[str, Any]]):
        self._orders = [dict(o) for o in orders]
        self.calls: List[dict] = []

    def find_many(self, filters=None, sort=None, skip=0, limit=100):
        self.calls.append({"filters": filters, "sort": sort, "limit": limit})
        rows = [dict(o) for o in self._orders if _match(o, filters or {})]
        if sort:
            rows.sort(key=lambda d: _sort_key(d, sort))
        return rows[skip : skip + limit] if limit else rows[skip:]


class _FakeTaskRepo:
    """Task repo good enough for create_system_task dedupe + escalation."""

    def __init__(self, tasks: Optional[List[Dict[str, Any]]] = None):
        self.tasks: List[Dict[str, Any]] = [dict(t) for t in (tasks or [])]
        self.calls: List[dict] = []
        self.updates: List[tuple] = []

    def find_many(self, filters=None, sort=None, skip=0, limit=100):
        self.calls.append(
            {"filters": filters, "sort": sort, "skip": skip, "limit": limit}
        )
        rows = [dict(t) for t in self.tasks if _match(t, filters or {})]
        if sort:
            rows.sort(key=lambda d: _sort_key(d, sort))
        return rows[skip : skip + limit] if limit else rows[skip:]

    def find_by_id(self, task_id):
        for t in self.tasks:
            if t.get("task_id") == task_id:
                return dict(t)
        return None

    def create(self, doc):
        self.tasks.append(dict(doc))
        return doc

    def update(self, task_id, data):
        for t in self.tasks:
            if t.get("task_id") == task_id:
                t.update(data)
                self.updates.append((task_id, dict(data)))
                return True
        return False


def _order(order_id: str, created_at: Any, **over) -> Dict[str, Any]:
    """An OVERPAID order (grand_total 100, amount_paid 150) -> one anomaly."""
    doc = {
        "order_id": order_id,
        "store_id": STORE,
        "grand_total": 100,
        "amount_paid": 150,
        "created_at": created_at,
    }
    doc.update(over)
    return doc


def _client(roles=("STORE_MANAGER",), store=STORE) -> TestClient:
    app = FastAPI()
    app.include_router(tasks_mod.router, prefix="/tasks")

    async def _user():
        return {
            "user_id": "u-test",
            "active_store_id": store,
            "roles": list(roles),
            "store_ids": [store],
        }

    app.dependency_overrides[get_current_user] = _user
    return TestClient(app)


def _run_variance_scan(orders, *, days=7):
    order_repo = _FakeOrderRepo(orders)
    task_repo = _FakeTaskRepo()
    with patch.object(
        tasks_mod, "get_order_repository", return_value=order_repo
    ), patch.object(tasks_mod, "get_task_repository", return_value=task_repo):
        resp = _client().post(f"/tasks/scan/payment-variance?days={days}")
    assert resp.status_code == 200, resp.text
    return resp.json(), order_repo, task_repo


class TestPaymentVarianceDateWindow:
    def test_bson_date_order_inside_window_is_matched(self):
        """THE F13 REGRESSION: a POS order whose created_at is a real BSON Date
        (what every live write path stores) must be scanned. Against the old
        ISO-STRING-only cutoff this matched nothing and the scan was dead."""
        recent = datetime.now() - timedelta(days=2)
        body, _repo, task_repo = _run_variance_scan([_order("O-DATE", recent)])

        assert body["scanned"] == 1
        assert body["anomalies"] == 1
        assert body["details"][0]["order_id"] == "O-DATE"
        assert body["details"][0]["kind"] == "OVERPAID"
        assert body["tasks_created"] == 1
        assert task_repo.tasks[0]["source_ref"] == "payvar:O-DATE"

    def test_legacy_iso_string_order_inside_window_is_also_matched(self):
        """The dual window keeps legacy ISO-string created_at rows visible."""
        recent_iso = (datetime.now() - timedelta(days=2)).isoformat()
        body, _repo, _tasks = _run_variance_scan([_order("O-STR", recent_iso)])

        assert body["scanned"] == 1
        assert body["anomalies"] == 1
        assert body["details"][0]["order_id"] == "O-STR"

    def test_both_typings_matched_in_one_scan(self):
        now = datetime.now()
        body, _repo, _tasks = _run_variance_scan(
            [
                _order("O-DATE", now - timedelta(days=1)),
                _order("O-STR", (now - timedelta(days=3)).isoformat()),
            ]
        )
        assert body["scanned"] == 2
        assert {d["order_id"] for d in body["details"]} == {"O-DATE", "O-STR"}

    def test_orders_older_than_window_are_excluded(self):
        """The window must still WORK -- both arms bound the low end."""
        old = datetime.now() - timedelta(days=40)
        body, _repo, _tasks = _run_variance_scan(
            [_order("O-OLD-DATE", old), _order("O-OLD-STR", old.isoformat())],
            days=7,
        )
        assert body["scanned"] == 0
        assert body["anomalies"] == 0
        assert body["tasks_created"] == 0

    def test_other_store_orders_are_excluded(self):
        body, _repo, _tasks = _run_variance_scan(
            [
                _order("O-MINE", datetime.now() - timedelta(hours=6)),
                _order("O-THEIRS", datetime.now() - timedelta(hours=6), store_id="S2"),
            ]
        )
        assert body["scanned"] == 1
        assert body["details"][0]["order_id"] == "O-MINE"

    def test_filter_carries_both_window_arms_and_a_deterministic_sort(self):
        _body, repo, _tasks = _run_variance_scan(
            [_order("O-DATE", datetime.now() - timedelta(days=1))]
        )
        filters = repo.calls[0]["filters"]
        arms = filters["$or"]
        assert len(arms) == 2
        bounds = [arm["created_at"]["$gte"] for arm in arms]
        assert any(isinstance(b, datetime) for b in bounds), "no datetime window"
        assert any(isinstance(b, str) for b in bounds), "no string window"
        assert repo.calls[0]["sort"] == [("created_at", -1)]


# ===========================================================================
# F14 - SLA escalation scan: ordered, complete, paged
# ===========================================================================


def _breached_task(idx: int, *, priority="P0", minutes_over=600) -> Dict[str, Any]:
    """An OPEN task well past its due_at + grace -> must escalate."""
    due = datetime.now() - timedelta(minutes=minutes_over)
    return {
        "task_id": f"TSK-BREACH-{idx:05d}",
        "title": f"breached {idx}",
        "status": "OPEN",
        "priority": priority,
        "assigned_to": "staff-1",
        "store_id": STORE,
        "escalation_level": 0,
        "created_at": due - timedelta(hours=1),
        "due_at": due,
        "history": [],
    }


def _healthy_task(idx: int) -> Dict[str, Any]:
    """A young P4 task: not past due, ack clock (3 days) not breached."""
    now = datetime.now()
    return {
        "task_id": f"TSK-OK-{idx:05d}",
        "title": f"healthy {idx}",
        "status": "OPEN",
        "priority": "P4",
        "assigned_to": "staff-1",
        "store_id": STORE,
        "escalation_level": 0,
        "created_at": now - timedelta(minutes=1),
        "due_at": now + timedelta(days=5),
        "history": [],
    }


def _run_auto_escalate(tasks):
    repo = _FakeTaskRepo(tasks)
    escalated: List[str] = []

    async def _fake_escalate(_repo, task, *, reason, by, now):
        escalated.append(task["task_id"])
        _repo.update(
            task["task_id"],
            {
                "status": "ESCALATED",
                "escalation_level": int(task.get("escalation_level", 0) or 0) + 1,
                "escalated_at": now,
                "escalation_reason": reason,
                "escalated_by": by,
            },
        )
        return {"user_id": "mgr-1"}

    with patch.object(
        tasks_mod, "get_task_repository", return_value=repo
    ), patch.object(
        tasks_mod, "_escalate_reassign_notify", _fake_escalate
    ), patch.object(
        tasks_mod, "_load_sla_config", return_value=None
    ):
        resp = _client().post("/tasks/auto-escalate-overdue")
    assert resp.status_code == 200, resp.text
    return resp.json(), escalated, repo


class TestAutoEscalateScanCompleteness:
    def test_breaches_beyond_the_old_500_cap_still_escalate(self):
        """THE F14 REGRESSION: 1,200 healthy tasks first, then 5 badly-breached
        P0s. The old `find_many(filters, limit=500)` never saw them, so they
        never escalated. The paged scan must escalate every one."""
        tasks = [_healthy_task(i) for i in range(1200)]
        tasks += [_breached_task(i) for i in range(5)]

        body, escalated, _repo = _run_auto_escalate(tasks)

        assert body["escalated"] == 5
        assert set(escalated) == {f"TSK-BREACH-{i:05d}" for i in range(5)}
        assert body["scanned"] == 1205
        assert body["truncated"] is False

    def test_nothing_unbreached_escalates(self):
        body, escalated, _repo = _run_auto_escalate(
            [_healthy_task(i) for i in range(700)]
        )
        assert body["escalated"] == 0
        assert escalated == []
        assert body["scanned"] == 700

    def test_most_overdue_are_escalated_first(self):
        """Ordering is most-overdue-first, so if a cap ever bites it bites the
        least-overdue tail, never the emergencies."""
        tasks = [_healthy_task(i) for i in range(600)]
        tasks.append(_breached_task(1, minutes_over=200))  # least overdue
        tasks.append(_breached_task(2, minutes_over=5000))  # most overdue
        tasks.append(_breached_task(3, minutes_over=1500))

        _body, escalated, _repo = _run_auto_escalate(tasks)

        assert escalated == [
            "TSK-BREACH-00002",
            "TSK-BREACH-00003",
            "TSK-BREACH-00001",
        ]

    def test_scan_is_paged_sorted_and_read_only_first(self):
        tasks = [_healthy_task(i) for i in range(1100)]
        tasks.append(_breached_task(9))
        _body, _escalated, repo = _run_auto_escalate(tasks)

        skips = [c["skip"] for c in repo.calls if c["filters"].get("status")]
        assert skips == [0, 500, 1000], skips
        for call in repo.calls:
            if call["filters"].get("status"):
                assert call["limit"] == 500
                assert call["sort"] == [("due_at", 1), ("created_at", 1)]

    def test_topped_out_tasks_are_filtered_server_side(self):
        """A task at the top of the ladder can never escalate again -- it must
        not consume page budget."""
        flt = tasks_mod.build_escalation_candidate_filter(STORE)
        assert flt["escalation_level"] == {"$not": {"$gte": MAX_ESCALATION_LEVEL}}
        assert flt["store_id"] == STORE
        assert "OPEN" in flt["status"]["$in"]
        assert "ESCALATED" in flt["status"]["$in"]

        maxed = _breached_task(1)
        maxed["escalation_level"] = MAX_ESCALATION_LEVEL
        maxed["status"] = "ESCALATED"
        maxed["escalated_at"] = datetime.now() - timedelta(days=30)

        body, escalated, _repo = _run_auto_escalate([maxed])
        assert body["scanned"] == 0
        assert escalated == []

    def test_no_date_prefilter_so_ack_clock_breaches_are_still_seen(self):
        """A task can breach its ACK clock before it is ever past due -- a
        due-date pre-filter would silently drop it."""
        flt = tasks_mod.build_escalation_candidate_filter(STORE)
        assert "due_at" not in flt and "created_at" not in flt

        now = datetime.now()
        not_yet_due = {
            "task_id": "TSK-ACK-1",
            "title": "unacknowledged P0",
            "status": "OPEN",
            "priority": "P0",  # ack 15m
            "assigned_to": "staff-1",
            "store_id": STORE,
            "escalation_level": 0,
            "created_at": now - timedelta(hours=2),
            "due_at": now + timedelta(days=1),  # NOT overdue
            "history": [],
        }
        _body, escalated, _repo = _run_auto_escalate([not_yet_due])
        assert escalated == ["TSK-ACK-1"]


# ===========================================================================
# F14 - the same completeness inside the TASKMASTER 5-minute tick
# ===========================================================================


class _Cursor:
    def __init__(self, rows):
        self._rows = rows

    def sort(self, spec):
        self._rows = sorted(self._rows, key=lambda d: _sort_key(d, spec))
        return self

    def skip(self, n):
        self._rows = self._rows[n:]
        return self

    def limit(self, n):
        self._rows = self._rows[:n]
        return self

    def __iter__(self):
        return iter(self._rows)


class _Coll:
    def __init__(self, docs=None):
        self.docs = [dict(d) for d in (docs or [])]
        self.find_calls = 0

    def find(self, query=None, *_a, **_kw):
        self.find_calls += 1
        return _Cursor([dict(d) for d in self.docs if _match(d, query or {})])

    def find_one(self, query, *_a, **_kw):
        for d in self.docs:
            if _match(d, query):
                return dict(d)
        return None

    def insert_one(self, doc):
        self.docs.append(dict(doc))

    def update_one(self, flt, update):
        for d in self.docs:
            if _match(d, flt):
                d.update(update.get("$set") or {})
                for field, value in (update.get("$push") or {}).items():
                    d.setdefault(field, []).append(value)
                return True
        return False


class _DB:
    def __init__(self, colls):
        self._colls = colls

    def get_collection(self, name):
        return self._colls.get(name)


def _taskmaster_with(tasks):
    docs = []
    for t in tasks:
        doc = dict(t)
        doc["_id"] = doc["task_id"]
        docs.append(doc)
    tasks_coll = _Coll(docs)
    colls = {
        "tasks": tasks_coll,
        "users": _Coll(
            [
                {
                    "user_id": "staff-1",
                    "roles": ["SALES_STAFF"],
                    "store_ids": [STORE],
                    "is_active": True,
                },
                {
                    "user_id": "mgr-1",
                    "roles": ["STORE_MANAGER"],
                    "store_ids": [STORE],
                    "is_active": True,
                },
            ]
        ),
        "agent_audit_log": _Coll([]),
        "notifications": _Coll([]),
        "task_sla_config": _Coll([]),
    }
    return TaskmasterAgent(db=_DB(colls)), tasks_coll, colls


class TestTaskmasterEscalationScan:
    def test_breach_beyond_the_old_200_cap_escalates(self):
        """The tick's `.limit(200)` slice hid breaches on any store with a few
        hundred live tasks."""
        tasks = [_healthy_task(i) for i in range(400)]
        tasks += [_breached_task(i) for i in range(3)]
        agent, tasks_coll, colls = _taskmaster_with(tasks)

        actions = asyncio.run(agent._escalate_overdue_tasks())

        assert len(actions) == 3
        assert {a["task_id"] for a in actions} == {
            f"TSK-BREACH-{i:05d}" for i in range(3)
        }
        assert all(a["to"] == "mgr-1" for a in actions)  # ladder unchanged
        escalated_docs = [d for d in tasks_coll.docs if d["status"] == "ESCALATED"]
        assert len(escalated_docs) == 3
        assert all(d["escalation_level"] == 1 for d in escalated_docs)
        # In-app notification still fires for each (behaviour unchanged).
        assert len(colls["notifications"].docs) == 3

    def test_healthy_tasks_are_untouched(self):
        agent, tasks_coll, _colls = _taskmaster_with(
            [_healthy_task(i) for i in range(300)]
        )
        actions = asyncio.run(agent._escalate_overdue_tasks())
        assert actions == []
        assert all(d["status"] == "OPEN" for d in tasks_coll.docs)

    def test_scan_pages_rather_than_truncating(self):
        tasks = [_healthy_task(i) for i in range(1100)]
        tasks.append(_breached_task(7))
        agent, tasks_coll, _colls = _taskmaster_with(tasks)

        actions = asyncio.run(agent._escalate_overdue_tasks())

        assert [a["task_id"] for a in actions] == ["TSK-BREACH-00007"]
        # 1101 candidates -> pages of 500 -> 3 find() calls.
        assert tasks_coll.find_calls == 3

    def test_topped_out_task_is_not_rescanned(self):
        maxed = _breached_task(1)
        maxed["escalation_level"] = MAX_ESCALATION_LEVEL
        maxed["status"] = "ESCALATED"
        maxed["escalated_at"] = datetime.now() - timedelta(days=30)
        agent, _coll, _colls = _taskmaster_with([maxed])
        assert asyncio.run(agent._escalate_overdue_tasks()) == []


# ===========================================================================
# Real-mongo proof of the type-bracketing the fakes model (CI has mongo:7.0)
# ===========================================================================


MONGO_HOST = os.getenv("MONGO_HOST", "127.0.0.1")
MONGO_PORT = int(os.getenv("MONGO_PORT", "27017"))


def _real_mongo_db():
    try:
        from pymongo import MongoClient
    except Exception:  # noqa: BLE001
        pytest.skip("pymongo not installed")
    try:
        client = MongoClient(MONGO_HOST, MONGO_PORT, serverSelectionTimeoutMS=1500)
        client.admin.command("ping")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"no local mongo at {MONGO_HOST}:{MONGO_PORT} ({exc})")
    name = f"ims_test_payvar_{uuid.uuid4().hex[:8]}"
    return client, client[name], name


def test_real_mongo_dual_window_matches_both_typings():
    """Against a REAL server: a string $gte bound cannot see a Date-typed
    created_at (that is why F13 was dead), and the dual window sees both."""
    client, db, name = _real_mongo_db()
    try:
        coll = db["orders"]
        now = datetime.now()
        cutoff_dt = now - timedelta(days=7)
        cutoff_iso = cutoff_dt.isoformat()
        coll.insert_many(
            [
                {"order_id": "D1", "created_at": now - timedelta(days=1)},
                {"order_id": "S1", "created_at": (now - timedelta(days=1)).isoformat()},
                {"order_id": "D-OLD", "created_at": now - timedelta(days=30)},
            ]
        )

        # The OLD filter: a string bound. It cannot match the BSON-Date rows.
        old_hits = {
            d["order_id"] for d in coll.find({"created_at": {"$gte": cutoff_iso}})
        }
        assert "D1" not in old_hits, "type-bracketing assumption is wrong"

        dual = {
            "$or": [
                {"created_at": {"$gte": cutoff_dt}},
                {"created_at": {"$gte": cutoff_iso}},
            ]
        }
        hits = {d["order_id"] for d in coll.find(dual)}
        assert hits == {"D1", "S1"}
    finally:
        client.drop_database(name)
        client.close()


def test_real_mongo_escalation_candidate_filter_and_sort():
    """Against a REAL server: the two load-bearing assumptions of the paged SLA
    scan -- `$not/$gte` keeps docs with NO escalation_level field, and the
    most-overdue-first sort puts missing due_at (ack-clock candidates) first."""
    client, db, name = _real_mongo_db()
    try:
        coll = db["tasks"]
        now = datetime.now()
        coll.insert_many(
            [
                {"task_id": "T-NOFIELD", "status": "OPEN", "store_id": STORE},
                {
                    "task_id": "T-LEVEL0",
                    "status": "OPEN",
                    "store_id": STORE,
                    "escalation_level": 0,
                    "due_at": now - timedelta(days=2),
                },
                {
                    "task_id": "T-MAXED",
                    "status": "ESCALATED",
                    "store_id": STORE,
                    "escalation_level": MAX_ESCALATION_LEVEL,
                    "due_at": now - timedelta(days=9),
                },
                {
                    "task_id": "T-OLDEST",
                    "status": "OPEN",
                    "store_id": STORE,
                    "escalation_level": 1,
                    "due_at": now - timedelta(days=5),
                },
            ]
        )

        flt = tasks_mod.build_escalation_candidate_filter(STORE)
        rows = list(coll.find(flt).sort(tasks_mod.ESCALATION_SCAN_SORT))
        ids = [r["task_id"] for r in rows]

        # Topped-out task excluded; the field-less doc survives $not/$gte.
        assert "T-MAXED" not in ids
        assert "T-NOFIELD" in ids
        # Missing due_at first, then most-overdue first.
        assert ids == ["T-NOFIELD", "T-OLDEST", "T-LEVEL0"]
    finally:
        client.drop_database(name)
        client.close()
