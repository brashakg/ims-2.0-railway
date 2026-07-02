"""
IMS 2.0 - Online-store sync health (read-only diagnostics)
==========================================================
A single fail-soft summary of the IMS <-> online-store (BVI/Shopify) bridge for
the SUPERADMIN integrations/status surface (council D10). It answers:

  1. last_shopify_sync    -- when did NEXUS last successfully sync Shopify?
  2. pending_reconcile    -- SKUs mis-listed online (oversell-risk +
                             over-allocated). Reuses /catalog/online-stock-reconcile.
  3. failed_webhooks      -- inbound webhook envelopes that failed or were skipped.
  4. drift                -- gids that Shopify updated AFTER our last_pushed_at
                             (dual-writer violation detector, Step 3).
  5. parity              -- catalog counts vs what has a Shopify gid (Step 6a).
  6. uploads_audit        -- product images still pointing at /uploads/ local paths
                             (Step 6b; hard cutover prereq).

Additional callables (NEXUS / endpoint use):
  detect_drift(db, limit)          -- async, per-gid Shopify updatedAt check.
  repush_oversell_risk(db, dry_run)-- async, re-push absolute inv for oversell SKUs.
  parity_summary(db)               -- sync, IMS-vs-Shopify catalog count diff.
  uploads_image_audit(db)          -- sync, images with local /uploads/ URLs.

Contract (mirrors the rest of the consolidation bridge):
- 100% FAIL-SOFT. Missing DB / driver / e-commerce Postgres unconfigured ->
  zeros + flags, NEVER raises, never 500s the status page.
- READ-ONLY for detect_drift / parity / uploads_audit.
  repush_oversell_risk respects the TRIPLE gate; dry_run=True is the safe default.
- The Shopify drift read uses shopify_push._graphql (the single network boundary).
  No Shopify creds -> checked:False, never raises.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
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
            coll.find({"integration": "shopify", "ok": True}, {"_id": 0, "ran_at": 1})
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


def _reserved_by_product(
    db, product_ids: List[str], store_id: Optional[str] = None
) -> Dict[str, int]:
    """Count RESERVED units per product from the serialized `stock_units`
    collection (one row per unit). Mirrors _on_hand_by_product but for the
    RESERVED status -- the same signal the inventory Stock Ledger rolls up as
    `reserved` (see routers/inventory._build_store_ledger). Read-only,
    fail-soft -> {}.

    NOTE (Phase 5 scope): today NOTHING in the online path writes RESERVED on
    order ingest -- that write-side allocation is the DEFERRED follow-up. This
    reader is here so the tally already reflects any RESERVED units the rest of
    IMS creates (e.g. a POS hold), and so the sellable math is correct the day
    the write-path lands. It never mutates stock."""
    if db is None or not product_ids:
        return {}
    match: Dict[str, Any] = {
        "product_id": {"$in": list(product_ids)},
        "status": "RESERVED",
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
        logger.warning("[STOCK_TALLY] reserved aggregate failed: %s", exc)
        return {}
    return out


def _recommended_buffer(on_hand: int) -> int:
    """A conservative reserve to keep OFF the online listing so a walk-in sale
    can't strand an online order. Rule: keep max(1, ceil(5% of on-hand)) units
    back -- but never suggest reserving more than what is on hand. A suggestion
    only; NOTHING here enforces it (the write-path allocation is deferred)."""
    on_hand = int(on_hand or 0)
    if on_hand <= 0:
        return 0
    from math import ceil

    return min(on_hand, max(1, ceil(on_hand * 0.05)))


def stock_tally_summary(
    db, limit: int = _RECONCILE_SCAN_LIMIT
) -> Dict[str, Any]:
    """READ-ONLY per-SKU reconciliation of online-listed qty vs real on-hand vs
    already-reserved -- the Online Store "Stock tally" dashboard (BVI Phase 5).

    For each online-eligible SKU it reports:
      - online_listed_qty : what the storefront currently lists (Shopify/BVI)
      - on_hand           : AVAILABLE serialized stock_units (reuses
                            _on_hand_by_product -- NOT re-derived)
      - reserved          : RESERVED serialized stock_units (reuses
                            _reserved_by_product)
      - sellable          : max(0, on_hand - reserved)
      - recommended_buffer: a conservative reserve suggestion (not enforced)
      - oversell_risk     : online_listed_qty > sellable  (the admin.py
                            oversell-risk rule: online listing exceeds what is
                            actually free to sell)

    Plus a summary {skus_checked, at_risk_count, total_online_listed,
    total_on_hand, total_reserved, total_sellable, online_configured}.

    100% read-only + fail-soft: no DB -> empty envelope; a Postgres that is
    unconfigured -> online_listed_qty 0 everywhere (online_configured=False so
    the UI can say so). NEVER mutates stock, NEVER reserves a unit."""
    from .online_catalog import ecommerce_db_configured, online_status_for_skus

    base: Dict[str, Any] = {
        "items": [],
        "summary": {
            "skus_checked": 0,
            "at_risk_count": 0,
            "total_online_listed": 0,
            "total_on_hand": 0,
            "total_reserved": 0,
            "total_sellable": 0,
            "online_configured": ecommerce_db_configured(),
        },
    }
    if db is None:
        return base

    try:
        products = list(
            _coll(db, "products")
            .find(
                {"sku": {"$nin": [None, ""]}, "is_active": {"$ne": False}},
                {"_id": 0, "product_id": 1, "sku": 1, "name": 1},
            )
            .limit(limit)
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[STOCK_TALLY] products scan failed: %s", exc)
        return base
    if not products:
        return base

    pids = [p.get("product_id") for p in products if p.get("product_id")]
    on_hand = _on_hand_by_product(db, pids)
    reserved = _reserved_by_product(db, pids)
    skus = [p.get("sku") for p in products if p.get("sku")]
    online = online_status_for_skus(skus)  # {} when Postgres unconfigured

    items: List[Dict[str, Any]] = []
    at_risk = 0
    tot_listed = tot_on_hand = tot_reserved = tot_sellable = 0

    for p in products:
        sku = p.get("sku")
        pid = p.get("product_id")
        o = online.get(sku, {})
        # Only assess SKUs that are actually listed online. A product that is
        # not online can't oversell online, so it is skipped from the tally
        # (the reconcile screen elsewhere shows the full catalog).
        if not bool(o.get("online")):
            continue
        oh = int(on_hand.get(pid, 0) or 0)
        rv = int(reserved.get(pid, 0) or 0)
        sellable = max(0, oh - rv)
        listed = int(o.get("online_stock") or 0)
        risk = listed > sellable
        if risk:
            at_risk += 1
        tot_listed += listed
        tot_on_hand += oh
        tot_reserved += rv
        tot_sellable += sellable
        items.append(
            {
                "sku": sku,
                "name": p.get("name") or "",
                "online_listed_qty": listed,
                "on_hand": oh,
                "reserved": rv,
                "sellable": sellable,
                "recommended_buffer": _recommended_buffer(oh),
                "oversell_risk": risk,
            }
        )

    # Worst first: oversell-risk rows on top, then by how far over they list.
    items.sort(
        key=lambda r: (
            0 if r["oversell_risk"] else 1,
            -(r["online_listed_qty"] - r["sellable"]),
        )
    )

    return {
        "items": items,
        "summary": {
            "skus_checked": len(items),
            "at_risk_count": at_risk,
            "total_online_listed": tot_listed,
            "total_on_hand": tot_on_hand,
            "total_reserved": tot_reserved,
            "total_sellable": tot_sellable,
            "online_configured": ecommerce_db_configured(),
        },
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


def fulfillment_store_health(db) -> Dict[str, Any]:
    """R6 guard: does the resolved ONLINE fulfillment store actually carry
    serialized stock_units?

    An online order decrements physical stock at the fulfillment store
    (shopify_ingest._mark_units_sold -> StockRepository on db.stock_units, status
    "AVAILABLE"). If that resolves to the virtual billing bucket (BV-ONLINE-01)
    or any store holding ZERO available units, every online-order decrement
    SILENTLY no-ops -> the online listing can oversell physical stock. This
    surfaces that misconfiguration BEFORE the cutover instead of after the first
    lost sale.

    Resolution mirrors shopify_ingest._online_fulfillment_store_id: the
    ONLINE_FULFILLMENT_STORE_ID env wins, else the resolved online billing store
    (integration config / ONLINE_STORE_ID / settings / primary store / the
    BV-ONLINE-01 default).

    Read-only + fail-soft -> never raises, never 500s the status tile.
    """
    import os

    out: Dict[str, Any] = {
        "checked": False,
        "store_id": None,
        "source": None,
        "available_units": 0,
        "is_virtual_default": False,
        "warning": None,
    }

    store_id = (os.getenv("ONLINE_FULFILLMENT_STORE_ID") or "").strip()
    source = "ONLINE_FULFILLMENT_STORE_ID" if store_id else None
    if not store_id:
        try:
            from .online_order_mapper import _resolve_online_store_id

            store_id = _resolve_online_store_id({}, db)
            source = "online_store_id (config/env/settings/primary/default)"
        except Exception:  # noqa: BLE001
            store_id = "BV-ONLINE-01"
            source = "default"

    out["store_id"] = store_id
    out["source"] = source
    out["is_virtual_default"] = store_id == "BV-ONLINE-01"

    if db is None:
        return out

    try:
        coll = _coll(db, "stock_units")
        if coll is not None:
            count = int(
                coll.count_documents({"store_id": store_id, "status": "AVAILABLE"})
            )
            out["available_units"] = count
            out["checked"] = True
            if count == 0:
                out["warning"] = (
                    f"Online fulfillment store '{store_id}' holds 0 AVAILABLE "
                    f"serialized stock units -- every online-order stock decrement "
                    f"will SILENTLY no-op (oversell risk). Set "
                    f"ONLINE_FULFILLMENT_STORE_ID to the physical store that fulfils "
                    f"online orders before go-live."
                )
            elif out["is_virtual_default"]:
                out["warning"] = (
                    "Online fulfillment store is the virtual default 'BV-ONLINE-01'. "
                    "It currently has stock, but set ONLINE_FULFILLMENT_STORE_ID "
                    "explicitly to the real fulfilling store to avoid ambiguity."
                )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[SYNC_HEALTH] fulfillment-store check failed: %s", exc)

    return out


def sync_health(db, safety_buffer: int = 0) -> Dict[str, Any]:
    """Assemble the full online-store sync-health summary. Fully fail-soft:
    each section degrades to its empty/zero shape independently, so the status
    tile always renders.

    The `drift` block reports a lightweight Shopify dual-writer check. When
    Shopify creds are absent (the common case during build) it degrades to
    {checked: False, reason: ...}. A live check requires awaiting detect_drift()
    directly -- sync_health calls the sync shim which is a no-op without creds.

    The `fulfillment_store` block (R6) warns when online orders would decrement
    stock at a store that holds no serialized units (a silent oversell footgun)."""
    from .online_catalog import ecommerce_db_configured

    drift = _drift_sync_shim(db)

    return {
        "online_configured": ecommerce_db_configured(),
        "last_shopify_sync": last_shopify_sync(db),
        "last_successful_shopify_sync_at": last_successful_shopify_sync_at(db),
        "reconcile": pending_reconcile_summary(db, safety_buffer=safety_buffer),
        "webhooks": failed_webhook_summary(db),
        "drift": drift,
        "fulfillment_store": fulfillment_store_health(db),
        "stock_miss": online_stock_miss_summary(db),
    }


def online_stock_miss_summary(db) -> Dict[str, Any]:
    """Count UNRESOLVED online stock-decrement misses (oversells): paid online
    orders whose physical units could not all be claimed (shopify_ingest writes
    an `online_stock_miss` doc on each). A non-zero `unresolved` is an oversell
    that needs operator action. Read-only + fail-soft -> {checked: False} on no DB."""
    out: Dict[str, Any] = {"checked": False, "unresolved": 0, "total": 0, "recent": []}
    coll = _coll(db, "online_stock_miss")
    if coll is None:
        return out
    try:
        out["unresolved"] = int(coll.count_documents({"resolved": {"$ne": True}}))
        out["total"] = int(coll.count_documents({}))
        out["checked"] = True
        recent = list(
            coll.find(
                {"resolved": {"$ne": True}},
                {"_id": 0, "order_id": 1, "store_id": 1, "reason": 1, "created_at": 1},
            ).limit(10)
        )
        out["recent"] = recent
    except Exception as exc:  # noqa: BLE001
        logger.warning("[SYNC_HEALTH] online_stock_miss read failed: %s", exc)
    return out


# ===========================================================================
# STEP 3 -- Dual-writer DRIFT detector
# ===========================================================================
# Invariant: only ONE system (IMS or BVI) should be writing to Shopify at any
# time. After the Phase-6 baton flip (IMS_SHOPIFY_WRITES=1), any Shopify
# updatedAt newer than our last_pushed_at means a second writer touched the
# object -- a DRIFT violation. This detector surfaces those gids.
#
# The Shopify read uses shopify_push._graphql -- the ONLY network boundary --
# so monkeypatching it in tests captures all live calls.
#
# Fail-soft: no creds / no gids -> {checked: False, reason: ...}, NEVER raises.

_DRIFT_QUERY = """
query imsDriftCheck($ids: [ID!]!) {
  nodes(ids: $ids) {
    ... on Product {
      id
      updatedAt
    }
    ... on Collection {
      id
      updatedAt
    }
  }
}
"""

_DRIFT_SCAN_LIMIT = 50  # Shopify nodes() supports up to 250; keep cheap.


async def detect_drift(db, limit: int = _DRIFT_SCAN_LIMIT) -> Dict[str, Any]:
    """Check each pushed IMS ecom object against Shopify's updatedAt and flag
    any gid that Shopify updated AFTER our last_pushed_at (dual-writer drift).

    Returns:
        {
            "checked": bool,        # False when creds absent / no gids
            "reason": str | None,   # why checked=False
            "drifted": [            # empty list when no drift
                {"gid": str, "sku": str, "shopify_updated_at": str,
                 "our_last_pushed_at": str}
            ],
            "counts": {
                "scanned": int,
                "drifted": int,
                "no_timestamp": int,  # gids pushed before last_pushed_at was
            }                         # recorded (pre-Step-3 rows)
        }

    Fail-soft: every error path returns checked=False with a reason string.
    Never raises.
    """
    _empty: Dict[str, Any] = {
        "checked": False,
        "reason": None,
        "drifted": [],
        "counts": {"scanned": 0, "drifted": 0, "no_timestamp": 0},
    }

    # ---- Gate: need creds -----------------------------------------------
    try:
        from .shopify_push import _has_shopify_creds, _graphql
        from agents.nexus_providers import ims_shopify_writes_enabled
    except Exception as exc:  # noqa: BLE001
        return {**_empty, "reason": f"import error: {exc}"}

    if not _has_shopify_creds(db):
        return {
            **_empty,
            "reason": "shopify creds not configured -- drift check skipped",
        }

    # ---- Collect pushed gids from catalog_products + ecom_collections ----
    candidates: List[Dict[str, Any]] = []
    try:
        if db is not None:
            for doc in (
                _coll(db, "catalog_products")
                .find(
                    {"ecom.shopify_product_id": {"$exists": True, "$nin": [None, ""]}},
                    {
                        "_id": 0,
                        "sku": 1,
                        "ecom.shopify_product_id": 1,
                        "ecom.last_pushed_at": 1,
                    },
                )
                .limit(limit)
            ):
                ecom = doc.get("ecom") or {}
                candidates.append(
                    {
                        "gid": str(ecom.get("shopify_product_id") or ""),
                        "sku": str(doc.get("sku") or ""),
                        "last_pushed_at": ecom.get("last_pushed_at"),
                    }
                )
            remaining = max(0, limit - len(candidates))
            if remaining:
                for col in (
                    _coll(db, "ecom_collections")
                    .find(
                        {
                            "shopify_collection_id": {
                                "$exists": True,
                                "$nin": [None, ""],
                            }
                        },
                        {
                            "_id": 0,
                            "handle": 1,
                            "shopify_collection_id": 1,
                            "last_synced_at": 1,
                        },
                    )
                    .limit(remaining)
                ):
                    candidates.append(
                        {
                            "gid": str(col.get("shopify_collection_id") or ""),
                            "sku": str(col.get("handle") or ""),
                            "last_pushed_at": col.get("last_synced_at"),
                        }
                    )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[DRIFT] candidate fetch failed: %s", exc)
        return {**_empty, "reason": f"candidate fetch failed: {exc}"}

    if not candidates:
        return {**_empty, "checked": True, "reason": "no pushed gids found"}

    # ---- Ask Shopify for updatedAt on all gids (one batch call) ----------
    gids = [c["gid"] for c in candidates if c["gid"]]
    gid_to_candidate = {c["gid"]: c for c in candidates if c["gid"]}

    try:
        body = await _graphql(db, _DRIFT_QUERY, {"ids": gids})
    except Exception as exc:  # noqa: BLE001
        logger.warning("[DRIFT] Shopify graphql call failed: %s", exc)
        return {**_empty, "reason": f"shopify graphql error: {exc}"}

    nodes = (body.get("data") or {}).get("nodes") or []

    drifted: List[Dict[str, Any]] = []
    no_timestamp = 0

    for node in nodes:
        if not isinstance(node, dict):
            continue
        gid = node.get("id") or ""
        shopify_updated_at_str = node.get("updatedAt") or ""
        cand = gid_to_candidate.get(gid) or {}
        last_pushed = cand.get("last_pushed_at")

        if not last_pushed:
            # Row was pushed before we started recording last_pushed_at;
            # we cannot compare -- report as no_timestamp (not drift).
            no_timestamp += 1
            continue

        # Normalise timestamps for comparison
        try:
            shopify_dt = datetime.fromisoformat(
                shopify_updated_at_str.replace("Z", "+00:00")
            )
            if isinstance(last_pushed, datetime):
                our_dt = (
                    last_pushed.replace(tzinfo=timezone.utc)
                    if last_pushed.tzinfo is None
                    else last_pushed
                )
            else:
                our_dt = datetime.fromisoformat(str(last_pushed).replace("Z", "+00:00"))
        except (ValueError, TypeError, AttributeError):
            no_timestamp += 1
            continue

        if shopify_dt > our_dt:
            drifted.append(
                {
                    "gid": gid,
                    "sku": cand.get("sku", ""),
                    "shopify_updated_at": shopify_updated_at_str,
                    "our_last_pushed_at": str(last_pushed),
                }
            )

    return {
        "checked": True,
        "reason": None,
        "drifted": drifted,
        "counts": {
            "scanned": len(nodes),
            "drifted": len(drifted),
            "no_timestamp": no_timestamp,
        },
    }


def _drift_sync_shim(db, limit: int = _DRIFT_SCAN_LIMIT) -> Dict[str, Any]:
    """Synchronous wrapper around detect_drift for embedding in the sync
    sync_health() summary. Uses asyncio.run() if no event loop is running,
    otherwise returns a checked=False advisory (the async endpoint is the
    authoritative drift surface). Never raises."""
    _fallback: Dict[str, Any] = {
        "checked": False,
        "reason": "use GET /admin/online-store/drift for a live check",
        "drifted": [],
        "counts": {"scanned": 0, "drifted": 0, "no_timestamp": 0},
    }
    try:
        # asyncio.get_running_loop() raises RuntimeError when no loop is running.
        asyncio.get_running_loop()
        # A loop IS running (e.g. inside FastAPI) -- can't call asyncio.run here.
        return _fallback
    except RuntimeError:
        pass
    try:
        return asyncio.run(detect_drift(db, limit=limit))
    except Exception as exc:  # noqa: BLE001
        logger.debug("[DRIFT] sync shim error: %s", exc)
        return {**_fallback, "reason": f"async run error: {exc}"}


# ===========================================================================
# STEP 4 -- Re-push SWEEP for oversell-risk SKUs (NEXUS-callable)
# ===========================================================================
# For each SKU flagged as oversell_risk by pending_reconcile_summary, push
# the absolute on-hand quantity back to Shopify via nexus_providers
# shopify_set_inventory_available so the live qty cannot go below zero.
#
# DARK by default (dry_run=True). Respects ims_shopify_writes_enabled();
# when writes are off OR dry_run, returns the PLAN without touching Shopify.
# Never raises.


async def repush_oversell_risk(db, dry_run: bool = True) -> Dict[str, Any]:
    """Re-push the absolute inventory quantity for every SKU that is flagged
    as oversell_risk (online qty > physical on-hand).

    Returns:
        {
            "dry_run": bool,
            "would_repush": [{"sku", "in_store", "online", "product_id"}],
            "repushed": [{"sku", "result"}],
            "skipped_reason": str | None,  # why we did nothing
        }

    DARK by default (dry_run=True). Respects the TRIPLE gate:
      ims_shopify_writes_enabled() AND DISPATCH_MODE=live AND creds.
    When any gate is off OR dry_run=True -> returns the plan, no Shopify call.
    Fail-soft: errors become structured entries in repushed, never raise.
    """
    base: Dict[str, Any] = {
        "dry_run": dry_run,
        "would_repush": [],
        "repushed": [],
        "skipped_reason": None,
    }

    try:
        from agents.nexus_providers import (
            ims_shopify_writes_enabled,
            shopify_set_inventory_available,
        )
        from agents.providers import dispatch_mode as _dispatch_mode
    except Exception as exc:  # noqa: BLE001
        return {**base, "skipped_reason": f"import error: {exc}"}

    if not ims_shopify_writes_enabled():
        base["skipped_reason"] = (
            "IMS_SHOPIFY_WRITES is OFF -- BVI owns the Shopify catalog; "
            "set IMS_SHOPIFY_WRITES=1 to enable"
        )

    # Build the oversell-risk list from the existing reconcile engine.
    try:
        from .online_catalog import online_status_for_skus
        from . import stock_allocation
    except Exception as exc:  # noqa: BLE001
        return {**base, "skipped_reason": f"import error: {exc}"}

    if db is None:
        return {**base, "skipped_reason": "no db"}

    try:
        products = list(
            _coll(db, "products")
            .find(
                {"sku": {"$nin": [None, ""]}, "is_active": {"$ne": False}},
                {"_id": 0, "product_id": 1, "sku": 1},
            )
            .limit(_RECONCILE_SCAN_LIMIT)
        )
    except Exception as exc:  # noqa: BLE001
        return {**base, "skipped_reason": f"products scan failed: {exc}"}

    if not products:
        return {**base, "skipped_reason": "no active products found"}

    pids = [p.get("product_id") for p in products if p.get("product_id")]
    on_hand = _on_hand_by_product(db, pids)
    skus = [p.get("sku") for p in products if p.get("sku")]
    online = online_status_for_skus(skus)

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
                "product_id": p.get("product_id"),
            }
        )

    result = stock_allocation.reconcile_items(items, safety_buffer=0)
    oversell_items = [
        i for i in result.get("items", []) if i.get("status") == "OVERSELL_RISK"
    ]
    base["would_repush"] = [
        {
            "sku": i.get("sku"),
            "in_store": i.get("in_store", 0),
            "online": i.get("online", 0),
            "product_id": next(
                (p.get("product_id") for p in products if p.get("sku") == i.get("sku")),
                None,
            ),
        }
        for i in oversell_items
    ]

    if not ims_shopify_writes_enabled() or dry_run:
        reason = base["skipped_reason"] or (
            "dry_run=True -- set dry_run=False to push (also requires "
            "IMS_SHOPIFY_WRITES=1 + DISPATCH_MODE=live + creds)"
        )
        base["skipped_reason"] = reason
        return base

    # ---- LIVE path ---------------------------------------------------------
    # For each oversell SKU, look up its catalog_products variant inventory ids
    # and call the absolute writeback.
    location_id = _shopify_online_location(db)
    if not location_id:
        return {
            **base,
            "skipped_reason": (
                "SHOPIFY_ONLINE_LOCATION_ID not configured -- "
                "set it on the integrations.shopify config doc"
            ),
        }

    repushed: List[Dict[str, Any]] = []
    for item in base["would_repush"]:
        sku = item.get("sku") or ""
        in_store = int(item.get("in_store") or 0)
        inv_item_id = _inventory_item_id_for_sku(db, sku)
        if not inv_item_id:
            repushed.append(
                {
                    "sku": sku,
                    "result": {
                        "ok": False,
                        "error": "no shopify inventory_item_id found for sku",
                    },
                }
            )
            continue
        try:
            sync_result = await shopify_set_inventory_available(
                db, inv_item_id, location_id, in_store
            )
            repushed.append(
                {
                    "sku": sku,
                    "result": {
                        "ok": sync_result.ok,
                        "notes": sync_result.notes,
                        "error": sync_result.error,
                    },
                }
            )
        except Exception as exc:  # noqa: BLE001
            repushed.append({"sku": sku, "result": {"ok": False, "error": str(exc)}})

    base["repushed"] = repushed
    return base


def _shopify_online_location(db) -> Optional[str]:
    """Read SHOPIFY_ONLINE_LOCATION_ID from the integrations.shopify config
    doc, or fall back to the env var. Fail-soft -> None."""
    import os

    env_val = os.getenv("SHOPIFY_ONLINE_LOCATION_ID", "").strip()
    if env_val:
        return env_val
    try:
        from agents.nexus_providers import _load_integration_config

        cfg = _load_integration_config(db, "shopify") or {}
        return cfg.get("online_location_id") or None
    except Exception:  # noqa: BLE001
        return None


def _inventory_item_id_for_sku(db, sku: str) -> Optional[str]:
    """Look up the Shopify inventoryItemId for a SKU from catalog_variants
    (field shopify_inventory_item_id). Fail-soft -> None."""
    if not sku or db is None:
        return None
    try:
        doc = _coll(db, "catalog_variants").find_one(
            {
                "sku": sku,
                "shopify_inventory_item_id": {"$exists": True, "$nin": [None, ""]},
            },
            {"_id": 0, "shopify_inventory_item_id": 1},
        )
        if doc:
            return str(doc["shopify_inventory_item_id"])
        # Fallback: check catalog_products ecom sub-doc for the inventory item id.
        p = _coll(db, "catalog_products").find_one(
            {"sku": sku},
            {"_id": 0, "ecom.shopify_inventory_item_id": 1},
        )
        if p:
            return (p.get("ecom") or {}).get("shopify_inventory_item_id") or None
    except Exception:  # noqa: BLE001
        return None
    return None


# ===========================================================================
# STEP 6 -- Parity oracle + /uploads/ image audit (read-only)
# ===========================================================================


def parity_summary(db) -> Dict[str, Any]:
    """Compare IMS catalog object counts vs what has a Shopify gid (pushed).

    Returns a dict with a row per entity type: total in IMS, pushed (has a
    Shopify gid), missing (total - pushed). All read-only + fail-soft.

    Shape:
        {
            "entities": {
                "catalog_products":  {"total": int, "pushed": int, "missing": int},
                "catalog_variants":  {"total": int, "pushed": int, "missing": int},
                "ecom_collections":  {"total": int, "pushed": int, "missing": int},
                "product_images":    {"total": int, "pushed": int, "missing": int},
            },
            "ok": bool,    # True if all totals could be read
        }
    """
    entities = {
        "catalog_products": {
            "total_filter": {},
            "pushed_filter": {
                "ecom.shopify_product_id": {"$exists": True, "$nin": [None, ""]}
            },
        },
        "catalog_variants": {
            "total_filter": {},
            "pushed_filter": {
                "shopify_variant_id": {"$exists": True, "$nin": [None, ""]}
            },
        },
        "ecom_collections": {
            "total_filter": {},
            "pushed_filter": {
                "shopify_collection_id": {"$exists": True, "$nin": [None, ""]}
            },
        },
        "product_images": {
            "total_filter": {},
            "pushed_filter": {
                "shopify_image_id": {"$exists": True, "$nin": [None, ""]}
            },
        },
    }

    if db is None:
        empty = {k: {"total": 0, "pushed": 0, "missing": 0} for k in entities}
        return {"entities": empty, "ok": False}

    result: Dict[str, Any] = {}
    all_ok = True
    for coll_name, spec in entities.items():
        try:
            coll = _coll(db, coll_name)
            total = int(coll.count_documents(spec["total_filter"]))
            pushed = int(coll.count_documents(spec["pushed_filter"]))
            result[coll_name] = {
                "total": total,
                "pushed": pushed,
                "missing": max(0, total - pushed),
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("[PARITY] %s count failed: %s", coll_name, exc)
            result[coll_name] = {"total": 0, "pushed": 0, "missing": 0}
            all_ok = False

    return {"entities": result, "ok": all_ok}


def uploads_image_audit(db, limit: int = 500) -> Dict[str, Any]:
    """Scan product_images for `url` or `edited_url` still pointing at a local
    /uploads/... path (NOT a durable https:// URL). These block the Shopify
    cutover because Shopify cannot pull a private /uploads/ path.

    Returns:
        {
            "checked": bool,
            "local_url_count": int,
            "items": [
                {"image_id": str, "sku": str, "url": str,
                 "edited_url": str | None, "status": str}
            ],
        }

    Read-only + fail-soft. No DB -> checked=False.
    """
    base: Dict[str, Any] = {
        "checked": False,
        "local_url_count": 0,
        "items": [],
    }

    if db is None:
        return base

    try:
        coll = _coll(db, "product_images")
        if coll is None:
            return base

        # A URL is "local" when it starts with /uploads/ or does NOT start with
        # http:// / https:// (Shopify CDN / R2 / S3 links are always absolute).
        all_images = list(
            coll.find(
                {},
                {
                    "_id": 0,
                    "image_id": 1,
                    "product_id": 1,
                    "url": 1,
                    "edited_url": 1,
                    "status": 1,
                    "sku": 1,
                },
            ).limit(limit)
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[UPLOADS_AUDIT] image scan failed: %s", exc)
        return base

    local_items: List[Dict[str, Any]] = []
    for img in all_images:
        url = str(img.get("url") or "")
        edited_url = img.get("edited_url") or None
        # Flag when either the primary OR edited url is local.
        primary_local = _is_local_url(url)
        edited_local = edited_url is not None and _is_local_url(str(edited_url))
        if primary_local or edited_local:
            local_items.append(
                {
                    "image_id": img.get("image_id") or "",
                    "sku": img.get("sku") or img.get("product_id") or "",
                    "url": url,
                    "edited_url": edited_url,
                    "status": img.get("status") or "",
                    "primary_local": primary_local,
                    "edited_local": edited_local,
                }
            )

    return {
        "checked": True,
        "local_url_count": len(local_items),
        "items": local_items,
    }


def _is_local_url(url: str) -> bool:
    """True when a URL is a local /uploads/ path rather than a durable remote URL."""
    if not url:
        return False
    if url.startswith("/uploads/") or url.startswith("uploads/"):
        return True
    # Any non-http(s) absolute URL is also local-ish (relative paths, data: URIs).
    if url.startswith("http://") or url.startswith("https://"):
        return False
    # Relative path or unknown scheme -> local.
    return True


# ===========================================================================
# STEP 6c -- /uploads/ image RE-HOST migration (the R3 cutover prereq fix)
# ===========================================================================
# uploads_image_audit() DETECTS images still on a local /uploads/ path; this
# closes the loop by RE-HOSTING them to durable object storage + rewriting the
# product_images url so Shopify can pull them after the cutover (Shopify cannot
# fetch a private /uploads/ path on the Railway container).
#
# The local files live on BVI's disk, NOT the IMS container -- so a bare
# "/uploads/x.jpg" usually isn't readable from here. Set BVI_UPLOADS_BASE_URL
# (e.g. https://uniparallel.com) so the tool fetches "/uploads/x" over HTTP from
# BVI's still-running server. http(s) URLs are fetched as-is. If neither works
# the item fails with a clear reason (the others still migrate).
#
# DRY-RUN BY DEFAULT: dry_run=True only PLANS (no fetch, no write, no DB update)
# and reports the storage backend + whether it is durable. Owner runs
# dry_run=False once a durable store (IMAGE_S3_* / Settings -> Integrations) is
# configured. Fully fail-soft: one bad image never aborts the batch.

_REHOST_FIELDS = ("url", "edited_url", "raw_url", "original_url")


def _guess_content_type(url: str) -> str:
    u = (url or "").lower()
    if u.endswith(".png"):
        return "image/png"
    if u.endswith(".webp"):
        return "image/webp"
    if u.endswith(".gif"):
        return "image/gif"
    if u.endswith(".svg"):
        return "image/svg+xml"
    return "image/jpeg"


def _ext_from_url(url: str) -> str:
    u = (url or "").split("?", 1)[0]
    for ext in (".png", ".webp", ".gif", ".svg", ".jpeg", ".jpg"):
        if u.lower().endswith(ext):
            return ext
    return ".jpg"


def _resolve_fetch_url(local_url: str) -> Optional[str]:
    """Turn a stored image url into something fetchable. http(s) -> as-is. A local
    /uploads/ path -> {BVI_UPLOADS_BASE_URL}/uploads/... when that env is set,
    else the bare path (only works if /uploads/ is mounted on this host).
    Returns None when there is nothing usable to fetch."""
    import os

    u = (local_url or "").strip()
    if not u:
        return None
    if u.startswith("http://") or u.startswith("https://"):
        return u
    base = (os.getenv("BVI_UPLOADS_BASE_URL") or "").strip().rstrip("/")
    path = u if u.startswith("/") else f"/{u}"
    if base:
        return f"{base}{path}"
    return path  # bare local path -- only readable if mounted here


def _fetch_bytes(fetch_url: str) -> bytes:
    """Fetch image bytes from an http(s) URL or a local filesystem path. Raises
    on failure (the caller records it per-item)."""
    if fetch_url.startswith("http://") or fetch_url.startswith("https://"):
        import httpx

        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            resp = client.get(fetch_url)
        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code} fetching {fetch_url}")
        return resp.content
    with open(fetch_url.lstrip("/"), "rb") as fh:
        return fh.read()


def rehost_uploads_images(db, dry_run: bool = True, limit: int = 500) -> Dict[str, Any]:
    """Re-host product images still on a local /uploads/ path to durable object
    storage and rewrite their product_images url(s). The R3 cutover prereq.

    dry_run=True (default) only PLANS -- no fetch, no upload, no DB write. It
    reports the storage backend + whether it is durable so the operator can
    confirm before arming. Set dry_run=False to migrate.

    Returns {checked, dry_run, storage_backend, durable, candidates, rehosted,
    failed, items:[{image_id, sku, field, old_url, new_url|error}]}.
    Fully fail-soft -- never raises."""
    out: Dict[str, Any] = {
        "checked": False,
        "dry_run": dry_run,
        "storage_backend": None,
        "durable": False,
        "candidates": 0,
        "rehosted": 0,
        "failed": 0,
        "items": [],
    }
    if db is None:
        return out

    # Resolve the storage backend + whether it is durable enough for the cutover
    # (local-disk on the ephemeral Railway container is NOT -- Shopify still
    # cannot reach it). We surface this rather than silently re-host to nowhere.
    try:
        from .object_storage import get_object_storage

        storage = get_object_storage()
        out["storage_backend"] = getattr(storage, "name", "unknown")
        out["durable"] = bool(
            getattr(storage, "name", "") == "s3" and storage.available()
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[REHOST] storage resolve failed: %s", exc)
        storage = None

    try:
        coll = _coll(db, "product_images")
        if coll is None:
            return out
        rows = list(
            coll.find(
                {},
                {
                    "_id": 0,
                    "image_id": 1,
                    "product_id": 1,
                    "sku": 1,
                    "url": 1,
                    "edited_url": 1,
                    "raw_url": 1,
                    "original_url": 1,
                },
            ).limit(limit)
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[REHOST] image scan failed: %s", exc)
        return out

    out["checked"] = True
    items: List[Dict[str, Any]] = []

    for img in rows:
        image_id = img.get("image_id") or ""
        sku = img.get("sku") or img.get("product_id") or ""
        for field in _REHOST_FIELDS:
            old_url = img.get(field)
            if not old_url or not _is_local_url(str(old_url)):
                continue
            out["candidates"] += 1
            entry: Dict[str, Any] = {
                "image_id": image_id,
                "sku": sku,
                "field": field,
                "old_url": old_url,
            }
            if dry_run:
                entry["fetch_url"] = _resolve_fetch_url(str(old_url))
                items.append(entry)
                continue

            if storage is None:
                entry["error"] = "no storage backend"
                out["failed"] += 1
                items.append(entry)
                continue

            fetch_url = _resolve_fetch_url(str(old_url))
            if not fetch_url:
                entry["error"] = "no fetchable source url"
                out["failed"] += 1
                items.append(entry)
                continue
            try:
                data = _fetch_bytes(fetch_url)
                key = f"bvi-rehost/{sku or 'misc'}/{image_id}_{field}{_ext_from_url(str(old_url))}"
                new_url = storage.put(
                    key, data, content_type=_guess_content_type(str(old_url))
                )
                coll.update_one(
                    {"image_id": image_id},
                    {"$set": {field: new_url, "locally_modified": True}},
                )
                entry["new_url"] = new_url
                out["rehosted"] += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[REHOST] image %s field %s failed: %s", image_id, field, exc
                )
                entry["error"] = str(exc)
                out["failed"] += 1
            items.append(entry)

    out["items"] = items
    return out
