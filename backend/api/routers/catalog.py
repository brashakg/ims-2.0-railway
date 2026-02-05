"""
IMS 2.0 - Catalog Router
========================
Comprehensive product catalog management with category-specific fields.
Handles product creation, SKU generation, and Shopify sync.
"""
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List, Union
from datetime import datetime
from enum import Enum
import uuid

from .auth import get_current_user

router = APIRouter()


# ============================================================================
# PRODUCT CATEGORIES WITH SKU PREFIXES
# ============================================================================

class ProductCategory(str, Enum):
    SUNGLASS = "SG"
    CONTACT_LENS = "CL"
    FRAME = "FR"
    ACCESSORIES = "ACC"
    LENS = "LS"
    READING_GLASSES = "RG"
    WRIST_WATCH = "WT"
    CLOCK = "CK"
    HEARING_AID = "HA"
    SMART_SUNGLASS = "SMTSG"
    SMART_FRAME = "SMTFR"
    SMART_WATCH = "SMTWT"


# Category display names
CATEGORY_NAMES = {
    ProductCategory.SUNGLASS: "Sunglass",
    ProductCategory.CONTACT_LENS: "Contact Lens",
    ProductCategory.FRAME: "Frame",
    ProductCategory.ACCESSORIES: "Accessories",
    ProductCategory.LENS: "Optical Lens",
    ProductCategory.READING_GLASSES: "Reading Glasses",
    ProductCategory.WRIST_WATCH: "Wrist Watch",
    ProductCategory.CLOCK: "Clock",
    ProductCategory.HEARING_AID: "Hearing Aid",
    ProductCategory.SMART_SUNGLASS: "Smart Sunglass",
    ProductCategory.SMART_FRAME: "Smart Glasses",
    ProductCategory.SMART_WATCH: "Smart Watch",
}


# ============================================================================
# CATEGORY-SPECIFIC FIELD DEFINITIONS
# ============================================================================

# Define which fields each category needs
CATEGORY_FIELDS = {
    ProductCategory.SUNGLASS: {
        "required": ["brand_name", "model_no", "colour_code"],
        "optional": ["subbrand", "lens_size", "bridge_width", "temple_length"],
        "fields": [
            {"name": "brand_name", "label": "Brand Name", "type": "select", "required": True},
            {"name": "subbrand", "label": "Sub Brand", "type": "text", "required": False},
            {"name": "model_no", "label": "Model No", "type": "text", "required": True},
            {"name": "colour_code", "label": "Colour Code", "type": "text", "required": True},
            {"name": "lens_size", "label": "Lens Size (mm)", "type": "number", "required": False},
            {"name": "bridge_width", "label": "Bridge Width (mm)", "type": "number", "required": False},
            {"name": "temple_length", "label": "Temple Length (mm)", "type": "number", "required": False},
        ]
    },
    ProductCategory.CONTACT_LENS: {
        "required": ["brand_name", "model_name", "power"],
        "optional": ["subbrand", "colour_name", "pack", "expiry_date"],
        "fields": [
            {"name": "brand_name", "label": "Brand Name", "type": "select", "required": True},
            {"name": "subbrand", "label": "Sub Brand", "type": "text", "required": False},
            {"name": "model_name", "label": "Model Name", "type": "text", "required": True},
            {"name": "colour_name", "label": "Colour Name", "type": "text", "required": False},
            {"name": "power", "label": "Power", "type": "text", "required": True, "placeholder": "-6.00 to +6.00"},
            {"name": "pack", "label": "Pack Size", "type": "select", "required": False, "options": ["1", "3", "6", "30", "90"]},
            {"name": "expiry_date", "label": "Expiry Date", "type": "date", "required": False},
        ]
    },
    ProductCategory.FRAME: {
        "required": ["brand_name", "model_no", "colour_code"],
        "optional": ["subbrand", "lens_size", "bridge_width", "temple_length"],
        "fields": [
            {"name": "brand_name", "label": "Brand Name", "type": "select", "required": True},
            {"name": "subbrand", "label": "Sub Brand", "type": "text", "required": False},
            {"name": "model_no", "label": "Model No", "type": "text", "required": True},
            {"name": "colour_code", "label": "Colour Code", "type": "text", "required": True},
            {"name": "lens_size", "label": "Lens Size (mm)", "type": "number", "required": False},
            {"name": "bridge_width", "label": "Bridge Width (mm)", "type": "number", "required": False},
            {"name": "temple_length", "label": "Temple Length (mm)", "type": "number", "required": False},
        ]
    },
    ProductCategory.ACCESSORIES: {
        "required": ["brand_name", "model_name"],
        "optional": ["subbrand", "size", "pack", "expiry_date", "accessory_type"],
        "fields": [
            {"name": "brand_name", "label": "Brand Name", "type": "select", "required": True},
            {"name": "subbrand", "label": "Sub Brand", "type": "text", "required": False},
            {"name": "model_name", "label": "Model Name", "type": "text", "required": True},
            {"name": "accessory_type", "label": "Accessory Type", "type": "select", "required": False,
             "options": ["Case", "Cloth", "Chain", "Nose Pad", "Temple Tip", "Screw Kit", "Spray", "Other"]},
            {"name": "size", "label": "Size", "type": "text", "required": False},
            {"name": "pack", "label": "Pack Size", "type": "number", "required": False},
            {"name": "expiry_date", "label": "Expiry Date", "type": "date", "required": False},
        ]
    },
    ProductCategory.LENS: {
        "required": ["brand_name", "index", "coating"],
        "optional": ["subbrand", "lens_category", "add_on_1", "add_on_2", "add_on_3"],
        "fields": [
            {"name": "brand_name", "label": "Brand Name", "type": "select", "required": True},
            {"name": "subbrand", "label": "Sub Brand", "type": "text", "required": False},
            {"name": "index", "label": "Index", "type": "select", "required": True,
             "options": ["1.50", "1.56", "1.59", "1.60", "1.67", "1.74"]},
            {"name": "coating", "label": "Coating", "type": "select", "required": True,
             "options": ["UC", "HC", "ARC", "Blue Cut", "Photochromic", "Transitions", "Polarized"]},
            {"name": "lens_category", "label": "Lens Category", "type": "select", "required": False,
             "options": ["Single Vision", "Bifocal", "Progressive", "Office", "Driving"]},
            {"name": "add_on_1", "label": "Add-On 1", "type": "text", "required": False},
            {"name": "add_on_2", "label": "Add-On 2", "type": "text", "required": False},
            {"name": "add_on_3", "label": "Add-On 3", "type": "text", "required": False},
        ]
    },
    ProductCategory.READING_GLASSES: {
        "required": ["brand_name", "model_no", "colour_code"],
        "optional": ["subbrand", "lens_size", "bridge_width", "temple_length", "power"],
        "fields": [
            {"name": "brand_name", "label": "Brand Name", "type": "select", "required": True},
            {"name": "subbrand", "label": "Sub Brand", "type": "text", "required": False},
            {"name": "model_no", "label": "Model No", "type": "text", "required": True},
            {"name": "colour_code", "label": "Colour Code", "type": "text", "required": True},
            {"name": "power", "label": "Power", "type": "select", "required": False,
             "options": ["+1.00", "+1.25", "+1.50", "+1.75", "+2.00", "+2.25", "+2.50", "+2.75", "+3.00", "+3.50"]},
            {"name": "lens_size", "label": "Lens Size (mm)", "type": "number", "required": False},
            {"name": "bridge_width", "label": "Bridge Width (mm)", "type": "number", "required": False},
            {"name": "temple_length", "label": "Temple Length (mm)", "type": "number", "required": False},
        ]
    },
    ProductCategory.WRIST_WATCH: {
        "required": ["brand_name", "model_no", "colour_code"],
        "optional": ["subbrand", "dial_colour", "belt_colour", "dial_size", "belt_size", "watch_category"],
        "fields": [
            {"name": "brand_name", "label": "Brand Name", "type": "select", "required": True},
            {"name": "subbrand", "label": "Sub Brand", "type": "text", "required": False},
            {"name": "model_no", "label": "Model No", "type": "text", "required": True},
            {"name": "colour_code", "label": "Colour Code", "type": "text", "required": True},
            {"name": "dial_colour", "label": "Dial Colour", "type": "text", "required": False},
            {"name": "belt_colour", "label": "Belt Colour", "type": "text", "required": False},
            {"name": "dial_size", "label": "Dial Size (mm)", "type": "number", "required": False},
            {"name": "belt_size", "label": "Belt Size (mm)", "type": "number", "required": False},
            {"name": "watch_category", "label": "Watch Category", "type": "select", "required": False,
             "options": ["Analog", "Digital", "Analog-Digital", "Chronograph", "Automatic", "Quartz"]},
        ]
    },
    ProductCategory.CLOCK: {
        "required": ["brand_name", "model_no", "colour_code"],
        "optional": ["subbrand", "dial_colour", "body_colour", "dial_size", "battery_size", "clock_category"],
        "fields": [
            {"name": "brand_name", "label": "Brand Name", "type": "select", "required": True},
            {"name": "subbrand", "label": "Sub Brand", "type": "text", "required": False},
            {"name": "model_no", "label": "Model No", "type": "text", "required": True},
            {"name": "colour_code", "label": "Colour Code", "type": "text", "required": True},
            {"name": "dial_colour", "label": "Dial Colour", "type": "text", "required": False},
            {"name": "body_colour", "label": "Body Colour", "type": "text", "required": False},
            {"name": "dial_size", "label": "Dial Size (inches)", "type": "number", "required": False},
            {"name": "battery_size", "label": "Battery Size", "type": "text", "required": False},
            {"name": "clock_category", "label": "Clock Category", "type": "select", "required": False,
             "options": ["Wall Clock", "Table Clock", "Alarm Clock", "Desk Clock", "Decorative"]},
        ]
    },
    ProductCategory.HEARING_AID: {
        "required": ["brand_name", "model_no"],
        "optional": ["subbrand", "serial_no", "machine_capacity", "machine_type"],
        "fields": [
            {"name": "brand_name", "label": "Brand Name", "type": "select", "required": True},
            {"name": "subbrand", "label": "Sub Brand", "type": "text", "required": False},
            {"name": "model_no", "label": "Model No", "type": "text", "required": True},
            {"name": "serial_no", "label": "Serial No", "type": "text", "required": False},
            {"name": "machine_capacity", "label": "Machine Capacity", "type": "select", "required": False,
             "options": ["Mild", "Moderate", "Severe", "Profound"]},
            {"name": "machine_type", "label": "Machine Type", "type": "select", "required": False,
             "options": ["BTE", "ITE", "ITC", "CIC", "RIC", "Body Worn"]},
        ]
    },
    ProductCategory.SMART_SUNGLASS: {
        "required": ["brand_name", "model_name", "colour_code"],
        "optional": ["subbrand", "lens_size", "bridge_width", "temple_length", "year_of_launch"],
        "fields": [
            {"name": "brand_name", "label": "Brand Name", "type": "select", "required": True},
            {"name": "subbrand", "label": "Sub Brand", "type": "text", "required": False},
            {"name": "model_name", "label": "Model Name", "type": "text", "required": True},
            {"name": "colour_code", "label": "Colour Code", "type": "text", "required": True},
            {"name": "lens_size", "label": "Lens Size (mm)", "type": "number", "required": False},
            {"name": "bridge_width", "label": "Bridge Width (mm)", "type": "number", "required": False},
            {"name": "temple_length", "label": "Temple Length (mm)", "type": "number", "required": False},
            {"name": "year_of_launch", "label": "Year of Launch", "type": "number", "required": False},
        ]
    },
    ProductCategory.SMART_FRAME: {
        "required": ["brand_name", "model_name", "colour_code"],
        "optional": ["subbrand", "lens_size", "bridge_width", "temple_length", "year_of_launch"],
        "fields": [
            {"name": "brand_name", "label": "Brand Name", "type": "select", "required": True},
            {"name": "subbrand", "label": "Sub Brand", "type": "text", "required": False},
            {"name": "model_name", "label": "Model Name", "type": "text", "required": True},
            {"name": "colour_code", "label": "Colour Code", "type": "text", "required": True},
            {"name": "lens_size", "label": "Lens Size (mm)", "type": "number", "required": False},
            {"name": "bridge_width", "label": "Bridge Width (mm)", "type": "number", "required": False},
            {"name": "temple_length", "label": "Temple Length (mm)", "type": "number", "required": False},
            {"name": "year_of_launch", "label": "Year of Launch", "type": "number", "required": False},
        ]
    },
    ProductCategory.SMART_WATCH: {
        "required": ["brand_name", "model_name", "colour_code"],
        "optional": ["subbrand", "body_colour", "belt_colour", "dial_size", "belt_size", "year_of_launch"],
        "fields": [
            {"name": "brand_name", "label": "Brand Name", "type": "select", "required": True},
            {"name": "subbrand", "label": "Sub Brand", "type": "text", "required": False},
            {"name": "model_name", "label": "Model Name", "type": "text", "required": True},
            {"name": "colour_code", "label": "Colour Code", "type": "text", "required": True},
            {"name": "body_colour", "label": "Body Colour", "type": "text", "required": False},
            {"name": "belt_colour", "label": "Belt Colour", "type": "text", "required": False},
            {"name": "dial_size", "label": "Dial Size (mm)", "type": "number", "required": False},
            {"name": "belt_size", "label": "Belt Size (mm)", "type": "number", "required": False},
            {"name": "year_of_launch", "label": "Year of Launch", "type": "number", "required": False},
        ]
    },
}


# ============================================================================
# SCHEMAS
# ============================================================================

class PricingInput(BaseModel):
    mrp: float = Field(..., gt=0)
    offer_price: Optional[float] = None
    cost_price: Optional[float] = None
    discount_category: str = "MASS"  # MASS, PREMIUM, LUXURY


class InventoryInput(BaseModel):
    initial_quantity: int = 0
    location_id: Optional[str] = None
    barcode: Optional[str] = None
    reorder_level: int = 5
    reorder_quantity: int = 10


class ShopifySyncInput(BaseModel):
    sync_to_shopify: bool = False
    shopify_product_type: Optional[str] = None
    shopify_tags: List[str] = []
    publish_to_online_store: bool = True
    publish_to_pos: bool = True


class SEOInput(BaseModel):
    page_title: Optional[str] = None
    meta_description: Optional[str] = None
    url_handle: Optional[str] = None


class ProductCreateInput(BaseModel):
    # Category
    category: ProductCategory

    # Category-specific attributes (dynamic based on category)
    attributes: Dict[str, Any]

    # Common fields
    description: Optional[str] = None
    hsn_code: Optional[str] = None
    gst_rate: float = 18.0
    weight: Optional[float] = None  # in grams

    # Pricing
    pricing: PricingInput

    # Inventory
    inventory: Optional[InventoryInput] = None

    # Media
    images: List[str] = []  # URLs

    # Shopify sync
    shopify: Optional[ShopifySyncInput] = None

    # SEO
    seo: Optional[SEOInput] = None


class ProductUpdateInput(BaseModel):
    attributes: Optional[Dict[str, Any]] = None
    description: Optional[str] = None
    hsn_code: Optional[str] = None
    gst_rate: Optional[float] = None
    weight: Optional[float] = None
    pricing: Optional[PricingInput] = None
    images: Optional[List[str]] = None
    is_active: Optional[bool] = None


# ============================================================================
# IN-MEMORY STORAGE
# ============================================================================

CATALOG_PRODUCTS: Dict[str, Dict] = {}
BRANDS: Dict[str, List[str]] = {
    "frames": ["Ray-Ban", "Oakley", "Vogue", "Prada", "Gucci", "Titan", "Fastrack", "Lenskart", "Vincent Chase", "John Jacobs"],
    "lenses": ["Essilor", "Zeiss", "Hoya", "Crizal", "Kodak", "Nikon", "Rodenstock"],
    "contact_lenses": ["Bausch & Lomb", "Johnson & Johnson", "Alcon", "CooperVision", "Acuvue"],
    "watches": ["Titan", "Fastrack", "Casio", "Fossil", "Timex", "Sonata", "HMT"],
    "hearing_aids": ["Phonak", "Signia", "Widex", "Oticon", "ReSound", "Starkey"],
    "accessories": ["Generic", "Ray-Ban", "Oakley", "Titan"],
}
SKU_COUNTERS: Dict[str, int] = {cat.value: 1000 for cat in ProductCategory}


def generate_sku(category: ProductCategory, attributes: Dict[str, Any]) -> str:
    """Generate unique SKU based on category and attributes"""
    prefix = category.value
    counter = SKU_COUNTERS.get(prefix, 1000)
    SKU_COUNTERS[prefix] = counter + 1

    # Add brand code
    brand = attributes.get("brand_name", "XX")[:2].upper()

    # Add model/colour for uniqueness
    model = attributes.get("model_no", attributes.get("model_name", ""))[:4].upper()
    colour = attributes.get("colour_code", attributes.get("colour_name", ""))[:3].upper()

    return f"{prefix}-{brand}-{model}{colour}-{counter}"


def generate_product_title(category: ProductCategory, attributes: Dict[str, Any]) -> str:
    """Generate product title from attributes"""
    brand = attributes.get("brand_name", "")
    subbrand = attributes.get("subbrand", "")
    model = attributes.get("model_no", attributes.get("model_name", ""))
    colour = attributes.get("colour_code", attributes.get("colour_name", ""))

    parts = [brand]
    if subbrand:
        parts.append(subbrand)
    if model:
        parts.append(model)
    if colour:
        parts.append(f"- {colour}")

    title = " ".join(parts)
    return f"{title} ({CATEGORY_NAMES.get(category, category.value)})"


# ============================================================================
# ENDPOINTS - Category Fields
# ============================================================================

@router.get("/categories")
async def list_categories(current_user: dict = Depends(get_current_user)):
    """List all product categories with their display names"""
    return {
        "categories": [
            {
                "code": cat.value,
                "name": CATEGORY_NAMES.get(cat, cat.value),
                "sku_prefix": cat.value
            }
            for cat in ProductCategory
        ]
    }


@router.get("/categories/{category}/fields")
async def get_category_fields(
    category: ProductCategory,
    current_user: dict = Depends(get_current_user)
):
    """Get the fields required for a specific category"""
    if category not in CATEGORY_FIELDS:
        raise HTTPException(status_code=404, detail="Category not found")

    return {
        "category": category.value,
        "category_name": CATEGORY_NAMES.get(category, category.value),
        "fields": CATEGORY_FIELDS[category]["fields"],
        "required_fields": CATEGORY_FIELDS[category]["required"],
        "optional_fields": CATEGORY_FIELDS[category]["optional"]
    }


@router.get("/brands")
async def get_brands(
    category: Optional[ProductCategory] = None,
    current_user: dict = Depends(get_current_user)
):
    """Get available brands, optionally filtered by category"""
    if category:
        # Map category to brand list
        brand_map = {
            ProductCategory.FRAME: "frames",
            ProductCategory.SUNGLASS: "frames",
            ProductCategory.READING_GLASSES: "frames",
            ProductCategory.SMART_FRAME: "frames",
            ProductCategory.SMART_SUNGLASS: "frames",
            ProductCategory.LENS: "lenses",
            ProductCategory.CONTACT_LENS: "contact_lenses",
            ProductCategory.WRIST_WATCH: "watches",
            ProductCategory.SMART_WATCH: "watches",
            ProductCategory.CLOCK: "watches",
            ProductCategory.HEARING_AID: "hearing_aids",
            ProductCategory.ACCESSORIES: "accessories",
        }
        brand_key = brand_map.get(category, "frames")
        return {"brands": BRANDS.get(brand_key, [])}

    # Return all brands
    all_brands = set()
    for brand_list in BRANDS.values():
        all_brands.update(brand_list)
    return {"brands": sorted(list(all_brands))}


# ============================================================================
# ENDPOINTS - Product CRUD
# ============================================================================

@router.get("/products")
async def list_catalog_products(
    category: Optional[ProductCategory] = None,
    brand: Optional[str] = None,
    search: Optional[str] = None,
    is_active: bool = True,
    limit: int = Query(default=50, le=250),
    page: int = 1,
    current_user: dict = Depends(get_current_user)
):
    """List all products in catalog"""
    products = list(CATALOG_PRODUCTS.values())

    # Apply filters
    if category:
        products = [p for p in products if p.get("category") == category.value]
    if brand:
        products = [p for p in products if p.get("attributes", {}).get("brand_name") == brand]
    if search:
        search_lower = search.lower()
        products = [
            p for p in products
            if search_lower in p.get("title", "").lower()
            or search_lower in p.get("sku", "").lower()
            or search_lower in str(p.get("attributes", {})).lower()
        ]
    if is_active is not None:
        products = [p for p in products if p.get("is_active") == is_active]

    # Sort by created date
    products.sort(key=lambda x: x.get("created_at", ""), reverse=True)

    total = len(products)
    start = (page - 1) * limit
    end = start + limit

    return {
        "products": products[start:end],
        "total": total,
        "page": page,
        "total_pages": (total + limit - 1) // limit
    }


@router.get("/products/{product_id}")
async def get_catalog_product(
    product_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Get a single product with all details"""
    product = CATALOG_PRODUCTS.get(product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    return {"product": product}


@router.post("/products")
async def create_catalog_product(
    product: ProductCreateInput,
    current_user: dict = Depends(get_current_user)
):
    """Create a new product in catalog"""
    if not any(role in current_user.get("roles", []) for role in ["SUPERADMIN", "ADMIN", "CATALOG_MANAGER"]):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    # Validate required fields for category
    category_config = CATEGORY_FIELDS.get(product.category)
    if not category_config:
        raise HTTPException(status_code=400, detail="Invalid category")

    for required_field in category_config["required"]:
        if required_field not in product.attributes or not product.attributes[required_field]:
            raise HTTPException(
                status_code=400,
                detail=f"Missing required field: {required_field}"
            )

    # Generate SKU and title
    product_id = f"prod_{uuid.uuid4().hex[:12]}"
    sku = generate_sku(product.category, product.attributes)
    title = generate_product_title(product.category, product.attributes)

    # Build product data
    product_data = {
        "id": product_id,
        "sku": sku,
        "title": title,
        "category": product.category.value,
        "category_name": CATEGORY_NAMES.get(product.category),
        "attributes": product.attributes,
        "description": product.description,
        "hsn_code": product.hsn_code,
        "gst_rate": product.gst_rate,
        "weight": product.weight,
        "pricing": {
            "mrp": product.pricing.mrp,
            "offer_price": product.pricing.offer_price or product.pricing.mrp,
            "cost_price": product.pricing.cost_price,
            "discount_category": product.pricing.discount_category,
        },
        "images": product.images,
        "inventory": {
            "total_quantity": product.inventory.initial_quantity if product.inventory else 0,
            "locations": {},
            "barcode": product.inventory.barcode if product.inventory else None,
            "reorder_level": product.inventory.reorder_level if product.inventory else 5,
            "reorder_quantity": product.inventory.reorder_quantity if product.inventory else 10,
        },
        "shopify": {
            "synced": False,
            "shopify_product_id": None,
            "last_sync": None,
        },
        "seo": {
            "page_title": product.seo.page_title if product.seo else title,
            "meta_description": product.seo.meta_description if product.seo else product.description,
            "url_handle": product.seo.url_handle if product.seo else sku.lower().replace(" ", "-"),
        },
        "is_active": True,
        "created_by": current_user.get("user_id"),
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
    }

    # Set inventory by location if provided
    if product.inventory and product.inventory.location_id:
        product_data["inventory"]["locations"][product.inventory.location_id] = product.inventory.initial_quantity

    CATALOG_PRODUCTS[product_id] = product_data

    # Sync to Shopify if requested
    shopify_result = None
    if product.shopify and product.shopify.sync_to_shopify:
        shopify_result = await _sync_product_to_shopify(product_data, product.shopify)
        product_data["shopify"] = shopify_result

    return {
        "product": product_data,
        "message": "Product created successfully",
        "shopify_sync": shopify_result
    }


@router.put("/products/{product_id}")
async def update_catalog_product(
    product_id: str,
    product: ProductUpdateInput,
    current_user: dict = Depends(get_current_user)
):
    """Update an existing product"""
    if not any(role in current_user.get("roles", []) for role in ["SUPERADMIN", "ADMIN", "CATALOG_MANAGER"]):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    existing = CATALOG_PRODUCTS.get(product_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Product not found")

    # Update fields
    if product.attributes:
        existing["attributes"].update(product.attributes)
        existing["title"] = generate_product_title(
            ProductCategory(existing["category"]),
            existing["attributes"]
        )

    if product.description is not None:
        existing["description"] = product.description
    if product.hsn_code is not None:
        existing["hsn_code"] = product.hsn_code
    if product.gst_rate is not None:
        existing["gst_rate"] = product.gst_rate
    if product.weight is not None:
        existing["weight"] = product.weight
    if product.pricing:
        existing["pricing"].update(product.pricing.model_dump(exclude_none=True))
    if product.images is not None:
        existing["images"] = product.images
    if product.is_active is not None:
        existing["is_active"] = product.is_active

    existing["updated_at"] = datetime.now().isoformat()

    return {"product": existing, "message": "Product updated successfully"}


@router.delete("/products/{product_id}")
async def delete_catalog_product(
    product_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Delete a product (soft delete)"""
    if not any(role in current_user.get("roles", []) for role in ["SUPERADMIN", "ADMIN"]):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    product = CATALOG_PRODUCTS.get(product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    product["is_active"] = False
    product["deleted_at"] = datetime.now().isoformat()
    product["deleted_by"] = current_user.get("user_id")

    return {"message": "Product deleted successfully"}


# ============================================================================
# ENDPOINTS - Inventory
# ============================================================================

@router.post("/products/{product_id}/inventory/adjust")
async def adjust_product_inventory(
    product_id: str,
    location_id: str,
    adjustment: int,  # Positive to add, negative to remove
    reason: Optional[str] = None,
    current_user: dict = Depends(get_current_user)
):
    """Adjust inventory for a product at a location"""
    if not any(role in current_user.get("roles", []) for role in ["SUPERADMIN", "ADMIN", "STORE_MANAGER", "WORKSHOP_STAFF"]):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    product = CATALOG_PRODUCTS.get(product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    current_qty = product["inventory"]["locations"].get(location_id, 0)
    new_qty = current_qty + adjustment

    if new_qty < 0:
        raise HTTPException(status_code=400, detail="Cannot have negative inventory")

    product["inventory"]["locations"][location_id] = new_qty
    product["inventory"]["total_quantity"] = sum(product["inventory"]["locations"].values())
    product["updated_at"] = datetime.now().isoformat()

    return {
        "product_id": product_id,
        "location_id": location_id,
        "previous_quantity": current_qty,
        "adjustment": adjustment,
        "new_quantity": new_qty,
        "total_quantity": product["inventory"]["total_quantity"]
    }


@router.get("/products/{product_id}/inventory")
async def get_product_inventory(
    product_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Get inventory levels for a product across all locations"""
    product = CATALOG_PRODUCTS.get(product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    return {
        "product_id": product_id,
        "sku": product["sku"],
        "title": product["title"],
        "total_quantity": product["inventory"]["total_quantity"],
        "locations": product["inventory"]["locations"],
        "reorder_level": product["inventory"]["reorder_level"],
        "needs_reorder": product["inventory"]["total_quantity"] <= product["inventory"]["reorder_level"]
    }


# ============================================================================
# ENDPOINTS - Shopify Sync
# ============================================================================

async def _sync_product_to_shopify(product: Dict, shopify_config: ShopifySyncInput) -> Dict:
    """Sync a product to Shopify (mock implementation)"""
    shopify_product_id = f"shopify_{uuid.uuid4().hex[:12]}"

    return {
        "synced": True,
        "shopify_product_id": shopify_product_id,
        "shopify_handle": product["seo"]["url_handle"],
        "last_sync": datetime.now().isoformat(),
        "published_channels": {
            "online_store": shopify_config.publish_to_online_store,
            "pos": shopify_config.publish_to_pos,
        }
    }


@router.post("/products/{product_id}/sync-shopify")
async def sync_product_to_shopify(
    product_id: str,
    sync_config: ShopifySyncInput,
    current_user: dict = Depends(get_current_user)
):
    """Sync a single product to Shopify"""
    if not any(role in current_user.get("roles", []) for role in ["SUPERADMIN", "ADMIN", "CATALOG_MANAGER"]):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    product = CATALOG_PRODUCTS.get(product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    result = await _sync_product_to_shopify(product, sync_config)
    product["shopify"] = result
    product["updated_at"] = datetime.now().isoformat()

    return {
        "product_id": product_id,
        "shopify_sync": result,
        "message": "Product synced to Shopify successfully"
    }


@router.post("/products/bulk-sync-shopify")
async def bulk_sync_products_to_shopify(
    product_ids: List[str],
    sync_config: ShopifySyncInput,
    current_user: dict = Depends(get_current_user)
):
    """Bulk sync multiple products to Shopify"""
    if not any(role in current_user.get("roles", []) for role in ["SUPERADMIN", "ADMIN"]):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    synced = 0
    errors = []

    for pid in product_ids:
        product = CATALOG_PRODUCTS.get(pid)
        if not product:
            errors.append({"product_id": pid, "error": "Not found"})
            continue

        result = await _sync_product_to_shopify(product, sync_config)
        product["shopify"] = result
        product["updated_at"] = datetime.now().isoformat()
        synced += 1

    return {
        "synced_count": synced,
        "errors": errors,
        "message": f"{synced} products synced to Shopify"
    }


# ============================================================================
# ENDPOINTS - Import/Export
# ============================================================================

@router.post("/products/import")
async def import_products(
    products: List[ProductCreateInput],
    current_user: dict = Depends(get_current_user)
):
    """Bulk import products"""
    if not any(role in current_user.get("roles", []) for role in ["SUPERADMIN", "ADMIN"]):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    created = 0
    errors = []

    for i, product in enumerate(products):
        try:
            # Validate and create
            category_config = CATEGORY_FIELDS.get(product.category)
            if not category_config:
                errors.append({"index": i, "error": "Invalid category"})
                continue

            product_id = f"prod_{uuid.uuid4().hex[:12]}"
            sku = generate_sku(product.category, product.attributes)
            title = generate_product_title(product.category, product.attributes)

            product_data = {
                "id": product_id,
                "sku": sku,
                "title": title,
                "category": product.category.value,
                "category_name": CATEGORY_NAMES.get(product.category),
                "attributes": product.attributes,
                "description": product.description,
                "hsn_code": product.hsn_code,
                "gst_rate": product.gst_rate,
                "weight": product.weight,
                "pricing": {
                    "mrp": product.pricing.mrp,
                    "offer_price": product.pricing.offer_price or product.pricing.mrp,
                    "cost_price": product.pricing.cost_price,
                    "discount_category": product.pricing.discount_category,
                },
                "images": product.images,
                "inventory": {"total_quantity": 0, "locations": {}, "reorder_level": 5},
                "shopify": {"synced": False},
                "seo": {},
                "is_active": True,
                "created_by": current_user.get("user_id"),
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
            }

            CATALOG_PRODUCTS[product_id] = product_data
            created += 1

        except Exception as e:
            errors.append({"index": i, "error": str(e)})

    return {
        "created_count": created,
        "errors": errors,
        "message": f"{created} products imported successfully"
    }


@router.get("/products/export")
async def export_products(
    category: Optional[ProductCategory] = None,
    format: str = "json",
    current_user: dict = Depends(get_current_user)
):
    """Export products"""
    if not any(role in current_user.get("roles", []) for role in ["SUPERADMIN", "ADMIN", "CATALOG_MANAGER"]):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    products = list(CATALOG_PRODUCTS.values())

    if category:
        products = [p for p in products if p.get("category") == category.value]

    return {
        "products": products,
        "total": len(products),
        "exported_at": datetime.now().isoformat()
    }
