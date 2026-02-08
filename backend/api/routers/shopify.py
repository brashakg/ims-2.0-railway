"""
IMS 2.0 - Comprehensive Shopify Integration Router
===================================================
Complete Shopify store management without needing to open Shopify admin.
Covers products, collections, inventory, media, SEO, metafields, and more.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
from datetime import datetime
from enum import Enum
import uuid

from .auth import get_current_user

router = APIRouter()


# ============================================================================
# ENUMS
# ============================================================================


class ProductStatus(str, Enum):
    ACTIVE = "active"
    DRAFT = "draft"
    ARCHIVED = "archived"


class InventoryPolicy(str, Enum):
    DENY = "deny"  # Stop selling when out of stock
    CONTINUE = "continue"  # Continue selling when out of stock


class WeightUnit(str, Enum):
    KG = "kg"
    G = "g"
    LB = "lb"
    OZ = "oz"


class CollectionSortOrder(str, Enum):
    ALPHA_ASC = "alpha-asc"
    ALPHA_DESC = "alpha-desc"
    BEST_SELLING = "best-selling"
    CREATED = "created"
    CREATED_DESC = "created-desc"
    MANUAL = "manual"
    PRICE_ASC = "price-asc"
    PRICE_DESC = "price-desc"


class CollectionConditionColumn(str, Enum):
    TITLE = "title"
    TYPE = "type"
    VENDOR = "vendor"
    TAG = "tag"
    PRICE = "price"
    COMPARE_AT_PRICE = "compare_at_price"
    WEIGHT = "weight"
    INVENTORY_STOCK = "inventory_stock"
    VARIANT_TITLE = "variant_title"


class CollectionConditionRelation(str, Enum):
    EQUALS = "equals"
    NOT_EQUALS = "not_equals"
    GREATER_THAN = "greater_than"
    LESS_THAN = "less_than"
    STARTS_WITH = "starts_with"
    ENDS_WITH = "ends_with"
    CONTAINS = "contains"
    NOT_CONTAINS = "not_contains"


# ============================================================================
# SCHEMAS - Products
# ============================================================================


class ProductVariantInput(BaseModel):
    sku: Optional[str] = None
    barcode: Optional[str] = None
    gtin: Optional[str] = None  # Global Trade Item Number
    upc: Optional[str] = None  # Universal Product Code
    price: float
    compare_at_price: Optional[float] = None
    cost_per_item: Optional[float] = None
    weight: Optional[float] = None
    weight_unit: WeightUnit = WeightUnit.G
    inventory_quantity: int = 0
    inventory_policy: InventoryPolicy = InventoryPolicy.DENY
    requires_shipping: bool = True
    taxable: bool = True
    option1: Optional[str] = None  # e.g., "Small"
    option2: Optional[str] = None  # e.g., "Red"
    option3: Optional[str] = None  # e.g., "Cotton"
    inventory_location_id: Optional[str] = None


class ProductMediaInput(BaseModel):
    url: str
    alt_text: Optional[str] = None
    media_type: str = "image"  # image, video, 3d_model
    position: int = 0


class ProductSEOInput(BaseModel):
    page_title: Optional[str] = None  # Max 70 chars recommended
    meta_description: Optional[str] = None  # Max 160 chars recommended
    url_handle: Optional[str] = None


class ProductMetafieldInput(BaseModel):
    namespace: str
    key: str
    value: str
    type: str = (
        "single_line_text_field"  # single_line_text_field, multi_line_text_field, number_integer, json, etc.
    )


class ProductInput(BaseModel):
    title: str
    body_html: Optional[str] = None
    vendor: Optional[str] = None
    product_type: Optional[str] = None
    tags: List[str] = []
    status: ProductStatus = ProductStatus.DRAFT
    variants: List[ProductVariantInput] = []
    options: List[Dict[str, Any]] = (
        []
    )  # e.g., [{"name": "Size", "values": ["S", "M", "L"]}]
    media: List[ProductMediaInput] = []
    seo: Optional[ProductSEOInput] = None
    metafields: List[ProductMetafieldInput] = []
    template_suffix: Optional[str] = None
    published_scope: str = "global"  # global, web


class ProductUpdate(BaseModel):
    title: Optional[str] = None
    body_html: Optional[str] = None
    vendor: Optional[str] = None
    product_type: Optional[str] = None
    tags: Optional[List[str]] = None
    status: Optional[ProductStatus] = None
    seo: Optional[ProductSEOInput] = None


# ============================================================================
# SCHEMAS - Collections
# ============================================================================


class CollectionCondition(BaseModel):
    column: CollectionConditionColumn
    relation: CollectionConditionRelation
    condition: str


class CollectionImageInput(BaseModel):
    url: str
    alt_text: Optional[str] = None


class CollectionInput(BaseModel):
    title: str
    body_html: Optional[str] = None
    handle: Optional[str] = None
    image: Optional[CollectionImageInput] = None
    sort_order: CollectionSortOrder = CollectionSortOrder.BEST_SELLING
    template_suffix: Optional[str] = None
    published: bool = True
    # For smart collections
    disjunctive: bool = False  # True = OR conditions, False = AND conditions
    conditions: List[CollectionCondition] = []
    seo: Optional[ProductSEOInput] = None
    metafields: List[ProductMetafieldInput] = []


# ============================================================================
# SCHEMAS - Inventory
# ============================================================================


class InventoryLocationInput(BaseModel):
    name: str
    address1: Optional[str] = None
    address2: Optional[str] = None
    city: Optional[str] = None
    province: Optional[str] = None
    country: str = "IN"
    zip: Optional[str] = None
    phone: Optional[str] = None
    active: bool = True
    fulfills_online_orders: bool = True


class InventoryAdjustment(BaseModel):
    inventory_item_id: str
    location_id: str
    available_adjustment: int  # Positive to add, negative to remove
    reason: Optional[str] = None


class InventoryTransfer(BaseModel):
    from_location_id: str
    to_location_id: str
    items: List[Dict[str, Any]]  # [{"inventory_item_id": "xxx", "quantity": 5}]
    notes: Optional[str] = None


# ============================================================================
# SCHEMAS - Channels/Publishing
# ============================================================================


class PublicationInput(BaseModel):
    product_id: str
    channel_id: str  # "online_store", "pos", "facebook", "instagram", etc.
    published: bool = True
    published_at: Optional[str] = None


# ============================================================================
# SCHEMAS - Shipping
# ============================================================================


class ShippingZoneInput(BaseModel):
    name: str
    countries: List[str]  # Country codes
    provinces: List[str] = []


class ShippingRateInput(BaseModel):
    name: str
    price: float
    min_order_subtotal: Optional[float] = None
    max_order_subtotal: Optional[float] = None
    min_weight: Optional[float] = None
    max_weight: Optional[float] = None
    weight_unit: WeightUnit = WeightUnit.KG


# ============================================================================
# IN-MEMORY STORAGE (would be Shopify API in production)
# ============================================================================

SHOPIFY_PRODUCTS: Dict[str, Dict] = {}
SHOPIFY_COLLECTIONS: Dict[str, Dict] = {}
SHOPIFY_INVENTORY_LOCATIONS: Dict[str, Dict] = {
    "loc_default": {
        "id": "loc_default",
        "name": "Primary Warehouse",
        "address1": "123 Vision Street",
        "city": "New Delhi",
        "province": "Delhi",
        "country": "IN",
        "zip": "110001",
        "active": True,
        "fulfills_online_orders": True,
    }
}
SHOPIFY_INVENTORY: Dict[str, Dict] = {}
SHOPIFY_CHANNELS: Dict[str, Dict] = {
    "online_store": {"id": "online_store", "name": "Online Store", "type": "web"},
    "pos": {"id": "pos", "name": "Point of Sale", "type": "pos"},
}
SHOPIFY_METAFIELDS: Dict[str, List[Dict]] = {}


# ============================================================================
# PRODUCTS ENDPOINTS
# ============================================================================


@router.get("/products")
async def list_products(
    status: Optional[ProductStatus] = None,
    product_type: Optional[str] = None,
    vendor: Optional[str] = None,
    collection_id: Optional[str] = None,
    tag: Optional[str] = None,
    created_at_min: Optional[str] = None,
    created_at_max: Optional[str] = None,
    updated_at_min: Optional[str] = None,
    limit: int = Query(default=50, le=250),
    page: int = 1,
    current_user: dict = Depends(get_current_user),
):
    """List all products with filtering"""
    products = list(SHOPIFY_PRODUCTS.values())

    # Apply filters
    if status:
        products = [p for p in products if p.get("status") == status]
    if product_type:
        products = [p for p in products if p.get("product_type") == product_type]
    if vendor:
        products = [p for p in products if p.get("vendor") == vendor]
    if tag:
        products = [p for p in products if tag in p.get("tags", [])]

    total = len(products)
    start = (page - 1) * limit
    end = start + limit

    return {
        "products": products[start:end],
        "total": total,
        "page": page,
        "limit": limit,
        "total_pages": (total + limit - 1) // limit,
    }


@router.get("/products/{product_id}")
async def get_product(product_id: str, current_user: dict = Depends(get_current_user)):
    """Get a single product with all details"""
    product = SHOPIFY_PRODUCTS.get(product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    # Include metafields
    product["metafields"] = SHOPIFY_METAFIELDS.get(product_id, [])

    return {"product": product}


@router.post("/products")
async def create_product(
    product: ProductInput, current_user: dict = Depends(get_current_user)
):
    """Create a new product in Shopify"""
    if not any(
        role in current_user.get("roles", [])
        for role in ["SUPERADMIN", "ADMIN", "CATALOG_MANAGER"]
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    product_id = f"prod_{uuid.uuid4().hex[:12]}"
    handle = (
        product.seo.url_handle
        if product.seo and product.seo.url_handle
        else product.title.lower().replace(" ", "-")
    )

    # Process variants
    variants = []
    for i, variant in enumerate(product.variants):
        variant_id = f"var_{uuid.uuid4().hex[:12]}"
        inventory_item_id = f"inv_{uuid.uuid4().hex[:12]}"

        variant_data = {
            "id": variant_id,
            "product_id": product_id,
            "inventory_item_id": inventory_item_id,
            "position": i + 1,
            **variant.model_dump(),
        }
        variants.append(variant_data)

        # Create inventory record
        SHOPIFY_INVENTORY[inventory_item_id] = {
            "id": inventory_item_id,
            "variant_id": variant_id,
            "sku": variant.sku,
            "tracked": True,
            "levels": {"loc_default": variant.inventory_quantity},
        }

    # If no variants provided, create default
    if not variants:
        variant_id = f"var_{uuid.uuid4().hex[:12]}"
        inventory_item_id = f"inv_{uuid.uuid4().hex[:12]}"
        variants.append(
            {
                "id": variant_id,
                "product_id": product_id,
                "inventory_item_id": inventory_item_id,
                "position": 1,
                "price": 0,
                "inventory_quantity": 0,
            }
        )

    # Process media
    media = []
    for i, m in enumerate(product.media):
        media.append(
            {
                "id": f"media_{uuid.uuid4().hex[:12]}",
                "position": m.position or i + 1,
                "src": m.url,
                "alt": m.alt_text,
                "media_type": m.media_type,
            }
        )

    product_data = {
        "id": product_id,
        "title": product.title,
        "body_html": product.body_html,
        "vendor": product.vendor,
        "product_type": product.product_type,
        "handle": handle,
        "tags": product.tags,
        "status": product.status,
        "options": product.options or [{"name": "Title", "values": ["Default Title"]}],
        "variants": variants,
        "media": media,
        "images": media,  # Shopify compatibility
        "seo": {
            "title": product.seo.page_title if product.seo else product.title,
            "description": product.seo.meta_description if product.seo else None,
        },
        "template_suffix": product.template_suffix,
        "published_scope": product.published_scope,
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
        "published_at": (
            datetime.now().isoformat()
            if product.status == ProductStatus.ACTIVE
            else None
        ),
    }

    SHOPIFY_PRODUCTS[product_id] = product_data

    # Store metafields
    if product.metafields:
        SHOPIFY_METAFIELDS[product_id] = [
            {
                "id": f"mf_{uuid.uuid4().hex[:8]}",
                "owner_id": product_id,
                **m.model_dump(),
            }
            for m in product.metafields
        ]

    return {"product": product_data, "message": "Product created successfully"}


@router.put("/products/{product_id}")
async def update_product(
    product_id: str,
    product: ProductUpdate,
    current_user: dict = Depends(get_current_user),
):
    """Update an existing product"""
    if not any(
        role in current_user.get("roles", [])
        for role in ["SUPERADMIN", "ADMIN", "CATALOG_MANAGER"]
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    existing = SHOPIFY_PRODUCTS.get(product_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Product not found")

    update_data = product.model_dump(exclude_none=True)

    if "seo" in update_data and update_data["seo"]:
        existing["seo"] = {
            "title": update_data["seo"].get("page_title")
            or existing.get("seo", {}).get("title"),
            "description": update_data["seo"].get("meta_description")
            or existing.get("seo", {}).get("description"),
        }
        if update_data["seo"].get("url_handle"):
            existing["handle"] = update_data["seo"]["url_handle"]
        del update_data["seo"]

    existing.update(update_data)
    existing["updated_at"] = datetime.now().isoformat()

    return {"product": existing, "message": "Product updated successfully"}


@router.delete("/products/{product_id}")
async def delete_product(
    product_id: str, current_user: dict = Depends(get_current_user)
):
    """Delete a product"""
    if not any(
        role in current_user.get("roles", []) for role in ["SUPERADMIN", "ADMIN"]
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    if product_id not in SHOPIFY_PRODUCTS:
        raise HTTPException(status_code=404, detail="Product not found")

    del SHOPIFY_PRODUCTS[product_id]
    SHOPIFY_METAFIELDS.pop(product_id, None)

    return {"message": "Product deleted successfully"}


# ============================================================================
# VARIANTS ENDPOINTS
# ============================================================================


@router.get("/products/{product_id}/variants")
async def list_variants(
    product_id: str, current_user: dict = Depends(get_current_user)
):
    """List all variants for a product"""
    product = SHOPIFY_PRODUCTS.get(product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    return {"variants": product.get("variants", [])}


@router.post("/products/{product_id}/variants")
async def create_variant(
    product_id: str,
    variant: ProductVariantInput,
    current_user: dict = Depends(get_current_user),
):
    """Add a variant to a product"""
    if not any(
        role in current_user.get("roles", [])
        for role in ["SUPERADMIN", "ADMIN", "CATALOG_MANAGER"]
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    product = SHOPIFY_PRODUCTS.get(product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    variant_id = f"var_{uuid.uuid4().hex[:12]}"
    inventory_item_id = f"inv_{uuid.uuid4().hex[:12]}"

    variant_data = {
        "id": variant_id,
        "product_id": product_id,
        "inventory_item_id": inventory_item_id,
        "position": len(product.get("variants", [])) + 1,
        **variant.model_dump(),
    }

    product.setdefault("variants", []).append(variant_data)
    product["updated_at"] = datetime.now().isoformat()

    # Create inventory record
    SHOPIFY_INVENTORY[inventory_item_id] = {
        "id": inventory_item_id,
        "variant_id": variant_id,
        "sku": variant.sku,
        "tracked": True,
        "levels": {
            variant.inventory_location_id or "loc_default": variant.inventory_quantity
        },
    }

    return {"variant": variant_data, "message": "Variant created successfully"}


@router.put("/variants/{variant_id}")
async def update_variant(
    variant_id: str,
    variant: ProductVariantInput,
    current_user: dict = Depends(get_current_user),
):
    """Update a variant"""
    if not any(
        role in current_user.get("roles", [])
        for role in ["SUPERADMIN", "ADMIN", "CATALOG_MANAGER"]
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    # Find variant in products
    for product in SHOPIFY_PRODUCTS.values():
        for i, v in enumerate(product.get("variants", [])):
            if v["id"] == variant_id:
                product["variants"][i].update(variant.model_dump(exclude_none=True))
                product["updated_at"] = datetime.now().isoformat()
                return {
                    "variant": product["variants"][i],
                    "message": "Variant updated successfully",
                }

    raise HTTPException(status_code=404, detail="Variant not found")


@router.delete("/variants/{variant_id}")
async def delete_variant(
    variant_id: str, current_user: dict = Depends(get_current_user)
):
    """Delete a variant"""
    if not any(
        role in current_user.get("roles", []) for role in ["SUPERADMIN", "ADMIN"]
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    for product in SHOPIFY_PRODUCTS.values():
        variants = product.get("variants", [])
        for i, v in enumerate(variants):
            if v["id"] == variant_id:
                if len(variants) <= 1:
                    raise HTTPException(
                        status_code=400, detail="Cannot delete the only variant"
                    )
                variants.pop(i)
                product["updated_at"] = datetime.now().isoformat()
                return {"message": "Variant deleted successfully"}

    raise HTTPException(status_code=404, detail="Variant not found")


# ============================================================================
# MEDIA ENDPOINTS
# ============================================================================


@router.get("/products/{product_id}/media")
async def list_product_media(
    product_id: str, current_user: dict = Depends(get_current_user)
):
    """List all media for a product"""
    product = SHOPIFY_PRODUCTS.get(product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    return {"media": product.get("media", [])}


@router.post("/products/{product_id}/media")
async def add_product_media(
    product_id: str,
    media: ProductMediaInput,
    current_user: dict = Depends(get_current_user),
):
    """Add media to a product"""
    if not any(
        role in current_user.get("roles", [])
        for role in ["SUPERADMIN", "ADMIN", "CATALOG_MANAGER"]
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    product = SHOPIFY_PRODUCTS.get(product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    media_id = f"media_{uuid.uuid4().hex[:12]}"
    media_data = {
        "id": media_id,
        "product_id": product_id,
        "position": media.position or len(product.get("media", [])) + 1,
        "src": media.url,
        "alt": media.alt_text,
        "media_type": media.media_type,
        "created_at": datetime.now().isoformat(),
    }

    product.setdefault("media", []).append(media_data)
    product.setdefault("images", []).append(media_data)
    product["updated_at"] = datetime.now().isoformat()

    return {"media": media_data, "message": "Media added successfully"}


@router.put("/media/{media_id}")
async def update_media(
    media_id: str,
    alt_text: Optional[str] = None,
    position: Optional[int] = None,
    current_user: dict = Depends(get_current_user),
):
    """Update media alt text or position"""
    if not any(
        role in current_user.get("roles", [])
        for role in ["SUPERADMIN", "ADMIN", "CATALOG_MANAGER"]
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    for product in SHOPIFY_PRODUCTS.values():
        for m in product.get("media", []):
            if m["id"] == media_id:
                if alt_text is not None:
                    m["alt"] = alt_text
                if position is not None:
                    m["position"] = position
                product["updated_at"] = datetime.now().isoformat()
                return {"media": m, "message": "Media updated successfully"}

    raise HTTPException(status_code=404, detail="Media not found")


@router.delete("/media/{media_id}")
async def delete_media(media_id: str, current_user: dict = Depends(get_current_user)):
    """Delete media from a product"""
    if not any(
        role in current_user.get("roles", [])
        for role in ["SUPERADMIN", "ADMIN", "CATALOG_MANAGER"]
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    for product in SHOPIFY_PRODUCTS.values():
        media = product.get("media", [])
        for i, m in enumerate(media):
            if m["id"] == media_id:
                media.pop(i)
                # Also remove from images
                images = product.get("images", [])
                product["images"] = [img for img in images if img.get("id") != media_id]
                product["updated_at"] = datetime.now().isoformat()
                return {"message": "Media deleted successfully"}

    raise HTTPException(status_code=404, detail="Media not found")


@router.put("/products/{product_id}/media/reorder")
async def reorder_media(
    product_id: str,
    media_ids: List[str],
    current_user: dict = Depends(get_current_user),
):
    """Reorder media for a product"""
    if not any(
        role in current_user.get("roles", [])
        for role in ["SUPERADMIN", "ADMIN", "CATALOG_MANAGER"]
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    product = SHOPIFY_PRODUCTS.get(product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    media = product.get("media", [])
    media_map = {m["id"]: m for m in media}

    reordered = []
    for i, mid in enumerate(media_ids):
        if mid in media_map:
            media_map[mid]["position"] = i + 1
            reordered.append(media_map[mid])

    product["media"] = reordered
    product["images"] = reordered
    product["updated_at"] = datetime.now().isoformat()

    return {"media": reordered, "message": "Media reordered successfully"}


# ============================================================================
# COLLECTIONS ENDPOINTS
# ============================================================================


@router.get("/collections")
async def list_collections(
    collection_type: Optional[str] = None,  # smart, custom
    published: Optional[bool] = None,
    limit: int = Query(default=50, le=250),
    page: int = 1,
    current_user: dict = Depends(get_current_user),
):
    """List all collections"""
    collections = list(SHOPIFY_COLLECTIONS.values())

    if collection_type:
        collections = [
            c for c in collections if c.get("collection_type") == collection_type
        ]
    if published is not None:
        collections = [c for c in collections if c.get("published") == published]

    total = len(collections)
    start = (page - 1) * limit
    end = start + limit

    return {
        "collections": collections[start:end],
        "total": total,
        "page": page,
        "total_pages": (total + limit - 1) // limit,
    }


@router.get("/collections/{collection_id}")
async def get_collection(
    collection_id: str, current_user: dict = Depends(get_current_user)
):
    """Get a single collection"""
    collection = SHOPIFY_COLLECTIONS.get(collection_id)
    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found")

    # Include products in collection
    collection["products"] = _get_collection_products(collection_id)
    collection["metafields"] = SHOPIFY_METAFIELDS.get(collection_id, [])

    return {"collection": collection}


def _get_collection_products(collection_id: str) -> List[Dict]:
    """Get products belonging to a collection"""
    collection = SHOPIFY_COLLECTIONS.get(collection_id)
    if not collection:
        return []

    if collection.get("collection_type") == "smart":
        # Filter products based on conditions
        products = []
        conditions = collection.get("conditions", [])
        disjunctive = collection.get("disjunctive", False)

        for product in SHOPIFY_PRODUCTS.values():
            matches = []
            for cond in conditions:
                match = _evaluate_condition(product, cond)
                matches.append(match)

            if disjunctive:
                if any(matches):
                    products.append(product)
            else:
                if all(matches):
                    products.append(product)

        return products
    else:
        # Manual collection - return products by ID
        product_ids = collection.get("product_ids", [])
        return [SHOPIFY_PRODUCTS[pid] for pid in product_ids if pid in SHOPIFY_PRODUCTS]


def _evaluate_condition(product: Dict, condition: Dict) -> bool:
    """Evaluate a smart collection condition against a product"""
    column = condition.get("column")
    relation = condition.get("relation")
    value = condition.get("condition", "")

    product_value = ""
    if column == "title":
        product_value = product.get("title", "")
    elif column == "type":
        product_value = product.get("product_type", "")
    elif column == "vendor":
        product_value = product.get("vendor", "")
    elif column == "tag":
        tags = product.get("tags", [])
        if relation == "equals":
            return value in tags
        elif relation == "not_equals":
            return value not in tags
        return False
    elif column == "price":
        variants = product.get("variants", [])
        if variants:
            product_value = variants[0].get("price", 0)

    if relation == "equals":
        return str(product_value).lower() == str(value).lower()
    elif relation == "not_equals":
        return str(product_value).lower() != str(value).lower()
    elif relation == "contains":
        return str(value).lower() in str(product_value).lower()
    elif relation == "not_contains":
        return str(value).lower() not in str(product_value).lower()
    elif relation == "starts_with":
        return str(product_value).lower().startswith(str(value).lower())
    elif relation == "ends_with":
        return str(product_value).lower().endswith(str(value).lower())
    elif relation == "greater_than":
        try:
            return float(product_value) > float(value)
        except (ValueError, TypeError):
            return False
    elif relation == "less_than":
        try:
            return float(product_value) < float(value)
        except (ValueError, TypeError):
            return False

    return False


@router.post("/collections")
async def create_collection(
    collection: CollectionInput, current_user: dict = Depends(get_current_user)
):
    """Create a new collection"""
    if not any(
        role in current_user.get("roles", [])
        for role in ["SUPERADMIN", "ADMIN", "CATALOG_MANAGER"]
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    collection_id = f"col_{uuid.uuid4().hex[:12]}"
    handle = collection.handle or collection.title.lower().replace(" ", "-")

    collection_type = "smart" if collection.conditions else "custom"

    collection_data = {
        "id": collection_id,
        "title": collection.title,
        "body_html": collection.body_html,
        "handle": handle,
        "image": collection.image.model_dump() if collection.image else None,
        "sort_order": collection.sort_order,
        "template_suffix": collection.template_suffix,
        "published": collection.published,
        "collection_type": collection_type,
        "disjunctive": collection.disjunctive,
        "conditions": [c.model_dump() for c in collection.conditions],
        "product_ids": [],  # For manual collections
        "seo": {
            "title": collection.seo.page_title if collection.seo else collection.title,
            "description": collection.seo.meta_description if collection.seo else None,
        },
        "products_count": 0,
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
        "published_at": datetime.now().isoformat() if collection.published else None,
    }

    # Calculate products count
    if collection_type == "smart":
        collection_data["products_count"] = len(_get_collection_products(collection_id))

    SHOPIFY_COLLECTIONS[collection_id] = collection_data

    # Store metafields
    if collection.metafields:
        SHOPIFY_METAFIELDS[collection_id] = [
            {
                "id": f"mf_{uuid.uuid4().hex[:8]}",
                "owner_id": collection_id,
                **m.model_dump(),
            }
            for m in collection.metafields
        ]

    return {"collection": collection_data, "message": "Collection created successfully"}


@router.put("/collections/{collection_id}")
async def update_collection(
    collection_id: str,
    collection: CollectionInput,
    current_user: dict = Depends(get_current_user),
):
    """Update a collection"""
    if not any(
        role in current_user.get("roles", [])
        for role in ["SUPERADMIN", "ADMIN", "CATALOG_MANAGER"]
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    existing = SHOPIFY_COLLECTIONS.get(collection_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Collection not found")

    existing.update(
        {
            "title": collection.title,
            "body_html": collection.body_html,
            "handle": collection.handle or existing.get("handle"),
            "image": (
                collection.image.model_dump()
                if collection.image
                else existing.get("image")
            ),
            "sort_order": collection.sort_order,
            "template_suffix": collection.template_suffix,
            "published": collection.published,
            "disjunctive": collection.disjunctive,
            "conditions": [c.model_dump() for c in collection.conditions],
            "seo": {
                "title": (
                    collection.seo.page_title if collection.seo else collection.title
                ),
                "description": (
                    collection.seo.meta_description if collection.seo else None
                ),
            },
            "updated_at": datetime.now().isoformat(),
            "published_at": (
                datetime.now().isoformat() if collection.published else None
            ),
        }
    )

    # Update collection type based on conditions
    existing["collection_type"] = "smart" if collection.conditions else "custom"

    return {"collection": existing, "message": "Collection updated successfully"}


@router.delete("/collections/{collection_id}")
async def delete_collection(
    collection_id: str, current_user: dict = Depends(get_current_user)
):
    """Delete a collection"""
    if not any(
        role in current_user.get("roles", []) for role in ["SUPERADMIN", "ADMIN"]
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    if collection_id not in SHOPIFY_COLLECTIONS:
        raise HTTPException(status_code=404, detail="Collection not found")

    del SHOPIFY_COLLECTIONS[collection_id]
    SHOPIFY_METAFIELDS.pop(collection_id, None)

    return {"message": "Collection deleted successfully"}


@router.post("/collections/{collection_id}/products")
async def add_products_to_collection(
    collection_id: str,
    product_ids: List[str],
    current_user: dict = Depends(get_current_user),
):
    """Add products to a manual collection"""
    if not any(
        role in current_user.get("roles", [])
        for role in ["SUPERADMIN", "ADMIN", "CATALOG_MANAGER"]
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    collection = SHOPIFY_COLLECTIONS.get(collection_id)
    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found")

    if collection.get("collection_type") == "smart":
        raise HTTPException(
            status_code=400, detail="Cannot manually add products to a smart collection"
        )

    existing_ids = set(collection.get("product_ids", []))
    for pid in product_ids:
        if pid in SHOPIFY_PRODUCTS:
            existing_ids.add(pid)

    collection["product_ids"] = list(existing_ids)
    collection["products_count"] = len(existing_ids)
    collection["updated_at"] = datetime.now().isoformat()

    return {
        "message": f"{len(product_ids)} products added to collection",
        "products_count": len(existing_ids),
    }


@router.delete("/collections/{collection_id}/products")
async def remove_products_from_collection(
    collection_id: str,
    product_ids: List[str],
    current_user: dict = Depends(get_current_user),
):
    """Remove products from a manual collection"""
    if not any(
        role in current_user.get("roles", [])
        for role in ["SUPERADMIN", "ADMIN", "CATALOG_MANAGER"]
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    collection = SHOPIFY_COLLECTIONS.get(collection_id)
    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found")

    if collection.get("collection_type") == "smart":
        raise HTTPException(
            status_code=400,
            detail="Cannot manually remove products from a smart collection",
        )

    existing_ids = set(collection.get("product_ids", []))
    for pid in product_ids:
        existing_ids.discard(pid)

    collection["product_ids"] = list(existing_ids)
    collection["products_count"] = len(existing_ids)
    collection["updated_at"] = datetime.now().isoformat()

    return {
        "message": f"{len(product_ids)} products removed from collection",
        "products_count": len(existing_ids),
    }


# ============================================================================
# TAGS ENDPOINTS
# ============================================================================


@router.get("/tags")
async def list_tags(current_user: dict = Depends(get_current_user)):
    """List all unique tags across products"""
    all_tags = set()
    for product in SHOPIFY_PRODUCTS.values():
        all_tags.update(product.get("tags", []))

    tag_counts = {}
    for tag in all_tags:
        count = sum(1 for p in SHOPIFY_PRODUCTS.values() if tag in p.get("tags", []))
        tag_counts[tag] = count

    return {
        "tags": [
            {"name": tag, "products_count": count}
            for tag, count in sorted(tag_counts.items())
        ]
    }


@router.post("/products/{product_id}/tags")
async def add_tags_to_product(
    product_id: str, tags: List[str], current_user: dict = Depends(get_current_user)
):
    """Add tags to a product"""
    if not any(
        role in current_user.get("roles", [])
        for role in ["SUPERADMIN", "ADMIN", "CATALOG_MANAGER"]
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    product = SHOPIFY_PRODUCTS.get(product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    existing_tags = set(product.get("tags", []))
    existing_tags.update(tags)
    product["tags"] = list(existing_tags)
    product["updated_at"] = datetime.now().isoformat()

    return {"tags": product["tags"], "message": "Tags added successfully"}


@router.delete("/products/{product_id}/tags")
async def remove_tags_from_product(
    product_id: str, tags: List[str], current_user: dict = Depends(get_current_user)
):
    """Remove tags from a product"""
    if not any(
        role in current_user.get("roles", [])
        for role in ["SUPERADMIN", "ADMIN", "CATALOG_MANAGER"]
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    product = SHOPIFY_PRODUCTS.get(product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    existing_tags = set(product.get("tags", []))
    for tag in tags:
        existing_tags.discard(tag)
    product["tags"] = list(existing_tags)
    product["updated_at"] = datetime.now().isoformat()

    return {"tags": product["tags"], "message": "Tags removed successfully"}


# ============================================================================
# INVENTORY LOCATIONS ENDPOINTS
# ============================================================================


@router.get("/inventory/locations")
async def list_inventory_locations(current_user: dict = Depends(get_current_user)):
    """List all inventory locations"""
    return {"locations": list(SHOPIFY_INVENTORY_LOCATIONS.values())}


@router.post("/inventory/locations")
async def create_inventory_location(
    location: InventoryLocationInput, current_user: dict = Depends(get_current_user)
):
    """Create a new inventory location"""
    if not any(
        role in current_user.get("roles", []) for role in ["SUPERADMIN", "ADMIN"]
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    location_id = f"loc_{uuid.uuid4().hex[:12]}"

    location_data = {
        "id": location_id,
        **location.model_dump(),
        "created_at": datetime.now().isoformat(),
    }

    SHOPIFY_INVENTORY_LOCATIONS[location_id] = location_data

    return {"location": location_data, "message": "Location created successfully"}


@router.put("/inventory/locations/{location_id}")
async def update_inventory_location(
    location_id: str,
    location: InventoryLocationInput,
    current_user: dict = Depends(get_current_user),
):
    """Update an inventory location"""
    if not any(
        role in current_user.get("roles", []) for role in ["SUPERADMIN", "ADMIN"]
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    existing = SHOPIFY_INVENTORY_LOCATIONS.get(location_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Location not found")

    existing.update(location.model_dump())

    return {"location": existing, "message": "Location updated successfully"}


@router.delete("/inventory/locations/{location_id}")
async def delete_inventory_location(
    location_id: str, current_user: dict = Depends(get_current_user)
):
    """Delete an inventory location"""
    if not any(
        role in current_user.get("roles", []) for role in ["SUPERADMIN", "ADMIN"]
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    if location_id == "loc_default":
        raise HTTPException(status_code=400, detail="Cannot delete default location")

    if location_id not in SHOPIFY_INVENTORY_LOCATIONS:
        raise HTTPException(status_code=404, detail="Location not found")

    del SHOPIFY_INVENTORY_LOCATIONS[location_id]

    return {"message": "Location deleted successfully"}


# ============================================================================
# INVENTORY LEVELS ENDPOINTS
# ============================================================================


@router.get("/inventory/levels")
async def get_inventory_levels(
    location_id: Optional[str] = None,
    inventory_item_ids: Optional[str] = None,  # Comma-separated
    current_user: dict = Depends(get_current_user),
):
    """Get inventory levels"""
    levels = []

    item_ids = inventory_item_ids.split(",") if inventory_item_ids else None

    for inv_item_id, inv_data in SHOPIFY_INVENTORY.items():
        if item_ids and inv_item_id not in item_ids:
            continue

        for loc_id, quantity in inv_data.get("levels", {}).items():
            if location_id and loc_id != location_id:
                continue
            levels.append(
                {
                    "inventory_item_id": inv_item_id,
                    "location_id": loc_id,
                    "available": quantity,
                    "updated_at": datetime.now().isoformat(),
                }
            )

    return {"inventory_levels": levels}


@router.post("/inventory/adjust")
async def adjust_inventory(
    adjustment: InventoryAdjustment, current_user: dict = Depends(get_current_user)
):
    """Adjust inventory level for an item at a location"""
    if not any(
        role in current_user.get("roles", [])
        for role in ["SUPERADMIN", "ADMIN", "STORE_MANAGER", "WORKSHOP_STAFF"]
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    inv_item = SHOPIFY_INVENTORY.get(adjustment.inventory_item_id)
    if not inv_item:
        raise HTTPException(status_code=404, detail="Inventory item not found")

    if adjustment.location_id not in SHOPIFY_INVENTORY_LOCATIONS:
        raise HTTPException(status_code=404, detail="Location not found")

    current_level = inv_item.get("levels", {}).get(adjustment.location_id, 0)
    new_level = current_level + adjustment.available_adjustment

    if new_level < 0:
        raise HTTPException(
            status_code=400, detail="Cannot adjust to negative inventory"
        )

    inv_item.setdefault("levels", {})[adjustment.location_id] = new_level

    return {
        "inventory_level": {
            "inventory_item_id": adjustment.inventory_item_id,
            "location_id": adjustment.location_id,
            "available": new_level,
            "adjustment": adjustment.available_adjustment,
        },
        "message": "Inventory adjusted successfully",
    }


@router.post("/inventory/set")
async def set_inventory_level(
    inventory_item_id: str,
    location_id: str,
    available: int,
    current_user: dict = Depends(get_current_user),
):
    """Set inventory level to a specific value"""
    if not any(
        role in current_user.get("roles", [])
        for role in ["SUPERADMIN", "ADMIN", "STORE_MANAGER"]
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    inv_item = SHOPIFY_INVENTORY.get(inventory_item_id)
    if not inv_item:
        raise HTTPException(status_code=404, detail="Inventory item not found")

    if location_id not in SHOPIFY_INVENTORY_LOCATIONS:
        raise HTTPException(status_code=404, detail="Location not found")

    if available < 0:
        raise HTTPException(status_code=400, detail="Inventory cannot be negative")

    inv_item.setdefault("levels", {})[location_id] = available

    return {
        "inventory_level": {
            "inventory_item_id": inventory_item_id,
            "location_id": location_id,
            "available": available,
        },
        "message": "Inventory set successfully",
    }


# ============================================================================
# CHANNELS/PUBLISHING ENDPOINTS
# ============================================================================


@router.get("/channels")
async def list_channels(current_user: dict = Depends(get_current_user)):
    """List all sales channels"""
    return {"channels": list(SHOPIFY_CHANNELS.values())}


@router.get("/products/{product_id}/publications")
async def get_product_publications(
    product_id: str, current_user: dict = Depends(get_current_user)
):
    """Get publication status for a product across channels"""
    product = SHOPIFY_PRODUCTS.get(product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    publications = product.get("publications", {})

    result = []
    for channel_id, channel in SHOPIFY_CHANNELS.items():
        pub = publications.get(channel_id, {"published": False})
        result.append(
            {
                "channel_id": channel_id,
                "channel_name": channel["name"],
                "published": pub.get("published", False),
                "published_at": pub.get("published_at"),
            }
        )

    return {"publications": result}


@router.post("/products/{product_id}/publish")
async def publish_product(
    product_id: str,
    publication: PublicationInput,
    current_user: dict = Depends(get_current_user),
):
    """Publish or unpublish a product to a channel"""
    if not any(
        role in current_user.get("roles", [])
        for role in ["SUPERADMIN", "ADMIN", "CATALOG_MANAGER"]
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    product = SHOPIFY_PRODUCTS.get(product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    if publication.channel_id not in SHOPIFY_CHANNELS:
        raise HTTPException(status_code=404, detail="Channel not found")

    product.setdefault("publications", {})[publication.channel_id] = {
        "published": publication.published,
        "published_at": datetime.now().isoformat() if publication.published else None,
    }
    product["updated_at"] = datetime.now().isoformat()

    return {
        "message": f"Product {'published to' if publication.published else 'unpublished from'} {SHOPIFY_CHANNELS[publication.channel_id]['name']}"
    }


@router.post("/products/bulk-publish")
async def bulk_publish_products(
    product_ids: List[str],
    channel_id: str,
    published: bool = True,
    current_user: dict = Depends(get_current_user),
):
    """Bulk publish/unpublish products to a channel"""
    if not any(
        role in current_user.get("roles", [])
        for role in ["SUPERADMIN", "ADMIN", "CATALOG_MANAGER"]
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    if channel_id not in SHOPIFY_CHANNELS:
        raise HTTPException(status_code=404, detail="Channel not found")

    updated = 0
    for pid in product_ids:
        product = SHOPIFY_PRODUCTS.get(pid)
        if product:
            product.setdefault("publications", {})[channel_id] = {
                "published": published,
                "published_at": datetime.now().isoformat() if published else None,
            }
            product["updated_at"] = datetime.now().isoformat()
            updated += 1

    return {
        "message": f"{updated} products {'published' if published else 'unpublished'}",
        "updated_count": updated,
    }


# ============================================================================
# METAFIELDS ENDPOINTS
# ============================================================================


@router.get("/products/{product_id}/metafields")
async def get_product_metafields(
    product_id: str,
    namespace: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
):
    """Get metafields for a product"""
    if product_id not in SHOPIFY_PRODUCTS:
        raise HTTPException(status_code=404, detail="Product not found")

    metafields = SHOPIFY_METAFIELDS.get(product_id, [])

    if namespace:
        metafields = [m for m in metafields if m.get("namespace") == namespace]

    return {"metafields": metafields}


@router.post("/products/{product_id}/metafields")
async def add_product_metafield(
    product_id: str,
    metafield: ProductMetafieldInput,
    current_user: dict = Depends(get_current_user),
):
    """Add a metafield to a product"""
    if not any(
        role in current_user.get("roles", [])
        for role in ["SUPERADMIN", "ADMIN", "CATALOG_MANAGER"]
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    if product_id not in SHOPIFY_PRODUCTS:
        raise HTTPException(status_code=404, detail="Product not found")

    metafield_id = f"mf_{uuid.uuid4().hex[:8]}"
    metafield_data = {
        "id": metafield_id,
        "owner_id": product_id,
        "owner_resource": "product",
        **metafield.model_dump(),
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
    }

    SHOPIFY_METAFIELDS.setdefault(product_id, []).append(metafield_data)

    return {"metafield": metafield_data, "message": "Metafield added successfully"}


@router.put("/metafields/{metafield_id}")
async def update_metafield(
    metafield_id: str, value: str, current_user: dict = Depends(get_current_user)
):
    """Update a metafield value"""
    if not any(
        role in current_user.get("roles", [])
        for role in ["SUPERADMIN", "ADMIN", "CATALOG_MANAGER"]
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    for owner_id, metafields in SHOPIFY_METAFIELDS.items():
        for mf in metafields:
            if mf["id"] == metafield_id:
                mf["value"] = value
                mf["updated_at"] = datetime.now().isoformat()
                return {"metafield": mf, "message": "Metafield updated successfully"}

    raise HTTPException(status_code=404, detail="Metafield not found")


@router.delete("/metafields/{metafield_id}")
async def delete_metafield(
    metafield_id: str, current_user: dict = Depends(get_current_user)
):
    """Delete a metafield"""
    if not any(
        role in current_user.get("roles", []) for role in ["SUPERADMIN", "ADMIN"]
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    for owner_id, metafields in SHOPIFY_METAFIELDS.items():
        for i, mf in enumerate(metafields):
            if mf["id"] == metafield_id:
                metafields.pop(i)
                return {"message": "Metafield deleted successfully"}

    raise HTTPException(status_code=404, detail="Metafield not found")


# ============================================================================
# SEO ENDPOINTS
# ============================================================================


@router.get("/products/{product_id}/seo")
async def get_product_seo(
    product_id: str, current_user: dict = Depends(get_current_user)
):
    """Get SEO data for a product"""
    product = SHOPIFY_PRODUCTS.get(product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    return {
        "seo": {
            "title": product.get("seo", {}).get("title"),
            "description": product.get("seo", {}).get("description"),
            "handle": product.get("handle"),
            "url": f"/products/{product.get('handle')}",
        }
    }


@router.put("/products/{product_id}/seo")
async def update_product_seo(
    product_id: str,
    seo: ProductSEOInput,
    current_user: dict = Depends(get_current_user),
):
    """Update SEO data for a product"""
    if not any(
        role in current_user.get("roles", [])
        for role in ["SUPERADMIN", "ADMIN", "CATALOG_MANAGER"]
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    product = SHOPIFY_PRODUCTS.get(product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    product["seo"] = {
        "title": seo.page_title or product.get("seo", {}).get("title"),
        "description": seo.meta_description
        or product.get("seo", {}).get("description"),
    }

    if seo.url_handle:
        product["handle"] = seo.url_handle

    product["updated_at"] = datetime.now().isoformat()

    return {
        "seo": product["seo"],
        "handle": product["handle"],
        "message": "SEO updated successfully",
    }


# ============================================================================
# BULK OPERATIONS
# ============================================================================


@router.post("/products/bulk-update")
async def bulk_update_products(
    product_ids: List[str],
    update: Dict[str, Any],
    current_user: dict = Depends(get_current_user),
):
    """Bulk update multiple products"""
    if not any(
        role in current_user.get("roles", [])
        for role in ["SUPERADMIN", "ADMIN", "CATALOG_MANAGER"]
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    updated = 0
    for pid in product_ids:
        product = SHOPIFY_PRODUCTS.get(pid)
        if product:
            for key, value in update.items():
                if key in [
                    "title",
                    "body_html",
                    "vendor",
                    "product_type",
                    "status",
                    "tags",
                ]:
                    product[key] = value
            product["updated_at"] = datetime.now().isoformat()
            updated += 1

    return {"message": f"{updated} products updated", "updated_count": updated}


@router.post("/products/bulk-delete")
async def bulk_delete_products(
    product_ids: List[str], current_user: dict = Depends(get_current_user)
):
    """Bulk delete multiple products"""
    if not any(
        role in current_user.get("roles", []) for role in ["SUPERADMIN", "ADMIN"]
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    deleted = 0
    for pid in product_ids:
        if pid in SHOPIFY_PRODUCTS:
            del SHOPIFY_PRODUCTS[pid]
            SHOPIFY_METAFIELDS.pop(pid, None)
            deleted += 1

    return {"message": f"{deleted} products deleted", "deleted_count": deleted}


# ============================================================================
# SYNC WITH IMS
# ============================================================================


@router.post("/sync/products-to-shopify")
async def sync_products_to_shopify(
    product_ids: Optional[List[str]] = None,
    current_user: dict = Depends(get_current_user),
):
    """Sync IMS products to Shopify"""
    if not any(
        role in current_user.get("roles", []) for role in ["SUPERADMIN", "ADMIN"]
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    # In production, would fetch from IMS database and push to Shopify
    return {
        "status": "success",
        "message": "Products synced to Shopify",
        "sync_details": {
            "products_synced": len(product_ids) if product_ids else 0,
            "products_created": 0,
            "products_updated": len(product_ids) if product_ids else 0,
            "errors": 0,
            "sync_time": datetime.now().isoformat(),
        },
    }


@router.post("/sync/shopify-to-ims")
async def sync_shopify_to_ims(
    since: Optional[str] = None, current_user: dict = Depends(get_current_user)
):
    """Sync Shopify products to IMS"""
    if not any(
        role in current_user.get("roles", []) for role in ["SUPERADMIN", "ADMIN"]
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    # In production, would fetch from Shopify API and update IMS database
    return {
        "status": "success",
        "message": "Products synced from Shopify",
        "sync_details": {
            "products_fetched": len(SHOPIFY_PRODUCTS),
            "products_created": 0,
            "products_updated": len(SHOPIFY_PRODUCTS),
            "errors": 0,
            "sync_time": datetime.now().isoformat(),
        },
    }


@router.post("/sync/inventory")
async def sync_inventory(
    location_id: Optional[str] = None, current_user: dict = Depends(get_current_user)
):
    """Sync inventory levels between IMS and Shopify"""
    if not any(
        role in current_user.get("roles", [])
        for role in ["SUPERADMIN", "ADMIN", "STORE_MANAGER"]
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    return {
        "status": "success",
        "message": "Inventory synced",
        "sync_details": {
            "items_synced": len(SHOPIFY_INVENTORY),
            "locations_synced": 1 if location_id else len(SHOPIFY_INVENTORY_LOCATIONS),
            "discrepancies_found": 0,
            "discrepancies_resolved": 0,
            "sync_time": datetime.now().isoformat(),
        },
    }
