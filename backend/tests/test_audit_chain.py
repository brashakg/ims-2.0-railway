"""
IMS 2.0 - Tamper-evident audit hash-chain (SYSTEM_INTENT 10)
============================================================
The audit trail must be immutable, even for Superadmin. We prove the two
guarantees that back that claim:

  * APPEND + CHAIN -- routing every primary-audit write through
    AuditRepository.create() (which funnels to audit_chain.append_audit_entry)
    links the rows: each row's prev_hash == the previous row's entry_hash,
    seq increments by 1, and the genesis row chains from 64 zeros.

  * TAMPER-EVIDENT -- editing any committed field of a MIDDLE row (exactly the
    kind of after-the-fact cover-up SYSTEM_INTENT 10 forbids) makes verify() report
    intact:false and pin the break to that row's seq.

  * CLEAN CHAIN -- an untouched chain verifies intact:true.

  * FAIL-SOFT -- the append helper never raises when the chain head is
    unreachable; the DB-backed cases SKIP (not fail) when no local mongo is
    present, mirroring backend/tests/test_transfer_stock_movement.py.

DB-backed tests run against a throwaway `ims_test_audit_*` database on the
local mongo at MONGO_HOST:MONGO_PORT, so the atomic head advance + real
find/sort path is exercised end to end.
"""

from __future__ import annotations

import os
import sys
import uuid

import pytest

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.repositories.audit_chain import (  # noqa: E402
    GENESIS_HASH,
    append_audit_entry,
    compute_entry_hash,
    verify_chain,
)


# ===========================================================================
# Pure-helper tests (no DB) -- hash determinism + sensitivity
# ===========================================================================


def test_compute_entry_hash_is_deterministic():
    doc = {"action": "order.edited", "user_id": "u1", "entity_id": "O-1"}
    h1 = compute_entry_hash(GENESIS_HASH, 1, doc)
    h2 = compute_entry_hash(GENESIS_HASH, 1, dict(doc))
    assert h1 == h2
    assert len(h1) == 64  # sha256 hex


def test_compute_entry_hash_changes_when_field_changes():
    base = {"action": "order.edited", "user_id": "u1", "entity_id": "O-1"}
    tampered = {"action": "order.edited", "user_id": "u1", "entity_id": "O-2"}
    assert compute_entry_hash(GENESIS_HASH, 1, base) != compute_entry_hash(
        GENESIS_HASH, 1, tampered
    )


def test_compute_entry_hash_changes_when_prev_or_seq_changes():
    doc = {"action": "a", "user_id": "u1"}
    assert compute_entry_hash(GENESIS_HASH, 1, doc) != compute_entry_hash(
        "f" * 64, 1, doc
    )
    assert compute_entry_hash(GENESIS_HASH, 1, doc) != compute_entry_hash(
        GENESIS_HASH, 2, doc
    )


def test_verify_empty_collection_is_intact():
    """No rows -> nothing to tamper with -> intact:true, 0 checked."""

    class _Empty:
        def find(self, *_a, **_k):
            class _C(list):
                def sort(self, *_a, **_k):
                    return self

            return _C()

    res = verify_chain(_Empty())
    assert res == {"intact": True, "broken_at_seq": None, "entries_checked": 0}


# ===========================================================================
# Fail-soft: append must not raise when the head is unreachable
# ===========================================================================


def test_append_failsoft_without_db_does_not_raise():
    """No db handle + a minimal collection (no find_one_and_update) -> the row
    is still inserted, just UNCHAINED, and nothing raises."""
    inserted = {}

    class _MinimalColl:
        # Deliberately NO find_one_and_update and NO `.database`.
        def insert_one(self, doc):
            inserted.update(doc)

            class _R:
                inserted_id = doc.get("_id")

            return _R()

    out = append_audit_entry(_MinimalColl(), {"action": "x", "user_id": "u"})
    assert out is not None
    # Unchained fail-soft row: no chain fields stamped, but the audit IS kept.
    assert "entry_hash" not in out
    assert inserted.get("action") == "x"


def test_append_failsoft_when_insert_blows_up_returns_none():
    class _BoomColl:
        def insert_one(self, doc):
            raise RuntimeError("db down")

    out = append_audit_entry(_BoomColl(), {"action": "x"})
    assert out is None  # never raises; signals failure by returning None


# ===========================================================================
# DB-backed chain tests against a real throwaway mongo
# ===========================================================================


MONGO_HOST = os.getenv("MONGO_HOST", "127.0.0.1")
MONGO_PORT = int(os.getenv("MONGO_PORT", "27017"))


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
    name = f"ims_test_audit_{uuid.uuid4().hex[:8]}"
    return client, client[name], name


@pytest.fixture
def audit_repo_db():
    """Real AuditRepository wired to the `audit_logs` collection of a throwaway
    mongo DB. The repo's create() override exercises the full hash-chain
    append, and `.collection.database` reaches the atomic head collection."""
    from database.repositories.audit_repository import AuditRepository

    client, db, name = _mongo_db()
    repo = AuditRepository(db.get_collection("audit_logs"))
    try:
        yield {"db": db, "repo": repo}
    finally:
        client.drop_database(name)
        client.close()


def _append(repo, action, entity_id, user_id="u-tester"):
    return repo.create(
        {
            "log_id": uuid.uuid4().hex,
            "action": action,
            "user_id": user_id,
            "entity_type": "order",
            "entity_id": entity_id,
            "store_id": "S-1",
            "severity": "info",
        }
    )


def test_appending_n_entries_links_them(audit_repo_db):
    repo = audit_repo_db["repo"]
    db = audit_repo_db["db"]
    N = 6

    for i in range(N):
        out = _append(repo, "order.edited", f"O-{i}")
        assert out is not None
        assert "entry_hash" in out and "prev_hash" in out and "seq" in out

    rows = list(
        db.get_collection("audit_logs").find({"seq": {"$exists": True}}).sort("seq", 1)
    )
    assert len(rows) == N

    # seq is 1..N monotonic; genesis chains from zeros; each prev_hash links.
    expected_prev = GENESIS_HASH
    for i, row in enumerate(rows):
        assert row["seq"] == i + 1
        assert row["prev_hash"] == expected_prev
        # The stored entry_hash matches a fresh recompute of the row.
        assert row["entry_hash"] == compute_entry_hash(
            row["prev_hash"], row["seq"], row
        )
        expected_prev = row["entry_hash"]


def test_verify_intact_on_untampered_chain(audit_repo_db):
    repo = audit_repo_db["repo"]
    for i in range(5):
        _append(repo, "order.cancelled", f"O-{i}")

    res = verify_chain(repo.collection)
    assert res["intact"] is True
    assert res["broken_at_seq"] is None
    assert res["entries_checked"] == 5


def test_tampering_middle_entry_is_detected(audit_repo_db):
    repo = audit_repo_db["repo"]
    db = audit_repo_db["db"]
    N = 5
    for i in range(N):
        _append(repo, "order.edited", f"O-{i}")

    # Tamper with the MIDDLE row's committed field (seq == 3) directly in the
    # DB, simulating an out-of-band edit that the API would never allow.
    coll = db.get_collection("audit_logs")
    coll.update_one({"seq": 3}, {"$set": {"user_id": "ATTACKER"}})

    res = verify_chain(repo.collection)
    assert res["intact"] is False
    # The recomputed hash of row 3 no longer matches its stored entry_hash.
    assert res["broken_at_seq"] == 3
    assert res["entries_checked"] == 3  # stops at the first break


def test_tampering_breaks_link_for_following_row(audit_repo_db):
    """Even if an attacker rewrites a row's OWN entry_hash to match its edited
    contents, the NEXT row's prev_hash still points at the original hash, so
    the break is caught at the successor."""
    repo = audit_repo_db["repo"]
    db = audit_repo_db["db"]
    for i in range(4):
        _append(repo, "order.edited", f"O-{i}")

    coll = db.get_collection("audit_logs")
    row2 = coll.find_one({"seq": 2})
    # Edit the field AND re-stamp a self-consistent entry_hash for row 2.
    row2["user_id"] = "ATTACKER"
    forged = compute_entry_hash(row2["prev_hash"], row2["seq"], row2)
    coll.update_one(
        {"seq": 2}, {"$set": {"user_id": "ATTACKER", "entry_hash": forged}}
    )

    res = verify_chain(repo.collection)
    assert res["intact"] is False
    # Row 2 now self-verifies, but row 3's prev_hash != row 2's forged hash.
    assert res["broken_at_seq"] == 3
