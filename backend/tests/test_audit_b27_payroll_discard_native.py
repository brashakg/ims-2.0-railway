"""
PR #1415 (D15 draft discard) -- Codex round 1, 2026-11-15, on a REAL Mongo
==========================================================================
P1  A discard that stalled past the payroll guard's 120-second lease, then
    failed to write its audit row, PUT THE DRAFT BACK after a period lock had
    committed: a DRAFT inside a locked month, approve and discard 423 for good.
P2  A delete whose acknowledgement was lost removed the draft with no audit
    row; the retry answered 404 and the trace of who discarded what was gone.

The correction: each removal is a durable operation (``payroll_discards``)
written before the delete, holding who asked and the exact draft. A removed
draft is never put back; a lost answer at any step is finished by the next
identical request (or the period lock) from that record, and its audit row is
written exactly once (fixed id, ``audit_logs._id`` unique).

Runs on the real Mongo and the strict in-memory double of
test_audit_b09_payroll_native.py (see its notes for
``CI=1 IMS_TEST_MONGO_PORT=27084 IMS_TEST_MONGO_DB=<your scratch name>``).
Faults are injected at the native collection / audit-repository boundary.
"""

from __future__ import annotations

import os
import sys
import time

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402
from pymongo.errors import AutoReconnect  # noqa: E402

from test_audit_b09_payroll_native import (  # noqa: E402,F401  (rig is a fixture)
    ADMIN,
    EMP,
    EMP2,
    MONTH,
    STORE,
    YEAR,
    _in_thread,
    rig,
)

from api.routers import payroll as payroll_router  # noqa: E402
from database.repositories.audit_repository import AuditRepository  # noqa: E402

DISCARD = "/api/v1/payroll/discard-draft"
ONE = {"month": MONTH, "year": YEAR, "employee_id": EMP}
SHOP = {"month": MONTH, "year": YEAR, "store_id": STORE}
OTHER_ADMIN = {**ADMIN, "user_id": "admin-b27-other", "username": "other"}


@pytest.fixture
def audited(rig, monkeypatch):
    """The real AuditRepository on the rig's own database."""
    monkeypatch.setattr(
        payroll_router, "get_audit_repository",
        lambda: AuditRepository(rig.db.get_collection("audit_logs")), raising=False,
    )
    assert rig.run().status_code == 200
    assert rig.statuses() == ["DRAFT", "DRAFT"]
    return rig


def _discard(rig, body=ONE, user=ADMIN):
    return rig.client(user).post(DISCARD, json=body)


def _row(rig, emp=EMP):
    return rig.db.get_collection("payroll").find_one(
        {"employee_id": emp, "month": MONTH, "year": YEAR})


def _audits(rig, emp=EMP):
    return list(rig.db.get_collection("audit_logs").find(
        {"action": "PAYROLL_DRAFT_DISCARDED", "detail.employee_id": emp}))


def _ops(rig, emp=EMP):
    return list(rig.db.get_collection("payroll_discards").find({"employee_id": emp}))


def _fail_audit_once(monkeypatch, then=None, land=False):
    """The discard's audit write fails once (``land``: it is written but the
    answer is lost). ``then`` runs at that moment, from inside the request;
    the audit log stays down until that first write has answered."""
    real = AuditRepository.create
    state = {"calls": 0, "down": False}

    def create(self, doc):
        if doc.get("action") != "PAYROLL_DRAFT_DISCARDED":
            return real(self, doc)
        if state["down"]:
            return None
        if state["calls"]:
            return real(self, doc)
        state["calls"] += 1
        state["down"] = True
        try:
            if then:
                then()
            if land:
                real(self, doc)
            return None
        finally:
            state["down"] = False

    monkeypatch.setattr(AuditRepository, "create", create)
    return state


def _fault_payroll_delete(monkeypatch, rig, land):
    """The draft's delete_one raises AutoReconnect once: after the real delete
    (``land``: the acknowledgement is lost) or before it (it never ran)."""
    original = rig.coll_cls.delete_one
    fired = []

    def delete_one(self, filter, *a, **k):
        if self.name != "payroll" or fired:
            return original(self, filter, *a, **k)
        fired.append(True)
        if land:
            original(self, filter, *a, **k)
        raise AutoReconnect("synthetic lost delete acknowledgement")

    monkeypatch.setattr(rig.coll_cls, "delete_one", delete_one)
    return fired


def _the_one_audit_says(rig, draft, user=ADMIN):
    [log] = _audits(rig)
    assert log["user_id"] == user["user_id"]
    assert log["entity_id"] == draft["payroll_id"]
    assert log["detail"]["net_salary"] == draft["net_salary"] > 0
    assert (log["detail"]["month"], log["detail"]["year"]) == (MONTH, YEAR)
    return log


# ===========================================================================
# P1 -- a removed draft is never put back, so none can land behind a lock
# ===========================================================================


def test_p1_expired_discard_whose_audit_fails_after_a_lock_leaves_no_draft_behind_it(
    audited, monkeypatch
):
    """Codex's exact sequence: at the audit write the guard's heartbeat is 180
    seconds old (a stalled request), "lock anyway" commits, the audit write
    fails. Before: the draft was put back INSIDE the locked month."""
    rig = audited
    # Codex's month had one employee: the other draft would stop the lock.
    rig.db.get_collection("payroll").delete_one({"employee_id": EMP2})
    draft = _row(rig)
    seen = {}

    def stall_then_lock():
        assert _row(rig) is None  # the delete has happened
        rig.db.get_collection("payroll_period_guards").update_one(
            {"_id": f"{YEAR}-{MONTH:02d}"}, {"$set": {"beat": time.time() - 180}})
        t, box = _in_thread(lambda: rig.lock(without_payroll=True))
        t.join(30)
        seen["lock"] = box["reply"]

    _fail_audit_once(monkeypatch, then=stall_then_lock)
    first = _discard(rig)

    assert seen["lock"].status_code == 200, seen["lock"].text
    assert first.status_code == 500 and "Press Discard again" in first.json()["detail"]
    assert len(rig.lock_rows()) == 1
    assert _row(rig) is None and not rig.stranded()
    assert [o["state"] for o in _ops(rig)] == ["PENDING"] and _audits(rig) == []

    # The retry finishes the log entry, even though the month is locked now.
    again = _discard(rig)
    assert again.status_code == 200, again.text
    assert again.json()["employees"] == [{"employee_id": EMP, "employee_name": "ZZ Native One"}]
    _the_one_audit_says(rig, draft)
    assert [o["state"] for o in _ops(rig)] == ["DONE"]
    assert _discard(rig).status_code == 423  # the locked month refuses anything new
    assert len(_audits(rig)) == 1 and not rig.stranded()


def test_p1_a_lock_finishes_the_log_of_a_discard_nobody_pressed_again(audited, monkeypatch):
    """The page was reloaded after the failed audit write: the draft has gone
    from the list, so nobody presses Discard again. Locking the month writes
    the missing audit row before the books close."""
    rig = audited
    draft = _row(rig)
    _fail_audit_once(monkeypatch)
    assert _discard(rig).status_code == 500

    assert rig.lock(without_payroll=True).status_code == 409  # EMP2 is a draft
    _the_one_audit_says(rig, draft)
    assert [o["state"] for o in _ops(rig)] == ["DONE"]


# ===========================================================================
# P2 -- an uncertain delete is finished, with its trace, by the retry
# ===========================================================================


def test_p2_lost_delete_acknowledgement_is_finished_by_the_identical_retry(audited, monkeypatch):
    rig = audited
    draft = _row(rig)
    fired = _fault_payroll_delete(monkeypatch, rig, land=True)

    first = _discard(rig)
    assert fired and first.status_code == 500
    assert _row(rig) is None and _audits(rig) == []  # the delete really landed

    again = _discard(rig)
    assert again.status_code == 200, again.text
    assert again.json()["discarded"] == 1
    _the_one_audit_says(rig, draft)
    # And a third press says plainly there is nothing left, adding nothing.
    assert _discard(rig).status_code == 404
    assert len(_audits(rig)) == 1


def test_p2_delete_that_never_ran_is_done_by_the_retry_once(audited, monkeypatch):
    rig = audited
    draft = _row(rig)
    _fault_payroll_delete(monkeypatch, rig, land=False)
    assert _discard(rig).status_code == 500
    assert _row(rig) == draft and _audits(rig) == []

    assert _discard(rig).status_code == 200
    assert _row(rig) is None
    _the_one_audit_says(rig, draft)


def test_lost_acknowledgement_of_the_discard_record_is_reused_by_the_retry(audited, monkeypatch):
    """The durable record is written, its answer is lost: nothing was deleted.
    The retry finds that same record (same who, same draft) and finishes."""
    rig = audited
    draft = _row(rig)
    original = rig.coll_cls.insert_one
    fired = []

    def insert_one(self, doc, *a, **k):
        if self.name != "payroll_discards" or fired:
            return original(self, doc, *a, **k)
        fired.append(True)
        original(self, doc, *a, **k)
        raise AutoReconnect("synthetic lost insert acknowledgement")

    monkeypatch.setattr(rig.coll_cls, "insert_one", insert_one)
    assert _discard(rig).status_code == 500
    assert _row(rig) == draft and [o["state"] for o in _ops(rig)] == ["PENDING"]

    assert _discard(rig, user=OTHER_ADMIN).status_code == 200
    assert _row(rig) is None and len(_ops(rig)) == 1
    _the_one_audit_says(rig, draft, user=ADMIN)


def test_lost_audit_acknowledgement_gives_one_audit_row(audited, monkeypatch):
    rig = audited
    draft = _row(rig)
    _fail_audit_once(monkeypatch, land=True)
    # It landed; only the answer was lost. The row is read back by its fixed
    # id, so this request already knows the discard is logged.
    assert _discard(rig).status_code == 200
    _the_one_audit_says(rig, draft)
    assert _discard(rig).status_code == 404
    assert len(_audits(rig)) == 1


def test_the_retry_by_another_admin_keeps_who_asked(audited, monkeypatch):
    rig = audited
    draft = _row(rig)
    _fault_payroll_delete(monkeypatch, rig, land=True)
    assert _discard(rig).status_code == 500
    assert _discard(rig, user=OTHER_ADMIN).status_code == 200
    _the_one_audit_says(rig, draft, user=ADMIN)


def test_two_identical_discards_at_once_write_one_audit_row(audited, monkeypatch):
    """The second request arrives while the first is writing its audit row."""
    rig = audited
    draft = _row(rig)
    second = {}

    def twin():
        t, box = _in_thread(lambda: _discard(rig))
        t.join(30)
        second["r"] = box["reply"]

    real = AuditRepository.create
    fired = []

    def create(self, doc):
        if doc.get("action") == "PAYROLL_DRAFT_DISCARDED" and not fired:
            fired.append(True)
            twin()
        return real(self, doc)

    monkeypatch.setattr(AuditRepository, "create", create)
    first = _discard(rig)

    assert first.status_code == 200, first.text
    assert second["r"].status_code == 200, second["r"].text
    _the_one_audit_says(rig, draft)
    assert [o["state"] for o in _ops(rig)] == ["DONE"]
    # The refused twin write leaves the hash chain intact.
    from database.repositories.audit_chain import verify_chain

    assert verify_chain(rig.db.get_collection("audit_logs"))["intact"] is True


def test_a_rerun_between_the_failed_try_and_the_retry_logs_the_figure_removed(
    audited, monkeypatch
):
    """Retry with changed content: the first try never deleted; a rerun then
    changed the draft (an advance recovery). The retry removes the NEW draft
    and logs ITS figure, once; the old request is cancelled, unlogged."""
    rig = audited
    _fault_payroll_delete(monkeypatch, rig, land=False)
    assert _discard(rig).status_code == 500
    old = _row(rig)
    assert rig.run(advances={EMP: 500}).status_code == 200
    new = _row(rig)
    assert new["payroll_id"] == old["payroll_id"] and new["net_salary"] == old["net_salary"] - 500

    assert _discard(rig).status_code == 200
    assert _row(rig) is None
    _the_one_audit_says(rig, new)


def test_a_draft_approved_after_the_failed_try_is_never_discarded(audited, monkeypatch):
    rig = audited
    _fault_payroll_delete(monkeypatch, rig, land=False)
    assert _discard(rig).status_code == 500
    assert rig.approve().status_code == 200
    approved = _row(rig)

    r = _discard(rig)
    assert r.status_code == 409 and "APPROVED" in r.json()["detail"]
    assert _row(rig) == approved and _audits(rig) == []
    assert [o["state"] for o in _ops(rig)] == ["CANCELLED"]


def test_a_new_draft_run_after_a_lost_delete_is_not_mistaken_for_the_old_one(
    audited, monkeypatch
):
    """The delete landed (answer lost) and payroll was run again, making a NEW
    draft. The retry logs the removed one and leaves the new one alone; a
    second discard then removes the new one with its own audit row."""
    rig = audited
    gone = _row(rig)
    _fault_payroll_delete(monkeypatch, rig, land=True)
    assert _discard(rig).status_code == 500
    assert rig.run().status_code == 200
    fresh = _row(rig)
    assert fresh["payroll_id"] != gone["payroll_id"]

    r = _discard(rig)
    assert r.status_code == 200 and r.json()["discarded"] == 2  # the old log + the new draft
    assert sorted(a["entity_id"] for a in _audits(rig)) == sorted(
        [gone["payroll_id"], fresh["payroll_id"]])
    assert _row(rig) is None


def test_a_shop_discard_failing_halfway_is_finished_by_the_retry(audited, monkeypatch):
    rig = audited
    nets = {r["employee_id"]: r["net_salary"] for r in rig.rows()}
    real = AuditRepository.create
    calls = []

    def create(self, doc):
        if doc.get("action") != "PAYROLL_DRAFT_DISCARDED":
            return real(self, doc)
        calls.append(doc["detail"]["employee_id"])
        return None if len(calls) == 2 else real(self, doc)

    monkeypatch.setattr(AuditRepository, "create", create)
    r = _discard(rig, SHOP)
    assert r.status_code == 500 and "1 draft(s) before it" in r.json()["detail"]
    assert rig.rows() == []  # both removed; the second one's log is pending

    monkeypatch.setattr(AuditRepository, "create", real)
    again = _discard(rig, SHOP)
    assert again.status_code == 200 and again.json()["discarded"] == 1
    logged = {}
    for emp in (EMP, EMP2):
        [a] = _audits(rig, emp)
        logged[emp] = a["detail"]["net_salary"]
    assert logged == nets
    assert _discard(rig, SHOP).status_code == 404
