"""
IMS 2.0 - AI change-proposal audit rows are HASH-CHAINED (SYSTEM_INTENT 8 + 10)
==============================================================================
Approving / rejecting an AI change-proposal must leave an IMMUTABLE, tamper-
evident audit trail. Before this change ProposalStore._write_audit did a raw
insert_one straight into `audit_logs`, BYPASSING the hash-chain -- so a proposal
audit row carried no seq / prev_hash / entry_hash and an out-of-band edit of the
captured before/after state was undetectable at GET /api/v1/audit/verify.

These tests prove the fix:

  * CHAINED      -- approve(advisory) / approve(reversible, executed) / reject
                    each write a row that carries seq + prev_hash + entry_hash.
  * VERIFIABLE   -- the resulting chain verifies intact:true.
  * COMMITTED    -- before_state / after_state are folded into the hash, so a
                    post-hoc edit of after_state flips verify() to intact:false.
  * FALLBACK     -- chaining still happens via the direct AuditRepository path
                    when the app dependency (get_audit_repository) is absent,
                    which is the cold-boot / unit-test case.

DB-backed, against a throwaway `ims_test_prop_audit_*` database on the local
mongo (CI runs a real mongo:7.0 service). SKIP -- not fail -- when no local
mongo is present, mirroring backend/tests/test_audit_chain.py.
"""

from __future__ import annotations

import os
import sys
import uuid

import pytest

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.proposals import ProposalStore, ProposalStatus  # noqa: E402
from database.repositories.audit_chain import (  # noqa: E402
    GENESIS_HASH,
    compute_entry_hash,
    verify_chain,
)


MONGO_HOST = os.getenv("MONGO_HOST", "127.0.0.1")
MONGO_PORT = int(os.getenv("MONGO_PORT", "27017"))

# Chain fields every PRIMARY (chained) audit row must carry.
CHAIN_FIELDS = ("seq", "prev_hash", "entry_hash")


def _mongo_db():
    """Connect to a throwaway DB on the local mongo, or skip if unreachable."""
    try:
        from pymongo import MongoClient
    except Exception:  # noqa: BLE001
        pytest.skip("pymongo not installed")
    try:
        client = MongoClient(MONGO_HOST, MONGO_PORT, serverSelectionTimeoutMS=1500)
        client.admin.command("ping")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"no local mongo at {MONGO_HOST}:{MONGO_PORT} ({exc})")
    name = f"ims_test_prop_audit_{uuid.uuid4().hex[:8]}"
    return client, client[name], name


@pytest.fixture
def prop_db():
    """A ProposalStore over a throwaway mongo db. ProposalStore binds its audit
    writes to its OWN db's audit_logs (AuditRepository.create -> append_audit_
    entry), exactly as it does in production where the router builds the store
    over the live/seeded DB -- so the full chained path runs in isolation here.

    Yields {db, store, audit_coll}. Drops the database on teardown.
    """
    client, db, name = _mongo_db()
    store = ProposalStore(db=db)
    try:
        yield {"db": db, "store": store, "audit_coll": db.get_collection("audit_logs")}
    finally:
        client.drop_database(name)
        client.close()


def _seed_pending(store, *, ptype: str, payload=None, before_state=None):
    """Enqueue a PENDING proposal and return its proposal_id."""
    doc = store.create(
        created_by_agent="ORACLE",
        proposal_type=ptype,
        title=f"test {ptype}",
        rationale="unit test",
        payload=payload or {},
        before_state=before_state,
    )
    assert doc is not None and doc["status"] == ProposalStatus.PENDING.value
    return doc["proposal_id"]


def _assert_chained(row):
    assert row is not None, "expected an audit_logs row to be written"
    for f in CHAIN_FIELDS:
        assert f in row, f"audit row is NOT chained: missing {f}"
    assert isinstance(row["seq"], int) and row["seq"] >= 1
    assert len(row["entry_hash"]) == 64  # sha256 hex
    # The stored entry_hash matches a fresh recompute of the row.
    assert row["entry_hash"] == compute_entry_hash(
        row["prev_hash"], row["seq"], row
    )


# ===========================================================================
# Reject -> chained audit row
# ===========================================================================


def test_reject_writes_chained_audit_row(prop_db):
    store, audit = prop_db["store"], prop_db["audit_coll"]
    pid = _seed_pending(store, ptype="price_ceiling_change")  # non-reversible

    res = store.reject(pid, reviewed_by="ceo@bv.in", reason="too aggressive")
    assert res["ok"] is True

    row = audit.find_one({"entity_id": pid, "action": "ai_proposal_rejected"})
    _assert_chained(row)
    assert row["entity_type"] == "ai_proposal"
    assert row["user_id"] == "ceo@bv.in"
    assert row["reason"] == "too aggressive"
    # The proposal now references the chained audit row.
    prop = store.get(pid)
    assert prop["status"] == ProposalStatus.REJECTED.value
    assert prop["audit_log_id"] == row["log_id"]

    assert verify_chain(audit)["intact"] is True


# ===========================================================================
# Approve (advisory, non-reversible) -> chained audit row
# ===========================================================================


def test_approve_advisory_writes_chained_audit_row(prop_db):
    store, audit = prop_db["store"], prop_db["audit_coll"]
    pid = _seed_pending(
        store,
        ptype="price_ceiling_change",  # NOT in REVERSIBLE_TYPES -> advisory
        before_state={"ceiling": 20},
    )

    res = store.approve(pid, reviewed_by="ceo@bv.in")
    assert res["ok"] is True and res["executed"] is False and res["advisory"] is True

    row = audit.find_one(
        {"entity_id": pid, "action": "ai_proposal_approved_advisory"}
    )
    _assert_chained(row)
    assert row["executed"] is False
    assert row["before_state"] == {"ceiling": 20}
    assert verify_chain(audit)["intact"] is True


# ===========================================================================
# Approve (reversible, executed) -> chained row that COMMITS before/after_state
# ===========================================================================


def test_approve_reversible_executed_chained_and_states_committed(prop_db):
    store, audit = prop_db["store"], prop_db["audit_coll"]
    # mark_task is reversible with a wired executor -> EXECUTED + after_state set.
    pid = _seed_pending(
        store,
        ptype="mark_task",
        payload={"title": "Follow up on reorder", "priority": "P2"},
    )

    res = store.approve(pid, reviewed_by="ceo@bv.in")
    assert res["ok"] is True and res["executed"] is True

    row = audit.find_one({"entity_id": pid, "action": "ai_proposal_executed"})
    _assert_chained(row)
    assert row["executed"] is True
    # The executor's after_state was captured on the (now hashed) audit row.
    assert row["after_state"] and row["after_state"].get("status") == "OPEN"

    # Whole chain is intact as written...
    assert verify_chain(audit)["intact"] is True

    # ...but editing the COMMITTED after_state out-of-band is now detectable,
    # which is the entire point of folding before/after_state into the hash.
    audit.update_one(
        {"log_id": row["log_id"]},
        {"$set": {"after_state": {"status": "TAMPERED"}}},
    )
    broken = verify_chain(audit)
    assert broken["intact"] is False
    assert broken["broken_at_seq"] == row["seq"]


# ===========================================================================
# Dependency-fallback: a db-less store chains via get_audit_repository()
# ===========================================================================


def test_chains_via_dependency_when_store_has_no_db(monkeypatch):
    """_audit_repo() binds to the store's own db first; when the store has NO
    db (db=None) it falls back to the app-level get_audit_repository(). Either
    way the write must go through AuditRepository (hash-chained), never a bare
    insert. Here we point the dependency at a throwaway db and drive a db-less
    store, proving the fallback path also chains + verifies."""
    from database.repositories.audit_repository import AuditRepository

    client, db, name = _mongo_db()
    try:
        repo = AuditRepository(db.get_collection("audit_logs"))
        import api.dependencies as deps

        monkeypatch.setattr(deps, "get_audit_repository", lambda: repo)

        # Store has no db of its own -> _write_audit must use the dependency.
        store = ProposalStore(db=None)
        proposal = {
            "proposal_id": "PROP-fallback",
            "created_by_agent": "ORACLE",
            "type": "price_ceiling_change",
            "reversible": False,
        }
        log_id = store._write_audit(
            action="ai_proposal_rejected",
            proposal=proposal,
            reviewed_by="ceo@bv.in",
            before_state={"ceiling": 20},
            after_state=None,
            executed=False,
            reason="fallback path",
        )
        assert log_id is not None

        audit = db.get_collection("audit_logs")
        row = audit.find_one({"log_id": log_id})
        _assert_chained(row)
        assert row["entity_id"] == "PROP-fallback"
        assert verify_chain(audit)["intact"] is True
    finally:
        client.drop_database(name)
        client.close()
