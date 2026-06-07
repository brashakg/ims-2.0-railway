"""
IMS 2.0 - Vouchers / Gift-Card Router
=====================================
Real database-backed gift-card and discount-voucher engine.

This handles MONEY, so the redeem path is built to be concurrency-safe:
every balance decrement is a single atomic find_one_and_update whose FILTER
itself encodes the spend guard (status ACTIVE, sufficient balance, not
expired). Two cashiers redeeming the same card at the same instant can
never overspend it — at most one of the two racing updates matches the
guard for the last available rupees; the other matches nothing and is
rejected with a 400.

Collection: vouchers
Document shape:
  { voucher_id, code (UNIQUE, uppercased), type ("GIFT_CARD"|"DISCOUNT"),
    initial_amount: float, balance: float, currency: "INR",
    status: "ACTIVE"|"REDEEMED"|"EXPIRED"|"CANCELLED",
    store_id, issued_to_customer_id, issued_by, expiry_date (ISO date|None),
    redemptions: [ {order_id, amount, redeemed_at, redeemed_by} ],
    created_at, updated_at }

Mounted at /api/v1/vouchers (see api/main.py).

Stage 2: the atomic redeem is exposed as a reusable module-level function
`redeem_voucher_atomic(db, code, amount, order_id, redeemed_by)` so the POS
payment path (orders.add_payment, GIFT_VOUCHER branch) can decrement a card
at the moment a payment is actually recorded — no logic duplication. The
HTTP /{code}/redeem endpoint calls the very same function.
"""

from __future__ import annotations

import logging
import secrets
import uuid
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from pymongo import ReturnDocument

from .auth import get_current_user, require_roles
from ..dependencies import resolve_store_scope

logger = logging.getLogger(__name__)
router = APIRouter()

# Roles permitted to issue / list / cancel vouchers. Mirrors the finance
# approval family; SUPERADMIN auto-passes inside require_roles.
_ADMIN_ROLES = ("ADMIN", "AREA_MANAGER", "STORE_MANAGER", "ACCOUNTANT")
# Roles permitted to redeem at the POS. Cashiers + sales staff + managers.
_REDEEM_ROLES = (
    "SALES_CASHIER",
    "SALES_STAFF",
    "CASHIER",
    "STORE_MANAGER",
    "AREA_MANAGER",
    "ADMIN",
)
# Roles permitted to cancel an active voucher.
_CANCEL_ROLES = ("ADMIN", "AREA_MANAGER", "STORE_MANAGER")

# Base32-ish alphabet for human-readable codes: no 0/1/O/I to avoid
# transcription ambiguity at the counter.
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_CODE_BODY_LEN = 8
_MAX_CODE_RETRIES = 6


# ============================================================================
# DB helper
# ============================================================================


def _get_db():
    """Return the live pymongo Database, or None when unavailable.

    Standard house pattern. get_db().db is None in stub/DB-less mode, in
    which case every endpoint fails soft rather than 500-ing.
    """
    from database.connection import get_db

    return get_db().db


def _coll(db):
    """vouchers collection accessor that tolerates either a raw pymongo
    Database (db["vouchers"]) or a wrapper exposing get_collection()."""
    if db is None:
        return None
    getter = getattr(db, "get_collection", None)
    if callable(getter):
        return getter("vouchers")
    return db["vouchers"]


# ============================================================================
# Schemas
# ============================================================================


class VoucherCreate(BaseModel):
    amount: float = Field(..., gt=0)
    type: str = "GIFT_CARD"
    expiry_date: Optional[date] = None
    customer_id: Optional[str] = None
    store_id: Optional[str] = None
    code: Optional[str] = None


class VoucherRedeem(BaseModel):
    amount: float = Field(..., gt=0)
    order_id: Optional[str] = None


# ============================================================================
# Internal helpers
# ============================================================================


def _today_iso() -> str:
    return date.today().isoformat()


def _generate_code() -> str:
    body = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_BODY_LEN))
    return f"GC-{body}"


def _is_expired(expiry_date: Optional[str], today_iso: Optional[str] = None) -> bool:
    """True when expiry_date (ISO date str) is strictly before today.

    A card expiring *today* is still redeemable (expiry is end-of-day).
    """
    if not expiry_date:
        return False
    today_iso = today_iso or _today_iso()
    # ISO date strings are lexicographically ordered, so a string compare
    # is correct and avoids parsing. Guard against datetime-style values.
    return str(expiry_date)[:10] < today_iso


def _public_view(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Strip Mongo's _id and return a JSON-safe voucher document."""
    if not doc:
        return {}
    out = dict(doc)
    out.pop("_id", None)
    return out


def _ensure_code_index(coll) -> None:
    """Best-effort unique index on `code`. The issue endpoint is also
    collision-safe via regeneration, so a missing index never breaks
    correctness — it's defense in depth against a duplicate slipping in
    under concurrency. Never raises."""
    try:
        coll.create_index("code", unique=True)
    except Exception:
        logger.debug("voucher code index create skipped", exc_info=True)


# ============================================================================
# Core redeem engine (reusable — POS payment path calls this too)
# ============================================================================


def redeem_voucher_atomic(
    db: Any,
    code: str,
    amount: float,
    order_id: Optional[str] = None,
    redeemed_by: Optional[str] = None,
) -> Dict[str, Any]:
    """Atomically decrement `amount` off a voucher's balance.

    This is the single source of truth for voucher spend. Both the HTTP
    POST /{code}/redeem endpoint and the POS payment path
    (orders.add_payment, GIFT_VOUCHER branch) call it, so the spend rules
    live in exactly one place.

    Concurrency safety: a SINGLE find_one_and_update does the guard AND the
    decrement in one indivisible operation. The filter requires status
    ACTIVE, balance >= amount, and (no expiry OR expiry >= today). Mongo
    guarantees only one writer can match-and-modify a given document at a
    time, so two simultaneous redemptions that together exceed the balance
    cannot both succeed — the second finds no matching document and fails.
    No read-modify-write window, no double-spend. Flips to REDEEMED when
    fully drained.

    Returns a plain dict (never raises for a business-rule failure):
      {"ok": bool, "balance": float, "status": str|None, "reason": str|None}
    `reason` is a human-readable message on failure (None on success). The
    caller decides how to surface it (HTTP 400, etc.). `ok` is False with
    reason "unavailable" when the DB is absent.
    """
    coll = _coll(db)
    if coll is None:
        return {"ok": False, "balance": 0.0, "status": None, "reason": "unavailable"}

    code_u = (code or "").strip().upper()
    amount = float(amount)
    now = datetime.now().isoformat()

    redemption = {
        "order_id": order_id,
        "amount": amount,
        "redeemed_at": now,
        "redeemed_by": redeemed_by,
    }

    # E1: the guarded mutation is funnelled through the money-guard engine -- a
    # single atomic find_one_and_update + an append-only audit row. The engine owns
    # the voucher spend semantics (status ACTIVE + not-expired, same date basis);
    # the shim passes only the redemptions push + updated_at so the on-disk filter/
    # update are identical to the historical path. record_ledger=False keeps the
    # voucher document shape unchanged (Phase A).
    from api.services import money_guard

    res = money_guard.debit(
        coll, "GIFT_VOUCHER", code_u, amount,
        reason="redeem", actor=redeemed_by, ref=order_id,
        set_extra={"updated_at": now},
        push_extra={"redemptions": redemption},
        record_ledger=False,
    )

    if not res.ok:
        # Disambiguate the failure with a follow-up read so the caller can
        # show a precise message. This read is purely for messaging — the
        # atomic guard above already prevented any spend.
        return {
            "ok": False,
            "balance": 0.0,
            "status": None,
            "reason": _redeem_failure_reason(coll, code_u, amount),
        }

    new_balance = res.balance
    status = res.status

    # If fully drained, flip to REDEEMED. Re-guard on balance<=0 and the
    # current status so a concurrent partial redeem can't be clobbered.
    if new_balance <= 0:
        flipped = coll.find_one_and_update(
            {"code": code_u, "status": "ACTIVE", "balance": {"$lte": 0}},
            {"$set": {"status": "REDEEMED", "updated_at": datetime.now().isoformat()}},
            return_document=ReturnDocument.AFTER,
        )
        if flipped is not None:
            status = flipped.get("status")
            new_balance = float(flipped.get("balance") or 0.0)

    return {"ok": True, "balance": new_balance, "status": status, "reason": None}


def _redeem_failure_reason(coll, code_u: str, amount: float) -> str:
    """Read the voucher post-failure to produce a human reason for the 400.

    Pure messaging helper — does not mutate anything.
    """
    doc = coll.find_one({"code": code_u})
    if not doc:
        return "Voucher not found"
    status = doc.get("status")
    if status == "CANCELLED":
        return "Voucher has been cancelled"
    if status == "REDEEMED":
        return "Voucher already fully redeemed"
    if status == "EXPIRED" or _is_expired(doc.get("expiry_date")):
        return "Voucher has expired"
    if status != "ACTIVE":
        return f"Voucher is not active ({status})"
    if float(doc.get("balance") or 0.0) < amount:
        return (
            f"Insufficient balance: requested {amount:.2f}, "
            f"available {float(doc.get('balance') or 0.0):.2f}"
        )
    # Status ACTIVE, balance sufficient, not expired -> a concurrent
    # redemption likely won the race for the last rupees.
    return "Voucher could not be redeemed; please re-check the balance"


# ============================================================================
# Endpoints
# ============================================================================


@router.post("")
@router.post("/")
async def issue_voucher(
    body: VoucherCreate,
    current_user: Dict[str, Any] = Depends(require_roles(*_ADMIN_ROLES)),
):
    """Issue a new gift card / discount voucher.

    balance starts at the full amount; status ACTIVE. If no code is given
    a unique GC-XXXXXXXX code is generated, retrying on the (rare) chance
    of a collision against the unique index.
    """
    db = _get_db()
    coll = _coll(db)
    if coll is None:
        return {}

    _ensure_code_index(coll)

    vtype = (body.type or "GIFT_CARD").upper()
    if vtype not in ("GIFT_CARD", "DISCOUNT"):
        raise HTTPException(
            status_code=400, detail="type must be GIFT_CARD or DISCOUNT"
        )

    now = datetime.now().isoformat()
    amount = float(body.amount)
    base_doc = {
        "voucher_id": str(uuid.uuid4()),
        "type": vtype,
        "initial_amount": amount,
        "balance": amount,
        "currency": "INR",
        "status": "ACTIVE",
        "store_id": body.store_id or current_user.get("active_store_id"),
        "issued_to_customer_id": body.customer_id,
        "issued_by": current_user.get("user_id"),
        "expiry_date": body.expiry_date.isoformat() if body.expiry_date else None,
        "redemptions": [],
        "created_at": now,
        "updated_at": now,
    }

    # Caller-supplied code: uppercase, single insert, surface a clean 409
    # on collision rather than a raw Mongo error.
    if body.code:
        doc = dict(base_doc, code=body.code.strip().upper())
        try:
            coll.insert_one(doc)
        except Exception as exc:
            if _is_dup_key(exc):
                raise HTTPException(
                    status_code=409, detail="Voucher code already exists"
                ) from exc
            raise
        return _public_view(doc)

    # Generated code: retry on duplicate-key until we land a free one.
    last_exc: Optional[Exception] = None
    for _ in range(_MAX_CODE_RETRIES):
        doc = dict(base_doc, code=_generate_code())
        try:
            coll.insert_one(doc)
            return _public_view(doc)
        except Exception as exc:  # pylint: disable=broad-except
            if _is_dup_key(exc):
                last_exc = exc
                # New voucher_id too, so a partial-unique setup can't loop.
                base_doc["voucher_id"] = str(uuid.uuid4())
                continue
            raise
    raise HTTPException(
        status_code=500,
        detail="Could not generate a unique voucher code; please retry",
    ) from last_exc


@router.get("/{code}")
async def get_voucher(
    code: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Validate / look up a voucher by code (case-insensitive).

    Any authenticated staff member can call this — cashiers validate cards
    at the POS before applying them. `valid` is False with a `reason` when
    the card is missing, not ACTIVE, expired, or drained.
    """
    db = _get_db()
    coll = _coll(db)
    if coll is None:
        return {"valid": False, "code": code.upper(), "reason": "unavailable"}

    doc = coll.find_one({"code": code.strip().upper()})
    if not doc:
        return {"valid": False, "code": code.strip().upper(), "reason": "not_found"}

    status = doc.get("status")
    balance = float(doc.get("balance") or 0.0)
    expiry = doc.get("expiry_date")

    reason: Optional[str] = None
    if status != "ACTIVE":
        reason = "cancelled" if status == "CANCELLED" else status.lower()
    elif _is_expired(expiry):
        reason = "expired"
    elif balance <= 0:
        reason = "no_balance"

    return {
        "valid": reason is None,
        "code": doc.get("code"),
        "balance": balance,
        "status": status,
        "expiry_date": expiry,
        "type": doc.get("type"),
        **({"reason": reason} if reason else {}),
    }


@router.post("/{code}/redeem")
async def redeem_voucher(
    code: str,
    body: VoucherRedeem,
    current_user: Dict[str, Any] = Depends(require_roles(*_REDEEM_ROLES)),
):
    """Atomically redeem `amount` off a voucher's balance.

    Thin HTTP wrapper over `redeem_voucher_atomic` — all spend rules and
    concurrency safety live there. A business-rule failure (insufficient
    balance, expired, cancelled, drained, lost race) becomes a 400 with the
    engine's human-readable reason; a missing DB becomes a 503.
    """
    db = _get_db()
    if _coll(db) is None:
        raise HTTPException(status_code=503, detail="Voucher store unavailable")

    code_u = code.strip().upper()
    result = redeem_voucher_atomic(
        db,
        code_u,
        float(body.amount),
        order_id=body.order_id,
        redeemed_by=current_user.get("user_id"),
    )

    if not result["ok"]:
        reason = result.get("reason")
        if reason == "unavailable":
            raise HTTPException(status_code=503, detail="Voucher store unavailable")
        raise HTTPException(status_code=400, detail=reason)

    return {
        "code": code_u,
        "redeemed": float(body.amount),
        "balance": result["balance"],
        "status": result["status"],
    }


@router.get("")
@router.get("/")
async def list_vouchers(
    store_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    current_user: Dict[str, Any] = Depends(require_roles(*_ADMIN_ROLES)),
):
    """List vouchers with optional store_id / status filters."""
    db = _get_db()
    coll = _coll(db)
    if coll is None:
        return {"vouchers": [], "total": 0}

    # BUG-062 tail: scope vouchers to the caller's store reach.
    store_id = resolve_store_scope(store_id, current_user)
    query: Dict[str, Any] = {}
    if store_id:
        query["store_id"] = store_id
    if status:
        query["status"] = status.upper()

    docs = list(coll.find(query).sort("created_at", -1))
    vouchers = [_public_view(d) for d in docs]
    return {"vouchers": vouchers, "total": len(vouchers)}


@router.post("/{code}/cancel")
async def cancel_voucher(
    code: str,
    current_user: Dict[str, Any] = Depends(require_roles(*_CANCEL_ROLES)),
):
    """Cancel a voucher. Only an ACTIVE voucher can be cancelled — the
    status guard is in the filter so this is itself race-safe (you can't
    cancel one that's mid-redemption-to-REDEEMED)."""
    db = _get_db()
    coll = _coll(db)
    if coll is None:
        raise HTTPException(status_code=503, detail="Voucher store unavailable")

    code_u = code.strip().upper()
    updated = coll.find_one_and_update(
        {"code": code_u, "status": "ACTIVE"},
        {
            "$set": {
                "status": "CANCELLED",
                "cancelled_by": current_user.get("user_id"),
                "cancelled_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
            }
        },
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        existing = coll.find_one({"code": code_u})
        if not existing:
            raise HTTPException(status_code=404, detail="Voucher not found")
        raise HTTPException(
            status_code=400,
            detail=f"Only ACTIVE vouchers can be cancelled (current: {existing.get('status')})",
        )
    return _public_view(updated)


# ============================================================================
# Misc
# ============================================================================


def _is_dup_key(exc: Exception) -> bool:
    """True if the exception is a Mongo duplicate-key error (E11000).

    Imported lazily so the module loads even if pymongo's errors module
    layout shifts; falls back to a string sniff.
    """
    try:
        from pymongo.errors import DuplicateKeyError

        if isinstance(exc, DuplicateKeyError):
            return True
    except Exception:  # pylint: disable=broad-except
        pass
    return "e11000" in str(exc).lower() or "duplicate key" in str(exc).lower()
