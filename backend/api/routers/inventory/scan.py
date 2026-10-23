"""Stock-count scanning interface."""

from ...dependencies import in_reach_or_404

from ._shared import (
    BaseModel,
    Depends,
    Field,
    HTTPException,
    Optional,
    Query,
    _INVENTORY_ROLES,
    _on_hand_status_clause,
    barcode_svc,
    logger,
    require_roles,
    router,
)
from .helpers import (
    _get_db,
)
from .stock_count import (
    _load_open_count,
    _record_scan,
)

# ============================================================================
# 2. STOCK COUNT SCANNING INTERFACE
# ============================================================================


class BarcodeScanRequest(BaseModel):
    barcode: str
    # Only for a session-less stock lookup. Inside a count session a scan is
    # ONE piece (owner ruling S1) and a number here is refused -- a typed
    # quantity goes on the style's line (POST /stock-count/{id}/items).
    physical_count: Optional[int] = Field(None, ge=0)
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

        # A scan inside a session is ONE piece. A number sent with it used to
        # REPLACE the style's whole counted line (the "scan replaces the
        # quantity" bug); refuse it rather than silently ignore it.
        if request.count_id and request.physical_count is not None:
            raise HTTPException(
                status_code=400,
                detail=(
                    "A scan counts one piece. To enter a number for a style "
                    "with no label, type it on that style's line instead."
                ),
            )

        # Resolve the session FIRST when one was given, so a scan against a
        # closed or someone else's count is refused before anything is read.
        count_doc = (
            _load_open_count(db, request.count_id, current_user)
            if request.count_id
            else None
        )

        # Find stock by barcode. Another shop's unit is the same 404 as an
        # unknown code: its product and on-hand are that shop's (audit
        # 2026-10-18); admins reach every shop.
        stock = in_reach_or_404(
            barcode_svc.find_unit_by_code(stock_coll, request.barcode),
            current_user,
            "Barcode not found",
        )

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
        # no system count, no variance, no "you matched" tell. It answers
        # only "saved" and how many pieces this session has scanned so far.
        if count_doc is not None:
            added, fresh = _record_scan(
                db,
                count_doc,
                unit=stock,
                product_name=product_name,
                sku=sku,
                user_id=current_user.get("user_id", ""),
            )
            return {
                "barcode": stock.get("barcode") or request.barcode,
                "product_id": product_id,
                "product_name": product_name,
                "sku": sku,
                "count_id": request.count_id,
                "recorded": True,
                # False: this exact piece was already scanned -- counted once.
                "added": added,
                "pieces_scanned": len(fresh.get("scans") or []),
                "items_counted": fresh.get("items_counted"),
            }

        # No session: a plain stock lookup against LIVE on-hand (nothing is
        # recorded), same information as the inventory dashboard.
        physical = int(request.physical_count or 0)
        variance = physical - system_count

        # BLIND COUNT, server side (S1 review M5): this door is one request
        # away from a counter mid-count, so the on-hand figure and the
        # variance go to the admins only. Everyone else gets "found".
        from ._shared import is_write_off_admin

        if not is_write_off_admin(current_user):
            return {
                "barcode": request.barcode,
                "product_id": product_id,
                "product_name": product_name,
                "sku": sku,
                "notes": request.notes,
                "count_id": None,
                "recorded": False,
                "items_counted": None,
            }

        return {
            "barcode": request.barcode,
            "product_id": product_id,
            "product_name": product_name,
            "sku": sku,
            "system_count": system_count,
            "physical_count": physical,
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
