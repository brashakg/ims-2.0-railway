"""
IMS 2.0 - Catalog Router
========================
Comprehensive product catalog management with category-specific fields.
Handles product creation, SKU generation, and Shopify sync.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from pydantic import BaseModel, Field, field_validator
from typing import Optional, Dict, Any, List, Union
from datetime import datetime
from enum import Enum
import logging
import uuid

logger = logging.getLogger(__name__)

from .auth import get_current_user, require_roles
from api.services.cost_mask import mask_cost, mask_cost_list
from ..services.online_catalog import (
    online_status_for_skus,
    online_summary,
    reconcile_store_barcodes,
    ecommerce_db_configured,
)
from ..services import stock_allocation
from ..services.pricing_caps import evaluate_offer_price, CATEGORY_DISCOUNT_CAPS
from ..services.gst_rates import gst_rate_for_category, hsn_for_category
from ..services import product_master as _pm
from .inventory import _on_hand_by_product

router = APIRouter()

# Canonical discount-cap tiers. Sourced from the SHARED pricing_caps cap table
# (the single source of truth also used by the bulk-price / POS cap logic) so
# the accepted set never drifts from what the resolver actually understands.
_VALID_DISCOUNT_CATEGORIES = frozenset(CATEGORY_DISCOUNT_CAPS.keys())
# NOTE: _get_db() is defined later in this module (reused here at call time).


# ============================================================================
# ONLINE CATALOG BRIDGE (IMS Mongo <-> e-commerce BVI Postgres)
# ============================================================================
# Lets IMS surface which products are "Online" (in Shopify, via the BVI
# Postgres in the same Railway project) + their online stock, matched by SKU.
# Fully fail-soft: if the e-commerce DB isn't configured/reachable, these
# return empty so IMS keeps working unchanged.


@router.get("/online-status")
async def get_online_status(
    skus: str = Query(..., description="Comma-separated SKUs to look up"),
    current_user: dict = Depends(get_current_user),
):
    """For each SKU, whether it's online (in Shopify) + its online stock.
    Returns {statuses: {sku: {online, online_stock, status}}}."""
    sku_list = [s.strip() for s in (skus or "").split(",") if s.strip()]
    return {"statuses": online_status_for_skus(sku_list)}


class OnlineStatusRequest(BaseModel):
    skus: List[str] = Field(default_factory=list)


@router.post("/online-status")
async def post_online_status(
    body: OnlineStatusRequest,
    current_user: dict = Depends(get_current_user),
):
    """POST variant of the SKU online-status lookup: the SKU list rides in the
    BODY, not the query string. The inventory page can carry thousands of SKUs,
    and a comma-joined `?skus=...` built a multi-KB URL that tripped server /
    proxy length limits -> net::ERR_CONNECTION_CLOSED, blanking the "Online"
    column (QA F12). Same response shape as the GET."""
    sku_list = [s.strip() for s in (body.skus or []) if s and s.strip()]
    return {"statuses": online_status_for_skus(sku_list)}


@router.get("/online-summary")
async def get_online_summary(current_user: dict = Depends(get_current_user)):
    """Diagnostic: is the e-commerce catalog DB configured/reachable + counts."""
    return online_summary()


class ReconcileBarcodesBody(BaseModel):
    # { sku: barcode } from the store's master spreadsheet.
    pairs: Dict[str, Union[str, int, float]] = Field(default_factory=dict)
    apply: bool = False  # dry-run unless explicitly applied
    only_empty: bool = True  # never overwrite an existing storeBarcode


@router.post("/reconcile-store-barcodes")
async def reconcile_store_barcodes_ep(
    body: ReconcileBarcodesBody,
    current_user: dict = Depends(require_roles("SUPERADMIN")),
):
    """One-time SUPERADMIN reconciliation: fill the e-commerce catalog's
    storeBarcode from a SKU->barcode map (the store master sheet). Dry-run by
    default; writes only the storeBarcode column, only where it's empty."""
    return reconcile_store_barcodes(
        body.pairs, apply=body.apply, only_empty=body.only_empty
    )


@router.get("/online-stock-reconcile")
async def online_stock_reconcile(
    store_id: Optional[str] = Query(
        None, description="Limit in-store on-hand to one store"
    ),
    safety_buffer: int = Query(
        0, ge=0, le=1000, description="Units to hold back from online"
    ),
    limit: int = Query(1000, ge=1, le=5000),
    current_user: dict = Depends(get_current_user),
):
    """Reconcile in-store physical on-hand (IMS) vs online-listed stock
    (BVI/Shopify) per SKU and flag overselling risk + a recommended safe online
    allocation (on-hand minus safety_buffer). Read-only + fail-soft: if the
    e-commerce DB isn't configured, every product shows 0 online stock."""
    db = _get_db()
    if db is None:
        return {
            "items": [],
            "summary": {},
            "online_configured": ecommerce_db_configured(),
        }
    try:
        products = list(
            _coll(db, "products")
            .find(
                {"sku": {"$nin": [None, ""]}, "is_active": {"$ne": False}},
                {"_id": 0, "product_id": 1, "sku": 1, "brand": 1, "model": 1},
            )
            .limit(limit)
        )
    except Exception:
        products = []

    pids = [p.get("product_id") for p in products if p.get("product_id")]
    on_hand = _on_hand_by_product(db, pids, store_id)
    skus = [p.get("sku") for p in products if p.get("sku")]
    online = online_status_for_skus(skus)  # {sku: {online, online_stock, status}}

    items = []
    for p in products:
        sku = p.get("sku")
        o = online.get(sku, {})
        items.append(
            {
                "sku": sku,
                "name": f"{p.get('brand', '') or ''} {p.get('model', '') or ''}".strip(),
                "in_store": on_hand.get(p.get("product_id"), 0),
                "online": int(o.get("online_stock") or 0),
                "is_online": bool(o.get("online")),
            }
        )

    result = stock_allocation.reconcile_items(items, safety_buffer=safety_buffer)
    result["online_configured"] = ecommerce_db_configured()
    return result


# ============================================================================
# PRODUCT CATEGORIES WITH SKU PREFIXES
# ============================================================================
#
# Unification step-8 note: the CANONICAL product-category taxonomy is owned by
# services/product_master (its _CATEGORY_SPECS registry). This short-code enum
# is the legacy /catalog door's accepted set; every value below is a registered
# SKU-prefix alias the registry resolves via product_master.resolve_category()
# (e.g. "SG" -> SUNGLASS, "CL" -> CONTACT_LENS, "LS" -> OPTICAL_LENS). It is
# deliberately LEFT IN PLACE (not repointed) because this enum is the /catalog
# door's accepted-category contract -- replacing it would change which categories
# the door accepts (a behaviour change).
# Step-9 (canonical product-create) repointed this door's VALIDATION to the
# registry via build_canonical_product; the per-category required fields below
# now AGREE with the registry (the two former divergences -- CONTACT_LENS and
# HEARING_AID -- were reconciled by owner sign-off). Only the nested
# catalog_products persistence stays here, pending the owner-gated step-10 spine.


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
#
# Unification step-8 FLAG -- DO NOT auto-reconcile, owner decision required.
# These per-category required-field sets are the /catalog door's contract and
# now AGREE byte-for-byte with the canonical registry
# (services/product_master._CATEGORY_SPECS) for every category. The two former
# divergences were reconciled by owner sign-off in step-9:
#
#   * CONTACT_LENS: requires {brand_name, model_name, power, expiry_date}
#       (both power AND expiry -- a contact lens is a powered medical device
#       with a shelf life).
#   * HEARING_AID:  requires {brand_name, model_no} only -- serial_no is NOT
#       required at catalogue (it is captured per-UNIT at stock-in).
#
# Step-9 routes this door's validation through build_canonical_product, so these
# sets and the registry cannot drift; a parity test locks them equal.
#
# Define which fields each category needs
CATEGORY_FIELDS = {
    ProductCategory.SUNGLASS: {
        "required": ["brand_name", "model_no", "colour_code"],
        "optional": ["subbrand", "lens_size", "bridge_width", "temple_length"],
        "fields": [
            {
                "name": "brand_name",
                "label": "Brand Name",
                "type": "select",
                "required": True,
            },
            {
                "name": "subbrand",
                "label": "Sub Brand",
                "type": "text",
                "required": False,
            },
            {"name": "model_no", "label": "Model No", "type": "text", "required": True},
            {
                "name": "colour_code",
                "label": "Colour Code",
                "type": "text",
                "required": True,
            },
            {
                "name": "lens_size",
                "label": "Lens Size (mm)",
                "type": "number",
                "required": False,
            },
            {
                "name": "bridge_width",
                "label": "Bridge Width (mm)",
                "type": "number",
                "required": False,
            },
            {
                "name": "temple_length",
                "label": "Temple Length (mm)",
                "type": "number",
                "required": False,
            },
        ],
    },
    ProductCategory.CONTACT_LENS: {
        # Step-9 owner-decided reconcile: a contact lens needs BOTH power AND
        # expiry_date -- now matches the canonical product_master registry.
        "required": ["brand_name", "model_name", "power", "expiry_date"],
        "optional": ["subbrand", "colour_name", "pack"],
        "fields": [
            {
                "name": "brand_name",
                "label": "Brand Name",
                "type": "select",
                "required": True,
            },
            {
                "name": "subbrand",
                "label": "Sub Brand",
                "type": "text",
                "required": False,
            },
            {
                "name": "model_name",
                "label": "Model Name",
                "type": "text",
                "required": True,
            },
            {
                "name": "colour_name",
                "label": "Colour Name",
                "type": "text",
                "required": False,
            },
            {
                "name": "power",
                "label": "Power",
                "type": "text",
                "required": True,
                "placeholder": "-6.00 to +6.00",
            },
            {
                "name": "pack",
                "label": "Pack Size",
                "type": "select",
                "required": False,
                "options": ["1", "3", "6", "30", "90"],
            },
            {
                "name": "expiry_date",
                "label": "Expiry Date",
                "type": "date",
                "required": True,
            },
        ],
    },
    ProductCategory.FRAME: {
        "required": ["brand_name", "model_no", "colour_code"],
        "optional": ["subbrand", "lens_size", "bridge_width", "temple_length"],
        "fields": [
            {
                "name": "brand_name",
                "label": "Brand Name",
                "type": "select",
                "required": True,
            },
            {
                "name": "subbrand",
                "label": "Sub Brand",
                "type": "text",
                "required": False,
            },
            {"name": "model_no", "label": "Model No", "type": "text", "required": True},
            {
                "name": "colour_code",
                "label": "Colour Code",
                "type": "text",
                "required": True,
            },
            {
                "name": "lens_size",
                "label": "Lens Size (mm)",
                "type": "number",
                "required": False,
            },
            {
                "name": "bridge_width",
                "label": "Bridge Width (mm)",
                "type": "number",
                "required": False,
            },
            {
                "name": "temple_length",
                "label": "Temple Length (mm)",
                "type": "number",
                "required": False,
            },
        ],
    },
    ProductCategory.ACCESSORIES: {
        "required": ["brand_name", "model_name"],
        "optional": ["subbrand", "size", "pack", "expiry_date", "accessory_type"],
        "fields": [
            {
                "name": "brand_name",
                "label": "Brand Name",
                "type": "select",
                "required": True,
            },
            {
                "name": "subbrand",
                "label": "Sub Brand",
                "type": "text",
                "required": False,
            },
            {
                "name": "model_name",
                "label": "Model Name",
                "type": "text",
                "required": True,
            },
            {
                "name": "accessory_type",
                "label": "Accessory Type",
                "type": "select",
                "required": False,
                "options": [
                    "Case",
                    "Cloth",
                    "Chain",
                    "Nose Pad",
                    "Temple Tip",
                    "Screw Kit",
                    "Spray",
                    "Other",
                ],
            },
            {"name": "size", "label": "Size", "type": "text", "required": False},
            {"name": "pack", "label": "Pack Size", "type": "number", "required": False},
            {
                "name": "expiry_date",
                "label": "Expiry Date",
                "type": "date",
                "required": False,
            },
        ],
    },
    ProductCategory.LENS: {
        "required": ["brand_name", "index", "coating"],
        "optional": ["subbrand", "lens_category", "add_on_1", "add_on_2", "add_on_3"],
        "fields": [
            {
                "name": "brand_name",
                "label": "Brand Name",
                "type": "select",
                "required": True,
            },
            {
                "name": "subbrand",
                "label": "Sub Brand",
                "type": "text",
                "required": False,
            },
            {
                "name": "index",
                "label": "Index",
                "type": "select",
                "required": True,
                "options": ["1.50", "1.56", "1.59", "1.60", "1.67", "1.74"],
            },
            {
                "name": "coating",
                "label": "Coating",
                "type": "select",
                "required": True,
                "options": [
                    "UC",
                    "HC",
                    "ARC",
                    "Blue Cut",
                    "Photochromic",
                    "Transitions",
                    "Polarized",
                ],
            },
            {
                "name": "lens_category",
                "label": "Lens Category",
                "type": "select",
                "required": False,
                "options": [
                    "Single Vision",
                    "Bifocal",
                    "Progressive",
                    "Office",
                    "Driving",
                ],
            },
            {
                "name": "add_on_1",
                "label": "Add-On 1",
                "type": "text",
                "required": False,
            },
            {
                "name": "add_on_2",
                "label": "Add-On 2",
                "type": "text",
                "required": False,
            },
            {
                "name": "add_on_3",
                "label": "Add-On 3",
                "type": "text",
                "required": False,
            },
        ],
    },
    ProductCategory.READING_GLASSES: {
        "required": ["brand_name", "model_no", "colour_code"],
        "optional": ["subbrand", "lens_size", "bridge_width", "temple_length", "power"],
        "fields": [
            {
                "name": "brand_name",
                "label": "Brand Name",
                "type": "select",
                "required": True,
            },
            {
                "name": "subbrand",
                "label": "Sub Brand",
                "type": "text",
                "required": False,
            },
            {"name": "model_no", "label": "Model No", "type": "text", "required": True},
            {
                "name": "colour_code",
                "label": "Colour Code",
                "type": "text",
                "required": True,
            },
            {
                "name": "power",
                "label": "Power",
                "type": "select",
                "required": False,
                "options": [
                    "+1.00",
                    "+1.25",
                    "+1.50",
                    "+1.75",
                    "+2.00",
                    "+2.25",
                    "+2.50",
                    "+2.75",
                    "+3.00",
                    "+3.50",
                ],
            },
            {
                "name": "lens_size",
                "label": "Lens Size (mm)",
                "type": "number",
                "required": False,
            },
            {
                "name": "bridge_width",
                "label": "Bridge Width (mm)",
                "type": "number",
                "required": False,
            },
            {
                "name": "temple_length",
                "label": "Temple Length (mm)",
                "type": "number",
                "required": False,
            },
        ],
    },
    ProductCategory.WRIST_WATCH: {
        "required": ["brand_name", "model_no", "colour_code"],
        "optional": [
            "subbrand",
            "dial_colour",
            "belt_colour",
            "dial_size",
            "belt_size",
            "watch_category",
        ],
        "fields": [
            {
                "name": "brand_name",
                "label": "Brand Name",
                "type": "select",
                "required": True,
            },
            {
                "name": "subbrand",
                "label": "Sub Brand",
                "type": "text",
                "required": False,
            },
            {"name": "model_no", "label": "Model No", "type": "text", "required": True},
            {
                "name": "colour_code",
                "label": "Colour Code",
                "type": "text",
                "required": True,
            },
            {
                "name": "dial_colour",
                "label": "Dial Colour",
                "type": "text",
                "required": False,
            },
            {
                "name": "belt_colour",
                "label": "Belt Colour",
                "type": "text",
                "required": False,
            },
            {
                "name": "dial_size",
                "label": "Dial Size (mm)",
                "type": "number",
                "required": False,
            },
            {
                "name": "belt_size",
                "label": "Belt Size (mm)",
                "type": "number",
                "required": False,
            },
            {
                "name": "watch_category",
                "label": "Watch Category",
                "type": "select",
                "required": False,
                "options": [
                    "Analog",
                    "Digital",
                    "Analog-Digital",
                    "Chronograph",
                    "Automatic",
                    "Quartz",
                ],
            },
        ],
    },
    ProductCategory.CLOCK: {
        "required": ["brand_name", "model_no", "colour_code"],
        "optional": [
            "subbrand",
            "dial_colour",
            "body_colour",
            "dial_size",
            "battery_size",
            "clock_category",
        ],
        "fields": [
            {
                "name": "brand_name",
                "label": "Brand Name",
                "type": "select",
                "required": True,
            },
            {
                "name": "subbrand",
                "label": "Sub Brand",
                "type": "text",
                "required": False,
            },
            {"name": "model_no", "label": "Model No", "type": "text", "required": True},
            {
                "name": "colour_code",
                "label": "Colour Code",
                "type": "text",
                "required": True,
            },
            {
                "name": "dial_colour",
                "label": "Dial Colour",
                "type": "text",
                "required": False,
            },
            {
                "name": "body_colour",
                "label": "Body Colour",
                "type": "text",
                "required": False,
            },
            {
                "name": "dial_size",
                "label": "Dial Size (inches)",
                "type": "number",
                "required": False,
            },
            {
                "name": "battery_size",
                "label": "Battery Size",
                "type": "text",
                "required": False,
            },
            {
                "name": "clock_category",
                "label": "Clock Category",
                "type": "select",
                "required": False,
                "options": [
                    "Wall Clock",
                    "Table Clock",
                    "Alarm Clock",
                    "Desk Clock",
                    "Decorative",
                ],
            },
        ],
    },
    ProductCategory.HEARING_AID: {
        "required": ["brand_name", "model_no"],
        "optional": ["subbrand", "serial_no", "machine_capacity", "machine_type"],
        "fields": [
            {
                "name": "brand_name",
                "label": "Brand Name",
                "type": "select",
                "required": True,
            },
            {
                "name": "subbrand",
                "label": "Sub Brand",
                "type": "text",
                "required": False,
            },
            {"name": "model_no", "label": "Model No", "type": "text", "required": True},
            {
                "name": "serial_no",
                "label": "Serial No",
                "type": "text",
                "required": False,
            },
            {
                "name": "machine_capacity",
                "label": "Machine Capacity",
                "type": "select",
                "required": False,
                "options": ["Mild", "Moderate", "Severe", "Profound"],
            },
            {
                "name": "machine_type",
                "label": "Machine Type",
                "type": "select",
                "required": False,
                "options": ["BTE", "ITE", "ITC", "CIC", "RIC", "Body Worn"],
            },
        ],
    },
    ProductCategory.SMART_SUNGLASS: {
        "required": ["brand_name", "model_name", "colour_code"],
        "optional": [
            "subbrand",
            "lens_size",
            "bridge_width",
            "temple_length",
            "year_of_launch",
        ],
        "fields": [
            {
                "name": "brand_name",
                "label": "Brand Name",
                "type": "select",
                "required": True,
            },
            {
                "name": "subbrand",
                "label": "Sub Brand",
                "type": "text",
                "required": False,
            },
            {
                "name": "model_name",
                "label": "Model Name",
                "type": "text",
                "required": True,
            },
            {
                "name": "colour_code",
                "label": "Colour Code",
                "type": "text",
                "required": True,
            },
            {
                "name": "lens_size",
                "label": "Lens Size (mm)",
                "type": "number",
                "required": False,
            },
            {
                "name": "bridge_width",
                "label": "Bridge Width (mm)",
                "type": "number",
                "required": False,
            },
            {
                "name": "temple_length",
                "label": "Temple Length (mm)",
                "type": "number",
                "required": False,
            },
            {
                "name": "year_of_launch",
                "label": "Year of Launch",
                "type": "number",
                "required": False,
            },
        ],
    },
    ProductCategory.SMART_FRAME: {
        "required": ["brand_name", "model_name", "colour_code"],
        "optional": [
            "subbrand",
            "lens_size",
            "bridge_width",
            "temple_length",
            "year_of_launch",
        ],
        "fields": [
            {
                "name": "brand_name",
                "label": "Brand Name",
                "type": "select",
                "required": True,
            },
            {
                "name": "subbrand",
                "label": "Sub Brand",
                "type": "text",
                "required": False,
            },
            {
                "name": "model_name",
                "label": "Model Name",
                "type": "text",
                "required": True,
            },
            {
                "name": "colour_code",
                "label": "Colour Code",
                "type": "text",
                "required": True,
            },
            {
                "name": "lens_size",
                "label": "Lens Size (mm)",
                "type": "number",
                "required": False,
            },
            {
                "name": "bridge_width",
                "label": "Bridge Width (mm)",
                "type": "number",
                "required": False,
            },
            {
                "name": "temple_length",
                "label": "Temple Length (mm)",
                "type": "number",
                "required": False,
            },
            {
                "name": "year_of_launch",
                "label": "Year of Launch",
                "type": "number",
                "required": False,
            },
        ],
    },
    ProductCategory.SMART_WATCH: {
        "required": ["brand_name", "model_name", "colour_code"],
        "optional": [
            "subbrand",
            "body_colour",
            "belt_colour",
            "dial_size",
            "belt_size",
            "year_of_launch",
        ],
        "fields": [
            {
                "name": "brand_name",
                "label": "Brand Name",
                "type": "select",
                "required": True,
            },
            {
                "name": "subbrand",
                "label": "Sub Brand",
                "type": "text",
                "required": False,
            },
            {
                "name": "model_name",
                "label": "Model Name",
                "type": "text",
                "required": True,
            },
            {
                "name": "colour_code",
                "label": "Colour Code",
                "type": "text",
                "required": True,
            },
            {
                "name": "body_colour",
                "label": "Body Colour",
                "type": "text",
                "required": False,
            },
            {
                "name": "belt_colour",
                "label": "Belt Colour",
                "type": "text",
                "required": False,
            },
            {
                "name": "dial_size",
                "label": "Dial Size (mm)",
                "type": "number",
                "required": False,
            },
            {
                "name": "belt_size",
                "label": "Belt Size (mm)",
                "type": "number",
                "required": False,
            },
            {
                "name": "year_of_launch",
                "label": "Year of Launch",
                "type": "number",
                "required": False,
            },
        ],
    },
}


# ============================================================================
# SCHEMAS
# ============================================================================


class PricingInput(BaseModel):
    mrp: float = Field(..., gt=0)
    # offer_price / cost_price must be POSITIVE when supplied (mirrors the
    # canonical products.ProductCreate `offer_price: Field(..., gt=0)`). They
    # stay Optional here (an omitted offer_price defaults to MRP), but a
    # non-positive value is rejected with 422 -- otherwise a negative offer
    # price slipped through (the MRP-rule guard only catches offer > MRP, and a
    # negative offer is `offer or mrp`-truthy so it was persisted verbatim).
    offer_price: Optional[float] = Field(default=None, gt=0)
    cost_price: Optional[float] = Field(default=None, gt=0)
    discount_category: str = (
        "MASS"  # MASS / PREMIUM / LUXURY / SERVICE / NON_DISCOUNTABLE
    )

    @field_validator("discount_category")
    @classmethod
    def _validate_discount_category(cls, v: str) -> str:
        """Reject an unrecognized discount-cap tier. The tier drives the
        discount cap in services/pricing_caps; an unknown / typo'd value
        silently degrades to the most-permissive MASS (15%) tier there, so a
        mistyped "NON_DISCOUNTABLE" would wrongly ALLOW a 15% discount. Pin it
        to the canonical set (the single source of truth -- the cap table keys)
        and normalize to upper-case so the persisted value matches what the cap
        resolver expects."""
        norm = (v or "").strip().upper()
        if norm not in _VALID_DISCOUNT_CATEGORIES:
            raise ValueError(
                "Invalid discount_category. Allowed: "
                f"{', '.join(sorted(_VALID_DISCOUNT_CATEGORIES))}."
            )
        return norm


class PricingPatchInput(BaseModel):
    """Diff-only pricing patch for PUT /catalog/products/{id}. The create-path
    PricingInput REQUIRES mrp and defaults discount_category to 'MASS', so
    reusing it on the update path made a partial pricing patch impossible
    (422 on the missing mrp) AND silently stamped MASS onto the merged block
    -- which flowed through the fail-soft spine mirror into the POS discount
    caps, widening a LUXURY item's 5% cap to MASS's 15%. Everything is
    Optional here: the exclude_none merge in the handler keeps only the fields
    the caller actually sent, and the MRP >= offer rule still runs on the
    EFFECTIVE post-merge values."""

    mrp: Optional[float] = Field(default=None, gt=0)
    offer_price: Optional[float] = Field(default=None, gt=0)
    cost_price: Optional[float] = Field(default=None, gt=0)
    discount_category: Optional[str] = None

    @field_validator("discount_category")
    @classmethod
    def _validate_discount_category(cls, v: Optional[str]) -> Optional[str]:
        """Same canonical-tier gate as the create path, but only when a value
        is actually provided (None = leave the stored tier alone)."""
        if v is None:
            return None
        return PricingInput._validate_discount_category(v)


class InventoryInput(BaseModel):
    initial_quantity: int = 0
    location_id: Optional[str] = None
    barcode: Optional[str] = None
    reorder_level: int = 5
    # Owner decision (2026-07-04): -1 means "no auto-reorder" -- every reorder
    # engine skips the product until a positive qty is explicitly configured
    # (see api/services/reorder_policy.py).
    reorder_quantity: int = -1


class ShopifySyncInput(BaseModel):
    sync_to_shopify: bool = False
    shopify_product_type: Optional[str] = None
    shopify_tags: List[str] = []
    # publish_to_online_store removed in Phase 6.12 — we don't run our
    # own storefront. Kept publish_to_pos for Shopify POS sync.
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
    # Optional so an omitted rate can be derived from HSN/category (mirrors
    # products.py ProductCreate). A hard-coded 18.0 default was a pricing
    # back-door: an optical frame (5% GST) would silently persist at 18%.
    gst_rate: Optional[float] = None
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
    # Catalog Manager review mini-form: re-categorise an imported doc (unmapped
    # BVI categories were dumped into ACCESSORIES + category_unmapped=True).
    # Canonicalised via product_master.resolve_category in the handler; when the
    # patch changes category and carries no explicit hsn_code/gst_rate, both are
    # re-derived from the new category (same helper the migration used).
    category: Optional[str] = None
    attributes: Optional[Dict[str, Any]] = None
    # Full-page review editor extensions (diff-only saves):
    #   name -- explicit operator name; applied AFTER the attributes block so
    #     it wins over the best-effort title regen for THAT save (sets both
    #     name and title; per-save only, no persistent flag).
    #   tags -- replaces the tag list (each trimmed, empties dropped).
    #   expected_updated_at -- ADDITIVE optimistic concurrency: when present
    #     and != the stored updated_at, the save 409s instead of clobbering a
    #     concurrent reviewer's fixes. Absent = today's last-write-wins (the
    #     existing drawer sends nothing and keeps working unchanged).
    name: Optional[str] = None
    tags: Optional[List[str]] = None
    description: Optional[str] = None
    hsn_code: Optional[str] = None
    gst_rate: Optional[float] = None
    weight: Optional[float] = None
    pricing: Optional[PricingPatchInput] = None
    images: Optional[List[str]] = None
    is_active: Optional[bool] = None
    expected_updated_at: Optional[str] = None


# ============================================================================
# IN-MEMORY STORAGE
# ============================================================================

CATALOG_PRODUCTS: Dict[str, Dict] = {}
BRANDS: Dict[str, List[str]] = {
    "frames": [
        "Ray-Ban",
        "Oakley",
        "Vogue",
        "Prada",
        "Gucci",
        "Titan",
        "Fastrack",
        "Lenskart",
        "Vincent Chase",
        "John Jacobs",
    ],
    "lenses": ["Essilor", "Zeiss", "Hoya", "Crizal", "Kodak", "Nikon", "Rodenstock"],
    "contact_lenses": [
        "Bausch & Lomb",
        "Johnson & Johnson",
        "Alcon",
        "CooperVision",
        "Acuvue",
    ],
    "watches": ["Titan", "Fastrack", "Casio", "Fossil", "Timex", "Sonata", "HMT"],
    "hearing_aids": ["Phonak", "Signia", "Widex", "Oticon", "ReSound", "Starkey"],
    "accessories": ["Generic", "Ray-Ban", "Oakley", "Titan"],
}
SKU_COUNTERS: Dict[str, int] = {cat.value: 1000 for cat in ProductCategory}


# ============================================================================
# PERSISTENCE  (MongoDB `catalog_products`, with in-memory fallback)
# ============================================================================
# These /catalog/products endpoints used the in-memory CATALOG_PRODUCTS dict
# above, which was lost on every restart. The frontend uses /admin/catalog/*
# and /products/* instead, so this surface had neither a consumer nor
# persistence. Now backed by the `catalog_products` collection, fail-soft to
# the in-memory dict when the DB is unavailable (local dev / tests).


def _get_db():
    try:
        from ..dependencies import get_db

        conn = get_db()
        if conn is not None and conn.is_connected:
            return conn.db
    except Exception:
        pass
    return None


def _coll(db, name: str):
    """Return a collection by name using subscript access.

    `_get_db()` returns the underlying database object: a real pymongo
    `Database` when Mongo is live, or the in-memory `MockDatabase` (seeded
    fallback) otherwise. A real `Database` supports BOTH `db[name]` and
    `db.get_collection(name)`, but `MockDatabase` only implements
    `__getitem__` -- so calling `.get_collection(...)` raised AttributeError
    and 500'd the catalog CRUD path whenever the seeded mock DB was active
    (a fresh deploy before Mongo connects, or local / test mock mode).
    Subscript access works on both, so use it everywhere here.
    """
    return db[name] if db is not None else None


def _catalog_coll():
    return _coll(_get_db(), "catalog_products")


def _save_catalog_product(product: Dict) -> None:
    coll = _catalog_coll()
    if coll is not None:
        # Explicit update-or-insert. The previous `update_one(..., upsert=True)`
        # works against real pymongo but BROKE on the seeded-mock fallback
        # (MockCollection.update_one has no `upsert` kwarg AND never inserts a
        # missing doc) -- so the catalog CRUD 500'd / silently lost writes
        # whenever Mongo wasn't connected. Checking existence first keeps the
        # 2-arg update_one / insert_one signatures that BOTH backends support,
        # and is functionally identical to an upsert on real Mongo.
        if coll.find_one({"id": product["id"]}) is not None:
            coll.update_one({"id": product["id"]}, {"$set": product})
        else:
            coll.insert_one(dict(product))
    else:
        # Mirror Mongo's $set semantics: merge into the stored doc so keys
        # ABSENT from the write survive (the PUT strips the promote-owned
        # flags from its write; a full replace here would drop them).
        stored = CATALOG_PRODUCTS.get(product["id"])
        if stored is not None:
            stored.update(product)
        else:
            CATALOG_PRODUCTS[product["id"]] = product


def _save_catalog_product_cas(product: Dict, expected_raw_updated_at) -> bool:
    """Compare-and-swap save for the optimistic-concurrency PUT path: the
    write only lands when the stored updated_at STILL equals the raw value
    read at load time (filtered on the raw datetime-or-string value so
    imported docs match). Returns False when another writer got in between --
    the caller 409s. The plain check-then-write left a multi-round-trip
    window where two overlapping saves both passed the timestamp check and
    the loser silently clobbered the winner's fixes."""
    coll = _catalog_coll()
    if coll is not None:
        res = coll.update_one(
            {"id": product["id"], "updated_at": expected_raw_updated_at},
            {"$set": product},
        )
        matched = getattr(res, "matched_count", None)
        if matched is None:
            # MockCollection's update_one result only carries modified_count.
            matched = getattr(res, "modified_count", 0)
        return bool(matched)
    stored = CATALOG_PRODUCTS.get(product["id"])
    if stored is None or stored.get("updated_at") != expected_raw_updated_at:
        return False
    stored.update(product)
    return True


def _get_catalog_product(product_id: str) -> Optional[Dict]:
    coll = _catalog_coll()
    if coll is not None:
        # No projection arg: MockCollection.find_one only accepts a filter, so
        # the prior `find_one({...}, {"_id": 0})` blew up in mock mode. Strip the
        # Mongo `_id` in Python instead (it isn't part of the catalog doc shape).
        doc = coll.find_one({"id": product_id})
        if doc is not None:
            doc.pop("_id", None)
        return doc
    return CATALOG_PRODUCTS.get(product_id)


def _all_catalog_products() -> List[Dict]:
    coll = _catalog_coll()
    if coll is not None:
        return list(coll.find({}, {"_id": 0}))
    return list(CATALOG_PRODUCTS.values())


def _next_sku_counter(prefix: str, db=None) -> int:
    """Next monotonic SKU counter for a category prefix.

    PERSISTENT + multi-worker safe when a DB is available: a single atomic
    find_one_and_update($inc) on the shared `counters` collection (same proven
    pattern as barcode.allocate_sequence / order_repository invoice serials),
    seeded at 1000 so the first issued value is 1001.

    The previous implementation used a MODULE-GLOBAL in-memory dict
    (SKU_COUNTERS), which (a) reset to 1000 on every server restart -> reissued
    already-used SKUs, and (b) was per-worker -> two Railway workers minted the
    SAME counter -> duplicate SKUs. Fail-soft: no DB / any error -> fall back to
    the in-memory dict (keeps local/test + offline create working).
    """
    if db is not None:
        try:
            from pymongo import ReturnDocument

            doc = db.get_collection("counters").find_one_and_update(
                {"_id": f"sku:{prefix}"},
                {"$inc": {"seq": 1}},
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
            if doc and isinstance(doc.get("seq"), int):
                # Seed so the series starts at 1001 (matches the legacy 1000 base).
                return 1000 + doc["seq"]
        except Exception:  # noqa: BLE001 - fail-soft, never block a create
            pass
    counter = SKU_COUNTERS.get(prefix, 1000)
    SKU_COUNTERS[prefix] = counter + 1
    return counter


def generate_sku(category: ProductCategory, attributes: Dict[str, Any], db=None) -> str:
    """Generate a unique SKU from category + attributes.

    Pass `db` so the numeric counter is allocated ATOMICALLY + PERSISTENTLY
    (see _next_sku_counter). The SKU still ends in a `find_by_sku` dedupe check
    at the call site, so even if two products share brand/model/colour the
    counter keeps them distinct.
    """
    prefix = category.value
    counter = _next_sku_counter(prefix, db=db)

    # Add brand code
    brand = attributes.get("brand_name", "XX")[:2].upper()

    # Add model/colour for uniqueness
    model = attributes.get("model_no", attributes.get("model_name", ""))[:4].upper()
    colour = attributes.get("colour_code", attributes.get("colour_name", ""))[
        :3
    ].upper()

    return f"{prefix}-{brand}-{model}{colour}-{counter}"


def generate_product_title(
    category: ProductCategory, attributes: Dict[str, Any]
) -> str:
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


@router.get("")
@router.get("/")
async def get_catalog_root():
    """Root endpoint for product catalog overview"""
    return {
        "module": "catalog",
        "status": "active",
        "message": "catalog overview endpoint ready",
    }


@router.get("/categories")
async def list_categories(current_user: dict = Depends(get_current_user)):
    """List all product categories with their display names"""
    return {
        "categories": [
            {
                "code": cat.value,
                "name": CATEGORY_NAMES.get(cat, cat.value),
                "sku_prefix": cat.value,
            }
            for cat in ProductCategory
        ]
    }


@router.get("/categories/{category}/fields")
async def get_category_fields(
    category: ProductCategory, current_user: dict = Depends(get_current_user)
):
    """Get the fields required for a specific category"""
    if category not in CATEGORY_FIELDS:
        raise HTTPException(status_code=404, detail="Category not found")

    return {
        "category": category.value,
        "category_name": CATEGORY_NAMES.get(category, category.value),
        "fields": CATEGORY_FIELDS[category]["fields"],
        "required_fields": CATEGORY_FIELDS[category]["required"],
        "optional_fields": CATEGORY_FIELDS[category]["optional"],
    }


@router.get("/brands")
async def get_brands(
    category: Optional[ProductCategory] = None,
    current_user: dict = Depends(get_current_user),
):
    """Get available brands, optionally filtered by category.

    The Brand Master (Settings -> Brand Master, `brand_masters`) is the
    source of truth: active brand names, filtered by the category's short
    prefix code when given (ProductCategory values ARE those codes). FAIL-
    OPEN to the legacy hardcoded BRANDS dict when the master is empty or
    unreadable so the Collections chip builder keeps working on a fresh /
    degraded deploy. Response shape unchanged: {"brands": [str, ...]}."""
    try:
        from ..dependencies import get_db as _get_db_dep
        from ..services import catalog_dictionary as _cd

        conn = _get_db_dep()
        if conn is not None and getattr(conn, "is_connected", False):
            prefix = category.value if category else None
            names = _cd.load_brand_options(conn, prefix)
            # None = read failed, [] = empty master -> both fall back.
            if names:
                return {"brands": names}
    except Exception as e:  # noqa: BLE001 - fail-open to the static dict
        logger.warning("[CATALOG] brand_masters read failed (fallback): %s", e)

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


def _catalog_sort_key(p: Dict) -> str:
    """Coalesce(created_at, migrated_at) as a sortable ISO string. The 4,393
    BVI-imported docs carry `migrated_at` (datetime) but no `created_at`, so a
    created_at-only sort sank them all to the bottom en masse."""
    v = p.get("created_at") or p.get("migrated_at") or ""
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return str(v)


@router.get("/products")
async def list_catalog_products(
    category: Optional[str] = None,
    brand: Optional[str] = None,
    search: Optional[str] = None,
    is_active: str = Query(
        default="true",
        pattern="^(true|false|all|True|False)$",
        description=(
            "Active filter. Default 'true' preserves the legacy behaviour "
            "(active only). 'all' surfaces inactive docs too -- the review "
            "queue needs it because BVI DRAFT/ARCHIVED imports carry "
            "is_active=False."
        ),
    ),
    needs_review: Optional[bool] = Query(
        default=None,
        description="Filter to imported docs awaiting review (Catalog Manager).",
    ),
    source: Optional[str] = Query(
        default=None, description="Filter by import source, e.g. 'bvi_import'."
    ),
    limit: int = Query(default=50, ge=1, le=250),
    page: int = Query(default=1, ge=1),
    current_user: dict = Depends(get_current_user),
):
    """List all products in catalog"""
    products = _all_catalog_products()

    # Apply filters
    if category:
        # Canonicalise BOTH sides: imported docs store the canonical long form
        # (FRAME/SUNGLASS), native docs store the short prefix code (FR/SG),
        # and the shared browse vocabulary sends canonical values. Fail-open to
        # a raw match when either side is unresolvable -- this can only ADD
        # matches for legacy callers sending short codes.
        want = _pm.resolve_category(category) or category
        products = [
            p
            for p in products
            if (_pm.resolve_category(p.get("category")) or p.get("category")) == want
        ]
    if brand:
        products = [
            p for p in products if p.get("attributes", {}).get("brand_name") == brand
        ]
    if search:
        search_lower = search.lower()
        products = [
            p
            for p in products
            if search_lower in p.get("title", "").lower()
            or search_lower in p.get("sku", "").lower()
            or search_lower in str(p.get("attributes", {})).lower()
        ]
    if needs_review is not None:
        products = [
            p for p in products if bool(p.get("needs_review", False)) == needs_review
        ]
    if source:
        products = [p for p in products if p.get("source") == source]
    # 'all' = no active filter; otherwise the legacy boolean equality match.
    if is_active != "all":
        active_bool = is_active in ("true", "True")
        products = [p for p in products if p.get("is_active") == active_bool]

    # Sort by created date (imported docs coalesce to migrated_at)
    products.sort(key=_catalog_sort_key, reverse=True)

    total = len(products)
    start = (page - 1) * limit
    end = start + limit

    # F35: strip cost/margin for roles that may not see it (CATALOG_MANAGER sees
    # cost only on the edit form, not this operational list -> default context).
    page_products = mask_cost_list(products[start:end], current_user)
    return {
        "products": page_products,
        "total": total,
        "page": page,
        "total_pages": (total + limit - 1) // limit,
    }


@router.get("/products/{product_id}")
async def get_catalog_product(
    product_id: str, current_user: dict = Depends(get_current_user)
):
    """Get a single product with all details"""
    product = _get_catalog_product(product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")

    # F35: product create/edit form -> CATALOG_MANAGER keeps cost (catalog_edit context).
    product = mask_cost(product, current_user, context="catalog_edit")
    return {"product": product}


def _guard_catalog_pricing(product: "ProductCreateInput") -> tuple:
    """Apply the two non-negotiable catalog pricing rules that the canonical
    POST /api/v1/products path already enforces, so /catalog/products is not an
    unguarded back-door (SYSTEM_INTENT section 3/10: offer_price > mrp -> BLOCK):

      1. Block offer_price > mrp (400), via the SHARED pricing_caps validator
         (services/pricing_caps.evaluate_offer_price) -- the same source of
         truth used by products.py and the bulk-price endpoints. Only the
         MRP_BELOW_OFFER verdict is raised here; discount-cap (CAP_EXCEEDED)
         stays a bulk/POS concern, matching products._assert_mrp_ge_offer.
      2. Derive gst_rate / hsn_code from the product category via the canonical
         services/gst_rates table when the client omits the rate (or omits the
         HSN), so the master rate equals what POS bills (a frame -> 5%, not the
         old hard-coded 18% default). An explicitly-supplied rate still wins.

    Returns the (gst_rate, hsn_code) to persist. The ProductCategory enum values
    are the short codes ("FR", "SG", ...) which are keys in GST_CATEGORY_TABLE.
    """
    mrp = product.pricing.mrp
    offer_price = product.pricing.offer_price
    if mrp is not None and offer_price is not None:
        verdict = evaluate_offer_price(mrp, offer_price)
        if verdict["reason"] == "MRP_BELOW_OFFER":
            raise HTTPException(status_code=400, detail="Offer price cannot exceed MRP")

    category_code = product.category.value
    if product.gst_rate is not None:
        gst_rate = product.gst_rate
    else:
        gst_rate = gst_rate_for_category(category_code)
    hsn_code = product.hsn_code or hsn_for_category(category_code)
    return gst_rate, hsn_code


@router.post("/products")
async def create_catalog_product(
    product: ProductCreateInput, current_user: dict = Depends(get_current_user)
):
    """Create a new product in catalog"""
    if not any(
        role in current_user.get("roles", [])
        for role in ["SUPERADMIN", "ADMIN", "CATALOG_MANAGER"]
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    # Validate the category is one this door accepts.
    category_config = CATEGORY_FIELDS.get(product.category)
    if not category_config:
        raise HTTPException(status_code=400, detail="Invalid category")

    # Non-negotiable pricing guards (block offer > MRP; derive GST from
    # HSN/category) -- same rules the canonical /products path enforces.
    gst_rate, hsn_code = _guard_catalog_pricing(product)

    # Generate SKU and title. Pass the DB so the SKU counter is allocated
    # atomically + persistently (not the per-worker in-memory dict).
    product_id = f"prod_{uuid.uuid4().hex[:12]}"
    sku = generate_sku(product.category, product.attributes, db=_get_db())
    title = generate_product_title(product.category, product.attributes)

    # PRODUCTS-CONVERGENCE step-10: validate through the canonical registry AND
    # persist a `products` SPINE row that SHARES this catalog id + sku, so a
    # catalog/PIM product is a first-class BILLABLE + discount-cap-enforced master
    # and POS -- which references the catalog id -- resolves straight to the
    # spine. Nothing can then be ordered off a catalog-only doc (the order-create
    # spine guard ③ fails loud otherwise). We insert the spine doc DIRECTLY
    # (build_canonical_product validates + builds it, no persistence) instead of
    # create_via_door, to avoid imposing the spine's identity duplicate-block on
    # the catalog/PIM door (which legitimately holds same-identity variants) and
    # to keep the native catalog_products doc (storefront/Shopify shape) below
    # unchanged.
    from ..dependencies import get_product_repository

    try:
        _spine = _pm.build_canonical_product(
            {
                "category": product.category.value,
                "attributes": dict(product.attributes or {}),
                "sku": sku,
                "mrp": product.pricing.mrp,
                "offer_price": product.pricing.offer_price or product.pricing.mrp,
                "cost_price": product.pricing.cost_price,
                "discount_category": product.pricing.discount_category,
                "hsn_code": hsn_code,
                "gst_rate": gst_rate,
                "created_by": current_user.get("user_id"),
            },
            source="CATALOG",
        )
    except _pm.ProductMasterError as err:
        # Preserve the catalog door's historical 400 "Missing required field"
        # contract for a missing-attr breach; surface other breaches verbatim.
        if err.status == 422 and err.field and err.field != "category":
            raise HTTPException(
                status_code=400, detail=f"Missing required field: {err.field}"
            ) from err
        raise HTTPException(status_code=err.status, detail=err.message) from err
    # Share the catalog id + sku so the catalog doc and the spine are ONE product.
    _spine["product_id"] = product_id
    _spine["id"] = product_id
    _spine["sku"] = sku
    # F6: persist the spine as a HARD gate on a GENUINE write failure. The products
    # spine is REQUIRED -- POS / PO / Buy Desk resolve the catalog id straight to
    # it, and the order-create spine guard (3) fails loud without it. Previously a
    # spine-write error was swallowed and the catalog doc saved anyway, leaving a
    # hidden catalog-only product that couldn't be billed until a sale surfaced the
    # gap. Now a REAL spine-write failure (a transient DB error -> create() returns
    # None, or a non-duplicate exception) aborts the create BEFORE the catalog doc
    # is saved -> 500.
    #
    # A DuplicateKeyError is DELIBERATELY tolerated: the catalog/PIM door
    # legitimately holds same-identity variants (see the build_canonical_product
    # note above -- the spine is inserted directly precisely to NOT impose the
    # identity duplicate-block here), and that identity already has a billable
    # spine. Failing it would break the by-design "accept the same product at the
    # form AND catalog doors" flow. When there is no DB (_pr is None) the catalog
    # save below also falls back to in-memory, so there is no orphan to guard.
    _pr = get_product_repository()
    if _pr is not None:
        try:
            _spine_created = _pr.create(_spine, raise_on_duplicate=True)
        except HTTPException:
            raise
        except Exception as err:  # noqa: BLE001
            if err.__class__.__name__ == "DuplicateKeyError":
                # Same-identity variant: the spine already exists (billable).
                # Tolerate + proceed to save the catalog/PIM doc, as before.
                logger.info(
                    "[CATALOG] spine already exists for the identity of %s (%s); "
                    "saving catalog variant",
                    product_id,
                    sku,
                )
                _spine_created = True
            else:
                logger.error(
                    "[CATALOG] spine write FAILED for %s (%s); aborting catalog create",
                    product_id,
                    sku,
                    exc_info=True,
                )
                raise HTTPException(
                    status_code=500,
                    detail=(
                        "Failed to create the product's billing record. The "
                        "product was not saved -- please retry."
                    ),
                ) from err
        if not _spine_created:
            # create() returns None on a swallowed (non-duplicate) DB error;
            # treat that as a hard failure too rather than saving an orphan.
            logger.error(
                "[CATALOG] spine write returned no doc for %s (%s); aborting create",
                product_id,
                sku,
            )
            raise HTTPException(
                status_code=500,
                detail=(
                    "Failed to create the product's billing record. The product "
                    "was not saved -- please retry."
                ),
            )

    # Build product data
    product_data = {
        "id": product_id,
        "sku": sku,
        "title": title,
        "category": product.category.value,
        "category_name": CATEGORY_NAMES.get(product.category),
        "attributes": product.attributes,
        "description": product.description,
        "hsn_code": hsn_code,
        "gst_rate": gst_rate,
        "weight": product.weight,
        "pricing": {
            "mrp": product.pricing.mrp,
            "offer_price": product.pricing.offer_price or product.pricing.mrp,
            "cost_price": product.pricing.cost_price,
            "discount_category": product.pricing.discount_category,
        },
        "images": product.images,
        "inventory": {
            "total_quantity": (
                product.inventory.initial_quantity if product.inventory else 0
            ),
            "locations": {},
            "barcode": product.inventory.barcode if product.inventory else None,
            "reorder_level": (
                product.inventory.reorder_level if product.inventory else 5
            ),
            # -1 = auto-reorder disabled (owner default; reorder_policy.py).
            "reorder_quantity": (
                product.inventory.reorder_quantity if product.inventory else -1
            ),
        },
        "shopify": {
            "synced": False,
            "shopify_product_id": None,
            "last_sync": None,
        },
        "seo": {
            "page_title": product.seo.page_title if product.seo else title,
            "meta_description": (
                product.seo.meta_description if product.seo else product.description
            ),
            "url_handle": (
                product.seo.url_handle if product.seo else sku.lower().replace(" ", "-")
            ),
        },
        "is_active": True,
        "created_by": current_user.get("user_id"),
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
    }

    # Set inventory by location if provided
    if product.inventory and product.inventory.location_id:
        product_data["inventory"]["locations"][
            product.inventory.location_id
        ] = product.inventory.initial_quantity

    _save_catalog_product(product_data)

    # Sync to Shopify if requested
    shopify_result = None
    if product.shopify and product.shopify.sync_to_shopify:
        shopify_result = await _sync_product_to_shopify(product_data, product.shopify)
        product_data["shopify"] = shopify_result

    return {
        "product": mask_cost(product_data, current_user, context="catalog_edit"),
        "message": "Product created successfully",
        "shopify_sync": shopify_result,
    }


@router.put("/products/{product_id}")
async def update_catalog_product(
    product_id: str,
    product: ProductUpdateInput,
    current_user: dict = Depends(get_current_user),
):
    """Update an existing product"""
    if not any(
        role in current_user.get("roles", [])
        for role in ["SUPERADMIN", "ADMIN", "CATALOG_MANAGER"]
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    existing = _get_catalog_product(product_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Product not found")
    # Work on a COPY: in no-DB mode _get_catalog_product returns the LIVE
    # in-memory dict, so mutating it mid-handler would leak partial edits into
    # the store on a later 4xx AND defeat the compare-and-swap write below
    # (the stored updated_at must keep its load-time value until the write
    # lands). Shallow is enough -- every field below is REPLACED, not mutated
    # in place.
    existing = dict(existing)

    # Additive optimistic concurrency (full-page review editor): the client
    # echoes the updated_at it loaded; a mismatch means another reviewer saved
    # in between, so 409 instead of silently clobbering their fixes. Compared
    # as ISO strings (imported docs may store a datetime -- isoformat it).
    # Callers that send nothing (the existing drawer) keep last-write-wins.
    # This early check is only a fast pre-check: the write itself is a
    # compare-and-swap on the RAW stored value (see below), so an overlapping
    # save in the check-to-write window still 409s instead of clobbering.
    raw_expected_ts = None
    if product.expected_updated_at is not None:
        raw_expected_ts = existing.get("updated_at")
        stored_ts = raw_expected_ts
        if isinstance(stored_ts, datetime):
            stored_ts = stored_ts.isoformat()
        if stored_ts != product.expected_updated_at:
            raise HTTPException(
                status_code=409,
                detail=(
                    "This item was changed by someone else - reload and "
                    "re-apply your fixes"
                ),
            )

    # Category change (Catalog Manager review mini-form). Canonicalised via the
    # shared registry resolver; unknown values are rejected loudly. When the
    # patch changes the category and does NOT explicitly send hsn_code/gst_rate,
    # both are re-derived from the NEW category via the same canonical table the
    # BVI migration used -- this un-does the ACCESSORIES dumping ground without
    # silently overriding an explicit HSN/GST the caller sent alongside.
    if product.category is not None:
        canonical = _pm.resolve_category(product.category)
        if canonical is None:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown product category '{product.category}'.",
            )
        category_changed = (
            _pm.resolve_category(existing.get("category")) or existing.get("category")
        ) != canonical
        existing["category"] = canonical
        if category_changed:
            existing["category_unmapped"] = False
            if product.hsn_code is None:
                existing["hsn_code"] = hsn_for_category(canonical)
            if product.gst_rate is None:
                existing["gst_rate"] = gst_rate_for_category(canonical)

    # Update fields
    if product.attributes:
        # Catalog Dictionary parity with the spine PUT (products.py): when the
        # owner configured allowed values for a field, an attributes patch must
        # match them (case-canonicalising). Fail-soft when no db.
        merged_attrs = {**(existing.get("attributes") or {}), **product.attributes}
        try:
            merged_attrs = _pm.enforce_dictionary_values(
                existing.get("category"), merged_attrs, db=_get_db()
            )
        except _pm.ProductMasterError as err:
            raise HTTPException(status_code=err.status, detail=err.message) from err
        existing["attributes"] = merged_attrs
        # Title regen is BEST-EFFORT: imported (BVI) docs store the canonical
        # long-form category ("FRAME"), which is not a ProductCategory short
        # code -- ProductCategory("FRAME") raises and previously 500'd any
        # attributes patch on an imported doc. Keep the existing title then.
        try:
            existing["title"] = generate_product_title(
                ProductCategory(existing["category"]), existing["attributes"]
            )
        except (ValueError, KeyError):
            pass
        # Courtesy mirror: the review-queue brand filter (and the promote
        # duplicate warnings) read the top-level brand/model, so a fixed
        # attributes.brand_name / model must be visible there immediately.
        # Keyed off the CALLER'S patch keys (not the merged set), so an
        # unrelated attribute fix (e.g. the drawer changing only colour) never
        # rewrites brand/model from stale attribute values it did not touch;
        # the mirrored VALUE still comes from the canonicalised merged set. An
        # explicit clear propagates as "" -- the full-doc $set cannot unset a
        # key, and consumers treat an empty top-level brand/model as absent.
        if "brand_name" in product.attributes:
            existing["brand"] = str(merged_attrs.get("brand_name") or "").strip()
        if "model_no" in product.attributes or "model_name" in product.attributes:
            existing["model"] = str(
                merged_attrs.get("model_no") or merged_attrs.get("model_name") or ""
            ).strip()

    # Explicit operator name wins over the best-effort title regen for THIS
    # save (hence applied AFTER the attributes block; per-save only -- the
    # regen above already no-ops for imported long-form categories).
    if product.name is not None:
        _name = product.name.strip()
        if _name:
            existing["name"] = _name
            existing["title"] = _name

    if product.tags is not None:
        existing["tags"] = [t.strip() for t in product.tags if t and t.strip()]

    if product.description is not None:
        existing["description"] = product.description
    if product.hsn_code is not None:
        existing["hsn_code"] = product.hsn_code
    if product.gst_rate is not None:
        existing["gst_rate"] = product.gst_rate
    if product.weight is not None:
        existing["weight"] = product.weight
    if product.pricing:
        # Merge the incoming pricing onto the existing block, then enforce the
        # non-negotiable MRP >= offer_price rule on the EFFECTIVE post-merge
        # values via the SHARED pricing_caps validator (same source of truth as
        # the create path + canonical /products). The create guard ran on a
        # fresh PricingInput, but the update path previously merged pricing with
        # NO re-validation -- so a partial pricing update (e.g. raising
        # offer_price above the existing MRP, or lowering MRP below the existing
        # offer_price) slipped a product into an MRP < offer state, exactly the
        # back-door SYSTEM_INTENT section 3/10 forbids. Mirrors the equivalent
        # fix in products.update_product.
        pricing_patch = product.pricing.model_dump(exclude_none=True)
        merged_pricing = {
            **(existing.get("pricing") or {}),
            **pricing_patch,
        }
        eff_mrp = merged_pricing.get("mrp")
        eff_offer = merged_pricing.get("offer_price")
        if "offer_price" in pricing_patch:
            # An offer-touching patch REQUIRES a usable effective MRP: price-
            # less imports (BVI fold_variant_prices leaves {mrp: 0.0}) made
            # evaluate_offer_price return INVALID_MRP, which the old
            # MRP_BELOW_OFFER-only check silently skipped -- persisting an
            # offer > MRP doc. Fall back to the top-level mrp mirror (the same
            # value the promote payload uses) before concluding it is absent;
            # an absent/zero/non-numeric MRP then rejects loudly.
            if eff_mrp is None:
                eff_mrp = existing.get("mrp")
            verdict = evaluate_offer_price(eff_mrp, eff_offer)
            if verdict["reason"] == "MRP_BELOW_OFFER":
                raise HTTPException(
                    status_code=400, detail="Offer price cannot exceed MRP"
                )
            if verdict["reason"] == "INVALID_MRP":
                raise HTTPException(
                    status_code=400,
                    detail="Set a valid MRP before or with the offer price",
                )
        elif eff_mrp is not None and eff_offer is not None:
            # Patches NOT touching offer_price stay lenient (metadata/cost
            # fixes on price-less imports must keep working): only the
            # non-negotiable MRP >= offer rule is enforced.
            verdict = evaluate_offer_price(eff_mrp, eff_offer)
            if verdict["reason"] == "MRP_BELOW_OFFER":
                raise HTTPException(
                    status_code=400, detail="Offer price cannot exceed MRP"
                )
        existing["pricing"] = merged_pricing
        # Imported (BVI) docs carry the price BOTH top-level (generic
        # consumers, incl. the promote payload) and nested under `pricing`
        # (orders' catalog fallback). Keep the two in sync on edit so a
        # review-form price fix is never shadowed by a stale top-level value.
        if "mrp" in existing and merged_pricing.get("mrp") is not None:
            existing["mrp"] = merged_pricing["mrp"]
        if "offer_price" in existing and merged_pricing.get("offer_price") is not None:
            existing["offer_price"] = merged_pricing["offer_price"]
    if product.images is not None:
        existing["images"] = product.images
    if product.is_active is not None:
        existing["is_active"] = product.is_active

    existing["updated_at"] = datetime.now().isoformat()
    # Per-user attribution (multiple staff catalogue in parallel): WHO made
    # this edit, for performance tracking and mistake tracing. Same fields
    # convention as the other routers' created_by_name; created_by is never
    # overwritten here.
    existing["updated_by"] = current_user.get("user_id")
    existing["updated_by_name"] = current_user.get("full_name") or current_user.get(
        "username"
    )

    # Promote is the ONLY writer of the review/promote flags (see the stamp in
    # promote_catalog_product): strip them from the WRITE (a copy -- the
    # response below still returns them as loaded) so a PUT whose snapshot was
    # loaded pre-promote can never resurrect needs_review=True over a
    # concurrent promote's stamp. $set leaves absent keys untouched, and the
    # in-memory fallback mirrors that merge semantics.
    to_write = dict(existing)
    for _flag in ("needs_review", "pos_ready", "promoted_at", "promoted_by"):
        to_write.pop(_flag, None)

    if product.expected_updated_at is not None:
        # Compare-and-swap: filter the write on the RAW stored updated_at
        # (datetime or string) captured at load. matched_count == 0 means
        # another writer landed inside the check-to-write window (which
        # includes live DB round-trips) -- 409 instead of clobbering.
        if not _save_catalog_product_cas(to_write, raw_expected_ts):
            raise HTTPException(
                status_code=409,
                detail=(
                    "This item was changed by someone else - reload and "
                    "re-apply your fixes"
                ),
            )
    else:
        _save_catalog_product(to_write)

    # Products-convergence: keep the billing SPINE in sync with this catalog edit
    # (the catalog id == the spine product_id). Propagate price / tier / gst /
    # active so POS bills the updated values. discount_category is upper-cased to
    # match what the cap resolver expects. Fail-soft: a spine-sync error never
    # breaks the catalog save.
    try:
        from ..dependencies import get_product_repository

        _pr = get_product_repository()
        if _pr is not None:
            _pricing = existing.get("pricing") or {}
            _tier = _pricing.get("discount_category")
            _patch = {
                "mrp": _pricing.get("mrp"),
                "offer_price": _pricing.get("offer_price"),
                "cost_price": _pricing.get("cost_price"),
                "discount_category": _tier.upper() if isinstance(_tier, str) else _tier,
                "hsn_code": existing.get("hsn_code"),
                "gst_rate": existing.get("gst_rate"),
                "is_active": existing.get("is_active", True),
            }
            _patch = {k: v for k, v in _patch.items() if v is not None}
            _pr.update(product_id, _patch)
    except Exception:  # noqa: BLE001
        logger.warning(
            "[CATALOG] spine sync on update skipped for %s", product_id, exc_info=True
        )

    return {
        "product": mask_cost(existing, current_user, context="catalog_edit"),
        "message": "Product updated successfully",
    }


# ============================================================================
# PROMOTE (Catalog Manager review queue -> POS-sellable spine row)
# ============================================================================
# The ONE thing that clears needs_review. The 4,393 BVI-imported docs live only
# in catalog_products (no `products` spine row), so POS order-create 400s them
# via convergence guard 3 (orders.py). "Re-save via the door" would mint a NEW
# product_id and strand the BVI CUID doc (catalog_variants.parent_product_id +
# ecom.* hang off that id) -- so approval is an IN-PLACE promotion: validate
# through the door's build_canonical_product (no validation fork), then insert
# a spine row PRESERVING the existing id (and sku when present).


def _promote_payload_from_doc(doc: Dict, actor: Optional[str]) -> Dict[str, Any]:
    """Map a catalog_products doc onto the canonical door payload. Pricing
    prefers the nested `pricing` block (the shape PUT /catalog/products
    edits) and falls back to the top-level mirror the BVI migration wrote."""
    pricing = doc.get("pricing") or {}
    mrp = pricing.get("mrp", doc.get("mrp"))
    offer = pricing.get("offer_price", doc.get("offer_price"))
    if offer in (None, 0):
        offer = mrp
    return {
        "category": doc.get("category"),
        "attributes": dict(doc.get("attributes") or {}),
        "sku": doc.get("sku") or None,
        "mrp": mrp,
        "offer_price": offer,
        "cost_price": pricing.get("cost_price"),
        "discount_category": pricing.get("discount_category"),
        "hsn_code": doc.get("hsn_code"),
        "gst_rate": doc.get("gst_rate"),
        "tags": doc.get("tags"),
        "created_by": actor,
    }


def _promote_extra_fields(doc: Dict) -> Dict[str, Any]:
    """Additive top-level spine columns carried over from the catalog doc
    (images/description/display name/weight). None values are dropped by the
    door core; canonical keys are never overridden."""
    return {
        "images": doc.get("images") or None,
        "description": doc.get("description") or None,
        "name": doc.get("name") or doc.get("title") or None,
        "weight": doc.get("weight"),
    }


def _gaps_from_pm_error(err: "_pm.ProductMasterError") -> List[Dict[str, Any]]:
    """Explode a door validation error into per-field checklist rows. The
    aggregated missing-required message names every gap in one string; split it
    so the FE renders one amber row per field. Other breaches stay one row."""
    msg = err.message or "Validation failed"
    prefix = "Cannot save product -- missing required: "
    if err.status == 422 and msg.startswith(prefix):
        names = [n.strip() for n in msg[len(prefix):].split(",") if n.strip()]
        if names:
            return [
                {
                    "field": name,
                    "label": _pm.field_label(name),
                    "message": f"{_pm.field_label(name)} is required",
                }
                for name in names
            ]
    return [
        {
            "field": err.field,
            "label": _pm.field_label(err.field) if err.field else None,
            "message": msg,
        }
    ]


def _promote_duplicate_warnings(repo, doc: Dict) -> List[Dict[str, Any]]:
    """SOFT duplicate signals against the spine (one $or query): an exact
    barcode match or a brand+model match. Warnings only -- the reviewer
    decides; the hard gates stay the id/sku 409s."""
    attrs = doc.get("attributes") or {}
    brand = str(attrs.get("brand_name") or doc.get("brand") or "").strip()
    model = str(attrs.get("model_no") or attrs.get("model_name") or "").strip()
    barcode = str(doc.get("barcode") or "").strip()
    or_terms: List[Dict[str, Any]] = []
    if barcode:
        or_terms.append({"barcode": barcode})
    if brand and model:
        or_terms.append({"brand": brand, "model": model})
    if not or_terms:
        return []
    warnings: List[Dict[str, Any]] = []
    try:
        for match in repo.find_many({"$or": or_terms}, limit=5):
            reason = (
                "same barcode"
                if barcode and match.get("barcode") == barcode
                else "same brand + model"
            )
            warnings.append(
                {
                    "product_id": match.get("product_id") or match.get("id"),
                    "sku": match.get("sku"),
                    "brand": match.get("brand"),
                    "model": match.get("model"),
                    "name": match.get("name"),
                    "reason": reason,
                }
            )
    except Exception:  # noqa: BLE001 - warnings are best-effort, never block
        logger.warning("[CATALOG] promote duplicate-warning query failed", exc_info=True)
    return warnings


@router.post("/products/{product_id}/promote")
async def promote_catalog_product(
    product_id: str,
    dry_run: bool = Query(
        default=False,
        description=(
            "true = validate only: returns {ok, gaps, duplicate_warnings} "
            "with ZERO writes. false = insert the spine row and clear "
            "needs_review."
        ),
    ),
    current_user: dict = Depends(require_roles("ADMIN", "CATALOG_MANAGER")),
):
    """Approve an imported catalog product for POS: validate it through the
    canonical product door and insert a `products` spine row that PRESERVES
    this catalog id (and sku when present), then stamp the catalog doc
    needs_review=false / pos_ready=true. The door's gates (required
    attributes, MRP>=offer, HSN/GST, dictionary values) are THE approval
    gates -- there is no separate validation fork, so bulk approval can never
    force an invalid product into POS."""
    from ..dependencies import get_product_repository

    doc = _get_catalog_product(product_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Product not found")

    repo = get_product_repository()
    if repo is None:
        raise HTTPException(
            status_code=503,
            detail="Product database is unavailable -- cannot promote right now.",
        )

    # HARD collision gates (id, then sku) -- plain-English, naming the row.
    existing_by_id = repo.find_by_id(product_id)
    if existing_by_id is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"'{doc.get('name') or doc.get('title') or product_id}' is already "
                f"a billing product (SKU {existing_by_id.get('sku') or 'unknown'}). "
                "It does not need approval again."
            ),
        )
    doc_sku = str(doc.get("sku") or "").strip()
    if doc_sku:
        existing_by_sku = repo.find_by_sku(doc_sku)
        if existing_by_sku is not None:
            _clash_name = " ".join(
                x
                for x in (existing_by_sku.get("brand"), existing_by_sku.get("model"))
                if x
            ) or existing_by_sku.get("name") or existing_by_sku.get("product_id")
            raise HTTPException(
                status_code=409,
                detail=(
                    f"SKU {doc_sku} already belongs to '{_clash_name}' in the "
                    "product master. Change this imported item's SKU (or retire "
                    "the duplicate) before approving it."
                ),
            )

    payload = _promote_payload_from_doc(doc, current_user.get("user_id"))
    if dry_run and not payload.get("sku"):
        # ZERO-writes contract: minting the real SKU would $inc the shared
        # counters collection. Validate with a placeholder instead; the real
        # promote mints the canonical SKU.
        payload["sku"] = "DRYRUN-PLACEHOLDER"

    db = _get_db()
    try:
        spine = _pm.build_canonical_product(
            payload,
            source="CATALOG",
            extra_fields=_promote_extra_fields(doc),
            product_repo=repo,
            db=db,
        )
    except _pm.ProductMasterError as err:
        if dry_run:
            return {
                "ok": False,
                "gaps": _gaps_from_pm_error(err),
                "duplicate_warnings": _promote_duplicate_warnings(repo, doc),
            }
        # Same shape as the create door's validation failures (message string).
        raise HTTPException(status_code=err.status, detail=err.message) from err

    if dry_run:
        return {
            "ok": True,
            "gaps": [],
            "duplicate_warnings": _promote_duplicate_warnings(repo, doc),
        }

    # PRESERVE the catalog identity: the spine row shares this doc's id (BVI
    # CUID -- catalog_variants.parent_product_id + ecom.* hang off it; the
    # orders $or resolver is format-agnostic). The door-minted SKU is kept only
    # when the doc had none, and is written back to the doc below.
    spine["product_id"] = product_id
    spine["id"] = product_id
    minted_sku = None if doc_sku else spine.get("sku")

    try:
        created = repo.create(spine, raise_on_duplicate=True)
    except HTTPException:
        raise
    except Exception as err:  # noqa: BLE001
        if err.__class__.__name__ == "DuplicateKeyError":
            raise HTTPException(
                status_code=409,
                detail=(
                    "A billing product with this id or SKU appeared while "
                    "approving -- refresh the review queue and try again."
                ),
            ) from err
        logger.error(
            "[CATALOG] promote spine write FAILED for %s", product_id, exc_info=True
        )
        raise HTTPException(
            status_code=500,
            detail=(
                "Failed to create the product's billing record. Nothing was "
                "approved -- please retry."
            ),
        ) from err
    if not created:
        logger.error(
            "[CATALOG] promote spine write returned no doc for %s", product_id
        )
        raise HTTPException(
            status_code=500,
            detail=(
                "Failed to create the product's billing record. Nothing was "
                "approved -- please retry."
            ),
        )

    # Stamp the catalog doc (partial $set -- promote is the ONLY writer of
    # these flags). Fail-soft: the spine row is the sellability truth; a
    # failed stamp leaves a stale queue entry, never an unsellable product.
    stamp: Dict[str, Any] = {
        "needs_review": False,
        "pos_ready": True,
        "promoted_at": datetime.now().isoformat(),
        "promoted_by": current_user.get("user_id"),
        "updated_at": datetime.now().isoformat(),
    }
    if minted_sku:
        stamp["sku"] = minted_sku
    try:
        coll = _catalog_coll()
        if coll is not None:
            coll.update_one({"id": product_id}, {"$set": stamp})
        else:
            CATALOG_PRODUCTS.setdefault(product_id, doc).update(stamp)
    except Exception:  # noqa: BLE001
        logger.warning(
            "[CATALOG] promote flag stamp failed for %s", product_id, exc_info=True
        )

    # Activity log (immutable audit chain). Fail-soft.
    try:
        from ..dependencies import get_audit_repository

        audit_repo = get_audit_repository()
        if audit_repo is not None:
            audit_repo.create(
                {
                    "action": "catalog_product.promoted",
                    "actor": current_user.get("user_id"),
                    "user_id": current_user.get("user_id"),
                    "entity_type": "product",
                    "entity_id": product_id,
                    "timestamp": datetime.now(),
                    "ts": datetime.now().isoformat(),
                    "after": {
                        "product_id": product_id,
                        "sku": spine.get("sku"),
                        "category": spine.get("category"),
                        "mrp": spine.get("mrp"),
                        "offer_price": spine.get("offer_price"),
                        "source": doc.get("source"),
                    },
                }
            )
    except Exception:  # noqa: BLE001
        logger.warning(
            "[CATALOG] promote audit write failed for %s", product_id, exc_info=True
        )

    # Bust the TTL_MEDIUM GET /products list cache so the newly sellable item
    # is immediately searchable at POS (same pattern as the bulk price ops).
    try:
        from ..services.cache import cache

        cache.delete_pattern("products:*")
    except Exception:  # noqa: BLE001
        pass

    return {
        "message": "Product approved for POS",
        "product_id": product_id,
        "sku": spine.get("sku"),
        "needs_review": False,
        "pos_ready": True,
    }


@router.delete("/products/{product_id}")
async def delete_catalog_product(
    product_id: str, current_user: dict = Depends(get_current_user)
):
    """Delete a product (soft delete)"""
    if not any(
        role in current_user.get("roles", []) for role in ["SUPERADMIN", "ADMIN"]
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    product = _get_catalog_product(product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")

    product["is_active"] = False
    product["deleted_at"] = datetime.now().isoformat()
    product["deleted_by"] = current_user.get("user_id")

    _save_catalog_product(product)

    # Products-convergence: deactivate the SPINE twin too (shared id) so a
    # soft-deleted catalog product can't still be sold at POS. Fail-soft.
    try:
        from ..dependencies import get_product_repository

        _pr = get_product_repository()
        if _pr is not None:
            _pr.update(product_id, {"is_active": False})
    except Exception:  # noqa: BLE001
        logger.warning(
            "[CATALOG] spine deactivate on delete skipped for %s",
            product_id,
            exc_info=True,
        )

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
    current_user: dict = Depends(get_current_user),
):
    """Adjust inventory for a product at a location"""
    if not any(
        role in current_user.get("roles", [])
        for role in ["SUPERADMIN", "ADMIN", "STORE_MANAGER", "WORKSHOP_STAFF"]
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    product = _get_catalog_product(product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")

    current_qty = product["inventory"]["locations"].get(location_id, 0)
    new_qty = current_qty + adjustment

    if new_qty < 0:
        raise HTTPException(status_code=400, detail="Cannot have negative inventory")

    product["inventory"]["locations"][location_id] = new_qty
    product["inventory"]["total_quantity"] = sum(
        product["inventory"]["locations"].values()
    )
    product["updated_at"] = datetime.now().isoformat()

    _save_catalog_product(product)
    return {
        "product_id": product_id,
        "location_id": location_id,
        "previous_quantity": current_qty,
        "adjustment": adjustment,
        "new_quantity": new_qty,
        "total_quantity": product["inventory"]["total_quantity"],
    }


@router.get("/products/{product_id}/inventory")
async def get_product_inventory(
    product_id: str, current_user: dict = Depends(get_current_user)
):
    """Get inventory levels for a product across all locations"""
    product = _get_catalog_product(product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")

    return {
        "product_id": product_id,
        "sku": product["sku"],
        "title": product["title"],
        "total_quantity": product["inventory"]["total_quantity"],
        "locations": product["inventory"]["locations"],
        "reorder_level": product["inventory"]["reorder_level"],
        "needs_reorder": product["inventory"]["total_quantity"]
        <= product["inventory"]["reorder_level"],
    }


# ============================================================================
# ENDPOINTS - Shopify Sync
# ============================================================================


async def _sync_product_to_shopify(
    product: Dict, shopify_config: ShopifySyncInput
) -> Dict:
    """RETIRED. The Shopify catalog is now owned solely by the e-commerce app
    (BVI) -- IMS no longer pushes products to Shopify (this was a mock that
    minted fake Shopify ids anyway). Manage online listings in the Online Store
    admin. Returns a 'retired' marker instead of faking a sync."""
    _ = shopify_config  # retained for signature compat; no longer used
    return {
        "synced": False,
        "retired": True,
        "owner": "ecommerce_app_bvi",
        "message": "Shopify is managed by the Online Store app. Add or edit this "
        "product's online listing there.",
        "last_sync": None,
    }


@router.post("/products/{product_id}/sync-shopify")
async def sync_product_to_shopify(
    product_id: str,
    sync_config: ShopifySyncInput,
    current_user: dict = Depends(get_current_user),
):
    """Sync a single product to Shopify"""
    if not any(
        role in current_user.get("roles", [])
        for role in ["SUPERADMIN", "ADMIN", "CATALOG_MANAGER"]
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    product = _get_catalog_product(product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")

    result = await _sync_product_to_shopify(product, sync_config)
    product["shopify"] = result
    product["updated_at"] = datetime.now().isoformat()
    _save_catalog_product(product)

    return {
        "product_id": product_id,
        "shopify_sync": result,
        "retired": True,
        "message": "Shopify is managed by the Online Store app (BVI). "
        "IMS product->Shopify sync is retired; manage the listing there.",
    }


@router.post("/products/bulk-sync-shopify")
async def bulk_sync_products_to_shopify(
    product_ids: List[str],
    sync_config: ShopifySyncInput,
    current_user: dict = Depends(get_current_user),
):
    """Bulk sync multiple products to Shopify"""
    if not any(
        role in current_user.get("roles", []) for role in ["SUPERADMIN", "ADMIN"]
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    synced = 0
    errors = []

    for pid in product_ids:
        product = _get_catalog_product(pid)
        if product is None:
            errors.append({"product_id": pid, "error": "Not found"})
            continue

        result = await _sync_product_to_shopify(product, sync_config)
        product["shopify"] = result
        product["updated_at"] = datetime.now().isoformat()
        _save_catalog_product(product)
        synced += 1

    return {
        "synced_count": synced,
        "errors": errors,
        "retired": True,
        "message": "Shopify is managed by the Online Store app (BVI). "
        "IMS product->Shopify sync is retired; manage listings there.",
    }


# ============================================================================
# ENDPOINTS - Import/Export
# ============================================================================


@router.post("/products/import")
async def import_products(
    products: List[ProductCreateInput], current_user: dict = Depends(get_current_user)
):
    """Bulk import products"""
    if not any(
        role in current_user.get("roles", []) for role in ["SUPERADMIN", "ADMIN"]
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    created = 0
    errors = []
    # Resolve the DB once so each row's SKU counter is allocated atomically +
    # persistently (the per-worker in-memory dict would collide under concurrency).
    _bulk_db = _get_db()

    for i, product in enumerate(products):
        try:
            # Validate and create
            category_config = CATEGORY_FIELDS.get(product.category)
            if not category_config:
                errors.append({"index": i, "error": "Invalid category"})
                continue

            # Step-9: enforce the SAME registry required-field rulebook as the
            # single /catalog create. A row missing a required field is recorded
            # + skipped (per-row), never a batch abort.
            try:
                _pm.build_canonical_product(
                    {
                        "category": product.category.value,
                        "attributes": dict(product.attributes or {}),
                        "mrp": product.pricing.mrp,
                        "offer_price": product.pricing.offer_price
                        or product.pricing.mrp,
                        "discount_category": product.pricing.discount_category,
                        "hsn_code": product.hsn_code,
                        "gst_rate": product.gst_rate,
                    },
                    source="CATALOG",
                )
            except _pm.ProductMasterError as req_exc:
                detail = (
                    f"Missing required field: {req_exc.field}"
                    if req_exc.status == 422
                    and req_exc.field
                    and req_exc.field != "category"
                    else req_exc.message
                )
                errors.append({"index": i, "error": detail})
                continue

            # Same pricing guards as the single-create path: block offer > MRP
            # and derive GST from HSN/category. A bad row is recorded + skipped
            # (the 400 raised by the guard is reported per-row, not as a batch
            # abort) so one bad row never poisons the whole import.
            try:
                gst_rate, hsn_code = _guard_catalog_pricing(product)
            except HTTPException as guard_exc:
                errors.append({"index": i, "error": guard_exc.detail})
                continue

            product_id = f"prod_{uuid.uuid4().hex[:12]}"
            sku = generate_sku(product.category, product.attributes, db=_bulk_db)
            title = generate_product_title(product.category, product.attributes)

            product_data = {
                "id": product_id,
                "sku": sku,
                "title": title,
                "category": product.category.value,
                "category_name": CATEGORY_NAMES.get(product.category),
                "attributes": product.attributes,
                "description": product.description,
                "hsn_code": hsn_code,
                "gst_rate": gst_rate,
                "weight": product.weight,
                "pricing": {
                    "mrp": product.pricing.mrp,
                    "offer_price": product.pricing.offer_price or product.pricing.mrp,
                    "cost_price": product.pricing.cost_price,
                    "discount_category": product.pricing.discount_category,
                },
                "images": product.images,
                # reorder_quantity -1 = auto-reorder disabled (owner default).
                "inventory": {
                    "total_quantity": 0,
                    "locations": {},
                    "reorder_level": 5,
                    "reorder_quantity": -1,
                },
                "shopify": {"synced": False},
                "seo": {},
                "is_active": True,
                "created_by": current_user.get("user_id"),
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
            }

            _save_catalog_product(product_data)
            created += 1

        except Exception as e:
            errors.append({"index": i, "error": str(e)})

    return {
        "created_count": created,
        "errors": errors,
        "message": f"{created} products imported successfully",
    }


@router.get("/products/export")
async def export_products(
    category: Optional[ProductCategory] = None,
    format: str = "json",
    current_user: dict = Depends(get_current_user),
):
    """Export products"""
    if not any(
        role in current_user.get("roles", [])
        for role in ["SUPERADMIN", "ADMIN", "CATALOG_MANAGER"]
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    products = _all_catalog_products()

    if category:
        products = [p for p in products if p.get("category") == category.value]

    return {
        "products": products,
        "total": len(products),
        "exported_at": datetime.now().isoformat(),
    }
