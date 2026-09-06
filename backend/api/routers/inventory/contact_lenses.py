"""Contact-lens inventory with batch/expiry tracking."""

from ._shared import (
    Depends,
    Dict,
    List,
    Optional,
    Query,
    _on_hand_status_clause,
    datetime,
    get_current_user,
    logger,
    router,
    validate_store_access,
)
from .helpers import (
    CL_CATEGORY_CODES,
    _get_db,
    compute_days_until_expiry,
    fefo_sort,
    partition_by_expiry,
)

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

        # 2. Pull on-hand stock for those products (store-scoped). Physical
        # question -- a reserved lens box is still in the drawer and still
        # expiring -- so the shared clause with RESERVED on.
        stock_filter: Dict[str, object] = {
            "product_id": {"$in": cl_product_ids},
            **_on_hand_status_clause(include_reserved=True),
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
