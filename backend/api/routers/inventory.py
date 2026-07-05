"""
IMS 2.0 - Inventory Router
===========================
Stock management, stock count/audit, aging analysis, barcode operations
"""

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field
from typing import List, Optional, Dict
from datetime import date, datetime, timedelta, timezone
import uuid
import logging

from .auth import get_current_user, require_roles
from ..services import power_grid
from ..services import barcode as barcode_svc
from ..services.reorder_policy import auto_reorder_disabled as _reorder_disabled
from ..dependencies import (
    get_stock_repository,
    get_product_repository,
    get_audit_repository,
    validate_store_access,
    can_access_store_scoped,
    resolve_store_scope,
)

logger = logging.getLogger(__name__)
router = APIRouter()

# Stock-manager roles permitted to drive the defective-unit quarantine lifecycle
# (mark / lift / print label). SUPERADMIN auto-passes via require_roles. This is
# DELIBERATELY narrower than _INVENTORY_ROLES -- a quarantine is a physical
# control decision (pull a defective unit off the sellable floor), reserved for
# the manager ladder, not catalog/workshop staff.
_STOCK_MANAGER_ROLES = (
    "ADMIN",
    "AREA_MANAGER",
    "STORE_MANAGER",
)

# The free-string status value a quarantined unit carries. NOT an enum / schema
# change (CORRECTIONS P0-6): stock_units.status is a free string, and every
# on-hand / sellable rollup uses an explicit AVAILABLE/RESERVED allowlist, so a
# QUARANTINED unit is excluded from POS, transfers and blind-count simply by not
# being in any allowlist.
STOCK_STATUS_QUARANTINED = "QUARANTINED"

# Allowed quarantine reasons (free-text fallback OTHER + notes). Kept here so the
# endpoint validates the dropdown the frontend shows.
_QUARANTINE_REASONS = {
    "DEFECTIVE",
    "SCRATCHED",
    "CUSTOMER_RETURN_DAMAGED",
    "QC_FAILED_WORKSHOP",
    "RECEIVED_DAMAGED",
    "OTHER",
}

# Roles permitted to mutate stock (add / count / scan / transfer / serials).
# Mirrors the inventory page route guard — the broadest role set any inventory
# write is reachable from in the UI — so this is zero-regression while still
# blocking the non-inventory roles (SALES_STAFF, SALES_CASHIER, CASHIER,
# OPTOMETRIST, ACCOUNTANT) from stock mutations. SUPERADMIN auto-passes.
_INVENTORY_ROLES = (
    "ADMIN",
    "AREA_MANAGER",
    "STORE_MANAGER",
    "CATALOG_MANAGER",
    "WORKSHOP_STAFF",
)


# ============================================================================
# SCHEMAS
# ============================================================================


class StockAddRequest(BaseModel):
    product_id: str
    # GENEROUS upper bound: /stock/add mints ONE serialized row per unit in a
    # `for _ in range(quantity)` loop (each iteration = a counter call + a DB
    # insert), so an unbounded quantity (fat-finger or malicious 1e9) floods the
    # DB and hangs the worker. 10k is far above any real single-SKU intake but
    # caps the loop. Mirrors the orders.py C-3 line-quantity guard.
    quantity: int = Field(..., ge=1, le=10000)
    location_code: Optional[str] = None
    batch_code: Optional[str] = None
    lot: Optional[str] = None  # alias accepted alongside batch_code (CL)
    expiry_date: Optional[date] = None


class StockTransferRequest(BaseModel):
    from_store_id: str
    to_store_id: str
    items: List[dict]  # stock_id, quantity


class StockCountItem(BaseModel):
    product_id: str
    product_name: Optional[str] = None
    sku: Optional[str] = None
    counted_quantity: int = Field(..., ge=0)
    notes: Optional[str] = None


class StartStockCountRequest(BaseModel):
    category: Optional[str] = None
    zone: Optional[str] = None
    notes: Optional[str] = None


class CompleteStockCountRequest(BaseModel):
    notes: Optional[str] = None


class QuarantineRequest(BaseModel):
    """Body for PATCH /stock/{stock_id}/quarantine."""

    reason: str = Field(..., min_length=1)
    notes: Optional[str] = Field(default=None, max_length=200)
    rtv_vendor_id: Optional[str] = None


class LiftQuarantineRequest(BaseModel):
    """Body for PATCH /stock/{stock_id}/lift-quarantine. lift_reason is
    MANDATORY (>=5 chars) so a mis-quarantine correction is always justified in
    the immutable audit trail."""

    lift_reason: str = Field(..., min_length=5)


# ============================================================================
# HELPERS
# ============================================================================


def generate_barcode(store_id: str, product_id: str) -> str:
    """Generate unique barcode for stock item"""
    short_uuid = str(uuid.uuid4())[:8].upper()
    return f"{store_id[:3]}-{short_uuid}"


def _get_db():
    """Get raw MongoDB database for collections without a dedicated repository"""
    try:
        from ..dependencies import get_db

        conn = get_db()
        if conn is not None and conn.is_connected:
            return conn.db
    except Exception:
        pass
    return None


def _on_hand_by_product(
    db, product_ids: List[str], store_id: Optional[str] = None
) -> Dict[str, int]:
    """Count on-hand units per product from the serialized `stock` collection
    (one row per unit). A unit is on-hand when its status is an available one
    (or absent) and quantity > 0. Optionally scoped to a store. Fail-soft -> {}.
    """
    if db is None or not product_ids:
        return {}
    # E3: reuse the canonical on-hand allowlist + the explicit non-sellable
    # exclusion list (QUARANTINED / UNDER_AUDIT / BLIND_COUNT / TRANSFERRED /
    # SOLD / VOID / DAMAGED / RTV) from the item-event ledger service so every
    # rollup shares one definition. The $nin makes the exclusion intent-explicit
    # even for a unit whose status was set outside the allowlist.
    from ..services.item_events import ON_HAND_STATUSES, EXCLUDED_STATUSES

    avail = list(ON_HAND_STATUSES)
    match: dict = {
        "product_id": {"$in": list(product_ids)},
        "status": {"$nin": list(EXCLUDED_STATUSES)},
        "$or": [
            {"status": {"$in": avail}},
            {"status": {"$exists": False}},
            {"status": None},
        ],
    }
    if store_id:
        match["store_id"] = store_id
    out: Dict[str, int] = {}
    try:
        for row in db.get_collection("stock_units").aggregate(
            [
                {"$match": match},
                {
                    "$group": {
                        "_id": "$product_id",
                        "n": {"$sum": {"$ifNull": ["$quantity", 1]}},
                    }
                },
            ]
        ):
            out[row["_id"]] = int(row.get("n", 0) or 0)
    except Exception:
        pass
    return out


# ----------------------------------------------------------------------------
# Contact-lens (CL) FEFO + near-expiry pure helpers (unit-tested, no DB)
# ----------------------------------------------------------------------------

# Categories that count as contact lenses across the codebase. "CL" is the
# legacy short code; "CCL" is the colour-contact short code (2026-07-05 split);
# the full enums are the current schema values.
CL_CATEGORY_CODES = ["CL", "CCL", "CONTACT_LENS", "COLORED_CONTACT_LENS"]


def _parse_expiry(value) -> Optional[datetime]:
    """Coerce a stored expiry (ISO string, date, or datetime) into a datetime.

    Returns None for missing / unparseable values so callers fail soft instead
    of raising on a single bad row.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00").split("+")[0])
    except (ValueError, TypeError):
        return None


def compute_days_until_expiry(expiry, now: Optional[datetime] = None) -> Optional[int]:
    """Whole days from `now` until `expiry` (negative = already expired).

    Returns None when the expiry is missing/unparseable. Pure + testable.
    """
    now = now or datetime.utcnow()
    parsed = _parse_expiry(expiry)
    if parsed is None:
        return None
    return (parsed - now).days


def fefo_sort(stock_rows: List[dict], now: Optional[datetime] = None) -> List[dict]:
    """First-Expiry-First-Out ordering: earliest expiry first.

    `stock_rows` are dicts that carry an `expiry_date`. Rows with no/blank
    expiry sort LAST (you'd pick a dated unit before an undated one). Stable
    for equal expiries. Pure helper — does not mutate the input list.
    """
    now = now or datetime.utcnow()

    def _key(row):
        parsed = _parse_expiry(row.get("expiry_date"))
        # None expiry -> push to the end via a far-future sentinel.
        return (parsed is None, parsed or datetime.max)

    return sorted(stock_rows, key=_key)


def partition_by_expiry(
    stock_rows: List[dict],
    near_days: int = 90,
    now: Optional[datetime] = None,
) -> Dict[str, List[dict]]:
    """Split CL stock rows into expired / near-expiry / safe / undated buckets.

    `near_days` is the configurable near-expiry alert window. Each returned row
    is annotated with `days_until_expiry`. Pure helper. Bucketing rule:
      - days < 0            -> expired
      - 0 <= days <= near   -> near_expiry
      - days > near         -> safe
      - no parseable expiry -> undated
    """
    now = now or datetime.utcnow()
    expired: List[dict] = []
    near: List[dict] = []
    safe: List[dict] = []
    undated: List[dict] = []

    for row in stock_rows:
        days = compute_days_until_expiry(row.get("expiry_date"), now)
        annotated = dict(row)
        annotated["days_until_expiry"] = days
        if days is None:
            undated.append(annotated)
        elif days < 0:
            expired.append(annotated)
        elif days <= near_days:
            near.append(annotated)
        else:
            safe.append(annotated)

    expired.sort(key=lambda r: r["days_until_expiry"])
    near.sort(key=lambda r: r["days_until_expiry"])
    return {
        "expired": expired,
        "near_expiry": near,
        "safe": safe,
        "undated": undated,
    }


# ============================================================================
# STOCK ENDPOINTS
# ============================================================================


@router.get("")
@router.get("/")
async def get_inventory_root():
    """Root endpoint for inventory stock list"""
    return {
        "module": "inventory",
        "status": "active",
        "message": "stock overview endpoint ready",
    }


@router.get("/stock")
async def get_stock(
    store_id: Optional[str] = Query(None),
    product_id: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    low_stock: bool = Query(False),
    current_user: dict = Depends(get_current_user),
):
    """Get the Stock Ledger view for a store.

    Returns ONE row per product the store can hold (every active product
    in the catalog), enriched with on-hand counts aggregated from the
    serialized `stock_units` collection. This is the canonical "Inventory"
    page view and MUST agree with the POS product search at the same
    store (a product the POS can sell -> a row in this list, with
    on_hand >= 0).

    Background: the older shape of this endpoint returned raw stock_units
    documents (one row per serialized unit, with no product fields like
    sku/name/brand/mrp). The frontend Stock Ledger then could not display
    or filter rows, surfacing as "No products found matching your filters"
    in the QA repro at BV-BOK-01 even though POS could sell the SKU at
    the same store. See `tests/test_inventory_pos_consistency.py` for the
    cross-surface guard.

    Modes:
    - `product_id` set: returns raw stock_units rows for that product+store
      (per-unit detail; consumers wanted unit-level data here, e.g. transfer
      builders that pick specific stock_ids).
    - `low_stock=true`: returns the per-product low-stock aggregation
      (unchanged, used by the Low-Stock tab).
    - default: per-product ledger view scoped to `store_id`, optionally
      filtered by `category`. Includes products with zero on-hand so the
      page reflects the full catalog the store stocks.
    """
    stock_repo = get_stock_repository()
    product_repo = get_product_repository()
    active_store = validate_store_access(store_id, current_user)

    # Category-filter fix: products store CANONICAL categories (SUNGLASS/FRAME),
    # callers send short codes (SG/FR) or plurals -- normalise fail-open.
    if category:
        from ..services.product_master import resolve_category

        category = resolve_category(category) or category

    if stock_repo is None or product_repo is None:
        return {"items": [], "total": 0}

    # Mode 1: per-product low-stock aggregation. Untouched.
    if low_stock:
        stock = stock_repo.find_low_stock(active_store)
        return {"items": stock, "total": len(stock)}

    # Mode 2: per-unit detail for one product. Consumers (e.g. transfer
    # picker that selects specific stock_ids) want the raw stock_units rows.
    if product_id:
        stock = stock_repo.find_by_product_store(product_id, active_store)
        return {"items": stock, "total": len(stock)}

    # Mode 3 (default): per-product ledger view. Aggregate stock_units by
    # product_id, join with the catalog so every row carries the fields the
    # frontend renders (sku, name, brand, category, mrp, offer_price). Then
    # union in catalog-only products so the page shows the full set the POS
    # can sell at this store - even ones with zero on-hand right now.
    items = _build_store_ledger(
        stock_repo,
        product_repo,
        active_store,
        category=category,
    )
    return {"items": items, "total": len(items)}


def _last_grn_by_product(store_id: Optional[str]) -> Dict[str, Dict]:
    """Latest ACCEPTED GRN per product at this store (procurement Phase 1).

    Additive + fail-soft + cheap: scans only the most recent 200 ACCEPTED GRNs
    for the store from the last 30 days (newest first) and keeps the FIRST hit
    per product, so a ledger row can show "+N via GRN-xxxx, <date>". Any error
    returns {} and the ledger simply omits the source chip -- this join must
    never break the Stock Ledger.
    """
    if not store_id:
        return {}
    out: Dict[str, Dict] = {}
    try:
        db = _get_db()
        if db is None:
            return {}
        cutoff = (datetime.now() - timedelta(days=30)).isoformat()
        cur = (
            db.get_collection("grns")
            .find(
                {
                    "store_id": store_id,
                    "status": "ACCEPTED",
                    "created_at": {"$gte": cutoff},
                },
                {
                    "_id": 0,
                    "grn_number": 1,
                    "items": 1,
                    "accepted_at": 1,
                    "created_at": 1,
                },
            )
            .sort("created_at", -1)
            .limit(200)
        )
        for grn in cur:
            when = grn.get("accepted_at") or grn.get("created_at") or ""
            for it in grn.get("items") or []:
                pid = it.get("product_id")
                if not pid or pid in out:
                    continue
                try:
                    qty = int(it.get("accepted_qty") or it.get("received_qty") or 0)
                except (TypeError, ValueError):
                    qty = 0
                if qty <= 0:
                    continue
                out[pid] = {
                    "grn_number": grn.get("grn_number") or "",
                    "qty": qty,
                    "date": str(when)[:10],
                }
    except Exception as exc:  # noqa: BLE001
        logger.warning("[INVENTORY] last-GRN join failed: %s", exc)
        return {}
    return out


def _build_store_ledger(
    stock_repo,
    product_repo,
    store_id: Optional[str],
    category: Optional[str] = None,
) -> List[Dict]:
    """Per-product Stock Ledger rows for a store.

    Aggregates `stock_units` by (product_id, status) so a single product
    with multiple serialized units rolls into ONE row carrying:
      - on-hand count (AVAILABLE + IN_STOCK + status-absent, sums `quantity`
        but defaults to 1 when missing because units are typically qty=1)
      - reserved count (RESERVED)
      - product master fields (sku, name, brand, category, mrp, offer_price)
      - a representative barcode + location_code from any AVAILABLE unit
        (so the row's Barcode + Location columns are populated)

    Joins to `products` so every row carries the catalog fields the
    frontend filters/renders. Products in the catalog with no stock_units
    at this store still appear (with stock=0, reserved=0) so the ledger
    shows what the store CAN sell, not just what it currently holds.

    Fail-soft: aggregation errors fall back to a product-only listing.
    """
    on_hand_by_product: Dict[str, int] = {}
    reserved_by_product: Dict[str, int] = {}
    sample_unit_by_product: Dict[str, Dict] = {}

    # ---- 1. Roll up stock_units per product at this store -------------
    if store_id:
        avail_statuses = ["AVAILABLE", "available", "IN_STOCK", "in_stock"]
        try:
            pipeline = [
                {"$match": {"store_id": store_id}},
                {
                    "$project": {
                        "product_id": 1,
                        "status": 1,
                        "quantity": 1,
                        "barcode": 1,
                        "location_code": 1,
                    }
                },
                {
                    "$group": {
                        "_id": {
                            "product_id": "$product_id",
                            "status": "$status",
                        },
                        "qty": {"$sum": {"$ifNull": ["$quantity", 1]}},
                        "barcode": {"$first": "$barcode"},
                        "location_code": {"$first": "$location_code"},
                    }
                },
            ]
            for row in stock_repo.collection.aggregate(pipeline):
                key = row["_id"] or {}
                pid = key.get("product_id") or ""
                status = key.get("status")
                qty = int(row.get("qty") or 0)
                if not pid:
                    continue
                if status in avail_statuses or status is None:
                    on_hand_by_product[pid] = on_hand_by_product.get(pid, 0) + qty
                    # Capture a sample barcode/location from any available unit
                    # for the Barcode + Location columns on the ledger row.
                    if pid not in sample_unit_by_product:
                        sample_unit_by_product[pid] = {
                            "barcode": row.get("barcode") or "",
                            "location_code": row.get("location_code") or "",
                        }
                elif status == "RESERVED":
                    reserved_by_product[pid] = reserved_by_product.get(pid, 0) + qty
        except (AttributeError, TypeError, ValueError) as exc:
            logger.warning("[INVENTORY] stock aggregation failed: %s", exc)

    # ---- 2. Catalog union: list every active product (optionally filtered
    # by category) so the ledger shows what the store CAN sell, not just
    # what is currently on the floor. This is what aligns with POS - a
    # product the POS can search for at this store is now ALWAYS in the
    # Stock Ledger for the same store. -------------------------------
    catalog_filter: Dict = {"is_active": True}
    if category:
        catalog_filter["category"] = category
    try:
        products = product_repo.find_many(catalog_filter, limit=5000)
    except (AttributeError, TypeError, ValueError) as exc:
        logger.warning("[INVENTORY] product list failed: %s", exc)
        products = []

    # Procurement Phase 1: latest ACCEPTED GRN per product (fail-soft -> {}).
    last_grn_map = _last_grn_by_product(store_id)

    items: List[Dict] = []
    seen_pids = set()
    for product in products:
        pid = str(product.get("product_id") or product.get("_id") or "")
        if not pid:
            continue
        seen_pids.add(pid)
        on_hand = on_hand_by_product.get(pid, 0)
        reserved = reserved_by_product.get(pid, 0)
        sample = sample_unit_by_product.get(pid, {})
        items.append(
            _ledger_row(
                product,
                on_hand,
                reserved,
                sample,
                store_id,
                last_grn=last_grn_map.get(pid),
            )
        )

    # ---- 3. Edge case - units exist for a product that's NOT in the
    # active catalog (deactivated SKU still on the shelf). Surface those
    # rows too so the manager can see + clear them. ------------------
    for pid, on_hand in on_hand_by_product.items():
        if pid in seen_pids:
            continue
        product = product_repo.find_by_id(pid) or {"product_id": pid}
        # Respect the category filter here too. Step 2 already excluded
        # off-category products from the active-catalog list; without this
        # guard a stranded unit of a different category (e.g. a SUNGLASS unit
        # at this store while filtering category=FRAME) would leak back into
        # the filtered ledger. Rows with no/blank category (legacy or orphan
        # units whose product master is gone) are still shown so genuinely
        # stranded stock is never hidden from the write-off view.
        if category:
            stranded_cat = product.get("category")
            if stranded_cat and stranded_cat != category:
                continue
        items.append(
            _ledger_row(
                product,
                on_hand,
                reserved_by_product.get(pid, 0),
                sample_unit_by_product.get(pid, {}),
                store_id,
                last_grn=last_grn_map.get(pid),
            )
        )

    return items


def _ledger_row(
    product: Dict,
    on_hand: int,
    reserved: int,
    sample_unit: Dict,
    store_id: Optional[str],
    last_grn: Optional[Dict] = None,
) -> Dict:
    """Build a single Stock Ledger row from a product master doc.

    Field naming MIRRORS the legacy raw-stock_units shape AND the
    product-master shape both because consumers landed on a mix:
      - `id` + `sku` + `name` + `brand` + `category` + `mrp` (used by
        the InventoryPage card grid, transfer modal, returns picker)
      - `product_id` + `quantity` + `reserved_quantity` (compatibility
        with code that grew up on stock_units rows)
      - `stock` (front-end alias for on-hand) + `offerPrice` (FE alias
        for offer_price) so existing renderers don't have to change.
    """
    pid = str(product.get("product_id") or product.get("_id") or "")
    brand = product.get("brand", "")
    model = product.get("model", "")
    # `name` is constructed from brand+model when the master doc doesn't
    # carry one explicitly; matches the convention in aging + reports.
    name = product.get("name") or f"{brand} {model}".strip() or product.get("sku", "")
    mrp = float(product.get("mrp", 0) or 0)
    offer_price = float(product.get("offer_price", mrp) or mrp)
    return {
        "id": pid,
        "product_id": pid,
        "stock_id": pid,  # legacy alias
        "sku": product.get("sku", ""),
        "name": name,
        "productName": name,
        "brand": brand,
        "model": model,
        "category": product.get("category", ""),
        "mrp": mrp,
        "offerPrice": offer_price,
        "offer_price": offer_price,
        "stock": on_hand,
        "quantity": on_hand,
        "reserved": reserved,
        "reservedQuantity": reserved,
        "reserved_quantity": reserved,
        "barcode": sample_unit.get("barcode", "") or product.get("barcode", ""),
        "location": sample_unit.get("location_code", "")
        or product.get("location_code", ""),
        "location_code": sample_unit.get("location_code", "")
        or product.get("location_code", ""),
        "store_id": store_id or "",
        "is_active": bool(product.get("is_active", True)),
        # Pass through CL identity fields so the contact-lens widgets
        # can read them without a second fetch.
        "modality": product.get("modality"),
        "cl_series": product.get("cl_series"),
        "base_curve": product.get("base_curve"),
        "diameter": product.get("diameter"),
        # Reorder policy passthrough (raw; None when the master doc has no
        # value). reorder_quantity <= 0 (the -1 default the create door
        # stamps) means auto-reorder is DISABLED for this product -- see
        # api/services/reorder_policy.py. The Reorder dashboard renders that
        # state honestly instead of fabricating a quantity.
        "reorder_quantity": product.get("reorder_quantity"),
        "reorder_point": product.get("reorder_point"),
        # Procurement Phase 1 (additive, optional): the latest ACCEPTED GRN
        # that put stock of this product on this store's shelf, or None.
        # Shape: {"grn_number": str, "qty": int, "date": "YYYY-MM-DD"}.
        "last_grn": last_grn or None,
        # Owner 2026-07-05: product images on the Inventory screen. The first
        # image as the row thumbnail + the full array for the click-to-zoom
        # lightbox. Spine docs carry images[] (image_url is a serve-time alias).
        "image_url": (
            product.get("image_url")
            or (
                product["images"][0]
                if isinstance(product.get("images"), list)
                and product.get("images")
                and isinstance(product["images"][0], str)
                else None
            )
        ),
        "images": (
            product.get("images") if isinstance(product.get("images"), list) else []
        ),
    }


# ============================================================================
# INV-12: BARCODE LIFECYCLE TRACE
# ============================================================================
# Returns the full movement history for a single physical barcode across all
# modules: purchase (GRN) -> stock_units -> sales (orders) -> transfers ->
# returns.  Satisfies SYSTEM_INTENT "Audit Everything" without any new
# collection: it collects existing audit rows + cross-collection joins in one
# call.  Fail-soft: a missing collection returns an empty section rather than
# 500-ing the whole response.


@router.get("/barcode/{barcode}/trace")
async def barcode_lifecycle_trace(
    barcode: str,
    current_user: dict = Depends(get_current_user),
):
    """Return the full movement history for a physical barcode (INV-12).

    Sections returned (each is a list, empty when no records found):
      * stock_unit   - the minted serialized row (who, when, source GRN/PO)
      * purchase     - GRN line that created the unit (if any)
      * sales        - order line(s) the barcode appeared in
      * transfers    - transfer line(s) the barcode was part of (ship+receive)
      * returns      - return line(s) that restocked this barcode
      * audit_trail  - raw stock_audit rows keyed on this stock_unit's id

    Honest empty state: when a barcode is unknown an empty envelope is returned
    (not 404) because the barcode may have been received via a path that did not
    mint a stock_units row yet.
    """
    db = _get_db()
    result: Dict = {
        "barcode": barcode,
        "stock_unit": None,
        "purchase": [],
        "sales": [],
        "transfers": [],
        "returns": [],
        "audit_trail": [],
    }
    if db is None:
        return result

    def _scrub(doc: Optional[dict]) -> Optional[dict]:
        if doc is None:
            return None
        doc.pop("_id", None)
        return doc

    def _scrub_list(docs) -> list:
        return [_scrub(dict(d)) for d in (docs or []) if d]

    try:
        # 1. Stock unit
        su = db.get_collection("stock_units").find_one({"barcode": barcode})
        if su:
            result["stock_unit"] = _scrub(dict(su))
            stock_id = str(su.get("stock_id") or su.get("stock_unit_id") or su.get("_id") or "")

            # 2. Purchase / GRN origin
            grn_id = su.get("source_id") if su.get("source_type") == "GRN" else None
            if not grn_id:
                grn_id = su.get("grn_id")
            if grn_id:
                grn = db.get_collection("grns").find_one({"grn_id": grn_id})
                if grn is None:
                    # Alternate collection name used by GRN repo
                    grn = db.get_collection("goods_receipt_notes").find_one({"grn_id": grn_id})
                if grn:
                    result["purchase"] = [_scrub(dict(grn))]

            # 3. Audit trail (stock_audit rows keyed on this unit's id)
            if stock_id:
                audit_rows = list(
                    db.get_collection("stock_audit").find(
                        {"stock_id": stock_id}, {"_id": 0}
                    ).sort("at", 1).limit(200)
                )
                result["audit_trail"] = _scrub_list(audit_rows)
        else:
            stock_id = ""

    except Exception as exc:  # noqa: BLE001
        logger.warning("[INV-12] stock_unit lookup failed for barcode %s: %s", barcode, exc)

    try:
        # 4. Sales: orders where an item carries this barcode
        orders = list(
            db.get_collection("orders").find(
                {"$or": [
                    {"items.barcode": barcode},
                    {"order_items.barcode": barcode},
                ]},
                {"_id": 0, "order_number": 1, "created_at": 1, "store_id": 1,
                 "status": 1, "items": 1, "order_items": 1},
            ).sort("created_at", 1).limit(50)
        )
        for order in orders:
            matching = [
                i for i in (order.get("items") or order.get("order_items") or [])
                if i.get("barcode") == barcode
            ]
            result["sales"].append({
                "order_number": order.get("order_number"),
                "created_at": order.get("created_at"),
                "store_id": order.get("store_id"),
                "status": order.get("status"),
                "matched_lines": matching,
            })
    except Exception as exc:  # noqa: BLE001
        logger.warning("[INV-12] orders lookup failed for barcode %s: %s", barcode, exc)

    try:
        # 5. Transfers: look for the barcode in shipped_stock_ids /
        #    received_stock_ids on each transfer line.  Also check transfer_id
        #    stamped on the stock_unit itself.
        transfer_filter: list = [
            {"items.shipped_stock_ids": barcode},
            {"items.received_stock_ids": barcode},
        ]
        # If the stock unit carries a transfer_id, add that as an exact lookup.
        transfer_id_on_unit = None
        if result.get("stock_unit"):
            transfer_id_on_unit = result["stock_unit"].get("transfer_id") or \
                result["stock_unit"].get("source_id") if (
                    result.get("stock_unit", {}) or {}
                ).get("source_type") == "TRANSFER" else None
        if transfer_id_on_unit:
            transfer_filter.append({"id": transfer_id_on_unit})
        transfers = list(
            db.get_collection("stock_transfers").find(
                {"$or": transfer_filter},
                {"_id": 0, "id": 1, "transfer_number": 1, "from_location_name": 1,
                 "to_location_name": 1, "status": 1, "shipped_at": 1, "received_at": 1},
            ).sort("created_at", 1).limit(50)
        )
        result["transfers"] = _scrub_list(transfers)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[INV-12] transfers lookup failed for barcode %s: %s", barcode, exc)

    try:
        # 6. Returns: return lines that reference this barcode or its stock_id
        return_filter: list = [{"items.barcode": barcode}]
        if stock_id:
            return_filter.append({"items.stock_id": stock_id})
        returns = list(
            db.get_collection("returns").find(
                {"$or": return_filter},
                {"_id": 0, "return_number": 1, "created_at": 1, "store_id": 1,
                 "status": 1, "items": 1},
            ).sort("created_at", 1).limit(50)
        )
        result["returns"] = _scrub_list(returns)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[INV-12] returns lookup failed for barcode %s: %s", barcode, exc)

    return result


# ============================================================================
# STOCK MOVEMENTS LEDGER (Movements tab)
# ============================================================================
# One reverse-chronological ledger merged from the three real event sources
# that move stock today:
#   RECEIVED     <- `grns` (status ACCEPTED)          qty positive
#   SOLD         <- `orders` (status in _SOLD_STATUSES) qty negative
#   TRANSFER_OUT <- `stock_transfers` shipped leg     qty negative (from store)
#   TRANSFER_IN  <- `stock_transfers` received leg    qty positive (to store)
#
# DELIBERATELY EXCLUDED for now: stock_units status flips (QUARANTINED /
# lift-quarantine / stock-count reconcile). Those are unit-level state changes
# recorded in `stock_audit` with a different granularity (per serialized unit,
# not per product line); folding them in honestly needs a per-unit -> per-line
# rollup that is out of scope here. The ledger says so via `sources`.
#
# Each source read is capped (_MOVEMENTS_PER_SOURCE_CAP newest docs inside the
# `days` window) and FAIL-SOFT: a source that errors shortens the ledger and is
# reported in `sources`, it never 5xxes the endpoint.

_MOVEMENT_TYPES = ("RECEIVED", "SOLD", "TRANSFER_IN", "TRANSFER_OUT")
_MOVEMENTS_PER_SOURCE_CAP = 300


def _movement_iso(value) -> str:
    """Coerce a stored timestamp (datetime, date, ISO string) to an ISO string
    so mixed-type `at` values sort correctly. Unknown -> '' (sorts last)."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day).isoformat()
    if value is None:
        return ""
    return str(value)


def _collect_received_events(
    db, store_id: Optional[str], cutoff_iso: str, product_id: Optional[str]
) -> List[Dict]:
    """RECEIVED events from ACCEPTED GRNs (qty = accepted units, positive).
    grns.created_at / accepted_at are ISO strings (see vendors.py)."""
    flt: Dict = {"status": "ACCEPTED", "created_at": {"$gte": cutoff_iso}}
    if store_id:
        flt["store_id"] = store_id
    if product_id:
        flt["items.product_id"] = product_id
    events: List[Dict] = []
    cur = (
        db.get_collection("grns")
        .find(
            flt,
            {
                "_id": 0,
                "grn_id": 1,
                "grn_number": 1,
                "po_number": 1,
                "store_id": 1,
                "items": 1,
                "accepted_at": 1,
                "created_at": 1,
            },
        )
        .sort("created_at", -1)
        .limit(_MOVEMENTS_PER_SOURCE_CAP)
    )
    for grn in cur:
        at = _movement_iso(grn.get("accepted_at") or grn.get("created_at"))
        ref = grn.get("grn_number") or grn.get("grn_id") or ""
        po_number = grn.get("po_number")
        detail = f"GRN {ref}" + (f" against PO {po_number}" if po_number else "")
        for idx, item in enumerate(grn.get("items") or []):
            pid = item.get("product_id")
            if not pid or (product_id and pid != product_id):
                continue
            # accepted_qty drives the ledger; received_qty is ONLY a fallback
            # for legacy lines missing the field. An explicit accepted_qty=0
            # (all units rejected) put nothing on the shelf -> no event.
            raw_qty = item.get("accepted_qty")
            if raw_qty is None:
                raw_qty = item.get("received_qty")
            try:
                qty = int(raw_qty or 0)
            except (TypeError, ValueError):
                qty = 0
            if qty <= 0:
                continue
            events.append(
                {
                    "id": f"RECEIVED:{grn.get('grn_id') or ref}:{pid}:{idx}",
                    "at": at,
                    "type": "RECEIVED",
                    "product_id": pid,
                    "product_name": item.get("product_name") or "",
                    "sku": item.get("sku") or "",
                    "qty": qty,
                    "ref": ref,
                    "ref_id": grn.get("grn_id") or "",
                    "store_id": grn.get("store_id") or "",
                    "detail": detail,
                }
            )
    return events


def _collect_sold_events(
    db, store_id: Optional[str], cutoff_dt: datetime, product_id: Optional[str]
) -> List[Dict]:
    """SOLD events from orders whose status means a real sale (qty negative).
    orders.created_at is a BSON Date (see order_repository.py)."""
    flt: Dict = {
        "created_at": {"$gte": cutoff_dt},
        "status": {"$in": _SOLD_STATUSES},
    }
    if store_id:
        flt["store_id"] = store_id
    if product_id:
        flt["items.product_id"] = product_id
    events: List[Dict] = []
    cur = (
        db.get_collection("orders")
        .find(
            flt,
            {
                "_id": 0,
                "order_id": 1,
                "order_number": 1,
                "invoice_number": 1,
                "store_id": 1,
                "items": 1,
                "created_at": 1,
            },
        )
        .sort("created_at", -1)
        .limit(_MOVEMENTS_PER_SOURCE_CAP)
    )
    for order in cur:
        at = _movement_iso(order.get("created_at"))
        ref = order.get("invoice_number") or order.get("order_number") or ""
        for idx, item in enumerate(order.get("items") or []):
            pid = item.get("product_id")
            if not pid or (product_id and pid != product_id):
                continue
            try:
                qty = int(item.get("quantity") or 1)
            except (TypeError, ValueError):
                qty = 1
            if qty <= 0:
                continue
            events.append(
                {
                    "id": f"SOLD:{order.get('order_id') or ref}:{pid}:{idx}",
                    "at": at,
                    "type": "SOLD",
                    "product_id": pid,
                    "product_name": item.get("product_name") or item.get("name") or "",
                    "sku": item.get("sku") or "",
                    "qty": -qty,
                    "ref": ref,
                    "ref_id": order.get("order_id") or "",
                    "store_id": order.get("store_id") or "",
                    "detail": f"Sale {ref}".strip(),
                }
            )
    return events


def _collect_transfer_events(
    db, store_id: Optional[str], cutoff_iso: str, product_id: Optional[str]
) -> List[Dict]:
    """TRANSFER_OUT (shipped leg, negative, stamped with the FROM store) and
    TRANSFER_IN (received leg, positive, stamped with the TO store) from
    `stock_transfers`. shipped_at / received_at are ISO strings (transfers.py).

    When the caller is scoped to one store, only the leg that touches that
    store is emitted (an outbound transfer is an OUT event at the sender and
    an IN event at the receiver -- never both in one store's ledger)."""
    time_or = {
        "$or": [
            {"shipped_at": {"$gte": cutoff_iso}},
            {"received_at": {"$gte": cutoff_iso}},
        ]
    }
    clauses: List[Dict] = [time_or]
    if store_id:
        clauses.append(
            {"$or": [{"from_location_id": store_id}, {"to_location_id": store_id}]}
        )
    if product_id:
        clauses.append({"items.product_id": product_id})
    flt: Dict = clauses[0] if len(clauses) == 1 else {"$and": clauses}
    events: List[Dict] = []
    cur = (
        db.get_collection("stock_transfers")
        .find(
            flt,
            {
                "_id": 0,
                "id": 1,
                "transfer_number": 1,
                "from_location_id": 1,
                "from_location_name": 1,
                "to_location_id": 1,
                "to_location_name": 1,
                "items": 1,
                "shipped_at": 1,
                "received_at": 1,
            },
        )
        .sort("shipped_at", -1)
        .limit(_MOVEMENTS_PER_SOURCE_CAP)
    )
    for transfer in cur:
        ref = transfer.get("transfer_number") or transfer.get("id") or ""
        tid = transfer.get("id") or ref
        from_id = transfer.get("from_location_id") or ""
        to_id = transfer.get("to_location_id") or ""
        from_name = transfer.get("from_location_name") or from_id
        to_name = transfer.get("to_location_name") or to_id
        shipped_at = transfer.get("shipped_at")
        received_at = transfer.get("received_at")
        for idx, item in enumerate(transfer.get("items") or []):
            pid = item.get("product_id")
            if not pid or (product_id and pid != product_id):
                continue
            name = item.get("product_name") or ""
            sku = item.get("sku") or ""
            # OUT leg: what actually shipped (older docs may only carry
            # quantity_requested -- respect it only when a ship happened).
            if shipped_at and (not store_id or from_id == store_id):
                try:
                    out_qty = int(
                        item.get("quantity_shipped")
                        if item.get("quantity_shipped") is not None
                        else item.get("quantity_requested") or 0
                    )
                except (TypeError, ValueError):
                    out_qty = 0
                if out_qty > 0:
                    events.append(
                        {
                            "id": f"TRANSFER_OUT:{tid}:{pid}:{idx}",
                            "at": _movement_iso(shipped_at),
                            "type": "TRANSFER_OUT",
                            "product_id": pid,
                            "product_name": name,
                            "sku": sku,
                            "qty": -out_qty,
                            "ref": ref,
                            "ref_id": tid,
                            "store_id": from_id,
                            "detail": f"Transfer {ref} to {to_name}",
                        }
                    )
            # IN leg: only units actually received (never the requested qty).
            if received_at and (not store_id or to_id == store_id):
                try:
                    in_qty = int(item.get("quantity_received") or 0)
                except (TypeError, ValueError):
                    in_qty = 0
                if in_qty > 0:
                    events.append(
                        {
                            "id": f"TRANSFER_IN:{tid}:{pid}:{idx}",
                            "at": _movement_iso(received_at),
                            "type": "TRANSFER_IN",
                            "product_id": pid,
                            "product_name": name,
                            "sku": sku,
                            "qty": in_qty,
                            "ref": ref,
                            "ref_id": tid,
                            "store_id": to_id,
                            "detail": f"Transfer {ref} from {from_name}",
                        }
                    )
    return events


def _enrich_movement_products(db, events: List[Dict]) -> None:
    """Fill product_name / sku on events whose source line didn't carry them
    (e.g. GRN items) with ONE batched products lookup. Fail-soft: any error
    leaves the events as-is."""
    missing = sorted(
        {
            e["product_id"]
            for e in events
            if e.get("product_id") and not (e.get("product_name") and e.get("sku"))
        }
    )
    if not missing:
        return
    try:
        by_id: Dict[str, Dict] = {}
        cur = db.get_collection("products").find(
            {
                "$or": [
                    {"product_id": {"$in": missing}},
                    {"_id": {"$in": missing}},
                ]
            },
            {"product_id": 1, "name": 1, "brand": 1, "model": 1, "sku": 1},
        )
        for product in cur:
            pid = str(product.get("product_id") or product.get("_id") or "")
            if pid:
                by_id[pid] = product
        for event in events:
            product = by_id.get(event.get("product_id") or "")
            if not product:
                continue
            if not event.get("product_name"):
                brand = product.get("brand", "")
                model = product.get("model", "")
                event["product_name"] = (
                    product.get("name")
                    or f"{brand} {model}".strip()
                    or product.get("sku", "")
                )
            if not event.get("sku"):
                event["sku"] = product.get("sku", "")
    except Exception as exc:  # noqa: BLE001
        logger.warning("[MOVEMENTS] product enrichment failed: %s", exc)


@router.get("/movements")
async def get_stock_movements(
    store_id: Optional[str] = Query(None),
    product_id: Optional[str] = Query(None),
    movement_type: Optional[str] = Query(
        None,
        alias="type",
        description=(
            "RECEIVED | SOLD | TRANSFER_IN | TRANSFER_OUT | TRANSFER "
            "(TRANSFER = both legs). Omit for all types."
        ),
    ),
    days: int = Query(90, ge=1, le=365),
    limit: int = Query(50, ge=1, le=200),
    skip: int = Query(0, ge=0),
    current_user: dict = Depends(get_current_user),
):
    """Merged stock-movement ledger for the Movements tab.

    Store resolution mirrors /inventory/stock: the explicit store_id is
    validated against the caller's reach (403 on a foreign store for a
    store-scoped role); omitted -> the caller's active store (admins with no
    active store see all stores). Sorted newest-first; paged via skip/limit
    over the merged in-window ledger; `sources` reports per-source health.
    """
    active_store = validate_store_access(store_id, current_user)

    if movement_type:
        movement_type = movement_type.strip().upper()
        if movement_type not in _MOVEMENT_TYPES + ("TRANSFER",):
            raise HTTPException(
                status_code=400,
                detail=(
                    "Invalid type. Use one of: "
                    + ", ".join(_MOVEMENT_TYPES + ("TRANSFER",))
                ),
            )

    empty = {
        "items": [],
        "total": 0,
        "skip": skip,
        "limit": limit,
        "has_more": False,
        "days": days,
        "store_id": active_store,
        "sources": {"grns": "skipped", "orders": "skipped", "transfers": "skipped"},
    }
    db = _get_db()
    if db is None:
        return empty

    cutoff_dt = datetime.utcnow() - timedelta(days=days)
    cutoff_iso = cutoff_dt.isoformat()

    events: List[Dict] = []
    sources: Dict[str, str] = {}
    collectors = (
        ("grns", lambda: _collect_received_events(db, active_store, cutoff_iso, product_id)),
        ("orders", lambda: _collect_sold_events(db, active_store, cutoff_dt, product_id)),
        ("transfers", lambda: _collect_transfer_events(db, active_store, cutoff_iso, product_id)),
    )
    for name, collect in collectors:
        try:
            events.extend(collect())
            sources[name] = "ok"
        except Exception as exc:  # noqa: BLE001
            logger.warning("[MOVEMENTS] source %s failed: %s", name, exc)
            sources[name] = "error"

    if movement_type == "TRANSFER":
        events = [e for e in events if e["type"] in ("TRANSFER_IN", "TRANSFER_OUT")]
    elif movement_type:
        events = [e for e in events if e["type"] == movement_type]

    events.sort(key=lambda e: e.get("at") or "", reverse=True)
    total = len(events)
    page = events[skip : skip + limit]
    _enrich_movement_products(db, page)

    return {
        "items": page,
        "total": total,
        "skip": skip,
        "limit": limit,
        "has_more": skip + limit < total,
        "days": days,
        "store_id": active_store,
        "sources": sources,
    }


# NOTE: Specific routes MUST come before /{parameter} routes
@router.get("/low-stock")
async def get_low_stock_alerts(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Get low stock alerts.

    Each item carries `auto_reorder_disabled` (per-product policy, see
    api/services/reorder_policy.py): True when the product master has
    reorder_quantity <= 0 (the -1 "no auto-reorder" sentinel). The alert
    list itself is UNCHANGED -- every low-stock product is still returned
    so managers see the state; the flag lets consumers (Reorder dashboard,
    Stock Replenishment suggestions) decide whether to propose a PO.
    """
    repo = get_stock_repository()
    active_store = validate_store_access(store_id, current_user)

    if repo is None:
        return {"items": []}

    items = repo.find_low_stock(active_store)

    # Join the product masters in ONE $in query (fail-soft: a join failure
    # only means the flag stays False, i.e. legacy-enabled behaviour).
    products_by_id: Dict[str, Dict] = {}
    product_repo = get_product_repository()
    pids = [str(i.get("_id") or "") for i in items if i.get("_id")]
    if product_repo is not None and pids:
        try:
            for prod in product_repo.find_many(
                {"product_id": {"$in": pids}}, limit=len(pids)
            ):
                key = str(prod.get("product_id") or prod.get("_id") or "")
                if key:
                    products_by_id[key] = prod
        except (AttributeError, TypeError, ValueError) as exc:
            logger.warning("[INVENTORY] low-stock reorder-policy join failed: %s", exc)

    for item in items:
        pid = str(item.get("_id") or "")
        item["auto_reorder_disabled"] = _reorder_disabled(products_by_id.get(pid, {}))

    return {"items": items}


@router.get("/barcode/{barcode}")
async def get_stock_by_barcode_short(
    barcode: str,
    store_id: Optional[str] = Query(
        None,
        description="Scope the lookup to this store; defaults to the caller's active store.",
    ),
    current_user: dict = Depends(get_current_user),
):
    """Resolve a SINGLE physical unit by its unique intake barcode.

    This backs the POS scan path. Behaviour:
      - Scope to a store: the `store_id` query param wins, else the caller's
        active store. A hit in a DIFFERENT store is NOT silently returned --
        it comes back flagged `cross_store: true` so the POS can warn the
        cashier (selling another store's stock at this terminal is wrong) and
        loud-fail rather than quietly adding a foreign unit to the cart.
      - Enrich with the product master (name / category / mrp / offer_price /
        gst_rate / brand): a `stock_units` row only carries product_id, so the
        scan response now joins the product so the cart has everything it needs
        without a second round-trip.
      - A barcode that matches NOTHING is a hard 404 (fail loudly), never a
        soft empty body that the caller might mistake for a hit.
    """
    repo = get_stock_repository()

    if repo is None:
        # No DB (stub mode) -- do not fabricate a hit; echo the barcode so the
        # caller can fall through without treating it as a real unit.
        return {"barcode": barcode}

    stock = repo.find_by_barcode(barcode)
    if not stock:
        raise HTTPException(status_code=404, detail="Stock item not found")

    # Determine the scope store (explicit param > active store).
    scope_store = store_id or current_user.get("active_store_id")
    unit_store = stock.get("store_id")
    cross_store = bool(scope_store and unit_store and unit_store != scope_store)
    stock["cross_store"] = cross_store

    # Join the product master so the POS scan has product fields in one hop.
    product_repo = get_product_repository()
    if product_repo is not None and stock.get("product_id"):
        product = product_repo.find_by_id(stock["product_id"])
        if product:
            product.pop("_id", None)
            stock["product"] = product

    return stock


@router.get("/expiring")
async def get_expiring_stock(
    days: int = Query(30, ge=1, le=365), current_user: dict = Depends(get_current_user)
):
    """Get stock items expiring within specified days"""
    repo = get_stock_repository()
    active_store = current_user.get("active_store_id")

    if repo is not None:
        items = repo.find_expiring(active_store, days)
        return {"items": items}

    return {"items": []}


@router.get("/stock/barcode/{barcode}")
async def get_stock_by_barcode(
    barcode: str, current_user: dict = Depends(get_current_user)
):
    """Get stock item by barcode (alternate path)"""
    repo = get_stock_repository()

    if repo is not None:
        stock = repo.find_by_barcode(barcode)
        if stock:
            return stock
        raise HTTPException(status_code=404, detail="Stock item not found")

    return {"barcode": barcode}


@router.post("/stock/add")
async def add_stock(
    request: StockAddRequest,
    current_user: dict = Depends(require_roles(*_INVENTORY_ROLES)),
):
    """Add stock to inventory"""
    stock_repo = get_stock_repository()
    product_repo = get_product_repository()
    active_store = current_user.get("active_store_id")

    if stock_repo is not None and product_repo is not None:
        # Verify product exists
        product = product_repo.find_by_id(request.product_id)
        if product is None:
            raise HTTPException(status_code=404, detail="Product not found")

        # Create stock entries for each unit. Each physical unit gets a UNIQUE
        # barcode (unique per unit per purchase): mint an EAN-13 from the atomic
        # counter, falling back to the legacy store+uuid scheme if no DB counter
        # is reachable so a GRN/intake is never blocked.
        _db = _get_db()
        _counter = _db.get_collection("counters") if _db is not None else None
        stock_items = []
        for _ in range(request.quantity):
            barcode = barcode_svc.next_unit_ean13(_counter) or generate_barcode(
                active_store, request.product_id
            )
            stock_data = {
                "product_id": request.product_id,
                "store_id": active_store,
                "barcode": barcode,
                # One serialized row == one physical unit. Persist quantity=1
                # so aggregations that sum `$quantity` count this unit instead
                # of summing a missing field (which silently yields 0).
                "quantity": 1,
                "location_code": request.location_code or "DEFAULT",
                "batch_code": request.batch_code or request.lot,
                "expiry_date": (
                    request.expiry_date.isoformat() if request.expiry_date else None
                ),
                "status": "AVAILABLE",
                "is_reserved": False,
                "barcode_printed": False,
                "created_by": current_user.get("user_id"),
            }
            created = stock_repo.create(stock_data)
            if created:
                stock_items.append(created)

        return {
            "stock_ids": [
                s.get("stock_unit_id", s.get("stock_id", "")) for s in stock_items
            ],
            "barcodes": [s.get("barcode", "") for s in stock_items],
            "quantity": len(stock_items),
        }

    return {"stock_id": str(uuid.uuid4()), "barcode": generate_barcode("STR", "PRD")}


# ============================================================================
# OPENING-STOCK IMPORTER (go-live)
# ============================================================================
# Bulk-seed shelf quantities at go-live: the owner uploads a CSV (parsed to JSON
# rows client-side) of {product_id|sku, quantity, [location_code, batch_code,
# expiry_date]}. PREVIEW validates every row and (critically) flags products
# that ALREADY hold stock so a re-run can't silently double inventory. COMMIT
# mints the serialized stock_units rows via the same path as /stock/add, with a
# skip_if_existing guard. Control-over-convenience: preview is the default; the
# owner sees exactly what will happen before any write.


class OpeningStockRow(BaseModel):
    # Identify the product by EITHER product_id or sku (sku is what owners have
    # in their spreadsheets). At least one must be present; product_id wins.
    product_id: Optional[str] = None
    sku: Optional[str] = None
    quantity: int = Field(..., ge=1, le=10000)
    location_code: Optional[str] = None
    batch_code: Optional[str] = None
    expiry_date: Optional[date] = None


class OpeningStockImport(BaseModel):
    rows: List[OpeningStockRow] = Field(..., min_length=1, max_length=5000)
    # When True, a product that already has AVAILABLE stock is SKIPPED (not
    # added to) — the safe default so a double-submit never doubles stock.
    skip_if_existing: bool = True


def _resolve_opening_stock_row(row, product_repo, stock_repo, active_store):
    """Return (product, existing_qty, error) for one import row. error is a
    human string when the row can't be imported; product is the matched doc."""
    ident = (row.product_id or "").strip() or (row.sku or "").strip()
    if not ident:
        return None, 0, "Row has neither product_id nor sku."
    product = None
    if row.product_id:
        product = product_repo.find_by_id(row.product_id.strip())
    if product is None and row.sku:
        product = product_repo.find_by_sku(row.sku.strip())
    if product is None:
        return None, 0, f"No product matches '{ident}'."
    pid = product.get("product_id")
    existing = stock_repo.find_available(pid, active_store)
    return product, existing, None


@router.post("/opening-stock/preview")
async def opening_stock_preview(
    payload: OpeningStockImport,
    current_user: dict = Depends(require_roles(*_INVENTORY_ROLES)),
):
    """Dry-run an opening-stock import: validate every row and report what COMMIT
    would do. Never writes. Flags rows whose product already holds stock (the
    re-import / double-count risk) so the owner decides before committing."""
    stock_repo = get_stock_repository()
    product_repo = get_product_repository()
    active_store = current_user.get("active_store_id")
    if stock_repo is None or product_repo is None:
        raise HTTPException(status_code=503, detail="Inventory store not available")

    results = []
    to_add = 0
    will_skip = 0
    errors = 0
    for i, row in enumerate(payload.rows):
        product, existing, err = _resolve_opening_stock_row(
            row, product_repo, stock_repo, active_store
        )
        if err:
            errors += 1
            results.append(
                {
                    "index": i,
                    "status": "ERROR",
                    "identifier": row.product_id or row.sku,
                    "message": err,
                }
            )
            continue
        already = existing > 0
        if already and payload.skip_if_existing:
            will_skip += 1
            status = "SKIP_EXISTING"
            msg = f"Already has {existing} in stock — will be skipped."
        else:
            to_add += row.quantity
            status = "WILL_ADD" if not already else "WILL_ADD_ON_TOP"
            msg = (
                f"Will add {row.quantity}."
                if not already
                else f"Already has {existing}; will ADD {row.quantity} on top."
            )
        results.append(
            {
                "index": i,
                "status": status,
                "product_id": product.get("product_id"),
                "sku": product.get("sku"),
                "name": product.get("model") or product.get("name") or "",
                "quantity": row.quantity,
                "existing": existing,
                "message": msg,
            }
        )

    return {
        "rows": results,
        "summary": {
            "total_rows": len(payload.rows),
            "units_to_add": to_add,
            "rows_to_skip": will_skip,
            "rows_with_errors": errors,
            "skip_if_existing": payload.skip_if_existing,
        },
    }


@router.post("/opening-stock/commit")
async def opening_stock_commit(
    payload: OpeningStockImport,
    current_user: dict = Depends(require_roles(*_INVENTORY_ROLES)),
):
    """Commit an opening-stock import: mint serialized stock_units rows (same as
    /stock/add) for every valid row. Per-row errors never abort the batch. With
    skip_if_existing=True (default) a product that already holds stock is left
    untouched, so a double-submit can't double inventory."""
    stock_repo = get_stock_repository()
    product_repo = get_product_repository()
    active_store = current_user.get("active_store_id")
    if stock_repo is None or product_repo is None:
        raise HTTPException(status_code=503, detail="Inventory store not available")

    _db = _get_db()
    _counter = _db.get_collection("counters") if _db is not None else None

    results = []
    units_added = 0
    rows_skipped = 0
    rows_errored = 0
    for i, row in enumerate(payload.rows):
        product, existing, err = _resolve_opening_stock_row(
            row, product_repo, stock_repo, active_store
        )
        if err:
            rows_errored += 1
            results.append(
                {
                    "index": i,
                    "status": "ERROR",
                    "identifier": row.product_id or row.sku,
                    "message": err,
                }
            )
            continue
        if existing > 0 and payload.skip_if_existing:
            rows_skipped += 1
            results.append(
                {
                    "index": i,
                    "status": "SKIPPED",
                    "product_id": product.get("product_id"),
                    "sku": product.get("sku"),
                    "existing": existing,
                    "message": f"Skipped — already has {existing} in stock.",
                }
            )
            continue

        pid = product.get("product_id")
        created_count = 0
        for _ in range(row.quantity):
            barcode = barcode_svc.next_unit_ean13(_counter) or generate_barcode(
                active_store or "STR", pid
            )
            stock_data = {
                "product_id": pid,
                "store_id": active_store,
                "barcode": barcode,
                "quantity": 1,
                "location_code": row.location_code or "DEFAULT",
                "batch_code": row.batch_code,
                "expiry_date": row.expiry_date.isoformat() if row.expiry_date else None,
                "status": "AVAILABLE",
                "is_reserved": False,
                "barcode_printed": False,
                "created_by": current_user.get("user_id"),
                "source": "OPENING_STOCK",
            }
            if stock_repo.create(stock_data):
                created_count += 1
        units_added += created_count
        results.append(
            {
                "index": i,
                "status": "ADDED",
                "product_id": pid,
                "sku": product.get("sku"),
                "added": created_count,
                "message": f"Added {created_count} unit(s).",
            }
        )

    return {
        "rows": results,
        "summary": {
            "total_rows": len(payload.rows),
            "units_added": units_added,
            "rows_skipped": rows_skipped,
            "rows_with_errors": rows_errored,
        },
    }


# ============================================================================
# STOCK AGING / NON-MOVING REPORT
# ============================================================================


@router.get("/aging")
async def get_stock_aging_report(
    store_id: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    classification: Optional[str] = Query(None, description="A, B, or C"),
    min_days: Optional[int] = Query(None, ge=0),
    current_user: dict = Depends(get_current_user),
):
    """
    Stock aging report — calculates days in stock, turnover rate,
    and ABC classification for each product in the store.
    Uses real stock + order data from MongoDB.
    """
    stock_repo = get_stock_repository()
    product_repo = get_product_repository()
    active_store = validate_store_access(store_id, current_user)

    # Category-filter fix: normalise short codes / plurals to canonical.
    if category:
        from ..services.product_master import resolve_category

        category = resolve_category(category) or category

    if stock_repo is None or product_repo is None:
        return {"products": [], "summary": {}}

    now = datetime.utcnow()
    thirty_days_ago = now - timedelta(days=30)
    ninety_days_ago = now - timedelta(days=90)

    # 1. Get all available stock grouped by product
    stock_pipeline = [
        {
            "$match": {
                "store_id": active_store,
                "status": {"$in": ["AVAILABLE", "RESERVED"]},
            }
        },
        {
            "$group": {
                "_id": "$product_id",
                "quantity": {"$sum": 1},
                "oldest_date": {"$min": "$created_at"},
                "total_value": {"$sum": {"$ifNull": ["$mrp", 0]}},
            }
        },
    ]
    stock_groups = stock_repo.aggregate(stock_pipeline)

    if not stock_groups:
        return {
            "products": [],
            "summary": {
                "total": 0,
                "classA": 0,
                "classB": 0,
                "classC": 0,
                "slowMovingValue": 0,
                "averageAge": 0,
            },
        }

    # 2. Get sold items in last 30 and 90 days for turnover calculation
    sold_30d_pipeline = [
        {
            "$match": {
                "store_id": active_store,
                "status": "SOLD",
                "sold_at": {"$gte": thirty_days_ago},
            }
        },
        {"$group": {"_id": "$product_id", "sales_30d": {"$sum": 1}}},
    ]
    sold_90d_pipeline = [
        {
            "$match": {
                "store_id": active_store,
                "status": "SOLD",
                "sold_at": {"$gte": ninety_days_ago},
            }
        },
        {"$group": {"_id": "$product_id", "sales_90d": {"$sum": 1}}},
    ]
    last_sale_pipeline = [
        {"$match": {"store_id": active_store, "status": "SOLD"}},
        {"$group": {"_id": "$product_id", "last_sale": {"$max": "$sold_at"}}},
    ]

    sales_30d = {
        r["_id"]: r["sales_30d"] for r in stock_repo.aggregate(sold_30d_pipeline)
    }
    sales_90d = {
        r["_id"]: r["sales_90d"] for r in stock_repo.aggregate(sold_90d_pipeline)
    }
    last_sales = {
        r["_id"]: r["last_sale"] for r in stock_repo.aggregate(last_sale_pipeline)
    }

    # 3. Enrich with product details and calculate metrics
    products = []
    for sg in stock_groups:
        pid = sg["_id"]
        product = product_repo.find_by_id(pid)
        if not product:
            # Catalog-only products are not in the products spine; fall back to
            # catalog_products using the helper from orders.py (reuse, not reimpl).
            try:
                from .orders import _resolve_catalog_product_doc
                product = _resolve_catalog_product_doc(pid)
            except Exception:
                product = None
        if not product:
            continue

        if category and product.get("category", "") != category:
            continue

        qty = sg.get("quantity", 0)
        oldest = sg.get("oldest_date")
        if isinstance(oldest, str):
            try:
                oldest = datetime.fromisoformat(oldest)
            except Exception:
                oldest = now
        days_in_stock = (now - oldest).days if oldest else 0

        s30 = sales_30d.get(pid, 0)
        s90 = sales_90d.get(pid, 0)
        last_sale = last_sales.get(pid)

        # Turnover rate (annualized from 90-day sales)
        turnover = (s90 / max(qty, 1)) * (365 / 90) if qty > 0 else 0

        # ABC classification based on turnover
        if turnover >= 4:
            cls = "A"
        elif turnover >= 1.5:
            cls = "B"
        else:
            cls = "C"

        # Age category
        if days_in_stock <= 30:
            age_cat = "0-30"
        elif days_in_stock <= 60:
            age_cat = "31-60"
        elif days_in_stock <= 90:
            age_cat = "61-90"
        elif days_in_stock <= 180:
            age_cat = "91-180"
        else:
            age_cat = "180+"

        mrp = product.get("mrp", 0) or 0
        value = qty * mrp

        if classification and cls != classification:
            continue
        if min_days is not None and days_in_stock < min_days:
            continue

        products.append(
            {
                "id": pid,
                "sku": product.get("sku", ""),
                "name": product.get("name", product.get("model", "")),
                "brand": product.get("brand", ""),
                "category": product.get("category", ""),
                "quantity": qty,
                "value": round(value, 2),
                "daysInStock": days_in_stock,
                "lastSaleDate": (
                    last_sale.isoformat()
                    if isinstance(last_sale, datetime)
                    else last_sale
                ),
                "salesLast30Days": s30,
                "salesLast90Days": s90,
                "turnoverRate": round(turnover, 1),
                "classification": cls,
                "ageCategory": age_cat,
            }
        )

    # Sort: Slow movers first (C, then B, then A), then by days in stock desc
    cls_order = {"C": 0, "B": 1, "A": 2}
    products.sort(
        key=lambda p: (cls_order.get(p["classification"], 1), -p["daysInStock"])
    )

    # Summary stats
    total = len(products)
    class_a = sum(1 for p in products if p["classification"] == "A")
    class_b = sum(1 for p in products if p["classification"] == "B")
    class_c = sum(1 for p in products if p["classification"] == "C")
    slow_value = sum(p["value"] for p in products if p["classification"] == "C")
    avg_age = sum(p["daysInStock"] for p in products) / max(total, 1)

    return {
        "products": products,
        "summary": {
            "total": total,
            "classA": class_a,
            "classB": class_b,
            "classC": class_c,
            "slowMovingValue": round(slow_value, 2),
            "averageAge": round(avg_age, 1),
            "oldStockCount": sum(1 for p in products if p["daysInStock"] > 90),
        },
    }


# ============================================================================
# STOCK COUNT / PHYSICAL VERIFICATION
# ============================================================================


@router.get("/stock-count")
async def list_stock_counts(
    store_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """List stock count sessions for the store"""
    active_store = validate_store_access(store_id, current_user)
    db = _get_db()

    if db is not None:
        try:
            collection = db.get_collection("stock_counts")
            query: Dict = {"store_id": active_store}
            if status:
                query["status"] = status
            counts = list(collection.find(query).sort("created_at", -1).limit(50))
            # Sanitize ObjectId
            for c in counts:
                c.pop("_id", None)
            return {"counts": counts}
        except Exception as e:
            logger.warning(f"stock_count list error: {e}")

    return {"counts": []}


@router.post("/stock-count/start")
async def start_stock_count(
    request: StartStockCountRequest,
    current_user: dict = Depends(require_roles(*_INVENTORY_ROLES)),
):
    """Start a new physical stock count session"""
    active_store = validate_store_access(None, current_user)
    stock_repo = get_stock_repository()
    db = _get_db()

    count_id = str(uuid.uuid4())
    now = datetime.utcnow()
    audit_number = f"AUDIT-{now.strftime('%y%m%d')}-{count_id[:6].upper()}"

    # Get system quantities for the category/store so we can calculate variances later.
    # INV-6: stock_units docs don't carry a `category` field (that lives on
    # the products collection), so we cannot filter the aggregation directly.
    # When a category is requested, first resolve the product_ids that belong
    # to it, then scope the stock aggregation to those ids. This ensures that
    # a category-limited count only snapshots the right products.
    system_quantities: Dict[str, int] = {}
    if stock_repo is not None:
        # Resolve category -> product_ids when filtering is requested.
        category_product_ids: Optional[List[str]] = None
        if request.category:
            product_repo = get_product_repository()
            if product_repo is not None:
                try:
                    cat_products = product_repo.find_many(
                        {"category": request.category, "is_active": True}, limit=5000
                    )
                    category_product_ids = [
                        str(p.get("product_id") or p.get("_id") or "")
                        for p in (cat_products or [])
                        if p.get("product_id") or p.get("_id")
                    ]
                except Exception as _exc:
                    logger.warning(
                        "[INVENTORY] category product lookup failed: %s", _exc
                    )

        match_clause: dict = {
            "store_id": active_store,
            "status": {"$in": ["AVAILABLE", "RESERVED"]},
        }
        if category_product_ids is not None:
            # Empty list means no products match -- yield no system quantities
            # rather than counting all products (which would be wrong).
            if not category_product_ids:
                pass  # system_quantities stays empty; skip the aggregation
            else:
                match_clause["product_id"] = {"$in": category_product_ids}

        if category_product_ids is None or category_product_ids:
            pipeline = [
                {"$match": match_clause},
                {"$group": {"_id": "$product_id", "qty": {"$sum": 1}}},
            ]
            for r in stock_repo.aggregate(pipeline):
                system_quantities[r["_id"]] = r["qty"]

    count_doc = {
        "count_id": count_id,
        "audit_number": audit_number,
        "store_id": active_store,
        "category": request.category,
        "zone": request.zone,
        "notes": request.notes,
        "status": "in_progress",
        "created_at": now.isoformat(),
        "created_by": current_user.get("user_id", ""),
        "created_by_name": current_user.get(
            "full_name", current_user.get("username", "")
        ),
        "items": [],
        "system_quantities": system_quantities,
        "completed_at": None,
        "variances": [],
        "items_counted": 0,
        "variance_percentage": None,
        "shrinkage_percentage": None,
    }

    if db is not None:
        try:
            db.get_collection("stock_counts").insert_one(count_doc)
        except Exception as e:
            logger.warning(f"stock_count create error: {e}")

    # Remove _id if present
    count_doc.pop("_id", None)
    return count_doc


@router.post("/stock-count/{count_id}/items")
async def record_count_item(
    count_id: str,
    item: StockCountItem,
    current_user: dict = Depends(require_roles(*_INVENTORY_ROLES)),
):
    """Record a counted item in an active stock count session"""
    db = _get_db()

    if db is None:
        return {"message": "Item recorded (no DB)", "count_id": count_id}

    try:
        collection = db.get_collection("stock_counts")
        count_doc = collection.find_one({"count_id": count_id})
        if not count_doc:
            raise HTTPException(status_code=404, detail="Stock count session not found")
        if not can_access_store_scoped(count_doc.get("store_id"), current_user):
            raise HTTPException(status_code=404, detail="Stock count session not found")
        if count_doc.get("status") != "in_progress":
            raise HTTPException(
                status_code=400, detail="Stock count is not in progress"
            )

        # Upsert item: if product already counted, update; else append
        items = count_doc.get("items", [])
        found = False
        for existing in items:
            if existing["product_id"] == item.product_id:
                existing["counted_quantity"] = item.counted_quantity
                existing["notes"] = item.notes
                existing["counted_at"] = datetime.utcnow().isoformat()
                existing["counted_by"] = current_user.get("user_id", "")
                found = True
                break

        if not found:
            items.append(
                {
                    "product_id": item.product_id,
                    "product_name": item.product_name or "",
                    "sku": item.sku or "",
                    "counted_quantity": item.counted_quantity,
                    "notes": item.notes,
                    "counted_at": datetime.utcnow().isoformat(),
                    "counted_by": current_user.get("user_id", ""),
                }
            )

        collection.update_one(
            {"count_id": count_id},
            {"$set": {"items": items, "items_counted": len(items)}},
        )

        return {
            "message": "Item recorded",
            "count_id": count_id,
            "items_counted": len(items),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"record_count_item error: {e}")
        return {"message": "Item recorded", "count_id": count_id}


@router.post("/stock-count/{count_id}/complete")
async def complete_stock_count(
    count_id: str,
    request: Optional[CompleteStockCountRequest] = None,
    current_user: dict = Depends(require_roles(*_INVENTORY_ROLES)),
):
    """Complete stock count — calculates variances between system and physical count"""
    db = _get_db()

    if db is None:
        return {"message": "Stock count completed", "variances": []}

    try:
        collection = db.get_collection("stock_counts")
        count_doc = collection.find_one({"count_id": count_id})
        if not count_doc:
            raise HTTPException(status_code=404, detail="Stock count session not found")
        if not can_access_store_scoped(count_doc.get("store_id"), current_user):
            raise HTTPException(status_code=404, detail="Stock count session not found")
        if count_doc.get("status") != "in_progress":
            raise HTTPException(
                status_code=400, detail="Stock count is not in progress"
            )

        system_quantities = count_doc.get("system_quantities", {})
        items = count_doc.get("items", [])

        # Calculate variances
        variances = []
        total_system = 0
        total_counted = 0
        total_shrinkage = 0

        for item in items:
            pid = item["product_id"]
            counted = item["counted_quantity"]
            system = system_quantities.get(pid, 0)
            variance = counted - system
            var_pct = round((variance / max(system, 1)) * 100, 2)

            total_system += system
            total_counted += counted
            if variance < 0:
                total_shrinkage += abs(variance)

            variances.append(
                {
                    "product_id": pid,
                    "product_name": item.get("product_name", ""),
                    "sku": item.get("sku", ""),
                    "system_quantity": system,
                    "physical_quantity": counted,
                    "variance": variance,
                    "variance_percentage": var_pct,
                }
            )

        # Overall metrics
        overall_var_pct = round(
            ((total_counted - total_system) / max(total_system, 1)) * 100, 2
        )
        shrinkage_pct = round((total_shrinkage / max(total_system, 1)) * 100, 2)

        now = datetime.utcnow()
        update_data = {
            "status": "completed",
            "completed_at": now.isoformat(),
            "completed_by": current_user.get("user_id", ""),
            "variances": variances,
            "variance_percentage": overall_var_pct,
            "shrinkage_percentage": shrinkage_pct,
            "notes": request.notes if request else None,
        }
        collection.update_one({"count_id": count_id}, {"$set": update_data})

        # Variance -> accountable SYSTEM task (fail-soft; deduped per count).
        try:
            from ..services.task_triggers import (
                create_system_task,
                stock_variance_priority,
            )
            from ..dependencies import get_task_repository

            pri = stock_variance_priority(shrinkage_pct, overall_var_pct)
            if pri:
                create_system_task(
                    get_task_repository(),
                    title=f"Stock-count variance: {count_doc.get('audit_number', count_id)}",
                    description=(
                        f"Shrinkage {shrinkage_pct}% / overall variance {overall_var_pct}% "
                        f"across {len(items)} items. Investigate and reconcile."
                    ),
                    priority=pri,
                    category="Inventory",
                    store_id=count_doc.get("store_id")
                    or current_user.get("active_store_id"),
                    dedupe_ref=f"stockcount:{count_id}",
                )
        except Exception as _e:  # noqa: BLE001
            logger.warning(f"[INVENTORY] variance task creation skipped: {_e}")

        return {
            "message": "Stock count completed",
            "count_id": count_id,
            "audit_number": count_doc.get("audit_number", ""),
            "items_counted": len(items),
            "variance_percentage": overall_var_pct,
            "shrinkage_percentage": shrinkage_pct,
            "variances": variances,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"complete_stock_count error: {e}")
        return {"message": "Stock count completed", "variances": []}


@router.get("/stock-count/{count_id}")
async def get_stock_count(
    count_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Get details of a specific stock count session"""
    db = _get_db()

    if db is None:
        raise HTTPException(status_code=404, detail="Stock count not found")

    try:
        collection = db.get_collection("stock_counts")
        count_doc = collection.find_one({"count_id": count_id})
        if not count_doc:
            raise HTTPException(status_code=404, detail="Stock count session not found")
        if not can_access_store_scoped(count_doc.get("store_id"), current_user):
            raise HTTPException(status_code=404, detail="Stock count session not found")
        count_doc.pop("_id", None)
        return count_doc
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"get_stock_count error: {e}")
        raise HTTPException(status_code=500, detail="Internal error")


# INV-8: Guided cycle-count reconcile step
# After completing a count, the manager reviews variances and applies the
# physical counts to the stock ledger.  Negative variances (shrinkage) are
# written to the `stock_shrinkage` audit collection; positive ones (overages)
# are left for manual investigation (we never silently inflate stock).
# The count is transitioned to status="reconciled" so it cannot be
# re-reconciled.  Fail-soft: DB unavailable returns a 503 (not a silent 200)
# because reconciliation is a stock-altering write, not a read.


class ReconcileStockCountRequest(BaseModel):
    notes: Optional[str] = None
    # Per-item overrides: the reviewer can accept a different final quantity
    # for specific items before writing.  If not supplied, the counted_quantity
    # from the completed count is used.
    overrides: Optional[List[Dict]] = None  # [{product_id, accepted_quantity}]


@router.post("/stock-count/{count_id}/reconcile")
async def reconcile_stock_count(
    count_id: str,
    request: Optional[ReconcileStockCountRequest] = None,
    current_user: dict = Depends(require_roles(*_INVENTORY_ROLES)),
):
    """Apply a completed cycle-count to the stock ledger (INV-8).

    For each variance line:
    - Negative variance (shrinkage): written to ``stock_shrinkage`` for audit
      and the stock_units document quantity is adjusted down.
    - Positive variance (overage): recorded for review but NOT silently inflated
      (SYSTEM_INTENT: fail loudly / never fabricate stock).

    Transitions the count document to ``status="reconciled"``.
    """
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    try:
        counts_coll = db.get_collection("stock_counts")
        shrinkage_coll = db.get_collection("stock_shrinkage")
        stock_coll = db.get_collection("stock_units")

        count_doc = counts_coll.find_one({"count_id": count_id})
        if not count_doc:
            raise HTTPException(status_code=404, detail="Stock count session not found")
        if not can_access_store_scoped(count_doc.get("store_id"), current_user):
            raise HTTPException(status_code=404, detail="Stock count session not found")
        if count_doc.get("status") != "completed":
            raise HTTPException(
                status_code=400,
                detail=(
                    "Only completed counts can be reconciled "
                    f"(current status: {count_doc.get('status')})"
                ),
            )

        variances = count_doc.get("variances", [])
        if not variances:
            raise HTTPException(
                status_code=400,
                detail="No variance data found — complete the count first",
            )

        # Build override map if supplied
        override_map: Dict[str, int] = {}
        if request and request.overrides:
            for ov in request.overrides:
                pid = ov.get("product_id") or ""
                qty = ov.get("accepted_quantity")
                if pid and qty is not None:
                    override_map[pid] = max(0, int(qty))

        now = datetime.utcnow()
        store_id = count_doc.get("store_id", "")
        shrinkage_records = []
        overage_records = []
        reconciled_items = []

        for v in variances:
            pid = v.get("product_id", "")
            system_qty = int(v.get("system_quantity", 0) or 0)
            counted_qty = int(v.get("physical_quantity", 0) or 0)
            accepted_qty = override_map.get(pid, counted_qty)
            net_variance = accepted_qty - system_qty

            reconciled_items.append(
                {
                    "product_id": pid,
                    "product_name": v.get("product_name", ""),
                    "sku": v.get("sku", ""),
                    "system_quantity": system_qty,
                    "physical_quantity": counted_qty,
                    "accepted_quantity": accepted_qty,
                    "net_variance": net_variance,
                }
            )

            if net_variance < 0:
                # Shrinkage: write an audit record.
                # Stock units are serialized (one row per unit) so we
                # VOID the excess rows rather than decrementing a counter.
                shrinkage_qty = abs(net_variance)
                shrinkage_records.append(
                    {
                        "shrinkage_id": str(uuid.uuid4()),
                        "count_id": count_id,
                        "audit_number": count_doc.get("audit_number", ""),
                        "store_id": store_id,
                        "product_id": pid,
                        "product_name": v.get("product_name", ""),
                        "sku": v.get("sku", ""),
                        "shrinkage_quantity": shrinkage_qty,
                        "system_quantity": system_qty,
                        "accepted_quantity": accepted_qty,
                        "recorded_at": now.isoformat(),
                        "recorded_by": current_user.get("user_id", ""),
                        "notes": request.notes if request else None,
                    }
                )
                # Void the oldest AVAILABLE units to reconcile stock
                try:
                    candidates = list(
                        stock_coll.find(
                            {
                                "product_id": pid,
                                "store_id": store_id,
                                "status": "AVAILABLE",
                            },
                            sort=[("created_at", 1)],
                            limit=shrinkage_qty,
                        )
                    )
                    if candidates:
                        ids_to_void = [c["_id"] for c in candidates if "_id" in c]
                        if ids_to_void:
                            stock_coll.update_many(
                                {"_id": {"$in": ids_to_void}},
                                {
                                    "$set": {
                                        "status": "VOID",
                                        "voided_at": now.isoformat(),
                                        "void_reason": f"cycle-count-reconcile:{count_id}",
                                    }
                                },
                            )
                except Exception as _e:
                    logger.warning(f"[INV-8] stock void skipped for {pid}: {_e}")

            elif net_variance > 0:
                # Overage: record for investigation only — do not inflate stock.
                overage_records.append(
                    {
                        "product_id": pid,
                        "product_name": v.get("product_name", ""),
                        "overage_quantity": net_variance,
                    }
                )

        # Persist shrinkage audit rows
        if shrinkage_records:
            try:
                shrinkage_coll.insert_many(shrinkage_records)
            except Exception as _e:
                logger.warning(f"[INV-8] shrinkage insert skipped: {_e}")

        # Mark count as reconciled
        counts_coll.update_one(
            {"count_id": count_id},
            {
                "$set": {
                    "status": "reconciled",
                    "reconciled_at": now.isoformat(),
                    "reconciled_by": current_user.get("user_id", ""),
                    "reconciliation_notes": request.notes if request else None,
                    "reconciled_items": reconciled_items,
                    "shrinkage_count": len(shrinkage_records),
                    "overage_count": len(overage_records),
                }
            },
        )

        return {
            "message": "Stock count reconciled",
            "count_id": count_id,
            "audit_number": count_doc.get("audit_number", ""),
            "items_reconciled": len(reconciled_items),
            "shrinkage_lines": len(shrinkage_records),
            "overage_lines": len(overage_records),
            "overages_pending_review": overage_records,
            "reconciled_at": now.isoformat(),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"reconcile_stock_count error: {e}")
        raise HTTPException(
            status_code=500, detail="Internal error during reconciliation"
        )


# ============================================================================
# INVENTORY INTELLIGENCE: transfer recommendations + staff accountability
# ============================================================================

_STOCK_MANAGER_ROLES = ("ADMIN", "AREA_MANAGER", "STORE_MANAGER")


class AccountabilityAssign(BaseModel):
    store_id: str
    category: Optional[str] = "ALL"
    staff_id: str
    staff_name: Optional[str] = None


@router.get("/transfer-recommendations")
async def transfer_recommendations(
    store_id: Optional[str] = Query(None),
    threshold: int = Query(5, ge=0, le=1000),
    current_user: dict = Depends(require_roles(*_INVENTORY_ROLES)),
):
    """Suggest inter-store transfers to refill the active store's low/out
    products from other stores that hold a surplus. Fail-soft."""
    from ..services.inventory_intel import recommend_transfers

    stock_repo = get_stock_repository()
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")
    if stock_repo is None or not active_store:
        return {"recommendations": [], "store_id": active_store}

    try:
        low = stock_repo.find_low_stock(active_store, threshold) or []
        low_ids = [r["_id"] for r in low if r.get("_id")]
        if not low_ids:
            return {"recommendations": [], "store_id": active_store}

        # Cross-store available levels for just the deficit products.
        rows = (
            stock_repo.aggregate(
                [
                    {"$match": {"product_id": {"$in": low_ids}, "status": "AVAILABLE"}},
                    {
                        "$group": {
                            "_id": {"p": "$product_id", "s": "$store_id"},
                            # One row == one unit; missing quantity counts as 1.
                            "qty": {"$sum": {"$ifNull": ["$quantity", 1]}},
                        }
                    },
                ]
            )
            or []
        )
        store_levels: Dict[str, Dict[str, int]] = {}
        for r in rows:
            key = r.get("_id", {})
            store_levels.setdefault(key.get("p"), {})[key.get("s")] = int(
                r.get("qty", 0) or 0
            )

        # Enrich with product names.
        names: Dict[str, str] = {}
        product_repo = get_product_repository()
        if product_repo is not None:
            for p in product_repo.find_many({"product_id": {"$in": low_ids}}) or []:
                names[p.get("product_id")] = (
                    p.get("name") or p.get("product_name") or ""
                )

        low_products = [
            {
                "product_id": r["_id"],
                "quantity": int(r.get("quantity", 0) or 0),
                "product_name": names.get(r["_id"], ""),
            }
            for r in low
            if r.get("_id")
        ]
        recs = recommend_transfers(
            active_store, low_products, store_levels, threshold=threshold
        )
        return {
            "store_id": active_store,
            "threshold": threshold,
            "recommendations": recs,
            "count": len(recs),
        }
    except Exception as e:  # noqa: BLE001
        logger.warning(f"transfer_recommendations error: {e}")
        return {"recommendations": [], "store_id": active_store}


@router.get("/cross-store-stock")
async def cross_store_stock(
    product_id: str = Query(..., description="Product ID to look up across stores"),
    exclude_store_id: Optional[str] = Query(
        None, description="Omit this store from results (usually the requesting store)"
    ),
    current_user: dict = Depends(require_roles(*_INVENTORY_ROLES)),
):
    """POS-7 BOPIS / ship-from-store: find which stores hold available stock for
    a product and how many units each carries.

    Returns stores ordered by available quantity descending so the caller can
    immediately suggest the best source for a cross-store reservation.

    Fail-soft: empty list on DB unavailable.
    """
    stock_repo = get_stock_repository()
    product_repo = get_product_repository()
    if stock_repo is None:
        return {"product_id": product_id, "stores": []}

    try:
        rows = (
            stock_repo.aggregate(
                [
                    {
                        "$match": {
                            "product_id": product_id,
                            "status": "AVAILABLE",
                        }
                    },
                    {
                        "$group": {
                            "_id": "$store_id",
                            "quantity": {"$sum": {"$ifNull": ["$quantity", 1]}},
                        }
                    },
                ]
            )
            or []
        )

        # Enrich with product name (once)
        product_name = ""
        if product_repo is not None:
            p = product_repo.find_by_id(product_id)
            if p:
                product_name = p.get("name") or p.get("product_name") or ""

        stores = []
        for r in rows:
            sid = r.get("_id")
            if not sid:
                continue
            if exclude_store_id and sid == exclude_store_id:
                continue
            qty = int(r.get("quantity") or 0)
            if qty <= 0:
                continue
            stores.append({"store_id": sid, "available_qty": qty})

        # backlog #4: add a human store_name beside each store_id.
        try:
            from ..services.name_resolver import store_name_map

            smap = store_name_map(_get_db(), [s["store_id"] for s in stores])
            for s in stores:
                s["store_name"] = smap.get(str(s["store_id"]), s["store_id"])
        except Exception:  # noqa: BLE001
            pass

        stores.sort(key=lambda x: x["available_qty"], reverse=True)

        return {
            "product_id": product_id,
            "product_name": product_name,
            "stores": stores,
        }
    except Exception as e:  # noqa: BLE001
        logger.warning("cross_store_stock error: %s", e)
        return {"product_id": product_id, "stores": []}


@router.post("/accountability")
async def assign_accountability(
    body: AccountabilityAssign,
    current_user: dict = Depends(require_roles(*_STOCK_MANAGER_ROLES)),
):
    """Assign a staff member as the stock custodian for a store (+ optional
    category), so count shrinkage can be attributed to them."""
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database not available")
    coll = db.get_collection("stock_accountability")
    key = {"store_id": body.store_id, "category": body.category or "ALL"}
    coll.update_one(
        key,
        {
            "$set": {
                **key,
                "staff_id": body.staff_id,
                "staff_name": body.staff_name,
                "assigned_by": current_user.get("user_id"),
                "assigned_at": datetime.now().isoformat(),
            }
        },
        upsert=True,
    )
    return {"message": "Custodian assigned", **key, "staff_id": body.staff_id}


@router.get("/accountability")
async def list_accountability(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(require_roles(*_INVENTORY_ROLES)),
):
    """List stock custodians for a store."""
    db = _get_db()
    if db is None:
        return {"custodians": []}
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")
    q = {"store_id": active_store} if active_store else {}
    try:
        items = list(db.get_collection("stock_accountability").find(q, {"_id": 0}))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"list_accountability error: {e}")
        return {"custodians": []}
    return {"custodians": items, "total": len(items)}


@router.get("/accountability/shrinkage")
async def accountability_shrinkage(
    store_id: Optional[str] = Query(None),
    days: int = Query(90, ge=1, le=365),
    current_user: dict = Depends(require_roles(*_STOCK_MANAGER_ROLES)),
):
    """Recent completed-count shrinkage attributed to each store's custodian."""
    from ..services.inventory_intel import shrinkage_by_custodian

    db = _get_db()
    if db is None:
        return {"rows": []}
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    q: dict = {"status": "completed", "completed_at": {"$gte": cutoff}}
    if active_store:
        q["store_id"] = active_store
    try:
        counts = list(
            db.get_collection("stock_counts").find(
                q,
                {
                    "_id": 0,
                    "store_id": 1,
                    "audit_number": 1,
                    "shrinkage_percentage": 1,
                    "completed_at": 1,
                },
            )
        )
        custodians = {
            c["store_id"]: c
            for c in db.get_collection("stock_accountability").find(
                {"category": "ALL"}, {"_id": 0}
            )
            if c.get("store_id")
        }
        rows = shrinkage_by_custodian(counts, custodians)
        # backlog #4: show the store NAME beside (or instead of) the store id.
        try:
            from ..services.name_resolver import store_name_map

            smap = store_name_map(db, [r.get("store_id") for r in rows])
            for r in rows:
                sid = r.get("store_id")
                if sid and str(sid) in smap:
                    r["store_name"] = smap[str(sid)]
        except Exception:  # noqa: BLE001
            pass
        return {
            "rows": rows,
            "count": len(counts),
        }
    except Exception as e:  # noqa: BLE001
        logger.warning(f"accountability_shrinkage error: {e}")
        return {"rows": []}


# ============================================================================
# TRANSFER STUBS (real transfers are in transfers.py router)
# ============================================================================


@router.get("/transfers")
async def list_transfers(
    store_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """List stock transfers — delegates to /transfers router for full implementation"""
    # This endpoint exists for backwards compatibility; the full transfer
    # workflow lives in transfers.py with approval/picking/shipping states
    return {
        "transfers": [],
        "note": "Use /api/v1/transfers for full transfer management",
    }


@router.post("/transfers")
async def create_transfer(
    request: StockTransferRequest,
    current_user: dict = Depends(require_roles(*_INVENTORY_ROLES)),
):
    """Create a stock transfer — delegates to /transfers for full workflow"""
    return {
        "transfer_id": str(uuid.uuid4()),
        "transfer_number": f"TRF-{uuid.uuid4().hex[:6].upper()}",
        "note": "Use /api/v1/transfers for full transfer management",
    }


# BUG-018: the legacy POST /inventory/transfers/{id}/send and
# /inventory/transfers/{id}/receive endpoints were DEAD STUBS -- they returned a
# hardcoded success message and moved NO stock. A caller could "send" or
# "receive" a transfer and get a 200 while both stores' on-hand stayed wrong.
# They were REMOVED (verified no caller: the frontend uses the REAL workflow at
# POST /api/v1/transfers/{id}/ship and /api/v1/transfers/{id}/receive in
# transfers.py, which actually move serialized stock_units). Callers must use the
# real /transfers/* router -- a success response now always means stock moved.


# ============================================================================
# ADVANCED INVENTORY FEATURES (IMS 2.0)
# ============================================================================

# Broad "this order represents a real sale" status set (both cases seen in DB).
# Defined here — before its first use in get_non_moving_stock — so it is
# unambiguously initialised before ANY call-site (including the functions below
# that also reference it: get_sell_through_analysis, get_overstock_analysis,
# get_stock_alerts, _aggregate_sales_by_barcode).
_SOLD_STATUSES = [
    "DELIVERED",
    "delivered",
    "Delivered",
    "COMPLETED",
    "completed",
    "Completed",
    "PAID",
    "paid",
    "Paid",
    "FULFILLED",
    "fulfilled",
    "Fulfilled",
]

# ============================================================================
# 1. NON-MOVING STOCK IDENTIFICATION
# ============================================================================


@router.get("/non-moving")
async def get_non_moving_stock(
    days: int = Query(90, ge=1, le=365),
    category: Optional[str] = Query(None),
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """
    Identify products with 0 sales in the last N days.
    GET /inventory/non-moving?days=90
    Scoped to the active store unless an explicit store_id is supplied.
    """
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection error")

    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")

    try:
        products_coll = db.get_collection("products")
        orders_coll = db.get_collection("orders")
        stock_coll = db.get_collection("stock_units")

        # Get all products (optionally filtered by category). Normalise short
        # codes / plurals to the canonical value the docs store (fail-open).
        if category:
            from ..services.product_master import resolve_category

            category = resolve_category(category) or category
        query = {} if not category else {"category": category}
        products = list(products_coll.find(query, {"_id": 1, "name": 1, "sku": 1}))

        # Get products with sales in last N days (at the active store)
        cutoff_date = datetime.utcnow() - timedelta(days=days)
        sold_products = set()

        orders_filter = {
            "created_at": {"$gte": cutoff_date},
            "status": {"$in": _SOLD_STATUSES},
        }
        if active_store:
            orders_filter["store_id"] = active_store
        orders = orders_coll.find(orders_filter, {"items": 1})

        for order in orders:
            for item in order.get("items", []):
                sold_products.add(item.get("product_id"))

        # Find non-moving products
        non_moving = []
        for product in products:
            product_id = str(product.get("_id"))
            if product_id not in sold_products:
                # BUG FIX: count ONLY on-hand (AVAILABLE/RESERVED) units.
                # Previously, ALL stock_units rows were counted regardless of
                # status, so SOLD units inflated current_stock and a product
                # with 10 sold and 0 available showed current_stock=10.
                stock_filter = {
                    "product_id": product_id,
                    "status": {
                        "$in": ["AVAILABLE", "RESERVED", "available", "reserved"]
                    },
                }
                if active_store:
                    stock_filter["store_id"] = active_store
                stock = stock_coll.find(stock_filter)
                # One serialized stock row == one physical unit; rows with no
                # `quantity` field still count as one unit on hand.
                total_qty = sum(s.get("quantity", 1) for s in stock)

                # Get last sold date (at the active store)
                last_order_filter = {"items.product_id": product_id}
                if active_store:
                    last_order_filter["store_id"] = active_store
                last_order = orders_coll.find_one(
                    last_order_filter,
                    {"created_at": 1},
                    sort=[("created_at", -1)],
                )

                # BUG FIX: days_since_sale was always set to the query
                # parameter `days` instead of the actual days elapsed since
                # the last sale. Products with a last_sold_date showed the
                # wrong staleness figure in the non-moving report.
                last_sold_dt = None
                if last_order:
                    raw_date = last_order.get("created_at")
                    if isinstance(raw_date, datetime):
                        last_sold_dt = raw_date
                    elif isinstance(raw_date, str):
                        try:
                            last_sold_dt = datetime.fromisoformat(
                                raw_date.replace("Z", "+00:00").split("+")[0]
                            )
                        except (ValueError, TypeError):
                            pass
                if last_sold_dt is not None:
                    actual_days_since = (datetime.utcnow() - last_sold_dt).days
                else:
                    actual_days_since = None  # never sold

                non_moving.append(
                    {
                        "product_id": product_id,
                        "name": product.get("name", ""),
                        "sku": product.get("sku", ""),
                        "current_stock": total_qty,
                        "last_sold_date": (
                            last_sold_dt.isoformat() if last_sold_dt else None
                        ),
                        "days_since_sale": actual_days_since,
                    }
                )

        return {
            "total": len(non_moving),
            "days_threshold": days,
            "products": sorted(
                non_moving, key=lambda x: x["current_stock"], reverse=True
            )[:100],
        }

    except Exception as e:
        logger.error(f"get_non_moving_stock error: {e}")
        raise HTTPException(status_code=500, detail="Error fetching non-moving stock")


# ============================================================================
# 2. STOCK COUNT SCANNING INTERFACE
# ============================================================================


class BarcodeScanRequest(BaseModel):
    barcode: str
    physical_count: int = Field(..., ge=0)
    notes: Optional[str] = None


@router.post("/stock-count-scan")
async def scan_barcode_for_count(
    request: BarcodeScanRequest,
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(require_roles(*_INVENTORY_ROLES)),
):
    """
    Scan barcode and record physical count.
    POST /inventory/stock-count-scan
    """
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection error")

    try:
        stock_coll = db.get_collection("stock_units")
        products_coll = db.get_collection("products")

        # Find stock by barcode
        stock = stock_coll.find_one({"barcode": request.barcode})
        if not stock:
            raise HTTPException(status_code=404, detail="Barcode not found")

        product_id = stock.get("product_id")
        product = products_coll.find_one({"_id": product_id})

        # The scanned barcode is one unit, but a stock count compares the
        # PHYSICAL count of a product against its on-hand SYSTEM count at this
        # store. Count the available serialized rows for the product (one row
        # == one unit; legacy rows have no `quantity` field, so $ifNull treats
        # a missing value as 1). Reading the scanned unit's raw `quantity`
        # returned 0 and produced a false +physical_count variance.
        count_match = {
            "product_id": product_id,
            "status": {"$in": ["AVAILABLE", "RESERVED"]},
        }
        unit_store = stock.get("store_id")
        if unit_store:
            count_match["store_id"] = unit_store
        agg = list(
            stock_coll.aggregate(
                [
                    {"$match": count_match},
                    {
                        "$group": {
                            "_id": None,
                            "n": {"$sum": {"$ifNull": ["$quantity", 1]}},
                        }
                    },
                ]
            )
        )
        system_count = int(agg[0]["n"]) if agg else 0

        # Products store no `name` field -- reconstruct from brand + model,
        # matching the convention used across aging / reports / serializer.
        if product:
            brand = product.get("brand", "")
            model = product.get("model", "")
            product_name = (
                product.get("name")
                or f"{brand} {model}".strip()
                or product.get("sku", "")
                or "Unknown"
            )
            sku = product.get("sku", "")
        else:
            product_name = "Unknown"
            sku = ""

        variance = request.physical_count - system_count

        return {
            "barcode": request.barcode,
            "product_id": product_id,
            "product_name": product_name,
            "sku": sku,
            "system_count": system_count,
            "physical_count": request.physical_count,
            "variance": variance,
            "variance_percent": round((variance / max(system_count, 1)) * 100, 2),
            "notes": request.notes,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"scan_barcode_for_count error: {e}")
        raise HTTPException(status_code=500, detail="Error processing barcode scan")


# ============================================================================
# 3. CONTACT LENS (CL) INVENTORY + BATCH/EXPIRY TRACKING
# ============================================================================


def _load_cl_stock_rows(db, store_id: Optional[str]) -> List[dict]:
    """Fetch AVAILABLE contact-lens stock joined to its CL product.

    One row per stock unit (matches the serialized one-row-per-unit model).
    Each row carries the CL identity fields off the product so callers can
    group by brand / power / base_curve / modality. Store-scoped when a
    store_id is given. Fail-soft: returns [] on any error or missing DB.
    """
    if db is None:
        return []
    try:
        stock_coll = db.get_collection("stock_units")
        products_coll = db.get_collection("products")

        # 1. Resolve the CL product ids first (category lives on the PRODUCT).
        cl_products = list(products_coll.find({"category": {"$in": CL_CATEGORY_CODES}}))
        if not cl_products:
            return []

        # Index products by every id key a stock row might reference.
        prod_by_id: Dict[str, dict] = {}
        for p in cl_products:
            for key in (p.get("product_id"), p.get("_id")):
                if key is not None:
                    prod_by_id[str(key)] = p

        cl_product_ids = list(prod_by_id.keys())

        # 2. Pull AVAILABLE stock for those products (store-scoped).
        stock_filter: Dict[str, object] = {
            "product_id": {"$in": cl_product_ids},
            "status": {"$in": ["AVAILABLE", "RESERVED"]},
        }
        if store_id:
            stock_filter["store_id"] = store_id

        rows: List[dict] = []
        for s in stock_coll.find(stock_filter):
            prod = prod_by_id.get(str(s.get("product_id"))) or {}
            rows.append(
                {
                    "stock_id": str(s.get("stock_id") or s.get("_id") or ""),
                    "product_id": str(s.get("product_id") or ""),
                    "store_id": s.get("store_id"),
                    "sku": prod.get("sku", ""),
                    "brand": prod.get("brand", ""),
                    "model": prod.get("model", ""),
                    "category": prod.get("category", ""),
                    "cl_series": prod.get("cl_series"),
                    "modality": prod.get("modality"),
                    "base_curve": prod.get("base_curve"),
                    "diameter": prod.get("diameter"),
                    "cl_power": prod.get("cl_power"),
                    "cl_cyl": prod.get("cl_cyl"),
                    "cl_axis": prod.get("cl_axis"),
                    "cl_add": prod.get("cl_add"),
                    "color": prod.get("color"),
                    "pack_size": prod.get("pack_size"),
                    "batch_code": s.get("batch_code") or s.get("lot"),
                    "expiry_date": s.get("expiry_date"),
                    "location_code": s.get("location_code"),
                }
            )
        return rows
    except Exception as e:  # noqa: BLE001 - fail soft
        logger.error("_load_cl_stock_rows error: %s", e)
        return []


def _group_cl_rows(rows: List[dict], now: Optional[datetime] = None) -> List[dict]:
    """Group per-unit CL rows into SKU x batch lines with on-hand qty + expiry.

    Grouping key = product_id + batch_code + expiry_date so each distinct batch
    surfaces its own nearest-expiry. Pure helper (no DB)."""
    now = now or datetime.utcnow()
    groups: Dict[tuple, dict] = {}
    for r in rows:
        key = (r.get("product_id"), r.get("batch_code"), r.get("expiry_date"))
        g = groups.get(key)
        if g is None:
            g = {
                k: r.get(k)
                for k in (
                    "product_id",
                    "sku",
                    "brand",
                    "model",
                    "category",
                    "cl_series",
                    "modality",
                    "base_curve",
                    "diameter",
                    "cl_power",
                    "cl_cyl",
                    "cl_axis",
                    "cl_add",
                    "color",
                    "pack_size",
                    "batch_code",
                    "expiry_date",
                    "location_code",
                )
            }
            g["on_hand"] = 0
            g["days_until_expiry"] = compute_days_until_expiry(
                r.get("expiry_date"), now
            )
            groups[key] = g
        g["on_hand"] += 1

    grouped = list(groups.values())
    # FEFO-style ordering on the lines: earliest expiry first, undated last.
    grouped.sort(
        key=lambda g: (
            g.get("days_until_expiry") is None,
            (
                g.get("days_until_expiry")
                if g.get("days_until_expiry") is not None
                else 10**9
            ),
        )
    )
    return grouped


@router.get("/contact-lenses")
async def list_contact_lens_inventory(
    store_id: Optional[str] = Query(None),
    brand: Optional[str] = Query(None),
    modality: Optional[str] = Query(None),
    base_curve: Optional[float] = Query(None),
    cl_power: Optional[float] = Query(None),
    near_expiry_days: Optional[int] = Query(
        None,
        ge=1,
        le=365,
        description="If set, only return lines expiring within N days",
    ),
    current_user: dict = Depends(get_current_user),
):
    """
    Contact-lens inventory grouped by SKU x batch (brand / power / base-curve /
    modality), with on-hand qty, nearest expiry and pack info.

    GET /inventory/contact-lenses?brand=Acuvue&modality=DAILY&near_expiry_days=90
    Store-scoped. Fail-soft: returns an empty list when DB is unavailable.
    """
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")
    db = _get_db()
    rows = _load_cl_stock_rows(db, active_store)

    # Optional in-memory filters (small CL footprint; keeps the query simple).
    if brand:
        rows = [r for r in rows if (r.get("brand") or "").lower() == brand.lower()]
    if modality:
        rows = [
            r for r in rows if (r.get("modality") or "").upper() == modality.upper()
        ]
    if base_curve is not None:
        rows = [r for r in rows if r.get("base_curve") == base_curve]
    if cl_power is not None:
        rows = [r for r in rows if r.get("cl_power") == cl_power]

    grouped = _group_cl_rows(rows)

    if near_expiry_days is not None:
        grouped = [
            g
            for g in grouped
            if g.get("days_until_expiry") is not None
            and g["days_until_expiry"] <= near_expiry_days
        ]

    total_units = sum(g.get("on_hand", 0) for g in grouped)
    return {
        "items": grouped,
        "total_lines": len(grouped),
        "total_units": total_units,
        "store_id": active_store,
    }


@router.get("/contact-lenses/expiry-status")
async def get_contact_lens_expiry_status(
    expiring_within_days: int = Query(90, ge=1, le=365),
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """
    Contact-lens stock partitioned into expired / expiring-soon / safe plus a
    FEFO (First-Expiry-First-Out) pick suggestion. `expiring_within_days` is the
    configurable near-expiry alert window.
    GET /inventory/contact-lenses/expiry-status?expiring_within_days=90
    Store-scoped. Fail-soft: returns empty buckets when DB is unavailable.
    """
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")
    db = _get_db()
    rows = _load_cl_stock_rows(db, active_store)

    # Group to SKU x batch lines so each batch reports its own expiry/qty.
    lines = _group_cl_rows(rows)
    buckets = partition_by_expiry(lines, near_days=expiring_within_days)

    expired = buckets["expired"]
    expiring_soon = buckets["near_expiry"]
    safe = buckets["safe"]

    # FEFO pick suggestion: dated batches with on-hand stock, earliest first.
    fefo = fefo_sort(
        [
            line
            for line in lines
            if line.get("expiry_date") and line.get("on_hand", 0) > 0
        ]
    )

    # Backward-compatible shape (expired / expiring_soon / safe / summary) plus
    # the new fefo_pick + near_expiry_days fields.
    return {
        "expired": expired,
        "expiring_soon": expiring_soon,
        "safe": safe[:20],
        "fefo_pick": fefo,
        "near_expiry_days": expiring_within_days,
        "summary": {
            "expired_count": len(expired),
            "expiring_soon_count": len(expiring_soon),
            "safe_count": len(safe),
            "undated_count": len(buckets["undated"]),
        },
    }


# ============================================================================
# 4. POWER-WISE LENS STOCK GRID
# ============================================================================


@router.get("/lenses/power-grid")
async def get_lens_power_grid(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """
    Get SPH x CYL matrix for optical lenses.
    Each cell shows available count.
    GET /inventory/lenses/power-grid
    """
    db = _get_db()
    if db is None:
        return {
            "sph_range": power_grid.sph_range(),
            "cyl_range": power_grid.cyl_range(),
            "grid": {},
            "total_units": 0,
        }

    try:
        # Lens category codes across the schema (short + full enums).
        lens_cats = [
            "LS",
            "OPTICAL_LENS",
            "OPTICAL_LENSES",
            "RX_LENSES",
            "LENS",
            "LENSES",
            "EYEGLASS_LENS",
            "SPECTACLE_LENS",
            "SPECTACLE_LENSES",
        ]
        lenses = list(
            db.get_collection("products").find(
                {"category": {"$in": lens_cats}},
                {"_id": 0, "product_id": 1, "sph": 1, "cyl": 1, "brand": 1, "model": 1},
            )
        )
        pids = [p.get("product_id") for p in lenses if p.get("product_id")]
        on_hand = _on_hand_by_product(db, pids, store_id)
        result = power_grid.build_lens_grid(lenses, on_hand)
        result["lens_skus"] = len(lenses)
        return result

    except Exception as e:
        logger.error(f"get_lens_power_grid error: {e}")
        raise HTTPException(status_code=500, detail="Error fetching lens grid")


@router.get("/contact-lenses/power-grid")
async def get_cl_power_grid(
    store_id: Optional[str] = Query(None),
    near_expiry_days: int = Query(90, ge=1, le=365),
    current_user: dict = Depends(get_current_user),
):
    """Contact-lens availability matrix: power (rows) x base-curve (cols).

    Counts on-hand units from the serialized `stock` collection and flags cells
    that hold near-expiry stock (within near_expiry_days).
    GET /inventory/contact-lenses/power-grid
    """
    db = _get_db()
    if db is None:
        return {"power_range": [], "curve_range": [], "grid": {}, "total_units": 0}

    try:
        cls = list(
            db.get_collection("products").find(
                {"category": {"$in": CL_CATEGORY_CODES}},
                {
                    "_id": 0,
                    "product_id": 1,
                    "cl_power": 1,
                    "base_curve": 1,
                    "brand": 1,
                    "cl_series": 1,
                },
            )
        )
        pids = [p.get("product_id") for p in cls if p.get("product_id")]
        on_hand = _on_hand_by_product(db, pids, store_id)

        # Near-expiry flag: any on-hand unit for the product expiring within the
        # window. Fail-soft.
        near: Dict[str, bool] = {}
        if pids:
            avail = ["AVAILABLE", "available", "IN_STOCK", "in_stock"]
            match: dict = {
                "product_id": {"$in": pids},
                "expiry_date": {"$exists": True},
            }
            if store_id:
                match["store_id"] = store_id
            try:
                for row in db.get_collection("stock_units").find(
                    match, {"_id": 0, "product_id": 1, "expiry_date": 1, "status": 1}
                ):
                    st = row.get("status")
                    if st is not None and st not in avail:
                        continue
                    days = compute_days_until_expiry(row.get("expiry_date"))
                    if days is not None and days <= near_expiry_days:
                        near[row.get("product_id")] = True
            except Exception:
                pass

        result = power_grid.build_cl_grid(cls, on_hand, near)
        result["cl_skus"] = len(cls)
        result["near_expiry_days"] = near_expiry_days
        return result

    except Exception as e:
        logger.error(f"get_cl_power_grid error: {e}")
        raise HTTPException(status_code=500, detail="Error fetching CL grid")


# ============================================================================
# 5. SELL-THROUGH % BY BRAND GROUP
# ============================================================================


@router.get("/sell-through-analysis")
async def get_sell_through_analysis(
    days: int = Query(30, ge=1, le=365),
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """
    Get sell-through rate per brand.
    Sell-through = units sold / units stocked * 100
    GET /inventory/sell-through-analysis?days=30
    """
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection error")

    try:
        orders_coll = db.get_collection("orders")
        stock_coll = db.get_collection("stock_units")
        products_coll = db.get_collection("products")

        cutoff_date = datetime.utcnow() - timedelta(days=days)

        # Store-scope: the endpoint accepted ?store_id but ignored it and
        # aggregated across ALL stores. Resolve the caller's scope (None = all
        # stores for HQ roles; the caller's OWN store for store-level roles) and
        # apply it to both the sales and the stock side.
        active_store = resolve_store_scope(store_id, current_user)
        _order_q = {
            "created_at": {"$gte": cutoff_date},
            "status": {"$in": _SOLD_STATUSES},
        }
        if active_store:
            _order_q["store_id"] = active_store

        # PERF: this endpoint used to run products.find_one PER ORDER ITEM and
        # PER PHYSICAL STOCK UNIT (thousands of round-trips on a live store).
        # Same batched shape as /brand-insights below: one orders pass + one
        # stock pass collect quantities per product_id, then ONE products
        # query maps product_id -> brand and the totals fold to brand level.
        # The old code resolved each id with find_one({"_id": pid}); _id is
        # unique, so a single {"_id": {"$in": [...]}} fetch resolves exactly
        # the same docs (a pid with no products doc stays skipped, as before).

        # Pass 1: units sold per product from completed orders
        sold_by_pid: Dict[str, Any] = {}
        orders = orders_coll.find(_order_q)
        for order in orders:
            for item in order.get("items", []):
                product_id = item.get("product_id")
                if product_id is None:
                    continue
                qty = item.get("quantity", 0)
                sold_by_pid[product_id] = sold_by_pid.get(product_id, 0) + qty

        # Pass 2: units on hand per product (same store-scope as sales above)
        stocked_by_pid: Dict[str, Any] = {}
        stocks = stock_coll.find({"store_id": active_store} if active_store else {})
        for stock in stocks:
            product_id = stock.get("product_id")
            if product_id is None:
                continue
            # One serialized stock row == one physical unit; a row with no
            # `quantity` field still represents one unit on hand.
            qty = stock.get("quantity", 1)
            stocked_by_pid[product_id] = stocked_by_pid.get(product_id, 0) + qty

        # ONE products lookup for every product seen on either side.
        all_pids = list({*sold_by_pid, *stocked_by_pid})
        brand_by_pid: Dict[str, Any] = {}
        if all_pids:
            for product in products_coll.find(
                {"_id": {"$in": all_pids}}, {"brand": 1}
            ):
                brand_by_pid[product["_id"]] = product.get("brand", "Unknown")

        # Fold product totals to brand level (unresolved pids skipped, exactly
        # as the per-item find_one behaved when it found no product).
        sales_by_brand = {}
        for pid, qty in sold_by_pid.items():
            if pid in brand_by_pid:
                brand = brand_by_pid[pid]
                sales_by_brand[brand] = sales_by_brand.get(brand, 0) + qty

        stock_by_brand = {}
        for pid, qty in stocked_by_pid.items():
            if pid in brand_by_pid:
                brand = brand_by_pid[pid]
                stock_by_brand[brand] = stock_by_brand.get(brand, 0) + qty

        # Calculate sell-through %
        brands = set(list(sales_by_brand.keys()) + list(stock_by_brand.keys()))
        results = []

        for brand in brands:
            units_sold = sales_by_brand.get(brand, 0)
            units_stocked = stock_by_brand.get(brand, 0)
            sell_through = (
                (units_sold / max(units_stocked, 1)) * 100 if units_stocked > 0 else 0
            )

            results.append(
                {
                    "brand": brand,
                    "units_sold": units_sold,
                    "units_stocked": units_stocked,
                    "sell_through_percent": round(sell_through, 2),
                }
            )

        return {
            "period_days": days,
            "brands": sorted(
                results, key=lambda x: x["sell_through_percent"], reverse=True
            ),
        }

    except Exception as e:
        logger.error(f"get_sell_through_analysis error: {e}")
        raise HTTPException(status_code=500, detail="Error calculating sell-through")


# ============================================================================
# 5b. BRAND-WISE INVENTORY INSIGHTS (Inventory > Insights > Brands)
# ============================================================================


@router.get("/brand-insights")
async def get_brand_insights(
    days: int = Query(30, ge=1, le=365),
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Brand-wise KPI rollup: on-hand units, stock value (offer-price basis,
    mrp fallback), units sold + revenue over the window, sell-through % and
    days of cover -- KPI math shared with collection_insights so the Brands
    and Collections insights tabs agree.

    GET /inventory/brand-insights?days=30

    Unlike /sell-through-analysis this does NO per-item product lookups:
    ONE projected products scan (brand + prices), ONE stock_units rollup
    (_on_hand_by_product: canonical ON_HAND/EXCLUDED status conventions) and
    ONE orders aggregation (qty/item_total fields as in
    collection_insights._movement_pipeline). Blank brands fold to "Unknown".
    """
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection error")

    # Resolve the caller's store reach BEFORE the catch-all try so a
    # legitimate 403 from validate_store_access is not swallowed into a 500.
    active_store = resolve_store_scope(store_id, current_user)

    try:
        from ..services import brand_insights as _bi

        # ONE projected spine scan: brand + unit pricing for every product.
        product_docs = list(
            db.get_collection("products").find(
                {},
                {"_id": 1, "product_id": 1, "brand": 1, "offer_price": 1, "mrp": 1},
            )
        )

        # On-hand rollup over every known pid (both id conventions), reusing
        # the canonical on-hand status allowlist/exclusions.
        pids: List[str] = []
        seen: set = set()
        for doc in product_docs:
            for key in (doc.get("product_id"), doc.get("_id")):
                if key is None:
                    continue
                pid = str(key)
                if pid and pid not in seen:
                    pids.append(pid)
                    seen.add(pid)
        on_hand = _on_hand_by_product(db, pids, active_store)

        # ONE pass over the window's sold orders: units + line revenue per
        # product. Same field conventions as the collections movement math
        # (qty | quantity | 1 for units; item_total for line revenue).
        cutoff_date = datetime.utcnow() - timedelta(days=days)
        order_match: Dict = {
            "created_at": {"$gte": cutoff_date},
            "status": {"$in": _SOLD_STATUSES},
        }
        if active_store:
            order_match["store_id"] = active_store
        qty_expr = {"$ifNull": ["$items.qty", {"$ifNull": ["$items.quantity", 1]}]}
        sales_by_pid: Dict[str, Dict] = {}
        try:
            sales_rows = db.get_collection("orders").aggregate(
                [
                    {"$match": order_match},
                    {"$unwind": "$items"},
                    {
                        "$group": {
                            "_id": "$items.product_id",
                            "units": {"$sum": qty_expr},
                            "revenue": {"$sum": {"$ifNull": ["$items.item_total", 0]}},
                        }
                    },
                ]
            )
            for row in sales_rows:
                if not isinstance(row, dict):
                    continue
                pid = row.get("_id")
                units = row.get("units")
                # Defend against the mock aggregate stub (echoes raw docs):
                # a real group row has a scalar _id and a numeric units sum.
                if not pid or isinstance(pid, dict) or not isinstance(units, (int, float)):
                    continue
                sales_by_pid[str(pid)] = {
                    "units": int(units or 0),
                    "revenue": float(row.get("revenue") or 0),
                }
        except Exception as agg_exc:  # noqa: BLE001 - fail-soft to zero sales
            logger.warning(f"brand-insights sales aggregation failed: {agg_exc}")

        rows = _bi.fold_brand_rows(product_docs, on_hand, sales_by_pid, days)
        return {"period_days": days, "store_id": active_store, "brands": rows}

    except Exception as e:
        logger.error(f"get_brand_insights error: {e}")
        raise HTTPException(status_code=500, detail="Error calculating brand insights")


# ============================================================================
# 6. STOCK DUMP ANALYSIS (OVERSTOCK)
# ============================================================================


@router.get("/overstock-analysis")
async def get_overstock_analysis(
    overstocking_threshold: float = Query(3.0, ge=1.0),
    days: int = Query(30, ge=1, le=365),
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """
    Flag overstocked items: current_stock > threshold * avg_monthly_sales
    GET /inventory/overstock-analysis?overstocking_threshold=3.0&days=30
    """
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection error")

    try:
        orders_coll = db.get_collection("orders")
        stock_coll = db.get_collection("stock_units")
        products_coll = db.get_collection("products")

        cutoff_date = datetime.utcnow() - timedelta(days=days)

        # Store-scope: honour the caller's reach (None = all stores for HQ roles;
        # own store for store-level) instead of ignoring ?store_id and reading
        # every store's sales + stock.
        active_store = resolve_store_scope(store_id, current_user)
        _order_q = {
            "created_at": {"$gte": cutoff_date},
            "status": {"$in": _SOLD_STATUSES},
        }
        if active_store:
            _order_q["store_id"] = active_store

        # Get sales volume by product
        sales_by_product = {}
        orders = orders_coll.find(_order_q)

        for order in orders:
            for item in order.get("items", []):
                product_id = item.get("product_id")
                qty = item.get("quantity", 0)
                sales_by_product[product_id] = sales_by_product.get(product_id, 0) + qty

        # Calculate average monthly sales
        months = max(days / 30, 1)
        avg_monthly_sales = {pid: qty / months for pid, qty in sales_by_product.items()}

        # Roll up on-hand per product. The serialized model stores ONE row per
        # physical unit, so we must aggregate (count) rows -- iterating raw rows
        # one-by-one compared a single unit against the threshold (which never
        # flags) and emitted a duplicate entry per unit. $ifNull counts legacy
        # rows that predate the `quantity` field as one unit each.
        stock_match = {"status": {"$in": ["AVAILABLE", "RESERVED"]}}
        if active_store:
            stock_match["store_id"] = active_store
        stock_rows = list(
            stock_coll.aggregate(
                [
                    {"$match": stock_match},
                    {
                        "$group": {
                            "_id": "$product_id",
                            "qty": {"$sum": {"$ifNull": ["$quantity", 1]}},
                        }
                    },
                ]
            )
        )

        # Identify overstock at the PRODUCT level.
        overstocked = []
        for row in stock_rows:
            product_id = str(row.get("_id"))
            current_qty = int(row.get("qty", 0) or 0)
            avg_monthly = avg_monthly_sales.get(product_id, 0)

            # Flag if current > threshold * average
            if current_qty > (overstocking_threshold * avg_monthly):
                product = products_coll.find_one({"_id": product_id})
                months_of_stock = current_qty / max(avg_monthly, 1)

                if product:
                    brand = product.get("brand", "")
                    model = product.get("model", "")
                    product_name = (
                        product.get("name")
                        or f"{brand} {model}".strip()
                        or product.get("sku", "")
                        or "Unknown"
                    )
                    sku = product.get("sku", "")
                else:
                    product_name = "Unknown"
                    sku = ""

                overstocked.append(
                    {
                        "product_id": product_id,
                        "product_name": product_name,
                        "sku": sku,
                        "current_stock": current_qty,
                        "avg_monthly_sales": round(avg_monthly, 2),
                        "months_of_stock": round(months_of_stock, 1),
                        "overstock_multiple": round(
                            current_qty / max(avg_monthly, 1), 2
                        ),
                    }
                )

        return {
            "threshold_multiple": overstocking_threshold,
            "analysis_period_days": days,
            "total_overstocked": len(overstocked),
            "items": sorted(
                overstocked, key=lambda x: x["months_of_stock"], reverse=True
            )[:50],
        }

    except Exception as e:
        logger.error(f"get_overstock_analysis error: {e}")
        raise HTTPException(status_code=500, detail="Error analyzing overstock")


# ============================================================================
# 7. UNIFIED STOCK ALERTS  (feeds StockAlertsOverview.tsx)
# ============================================================================
#
# Replaces the old hardcoded mock list (Vogue Cat Eye / Prada Baroque / etc.)
# the component used to render. Computes real, actionable alerts from the
# `products` collection (where TechCherry-imported stock-on-hand lives as
# `stock_quantity`) joined to `orders.items` by barcode for sales velocity.
#
# Each product yields AT MOST ONE alert, chosen by priority:
#   REORDER_ALERT > LOW_STOCK > DEAD_STOCK > OVERSTOCK > FAST_MOVING
# so a fast seller about to run out is a REORDER, not also a FAST_MOVING.
#
# NOTE on order status: TechCherry historic orders are stamped status
# "DELIVERED" (uppercase); live IMS orders use mixed case. We match a broad
# set of "sold" statuses so imported sales actually count. The same
# _SOLD_STATUSES set is now also used by /non-moving, /overstock-analysis
# and /sell-through-analysis (previously lowercase-only, so they silently
# missed every imported sale).
#
# _SOLD_STATUSES is defined near the top of the ADVANCED INVENTORY FEATURES
# section (before its first use in get_non_moving_stock) so it is always
# initialised before any caller runs.


def _empty_alert_stats() -> dict:
    return {
        "totalAlerts": 0,
        "critical": 0,
        "high": 0,
        "medium": 0,
        "low": 0,
        "deadStockValue": 0,
        "recommendedRestockValue": 0,
    }


def _summarise_alert_stats(alerts: List[dict]) -> dict:
    """Roll up an alert list into the AlertStats shape the frontend expects."""
    stats = _empty_alert_stats()
    stats["totalAlerts"] = len(alerts)
    for a in alerts:
        sev = str(a.get("severity", "LOW")).lower()
        if sev in ("critical", "high", "medium", "low"):
            stats[sev] += 1
        impact = a.get("costImpact", 0) or 0
        if a.get("alertType") == "DEAD_STOCK":
            stats["deadStockValue"] += impact
        elif a.get("alertType") in ("REORDER_ALERT", "LOW_STOCK"):
            stats["recommendedRestockValue"] += impact
    stats["deadStockValue"] = round(stats["deadStockValue"], 2)
    stats["recommendedRestockValue"] = round(stats["recommendedRestockValue"], 2)
    return stats


def _build_stock_alert(
    product: dict,
    sold_30: float,
    last_sale: Optional[datetime],
    now: datetime,
    dead_days: int,
    lead_time_days: int,
) -> Optional[dict]:
    """Pure classifier — given a product doc plus its sales signals, return a
    single frontend-shaped (camelCase) StockAlert dict, or None if the product
    warrants no alert. No DB access, so it is fully unit-testable.
    """
    stock = int(product.get("stock_quantity", 0) or 0)
    cost = float(product.get("cost_price", 0) or 0)
    reorder_point = int(product.get("reorder_point", 0) or 0)
    # Owner decision (2026-07-04): reorder_quantity <= 0 (the new -1 default)
    # means auto-reorder is DISABLED for this product -- never emit a
    # REORDER_ALERT / restock suggestion for it. Informational alerts
    # (LOW_STOCK without a suggested qty, DEAD_STOCK, OVERSTOCK, FAST_MOVING)
    # still apply. See api/services/reorder_policy.py.
    reorder_suggestions_off = _reorder_disabled(product)

    velocity = (sold_30 or 0) / 30.0  # units/day from the last 30 days
    days_without_movement = (now - last_sale).days if last_sale else None
    projected = (stock / velocity) if velocity > 0 else None

    sku = product.get("sku") or product.get("barcode") or ""
    base = {
        "id": product.get("barcode") or sku or product.get("name", ""),
        "sku": sku,
        "productName": product.get("name", ""),
        "brand": product.get("brand", ""),
        "category": product.get("category", ""),
        "currentStock": stock,
        "reorderPoint": reorder_point,
        "safetyStock": 0,
        "projectedDaysToStockout": round(projected, 1) if projected is not None else 0,
        "lastMovementDate": (
            last_sale.isoformat() if isinstance(last_sale, datetime) else None
        ),
        "daysWithoutMovement": days_without_movement,
        "salesVelocity": round(velocity, 3),
        "recommendedOrder": 0,
        "costImpact": 0,
    }

    # 1. REORDER_ALERT — sells AND will run out within the reorder lead time
    #    (or is already at/below an explicit reorder point, or out of stock
    #     while still selling).
    out_of_stock_but_selling = stock <= 0 and velocity > 0
    below_reorder_point = reorder_point > 0 and stock <= reorder_point and velocity > 0
    runs_out_soon = projected is not None and projected <= lead_time_days
    if not reorder_suggestions_off and (
        out_of_stock_but_selling or below_reorder_point or runs_out_soon
    ):
        target = velocity * lead_time_days * 2  # cover 2x lead time
        recommended = max(int(round(target - stock)), 1)
        if stock <= 0 or (projected is not None and projected <= lead_time_days / 2):
            severity = "CRITICAL"
        else:
            severity = "HIGH"
        base.update(
            {
                "alertType": "REORDER_ALERT",
                "severity": severity,
                "recommendedOrder": recommended,
                "costImpact": round(recommended * cost, 2),
                "actionRequired": (
                    f"Out of stock - reorder {recommended} units now"
                    if stock <= 0
                    else f"~{int(projected)} days of stock left - reorder {recommended} units"
                ),
            }
        )
        return base

    # 2. LOW_STOCK — sells, getting low, but not yet reorder-critical.
    # When auto-reorder is disabled the alert stays (it is informational)
    # but with NO suggested restock qty (recommendedOrder 0, costImpact 0).
    if velocity > 0 and projected is not None and projected <= lead_time_days * 2:
        recommended = (
            0
            if reorder_suggestions_off
            else max(int(round(velocity * lead_time_days * 2 - stock)), 1)
        )
        base.update(
            {
                "alertType": "LOW_STOCK",
                "severity": "MEDIUM",
                "recommendedOrder": recommended,
                "costImpact": round(recommended * cost, 2),
                "actionRequired": f"Stock running low (~{int(projected)} days left)",
            }
        )
        return base

    # 3. DEAD_STOCK — has stock but no movement in the dead-stock window
    is_dead = stock > 0 and (
        last_sale is None
        or (days_without_movement is not None and days_without_movement >= dead_days)
    )
    if is_dead:
        impact = round(stock * cost, 2)
        if impact >= 50000:
            severity = "CRITICAL"
        elif impact >= 20000:
            severity = "HIGH"
        elif impact >= 5000:
            severity = "MEDIUM"
        else:
            severity = "LOW"
        base.update(
            {
                "alertType": "DEAD_STOCK",
                "severity": severity,
                "costImpact": impact,
                "actionRequired": (
                    f"No recorded sales - {stock} units of capital tied up"
                    if last_sale is None
                    else f"No sales in {days_without_movement} days - consider clearance"
                ),
            }
        )
        return base

    # 4/5. OVERSTOCK vs FAST_MOVING (both require active selling)
    if stock > 0 and velocity > 0:
        months_of_stock = stock / (velocity * 30.0)
        if months_of_stock >= 6:
            excess = max(int(round(stock - velocity * 30 * 3)), 0)  # beyond 3mo cover
            base.update(
                {
                    "alertType": "OVERSTOCK",
                    "severity": "MEDIUM" if months_of_stock >= 12 else "LOW",
                    "costImpact": round(excess * cost, 2),
                    "actionRequired": (
                        f"~{months_of_stock:.0f} months of stock on hand "
                        f"- {excess} units excess"
                    ),
                }
            )
            return base
        if velocity >= 0.5:  # ~15+ units/month and healthy cover = strong seller
            base.update(
                {
                    "alertType": "FAST_MOVING",
                    "severity": "LOW",
                    "actionRequired": (
                        f"Strong seller (~{velocity * 30:.0f} units/month) "
                        f"- keep well stocked"
                    ),
                }
            )
            return base

    return None


def _aggregate_sales_by_barcode(orders_coll, active_store, thirty_cutoff):
    """Return (sales_30, last_sales) dicts keyed by item barcode.
    sales_30: units sold in the last 30 days. last_sales: all-time last sale
    datetime per barcode. Order items link to products by barcode."""
    match: Dict = {"status": {"$in": _SOLD_STATUSES}}
    if active_store:
        match["store_id"] = active_store

    thirty_pipeline = [
        {"$match": {**match, "created_at": {"$gte": thirty_cutoff}}},
        {"$unwind": "$items"},
        {
            "$group": {
                "_id": "$items.barcode",
                "qty": {"$sum": {"$ifNull": ["$items.quantity", 0]}},
            }
        },
    ]
    last_sale_pipeline = [
        {"$match": match},
        {"$unwind": "$items"},
        {"$group": {"_id": "$items.barcode", "last": {"$max": "$created_at"}}},
    ]

    sales_30 = {
        r["_id"]: r["qty"]
        for r in orders_coll.aggregate(thirty_pipeline)
        if r.get("_id")
    }
    last_sales = {
        r["_id"]: r["last"]
        for r in orders_coll.aggregate(last_sale_pipeline)
        if r.get("_id")
    }
    return sales_30, last_sales


@router.get("/alerts")
async def get_stock_alerts(
    store_id: Optional[str] = Query(None),
    dead_days: int = Query(90, ge=7, le=365),
    lead_time_days: int = Query(14, ge=1, le=90),
    limit: int = Query(200, ge=1, le=1000),
    current_user: dict = Depends(get_current_user),
):
    """
    Unified, actionable stock alerts for the Inventory > Alerts tab.
    GET /inventory/alerts?dead_days=90&lead_time_days=14

    Returns { alerts: StockAlert[], stats: AlertStats } shaped exactly for
    StockAlertsOverview.tsx. Fail-soft: any DB issue returns an empty
    envelope so the UI shows its clean "No Alerts" state rather than 500ing.
    """
    db = _get_db()
    if db is None:
        return {"alerts": [], "stats": _empty_alert_stats()}

    active_store = validate_store_access(store_id, current_user)

    try:
        products_coll = db.get_collection("products")
        orders_coll = db.get_collection("orders")

        now = datetime.utcnow()
        thirty_cutoff = now - timedelta(days=30)

        prod_filter: Dict = {"is_active": {"$ne": False}}
        if active_store:
            prod_filter["store_id"] = active_store

        products = list(
            products_coll.find(
                prod_filter,
                {
                    "_id": 0,
                    "name": 1,
                    "brand": 1,
                    "category": 1,
                    "barcode": 1,
                    "sku": 1,
                    "mrp": 1,
                    "offer_price": 1,
                    "cost_price": 1,
                    "stock_quantity": 1,
                    "reorder_point": 1,
                    "reorder_quantity": 1,
                },
            )
        )

        sales_30, last_sales = _aggregate_sales_by_barcode(
            orders_coll, active_store, thirty_cutoff
        )

        alerts: List[dict] = []
        for p in products:
            barcode = p.get("barcode") or p.get("sku") or ""
            alert = _build_stock_alert(
                p,
                sold_30=sales_30.get(barcode, 0),
                last_sale=last_sales.get(barcode),
                now=now,
                dead_days=dead_days,
                lead_time_days=lead_time_days,
            )
            if alert:
                alerts.append(alert)

        sev_rank = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
        alerts.sort(
            key=lambda a: (
                sev_rank.get(a.get("severity", "LOW"), 4),
                -(a.get("costImpact", 0) or 0),
            )
        )
        alerts = alerts[:limit]

        return {"alerts": alerts, "stats": _summarise_alert_stats(alerts)}

    except Exception as e:
        logger.error(f"get_stock_alerts error: {e}")
        return {"alerts": [], "stats": _empty_alert_stats()}


# ============================================================================
# 8. SERIALIZED INVENTORY  (feeds SerialNumberTracker.tsx)
# ============================================================================
#
# Tracks individual high-value units (hearing aids, smart watches, premium
# frames) by serial number. Replaces the hardcoded mock list the component
# used to render (Phonak Audeo P90-R "sold to Mr. Rajesh Kumar", Apple Watch
# Series 9, etc.). Data lives in the `serial_numbers` collection; the GET
# enriches each row with product details and a computed warranty status.


class SerialCreate(BaseModel):
    product_id: str
    serial_number: str = Field(..., min_length=1)
    status: str = "IN_STOCK"
    location_code: Optional[str] = None
    purchase_date: Optional[str] = None
    warranty_months: Optional[int] = 12
    warranty_expiry_date: Optional[str] = None
    supplier_batch: Optional[str] = None
    notes: Optional[str] = None
    sold_to: Optional[str] = None
    sold_date: Optional[str] = None
    store_id: Optional[str] = None


class SerialUpdate(BaseModel):
    status: Optional[str] = None
    location_code: Optional[str] = None
    purchase_date: Optional[str] = None
    warranty_months: Optional[int] = None
    warranty_expiry_date: Optional[str] = None
    supplier_batch: Optional[str] = None
    notes: Optional[str] = None
    sold_to: Optional[str] = None
    sold_date: Optional[str] = None


_SERIAL_STATUSES = {"IN_STOCK", "SOLD", "WARRANTY_CLAIM", "DAMAGED", "LOST_STOLEN"}


def _compute_warranty_status(expiry: Optional[str], now: datetime) -> str:
    """ACTIVE if a future warranty-expiry date exists, EXPIRED if past,
    NONE if there is no expiry. Mirrors the frontend's own derivation so the
    server is the single source of truth. Pure → unit-testable."""
    if not expiry:
        return "NONE"
    try:
        exp = datetime.fromisoformat(str(expiry).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return "NONE"
    if exp.tzinfo is not None:
        exp = exp.replace(tzinfo=None)
    return "ACTIVE" if exp > now else "EXPIRED"


def _serial_to_frontend(doc: dict, product: Optional[dict], now: datetime) -> dict:
    """Map a serial_numbers doc (+ optional product) to the camelCase
    SerializedItem shape SerialNumberTracker.tsx expects. Pure → testable."""
    product = product or {}
    expiry = doc.get("warranty_expiry_date")
    return {
        "id": doc.get("serial_id", ""),
        "productId": doc.get("product_id", ""),
        "serialNumber": doc.get("serial_number", ""),
        "status": doc.get("status", "IN_STOCK"),
        "locationCode": doc.get("location_code"),
        "purchaseDate": doc.get("purchase_date"),
        "warrantyMonths": doc.get("warranty_months"),
        "warrantyExpiryDate": expiry,
        "supplierBatch": doc.get("supplier_batch"),
        "notes": doc.get("notes"),
        "soldTo": doc.get("sold_to"),
        "soldDate": doc.get("sold_date"),
        "productName": product.get("name", doc.get("product_name", "")),
        "productSku": product.get("sku", product.get("barcode", "")),
        "productBrand": product.get("brand", ""),
        "productCategory": product.get("category", ""),
        "soldToCustomer": doc.get("sold_to"),
        "warrantyStatus": _compute_warranty_status(expiry, now),
    }


def _lookup_product(products_coll, product_id: str) -> Optional[dict]:
    """Resolve a product by its natural keys (sku / barcode / product_id),
    falling back to MongoDB ObjectId. product_id semantics vary by caller so
    we try the cheap string matches first. Defensive — never raises."""
    if not product_id:
        return None
    projection = {
        "_id": 0,
        "name": 1,
        "sku": 1,
        "barcode": 1,
        "brand": 1,
        "category": 1,
    }
    try:
        p = products_coll.find_one(
            {
                "$or": [
                    {"sku": product_id},
                    {"barcode": product_id},
                    {"product_id": product_id},
                ]
            },
            projection,
        )
        if p:
            return p
        try:
            from bson import ObjectId

            return products_coll.find_one({"_id": ObjectId(product_id)}, projection)
        except Exception:
            return None
    except Exception:
        return None


@router.get("/serials")
async def list_serials(
    store_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=1000),
    current_user: dict = Depends(get_current_user),
):
    """
    List serialized inventory units, enriched with product details and a
    computed warranty status. GET /inventory/serials?status=IN_STOCK
    Fail-soft: empty list on any DB issue so the tracker shows its empty state.
    """
    db = _get_db()
    if db is None:
        return {"items": []}

    active_store = validate_store_access(store_id, current_user)

    try:
        serials_coll = db.get_collection("serial_numbers")
        products_coll = db.get_collection("products")

        query: Dict = {}
        if active_store:
            query["store_id"] = active_store
        if status and status in _SERIAL_STATUSES:
            query["status"] = status

        docs = list(serials_coll.find(query).sort("created_at", -1).limit(limit))
        now = datetime.utcnow()
        prod_cache: Dict[str, dict] = {}
        items: List[dict] = []

        for d in docs:
            d.pop("_id", None)
            pid = d.get("product_id", "")
            if pid not in prod_cache:
                prod_cache[pid] = _lookup_product(products_coll, pid) or {}
            item = _serial_to_frontend(d, prod_cache[pid], now)
            if search:
                needle = search.lower()
                hay = " ".join(
                    [
                        item["serialNumber"],
                        item["productName"],
                        item["productSku"],
                        item.get("soldToCustomer") or "",
                    ]
                ).lower()
                if needle not in hay:
                    continue
            items.append(item)

        return {"items": items}

    except Exception as e:
        logger.error(f"list_serials error: {e}")
        return {"items": []}


@router.post("/serials")
async def create_serial(
    req: SerialCreate,
    current_user: dict = Depends(require_roles(*_INVENTORY_ROLES)),
):
    """Register a new serialized unit. Serial numbers are unique within a store."""
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    active_store = validate_store_access(req.store_id, current_user)
    if req.status not in _SERIAL_STATUSES:
        raise HTTPException(status_code=400, detail="Invalid status")

    try:
        serials_coll = db.get_collection("serial_numbers")
        products_coll = db.get_collection("products")

        sn = req.serial_number.strip().upper()
        dup_query: Dict = {"serial_number": sn}
        if active_store:
            dup_query["store_id"] = active_store
        if serials_coll.find_one(dup_query, {"_id": 1}):
            raise HTTPException(status_code=400, detail="Serial number already exists")

        now = datetime.utcnow()
        doc = {
            "serial_id": str(uuid.uuid4()),
            "serial_number": sn,
            "product_id": req.product_id,
            "store_id": active_store,
            "status": req.status,
            "location_code": req.location_code,
            "purchase_date": req.purchase_date,
            "warranty_months": req.warranty_months,
            "warranty_expiry_date": req.warranty_expiry_date,
            "supplier_batch": req.supplier_batch,
            "notes": req.notes,
            "sold_to": req.sold_to,
            "sold_date": req.sold_date,
            "created_at": now.isoformat(),
            "created_by": current_user.get("user_id", ""),
            "updated_at": now.isoformat(),
        }
        serials_coll.insert_one(doc)
        doc.pop("_id", None)
        product = _lookup_product(products_coll, req.product_id) or {}
        return _serial_to_frontend(doc, product, now)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"create_serial error: {e}")
        raise HTTPException(status_code=500, detail="Error creating serial")


@router.patch("/serials/{serial_id}")
async def update_serial(
    serial_id: str,
    req: SerialUpdate,
    current_user: dict = Depends(require_roles(*_INVENTORY_ROLES)),
):
    """Update a serialized unit (status / location / warranty / sold-to).
    The serial number itself is immutable once created."""
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    try:
        serials_coll = db.get_collection("serial_numbers")
        products_coll = db.get_collection("products")

        existing = serials_coll.find_one({"serial_id": serial_id})
        if not existing:
            raise HTTPException(status_code=404, detail="Serial not found")

        updates = req.model_dump(exclude_unset=True, exclude_none=True)
        if "status" in updates and updates["status"] not in _SERIAL_STATUSES:
            raise HTTPException(status_code=400, detail="Invalid status")

        now = datetime.utcnow()
        updates["updated_at"] = now.isoformat()
        serials_coll.update_one({"serial_id": serial_id}, {"$set": updates})

        merged = {**existing, **updates}
        merged.pop("_id", None)
        product = _lookup_product(products_coll, merged.get("product_id", "")) or {}
        return _serial_to_frontend(merged, product, now)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"update_serial error: {e}")
        raise HTTPException(status_code=500, detail="Error updating serial")


# ============================================================================
# DEFECTIVE QUARANTINE  (F21 -- the E3-shim)
# ============================================================================
# A store manager pulls a physically defective / damaged unit off the sellable
# floor by flipping its free-string status to QUARANTINED. Because every on-hand
# / sellable rollup in this module (and product_repository.find_available, and
# transfers' ship-move) uses an explicit AVAILABLE/RESERVED allowlist, a
# QUARANTINED unit is excluded from POS sale, transfers and blind-count purely
# by not being in any allowlist -- no rollup edit is needed. Each status
# transition writes ONE hash-chained audit row via AuditRepository.create (never
# append_audit_entry directly) and dispatches a fail-soft stock.quarantined
# event. Standalone Mongo: every write is a single-document op (no transactions).


def _now_ist():
    """IST-stamped datetime for quarantine records (India-time forensic trail).
    Falls back to naive local only if the IST helper is unavailable."""
    try:
        from api.utils.ist import now_ist

        return now_ist()
    except Exception:  # noqa: BLE001
        return datetime.now()


def _quarantine_audit(action: str, stock_id: str, store_id, user_id,
                      before_state: Dict, after_state: Dict, detail: Dict) -> None:
    """Write one hash-chained STOCK_UNIT audit row. Fail-soft: an audit hiccup
    never undoes the business write that triggered it."""
    try:
        audit = get_audit_repository()
        if audit is not None:
            audit.create(
                {
                    "action": action,
                    "entity_type": "STOCK_UNIT",
                    "entity_id": stock_id,
                    "store_id": store_id,
                    "user_id": user_id,
                    "before_state": before_state,
                    "after_state": after_state,
                    "detail": detail,
                }
            )
    except Exception as e:  # noqa: BLE001
        logger.warning("[INVENTORY] quarantine audit failed: %s", e)


@router.patch("/stock/{stock_id}/quarantine")
async def quarantine_stock_unit(
    stock_id: str,
    req: QuarantineRequest,
    current_user: dict = Depends(require_roles(*_STOCK_MANAGER_ROLES)),
):
    """Mark a physical stock unit QUARANTINED (defective -- pull off the floor).

    Guards: the unit must exist, be store-accessible to the caller, and be in an
    eligible status (AVAILABLE or DAMAGED -- NOT SOLD/TRANSFERRED/QUARANTINED).
    Writes the free-string QUARANTINED status + quarantine metadata, audits the
    transition, and dispatches a fail-soft stock.quarantined event so TASKMASTER
    can chase an RTV later. No accounting entry (per F21 owner decision).
    """
    stock_repo = get_stock_repository()
    if stock_repo is None:
        raise HTTPException(status_code=503, detail="Stock repository unavailable")

    unit = stock_repo.find_by_id(stock_id)
    if unit is None:
        raise HTTPException(status_code=404, detail="Stock unit not found")

    store_id = unit.get("store_id")
    # Existence-hide a cross-store unit (same 404 contract as other IDOR guards).
    if not can_access_store_scoped(store_id, current_user):
        raise HTTPException(status_code=404, detail="Stock unit not found")

    reason = (req.reason or "").strip().upper()
    if reason not in _QUARANTINE_REASONS:
        raise HTTPException(
            status_code=422,
            detail=f"reason must be one of {sorted(_QUARANTINE_REASONS)}",
        )

    current_status = (unit.get("status") or "AVAILABLE").strip().upper()
    if current_status == STOCK_STATUS_QUARANTINED:
        raise HTTPException(
            status_code=409,
            detail={"code": "already_quarantined", "message": "Unit is already quarantined."},
        )
    if current_status not in ("AVAILABLE", "DAMAGED"):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "not_eligible",
                "message": f"A unit in status {current_status} cannot be quarantined.",
            },
        )

    # Period-lock check (audit completeness; fail-soft -- only raises 423 for an
    # explicitly locked accounting month). Quarantine is a physical control, not
    # a financial write, so current-period operations are never gated.
    try:
        db = _get_db()
        if db is not None:
            from .finance import check_period_locked
            from api.utils.ist import ist_today

            # IST business day, not the UTC box date (00:00-05:30 IST is still
            # "yesterday" in UTC -- the wrong accounting month on the 1st).
            check_period_locked(db, ist_today())
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        logger.warning("[INVENTORY] quarantine period-lock check skipped: %s", e)

    now = _now_ist()
    actor = current_user.get("user_id")
    actor_name = current_user.get("name") or current_user.get("username") or ""

    update = {
        "status": STOCK_STATUS_QUARANTINED,
        "quarantine_reason": reason,
        "quarantine_at": now,
        "quarantine_by": actor,
        "quarantine_by_name": actor_name,
        "quarantine_notes": (req.notes or "")[:200],
        "quarantine_label_printed": False,
    }
    if req.rtv_vendor_id:
        update["rtv_vendor_id"] = req.rtv_vendor_id

    if not stock_repo.update(stock_id, update):
        raise HTTPException(status_code=500, detail="Failed to quarantine stock unit")

    _quarantine_audit(
        "STOCK_QUARANTINED",
        stock_id,
        store_id,
        actor,
        {"status": current_status},
        {"status": STOCK_STATUS_QUARANTINED, "quarantine_reason": reason},
        {"notes": update["quarantine_notes"], "rtv_vendor_id": req.rtv_vendor_id},
    )

    # E3w: converge this legacy F21 write-path onto the item-event ledger. The
    # status write + audit above already succeeded; this is a PURELY ADDITIVE
    # ledger row (no CAS, no projection) recording the AVAILABLE/DAMAGED ->
    # QUARANTINED transition so /items/{id}/events sees it. Fail-soft: any error
    # is logged and swallowed -- it can never undo the quarantine just written.
    try:
        from ..services import item_events as ie

        db_le = _get_db()
        if db_le is not None:
            frm = ie.canonical_state(current_status) or current_status
            ie.record_post_write_event(
                db_le,
                event_type=ie.ItemEventType.QUARANTINE_IN,
                actor_id=actor,
                stock_id=stock_id,
                from_state=frm,
                to_state=ie.StockState.QUARANTINED,
                store_id=store_id,
                product_id=unit.get("product_id"),
                source_type="F21",
                payload={"quarantine_reason": reason,
                         "notes": update["quarantine_notes"],
                         "rtv_vendor_id": req.rtv_vendor_id},
            )
    except Exception as e:  # noqa: BLE001
        logger.warning("[INVENTORY] quarantine ledger emit failed: %s", e)

    # Event bus (fail-soft): lets TASKMASTER raise an RTV follow-up after 7 days.
    try:
        from agents.registry import dispatch_event

        await dispatch_event(
            "stock.quarantined",
            {
                "stock_id": stock_id,
                "store_id": store_id,
                "reason": reason,
                "actor_id": actor,
            },
            source="inventory_router",
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("[INVENTORY] stock.quarantined dispatch failed: %s", e)

    updated = stock_repo.find_by_id(stock_id) or {**unit, **update}
    return {"stock_unit": updated, "message": "Stock unit quarantined"}


@router.patch("/stock/{stock_id}/lift-quarantine")
async def lift_quarantine_stock_unit(
    stock_id: str,
    req: LiftQuarantineRequest,
    current_user: dict = Depends(require_roles(*_STOCK_MANAGER_ROLES)),
):
    """Lift a quarantine (mis-quarantine correction) -- restore to AVAILABLE.

    A mandatory lift_reason (>=5 chars) is recorded in the audit trail. The unit
    must currently be QUARANTINED (409 not_quarantined otherwise). No approval /
    PIN gate -- store-manager self-approval is sufficient (F21 owner decision).
    """
    stock_repo = get_stock_repository()
    if stock_repo is None:
        raise HTTPException(status_code=503, detail="Stock repository unavailable")

    unit = stock_repo.find_by_id(stock_id)
    if unit is None:
        raise HTTPException(status_code=404, detail="Stock unit not found")

    store_id = unit.get("store_id")
    if not can_access_store_scoped(store_id, current_user):
        raise HTTPException(status_code=404, detail="Stock unit not found")

    current_status = (unit.get("status") or "").strip().upper()
    if current_status != STOCK_STATUS_QUARANTINED:
        raise HTTPException(
            status_code=409,
            detail={"code": "not_quarantined", "message": "Unit is not quarantined."},
        )

    now = _now_ist()
    actor = current_user.get("user_id")

    update = {
        "status": "AVAILABLE",
        "quarantine_lifted_at": now,
        "quarantine_lifted_by": actor,
        "quarantine_lift_reason": req.lift_reason,
        "quarantine_label_printed": False,
    }
    if not stock_repo.update(stock_id, update):
        raise HTTPException(status_code=500, detail="Failed to lift quarantine")

    _quarantine_audit(
        "QUARANTINE_LIFTED",
        stock_id,
        store_id,
        actor,
        {"status": STOCK_STATUS_QUARANTINED},
        {"status": "AVAILABLE"},
        {"lift_reason": req.lift_reason},
    )

    # E3w: ledger the QUARANTINED -> AVAILABLE release (additive, no CAS) so the
    # two divergent QUARANTINED write-paths both land in item_events. Fail-soft.
    try:
        from ..services import item_events as ie

        db_le = _get_db()
        if db_le is not None:
            ie.record_post_write_event(
                db_le,
                event_type=ie.ItemEventType.QUARANTINE_OUT,
                actor_id=actor,
                stock_id=stock_id,
                from_state=ie.StockState.QUARANTINED,
                to_state=ie.StockState.AVAILABLE,
                store_id=store_id,
                product_id=unit.get("product_id"),
                source_type="F21",
                payload={"disposition": "RESTOCK",
                         "lift_reason": req.lift_reason},
            )
    except Exception as e:  # noqa: BLE001
        logger.warning("[INVENTORY] lift-quarantine ledger emit failed: %s", e)

    updated = stock_repo.find_by_id(stock_id) or {**unit, **update}
    return {"stock_unit": updated, "message": "Quarantine lifted"}


@router.get("/stock/quarantined")
async def list_quarantined_stock(
    store_id: Optional[str] = Query(None),
    rtv_vendor_id: Optional[str] = Query(None),
    label_printed: Optional[bool] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    current_user: dict = Depends(
        require_roles(*_STOCK_MANAGER_ROLES, "ACCOUNTANT")
    ),
):
    """The Quarantine Queue: all QUARANTINED units for the caller's store(s).

    Store-scoped: a store-level role only ever sees its OWN store (an explicit
    cross-store ?store_id is 403'd by resolve_store_scope); HQ roles may pass any
    store_id or see all. Each row carries product name/brand/category and the
    quarantine metadata; the summary reports the count of UNLABELED units (the
    ones that still need a red sticker before they can be cleared).
    """
    db = _get_db()
    if db is None:
        return {"items": [], "total": 0, "unlabeled_count": 0}

    # Authorise + resolve the store filter (store-roles pinned to their own).
    scoped_store = resolve_store_scope(store_id, current_user)

    match: Dict = {"status": STOCK_STATUS_QUARANTINED}
    if scoped_store:
        match["store_id"] = scoped_store
    if rtv_vendor_id:
        match["rtv_vendor_id"] = rtv_vendor_id
    if label_printed is not None:
        if label_printed:
            match["quarantine_label_printed"] = True
        else:
            match["quarantine_label_printed"] = {"$ne": True}
    if date_from or date_to:
        # quarantine_at is a tz-aware IST datetime; a raw STRING bound never matches
        # the BSON Date (type bracket) -> the filter was a silent no-op returning [].
        # Coerce to IST-aware datetimes; a date-only date_to covers the whole IST day.
        _IST = timezone(timedelta(hours=5, minutes=30))
        rng: Dict = {}
        try:
            if date_from:
                _f = datetime.fromisoformat(date_from)
                rng["$gte"] = _f.replace(tzinfo=_IST) if _f.tzinfo is None else _f
            if date_to:
                _t = datetime.fromisoformat(date_to)
                if _t.tzinfo is None:
                    if (_t.hour, _t.minute, _t.second) == (0, 0, 0):
                        _t = _t + timedelta(days=1) - timedelta(microseconds=1)
                    _t = _t.replace(tzinfo=_IST)
                rng["$lte"] = _t
        except ValueError:
            raise HTTPException(status_code=422, detail="date_from / date_to must be ISO format (YYYY-MM-DD)")
        match["quarantine_at"] = rng

    items: List[Dict] = []
    unlabeled = 0
    try:
        stock_coll = db.get_collection("stock_units")
        products_coll = db.get_collection("products")
        prod_cache: Dict[str, Dict] = {}
        for row in stock_coll.find(match):
            row.pop("_id", None)
            pid = row.get("product_id")
            prod = prod_cache.get(pid)
            if prod is None and pid:
                prod = products_coll.find_one({"product_id": pid}) or {}
                prod_cache[pid] = prod
            prod = prod or {}
            if not row.get("quarantine_label_printed"):
                unlabeled += 1
            items.append(
                {
                    **row,
                    "product_name": prod.get("name") or prod.get("product_name") or "",
                    "brand": prod.get("brand") or "",
                    "category": prod.get("category") or "",
                }
            )
    except Exception as e:  # noqa: BLE001
        logger.warning("[INVENTORY] quarantine queue read failed: %s", e)
        return {"items": [], "total": 0, "unlabeled_count": 0}

    # backlog #4: resolve the RTV vendor id -> vendor name for display.
    try:
        from ..services.name_resolver import vendor_name_map

        vmap = vendor_name_map(db, [it.get("rtv_vendor_id") for it in items])
        for it in items:
            vid = it.get("rtv_vendor_id")
            if vid and str(vid) in vmap:
                it["rtv_vendor_name"] = vmap[str(vid)]
    except Exception:  # noqa: BLE001
        pass

    return {"items": items, "total": len(items), "unlabeled_count": unlabeled}
