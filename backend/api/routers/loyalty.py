"""
IMS 2.0 - Loyalty Router
=========================
Customer points engine — earn / redeem / tier multipliers / expiry sweep.

Endpoints (mounted at /api/v1/loyalty):
  GET    /loyalty/account/{customer_id}        account + recent 20 txns
  GET    /loyalty/account/{customer_id}/ledger paginated full ledger
  POST   /loyalty/earn                         award points for an order
  POST   /loyalty/redeem                       deduct points -> rupee discount
  POST   /loyalty/adjust   (admin only)        manual credit/debit
  GET    /loyalty/settings                     read engine config
  PUT    /loyalty/settings (SUPERADMIN only)   patch engine config
  POST   /loyalty/expire   (cron)              sweep expired EARN rows

Side-effect on order create:
  earn_for_order_internal() is invoked by orders.py inside a try/except
  so a loyalty failure NEVER blocks POS.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .auth import get_current_user
from ..dependencies import (
    get_audit_repository,
    get_loyalty_account_repository,
    get_loyalty_settings_repository,
    get_loyalty_transaction_repository,
    get_order_repository,
)
from ..services.loyalty_engine import (
    calc_earn_points,
    calc_redeem,
    compute_tier,
    expirable_points_by_lot,
    expiry_for_earn,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ============================================================================
# Request / response models
# ============================================================================


class EarnItem(BaseModel):
    item_total: Optional[float] = None
    line_total: Optional[float] = None
    amount: Optional[float] = None
    unit_price: Optional[float] = None
    quantity: Optional[float] = None
    category: Optional[str] = None
    item_type: Optional[str] = None
    product_category: Optional[str] = None


class EarnRequest(BaseModel):
    customer_id: str
    order_id: Optional[str] = None
    rupee_value: float = Field(..., ge=0)
    items: Optional[List[EarnItem]] = None
    reason: Optional[str] = None


class RedeemRequest(BaseModel):
    customer_id: str
    order_id: Optional[str] = None
    points: int = Field(..., gt=0)
    order_value: Optional[float] = Field(None, ge=0)


class AdjustRequest(BaseModel):
    customer_id: str
    points: int  # signed: + credit, - debit
    reason: str = Field(..., min_length=2, max_length=500)


class SettingsPatch(BaseModel):
    enabled: Optional[bool] = None
    points_per_rupee: Optional[float] = Field(None, ge=0)
    category_multipliers: Optional[Dict[str, float]] = None
    min_order_for_earn: Optional[float] = Field(None, ge=0)
    expiry_days: Optional[int] = Field(None, ge=0)
    redeem_rupee_per_point: Optional[float] = Field(None, ge=0)
    min_redeem_points: Optional[int] = Field(None, ge=0)
    max_redeem_pct_of_order: Optional[float] = Field(None, ge=0, le=100)
    tier_thresholds: Optional[Dict[str, int]] = None
    tier_multipliers: Optional[Dict[str, float]] = None


# ============================================================================
# Internal helpers
# ============================================================================


def _is_admin(user: Dict[str, Any]) -> bool:
    roles = user.get("roles", []) or []
    return any(r in roles for r in ("SUPERADMIN", "ADMIN"))


def _is_superadmin(user: Dict[str, Any]) -> bool:
    roles = user.get("roles", []) or []
    return "SUPERADMIN" in roles


def _settings_safe() -> Dict[str, Any]:
    """Safe wrapper -- never raises, always returns a dict (defaults fall in)."""
    try:
        repo = get_loyalty_settings_repository()
        if repo is not None:
            return repo.get()
    except Exception:
        pass
    # No DB -- import the defaults dict directly so callers always get a
    # usable settings shape.
    from database.repositories.loyalty_repository import DEFAULT_SETTINGS

    out: Dict[str, Any] = {}
    for k, v in DEFAULT_SETTINGS.items():
        out[k] = dict(v) if isinstance(v, dict) else v
    return out


def _audit(
    action: str, user: Dict[str, Any], detail: Dict[str, Any], entity_id: str
) -> None:
    repo = get_audit_repository()
    if repo is None:
        return
    try:
        repo.create(
            {
                "action": action,
                "entity_type": "loyalty",
                "entity_id": entity_id,
                "store_id": user.get("active_store_id"),
                "user_id": user.get("user_id"),
                "username": user.get("username"),
                "detail": detail,
            }
        )
    except Exception:
        # audit must never block business logic
        logger.warning("loyalty audit write failed", exc_info=True)


# ============================================================================
# Endpoints
# ============================================================================


@router.get("/account/{customer_id}")
async def get_account(
    customer_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Account snapshot + last 20 ledger rows."""
    accounts = get_loyalty_account_repository()
    txns = get_loyalty_transaction_repository()
    if accounts is None or txns is None:
        # No DB -- empty envelope.
        return {
            "account": {
                "customer_id": customer_id,
                "balance_points": 0,
                "tier": "BRONZE",
                "lifetime_earned": 0,
                "lifetime_redeemed": 0,
            },
            "recent_transactions": [],
            "settings": _settings_safe(),
        }

    account = accounts.find_or_create(customer_id)
    recent = txns.find_for_customer(customer_id, limit=20)
    settings = _settings_safe()

    # Derive expiring-soon points (any EARN with expires_at within 30 days
    # that hasn't been spent / expired).
    now = datetime.now()
    expiring_soon = 0
    for t in recent:
        if t.get("type") != "EARN" or t.get("expired"):
            continue
        exp = t.get("expires_at")
        if not isinstance(exp, datetime):
            continue
        delta = (exp - now).total_seconds()
        if 0 < delta <= 30 * 86400:
            expiring_soon += int(t.get("points") or 0)

    return {
        "account": account,
        "recent_transactions": recent,
        "expiring_soon_points": expiring_soon,
        "settings": settings,
    }


@router.get("/account/{customer_id}/ledger")
async def get_ledger(
    customer_id: str,
    limit: int = Query(50, ge=1, le=500),
    skip: int = Query(0, ge=0),
    type: Optional[str] = Query(None),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Paginated ledger for one customer, newest-first."""
    txns = get_loyalty_transaction_repository()
    if txns is None:
        return {"items": [], "total": 0, "limit": limit, "skip": skip}
    items = txns.find_for_customer(
        customer_id,
        limit=limit,
        skip=skip,
        type_filter=type,
    )
    total = txns.count_for_customer(customer_id, type_filter=type)
    return {"items": items, "total": total, "limit": limit, "skip": skip}


@router.post("/earn")
async def earn(
    body: EarnRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Award loyalty points for an order. Idempotent on (customer, order)."""
    accounts = get_loyalty_account_repository()
    txns = get_loyalty_transaction_repository()
    if accounts is None or txns is None:
        raise HTTPException(status_code=503, detail="Loyalty store unavailable")

    settings = _settings_safe()
    if not settings.get("enabled", True):
        return {"awarded": 0, "skipped_reason": "loyalty_disabled"}

    # Idempotency
    if body.order_id and txns.has_earn_for_order(body.customer_id, body.order_id):
        existing = txns.find_for_customer(body.customer_id, limit=20)
        for t in existing:
            if t.get("order_id") == body.order_id and t.get("type") == "EARN":
                return {
                    "awarded": int(t.get("points") or 0),
                    "txn_id": t.get("txn_id"),
                    "deduped": True,
                }

    account = accounts.find_or_create(body.customer_id)
    items = [i.model_dump(exclude_none=True) for i in (body.items or [])]
    earn_result = calc_earn_points(
        body.rupee_value,
        items,
        account.get("tier", "BRONZE"),
        settings,
    )

    points = int(earn_result.get("points") or 0)
    if points <= 0:
        return {"awarded": 0, "skipped_reason": earn_result.get("skipped_reason")}

    txn_id = str(uuid.uuid4())
    txns.create(
        {
            "txn_id": txn_id,
            "customer_id": body.customer_id,
            "type": "EARN",
            "points": points,
            "rupee_value": float(body.rupee_value or 0.0),
            "order_id": body.order_id,
            "reason": body.reason
            or (f"Order {body.order_id}" if body.order_id else "Loyalty earn"),
            "expires_at": expiry_for_earn(settings),
            "tier_at_earn": earn_result.get("tier_at_earn"),
            "tier_multiplier": earn_result.get("tier_multiplier"),
            "created_by": current_user.get("user_id"),
            "created_at": datetime.now(),
        }
    )

    new_lifetime = int(account.get("lifetime_earned", 0)) + points
    new_tier = compute_tier(new_lifetime, settings)
    accounts.adjust_balance(
        body.customer_id,
        delta_points=points,
        delta_lifetime_earned=points,
        new_tier=new_tier if new_tier != account.get("tier") else None,
    )

    _audit(
        "loyalty.earn",
        current_user,
        {
            "points": points,
            "order_id": body.order_id,
            "rupee_value": body.rupee_value,
            "tier_before": account.get("tier"),
            "tier_after": new_tier,
        },
        body.customer_id,
    )

    return {
        "awarded": points,
        "txn_id": txn_id,
        "tier": new_tier,
        "tier_changed": new_tier != account.get("tier"),
        "rupee_value": body.rupee_value,
    }


@router.post("/redeem")
async def redeem(
    body: RedeemRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Deduct points and return the rupee discount they map to."""
    accounts = get_loyalty_account_repository()
    txns = get_loyalty_transaction_repository()
    if accounts is None or txns is None:
        raise HTTPException(status_code=503, detail="Loyalty store unavailable")

    settings = _settings_safe()
    account = accounts.find_or_create(body.customer_id)

    result = calc_redeem(
        body.points,
        account.get("balance_points", 0),
        body.order_value,
        settings,
    )
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result)

    capped_points = int(result["capped_points"])
    rupee_value = float(result["rupee_value"])

    # ATOMIC GUARDED DEBIT (no double-spend). The Python balance check above
    # (calc_redeem) is advisory only -- two concurrent redeems can both pass it.
    # The authoritative debit is a single find_one_and_update whose FILTER
    # requires balance_points >= capped_points, so only one of two racing
    # redemptions for the same last points can match. On no-match the balance is
    # insufficient (or another redeem won the race) -> 409, and we DON'T write a
    # ledger row, so the immutable ledger never records a redeem that didn't
    # actually decrement the balance.
    debited = accounts.try_debit(
        body.customer_id,
        capped_points,
        delta_lifetime_redeemed=capped_points,
    )
    if debited is None:
        # Re-read for an accurate "available" in the message (best-effort).
        try:
            current = int(
                (accounts.find_by_id(body.customer_id) or {}).get(
                    "balance_points", 0
                )
            )
        except Exception:  # noqa: BLE001
            current = int(account.get("balance_points", 0))
        raise HTTPException(
            status_code=409,
            detail={
                "ok": False,
                "reason": "insufficient_balance",
                "requested_points": capped_points,
                "available_points": current,
                "message": (
                    "Insufficient points balance -- the balance changed before "
                    "this redemption could be applied."
                ),
            },
        )

    txn_id = str(uuid.uuid4())
    txns.create(
        {
            "txn_id": txn_id,
            "customer_id": body.customer_id,
            "type": "REDEEM",
            "points": capped_points,
            "rupee_value": rupee_value,
            "order_id": body.order_id,
            "reason": (
                f"Redeem on order {body.order_id}" if body.order_id else "Manual redeem"
            ),
            "expires_at": None,
            "was_capped": result.get("was_capped", False),
            "created_by": current_user.get("user_id"),
            "created_at": datetime.now(),
        }
    )

    _audit(
        "loyalty.redeem",
        current_user,
        {
            "requested_points": result.get("requested_points"),
            "capped_points": capped_points,
            "rupee_value": rupee_value,
            "order_id": body.order_id,
        },
        body.customer_id,
    )

    return {
        "redeemed_points": capped_points,
        "rupee_value": rupee_value,
        "was_capped": result.get("was_capped", False),
        "txn_id": txn_id,
    }


@router.post("/adjust")
async def adjust(
    body: AdjustRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Manual credit/debit. SUPERADMIN/ADMIN only."""
    if not _is_admin(current_user):
        raise HTTPException(status_code=403, detail="Admin role required")
    if body.points == 0:
        raise HTTPException(status_code=400, detail="points cannot be zero")

    accounts = get_loyalty_account_repository()
    txns = get_loyalty_transaction_repository()
    if accounts is None or txns is None:
        raise HTTPException(status_code=503, detail="Loyalty store unavailable")

    settings = _settings_safe()
    account = accounts.find_or_create(body.customer_id)
    points = int(body.points)

    delta_lifetime_earned = points if points > 0 else 0
    new_lifetime = int(account.get("lifetime_earned", 0)) + delta_lifetime_earned
    new_tier = compute_tier(new_lifetime, settings)
    tier_to_set = new_tier if new_tier != account.get("tier") else None

    updated_account: Optional[Dict[str, Any]] = None
    if points < 0:
        # DEBIT -> atomic guarded decrement (no negative balance / double-spend).
        # Same guard-in-filter as redeem: only succeeds while balance covers it.
        updated_account = accounts.try_debit(
            body.customer_id,
            abs(points),
            new_tier=tier_to_set,
        )
        if updated_account is None:
            raise HTTPException(
                status_code=400,
                detail="cannot debit below zero balance",
            )
    else:
        # CREDIT is safe to apply unconditionally.
        updated_account = accounts.adjust_balance(
            body.customer_id,
            delta_points=points,
            delta_lifetime_earned=delta_lifetime_earned,
            new_tier=tier_to_set,
        )

    # Ledger row only AFTER the balance actually moved -> the immutable ledger
    # never records a debit that didn't decrement the balance.
    txn_id = str(uuid.uuid4())
    txns.create(
        {
            "txn_id": txn_id,
            "customer_id": body.customer_id,
            "type": "ADJUST",
            "points": abs(points),
            "delta": points,  # signed copy for clarity
            "rupee_value": 0.0,
            "order_id": None,
            "reason": body.reason,
            "expires_at": None,
            "created_by": current_user.get("user_id"),
            "created_at": datetime.now(),
        }
    )

    _audit(
        "loyalty.adjust",
        current_user,
        {"delta": points, "reason": body.reason},
        body.customer_id,
    )

    # Prefer the authoritative post-update balance; fall back to the snapshot.
    if isinstance(updated_account, dict) and "balance_points" in updated_account:
        balance_after = int(updated_account.get("balance_points", 0))
    else:
        balance_after = int(account.get("balance_points", 0)) + points

    return {
        "txn_id": txn_id,
        "delta": points,
        "balance_after": balance_after,
        "tier": new_tier,
    }


@router.get("/program-stats")
async def program_stats(current_user: Dict[str, Any] = Depends(get_current_user)):
    """Chain-wide loyalty program summary: total members, tier mix, and points
    issued / redeemed / outstanding with the redemption rate.

    Loyalty accounts are global (no store_id), so this reflects the whole
    program. Aggregated in a single pass over loyalty_accounts; fail-soft to an
    empty envelope when the store is unavailable.
    """
    empty: Dict[str, Any] = {
        "total_members": 0,
        "by_tier": {},
        "active_points_balance": 0,
        "points_issued": 0,
        "points_redeemed": 0,
        "redemption_rate": 0.0,
        "avg_points_per_member": 0,
    }
    repo = get_loyalty_account_repository()
    if repo is None:
        return empty
    try:
        pipeline = [
            {
                "$facet": {
                    "totals": [
                        {
                            "$group": {
                                "_id": None,
                                "total_members": {"$sum": 1},
                                "active_points_balance": {
                                    "$sum": {"$ifNull": ["$balance_points", 0]}
                                },
                                "points_issued": {
                                    "$sum": {"$ifNull": ["$lifetime_earned", 0]}
                                },
                                "points_redeemed": {
                                    "$sum": {"$ifNull": ["$lifetime_redeemed", 0]}
                                },
                            }
                        }
                    ],
                    "by_tier": [
                        {
                            "$group": {
                                "_id": {"$ifNull": ["$tier", "BRONZE"]},
                                "count": {"$sum": 1},
                            }
                        }
                    ],
                }
            }
        ]
        agg = list(repo.collection.aggregate(pipeline))
    except Exception:
        logger.warning("loyalty program-stats aggregation failed", exc_info=True)
        return empty

    if not agg:
        return empty
    facet = agg[0]
    totals_list = facet.get("totals") or []
    totals = totals_list[0] if totals_list else {}
    by_tier = {
        str(row.get("_id") or "BRONZE").upper(): int(row.get("count", 0) or 0)
        for row in (facet.get("by_tier") or [])
    }
    total_members = int(totals.get("total_members", 0) or 0)
    active_balance = int(totals.get("active_points_balance", 0) or 0)
    issued = int(totals.get("points_issued", 0) or 0)
    redeemed = int(totals.get("points_redeemed", 0) or 0)
    return {
        "total_members": total_members,
        "by_tier": by_tier,
        "active_points_balance": active_balance,
        "points_issued": issued,
        "points_redeemed": redeemed,
        "redemption_rate": round(redeemed / issued * 100, 1) if issued else 0.0,
        "avg_points_per_member": (
            round(active_balance / total_members) if total_members else 0
        ),
    }


@router.get("/settings")
async def read_settings(current_user: Dict[str, Any] = Depends(get_current_user)):
    return _settings_safe()


@router.put("/settings")
async def write_settings(
    body: SettingsPatch,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    if not _is_superadmin(current_user):
        raise HTTPException(status_code=403, detail="SUPERADMIN required")
    repo = get_loyalty_settings_repository()
    if repo is None:
        raise HTTPException(status_code=503, detail="Loyalty store unavailable")
    patch = body.model_dump(exclude_unset=True)
    if not patch:
        return _settings_safe()
    merged = repo.update(patch)
    _audit("loyalty.settings.update", current_user, {"patch": patch}, "settings")
    return merged


@router.post("/expire")
async def expire_sweep(
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Cron-style sweep: for every EARN row whose expires_at <= now, write a
    balancing EXPIRE row for the lot's UNSPENT remainder, mark the source EARN
    as expired, and decrement balance_points.

    P2-C fix: the old code expired ``min(lot.points, account_balance)`` per lot.
    The account balance can belong to NEWER, non-expired lots, so a customer who
    earned an old (now-expiring) lot, spent it, then earned a fresh lot would
    have the FRESH lot's points destroyed when the old lot expired. We now use
    per-lot FIFO (``expirable_points_by_lot``): redemptions consume the oldest
    lots first, so an expired lot only sheds the points it still holds -- newer
    lots are never touched.
    """
    if not _is_admin(current_user):
        raise HTTPException(status_code=403, detail="Admin role required")
    txns = get_loyalty_transaction_repository()
    accounts = get_loyalty_account_repository()
    if txns is None or accounts is None:
        return {"expired_txns": 0, "points_expired": 0}

    now = datetime.now()
    candidates = txns.find_expired_unprocessed(now)

    by_customer: Dict[str, List[Dict[str, Any]]] = {}
    for c in candidates:
        cid = c.get("customer_id")
        if cid:
            by_customer.setdefault(cid, []).append(c)

    expired_txns = 0
    total_points = 0

    for customer_id, rows in by_customer.items():
        # Per-lot FIFO over the customer's FULL ledger decides how many points
        # each expired lot may shed (its unspent remainder), so we never expire
        # points that were already redeemed or belong to a newer, valid lot.
        ledger = txns.find_for_customer(customer_id, limit=100000)
        expirable_by_lot = expirable_points_by_lot(ledger, now)

        for row in rows:
            lot_id = row.get("txn_id")
            # Always mark the lot processed so a future sweep skips it (even when
            # it has nothing left to shed -- it was fully spent).
            txns.mark_expired(lot_id)
            expirable = int(expirable_by_lot.get(lot_id, 0))
            if expirable <= 0:
                continue
            txn_id = str(uuid.uuid4())
            txns.create(
                {
                    "txn_id": txn_id,
                    "customer_id": customer_id,
                    "type": "EXPIRE",
                    "points": expirable,
                    "rupee_value": 0.0,
                    "order_id": None,
                    "reason": f"Auto-expire of {lot_id}",
                    "source_earn_txn_id": lot_id,
                    "expires_at": None,
                    "created_by": current_user.get("user_id"),
                    "created_at": datetime.now(),
                }
            )
            accounts.adjust_balance(customer_id, delta_points=-expirable)
            expired_txns += 1
            total_points += expirable

    _audit(
        "loyalty.expire",
        current_user,
        {"expired_txns": expired_txns, "points_expired": total_points},
        "system",
    )
    return {"expired_txns": expired_txns, "points_expired": total_points}


# ============================================================================
# Internal hook -- called by orders.py
# ============================================================================


def earn_for_order_internal(
    customer_id: Optional[str],
    order_id: str,
    items: Optional[List[Dict[str, Any]]],
    rupee_value: float,
    user_id: Optional[str] = None,
    store_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Fire-and-forget earn invocation from the order-create path.

    Always returns a dict — never raises. Wrapped in try/except by the
    caller anyway, but every internal call is also caught here so we
    never bubble a stack trace to the order response.
    """
    if not customer_id:
        return {"awarded": 0, "skipped_reason": "no_customer"}
    try:
        accounts = get_loyalty_account_repository()
        txns = get_loyalty_transaction_repository()
        if accounts is None or txns is None:
            return {"awarded": 0, "skipped_reason": "no_db"}

        settings = _settings_safe()
        if not settings.get("enabled", True):
            return {"awarded": 0, "skipped_reason": "loyalty_disabled"}

        if order_id and txns.has_earn_for_order(customer_id, order_id):
            return {"awarded": 0, "skipped_reason": "already_earned"}

        account = accounts.find_or_create(customer_id)
        result = calc_earn_points(
            rupee_value,
            items or [],
            account.get("tier", "BRONZE"),
            settings,
        )
        points = int(result.get("points") or 0)
        if points <= 0:
            return {"awarded": 0, "skipped_reason": result.get("skipped_reason")}

        txn_id = str(uuid.uuid4())
        txns.create(
            {
                "txn_id": txn_id,
                "customer_id": customer_id,
                "type": "EARN",
                "points": points,
                "rupee_value": float(rupee_value or 0.0),
                "order_id": order_id,
                "reason": f"Order {order_id}",
                "expires_at": expiry_for_earn(settings),
                "tier_at_earn": result.get("tier_at_earn"),
                "tier_multiplier": result.get("tier_multiplier"),
                "store_id": store_id,
                "created_by": user_id,
                "created_at": datetime.now(),
            }
        )
        new_lifetime = int(account.get("lifetime_earned", 0)) + points
        new_tier = compute_tier(new_lifetime, settings)
        accounts.adjust_balance(
            customer_id,
            delta_points=points,
            delta_lifetime_earned=points,
            new_tier=new_tier if new_tier != account.get("tier") else None,
        )
        return {"awarded": points, "txn_id": txn_id, "tier": new_tier}
    except Exception as exc:
        logger.warning("earn_for_order_internal failed: %s", exc)
        return {"awarded": 0, "skipped_reason": "error", "error": str(exc)}
