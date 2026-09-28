"""Shopify push -- the lost-create journal

productCreate, collectionCreate and menuCreate are SENT ONCE (transport): an
answer lost after the send may have created the object, and a blind re-send
would create a second one. So before each create the door records its INTENT
here, a clean answer (created or refused) clears it, and a lost one leaves it
for the NEXT press of that object, which reads Shopify first
(``settle_lost_create``) and never creates blind:
  found   exactly ONE object made by that send -> the press links it and goes
          on as an UPDATE of it (its gid written back as usual);
  create  none -- and Shopify has had time to show one (_SETTLE_AFTER) -> the
          intent is cleared and the press creates;
  refuse  too early to tell, more than one candidate, or Shopify unreadable
          -> nothing is sent; the press says when to press again.
The object a send made is told by its TITLE (and the handle IMS sent) and by
WHEN it was made: a product's createdAt inside the send window (the photo
pass's window, media._SKEW / media._REACH), a collection's updatedAt from the
window on. A menu handle is unique on a shop: the menu with IMS's handle.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from ._shared import logger
from .media import _REACH, _SKEW, _created, _utc
from .transport import _graphql, _now

CREATES_COLLECTION = "online_creates"
# Shopify's search index (products / collections with a query) can show a
# new object minutes after it is made: "none found" means "never made" only
# once the send window has closed AND this has passed.
_SETTLE_AFTER = _REACH + timedelta(minutes=10)

_PRODUCTS_MADE = """
query imsProductsMade($q: String!) {
  products(first: 20, query: $q, sortKey: CREATED_AT) { nodes { id title createdAt } }
}
"""
_COLLECTIONS_TOUCHED = """
query imsCollectionsTouched($q: String!) {
  collections(first: 50, query: $q, sortKey: UPDATED_AT) { nodes { id title handle } }
}
"""
_MENUS = """
query imsMenus {
  menus(first: 250) { nodes { id title handle } }
}
"""


def _iso(dt: datetime) -> str:
    return _utc(dt).strftime("%Y-%m-%dT%H:%M:%SZ")


def _nodes(body: Any, field: str) -> List[Dict[str, Any]]:
    """The connection's nodes; RAISES on an error body -- an unreadable
    Shopify is never 'nothing there'."""
    if not isinstance(body, dict) or body.get("errors"):
        raise RuntimeError("graphql errors: %s" % str((body or {}).get("errors"))[:300])
    return [n for n in ((body.get("data") or {}).get(field) or {}).get("nodes") or [] if isinstance(n, dict)]


async def _made_products(db, intent: Dict[str, Any]) -> List[str]:
    lo, hi = _utc(intent["sent_at"]) - _SKEW, _utc(intent["sent_at"]) + _REACH
    q = "created_at:>='%s' AND created_at:<='%s'" % (_iso(lo), _iso(hi))
    return [
        str(n["id"])
        for n in _nodes(await _graphql(db, _PRODUCTS_MADE, {"q": q}), "products")
        if n.get("id") and n.get("title") == intent["title"] and lo <= (_created(n) or lo - _SKEW) <= hi
    ]


async def _made_collections(db, intent: Dict[str, Any]) -> List[str]:
    q = "updated_at:>='%s'" % _iso(_utc(intent["sent_at"]) - _SKEW)
    return [
        str(n["id"])
        for n in _nodes(await _graphql(db, _COLLECTIONS_TOUCHED, {"q": q}), "collections")
        if n.get("id")
        and n.get("title") == intent["title"]
        and (not intent.get("handle") or n.get("handle") == intent["handle"])
    ]


async def _made_menus(db, intent: Dict[str, Any]) -> List[str]:
    return [
        str(n["id"])
        for n in _nodes(await _graphql(db, _MENUS, {}), "menus")
        if n.get("id") and n.get("handle") == intent.get("handle")
    ]


_FINDERS = {"product": _made_products, "collection": _made_collections, "menu": _made_menus}


def _key(entity: str, key: Any) -> str:
    return "%s:%s" % (entity, key)


def record_create(db, entity: str, key: Any, title: Any, handle: Any = None) -> None:
    """Record the intent BEFORE the create is sent. RAISES on a db error: no
    record, no create."""
    coll = db[CREATES_COLLECTION]
    coll.delete_one({"_id": _key(entity, key)})
    coll.insert_one(
        {"_id": _key(entity, key), "entity": entity, "title": title, "handle": handle, "sent_at": _now()}
    )


def clear_create(db, entity: str, key: Any) -> None:
    """The create's answer came back (created or refused): nothing to find
    later. Fail-soft -- a stale intent only makes the next create look first."""
    try:
        db[CREATES_COLLECTION].delete_one({"_id": _key(entity, key)})
    except Exception as exc:  # noqa: BLE001
        logger.warning("[SHOPIFY_PUSH] create journal clear failed %s: %s", _key(entity, key), exc)


async def settle_lost_create(db, entity: str, key: Any) -> Tuple[str, Optional[str], Optional[str]]:
    """Before a create of (entity, key): ('create', None, None) -- no lost
    create, send it; ('found', gid, None) -- the lost create landed, update
    that object; ('refuse', None, line) -- send nothing. Never raises."""
    try:
        intent = db[CREATES_COLLECTION].find_one({"_id": _key(entity, key)})
    except Exception as exc:  # noqa: BLE001 -- unknown is never 'none'
        return "refuse", None, "refused: the create journal could not be read (%s) -- nothing was sent; press again" % exc
    if not intent or not isinstance(intent.get("sent_at"), datetime):
        return "create", None, None
    sent = _utc(intent["sent_at"])
    try:
        made = await _FINDERS[entity](db, intent)
    except Exception as exc:  # noqa: BLE001
        return "refuse", None, (
            "refused: an earlier create of this %s may have reached Shopify and Shopify could not be "
            "read to check (%s) -- nothing was sent; press again" % (entity, exc)
        )
    if len(made) == 1:
        clear_create(db, entity, key)
        return "found", made[0], None
    if made:
        return "refuse", None, (
            "refused: an earlier create of this %s may have reached Shopify and %d objects there could "
            "be it (%s) -- a person must check Shopify and delete the duplicate; nothing was sent"
            % (entity, len(made), ", ".join(made))
        )
    if _now() - sent < _SETTLE_AFTER:
        return "refuse", None, (
            "refused: an earlier create of this %s (sent %s UTC) may still show up on Shopify -- nothing "
            "was sent, so it is never created twice; press again after %s UTC"
            % (entity, _iso(sent), _iso(sent + _SETTLE_AFTER))
        )
    clear_create(db, entity, key)
    return "create", None, None
