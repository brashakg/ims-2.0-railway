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
  2. For each sold SKU we recompute the POOLED IMS on-hand: AVAILABLE
     stock_units across ALL stores, MINUS a safety buffer, via the canonical
     stock_allocation.recommend_allocation. The online store owns no stock and
     sells from every shop combined (owner decision 2026-07-20), so the
     caller's store_id is CONTEXT ONLY (logging/summary) and never scopes the
     quantity -- selling one store's last unit must not zero a listing that
     still has stock elsewhere. This is the same pooled math the nightly
     shopify_stock_parity check compares against.
  3. We look up the SKU's Shopify InventoryItem GID + location GID in IMS Mongo
     (online_catalog.online_variant_targets_for_skus over
     catalog_variants.shopify_inventory_item_id -- BVI + its Postgres were
     deleted 2026-07-20; IMS is the sole Shopify writer). No mapping -> NO-OP
     for a product that simply isn't online, but a SKU that IS listed online
     and cannot be targeted is a GUARD GAP: it is logged loudly and files a
     deduped SYSTEM task (never a silent fake success).
  4. We call the Shopify GraphQL inventorySetQuantities setter with the ABSOLUTE
     quantity (idempotent on retry).

Contract (mirrors the rest of the consolidation bridge + the NEXUS providers):
- 100% FAIL-SOFT. A Shopify/Mongo failure is caught + logged and NEVER
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
    """Map each SKU -> IMS on-hand (AVAILABLE stock_units). store_id=None (the
    ONLY value the write-back uses) means POOLED across all stores -- the
    quantity the online listing must reflect. Reuses
    inventory._on_hand_by_product (the canonical on-hand math) after resolving
    SKU -> product_id via the products collection. Fail-soft -> {} (the caller
    must treat {} as UNKNOWN, never as zero)."""
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
    """Core: push the POOLED (on_hand - buffer) available quantity to Shopify
    for each SKU that maps to an online variant. Gated + fail-soft. Returns a
    summary dict (pushed / skipped / failed / simulated). NEVER raises.

    ``store_id`` is CONTEXT ONLY (recorded on the summary for logging); the
    pushed quantity is always the ALL-store pooled on-hand -- the online store
    sells from every shop combined, so scoping to the selling store would zero
    a listing that still has stock elsewhere (audit fix-round P0).

    Safety rules for the absolute write:
    - A SKU with no Shopify InventoryItem mapping is skipped -- and when it IS
      sellable online (PUBLISHED / live variant) that is a GUARD GAP alerted
      LOUDLY (_alert_unmapped_online), never a silent fake success.
    - UNKNOWN on-hand is NEVER written as 0: a SKU absent from the on-hand map
      is skipped (skipped_no_onhand), and an entirely-empty on-hand result for
      a non-empty target set aborts the batch with a WARNING + not-ok
      sync_runs row (audit fix-round P1). A SKU PRESENT with on-hand 0 still
      pushes 0 -- that IS the oversell guard.
    """
    summary: Dict[str, Any] = {
        "source": source,
        "store_id": store_id,  # context only -- quantities are pooled
        "candidates": 0,
        "pushed": 0,
        "simulated": 0,
        "skipped_no_mapping": 0,
        "skipped_no_onhand": 0,
        "failed": 0,
        "unmapped_online": 0,
        "online_configured": False,
    }
    db = _resolve_db(db)
    distinct = [s for s in dict.fromkeys(skus or []) if s]
    summary["candidates"] = len(distinct)
    if not distinct:
        return summary

    # Lazy imports keep this module import-light so the backend boots even if a
    # sibling service is broken.
    try:
        from . import online_catalog
        from . import stock_allocation
        from agents.nexus_providers import shopify_set_inventory_available
    except Exception as exc:  # noqa: BLE001
        logger.debug("[STOCK_WRITEBACK] deps unavailable: %s", exc)
        return summary

    summary["online_configured"] = online_catalog.online_mapping_available(db)

    buf = _safety_buffer(db) if safety_buffer is None else max(0, int(safety_buffer))

    # 1. Resolve Shopify targets (inventory-item + location GIDs) from the IMS
    #    Mongo mapping. A SKU with no target is skipped -- and checked below for
    #    the online-but-unmapped guard gap.
    targets = online_catalog.online_variant_targets_for_skus(db, distinct)
    if not targets:
        summary["skipped_no_mapping"] = len(distinct)
        _alert_unmapped_online(db, distinct, summary)
        _record_run(db, summary)
        return summary

    # 2. Compute on-hand once for the targeted SKUs -- POOLED across ALL stores
    #    (store_id deliberately NOT passed: the online listing reflects the
    #    whole chain's availability, never one shop's).
    on_hand = _on_hand_for_skus(db, list(targets.keys()), None)
    if not on_hand:
        # UNKNOWN on-hand for the whole batch (lookup failed or no spine rows).
        # An absolute stock WRITER must never fail soft to 0 -- writing 0 would
        # delist every sold SKU that is physically in stock. Abort loudly.
        summary["skipped_no_onhand"] = len(targets)
        summary["skipped_no_mapping"] = len(distinct) - len(targets)
        logger.warning(
            "[STOCK_WRITEBACK] on-hand UNKNOWN for all %d targeted SKU(s) "
            "(lookup failed or products spine has no rows) -- aborting the "
            "push batch; NOT writing 0 to the live listing. SKUs: %s",
            len(targets),
            ", ".join(sorted(targets.keys())[:20]),
        )
        _record_run(db, summary)
        return summary

    # SUPERADMIN "block a collection from online sale": a product that belongs to
    # an online_sync_blocked collection must NEVER be sellable online even if it
    # is physically in stock, so we push available=0 for it (a delist-by-
    # availability). Fail-soft -> no SKUs treated as blocked on any error.
    try:
        from . import online_block

        blocked = online_block.blocked_skus(db, list(targets.keys()))
    except Exception as exc:  # noqa: BLE001
        logger.debug("[STOCK_WRITEBACK] block lookup skipped: %s", exc)
        blocked = set()
    summary["blocked_online"] = 0

    for sku, tgt in targets.items():
        try:
            if sku in blocked:
                # Deliberate delist: 0 regardless of on-hand.
                qty = 0
                summary["blocked_online"] += 1
            elif sku not in on_hand:
                # UNKNOWN on-hand for this SKU: skip, never write 0. (A SKU
                # present with value 0 falls through and pushes 0 -- correct.)
                summary["skipped_no_onhand"] += 1
                continue
            else:
                qty = stock_allocation.recommend_allocation(on_hand[sku], buf)
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
    unmapped = [s for s in distinct if s not in targets]
    summary["skipped_no_mapping"] = len(unmapped)
    if unmapped:
        _alert_unmapped_online(db, unmapped, summary)

    _record_run(db, summary)
    return summary


def _alert_unmapped_online(db, skus: List[str], summary: Dict[str, Any]) -> None:
    """OVERSELL-GUARD GAP alert (audit OS-015 + fix-round P1): a sold SKU that
    is SELLABLE online but has NO Shopify inventory mapping cannot receive the
    post-sale stock write-back -- the storefront keeps listing the pre-sale
    quantity, a real oversell window.

    Gating (fix-round): the alert requires sellable_online -- ecom.status
    PUBLISHED, or a live variant gid -- NOT mere shopify_product_id presence.
    An unpurchasable Shopify DRAFT (e.g. the 2,032 staged drafts) cannot
    oversell, so it must never fire this alarm or clog the dedupe. A SKU that
    simply isn't online at all stays a silent, correct no-op.

    Alerts LOUDLY, never silently: structured ERROR log + a deduped,
    SELF-UPDATING SYSTEM task (new gap SKUs are $addToSet-ed into the open
    task's payload) and stamps summary['unmapped_online'] so _record_run
    writes a not-ok sync_runs row. Fail-soft: never raises into the sale path."""
    try:
        from . import online_catalog

        statuses = online_catalog.online_status_for_skus(db, skus)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[STOCK_WRITEBACK] unmapped-online check skipped: %s", exc)
        return
    online_unmapped = sorted(
        s for s in skus if (statuses.get(s) or {}).get("sellable_online")
    )
    if not online_unmapped:
        return
    summary["unmapped_online"] = len(online_unmapped)
    logger.error(
        "[STOCK_WRITEBACK] OVERSELL-GUARD GAP: %d sold SKU(s) are sellable "
        "online but carry no Shopify inventory mapping -- stock write-back "
        "could NOT run for: %s",
        len(online_unmapped),
        ", ".join(online_unmapped[:20]),
    )
    _file_guard_gap_task(db, online_unmapped)


# Stable dedupe key: at most ONE open guard-gap task; new gap SKUs are merged
# into the open task's payload (see _file_guard_gap_task) so a later, different
# gap is never silently masked by an already-open task.
_GUARD_GAP_TASK_REF = "online-stock-writeback-unmapped"


def _file_guard_gap_task(db, skus: List[str]) -> None:
    """File ONE deduped P1 SYSTEM task for the guard gap; when a task is already
    OPEN for this ref, MERGE the new gap SKUs into its payload ($addToSet) so
    the open task always reflects the full current gap set (fix-round P1: a
    frozen first-filing payload must not mask later, different gaps).
    Fail-soft."""
    try:
        from .task_triggers import create_system_task
        from database.repositories.task_repository import TaskRepository

        coll = None
        getter = getattr(db, "get_collection", None)
        if callable(getter):
            coll = getter("tasks")
        elif db is not None:
            coll = db["tasks"]
        if coll is None:
            return
        created = create_system_task(
            TaskRepository(coll),
            title="Online oversell guard gap: unmapped online SKUs",
            description=(
                f"{len(skus)} SKU(s) sold in-store are sellable online but have "
                f"no Shopify inventory mapping, so the automatic post-sale stock "
                f"write-back CANNOT correct their online quantity (oversell "
                f"window). IMS does not yet write variant inventory mappings on "
                f"push (pending work package: variant-gid write-back on LIVE "
                f"productCreate), so re-pushing will NOT fix this. Until the "
                f"mapping backfill ships: manually reduce the Shopify quantity "
                f"for these SKUs after in-store sales, or unpublish them. The "
                f"full current SKU set is in this task's payload. First SKUs: "
                f"{', '.join(skus[:20])}"
            ),
            priority="P1",
            category="Inventory",
            store_id=None,
            dedupe_ref=_GUARD_GAP_TASK_REF,
            extra={"payload": {"skus": skus[:50]}},
        )
        if created is None:
            # Deduped: a guard-gap task is already open. Merge the new SKUs
            # into its payload so the open task reflects the CURRENT gap set
            # instead of freezing at the first filing.
            coll.update_one(
                {
                    "source_ref": _GUARD_GAP_TASK_REF,
                    "status": {"$in": ["OPEN", "IN_PROGRESS", "ESCALATED"]},
                },
                {"$addToSet": {"payload.skus": {"$each": skus[:50]}}},
            )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[STOCK_WRITEBACK] guard-gap task skipped: %s", exc)


def _record_run(db, summary: Dict[str, Any]) -> None:
    """Best-effort sync_runs row so the SUPERADMIN online-store sync-health tile
    can see write-back activity. Never raises."""
    if db is None:
        return
    # Record when something actually happened live (a push or a failure), the
    # guard gapped (sellable-online SKU with no mapping), or on-hand was
    # UNKNOWN and SKUs were skipped; a pure simulate/offline no-op shouldn't
    # spam the sync log.
    if not (
        summary.get("pushed")
        or summary.get("failed")
        or summary.get("unmapped_online")
        or summary.get("skipped_no_onhand")
    ):
        return
    unmapped_online = int(summary.get("unmapped_online", 0) or 0)
    skipped_no_onhand = int(summary.get("skipped_no_onhand", 0) or 0)
    errors = []
    if summary.get("failed"):
        errors.append(f"{summary.get('failed')} push(es) failed")
    if unmapped_online:
        errors.append(
            f"{unmapped_online} online SKU(s) had no Shopify inventory mapping "
            f"(oversell-guard gap)"
        )
    if skipped_no_onhand:
        errors.append(
            f"{skipped_no_onhand} SKU(s) skipped: on-hand UNKNOWN "
            f"(never written as 0)"
        )
    try:
        coll = db.get_collection("sync_runs")
        if coll is None:
            return
        coll.insert_one(
            {
                "integration": "shopify",
                "kind": "stock_writeback",
                "ok": (
                    summary.get("failed", 0) == 0
                    and unmapped_online == 0
                    and skipped_no_onhand == 0
                ),
                "items_synced": int(summary.get("pushed", 0)),
                "error": ("; ".join(errors) if errors else None),
                "source": summary.get("source"),
                "store_id": summary.get("store_id"),
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
    blocked or slowed. store_id is context only; the pushed quantity is always
    the ALL-store pooled on-hand. NEVER raises."""
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
