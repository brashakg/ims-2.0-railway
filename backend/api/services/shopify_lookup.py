"""
IMS 2.0 - find the IMS record a Shopify notification is about
=============================================================
ONE lookup for the Shopify webhook handlers that act on an existing IMS record
(orders/delete, fulfillments/*, refunds/create, customers/delete).

Audit L1-F29: each handler had its own copy that swallowed a FAILED read and
answered None, which the handler reads as "we never had this record" - a
finished outcome. A storage outage therefore acknowledged the notification as
handled (inbox row succeeded) and the deletion / fulfilment / refund was lost.

"No such record" and "could not look" are different answers:
  * the read worked and found nothing  -> None  (the handler's safe no-op)
  * the read failed, or the database is not connected -> RAISES. Every handler
    already turns an exception into {"reason": "exception:<Type>"}, which
    NEXUS records as a failed inbox row and retries.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


def find_by_shopify_id(
    db, collection: str, field: str, value: str
) -> Optional[Dict[str, Any]]:
    """The doc in `collection` whose `field` is this Shopify id, or None when
    there is none. Raises when the lookup itself could not be done."""
    if db is None or not value:
        return None
    coll = db.get_collection(collection)
    if coll is None:
        # database.connection hands back None while disconnected.
        raise RuntimeError(f"{collection} lookup unavailable: database not connected")
    return coll.find_one({field: value})
