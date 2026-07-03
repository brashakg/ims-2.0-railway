"""
IMS 2.0 - Collection Insights router  (Collections Phase 1, Track 2)
====================================================================
Read-only analytics endpoints over the materialised `collection_products`
view + stock_units + orders. Service logic lives in
api/services/collection_insights.py; this router only handles auth, store
scoping and envelopes.

  GET  /api/v1/collections/insights/summary                per-collection roll-up (batched)
  POST /api/v1/collections/preview                         evaluate UNSAVED rules
  GET  /api/v1/collections/{collection_id}/insights        KPI block for one collection
  GET  /api/v1/collections/{collection_id}/insights/stores per-store breakdown

Mounted at /api/v1/collections AFTER collections_browse (same prefix). The
literal paths (/insights/summary, /preview) are declared BEFORE the
/{collection_id}/... wildcards and do not shadow any browse route
(GET '' | GET /{handle}/products | POST /{handle}/refresh).

ROLE GATE: ADMIN / AREA_MANAGER / STORE_MANAGER / CATALOG_MANAGER (SUPERADMIN
auto-passes via require_roles). Catalogued in rbac_policy.POLICY with the same
set.

STORE SCOPING: a caller whose roles include none of SUPERADMIN / ADMIN /
AREA_MANAGER / CATALOG_MANAGER (i.e. a STORE_MANAGER here) is FORCED to their
token's active_store_id on every endpoint -- a requested ?store_id= is
overridden, not 403'd. The effective store is echoed back as `store_id`.

Everything is FAIL-SOFT (mirrors collections_browse): no DB -> zeroed
envelopes, never a 500. An unknown collection_id on a live DB -> 404.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .auth import require_roles
from ..services import collection_insights as _svc

router = APIRouter()

# Roles allowed into the insights surface (SUPERADMIN auto-passes via
# require_roles but IS listed in the POLICY rows, per that file's convention).
_INSIGHT_ROLES = ("ADMIN", "AREA_MANAGER", "STORE_MANAGER", "CATALOG_MANAGER")

# Roles that may look across stores; anyone else is pinned to their own store.
_HQ_ROLES = frozenset({"SUPERADMIN", "ADMIN", "AREA_MANAGER", "CATALOG_MANAGER"})

# How many collections the batched summary will evaluate before ranking.
# Must cover the full catalogue: ~1,160 BVI-migrated collections exist, and
# the list page's "Show all" toggle needs every row (le on the limit query
# param matches).
_SUMMARY_SCAN_MAX = 2000


def _get_db():
    """Live DB object (real pymongo Database or seeded MockDatabase) when
    connected, else None. Mirrors collections_browse._get_db."""
    try:
        from ..dependencies import get_db

        conn = get_db()
        if conn is not None and getattr(conn, "is_connected", False):
            return conn.db
    except Exception:  # noqa: BLE001
        pass
    return None


def _repo(db):
    try:
        from database.repositories import EcomCollectionRepository

        return EcomCollectionRepository(db["ecom_collections"])
    except Exception:  # noqa: BLE001
        return None


def _scope_store(current_user: dict, store_id: Optional[str]) -> Optional[str]:
    """Force non-HQ callers onto their own store. HQ roles keep whatever they
    asked for (None = chain-wide)."""
    roles = set((current_user or {}).get("roles") or [])
    if roles & _HQ_ROLES:
        return store_id or None
    return (current_user or {}).get("active_store_id")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class PreviewBody(BaseModel):
    """Unsaved SMART-collection rules to evaluate (IMS or Shopify shape --
    normalised on read, same as the stored rules)."""

    rules: List[Dict] = Field(default_factory=list)
    disjunctive: bool = False


# ---------------------------------------------------------------------------
# literal paths FIRST (before the /{collection_id} wildcards)
# ---------------------------------------------------------------------------


@router.get("/insights/summary")
async def collections_insights_summary(
    limit: int = Query(100, ge=1, le=2000),
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(require_roles(*_INSIGHT_ROLES)),
):
    """Batched per-collection roll-up (members / on-hand / stock value /
    sold_30d), sorted by sold_30d desc. No N+1: one membership read, one stock
    rollup and one movement aggregation over the union."""
    store = _scope_store(current_user, store_id)
    envelope = {
        "collections": [],
        "total": 0,
        "store_id": store,
        "as_of": _now_iso(),
    }
    db = _get_db()
    if db is None:
        return envelope
    try:
        docs = list(db["ecom_collections"].find({}).limit(_SUMMARY_SCAN_MAX))
    except Exception:  # noqa: BLE001
        try:
            docs = list(db["ecom_collections"].find({}))[:_SUMMARY_SCAN_MAX]
        except Exception:  # noqa: BLE001
            return envelope
    rows = _svc.summary(db, docs, store_id=store)[:limit]
    envelope["collections"] = rows
    envelope["total"] = len(rows)
    return envelope


@router.post("/preview")
async def preview_collection_rules(
    payload: PreviewBody,
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(require_roles(*_INSIGHT_ROLES)),
):
    """Evaluate UNSAVED rules over the live catalogue (the same
    products + catalog_products union the materializer resolves against,
    scan-capped) and report the would-be membership + its on-hand stock and a
    12-row sample. Nothing is persisted."""
    store = _scope_store(current_user, store_id)
    db = _get_db()
    result = _svc.preview(
        db, payload.rules, disjunctive=payload.disjunctive, store_id=store
    )
    result["store_id"] = store
    return result


# ---------------------------------------------------------------------------
# per-collection paths
# ---------------------------------------------------------------------------


def _load_collection_or_404(db, collection_id: str) -> Dict:
    repo = _repo(db)
    doc = repo.get_by_id(collection_id) if repo is not None else None
    if doc is None:
        raise HTTPException(
            status_code=404, detail=f"Collection not found: {collection_id}"
        )
    return doc


def _zero_kpis() -> Dict:
    return {
        "members": 0,
        "units_on_hand": 0,
        "stock_value": 0.0,
        "value_basis": None,
        "stock_value_mrp": 0.0,
        "sold": {"d7": 0, "d30": 0, "d90": 0},
        "revenue_30d": 0.0,
        "margin_30d": None,
        "sell_through_30d": None,
        "days_of_cover": None,
        "membership_capped": False,
        "materialized_at": None,
    }


@router.get("/{collection_id}/insights")
async def collection_insights(
    collection_id: str,
    days: int = Query(
        30,
        ge=1,
        le=90,
        description=(
            "Reserved window control; the KPI block currently reports the "
            "fixed d7/d30/d90 windows and echoes this back as window_days."
        ),
    ),
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(require_roles(*_INSIGHT_ROLES)),
):
    """KPI block for one collection: membership, on-hand units + valuation
    (labelled basis), d7/d30/d90 sold, 30-day revenue/margin, sell-through and
    days-of-cover. 404 for an unknown collection on a live DB; zeroed envelope
    when no DB is available (fail-soft)."""
    store = _scope_store(current_user, store_id)
    db = _get_db()
    if db is None:
        return {
            "collection_id": collection_id,
            "title": None,
            **_zero_kpis(),
            "store_id": store,
            "window_days": days,
        }
    doc = _load_collection_or_404(db, collection_id)
    block = _svc.kpis(db, doc, store_id=store)
    return {
        "collection_id": collection_id,
        "title": doc.get("title") or doc.get("name") or doc.get("handle"),
        **block,
        "store_id": store,
        "window_days": days,
    }


@router.get("/{collection_id}/insights/stores")
async def collection_insights_stores(
    collection_id: str,
    current_user: dict = Depends(require_roles(*_INSIGHT_ROLES)),
):
    """Per-store breakdown for one collection (on-hand, valuation + basis,
    sold_30d, sell-through, days-of-cover). A store-pinned caller sees only
    their own store's row. Store names resolve from the `stores` collection,
    failing soft to the raw id."""
    store = _scope_store(current_user, None)
    db = _get_db()
    if db is None:
        return {
            "collection_id": collection_id,
            "title": None,
            "stores": [],
            "store_id": store,
        }
    doc = _load_collection_or_404(db, collection_id)
    rows = _svc.store_breakdown(db, doc, store_id=store)
    return {
        "collection_id": collection_id,
        "title": doc.get("title") or doc.get("name") or doc.get("handle"),
        "stores": rows,
        "store_id": store,
    }
