"""
IMS 2.0 - Nightly Shopify stock-parity check, PER LOCATION (read-only diagnostic)
================================================================================
IMS is the inventory MASTER. Every physical shop is its own Shopify location
(multi-location design, owner 2026-09-06) and the ONE writer
(shopify_push.inventory.set_inventory_quantities, via push_skus_stock) sends
each shop's own number to that shop's location. A dropped write-back, a manual
Shopify edit or a race can still let one location's live "available" drift
away from what the writer would send there.

Once a day (SENTINEL, ~03:00 IST) this samples up to N online-mapped variants
and compares, per (SKU, MAPPED shop):

    IMS side     = online_stock_writeback.online_quantities_for_skus(db, skus)
                   [sku][shop] -- the writer's OWN call (same rule, same
                   buffer), read per shop, never re-computed and never summed
    Shopify side = the inventory level at THAT shop's location (0 when Shopify
                   returned the item but it is not stocked there)

Reported separately, NEVER as drift (the writer already files the "map me"
task for both, so parity only reports):
  * unmapped_holders    -- shops with no usable Shopify location that hold
    listed stock (Pune, by design, until its opening stock lands). The
    writer's own inventory.unmapped_holders answers it.
  * unclaimed_locations -- Shopify locations holding stock of a sampled item
    that no IMS shop carries.
In-transit units count at neither location (owner accepted): the rule reads
the shelf, so they are in no row here either.

Tasks: ONE per shop (source_ref ``shopify-stock-parity-drift:<store_id>``) --
filed on drift, refreshed (description + payload) while the drift persists,
completed when a later tick compares that shop clean. The pre-PR-4 POOLED task
(the bare ``shopify-stock-parity-drift`` ref) is never filed again;
scripts/close_pooled_parity_task.py closes the stuck one.

Contract (mirrors the rest of the Shopify bridge):
  * 100% FAIL-SOFT, end to end. No creds / no DB / Shopify error -> a
    structured reason, never a raise. It must NEVER take down SENTINEL.
  * READ-ONLY vs Shopify (single boundary: shopify_push._graphql, injectable).
    Half an answer is no answer: when any Shopify batch fails, nothing is
    compared and no task is filed OR closed.
  * The row builder and the comparator are PURE (parity_rows,
    compare_location_parity, unclaimed_locations): unit-tested without a DB
    or Shopify.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

# Sampling ceiling (keeps the nightly Shopify call cheap on a large catalog).
_DEFAULT_SAMPLE = 500
# Batch size for the Shopify nodes() inventory query.
_INV_BATCH = 100
# Compact snapshot retention.
_SNAPSHOT_RETENTION_DAYS = 30
# Drift tolerance default (units). Env override: SHOPIFY_STOCK_PARITY_TOLERANCE.
_DEFAULT_TOLERANCE = 2
# Stable dedupe key: at most ONE active parity-drift task PER SHOP.
_DRIFT_TASK_REF = "shopify-stock-parity-drift:{store_id}"
# create_system_task's own "still active" statuses (it dedupes on these).
_ACTIVE_TASK_STATUSES = {"OPEN", "IN_PROGRESS", "ESCALATED"}

# GraphQL: live available per InventoryItem, PER LOCATION.
# quantities(names:["available"]) is the current Shopify Admin API shape.
_INV_LEVELS_QUERY = """
query ImsInvLevels($ids: [ID!]!) {
  nodes(ids: $ids) {
    ... on InventoryItem {
      id
      inventoryLevels(first: 50) {
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


# ---------------------------------------------------------------------------
# Pure: rows, comparator, unclaimed locations
# ---------------------------------------------------------------------------


def parity_rows(
    variants: List[Dict[str, Any]],
    quantities: Dict[str, Dict[str, int]],
    levels: Dict[str, Dict[str, int]],
    mapped: Dict[str, str],
) -> List[Dict[str, Any]]:
    """PURE: one row per (sampled variant, MAPPED shop).

    ``ims_available`` is ``quantities[sku][store_id]`` -- None when the rule
    could not read that shop or SKU (unknown is never a 0). ``shopify_available``
    is ``levels[item][location_gid]`` -- 0 when Shopify returned the item but
    it is not stocked at that location (it sells nothing there), None when
    Shopify returned no such item."""
    rows: List[Dict[str, Any]] = []
    for v in variants:
        per_shop = quantities.get(v["sku"]) or {}
        item_levels = levels.get(v["inventory_item_id"])
        for sid, gid in mapped.items():
            rows.append(
                {
                    "sku": v["sku"],
                    "inventory_item_id": v["inventory_item_id"],
                    "store_id": sid,
                    "location_id": gid,
                    "ims_available": per_shop.get(sid),
                    "shopify_available": None if item_levels is None else int(item_levels.get(gid, 0)),
                }
            )
    return rows


def compare_variant_parity(
    rows: List[Dict[str, Any]], tolerance: int
) -> Dict[str, Any]:
    """PURE: given rows of {sku, inventory_item_id, store_id, ims_available,
    shopify_available}, return the drift summary. A row whose either side is
    None (or junk) is counted as UNKNOWN and never a drift.

    Returns {compared, unknown, drift[], drift_count, max_delta, tolerance}
    where drift is [{sku, inventory_item_id, store_id, ims, shopify, delta}]
    sorted by the biggest delta first."""
    tol = max(0, int(tolerance or 0))
    drift: List[Dict[str, Any]] = []
    compared = 0
    unknown = 0
    max_delta = 0
    for r in rows or []:
        ims, shop = r.get("ims_available"), r.get("shopify_available")
        if ims is None or shop is None:
            unknown += 1
            continue
        try:
            ims, shop = int(ims), int(shop)
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
                    "store_id": r.get("store_id"),
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


def compare_location_parity(rows: List[Dict[str, Any]], tolerance: int) -> Dict[str, Any]:
    """PURE: ``compare_variant_parity`` over every row, plus the same summary
    per shop under ``stores`` ({store_id: summary}) -- a shop's drift task is
    decided by its OWN rows only."""
    by_store: Dict[Any, List[Dict[str, Any]]] = {}
    for r in rows or []:
        by_store.setdefault(r.get("store_id"), []).append(r)
    return {
        **compare_variant_parity(rows, tolerance),
        "stores": {sid: compare_variant_parity(rs, tolerance) for sid, rs in by_store.items()},
    }


def unclaimed_locations(
    variants: List[Dict[str, Any]], levels: Dict[str, Dict[str, int]], claimed: Iterable[str]
) -> List[Dict[str, Any]]:
    """PURE: Shopify locations holding stock (available > 0) of a sampled item
    that NO IMS shop carries: ``[{location_id, units, skus}]``, most units
    first. Reported, never drift -- IMS has no number for such a location."""
    claimed = set(claimed)
    out: Dict[str, Dict[str, Any]] = {}
    for v in variants:
        for gid, q in (levels.get(v["inventory_item_id"]) or {}).items():
            if gid in claimed or int(q) <= 0:
                continue
            row = out.setdefault(gid, {"location_id": gid, "units": 0, "skus": []})
            row["units"] += int(q)
            row["skus"].append(v["sku"])
    return sorted(out.values(), key=lambda r: -r["units"])


# ---------------------------------------------------------------------------
# Reads (IMS catalog sample, Shopify levels)
# ---------------------------------------------------------------------------


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


async def shopify_levels_by_item(
    db, inventory_item_ids: List[str], *, graphql: Optional[Callable] = None
) -> Optional[Dict[str, Dict[str, int]]]:
    """Live Shopify 'available' per inventory item PER LOCATION:
    ``{inventory_item_id (as supplied): {location_gid: available}}``. An item
    Shopify did not return is absent (the caller reads it as UNKNOWN).

    None when ANY batch failed: half an answer is no answer -- a drift task
    must never be closed on a read that skipped the drifted item.
    Read-only; never raises."""
    if not inventory_item_ids:
        return {}
    try:
        from .shopify_push import _graphql
        from agents.nexus_providers import _as_shopify_gid
    except Exception as exc:  # noqa: BLE001
        logger.warning("[STOCK_PARITY] shopify deps unavailable: %s", exc)
        return None
    gql = graphql or _graphql

    # Map GID -> the caller's supplied id so we can key the result back exactly.
    gid_to_supplied: Dict[str, str] = {}
    for raw in inventory_item_ids:
        gid = _as_shopify_gid(raw, "InventoryItem")
        if gid:
            gid_to_supplied.setdefault(gid, str(raw))

    out: Dict[str, Dict[str, int]] = {}
    gids = list(gid_to_supplied.keys())
    for i in range(0, len(gids), _INV_BATCH):
        chunk = gids[i : i + _INV_BATCH]
        try:
            body = await gql(db, _INV_LEVELS_QUERY, {"ids": chunk})
            nodes = (body.get("data") or {}).get("nodes")
            if not isinstance(nodes, list):
                raise ValueError(f"no nodes in the answer: {body.get('errors')}")
        except Exception as exc:  # noqa: BLE001
            logger.warning("[STOCK_PARITY] shopify inventory query failed: %s", exc)
            return None
        for node in nodes:
            if not isinstance(node, dict):
                continue
            supplied = gid_to_supplied.get(str(node.get("id") or ""))
            if not supplied:
                continue
            per_location: Dict[str, int] = {}
            for edge in ((node.get("inventoryLevels") or {}).get("edges")) or []:
                lnode = edge.get("node") if isinstance(edge, dict) else None
                if not isinstance(lnode, dict):
                    continue
                loc = _as_shopify_gid(str((lnode.get("location") or {}).get("id") or ""), "Location")
                if not loc:
                    continue
                for q in lnode.get("quantities") or []:
                    if isinstance(q, dict) and q.get("name") == "available":
                        try:
                            per_location[loc] = per_location.get(loc, 0) + int(q.get("quantity") or 0)
                        except (TypeError, ValueError):
                            pass
            out[supplied] = per_location
    return out


# ---------------------------------------------------------------------------
# Tasks: one per shop, filed / refreshed / closed
# ---------------------------------------------------------------------------


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


def sync_drift_task(repo, store: Dict[str, Any], summary: Dict[str, Any]) -> Optional[str]:
    """ONE shop's drift task, from that shop's own ``compare_location_parity``
    summary (source_ref ``shopify-stock-parity-drift:<store_id>``):

      * drift             -> refresh every ACTIVE task's description + payload,
                             or file one when none is active (never a second);
      * compared, 0 drift -> complete every active task (the drift cleared);
      * nothing compared  -> leave it alone (unknown is not clear).

    Returns "filed" | "refreshed" | "closed" | None. Fail-soft."""
    sid = str(store.get("store_id") or "")
    label = store.get("store_code") or store.get("store_name") or sid
    ref = _DRIFT_TASK_REF.format(store_id=sid)
    try:
        active = [
            t
            for t in (repo.find_many({"source_ref": ref}) or [])
            if str(t.get("status", "")).upper() in _ACTIVE_TASK_STATUSES
        ]
        if summary.get("drift_count"):
            worst = (summary.get("drift") or [])[:5]
            lines = ", ".join(
                f"{d.get('sku')} (IMS {d.get('ims')} vs Shopify {d.get('shopify')})" for d in worst
            )
            description = (
                f"{summary.get('drift_count')} online SKU(s) at {label}'s Shopify location "
                f"drifted beyond tolerance {summary.get('tolerance')} unit(s); worst delta "
                f"{summary.get('max_delta')}. Top: {lines}. The next stock push "
                f"(01:00 / 09:00 IST, or Push stock) re-sends IMS's numbers."
            )
            payload = {
                "store_id": sid,
                "drift": worst,
                "drift_count": summary.get("drift_count"),
                "max_delta": summary.get("max_delta"),
            }
            if active:
                for t in active:
                    repo.update(t.get("task_id"), {"description": description, "payload": payload})
                return "refreshed"
            from .task_triggers import create_system_task

            created = create_system_task(
                repo,
                title=f"Shopify stock drift at {label}",
                description=description,
                priority="P2",
                category="Inventory",
                store_id=sid or None,
                dedupe_ref=ref,
                extra={"payload": payload},
            )
            return "filed" if created else None
        if active and summary.get("compared"):
            for t in active:
                repo.complete_task(
                    t.get("task_id"),
                    notes=f"Auto-closed: {label} compared clean ({summary.get('compared')} SKU(s)).",
                )
            return "closed"
    except Exception as exc:  # noqa: BLE001
        logger.warning("[STOCK_PARITY] drift task sync failed for %s: %s", sid, exc)
    return None


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# The tick
# ---------------------------------------------------------------------------


async def run_parity_tick(
    db,
    *,
    sample_limit: int = _DEFAULT_SAMPLE,
    graphql: Optional[Callable] = None,
) -> Dict[str, Any]:
    """The daily tick body (called by SENTINEL ~03:00 IST). Samples
    online-mapped variants, compares each MAPPED shop's IMS number with the
    live level at that shop's Shopify location, reports unmapped holders and
    unclaimed locations separately, stores a compact snapshot and syncs one
    drift task per shop.

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
        "unknown": 0,
        "drift_count": 0,
        "max_delta": 0,
        "tolerance": tolerance,
        "drift": [],
        "stores": [],
        "unmapped_holders": [],
        "unclaimed_locations": [],
        "tasks": {"filed": [], "refreshed": [], "closed": []},
        "task_filed": False,
    }

    try:
        # Gate on creds so we don't mint tokens / call Shopify when unconfigured.
        try:
            from .shopify_push import _has_shopify_creds

            if not _has_shopify_creds(db):
                snap = {**base, "reason": "shopify creds not configured"}
                _store_snapshot(db, snap)
                return snap
        except Exception as exc:  # noqa: BLE001
            return {**base, "reason": f"creds check failed: {exc}"}

        variants = _sample_variants(db, sample_limit)
        base["sampled"] = len(variants)
        if not variants:
            snap = {**base, "checked": True, "reason": "no online-mapped variants"}
            _store_snapshot(db, snap)
            return snap

        from .online_stock_writeback import online_quantities_for_skus
        from .shopify_push.inventory import _mapped, _stores, unmapped_holders

        stores = _stores(db)
        mapped = _mapped(stores)
        skus = [v["sku"] for v in variants]
        # The writer's own call (inventory.push_skus_stock): same rule, same
        # buffer -- so a non-zero safety buffer is never read as drift.
        quantities = online_quantities_for_skus(db, skus) or {}
        levels = await shopify_levels_by_item(
            db, [v["inventory_item_id"] for v in variants], graphql=graphql
        )
        if levels is None:
            snap = {**base, "reason": "shopify inventory read failed -- nothing compared, no task touched"}
            _store_snapshot(db, snap)
            return snap

        cmp = compare_location_parity(parity_rows(variants, quantities, levels, mapped), tolerance)
        claimed = {str(s.get("shopify_location_id") or "") for s in stores} - {""}
        per_store = cmp["stores"]
        snapshot = {
            **base,
            "checked": True,
            "compared": cmp["compared"],
            "unknown": cmp["unknown"],
            "drift_count": cmp["drift_count"],
            "max_delta": cmp["max_delta"],
            # Cap the stored drift lists so the snapshot stays compact.
            "drift": cmp["drift"][:50],
            "stores": [
                {
                    "store_id": sid,
                    "location_id": gid,
                    "compared": (per_store.get(sid) or {}).get("compared", 0),
                    "unknown": (per_store.get(sid) or {}).get("unknown", 0),
                    "drift_count": (per_store.get(sid) or {}).get("drift_count", 0),
                    "max_delta": (per_store.get(sid) or {}).get("max_delta", 0),
                    "drift": ((per_store.get(sid) or {}).get("drift") or [])[:20],
                }
                for sid, gid in mapped.items()
            ],
            "unmapped_holders": unmapped_holders(db, quantities, stores, skus, mapped),
            "unclaimed_locations": unclaimed_locations(variants, levels, claimed),
            "tasks": {"filed": [], "refreshed": [], "closed": []},
        }

        repo = _task_repo(db)
        if repo is not None:
            for store in stores:
                sid = str(store.get("store_id") or "")
                if sid in mapped:
                    outcome = sync_drift_task(repo, store, per_store.get(sid) or {})
                    if outcome:
                        snapshot["tasks"][outcome].append(sid)
        snapshot["task_filed"] = bool(snapshot["tasks"]["filed"])

        _store_snapshot(db, snapshot)
        logger.info(
            "[STOCK_PARITY] tick: sampled=%s compared=%s drift=%s max_delta=%s tasks=%s "
            "unmapped_holders=%s unclaimed_locations=%s",
            snapshot["sampled"],
            snapshot["compared"],
            snapshot["drift_count"],
            snapshot["max_delta"],
            snapshot["tasks"],
            len(snapshot["unmapped_holders"]),
            len(snapshot["unclaimed_locations"]),
        )
        return snapshot
    except Exception as exc:  # noqa: BLE001 -- never crash the scheduler
        logger.warning("[STOCK_PARITY] tick failed: %s", exc)
        return {**base, "reason": f"tick error: {exc}"}
