"""INV-12 barcode lifecycle trace."""

from ...dependencies import can_access_store_scoped, user_store_scope
from ...services import cost_mask
from ._shared import (
    Depends,
    Dict,
    Optional,
    barcode_svc,
    get_current_user,
    logger,
    router,
)
from .helpers import (
    _get_db,
)
from ...services.cost_mask import mask_cost, mask_purchase

# ============================================================================
# INV-12: BARCODE LIFECYCLE TRACE
# ============================================================================
# Returns the full movement history for a single physical barcode across all
# modules: purchase (GRN) -> stock_units -> sales (orders) -> transfers ->
# returns.  Satisfies SYSTEM_INTENT "Audit Everything" without any new
# collection: it collects existing audit rows + cross-collection joins in one
# call.  Fail-soft: a missing collection returns an empty section rather than
# 500-ing the whole response.


@router.get("/barcode/{barcode}/trace")
async def barcode_lifecycle_trace(
    barcode: str,
    current_user: dict = Depends(get_current_user),
):
    """Return the full movement history for a physical barcode (INV-12).

    Sections returned (each is a list, empty when no records found):
      * stock_unit   - the minted serialized row (who, when, source GRN/PO)
      * purchase     - GRN line that created the unit (if any)
      * sales        - order line(s) the barcode appeared in
      * transfers    - transfer line(s) the barcode was part of (ship+receive)
      * returns      - return line(s) that restocked this barcode
      * audit_trail  - raw stock_audit rows keyed on this stock_unit's id

    Honest empty state: when a barcode is unknown an empty envelope is returned
    (not 404) because the barcode may have been received via a path that did not
    mint a stock_units row yet.

    Shop scope (audit 2026-10-18): a unit outside the caller's shops answers
    exactly as an unknown barcode does, and the sales, returns and transfers
    listed are the caller's shops' only (admins see every shop).
    """
    db = _get_db()
    result: Dict = {
        "barcode": barcode,
        "stock_unit": None,
        "purchase": [],
        "sales": [],
        "transfers": [],
        "returns": [],
        "audit_trail": [],
    }
    if db is None:
        return result

    def _scrub(doc: Optional[dict]) -> Optional[dict]:
        if doc is None:
            return None
        doc.pop("_id", None)
        return doc

    def _scrub_list(docs) -> list:
        return [_scrub(dict(d)) for d in (docs or []) if d]

    # The code as typed finds the unit in any letter case; everything after
    # that is looked up by the unit's OWN code, the one other records carry.
    code = barcode
    stock_id = ""  # a failed unit lookup still lets the sales and returns run
    sold_on = None
    every_shop, my_shops = user_store_scope(current_user)
    shops = sorted(s for s in my_shops if isinstance(s, str) and s)
    if not every_shop and not shops:
        # A login with no shop sees no shop's unit (R4): nothing to look up.
        return result

    def _mine(query: dict, *fields: str) -> dict:
        """`query` held to the caller's shops on any of `fields`."""
        if every_shop:
            return query
        return {"$and": [query, {"$or": [{f: {"$in": shops}} for f in fields]}]}

    try:
        # 1. Stock unit
        su = db.get_collection("stock_units").find_one(barcode_svc.unit_barcode_match(barcode))
        if su and not can_access_store_scoped(su.get("store_id"), current_user):
            su = None  # another shop's unit: the unknown-barcode answer
        if su:
            # Its cost answers the one per-unit cost rule ("product", the
            # mask at the end), as on every read of a unit.
            result["stock_unit"] = _scrub(dict(su))
            code = su.get("barcode") or barcode
            # The till stamps the sale on the UNIT (mark_sold), not the code
            # on the order line.
            sold_on = su.get("order_id")
            stock_id = str(su.get("stock_id") or su.get("stock_unit_id") or su.get("_id") or "")

            # 2. Purchase / GRN origin
            grn_id = su.get("source_id") if su.get("source_type") == "GRN" else None
            if not grn_id:
                grn_id = su.get("grn_id")
            if grn_id:
                grn = db.get_collection("grns").find_one({"grn_id": grn_id})
                if grn is None:
                    # Alternate collection name used by GRN repo
                    grn = db.get_collection("goods_receipt_notes").find_one({"grn_id": grn_id})
                if grn:
                    # The price paid per line (a "Bought without PO" receipt
                    # records it) is the same cost, under the same rule.
                    result["purchase"] = [
                        cost_mask.mask_receipt(_scrub(dict(grn)), current_user)
                    ]

            # 3. Audit trail (stock_audit rows keyed on this unit's id)
            if stock_id:
                audit_rows = list(
                    db.get_collection("stock_audit").find(
                        {"stock_id": stock_id}, {"_id": 0}
                    ).sort("at", 1).limit(200)
                )
                result["audit_trail"] = _scrub_list(audit_rows)

    except Exception as exc:  # noqa: BLE001
        logger.warning("[INV-12] stock_unit lookup failed for barcode %s: %s", barcode, exc)

    try:
        # 4. Sales: the scanned unit on a line, its original barcode, or
        #    the order stamped by a typed-item sale on the unit itself.
        sales_filter: list = [
            {"items.barcode": code},
            {"order_items.barcode": code},
        ]
        if stock_id:
            sales_filter.append({"items.stock_id": stock_id})
        if sold_on:
            sales_filter.append({"order_id": sold_on})
        orders = list(
            db.get_collection("orders").find(
                _mine({"$or": sales_filter}, "store_id"),
                {"_id": 0, "order_number": 1, "created_at": 1, "store_id": 1,
                 "status": 1, "items": 1, "order_items": 1},
            ).sort("created_at", 1).limit(50)
        )
        for order in orders:
            matching = [
                i for i in (order.get("items") or order.get("order_items") or [])
                if i.get("barcode") == code
                or (stock_id and i.get("stock_id") == stock_id)
            ]
            result["sales"].append({
                "order_number": order.get("order_number"),
                "created_at": order.get("created_at"),
                "store_id": order.get("store_id"),
                "status": order.get("status"),
                "matched_lines": matching,
            })
    except Exception as exc:  # noqa: BLE001
        logger.warning("[INV-12] orders lookup failed for barcode %s: %s", barcode, exc)

    try:
        # 5. Transfers: shipped_stock_ids / received_stock_ids on each transfer
        #    line hold the moved units' stock_ids.  Also check transfer_id
        #    stamped on the stock_unit itself.
        transfer_filter: list = [
            {f"items.{field}": v}
            for field in ("shipped_stock_ids", "received_stock_ids")
            for v in (code, stock_id)
            if v
        ]
        # If the stock unit carries a transfer_id, add that as an exact lookup.
        transfer_id_on_unit = None
        if result.get("stock_unit"):
            transfer_id_on_unit = result["stock_unit"].get("transfer_id") or \
                result["stock_unit"].get("source_id") if (
                    result.get("stock_unit", {}) or {}
                ).get("source_type") == "TRANSFER" else None
        if transfer_id_on_unit:
            transfer_filter.append({"id": transfer_id_on_unit})
        transfers = list(
            db.get_collection("stock_transfers").find(
                _mine({"$or": transfer_filter}, "from_location_id", "to_location_id"),
                {"_id": 0, "id": 1, "transfer_number": 1, "from_location_name": 1,
                 "to_location_name": 1, "status": 1, "shipped_at": 1, "received_at": 1},
            ).sort("created_at", 1).limit(50)
        )
        result["transfers"] = _scrub_list(transfers)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[INV-12] transfers lookup failed for barcode %s: %s", barcode, exc)

    try:
        # 6. Returns: return lines that reference this barcode or its stock_id
        return_filter: list = [{"items.barcode": code}]
        if stock_id:
            return_filter.append({"items.stock_id": stock_id})
        returns = list(
            db.get_collection("returns").find(
                _mine({"$or": return_filter}, "store_id"),
                {"_id": 0, "return_number": 1, "created_at": 1, "store_id": 1,
                 "status": 1, "items": 1},
            ).sort("created_at", 1).limit(50)
        )
        result["returns"] = _scrub_list(returns)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[INV-12] returns lookup failed for barcode %s: %s", barcode, exc)

    # The unit's cost, a sale line's cost_at_sale, a transfer line's unit cost
    # and the receipt's prices go to the roles that see what we pay only
    # (owner rulings D7 / 2026-10-12). The receipt is a purchase document: its
    # line prices and totals follow the same mask as GET /vendors/grn/{id}.
    result["purchase"] = mask_purchase(result["purchase"], current_user)
    return mask_cost(result, current_user, "product")
