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
from ..dependencies import (
    get_stock_repository,
    get_product_repository,
    validate_store_access,
)

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
            "stock_ids": [
                s.get("stock_unit_id", s.get("stock_id", "")) for s in stock_items
            ],
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
            {
                "$match": {
                    "store_id": active_store,
                    "status": {"$in": ["AVAILABLE", "RESERVED"]},
                }
            },
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
    current_user: dict = Depends(get_current_user),
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
    current_user: dict = Depends(get_current_user),
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
    return {
        "transfers": [],
        "note": "Use /api/v1/transfers for full transfer management",
    }


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


# ============================================================================
# ADVANCED INVENTORY FEATURES (IMS 2.0)
# ============================================================================

# ============================================================================
# 1. NON-MOVING STOCK IDENTIFICATION
# ============================================================================


@router.get("/non-moving")
async def get_non_moving_stock(
    days: int = Query(90, ge=1, le=365),
    category: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """
    Identify products with 0 sales in the last N days.
    GET /inventory/non-moving?days=90
    """
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection error")

    try:
        products_coll = db.get_collection("products")
        orders_coll = db.get_collection("orders")
        stock_coll = db.get_collection("stock")

        # Get all products (optionally filtered by category)
        query = {} if not category else {"category": category}
        products = list(products_coll.find(query, {"_id": 1, "name": 1, "sku": 1}))

        # Get products with sales in last N days
        cutoff_date = datetime.utcnow() - timedelta(days=days)
        sold_products = set()

        orders = orders_coll.find(
            {
                "created_at": {"$gte": cutoff_date},
                "status": {"$in": _SOLD_STATUSES},
            },
            {"items": 1},
        )

        for order in orders:
            for item in order.get("items", []):
                sold_products.add(item.get("product_id"))

        # Find non-moving products
        non_moving = []
        for product in products:
            product_id = str(product.get("_id"))
            if product_id not in sold_products:
                # Get current stock
                stock = stock_coll.find({"product_id": product_id})
                total_qty = sum(s.get("quantity", 0) for s in stock)

                # Get last sold date
                last_order = orders_coll.find_one(
                    {"items.product_id": product_id},
                    {"created_at": 1},
                    sort=[("created_at", -1)],
                )

                non_moving.append(
                    {
                        "product_id": product_id,
                        "name": product.get("name", ""),
                        "sku": product.get("sku", ""),
                        "current_stock": total_qty,
                        "last_sold_date": (
                            last_order.get("created_at") if last_order else None
                        ),
                        "days_since_sale": days,
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
    current_user: dict = Depends(get_current_user),
):
    """
    Scan barcode and record physical count.
    POST /inventory/stock-count-scan
    """
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection error")

    try:
        stock_coll = db.get_collection("stock")
        products_coll = db.get_collection("products")

        # Find stock by barcode
        stock = stock_coll.find_one({"barcode": request.barcode})
        if not stock:
            raise HTTPException(status_code=404, detail="Barcode not found")

        product_id = stock.get("product_id")
        product = products_coll.find_one({"_id": product_id})

        system_count = stock.get("quantity", 0)
        variance = request.physical_count - system_count

        return {
            "barcode": request.barcode,
            "product_id": product_id,
            "product_name": product.get("name") if product else "Unknown",
            "sku": product.get("sku") if product else "",
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
# 3. CONTACT LENS BATCH/EXPIRY TRACKING
# ============================================================================


@router.get("/contact-lenses/expiry-status")
async def get_contact_lens_expiry_status(
    expiring_within_days: int = Query(90, ge=1, le=365),
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """
    Get contact lens products with expiry dates.
    Highlight those expiring within threshold days.
    GET /inventory/contact-lenses/expiry-status?expiring_within_days=90
    """
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection error")

    try:
        stock_coll = db.get_collection("stock")
        products_coll = db.get_collection("products")

        # Get all contact lens stocks with expiry dates
        cutoff_date = datetime.utcnow() + timedelta(days=expiring_within_days)
        stocks = list(
            stock_coll.find(
                {
                    "category": {"$in": ["CL", "CONTACT_LENS"]},
                    "expiry_date": {"$exists": True, "$ne": None},
                }
            )
        )

        expiring_soon = []
        expired = []
        safe = []

        for stock in stocks:
            product = products_coll.find_one({"_id": stock.get("product_id")})
            expiry = stock.get("expiry_date")
            if isinstance(expiry, str):
                expiry = datetime.fromisoformat(expiry)

            days_until_expiry = (expiry - datetime.utcnow()).days

            item = {
                "stock_id": str(stock.get("_id")),
                "product_id": stock.get("product_id"),
                "product_name": product.get("name") if product else "Unknown",
                "sku": product.get("sku") if product else "",
                "quantity": stock.get("quantity", 0),
                "expiry_date": expiry.isoformat() if expiry else None,
                "days_until_expiry": days_until_expiry,
            }

            if days_until_expiry < 0:
                expired.append(item)
            elif days_until_expiry <= expiring_within_days:
                expiring_soon.append(item)
            else:
                safe.append(item)

        return {
            "expired": sorted(expired, key=lambda x: x["days_until_expiry"]),
            "expiring_soon": sorted(
                expiring_soon, key=lambda x: x["days_until_expiry"]
            ),
            "safe": safe[:20],  # Limit to 20 items
            "summary": {
                "expired_count": len(expired),
                "expiring_soon_count": len(expiring_soon),
                "safe_count": len(safe),
            },
        }

    except Exception as e:
        logger.error(f"get_contact_lens_expiry_status error: {e}")
        raise HTTPException(status_code=500, detail="Error fetching lens expiry status")


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
        raise HTTPException(status_code=500, detail="Database connection error")

    try:
        stock_coll = db.get_collection("stock")
        products_coll = db.get_collection("products")

        # Define SPH and CYL ranges
        sph_values = [str(x / 2) for x in range(-16, 13)]  # -8.00 to +6.00
        cyl_values = [str(x / 2) for x in range(0, -9, -1)]  # 0 to -4.00

        # Initialize grid
        grid = {}
        for sph in sph_values:
            grid[sph] = {}
            for cyl in cyl_values:
                grid[sph][cyl] = {"count": 0, "in_stock": False}

        # Populate grid from stock
        optical_lenses = list(
            products_coll.find({"category": {"$in": ["LS", "OPTICAL_LENS"]}})
        )

        for product in optical_lenses:
            sph = product.get("attributes", {}).get("sph", "")
            cyl = product.get("attributes", {}).get("cyl", "")

            if sph in grid and cyl in grid.get(sph, {}):
                stock = stock_coll.find_one({"product_id": str(product.get("_id"))})
                count = stock.get("quantity", 0) if stock else 0
                grid[sph][cyl]["count"] = count
                grid[sph][cyl]["in_stock"] = count > 0

        return {
            "grid": grid,
            "sph_range": sph_values,
            "cyl_range": cyl_values,
        }

    except Exception as e:
        logger.error(f"get_lens_power_grid error: {e}")
        raise HTTPException(status_code=500, detail="Error fetching lens grid")


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
        stock_coll = db.get_collection("stock")
        products_coll = db.get_collection("products")

        cutoff_date = datetime.utcnow() - timedelta(days=days)

        # Get sales by brand from completed orders
        sales_by_brand = {}
        orders = orders_coll.find(
            {
                "created_at": {"$gte": cutoff_date},
                "status": {"$in": _SOLD_STATUSES},
            }
        )

        for order in orders:
            for item in order.get("items", []):
                product_id = item.get("product_id")
                product = products_coll.find_one({"_id": product_id})
                if product:
                    brand = product.get("brand", "Unknown")
                    qty = item.get("quantity", 0)
                    sales_by_brand[brand] = sales_by_brand.get(brand, 0) + qty

        # Get current stock by brand
        stock_by_brand = {}
        stocks = stock_coll.find({})
        for stock in stocks:
            product = products_coll.find_one({"_id": stock.get("product_id")})
            if product:
                brand = product.get("brand", "Unknown")
                qty = stock.get("quantity", 0)
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
        stock_coll = db.get_collection("stock")
        products_coll = db.get_collection("products")

        cutoff_date = datetime.utcnow() - timedelta(days=days)

        # Get sales volume by product
        sales_by_product = {}
        orders = orders_coll.find(
            {
                "created_at": {"$gte": cutoff_date},
                "status": {"$in": _SOLD_STATUSES},
            }
        )

        for order in orders:
            for item in order.get("items", []):
                product_id = item.get("product_id")
                qty = item.get("quantity", 0)
                sales_by_product[product_id] = sales_by_product.get(product_id, 0) + qty

        # Calculate average monthly sales
        months = max(days / 30, 1)
        avg_monthly_sales = {pid: qty / months for pid, qty in sales_by_product.items()}

        # Get current stock and identify overstock
        overstocked = []
        all_stocks = list(stock_coll.find({}))

        for stock in all_stocks:
            product_id = str(stock.get("product_id"))
            current_qty = stock.get("quantity", 0)
            avg_monthly = avg_monthly_sales.get(product_id, 0)

            # Flag if current > threshold * average
            if current_qty > (overstocking_threshold * avg_monthly):
                product = products_coll.find_one({"_id": product_id})
                months_of_stock = current_qty / max(avg_monthly, 1)

                overstocked.append(
                    {
                        "product_id": product_id,
                        "product_name": product.get("name") if product else "Unknown",
                        "sku": product.get("sku") if product else "",
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


# Broad "this order represents a real sale" status set (both cases seen in DB)
_SOLD_STATUSES = [
    "DELIVERED", "delivered", "Delivered",
    "COMPLETED", "completed", "Completed",
    "PAID", "paid", "Paid",
    "FULFILLED", "fulfilled", "Fulfilled",
]


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
    if out_of_stock_but_selling or below_reorder_point or runs_out_soon:
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

    # 2. LOW_STOCK — sells, getting low, but not yet reorder-critical
    if velocity > 0 and projected is not None and projected <= lead_time_days * 2:
        recommended = max(int(round(velocity * lead_time_days * 2 - stock)), 1)
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
    projection = {"_id": 0, "name": 1, "sku": 1, "barcode": 1, "brand": 1, "category": 1}
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
    current_user: dict = Depends(get_current_user),
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
    current_user: dict = Depends(get_current_user),
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
