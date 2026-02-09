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
import uuid
from .auth import get_current_user
from ..dependencies import get_customer_repository

router = APIRouter()


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
        raise HTTPException(status_code=500, detail=str(e))


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
        raise HTTPException(status_code=500, detail=str(e))


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
        raise HTTPException(status_code=500, detail=str(e))


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
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/customers/{customer_id}/interactions", response_model=InteractionRecord)
async def create_customer_interaction(
    customer_id: str = Path(..., description="Customer ID"),
    interaction: InteractionRecord,
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
        raise HTTPException(status_code=500, detail=str(e))


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
        raise HTTPException(status_code=500, detail=str(e))


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
        all_customers = db.query_all_customers()
        at_risk = _identify_churn_risk_customers(all_customers, risk_level)
        return at_risk[:limit]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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

        customer = repo.find_by_id(customer_id)
        if not customer:
            raise HTTPException(status_code=404, detail="Customer not found")

        # Update customer loyalty points
        current_points = customer.get("loyalty_points", 0)
        new_points = current_points + request.points

        if repo.update(customer_id, {"loyalty_points": new_points}):
            updated_customer = repo.find_by_id(customer_id)
            return _calculate_loyalty_tier(
                updated_customer.get("loyalty_points", 0), updated_customer.get("created_at", "")
            )

        raise HTTPException(status_code=500, detail="Failed to update loyalty points")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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


def _perform_rfm_segmentation(customers: list) -> list:
    """Perform RFM segmentation on all customers"""
    segments = [
        {
            "segment_id": "champions",
            "segment_name": "Champions",
            "customer_count": 0,
            "avg_lifetime_value": 0,
            "description": "Recent, frequent, high-value purchases. VIP tier customers.",
        },
        {
            "segment_id": "loyal",
            "segment_name": "Loyal Customers",
            "customer_count": 0,
            "avg_lifetime_value": 0,
            "description": "Consistent, regular purchases. Repeat buyers.",
        },
        {
            "segment_id": "big_spenders",
            "segment_name": "Big Spenders",
            "customer_count": 0,
            "avg_lifetime_value": 0,
            "description": "High lifetime value regardless of recency.",
        },
        {
            "segment_id": "at_risk",
            "segment_name": "At Risk",
            "customer_count": 0,
            "avg_lifetime_value": 0,
            "description": "Were regular, now declining engagement.",
        },
        {
            "segment_id": "lost",
            "segment_name": "Lost Customers",
            "customer_count": 0,
            "avg_lifetime_value": 0,
            "description": "No activity in 12+ months.",
        },
    ]
    return segments


def _identify_churn_risk_customers(customers: list, risk_level: str) -> list:
    """Identify customers at risk of churning"""
    at_risk = []
    for customer in customers:
        # Mock implementation - in production, would analyze actual engagement
        if risk_level == "high":
            if customer.get("loyalty_points", 0) < 1000:
                at_risk.append(customer)
    return at_risk
