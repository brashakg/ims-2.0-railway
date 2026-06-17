"""
IMS 2.0 - CRM Router
====================
Enterprise CRM endpoints for customer 360 views, segmentation,
lifecycle management, and customer intelligence
"""

from fastapi import APIRouter, HTTPException, Depends, Query, Path, Body
from pydantic import BaseModel, Field
from typing import List, Optional, Literal
from datetime import datetime, date, timedelta
import re
import uuid
import logging

logger = logging.getLogger(__name__)

from .auth import get_current_user, require_roles
from ..dependencies import (
    get_customer_repository,
    get_loyalty_account_repository,
    get_order_repository,
    get_prescription_repository,
    filter_docs_by_store,
    get_task_repository,
    get_audit_repository,
)
from ..services.task_triggers import create_system_task

router = APIRouter()


# ============================================================================
# DATA ACCESS ADAPTER
# ============================================================================
# This router was originally written against a unified `db` query object that
# never existed. The adapter below provides the legacy query API expected by
# the endpoints below, delegating to the real repositories where possible.
# Missing data returns empty lists rather than 500s, which lets the frontend
# render the Customer 360 / CRM screens even when collections are empty.


class _CRMDataAdapter:
    """Adapter exposing the query API the CRM endpoints were written against."""

    def query_customer(self, customer_id: str):
        repo = get_customer_repository()
        return repo.find_by_id(customer_id) if repo else None

    def query_all_customers(self):
        repo = get_customer_repository()
        return repo.find_many({}, limit=0) if repo else []

    def query_customers_by_store(self, store_id: str):
        repo = get_customer_repository()
        if not repo:
            return []
        # Store is recorded under different keys across data sources:
        # TechCherry import uses `preferred_store_id`, native docs use
        # `home_store_id`, and older/legacy code wrote `primary_store_id`
        # or `store_id`. OR all four so no store-scoped customer is missed.
        return repo.find_many(
            {
                "$or": [
                    {"preferred_store_id": store_id},
                    {"home_store_id": store_id},
                    {"primary_store_id": store_id},
                    {"store_id": store_id},
                ]
            },
            limit=0,
        )

    def query_customer_orders(self, customer_id: str):
        repo = get_order_repository()
        return repo.find_by_customer(customer_id) if repo else []

    def query_customer_prescriptions(self, customer_id: str):
        repo = get_prescription_repository()
        if repo and hasattr(repo, "find_by_customer"):
            return repo.find_by_customer(customer_id)
        # Fallback: prescriptions embedded in customer document
        customer = self.query_customer(customer_id)
        return customer.get("prescriptions", []) if customer else []

    def query_customer_interactions(self, customer_id: str, limit: int = 100):
        # Interactions are stored as an array on the customer document.
        customer = self.query_customer(customer_id)
        if not customer:
            return []
        interactions = customer.get("interactions", []) or []
        return interactions[:limit]

    def create_customer_interaction(self, interaction_data: dict) -> bool:
        repo = get_customer_repository()
        if not repo:
            return False
        try:
            repo.collection.update_one(
                {"customer_id": interaction_data["customer_id"]},
                {"$push": {"interactions": interaction_data}},
            )
            return True
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning("Failed to persist customer interaction: %s", exc)
            return False


db = _CRMDataAdapter()


# ============================================================================
# SCHEMAS
# ============================================================================


class CustomerStatsResponse(BaseModel):
    """Customer lifetime value and engagement statistics"""

    total_lifetime_value: float
    total_orders: int
    last_order_date: Optional[str] = None
    last_order_amount: Optional[float] = None
    customer_since_date: str
    preferred_store: str
    average_order_value: float
    visit_frequency: float  # visits per month
    referral_count: int = 0
    active_loans: int = 0


class InteractionRecord(BaseModel):
    """Customer interaction history entry"""

    id: str
    type: Literal["call", "sms", "email", "whatsapp", "in_person"]
    date: str
    notes: str
    duration: Optional[int] = None  # minutes
    initiated_by: str  # 'Customer' or 'Business'


class LoyaltyTierResponse(BaseModel):
    """Customer loyalty program data"""

    tier: Literal["Bronze", "Silver", "Gold", "Platinum", "Diamond"]
    points: int
    points_to_next_tier: int
    redeemed_points: int
    total_points_earned: int
    member_since: str
    birthday_month: Optional[int] = None


class AddLoyaltyPointsRequest(BaseModel):
    """Request to add loyalty points to customer"""

    points: int = Field(..., gt=0, description="Loyalty points to add")
    reason: str = Field(default="Purchase", description="Reason for adding points")


class PrescriptionWithStatusResponse(BaseModel):
    """Prescription with renewal status"""

    id: str
    customer_id: str
    issue_date: str
    expiry_date: Optional[str] = None
    sph_od: Optional[float] = None
    cyl_od: Optional[float] = None
    axis_od: Optional[int] = None
    add_od: Optional[float] = None
    pd_od: Optional[float] = None
    sph_os: Optional[float] = None
    cyl_os: Optional[float] = None
    axis_os: Optional[int] = None
    add_os: Optional[float] = None
    pd_os: Optional[float] = None
    doctor_name: Optional[str] = None
    renewal_status: Literal["current", "upcoming", "expired"]
    days_until_renewal: Optional[int] = None


class Customer360Response(BaseModel):
    """Complete 360-degree customer view"""

    id: str
    name: str
    phone: str
    email: Optional[str]
    address: Optional[str]
    created_at: str
    stats: CustomerStatsResponse
    loyalty_data: LoyaltyTierResponse
    prescriptions: List[PrescriptionWithStatusResponse]
    interactions_count: int


class CustomerSegmentResponse(BaseModel):
    """Customer segmentation result"""

    segment_id: str
    segment_name: str
    customer_count: int
    avg_lifetime_value: float
    description: str


class LifecyclePhase(BaseModel):
    """Customer lifecycle phase"""

    phase: Literal["prospect", "new", "active", "at_risk", "inactive", "vip"]
    reason: str
    recommended_action: str


# ============================================================================
# ENDPOINTS
# ============================================================================


@router.get("")
@router.get("/")
async def get_crm_root():
    """Root endpoint for customer CRM overview"""
    return {
        "module": "crm",
        "status": "active",
        "message": "CRM overview endpoint ready",
    }


@router.get("/customers/360/{customer_id}", response_model=Customer360Response)
async def get_customer_360(
    customer_id: str = Path(..., description="Customer ID"),
    current_user: dict = Depends(get_current_user),
):
    """
    Get complete 360-degree customer view with stats, loyalty, prescriptions,
    and interaction history.

    **Includes:**
    - Lifetime value and order statistics
    - Loyalty program status with tier
    - Complete prescription history with renewal status
    - Recent interaction history
    - Engagement metrics
    """
    try:
        # Fetch customer from database
        customer = db.query_customer(customer_id)
        if not customer:
            raise HTTPException(status_code=404, detail="Customer not found")

        # Fetch orders and calculate stats
        orders = db.query_customer_orders(customer_id)
        stats = _calculate_customer_stats(customer, orders)

        # Calculate loyalty tier — pass customer_id so we read the real points
        # balance from loyalty_accounts, and customer for birthday_month from DOB.
        loyalty_data = _calculate_loyalty_tier(
            stats["total_lifetime_value"], customer["created_at"],
            customer_id=customer_id, customer_doc=customer,
        )

        # Fetch prescriptions with renewal status
        prescriptions = db.query_customer_prescriptions(customer_id)
        prescriptions_with_status = [
            _add_prescription_status(rx) for rx in prescriptions
        ]

        # Get interaction count
        interactions = db.query_customer_interactions(customer_id, limit=100)

        return {
            "id": customer_id,
            "name": customer["name"],
            "phone": customer["phone"],
            "email": customer.get("email"),
            "address": customer.get("address"),
            "created_at": customer["created_at"],
            "stats": stats,
            "loyalty_data": loyalty_data,
            "prescriptions": prescriptions_with_status,
            "interactions_count": len(interactions),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("CRM operation failed: %s", e)
        raise HTTPException(
            status_code=500, detail="An internal error occurred. Please try again."
        )


@router.get("/customers/{customer_id}/lifecycle", response_model=LifecyclePhase)
async def get_customer_lifecycle_phase(
    customer_id: str = Path(..., description="Customer ID"),
    current_user: dict = Depends(get_current_user),
):
    """
    Determine customer lifecycle phase based on engagement metrics.

    **Phases:**
    - prospect: No purchases yet
    - new: First purchase within 90 days
    - active: Regular purchases in last 6 months
    - at_risk: No purchases in 6+ months
    - inactive: No purchases in 12+ months
    - vip: High lifetime value (₹100k+) or frequent buyer
    """
    try:
        customer = db.query_customer(customer_id)
        if not customer:
            raise HTTPException(status_code=404, detail="Customer not found")

        orders = db.query_customer_orders(customer_id)
        return _determine_lifecycle_phase(customer, orders)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("CRM operation failed: %s", e)
        raise HTTPException(
            status_code=500, detail="An internal error occurred. Please try again."
        )


@router.get("/customers/segment/rfm", response_model=List[CustomerSegmentResponse])
async def get_rfm_segmentation(
    current_user: dict = Depends(get_current_user),
):
    """
    RFM (Recency, Frequency, Monetary) segmentation of all customers.

    **Segments:**
    - Champions: Recent, frequent, high spenders
    - Loyal Customers: Consistent, regular purchases
    - Big Spenders: High lifetime value
    - At Risk: Were regular, now declining engagement
    - Lost: No activity in 12+ months
    """
    try:
        all_customers = db.query_all_customers()
        segments = _perform_rfm_segmentation(all_customers)
        return segments
    except Exception as e:
        logger.error("CRM operation failed: %s", e)
        raise HTTPException(
            status_code=500, detail="An internal error occurred. Please try again."
        )


@router.get(
    "/customers/{customer_id}/interactions", response_model=List[InteractionRecord]
)
async def get_customer_interactions(
    customer_id: str = Path(..., description="Customer ID"),
    limit: int = Query(50, ge=1, le=500),
    interaction_type: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """
    Get complete interaction history for a customer including calls, SMS,
    emails, WhatsApp messages, and in-person visits.

    **Interaction Types:**
    - call: Phone calls with duration
    - sms: SMS messages
    - email: Email communications
    - whatsapp: WhatsApp messages
    - in_person: In-store or face-to-face visits
    """
    try:
        interactions = db.query_customer_interactions(customer_id, limit=limit)
        if interaction_type:
            interactions = [i for i in interactions if i["type"] == interaction_type]
        return interactions
    except Exception as e:
        logger.error("CRM operation failed: %s", e)
        raise HTTPException(
            status_code=500, detail="An internal error occurred. Please try again."
        )


@router.post("/customers/{customer_id}/interactions", response_model=InteractionRecord)
async def create_customer_interaction(
    customer_id: str = Path(..., description="Customer ID"),
    interaction: InteractionRecord = Body(...),
    current_user: dict = Depends(get_current_user),
):
    """Log a new customer interaction (call, message, visit, etc.)"""
    try:
        customer = db.query_customer(customer_id)
        if not customer:
            raise HTTPException(status_code=404, detail="Customer not found")

        interaction_id = str(uuid.uuid4())
        interaction_data = {
            "id": interaction_id,
            "customer_id": customer_id,
            **interaction.dict(),
        }
        db.create_customer_interaction(interaction_data)
        return interaction_data
    except HTTPException:
        raise
    except Exception as e:
        logger.error("CRM operation failed: %s", e)
        raise HTTPException(
            status_code=500, detail="An internal error occurred. Please try again."
        )


@router.get(
    "/customers/{customer_id}/prescriptions",
    response_model=List[PrescriptionWithStatusResponse],
)
async def get_customer_prescriptions(
    customer_id: str = Path(..., description="Customer ID"),
    current_user: dict = Depends(get_current_user),
):
    """Get all prescriptions for a customer with renewal status indicators"""
    try:
        # BUG-088: clinical Rx is store-scoped PII -- never return another store's
        # prescriptions to a store-level caller (admins are cross-store).
        prescriptions = filter_docs_by_store(
            db.query_customer_prescriptions(customer_id), current_user
        )
        return [_add_prescription_status(rx) for rx in prescriptions]
    except Exception as e:
        logger.error("CRM operation failed: %s", e)
        raise HTTPException(
            status_code=500, detail="An internal error occurred. Please try again."
        )


@router.get("/customers/churn-risk/list", response_model=List[dict])
async def get_churn_risk_customers(
    risk_level: Literal["high", "medium", "low"] = Query("high"),
    limit: int = Query(50, ge=1, le=500),
    current_user: dict = Depends(get_current_user),
):
    """
    Get list of customers at risk of churning based on engagement metrics.

    **Risk Levels:**
    - high: No purchases in 6+ months, was previously active
    - medium: Declining purchase frequency
    - low: Minor engagement decline
    """
    try:
        store_id = current_user.get("active_store_id")
        # Filter customers by store to prevent cross-store data leakage
        if store_id and hasattr(db, "query_customers_by_store"):
            all_customers = db.query_customers_by_store(store_id)
        else:
            all_customers = db.query_all_customers()
            # Fallback: filter in-memory if store_id is available
            if store_id:
                all_customers = [
                    c
                    for c in all_customers
                    if store_id
                    in (c.get("store_ids", []) + [c.get("primary_store_id", "")])
                ]
        at_risk = _identify_churn_risk_customers(all_customers, risk_level)
        return at_risk[:limit]
    except Exception as e:
        logger.error("Churn risk query failed: %s", e)
        raise HTTPException(
            status_code=500, detail="Failed to retrieve churn risk data"
        )


# ============================================================================
# F40 - VIP churn watchlist (#40)
# Personalised-interval VIP churn (vip_churn_risk subdoc written nightly by
# ORACLE's EOD scan). Read-only watchlist + an admin "Intervene" action that
# creates a P1 task (deduped per 30-day window). SUPERADMIN/ADMIN only;
# ADMIN is store-scoped. Never touches orders / prices / balances.
# ============================================================================

_VIP_INTERVENTIONS = (
    "PERSONAL_CALL",
    "EXCLUSIVE_OFFER",
    "LOYALTY_BONUS",
    "WINBACK_WHATSAPP",
)


class VipInterveneBody(BaseModel):
    intervention_type: Literal[
        "PERSONAL_CALL", "EXCLUSIVE_OFFER", "LOYALTY_BONUS", "WINBACK_WHATSAPP"
    ]
    notes: str = Field(default="", max_length=500)


def _vip_store_guard(current_user: dict, store_id: Optional[str]) -> Optional[str]:
    """SUPERADMIN sees any/all stores; ADMIN must operate within an owned store.
    Returns the effective store_id (may be None for SUPERADMIN all-stores)."""
    roles = current_user.get("roles", []) or []
    if "SUPERADMIN" in roles:
        return store_id
    owned = list(current_user.get("store_ids", []) or [])
    active = current_user.get("active_store_id")
    if active and active not in owned:
        owned.append(active)
    if store_id is None:
        store_id = active or (owned[0] if owned else None)
    if store_id not in owned:
        raise HTTPException(status_code=403, detail="Not permitted for this store")
    return store_id


@router.get("/vip-churn")
async def get_vip_churn_watchlist(
    store_id: Optional[str] = Query(None),
    risk_label: Optional[Literal["WATCH", "HIGH"]] = Query(None),
    sort_by: Literal["overdue_by_days", "ltv", "last_purchase_days_ago"] = Query(
        "overdue_by_days"
    ),
    limit: int = Query(50, ge=1, le=500),
    current_user: dict = Depends(require_roles("SUPERADMIN", "ADMIN")),
):
    """Ranked VIP-churn watchlist (WATCH/HIGH) + the latest daily snapshot trend.
    Fail-soft: no DB -> empty envelope (200, never 500)."""
    store_id = _vip_store_guard(current_user, store_id)
    db = _crm_get_db()
    if db is None:
        return {"customers": [], "trend": None, "total": 0}
    labels = [risk_label] if risk_label else ["WATCH", "HIGH"]
    query: dict = {"vip_churn_risk.risk_label": {"$in": labels}}
    if store_id:
        query["$or"] = [
            {"store_ids": store_id},
            {"primary_store_id": store_id},
            {"store_id": store_id},
        ]
    try:
        rows = list(db.get_collection("customers").find(query, {"_id": 0}))
    except Exception:  # noqa: BLE001
        rows = []

    def _ltv(c: dict) -> float:
        return float(c.get("total_lifetime_value", c.get("ltv", 0)) or 0)

    def _sort_key(c: dict):
        if sort_by == "ltv":
            return -_ltv(c)
        return -float((c.get("vip_churn_risk") or {}).get(sort_by, 0) or 0)

    rows.sort(key=_sort_key)
    customers = []
    for c in rows[:limit]:
        vr = c.get("vip_churn_risk") or {}
        customers.append(
            {
                "customer_id": c.get("customer_id"),
                "name": c.get("name") or c.get("full_name") or "",
                "store_id": c.get("primary_store_id")
                or (c.get("store_ids") or [None])[0],
                "ltv": round(_ltv(c), 2),
                "vip_churn_risk": {
                    k: vr.get(k)
                    for k in (
                        "usual_interval_days",
                        "last_purchase_days_ago",
                        "overdue_by_days",
                        "risk_score",
                        "risk_label",
                        "narrative",
                    )
                },
            }
        )
    trend = None
    try:
        snap_q = {"store_id": store_id} if store_id else {}
        snap = list(
            db.get_collection("vip_churn_snapshots")
            .find(snap_q, {"_id": 0})
            .sort("scanned_at", -1)
            .limit(1)
        )
        if snap:
            s = snap[0]
            trend = {
                "scanned_at": s.get("scanned_at"),
                "vip_count": s.get("vip_count"),
                "watch_count": s.get("watch_count"),
                "high_risk_count": s.get("high_risk_count"),
            }
    except Exception:  # noqa: BLE001
        trend = None
    return {"customers": customers, "trend": trend, "total": len(customers)}


@router.post("/vip-churn/{customer_id}/intervene")
async def intervene_vip_churn(
    customer_id: str,
    body: VipInterveneBody,
    current_user: dict = Depends(require_roles("SUPERADMIN", "ADMIN")),
):
    """Create a P1 CRM task for a VIP-churn intervention (deduped per 30-day window
    via the tasks engine). Audited. WINBACK_WHATSAPP additionally queues a PENDING
    notification row that MEGAPHONE drains (honouring DISPATCH_MODE) -- it does NOT
    send synchronously. No order/price/balance is touched."""
    cust = None
    try:
        repo = get_customer_repository()
        cust = repo.find_by_id(customer_id) if repo else None
    except Exception:  # noqa: BLE001
        cust = None
    if not cust:
        raise HTTPException(status_code=404, detail="customer not found")
    # The intervene target store is the CUSTOMER's own, resolved across EVERY
    # canonical store field (TechCherry=preferred_store_id, native=home_store_id,
    # legacy=primary_store_id/store_ids/store_id). Do NOT backfill to the caller's
    # store -- a non-SUPERADMIN who doesn't own the customer's store is DENIED
    # (was a cross-store write IDOR: P1-grade task/audit/notification on any store).
    store_id = (
        cust.get("preferred_store_id")
        or cust.get("home_store_id")
        or cust.get("primary_store_id")
        or (cust.get("store_ids") or [None])[0]
        or cust.get("store_id")
    )
    roles = current_user.get("roles", []) or []
    if "SUPERADMIN" not in roles:
        owned = list(current_user.get("store_ids", []) or [])
        active = current_user.get("active_store_id")
        if active and active not in owned:
            owned.append(active)
        if not store_id or store_id not in owned:
            raise HTTPException(
                status_code=403,
                detail="Not permitted to intervene for this customer's store",
            )

    # 30-day rolling window so a fresh window allows a new task; same window dedupes.
    window = (datetime.now() - datetime(2020, 1, 1)).days // 30
    dedupe_ref = f"vip_intervene:{customer_id}:{window}"
    task = create_system_task(
        repo=get_task_repository(),
        title=f"VIP win-back ({body.intervention_type})",
        description=(body.notes or "")[:500]
        or f"VIP churn intervention for {customer_id}",
        priority="P1",
        category="CRM",
        store_id=store_id,
        dedupe_ref=dedupe_ref,
    )
    already_intervened = task is None

    try:
        arepo = get_audit_repository()
        if arepo is not None:
            arepo.create(
                {
                    "action": "VIP_CHURN_INTERVENTION",
                    "entity_type": "customer",
                    "entity_id": customer_id,
                    "user_id": current_user.get("user_id"),
                    "user_name": current_user.get("full_name")
                    or current_user.get("username"),
                    "store_id": store_id,
                    "severity": "INFO",
                    "source": "crm",
                    "before_state": {},
                    "after_state": {
                        "intervention_type": body.intervention_type,
                        "notes": body.notes,
                        "deduped": already_intervened,
                    },
                }
            )
    except Exception:  # noqa: BLE001
        pass

    if body.intervention_type == "WINBACK_WHATSAPP" and not already_intervened:
        db = _crm_get_db()
        if db is not None:
            try:
                phone = (cust or {}).get("mobile") or (cust or {}).get("phone")
                db.get_collection("notification_logs").insert_one(
                    {
                        "notification_id": f"NTF-VIP-{uuid.uuid4().hex[:10]}",
                        "kind": "vip_winback",
                        "channel": "whatsapp",
                        "customer_id": customer_id,
                        "customer_phone": phone,
                        "status": "PENDING",
                        "created_at": datetime.now(),
                    }
                )
            except Exception:  # noqa: BLE001
                pass

    return {
        "ok": True,
        "task_id": (task or {}).get("task_id"),
        "intervention_type": body.intervention_type,
        "already_intervened": already_intervened,
    }


# ============================================================================
# F39 - NBA (next-best-action) daily call list (#39)
# A ranked daily list of customers a STORE associate should MANUALLY PHONE
# today. This is a CALL LIST, not a message channel: nothing here sends
# WhatsApp/SMS. Marking a card done/skipped records an in-app follow_up doc
# (the durable audit), never a provider send.
#
# Reuses the merged campaign_segments resolvers + the persisted vip_churn_risk
# subdoc (READ, never recomputed). 15 cards/day with 2 reserved VIP slots, both
# caps from E2 policy. Single-doc writes only (standalone Mongo, no transactions).
# ============================================================================


class NbaDismissBody(BaseModel):
    customer_id: str = Field(..., description="Customer whose card is being skipped")
    reason: Literal["not_interested", "already_called", "no_answer", "wrong_number"]


class NbaCompleteBody(BaseModel):
    customer_id: str = Field(..., description="Customer whose card is being completed")
    outcome_notes: str = Field(..., min_length=10, max_length=2000)
    follow_up_scheduled_date: Optional[str] = Field(
        default=None, description="Optional YYYY-MM-DD to schedule a next follow-up"
    )


def _nba_card_for(doc: dict, customer_id: str) -> Optional[dict]:
    for card in (doc or {}).get("cards", []):
        if card.get("customer_id") == customer_id:
            return card
    return None


def _nba_audit(
    action: str, customer_id: str, store_id: str, current_user: dict, detail: dict
) -> None:
    """Best-effort audit row. Fail-soft -- never undoes the NBA write."""
    try:
        arepo = get_audit_repository()
        if arepo is None:
            return
        arepo.create(
            {
                "action": action,
                "entity_type": "customer",
                "entity_id": customer_id,
                "user_id": current_user.get("user_id") or current_user.get("id"),
                "user_name": current_user.get("full_name")
                or current_user.get("username"),
                "store_id": store_id,
                "severity": "INFO",
                "source": "crm.nba",
                "before_state": {},
                "after_state": detail,
            }
        )
    except Exception:  # noqa: BLE001
        pass


@router.get("/nba/{store_id}")
async def get_nba_call_list(
    store_id: str = Path(..., description="Store ID"),
    date: Optional[str] = Query(
        None, description="YYYY-MM-DD; defaults to today (IST)"
    ),
    current_user: dict = Depends(get_current_user),
):
    """Today's ranked NBA call list for a store (max cards/day, 2 reserved VIP
    slots). Reads the MEGAPHONE-written nba_scores doc; if absent (the agent has
    not run yet), scores synchronously as a fallback. The internal `score` is
    NEVER in the response -- associates see `rank`. Dismissed cards are excluded.

    Store-scoped (validate_store_access): a store-scoped role gets 403 for another
    store. Fail-soft: no DB -> empty list."""
    from fastapi import HTTPException as _HX

    from ..dependencies import validate_store_access
    from ..services import nba_call_list as nba

    store_id = validate_store_access(store_id, current_user)
    db = _crm_get_db()
    target_date = date or nba._today_ist()
    if db is None:
        return {
            "store_id": store_id,
            "date": target_date,
            "generated_at": None,
            "cards": [],
        }

    doc = None
    try:
        doc = db.get_collection("nba_scores").find_one(
            {"store_id": store_id, "date": target_date}
        )
    except Exception:  # noqa: BLE001
        doc = None

    if doc:
        return {
            "store_id": store_id,
            "date": target_date,
            "generated_at": doc.get("generated_at"),
            "cards": nba.public_cards(doc.get("cards", [])),
        }

    # Fallback: score synchronously (capped) so the page is never empty just
    # because the agent has not ticked yet.
    cards = nba.score_nba(db, store_id, max_customers=200)
    return {
        "store_id": store_id,
        "date": target_date,
        "generated_at": datetime.utcnow().isoformat(),
        "cards": nba.public_cards(cards),
    }


@router.post("/nba/{store_id}/dismiss")
async def dismiss_nba_card(
    store_id: str = Path(..., description="Store ID"),
    body: NbaDismissBody = Body(...),
    current_user: dict = Depends(get_current_user),
):
    """Skip a card: mark it dismissed in today's nba_scores doc (single-doc
    update) and resolve its pre-created follow_up to status=skipped with the
    reason. Writes an audit row. A dismissed customer does not reappear today."""
    from ..dependencies import validate_store_access
    from ..services import nba_call_list as nba

    store_id = validate_store_access(store_id, current_user)
    db = _crm_get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    target_date = nba._today_ist()
    # Single-document update on nba_scores: flip the matching card's dismissed flag.
    updated = db.get_collection("nba_scores").find_one_and_update(
        {
            "store_id": store_id,
            "date": target_date,
            "cards.customer_id": body.customer_id,
        },
        {"$set": {"cards.$.dismissed": True}},
        return_document=True,
    )
    card = _nba_card_for(updated, body.customer_id) if updated else None
    follow_up_id = (card or {}).get("follow_up_id")

    # Resolve the linked follow_up (single-document update on follow_ups).
    if follow_up_id:
        try:
            db.get_collection("follow_ups").find_one_and_update(
                {"follow_up_id": follow_up_id, "store_id": store_id},
                {
                    "$set": {
                        "status": "skipped",
                        "outcome": body.reason,
                        "completed_at": datetime.now().isoformat(),
                        "completed_by": current_user.get("user_id")
                        or current_user.get("id"),
                    }
                },
            )
        except Exception:  # noqa: BLE001
            pass

    _nba_audit(
        "nba.dismissed",
        body.customer_id,
        store_id,
        current_user,
        {"reason": body.reason, "follow_up_id": follow_up_id},
    )
    return {"ok": True}


@router.post("/nba/{store_id}/complete")
async def complete_nba_card(
    store_id: str = Path(..., description="Store ID"),
    body: NbaCompleteBody = Body(...),
    current_user: dict = Depends(get_current_user),
):
    """Complete a card after the staff member calls the customer: mark it
    dismissed in today's nba_scores doc, resolve the pre-created follow_up to
    status=completed with the outcome notes, and optionally insert a NEW follow_up
    for a scheduled next touch. Writes an audit row. NO message is sent -- this is
    a pure in-app record of a manual call (WhatsApp ban; F39 is dark)."""
    from ..dependencies import validate_store_access
    from ..services import nba_call_list as nba

    store_id = validate_store_access(store_id, current_user)
    db = _crm_get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    target_date = nba._today_ist()
    updated = db.get_collection("nba_scores").find_one_and_update(
        {
            "store_id": store_id,
            "date": target_date,
            "cards.customer_id": body.customer_id,
        },
        {"$set": {"cards.$.dismissed": True}},
        return_document=True,
    )
    card = _nba_card_for(updated, body.customer_id) if updated else None
    follow_up_id = (card or {}).get("follow_up_id")

    now_iso = datetime.now().isoformat()
    user_id = current_user.get("user_id") or current_user.get("id")
    if follow_up_id:
        try:
            db.get_collection("follow_ups").find_one_and_update(
                {"follow_up_id": follow_up_id, "store_id": store_id},
                {
                    "$set": {
                        "status": "completed",
                        "outcome": "completed",
                        "notes": body.outcome_notes,
                        "completed_at": now_iso,
                        "completed_by": user_id,
                    }
                },
            )
        except Exception:  # noqa: BLE001
            pass

    next_follow_up_id = None
    if body.follow_up_scheduled_date:
        next_follow_up_id = (
            f"FU-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}"
        )
        try:
            db.get_collection("follow_ups").insert_one(
                {
                    "follow_up_id": next_follow_up_id,
                    "customer_id": body.customer_id,
                    "customer_name": (card or {}).get("customer_name", ""),
                    "customer_phone": (card or {}).get("customer_mobile", ""),
                    "store_id": store_id,
                    "type": "general",
                    "scheduled_date": body.follow_up_scheduled_date,
                    "status": "pending",
                    "outcome": None,
                    "notes": "Scheduled from NBA call list",
                    "created_at": now_iso,
                    "completed_at": None,
                    "completed_by": None,
                }
            )
        except Exception:  # noqa: BLE001
            next_follow_up_id = None

    _nba_audit(
        "nba.completed",
        body.customer_id,
        store_id,
        current_user,
        {"follow_up_id": follow_up_id, "next_follow_up_id": next_follow_up_id},
    )
    return {"ok": True, "next_follow_up_id": next_follow_up_id}


# ============================================================================
# F41 - Lapsed-patient reactivation (#41)
# An in-app, per-store REACTIVATION WORK-LIST of clinically lapsed patients (no
# confirmed order AND no prescription exam in the lapse window, default 24
# months). This is a WORK-LIST, not a message channel: nothing here sends
# WhatsApp/SMS and nothing mints a voucher. Marking an entry Reached/Skipped
# records an in-app reactivation_call follow_up doc (the durable audit), NEVER a
# provider send (WhatsApp ban -- STATUS COMMS DIRECTIVE; #41 reactivation-send is
# DEFERRED; F41 ships DARK exactly like the #39 NBA call list).
#
# Reuses the merged campaign_segments._resolve_lapsed_patient resolver + the
# persisted vip_churn_risk subdoc (READ, never recomputed). Lapse window + cohort
# size from E2 policy. Single-doc writes only (standalone Mongo, no transactions).
# ============================================================================


class ReactivationLogBody(BaseModel):
    customer_id: str = Field(..., description="Lapsed patient being actioned")
    outcome: Literal[
        "reached", "no_answer", "not_interested", "wrong_number", "scheduled_visit"
    ]
    notes: str = Field(default="", max_length=2000)
    follow_up_scheduled_date: Optional[str] = Field(
        default=None,
        description="Optional YYYY-MM-DD to schedule a next reactivation touch",
    )


def _reactivation_entry_for(doc: dict, customer_id: str) -> Optional[dict]:
    for e in (doc or {}).get("entries", []):
        if e.get("customer_id") == customer_id:
            return e
    return None


@router.get("/reactivation/{store_id}")
async def get_reactivation_worklist(
    store_id: str = Path(..., description="Store ID"),
    date: Optional[str] = Query(
        None, description="YYYY-MM-DD; defaults to today (IST)"
    ),
    preview: bool = Query(False, description="Read-only: never persists a cohort doc"),
    current_user: dict = Depends(get_current_user),
):
    """Today's reactivation work-list for a store: ranked lapsed patients (VIPs
    first, then most-lapsed), capped by the E2 cohort size. Reads the
    MEGAPHONE-built reactivation_cohorts doc; if absent, builds synchronously as a
    fallback so the page is never empty. `preview=true` ALWAYS computes live and
    NEVER persists (a pure read-only count for Settings).

    Store-scoped (validate_store_access): a store-scoped role gets 403 for another
    store. NO message is sent and NO voucher is minted -- this is an in-app
    work-list only. Fail-soft: no DB -> empty list."""
    from ..dependencies import validate_store_access
    from ..services import lapsed_reactivation as react

    store_id = validate_store_access(store_id, current_user)
    db = _crm_get_db()
    target_date = date or react._today_ist()
    if db is None:
        return {
            "store_id": store_id,
            "date": target_date,
            "generated_at": None,
            "entries": [],
        }

    if not preview:
        doc = None
        try:
            doc = db.get_collection("reactivation_cohorts").find_one(
                {"store_id": store_id, "date": target_date}
            )
        except Exception:  # noqa: BLE001
            doc = None
        if doc:
            return {
                "store_id": store_id,
                "date": target_date,
                "generated_at": doc.get("generated_at"),
                "lapse_months": doc.get("lapse_months"),
                "entries": react.public_entries(doc.get("entries", [])),
            }

    # Fallback / preview: build synchronously (read-only -- no persist, no send).
    entries = react.build_cohort(db, store_id)
    return {
        "store_id": store_id,
        "date": target_date,
        "generated_at": datetime.utcnow().isoformat(),
        "entries": react.public_entries(entries),
    }


@router.post("/reactivation/{store_id}/log")
async def log_reactivation_outcome(
    store_id: str = Path(..., description="Store ID"),
    body: ReactivationLogBody = Body(...),
    current_user: dict = Depends(get_current_user),
):
    """Record the outcome of a reactivation outreach after the staff member calls
    / visits the lapsed patient: mark the entry done in today's
    reactivation_cohorts doc (single-doc update), resolve the linked
    reactivation_call follow_up (creating one if MEGAPHONE has not pre-created it),
    and optionally schedule a NEXT reactivation touch. Writes an audit row.

    A customer NOT on today's persisted work-list is a 404 (not_on_todays_list)
    and writes NOTHING -- an off-list outcome must not orphan a reactivation_call
    follow_up into the analytics (audit F41-P3).

    NO message is sent and NO voucher is minted -- this is a pure in-app record of
    a manual outreach (WhatsApp ban; F41 is dark)."""
    from ..dependencies import validate_store_access
    from ..services import lapsed_reactivation as react

    store_id = validate_store_access(store_id, current_user)
    db = _crm_get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    target_date = react._today_ist()
    updated = db.get_collection("reactivation_cohorts").find_one_and_update(
        {
            "store_id": store_id,
            "date": target_date,
            "entries.customer_id": body.customer_id,
        },
        {"$set": {"entries.$.dismissed": True}},
        return_document=True,
    )
    if updated is None:
        # Off-list outcome (audit F41-P3): the customer is NOT on today's
        # persisted work-list (no cohort doc for today, or no matching entry).
        # Inserting a reactivation_call follow_up here would orphan-pollute the
        # analytics, so record NOTHING and fail loudly.
        raise HTTPException(status_code=404, detail="not_on_todays_list")
    entry = _reactivation_entry_for(updated, body.customer_id)
    follow_up_id = (entry or {}).get("follow_up_id")
    now_iso = datetime.now().isoformat()
    user_id = current_user.get("user_id") or current_user.get("id")
    reached = body.outcome in ("reached", "scheduled_visit")

    if follow_up_id:
        try:
            db.get_collection("follow_ups").find_one_and_update(
                {"follow_up_id": follow_up_id, "store_id": store_id},
                {
                    "$set": {
                        "status": "completed" if reached else "skipped",
                        "outcome": body.outcome,
                        "notes": body.notes,
                        "completed_at": now_iso,
                        "completed_by": user_id,
                    }
                },
            )
        except Exception:  # noqa: BLE001
            pass
    else:
        # MEGAPHONE had not pre-created one (e.g. synchronous fallback work-list):
        # write the in-app reactivation_call follow_up RECORD now (NOT a message).
        follow_up_id = (
            f"FU-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}"
        )
        try:
            db.get_collection("follow_ups").insert_one(
                {
                    "follow_up_id": follow_up_id,
                    "customer_id": body.customer_id,
                    "customer_name": (entry or {}).get("customer_name", ""),
                    "customer_phone": (entry or {}).get("customer_mobile", ""),
                    "store_id": store_id,
                    "type": "reactivation_call",
                    "scheduled_date": target_date,
                    "status": "completed" if reached else "skipped",
                    "outcome": body.outcome,
                    "notes": body.notes,
                    "created_at": now_iso,
                    "completed_at": now_iso,
                    "completed_by": user_id,
                }
            )
        except Exception:  # noqa: BLE001
            follow_up_id = None

    next_follow_up_id = None
    if body.follow_up_scheduled_date:
        next_follow_up_id = (
            f"FU-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}"
        )
        try:
            db.get_collection("follow_ups").insert_one(
                {
                    "follow_up_id": next_follow_up_id,
                    "customer_id": body.customer_id,
                    "customer_name": (entry or {}).get("customer_name", ""),
                    "customer_phone": (entry or {}).get("customer_mobile", ""),
                    "store_id": store_id,
                    "type": "reactivation_call",
                    "scheduled_date": body.follow_up_scheduled_date,
                    "status": "pending",
                    "outcome": None,
                    "notes": "Scheduled from reactivation work-list",
                    "created_at": now_iso,
                    "completed_at": None,
                    "completed_by": None,
                }
            )
        except Exception:  # noqa: BLE001
            next_follow_up_id = None

    _nba_audit(
        "reactivation.logged",
        body.customer_id,
        store_id,
        current_user,
        {
            "outcome": body.outcome,
            "follow_up_id": follow_up_id,
            "next_follow_up_id": next_follow_up_id,
        },
    )
    return {
        "ok": True,
        "follow_up_id": follow_up_id,
        "next_follow_up_id": next_follow_up_id,
    }


@router.get("/reactivation/{store_id}/analytics")
async def get_reactivation_analytics(
    store_id: str = Path(..., description="Store ID"),
    days: int = Query(90, ge=1, le=365, description="Look-back window (days)"),
    current_user: dict = Depends(get_current_user),
):
    """Reactivation outcomes for a store over the look-back window, aggregated from
    the in-app reactivation_call follow_up records (the durable outcome log -- NOT
    a notification/send log). Returns total reached / no_answer / not_interested /
    scheduled_visit + the count of patients currently on the live work-list.

    Store-scoped. Read-only. Fail-soft: no DB -> zeroed envelope."""
    from ..dependencies import validate_store_access
    from ..services import lapsed_reactivation as react

    store_id = validate_store_access(store_id, current_user)
    db = _crm_get_db()
    empty = {
        "store_id": store_id,
        "window_days": days,
        "logged": 0,
        "reached": 0,
        "no_answer": 0,
        "not_interested": 0,
        "scheduled_visit": 0,
        "wrong_number": 0,
        "currently_lapsed": 0,
    }
    if db is None:
        return empty

    since_iso = (datetime.now() - timedelta(days=days)).isoformat()
    counts = {
        "reached": 0,
        "no_answer": 0,
        "not_interested": 0,
        "scheduled_visit": 0,
        "wrong_number": 0,
    }
    logged = 0
    try:
        for fu in (
            db.get_collection("follow_ups")
            .find(
                {
                    "store_id": store_id,
                    "type": "reactivation_call",
                    "completed_at": {"$gte": since_iso},
                },
                {"_id": 0, "outcome": 1},
            )
            .limit(50000)
        ):
            logged += 1
            oc = str(fu.get("outcome") or "")
            if oc in counts:
                counts[oc] += 1
    except Exception:  # noqa: BLE001
        pass

    currently_lapsed = 0
    try:
        currently_lapsed = len(react.build_cohort(db, store_id))
    except Exception:  # noqa: BLE001
        currently_lapsed = 0

    return {
        "store_id": store_id,
        "window_days": days,
        "logged": logged,
        **counts,
        "currently_lapsed": currently_lapsed,
    }


@router.get("/customers/{customer_id}/cl-refill-status")
async def get_cl_refill_status(
    customer_id: str = Path(..., description="Customer ID"),
    current_user: dict = Depends(get_current_user),
):
    """
    Contact-lens auto-refill signal for a customer (CRM-2).

    Looks at the customer's most recent contact-lens order, uses the SKU's
    pack_size + daily_wear flag (assumed 1 lens/eye/day for daily disposables,
    or pack_size/30 days for monthlies) to predict when they will run out,
    and returns a refill_due_date + days_remaining.

    Fail-soft: returns a safe empty result when the DB is unavailable or no
    CL orders are found.

    GET /crm/customers/{customer_id}/cl-refill-status
    """
    db_conn = _crm_get_db()
    empty = {
        "customer_id": customer_id,
        "has_cl_history": False,
        "refill_due_date": None,
        "days_remaining": None,
        "last_cl_order_id": None,
        "last_cl_order_date": None,
        "sku": None,
        "modality": None,
        "pack_size": None,
        "note": "No contact-lens order history found",
    }
    if db_conn is None:
        return empty

    try:
        # CL categories used across the codebase (see inventory.py _CL_CATEGORIES).
        cl_categories = {
            "CONTACT_LENS",
            "CONTACT_LENSES",
            "CONTACT LENS",
            "CONTACT LENSES",
            "CL",
            "CONTACTS",
        }

        # Find the customer's most recent order that contains a CL line item.
        # Scan orders newest-first; stop on the first CL hit.
        orders_coll = db_conn.get_collection("orders")
        cursor = (
            orders_coll.find(
                {"customer_id": customer_id},
                {"_id": 0, "order_id": 1, "created_at": 1, "items": 1, "order_date": 1},
            )
            .sort("created_at", -1)
            .limit(50)
        )

        cl_line = None
        cl_order = None
        for order in cursor:
            for item in order.get("items") or []:
                cat = str(item.get("category") or item.get("item_type") or "").upper()
                if cat in cl_categories:
                    cl_line = item
                    cl_order = order
                    break
            if cl_line is not None:
                break

        if cl_line is None:
            return empty

        # Refill maths via the shared pure helper so this per-customer view and
        # the store worklist (GET /crm/cl-refill/{store_id}/due) never diverge.
        from ..services import cl_refill as clr

        order_date_raw = cl_order.get("created_at") or cl_order.get("order_date")
        refill = clr.compute_refill(cl_line, order_date_raw)
        days_remaining = refill["days_remaining"]

        return {
            "customer_id": customer_id,
            "has_cl_history": True,
            "refill_due_date": refill["refill_due_date"],
            "days_remaining": days_remaining,
            "last_cl_order_id": cl_order.get("order_id"),
            "last_cl_order_date": refill["last_cl_order_date"],
            "sku": refill["sku"] or "",
            "modality": refill["modality"],
            "pack_size": refill["pack_size"],
            "note": (
                "Refill overdue"
                if days_remaining < 0
                else (
                    "Refill due within 7 days"
                    if days_remaining <= 7
                    else "Refill due within 30 days" if days_remaining <= 30 else None
                )
            ),
        }
    except Exception as exc:
        logger.warning("[CRM] cl-refill-status failed for %s: %s", customer_id, exc)
        return empty


# ---------------------------------------------------------------------------
# CL auto-refill IN-APP trigger (CRM-2 phase 2): a store worklist + a deduped
# SYSTEM-task creator. NO outbound message -- the customer-facing WhatsApp/SMS
# send stays dark. Staff follow-up only.
# ---------------------------------------------------------------------------


class CLRefillReminderBody(BaseModel):
    """Optional knobs for the reminder-creator. Defaults match the worklist."""

    due_within_days: int = Field(
        14, ge=0, le=120, description="Refill-due horizon (days)"
    )
    assigned_to: Optional[str] = Field(
        None, description="User to own the reminder tasks (default: the actor)"
    )


@router.get("/cl-refill/{store_id}/due")
async def get_cl_refill_worklist(
    store_id: str = Path(..., description="Store ID"),
    due_within_days: int = Query(
        14, ge=0, le=120, description="Refill-due horizon (days)"
    ),
    current_user: dict = Depends(get_current_user),
):
    """In-app CL refill-due worklist for a store: customers whose contact-lens
    refill is due within the horizon (default 14 days) or already overdue,
    most-overdue first. Read-only -- NO message sent.

    Store-scoped (validate_store_access): a store-scoped role gets 403 for
    another store. Fail-soft: no DB -> empty list.
    """
    from ..dependencies import validate_store_access
    from ..services import cl_refill as clr

    store_id = validate_store_access(store_id, current_user)
    db = _crm_get_db()
    rows = clr.scan_due_refills(db, store_id, due_within_days=due_within_days)
    return {
        "store_id": store_id,
        "due_within_days": due_within_days,
        "generated_at": datetime.utcnow().isoformat(),
        "count": len(rows),
        "overdue_count": sum(1 for r in rows if r.get("overdue")),
        "items": rows,
    }


@router.post("/cl-refill/{store_id}/create-reminders")
async def create_cl_refill_reminders(
    store_id: str = Path(..., description="Store ID"),
    body: CLRefillReminderBody = Body(default=CLRefillReminderBody()),
    current_user: dict = Depends(get_current_user),
):
    """Turn the CL refill-due worklist into deduped in-app SYSTEM tasks (the
    SAME task engine the SLA/variance reminders use, so each rides the existing
    bell + escalation ladder). One task PER due customer, deduped by
    source_ref=cl_refill:{customer_id}:{refill_due_date} so a re-run never
    double-creates. NO outbound message is sent.

    Store-scoped. Manager+ (creating follow-up work for the store).
    """
    from ..dependencies import validate_store_access
    from ..services import cl_refill as clr

    _MANAGE = {"SUPERADMIN", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"}
    roles = {str(r).upper() for r in (current_user.get("roles", []) or [])}
    if not (roles & _MANAGE):
        raise HTTPException(
            status_code=403, detail="not permitted to create refill reminders"
        )

    store_id = validate_store_access(store_id, current_user)
    db = _crm_get_db()
    rows = clr.scan_due_refills(db, store_id, due_within_days=body.due_within_days)

    repo = get_task_repository()
    assigned_to = body.assigned_to or current_user.get("user_id")
    created: List[dict] = []
    deduped = 0
    for r in rows:
        cid = r.get("customer_id")
        due_date = r.get("refill_due_date")
        days = int(r.get("days_remaining") or 0)
        name = r.get("customer_name") or cid
        when = "overdue" if r.get("overdue") else f"due in {days}d"
        task = create_system_task(
            repo,
            title=f"Contact-lens refill {when}: {name}",
            description=(
                f"Customer {name} ({cid}) is {when} for a contact-lens refill "
                f"(due {due_date}, last order {r.get('last_cl_order_id')}). "
                f"Call to reorder. SKU {r.get('sku') or 'n/a'}, "
                f"modality {r.get('modality') or 'n/a'}."
            ),
            priority=clr.refill_task_priority(days),
            category="CRM",
            store_id=store_id,
            dedupe_ref=f"cl_refill:{cid}:{due_date}",
            assigned_to=assigned_to,
        )
        if task is None:
            deduped += 1
        else:
            created.append(
                {"task_id": task.get("task_id"), "customer_id": cid}
            )

    return {
        "store_id": store_id,
        "due_within_days": body.due_within_days,
        "candidates": len(rows),
        "created": len(created),
        "deduped": deduped,
        "tasks": created,
    }


@router.get("/customers/{customer_id}/return-risk")
async def get_customer_return_risk(
    customer_id: str = Path(..., description="Customer ID"),
    current_user: dict = Depends(get_current_user),
):
    """
    Return-abuse / serial-returner risk signal for a customer (CRM-5).

    Computes the customer's return-rate (returns / orders) and total return
    count from the `returns` collection. Flags customers with >= 3 returns or
    a return_rate >= 30% as HIGH risk, 1-2 returns or 15-29% rate as MEDIUM,
    else LOW (or NONE for no history). This is an ADVISORY read-only signal
    -- it never blocks a transaction. The risk score is surfaced on Customer
    360 for staff visibility.

    Fail-soft: returns NONE risk when the DB is unavailable.

    GET /crm/customers/{customer_id}/return-risk
    """
    db_conn = _crm_get_db()
    if db_conn is None:
        return {
            "customer_id": customer_id,
            "return_count": 0,
            "order_count": 0,
            "return_rate_pct": 0.0,
            "risk_level": "NONE",
            "note": "Database unavailable",
        }

    try:
        # Count orders (non-cancelled, non-draft).
        order_count = 0
        try:
            order_count = db_conn.get_collection("orders").count_documents(
                {
                    "customer_id": customer_id,
                    "status": {"$nin": ["CANCELLED", "DRAFT"]},
                }
            )
        except Exception:
            pass

        # Count returns linked to this customer.
        return_count = 0
        total_returned_value = 0.0
        try:
            for rdoc in db_conn.get_collection("returns").find(
                {"customer_id": customer_id},
                {"_id": 0, "returned_value": 1, "return_type": 1},
            ):
                return_count += 1
                total_returned_value += float(rdoc.get("returned_value") or 0)
        except Exception:
            pass

        return_rate_pct = (
            round(return_count / order_count * 100, 1) if order_count > 0 else 0.0
        )

        # Risk band: advisory only — never blocks a transaction.
        if return_count >= 3 or return_rate_pct >= 30.0:
            risk_level = "HIGH"
        elif return_count >= 1 or return_rate_pct >= 15.0:
            risk_level = "MEDIUM"
        elif return_count > 0:
            risk_level = "LOW"
        else:
            risk_level = "NONE"

        return {
            "customer_id": customer_id,
            "return_count": return_count,
            "order_count": order_count,
            "return_rate_pct": return_rate_pct,
            "total_returned_value": round(total_returned_value, 2),
            "risk_level": risk_level,
            "note": (
                "Advisory only — does not block transactions. "
                "Review before issuing refund."
                if risk_level == "HIGH"
                else None
            ),
        }
    except Exception as exc:
        logger.warning("[CRM] return-risk failed for %s: %s", customer_id, exc)
        return {
            "customer_id": customer_id,
            "return_count": 0,
            "order_count": 0,
            "return_rate_pct": 0.0,
            "risk_level": "NONE",
            "note": "Error computing return risk",
        }


@router.post(
    "/customers/{customer_id}/loyalty-points", response_model=LoyaltyTierResponse
)
async def add_loyalty_points(
    customer_id: str = Path(..., description="Customer ID"),
    request: AddLoyaltyPointsRequest = Body(...),
    current_user: dict = Depends(get_current_user),
):
    """Add loyalty points to customer account"""
    try:
        repo = get_customer_repository()
        if not repo:
            raise HTTPException(status_code=500, detail="Database connection failed")

        if not repo.find_by_id(customer_id):
            raise HTTPException(status_code=404, detail="Customer not found")

        updated_customer = repo.increment_loyalty_points(customer_id, request.points)
        if updated_customer is None:
            raise HTTPException(
                status_code=500, detail="Failed to update loyalty points"
            )

        return _calculate_loyalty_tier(
            updated_customer.get("loyalty_points", 0),
            updated_customer.get("created_at", ""),
            customer_id=customer_id,
            customer_doc=updated_customer,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("CRM operation failed: %s", e)
        raise HTTPException(
            status_code=500, detail="An internal error occurred. Please try again."
        )


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================


def _calculate_customer_stats(customer: dict, orders: list) -> dict:
    """Calculate customer engagement and value statistics"""
    total_lifetime_value = sum(order.get("total_amount", 0) for order in orders)
    total_orders = len(orders)
    avg_order_value = total_lifetime_value / total_orders if total_orders > 0 else 0

    last_order = orders[0] if orders else None
    customer_since = datetime.fromisoformat(
        customer["created_at"].replace("Z", "+00:00")
    )
    months_as_customer = (
        datetime.now(customer_since.tzinfo) - customer_since
    ).days / 30
    visit_frequency = total_orders / max(1, months_as_customer)

    return {
        "total_lifetime_value": round(total_lifetime_value, 2),
        "total_orders": total_orders,
        "last_order_date": last_order.get("order_date") if last_order else None,
        "last_order_amount": (last_order.get("total_amount") if last_order else None),
        "customer_since_date": customer["created_at"],
        "preferred_store": customer.get("store_id", "Main Store"),
        "average_order_value": round(avg_order_value, 2),
        "visit_frequency": round(visit_frequency, 1),
        "referral_count": 0,
        "active_loans": 0,
    }


def _calculate_loyalty_tier(lifetime_value: float, created_at: str,
                             customer_id: str = "", customer_doc: dict = None) -> dict:
    """Calculate loyalty tier based on lifetime value.

    Reads the real points balance from the loyalty_accounts ledger when
    customer_id is supplied (fail-soft to 0 if the account does not exist or the
    DB is unavailable). `points` / `total_points_earned` / `redeemed_points` were
    previously fabricated as rupee amounts cast to int -- that is wrong; loyalty
    points are earned at the configured rate, not 1pt-per-rupee.
    Also populates birthday_month from the customer DOB if available.
    """
    if lifetime_value >= 100000:
        tier = "Diamond"
    elif lifetime_value >= 50000:
        tier = "Platinum"
    elif lifetime_value >= 25000:
        tier = "Gold"
    elif lifetime_value >= 10000:
        tier = "Silver"
    else:
        tier = "Bronze"

    thresholds = {
        "Bronze": 10000,
        "Silver": 25000,
        "Gold": 50000,
        "Platinum": 100000,
        "Diamond": 100000,
    }
    next_threshold = thresholds[tier]
    points_to_next = max(0, next_threshold - lifetime_value)

    # Read real points balance from the loyalty_accounts ledger.
    balance_points = 0
    lifetime_earned = 0
    lifetime_redeemed = 0
    if customer_id:
        try:
            acct_repo = get_loyalty_account_repository()
            if acct_repo is not None:
                acct = acct_repo.find_or_create(customer_id)
                if acct:
                    balance_points = int(acct.get("balance_points") or 0)
                    lifetime_earned = int(acct.get("lifetime_earned") or 0)
                    lifetime_redeemed = int(acct.get("lifetime_redeemed") or 0)
        except Exception:
            pass  # fail-soft: leave zeros rather than 500 the caller

    # Birthday month from customer DOB field (multiple possible keys).
    birthday_month = None
    if customer_doc:
        dob = (
            customer_doc.get("dob")
            or customer_doc.get("date_of_birth")
            or customer_doc.get("birthday")
        )
        if dob:
            try:
                if isinstance(dob, str):
                    dob = dob[:10]  # "YYYY-MM-DD" prefix
                    birthday_month = int(dob.split("-")[1])
                elif hasattr(dob, "month"):
                    birthday_month = dob.month
            except Exception:
                birthday_month = None

    return {
        "tier": tier,
        "points": balance_points,
        "points_to_next_tier": int(points_to_next),
        "redeemed_points": lifetime_redeemed,
        "total_points_earned": lifetime_earned,
        "member_since": created_at,
        "birthday_month": birthday_month,
    }


def _add_prescription_status(prescription: dict) -> dict:
    """Add renewal status to prescription data"""
    issue_date = datetime.fromisoformat(
        prescription.get("issue_date", "").replace("Z", "+00:00")
    )
    expiry_date_str = prescription.get("expiry_date")
    expiry_date = None
    if expiry_date_str:
        expiry_date = datetime.fromisoformat(expiry_date_str.replace("Z", "+00:00"))

    today = datetime.now(issue_date.tzinfo)
    renewal_status = "current"
    days_until_renewal = None

    if expiry_date:
        days_until = (expiry_date - today).days
        days_until_renewal = max(0, days_until)

        if days_until < 0:
            renewal_status = "expired"
        elif days_until <= 30:
            renewal_status = "upcoming"

    return {
        **prescription,
        "renewal_status": renewal_status,
        "days_until_renewal": days_until_renewal,
    }


def _determine_lifecycle_phase(customer: dict, orders: list) -> dict:
    """Determine customer lifecycle phase"""
    if not orders:
        return {
            "phase": "prospect",
            "reason": "No purchase history",
            "recommended_action": "Send welcome offer and introduction email",
        }

    customer_since = datetime.fromisoformat(
        customer["created_at"].replace("Z", "+00:00")
    )
    days_since_signup = (datetime.now(customer_since.tzinfo) - customer_since).days

    if days_since_signup <= 90:
        return {
            "phase": "new",
            "reason": "First purchase within 90 days",
            "recommended_action": "Send product recommendations and loyalty program details",
        }

    last_order_date = datetime.fromisoformat(
        orders[0]["order_date"].replace("Z", "+00:00")
    )
    days_since_purchase = (datetime.now(last_order_date.tzinfo) - last_order_date).days
    total_lifetime_value = sum(order.get("total_amount", 0) for order in orders)

    if total_lifetime_value >= 100000 or len(orders) >= 20:
        return {
            "phase": "vip",
            "reason": "High lifetime value or frequent buyer",
            "recommended_action": "Exclusive offers, priority support, VIP events",
        }

    if days_since_purchase > 365:
        return {
            "phase": "inactive",
            "reason": f"No purchases in {days_since_purchase} days",
            "recommended_action": "Win-back campaign with special discounts",
        }

    if days_since_purchase > 180:
        return {
            "phase": "at_risk",
            "reason": f"No purchases in {days_since_purchase} days",
            "recommended_action": "Re-engagement email with personalized offers",
        }

    return {
        "phase": "active",
        "reason": f"Regular purchases, last order {days_since_purchase} days ago",
        "recommended_action": "Continue regular engagement and loyalty rewards",
    }


_SEGMENT_DEFS = [
    (
        "champions",
        "Champions",
        "Recent, frequent, high-value purchases. VIP tier customers.",
    ),
    ("loyal", "Loyal Customers", "Consistent, regular purchases. Repeat buyers."),
    ("big_spenders", "Big Spenders", "High lifetime value regardless of recency."),
    ("at_risk", "At Risk", "Were regular, now declining engagement."),
    ("lost", "Lost Customers", "No activity in 12+ months."),
]

# Order statuses that count as a real sale (both cases — TechCherry uses
# uppercase "DELIVERED"). Mirrors inventory._SOLD_STATUSES.
_SOLD_STATUSES = [
    "DELIVERED",
    "delivered",
    "Delivered",
    "COMPLETED",
    "completed",
    "Completed",
    "PAID",
    "paid",
    "Paid",
    "FULFILLED",
    "fulfilled",
    "Fulfilled",
]


def _crm_get_db():
    try:
        from ..dependencies import get_db

        conn = get_db()
        if conn is not None and conn.is_connected:
            return conn.db
    except Exception:
        pass
    return None


def _norm_phone(v) -> str:
    if not v:
        return ""
    digits = re.sub(r"\D", "", str(v))
    return digits[-10:] if len(digits) >= 10 else digits


def _empty_segments() -> list:
    return [
        {
            "segment_id": k,
            "segment_name": n,
            "customer_count": 0,
            "avg_lifetime_value": 0,
            "description": d,
        }
        for k, n, d in _SEGMENT_DEFS
    ]


def _perform_rfm_segmentation(customers: list) -> list:
    """Real RFM segmentation computed from the orders collection.

    Each customer is matched to their orders by customer_id (native) or by
    normalised phone (TechCherry orders carry customer_phone, not id). We
    derive Recency (days since last order), Frequency (order count) and
    Monetary (total spend) and bucket purchasers into the five segments.
    Customers with no matched orders are prospects, not an RFM segment, so
    they are excluded rather than padding "Lost". Returns honest zero counts
    when the DB is unavailable. Previously this returned all-zeros (a stub).
    """
    db_conn = _crm_get_db()
    if db_conn is None or not customers:
        return _empty_segments()

    by_cid: dict = {}
    by_phone: dict = {}
    try:
        orders_coll = db_conn.get_collection("orders")
        cursor = orders_coll.find(
            {"status": {"$in": _SOLD_STATUSES}},
            {
                "_id": 0,
                "customer_id": 1,
                "customer_phone": 1,
                "grand_total": 1,
                "total_amount": 1,
                "created_at": 1,
            },
        ).limit(50000)
        for o in cursor:
            amt = float(o.get("grand_total") or o.get("total_amount") or 0)
            dt = o.get("created_at")
            for key_map, key in (
                (by_cid, o.get("customer_id")),
                (by_phone, _norm_phone(o.get("customer_phone"))),
            ):
                if not key:
                    continue
                rec = key_map.setdefault(
                    key, {"count": 0, "monetary": 0.0, "last": None}
                )
                rec["count"] += 1
                rec["monetary"] += amt
                if dt and (rec["last"] is None or dt > rec["last"]):
                    rec["last"] = dt
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning("RFM order aggregation failed: %s", exc)
        return _empty_segments()

    now = datetime.utcnow()

    def _recency_days(last):
        if not last:
            return None
        try:
            if isinstance(last, str):
                last = datetime.fromisoformat(last.replace("Z", "+00:00"))
            if getattr(last, "tzinfo", None) is not None:
                last = last.replace(tzinfo=None)
            return (now - last).days
        except Exception:
            return None

    buckets = {k: {"count": 0, "ltv_sum": 0.0} for k, _, _ in _SEGMENT_DEFS}

    for c in customers:
        stats = by_cid.get(c.get("customer_id"))
        if not stats:
            ph = _norm_phone(c.get("mobile") or c.get("phone"))
            stats = by_phone.get(ph) if ph else None
        if not stats or stats["count"] == 0:
            continue  # no purchase history → prospect, not an RFM segment

        freq = stats["count"]
        monetary = stats["monetary"]
        rdays = _recency_days(stats["last"])
        rdays = rdays if rdays is not None else 99999

        if rdays <= 90 and freq >= 3:
            seg = "champions"
        elif freq >= 3:
            seg = "loyal"
        elif monetary >= 25000:
            seg = "big_spenders"
        elif rdays <= 365:
            seg = "at_risk"
        else:
            seg = "lost"
        buckets[seg]["count"] += 1
        buckets[seg]["ltv_sum"] += monetary

    return [
        {
            "segment_id": k,
            "segment_name": n,
            "customer_count": buckets[k]["count"],
            "avg_lifetime_value": (
                round(buckets[k]["ltv_sum"] / buckets[k]["count"], 2)
                if buckets[k]["count"]
                else 0
            ),
            "description": d,
        }
        for k, n, d in _SEGMENT_DEFS
    ]


def _identify_churn_risk_customers(customers: list, risk_level: str) -> list:
    """Identify customers at risk of churning using real recency-based signals.

    High:   no purchases in 180+ days (was previously active with >=1 order).
    Medium: 91-179 days since last purchase.
    Low:    31-90 days since last purchase with a declining order trend.

    Previously this was a stub that only handled 'high' via a phantom
    loyalty_points field on the customer doc (CRM-3 fix). Now we look up
    actual orders from the orders collection.
    """
    # Recency thresholds (days since last purchase).
    THRESHOLDS = {
        "high": (180, None),  # >= 180 days
        "medium": (91, 179),  # 91-179 days
        "low": (31, 90),  # 31-90 days
    }
    bounds = THRESHOLDS.get(risk_level)
    if bounds is None:
        return []
    lo, hi = bounds

    db_conn = _crm_get_db()
    if db_conn is None or not customers:
        return []

    # Build a recency map: customer_id -> days since last purchase.
    cid_set = {c.get("customer_id") for c in customers if c.get("customer_id")}
    recency_map: dict = {}
    if cid_set:
        try:
            pipeline = [
                {"$match": {"customer_id": {"$in": list(cid_set)}}},
                {
                    "$group": {
                        "_id": "$customer_id",
                        "last": {"$max": "$created_at"},
                        "count": {"$sum": 1},
                    }
                },
            ]
            now = datetime.utcnow()
            for row in db_conn.get_collection("orders").aggregate(pipeline):
                cid = row["_id"]
                last_raw = row.get("last")
                count = row.get("count", 0)
                if not last_raw or count == 0:
                    continue
                try:
                    if isinstance(last_raw, str):
                        last_raw = datetime.fromisoformat(
                            last_raw.replace("Z", "+00:00")
                        )
                    if getattr(last_raw, "tzinfo", None) is not None:
                        last_raw = last_raw.replace(tzinfo=None)
                    days = (now - last_raw).days
                    recency_map[cid] = {"days": days, "count": count}
                except Exception:
                    pass
        except Exception as exc:
            logger.warning("[CHURN] recency aggregation failed: %s", exc)
            return []

    at_risk = []
    for customer in customers:
        cid = customer.get("customer_id")
        rec = recency_map.get(cid)
        if rec is None:
            # No orders at all — not a "churned" customer, so not in any band.
            continue
        days = rec["days"]
        count = rec["count"]
        # Only flag previously active customers (at least one order).
        if count == 0:
            continue
        in_band = days >= lo and (hi is None or days <= hi)
        if in_band:
            at_risk.append(
                {
                    **customer,
                    "churn_risk_level": risk_level,
                    "days_since_last_purchase": days,
                    "total_orders": count,
                }
            )
    return at_risk


# ============================================================================
# F43 - Centralized VIP personal-triggers engine (STAFF_ALERT slice, DARK)
# ============================================================================
# A VIP customer's personal events (wedding anniversary, birthday + N days, a
# recurring cadence, a one-shot custom date) drive an IN-APP STAFF ALERT a few
# days BEFORE the event so the store can reach out personally. This is the
# STAFF_ALERT slice: it creates a follow_up work-list row + an in-app
# notification (mirroring the VIP-churn intervene + MEGAPHONE _scan_rx_expiring
# patterns). The customer-MESSAGE channel for #43 is DEFERRED under the
# WhatsApp ban -- nothing is sent here; any future customer send would ride
# notification_service as a PENDING row gated by DISPATCH_MODE.
#
# Pure date math + persistence live in api/services/vip_triggers.py.
# ============================================================================

from ..services import vip_triggers as _vtr  # noqa: E402

# Write/manage: CRM management roles. Read also adds CATALOG_MANAGER/OPTOMETRIST
# per the crm.py read-norms (they see the 360 view).
_VIP_WRITE_ROLES = ("STORE_MANAGER", "AREA_MANAGER", "ADMIN", "SUPERADMIN")
_VIP_READ_ROLES = (
    "STORE_MANAGER",
    "AREA_MANAGER",
    "ADMIN",
    "SUPERADMIN",
    "CATALOG_MANAGER",
    "OPTOMETRIST",
)


class VipProfileBody(BaseModel):
    vip_tags: Optional[List[str]] = Field(default=None, max_length=20)
    vip_override: Optional[bool] = None
    note: Optional[str] = Field(default=None, max_length=1000)


class PersonalTriggerCreate(BaseModel):
    customer_id: str = Field(..., min_length=1)
    type: Literal["ANNIVERSARY", "BIRTHDAY_PLUS_N", "RECURRING", "CUSTOM_DATE"]
    base_date: str = Field(..., description="YYYY-MM-DD anchor event date")
    label: str = Field(default="", max_length=120)
    lead_time_days: int = Field(default=7, ge=0, le=365)
    recur_every_days: Optional[int] = Field(default=None, ge=1)
    plus_n_days: Optional[int] = Field(default=None, ge=0)
    store_id: Optional[str] = None


class PersonalTriggerUpdate(BaseModel):
    type: Optional[
        Literal["ANNIVERSARY", "BIRTHDAY_PLUS_N", "RECURRING", "CUSTOM_DATE"]
    ] = None
    base_date: Optional[str] = None
    label: Optional[str] = Field(default=None, max_length=120)
    lead_time_days: Optional[int] = Field(default=None, ge=0, le=365)
    recur_every_days: Optional[int] = Field(default=None, ge=1)
    plus_n_days: Optional[int] = Field(default=None, ge=0)
    active: Optional[bool] = None


def _vip_audit(action: str, customer_id: str, current_user: dict, detail: dict) -> None:
    """Best-effort audit row. Fail-soft -- never undoes the VIP write."""
    try:
        arepo = get_audit_repository()
        if arepo is None:
            return
        arepo.create(
            {
                "action": action,
                "entity_type": "customer",
                "entity_id": customer_id,
                "user_id": current_user.get("user_id"),
                "user_name": current_user.get("full_name")
                or current_user.get("username"),
                "severity": "INFO",
                "source": "crm",
                "before_state": {},
                "after_state": detail,
            }
        )
    except Exception:  # noqa: BLE001
        pass


@router.post("/customers/{customer_id}/vip")
async def set_vip_profile(
    customer_id: str,
    body: VipProfileBody,
    current_user: dict = Depends(require_roles(*_VIP_WRITE_ROLES)),
):
    """Set a customer's VIP profile (vip_tags / vip_override) and/or append a
    personal note. Tags are de-duped + capped; the note is appended, never
    replacing prior notes. Single-doc update + audit. 404 if no such customer."""
    repo = get_customer_repository()
    if repo is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    customer = repo.find_by_id(customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    updates = _vtr.build_vip_update(
        customer,
        vip_tags=body.vip_tags,
        vip_override=body.vip_override,
        note_text=body.note,
        note_by=current_user.get("user_id") or current_user.get("username"),
    )
    if not updates:
        return _vtr.read_vip_profile(customer)
    try:
        repo.update(customer_id, updates)
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=503, detail="Failed to persist VIP profile")

    refreshed = repo.find_by_id(customer_id) or {**customer, **updates}
    _vip_audit(
        "VIP_PROFILE_SET",
        customer_id,
        current_user,
        {k: updates[k] for k in updates if k != "personal_notes"},
    )
    return _vtr.read_vip_profile(refreshed)


@router.get("/customers/{customer_id}/vip")
async def get_vip_profile(
    customer_id: str,
    current_user: dict = Depends(require_roles(*_VIP_READ_ROLES)),
):
    """Read a customer's VIP profile (tags / override / notes). 404 if absent."""
    repo = get_customer_repository()
    if repo is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    customer = repo.find_by_id(customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    return _vtr.read_vip_profile(customer)


@router.post("/personal-triggers")
async def create_personal_trigger(
    body: PersonalTriggerCreate,
    current_user: dict = Depends(require_roles(*_VIP_WRITE_ROLES)),
):
    """Create a personal trigger for a customer. The store is guarded: a
    non-SUPERADMIN must own the store the trigger is scoped to. 422 on a bad
    type/date combination (validation lives in the pure service)."""
    db_conn = _crm_get_db()
    if db_conn is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    repo = get_customer_repository()
    if repo is not None and not repo.find_by_id(body.customer_id):
        raise HTTPException(status_code=404, detail="Customer not found")

    store_id = _vip_store_guard(current_user, body.store_id)
    payload = body.model_dump()
    try:
        doc = _vtr.create_trigger(
            db_conn,
            customer_id=body.customer_id,
            payload=payload,
            created_by=current_user.get("user_id") or current_user.get("username"),
            store_id=store_id,
        )
    except ValueError as ve:
        raise HTTPException(status_code=422, detail=str(ve))
    except RuntimeError:
        raise HTTPException(status_code=503, detail="Database unavailable")
    _vip_audit(
        "VIP_TRIGGER_CREATE",
        body.customer_id,
        current_user,
        {"trigger_id": doc.get("trigger_id"), "type": doc.get("type")},
    )
    return doc


@router.get("/personal-triggers")
async def list_personal_triggers(
    customer_id: Optional[str] = Query(None),
    store_id: Optional[str] = Query(None),
    active_only: bool = Query(False),
    current_user: dict = Depends(require_roles(*_VIP_READ_ROLES)),
):
    """List personal triggers (optionally filtered by customer / store).
    Store-scoped for non-SUPERADMIN: an explicit store_id is guarded; with no
    store_id a non-SUPERADMIN is scoped to their own store. Fail-soft -> []."""
    db_conn = _crm_get_db()
    if db_conn is None:
        return {"triggers": [], "total": 0}
    roles = current_user.get("roles", []) or []
    if "SUPERADMIN" not in roles:
        # Non-SUPERADMIN is always store-scoped (guard raises 403 on a foreign store).
        store_id = _vip_store_guard(current_user, store_id)
    rows = _vtr.list_triggers(
        db_conn, customer_id=customer_id, store_id=store_id, active_only=active_only
    )
    return {"triggers": rows, "total": len(rows)}


@router.put("/personal-triggers/{trigger_id}")
async def update_personal_trigger(
    trigger_id: str,
    body: PersonalTriggerUpdate,
    current_user: dict = Depends(require_roles(*_VIP_WRITE_ROLES)),
):
    """Edit or deactivate a personal trigger. A non-SUPERADMIN may only touch a
    trigger scoped to a store they own. 404 if not found, 422 on bad shape."""
    db_conn = _crm_get_db()
    if db_conn is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    existing = _vtr.get_trigger(db_conn, trigger_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Trigger not found")
    roles = current_user.get("roles", []) or []
    if "SUPERADMIN" not in roles:
        # Owning-store guard: raises 403 if the caller does not own its store.
        _vip_store_guard(current_user, existing.get("store_id"))

    payload = {k: v for k, v in body.model_dump().items() if v is not None}
    try:
        doc = _vtr.update_trigger(db_conn, trigger_id, payload)
    except ValueError as ve:
        raise HTTPException(status_code=422, detail=str(ve))
    except RuntimeError:
        raise HTTPException(status_code=503, detail="Database unavailable")
    if doc is None:
        raise HTTPException(status_code=404, detail="Trigger not found")
    _vip_audit(
        "VIP_TRIGGER_UPDATE",
        existing.get("customer_id"),
        current_user,
        {"trigger_id": trigger_id, "changes": list(payload.keys())},
    )
    return doc


@router.delete("/personal-triggers/{trigger_id}")
async def delete_personal_trigger(
    trigger_id: str,
    current_user: dict = Depends(require_roles(*_VIP_WRITE_ROLES)),
):
    """Delete a personal trigger. Store-guarded for non-SUPERADMIN. 404 if absent."""
    db_conn = _crm_get_db()
    if db_conn is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    existing = _vtr.get_trigger(db_conn, trigger_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Trigger not found")
    roles = current_user.get("roles", []) or []
    if "SUPERADMIN" not in roles:
        _vip_store_guard(current_user, existing.get("store_id"))
    ok = _vtr.delete_trigger(db_conn, trigger_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Trigger not found")
    _vip_audit(
        "VIP_TRIGGER_DELETE",
        existing.get("customer_id"),
        current_user,
        {"trigger_id": trigger_id},
    )
    return {"ok": True, "trigger_id": trigger_id}
