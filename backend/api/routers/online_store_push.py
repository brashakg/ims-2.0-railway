"""
IMS 2.0 - Online Store : Shopify PUSH Router  (BVI Phase 5 -- IMS -> Shopify)
============================================================================
The IMS -> Shopify PUSH control surface. It drives the GraphQL push engine
(api/services/shopify_push.py) for the four ecom entities and reports the current
push posture.

***** BUILT DARK (the non-negotiable safety contract) *****
Every push here is SIMULATED -- it returns a dry-run PLAN and makes NO Shopify
network call -- UNLESS ALL of: IMS_SHOPIFY_WRITES on AND DISPATCH_MODE=live AND
Shopify creds present in the `integrations` collection. Default / missing-creds /
gate-off => SIMULATED. Per #262 BVI is the single Shopify writer; the IMS push
stays retired until the owner flips the gates in the Phase-6 baton cutover. See
docs/reference/BVI_MERGE_PLAN.md A.3 + Phase 5.

ROLE GATE (router-level): SUPERADMIN / ADMIN ONLY. Pushing to the live storefront
is integration-critical, so -- unlike the rest of the Online Store module (which
also admits CATALOG_MANAGER / DESIGN_MANAGER) -- the push surface is narrowed to
SUPERADMIN + ADMIN. SUPERADMIN is auto-granted by require_roles. Every route is
catalogued in api/services/rbac_policy.POLICY with exactly {ADMIN, SUPERADMIN}
(kept in lock-step -- test_rbac_policy.test_no_uncatalogued_routes is the lock).

AUDIT EVERYTHING: every push ATTEMPT writes a chained audit_logs row
(get_audit_repository().create) capturing the mode (SIMULATED|LIVE), the target,
and the structured result -- so the owner has an immutable record of every push,
dry-run or live (SYSTEM_INTENT: Audit Everything). Audit is fail-soft: an audit
error never undoes / blocks the push.

Mounted at /api/v1/online-store/push:
  POST /product/{product_id}      push a catalog product (+ ecom + variants)
  POST /collection/{collection_id} push an ecom_collections doc (+ smart ruleSet)
  POST /menu/{menu_id}            push an ecom_menus doc (the nav / mega-menu)
  POST /image/{image_id}          push ONE APPROVED product image (productCreateMedia)
  GET  /status                    per-entity pushed-vs-pending + the current mode

Everything is FAIL-SOFT: no DB -> writes 503 (not a false 200); reads degrade to
zeros; a Shopify error becomes a structured {ok:false} result, never a 500.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException

from .auth import require_roles
from ..services import shopify_push

router = APIRouter()

# Push is integration-critical -> SUPERADMIN / ADMIN ONLY (narrower than the rest
# of the Online Store module). SUPERADMIN is auto-granted by require_roles, so it
# is not repeated in the tuple but IS listed in every POLICY row.
_PUSH_ROLES = ("ADMIN",)


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


def _require_db():
    db = _get_db()
    if db is None:
        # No DB -> the push store is unavailable. 503 (not a false 200).
        raise HTTPException(status_code=503, detail="Online Store push unavailable (no DB)")
    return db


def _write_audit(result: Dict[str, Any], current_user: dict) -> None:
    """Write a chained audit row for a push ATTEMPT (live OR dry-run). Captures
    the mode + entity + target + ok + shopify_id + error so the owner has an
    immutable record of every push. Fail-soft: any audit error is swallowed so it
    can never undo/block the push (mirrors online_store_images._write_audit)."""
    try:
        from ..dependencies import get_audit_repository

        audit = get_audit_repository()
        if audit is None:
            return
        mode = result.get("mode")
        ok = result.get("ok")
        # A failed push (live or dry-run) is a WARNING so it surfaces in the
        # warnings/critical audit views; a clean push is INFO.
        severity = "INFO" if ok else "WARNING"
        audit.create(
            {
                "action": "ONLINE_STORE_PUSH",
                "entity_type": result.get("entity"),
                "entity_id": result.get("target_id"),
                "user_id": current_user.get("user_id"),
                "severity": severity,
                "details": {
                    "mode": mode,
                    "push_action": result.get("action"),
                    "ok": ok,
                    "shopify_id": result.get("shopify_id"),
                    "error": result.get("error"),
                    "reason": result.get("reason"),
                },
            }
        )
    except Exception:  # noqa: BLE001 -- audit must never break the push
        pass


# ---------------------------------------------------------------------------
# Push routes -- one per entity. Each runs the engine + writes a chained audit
# row + returns the structured PushResult.
# ---------------------------------------------------------------------------

@router.post("/product/{product_id}")
async def push_product(
    product_id: str,
    current_user: dict = Depends(require_roles(*_PUSH_ROLES)),
) -> Dict[str, Any]:
    """Push a catalog product (+ its ecom sub-doc + catalog_variants) to Shopify.

    DARK by default -> SIMULATED dry-run (the ProductInput plan, no network call).
    LIVE only behind the three gates. Writes a chained audit row either way. An
    unknown product is a 404; a product with no `ecom` sub-doc is a 400 (it was
    never staged for the online store)."""
    db = _require_db()
    product = _get_catalog_product(db, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")
    if not product.get("ecom"):
        raise HTTPException(
            status_code=400,
            detail="Product has no ecom sub-doc -- stage it for the online store first",
        )
    variants = _get_variants_for_product(db, product)
    result = await shopify_push.push_product(db, product, variants)
    data = result.to_dict()
    _write_audit(data, current_user)
    return {"result": data}


@router.post("/collection/{collection_id}")
async def push_collection(
    collection_id: str,
    current_user: dict = Depends(require_roles(*_PUSH_ROLES)),
) -> Dict[str, Any]:
    """Push an ecom_collections doc to Shopify (collectionCreate/Update + smart
    ruleSet when SMART). DARK by default; LIVE behind the gates. Writes a chained
    audit row. Unknown collection -> 404."""
    db = _require_db()
    repo = _collection_repo(db)
    doc = repo.get_by_id(collection_id) if repo else None
    if doc is None:
        raise HTTPException(status_code=404, detail="Collection not found")
    result = await shopify_push.push_collection(db, doc)
    data = result.to_dict()
    _write_audit(data, current_user)
    return {"result": data}


@router.post("/menu/{menu_id}")
async def push_menu(
    menu_id: str,
    current_user: dict = Depends(require_roles(*_PUSH_ROLES)),
) -> Dict[str, Any]:
    """Push an ecom_menus doc (the Online Store nav / mega-menu) to Shopify
    (menuCreate/Update, mapping the nested item tree). DARK by default; LIVE
    behind the gates. Writes a chained audit row. Unknown menu -> 404."""
    db = _require_db()
    repo = _menu_repo(db)
    doc = repo.get_by_id(menu_id) if repo else None
    if doc is None:
        raise HTTPException(status_code=404, detail="Menu not found")
    result = await shopify_push.push_menu(db, doc)
    data = result.to_dict()
    _write_audit(data, current_user)
    return {"result": data}


@router.post("/image/{image_id}")
async def push_image(
    image_id: str,
    current_user: dict = Depends(require_roles(*_PUSH_ROLES)),
) -> Dict[str, Any]:
    """Push ONE APPROVED product image to Shopify (productCreateMedia onto its
    parent product). DARK by default; LIVE behind the gates. Writes a chained
    audit row. Unknown image -> 404. A non-APPROVED image is NOT a route error
    (the engine returns ok=false action=skip) so the audit still records the
    refusal."""
    db = _require_db()
    repo = _image_repo(db)
    doc = repo.get_by_id(image_id) if repo else None
    if doc is None:
        raise HTTPException(status_code=404, detail="Image not found")
    result = await shopify_push.push_image(db, doc)
    data = result.to_dict()
    _write_audit(data, current_user)
    return {"result": data}


@router.get("/status")
async def push_status(
    current_user: dict = Depends(require_roles(*_PUSH_ROLES)),
) -> Dict[str, Any]:
    """Report the CURRENT push posture + per-entity pushed-vs-pending counts.

    `mode` block: are we DARK or LIVE, and WHY (the three gate components +
    creds-present). `counts` block: per entity, how many docs are already mapped
    to Shopify (pushed) vs still pending (a dirty `locally_modified` row, or one
    with no Shopify id yet). Fail-soft: no DB -> zeros + db_connected False, never
    a 500."""
    db = _get_db()
    mode = shopify_push.push_mode_status(db)
    if db is None:
        return {"mode": mode, "db_connected": False, "counts": _empty_counts()}

    # Counts are computed in Python over the (bounded) ecom collections rather
    # than via nested `ecom.*` / `$exists` Mongo queries. Reasons: (1) the in-memory
    # MockCollection used in no-DB / test mode does not model dot-notation or
    # `$exists`, so a server-side query would silently mis-count there; (2) this is
    # a status/dashboard endpoint (not a hot path) and these collections are small
    # (the PIM master is one row per product, collections/menus are tens of rows),
    # so a single bounded pass is cheap AND exact on BOTH backends. Fail-soft -> the
    # entity block degrades to zeros, never a 500.
    counts = {
        "products": _product_counts(db),
        "collections": _doc_counts(db, "ecom_collections", "shopify_collection_id"),
        "menus": _doc_counts(db, "ecom_menus", "shopify_menu_id"),
        "images": _image_counts(db),
    }
    return {"mode": mode, "db_connected": True, "counts": counts}


def _all_docs(db, name: str) -> List[Dict]:
    """All docs in a collection (Mongo _id stripped). Fail-soft -> []."""
    try:
        rows = list(db[name].find({}, {"_id": 0}))
        for r in rows:
            if isinstance(r, dict):
                r.pop("_id", None)
        return rows
    except Exception:  # noqa: BLE001
        return []


def _product_counts(db) -> Dict[str, int]:
    """staged = catalog_products carrying an `ecom` sub-doc; pushed = those whose
    ecom has a shopify_product_id; pending = those whose ecom is dirty
    (locally_modified). Computed in Python (portable + exact)."""
    staged = pushed = pending = 0
    for doc in _all_docs(db, "catalog_products"):
        ecom = doc.get("ecom")
        if not ecom:
            continue
        staged += 1
        if ecom.get("shopify_product_id"):
            pushed += 1
        if ecom.get("locally_modified"):
            pending += 1
    return {"staged": staged, "pushed": pushed, "pending": pending}


def _doc_counts(db, name: str, shopify_field: str) -> Dict[str, int]:
    """total / pushed (has the shopify id) / pending (locally_modified) for a
    flat ecom collection (ecom_collections, ecom_menus)."""
    total = pushed = pending = 0
    for doc in _all_docs(db, name):
        total += 1
        if doc.get(shopify_field):
            pushed += 1
        if doc.get("locally_modified"):
            pending += 1
    return {"total": total, "pushed": pushed, "pending": pending}


def _image_counts(db) -> Dict[str, int]:
    """approved (push-eligible) / pushed (has shopify_image_id) / pending (APPROVED
    but not yet pushed)."""
    approved = pushed = pending = 0
    for doc in _all_docs(db, "product_images"):
        is_approved = str(doc.get("status") or "").upper() == "APPROVED"
        has_gid = bool(doc.get("shopify_image_id"))
        if is_approved:
            approved += 1
            if not has_gid:
                pending += 1
        if has_gid:
            pushed += 1
    return {"approved": approved, "pushed": pushed, "pending": pending}


def _empty_counts() -> Dict[str, Any]:
    return {
        "products": {"staged": 0, "pushed": 0, "pending": 0},
        "collections": {"total": 0, "pushed": 0, "pending": 0},
        "menus": {"total": 0, "pushed": 0, "pending": 0},
        "images": {"approved": 0, "pushed": 0, "pending": 0},
    }


# ---------------------------------------------------------------------------
# Doc fetch helpers (reuse the Phase 1-4 repositories / catalog access)
# ---------------------------------------------------------------------------

def _get_catalog_product(db, product_id: str) -> Optional[Dict]:
    """Fetch a catalog_products doc by its `id` (the catalog key, never _id).
    Strips the Mongo _id. Fail-soft -> None."""
    try:
        doc = db["catalog_products"].find_one({"id": product_id})
        if doc is not None:
            doc.pop("_id", None)
        return doc
    except Exception:  # noqa: BLE001
        return None


def _get_variants_for_product(db, product: Dict) -> List[Dict]:
    """All catalog_variants whose parent is this product (by parent_product_id or
    parent_sku). Fail-soft -> []."""
    try:
        from database.repositories import CatalogVariantRepository

        repo = CatalogVariantRepository(db["catalog_variants"])
        pid = product.get("id") or product.get("product_id")
        rows = repo.list_by_parent(pid) if pid else []
        if not rows and product.get("sku"):
            # Fall back to parent_sku linkage when the id link wasn't set.
            rows = repo.find_many({"parent_sku": product.get("sku")})
        return rows or []
    except Exception:  # noqa: BLE001
        return []


def _collection_repo(db):
    try:
        from database.repositories import EcomCollectionRepository

        return EcomCollectionRepository(db["ecom_collections"])
    except Exception:  # noqa: BLE001
        return None


def _menu_repo(db):
    try:
        from database.repositories import EcomMenuRepository

        return EcomMenuRepository(db["ecom_menus"])
    except Exception:  # noqa: BLE001
        return None


def _image_repo(db):
    try:
        from database.repositories import ProductImageRepository

        return ProductImageRepository(db["product_images"])
    except Exception:  # noqa: BLE001
        return None
