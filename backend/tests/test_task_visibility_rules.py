"""Pins two corners of services/task_visibility.py that the role suite misses:
a started-but-late task is overdue, and catalogue-flag tasks (no shop) are
seen by exactly the people who raise or clear flags."""

import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import mongomock  # noqa: E402

from api.services import catalog_flags  # noqa: E402
from api.services.task_visibility import overdue_filter, visible  # noqa: E402


def _user(role, uid="u1"):
    return {"user_id": uid, "roles": [role], "store_ids": ["S1"], "active_store_id": "S1"}


def _coll(*docs):
    c = mongomock.MongoClient().db.tasks
    c.insert_many([dict(d) for d in docs])
    return c


def test_an_in_progress_task_past_due_is_overdue():
    late = datetime.now() - timedelta(hours=1)
    c = _coll(
        {"task_id": "A", "status": "IN_PROGRESS", "due_at": late},
        {"task_id": "B", "status": "in_progress", "due_at": late},
        {"task_id": "C", "status": "COMPLETED", "due_at": late},
    )
    assert sorted(t["task_id"] for t in c.find(overdue_filter())) == ["A", "B"]


def test_flag_tasks_are_seen_by_flag_handlers_only():
    c = _coll(
        {"task_id": "F", "store_id": None, "assigned_to": None,
         "payload": {"kind": catalog_flags.TASK_KIND}},
        {"task_id": "S", "store_id": "S1", "assigned_to": "x"},
    )

    def seen(role):
        return sorted(t["task_id"] for t in c.find(visible({}, _user(role))))

    assert seen("STORE_MANAGER") == ["F", "S"]  # raises flags
    assert seen("CATALOG_MANAGER") == ["F"]  # clears flags
    assert seen("ADMIN") == ["F", "S"]
    assert seen("AREA_MANAGER") == ["S"]
    assert seen("SALES_STAFF") == []
