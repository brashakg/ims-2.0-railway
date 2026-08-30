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
    # Per-line data comes from the ORDER document, never body.items: client
    # lines could omit discount_percent/promo stamps and defeat the >=5%/offer
    # earn gate (same "points are money" stance as the basis clamp above).
    items = order_doc.get("items") or []
    earn_result = calc_earn_points(
        rupee_value,
        items,
        account.get("tier", "BRONZE"),
        settings,
        cart_discount_percent=float(
            order_doc.get("cart_discount_percent") or 0.0
        ),
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

    # OVER-REDEEM GUARD (security): when a redemption IS tied to an order, it must
    # be bounded by that order -- otherwise points worth more than the order could
    # be redeemed, minting rupee value the sale never earned. A STANDALONE redeem
    # (no order) is ALLOWED (owner decision: goodwill/manual redemption) and stays
    # bounded by the customer's point balance.
    #   * If order_id / order_value is supplied, derive a ceiling and cap to it.
    #     The POS redeem flow always sends both (POSLayout -> loyaltyApi.redeem
    #     with order_id + order_value), so the cap applies at real checkout.
    #   * When order_id is present, look the order up and derive the
    #     AUTHORITATIVE ceiling from the order's grand_total (never trust the
    #     client order_value alone). The order must belong to this customer.
    #   * The redeemed rupee_value is then hard-capped to the order ceiling
    #     below (in addition to calc_redeem's max_pct cap).
    order_ceiling: Optional[float] = None
    if body.order_id:
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
        order_ceiling = max(float(order_doc.get("grand_total") or 0.0), 0.0)
    elif body.order_value is not None:
        # No order_id, but an explicit order_value was supplied -> use it as the
        # ceiling (a manual redeem tied to a known cart total).
        order_ceiling = max(float(body.order_value), 0.0)
    # else: NO order linkage -> a STANDALONE / goodwill redemption (owner-allowed:
    # points may be redeemed without a bill). order_ceiling stays None so the
    # order-value cap below is skipped; the redemption is STILL bounded by the
    # customer's point balance (calc_redeem + the atomic try_debit guard), so it
    # can never mint rupee value beyond the points actually earned. The
    # over-redeem hole this fix closes is redeeming MORE than the order is worth
    # WHEN an order is present -- that path is capped below.

    # Feed calc_redeem the AUTHORITATIVE ceiling (the order's grand_total when we
    # resolved one), not the raw client order_value, so its max_pct cap is
    # computed against the real order total.
    result = calc_redeem(
        body.points,
        account.get("balance_points", 0),
        order_ceiling,
        settings,
    )
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result)

    capped_points = int(result["capped_points"])
    rupee_value = float(result["rupee_value"])

    # HARD over-redeem reject: the rupee value redeemed can NEVER exceed the
    # order's own value. calc_redeem's percentage cap only bites when a
    # max_redeem_pct_of_order < 100 is configured; this is the unconditional
    # floor that closes the "redeem more rupees than the order is worth" hole
    # regardless of settings. 1-paisa epsilon for float noise.
    if order_ceiling is not None and rupee_value > order_ceiling + 0.01:
        raise HTTPException(
            status_code=400,
            detail={
                "ok": False,
                "reason": "exceeds_order_value",
                "rupee_value": rupee_value,
                "order_value": round(order_ceiling, 2),
                "message": (
                    "Redemption value exceeds the order's value; points redeemed "
                    "cannot be worth more than the order they discount."
                ),
            },
        )

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
    cart_discount_percent: float = 0.0,
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
            cart_discount_percent=cart_discount_percent,
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


# Ledger fields that tag an ADJUST row as "this row already reversed the
# loyalty of some order". `return_id` is the returns flow (BUG-099);
# `cancel_of_order_id` is the order-cancel flow (F4). `reversal_of_order_id` is
# the CANONICAL one written by BOTH flows -- the per-flow ids stay for audit and
# per-retry idempotency, but the ORDER is what must only ever be reversed once.
_REVERSAL_MARKER_FIELDS = ("return_id", "cancel_of_order_id")
_REVERSAL_ORDER_FIELD = "reversal_of_order_id"
def _read_order_ledger(txns, customer_id: str, order_id: str, account: Dict[str, Any]):
    """Every ledger row for (customer, order), plus whether the read SUCCEEDED.

    Two problems with `find_for_customer(customer_id, limit=1000)`:
      * it swallows every driver error and returns [], so a read blip is
        indistinguishable from "this order earned nothing" -- and the reversal
        then reports ok=True / clawed 0 / no failure flag;
      * the hard limit=1000 silently truncates a long-lived customer's ledger,
        so an old order's EARN row can fall off the end and never be clawed.

    Preferred path is a direct order-scoped query through the collection: it
    cannot truncate, and a driver error RAISES so we can report it. Falls back
    to the repo method, where an empty ledger for an account that has earned in
    its lifetime is treated as a failed read rather than an empty one.
    """
    coll = getattr(txns, "collection", None)
    if coll is not None:
        try:
            rows = list(coll.find({"customer_id": customer_id, "order_id": order_id}))
            return rows, True
        except Exception as exc:  # noqa: BLE001
            logger.warning("order-scoped ledger read failed: %s", exc)
            return [], False
    try:
        rows = txns.find_for_customer(customer_id, limit=1000)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ledger read raised: %s", exc)
        return [], False
    if not rows and int(account.get("lifetime_earned", 0) or 0) > 0:
        # The account has demonstrably earned before, so an empty ledger is a
        # swallowed read error, not an empty history.
        return [], False
    return rows, True


_APPLIED_REVERSALS = "applied_reversals"


def _apply_reversal_balance(
    accounts,
    customer_id: str,
    txn_id: str,
    net_delta: int,
    earned: int,
    redeemed: int,
    new_tier: Optional[str],
):
    """Move the balance for reversal `txn_id`. EXACTLY ONCE, BY CONSTRUCTION.

    Returns (status, doc) where status is one of:
      "applied"          -- this call moved the money
      "already_applied"  -- some earlier call moved it; this call did nothing
      "underflow"        -- the balance no longer covers the clawback
      "failed"           -- the write itself errored
      "unguarded"        -- no collection available (test/legacy shape only)

    THE WHOLE DESIGN IS THE FILTER. `applied_reversals: {"$ne": txn_id}` makes
    the same reversal id unable to apply twice under ANY interleaving, and
    `$addToSet` records it in the SAME atomic write. That replaces four rounds of
    verify-after-the-fact machinery -- the tri-state landed check, the
    exact-equality already-landed comparison, the incomplete-marker flag and the
    verification_unknown trap -- all of which tried to PROVE a write landed
    afterwards. A matched filter IS the proof, and an unmatched one is equally
    definitive: re-read the doc and the array says which case it was.

    Why the old approach could not work: the marker was written BEFORE the money
    moved and cleared only after, so for that whole window a marker mid-flight
    was indistinguishable from one whose money never landed -- and two workers
    (Dockerfile runs --workers 4) both applied the same $inc. The exact-equality
    fallback only held while the account was frozen; any ordinary earn between
    failure and retry defeated it.

    The underflow guard rides in the same filter for a negative delta, so a
    concurrent redeem cannot drive balance_points negative.
    """
    coll = getattr(accounts, "collection", None)
    updater = getattr(coll, "find_one_and_update", None) if coll is not None else None
    if not callable(updater):
        # No atomic surface (a hand-rolled fake). Best effort, clearly labelled;
        # production always has a real collection via BaseRepository.
        try:
            updated = accounts.adjust_balance(
                customer_id,
                delta_points=net_delta,
                delta_lifetime_earned=-earned,
                delta_lifetime_redeemed=-redeemed,
                new_tier=new_tier,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("adjust_balance raised: %s", exc)
            return "failed", None
        logger.warning(
            "loyalty reversal %s applied WITHOUT the exactly-once guard "
            "(no collection on the account repo)", txn_id,
        )
        return "unguarded", updated

    flt: Dict[str, Any] = {
        "customer_id": customer_id,
        _APPLIED_REVERSALS: {"$ne": txn_id},
    }
    if net_delta < 0:
        flt["balance_points"] = {"$gte": abs(int(net_delta))}
    inc: Dict[str, Any] = {}
    if net_delta:
        inc["balance_points"] = net_delta
    if earned:
        inc["lifetime_earned"] = -earned
    if redeemed:
        inc["lifetime_redeemed"] = -redeemed
    set_block: Dict[str, Any] = {"updated_at": datetime.now()}
    if new_tier:
        set_block["tier"] = new_tier
    update: Dict[str, Any] = {
        "$addToSet": {_APPLIED_REVERSALS: txn_id},
        "$set": set_block,
    }
    if inc:
        update["$inc"] = inc
    try:
        doc = updater(flt, update)
    except Exception as exc:  # noqa: BLE001
        logger.error("guarded reversal write failed: %s", exc)
        return "failed", None
    if doc is not None:
        # find_one_and_update returns the PRE-image; report the post-state.
        after = dict(doc)
        after["balance_points"] = int(doc.get("balance_points", 0)) + net_delta
        after["lifetime_earned"] = int(doc.get("lifetime_earned", 0)) - earned
        after["lifetime_redeemed"] = int(doc.get("lifetime_redeemed", 0)) - redeemed
        return "applied", after
    # No match. Two possibilities, and the array tells us which -- no inference.
    try:
        current = accounts.find_by_id(customer_id) or {}
    except Exception:  # noqa: BLE001
        current = {}
    if txn_id in (current.get(_APPLIED_REVERSALS) or []):
        return "already_applied", current
    return "underflow", current


def _ensure_reversal_applied(accounts, marker: Dict[str, Any]) -> Dict[str, Any]:
    """Make sure the money for an EXISTING reversal marker has moved.

    Safe to call any number of times, from any worker, at any point in another
    worker's flow -- that is the entire benefit of keying the write on the
    marker's txn_id. There is nothing to detect and nothing to prove: re-issuing
    the guarded write either applies the outstanding delta or matches nothing.

    This replaces the repair door, which had to guess whether a flagged marker
    was mid-flight or genuinely stranded and got it wrong in both directions.
    """
    txn_id = marker.get("txn_id")
    customer_id = marker.get("customer_id") or ""
    if not txn_id or not customer_id:
        return {"ok": True, "already_reversed": True}

    # PRE-GUARD MARKER -- written by the code running in production TODAY, whose
    # money already moved through adjust_balance and which never touched the
    # account's applied_reversals array.
    #
    # This is the one shape the exactly-once guard cannot see. `$ne` on a
    # MISSING field MATCHES in Mongo, and it matches an EMPTY array just the
    # same, because the guard keys on membership of THIS marker's txn_id. So
    # every legacy marker looks unapplied forever: re-issuing the write would
    # claw a second time (a REGRESSION -- main returns already_reversed with no
    # money move), or, when the $gte guard refuses, report
    # unapplied_reversal=True on every retry of a reversal that main already
    # completed, permanently wedging the cancel retry door open.
    #
    # Detected by SHAPE, not by a backfill: a marker missing this PR's own
    # fields predates the guard. A backfill alone cannot work -- old and new
    # workers coexist during a rolling deploy, so a legacy marker can be written
    # AFTER the migration runs. The shape guard needs no migration and no
    # ordering constraint, and it fails in the safe direction this PR chose:
    # never move money we cannot prove is outstanding.
    if _REVERSAL_ORDER_FIELD not in marker or "reversed_earn_points" not in marker:
        logger.info(
            "loyalty reversal %s predates the exactly-once guard "
            "(cust=%s order=%s) -- its money moved under the old path; "
            "not re-issuing",
            txn_id, customer_id, marker.get("order_id"),
        )
        return {
            "ok": True,
            "already_reversed": True,
            "pre_guard_marker": True,
            "txn_id": txn_id,
        }
    net_delta = int(marker.get("points") or 0)
    earned = int(marker.get("reversed_earn_points") or 0)
    redeemed = int(marker.get("reversed_redeem_points") or 0)

    new_tier = None
    if marker.get("recompute_tier"):
        try:
            account = accounts.find_or_create(customer_id)
            settings = _settings_safe()
            lifetime_after = max(0, int(account.get("lifetime_earned", 0)) - earned)
            recomputed = compute_tier(lifetime_after, settings)
            if recomputed != account.get("tier"):
                new_tier = recomputed
        except Exception as exc:  # noqa: BLE001
            logger.warning("reversal tier recompute skipped: %s", exc)

    status, _doc = _apply_reversal_balance(
        accounts, customer_id, txn_id, net_delta, earned, redeemed, new_tier
    )
    out: Dict[str, Any] = {"ok": True, "already_reversed": True, "txn_id": txn_id}
    if status == "applied":
        # The earlier attempt never moved the money; this one did.
        logger.warning(
            "completed a previously-unapplied loyalty reversal %s (cust=%s)",
            txn_id, customer_id,
        )
        out["completed_now"] = True
        out["earned_clawed"] = earned
        out["redeemed_restored"] = redeemed
        out["net_delta"] = net_delta
    elif status in ("underflow", "failed"):
        # STILL unresolved -- and we say so, every time, instead of reporting a
        # cheerful already_reversed that erases the only reconciliation signal.
        logger.error(
            "loyalty reversal %s is STILL UNAPPLIED (cust=%s status=%s)",
            txn_id, customer_id, status,
        )
        return {
            "ok": False,
            "reason": "balance_underflow" if status == "underflow" else
                      "balance_update_failed",
            "unapplied_reversal": True,
            "txn_id": txn_id,
            "net_delta": net_delta,
        }
    return out


def _reverse_order_loyalty(
    *,
    order_id: str,
    customer_id: str,
    marker_field: str,
    marker_value: str,
    reason_prefix: str,
    extra_marker: Optional[Dict[str, Any]] = None,
    block_on_any_prior_reversal: bool = False,
    recompute_tier: bool = False,
) -> Dict[str, Any]:
    """Shared engine behind reverse_for_return (returns) and reverse_for_cancel
    (order cancel): claw back the points EARNED on `order_id` and restore the
    points REDEEMED on it.

    The balance + both lifetime counters move in a SINGLE atomic adjust_balance
    ($inc), and the result is VERIFIED (adjust_balance cannot raise). Never
    raises -- always returns a dict; the caller decides how to surface a failure.

    IDEMPOTENCY, in two independent layers:
      * the ADJUST ledger row tagged ``{marker_field: marker_value}`` is written
        FIRST, against a partial-unique index, so only one caller ever mints a
        reversal for a given return / cancelled order; and
      * the BALANCE MOVE itself is keyed on that row's ``txn_id`` via
        ``applied_reversals`` (see _apply_reversal_balance), so the same
        reversal cannot apply twice under ANY interleaving.
    The second layer is what makes "marker written, money not moved" a
    self-healing state rather than a puzzle: any later call simply re-issues the
    same guarded write, which applies the outstanding delta or matches nothing.

    ``block_on_any_prior_reversal`` treats a reversal written by ANY flow for
    this ORDER as done, so neither cancel-after-return nor a second partial
    return can claw the same points twice.

    ``recompute_tier`` is OPT-IN and belongs to the CANCEL flow only. A cancel
    un-does the whole order, so the tier it bought must come back down. A RETURN
    must NOT move the tier: origin/main's reverse_for_return passed no new_tier,
    and because the return claw is order-wide rather than line-proportional, one
    partial return of a multi-line order would otherwise demote a legitimately
    held tier and permanently cut the customer's earn multiplier.

    buy -> earn 100 / redeem 50, then reverse:
      net balance delta = redeemed(50) - earned(100) = -50 (claw 50 net),
      lifetime_earned -= 100, lifetime_redeemed -= 50.
    """
    accounts = get_loyalty_account_repository()
    txns = get_loyalty_transaction_repository()
    if accounts is None or txns is None:
        return {"ok": False, "reason": "loyalty_db_unavailable"}
    account = accounts.find_or_create(customer_id)
    ledger, read_ok = _read_order_ledger(txns, customer_id, order_id, account)
    if not read_ok:
        # DISTINGUISHABLE from "no loyalty rows". find_for_customer swallows
        # every driver error and returns [], so a transient read blip used to
        # look like an order that simply earned nothing: ok=True, clawed 0,
        # loyalty_reversal_failed=False -- leaving the customer holding
        # redeemable points on a cancelled order with no flag and no retry
        # signal. That is the exact farm-and-cancel hole this reversal exists
        # to close, so it must fail LOUD.
        logger.error(
            "%s ledger read FAILED for cust=%s order=%s -- refusing to reverse "
            "on an unreadable ledger",
            reason_prefix, customer_id, order_id,
        )
        return {"ok": False, "reason": "ledger_read_failed"}

    # Idempotency. A prior reversal row short-circuits -- but we ALWAYS re-issue
    # its guarded balance write first. That is free (the applied_reversals guard
    # makes it a no-op when the money already moved) and it is what finishes a
    # reversal whose marker landed but whose money did not. No flag to inspect,
    # no mid-flight-vs-stranded guess to get wrong.
    for row in ledger:
        if row.get("type") != "ADJUST":
            continue
        same_marker = row.get(marker_field) == marker_value
        same_order = (
            block_on_any_prior_reversal
            and row.get("order_id") == order_id
            and (
                row.get(_REVERSAL_ORDER_FIELD) == order_id
                or any(row.get(f) for f in _REVERSAL_MARKER_FIELDS)
            )
        )
        if not (same_marker or same_order):
            continue
        out = _ensure_reversal_applied(accounts, row)
        # `same_order` without `same_marker` is the guard that stops a SECOND
        # PARTIAL RETURN (different return_id, different unique key, no index
        # collision) from reversing the WHOLE order again.
        if out.get("ok") and not same_marker:
            out["reversed_by"] = next(
                (f for f in _REVERSAL_MARKER_FIELDS if row.get(f)),
                _REVERSAL_ORDER_FIELD,
            )
        return out

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

    # (account was read above, before the ledger, so the read-failure heuristic
    # could compare the ledger against lifetime_earned.)
    balance_before = int(account.get("balance_points", 0))
    net_delta = redeemed - earned  # claw earned, restore redeemed
    if balance_before + net_delta < 0:
        # The clawback would drive the balance negative (the earned points were
        # already spent on a LATER order). Do NOT silently clamp -- escalate so a
        # human reconciles; the caller flags the doc for retry.
        logger.error(
            "%s BALANCE UNDERFLOW cust=%s order=%s ref=%s balance=%s net_delta=%s",
            reason_prefix, customer_id, order_id, marker_value,
            balance_before, net_delta,
        )
        return {"ok": False, "reason": "balance_underflow",
                "balance": balance_before, "net_delta": net_delta}

    # ATOMIC CLAIM (panel must-fix 2). The ledger scan above is ADVISORY only --
    # it is a check-then-write and two concurrent cancels can both pass it. The
    # authoritative guard is the INSERT: the marker row is written with
    # raise_on_duplicate=True against the partial UNIQUE index on
    # (customer_id, <marker_field>) for type=ADJUST (database/connection.py), so
    # exactly ONE of two racing reversals can insert. The loser gets
    # DuplicateKeyError and returns already_reversed BEFORE touching the balance.
    #
    # This is what stops the two-way money bug: without it, two concurrent
    # cancels of a redeem-only order DOUBLE-RESTORE (minting redeemable rupees)
    # and of an earned order DOUBLE-CLAW (burning the customer's points).
    # Marker-before-balance also means the only partial-failure mode is "marker
    # written, balance not moved" -- we fail toward NOT clawing (customer keeps
    # their points) and the caller flags the doc for reconciliation.
    txn_id = str(uuid.uuid4())
    marker: Dict[str, Any] = {
        "txn_id": txn_id,
        "customer_id": customer_id,
        "type": "ADJUST",
        "points": net_delta,
        "order_id": order_id,
        marker_field: marker_value,
        # CANONICAL per-ORDER key, written by BOTH flows and backed by its own
        # partial-unique index. The per-flow ids remain the per-retry key; this
        # one is what makes "this order's loyalty has been reversed" a single
        # fact that a second partial return cannot side-step.
        _REVERSAL_ORDER_FIELD: order_id,
        # The two lifetime deltas, so a re-issued guarded write can reconstruct
        # the exact $inc without re-deriving it from a ledger that has moved on.
        "reversed_earn_points": earned,
        "reversed_redeem_points": redeemed,
        # Whether THIS flow owns the tier, so a completion applies the same tier
        # rule the primary path did (a cancel drops the tier, a return does not).
        "recompute_tier": bool(recompute_tier),
        # Diagnostics only -- nothing branches on these. Exactly-once is
        # enforced by the applied_reversals guard on the ACCOUNT, not by
        # comparing the account against a remembered snapshot (any ordinary earn
        # between failure and retry defeats that comparison).
        "balance_before": balance_before,
        "reason": (
            f"{reason_prefix}: claw {earned} earned + restore {redeemed} "
            f"redeemed on order {order_id}"
        ),
        "created_at": datetime.now(),
    }
    if extra_marker:
        marker.update(extra_marker)
    try:
        claimed = txns.create(marker, raise_on_duplicate=True)
    except Exception as exc:  # noqa: BLE001
        if exc.__class__.__name__ == "DuplicateKeyError":
            # A concurrent reversal won the claim. Do NOT move the balance.
            logger.info(
                "%s lost the reversal claim race (already reversed) cust=%s",
                reason_prefix, customer_id,
            )
            return {"ok": True, "already_reversed": True, "raced": True}
        logger.error("%s marker write failed: %s", reason_prefix, exc)
        return {"ok": False, "reason": "marker_write_failed", "error": str(exc)}
    if claimed is None:
        # create() fail-soft-returned None (write rejected without raising) --
        # we do NOT hold the claim, so we must not move money.
        logger.error("%s marker write returned no row; skipping balance move",
                     reason_prefix)
        return {"ok": False, "reason": "marker_write_failed"}

    # TIER (panel must-fix 9, corrected): lifetime_earned is being decremented,
    # so for a CANCEL the tier it drives must come back down in the SAME
    # adjust_balance call -- otherwise create-order -> cancel leaves a
    # permanently inflated GOLD/PLATINUM multiplier (1.25x / 1.5x) on every
    # FUTURE genuine purchase.
    #
    # OPT-IN ONLY. Doing this unconditionally silently changed the RETURNS money
    # path, which origin/main never touched: because the return claw is
    # order-wide rather than line-proportional, ONE partial return of a two-line
    # order would demote a legitimately held GOLD to SILVER and permanently cut
    # the customer's earn rate. Returns therefore pass recompute_tier=False.
    new_tier = None
    if recompute_tier:
        try:
            settings = _settings_safe()
            lifetime_after = max(0, int(account.get("lifetime_earned", 0)) - earned)
            recomputed = compute_tier(lifetime_after, settings)
            if recomputed != account.get("tier"):
                new_tier = recomputed
        except Exception as exc:  # noqa: BLE001 -- tier math must never block the claw
            logger.warning("%s tier recompute skipped: %s", reason_prefix, exc)

    status, _updated = _apply_reversal_balance(
        accounts, customer_id, txn_id, net_delta, earned, redeemed, new_tier
    )
    if status == "underflow":
        # A concurrent redeem landed between our read and our write. The guarded
        # filter refused rather than driving the balance negative. The marker
        # stays; any later call re-issues the same guarded write and completes
        # it once the balance can cover it.
        logger.error(
            "%s BALANCE UNDERFLOW AT WRITE (marker %s) cust=%s net_delta=%s",
            reason_prefix, txn_id, customer_id, net_delta,
        )
        return {"ok": False, "reason": "balance_underflow", "net_delta": net_delta}
    if status == "failed":
        logger.error(
            "%s balance write FAILED (marker %s) cust=%s -- the marker stands; "
            "a later call will re-issue the same guarded write",
            reason_prefix, txn_id, customer_id,
        )
        return {"ok": False, "reason": "balance_update_failed"}
    if status == "already_applied":
        # Only reachable if this txn_id somehow already rode a write. Harmless
        # and self-consistent: the money is exactly once applied.
        return {"ok": True, "already_reversed": True, "txn_id": txn_id}

    return {
        "ok": True,
        "tier": new_tier or account.get("tier"),
        "tier_changed": new_tier is not None,
        "earned_clawed": earned,
        "redeemed_restored": redeemed,
        "net_delta": net_delta,
        "txn_id": txn_id,
    }


def regate_earn_after_edit(
    order_doc: Dict[str, Any],
    user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Re-run the >=5%/offer earn gate after a discount-changing ORDER EDIT
    and claw back points that are no longer eligible (owner hard rule
    2026-08-30 -- earn fires at create, but a superadmin edit can raise
    discounts afterwards and must not leave pre-gate points standing).

    Delta semantics, not full reversal: recompute what the edited order WOULD
    earn now, compare against the order's net earned-so-far (EARN rows minus
    prior regate claws), and claw only the difference. Never credits -- an
    edit that LOWERS a discount does not mint points retroactively. Repeated
    edits converge (net == target -> no write). Fail-soft: always returns a
    dict, never raises.

    ponytail: the read-compare-claw is not race-guarded per delta (unlike the
    unique-indexed full reversals); superadmin edits are a rare single-human
    flow. Add a per-(order, net) claim key if concurrent edits ever appear.
    """
    try:
        order_id = str(order_doc.get("order_id") or "")
        customer_id = order_doc.get("customer_id") or ""
        if not order_id or not customer_id or customer_id.startswith(
            ("walkin-", "walk-in")
        ):
            return {"ok": True, "clawed": 0, "skipped_reason": "no_customer"}
        accounts = get_loyalty_account_repository()
        txns = get_loyalty_transaction_repository()
        if accounts is None or txns is None:
            return {"ok": False, "reason": "loyalty_db_unavailable"}
        account = accounts.find_or_create(customer_id)
        ledger, read_ok = _read_order_ledger(txns, customer_id, order_id, account)
        if not read_ok:
            logger.error(
                "edit-regate ledger read FAILED cust=%s order=%s -- refusing "
                "to claw on an unreadable ledger", customer_id, order_id,
            )
            return {"ok": False, "reason": "ledger_read_failed"}

        earn_rows = [
            t for t in ledger
            if t.get("order_id") == order_id and t.get("type") == "EARN"
        ]
        earned = sum(int(t.get("points") or 0) for t in earn_rows)
        prior_claw = sum(
            int(t.get("points") or 0)
            for t in ledger
            if t.get("type") == "ADJUST"
            and t.get("edit_regate_of_order_id") == order_id
        )
        # A full reversal (cancel/return) already zeroed this order's loyalty.
        if any(
            t.get("type") == "ADJUST"
            and t.get(_REVERSAL_ORDER_FIELD) == order_id
            for t in ledger
        ):
            return {"ok": True, "clawed": 0, "skipped_reason": "already_reversed"}

        net = earned + prior_claw  # prior_claw rows carry negative points
        if net <= 0:
            return {"ok": True, "clawed": 0, "skipped_reason": "nothing_earned"}

        settings = _settings_safe()
        basis = max(
            round(
                float(order_doc.get("grand_total") or 0.0)
                - float(order_doc.get("tax_amount") or 0.0),
                2,
            ),
            0.0,
        )
        tier = (earn_rows[0].get("tier_at_earn") if earn_rows else None) or (
            account.get("tier", "BRONZE")
        )
        target = int(
            calc_earn_points(
                basis,
                order_doc.get("items") or [],
                tier,
                settings,
                cart_discount_percent=float(
                    order_doc.get("cart_discount_percent") or 0.0
                ),
            ).get("points")
            or 0
        )
        delta = net - min(target, net)  # never credit
        if delta <= 0:
            return {"ok": True, "clawed": 0}

        balance = int(account.get("balance_points", 0))
        if balance < delta:
            # Mirror the full-reversal stance: fail toward NOT clawing and
            # escalate loudly rather than driving the balance negative.
            logger.error(
                "edit-regate BALANCE UNDERFLOW cust=%s order=%s balance=%s "
                "delta=%s -- points already spent; human reconciliation needed",
                customer_id, order_id, balance, delta,
            )
            return {"ok": False, "reason": "balance_underflow",
                    "balance": balance, "delta": delta}

        txn_id = str(uuid.uuid4())
        txns.create(
            {
                "txn_id": txn_id,
                "customer_id": customer_id,
                "type": "ADJUST",
                "points": -delta,
                "order_id": order_id,
                "edit_regate_of_order_id": order_id,
                "reason": (
                    f"earn re-gate after order edit: order {order_id} now "
                    f"eligible for {min(target, net)} of {net} earned points"
                ),
                "created_by": user_id,
                "created_at": datetime.now(),
            }
        )
        accounts.adjust_balance(
            customer_id,
            delta_points=-delta,
            delta_lifetime_earned=-delta,
        )
        return {"ok": True, "clawed": delta, "txn_id": txn_id}
    except Exception as exc:  # noqa: BLE001
        logger.warning("regate_earn_after_edit failed: %s", exc)
        return {"ok": False, "reason": "error", "error": str(exc)}


def reverse_for_return(
    return_id: str, order_id: str, customer_id: str
) -> Dict[str, Any]:
    """Reverse loyalty when goods are returned (BUG-099): claw back the points
    EARNED on the original order and restore the points REDEEMED on it.

    Called by returns.create_return after the atomic qty claim. Idempotent on
    return_id, AND -- since the claw is order-wide, not line-proportional -- on
    the ORDER: partial returns are cumulative by design (returns.py:1524), so a
    SECOND partial return of the same order carries a DIFFERENT return_id, hits
    no unique-index collision, and used to reverse the WHOLE order a second time:
    points MINTED and lifetime_redeemed driven NEGATIVE. block_on_any_prior_
    reversal closes that, backed by the canonical reversal_of_order_id index.

    CHOICE OF FIX (the alternative was making the claw line-proportional and
    keying the marker per line): blocking is the conservative option because it
    preserves origin/main's order-wide claw semantics exactly. Re-proportioning
    the return claw would change what every partial return pays back -- a real
    money-behaviour change, which is precisely the class of regression this round
    is correcting. It belongs in its own PR with the accountant in the room.

    TIER IS NOT RECOMPUTED HERE. origin/main passed no new_tier, and because the
    claw is order-wide, one partial return of a multi-line order would otherwise
    demote a legitimately held tier and permanently cut the earn multiplier.
    """
    if not customer_id or not order_id or not return_id:
        return {"ok": False, "reason": "missing_ids"}
    return _reverse_order_loyalty(
        order_id=order_id,
        customer_id=customer_id,
        marker_field="return_id",
        marker_value=return_id,
        reason_prefix=f"Return {return_id}",
        block_on_any_prior_reversal=True,
        recompute_tier=False,
    )


def reverse_for_cancel(order_id: str, customer_id: Optional[str]) -> Dict[str, Any]:
    """F4: reverse loyalty when an ORDER IS CANCELLED.

    orders.create_order awards points at CREATE (even on a DRAFT) and cancel had
    NO reversal at all, so a cancelled order left redeemable points behind
    (unfunded discounts, plus a farm-and-cancel vector: big order -> points ->
    cancel -> redeem), while any points the customer REDEEMED against that order
    were silently burned. This claws back the EARN and restores the REDEEM.

    IDEMPOTENT ON THE ORDER: the marker is `cancel_of_order_id == order_id`, so a
    retried cancel (or a cancel racing itself) reverses EXACTLY once -- there is
    no per-cancel id to key on, and the order id is the natural key. It also
    stands down when a RETURN already reversed this order, so the two flows can
    never both claw the same points.

    Walk-in pseudo-customers never earn, so they are skipped. Never raises.
    """
    if not order_id:
        return {"ok": False, "reason": "missing_ids"}
    cid = str(customer_id or "").strip()
    if not cid:
        return {"ok": False, "reason": "missing_ids"}
    if cid.startswith(("walkin-", "walk-in")):
        return {"ok": True, "skipped_reason": "walkin", "earned_clawed": 0}
    try:
        return _reverse_order_loyalty(
            order_id=order_id,
            customer_id=cid,
            marker_field="cancel_of_order_id",
            marker_value=order_id,
            reason_prefix=f"Cancel {order_id}",
            extra_marker={"source": "ORDER_CANCEL"},
            block_on_any_prior_reversal=True,
            # A cancel un-does the ENTIRE order, so the tier that order bought
            # must come back down with it. This is the ONLY caller that asks for
            # it -- see reverse_for_return for why returns must not.
            recompute_tier=True,
        )
    except Exception as exc:  # noqa: BLE001 -- must never break a cancel
        logger.error("reverse_for_cancel failed for order %s: %s", order_id, exc)
        return {"ok": False, "reason": "error", "error": str(exc)}
