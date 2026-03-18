"""
IMS 2.0 - Incentives Router
=============================
Staff incentive tracking and leaderboard management
Handles sales targets, kicker programs, and incentive calculations
"""

from fastapi import APIRouter, Depends, Query, HTTPException, Body
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime, date
from calendar import monthrange
import uuid
from .auth import get_current_user
from ..dependencies import (
    get_user_repository,
    get_store_repository,
)

# Import database connection
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

try:
    from database.connection import get_db
    DATABASE_AVAILABLE = True
except ImportError:
    DATABASE_AVAILABLE = False

router = APIRouter()


# ============================================================================
# SCHEMAS
# ============================================================================


class IncentiveTarget(BaseModel):
    """Monthly sales target for a staff member"""
    staff_id: str
    target_amount: float = Field(..., gt=0)
    month: int = Field(..., ge=1, le=12)
    year: int
    description: Optional[str] = None


class KickerSale(BaseModel):
    """Record a kicker sale (Zeiss, Safilo brands)"""
    brand: str  # e.g., "Zeiss SmartLife", "Zeiss Progressive", "Safilo"
    product_name: Optional[str] = None
    sale_amount: float = Field(..., gt=0)
    sale_date: Optional[date] = None


class IncentiveResponse(BaseModel):
    """Incentive calculation response"""
    staff_id: str
    staff_name: str
    month: int
    year: int
    target_amount: float
    actual_sales: float
    achievement_percentage: float
    base_incentive: float
    kicker_count: int
    kicker_bonus: float
    google_reviews: int
    google_review_bonus: float
    total_incentive: float
    status: str  # "Below Target", "Qualified", "Exceeded"


class LeaderboardEntry(BaseModel):
    """Staff member leaderboard entry"""
    rank: int
    staff_id: str
    staff_name: str
    achievement_percentage: float
    actual_sales: float
    target_amount: float
    total_incentive: float


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================


def _get_db():
    """Get database connection"""
    if not DATABASE_AVAILABLE:
        return None
    return get_db()


def _get_current_month_year():
    """Get current month and year"""
    today = datetime.now()
    return today.month, today.year


def _calculate_incentive(actual_sales: float, target: float, kicker_count: int) -> tuple:
    """
    Calculate incentive based on sales and kicker count.
    Returns: (base_incentive, status, achievement_percentage)
    
    Incentive slabs:
    - Below 80%: No incentive (status: "Below Target")
    - 80-99%: 0.8% of sales (status: "Qualified")
    - 100-119%: 1% of sales (status: "Exceeded")
    - 120%+: 1.5% of sales (status: "Exceeded")
    
    Kicker bonus: ₹200 per kicker, minimum 3 kickers to qualify
    """
    achievement_pct = (actual_sales / target) * 100 if target > 0 else 0
    
    if achievement_pct < 80:
        return 0, "Below Target", achievement_pct
    
    # Determine incentive rate
    if achievement_pct >= 120:
        rate = 0.015  # 1.5%
    elif achievement_pct >= 100:
        rate = 0.01   # 1%
    else:
        rate = 0.008  # 0.8%
    
    base_incentive = actual_sales * rate
    
    # Kicker bonus: ₹200 per kicker, minimum 3 kickers
    kicker_bonus = kicker_count * 200 if kicker_count >= 3 else 0
    
    status = "Exceeded" if achievement_pct >= 100 else "Qualified"
    
    return base_incentive, status, achievement_pct


def _get_staff_sales(staff_id: str, month: int, year: int) -> float:
    """
    Get total sales for a staff member in a given month.
    This would typically query the orders collection.
    For now, returning mock data - implement actual query based on your orders schema.
    """
    db = _get_db()
    if not db:
        return 0
    
    try:
        # Assuming orders collection has staff_id, created_at, and total fields
        orders_coll = db.get_collection("orders")
        
        # Calculate date range for the month
        start_date = datetime(year, month, 1)
        if month == 12:
            end_date = datetime(year + 1, 1, 1)
        else:
            end_date = datetime(year, month + 1, 1)
        
        # Query orders for this staff member in the month
        pipeline = [
            {
                "$match": {
                    "staff_id": staff_id,
                    "created_at": {
                        "$gte": start_date.isoformat(),
                        "$lt": end_date.isoformat()
                    },
                    "status": {"$in": ["COMPLETED", "PAID"]}  # Only count completed orders
                }
            },
            {
                "$group": {
                    "_id": None,
                    "total_sales": {"$sum": "$total"}
                }
            }
        ]
        
        result = list(orders_coll.aggregate(pipeline))
        return result[0]["total_sales"] if result else 0
    except Exception as e:
        print(f"Error calculating staff sales: {e}")
        return 0


def _get_staff_kickers(staff_id: str, month: int, year: int) -> int:
    """Get count of kicker sales for a staff member in a month"""
    db = _get_db()
    if not db:
        return 0
    
    try:
        kickers_coll = db.get_collection("kicker_sales")
        
        # Calculate date range
        start_date = datetime(year, month, 1)
        if month == 12:
            end_date = datetime(year + 1, 1, 1)
        else:
            end_date = datetime(year, month + 1, 1)
        
        count = kickers_coll.count_documents({
            "staff_id": staff_id,
            "created_at": {
                "$gte": start_date.isoformat(),
                "$lt": end_date.isoformat()
            }
        })
        
        return count
    except Exception:
        return 0


# ============================================================================
# INCENTIVE ENDPOINTS
# ============================================================================


@router.get("/dashboard")
async def get_incentive_dashboard(
    month: Optional[int] = Query(None),
    year: Optional[int] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """
    Get incentive summary for current staff member.
    Returns: current month achievement, kicker status, total earned, next slab info
    """
    db = _get_db()
    
    # Use current month/year if not specified
    if not month or not year:
        month, year = _get_current_month_year()
    
    staff_id = current_user.get("user_id")
    staff_name = current_user.get("full_name", "")
    
    # Get target for this month
    targets_coll = db.get_collection("incentive_targets") if db else None
    target_doc = None
    
    if targets_coll:
        target_doc = targets_coll.find_one({
            "staff_id": staff_id,
            "month": month,
            "year": year
        })
    
    target_amount = target_doc["target_amount"] if target_doc else 0
    
    # Get actual sales
    actual_sales = _get_staff_sales(staff_id, month, year)
    
    # Get kicker count
    kicker_count = _get_staff_kickers(staff_id, month, year)
    
    # Calculate incentive
    base_incentive, status, achievement_pct = _calculate_incentive(
        actual_sales, target_amount, kicker_count
    )
    
    # Kicker bonus
    kicker_bonus = kicker_count * 200 if kicker_count >= 3 else 0
    
    # Google review bonus (mock - ₹25/₹50 per review)
    # In production, query from reviews collection
    google_reviews = 0
    google_review_bonus = 0
    
    # Total incentive
    total_incentive = base_incentive + kicker_bonus + google_review_bonus
    
    return {
        "staff_id": staff_id,
        "staff_name": staff_name,
        "month": month,
        "year": year,
        "target_amount": target_amount,
        "actual_sales": actual_sales,
        "achievement_percentage": round(achievement_pct, 2),
        "base_incentive": round(base_incentive, 2),
        "kicker_count": kicker_count,
        "kicker_bonus": round(kicker_bonus, 2),
        "google_reviews": google_reviews,
        "google_review_bonus": round(google_review_bonus, 2),
        "total_incentive": round(total_incentive, 2),
        "status": status,
        "next_slab": {
            "current_slab": f"{int(achievement_pct)}% achieved",
            "next_milestone": "100% (1% rate)" if achievement_pct < 100 else "120% (1.5% rate)"
        }
    }


@router.get("/targets/{staff_id}")
async def get_staff_targets(
    staff_id: str,
    month: Optional[int] = Query(None),
    year: Optional[int] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Get monthly targets for a staff member"""
    db = _get_db()
    if not db:
        raise HTTPException(status_code=503, detail="Database not available")
    
    # Use current month/year if not specified
    if not month or not year:
        month, year = _get_current_month_year()
    
    targets_coll = db.get_collection("incentive_targets")
    
    target_doc = targets_coll.find_one({
        "staff_id": staff_id,
        "month": month,
        "year": year
    })
    
    if not target_doc:
        raise HTTPException(status_code=404, detail="No target set for this period")
    
    return {
        "target_id": target_doc.get("target_id", ""),
        "staff_id": target_doc.get("staff_id", ""),
        "target_amount": target_doc.get("target_amount", 0),
        "month": target_doc.get("month", 0),
        "year": target_doc.get("year", 0),
        "description": target_doc.get("description", ""),
        "created_at": target_doc.get("created_at", ""),
        "created_by": target_doc.get("created_by", ""),
    }


@router.post("/targets", status_code=201)
async def set_staff_target(
    target: IncentiveTarget,
    current_user: dict = Depends(get_current_user),
):
    """
    Set monthly sales target for a staff member.
    Admin only endpoint.
    """
    db = _get_db()
    if not db:
        raise HTTPException(status_code=503, detail="Database not available")
    
    # Check if user is admin
    if not any(r in ["ADMIN", "SUPERADMIN", "STORE_MANAGER"] for r in current_user.get("roles", [])):
        raise HTTPException(status_code=403, detail="Admin access required")
    
    targets_coll = db.get_collection("incentive_targets")
    
    # Check if target already exists
    existing = targets_coll.find_one({
        "staff_id": target.staff_id,
        "month": target.month,
        "year": target.year
    })
    
    target_id = existing.get("target_id") if existing else str(uuid.uuid4())
    
    target_data = {
        "target_id": target_id,
        "staff_id": target.staff_id,
        "target_amount": target.target_amount,
        "month": target.month,
        "year": target.year,
        "description": target.description or "",
        "created_by": current_user.get("user_id"),
        "created_at": datetime.now().isoformat(),
        "store_id": current_user.get("active_store_id"),
    }
    
    if existing:
        targets_coll.update_one(
            {"target_id": target_id},
            {"$set": target_data}
        )
        message = "Target updated"
    else:
        targets_coll.insert_one(target_data)
        message = "Target created"
    
    return {
        "target_id": target_id,
        "message": message,
        "target_amount": target.target_amount,
    }


@router.get("/leaderboard")
async def get_incentive_leaderboard(
    month: Optional[int] = Query(None),
    year: Optional[int] = Query(None),
    limit: int = Query(50, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
):
    """
    Get staff ranking by achievement percentage for current store/month.
    Staff earn incentives based on sales targets and kicker programs.
    """
    db = _get_db()
    if not db:
        raise HTTPException(status_code=503, detail="Database not available")
    
    # Use current month/year if not specified
    if not month or not year:
        month, year = _get_current_month_year()
    
    store_id = current_user.get("active_store_id")
    
    # Get all targets for this store and month
    targets_coll = db.get_collection("incentive_targets")
    targets = list(targets_coll.find({
        "store_id": store_id,
        "month": month,
        "year": year
    }))
    
    # Build leaderboard
    leaderboard = []
    
    for i, target_doc in enumerate(targets):
        staff_id = target_doc.get("staff_id")
        
        # Get actual sales
        actual_sales = _get_staff_sales(staff_id, month, year)
        
        # Get kicker count
        kicker_count = _get_staff_kickers(staff_id, month, year)
        
        # Calculate incentive
        target_amount = target_doc.get("target_amount", 0)
        base_incentive, status, achievement_pct = _calculate_incentive(
            actual_sales, target_amount, kicker_count
        )
        
        # Kicker bonus
        kicker_bonus = kicker_count * 200 if kicker_count >= 3 else 0
        
        # Total
        total_incentive = base_incentive + kicker_bonus
        
        # Get staff name from users collection
        users_repo = get_user_repository()
        staff_doc = users_repo.find_by_id(staff_id) if users_repo else None
        staff_name = staff_doc.get("full_name", "Unknown") if staff_doc else "Unknown"
        
        leaderboard.append({
            "rank": i + 1,
            "staff_id": staff_id,
            "staff_name": staff_name,
            "achievement_percentage": round(achievement_pct, 2),
            "actual_sales": round(actual_sales, 2),
            "target_amount": round(target_amount, 2),
            "total_incentive": round(total_incentive, 2),
            "status": status,
        })
    
    # Sort by achievement percentage descending
    leaderboard.sort(key=lambda x: x["achievement_percentage"], reverse=True)
    
    # Re-rank after sort
    for i, entry in enumerate(leaderboard):
        entry["rank"] = i + 1
    
    return {
        "period": f"{month}/{year}",
        "total_staff": len(leaderboard),
        "leaderboard": leaderboard[:limit],
    }


@router.post("/kickers", status_code=201)
async def record_kicker_sale(
    kicker: KickerSale,
    staff_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """
    Record a kicker sale (Zeiss SmartLife/Progressive/Photofusion, Safilo brands).
    Staff member can record their own sales, managers can record for others.
    """
    db = _get_db()
    if not db:
        raise HTTPException(status_code=503, detail="Database not available")
    
    # If staff_id not provided, use current user
    if not staff_id:
        staff_id = current_user.get("user_id")
    
    # Check permission: only admins can record for other staff
    if staff_id != current_user.get("user_id"):
        if not any(r in ["ADMIN", "SUPERADMIN", "STORE_MANAGER"] for r in current_user.get("roles", [])):
            raise HTTPException(status_code=403, detail="Cannot record kickers for other staff")
    
    kickers_coll = db.get_collection("kicker_sales")
    
    # Get staff name
    users_repo = get_user_repository()
    staff_doc = users_repo.find_by_id(staff_id) if users_repo else None
    staff_name = staff_doc.get("full_name", "") if staff_doc else ""
    
    kicker_id = str(uuid.uuid4())
    
    kicker_data = {
        "kicker_id": kicker_id,
        "staff_id": staff_id,
        "staff_name": staff_name,
        "brand": kicker.brand,
        "product_name": kicker.product_name or "",
        "sale_amount": kicker.sale_amount,
        "sale_date": (kicker.sale_date or date.today()).isoformat(),
        "created_by": current_user.get("user_id"),
        "created_at": datetime.now().isoformat(),
        "store_id": current_user.get("active_store_id"),
    }
    
    kickers_coll.insert_one(kicker_data)
    
    return {
        "kicker_id": kicker_id,
        "message": "Kicker sale recorded",
        "brand": kicker.brand,
        "sale_amount": kicker.sale_amount,
    }


@router.get("/kickers/{staff_id}")
async def get_staff_kickers(
    staff_id: str,
    month: Optional[int] = Query(None),
    year: Optional[int] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Get kicker summary for a staff member"""
    db = _get_db()
    if not db:
        raise HTTPException(status_code=503, detail="Database not available")
    
    # Use current month/year if not specified
    if not month or not year:
        month, year = _get_current_month_year()
    
    kickers_coll = db.get_collection("kicker_sales")
    
    # Calculate date range for the month
    start_date = datetime(year, month, 1)
    if month == 12:
        end_date = datetime(year + 1, 1, 1)
    else:
        end_date = datetime(year, month + 1, 1)
    
    # Get all kickers for this staff in the month
    kickers = list(kickers_coll.find({
        "staff_id": staff_id,
        "created_at": {
            "$gte": start_date.isoformat(),
            "$lt": end_date.isoformat()
        }
    }).sort("created_at", -1))
    
    # Count by brand
    brand_summary = {}
    total_kickers = 0
    total_sales = 0
    
    for kicker in kickers:
        brand = kicker.get("brand", "Unknown")
        brand_summary[brand] = brand_summary.get(brand, 0) + 1
        total_kickers += 1
        total_sales += kicker.get("sale_amount", 0)
    
    # Get staff name
    users_repo = get_user_repository()
    staff_doc = users_repo.find_by_id(staff_id) if users_repo else None
    staff_name = staff_doc.get("full_name", "") if staff_doc else ""
    
    return {
        "staff_id": staff_id,
        "staff_name": staff_name,
        "month": month,
        "year": year,
        "total_kickers": total_kickers,
        "total_sales": round(total_sales, 2),
        "kicker_bonus": total_kickers * 200 if total_kickers >= 3 else 0,
        "brand_summary": brand_summary,
        "kickers": [
            {
                "kicker_id": k.get("kicker_id", ""),
                "brand": k.get("brand", ""),
                "product_name": k.get("product_name", ""),
                "sale_amount": k.get("sale_amount", 0),
                "sale_date": k.get("sale_date", ""),
            }
            for k in kickers
        ],
    }
