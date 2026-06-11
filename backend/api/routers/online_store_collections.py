"""
IMS 2.0 - Online Store : Collections Router  (BVI Phase 2 -- FLAGSHIP #1)
========================================================================
CRUD + membership for `ecom_collections` -- BVI's Shopify Custom/Smart
Collections, folded into IMS (BVI_MERGE_PLAN.md A.1 / Phase 2).

PUSH-DARK: every route here stores/edits collections inside IMS Mongo ONLY. No
Shopify network write happens in Phase 2 (the GraphQL push is Phase 5). Writes
flip the `locally_modified` dirty flag (handled in the repository) so the
Phase-5 push queue can later find what changed.

Mounted at /api/v1/online-store/collections. ROLE GATE (router-level):
SUPERADMIN / ADMIN / CATALOG_MANAGER / DESIGN_MANAGER (SUPERADMIN auto-granted by
require_roles). Every route is catalogued in api/services/rbac_policy.POLICY with
this exact set (kept in lock-step -- test_rbac_policy.test_no_uncatalogued_routes
is the regression lock).

Routes:
  GET    /                       list (filter published/type/category_anchor/auto_source)
  POST   /                       create (CUSTOM or SMART; accepts auto_source/anchor)
  GET    /{id}                   fetch one
  PUT    /{id}                   update
  DELETE /{id}                   delete
  POST   /{id}/products          add a SKU to a manual collection
  DELETE /{id}/products/{sku}    remove a SKU
  PUT    /{id}/products/reorder  reorder the manual membership
  GET    /{id}/resolved-products evaluate SMART rules -> matching SKUs

Everything is FAIL-SOFT: no DB -> reads return empty / writes 503; the resolver
returns an empty set rather than 500.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .auth import require_roles
from ..services import ecom_smart_rules

router = APIRouter()

# Roles allowed into the Collections surface. SUPERADMIN is auto-granted by
# require_roles, so it is not repeated in the tuple but IS listed in the POLICY
# rows. Keep this in lock-step with rbac_policy.POLICY for every route below.
_ECOM_ROLES = ("ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER")

# A SMART collection can resolve against a large catalog; cap the scan + result.
_RESOLVE_MAX = 1000


# ---------------------------------------------------------------------------
# DB helpers (fail-soft; mirror routers/catalog.py + online_store.py)
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
    """Return an EcomCollectionRepository bound to the live `ecom_collections`
    collection, or None when no DB is available (so the route can 503 cleanly)."""
    db = _get_db()
    if db is None:
        return None
    try:
        from database.repositories import EcomCollectionRepository

        return EcomCollectionRepository(db["ecom_collections"])
    except Exception:  # noqa: BLE001
        return None


def _catalog_products() -> List[Dict]:
    """All catalog_products (for the SMART resolver). Fail-soft -> []. Strips the
    Mongo _id in Python (MockCollection.find takes only a filter)."""
    db = _get_db()
    if db is None:
        return []
    try:
        coll = db["catalog_products"]
        docs = list(coll.find({}))
        for d in docs:
            d.pop("_id", None)
        return docs
    except Exception:  # noqa: BLE001
        return []


def _products_by_sku(skus: List[str]) -> Dict[str, Dict]:
    """Map sku -> product detail doc, looking in catalog_products then products.

    Used to render a CUSTOM collection's manual membership (stored SKU-only) as
    rich rows for the editor. Fail-soft -> {} (the route then returns sku-only
    rows so a catalog gap never hides a member)."""
    out: Dict[str, Dict] = {}
    if not skus:
        return out
    db = _get_db()
    if db is None:
        return out
    for coll_name in ("catalog_products", "products"):
        try:
            coll = db[coll_name]
            for d in coll.find({"sku": {"$in": skus}}):
                sku = d.get("sku")
                if sku and sku not in out:
                    d.pop("_id", None)
                    out[sku] = d
        except Exception:  # noqa: BLE001
            continue
    return out


def _require_repo():
    repo = _repo()
    if repo is None:
        # No DB -> the collections store is unavailable. 503 (not a false 200).
        raise HTTPException(status_code=503, detail="Collections store unavailable")
    return repo


def _materialize(collection_id: Optional[str]) -> None:
    """Recompute a collection's materialised membership after a create/rule edit
    (step-13). FULLY fail-soft -- never raises into the CRUD path; a no-op when
    there is no live DB."""
    if not collection_id:
        return
    try:
        from ..services import collection_materializer as _mat

        db = _get_db()
        if db is not None:
            _mat.refresh_collection(db, collection_id)
    except Exception:  # noqa: BLE001 - materialise must never block a rule edit
        pass


def _with_id(doc):
    """Mirror the internal `collection_id` onto a stable `id` key so every FE
    consumer (which reads `row.id`) gets the same handle regardless of entity.
    Additive + non-destructive: leaves an existing `id` alone, tolerates a list
    (maps each element) or a non-dict (returned untouched). Fail-soft."""
    if isinstance(doc, list):
        return [_with_id(d) for d in doc]
    if isinstance(doc, dict):
        if doc.get("id") is None and doc.get("collection_id") is not None:
            doc["id"] = doc["collection_id"]
    return doc


def _with_normalized_rules(doc):
    """Normalize-on-read for SMART rules (BVI revive, unification step 11).

    The 1,160 BVI-migrated collections store rules in the SHOPIFY shape
    ({column, relation, condition}); the FE editor + rule engine speak
    {field, relation, value}, so those rows rendered as BLANK rule rows and
    resolved to zero products. Serving the normalized shape here revives them
    in the editor WITHOUT rewriting the stored docs (idempotent read-side
    translation -- native-shape rules pass through untouched). Returns a
    shallow COPY when a translation happened so the read never mutates a
    cached / mock-store doc. Tolerates a list (maps each element) or a
    non-dict (returned untouched). Fail-soft."""
    if isinstance(doc, list):
        return [_with_normalized_rules(d) for d in doc]
    if isinstance(doc, dict):
        rules = doc.get("rules")
        if isinstance(rules, list) and rules:
            normalized = ecom_smart_rules.normalize_rules(rules)
            if normalized != rules:
                doc = dict(doc)
                doc["rules"] = normalized
    return doc


# ---------------------------------------------------------------------------
# Pydantic payloads
# ---------------------------------------------------------------------------


class SmartRule(BaseModel):
    field: str = Field(..., description="Logical field, e.g. brand / category / tag")
    relation: str = Field("EQUALS", description="EQUALS | CONTAINS")
    value: str = Field(..., description="Value to match (case-insensitive)")


class CollectionCreate(BaseModel):
    title: str
    handle: str = Field(..., description="Unique storefront slug")
    description: Optional[str] = None
    description_html: Optional[str] = None
    collection_type: str = Field("CUSTOM", description="CUSTOM | SMART")
    sort_order: Optional[str] = None
    template_suffix: Optional[str] = None
    image_url: Optional[str] = None
    image_alt: Optional[str] = None
    seo_title: Optional[str] = None
    seo_description: Optional[str] = None
    published: bool = True
    rules: Optional[List[SmartRule]] = None
    disjunctive: bool = False
    banner_image: Optional[str] = None
    short_description: Optional[str] = None
    sort_priority: int = 100
    metafields: Optional[Dict] = None
    auto_source: Optional[str] = None
    category_anchor: Optional[str] = None


class CollectionUpdate(BaseModel):
    """All fields optional -- only provided keys are patched."""

    title: Optional[str] = None
    handle: Optional[str] = None
    description: Optional[str] = None
    description_html: Optional[str] = None
    collection_type: Optional[str] = None
    sort_order: Optional[str] = None
    template_suffix: Optional[str] = None
    image_url: Optional[str] = None
    image_alt: Optional[str] = None
    seo_title: Optional[str] = None
    seo_description: Optional[str] = None
    published: Optional[bool] = None
    rules: Optional[List[SmartRule]] = None
    disjunctive: Optional[bool] = None
    banner_image: Optional[str] = None
    short_description: Optional[str] = None
    sort_priority: Optional[int] = None
    metafields: Optional[Dict] = None
    auto_source: Optional[str] = None
    category_anchor: Optional[str] = None


class AddProduct(BaseModel):
    sku: str
    position: Optional[int] = None


class ReorderProducts(BaseModel):
    skus: List[str] = Field(..., description="SKUs in the desired display order")


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _validate_type(collection_type: Optional[str]) -> Optional[str]:
    if collection_type is None:
        return None
    t = collection_type.strip().upper()
    if t not in ("CUSTOM", "SMART"):
        raise HTTPException(
            status_code=400, detail="collection_type must be CUSTOM or SMART"
        )
    return t


def _rules_to_dicts(rules: Optional[List[SmartRule]]) -> List[Dict]:
    return [r.model_dump() for r in (rules or [])]


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


@router.get("")
async def list_collections(
    published: Optional[bool] = Query(None),
    collection_type: Optional[str] = Query(None),
    category_anchor: Optional[str] = Query(None),
    auto_source: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=1000),
    skip: int = Query(0, ge=0),
    current_user: dict = Depends(require_roles(*_ECOM_ROLES)),
) -> Dict:
    """List collections (filtered, fail-soft). No DB -> empty list, never 500."""
    repo = _repo()
    if repo is None:
        return {"collections": [], "count": 0, "db_connected": False}
    ctype = _validate_type(collection_type)
    rows = repo.list(
        published=published,
        collection_type=ctype,
        category_anchor=category_anchor,
        auto_source=auto_source,
        skip=skip,
        limit=limit,
    )
    rows = _with_normalized_rules(rows)
    return {"collections": _with_id(rows), "count": len(rows), "db_connected": True}


@router.post("", status_code=201)
async def create_collection(
    payload: CollectionCreate,
    current_user: dict = Depends(require_roles(*_ECOM_ROLES)),
) -> Dict:
    """Create a CUSTOM or SMART collection (PUSH-DARK -- stored in IMS only).

    `handle` must be unique; a duplicate is a 409. Accepts auto_source /
    category_anchor lineage on create (the auto-generation cron that WRITES them
    is Phase 2b/5 -- here they are just stored).
    """
    repo = _require_repo()
    ctype = _validate_type(payload.collection_type) or "CUSTOM"

    handle = (payload.handle or "").strip()
    if not handle:
        raise HTTPException(status_code=400, detail="handle is required")
    if repo.get_by_handle(handle) is not None:
        raise HTTPException(status_code=409, detail=f"handle already exists: {handle}")

    data = payload.model_dump(exclude_none=True)
    data["handle"] = handle
    data["collection_type"] = ctype
    data["rules"] = _rules_to_dicts(payload.rules)
    data["created_by"] = current_user.get("user_id")

    # The handle pre-check above is check-then-insert and therefore racy across
    # workers; the unique ecom_collections.handle index is the real backstop.
    # When two concurrent creates race the same handle, the loser's insert is
    # rejected by that index (DuplicateKeyError -- surfaced either directly or
    # as a fail-soft None from the repository). Map BOTH shapes of that loss to
    # the same 409 as the pre-check, not a 500.
    try:
        created = repo.create(data)
    except Exception as exc:  # noqa: BLE001 -- DuplicateKeyError without a hard pymongo import
        if type(exc).__name__ == "DuplicateKeyError":
            raise HTTPException(
                status_code=409, detail=f"handle already exists: {handle}"
            ) from exc
        raise
    if created is None:
        if repo.get_by_handle(handle) is not None:
            # Lost a concurrent create race -- the row now exists.
            raise HTTPException(
                status_code=409, detail=f"handle already exists: {handle}"
            )
        raise HTTPException(status_code=500, detail="Failed to create collection")
    # Step-13: materialise the new collection's membership (fail-soft).
    _materialize(created.get("collection_id") or created.get("id"))
    return {"collection": _with_id(created)}


@router.get("/{collection_id}")
async def get_collection(
    collection_id: str,
    current_user: dict = Depends(require_roles(*_ECOM_ROLES)),
) -> Dict:
    repo = _require_repo()
    doc = repo.get_by_id(collection_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Collection not found")
    return {"collection": _with_id(_with_normalized_rules(doc))}


@router.put("/{collection_id}")
async def update_collection(
    collection_id: str,
    payload: CollectionUpdate,
    current_user: dict = Depends(require_roles(*_ECOM_ROLES)),
) -> Dict:
    """Patch a collection. Only provided fields change; the row is marked
    locally_modified for the Phase-5 push queue."""
    repo = _require_repo()
    existing = repo.get_by_id(collection_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Collection not found")

    data = payload.model_dump(exclude_none=True)
    if "collection_type" in data:
        data["collection_type"] = _validate_type(data["collection_type"])
    if payload.rules is not None:
        data["rules"] = _rules_to_dicts(payload.rules)
    # A handle change must not collide with another collection's slug.
    new_handle = data.get("handle")
    if new_handle:
        new_handle = new_handle.strip()
        clash = repo.get_by_handle(new_handle)
        if clash is not None and clash.get("collection_id") != collection_id:
            raise HTTPException(
                status_code=409, detail=f"handle already exists: {new_handle}"
            )
        data["handle"] = new_handle

    if not data:
        # Nothing to change -> return the unchanged doc (idempotent no-op).
        return {"collection": _with_id(_with_normalized_rules(existing)), "updated": False}

    repo.update(collection_id, data)
    # Step-13: a rule / type / membership-affecting edit re-materialises the
    # collection so the browse view stays in step (fail-soft).
    _materialize(collection_id)
    return {
        "collection": _with_id(_with_normalized_rules(repo.get_by_id(collection_id))),
        "updated": True,
    }


@router.delete("/{collection_id}")
async def delete_collection(
    collection_id: str,
    current_user: dict = Depends(require_roles(*_ECOM_ROLES)),
) -> Dict:
    repo = _require_repo()
    if repo.get_by_id(collection_id) is None:
        raise HTTPException(status_code=404, detail="Collection not found")
    ok = repo.delete(collection_id)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to delete collection")
    return {"deleted": True, "collection_id": collection_id}


# ---------------------------------------------------------------------------
# Manual membership (embedded ordered SKU array)
# ---------------------------------------------------------------------------


@router.post("/{collection_id}/products")
async def add_collection_product(
    collection_id: str,
    payload: AddProduct,
    current_user: dict = Depends(require_roles(*_ECOM_ROLES)),
) -> Dict:
    """Add a SKU to a manual collection's ordered membership (idempotent)."""
    repo = _require_repo()
    existing = repo.get_by_id(collection_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Collection not found")
    sku = (payload.sku or "").strip()
    if not sku:
        raise HTTPException(status_code=400, detail="sku is required")
    updated = repo.add_product(collection_id, sku, payload.position)
    if updated is None:
        raise HTTPException(status_code=500, detail="Failed to add product")
    _materialize(collection_id)
    return {"collection": _with_id(updated)}


@router.delete("/{collection_id}/products/{sku}")
async def remove_collection_product(
    collection_id: str,
    sku: str,
    current_user: dict = Depends(require_roles(*_ECOM_ROLES)),
) -> Dict:
    """Remove a SKU from a manual collection's membership (idempotent)."""
    repo = _require_repo()
    existing = repo.get_by_id(collection_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Collection not found")
    updated = repo.remove_product(collection_id, sku)
    if updated is None:
        raise HTTPException(status_code=500, detail="Failed to remove product")
    _materialize(collection_id)
    return {"collection": _with_id(updated)}


@router.put("/{collection_id}/products/reorder")
async def reorder_collection_products(
    collection_id: str,
    payload: ReorderProducts,
    current_user: dict = Depends(require_roles(*_ECOM_ROLES)),
) -> Dict:
    """Reorder a manual collection's membership to match the given SKU order."""
    repo = _require_repo()
    existing = repo.get_by_id(collection_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Collection not found")
    updated = repo.reorder_products(collection_id, payload.skus or [])
    if updated is None:
        raise HTTPException(status_code=500, detail="Failed to reorder products")
    _materialize(collection_id)
    return {"collection": _with_id(updated)}


@router.get("/{collection_id}/products")
async def list_collection_products(
    collection_id: str,
    current_user: dict = Depends(require_roles(*_ECOM_ROLES)),
) -> Dict:
    """Return a CUSTOM collection's ordered manual membership as rich rows.

    Membership is stored as an embedded `products: [{sku, position}]` array
    (SKU-keyed). The editor needs title/brand/category/image to render the list,
    so we join the catalog (catalog_products, then products) by SKU, ordered by
    position. Fail-soft: an unknown SKU still appears (sku-only) so a catalog gap
    never hides a member. A SMART collection has no manual membership here -> []
    (its set is served by GET /{id}/resolved-products). This is the read the FE
    collectionsApi.members() expects; without it the list 404'd and rendered
    EMPTY after a drawer reopen even though the writes persisted.
    """
    repo = _require_repo()
    doc = repo.get_by_id(collection_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Collection not found")

    members = sorted(
        (doc.get("products") or []),
        key=lambda p: int(p.get("position", 0)),
    )
    skus = [p.get("sku") for p in members if p.get("sku")]
    detail = _products_by_sku(skus)

    rows: List[Dict] = []
    for p in members:
        sku = p.get("sku")
        if not sku:
            continue
        d = detail.get(sku, {})
        images = d.get("images")
        image = images[0] if isinstance(images, list) and images else d.get("image")
        rows.append(
            {
                "product_id": d.get("product_id") or d.get("id") or sku,
                "sku": sku,
                "title": d.get("title") or d.get("name") or d.get("model"),
                "brand": d.get("brand"),
                "category": d.get("category"),
                "image": image,
                "position": int(p.get("position", 0)),
            }
        )
    return {"products": rows, "count": len(rows)}


# ---------------------------------------------------------------------------
# SMART rule resolution
# ---------------------------------------------------------------------------


@router.get("/{collection_id}/resolved-products")
async def resolved_products(
    collection_id: str,
    limit: int = Query(_RESOLVE_MAX, ge=1, le=_RESOLVE_MAX),
    current_user: dict = Depends(require_roles(*_ECOM_ROLES)),
) -> Dict:
    """Evaluate a SMART collection's rules against the catalog and return the
    matching SKUs (Phase-2 preview; nothing is pushed).

    For a CUSTOM collection this returns its stored manual membership SKUs
    (in position order) so the endpoint gives the effective product set for
    either type. Fail-soft: no rules / no DB -> empty set.
    """
    repo = _require_repo()
    doc = repo.get_by_id(collection_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Collection not found")

    ctype = (doc.get("collection_type") or "CUSTOM").upper()
    if ctype == "CUSTOM":
        members = sorted(
            (doc.get("products") or []),
            key=lambda p: int(p.get("position", 0)),
        )
        skus = [p.get("sku") for p in members if p.get("sku")][:limit]
        return {
            "collection_id": collection_id,
            "collection_type": "CUSTOM",
            "skus": skus,
            "count": len(skus),
            "source": "manual",
        }

    # Normalize-on-read: migrated Shopify-shape rules ({column, relation,
    # condition}) evaluate + echo in the IMS shape; stored doc is untouched.
    rules = ecom_smart_rules.normalize_rules(doc.get("rules") or [])
    disjunctive = bool(doc.get("disjunctive", False))
    products = _catalog_products()
    skus = ecom_smart_rules.resolve_skus(
        products, rules, disjunctive=disjunctive, limit=limit
    )
    return {
        "collection_id": collection_id,
        "collection_type": "SMART",
        "disjunctive": disjunctive,
        "rules": rules,
        "skus": skus,
        "count": len(skus),
        "scanned": len(products),
        "source": "smart_rules",
    }
