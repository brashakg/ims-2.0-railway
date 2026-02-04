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
from ..dependencies import get_store_repository

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

    if repo:
        summary = repo.get_store_summary()
        return {"summary": summary}

    return {"summary": {}}


@router.get("/")
async def list_stores(
    brand: Optional[str] = Query(None),
    city: Optional[str] = Query(None),
    active_only: bool = Query(True),
    current_user: dict = Depends(get_current_user)
):
    """List stores with optional filtering"""
    repo = get_store_repository()

    if repo:
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


@router.post("/", status_code=201)
async def create_store(
    store: StoreCreate,
    current_user: dict = Depends(get_current_user)
):
    """Create a new store"""
    repo = get_store_repository()

    if repo:
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
            "created_by": current_user.get("user_id")
        }

        created = repo.create(store_data)
        if created:
            return {
                "store_id": created["store_id"],
                "store_code": created["store_code"],
                "message": "Store created"
            }

        raise HTTPException(status_code=500, detail="Failed to create store")

    return {"store_id": str(uuid.uuid4()), "message": "Store created"}


@router.get("/{store_id}")
async def get_store(
    store_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Get store by ID"""
    repo = get_store_repository()

    if repo:
        store = repo.find_by_id(store_id)
        if store:
            return store
        raise HTTPException(status_code=404, detail="Store not found")

    return {"store_id": store_id}


@router.put("/{store_id}")
async def update_store(
    store_id: str,
    store: StoreUpdate,
    current_user: dict = Depends(get_current_user)
):
    """Update store details"""
    repo = get_store_repository()

    if repo:
        existing = repo.find_by_id(store_id)
        if not existing:
            raise HTTPException(status_code=404, detail="Store not found")

        update_data = store.model_dump(exclude_unset=True)
        update_data["updated_by"] = current_user.get("user_id")

        if repo.update(store_id, update_data):
            return {"store_id": store_id, "message": "Store updated"}

        raise HTTPException(status_code=500, detail="Failed to update store")

    return {"message": "Store updated"}


@router.post("/{store_id}/categories/{category}")
async def enable_category(
    store_id: str,
    category: str,
    current_user: dict = Depends(get_current_user)
):
    """Enable a category for the store"""
    repo = get_store_repository()

    if repo:
        existing = repo.find_by_id(store_id)
        if not existing:
            raise HTTPException(status_code=404, detail="Store not found")

        if repo.enable_category(store_id, category):
            return {"message": f"Category {category} enabled"}

        raise HTTPException(status_code=500, detail="Failed to enable category")

    return {"message": f"Category {category} enabled"}


@router.delete("/{store_id}/categories/{category}")
async def disable_category(
    store_id: str,
    category: str,
    current_user: dict = Depends(get_current_user)
):
    """Disable a category for the store"""
    repo = get_store_repository()

    if repo:
        existing = repo.find_by_id(store_id)
        if not existing:
            raise HTTPException(status_code=404, detail="Store not found")

        if repo.disable_category(store_id, category):
            return {"message": f"Category {category} disabled"}

        raise HTTPException(status_code=500, detail="Failed to disable category")

    return {"message": f"Category {category} disabled"}
