"""THE one rule for which goods receipt a stock piece came in on, read both
ways: origin_filters (receipt -> its pieces, wherever they are now: the
receipt counts in vendors/grn_accept_lock, the purchases report) and
receipt_of (piece -> its receipt: the average cost, its backfill).

The immutable grn_id the mint stamps is authoritative. A piece minted before
that stamp (#1165) has none: its GRN source names the receipt, or -- once a
stock transfer has rewritten its source (transfers._rehome) -- the receipt's
grn_number (stamped at mint since #310, never rewritten), but only while one
receipt alone carries that number (before #711 shops shared a series).
"""

from typing import Optional

# Statuses that mean "this receipt HAS been accepted" (vendors/grn_accept_lock,
# grn_void, cockpit, the average-cost backfill). Lives here, not in a router,
# so a one-off data script can read it without the app's login secret.
GRN_TERMINAL_ACCEPT_STATUSES = ("ACCEPTED", "PARTIALLY_ACCEPTED")


def number_is_unique(grns, number) -> bool:
    """Exactly one receipt in `grns` (the collection) carries `number`. Raises
    on a read error: each caller decides how to fail."""
    return int(grns.count_documents({"grn_number": number}) or 0) == 1


def origin_filters(grn: dict, number_unique: bool, **match) -> list:
    """Disjoint filters for the pieces receipt `grn` minted (narrowed by
    `match`); `number_unique`: number_is_unique for its grn_number."""
    grn_id = grn.get("grn_id")
    origin = [{"source_type": "GRN", "source_id": grn_id}]
    if grn.get("grn_number") and number_unique:
        origin.append({"grn_number": grn["grn_number"]})
    return [
        {**match, "grn_id": grn_id},
        {**match, "grn_id": None, "$or": origin},
    ]


def receipt_of(db, unit: dict) -> Optional[str]:
    """The grn_id of the receipt whose origin_filters match `unit`, or None."""
    if unit.get("grn_id"):
        return unit["grn_id"]
    if unit.get("source_type") == "GRN" and unit.get("source_id"):
        return unit["source_id"]
    number = unit.get("grn_number")
    if not number:
        return None
    # ponytail: two reads per moved pre-#1165 piece (a handful); batch if many.
    grns = db.get_collection("grns")
    if not number_is_unique(grns, number):
        return None
    return (grns.find_one({"grn_number": number}, {"_id": 0, "grn_id": 1}) or {}).get("grn_id")


def receipt_store_id(db, grn_doc, linked_dc_ids) -> Optional[str]:
    """The shop the goods were DELIVERED to -- for the booking, its preview
    and the DC draft alike: the linked GRN's store, else the first linked DC
    that names one (a legacy DC with no store is skipped; two different shops
    are refused by purchase_invoices._assert_dcs_single_vendor_store).
    Fail-soft: None on a miss."""
    store_id = (grn_doc or {}).get("store_id")
    for dc_id in linked_dc_ids or []:
        if store_id or db is None:
            break
        try:
            dc = db.get_collection("grns").find_one(
                {"grn_id": dc_id}, {"_id": 0, "store_id": 1}
            )
        except Exception:
            dc = None
        store_id = (dc or {}).get("store_id")
    return store_id or None
