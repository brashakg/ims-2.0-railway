"""Opening-stock importer (go-live): preview + commit."""

from ._shared import (
    BaseModel,
    Depends,
    Dict,
    Field,
    HTTPException,
    List,
    Optional,
    _INVENTORY_ROLES,
    barcode_svc,
    date,
    datetime,
    get_audit_repository,
    get_product_repository,
    get_stock_repository,
    logger,
    require_roles,
    router,
    uuid,
)
from .helpers import (
    _get_db,
    _reject_stock_mint_on_online_store,
    generate_barcode,
)

# ============================================================================
# OPENING-STOCK IMPORTER (go-live)
# ============================================================================
# Bulk-seed shelf quantities at go-live: the owner uploads a CSV (parsed to JSON
# rows client-side) of {product_id|sku, quantity, [unit_cost, location_code,
# batch_code, expiry_date]}. PREVIEW validates every row, flags products that
# ALREADY hold stock so a re-run can't silently double inventory, and shows the
# total valuation. COMMIT mints the serialized stock_units rows via the same
# path as /stock/add (stamping GRN-style cost fields when unit_cost is given),
# with a skip_if_existing guard; it then writes ONE `opening_stock_batches`
# summary doc (feeds the movements ledger) and ONE compliance audit row -- both
# fail-soft. Control-over-convenience: preview is the default; the owner sees
# exactly what will happen before any write.


class OpeningStockRow(BaseModel):
    # Identify the product by EITHER product_id or sku (sku is what owners have
    # in their spreadsheets). At least one must be present; product_id wins.
    product_id: Optional[str] = None
    sku: Optional[str] = None
    quantity: int = Field(..., ge=1, le=10000)
    location_code: Optional[str] = None
    batch_code: Optional[str] = None
    expiry_date: Optional[date] = None
    # Optional per-unit landed cost. When provided (> 0) every minted unit is
    # stamped with unit_cost / cost_price / cost_source="OPENING_STOCK" -- the
    # same cost fields the GRN accept path stamps (vendors.py) -- so opening
    # stock enters the books at a real valuation. Absent/0 -> units mint
    # exactly as before (no cost fields).
    unit_cost: Optional[float] = Field(None, ge=0)


class OpeningStockImport(BaseModel):
    rows: List[OpeningStockRow] = Field(..., min_length=1, max_length=5000)
    # When True, a product that already has AVAILABLE stock is SKIPPED (not
    # added to) — the safe default so a double-submit never doubles stock.
    skip_if_existing: bool = True


def _resolve_opening_stock_row(row, product_repo, stock_repo, active_store):
    """Return (product, existing_qty, error) for one import row. error is a
    human string when the row can't be imported; product is the matched doc."""
    ident = (row.product_id or "").strip() or (row.sku or "").strip()
    if not ident:
        return None, 0, "Row has neither product_id nor sku."
    product = None
    if row.product_id:
        product = product_repo.find_by_id(row.product_id.strip())
    if product is None and row.sku:
        product = product_repo.find_by_sku(row.sku.strip())
    if product is None:
        return None, 0, f"No product matches '{ident}'."
    pid = product.get("product_id")
    existing = stock_repo.find_available(pid, active_store)
    return product, existing, None


def _opening_stock_cost_fields(row) -> Dict:
    """Cost fields to stamp on every unit minted for this row. Mirrors the GRN
    accept path (vendors.py): unit_cost + cost_price (same value) +
    cost_source. Empty dict when no usable cost -> mint exactly as before."""
    if row.unit_cost and row.unit_cost > 0:
        unit_cost = round(float(row.unit_cost), 2)
        return {
            "unit_cost": unit_cost,
            "cost_price": unit_cost,
            "cost_source": "OPENING_STOCK",
        }
    return {}


def _write_opening_stock_batch(
    db, store_id, user_id, skip_if_existing, lines, total_units, total_value
) -> Optional[str]:
    """Persist ONE compact `opening_stock_batches` summary doc per commit (one
    line per product actually minted). The movements ledger reads THIS doc --
    one small query instead of a giant per-unit stock_units scan. Fail-soft:
    any error is logged and swallowed (the minted stock always wins); returns
    the batch_id on success, None when nothing was written."""
    if db is None or not lines:
        return None
    batch_id = f"OSB-{uuid.uuid4().hex[:10].upper()}"
    try:
        db.get_collection("opening_stock_batches").insert_one(
            {
                "batch_id": batch_id,
                "store_id": store_id,
                "committed_by": user_id,
                "committed_at": datetime.utcnow().isoformat(),
                "skip_if_existing": skip_if_existing,
                "lines": lines,
                "total_units": total_units,
                "total_value": round(total_value, 2),
            }
        )
        return batch_id
    except Exception as exc:  # noqa: BLE001
        logger.warning("[INVENTORY] opening-stock batch summary failed: %s", exc)
        return None


def _opening_stock_audit(batch_id, store_id, user_id, details: Dict) -> None:
    """ONE compliance audit row per opening-stock commit (who/when/store/
    counts/value). Fail-soft: an audit hiccup never undoes the import that
    triggered it (mirrors _quarantine_audit / online_store_push._write_audit)."""
    try:
        audit = get_audit_repository()
        if audit is not None:
            audit.create(
                {
                    "action": "OPENING_STOCK_IMPORT",
                    "entity_type": "OPENING_STOCK_BATCH",
                    "entity_id": batch_id or "",
                    "store_id": store_id,
                    "user_id": user_id,
                    "details": details,
                }
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[INVENTORY] opening-stock audit failed: %s", exc)


@router.post("/opening-stock/preview")
async def opening_stock_preview(
    payload: OpeningStockImport,
    current_user: dict = Depends(require_roles(*_INVENTORY_ROLES)),
):
    """Dry-run an opening-stock import: validate every row and report what COMMIT
    would do. Never writes. Flags rows whose product already holds stock (the
    re-import / double-count risk) so the owner decides before committing."""
    stock_repo = get_stock_repository()
    product_repo = get_product_repository()
    active_store = current_user.get("active_store_id")
    if stock_repo is None or product_repo is None:
        raise HTTPException(status_code=503, detail="Inventory store not available")

    results = []
    to_add = 0
    will_skip = 0
    errors = 0
    total_value = 0.0
    for i, row in enumerate(payload.rows):
        product, existing, err = _resolve_opening_stock_row(
            row, product_repo, stock_repo, active_store
        )
        if err:
            errors += 1
            results.append(
                {
                    "index": i,
                    "status": "ERROR",
                    "identifier": row.product_id or row.sku,
                    "message": err,
                }
            )
            continue
        unit_cost = _opening_stock_cost_fields(row).get("unit_cost")
        already = existing > 0
        if already and payload.skip_if_existing:
            will_skip += 1
            status = "SKIP_EXISTING"
            msg = f"Already has {existing} in stock — will be skipped."
        else:
            to_add += row.quantity
            if unit_cost:
                total_value += unit_cost * row.quantity
            status = "WILL_ADD" if not already else "WILL_ADD_ON_TOP"
            msg = (
                f"Will add {row.quantity}."
                if not already
                else f"Already has {existing}; will ADD {row.quantity} on top."
            )
        results.append(
            {
                "index": i,
                "status": status,
                "product_id": product.get("product_id"),
                "sku": product.get("sku"),
                "name": product.get("model") or product.get("name") or "",
                "quantity": row.quantity,
                "existing": existing,
                "unit_cost": unit_cost,
                "message": msg,
            }
        )

    return {
        "rows": results,
        "summary": {
            "total_rows": len(payload.rows),
            "units_to_add": to_add,
            "rows_to_skip": will_skip,
            "rows_with_errors": errors,
            # Valuation the operator is about to put on the shelf (only rows
            # that WILL add units and carry a unit_cost contribute).
            "total_value": round(total_value, 2),
            "skip_if_existing": payload.skip_if_existing,
        },
    }


@router.post("/opening-stock/commit")
async def opening_stock_commit(
    payload: OpeningStockImport,
    current_user: dict = Depends(require_roles(*_INVENTORY_ROLES)),
):
    """Commit an opening-stock import: mint serialized stock_units rows (same as
    /stock/add) for every valid row. Per-row errors never abort the batch. With
    skip_if_existing=True (default) a product that already holds stock is left
    untouched, so a double-submit can't double inventory."""
    stock_repo = get_stock_repository()
    product_repo = get_product_repository()
    active_store = current_user.get("active_store_id")

    # F9: an ONLINE store is pooled + stockless -- a bulk opening-stock seed
    # would strand thousands of units on a store that can never sell them and
    # would double every pooled on-hand rollup.
    _reject_stock_mint_on_online_store(active_store, "import opening stock")

    if stock_repo is None or product_repo is None:
        raise HTTPException(status_code=503, detail="Inventory store not available")

    _db = _get_db()
    _counter = _db.get_collection("counters") if _db is not None else None

    results = []
    units_added = 0
    rows_skipped = 0
    rows_errored = 0
    total_value = 0.0
    batch_lines: List[Dict] = []
    for i, row in enumerate(payload.rows):
        product, existing, err = _resolve_opening_stock_row(
            row, product_repo, stock_repo, active_store
        )
        if err:
            rows_errored += 1
            results.append(
                {
                    "index": i,
                    "status": "ERROR",
                    "identifier": row.product_id or row.sku,
                    "message": err,
                }
            )
            continue
        if existing > 0 and payload.skip_if_existing:
            rows_skipped += 1
            results.append(
                {
                    "index": i,
                    "status": "SKIPPED",
                    "product_id": product.get("product_id"),
                    "sku": product.get("sku"),
                    "existing": existing,
                    "message": f"Skipped — already has {existing} in stock.",
                }
            )
            continue

        pid = product.get("product_id")
        cost_fields = _opening_stock_cost_fields(row)
        created_count = 0
        for _ in range(row.quantity):
            barcode = barcode_svc.next_unit_ean13(_counter) or generate_barcode(
                active_store or "STR", pid
            )
            stock_data = {
                "product_id": pid,
                "store_id": active_store,
                "barcode": barcode,
                "quantity": 1,
                "location_code": row.location_code or "DEFAULT",
                "batch_code": row.batch_code,
                "expiry_date": row.expiry_date.isoformat() if row.expiry_date else None,
                "status": "AVAILABLE",
                "is_reserved": False,
                "barcode_printed": False,
                "created_by": current_user.get("user_id"),
                "source": "OPENING_STOCK",
                **cost_fields,
            }
            if stock_repo.create(stock_data):
                created_count += 1
        units_added += created_count
        if cost_fields:
            total_value += cost_fields["unit_cost"] * created_count
        if created_count > 0:
            batch_lines.append(
                {
                    "product_id": pid,
                    "sku": product.get("sku"),
                    "product_name": product.get("model") or product.get("name") or "",
                    "qty": created_count,
                    "unit_cost": cost_fields.get("unit_cost"),
                }
            )
        results.append(
            {
                "index": i,
                "status": "ADDED",
                "product_id": pid,
                "sku": product.get("sku"),
                "added": created_count,
                "unit_cost": cost_fields.get("unit_cost"),
                "message": f"Added {created_count} unit(s).",
            }
        )

    # ONE compact summary doc per commit -- the movements ledger's
    # OPENING_STOCK source reads this (never a per-unit stock_units scan).
    batch_id = _write_opening_stock_batch(
        _db,
        active_store,
        current_user.get("user_id"),
        payload.skip_if_existing,
        batch_lines,
        units_added,
        total_value,
    )
    # ONE compliance audit row per commit (who/when/store/counts/value).
    _opening_stock_audit(
        batch_id,
        active_store,
        current_user.get("user_id"),
        {
            "total_rows": len(payload.rows),
            "products_count": len(batch_lines),
            "units_minted": units_added,
            "rows_skipped": rows_skipped,
            "rows_with_errors": rows_errored,
            "total_value": round(total_value, 2),
            "skip_if_existing": payload.skip_if_existing,
        },
    )

    return {
        "rows": results,
        "summary": {
            "total_rows": len(payload.rows),
            "units_added": units_added,
            "rows_skipped": rows_skipped,
            "rows_with_errors": rows_errored,
            "total_value": round(total_value, 2),
            "batch_id": batch_id,
        },
    }
