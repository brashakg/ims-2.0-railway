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
        CATALOG_PRODUCTS[product["id"]] = product


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
    limit: int = Query(default=50, ge=1, le=250),
    page: int = Query(default=1, ge=1),
    current_user: dict = Depends(get_current_user),
):
    """List all products in catalog"""
    products = _all_catalog_products()

    # Apply filters
    if category:
        products = [p for p in products if p.get("category") == category.value]
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
    if is_active is not None:
        products = [p for p in products if p.get("is_active") == is_active]

    # Sort by created date
    products.sort(key=lambda x: x.get("created_at", ""), reverse=True)

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
    # F6: persist the spine as a HARD gate. The products spine is REQUIRED -- POS
    # / PO / Buy Desk resolve the catalog id straight to it, and the order-create
    # spine guard (3) fails loud without it. Previously a spine-write error was
    # swallowed and the catalog doc saved anyway, leaving a hidden catalog-only
    # product that couldn't be billed until a sale surfaced the gap. Now a
    # spine-write failure aborts the create BEFORE the catalog doc is saved:
    # a duplicate identity/SKU -> 409, any other failure (incl. a swallowed None
    # return) -> 500. When there is no DB (_pr is None) the catalog save below
    # also falls back to in-memory, so there is no orphan to guard against.
    _pr = get_product_repository()
    if _pr is not None:
        try:
            _spine_created = _pr.create(_spine, raise_on_duplicate=True)
        except HTTPException:
            raise
        except Exception as err:  # noqa: BLE001
            if err.__class__.__name__ == "DuplicateKeyError":
                logger.warning("[CATALOG] spine duplicate for %s (%s)", product_id, sku)
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "A product with the same identity or SKU already exists. "
                        "The catalog product was not created."
                    ),
                ) from err
            logger.error(
                "[CATALOG] spine write FAILED for %s (%s); aborting catalog create",
                product_id,
                sku,
                exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail=(
                    "Failed to create the product's billing record. The product "
                    "was not saved -- please retry."
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
            "reorder_quantity": (
                product.inventory.reorder_quantity if product.inventory else 10
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

    # Update fields
    if product.attributes:
        existing["attributes"].update(product.attributes)
        existing["title"] = generate_product_title(
            ProductCategory(existing["category"]), existing["attributes"]
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
        merged_pricing = {
            **(existing.get("pricing") or {}),
            **product.pricing.model_dump(exclude_none=True),
        }
        eff_mrp = merged_pricing.get("mrp")
        eff_offer = merged_pricing.get("offer_price")
        if eff_mrp is not None and eff_offer is not None:
            verdict = evaluate_offer_price(eff_mrp, eff_offer)
            if verdict["reason"] == "MRP_BELOW_OFFER":
                raise HTTPException(
                    status_code=400, detail="Offer price cannot exceed MRP"
                )
        existing["pricing"] = merged_pricing
    if product.images is not None:
        existing["images"] = product.images
    if product.is_active is not None:
        existing["is_active"] = product.is_active

    existing["updated_at"] = datetime.now().isoformat()

    _save_catalog_product(existing)

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
                "inventory": {"total_quantity": 0, "locations": {}, "reorder_level": 5},
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
