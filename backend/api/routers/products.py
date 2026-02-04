"""
IMS 2.0 - Products Router
==========================
Product catalog management endpoints
"""
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field
from typing import List, Optional
import uuid
from .auth import get_current_user
from ..dependencies import get_product_repository

router = APIRouter()


# ============================================================================
# SCHEMAS
# ============================================================================

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


# ============================================================================
# ENDPOINTS
# ============================================================================

@router.get("/")
async def list_products(
    category: Optional[str] = Query(None),
    brand: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    current_user: dict = Depends(get_current_user)
):
    """List products with filtering"""
    repo = get_product_repository()

    if repo:
        if search:
            products = repo.search_products(search, category)
        elif brand:
            products = repo.find_by_brand(brand, category)
        elif category:
            products = repo.find_by_category(category)
        else:
            products = repo.find_many({}, skip=skip, limit=limit)

        total = len(products) if search or brand else repo.count({"category": category} if category else {})
        return {"products": products, "total": total}

    return {"products": [], "total": 0}


@router.post("/", status_code=201)
async def create_product(
    product: ProductCreate,
    current_user: dict = Depends(get_current_user)
):
    """Create a new product"""
    # Validate MRP >= Offer Price
    if product.offer_price > product.mrp:
        raise HTTPException(status_code=400, detail="Offer price cannot exceed MRP")

    repo = get_product_repository()

    if repo:
        # Check if SKU already exists
        existing = repo.find_by_sku(product.sku)
        if existing:
            raise HTTPException(status_code=400, detail="Product with this SKU already exists")

        product_data = {
            "sku": product.sku,
            "category": product.category,
            "brand": product.brand,
            "model": product.model,
            "variant": product.variant,
            "color": product.color,
            "size": product.size,
            "mrp": product.mrp,
            "offer_price": product.offer_price,
            "hsn_code": product.hsn_code,
            "tax_rate": product.tax_rate,
            "attributes": product.attributes or {},
            "is_active": True,
            "created_by": current_user.get("user_id")
        }

        created = repo.create(product_data)
        if created:
            return {"product_id": created["product_id"], "sku": created["sku"]}

        raise HTTPException(status_code=500, detail="Failed to create product")

    return {"product_id": str(uuid.uuid4()), "sku": product.sku}


# NOTE: Specific routes MUST come before /{product_id}
@router.get("/brands/list")
async def list_brands(
    category: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user)
):
    """List all brands, optionally filtered by category"""
    repo = get_product_repository()

    if repo:
        brands = repo.get_brands(category)
        return {"brands": brands}

    return {"brands": []}


@router.get("/categories/list")
async def list_categories(current_user: dict = Depends(get_current_user)):
    """List all product categories"""
    return {
        "categories": [
            "FRAME", "SUNGLASS", "READING_GLASSES", "OPTICAL_LENS", "CONTACT_LENS",
            "COLORED_CONTACT_LENS", "WATCH", "SMARTWATCH", "SMARTGLASSES", "WALL_CLOCK",
            "HEARING_AID", "ACCESSORIES", "SERVICES"
        ]
    }


@router.get("/sku/{sku}")
async def get_product_by_sku(
    sku: str,
    current_user: dict = Depends(get_current_user)
):
    """Get product by SKU"""
    repo = get_product_repository()

    if repo:
        product = repo.find_by_sku(sku)
        if product:
            return product
        raise HTTPException(status_code=404, detail="Product not found")

    return {"sku": sku}


@router.get("/{product_id}")
async def get_product(
    product_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Get product by ID"""
    repo = get_product_repository()

    if repo:
        product = repo.find_by_id(product_id)
        if product:
            return product
        raise HTTPException(status_code=404, detail="Product not found")

    return {"product_id": product_id}


@router.put("/{product_id}")
async def update_product(
    product_id: str,
    product: ProductUpdate,
    current_user: dict = Depends(get_current_user)
):
    """Update product details"""
    repo = get_product_repository()

    if repo:
        existing = repo.find_by_id(product_id)
        if not existing:
            raise HTTPException(status_code=404, detail="Product not found")

        update_data = product.model_dump(exclude_unset=True)

        # Validate MRP >= Offer Price if both are being updated
        if "mrp" in update_data and "offer_price" in update_data:
            if update_data["offer_price"] > update_data["mrp"]:
                raise HTTPException(status_code=400, detail="Offer price cannot exceed MRP")

        update_data["updated_by"] = current_user.get("user_id")

        if repo.update(product_id, update_data):
            return {"message": "Product updated", "product_id": product_id}

        raise HTTPException(status_code=500, detail="Failed to update product")

    return {"message": "Product updated"}
