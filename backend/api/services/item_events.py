"""
IMS 2.0 - E3 Item-event ledger (the append-only stock-state spine)
==================================================================

Every physical state change to a stocked unit -- mint, serial-bind, quarantine,
quarantine-release, transfer-ship/receive, reserve, sell, void, adjust -- is
recorded as ONE immutable row in the `item_events` collection. These rows are:

  * Monotonically sequenced per a shared `item_event_seq` counter, reusing the
    EXISTING atomic counter pattern (`barcode.allocate_sequence`). `event_seq` is
    strictly increasing across the whole deployment; per-unit history is read by
    sorting on it.
  * Append-only BY POLICY: no update/delete path exists (the router exposes no
    PUT/DELETE on a ledger row, mirroring how `audit_logs` is protected). There
    is NO unit-level hash-chain on the event head (CORRECTIONS P0-2): routing the
    high-frequency event stream through `audit_chain.append_audit_entry` would
    serialise the POS sell-path on a single global chain head and the per-unit
    chain is never verified. Instead each material event ALSO writes ONE row to
    the general tamper-evident `audit_logs` trail via `AuditRepository.create`
    (NEVER `append_audit_entry` directly).
  * The single source of truth for unit history; `stock_units.status` is a
    projection this service keeps in sync via a single-document
    `find_one_and_update` CAS (guarded on the expected `from_state`).

Standalone Mongo (no replica set) => NO multi-document transactions. Each
`record_event` is two SINGLE-document operations: a CAS `find_one_and_update` on
`stock_units` (when a stock_id is supplied), then an `insert_one` on
`item_events`. We never claim cross-collection atomicity. The CAS is the only
concurrency guarantee -- it mirrors `vouchers.redeem_voucher_atomic`: exactly one
of two racing writers wins the transition; the loser gets `ok=False`.

The new statuses QUARANTINED, UNDER_AUDIT and BLIND_COUNT are excluded from every
on-hand / sellable rollup (see `EXCLUDED_STATUSES`), so a unit in any of those
states cannot be sold or transferred -- it simply is not in any sellable allowlist.

`status` stays a FREE STRING in the Mongo document (CORRECTIONS P0-6 / P1): the
`StockState` enum below is used by new code for legal-transition reasoning, but a
unit minted with a legacy lowercase "available" is still canonicalised on read.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Shared counter doc name for the monotonic event sequence (reuses the existing
# `counters` collection + barcode.allocate_sequence atomic find_one_and_update).
EVENT_SEQ_COUNTER = "item_event_seq"


# ---------------------------------------------------------------------------
# Enums (StockState + ItemEventType) + state machine
# ---------------------------------------------------------------------------


class StockState(str, Enum):
    """Canonical lifecycle states for a serialized stock unit.

    The Mongo document stores `status` as a free string; this enum is the
    canonical vocabulary new code reasons over. `canonical_state` maps legacy
    colour-flag / case variants onto these members.
    """

    AVAILABLE = "AVAILABLE"
    RESERVED = "RESERVED"
    TRANSFERRED = "TRANSFERRED"
    QUARANTINED = "QUARANTINED"
    UNDER_AUDIT = "UNDER_AUDIT"
    BLIND_COUNT = "BLIND_COUNT"
    SOLD = "SOLD"
    VOID = "VOID"
    DAMAGED = "DAMAGED"
    RTV = "RTV"


class ItemEventType(str, Enum):
    """Material events recorded in the ledger."""

    MINT = "mint"
    SERIAL_BIND = "serial.bind"
    QUARANTINE_IN = "quarantine.in"
    QUARANTINE_OUT = "quarantine.out"
    AUDIT_FLAG = "audit.flag"
    AUDIT_CLEAR = "audit.clear"
    TRANSFER_SHIP = "transfer.ship"
    TRANSFER_RECEIVE = "transfer.receive"
    RESERVE = "reserve"
    RELEASE = "release"
    SELL = "sell"
    VOID = "void"
    ADJUST = "adjust"


# Legal state-machine edges (pure dict, unit-tested). A transition is legal iff
# `to` is in ALLOWED_TRANSITIONS[from]. Terminal states (SOLD / VOID / RTV) have
# no outgoing edges -- you cannot quarantine a SOLD unit, etc.
ALLOWED_TRANSITIONS: Dict[StockState, set] = {
    StockState.AVAILABLE: {
        StockState.RESERVED,
        StockState.TRANSFERRED,
        StockState.QUARANTINED,
        StockState.UNDER_AUDIT,
        StockState.BLIND_COUNT,
        StockState.SOLD,
        StockState.VOID,
        StockState.DAMAGED,
    },
    StockState.RESERVED: {
        StockState.AVAILABLE,  # release
        StockState.SOLD,  # commit
        StockState.VOID,
    },
    StockState.TRANSFERRED: {
        StockState.AVAILABLE,  # receive at destination
        StockState.VOID,
    },
    StockState.QUARANTINED: {
        StockState.AVAILABLE,  # release/RESTOCK
        StockState.DAMAGED,  # release/DAMAGE
        StockState.RTV,  # release/RTV
    },
    StockState.UNDER_AUDIT: {
        StockState.AVAILABLE,  # audit.clear
        StockState.VOID,  # shrinkage
    },
    StockState.BLIND_COUNT: {
        StockState.AVAILABLE,
        StockState.VOID,
    },
    StockState.DAMAGED: {
        StockState.QUARANTINED,
        StockState.RTV,
        StockState.VOID,
    },
    # Terminal -- no outgoing transitions.
    StockState.SOLD: set(),
    StockState.VOID: set(),
    StockState.RTV: set(),
}


# Status values that mean "physically on hand and sellable now". Includes the
# legacy lowercase / IN_STOCK variants seen in dirty/migrated data so a unit
# minted under an old code path still counts.
ON_HAND_STATUSES: List[str] = ["AVAILABLE", "available", "IN_STOCK", "in_stock"]

# Statuses that are explicitly NOT on hand / NOT sellable. Every rollup excludes
# these. RESERVED is on hold for an order so it is NOT in either list (a reserved
# unit is committed stock, neither freely sellable nor excluded from valuation).
EXCLUDED_STATUSES: List[str] = [
    "QUARANTINED",
    "UNDER_AUDIT",
    "BLIND_COUNT",
    "TRANSFERRED",
    "SOLD",
    "VOID",
    "DAMAGED",
    "RTV",
]


def canonical_state(raw) -> Optional[StockState]:
    """Map any stored status string (legacy colour-flag / case variant) onto the
    canonical `StockState`. None for an unknown value (caller decides)."""
    if raw is None:
        return None
    s = str(raw).strip().upper()
    if s in ("AVAILABLE", "IN_STOCK"):
        return StockState.AVAILABLE
    try:
        return StockState(s)
    except ValueError:
        return None


def is_legal_transition(frm, to) -> bool:
    """True iff `frm -> to` is a legal edge. Accepts StockState or raw strings.

    Returns False for an unknown `frm` so an illegal/garbage source state can
    never be transitioned (fail-closed for the state machine)."""
    frm_s = frm if isinstance(frm, StockState) else canonical_state(frm)
    to_s = to if isinstance(to, StockState) else canonical_state(to)
    if frm_s is None or to_s is None:
        return False
    return to_s in ALLOWED_TRANSITIONS.get(frm_s, set())


# ---------------------------------------------------------------------------
# Recorder (the only writer of item_events)
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _allocate_seq(db) -> Optional[int]:
    """Claim the next monotonic event_seq from the shared `counters` collection
    via the EXISTING atomic find_one_and_update (barcode.allocate_sequence).
    Fail-soft None when no DB / counter is reachable."""
    if db is None:
        return None
    try:
        from . import barcode as barcode_svc

        counter = db.get_collection("counters")
        return barcode_svc.allocate_sequence(counter, EVENT_SEQ_COUNTER)
    except Exception as e:  # noqa: BLE001
        logger.warning("[ITEM_EVENTS] seq allocation failed: %s", e)
        return None


# Events that are business-critical enough to also write a row to the general
# tamper-evident audit trail (AuditRepository.create). High-frequency events
# (reserve/release/transfer/sell) stay in item_events only -- routing them
# through the global hash-chain head would serialise the POS path (P0-2).
_AUDITED_EVENT_TYPES = {
    ItemEventType.QUARANTINE_IN,
    ItemEventType.QUARANTINE_OUT,
    ItemEventType.AUDIT_FLAG,
    ItemEventType.AUDIT_CLEAR,
    ItemEventType.SERIAL_BIND,
    ItemEventType.VOID,
}


def _write_audit_row(event_type: ItemEventType, doc: dict) -> None:
    """One AuditRepository.create row per MATERIAL event (P0-2 / P1). Fail-soft:
    an audit hiccup never undoes the business write that triggered it."""
    if event_type not in _AUDITED_EVENT_TYPES:
        return
    try:
        from ..dependencies import get_audit_repository

        audit = get_audit_repository()
        if audit is not None:
            audit.create(
                {
                    "action": f"ITEM_EVENT_{event_type.name}",
                    "entity_type": "STOCK_UNIT" if doc.get("stock_id") else "STOCK_CELL",
                    "entity_id": doc.get("stock_id") or doc.get("lens_line_id"),
                    "store_id": doc.get("store_id"),
                    "user_id": doc.get("actor_id"),
                    "before_state": {"status": doc.get("from_state")},
                    "after_state": {"status": doc.get("to_state")},
                    "detail": {
                        "event_id": doc.get("event_id"),
                        "event_seq": doc.get("event_seq"),
                        "event_type": doc.get("event_type"),
                        "payload": doc.get("payload"),
                    },
                }
            )
    except Exception as e:  # noqa: BLE001
        logger.warning("[ITEM_EVENTS] audit row failed: %s", e)


def record_event(
    db,
    *,
    event_type: ItemEventType,
    actor_id: str,
    stock_id: Optional[str] = None,
    from_state=None,
    to_state=None,
    store_id: Optional[str] = None,
    to_store_id: Optional[str] = None,
    product_id: Optional[str] = None,
    source_type: Optional[str] = None,
    source_id: Optional[str] = None,
    serial: Optional[str] = None,
    payload: Optional[dict] = None,
    lens_line_id: Optional[str] = None,
    cell_key: Optional[str] = None,
    enforce_transition: bool = True,
) -> Optional[dict]:
    """Record one material item event + (when a stock_id is given) project the
    status onto `stock_units` via a single-document CAS.

    Returns the inserted event doc on success, or None when:
      * no DB / counter is reachable (fail-soft), OR
      * `enforce_transition` and the CAS lost (the unit was not in `from_state`
        when we tried to flip it -- a racing writer won, or the caller's
        expectation was wrong).

    The CAS guards on ``{stock_id, status: from_state}`` so exactly one of two
    concurrent transitions on the same unit succeeds (mirrors
    `vouchers.redeem_voucher_atomic`). Standalone Mongo: this is two single-doc
    ops, never a transaction.
    """
    if db is None:
        return None

    frm = from_state.value if isinstance(from_state, StockState) else from_state
    to = to_state.value if isinstance(to_state, StockState) else to_state

    # Legal-transition gate (only when this event projects a status change).
    if enforce_transition and stock_id is not None and frm is not None and to is not None:
        if not is_legal_transition(frm, to):
            return None

    seq = _allocate_seq(db)
    if seq is None:
        return None

    # 1. CAS the stock_units projection FIRST (when this event owns a unit
    #    status change). Guard on the expected from_state so a racing writer or
    #    a stale expectation loses cleanly. A pure-read event (no to_state) skips
    #    the CAS.
    if stock_id is not None and to is not None:
        try:
            from pymongo import ReturnDocument

            cas_filter: dict = {"stock_id": stock_id}
            if frm is not None:
                # Accept the canonical value OR its legacy lowercase/IN_STOCK
                # variants so a unit minted under an old path still transitions.
                variants = {frm}
                if frm == "AVAILABLE":
                    variants |= {"available", "IN_STOCK", "in_stock"}
                cas_filter["status"] = {"$in": list(variants)}
            set_block: dict = {"status": to, "last_event_seq": seq}
            if serial is not None:
                set_block["serial"] = serial
            if isinstance(payload, dict):
                for k in ("quarantine_reason", "blind_count_id", "under_audit"):
                    if k in payload:
                        set_block[k] = payload[k]
            updated = db.get_collection("stock_units").find_one_and_update(
                cas_filter,
                {"$set": set_block},
                return_document=ReturnDocument.AFTER,
            )
            if updated is None:
                # CAS lost -- the unit was not in the expected from_state.
                return None
            if product_id is None:
                product_id = updated.get("product_id")
            if store_id is None:
                store_id = updated.get("store_id")
        except Exception as e:  # noqa: BLE001
            logger.warning("[ITEM_EVENTS] stock CAS failed for %s: %s", stock_id, e)
            return None

    # 2. Insert the immutable ledger row (append-only; no update/delete path).
    doc = {
        "event_id": str(uuid.uuid4()),
        "event_seq": seq,
        "stock_id": stock_id,
        "lens_line_id": lens_line_id,
        "cell_key": cell_key,
        "serial": serial,
        "event_type": event_type.value if isinstance(event_type, ItemEventType) else event_type,
        "from_state": frm,
        "to_state": to,
        "product_id": product_id,
        "store_id": store_id,
        "to_store_id": to_store_id,
        "actor_id": actor_id,
        "source_type": source_type,
        "source_id": source_id,
        "payload": payload or {},
        "at": _now(),
    }
    try:
        db.get_collection("item_events").insert_one(dict(doc))
    except Exception as e:  # noqa: BLE001
        logger.warning("[ITEM_EVENTS] ledger insert failed: %s", e)
        # The status projection already happened; the ledger row is advisory.
        # We still surface the doc so the caller's response is consistent.

    # 3. One audit-trail row for business-critical events (P0-2 / P1).
    et = event_type if isinstance(event_type, ItemEventType) else None
    if et is not None:
        _write_audit_row(et, doc)

    return doc


def record_event_atomic(db, **kw) -> Tuple[bool, Optional[dict]]:
    """``(ok, event)``: ok=False on CAS loss / illegal transition / no-DB.

    Thin wrapper over `record_event` for callers that want an explicit success
    boolean (e.g. the concurrency-safe sell path). The CAS inside `record_event`
    is the single-document guarantee."""
    ev = record_event(db, **kw)
    return (ev is not None), ev


# ---------------------------------------------------------------------------
# Ledger read helper
# ---------------------------------------------------------------------------


def unit_history(db, stock_id: str) -> List[dict]:
    """Full event ledger for one unit, ascending by `event_seq`. Fail-soft []."""
    if db is None or not stock_id:
        return []
    try:
        rows = list(db.get_collection("item_events").find({"stock_id": stock_id}))
    except Exception:  # noqa: BLE001
        return []
    for r in rows:
        r.pop("_id", None)
    rows.sort(key=lambda r: r.get("event_seq", 0))
    return rows


# ---------------------------------------------------------------------------
# Base-Bank replenishment (pure helpers, no DB)
# ---------------------------------------------------------------------------


def required_qty(base_bank: int, in_hand: int) -> int:
    """Signed replenishment need: base_bank - in_hand. Negative = overstock."""
    return int(base_bank) - int(in_hand)


def build_replenishment(slots: List[dict]) -> List[dict]:
    """Annotate each cell with its `required` qty.

    slots: ``[{cell_key, base_bank, in_hand}, ...]`` -> same with `required`
    added. Pure -- no DB, deterministic, unit-tested independently.
    """
    out: List[dict] = []
    for s in slots or []:
        base = int(s.get("base_bank", 0) or 0)
        hand = int(s.get("in_hand", 0) or 0)
        row = dict(s)
        row["base_bank"] = base
        row["in_hand"] = hand
        row["required"] = required_qty(base, hand)
        out.append(row)
    return out
