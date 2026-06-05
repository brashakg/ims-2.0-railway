"""
IMS 2.0 - Online Store : Menus / Mega-Menu Router  (BVI Phase 3 -- FLAGSHIP #2)
==============================================================================
CRUD + item-tree editing for `ecom_menus` -- BVI's Shopify navigation Menus +
the mega-menu, folded into IMS (BVI_MERGE_PLAN.md A.1 / Phase 3).

PUSH-DARK: every route here stores/edits menus inside IMS Mongo ONLY. No Shopify
network write happens in Phase 3 (the GraphQL `menuUpdate` push is Phase 5).
Writes flip the `locally_modified` dirty flag (handled in the repository) so the
Phase-5 push queue can later find what changed.

The whole item hierarchy is an EMBEDDED recursive tree on the menu doc (`items`);
each node has its own `children` array. Nodes are addressed by their own `id`
(a uuid the server mints), never by Mongo `_id`. The convenience item routes
(add / update / move / remove a node) keep the editor from having to PUT the full
tree on every small change, but PUT /{menu_id} with `items` is also supported for
a wholesale replace.

Mounted at /api/v1/online-store/menus. ROLE GATE (router-level): SUPERADMIN /
ADMIN / CATALOG_MANAGER / DESIGN_MANAGER (SUPERADMIN auto-granted by
require_roles). Every route is catalogued in api/services/rbac_policy.POLICY with
this exact set (kept in lock-step -- test_rbac_policy.test_no_uncatalogued_routes
is the regression lock).

Routes:
  GET    /                                list (filter active / is_default)
  POST   /                                create (optional initial items tree)
  GET    /{menu_id}                       fetch one
  PUT    /{menu_id}                       update title/active/is_default + full tree
  DELETE /{menu_id}                       delete
  POST   /{menu_id}/items                 add a node (under parent_id, at position)
  PUT    /{menu_id}/items/reorder         replace the whole items tree (reorder)
  PUT    /{menu_id}/items/{item_id}       patch a node's presentation fields
  PUT    /{menu_id}/items/{item_id}/move  move a node (new parent + position)
  DELETE /{menu_id}/items/{item_id}       remove a node (+ its subtree)

Everything is FAIL-SOFT: no DB -> reads return empty / writes 503; never 500.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .auth import require_roles

router = APIRouter()

# Roles allowed into the Menus surface. SUPERADMIN is auto-granted by
# require_roles, so it is not repeated in the tuple but IS listed in the POLICY
# rows. Keep this in lock-step with rbac_policy.POLICY for every route below.
_ECOM_ROLES = ("ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER")

# Shopify MenuItemType enum (BVI MenuItem.itemType). Validated on add/update so a
# typo can't store an un-pushable type.
_ITEM_TYPES = {
    "COLLECTION",
    "COLLECTIONS",
    "PRODUCT",
    "PAGE",
    "BLOG",
    "ARTICLE",
    "FRONTPAGE",
    "CATALOG",
    "SEARCH",
    "HTTP",
    "SHOP_POLICY",
    "METAOBJECT",
}


# ---------------------------------------------------------------------------
# DB helpers (fail-soft; mirror routers/online_store_collections.py)
# ---------------------------------------------------------------------------


def _get_db():
    """Underlying DB object (real pymongo Database or seeded MockDatabase) when
    connected, else None. Subscript access (db[name]) works on both."""
    try:
        from ..dependencies import get_db

        conn = get_db()
        if conn is not None and getattr(conn, "is_connected", False):
            return conn.db
    except Exception:  # noqa: BLE001
        pass
    return None


def _repo():
    """Return an EcomMenuRepository bound to the live `ecom_menus` collection, or
    None when no DB is available (so the route can 503 cleanly)."""
    db = _get_db()
    if db is None:
        return None
    try:
        from database.repositories import EcomMenuRepository

        return EcomMenuRepository(db["ecom_menus"])
    except Exception:  # noqa: BLE001
        return None


def _require_repo():
    repo = _repo()
    if repo is None:
        # No DB -> the menus store is unavailable. 503 (not a false 200).
        raise HTTPException(status_code=503, detail="Menus store unavailable")
    return repo


def _with_id(doc):
    """Mirror the internal `menu_id` onto a stable `id` key so every FE consumer
    (which reads `row.id`) gets the same handle regardless of entity. Additive +
    non-destructive: leaves an existing `id` alone, tolerates a list (maps each
    element) or a non-dict (returned untouched). Item-tree node ids already use
    their own `id`, so only the menu envelope needs this. Fail-soft."""
    if isinstance(doc, list):
        return [_with_id(d) for d in doc]
    if isinstance(doc, dict):
        if doc.get("id") is None and doc.get("menu_id") is not None:
            doc["id"] = doc["menu_id"]
    return doc


# ---------------------------------------------------------------------------
# Pydantic payloads
# ---------------------------------------------------------------------------


class MenuItemIn(BaseModel):
    """A MenuItem node as supplied by the editor. `id` is server-owned (ignored
    on input); `children` lets a whole subtree be added/replaced in one call.

    Declared with a forward self-reference so `children` is the same shape
    (recursive mega-menu). model_rebuild() below resolves the reference.
    """

    title: str = ""
    item_type: Optional[str] = Field(None, description="Shopify MenuItemType")
    url: Optional[str] = None
    resource_id: Optional[str] = None
    tags_filter: Optional[str] = None
    icon_url: Optional[str] = None
    banner_url: Optional[str] = None
    badge_text: Optional[str] = None
    badge_color: Optional[str] = None
    pinned_to_top: bool = False
    children: Optional[List["MenuItemIn"]] = None


MenuItemIn.model_rebuild()


class MenuCreate(BaseModel):
    title: str
    handle: str = Field(..., description="Unique menu slug, e.g. main-menu / footer")
    is_default: bool = False
    active: bool = True
    # Optional initial item tree (each node may carry its own children).
    items: Optional[List[MenuItemIn]] = None


class MenuUpdate(BaseModel):
    """All fields optional -- only provided keys are patched. Supplying `items`
    REPLACES the whole tree (normalized + renumbered by the repository)."""

    title: Optional[str] = None
    handle: Optional[str] = None
    is_default: Optional[bool] = None
    active: Optional[bool] = None
    items: Optional[List[MenuItemIn]] = None


class AddItem(BaseModel):
    """Add one node under `parent_id` (None = top level) at `position`
    (None = append). The node payload is `item`; its `children` (if any) are
    added with it."""

    item: MenuItemIn
    parent_id: Optional[str] = None
    position: Optional[int] = None


class MoveItem(BaseModel):
    new_parent_id: Optional[str] = None
    position: Optional[int] = None


class UpdateItem(BaseModel):
    """Patch a node's presentation/linkage fields in place (no re-parenting --
    use the move route for that). Only provided keys change."""

    title: Optional[str] = None
    item_type: Optional[str] = None
    url: Optional[str] = None
    resource_id: Optional[str] = None
    tags_filter: Optional[str] = None
    icon_url: Optional[str] = None
    banner_url: Optional[str] = None
    badge_text: Optional[str] = None
    badge_color: Optional[str] = None
    pinned_to_top: Optional[bool] = None


class ReorderItems(BaseModel):
    """Replace the whole items tree with `items` (the editor's drag-reorder
    result). Equivalent to PUT /{menu_id} with only `items`, exposed separately
    for a cleaner editor call."""

    items: List[MenuItemIn] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _validate_item_type(item_type: Optional[str]) -> Optional[str]:
    """Uppercase + validate a Shopify MenuItemType, or 400. None passes through
    (item_type is optional on a node)."""
    if item_type is None:
        return None
    t = item_type.strip().upper()
    if t not in _ITEM_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"item_type must be one of {sorted(_ITEM_TYPES)}",
        )
    return t


def _item_to_dict(item: MenuItemIn) -> Dict:
    """Convert a MenuItemIn (recursively) into the plain dict the repository
    stores, validating each node's item_type. `id`/`parent_id`/`position` are
    left to the repository to own."""
    data = item.model_dump(exclude_none=True)
    if "item_type" in data:
        data["item_type"] = _validate_item_type(data["item_type"])
    kids = item.children or []
    data["children"] = [_item_to_dict(k) for k in kids]
    return data


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


@router.get("")
async def list_menus(
    active: Optional[bool] = Query(None),
    is_default: Optional[bool] = Query(None),
    limit: int = Query(200, ge=1, le=1000),
    skip: int = Query(0, ge=0),
    current_user: dict = Depends(require_roles(*_ECOM_ROLES)),
) -> Dict:
    """List menus (filtered, fail-soft). No DB -> empty list, never 500."""
    repo = _repo()
    if repo is None:
        return {"menus": [], "count": 0, "db_connected": False}
    rows = repo.list(active=active, is_default=is_default, skip=skip, limit=limit)
    return {"menus": _with_id(rows), "count": len(rows), "db_connected": True}


@router.post("", status_code=201)
async def create_menu(
    payload: MenuCreate,
    current_user: dict = Depends(require_roles(*_ECOM_ROLES)),
) -> Dict:
    """Create a menu (PUSH-DARK -- stored in IMS only).

    `handle` must be unique; a duplicate is a 409. An optional initial `items`
    tree is normalized (ids minted, positions fixed) by the repository.
    """
    repo = _require_repo()

    handle = (payload.handle or "").strip()
    if not handle:
        raise HTTPException(status_code=400, detail="handle is required")
    if repo.get_by_handle(handle) is not None:
        raise HTTPException(status_code=409, detail=f"handle already exists: {handle}")

    data: Dict = {
        "title": payload.title,
        "handle": handle,
        "is_default": payload.is_default,
        "active": payload.active,
        "items": [_item_to_dict(i) for i in (payload.items or [])],
        "created_by": current_user.get("user_id"),
    }
    created = repo.create(data)
    if created is None:
        raise HTTPException(status_code=500, detail="Failed to create menu")
    return {"menu": _with_id(created)}


@router.get("/{menu_id}")
async def get_menu(
    menu_id: str,
    current_user: dict = Depends(require_roles(*_ECOM_ROLES)),
) -> Dict:
    repo = _require_repo()
    doc = repo.get_by_id(menu_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Menu not found")
    return {"menu": _with_id(doc)}


@router.put("/{menu_id}")
async def update_menu(
    menu_id: str,
    payload: MenuUpdate,
    current_user: dict = Depends(require_roles(*_ECOM_ROLES)),
) -> Dict:
    """Patch a menu (title / active / is_default and/or the FULL items tree).
    Supplying `items` replaces the whole tree. The row is marked locally_modified
    for the Phase-5 push queue."""
    repo = _require_repo()
    existing = repo.get_by_id(menu_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Menu not found")

    data = payload.model_dump(exclude_none=True, exclude={"items"})
    # A handle change must not collide with another menu's slug.
    new_handle = data.get("handle")
    if new_handle:
        new_handle = new_handle.strip()
        clash = repo.get_by_handle(new_handle)
        if clash is not None and clash.get("menu_id") != menu_id:
            raise HTTPException(
                status_code=409, detail=f"handle already exists: {new_handle}"
            )
        data["handle"] = new_handle
    # Whole-tree replacement (only when `items` was explicitly provided).
    if payload.items is not None:
        data["items"] = [_item_to_dict(i) for i in payload.items]

    if not data:
        # Nothing to change -> return the unchanged doc (idempotent no-op).
        return {"menu": _with_id(existing), "updated": False}

    repo.update(menu_id, data)
    return {"menu": _with_id(repo.get_by_id(menu_id)), "updated": True}


@router.delete("/{menu_id}")
async def delete_menu(
    menu_id: str,
    current_user: dict = Depends(require_roles(*_ECOM_ROLES)),
) -> Dict:
    repo = _require_repo()
    if repo.get_by_id(menu_id) is None:
        raise HTTPException(status_code=404, detail="Menu not found")
    ok = repo.delete(menu_id)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to delete menu")
    return {"deleted": True, "menu_id": menu_id}


# ---------------------------------------------------------------------------
# Item-tree editing (embedded recursive nodes addressed by their own id)
# ---------------------------------------------------------------------------


@router.post("/{menu_id}/items")
async def add_menu_item(
    menu_id: str,
    payload: AddItem,
    current_user: dict = Depends(require_roles(*_ECOM_ROLES)),
) -> Dict:
    """Add a node under `parent_id` (None = top level) at `position` (None =
    append). The server mints the node id. An unknown parent_id is a 404."""
    repo = _require_repo()
    if repo.get_by_id(menu_id) is None:
        raise HTTPException(status_code=404, detail="Menu not found")
    item = _item_to_dict(payload.item)
    updated = repo.add_item(
        menu_id, item, parent_id=payload.parent_id, position=payload.position
    )
    if updated is None:
        # add_item returns None for an unknown parent (caller error) too.
        raise HTTPException(
            status_code=400, detail="Failed to add item (unknown parent_id?)"
        )
    return {"menu": _with_id(updated)}


@router.put("/{menu_id}/items/reorder")
async def reorder_menu_items(
    menu_id: str,
    payload: ReorderItems,
    current_user: dict = Depends(require_roles(*_ECOM_ROLES)),
) -> Dict:
    """Replace the whole items tree (the editor's drag-reorder result). The tree
    is normalized + renumbered by the repository."""
    repo = _require_repo()
    if repo.get_by_id(menu_id) is None:
        raise HTTPException(status_code=404, detail="Menu not found")
    items = [_item_to_dict(i) for i in (payload.items or [])]
    ok = repo.update(menu_id, {"items": items})
    return {"menu": _with_id(repo.get_by_id(menu_id)), "updated": bool(ok)}


@router.put("/{menu_id}/items/{item_id}/move")
async def move_menu_item(
    menu_id: str,
    item_id: str,
    payload: MoveItem,
    current_user: dict = Depends(require_roles(*_ECOM_ROLES)),
) -> Dict:
    """Move a node to a new parent (None = top level) + position. Moving a node
    under itself or a descendant (a cycle) or to an unknown parent is a 400."""
    repo = _require_repo()
    if repo.get_by_id(menu_id) is None:
        raise HTTPException(status_code=404, detail="Menu not found")
    updated = repo.move_item(
        menu_id, item_id, new_parent_id=payload.new_parent_id, position=payload.position
    )
    if updated is None:
        raise HTTPException(
            status_code=400,
            detail="Failed to move item (unknown item/parent or illegal cycle)",
        )
    return {"menu": _with_id(updated)}


@router.put("/{menu_id}/items/{item_id}")
async def update_menu_item(
    menu_id: str,
    item_id: str,
    payload: UpdateItem,
    current_user: dict = Depends(require_roles(*_ECOM_ROLES)),
) -> Dict:
    """Patch a node's presentation/linkage fields in place (title / item_type /
    url / badges / icon / pinned_to_top / ...). Re-parenting is the move route.
    An unknown node is a 404."""
    repo = _require_repo()
    if repo.get_by_id(menu_id) is None:
        raise HTTPException(status_code=404, detail="Menu not found")
    fields = payload.model_dump(exclude_none=True)
    if "item_type" in fields:
        fields["item_type"] = _validate_item_type(fields["item_type"])
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")
    updated = repo.update_item(menu_id, item_id, fields)
    if updated is None:
        raise HTTPException(status_code=404, detail="Menu item not found")
    return {"menu": _with_id(updated)}


@router.delete("/{menu_id}/items/{item_id}")
async def remove_menu_item(
    menu_id: str,
    item_id: str,
    current_user: dict = Depends(require_roles(*_ECOM_ROLES)),
) -> Dict:
    """Remove a node (and its whole subtree) from the menu (idempotent)."""
    repo = _require_repo()
    if repo.get_by_id(menu_id) is None:
        raise HTTPException(status_code=404, detail="Menu not found")
    updated = repo.remove_item(menu_id, item_id)
    if updated is None:
        raise HTTPException(status_code=500, detail="Failed to remove item")
    return {"menu": _with_id(updated)}
