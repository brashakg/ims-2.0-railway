"""
IMS 2.0 - Feature #49: Family/household loyalty points wallet router
=====================================================================
HTTP surface for ``api/services/family_wallet.py``. A household groups up to
``loyalty.pool_max_members`` (E2 policy, default 7) customers into ONE shared
loyalty-points pool (a money_guard FAMILY_WALLET account; integer POINTS unit).

Flow: a manager creates the household + members; the pool earns points; a
redemption is OTP-gated -- /redeem/request-otp sends a 6-digit code to the
PRIMARY member's mobile via the EXISTING reminder_rail OTP slice (rides the
transactional SMS path; category=OTP bypasses consent/quiet-hours; gated by
DISPATCH_MODE so a test deploy never texts a customer), then /redeem verifies
the code (atomic consume-once) and debits the pool through money_guard's
guarded find_one_and_update (floor IN the filter -- two racing redeems near
the floor produce one winner). The redeemed value is minted as a store-credit
voucher via the canonical ``vouchers.mint_voucher`` helper so it is spendable
through the existing voucher flow -- NO orders.py / POS change.

CHAIN-WIDE BY OWNER DECISION: household lookup + redemption are NOT
store-scoped (mirrors chain-wide customer-lookup + voucher-redeem). The
household records its creating store for provenance only. Household CREATE /
member management is manager+ (enrolment changes who can spend a shared
balance); redemption + reads are open to the POS/staff role families below.

No emoji (Windows cp1252).
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .auth import get_current_user
from ..services import family_wallet as svc

router = APIRouter(tags=["family-wallet"])

# Household creation + membership edits change WHO can spend a shared family
# balance -> manager ladder only (a cashier cannot enrol/remove members).
_MANAGE_ROLES = {"STORE_MANAGER", "AREA_MANAGER", "ADMIN", "SUPERADMIN"}
# Redemption is a POS-counter money action -> the POS money family (mirrors
# loyalty._POS_ROLES) + SUPERADMIN.
_REDEEM_ROLES = {"SALES_CASHIER", "SALES_STAFF", "CASHIER", "STORE_MANAGER",
                 "AREA_MANAGER", "ADMIN", "SUPERADMIN"}
# Read-only household/balance lookup: any authenticated store staff.
_READ_ROLES = {"SALES_CASHIER", "CASHIER", "SALES_STAFF", "OPTOMETRIST",
               "CATALOG_MANAGER", "STORE_MANAGER", "AREA_MANAGER", "ACCOUNTANT",
               "ADMIN", "SUPERADMIN"}


def _get_db():
    from database.connection import get_db

    return get_db().db


def _roles(user: Dict[str, Any]) -> set:
    return {str(r).upper() for r in (user.get("roles", []) or [])}


def _require(user: Dict[str, Any], allowed: set, what: str):
    if not (_roles(user) & allowed):
        raise HTTPException(status_code=403, detail=f"not permitted to {what}")


def _get_policy(key, scope=None, *, default=None):
    try:
        from ..services.policy_engine import get_policy

        return get_policy(key, scope, default=default)
    except Exception:  # noqa: BLE001
        return default


def _scope(user: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    sid = user.get("active_store_id")
    return {"store_id": sid} if sid else None


def _max_members(user: Dict[str, Any]) -> int:
    try:
        return int(_get_policy("loyalty.pool_max_members", _scope(user),
                               default=svc.DEFAULT_MAX_MEMBERS)
                   or svc.DEFAULT_MAX_MEMBERS)
    except (TypeError, ValueError):
        return svc.DEFAULT_MAX_MEMBERS


def _require_otp(user: Dict[str, Any]) -> bool:
    val = _get_policy("loyalty.pool_redeem_requires_otp", _scope(user), default=True)
    if val is None:
        return True
    return bool(val)


def _redeem_rate() -> float:
    """Loyalty redeem_rupee_per_point (rupees per point) for the voucher mint.
    Fail-soft default 1.0 -- the POOL stays in points; rupees appear only on
    the minted voucher."""
    try:
        from ..dependencies import get_loyalty_settings_repository

        repo = get_loyalty_settings_repository()
        if repo is not None:
            return float((repo.get() or {}).get("redeem_rupee_per_point", 1.0) or 1.0)
    except Exception:  # noqa: BLE001
        pass
    return 1.0


def _raise(result: Dict[str, Any]):
    """Translate a service {ok: False, http, error} envelope to HTTPException."""
    raise HTTPException(status_code=int(result.get("http") or 400),
                        detail=result.get("error") or "request_failed")


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class HouseholdCreateBody(BaseModel):
    primary_customer_id: str = Field(..., min_length=1)
    store_id: Optional[str] = Field(
        None, description="Creating store (provenance only; lookup is chain-wide)")


class MemberAddBody(BaseModel):
    customer_id: str = Field(..., min_length=1)


class RequestOtpBody(BaseModel):
    points: int = Field(..., gt=0, description="Points the OTP authorizes")


class RedeemBody(BaseModel):
    points: int = Field(..., gt=0)
    redeeming_customer_id: str = Field(..., min_length=1)
    otp_id: Optional[str] = None
    otp_code: Optional[str] = None


# ---------------------------------------------------------------------------
# Household lifecycle (manager+)
# ---------------------------------------------------------------------------


@router.post("/households")
async def create_household(body: HouseholdCreateBody,
                           current_user: Dict[str, Any] = Depends(get_current_user)):
    """Create a household with the primary customer as member[0]. Manager+."""
    _require(current_user, _MANAGE_ROLES, "create a household")
    out = svc.create_household(
        _get_db(), primary_customer_id=body.primary_customer_id,
        actor=current_user,
        store_id=body.store_id or current_user.get("active_store_id"),
    )
    if not out.get("ok"):
        _raise(out)
    return out["household"]


@router.post("/households/{household_id}/members")
async def add_member(household_id: str, body: MemberAddBody,
                     current_user: Dict[str, Any] = Depends(get_current_user)):
    """Add a member (manager+). The E2 max-member cap is enforced IN the
    guarded write's filter -- a full household 409s, a duplicate add 409s."""
    _require(current_user, _MANAGE_ROLES, "add a household member")
    out = svc.add_member(_get_db(), household_id, body.customer_id,
                         actor=current_user, max_members=_max_members(current_user))
    if not out.get("ok"):
        _raise(out)
    return out["household"]


@router.delete("/households/{household_id}/members/{customer_id}")
async def remove_member(household_id: str, customer_id: str,
                        current_user: Dict[str, Any] = Depends(get_current_user)):
    """Remove a NON-PRIMARY member (manager+). The primary is irremovable."""
    _require(current_user, _MANAGE_ROLES, "remove a household member")
    out = svc.remove_member(_get_db(), household_id, customer_id, actor=current_user)
    if not out.get("ok"):
        _raise(out)
    return out["household"]


# ---------------------------------------------------------------------------
# Reads (CHAIN-WIDE by owner decision -- no store 403 on lookup)
# ---------------------------------------------------------------------------


@router.get("/households/by-customer/{customer_id}")
async def get_by_customer(customer_id: str,
                          current_user: Dict[str, Any] = Depends(get_current_user)):
    """The ACTIVE household containing this customer. CHAIN-WIDE lookup (owner
    decision: customer lookup is never store-fenced)."""
    _require(current_user, _READ_ROLES, "look up a household")
    household = svc.get_household_by_customer(_get_db(), customer_id)
    if household is None:
        raise HTTPException(status_code=404, detail="household not found")
    household["pool_balance_points"] = svc.pool_balance(
        _get_db(), household.get("household_id"))
    return household


@router.get("/households/{household_id}")
async def get_household(household_id: str,
                        current_user: Dict[str, Any] = Depends(get_current_user)):
    """One household + its live pool balance (points). CHAIN-WIDE."""
    _require(current_user, _READ_ROLES, "look up a household")
    household = svc.get_household(_get_db(), household_id)
    if household is None:
        raise HTTPException(status_code=404, detail="household not found")
    household["pool_balance_points"] = svc.pool_balance(_get_db(), household_id)
    return household


# ---------------------------------------------------------------------------
# OTP-gated pool redemption (standalone; NOT in the POS order path)
# ---------------------------------------------------------------------------


@router.post("/households/{household_id}/redeem/request-otp")
async def request_redeem_otp(household_id: str, body: RequestOtpBody,
                             current_user: Dict[str, Any] = Depends(get_current_user)):
    """Issue a redemption OTP to the PRIMARY member's mobile (reminder_rail
    slice: sha256-hashed code, 5-min expiry, max 5 attempts, DISPATCH_MODE-
    gated SMS). Returns otp_id + expiry -- NEVER the code."""
    _require(current_user, _REDEEM_ROLES, "request a pool redemption OTP")
    db = _get_db()
    household = svc.get_household(db, household_id)
    if household is None:
        raise HTTPException(status_code=404, detail="household not found")
    if household.get("status") != svc.STATUS_ACTIVE:
        raise HTTPException(status_code=409, detail="household_inactive")
    # Advisory: do not burn an SMS on an obviously-insufficient pool.
    if svc.pool_balance(db, household_id) < int(body.points):
        raise HTTPException(status_code=409, detail="insufficient_balance")

    from ..services.reminder_rail import send_pool_redemption_otp

    out = await send_pool_redemption_otp(
        db,
        primary_customer_id=household.get("primary_customer_id"),
        household_id=household_id,
        amount=int(body.points),  # points-unit (documented on the service)
        requested_by=current_user.get("user_id"),
    )
    if not out.get("ok"):
        raise HTTPException(status_code=503,
                            detail=out.get("reason") or "otp_unavailable")
    return {"otp_id": out["otp_id"], "expires_at": out["expires_at"],
            "sent_to": "primary_member_mobile"}


@router.post("/households/{household_id}/redeem")
async def redeem(household_id: str, body: RedeemBody,
                 current_user: Dict[str, Any] = Depends(get_current_user)):
    """Verify the OTP (atomic consume-once) + debit the pool (guarded floor in
    the filter) + mint the redeemed value as a store-credit voucher via the
    canonical vouchers.mint_voucher. CHAIN-WIDE by owner decision."""
    _require(current_user, _REDEEM_ROLES, "redeem from the family pool")
    db = _get_db()
    require_otp = _require_otp(current_user)

    out = svc.pool_redeem(
        db, household_id, body.points,
        redeeming_customer_id=body.redeeming_customer_id,
        actor=current_user, otp_id=body.otp_id, otp_code=body.otp_code,
        require_otp=require_otp,
    )
    if not out.get("ok"):
        _raise(out)

    # Mint the spendable store-credit voucher (rupee conversion happens HERE,
    # not in the pool -- the pool unit is points).
    rate = _redeem_rate()
    rupee_value = round(int(out["points"]) * rate, 2)
    voucher = None
    try:
        from .vouchers import mint_voucher

        voucher = mint_voucher(
            db.get_collection("vouchers"),
            vtype="GIFT_CARD",
            amount=rupee_value,
            store_id=current_user.get("active_store_id"),
            customer_id=body.redeeming_customer_id,
            issued_by=current_user.get("user_id"),
            extra={
                "source": "family_wallet_pool",
                "household_id": household_id,
                "pool_txn_id": out.get("txn_id"),
                "pool_points": int(out["points"]),
            },
        )
    except Exception:  # noqa: BLE001
        voucher = None
    if voucher is None:
        # Standalone Mongo -- no cross-collection transaction. The points
        # already left the pool, so COMPENSATE: credit them back and fail
        # loudly. The reversal is idempotent on the debit txn_id.
        from ..services import money_guard as mg

        mg.credit(db, svc.ACCOUNT_TYPE, household_id, int(out["points"]),
                  reason="pool_redeem_mint_failed_reversal",
                  actor=current_user.get("user_id"), ref=out.get("txn_id"),
                  idempotency_key=f"poolredeem-reverse:{out.get('txn_id')}")
        raise HTTPException(status_code=503, detail="voucher_mint_failed")

    return {
        "ok": True,
        "household_id": household_id,
        "points_redeemed": int(out["points"]),
        "rupee_value": rupee_value,
        "pool_balance_points": int(out["balance"]),
        "txn_id": out.get("txn_id"),
        "voucher": {
            "voucher_id": voucher.get("voucher_id"),
            "code": voucher.get("code"),
            "balance": voucher.get("balance"),
            "expiry_date": voucher.get("expiry_date"),
        },
    }
