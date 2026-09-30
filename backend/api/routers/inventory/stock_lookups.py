"""Low-stock, expiring, barcode lookups and POST /stock/add."""

from ._shared import (
    Depends,
    Dict,
    HTTPException,
    Optional,
    Query,
    _INVENTORY_ROLES,
    _reorder_disabled,
    barcode_svc,
    get_current_user,
    get_product_repository,
    get_stock_repository,
    logger,
    require_roles,
    router,
    uuid,
    validate_store_access,
)
from .models import (
    StockAddRequest,
)
from .helpers import (
    _get_db,
    _reject_stock_mint_on_online_store,
    generate_barcode,
)

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


_NEVER_GATED_AT = 10**6  # a line the guard lets through at this size, it never gates


def _most_it_sells(sells, hint: int) -> Optional[int]:
    """The largest quantity `sells(q)` lets through, or None when it lets
    _NEVER_GATED_AT through (the guard does not gate the line). Assumes only
    that the rule is monotone (refusing q means refusing q + 1). `hint` is
    where the search starts, never the answer."""
    if sells(hint + 1):
        if sells(_NEVER_GATED_AT):
            return None
        lo, hi = hint + 1, _NEVER_GATED_AT  # stock arrived since the hint
    elif hint == 0 or sells(hint):
        return hint  # the usual answer: two asks
    else:
        lo, hi = 0, hint  # stock left since the hint
    while hi - lo > 1:  # sells(lo) (0 always does), never sells(hi)
        mid = (lo + hi) // 2
        lo, hi = (mid, hi) if sells(mid) else (lo, mid)
    return lo


@router.get("/sellable")
def get_sellable_counts(
    product_ids: str = Query(..., description="Comma-separated product ids, at most 100."),
    item_types: Optional[str] = Query(
        None,
        description="Comma-separated order item_type per id, same order (the till's mapCategory).",
    ),
    current_user: dict = Depends(get_current_user),
):
    """F46: the till's per-tile stock badge and cart-line warning. For each id,
    the most order-create's oversell guard (_assert_serialized_stock_available)
    would let one line sell, found by ASKING THE GUARD: every number returned
    is a quantity it let through, one more is a quantity it refused; None when
    it never gates the line. find_available only picks the first question.

    The store is the one in the sign-in token, where create_order binds the
    guard (create.py: store_id = current_user['active_store_id']); no caller-
    named store, so the figure is always the store Complete sale checks.
    `canonical` is the id the guard sums a line under (_canonical_pid), so the
    cart adds a SKU line and a product_id line of one product together, as the
    guard does. Read-only; a plain def, so its reads run off the event loop."""
    from ..orders.stock import _assert_serialized_stock_available, _canonical_pid

    store = current_user.get("active_store_id")
    ids = [p.strip() for p in product_ids.split(",") if p.strip()]
    if len(ids) > 100:
        raise HTTPException(status_code=400, detail="At most 100 product ids per call")
    types = [t.strip() for t in (item_types or "").split(",")]
    stock_repo = get_stock_repository()
    product_repo = get_product_repository()

    def sells(line: dict, qty: int) -> bool:
        try:
            _assert_serialized_stock_available([{**line, "quantity": qty}], store, quiet=True)
        except HTTPException as exc:
            if exc.status_code != 409:
                raise
            return False
        return True

    # ponytail: per id, 1-4 reads to resolve it, 1 for the hint, then two asks
    # of the guard (1 read each untracked, 2 tracked): 4-9 indexed reads. A
    # receipt or sale landing mid-call bisects instead (~20 asks, rare). <= 24
    # tiles + the cart per call; move to one aggregate if that ever hurts.
    sellable: Dict[str, Optional[int]] = {}
    canonical: Dict[str, str] = {}
    for i, pid in enumerate(ids):
        canon = canonical[pid] = _canonical_pid(product_repo, pid)
        line = {"product_id": canon, "item_type": types[i] if i < len(types) else ""}
        try:
            hint = max(0, int(stock_repo.find_available(canon, store)))
        except Exception:  # noqa: BLE001 -- only a hint; the guard decides
            hint = 0
        sellable[pid] = _most_it_sells(lambda q, line=line: sells(line, q), hint)
    return {"store_id": store, "sellable": sellable, "canonical": canonical}


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

    # F9: never mint physical units onto a pooled, stockless ONLINE store.
    # Runs BEFORE the repo checks so the answer is the same 400 whether or not
    # the DB is reachable.
    _reject_stock_mint_on_online_store(active_store, "add stock")

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
