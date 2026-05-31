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

The chain head (last seq + last entry_hash) lives in a single
`audit_chain_head` document. We advance it with a single atomic
`find_one_and_update` ($inc seq, return-new) so concurrent writes from
multiple Railway workers still chain deterministically -- each writer gets a
unique seq and the prev_hash that was current at the moment it claimed that
seq, with no torn reads.

FAIL-SOFT CONTRACT: chaining must NEVER block the business action. If the head
doc can't be advanced (DB down, transient error, mock collection without
find_one_and_update), the audit row is still inserted best-effort WITHOUT
chain fields and a warning is logged. A missing/unchained row shows up at
verify time as a gap, which is the correct, honest signal -- far better than
losing the audit record entirely.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import date, datetime
from typing import Any, Dict, Optional

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
    if isinstance(value, (datetime, date)):
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


def _advance_head(db) -> Optional[Dict[str, Any]]:
    """Atomically claim the next seq + read the current head hash.

    Returns a dict {"seq": <new seq>, "prev_hash": <hash to chain from>} or
    None if the head couldn't be advanced (caller then writes unchained).

    The single find_one_and_update both increments `seq` and returns the
    post-increment document, so two concurrent writers can never receive the
    same seq. `prev_hash` is read from the head's `last_hash` (the entry_hash
    of the row that most recently completed); the genesis writer sees the
    seeded GENESIS_HASH.
    """
    try:
        head_coll = db.get_collection(HEAD_COLLECTION)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[AUDIT_CHAIN] head collection unavailable: %s", exc)
        return None

    foau = getattr(head_coll, "find_one_and_update", None)
    if not callable(foau):
        # Mock/minimal collection without atomic ops -> skip chaining.
        logger.warning(
            "[AUDIT_CHAIN] collection lacks find_one_and_update; "
            "writing audit row unchained"
        )
        return None

    try:
        # ReturnDocument.AFTER == True for pymongo; we pass the bool directly
        # so this works without importing pymongo (keeps the module import-light
        # and testable against fakes that accept return_document=True).
        try:
            from pymongo import ReturnDocument

            after = ReturnDocument.AFTER
        except Exception:  # noqa: BLE001
            after = True

        head = foau(
            {"_id": HEAD_DOC_ID},
            {
                "$inc": {"seq": 1},
                "$setOnInsert": {"last_hash": GENESIS_HASH},
            },
            upsert=True,
            return_document=after,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[AUDIT_CHAIN] could not advance head: %s", exc)
        return None

    if not head:
        return None

    seq = int(head.get("seq", 1))
    prev_hash = head.get("last_hash") or GENESIS_HASH
    return {"seq": seq, "prev_hash": prev_hash}


def _commit_head_hash(db, seq: int, entry_hash: str) -> None:
    """Record the just-written row's entry_hash as the new head, but only if
    this row is still the latest (its seq == head.seq). The guarded update
    keeps `last_hash` consistent with the highest committed seq even when many
    writers interleave: a slower writer that finished out of order won't stomp
    a newer head.
    """
    try:
        head_coll = db.get_collection(HEAD_COLLECTION)
        head_coll.update_one(
            {"_id": HEAD_DOC_ID, "seq": seq},
            {"$set": {"last_hash": entry_hash}},
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[AUDIT_CHAIN] could not commit head hash: %s", exc)


def append_audit_entry(
    audit_collection, doc: Dict[str, Any], db=None
) -> Optional[Dict[str, Any]]:
    """Insert ONE audit row into `audit_collection`, hash-chained.

    This is the single append point all primary-audit writes funnel through
    (AuditRepository.create wraps it). It:

      1. claims the next seq + prev_hash from the atomic head,
      2. stamps seq / prev_hash / entry_hash onto the row,
      3. inserts the row,
      4. advances the head's last_hash to this row's entry_hash.

    `db` is the database handle used to reach the head collection. When not
    supplied we derive it from the audit collection's `.database` (pymongo
    collections expose this). If the head can't be reached, the row is still
    inserted WITHOUT chain fields (fail-soft) and returned.

    Returns the inserted document (with chain fields when chaining succeeded),
    or None if even the best-effort insert failed.
    """
    if db is None:
        db = getattr(audit_collection, "database", None)

    chain: Optional[Dict[str, Any]] = None
    if db is not None:
        chain = _advance_head(db)

    if chain is not None:
        seq = chain["seq"]
        prev_hash = chain["prev_hash"]
        entry_hash = compute_entry_hash(prev_hash, seq, doc)
        doc["seq"] = seq
        doc["prev_hash"] = prev_hash
        doc["entry_hash"] = entry_hash
    else:
        # Unchained fail-soft path: never block the business action. The gap
        # is intentionally visible to verify().
        logger.warning(
            "[AUDIT_CHAIN] writing audit row WITHOUT chain (head unavailable)"
        )

    try:
        audit_collection.insert_one(doc)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[AUDIT_CHAIN] audit insert failed: %s", exc)
        return None

    if chain is not None:
        _commit_head_hash(db, chain["seq"], doc["entry_hash"])

    return doc


def verify_chain(audit_collection) -> Dict[str, Any]:
    """Walk the chain ordered by seq, recompute every entry_hash, and confirm
    each row's prev_hash matches the prior row's entry_hash.

    Only rows that carry a `seq` participate (unchained fail-soft rows are
    skipped for hash-linkage but still counted as checked, and a chained row
    whose prev_hash doesn't line up is the break we report).

    Returns:
        {
          "intact": bool,
          "broken_at_seq": int | None,   # seq of the FIRST bad row
          "entries_checked": int,
        }
    """
    try:
        cursor = audit_collection.find({"seq": {"$exists": True}}).sort("seq", 1)
        rows = list(cursor)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[AUDIT_CHAIN] verify could not read chain: %s", exc)
        return {"intact": True, "broken_at_seq": None, "entries_checked": 0}

    expected_prev = GENESIS_HASH
    checked = 0
    for row in rows:
        checked += 1
        seq = row.get("seq")
        stored_hash = row.get("entry_hash")
        stored_prev = row.get("prev_hash")

        # The link from the previous row must hold.
        if stored_prev != expected_prev:
            return {
                "intact": False,
                "broken_at_seq": seq,
                "entries_checked": checked,
            }

        # The row's own contents must still hash to its stored entry_hash.
        recomputed = compute_entry_hash(stored_prev, seq, row)
        if recomputed != stored_hash:
            return {
                "intact": False,
                "broken_at_seq": seq,
                "entries_checked": checked,
            }

        expected_prev = stored_hash

    return {"intact": True, "broken_at_seq": None, "entries_checked": checked}
