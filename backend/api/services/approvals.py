"""
IMS 2.0 - E4 Approval / PIN + Maker-Checker engine
====================================================
A reusable, PIN-gated, time-limited, store-scoped, audit-chained maker-checker
engine that every domain consumes instead of ad-hoc approval logic.

Lifecycle (one collection: ``approval_requests``):

  1. request()  -- any maker records a REQUESTED doc with a 60-minute TTL.
  2. approve()  -- an approver with the right role AND a set PIN flips the doc
                   from REQUESTED -> APPROVED, minting an approval_token in the
                   SAME single atomic find_one_and_update. Two racing approves
                   -> exactly one token. Wrong PIN increments a brute-force
                   throttle counter on the user doc.
  3. consume_approval() -- the maker spends the approval EXACTLY ONCE via another
                   single atomic find_one_and_update guarded on
                   ``consumed:false, status:APPROVED, expires_at>now``.
  4. expire_stale() -- TASKMASTER's 5-min tick flips overdue REQUESTED docs to
                   EXPIRED (no Mongo TTL-delete index; expired rows stay
                   auditable). Every individual read also lazy-flips an overdue
                   row.
  5. Audit       -- every transition writes a hash-chained row to ``audit_logs``
                   via AuditRepository.create (entity_type "approval_request").
                   PIN values + bcrypt hashes never appear in any audit row.

Atomicity (CORRECTIONS P1/E4, R1): this is standalone Mongo -- NO transactions.
Every state change is a SINGLE find_one_and_update on a SINGLE document whose
FILTER encodes the guard, mirroring vouchers.redeem_voucher_atomic. The
approval_token is minted in the same op. A double-submit can never consume an
approval twice.

Tiers (CORRECTIONS): refund.tier.* thresholds are read from E2 ``get_policy``
(paisa-integers). E4 owns NO _DEFAULT_TIERS constant -- E2's registry default IS
the fallback. The PIN validity window comes from ``approval.pin_validity_min``.

PIN (CORRECTIONS): a PIN is a short password -- hashed/verified with bcrypt via
auth.py ``hash_password`` / ``verify_password``. Brute-force throttle:
``pin_attempts`` sub-doc on the user (count + window_start); five failures in
15 minutes -> 423 Locked.

Conventions (CLAUDE.md): NO emoji (Windows cp1252); ASCII log tag [APPROVALS].
Fail-soft everywhere: ``db=None`` => reads empty, writes no-op, never raises.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ============================================================================
# Status + action-type enums
# ============================================================================


class ApprovalStatus(str, Enum):
    REQUESTED = "REQUESTED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    CONSUMED = "CONSUMED"


# Action types supported at launch. journal_entry is maker-checker enforced
# (approver must differ from maker).
ACTION_TYPES: frozenset = frozenset({
    "discount_override",
    "refund",
    "journal_entry",
    "profile_merge",
    "petty_cash",
    "endless_aisle",
    "rtv",
    # E3w: a manager override for a return blocked by a serial mismatch. Resolves to
    # the "auto" tier (STORE_MANAGER+); single-use + store-bound at approve-time, so
    # a manager of store A cannot approve an override consumed against store B.
    "RETURN_SERIAL_OVERRIDE",
    # F26: a remote PIN-gated leave approval. amount=None -> resolves to the "auto"
    # tier (STORE_MANAGER+), single-use + store-bound at approve-time so a manager
    # of store A cannot approve a leave filed against store B. Self-approval (the
    # applicant approving their own leave) is blocked at the leave-router layer,
    # which knows the leave doc's employee_id (the engine does not).
    "leave_approval",
})

# Actions that REQUIRE separation of duties (approver != maker).
# petty_cash: an over-threshold petty-cash payout is real two-person control --
# the manager who raises the request cannot also PIN-approve it (F17).
MAKER_CHECKER_ACTIONS: frozenset = frozenset({"journal_entry", "petty_cash"})

# Role tiers. A request resolved to a tier may be approved by any role at or
# above that tier. SUPERADMIN passes everything.
_TIER_ROLES: Dict[str, List[str]] = {
    "auto": ["SUPERADMIN", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    "admin": ["SUPERADMIN", "ADMIN", "AREA_MANAGER"],
    "super": ["SUPERADMIN"],
}

# Roles that bypass store-scope (HQ). STORE_MANAGER / AREA_MANAGER are scoped to
# their assigned stores; ADMIN/SUPERADMIN see every store.
_HQ_ROLES: frozenset = frozenset({"SUPERADMIN", "ADMIN"})

# PIN brute-force throttle (CORRECTIONS P1/E4).
_PIN_MAX_ATTEMPTS = 5
_PIN_WINDOW_MIN = 15

# Approval-request validity window default (minutes). The live value is read
# from E2 get_policy("approval.pin_validity_min"); this is only the no-E2/no-DB
# fallback. DECISIONS sec 4: 60 minutes.
_DEFAULT_VALIDITY_MIN = 60

# PIN format: 4-6 digits.
_PIN_MIN_LEN = 4
_PIN_MAX_LEN = 6


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if hasattr(dt, "isoformat"):
        return dt.isoformat()
    return str(dt)


def _validity_minutes() -> int:
    """Approval-request validity window in minutes, from E2 get_policy with a
    safe code fallback. Never raises."""
    try:
        from api.services.policy_engine import get_policy

        val = get_policy("approval.pin_validity_min", default=_DEFAULT_VALIDITY_MIN)
        return int(val)
    except Exception:  # noqa: BLE001 - fail-soft to the locked 60-min default
        return _DEFAULT_VALIDITY_MIN


# ============================================================================
# PIN helpers (module-level) -- bcrypt via auth.py; brute-force throttle
# ============================================================================


def _hash_password(pin: str) -> str:
    from api.routers.auth import hash_password

    return hash_password(pin)


def _verify_password(pin: str, hashed: str) -> bool:
    from api.routers.auth import verify_password

    try:
        return verify_password(pin, hashed)
    except Exception:  # noqa: BLE001
        return False


def _users_coll(db):
    """Resolve the ``users`` collection from a pymongo Database or a wrapper that
    exposes get_collection(). None when no DB."""
    if db is None:
        return None
    getter = getattr(db, "get_collection", None)
    if callable(getter):
        try:
            return getter("users")
        except Exception:  # noqa: BLE001
            return None
    try:
        return db["users"]
    except Exception:  # noqa: BLE001
        return None


def _is_pin_valid_format(pin: str) -> bool:
    return (
        isinstance(pin, str)
        and pin.isdigit()
        and _PIN_MIN_LEN <= len(pin) <= _PIN_MAX_LEN
    )


def set_approver_pin(db, user_id: str, pin: str, set_by: str) -> Dict[str, Any]:
    """Set/rotate an approver PIN (bcrypt). Validates 4-6 digits, clears the
    brute-force throttle, audits ``pin_set``. The hash never leaves this layer.
    Fail-soft on no DB."""
    if not _is_pin_valid_format(pin):
        return {"ok": False, "error": "invalid_pin_format"}
    coll = _users_coll(db)
    if coll is None:
        return {"ok": False, "error": "no_db"}
    now = _now()
    try:
        res = coll.update_one(
            {"user_id": user_id},
            {
                "$set": {
                    "approval_pin_hash": _hash_password(pin),
                    "approval_pin_set_at": now,
                    "pin_attempts": {"count": 0, "window_start": now},
                }
            },
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("[APPROVALS] set_approver_pin failed for %s: %s", user_id, e)
        return {"ok": False, "error": "write_failed"}
    if getattr(res, "matched_count", 1) == 0:
        return {"ok": False, "error": "user_not_found"}
    ApprovalEngine(db=db)._write_pin_audit("pin_set", user_id, set_by)
    return {"ok": True, "pin_set_at": _iso(now)}


def clear_approver_pin(db, user_id: str, cleared_by: str) -> Dict[str, Any]:
    """Remove an approver PIN. Audits ``pin_cleared``. Fail-soft on no DB."""
    coll = _users_coll(db)
    if coll is None:
        return {"ok": False, "error": "no_db"}
    try:
        res = coll.update_one(
            {"user_id": user_id},
            {
                "$unset": {
                    "approval_pin_hash": "",
                    "approval_pin_set_at": "",
                    "pin_attempts": "",
                }
            },
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("[APPROVALS] clear_approver_pin failed for %s: %s", user_id, e)
        return {"ok": False, "error": "write_failed"}
    if getattr(res, "matched_count", 1) == 0:
        return {"ok": False, "error": "user_not_found"}
    ApprovalEngine(db=db)._write_pin_audit("pin_cleared", user_id, cleared_by)
    return {"ok": True}


def verify_approver_pin(db, user_id: str, pin: str) -> bool:
    """Plain PIN verification (no throttle), used for self-rotation's current-PIN
    check. The brute-force-throttled path is ApprovalEngine._check_and_count_pin.
    Fail-soft -> False with no DB / no PIN."""
    coll = _users_coll(db)
    if coll is None:
        return False
    try:
        u = coll.find_one({"user_id": user_id})
    except Exception:  # noqa: BLE001
        return False
    if not u or not u.get("approval_pin_hash"):
        return False
    return _verify_password(pin, u.get("approval_pin_hash"))


def has_approver_pin(db, user_id: str) -> Dict[str, Any]:
    """Report whether a user has a PIN set (never the hash)."""
    coll = _users_coll(db)
    if coll is None:
        return {"has_pin": False}
    try:
        u = coll.find_one({"user_id": user_id})
    except Exception:  # noqa: BLE001
        return {"has_pin": False}
    if not u:
        return {"has_pin": False}
    return {
        "has_pin": bool(u.get("approval_pin_hash")),
        "pin_set_at": _iso(u.get("approval_pin_set_at")),
    }


# ============================================================================
# Engine
# ============================================================================


class ApprovalEngine:
    """Lifecycle wrapper over the ``approval_requests`` collection. Modelled on
    ProposalStore; every method fail-soft on ``db is None``."""

    COLLECTION = "approval_requests"
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

    def _users(self):
        return _users_coll(self._db)

    def ensure_indexes(self) -> None:
        """Idempotent index creation. NO TTL-delete index -- expired docs stay
        auditable. Best-effort; never raises."""
        coll = self._coll()
        if coll is None:
            return
        try:
            coll.create_index("request_id", unique=True)
            coll.create_index("approval_token", sparse=True, unique=True)
            coll.create_index([("status", 1), ("store_id", 1), ("created_at", -1)])
            coll.create_index([("requested_by", 1), ("status", 1), ("created_at", -1)])
            coll.create_index("expires_at")
            coll.create_index(
                "dedupe_key",
                unique=True,
                # MUST guard on $exists: a partial index does NOT skip docs missing the
                # field -- it indexes them as null, so two dedupe-less REQUESTED requests
                # (the normal case -- approvals sit pending up to 60 min) would collide
                # E11000. sparse cannot be combined with partialFilterExpression.
                partialFilterExpression={
                    "status": ApprovalStatus.REQUESTED.value,
                    "dedupe_key": {"$exists": True},
                },
            )
        except Exception:  # noqa: BLE001
            logger.debug("[APPROVALS] ensure_indexes skipped", exc_info=True)

    # --- tier resolution (E2 seam) ----------------------------------------

    def _resolve_tier(
        self,
        amount: Optional[float],
        action_type: str,
        store_id: Optional[str] = None,
        entity_id: Optional[str] = None,
        required_tier: Optional[str] = None,
    ) -> str:
        """Resolve the approval tier from the rupee ``amount`` and the E2
        refund.tier.* thresholds (paisa-integers). E4 owns NO threshold
        constant -- E2's registry default is the fallback.

        ``required_tier`` (explicit) may only RAISE the tier (e.g. a serial-mismatch
        refund pins to "admin" even for a small amount, DECISIONS sec 7). It can NEVER
        lower it: a maker must not be able to route a high-value request to a low-tier
        approver by passing required_tier="auto" (tier-escalation bypass).
        """
        # E2 thresholds are paisa; the request amount is rupees. Convert E2 ->
        # rupees so the comparison is in one unit. Fail-soft to the locked
        # DECISIONS sec 6 defaults if E2 is unavailable.
        auto_below_rs = 500.0
        admin_above_rs = 2000.0
        super_above_rs = 10000.0
        scope = {}
        if store_id:
            scope["store_id"] = store_id
        if entity_id:
            scope["entity_id"] = entity_id
        try:
            from api.services.policy_engine import get_policy

            auto_below_rs = float(get_policy("refund.tier.auto_below", scope or None, default=50000)) / 100.0
            admin_above_rs = float(get_policy("refund.tier.admin_above", scope or None, default=200000)) / 100.0
            super_above_rs = float(get_policy("refund.tier.super_above", scope or None, default=1000000)) / 100.0
        except Exception:  # noqa: BLE001 - keep the locked defaults
            logger.debug("[APPROVALS] tier policy read failed; using defaults", exc_info=True)

        amt = float(amount or 0.0)
        if amt >= super_above_rs:
            amount_tier = "super"
        elif amt >= admin_above_rs:
            amount_tier = "admin"
        else:
            # < auto_below or between auto_below and admin_above -> auto tier
            # (managers can approve); only >= admin_above escalates.
            amount_tier = "auto"

        # required_tier may only RAISE the tier (max by severity), never lower it.
        _severity = {"auto": 0, "admin": 1, "super": 2}
        if required_tier in _TIER_ROLES and _severity.get(required_tier, 0) > _severity.get(amount_tier, 0):
            return required_tier  # type: ignore[return-value]
        return amount_tier

    @staticmethod
    def _tier_to_roles(tier: str) -> List[str]:
        return list(_TIER_ROLES.get(tier, _TIER_ROLES["auto"]))

    # --- create ------------------------------------------------------------

    def request(
        self,
        *,
        action_type: str,
        requested_by: str,
        requested_by_roles: Optional[List[str]] = None,
        store_id: Optional[str] = None,
        entity_id: Optional[str] = None,
        amount: Optional[float] = None,
        context: Optional[Dict[str, Any]] = None,
        reason: str = "",
        required_tier: Optional[str] = None,
        dedupe_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Record a REQUESTED approval with a 60-min TTL. Dedupes an identical
        live (REQUESTED) request via dedupe_key. Fail-soft -> {"ok": False,
        "error": "no_db"} with no DB."""
        coll = self._coll()
        if coll is None:
            return {"ok": False, "error": "no_db"}

        if action_type not in ACTION_TYPES:
            return {"ok": False, "error": "unknown_action_type"}

        # Dedupe: an identical live request returns the existing one.
        if dedupe_key:
            try:
                existing = coll.find_one(
                    {"dedupe_key": dedupe_key, "status": ApprovalStatus.REQUESTED.value},
                    {"_id": 0},
                )
                if existing:
                    return {
                        "ok": True,
                        "deduped": True,
                        "request_id": existing.get("request_id"),
                        "status": existing.get("status"),
                        "required_roles": existing.get("required_roles"),
                        "expires_at": _iso(existing.get("expires_at")),
                    }
            except Exception as e:  # noqa: BLE001
                logger.debug("[APPROVALS] dedupe lookup failed: %s", e)

        tier = self._resolve_tier(amount, action_type, store_id, entity_id, required_tier)
        required_roles = self._tier_to_roles(tier)
        now = _now()
        expires_at = now + timedelta(minutes=_validity_minutes())
        is_mc = action_type in MAKER_CHECKER_ACTIONS

        doc: Dict[str, Any] = {
            "request_id": f"REQ-{uuid.uuid4().hex[:12]}",
            "action_type": action_type,
            "status": ApprovalStatus.REQUESTED.value,
            "requested_by": requested_by,
            "requested_by_roles": list(requested_by_roles or []),
            "store_id": store_id,
            "entity_id": entity_id,
            "amount": float(amount) if amount is not None else None,
            "required_tier": tier,
            "required_roles": required_roles,
            "context": context or {},
            "reason": reason or "",
            "maker_checker": is_mc,
            "created_at": now,
            "expires_at": expires_at,
            "reviewed_by": None,
            "reviewed_at": None,
            "reject_reason": None,
            "approval_token": None,
            "consumed": False,
            "consumed_at": None,
            "consumed_by": None,
            "audit_log_id": None,
        }
        if dedupe_key:
            doc["dedupe_key"] = dedupe_key

        try:
            coll.insert_one(doc)
        except Exception as e:  # noqa: BLE001
            logger.warning("[APPROVALS] request insert failed (%s): %s", action_type, e)
            return {"ok": False, "error": "write_failed"}

        audit_id = self._write_audit("approval_requested", doc, actor=requested_by)
        self._set(doc["request_id"], {"audit_log_id": audit_id})
        self._notify_approvers(doc)
        logger.info(
            "[APPROVALS] %s REQUESTED (%s, tier=%s) by %s",
            doc["request_id"], action_type, tier, requested_by,
        )
        return {
            "ok": True,
            "request_id": doc["request_id"],
            "status": doc["status"],
            "required_tier": tier,
            "required_roles": required_roles,
            "expires_at": _iso(expires_at),
        }

    # --- PIN throttle ------------------------------------------------------

    def _check_and_count_pin(self, user_id: str, pin: str) -> Dict[str, Any]:
        """Verify the PIN against the user's bcrypt hash with a brute-force
        throttle. Returns one of:
          {"ok": True}                                   -- correct PIN, counter reset
          {"ok": False, "error": "pin_not_set"}          -- no hash on the user
          {"ok": False, "error": "pin_locked", "retry_after_min": N}
          {"ok": False, "error": "wrong_pin", "remaining": N}

        The lock is enforced via a single atomic find_one_and_update on the user
        doc (Mongo, not Redis) so it works even with Redis absent. Five wrong
        attempts within a 15-minute rolling window lock the account.
        """
        from pymongo import ReturnDocument

        coll = self._users()
        if coll is None:
            return {"ok": False, "error": "no_db"}
        try:
            user = coll.find_one({"user_id": user_id})
        except Exception:  # noqa: BLE001
            return {"ok": False, "error": "no_db"}
        if not user:
            return {"ok": False, "error": "user_not_found"}

        pin_hash = user.get("approval_pin_hash")
        if not pin_hash:
            return {"ok": False, "error": "pin_not_set"}

        now = _now()
        attempts = user.get("pin_attempts") or {}
        count = int(attempts.get("count") or 0)
        window_start = attempts.get("window_start")
        window_open = (
            window_start is not None
            and (now - _as_aware(window_start)) < timedelta(minutes=_PIN_WINDOW_MIN)
        )

        # Locked: max attempts reached inside the live window.
        if window_open and count >= _PIN_MAX_ATTEMPTS:
            elapsed = now - _as_aware(window_start)
            retry_after = max(
                0, int((timedelta(minutes=_PIN_WINDOW_MIN) - elapsed).total_seconds() // 60) + 1
            )
            return {"ok": False, "error": "pin_locked", "retry_after_min": retry_after}

        if _verify_password(pin, pin_hash):
            # Correct -> clear the throttle.
            try:
                coll.update_one(
                    {"user_id": user_id},
                    {"$set": {"pin_attempts": {"count": 0, "window_start": now}}},
                )
            except Exception:  # noqa: BLE001
                pass
            return {"ok": True}

        # Wrong PIN -> increment within the rolling window (reset the window if
        # it lapsed). Atomic on the single user doc.
        if window_open:
            try:
                updated = coll.find_one_and_update(
                    {"user_id": user_id},
                    {"$inc": {"pin_attempts.count": 1}},
                    return_document=ReturnDocument.AFTER,
                )
            except Exception:  # noqa: BLE001
                updated = None
            new_count = int(((updated or {}).get("pin_attempts") or {}).get("count") or count + 1)
        else:
            try:
                coll.update_one(
                    {"user_id": user_id},
                    {"$set": {"pin_attempts": {"count": 1, "window_start": now}}},
                )
            except Exception:  # noqa: BLE001
                pass
            new_count = 1

        if new_count >= _PIN_MAX_ATTEMPTS:
            return {"ok": False, "error": "pin_locked", "retry_after_min": _PIN_WINDOW_MIN}
        return {"ok": False, "error": "wrong_pin", "remaining": _PIN_MAX_ATTEMPTS - new_count}

    # --- approve (single atomic find_one_and_update mints the token) -------

    def approve(
        self,
        request_id: str,
        *,
        approver_user_id: str,
        approver_roles: List[str],
        pin: str,
        approver_store_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Approve a REQUESTED request, PIN-gated. The REQUESTED -> APPROVED
        transition is a SINGLE atomic find_one_and_update whose filter guards
        status/expiry/consumed AND mints the approval_token in the same op
        (CORRECTIONS P1/E4). Two racing approves -> exactly one token; the loser
        gets 409.

        Returns {"ok": True, "approval_token": ..., ...} or
        {"ok": False, "http": <code>, "error": <str>}.
        """
        from pymongo import ReturnDocument

        coll = self._coll()
        if coll is None:
            return {"ok": False, "http": 503, "error": "no_db"}

        req = self.get(request_id)
        if req is None:
            return {"ok": False, "http": 404, "error": "not_found"}

        # Pre-flight (non-atomic) gates: tier role + maker-checker + store scope.
        # These do not mutate; they 403 BEFORE we touch the PIN throttle so a
        # wrong-role caller can't burn an approver's attempts. The atomic guard
        # below is the only thing that flips the doc.
        roles = set(approver_roles or [])
        required = set(req.get("required_roles") or [])
        if "SUPERADMIN" not in roles and not (roles & required):
            return {"ok": False, "http": 403, "error": "insufficient_tier"}

        if req.get("maker_checker") and approver_user_id == req.get("requested_by"):
            return {"ok": False, "http": 403, "error": "cannot_approve_own"}

        if not self._store_scope_ok(req.get("store_id"), roles, approver_store_ids):
            return {"ok": False, "http": 403, "error": "store_scope"}

        # PIN gate + brute-force throttle.
        pin_res = self._check_and_count_pin(approver_user_id, pin)
        if not pin_res.get("ok"):
            err = pin_res.get("error")
            if err in ("pin_not_set", "pin_locked"):
                out = {"ok": False, "http": 423, "error": err}
            elif err == "wrong_pin":
                out = {"ok": False, "http": 403, "error": "wrong_pin",
                       "remaining": pin_res.get("remaining")}
            elif err in ("no_db",):
                out = {"ok": False, "http": 503, "error": "no_db"}
            else:
                out = {"ok": False, "http": 404, "error": err or "user_not_found"}
            if "retry_after_min" in pin_res:
                out["retry_after_min"] = pin_res["retry_after_min"]
            return out

        # THE atomic guard: only one writer can match-and-modify. The filter
        # encodes status==REQUESTED, not expired, not consumed; the update mints
        # the token in the SAME op.
        now = _now()
        token = f"APT-{uuid.uuid4().hex}"
        updated = coll.find_one_and_update(
            {
                "request_id": request_id,
                "status": ApprovalStatus.REQUESTED.value,
                "consumed": False,
                "expires_at": {"$gt": now},
            },
            {
                "$set": {
                    "status": ApprovalStatus.APPROVED.value,
                    "approval_token": token,
                    "reviewed_by": approver_user_id,
                    "reviewed_at": now,
                    # Re-arm the validity window FROM THE APPROVAL (same atomic op):
                    # without this an approval minted at minute 50 of the request's
                    # 60-min TTL leaves only 10 min to consume -- "approve now,
                    # post at end-of-day" dead-ended. The token is now valid for a
                    # full validity window from the moment it was approved.
                    "expires_at": now + timedelta(minutes=_validity_minutes()),
                }
            },
            return_document=ReturnDocument.AFTER,
        )

        if updated is None:
            # Disambiguate: expired vs already-reviewed (lost the race).
            return self._approve_failure(request_id)

        updated.pop("_id", None)
        audit_id = self._write_audit("approved", updated, actor=approver_user_id)
        self._set(request_id, {"audit_log_id": audit_id})
        logger.info("[APPROVALS] %s APPROVED by %s", request_id, approver_user_id)
        return {
            "ok": True,
            "approval_token": token,
            "status": updated.get("status"),
            "reviewed_at": _iso(now),
            "request_id": request_id,
        }

    def _approve_failure(self, request_id: str) -> Dict[str, Any]:
        """Pure messaging helper after the atomic approve filter matched nothing.
        Lazy-flips an overdue REQUESTED row to EXPIRED."""
        doc = self._raw(request_id)
        if not doc:
            return {"ok": False, "http": 404, "error": "not_found"}
        status = doc.get("status")
        now = _now()
        if status == ApprovalStatus.REQUESTED.value and _as_aware(doc.get("expires_at")) <= now:
            self._lazy_expire(doc)
            return {"ok": False, "http": 410, "error": "expired"}
        if status in (ApprovalStatus.APPROVED.value, ApprovalStatus.REJECTED.value,
                      ApprovalStatus.CONSUMED.value):
            return {"ok": False, "http": 409, "error": "already_reviewed", "status": status}
        if status == ApprovalStatus.EXPIRED.value:
            return {"ok": False, "http": 410, "error": "expired"}
        return {"ok": False, "http": 409, "error": "conflict", "status": status}

    # --- reject ------------------------------------------------------------

    def reject(
        self,
        request_id: str,
        *,
        approver_user_id: str,
        approver_roles: List[str],
        pin: str,
        reason: str = "",
        approver_store_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Reject a REQUESTED request. Same PIN/role/scope/expiry guards as
        approve; atomic flip to REJECTED."""
        from pymongo import ReturnDocument

        coll = self._coll()
        if coll is None:
            return {"ok": False, "http": 503, "error": "no_db"}

        req = self.get(request_id)
        if req is None:
            return {"ok": False, "http": 404, "error": "not_found"}

        roles = set(approver_roles or [])
        required = set(req.get("required_roles") or [])
        if "SUPERADMIN" not in roles and not (roles & required):
            return {"ok": False, "http": 403, "error": "insufficient_tier"}
        if not self._store_scope_ok(req.get("store_id"), roles, approver_store_ids):
            return {"ok": False, "http": 403, "error": "store_scope"}

        pin_res = self._check_and_count_pin(approver_user_id, pin)
        if not pin_res.get("ok"):
            err = pin_res.get("error")
            if err in ("pin_not_set", "pin_locked"):
                out = {"ok": False, "http": 423, "error": err}
            elif err == "wrong_pin":
                out = {"ok": False, "http": 403, "error": "wrong_pin",
                       "remaining": pin_res.get("remaining")}
            else:
                out = {"ok": False, "http": 503 if err == "no_db" else 404,
                       "error": err or "user_not_found"}
            if "retry_after_min" in pin_res:
                out["retry_after_min"] = pin_res["retry_after_min"]
            return out

        now = _now()
        updated = coll.find_one_and_update(
            {
                "request_id": request_id,
                "status": ApprovalStatus.REQUESTED.value,
                "consumed": False,
                "expires_at": {"$gt": now},
            },
            {
                "$set": {
                    "status": ApprovalStatus.REJECTED.value,
                    "reviewed_by": approver_user_id,
                    "reviewed_at": now,
                    "reject_reason": reason or "",
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        if updated is None:
            return self._approve_failure(request_id)
        updated.pop("_id", None)
        audit_id = self._write_audit("rejected", updated, actor=approver_user_id, reason=reason)
        self._set(request_id, {"audit_log_id": audit_id})
        logger.info("[APPROVALS] %s REJECTED by %s", request_id, approver_user_id)
        return {"ok": True, "status": updated.get("status"), "request_id": request_id}

    # --- consume (single atomic single-use) --------------------------------

    def consume_approval(
        self,
        *,
        consumed_by: str,
        action_type: str,
        request_id: Optional[str] = None,
        approval_token: Optional[str] = None,
        amount: Optional[float] = None,
        expected_store_id: Optional[str] = None,
        expected_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Spend an APPROVED approval EXACTLY ONCE. A SINGLE atomic
        find_one_and_update guards status==APPROVED, consumed==false, not
        expired; flips to CONSUMED in the same op. Two racing consumes -> exactly
        one wins; the loser gets "already_consumed".

        Post-match checks on the returned doc: action_type matches; (if an amount
        is supplied) amount <= the approved amount; and -- the P1-2 binding -- if
        ``expected_store_id`` is supplied it must equal the approval's store_id,
        and any key in ``expected_context`` must equal the approval's stored
        ``context`` value for that key (e.g. {"rma_id": "RMA-..."}). This binds a
        token to the exact resource + store it was minted for, so an APPROVED rtv
        token cannot be replayed against another store or another RMA. The
        binding is OPT-IN: callers that pass neither (the historical refund /
        discount_override / journal_entry / leave consumers) are UNAFFECTED. Any
        mismatch is rolled back so the approval is not silently burned.
        """
        from pymongo import ReturnDocument

        coll = self._coll()
        if coll is None:
            return {"ok": False, "error": "no_db"}
        if not request_id and not approval_token:
            return {"ok": False, "error": "missing_identifier"}

        now = _now()
        ident: Dict[str, Any] = {}
        if request_id:
            ident["request_id"] = request_id
        if approval_token:
            ident["approval_token"] = approval_token

        updated = coll.find_one_and_update(
            {
                **ident,
                "status": ApprovalStatus.APPROVED.value,
                "consumed": False,
                "expires_at": {"$gt": now},
            },
            {
                "$set": {
                    "status": ApprovalStatus.CONSUMED.value,
                    "consumed": True,
                    "consumed_at": now,
                    "consumed_by": consumed_by,
                }
            },
            return_document=ReturnDocument.AFTER,
        )

        if updated is None:
            return self._consume_failure(ident)

        updated.pop("_id", None)

        # Post-match validation. A mismatch must NOT keep the approval burned --
        # roll the doc back to APPROVED (atomic re-claim on consumed==True by us).
        # These are INDEPENDENT guards (not an elif chain): an in-bounds amount
        # must NOT short-circuit the store / context binding checks below.
        mismatch: Optional[str] = None
        if updated.get("action_type") != action_type:
            mismatch = "action_mismatch"
        if mismatch is None and amount is not None and updated.get("amount") is not None:
            if float(amount) > float(updated.get("amount")):
                mismatch = "amount_exceeded"
        if mismatch is None and expected_store_id is not None \
                and updated.get("store_id") != expected_store_id:
            # P1-2: a token minted for store A cannot be consumed against store B.
            mismatch = "store_mismatch"
        if mismatch is None and expected_context:
            # P1-2: every supplied context key (e.g. rma_id) must match the value
            # the approval was minted with -- binds the token to its exact resource.
            stored_ctx = updated.get("context") or {}
            for _k, _v in expected_context.items():
                if stored_ctx.get(_k) != _v:
                    mismatch = "context_mismatch"
                    break

        if mismatch:
            try:
                coll.find_one_and_update(
                    {"request_id": updated.get("request_id"), "consumed": True,
                     "consumed_by": consumed_by},
                    {"$set": {
                        "status": ApprovalStatus.APPROVED.value,
                        "consumed": False,
                        "consumed_at": None,
                        "consumed_by": None,
                    }},
                )
            except Exception:  # noqa: BLE001
                pass
            return {"ok": False, "error": mismatch}

        audit_id = self._write_audit("consumed", updated, actor=consumed_by)
        self._set(updated.get("request_id"), {"audit_log_id": audit_id})
        logger.info("[APPROVALS] %s CONSUMED by %s", updated.get("request_id"), consumed_by)
        return {"ok": True, "request": _jsonable(updated)}

    def _consume_failure(self, ident: Dict[str, Any]) -> Dict[str, Any]:
        doc = None
        coll = self._coll()
        if coll is not None:
            try:
                doc = coll.find_one(ident)
            except Exception:  # noqa: BLE001
                doc = None
        if not doc:
            return {"ok": False, "error": "not_found"}
        status = doc.get("status")
        if doc.get("consumed") or status == ApprovalStatus.CONSUMED.value:
            return {"ok": False, "error": "already_consumed"}
        if status == ApprovalStatus.REQUESTED.value and _as_aware(doc.get("expires_at")) <= _now():
            self._lazy_expire(doc)
            return {"ok": False, "error": "expired"}
        if status == ApprovalStatus.EXPIRED.value:
            return {"ok": False, "error": "expired"}
        if status != ApprovalStatus.APPROVED.value:
            return {"ok": False, "error": "not_approved", "status": status}
        if _as_aware(doc.get("expires_at")) <= _now():
            return {"ok": False, "error": "expired"}
        return {"ok": False, "error": "not_approved", "status": status}

    # --- read --------------------------------------------------------------

    def _raw(self, request_id: str) -> Optional[Dict[str, Any]]:
        coll = self._coll()
        if coll is None:
            return None
        try:
            return coll.find_one({"request_id": request_id}, {"_id": 0})
        except Exception:  # noqa: BLE001
            return None

    def get(self, request_id: str) -> Optional[Dict[str, Any]]:
        """Single fetch; lazy-flips an overdue REQUESTED row to EXPIRED."""
        doc = self._raw(request_id)
        if doc is None:
            return None
        if (
            doc.get("status") == ApprovalStatus.REQUESTED.value
            and _as_aware(doc.get("expires_at")) <= _now()
        ):
            self._lazy_expire(doc)
            doc["status"] = ApprovalStatus.EXPIRED.value
        return doc

    def list_inbox(
        self,
        approver_roles: List[str],
        store_ids: Optional[List[str]] = None,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Pending/history inbox for an approver. HQ roles (ADMIN/SUPERADMIN) see
        every store; STORE/AREA managers are scoped to ``store_ids``."""
        coll = self._coll()
        if coll is None:
            return []
        q: Dict[str, Any] = {}
        if status and status.upper() != "ALL":
            q["status"] = status.upper()
        roles = set(approver_roles or [])
        if not (roles & _HQ_ROLES):
            scoped = list(store_ids or [])
            # A scoped approver sees their stores' requests plus store-less ones.
            q["store_id"] = {"$in": scoped + [None]}
        try:
            rows = list(coll.find(q, {"_id": 0}).sort("created_at", -1).limit(int(limit)))
        except Exception:  # noqa: BLE001
            return []
        return [_jsonable(r) for r in rows]

    def list_mine(self, requested_by: str, limit: int = 100) -> List[Dict[str, Any]]:
        coll = self._coll()
        if coll is None:
            return []
        try:
            rows = list(
                coll.find({"requested_by": requested_by}, {"_id": 0})
                .sort("created_at", -1)
                .limit(int(limit))
            )
        except Exception:  # noqa: BLE001
            return []
        return [_jsonable(r) for r in rows]

    def pending_count(self, approver_roles: List[str], store_ids: Optional[List[str]] = None) -> int:
        rows = self.list_inbox(approver_roles, store_ids, status="REQUESTED", limit=500)
        return len(rows)

    # --- expiry ------------------------------------------------------------

    def expire_stale(self) -> int:
        """Flip every overdue REQUESTED doc to EXPIRED, one audit row each.
        Called by TASKMASTER's 5-min tick. NOT a TTL-delete (rows stay
        auditable). Fail-soft -> 0 with no DB."""
        coll = self._coll()
        if coll is None:
            return 0
        now = _now()
        try:
            stale = list(
                coll.find(
                    {"status": ApprovalStatus.REQUESTED.value, "expires_at": {"$lt": now}},
                    {"_id": 0},
                )
            )
        except Exception:  # noqa: BLE001
            return 0
        count = 0
        for doc in stale:
            try:
                res = coll.update_one(
                    {"request_id": doc.get("request_id"), "status": ApprovalStatus.REQUESTED.value},
                    {"$set": {"status": ApprovalStatus.EXPIRED.value}},
                )
            except Exception:  # noqa: BLE001
                continue
            if getattr(res, "modified_count", 0):
                count += 1
                expired_doc = dict(doc, status=ApprovalStatus.EXPIRED.value)
                self._write_audit("expired", expired_doc, actor="SYSTEM")
        if count:
            logger.info("[APPROVALS] expire_stale flipped %d stale request(s)", count)
        return count

    def _lazy_expire(self, doc: Dict[str, Any]) -> None:
        coll = self._coll()
        if coll is None:
            return
        try:
            res = coll.update_one(
                {"request_id": doc.get("request_id"), "status": ApprovalStatus.REQUESTED.value},
                {"$set": {"status": ApprovalStatus.EXPIRED.value}},
            )
        except Exception:  # noqa: BLE001
            return
        if getattr(res, "modified_count", 0):
            self._write_audit("expired", dict(doc, status=ApprovalStatus.EXPIRED.value),
                              actor="SYSTEM")

    # --- internals ---------------------------------------------------------

    def _store_scope_ok(
        self,
        request_store_id: Optional[str],
        approver_roles: set,
        approver_store_ids: Optional[List[str]],
    ) -> bool:
        if approver_roles & _HQ_ROLES:
            return True
        if not request_store_id:
            return True  # store-less request is org-wide
        return request_store_id in set(approver_store_ids or [])

    def _set(self, request_id: str, fields: Dict[str, Any]) -> None:
        coll = self._coll()
        if coll is None or not fields:
            return
        try:
            coll.update_one({"request_id": request_id}, {"$set": fields})
        except Exception as e:  # noqa: BLE001
            logger.warning("[APPROVALS] update failed for %s: %s", request_id, e)

    def _notify_approvers(self, doc: Dict[str, Any]) -> None:
        """Best-effort in-app bell write for eligible approvers. Fail-soft; never
        blocks the request. Outbound WhatsApp is MEGAPHONE's job (DISPATCH_MODE
        gated) and is intentionally not done synchronously here."""
        if self._db is None:
            return
        try:
            ncoll = self._db.get_collection("notifications")
        except Exception:  # noqa: BLE001
            return
        if ncoll is None:
            return
        amount = doc.get("amount")
        msg_amt = ("Rs " + format(amount, ".2f")) if amount is not None else "review"
        try:
            ncoll.insert_one({
                "notification_id": f"NTF-APR-{uuid.uuid4().hex[:10]}",
                "kind": "approval_request",
                "title": "Approval required",
                "message": f"{doc.get('action_type')} for {msg_amt}",
                "for_roles": doc.get("required_roles"),
                "store_id": doc.get("store_id"),
                "request_id": doc.get("request_id"),
                "status": "PENDING",
                "source": "APPROVALS",
                "created_at": _now(),
            })
        except Exception:  # noqa: BLE001
            logger.debug("[APPROVALS] bell write skipped", exc_info=True)

    # --- audit -------------------------------------------------------------

    def _audit_repo(self):
        coll = self._audit_coll()
        if coll is not None:
            try:
                from database.repositories.audit_repository import AuditRepository

                return AuditRepository(coll)
            except Exception as e:  # noqa: BLE001
                logger.debug("[APPROVALS] AuditRepository build failed: %s", e)
        try:
            from api.dependencies import get_audit_repository

            return get_audit_repository()
        except Exception as e:  # noqa: BLE001
            logger.debug("[APPROVALS] get_audit_repository unavailable: %s", e)
            return None

    def _write_audit(
        self,
        action: str,
        request_doc: Dict[str, Any],
        *,
        actor: str,
        reason: str = "",
    ) -> Optional[str]:
        """Append a hash-chained ``approval_request`` row via
        AuditRepository.create. NEVER includes the PIN value or any bcrypt hash.
        Fail-soft -> None; the lifecycle transition still happens."""
        repo = self._audit_repo()
        if repo is None:
            return None
        log_id = f"AUD-{uuid.uuid4().hex[:12]}"
        # before/after snapshots are PIN-free by construction: request_doc is an
        # approval_requests row (no pin fields). Snapshot only auditable fields.
        snapshot = {
            "request_id": request_doc.get("request_id"),
            "action_type": request_doc.get("action_type"),
            "status": request_doc.get("status"),
            "amount": request_doc.get("amount"),
            "required_tier": request_doc.get("required_tier"),
            "store_id": request_doc.get("store_id"),
            "consumed": request_doc.get("consumed"),
        }
        doc = {
            "log_id": log_id,
            "action": action,
            "entity_type": "approval_request",
            "entity_id": request_doc.get("request_id"),
            "user_id": actor,
            "actor": actor,
            "source": "APPROVALS",
            "action_type": request_doc.get("action_type"),
            "before_state": None,
            "after_state": snapshot,
            "reason": reason or None,
            "severity": "INFO",
            "timestamp": _now(),
        }
        try:
            created = repo.create(doc)
        except Exception as e:  # noqa: BLE001
            logger.warning("[APPROVALS] audit write failed for %s: %s",
                           request_doc.get("request_id"), e)
            return None
        if created is None:
            return None
        return created.get("log_id", log_id)

    def _write_pin_audit(self, action: str, user_id: str, actor: str) -> Optional[str]:
        """Audit a PIN set/clear. The PIN value + hash are NEVER recorded."""
        repo = self._audit_repo()
        if repo is None:
            return None
        log_id = f"AUD-{uuid.uuid4().hex[:12]}"
        doc = {
            "log_id": log_id,
            "action": action,
            "entity_type": "approval_request",
            "entity_id": f"pin:{user_id}",
            "user_id": actor,
            "actor": actor,
            "source": "APPROVALS",
            "before_state": None,
            "after_state": {"pin_for_user": user_id, "has_pin": action == "pin_set"},
            "severity": "INFO",
            "timestamp": _now(),
        }
        try:
            created = repo.create(doc)
        except Exception:  # noqa: BLE001
            return None
        return (created or {}).get("log_id", log_id)


# ============================================================================
# Module helpers
# ============================================================================


def _as_aware(dt: Any) -> datetime:
    """Coerce a stored datetime to a tz-aware UTC datetime for safe comparison.
    Mongo round-trips naive UTC; treat naive as UTC. A missing value sorts as
    'already expired' (epoch)."""
    if dt is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt)
        except Exception:  # noqa: BLE001
            return datetime.min.replace(tzinfo=timezone.utc)
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt
    return datetime.min.replace(tzinfo=timezone.utc)


def _jsonable(doc: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(doc)
    out.pop("_id", None)
    for k in ("created_at", "expires_at", "reviewed_at", "consumed_at",
              "approval_pin_set_at"):
        if k in out:
            out[k] = _iso(out[k])
    return out


def request_approval(
    db,
    *,
    action_type: str,
    requested_by: str,
    requested_by_roles: Optional[List[str]] = None,
    store_id: Optional[str] = None,
    entity_id: Optional[str] = None,
    amount: Optional[float] = None,
    context: Optional[Dict[str, Any]] = None,
    reason: str = "",
    required_tier: Optional[str] = None,
    dedupe_key: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Module-level convenience so a domain router can open a request in one
    call. Fail-soft like create_proposal: returns None when there is no DB (or
    the underlying request could not be recorded); never raises."""
    if db is None:
        return None
    try:
        res = ApprovalEngine(db=db).request(
            action_type=action_type,
            requested_by=requested_by,
            requested_by_roles=requested_by_roles,
            store_id=store_id,
            entity_id=entity_id,
            amount=amount,
            context=context,
            reason=reason,
            required_tier=required_tier,
            dedupe_key=dedupe_key,
        )
        # A no_db / write_failed result is surfaced as None for the fail-soft
        # agent path (matches proposals.create_proposal).
        if not res or not res.get("ok"):
            return None
        return res
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("[APPROVALS] request_approval helper failed: %s", e)
        return None
