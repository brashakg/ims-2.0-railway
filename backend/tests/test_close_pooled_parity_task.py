"""
scripts/close_pooled_parity_task.py -- closes the stuck July ESCALATED pooled
stock-parity task after multi-location PR 4 merges.

Pins (each red when its rule is removed):
  * a dry run writes NOTHING (revert `if not commit: return` -> closed -> red);
  * --commit closes ONLY active rows whose source_ref EQUALS the pooled ref --
    never a per-shop ":<store_id>" ref, never another ref, never a row that is
    already closed (revert the filter to a prefix/regex match -> the per-shop
    task is closed -> red);
  * it refuses any collection but `tasks`, and any matched row whose ref is
    not exactly the pooled one, with SystemExit -- never a bare `assert`,
    which `python -O` strips (put either `assert` back -> AssertionError is
    not SystemExit -> red).

StrictCollection only -- no network, no production.
"""

import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(_HERE)), "scripts"))

from strict_fakes import StrictCollection  # noqa: E402
import close_pooled_parity_task as script  # noqa: E402


def _tasks():
    return StrictCollection(
        "tasks",
        [
            {"task_id": "T-JULY", "source_ref": "shopify-stock-parity-drift", "status": "ESCALATED", "title": "pooled"},
            {"task_id": "T-OLD", "source_ref": "shopify-stock-parity-drift", "status": "COMPLETED", "title": "done"},
            {"task_id": "T-SHOP", "source_ref": "shopify-stock-parity-drift:BV-A", "status": "OPEN", "title": "shop"},
            {"task_id": "T-OTHER", "source_ref": "shopify-store-unmapped:BV-PUN", "status": "OPEN", "title": "map"},
        ],
    )


def _status(coll):
    return {d["task_id"]: d["status"] for d in coll.docs}


def test_dry_run_writes_nothing():
    coll = _tasks()
    before = [dict(d) for d in coll.docs]
    rows = script.close_pooled(coll, commit=False)
    assert [r["task_id"] for r in rows] == ["T-JULY"]
    assert coll.docs == before


def test_commit_closes_only_the_exact_pooled_ref():
    coll = _tasks()
    script.close_pooled(coll, commit=True)
    assert _status(coll) == {
        "T-JULY": "COMPLETED",
        "T-OLD": "COMPLETED",
        "T-SHOP": "OPEN",
        "T-OTHER": "OPEN",
    }
    july = next(d for d in coll.docs if d["task_id"] == "T-JULY")
    assert july["completion_notes"] == script.NOTES
    assert july["history"][-1]["status"] == "COMPLETED"
    # a re-run finds nothing left to close
    assert script.close_pooled(coll, commit=True) == []


def test_refuses_any_collection_but_tasks():
    other = StrictCollection("stock_units", [{"source_ref": "shopify-stock-parity-drift", "status": "OPEN"}])
    with pytest.raises(SystemExit, match="refusing"):
        script.close_pooled(other, commit=True)
    assert other.docs[0]["status"] == "OPEN"


def test_refuses_a_matched_row_that_is_not_the_exact_pooled_ref():
    """Belt and braces behind the exact-ref filter: a `tasks` collection that
    answers the find with the pooled row AND a per-shop row (a filter that
    drifted to a prefix) writes NOTHING -- not even the pooled row first --
    and exits."""
    coll = _tasks()
    rows = [dict(d) for d in coll.docs if d["task_id"] in ("T-JULY", "T-SHOP")]
    coll.find = lambda *_a, **_k: [dict(r) for r in rows]
    with pytest.raises(SystemExit, match="refusing"):
        script.close_pooled(coll, commit=True)
    assert _status(coll)["T-JULY"] == "ESCALATED" and _status(coll)["T-SHOP"] == "OPEN"


def test_no_connection_is_a_clean_exit(monkeypatch):
    for k in ("MONGO_PUBLIC_URL", "MONGODB_URI", "MONGODB_URL", "MONGO_URL"):
        monkeypatch.delenv(k, raising=False)
    assert script.main([]) == 2


def test_the_command_is_a_dry_run_unless_commit(monkeypatch):
    """The runbook runs the COMMAND (`railway run ... close_pooled_parity_task.py`
    with no flag), not the helper: main() must pass commit only on --commit.
    Hard-wire `close_pooled(..., commit=True)` in main() -> the flagless run
    closes T-JULY -> fails."""
    import pymongo

    coll = _tasks()

    class _Client:
        def __init__(self, *_a, **_k):
            pass

        def __getitem__(self, _db):
            return {"tasks": coll}

    monkeypatch.setattr(pymongo, "MongoClient", _Client)
    before = [dict(d) for d in coll.docs]
    assert script.main(["--mongo-uri", "mongodb://fake"]) == 0
    assert coll.docs == before
    assert script.main(["--mongo-uri", "mongodb://fake", "--commit"]) == 0
    assert _status(coll)["T-JULY"] == "COMPLETED"
