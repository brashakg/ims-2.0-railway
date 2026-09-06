"""Inventory intelligence: transfer recommendations + staff accountability."""

from ._shared import (
    BaseModel,
    Depends,
    Dict,
    HTTPException,
    Optional,
    Query,
    _INVENTORY_ROLES,
    datetime,
    get_product_repository,
    get_stock_repository,
    logger,
    require_roles,
    router,
    timedelta,
    validate_store_access,
)
from .helpers import (
    _get_db,
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
    from ...services.inventory_intel import recommend_transfers

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
            from ...services.name_resolver import store_name_map

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
    from ...services.inventory_intel import shrinkage_by_custodian

    db = _get_db()
    if db is None:
        return {"rows": []}
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    # A count that has been WRITTEN OFF is not off the hook: it is the one
    # whose loss was confirmed. Querying "completed" alone dropped every count
    # from this report the moment an admin wrote it off -- the only report
    # that names who was responsible for the shelf.
    q: dict = {
        "status": {"$in": ["completed", "reconciled"]},
        "completed_at": {"$gte": cutoff},
    }
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
            from ...services.name_resolver import store_name_map

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
