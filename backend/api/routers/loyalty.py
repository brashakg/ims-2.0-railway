"""
IMS 2.0 - Loyalty Router
=========================
Customer points engine — earn / redeem / tier multipliers / expiry sweep.

Endpoints (mounted at /api/v1/loyalty):
  GET    /loyalty/account/{customer_id}        account + recent 20 txns
  GET    /loyalty/account/{customer_id}/ledger paginated full ledger
  POST   /loyalty/earn     (POS roles)         award points for an order --
                                               value derived from the order
  POST   /loyalty/redeem   (POS roles)         deduct points -> rupee discount
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
from typing import Any, Dict, List, Literal, Optional
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .auth import get_current_user, require_roles
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

# Roles permitted to move points at the POS (earn on an order / redeem as a
# tender). Points are MONEY, so this mirrors the POS payment family exactly:
# vouchers._REDEEM_ROLES (gift-card redeem at payment time) and the POST
# /api/v1/orders policy row. SUPERADMIN auto-passes inside require_roles.
# Clinical / workshop / catalog / accounting roles are NOT in the set -- a
# manual no-order credit is POST /loyalty/adjust (ADMIN/SUPERADMIN only).
_POS_ROLES = (
    "ADMIN",
    "AREA_MANAGER",
    "STORE_MANAGER",
    "SALES_CASHIER",
    "SALES_STAFF",
    "CASHIER",
)


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
    # order_id is REQUIRED by the route (400 without it) -- kept Optional in the
    # schema only so the error is a clean 400 pointing at /loyalty/adjust
    # rather than a generic 422.
    order_id: Optional[str] = None
    # Client value is ADVISORY only: the authoritative earn basis is derived
    # server-side from the order. A supplied value may only LOWER the basis
    # (partial award); anything above the order's value is clamped down.
    rupee_value: Optional[float] = Field(None, ge=0)
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
    current_user: Dict[str, Any] = Depends(require_roles(*_POS_ROLES)),
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
    current_user: Dict[str, Any] = Depends(require_roles(*_POS_ROLES)),
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
    current_user: Dict[str, Any] = Depends(require_roles(*_POS_ROLES)),
):
    """Award loyalty points for an order. Idempotent on (customer, order).

    IDOR/value-trust hardening: points are money, so the earn basis is
    derived from the ORDER (grand_total - tax_amount, i.e. the taxable value
    after all discounts -- exactly what orders.create_order passes to
    earn_for_order_internal), never trusted from the client. order_id is
    REQUIRED; a no-order manual credit is POST /loyalty/adjust (admin-gated).
    A client rupee_value may only LOWER the basis; an inflated value is
    clamped to the order's, so no caller can mint more points than the order
    supports.
    """
    accounts = get_loyalty_account_repository()
    txns = get_loyalty_transaction_repository()
    if accounts is None or txns is None:
        raise HTTPException(status_code=503, detail="Loyalty store unavailable")

    settings = _settings_safe()
    if not settings.get("enabled", True):
        return {"awarded": 0, "skipped_reason": "loyalty_disabled"}

    if not body.order_id:
        raise HTTPException(
            status_code=400,
            detail=(
                "order_id is required: earn points are derived from the "
                "order's value. Use POST /loyalty/adjust (admin) for a "
                "manual credit."
            ),
        )

    orders = get_order_repository()
    if orders is None:
        raise HTTPException(status_code=503, detail="Order store unavailable")
    order_doc = orders.find_by_id(body.order_id)
    if not order_doc:
        raise HTTPException(status_code=404, detail="Order not found")
    if (order_doc.get("customer_id") or "") != body.customer_id:
        raise HTTPException(
            status_code=400,
            detail="Order does not belong to this customer",
        )

    # Authoritative earn basis: the order's taxable value (pre-GST, after all
    # discounts) = grand_total - tax_amount, both persisted at order create.
    order_basis = max(
        round(
            float(order_doc.get("grand_total") or 0.0)
            - float(order_doc.get("tax_amount") or 0.0),
            2,
        ),
        0.0,
    )
    rupee_value = order_basis
    value_clamped = False
    if body.rupee_value is not None:
        client_value = float(body.rupee_value)
        value_clamped = client_value > order_basis
        rupee_value = min(client_value, order_basis)

    # Idempotency fast-path: an already-earned order returns its prior row
    # without recomputing. The AUTHORITATIVE guard against a concurrent
    # double-earn is the atomic claim_earn_for_order below (this read alone is
    # racy -- two callers can both see "not earned").
    if txns.has_earn_for_order(body.customer_id, body.order_id):
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
        rupee_value,
        items,
        account.get("tier", "BRONZE"),
        settings,
    )

    points = int(earn_result.get("points") or 0)
    if points <= 0:
        return {"awarded": 0, "skipped_reason": earn_result.get("skipped_reason")}

    txn_id = str(uuid.uuid4())
    # ATOMIC IDEMPOTENT EARN (no double-earn). A single guarded upsert writes
    # the EARN row only if (customer, order) has none; a racing second caller
    # gets None and we return the existing row WITHOUT bumping the balance, so
    # the points math runs exactly once per (customer, order) even under
    # concurrency. Mirrors the atomic guard redeem uses for the debit.
    claimed = txns.claim_earn_for_order(
        body.customer_id,
        body.order_id,
        {
            "txn_id": txn_id,
            "customer_id": body.customer_id,
            "type": "EARN",
            "points": points,
            "rupee_value": float(rupee_value or 0.0),
            "order_id": body.order_id,
            "reason": body.reason
            or (f"Order {body.order_id}" if body.order_id else "Loyalty earn"),
            "expires_at": expiry_for_earn(settings),
            "tier_at_earn": earn_result.get("tier_at_earn"),
            "tier_multiplier": earn_result.get("tier_multiplier"),
            "created_by": current_user.get("user_id"),
            "created_at": datetime.now(),
        },
    )
    if claimed is None:
        # A concurrent earn won the race -> already earned. Return its row; do
        # NOT bump the balance (it was bumped by the winner).
        for t in txns.find_for_customer(body.customer_id, limit=20):
            if t.get("order_id") == body.order_id and t.get("type") == "EARN":
                return {
                    "awarded": int(t.get("points") or 0),
                    "txn_id": t.get("txn_id"),
                    "deduped": True,
                }
        return {"awarded": 0, "deduped": True}

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
            "rupee_value": rupee_value,
            "client_rupee_value": body.rupee_value,
            "value_clamped": value_clamped,
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
        "rupee_value": rupee_value,
        "value_clamped": value_clamped,
    }


@router.post("/redeem")
async def redeem(
    body: RedeemRequest,
    current_user: Dict[str, Any] = Depends(require_roles(*_POS_ROLES)),
):
    """Deduct points and return the rupee discount they map to.

    Gated to the POS money family (_POS_ROLES) -- redeem debits a customer's
    balance, so it must not be reachable by every authenticated role. The
    atomic guarded debit below is unchanged.
    """
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
                (accounts.find_by_id(body.customer_id) or {}).get("balance_points", 0)
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
# CRM-13: LOYALTY REWARD CATALOG
# ============================================================================
# Staff can define redeemable "rewards" that a customer may exchange points for.
# Each reward has a point cost, an optional cash-value equivalent, an
# availability cap and an optional expiry date.  Redemption is always via the
# normal loyalty-redeem path (points deducted from the account ledger);
# the catalog is the *description* of what can be redeemed, not a separate
# transactional ledger.
#
# Reward types:
#   DISCOUNT   – a percentage or fixed-amount discount voucher
#   FREE_ITEM  – a physical reward (free glasses-cloth, case, etc.)
#   VOUCHER    – store credit / gift voucher
#   EXPERIENCE – event ticket, eye-test, etc.
# ============================================================================

_REWARD_TYPES = {"DISCOUNT", "FREE_ITEM", "VOUCHER", "EXPERIENCE"}
_REWARD_CATALOG_ROLES = ("ADMIN", "AREA_MANAGER", "STORE_MANAGER")


class RewardCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=120)
    type: Literal["DISCOUNT", "FREE_ITEM", "VOUCHER", "EXPERIENCE"]
    description: Optional[str] = None
    point_cost: int = Field(..., ge=1)
    # Cash-equivalent value (optional — for display on the FE).
    cash_value: Optional[float] = Field(None, ge=0)
    # For DISCOUNT type: percentage or fixed amount.
    discount_pct: Optional[float] = Field(None, ge=0, le=100)
    discount_fixed: Optional[float] = Field(None, ge=0)
    # Cap on total redemptions (None = unlimited).
    max_redemptions: Optional[int] = Field(None, ge=1)
    # Availability window.
    valid_from: Optional[str] = None
    valid_until: Optional[str] = None
    active: bool = True
    store_id: Optional[str] = None


class RewardUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=120)
    description: Optional[str] = None
    point_cost: Optional[int] = Field(None, ge=1)
    cash_value: Optional[float] = Field(None, ge=0)
    discount_pct: Optional[float] = Field(None, ge=0, le=100)
    discount_fixed: Optional[float] = Field(None, ge=0)
    max_redemptions: Optional[int] = Field(None, ge=1)
    valid_from: Optional[str] = None
    valid_until: Optional[str] = None
    active: Optional[bool] = None
    store_id: Optional[str] = None


def _reward_db():
    try:
        from database.connection import get_db
        return get_db().db
    except Exception:
        return None


@router.get("/rewards")
async def list_rewards(
    store_id: Optional[str] = Query(None),
    active_only: bool = Query(True),
    limit: int = Query(100, le=500),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """List the reward catalog (visible to all authenticated staff)."""
    db = _reward_db()
    if db is None:
        return {"rewards": [], "total": 0}
    query: Dict[str, Any] = {}
    if store_id:
        query["store_id"] = store_id
    if active_only:
        query["active"] = True
    rewards = list(db.get_collection("loyalty_rewards").find(query).sort("point_cost", 1).limit(limit))
    for r in rewards:
        r.pop("_id", None)
    return {"rewards": rewards, "total": len(rewards)}


@router.post("/rewards")
async def create_reward(
    req: RewardCreate,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Create a new loyalty reward (ADMIN/AREA_MANAGER/STORE_MANAGER)."""
    if not any(role in (current_user.get("roles") or []) for role in ("SUPERADMIN", *_REWARD_CATALOG_ROLES)):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = _reward_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    reward_id = f"RWD-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}"
    doc = {
        "reward_id": reward_id,
        "name": req.name,
        "type": req.type,
        "description": req.description,
        "point_cost": req.point_cost,
        "cash_value": req.cash_value,
        "discount_pct": req.discount_pct,
        "discount_fixed": req.discount_fixed,
        "max_redemptions": req.max_redemptions,
        "redemption_count": 0,
        "valid_from": req.valid_from,
        "valid_until": req.valid_until,
        "active": req.active,
        "store_id": req.store_id,
        "created_by": current_user.get("user_id", "unknown"),
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
    }
    db.get_collection("loyalty_rewards").insert_one(doc)
    _audit("loyalty.reward.create", current_user, {"name": req.name, "type": req.type}, reward_id)
    doc.pop("_id", None)
    return {"message": "Reward created", "reward": doc}


@router.get("/rewards/{reward_id}")
async def get_reward(
    reward_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Fetch a single reward from the catalog."""
    db = _reward_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    doc = db.get_collection("loyalty_rewards").find_one({"reward_id": reward_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Reward not found")
    doc.pop("_id", None)
    return doc


@router.put("/rewards/{reward_id}")
async def update_reward(
    reward_id: str,
    req: RewardUpdate,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Update a reward entry (ADMIN/AREA_MANAGER/STORE_MANAGER)."""
    if not any(role in (current_user.get("roles") or []) for role in ("SUPERADMIN", *_REWARD_CATALOG_ROLES)):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = _reward_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    doc = db.get_collection("loyalty_rewards").find_one({"reward_id": reward_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Reward not found")
    updates = req.model_dump(exclude_unset=True, exclude_none=True)
    if not updates:
        doc.pop("_id", None)
        return doc
    updates["updated_at"] = datetime.now().isoformat()
    db.get_collection("loyalty_rewards").update_one({"reward_id": reward_id}, {"$set": updates})
    _audit("loyalty.reward.update", current_user, {"fields": list(updates.keys())}, reward_id)
    fresh = db.get_collection("loyalty_rewards").find_one({"reward_id": reward_id})
    fresh.pop("_id", None)
    return {"message": "Reward updated", "reward": fresh}


@router.delete("/rewards/{reward_id}")
async def delete_reward(
    reward_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Delete a reward from the catalog (ADMIN/SUPERADMIN only)."""
    if not _is_admin(current_user):
        raise HTTPException(status_code=403, detail="ADMIN or SUPERADMIN required")
    db = _reward_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    doc = db.get_collection("loyalty_rewards").find_one({"reward_id": reward_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Reward not found")
    db.get_collection("loyalty_rewards").delete_one({"reward_id": reward_id})
    _audit("loyalty.reward.delete", current_user, {"name": doc.get("name")}, reward_id)
    return {"message": "Reward deleted", "reward_id": reward_id}


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

        # Fast-path idempotency read (the atomic claim below is the real guard).
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
        # ATOMIC IDEMPOTENT EARN: write the EARN row only if (customer, order)
        # has none; a racing caller gets None and we skip the balance bump, so
        # the order earns exactly once even under concurrency (same guard as the
        # POST /earn endpoint + redeem's atomic debit).
        claimed = txns.claim_earn_for_order(
            customer_id,
            order_id,
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
            },
        )
        if claimed is None:
            return {"awarded": 0, "skipped_reason": "already_earned"}
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


def reverse_for_return(
    return_id: str, order_id: str, customer_id: str
) -> Dict[str, Any]:
    """Reverse loyalty when goods are returned (BUG-099): claw back the points
    EARNED on the original order and restore the points REDEEMED on it.

    Called by returns.create_return after the atomic qty claim. Idempotent on
    return_id (an ADJUST ledger row tagged with the return_id is the guard, so a
    retried / duplicate return never double-reverses). The balance + both lifetime
    counters move in a SINGLE atomic adjust_balance ($inc). Never raises -- always
    returns a dict; the caller decides how to surface a failure.

    buy -> earn 100 / redeem 50, then return:
      net balance delta = redeemed(50) - earned(100) = -50 (claw 50 net),
      lifetime_earned -= 100, lifetime_redeemed -= 50.
    """
    if not customer_id or not order_id or not return_id:
        return {"ok": False, "reason": "missing_ids"}
    accounts = get_loyalty_account_repository()
    txns = get_loyalty_transaction_repository()
    if accounts is None or txns is None:
        return {"ok": False, "reason": "loyalty_db_unavailable"}
    try:
        ledger = txns.find_for_customer(customer_id, limit=1000)
    except Exception as exc:  # noqa: BLE001
        logger.warning("reverse_for_return ledger read failed: %s", exc)
        return {"ok": False, "reason": "ledger_read_failed"}

    # Idempotency: an ADJUST row already tagged with THIS return_id == done.
    for row in ledger:
        if row.get("type") == "ADJUST" and row.get("return_id") == return_id:
            return {"ok": True, "already_reversed": True}

    earned = sum(
        int(t.get("points") or 0)
        for t in ledger
        if t.get("order_id") == order_id and t.get("type") == "EARN"
    )
    redeemed = sum(
        int(t.get("points") or 0)
        for t in ledger
        if t.get("order_id") == order_id and t.get("type") == "REDEEM"
    )
    if earned <= 0 and redeemed <= 0:
        return {"ok": True, "earned_clawed": 0, "redeemed_restored": 0, "net_delta": 0}

    account = accounts.find_or_create(customer_id)
    balance_before = int(account.get("balance_points", 0))
    net_delta = redeemed - earned  # claw earned, restore redeemed
    if balance_before + net_delta < 0:
        # The clawback would drive the balance negative (the earned points were
        # already spent on a LATER order). Do NOT silently clamp -- escalate so a
        # human reconciles; the caller flags the return for retry.
        logger.error(
            "reverse_for_return BALANCE UNDERFLOW cust=%s order=%s return=%s "
            "balance=%s net_delta=%s",
            customer_id, order_id, return_id, balance_before, net_delta,
        )
        return {"ok": False, "reason": "balance_underflow",
                "balance": balance_before, "net_delta": net_delta}

    # Marker FIRST (the idempotency claim), then the atomic balance update. If the
    # balance update fails after the marker, a retry is a safe no-op (marker found)
    # and the caller flags loyalty_reversal_failed for reconciliation -- we fail
    # toward NOT-clawing (customer keeps points) rather than double-clawing.
    txn_id = str(uuid.uuid4())
    try:
        txns.create({
            "txn_id": txn_id,
            "customer_id": customer_id,
            "type": "ADJUST",
            "points": net_delta,
            "order_id": order_id,
            "return_id": return_id,
            "reason": (
                f"Return {return_id}: claw {earned} earned + restore {redeemed} "
                f"redeemed on order {order_id}"
            ),
            "created_at": datetime.now(),
        })
    except Exception as exc:  # noqa: BLE001
        logger.error("reverse_for_return marker write failed: %s", exc)
        return {"ok": False, "reason": "marker_write_failed", "error": str(exc)}
    try:
        accounts.adjust_balance(
            customer_id,
            delta_points=net_delta,
            delta_lifetime_earned=-earned,
            delta_lifetime_redeemed=-redeemed,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "reverse_for_return balance update FAILED (marker %s written) cust=%s: %s",
            txn_id, customer_id, exc,
        )
        return {"ok": False, "reason": "balance_update_failed", "error": str(exc)}

    return {
        "ok": True,
        "earned_clawed": earned,
        "redeemed_restored": redeemed,
        "net_delta": net_delta,
        "txn_id": txn_id,
    }
