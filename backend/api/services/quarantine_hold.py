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

from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

# A return being saved holds its pieces; a save that never finished (a
# crashed request) stops holding them after this.
SAVING_MINUTES = 10


def stamp(dt: datetime) -> str:
    """The sortable UTC time string the claims are stamped with."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")


# The claim rows a create writes before it is a return: being saved, being
# undone (its request died), or the tombstone of an undone save.
UNSAVED = ("saving", "abandoning", "abandoned")


def claim_is_dead(ret: Dict) -> bool:
    """A claim whose request died: an undone tombstone, or a claim (being
    saved or being undone) older than SAVING_MINUTES. A return is never one."""
    status = (ret or {}).get("status")
    if status == "abandoned":
        return True
    dead = stamp(datetime.now(timezone.utc) - timedelta(minutes=SAVING_MINUTES))
    return status in ("saving", "abandoning") and str(ret.get("claimed_at") or "") < dead


def open_return_of(db, unit: Dict) -> Optional[str]:
    """The open (not cancelled) vendor return a piece is on, if any. A dead
    claim holds nothing (a manager may lift its pieces)."""
    rid = (unit or {}).get("rtv_vendor_id")
    if not rid or db is None:
        return None
    ret = db.get_collection("vendor_returns").find_one({"return_id": rid})
    if not ret or ret.get("status") == "cancelled" or claim_is_dead(ret):
        return None
    return rid


def stale_claim_link(db, unit: Dict) -> bool:
    """The piece's rtv_vendor_id names a dead claim -- a link to clear (a
    VENDOR id the Quarantine screen wrote names no return, and stays)."""
    rid = (unit or {}).get("rtv_vendor_id")
    if not rid or db is None:
        return False
    return claim_is_dead(db.get_collection("vendor_returns").find_one({"return_id": rid}) or {})


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
