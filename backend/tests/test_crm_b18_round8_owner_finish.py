"""Batch b18 (CRM), PR #1428 review round 8.

B1  a card whose follow-up was closed by ANOTHER work-list action never sticks
    on the list: Skip / a changed Complete finishes what the saved action asked
    for (its next reminder, once, with its own date) and takes the card off,
    then answers 409 (the saved outcome is kept). Round 7 kept the card for
    every such 409, so a card whose list write had failed came back all day.
    Same for the unlinked reactivation entry.
B2  the audit row of one action has a fixed id taken from the action: never
    lost (a retry after a failed / killed audit write writes it) and never
    doubled (a retry, a double press or two overlapping requests, including a
    card with no follow-up and no next date). Round 7 marked the follow-up
    "audited" BEFORE writing the row, so a failed write lost it for good.

Runs on StrictDB and on a native Mongo when one is reachable.
"""

from __future__ import annotations

import copy
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from test_crm_b18_round6_codex import (  # noqa: F401  (store is a fixture)
    NEXT, STORE, _complete, _entry, _hdr, _inject, _is_next, _react, _rows, crm_mod, store,
)


class _Audit:
    """An audit store with Mongo's one guarantee the fix relies on: ``_id``
    (= log_id) is unique. ``fail`` makes the next N writes fail (a killed or
    failed audit write), the way AuditRepository.create fails soft."""

    def __init__(self):
        self.rows = {}
        self.fail = 0

    def find_by_id(self, log_id):
        return self.rows.get(log_id)

    def create(self, row):
        if self.fail:
            self.fail -= 1
            raise ConnectionError("AutoReconnect: audit write lost")
        log_id = row.setdefault("log_id", f"AUD-{len(self.rows)}")
        if log_id in self.rows:
            return None  # DuplicateKeyError, swallowed like AuditRepository
        self.rows[log_id] = row
        return row


@pytest.fixture
def audit(monkeypatch):
    rec = _Audit()
    monkeypatch.setattr(crm_mod, "get_audit_repository", lambda: rec)
    return rec


def _for(audit, cid):
    return [r for r in audit.rows.values() if r["entity_id"] == cid]


def _dismiss(cid):
    return (f"/api/v1/crm/nba/{STORE}/dismiss", {"customer_id": cid, "reason": "no_answer"})


def _fail_list_write(monkeypatch, db, coll_name):
    """The list write (card / entry dismissed) fails once the outcome is saved."""
    real_get = db.get_collection

    class _Bad:
        def __init__(self, c):
            self._c = c

        def __getattr__(self, name):
            if name == "find_one_and_update":
                def boom(*a, **k):
                    raise ConnectionError("AutoReconnect: list write failed")
                return boom
            return getattr(self._c, name)

    monkeypatch.setattr(db, "get_collection",
                        lambda n: _Bad(real_get(n)) if n == coll_name else real_get(n))
    return lambda: monkeypatch.setattr(db, "get_collection", real_get)


# ---------------------------------------------------------------------------
# B1  the card never sticks behind another action's outcome
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("second", ["skip", "changed"])
def test_list_write_failed_then_other_press_takes_the_card_off(store, audit, monkeypatch, second):
    client, db, c1, _c2 = store
    url = f"/api/v1/crm/nba/{STORE}/complete"
    body = {"customer_id": c1, "outcome_notes": "Notes A, will visit."}  # no next date
    undo = _fail_list_write(monkeypatch, db, "nba_scores")
    r = client.post(url, headers=_hdr(), json=body)
    undo()
    assert r.status_code == 503 and "Reload the list" in r.json()["detail"], r.text
    assert not _entry(db, "nba_scores", "cards", c1).get("dismissed")
    # staff reload (the card is there) and press Skip / Done with other notes
    if second == "skip":
        surl, sbody = _dismiss(c1)
    else:
        surl, sbody = url, {**body, "outcome_notes": "Other notes entirely."}
    assert client.post(surl, headers=_hdr(), json=sbody).status_code == 409
    assert _entry(db, "nba_scores", "cards", c1)["dismissed"] is True  # off the list
    [row] = _rows(db, c1)
    assert row["status"] == "completed"  # the saved outcome is kept
    assert [a["notes"] for a in row["attempts"]] == ["Notes A, will visit."]


@pytest.mark.parametrize("door", ["complete", "reactivation", "reactivation-unlinked"])
def test_reminder_not_set_then_other_press_sets_the_saved_reminder_and_clears(
        store, audit, monkeypatch, door):
    """D69: the saved call asked for a next reminder that was not written.
    Skip / a changed entry (even after a reload, when the screen no longer
    holds the original entry) sets THAT reminder once, with ITS date, and only
    then takes the card off; the changed entry itself is refused (409)."""
    client, db, c1, c2 = store
    if door == "complete":
        cid, (url, body) = c1, _complete(c1)
        coll, arr = "nba_scores", "cards"
        other = _dismiss(c1)
    else:
        cid = c1 if door == "reactivation" else c2
        url, body = _react(cid)
        coll, arr = "reactivation_cohorts", "entries"
        other = _react(cid, outcome="wrong_number", notes="CHANGED after reload",
                       follow_up_scheduled_date=None)
    undo = _inject(monkeypatch, db, "insert_one", _is_next, "fail")
    r = client.post(url, headers=_hdr(), json=body)
    undo()
    assert r.status_code == 503 and r.json()["detail"].startswith("Call saved."), r.text
    assert client.post(other[0], headers=_hdr(), json=other[1]).status_code == 409
    assert _entry(db, coll, arr, cid)["dismissed"] is True
    pending = [x for x in _rows(db, cid) if x["status"] == "pending"]
    assert [p["scheduled_date"] for p in pending] == [NEXT], _rows(db, cid)
    # the original Retry (screen still open) is answered and repeats nothing
    again = client.post(url, headers=_hdr(), json=body)
    assert again.status_code == 200, again.text
    assert len([x for x in _rows(db, cid) if x["status"] == "pending"]) == 1


# ---------------------------------------------------------------------------
# B2  one audit row per action: never lost, never doubled
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("door", ["complete", "dismiss", "reactivation",
                                  "reactivation-unlinked", "complete-unlinked"])
def test_failed_audit_write_is_written_by_the_retry_once(store, audit, door):
    client, _db, c1, c2 = store
    url, body = {
        "complete": lambda: _complete(c1),
        "dismiss": lambda: _dismiss(c1),
        "reactivation": lambda: _react(c1),
        "reactivation-unlinked": lambda: _react(c2),
        "complete-unlinked": lambda: _complete(c2),
    }[door]()
    cid = body["customer_id"]
    audit.fail = 1  # the audit write is lost (worker killed / Mongo blip)
    assert client.post(url, headers=_hdr(), json=body).status_code == 200
    assert _for(audit, cid) == []
    for _ in range(2):  # Retry, then a double press
        assert client.post(url, headers=_hdr(), json=body).status_code == 200
    assert len(_for(audit, cid)) == 1, _for(audit, cid)


def test_overlapping_skips_of_a_card_with_no_follow_up_write_one_audit_row(
        store, audit, monkeypatch):
    client, db, _c1, c2 = store
    url, body = _dismiss(c2)  # no follow-up, no next date: no record to key on
    barrier = threading.Barrier(2)
    overlapped = []
    real_get = db.get_collection

    class _Hold:
        def __init__(self, c):
            self._c = c

        def __getattr__(self, name):
            return getattr(self._c, name)

        def find_one(self, q=None, *a, **k):
            out = copy.deepcopy(self._c.find_one(q, *a, **k))  # a read, as Mongo returns it
            if q and q.get("cards.customer_id") == c2:
                barrier.wait(timeout=10)  # both requests read the open card
                overlapped.append(1)
            return out

    monkeypatch.setattr(db, "get_collection",
                        lambda n: _Hold(real_get(n)) if n == "nba_scores" else real_get(n))
    with ThreadPoolExecutor(max_workers=2) as pool:
        answers = list(pool.map(lambda _: client.post(url, headers=_hdr(), json=body), [1, 2]))
    monkeypatch.setattr(db, "get_collection", real_get)
    assert len(overlapped) == 2  # the two requests really overlapped
    assert [a.status_code for a in answers] == [200, 200], [a.text for a in answers]
    assert len(_for(audit, c2)) == 1, _for(audit, c2)
