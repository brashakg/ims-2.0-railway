"""
IMS 2.0 - N4 Vendor RMA (Return Merchandise Authorization) lifecycle engine
============================================================================
Defective / returnable stock going BACK to a vendor (optical-lens labs --
Essilor / Zeiss / Alifnoor / GKB / Vinod -- Luxottica warranty, Zeiss
replacement) needs a tracked RMA distinct from the lighter RTV/debit-note flow
in ``vendor_returns.py``. N4 grounds roadmap #14 (vendor credit sync) + #20
(RTV debit note) into ONE authorization -> courier -> credit-reconcile machine.

What ``vendor_returns.py`` already covered (NOT re-built here)
-------------------------------------------------------------
  * Raising a return against a vendor with line items + reason.
  * A debit/credit-note NUMBER mint on the ``credit_issued`` transition.
  * F21 physical-unit (QUARANTINED stock_unit) linkage via rtv_vendor_id.
  * The vendor/AP RBAC role set + the store-IDOR guard (validate_store_access).

What N4 ADDS (the RMA-specific gap)
-----------------------------------
  1. The vendor's RMA AUTHORIZATION number (the lab/Luxottica reference the
     goods must travel under) -- recorded at the DRAFT -> AUTHORIZED step.
  2. COURIER dispatch tracking (carrier, AWB/tracking, dispatch date) recorded
     when the goods physically ship back -- AUTHORIZED -> DISPATCHED.
  3. CREDIT-NOTE RECONCILIATION against the RMA: an EXPECTED credit (paisa-exact
     from the line items) vs the SUM of one-or-more RECEIVED vendor credit notes
     (partial credits supported), with a paisa-exact variance. CLOSED only when
     fully reconciled (or force-closed with an audited residual write-off).

Lifecycle (one collection: ``vendor_rmas``)
-------------------------------------------
    DRAFT -> AUTHORIZED -> DISPATCHED -> CREDIT_RECEIVED -> CLOSED
       \\-> REJECTED            (a terminal rejection from any pre-credit state)

Each transition is a SINGLE guarded find_one_and_update on a SINGLE document
whose FILTER encodes the from-state (standalone Mongo -- NO transactions,
mirroring vouchers.redeem_voucher_atomic and approvals.ApprovalEngine). Two
concurrent transitions -> exactly one wins; the loser gets a 409-shaped result.
Every transition appends a hash-chained ``audit_logs`` row via
AuditRepository.create (entity_type "VENDOR_RMA").

Money (CLAUDE.md / SYSTEM_INTENT "paisa-exact"): all amounts are stored as
INTEGER PAISE. Rupee inputs are converted once at the edge (round-half-up to the
nearest paisa). Expected-vs-received variance is integer subtraction -- never a
float drift.

E4 maker-checker seam: a credit-note record whose received amount (or its
variance) crosses a configured tier routes through the E4 ApprovalEngine
(action_type "rtv") -- the caller must hold a consumed approval token. Below the
threshold the credit is recorded directly. This module owns NO threshold
constant -- the tier comes from E2 get_policy (same registry the refund tiers
use), with a safe code fallback.

Conventions (CLAUDE.md): NO emoji (Windows cp1252); ASCII log tag [VENDOR_RMA].
Fail-soft: ``db=None`` => reads empty, writes a structured error, never raises.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ============================================================================
# Status + reason enums
# ============================================================================


class RMAStatus(str, Enum):
    DRAFT = "DRAFT"
    AUTHORIZED = "AUTHORIZED"
    DISPATCHED = "DISPATCHED"
    CREDIT_RECEIVED = "CREDIT_RECEIVED"
    CLOSED = "CLOSED"
    REJECTED = "REJECTED"


# Forward-only transition graph. The from-state is the atomic guard; a
# transition not in this map for the current status is a 409 conflict.
_TRANSITIONS: Dict[str, List[str]] = {
    RMAStatus.DRAFT.value: [RMAStatus.AUTHORIZED.value, RMAStatus.REJECTED.value],
    RMAStatus.AUTHORIZED.value: [RMAStatus.DISPATCHED.value, RMAStatus.REJECTED.value],
    RMAStatus.DISPATCHED.value: [RMAStatus.CREDIT_RECEIVED.value, RMAStatus.REJECTED.value],
    # CREDIT_RECEIVED -> CLOSED is driven by the reconciliation helper (close_rma),
    # not a bare status patch, so the credit math is the gate.
    RMAStatus.CREDIT_RECEIVED.value: [RMAStatus.CLOSED.value],
    RMAStatus.CLOSED.value: [],
    RMAStatus.REJECTED.value: [],
}

# Defect/return reason taxonomy (N4 intent: DEFECTIVE / WRONG / EXCESS / WARRANTY,
# plus the optical-lab non-adapt case which is the dominant Zeiss/Essilor claim).
RMA_REASONS: frozenset = frozenset({
    "DEFECTIVE",
    "WRONG",
    "EXCESS",
    "WARRANTY",
    "NON_ADAPT",
})


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if hasattr(dt, "isoformat"):
        return dt.isoformat()
    return str(dt)


def rupees_to_paise(amount: Any) -> int:
    """Convert a rupee amount (int/float/str/Decimal) to integer paise, rounding
    half-up to the nearest paisa. The single edge-conversion so all downstream
    math is integer-exact. Negative / non-numeric -> 0 (defensive; callers also
    validate at the schema layer)."""
    try:
        d = Decimal(str(amount))
    except Exception:  # noqa: BLE001
        return 0
    if d <= 0:
        return 0
    paise = (d * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(paise)


def paise_to_rupees(paise: Any) -> float:
    """Render integer paise back to a rupee float for API responses ONLY (display
    boundary). Storage + arithmetic always stay in integer paise."""
    try:
        return round(int(paise) / 100.0, 2)
    except Exception:  # noqa: BLE001
        return 0.0


def _line_expected_paise(line: Dict[str, Any]) -> int:
    """Expected credit for one RMA line = qty * unit_cost (paisa-exact).
    ``unit_cost_paise`` is authoritative when present (already integer); else the
    rupee ``unit_cost`` is converted at the edge."""
    qty = int(line.get("quantity") or 0)
    if "unit_cost_paise" in line and line.get("unit_cost_paise") is not None:
        unit = int(line.get("unit_cost_paise") or 0)
    else:
        unit = rupees_to_paise(line.get("unit_cost"))
    if qty <= 0 or unit < 0:
        return 0
    return qty * unit


def expected_credit_paise(lines: List[Dict[str, Any]]) -> int:
    """Sum the per-line expected credit. Integer addition -- no float drift."""
    return sum(_line_expected_paise(ln) for ln in (lines or []))


# ============================================================================
# Engine
# ============================================================================


class VendorRMAEngine:
    """Lifecycle wrapper over the ``vendor_rmas`` collection. Every method is
    fail-soft on ``db is None``; every state change is a single atomic
    find_one_and_update; every transition writes a hash-chained audit row."""

    COLLECTION = "vendor_rmas"
    AUDIT_COLLECTION = "audit_logs"

    def __init__(self, db=None):
        self._db = db

    # --- collection access -------------------------------------------------

    def _coll(self):
        if self._db is None:
            return None
        try:
            return self._db.get_collection(self.COLLECTION)
        except Exception:  # noqa: BLE001
            try:
                return self._db[self.COLLECTION]
            except Exception:  # noqa: BLE001
                return None

    def _audit_coll(self):
        if self._db is None:
            return None
        try:
            return self._db.get_collection(self.AUDIT_COLLECTION)
        except Exception:  # noqa: BLE001
            try:
                return self._db[self.AUDIT_COLLECTION]
            except Exception:  # noqa: BLE001
                return None

    def ensure_indexes(self) -> None:
        """Idempotent index creation. Best-effort; never raises."""
        coll = self._coll()
        if coll is None:
            return
        try:
            coll.create_index("rma_id", unique=True)
            coll.create_index([("store_id", 1), ("status", 1), ("created_at", -1)])
            coll.create_index([("vendor_id", 1), ("status", 1)])
            # P1-1 DB backstop: at most ONE credit note per (rma_id, CN number).
            # A partial index keyed on the embedded credit_notes element so a racing
            # duplicate-CN double-credit is rejected at the storage layer even if the
            # in-filter $ne guard were ever bypassed. partialFilterExpression skips
            # docs with no credit notes yet (the embedded path is absent at DRAFT).
            coll.create_index(
                [("rma_id", 1), ("credit_notes.credit_note_number", 1)],
                unique=True,
                partialFilterExpression={
                    "credit_notes.credit_note_number": {"$exists": True}
                },
            )
        except Exception:  # noqa: BLE001
            logger.debug("[VENDOR_RMA] ensure_indexes skipped", exc_info=True)

    # --- read --------------------------------------------------------------

    def get(self, rma_id: str) -> Optional[Dict[str, Any]]:
        coll = self._coll()
        if coll is None:
            return None
        try:
            return coll.find_one({"rma_id": rma_id}, {"_id": 0})
        except Exception:  # noqa: BLE001
            return None

    def list(
        self,
        *,
        store_id: Optional[str] = None,
        store_ids: Optional[List[str]] = None,
        vendor_id: Optional[str] = None,
        status: Optional[str] = None,
        skip: int = 0,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """List RMAs. ``store_id`` pins a single store; ``store_ids`` (used by the
        router after resolve_store_scope) restricts to a caller's reach. HQ
        callers pass neither and see all."""
        coll = self._coll()
        if coll is None:
            return []
        q: Dict[str, Any] = {}
        if store_id:
            q["store_id"] = store_id
        elif store_ids is not None:
            q["store_id"] = {"$in": list(store_ids)}
        if vendor_id:
            q["vendor_id"] = vendor_id
        if status:
            q["status"] = status
        try:
            rows = list(
                coll.find(q, {"_id": 0})
                .sort("created_at", -1)
                .skip(int(skip))
                .limit(int(limit))
            )
        except Exception:  # noqa: BLE001
            return []
        return rows

    # --- create (DRAFT) ----------------------------------------------------

    def raise_rma(
        self,
        *,
        vendor_id: str,
        vendor_name: str,
        store_id: str,
        lines: List[Dict[str, Any]],
        created_by: str,
        notes: str = "",
        po_id: Optional[str] = None,
        grn_id: Optional[str] = None,
        return_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Open a DRAFT RMA with line items. Computes the paisa-exact expected
        credit at creation. Fail-soft -> {"ok": False, "error": "no_db"}."""
        coll = self._coll()
        if coll is None:
            return {"ok": False, "error": "no_db"}
        if not lines:
            return {"ok": False, "error": "no_lines"}

        norm_lines: List[Dict[str, Any]] = []
        for ln in lines:
            reason = str(ln.get("reason") or "").upper()
            if reason not in RMA_REASONS:
                return {"ok": False, "error": "bad_reason", "reason": reason}
            qty = int(ln.get("quantity") or 0)
            if qty <= 0:
                return {"ok": False, "error": "bad_quantity"}
            unit_paise = (
                int(ln["unit_cost_paise"])
                if ln.get("unit_cost_paise") is not None
                else rupees_to_paise(ln.get("unit_cost"))
            )
            if unit_paise < 0:
                return {"ok": False, "error": "bad_unit_cost"}
            norm_lines.append({
                "product_id": ln.get("product_id"),
                "product_name": ln.get("product_name"),
                "quantity": qty,
                "reason": reason,
                "unit_cost_paise": unit_paise,
                "line_expected_paise": qty * unit_paise,
            })

        exp_paise = sum(ln["line_expected_paise"] for ln in norm_lines)
        now = _now()
        rma_id = f"RMA-{now.strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}"
        doc: Dict[str, Any] = {
            "rma_id": rma_id,
            "vendor_id": vendor_id,
            "vendor_name": vendor_name,
            "store_id": store_id,
            "po_id": po_id,
            "grn_id": grn_id,
            "return_id": return_id,
            "lines": norm_lines,
            "status": RMAStatus.DRAFT.value,
            "expected_credit_paise": exp_paise,
            # Credit reconciliation state (filled at CREDIT_RECEIVED+).
            "credit_notes": [],
            "received_credit_paise": 0,
            "variance_paise": exp_paise,  # expected - received; full expected at draft
            "vendor_rma_number": None,    # vendor's authorization number (set at AUTHORIZE)
            # Courier dispatch (set at DISPATCH).
            "courier": None,
            "notes": notes or "",
            "created_by": created_by,
            "created_at": now,
            "updated_at": now,
            "status_history": [
                {
                    "status": RMAStatus.DRAFT.value,
                    "at": now,
                    "by": created_by,
                    "notes": "RMA raised",
                }
            ],
        }
        try:
            coll.insert_one(doc)
        except Exception as e:  # noqa: BLE001
            logger.warning("[VENDOR_RMA] raise insert failed: %s", e)
            return {"ok": False, "error": "write_failed"}

        self._audit("rma_raised", doc, actor=created_by,
                    before=None, after=self._snapshot(doc))
        logger.info("[VENDOR_RMA] %s DRAFT raised (vendor=%s, expected=%d paise)",
                    rma_id, vendor_id, exp_paise)
        return {"ok": True, "rma_id": rma_id, "status": doc["status"],
                "expected_credit_paise": exp_paise}

    # --- authorize (DRAFT -> AUTHORIZED) -----------------------------------

    def authorize(
        self,
        rma_id: str,
        *,
        vendor_rma_number: str,
        actor: str,
        notes: str = "",
    ) -> Dict[str, Any]:
        """Record the vendor's RMA AUTHORIZATION number and flip DRAFT ->
        AUTHORIZED in a single atomic guarded op. ``vendor_rma_number`` is
        REQUIRED -- goods cannot ship without the lab/Luxottica reference."""
        if not vendor_rma_number or not str(vendor_rma_number).strip():
            return {"ok": False, "http": 400, "error": "vendor_rma_number_required"}
        extra = {"vendor_rma_number": str(vendor_rma_number).strip()}
        return self._transition(
            rma_id,
            from_status=RMAStatus.DRAFT.value,
            to_status=RMAStatus.AUTHORIZED.value,
            actor=actor,
            notes=notes,
            extra_set=extra,
            audit_action="rma_authorized",
        )

    # --- dispatch (AUTHORIZED -> DISPATCHED) -------------------------------

    def dispatch(
        self,
        rma_id: str,
        *,
        carrier: str,
        awb: str,
        dispatch_date: Optional[str],
        actor: str,
        notes: str = "",
    ) -> Dict[str, Any]:
        """Record COURIER dispatch (carrier + AWB/tracking + date) and flip
        AUTHORIZED -> DISPATCHED atomically."""
        if not carrier or not str(carrier).strip():
            return {"ok": False, "http": 400, "error": "carrier_required"}
        if not awb or not str(awb).strip():
            return {"ok": False, "http": 400, "error": "awb_required"}
        courier = {
            "carrier": str(carrier).strip(),
            "awb": str(awb).strip(),
            "dispatch_date": dispatch_date or _iso(_now()),
            "recorded_at": _iso(_now()),
            "recorded_by": actor,
        }
        return self._transition(
            rma_id,
            from_status=RMAStatus.AUTHORIZED.value,
            to_status=RMAStatus.DISPATCHED.value,
            actor=actor,
            notes=notes,
            extra_set={"courier": courier},
            audit_action="rma_dispatched",
        )

    # --- reject (any pre-credit state -> REJECTED) -------------------------

    def reject(self, rma_id: str, *, actor: str, reason: str = "") -> Dict[str, Any]:
        """Reject an RMA from DRAFT / AUTHORIZED / DISPATCHED. The from-state is
        whatever the doc currently holds among those three (atomic guard via
        $in). Terminal."""
        from pymongo import ReturnDocument

        coll = self._coll()
        if coll is None:
            return {"ok": False, "http": 503, "error": "no_db"}
        cur = self.get(rma_id)
        if cur is None:
            return {"ok": False, "http": 404, "error": "not_found"}
        rejectable = [
            RMAStatus.DRAFT.value,
            RMAStatus.AUTHORIZED.value,
            RMAStatus.DISPATCHED.value,
        ]
        now = _now()
        try:
            updated = coll.find_one_and_update(
                {"rma_id": rma_id, "status": {"$in": rejectable}},
                {
                    "$set": {"status": RMAStatus.REJECTED.value, "updated_at": now},
                    "$push": {
                        "status_history": {
                            "status": RMAStatus.REJECTED.value,
                            "at": now,
                            "by": actor,
                            "notes": reason or "",
                        }
                    },
                },
                return_document=ReturnDocument.AFTER,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("[VENDOR_RMA] reject failed for %s: %s", rma_id, e)
            return {"ok": False, "http": 500, "error": "write_failed"}
        if updated is None:
            return self._conflict(rma_id)
        updated.pop("_id", None)
        self._audit("rma_rejected", updated, actor=actor,
                    before=self._snapshot(cur), after=self._snapshot(updated),
                    reason=reason)
        return {"ok": True, "status": updated.get("status"), "rma_id": rma_id}

    # --- credit-note reconciliation ----------------------------------------

    def record_credit_note(
        self,
        rma_id: str,
        *,
        credit_note_number: str,
        received_amount: Any,
        received_amount_paise: Optional[int] = None,
        actor: str,
        notes: str = "",
        approval_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Record a vendor CREDIT NOTE against a DISPATCHED (or already
        CREDIT_RECEIVED, for partials) RMA. Computes expected-vs-received
        variance paisa-exact and appends to ``credit_notes``; the cumulative
        received total + variance are recomputed atomically. The first credit
        note flips DISPATCHED -> CREDIT_RECEIVED; subsequent ones (partial
        credits) keep the status and accumulate.

        ``approval_token`` is the E4-consumed token when the credit (or its
        variance) crossed a tier -- the ROUTER consumes it; this method records
        the reference for the audit trail.
        """
        from pymongo import ReturnDocument

        coll = self._coll()
        if coll is None:
            return {"ok": False, "http": 503, "error": "no_db"}
        if not credit_note_number or not str(credit_note_number).strip():
            return {"ok": False, "http": 400, "error": "credit_note_number_required"}

        cn_number = str(credit_note_number).strip()
        if received_amount_paise is not None:
            recv_paise = int(received_amount_paise)
        else:
            recv_paise = rupees_to_paise(received_amount)
        if recv_paise < 0:
            return {"ok": False, "http": 400, "error": "bad_amount"}

        cur = self.get(rma_id)
        if cur is None:
            return {"ok": False, "http": 404, "error": "not_found"}

        # NOTE (P1-1): the duplicate-credit-note guard is NOT a read-then-check
        # here -- it is enforced atomically inside the find_one_and_update filter
        # below (``credit_notes.credit_note_number {$ne}``) plus the partial UNIQUE
        # index in ensure_indexes, so a racing duplicate cannot slip through.

        # Allowed source states: DISPATCHED (first credit) or CREDIT_RECEIVED
        # (partial follow-ups). Atomic guard encodes this via $in.
        recordable = [RMAStatus.DISPATCHED.value, RMAStatus.CREDIT_RECEIVED.value]
        now = _now()
        cn_entry = {
            "credit_note_number": cn_number,
            "received_paise": recv_paise,
            "recorded_at": now,
            "recorded_by": actor,
            "notes": notes or "",
            "approval_token": approval_token,
        }

        # ATOMIC ACCUMULATION (P1-1). The received total is incremented with
        # ``$inc`` -- NOT a read-then-absolute-$set -- so two concurrent partial
        # credits cannot clobber each other (lost update). The dup-CN guard moves
        # INTO the filter (``credit_notes.credit_note_number {$ne}``) so a racing
        # duplicate note cannot slip past the read-then-check window; a partial
        # UNIQUE index (ensure_indexes) is the DB backstop. The guarded filter
        # therefore encodes BOTH the from-state ($in recordable) AND the dup guard.
        try:
            updated = coll.find_one_and_update(
                {
                    "rma_id": rma_id,
                    "status": {"$in": recordable},
                    "credit_notes.credit_note_number": {"$ne": cn_number},
                },
                {
                    "$set": {
                        "status": RMAStatus.CREDIT_RECEIVED.value,
                        "updated_at": now,
                    },
                    "$inc": {"received_credit_paise": recv_paise},
                    "$push": {
                        "credit_notes": cn_entry,
                        "status_history": {
                            "status": RMAStatus.CREDIT_RECEIVED.value,
                            "at": now,
                            "by": actor,
                            "notes": f"credit note {cn_number} for {recv_paise} paise",
                        },
                    },
                },
                return_document=ReturnDocument.AFTER,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("[VENDOR_RMA] record_credit_note failed for %s: %s", rma_id, e)
            return {"ok": False, "http": 500, "error": "write_failed"}

        if updated is None:
            return self._credit_conflict(rma_id, cn_number)
        updated.pop("_id", None)

        # Derive the variance from the ATOMICALLY-incremented received total and
        # persist it (a CAS keyed on the exact received value we just observed, so
        # a concurrent credit that lands between the $inc and this write recomputes
        # its OWN variance against the latest total -- never a stale overwrite).
        new_received = int(updated.get("received_credit_paise") or 0)
        new_variance = int(updated.get("expected_credit_paise") or 0) - new_received
        try:
            varianced = coll.find_one_and_update(
                {"rma_id": rma_id, "received_credit_paise": new_received},
                {"$set": {"variance_paise": new_variance}},
                return_document=ReturnDocument.AFTER,
            )
            if varianced is not None:
                varianced.pop("_id", None)
                updated = varianced
        except Exception:  # noqa: BLE001 - variance is derivable on read; fail-soft
            updated["variance_paise"] = new_variance

        self._audit("rma_credit_recorded", updated, actor=actor,
                    before=self._snapshot(cur), after=self._snapshot(updated),
                    reason=notes)
        logger.info(
            "[VENDOR_RMA] %s credit note %s recorded (received=%d, total=%d, variance=%d)",
            rma_id, cn_number, recv_paise, new_received, new_variance,
        )
        return {
            "ok": True,
            "rma_id": rma_id,
            "status": updated.get("status"),
            "received_credit_paise": new_received,
            "expected_credit_paise": int(updated.get("expected_credit_paise") or 0),
            "variance_paise": new_variance,
            "fully_reconciled": new_variance <= 0,
        }

    # --- close (CREDIT_RECEIVED -> CLOSED) ---------------------------------

    def close_rma(
        self,
        rma_id: str,
        *,
        actor: str,
        notes: str = "",
        write_off_variance: bool = False,
    ) -> Dict[str, Any]:
        """Close a CREDIT_RECEIVED RMA. Refuses to close with an OUTSTANDING
        positive variance (vendor still owes credit) unless ``write_off_variance``
        is set -- a force-close that audits the residual as a written-off loss.
        A negative variance (vendor over-credited) is allowed to close (it is a
        gain, surfaced for finance). Atomic guard on status==CREDIT_RECEIVED."""
        from pymongo import ReturnDocument

        coll = self._coll()
        if coll is None:
            return {"ok": False, "http": 503, "error": "no_db"}
        cur = self.get(rma_id)
        if cur is None:
            return {"ok": False, "http": 404, "error": "not_found"}
        variance = int(cur.get("variance_paise") or 0)
        if variance > 0 and not write_off_variance:
            return {
                "ok": False,
                "http": 409,
                "error": "variance_outstanding",
                "variance_paise": variance,
            }
        now = _now()
        set_fields: Dict[str, Any] = {
            "status": RMAStatus.CLOSED.value,
            "closed_at": now,
            "closed_by": actor,
            "updated_at": now,
        }
        if variance > 0 and write_off_variance:
            set_fields["written_off_paise"] = variance
        try:
            updated = coll.find_one_and_update(
                {"rma_id": rma_id, "status": RMAStatus.CREDIT_RECEIVED.value},
                {
                    "$set": set_fields,
                    "$push": {
                        "status_history": {
                            "status": RMAStatus.CLOSED.value,
                            "at": now,
                            "by": actor,
                            "notes": notes or (
                                f"closed; wrote off {variance} paise"
                                if (variance > 0 and write_off_variance) else "closed"
                            ),
                        }
                    },
                },
                return_document=ReturnDocument.AFTER,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("[VENDOR_RMA] close failed for %s: %s", rma_id, e)
            return {"ok": False, "http": 500, "error": "write_failed"}
        if updated is None:
            return self._conflict(rma_id)
        updated.pop("_id", None)
        self._audit("rma_closed", updated, actor=actor,
                    before=self._snapshot(cur), after=self._snapshot(updated),
                    reason=notes)
        return {
            "ok": True,
            "rma_id": rma_id,
            "status": updated.get("status"),
            "variance_paise": variance,
            "written_off_paise": variance if (variance > 0 and write_off_variance) else 0,
        }

    # --- generic transition (single atomic guarded find_one_and_update) ----

    def _transition(
        self,
        rma_id: str,
        *,
        from_status: str,
        to_status: str,
        actor: str,
        notes: str,
        extra_set: Optional[Dict[str, Any]] = None,
        audit_action: str,
    ) -> Dict[str, Any]:
        from pymongo import ReturnDocument

        coll = self._coll()
        if coll is None:
            return {"ok": False, "http": 503, "error": "no_db"}
        cur = self.get(rma_id)
        if cur is None:
            return {"ok": False, "http": 404, "error": "not_found"}

        now = _now()
        set_fields: Dict[str, Any] = {"status": to_status, "updated_at": now}
        if extra_set:
            set_fields.update(extra_set)
        try:
            updated = coll.find_one_and_update(
                {"rma_id": rma_id, "status": from_status},
                {
                    "$set": set_fields,
                    "$push": {
                        "status_history": {
                            "status": to_status,
                            "at": now,
                            "by": actor,
                            "notes": notes or "",
                        }
                    },
                },
                return_document=ReturnDocument.AFTER,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("[VENDOR_RMA] transition %s->%s failed for %s: %s",
                           from_status, to_status, rma_id, e)
            return {"ok": False, "http": 500, "error": "write_failed"}
        if updated is None:
            return self._conflict(rma_id)
        updated.pop("_id", None)
        self._audit(audit_action, updated, actor=actor,
                    before=self._snapshot(cur), after=self._snapshot(updated),
                    reason=notes)
        logger.info("[VENDOR_RMA] %s %s -> %s by %s", rma_id, from_status, to_status, actor)
        return {"ok": True, "status": to_status, "rma_id": rma_id}

    def _conflict(self, rma_id: str) -> Dict[str, Any]:
        """The atomic filter matched nothing: disambiguate not-found vs the doc
        having already moved past the expected from-state (lost the race)."""
        doc = self.get(rma_id)
        if doc is None:
            return {"ok": False, "http": 404, "error": "not_found"}
        return {"ok": False, "http": 409, "error": "invalid_transition",
                "status": doc.get("status")}

    def _credit_conflict(self, rma_id: str, cn_number: str) -> Dict[str, Any]:
        """The credit-note atomic filter matched nothing. The filter encodes BOTH
        the from-state ($in recordable) AND the dup-CN guard ($ne cn_number), so a
        miss is one of: not-found (404), this exact CN already recorded (409
        duplicate_credit_note), or the RMA is not in a creditable state (409
        invalid_transition). Read back to disambiguate for a precise message."""
        doc = self.get(rma_id)
        if doc is None:
            return {"ok": False, "http": 404, "error": "not_found"}
        existing = {c.get("credit_note_number") for c in (doc.get("credit_notes") or [])}
        if cn_number in existing:
            return {"ok": False, "http": 409, "error": "duplicate_credit_note"}
        return {"ok": False, "http": 409, "error": "invalid_transition",
                "status": doc.get("status")}

    # --- audit -------------------------------------------------------------

    @staticmethod
    def _snapshot(doc: Dict[str, Any]) -> Dict[str, Any]:
        """Compact, money-bearing snapshot for the audit before/after states."""
        return {
            "rma_id": doc.get("rma_id"),
            "vendor_id": doc.get("vendor_id"),
            "store_id": doc.get("store_id"),
            "status": doc.get("status"),
            "expected_credit_paise": doc.get("expected_credit_paise"),
            "received_credit_paise": doc.get("received_credit_paise"),
            "variance_paise": doc.get("variance_paise"),
            "vendor_rma_number": doc.get("vendor_rma_number"),
        }

    def _audit_repo(self):
        coll = self._audit_coll()
        if coll is not None:
            try:
                from database.repositories.audit_repository import AuditRepository

                return AuditRepository(coll)
            except Exception as e:  # noqa: BLE001
                logger.debug("[VENDOR_RMA] AuditRepository build failed: %s", e)
        try:
            from api.dependencies import get_audit_repository

            return get_audit_repository()
        except Exception as e:  # noqa: BLE001
            logger.debug("[VENDOR_RMA] get_audit_repository unavailable: %s", e)
            return None

    def _audit(
        self,
        action: str,
        doc: Dict[str, Any],
        *,
        actor: str,
        before: Optional[Dict[str, Any]],
        after: Optional[Dict[str, Any]],
        reason: str = "",
    ) -> Optional[str]:
        """Append a hash-chained ``VENDOR_RMA`` audit row. Fail-soft -> None; the
        lifecycle transition still stands."""
        repo = self._audit_repo()
        if repo is None:
            return None
        log_id = f"AUD-{uuid.uuid4().hex[:12]}"
        row = {
            "log_id": log_id,
            "action": action,
            "entity_type": "VENDOR_RMA",
            "entity_id": doc.get("rma_id"),
            "store_id": doc.get("store_id"),
            "user_id": actor,
            "actor": actor,
            "source": "VENDOR_RMA",
            "before_state": before,
            "after_state": after,
            "reason": reason or None,
            "severity": "INFO",
            "timestamp": _now(),
        }
        try:
            created = repo.create(row)
        except Exception as e:  # noqa: BLE001
            logger.warning("[VENDOR_RMA] audit write failed for %s: %s",
                           doc.get("rma_id"), e)
            return None
        if created is None:
            return None
        return created.get("log_id", log_id)


# ============================================================================
# E4 tier seam (read-only; the router decides whether to route to maker-checker)
# ============================================================================


def credit_requires_approval(
    received_paise: int,
    variance_paise: int,
    *,
    store_id: Optional[str] = None,
) -> bool:
    """Does recording this credit need an E4 maker-checker approval?

    A credit (or a credit whose variance is large) is a financial event against
    a vendor. We route to E4 when EITHER the received credit amount OR the
    absolute variance crosses the E2 ``rtv.credit.approval_above`` threshold
    (paisa-integers). This module owns NO threshold constant -- E2's registry
    default is the fallback (Rs 50,000 = 5,000,000 paise), matching the refund
    super-tier order of magnitude for a single vendor credit.

    Fail-soft: any policy-read error keeps the locked default.
    """
    default_paise = 5_000_000  # Rs 50,000
    threshold = default_paise
    try:
        from api.services.policy_engine import get_policy

        scope = {"store_id": store_id} if store_id else None
        threshold = int(get_policy("rtv.credit.approval_above", scope, default=default_paise))
    except Exception:  # noqa: BLE001
        threshold = default_paise
    return abs(int(received_paise)) >= threshold or abs(int(variance_paise)) >= threshold
