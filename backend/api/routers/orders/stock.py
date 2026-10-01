"""Product resolution and serialized-unit stock: availability assertions,
lens reservation keys and the sell-side unit marking.

Moved verbatim out of the 6,649-line api/routers/orders.py (Wave 5 package
split): no path, method, dependency, status code, response_model, default,
rounding or validation was changed.
"""

from fastapi import HTTPException
from typing import Dict, List, Optional
from ...dependencies import get_stock_repository
from ._shared import logger


def _resolve_product_doc(product_repo, pid: str):
    """Tolerant product existence lookup for order-create.

    Imported products are often referenced by their Mongo `_id` or `sku`
    rather than `product_id` (the same tolerance the admin catalog uses).
    Tries product_id, then sku, then _id (string or ObjectId). Returns the
    product doc or None. Existence only — pricing comes from the order payload.
    """
    if not pid:
        return None
    # None-safe: the live seeded-catalog path calls this with product_repo=None
    # (products live in catalog_products, resolved below). Only touch the products
    # repo when it is actually present.
    if product_repo is not None:
        product = product_repo.find_by_id(pid)
        if product is not None:
            return product
        try:
            coll = product_repo.collection
            product = coll.find_one(
                {"$or": [{"product_id": pid}, {"sku": pid}, {"_id": pid}]}
            )
            if product is None and len(pid) == 24:
                from bson import ObjectId

                try:
                    product = coll.find_one({"_id": ObjectId(pid)})
                except Exception:
                    product = None
            if product is not None:
                return product
        except Exception:
            product = None
    # C-1: seeded catalog products live in the `catalog_products` collection
    # (served by GET /catalog/products), NOT in `products`. When the lookup
    # above misses, fall back to catalog_products by the same id so a
    # catalog-only product can still be ordered. Fail-soft: any error -> None.
    return _resolve_catalog_product_doc(pid)


def _canonical_pid(product_repo, pid: str) -> str:
    """NEW-ORDER-PRODUCTID-STAMP: resolve a client-supplied product reference
    (which may be a SKU or a Mongo _id, e.g. for imported/catalog products) to
    the catalog's CANONICAL product_id, so persisted order lines reconcile
    against the catalog instead of storing a raw SKU. Virtual skus
    (custom-/lens-/lens-sug-) and unresolvable ids are returned unchanged."""
    if not pid or pid.startswith(("custom-", "lens-", "lens-sug-")):
        return pid
    try:
        doc = _resolve_product_doc(product_repo, pid)
    except Exception:  # noqa: BLE001 -- resolution is best-effort
        doc = None
    if not doc:
        return pid
    return str(doc.get("id") or doc.get("product_id") or pid)


def _resolve_billable_product(product_repo, pid: str, product_name: str = ""):
    """THE product resolution for a billing door -- create_order AND
    POST /{order_id}/items. One lookup, one refusal policy:

      * empty pids and virtual lines (custom-/lens-/lens-sug-) -> None
        (no product doc; the payload prices itself, role cap still applies);
      * a pid that resolves from the `products` spine (by product_id, sku,
        or _id -- the tolerant _resolve_product_doc) -> the doc;
      * a pid that does NOT resolve -> 400. Never silently bill an unknown
        product: the add-item door used to fail OPEN here, appending the line
        with the MRP ceiling, cost floor, HQ-offer rule, category/brand cap
        and reason requirement ALL silently skipped;
      * a pid that resolves ONLY from catalog_products -> 400 (no billing
        spine row; the same refusal create_order has always made -- the
        add-item door billed these outright).

    The add-item door previously resolved with a NARROWER lookup
    (find_by_id only), so a product referenced by SKU or Mongo _id missed
    every guard. That copy is deleted; both doors resolve HERE.
    """
    if not pid or pid.startswith(_VIRTUAL_PID_PREFIXES):
        return None
    product = _resolve_product_doc(product_repo, pid)
    if product is None:
        raise HTTPException(
            status_code=400,
            detail=f"Product not found: {pid} ({product_name or 'unknown'})",
        )
    # Products-convergence (3): billing requires the `products` SPINE. A
    # product that resolves ONLY from catalog_products is not a governed
    # billing master -- fail LOUD instead of silently billing off the catalog
    # (the path the discount-cap could not fully enforce).
    if product.get("_resolved_from") == "catalog_products":
        raise HTTPException(
            status_code=400,
            detail=(
                f"{product_name or pid} exists only in the catalog "
                f"and has no billing master record. Re-save it via "
                f"Catalog / Add Product before selling it."
            ),
        )
    return product


def _get_catalog_collection():
    """Return the `catalog_products` Mongo collection, or None if the DB is
    unavailable. Mirrors catalog.py's accessor; module-level + import-light so
    tests can monkeypatch it. Fail-soft."""
    try:
        from ...dependencies import get_db

        db = get_db()
        if db is not None and getattr(db, "is_connected", False):
            return db.get_collection("catalog_products")
    except Exception:  # noqa: BLE001
        pass
    return None


def _resolve_catalog_product_doc(pid: str):
    """C-1 fallback: look a product up in `catalog_products` by id/sku and map
    its (nested) shape to the flat fields the order-create path reads
    (product_id, name, mrp/offer_price/cost_price, category, gst_rate/hsn_code,
    discount_category, item_type). Returns the mapped dict or None. Fail-soft.

    The catalog doc stores pricing under a nested `pricing` block and the name
    under `title`; the order path reads flat `cost_price` (COGS snapshot) and
    `discount_category` (category-cap), so we surface those at the top level.
    """
    if not pid:
        return None
    coll = _get_catalog_collection()
    if coll is None:
        return None
    try:
        doc = coll.find_one({"$or": [{"id": pid}, {"sku": pid}, {"_id": pid}]})
    except Exception:  # noqa: BLE001
        return None
    if not doc:
        return None
    pricing = doc.get("pricing") or {}
    category = doc.get("category")
    mapped = {
        "product_id": doc.get("id") or pid,
        "id": doc.get("id") or pid,
        "sku": doc.get("sku"),
        "name": doc.get("title") or doc.get("name"),
        "model": doc.get("title") or doc.get("name"),
        "category": category,
        "item_type": category,
        "hsn_code": doc.get("hsn_code"),
        "gst_rate": doc.get("gst_rate"),
        "mrp": pricing.get("mrp"),
        "offer_price": pricing.get("offer_price"),
        "cost_price": pricing.get("cost_price"),
        "discount_category": pricing.get("discount_category"),
        # brand is needed for the luxury-brand discount cap (Cartier 2% etc.).
        # The catalog doc may hold it flat or under pricing; surface either.
        "brand": doc.get("brand") or pricing.get("brand"),
        "is_active": doc.get("is_active", True),
        # Mark the source so any future caller can tell a catalog-resolved
        # product from a `products` one without re-querying.
        "_resolved_from": "catalog_products",
    }
    return mapped


# Item types / product_id prefixes that have NO serialized stock to mark sold.
# SERVICE = labour line (eg fitting); EYE_TEST/etc = a clinical consultation line
# (GST-exempt SAC 9993, see gst_rates.py) -- a service, not a stocked good;
# custom-/lens-/lens-sug- = virtual POS items the configurator/suggestion helper
# generates on the fly. These never carry a stock_unit row, so trying to
# mark_sold them is a no-op (not an error).
_VIRTUAL_PID_PREFIXES = ("custom-", "lens-", "lens-sug-")
_NON_SERIALIZED_ITEM_TYPES = {
    "SERVICE",
    "EYE_TEST",
    "EYE_EXAM",
    "EYE_CHECKUP",
    "CONSULT",
    "CONSULTATION",
    "OPTOMETRY",
}

# Item types whose stock is reserved by the LENS hook (reserve_for_order_item),
# NOT by the serialized stock_units availability gate below.
_LENS_RESERVED_ITEM_TYPES = {"LENS"}


def _takes_serialized_stock(line: dict) -> bool:
    """Does this order line consume a SERIALIZED stock_units row?

    THE single predicate for that question. It previously existed three times,
    written three slightly different ways, and they drifted: the availability
    assert and the line-release both excluded LENS lines while _mark_units_sold
    did NOT. POSLayout maps OPTICAL_LENS to item_type 'LENS' and sends the REAL
    catalog product_id, so a stock-lens line had its unit flipped SOLD on the
    way in, was never availability-checked, and was never released when the line
    was removed -- stranded permanently once the order reached DELIVERED.

    Reserved-by-the-lens-hook lines and non-stocked service lines are excluded;
    so are virtual POS ids that have no stock_units row at all.
    """
    if not isinstance(line, dict):
        return False
    item_type = (line.get("item_type") or "").upper()
    if (
        item_type in _NON_SERIALIZED_ITEM_TYPES
        or item_type in _LENS_RESERVED_ITEM_TYPES
    ):
        return False
    pid = line.get("product_id") or ""
    if not pid or pid.startswith(_VIRTUAL_PID_PREFIXES):
        return False
    return True


def _assert_serialized_stock_available(
    items_data: List[dict], store_id: Optional[str]
) -> None:
    """BUG-097: reject order creation (409) when a SERIALIZED non-lens line does
    not have enough AVAILABLE units in stock_units -- closes the non-lens oversell
    where _mark_units_sold silently continued on 0 available.

    Only enforced for a product that IS serialized-tracked at this store
    (count(any status) > 0); a product with no stock_units row (a service, a
    virtual item, or simply not unit-tracked) is left UNAFFECTED so a legit sale is
    never false-blocked. Lens lines are gated by the lens reserve.

    F7: a line carrying an EXPLICIT stock_id (barcode-scan flow) used to be
    skipped entirely here and then handed to an UNGUARDED mark_sold -- so
    scanning a unit that was already SOLD / TRANSFERRED / QUARANTINED / DAMAGED
    silently "succeeded" and overwrote that unit's sale lineage. Those lines are
    now validated HERE, pre-persist, so the POS gets a real, readable 409 instead
    of a silent corruption.

    NOTE: this is a pre-persist availability ASSERT -- a strict improvement over
    the silent oversell, but check-then-act, so two highly-concurrent orders for
    the last unit can still both pass. The atomic guards (claim_one_available /
    the now-guarded mark_sold) are what actually make the WRITE safe.
    """
    if not store_id or not items_data:
        return
    try:
        stock_repo = get_stock_repository()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[STOCK] availability assert: repo unavailable: %s", exc)
        stock_repo = None
    if stock_repo is None:
        return  # no stock backend -> fail-soft (pre-existing behaviour)

    need: Dict[str, int] = {}
    explicit_units: List[tuple] = []
    for line in items_data:
        if not _takes_serialized_stock(line):
            continue
        pid = line.get("product_id") or ""
        sid = line.get("stock_id")
        if sid:
            explicit_units.append((str(sid), pid, line.get("product_name") or pid))
            continue  # explicit unit -> validated by _assert_explicit_unit_sellable
        need[pid] = need.get(pid, 0) + int(line.get("quantity") or 1)

    for sid, pid, label in explicit_units:
        _assert_explicit_unit_sellable(stock_repo, sid, pid, label, store_id)

    for pid, qty in need.items():
        try:
            tracked = stock_repo.count({"product_id": pid, "store_id": store_id})
        except Exception:  # noqa: BLE001
            tracked = 0
        if not tracked:
            continue  # not serialized-tracked here -> never false-block a sale
        try:
            avail = stock_repo.find_available(pid, store_id)
        except Exception:  # noqa: BLE001
            continue  # availability lookup failed -> fail-soft
        if avail < qty:
            # F2: when the shortfall is caused by the EXPIRY FLOOR, say so --
            # "0 available" on a shelf with 6 visible boxes is not an actionable
            # message. Fail-soft: an unsupported/old repo just omits the hint.
            expired = 0
            try:
                counter = getattr(stock_repo, "count_expired", None)
                if callable(counter):
                    expired = int(counter(pid, store_id) or 0)
            except Exception:  # noqa: BLE001
                expired = 0
            hint = ""
            if expired > 0:
                logger.warning(
                    "[STOCK] %s expired unit(s) held back from sale for %s @ %s",
                    expired,
                    pid,
                    store_id,
                )
                hint = (
                    f" {expired} unit(s) are PAST THEIR EXPIRY DATE and cannot "
                    f"be sold -- quarantine them."
                )
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Insufficient stock for '{pid}': {avail} available at this "
                    f"store, {qty} requested. Cannot oversell.{hint}"
                ),
            )


def _assert_explicit_unit_sellable(
    stock_repo,
    stock_id: str,
    product_id: str,
    label: str,
    store_id: str,
) -> None:
    """F7: pre-persist gate for the barcode-scan path -- the SPECIFIC unit the
    POS named must actually be sellable HERE and NOW, or the sale is refused
    with a message a shop-floor user can act on.

    Fail-soft on infrastructure (unit not found / lookup error) so a stock-data
    gap can never block revenue; fail-LOUD on a real conflict (already sold,
    transferred out, quarantined/damaged, wrong store, expired). The atomic
    guard inside StockRepository.mark_sold is the authoritative write-side
    check -- this exists so the user sees WHY.
    """
    try:
        unit = stock_repo.find_by_id(stock_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[STOCK] explicit unit %s lookup failed: %s", stock_id, exc)
        return
    if not unit:
        # Unknown id -> the FIFO path can still serve this line; don't block.
        logger.warning(
            "[STOCK] explicit stock_id %s not found (product %s) -- not blocking",
            stock_id,
            product_id,
        )
        return

    status = str(unit.get("status") or "").upper()
    if status != "AVAILABLE":
        raise HTTPException(
            status_code=409,
            detail=(
                f"'{label}' (unit {stock_id}) cannot be sold: this unit is "
                f"{status or 'in an unknown state'}, not available. Scan a "
                f"different unit."
            ),
        )

    unit_store = unit.get("store_id")
    if unit_store and store_id and unit_store != store_id:
        raise HTTPException(
            status_code=409,
            detail=(
                f"'{label}' (unit {stock_id}) belongs to store {unit_store}, "
                f"not this store. It cannot be sold here."
            ),
        )

    # A product-id mismatch is LOGGED, never blocked. product_id canonicalisation
    # (the products vs catalog_products convergence) means an older stock_units
    # row can legitimately carry the pre-canonical id for the same physical item;
    # 409-ing on that would false-block real scans at the counter. The unit's own
    # status/store/expiry -- checked above -- are the safety-relevant facts.
    unit_pid = unit.get("product_id")
    if unit_pid and product_id and unit_pid != product_id:
        logger.warning(
            "[STOCK] scanned unit %s is product %s but the line says %s "
            "(id canonicalisation drift?) -- selling the scanned unit",
            stock_id,
            unit_pid,
            product_id,
        )

    # F2 (patient safety): an in-date check for the scan path too -- FEFO only
    # governs auto-allocation, so without this a staffer could still scan an
    # expired contact-lens box straight through the till. Only DATED units are
    # affected; a frame has no expiry_date and is never touched.
    #
    # ONLY a canonical ISO date may block the sale (panel must-fix 8). A raw
    # string compare is lexicographic, not chronological: "15-08-2027" is a
    # VALID FUTURE date that sorts below today and would have blocked real
    # in-date stock, while "31/12/2025" is genuinely expired and would have
    # sailed through. Anything we cannot parse with certainty is SOLD with a
    # warning -- the fail-soft direction must be "never false-block the
    # counter", consistently. The durable fix is normalising at the GRN door.
    expiry = unit.get("expiry_date")
    if expiry not in (None, ""):
        try:
            from database.repositories.product_repository import StockRepository
            from ...utils.ist import ist_today

            if not StockRepository.is_iso_expiry(expiry):
                logger.warning(
                    "[STOCK] unit %s has an UNPARSEABLE expiry_date %r -- selling "
                    "it (fail-open); normalise this value at the GRN door",
                    stock_id,
                    expiry,
                )
            elif expiry[:10] < ist_today().isoformat():
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"'{label}' (unit {stock_id}) EXPIRED on {expiry[:10]} "
                        f"and cannot be sold. Quarantine this unit."
                    ),
                )
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001 -- clock/parse issue: never block
            logger.warning("[STOCK] expiry check skipped for %s: %s", stock_id, exc)


def _lens_reservation_key(line: dict, fallback_position: int):
    """The lens-reservation key component for one order line.

    The lens hook builds its idempotency / release id as
    "{order_id}#{line_index}", so whatever we pass as `line_index` IS the key.

    It must be UNIQUE-FOR-EVER within the order and STABLE across edits:
      * POSITION is not stable -- removing a line shifts every later line, so a
        cancel would release someone else's cell and leak this line's;
      * a monotonic max+1 counter is not unique-for-ever either -- deleting the
        HIGHEST line makes the next append REUSE that number, and the hook then
        short-circuits on the deleted line's stale audit row, so the new line
        reserves NOTHING (no 409 even at zero stock). That was panel must-fix 7.

    `item_id` is a uuid4 minted once when the line is created and never reused,
    so it is the only correct key. Legacy lines written before this change have
    no usable item_id and fall back to their POSITION -- byte-identical to the
    pre-change behaviour for those orders.
    """
    try:
        item_id = (line or {}).get("item_id")
    except AttributeError:
        return fallback_position
    if isinstance(item_id, str) and item_id.strip():
        return item_id.strip()
    return fallback_position


def _legacy_lens_reservation_key(line: dict, fallback_position: int):
    """The key a line's reservation MAY have been written under before the
    item_id switch: its persisted `line_index`, else its position.

    Release paths try this as well as the item_id key, so a cell reserved by the
    older code is still released instead of leaking forever. Returns None when
    it would duplicate the primary key (nothing extra to try).
    """
    raw = (line or {}).get("line_index") if isinstance(line, dict) else None
    if isinstance(raw, bool) or raw is None:
        legacy = fallback_position
    else:
        try:
            legacy = int(raw)
        except (TypeError, ValueError):
            legacy = fallback_position
    if legacy == _lens_reservation_key(line, fallback_position):
        return None
    return legacy


def _mark_units_sold(
    order_id: str,
    items_data: List[dict],
    store_id: Optional[str],
) -> List[str]:
    """For each serialized item on a created order, flip its stock_unit row to
    SOLD with the order_id stamped on it. Returns the list of stock_ids marked.

    Two paths:
      1. Item carries an explicit stock_id (POS knew the unit; barcode-scan
         flow). Just call mark_sold(stock_id, order_id).
      2. No stock_id but a real product_id + store_id. FIFO-allocate the first
         AVAILABLE unit via find_by_product_store and mark THAT sold.

    Virtual items (SERVICE / custom-/ lens-/ lens-sug-) and items without a
    product_id are skipped silently - they have no serialized row to mark.

    Fail-soft: any lookup or write failure is logged. Order creation must
    NEVER be blocked by stock-side issues; if we can't mark a unit, the
    returns flow will fall back to the no-order-id path (any non-AVAILABLE
    unit for that product+store).
    """
    if not order_id or not items_data:
        return []
    try:
        stock_repo = get_stock_repository()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[STOCK] mark_sold: stock repo unavailable: %s", exc)
        return []
    if stock_repo is None:
        return []

    marked: List[str] = []
    # Track stock_ids consumed within this order so two lines for the same
    # product_id don't grab the same unit twice.
    used: set = set()

    for line in items_data or []:
        # ONE shared predicate with the availability assert and the line
        # release. This used to omit the LENS exclusion the other two applied,
        # so a stock-lens line was marked SOLD here but never checked and never
        # released -- the drift the shared predicate exists to prevent.
        if not _takes_serialized_stock(line):
            continue
        pid = line.get("product_id") or ""
        qty = int(line.get("quantity") or 1)
        if qty < 1:
            continue

        explicit_sid = line.get("stock_id")
        for _ in range(qty):
            sid: Optional[str] = None
            if explicit_sid and explicit_sid not in used:
                # Path 1: POS told us exactly which unit.
                try:
                    ok = stock_repo.mark_sold(explicit_sid, order_id)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "[STOCK] mark_sold(stock_id=%s) failed: %s",
                        explicit_sid,
                        exc,
                    )
                    ok = False
                if ok:
                    sid = explicit_sid
                else:
                    # F7: mark_sold is now an ATOMIC guarded write -- False means
                    # the unit was NOT AVAILABLE (already sold / transferred /
                    # quarantined / expired) and NOTHING was written, so a prior
                    # sale's lineage is intact. The pre-persist gate
                    # (_assert_explicit_unit_sellable) normally catches this, so
                    # reaching here means we lost a real race. We deliberately do
                    # NOT silently substitute a different unit: the scanned
                    # barcode IS the physical item handed over, and swapping in
                    # another serial would corrupt warranty-by-serial lineage.
                    logger.error(
                        "[STOCK] SCANNED UNIT NOT SELLABLE: stock_id=%s product=%s "
                        "store=%s order=%s -- unit left untouched, this line did "
                        "NOT decrement stock. Reconcile manually.",
                        explicit_sid,
                        pid,
                        store_id,
                        order_id,
                    )
                # Only consume the explicit stock_id once per line; the
                # remaining qty falls through to FIFO.
                explicit_sid = None
            else:
                # Path 2: FIFO-allocate from product+store via an ATOMIC claim
                # (find_one_and_update on status=AVAILABLE). Closes the prior
                # check-then-act race where two concurrent last-unit sales both
                # picked AND marked the SAME unit SOLD. `used` excludes units
                # already claimed earlier in THIS order (two lines, same product).
                if not store_id:
                    continue
                try:
                    claimed = stock_repo.claim_one_available(
                        pid, store_id, order_id, used
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "[STOCK] claim_one_available(%s,%s) failed: %s",
                        pid,
                        store_id,
                        exc,
                    )
                    claimed = None
                if claimed:
                    sid = str(claimed)
                else:
                    # No AVAILABLE unit to claim: genuinely out of stock (the
                    # pre-persist assert normally catches this) or we lost a
                    # concurrent race for the last unit. Log for ops visibility.
                    logger.warning(
                        "[STOCK] no AVAILABLE unit to claim for %s @ %s (order %s) "
                        "-- possible concurrent sale of the last unit",
                        pid,
                        store_id,
                        order_id,
                    )

            if sid:
                used.add(sid)
                marked.append(sid)
                # E3w-DEFERRED: POS-sell ledger emit needs POS sign-off. The
                # AVAILABLE -> SOLD item_events emit for this revenue-critical
                # path is intentionally NOT wired here; it is owner-gated to a
                # separate item (and the /items/{id}/sell route gates it behind
                # FF_E3_POS_SELL). Do NOT add a record_event call here.

    return marked
