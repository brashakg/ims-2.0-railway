"""
IMS 2.0 - Loyalty-redemption OTP (customer-present verification)
================================================================
Owner ruling 2026-08-30 (final): OTP gates LOYALTY-POINTS REDEMPTION ONLY -
never customer creation. The customer's own mobile receives a one-time code,
staff enter it at the POS, the server verifies, and only then do points
release. Stops anyone redeeming a customer's points without the customer
present.

THE gate (one implementation - do not re-implement anywhere else):
    redeem_otp_required(store_id) is True only when BOTH hold:
      * policy msg.loyalty_otp resolves to "on" (registered in
        policy_registry; default off; store-scopable), AND
      * the dispatch gate is armed (DISPATCH_MODE test|live).
    Dark (DISPATCH_MODE=off/unset) or policy-off -> False, and the redeem
    door behaves exactly as it did before this module existed.

Verification is LOCAL: IMS generates the code, stores only a salted SHA-256
hash, and verifies the staff-entered code itself. MSG91 is transport only
(providers.send_otp passes our code to the MSG91 OTP API); nothing about
correctness depends on MSG91, so the whole flow is provable dark via the
SIMULATED path.

Challenge lifecycle (collection: loyalty_otp_challenges):
    PENDING   - code sent, waiting for staff to enter it (5 min window,
                max 5 wrong attempts)
    VERIFIED  - staff entered the right code; valid for 15 min so a normal
                checkout (order create -> deferred redeem) fits inside it
    USED      - consumed by exactly one successful redemption
Every transition is a single guarded find_one_and_update, mirroring the
vouchers.redeem_voucher_atomic house shape: no read-modify-write window, a
challenge can never be consumed twice, and the redeem door refuses BEFORE
any points are debited when no consumable challenge exists.

Fail-soft direction: a policy-engine/DB hiccup makes the gate read "off"
(today's behaviour), never "on" - this is an anti-fraud courtesy gate and a
config hiccup must not block POS revenue. No emoji (Windows cp1252).
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import time
import uuid
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

CODE_TTL_SECONDS = 5 * 60        # PENDING: staff must enter the code within this
VERIFIED_TTL_SECONDS = 15 * 60   # VERIFIED: checkout must finish within this
MAX_ATTEMPTS = 5                 # wrong entries before the challenge dies

_COLLECTION = "loyalty_otp_challenges"


def _coll():
    """The challenges collection, or None when the DB is unreachable."""
    try:
        from database.connection import get_db

        db = get_db()
        if db is None or not getattr(db, "is_connected", False):
            return None
        return db.get_collection(_COLLECTION)
    except Exception:  # noqa: BLE001 - fail-soft, callers handle None
        return None


def _hash(challenge_id: str, code: str) -> str:
    """Salted hash so the stored doc never contains the code itself."""
    return hashlib.sha256(f"{challenge_id}:{code}".encode("utf-8")).hexdigest()


def redeem_otp_required(store_id: Optional[str] = None) -> bool:
    """True when redeeming loyalty points requires the customer's OTP.

    Both switches must be on: policy msg.loyalty_otp == "on" (store-scoped
    resolution) AND DISPATCH_MODE armed (test|live). While dark, this is
    False regardless of the policy, so an unarmed deployment is untouched.
    """
    from agents.providers import dispatch_mode

    if dispatch_mode() not in ("test", "live"):
        return False
    try:
        from api.services.policy_engine import get_policy

        scope = {"store_id": store_id} if store_id else None
        value = get_policy("msg.loyalty_otp", scope, default="off")
        return str(value or "off").strip().lower() == "on"
    except Exception:  # noqa: BLE001 - fail-soft to today's behaviour
        return False


async def start_challenge(
    customer_id: str, mobile: str, created_by: Optional[str] = None
) -> Dict[str, Any]:
    """Create a PENDING challenge for `customer_id` and send the code to
    `mobile` (the customer's number as stored in IMS - never client-supplied).

    Returns {ok, challenge_id, send_status, expires_in_seconds, error}.
    ok=False only when the DB is unavailable or the armed send FAILED; the
    SIMULATED dark path is ok=True (it proves the payload shape).
    The plain code exists only in this function's locals and the provider
    call - it is never stored, logged, or returned.
    """
    coll = _coll()
    if coll is None:
        return {"ok": False, "error": "unavailable", "send_status": None}

    challenge_id = str(uuid.uuid4())
    code = f"{secrets.randbelow(900000) + 100000}"  # 6 digits, 100000-999999
    now = time.time()
    coll.insert_one(
        {
            "challenge_id": challenge_id,
            "customer_id": customer_id,
            "code_hash": _hash(challenge_id, code),
            "status": "PENDING",
            "attempts": 0,
            "created_at": now,
            "expires_at": now + CODE_TTL_SECONDS,
            "created_by": created_by,
        }
    )

    from agents import providers

    result = await providers.send_otp(
        mobile, code, expiry_minutes=CODE_TTL_SECONDS // 60
    )
    if not result.ok:
        # Armed send failed -> the customer never got a code; report honestly.
        return {
            "ok": False,
            "challenge_id": challenge_id,
            "send_status": result.status,
            "error": result.error,
        }
    return {
        "ok": True,
        "challenge_id": challenge_id,
        "send_status": result.status,
        "expires_in_seconds": CODE_TTL_SECONDS,
    }


def verify_challenge(customer_id: str, code: str) -> Tuple[bool, str]:
    """Check a staff-entered code against the customer's latest PENDING
    challenge. On success the challenge flips PENDING -> VERIFIED (atomic)
    and its window extends to VERIFIED_TTL_SECONDS so the checkout that
    follows fits inside it. Returns (ok, plain-English reason).
    """
    coll = _coll()
    if coll is None:
        return False, "Verification store unavailable - try again."

    now = time.time()
    # Latest PENDING challenge for this customer (the code just sent).
    candidates = list(coll.find({"customer_id": customer_id, "status": "PENDING"}))
    if not candidates:
        return False, "No code has been sent - send a code to the customer first."
    doc = max(candidates, key=lambda d: d.get("created_at") or 0)
    challenge_id = doc.get("challenge_id")

    # ATOMIC verify: filter carries expiry, attempt cap AND the code hash, so
    # only the right code inside the window can flip the status - and only
    # once (Mongo matches-and-modifies a doc atomically).
    flipped = coll.find_one_and_update(
        {
            "challenge_id": challenge_id,
            "status": "PENDING",
            "expires_at": {"$gt": now},
            "attempts": {"$lt": MAX_ATTEMPTS},
            "code_hash": _hash(str(challenge_id), str(code or "").strip()),
        },
        {
            "$set": {
                "status": "VERIFIED",
                "verified_at": now,
                "expires_at": now + VERIFIED_TTL_SECONDS,
            }
        },
        return_document=True,
    )
    if flipped is not None:
        return True, "verified"

    # Wrong / expired / attempt-capped: burn one attempt (best-effort) and
    # answer with the precise plain reason. Nothing was spent.
    if (doc.get("expires_at") or 0) <= now:
        return False, "That code has expired - send a fresh code."
    if int(doc.get("attempts") or 0) >= MAX_ATTEMPTS:
        return False, "Too many wrong attempts - send a fresh code."
    try:
        coll.update_one(
            {"challenge_id": challenge_id, "status": "PENDING"},
            {"$inc": {"attempts": 1}},
        )
    except Exception:  # noqa: BLE001 - attempt bookkeeping is best-effort
        pass
    return False, "That code is not correct - check with the customer and retry."


def consume_verified(customer_id: str) -> bool:
    """Atomically consume ONE VERIFIED, unexpired challenge for the customer.

    Called by the redeem door BEFORE any points are debited. A single
    guarded find_one_and_update (VERIFIED -> USED) means one verification
    releases exactly one redemption - two racing redeems cannot both consume
    the same challenge. Returns False when nothing consumable exists (the
    redeem then refuses with points untouched).
    """
    coll = _coll()
    if coll is None:
        return False
    used = coll.find_one_and_update(
        {
            "customer_id": customer_id,
            "status": "VERIFIED",
            "expires_at": {"$gt": time.time()},
        },
        {"$set": {"status": "USED", "used_at": time.time()}},
        return_document=True,
    )
    return used is not None
