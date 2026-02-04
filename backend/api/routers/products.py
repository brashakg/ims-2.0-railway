"""
IMS 2.0 - Products Router
"""
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field
from typing import List, Optional
from .auth import get_current_user

router = APIRouter()

class ProductCreate(BaseModel):
    sku: str
    category: str
    brand: str
    model: str
    variant: Optional[str] = None
    color: Optional[str] = None
    size: Optional[str] = None
    mrp: float = Field(..., gt=0)
    offer_price: float = Field(..., gt=0)
    hsn_code: Optional[str] = None
    tax_rate: float = Field(default=18.0)
    attributes: Optional[dict] = None

class ProductUpdate(BaseModel):
    brand: Optional[str] = None
    model: Optional[str] = None
    mrp: Optional[float] = Field(None, gt=0)
    offer_price: Optional[float] = Field(None, gt=0)
    is_active: Optional[bool] = None

@router.get("/")
async def list_products(
    category: Optional[str] = Query(None),
    brand: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    current_user: dict = Depends(get_current_user)
):
    return {"products": [], "total": 0}

@router.post("/", status_code=201)
async def create_product(product: ProductCreate, current_user: dict = Depends(get_current_user)):
    # Validate MRP >= Offer Price
    if product.offer_price > product.mrp:
        raise HTTPException(status_code=400, detail="Offer price cannot exceed MRP")
    return {"product_id": "new-product-id", "sku": product.sku}

@router.get("/sku/{sku}")
async def get_product_by_sku(sku: str, current_user: dict = Depends(get_current_user)):
    return {"sku": sku}

@router.get("/{product_id}")
async def get_product(product_id: str, current_user: dict = Depends(get_current_user)):
    return {"product_id": product_id}

@router.put("/{product_id}")
async def update_product(product_id: str, product: ProductUpdate, current_user: dict = Depends(get_current_user)):
    return {"message": "Product updated"}

@router.get("/brands/list")
async def list_brands(category: Optional[str] = Query(None), current_user: dict = Depends(get_current_user)):
    return {"brands": []}

@router.get("/categories/list")
async def list_categories(current_user: dict = Depends(get_current_user)):
    return {"categories": ["FRAME", "SUNGLASS", "READING_GLASSES", "OPTICAL_LENS", "CONTACT_LENS",
                          "COLORED_CONTACT_LENS", "WATCH", "SMARTWATCH", "SMARTGLASSES", "WALL_CLOCK",
                          "ACCESSORIES", "SERVICES"]}
