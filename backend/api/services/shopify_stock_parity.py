"""
IMS 2.0 - Nightly Shopify stock-parity check (read-only diagnostic)
===================================================================
IMS is the inventory MASTER; the online listing quantity is written OUT to
Shopify. Over time a dropped write-back, a manual Shopify edit, or a race can let
the live Shopify "available" drift away from IMS's true POOLED availability
(the online store owns no stock -- it sells from all physical shops combined).

This service samples up to N online-mapped variants, asks Shopify for their live
available quantity, compares against IMS pooled availability, stores a compact
snapshot, and -- when the worst drift exceeds a tolerance -- files ONE deduped
SYSTEM task so ops actually fixes it. SENTINEL calls this once a day (~03:00 IST).

Contract (mirrors the rest of the Shopify bridge):
  * 100% FAIL-SOFT, end to end. No creds / no DB / Shopify error -> a structured
    reason, never a raise. It must NEVER take down the SENTINEL scheduler.
  * READ-ONLY vs Shopify (single boundary: shopify_push._graphql, injectable).
  * Pooled availability reuses the SAME helper the online write-back layer uses
    (online_stock_writeback._on_hand_for_skus with store_id=None = all shops).
  * The comparator is a PURE function (compare_variant_parity) so drift logic is
    unit-tested without a DB or Shopify.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# Sampling ceiling (keeps the nightly Shopify call cheap on a large catalog).
_DEFAULT_SAMPLE = 500
# Batch size for the Shopify nodes() inventory query.
_INV_BATCH = 100
# Compact snapshot retention.
_SNAPSHOT_RETENTION_DAYS = 30
# Drift tolerance default (units). Env override: SHOPIFY_STOCK_PARITY_TOLERANCE.
_DEFAULT_TOLERANCE = 2
# Stable dedupe key: at most ONE active parity-drift task at a time.
_DRIFT_TASK_REF = "shopify-stock-parity-drift"

# GraphQL: live available per InventoryItem (summed across its inventory levels).
# quantities(names:["available"]) is the current Shopify Admin API shape.
_INV_LEVELS_QUERY = """
query ImsInvLevels($ids: [ID!]!) {
  nodes(ids: $ids) {
    ... on InventoryItem {
      id
      inventoryLevels(first: 20) {
        edges {
          node {
            location { id }
            quantities(names: ["available"]) { name quantity }
          }
        }
      }
    }
  }
}
"""


def _coll(db, name: str):
    """Collection access tolerant of DatabaseConnection + the in-memory Mock."""
    if db is None:
        return None
    try:
        getter = getattr(db, "get_collection", None)
        if callable(getter):
            return getter(name)
    except Exception:  # noqa: BLE001
        pass
    try:
        return db[name]
    except Exception:  # noqa: BLE001
        return None


def parity_tolerance() -> int:
    """Drift tolerance in units: SHOPIFY_STOCK_PARITY_TOLERANCE env, else 2.
    A non-negative int; junk env values fall back to the default."""
    raw = os.getenv("SHOPIFY_STOCK_PARITY_TOLERANCE")
    if raw is None or str(raw).strip() == "":
        return _DEFAULT_TOLERANCE
    try:
        return max(0, int(str(raw).strip()))
    except (TypeError, ValueError):
        return _DEFAULT_TOLERANCE


def compare_variant_parity(
    rows: List[Dict[str, Any]], tolerance: int
) -> Dict[str, Any]:
    """PURE: given rows of {sku, inventory_item_id, ims_available, shopify_available},
    return the drift summary. A row whose shopify_available is None (Shopify
    returned nothing for that item) is counted as UNKNOWN and never a drift.

    Returns {compared, unknown, drift[], drift_count, max_delta, tolerance}
    where drift is [{sku, inventory_item_id, ims, shopify, delta}] sorted by
    the biggest delta first."""
    tol = max(0, int(tolerance or 0))
    drift: List[Dict[str, Any]] = []
    compared = 0
    unknown = 0
    max_delta = 0
    for r in rows or []:
        shop = r.get("shopify_available")
        if shop is None:
            unknown += 1
            continue
        try:
            ims = int(r.get("ims_available") or 0)
            shop = int(shop)
        except (TypeError, ValueError):
            unknown += 1
            continue
        compared += 1
        delta = abs(ims - shop)
        if delta > max_delta:
            max_delta = delta
        if delta > tol:
            drift.append(
                {
                    "sku": r.get("sku"),
                    "inventory_item_id": r.get("inventory_item_id"),
                    "ims": ims,
                    "shopify": shop,
                    "delta": delta,
                }
            )
    drift.sort(key=lambda d: d.get("delta", 0), reverse=True)
    return {
        "compared": compared,
        "unknown": unknown,
        "drift": drift,
        "drift_count": len(drift),
        "max_delta": max_delta,
        "tolerance": tol,
    }


def _sample_variants(db, limit: int = _DEFAULT_SAMPLE) -> List[Dict[str, Any]]:
    """Up to `limit` catalog_variants that carry a Shopify inventory item id.
    Returns [{sku, inventory_item_id}]. Fail-soft -> []."""
    coll = _coll(db, "catalog_variants")
    if coll is None:
        return []
    out: List[Dict[str, Any]] = []
    try:
        cursor = coll.find(
            {"shopify_inventory_item_id": {"$exists": True, "$nin": [None, ""]}},
            {"_id": 0, "sku": 1, "shopify_inventory_item_id": 1},
        ).limit(int(limit))
        for doc in cursor:
            sku = str(doc.get("sku") or "").strip()
            inv = str(doc.get("shopify_inventory_item_id") or "").strip()
            if sku and inv:
                out.append({"sku": sku, "inventory_item_id": inv})
    except Exception as exc:  # noqa: BLE001
        logger.warning("[STOCK_PARITY] variant sample failed: %s", exc)
        return []
    return out


def _pooled_availability(db, skus: List[str]) -> Dict[str, int]:
    """POOLED (all-shops-combined) IMS on-hand per SKU. Reuses the online layer's
    own helper (online_stock_writeback._on_hand_for_skus) with store_id=None so
    the number matches what the write-back pushes online. Fail-soft -> {}."""
    if not skus:
        return {}
    try:
        from .online_stock_writeback import _on_hand_for_skus

        return _on_hand_for_skus(db, list(skus), None) or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("[STOCK_PARITY] pooled availability failed: %s", exc)
        return {}


async def _shopify_available_by_item(
    db, inventory_item_ids: List[str], *, graphql: Optional[Callable] = None
) -> Dict[str, int]:
    """Batch-query Shopify for the live available quantity per inventory item.
    Returns {inventory_item_id (as supplied): available_units}. Items Shopify
    doesn't return are simply absent (caller treats absent as UNKNOWN).
    Read-only; fail-soft -> {}."""
    if not inventory_item_ids:
        return {}
    try:
        from .shopify_push import _graphql
        from agents.nexus_providers import _as_shopify_gid
    except Exception as exc:  # noqa: BLE001
        logger.warning("[STOCK_PARITY] shopify deps unavailable: %s", exc)
        return {}
    gql = graphql or _graphql

    # Map GID -> the caller's supplied id so we can key the result back exactly.
    gid_to_supplied: Dict[str, str] = {}
    for raw in inventory_item_ids:
        gid = _as_shopify_gid(raw, "InventoryItem")
        if gid:
            gid_to_supplied.setdefault(gid, str(raw))

    out: Dict[str, int] = {}
    gids = list(gid_to_supplied.keys())
    for i in range(0, len(gids), _INV_BATCH):
        chunk = gids[i : i + _INV_BATCH]
        try:
            body = await gql(db, _INV_LEVELS_QUERY, {"ids": chunk})
        except Exception as exc:  # noqa: BLE001
            logger.warning("[STOCK_PARITY] shopify inventory query failed: %s", exc)
            continue
        for node in (body.get("data") or {}).get("nodes") or []:
            if not isinstance(node, dict):
                continue
            gid = str(node.get("id") or "")
            supplied = gid_to_supplied.get(gid)
            if not supplied:
                continue
            available = 0
            for edge in ((node.get("inventoryLevels") or {}).get("edges")) or []:
                lnode = edge.get("node") if isinstance(edge, dict) else None
                if not isinstance(lnode, dict):
                    continue
                for q in lnode.get("quantities") or []:
                    if isinstance(q, dict) and q.get("name") == "available":
                        try:
                            available += int(q.get("quantity") or 0)
                        except (TypeError, ValueError):
                            pass
            out[supplied] = available
    return out


def _task_repo(db):
    """A TaskRepository over the `tasks` collection, or None. Fail-soft."""
    coll = _coll(db, "tasks")
    if coll is None:
        return None
    try:
        from database.repositories.task_repository import TaskRepository

        return TaskRepository(coll)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[STOCK_PARITY] task repo unavailable: %s", exc)
        return None


def file_drift_task(repo, summary: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """File ONE deduped SYSTEM task for stock drift (stable source_ref means at
    most one ACTIVE task at a time -- create_system_task no-ops while one is
    open). Returns the created task, or None when deduped / no repo. Fail-soft."""
    try:
        from .task_triggers import create_system_task
    except Exception as exc:  # noqa: BLE001
        logger.warning("[STOCK_PARITY] create_system_task import failed: %s", exc)
        return None

    drift = summary.get("drift") or []
    worst = drift[:5]
    lines = ", ".join(
        f"{d.get('sku')} (IMS {d.get('ims')} vs Shopify {d.get('shopify')})" for d in worst
    )
    description = (
        f"{summary.get('drift_count', 0)} online SKU(s) drifted beyond tolerance "
        f"{summary.get('tolerance')} unit(s); worst delta {summary.get('max_delta')}. "
        f"Top: {lines}"
    )
    return create_system_task(
        repo,
        title="Shopify stock parity drift detected",
        description=description,
        priority="P2",
        category="Inventory",
        store_id=None,
        dedupe_ref=_DRIFT_TASK_REF,
        extra={"payload": {"drift": worst, "max_delta": summary.get("max_delta")}},
    )


def prune_snapshots(coll, retention_days: int = _SNAPSHOT_RETENTION_DAYS, now=None) -> int:
    """Delete parity snapshots older than retention_days. Pure-ish (fake coll ok).
    Returns rows deleted, 0 on any error / missing collection. Fail-soft."""
    if coll is None:
        return 0
    if now is None:
        now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=retention_days)).isoformat()
    try:
        res = coll.delete_many({"generated_at": {"$lt": cutoff}})
        return int(getattr(res, "deleted_count", 0) or 0)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[STOCK_PARITY] snapshot prune skipped: %s", exc)
        return 0


def _store_snapshot(db, snapshot: Dict[str, Any]) -> None:
    """Persist the compact snapshot + prune the collection to 30 days. Fail-soft."""
    coll = _coll(db, "shopify_stock_parity_snapshots")
    if coll is None:
        return
    try:
        coll.insert_one(dict(snapshot))
    except Exception as exc:  # noqa: BLE001
        logger.debug("[STOCK_PARITY] snapshot insert skipped: %s", exc)
        return
    prune_snapshots(coll)


async def run_parity_tick(
    db,
    *,
    sample_limit: int = _DEFAULT_SAMPLE,
    graphql: Optional[Callable] = None,
) -> Dict[str, Any]:
    """The daily tick body (called by SENTINEL ~03:00 IST). Samples online-mapped
    variants, compares live Shopify availability vs IMS pooled availability,
    stores a compact snapshot, and files a deduped SYSTEM task on drift.

    Returns a small summary dict. FAIL-SOFT end to end -- every failure path
    returns a reason and NEVER raises (must not crash the scheduler)."""
    generated_at = datetime.now(timezone.utc).isoformat()
    tolerance = parity_tolerance()
    base: Dict[str, Any] = {
        "generated_at": generated_at,
        "checked": False,
        "reason": None,
        "sampled": 0,
        "compared": 0,
        "drift_count": 0,
        "max_delta": 0,
        "tolerance": tolerance,
        "task_filed": False,
    }

    try:
        # Gate on creds so we don't mint tokens / call Shopify when unconfigured.
        try:
            from .shopify_push import _has_shopify_creds

            if not _has_shopify_creds(db):
                snap = {**base, "reason": "shopify creds not configured", "drift": []}
                _store_snapshot(db, snap)
                return snap
        except Exception as exc:  # noqa: BLE001
            return {**base, "reason": f"creds check failed: {exc}"}

        variants = _sample_variants(db, sample_limit)
        base["sampled"] = len(variants)
        if not variants:
            snap = {**base, "checked": True, "reason": "no online-mapped variants", "drift": []}
            _store_snapshot(db, snap)
            return snap

        skus = [v["sku"] for v in variants]
        inv_ids = [v["inventory_item_id"] for v in variants]
        pooled = _pooled_availability(db, skus)
        shopify_avail = await _shopify_available_by_item(db, inv_ids, graphql=graphql)

        rows = [
            {
                "sku": v["sku"],
                "inventory_item_id": v["inventory_item_id"],
                "ims_available": int(pooled.get(v["sku"], 0) or 0),
                "shopify_available": shopify_avail.get(v["inventory_item_id"]),
            }
            for v in variants
        ]

        cmp = compare_variant_parity(rows, tolerance)
        snapshot = {
            "generated_at": generated_at,
            "checked": True,
            "reason": None,
            "sampled": len(variants),
            "compared": cmp["compared"],
            "unknown": cmp["unknown"],
            "drift_count": cmp["drift_count"],
            "max_delta": cmp["max_delta"],
            "tolerance": tolerance,
            # Cap the stored drift list so the snapshot stays compact.
            "drift": cmp["drift"][:50],
            "task_filed": False,
        }

        if cmp["drift_count"] > 0:
            repo = _task_repo(db)
            if repo is not None:
                task = file_drift_task(repo, cmp)
                snapshot["task_filed"] = bool(task)

        _store_snapshot(db, snapshot)
        logger.info(
            "[STOCK_PARITY] tick: sampled=%s compared=%s drift=%s max_delta=%s task=%s",
            snapshot["sampled"],
            snapshot["compared"],
            snapshot["drift_count"],
            snapshot["max_delta"],
            snapshot["task_filed"],
        )
        return snapshot
    except Exception as exc:  # noqa: BLE001 -- never crash the scheduler
        logger.warning("[STOCK_PARITY] tick failed: %s", exc)
        return {**base, "reason": f"tick error: {exc}"}
