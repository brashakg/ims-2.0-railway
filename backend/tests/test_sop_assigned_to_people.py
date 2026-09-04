# ============================================================================
# A daily SOP belongs to a PERSON, not to whoever pressed the button
# ============================================================================
# Owner ruling 2026-09-03: "instead of assigning it to store manager, Cashier,
# optom, assign it to individuals like sameer, rupesh and so".
#
# THE BUG. POST /tasks/auto-generate built every task with
# `"assigned_to": current_user["user_id"]`. So the manager who ran the morning
# generate was issued the cashier's cash-drawer SOP, the optometrist's
# room-prep SOP and every other one, while nobody else on the floor received
# anything. The template's `assigned_users` field was written by the SOP editor
# and read by NOTHING - the assignment screen had no effect on the world.
#
# THE FIX. One task per named person on the template. A ROLE on a template
# resolves to the people holding it in this store, because a job title cannot
# do the work - Sameer and Rupesh do. Re-running the same day tops up what is
# missing instead of handing everyone a duplicate.

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from unittest.mock import patch

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")
os.environ.setdefault("ENVIRONMENT", "test")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api.routers import tasks as tasks_mod  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402

PRESSER = "u-manager"


class _Repo:
    """In-memory task store. `find_many` honours only what the door asks of it."""

    def __init__(self, existing: Optional[List[Dict[str, Any]]] = None) -> None:
        self.rows: List[Dict[str, Any]] = list(existing or [])

    def create(self, doc: dict) -> dict:
        self.rows.append(doc)
        return doc

    def find_many(self, flt: Optional[dict] = None) -> List[Dict[str, Any]]:
        out = []
        for r in self.rows:
            if flt:
                if "store_id" in flt and r.get("store_id") != flt["store_id"]:
                    continue
                if "source" in flt and r.get("source") != flt["source"]:
                    continue
                window = flt.get("created_at") or {}
                created = r.get("created_at")
                if window and isinstance(created, datetime):
                    if "$gte" in window and created < window["$gte"]:
                        continue
                    if "$lt" in window and created >= window["$lt"]:
                        continue
            out.append(r)
        return out


class _Users:
    """Staff by role, per store. Only find_by_role is used by the door."""

    STAFF = {
        ("CASHIER", "S1"): [
            {"user_id": "u-sameer", "name": "Sameer"},
            {"user_id": "u-rupesh", "name": "Rupesh"},
        ],
        ("OPTOMETRIST", "S1"): [{"user_id": "u-neha", "name": "Neha"}],
        # A cashier at the OTHER shop, who must never be handed S1's work.
        ("CASHIER", "S2"): [{"user_id": "u-other", "name": "Other Shop"}],
    }

    def find_by_role(self, role: str, store_id: Optional[str] = None):
        return self.STAFF.get((role, store_id), [])


def _template(**over) -> Dict[str, Any]:
    tpl = {
        "template_id": "SOP-1",
        "title": "Open the till",
        "description": "Morning routine",
        "category": "Operations",
        "frequency": "DAILY",
        "steps": [{"step_number": 1, "instruction": "Count the drawer"}],
        "assigned_roles": [],
        "assigned_users": [],
        "store_id": "S1",
    }
    tpl.update(over)
    return tpl


class _SopCol:
    def __init__(self, templates: List[Dict[str, Any]]) -> None:
        self._t = templates

    def find(self, *_a, **_kw):
        return list(self._t)


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(tasks_mod.router, prefix="/tasks")

    async def _user():
        return {
            "user_id": PRESSER,
            "active_store_id": "S1",
            "roles": ["STORE_MANAGER"],
        }

    app.dependency_overrides[get_current_user] = _user
    return app


_APP = _app()


def _generate(templates, repo, users=None):
    """Press the button, return (json, repo)."""
    with patch.object(tasks_mod, "get_task_repository", return_value=repo), patch.object(
        tasks_mod, "_sop_collection", return_value=_SopCol(templates)
    ), patch.object(
        tasks_mod, "get_user_repository", return_value=users or _Users()
    ), patch.object(
        tasks_mod, "validate_store_access", return_value="S1"
    ):
        res = TestClient(_APP).post("/tasks/auto-generate")
    assert res.status_code == 200, res.text
    return res.json()


def _assignees(repo) -> List[str]:
    return sorted(t["assigned_to"] for t in repo.rows)


# ---------------------------------------------------------------------------


def test_named_people_each_get_their_own_task():
    """THE RULING. Two people named on the template, two tasks, one each."""
    repo = _Repo()
    body = _generate([_template(assigned_users=["u-sameer", "u-rupesh"])], repo)

    assert body["generated"] == 2, body
    assert _assignees(repo) == ["u-rupesh", "u-sameer"]
    # And NOT to the person who pressed the button - the whole defect.
    assert PRESSER not in _assignees(repo)


def test_a_role_on_a_template_resolves_to_the_people_holding_it():
    """A job title cannot do the work. CASHIER means Sameer and Rupesh."""
    repo = _Repo()
    body = _generate([_template(assigned_roles=["CASHIER"])], repo)

    assert body["generated"] == 2, body
    assert _assignees(repo) == ["u-rupesh", "u-sameer"]


def test_a_role_resolves_within_THIS_store_only():
    """The negative control for the test above. The cashier at the other shop
    holds the same role and must never be handed this shop's procedure."""
    repo = _Repo()
    _generate([_template(assigned_roles=["CASHIER"])], repo)
    assert "u-other" not in _assignees(repo)


def test_one_person_named_twice_gets_one_task():
    """Named directly AND covered by a role is still one person."""
    repo = _Repo()
    body = _generate(
        [_template(assigned_users=["u-sameer"], assigned_roles=["CASHIER"])], repo
    )
    assert body["generated"] == 2, body  # Sameer + Rupesh, not Sameer twice
    assert _assignees(repo) == ["u-rupesh", "u-sameer"]


def test_a_template_with_nobody_on_it_is_named_back_not_silently_dropped():
    """Work must not stop happening, and the gap must be visible and fixable.

    Counting them ("3 templates unassigned") would not be actionable - the
    manager has to open a specific one and put a specific person on it.
    """
    repo = _Repo()
    body = _generate([_template(title="Lock up")], repo)

    assert body["generated"] == 1
    assert _assignees(repo) == [PRESSER]  # it still gets done today
    assert body["unassigned_templates"] == ["Lock up"]
    assert "Lock up" in body["message"]


def test_pressing_generate_twice_does_not_duplicate_anyone_s_day():
    """Fanning out per person multiplies this hazard, so it is closed here.

    Without the guard the second press hands Sameer and Rupesh a second copy
    of every SOP, and a checklist nobody trusts stops being followed.
    """
    repo = _Repo()
    tpl = _template(assigned_users=["u-sameer", "u-rupesh"])
    _generate([tpl], repo)
    body = _generate([tpl], repo)

    assert body["generated"] == 0, body
    assert len(repo.rows) == 2


def test_a_person_added_after_this_morning_still_gets_today_s_task():
    """The negative control for the guard: it must top up, not lock the day.

    A test that only checked "second press generates 0" would also pass on a
    door that refused to generate anything at all after the first run.
    """
    repo = _Repo()
    _generate([_template(assigned_users=["u-sameer"])], repo)
    body = _generate([_template(assigned_users=["u-sameer", "u-rupesh"])], repo)

    assert body["generated"] == 1, body
    assert _assignees(repo) == ["u-rupesh", "u-sameer"]


def test_yesterday_s_tasks_do_not_suppress_today_s():
    """The window is TODAY. A daily SOP that stopped reappearing after day one
    would be the worst possible outcome of the dedupe guard."""
    yesterday = datetime.now() - timedelta(days=1)
    repo = _Repo(
        [
            {
                "task_id": "old",
                "store_id": "S1",
                "source": "SOP",
                "sop_template_id": "SOP-1",
                "assigned_to": "u-sameer",
                "created_at": yesterday,
            }
        ]
    )
    body = _generate([_template(assigned_users=["u-sameer"])], repo)
    assert body["generated"] == 1, body


def test_no_templates_means_no_tasks_and_says_so():
    """The SOP-fiction ruling, still in force: an empty checklist is honest."""
    repo = _Repo()
    body = _generate([], repo)
    assert body["generated"] == 0
    assert repo.rows == []
    assert "no daily" in body["message"].lower()


@pytest.mark.parametrize("phrase", ["5,000", "starting float", "minimum 50%"])
def test_no_invented_procedure_can_reach_a_member_of_staff(phrase):
    """Guards the deletion, not just the fix: with no templates configured the
    door must produce nothing at all, so no invented figure can be issued."""
    repo = _Repo()
    _generate([], repo)
    blob = repr(repo.rows)
    assert phrase not in blob
