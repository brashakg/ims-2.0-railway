"""
scripts/close_pooled_parity_task.py -- closes the stuck July ESCALATED pooled
stock-parity task after multi-location PR 4 merges.

Pins (each red when its rule is removed):
  * a dry run writes NOTHING (revert `if not commit: return` -> closed -> red);
  * --commit closes ONLY active rows whose source_ref EQUALS the pooled ref --
    never a per-shop ":<store_id>" ref, never another ref, never a row that is
    already closed (revert the filter to a prefix/regex match -> the per-shop
    task is closed -> red);
  * it refuses any collection but `tasks` (drop the assert -> red).

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
    with pytest.raises(AssertionError, match="refusing"):
        script.close_pooled(other, commit=True)
    assert other.docs[0]["status"] == "OPEN"


def test_no_connection_is_a_clean_exit(monkeypatch):
    for k in ("MONGO_PUBLIC_URL", "MONGODB_URI", "MONGODB_URL", "MONGO_URL"):
        monkeypatch.delenv(k, raising=False)
    assert script.main([]) == 2
