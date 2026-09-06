"""Stock count sessions: list, start, and the countable-scope helpers."""

from ._shared import (
    Any,
    Depends,
    Dict,
    HTTPException,
    List,
    Optional,
    Query,
    _INVENTORY_ROLES,
    _on_hand_status_clause,
    can_access_store_scoped,
    datetime,
    get_current_user,
    get_product_repository,
    get_stock_repository,
    hashlib,
    logger,
    require_roles,
    router,
    uuid,
    validate_store_access,
)
from .models import (
    StartStockCountRequest,
)
from .helpers import (
    _get_db,
)

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
            # Sanitize ObjectId; and an OPEN session's row must not ship the
            # opening snapshot (blind count -- see _withhold_expected_while_open).
            for c in counts:
                c.pop("_id", None)
                _withhold_expected_while_open(c)
            return {"counts": counts}
        except Exception as e:
            logger.warning(f"stock_count list error: {e}")

    return {"counts": []}


@router.post("/stock-count/start")
async def start_stock_count(
    request: StartStockCountRequest,
    current_user: dict = Depends(require_roles(*_INVENTORY_ROLES)),
):
    """Start a new physical stock count session"""
    active_store = validate_store_access(None, current_user)
    stock_repo = get_stock_repository()
    db = _get_db()

    count_id = str(uuid.uuid4())
    now = datetime.utcnow()
    audit_number = f"AUDIT-{now.strftime('%y%m%d')}-{count_id[:6].upper()}"

    # Get system quantities for the category/store so we can calculate variances later.
    # INV-6: stock_units docs don't carry a `category` field (that lives on
    # the products collection), so we cannot filter the aggregation directly.
    # When a category is requested, first resolve the product_ids that belong
    # to it, then scope the stock aggregation to those ids. This ensures that
    # a category-limited count only snapshots the right products.
    system_quantities: Optional[Dict[str, int]] = {}
    # WHICH units were on hand per product when the session opened -- see the
    # "ONE SNAPSHOT MECHANISM" note above. Without it, a sale and a receipt
    # that cancel out leave the line looking untouched.
    system_unit_fingerprints: Dict[str, str] = {}
    # Resolve category -> product_ids when filtering is requested, with the
    # SAME resolver the scope snapshot uses. None = the category could not be
    # resolved; the old fallback silently snapshotted the WHOLE STORE instead,
    # handing a category count a scope it never asked for.
    category_scope: Optional[List[str]] = None
    scope_failed = False
    if request.category:
        category_scope = _category_product_ids(
            db, request.category, product_repo=get_product_repository()
        )
        scope_failed = category_scope is None
    if stock_repo is None or scope_failed:
        # "I could not read the shelf" is NOT an EMPTY shelf. {} completes as
        # "nothing was expected -> full count, coverage 100%"; None completes
        # as coverage UNKNOWN and never a clean day-end (see `coverage`).
        system_quantities = None
    elif category_scope is not None and not category_scope:
        # A category with no products expects nothing -- a real, empty scope.
        # Skip the aggregation rather than run it store-wide.
        pass
    else:
        pipeline = [
            {"$match": _countable_match(active_store, category_scope)},
            _COUNTABLE_GROUP,
        ]
        for r in stock_repo.aggregate(pipeline):
            system_quantities[r["_id"]] = r["qty"]
            # Absent only if the engine did not return the unit ids; the
            # completion check then falls back to the quantity rather than
            # flagging every line as moved.
            if "units" in r:
                system_unit_fingerprints[str(r["_id"])] = _unit_fingerprint(
                    r.get("units")
                )

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
        "system_unit_fingerprints": system_unit_fingerprints,
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

    # Remove _id if present. The stored doc above keeps the snapshot; the
    # RESPONSE goes to the person about to count, so it is blind (the strip
    # runs after insert_one, which serialized its own copy).
    count_doc.pop("_id", None)
    return _withhold_expected_while_open(count_doc)


def _product_costs(db, product_ids: List[str]) -> Dict[str, float]:
    """Unit cost per product, for putting a rupee figure on a count variance.

    ``products.cost_price`` is the moving-average purchase cost the rest of the
    app values stock at (reports, reorder economics), with ``landed_cost`` as
    the fallback the reorder engine also uses. A product with neither is
    reported as 0.0 and COUNTED as un-costed by the caller, so a rupee total
    is never quietly understated into looking like "no loss".

    Products are keyed by ``_id``; a few writers also carry ``product_id``, so
    both are matched and both are mapped.
    """
    costs: Dict[str, float] = {}
    ids = [pid for pid in product_ids if pid]
    if not ids:
        return costs
    cursor = db.get_collection("products").find(
        {"$or": [{"_id": {"$in": ids}}, {"product_id": {"$in": ids}}]},
        {"_id": 1, "product_id": 1, "cost_price": 1, "landed_cost": 1},
    )
    for p in cursor:
        cost = float(p.get("cost_price") or p.get("landed_cost") or 0.0)
        for key in (p.get("_id"), p.get("product_id")):
            if key:
                costs[str(key)] = cost
    return costs


# ---------------------------------------------------------------------------
# ONE SNAPSHOT MECHANISM for every physical count in the building.
#
# A count decides "did this line move while the session was open?" by
# comparing the picture taken when the session opened against the picture
# now. Both pictures must therefore be taken the SAME way -- one serialized
# row == one unit, on hand per `_countable_match` -- and both must record
# not only HOW MANY units were on hand but WHICH ONES.
#
# Why WHICH ones: quantities cancel. One frame sells at the till at 10:15 and
# one is received into the stockroom at 11:00; the total is unchanged, the
# line reads as untouched, the counter's honest shelf count of 2 is banked as
# shrinkage, and the write-off destroys a frame the shop still owns. A set of
# unit identities cannot cancel like that.
#
# Deliberately NO timestamps anywhere in this comparison: the till stamps
# `sold_at` with a local-naive datetime.now() while the count stamps a UTC ISO
# string, and netting those against each other is the mixed-clock trap
# (BUG-104). Comparing sets answers the same question without reading a clock.
# ---------------------------------------------------------------------------


def _countable_match(
    store_id: str, product_ids: Optional[List[str]] = None
) -> Dict[str, Any]:
    """The $match EVERY count snapshot uses -- the opening snapshot, the
    coverage scope, the live re-read at completion, and the blind count's
    expected on-hand. product_ids=None means the whole store.

    "On hand for a count" is not a second list of statuses: it is the shared
    `_on_hand_status_clause` with RESERVED switched on (a reserved unit is not
    sellable but it IS standing on this shop's shelf). One definition, one
    named difference -- see the block comment above `_on_hand_status_clause`.
    """
    match: Dict[str, Any] = {
        "store_id": store_id,
        **_on_hand_status_clause(include_reserved=True),
    }
    if product_ids is not None:
        match["product_id"] = {"$in": list(product_ids)}
    return match


# HOW MANY units, and WHICH ones. Both call sites (the opening snapshot and
# the re-read at completion) share this stage so the two pictures compare.
# ponytail: the unit ids come back to Python and are hashed away immediately.
# At six shops that is a few thousand ids once per count; if a store ever grows
# past that, hash them inside the pipeline instead.
_COUNTABLE_GROUP = {
    "$group": {
        "_id": "$product_id",
        "qty": {"$sum": 1},
        "units": {"$addToSet": "$_id"},
    }
}


def _unit_fingerprint(unit_ids) -> str:
    """A short, stable fingerprint of WHICH units were on hand.

    Order-independent (it is a set) and cheap to store beside the quantity, so
    the count document does not have to carry every unit id. Two different
    sets of units effectively never share a fingerprint; two identical sets
    always do.
    """
    joined = "|".join(sorted(str(u) for u in (unit_ids or [])))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def _category_product_ids(
    db, category: str, product_repo=None
) -> Optional[List[str]]:
    """Every active product_id in `category` -- the ONE resolver both count
    doors use (the cycle count's opening snapshot and the scope snapshot; it
    may not be re-typed in either).

    The cycle-count door passes its injected product repository (the same
    data door it uses for every other product read); db-only callers (the
    blind scope snapshot) fall back to the raw collection. One rule, two
    transports over the same collection.

    None = the lookup FAILED, which is "unanswerable", never "the whole
    store": a category count that cannot resolve its category must not
    silently widen to everything. [] is a real answer -- a category with no
    products expects nothing."""
    try:
        if product_repo is not None:
            rows = product_repo.find_many(
                {"category": category, "is_active": True}, limit=5000
            )
        elif db is not None:
            rows = db.get_collection("products").find(
                {"category": category, "is_active": True},
                {"_id": 1, "product_id": 1},
            )
        else:
            return None
        return [
            str(p.get("product_id") or p.get("_id") or "")
            for p in (rows or [])
            if p.get("product_id") or p.get("_id")
        ]
    except Exception as exc:  # noqa: BLE001
        logger.warning("[INVENTORY] category product lookup failed: %s", exc)
        return None


def _scoped_product_ids(
    db, store_id: str, category: Optional[str] = None
) -> Optional[List[str]]:
    """Every product this store is expected to have on hand right now
    (optionally narrowed to one category) -- the SET a count is judged
    complete against.

    Snapshotting this when a session OPENS is what makes "how much of the
    shelf was actually walked" answerable later. Without it, counting 1
    product out of 400 reports a clean shelf.

    Returns None when the question CANNOT be answered (no store behind it, a
    failed read) -- which is not the same as an empty shelf. An empty list
    would tell the count "nothing was expected, so you walked all of it",
    which is the very lie this exists to stop.
    """
    if db is None or not store_id:
        return None
    product_ids: Optional[List[str]] = None
    if category:
        product_ids = _category_product_ids(db, category)
        if product_ids is None:
            return None
        # A category with no products expects nothing -- it must NOT fall back
        # to counting the whole store.
        if not product_ids:
            return []
    try:
        rows = db.get_collection("stock_units").aggregate(
            [
                {"$match": _countable_match(store_id, product_ids)},
                {"$group": {"_id": "$product_id"}},
            ]
        )
        return sorted({str(r["_id"]) for r in rows if r.get("_id")})
    except Exception as exc:  # noqa: BLE001
        logger.warning("[INVENTORY] scope snapshot failed: %s", exc)
        return None


def _on_hand_now(db, store_id: str, product_ids: List[str]):
    """Live on-hand at this store: (quantities, unit fingerprints) per product,
    taken exactly the way the session's opening snapshot was.

    The quantity says how far off the count is; the fingerprint says whether
    the line moved at all -- see the block comment above for why a quantity
    cannot answer the second question.
    """
    live: Dict[str, int] = {}
    prints: Dict[str, str] = {}
    ids = [pid for pid in product_ids if pid]
    if not ids or db is None:
        return live, prints
    try:
        rows = db.get_collection("stock_units").aggregate(
            [{"$match": _countable_match(store_id, ids)}, _COUNTABLE_GROUP]
        )
        for r in rows:
            pid = str(r["_id"])
            live[pid] = int(r.get("qty", 0) or 0)
            prints[pid] = _unit_fingerprint(r.get("units"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("[INVENTORY] live on-hand check failed: %s", exc)
        return live, prints
    # A product with every unit gone reports no group row at all -- that is a
    # real zero (and an empty set), not "unknown", and it is exactly the
    # movement worth catching.
    for pid in ids:
        live.setdefault(pid, 0)
        prints.setdefault(pid, _unit_fingerprint([]))
    return live, prints


def _expected_lines(db, count_doc: dict) -> List[dict]:
    """The COUNT SHEET: every product this session expects to find, with what
    has been counted against it so far.

    The only wired door used to be the barcode scanner, so a shortage was
    findable only while at least one unit of that style survived on the shelf.
    If the last one has walked, so has its label -- which is exactly the case
    a count exists to find. The sheet gives every expected line a quantity box
    whether or not a unit is left to scan.
    """
    system_quantities = count_doc.get("system_quantities") or {}
    counted = {
        i.get("product_id"): i for i in (count_doc.get("items") or []) if i.get("product_id")
    }
    ids = [pid for pid in list(system_quantities.keys()) + list(counted.keys()) if pid]
    labels: Dict[str, tuple] = {}
    if ids:
        try:
            for p in db.get_collection("products").find(
                {"$or": [{"_id": {"$in": ids}}, {"product_id": {"$in": ids}}]},
                {"_id": 1, "product_id": 1, "sku": 1, "brand": 1, "model": 1, "name": 1},
            ):
                sku = p.get("sku", "") or ""
                name = (
                    p.get("name")
                    or f"{p.get('brand', '')} {p.get('model', '')}".strip()
                    or sku
                    or "Unknown"
                )
                for key in (p.get("_id"), p.get("product_id")):
                    if key:
                        labels[str(key)] = (name, sku)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[INVENTORY] count sheet product lookup failed: %s", exc)

    lines = []
    for pid in dict.fromkeys(list(system_quantities.keys()) + list(counted.keys())):
        name, sku = labels.get(pid, ("Unknown", ""))
        line = counted.get(pid)
        lines.append(
            {
                "product_id": pid,
                "product_name": name,
                "sku": sku,
                "system_quantity": int(system_quantities.get(pid, 0) or 0),
                # None means NOT COUNTED YET. A counted zero is a real answer
                # -- the whole point of the sheet -- so the two must not be
                # collapsed into the same falsy number.
                "counted_quantity": (
                    int(line.get("counted_quantity", 0) or 0) if line else None
                ),
                "counted_at": line.get("counted_at") if line else None,
            }
        )
    lines.sort(key=lambda ln: (ln["product_name"] or "", ln["sku"] or ""))
    return lines


def _withhold_expected_while_open(count_doc: dict) -> dict:
    """OWNER RULING 2026-08-25: a BLIND count is THE day-end.

    While a session is still ``in_progress`` its RESPONSE must not carry what
    the books expect -- not as a column, and not in the body one devtools tab
    away (this repo has shipped that leak twice before). Withheld here, at the
    source, for EVERY reader of an open session: blindness is decided by
    SESSION STATUS alone, deliberately role-blind, because a manager who is
    also the counter would otherwise defeat the control through their own
    screen. The moment the session leaves ``in_progress`` (complete /
    reconcile) the expected figures and variances flow again -- that
    comparison is the whole value of counting.

    Mutates and returns the response dict; the stored document keeps its
    snapshot untouched (callers strip AFTER persistence / on read copies).
    """
    if count_doc.get("status") != "in_progress":
        return count_doc
    count_doc.pop("system_quantities", None)
    count_doc.pop("system_unit_fingerprints", None)
    for line in count_doc.get("expected_lines") or []:
        line.pop("system_quantity", None)
    return count_doc


def _load_open_count(db, count_id: str, current_user: dict) -> dict:
    """The in-progress count session, or the right refusal.

    Shared by the two doors a counted quantity can arrive through (typed line
    and barcode scan) so they can never drift on who may write to which
    session: 404 hides a session belonging to another store, and a session
    that is not in progress is never writable.
    """
    count_doc = db.get_collection("stock_counts").find_one({"count_id": count_id})
    if not count_doc:
        raise HTTPException(status_code=404, detail="Stock count session not found")
    if not can_access_store_scoped(count_doc.get("store_id"), current_user):
        raise HTTPException(status_code=404, detail="Stock count session not found")
    if count_doc.get("status") != "in_progress":
        raise HTTPException(status_code=400, detail="Stock count is not in progress")
    return count_doc


def _upsert_count_item(
    db,
    count_doc: dict,
    *,
    product_id: str,
    product_name: str,
    sku: str,
    counted_quantity: int,
    notes: Optional[str],
    user_id: str,
) -> int:
    """Write one counted quantity onto the session; return the line count.

    Re-counting the same product REPLACES its line (a recount corrects the
    first pass, it does not add to it).

    ponytail: read-modify-write of the whole `items` array, which is what the
    original line-recording endpoint already did -- one counter per session is
    the real-world shape. If two people ever count one session at once, move
    to a positional `$[elem]` update.
    """
    items = list(count_doc.get("items", []) or [])
    now_iso = datetime.utcnow().isoformat()
    line = {
        "product_id": product_id,
        "product_name": product_name or "",
        "sku": sku or "",
        "counted_quantity": int(counted_quantity),
        "notes": notes,
        "counted_at": now_iso,
        "counted_by": user_id,
    }

    for idx, existing in enumerate(items):
        if existing.get("product_id") == product_id:
            items[idx] = {**existing, **line}
            break
    else:
        items.append(line)

    db.get_collection("stock_counts").update_one(
        {"count_id": count_doc["count_id"]},
        {"$set": {"items": items, "items_counted": len(items)}},
    )
    return len(items)
