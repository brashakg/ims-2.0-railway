"""Batch b18 (CRM), Codex PR-1428 round-3 report corrections.

R3-A  An identical retry pressed by ANOTHER staff member after the saved
      call's audit write failed (503) writes the saved action's audit row
      under the person who SAVED it (the record's ``saved_audit``), never the
      retrier. A failed read of that saved identity is a 503, never a guess.
R3-B  Two overlapping identical requests that both reach the fixed-id audit
      insert leave the audit hash chain intact (GET /audit/verify): checked on
      a native Mongo with the REAL AuditRepository / audit_chain, including
      the next ordinary audit row written after them.

StrictDB and native Mongo (the round-6 ``store`` fixture); R3-B is native only.
"""

from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from test_crm_b18_round6_codex import (  # noqa: F401  (store is a fixture)
    _client, _complete, _entry, _hdr, _inject, _native_db, _NativeDB, _react, _seed,
    crm_mod, store,
)
from test_crm_b18_round9_audit_d84 import _door, _hdr2, audit  # noqa: F401


# ---------------------------------------------------------------------------
# R3-A  identical retry by another person keeps the saved actor
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("door", ["complete", "reactivation", "reactivation-unlinked",
                                  "complete-unlinked"])
def test_identical_retry_by_other_staff_keeps_the_saved_actor(store, audit, door):
    client, db, c1, c2 = store
    cid, (url, body), coll, arr, action = _door(door, c1, c2)
    audit.fail = 1  # the saved call's audit write does not land
    first = client.post(url, headers=_hdr(), json=body)
    assert first.status_code == 503 and first.json()["detail"].startswith("Call saved."), \
        first.text
    assert audit.rows(cid) == []
    again = client.post(url, headers=_hdr2(), json=body)  # SAME body, other person
    assert again.status_code == 200, again.text
    [row] = audit.rows(cid)
    assert row["action"] == action
    assert row["user_id"] == "u1", row  # the person who saved the call, not u2
    assert _entry(db, coll, arr, cid)["dismissed"] is True


def test_unreadable_saved_actor_is_a_503_never_the_retrier(store, audit, monkeypatch):
    client, db, c1, _c2 = store
    url, body = _complete(c1)
    audit.fail = 1
    assert client.post(url, headers=_hdr(), json=body).status_code == 503
    undo = _inject(monkeypatch, db, "find_one",
                   lambda q, *_: isinstance(q, dict) and "saved_audit" in q, "fail")
    r = client.post(url, headers=_hdr2(), json=body)
    undo()
    assert r.status_code == 503 and "audit" in r.json()["detail"], r.text
    assert audit.rows(c1) == []  # nothing written under a guessed person
    assert client.post(url, headers=_hdr2(), json=body).status_code == 200
    [row] = audit.rows(c1)
    assert row["user_id"] == "u1"


# ---------------------------------------------------------------------------
# R3-B  overlapping fixed-id audit inserts keep the hash chain whole (native)
# ---------------------------------------------------------------------------


class _HoldFirstInsert:
    """audit_logs whose first CRM insert waits until a second CRM insert has
    arrived (or 5 s): both overlapping requests reach the fixed-id insert."""

    def __init__(self, coll, cid):
        self._c, self._cid = coll, cid
        self._arrived = threading.Semaphore(0)
        self._count = 0
        self._lock = threading.Lock()

    def __getattr__(self, name):
        return getattr(self._c, name)

    def insert_one(self, doc, *a, **k):
        if doc.get("entity_id") == self._cid:
            with self._lock:
                self._count += 1
                n = self._count
            if n == 1:
                self._arrived.acquire(timeout=5)
            else:
                self._arrived.release()
                threading.Event().wait(0.05)  # the first goes in first
        return self._c.insert_one(doc, *a, **k)


def test_overlapping_identical_requests_keep_the_audit_chain_intact(monkeypatch):
    raw = _native_db()
    if raw is None:
        pytest.skip("no native Mongo reachable")
    name = f"{raw.name}_chain_{uuid.uuid4().hex[:6]}"
    fresh = raw.client[name]
    assert fresh.name == name and name.startswith(("ims_claude_", "ims_test_"))
    try:
        from database.repositories.audit_chain import verify_chain
        from database.repositories.audit_repository import AuditRepository

        db = _NativeDB(fresh)
        _c1, c2 = _seed(db)
        client = _client(monkeypatch, db)
        audit_coll = fresh.get_collection("audit_logs")
        audit_coll.create_index("seq", unique=True, sparse=True)
        repo = AuditRepository(audit_coll)
        repo.create({"action": "seed", "entity_type": "test", "entity_id": "SEED"})
        assert verify_chain(audit_coll)["intact"] is True

        repo.collection = _HoldFirstInsert(audit_coll, c2)
        monkeypatch.setattr(crm_mod, "get_audit_repository", lambda: repo)
        url, body = _react(c2)
        with ThreadPoolExecutor(2) as pool:
            answers = list(pool.map(lambda _: client.post(url, headers=_hdr(), json=body),
                                    [1, 2]))
        assert [a.status_code for a in answers] == [200, 200], [a.text for a in answers]
        assert len(list(audit_coll.find({"entity_id": c2}))) == 1
        repo.collection = audit_coll
        repo.create({"action": "next ordinary", "entity_type": "test", "entity_id": "NEXT"})
        verdict = verify_chain(audit_coll)
        assert verdict["intact"] is True, verdict
        assert verdict["entries_checked"] == audit_coll.count_documents({}), verdict
    finally:
        raw.client.drop_database(name)
