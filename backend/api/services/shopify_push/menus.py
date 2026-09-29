"""Shopify push -- menus

Menus: the MenuItem*Input tree builder and `push_menu`.
"""

from __future__ import annotations

from typing import Any, Dict, List

from agents.nexus_providers import _as_shopify_gid

from ._shared import MODE_LIVE, MODE_SIMULATED, PushResult, _live_or_reason
from .transport import SentOnce, _graphql, _user_errors
from .creates import clear_create, record_create, settle_lost_create
from .queries import _MENU_CREATE, _MENU_UPDATE
from .writeback import _writeback_simple

def build_menu_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Recursively map the IMS ecom_menus item tree -> Shopify MenuItem*Input.
    Each node carries title + type (+ url/resourceId) and its children. Used for
    BOTH create + update (the input shapes are field-compatible for our subset)."""
    out: List[Dict[str, Any]] = []
    for node in items or []:
        item: Dict[str, Any] = {
            "title": node.get("title") or "",
            "type": str(node.get("item_type") or "HTTP").upper(),
        }
        if node.get("url"):
            item["url"] = node["url"]
        if node.get("resource_id"):
            item["resourceId"] = _as_shopify_gid(node["resource_id"], "Collection")
        kids = node.get("children") or []
        if kids:
            item["items"] = build_menu_items(kids)
        out.append(item)
    return out


# The path the in-app uploader (routers/products.py upload_product_image)
# stores; served public by GET /products/image/{file_id}.

async def push_menu(db, menu: Dict[str, Any]) -> PushResult:
    """Push an ecom_menus doc (the Online Store nav / mega-menu) to Shopify
    (menuCreate / menuUpdate) mapping the nested item tree. DARK by default; LIVE
    behind the gates with gid write-back. Never raises."""
    mid = menu.get("menu_id")
    existing_gid = menu.get("shopify_menu_id")
    items = build_menu_items(menu.get("items") or [])
    title = menu.get("title") or menu.get("handle")
    handle = menu.get("handle")
    action = "update" if existing_gid else "create"
    payload: Dict[str, Any] = {"title": title, "handle": handle, "items": items}
    if existing_gid:
        payload["id"] = _as_shopify_gid(existing_gid, "Menu")

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
        # NEVER A BLIND RE-CREATE (creates.py): a menuCreate whose answer was
        # lost is looked for first (by its title and handle, never one already
        # on the shop before the send); the record stays until its gid is saved.
        creating = not existing_gid
        if creating:
            verdict, found, why = await settle_lost_create(db, "menu", mid)
            if verdict == "refuse":
                return PushResult(
                    mode=MODE_LIVE,
                    entity="menu",
                    action=action,
                    target_id=mid,
                    ok=False,
                    payload=payload,
                    error=why,
                    reason="create_unsettled",
                )
            if verdict == "found":
                creating = False
                existing_gid = found
                query, field_name = _MENU_UPDATE, "menuUpdate"
                variables = {"id": found, "title": title, "handle": handle, "items": items}
            else:
                await record_create(db, "menu", mid, title, handle)
        try:
            body = await _graphql(db, query, variables)
        except SentOnce:
            raise  # the intent stays: the next press looks for it first
        except Exception:
            if creating:
                clear_create(db, "menu", mid)  # refused unapplied: nothing to find
            raise
        err = _user_errors(body, field_name)
        if err:
            if creating:
                clear_create(db, "menu", mid)  # refused: nothing was made
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
        if new_gid and mid and _writeback_simple(
            db, "ecom_menus", "menu_id", mid, "shopify_menu_id", new_gid
        ) and action == "create":
            clear_create(db, "menu", mid)  # the gid is saved: nothing to find
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

