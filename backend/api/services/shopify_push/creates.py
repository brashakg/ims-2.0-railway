"""Shopify push -- the lost-create journal

productCreate, collectionCreate and menuCreate are SENT ONCE (transport): an
answer lost after the send may have created the object, and a blind re-send
would create a second one. So before each create the door records its INTENT
here, and the record stays until the object's gid is SAVED in IMS
(``clear_create`` after the gid write-back) or an answer says the create was
refused unapplied. A press of an object that has a record reads Shopify first
(``settle_lost_create``) and never creates blind:
  found   exactly ONE object that send made -> the press UPDATES it (an
          update-shaped input) and saves its gid; the record stays until the
          gid is saved, so a failed update leaves it for the next press to
          find again -- never a second create;
  create  none -- and Shopify has had time to show one (_SETTLE_AFTER) -> the
          press creates (``record_create`` replaces the record);
  refuse  too early to tell, more than one candidate, or Shopify unreadable
          -> nothing is sent; the press says when to press again.
WHICH OBJECT A SEND MADE: the TITLE IMS sent (and the handle, when IMS
sent one), and
  a product     its createdAt inside the send window (the photo pass's
                window, media._SKEW / media._REACH);
  a collection  (neither has a creation time) the handle IMS sent, or
  or a menu     Shopify's '-<n>' suffix of it (a taken handle made unique),
                AND not on the shop when the create was recorded:
                ``record_create`` lists the objects that already match and
                settle leaves them out, so the shop's own main-menu, or a
                same-title collection already there, is never IMS's.
An object another IMS doc already holds (its gid saved there) is never a
candidate, and a candidate that ANOTHER open record of the same title could
have made too (two same-title products sent inside one window, both answers
lost) is told apart by neither: refused, the rival named, for a person.
Every read pages to its end; past _PAGES pages it is unknown and the press
refuses (ponytail: a shop with more than 5,000 collections, or 5,000
products made inside one send window, is refused, never guessed).
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

from agents.nexus_providers import _as_shopify_gid

from ._shared import logger
from .media import _REACH, _SKEW, _created, _numeric_id, _utc
from .transport import _graphql, _now

CREATES_COLLECTION = "online_creates"
# Shopify's search index (the products search) can show a new object minutes
# after it is made: "none found" means "never made" only once the send window
# has closed AND this has passed.
_SETTLE_AFTER = _REACH + timedelta(minutes=10)
_PAGES = 20

_PRODUCTS_MADE = """
query imsProductsMade($q: String!, $after: String) {
  products(first: 250, after: $after, query: $q, sortKey: CREATED_AT) {
    nodes { id title handle createdAt }
    pageInfo { hasNextPage endCursor }
  }
}
"""
_COLLECTIONS = """
query imsCollections($after: String) {
  collections(first: 250, after: $after) {
    nodes { id title handle }
    pageInfo { hasNextPage endCursor }
  }
}
"""
_MENUS = """
query imsMenus($after: String) {
  menus(first: 250, after: $after) {
    nodes { id title handle }
    pageInfo { hasNextPage endCursor }
  }
}
"""
_LISTS = {"collection": (_COLLECTIONS, "collections"), "menu": (_MENUS, "menus")}
# entity -> (the IMS collection, its key field, the gid field, the gid kind)
_HOLDERS = {
    "product": ("catalog_products", "id", "ecom.shopify_product_id", "Product"),
    "collection": ("ecom_collections", "collection_id", "shopify_collection_id", "Collection"),
    "menu": ("ecom_menus", "menu_id", "shopify_menu_id", "Menu"),
}


def _iso(dt: datetime) -> str:
    return _utc(dt).strftime("%Y-%m-%dT%H:%M:%SZ")


async def _all(db, query: str, field: str, variables: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Every node of a connection, page after page. RAISES on an error body
    or past _PAGES pages -- an unreadable or unfinished read is never
    'nothing there'."""
    out: List[Dict[str, Any]] = []
    after = None
    for _ in range(_PAGES):
        body = await _graphql(db, query, {**(variables or {}), "after": after})
        if not isinstance(body, dict) or body.get("errors"):
            raise RuntimeError("graphql errors: %s" % str((body or {}).get("errors"))[:300])
        conn = (body.get("data") or {}).get(field) or {}
        out += [n for n in conn.get("nodes") or [] if isinstance(n, dict)]
        page = conn.get("pageInfo") or {}
        if not page.get("hasNextPage"):
            return out
        after = page.get("endCursor")
    raise RuntimeError("more than %d pages of %s: too many to tell" % (_PAGES, field))


def _handle_fits(sent: Any, got: Any) -> bool:
    """The handle IMS sent, or Shopify's unique '-<n>' form of it; any handle
    when IMS sent none (Shopify made one from the title). Pure."""
    if not sent:
        return True
    return got == sent or bool(re.fullmatch(re.escape(str(sent)) + r"-\d+", str(got or "")))


def _fits(entity: str, intent: Dict[str, Any], node: Dict[str, Any]) -> bool:
    """Could the create ``intent`` recorded have made ``node``: its title and
    handle, and for a product its createdAt inside the send window (the
    ``before`` list is the caller's). Pure."""
    if not node.get("id") or node.get("title") != intent.get("title"):
        return False
    if not _handle_fits(intent.get("handle"), node.get("handle")):
        return False
    if entity != "product":
        return True
    lo, hi = _utc(intent["sent_at"]) - _SKEW, _utc(intent["sent_at"]) + _REACH
    return lo <= (_created(node) or lo - _SKEW) <= hi


async def _candidates(db, entity: str, intent: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The nodes on the shop that fit the intent (see the module note), the
    ``before`` list left in. RAISES when Shopify cannot be read."""
    if entity == "product":
        lo, hi = _utc(intent["sent_at"]) - _SKEW, _utc(intent["sent_at"]) + _REACH
        q = "created_at:>='%s' AND created_at:<='%s'" % (_iso(lo), _iso(hi))
        nodes = await _all(db, _PRODUCTS_MADE, "products", {"q": q})
    else:
        nodes = await _all(db, *_LISTS[entity])
    return [n for n in nodes if _fits(entity, intent, n)]


def _held(db, entity: str, key: Any, gids: List[str]) -> Set[str]:
    """The gids among ``gids`` that ANOTHER IMS doc of the entity holds (its
    gid saved, as a gid or a bare number). RAISES on a db error."""
    if not gids:
        return set()
    name, id_field, gid_field, kind = _HOLDERS[entity]
    wanted = {_as_shopify_gid(g, kind) for g in gids}
    held: Set[str] = set()
    for doc in db[name].find({gid_field: {"$in": sorted(wanted) + [_numeric_id(g) for g in wanted]}}):
        value: Any = doc
        for part in gid_field.split("."):
            value = (value or {}).get(part) if isinstance(value, dict) else None
        if doc.get(id_field) != key:
            held.add(_as_shopify_gid(value, kind))
    return held & wanted


def _rivals(db, entity: str, intent: Dict[str, Any], made: List[Dict[str, Any]]) -> List[str]:
    """The OTHER open records of the entity, sent with the same title, that
    could have made one of ``made`` too (_fits, and not on their own
    ``before`` list): such a candidate is told apart by neither. RAISES on a
    db error."""
    return [
        str(r["_id"])
        for r in db[CREATES_COLLECTION].find({"entity": entity, "title": intent.get("title")})
        if r.get("_id") != intent["_id"]
        and isinstance(r.get("sent_at"), datetime)
        and any(_fits(entity, r, n) and str(n["id"]) not in (r.get("before") or []) for n in made)
    ]


def _key(entity: str, key: Any) -> str:
    return "%s:%s" % (entity, key)


async def record_create(db, entity: str, key: Any, title: Any, handle: Any = None) -> None:
    """Record the intent BEFORE the create is sent -- for a collection or menu
    with the objects that already fit it (``before``). RAISES on a db error
    or an unreadable Shopify: no record, no create."""
    intent: Dict[str, Any] = {"_id": _key(entity, key), "entity": entity, "title": title, "handle": handle}
    if entity in _LISTS:
        intent["before"] = [str(n["id"]) for n in await _candidates(db, entity, intent)]
    intent["sent_at"] = _now()
    coll = db[CREATES_COLLECTION]
    coll.delete_one({"_id": intent["_id"]})
    coll.insert_one(intent)


def clear_create(db, entity: str, key: Any) -> None:
    """The object's gid is saved in IMS, or its create was refused unapplied:
    nothing to find later. Fail-soft -- a stale record only makes a later
    create of this key look first."""
    try:
        db[CREATES_COLLECTION].delete_one({"_id": _key(entity, key)})
    except Exception as exc:  # noqa: BLE001
        logger.warning("[SHOPIFY_PUSH] create journal clear failed %s: %s", _key(entity, key), exc)


async def settle_lost_create(db, entity: str, key: Any) -> Tuple[str, Optional[str], Optional[str]]:
    """Before a create of (entity, key): ('create', None, None) -- no lost
    create, send it; ('found', gid, None) -- the lost create landed, update
    that object (the record stays until its gid is saved); ('refuse', None,
    line) -- send nothing. Never raises."""
    try:
        intent = db[CREATES_COLLECTION].find_one({"_id": _key(entity, key)})
    except Exception as exc:  # noqa: BLE001 -- unknown is never 'none'
        return "refuse", None, "refused: the create journal could not be read (%s) -- nothing was sent; press again" % exc
    if not intent or not isinstance(intent.get("sent_at"), datetime):
        return "create", None, None
    sent = _utc(intent["sent_at"])
    try:
        before = set(intent.get("before") or [])
        nodes = [n for n in await _candidates(db, entity, intent) if str(n["id"]) not in before]
        held = _held(db, entity, key, [str(n["id"]) for n in nodes])
        nodes = [n for n in nodes if _as_shopify_gid(n["id"], _HOLDERS[entity][3]) not in held]
        rivals = _rivals(db, entity, intent, nodes) if nodes else []
    except Exception as exc:  # noqa: BLE001
        return "refuse", None, (
            "refused: an earlier create of this %s may have reached Shopify and Shopify could not be "
            "read to check (%s) -- nothing was sent; press again" % (entity, exc)
        )
    made = [str(n["id"]) for n in nodes]
    if len(made) == 1 and not rivals:
        return "found", made[0], None
    if rivals:
        return "refuse", None, (
            "refused: an earlier create of this %s may have reached Shopify, and %s -- sent with the same "
            "title and not yet linked -- may be the one that made %s. Neither can tell which: delete it "
            "in the Shopify admin and each is created afresh on its next press; nothing was sent"
            % (entity, ", ".join(rivals), ", ".join(made))
        )
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
    return "create", None, None
