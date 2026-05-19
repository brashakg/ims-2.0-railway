"""
IMS 2.0 - Stores Router
========================
Store management endpoints
"""

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field
from typing import List, Optional
import uuid

from .auth import get_current_user
from ..dependencies import get_store_repository, get_user_repository, get_order_repository

router = APIRouter()


# ============================================================================
# SCHEMAS
# ============================================================================


class StoreCreate(BaseModel):
    store_code: str = Field(..., min_length=2, max_length=10)
    store_name: str
    brand: str  # BETTER_VISION, WIZOPT
    address: str
    city: str
    state: str
    pincode: str
    phone: str
    email: Optional[str] = None
    gstin: str
    enabled_categories: List[str] = []


class StoreUpdate(BaseModel):
    store_name: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    enabled_categories: Optional[List[str]] = None
    is_active: Optional[bool] = None


# ============================================================================
# ENDPOINTS
# ============================================================================


@router.get("/summary")
async def get_store_summary(current_user: dict = Depends(get_current_user)):
    """Get summary of all stores by brand"""
    repo = get_store_repository()

    if repo is not None:
        summary = repo.get_store_summary()
        return {"summary": summary}

    return {"summary": {}}


@router.get("")
async def list_stores(
    brand: Optional[str] = Query(None),
    city: Optional[str] = Query(None),
    active_only: bool = Query(True),
    current_user: dict = Depends(get_current_user),
):
    """List stores with optional filtering"""
    repo = get_store_repository()

    if repo is not None:
        filter_dict = {}
        if brand:
            filter_dict["brand"] = brand
        if city:
            filter_dict["city"] = city

        if active_only:
            stores = repo.find_active(filter_dict if filter_dict else None)
        else:
            stores = repo.find_many(filter_dict, sort=[("brand", 1), ("store_name", 1)])

        return {"stores": stores, "total": len(stores)}

    return {"stores": [], "total": 0}


@router.post("", status_code=201)
async def create_store(
    store: StoreCreate, current_user: dict = Depends(get_current_user)
):
    """Create a new store (SUPERADMIN/ADMIN only)"""
    # RBAC: Only admins can create stores
    if not any(role in current_user.get("roles", []) for role in ["SUPERADMIN", "ADMIN"]):
        raise HTTPException(status_code=403, detail="Only admins can create stores")

    repo = get_store_repository()

    if repo is not None:
        # Check if store code exists
        if repo.find_by_code(store.store_code):
            raise HTTPException(status_code=400, detail="Store code already exists")

        store_data = {
            "store_code": store.store_code,
            "store_name": store.store_name,
            "brand": store.brand,
            "address": store.address,
            "city": store.city,
            "state": store.state,
            "pincode": store.pincode,
            "phone": store.phone,
            "email": store.email,
            "gstin": store.gstin,
            "enabled_categories": store.enabled_categories,
            "is_active": True,
            "is_hq": False,
            "created_by": current_user.get("user_id"),
        }

        created = repo.create(store_data)
        if created:
            return {
                "store_id": created["store_id"],
                "store_code": created["store_code"],
                "message": "Store created",
            }

        raise HTTPException(status_code=500, detail="Failed to create store")

    return {"store_id": str(uuid.uuid4()), "message": "Store created"}


@router.get("/{store_id}")
async def get_store(store_id: str, current_user: dict = Depends(get_current_user)):
    """Get store by ID"""
    repo = get_store_repository()

    if repo is not None:
        store = repo.find_by_id(store_id)
        if store is not None:
            return store
        raise HTTPException(status_code=404, detail="Store not found")

    return {"store_id": store_id}


@router.get("/{store_id}/stats")
async def get_store_stats(store_id: str, current_user: dict = Depends(get_current_user)):
    """Quick store KPIs — order count + revenue + staff count. The
    frontend's storeApi.getStoreStats was 404'ing (no such route).
    Two-segment path, so it isn't shadowed by GET /{store_id}."""
    order_repo = get_order_repository()
    user_repo = get_user_repository()
    stats = {
        "store_id": store_id,
        "total_orders": 0,
        "total_revenue": 0.0,
        "staff_count": 0,
    }
    try:
        if order_repo is not None:
            orders = order_repo.find_many({"store_id": store_id}) or []
            stats["total_orders"] = len(orders)
            stats["total_revenue"] = round(
                sum(float(o.get("grand_total") or 0) for o in orders), 2
            )
    except Exception:
        pass
    try:
        if user_repo is not None:
            # Users whose store access includes this store
            staff = user_repo.find_many({"store_ids": store_id}) or []
            stats["staff_count"] = len(staff)
    except Exception:
        pass
    return stats


@router.get("/{store_id}/users")
async def get_store_users(store_id: str, current_user: dict = Depends(get_current_user)):
    """List users with access to this store. Frontend storeApi.getStoreUsers
    was 404'ing."""
    user_repo = get_user_repository()
    if user_repo is None:
        return {"users": [], "total": 0}
    # Match either the home store or the multi-store access list
    users = user_repo.find_many({
        "$or": [
            {"store_ids": store_id},
            {"home_store_id": store_id},
            {"active_store_id": store_id},
        ]
    }) or []
    # Strip password hashes defensively
    safe = []
    for u in users:
        u.pop("password", None)
        u.pop("password_hash", None)
        u.pop("_id", None)
        safe.append(u)
    return {"users": safe, "total": len(safe)}


@router.put("/{store_id}")
async def update_store(
    store_id: str, store: StoreUpdate, current_user: dict = Depends(get_current_user)
):
    """Update store details"""
    repo = get_store_repository()

    if repo is not None:
        existing = repo.find_by_id(store_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Store not found")

        update_data = store.model_dump(exclude_unset=True)
        update_data["updated_by"] = current_user.get("user_id")

        if repo.update(store_id, update_data):
            return {"store_id": store_id, "message": "Store updated"}

        raise HTTPException(status_code=500, detail="Failed to update store")

    return {"message": "Store updated"}


@router.delete("/{store_id}")
async def delete_store(store_id: str, current_user: dict = Depends(get_current_user)):
    """Soft-delete a store (is_active=False). Frontend adminStoreApi.deleteStore
    was 404'ing. Hard delete is intentionally not exposed — stores carry
    historical orders/inventory that must remain referenceable."""
    roles = current_user.get("roles") or []
    if not any(r in {"SUPERADMIN", "ADMIN"} for r in roles):
        raise HTTPException(status_code=403, detail="SUPERADMIN/ADMIN required")
    repo = get_store_repository()
    if repo is None:
        return {"store_id": store_id, "message": "Store deactivated"}
    existing = repo.find_by_id(store_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Store not found")
    repo.update(store_id, {
        "is_active": False,
        "deactivated_at": __import__("datetime").datetime.now().isoformat(),
        "deactivated_by": current_user.get("user_id"),
    })
    return {"store_id": store_id, "message": "Store deactivated"}


@router.post("/{store_id}/categories/{category}")
async def enable_category(
    store_id: str, category: str, current_user: dict = Depends(get_current_user)
):
    """Enable a category for the store"""
    repo = get_store_repository()

    if repo is not None:
        existing = repo.find_by_id(store_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Store not found")

        if repo.enable_category(store_id, category):
            return {"message": f"Category {category} enabled"}

        raise HTTPException(status_code=500, detail="Failed to enable category")

    return {"message": f"Category {category} enabled"}


@router.delete("/{store_id}/categories/{category}")
async def disable_category(
    store_id: str, category: str, current_user: dict = Depends(get_current_user)
):
    """Disable a category for the store"""
    repo = get_store_repository()

    if repo is not None:
        existing = repo.find_by_id(store_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Store not found")

        if repo.disable_category(store_id, category):
            return {"message": f"Category {category} disabled"}

        raise HTTPException(status_code=500, detail="Failed to disable category")

    return {"message": f"Category {category} disabled"}
