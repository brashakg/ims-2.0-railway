"""
IMS 2.0 - Stranded transfer units: finder + repair + SENTINEL guard
==================================================================
Data-repair follow-up to PR #1023. #1023 stopped NEW units being stranded (the
receive path picks what to re-home by identity, not by a positional slice); it
does not repair the units already stranded in production. A stranded unit is a
`stock_units` row still at TRANSFERRED whose transfer is already closed
(received / completed / cancelled) or missing -- on-hand at NEITHER store, and
unreachable by every code path (receive is gated to in_transit /
partially_received, cancel is refused at received, complete moves no stock).

The three things that would hurt if they broke:

  * A unit on an OPEN transfer must never be reported or repaired. Those are
    legitimately in transit and #1023 re-homes them on the next receive.
    Repairing one would yank a frame out of a live shipment.
  * A repair must not touch a unit that moved between the report and the write
    (the update is guarded on status+transfer_id).
  * `destination` must be refused when there is no destination to re-home to
    (a missing/cancelled transfer) rather than writing store_id = "".

No Mongo: a tiny in-memory stand-in for the two collections the finder reads
and the three the repair writes.
"""

from __future__ import annotations

import importlib.util
import os
import sys

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_SCRIPT = os.path.join(_REPO_ROOT, "scripts", "stranded_transfer_units.py")

_spec = importlib.util.spec_from_file_location("stranded_transfer_units", _SCRIPT)
stu = importlib.util.module_from_spec(_spec)
sys.modules["stranded_transfer_units"] = stu
_spec.loader.exec_module(stu)


# ---------------------------------------------------------------------------
# In-memory stand-ins (only the operations the script actually uses)
# ---------------------------------------------------------------------------


class FakeColl:
    def __init__(self, docs=None):
        self.docs = [dict(d) for d in (docs or [])]

    def _match(self, doc, query):
        for key, want in query.items():
            got = doc.get(key)
            if isinstance(want, dict) and "$in" in want:
                if got not in want["$in"]:
                    return False
            elif got != want:
                return False
        return True

    def find(self, query, projection=None):
        return [dict(d) for d in self.docs if self._match(d, query)]

    def find_one(self, query, projection=None):
        for d in self.docs:
            if self._match(d, query):
                return dict(d)
        return None

    def insert_one(self, doc):
        self.docs.append(dict(doc))

    def update_one(self, query, update):
        for d in self.docs:
            if self._match(d, query):
                d.update(update["$set"])
                return type("R", (), {"matched_count": 1})()
        return type("R", (), {"matched_count": 0})()

    def count_documents(self, query):
        return len(self.find(query))

    def distinct(self, field, query):
        return list({d.get(field) for d in self.docs if self._match(d, query)})


class FakeDB(dict):
    def __missing__(self, name):
        self[name] = FakeColl()
        return self[name]

    # SENTINEL reads collections through get_collection.
    def get_collection(self, name):
        return self[name]


def _unit(sid, *, transfer_id, store="ST_SRC", dest="ST_DST", status="TRANSFERRED"):
    return {
        "stock_id": sid,
        "barcode": f"BC-{sid}",
        "product_id": "P1",
        "store_id": store,
        "status": status,
        "transfer_id": transfer_id,
        "transfer_to_store_id": dest,
    }


def _transfer(tid, status, *, received_ids=()):
    return {
        "id": tid,
        "transfer_number": f"TRF-{tid}",
        "status": status,
        "from_location_id": "ST_SRC",
        "to_location_id": "ST_DST",
        "items": [{
            "product_id": "P1",
            "quantity_shipped": 2,
            "quantity_received": 2,
            "received_stock_ids": list(received_ids),
        }],
    }


@pytest.fixture()
def db():
    d = FakeDB()
    d["stock_units"] = FakeColl([
        # Stranded: the transfer closed as RECEIVED but this frame was stepped
        # over by the old positional slice (only u1 is in received_stock_ids).
        _unit("u2", transfer_id="T_CLOSED"),
        # Healthy: still in transit -- #1023 re-homes it on the next receive.
        _unit("u3", transfer_id="T_OPEN"),
        # Stranded with nowhere to go: the transfer doc does not exist.
        _unit("u4", transfer_id="T_GONE", dest=""),
        # Already re-homed by a real receive; must never be reported.
        _unit("u1", transfer_id=None, status="AVAILABLE", store="ST_DST"),
    ])
    d["stock_transfers"] = FakeColl([
        _transfer("T_CLOSED", "received", received_ids=["u1"]),
        _transfer("T_OPEN", "in_transit"),
    ])
    d["products"] = FakeColl([{"product_id": "P1", "sku": "SKU1",
                               "name": "Ray-Ban RB3025", "cost_price": 1500}])
    d["stores"] = FakeColl([
        {"store_id": "ST_SRC", "store_name": "Ranchi Main"},
        {"store_id": "ST_DST", "store_name": "Pune FC Road"},
    ])
    return d


# ---------------------------------------------------------------------------
# Finder
# ---------------------------------------------------------------------------


def test_finds_only_the_stranded_units(db):
    rows = stu.find_stranded(db, stores=[], transfers_filter=[])
    assert {r["stock_id"] for r in rows} == {"u2", "u4"}, (
        "a unit on an OPEN transfer (u3) is in transit, not stranded; an "
        "AVAILABLE unit (u1) already landed"
    )
    by_id = {r["stock_id"]: r for r in rows}
    assert by_id["u2"]["reason"] == "CLOSED_RECEIVED"
    assert by_id["u4"]["reason"] == "MISSING_TRANSFER"
    # The discrepancy has to be legible: the line declared 2 received but names
    # only 1 re-homed id -- that gap IS this unit.
    assert by_id["u2"]["line_quantity_received"] == 2
    assert by_id["u2"]["line_received_ids"] == 1
    assert by_id["u2"]["product"] == "Ray-Ban RB3025"
    assert by_id["u2"]["source_store"] == "Ranchi Main"
    assert by_id["u2"]["dest_store"] == "Pune FC Road"
    assert by_id["u2"]["value"] == 1500


def test_unit_transferred_against_nothing_is_stranded():
    assert stu.classify({"transfer_id": None}, None) == "NO_TRANSFER_ID"
    assert stu.classify({"transfer_id": "T"}, None) == "MISSING_TRANSFER"
    assert stu.classify({"transfer_id": "T"}, {"status": "COMPLETED"}) == "CLOSED_COMPLETED"
    # Open -> not stranded, whatever the case the writer stamped.
    assert stu.classify({"transfer_id": "T"}, {"status": "In_Transit"}) is None


def test_store_filter_narrows_to_one_source(db):
    assert stu.find_stranded(db, stores=["ST_OTHER"], transfers_filter=[]) == []
    assert len(stu.find_stranded(db, stores=["ST_SRC"], transfers_filter=[])) == 2


# ---------------------------------------------------------------------------
# Repair
# ---------------------------------------------------------------------------


def test_dry_run_writes_nothing(db):
    rows = stu.find_stranded(db, stores=[], transfers_filter=[])
    stu.repair(db, rows, disposition="source", apply=False, actor="t")
    assert db["stock_units"].find_one({"stock_id": "u2"})["status"] == "TRANSFERRED"
    assert db["stock_audit"].count_documents({}) == 0


@pytest.mark.parametrize(
    "disposition,expect_status,expect_store",
    [
        ("destination", "AVAILABLE", "ST_DST"),
        ("source", "AVAILABLE", "ST_SRC"),
        ("quarantine", "QUARANTINED", "ST_SRC"),
    ],
)
def test_each_disposition_lands_the_unit_and_audits_it(
    db, disposition, expect_status, expect_store
):
    rows = [r for r in stu.find_stranded(db, stores=[], transfers_filter=[])
            if r["stock_id"] == "u2"]
    stu.repair(db, rows, disposition=disposition, apply=True, actor="t")

    unit = db["stock_units"].find_one({"stock_id": "u2"})
    assert unit["status"] == expect_status
    assert unit["store_id"] == expect_store
    # It must stop being held against a transfer that will never move it again.
    assert unit["transfer_id"] is None
    assert unit["transfer_to_store_id"] is None

    audit = db["stock_audit"].find_one({"stock_id": "u2"})
    assert audit["prior_status"] == "TRANSFERRED"
    assert audit["new_status"] == expect_status
    assert audit["disposition"] == disposition
    assert audit["source"] == "STOCK_TRANSFER_REPAIR"


def test_destination_is_refused_when_there_is_no_destination(db):
    rows = [r for r in stu.find_stranded(db, stores=[], transfers_filter=[])
            if r["stock_id"] == "u4"]
    res = stu.repair(db, rows, disposition="destination", apply=True, actor="t")
    assert res["repaired"] == 0 and res["skipped_no_destination"] == 1
    assert db["stock_units"].find_one({"stock_id": "u4"})["status"] == "TRANSFERRED"
    # ...but it can still be put back on the source floor.
    assert stu.repair(db, rows, disposition="source", apply=True, actor="t")["repaired"] == 1


def test_a_unit_that_moved_since_the_report_is_left_alone(db):
    rows = stu.find_stranded(db, stores=[], transfers_filter=[])
    # A real receive lands u2 at the destination between report and repair.
    db["stock_units"].update_one(
        {"stock_id": "u2"},
        {"$set": {"status": "AVAILABLE", "store_id": "ST_DST", "transfer_id": None}},
    )
    res = stu.repair(db, rows, disposition="source", apply=True, actor="t")
    assert res["skipped_already_moved"] == 1
    unit = db["stock_units"].find_one({"stock_id": "u2"})
    assert unit["status"] == "AVAILABLE" and unit["store_id"] == "ST_DST", (
        "the guarded update must not yank a frame back off the destination floor"
    )


# ---------------------------------------------------------------------------
# SENTINEL guard -- so this never goes unnoticed again
# ---------------------------------------------------------------------------


def test_sentinel_counts_stranded_units_and_ignores_in_transit(db):
    from agents.implementations.sentinel import SentinelAgent

    agent = SentinelAgent.__new__(SentinelAgent)
    agent.get_collection = db.get_collection  # type: ignore[method-assign]

    assert agent._count_stranded_transfer_units() == 2

    stu.repair(db, stu.find_stranded(db, stores=[], transfers_filter=[]),
               disposition="source", apply=True, actor="t")
    assert agent._count_stranded_transfer_units() == 0
