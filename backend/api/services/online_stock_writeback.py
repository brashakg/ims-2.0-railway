"""
IMS 2.0 - IMS -> Shopify stock write-back (oversell guard)   [Council B11]
==========================================================================
IMS is the inventory MASTER. When a unit is sold in-store (POS) the website
must not be able to sell the same unit. This module pushes the REDUCED
available quantity for the affected SKUs up to Shopify so the online listing
can never oversell.

Flow on a sale:
  1. The POS create-order path flips serialized stock_units to SOLD, then calls
     writeback_after_sale(db, items_data, store_id).
  2. For each sold SKU we recompute IMS on-hand (the AVAILABLE stock_units count
     for the online-serving store) MINUS a safety buffer, via the canonical
     stock_allocation.recommend_allocation -- the SAME math the
     /catalog/online-stock-reconcile diagnostic uses.
  3. We look up the SKU's Shopify InventoryItem GID + location GID in the BVI
     Postgres (online_catalog.online_variant_targets_for_skus). No mapping ->
     NO-OP (not every IMS product is online).
  4. We call the Shopify GraphQL inventorySetQuantities setter with the ABSOLUTE
     quantity (idempotent on retry).

Contract (mirrors the rest of the consolidation bridge + the NEXUS providers):
- 100% FAIL-SOFT. A Shopify/Postgres/Mongo failure is caught + logged and NEVER
  propagates into (or slows/blocks) the sale. The sale already happened; the
  write-back is best-effort.
- GATED. shopify_set_inventory_available enforces IMS_SHOPIFY_WRITES +
  DISPATCH_MODE. With DISPATCH_MODE unset/off the behaviour is byte-identical
  to today: no live Shopify write (the setter returns a SIMULATED result).
- The async push is fire-and-forget (scheduled on the running loop) so the HTTP
  round-trip is fully off the request path. If no loop is running (sync /
  test context) it runs inline and still never raises.

Returns / restock: when stock goes back UP (a GOOD-condition return is
re-shelved) the same writeback_skus path re-pushes the higher available count,
so the online listing recovers too.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Default oversell safety buffer (units held back from the online listing).
# 0 = list the true on-hand. Override per-deploy with ONLINE_STOCK_SAFETY_BUFFER
# or via the `integrations` shopify config {safety_buffer:N}.
_DEFAULT_SAFETY_BUFFER = 0

# Item types / product_id prefixes with no serialized online stock to sync.
# Mirrors orders._NON_SERIALIZED_ITEM_TYPES / _VIRTUAL_PID_PREFIXES so a SERVICE
# or virtual lens line never triggers a pointless Shopify lookup.
_VIRTUAL_PID_PREFIXES = ("custom-", "lens-", "lens-sug-")
_NON_SERIALIZED_ITEM_TYPES = {
    "SERVICE",
    "EYE_TEST",
    "EYE_EXAM",
    "EYE_CHECKUP",
    "CONSULT",
    "CONSULTATION",
    "OPTOMETRY",
}


def _resolve_db(db):
    """Return a usable DatabaseConnection. Accepts an explicit db or resolves
    the process default. Fail-soft -> None."""
    if db is not None:
        return db
    try:
        from ..dependencies import get_db

        d = get_db()
        if d is not None and getattr(d, "is_connected", False):
            return d
    except Exception:  # noqa: BLE001
        pass
    return None


def _safety_buffer(db) -> int:
    """Resolve the oversell safety buffer: integrations.shopify.config.safety_buffer
    wins, else ONLINE_STOCK_SAFETY_BUFFER env, else the default. Fail-soft."""
    import os

    # 1. Per-tenant override on the shopify integration doc.
    try:
        from agents.nexus_providers import _load_integration_config

        cfg = _load_integration_config(db, "shopify")
        if cfg and cfg.get("safety_buffer") is not None:
            return max(0, int(cfg.get("safety_buffer")))
    except Exception:  # noqa: BLE001
        pass
    # 2. Env override.
    raw = os.getenv("ONLINE_STOCK_SAFETY_BUFFER")
    if raw is not None and str(raw).strip() != "":
        try:
            return max(0, int(raw))
        except (TypeError, ValueError):
            pass
    return _DEFAULT_SAFETY_BUFFER


def skus_from_items(items_data: List[dict]) -> List[str]:
    """Extract the distinct, sellable-good SKUs from order items. Skips service /
    virtual lines and blank SKUs. Pure."""
    seen: List[str] = []
    for line in items_data or []:
        if not isinstance(line, dict):
            continue
        item_type = (line.get("item_type") or "").upper()
        if item_type in _NON_SERIALIZED_ITEM_TYPES:
            continue
        pid = line.get("product_id") or ""
        if pid.startswith(_VIRTUAL_PID_PREFIXES):
            continue
        sku = line.get("sku")
        sku = str(sku).strip() if sku not in (None, "") else ""
        if sku and sku not in seen:
            seen.append(sku)
    return seen


def _on_hand_for_skus(db, skus: List[str], store_id: Optional[str]) -> Dict[str, int]:
    """Map each SKU -> IMS on-hand (AVAILABLE stock_units) for the given store.
    Reuses inventory._on_hand_by_product (the canonical on-hand math) after
    resolving SKU -> product_id via the products collection. Fail-soft -> {}."""
    if db is None or not skus:
        return {}
    try:
        raw = db.get_collection("products")
        prod_coll = raw
    except Exception:  # noqa: BLE001
        return {}
    if prod_coll is None:
        return {}
    sku_to_pid: Dict[str, str] = {}
    try:
        for p in prod_coll.find(
            {"sku": {"$in": list(skus)}}, {"_id": 0, "product_id": 1, "sku": 1}
        ):
            sku = str(p.get("sku") or "").strip()
            pid = p.get("product_id")
            if sku and pid and sku not in sku_to_pid:
                sku_to_pid[sku] = pid
    except Exception as exc:  # noqa: BLE001
        logger.debug("[STOCK_WRITEBACK] sku->product lookup failed: %s", exc)
        return {}
    if not sku_to_pid:
        return {}

    try:
        from ..routers.inventory import _on_hand_by_product

        on_hand_by_pid = _on_hand_by_product(db, list(sku_to_pid.values()), store_id)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[STOCK_WRITEBACK] on-hand aggregate failed: %s", exc)
        return {}

    out: Dict[str, int] = {}
    for sku, pid in sku_to_pid.items():
        out[sku] = int(on_hand_by_pid.get(pid, 0) or 0)
    return out


async def writeback_skus(
    db,
    skus: List[str],
    store_id: Optional[str] = None,
    *,
    source: str = "sale",
    safety_buffer: Optional[int] = None,
) -> Dict[str, Any]:
    """Core: push the (on_hand - buffer) available quantity to Shopify for each
    SKU that maps to an online variant. Gated + fail-soft. Returns a summary
    dict (pushed / skipped / failed / simulated). NEVER raises.

    A SKU is skipped (no-op) when it has no Shopify InventoryItem mapping in the
    BVI catalog -- not every IMS product is online.
    """
    summary: Dict[str, Any] = {
        "source": source,
        "candidates": 0,
        "pushed": 0,
        "simulated": 0,
        "skipped_no_mapping": 0,
        "failed": 0,
        "online_configured": False,
    }
    db = _resolve_db(db)
    distinct = [s for s in dict.fromkeys(skus or []) if s]
    summary["candidates"] = len(distinct)
    if not distinct:
        return summary

    # Lazy imports keep this module import-light (and the Postgres/psycopg2 path
    # optional) so a backend without the e-commerce bridge still boots.
    try:
        from . import online_catalog
        from . import stock_allocation
        from agents.nexus_providers import shopify_set_inventory_available
    except Exception as exc:  # noqa: BLE001
        logger.debug("[STOCK_WRITEBACK] deps unavailable: %s", exc)
        return summary

    summary["online_configured"] = online_catalog.ecommerce_db_configured()

    buf = _safety_buffer(db) if safety_buffer is None else max(0, int(safety_buffer))

    # 1. Resolve Shopify targets (inventory-item + location GIDs). Empty when the
    #    bridge is unconfigured/unreachable -> everything is a no-op skip.
    targets = online_catalog.online_variant_targets_for_skus(distinct)
    if not targets:
        summary["skipped_no_mapping"] = len(distinct)
        return summary

    # 2. Compute on-hand once for the targeted SKUs (the only ones worth pushing).
    on_hand = _on_hand_for_skus(db, list(targets.keys()), store_id)

    for sku, tgt in targets.items():
        try:
            qty = stock_allocation.recommend_allocation(on_hand.get(sku, 0), buf)
            res = await shopify_set_inventory_available(
                db, tgt.get("inventory_item_id"), tgt.get("location_id"), qty
            )
        except Exception as exc:  # noqa: BLE001
            # Defensive: the setter is already fail-soft, but a SKU's failure
            # must never abort the remaining pushes.
            logger.warning("[STOCK_WRITEBACK] push raised for %s: %s", sku, exc)
            summary["failed"] += 1
            continue
        if not res.ok:
            summary["failed"] += 1
        elif res.items_synced and res.items_synced > 0:
            summary["pushed"] += 1
        else:
            # ok but no live write -> SIMULATED (off/test) or RETIRED skip.
            summary["simulated"] += 1

    # SKUs with no online mapping (present in distinct but not targets).
    summary["skipped_no_mapping"] = len(distinct) - len(targets)

    _record_run(db, summary)
    return summary


def _record_run(db, summary: Dict[str, Any]) -> None:
    """Best-effort sync_runs row so the SUPERADMIN online-store sync-health tile
    can see write-back activity. Never raises."""
    if db is None:
        return
    # Only record when something actually happened live (a push or a failure);
    # a pure simulate/no-op shouldn't spam the sync log.
    if not (summary.get("pushed") or summary.get("failed")):
        return
    try:
        coll = db.get_collection("sync_runs")
        if coll is None:
            return
        coll.insert_one(
            {
                "integration": "shopify",
                "kind": "stock_writeback",
                "ok": summary.get("failed", 0) == 0,
                "items_synced": int(summary.get("pushed", 0)),
                "error": (
                    f"{summary.get('failed')} push(es) failed"
                    if summary.get("failed")
                    else None
                ),
                "source": summary.get("source"),
                "ran_at": datetime.now(timezone.utc).isoformat(),
            }
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[STOCK_WRITEBACK] sync_runs insert skipped: %s", exc)


def _dispatch(coro) -> None:
    """Run an async write-back without blocking the caller. If an event loop is
    already running (FastAPI request path) schedule it fire-and-forget; else run
    it to completion inline (sync / test context). NEVER raises."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    try:
        if loop is not None:
            task = loop.create_task(coro)
            # Swallow any late exception so an un-awaited task can't surface a
            # "Task exception was never retrieved" warning into the sale path.
            task.add_done_callback(_swallow_task_result)
        else:
            asyncio.run(coro)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[STOCK_WRITEBACK] dispatch skipped: %s", exc)


def _swallow_task_result(task: "asyncio.Task") -> None:
    try:
        task.result()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[STOCK_WRITEBACK] background push failed: %s", exc)


def writeback_after_sale(db, items_data: List[dict], store_id: Optional[str]) -> None:
    """Fail-soft entrypoint for the POS create-order path. Schedules a Shopify
    stock push for the sold SKUs and returns IMMEDIATELY -- the sale is never
    blocked or slowed. NEVER raises."""
    try:
        skus = skus_from_items(items_data)
        if not skus:
            return
        _dispatch(writeback_skus(db, skus, store_id, source="sale"))
    except Exception as exc:  # noqa: BLE001
        logger.debug("[STOCK_WRITEBACK] after-sale skipped: %s", exc)


def writeback_after_restock(
    db, skus: List[str], store_id: Optional[str], *, source: str = "return_restock"
) -> None:
    """Fail-soft entrypoint for the returns/restock path (stock went back UP).
    Re-pushes the recovered available count. NEVER raises."""
    try:
        clean = [str(s).strip() for s in (skus or []) if str(s or "").strip()]
        if not clean:
            return
        _dispatch(writeback_skus(db, clean, store_id, source=source))
    except Exception as exc:  # noqa: BLE001
        logger.debug("[STOCK_WRITEBACK] after-restock skipped: %s", exc)
