"""Power-wise lens and contact-lens stock grids."""

from ._shared import (
    Depends,
    Dict,
    HTTPException,
    Optional,
    Query,
    get_current_user,
    is_on_hand,
    logger,
    power_grid,
    router,
)
from .helpers import (
    CL_CATEGORY_CODES,
    _get_db,
    _on_hand_by_product,
    compute_days_until_expiry,
)

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
                    # The SAME sellable question as the grid's own on_hand
                    # column (_on_hand_by_product) -- one rule, one answer. A
                    # Title-case / padded legacy unit must never be counted as
                    # stock in the cell yet skipped by its expiry warning.
                    if not is_on_hand(row.get("status")):
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
