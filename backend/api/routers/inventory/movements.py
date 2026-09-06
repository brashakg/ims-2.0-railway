"""Stock movements ledger (Movements tab) and its per-source collectors."""

from ._shared import (
    Depends,
    Dict,
    HTTPException,
    List,
    Optional,
    Query,
    _SOLD_STATUSES,
    date,
    datetime,
    get_current_user,
    logger,
    router,
    timedelta,
    validate_store_access,
)
from .helpers import (
    _get_db,
)

# ============================================================================
# STOCK MOVEMENTS LEDGER (Movements tab)
# ============================================================================
# One reverse-chronological ledger merged from the real event sources that
# move stock today:
#   RECEIVED      <- `grns` (status ACCEPTED)          qty positive
#   SOLD          <- `orders` (status in _SOLD_STATUSES) qty negative
#   TRANSFER_OUT  <- `stock_transfers` shipped leg     qty negative (from store)
#   TRANSFER_IN   <- `stock_transfers` received leg    qty positive (to store)
#   OPENING_STOCK <- `opening_stock_batches` commit summaries, qty positive
#                    (one event per commit batch x product line)
#
# DELIBERATELY EXCLUDED for now: stock_units status flips (QUARANTINED /
# lift-quarantine / stock-count reconcile). Those are unit-level state changes
# recorded in `stock_audit` with a different granularity (per serialized unit,
# not per product line); folding them in honestly needs a per-unit -> per-line
# rollup that is out of scope here. The ledger says so via `sources`.
#
# Each source read is capped (_MOVEMENTS_PER_SOURCE_CAP newest docs inside the
# `days` window) and FAIL-SOFT: a source that errors shortens the ledger and is
# reported in `sources`, it never 5xxes the endpoint.

_MOVEMENT_TYPES = ("RECEIVED", "SOLD", "TRANSFER_IN", "TRANSFER_OUT", "OPENING_STOCK")
_MOVEMENTS_PER_SOURCE_CAP = 300


def _movement_iso(value) -> str:
    """Coerce a stored timestamp (datetime, date, ISO string) to an ISO string
    so mixed-type `at` values sort correctly. Unknown -> '' (sorts last)."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day).isoformat()
    if value is None:
        return ""
    return str(value)


def _collect_received_events(
    db, store_id: Optional[str], cutoff_iso: str, product_id: Optional[str]
) -> List[Dict]:
    """RECEIVED events from ACCEPTED GRNs (qty = accepted units, positive).
    grns.created_at / accepted_at are ISO strings (see vendors.py)."""
    flt: Dict = {"status": "ACCEPTED", "created_at": {"$gte": cutoff_iso}}
    if store_id:
        flt["store_id"] = store_id
    if product_id:
        flt["items.product_id"] = product_id
    events: List[Dict] = []
    cur = (
        db.get_collection("grns")
        .find(
            flt,
            {
                "_id": 0,
                "grn_id": 1,
                "grn_number": 1,
                "po_number": 1,
                "store_id": 1,
                "items": 1,
                "accepted_at": 1,
                "created_at": 1,
            },
        )
        .sort("created_at", -1)
        .limit(_MOVEMENTS_PER_SOURCE_CAP)
    )
    for grn in cur:
        at = _movement_iso(grn.get("accepted_at") or grn.get("created_at"))
        ref = grn.get("grn_number") or grn.get("grn_id") or ""
        po_number = grn.get("po_number")
        detail = f"GRN {ref}" + (f" against PO {po_number}" if po_number else "")
        for idx, item in enumerate(grn.get("items") or []):
            pid = item.get("product_id")
            if not pid or (product_id and pid != product_id):
                continue
            # accepted_qty drives the ledger; received_qty is ONLY a fallback
            # for legacy lines missing the field. An explicit accepted_qty=0
            # (all units rejected) put nothing on the shelf -> no event.
            raw_qty = item.get("accepted_qty")
            if raw_qty is None:
                raw_qty = item.get("received_qty")
            try:
                qty = int(raw_qty or 0)
            except (TypeError, ValueError):
                qty = 0
            if qty <= 0:
                continue
            events.append(
                {
                    "id": f"RECEIVED:{grn.get('grn_id') or ref}:{pid}:{idx}",
                    "at": at,
                    "type": "RECEIVED",
                    "product_id": pid,
                    "product_name": item.get("product_name") or "",
                    "sku": item.get("sku") or "",
                    "qty": qty,
                    "ref": ref,
                    "ref_id": grn.get("grn_id") or "",
                    "store_id": grn.get("store_id") or "",
                    "detail": detail,
                }
            )
    return events


def _collect_sold_events(
    db, store_id: Optional[str], cutoff_dt: datetime, product_id: Optional[str]
) -> List[Dict]:
    """SOLD events from orders whose status means a real sale (qty negative).
    orders.created_at is a BSON Date (see order_repository.py)."""
    flt: Dict = {
        "created_at": {"$gte": cutoff_dt},
        "status": {"$in": _SOLD_STATUSES},
    }
    if store_id:
        flt["store_id"] = store_id
    if product_id:
        flt["items.product_id"] = product_id
    events: List[Dict] = []
    cur = (
        db.get_collection("orders")
        .find(
            flt,
            {
                "_id": 0,
                "order_id": 1,
                "order_number": 1,
                "invoice_number": 1,
                "store_id": 1,
                "items": 1,
                "created_at": 1,
            },
        )
        .sort("created_at", -1)
        .limit(_MOVEMENTS_PER_SOURCE_CAP)
    )
    for order in cur:
        at = _movement_iso(order.get("created_at"))
        ref = order.get("invoice_number") or order.get("order_number") or ""
        for idx, item in enumerate(order.get("items") or []):
            pid = item.get("product_id")
            if not pid or (product_id and pid != product_id):
                continue
            try:
                qty = int(item.get("quantity") or 1)
            except (TypeError, ValueError):
                qty = 1
            if qty <= 0:
                continue
            events.append(
                {
                    "id": f"SOLD:{order.get('order_id') or ref}:{pid}:{idx}",
                    "at": at,
                    "type": "SOLD",
                    "product_id": pid,
                    "product_name": item.get("product_name") or item.get("name") or "",
                    "sku": item.get("sku") or "",
                    "qty": -qty,
                    "ref": ref,
                    "ref_id": order.get("order_id") or "",
                    "store_id": order.get("store_id") or "",
                    "detail": f"Sale {ref}".strip(),
                }
            )
    return events


def _collect_transfer_events(
    db, store_id: Optional[str], cutoff_iso: str, product_id: Optional[str]
) -> List[Dict]:
    """TRANSFER_OUT (shipped leg, negative, stamped with the FROM store) and
    TRANSFER_IN (received leg, positive, stamped with the TO store) from
    `stock_transfers`. shipped_at / received_at are ISO strings (transfers.py).

    When the caller is scoped to one store, only the leg that touches that
    store is emitted (an outbound transfer is an OUT event at the sender and
    an IN event at the receiver -- never both in one store's ledger)."""
    time_or = {
        "$or": [
            {"shipped_at": {"$gte": cutoff_iso}},
            {"received_at": {"$gte": cutoff_iso}},
        ]
    }
    clauses: List[Dict] = [time_or]
    if store_id:
        clauses.append(
            {"$or": [{"from_location_id": store_id}, {"to_location_id": store_id}]}
        )
    if product_id:
        clauses.append({"items.product_id": product_id})
    flt: Dict = clauses[0] if len(clauses) == 1 else {"$and": clauses}
    events: List[Dict] = []
    cur = (
        db.get_collection("stock_transfers")
        .find(
            flt,
            {
                "_id": 0,
                "id": 1,
                "transfer_number": 1,
                "from_location_id": 1,
                "from_location_name": 1,
                "to_location_id": 1,
                "to_location_name": 1,
                "items": 1,
                "shipped_at": 1,
                "received_at": 1,
            },
        )
        .sort("shipped_at", -1)
        .limit(_MOVEMENTS_PER_SOURCE_CAP)
    )
    for transfer in cur:
        ref = transfer.get("transfer_number") or transfer.get("id") or ""
        tid = transfer.get("id") or ref
        from_id = transfer.get("from_location_id") or ""
        to_id = transfer.get("to_location_id") or ""
        from_name = transfer.get("from_location_name") or from_id
        to_name = transfer.get("to_location_name") or to_id
        shipped_at = transfer.get("shipped_at")
        received_at = transfer.get("received_at")
        for idx, item in enumerate(transfer.get("items") or []):
            pid = item.get("product_id")
            if not pid or (product_id and pid != product_id):
                continue
            name = item.get("product_name") or ""
            sku = item.get("sku") or ""
            # OUT leg: what actually shipped (older docs may only carry
            # quantity_requested -- respect it only when a ship happened).
            if shipped_at and (not store_id or from_id == store_id):
                try:
                    out_qty = int(
                        item.get("quantity_shipped")
                        if item.get("quantity_shipped") is not None
                        else item.get("quantity_requested") or 0
                    )
                except (TypeError, ValueError):
                    out_qty = 0
                if out_qty > 0:
                    events.append(
                        {
                            "id": f"TRANSFER_OUT:{tid}:{pid}:{idx}",
                            "at": _movement_iso(shipped_at),
                            "type": "TRANSFER_OUT",
                            "product_id": pid,
                            "product_name": name,
                            "sku": sku,
                            "qty": -out_qty,
                            "ref": ref,
                            "ref_id": tid,
                            "store_id": from_id,
                            "detail": f"Transfer {ref} to {to_name}",
                        }
                    )
            # IN leg: only units actually received (never the requested qty).
            if received_at and (not store_id or to_id == store_id):
                try:
                    in_qty = int(item.get("quantity_received") or 0)
                except (TypeError, ValueError):
                    in_qty = 0
                if in_qty > 0:
                    events.append(
                        {
                            "id": f"TRANSFER_IN:{tid}:{pid}:{idx}",
                            "at": _movement_iso(received_at),
                            "type": "TRANSFER_IN",
                            "product_id": pid,
                            "product_name": name,
                            "sku": sku,
                            "qty": in_qty,
                            "ref": ref,
                            "ref_id": tid,
                            "store_id": to_id,
                            "detail": f"Transfer {ref} from {from_name}",
                        }
                    )
    return events


def _collect_opening_stock_events(
    db, store_id: Optional[str], cutoff_iso: str, product_id: Optional[str]
) -> List[Dict]:
    """OPENING_STOCK events (qty positive) from `opening_stock_batches` -- the
    compact one-doc-per-commit summary the importer writes. One event per
    batch x product line; committed_at is an ISO string (see
    _write_opening_stock_batch)."""
    flt: Dict = {"committed_at": {"$gte": cutoff_iso}}
    if store_id:
        flt["store_id"] = store_id
    if product_id:
        flt["lines.product_id"] = product_id
    events: List[Dict] = []
    cur = (
        db.get_collection("opening_stock_batches")
        .find(
            flt,
            {
                "_id": 0,
                "batch_id": 1,
                "store_id": 1,
                "committed_at": 1,
                "lines": 1,
            },
        )
        .sort("committed_at", -1)
        .limit(_MOVEMENTS_PER_SOURCE_CAP)
    )
    for batch in cur:
        at = _movement_iso(batch.get("committed_at"))
        ref = batch.get("batch_id") or ""
        for idx, line in enumerate(batch.get("lines") or []):
            pid = line.get("product_id")
            if not pid or (product_id and pid != product_id):
                continue
            try:
                qty = int(line.get("qty") or 0)
            except (TypeError, ValueError):
                qty = 0
            if qty <= 0:
                continue
            events.append(
                {
                    "id": f"OPENING_STOCK:{ref}:{pid}:{idx}",
                    "at": at,
                    "type": "OPENING_STOCK",
                    "product_id": pid,
                    "product_name": line.get("product_name") or "",
                    "sku": line.get("sku") or "",
                    "qty": qty,
                    "ref": ref,
                    "ref_id": ref,
                    "store_id": batch.get("store_id") or "",
                    "detail": f"Opening stock import {ref}",
                }
            )
    return events


def _enrich_movement_products(db, events: List[Dict]) -> None:
    """Fill product_name / sku on events whose source line didn't carry them
    (e.g. GRN items) with ONE batched products lookup. Fail-soft: any error
    leaves the events as-is."""
    missing = sorted(
        {
            e["product_id"]
            for e in events
            if e.get("product_id") and not (e.get("product_name") and e.get("sku"))
        }
    )
    if not missing:
        return
    try:
        by_id: Dict[str, Dict] = {}
        cur = db.get_collection("products").find(
            {
                "$or": [
                    {"product_id": {"$in": missing}},
                    {"_id": {"$in": missing}},
                ]
            },
            {"product_id": 1, "name": 1, "brand": 1, "model": 1, "sku": 1},
        )
        for product in cur:
            pid = str(product.get("product_id") or product.get("_id") or "")
            if pid:
                by_id[pid] = product
        for event in events:
            product = by_id.get(event.get("product_id") or "")
            if not product:
                continue
            if not event.get("product_name"):
                brand = product.get("brand", "")
                model = product.get("model", "")
                event["product_name"] = (
                    product.get("name")
                    or f"{brand} {model}".strip()
                    or product.get("sku", "")
                )
            if not event.get("sku"):
                event["sku"] = product.get("sku", "")
    except Exception as exc:  # noqa: BLE001
        logger.warning("[MOVEMENTS] product enrichment failed: %s", exc)


@router.get("/movements")
async def get_stock_movements(
    store_id: Optional[str] = Query(None),
    product_id: Optional[str] = Query(None),
    movement_type: Optional[str] = Query(
        None,
        alias="type",
        description=(
            "RECEIVED | SOLD | TRANSFER_IN | TRANSFER_OUT | OPENING_STOCK | "
            "TRANSFER (TRANSFER = both legs). Omit for all types."
        ),
    ),
    days: int = Query(90, ge=1, le=365),
    limit: int = Query(50, ge=1, le=200),
    skip: int = Query(0, ge=0),
    current_user: dict = Depends(get_current_user),
):
    """Merged stock-movement ledger for the Movements tab.

    Store resolution mirrors /inventory/stock: the explicit store_id is
    validated against the caller's reach (403 on a foreign store for a
    store-scoped role); omitted -> the caller's active store (admins with no
    active store see all stores). Sorted newest-first; paged via skip/limit
    over the merged in-window ledger; `sources` reports per-source health.
    """
    active_store = validate_store_access(store_id, current_user)

    if movement_type:
        movement_type = movement_type.strip().upper()
        if movement_type not in _MOVEMENT_TYPES + ("TRANSFER",):
            raise HTTPException(
                status_code=400,
                detail=(
                    "Invalid type. Use one of: "
                    + ", ".join(_MOVEMENT_TYPES + ("TRANSFER",))
                ),
            )

    empty = {
        "items": [],
        "total": 0,
        "skip": skip,
        "limit": limit,
        "has_more": False,
        "days": days,
        "store_id": active_store,
        "sources": {
            "grns": "skipped",
            "orders": "skipped",
            "transfers": "skipped",
            "opening_stock": "skipped",
        },
    }
    db = _get_db()
    if db is None:
        return empty

    cutoff_dt = datetime.utcnow() - timedelta(days=days)
    cutoff_iso = cutoff_dt.isoformat()

    events: List[Dict] = []
    sources: Dict[str, str] = {}
    collectors = (
        ("grns", lambda: _collect_received_events(db, active_store, cutoff_iso, product_id)),
        ("orders", lambda: _collect_sold_events(db, active_store, cutoff_dt, product_id)),
        ("transfers", lambda: _collect_transfer_events(db, active_store, cutoff_iso, product_id)),
        ("opening_stock", lambda: _collect_opening_stock_events(db, active_store, cutoff_iso, product_id)),
    )
    for name, collect in collectors:
        try:
            events.extend(collect())
            sources[name] = "ok"
        except Exception as exc:  # noqa: BLE001
            logger.warning("[MOVEMENTS] source %s failed: %s", name, exc)
            sources[name] = "error"

    if movement_type == "TRANSFER":
        events = [e for e in events if e["type"] in ("TRANSFER_IN", "TRANSFER_OUT")]
    elif movement_type:
        events = [e for e in events if e["type"] == movement_type]

    events.sort(key=lambda e: e.get("at") or "", reverse=True)
    total = len(events)
    page = events[skip : skip + limit]
    _enrich_movement_products(db, page)

    return {
        "items": page,
        "total": total,
        "skip": skip,
        "limit": limit,
        "has_more": skip + limit < total,
        "days": days,
        "store_id": active_store,
        "sources": sources,
    }


# NOTE: Specific routes MUST come before /{parameter} routes
