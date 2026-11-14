"""Audit batch b21: SOP edits, handoff snooze/revoke, notification clock + reads.

R12  a deliberately cleared SOP description is saved as cleared.
R28  reordering / renumbering SOP steps never moves a stored tick onto a
     different instruction (a tick follows the step's stable step_id, and a
     reworded step does not inherit the old wording's tick); D47: a page
     opened on an older revision is refused (409) and signs nothing.
R22  a snoozed handoff is hidden when Mongo hands snooze_until back NAIVE.
R06  a notification snooze sent with an offset is stored as naive UTC (the
     offset is converted, not dropped), so "1 hour" is never "in the past".
R40  a failed notification read is a 503, not an empty "all caught up" list.
R23 lives in test_handoffs.py (it needs that file's handoff/file-store fakes).
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import tasks as tasks_mod  # noqa: E402
from api.routers import notifications as notif_mod  # noqa: E402
from api.routers.handoffs import _is_visible_on_hub  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402
from api.services.sop_checklist import merge_checklist  # noqa: E402
from tests.strict_fakes import StrictCollection  # noqa: E402

A = "AUDTA"
MGR_A = {"user_id": "u-mgr", "roles": ["STORE_MANAGER"], "active_store_id": A, "store_ids": [A]}
COUNT, SAFE = "Count display pieces", "Verify safe is locked"


def _client(app_router, prefix, user):
    app = FastAPI()
    app.include_router(app_router, prefix=prefix)

    async def _u():
        return dict(user)

    app.dependency_overrides[get_current_user] = _u
    return TestClient(app)


@pytest.fixture
def sop(monkeypatch):
    tcol = StrictCollection("sop_templates")
    ccol = StrictCollection("sop_completions")
    monkeypatch.setattr(tasks_mod, "_sop_collection", lambda: tcol)
    monkeypatch.setattr(tasks_mod, "_sop_completions_collection", lambda: ccol)
    c = _client(tasks_mod.router, "/tasks", MGR_A)
    r = c.post("/tasks/sop-templates", json={
        "title": "Closing", "description": "Old description", "store_id": A,
        "steps": [{"step_number": 1, "instruction": COUNT}, {"step_number": 2, "instruction": SAFE}],
    })
    assert r.status_code == 201, r.text
    return c, r.json()["template_id"], tcol, ccol


def _checklist(c, tid):
    r = c.get("/tasks/sop-checklist", params={"template_id": tid, "store_id": A, "date": "2026-11-12"})
    assert r.status_code == 200, r.text
    return {i["instruction"]: i["completed"] for i in r.json()["items"]}


def _open(c, tid):
    """What the checklist page holds after loading: its items and revision."""
    r = c.get("/tasks/sop-checklist", params={"template_id": tid, "store_id": A, "date": "2026-11-12"})
    assert r.status_code == 200, r.text
    return r.json()


def _submit(c, tid, page, instruction, completed=True):
    """Tick the step the PAGE shows with this instruction, exactly as the
    page sends it (its step_number, step_id and revision)."""
    item = next(i for i in page["items"] if i["instruction"] == instruction)
    return c.post("/tasks/sop-checklist/item", json={
        "template_id": tid, "step_number": item["step_number"], "step_id": item["step_id"],
        "revision": page["revision"], "completed": completed, "store_id": A, "date": "2026-11-12"})


def _tick(c, tid, n):
    page = _open(c, tid)
    item = next(i for i in page["items"] if i["step_number"] == n)
    r = _submit(c, tid, page, item["instruction"])
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------- R28

def test_r28_reorder_like_the_editor_keeps_the_tick_on_its_own_step(sop):
    c, tid, _, _ = sop
    _tick(c, tid, 1)  # only "count display pieces"
    assert _checklist(c, tid) == {COUNT: True, SAFE: False}
    # The editor's saved shape: swapped and renumbered, no ids sent.
    r = c.patch(f"/tasks/sop-templates/{tid}", json={"steps": [
        {"step_number": 1, "instruction": SAFE}, {"step_number": 2, "instruction": COUNT}]})
    assert r.status_code == 200, r.text
    assert _checklist(c, tid) == {COUNT: True, SAFE: False}


def test_r28_reorder_with_ids_round_tripped_keeps_identity(sop):
    c, tid, tcol, _ = sop
    _tick(c, tid, 1)
    steps = tcol.find_one({"template_id": tid})["steps"]
    ids = {s["instruction"]: s["step_id"] for s in steps}
    assert all(ids.values()) and len(set(ids.values())) == 2
    r = c.patch(f"/tasks/sop-templates/{tid}", json={"steps": [
        {"step_id": ids[SAFE], "step_number": 1, "instruction": SAFE},
        {"step_id": ids[COUNT], "step_number": 2, "instruction": COUNT}]})
    assert r.status_code == 200
    assert {s["instruction"]: s["step_id"] for s in tcol.find_one({"template_id": tid})["steps"]} == ids
    assert _checklist(c, tid) == {COUNT: True, SAFE: False}


def test_r28_reworded_step_does_not_inherit_the_old_tick_but_evidence_stays(sop):
    c, tid, tcol, ccol = sop
    _tick(c, tid, 1)
    sid = tcol.find_one({"template_id": tid})["steps"][0]["step_id"]
    r = c.patch(f"/tasks/sop-templates/{tid}", json={"steps": [
        {"step_id": sid, "step_number": 1, "instruction": "Count AND photograph display pieces"},
        {"step_number": 2, "instruction": SAFE}]})
    assert r.status_code == 200
    assert not any(_checklist(c, tid).values())
    stored = ccol.find_one({"template_id": tid})["items"]
    assert any(i.get("instruction") == COUNT and i.get("completed") for i in stored)


def test_r28_legacy_ticks_stay_with_the_step_that_had_their_number(sop):
    """A template + tick stored before step ids existed (no step_id, no
    instruction on the tick), then reordered by the editor."""
    c, tid, tcol, ccol = sop
    tcol.update_one({"template_id": tid}, {"$set": {"steps": [
        {"step_number": 1, "instruction": COUNT, "warning": None},
        {"step_number": 2, "instruction": SAFE, "warning": None}]}})
    ccol.insert_one({"template_id": tid, "store_id": A, "date": "2026-11-12", "items": [
        {"step_number": 1, "completed": True, "completed_by": "u-mgr", "completed_at": None}]})
    assert _checklist(c, tid) == {COUNT: True, SAFE: False}
    r = c.patch(f"/tasks/sop-templates/{tid}", json={"steps": [
        {"step_number": 1, "instruction": SAFE}, {"step_number": 2, "instruction": COUNT}]})
    assert r.status_code == 200
    assert _checklist(c, tid) == {COUNT: True, SAFE: False}


REORDERED = {"steps": [{"step_number": 1, "instruction": SAFE}, {"step_number": 2, "instruction": COUNT}]}
STALE = "This checklist was changed by an editor; refresh to continue"


@pytest.mark.parametrize("edit", [
    REORDERED,
    {"steps": [{"step_number": 1, "instruction": "Count AND photograph display pieces"},
               {"step_number": 2, "instruction": SAFE}]},
    {"steps": [{"step_number": 1, "instruction": COUNT, "warning": "Use the count sheet"},
               {"step_number": 2, "instruction": SAFE}]},
])
def test_r28_d47_page_opened_on_an_older_revision_is_refused_and_signs_nothing(sop, edit):
    """Codex PR-1436 round 1 P2: the page is opened, a colleague reorders /
    rewords the SOP, the stale page ticks "Count display pieces". It used to
    sign "Verify safe is locked" (step 1 now). D47: refuse, sign nothing."""
    c, tid, _, ccol = sop
    page = _open(c, tid)
    assert c.patch(f"/tasks/sop-templates/{tid}", json=edit).status_code == 200
    before = list(ccol.docs)
    r = _submit(c, tid, page, COUNT)
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == STALE
    assert ccol.docs == before
    assert not any(_checklist(c, tid).values())


def test_r28_d47_old_client_without_a_revision_is_refused(sop):
    c, tid, _, ccol = sop
    r = c.post("/tasks/sop-checklist/item", json={
        "template_id": tid, "step_number": 1, "completed": True, "store_id": A, "date": "2026-11-12"})
    assert r.status_code == 409 and r.json()["detail"] == STALE
    assert ccol.docs == []


def test_r28_d47_step_id_that_does_not_hold_that_number_is_refused(sop):
    c, tid, _, ccol = sop
    page = _open(c, tid)
    safe = next(i for i in page["items"] if i["instruction"] == SAFE)
    r = c.post("/tasks/sop-checklist/item", json={
        "template_id": tid, "step_number": 1, "step_id": safe["step_id"], "revision": page["revision"],
        "completed": True, "store_id": A, "date": "2026-11-12"})
    assert r.status_code == 409
    assert ccol.docs == []


def test_r28_d47_refresh_then_tick_signs_the_right_step_and_retry_is_idempotent(sop):
    c, tid, _, ccol = sop
    _tick(c, tid, 2)  # SAFE signed on the original revision
    old_page = _open(c, tid)
    assert c.patch(f"/tasks/sop-templates/{tid}", json=REORDERED).status_code == 200
    assert _submit(c, tid, old_page, COUNT).status_code == 409
    page = _open(c, tid)
    assert page["revision"] != old_page["revision"]
    first = _submit(c, tid, page, COUNT)
    assert first.status_code == 200, first.text
    assert first.json()["revision"] == page["revision"]
    assert _submit(c, tid, page, COUNT).status_code == 200  # identical retry
    assert _checklist(c, tid) == {COUNT: True, SAFE: True}
    items = ccol.find_one({"template_id": tid})["items"]
    assert sorted(i["instruction"] for i in items) == [COUNT, SAFE]  # one row each, as signed


def test_r28_pure_merge_never_joins_an_id_tick_by_number():
    steps = [{"step_id": "b", "step_number": 1, "instruction": SAFE},
             {"step_id": "a", "step_number": 2, "instruction": COUNT}]
    items = [{"step_id": "a", "step_number": 1, "instruction": COUNT, "completed": True}]
    merged, progress = merge_checklist(steps, items)
    assert [m["completed"] for m in merged] == [False, True]
    assert progress == {"done": 1, "total": 2, "percent": 50}


# ---------------------------------------------------------------- R12

@pytest.mark.parametrize("cleared", ["", None, "   "])
def test_r12_cleared_description_is_saved_cleared(sop, cleared):
    c, tid, tcol, _ = sop
    r = c.patch(f"/tasks/sop-templates/{tid}", json={"description": cleared})
    assert r.status_code == 200, r.text
    assert tcol.find_one({"template_id": tid})["description"] == ""


def test_r12_omitted_description_is_left_unchanged(sop):
    c, tid, tcol, _ = sop
    r = c.patch(f"/tasks/sop-templates/{tid}", json={"title": "Closing v2"})
    assert r.status_code == 200
    assert tcol.find_one({"template_id": tid})["description"] == "Old description"


# ---------------------------------------------------------------- R22

def test_r22_naive_snooze_from_mongo_hides_the_card():
    now = datetime.now(timezone.utc)
    future_naive = (now + timedelta(hours=1)).replace(tzinfo=None)
    past_naive = (now - timedelta(minutes=1)).replace(tzinfo=None)
    assert _is_visible_on_hub({"snooze_until": future_naive}, now) is False
    assert _is_visible_on_hub({"snooze_until": past_naive}, now) is True
    assert _is_visible_on_hub({"snooze_until": (now + timedelta(hours=1)).isoformat()}, now) is False
    # keep / dismiss stay per-recipient and unchanged
    assert _is_visible_on_hub({"kept": True, "snooze_until": future_naive}, now) is True
    assert _is_visible_on_hub({"dismissed": True}, now) is False


# ---------------------------------------------------------------- R06 / R40

class _NotifCol:
    def __init__(self, doc=None, fail=False):
        self.doc, self.fail, self.set = doc, fail, None

    def _maybe_fail(self):
        if self.fail:
            raise RuntimeError("mongo down")

    def find_one(self, *_a, **_k):
        return self.doc

    def update_one(self, _f, update, *_a, **_k):
        self.set = update["$set"]
        return type("R", (), {"matched_count": 1, "modified_count": 1})()

    def find(self, *_a, **_k):
        self._maybe_fail()
        raise AssertionError("unreachable")

    def count_documents(self, *_a, **_k):
        self._maybe_fail()
        return 0


def _notif(monkeypatch, col):
    monkeypatch.setattr(notif_mod, "_coll", lambda: col)
    return _client(notif_mod.router, "/notifications", {"user_id": "u1", "roles": ["STORE_MANAGER"]})


@pytest.mark.parametrize("offset", [timezone.utc, timezone(timedelta(hours=5, minutes=30))])
def test_r06_one_hour_snooze_is_stored_as_utc_one_hour_ahead(monkeypatch, offset):
    col = _NotifCol({"notification_id": "n1", "user_id": "u1", "snooze_count": 0})
    c = _notif(monkeypatch, col)
    until = (datetime.now(timezone.utc) + timedelta(hours=1)).astimezone(offset).isoformat()
    r = c.post("/notifications/n1/snooze", params={"until": until})
    assert r.status_code == 200, r.text
    stored = col.set["snoozed_until"]
    assert stored.tzinfo is None
    utc_now = datetime.now(timezone.utc).replace(tzinfo=None)
    assert timedelta(minutes=55) < stored - utc_now <= timedelta(hours=1)
    assert r.json()["snoozed_until"].endswith("Z")


def test_r40_failed_list_read_is_unavailable_not_empty(monkeypatch):
    c = _notif(monkeypatch, _NotifCol(fail=True))
    assert c.get("/notifications", params={"include_snoozed": True}).status_code == 503
    assert c.get("/notifications/unread-count").status_code == 503


def test_r40_no_database_is_unavailable_not_empty(monkeypatch):
    monkeypatch.setattr(notif_mod, "_coll", lambda: None)
    c = _client(notif_mod.router, "/notifications", {"user_id": "u1", "roles": ["STORE_MANAGER"]})
    assert c.get("/notifications").status_code == 503
    assert c.get("/notifications/unread-count").status_code == 503


def test_r06_oracle_milestone_bell_row_is_stored_as_naive_utc():
    """ORACLE writes TARGET_MILESTONE rows into the same notifications
    collection the bell reads as UTC; an IST wall clock would show 5h30m ahead."""
    from agents.implementations.oracle import OracleAgent

    class _Users:
        def find(self, *_a, **_k):
            return [{"user_id": "u-floor"}]

    class _Notifs:
        docs = []

        def insert_many(self, docs, **_k):
            self.docs.extend(docs)

    notifs = _Notifs()
    agent = OracleAgent(db=None)
    agent.get_collection = lambda name: {"users": _Users(), "notifications": notifs}[name]
    agent._notify_floor_staff("S1", 50)
    assert len(notifs.docs) == 1
    stored = notifs.docs[0]["created_at"]
    assert stored.tzinfo is None
    assert abs(stored - datetime.now(timezone.utc).replace(tzinfo=None)) < timedelta(minutes=1)
