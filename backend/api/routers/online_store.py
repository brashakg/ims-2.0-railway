"""
IMS 2.0 - Online Store Module Router  (BVI Phase 1 foundation)
==============================================================
The "Online Store" module folds BVI's Shopify PIM into IMS as ONE app. Full
target architecture + phased roadmap: docs/reference/BVI_MERGE_PLAN.md.

THIS IS PHASE-1 FOUNDATION ONLY. The actual PIM features (collections editor,
mega-menu, image design-queue, Shopify push) land in later phases. This router
ships:
  - the module SKELETON mounted at /api/v1/online-store, and
  - a STUB `GET /online-store/summary` that reports module status + the planned
    feature list + live counts (catalog_variants, products-with-ecom).

ROLE GATE (router-level): SUPERADMIN / ADMIN / CATALOG_MANAGER / DESIGN_MANAGER.
SUPERADMIN passes implicitly via require_roles. DESIGN_MANAGER is the new
lowest-privilege role for the ecom design-queue (added to the role matrix in
api/services/rbac_policy.py + database/schemas.py USER_SCHEMA). Sales / cashier /
optometrist / workshop staff get 403.

Everything is FAIL-SOFT: no DB -> counts come back 0 and the module still
reports its status; the summary never 500s.
"""
from __future__ import annotations

from typing import Dict, Optional

from fastapi import APIRouter, Depends

from .auth import require_roles

router = APIRouter()

# Roles allowed into the Online Store module. SUPERADMIN is auto-granted by
# require_roles, so it is not repeated here. Keep this in lock-step with the
# rbac_policy.POLICY rows for every /api/v1/online-store route.
_ECOM_ROLES = ("ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER")

# The differentiator surfaces this module will deliver, in roadmap order
# (BVI_MERGE_PLAN.md section B). Surfaced by the summary so the owner sees the
# plan even before the screens exist. STATUS is "planned" for all of them in
# Phase 1 -- the skeleton is live, the features are not.
_PLANNED_FEATURES = [
    {"key": "catalog_variants", "name": "Variant identity + Shopify mapping", "phase": 1, "status": "foundation"},
    {"key": "collections", "name": "Collections (custom + smart, auto-lineage)", "phase": 2, "status": "planned"},
    {"key": "mega_menu", "name": "Menus / Mega-menu editor", "phase": 3, "status": "planned"},
    {"key": "online_orders", "name": "Online-order ingestion into IMS books", "phase": 3, "status": "planned"},
    {"key": "image_design_queue", "name": "Image design workflow (RAW -> EDITED)", "phase": 4, "status": "planned"},
    {"key": "shopify_push", "name": "Shopify GraphQL push (single-writer, gated)", "phase": 5, "status": "planned"},
]


def _get_db():
    """Return the underlying database object (real pymongo Database or the
    seeded MockDatabase) when connected, else None. Mirrors the fail-soft
    pattern in catalog.py so subscript access (`db[name]`) works on both."""
    try:
        from ..dependencies import get_db

        conn = get_db()
        if conn is not None and getattr(conn, "is_connected", False):
            return conn.db
    except Exception:  # noqa: BLE001
        pass
    return None


def _safe_count(db, name: str, filter_: Optional[Dict] = None) -> int:
    """count_documents on a collection, fail-soft to 0. Works on real Mongo and
    the in-memory MockCollection (both implement count_documents)."""
    if db is None:
        return 0
    try:
        coll = db[name]
        if coll is None:
            return 0
        return int(coll.count_documents(filter_ or {}))
    except Exception:  # noqa: BLE001
        return 0


@router.get("/summary")
async def online_store_summary(
    current_user: dict = Depends(require_roles(*_ECOM_ROLES)),
) -> Dict:
    """Module status + planned feature list + live foundation counts.

    Phase-1 STUB. `catalog_variants` is the count of variant-identity rows;
    `products_with_ecom` is the count of catalog_products carrying the optional
    embedded `ecom` sub-doc. Both are 0 until later phases populate them; the
    endpoint is fail-soft (no DB -> zeros, never a 500).
    """
    db = _get_db()
    counts = {
        "catalog_variants": _safe_count(db, "catalog_variants"),
        # catalog_products that have the optional embedded `ecom` sub-doc.
        "products_with_ecom": _safe_count(
            db, "catalog_products", {"ecom": {"$exists": True}}
        ),
    }
    return {
        "module": "online_store",
        "status": "foundation",  # Phase 1: skeleton live, features planned
        "phase": 1,
        "db_connected": db is not None,
        "shopify_writes_enabled": False,  # IMS_SHOPIFY_WRITES default OFF (gated)
        "planned_features": _PLANNED_FEATURES,
        "counts": counts,
        "blueprint": "docs/reference/BVI_MERGE_PLAN.md",
    }
