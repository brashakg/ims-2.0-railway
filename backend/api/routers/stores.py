"""
IMS 2.0 - Stores Router
"""
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field
from typing import List, Optional
from .auth import get_current_user

router = APIRouter()

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
    enabled_categories: Optional[List[str]] = None
    is_active: Optional[bool] = None

@router.get("/")
async def list_stores(
    brand: Optional[str] = Query(None),
    city: Optional[str] = Query(None),
    active_only: bool = Query(True),
    current_user: dict = Depends(get_current_user)
):
    return {"stores": []}

@router.post("/", status_code=201)
async def create_store(store: StoreCreate, current_user: dict = Depends(get_current_user)):
    return {"store_id": "new-store-id", "message": "Store created"}

@router.get("/{store_id}")
async def get_store(store_id: str, current_user: dict = Depends(get_current_user)):
    return {"store_id": store_id}

@router.put("/{store_id}")
async def update_store(store_id: str, store: StoreUpdate, current_user: dict = Depends(get_current_user)):
    return {"message": "Store updated"}

@router.post("/{store_id}/categories/{category}")
async def enable_category(store_id: str, category: str, current_user: dict = Depends(get_current_user)):
    return {"message": f"Category {category} enabled"}

@router.delete("/{store_id}/categories/{category}")
async def disable_category(store_id: str, category: str, current_user: dict = Depends(get_current_user)):
    return {"message": f"Category {category} disabled"}
