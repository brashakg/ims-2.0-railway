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
