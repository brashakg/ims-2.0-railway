"""INV-12 barcode lifecycle trace."""

from ._shared import (
    Depends,
    Dict,
    Optional,
    get_current_user,
    logger,
    router,
)
from .helpers import (
    _get_db,
)

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

    try:
        # 1. Stock unit
        su = db.get_collection("stock_units").find_one({"barcode": barcode})
        if su:
            result["stock_unit"] = _scrub(dict(su))
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
                    result["purchase"] = [_scrub(dict(grn))]

            # 3. Audit trail (stock_audit rows keyed on this unit's id)
            if stock_id:
                audit_rows = list(
                    db.get_collection("stock_audit").find(
                        {"stock_id": stock_id}, {"_id": 0}
                    ).sort("at", 1).limit(200)
                )
                result["audit_trail"] = _scrub_list(audit_rows)
        else:
            stock_id = ""

    except Exception as exc:  # noqa: BLE001
        logger.warning("[INV-12] stock_unit lookup failed for barcode %s: %s", barcode, exc)

    try:
        # 4. Sales: orders where an item carries this barcode
        orders = list(
            db.get_collection("orders").find(
                {"$or": [
                    {"items.barcode": barcode},
                    {"order_items.barcode": barcode},
                ]},
                {"_id": 0, "order_number": 1, "created_at": 1, "store_id": 1,
                 "status": 1, "items": 1, "order_items": 1},
            ).sort("created_at", 1).limit(50)
        )
        for order in orders:
            matching = [
                i for i in (order.get("items") or order.get("order_items") or [])
                if i.get("barcode") == barcode
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
        # 5. Transfers: look for the barcode in shipped_stock_ids /
        #    received_stock_ids on each transfer line.  Also check transfer_id
        #    stamped on the stock_unit itself.
        transfer_filter: list = [
            {"items.shipped_stock_ids": barcode},
            {"items.received_stock_ids": barcode},
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
                {"$or": transfer_filter},
                {"_id": 0, "id": 1, "transfer_number": 1, "from_location_name": 1,
                 "to_location_name": 1, "status": 1, "shipped_at": 1, "received_at": 1},
            ).sort("created_at", 1).limit(50)
        )
        result["transfers"] = _scrub_list(transfers)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[INV-12] transfers lookup failed for barcode %s: %s", barcode, exc)

    try:
        # 6. Returns: return lines that reference this barcode or its stock_id
        return_filter: list = [{"items.barcode": barcode}]
        if stock_id:
            return_filter.append({"items.stock_id": stock_id})
        returns = list(
            db.get_collection("returns").find(
                {"$or": return_filter},
                {"_id": 0, "return_number": 1, "created_at": 1, "store_id": 1,
                 "status": 1, "items": 1},
            ).sort("created_at", 1).limit(50)
        )
        result["returns"] = _scrub_list(returns)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[INV-12] returns lookup failed for barcode %s: %s", barcode, exc)

    return result
