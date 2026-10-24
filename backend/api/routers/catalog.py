"""
IMS 2.0 - Catalog Router
========================
Comprehensive product catalog management with category-specific fields.
Handles product creation, SKU generation, and Shopify sync.
"""

from api.utils.event_time import utc_now_iso

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from pydantic import BaseModel, Field, field_validator
from typing import Optional, Dict, Any, List, Tuple, Union
from datetime import datetime, timezone
from enum import Enum
import copy
import logging
import uuid
from copy import deepcopy

from ..services.cache import invalidate_product_lists

logger = logging.getLogger(__name__)

from .auth import get_current_user, require_roles
from api.services.cost_mask import mask_cost, mask_cost_list, refuse_cost_write
from ..services.online_catalog import (
    online_status_for_skus,
    online_summary,
    doc_online_state,
    reconcile_store_barcodes,
    online_mapping_available,
)
from ..services import stock_allocation
from ..services.pricing_caps import (
    evaluate_offer_price,
    offer_terms_changed,
    offer_write_problem,
    CATEGORY_DISCOUNT_CAPS,
)
from ..services.gst_rates import gst_rate_for_category, hsn_for_category
from ..services import product_master as _pm
from ..services import online_delist as _delist
from ..services import catalog_flags as _flags
from ..services import catalog_list as _catalog_list
from ..services.shopify_push import is_variant_of as _is_variant_of
# THE ONLINE on-hand reader, the one the oversell-risk tile reads (panel round
# 7, one-rule P4). The reconciliation screen compares IMS against the WEBSITE's
# listed number, and read the router's physical counter instead -- which does
# not exclude the ONLINE stores, so an AVAILABLE unit parked on BV-ONLINE-01
# (unpickable: no shelf, POS blocked) counted as in-store here while the writer
# publishes 0 for it, and a real oversell classified OK on this screen after the
# tile had stopped hiding it. Two online screens, one reader.
from ..services.online_sync_health import _on_hand_by_product

router = APIRouter()

# Canonical discount-cap tiers. Sourced from the SHARED pricing_caps cap table
# (the single source of truth also used by the bulk-price / POS cap logic) so
# the accepted set never drifts from what the resolver actually understands.
_VALID_DISCOUNT_CATEGORIES = frozenset(CATEGORY_DISCOUNT_CAPS.keys())
# NOTE: _get_db() is defined later in this module (reused here at call time).


# ============================================================================
# ONLINE CATALOG STATUS (IMS Mongo -- post-BVI)
# ============================================================================
# Lets IMS surface which products are "Online" (on Shopify), matched by
# SKU/barcode against the IMS catalog itself (catalog_products.ecom +
# catalog_variants) -- BVI and its Postgres were deleted 2026-07-20 and IMS is
# the sole source of online truth. Fully fail-soft: any failure returns empty
# so IMS keeps working unchanged. The live LISTED quantity stays on Shopify
# (online_stock is null here; see /online-store/stock-tally for a live read).


def _online_statuses(db, sku_list: List[str]) -> Dict[str, Any]:
    """online_status_for_skus with ``online`` read through THE one "is this
    listing live on Shopify" reader (inventory.skus_on_live_listings) -- the
    one the nightly parity, the Stock Tally and the reconciliation view read
    -- so the Inventory screen's Online column never calls a draft or
    taken-down listing online while those views call it not online. A
    failed read -- the catalogue lookup itself or the live read -- answers
    ``online`` None (unknown: the screen says Unverified), never a confident
    "in-store only"."""
    from ..services.shopify_push.inventory import live_listing_split

    try:
        statuses = online_status_for_skus(db, sku_list, strict=True)
    except Exception:  # noqa: BLE001
        # Which keys are on Shopify at all is unknown: every key unknown,
        # never a confident "in-store only".
        return {
            k: {"online": None, "sellable_online": None, "online_stock": None, "status": None,
                "shares_item": False}
            for k in dict.fromkeys(s.strip() for s in sku_list if s and s.strip())
        }
    if not statuses:
        return statuses
    try:
        live, shared = live_listing_split(db, list(statuses), strict=True)
    except Exception:  # noqa: BLE001
        live, shared = None, set()
    for key, status in statuses.items():
        name = str(key).strip()
        # A SKU on a LIVE listing that shares its Shopify item with another
        # product is on the website but the writer refuses it: unknown
        # (Unverified), never "In-store only".
        # shares_item names the cause, so a screen can tell it from a failed
        # read (both are online None).
        status["shares_item"] = live is not None and name in shared
        status["online"] = None if live is None or name in shared else name in live
    return statuses


@router.get("/online-status")
async def get_online_status(
    skus: str = Query(..., description="Comma-separated SKUs to look up"),
    current_user: dict = Depends(get_current_user),
):
    """For each SKU, whether it's online (on Shopify, per the IMS catalog).
    Returns {statuses: {sku: {online, online_stock(null), status}}}."""
    sku_list = [s.strip() for s in (skus or "").split(",") if s.strip()]
    return {"statuses": _online_statuses(_get_db(), sku_list)}


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
    return {"statuses": _online_statuses(_get_db(), sku_list)}


@router.get("/online-summary")
async def get_online_summary(current_user: dict = Depends(get_current_user)):
    """Diagnostic: is the IMS online catalog (Mongo) populated + mapped counts."""
    return online_summary(_get_db())


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
    """RETIRED (2026-07-20): this filled storeBarcode in the BVI Postgres, which
    was deleted with BVI. Store barcodes now live on IMS
    catalog_variants.store_barcode. Kept as an honest no-op (never writes)."""
    return reconcile_store_barcodes(
        body.pairs, apply=body.apply, only_empty=body.only_empty
    )


@router.get("/online-stock-reconcile")
async def online_stock_reconcile(
    store_id: Optional[str] = Query(
        None, description="Limit in-store on-hand to one store"
    ),
    limit: int = Query(1000, ge=1, le=5000),
    current_user: dict = Depends(get_current_user),
):
    """Reconcile in-store physical on-hand (IMS) vs online-listed stock
    (Shopify) per SKU, PER SHOPIFY LOCATION, against THE ONE RULE the writer
    sends (online_sync_health.rule_by_location).

    Post-BVI: "which SKUs are online" is the SKU's listing live on Shopify
    (inventory.skus_on_live_listings, the reader the nightly parity and the
    Stock Tally share), and the LISTED quantity is read LIVE from Shopify for
    the live, online-mapped SKUs
    (creds-gated, read-only, capped to the MAPPED set), per location. With
    ``store_id`` the row is that shop's own Shopify location, in full, as
    parity compares it (an unmapped shop lists 0 and is recommended 0);
    without it the row sums every mapped location plus every other location
    the storefront sells from (live_listed_qty_for_skus ``levels``).
    Either way the verdict is decided location by location
    (shopify_stock_parity.unbacked_units) -- never one shop's (or unmapped
    Pune's) shelf backing another shop's listing:
      * OVERSELL_RISK  -- a location the storefront sells from lists beyond
        the shelf behind it;
      * OVER_ALLOCATED -- a location lists beyond what the writer sends there
        (its safety buffer, the SUPERADMIN online block), sells online or
        not, by even one unit. Same level, same writer's number as the
        nightly parity, but not its verdict: parity also files a location
        listing FEWER than the writer sends, and lets a gap within its
        tolerance pass where the writer sends more than 0 -- so a shop's
        drift task can sit beside OK (under-listed) and OVER_ALLOCATED beside
        no task (within tolerance). Parity compares only a SKU IMS still
        sells (a retired one is on neither this page nor a task) on a live
        listing, and a row is assessed (is_online) by the SAME reader
        (inventory.skus_on_live_listings: a draft or taken-down listing is
        assessed by neither), so every SKU a shop's drift task names is a
        row here
        (within ``limit``), and that shop's Online and ``recommended`` are
        the two numbers the task names, read now (the task's are from the
        night it compared them); ``recommended`` is the number its
        instruction asks for (shopify_stock_parity.sync_drift_task);
      * ``recommended`` -- what the writer sends, summed over the mapped shops
        in view. The buffer is the writer's own (the Shopify integration's
        safety_buffer); this page has no second one;
      * ``delta`` -- units listed beyond what the writer sends, counted
        location by location (never listed-total minus recommended-total).
    Honesty contract (audit fix-round P1): an online SKU the live read did NOT
    cover carries online=null and classifies LISTED_UNKNOWN; a rule IMS could
    not read classifies ONHAND_UNKNOWN -- never a confident 0/OK.
    listed_qty_live is True only on FULL mapped coverage;
    listed_live_rows / listed_mapped_rows expose partial coverage.
    Read-only + fail-soft."""
    # The shop key as the writer's map (inventory._mapped) and the rule spell
    # it: a stored 'BV-A ' is mapped, and its shelf read, as 'BV-A'.
    store_id = (store_id or "").strip() or None
    db = _get_db()
    if db is None:
        return {
            "items": [],
            "summary": {},
            "online_configured": False,
            "listed_qty_live": False,
            "listed_live_rows": 0,
            "listed_mapped_rows": 0,
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
    except Exception:  # noqa: BLE001
        # Which SKUs exist is unknown: never "nothing to reconcile, fully
        # covered" beside a drift task parity keeps open.
        return {
            "items": [],
            "summary": {},
            "online_configured": online_mapping_available(db),
            "listed_qty_live": False,
            "listed_live_rows": 0,
            "listed_mapped_rows": 0,
            "live_listings_unknown": True,
        }

    pids = [p.get("product_id") for p in products if p.get("product_id")]
    on_hand = _on_hand_by_product(db, pids, store_id)
    skus = [p.get("sku") for p in products if p.get("sku")]
    from ..services.online_stock_writeback import _safety_buffer
    from ..services.online_sync_health import live_listed_qty_for_skus, rule_by_location
    from ..services.shopify_push.inventory import live_listing_split
    from ..services.shopify_stock_parity import unbacked_units

    # Online = the SKU's listing is live on Shopify: THE one reader the
    # nightly parity and the Stock Tally read (a draft or taken-down listing
    # sells nothing and is not assessed, exactly as parity skips it). Read
    # STRICT: a failed read is unknown (every row LISTED_UNKNOWN), never a
    # confident NOT_ONLINE beside a drift task parity keeps open.
    try:
        on_live, shares_item = live_listing_split(db, skus, strict=True)
    except Exception:  # noqa: BLE001
        on_live, shares_item = None, set()
    live_skus = [s for s in skus if on_live is not None and str(s).strip() in on_live]

    # Live Shopify listed quantities PER LOCATION for the live SKUs: mapped
    # SKUs first, cap on the mapped set, coverage counts carried through
    # (None when unavailable).
    live = await live_listed_qty_for_skus(db, live_skus) if live_skus else None
    variants = (live or {}).get("variants") or []
    # Parity's full level (the Online column, OVER_ALLOCATED) and the level
    # the storefront sells from (OVERSELL_RISK): live_listed_qty_for_skus.
    levels = (live or {}).get("levels") or {}
    selling = (live or {}).get("selling") or {}
    # The shelf and the writer's number per shop + the writer's own shop ->
    # location map (one shop's entry with store_id). None = unknown ->
    # ONHAND_UNKNOWN, never clean.
    shelf, sent, mapped = rule_by_location(db, skus, store_id=store_id) or (None, None, None)
    if store_id:
        # ONE shop = its OWN location only; a shop with no location lists 0.
        # An unreadable map = which location is unknown -> listed unknown.
        gid = (mapped or {}).get(store_id)

        def own(lv):
            return {} if mapped is None else {
                inv: ({gid: per.get(gid, 0)} if gid else {}) for inv, per in lv.items()
            }

        levels, selling = own(levels), own(selling)
    over = None if shelf is None else unbacked_units(variants, shelf, selling, mapped)
    excess = None if sent is None else unbacked_units(variants, sent, levels, mapped)
    inv_of = {v["sku"]: v["inventory_item_id"] for v in variants}

    items = []
    for p in products:
        sku = p.get("sku")
        is_online = None if on_live is None else str(sku or "").strip() in on_live
        shares = str(sku or "").strip() in shares_item
        # Uncovered online SKU -> None (LISTED_UNKNOWN downstream), never a
        # confident 0. Offline SKUs carry 0 (they are not assessed anyway).
        per_location = levels.get(inv_of.get(sku))
        listed = None if per_location is None else sum(int(q) for q in per_location.values())
        per_shop = None if sent is None else sent.get(sku)
        items.append(
            {
                "sku": sku,
                "name": f"{p.get('brand', '') or ''} {p.get('model', '') or ''}".strip(),
                # UNKNOWN on-hand is None (ONHAND_UNKNOWN), never a confident 0.
                "in_store": None if on_hand is None else on_hand.get(p.get("product_id"), 0),
                # A shared SKU is live but the writer refuses it: its listed
                # number is not shown as a confident 0 (None = unknown).
                "online": None if shares else (listed if is_online is not False else 0),
                "is_online": is_online,
                "shares_item": shares,
                # What the writer sends to the mapped shops in view (None: unknown).
                "recommended": None if per_shop is None else sum(int(per_shop.get(sid, 0)) for sid in mapped),
                # Listed beyond the shelf / beyond the writer's number, location
                # by location (None: unknown).
                "unbacked": (None if over is None else over.get(sku)) if is_online is not False else 0,
                "excess": (None if excess is None else excess.get(sku)) if is_online is not False else 0,
            }
        )

    result = stock_allocation.reconcile_items(items)
    result["summary"]["safety_buffer"] = _safety_buffer(db)
    result["online_configured"] = online_mapping_available(db)
    live_rows = int(live["live"]) if live else 0
    mapped_rows = int(live["mapped"]) if live else 0
    # No live listing at all (the read succeeded) = nothing to read: covered,
    # not "Shopify unavailable".
    result["listed_qty_live"] = (on_live is not None and not live_skus) or (
        bool(live) and mapped_rows > 0 and live_rows >= mapped_rows)
    result["live_listings_unknown"] = on_live is None
    result["listed_live_rows"] = live_rows
    result["listed_mapped_rows"] = mapped_rows
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
    # No chain-wide reorder_level: levels are per shop (owner ruling D12),
    # set through PUT /inventory/reorder-levels/{product_id}.
    # Owner decision (2026-07-04): -1 means "no auto-reorder" -- every reorder
    # engine skips the product until a positive qty is explicitly configured
    # (see api/services/reorder_policy.py).
    reorder_quantity: int = -1


class ShopifySyncInput(BaseModel):
    sync_to_shopify: bool = False
    shopify_product_type: Optional[str] = None
    # The form's "Shopify tags" box -> the spine's governed `tags` + the twin's
    # ecom.seo.tags (product_master.set_twin_tags), the list the push sends.
    shopify_tags: List[str] = []
    # No POS channel flag: IMS is the till, a product never publishes to
    # Shopify POS (owner ruling 2026-09-28).


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
    # Grams, on the spine; 0 = no weight (never stored). Same bound as the
    # spine PUT (products.ProductUpdate).
    weight: Optional[float] = Field(None, ge=0)

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
    #   tags -- replaces the tag list (normalised, landed at ecom.seo.tags --
    #     the one spelling the Shopify push reads; product_master.set_twin_tags).
    #   expected_updated_at -- ADDITIVE optimistic concurrency: when present
    #     and != the stored updated_at, the save 409s instead of clobbering a
    #     concurrent reviewer's fixes. Absent = today's last-write-wins (the
    #     existing drawer sends nothing and keeps working unchanged).
    name: Optional[str] = None
    tags: Optional[List[str]] = None
    description: Optional[str] = None
    hsn_code: Optional[str] = None
    gst_rate: Optional[float] = None
    # Grams, written on the SPINE. A positive number sets it; an explicit
    # null or 0 CLEARS it (0 is "no weight", never stored); absent = untouched.
    weight: Optional[float] = Field(None, ge=0)
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


def _mark_online_dirty(product: Dict) -> None:
    """Queue this catalog_products doc for the next MANUAL Online Store push.

    `ecom.locally_modified` is the ONE dirty flag the push reads -- both to pick
    the rows the operator's "push all pending" sweep sends AND to compute the
    `pending` number shown on the Online Store screen
    (online_store_push._product_counts). Nothing on the product doors used to
    set it, so a catalogued product was never queued and the screen truthfully
    reported the flag while lying about the work: "pending: 0".

    Same convention as ecom_collection_repository / ecom_menu_repository,
    adapted to one difference: those repos can use `setdefault` because their
    payload is a PATCH, whereas the catalog doors save the WHOLE stored doc --
    which already carries `locally_modified: False` once a push has cleared it.
    A setdefault here would therefore never re-queue an already-pushed product.
    So this assigns, and the opt-out is an explicit `mark_dirty=False` at the
    call site instead.

    The `ecom` sub-doc is created when absent: a doc with no `ecom` is not even
    counted as `staged`, so the flag would have nowhere to live and the count
    would stay blind. Queuing is not yet publishing: the next press or the
    scheduled sync sends it (a FIRST publish too, owner ruling 2026-10-20),
    through the product gate, and only a SUCCESSFUL Shopify write clears the
    flag again (shopify_push._writeback_product)."""
    ecom = product.get("ecom")
    if not isinstance(ecom, dict):
        ecom = {}
        product["ecom"] = ecom
    # A doc created without an `ecom` key (the catalog door and bulk import)
    # would otherwise be counted as staged+pending while belonging to NO status
    # bucket -- the Online Store screen's DRAFT and PUBLISHED cards would both
    # show 0 for a row sitting in the queue. Same default _build_pim_doc uses.
    # "The count was the lie" is this change's whole thesis; do not reintroduce
    # it one card over.
    ecom.setdefault("status", "DRAFT")
    # Flag + a fresh edit revision (SF-04): a push already holding the older
    # payload can then never take this edit off the queue.
    from api.services.catalog_ecom import dirty_marks

    ecom.update(dirty_marks())


# The ecom keys a catalogue edit OWNS (review of PR #1227). Everything else
# under ecom -- the Shopify ids, the first-publish claim, the stock / photo /
# tag ledgers, the take-down marks -- belongs to the push and the online
# services, and an edit's copy of them may be minutes old (a page read before
# a press, saved after it). So an existing row is written WITHOUT ecom and only
# these keys are merged onto the CURRENT ecom (compare-and-set); status only
# when the row has none. A save that is not a catalogue edit writes no ecom.
_CATALOG_ECOM_KEYS = ("locally_modified", "edit_rev", "seo")


def _without_ecom(product: Dict) -> Dict:
    return {k: v for k, v in product.items() if k not in ("ecom", "_id")}


def _merge_catalog_ecom(coll, product: Dict) -> None:
    from api.services.catalog_ecom import update_ecom_in

    mine = product.get("ecom") if isinstance(product.get("ecom"), dict) else {}

    def _merge(ecom: Dict) -> Dict:
        for key in _CATALOG_ECOM_KEYS:
            if key in mine:
                ecom[key] = copy.deepcopy(mine[key])
        if "status" in mine:
            ecom.setdefault("status", mine["status"])
        return ecom

    update_ecom_in(coll, product["id"], _merge)


def _save_catalog_product(product: Dict, *, mark_dirty: bool = True) -> None:
    # Dirty by DEFAULT: every save through this door is a human catalogue edit
    # unless it says otherwise. Writes that are NOT a catalogue edit -- a stock
    # movement, the retired Shopify-sync bookkeeping stamp, the soft-delete
    # stamp -- pass mark_dirty=False and say why at the call site.
    if mark_dirty:
        _mark_online_dirty(product)
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
            coll.update_one({"id": product["id"]}, {"$set": _without_ecom(product)})
            if mark_dirty:
                _merge_catalog_ecom(coll, product)
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
    if mark_dirty:
        _queue_model_of(product)


def _queue_model_of(product: Dict) -> None:
    """A catalogue edit of a colour/size of a model queues its MODEL's
    listing (G2, review of #1233: the colour itself owns no listing, so its
    own dirty flag is never swept). Fail-soft."""
    if (product.get("ecom") or {}).get("variant_of"):
        _pm.queue_model_listing_of(_get_db(), product)


def _save_catalog_product_cas(
    product: Dict, expected_raw_updated_at, *, mark_dirty: bool = True
) -> bool:
    """Compare-and-swap save for the optimistic-concurrency PUT path: the
    write only lands when the stored updated_at STILL equals the raw value
    read at load time (filtered on the raw datetime-or-string value so
    imported docs match). Returns False when another writer got in between --
    the caller 409s. The plain check-then-write left a multi-round-trip
    window where two overlapping saves both passed the timestamp check and
    the loser silently clobbered the winner's fixes.

    Marks the row dirty for the Online Store push by default, exactly like
    _save_catalog_product (this is the same catalogue-edit door, just with an
    optimistic-concurrency filter). The stamp happens before the write attempt;
    when the CAS loses the write does not land, so nothing is queued."""
    if mark_dirty:
        _mark_online_dirty(product)
    coll = _catalog_coll()
    if coll is not None:
        res = coll.update_one(
            {"id": product["id"], "updated_at": expected_raw_updated_at},
            {"$set": _without_ecom(product)},
        )
        matched = getattr(res, "matched_count", None)
        if matched is None:
            # MockCollection's update_one result only carries modified_count.
            matched = getattr(res, "modified_count", 0)
        if matched and mark_dirty:
            _merge_catalog_ecom(coll, product)
            _queue_model_of(product)
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


# The answer when a save that needs the linked spine could not read it.
_SPINE_UNCHECKED = (
    "The billing product could not be checked, so nothing was saved. Please save again."
)


def _spine_of(repo, catalog_doc: Optional[Dict]) -> Optional[Dict]:
    """The billing SPINE row of a catalog twin -- the ONE resolver both
    catalog doors' spine sync (PUT is_active / price / tier / hsn / gst,
    DELETE is_active) key their `products` write on.

    A legacy / convergence twin shares the spine id. A DOOR-created twin
    (product_master.create_product: 71 of 77 live products on the 08-30
    census, and every size variant) is keyed by the spine's pim_product_id
    -- a DIFFERENT uuid -- so it is found through that link (the mirror
    image of the spine PUT's pim_product_id-first twin lookup,
    product_master.mirror_update_to_catalog_twin; _spine_ids is THE rule).
    Until this, both doors' `_pr.update(<catalog id>, ...)`
    silently matched NO spine on a door-created product: the drawer's
    deactivate / the Delete button never reached the spine a soft-deleted
    product is sold from at POS, and for a size variant -- whose only
    off-sale marker is the spine's is_active (online_stock_writeback lists
    an inactive spine at 0) -- the next stock pass put the size straight
    back on sale. None when there is no spine (a BVI-only twin).

    Strict reads (the one rule, _spine_ids, then _pm.strict_find_many): a
    read error RAISES, never reads as "no spine" -- BaseRepository.find_by_id
    prints it and answers None, and DELETE would then discard an ordered
    draft as an ordinary product."""
    if repo is None or not catalog_doc:
        return None
    sid = _spine_ids(repo, [catalog_doc])[0]
    rows = _pm.strict_find_many(repo, {"product_id": sid}) if sid else []
    return rows[0] if rows else None


def _spine_product_id(repo, catalog_doc: Optional[Dict]) -> Optional[str]:
    """The spine id of a catalog twin (_spine_of's, by the one rule
    _spine_ids). None when there is no spine (a BVI-only twin). Read errors
    raise: a barcode save must refuse rather than silently skip its spine."""
    if repo is None or not catalog_doc:
        return None
    return _spine_ids(repo, [catalog_doc])[0]


def _spine_ids(repo, docs: List[Optional[Dict]]) -> List[Optional[str]]:
    """THE twin -> spine rule (_spine_product_id's, and the review list's
    Added column and sort through _spine_facts), batched: for each twin (same
    order) the spine whose pim_product_id is the twin's id (the create door's
    own link, the one every spine -> twin lookup reads first), else the spine
    whose product_id is the twin's id (a convergence twin shares it); None
    when none. Never by sku alone: a review-queue twin can share a sku with
    an UNRELATED product's spine (promote 409s on exactly that), and the sku
    match wrote this twin's price, active flag and barcode onto the other
    product and excused that product's barcode as this one's own in the
    one-holder check. One read; raises when it fails. _twins_of is this rule
    read the other way."""
    ids = [d["id"] for d in docs if d and d.get("id")]
    by_pim: Dict[Any, str] = {}
    by_id: Dict[Any, str] = {}
    if ids:
        for s in repo.collection.find(
            {"$or": [{"pim_product_id": {"$in": ids}}, {"product_id": {"$in": ids}}]},
            {"_id": 0, "product_id": 1, "pim_product_id": 1},
        ):
            if s.get("product_id"):
                by_id.setdefault(s["product_id"], s["product_id"])
                if s.get("pim_product_id"):
                    by_pim.setdefault(s["pim_product_id"], s["product_id"])
    return [
        (by_pim.get(d.get("id")) or by_id.get(d.get("id"))) if d else None
        for d in docs
    ]


def _twins_of(repo, spines: List[Optional[Dict]]) -> List[Optional[Dict]]:
    """THE spine -> twin rule for the Catalog page's archive and restore:
    _spine_ids read the other way, so the two directions can never disagree
    -- for each spine (same order) the twin whose spine _spine_ids picks IS
    this spine; None when no twin's is. A twin can only point at a spine
    through the spine's pim_product_id or its product_id, so those are the
    candidates (in that order). Two reads; raises when one fails."""
    keys = [k for s in spines if s for k in (s.get("pim_product_id"), s.get("product_id")) if k]
    coll = _catalog_coll()
    if not keys:
        found: List[Dict] = []
    elif coll is not None:
        found = list(coll.find({"id": {"$in": keys}}))
    else:
        found = [t for t in CATALOG_PRODUCTS.values() if t.get("id") in keys]
    found = [dict(t) for t in found if t.get("id")]
    owner = dict(zip((t["id"] for t in found), _spine_ids(repo, found)))
    out: List[Optional[Dict]] = []
    for s in spines:
        sid = (s or {}).get("product_id")
        mine = [t for t in found if sid and owner[t["id"]] == sid]
        mine.sort(key=lambda t: (t["id"] != s.get("pim_product_id"), t["id"] != sid))
        out.append(mine[0] if mine else None)
    return out


def _products_coll():
    """The `products` (spine) collection, or None when it cannot be reached --
    then no spine is known, and no catalogue door can write one either (their
    spine syncs go through the same store, fail-soft)."""
    from ..dependencies import get_product_repository

    try:
        return getattr(get_product_repository(), "collection", None)
    except Exception:  # noqa: BLE001
        logger.warning("[CATALOG] products store unreachable", exc_info=True)
        return None


def _refuse_ordered_draft(doc: Optional[Dict]) -> None:
    """An item a manager ordered on a PO before it was catalogued (audit C1)
    already HAS its billing row. It is finished in the product editor
    (PUT /products/{id}) -- the save that restamps it and puts the units its
    receipts hold on the shelf. The import review's save / approve would write
    around that door, so they refuse it and say where to go. The one rule
    (product_master.is_ordered_draft) decides, read on a copy of the doc so
    nothing stamped here is ever saved."""
    probe = dict(doc or {})
    _pm.stamp_ordered_drafts([probe], _products_coll())
    if probe["ordered_draft"]:
        raise HTTPException(
            status_code=409,
            detail=(
                "This item was ordered on a purchase order before it was "
                "catalogued. Finish it in the product editor (Catalogue > Needs "
                "review > open it); its held stock goes on the shelf when you save."
            ),
        )


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


def _iso(value) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value or "")


def _twin_mrp(p: Dict) -> float:
    v = (p.get("pricing") or {}).get("mrp", p.get("mrp"))
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


# The imported (review) segment's Sort by: the same eight choices as
# services/catalog_list.SORTS. Each key reads (twin, the row's facts): the
# dates are the very values the Added column shows on the same row
# (_row_facts) -- its spine's, or for an import with no spine yet its own
# import date and its own last edit. "edited" is last_edited_at
# (catalog_list.last_edited_at, the one rule).
_IMPORTED_SORTS = {
    "edited": (lambda p, s: _iso(s.get("last_edited_at")), True),
    "newest": (lambda p, s: _iso(s.get("created_at")), True),
    "oldest": (lambda p, s: _iso(s.get("created_at")), False),
    "name_asc": (lambda p, s: str(p.get("title") or "").lower(), False),
    "name_desc": (lambda p, s: str(p.get("title") or "").lower(), True),
    "brand_asc": (
        lambda p, s: str((p.get("attributes") or {}).get("brand_name") or "").lower(),
        False,
    ),
    "mrp_desc": (lambda p, s: _twin_mrp(p), True),
    "mrp_asc": (lambda p, s: _twin_mrp(p), False),
}

_ATTRIBUTION_FIELDS = (
    "created_by",
    "created_by_name",
    "created_at",
    "updated_by",
    "updated_by_name",
    "updated_at",
    "last_edited_at",
)


# An imported row's "added" moment when it has no spine: the import date
# (the 4,393 BVI docs carry migrated_at, no created_at) -- _catalog_sort_key's
# coalesce, the legacy newest-first order.
_TWIN_ADDED = {"$ifNull": ["$created_at", "$migrated_at"]}


def _own_facts(docs: List[Dict]) -> Dict[str, Dict]:
    """{twin id: its own facts} for imports with no spine yet: the import date
    and the last edit saved on the twin itself (PUT /catalog/products/{id}
    stamps it through catalog_list.edit_stamp), in one read. Who ADDED it is
    never guessed from the twin. {} when the read fails."""
    coll = _catalog_coll()
    ids = [d.get("id") for d in docs if d.get("id")]
    if coll is None or not ids:
        return {}
    proj = {
        "_id": 0,
        "id": 1,
        "updated_by": 1,
        "updated_by_name": 1,
        "updated_at": 1,
        "created_at": _TWIN_ADDED,
        "last_edited_at": _catalog_list.last_edited_at(_TWIN_ADDED),
    }
    try:
        return {
            str(r["id"]): r
            for r in coll.aggregate([{"$match": {"id": {"$in": ids}}}, {"$project": proj}])
        }
    except Exception:  # noqa: BLE001 -- the column just shows '-'
        logger.warning("[CATALOG] imported-row facts read failed", exc_info=True)
        return {}


def _row_facts(docs: List[Dict]) -> List[Dict]:
    """THE added / edited facts of each imported row (same order): its
    spine's (_spine_facts) when it has one -- every edit of the row reaches
    the spine (the twin PUT stamps it) -- else its own (_own_facts). {} when
    neither is readable: the page shows '-' honestly, the sort reads the same
    nothing."""
    spine = _spine_facts(docs)
    if spine is None:
        return [{} for _ in docs]
    own = _own_facts([d for d, s in zip(docs, spine) if not s])
    return [s or own.get(str(d.get("id") or "")) or {} for d, s in zip(docs, spine)]


def _spine_facts(docs: List[Dict]) -> Optional[List[Dict]]:
    """The added / edited facts of each imported row's spine (same order) --
    the spine THE rule picks (_spine_ids: the one every edit / archive /
    restore door acts on) -- with its last_edited_at computed by
    catalog_list.LAST_EDITED_AT. {} for a row with no spine; None when a read
    fails (unknown, never "no spine")."""
    from ..dependencies import get_product_repository

    repo = get_product_repository()
    if repo is None or not docs:
        return [{} for _ in docs]
    proj: Dict[str, Any] = {"_id": 0, "product_id": 1}
    proj.update({f: 1 for f in _ATTRIBUTION_FIELDS})
    proj["last_edited_at"] = _catalog_list.LAST_EDITED_AT
    try:
        spine_ids = _spine_ids(repo, docs)
        found = [p for p in spine_ids if p]
        facts = {
            s["product_id"]: s
            for s in repo.collection.aggregate(
                [{"$match": {"product_id": {"$in": found}}}, {"$project": proj}]
            )
        } if found else {}
    except Exception:  # noqa: BLE001 -- the column just shows '-'
        logger.warning("[CATALOG] spine attribution read failed", exc_info=True)
        return None
    return [facts.get(p) or {} if p else {} for p in spine_ids]


def _stamp_spine_weight(docs: List[Dict]) -> None:
    """WEIGHT lives on the SPINE (grams): the one copy the edit page, the
    till and the Shopify push read. Put each row's spine weight on it, in
    place, so the drawer and the review editor show the stored value. A twin
    with no spine (an import awaiting promote) keeps its own until promote
    carries it across (_promote_extra_fields). Fail-soft: unread, a row
    shows what it holds."""
    from ..dependencies import get_product_repository

    try:
        repo = get_product_repository()
        if repo is None or not docs:
            return
        sids = _spine_ids(repo, docs)
        found = [s for s in sids if s]
        weights = {
            s["product_id"]: s.get("weight")
            for s in repo.collection.find(
                {"product_id": {"$in": found}}, {"_id": 0, "product_id": 1, "weight": 1}
            )
        } if found else {}
    except Exception:  # noqa: BLE001
        logger.warning("[CATALOG] spine weight read failed", exc_info=True)
        return
    for d, sid in zip(docs, sids):
        if sid and weights.get(sid):
            d["weight"] = weights[sid]


def _stamp_spine_attribution(docs: List[Dict], facts: List[Dict]) -> None:
    """Put each row's facts (_row_facts, same order) on it, in place, and the
    names from users (catalog_list.stamp_people)."""
    from ..dependencies import get_db

    for d, spine in zip(docs, facts):
        for f in _ATTRIBUTION_FIELDS:
            d[f] = spine.get(f)
    _catalog_list.stamp_people(get_db(), docs)


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
    ordered_draft: Optional[bool] = Query(
        default=None,
        description=(
            "true = only items a manager ordered on a PO before they were "
            "catalogued (they carry spine_product_id and open in the product "
            "editor); false = none of them -- the import-review queue."
        ),
    ),
    photo: Optional[str] = Query(
        default=None,
        pattern="^(has|missing)$",
        description=(
            "Photo filter. 'has' = a usable photo by the Shopify push's own "
            "predicate (an absolute http(s) URL), 'missing' = none -- the "
            "Missing-photos work list. Every row also carries has_photo + "
            "online (LIVE / QUEUED / OFF / BLOCKED), computed here, never "
            "re-derived by the screen."
        ),
    ),
    limit: int = Query(default=50, ge=1, le=250),
    page: int = Query(default=1, ge=1),
    sort: Optional[str] = Query(
        default=None,
        pattern=_catalog_list.SORT_PATTERN,
        description=(
            "The Catalog page's Sort by (owner 2026-10-12). Absent = the "
            "legacy newest-first order. Given, the rows also carry who added "
            "/ last edited them, from their spine or, for an import with no "
            "spine yet, its own import date and edit (manager roles)."
        ),
    ),
    current_user: dict = Depends(get_current_user),
):
    """List all products in catalog"""
    products = _all_catalog_products()
    # Direct-call safety (unit suites call this handler as a plain function):
    # an unsupplied `photo` arrives as its Query default object, not None.
    if not isinstance(photo, str):
        photo = None
    if not isinstance(ordered_draft, bool):
        ordered_draft = None
    if not isinstance(sort, str) or sort not in _IMPORTED_SORTS:
        sort = None

    # Photo + online truth, ONE rule (online_catalog.product_online_state,
    # with THE push gate's verdict), stamped before the filters so the photo
    # filter reads the same value the column shows.
    db = _get_db()
    for p in products:
        state = doc_online_state(db, p)
        p["has_photo"] = state["has_photo"]
        p["online"] = state["online"]
        p["online_note"] = state["note"]
    if photo:
        want = photo == "has"
        products = [p for p in products if p.get("has_photo") is want]

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
    # Needs review = an ordered draft (product_master.is_ordered_draft, read off
    # its product) or an import awaiting review: the one rule the count and
    # the badge read too (online_catalog.catalog_counts).
    _pm.stamp_ordered_drafts(products, _products_coll())
    if needs_review is not None:
        products = [p for p in products if _pm.in_needs_review(p) == needs_review]
    if source:
        products = [p for p in products if p.get("source") == source]
    if ordered_draft is not None:
        products = [p for p in products if p["ordered_draft"] == ordered_draft]
    # 'all' = no active filter; otherwise the legacy boolean equality match.
    if is_active != "all":
        active_bool = is_active in ("true", "True")
        products = [p for p in products if p.get("is_active") == active_bool]

    facts: List[Dict] = []
    if sort:
        # The whole filtered list, then the page; the id breaks ties (a stable
        # sort keeps the id order among equal keys, reversed or not) so pages
        # never overlap. The dates are the row's facts, as the column shows.
        key, reverse = _IMPORTED_SORTS[sort]
        rows = sorted(
            zip(products, _row_facts(products)), key=lambda t: str(t[0].get("id") or "")
        )
        rows.sort(key=lambda t: key(*t), reverse=reverse)
        products = [p for p, _ in rows]
        facts = [f for _, f in rows]
    elif needs_review is True:
        # In the Needs-review list ONLY, an ordered draft (audit C1) leads:
        # stock is, or soon will be, waiting on it. Everywhere else it sorts
        # by date like any product.
        products.sort(
            key=lambda p: (p["ordered_draft"], _catalog_sort_key(p)),
            reverse=True,
        )
    else:
        # Sort by created date (imported docs coalesce to migrated_at)
        products.sort(key=_catalog_sort_key, reverse=True)

    total = len(products)
    start = (page - 1) * limit
    end = start + limit

    # F35: per-unit cost answers to the one product-cost rule (services/
    # cost_mask "product": the managers see it, counter roles never).
    # Copies: in no-Mongo mode the rows ARE the in-memory store.
    page_products = [dict(p) for p in mask_cost_list(products[start:end], current_user, "product")]
    _stamp_spine_weight(page_products)
    if sort:
        from .products import _CATALOGUER_VIEW_ROLES

        if set(current_user.get("roles") or []) & (
            {"SUPERADMIN"} | set(_CATALOGUER_VIEW_ROLES)
        ):
            _stamp_spine_attribution(page_products, facts[start:end])
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

    # F35: the one product-cost rule, as on the list and /products.
    product = dict(mask_cost(product, current_user, "product"))
    # The drawer's 'Ordered - finish it' reads the one rule, as the list does.
    _pm.stamp_ordered_drafts([product], _products_coll())
    _stamp_spine_weight([product])
    return {"product": product}


def _guard_catalog_pricing(product: "ProductCreateInput") -> tuple:
    """Apply the two non-negotiable catalog pricing rules that the canonical
    POST /api/v1/products path already enforces, so /catalog/products is not an
    unguarded back-door (SYSTEM_INTENT section 3/10: offer_price > mrp -> BLOCK):

      1. Block offer_price > mrp, and an offer deeper than the item's
         category / luxury-brand cap (400), via THE offer-write check
         (services/pricing_caps.offer_write_problem) every offer door and
         the bulk tools share. (The canonical door re-checks with the tier it
         derives from the Brand Master.)
      2. Derive gst_rate / hsn_code from the product category via the canonical
         services/gst_rates table when the client omits the rate (or omits the
         HSN), so the master rate equals what POS bills (a frame -> 5%, not the
         old hard-coded 18% default). An explicitly-supplied rate still wins.

    Returns the (gst_rate, hsn_code) to persist. The ProductCategory enum values
    are the short codes ("FR", "SG", ...) which are keys in GST_CATEGORY_TABLE.
    """
    attrs = product.attributes or {}
    problem = offer_write_problem(
        product.pricing.mrp,
        product.pricing.offer_price,
        product.pricing.discount_category,
        attrs.get("brand_name") or attrs.get("brand"),
    )
    if problem:
        raise HTTPException(status_code=400, detail=problem)

    category_code = product.category.value
    if product.gst_rate is not None:
        gst_rate = product.gst_rate
    else:
        gst_rate = gst_rate_for_category(category_code)
    hsn_code = product.hsn_code or hsn_for_category(category_code)
    return gst_rate, hsn_code


def _catalog_pm_error(err: "_pm.ProductMasterError", product) -> Tuple[int, Any]:
    """(status, detail) the catalog doors (single create and import) give a
    registry breach: their historical 400 "Missing required field: X" only
    when X really is missing; anything else (a contact-lens power that is not
    a number) in its own words, with its own status."""
    conflict = getattr(err, "conflict", None)
    if conflict:
        return err.status, {
            "message": err.message,
            "code": getattr(err, "code", "DUPLICATE_PRODUCT"),
            "existing": conflict,
        }
    missing = _pm.missing_required_fields(
        product.category.value, dict(product.attributes or {})
    )
    if err.status == 422 and err.field in missing and err.field != "category":
        return 400, f"Missing required field: {err.field}"
    return err.status, err.message


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
    refuse_cost_write(current_user, product.pricing.cost_price)  # FU-23

    # Validate the category is one this door accepts.
    category_config = CATEGORY_FIELDS.get(product.category)
    if not category_config:
        raise HTTPException(status_code=400, detail="Invalid category")

    # Non-negotiable pricing guards (block offer > MRP; derive GST from
    # HSN/category) -- same rules the canonical /products path enforces.
    gst_rate, hsn_code = _guard_catalog_pricing(product)

    # SKU: the ONE readable rule (product_master.build_sku, owner D5), made
    # unique by the same collision suffix every door uses.
    from ..dependencies import get_product_repository

    product_id = f"prod_{uuid.uuid4().hex[:12]}"
    title = generate_product_title(product.category, product.attributes)

    # The catalogue twin and its billing spine share one id. Build the spine
    # here to preserve the native storefront shape below; persistence uses the
    # same identity/GTIN gate as the canonical product-create door.
    try:
        sku = _pm.mint_unique_sku(
            product.category.value, product.attributes,
            product_repo=get_product_repository(), db=_get_db(),
        )
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
                # Grams, on the SPINE (the one copy every reader uses). It
                # used to be stored on the twin alone.
                "weight": product.weight or None,
                # The form's "Shopify tags" box. Declared on ShopifySyncInput and
                # read by nothing until sync audit gap #4: normalised into the
                # spine's governed `tags` here, mirrored onto the twin below.
                "tags": product.shopify.shopify_tags if product.shopify else None,
                "created_by": current_user.get("user_id"),
            },
            source="CATALOG",
        )
    except _pm.ProductMasterError as err:
        # The catalog door's historical 400 "Missing required field" for a
        # field that IS missing; any other breach (a power that is not a
        # number, a bad GTIN) in its own words, with its own status.
        status, detail = _catalog_pm_error(err, product)
        raise HTTPException(status_code=status, detail=detail) from err
    # Share the catalog id + sku so the catalog doc and the spine are ONE product.
    _spine["product_id"] = product_id
    _spine["id"] = product_id
    _spine["sku"] = sku
    # Save the billing identity before the catalogue twin. A refused duplicate
    # (including a concurrent unique-index loss) must never save a second twin
    # pointing at this newly minted id, which has no billing record.
    _pr = get_product_repository()
    if _pr is not None:
        try:
            _pm.persist_new_spine(_spine, _pr)
        except _pm.ProductMasterError as err:
            status, detail = _catalog_pm_error(err, product)
            raise HTTPException(status_code=status, detail=detail) from err
        except Exception as err:  # noqa: BLE001
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

    # Build product data
    product_data = {
        "id": product_id,
        "sku": sku,
        "title": title,
        "category": product.category.value,
        "category_name": CATEGORY_NAMES.get(product.category),
        # The NORMALISED attributes, not the raw pydantic ones. This document is
        # what the storefront push reads, so persisting the raw values here sent
        # typed-in-capitals text to bettervision.in while the billing spine held
        # the clean copy -- one product, two spellings.
        "attributes": (_spine or {}).get("attributes") or product.attributes,
        "description": product.description,
        "hsn_code": hsn_code,
        "gst_rate": gst_rate,
        # Weight is the spine's (above); only a twin with no spine store holds it.
        **({"weight": product.weight} if _pr is None and product.weight else {}),
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
        "created_at": utc_now_iso(),
        "updated_at": utc_now_iso(),
    }

    # Set inventory by location if provided
    if product.inventory and product.inventory.location_id:
        product_data["inventory"]["locations"][
            product.inventory.location_id
        ] = product.inventory.initial_quantity

    # The twin's tag list (ecom.seo.tags, the ONLY spelling the push reads) is
    # the spine's normalised list -- the same value in the two documents that
    # hold it, exactly as the spine mirror keeps them after an edit.
    _pm.set_twin_tags(product_data, _spine.get("tags"))

    _save_catalog_product(product_data)

    # Model families (G1): a model that already exists takes this product as
    # a new colour/size -- the same link_to_family every door uses. Fail-soft:
    # an unlinked product is still a product (catalogue review links it).
    if _pr is not None:
        try:
            _fdb = _get_db()
            _lead = _pm.find_family_lead(_fdb.get_collection("products"), _spine, exclude_id=product_id)
            if _lead is not None:
                _pm.link_to_family(_fdb, product_id, _lead["product_id"], actor=current_user.get("user_id"))
                product_data = _get_catalog_product(product_id) or product_data
        except Exception:  # noqa: BLE001
            logger.warning("[CATALOG] model link skipped for %s", product_id, exc_info=True)

    # Online discount engine (rebuild of BVI DiscountRule): compute + store the
    # ONLINE storefront price from the active rules onto product_data.ecom.*
    # (and any variants). ADDITIVE + FAIL-SOFT -- it must NEVER block a save, and
    # it writes ONLY online-lineage fields (never the in-store offer_price). Still
    # DARK: the stored price reaches Shopify only behind the Phase-5 push gates.
    try:
        from ..services import online_discount_engine as _ode

        _ode.recompute_online_price(product_data, db=_get_db())
    except Exception:  # noqa: BLE001 -- online pricing must never break the create
        logger.warning(
            "[CATALOG] online price recompute skipped for %s", product_id, exc_info=True
        )

    # Sync to Shopify if requested
    shopify_result = None
    if product.shopify and product.shopify.sync_to_shopify:
        shopify_result = await _sync_product_to_shopify(product_data, product.shopify)
        product_data["shopify"] = shopify_result

    return {
        "product": mask_cost({**product_data, "weight": product.weight or None}, current_user, "product"),
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
    refuse_cost_write(  # FU-23
        current_user, product.pricing.cost_price if product.pricing else None
    )

    existing = _get_catalog_product(product_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Product not found")
    _refuse_ordered_draft(existing)
    # Work on a COPY: in no-DB mode _get_catalog_product returns the LIVE
    # in-memory dict, so mutating it mid-handler would leak partial edits into
    # the store on a later 4xx AND defeat the compare-and-swap write below
    # (the stored updated_at must keep its load-time value until the write
    # lands). Shallow is enough -- every field below is REPLACED, not mutated
    # in place.
    existing = dict(existing)
    # The brand before this edit: an attributes-only brand_name change renames
    # the product too, and is judged against its offer below.
    _brand_before = existing.get("brand") or (existing.get("attributes") or {}).get("brand_name")
    # Pre-edit snapshot for the scorecard audit row written after the save:
    # top-level values are REPLACED below (never mutated in place), so a
    # second shallow copy preserves the original values.
    pre_edit = dict(existing)

    # Only Restore brings an archived product back (catalog_list.
    # switches_on_archived): the twin or the spine it would switch on. A
    # spine that cannot be read is not written either (the mirror below is
    # fail-soft too), so only the twin decides then.
    if product.is_active is True:
        _spine = None
        try:
            from ..dependencies import get_product_repository

            _repo = get_product_repository()
            _sid = _spine_product_id(_repo, existing)
            _spine = _repo.find_by_id(_sid) if _sid else None
        except Exception:  # noqa: BLE001
            logger.warning("[CATALOG] spine read for the archive check skipped", exc_info=True)
        if any(_catalog_list.switches_on_archived(d, True) for d in (existing, _spine)):
            raise HTTPException(status_code=409, detail=_catalog_list.USE_RESTORE)

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
    category_changed = False
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
    gtin_written = False
    if product.attributes:
        # Catalog Dictionary parity with the spine PUT (products.py): when the
        # owner configured allowed values for a field, an attributes patch must
        # match them (case-canonicalising). Fail-soft when no db.
        from ..dependencies import get_product_repository

        try:
            # Case ONLY what this submit carries. The merged dict below
            # holds every stored attribute, so casing that would rewrite
            # values the owner had corrected by hand, on every save.
            _typed = _pm.apply_field_casing(
                product.attributes or {}, only=set((product.attributes or {}).keys())
            )
            # The review editor's 'GTIN (mfr)' / 'UPC (mfr)' boxes are the
            # manufacturer barcodes that reach Shopify and Google: the same
            # strict guard and one-holder rule as the spine doors.
            _typed = _pm._guard_gtin_attribute(
                _typed, strict=True, category=existing.get("category")
            )
            product.attributes = _typed
            # Another stored spelling of a barcode key folds on and goes; a
            # gtin the fold makes is checked like a sent one, and written.
            _gtin_repo = get_product_repository()
            try:
                # A spineless twin (an import) is its own holder: its id.
                _holder = _spine_product_id(_gtin_repo, existing) or existing.get("id")
            except Exception as exc:  # noqa: BLE001
                # The one-holder check excuses this product's own spine; not
                # knowing it would excuse the wrong one. Refused, nothing saved.
                raise HTTPException(status_code=503, detail=_SPINE_UNCHECKED) from exc
            merged_attrs, gtin_written = _pm.merge_attributes_edit(
                existing.get("attributes"),
                _typed,
                _gtin_repo,
                _holder,
                existing.get("category"),
            )
            merged_attrs = _pm.enforce_dictionary_values(
                existing.get("category"), merged_attrs, db=_get_db(),
                stored=pre_edit.get("attributes"),
            )
        except _pm.ProductMasterError as err:
            raise HTTPException(status_code=err.status, detail=err.message) from err
        # The website page (owner 2026-10-19) is the model's and lives on the
        # spine: this door edits the catalogue copy only, so it never moves it.
        _pre_attrs = (pre_edit.get("attributes") or {}) if isinstance(pre_edit, dict) else {}
        _page_moved = [
            k for k in ("web_page_type", "preorder_ship_days")
            if k in (product.attributes or {}) and str(merged_attrs.get(k) or "") != str(_pre_attrs.get(k) or "")
        ]
        if _page_moved:
            raise HTTPException(
                status_code=422,
                detail="The website page is set on the product (Edit Product, on the model), not here.",
            )
        existing["attributes"] = merged_attrs
        if gtin_written:
            # The twin's top-level barcode fields follow (the same projection
            # as the spine door's mirror).
            existing.update(_pm.twin_barcode_fields(merged_attrs.get("gtin")))
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

    # Model families (G1): THE variant guard (product_master) on what this
    # save carries -- a colour/size's model-level fields and category are
    # edited on its model. Read before any write; an unreadable spine is not
    # waved through.
    if product.attributes or category_changed:
        try:
            from ..dependencies import get_product_repository

            _frepo = get_product_repository()
            _fspine = _spine_of(_frepo, pre_edit) if _frepo is not None else None
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=503, detail=_SPINE_UNCHECKED) from exc
        if _fspine:
            _carried = {
                k: (existing.get("attributes") or {}).get(k) for k in (product.attributes or {})
            }
            try:
                _pm.assert_variant_model_fields(
                    _fspine,
                    _carried,
                    category=existing.get("category") if category_changed else None,
                )
            except _pm.ProductMasterError as err:
                raise HTTPException(status_code=err.status, detail=err.message) from err

    # The code the spine (and a size variant's row) must take; None = this
    # save moved no barcode, so it writes none: the twin is never "the newer
    # copy" (a Manage Barcode edit lands on the spine first).
    spine_gtin = (
        (existing.get("attributes") or {}).get("gtin") or "" if gtin_written else None
    )

    # Explicit operator name wins over the best-effort title regen for THIS
    # save (hence applied AFTER the attributes block; per-save only -- the
    # regen above already no-ops for imported long-form categories).
    if product.name is not None:
        _name = product.name.strip()
        if _name:
            existing["name"] = _name
            existing["title"] = _name

    if product.tags is not None:
        # ONE spelling: ecom.seo.tags, the list the Shopify push reads. This
        # used to write a top-level `tags` nothing pushed (sync audit gap #4).
        _pm.set_twin_tags(existing, product.tags)

    if product.description is not None:
        existing["description"] = product.description
    if product.hsn_code is not None:
        existing["hsn_code"] = product.hsn_code
    if product.gst_rate is not None:
        existing["gst_rate"] = product.gst_rate
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
        # THE offer-write check (pricing_caps.offer_write_problem). The cap is
        # judged when this edit CHANGES the MRP, offer or tier; an untouched
        # legacy offer never blocks an unrelated edit.
        _judge_cap = offer_terms_changed(pricing_patch, existing.get("pricing") or {})
        _dc = merged_pricing.get("discount_category") or existing.get("discount_category")
        _brand = existing.get("brand") or (existing.get("attributes") or {}).get("brand_name")
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
            problem = offer_write_problem(eff_mrp, eff_offer, _dc, _brand, _judge_cap)
            if problem:
                raise HTTPException(status_code=400, detail=problem)
            if verdict["reason"] == "INVALID_MRP":
                raise HTTPException(
                    status_code=400,
                    detail="Set a valid MRP before or with the offer price",
                )
        elif eff_mrp is not None and eff_offer is not None:
            # Patches NOT touching offer_price stay lenient (metadata/cost
            # fixes on price-less imports must keep working): the MRP >= offer
            # rule, and the cap when the MRP or the tier changes.
            problem = offer_write_problem(eff_mrp, eff_offer, _dc, _brand, _judge_cap)
            if problem:
                raise HTTPException(status_code=400, detail=problem)
        existing["pricing"] = merged_pricing
        # Imported (BVI) docs carry the price BOTH top-level (generic
        # consumers, incl. the promote payload) and nested under `pricing`
        # (orders' catalog fallback). Keep the two in sync on edit so a
        # review-form price fix is never shadowed by a stale top-level value.
        if "mrp" in existing and merged_pricing.get("mrp") is not None:
            existing["mrp"] = merged_pricing["mrp"]
        if "offer_price" in existing and merged_pricing.get("offer_price") is not None:
            existing["offer_price"] = merged_pricing["offer_price"]
    # A brand change -- attributes-only included -- is judged against the
    # standing offer (THE offer-write check), pricing patch or not.
    _brand_now = existing.get("brand") or (existing.get("attributes") or {}).get("brand_name")
    if _pm.normalise_identity_component(_brand_now) != _pm.normalise_identity_component(_brand_before):
        _p = existing.get("pricing") or {}
        _problem = offer_write_problem(
            _p.get("mrp", existing.get("mrp")),
            _p.get("offer_price", existing.get("offer_price")),
            _p.get("discount_category") or existing.get("discount_category"),
            _brand_now,
        )
        if _problem:
            raise HTTPException(status_code=400, detail=_problem)
    if product.images is not None:
        existing["images"] = product.images
    if product.is_active is not None:
        existing["is_active"] = product.is_active

    existing["updated_at"] = utc_now_iso()
    # Per-user attribution (multiple staff catalogue in parallel): WHO made
    # this edit and WHEN -- THE edit stamp (catalog_list.edit_stamp), the
    # fields the review list reads for an import with no spine yet.
    # created_by is never overwritten here.
    _edit = _catalog_list.edit_stamp(
        current_user.get("user_id"),
        current_user.get("full_name") or current_user.get("username"),
    )
    existing.update(_edit)

    # Spine and twin hold ONE GTIN (Manage Barcode, the till and the stock page
    # read the spine's). Written FIRST, after every check: a refused write
    # 503s with NOTHING saved, so the retry -- the same payload and updated_at
    # -- redoes all of it, the take-down included. NOT fail-soft: a spine that
    # silently kept its old code is worse than a save that says so. A twin
    # with no LINKED spine keeps the code alone, and the answer says so.
    _pr = _spine_id = None
    try:
        from ..dependencies import get_product_repository

        _pr = get_product_repository()
        _spine_id = _spine_product_id(_pr, existing)
    except Exception as exc:  # noqa: BLE001 -- fail-soft unless a barcode or weight moves
        if spine_gtin is not None or "weight" in product.model_fields_set:
            # Weight too: unresolved, it would land on the twin alone while
            # the answer said "saved" -- the very strand this door fixed.
            # "Could not look" is not "no linked spine": saving the twin alone
            # would split the two copies for good. Refused, nothing saved.
            raise HTTPException(status_code=503, detail=_SPINE_UNCHECKED) from exc
        logger.warning(
            "[CATALOG] spine lookup skipped for %s", product_id, exc_info=True
        )
    # WEIGHT (grams) lives on the SPINE -- the copy the edit page, the push and
    # this door's own reads (_stamp_spine_weight) all use; it travels in the
    # spine sync below. Only a twin with no spine (an import awaiting promote)
    # keeps it itself, and promote carries it across. It used to land on the
    # twin alone, where neither the edit page nor the spine ever saw it.
    weight_sent = "weight" in product.model_fields_set
    new_weight = product.weight or None  # 0 / null = no weight (a clear)
    if weight_sent and not _spine_id:
        if new_weight:
            existing["weight"] = new_weight
        else:
            existing.pop("weight", None)
    message = "Product updated successfully"
    barcode_token = uuid.uuid4().hex
    spine_before = size_before = None
    size_collection = None

    def undo_refused_barcode():
        """A refused catalogue edit must not leave its barcode on the till
        or the size row. Token guards preserve any newer barcode writer."""
        try:
            winner = _get_catalog_product(product_id)
            if winner is None:
                raise LookupError("catalogue winner could not be read")
            copies = []
            if spine_before is not None:
                copies.append((_pr.collection, {"product_id": _spine_id}, spine_before, True))
            if size_before is not None:
                copies.append((size_collection, {"sku": existing.get("sku")}, size_before, False))
            for collection, key, before, is_spine in copies:
                _pm.undo_catalogue_barcode_write(
                    collection, key, before, barcode_token, spine=is_spine,
                    winning_attributes=winner.get("attributes"),
                    previous_attributes=pre_edit.get("attributes"),
                )
        except Exception as exc:  # noqa: BLE001 - never claim clean refusal after failed repair
            logger.error("[CATALOG] refused barcode edit could not be restored for %s", product_id, exc_info=True)
            raise HTTPException(
                status_code=503,
                detail="This catalogue edit was not saved, but its barcode copies "
                "could not be restored. Reload the item and check its barcode before retrying.",
            ) from exc

    if spine_gtin is not None:
        # A size with no website row: refused BEFORE the spine write, or the
        # spine would take the code while the twin (refused below) never can.
        if _is_variant_of(existing):
            try:
                size_db = _get_db()
                _pm.assert_size_row(existing.get("sku"), size_db)
                size_collection = size_db.get_collection("catalog_variants")
                size_before = deepcopy(size_collection.find_one({"sku": existing.get("sku")}))
            except _pm.ProductMasterError as err:
                raise HTTPException(status_code=err.status, detail=err.message) from err
        if _spine_id:
            try:
                spine_before = deepcopy(_pr.find_by_id(_spine_id))
                _pm.write_spine_gtin(_pr, _spine_id, spine_gtin, write_token=barcode_token)
            except _pm.ProductMasterError as err:
                undo_refused_barcode()
                raise HTTPException(status_code=err.status, detail=err.message) from err
        else:
            message = (
                "Saved on the catalogue copy only: no billing product is linked "
                "to it, so the till and Manage Barcode do not have this barcode."
            )
        # A SIZE VARIANT's barcode reaches the website on its catalog_variants
        # ROW (the parent's push), so it lands there too, before the twin save
        # and NOT fail-soft -- as Manage Barcode: a size left on the old code is
        # a failed save. Refused, the twin is not saved, so the retry (the same
        # payload and updated_at) redoes it.
        # A False here is a row deleted since the check above: a failed size
        # write too.
        if _is_variant_of(existing):
            try:
                if not _pm._mirror_variant_row_update(
                    current=existing,
                    patch={"attributes": {"gtin": spine_gtin}},
                    db=_get_db(),
                    write_token=barcode_token,
                ):
                    raise LookupError(f"no size row for {existing.get('sku')}")
            except Exception as exc:  # noqa: BLE001
                undo_refused_barcode()
                raise HTTPException(
                    status_code=503,
                    detail="The website copy of this size could not take the "
                    "barcode, so the catalogue copy was not saved. Please save again.",
                ) from exc

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
            if spine_gtin is not None:
                undo_refused_barcode()
            raise HTTPException(
                status_code=409,
                detail=(
                    "This item was changed by someone else - reload and "
                    "re-apply your fixes"
                ),
            )
    else:
        _save_catalog_product(to_write)
    if weight_sent and not new_weight and not _spine_id:
        # A cleared weight on a spine-less import: the full-doc $set above
        # cannot remove a key.
        _twins = _catalog_coll()
        if _twins is not None:
            _twins.update_one({"id": product_id}, {"$unset": {"weight": ""}})
        else:
            CATALOG_PRODUCTS.get(product_id, {}).pop("weight", None)

    # Compact field-classified audit row (cataloguing scorecard corrections):
    # local mirror of the spine PUT's twin in products.update_product -- keep
    # the two in sync (no shared helper: routers deliberately don't import each
    # other). before/after carry ONLY the top-level keys the CALLER's patch
    # touched, with light values (heavy payloads elided to "[changed]") -- the
    # scorecard classifier reads keys, not values, so a pricing-only edit
    # through this door is now correctly NOT counted as a correction (this
    # retires the field-blind "approximate" bucket). expected_updated_at is a
    # concurrency token, never a field change. Fail-soft: an audit hiccup must
    # never block the catalog save.
    try:
        _changed_keys = []
        if product.category is not None:
            _changed_keys.append("category")
        if product.attributes:
            _changed_keys.append("attributes")
        if product.name is not None and product.name.strip():
            _changed_keys.append("name")
        if product.tags is not None:
            _changed_keys.append("tags")
        if product.description is not None:
            _changed_keys.append("description")
        if product.hsn_code is not None:
            _changed_keys.append("hsn_code")
        if product.gst_rate is not None:
            _changed_keys.append("gst_rate")
        if weight_sent:
            _changed_keys.append("weight")
        if product.pricing is not None:
            _changed_keys.append("pricing")
        if product.images is not None:
            _changed_keys.append("images")
        if product.is_active is not None:
            _changed_keys.append("is_active")
        if _changed_keys:
            from ..dependencies import get_audit_repository as _get_audit_repo

            _audit_repo = _get_audit_repo()
            if _audit_repo is not None:
                _HEAVY = ("attributes", "images", "description")
                _before: Dict[str, Any] = {}
                _after: Dict[str, Any] = {}
                for _k in _changed_keys:
                    if _k in _HEAVY:
                        _before[_k] = "[changed]"
                        _after[_k] = "[changed]"
                    else:
                        _before[_k] = pre_edit.get(_k)
                        _after[_k] = existing.get(_k)
                _audit_repo.create(
                    {
                        "action": "product.updated",
                        "actor": current_user.get("user_id"),
                        "user_id": current_user.get("user_id"),
                        # Same display-name convention as the updated_by_name
                        # stamp above.
                        "actor_name": current_user.get("full_name")
                        or current_user.get("username"),
                        "entity_type": "product",
                        # The catalog doc id == the spine product_id (the
                        # scorecard's creators map matches on it).
                        "entity_id": product_id,
                        "timestamp": datetime.now(),
                        "ts": utc_now_iso(),
                        "source": "catalog_put",
                        "before": _before,
                        "after": _after,
                    }
                )
    except Exception:  # noqa: BLE001
        logger.warning(
            "[CATALOG] update audit row skipped for %s", product_id, exc_info=True
        )

    # Products-convergence: keep the billing SPINE in sync with this catalog edit
    # (the spine resolved by _spine_of). Propagate price / tier / gst /
    # active so POS bills the updated values. discount_category is upper-cased to
    # match what the cap resolver expects. Keyed on what THIS PUT carried, never
    # on the merged twin: a door-created twin holds no is_active at all (71 of
    # 77 prod twins), so the old `existing.get("is_active", True)` put a retired
    # spine back on sale at POS on ANY copy edit -- and the twin's pricing /
    # hsn / gst can drift from a spine moved by a path that did not mirror
    # (pre-#1029 spine PUTs, GRN cost updates, scripts), so the same copy edit
    # wrote the twin's stale MRP onto the billing spine. Both were latent while
    # the write matched no spine. A category change is the one case where the
    # PUT moves hsn / gst without naming them (re-derived above): it travels.
    # Fail-soft: a spine-sync error never breaks the catalog save.
    try:
        if _spine_id:
            _sent = product.pricing.model_dump(exclude_none=True) if product.pricing else {}
            _pricing = existing.get("pricing") or {}
            _patch: Dict[str, Any] = {
                k: _pricing.get(k) for k in ("mrp", "offer_price", "cost_price") if k in _sent
            }
            if "discount_category" in _sent:
                _tier = _pricing.get("discount_category")
                _patch["discount_category"] = _tier.upper() if isinstance(_tier, str) else _tier
            if product.hsn_code is not None or category_changed:
                _patch["hsn_code"] = existing.get("hsn_code")
            if product.gst_rate is not None or category_changed:
                _patch["gst_rate"] = existing.get("gst_rate")
            if product.is_active is not None:
                _patch["is_active"] = existing.get("is_active")
            if new_weight:
                _patch["weight"] = new_weight
            _patch = {k: v for k, v in _patch.items() if v is not None}
            # A person's change to the product, whatever it touched: the
            # spine records who and when (the same stamp as the twin above),
            # so the Catalog page and the review list read ONE last edit.
            _patch.update(_edit)
            _pr.update(_spine_id, _patch)
            if weight_sent and not new_weight:
                _pr.collection.update_one({"product_id": _spine_id}, {"$unset": {"weight": ""}})
    except Exception:  # noqa: BLE001
        logger.warning(
            "[CATALOG] spine sync on update skipped for %s", product_id, exc_info=True
        )
        if weight_sent:
            # The weight has no other copy: say so rather than drop it quietly.
            message = "Saved, but the weight could not be saved. Please save again."
    # Sync audit gap #2 (owner, 2026-09-06): is_active off -> take it OFF
    # Shopify; back on -> queue it for the next press / live sync. The ONE
    # rule lives in services/online_delist; runs AFTER the save (the full-doc
    # $set above would clobber the take-down's own ecom write-back) AND AFTER
    # the spine mirror above (recheck round 2): the spine's is_active is the
    # only off-sale marker the quantity rule reads, and the delist row now
    # refuses to be green while it is still active -- so the marker goes first
    # and the take-down second, the order the DELETE door already uses.
    # Fail-soft: the catalog save stands even if Shopify says no.
    if product.is_active is not None:
        await _delist.on_active_flip(
            _get_db(),
            to_write,
            was_active=pre_edit.get("is_active", True),
            now_active=product.is_active,
            actor=current_user,
        )

    # A SIZE VARIANT (variant-of rule, owner 2026-09-06) owns no listing: its
    # price and barcode reach the website through its catalog_variants ROW on
    # the PARENT's price push (product_input._resolve_variant_pricing reads the
    # row, never this twin), and the engine recompute below finds no rows for
    # a child twin (its row's parent_product_id is the PARENT). So the drawer's
    # mrp edit goes through the ONE row writer -- the same one the spine PUT's
    # mirror uses -- which also re-derives the online price and queues the
    # PARENT. Without it the edit was silently inert on the website. (Its
    # barcode went to the row above, before the save.) Fail-soft: the catalog
    # save stands.
    if (
        _is_variant_of(existing)
        and product.pricing is not None
        and product.pricing.mrp is not None
    ):
        try:
            _pm._mirror_variant_row_update(
                current=existing, patch={"mrp": product.pricing.mrp}, db=_get_db()
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "[CATALOG] variant row mirror skipped for %s",
                product_id,
                exc_info=True,
            )

    # Online discount engine: re-derive the ONLINE storefront price after an edit
    # (MRP / brand / category changes can move the winning rule). ADDITIVE +
    # FAIL-SOFT; ONLINE-only (never touches the in-store offer_price). DARK.
    try:
        from ..services import online_discount_engine as _ode

        _ode.recompute_online_price(existing, db=_get_db())
    except Exception:  # noqa: BLE001 -- online pricing must never break the update
        logger.warning(
            "[CATALOG] online price recompute skipped for %s", product_id, exc_info=True
        )

    # The answer carries the weight the screens read (the spine's), so the
    # review editor re-seeds from it.
    reply = dict(mask_cost(existing, current_user, "product"))
    _stamp_spine_weight([reply])
    return {
        "product": reply,
        "message": message,
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
        # ecom.seo.tags (the door-written spelling), else the BVI import's
        # top-level `tags` -- product_master.twin_tags is the one reader.
        "tags": _pm.twin_tags(doc),
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
        # The identity rule too: 'CARRERA 3001/S' is the 3001/S already here.
        family = _pm.model_family_query(doc.get("category"), brand, model)
        if family:
            or_terms.append(family)
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
    _refuse_ordered_draft(doc)
    # Approving puts it on sale at the till: only Restore brings an archived
    # product back (catalog_list.switches_on_archived), dry run included.
    if _catalog_list.switches_on_archived(doc, True):
        raise HTTPException(status_code=409, detail=_catalog_list.USE_RESTORE)

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
        # One product per manufacturer GTIN: Approve is a create door too.
        _pm.assert_gtin_free(
            (spine.get("attributes") or {}).get("gtin"), repo, product_id
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
    #
    # This write deliberately BYPASSES _save_catalog_product, so it never
    # queues the row for the Online Store push -- correctly. needs_review /
    # pos_ready / promoted_* are internal review workflow, in no pushed Shopify
    # payload. The one field here that IS pushed content is a newly minted
    # `sku`, and it needs no flag: a product only reaches promote from the
    # review queue, where it is still dirty from creation (born dirty in
    # product_master._build_pim_doc / the catalog create door), so the minted
    # sku rides along on the first push that ever happens.
    stamp: Dict[str, Any] = {
        "needs_review": False,
        "pos_ready": True,
        "promoted_at": utc_now_iso(),
        "promoted_by": current_user.get("user_id"),
        "updated_at": utc_now_iso(),
    }
    if minted_sku:
        stamp["sku"] = minted_sku
    # The twin's manufacturer barcodes (gtin / upc, in any spelling) become
    # EXACTLY what the spine stored through the door's guard -- folded,
    # sanitised, junk with no box dropped -- in this same write: one value on
    # both copies, so find_twin_by_barcode and the spine agree.
    _doc_attrs = doc.get("attributes")
    if isinstance(_doc_attrs, dict):
        _spine_attrs = spine.get("attributes") or {}
        _twin_attrs = {
            k: v for k, v in _doc_attrs.items() if not _pm.manufacturer_barcode_key(k)
        }
        _twin_attrs.update(
            {k: v for k, v in _spine_attrs.items() if _pm.manufacturer_barcode_key(k)}
        )
        if _twin_attrs != _doc_attrs:
            stamp["attributes"] = _twin_attrs
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
                    "ts": utc_now_iso(),
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
        invalidate_product_lists()
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

    # The product it copies decides everything below (a receipt holding it,
    # whether it is an ordered draft): unreadable, nothing is deleted -- a
    # read error never passes for "no product" (fail loudly).
    try:
        from ..dependencies import get_product_repository

        spine_doc = _spine_of(get_product_repository(), product)
    except Exception:  # noqa: BLE001
        logger.error("[CATALOG] spine lookup on delete failed for %s", product_id, exc_info=True)
        raise HTTPException(
            status_code=503,
            detail="Could not read this item's product record, so it was not deleted. Try again.",
        )
    refused = _archive_refusal(spine_doc)
    if refused is not None:
        raise refused
    await _archive_product(
        product,
        current_user,
        spine_id=(spine_doc or {}).get("product_id"),
        discarded_draft=_pm.is_ordered_draft(spine_doc),
    )
    return {"message": "Product deleted successfully"}


def _archive_refusal(spine_doc: Optional[Dict]) -> Optional[HTTPException]:
    """Why a product cannot be archived now (audit C1), as the refusal to
    raise -- None when it can. Both archive doors (the single Delete and the
    Catalog page's bulk Delete) ask it, so neither discards an item behind a
    receipt's back.

      * A receipt holding units of it: its tasks, the receipt and the draft
        would all be left pointing at a deleted product. The advice per
        receipt follows why it holds the item.
      * An ordered draft an open order line still expects, or a receipt still
        waits on (draft_discard_blockers): discarded, the box would arrive
        held behind a deleted product, its task pointing at a Needs-review
        queue it has left. The refusal names, per blocker, only an action the
        server allows; orders it cannot read refuse (503) -- fail loudly."""
    spine_id = (spine_doc or {}).get("product_id")
    if spine_id:
        from .vendors.grn_accept import held_receipts

        holding = held_receipts(spine_id)
        if holding:
            # A line beyond its order is settled by the store manager's 'Not
            # received' (or 'Add to stock'), whatever else that receipt
            # shelved -- finishing the item does nothing for it (R1-14).
            def _why(g: dict) -> str:
                number = g.get("grn_number") or g.get("grn_id")
                reasons = {
                    (ln or {}).get("reason")
                    for ln in g.get("unresolved_lines") or []
                    if (ln or {}).get("product_id") == spine_id
                }
                if reasons == {"over_order"}:
                    return (
                        f"Receipt {number} holds more of this item than its order "
                        "asked for: the store manager presses 'Not received' in "
                        "Receive Goods (or 'Add to stock' if the vendor really "
                        "sent them)."
                    )
                return (
                    f"Receipt {number} is holding units of this item. Finish the "
                    "item in the product editor -- or, if none of that receipt's "
                    "units went on the shelf and they are going back, void it in "
                    "Receive Goods."
                )

            return HTTPException(
                status_code=409,
                detail=" ".join(_why(g) for g in holding) + " Then delete the item.",
            )
    if _pm.is_ordered_draft(spine_doc):
        from .vendors.grn_accept import draft_discard_blockers

        try:
            blockers = draft_discard_blockers(spine_id) if spine_id else None
        except Exception:  # noqa: BLE001 - unreadable reads as "cannot check"
            logger.error("[CATALOG] discard check could not read for %s", spine_id, exc_info=True)
            blockers = None
        if blockers is None:
            return HTTPException(
                status_code=503,
                detail=(
                    "Could not check the purchase orders and receipts for this "
                    "draft, so it was not deleted. Try again."
                ),
            )
        if blockers:
            return HTTPException(
                status_code=409,
                detail="This draft was not deleted. " + " ".join(blockers),
            )
    return None


async def _archive_product(
    product: Optional[Dict],
    actor: Dict,
    *,
    spine_id: Optional[str] = None,
    discarded_draft: bool = False,
) -> None:
    """THE archive rule (soft delete), for the single Delete door and the
    Catalog page's bulk Delete (archive): the catalog twin and its spine go
    is_active False with deleted_at / deleted_by, and a live listing is taken
    down. Never a hard delete. `product` is the twin (None when the spine has
    none); `spine_id` skips the twin -> spine lookup when the caller has it.
    Both doors ask _archive_refusal first.

    `discarded_draft` (an ordered draft, product_master.is_ordered_draft):
    it leaves the Needs-review queue and is no longer provisional -- finishing
    a provisional draft switches it on (restamp_on_update), a deleted one must
    stay off -- and carries the discard mark, so a manager typing the same
    item on a new order gets THIS draft back (purchase_orders,
    revive_discarded_draft): its identity key is unique, so a second row for
    it can never be made. The asks for cataloguing naming it close, and an
    audit row records who discarded it."""
    # Model families (G1): a model's LEAD that is on the website is taken off
    # it FIRST -- its other colours and sizes move to a new lead only once
    # the listing is down. Refused (nothing written) when the take-down did
    # not land, so no variant is ever left under a listing still on sale.
    #
    # G2 (owner 2026-10-18): when another colour/size of the model is still
    # in the catalogue, the LISTING is handed over instead -- it stays on the
    # website under the next first-catalogued colour (same Shopify product,
    # same handle; product_master._hand_over_listing) and only the archived
    # lead's own variant is taken off sale, after the hand-over. The whole
    # listing comes down first only when nothing is left to sell on it.
    delisted_first = False
    hand_over = False
    try:
        from ..dependencies import get_product_repository as _lead_repo

        spine_id = spine_id or _spine_product_id(_lead_repo(), product)
        members = (
            list(_get_db().get_collection("products").find({"variant_of": spine_id})) if spine_id else []
        )
        leads_variants = bool(members)
        hand_over = any(not m.get("deleted_at") for m in members)
    except Exception:  # noqa: BLE001
        logger.warning("[CATALOG] model check on delete skipped for %s", spine_id, exc_info=True)
        leads_variants = False
    on_website = product is not None and bool((product.get("ecom") or {}).get("shopify_product_id"))
    hand_over = hand_over and on_website
    if leads_variants and on_website and not hand_over:
        taken = await _delist.delist_if_live(_get_db(), product, reason="deleted", actor=actor)
        if not (taken and taken.get("ok")):
            raise HTTPException(
                status_code=409,
                detail=(
                    "This model is on the website and its listing could not be taken "
                    "down, so nothing was deleted (its other colours and sizes would "
                    "be left under it). Try again in a minute."
                ),
            )
        product = _get_catalog_product(product.get("id")) or product
        delisted_first = True

    deleted_at = datetime.now(timezone.utc).isoformat()
    if product is not None:
        product.pop("_id", None)  # a twin read raw from Mongo; never $set _id
        if not product.get("deleted_at"):
            # Restore puts it back exactly as it was (_restore_product).
            product[ACTIVE_BEFORE_ARCHIVE] = product.get("is_active", True) is not False
        if discarded_draft:
            product["needs_review"] = False
        product["is_active"] = False
        product["deleted_at"] = deleted_at
        product["deleted_by"] = actor.get("user_id")

        # NOT a catalogue edit: `is_active` / deleted_at / deleted_by appear in
        # NO pushed Shopify payload (build_product_input reads title,
        # ecom.status / handle / seo, brand, category, description,
        # attributes) -- queuing here would send a push that changes nothing
        # on the storefront. Removing a live listing has its own path
        # (shopify_push.push_product_delist).
        _save_catalog_product(product, mark_dirty=False)
        # NEVER PUBLISHED -> OUT OF THE WEBSITE QUEUE (owner 2026-10-29): an
        # archived product is never first-published (listing.
        # first_publish_not_ready), so a queued one only printed a red
        # "archived -- stays queued" line on every sync. The edit_rev rule
        # (catalog_ecom.unqueue_in). A listing on the website is left to the
        # take-down below, whose write-back owns its flag.
        ecom = product.get("ecom") or {}
        coll = _catalog_coll()
        if coll is not None and ecom.get("locally_modified") and not ecom.get("shopify_product_id"):
            from api.services.catalog_ecom import EDIT_REV, unqueue_in

            unqueue_in(coll, product["id"], ecom.get(EDIT_REV))

    # Products-convergence: deactivate the SPINE twin too (shared id) so a
    # soft-deleted catalog product can't still be sold at POS, and stamp it
    # deleted there too: is_active False alone reads like a provisional buy
    # not switched on yet (reorder_policy.discontinued). Fail-soft.
    try:
        from ..dependencies import get_product_repository

        _pr = get_product_repository()
        spine_id = spine_id or _spine_product_id(_pr, product)
        if _pr is not None and spine_id:
            _patch: Dict[str, Any] = {
                "is_active": False,
                "deleted_at": deleted_at,
                "deleted_by": actor.get("user_id"),
            }
            if discarded_draft:
                _patch.update(provisional=False, discarded_draft=True, discarded_at=deleted_at)
            _before = _pr.find_by_id(spine_id) or {}
            if not _before.get("deleted_at"):
                _patch[ACTIVE_BEFORE_ARCHIVE] = _before.get("is_active", True) is not False
            # Not a catalogue edit either: no updated_by, so the last edit
            # stays where it was (ProductRepository.update).
            _pr.update(spine_id, _patch)
    except Exception:  # noqa: BLE001
        logger.warning(
            "[CATALOG] spine deactivate on delete skipped for %s",
            (product or {}).get("id") or spine_id,
            exc_info=True,
        )
    if discarded_draft and spine_id:
        from .vendors.grn_accept import close_catalogue_asks

        close_catalogue_asks(spine_id, "The item was discarded from the catalogue.")
        # The discard is a decision a later re-order undoes
        # (revive_discarded_draft audits that side): record who made it.
        try:
            from ..dependencies import get_audit_repository

            _audit = get_audit_repository()
            if _audit is not None:
                _audit.create(
                    {
                        "action": "product.draft_discarded",
                        "entity_type": "product",
                        "entity_id": spine_id,
                        "user_id": actor.get("user_id"),
                        "detail": {
                            "sku": (product or {}).get("sku"),
                            "twin_id": (product or {}).get("id"),
                        },
                    }
                )
        except Exception:  # noqa: BLE001 - the audit never undoes the delete
            logger.warning("[CATALOG] discard audit failed for %s", spine_id, exc_info=True)

    # Sync audit gap #2 (owner, 2026-09-06): a deleted product must stop being
    # SOLD ONLINE too. The ONE take-down rule (services/online_delist ->
    # the existing push_product_delist; DRAFT on Shopify, gid kept). Runs
    # AFTER the write above on purpose: the soft delete keeps the row and its
    # gid, and the full-doc $set would clobber the take-down's own ecom
    # write-back. Fail-soft: the delete stands even if Shopify says no.
    if product is not None and not delisted_first and not hand_over:
        await _delist.delist_if_live(_get_db(), product, reason="deleted", actor=actor)
    # Model families (G1): an archived LEAD hands the model to its next
    # first-catalogued variant -- after the take-down above (a lead on the
    # website was taken down first, or nothing was deleted). Fail-soft: the
    # archive stands.
    handed_over = False
    if spine_id:
        try:
            from ..dependencies import get_audit_repository

            handed_over = bool(
                _pm.promote_new_lead(
                    _get_db(), spine_id, actor=actor.get("user_id"), audit_repo=get_audit_repository()
                )
            )
        except Exception:  # noqa: BLE001
            logger.warning("[CATALOG] model lead hand-over skipped for %s", spine_id, exc_info=True)
    if hand_over and product is not None:
        # G2: the listing now belongs to the new lead -- the archived lead's
        # OWN variant comes off sale (DENY + 0, never deleted). A hand-over
        # that did not happen leaves the lead holding the listing: then the
        # whole listing comes down, exactly as when nothing is left on it.
        fresh = _get_catalog_product(product.get("id")) or product
        await _delist.delist_if_live(
            _get_db(), fresh if handed_over else product, reason="deleted", actor=actor
        )
    # Every archive door: a cached Catalog / till list page must not keep
    # listing it until the cache runs out.
    invalidate_product_lists()


# Whether the product was switched on when it was archived (archive writes
# it, restore reads and clears it): a restore puts it back as it was.
ACTIVE_BEFORE_ARCHIVE = "active_before_archive"

RESTORED_AS_BEFORE = (
    "Back in the catalogue switched off, as it was before it was archived."
)
RESTORED_SWITCHED_OFF = (
    "Back in the catalogue switched off: it is on a listing that is live on "
    "the website, so switching it on puts it back on sale there."
)
RESTORED_UNCHECKED = (
    "Back in the catalogue switched off: the website could not be checked, "
    "so it was not switched on."
)


def _live_skus(skus: List[str]) -> Optional[set]:
    """THE "is this SKU on sale on the website" reader for the archive and
    restore doors (shopify_push.inventory.skus_on_sale_online: its listing is
    up -- a size judged by its PARENT's, a failed take-down still up -- even
    where the stock writer could not address it). None when the read failed:
    unknown, never "nothing is live"."""
    from ..services.shopify_push.inventory import skus_on_sale_online

    keys = [k for k in skus if k]
    if not keys:
        return set()
    try:
        return skus_on_sale_online(_get_db(), keys, strict=True)
    except Exception:  # noqa: BLE001
        logger.warning("[CATALOG] live-listing read failed", exc_info=True)
        return None


def _restore_refusal(repo, spine: Optional[Dict], twin: Optional[Dict] = None) -> Optional[str]:
    """Why an archived product may not come back (None: it may), asked per
    row so a bulk restore sees each earlier restore.

      * THE create door's "we already have this" rule
        (product_master.identity_conflict, over the key the create rule gives
        the row -- never its stored key, which an archived twin may have given
        up) names ANOTHER row, live or archived. A live one would be a
        duplicate on sale; an archived one holds the key, so this row would
        come back without one -- restore that one instead. An eye-size item
        with no eye size whose frame is catalogued by eye size: EYE_SIZE_NEEDED.
      * Its barcode (_restore_barcode_refusal), for every row -- keyless,
        unreadable or a spineless catalogue twin too."""
    if spine:
        why = _restore_identity_refusal(repo, spine)
        if why:
            return why
    return _restore_barcode_refusal(repo, spine, twin)


def _restore_identity_refusal(repo, spine: Dict) -> Optional[str]:
    try:
        probe = _pm.identity_spine(spine)
    except _pm.ProductMasterError:
        return None  # a stored value that is not a number: keyless as before
    if not probe.get("identity_key"):
        return None
    # The create door's SKU check too (never its own twin: self_id).
    probe["sku"] = spine.get("sku")
    err = _pm.identity_conflict(probe, repo, self_id=spine.get("product_id"))
    code = getattr(err, "code", None)
    if code == "DUPLICATE_PRODUCT":
        other = err.conflict or {}
        try:
            row = repo.find_by_id(other.get("product_id")) if other.get("product_id") else None
        except Exception:  # noqa: BLE001 - the refusal stands either way
            row = None
        if row is not None and row.get("deleted_at"):
            return (
                f"Not restored: the same product is archived as {other.get('sku')}. "
                "Restore that one instead (or edit this one so it is a different product)."
            )
        return (
            f"Not restored: the same product is already in the catalogue as {other.get('sku')}. "
            "Archive that one first, or edit this one so it is a different product."
        )
    if err is not None:
        return f"Not restored: {err.message}"
    return None


def _restore_barcode_refusal(repo, spine: Optional[Dict], twin: Optional[Dict]) -> Optional[str]:
    """Add product's own barcode rule (assert_gtin_free) over EVERY barcode
    the row and its catalogue twin carry (barcode, gtin, attributes.gtin on
    either), each distinct value -- only a code the website would publish
    (gtin.sanitise_gtin, the push's own rule: a junk stub, all zeros or a
    restricted in-store GS1 20-29 code is no public barcode, never a clash).
    Only a LIVE product or twin holds a code (live_only), so two
    archived copies sharing an EAN never block each other: once one is back,
    the other is refused here. A read failure is a plain refusal, never a
    guess."""
    own = (spine or {}).get("product_id") or (twin or {}).get("id")
    from ..services.gtin import sanitise_gtin

    try:
        codes: List[str] = []
        for doc in (spine or {}, twin or {}):
            attrs = doc.get("attributes") if isinstance(doc.get("attributes"), dict) else {}
            for value in (doc.get("barcode"), attrs.get("gtin"), doc.get("gtin")):
                code = sanitise_gtin(value)
                if code and code not in codes:
                    codes.append(code)
        for code in codes:
            _pm.assert_gtin_free(code, repo, own, live_only=True)
    except _pm.ProductMasterError as clash:
        return f"Not restored: {clash.message}"
    except Exception:  # noqa: BLE001 - unread is never "free"
        logger.warning("[CATALOG] restore barcode check could not read", exc_info=True)
        return "Not restored: could not check the barcode right now, try again."
    return None


def _restore_product(
    product: Optional[Dict],
    spine: Optional[Dict],
    actor: Dict,
    *,
    on_website: Optional[bool],
) -> Optional[str]:
    """THE way back from _archive_product (the Catalog page's Restore and the
    Needs-review drawer's): deleted_at / deleted_by cleared, the product put
    back EXACTLY as it was before the archive -- switched on only if it was
    on then (a product switched off before it was archived comes back
    switched off) -- and an audit row written. NOT re-published online
    (owner 2026-10-12) -- the twin is saved clean and no republish is queued,
    so not even the scheduled sync (which first-publishes QUEUED rows since
    the 2026-10-20 ruling) picks it up, and the take-down marker
    (ecom.taken_down_at) stays. One case cannot be both: a product whose SKU
    is on a listing live on the website (a size whose parent listing is up,
    or a take-down that failed). Its is_active is the only thing keeping it
    off sale there (online_stock_writeback lists an inactive spine at 0), so
    it comes back SWITCHED OFF and the returned note says why; `on_website`
    None (the read failed) does the same. `spine` None: an import with no
    spine (the twin alone). Not a catalogue edit."""
    spine_id = (spine or {}).get("product_id")
    # Archived before this was remembered: switched on, as restore did then.
    was_active = (spine or product or {}).get(ACTIVE_BEFORE_ARCHIVE) is not False
    active = was_active and on_website is False
    if product is not None:
        product.pop("_id", None)
        product["is_active"] = active
        product["deleted_at"] = None
        product["deleted_by"] = None
        product[ACTIVE_BEFORE_ARCHIVE] = None
        _save_catalog_product(product, mark_dirty=False)
    from ..dependencies import get_audit_repository, get_db, get_product_repository
    from ..services.name_resolver import actor_name

    _pr = get_product_repository()
    if _pr is not None and spine_id:
        back: Dict[str, Any] = {
            "is_active": active,
            "deleted_at": None,
            "deleted_by": None,
            ACTIVE_BEFORE_ARCHIVE: None,
        }
        # Keyed again by the create rule when its key is free (an archived
        # twin may have given its key up): a restored row is never keyless.
        try:
            key = _pm.identity_key_of(spine)
            if key and key != spine.get("identity_key") and _pr.find_by_identity_key(key) is None:
                back["identity_key"] = key
        except _pm.ProductMasterError:
            pass
        _pr.update(spine_id, back)
    try:
        audit_repo = get_audit_repository()
        if audit_repo is not None:
            audit_repo.create(
                {
                    "action": "product.restored",
                    "actor": actor.get("user_id"),
                    "user_id": actor.get("user_id"),
                    "actor_name": actor_name(get_db(), actor),
                    "entity_type": "product",
                    "entity_id": spine_id or (product or {}).get("id"),
                    "switched_on": active,
                    "timestamp": datetime.now(timezone.utc),
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "source": "catalog_bulk_restore",
                }
            )
    except Exception:  # noqa: BLE001
        logger.warning("[CATALOG] restore audit row skipped for %s", spine_id, exc_info=True)
    invalidate_product_lists()  # as _archive_product: no cached page keeps it hidden
    if active:
        return None
    if not was_active:
        return RESTORED_AS_BEFORE
    return RESTORED_SWITCHED_OFF if on_website else RESTORED_UNCHECKED


def _twins_or_503(repo, spines: List[Optional[Dict]]) -> List[Optional[Dict]]:
    """_twins_of for the bulk doors: a failed read stops the call before
    anything is written (an archive that missed the twin would leave its
    listing up on the website)."""
    try:
        return _twins_of(repo, spines)
    except Exception:  # noqa: BLE001
        logger.warning("[CATALOG] twin lookup for a bulk door failed", exc_info=True)
        raise HTTPException(
            status_code=503, detail="Could not read the catalogue right now. Try again in a minute."
        ) from None


class BulkProductIds(BaseModel):
    """The Catalog page's bulk doors: at most 200 spine product ids a call.
    `apply` False (the default) is the dry run the confirm dialog reads."""

    product_ids: List[str] = Field(..., min_length=1, max_length=200)
    apply: bool = False


def _in_stock_units(product_ids: List[str]) -> Optional[Dict[str, int]]:
    """Units still physically at the shops, per product (the one on-hand
    rule, reserved units included -- they are on a shelf). None when the
    read failed: unknown, never "no units" (as _live_skus)."""
    from ..services.item_events import on_hand_match

    if not product_ids:
        return {}
    db = _get_db()
    out: Dict[str, int] = {}
    try:
        if db is None:
            raise RuntimeError("the database is not reachable")
        for unit in db["stock_units"].find(
            {"$and": [{"product_id": {"$in": product_ids}}, on_hand_match(include_reserved=True)]},
            {"product_id": 1},
        ):
            pid = unit.get("product_id")
            out[pid] = out.get(pid, 0) + 1
    except Exception:  # noqa: BLE001
        logger.warning("[CATALOG] stock read for the archive dialog failed", exc_info=True)
        return None
    return out


@router.post("/products/bulk-archive")
async def bulk_archive_products(
    body: BulkProductIds,
    current_user: dict = Depends(require_roles("ADMIN")),
):
    """Delete (archive) up to 200 products from the Catalog page -- the SAME
    soft delete as DELETE /catalog/products/{id} (_archive_product). The dry
    run (apply=false) tells the confirm dialog which products still have
    units at the shops and which are live on the website (they are taken
    down). Per-product results: done / refused with the reason."""
    from ..dependencies import get_product_repository

    repo = get_product_repository()
    if repo is None:
        raise HTTPException(status_code=503, detail="The catalogue is not reachable")
    ids = list(dict.fromkeys(p for p in body.product_ids if p))
    spines = {p: repo.find_by_id(p) for p in ids}
    live_skus = _live_skus([s.get("sku") for s in spines.values() if s])
    if live_skus is None and not body.apply:
        # The dialog must never say "none of them is live" on a dead read.
        raise HTTPException(
            status_code=503,
            detail="Could not check the website right now. Try again in a minute.",
        )
    live = {
        pid for pid, s in spines.items() if s and live_skus and s.get("sku") in live_skus
    }
    stock = _in_stock_units(ids)
    if stock is None and not body.apply:
        # Nor "none of them has units left at the shops" on a dead read.
        raise HTTPException(
            status_code=503,
            detail="Could not check the stock at the shops right now. Try again in a minute.",
        )
    twins = _twins_or_503(repo, [spines[p] for p in ids]) if body.apply else [None] * len(ids)
    results = []
    for pid, twin in zip(ids, twins):
        spine = spines[pid]
        row: Dict[str, Any] = {
            "product_id": pid,
            "in_stock_units": None if stock is None else stock.get(pid, 0),
            "live_online": pid in live,
        }
        refused = _archive_refusal(spine) if spine and not spine.get("deleted_at") else None
        if spine is None:
            row.update(status="refused", reason="Product not found")
        elif spine.get("deleted_at"):
            row.update(status="refused", reason="Already archived")
        elif refused is not None:
            # The single Delete door's rule (audit C1): never behind a receipt.
            row.update(status="refused", reason=refused.detail)
        elif body.apply:
            try:
                await _archive_product(
                    twin, current_user, spine_id=pid, discarded_draft=_pm.is_ordered_draft(spine)
                )
                row["status"] = "done"
            except HTTPException as exc:
                # A model on the website whose listing did not come down (G1).
                row.update(status="refused", reason=exc.detail)
        else:
            row["status"] = "ready"
        if spine is not None:
            row["name"] = spine.get("name") or spine.get("model") or spine.get("sku")
        results.append(row)
    return {"applied": body.apply, "results": results}


@router.post("/products/bulk-restore")
async def bulk_restore_products(
    body: BulkProductIds,
    current_user: dict = Depends(require_roles("ADMIN")),
):
    """Restore archived products (_restore_product: back as they were before
    the archive, NOT re-published online) -- the ONE way back from an
    archive. An id is a spine product_id (Catalog page, Flags: Archived) or
    a catalog twin id (the Needs-review drawer), with or without a spine.
    Per-product results: done / refused with the reason."""
    from ..dependencies import get_product_repository

    repo = get_product_repository()
    if repo is None:
        raise HTTPException(status_code=503, detail="The catalogue is not reachable")
    ids = list(dict.fromkeys(p for p in body.product_ids if p))

    def _pair(pid: str):
        spine = repo.find_by_id(pid)
        if spine is not None:
            return spine, None
        twin = _get_catalog_product(pid)
        sid = _spine_product_id(repo, twin)
        return (repo.find_by_id(sid) if sid else None), (dict(twin) if twin else None)

    try:
        pairs = {p: _pair(p) for p in ids}
    except Exception:  # noqa: BLE001 -- "could not look" is not "no spine"
        logger.warning("[CATALOG] spine lookup for bulk restore failed", exc_info=True)
        raise HTTPException(
            status_code=503, detail="Could not read the catalogue right now. Try again in a minute."
        ) from None
    # A spine id: its twin by the same rule the drawer's twin id took -- in the
    # dry run too, so the confirm dialog's answer is the one Restore gives.
    bare = [p for p in ids if pairs[p][0] is not None and pairs[p][1] is None]
    for p, twin in zip(bare, _twins_or_503(repo, [pairs[p][0] for p in bare])):
        pairs[p] = (pairs[p][0], twin)
    live_skus = (
        _live_skus([(s or t).get("sku") for s, t in pairs.values() if s or t])
        if body.apply
        else set()
    )
    results = []
    for pid in ids:
        spine, twin = pairs[pid]
        doc = spine or twin  # the spine is the truth when there is one
        if doc is None:
            results.append({"product_id": pid, "status": "refused", "reason": "Product not found"})
        elif not doc.get("deleted_at"):
            results.append({"product_id": pid, "status": "refused", "reason": "Not archived"})
        elif why := _restore_refusal(repo, spine, twin):
            results.append({"product_id": pid, "status": "refused", "reason": why})
        elif not body.apply:
            results.append({"product_id": pid, "status": "ready"})
        else:
            note = _restore_product(
                twin,
                spine,
                current_user,
                on_website=None if live_skus is None else doc.get("sku") in live_skus,
            )
            row: Dict[str, Any] = {
                "product_id": pid,
                "status": "done",
                "switched_on": note is None,
            }
            if note:
                row["note"] = note
            results.append(row)
    return {"applied": body.apply, "results": results}


def _purge_rows(ids: List[str], actor: Dict, apply: bool) -> List[Dict[str, Any]]:
    """THE permanent-delete door body (services/product_purge), for the
    single door and the Catalog page's bulk door. Per spine id: ready / done
    / refused with the reason and its HTTP code."""
    from ..dependencies import get_audit_repository, get_product_repository
    from ..services import product_purge

    repo = get_product_repository()
    if repo is None:
        raise HTTPException(status_code=503, detail="The catalogue is not reachable")
    spines = [repo.find_by_id(p) for p in ids]
    twins = _twins_or_503(repo, spines)
    db = _get_db()
    run_id = f"PURGE-{uuid.uuid4().hex[:12].upper()}"
    rows: List[Dict[str, Any]] = []
    for pid, spine, twin in zip(ids, spines, twins):
        row: Dict[str, Any] = {"product_id": pid}
        if spine is not None:
            row["sku"] = spine.get("sku")
            row["name"] = spine.get("name") or spine.get("model") or spine.get("sku")
        refused = product_purge.purge_refusal(db, spine, twin)
        if refused is not None:
            row.update(status="refused", code=refused[0], reason=refused[1])
        elif not apply:
            row["status"] = "ready"
        else:
            try:
                row["backup_id"] = product_purge.purge_product(
                    db, spine, twin, actor, run_id=run_id, audit_repo=get_audit_repository()
                )
                row["status"] = "done"
            except Exception:  # noqa: BLE001
                logger.error("[CATALOG] permanent delete failed for %s", pid, exc_info=True)
                row.update(
                    status="refused",
                    code=503,
                    reason="Could not delete it right now (nothing was lost: it is backed up "
                    "first). Try again in a minute.",
                )
        rows.append(row)
    if apply and any(r["status"] == "done" for r in rows):
        invalidate_product_lists()
    return rows


@router.delete("/products/{product_id}/permanent")
async def purge_catalog_product(
    product_id: str,
    current_user: dict = Depends(require_roles("SUPERADMIN")),
):
    """Delete an ARCHIVED product for good (owner 2026-10-24), SUPERADMIN only.
    Refused 409 (not archived / on Shopify / leads a model / any history) or
    503 (a check could not read) -- nothing deleted. Backed up first."""
    (row,) = _purge_rows([product_id], current_user, apply=True)
    if row["status"] != "done":
        raise HTTPException(status_code=row["code"], detail=row["reason"])
    return {"message": "Product deleted permanently", **row}


@router.post("/products/bulk-purge")
async def bulk_purge_products(
    body: BulkProductIds,
    current_user: dict = Depends(require_roles("SUPERADMIN")),
):
    """The Catalog page's 'Delete permanently' (Flags: Archived), SUPERADMIN
    only: the same rule as the single door, up to 200 spine ids. The dry run
    (apply=false) tells the confirm dialog which would be refused and why."""
    ids = list(dict.fromkeys(p for p in body.product_ids if p))
    return {"applied": body.apply, "results": _purge_rows(ids, current_user, body.apply)}


# ============================================================================
# CATALOGUE FLAGS ("needs work") -- services/catalog_flags
# ============================================================================


class RaiseFlagsRequest(BaseModel):
    product_ids: List[str] = Field(..., min_length=1, max_length=200)
    type: str
    note: Optional[str] = Field(default=None, max_length=500)
    # False = the Flag dialog's preview: who would get the tasks, nothing written.
    apply: bool = True


class FixFlagsRequest(BaseModel):
    product_id: str
    flag_ids: List[str] = Field(..., min_length=1, max_length=50)


@router.post("/flags")
async def raise_catalog_flags(
    body: RaiseFlagsRequest,
    current_user: dict = Depends(require_roles(*_flags.FLAGGER_ROLES)),
):
    """Flag products as needing work. ADMIN / SUPERADMIN / STORE_MANAGER only
    (its own capability, catalog:flag). One task per person who added the
    products. Flags never block the till or the website. apply=false is the
    dialog's preview of who gets the tasks (the same rule, nothing written)."""
    try:
        return _flags.raise_flags(
            body.product_ids, body.type, body.note, current_user, apply=body.apply
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/flags/fix")
async def fix_catalog_flags(
    body: FixFlagsRequest,
    current_user: dict = Depends(require_roles(*_flags.FIXER_ROLES)),
):
    """Mark fixed (the product edit page). The last fixed flag of a task
    completes that task."""
    try:
        return _flags.fix_flags(body.product_id, body.flag_ids, current_user)
    except LookupError:
        raise HTTPException(status_code=404, detail="Product not found")


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
    if adjustment < 0:
        # Taking stock DOWN is a write-off: ADMIN / SUPERADMIN only, at every
        # value (owner ruling 2026-08-25).
        from .inventory._shared import refuse_write_off_unless_admin

        refuse_write_off_unless_admin(current_user, "adjust stock down")

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
    product["updated_at"] = utc_now_iso()

    # NOT a catalogue edit: a stock movement. Quantity is never pushed from the
    # product payload (online qty is the derived allocation, written by the
    # stock write-back), so flagging here would queue a no-change push on every
    # sale and GRN.
    _save_catalog_product(product, mark_dirty=False)
    return {
        "product_id": product_id,
        "location_id": location_id,
        "previous_quantity": current_qty,
        "adjustment": adjustment,
        "new_quantity": new_qty,
        "total_quantity": product["inventory"]["total_quantity"],
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
    product["updated_at"] = utc_now_iso()
    # SYNC PATH -- deliberately does NOT queue the row. This write is sync
    # book-keeping (the retired-marker stamp), not a human catalogue edit.
    # Marking a row dirty from anything on the Shopify sync side is the
    # ping-pong hazard: push -> write-back marks dirty -> next sweep pushes it
    # again -> forever, against a LIVE storefront. Same reason
    # shopify_push._writeback_product only ever sets the flag to False.
    _save_catalog_product(product, mark_dirty=False)

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
        product["updated_at"] = utc_now_iso()
        # SYNC PATH -- same reason as the single sync above: never queue from
        # the Shopify sync side (ping-pong).
        _save_catalog_product(product, mark_dirty=False)
        synced += 1

    return {
        "synced_count": synced,
        "errors": errors,
        "retired": True,
        "message": "Shopify is managed by the Online Store app (BVI). "
        "IMS product->Shopify sync is retired; manage listings there.",
    }
