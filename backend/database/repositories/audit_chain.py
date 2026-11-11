"""
IMS 2.0 - Tamper-evident audit hash-chain
==========================================
SYSTEM_INTENT 10: the audit trail must be immutable, even for Superadmin.
We enforce that with two complementary guarantees:

  1. APPEND-ONLY at the app layer -- the `audit_logs` collection has NO
     update/delete route anywhere in the API. New entries can only be added,
     never edited or removed. (See backend/api/routers/audit.py for the note
     and the 403-gated mutation guards.)

  2. TAMPER-EVIDENT hash-chain -- every primary-audit row carries:
        seq         monotonically increasing sequence number
        prev_hash   the entry_hash of the row before it (genesis = 64 zeros)
        entry_hash  sha256(prev_hash + canonical_json(immutable fields))
     Any post-hoc edit to a chained field changes that row's entry_hash, which
     no longer matches the prev_hash recorded on the NEXT row -- so a single
     pass (GET /api/v1/audit/verify) detects the break and reports its seq.

CONCURRENT WRITERS (R19). A row's place in the chain is claimed by the INSERT
of the row itself: the writer reads the last chained row (the tail), stamps
seq = tail.seq + 1 and prev_hash = tail.entry_hash, and inserts. The UNIQUE
index on `audit_logs.seq` lets exactly one writer take a seq; a loser gets a
duplicate-key error, re-reads the tail and chains from the row that won. So
the claim and the predecessor it commits to are ONE single-document write --
no transaction needed, and nothing to resume: a writer that dies leaves either
a whole chained row or no row. (The old scheme claimed a seq on a head
document and committed its hash in a second write; two workers interleaving
between the two both chained from the same predecessor -- a "broken" chain
nobody had edited.)

The `audit_chain_head` document is no longer the source of the next seq. It
records the newest committed row (seq + entry_hash), written AFTER the row and
only ever forward, so verify can tell a trail whose last rows were removed
from a whole one (R20). A lagging head (a writer died between its row and
this note) is harmless: the next writer moves it on.

FAIL-SOFT CONTRACT: chaining must NEVER block the business action. If the tail
can't be read (DB down, transient error, a minimal mock collection), the audit
row is still inserted best-effort WITHOUT chain fields and a warning is
logged. verify counts such unchained rows separately (`unchained_rows`) --
far better than losing the audit record entirely.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import date, datetime, timezone
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# Genesis predecessor hash for the very first entry in the chain.
GENESIS_HASH = "0" * 64

# Name of the single-document collection holding the chain head.
HEAD_COLLECTION = "audit_chain_head"
HEAD_DOC_ID = "primary"

# The immutable business fields that the entry_hash commits to. Anything NOT
# in this list (e.g. derived/display fields) is intentionally excluded so the
# hash is stable and reproducible at verify time. `seq` and `prev_hash` are
# folded in separately (they ARE part of the commitment) -- see _compute_hash.
HASHED_FIELDS = (
    "action",
    "user_id",
    "entity_type",
    "entity_id",
    "store_id",
    "timestamp",
    "severity",
    "detail",
    "description",
    "previous_value",
    "new_value",
    # before_state / after_state are the canonical old->new snapshot the AI
    # change-proposal trail (agents/proposals.py) and other state-diffing
    # callers record. Committing them to the hash means a post-hoc edit of the
    # captured change is detectable at GET /api/v1/audit/verify, not just an
    # edit of the previous_value/new_value pair. Absent fields are skipped by
    # _hashable_view (if k in doc), so rows that never set these stay byte-for-
    # byte identical to before and the existing chain still verifies.
    "before_state",
    "after_state",
    "diff",
    "context",
    "metadata",
)


def _json_default(value: Any) -> str:
    """Make datetimes / dates / ObjectIds JSON-serialisable and, crucially,
    STABLE: the same logical value must serialise identically at write time
    and at verify time, otherwise the recomputed hash would never match."""
    if isinstance(value, datetime):
        # Two normalisations are BOTH required for the write-time and verify-time
        # byte strings to be identical (any drift -> the chain hash never matches
        # and GET /api/v1/audit/verify raises a FALSE "tamper detected").
        #
        # 1. TIMEZONE. Mongo stores datetimes as UTC and, with the default
        #    tz_aware=False client, returns them NAIVE. A tz-AWARE value hashed at
        #    write time isoformats WITH a "+00:00" offset that the naive read-back
        #    lacks -> the bytes differ. (This is the residual test_proposal_audit_
        #    chain failure the earlier ms-only fix missed: agents/proposals._now()
        #    is datetime.now(timezone.utc) -- tz-aware.) Convert any aware value to
        #    UTC and DROP tzinfo so both sides render the same naive-UTC string,
        #    regardless of the client's tz_aware setting or the source tz.
        # 2. PRECISION. Mongo stores at MILLISECOND precision; a microsecond value
        #    would not match the ms-truncated read-back. Truncate us -> ms.
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        if value.microsecond:
            value = value.replace(microsecond=(value.microsecond // 1000) * 1000)
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def canonical_json(payload: Dict[str, Any]) -> str:
    """Deterministic JSON: sorted keys, no insignificant whitespace, stable
    encoding of datetimes/ObjectIds. This is the exact byte-string the hash
    is taken over, so both the writer and the verifier must call it."""
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
        ensure_ascii=False,
    )


def _hashable_view(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Project a row down to only the immutable business fields the chain
    commits to, in a form safe for canonical_json."""
    return {k: doc.get(k) for k in HASHED_FIELDS if k in doc}


def compute_entry_hash(prev_hash: str, seq: int, doc: Dict[str, Any]) -> str:
    """entry_hash = sha256(prev_hash + canonical_json({seq, fields})).

    `seq` is included in the commitment so that reordering rows (or reusing a
    seq) also breaks verification, not just field edits.
    """
    commitment = {"seq": seq, "fields": _hashable_view(doc)}
    material = f"{prev_hash}{canonical_json(commitment)}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


# A writer that keeps losing the seq race gives up chaining after this many
# tries and writes its row unchained (fail-soft) rather than spin.
_MAX_CLAIM_ATTEMPTS = 25


def _is_duplicate_key(exc: Exception) -> bool:
    return getattr(exc, "code", None) == 11000 or type(exc).__name__ == "DuplicateKeyError"


def _ensure_seq_index(audit_collection) -> None:
    """The unique sparse `seq` index IS the serializer of concurrent appends
    (module docstring). App startup builds it (connection.py / schemas.py, the
    same spec); this builds it when the FIRST row of a chain is written, so a
    collection that startup never saw -- or one dropped and started again --
    is still guarded before two writers can meet. Nothing is chained yet at
    that point, so the build has no existing data to trip on. A failed build
    is logged loudly: without the index two workers can take the same seq."""
    create_index = getattr(audit_collection, "create_index", None)
    if not callable(create_index):
        return
    try:
        create_index("seq", unique=True, sparse=True)
    except Exception as exc:  # noqa: BLE001
        logger.error("[AUDIT_CHAIN] CRITICAL: unique seq index is NOT in place: %s", exc)


def _chain_tail(audit_collection) -> Tuple[int, str]:
    """(seq, entry_hash) of the last chained row; (0, GENESIS_HASH) when the
    chain is empty. Read from the trail itself, never from the head document."""
    for row in audit_collection.find({"seq": {"$gt": 0}}).sort("seq", -1).limit(1):
        return int(row["seq"]), row.get("entry_hash") or GENESIS_HASH
    return 0, GENESIS_HASH


def _note_head(db, seq: int, entry_hash: str) -> None:
    """Record the newest committed row on the head document, forward only
    (a slower writer never moves it back). Best-effort: verify reads it to
    notice a trail whose last rows are gone; nothing chains from it."""
    if db is None:
        return
    try:
        db.get_collection(HEAD_COLLECTION).update_one(
            {"_id": HEAD_DOC_ID, "seq": {"$lt": seq}},
            {"$set": {"seq": seq, "last_hash": entry_hash}},
            upsert=True,
        )
    except Exception as exc:  # noqa: BLE001
        # Duplicate key = the head is already at or past this row: fine.
        if not _is_duplicate_key(exc):
            logger.warning("[AUDIT_CHAIN] could not note chain head: %s", exc)


def append_audit_entry(
    audit_collection, doc: Dict[str, Any], db=None
) -> Optional[Dict[str, Any]]:
    """Insert ONE audit row into `audit_collection`, hash-chained.

    This is the single append point all primary-audit writes funnel through
    (AuditRepository.create wraps it). It reads the chain tail, stamps
    seq / prev_hash / entry_hash onto the row and inserts it; the unique seq
    index makes that insert the claim (module docstring), so a writer that
    lost the seq to a concurrent one re-reads the tail and tries again.

    `db` is the database handle used to reach the head collection. When not
    supplied we derive it from the audit collection's `.database` (pymongo
    collections expose this). If the tail can't be read, the row is still
    inserted WITHOUT chain fields (fail-soft) and returned.

    Returns the inserted document (with chain fields when chaining succeeded),
    or None if the insert failed.
    """
    if db is None:
        db = getattr(audit_collection, "database", None)

    for _ in range(_MAX_CLAIM_ATTEMPTS):
        try:
            seq, prev_hash = _chain_tail(audit_collection)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[AUDIT_CHAIN] chain tail unreadable: %s", exc)
            break
        if seq == 0:
            _ensure_seq_index(audit_collection)  # first row of the chain
        seq += 1
        doc["seq"] = seq
        doc["prev_hash"] = prev_hash
        doc["entry_hash"] = compute_entry_hash(prev_hash, seq, doc)
        try:
            audit_collection.insert_one(doc)
        except Exception as exc:  # noqa: BLE001
            try:
                seq_taken = _is_duplicate_key(exc) and (
                    audit_collection.find_one({"seq": seq}) is not None
                )
            except Exception:  # noqa: BLE001
                seq_taken = False
            if seq_taken:
                continue  # another writer took this seq: chain from the new tail
            # Any other failure (a duplicate _id / log_id, the DB) loses this
            # row exactly as before -- and leaves the chain untouched.
            logger.warning("[AUDIT_CHAIN] audit insert failed: %s", exc)
            return None
        _note_head(db, seq, doc["entry_hash"])
        return doc

    # Unchained fail-soft path: never block the business action. verify
    # counts these rows (unchained_rows).
    for field in ("seq", "prev_hash", "entry_hash"):
        doc.pop(field, None)
    logger.warning("[AUDIT_CHAIN] writing audit row WITHOUT chain (tail unavailable)")
    try:
        audit_collection.insert_one(doc)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[AUDIT_CHAIN] audit insert failed: %s", exc)
        return None
    return doc


def unverified_result(reason: str = "unreadable") -> Dict[str, Any]:
    """The answer when the trail could not be read: NOT intact, NOT verified.
    Nothing is known about it, which is never the same as a verified-empty
    trail (R20)."""
    return {
        "intact": False,
        "verified": False,
        "reason": reason,
        "broken_at_seq": None,
        "entries_checked": 0,
        "unchained_rows": None,
    }


def _head_hash(audit_collection) -> Optional[str]:
    """entry_hash of the newest row the head document recorded, or None when
    there is no head to compare (no database handle, or nothing chained yet)."""
    db = getattr(audit_collection, "database", None)
    if db is None:
        return None
    head = db.get_collection(HEAD_COLLECTION).find_one({"_id": HEAD_DOC_ID}) or {}
    last = head.get("last_hash")
    return last if last and last != GENESIS_HASH else None


def verify_chain(audit_collection) -> Dict[str, Any]:
    """Walk the chain ordered by seq, recompute every entry_hash, and confirm
    each row's prev_hash matches the prior row's entry_hash; then confirm the
    row the head document recorded is in the trail.

    Returns:
        {
          "intact": bool,              # True ONLY when every check ran and passed
          "verified": bool,            # False = the trail could not be read
          "reason": str | None,        # why it is not intact:
                                       #   "unreadable"           a read failed; nothing is known
                                       #   "prev_hash_mismatch"   a row does not link to the row before it
                                       #   "entry_hash_mismatch"  a row's contents no longer match its hash
                                       #   "head_not_in_trail"    the newest recorded row is missing:
                                       #                          the end of the trail was removed
          "broken_at_seq": int | None, # seq of the FIRST bad row
          "entries_checked": int,      # chained rows walked
          "unchained_rows": int | None # rows with no seq (written while the
                                       # chain was unreachable): outside the
                                       # hash chain, so NOT covered by "intact"
        }
    """
    try:
        # Head first: a row appended while we walk is then already in the trail.
        head_hash = _head_hash(audit_collection)
        unchained = int(audit_collection.count_documents({"seq": {"$exists": False}}))
        cursor = audit_collection.find({"seq": {"$exists": True}}).sort("seq", 1)

        def _broken(reason: str, seq, checked: int) -> Dict[str, Any]:
            return {"intact": False, "verified": True, "reason": reason,
                    "broken_at_seq": seq, "entries_checked": checked,
                    "unchained_rows": unchained}

        expected_prev = GENESIS_HASH
        checked = 0
        head_seen = head_hash is None
        for row in cursor:
            checked += 1
            seq = row.get("seq")
            stored_hash = row.get("entry_hash")
            stored_prev = row.get("prev_hash")

            # The link from the previous row must hold.
            if stored_prev != expected_prev:
                return _broken("prev_hash_mismatch", seq, checked)

            # The row's own contents must still hash to its stored entry_hash.
            if compute_entry_hash(stored_prev, seq, row) != stored_hash:
                return _broken("entry_hash_mismatch", seq, checked)

            head_seen = head_seen or stored_hash == head_hash
            expected_prev = stored_hash

        if not head_seen:
            return _broken("head_not_in_trail", None, checked)
        return {"intact": True, "verified": True, "reason": None, "broken_at_seq": None,
                "entries_checked": checked, "unchained_rows": unchained}
    except Exception as exc:  # noqa: BLE001
        logger.warning("[AUDIT_CHAIN] verify could not read chain: %s", exc)
        return unverified_result()
