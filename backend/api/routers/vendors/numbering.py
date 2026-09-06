"""PO/GRN document numbers and the goods-receipt arithmetic helpers."""

from ._shared import Optional, _get_db, datetime, uuid


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================


def _counters_collection():
    """Shared ``counters`` collection for atomic purchase numbering. Fail-soft:
    returns None (DB-less / mock) so the numberers fall back to a timestamp."""
    try:
        db = _get_db()
        return db.get_collection("counters") if db is not None else None
    except Exception:  # noqa: BLE001
        return None


def generate_po_number(store_id: str, store_code: Optional[str] = None) -> str:
    """Allocate the next PO number for a store in its financial year.

    Format ``PO/{store}/{FY}/{serial}`` (e.g. ``PO/BV-BOK-01/26-27/0001``) --
    a consecutive, per-store, per-FY serial via the shared counters collection
    (S5), the same discipline as the GST invoice number. Fail-soft: with no DB
    the service returns a time-derived suffix in the same format."""
    from ...services.purchase_numbering import next_purchase_number

    return next_purchase_number(
        _counters_collection(),
        doc_type="PO",
        store_id=store_id,
        store_code=store_code or store_id,
    )


def generate_grn_number(store_id: str, store_code: Optional[str] = None) -> str:
    """Allocate the next goods-receipt (GRN) number for a store in its FY.

    Format ``RCPT/{store}/{FY}/{serial}``. Atomic per (store, FY) via the shared
    counters collection (S5); fail-soft to a time-derived suffix when DB-less."""
    from ...services.purchase_numbering import next_purchase_number

    return next_purchase_number(
        _counters_collection(),
        doc_type="GRN",
        store_id=store_id,
        store_code=store_code or store_id,
    )


def classify_grn_line_variance(received_qty, ordered_qty, tolerance: int = 0) -> str:
    """Classify a single received line against what was ordered on the PO.

    Returns one of:
      * "UNMATCHED" -- the line could not be matched to a PO line (ordered_qty
        is None), so there is nothing to compare against.
      * "SHORT"     -- received fewer units than ordered (beyond tolerance).
      * "OVER"      -- received more units than ordered (beyond tolerance).
      * "EXACT"     -- received exactly what was ordered (within tolerance).

    Pure + total: garbage/missing numbers coerce to 0 so this never raises.
    Used both to stamp a per-line `variance_status` on the GRN at create time
    and to drive the receiving UI's short/exact/over flags.
    """

    def _int(v) -> int:
        try:
            return int(v or 0)
        except (TypeError, ValueError):
            return 0

    if ordered_qty is None:
        return "UNMATCHED"

    tol = abs(_int(tolerance))
    delta = _int(received_qty) - _int(ordered_qty)
    if delta < -tol:
        return "SHORT"
    if delta > tol:
        return "OVER"
    return "EXACT"


def compute_po_receipt_state(
    po_items, received_by_product: dict, tolerance: int = 0
) -> str:
    """Decide whether a PO is fully or partially received.

    Compares the cumulative received quantity per product (summed across every
    ACCEPTED GRN for the PO) against the ordered quantity on each PO line.

    Returns "RECEIVED" when every ordered line has been received in full (or
    over-received) within tolerance, otherwise "PARTIALLY_RECEIVED". A PO with
    no line items resolves to "RECEIVED" (nothing left to receive).

    Pure + total: bad fields coerce to 0; never raises. This is the core
    partial-vs-full decision and is unit-tested without a database.
    """

    def _int(v) -> int:
        try:
            return int(v or 0)
        except (TypeError, ValueError):
            return 0

    tol = abs(_int(tolerance))
    items = po_items if isinstance(po_items, list) else []
    received_by_product = received_by_product or {}

    # Roll the ordered quantity up per product so multiple PO lines for the same
    # product are compared against the combined received count.
    ordered_by_product: dict = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        pid = item.get("product_id")
        if pid is None:
            continue
        ordered_by_product[pid] = ordered_by_product.get(pid, 0) + _int(
            item.get("quantity")
        )

    if not ordered_by_product:
        return "RECEIVED"

    for pid, ordered in ordered_by_product.items():
        received = _int(received_by_product.get(pid))
        if received < ordered - tol:
            return "PARTIALLY_RECEIVED"

    return "RECEIVED"


def grn_has_discrepancy(grn: dict, qty_tolerance: int = 0) -> bool:
    """True if a goods-receipt note shows a receiving variance worth a task.

    A discrepancy is any of:
      * a line with rejected_qty > 0 (goods sent back as defective/wrong), or
      * a line whose received_qty differs from its ordered_qty beyond
        qty_tolerance (short or over shipment), where ordered_qty is matched
        from the PO and stamped onto the line, or
      * a top-level total_received != total_ordered beyond qty_tolerance.

    Pure and total: missing/garbage fields coerce to 0 so a malformed GRN never
    raises here (the caller is fail-soft regardless). Only line-level signals are
    used when present; the total check is a backstop for callers that pass totals
    but not per-line ordered quantities.
    """
    if not isinstance(grn, dict):
        return False

    def _int(v) -> int:
        try:
            return int(v or 0)
        except (TypeError, ValueError):
            return 0

    tol = abs(_int(qty_tolerance))
    items = grn.get("items") if isinstance(grn.get("items"), list) else []

    for item in items:
        if not isinstance(item, dict):
            continue
        if _int(item.get("rejected_qty")) > 0:
            return True
        if "ordered_qty" in item:
            if (
                abs(_int(item.get("received_qty")) - _int(item.get("ordered_qty")))
                > tol
            ):
                return True

    if grn.get("total_ordered") is not None:
        if abs(_int(grn.get("total_received")) - _int(grn.get("total_ordered"))) > tol:
            return True

    return False


def _grn_barcode(store_id: Optional[str], product_id: Optional[str]) -> str:
    """Generate a barcode for a GRN-minted serialized unit.

    Reuses inventory.generate_barcode (the canonical stock-barcode format) so a
    unit received via GRN is indistinguishable from one added via the inventory
    /stock/add screen. Fail-soft: if that helper can't be imported for any
    reason, fall back to a uuid-derived barcode so the stock write still
    succeeds (a missing barcode must never block receiving goods).
    """
    try:
        from ..inventory import generate_barcode

        return generate_barcode(store_id, product_id)
    except Exception:  # noqa: BLE001
        return f"BC-{uuid.uuid4().hex[:12].upper()}"


def _grn_stock_audit(
    stock_id: str,
    new_status: str,
    grn_id: str,
    po_id: Optional[str],
    store_id: Optional[str],
    user_id: Optional[str],
) -> None:
    """Cheap, fail-soft insert into the stock_audit collection for every unit
    minted while posting a GRN. Mirrors the returns-restock audit shape so the
    audit trail answers "which unit entered stock when, and from which GRN/PO".

    Any error is swallowed -- the audit row must never break (or roll back) the
    stock write that already happened. This is the Fail-Loudly-but-not-here
    boundary: losing an audit row is acceptable; losing received stock is not.
    """
    if not stock_id:
        return
    try:
        from ...dependencies import get_db

        db = get_db()
        if db is None or not getattr(db, "is_connected", False):
            return
        coll = db.db.get_collection("stock_audit")
        if coll is None:
            return
        coll.insert_one(
            {
                "stock_id": str(stock_id),
                "prior_status": None,
                "new_status": new_status,
                "source": "GRN_RECEIPT",
                "grn_id": grn_id,
                "po_id": po_id,
                "store_id": store_id,
                "by_user": user_id,
                "at": datetime.now().isoformat(),
            }
        )
    except Exception:  # noqa: BLE001
        pass


def _cumulative_received_by_product(grn_repo, po_id: str) -> dict:
    """Sum accepted_qty per product across every ACCEPTED GRN for a PO.

    This is the running on-hand-received tally used to decide whether the PO is
    now fully or partially received. Fail-soft: any read error returns {} so the
    caller degrades to "partial" rather than crashing the accept.
    """
    totals: dict = {}
    if grn_repo is None or not po_id:
        return totals
    try:
        accepted_grns = grn_repo.find_many(
            {"po_id": po_id, "status": "ACCEPTED"}, limit=1000
        )
    except Exception:  # noqa: BLE001
        return totals
    for grn in accepted_grns or []:
        if not isinstance(grn, dict):
            continue
        for item in grn.get("items", []) or []:
            if not isinstance(item, dict):
                continue
            pid = item.get("product_id")
            if pid is None:
                continue
            try:
                totals[pid] = totals.get(pid, 0) + int(item.get("accepted_qty", 0) or 0)
            except (TypeError, ValueError):
                continue
    return totals
