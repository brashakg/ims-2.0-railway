"""
IMS 2.0 - F17/#17 Petty cash float controls
============================================
Per-store petty-cash float management: open a float, top it up, record a payout
(an APPROVED petty-cash expense debits the float), reverse a payout when an
expense is rejected, and read the current balance + recent ledger.

Money safety (CORRECTIONS P0-1 / R1): this is standalone Mongo -> NO multi-
document transactions. Every balance mutation is a SINGLE guarded
find_one_and_update on ONE document in the `petty_cash_floats` collection (one
doc per store). The float is a registered E1 ``money_guard`` account type
(PETTY_CASH); this module is a THIN facade over ``money_guard.credit`` /
``money_guard.debit`` / ``money_guard.get_balance`` -- the atomic guard is NOT
re-implemented here. The debit floor (balance >= amount) lives in the
money_guard FILTER, so two racing payouts can never both win and the balance can
never go negative (mirrors vouchers.redeem_voucher_atomic).

The over-threshold approval gate is the shared E4 engine
(``api.services.approvals.ApprovalEngine``, action_type="petty_cash" -- already
in E4's ACTION_TYPES). It is NOT forked here; the expenses router drives the
request/approve/consume lifecycle and only calls debit_float once the approval is
consumed.

Config (float cap, approval threshold, low-balance alert) resolves via E2
``policy_engine.get_policy`` with safe code defaults, so a fresh DB / missing E2
key never blocks the feature.

Audit: money_guard already writes one append-only ``audit_logs`` row per
credit/debit via AuditRepository.create. This module adds a petty-cash-specific
audit row (action="petty_cash.float_open|topup|payout|reversal") so float
reconciliation has an explicit forensic trail. NO emoji (Windows cp1252); ASCII
log tag [PETTYCASH]. Fail-soft: db=None => no-op writes, never raises.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional

from . import money_guard as mg

logger = logging.getLogger(__name__)

COLLECTION = "petty_cash_floats"
ACCOUNT_TYPE = "PETTY_CASH"

# Float status enum -- explicit strings, never colour flags (DECISIONS).
STATUS_ACTIVE = "ACTIVE"
STATUS_FROZEN = "FROZEN"
STATUS_CLOSED = "CLOSED"

# E2-configurable defaults (rupees). Resolved per store via get_policy with these
# as the code fallback so a fresh DB / no-E2 deploy still works.
_DEFAULT_FLOAT_LIMIT = 5000.0
_DEFAULT_LOW_BALANCE_THRESHOLD = 500.0
# Auto-approval threshold: a petty-cash payout strictly above this routes through
# an E4 PIN-gated approval before the float is debited (DECISIONS sec 6 default).
_DEFAULT_AUTO_THRESHOLD = 500.0
# Receipt is mandatory for any petty-cash claim strictly above this (rupees).
_RECEIPT_REQUIRED_ABOVE = 200.0

# The low-balance event emitted on the agent bus when a payout drops the float
# below its threshold. SENTINEL / TASKMASTER subscribe to alert the manager.
LOW_BALANCE_EVENT = "cash.float.low"


def _now_iso() -> str:
    """IST-naive ISO timestamp for the petty-cash ledger (matches money_guard)."""
    try:
        from ..utils.ist import now_ist_naive

        return now_ist_naive().isoformat()
    except Exception:  # noqa: BLE001
        from datetime import datetime

        return datetime.now().isoformat()


def _coll(db):
    """Resolve the petty_cash_floats collection from a DB handle. None when no DB."""
    if db is None:
        return None
    getter = getattr(db, "get_collection", None)
    if callable(getter):
        try:
            return getter(COLLECTION)
        except Exception:  # noqa: BLE001
            return None
    try:
        return db[COLLECTION]
    except Exception:  # noqa: BLE001
        return None


def ensure_indexes(db) -> None:
    """Idempotent index creation: a UNIQUE store_id (one float doc per store) is
    the structural guarantee behind the double-open 409. Best-effort; never
    raises."""
    coll = _coll(db)
    if coll is None:
        return
    try:
        coll.create_index("store_id", unique=True)
        coll.create_index("status")
    except Exception:  # noqa: BLE001
        logger.debug("[PETTYCASH] ensure_indexes skipped", exc_info=True)


# ---------------------------------------------------------------------------
# Config (E2 seam)
# ---------------------------------------------------------------------------


def _policy(key: str, scope: Optional[Dict[str, Any]], default: float) -> float:
    """Resolve an E2 policy key with a safe rupee fallback. Never raises."""
    try:
        from .policy_engine import get_policy

        return float(get_policy(key, scope or None, default=default))
    except Exception:  # noqa: BLE001
        return float(default)


def float_limit_for(db, store_id: str, entity_id: Optional[str] = None) -> float:
    scope = {"store_id": store_id}
    if entity_id:
        scope["entity_id"] = entity_id
    return _policy("petty_cash.float_limit", scope, _DEFAULT_FLOAT_LIMIT)


def low_balance_threshold_for(db, store_id: str, entity_id: Optional[str] = None) -> float:
    scope = {"store_id": store_id}
    if entity_id:
        scope["entity_id"] = entity_id
    return _policy("petty_cash.low_balance_threshold", scope, _DEFAULT_LOW_BALANCE_THRESHOLD)


def auto_approval_threshold_for(db, store_id: Optional[str] = None,
                                entity_id: Optional[str] = None) -> float:
    """Petty-cash payouts strictly above this require an E4 approval (rupees)."""
    scope: Dict[str, Any] = {}
    if store_id:
        scope["store_id"] = store_id
    if entity_id:
        scope["entity_id"] = entity_id
    return _policy("petty_cash.auto_approval_threshold", scope or None, _DEFAULT_AUTO_THRESHOLD)


def receipt_required_above() -> float:
    """Receipt mandatory for any petty-cash claim strictly above this (rupees)."""
    return _policy("petty_cash.receipt_required_above", None, _RECEIPT_REQUIRED_ABOVE)


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


def _audit(db, action: str, store_id: str, *, delta: float, balance_after: float,
           reason: str, actor: Optional[str], expense_id: Optional[str] = None,
           txn_id: Optional[str] = None) -> None:
    """Append one petty-cash audit row via AuditRepository.create. Fail-soft --
    an audit failure NEVER undoes or blocks the float move."""
    if db is None:
        return
    try:
        coll = db.get_collection("audit_logs")
        if coll is None:
            return
        from database.repositories.audit_repository import AuditRepository

        repo = AuditRepository(coll)
        repo.create({
            "log_id": f"AUD-{uuid.uuid4().hex[:12]}",
            "action": action,
            "entity_type": "petty_cash_float",
            "entity_id": store_id,
            "user_id": actor,
            "actor": actor,
            "store_id": store_id,
            "source": "PETTYCASH",
            "before_state": None,
            "after_state": {
                "delta": delta,
                "balance_after": balance_after,
                "reason": reason,
                "expense_id": expense_id,
                "txn_id": txn_id,
            },
            "severity": "INFO",
            "timestamp": _now_iso(),
        })
    except Exception:  # noqa: BLE001
        logger.debug("[PETTYCASH] audit write skipped", exc_info=True)


# ---------------------------------------------------------------------------
# Low-balance alert (existing agent event bus; fail-soft)
# ---------------------------------------------------------------------------


def _maybe_emit_low_balance(store_id: str, balance: float, threshold: float) -> bool:
    """Emit cash.float.low on the agent bus when the post-debit balance is below
    threshold. Returns True if emitted. Fail-soft: a missing bus / no event loop
    never blocks the payout (the money already moved)."""
    if balance >= threshold:
        return False
    payload = {
        "store_id": store_id,
        "balance": round(float(balance), 2),
        "threshold": round(float(threshold), 2),
    }
    try:
        import asyncio

        from agents.registry import dispatch_event

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None and loop.is_running():
            # Inside a running event loop (FastAPI request): schedule, don't await.
            loop.create_task(dispatch_event(LOW_BALANCE_EVENT, payload, source="PETTYCASH"))
        else:
            asyncio.run(dispatch_event(LOW_BALANCE_EVENT, payload, source="PETTYCASH"))
        return True
    except Exception:  # noqa: BLE001
        logger.debug("[PETTYCASH] low-balance event skipped", exc_info=True)
        return True  # the condition WAS met even if dispatch was a no-op


# ---------------------------------------------------------------------------
# Float lifecycle
# ---------------------------------------------------------------------------


def open_float(db, *, store_id: str, amount: float, actor: str,
               float_limit: Optional[float] = None,
               low_balance_threshold: Optional[float] = None,
               entity_id: Optional[str] = None) -> Dict[str, Any]:
    """Initialise a store's petty-cash float with an opening balance.

    Idempotency / double-open guard: the document is inserted ONLY when no
    ACTIVE float exists for the store (the unique store_id index + a status
    check). A second open on an ACTIVE float returns http 409 -- the balance is
    never doubled. Fail-soft -> 503 with no DB.
    """
    coll = _coll(db)
    if coll is None:
        return {"ok": False, "http": 503, "error": "no_db"}
    try:
        amt = round(float(amount), 2)
    except (TypeError, ValueError):
        return {"ok": False, "http": 422, "error": "invalid_amount"}
    if amt <= 0:
        return {"ok": False, "http": 422, "error": "invalid_amount"}

    limit = float(float_limit) if float_limit is not None else float_limit_for(db, store_id, entity_id)
    threshold = (
        float(low_balance_threshold)
        if low_balance_threshold is not None
        else low_balance_threshold_for(db, store_id, entity_id)
    )
    if amt > limit:
        return {"ok": False, "http": 422, "error": "exceeds_float_limit", "float_limit": limit}

    existing = coll.find_one({"store_id": store_id})
    if existing and existing.get("status") == STATUS_ACTIVE:
        return {"ok": False, "http": 409, "error": "float_already_open",
                "balance": float(existing.get("balance") or 0.0)}

    now = _now_iso()
    txn_id = str(uuid.uuid4())
    ledger_entry = {
        "txn_id": txn_id,
        "type": "CREDIT",
        "delta": amt,
        "balance_after": amt,
        "reason": "float_open",
        "expense_id": None,
        "actor": actor,
        "created_at": now,
    }
    doc = {
        "store_id": store_id,
        "entity_id": entity_id,
        "balance": amt,
        "float_limit": round(limit, 2),
        "low_balance_threshold": round(threshold, 2),
        "status": STATUS_ACTIVE,
        "opened_by": actor,
        "opened_at": now,
        "ledger": [ledger_entry],
        "money_ledger": [],
        "updated_at": now,
    }
    try:
        if existing:
            # A previously CLOSED/FROZEN float doc exists -- reactivate it in a
            # single guarded write (re-open from a non-ACTIVE state). The guard on
            # status keeps this race-safe against a concurrent open.
            from pymongo import ReturnDocument

            after = coll.find_one_and_update(
                {"store_id": store_id, "status": {"$ne": STATUS_ACTIVE}},
                {"$set": doc},
                return_document=ReturnDocument.AFTER,
            )
            if after is None:
                cur = coll.find_one({"store_id": store_id}) or {}
                return {"ok": False, "http": 409, "error": "float_already_open",
                        "balance": float(cur.get("balance") or 0.0)}
        else:
            coll.insert_one(doc)
    except Exception as e:  # noqa: BLE001
        # A racing insert on the unique store_id index (E11000) means another
        # caller won the open -- report the conflict, do not double-credit.
        logger.warning("[PETTYCASH] open_float %s failed: %s", store_id, e)
        cur = coll.find_one({"store_id": store_id}) or {}
        if cur:
            return {"ok": False, "http": 409, "error": "float_already_open",
                    "balance": float(cur.get("balance") or 0.0)}
        return {"ok": False, "http": 503, "error": "write_failed"}

    _audit(db, "petty_cash.float_open", store_id, delta=amt, balance_after=amt,
           reason="float_open", actor=actor, txn_id=txn_id)
    logger.info("[PETTYCASH] float OPENED for %s: Rs %.2f by %s", store_id, amt, actor)
    return {"ok": True, "balance": amt, "txn_id": txn_id,
            "float_limit": round(limit, 2), "low_balance_threshold": round(threshold, 2)}


def topup_float(db, *, store_id: str, amount: float, actor: str,
                reason: str = "float_topup") -> Dict[str, Any]:
    """Replenish an ACTIVE float. A single guarded money_guard.credit (no floor on
    a credit). Caps the post-credit balance at the float_limit. Fail-soft."""
    coll = _coll(db)
    if coll is None:
        return {"ok": False, "http": 503, "error": "no_db"}
    try:
        amt = round(float(amount), 2)
    except (TypeError, ValueError):
        return {"ok": False, "http": 422, "error": "invalid_amount"}
    if amt <= 0:
        return {"ok": False, "http": 422, "error": "invalid_amount"}

    cur = coll.find_one({"store_id": store_id})
    if not cur:
        return {"ok": False, "http": 404, "error": "float_not_open"}
    if cur.get("status") != STATUS_ACTIVE:
        return {"ok": False, "http": 409, "error": "float_not_active",
                "status": cur.get("status")}
    limit = float(cur.get("float_limit") or float_limit_for(db, store_id, cur.get("entity_id")))
    if round(float(cur.get("balance") or 0.0) + amt, 2) > limit + 1e-6:
        return {"ok": False, "http": 422, "error": "exceeds_float_limit",
                "float_limit": limit, "balance": float(cur.get("balance") or 0.0)}

    txn_id = str(uuid.uuid4())
    res = mg.credit(
        db, ACCOUNT_TYPE, store_id, amt,
        reason=reason, actor=actor, ref=txn_id, store_id=store_id,
        push_extra={"ledger": _ledger_row(txn_id, "CREDIT", amt, reason, actor, None)},
        record_ledger=False,
    )
    if not res.ok:
        return {"ok": False, "http": 409, "error": res.reason or "credit_failed"}
    _stamp_balance_after(coll, store_id, txn_id)
    _audit(db, "petty_cash.topup", store_id, delta=amt, balance_after=res.balance,
           reason=reason, actor=actor, txn_id=txn_id)
    logger.info("[PETTYCASH] topup %s: +Rs %.2f -> Rs %.2f by %s",
                store_id, amt, res.balance, actor)
    return {"ok": True, "balance": res.balance, "txn_id": txn_id}


def debit_float(db, *, store_id: str, amount: float, expense_id: Optional[str],
                actor: str, reason: str = "payout") -> Dict[str, Any]:
    """Record a petty-cash payout: a SINGLE guarded money_guard.debit. The floor
    (balance >= amount) is in the money_guard FILTER, so two racing payouts can
    never both win and the float can never go negative. Emits the low-balance
    event when the post-debit balance crosses below the threshold.

    Returns {"ok": True, "balance", "txn_id"} or {"ok": False, "http", "error"}.
    A FROZEN/CLOSED float (status != ACTIVE) is rejected by the money_guard status
    guard; insufficient balance returns error="insufficient".
    """
    coll = _coll(db)
    if coll is None:
        return {"ok": False, "http": 503, "error": "no_db"}
    try:
        amt = round(float(amount), 2)
    except (TypeError, ValueError):
        return {"ok": False, "http": 422, "error": "invalid_amount"}
    if amt <= 0:
        return {"ok": False, "http": 422, "error": "invalid_amount"}

    txn_id = str(uuid.uuid4())
    res = mg.debit(
        db, ACCOUNT_TYPE, store_id, amt,
        reason=reason, actor=actor, ref=(expense_id or txn_id), store_id=store_id,
        push_extra={"ledger": _ledger_row(txn_id, "DEBIT", amt, reason, actor, expense_id)},
        record_ledger=False,
    )
    if not res.ok:
        # reason machine-codes from money_guard: insufficient / inactive /
        # not_found / no_atomic / unavailable.
        code = {
            "insufficient": 409, "inactive": 409, "not_found": 404,
            "no_atomic": 503, "unavailable": 503, "invalid_amount": 422,
        }.get(res.reason or "", 409)
        return {"ok": False, "http": code, "error": res.reason or "debit_failed"}

    _stamp_balance_after(coll, store_id, txn_id)
    _audit(db, "petty_cash.payout", store_id, delta=-amt, balance_after=res.balance,
           reason=reason, actor=actor, expense_id=expense_id, txn_id=txn_id)

    # Low-balance alert: a check in the debit path, not a separate cron.
    threshold = low_balance_threshold_for(db, store_id)
    cur = coll.find_one({"store_id": store_id}) or {}
    threshold = float(cur.get("low_balance_threshold") or threshold)
    is_low = _maybe_emit_low_balance(store_id, res.balance, threshold)

    logger.info("[PETTYCASH] payout %s: -Rs %.2f -> Rs %.2f (expense=%s) by %s",
                store_id, amt, res.balance, expense_id, actor)
    return {"ok": True, "balance": res.balance, "txn_id": txn_id, "is_low": is_low}


def reverse_payout(db, *, store_id: str, txn_id: str, actor: str,
                   reason: str = "reversal") -> Dict[str, Any]:
    """Restore a previously debited payout (a rejected/voided petty-cash expense).
    Looks up the original DEBIT ledger row by txn_id, then credits the float by
    the exact original amount via a single guarded money_guard.credit. Idempotent:
    a txn_id already reversed (a CREDIT reversal row exists) returns
    already_reversed and does NOT double-credit."""
    coll = _coll(db)
    if coll is None:
        return {"ok": False, "http": 503, "error": "no_db"}
    cur = coll.find_one({"store_id": store_id})
    if not cur:
        return {"ok": False, "http": 404, "error": "float_not_open"}

    ledger = cur.get("ledger") or []
    debit_row = next(
        (r for r in ledger if r.get("txn_id") == txn_id and r.get("type") == "DEBIT"),
        None,
    )
    if debit_row is None:
        return {"ok": False, "http": 404, "error": "payout_not_found"}
    # Idempotency: a reversal CREDIT already references this txn_id -> stop.
    if any(r.get("reverses") == txn_id for r in ledger):
        return {"ok": False, "http": 409, "error": "already_reversed"}

    amt = round(abs(float(debit_row.get("delta") or 0.0)), 2)
    if amt <= 0:
        return {"ok": False, "http": 422, "error": "invalid_amount"}

    rev_txn = str(uuid.uuid4())
    row = _ledger_row(rev_txn, "CREDIT", amt, reason, actor, debit_row.get("expense_id"))
    row["reverses"] = txn_id
    res = mg.credit(
        db, ACCOUNT_TYPE, store_id, amt,
        reason=reason, actor=actor, ref=txn_id, store_id=store_id,
        push_extra={"ledger": row}, record_ledger=False,
    )
    if not res.ok:
        return {"ok": False, "http": 409, "error": res.reason or "reverse_failed"}
    _stamp_balance_after(coll, store_id, rev_txn)
    _audit(db, "petty_cash.reversal", store_id, delta=amt, balance_after=res.balance,
           reason=reason, actor=actor, expense_id=debit_row.get("expense_id"), txn_id=rev_txn)
    logger.info("[PETTYCASH] reversal %s: +Rs %.2f -> Rs %.2f (reverses %s) by %s",
                store_id, amt, res.balance, txn_id, actor)
    return {"ok": True, "balance": res.balance, "txn_id": rev_txn, "reverses": txn_id}


def get_balance(db, store_id: str, *, ledger_limit: int = 20) -> Dict[str, Any]:
    """Read-only float view: balance, limit, threshold, status, is_low, and the
    most recent ledger entries. Fail-soft -> a not_open envelope."""
    coll = _coll(db)
    empty = {
        "ok": True, "store_id": store_id, "exists": False, "balance": 0.0,
        "float_limit": 0.0, "low_balance_threshold": 0.0, "status": None,
        "is_low": False, "recent_ledger": [],
    }
    if coll is None:
        return empty
    try:
        doc = coll.find_one({"store_id": store_id}, {"_id": 0})
    except Exception:  # noqa: BLE001
        return empty
    if not doc:
        return empty
    balance = float(doc.get("balance") or 0.0)
    threshold = float(doc.get("low_balance_threshold") or 0.0)
    ledger = doc.get("ledger") or []
    recent = list(ledger)[-int(ledger_limit):][::-1] if ledger else []
    return {
        "ok": True,
        "store_id": store_id,
        "exists": True,
        "balance": round(balance, 2),
        "float_limit": float(doc.get("float_limit") or 0.0),
        "low_balance_threshold": threshold,
        "status": doc.get("status"),
        "is_low": balance < threshold,
        "opened_by": doc.get("opened_by"),
        "opened_at": doc.get("opened_at"),
        "recent_ledger": recent,
    }


# ---------------------------------------------------------------------------
# Ledger helpers
# ---------------------------------------------------------------------------


def _ledger_row(txn_id: str, ltype: str, amount: float, reason: str,
                actor: Optional[str], expense_id: Optional[str]) -> Dict[str, Any]:
    """One human-readable ledger entry (balance_after stamped after the guarded
    write, since money_guard owns the post-balance). delta is always positive;
    direction is read from type."""
    return {
        "txn_id": txn_id,
        "type": ltype,
        "delta": round(float(amount), 2),
        "balance_after": None,
        "reason": reason,
        "expense_id": expense_id,
        "actor": actor,
        "created_at": _now_iso(),
    }


def _stamp_balance_after(coll, store_id: str, txn_id: str) -> None:
    """After a guarded credit/debit, stamp balance_after onto the just-pushed
    ledger row (the post-balance is the doc's current balance). Best-effort."""
    if coll is None:
        return
    try:
        doc = coll.find_one({"store_id": store_id})
        if not doc:
            return
        bal = round(float(doc.get("balance") or 0.0), 2)
        coll.update_one(
            {"store_id": store_id, "ledger.txn_id": txn_id},
            {"$set": {"ledger.$.balance_after": bal}},
        )
    except Exception:  # noqa: BLE001
        return
