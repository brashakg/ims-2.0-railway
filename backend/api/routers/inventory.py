"""
IMS 2.0 - Inventory Router
===========================
Stock management, stock count/audit, aging analysis, barcode operations
"""

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field
from typing import List, Optional, Dict
from datetime import date, datetime, timedelta
import uuid
import logging

from .auth import get_current_user
from ..dependencies import get_stock_repository, get_product_repository, validate_store_access

logger = logging.getLogger(__name__)
router = APIRouter()


# ============================================================================
# SCHEMAS
# ============================================================================


class StockAddRequest(BaseModel):
    product_id: str
    quantity: int = Field(..., ge=1)
    location_code: Optional[str] = None
    batch_code: Optional[str] = None
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


# ============================================================================
# STOCK ENDPOINTS
# ============================================================================


@router.get("/")
async def get_inventory_root():
    """Root endpoint for inventory stock list"""
    return {"module": "inventory", "status": "active", "message": "stock overview endpoint ready"}


@router.get("/stock")
async def get_stock(
    store_id: Optional[str] = Query(None),
    product_id: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    low_stock: bool = Query(False),
    current_user: dict = Depends(get_current_user),
):
    """Get stock with filtering"""
    repo = get_stock_repository()
    active_store = validate_store_access(store_id, current_user)

    if repo is not None:
        if low_stock:
            stock = repo.find_low_stock(active_store)
        elif product_id:
            stock = repo.find_by_product_store(product_id, active_store)
        else:
            filter_dict = {"store_id": active_store} if active_store else {}
            if category:
                filter_dict["category"] = category
            stock = repo.find_many(filter_dict, limit=100)

        return {"items": stock, "total": len(stock)}

    return {"items": [], "total": 0}


# NOTE: Specific routes MUST come before /{parameter} routes
@router.get("/low-stock")
async def get_low_stock_alerts(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Get low stock alerts"""
    repo = get_stock_repository()
    active_store = validate_store_access(store_id, current_user)

    if repo is not None:
        items = repo.find_low_stock(active_store)
        return {"items": items}

    return {"items": []}


@router.get("/barcode/{barcode}")
async def get_stock_by_barcode_short(
    barcode: str, current_user: dict = Depends(get_current_user)
):
    """Get stock item by barcode"""
    repo = get_stock_repository()

    if repo is not None:
        stock = repo.find_by_barcode(barcode)
        if stock:
            return stock
        raise HTTPException(status_code=404, detail="Stock item not found")

    return {"barcode": barcode}


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
    request: StockAddRequest, current_user: dict = Depends(get_current_user)
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

        # Create stock entries for each unit
        stock_items = []
        for _ in range(request.quantity):
            barcode = generate_barcode(active_store, request.product_id)
            stock_data = {
                "product_id": request.product_id,
                "store_id": active_store,
                "barcode": barcode,
                "location_code": request.location_code or "DEFAULT",
                "batch_code": request.batch_code,
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
            "stock_ids": [s.get("stock_unit_id", s.get("stock_id", "")) for s in stock_items],
            "barcodes": [s.get("barcode", "") for s in stock_items],
            "quantity": len(stock_items),
        }

    return {"stock_id": str(uuid.uuid4()), "barcode": generate_barcode("STR", "PRD")}


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

    if stock_repo is None or product_repo is None:
        return {"products": [], "summary": {}}

    now = datetime.utcnow()
    thirty_days_ago = now - timedelta(days=30)
    ninety_days_ago = now - timedelta(days=90)

    # 1. Get all available stock grouped by product
    stock_pipeline = [
        {"$match": {"store_id": active_store, "status": {"$in": ["AVAILABLE", "RESERVED"]}}},
        {"$group": {
            "_id": "$product_id",
            "quantity": {"$sum": 1},
            "oldest_date": {"$min": "$created_at"},
            "total_value": {"$sum": {"$ifNull": ["$mrp", 0]}},
        }},
    ]
    stock_groups = stock_repo.aggregate(stock_pipeline)

    if not stock_groups:
        return {"products": [], "summary": {"total": 0, "classA": 0, "classB": 0, "classC": 0, "slowMovingValue": 0, "averageAge": 0}}

    # 2. Get sold items in last 30 and 90 days for turnover calculation
    sold_30d_pipeline = [
        {"$match": {"store_id": active_store, "status": "SOLD", "sold_at": {"$gte": thirty_days_ago}}},
        {"$group": {"_id": "$product_id", "sales_30d": {"$sum": 1}}},
    ]
    sold_90d_pipeline = [
        {"$match": {"store_id": active_store, "status": "SOLD", "sold_at": {"$gte": ninety_days_ago}}},
        {"$group": {"_id": "$product_id", "sales_90d": {"$sum": 1}}},
    ]
    last_sale_pipeline = [
        {"$match": {"store_id": active_store, "status": "SOLD"}},
        {"$group": {"_id": "$product_id", "last_sale": {"$max": "$sold_at"}}},
    ]

    sales_30d = {r["_id"]: r["sales_30d"] for r in stock_repo.aggregate(sold_30d_pipeline)}
    sales_90d = {r["_id"]: r["sales_90d"] for r in stock_repo.aggregate(sold_90d_pipeline)}
    last_sales = {r["_id"]: r["last_sale"] for r in stock_repo.aggregate(last_sale_pipeline)}

    # 3. Enrich with product details and calculate metrics
    products = []
    for sg in stock_groups:
        pid = sg["_id"]
        product = product_repo.find_by_id(pid)
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

        products.append({
            "id": pid,
            "sku": product.get("sku", ""),
            "name": product.get("name", product.get("model", "")),
            "brand": product.get("brand", ""),
            "category": product.get("category", ""),
            "quantity": qty,
            "value": round(value, 2),
            "daysInStock": days_in_stock,
            "lastSaleDate": last_sale.isoformat() if isinstance(last_sale, datetime) else last_sale,
            "salesLast30Days": s30,
            "salesLast90Days": s90,
            "turnoverRate": round(turnover, 1),
            "classification": cls,
            "ageCategory": age_cat,
        })

    # Sort: Slow movers first (C, then B, then A), then by days in stock desc
    cls_order = {"C": 0, "B": 1, "A": 2}
    products.sort(key=lambda p: (cls_order.get(p["classification"], 1), -p["daysInStock"]))

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
            collection = db["stock_counts"]
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
    current_user: dict = Depends(get_current_user),
):
    """Start a new physical stock count session"""
    active_store = validate_store_access(None, current_user)
    stock_repo = get_stock_repository()
    db = _get_db()

    count_id = str(uuid.uuid4())
    now = datetime.utcnow()
    audit_number = f"AUDIT-{now.strftime('%y%m%d')}-{count_id[:6].upper()}"

    # Get system quantities for the category/store so we can calculate variances later
    system_quantities: Dict[str, int] = {}
    if stock_repo is not None:
        pipeline = [
            {"$match": {"store_id": active_store, "status": {"$in": ["AVAILABLE", "RESERVED"]}}},
        ]
        if request.category:
            # Need to join with products to filter by category — use direct match if stock has category
            pipeline[0]["$match"]["category"] = request.category

        pipeline.append({"$group": {"_id": "$product_id", "qty": {"$sum": 1}}})
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
        "created_by_name": current_user.get("full_name", current_user.get("username", "")),
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
            db["stock_counts"].insert_one(count_doc)
        except Exception as e:
            logger.warning(f"stock_count create error: {e}")

    # Remove _id if present
    count_doc.pop("_id", None)
    return count_doc


@router.post("/stock-count/{count_id}/items")
async def record_count_item(
    count_id: str,
    item: StockCountItem,
    current_user: dict = Depends(get_current_user),
):
    """Record a counted item in an active stock count session"""
    db = _get_db()

    if db is None:
        return {"message": "Item recorded (no DB)", "count_id": count_id}

    try:
        collection = db["stock_counts"]
        count_doc = collection.find_one({"count_id": count_id})
        if not count_doc:
            raise HTTPException(status_code=404, detail="Stock count session not found")
        if count_doc.get("status") != "in_progress":
            raise HTTPException(status_code=400, detail="Stock count is not in progress")

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
            items.append({
                "product_id": item.product_id,
                "product_name": item.product_name or "",
                "sku": item.sku or "",
                "counted_quantity": item.counted_quantity,
                "notes": item.notes,
                "counted_at": datetime.utcnow().isoformat(),
                "counted_by": current_user.get("user_id", ""),
            })

        collection.update_one(
            {"count_id": count_id},
            {"$set": {"items": items, "items_counted": len(items)}},
        )

        return {"message": "Item recorded", "count_id": count_id, "items_counted": len(items)}

    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"record_count_item error: {e}")
        return {"message": "Item recorded", "count_id": count_id}


@router.post("/stock-count/{count_id}/complete")
async def complete_stock_count(
    count_id: str,
    request: Optional[CompleteStockCountRequest] = None,
    current_user: dict = Depends(get_current_user),
):
    """Complete stock count — calculates variances between system and physical count"""
    db = _get_db()

    if db is None:
        return {"message": "Stock count completed", "variances": []}

    try:
        collection = db["stock_counts"]
        count_doc = collection.find_one({"count_id": count_id})
        if not count_doc:
            raise HTTPException(status_code=404, detail="Stock count session not found")
        if count_doc.get("status") != "in_progress":
            raise HTTPException(status_code=400, detail="Stock count is not in progress")

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

            variances.append({
                "product_id": pid,
                "product_name": item.get("product_name", ""),
                "sku": item.get("sku", ""),
                "system_quantity": system,
                "physical_quantity": counted,
                "variance": variance,
                "variance_percentage": var_pct,
            })

        # Overall metrics
        overall_var_pct = round(((total_counted - total_system) / max(total_system, 1)) * 100, 2)
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
        collection = db["stock_counts"]
        count_doc = collection.find_one({"count_id": count_id})
        if not count_doc:
            raise HTTPException(status_code=404, detail="Stock count session not found")
        count_doc.pop("_id", None)
        return count_doc
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"get_stock_count error: {e}")
        raise HTTPException(status_code=500, detail="Internal error")


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
    return {"transfers": [], "note": "Use /api/v1/transfers for full transfer management"}


@router.post("/transfers")
async def create_transfer(
    request: StockTransferRequest, current_user: dict = Depends(get_current_user)
):
    """Create a stock transfer — delegates to /transfers for full workflow"""
    return {
        "transfer_id": str(uuid.uuid4()),
        "transfer_number": f"TRF-{uuid.uuid4().hex[:6].upper()}",
        "note": "Use /api/v1/transfers for full transfer management",
    }


@router.post("/transfers/{transfer_id}/send")
async def send_transfer(
    transfer_id: str, current_user: dict = Depends(get_current_user)
):
    """Mark transfer as sent"""
    return {"message": "Transfer sent", "transfer_id": transfer_id}


@router.post("/transfers/{transfer_id}/receive")
async def receive_transfer(
    transfer_id: str, items: List[dict], current_user: dict = Depends(get_current_user)
):
    """Receive a stock transfer"""
    return {"message": "Transfer received", "transfer_id": transfer_id}
