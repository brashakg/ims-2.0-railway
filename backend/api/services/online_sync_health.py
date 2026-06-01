"""
IMS 2.0 - Online-store sync health (read-only diagnostics)
==========================================================
A single fail-soft summary of the IMS <-> online-store (BVI/Shopify) bridge for
the SUPERADMIN integrations/status surface (council D10). It answers three
operational questions at a glance:

  1. last_shopify_sync    -- when did the NEXUS agent last successfully sync
                             Shopify? (reads the `sync_runs` collection)
  2. pending_reconcile    -- how many SKUs are currently mis-listed online
                             (oversell-risk + over-allocated)? This REUSES the
                             exact reconcile logic behind
                             GET /catalog/online-stock-reconcile
                             (services.stock_allocation.reconcile_items over
                             in-store on-hand vs online-listed stock).
  3. failed_webhooks      -- how many inbound webhook envelopes failed to
                             process or were skipped (reads `webhook_inbox`).

Contract (mirrors the rest of the consolidation bridge):
- 100% FAIL-SOFT. Missing DB / driver / e-commerce Postgres unconfigured ->
  zeros + flags, NEVER raises, never 500s the status page.
- READ-ONLY. Touches no write path.
- No live Shopify/network call is required: the Shopify-side signal comes from
  the locally-recorded `sync_runs` rows and the (already fail-soft) Postgres
  read in services.online_catalog.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Cap how many products we scan for the reconcile diff so the status tile stays
# cheap even on a large catalog. Matches the catalog endpoint's default ceiling.
_RECONCILE_SCAN_LIMIT = 1000

# On-hand availability statuses (kept in sync with inventory._on_hand_by_product).
_AVAILABLE_STATUSES = ["AVAILABLE", "available", "IN_STOCK", "in_stock"]


def _coll(db, name: str):
    """Subscript collection access that works on both a real pymongo Database
    and the in-memory MockDatabase (which lacks .get_collection)."""
    try:
        return db[name] if db is not None else None
    except Exception:  # noqa: BLE001
        return None


def last_shopify_sync(db) -> Dict[str, Any]:
    """Most-recent NEXUS sync_run row for Shopify. Returns
    {found, ok, ran_at, items_synced, error}. Fail-soft -> {found: False}."""
    out: Dict[str, Any] = {"found": False}
    coll = _coll(db, "sync_runs")
    if coll is None:
        return out
    try:
        # NEXUS writes one row per integration per tick: integration='shopify',
        # ok (bool), ran_at (ISO-8601 string), items_synced, error.
        cursor = (
            coll.find({"integration": "shopify"}, {"_id": 0})
            .sort("ran_at", -1)
            .limit(1)
        )
        rows = list(cursor)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[SYNC_HEALTH] sync_runs read failed: %s", exc)
        return out
    if not rows:
        return out
    row = rows[0]
    return {
        "found": True,
        "ok": bool(row.get("ok")),
        "ran_at": row.get("ran_at"),
        "items_synced": int(row.get("items_synced") or 0),
        "error": row.get("error") or None,
    }


def last_successful_shopify_sync_at(db) -> Optional[str]:
    """ran_at (ISO string) of the most-recent SUCCESSFUL Shopify sync, or None.
    Distinct from last_shopify_sync, which may report a failed last attempt."""
    coll = _coll(db, "sync_runs")
    if coll is None:
        return None
    try:
        rows = list(
            coll.find(
                {"integration": "shopify", "ok": True}, {"_id": 0, "ran_at": 1}
            )
            .sort("ran_at", -1)
            .limit(1)
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[SYNC_HEALTH] successful-sync read failed: %s", exc)
        return None
    return rows[0].get("ran_at") if rows else None


def _on_hand_by_product(
    db, product_ids: List[str], store_id: Optional[str] = None
) -> Dict[str, int]:
    """Count on-hand units per product from the serialized `stock_units`
    collection (one row per unit). Self-contained mirror of
    inventory._on_hand_by_product so this service has no router dependency.
    Fail-soft -> {}."""
    if db is None or not product_ids:
        return {}
    match: Dict[str, Any] = {
        "product_id": {"$in": list(product_ids)},
        "$or": [
            {"status": {"$in": _AVAILABLE_STATUSES}},
            {"status": {"$exists": False}},
            {"status": None},
        ],
    }
    if store_id:
        match["store_id"] = store_id
    out: Dict[str, int] = {}
    try:
        coll = _coll(db, "stock_units")
        if coll is None:
            return {}
        for row in coll.aggregate(
            [
                {"$match": match},
                {
                    "$group": {
                        "_id": "$product_id",
                        "n": {"$sum": {"$ifNull": ["$quantity", 1]}},
                    }
                },
            ]
        ):
            out[row.get("_id")] = int(row.get("n") or 0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[SYNC_HEALTH] on-hand aggregate failed: %s", exc)
        return {}
    return out


def pending_reconcile_summary(
    db, safety_buffer: int = 0, limit: int = _RECONCILE_SCAN_LIMIT
) -> Dict[str, Any]:
    """Count SKUs whose online listing is out of sync with physical on-hand,
    REUSING the exact reconcile logic behind /catalog/online-stock-reconcile.

    Returns a small summary: total scanned, oversell_risk, over_allocated,
    pending (= oversell_risk + over_allocated), oversell_risk_units, plus
    online_configured. Fail-soft -> zeros."""
    from .online_catalog import ecommerce_db_configured, online_status_for_skus
    from . import stock_allocation

    base = {
        "scanned": 0,
        "oversell_risk": 0,
        "over_allocated": 0,
        "pending": 0,
        "oversell_risk_units": 0,
        "online_configured": ecommerce_db_configured(),
    }
    if db is None:
        return base

    try:
        products = list(
            _coll(db, "products")
            .find(
                {"sku": {"$nin": [None, ""]}, "is_active": {"$ne": False}},
                {"_id": 0, "product_id": 1, "sku": 1},
            )
            .limit(limit)
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[SYNC_HEALTH] products scan failed: %s", exc)
        return base
    if not products:
        return base

    pids = [p.get("product_id") for p in products if p.get("product_id")]
    on_hand = _on_hand_by_product(db, pids)
    skus = [p.get("sku") for p in products if p.get("sku")]
    online = online_status_for_skus(skus)  # {} when Postgres unconfigured

    items = []
    for p in products:
        sku = p.get("sku")
        o = online.get(sku, {})
        items.append(
            {
                "sku": sku,
                "in_store": on_hand.get(p.get("product_id"), 0),
                "online": int(o.get("online_stock") or 0),
                "is_online": bool(o.get("online")),
            }
        )

    result = stock_allocation.reconcile_items(items, safety_buffer=safety_buffer)
    summary = result.get("summary", {})
    oversell = int(summary.get("oversell_risk") or 0)
    over_alloc = int(summary.get("over_allocated") or 0)
    return {
        "scanned": int(summary.get("total") or 0),
        "oversell_risk": oversell,
        "over_allocated": over_alloc,
        "pending": oversell + over_alloc,
        "oversell_risk_units": int(summary.get("oversell_risk_units") or 0),
        "online_configured": ecommerce_db_configured(),
    }


def failed_webhook_summary(db) -> Dict[str, Any]:
    """Count inbound webhook envelopes that failed to process or were skipped,
    from the `webhook_inbox` collection.

    A row is 'failed' if NEXUS recorded a handler_error, and 'skipped' if the
    receiver couldn't process it (e.g. skipped_reason=secret_not_configured).
    Also reports how many envelopes are still unprocessed (pending drain).
    Fail-soft -> zeros."""
    out = {"failed": 0, "skipped": 0, "pending": 0}
    coll = _coll(db, "webhook_inbox")
    if coll is None:
        return out
    try:
        out["failed"] = int(
            coll.count_documents(
                {"handler_error": {"$exists": True, "$nin": [None, ""]}}
            )
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[SYNC_HEALTH] webhook failed-count failed: %s", exc)
    try:
        out["skipped"] = int(
            coll.count_documents(
                {"skipped_reason": {"$exists": True, "$nin": [None, ""]}}
            )
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[SYNC_HEALTH] webhook skipped-count failed: %s", exc)
    try:
        out["pending"] = int(coll.count_documents({"processed": {"$ne": True}}))
    except Exception as exc:  # noqa: BLE001
        logger.warning("[SYNC_HEALTH] webhook pending-count failed: %s", exc)
    return out


def sync_health(db, safety_buffer: int = 0) -> Dict[str, Any]:
    """Assemble the full online-store sync-health summary. Fully fail-soft:
    each section degrades to its empty/zero shape independently, so the status
    tile always renders."""
    from .online_catalog import ecommerce_db_configured

    return {
        "online_configured": ecommerce_db_configured(),
        "last_shopify_sync": last_shopify_sync(db),
        "last_successful_shopify_sync_at": last_successful_shopify_sync_at(db),
        "reconcile": pending_reconcile_summary(db, safety_buffer=safety_buffer),
        "webhooks": failed_webhook_summary(db),
    }
