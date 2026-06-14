"""Endless Aisle - inter-branch fulfillment of an out-of-stock SKU.

Feature #38. When a selling store is out of stock of a SKU but another
branch holds the unit, a STORE_MANAGER+ can open a fulfillment request.
A two-step source-ACCEPT (the holding branch confirms the unit is real and
sellable) prevents selling ghost stock. The company bears shipping (booked
to the SELLING store's P&L); the customer pays nothing extra and sees no
shipping line on the bill. Eligible source stores are an editable setting
(default ALL). Payment is always collected at the SELLING store.

This module is pure (no DB): the lifecycle state machine, the source-store
selection algorithm, and validation helpers. The router owns all I/O.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# --- Status lifecycle -------------------------------------------------------

STATUS_PENDING = "PENDING"
STATUS_ACCEPTED = "ACCEPTED"
STATUS_TRANSFER_CREATED = "TRANSFER_CREATED"
STATUS_SHIPPED = "SHIPPED"
STATUS_DELIVERED = "DELIVERED"
STATUS_REJECTED = "REJECTED"
STATUS_CANCELLED = "CANCELLED"

ALL_STATUSES = (
    STATUS_PENDING,
    STATUS_ACCEPTED,
    STATUS_TRANSFER_CREATED,
    STATUS_SHIPPED,
    STATUS_DELIVERED,
    STATUS_REJECTED,
    STATUS_CANCELLED,
)

# Legal transitions. PENDING may be accepted, rejected, or cancelled.
# An ACCEPTED request becomes a transfer or may still be cancelled.
ALLOWED_TRANSITIONS: Dict[str, set] = {
    STATUS_PENDING: {STATUS_ACCEPTED, STATUS_REJECTED, STATUS_CANCELLED},
    STATUS_ACCEPTED: {STATUS_TRANSFER_CREATED, STATUS_CANCELLED},
    STATUS_TRANSFER_CREATED: {STATUS_SHIPPED, STATUS_CANCELLED},
    STATUS_SHIPPED: {STATUS_DELIVERED},
    STATUS_DELIVERED: set(),
    STATUS_REJECTED: set(),
    STATUS_CANCELLED: set(),
}


def can_transition(frm: str, to: str) -> bool:
    """True iff moving a request from status `frm` to `to` is legal."""
    return to in ALLOWED_TRANSITIONS.get(frm, set())


# --- Source-store selection (pure) -----------------------------------------


def find_fulfillment_sources(
    on_hand_by_store: Dict[str, int],
    selling_store_id: str,
    eligible_store_ids: Optional[List[str]],
    qty: int,
) -> List[Dict[str, Any]]:
    """Candidate source stores for an OOS SKU. Pure.

    Excludes the selling store; keeps only ELIGIBLE stores (an empty / falsy
    ``eligible_store_ids`` means ALL stores are eligible -- the default); keeps
    only stores whose on-hand covers ``qty``; returns them best-first (most
    stock), deterministic (ties broken by store_id).
    """
    want = max(1, int(qty or 0))
    eligible = set(eligible_store_ids or [])
    out: List[Dict[str, Any]] = []
    for store_id, on_hand in (on_hand_by_store or {}).items():
        if store_id == selling_store_id:
            continue
        if eligible and store_id not in eligible:
            continue
        oh = int(on_hand or 0)
        if oh < want:
            continue
        out.append({"store_id": store_id, "on_hand": oh, "can_fulfill": True})
    out.sort(key=lambda r: (-r["on_hand"], str(r["store_id"])))
    return out


# ---------------------------------------------------------------------------
# Errors + DB engine
# ---------------------------------------------------------------------------

COLLECTION = "endless_aisle_requests"
TRANSFERS_COLLECTION = "transfers"
POLICY_ENABLED = "endless_aisle.enabled"
POLICY_ELIGIBLE_STORES = "endless_aisle.eligible_store_ids"


class EndlessAisleError(Exception):
    """A business-rule failure -- carries an HTTP-ish status for the router."""

    def __init__(
        self, message: str, status: int = 400, code: str = "endless_aisle_error"
    ):
        super().__init__(message)
        self.status = status
        self.code = code


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_db(db) -> None:
    if db is None:
        raise EndlessAisleError(
            "endless-aisle store unavailable", status=503, code="no_db"
        )


def ensure_indexes(db) -> None:
    """Idempotent indexes. Fail-soft."""
    if db is None:
        return
    try:
        coll = db.get_collection(COLLECTION)
        coll.create_index([("selling_store_id", 1), ("status", 1)])
        coll.create_index([("source_store_id", 1), ("status", 1)])
    except Exception:  # noqa: BLE001
        return


def open_request(
    db, payload: Dict[str, Any], *, actor: Dict[str, Any], on_hand_resolver
) -> Dict[str, Any]:
    """Open a fulfillment request at PENDING. THE GHOST-STOCK GUARD: re-validate
    the source store's live on-hand at submit time (not just the stale
    availability the manager saw) -- 409 if the source can no longer cover the
    qty. ``on_hand_resolver(product_id, store_id) -> int`` is injected so this
    stays testable. Single insert; the request is the unit of work."""
    _require_db(db)
    product_id = str(payload.get("product_id") or "").strip()
    selling = str(payload.get("selling_store_id") or "").strip()
    source = str(payload.get("source_store_id") or "").strip()
    qty = int(payload.get("qty") or 0)
    if not product_id or not selling or not source:
        raise EndlessAisleError(
            "product_id, selling_store_id, source_store_id are required", status=422
        )
    if qty <= 0:
        raise EndlessAisleError("qty must be > 0", status=422)
    if source == selling:
        raise EndlessAisleError(
            "source store must differ from the selling store", status=422
        )
    live = int(on_hand_resolver(product_id, source) or 0)
    if live < qty:
        raise EndlessAisleError(
            "source store can no longer cover this quantity (stock changed)",
            status=409,
            code="ghost_stock",
        )
    rid = "EAR-" + uuid.uuid4().hex[:10].upper()
    now = _now_iso()
    doc = {
        "_id": rid,
        "request_id": rid,
        "order_id": payload.get("order_id"),
        "product_id": product_id,
        "qty": qty,
        "selling_store_id": selling,
        "source_store_id": source,
        "delivery_address": payload.get("delivery_address"),
        "status": STATUS_PENDING,
        # Company bears shipping -> booked to the SELLING store's P&L; the
        # customer is charged nothing extra and sees no shipping line.
        "shipping_borne_by": "COMPANY",
        "shipping_cost_store_id": selling,
        "customer_shipping_charge_paise": 0,
        "status_history": [
            {"from": None, "to": STATUS_PENDING, "by": actor.get("user_id"), "at": now}
        ],
        "created_by": actor.get("user_id"),
        "created_at": now,
        "updated_at": now,
    }
    db.get_collection(COLLECTION).insert_one(dict(doc))
    return doc


def get_request(db, request_id: str) -> Optional[Dict[str, Any]]:
    if db is None:
        return None
    return db.get_collection(COLLECTION).find_one({"request_id": request_id})


def list_requests(
    db, *, store_id: Optional[str] = None, status: Optional[str] = None
) -> List[Dict[str, Any]]:
    if db is None:
        return []
    query: Dict[str, Any] = {}
    if store_id:
        query["$or"] = [{"selling_store_id": store_id}, {"source_store_id": store_id}]
    if status:
        query["status"] = str(status).upper()
    try:
        return list(db.get_collection(COLLECTION).find(query))
    except Exception:  # noqa: BLE001
        return []


def _guarded_transition(
    db, request_id: str, frm: str, to: str, *, actor, extra=None
) -> Optional[Dict[str, Any]]:
    """Atomic status flip keyed on the current status -- two concurrent
    transitions: exactly one wins (loser's filter no longer matches)."""
    from pymongo import ReturnDocument

    now = _now_iso()
    set_fields = {"status": to, "updated_at": now}
    if extra:
        set_fields.update(extra)
    return db.get_collection(COLLECTION).find_one_and_update(
        {"request_id": request_id, "status": frm},
        {
            "$set": set_fields,
            "$push": {
                "status_history": {
                    "from": frm,
                    "to": to,
                    "by": actor.get("user_id"),
                    "at": now,
                }
            },
        },
        return_document=ReturnDocument.AFTER,
    )


def accept_request(
    db, request_id: str, *, actor: Dict[str, Any], on_hand_resolver
) -> Dict[str, Any]:
    """SOURCE store confirms the unit is real + sellable -> ACCEPTED. Re-validate
    the source on-hand AGAIN here (the ghost-stock window between open and
    accept) -- 409 if it vanished. Guarded PENDING->ACCEPTED (one winner)."""
    _require_db(db)
    req = get_request(db, request_id)
    if req is None:
        raise EndlessAisleError("request not found", status=404, code="not_found")
    if req.get("status") != STATUS_PENDING:
        raise EndlessAisleError(
            "request is not pending", status=409, code="not_pending"
        )
    live = int(on_hand_resolver(req.get("product_id"), req.get("source_store_id")) or 0)
    if live < int(req.get("qty") or 0):
        raise EndlessAisleError(
            "source store can no longer cover this quantity",
            status=409,
            code="ghost_stock",
        )
    updated = _guarded_transition(
        db, request_id, STATUS_PENDING, STATUS_ACCEPTED, actor=actor
    )
    if updated is None:
        raise EndlessAisleError(
            "request changed concurrently", status=409, code="conflict"
        )
    return updated


def reject_request(
    db, request_id: str, *, actor: Dict[str, Any], reason: Optional[str] = None
) -> Dict[str, Any]:
    _require_db(db)
    req = get_request(db, request_id)
    if req is None:
        raise EndlessAisleError("request not found", status=404, code="not_found")
    if req.get("status") != STATUS_PENDING:
        raise EndlessAisleError(
            "request is not pending", status=409, code="not_pending"
        )
    updated = _guarded_transition(
        db,
        request_id,
        STATUS_PENDING,
        STATUS_REJECTED,
        actor=actor,
        extra={"reject_reason": reason},
    )
    if updated is None:
        raise EndlessAisleError(
            "request changed concurrently", status=409, code="conflict"
        )
    return updated


def create_transfer(
    db, request_id: str, *, actor: Dict[str, Any], on_hand_resolver
) -> Dict[str, Any]:
    """On ACCEPTED, create the inter-store transfer (source -> selling) and link
    it. Shipping is booked to the SELLING store (company bears it). Re-validate
    on-hand ONE more time (final ghost-stock guard) before committing. The
    physical stock movement rides the EXISTING transfers lifecycle (ship /
    receive) via the linked transfer doc -- endless-aisle orchestrates + links,
    it does not fork the transfer stock-pinning logic."""
    _require_db(db)
    req = get_request(db, request_id)
    if req is None:
        raise EndlessAisleError("request not found", status=404, code="not_found")
    if req.get("status") != STATUS_ACCEPTED:
        raise EndlessAisleError(
            "request must be ACCEPTED before a transfer is created",
            status=409,
            code="not_accepted",
        )
    live = int(on_hand_resolver(req.get("product_id"), req.get("source_store_id")) or 0)
    if live < int(req.get("qty") or 0):
        raise EndlessAisleError(
            "source store can no longer cover this quantity",
            status=409,
            code="ghost_stock",
        )
    tid = "TRF-EA-" + uuid.uuid4().hex[:8].upper()
    now = _now_iso()
    transfer = {
        "_id": tid,
        "id": tid,
        "transfer_id": tid,
        "transfer_type": "store_to_store",
        "from_location_id": req.get("source_store_id"),
        "to_location_id": req.get("selling_store_id"),
        "status": "pending",
        "items": [
            {"product_id": req.get("product_id"), "quantity": int(req.get("qty") or 0)}
        ],
        "source": "ENDLESS_AISLE",
        "endless_aisle_request_id": request_id,
        # Company-borne shipping: cost books to the SELLING store, NOT the customer.
        "shipping_cost_store_id": req.get("selling_store_id"),
        "shipping_borne_by": "COMPANY",
        "created_by": actor.get("user_id"),
        "created_at": now,
    }
    try:
        db.get_collection(TRANSFERS_COLLECTION).insert_one(dict(transfer))
    except Exception as exc:  # noqa: BLE001
        raise EndlessAisleError(
            "could not create the transfer", status=503, code="transfer_failed"
        ) from exc
    updated = _guarded_transition(
        db,
        request_id,
        STATUS_ACCEPTED,
        STATUS_TRANSFER_CREATED,
        actor=actor,
        extra={"transfer_id": tid},
    )
    if updated is None:
        raise EndlessAisleError(
            "request changed concurrently", status=409, code="conflict"
        )
    return updated


def advance(db, request_id: str, to: str, *, actor: Dict[str, Any]) -> Dict[str, Any]:
    """Advance TRANSFER_CREATED->SHIPPED->DELIVERED (or cancel where legal)."""
    _require_db(db)
    req = get_request(db, request_id)
    if req is None:
        raise EndlessAisleError("request not found", status=404, code="not_found")
    frm = req.get("status")
    to_u = str(to or "").upper()
    if not can_transition(frm, to_u):
        raise EndlessAisleError(
            "illegal transition " + str(frm) + " -> " + to_u,
            status=422,
            code="illegal_transition",
        )
    updated = _guarded_transition(db, request_id, frm, to_u, actor=actor)
    if updated is None:
        raise EndlessAisleError(
            "request changed concurrently", status=409, code="conflict"
        )
    return updated
