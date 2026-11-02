"""Shopify push -- menus

Menus: the MenuItem*Input tree builder and `push_menu`.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set
from urllib.parse import urlsplit

from agents.nexus_providers import _as_shopify_gid

from ._shared import MODE_LIVE, MODE_SIMULATED, PushResult, _live_or_reason
from .transport import _graphql, _user_errors
from .queries import _MENU_CREATE, _MENU_UPDATE, _PAGES_READ
from .writeback import _writeback_simple

# Store paths Shopify links by its own item type (no resource behind them).
_PATH_TYPES = {"/": "FRONTPAGE", "/collections": "COLLECTIONS", "/collections/all": "CATALOG", "/search": "SEARCH"}
# /<segment>/<handle>: the resource IMS looks up by handle before a LIVE push.
_HANDLE_TYPES = {"collections": "COLLECTION", "pages": "PAGE"}
_GID_KIND = {
    "COLLECTION": "Collection", "PAGE": "Page", "PRODUCT": "Product", "BLOG": "Blog", "ARTICLE": "Article",
    "SHOP_POLICY": "ShopPolicy", "METAOBJECT": "Metaobject", "CUSTOMER_ACCOUNT_PAGE": "CustomerAccountPage",
}


class UnresolvedLink(ValueError):
    """A menu link IMS cannot send in Shopify's own form: the push is refused."""


def item_form(node: Dict[str, Any], resources: Optional[Dict[str, Dict[str, str]]] = None) -> Dict[str, Any]:
    """THE one rule for how a menu link goes to Shopify: {type, url?, resourceId?}.

    Shopify keeps an HTTP item's url only when it is '#' or absolute; a store
    path sent as HTTP is stored as http://<last segment> (live 2026-11-02:
    /pages/about-us became http://about-us, a dead link). So:
      * an item that names its resource (PAGE / COLLECTION / ... + resource_id)
        is sent with it (the gid kind follows the type);
      * '#' or an absolute address (https://..., mailto:) -> HTTP as written;
      * '/', '/collections', '/collections/all', '/search' -> Shopify's own type;
      * '/pages/<h>' -> PAGE, '/collections/<h>' -> COLLECTION, the resourceId
        looked up by handle in ``resources`` ({"PAGE": {handle: gid}, ...},
        read live right before the push);
      * any other store path, or a handle Shopify does not have -> UnresolvedLink.
    Without ``resources`` (the DARK preview, the change hash) a handle link is
    described by type + url only and nothing is refused."""
    title = node.get("title") or ""
    kind = str(node.get("item_type") or "HTTP").upper()
    url = str(node.get("url") or "").strip()
    rid = node.get("resource_id")
    with_url = {"url": url} if url else {}
    if rid and kind != "HTTP":
        return {"type": kind, "resourceId": _as_shopify_gid(rid, _GID_KIND.get(kind, "Collection")), **with_url}
    try:
        parts = urlsplit(url)
    except ValueError:
        parts = None
    if not url or url.startswith("#") or (parts is not None and (parts.scheme or parts.netloc)):
        return {"type": "HTTP", **with_url}
    path = (parts.path.rstrip("/") or "/") if parts is not None else ""
    if path in _PATH_TYPES:
        return {"type": _PATH_TYPES[path], "url": url}
    seg = path.strip("/").split("/")
    typ = _HANDLE_TYPES.get(seg[0]) if len(seg) == 2 and seg[1] else None
    if resources is None:
        return {"type": typ or kind, "url": url}
    if typ is None:
        raise UnresolvedLink(f"the link '{title}' goes to {url}, a website address IMS cannot send to Shopify")
    gid = (resources.get(typ) or {}).get(seg[1].lower())
    if not gid:
        raise UnresolvedLink(f"the link '{title}' goes to {url}, but Shopify has no {typ.lower()} '{seg[1]}'")
    return {"type": typ, "resourceId": gid, "url": url}


def _needs(items: List[Dict[str, Any]]) -> Set[str]:
    """The resource types (PAGE / COLLECTION) the tree's links must look up."""
    out: Set[str] = set()
    for node in items or []:
        form = item_form(node)
        if form["type"] in _HANDLE_TYPES.values() and "resourceId" not in form:
            out.add(form["type"])
        out |= _needs(node.get("children") or [])
    return out


async def _link_resources(db, items: List[Dict[str, Any]]) -> Dict[str, Dict[str, str]]:
    """{type: {handle: gid}} of every shop page / collection the tree links
    by address (only the kinds it needs; nothing read when none). Raises on a
    failed or partial read: a link is never guessed."""
    need = _needs(items)
    out: Dict[str, Dict[str, str]] = {}
    if "PAGE" in need:
        pages: Dict[str, str] = {}
        after = None
        while True:
            body = await _graphql(db, _PAGES_READ, {"after": after})
            page = ((body or {}).get("data") or {}).get("pages")
            if page is None or (body or {}).get("errors"):
                raise RuntimeError(f"pages read failed: {(body or {}).get('errors')}")
            pages.update({str(p["handle"]).lower(): p["id"] for p in page.get("nodes") or [] if p.get("handle")})
            info = page.get("pageInfo") or {}
            if not info.get("hasNextPage"):
                break
            after = info.get("endCursor")
        out["PAGE"] = pages
    if "COLLECTION" in need:
        from .collection_registry import shop_collections

        out["COLLECTION"] = {str(c["handle"]).lower(): c["id"] for c in await shop_collections(db) if c.get("handle")}
    return out


def build_menu_items(items: List[Dict[str, Any]], with_ids: bool = False,
                     resources: Optional[Dict[str, Dict[str, str]]] = None) -> List[Dict[str, Any]]:
    """Recursively map the IMS ecom_menus item tree -> Shopify MenuItem*Input.
    Each node carries title + its Shopify form (item_form: type + url /
    resourceId) and its children. Used for BOTH create + update (the input
    shapes are field-compatible for our subset). ``with_ids`` (an update only
    -- MenuItemCreateInput has no id) sends a node's ``shopify_item_id`` so
    Shopify keeps that item instead of a new one, with the live item's tags
    (``shopify_item_tags``) so they are not dropped. ``resources``: see
    item_form (a LIVE push passes them; raises UnresolvedLink)."""
    out: List[Dict[str, Any]] = []
    for node in items or []:
        item: Dict[str, Any] = {"title": node.get("title") or ""}
        item.update(item_form(node, resources))
        if with_ids and node.get("shopify_item_id"):
            item["id"] = node["shopify_item_id"]
            if node.get("shopify_item_tags"):
                item["tags"] = list(node["shopify_item_tags"])
        kids = node.get("children") or []
        if kids:
            item["items"] = build_menu_items(kids, with_ids, resources)
        out.append(item)
    return out


# The path the in-app uploader (routers/products.py upload_product_image)
# stores; served public by GET /products/image/{file_id}.

async def push_menu(db, menu: Dict[str, Any], live: Optional[bool] = None) -> PushResult:
    """Push an ecom_menus doc (the Online Store nav / mega-menu) to Shopify
    (menuCreate / menuUpdate) mapping the nested item tree. DARK by default; LIVE
    behind the gates with gid write-back. Never raises. ``live``: the caller
    already decided the gates (generated_menu decides ONCE per push and has
    read / backed up the shop on that decision); None checks them here.
    LIVE sends every link in Shopify's own form (item_form): the pages and
    collections it links by address are read first, and a link that cannot
    be resolved (or a failed read) refuses the whole push -- never a broken
    http://<handle> link on the website."""
    mid = menu.get("menu_id")
    existing_gid = menu.get("shopify_menu_id")
    items = build_menu_items(menu.get("items") or [], with_ids=bool(existing_gid))
    title = menu.get("title") or menu.get("handle")
    handle = menu.get("handle")
    action = "update" if existing_gid else "create"
    payload: Dict[str, Any] = {"title": title, "handle": handle, "items": items}
    if existing_gid:
        payload["id"] = _as_shopify_gid(existing_gid, "Menu")

    reason = None
    if live is None:
        live, reason = _live_or_reason(db)
    if not live:
        return PushResult(
            mode=MODE_SIMULATED,
            entity="menu",
            action=action,
            target_id=mid,
            ok=True,
            shopify_id=existing_gid,
            payload=payload,
            reason=reason,
        )

    try:  # every link in Shopify's own form, or nothing is sent
        items = build_menu_items(menu.get("items") or [], with_ids=bool(existing_gid),
                                 resources=await _link_resources(db, menu.get("items") or []))
    except Exception as e:  # noqa: BLE001 -- an unresolved / unreadable link refuses the push
        return PushResult(
            mode=MODE_LIVE,
            entity="menu",
            action=action,
            target_id=mid,
            ok=False,
            payload=payload,
            error=f"refused, nothing was sent to Shopify: {e}",
        )
    payload["items"] = items
    if existing_gid:
        query, field_name = _MENU_UPDATE, "menuUpdate"
        variables = {
            "id": _as_shopify_gid(existing_gid, "Menu"),
            "title": title,
            "handle": handle,
            "items": items,
        }
    else:
        query, field_name = _MENU_CREATE, "menuCreate"
        variables = {"title": title, "handle": handle, "items": items}
    try:
        body = await _graphql(db, query, variables)
        err = _user_errors(body, field_name)
        if err:
            return PushResult(
                mode=MODE_LIVE,
                entity="menu",
                action=action,
                target_id=mid,
                ok=False,
                payload=payload,
                error=err,
            )
        menu_obj = ((body.get("data") or {}).get(field_name) or {}).get("menu") or {}
        new_gid = menu_obj.get("id") or existing_gid
        if new_gid and mid:
            _writeback_simple(
                db, "ecom_menus", "menu_id", mid, "shopify_menu_id", new_gid,
                sent_rev=menu.get("edit_rev"),
            )
        return PushResult(
            mode=MODE_LIVE,
            entity="menu",
            action=action,
            target_id=mid,
            ok=True,
            shopify_id=new_gid,
            payload=payload,
        )
    except Exception as e:  # noqa: BLE001
        return PushResult(
            mode=MODE_LIVE,
            entity="menu",
            action=action,
            target_id=mid,
            ok=False,
            payload=payload,
            error=str(e),
        )

