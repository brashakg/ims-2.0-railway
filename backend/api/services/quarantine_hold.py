"""What holds a quarantined piece in quarantine.

THE check every door that lets a piece out of quarantine by hand asks -- the
Quarantine screen's lift (routers/inventory/quarantine.py) and the item
ledger's release (routers/item_events.py): a piece rejected at receiving
follows its supplier return draft, and a piece on an open supplier return
leaves with it (shipped) or comes back on the return's cancel. Neither is
lifted, restocked, damaged or sent to RTV past the return that owns it.

stock_units.rtv_vendor_id holds a return id when a return linked the piece --
and a VENDOR id when the Quarantine screen named one -- so a link is believed
only when it names a return that exists and is not cancelled.
"""

from typing import Dict, Optional


def open_return_of(db, unit: Dict) -> Optional[str]:
    """The open (not cancelled) vendor return a piece is on, if any."""
    rid = (unit or {}).get("rtv_vendor_id")
    if not rid or db is None:
        return None
    ret = db.get_collection("vendor_returns").find_one({"return_id": rid})
    return rid if ret and ret.get("status") != "cancelled" else None


def release_refusal(db, unit: Dict) -> Optional[Dict]:
    """The 409 detail for a piece that may not leave quarantine by hand; None
    when it may."""
    if (unit or {}).get("receipt_disposition") == "REJECTED":
        return {
            "code": "receipt_rejected",
            "message": "This piece was rejected at receiving. Follow its supplier return; "
                       "lifting quarantine cannot accept it into sellable stock.",
        }
    rid = open_return_of(db, unit)
    if rid:
        return {
            "code": "on_supplier_return",
            "message": f"This piece is on supplier return {rid}. "
                       "Cancel that return to put it back on the shelf.",
        }
    return None
