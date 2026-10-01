"""GET /stock - the store stock ledger, plus its GRN-cost and ledger-row builders."""

from ._shared import (
    _INVENTORY_ROLES,
    BaseModel,
    Depends,
    Dict,
    Field,
    HTTPException,
    List,
    Optional,
    Query,
    StockState,
    can_access_store_scoped,
    canonical_state,
    datetime,
    get_current_user,
    get_product_repository,
    get_stock_repository,
    is_on_hand,
    ist_date_str,
    logger,
    require_roles,
    router,
    timedelta,
    validate_store_access,
)
from ...services.cost_mask import can_see_cost, mask_cost_list
from ...services.item_events import on_hand_match
from ...utils.ist import ist_date_str_from_stored
from .helpers import (
    _get_db,
)
from ...services.reorder_policy import is_low_stock, low_stock_rows, reorder_level

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
    created_by: Optional[str] = Query(
        None,
        description=(
            "Cataloguer attribution filter: restrict the ledger to products "
            "created by this user_id (products.created_by). Only applies to "
            "the default per-product ledger view."
        ),
    ),
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

    # Cataloguer attribution is a MANAGER surface (panel finding 4): mirrors
    # the GET /products/cataloguers gate so the roster + per-user activity
    # cannot be reconstructed from ledger rows by regular staff. Non-managers
    # get neither the created_by filter (403, loud) nor the attribution
    # fields on the returned rows.
    can_see_attribution = bool(
        set(current_user.get("roles") or [])
        & {"SUPERADMIN", "ADMIN", "AREA_MANAGER", "STORE_MANAGER", "CATALOG_MANAGER"}
    )
    if created_by and isinstance(created_by, str) and not can_see_attribution:
        raise HTTPException(
            status_code=403,
            detail="Your role does not have access to the cataloguer filter",
        )

    # Category-filter fix: products store CANONICAL categories (SUNGLASS/FRAME),
    # callers send short codes (SG/FR) or plurals -- normalise fail-open.
    if category:
        from ...services.product_master import resolve_category

        category = resolve_category(category) or category

    if stock_repo is None or product_repo is None:
        return {"items": [], "total": 0}

    # Mode 1: THIS shop's low-stock list (the shop's own levels, D12).
    if low_stock:
        stock = low_stock_rows(product_repo, stock_repo, store_id=active_store)
        return {"items": stock, "total": len(stock)}

    # Mode 2: per-unit detail for one product. Consumers (e.g. transfer
    # picker that selects specific stock_ids) want the raw stock_units rows.
    if product_id:
        stock = mask_cost_list(
            stock_repo.find_by_product_store(product_id, active_store), current_user
        )
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
        created_by=created_by,
        include_attribution=can_see_attribution,
        shelf_cost=_shelf_cost(stock_repo, product_repo, active_store, current_user),
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
        # BUG-104 sweep finding, NOT an IST bug -- the same frame-TYPE bug as
        # buy_desk.py:110 and the vendor spend chart. `grns.created_at` is a
        # BSON datetime (BaseRepository._add_timestamps overwrites whatever the
        # caller passed), and Mongo type-brackets: an ISO-STRING $gte never
        # matches a Date, so this join returned NOTHING and the "+N via GRN-xxx"
        # source chip never appeared on the stock ledger. `cutoff` is pure
        # elapsed time in the stored naive-UTC frame, so it needs no IST shift.
        cutoff = datetime.now() - timedelta(days=30)
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
                    # BUG-104, VALUE rule (+5:30). accepted_at / created_at are
                    # naive-UTC instants; this is the "last received on" date a
                    # store user reads next to the stock line, so it is the IST
                    # business day. Goods booked in before 05:30 IST used to show
                    # the previous day.
                    "date": ist_date_str(when),
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
    created_by: Optional[str] = None,
    include_attribution: bool = True,
    shelf_cost: Optional[Dict[str, Dict]] = None,
) -> List[Dict]:
    """Per-product Stock Ledger rows for a store.

    Aggregates `stock_units` by (product_id, status) so a single product
    with multiple serialized units rolls into ONE row carrying:
      - on-hand count (anything canonicalising to AVAILABLE, plus a unit with
        no status at all; sums `quantity` but defaults to 1 when missing
        because units are typically qty=1)
      - reserved count (anything canonicalising to RESERVED)
      - product master fields (sku, name, brand, category, mrp, offer_price)
      - a representative location_code from any AVAILABLE unit. NOT a
        barcode: every unit carries its own, so one unit's code on the row
        read as the product's and changed when that unit shipped (F27). The
        units live behind GET /inventory/units.

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
        try:
            pipeline = [
                {"$match": {"store_id": store_id}},
                {
                    "$project": {
                        "product_id": 1,
                        "status": 1,
                        "quantity": 1,
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
                # Same rule as the $match readers, answered in Python because
                # the pipeline groups BY status: a copied allowlist here (and a
                # bare `== "RESERVED"` below) is what made a lowercase
                # `reserved` unit fall into neither bucket and vanish from the
                # ledger entirely.
                if is_on_hand(status):
                    on_hand_by_product[pid] = on_hand_by_product.get(pid, 0) + qty
                    # A sample location from any available unit for the
                    # Location column on the ledger row.
                    if pid not in sample_unit_by_product:
                        sample_unit_by_product[pid] = {
                            "location_code": row.get("location_code") or "",
                        }
                elif canonical_state(status) is StockState.RESERVED:
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
    # Cataloguer attribution filter: an index-friendly equality match pushed
    # into the SAME catalog query (no extra round-trip); absent -> untouched.
    if created_by:
        catalog_filter["created_by"] = created_by
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
                include_attribution=include_attribution,
            )
        )

    # ---- 3. Edge case - units exist for a product that's NOT in the
    # active catalog (deactivated SKU still on the shelf). Surface those
    # rows too so the manager can see + clear them. ------------------
    for pid, on_hand in on_hand_by_product.items():
        if pid in seen_pids:
            continue
        product = product_repo.find_by_id(pid) or {"product_id": pid}
        # Respect the cataloguer filter on stranded rows too -- a unit whose
        # product was created by someone else must not leak into a filtered
        # view. Rows with no created_by are hidden ONLY while the filter is
        # active (the unfiltered ledger still surfaces all stranded stock).
        if created_by and product.get("created_by") != created_by:
            continue
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
                include_attribution=include_attribution,
            )
        )

    # F47: each row's units at cost -- only when the caller may see cost
    # (_shelf_cost answers None for everyone else, so no cost key is written).
    if shelf_cost is not None:
        for row in items:
            row.update(_row_cost(shelf_cost.get(row["product_id"])))
    return items


def _shelf_cost(
    stock_repo, product_repo, store_id: Optional[str], user: dict
) -> Optional[Dict[str, Dict]]:
    """F47: what the units at this shop COST, per product -- the one stock
    value rule (services/stock_value), for the cost readers only
    (cost_mask "purchase": managers + accounts). None for the counter, which
    never sees cost, and when no shop is in view."""
    from ...services import stock_value
    from ...services.cost_mask import can_see_cost

    if not store_id or not can_see_cost(user, "purchase"):
        return None
    return stock_value.by_product(
        stock_value.shelf_units(stock_repo, product_repo, store_id)
    )


def _row_cost(held: Optional[Dict]) -> Dict:
    """The cost fields of one ledger row, read off stock_value.by_product.

    cost_value: the units on the shelf + reserved, at cost (the same units as
    the row's stock + reserved). unit_cost: per COSTED unit, None when no unit
    has a cost (never Rs 0). uncosted_units: units with no cost, so the screen
    can say so instead of quietly valuing them at nothing."""
    held = held or {}
    return {
        "cost_value": held.get("cost") or 0.0,
        "unit_cost": held.get("unit_cost"),
        "uncosted_units": held.get("uncosted_units") or 0,
    }


def _ledger_row(
    product: Dict,
    on_hand: int,
    reserved: int,
    sample_unit: Dict,
    store_id: Optional[str],
    last_grn: Optional[Dict] = None,
    include_attribution: bool = True,
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
        # The PRODUCT's own barcode only (F27) -- never a unit's.
        "barcode": product.get("barcode", "") or "",
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
        # THIS shop's level (None = not set, never -1) and the one rule's
        # low-stock verdict for it (owner ruling D12: levels are per shop).
        "reorder_point": reorder_level(product, store_id=store_id),
        "low_stock": is_low_stock(product, on_hand, store_id=store_id),
        # Procurement Phase 1 (additive, optional): the latest ACCEPTED GRN
        # that put stock of this product on this store's shelf, or None.
        # Shape: {"grn_number": str, "qty": int, "date": "YYYY-MM-DD"}.
        "last_grn": last_grn or None,
        # Cataloguer attribution (additive; free -- the ledger already joins
        # the full product doc, so this is a passthrough, never an extra
        # lookup). None on legacy docs created before the stamp existed.
        # MANAGER-ONLY (panel finding 4): withheld for regular staff so the
        # roster the /products/cataloguers gate protects stays non-derivable.
        "created_by": product.get("created_by") if include_attribution else None,
        "created_by_name": (
            product.get("created_by_name") if include_attribution else None
        ),
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
# UNITS VIEW + LABEL PRINT RECORD (F26 / F27)
# ============================================================================

# ponytail: one read capped at 500 units per product/receipt at one shop. The
# units still in the shop are read FIRST, so the cap only ever trims history
# (sold / transferred out); page it if 500 on one shelf ever happens.
_UNITS_LIMIT = 500


@router.get("/units")
async def list_units(
    store_id: Optional[str] = Query(None),
    product_id: Optional[str] = Query(None),
    grn_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Every serialised unit of one product -- or of one goods receipt -- at a shop.

    The ledger is one row per product; receiving mints one stock_unit, with its
    own barcode, per physical piece. This is where those pieces are listed and
    their labels printed from. Each unit carries the label fields (brand, model,
    colour, size, MRP) so the label renderer needs no second fetch. cost_price
    only for roles that already see cost (cost_mask.can_see_cost).
    """
    if not product_id and not grn_id:
        raise HTTPException(status_code=400, detail="Provide product_id or grn_id")
    flt: Dict = {}
    # A receipt's units sit at the shop it was received into, which need not be
    # the caller's active store (an admin receiving for another shop). Without
    # an explicit store, a receipt read is scoped per unit below instead.
    if store_id or not grn_id:
        active_store = validate_store_access(store_id, current_user)
        if not active_store:
            raise HTTPException(status_code=400, detail="Pick a store first")
        flt["store_id"] = active_store
    stock_repo = get_stock_repository()
    product_repo = get_product_repository()
    if stock_repo is None or product_repo is None:
        return {"units": [], "total": 0}

    if product_id:
        flt["product_id"] = product_id
    if grn_id:
        flt["source_type"] = "GRN"
        flt["source_id"] = grn_id
    # "In the shop" (the only units a label is for) is the ONE physical rule,
    # item_events.on_hand_match / is_on_hand with include_reserved=True: on the
    # shelf or reserved for an order. The ledger's "N units" (stock + reserved)
    # buckets the same rule; tests/test_on_hand_is_one_rule.py pins the two.
    in_shop = on_hand_match(include_reserved=True)
    found = stock_repo.find_many(
        {"$and": [flt, in_shop]}, sort=[("created_at", 1)], limit=_UNITS_LIMIT
    )
    if len(found) < _UNITS_LIMIT:  # then the newest history, up to the cap
        found += stock_repo.find_many(
            {"$and": [flt, {"$nor": [in_shop]}]},
            sort=[("created_at", -1)],
            limit=_UNITS_LIMIT - len(found),
        )
    docs = [d for d in found if can_access_store_scoped(d.get("store_id"), current_user)]

    def _in_shop(d: Dict) -> bool:
        return is_on_hand(d.get("status"), include_reserved=True)

    # Units still in the shop first (the ones that need a label), oldest first.
    docs.sort(key=lambda d: (not _in_shop(d), str(d.get("created_at") or "")))

    from ...services.product_master import existing_product_summary

    products: Dict[str, Dict] = {}
    show_cost = can_see_cost(current_user)
    units = []
    for d in docs:
        pid = d.get("product_id") or ""
        if pid not in products:
            products[pid] = existing_product_summary(product_repo.find_by_id(pid) or {})
        p = products[pid]
        # The one on-hand rule decides AVAILABLE (a legacy 'in_stock' or a unit
        # with no status is on the shelf); anything else by its canonical name.
        raw = d.get("status")
        state = StockState.AVAILABLE if is_on_hand(raw) else canonical_state(raw)
        unit = {
            "stock_id": d.get("stock_id"),
            "product_id": pid,
            "barcode": d.get("barcode") or "",
            "status": state.value if state else (str(raw or "").strip().upper() or "UNKNOWN"),
            # The dialog labels exactly these; it keeps no status list of its own.
            "in_shop": _in_shop(d),
            "grn_number": d.get("grn_number") or "",
            "source": d.get("source_type") or d.get("source") or "",
            # A transfer re-homes the unit and stamps received_at; created_at
            # is when (and where) it was first minted.
            "transfer_number": d.get("transfer_number") or "",
            "from_store_id": d.get("from_store_id") or "",
            "received_on": ist_date_str_from_stored(
                d.get("received_at") or d.get("created_at")
            ),
            "barcode_printed": bool(d.get("barcode_printed")),
            "location_code": d.get("location_code") or "",
            "name": p.get("name") or "",
            "brand": p.get("brand") or "",
            "model": p.get("model") or "",
            "colour": p.get("colour_code") or "",
            "size": p.get("size") or "",
            "mrp": p.get("mrp"),
        }
        if show_cost:
            unit["cost_price"] = d.get("cost_price")
        units.append(unit)
    return {"units": units, "total": len(units)}


class BarcodePrintedRequest(BaseModel):
    stock_ids: List[str] = Field(..., min_length=1, max_length=_UNITS_LIMIT)


@router.post("/units/barcode-printed")
async def mark_units_barcode_printed(
    body: BarcodePrintedRequest,
    current_user: dict = Depends(require_roles(*_INVENTORY_ROLES)),
):
    """Record that these units' labels were sent to the print dialog (F26).

    Called by the label renderer only after the print window opened. A unit in
    a shop the caller cannot reach is skipped, never 403'd, so one stray id
    cannot sink a whole receipt's record.
    """
    stock_repo = get_stock_repository()
    if stock_repo is None:
        raise HTTPException(status_code=503, detail="Stock store unavailable")
    updated: List[str] = []
    for sid in dict.fromkeys(body.stock_ids):
        unit = stock_repo.find_by_id(sid)
        if not unit or not can_access_store_scoped(unit.get("store_id"), current_user):
            continue
        if stock_repo.mark_barcode_printed(sid):
            updated.append(sid)
    return {"updated": len(updated), "stock_ids": updated}
