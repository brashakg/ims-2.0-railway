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

from .auth import get_current_user
from ..dependencies import (
    get_customer_repository,
    get_order_repository,
    get_prescription_repository,
)

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
        return repo.find_many({}) if repo else []

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
            }
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

        # Calculate loyalty tier
        loyalty_data = _calculate_loyalty_tier(
            stats["total_lifetime_value"], customer["created_at"]
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
        prescriptions = db.query_customer_prescriptions(customer_id)
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

        # Determine pack info.
        pack_size = int(cl_line.get("pack_size") or cl_line.get("qty") or 0)
        modality = str(cl_line.get("modality") or cl_line.get("cl_modality") or "")
        sku = str(cl_line.get("sku") or cl_line.get("product_id") or "")
        order_qty = int(cl_line.get("quantity") or cl_line.get("return_qty") or 1)

        # Estimate supply in days.
        # Daily disposables: total_lenses / 2 eyes / 1 per day.
        # Monthly disposables: each pack = 30 days (one pair per box assumed).
        total_lenses = pack_size * order_qty
        if modality.upper() in ("DAILY", "DAILY DISPOSABLE", "1-DAY"):
            supply_days = total_lenses // 2 if total_lenses >= 2 else total_lenses
        elif modality.upper() in ("MONTHLY", "MONTHLY DISPOSABLE", "30-DAY"):
            supply_days = order_qty * 30
        elif modality.upper() in ("BIWEEKLY", "2-WEEK", "FORTNIGHTLY"):
            supply_days = order_qty * 14
        else:
            # Unknown modality: use pack_size / 2 per day as a conservative guess.
            supply_days = total_lenses // 2 if total_lenses >= 2 else 30

        # Anchor from the order date.
        order_date_raw = cl_order.get("created_at") or cl_order.get("order_date")
        try:
            if isinstance(order_date_raw, str):
                order_dt = datetime.fromisoformat(
                    order_date_raw.replace("Z", "+00:00")
                ).replace(tzinfo=None)
            elif isinstance(order_date_raw, datetime):
                order_dt = order_date_raw.replace(tzinfo=None)
            else:
                order_dt = datetime.utcnow()
        except Exception:
            order_dt = datetime.utcnow()

        refill_due = order_dt + timedelta(days=max(supply_days, 1))
        days_remaining = (refill_due - datetime.utcnow()).days

        return {
            "customer_id": customer_id,
            "has_cl_history": True,
            "refill_due_date": refill_due.date().isoformat(),
            "days_remaining": days_remaining,
            "last_cl_order_id": cl_order.get("order_id"),
            "last_cl_order_date": order_dt.date().isoformat(),
            "sku": sku,
            "modality": modality or None,
            "pack_size": pack_size or None,
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
            raise HTTPException(status_code=500, detail="Failed to update loyalty points")

        return _calculate_loyalty_tier(
            updated_customer.get("loyalty_points", 0),
            updated_customer.get("created_at", ""),
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


def _calculate_loyalty_tier(lifetime_value: float, created_at: str) -> dict:
    """Calculate loyalty tier based on lifetime value"""
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

    return {
        "tier": tier,
        "points": int(lifetime_value),
        "points_to_next_tier": int(points_to_next),
        "redeemed_points": 0,
        "total_points_earned": int(lifetime_value),
        "member_since": created_at,
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
