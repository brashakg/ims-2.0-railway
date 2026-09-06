"""Stock-count scanning interface."""

from ._shared import (
    BaseModel,
    Depends,
    Field,
    HTTPException,
    Optional,
    Query,
    _INVENTORY_ROLES,
    _on_hand_status_clause,
    logger,
    require_roles,
    router,
)
from .helpers import (
    _get_db,
)
from .stock_count import (
    _load_open_count,
    _upsert_count_item,
)

# ============================================================================
# 2. STOCK COUNT SCANNING INTERFACE
# ============================================================================


class BarcodeScanRequest(BaseModel):
    barcode: str
    physical_count: int = Field(..., ge=0)
    notes: Optional[str] = None
    # The count session this scan belongs to. Without it the scan resolved a
    # barcode, calculated a difference and THREW IT AWAY -- which is how every
    # count in the business completed with nothing recorded (audit S2). With
    # it, the scan is the count sheet: the counted quantity is written onto the
    # session and the variance is measured against that session's opening
    # snapshot, so what the counter sees is what completion will report.
    count_id: Optional[str] = None


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

        # Resolve the session FIRST when one was given, so a scan against a
        # closed or someone else's count is refused before anything is read.
        count_doc = (
            _load_open_count(db, request.count_id, current_user)
            if request.count_id
            else None
        )

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
            **_on_hand_status_clause(include_reserved=True),
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

        # Recording onto an OPEN session: the count is BLIND (owner ruling
        # 2026-08-25). The scan door must not echo what the books expect --
        # no system count, no variance, no "you matched" tell. The comparison
        # against the opening snapshot happens at /complete, after the
        # answers are in.
        if count_doc is not None:
            items_counted = _upsert_count_item(
                db,
                count_doc,
                product_id=product_id,
                product_name=product_name,
                sku=sku,
                counted_quantity=request.physical_count,
                notes=request.notes,
                user_id=current_user.get("user_id", ""),
            )
            return {
                "barcode": request.barcode,
                "product_id": product_id,
                "product_name": product_name,
                "sku": sku,
                "physical_count": request.physical_count,
                "notes": request.notes,
                "count_id": request.count_id,
                "recorded": True,
                "items_counted": items_counted,
            }

        # No session: a plain stock lookup against LIVE on-hand (nothing is
        # recorded), same information as the inventory dashboard.
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
            "count_id": request.count_id,
            "recorded": False,
            "items_counted": None,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"scan_barcode_for_count error: {e}")
        raise HTTPException(status_code=500, detail="Error processing barcode scan")
