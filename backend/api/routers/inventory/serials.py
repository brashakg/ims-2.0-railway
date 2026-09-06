"""Serialized inventory (feeds SerialNumberTracker.tsx)."""

from ._shared import (
    BaseModel,
    Depends,
    Dict,
    Field,
    HTTPException,
    List,
    Optional,
    Query,
    _INVENTORY_ROLES,
    datetime,
    get_current_user,
    logger,
    require_roles,
    router,
    uuid,
    validate_store_access,
)
from .helpers import (
    _get_db,
)

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
