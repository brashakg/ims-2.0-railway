"""Batch b18 (CRM), PR #1428 review round 7.

A1  one work-list action writes ONE audit row: an identical retry after a lost
    answer, the same request pressed twice and two overlapping identical
    requests are recognised for the follow-up and reminder writes, and the
    audit row ('nba.completed' / 'nba.dismissed' / 'reactivation.logged')
    follows the same rule (it used to be written once per request).
A2  D69: after "Call saved. The next reminder was NOT set.", pressing Skip
    (or a changed Complete) on the same card is refused AND the card stays on
    the list, so the original Retry can still set the reminder. It used to
    take the card off the list with the reminder never written.

Runs on StrictDB and on a native Mongo when one is reachable (see
test_crm_b18_round6_codex._native_db).
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from test_crm_b18_round6_codex import (  # noqa: F401  (store is a fixture)
    STORE, _complete, _entry, _hdr, _inject, _is_next, _react, _rows, crm_mod, store,
)


class _Recorder:
    def __init__(self):
        self.rows = []

    def create(self, row):
        self.rows.append(row)
        return row


@pytest.fixture
def audit(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(crm_mod, "get_audit_repository", lambda: rec)
    return rec


def _dismiss(cid):
    return (f"/api/v1/crm/nba/{STORE}/dismiss", {"customer_id": cid, "reason": "no_answer"})


DOORS = {
    "complete": lambda c1, c2: _complete(c1),
    "dismiss": lambda c1, c2: _dismiss(c1),
    "reactivation": lambda c1, c2: _react(c1),
    "reactivation-unlinked": lambda c1, c2: _react(c2),
    "complete-unlinked": lambda c1, c2: _complete(c2),
}


def _for(audit, cid):
    return [r for r in audit.rows if r["entity_id"] == cid]


@pytest.mark.parametrize("door", list(DOORS))
def test_identical_retry_writes_no_second_audit_row(store, audit, door):
    client, _db, c1, c2 = store
    url, body = DOORS[door](c1, c2)
    cid = body["customer_id"]
    first = client.post(url, headers=_hdr(), json=body)
    again = client.post(url, headers=_hdr(), json=body)  # lost answer / double press
    assert first.status_code == again.status_code == 200, (first.text, again.text)
    assert len(_for(audit, cid)) == 1, _for(audit, cid)


@pytest.mark.parametrize("door", ["complete", "reactivation"])
def test_failed_reminder_then_retry_writes_exactly_one_audit_row(store, audit, monkeypatch, door):
    client, db, c1, c2 = store
    url, body = DOORS[door](c1, c2)
    undo = _inject(monkeypatch, db, "insert_one", _is_next, "fail")
    assert client.post(url, headers=_hdr(), json=body).status_code == 503
    undo()
    assert _for(audit, c1) == []
    for _ in range(2):
        assert client.post(url, headers=_hdr(), json=body).status_code == 200
    rows = _for(audit, c1)
    assert len(rows) == 1 and rows[0]["after_state"]["next_follow_up_id"], rows


@pytest.mark.parametrize("door", ["complete", "dismiss", "reactivation"])
def test_two_overlapping_identical_requests_write_one_audit_row(store, audit, monkeypatch, door):
    client, db, c1, c2 = store
    url, body = DOORS[door](c1, c2)
    coll = "reactivation_cohorts" if door == "reactivation" else "nba_scores"
    key = "entries.customer_id" if door == "reactivation" else "cards.customer_id"
    barrier = threading.Barrier(2)
    real_get = db.get_collection

    class _Hold:
        def __init__(self, c):
            self._c = c

        def __getattr__(self, name):
            return getattr(self._c, name)

        def find_one(self, q=None, *a, **k):
            out = self._c.find_one(q, *a, **k)
            if q and q.get(key) == c1:
                barrier.wait(timeout=10)  # both have read the open card
            return out

    monkeypatch.setattr(db, "get_collection",
                        lambda n: _Hold(real_get(n)) if n == coll else real_get(n))
    with ThreadPoolExecutor(max_workers=2) as pool:
        answers = list(pool.map(lambda _: client.post(url, headers=_hdr(), json=body), [1, 2]))
    monkeypatch.setattr(db, "get_collection", real_get)
    assert [a.status_code for a in answers] == [200, 200], [a.text for a in answers]
    assert len(_for(audit, c1)) == 1, _for(audit, c1)


def test_skip_after_reminder_not_set_keeps_the_card_for_the_retry(store, audit, monkeypatch):
    client, db, c1, _c2 = store
    url, body = _complete(c1)
    undo = _inject(monkeypatch, db, "insert_one", _is_next, "fail")
    r = client.post(url, headers=_hdr(), json=body)
    undo()
    assert r.json()["detail"].startswith("Call saved. The next reminder was NOT set.")
    # staff cancel and press Skip, then a Complete with other notes
    durl, dbody = _dismiss(c1)
    assert client.post(durl, headers=_hdr(), json=dbody).status_code == 409
    changed = {**body, "outcome_notes": "Something else."}
    assert client.post(url, headers=_hdr(), json=changed).status_code == 409
    assert not _entry(db, "nba_scores", "cards", c1).get("dismissed")  # never shown done
    # the original Retry still sets the reminder, once
    r = client.post(url, headers=_hdr(), json=body)
    assert r.status_code == 200 and r.json()["next_follow_up_id"], r.text
    assert sorted(x["status"] for x in _rows(db, c1)) == ["completed", "pending"]
    assert _entry(db, "nba_scores", "cards", c1)["dismissed"] is True
    assert [a["action"] for a in _for(audit, c1)] == ["nba.completed"]
