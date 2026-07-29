"""
IMS 2.0 - Online Store Module Router  (BVI Phase 1 foundation)
==============================================================
The "Online Store" module folds BVI's Shopify PIM into IMS as ONE app. Full
target architecture + phased roadmap: docs/reference/BVI_MERGE_PLAN.md.

STATUS 2026-07-05: phases 1-5 of the blueprint are SHIPPED (variants,
collections, menus, order ingestion, image queue, gated Shopify push). This
router ships:
  - the module skeleton mounted at /api/v1/online-store, and
  - `GET /online-store/summary` reporting the feature list, live counts AND the
    real push gate (shopify_push.push_mode_status) -- no more hardcoded
    "foundation/phase 1" values.

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

# The module's feature list in roadmap order (BVI_MERGE_PLAN.md section B).
# 2026-07-05 status refresh: phases 1-5 all SHIPPED across prior sessions --
# only the Phase-6 baton cutover (arm the gates, flip BVI's writer off) is
# pending, and that is an owner/config action, not code.
_PLANNED_FEATURES = [
    {
        "key": "catalog_variants",
        "name": "Variant identity + Shopify mapping",
        "phase": 1,
        "status": "live",
    },
    {
        "key": "collections",
        "name": "Collections (custom + smart, auto-lineage)",
        "phase": 2,
        "status": "live",
    },
    {
        "key": "mega_menu",
        "name": "Menus / Mega-menu editor",
        "phase": 3,
        "status": "live",
    },
    {
        "key": "online_orders",
        "name": "Online-order ingestion into IMS books",
        "phase": 3,
        "status": "live",
    },
    {
        "key": "image_design_queue",
        "name": "Image design workflow (RAW -> EDITED)",
        "phase": 4,
        "status": "live",
    },
    {
        # Engine + screens are BUILT; every push runs as a dry-run PLAN until
        # the Phase-6 triple gate is armed (IMS_SHOPIFY_WRITES=1 +
        # DISPATCH_MODE=live + Shopify creds in the integrations collection).
        "key": "shopify_push",
        "name": "Shopify GraphQL push (single-writer, gated)",
        "phase": 5,
        "status": "built-gated",
    },
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


def _safe_count(
    db,
    name: str,
    filter_: Optional[Dict] = None,
    failures: Optional[list] = None,
) -> int:
    """count_documents on a collection, fail-soft to 0. Works on real Mongo and
    the in-memory MockCollection (both implement count_documents).

    When `failures` is passed, a mid-request error appends the collection name
    to it so the caller can report degraded=True -- otherwise the fail-soft zero
    is indistinguishable from a genuinely-empty business state (OS-021)."""
    if db is None:
        return 0
    try:
        coll = db[name]
        if coll is None:
            return 0
        return int(coll.count_documents(filter_ or {}))
    except Exception:  # noqa: BLE001
        if failures is not None:
            failures.append(name)
        return 0


def _storefront_postures(db, push_mode: Optional[Dict]) -> list:
    """Per-storefront push posture rows for the summary (OS-034).

    Names come from the storefronts registry (services.storefronts) -- never
    page-level string literals -- falling back to the BV seed definition when
    the registry has no rows yet, so the payload always names at least the
    default storefront. Posture per row: is_live = writes gate AND dispatch
    gate AND that storefront's credentials. The default storefront reuses
    push_mode's already-resolved creds_present (no second credential lookup);
    non-default storefronts (WizOpt, once registered) resolve their own and
    stay DARK until configured. Fail-soft -> [] (the summary never 500s)."""
    try:
        from ..services import storefronts as sf_registry

        rows = sf_registry.list_storefronts(db) if db is not None else []
        if not rows:
            rows = [dict(sf_registry.BV_STOREFRONT)]
        writes = bool(push_mode and push_mode.get("writes_enabled"))
        dispatch_live = bool(push_mode and push_mode.get("dispatch_mode") == "live")
        default_creds = bool(push_mode and push_mode.get("creds_present"))
        out = []
        for row in rows:
            sid = str(row.get("storefront_id") or "")
            is_default = bool(row.get("is_default")) or (
                sid == sf_registry.DEFAULT_STOREFRONT_ID
            )
            if is_default:
                creds = default_creds
            else:
                try:
                    from ..services.shopify_push import _has_shopify_creds

                    creds = _has_shopify_creds(db, sid)
                except Exception:  # noqa: BLE001
                    creds = False
            out.append(
                {
                    "storefront_id": sid,
                    "name": row.get("name") or sid,
                    "is_default": is_default,
                    "creds_present": creds,
                    "is_live": bool(writes and dispatch_live and creds),
                }
            )
        return out
    except Exception:  # noqa: BLE001 -- posture rows must never 500 the summary
        return []


@router.get("/summary")
async def online_store_summary(
    current_user: dict = Depends(require_roles(*_ECOM_ROLES)),
) -> Dict:
    """Module status + planned feature list + live counts.

    BVI-9 fix: returns the full count set the FE OnlineStoreCounts interface
    reads (`products`, `variants`, `collections`, `menus`,
    `images_pending_design`, `customers`, `orders`). Each _safe_count is 0 if
    the collection doesn't exist yet (fail-soft, no 500).
    """
    db = _get_db()
    # Mid-request count failures -> degraded=True (OS-021): the FE renders an
    # honest "figures may be incomplete" note instead of presenting fail-soft
    # zeros as real business counts.
    count_failures: list = []
    counts: Dict = {
        # Products / PIM card -- catalog_products with ecom sub-doc.
        "products": _safe_count(
            db, "catalog_products", {"ecom": {"$exists": True}}, count_failures
        ),
        # Variant tier (catalog_variants collection).
        "variants": _safe_count(db, "catalog_variants", None, count_failures),
        # Collections (Phase 2 -- ecom_collections).
        "collections": _safe_count(db, "ecom_collections", None, count_failures),
        # Menus / mega-menu (Phase 3 -- ecom_menus).
        "menus": _safe_count(db, "ecom_menus", None, count_failures),
        # Images awaiting design work (Phase 4 -- product_images, QUEUED status).
        "images_pending_design": _safe_count(
            db, "product_images", {"design_status": "QUEUED"}, count_failures
        ),
        # Online customers (joined from Shopify -- have shopify_customer_id set).
        "customers": _safe_count(
            db,
            "customers",
            {"shopify_customer_id": {"$exists": True, "$ne": None}},
            count_failures,
        ),
        # Online orders (Phase 3b -- orders ingested from Shopify webhooks).
        "orders": _safe_count(db, "orders", {"channel": "ONLINE"}, count_failures),
    }
    # DRAFT vs PUBLISHED breakdown + text-only (no images) among staged docs.
    # Makes the owner's 2,032-draft publish decision VISIBLE in the UI without
    # any new write semantics. Same route + same roles as this summary, so NO
    # new rbac_policy row is needed. Simple count_documents at ~4.4k docs.
    products_ecom = {
        # ecom.status is the field the Shopify push honours (DRAFT->DRAFT,
        # PUBLISHED->ACTIVE). A status query implies the ecom sub-doc exists.
        "draft": _safe_count(
            db, "catalog_products", {"ecom.status": "DRAFT"}, count_failures
        ),
        "published": _safe_count(
            db, "catalog_products", {"ecom.status": "PUBLISHED"}, count_failures
        ),
        # Everything staged for online (has an ecom sub-doc) -- mirrors counts.products.
        "staged_total": _safe_count(
            db, "catalog_products", {"ecom": {"$exists": True}}, count_failures
        ),
        # Staged but carrying no product images (array empty/null/missing) --
        # the text-only drafts left after the 2026-07-20 image loss. $in with
        # [None, []] matches missing, null, and the empty array.
        "text_only": _safe_count(
            db,
            "catalog_products",
            {"ecom": {"$exists": True}, "images": {"$in": [None, []]}},
            count_failures,
        ),
    }
    # Report the REAL push gate instead of hardcoded values. OS-033: `status`
    # and `phase` DERIVE from the live gate -- "live"/6 once the Phase-6 cutover
    # is armed (2026-07-20), "cutover-ready"/5 while the gates are dark. The
    # hub can no longer show "cutover-ready" next to "Shopify push: LIVE".
    try:
        from ..services import shopify_push

        push_mode = shopify_push.push_mode_status(db)
    except Exception:  # noqa: BLE001 - status endpoint must never 500
        push_mode = None
    is_live = bool(push_mode and push_mode.get("is_live"))
    # Derive the shopify_push feature status the same way (copy, never mutate
    # the module-level constant).
    planned_features = [dict(f) for f in _PLANNED_FEATURES]
    for feat in planned_features:
        if feat.get("key") == "shopify_push":
            feat["status"] = "live" if is_live else "built-gated"
    return {
        "module": "online_store",
        "status": "live" if is_live else "cutover-ready",
        "phase": 6 if is_live else 5,
        "db_connected": db is not None,
        # OS-021: True when any count failed mid-request -- the zeros in
        # counts/products_ecom are then fail-soft artefacts, not business truth.
        "degraded": bool(count_failures),
        "shopify_writes_enabled": is_live,
        "push_mode": push_mode,
        # OS-034: per-storefront posture rows (BV live / WO dark once
        # registered), names from the storefronts registry.
        "storefronts": _storefront_postures(db, push_mode),
        "planned_features": planned_features,
        "counts": counts,
        "products_ecom": products_ecom,
        "blueprint": "docs/reference/BVI_MERGE_PLAN.md",
    }


@router.get("/stock-tally")
async def online_store_stock_tally(
    current_user: dict = Depends(require_roles(*_ECOM_ROLES)),
) -> Dict:
    """READ-ONLY reconciliation dashboard (BVI Phase 5 "Stock tally").

    Per online-listed SKU, compares what the storefront lists against the real
    physical on-hand and what is already reserved, and flags any SKU that lists
    MORE than is actually free to sell (oversell risk). It also suggests a
    conservative buffer to keep off the online listing -- a SUGGESTION only.

    This surface is strictly read-only: it REUSES the on-hand / reserved
    aggregations (services.online_sync_health) + the IMS Mongo online catalog
    (post-BVI truth) and NEVER mutates stock, NEVER reserves a unit. The listed
    quantity is read LIVE from Shopify (creds-gated, read-only); when the live
    read is unavailable, online_listed_qty is null (honest unknown -- never a
    fake 0) and summary.listed_qty_live=False. The write-path allocation
    (marking units RESERVED on order ingest, excluding them from on-hand) is a
    deliberate, separately-reviewed follow-up (needs an atomic claim +
    idempotency) and is NOT part of this endpoint.

    Fail-soft: no DB -> an empty envelope. Never 500s.
    """
    from ..services.online_sync_health import stock_tally_live

    return await stock_tally_live(_get_db())


@router.get("/store-health")
async def online_store_health(
    current_user: dict = Depends(require_roles(*_ECOM_ROLES)),
) -> Dict:
    """Pre-cutover readiness dashboard (Phase 5 "Store health" card).

    Consolidates the scattered readiness signals into ONE payload:
      - orphan SKUs (no Shopify mapping / not in a collection / no spine link),
      - per-attribute + overall coverage (HSN, category, brand, barcode, image),
      - barcode match rate (present AND unique),
      - a composite readiness_pct (0-100) + a concrete fixes_needed list,
      - the existing IMS-vs-Shopify parity counts (reused, not re-derived).

    Read-only + FAIL-SOFT: no DB -> a fully-zeroed envelope, never 500s. Gated to
    the ecom role set (SUPERADMIN/ADMIN/CATALOG_MANAGER/DESIGN_MANAGER) like the
    rest of the module; catalogued in rbac_policy.POLICY."""
    from ..services.store_health import store_health_envelope

    return store_health_envelope(_get_db())
