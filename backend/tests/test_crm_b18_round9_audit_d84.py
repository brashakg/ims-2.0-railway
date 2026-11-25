"""Batch b18 (CRM), PR #1428: lead's corrections after Claude's review (D84, D87).

C1  A saved work-list action whose own request ended in 503 before its audit
    row was written (next reminder or audit write failed), then finished from
    ANOTHER press (Skip, a changed entry; the 409 paths) gets its OWN audit row
    written too: fixed id AUD-CRM-<its action_key>, once, naming its own user.
C2  D84: the audit row is written (and read back) BEFORE success is answered;
    a failed or unconfirmed audit write, or a missing audit repository, is a
    503 with the card still on the list, and the identical retry writes it once.
C3  D87: the 409 after a Skip or changed entry on a saved call says
    "This call was already saved. The saved outcome is kept and its next
    reminder is set. Reload the list."

Runs on StrictDB and on a native Mongo when one is reachable (the `store`
fixture of round 6); on native Mongo the audit rows go through the REAL
AuditRepository (hash chain, _id = log_id) of that database.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import jwt
import pytest

from test_crm_b18_round6_codex import (  # noqa: F401  (store is a fixture)
    NEXT, STORE, _complete, _entry, _hdr, _inject, _is_next, _NativeDB, _react, _rows,
    auth_mod, crm_mod, store,
)
from test_crm_b18_round8_owner_finish import _Audit, _dismiss

D87 = ("This call was already saved. The saved outcome is kept and its next "
       "reminder is set. Reload the list.")


def _hdr2():
    """A second staff member of the same shop (the other press)."""
    tok = jwt.encode({"sub": "u2", "user_id": "u2", "username": "t2", "roles": ["STORE_MANAGER"],
                      "store_ids": [STORE], "active_store_id": STORE,
                      "exp": datetime.utcnow() + timedelta(hours=1)},
                     auth_mod.SECRET_KEY, algorithm=auth_mod.ALGORITHM)
    return {"Authorization": f"Bearer {tok}"}


class _Repo:
    """The audit store the route sees: the real AuditRepository on native
    Mongo, the _id-unique fake on StrictDB; ``fail`` loses the next N writes
    the way AuditRepository.create fails soft (returns None)."""

    def __init__(self, inner):
        self.inner, self.fail = inner, 0

    def find_by_id(self, log_id):
        return self.inner.find_by_id(log_id)

    def create(self, row):
        if self.fail:
            self.fail -= 1
            return None
        return self.inner.create(row)


@pytest.fixture
def audit(store, monkeypatch):
    _client, db, _c1, _c2 = store
    if isinstance(db, _NativeDB):
        from database.repositories.audit_repository import AuditRepository

        coll = db._db.get_collection("audit_logs")
        inner = AuditRepository(coll)
        rows = lambda cid: list(coll.find({"entity_id": cid, "source": "crm.nba"}))  # noqa: E731
    else:
        inner = _Audit()
        rows = lambda cid: [r for r in inner.rows.values() if r["entity_id"] == cid]  # noqa: E731
    repo = _Repo(inner)
    repo.rows = rows
    monkeypatch.setattr(crm_mod, "get_audit_repository", lambda: repo)
    return repo


def _door(door, c1, c2):
    return {
        "complete": (c1, _complete(c1), "nba_scores", "cards", "nba.completed"),
        "reactivation": (c1, _react(c1), "reactivation_cohorts", "entries",
                         "reactivation.logged"),
        "reactivation-unlinked": (c2, _react(c2), "reactivation_cohorts", "entries",
                                  "reactivation.logged"),
        "complete-unlinked": (c2, _complete(c2), "nba_scores", "cards", "nba.completed"),
    }[door]


def _other(door, cid, second):
    if second == "skip":
        if door.startswith("reactivation"):
            return _react(cid, outcome="no_answer", notes="", follow_up_scheduled_date=None)
        return _dismiss(cid)
    no_next = {"follow_up_scheduled_date": None} if second == "changed-no-next" else {}
    if door.startswith("reactivation"):
        return _react(cid, outcome="wrong_number", notes="CHANGED after reload", **no_next)
    url, body = _complete(cid)
    return url, {**body, "outcome_notes": "Other notes entirely, changed.", **no_next}


# ---------------------------------------------------------------------------
# C1  the saved action's own audit row, written by the other press
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("second", ["skip", "changed", "changed-no-next"])
@pytest.mark.parametrize("fault", ["next", "audit"])
@pytest.mark.parametrize("door", ["complete", "reactivation", "reactivation-unlinked",
                                  "complete-unlinked"])
def test_other_press_writes_the_saved_actions_own_audit_row(
        store, audit, monkeypatch, door, fault, second):
    client, db, c1, c2 = store
    cid, (url, body), coll, arr, action = _door(door, c1, c2)
    if door == "complete-unlinked" and fault == "next":
        pytest.skip("an unlinked card whose reminder failed saved nothing: no row is owed")
    if fault == "next":
        undo = _inject(monkeypatch, db, "insert_one", _is_next, "fail")
    else:
        audit.fail = 1
    first = client.post(url, headers=_hdr(), json=body)
    if fault == "next":
        undo()
    assert first.status_code == 503 and first.json()["detail"].startswith("Call saved."), \
        first.text
    assert audit.rows(cid) == []  # the saved action's own row is still owed
    assert not _entry(db, coll, arr, cid).get("dismissed")  # the card stays

    o_url, o_body = _other(door, cid, second)
    r = client.post(o_url, headers=_hdr2(), json=o_body)
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == D87
    assert _entry(db, coll, arr, cid)["dismissed"] is True
    [row] = audit.rows(cid)  # the saved action's own row, once; none for the refused press
    assert row["action"] == action and row["user_id"] == "u1", row
    assert row["log_id"].startswith("AUD-CRM-")
    pending = [x for x in _rows(db, cid) if x["status"] == "pending"]
    assert [p["scheduled_date"] for p in pending] == [NEXT]
    # a further Skip / changed press and the original Retry repeat nothing
    client.post(o_url, headers=_hdr2(), json=o_body)
    client.post(url, headers=_hdr(), json=body)
    assert len(audit.rows(cid)) == 1


# ---------------------------------------------------------------------------
# C2  D84: written before success, or a 503 whose identical retry writes it
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("door", ["complete", "dismiss", "reactivation",
                                  "reactivation-unlinked", "complete-unlinked",
                                  "dismiss-unlinked"])
def test_failed_audit_write_is_a_503_and_the_retry_writes_it_once(store, audit, door):
    client, db, c1, c2 = store
    url, body = {
        "complete": lambda: _complete(c1),
        "dismiss": lambda: _dismiss(c1),
        "reactivation": lambda: _react(c1),
        "reactivation-unlinked": lambda: _react(c2),
        "complete-unlinked": lambda: _complete(c2),
        "dismiss-unlinked": lambda: _dismiss(c2),
    }[door]()
    cid = body["customer_id"]
    coll, arr = (("reactivation_cohorts", "entries") if door.startswith("reactivation")
                 else ("nba_scores", "cards"))
    audit.fail = 1  # the audit insert does not land (create answers None)
    r = client.post(url, headers=_hdr(), json=body)
    assert r.status_code == 503, r.text
    assert "audit" in r.json()["detail"] and ("Retry" in r.json()["detail"] or "Try again" in r.json()["detail"])
    assert audit.rows(cid) == []
    assert not _entry(db, coll, arr, cid).get("dismissed")  # the card stays for the Retry
    for _ in range(2):  # Retry, then a double press
        again = client.post(url, headers=_hdr(), json=body)
        assert again.status_code == 200, again.text
    assert len(audit.rows(cid)) == 1
    assert _entry(db, coll, arr, cid)["dismissed"] is True


def test_missing_audit_repository_is_a_503_not_a_silent_success(store, monkeypatch):
    client, db, c1, _c2 = store
    monkeypatch.setattr(crm_mod, "get_audit_repository", lambda: None)
    url, body = _complete(c1)
    r = client.post(url, headers=_hdr(), json=body)
    assert r.status_code == 503 and "audit" in r.json()["detail"], r.text
    assert not _entry(db, "nba_scores", "cards", c1).get("dismissed")


def test_audit_read_back_failure_is_a_503(store, audit, monkeypatch):
    """create() answered but the row cannot be confirmed: not a success."""
    client, db, c1, _c2 = store
    real_find = audit.find_by_id
    calls = []

    def _find(log_id):
        calls.append(log_id)
        return None if len(calls) <= 2 else real_find(log_id)
    monkeypatch.setattr(audit, "find_by_id", _find)
    audit.fail = 1
    url, body = _dismiss(c1)
    assert client.post(url, headers=_hdr(), json=body).status_code == 503
    assert client.post(url, headers=_hdr(), json=body).status_code == 200
    assert len(audit.rows(c1)) == 1
