"""
IMS 2.0 - AI Change-Proposal Workflow
=======================================
Implements SYSTEM_INTENT.md section 8's loop:

    AI generates a suggestion
      -> Superadmin reviews
      -> approve / reject
      -> on approve, the system AUTO-EXECUTES it ONLY IF the proposal type
         is in a reversible Tier-1 whitelist (otherwise it stays advisory)
      -> full immutable before/after audit.

This is also the sanctioned RESOLUTION of the TASKMASTER vs SYSTEM_INTENT
section 8 contradiction. TASKMASTER's three-tier model already says Tier-1
(reversible, low-stakes) acts may auto-execute. SYSTEM_INTENT section 8 said
"NO auto-execution". The product owner chose "auto-execute reversible only":
a Superadmin must still approve, and only the reversible Tier-1 set ever
runs without a human doing the work by hand.

Design
------
- ``ai_proposals`` MongoDB collection is the queue + the audit ledger of
  proposals. Each row is a single suggestion with a lifecycle status.
- ``REVERSIBLE_TYPES`` mirrors TASKMASTER's Tier-1 reversible set. Only
  these auto-execute on approval. Everything else is ADVISORY: approving
  records the approval but a human still performs the change.
- The execution dispatcher reuses EXISTING domain repos/services (we do
  NOT duplicate business logic), captures before_state / after_state, and
  writes an immutable ``audit_logs`` entry.

Conventions (CLAUDE.md)
-----------------------
- NO emojis (Windows cp1252). ASCII log tag ``[PROPOSALS]``.
- Fail-soft everywhere: a missing DB or a failing executor never raises out
  of the public surface; the proposal just lands in FAILED with the error
  captured. A fresh Railway deploy with no data does nothing harmful.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional
import logging
import uuid

logger = logging.getLogger(__name__)


# ============================================================================
# Status + reversible whitelist
# ============================================================================


class ProposalStatus(str, Enum):
    PENDING = "PENDING"      # awaiting Superadmin review
    APPROVED = "APPROVED"    # approved, advisory only (a human acts)
    REJECTED = "REJECTED"    # Superadmin declined
    EXECUTED = "EXECUTED"    # approved AND auto-executed (reversible)
    FAILED = "FAILED"        # approved + reversible, but execution errored


# Reversible Tier-1 proposal types. Approving one of THESE auto-executes the
# change. Mirrors TASKMASTER's "auto-act" tier: low-stakes, fully reversible.
#
#   draft_po                       -> create a DRAFT purchase order (NOT sent
#                                     to the vendor; sending is a separate,
#                                     non-reversible step gated elsewhere)
#   inter_store_transfer_suggestion-> record a DRAFT transfer suggestion
#   rx_reminder                    -> queue a prescription-expiry reminder
#   mark_task                      -> create/flag a follow-up task
#
# Anything NOT in this set (price_ceiling_change, refund, writeoff,
# staff_transfer, po_send, ...) is ADVISORY: approval is recorded and
# audited, but the system does NOT act. A human performs the change.
REVERSIBLE_TYPES: frozenset = frozenset({
    "draft_po",
    "inter_store_transfer_suggestion",
    "rx_reminder",
    "mark_task",
})


def is_reversible(proposal_type: str) -> bool:
    """True if approving this proposal type auto-executes (Tier-1 reversible)."""
    return proposal_type in REVERSIBLE_TYPES


# ============================================================================
# Store + lifecycle
# ============================================================================


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if hasattr(dt, "isoformat"):
        return dt.isoformat()
    return str(dt)


class ProposalStore:
    """
    Thin data-access + lifecycle wrapper over the ``ai_proposals`` collection.

    Every method is fail-soft: with ``db is None`` (no Mongo) reads return
    empty and writes are no-ops, so unit tests and a cold Railway boot don't
    crash. The store does NOT own business logic - execution is delegated to
    the registered executors in this module which reuse domain repos.
    """

    COLLECTION = "ai_proposals"
    AUDIT_COLLECTION = "audit_logs"

    def __init__(self, db=None):
        self._db = db

    # --- collection access -------------------------------------------------

    def _coll(self):
        if self._db is None:
            return None
        try:
            return self._db.get_collection(self.COLLECTION)
        except Exception:
            try:
                return getattr(self._db, self.COLLECTION, None)
            except Exception:
                return None

    def _audit_coll(self):
        if self._db is None:
            return None
        try:
            return self._db.get_collection(self.AUDIT_COLLECTION)
        except Exception:
            try:
                return getattr(self._db, self.AUDIT_COLLECTION, None)
            except Exception:
                return None

    # --- create ------------------------------------------------------------

    def create(
        self,
        *,
        created_by_agent: str,
        proposal_type: str,
        title: str,
        rationale: str,
        payload: Optional[Dict[str, Any]] = None,
        before_state: Optional[Dict[str, Any]] = None,
        dedupe_key: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Enqueue a new PENDING proposal. Used by agents (ORACLE / TASKMASTER)
        to surface a suggestion for Superadmin review INSTEAD of silently
        acting.

        ``dedupe_key`` (optional): if a PENDING proposal with the same
        dedupe_key already exists, the existing one is returned rather than
        inserting a duplicate. This keeps the hourly ORACLE tick from piling
        up identical draft-PO suggestions for the same SKU.

        Returns the stored proposal doc (without Mongo ``_id``) or None when
        no DB is available.
        """
        coll = self._coll()
        if coll is None:
            logger.debug("[PROPOSALS] create skipped - no DB (type=%s)", proposal_type)
            return None

        # De-dupe: don't stack identical pending suggestions.
        if dedupe_key:
            try:
                existing = coll.find_one(
                    {"dedupe_key": dedupe_key, "status": ProposalStatus.PENDING.value},
                    {"_id": 0},
                )
                if existing:
                    return existing
            except Exception as e:
                logger.debug("[PROPOSALS] dedupe lookup failed: %s", e)

        doc: Dict[str, Any] = {
            "proposal_id": f"PROP-{uuid.uuid4().hex[:12]}",
            "created_by_agent": created_by_agent,
            "type": proposal_type,
            "title": title,
            "rationale": rationale,
            "payload": payload or {},
            "status": ProposalStatus.PENDING.value,
            "reversible": is_reversible(proposal_type),
            "created_at": _now(),
            "reviewed_by": None,
            "reviewed_at": None,
            "reject_reason": None,
            "before_state": before_state,
            "after_state": None,
            "execution_error": None,
            "audit_log_id": None,
        }
        if dedupe_key:
            doc["dedupe_key"] = dedupe_key

        try:
            coll.insert_one(doc)
            logger.info(
                "[PROPOSALS] enqueued %s (%s) by %s - reversible=%s",
                doc["proposal_id"], proposal_type, created_by_agent, doc["reversible"],
            )
        except Exception as e:
            logger.warning("[PROPOSALS] insert failed (type=%s): %s", proposal_type, e)
            return None

        # Strip Mongo's injected _id for a clean return / JSON shape.
        doc.pop("_id", None)
        return doc

    # --- read --------------------------------------------------------------

    def get(self, proposal_id: str) -> Optional[Dict[str, Any]]:
        coll = self._coll()
        if coll is None:
            return None
        try:
            return coll.find_one({"proposal_id": proposal_id}, {"_id": 0})
        except Exception as e:
            logger.debug("[PROPOSALS] get failed: %s", e)
            return None

    def list(
        self,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        coll = self._coll()
        if coll is None:
            return []
        q: Dict[str, Any] = {}
        if status:
            q["status"] = status
        try:
            rows = list(
                coll.find(q, {"_id": 0}).sort("created_at", -1).limit(int(limit))
            )
        except Exception as e:
            logger.debug("[PROPOSALS] list failed: %s", e)
            return []
        for r in rows:
            r["created_at"] = _iso(r.get("created_at"))
            r["reviewed_at"] = _iso(r.get("reviewed_at"))
        return rows

    # --- approve / reject --------------------------------------------------

    def approve(
        self,
        proposal_id: str,
        *,
        reviewed_by: str,
    ) -> Dict[str, Any]:
        """
        Approve a PENDING proposal.

        - Reversible (Tier-1)  -> run the executor; capture before/after;
          write an immutable audit_logs row; set status EXECUTED (or FAILED
          if the executor raised - fail-soft, never crashes the request).
        - Not reversible       -> ADVISORY: record the approval + an audit
          row, set status APPROVED, do NOT auto-execute (a human acts).

        Returns the updated proposal doc plus an ``executed`` flag and
        (when reversible) an ``execution`` result block.
        """
        proposal = self.get(proposal_id)
        if proposal is None:
            return {"ok": False, "error": "not_found"}

        if proposal.get("status") != ProposalStatus.PENDING.value:
            return {
                "ok": False,
                "error": "not_pending",
                "status": proposal.get("status"),
            }

        reversible = bool(proposal.get("reversible"))
        ptype = proposal.get("type", "")
        now = _now()

        if not reversible:
            # ADVISORY path - record approval, audit it, but DO NOT execute.
            audit_id = self._write_audit(
                action="ai_proposal_approved_advisory",
                proposal=proposal,
                reviewed_by=reviewed_by,
                before_state=proposal.get("before_state"),
                after_state=None,
                executed=False,
            )
            self._set(
                proposal_id,
                {
                    "status": ProposalStatus.APPROVED.value,
                    "reviewed_by": reviewed_by,
                    "reviewed_at": now,
                    "audit_log_id": audit_id,
                },
            )
            updated = self.get(proposal_id) or proposal
            logger.info(
                "[PROPOSALS] %s APPROVED (advisory, type=%s) by %s",
                proposal_id, ptype, reviewed_by,
            )
            return {
                "ok": True,
                "executed": False,
                "advisory": True,
                "proposal": _jsonable(updated),
            }

        # REVERSIBLE path - execute via a domain executor, fail-soft.
        executor = _EXECUTORS.get(ptype)
        before_state = proposal.get("before_state")
        after_state: Optional[Dict[str, Any]] = None
        exec_error: Optional[str] = None
        exec_result: Dict[str, Any] = {}

        if executor is None:
            # Whitelisted as reversible but no executor wired - treat as a
            # soft failure rather than silently marking EXECUTED with no work.
            exec_error = f"no executor registered for reversible type '{ptype}'"
        else:
            try:
                exec_result = executor(self._db, proposal) or {}
                before_state = exec_result.get("before_state", before_state)
                after_state = exec_result.get("after_state")
            except Exception as e:  # fail-soft: never crash the approve call
                exec_error = f"{type(e).__name__}: {e}"
                logger.warning(
                    "[PROPOSALS] executor for %s (%s) raised: %s",
                    proposal_id, ptype, e,
                )

        if exec_error is None:
            audit_id = self._write_audit(
                action="ai_proposal_executed",
                proposal=proposal,
                reviewed_by=reviewed_by,
                before_state=before_state,
                after_state=after_state,
                executed=True,
            )
            self._set(
                proposal_id,
                {
                    "status": ProposalStatus.EXECUTED.value,
                    "reviewed_by": reviewed_by,
                    "reviewed_at": now,
                    "before_state": before_state,
                    "after_state": after_state,
                    "audit_log_id": audit_id,
                    "execution_error": None,
                },
            )
            updated = self.get(proposal_id) or proposal
            logger.info(
                "[PROPOSALS] %s EXECUTED (type=%s) by %s",
                proposal_id, ptype, reviewed_by,
            )
            return {
                "ok": True,
                "executed": True,
                "advisory": False,
                "execution": exec_result,
                "proposal": _jsonable(updated),
            }

        # Execution failed - capture FAILED + still audit the attempt.
        audit_id = self._write_audit(
            action="ai_proposal_execution_failed",
            proposal=proposal,
            reviewed_by=reviewed_by,
            before_state=before_state,
            after_state=None,
            executed=False,
            error=exec_error,
        )
        self._set(
            proposal_id,
            {
                "status": ProposalStatus.FAILED.value,
                "reviewed_by": reviewed_by,
                "reviewed_at": now,
                "execution_error": exec_error,
                "audit_log_id": audit_id,
            },
        )
        updated = self.get(proposal_id) or proposal
        return {
            "ok": True,
            "executed": False,
            "advisory": False,
            "error": exec_error,
            "proposal": _jsonable(updated),
        }

    def reject(
        self,
        proposal_id: str,
        *,
        reviewed_by: str,
        reason: str = "",
    ) -> Dict[str, Any]:
        """Decline a PENDING proposal. Records reason + audits the decision."""
        proposal = self.get(proposal_id)
        if proposal is None:
            return {"ok": False, "error": "not_found"}
        if proposal.get("status") != ProposalStatus.PENDING.value:
            return {
                "ok": False,
                "error": "not_pending",
                "status": proposal.get("status"),
            }

        audit_id = self._write_audit(
            action="ai_proposal_rejected",
            proposal=proposal,
            reviewed_by=reviewed_by,
            before_state=proposal.get("before_state"),
            after_state=None,
            executed=False,
            reason=reason,
        )
        self._set(
            proposal_id,
            {
                "status": ProposalStatus.REJECTED.value,
                "reviewed_by": reviewed_by,
                "reviewed_at": _now(),
                "reject_reason": reason,
                "audit_log_id": audit_id,
            },
        )
        updated = self.get(proposal_id) or proposal
        logger.info("[PROPOSALS] %s REJECTED by %s", proposal_id, reviewed_by)
        return {"ok": True, "proposal": _jsonable(updated)}

    # --- internals ---------------------------------------------------------

    def _set(self, proposal_id: str, fields: Dict[str, Any]) -> None:
        coll = self._coll()
        if coll is None:
            return
        try:
            coll.update_one({"proposal_id": proposal_id}, {"$set": fields})
        except Exception as e:
            logger.warning("[PROPOSALS] update failed for %s: %s", proposal_id, e)

    def _audit_repo(self):
        """Resolve the hash-chaining AuditRepository for ``audit_logs``.

        Bind to THIS store's own db (the one handed to ProposalStore) so the
        audit row lands in the same database as the proposal -- in production
        that's the live/seeded DB (the router builds ProposalStore over
        get_seeded_db()), and in unit tests it's the in-memory fake, keeping
        writes isolated. Wrapping that collection in AuditRepository routes the
        write through AuditRepository.create -> audit_chain.append_audit_entry,
        the SAME chained path GET /api/v1/audit/verify walks.

        Only when this store has no db at all do we fall back to the app-level
        get_audit_repository() dependency. Returns None when there is genuinely
        no audit collection to write to.
        """
        coll = self._audit_coll()
        if coll is not None:
            try:
                from database.repositories.audit_repository import AuditRepository

                return AuditRepository(coll)
            except Exception as e:  # noqa: BLE001 - fall through to the dependency
                logger.debug("[PROPOSALS] AuditRepository build failed: %s", e)
        try:
            from api.dependencies import get_audit_repository

            return get_audit_repository()
        except Exception as e:  # noqa: BLE001
            logger.debug("[PROPOSALS] get_audit_repository unavailable: %s", e)
            return None

    def _write_audit(
        self,
        *,
        action: str,
        proposal: Dict[str, Any],
        reviewed_by: str,
        before_state: Optional[Dict[str, Any]],
        after_state: Optional[Dict[str, Any]],
        executed: bool,
        reason: str = "",
        error: Optional[str] = None,
    ) -> Optional[str]:
        """
        Write an IMMUTABLE, HASH-CHAINED row to the shared ``audit_logs``
        collection. Per SYSTEM_INTENT 'Audit Everything' + 'Delete audit logs =
        forbidden': this is an append-only record of who approved/rejected what,
        when, and the old vs new state.

        Routed through AuditRepository.create() (NOT a raw insert_one) so the
        row is stamped with seq / prev_hash / entry_hash by the tamper-evident
        chain (database/repositories/audit_chain.py). before_state/after_state
        are in HASHED_FIELDS, so the captured change is committed to the hash
        and any post-hoc edit surfaces at GET /api/v1/audit/verify.

        Fail-soft: returns None on any error; the lifecycle transition still
        happens (we never block an approval because the audit write hiccuped,
        but we DO log loudly so the gap is visible).
        """
        repo = self._audit_repo()
        if repo is None:
            return None
        log_id = f"AUD-{uuid.uuid4().hex[:12]}"
        doc = {
            "log_id": log_id,
            "action": action,
            "entity_type": "ai_proposal",
            "entity_id": proposal.get("proposal_id"),
            "user_id": reviewed_by,
            "actor": reviewed_by,
            "source": "JARVIS_PROPOSAL",
            "agent_id": proposal.get("created_by_agent"),
            "proposal_type": proposal.get("type"),
            "reversible": bool(proposal.get("reversible")),
            "executed": executed,
            "before_state": before_state,
            "after_state": after_state,
            "reason": reason or None,
            "error": error,
            "severity": "WARNING" if error else "INFO",
            "timestamp": _now(),
        }
        try:
            created = repo.create(doc)
        except Exception as e:  # noqa: BLE001 - never block the lifecycle
            logger.warning("[PROPOSALS] audit write failed for %s: %s",
                           proposal.get("proposal_id"), e)
            return None
        if created is None:
            logger.warning(
                "[PROPOSALS] audit write returned no row for %s",
                proposal.get("proposal_id"),
            )
            return None
        # AuditRepository.create stamps/returns the row; trust its log_id.
        return created.get("log_id", log_id)


def _jsonable(proposal: Dict[str, Any]) -> Dict[str, Any]:
    """Coerce datetimes to ISO strings for a clean API/return payload."""
    out = dict(proposal)
    out.pop("_id", None)
    for k in ("created_at", "reviewed_at"):
        if k in out:
            out[k] = _iso(out[k])
    return out


# ============================================================================
# Executors - reuse EXISTING domain repos / services. Do NOT duplicate logic.
# ============================================================================
#
# Each executor takes (db, proposal) and returns a dict that SHOULD include
# ``before_state`` and ``after_state`` so the audit row captures the change.
# Executors must be safe to call with a real Mongo handle; they fail by
# raising, which the approve() path catches and turns into status=FAILED.


def _exec_draft_po(db, proposal: Dict[str, Any]) -> Dict[str, Any]:
    """
    Reversible: create a DRAFT purchase order (NOT sent to the vendor).

    Reuses the SAME ``purchase_orders`` collection + DRAFT shape the rest of
    the system uses (vendors.create_po, TASKMASTER._draft_reorders). Sending
    the PO to the vendor is a SEPARATE, non-reversible action gated by the
    /vendors '.../send' endpoint and is never auto-executed here.

    payload: { sku, quantity, vendor_id?, reorder_point?, on_hand? }
    """
    if db is None:
        raise RuntimeError("database unavailable")
    payload = proposal.get("payload") or {}
    sku = payload.get("sku")
    if not sku:
        raise ValueError("draft_po payload missing 'sku'")
    qty = int(payload.get("quantity") or 1)

    try:
        po_coll = db.get_collection("purchase_orders")
    except Exception as e:
        raise RuntimeError(f"purchase_orders collection unavailable: {e}")
    if po_coll is None:
        raise RuntimeError("purchase_orders collection unavailable")

    po_number = (
        f"PO-PROP-{datetime.now(timezone.utc).strftime('%y%m%d-%H%M%S')}"
        f"-{str(sku)[:6]}"
    )
    po_doc = {
        "po_id": uuid.uuid4().hex,
        "po_number": po_number,
        "sku": sku,
        "vendor_id": payload.get("vendor_id"),
        "quantity": qty,
        "status": "DRAFT",                       # DRAFT only - not sent
        "auto_drafted_by": proposal.get("created_by_agent"),
        "from_proposal_id": proposal.get("proposal_id"),
        "requires_approval": True,               # sending still needs a human
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    po_coll.insert_one(po_doc)
    return {
        "before_state": {
            "sku": sku,
            "on_hand": payload.get("on_hand"),
            "reorder_point": payload.get("reorder_point"),
            "draft_po": None,
        },
        "after_state": {
            "po_number": po_number,
            "po_status": "DRAFT",
            "quantity": qty,
        },
        "po_number": po_number,
    }


def _exec_mark_task(db, proposal: Dict[str, Any]) -> Dict[str, Any]:
    """
    Reversible: create a follow-up task in the ``tasks`` collection.
    Reuses the same task doc shape TASKMASTER uses for advisory tasks.

    payload: { title?, description?, priority?, assigned_to?, store_id? }
    """
    if db is None:
        raise RuntimeError("database unavailable")
    payload = proposal.get("payload") or {}
    try:
        coll = db.get_collection("tasks")
    except Exception as e:
        raise RuntimeError(f"tasks collection unavailable: {e}")
    if coll is None:
        raise RuntimeError("tasks collection unavailable")

    now = datetime.now(timezone.utc)
    task_id = f"TSK-PROP-{now.strftime('%y%m%d-%H%M%S')}"
    task_doc = {
        "task_id": task_id,
        "title": payload.get("title") or proposal.get("title") or "Follow-up",
        "description": payload.get("description") or proposal.get("rationale") or "",
        "category": payload.get("category") or "Review",
        "priority": payload.get("priority") or "P2",
        "status": "OPEN",
        "source": "JARVIS_PROPOSAL",
        "assigned_to": payload.get("assigned_to"),
        "store_id": payload.get("store_id"),
        "from_proposal_id": proposal.get("proposal_id"),
        "created_at": now,
        "updated_at": now,
        "escalation_level": 0,
    }
    coll.insert_one(task_doc)
    return {
        "before_state": {"task": None},
        "after_state": {"task_id": task_id, "status": "OPEN"},
        "task_id": task_id,
    }


def _exec_rx_reminder(db, proposal: Dict[str, Any]) -> Dict[str, Any]:
    """
    Reversible: QUEUE an Rx-expiry reminder (PENDING, not dispatched).

    We deliberately only ENQUEUE a notification row - actually sending the
    WhatsApp/SMS is MEGAPHONE's job and is gated by DISPATCH_MODE. Queuing is
    reversible (delete the row); sending is not. Reuses ``notification_logs``.

    payload: { customer_id?, customer_phone?, customer_name?, message? }
    """
    if db is None:
        raise RuntimeError("database unavailable")
    payload = proposal.get("payload") or {}
    try:
        coll = db.get_collection("notification_logs")
    except Exception as e:
        raise RuntimeError(f"notification_logs collection unavailable: {e}")
    if coll is None:
        raise RuntimeError("notification_logs collection unavailable")

    now = datetime.now(timezone.utc)
    notif_id = f"NTF-PROP-{uuid.uuid4().hex[:10]}"
    notif_doc = {
        "notification_id": notif_id,
        "kind": "rx_reminder",
        "channel": payload.get("channel") or "whatsapp",
        "customer_id": payload.get("customer_id"),
        "customer_phone": payload.get("customer_phone"),
        "customer_name": payload.get("customer_name"),
        "message": payload.get("message") or proposal.get("rationale") or "",
        "status": "PENDING",                     # queued, NOT sent
        "source": "JARVIS_PROPOSAL",
        "from_proposal_id": proposal.get("proposal_id"),
        "created_at": now,
    }
    coll.insert_one(notif_doc)
    return {
        "before_state": {"notification": None},
        "after_state": {"notification_id": notif_id, "status": "PENDING"},
        "notification_id": notif_id,
    }


def _exec_inter_store_transfer_suggestion(db, proposal: Dict[str, Any]) -> Dict[str, Any]:
    """
    Reversible: record a DRAFT inter-store transfer suggestion.

    Writes a DRAFT row to ``stock_transfers`` (no stock is moved - that
    happens only when a human dispatches the transfer, which removes
    barcodes per SYSTEM_INTENT section 5 and is not auto-executed).

    payload: { sku, quantity, from_store_id, to_store_id }
    """
    if db is None:
        raise RuntimeError("database unavailable")
    payload = proposal.get("payload") or {}
    sku = payload.get("sku")
    if not sku:
        raise ValueError("inter_store_transfer_suggestion payload missing 'sku'")
    try:
        coll = db.get_collection("stock_transfers")
    except Exception as e:
        raise RuntimeError(f"stock_transfers collection unavailable: {e}")
    if coll is None:
        raise RuntimeError("stock_transfers collection unavailable")

    now = datetime.now(timezone.utc)
    transfer_id = f"TRF-PROP-{uuid.uuid4().hex[:10]}"
    doc = {
        "transfer_id": transfer_id,
        "sku": sku,
        "quantity": int(payload.get("quantity") or 1),
        "from_store_id": payload.get("from_store_id"),
        "to_store_id": payload.get("to_store_id"),
        "status": "DRAFT",                       # suggestion only
        "source": "JARVIS_PROPOSAL",
        "from_proposal_id": proposal.get("proposal_id"),
        "created_at": now,
    }
    coll.insert_one(doc)
    return {
        "before_state": {"transfer": None},
        "after_state": {"transfer_id": transfer_id, "status": "DRAFT"},
        "transfer_id": transfer_id,
    }


# Executor registry - keyed by proposal type. Keys MUST be a subset of
# REVERSIBLE_TYPES; a reversible type with no executor lands the approval in
# FAILED (see approve()), which is the safe outcome.
_EXECUTORS: Dict[str, Callable[[Any, Dict[str, Any]], Dict[str, Any]]] = {
    "draft_po": _exec_draft_po,
    "mark_task": _exec_mark_task,
    "rx_reminder": _exec_rx_reminder,
    "inter_store_transfer_suggestion": _exec_inter_store_transfer_suggestion,
}


# ============================================================================
# Agent-facing convenience helper
# ============================================================================


def create_proposal(
    db,
    *,
    created_by_agent: str,
    proposal_type: str,
    title: str,
    rationale: str,
    payload: Optional[Dict[str, Any]] = None,
    before_state: Optional[Dict[str, Any]] = None,
    dedupe_key: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Module-level helper so agents (ORACLE / TASKMASTER) can enqueue a
    proposal in one call instead of constructing a ProposalStore:

        from agents.proposals import create_proposal
        create_proposal(self.db, created_by_agent=self.agent_id,
                        proposal_type="draft_po", title=..., rationale=...,
                        payload={...}, dedupe_key=...)

    Fail-soft: returns None when no DB. Never raises.
    """
    try:
        return ProposalStore(db=db).create(
            created_by_agent=created_by_agent,
            proposal_type=proposal_type,
            title=title,
            rationale=rationale,
            payload=payload,
            before_state=before_state,
            dedupe_key=dedupe_key,
        )
    except Exception as e:  # pragma: no cover - defensive, never crash a tick
        logger.warning("[PROPOSALS] create_proposal helper failed: %s", e)
        return None
